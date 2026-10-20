"""Web API authentication tests (AUTHZ §2.1).

The regression: every route — including approval decisions and proposal apply —
was reachable with no credential at all. These tests pin the two halves of the
fix: the middleware refuses unauthenticated requests when auth is on, and the
identity the handlers use comes from the server, never from the request body.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.datastructures import Headers
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from Sprout.config.defaults import default_settings
from web.webapi.app import create_app
from web.webapi.auth import WebAuth, caller_user_id
from web.webapi.database import WebDatabase

_FRONTEND = Path(__file__).resolve().parents[2] / "web" / "frontend"

TOKEN = "s3cret-token"
TOKEN_ENV = "SPROUT_WEB_TOKEN"


@pytest.fixture
def database(tmp_path) -> WebDatabase:
    db = WebDatabase(tmp_path / "webapi.db")
    db.initialize()
    yield db
    db.close()


def _settings(*, auth_enabled: bool):
    settings = default_settings()
    settings.security.web_auth_enabled = auth_enabled
    return settings


def _client(runtime, database, *, auth_enabled: bool) -> TestClient:
    static_dir = _FRONTEND if (_FRONTEND / "index.html").exists() else None
    app = create_app(
        runtime,
        settings=_settings(auth_enabled=auth_enabled),
        database=database,
        static_dir=static_dir,
    )
    return TestClient(app)


def _auth_headers(user_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if user_id is not None:
        headers["X-Sprout-User"] = user_id
    return headers


# -- the switch ----------------------------------------------------------------


def test_auth_disabled_keeps_the_loopback_default_working(runtime, database) -> None:
    with _client(runtime, database, auth_enabled=False) as client:
        assert client.get("/api/tasks").status_code == 200


def test_auth_enabled_without_a_token_fails_closed(
    runtime, database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turning authentication on without a token must not remove it."""
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    with _client(runtime, database, auth_enabled=True) as client:
        response = client.get("/api/tasks")
    assert response.status_code == 503
    assert TOKEN_ENV in response.json()["error"]


