"""Zero-Trust REST / WebSocket authentication tests (ADR-0004).

Covers:
- Every ``/api/*`` route except pairing requires a valid session token.
- ``Authorization: Bearer`` and legacy ``X-Session-Token`` headers are accepted.
- Revoked sessions get 401 on REST and their live WebSockets are closed with 4401.
- ``/api/wan/toggle`` ignores the client-supplied port.
- OpenAPI docs are disabled outside debug mode; non-JSON WS input gets an error envelope.
"""

from __future__ import annotations

import time
from typing import Generator

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.tunnel_adapter import MockTunnelAdapter
from riftpilot.adapters.web_hub import WS_CLOSE_UNAUTHORIZED, create_app
from riftpilot.core.pairing import OTTManager, SessionManager
from riftpilot.core.transport import TunnelInfo

from tests.conftest import authorize_client


class RecordingTunnelAdapter(MockTunnelAdapter):
    """Mock tunnel that records the local port it was asked to expose."""

    def __init__(self) -> None:
        super().__init__()
        self.started_ports: list[int] = []

    async def start_tunnel(self, local_port: int) -> TunnelInfo:
        self.started_ports.append(local_port)
        return await super().start_tunnel(local_port)


@pytest.fixture
def ott_manager() -> OTTManager:
    return OTTManager()


@pytest.fixture
def session_manager() -> SessionManager:
    return SessionManager()


@pytest.fixture
def tunnel() -> RecordingTunnelAdapter:
    return RecordingTunnelAdapter()


@pytest.fixture
def client(
    ott_manager: OTTManager,
    session_manager: SessionManager,
    tunnel: RecordingTunnelAdapter,
) -> Generator[TestClient, None, None]:
    app = create_app(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lcu_adapter=MockLcuAdapter(),
        tunnel_adapter=tunnel,
        enable_monitor=False,
        server_port=8765,
    )
    yield TestClient(app)


PROTECTED_GETS = [
    "/api/lcu/status",
    "/api/champ-select",
    "/api/spells",
    "/api/lobby",
    "/api/wan/status",
    "/api/runes/trees",
]


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_rest_requires_token(client: TestClient, path: str) -> None:
    resp = client.get(path)
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize(
    "path", ["/api/match/accept", "/api/lobby/matchmaking/start", "/api/wan/toggle"]
)
def test_rest_post_requires_token(client: TestClient, path: str) -> None:
    assert client.post(path).status_code == 401


