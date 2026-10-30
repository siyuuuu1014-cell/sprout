# R04 first review — I/O inventory and zero-bypass contracts

## Status, provenance and limits

**Review only. Production code is unchanged. R04 is not complete.**

Baseline: `dev@9258289a3bb3488bd19f2491b765369f0c407090`.
Branch: `codex/r04-broker-zero-bypass`; independent worktree.
The original checkout remains at `a016d357...`; its extra merge and historical
README deletion are not included. Remote dev was checked with read-only Git;
no application/test network traffic, live service or real database was used.

Read with `PR_IMPLEMENTATION_ORDER.md:R04` and `FINDINGS_REVALIDATED.md:F03/F04/F08`.
Historical line references do not supersede this source review.

The companion `R04_IO_CALLS.csv` indexes 1,640 candidate call sites in 485
production Python files (`src/Sprout`, excluding tests, and `web/webapi`), from
23,073 AST call nodes. It contains repository paths, owner symbols, line numbers,
call shapes and review domains, not argument values. It deliberately includes
false positives such as generic execute/replace/request methods. A CSV row is
**not** a proven bypass. Dynamic dispatch, third-party implementation, arbitrary
workspace code, generated subprocesses, C extensions and plugins prevent a
static claim of exhaustive transitive effects. Those remain unproved, not safe.
Use the semantic ledger below to distinguish model actions from trusted service
persistence. Source-manifest evidence is kept outside the repository.

## Current call chain and historical corrections

`runtime/factory.py:93` creates the registry; `runtime/runtime.py:410-423`
**replaces** CLI/download/Git entries with broker-equipped instances. Therefore
the historical assertion that every production Runtime CLI call lacks a Broker
is incorrect. Bare `create_tool_registry()` and public tool constructors still
allow missing brokers (`tools/registry.py:192-203`).

`tools/executor.py:99-200` intersects risk and policy, audits when wired, checks
approval and unattended source, then invokes the selected tool. It does not pass
the caller's DelegationScope into its TOOL_INVOKE ActionRequest. Context is
injected only for `cli_tool_run` (`tools/context_keys.py:34`); it carries task,
source, requester and approval, but not a general authoritative scope/principal.
Do not interpret this as authorization from arbitrary model metadata.

There is **no universal trusted_local/restricted mode parameter** on these tool
constructors or MCP adapter. Actual restricted coding uses server-selected
`NodeExecutor._isolated_coding_required`, isolated model path guards and strict
node-local tools (`runtime/nodes.py:318-381`, `agent/loop.py:478`). Strict resolution
rejects global registry fallback (`tools/executor.py:_resolve`). Docker coding's
router refuses missing backends (`execution/coding_router.py:26`), and sandbox
Git explicitly rejects host execution when `allow_host_git=False`
(`execution/sandbox_tool.py:680`). These are existing safeguards, not bypasses.
Local versus Temporal orchestration alone does not prove I/O isolation.

Below, **L** means current local host capability, **R** means actual restricted
node exposure. Global tool red probes do not by themselves prove a reachable
restricted-node escape. **Outer** means ToolExecutor risk/policy/approval/event
checks when called through it; direct `invoke()` has no such automatic wrapper.
Broker policy events are optional, and the production Runtime omits `events=`
on several brokers. Layered policy may still write its audit stream: absence of
events does not establish absence of every audit, or complete end-to-end audit.

## Model and service action ledger

