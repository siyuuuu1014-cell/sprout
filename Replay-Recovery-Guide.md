# Independent replay recovery

## Versioned coding boundary configuration

`replay-compare --coding-image <immutable-image-id-or-digest>` records a v5 plan
with `coding_profile`: the fixed coding image, `node-json-v1` host acceptance
protocol and mandatory explicit auxiliary-check adapters. Baseline and candidate
must use the same profile. A mutable tag or unsupported protocol is rejected.

This option currently binds configuration only. It does not commission an image,
install a production execution session, download dependencies or grant approval.
The full coding path still waits until session, Git management and auxiliary
checks are safely assembled. Legacy plans remain readable but no coding profile
is backfilled; they cannot supply evidence of this new boundary.

## Explicit regression oracle

`replay-compare --regression-spec <outside-repository.json>` accepts a local,
operator-selected JSON oracle (regular file, at most 64 KiB). It is read once
before either run and normalized/frozen separately in each experiment directory
as `regression-spec.json`, before coding. The same digest must bind both runs.
Example payload:

```json
{"version":1,"cases":[{"id":"existing_boundary","target":"app:calculate","args":[0],"kwargs":{},"expected_output":0,"category":"boundary"}]}
```

Use actual existing behavior and public Python functions, not the illustrative
target above. Missing configuration leaves independent regression measurement
waiting; pytest regression commands remain auxiliary observations, never a
substitute. Recovery cannot replace the frozen oracle, and a changed/missing
file with a recorded digest blocks recovery before coding. Each function and
regression case uses a distinct durable execution identity and dedicated export.
These records still do not authorize growth validation or publication.

## Scope

Only the original operating-system account may explicitly recover a new local
CLI experiment parked at image preflight. The existing isolated task and
databases are reused; the source task and its nodes are never executed.
Operational, metadata, session, context, usage, audit, blobs, trajectory, and
long-term memory stay under the experiment directory; graph is disabled and
knowledge/cache/vectors are in memory rather than source-project services.
Old experiments missing a trusted creator/configuration/runtime snapshot cannot
be upgraded in place for recovery. There is no Web/MCP, force, release, or retry
entry for uncertain effects.

## Commands

Use the isolated task ID and `metadata.db` under its `evidence_dir`, not the
source task ID or the normal project database:

```powershell
uv run sprout evolution replay-recovery-check <isolated-task-id> --database "<evidence-dir>\metadata.db" --config "<original-config>"
uv run sprout evolution replay-resume <isolated-task-id> --database "<evidence-dir>\metadata.db" --config "<original-config>"
```

The check is read-only and does not assemble a Runtime. Its
`current_runtime_snapshot_required` blocker is expected when the other static
checks pass; it is not permission to run. The resume command performs the
isolated assembly and the remaining snapshot checks.

If the image inspection needs review, resume returns a pending `approval_id`
without claiming recovery or running the model. After reviewing that request,
the human can explicitly approve the exact ID and recover:

```powershell
uv run sprout evolution replay-resume <isolated-task-id> --database "<evidence-dir>\metadata.db" --config "<original-config>" --approve <exact-approval-id>
```

This records the decision through the existing ApprovalManager, not the generic
session/task resume route. Only a pending `process_run` request for that same
task, CLI source, and creator is eligible. Current policy DENY still wins. No
image build/download or Docker auto-allowlist is introduced.

## Sequence and outcomes

Current OS account → static evidence/config checks → isolated Runtime/candidate
assembly → registry snapshot match → current image policy/approval/readiness →
repeat evidence checks → single durable recovery owner → repeat evidence checks
→ existing task execution → Docker-only independent measurement → evidence and
owner settlement.

- A pre-claim wait is safe to revisit after resolving its stated blocker.
- Once claimed, crashes, cancellation, evidence changes, or unknown settlement
  retain active/uncertain ownership. Do not start another experiment to bypass it.
- `completed` recovery ownership means the recovery operation was recorded, not
  that coding/tests/independent validation passed. Inspect task status and
  `measurement_status` separately; `validation_committed` remains false.
- A completed coding task may still have a waiting independent measurement.
  This command does not restart coding to resolve it.
- Ordinary `Runtime.execute`/`resume_task` cannot directly execute the initial
  replay preflight wait, including through a stale CREATED task object.

## Boundaries

Generic coding still uses existing Runtime execution/approval/sandbox policies;
only independent measurement is Docker-only. Same configuration is not a full
dependency/supply-chain proof. External MCP clients are blocked in this recovery
entry. Filesystem checks are repeated but are not an OS isolation guarantee
against a concurrently malicious local process. No automatic candidate
validation, promotion, publication, or paired-manifest replacement occurs.

Tests exercise real isolated SQLite/Runtime assembly and ApprovalManager /
ProcessBroker. Opt-in real Docker acceptance also covers approved container
measurements, client timeout with a still-running container and retained
uncertain ownership, and a refused local Docker endpoint followed by successful
recovery before any execution claim. The existing daemon is not stopped;
test-only, labelled containers are removed only after naturally stopping, with
inspection evidence retained.

Coding completion is a controlled fixture, not a production model execution.
The narrow Docker-review policy is test-only; it does not modify production
allowlists. These tests do not prove daemon restart/crash recovery, a production
model workflow, or arbitrary plugin compatibility. Independent reports remain
untrusted and do not automatically validate or publish candidates.
