# R04-A supplementary audit: upstream approval provenance

Date: 2026-10-21. Investigation and intentionally failing tests only.
Branch: `codex/r04-broker-zero-bypass`.
Unmodified HEAD: `0d53f7dd26fbd49b37b02db7905ec33c9ad3ab6a`.
No production edits, staging, commit, push, external services or real child execution.
This record was added after the final test run; it is not part of that run's hash manifest.

## Confirmed finding

An in-process caller holding a broker-injected `CliRunTool` can bypass its
REQUIRE_APPROVAL gate by directly invoking it with a caller-created dictionary:
`_sprout_context.approval_granted=True`. This also bypasses the unattended-source
refusal for `cron` and `automation`. All three cases reached a mocked OS process
creation exactly once, with `result.ok=True`, without a genuine human approval.

This is a confirmed capability/provenance defect in the direct Python API.
It is NOT proof that an external model, MCP client, message sender or restricted
node can access that direct API. No such stock external bypass was established.
Explicit policy DENY remains DENY and prevents mock process creation.

## Code evidence and reachability

Line references apply to the unchanged HEAD listed above.

1. Runtime exposes its registry as `self.tools` (`runtime/runtime.py:329`) and
   injects the real ProcessBroker into CliRunTool (`runtime/runtime.py:409-414`).
   `ToolRegistry.get()` returns the actual object (`tools/registry.py:40-41`).
   Trusted embedding/extension Python can therefore call
   `runtime.tools.get("cli_tool_run").invoke(arguments)` directly. The test uses
   the same class with an explicitly injected real broker, not a fake broker.
2. CliRunTool forwards the caller's reserved dictionary
   (`tools/system_tools.py:447-448`) and coerces its claimed approval to bool
   (`tools/system_tools.py:469-481`). The string key is a convention, not a
   provenance capability (`tools/context_keys.py:26-33`).
3. ProcessBroker evaluates real policy (`execution/process_broker.py:166`), then
   passes the bool into `_withheld` (`:171-182`). `_withheld` rejects non-approval
   decisions first (`:290-291`), but returns permission on the bool (`:296-298`)
   BEFORE checking manager availability or unattended source (`:299-314`).
   Execution reaches `asyncio.create_subprocess_exec` (`:199`).
4. Standard ToolExecutor starts with `approval_granted=False` (`tools/executor.py:140`),
   consults real approval records (`:159-168`), and replaces caller context after
   policy/approval (`:184-193`, `:252-266`). A fake bool cannot survive this path.
   With default policy, the interactive call parks; unattended calls are refused
   before invoke (`:148-158`). With a test-only outer allow policy, the real broker
   still receives false and the server's task/session/source/requester.
5. Default model dispatch: `agent/loop.py:443-487` ->
   `ActionExecutor.run_calls_detailed` (`agent/executor.py:71-87`) ->
   `ToolExecutor.execute`. A mocked model ToolCall cannot select direct invoke.
   The isolated test covers the ActionExecutor boundary, not a real model call.
6. Node dispatch uses `with_scoped_tools(..., strict=True)`
   (`agent/loop.py:478`); `ToolExecutor._resolve` refuses registry fallback
   (`tools/executor.py:267-274`). Current node tools are explicitly assembled
   (`runtime/nodes.py:321-370`, `:426`) without global cli_tool_run. Isolated model
   adapters reject unsupported names, including CLI (`execution/isolated_model_tools.py:91-93`).
   Empty strict-overlay refusal is tested; no real container/worker is run.
7. MCP inbound `sprout_message` calls Runtime.handle with its fixed Principal
   (`mcp/server/tools.py:120-135`), not raw CliRunTool.invoke. MCP outbound
   `MCPTool.invoke` calls its remote connection (`mcp/adapter/tool.py:68-70`), not
   this host CLI object. Remote service behavior is outside this evidence.
8. Skill instruction text becomes model context, and its tool requests follow
   the same default AgentLoop route. `SkillScriptTool` is a DIFFERENT high-risk
   executor (`tools/system_tools.py:507`, `:539-590`) with the previously known
   R04 audit risk. No Skill script or Temporal service was executed here. Arbitrary
   Python extensions/custom Agents holding actual context tool objects can call
   them directly, but that is trusted in-process code, not proof that Skill text
   or external MCP parameters obtain such references.

There is no uniform `trusted_local` flag that grants authority here. Trusted local
embedding code has the direct object access described above. Current restricted
node construction does not expose that object; this audit does not claim complete
R04/R05 isolation or arbitrary-plugin safety.

## Tests, results and evidence

