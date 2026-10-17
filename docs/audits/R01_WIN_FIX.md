# R01-WIN-FIX — Local Acceptance executor binding

Date: 2026-10-18. Status: locally implemented and verified; not pushed.

## Scope and authority

- Worktree branch: `codex/r01-win-acceptance-probe`.
- Parent: `436b1731ee1937d49a99fc41d93b088a10f1881d`.
- Remote PR #2 remains Draft at the parent; no push, merge, Ready transition or R02.
- `dev` remains `32e5f734fc556f5ccdce152b8ec949b75021fbd6`.
- Original branch `codex/r01-baseline-ci` retains `b7f759c19722c8a0fb9e9b662504163b2a35af57`.
- The pre-existing untracked `test_local_acceptance_binding.py` was preserved unchanged and included.
- Production edits are confined to acceptance.py, process_broker.py, nodes.py, changes.py and runtime.py.
- No schema, dependency, AcceptanceSpec, fixture initial content, fixed-suite expectation,
  generated input/output assertion, independent Oracle or CI gate changes.

### File-level diff inventory

| Path | Change |
| --- | --- |
| src/Sprout/execution/acceptance.py | Server binding, controlled child protocol, narrow red classification, applicability helpers |
| src/Sprout/execution/process_broker.py | Dedicated bound route and additional versioned approval fingerprint input |
| src/Sprout/runtime/nodes.py | Freeze/reuse baseline identity, strict evidence checks, normal apply guard |
| src/Sprout/runtime/changes.py | Standalone direct-apply guard, preserve explicit unverified force override |
| src/Sprout/runtime/runtime.py | Current/historical read projection and preserve external scheduler selection |
| src/Sprout/tests/test_local_acceptance_binding.py | Preserve existing untracked regression unchanged |
| src/Sprout/tests/test_local_acceptance_execution.py | Actual child and approval-negative regressions |
| src/Sprout/tests/test_local_acceptance_apply.py | Direct service/node apply and current-report regressions |
| src/Sprout/tests/test_task_e2e.py | Explicit ordinary-command approval prerequisite; original assertions unchanged |
| docs/audits/R01_WIN_FIX.md | This audit, failure history, commands, compatibility and rollback |

## Root cause and implementation

Remote diagnosis previously established an unexpected Acceptance interpreter without pytest.
That remote diagnosis is not a new CI run of this patch.
The previous read-only, in-memory reproduction also established that the old task-wide
verification approval did not distinguish executable A from B; no process was spawned.

`execution/acceptance.py` chooses the running service's absolute `sys.executable`,
without resolving away a POSIX venv symlink. No PATH search, alternative interpreter search,
dependency installation or host fallback occurs. The argv is a sequence:

```text
[absolute_service_python, -I, -c, server_owned_launcher_source, frozen_case_payload]
```

`-I` is Python's isolated startup mode, NOT a Docker or OS sandbox. The server-owned
launcher source is copied from this module, not loaded from the task workspace.
The generated test source and its original expected-output assertions are unchanged.

The version-1, domain-separated `local_executor` binding contains only
`version`, `domain`, and a SHA-256 `fingerprint`. Its private inputs include executable
path/content, Python version/prefixes, venv configuration, installed distribution names/versions,
pytest/_pytest source contents and the launcher protocol source digest. Paths are not exposed
in the binding. The actual child independently computes and compares its binding, imports
pytest and observes collection/call phases. Unavailable or unprovable identity is not a match.

The TEST_DESIGN output freezes the binding and `local_baseline_valid`; EVALUATION reuses it.
Historical frozen designs without a binding are NOT upgraded in place. Configuration waits
that have not yet frozen a spec may establish a new binding once the environment is available.
Baseline, agent entry, formal acceptance and normal apply fail closed on inadequate evidence.

### Red baseline evidence

Only these categories are allowed as pre-implementation red evidence:

| Category | Required observed evidence |
| --- | --- |
| assertion_failed | Exact frozen test file/case executed; assertion fails at its original Oracle line |
| requirement_not_implemented | Exact target module imported from its pinned source; missing function at target resolution; no dynamic module getter or nested traceback |
| target_module_not_implemented | Exact frozen target import fails during the controlled test; filesystem/import-spec evidence independently shows the target absent; explicit parent packages exist and import from their expected paths |

