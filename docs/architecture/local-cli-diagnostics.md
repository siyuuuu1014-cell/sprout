# Temporary local CLI approval diagnostics

Baseline: `bf0a902d628fc0bc9c18ec8ffad36fcf3d59ff46` (dev).
This diagnostic switch does not disable the production security layer.

## Enable and disable

In PowerShell, using the source checkout under review:

```powershell
$env:PYTHONPATH = "C:/Users/WingYouther/.codex/worktrees/temporal-t0-t1/sprout/src"
$env:SPROUT_DEV_NO_HUMAN_APPROVAL = "1"
& D:/dev/projects/sprout/.venv/Scripts/python.exe -m Sprout.cli.app chat
```

Run from the workspace to inspect. The CLI still confirms which workspace is
selected; selecting a workspace does not replace Scope or Policy checks. Existing
model configuration and its normal environment loading apply. No model setting
or credential is changed by diagnostics.

To disable: exit the CLI, then remove the variable and start a new session:

```powershell
Remove-Item Env:SPROUT_DEV_NO_HUMAN_APPROVAL -ErrorAction SilentlyContinue
```

Only `chat` without a one-shot message or `--session` activates the switch. Both
input and output must be interactive terminals. `TEMPORAL_HOST` must be unset.
Web, MCP, service entry points and normal Runtime construction do not inspect the
switch and keep their original approval behavior. There is no persistent config
flag, schema migration, global Policy override or command auto-run expansion.

## Data and lifetime

Each launch prints a newly created `sprout-cli-diagnostic-*` temporary directory.
All SQLite lanes, observations, audit, memory, blobs, trajectories, project data
and skill directories are redirected there. MCP clients and evolution attachment
are disabled for this diagnostic session. Original user databases are untouched;
no old Session, approval or Task is imported or deleted.

The existing ApprovalManager implementation and record/storage contracts are
reused via a CLI-only subclass. Diagnostic decisions are labelled **not human**.
Records expire after 60 seconds. Normal exit conditionally consumes outstanding
diagnostic records before Storage closes. New launches use a different directory.
After abnormal termination, temporary evidence can remain on disk until manually
removed; no normal launch loads these databases or inherits a diagnostic grant.
No automatic deletion is performed.

## Current supported and refused effects

- Ordinary chat continues through the existing model path.
- Explicit-file and directory project analysis use the existing Task, read-only
  Scope, project-analysis tools and FileBroker. Only the human confirmation is
  substituted. Directory analysis retains its existing exact approval record,
  but creates no APPROVAL_REQUESTED event and leaves no waiting request.
- A Host-assembled IsolatedCodingCommandTool may receive a temporary coding
  grant through the existing PlanBoundCodingCommands or a live Filesystem
  Experiment. Both require the active node owner and an authenticated,
  workspace-bound foreground CLI Session.
  Actual image checking, Docker plan, ProcessBroker checks, import rules and
  Coding Execution Store remain mandatory. The mode does not construct or
  commission an isolated execution environment.
- Workspace requests keep their ordinary consent path. A live node in the
  current Session's bound Workspace can use the existing FileBroker-backed
  read, write and edit tools. If the tool gate asks for approval, diagnostics
  approves the exact invocation, then FileBroker independently checks the
  resource, scope and effect. The tool can still fail after approval.
- Unconfined host commands, unclassified/plugin tools, network and operational
  REPL slash commands remain unavailable. The working directory alone does
  not confine a host program.
- The interactive CLI shows observed intent, workspace, task/node stages,
  automatic decisions and tool start/result markers. The progress lines omit
  tool arguments and model reasoning.
- Detached and background Tasks cannot inherit this foreground capability.
  Diagnostic Apply is limited to the exact Filesystem v3 plan after independent
  Verification. Git ChangeProposal approval, Git Apply, Commit and Push remain
  outside this diagnostic grant.
- DENY always retains precedence. File access, identity, budget or provenance
  failures remain failures. No os.walk/rglob fallback is added.

## Historical validation

The original diagnostic-mode targeted run recorded **393 passed**, including diagnostic tests, original A1/A2
approval tests, ToolExecutor, FileBroker, Windows directory discovery, directory
Policy, project offers, clarification and budget regressions. This is not full CI.
The initial switch/data regression run failed six tests before implementation.

