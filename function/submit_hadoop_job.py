import json
import logging
import os
import socket
import time

import paramiko

from job_command import build_hadoop_command, validate_job_params


MAX_REQUEST_BYTES = 64 * 1024
MAX_REMOTE_OUTPUT_BYTES = 1024 * 1024
REMOTE_COMPLETION_TIMEOUT_SECONDS = 30
_OUTPUT_CHUNK_BYTES = 32 * 1024
_OUTPUT_POLL_SECONDS = 0.01
LOGGER = logging.getLogger(__name__)


def _required_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable {name} is not set")
    return value


def _request_id(ctx):
    if ctx is None or not hasattr(ctx, "CallID"):
        return None
    try:
        value = ctx.CallID()
    except Exception:  # pragma: no cover - defensive against runtime context differences
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _with_request_id(response, request_id):
    if not request_id:
        return response
    return {**response, "request_id": request_id}


def _collect_remote_result(channel):
    """Drain both streams with an application-level output/status deadline.

    Nonblocking reads avoid waiting for input; Paramiko's internal transport
    writes (for example, receive-window updates) retain their own behavior.
    """
    deadline = time.monotonic() + REMOTE_COMPLETION_TIMEOUT_SECONDS
    channel.settimeout(0.0)
    stdout = bytearray()
    sizes = {"stdout": 0, "stderr": 0}
    finished = set()
    readers = (("stdout", channel.recv), ("stderr", channel.recv_stderr))

    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("remote command completion timed out; outcome is unknown")

        received_output = False
        for label, receive in readers:
            if label in finished:
                continue
            # Read at most one byte beyond the limit, and give both streams a
            # turn so a full stderr window cannot block stdout completion.
            size = min(_OUTPUT_CHUNK_BYTES, MAX_REMOTE_OUTPUT_BYTES - sizes[label] + 1)
            try:
                chunk = receive(size)
            except socket.timeout:
                continue
            if not chunk:
                finished.add(label)
                continue
            received_output = True
            sizes[label] += len(chunk)
            if sizes[label] > MAX_REMOTE_OUTPUT_BYTES:
                raise RuntimeError(
                    f"remote {label} exceeded {MAX_REMOTE_OUTPUT_BYTES} byte limit"
                )
            if label == "stdout":
                stdout.extend(chunk)

        # An exit status may arrive before the final output. Wait for both
        # streams' EOF as well, but never call recv_exit_status while it blocks.
        if len(finished) == 2 and channel.exit_status_ready():
            return channel.recv_exit_status(), stdout.decode("utf-8", errors="replace")

        if not received_output:
            time.sleep(min(_OUTPUT_POLL_SECONDS, max(0, deadline - time.monotonic())))


def submit_hadoop_job(job_params):
    params = validate_job_params(job_params)
    instance_ip = _required_env("HADOOP_HOST")
    username = os.environ.get("HADOOP_USER", "opc")
    private_key_path = _required_env("HADOOP_PRIVATE_KEY")

    ssh_client = paramiko.SSHClient()
    ssh_client.load_system_host_keys()
    ssh_client.set_missing_host_key_policy(paramiko.RejectPolicy())

    try:
        ssh_key = paramiko.RSAKey(filename=private_key_path)
        ssh_client.connect(
            hostname=instance_ip,
            username=username,
            pkey=ssh_key,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
        )

        command = build_hadoop_command(params)
        _stdin, stdout, _stderr = ssh_client.exec_command(
            command, timeout=REMOTE_COMPLETION_TIMEOUT_SECONDS
        )
        exit_status, stdout_text = _collect_remote_result(stdout.channel)

        if exit_status != 0:
            raise RuntimeError(f"Hadoop command failed with exit status {exit_status}")

        return stdout_text
    finally:
        ssh_client.close()


def handle_request(request, request_id=None):
    try:
        job_status = submit_hadoop_job(request)
        return {
            "message": "Hadoop job submitted successfully",
            "job_status": job_status,
        }
    except ValueError as exc:
        return {"error": str(exc)}
    except (RuntimeError, OSError, EOFError, paramiko.SSHException):
        LOGGER.exception(
            "Hadoop job submission failed request_id=%s",
            request_id or "unknown",
        )
        return {"error": "job submission failed"}


def handler(ctx, data):
    request_id = _request_id(ctx)

    if not isinstance(data, (bytes, bytearray)):
        return _with_request_id({"error": "request body must be bytes"}, request_id)
    if len(data) > MAX_REQUEST_BYTES:
        return _with_request_id(
            {"error": f"request body exceeds {MAX_REQUEST_BYTES} byte limit"},
            request_id,
        )

    try:
        request = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _with_request_id({"error": f"invalid JSON request: {exc}"}, request_id)

    response = handle_request(request, request_id=request_id)
    return _with_request_id(response, request_id)