| Action / source | Direct and indirect effects | L / R exposure | Scope, Policy, Approval, audit and missing dependency | Existing tests / gap / proposed unit |
|---|---|---|---|---|
| `cli_tool_run`, `system_tools.py:400-515` | Host executable; arbitrary child file/network/Git/DB effects | L global and bare constructors; R strict nodes normally exclude it | Runtime injects ProcessBroker; bare constructor runs `_run_command` when missing. Broker receives task/source via executor but no task Scope here. Outer high approval; child env whitelist is not isolation | `test_cli_run_approval`, `test_execution_gate`; R04 red missing Broker. Unit A |
| `cli_tool_download`, `system_tools.py:293-359` | DNS/HTTP and host destination mkdir/write; downloaded code can later execute | L; not in ordinary restricted node tool set | Outer high; guard and optional NetworkBroker. Parent mkdir occurs before broker decision; raw HTTP when missing. Network download itself does not use FileBroker for destination | `test_execution_gate`; R04 Fake HTTP red. Unit A + D |
| `cli_tool_detect`, `system_tools.py:224-262` | PATH lookup and arbitrary discovered executable with version/help argv; indirect effects not known | L; R not node allowlisted | Outer low; flag allowlist exists but no ProcessBroker. Executable name/version label is not no-effect proof | Version-flag tests plus R04 spy red. Unit A |
| `grep_check`, `system_tools.py:107-190` | Host grep/rg process, arbitrary requested path reads, possible option interpretation | L; R uses separate sandbox tools | Outer low; no File/ProcessBroker or caller Scope; rg fallback is tool selection, not isolation | Tool tests plus R04 spy red. Unit A/D |
| `project_analyze`, `project_analyze_tool.py:57-234` | Workspace scan/read; may persist graph/project facts; optional git/rg/tokei/cloc process; local line-count reads | L global; restricted exposure must follow strict context, not its low label | Outer low; Runtime analyzer callback differs from bare scanner. Optional `_system_tool_probe` directly starts process, inherits default child env; timeout returns without explicit child termination | Project/workspace intelligence tests; launch denial/cancellation/scope gaps not dynamically tested here. Unit D |
| `workspace_query`, `workspace_query_tool.py:81` | Analyze workspace then graph reads; analyzer may refresh/persist results | L or server-installed node tool only | Outer low; workspace service/read plan boundaries, not no-effect by name. No tool-local Broker fallback switch | Workspace query tests; prove owner/scope/source over analyzer chain before exposing in R. Unit D |
| `knowledge_search`, `registry.py:78` | Knowledge backend queries, which can use remote DB drivers | L global; strict nodes only if explicitly installed | Outer low; persistence backend is trusted infrastructure, not arbitrary SQL. No direct model-selected backend/credential | Tool/storage tests; remote backend L2 not_run. Ledger service group S |
| `request_workspace`, `registry.py:156` | Returns a request signal; Runtime can persist pending consent afterward | L; no file/process launch here | Outer low; R02 trusted intent, session owner and durable consent remain authority; tool result is not a grant | Existing R02 regressions. No R04 rewrite |
| `strategy_plan`, `tools/strategy_tool.py` | Callback/computation; downstream plans are not authority to execute | Server-installed; not general restricted privilege | Outer low when used; inspect concrete callback, not tool name | Strategy/intent tests; no claimed new bypass |
| `database_query`, `database_tool.py:47-72` | SQLite connect/SQL; read action can create missing DB or dispatch non-read SQL | L global; R not automatically exposed | DatabaseBroker present; task/scope/actor not forwarded by adapter; `_query` uses ordinary sqlite connect, not URI readonly or authorizer. Policy sees SQL and blocks ATTACH/load_extension, but no generic read-only enforcement | `test_security_gaps`; four R04 Fake-connection red cases (CREATE/INSERT/DELETE/PRAGMA). Unit D |
| `sandbox_read_file` / write/edit/apply_patch, `execution/sandbox_tool.py:67-505` | Pinned file read/write/delete; edit/patch can have multiple effects | L sandbox; R path guard further restricts | FileBroker required; task Scope and bound source on mutating tools. R03 secure open preserved. Apply-patch deletion requests approval. No missing-broker host fallback | `test_sandbox_tool`, `test_r03_*`; no new regression established here |
| `sandbox_list_files` / `sandbox_search`, `sandbox_tool.py:514-640` | Directory walk/stat and direct read_bytes; regex compute | L sandbox; R IsolatedModelPathGuard | Outer low; path guard/filter is not FileBroker's open-time policy, redaction, expiry or audit. No per-file Broker. Link replacement/resource classification needs dedicated review | Existing sandbox/isolated-model tests; direct per-resource scope/open boundary not fully tested. Unit D |
| `sandbox_git_inspect`, `sandbox_tool.py:652-703` | git status/diff/log, config/filter/helper effects possible | L host; R rejects host Git before launch | No GitBroker; git_env/git_args pins some config; no scope/approval per operation here | Existing sandbox/isolated tests verify no R fallback. Unit B |
| `isolated_coding_command`, `execution/coding_command_tool.py`, `coding_backend.py` | Authorized Docker command, mutable scratch workspace, durable execution claim | R server backend only | Router/backend refuse missing authority/backend/image; preserve UNKNOWN/cancellation and node claims. No host fallback proven | Coding/router/isolated tests; real Docker L2 not_run. Do not weaken for R04 |
| `skill_view`, `skills/tools.py:91-136` | Trusted skill body or relative auxiliary-file read | L registry; R frozen skill bodies only | Trust/enabled requirements exist; auxiliary filesystem reads use safe-relative resolve then direct read_text, not FileBroker | `test_skills_disclosure`, `test_skills_runtime_wiring`; race/resource audit gap. Unit E |
| `skill_search`, `skills/tools.py:174` | Local cache reads by default; refresh may crawl and write cache | L; R should not use host resolver | Outer low; Runtime sources are catalog/crawl; refresh is an effect, not pure search | Resolver/crawler tests; network/refresh capability needs explicit bound runner. Unit E |
| `skill_install`, `skills/tools.py:229`, resolver/broker | Fetch external bundle, quarantine/write/promote/install index/store | L high; R no implicit host install | Outer high and SkillInstallBroker scan/approval; install approval is not authorization for every earlier fetch. Missing fetch runner can become SubprocessRunner | Existing Skill lifecycle tests. Unit E, retain R00 trust |
| `skill_script`, `system_tools.py:550-611` | Calls ensure_temporal_async before argument validation; then host script process, arbitrary effects | L high; R not ordinary node tool | No ProcessBroker; skill-root resolve does not prove trust/hash/allowed effects. Env whitelist alone not enough | R04 mocked ensure/process red. Unit E |
| Skill GitHub/URL/marketplace fetch, `skills/sources/cli.py:53-87`, source constructors | curl/git subprocess + download/temp/cache; Git config/helpers and curl redirect effects | CLI explicitly local direct by default; model resolver path must be distinguished | SubprocessRunner is documented fallback; ProcessBrokerRunner/NetworkBrokerRunner exist, but Runtime build_crawl_source does not inject them. Network runner output writes host path directly | `test_skills_cli_sources` explicitly pins direct CLI default; cannot silently break operator CLI. Unit E |
| external MCP tool, `mcp/adapter/tool.py:35-77` | Arbitrary remote effects, remote may run local child/service | L global attach; R strict nodes do not automatically inherit globals | Default medium + default allow-medium => outer calls even unattended; no trusted per-op risk/Scope/action mapping. Timeout becomes generic failure, not uncertainty. CancelledError propagates | `test_mcp_adapter` expects medium/success; R04 Fake remote red and timeout red. Unit C |
| MCP stdio lifecycle, `mcp/client/connection.py:39-100` | Launch server executable, initialize, list/call remote APIs, terminate | Trusted config required, but no execution-mode gate here | MCPClientSettings command/args/env are server settings; client manager does not pass configured Principal into connection/tool. SDK stdio launch not ProcessBroker; environment inheritance needs SDK proof | Existing mock adapter/server tests do not prove launch env/identity. Unit C; true stdio L2 not_run |
| MCP resources/prompts, `mcp/client/discovery.py`, adapter resource/prompt | Remote RPC may itself mutate; returned content untrusted | Explicit client discovery/config only | No assertion that list/read names imply side-effect-free remote work. Server-side identity/risk bounds needed | Discovery/adapter coverage; actual remote service not_run. Unit C |
| MCP server exports, `mcp/server/*` | Calls Runtime workspace/task/proposal APIs and serializes results | Server-configured Principal via Gateway | Inbound Principal and R02 session/source rules are separate from outbound client trust; do not conflate | MCP/Gateway auth tests; preserve rather than replace |

