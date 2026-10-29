# R04-B Final Review — generic closure complete; internal Git HOLD

Date: 2026-10-21. Reviewed branch: `codex/r04-broker-zero-bypass`.
Reviewed HEAD: `33a7b06946a0b086a9078bb4893e4fd39f1262ed`.

## 1. Decision, authority and evidence limits

- **A / PASS (limited):** B1/B2/B3-min generic built-in entry closure is sufficiently supported by current source and historical Mock evidence. This accepts denial, NOT usable native S1 execution.
- **B / HOLD:** internal Git remains executable with material installation/config/helper, resource binding and effect/cleanup risks. S1 protections do not apply to those consumers.
- **C / 仅通用入口子范围完成，整体 HOLD。** Cancelling public B4–B7 feature expansion does not cancel the security obligations of existing internal execution.
- No new bypass of the exact built-in retired GitWriteTool or six retired GitBroker methods was found in this static review. This is not a claim that arbitrary Python, plugins, CLI, Skill or internal Git cannot run Git.
- This round ran NO pytest, native Git operation, hook/helper, network, Runtime or database. Only source, existing tests and historical logs were read. The sole new repository file is this report, untracked/unstaged.
- At entry: expected branch/HEAD, empty index, no tracked changes; nine historical investigation files present and hashes matched the previously reviewed values (section 9). No fetch or remote contact; remote dev freshness was not revalidated.

Architecture: `PR_IMPLEMENTATION_ORDER.md:70–79` requires an I/O inventory, mode/authorization/audit/effect contracts and no unexplained fallback; `FINDINGS_REVALIDATED.md:14–19` distinguishes outer ToolExecutor checks from missing broker boundaries. Historical F04's host fallback description is superseded for these built-ins, not for all internal consumers. Existing modular runtime, Proposal verification/approval and isolated transaction mechanisms must be preserved.

Evidence levels below: **S** current source; **M** previously executed Mock tests; **N** native/real integration not_run. Paths are repository relative; line anchors refer to reviewed HEAD, not historical documents.

## 2. Final 31-operation matrix

Common Write path W: `system_tools.py:813–821` validates operation then returns retirement denial, irrespective of broker, metadata or claimed approval. Normal ToolExecutor detects exact built-in earlier (section 3). All input operation names and schema remain; `risk_level="high"` retained. No Write operation resolves a workspace, delegates or asks a human inside invoke.

Common Inspect path I: `system_tools.py:695–722`: invalid operation rejects; missing/non-callable broker rejects; only status/log/branch/diff delegate. No `_run_git` fallback. Default Runtime wiring is `runtime/runtime.py:396,421–422` with the real GitBroker. An intentionally injected arbitrary Python broker implementation is NOT a trustworthy GitBroker proof.

| # | Surface / operation | Final default production route | Result / evidence |
|---|---|---|---|
| 1 | Inspect status | I → GitBroker.status:43 → policy → git_s1.run_read:71 | No installation issuer, denied; S/M, N |
| 2 | Inspect diff | I → GitBroker.diff:51 → policy → S1 | Same denial; no real diff; S/M, N |
| 3 | Inspect log | I → GitBroker.log:59 → _refuse | Final DENY even custom ALLOW; S/M |
| 4 | Inspect branch | I → GitBroker.branch:62 → _refuse | Same; S/M |
| 5 | Inspect show | I unsupported | Zero broker/host call; S/M |
| 6 | Inspect remote | I unsupported | Zero broker/host call; S/M |
| 7 | Inspect tag | I unsupported | Zero broker/host call; S/M |
| 8 | Inspect stash_list | I unsupported | Zero broker/host call; S/M |
| 9 | Inspect rev_parse | I unsupported | Zero broker/host call; S/M |
| 10 | Inspect ls_files | I unsupported | Zero broker/host call; S/M |
| 11 | Write add | W | Retired; zero delegation/host; S/M |
| 12 | Write commit | W | Retired; S/M |
| 13 | Write pull | W | Retired; S/M |
| 14 | Write fetch | W | Retired; S/M |
| 15 | Write push | W | Retired; S/M |
| 16 | Write checkout | W | Retired; S/M |
| 17 | Write switch | W | Retired; S/M |
| 18 | Write branch_create | W | Retired; S/M |
| 19 | Write branch_delete | W | Retired; S/M |
| 20 | Write merge | W | Retired; S/M |
| 21 | Write rebase | W | Retired; S/M |
| 22 | Write reset | W | Retired; S/M |
| 23 | Write restore | W | Retired; S/M |
| 24 | Write revert | W | Retired; S/M |
| 25 | Write stash_push | W | Retired; S/M |
| 26 | Write stash_apply | W | Retired; S/M |
| 27 | Write stash_pop | W | Retired; S/M |
| 28 | Write stash_drop | W | Retired; S/M |
| 29 | Write tag_create | W | Retired; S/M |
| 30 | Write tag_delete | W | Retired; S/M |
| 31 | Write clone | W | Retired; S/M |

