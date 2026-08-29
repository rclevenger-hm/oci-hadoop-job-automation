# Hadoop submission function

The function submits a Hadoop JAR over SSH to a configured cluster host.

## Runtime configuration

Configure these environment variables in the OCI Function deployment:

- `HADOOP_HOST` — required cluster hostname or IP.
- `HADOOP_PRIVATE_KEY` — required path to the mounted SSH private key.
- `HADOOP_USER` — optional SSH user; defaults to `opc`.

The function intentionally does not accept host, username, or private-key path from the request body.

## Host-key verification

Unknown SSH host keys are rejected. Provision the expected host key in the function runtime's known-hosts file before invoking the function. This prevents silently trusting a different host after DNS, routing, or instance changes.

## Request contract

```json
{
  "jar_path": "/opt/jobs/wordcount.jar",
  "job_class": "com.example.WordCount",
  "input_path": "oci://example-bucket/input",
  "output_path": "oci://example-bucket/output"
}
```

All four fields are required. Paths are shell-quoted before execution and `job_class` is restricted to Java-style class-name characters. Newlines, carriage returns, and NUL bytes are rejected.

## Operational behavior

- SSH connection/authentication timeouts are bounded.
- Non-zero Hadoop exit codes are returned as errors with stderr context.
- SSH sessions are closed in a `finally` block.
- Request/configuration errors are returned without exposing credentials.

The private key itself should be supplied through an appropriate secret/mount mechanism and must not be committed to this repository.