Real Docker diagnostic test: `test_local_cli_diagnostics_docker.py`, fixed local
image `sha256:11880faa03b5dda1f3f54a915ad1b0e3d2a97664799d7a9bfcd3790a9dc7a31d`.
The deterministic assembly fixture has synthetic prior verification outputs; the
claimed real execution is only the subsequent Coding command through the real
PlanBoundCodingCommands, Docker backend and ProcessBroker. One container launch,
exit 0, stdout `diagnostic-docker-ok`, zero human prompts, zero pending records,
one diagnostic capability record. A second call was refused and the record was
consumed at foreground exit. This is not full CLI Coding Task/Temporal acceptance.

Real Windows CLI with current source and configured DeepSeek provider:

| Flow | Normal mode evidence | Diagnostic mode evidence |
| --- | --- | --- |
| Capability offer -> confirmation -> small Python project | Directory confirmation required; real analysis returned | No directory approval prompt; same real project name/language/function graph returned |
| Original project root discovery | Windows secure_open_refused:5 at a cache directory; no analysis success | Explicit project_auto_discovery_unavailable: directory_access_denied; no bypass |
| Model summary after tool result | DeepSeek HTTP 400: reasoning_content required | Same protocol error; real tool result still displayed |
| Host subprocess | Existing approval behavior retained by regression | Denied before spawn; no pending approval |

Final additional real CLI Session: `b2c551a6-c8ef-4d81-b8cb-3c872defb077`.
Ordinary `你好` returned a real model greeting with no approval. Explicit
`分析 src/app.py` read exactly that file and returned a real function graph
without confirmation; its follow-up model summary had the same HTTP 400.

Diagnostic sample Session: `26543e7e-e8c6-44c7-ada2-c6ec4e9faa31`.
Diagnostic original-workspace Session: `1c343812-f512-4b6f-a99e-d34c8b3c4f8a`.
The original-workspace diagnostic database held one directory.list record; after
exit it was consumed. The original user audit database had zero approval records
for that diagnostic Session. The model summary failure was independent of
human approval at that stage. The later DeepSeek provider fix addressed the
thinking-mode request contract, and real project analysis subsequently returned
a model summary after its tool result.

Those early runs predate G1 through G3-B. The current NodeExecutionGuard
distinguishes a Filesystem Apply resource wait before writing from an unknown
write outcome. Temporal, GitBroker, the base ApprovalManager and production
Policy settings remain outside this change.

## Rollback

This work is uncommitted. To stop using it, close the diagnostic CLI, unset the
environment variable and run the stable dev source. No database migration needs
rollback. Reverting the reviewed task-owned patch restores the source; preserve
other worktrees and their experimental changes. Temporary diagnostic directories
are evidence, not user databases, and are not automatically deleted.

## Filesystem Apply

A foreground, authenticated local CLI task can apply a non-Git ChangeSet when
the host supplies a pinned image and frozen acceptance and regression specs.
Normal CLI requests single-use approval after presenting the diff. Diagnostic
CLI can approve the same exact plan within the active, bound diagnostic session.

SQLite uses the existing Apply effect ledger and its workspace and target
exclusion indexes. Version 3 binds a Filesystem ChangeSet, settled Review and
Evaluation nodes, exact source versions and the approval reference. Git
versions 1 and 2 still require their original Proposal Snapshot. There is no
schema migration or backfill.

An unwritten file is PREPARED. A write records WRITE_STARTED before the
broker opens the target. WRITTEN records the hash read back by the broker.
A write refusal is REJECTED, and an effect without confirmed readback or
settlement is UNKNOWN. Multi-file Apply settles COMPLETED only after all files
have WRITTEN evidence. PARTIAL, UNKNOWN and abandoned WRITE_STARTED records
preserve the exclusion and require reconciliation. Reopening the database
does not transfer the write permit or replay a write.

Use the original authenticated session and database to inspect the persisted
review and transaction through /status. Diagnostic CLI launches create a new
diagnostic database, so a new launch does not reopen a previous diagnostic
session automatically.

A code rollback should retain the database and transaction records. Reverting
the adapter does not revert files that were already applied. Reconcile those
files against the transaction's recorded before and after hashes before any
separately authorized restoration.


## G1 through G3-B operating scope

The non-Git path is for an authenticated foreground local CLI Session with a
bound Workspace, explicit source inputs and output paths, and a pinned local
Docker image for command execution. For Apply, the Host must supply acceptance
and regression specifications in SPROUT_FILESYSTEM_VERIFICATION_SPEC and
confirm their fingerprint before Coding starts. The Docker Coding command can
run Agent-generated tests. Only the separate read-only Evaluation node running
the frozen Host specifications can verify a ChangeSet for Apply.