Direct GitBroker log/branch/add/commit/push/pull (`git_broker.py:59–110`) all construct their existing ActionRequest through `_decision:120–145`. Original DENY (including Scope expiry) is retained. ALLOW/REQUIRE_APPROVAL becomes capability DENY before final policy event, no approval query/consumption. `_run:148` and module `run_capture:24` are refusal-only tombstones, NOT process wrappers. Argument/signature compatibility is not execution availability.

## 3. B1/B2/B3-min contract acceptance

| Contract | Source and historical test evidence | Verdict / limitation |
|---|---|---|
| No broker / unsupported operation never host-fallback | Inspect I; Write W; B1 `test_missing_broker_refuses_all_operations`, `test_unsupported_operation_never_calls_even_available_broker` and interruption cases in `test_r04_b1_git_no_fallback.py`; original B-design 31 missing-broker probes | PASS S/M; no native execution required for refusal |
| 21 Write operations zero broker, zero host | W; B3 `test_all_public_writes_refuse_even_with_allowing_broker` (21), `test_retired_write_is_rejected_before_meaningless_human_prompt` (21) | PASS S/M |
| Direct APIs custom ALLOW/REQUIRE/old approval cannot execute | `_refuse/_decision`; B3 direct6, old approvals6 (single/reusable), final-policy18 | PASS S/M; ApprovalManager unchanged |
| No bare GitBroker execution leaf | `_run` and legacy module symbol raise immediately; B3 `test_private_legacy_runner_is_a_tombstone` | PASS S/M; different modules' runners remain callable |
| Final denial before approval/invoke | `executor.py:95–165` resolves object, preserves bound node identity/risk/layered decision, retires exact `type(tool) is GitWriteTool`; approval lookup:186, request:196, invoke:231 are later | PASS S/M; final audit deny + TOOL_DENIED reason capability_retired; original policy DENY reason policy preserved |
| No old-record consumption/pending prompt | B3 `test_effective_tool_denial_audit_no_prompt_no_consumption` 84 cases, no lookup/request/invoke; approved record unchanged, pending empty | PASS M; source has no retired branch approval access |
| Identity/Scope remain fail-closed | Executor bound context:102–108 before denial; Broker ActionRequest task/scope:125–135; B3 expired Scope event and request-field tests | PASS for denial; not proof of new end-to-end identity/Scope transport for a hypothetical future executable Git API |
| S1 status/diff no authority means refusal | `git_s1.py:56–68` both resolvers return None; run_read:71–85; B2 test suite | PASS for framework; real installation/view issuance HOLD; private runner Mock is not filesystem isolation |
| Alias/registry/compatibility | `ToolRegistry.get/register:34–41`, base `Registry.get:30`, Executor `_resolve:307–313` return actual objects; strict overlay does not name-select retired code. Exact object remains denied even when reached through embedding; direct invoke W also denies | S; no production alias/wrapper restoring these built-ins found by repository search |