## Git operation ledger — each operation, not just tool risk

Entry: `tools/system_tools.py:GitInspectTool.invoke / GitWriteTool.invoke`.
Runtime injects GitBroker, but its operation dispatch is incomplete. **GB** below
means it calls the broker method, not that identity/approval/audit is complete.
**HOST** means `_run_git -> _run_command -> create_subprocess_exec`, without
GitBroker's git_args/git_env pins. Missing Broker uses HOST for every operation.
All ordinary global Git tools are L; current strict R nodes use separate tools.
High-risk outer approvals still apply when using ToolExecutor. No row means a
blanket approval or a right to run in restricted mode.

| Tool operation | Runtime path | Direct/indirect effect and repair requirement |
|---|---|---|
| inspect status | GB.status | Index refresh/filesystem/hooks/helpers may matter; preserve scope, environment and command identity |
| inspect diff | GB.diff | --no-ext-diff exists in GB; textconv/config remains an indirect-execution question; current adapter ignores requested files/base/ref in this branch |
| inspect log | GB.log | Read objects plus config; no scope/task parameters on method |
| inspect branch | GB.branch | Read refs/config; no scope/task parameters on method |
| inspect show | HOST | Read object and possible textconv; must broker and freeze ref/options |
| inspect remote | HOST | Exposes configured remote URLs, possibly userinfo/credentials; redact and broker |
| inspect tag | HOST | Read refs, helper/config context; explicit broker action |
| inspect stash_list | HOST | Read reflogs with user content; explicit broker action |
| inspect rev_parse | HOST | Resolve requested ref; constrain ref syntax and output |
| inspect ls_files | HOST | Index/untracked disclosure; per-file scope and option separators |
| write add | GB.add | Mutates index; clean filters may run; files may be option-like; no task/source/scope forwarded |
| write commit | GB.commit | Stages then commits/hooks/signing; adapter omits task/source/scope and full original approval args |
| write pull | GB.pull | Network fetch plus merge/index/worktree/hooks; adapter ignores requested remote/upstream and omits task/source |
| write fetch | HOST | Network, refs, object writes, transport/helper and credentials; bind remote URL+refspec |
| write push | HOST (despite GB.push existing) | Remote mutation, pre-push hook and credentials; explicit source/approval/refspec/transport constraints |
| write checkout | HOST | Worktree/index/HEAD and post-checkout hook; freeze target, reject unsafe effects in R |
| write switch | HOST | Branch/worktree/index and hook; same constraints |
| write branch_create | HOST | Ref mutation; freeze source/destination names |
| write branch_delete | HOST | Ref deletion; exact authorized target, no caller privilege escalation |
| write merge | HOST | Worktree/index/ref changes and hooks; unknown result not auto-retried |
| write rebase | HOST | Repeated commits, sequencer/editor/hooks, conflicts; no implicit host retry |
| write reset | HOST | Index/ref/worktree effects differ by soft/mixed/hard; distinct approved effect |
| write restore | HOST | Overwrites files; bind source and pathspec; cannot grant all paths implicitly |
| write revert | HOST | Creates commit and invokes hooks, edits worktree/index; exact target approval |
| write stash_push | HOST | Captures file contents then changes worktree/index; scope contents and destination |
| write stash_apply | HOST | Writes files/index; conflicts can leave partial effects |
| write stash_pop | HOST | Apply plus reflog deletion; multi-stage partial/unknown semantics |
| write stash_drop | HOST | Deletes reflog entry; exact stash identity |
| write tag_create | HOST | Ref plus optional annotated tag/signing effects; exact name/target |
| write tag_delete | HOST | Deletes ref; exact target |
| write clone | HOST | URL/transport/helper/credential access and target-directory writes; current raw URL isn't a trusted remote |