The launcher checks the frozen generated-test digest, target/preimage and parent-file
digests before and after execution. For a missing module, conventional source/bytecode/extension
suffixes and a same-name directory are also checked. Changed parent search paths, dynamic
getters, ambiguous directories, missing parents, dependency/import/call exceptions, collection
failure, missing pytest, absent execution and incomplete evidence cannot establish a red baseline.
Classification uses structured pytest call/traceback objects and workspace evidence, not log signatures.
The parent requires one protocol record with matching identity, collection/execution confirmation
and an appropriate exit code. Missing/inconsistent evidence becomes unknown/not_executed.
No red classification is accepted as a final passing Acceptance result.

### Approval boundary

`ProcessBroker.run_local_acceptance` resolves identity itself and builds its own command.
A caller's frozen binding is a restriction to compare, not authorization or self-attestation.
Broker-owned async-local context scopes the dedicated call; no caller `trusted=True` flag exists.

Ordinary `run()` approvals retain `{action, task_id, resource_scope}`. Only the dedicated
Acceptance route adds `local_acceptance_executor_v1` to the existing approval arguments.
No ApprovalManager/schema change, allowlist expansion or auto_run expansion occurs.
Legacy approvals cannot authorize the bound route; A cannot authorize B. A matching binding
retains task-round reuse. DENY still wins, and upstream CLI approval cannot bypass binding approval.

The CLI resume test now explicitly grants the separate ordinary-verification prerequisite
before exercising CLI approval of bound Acceptance. Its original completion assertion is intact;
the change does NOT turn a binding grant into a broader ordinary-command grant.

### Apply, read projection and other backends

Standalone ChangeProposalService defaults to enforcing current Local evidence applicability.
Only match permits ordinary Local apply; mismatch/unknown return before ApplyBroker and do not
write proposal state. NodeExecutor's direct ApplyBroker compatibility branch enforces the same guard.
Explicit force remains available with its original reason/audit requirements; override evidence
retains `verified=false`. No old record or authorization is rewritten.

`get_verification_report` preserves existing top-level fields and adds a read-only
`historical_verification` report and `current_applicability` for applicable Local evidence.
Mismatch/unknown downgrade current verified to false/incomplete without changing stored history.

Temporal and isolated paths retain their own execution selection. External scheduler node
execution disables Local binding on its freshly built executor/service. A Local-marked proposal
cannot be reinterpreted as non-Local merely by changing the service configuration. No worker or
container receives a submission-host executable path. No live backend claim is made by these unit tests.

## Verification and failure history

Existing bare-python regression before this implementation: 1 failed, 1 passed (previous turn).
After the initial helper change, acceptance contract/preflight/binding selection: 9 passed.

All intermediate failures remain recorded here rather than described as green:

| Batch | Actual outcome | Explanation/action |
| --- | --- | --- |
| First child regression attempt | 3 passed, 10 errors | System pytest temp directory permission error; moved to new task-owned basetemp |
| Child attempts 2 and 3 | Each 9 passed, 4 failed | Windows text normalization, then pytest collection-hook timing; corrected observations |
| Child boundary regression | 11 passed | Real subprocesses distinguish valid/invalid red baselines |
| First apply projection regression | 7 passed, 1 warning | Imported TestResult alias later removed that extra collection warning |
| Expanded security regression | 21 passed, 1 warning | Approval and applicability tests |
| Fixed suite + Task E2E first run | 9 passed, 11 failed | Fixed suite passed; E2E rootdir-dependent node identity rejected actual calls |
| Child + Task E2E second run | 24 passed, 1 failed | CLI test lacked separate ordinary-verification prerequisite approval |
| CLI + child regression | 14 passed | Original completion assertion retained |
| Clean environment first attempt | lock passed, sync failed | hatchling download timed out; downstream checks not_run |
| Clean snapshot 2 | 2383 passed, 5 failed, 1 skipped, 58 not_run; 3 warnings | Four isolated test-object attribute errors and CLI prerequisite; corrected without lowering assertions |
| Clean snapshot 3 | 2393 passed, 0 failed, 1 skipped, 58 not_run; 2 warnings | Before final two ambiguity regressions |
| Final targeted boundary suite | 28 passed, 0 failed, 0 skipped | Includes dynamic parent paths and ambiguous target directory |