Searches covered GitBroker imports/construction, `_git_broker` consumers, `_run_git(` and SandboxGitTool construction under `src/Sprout` and `web`. `execution/__init__.py` still exports GitBroker, but its methods remain denied. `tools/registry.py:200–201` constructs the same classes with no broker; Runtime replaces them with broker-injected instances; neither restores execution. No automatic redirection from refused generic operations to Sandbox/Apply/CLI was found.

Important excluded authority: registry replacement, a same-name custom plugin, subclass with overridden invoke, a wrapper with its own implementation, monkeypatching or private Python calls are arbitrary host-code capabilities, not the exact built-in contract. B3's plugin/subclass tests preserve their old policy behavior deliberately. `system_tools._run_git:824–837` still implements a private host helper, but repository search finds no production caller (only definition and test spies). It is not GitBroker._run, and must not be reused; its existence precludes a claim of “all Python Git execution removed.” Generic CLI and sandbox tools below are actual independent execution paths, not hidden restoration of W.

## 4. Shared native execution risk model

`execution/git_env.py:19–23` explicitly leaves Hooks enabled. Pins: fsmonitor=false and pager=cat (`:47–52`); noninteractive editors/askpass and a SecretBroker environment whitelist (`:35–41,66–72`) are partial defense. PATH/HOME/USERPROFILE/XDG remain allowed (`security/secret_broker.py:24–50`): removing API key variables does not remove disk credentials, global config, includes, remote helpers, filters or attributes. No trusted installation identity is established by resolving bare `git` through PATH. Environment secrecy is not execution authorization.

`proc.run_capture:52–85` defaults timeout=None, uses unbounded communicate, and returns primitive code/text tuples; exceptions attempt terminate. `kill_tree:18–38` uses taskkill /T on Windows then direct-child kill; POSIX does not establish a process group/tree boundary. Cleanup wait has no global bound here. This is source-observed best effort, not evidence all descendants die or effects did not happen. Missing/unknown returncode is coerced through `process.returncode or 0`; native guarantees are not proven. Timeout/cancel after mutation is not automatically safe to retry. These constraints differ from B2's private bounded runner and its explicit UNCONFIRMED behavior.

## 5. Internal Git consumers and residual risk matrix

All rows remain **outside the completed generic-entry subrange**. No row inherits S1 installation/view authority. Severity below is review priority, not a demonstrated unauthenticated exploit.

