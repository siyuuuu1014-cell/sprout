# R01-WIN-FIX: legacy verification provenance boundary

Date: 2026-10-19. Base: `2a7fe37705e97562c9bca7c5db7dcfd57fc4f4e0`.
Branch: `codex/r01-win-acceptance-probe`. Local-only follow-up; PR #2 stays Draft.

## Observed failure and regression

The prior review added `test_legacy_acceptance_backend_boundary.py` without
changing production code. It reproduced **3 failed, 9 passed, 0 skipped**:

- The same in-memory, genuinely approved legacy proposal was rejected by Local
  Runtime, but switching to Temporal made public direct Apply call mock ApplyBroker.
- Temporal and isolated configuration report projections returned current verified=true.
- The proposal had historical passing checks but neither local_executor nor
  local_baseline_valid, and no verification override. The stored bytes were not upgraded.

Mock ApplyBroker returned applied=false, so this proved entry into the forbidden
effect boundary, not a real Git commit. No user repository or database was applied to.

The original failure assertions are retained unchanged. After the production fix,
the original twelve plus the existing nine Apply regressions gave **21 passed**.
The expanded boundary, prior Local binding/execution/Apply and R00 batch gave
**132 passed, 0 failed, 0 skipped**. A final context-consistency regression then
observed **1 failed, 2 passed, 36 deselected**: with a valid Local binding but current
isolated configuration, Runtime's direct service remained enabled while its report
was unverified. The same approved runtime.py change now restricts _changes() in
isolated configuration too. No worker selection is changed. The final targeted
batch is **135 passed, 0 failed, 0 skipped**. During test authoring, Ruff detected a misplaced
function tail and import ordering; these were corrected before the clean snapshot.
That intermediate lint failure is not a passing test result.

## Source-backed change

- `execution/acceptance.py:117`: shared read-only verification_evidence_applicability.
- `runtime/changes.py:154`: unconditional gate before ApplyBroker, including standalone service.
- `runtime/nodes.py:1164`: same gate before service and direct-broker compatibility routes.
- `runtime/runtime.py:2227`: unconditional historical/current report projection.
- `runtime/runtime.py:577`: current isolated configuration restricts Local service use,
  consistently with the report projection; configuration never establishes provenance.

The existing Local v1 frozen binding plus local_baseline_valid is checked using
the server-resolved environment, never a caller-declared backend. The decision is
local_match, local_mismatch or unknown. No trusted_nonlocal result is fabricated.
The server assembly's local_execution_available is only an additional restriction:
True alone proves nothing; False cannot reinterpret old evidence as non-Local.
The predicate reads existing identity inputs and has no persistent writes.

Only local_match permits normal Apply, alongside all existing verification,
approval, policy and isolation gates. Unknown/mismatch do not call ApplyBroker or
save proposal state. Existing force + non-empty reason keeps its explicit audit,
adds the provenance blocker to other blockers, and remains verified=false.

Reports retain historical_verification and project current verified=false/status=incomplete
for unproven evidence, without modifying storage. Existing public current_applicability
values match/mismatch/unknown and existing failure-code vocabulary are retained.

## Why no trusted_nonlocal branch

The existing proposal verification envelope contains checks/spec/demo/baseline/results
but has no checked non-Local provenance envelope binding that proposal's exact
verification evidence to an applicable executor. Temporal submission records prove
submission/scheduling, not the origin and applicability of this proposal's passing
checks (see orchestration/terminal/temporal.py:93 and :119). A workflow/backend name,
legacy acceptance_fingerprint or free-form metadata is insufficient. Existing
isolation markers are restrictions, not positive authority.

Consequently historical non-Local records without adequate proof are unknown, not
misclassified as Local. Tests explicitly reject claimed temporal/docker/trusted flags
and legacy fingerprints. Supporting positive non-Local provenance requires a separately
reviewed producer-to-consumer evidence contract, not a fabricated success fixture here.

## Isolation and compatibility

The replay_plan/replay_source_task_id guard in changes.py:144 remains before Apply.
NodeExecutor's isolated_coding_required guard remains before the new predicate.
No host path is injected into a container or worker. Worker selection is unchanged.

