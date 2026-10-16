"""Existing-database CLI decision adapter; no Runtime or schema initialization."""

import sqlite3
from pathlib import Path

from Sprout.runtime.experiment_commits import decide_commit_request_for_principal
from Sprout.storage.local.sqlite.commit_request import decide_request, get_request
from Sprout.storage.local.sqlite.commit_status import _regular, commit_status_report


class _DecisionStore:
    def __init__(self, connection):
        self.connection = connection

    async def fetchone(self, sql, parameters):
        return self.connection.execute(sql, parameters).fetchone()

    async def fetchall(self, sql, parameters):
        return self.connection.execute(sql, parameters).fetchall()

    async def execute(self, sql, parameters):
        changed = self.connection.execute(sql, parameters).rowcount
        self.connection.commit()
        return changed

    async def execute_batch(self, statements):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for sql, parameters in statements:
                self.connection.execute(sql, parameters)
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    async def get_commit_request(self, request_id):
        return await get_request(self, request_id)

    async def decide_commit_request(self, request_id, **decision):
        return await decide_request(self, request_id, **decision)

    async def get_commit_dispatch(self, request_id):
        from Sprout.storage.local.sqlite.commit_request import get_dispatch

        return await get_dispatch(self, request_id)

    async def ensure_commit_dispatch(self, record):
        from Sprout.storage.local.sqlite.commit_request import ensure_dispatch

        return await ensure_dispatch(self, record)

    async def mark_commit_dispatch(self, request_id, state, run_id=None):
        from Sprout.storage.local.sqlite.commit_request import mark_dispatch

        return await mark_dispatch(self, request_id, state, run_id)


async def save_cli_decision(database, task_id, request_id, *, principal,
                            expected_fingerprint, approved, reason=""):
    checked = commit_status_report(database, task_id, principal=principal,
                                   request_id=request_id, include_review=True)
    if checked["requests"][0]["decision_fingerprint"] != expected_fingerprint:
        raise ValueError("Displayed commit request changed; review again")
    path = _regular(Path(database).parent / "operational.db")
    # mode=rw refuses missing files; never create a database or run migrations.
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise ValueError("Existing decision database cannot be opened") from exc
    connection.row_factory = sqlite3.Row
    try:
        return await decide_commit_request_for_principal(_DecisionStore(connection), request_id,
            principal=principal, expected_fingerprint=expected_fingerprint,
            approved=approved, reason=reason)
    except sqlite3.Error as exc:
        raise ValueError("Commit decision storage unavailable; no local execution") from exc
    finally:
        connection.close()