| Consumer / actual entry and subcommands | Existing authorization and positive evidence | Native environment / lifecycle / residual risk | Owner / blocks full B? |
|---|---|---|---|
| Proposal → `runtime/changes.py:136–196,213–232` → `ApplyBroker.apply/rollback` (`execution/apply.py:73–241`): apply --check/apply, rev-parse HEAD, add -- paths or fallback add -A, commit, revert recorded commit; legacy apply -R, restore --staged | Proposal service checks isolated replay prohibition, verification blockers + current provenance, explicit force reason/audit, task Scope. Broker evaluate:39–70 uses GIT_COMMIT policy + proposal_id/risk approval; DENY preserved. Historical internal Mock and B3 three controls preserve route/gates | Bare host git `_git:218`; shared env/hooks/config; index/attributes clean filters, commit signing/helper, rollback hooks. Resource is workspace root, not per-file policy; zero arbitrary-path/patch race proof. add -A when files empty and preexisting index contents require exact commit-binding review. No timeout/output bound; failed mutation and `_undo` are not a durable uncertainty protocol. Direct ApplyBroker does not itself duplicate service verification; do not treat its callers as automatically validated | B-internal Apply execution first; concurrent/pinned patch transaction R06/R06b and verification packages as needed; **yes** |
| `NodeExecutor._sandbox/_approval` (`runtime/nodes.py:720,1067`) → `GitWorktreeSandbox.create/diff/changed_files/remove` (`sandbox/git_worktree.py:54–146,190–248`): worktree add -b/remove, branch -D, add -N, diff HEAD, status | Node-owned sandbox/task lifecycle; remove validates parent/prefix and dedicated branch prefix. Diff excludes caches, --no-ext-diff and path separator. Existing sandbox lifecycle tests exist; B3 Mock diff/changed-files retained | Host `_run:241` has no per-action Broker/Scope argument; fixed ref/root lifecycle is not a stable config view. diff omits --no-textconv; add -N mutates index and may engage filters. Shared config/hooks/global credentials; worktree hooks possible. No timeout/output cap in `_run`; plain RuntimeError on nonzero; cleanup/cancel ambiguity | B-internal sandbox/read profile; R05 OS admission and R06 transaction links; **yes** |
| `SandboxGitTool.invoke` (`execution/sandbox_tool.py:647–702`), injected at nodes.py:349,474,653: status/diff/log | SandboxRef-bound root; isolated coding passes allow_host_git=False and rejects before spawn; outer ToolExecutor still exists for model tools. B3 does NOT touch it | Local host git remains, low-risk label not S1; --no-ext-diff without --no-textconv, shared config/attributes/helpers. 30s timeout but unbounded output, generic failure on nonzero, no durable UNKNOWN. Constructor default host=True is trusted Python API, not isolation proof | B-internal sandbox inspect, R05 for isolated admission; **yes** |
| `IsolatedGitWorktreeSandbox._run` (`sandbox/isolated_git_worktree.py:17–73`): allowlist worktree/branch/status/add/diff; first config --get-regexp ^filter | Checks absolute root, .git marker/backlink containment, rejects symlinks; global/system config isolation, hooks/attributes devnull, filters rejected, no submodule recurse, --no-textconv inserted for diff; run_code refuses host code | Still executes host PATH git. Check/filter scan → execute is mutable TOCTOU, not a pinned filesystem view; local includes/alternates/promisor/repo-config helper surfaces not fully excluded by ^filter scan. git config itself reads config. 15s/30s finite but unbounded output/shared cleanup semantics. Node `_static_check_commands:998` also directly constructs ordinary GitWorktreeSandbox; isolation must be checked per entry, not assumed from backend name | B-internal controlled view + R05; **yes** |
| `_commit_experiment` (`execution/isolated_git_coordinator.py:24–120`) → `_write_commit` (`isolated_git_writer.py:16–65`) → `_CommitCommands(IsolatedGitPlan)`: diff, apply --check --index/apply --index, commit, rev-parse, rev-list, show, status/ls-files | Persisted replay/task/worker binding; exact diff/proposal/evaluation/write contract; CLI principal; consume exact approval after durable claim + re-admit; one-shot begin-write; settlement; exception shield-finishes uncertain with automatic_retry=False. Writer checks baseline/parent/author/message/diff/content after commit, signing disabled. No generic GitBroker dependency | Inherits stronger isolated runner, but no S1 installation or execution-long view guarantee. Rechecks reduce not eliminate races. Hook path disabled plus --no-verify does not itself prove all helper/filter/config paths impossible. Native output and cancellation/process-tree guarantee still incomplete; uncertain persistence failure must retain ownership. Writer helper is internal Python, not a stand-alone authorization endpoint | B-internal isolated executor review; preserve existing transaction state machine, R06/R05 evidence; **yes for full internal claim**, not a newly shown claim bypass |
| Growth replay `evolution/isolated_replay.py:16–54` `git/prepare`: rev-parse verify, clone --no-local --no-checkout local path, checkout --detach, verify HEAD | Explicit revision (reject leading -), commit resolution, source/output separation; separate experiment directory, global/system config isolation and hooks devnull. Not an OS sandbox; docstring says so | Host subprocess.run timeout30/capture_output unbounded; subprocess timeout not full descendant-tree proof; clone/checkout filters/config/attributes and source/object store stability not pinned. Local source does not prove implicit lazy object access/helper/network absent; credentials on disk remain possible. No durable operation ledger for partial prepare | B-internal replay acquisition + R05; **yes** for blanket Git boundary assurance; native effects N |
| Skill Git acquisition `skills/sources/crawl.py:45,70` → `GitHubSource.fetch:151–187`: network clone --depth1 --filter=blob:none --sparse, optional --branch, sparse-checkout set | `Runtime._skill_sources:494–530` builds catalog source; install broker later authorizes installation (`runtime.py:532–543`), not automatically each source subprocess. CLI skills.py:112 can supply ProcessBrokerRunner; explicit refusal raises, no source fallback on broker denial. Skill eager/install trust checks are distinct | Default source runner SubprocessRunner (`skills/sources/cli.py:53–88`) no Policy/Approval; filtered env still PATH/HOME config, clone checkout filters/helpers, lazy fetch/network/credentials. Only timeout caught; direct-child kill, unbounded communicate, cancellation cleanup missing. GitHubSource retries transient failures up to3, including possible partial clone; no UNKNOWN transaction proof. Not covered by GitWriteTool.clone refusal | R04 Skill/process and network acquisition; R17 supply-chain strengthening later; **yes for complete Git I/O claim**, original non-B risk must stay visible |
| Generic CLI / ProcessBroker: `CliRunTool:410–517` → `process_broker.py:108–264`; SkillScriptTool and CliDetect can also invoke arbitrary program named Git | A blocks missing Broker; A2 trusted one-shot handoff prevents caller bool authority, DENY/source checks (`process_broker.py:307–315`); request carries actor/task/resource/scope; normal approval or server allowlist. Local Acceptance remains separately bound | CLI Git is not S1: PATH or absolute executable, arguments/env; no Git-specific repo/config/helper contract. `commands.py:136–236` recognizes named Git read subcommands, rejects -c/config injection, but subcommand label alone does not prove no helpers or branch mutation flags; `--exec-path` is parsed as a value option. Tool approval shortcut never overrides DENY, yet broad process authorization can still execute host Git. Bounded capture only optional; failure started=None/exit=None must remain unknown, not retry proof | B-internal indirect Git admission proposal, and R04 process/tool leftovers; do not weaken A2 or global policies; **yes** |