def test_rest_rejects_forged_and_malformed_tokens(client: TestClient) -> None:
    assert client.get("/api/lobby", headers={"Authorization": "Bearer forged"}).status_code == 401
    assert client.get("/api/lobby", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/api/lobby", headers={"X-Session-Token": "forged"}).status_code == 401


def test_rest_accepts_valid_bearer_token(client: TestClient, ott_manager: OTTManager) -> None:
    authorize_client(client, ott_manager)
    assert client.get("/api/lcu/status").status_code == 200


def test_rest_accepts_legacy_x_session_token(client: TestClient, ott_manager: OTTManager) -> None:
    token = authorize_client(client, ott_manager)
    del client.headers["Authorization"]
    resp = client.get("/api/lcu/status", headers={"X-Session-Token": token})
    assert resp.status_code == 200


def test_rest_rejects_revoked_token(
    client: TestClient, ott_manager: OTTManager, session_manager: SessionManager
) -> None:
    token = authorize_client(client, ott_manager)
    assert client.get("/api/lcu/status").status_code == 200
    assert session_manager.revoke_session(token) is True
    assert client.get("/api/lcu/status").status_code == 401


def test_pair_endpoint_and_root_stay_open(client: TestClient, ott_manager: OTTManager) -> None:
    assert client.get("/").status_code == 200
    ott = ott_manager.generate_ott()
    resp = client.post("/api/pair", json={"ott": ott.token, "deviceName": "Phone"})
    assert resp.status_code == 200
    assert resp.json()["sessionToken"]
    # Burn-on-read still applies: replaying the OTT fails with 401.
    replay = client.post("/api/pair", json={"ott": ott.token, "deviceName": "Phone"})
    assert replay.status_code == 401


def test_openapi_docs_disabled_by_default(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_openapi_docs_enabled_in_debug(ott_manager: OTTManager, session_manager: SessionManager) -> None:
    app = create_app(ott_manager, session_manager, enable_monitor=False, debug=True)
    assert TestClient(app).get("/openapi.json").status_code == 200


def test_wan_toggle_ignores_client_port(
    client: TestClient, ott_manager: OTTManager, tunnel: RecordingTunnelAdapter
) -> None:
    authorize_client(client, ott_manager)
    resp = client.post("/api/wan/toggle?enable=true&port=22")
    assert resp.status_code == 200
    assert tunnel.started_ports == [8765]
    assert client.post("/api/wan/toggle?enable=false").status_code == 200


def test_ws_rejects_invalid_token_with_4401(client: TestClient) -> None:
    with client.websocket_connect("/ws?token=forged") as ws:
        assert ws.receive_json()["type"] == "UNAUTHORIZED"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
    assert exc_info.value.code == WS_CLOSE_UNAUTHORIZED == 4401


def test_revoke_closes_live_websocket_with_4401(
    client: TestClient, ott_manager: OTTManager, session_manager: SessionManager
) -> None:
    token = authorize_client(client, ott_manager)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        assert ws.receive_json()["type"] == "WELCOME"
        # Revocation happens on another thread (Qt in production) without any client input.
        session_manager.revoke_session(token)
        assert ws.receive_json()["type"] == "REVOKED"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
    assert exc_info.value.code == 4401


def test_revoke_only_closes_that_device(
    client: TestClient, ott_manager: OTTManager, session_manager: SessionManager
) -> None:
    keep = authorize_client(client, ott_manager, "Keep")
    drop = authorize_client(client, ott_manager, "Drop")
    with client.websocket_connect(f"/ws?token={keep}") as ws_keep:
        ws_keep.receive_json()  # WELCOME
        with client.websocket_connect(f"/ws?token={drop}") as ws_drop:
            ws_drop.receive_json()  # WELCOME
            session_manager.revoke_session(drop)
            assert ws_drop.receive_json()["type"] == "REVOKED"
        ws_keep.send_json({"type": "PING"})
        assert ws_keep.receive_json()["type"] != "REVOKED"


def test_ws_non_json_gets_error_envelope(client: TestClient, ott_manager: OTTManager) -> None:
    token = authorize_client(client, ott_manager)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()  # WELCOME
        ws.send_text("hello")
        msg = ws.receive_json()
        assert msg["type"] == "ERROR"
        assert msg["error"] == "INVALID_MESSAGE"


def test_session_idle_ttl_expires_and_purges() -> None:
    manager = SessionManager(idle_ttl_seconds=60)
    session = manager.create_session("Phone", "dev-1")
    assert manager.validate_session(session.session_token) is session
    session.last_seen_at = time.time() - 120
    assert manager.validate_session(session.session_token) is None
    assert manager.list_active_sessions() == []


def test_session_absolute_ttl_expires() -> None:
    manager = SessionManager(idle_ttl_seconds=None, absolute_ttl_seconds=10)
    session = manager.create_session("Phone", "dev-1")
    session.created_at = time.time() - 11
    assert manager.validate_session(session.session_token) is None


def test_revocation_listener_and_purge() -> None:
    manager = SessionManager()
    seen: list[str] = []
    manager.add_revocation_listener(lambda s: seen.append(s.session_token))
    session = manager.create_session("Phone", "dev-1")
    assert manager.revoke_device("dev-1") is True
    assert seen == [session.session_token]
    assert session.is_active is False
    assert manager._sessions == {}
