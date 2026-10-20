# R02 local security progress — local changes awaiting review

## Ninth authorization: durable workspace consent v1 (local only)

2026-10-20 (Asia/Shanghai). Production scope: `runtime/runtime.py`,
`rootstock/contract.py`, `rootstock/backends/sqlite_store.py`. The optional
`WorkspaceConsentStore` capability is separate from the base SessionStore:
Memory/JSONL ordinary initialization, conversation and ownership are not made
dependent on this capability. Unsupported grant raises an explicit LookupError.
The existing SixLaneFanout public `authority` is used only for this authoritative
operation; no fan-out/derived fail-open semantics apply to claims.

`workspace_consents_v1` is an additive, version-checked SQLite table keyed by
`(session_id, held_turn_id)`. It freezes the owner, original Principal/source,
SHA-256 of the held text and full stored message envelope in snapshot_json.
Pending issuance occurs only immediately after Runtime persists a new server-held
Turn. Missing old issuance is never reconstructed from history. The old caller-
supplied inbound message id and `_consumed_consents` set no longer gate eligibility.

Issuance and claim compare authoritative session/turn snapshots. A single SQLite
conditional UPDATE changes pending to claimed and returns the affected-row count;
the unique key and database writer serialization apply across connections, not
merely across coroutines. The update rechecks the read snapshot inside the SQL
statement. A claim is permanent: no expiry, release, takeover or reset API exists.
Owner/frozen source/known-nonwrite checks precede claiming; a true durable claim
precedes open_workspace, binding, Task creation and submission. An exception or
unknown claim return never enters those side-effect paths.

Crash semantics: append-before-issue leaves unclaimable history; claim-before-open
leaves consumed eligibility with uncertain/incomplete task outcome; task-save or
submit failures never reopen consent. A known failure before any claim write can
leave pending, but retry still needs an explicit grant and atomic current claim.
Claimed means at-most-once durable claim, **not** successful Task execution or
end-to-end exactly-once. Do not automatically retry old instructions when effects
are uncertain. Require a fresh explicit instruction and inspect existing tasks.

The separately authorized existing source-freezing test now uses a temporary
SQLite SessionStore only; its actor, cron source, one-task and no-foreground
assertions are unchanged. Original 116, 289 and 113-case test files remain byte
identical. New `test_r02_durable_consent.py` covers restart normal/stream, same-
Runtime and two independent connection concurrency, claim/open/create/submit
fault windows, Task persistence followed by exception, issuance interruption,
legacy missing eligibility, permanent claims, wrong owner/source/content/envelope,
known-nonwrite, caller-changed snapshots, unsupported Memory refusal and ordinary
conversation, source preservation including MCP, and additive old-schema behavior.

Pre-fix run `r02-round9/red`: 10 failed, exit 1. This includes actual restart,
concurrency and replay failures; two contract tests initially fail because the
claim API does not exist, not because an existing claim misbehaves. First repaired
run: 10 passed; related run: 573 passed. An expanded test setup initially counted
an unrelated pending request from a scripted hello response; it was corrected to
assert zero issuance for the precise legacy Turn, without changing production or
any existing assertion. Final expanded targeted run: 28 passed, exit 0.

Only isolated temporary databases have initialized the additive Schema. No real
database has been opened for migration. Rollout must separately back up and
authorize initialization of the new table; old records remain readable and are
not backfilled. Non-capable backends reject grant. Offloaded/blob-backed held
instructions with no provable original inline snapshot are also rejected rather
than guessed. Rollback must retain consumption evidence and fail-closed behavior,
not restore in-memory-only consent. No table/history deletion is a rollback step.

Final frozen snapshot results and complete diffs will be recorded outside this
tested document under `D:/dev/projects/sprout/.workbuddy/test-runs/r02-round9/final/`.
Earlier Windows native access violation and long-path risks remain unresolved;
symlink skip and external integration not_run remain separate claims. No stage,
commit, push, real migration or R03 is authorized.

## Seventh authorization: post-agent workspace and held-instruction admission

2026-10-19. Branch/HEAD/cached origin/dev remain
`codex/r02-trusted-intent-routing` / `3a532341b7773ccbbcabd8a84341a8a8d21c225c`.
This round changes only production `intents.py` and `runtime/runtime.py`, adds
`test_r02_post_agent_workspace.py`, and supplements this document. Prior source
and tests are preserved. No AgentLoop, Tool Registry, Broker, Approval, Apply,
Temporal, schema, dependency, credential, deployment or original-worktree edit.
No stage, commit, push, PR, merge, migration or R03 work.

### Minimal source change and confirmation contract

`write_intent_disposition` is a shared pure text judgment returning
`explicit_write`, `known_nonwrite` or `unknown`. It is not an authorization
record or an additional high-level Intent. The eight Intent values/events are
unchanged. Public `detect_task_hint(text)` remains bool and is true only for
`explicit_write`; all 289 sixth-round cases remain unchanged and pass.

Known negatives/status/history/quotes/nominal forms are distinct from unfamiliar
wording. A pure-classification regression exposed `implementation of ...` as
unknown; noun-plus-preposition recognition now classifies it known_nonwrite
without changing its false bool result. No model/metadata flag supplies this
judgment. Empty text is known_nonwrite, not a way to mint a held task.

`_workspace_request_from_agent` refuses known_nonwrite before requesting or
holding consent, through the shared ordinary/streaming path. The real model
tool call is retained and observed; AgentLoop/RequestWorkspaceTool are unchanged.
`grant_workspace_consent` checks ownership and frozen Principal, reads the
saved original held instruction, and refuses known_nonwrite BEFORE workspace
opening, consumption, binding or Task creation. Refusal changes no history,
consumed set or existing binding. Caller identity/source labels and intent
metadata cannot replace the original held content or frozen Principal.

Unknown input can still use the controlled Agent-assisted path. Its prompt now
explicitly asks whether the user wants to execute the displayed saved original
as a coding task in the proposed workspace, states the effect of agreement,
and leaves all workspace/Task actions pending. A separate trusted human
`grant_workspace_consent` call confirms it; a model signal alone does not.
The existing CLI calls this method only after `Yes, for this task` and the API
already documents consent for the saved single instruction. Thus this reuses
the existing affirmative action, adds no authorization parameter or persistent
field, does not change historical approvals, and does not migrate held turns.
Legacy known non-writing holds are refused; legitimate unknown holds remain
subject to the same separate explicit human consent, never auto-promoted.

### Formal failure regressions and observed results

The two independent probes from sixth-round review are now formal cases in
`test_r02_post_agent_workspace.py`, using real AgentLoop/executor/real
RequestWorkspaceTool, a scripted local model, temporary SQLite, and mocked
Task/execute/submit/workspace effects for negative cases. Their original
zero-effect and zero-consent assertions are retained; model.asked must be true
to prove the call was not hidden. No paid model or external transport is used.

Guarded commands: `r02-round4/run_checks.ps1 -Fresh -Mode post -Run <name>`.
Per-run raw logs/JUnit are retained below `r02-round4/`.

