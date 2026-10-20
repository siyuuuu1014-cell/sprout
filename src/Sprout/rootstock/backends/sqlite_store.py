"""SQLite session store: the default local-first backend (sprout_conversation.db).

The schema mirrors the ``sessions``/``turns`` tables of the operational store,
so an existing ``sprout_audit.db`` can be adopted directly by pointing the session
DSN at it. Turns are ordered by a per-session monotonic ``seq`` — never by
wall-clock time alone.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from Sprout.message.attachment import Attachment
from Sprout.rootstock.contract import BLOB_URI_ENVELOPE_KEY
from Sprout.session.models import Session, Turn
from Sprout.storage.local.sqlite.driver import SqliteDatabase, SqlitePragmas
from Sprout.storage.local.sqlite.turns import (
    ATTACHMENTS_SCHEMA,
    MESSAGE_TASK_LINK_SCHEMA,
    SESSIONS_SCHEMA,
    TURNS_SCHEMA,
    migrate_sessions_columns,
    migrate_turns_envelope,
    migrate_turns_message_columns,
    migrate_turns_seq,
    row_to_session,
    row_to_turn,
)

# NOTE: Sprout.memory.outbox reaches back into this package for the shared
# turns helpers, so it is imported lazily inside the methods below; a
# module-level import here would close an import cycle at load time.

#: ``trigram`` rather than ``unicode61``: unicode61 treats a whole CJK sentence
#: as a single token, so ``sprout session search`` could never match a Chinese
#: sub-phrase. trigram handles CJK and English alike. This store is the first
#: creator of ``turns_fts``, so it is the one that decides — an existing
#: unicode61 database keeps its tokenizer (``IF NOT EXISTS``) rather than being
#: silently rebuilt.
SCHEMA = (
    SESSIONS_SCHEMA
    + TURNS_SCHEMA
    + ATTACHMENTS_SCHEMA
    + MESSAGE_TASK_LINK_SCHEMA
    + """
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    session_id UNINDEXED, turn_id UNINDEXED, role UNINDEXED,
    seq UNINDEXED, body, tokenize='trigram'
);
"""
)

# Additive v1 domain table: no historical records are issued/backfilled.
CONSENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_consents_v1 (
    session_id TEXT NOT NULL,
    held_turn_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version=1),
    snapshot_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','claimed')),
    claimant TEXT,
    issued_at TEXT NOT NULL,
    claimed_at TEXT,
    PRIMARY KEY(session_id, held_turn_id),
    CHECK((state='pending' AND claimant IS NULL AND claimed_at IS NULL)
       OR (state='claimed' AND claimant IS NOT NULL AND claimed_at IS NOT NULL))
);
"""

