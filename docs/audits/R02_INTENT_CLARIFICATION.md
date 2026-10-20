# R02 enhancement: durable active intent clarification

Subsequent semantic-analysis extension is documented in
`R02_SEMANTIC_INTENT_ANALYSIS.md`. The earlier exact-only/pre-context/model-free
descriptions below are historical scopes, not a claim about the later bounded
semantic classifier or natural-language answer paths.

Baseline: `dev@1680cac6bbcb3a0df1c6e9ebc2aa2f7d50d93c75`.
Feature branch: `codex/r02-intent-clarification`. Local only; no commit/push/deployment.

## Architecture and invariants

- Preserve the eight existing Intent values and canonical events. The historical
  architecture's seven-value table is not the current authority.
- `intents.py` adds conservative, deterministic inspect/modify ambiguity detection
  on top of `write_intent_disposition`; not every UNKNOWN/low-confidence input is
  intercepted. No LLM classification result grants write authority.
- Both `handle` and `handle_stream` call the same pre-context/pre-agent router,
  after the existing SessionManager owner check. Pending questions do not build
  agent context, call a model/tool, open a workspace, create a Task, issue Workspace
  Consent, or publish `INTENT_TASK_REQUESTED`.
- Only exact text choices resolve a question. Generic assent repeats it. A new
  topic supersedes it and is routed independently. Client metadata is not a
  confirmation channel; reserved clarification/confirmed fields are removed.
- Read selection uses a model-only, tool-free analysis call on the supplied
  original request. It does not inspect repository files or claim independent
  project verification. Model failure stays nonwrite; unsolicited calls are not
  executed. Chat/cancel selections have deterministic non-executing replies.
- Write selection is **not authorization**: it only asks the existing independent
  Workspace Consent question, including the original scope. The real answer is
  persisted once, not replaced with fabricated user wording. Consent recovery
  checks the server-side resolved clarification, response identity/content digest,
  and frozen principal before recovering `/task <original request>` and consuming
  the existing durable Workspace Consent. Existing verification/Apply remains.

## Persistence v1 and failure semantics

SQLite SessionStore creates additive `intent_clarifications_v1` plus a partial
unique pending-per-session index. Fields include version, original Turn ID and
Message ID, exact original request and SHA-256, owner/frozen principal/envelope,
question/choices, created/expires times, state, selected resolution, answer ID and
SHA-256, and resolution time. TTL is 30 minutes, v1 server-defined.

States:

`pending -> resolved(read/write/chat) | cancelled | superseded | expired`

The Session and original user/assistant Turns are saved before issuance. Issuance
and consumption verify the actual persisted Session/Turn snapshot. No history
scan issues eligibility. A single conditional SQLite UPDATE, with live-authority
EXISTS predicates, claims pending state across independent connections. Claimed
records are never reset, released on timeout, or inferred to represent execution.

- Crash after Turns but before issuance: no eligible record; fail closed.
- Crash before a known failed claim: pending remains; no downstream effects.
- Claim exception/unknown outcome: no downstream effects in that attempt.
- Crash after resolution but before persistence/Consent issuance: consumed; do not
  automatically replay. The user must send a fresh complete instruction.
- Separate Consent claim and Task failure semantics remain those of R02.
- Ordinary session deletion removes associated clarification bodies in the same
  transaction as existing turn cleanup.

This is **at-most-once durable resolution**, not end-to-end exactly-once execution.

## Backend, API and transport compatibility

The optional `IntentClarificationStore` capability does not alter required
SessionStore methods. Memory/JSONL/reserved backends retain ordinary conversations
but explicitly refuse durable clarification recovery; no process-local fallback.
Old owner-known sessions without a frozen Principal are not backfilled/adopted.

`Runtime.create_session` adds an optional trusted `principal` keyword; old callers
remain valid. MCP Server's session creation now passes its existing fixed server
Principal for **new** sessions. No remote identity/roles parameter is added.
MCP clients should create a session first, then reuse its returned ID in
`sprout_message`; the old string response API is unchanged. Legacy MCPGateway
without a trusted Principal remains degraded/fail-closed. SDKGateway has no chat
API; trusted embedders use Runtime or RuntimeGateway. CLI/Web/MCP conversation
adapters ultimately reach the same Runtime gate. HTTP responses remain text plus
Session ID; SSE/WebSocket retain existing metadata/stream contracts. No new UI,
new public event, or high-level agent framework is introduced.

## Schema operations and rollback

Only isolated temporary SQLite files were used for local testing. No user database
was opened or migrated. Future startup on a real SQLite SessionStore will create
the new table/index automatically: this is a deployment/schema operation requiring
separate approval, a consistent backup, and old-reader validation. No historical
data is backfilled or rewritten. Existing Workspace Consent schema is unchanged.

Rollback must retain all R02 identity, intent, Consent, verification and Apply
guards. Stop accepting answers to feature-version questions before rollback;
retain the additive table/records for audit, never convert resolved records into
fresh pending work or replay their requests. An older binary ignores the additive
table. Do not drop or migrate production tables as an automatic rollback action.

## Validation and limitations

Initial red suite: **15 failed / 6 passed**, no errors/skips. Initial green: 21
passed; expanded suite: 44 passed. Original 625 R02 tests plus initial 44 new
tests: **669 passed**. The first expanded entry check exposed a new test's wrong
assumption about legacy MCPGateway's signature (1 failed / 53 passed); it was
corrected to exercise the actual degraded contract, not by expanding authority.
Actual fixed-principal MCP Server recovery is tested separately. A subsequent
compatibility group had **151 passed** (overlaps these groups; do not add totals).