| Run | Actual result |
| --- | --- |
| `r7red`, before production edits | 46 failed, 0 passed/errors/skipped: 2 original probes, 28 model/intent variants, 10 historical-hold checks, 6 unknown confirmation checks |
| `r7green` | Same 46 cases passed |
| `r7post`, expanded pure tri-state coverage | 1 failed / 106 passed, noun-preposition unknown distinction exposed |
| `r7all`, corrected distinction and additional nominal/empty checks | 113 passed, 0 failed/errors/skipped |
| `r7status` | Original unchanged sixth-round matrix: 289 passed |
| `r7tgt` | Original audit, Runtime/session/workspace/Web/iLink regressions: 269 passed, 0 failed/errors/skipped |

The final new file contains 113 tests: 2 original probes, 32 real-model-tool
variants, 10 historical nonwrite holds with/without a binding, 6 unknown
single-task confirmation/resume tests, and 63 pure disposition/compatibility
checks. Unknown tests cover the original typo, unfamiliar Chinese wording and
an English request, in normal/stream modes. Before consent they require zero
workspace/Task/submit/execute; after explicit consent they require exactly one
Task/submission with the saved original text and frozen user/source; a second
grant must fail without another Task. Historical nonwrite refusal also checks
that a pre-existing binding, history and consumed set are unchanged.

Original audit hashes remain
`1B000FA341F6CB99836743D9517FD669147F13D4C88F55946BFB31D5C72C0DB2` and
`E3A6229FA3B737FF491D30163459FF7F05E1995DBE8F12B9F01EDD3C28176B58`.
The complete sixth-round test file is likewise unchanged:
`F345CBF389F98A8678838C9361FF104024684228E79CCC65056CDAD012894765`.

### Final validation and retained boundaries

Final source/tests/audit will be frozen with full SHA256 manifests before
collection and full L1. Final result and after-hash comparison are written
outside that snapshot under `D:/dev/projects/sprout/.workbuddy/test-runs/r02-round7-final/`.
Historical sections below remain evidence, not a claim that old failures still
exist or that previous green runs prove this final revision passed.

Keep native Windows `0xC0000005`, separate Git long-path failure, one symlink
skip and 58 external gates explicit. No live Docker/Temporal/database/model or
deployment claim. Text classification is heuristic, not a malicious-code
sandbox or exhaustive semantic proof. Existing consent is an affirmative
trusted-adapter action, not a remote caller self-declaration; embedders must
not auto-call it merely because a model requested a workspace.

Rollback must retain the new post-agent nonwrite gate, historical refusal and
all prior metadata/session/source safeguards. Prefer a reviewed forward patch
or disable affected implicit admission, never restore a known bypass, alter
historical records, delete data or reset a worktree. Public bool and signatures
remain compatible; historical nonwrite consent is intentionally refused via
the existing LookupError path. Final DoD remains subject to same-snapshot L1
and user review; this is not commit authorization.

## Sixth authorization: F02 status-narrative correction (local only)

2026-10-19. Branch `codex/r02-trusted-intent-routing`; HEAD and cached
origin/dev remain `3a532341b7773ccbbcabd8a84341a8a8d21c225c`. No staging,
commit, push, PR, merge, deployment, migration or R03 work is authorized.
All earlier sections are preserved historical evidence, not current blockers
unless explicitly carried forward here or in the final validation report.

This round modifies only `src/Sprout/intents.py`, adds
`src/Sprout/tests/test_r02_status_narrative.py`, and appends this audit section.
No Runtime, Gateway, Principal, session, Approval, Apply, Temporal, schema,
acceptance generator, lockfile or CI production changes were made this round.
The original two red regression files retain their recorded SHA256 values.

### Root cause and limited correction

`detect_task_hint` previously treated a leading coding keyword as an imperative.
Thus `修复登录问题的任务目前仍未完成` reached real Runtime task creation with a
bound workspace; with no workspace it incorrectly requested workspace consent.
The new tests exercise both normal/stream routes with in-memory stores and
mock submit/execute, no registered tools or models, and no real file Apply.

The recognizer now checks action-subject / status-predicate relationships,
nominal subject fragments, progress/history clauses, and ambiguous modal
clauses before accepting the existing imperative patterns. English copular /
finite predicates are distinguished from relative clauses such as `that was`.
English verb boundaries stop `fixing`/`implementation` prefix matches, while
explicit a/an determiner handling preserves `write/add/create an endpoint`.
This is bounded heuristic admission, not a complete natural-language parser.

There is no new global ban on `任务`, `目前` or `未完成`. Explicit commands
`请修复尚未完成的登录问题`, `修复尚未完成的任务`, and `please fix the bug that is
still pending` remain accepted. `/task` keeps its existing explicit-command
semantics, including when the text following it resembles a task description.
The eight intent values/events and the original negation/metadata/identity
boundaries remain unchanged. No model classification grants write permission.

### Red-to-green evidence (all local, locked toolchain)

Evidence root: `D:/dev/projects/sprout/.workbuddy/test-runs/r02-round4/`.
Existing guarded launcher: `run_checks.ps1 -Fresh -Mode status -Run <name>`.

| Run | Source/test stage | Actual result |
| --- | --- | --- |
| `s6red` | Before production correction, initial 194 tests | 124 failed, 70 passed, 0 errors/skipped; real routing proves erroneous Task or consent paths |
| `s6green` | Initial subject/predicate correction | 194 passed |
| `s6edge` | Expanded to 234; finite-predicate/colon/state-clause edge cases added | 20 failed, 214 passed; failures preserved, not skipped |
| `s6final` | Corresponding structured predicate checks | 234 passed |
| `s6article` | Added three a/an positive compatibility cases; 249 total | 15 failed, 234 passed; overly strict new verb boundary exposed and corrected |
| `s6all` | Final recognizer with a/an support | 249 passed, 0 failed/errors/skipped |
| `s6target` | Original audit, Runtime/session/workspace/Web/iLink affected regressions (before final a/an-only adjustment) | 269 passed, 0 failed/errors/skipped |
| `s6compat` | Existing Gateway/SDK/MCP/CLI/intents/authz tests (same intermediate stage) | 199 passed, 1 existing symlink skip |

Intermediate test file: 28 Chinese/English negative status/history/nominal/ambiguity
texts and 21 positive commands. 49 direct hint checks, 196 normal/stream x
bound/unbound Runtime checks, and 4 forced-model-task-label checks = 249.
Negative assertions require zero Task creation, zero submit, zero execute,
zero workspace-consent requests and zero workspace opening. Positive bound
requests use existing Task creation/submit; unbound ones request consent only.
No assertion or fixture in either original red audit was changed.

Initial Ruff found two E501 lines in the new test signatures; these were
normally wrapped without changing assertions or disabling rules. Subsequent
whole-tree Ruff 0.16.8 and diff checks pass. Exact final same-snapshot collection,
full L1 and hashes will be recorded separately under
`D:/dev/projects/sprout/.workbuddy/test-runs/r02-status-final-v2/` after freezing
this document, so the final report can be written without mutating the tested
source/tests/audit snapshot. Overlapping group counts must not be added to L1.