# Separate domain: selecting a goal never grants workspace or tool authority.
# Additive schema; no old turns are adopted or issued clarification eligibility.
CLARIFICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS intent_clarifications_v1 (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    original_message_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version=1),
    snapshot_json TEXT NOT NULL,
    question TEXT NOT NULL,
    choices_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN
        ('pending','resolved','cancelled','superseded','expired')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    resolution TEXT,
    answer_message_id TEXT,
    answer_sha256 TEXT,
    resolved_at TEXT,
    UNIQUE(session_id, original_message_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS intent_clarification_pending_v1
    ON intent_clarifications_v1(session_id) WHERE state='pending';
CREATE UNIQUE INDEX IF NOT EXISTS intent_clarification_answer_message_v1
    ON intent_clarifications_v1(answer_message_id) WHERE answer_message_id IS NOT NULL;
-- Deny-only receipts survive deletion of private session bodies.
CREATE TABLE IF NOT EXISTS intent_clarification_receipts_v1 (
    message_id TEXT PRIMARY KEY,
    clarification_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS intent_clarification_receipt_guard_v1
BEFORE UPDATE OF answer_message_id ON intent_clarifications_v1
WHEN NEW.answer_message_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM intent_clarification_receipts_v1 WHERE message_id=NEW.answer_message_id
)
BEGIN
    SELECT RAISE(IGNORE);
END;
CREATE TRIGGER IF NOT EXISTS intent_clarification_receipt_record_v1
AFTER UPDATE OF answer_message_id ON intent_clarifications_v1
WHEN NEW.answer_message_id IS NOT NULL
BEGIN
    INSERT INTO intent_clarification_receipts_v1(message_id,clarification_id,claimed_at)
    VALUES(NEW.answer_message_id,NEW.id,NEW.resolved_at);
END;
"""

# A resolved write owns the hand-off until its exact frozen Workspace Consent
# is permanently claimed. Missing/unknown hand-off evidence remains blocking.
# Parameters: session ID twice. No current backend or caller metadata is proof.
_CLARIFICATION_OPERATION_PENDING = """
EXISTS (SELECT 1 FROM workspace_consents_v1 w WHERE w.session_id=? AND w.state='pending')
OR EXISTS (
    SELECT 1 FROM intent_clarifications_v1 c
    WHERE c.session_id=? AND c.state='resolved' AND c.resolution='write'
    AND NOT EXISTS (
        SELECT 1 FROM workspace_consents_v1 w
        WHERE w.session_id=c.session_id AND w.state='claimed'
        AND json_extract(w.snapshot_json,
            '$.envelope.message_metadata.intent_clarification_v1')=c.id
        AND json_extract(w.snapshot_json,'$.envelope.message_id')=c.answer_message_id
    )
)
"""


def _clarification_snapshot(session: Session, turn: Turn) -> str:
    from Sprout.session.manager import SessionManager

    principal = SessionManager.frozen_principal(session)
    if (not session.user_id or turn.session_id != session.id or turn.role != "user"
            or turn.metadata.get("user_id") != session.user_id
            or turn.metadata.get("channel") != {"mcp_client": "mcp"}.get(
                principal.source, principal.source)
            or turn.content_blob_uri or turn.metadata.get("blob_uri")):
        raise ValueError("Clarification authority snapshot unavailable")
    return json.dumps({
        "version": 1, "session_id": session.id, "owner": session.user_id,
        "principal": session.metadata["entry_principal_v1"], "turn_id": turn.id,
        "request": turn.content, "content_sha256": sha256(turn.content.encode()).hexdigest(),
        "envelope": turn.metadata,
    }, sort_keys=True, ensure_ascii=False)


def _consent_snapshot(session: Session, turn: Turn) -> str:
    """Only authority records, never message metadata, supply owner/source."""
    principal = session.metadata.get("entry_principal_v1")
    envelope = turn.metadata
    if (not isinstance(principal, dict)
            or set(principal) != {"user_id", "source", "authenticated"}
            or not session.user_id or principal.get("user_id") != session.user_id
            or not isinstance(principal.get("source"), str) or not principal["source"]
            or type(principal.get("authenticated")) is not bool
            or turn.session_id != session.id or turn.role != "user"
            or envelope.get("user_id") != session.user_id
            or envelope.get("channel") != {"mcp_client": "mcp"}.get(
                principal["source"], principal["source"])
            or envelope.get("message_metadata", {}).get("workspace_consent_required") is not True
            or envelope.get("blob_uri") or turn.content_blob_uri):
        raise ValueError("Consent authority snapshot unavailable")
    return json.dumps({"version": 1, "session_id": session.id, "held_turn_id": turn.id,
                       "owner": session.user_id, "principal": principal,
                       "content_sha256": sha256(turn.content.encode()).hexdigest(),
                       "envelope": envelope}, sort_keys=True, ensure_ascii=False)


def _row_to_turn(row) -> Turn:
    return row_to_turn(row)


class SqliteSessionStore:
    """Sessions and turns in one reserved SQLite database."""

    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db
        db.executescript(SCHEMA)
        migrate_turns_seq(db)
        migrate_turns_envelope(db)
        migrate_sessions_columns(db)
        migrate_turns_message_columns(db)
        from Sprout.memory.outbox import migrate_outbox

        migrate_outbox(db)
        db.executescript(CONSENT_SCHEMA)
        db.executescript(CLARIFICATION_SCHEMA)

    @property
    def database(self) -> SqliteDatabase:
        """The backing SQLite handle.

        The outbox worker drains the ``outbox`` table through this handle;
        exposing it keeps the fan-out wrapper from reaching into a private
        attribute.
        """
        return self._db

    def close(self) -> None:
        self._db.close()

    async def get_session(self, session_id: str) -> Session | None:
        row = await self._db.fetchone(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        if row is None:
            return None
        return row_to_session(row)

    async def save_session(self, session: Session) -> None:
        await self._db.execute(
            "INSERT INTO sessions (id, user_id, created_at, metadata_json, "
            "channel, target_ref_id, title, status, locale, summary, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET user_id = excluded.user_id, "
            "metadata_json = excluded.metadata_json, "
            "channel = excluded.channel, "
            "target_ref_id = excluded.target_ref_id, "
            "title = excluded.title, "
            "status = excluded.status, "
            "locale = excluded.locale, "
            "summary = excluded.summary, "
            "updated_at = excluded.updated_at",
            (
                session.id,
                session.user_id,
                session.created_at.isoformat(),
                json.dumps(session.metadata, ensure_ascii=False, default=str),
                session.channel,
                session.target_ref_id,
                session.title,
                session.status,
                session.locale,
                session.summary,
                session.updated_at.isoformat() if session.updated_at else None,
            ),
        )

    async def append_turn(self, turn: Turn) -> None:
        # The turn and its outbox events go in one transaction, so a crash can
        # never strand a turn without its derived-layer counterparts.
        from Sprout.memory.outbox import append_turn_with_outbox

        await append_turn_with_outbox(self._db, turn)

    async def _consent_authority(self, session: Session, turn: Turn):
        snapshot = _consent_snapshot(session, turn)
        row = await self._db.fetchone(
            "SELECT s.metadata_json AS session_json, t.metadata_json AS turn_json "
            "FROM sessions s JOIN turns t ON t.session_id=s.id "
            "WHERE s.id=? AND s.user_id=? AND t.id=? AND t.role='user' AND t.content=?",
            (session.id, session.user_id, turn.id, turn.content),
        )
        if row is None:
            return None
        live_session = Session(session.id, session.user_id,
                               metadata=json.loads(row["session_json"]))
        live_turn = Turn(session.id, "user", turn.content, id=turn.id,
                         metadata=json.loads(row["turn_json"]))
        if _consent_snapshot(live_session, live_turn) != snapshot:
            return None
        return snapshot, row["session_json"], row["turn_json"]

    async def issue_workspace_consent(self, session: Session, turn: Turn) -> bool:
        """Trusted server operation; never issue by scanning legacy history."""
        authority = await self._consent_authority(session, turn)
        if authority is None:
            return False
        snapshot, session_json, turn_json = authority
        changed = await self._db.execute(
            "INSERT OR IGNORE INTO workspace_consents_v1 "
            "(session_id,held_turn_id,version,snapshot_json,state,issued_at) "
            "SELECT ?,?,1,?,'pending',? WHERE EXISTS "
            "(SELECT 1 FROM sessions s JOIN turns t ON t.session_id=s.id "
            "WHERE s.id=? AND s.user_id=? AND s.metadata_json=? "
            "AND t.id=? AND t.role='user' AND t.content=? AND t.metadata_json=?)",
            (session.id, turn.id, snapshot, datetime.now(UTC).isoformat(),
             session.id, session.user_id, session_json, turn.id, turn.content, turn_json),
        )
        return changed == 1

    async def claim_workspace_consent(self, session: Session, turn: Turn, claimant: str) -> bool:
        """Single durable pending->claimed transition. No release/timeout/retry."""
        if not claimant:
            raise ValueError("Consent claimant required")
        authority = await self._consent_authority(session, turn)
        if authority is None:
            return False
        snapshot, session_json, turn_json = authority
        changed = await self._db.execute(
            "UPDATE workspace_consents_v1 SET state='claimed',claimant=?,claimed_at=? "
            "WHERE session_id=? AND held_turn_id=? AND version=1 "
            "AND state='pending' AND snapshot_json=? AND EXISTS "
            "(SELECT 1 FROM sessions s JOIN turns t ON t.session_id=s.id "
            "WHERE s.id=? AND s.user_id=? AND s.metadata_json=? "
            "AND t.id=? AND t.role='user' AND t.content=? AND t.metadata_json=?)",
            (claimant, datetime.now(UTC).isoformat(), session.id, turn.id, snapshot,
             session.id, session.user_id, session_json, turn.id, turn.content, turn_json),
        )
        return changed == 1

    async def _clarification_authority(self, session: Session, turn_id: str):
        row = await self._db.fetchone(
            "SELECT s.metadata_json AS session_json, t.* "
            "FROM sessions s JOIN turns t ON t.session_id=s.id "
            "WHERE s.id=? AND s.user_id=? AND t.id=?",
            (session.id, session.user_id, turn_id),
        )
        if row is None:
            return None
        live = Session(session.id, session.user_id, metadata=json.loads(row["session_json"]))
        if live.metadata.get("entry_principal_v1") != session.metadata.get("entry_principal_v1"):
            return None
        turn = row_to_turn(row)
        return _clarification_snapshot(live, turn), row["session_json"], row["metadata_json"]

    async def issue_intent_clarification(
        self, session: Session, turn: Turn, question: str, expires_at: str,
        *, choices: tuple[str, ...] = ("read", "write", "chat", "cancel"),
    ) -> bool:
        if (not choices or len(set(choices)) != len(choices)
                or not set(choices) <= {"read", "write", "chat", "cancel"}):
            raise ValueError("Unsupported clarification choices")
        snapshot = _clarification_snapshot(session, turn)
        authority = await self._clarification_authority(session, turn.id)
        if authority is None or authority[0] != snapshot:
            return False
        _, session_json, turn_json = authority
        changed = await self._db.execute(
            "INSERT OR IGNORE INTO intent_clarifications_v1 "
            "(id,session_id,original_message_id,version,snapshot_json,question,choices_json,"
            "state,created_at,expires_at) SELECT ?,?,?,1,?,?,?,'pending',?,? "
            "WHERE EXISTS (SELECT 1 FROM sessions s JOIN turns t ON t.session_id=s.id "
            "WHERE s.id=? AND s.user_id=? AND s.metadata_json=? AND t.id=? "
            "AND t.content=? AND t.metadata_json=?) AND NOT ("
            + _CLARIFICATION_OPERATION_PENDING + ")",
            (turn.id, session.id, turn.metadata["message_id"], snapshot, question,
             json.dumps(choices),
             datetime.now(UTC).isoformat(), expires_at, session.id, session.user_id,
             session_json, turn.id, turn.content, turn_json, session.id, session.id),
        )
        return changed == 1

    async def get_intent_clarification(
        self, session_id: str, record_id: str | None = None,
    ) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM intent_clarifications_v1 WHERE session_id=? "
            + ("AND id=?" if record_id is not None else "ORDER BY rowid DESC LIMIT 1"),
            (session_id, record_id) if record_id is not None else (session_id,),
        )
        return dict(row) if row is not None else None

    async def has_consumed_clarification_message(self, message_id: str) -> bool:
        return bool(await self._db.scalar(
            "SELECT EXISTS(SELECT 1 FROM intent_clarification_receipts_v1 WHERE message_id=? "
            "UNION ALL SELECT 1 FROM intent_clarifications_v1 WHERE answer_message_id=?)",
            (message_id, message_id),
        ))

    async def has_pending_clarification_operation(self, session_id: str) -> bool:
        return bool(await self._db.scalar(
            "SELECT " + _CLARIFICATION_OPERATION_PENDING, (session_id, session_id),
        ))

    async def resolve_intent_clarification(
        self, session: Session, record_id: str, message_id: str, answer: str, choice: str,
    ) -> bool:
        if choice not in {"read", "write", "chat", "cancel", "superseded", "expired"}:
            raise ValueError("Unknown clarification resolution")
        row = await self._db.fetchone(
            "SELECT * FROM intent_clarifications_v1 WHERE id=? AND session_id=?",
            (record_id, session.id),
        )
        if row is None or not message_id:
            return False
        authority = await self._clarification_authority(session, record_id)
        if authority is None or authority[0] != row["snapshot_json"]:
            return False
        snapshot, session_json, turn_json = authority
        state = {"cancel": "cancelled", "superseded": "superseded", "expired": "expired"}.get(
            choice, "resolved"
        )
        now = datetime.now(UTC).isoformat()
        changed = await self._db.execute(
            "UPDATE OR IGNORE intent_clarifications_v1 SET state=?,resolution=?,"
            "answer_message_id=?,"
            "answer_sha256=?,resolved_at=? WHERE id=? AND session_id=? AND state='pending' "
            "AND snapshot_json=? AND (expires_at>? OR ?='expired') AND EXISTS "
            "(SELECT 1 FROM sessions s JOIN turns t ON t.session_id=s.id "
            "WHERE s.id=? AND s.user_id=? AND s.metadata_json=? AND t.id=? "
            "AND t.content=? AND t.metadata_json=?) "
            "AND (? IN ('superseded','expired') OR EXISTS "
            "(SELECT 1 FROM json_each(choices_json) WHERE value=?)) "
            "AND (?!='write' OR NOT ("
            + _CLARIFICATION_OPERATION_PENDING + "))",
            (state, choice, message_id, sha256(answer.encode()).hexdigest(), now,
             record_id, session.id, snapshot, now, choice, session.id, session.user_id,
             session_json, record_id, json.loads(snapshot)["request"], turn_json,
             choice, choice, choice, session.id, session.id),
        )
        return changed == 1

    async def save_attachment(
        self,
        attachment: Attachment,
        *,
        message_id: str,
        kind: str = "",
        content_hash: str = "",
        scan_status: str = "unscanned",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist one §19.2 attachment row for ``message_id`` (a turn id)."""
        await self._db.execute(
            "INSERT INTO attachments (id, message_id, kind, filename, mime_type, "
            "blob_uri, size_bytes, content_hash, scan_status, metadata_json, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET message_id = excluded.message_id, "
            "kind = excluded.kind, filename = excluded.filename, "
            "mime_type = excluded.mime_type, blob_uri = excluded.blob_uri, "
            "size_bytes = excluded.size_bytes, content_hash = excluded.content_hash, "
            "scan_status = excluded.scan_status, "
            "metadata_json = excluded.metadata_json",
            (
                attachment.id,
                message_id,
                kind,
                attachment.filename,
                attachment.mime_type,
                attachment.uri,
                attachment.size,
                content_hash,
                scan_status,
                json.dumps(dict(metadata or {}), ensure_ascii=False, default=str),
                datetime.now(UTC).isoformat(),
            ),
        )

    async def attachments_for_message(self, message_id: str) -> list[dict[str, Any]]:
        """Return the §19.2 attachment rows bound to one message (turn id)."""
        rows = await self._db.fetchall(
            "SELECT * FROM attachments WHERE message_id = ? ORDER BY created_at",
            (message_id,),
        )
        return [dict(row) for row in rows]

    async def link_message_task(
        self,
        message_id: str,
        task_id: str,
        *,
        relation: str = "created",
        confidence: float = 1.0,
    ) -> None:
        """Record one §19.2 message↔task edge (idempotent per relation)."""
        await self._db.execute(
            "INSERT INTO message_task_link (message_id, task_id, relation, "
            "confidence, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(message_id, task_id, relation) DO UPDATE SET "
            "confidence = excluded.confidence",
            (message_id, task_id, relation, confidence, datetime.now(UTC).isoformat()),
        )

    async def tasks_for_message(self, message_id: str) -> list[dict[str, Any]]:
        """Return the §19.2 message↔task edges for one message (turn id)."""
        rows = await self._db.fetchall(
            "SELECT * FROM message_task_link WHERE message_id = ? ORDER BY created_at",
            (message_id,),
        )
        return [dict(row) for row in rows]

    async def recent_turns(self, session_id: str, limit: int = 20) -> list[Turn]:
        rows = await self._db.fetchall(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY seq DESC LIMIT ?",
            (session_id, limit),
        )
        turns = [_row_to_turn(row) for row in rows]
        turns.reverse()
        return turns

    async def turns_before(
        self, session_id: str, seq: int, limit: int = 100
    ) -> list[Turn]:
        """Up to ``limit`` turns with ``seq`` below ``seq``, oldest first (keyset)."""
        rows = await self._db.fetchall(
            "SELECT * FROM turns WHERE session_id = ? AND seq < ? "
            "ORDER BY seq DESC LIMIT ?",
            (session_id, seq, limit),
        )
        turns = [_row_to_turn(row) for row in rows]
        turns.reverse()
        return turns

    async def delete_session(self, session_id: str) -> int:
        """Delete a session, its turns, and the per-turn derived rows.

        ``attachments.message_id`` and ``message_task_link.message_id`` both hold
        a turn id (§19.2), so both are keyed off this session's turns. Leaving
        them behind orphaned every attachment and task edge whose session had
        been removed.
        """
        before = int(await self._db.scalar(
            "SELECT COUNT(*) FROM turns WHERE session_id = ?", (session_id,)
        ) or 0)
        await self._db.execute_batch(
            [
                ("INSERT OR IGNORE INTO intent_clarification_receipts_v1 "
                 "(message_id,clarification_id,claimed_at) "
                 "SELECT answer_message_id,id,COALESCE(resolved_at,created_at) "
                 "FROM intent_clarifications_v1 "
                 "WHERE session_id=? AND answer_message_id IS NOT NULL", (session_id,)),
                ("DELETE FROM intent_clarifications_v1 WHERE session_id = ?", (session_id,)),
                (
                    "DELETE FROM attachments WHERE message_id IN "
                    "(SELECT id FROM turns WHERE session_id = ?)",
                    (session_id,),
                ),
                (
                    "DELETE FROM message_task_link WHERE message_id IN "
                    "(SELECT id FROM turns WHERE session_id = ?)",
                    (session_id,),
                ),
                ("DELETE FROM turns WHERE session_id = ?", (session_id,)),
                ("DELETE FROM sessions WHERE id = ?", (session_id,)),
            ]
        )
        return before

    async def session_blob_uris(self, session_id: str) -> list[str]:
        """Blob URIs referenced by this session's turns, for cascade cleanup.

        Offloaded bodies are content-addressed — the URI is a bare hash file
        name with no session component — so they cannot be found by prefix. The
        turns are the only index, and a URI can sit in either of two places: the
        ``content_blob_uri`` column, or the ``blob_uri`` envelope key that
        ``Runtime._offload_body`` actually writes. Both are read here; selecting
        only the column returned nothing for every body the runtime offloaded.
        """
        rows = await self._db.fetchall(
            "SELECT content_blob_uri, metadata_json FROM turns WHERE session_id = ?",
            (session_id,),
        )
        found: dict[str, None] = {}
        for row in rows:
            column = row["content_blob_uri"]
            if isinstance(column, str) and column:
                found.setdefault(column, None)
            envelope = _envelope_blob_uri(row["metadata_json"])
            if envelope:
                found.setdefault(envelope, None)
        return list(found)

    async def count_sessions(self) -> int:
        return int(await self._db.scalar("SELECT COUNT(*) FROM sessions") or 0)

    async def count_turns(self) -> int:
        return int(await self._db.scalar("SELECT COUNT(*) FROM turns") or 0)


def _envelope_blob_uri(raw_metadata: Any) -> str:
    """The ``blob_uri`` envelope key of a stored ``metadata_json`` column.

    Read straight from JSON rather than through ``row_to_turn`` so the cascade
    can select just the two columns it needs; a malformed envelope yields "",
    which reads as "this row references no blob" — the same answer the read side
    gives for an unresolvable reference.
    """
    if not raw_metadata:
        return ""
    try:
        envelope = json.loads(raw_metadata)
    except (TypeError, ValueError):
        return ""
    if not isinstance(envelope, dict):
        return ""
    uri = envelope.get(BLOB_URI_ENVELOPE_KEY)
    return uri if isinstance(uri, str) else ""


def open_session_store(
    path: str, pragmas: SqlitePragmas | None = None
) -> SqliteSessionStore:
    """Open (and reserve) a sprout_conversation.db at ``path``, creating the schema if needed."""
    return SqliteSessionStore(SqliteDatabase.open(path, pragmas))
