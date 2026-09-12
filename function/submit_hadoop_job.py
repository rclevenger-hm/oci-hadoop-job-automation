import json
import logging
import os

import paramiko

from job_command import build_hadoop_command, validate_job_params


MAX_REQUEST_BYTES = 64 * 1024
MAX_REMOTE_OUTPUT_BYTES = 1024 * 1024
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


def _read_bounded(stream, label):
    raw = stream.read(MAX_REMOTE_OUTPUT_BYTES + 1)
    if len(raw) > MAX_REMOTE_OUTPUT_BYTES:
        raise RuntimeError(f"remote {label} exceeded {MAX_REMOTE_OUTPUT_BYTES} byte limit")
    return raw.decode("utf-8", errors="replace")


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
        _, stdout, stderr = ssh_client.exec_command(command, timeout=30)
        exit_status = stdout.channel.recv_exit_status()
        stdout_text = _read_bounded(stdout, "stdout")
        stderr_text = _read_bounded(stderr, "stderr")

        if exit_status != 0:
            raise RuntimeError(
                f"Hadoop command failed with exit status {exit_status}: {stderr_text.strip()}"
            )

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
    except (RuntimeError, OSError, paramiko.SSHException):
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