### Final aspect/relative-object refinement

The intermediate 249-case version's full offline L1 (`s6l`) completed:
2851 passed, 0 failed/errors, 1 skipped, 58 external not_run, exit 0,
401.59 seconds. Its 33-file before/after and six-control hashes match under
`r02-status-final/`; retain it as intermediate evidence, not final acceptance.

An additional pure-recognizer review found incomplete aspect handling:
`修复登录问题仍在进行中`, `修改配置目前正在排队`, `重构缓存还没有开始` were accepted;
`修复仍在报错的任务` and `修复目前仍未完成的任务` were rejected. The source/tests
were left unchanged until that L1 finished and its snapshot was checked.

Formal paired regressions then extended to 32 negative texts and 25 positive
commands: 57 direct hint checks, 228 Runtime normal/stream x bound/unbound
checks, and four forced-model-task-label checks = **289**. Run `s6aspect`:
25 failed, 264 passed, 0 errors/skipped. The recognizer's temporal clause now
matches aspect/auxiliary plus predicate completion (including ongoing state),
while a trailing relative-object `的...` is not treated as sentence-level
predication. The relative-object prefix likewise admits ongoing-task objects.
This replaces narrower completion-word matching; no new global topic blacklist.
No Runtime or other production module changes were needed.

`s6vgreen`: **289 passed, 0 failed/errors/skipped**, 3.94 seconds. All earlier
negative/positive assertions are retained. Whole-tree Ruff 0.16.8 and diff check
pass. The final snapshot is recorded separately under `r02-status-final-v2/`,
with final full L1 to run as `run_checks.ps1 -Fresh -Mode l1 -Run s6v` and
collection as `-Mode collect -Run s6vc`. This audit is frozen before those
checks; final results will be written to the separate review report, not
silently inserted into the tested snapshot. Native crash/long-path limitations
and the one symlink skip / 58 external not_run remain explicit.

### Compatibility and retained limitations

Only task-hint admission changes this round; schemas, API signatures, intent
events, source freezing, session ownership and existing approvals are intact.
Bare nominal/task-title forms can be conservatively refused; a fresh explicit
instruction or `/task` remains available. This does not prove correctness for
all natural-language expressions or for untrusted in-process Python code.

The prior Windows `0xC0000005` native access violation and separate long-path
Git failure remain risks with unestablished root causes. A passing rerun does
not claim those causes fixed. Preserve the symlink skip and 58 external
integration not_run entries. No live Docker/Temporal/database/model/deployment
claim. Rollback must retain the no-status-as-write boundary and other R02
safeguards: use a reviewed forward patch or disable affected write admission,
never restore metadata authority or unchecked session reuse, reset worktrees,
delete state or upgrade historical evidence. Final DoD recommendation remains
subject to same-snapshot validation and user review, not a commit authorization.

## Fifth authorization: generated source formatting and native-crash diagnosis

2026-10-19. Branch `codex/r02-trusted-intent-routing`, HEAD and cached origin/dev
`3a532341b7773ccbbcabd8a84341a8a8d21c225c`; index remains empty. Earlier
worktree changes and both original red regression files are preserved.

Only additional production file: `src/Sprout/execution/acceptance.py`.
Empty literal argument expansions are omitted; nonempty keyword dictionaries
are assigned to a local variable before expansion. Existing E501 annotations
are emitted only for genuinely over-100-column literal lines. No lint rule,
lockfile, specification, target, assertion, command, observer, executor protocol,
approval, blocker, Apply or Oracle changes. Target import and oracle assertion
statement prefixes consumed by the trusted observer are unchanged.

New regression file: `src/Sprout/tests/test_r02_acceptance_format.py`.
It checks locked Ruff 0.16.8, actual generated test/demo source, long literal
inputs, empty/nonempty kwargs, synchronous/asynchronous calls, real assertion
failures, new content hashes and refusal of old-file/new-hash and
new-file/old-hash combinations before collection/execution. No frozen evidence
is rewritten or upgraded. The existing local protocol hash implementation is
unchanged; newly rendered source receives its own existing content hash.

### Reproducible commands and evidence

Launcher (test-only, ignored):
`D:/dev/projects/sprout/.workbuddy/test-runs/r02-round4/run_checks.ps1`.
All runs use `-Fresh`, CPython 3.13.9 in `r02-round4/locked-venv`, Ruff 0.16.8,
pytest 9.1.1, pinned R02 source, redirected home/temp and offline test guard.
Per-run evidence below is relative to `D:/dev/projects/sprout/.workbuddy/test-runs/r02-round4/`.
Generated sources and raw Ruff JSON are retained under the corresponding
`r02-<Run>` test scratch beneath the authorized visualizations root; local only,
not uploaded. No original user repository Apply or user database is used.

| Command arguments | Actual result | Evidence |
| --- | --- | --- |
| `-Mode format -Run fifth-format-red` before production edit | 2 failed, 1 passed; PIE804 and RUF100 in both generated sources | `fifth-format-red/output.log`, JUnit; scratch `tests.py`, `demo.py`, `ruff.json` |
| `-Mode format -Run fifth-format-final` | 11 passed, 0 failed/errors/skipped | `fifth-format-final/report.xml` |
| `-Mode four -Run fifth-four` | Original four E2E: 4 passed, unchanged assertions | `fifth-four/report.xml` |
| `-Mode evaluation -Run fifth-evaluation` | 7 passed; fixed suite 10/10 scenarios, 4 completed coding tasks, no model-quality claim | `fifth-evaluation/report.xml`; controlled evaluator report |
| `-Mode binding -Run fifth-binding` | 126 passed, 0 failed/errors/skipped; includes R00 67 and examples 18 | `fifth-binding/report.xml` |
| `-Mode collect -Run fifth-collect` | 2661 collected, 0 collection errors | `fifth-collect/output.log` |
| `-Mode l1 -Run fifth-final-l1` | 2600 passed, 0 failed, 0 errors, 1 skipped, 58 external not_run; exit 0, 400.27 seconds | `fifth-final-l1/report.xml`, `gates.json`, `output.log` |
| Locked Python `-m ruff check --no-cache .`; `git diff --check` | both passed | local command output |

The L1 invocation collected 2659 tests: its collection preceded the addition of
the two frozen-hash refusal tests. Those two passed in the final 11-test
generator run; latest complete collection is 2661. Do not present 2602 passed
as the result of a single full-suite invocation. The one L1 skip is
`test_read_broker_rejects_symlink_escape` (symlinks unavailable). Its 58 external
tests are explicitly excluded by the L1 gate and recorded not_run, not passed.
The two warnings are existing AnyIO BlockingPortal and protobuf/lark
utcfromtimestamp deprecations. Original four E2Es also passed in this L1 run.

The first whole-tree Ruff invocation failed initializing an inaccessible cache;
the no-cache rerun passed without changing lint rules. It is not counted as a
code lint failure or silently omitted.

### Native access violation: observations, not a claimed production root cause