`execution/git_broker.py:193-231` applies policy and optional events but only
commit/push/pull check approvals, using reduced args and default actor/source.
`git_env.py:20-24` explicitly keeps repository hooks enabled; pins only fsmonitor
and pager (`:49-54`). SecretBroker filters environment, but this does not prove
credential helpers, global/local Git config, URL rewrite, protocol helpers,
filters, signing, hooks or remote destination are harmless. `_safe_git_ref`
only refuses leading '-' and NUL; it is not a remote/refspec trust validator.
Bare GitBroker itself accepts an option-like remote in the mock regression.

## Lower-level/service I/O inventory and ownership

| Service family / locator | I/O and trusted purpose | Authorization / mode / missing capability / test ownership |
|---|---|---|
| `workspace/read_broker.py:ReadBroker.read`, scanner/intelligence/resource cache | Classify and read repository inputs; direct pathlib in scanner/project facts | R03 FileBroker improvements do not automatically cover this separate reader. Scope is accepted, but safe-open and cache provenance require review. Workspace/user-facing adapters must not replace owner/consent. Workspace/security tests; races not dynamically proved here |
| `execution/apply.py`, patch/change, `sandbox/git_worktree.py` | Patch temp files, git worktree create/remove, apply/commit/rollback | Apply uses policy, approval and provenance gates; Git helper pins differ from raw tools. GitWorktree has a logical boundary, not hostile-code isolation. Preserve R01/R03 completion gates; changes here require separately scoped review |
| `execution/process_broker.py`, proc/verification/local acceptance | Authorized host child process and capture/terminate | Policy+Scope+source/approval; R01 executor binding special path preserved. DENY remains DENY; upstream approval bypasses second REQUIRE_APPROVAL for non-Acceptance calls. Generic raw flag isn't proof of server authority outside trusted ToolExecutor. Real execution L2 not_run |
| `execution/network_broker.py` | HTTP GET/POST, secret redaction, download writes | Guard is optional; Runtime may construct without guard when no SecurityLayer. Downloads make raw files without FileBroker/Scope. Network access is not filesystem write approval. Need Fake HTTP/transport tests before fixing |
| `execution/database_broker.py` | SQLite query/write | Policy receives SQL; DB_READ currently not enforced by readonly URI/authorizer. sandbox bool in write isn't independently proven storage isolation. R04 Fake connect red; actual user DB never opened |
| coding/verification/function/docker/isolated_* backends | Export/import scratch files; Docker argv; persisted execution claims; isolated Git requests | Existing node-owner, manifest, frozen identity and no-host-fallback contracts must be retained. New I/O must use their existing authorized runner, not global CLI. Native Docker/Temporal L2 requires separate authorization |
| `storage/local/*`, `rootstock/backends/*`, context/memory/blob/trajectory/usage/audit stores | SQLite/JSONL/files and optional Redis/Milvus/Neo4j; durable internal bookkeeping | Trusted service persistence is not a model-selected general SQL capability and does not automatically need a new Broker. Backend/config/data-path authority is server-side. Any model-reachable arbitrary DB/path must use corresponding boundary. Temp/memory tests only; true external DB not_run |
| `models/providers/*`, model/router/transport | HTTP SDK request and model output | Model credentials/provider are trusted server config; not NetworkBroker tool downloads. Models cannot use metadata to gain authority. No real model called in this review |
| `gateway/channels/*`, CLI/Web/SDK/MCP HTTP/WS/lifecycle | Inbound/outbound chat, polling, auth/session persistence, user-visible files | Existing R02 trusted Principal/session boundaries remain. Connector networking is a service capability, not granted to model tools; controlled modes must not inherit it. Real channels/network not_run |
| orchestration queues/workers/terminal ensure | Temporal client RPC, worker lifecycle and optional service bootstrap | Not authorized just because Skill tool executes; separate startup authority needed. Temporal/distributed uncertainty tests stay separate from global tool dispatch. Real Temporal not_run |
| evolution/evaluation/replay/knowledge graph/project diagnostics | Candidate artifacts, isolated runs, reports/indexes and optional model/replay execution | Retain durable claim/provenance/frozen-source semantics. Evaluation test fixtures in production package are indexed as candidates, not assumed online tool exposure. No automatic historical task replay or L2 execution |
| skills index/quarantine/layout/local sources | Local manifests/hash/read/promote/delete/write | InstallBroker decides trust/approval; local file operation boundaries and task context still need inventory mapping. Existing R00 and Skill lifecycle tests, no new trust from install text |