New test: `src/Sprout/tests/test_r04_upstream_approval_audit.py`.
Its SHA-256 after final run:
`5875ef44ac2b2f04cd39e244b041271b11a7ee0e1b327baccc028cae88e97188`.

Real components: PolicyEngine inside LayeredPolicyEngine assembled from
`default_settings().security`, ProcessBroker, ApprovalManager and memory store.
An observation wrapper records decisions without changing them. Only final
`asyncio.create_subprocess_exec` is mocked; direct `_run_command` fallback raises.
No command, Git subprocess, socket connection or external model runs.

| New tests | Cases | Passed | Failed | Mock spawn evidence |
| --- | ---: | ---: | ---: | --- |
| Missing/false claimed approval controls | 6 | 6 | 0 | zero |
| Forged true, interactive/cron/automation | 3 | 0 | 3 | one per case |
| Explicit real organization DENY | 3 | 3 | 0 | zero |
| Default ToolExecutor refuses/parks | 3 | 3 | 0 | zero |
| Executor overwrites forged context before real broker | 3 | 3 | 0 | zero |
| Model ActionExecutor boundary | 1 | 1 | 0 | zero |
| Strict node refuses registry fallback | 1 | 1 | 0 | zero |

Final combined run includes the new 20 tests plus the committed 21 R04-A tests and
4 CLI approval regressions: **45 collected/executed; 42 passed, 3 failed, 0 errors,
0 skipped; raw Python exit 1; 2.22 seconds**. The three failure assertions explicitly
require refusal and zero spawn. They remain red, with no skip/xfail.

Final command (PowerShell, executed from the R04 worktree):

```powershell
& D:/dev/projects/sprout/.venv/Scripts/python.exe -I -S -B C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/r03_worktree_checks.py C:/Users/WingYouther/.codex/worktrees/r04-broker-zero-bypass/sprout r04a-upstream-final src/Sprout/tests/test_r04_upstream_approval_audit.py src/Sprout/tests/test_r04_cli_broker.py src/Sprout/tests/test_cli_run_approval.py
```

Evidence directory:
`C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/r04a-upstream-final/`.
Files: `command.json`, `pytest.log`, `junit.xml`, `summary.json`,
`source-before.json`, `source-after.json`. Source hashes match before/after.
JUnit properties record fixed policy enum, forwarded bool/source and mock spawn
count. Pytest issued 15 record_property/xunit2 compatibility warnings; the actual
properties were independently read from the generated XML. No warning suppressed.

Earlier calibration evidence is preserved:
- `r04a-upstream-audit`: 14 passed / 6 failed, exit 1 (20 cases).
- `r04a-upstream-layered`: 39 passed / 6 failed, exit 1 (45 cases).
Both initially omitted the organization DENY rules; the git-force test therefore
received REQUIRE_APPROVAL rather than the intended explicit DENY. This was a
test-fixture configuration error, NOT proof that a real DENY was overridden.
The final fixture uses the actual default Settings assembly; no production policy
or test safety assertion was weakened. The three forged-approval failures persist.

Full-repository Ruff 0.16.8: passed, exit 0. `git diff --check`: exit 0.
Full L1, native POSIX, Docker/Temporal, real MCP/Skill execution, real process L2,
real databases, live networks and paid models: not_run.

## Proposed minimal remediation (NOT implemented or authorized)

Candidate production scope: `tools/context_keys.py`, `tools/executor.py`,
`tools/system_tools.py`, `execution/process_broker.py`, plus targeted tests.
Remove authority from caller-created dictionaries/bools. Keep their attribution
data separate from a server-issued, broker-verifiable approval handoff bound to
the actual authorized invocation, caller/source and validity. Verify the handoff
at the broker; an arbitrary caller-chosen object or `trusted=True` is insufficient.
Retain double gating, unconditional DENY precedence, legitimate one-prompt reuse,
Local Acceptance's separate executor-bound authorization, and unattended behavior.
Do not simply delete upstream support and silently break valid approval reuse.
Exact implementation and any persistence/atomic-consumption needs require review
and fresh authorization; no schema or generic approval behavior changed here.

## Final state / rollback

Only new investigative test and this new record were added this round.
Committed production/test files remain unchanged; HEAD remains 0d53f7d, index empty.
Original R04_IO_AUDIT.md, R04_IO_CALLS.csv and test_r04_broker_audit.py retain their
previous hashes. No commit was amended. No rollback is performed. A future safe
remediation must preserve the R04-A no-broker denial, not restore host fallback.

**HOLD for this newly confirmed direct-API authorization-provenance defect.**
Stop and seek approval for a separate narrowly scoped repair; do not begin R04-B.