Final consolidated results and commands are retained outside the repository under
`%TEMP%/sprout-r02-local-1c0815af/clarification-final-3/`; the final user report records
the exact results. Existing project `.venv` Python 3.13.9 is used, without installs.
An intermediate 891-case run had 890 passed / 1 failed: the newly added Chinese
inspect-or-modify alternative was misread by the legacy narrative predicate
(`还是` as `还/是`). The clarification helper normalizes that choice connector
before reusing the existing nonwrite guard; no old assertion or write-admission
rule was relaxed. That failure log remains in `clarification-final-2/`.
All pytest files/SQLite/temporary HOME/logs/JUnit stay in that isolated directory.
The reviewed temporary network guard permits only its owned socketpairs; no real
services, paid models, Docker, Temporal, deployment, or coding execution is used.
It is test hygiene, **not an OS sandbox**. No remote CI or full L1 claim is made.

Detection and response understanding deliberately use bounded Chinese/English
rules, not a general natural-language parser; unfamiliar expressions retain the
existing safe R02 routing. Analysis has no repository-reading tools. UI choice
buttons, arbitrary clarification scopes, and additional persistent backends are
not implemented. Crashes may leave safely unclaimable original/answer Turns;
automatic resume of partially resolved work is intentionally not implemented.

## Review fix: short replies without a live pending question

The original router incorrectly intercepted all exact clarification vocabulary
even when no pending question existed. This review fixes only that routing layer;
Principal/Session ownership, SQLite claim predicates, and Consent are unchanged.

- Only a live pending question interprets ordinary short replies as selections.
  No record, resolved/cancelled/superseded records, and retired expired records
  leave ordinary assent, cancellation and read-only words to the existing router.
- Expired pending records are atomically marked expired without treating the new
  message as a read/write selection; ordinary chat then continues normally.
- Exact already-resolved answer Message IDs remain non-replayable and do not add
  duplicate Turns. A fresh short reply cannot consume terminal state.
- Object-less write phrases (for example `直接修改` and `fix it`) have a separate
  scope-required response, even with a bound workspace or a model task signal.
  This is not a clarification answer or an authorization. Write admission rules
  in `intents.py` were not changed for this review.
- Model-only read selection now visibly discloses that it is based on request
  text and **has not read or inspected actual repository files**. Response
  metadata includes `analysis_scope=request_text_only` and
  `repository_inspected=false`. The existing tool-call rejection test checks the
  complete prefixed response, preserving its original model-body assertion.

New regressions cover six short replies across seven states, ordinary and stream
paths, plus valid pending questions, exact replay, misleading model signals,
bound workspaces, disclosure, and unsupported-memory-backend ordinary chat.
The initial 104-case red run recorded 95 failed / 9 passed, exit 1; the first
green combined run recorded 167 passed / 0 failed/errors/skipped, exit 0.
Final consolidated evidence is outside Git at
`%TEMP%/sprout-r02-local-1c0815af/clarification-short-replies-final/`.
These are local offline test claims, not remote CI/full-L1/deployment approval.

## Review fix: cross-record replay and Workspace Consent hand-off

The cross-record red run reproduced all ten initial probes (10 failed, exit 1):
an old answer could resolve a newer question, two independent SQLite connections
could consume one answer ID for different records, and a newer question could
hide the original Consent proof. Evidence is retained outside Git under
`%TEMP%/sprout-r02-local-1c0815af/clarification-cross-record-red/`.
A further temporary-session-deletion probe failed (10 passed, 1 failed, exit 1)
under `clarification-cross-record-retention-red/`.

The SQLite authority now enforces answer Message ID uniqueness across records
and Sessions in the same database. A minimal v1 receipt table and SQLite
triggers commit the deny-only receipt together with the conditional resolution
UPDATE; losing concurrent calls return false. Receipts retain only message ID,
clarification ID and time, survive session-body deletion, and confer no authority.
Existing terminal feature records remain deny evidence; deleting such a session
preserves minimal receipts in the existing deletion transaction. No real user
database was opened or migrated. Duplicate consumed IDs in an older experimental
database prevent unique-index initialization rather than silently deduplicating
or adopting ambiguous evidence. Deployment requires separate schema review.

A pending Workspace Consent or an unresolved write-selection hand-off blocks
issuance of a conflicting clarification in that Session, both in routing and
in the authoritative INSERT predicate. Write resolution has the same SQL guard.
The user is told to finish the original confirmation or start a new Session.
Missing hand-off evidence remains blocking; no automatic retry or timeout release
is introduced. A permanently claimed, exactly linked Consent releases this
hand-off, not proof of Task execution success. Consent recovery fetches its
frozen clarification ID, never whichever record happens to be latest.

This is at-most-once durable answer consumption within one SQLite authority,
not end-to-end exactly-once execution or cross-database replay protection.
Principal, Session ownership, Task/Apply approvals and Consent claims are not
relaxed. Rollback must preserve receipts and fail-closed unresolved operations;
reverting to the older latest-record-only router is not a safe rollback.
The combined clarification green run recorded 184 passed, no failures/errors/
skips, Python exit 0 (`clarification-cross-record-green-2/`). This is not full L1.