## Reproduction evidence (no real subprocess/network/DB effects)

External evidence directory:
`C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/`.
The isolated runner explicitly verifies imports come from this R04 worktree,
uses the existing locked venv, isolated HOME/temp/JUnit, bytecode/cache disabled
and network/SQLite/write guards. That guard is not an OS hostile-code sandbox.

- `r04-existing-baseline`: 35 passed / 0 failed / 0 errors / 0 skipped, raw Python 0;
  existing `test_tools.py`, `test_mcp_adapter.py`, `test_tool_scoping.py` unchanged.
- `r04-red-broker-audit`: first 43 contracts, 31 failed / 12 passed, raw Python 1.
- `r04-red-final`: 50 contracts, **38 failed / 12 passed / 0 errors / 0 skipped**,
  raw Python 1. Original failure logs/JUnit/commands/hash manifests retained.
- All 38 failures are expected red safety contracts, not fixed or marked xfail.
  `_run_command`, `_run_git`, GitBroker capture and remote MCP are spies/Fakes.
  SQL uses a Fake connection; no real SQLite connection is opened by those probes.
  Skill Temporal ensure is mocked; Fake download writes only a temporary file.
- Passing controls: injected add/commit/pull and status/log/branch/diff obey lower
  DENY; unattended high-risk outer CLI refuses; lower ProcessBroker DENY,
  timeout and cancellation do not trigger a second host execution.