No Schema, dependency, ApprovalManager, ProcessBroker, frozen Oracle, fixed scenario,
old test assertion or persisted history is changed. The intentional behavior change:
unproven legacy non-Local evidence can no longer authorize normal Apply or present
current verified=true. Re-establish applicable evidence; do not rewrite old records.
General adversarial Local Python isolation/attestation remains outside this patch.

## Reproducible verification

Targeted invocation (absolute Python from the prior task-owned venv; PYTHONPATH
explicitly points at this worktree's src and root, no production config loaded):

```powershell
& $taskPython -B -m pytest -p no:cacheprovider -q src/Sprout/tests/test_legacy_acceptance_backend_boundary.py src/Sprout/tests/test_local_acceptance_apply.py src/Sprout/tests/test_local_acceptance_execution.py src/Sprout/tests/test_local_acceptance_binding.py src/Sprout/tests/test_skills_disclosure.py --basetemp=D:/dev/projects/sprout/.workbuddy/test-runs/r01-provenance-targeted-final --tb=short
```

Clean runner (tracked working-tree snapshot, new venv/home/temp/cache, credential-free
environment; only named task files staged to include the new test in the snapshot):

```powershell
& D:/dev/runtimes/conda/python.exe scripts/ci_baseline.py --output D:/dev/projects/sprout/.workbuddy/test-runs/r01-provenance-clean-final --python D:/dev/runtimes/conda/python.exe
git diff --check
git diff --cached --check
```

The runner executes uv lock --check, uv sync --locked --group dev, imports,
Ruff check . --no-cache, complete collect, examples, R00 and full baseline-l1.
Exact argv and local raw logs/JUnit/source-manifest are retained in that output
directory; never upload the directory wholesale. No CI workflow/upload change.

The initial clean snapshot r01-provenance-clean-1 completed with 2491 collected,
2432 passed, 0 failed, 1 skipped, 58 not_run, 2 warnings (287.14s). It precedes the
last isolated-configuration consistency fix and is not final-code acceptance.

Final clean run: **2435 passed, 0 failed, 1 skipped, 58 not_run**, 2 warnings,
286.60 seconds. Python 3.13.9, uv 0.11.25, Ruff 0.16.8. Lock check resolved124;
locked sync installed68 in a new venv. Imports and full Ruff exited0.
Complete collection: **2494**, zero errors (4.29s). Examples **18 passed**;
R00 **67 passed**, neither skipped. Final five source/test hashes match the
immutable source-manifest.json; only this audit is added after that snapshot.

Selected groups inside the same full L1 (not additional disjoint totals):

| Group | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| New provenance/backend boundary | 39 | 0 | 0 |
| Existing Local Apply/report | 9 | 0 | 0 |
| Existing Local child/approval | 18 | 0 | 0 |
| Existing binding regression | 2 | 0 | 0 |
| Original Task E2E | 13 | 0 | 0 |
| Evaluation runner | 7 | 0 | 0 |

The persisted fixed-suite report confirms all ten A01/A02/A03/C01/C02/C03/C04/C05/R01/R02
scenarios passed, completed_code_tasks=4, model_quality_measured=false. In particular
expected failing/unverified scenarios retained their independent Oracle outcomes;
scenario_passed does not mean every task completed successfully.

The one existing skip is test_authz::test_read_broker_rejects_symlink_escape,
reason: symlinks are unavailable in this environment. It is not a security pass.
The 58 not_run tests are unchanged explicit external gate selections. The two
warnings are third-party AnyIO BlockingPortal and protobuf utcfromtimestamp
deprecations, not suppressed. The initial attempt to read pytest-temp evidence
was denied by the sandbox; the approved read-only retry obtained only fixed
scenario IDs/boolean verdicts/counters. No raw task evidence was published.

New remote Ubuntu/Windows Actions and live Docker/Temporal/model/database checks
are not_run for this patch. Prior green CI at 2a7fe377 is historical evidence only.

## Git, rollback and stop

Only the four approved production files, one boundary regression and this audit
belong to the follow-up. The original b7f759c README deletion stays untouched on
the original branch. No push, merge, Ready transition, dev change or R02 work.

Rollback by a separately reviewed revert of this follow-up commit; no schema/data
rollback is required. A revert reintroduces the known boundary weakness and is not
a safety endorsement. Stop after the local commit for review and push authorization.