There is no claim the paths above are reachable by an unauthenticated remote attacker. Reachability is established for configured/embedded runtime or node workflow; authorization and OS trust requirements differ. Plain `trusted_local` config labels are not substitutes for repository stability or operator-approved executable identity. Restricted host-inspector rejection is useful but does not cover host management operations or the ordinary sandbox read at nodes.py:998.

## 6. Historical 49 failures: exact accounting

Source of truth (unchanged): `C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/r04b3-remaining-audits/junit.xml`, `summary.json`, `pytest.log`. Parsed XML testcase failure/error nodes read-only this round: 114 executed, **65 passed /49 failed /0 errors /0 skipped**, child exit1. No rerun or retrospective green rewrite.

### 6.1 35 obsolete execution expectations (not 35 newly fixed native behaviors)

All names in this subsection are original testcase IDs. Prefix A=`src/Sprout/tests/test_r04_broker_audit.py::`, D=`src/Sprout/tests/test_r04_b_git_design.py::`.

| IDs (parameters enumerated completely) | Count | Why obsolete / current valid replacement evidence |
|---|---:|---|
| A `test_every_git_write_operation_obeys_injected_broker[add, branch_create, branch_delete, checkout, clone, commit, fetch, merge, pull, push, rebase, reset, restore, revert, stash_apply, stash_drop, stash_pop, stash_push, switch, tag_create, tag_delete]` (one testcase per listed parameter) | 21 | Old len(broker.calls)==1 conflicts with unconditional retirement; not-result.ok and zero host still valid. B3 all_public_writes21, no_prompt21 and final denial84 provide stronger zero-broker/approval assertions |
| A `test_every_git_inspect_operation_obeys_injected_broker[ls_files, remote, rev_parse, show, stash_list, tag]` | 6 | B1 explicitly unsupported; zero delegation, not one. B1 unsupported/no-broker and static no-host tests retained |
| D `test_delegated_pull_keeps_remote_and_upstream` | 1 | No pull delegation after retirement; B3 write and old-approval refusal cover zero calls |
| D `test_genuine_outer_commit_approval_has_one_prompt_and_bound_identity` | 1 | Generic commit now no prompt, no approved execution. B3 final denial84 + internal Apply control distinguish preserved real Proposal approval |
| D `test_broker_diff_explicitly_disables_textconv` | 1 | Spies old module run_capture and dereferences absent call; S1 denies earlier. B2 _argv test asserts both flags on private runner; native behavior unverified |
| D `test_broker_execution_has_a_finite_timeout` | 1 | Same obsolete observation point, production status never starts. B2 lifecycle Mock checks finite timeout/bounds/cleanup; not a usable S1 claim |
| D `test_mutating_timeout_is_unknown_and_never_retried` | 1 | Expected retired commit to reach runner/raise; now refusal occurs before timeout possible. B3 zero-run interruption/tombstone tests replace; internal UNKNOWN still unresolved |
| D `test_delegated_broker_interruption_is_not_host_retry[TimeoutError-write, RuntimeError-write, CancelledError-write]` | 3 | Write no longer invokes erroring broker. B1 preserved outcome matrix now requires zero Write calls; Inspect interruption expectations remain |
| Total | **35** | Original files and failure evidence preserved, not deleted/skip/xfail |

