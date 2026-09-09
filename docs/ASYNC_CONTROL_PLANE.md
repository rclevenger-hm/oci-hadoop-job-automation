# Async job control-plane design

The current OCI Function intentionally executes a bounded SSH command synchronously. That is appropriate for short submission/management commands, but it creates an ambiguous outcome when the function or SSH session times out after the remote command has already started.

This document defines a safe evolution path for long-running Hadoop workloads without pretending the current code already implements it.

## Goals

- accept a job request quickly and return a stable job identifier;
- make retries idempotent;
- persist state independently of a function invocation;
- separate submission from status retrieval;
- make remote-start ambiguity observable and recoverable;
- preserve strict SSH host-key verification and bounded network operations.

## Proposed flow

```text
client
  -> submit API/function
  -> validate request + idempotency key
  -> durable job record
  -> queue
  -> submission worker
  -> SSH / Hadoop submit
  -> capture Hadoop/YARN application ID
  -> update durable job record

client -> status API/function -> durable job record
```

## Job state model

Suggested states:

- `accepted` — request persisted but not yet claimed;
- `submitting` — worker owns the submission attempt;
- `submitted` — remote scheduler returned a stable application/job ID;
- `running` — optional state derived from the Hadoop/YARN control plane;
- `succeeded` / `failed` — terminal remote state;
- `submission-unknown` — SSH/function outcome is ambiguous and must be reconciled before retry;
- `rejected` — request failed validation before remote execution.

Do not represent an SSH timeout as `failed` unless cluster state proves the command did not start.

## Idempotency

Require an idempotency key scoped to the logical job request. Persist the request fingerprint with that key. A replay with the same key and same fingerprint returns the existing job record; the same key with different inputs is rejected.

The worker should acquire/record a submission lease before opening SSH so concurrent queue delivery cannot submit the same job twice.

## Remote identity

The submission command should emit or allow discovery of the stable Hadoop/YARN application identifier. Persist that identifier immediately. Subsequent status checks should query the cluster using that ID rather than keeping the original SSH session open.

## Ambiguous submission reconciliation

When the SSH connection drops or times out during submission:

1. move the record to `submission-unknown`;
2. do not automatically submit again;
3. query cluster history/state using a correlation marker where possible;
4. if the existing application is found, attach its ID and continue tracking;
5. only retry submission after proving no application exists for the idempotency key/request.

This is the core safety property that the synchronous implementation cannot currently provide.

## Reliability controls

- queue/DLQ with bounded retry count;
- worker concurrency limit sized to the SSH endpoint/cluster;
- explicit connect/auth/command timeouts;
- lease expiry for abandoned `submitting` records;
- alarms for queue age, DLQ depth, records stuck in nonterminal states, and reconciliation failures;
- structured status transitions with timestamps/reason codes;
- no private key or raw credential material in job records/logs.

## Operational API

A minimal interface could expose:

- `POST /jobs` -> accepted job ID/status;
- `GET /jobs/{id}` -> state, remote application ID when known, timestamps, safe error summary;
- optional operator-only redrive/reconcile action rather than a public arbitrary retry endpoint.

## Migration path

1. Add a durable job-record abstraction and tests while retaining synchronous execution.
2. Add idempotency behavior at intake.
3. Introduce queue + worker submission behind the same validator/command builder.
4. Capture remote application IDs and implement status polling.
5. Add `submission-unknown` reconciliation tests using simulated SSH timeouts.
6. Move long-running workloads to async mode; retain synchronous mode only for explicitly bounded management commands if still useful.

## Acceptance evidence

Before calling async submission production-ready, demonstrate duplicate queue delivery without duplicate remote jobs, timeout-after-start reconciliation, worker crash/retry recovery, DLQ/redrive behavior, stale lease recovery, and status consistency through remote application completion.