Previous final L1 `locked-final-l1`: CPython 3.13.9, process exit
`-1073741819` (`0xC0000005`), no final JUnit. Fatal stack was in distribution
metadata enumeration inside `_local_identity`, during fixed-suite security
scenario approval recovery. No reliable final passed/failed counts exist.
Completed intermediate `final-l1` used the older dedicated test venv with the
same Python version and locked Ruff: 2586 passed, 4 failed, 1 skipped,
58 external not_run. Those four failures were the now-repaired formatting
failures. Neither run proves the current full suite passed.

Fresh grouped `fifth-evaluation` completed exit 0. A 60-second faulthandler
snapshot occurred while hashing evaluation evidence; it was a timeout stack
sample, not a fatal access violation. A second grouped run
`fifth-evaluation-repeat` completed exit 1, 6 passed/1 failed: fixed suite 9/10,
R02 reported `deletion approval was never reached`. It also logged Git
`Filename too long` during sandbox cleanup at its longer scratch path. The
cleanup signal alone does not establish the earlier assertion's root cause.
This run did not reproduce the native crash. The shorter-path grouped control
`-Mode evaluation -Run e5r` completed exit 0, 7 passed in 75.01 seconds, with
no native crash. A 60-second stack sample is not a fatal exception. The current
full L1 completed exit 0 without a native exception (counts above). Thus the
previous native access violation was not reproduced in these completed runs;
its underlying cause remains unestablished. The grouped long-path failure
remains an observed separate issue, not a proven native-crash explanation.
No production crash fix,
Git setting change or destructive cleanup was attempted.

External Docker, Temporal, external databases, paid models and deployments
remain not_run. Local is not a hostile-code sandbox. No staging, commit, push,
PR, migration, deployment or R03 work is authorized or performed.

Compatibility: output/call semantics and existing commands are preserved;
generated bytes change, so old frozen hashes remain inapplicable, requiring
fresh applicable evidence rather than adoption. Rollback is a reviewed patch
of these generator/test changes only, never a reset of existing R02 work or
an upgrade of historical records. This formatting patch alone does not confer
R02 final acceptance or an assertion that the native-crash cause is resolved.
The long-path failure and native-crash uncertainty are submitted for review,
not repaired outside scope. Final tracked diff is 23 files (+467/-165), plus
the existing untracked audit and eight untracked tests. Original R02 red-test
SHA256 values remain unchanged: trusted-intent `1B000FA341F6CB99836743D9517FD669147F13D4C88F55946BFB31D5C72C0DB2`,
session probe `E3A6229FA3B737FF491D30163459FF7F05E1995DBE8F12B9F01EDD3C28176B58`.
The protected original worktree still points to `b7f759c`; no README changes
were made in this round.

## Fourth authorization: implemented locally; acceptance-template blocker remains

2026-10-19. HEAD and cached origin/dev remain `3a532341b7773ccbbcabd8a84341a8a8d21c225c`.
No staging, commit, push, PR, merge, deployment, migration, Temporal change or
MCP configuration work was performed in this round. The historical sections
below are retained as history, not current implementation descriptions.

### Implemented contracts and changed files

Production scope is exactly these 12 authorized files:

| File | Implementation |
| --- | --- |
| `src/Sprout/intents.py` | Eight canonical intents, including strategy; conservative write-request recognition; restore explicit Chinese positive wording |
| `src/Sprout/runtime/runtime.py` | Shared normal/stream input filtering and write admission; server Principal source rather than caller channel; frozen-source recovery; trusted foreground argument |
| `src/Sprout/session/manager.py` | Owner check before reuse; server-written `entry_principal_v1` recovery evidence; reject unknown historical recovery identity |
| `src/Sprout/gateway/runtime_gateway.py` | Separate Principal/foreground arguments, never deserialized from Message.metadata |
| `src/Sprout/gateway/transport_gateways.py` | Trusted OS CLI Principal propagation; explicit foreground choice, default false |
| `src/Sprout/cli/commands/chat.py` | CLI entry chooses foreground explicitly and resolves OS identity for streaming/consent/approval recovery |
| `src/Sprout/mcp/server/tools.py` | Fixed server-side MCP chat identity and owner-checked history, no arbitrary session-create label |
| `web/webapi/auth.py` | Single fixed operator; legacy identity header explicitly rejected; unauthenticated body fallback ignored |
| `web/webapi/routes/chat.py` | Principal propagation, HTTP ownership rejection, pre-SSE ownership check, guarded history/resume |
| `web/webapi/websocket/chat.py` | Server Principal on streaming and non-streaming paths |
| `src/Sprout/cli/commands/gateway.py` | Reject missing/unsafe account ID before client/state construction; preserve exact known filename; reject links/junctions/case aliases |
| `src/Sprout/gateway/channels/weixin_ilink.py` | Account/sender identity; instance-local poll receipts; direct DTO refusal; account state-object/path binding |

No edits to acceptance.py, nodes.py, ProcessBroker, ApprovalManager, TaskAdapter,
SDKGateway, weixin_ilink_state.py, database schema, uv.lock, or CI workflows.

Existing test updates: `test_intents`, `test_message_persistence`, `test_runtime`,
`test_security_gaps`, `test_weixin_ilink_gateway`, `test_workspace_consent`,
`web/tests/test_approvals_api`, `test_task_query_routes`, `test_web_auth`, `test_webapi`.
Valid embedding test callers now explicitly pass Principal; their behavior
assertions remain. Exact-seven enum assertions now retain seven plus strategy.
Header-selection, reviewer-label and missing-session expectations explicitly
follow the approved fixed-operator/404 contract. No completed/verified/Oracle
assertion was weakened. The two original R02 audit/probe files are unchanged.

New R02 tests remain untracked for review, including account scope, poll
security, ownership, and routing/recovery. The older iLink continuation probe
now drives a mocked successful instance-client poll rather than directly
claiming trusted provenance; its session/task/reply assertions remain intact.
The missing-account probe now requires both accounts to be rejected before any
state constructor (stronger than allowing two stores and checking contents).

Exact untracked test inventory: `test_r02_trusted_intent_audit.py`,
`test_r02_session_boundary_probe.py` (both original files unchanged),
`test_r02_session_ownership.py`, `test_r02_ilink_identity_boundary.py`,
`test_r02_ilink_account_scope.py`, `test_r02_ilink_poll_security.py`, and
`test_r02_routing_recovery.py`, all under `src/Sprout/tests/`.

Tracked diff: 22 files, 448 insertions, 161 deletions. Together with seven
untracked R02 test files and this audit, 30 paths are visible for review.
The index is empty. The test launcher/evidence under `.workbuddy/test-runs/`
is ignored local test infrastructure, not a production/CI change.

### iLink invariants

* A real nonempty account ID is validated verbatim before CLI client or state
  construction. No token hash, sender, random ID or shared default substitutes
  for it. ASCII filename-safe IDs are accepted; unknown ID forms fail closed,
  never normalized or migrated. No real account outside those forms was tested.
