import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

import submit_hadoop_job


VALID_JOB = {
    "jar_path": "/opt/jobs/example.jar",
    "job_class": "com.example.WordCount",
    "input_path": "oci://bucket@namespace/input",
    "output_path": "oci://bucket@namespace/output",
}


class FakeStream(io.BytesIO):
    def __init__(self, body=b"", exit_status=0):
        super().__init__(body)
        self.channel = MagicMock()
        self.channel.recv_exit_status.return_value = exit_status


class SubmitHadoopJobTests(unittest.TestCase):
    def env(self):
        return patch.dict(
            os.environ,
            {
                "HADOOP_HOST": "10.0.0.10",
                "HADOOP_USER": "opc",
                "HADOOP_PRIVATE_KEY": "/tmp/test-key",
            },
            clear=True,
        )

    @patch.object(submit_hadoop_job.paramiko, "RSAKey")
    @patch.object(submit_hadoop_job.paramiko, "SSHClient")
    def test_success_executes_validated_command_and_closes_client(self, ssh_client_cls, rsa_key_cls):
        client = ssh_client_cls.return_value
        stdout = FakeStream(b"application_123 submitted\n", exit_status=0)
        stderr = FakeStream(b"")
        client.exec_command.return_value = (MagicMock(), stdout, stderr)
        rsa_key_cls.return_value = MagicMock(name="key")

        with self.env():
            result = submit_hadoop_job.submit_hadoop_job(VALID_JOB)

        self.assertIn("application_123", result)
        client.load_system_host_keys.assert_called_once_with()
        client.set_missing_host_key_policy.assert_called_once()
        client.connect.assert_called_once_with(
            hostname="10.0.0.10",
            username="opc",
            pkey=rsa_key_cls.return_value,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
        )
        command = client.exec_command.call_args.args[0]
        self.assertEqual(
            command,
            "hadoop jar /opt/jobs/example.jar com.example.WordCount "
            "oci://bucket@namespace/input oci://bucket@namespace/output",
        )
        client.close.assert_called_once_with()

    @patch.object(submit_hadoop_job.paramiko, "RSAKey")
    @patch.object(submit_hadoop_job.paramiko, "SSHClient")
    def test_nonzero_exit_surfaces_stderr_and_still_closes(self, ssh_client_cls, rsa_key_cls):
        client = ssh_client_cls.return_value
        client.exec_command.return_value = (
            MagicMock(),
            FakeStream(b"", exit_status=17),
            FakeStream(b"output path already exists\n"),
        )

        with self.env():
            with self.assertRaisesRegex(RuntimeError, "exit status 17.*output path already exists"):
                submit_hadoop_job.submit_hadoop_job(VALID_JOB)

        client.close.assert_called_once_with()

    def test_missing_required_environment_is_returned_as_error(self):
        with patch.dict(os.environ, {}, clear=True):
            response = submit_hadoop_job.handle_request(VALID_JOB)
        self.assertIn("HADOOP_HOST", response["error"])

    def test_invalid_json_handler_returns_safe_error(self):
        response = submit_hadoop_job.handler(None, b"{not-json")
        self.assertIn("invalid JSON request", response["error"])

    @patch.object(submit_hadoop_job, "submit_hadoop_job")
    def test_handle_request_preserves_success_contract(self, submit):
        submit.return_value = "submitted"
        response = submit_hadoop_job.handle_request(VALID_JOB)
        self.assertEqual(
            response,
            {"message": "Hadoop job submitted successfully", "job_status": "submitted"},
        )


if __name__ == "__main__":
    unittest.main()