- Failure breakdown: 1 missing ProcessBroker; 18 Git writes and 6 Git inspections
  bypass injected broker; 2 remote/hook contracts; 2 unclassified MCP calls
  (interactive/cron); 1 MCP timeout uncertainty; 1 unbrokered Skill script;
  2 low-risk process tools; 4 mutating SQL via read; 1 missing download broker.

Important: the hook probe proves no explicit hook isolation argument is provided,
not that a malicious real hook ran. The option-like remote probe uses fake policy
and process capture, not an actual push. Missing-broker probes use public tool
construction; they do not assert a demonstrated strict-node escape. Remote MCP
timeout proves evidence is collapsed to ordinary failure, not that a remote side
effect actually happened. Credential/config/refspec full attack paths remain L2
or further mocked tests, not established safe or exploitable here.

Existing `test_tools.py:test_git_tools_fall_back_when_broker_lacks_new_operations`
explicitly expects current fallback; `test_mcp_adapter.py` expects medium risk;
`test_skills_cli_sources.py` pins operator CLI direct-fetch default. None were
weakened. Their contract changes require review alongside future implementation.

Final related regressions, collection, Ruff, source hashes and raw exit codes are
retained in `r04-related-final`, `r04-final-collect` and external review evidence.
They must be read independently of the intentionally failing red suite; no
claim that all tests pass or that full L1/L2 ran is made.

## Proposed minimal implementation order (approval required)