* Known accounts retain `state/<account_id>.json` and the existing JSON format.
  Case-alias files, symlinks/junctions and ambiguous store ownership are refused.
  A weak, process-local store-owner registry prevents sharing one state object
  between accounts; file-backed stores must have the matching account filename.
  Existing state bytes are not rewritten for identity adoption.
* Only a successful `ret=0` response from this instance's configured client can
  create a receipt. Existing admission rules run before receipt minting.
  Each receipt binds the actual parsed object and a snapshot of its fields;
  it is removed in finally after dispatch. Direct public DTO calls cannot
  create, reuse or transfer a receipt. Python embedding code remains trusted;
  this is not a sandbox against code that can mutate the Gateway itself.
* Principal IDs are versioned SHA256 of the unambiguous JSON pair
  `[account_id, sender_id]`, not ambiguous concatenation. This hashes identity
  components, NOT credentials. Different account/sender pairs differ; the same
  pair is stable. `authenticated=False` is retained: a transport observation
  does not authenticate an independent human account.
* Sender, account and service identities remain distinct. Task read scope,
  dm_policy, allowed_users, protocol, credential store and dedup semantics are
  unchanged. Explicit task dispatch also requires a poll receipt.
* SessionManager rejects another owner before context/history append. Old
  sender-only sessions are not promoted to new composite identities. A wrongly
  stored foreign session locator still cannot bypass ownership.

### Session, routing and recovery invariants

The original audit's 58 red cases and 58 formerly green cases all pass with
unchanged assertions. Natural language is admission evidence, not approval.
Classification cannot choose a different canonical event or grant write access.
Normal and stream paths both discard intent/authority/execution/resume metadata.
The channel used for execution is server-resolved; identity-free embedded
messages become anonymous/unattended, not caller-declared interactive requests.

New sessions with a resolved Principal persist versioned recovery identity in
the existing metadata column. There is no schema change or backfill. Ordinary
reuse checks owner; automatic/explicit recovery additionally requires that
frozen evidence. Missing or malformed history fails closed. Approving a record
does not itself establish ownership: automatic recovery matches requested_by
against the already frozen session identity. Consent and recovery use the
original source, not a newer caller channel. The cron-to-CLI consent regression
confirms that foreground=True cannot upgrade an unattended original source.

Direct Runtime/Principal construction is a trusted embedding interface. Remote
adapters must resolve Principal themselves, never decode it from JSON. The
privileged identity-free in-process history inspection API remains explicitly
separate from owner-checked remote history. Local execution is not a hostile
code sandbox. A shared Web token still represents one operator, not Alice/Bob.

### Checks and exact outcomes

Evidence root: `D:/dev/projects/sprout/.workbuddy/test-runs/r02-round4/`.
`run_checks.ps1` sanitizes environment, redirects Path.home through existing
ci_support/sitecustomize, denies non-loopback sockets, pins this R02 source and
the chosen Python, and writes logs/JUnit locally. No artifacts were uploaded.
Test repositories use the permitted visualizations scratch directory so they
do not inherit the real project Ruff configuration/cache. Production data and
existing .venv were not used as writable test stores.

Commands:

```text
UV_PROJECT_ENVIRONMENT=<evidence>/locked-venv uv lock --check --offline --python <R01 test Python>
UV_PROJECT_ENVIRONMENT=<evidence>/locked-venv uv sync --locked --offline --group dev --python <R01 test Python>
run_checks.ps1 -Mode targeted -Run locked-targeted -Fresh
run_checks.ps1 -Mode affected -Run locked-affected -Fresh
run_checks.ps1 -Mode smoke -Run locked-smoke -Fresh
run_checks.ps1 -Mode collect -Run locked-final-collect -Fresh
run_checks.ps1 -Mode l1 -Run locked-final-l1 -Fresh
<locked Python> -m ruff check . --no-cache
git diff --check
```

The launcher expands L1 to `python -B -m pytest -p
scripts.ci_support.pytest_gates -p no:cacheprovider -q src/Sprout/tests web/tests
--baseline-l1 --tb=short -o faulthandler_timeout=60 --basetemp=<scratch>
--junitxml=<evidence>/report.xml --gate-report=<evidence>/gates.json`.

| Check | Actual result |
| --- | --- |
| Offline lock check and dedicated fresh locked sync | passed; 124 resolved, 68 installed; no existing venv modified |
| Fresh imports of Sprout/CLI/Web/pytest/ruff and source assertion | passed; imports point inside this R02 worktree |
| Versions | Python 3.13.9, uv 0.11.25, Ruff 0.16.8, pytest 9.1.1 |
| Missing-account and same-peer original iLink probes | both pass after contract-correct harness updates |
| Shared-state-account negative regression | first 1 failed, then passed after in-scope Gateway binding guard |
| Fresh targeted suites | 269 passed, 0 failed/errors/skipped; includes original R02 116, iLink 30, session/WebAuth 26 and recovery 6 |
| Fresh related Gateway/SDK/MCP/CLI/intent/authz suites | 199 passed, 0 failed/errors, 1 skipped |
| Original 39 related cases | All 39 matched by classname/name in the fresh targeted/affected JUnit reports, all passed |
| Fresh R00 and examples | 85 passed (67 + 18), 0 failed/errors/skipped |
| Fresh complete collection | 2650 collected, 0 collection errors |
| Whole-tree Ruff 0.16.8 | passed; original two E501 fixed normally |
| git diff --check | passed |
| Completed intermediate full L1 | 2586 passed, 4 failed, 0 errors, 1 skipped, 58 external not_run; 2649 collected before final shared-state regression |
| Final fresh-environment full L1 | aborted with native exit -1073741819 / 0xC0000005; no completed JUnit or trustworthy passed/failed/error counts |

Do not add overlapping targeted-suite counts together. Skip is
`test_read_broker_rejects_symlink_escape`: symlinks unavailable. The completed
L1 additionally recorded the existing AnyIO and lark/protobuf deprecations.
Locked sync warned about hardlink fallback to copy, not a dependency failure.

The fresh full run logged a 60-second faulthandler dump followed by Windows
native access violation, while fixed evaluation was executing and
acceptance._local_identity was enumerating distribution versions through
importlib.metadata. This is observed location, NOT an established root cause.
It is not a passed evaluation, not a normal pytest failure summary, and cannot
inherit counts from the previous environment. No runtime workaround was added.

An auxiliary retry also produced four setup errors using the default Windows
pytest temp directory; an unsanitized compatibility attempt produced 28
failures/50 passes, including readonly-database errors. Those are invalid test
environment attempts, not acceptance evidence; they are not hidden or counted
as fixes. Corrected guarded runs use explicit fresh scratch/home locations.

### Thirty original L1 failures: per-case disposition

Codes below describe actual earlier exceptions and the concrete repair. All
26 non-E2E cases pass in the fresh targeted report. E2E rows remain blocked.