The initial G3-B Windows runs completed a new-file Apply in D:/sprout-model-test
and a two-file Apply in a disposable copy. Both used real DeepSeek and Docker.
After closing the CLI, reopening SQLite confirmed the hashes and denied duplicate
claims and write starts. A separate real Windows fault-injection run stopped
after writing two bytes to the second file. SQLite retained WRITTEN for the
first file and UNKNOWN for the second, with the Task waiting for reconciliation.
Those test files were kept as evidence.

Normal mode uses the same exact approvals with a human decision. The foreground
Filesystem path keeps the authenticated Task and Session live while prompting
for the Coding or scoped-file approval and later for Apply. Existing Git
Worktree and Git Apply use their original contracts. Filesystem Apply uses the
Windows native broker primitives. Background Filesystem Workers, resuming an
unfinished experiment from a rebuilt Runtime, and automated reconciliation or
rollback of uncertain writes are outside the supported scope.

## Stabilization evidence

Normal Windows CLI, with diagnostics disabled, exercised real DeepSeek and
Docker through independent verification and the exact human Apply prompt.
Rejecting Apply left app.py unchanged, did not create test_app.py, and recorded
a rejected approval without an Apply transaction. Approving a separate task
completed transaction a4652fa9-d27c-5878-bf34-f8ddc26b03ae and settled both
files and the Task. Reopening the same SQLite files in a new process rejected
the consumed approval, duplicate transaction claim, and duplicate write start.

Windows diagnostic fault tests used disposable non-Git workspaces and real
Docker. An external edit after Review and a newly occupied output path were
preserved. Each stopped before any Apply claim or write. A deliberately wrong
Host expected result ran in independent Evaluation and blocked Apply.
An injected SQLite settlement exception after a native write kept that file
UNKNOWN and prevented replay. A process exit after transaction settlement
left both file hashes verified and the transaction COMPLETED, while the Task
and Apply node still showed running. The ledger rejected a new write start
after reopening. There is no Task-status reconciliation entry point for this
case yet.

Exited Docker containers and diagnostic evidence directories can be retained
for inspection. A hard process exit may also leave the isolated filesystem
sandbox on disk. Preserve those artifacts during reconciliation and compare
file identities and hashes with the ledger before considering a separately
authorized restoration.

The first full Windows source-suite run recorded 6,477 passed, 32 failed and
179 skipped. A separate archive of bf0a902 reproduced 27 of those failures:
23 R02 CLI tests, one missing approval translation, one Windows GBK decoding
failure, and two R04 analysis assertions. The five remaining failures were
fixed in the cumulative patch. Three concerned Git Coding Import accessing a
Filesystem-only registry. Two covered Runtime shutdown and verification
provenance after shutdown. The new shutdown regression also checks that storage
closes when filesystem cleanup itself raises. These totals describe that first
run, not the final retest.

The final full Windows source-suite run recorded 6,484 passed, 27 failed,
179 skipped and no setup errors. The failed-test set matches the clean
bf0a902 comparison. The final focused suite recorded 214 passed and one
Windows symlink skip, and three separate Windows native Git Apply cases
passed. The Apply ledger suite was rerun after adding the Workspace and
Session mismatch cases, with 11 passed. Ruff scanned all 34 changed or new
Python files. Linux and hosted CI were not run.

## Functional closeout

When a Runtime starts with the same SQLite metadata database, it can now
reconcile a running Filesystem Apply node and Task from a COMPLETED v3 ledger
record. The operation verifies the complete per-file WRITTEN observations and
settles the node and Task in one SQLite transaction. It does not read or write
project files, reuse an approval or replay the Apply. UNKNOWN, PARTIAL and
unsettled records still require manual inspection. This supersedes the earlier
note about a missing completed-transaction Task-status reconciliation path.
A fresh diagnostic launch continues to use a separate database.

Filesystem independent verification now compares its export to the reviewed
manifest before and after persisting its intent and at each case boundary.
The evaluation does not replace the expected manifest with a fresh scan.
A sandbox deletion failure is reported separately from the settled business
result. It can leave temporary evidence for inspection.

The Apply approval wait Scope-narrowing issue remains deferred. This closeout
does not change the approval, Policy, Scope or Git contracts, or add recovery
for a partially applied transaction.

Windows closeout evidence: transaction 113cd6a1-83a2-51cb-9b77-ae8049ee5088
completed through normal CLI human approval in a disposable workspace.
Docker Coding and all three independent verification commands exited zero,
and both applied files matched the ledger. The same SQLite database from the
earlier hard-exit fixture (transaction b7a6422f-5fc0-52af-b90c-e62f35495b12)
was reopened by a new Runtime. Startup changed the running Apply node and
Task to completed, while file hashes and modification times stayed identical.
A new write start was refused. The targeted suite recorded 151 passed and
one Windows symlink skip. The final ledger-only suite including explicit
UNKNOWN coverage recorded 12 passed.

