# Sprout — Codex repository instructions

## Project context
- Product: local-first, embeddable AI software-engineering runtime; preserve the existing Python modular monolith, Local/Temporal orchestration, CLI/Web/MCP/SDK public surfaces, audit/approval, independent verification, and evolution safeguards.
- Constraints: do not train, fine-tune, or distill models. Do not replace the project with another high-level Agent runtime. Prefer incremental, reviewable changes.
- Read `docs/architecture/sprout-v1.md` for product direction and `docs/architecture/sprout-review-v2.1.md` for source-review findings F01–F23 and execution packages R00/R01–R18. Both documents describe a historical baseline, not guaranteed current code.
- **Authority:** inspect current source and tests first; if documentation conflicts with code, report the discrepancy and reason. For planned work, apply v2.1 corrections over v1. Neither document overrides actual security requirements or user instructions.

## Hard implementation rules
1. Before editing: report active branch, commit SHA, dirty files, affected call chain, existing tests, and relevant architecture contracts. Never silently discard user changes.
2. Work one small independent task/PR at a time. Follow dependency order and do not implement all 23 findings in a single change.
3. P0 first: R00 (`eager` Skill trust), R01 (reproducible baseline), R02 (trusted intent/negation), R03 (path scope), R04 (Broker zero bypass), R05 (restricted isolation).
4. Add a failing regression test when feasible, implement minimal change, run targeted tests and existing relevant regressions. Report exact commands/results and `not_run` explicitly for missing Docker/Temporal/credentials.
5. Preserve current public APIs and persisted data. Any schema/field change requires versioning, backfill, rollback notes, and old-client compatibility tests. Do not duplicate existing `security.access.ActionRequest`.
6. Any action on files, process, network, Git, DB, MCP, or Skill execution must preserve server-resolved identity, authorization, audit, and effect semantics. User or model metadata cannot grant authority. No silent host fallback from restricted mode.
7. Never treat a model assertion, skipped test, missing evidence, or UNKNOWN effect as passed or safe to retry. Verification/approval/apply must refer to the same pinned source, specification, and patch identity.
8. Avoid training, wholesale rewrites, speculative frameworks, and microservice proliferation. New dependencies require justification, pinned versions, licensing and security review.
9. Never use production credentials, contact external services, push, merge, deploy, or perform destructive migrations without explicit approval.
10. At the end of each task provide: file-level diff summary; tests run/passed/failed/not_run; remaining risks; compatibility impact; rollback method; next package and blockers. Stop after the scoped task.

## Development baseline
- Project uses Python >=3.12; `uv.lock`, pytest and ruff. Inspect the current `pyproject.toml` before choosing commands.
- Typical commands when supported by the environment: `uv sync --locked --group dev`, `uv run ruff check .`, `uv run pytest -q src/Sprout/tests`.
- Some real Docker tests require explicit images or infrastructure; do not count them as passed when skipped. Treat Local and Temporal as separate integration claims.