| Original case | Earlier exception / evidence | Current disposition |
| --- | --- | --- |
| test_intent_table_is_single_source | Extra strategy event in exact-seven set | Retain original seven and explicitly add strategy; pass |
| test_handle_persists_envelope_on_authority_turns | anonymous != u-1 | Trusted test caller supplies Principal; original envelope assertions pass |
| test_resume_pending_requires_previous_approval | PermissionError | Valid newly owned session with frozen evidence and Principal; original no-pending LookupError still asserted; pass |
| test_resume_pending_task_continues_without_new_user_turn | PermissionError | Trusted original Principal/recovery evidence; original turns/tool assertions pass |
| test_decide_approval_resumes_chat_session | Tool seen=[] | Frozen-session automatic recovery identity; original resumed-effect assertions pass |
| test_delete_approval_survives_restart_and_controls_commit[delete-approve] | final failed, expected completed | Ruff blocker below; NOT fixed |
| test_delete_approval_survives_restart_and_controls_commit[rename-approve] | final failed, expected completed | Ruff blocker below; NOT fixed |
| test_requirement_output_controls_whether_the_change_lands[True] | final failed, expected completed | Ruff blocker below; NOT fixed |
| test_a_proposal_offers_only_the_agents_edits | final failed, expected completed | Ruff blocker below; NOT fixed |
| test_gateway_handles_dm_and_sends_reply | Fake handle rejects principal kwarg | Adapt mock signature and drive successful client poll; assertions retained; pass |
| test_gateway_deduplicates_redelivered_message | Same TypeError | Same poll harness, original duplicate assertion retained; pass |
| test_gateway_reuses_conversation_session | Same TypeError | Same harness; original second-session assertion retained; pass |
| test_a_coding_request_without_a_workspace_asks_before_running | Consent marker None | Chinese imperative recognition restored; pass |
| test_granting_consent_registers_the_workspace_and_queues_the_task | Consent marker None | Recognition + trusted fixture adapter + frozen-source consent; pass |
| test_consent_is_single_use | Consent marker None | Same repair, original second-use refusal asserted; pass |
| test_consent_without_a_held_turn_is_an_error | PermissionError, expected LookupError | Approved missing-session confidentiality contract explicitly asserts PermissionError; still no task; pass |
| test_the_task_uses_the_directory_the_operator_named | Consent marker None | Recognition/trusted consent; original workspace assertion retained; pass |
| test_the_held_instruction_survives_into_the_task | Consent marker None | Same repair; original held-content assertion retained; pass |
| test_consent_from_the_agent_route_still_queues_the_task | PermissionError | Trusted test caller and frozen-source recovery; original task assertions retained; pass |
| test_the_agent_does_not_reask_when_the_session_has_an_active_task | PermissionError | Same recovery repair; original no-reask behavior retained; pass |
| test_a_consented_task_is_filed_under_the_surface_it_came_from | Consent marker None | Original trusted source restored from frozen identity; pass |
| test_a_task_queued_from_chat_can_be_reported_back_to_it | Consent marker None | Recognition/consent/session link retained; pass |
| test_consent_is_not_asked_again_for_the_next_coding_request | Consent marker None | Recognition + owner-checked bound workspace; pass |
| test_a_later_coding_request_actually_runs_in_the_bound_workspace | Consent marker None | Same repair; original two-task assertion retained; pass |
| test_a_status_question_is_not_run_as_a_task | Setup consent marker None | Positive setup restored; original no-extra-task assertion retained; pass |
| test_consent_stays_scoped_to_its_own_session | Consent marker None | Positive setup restored; original separate-session assertion retained; pass |
| test_the_bound_workspace_survives_a_reload | Consent marker None | Frozen identity and workspace persist in existing metadata; reload assertions pass |
| test_pending_approval_can_be_approved_over_http | web-user != reviewer | Fixed operator replaces body-selected decision label; status/conflict assertions retained; pass |
| test_foreign_identity_parameters_and_invalid_observation | 400 != 403 | Legacy header now rejected before HTTP/RPC dispatch; no query effect assertion retained; pass |
| test_history_of_unknown_session_is_empty | 404 != 200 | Missing/foreign history consistently 404, no session existence oracle; pass |

### Established E2E blocker and requested next scope

The four E2E cases were examined separately using controlled test SQLite
records and existing whitelisted diagnostics, not inferred from their final
task statuses. In the initial nested-D-directory run each had Ruff exit 2:
cache temporary-file permission failure. pytest, Acceptance and PoC passed;
Apply was verification-blocked and HEAD did not change.

Moving scratch repositories outside the real project's ancestor configuration
exposed a second issue. With the **locked Ruff 0.16.8**, each of the four cases
has Ruff exit 1 and codes **PIE804, RUF100**, at evaluation (first failing node
index 5/check index 3); pytest/Acceptance/PoC pass, verification=false, ordinary
Apply remains blocked and Git HEAD unchanged. These are not interpreter-missing
or failed-Oracle cases. No Apply/verification assertion was changed.

Correction to the interim commentary: the separate four-test green run used
the ambient system Ruff 0.12.0, NOT the locked version. It is invalid evidence
of the locked baseline being green and must not be used for acceptance.

A read-only stdin experiment renders the SAME frozen spec through the existing
acceptance and demo generators, then feeds each unchanged string to both tools:

| Tool | Generated acceptance | Generated demo |
| --- | --- | --- |
| System Ruff 0.12.0 | exit 0 | exit 0 |
| Locked Ruff 0.16.8 | exit 1: PIE804, RUF100 | exit 1: PIE804, RUF100 |

`execution/acceptance.py:28,31,324,327` emits empty `**{}` and unconditional
`# noqa: E501`. This directly explains the observed locked-tool errors. That
file is outside the current R02 production authorization. It remains unmodified.

Request a separate, narrowly reviewed scope for generated-source formatting in
`src/Sprout/execution/acceptance.py` and direct template regressions: avoid empty
kwargs and unnecessary noqa through structurally formatted code. Preserve target,
Oracle, cases, assertions, commands, approvals, baseline categories and gates.
Review generated-content hashes/frozen historical evidence; do not rewrite old
records. Do not downgrade Ruff, change selectors, hide tests or relax assertions.
Independently diagnose the fresh-environment native crash without speculative
Runtime/Temporal changes. No production implementation beyond current scope.

One auxiliary direct strategy-module import also exposed an import-order cycle;
normal Sprout/CLI/Web imports passed. Its origin is not established and no
unrelated module was changed. It must not be called fixed or silently ignored
in a future standalone-import claim.

### Compatibility, rollback and current decision

Web label switching, arbitrary MCP session-create labels, identity-free reuse,
source-free recovery, missing iLink account IDs and direct unreceipted inbound
calls intentionally stop working. Known account state paths/JSON remain exact;
old states are preserved, not claimed. New recovery evidence is v1 metadata in
the existing column; no legacy backfill. Direct trusted embedders must supply
Principal explicitly. Receipts and composite iLink IDs are not human credentials.

Rollback must be reviewed per task-owned path, retaining fixed Web identity,
owner checks, unknown-source refusal and metadata authority removal. Disable an
affected entry rather than restore a known authorization hole. Do not delete
user state or reset any worktree. No commit exists to revert in this round.