### 6.2 14 unresolved/deferred failures

| # / original ID (A/D prefixes above) | Current evidence and interpretation | Severity / owner / blocking |
|---|---|---|
| 1 D `test_delegated_inspect_does_not_drop_requested_filters[log-expected0]` | system_tools.py:710–716 only passes limit, drops requested ref; real Broker.log is retired, so no default execution exploit shown. Fake injected broker proves API mismatch, not native Git | Medium deferred P1 read contract; blocks any future log restoration, not generic refusal acceptance |
| 2 D `test_delegated_inspect_does_not_drop_requested_filters[diff-expected1]` | system_tools.py:721 passes workspace only, drops files/base/ref/name_only; default S1 refuses. An arbitrary injected broker can ignore requested restriction | Medium deferred P1 scope/output semantics; same conditional blocker |
| 3 D `test_git_environment_explicitly_blocks_unreviewed_user_config` | git_env.py:35–72 does not set GIT_CONFIG_NOSYSTEM/GLOBAL; Apply/Sandbox callers active; section4 | High B-internal profile/config; **blocks full B** |
| 4 A `test_unclassified_remote_requires_trusted_risk_before_call[interactive]` | mcp/adapter/tool.py:39–49 default medium, invoke:70 directly calls remote; original Fake sees call before trusted risk | High R04-C/MCP F08; blocks R04, not acceptance of retired Git |
| 5 A `test_unclassified_remote_requires_trusted_risk_before_call[cron]` | Same medium classification does not enter high-risk human gate; source alone not safety proof | High R04-C/MCP; same |
| 6 A `test_remote_timeout_does_not_prove_no_effects` | MCPTool.invoke:71–72 converts timeout to ordinary failure instead of ToolExecutionUnconfirmed; remote may have acted | High R04-C effects; same |
| 7 A `test_skill_script_has_no_unbrokered_host_execution` | system_tools.py:561–611 resolves Skill/script then _run_command; script can invoke Git or other I/O, independent of tool retirement | High R04 Skill/process F03; blocks R04 and blanket Git claim, not generic subrange |
| 8 A `test_low_risk_label_is_not_process_authority[CliDetectTool-arguments0]` | system_tools.py:226–253 accepts command, which and direct version probe; low label does not pin executable/config | High R04 process/utility; same |
| 9 A `test_low_risk_label_is_not_process_authority[GrepCheckTool-arguments1]` | system_tools.py:143–183 chooses host grep/rg via PATH, direct runner without ProcessBroker | High R04 process/utility; same (Git-specific exploitation not demonstrated) |
| 10 A `test_database_read_action_cannot_dispatch_mutating_sql[CREATE TABLE test_only (value TEXT)]` | database_broker.py:28–41 classifies DB_READ then _query:101–111 sqlite connect/execute; no read-only URI or SQL read-only enforcement | High R04 DB boundary; blocks R04, **not a Git fix** |
| 11 A `test_database_read_action_cannot_dispatch_mutating_sql[INSERT INTO test_only VALUES ('test')]` | Same; Mock proves dispatch, not durable real INSERT commit | High R04 DB; same |
| 12 A `test_database_read_action_cannot_dispatch_mutating_sql[DELETE FROM test_only]` | Same; do not infer persistence from SQLite default transaction semantics | High R04 DB; same |
| 13 A `test_database_read_action_cannot_dispatch_mutating_sql[PRAGMA journal_mode=WAL]` | Same; DB_READ path can reach state-affecting pragma/connection; Mock only | High R04 DB; same |
| 14 A `test_download_without_network_broker_has_no_effect` | system_tools.py:303–353: guard + optional NetworkBroker, absent broker falls through to direct httpx stream/write. Fake proves fallback; real SSRF/network not tested | High R04 network/download F03; blocks R04, not a Git fix |

