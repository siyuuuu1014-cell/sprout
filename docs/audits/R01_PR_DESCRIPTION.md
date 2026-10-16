# R01 PR description — local draft, not a GitHub PR

Base: `dev`; compare: `codex/r01-baseline-ci`.
Starting commit: `32e5f734fc556f5ccdce152b8ec949b75021fbd6`.
Task: R01 / reproducible baseline and L0/L1 CI (F11; documentation-only F14 correction).
Status: **draft / blocked; do not merge as a green baseline**.

## Changes

- Add newly authored, versioned reference fixtures; keep all 18 existing example behavior tests and remove root-level module/sys.path assumptions. These fixtures do not demonstrate Agent coding ability.
- Add a fresh-environment baseline runner, credential-free source snapshot, temporary test home/Git config, complete logs, JUnit and per-test external-gate inventory.
- Add explicit Docker/Temporal/model/database gate classification and 13 offline infrastructure contracts. Missing prerequisites or skipped explicitly selected integration checks cannot count as success.
- Repair stale preflight test doubles by binding the real Runtime helper and preserve test bootstrap paths in the CLI child. Do not change existing business assertions.
- Add SHA-pinned official Actions for Ubuntu/Python3.12 and Windows/Python3.13, locked installation, imports, lint, complete collection and offline L1; upload evidence on failure.
- Record the independent-branch / reviewed-PR workflow in AGENTS.md. Restore none of the eight historical documents; their recovery remains a separate PR.

## Evidence and tests

See `docs/audits/R01_TEST_BASELINE.md` for full commands, intermediate failures and scope.
Latest full Windows clean run (previous execution, not a new run in the branch-bookkeeping turn):

| Check | Actual result |
|---|---|
| Locked clean install | Python3.13.9 / uv0.11.25; lock124 packages, install68; original .venv/uv.lock unchanged |
| Imports / ruff | passed |
| Full core + Web collection | 2396 collected, 0 collection errors, 1 warning |
| Example behaviors | 18 passed |
| R00 regression | 67 passed |
| Offline L1 | 2336 passed, 1 failed, 1 skipped, 58 not_run, 2 warnings |
| Docker / Temporal / real model / external databases | not_run |
| Ubuntu and Windows GitHub Actions | not_run; no push/PR authorized yet |

The one Windows skip is symlink creation unavailable. Do not add subset pass counts to the full-suite total.
Full logs, JUnit and source manifest remain in `.workbuddy/test-runs/r01-clean-windows-reviewed/`;
do not commit scratch databases, credentials or complete virtual environments.

## Unfinished / merge blockers

1. `src/Sprout/tests/test_evaluation_runner.py::test_fixed_suite_and_persisted_report` fails:
   `evaluation/runner.py:87` generates a conftest import block without a separating blank line,
   triggering ruff I001. C01/C02/C03/R01 remain unverified and scenario_passed is6/10.
   No assertion, command or completion gate was weakened. Fixing the business template still
   needs the separate approval requested in the R01 report; it is not included here.
2. Remote Actions have not run. Push requires explicit approval; after push, open this PR against
   dev and wait for review. Do not merge it or delete the remote branch automatically.
3. Real integrations remain independently authorized gates, not inferred from offline tests.

## Compatibility and rollback

No Runtime/Broker/approval/schema/task-execution changes, public API changes, migrations or
production configuration/data writes. R00's two files remain unchanged.
Rollback the task branch's test/fixture/CI/documentation commits only; retain dev/R00 and test
evidence. Do not reset the repository or remove user databases. If later approved for merge,
revert the reviewed task commits through another reviewed PR rather than rewriting history.