Docker, Temporal, external databases, real models, deployments and migrations:
**not_run**. External L1 inventory: 58 not_run, not counted as passed.
R02 DoD: **not met**. Not ready for local commit review until the locked-tool
blocker and final complete L1 claim are resolved. R03 was not started.

## Third authorization follow-up: account-scope stop (2026-10-19)

Branch, HEAD and cached origin/dev were rechecked: unchanged at `3a532341`.
The index is empty. The initial working diff remains 11 tracked files,
244 insertions and 114 deletions; the pre-existing five untracked files were
preserved. No existing production patch was changed during this follow-up.
The original audit/session-probe hashes still match the previous report.

Before wiring iLink identity, the requested account-scope review found:

* `cli/commands/gateway.py::_run_weixin_ilink()` selects a state file using
  `account.account_id or "account"`. Distinct known account IDs get different
  state paths; accounts without IDs use the same fallback path.
* `WeixinIlinkAccount` and the account loader accept missing/empty account IDs.
* `WeixinIlinkStateStore` keys sessions/context/tasks by peer alone inside that
  file and has no independently recorded account owner to reject a mismatch.

New isolated regression:
`test_r02_ilink_account_scope.py::test_distinct_unidentified_accounts_cannot_reuse_the_same_peer_state`.
It calls the actual CLI `_run_weixin_ilink()` twice with two distinct test-only
account credentials and absent account IDs. It captures the actual StateStore
constructors and stops before gateway startup. All state is under pytest's
temporary directory. No HTTP, Runtime, model, real credential or user data is
used. The second store reads the first account's test session locator.

Command: existing test Python, explicit R02 PYTHONPATH, offline guard and
temporary baseline home, `-B -m pytest -p no:cacheprovider -q
src/Sprout/tests/test_r02_ilink_account_scope.py --tb=short`.
Result: **1 failed, 0 errors, 0 skipped**, 0.42 seconds. Expected empty locator;
observed `test-only-account-a-session`.

This establishes a supported-configuration state-key collision, NOT a claim
that the live iLink service emits missing IDs or that a private transcript was
disclosed. There was no live service test. The older one-failure continuation
probe was not edited or claimed green.

The user explicitly requested reporting cross-account conflicts before state
format changes. Production implementation is therefore paused. No state format,
account data, CLI code, polling logic or credentials were changed.

Proposed boundary for approval before implementation:

1. Reject missing/unprovable account identity rather than use it to authorize
   chat. Do not claim/convert old account-fallback state automatically.
2. Scope conversation Principal identity to both the configured account and
   admitted sender, while keeping account, sender and service roles distinct.
   Sender ID alone must not set authenticated=True.
3. Explicitly bind identity receipt to the trusted client/poll path. Currently
   poll_once forwards a plain inbound DTO into public handle_inbound; the DTO
   alone does not prove provenance. Do not simply turn an arbitrary public DTO
   into an authenticated Principal. If a server-only receipt is chosen, obtain
   permission for that narrow polling-to-dispatch plumbing (not protocol,
   deduplication or polling-policy changes).
4. For known account IDs, preserve existing per-account state-file layout.
   Fail closed on ambiguous legacy ownership; no schema migration or adoption.

Further full L1, collection, production Ruff fixes and compatibility work were
not run in this follow-up because of this stop. The results below remain the
previous run's historical evidence, not new successful verification.

Date: 2026-10-19. Baseline and unchanged HEAD:
`3a532341b7773ccbbcabd8a84341a8a8d21c225c`.
Branch: `codex/r02-trusted-intent-routing`.

This is an incomplete local patch, not an acceptance or deployment report.
No commit, staging, push, PR, merge, migration or R03 work was performed.
No production files outside the authorized list were modified.

## Authentication contract

Web authentication now represents one operator. Valid shared-token requests use
`DEFAULT_WEB_USER`; a present `X-Sprout-User` header is explicitly rejected with
400 after credential validation. Wrong/missing tokens remain 401; an enabled
configuration without a token remains 503. The principal resolver itself ignores
the header. Authentication-disabled requests also use the fixed local identity,
with `authenticated=False`; body fallback labels are ignored.

This intentionally breaks legacy label switching. Existing labelled sessions
are not reassigned or migrated. It does not implement independent authenticated
Alice/Bob accounts, nor make unauthenticated remote service exposure safe.

The original Alice/Bob route probe injects already-resolved principals; it is a
lower-level ownership boundary test, not evidence of independent Web accounts.

## Current local changes

* `web/webapi/auth.py`, `web/tests/test_web_auth.py`: fixed identity, explicit
  legacy-header rejection, tests for both labels and unauthenticated fallback.
  Original label-switching assertions were explicitly revised to the approved
  single-operator contract, not deleted.
* `session/manager.py`: `require_owner()` checks the stored owner against an
  explicit entry-point Principal before returning an existing session. Missing
  principal/source/owner or unequal owner fails with a generic PermissionError.
  Message labels and metadata do not prove ownership. New identity-free embedded
  conversations are unowned and cannot subsequently be reclaimed by label.
* `runtime/runtime.py`: optional server-side Principal arguments for normal,
  streaming, approval-resume and workspace-consent entry points. Resume/consent
  checks precede history/context/workspace access. Ordinary handle sanitizes
  routing/authority metadata. Task actor/source no longer use origin metadata;
  unresolved callers are conservatively unattended. Model task classification
  additionally requires deterministic write wording before task dispatch.
* `gateway/runtime_gateway.py`, `gateway/transport_gateways.py`: explicit
  Principal propagation; CLI resolves the existing OS Principal. Web callers can
  supply their server-resolved Principal separately from Message.
* `cli/commands/chat.py`: OS Principal passed on streaming and recovery paths.
* `mcp/server/tools.py`: fixed MCP server operator on chat/history; reject
  arbitrary session-create user selection. No external MCP calls were made.
* `web/webapi/routes/chat.py`: Principal forwarding, generic HTTP ownership
  denial, ownership precheck before SSE construction, history checks even when
  authentication is disabled.
* `web/webapi/websocket/chat.py`: forwards the resolved Principal on both paths.
* `intents.py`: strategy joins the original seven canonical intents; canonical
  events/prompts derive from the shared table. Conservative negation, quotation,
  historical/read-only/ambiguity filtering. This implementation still rejects
  some legitimate Chinese imperative wording and is not finished.

Additional tests: `test_r02_session_ownership.py` (10 cases), and
`test_r02_ilink_identity_boundary.py` (one retained failing scope probe).
The original 116-case audit and original session probe were not edited.

Original audit SHA256:
`1B000FA341F6CB99836743D9517FD669147F13D4C88F55946BFB31D5C72C0DB2`.

## Scope stop: real iLink conversation identity is not wired

Observed call chain:

1. `gateway/channels/weixin_ilink.py::_handle_conversation()` obtains the peer's
   remembered session and calls RuntimeGateway with Message only.
2. RuntimeGateway forwards no Principal.
3. SessionManager refuses reuse because neither a message user label nor a
   session locator is authorization.