35+14=49. Original audit:12 pass/38 fail; B-design:53 pass/11 fail. Some old scope/remote/ref/hook probes now pass vacuously because no runner executes. That is valid zero-effect closure evidence but NOT evidence native/internal Git validates those operands or disables all helpers.

## 7. Historical regression evidence (not new execution)

Evidence root: `C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/`. Each historical run has command.json, summary.json, junit.xml, pytest.log and source-before/after manifests; summary records tracked_and_task_files_unchanged=true. Groups overlap and must NOT be added.

| Directory | Prior result | Exit | Scope |
|---|---|---:|---|
| r04b3-min-final-red | 29 passed /39 failed | 1 | Original68 before approved repair |
| r04b3-min-original68-green | 68 passed, no fail/error/skip | 0 | Original68 unchanged after repair |
| r04b3-r04-safety | 492 passed, no fail/error/skip | 0 | B3 extended179, A/A2/B1/B2, approval/tools/scoping |
| r04b3-internal-mock | 79 passed, no fail/error/skip | 0 | Selected internal validation/provenance/isolated auth; NOT native commit workflow |
| r04b3-r02-r03 | 622 passed, no fail/error/skip | 0 | R02 and Scope regressions |
| r04b3-r03-file-boundary | 44 passed /15 skipped | 0 | Native temporary-file boundary on Windows; POSIX skips, NOT native Git |
| r04b3-remaining-audits | 65 passed /49 failed /0 errors /0 skipped | 1 | Unchanged historical probes above |
| r04b3-final-collect | 4115 collected | 0 | Collection only, not full L1 execution |

Prior Ruff0.16.8 and diff check passed. None rerun this round. Original68 prefix was preserved (10814 bytes, SHA256 AA55C9D33323D28DE6AD265C4F3CA79B16077DDD54EBBB7B5E5EBBEC6FA43C55). New tests appended rather than weakening original assertions. The prior full review artifact is `R04_B3_MIN_FIX_REVIEW.md` in the same evidence root.

Missing evidence: complete L1; real Windows/POSIX Git executable identity/loader verification; stable configuration/view and concurrent replacement; hooks/textconv/filter/fsmonitor/helper/signing/submodule/alternates/promisor/lazy-fetch exclusion; credential/network isolation; whole-process-tree cancellation and output exhaustion; real Apply/index/commit/rollback and isolated one-shot transaction fault recovery. Real-Git setup suites were intentionally not run under Mock-only authorization. Historical external58 not_run, native Windows crash/longpath/symlink limitations remain historical risks, not a new count or newly solved claim.

## 8. Minimal next packages (proposal only; no implementation)

