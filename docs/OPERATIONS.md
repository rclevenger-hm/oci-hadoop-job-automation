# Operations runbook

This project executes a Hadoop command over SSH from an OCI Function. The function currently waits for the remote command to finish and returns the command output; it does not maintain an asynchronous job-control plane or a separate status API.

## Runtime contract

The function requires:

- `HADOOP_HOST`: target host or private IP reachable from the function runtime.
- `HADOOP_PRIVATE_KEY`: path to the private key available to the function runtime.
- `HADOOP_USER`: optional SSH username; defaults to `opc`.

The SSH client loads known host keys and rejects unknown hosts. Connection, banner, and authentication setup are bounded by 10-second timeouts. Remote command execution is bounded by a 30-second timeout.

## Primary failure modes

| Failure | Expected symptom | First checks | Recovery |
| --- | --- | --- | --- |
| Missing configuration | Function returns a required-environment-variable error | Function configuration | Restore the missing setting and redeploy/reinvoke |
| Host-key mismatch / unknown host | SSH connection fails before command execution | Known-hosts material and target identity | Verify the instance identity before updating known-hosts data |
| Network or NSG failure | SSH connection timeout | VCN route, NSGs/security lists, target SSH listener | Restore connectivity; do not weaken host-key policy as a workaround |
| Authentication failure | SSH authentication exception | Key file availability, ownership, target authorized keys, username | Restore the intended key/user mapping |
| Invalid request | Validation error before SSH | Required fields and job class | Correct the request; do not bypass validation |
| Hadoop process failure | Non-zero exit status with stderr | Hadoop/YARN logs and stderr | Correct the job or cluster condition, then retry deliberately |
| Execution timeout | Function-side SSH command timeout | Job duration and cluster health | Determine whether this workload belongs in the current synchronous execution model |

## Incident triage

1. Capture the function invocation identifier, request metadata that is safe to retain, error category, and timestamp.
2. Determine whether the failure occurred before SSH, during connection/authentication, or after command execution began.
3. Check OCI Function logs for the application error without recording private-key contents or sensitive input data.
4. For SSH failures, verify routing and host identity before changing credentials or trust material.
5. For Hadoop failures, correlate the submitted job with Hadoop/YARN logs on the target cluster.
6. Confirm whether a retry is safe. The current interface has no idempotency key and cannot prove that a timed-out remote command did not start.

## Retry policy

Do not automatically retry every error.

- Validation and configuration errors require correction, not retry.
- Authentication and host-key failures require investigation.
- Connection timeouts may be retried only after establishing that the target is healthy and the request is safe to repeat.
- Remote-command timeouts are ambiguous: the client may have lost visibility while the Hadoop process continued. Treat these as `unknown outcome` until cluster state is checked.
- Non-zero Hadoop exits should be retried only after the failure cause is understood.

## Observability recommendations

The current implementation returns errors to the caller but does not emit structured operational metrics. A production-oriented follow-up should add:

- invocation counts by outcome category;
- SSH connect/authentication failure counts;
- Hadoop non-zero exit counts;
- remote-command timeout counts;
- command execution duration;
- correlation/job identifiers that do not expose sensitive paths or credentials;
- alerting on sustained failure rate rather than individual job failures.

Avoid logging private-key material, authentication data, or raw payloads by default.

## Synchronous execution boundary

The current function waits for `recv_exit_status()` and then reads stdout/stderr. That makes it appropriate for short commands that complete inside the execution envelope, but it is not a reliable control plane for long-running Hadoop workloads.

For long jobs, the safer evolution is:

1. validate the request;
2. submit work and capture a stable job/application identifier;
3. return the identifier immediately;
4. query status separately from submission;
5. persist state and expose explicit terminal outcomes;
6. add idempotency so uncertain retries cannot create duplicate work.

Until that design exists, the project should not claim real-time background job tracking.

## Change-validation checklist

Before merging changes that affect submission behavior:

- run the unit test suite;
- verify shell quoting and job-class validation remain intact;
- confirm unknown SSH hosts are still rejected;
- confirm all network operations remain bounded by timeouts;
- document any retry/idempotency behavior change;
- test error messages for useful context without leaking credentials;
- distinguish a submitted job from a completed job in documentation and API responses.