def test_auth_enabled_requires_a_bearer_token(
    runtime, database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    with _client(runtime, database, auth_enabled=True) as client:
        assert client.get("/api/tasks").status_code == 401
        assert client.get(
            "/api/tasks", headers={"Authorization": "Bearer wrong"}
        ).status_code == 401
        assert client.get(
            "/api/tasks", headers={"Authorization": TOKEN}
        ).status_code == 401  # the scheme is part of the credential
        assert client.get("/api/tasks", headers=_auth_headers()).status_code == 200


def test_health_stays_reachable_without_a_token(
    runtime, database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    with _client(runtime, database, auth_enabled=True) as client:
        assert client.get("/api/health").status_code == 200


def test_websocket_is_guarded_too(runtime, database, monkeypatch: pytest.MonkeyPatch) -> None:
    """``/ws/chat`` is not an HTTP route, so it needs its own coverage."""
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    with _client(runtime, database, auth_enabled=True) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/chat") as ws:
                ws.receive_json()


# -- masking -------------------------------------------------------------------


def test_settings_endpoint_masks_credentials(
    runtime, database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/api/settings`` used to hand out DSN passwords verbatim."""
    settings = _settings(auth_enabled=False)
    settings.storage.audit = "neo4j://neo4j:hunter2@graph.internal:7687"
    static_dir = _FRONTEND if (_FRONTEND / "index.html").exists() else None
    app = create_app(runtime, settings=settings, database=database, static_dir=static_dir)
    with TestClient(app) as client:
        body = client.get("/api/settings").json()
    assert "hunter2" not in str(body)
    masked = body["settings"]["storage"]["audit"]
    assert "[REDACTED]" in masked
    # The redactor masks the whole ``scheme://user:pass@`` run, so the host and
    # port are what prove the value is still readable as a DSN rather than gone.
    assert "graph.internal:7687" in masked


# -- identity comes from the server --------------------------------------------


class _FakeRequest:
    def __init__(self, principal) -> None:
        self.state = type("S", (), {"principal": principal})()


def test_caller_user_id_prefers_the_authenticated_principal() -> None:
    from Sprout.gateway.identity import Principal

    authenticated = Principal(user_id="operator", authenticated=True, source="web")
    anonymous = Principal(user_id="web-user", authenticated=False, source="web")

    assert caller_user_id(_FakeRequest(authenticated), "victim") == "operator"
    # R02 intentionally removes body-selected identities in local mode too.
    assert caller_user_id(_FakeRequest(anonymous), "someone") == "web-user"


def test_session_ownership_is_enforced_for_authenticated_callers(
    runtime, database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    with _client(runtime, database, auth_enabled=True) as client:
        created = client.post(
            "/api/chat",
            json={"message": "hi", "user_id": "attacker"},
            headers=_auth_headers(),
        )
        assert created.status_code == 200
        session_id = created.json()["session_id"]

        # Legacy label switching is explicitly rejected, not an isolation claim.
        assert client.get(
            f"/api/sessions/{session_id}/history", headers=_auth_headers("attacker")
        ).status_code == 400
        assert client.get(
            f"/api/sessions/{session_id}/history", headers=_auth_headers()
        ).status_code == 200


def test_web_auth_reports_its_own_state() -> None:
    auth = WebAuth(enabled=True, token=TOKEN, token_env=TOKEN_ENV)
    assert auth.rejection(Headers({"authorization": f"Bearer {TOKEN}"})) is None
    assert auth.rejection(Headers({})) is not None
    assert auth.is_exempt("/api/health")
    assert not auth.is_exempt("/api/approvals")


def test_web_auth_without_a_token_rejects_with_service_unavailable() -> None:
    auth = WebAuth(enabled=True, token="", token_env=TOKEN_ENV)
    rejection = auth.rejection(Headers({"authorization": "Bearer anything"}))
    assert rejection is not None
    assert rejection[0] == 503
    assert TOKEN_ENV in rejection[1]


@pytest.mark.parametrize("label", ["alice", "bob", ""])
def test_shared_token_rejects_identity_selection(label: str) -> None:
    auth = WebAuth(enabled=True, token=TOKEN)
    headers = Headers({"authorization": f"Bearer {TOKEN}", "X-Sprout-User": label})
    rejection = auth.rejection(headers)
    assert rejection is not None
    assert rejection[0] == 400
    assert TOKEN not in rejection[1]
    # Even an internal direct call cannot turn the label into an identity.
    principal = auth.principal_for(headers)
    assert principal.user_id == "web-user"
    assert principal.roles == ()
    assert principal.authenticated


def test_unauthenticated_identity_ignores_body_fallback() -> None:
    assert caller_user_id(_FakeRequest(None), "alice") == "web-user"
    assert caller_user_id(_FakeRequest(WebAuth().anonymous_principal()), "bob") == "web-user"


def test_storage_status_masks_dsns_too(
    runtime, database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/api/storage/status`` must mask what ``/api/settings`` masks.

    Both endpoints return the same ``storage.graph`` value, but only the
    settings route ran it through the redactor — so the console showed
    ``[REDACTED]`` on one screen and the live password on another. Masking that
    depends on which endpoint you ask is not masking.
    """
    settings = _settings(auth_enabled=False)
    settings.storage.graph = "neo4j://neo4j:hunter2@graph.internal:7687"
    static_dir = _FRONTEND if (_FRONTEND / "index.html").exists() else None
    app = create_app(runtime, settings=settings, database=database, static_dir=static_dir)

    with TestClient(app) as client:
        body = client.get("/api/storage/status").json()

    assert "hunter2" not in str(body), "the DSN password was served by /api/storage/status"
    masked = body["lanes"]["graph"]
    assert "[REDACTED]" in masked
    assert "graph.internal:7687" in masked
