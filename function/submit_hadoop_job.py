import json
import os

import paramiko

from job_command import build_hadoop_command, validate_job_params


def _required_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable {name} is not set")
    return value


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
        stdout_text = stdout.read().decode("utf-8", errors="replace")
        stderr_text = stderr.read().decode("utf-8", errors="replace")

        if exit_status != 0:
            raise RuntimeError(
                f"Hadoop command failed with exit status {exit_status}: {stderr_text.strip()}"
            )

        return stdout_text
    finally:
        ssh_client.close()


def handle_request(request):
    try:
        job_status = submit_hadoop_job(request)
        return {
            "message": "Hadoop job submitted successfully",
            "job_status": job_status,
        }
    except (ValueError, RuntimeError, OSError, paramiko.SSHException) as exc:
        return {"error": str(exc)}


def handler(ctx, data):
    try:
        request = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"error": f"invalid JSON request: {exc}"}

    return handle_request(request)
