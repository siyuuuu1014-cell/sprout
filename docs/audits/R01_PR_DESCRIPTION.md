# R01 PR #2 — Draft / Windows CI blocked

Existing PR: https://github.com/siyuuuu1014-cell/sprout/pull/2

Base: `dev`; compare: `codex/r01-baseline-ci`.
Starting commit: `32e5f734fc556f5ccdce152b8ec949b75021fbd6`.
Task: R01 / reproducible baseline and L0/L1 CI (F11; documentation-only F14 correction).
Status: **draft / blocked; do not merge as a green baseline**.

## R01-DIAG local patch — NOT pushed, remote results below are historical

Authorized test-only diagnostics reuse existing evaluation JSON and read-only Runtime queries for the four task_e2e failures. Explicit projections contain known enums, numeric exit codes/versions/counts and fixed error-signature categories only. No Runtime, Broker, approval, Apply, Oracle or business assertion changes. First failure is record order, not a wall-clock root-cause claim.
CI upload is restricted to `public-diagnostics/*.json`: `baseline.json` plus up to eight fixed failure-fixture summaries. Raw logs/JUnit/SQLite/dialogue/metadata/commands/approval parameters/diffs/temp directories are NOT uploaded. Original raw reports remain local; subprocess log tails no longer appear in Actions console. Tests inject simulated credentials and private contents to prove exclusion at projection time, not post-upload masking.
Local validation and exact final counts are in R01_TEST_BASELINE.md. Remote Windows root cause remains unestablished until separately approved push/retest. This patch is a local commit only; PR #2 remains Draft, no merge/Ready/R02. Previous uncommitted audit records are preserved in this local change, not silently dropped.
Final clean local verification:2415 collected (0 errors,1 warning); L1 **2356 passed,0 failed,1 skipped,58 not_run,2 warnings**; examples18 passed,R00 67 passed; locked install/imports/ruff passed. All15 diagnostic tests passed within L1. One skip is local Windows symlink support, not a passed check. No new remote CI was run. During the pause, operator commit37934c9 already recorded the two new diagnostic files; retain it and commit only the remaining six scoped files. Preserve the unrelated README-交接说明.md deletion unstaged. Neither local commit is pushed this round.

## Latest remote verification — c9eaec5 pushed with approval