## G4 automatic foreground preparation — 2026-11-09

Current development base: `bbf109984aa23b226bc4865f80019bef13ee5069`.
The earlier manual READ/WRITE preparation instructions describe the historical
G1–G3 flow. Foreground non-Git CODE now performs Docker preflight, obtains a fresh
publication from the existing controlled project analyzer, and plans file inputs
and outputs without asking for READ paths, WRITE paths or an image string.
Diagnostic mode also skips the preparation confirmation; normal mode retains
explicit directory, plan, tool and Apply confirmations. Workspace selection,
Policy, private-resource rejection, file snapshots, independent Verification and
FileBroker conditional Apply remain in force. Git CODE preparation is unchanged.

### Implemented scope

- Small authorized Python projects (at most 64 captured inputs): derive module
  and test filenames from the real analysis inventory. Existing matching test
  files are updated; missing test files become creation candidates. Explicit
  file requests and README creation also have deterministic planning rules.
  Ambiguous, private, escaping or unresolved selections fail rather than grant
  authority. `filesystem_plan_v1` in Task metadata is planning data, not a
  credential; Sandbox capture reopens and validates inputs before execution.
- Inspect the actual Docker engine through ProcessBroker before creating the
  Coding Task. Prefer `SPROUT_FILESYSTEM_CODING_IMAGE` when pinned; otherwise
  inspect locally cached images bearing the existing isolated-coding role/schema
  labels and pin the selected image ID. Missing engines or compatible images
  return an early preparation error. Exact Host Docker inspections have their
  own short-lived approval records; no general host-command grant is installed.
- Existing CodingNodeSession and DockerCodingBackend still launch and settle
  real commands, import artifacts, and run independent Verification in separate
  containers. No execution or cleanup framework was replaced.
- Diagnostic `/status [task-id-prefix]` is read-only and limited to the owning
  Session and Workspace. It lists persisted node/Verification/Apply results and
  Coding execution settlement. It does not approve, resume, reconcile or retry.
  `live_container_state: NOT_QUERIED` means no live Docker state was queried;
  a persisted completed execution is not presented as a running-container probe.

### Actual Windows acceptance

Real production CLI, DeepSeek `deepseek/deepseek-flash`, `D:/sprout-model-test`,
fresh diagnostic SQLite data, no file-path or image input:

- Session `6ca2ec3d-b218-4052-9a51-eb8e54888eb6`.
- Engine available; local image automatically selected without setting the
  image environment variable:
  `sha256:11880faa03b5dda1f3f54a915ad1b0e3d2a97664799d7a9bfcd3790a9dc7a31d`.
- Initial test-generation Task `0f90d0dd-c41a-443b-a3c4-6579ab7e434f`
  ran Coding and found no content changes. Completed as `no_changes`;
  independent Verification was correctly `NOT_RUN` for that empty ChangeSet.
- Follow-up request explicitly added `test_add_complex_g4` to existing tests.
  Task `6548d9ff-ffe2-4937-ac62-b0bb689f23a5` selected `app.py, test_app.py`
  for reads and `test_app.py` for update. DeepSeek edited the Sandbox; one real
  Coding container and three separate acceptance/regression/demo containers
  exited 0. Independent outputs were `5`, `"Hello, Ada!"`, `5`.
- ChangeSet `e4b19c40511e074feabba3efe1103be3a12574aa5fb65cd80e636a158a57dc2b`;
  exact diagnostic Apply transaction `504b55bc-53e5-579f-a1a0-1f01bb0410d5`
  settled `COMPLETED`, file `WRITTEN`, all six guarded nodes and Task completed.
  `test_app.py` changed from
  `115e5ff758b3612fb2385446f6b451f48b7f8eadc50fd49420199fcf2b5cc380` to
  `53508121f845294d91165fc82dfaa55dc92937a67d23b72016ed7befc25ca1f5`.
  Unchanged `app.py` SHA-256:
  `49b4d3d54ef214ece6873eb97e3334cc79702d04d0d1d508f7f0555b8d0c3bbd`.
  SQLite was inspected read-only after CLI exit; Sandbox no longer existed.
- A subsequent real CLI Session verified `/status 816f6542` against its own
  completed Task after fixing list-valued node outputs. No approval or execution
  was triggered by the query. The persisted-Coding projection was added afterward
  and covered by deterministic query tests.

