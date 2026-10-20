# R03 local implementation review

Baseline: `766a0b48e6709e75b59713384e7b56fa11cbbc7b`.
Branch: `codex/r03-scope-path-authorization`. No commit/push/merge authorized.

## Contracts and implementation

- `task/paths.py` provides lexical component matching without filesystem or DNS access. POSIX is case-sensitive; Windows drive/UNC families use Windows lexical case normalization. Different anchors never overlap. Device namespaces, ambiguous drive-relative forms, invalid Windows components and traversal above the explicit base fail closed.
- `DelegationScope` preserves empty allowed collections as unrestricted. An unsatisfiable intersection uses the existing `denied_paths=("",)` deny-all representation, including pathless actions. Denied rules win; expiry and delegation depth cannot be widened. Relative grants are bound only to FileBroker's explicit sandbox root, not process CWD.
- Task SQLite Scope JSON now records version 1, expiry, depth and issuer in the existing JSON column. There is no SQL schema change. Legacy four-collection records remain readable; malformed/unknown versions deny all. Already-lost historical expiry cannot be recovered and is not invented or backfilled.
- FileBroker keeps its public methods and policy/approval/event contracts. `exists` now requires read authorization. Final authorization is repeated while resource ancestors are pinned and before effects. A prior ALLOW decision is not treated as human approval if policy changes to REQUIRE_APPROVAL.
- `execution/file_access.py` is a private I/O primitive, not an alternate Broker. Windows pins local-volume ancestors with non-write/non-delete sharing, refuses reparse/case-sensitive directories and short-name aliases, validates leaf handles before truncation, and deletes through a handle. UNC/subst/unproved volumes fail closed. POSIX walks no-follow directory descriptors and uses relative no-follow leaf operations; unavailable primitives fail closed. Neither path falls back to ordinary pathname I/O.

## Evidence and platform limits

External evidence root:
`C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/`.

- `r03-initial-red`: 18 passed, 25 failed, Python exit 1. Component-prefix/action intersection escalation, exists bypass and temporary junction substitution reproduced.
- `r03-persistence-and-approval-red`: 43 passed, 2 failed, Python exit 1. Scope expiry/depth loss and changed final approval decision reproduced.
- `r03-native-local-regression`: 57 passed, Python exit 0.
- `r03-expanded-local`: 225 passed, 1 skipped, Python exit 0. This is intermediate evidence, not the final snapshot result.
- Final verification is reported separately with command, JUnit, raw exit code and before/after source manifests; do not infer unexecuted platforms passed.

Codex's host sandbox denied metadata handles on the user-directory ancestor, causing eight intermediate positive tests to fail. Reverification used approved out-of-sandbox execution while retaining the offline Python guard, isolated HOME, temporary databases and temporary test files. No product check was disabled. The Python guard is not an OS security sandbox.

Native POSIX execution, real external integrations, Docker/Temporal and paid models are not verified by local Windows runs. Windows junction tests exercise temporary directories only. Local execution is not a hostile-code sandbox: privileged mount/volume manipulation and arbitrary native code require stronger isolation. Hardlinked leaves are refused; this does not establish a general hostile-filesystem guarantee. Directory creation after authorization may remain partial if later I/O fails; no transactional rollback/exactly-once claim is made.

## Compatibility and safe rollback

R02 routing/identity/consent modules are untouched. Normal unlinked local files retain their API and return types. Previously accepted ambiguous paths, links, shared/hardlinked files, unproved volume types and unauthorized existence probes now refuse. Existing approvals whose resource spelling differs from canonical Windows paths may require fresh approval; no old approval is rewritten.

No real user database was opened or migrated. Only isolated/memory SQLite tests are permitted. JSON version 1 needs the safe reader/writer on rollback: do not deploy an old writer that drops expiry/depth or restore prefix-based authorization. If rollback is needed, disable affected file/delegation operations or backport these checks, preserve stored evidence, and require fresh scopes where historical authority cannot be proved. No data deletion/backfill is proposed.

Review remains local; Ubuntu native resource-open verification is pending before any cross-platform acceptance claim.

## First review corrections

Invalid allowed path entries are retained during intersection so `permits()`
continues to fail closed; they are never silently dropped in favour of a valid
overlap. A representative Cartesian property regression checks that every grant
of a narrowed scope is a grant of both operands, including invalid restrictions,
denials, unrestricted collections, action conflicts, expiry and depth budgets.

Authorization is now checked before each directory creation, before a leaf open
that could create a file, and again after leaf inspection before read/truncate
or deletion. Pinned ancestors and no-follow semantics remain unchanged. An
already-authorized intermediate directory can remain after later revocation;
this is not permission to create the next directory or target file.

`r03-review-red` records 7 failed, 76 passed, 2 native POSIX skips (Python 1).
`r03-review-green` records 86 passed, 5 native POSIX skips (Python 0); its first
Ruff invocation found one overlong new test declaration, subsequently wrapped.
The final combined run is separately retained as `r03-review-final-local` with
source-before/source-after SHA-256 manifests. Native POSIX tests are not counted
as passed on Windows. No production database, dependency or Git history changed.