The existing three iLink L1 failures first encounter an outdated fake Runtime
signature (`unexpected keyword argument 'principal'`). That alone does NOT prove
a real Runtime integration defect. A separate new regression uses the actual
iLink conversation method, actual R02 Runtime, in-memory storage and mocked
state/reply transport. Its first turn succeeds and its second turn fails with
`PermissionError: Session unavailable`. No client/network/default on-disk state
is instantiated. This proves missing adapter identity wiring, not external
iLink authentication or a cross-user data disclosure.

It cannot be fixed by treating transport name or Message.user_id as authority.
To preserve legitimate iLink continuation, approval is needed to inspect and
minimally wire server-resolved peer identity in:
`src/Sprout/gateway/channels/weixin_ilink.py` (and corresponding tests).
Do not modify its credential store, network behavior or admission policy under
this request. If its transport evidence cannot establish the peer identity,
remain fail-closed and request a separate authentication-contract decision.

Production changes stopped when the real-runtime probe confirmed this blocker.

## Executed checks

Python: the read-only existing R01 test environment at
`D:/dev/projects/sprout/.workbuddy/test-runs/r01-provenance-clean-final/venv/Scripts/python.exe`.
Source imports explicitly use this R02 worktree's `src` and repository root,
not the R01 source. No fresh locked-install claim is made. Full L1 runs with
credential/environment stripping, isolated temporary home, and the existing
offline sitecustomize/network guard. Its source did not change during the run.

Local evidence directory:
`D:/dev/projects/sprout/.workbuddy/test-runs/r02-local-contract-1/`.
Raw logs and JUnit remain local; nothing was uploaded.

Commands use the Python above, `-B -m pytest -p no:cacheprovider` and `--tb=short`:

| Check | Result |
| --- | --- |
| New WebAuth tests before production fix, `web/tests/test_web_auth.py -k 'shared_token_rejects or unauthenticated_identity_ignores'` | 4 failed, 11 deselected |
| WebAuth suite after fix | 15 passed, one existing AnyIO deprecation warning |
| WebAuth + original session probe + new ownership suite | 26 passed, 0 failed/errors/skipped |
| Preserved original R02 audit file | 116 passed, 0 failed/errors/skipped |
| Previous 39 selected related tests | 38 passed, 1 failed (old exact-seven enum contract) |
| R00 + two example suites | 85 passed (67 + 18), 0 failed/errors/skipped |
| Full offline L1 | 2536 passed, 30 failed, 0 errors, 1 skipped, 58 not_run |
| Added real iLink continuation probe after full L1 | 1 failed, 0 errors/skipped |
| Complete collection after adding iLink probe | 2626 collected, no collection errors |
| Whole-tree Ruff `check . --no-cache` | failed: two E501 findings, Runtime line 738 and new ownership test line 34 |
| `git diff --check` | passed |

Full L1 command:

```text
python -B -m pytest -p scripts.ci_support.pytest_gates -p no:cacheprovider -q src/Sprout/tests web/tests --baseline-l1 --basetemp=<evidence>/pytest-temp --junitxml=<evidence>/l1.xml --gate-report=<evidence>/l1-gates.json --tb=short
```

The full L1 collected 2625 tests before the extra iLink scope probe; selected
2567 and recorded 58 external tests as not_run. Its JUnit reports 30 failures,
zero errors and one skip; duration 373.62 seconds. The later collection of 2626
is not a second full L1 run. Warnings: AnyIO BlockingPortal deprecation and the
existing lark/protobuf utcfromtimestamp deprecation.

Full L1 failures, not hidden:

| Test module | Failed | Observed evidence / limitation |
| --- | ---: | --- |
| test_intents | 1 | Old expected set omits strategy; retain seven and explicitly add eighth in follow-up |
| test_message_persistence | 1 | Old message label u-1 becomes anonymous without server Principal |
| test_runtime | 3 | Identity-free resume refused; approval auto-resume lacks Principal wiring |
| test_task_e2e | 4 | Final failed instead of completed; root cause NOT established; do not attribute to old Windows failure |
| test_weixin_ilink_gateway | 3 | Existing fake Runtime rejects new Principal kwarg; separate real probe establishes missing adapter wiring |
| test_workspace_consent | 15 | Positive Chinese wording and identity-free consent/resume compatibility failures |
| test_approvals_api | 1 | Fixed operator replaces body-selected reviewer |
| test_task_query_routes | 1 | Legacy user-selection header now rejected with 400 before older 403 assertion |
| test_webapi | 1 | Unknown session history now returns 404, not identity-free empty 200 |

Original 58 red cases are now green, with original assertions preserved:
28 wording, 8 metadata task overrides, 12 read-only routing, 3 model-only task
labels, 2 event mismatch, 1 strategy enum, 1 origin-source spoof, 1 streaming
metadata task override, 2 actual adapter read-only cases. The original 58 green
cases remain green. This does not mean full R02 acceptance is complete.

## Remaining work and risks — explicitly NOT accepted

* Obtain narrowly scoped iLink adapter permission before further implementation.
* Make ordinary/streaming input sanitization identical; streaming does not yet
  apply the complete ordinary metadata filter. Retain/add negative tests for
  resume/foreground/consent metadata, not just intent overrides.
* Complete trusted frozen-origin propagation for approval auto-resume and
  workspace consent. Current ownership checks are present, but legitimate
  recovery/source preservation is incomplete. Never recover authority from old
  free-form origin metadata or merely from requested_by.
* CLI foreground behavior needs its explicit trusted argument wired consistently;
  ordinary metadata must not regain foreground execution authority.
* Expand legitimate positive Chinese imperative coverage without relaxing the
  28 negative cases; conservatism currently causes functional regressions.
* Complete WebSocket/SSE and same-operator recovery compatibility tests; current
  results do not establish all requested transport/recovery guarantees.
* Fix the two Ruff formatting findings after work resumes.
* Diagnose the four Task E2E failures from task/node/verification evidence.
* Adapt affected valid test callers to explicit trusted Principal where warranted,
  retaining assertions and documenting approved breaking contracts. No blanket
  fake principal injection or weakened security assertions.
* Re-run all targeted suites, original39, collection, Ruff and full L1 on the
  final frozen patch. No local commit review recommendation yet.

`Runtime.history()` without a Principal remains a privileged in-process
inspection API for compatibility; remote adapters must never expose that form.
Embedding code that can call Runtime directly or construct Principal is trusted
application code, not an authentication sandbox. Local execution is not a
malicious-code sandbox. Docker, Temporal, real models, external databases,
deployment and migration were not run.

## Compatibility and rollback

No database schema or existing records were changed by the patch. No ownership
backfill or automatic session claiming exists. Legacy label-selected sessions
remain stored but are not granted to the fixed Web operator.

Keep the baseline and current uncommitted task-owned diff for review. Roll back
an incomplete local patch only by explicit, reviewed path-level changes, never
by resetting the user's worktrees. A releasable rollback must retain fixed Web
identity, ownership refusal and the ban on metadata-granted authority; restoring
the old source verbatim would reopen known defects and is not a safe rollback.

R02 DoD: **not met**. PR/commit readiness: **no**. R03: **not started**.