Reviewed commit `c9eaec552d6b92efe580185a99a066cc46545272` was normally pushed to the existing branch (no force).
PR #2 remains open/Draft, base dev `32e5f734fc556f5ccdce152b8ec949b75021fbd6`; no merge or Ready transition.
[Actions run 38885294318](https://github.com/siyuuuu1014-cell/sprout/actions/runs/38885294318) automatically tested merge-check SHA `eeeebb30b0e500cd0443366ae43092b25a7bf310`, not a merged dev commit.

| Actual check | Ubuntu / Python3.12.15 | Windows / Python3.13.16 |
|---|---|---|
| Job | completed/success,116921454445 | completed/failure,116921454638 |
| Locked installation | lock124/install66,exit0 | lock124/install68,exit0 |
| Imports / ruff | passed | passed |
| Complete collection | 2400,0 errors,1 warning | 2400,0 errors,1 warning |
| Examples / R00 | 18 / 67 passed | 18 / 67 passed |
| Full offline L1 | 2341 passed,0 failed,1 skipped,58 not_run,2 warnings | 2337 passed,5 failed,0 skipped,58 not_run,2 warnings |
| Template Ruff and three snapshot regressions | all passed | all passed |
| Fixed 10-scenario evaluation | passed | failed,6/10 |
| Four task_e2e cases from first review | all passed | all still failed |

Ubuntu's only skip is the Windows-only junction guard, not a passed integration. Docker,Temporal,real models and external databases remain not_run.
Windows failures: fixed evaluation (`test_evaluation_runner.py:58`); delete-approve and rename-approve (`test_task_e2e.py:205`); requirement output `[True]` (`:318`); proposal Agent-only edits (`:424`). Completed/verified assertions were not changed.

Both logs and downloaded JUnit/summary confirm these results. The template lint now passes on Windows, so persistent failures cannot simply be attributed to the missing blank line. The 18-file Windows artifact lacks internal task/NodeResult/VerificationReport/approval/Apply evidence and has no JUnit system-out. Platform/environment versus Runtime root cause is **not established**. The existing evaluator writes nodes.json and verification.json under pytest-temp, outside the workflow's artifact glob.

**R01 DoD remains unmet.** Proposed next step, not implemented: approve a narrowly scoped R01 diagnostic patch to retain whitelisted test-only task/verification/apply evidence and rerun Windows. No Runtime changes until evidence supports an independently reviewed fix. Do not skip tests, weaken gates, merge, mark Ready or start R02.
Artifacts: [Ubuntu12038987947](https://github.com/siyuuuu1014-cell/sprout/actions/runs/38885294318/artifacts/12038987947), [Windows12039318726](https://github.com/siyuuuu1014-cell/sprout/actions/runs/38885294318/artifacts/12039318726). Local downloaded evidence: `.workbuddy/test-runs/r01-remote-run2/`.
Only the reviewed c9eaec5 code commit was pushed. At that remote-verification handoff the audit edits were uncommitted; they are preserved and included in the subsequent R01-DIAG local patch, not pushed. The remote PR body was updated at that handoff; this DIAG update remains local. All pre-push/not_run descriptions below are historical observations, not erased results.

## Changes

- Add newly authored, versioned reference fixtures; keep all 18 existing example behavior tests and remove root-level module/sys.path assumptions. These fixtures do not demonstrate Agent coding ability.
- Add a fresh-environment baseline runner, credential-free source snapshot, temporary test home/Git config, complete logs, JUnit and per-test external-gate inventory.
- Add explicit Docker/Temporal/model/database gate classification and 13 offline infrastructure contracts. Missing prerequisites or skipped explicitly selected integration checks cannot count as success.
- Repair stale preflight test doubles by binding the real Runtime helper and preserve test bootstrap paths in the CLI child. Do not change existing business assertions.
- Add SHA-pinned official Actions for Ubuntu/Python3.12 and Windows/Python3.13, locked installation, imports, lint, complete collection and offline L1; upload evidence on failure.
- Record the independent-branch / reviewed-PR workflow in AGENTS.md. Restore none of the eight historical documents; their recovery remains a separate PR.
- First-review correction: add only the missing blank line in generated evaluation conftest; preserve all ten scenarios, Oracle and gates. Add a Ruff regression.
- Restrict snapshots to `git ls-files -z`; add three temporary-repository regressions proving untracked simulated sensitive files are excluded and remain untouched.

## Evidence and tests

See `docs/audits/R01_TEST_BASELINE.md` for full commands, intermediate failures and scope.
Original full Windows clean run before review corrections (historical, not the latest result):

| Check | Actual result |
|---|---|
| Locked clean install | Python3.13.9 / uv0.11.25; lock124 packages, install68; original .venv/uv.lock unchanged |
| Imports / ruff | passed |
| Full core + Web collection | 2396 collected, 0 collection errors, 1 warning |
| Example behaviors | 18 passed |
| R00 regression | 67 passed |
| Offline L1 | 2336 passed, 1 failed, 1 skipped, 58 not_run, 2 warnings |
| Docker / Temporal / real model / external databases | not_run |
| Ubuntu and Windows GitHub Actions | see actual completed runs below; neither platform is green |

The one Windows skip is symlink creation unavailable. Do not add subset pass counts to the full-suite total.
Full logs, JUnit and source manifest remain in `.workbuddy/test-runs/r01-clean-windows-reviewed/`;
do not commit scratch databases, credentials or complete virtual environments.

First-review clean retest: `.workbuddy/test-runs/r01-review-clean-windows-1/`, Python3.13.9 / uv0.11.25, lock124/install68, imports/ruff passed; collection2400 (0 errors,1 warning); examples18 passed, R00 67 passed; L1 **2341 passed,0 failed,1 skipped,58 not_run,2 warnings**,257.20s. No assertion or gate was relaxed. The local symlink skip remains explicitly unverified. New source snapshot uses tracked working-tree bytes; source-manifest.json identifies the exact content, not merely the pre-fix HEAD. Remote checks for these unpushed corrections remain not_run; R01's complete DoD is still blocked by remote revalidation and the four additional Windows CI failures.

## Unfinished / merge blockers

1. The authorized template fix now passes its targeted regression and unchanged fixed evaluation locally (18 combined targeted tests passed). Before the fix, template regression failed once with I001; snapshot regressions failed in all three directories. Full clean retest results are recorded in R01_TEST_BASELINE.md.
2. Actual [Actions run 38883570451](https://github.com/siyuuuu1014-cell/sprout/actions/runs/38883570451), testing original head d08de113 via merge SHA e6745159: Ubuntu/Python3.12.15 completed/failure, L1 2336 passed/1 failed/1 skipped/58 not_run; Windows/Python3.13.16 completed/failure, L1 2333 passed/5 failed/0 skipped/58 not_run. Both collected2396, passed imports/ruff, examples18 and R00 67; both reported2 L1 warnings. Windows has four additional task_e2e completion failures, not proven to share the evaluator's root cause. Short logs lack internal task evidence; diagnosis is incomplete, not waived. Review fixes have NOT been pushed and their remote checks are not_run. Keep this existing PR Draft; do not open a replacement, merge or mark Ready.
3. Real integrations remain independently authorized gates, not inferred from offline tests.

## Compatibility and rollback

No Runtime/Broker/approval/schema/task-execution changes, public API changes, migrations or
production configuration/data writes. R00's two files remain unchanged.
The only production-source change is generated conftest whitespace, not evaluation business semantics. Snapshot inventory now intentionally excludes new untracked files; add intended new files explicitly before testing them in snapshots.
Rollback the task branch's test/fixture/CI/documentation commits only; retain dev/R00 and test
evidence. Do not reset the repository or remove user databases. If later approved for merge,
revert the reviewed task commits through another reviewed PR rather than rewriting history.