1. **B-internal-Apply preflight/design first:** inventory exact Proposal/Apply actions, patch/index/HEAD identity, task/file Scope and direct-vs-service API. Design trusted installation + execution-long controlled config/attributes view for existing ApplyBroker, not resurrection of generic commit. Define hooks/signing/filter defaults and operator compatibility. Include timeout/bounded output and durable uncertain/no-retry semantics. Needs explicit file scope and native test authorization; do not globally deny GIT_COMMIT and break Proposal flow.
2. **B-internal sandbox reads/management:** distinguish no-effect reads from add -N and worktree/branch lifecycle, close textconv/config/helper surfaces and ordinary-provider use under isolated nodes. Reuse existing lifecycle plus a proven adapter; do not silently borrow B2 issuers or duplicate transaction state machines. No S1 issuer activation without separate authority/view proof.
3. **Existing isolated/replay executor verification:** preserve exact approval, durable claim/one-shot state and uncertain retention; prove installation/view/no implicit network + Windows/POSIX cleanup. No opportunistic rewrite of isolated coordinator.
4. **Indirect CLI/Skill Git admission review:** ensure read-only labels and process approval do not claim Git config/helper safety; source/acquisition retry and cancellation require explicit effect contract. MCP/DB/download residuals stay with original R04 owners; do not start R04-C here.
5. P1 public Inspect filter-forwarding can stay unavailable until requested; no need to implement B4–B7 general-purpose operations. Native S1 remains HOLD. Public schema descriptions promising writes are an acknowledged UX/compatibility debt, not authority or reason to reopen execution.

Rollback: remove/revise this review document only if needed. Any future production rollback must retain exact built-in retirement, broker tombstones, no host fallback, A2 handoff, DENY priority and S1 default refusal. Do not “restore compatibility” via old approvals, arbitrary PATH, plugin substitution or host retry. Existing internal transaction history/approval records must not be rewritten.

## 9. Integrity record

Nine original untracked files matched these hashes before the report was written; they remain historical evidence, not newly accepted contracts:

| Path | SHA-256 |
|---|---|
| docs/audits/R04_B3_MIN_PLAN.md | DCC5CF14A39AE55227A190C8F483B4080E19B58CD4ABABCB03C53C83F31DA65C |
| docs/audits/R04_B_MINIMUM_CAPABILITIES.md | 1DD6ACB79C5C499C7DD1E475D3751D58046564A37963BB2386135B9EACBE50DC |
| docs/audits/R04_B_GIT_DESIGN.md | E2F244764D33D2ECB2DBF4446A4858D3042276FEFD461E06E9F6E9DA7D0B2A1A |
| docs/audits/R04_IO_AUDIT.md | 5E4951A3933BD50FFE035DD644E129D933E6ECAFB99B5FDFB26CF8AF78F8F17C |
| docs/audits/R04_IO_CALLS.csv | E9F94F5906A93D38E0318FB63C5C385430D31F06B8B15D08BFFCF6C0B021AEA5 |
| docs/audits/R04_UPSTREAM_APPROVAL_AUDIT.md | 9EDF7C6D8B26ACF0730FADC1D13D673B927E089E1B069F81F00830BF4C9A3E95 |
| src/Sprout/tests/test_r04_broker_audit.py | 0C32D8ADC14D80E8D29E48255CF37C0D32E01C6E9CC7B98BDC254A9238F4AF5B |
| src/Sprout/tests/test_r04_b_git_design.py | 36713610CBB1426E20EF42CF1BE8DE84D957900DC9890CF62DE42D16335B8CD4 |
| src/Sprout/tests/test_r04_upstream_approval_audit.py | 5875EF44AC2B2F04CD39E244B041271B11A7EE0E1B327BACCC028CAE88E97188 |

No production/test/policy/approval/S1 changes, no staging, commit or hook exception this round. Final report SHA-256 and final Git state are supplied in the handoff (not embedded recursively in this file).
