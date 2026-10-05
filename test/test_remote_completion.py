"""Exercise real Paramiko receive buffers without a live cluster or sockets."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

import submit_hadoop_job as submission


VALID_JOB = {
    "jar_path": "/opt/jobs/example.jar",
    "job_class": "com.example.WordCount",
    "input_path": "oci://bucket@namespace/input",
    "output_path": "oci://bucket@namespace/output",
}


class FakeClock:
    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.on_sleep = on_sleep
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        if seconds <= 0:
            raise AssertionError("idle polling must yield for a positive interval")
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


class RemoteCompletionTests(unittest.TestCase):
    def channel(self, stdout=b"", stderr=b"", eof=True, status=0):
        channel = submission.paramiko.Channel(0)
        self.addCleanup(channel.close)
        channel.in_buffer.feed(stdout)
        channel.in_stderr_buffer.feed(stderr)
        if eof:
            channel.in_buffer.close()
            channel.in_stderr_buffer.close()
        if status is not None:
            channel.exit_status = status
            channel.status_event.set()
        return channel

    def collect(self, channel, clock=None):
        clock = clock or FakeClock()
        with patch.object(submission.time, "monotonic", clock.monotonic), patch.object(
            submission.time, "sleep", clock.sleep
        ):
            return submission._collect_remote_result(channel)

    def submit(self, channel, clock=None, through_handler=False):
        clock = clock or FakeClock()
        client = MagicMock()
        client.exec_command.return_value = (
            MagicMock(), channel.makefile("rb"), channel.makefile_stderr("rb")
        )
        env = {"HADOOP_HOST": "10.0.0.10", "HADOOP_PRIVATE_KEY": "/tmp/test-key"}
        with patch.dict(os.environ, env, clear=True), patch.object(
            submission.paramiko, "SSHClient", return_value=client
        ), patch.object(submission.paramiko, "RSAKey"), patch.object(
            submission.time, "monotonic", clock.monotonic
        ), patch.object(submission.time, "sleep", clock.sleep):
            try:
                if through_handler:
                    return submission.handler(None, json.dumps(VALID_JOB).encode())
                return submission.submit_hadoop_job(VALID_JOB)
            finally:
                client.close.assert_called_once_with()
                client.exec_command.assert_called_once()
                self.assertEqual(client.exec_command.call_args.kwargs["timeout"], 30)

    def test_submission_drains_both_streams_before_receiving_exit_status(self):
        channel = self.channel(b"x" * 100_000, b"private stderr" * 10_000)
        receive_status = channel.recv_exit_status

        def guard_status():
            self.assertFalse(channel.recv_ready(), "stdout must be drained first")
            self.assertFalse(channel.recv_stderr_ready(), "stderr must be drained first")
            return receive_status()

        with patch.object(channel, "recv_exit_status", side_effect=guard_status):
            self.assertEqual(self.submit(channel), "x" * 100_000)
        self.assertEqual(channel.gettimeout(), 0.0)

    def test_both_streams_get_a_turn_before_stdout_is_drained(self):
        channel = self.channel(b"x" * 100_000, b"diagnostic")
        receive_stderr = channel.recv_stderr
        calls = []

        def stderr(size):
            calls.append(channel.recv_ready())
            return receive_stderr(size)

        with patch.object(channel, "recv_stderr", side_effect=stderr):
            self.assertEqual(self.collect(channel), (0, "x" * 100_000))
        self.assertTrue(calls[0])

    def test_accepts_exact_output_limit_on_each_stream(self):
        limit = submission.MAX_REMOTE_OUTPUT_BYTES
        channel = self.channel(b"x" * limit, b"y" * limit)
        self.assertEqual(self.collect(channel), (0, "x" * limit))

    def test_rejects_each_oversized_stream_before_waiting_for_status(self):
        for label in ("stdout", "stderr"):
            with self.subTest(stream=label):
                data = {label: b"x" * (submission.MAX_REMOTE_OUTPUT_BYTES + 1)}
                channel = self.channel(**data, eof=False, status=None)
                with patch.object(channel, "recv_exit_status") as status:
                    with self.assertRaisesRegex(RuntimeError, f"remote {label} exceeded"):
                        self.submit(channel)
                    status.assert_not_called()

    def test_idle_open_command_times_out_without_blocking_status_call(self):
        channel = self.channel(eof=False, status=None)
        clock = FakeClock()
        with patch.object(channel, "recv_exit_status") as status:
            with self.assertRaisesRegex(TimeoutError, "outcome is unknown"):
                self.submit(channel, clock)
            status.assert_not_called()
        self.assertAlmostEqual(clock.now, 30)
        self.assertGreater(len(clock.sleeps), 0)

    def test_eof_without_exit_status_still_has_a_deadline(self):
        channel = self.channel(status=None)
        clock = FakeClock()
        with self.assertRaises(TimeoutError):
            self.collect(channel, clock)
        self.assertAlmostEqual(clock.now, 30)

    def test_exit_status_does_not_truncate_later_output(self):
        channel = self.channel(eof=False, status=0)
        events = [
            lambda: channel.in_buffer.feed(b"first "),
            lambda: channel.in_stderr_buffer.feed(b"private diagnostic"),
            lambda: channel.in_buffer.feed(b"last"),
            lambda: (channel.in_buffer.close(), channel.in_stderr_buffer.close()),
        ]
        clock = FakeClock(lambda: events.pop(0)() if events else None)
        self.assertEqual(self.collect(channel, clock), (0, "first last"))
        self.assertEqual(events, [])

    def test_exit_status_without_eof_cannot_bypass_deadline(self):
        channel = self.channel(eof=False)
        with self.assertRaises(TimeoutError):
            self.collect(channel)

    def test_continuous_output_cannot_reset_or_bypass_deadline(self):
        channel = self.channel(eof=False, status=None)
        clock = FakeClock()

        def receive(size):
            clock.now += 1
            return b"x"

        with patch.object(channel, "recv", side_effect=receive):
            with self.assertRaises(TimeoutError):
                self.collect(channel, clock)
        self.assertEqual(clock.now, 30)
        self.assertEqual(clock.sleeps, [])

    def test_periodic_output_does_not_reset_deadline(self):
        channel = self.channel(eof=False, status=None)
        clock = FakeClock(lambda: channel.in_buffer.feed(b"x"))
        with self.assertRaises(TimeoutError):
            self.collect(channel, clock)
        self.assertAlmostEqual(clock.now, 30)

    def test_waits_for_status_after_eof(self):
        channel = self.channel(b"done", status=None)

        def status_arrives():
            channel.exit_status = 0
            channel.status_event.set()

        self.assertEqual(self.collect(channel, FakeClock(status_arrives)), (0, "done"))

    def test_closed_channel_without_status_is_not_reported_as_success(self):
        channel = self.channel(status=None)
        with channel.lock:
            channel._set_closed()
        with self.assertRaisesRegex(RuntimeError, "exit status -1"):
            self.submit(channel)

    def test_split_multibyte_and_invalid_utf8_are_decoded_after_collection(self):
        channel = self.channel(b"x" * (submission._OUTPUT_CHUNK_BYTES - 1) + "€".encode() + b"\xff")
        status, result = self.collect(channel)
        self.assertEqual(status, 0)
        self.assertEqual(result, "x" * (submission._OUTPUT_CHUNK_BYTES - 1) + "€\ufffd")

    def test_timeout_response_preserves_generic_error_and_no_retry(self):
        channel = self.channel(b"private stdout", b"private stderr", eof=False, status=None)
        with self.assertLogs(submission.LOGGER, level="ERROR") as logs:
            response = self.submit(channel, through_handler=True)
        self.assertEqual(response, {"error": "job submission failed"})
        self.assertNotIn("private stdout", " ".join(logs.output))
        self.assertNotIn("private stderr", " ".join(logs.output))

    def test_transport_eof_returns_generic_error_and_closes_client(self):
        channel = self.channel(eof=False, status=None)
        with patch.object(channel, "recv", side_effect=EOFError("transport disconnected")):
            with self.assertLogs(submission.LOGGER, level="ERROR"):
                response = self.submit(channel, through_handler=True)
        self.assertEqual(response, {"error": "job submission failed"})


if __name__ == "__main__":
    unittest.main()