Development failures were retained as evidence: the first new test collection
failed because the preparation module did not yet exist. Initial real acceptance
exposed missing dispatch keyword arguments; a real-Runtime dispatch regression
now covers that call. `/status` initially crashed on list-valued READ output;
its regression now covers that representation. An incorrectly named regression
file caused one no-tests-run invocation and was corrected; it is not a PASS.

### Validation and remaining work

Final affected regression set: **265 passed, 1 skipped** (64.12 seconds), including
explicitly enabled Windows native Git CODE tests. JUnit:
`.pytest_cache/g4-final.xml` (local artifact, not committed). The single skip was
Windows symlink creation unavailable. Ruff subsequently passed all four changed
Python files after a test-only line wrap; `git diff --check` passed.
Full CI, Linux and hosted CI: **NOT_RUN**. New normal-mode interactive end-to-end
acceptance was not repeated; existing approval/Apply regressions passed.

G4 is **partial**, not general environment provisioning: automatic remote image
pull/build and arbitrary-language dependency resolution are not implemented.
There is no existing trusted registry/pull profile in Settings; missing images
fail early rather than downloading an unknown image or bypassing Broker/network
policy. A compatible local image must already contain required dependencies.
Larger/ambiguous projects still require a separate bounded planner extension.
Independent acceptance/regression specifications must still be frozen by Host
using `SPROUT_FILESYSTEM_VERIFICATION_SPEC` before Coding; model-written tests
never become an independent oracle. The acceptance above used the preconfigured
Host add/greet specifications. No general automatic trusted-spec generator is
claimed. Foreground `/status` is entered between turns, not concurrently with a
blocking foreground turn. Background Worker recovery remains outside this change.

Rollback: reverse only the G4 working-tree diff (including the two new modules)
after review; do not reset the branch or discard earlier committed G1–G3 results.
No Git commit, push, merge or deployment was performed for G4.

### G4 planner correction — pre-commit review follow-up

The pre-commit review reproduced a real planning defect: `修改 app.py 中的函数，
并运行测试` selected only `test_app.py` for writing. The previous test-word check
conflated source modification, test generation/extension and test execution.

`plan_files` now reuses the existing nonwrite/negation disposition and plans
clause-level effects separately. Explicit modification targets remain write
targets; generating/extending tests adds the corresponding test targets.
Running tests alone does not add any write path. A missing explicit source or
existing-test reference raises `filesystem_planning_file_unavailable` rather than
falling back to unrelated files; explicit creation can still plan a new file.
Unique basename resolution is permitted, ambiguous basenames are rejected.

The plan retains `reads` and `writes` and adds ordinary `actions` and `execution`
data. The CLI displays requested execution separately and saves those fields
inside the existing `filesystem_plan_v1` Task metadata. These are planning hints,
not execution evidence or authorization; no executor, Verification, Apply,
Policy/Scope, Git CODE or persistent database schema was changed. In particular,
`execution: [run_tests]` means tests were requested, not that they passed.

Representative actual plan assertions:

| Request | WRITE | Execution |
|---|---|---|
| 修改 app.py 中的函数，并运行测试 | app.py | run_tests |
| 修改 app.py 并补充对应测试 | app.py, test_app.py | none requested |
| 为 app.py 生成单元测试 | test_app.py | none requested |
| 补充现有 test_app.py 的覆盖率 | test_app.py | none requested |
| 只运行当前项目测试 | empty | run_tests |
| 修改 app.py 和 utils.py 并运行测试 | app.py, utils.py | run_tests |
| 只讨论如何编写测试 | empty | none requested |

TDD evidence: 15 new parametrized checks failed against the old planner after
correcting a pytest reserved parameter name in the test setup. The complete
G4 preparation module now has **31 passing tests**, including real Runtime
analysis dispatch and CLI plan persistence/display checks. The full directly
related set passed **281 tests, 1 skipped** in 63.36 seconds, with native Windows
Git CODE integration enabled; JUnit `.pytest_cache/g4-actions-final.xml`.
The skip is Windows symlink creation unavailable. Four G4 Python files passed
Ruff and `git diff --check` passed. The earlier 265/1 result remains historical,
not a second result added to this run. Real DeepSeek/Docker CLI acceptance and
full CI were **NOT_RUN** during this planner-only correction; the earlier real
G4 evidence remains recorded above, without claiming it exercised this fix.
All five cumulative G4 files remain uncommitted; no commit/push/merge was done.