| Unit | Proposed files and steps | Regression / DoD / compatibility and safe rollback |
|---|---|---|
| A: fail-closed process capability | `tools/system_tools.py`, `tools/registry.py`, minimal existing Runtime injection if needed. Require ProcessBroker on model-visible execution, including detect/grep/probes. Decide server-side trusted-local operator exception explicitly; no model mode flag. Preserve outer gate and exact context | Red missing-broker and low-risk process tests green; reject before spawn, preserve DENY/cancel/timeout. Legacy bare constructors may stop working; disable capability rather than restore fallback. Do not redesign AgentLoop |
| B: all Git operations through authority | `execution/git_broker.py`, `execution/git_env.py`, `tools/system_tools.py` and corresponding tests. Enumerate actions, validate operation/argv/paths/remote/refspec, server-selected local/restricted hook/credential contract, same pinned approval identity/task/source/scope and explicit audit | 21 write/10 inspect spy matrix, denied no-spawn, no implicit add-all, read helpers tested. Unknown/new operation refuses instead of host fallback. Pin approved destination and effects. Existing fallback/approval-args tests need explicit reviewed update; disable unsupported operations on rollback |
| C: MCP default risk and launch | `mcp/adapter/tool.py`, `mcp/client/client.py`, `connection.py`, server-side config contracts if necessary. Unknown operation denies; authenticated server/operation capability, Principal/env/timeout bounds; uncertainty remains uncertain | Fake high/unknown/unattended and forged annotations; startup no-broker/missing identity, cancel/timeout, no ambient secrets. Existing adapter medium tests must change explicitly. Schema/config expansion requires version/compatibility decision before edits. Disable unknown remote tools on rollback |
| D: residual read/network/DB effects | `tools/project_analyze_tool.py`, `database_tool.py`, workspace/sandbox readers, `execution/network_broker.py`, `database_broker.py`. Treat destination write separately; enforce actual SQL read-only capability; resource-open checks rather than labels | No side effects on denied DB/query/download; path scope, redaction, temporary SQLite/Fake HTTP, no CWD authority. Split per I/O family, no wholesale framework. Do not add a second broker; disable unsupported adapters on rollback |
| E: Skill process and fetch boundary | `tools/system_tools.py:SkillScriptTool`, `skills/sources/cli.py`, resolver/runtime source assembly, CLI operator path only if approved. Validate before bootstrap, bind script trust/hash and capabilities, use existing runners with no fallback in restricted/model path | Temp script/spawn spy, no unsolicited Temporal startup, fetch/refresh/install independent authority; keep R00 trust and existing direct-operator CLI behavior explicitly scoped. Remote lifecycle/supply-chain expansion stays R17 |
| F: cross-surface proof | Tests and existing docs only after A-E. Enumerate real Runtime, standalone SDK, Local/Temporal, strict node and server-installed tools | Same Scope/identity/policy/approval/audit and no escalation on failure. Full offline L1 plus separately approved Linux/Windows/native process/Git/stdio L2. Do not call mocks proof of OS containment |

Recommendation: review the ledger, then authorize **A only** with a concrete
server-side trusted_local/restricted compatibility decision. B/C may have
separate PR-sized reviews after A; they are not implicitly approved by this
document. Do not infer a universally trusted nonlocal backend from its name.

## Remaining risks, next gate and rollback

F03/F04/F08 remain open. No production defect is fixed in this first review.
Real hook/credential/transport effects, remote MCP semantic side effects, Linux
native behavior and external services remain not_run. Existing strict-node,
ApprovalManager, PolicyEngine, R02 intent/session/consent and R03 secure-file
logic must remain unchanged while implementing the approved units.

The review adds only documents and a red regression module. Rollback means
archiving/removing those task-owned additions after approval; no persistence
migration or runtime rollback is needed. For later production units, capability
disablement is the rollback: never reopen missing-broker host execution to
recover compatibility. No commit, stage, push, PR, merge, deployment or R05 was
performed. Stop here for scope and compatibility review.
