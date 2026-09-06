# OCI Hadoop Job Automation

Automate validated Hadoop command submission to an OCI-hosted cluster through an OCI Function and SSH. The project focuses on safe request handling, bounded network execution, and an explicit operational boundary for short-running jobs.

## What it demonstrates

- **Validated job requests:** required fields are checked before any remote command is constructed.
- **Safer command construction:** path arguments are shell-quoted and the Java job class is constrained to an allowlisted character pattern.
- **SSH trust enforcement:** system host keys are loaded and unknown hosts are rejected rather than automatically trusted.
- **Bounded remote execution:** connection/authentication setup and remote command execution use explicit timeouts.
- **Clear failure propagation:** non-zero Hadoop exits and SSH/configuration failures are returned to the caller with useful context.

## Current execution model

The implementation is synchronous: the function connects to the configured Hadoop host, runs the command, waits for its exit status, and returns stdout on success. It does **not** currently provide a background job-control plane, persisted job state, or a separate real-time status endpoint.

That makes the current design most appropriate for short submission/management commands. Long-running Hadoop workloads should evolve toward submit-and-return semantics with a stable job/application identifier, persisted status, idempotency, and a separate status query path.

See [`docs/OPERATIONS.md`](docs/OPERATIONS.md) for failure modes, retry guidance, incident triage, observability recommendations, and the safe evolution path for longer-running jobs.

## Getting started

1. Provide the function runtime with the required configuration:
   - `HADOOP_HOST`: target Hadoop host/private IP.
   - `HADOOP_PRIVATE_KEY`: path to the private key available to the runtime.
   - `HADOOP_USER`: optional SSH username; defaults to `opc`.
2. Ensure the target host key is present in the runtime's known-hosts data. Unknown hosts are intentionally rejected.
3. Install dependencies from `function/requirements.txt`.
4. Deploy the function using your normal OCI Functions workflow.

Do not commit private keys or credentials to this repository.

## Request shape

Submit a JSON object containing the required Hadoop command inputs:

```json
{
  "jar_path": "/path/to/your/hadoop/job.jar",
  "job_class": "com.example.hadoop.JobClass",
  "input_path": "/path/to/input",
  "output_path": "/path/to/output"
}
```

The function validates all four fields before connecting to the cluster.

## Validation

Run the unit tests from the repository root:

```bash
python -m unittest discover -s test -v
```

The tests cover expected command construction, shell metacharacter quoting, invalid job-class rejection, missing required fields, and unsupported control characters.

## Operational cautions

- A remote execution timeout is an **unknown outcome**, not proof that the Hadoop command never started.
- The current API has no idempotency key, so ambiguous failures should be checked against cluster state before retrying.
- Host-key verification should never be disabled as a recovery shortcut.
- Raw credentials, key material, and sensitive payloads should not be logged.

## License

This project is licensed under the [MIT License](LICENSE).