Final immutable snapshot/evidence directory:
`D:/dev/projects/sprout/.workbuddy/test-runs/r01 win fix final/`.
This directory is ignored local test evidence; do not upload it wholesale.
It contains a new checkout, new venv, isolated test HOME/temp, source-manifest.json, logs and JUnit.
Dependency artifacts were reused from the earlier task-owned uv cache; no existing venv was modified.

Final source snapshot: 2454 collected, 2395 passed, 0 failed, 1 skipped,
58 explicitly not_run (pytest displays deselected), 2 warnings; 295.13 seconds.
R00: 67 passed. Examples: 18 passed. Ruff/imports/locked sync/lock check: exit 0.
Fixed suite: 10/10 scenario_passed, 4 completed coding tasks, model_quality_measured=false.
All original Task E2E assertions, including the four Windows failures, passed in that L1 batch.

A final confirmation run is in `sanitized/` under that same directory. It removes every
non-whitelisted environment variable from its own PowerShell process before launching pytest,
then injects only the test HOME/temp, fixture Git identity and offline bootstrap configuration.
Earlier manual verification batches retained the launching process environment; they are
functional evidence, not the final credential-free environment claim.
Sanitized confirmation: **2395 passed, 0 failed, 1 skipped, 58 not_run**, 2 warnings,
272.95 seconds. Full collection: **2454**, zero collection errors (3.79 seconds).
R00: **67 passed**. Examples: **18 passed**. Imports and Ruff: exit 0.
The final nine production/test file hashes were compared with source-manifest.json and matched.
Only this audit document was added after the immutable source snapshot.

Selected JUnit groups (all included in full L1, not additional disjoint totals):

| Group | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| Preserved binding regression | 2 | 0 | 0 |
| Child/binding/approval boundary regressions | 18 | 0 | 0 |
| Apply/read projection regressions | 8 | 0 | 0 |
| Original Task E2E | 13 | 0 | 0 |
| Evaluation runner tests (including ten scenarios) | 7 | 0 | 0 |

One additional normal-execution (NOT preflight) in-memory probe approved A, switched the
server identity to B, and invoked the bound route with subprocess creation instrumented:
new approval required, allowed=false, spawn count=0, persistent writes=0.

The sole existing skip is `test_authz.py::test_read_broker_rejects_symlink_escape`:
symlinks are unavailable in this environment. It is not a verified security pass.
The 58 not_run entries are the existing explicit gate selection, unchanged by this patch:
databases=6; docker=17; docker_faults+temporal=2; docker+model+temporal=1;
docker+temporal=10; model+temporal=1; temporal=21 (disjoint gate-set counts).

Observed frozen baseline evidence from final sanitized fixed-suite node records:

| Case | Baseline categories | Baseline valid | All evaluation bindings match | Final |
| --- | --- | --- | --- | --- |
| C01 | passed, assertion_failed | true | true | completed, verified |
| C02 | target_module_not_implemented | true | true | completed, verified |
| C03 | target_module_not_implemented | true | true | completed, verified |
| C04 | target_module_not_implemented | true | true | failed, unverified |
| C05 | target_module_not_implemented | true | true | failed, unverified, overridden |
| R01 | target_module_not_implemented | true | true | completed, verified; no repeated apply |

These are scripted-model Runtime/verification/delivery results, not paid-model coding-quality evidence.

### Exact final command families

PowerShell execution used the final snapshot checkout as cwd, absolute venv Python,
test-only `PYTHONPATH` containing `scripts/ci_support` and checkout, explicit isolated
`SPROUT_BASELINE_HOME`, `SPROUT_BASELINE_OFFLINE=1`, task-owned TEMP/TMP and Git fixture identity.
No production .env, user database or provider credentials were copied.

