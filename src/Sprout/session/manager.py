"""Session resolution and creation against the session store (Rootstock)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from Sprout.gateway.identity import Principal
from Sprout.message.models import Message
from Sprout.session.models import Session

if TYPE_CHECKING:  # pragma: no cover - annotation only
    # Imported for typing alone: ``SessionStore`` is a Protocol used only in the
    # constructor annotation below, and this module uses PEP 563 annotations, so
    # it is never needed at runtime. A runtime import here closes a cycle —
    # ``rootstock.contract`` imports ``Sprout.session.models``, which runs
    # ``session/__init__.py``, which imports this module, which would then ask
    # ``rootstock.contract`` for a name it has not bound yet. Importing
    # ``Sprout.storage`` on its own used to fail for exactly that reason.
    from Sprout.rootstock.contract import SessionStore


class SessionManager:
    def __init__(self, store: SessionStore) -> None:
        self._store = store

    @staticmethod
    def frozen_principal(session: Session) -> Principal:
        """Only new server-written v1 evidence supports automatic recovery.

        No legacy ownership/source fields are backfilled or promoted.
        """
        evidence = session.metadata.get("entry_principal_v1")
        if (
            not isinstance(evidence, dict)
            or set(evidence) != {"user_id", "source", "authenticated"}
            or evidence.get("user_id") != session.user_id
            or not isinstance(evidence.get("source"), str) or not evidence["source"]
            or type(evidence.get("authenticated")) is not bool
        ):
            raise PermissionError("Session recovery identity unavailable")
        return Principal(**evidence)

    async def require_owner(self, session_id: str, principal: Principal | None) -> Session:
        """Check server-supplied identity before exposing an existing session.

        The embedding application is responsible for resolving ``principal``;
        message fields and transport names are not credentials.
        """
        session = await self._store.get_session(session_id)
        if (
            session is None
            or principal is None
            or not principal.user_id
            or not principal.source
            or not session.user_id
            or session.user_id != principal.user_id
        ):
            raise PermissionError("Session unavailable")
        return session

    async def resolve(self, message: Message, *, principal: Principal | None = None) -> Session:
        """Return the session referenced by the message, creating it if needed."""
        session_id = message.session_id or str(uuid4())
        session = await self._store.get_session(session_id)
        if session is not None:
            return await self.require_owner(session_id, principal)
        if session is None:
            # Unauthenticated embedded callers may start a new conversation,
            # but cannot later prove ownership merely by repeating its label.
            session = Session(
                id=session_id, user_id=principal.user_id if principal else "",
                metadata={"entry_principal_v1": {
                    "user_id": principal.user_id, "source": principal.source,
                    "authenticated": principal.authenticated,
                }} if principal and principal.user_id and principal.source else {},
            )
            await self._store.save_session(session)
        return session

    async def create(self, user_id: str, *, session_id: str | None = None) -> Session:
        session = Session(id=session_id or str(uuid4()), user_id=user_id)
        await self._store.save_session(session)
        return session