```powershell
uv sync --locked --group dev --offline --cache-dir D:/dev/projects/sprout/.workbuddy/test-runs/r01-win-probe-clean-1/uv-cache --python D:/dev/runtimes/conda/python.exe
uv lock --check --offline --cache-dir D:/dev/projects/sprout/.workbuddy/test-runs/r01-win-probe-clean-1/uv-cache --python D:/dev/runtimes/conda/python.exe
# $taskPython is the absolute final/venv/Scripts/python.exe path (containing spaces).
& $taskPython -B -c 'import Sprout, Sprout.cli.app, web.webapi.app, pytest, ruff; print("imports OK")'
& $taskPython -m ruff check . --no-cache
& $taskPython -B -m pytest -p scripts.ci_support.pytest_gates -p no:cacheprovider --collect-only -q src/Sprout/tests web/tests --junitxml=../collect.xml --gate-report=../collect-gates.json
& $taskPython -B -m pytest -p scripts.ci_support.pytest_gates -p no:cacheprovider -q src/Sprout/tests/test_skills_disclosure.py --junitxml=../r00.xml
& $taskPython -B -m pytest -p scripts.ci_support.pytest_gates -p no:cacheprovider -q src/Sprout/tests/test_bubble_sort.py src/Sprout/tests/test_hello_world.py --junitxml=../examples.xml
& $taskPython -B -m pytest -p scripts.ci_support.pytest_gates -p no:cacheprovider --tb=short -o faulthandler_timeout=60 -q src/Sprout/tests web/tests --baseline-l1 --basetemp=../pytest-temp --junitxml=../l1.xml --gate-report=../l1-gates.json
git diff --check
git diff --cached --check
```

The sanitized confirmation used the same command families with reports under `../sanitized/`,
basetemp `../sanitized/pytest-temp`, and a separate sanitized test HOME/temp. Before setting
those test variables, its temporary PowerShell process retained only PATH, SYSTEMROOT, WINDIR,
COMSPEC, PATHEXT, SYSTEMDRIVE, NUMBER_OF_PROCESSORS, PROCESSOR_ARCHITECTURE, LANG and LC_ALL.
No machine/user environment configuration was modified.

## Source anchors and acceptance status

- `execution/acceptance.py:45`: private identity inputs; `:117`: controlled observation;
  `:252`: structured argv; `:263`: parent-side protocol validation.
- `execution/process_broker.py:82`: broker-minted dedicated call; `:325`: bound approval arguments.
- `runtime/nodes.py:506`: frozen baseline; `:1152`: node apply compatibility guard.
- `runtime/changes.py:136`: direct service apply and force behavior.
- `runtime/runtime.py:2229`: historical/current read projection.

Local implementation/baseline DoD is met: no known selected offline test failures, no weakened
Oracle/assertion/CI gate, no production-file expansion beyond the five approved modules.
This does NOT mark the entire R01 PR accepted: new Ubuntu/Windows remote Actions and live
integration gates remain unrun. Await user review and separate push authorization; do not enter R02.

## Compatibility, residual risk and rollback

- Existing APIs remain callable; report fields and metadata are additive. No database migration.
- Old Local evidence without a binding is intentionally unknown: create a fresh task/evidence
  and approvals rather than modifying frozen history. Moving/upgrading a venv invalidates the binding.
- Namespace packages, dynamic importers and unprovable source origins remain fail-closed; no
  claim of general Python import resolution support is made.
- This is a logical Local runner, not adversarial-code isolation. Hostile same-process Python
  introspection or pytest plugins are not made safe by `-I` or a structured protocol.
- Binding covers the stated identity inputs, not a full immutable OS/filesystem attestation.
- New process calls still use the unchanged generic Broker policy, timeout, environment and auditing.
- Ubuntu/Python 3.12 and remote Windows Actions for this patch: not_run (push prohibited).
- Docker, live Temporal, real models and external databases: not_run; offline contracts are not
  live infrastructure validation. The existing pytest skip remains visible, not counted as passed.
- Rollback, if approved later: revert only this task's local commit on an independent branch;
  retain persisted metadata/history. No schema down-migration or user-data deletion is needed.
  Reverting restores the earlier interpreter/approval weakness, so it is not a safety endorsement.
- Do not push the original branch containing b7f759c. Stop after this local task for user review.
