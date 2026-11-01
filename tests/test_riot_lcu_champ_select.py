"""RiftPilot Unit Tests — Riot LCU Champion Select Adapter and Stream.

Verifies RiotLcuAdapter and LcuEventStream champ select operations:
- GET /lol-champ-select/v1/session
- PATCH /lol-champ-select/v1/session/actions/{id}
- GET /lol-champ-select/v1/all-grid-champions
- Real-time WebSocket event dispatching for champ select sessions
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from riftpilot.adapters.lcu_discovery import LcuCredentials
from riftpilot.adapters.lcu_stream import LcuEventStream
from riftpilot.adapters.riot_lcu import RiotLcuAdapter
from riftpilot.core.champ_select import ChampSelectSession, ChampionSummary
from riftpilot.core.lcu_port import LcuConnectionStatus


@pytest.fixture
def credentials() -> LcuCredentials:
    return LcuCredentials(
        port=29999,
        auth_token="test_secret_token",
        pid=12345,
        protocol="https",
    )


class MockTransport(httpx.AsyncBaseTransport):
    """Custom HTTPX mock transport for testing RiotLcuAdapter endpoints."""

    def __init__(self, handler: Any) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._handler(request)


class MockWebSocketConnection:
    """Mock WebSocket client for LcuEventStream testing."""

    def __init__(self, incoming_messages: list[str | bytes] | None = None) -> None:
        self._queue: asyncio.Queue[str | bytes] = asyncio.Queue()
        if incoming_messages:
            for msg in incoming_messages:
                self._queue.put_nowait(msg)
        self.sent_messages: list[str | bytes] = []
        self.is_closed = False

    async def send(self, data: str | bytes) -> None:
        self.sent_messages.append(data)

    async def recv(self) -> str | bytes:
        return await self._queue.get()

    def push(self, data: str | bytes) -> None:
        self._queue.put_nowait(data)

    async def close(self) -> None:
        self.is_closed = True


@pytest.mark.asyncio
async def test_get_champ_select_session_success_200(
    credentials: LcuCredentials,
) -> None:
    """RiotLcuAdapter queries GET /lol-champ-select/v1/session and returns parsed session."""
    session_data = {
        "localPlayerCellId": 2,
        "myTeam": [{"cellId": 2, "championId": 103, "team": 1}],
        "theirTeam": [],
        "actions": [
            [{"id": 1, "actorCellId": 2, "type": "pick", "completed": False, "isInProgress": True}]
        ],
        "timer": {"phase": "BAN_PICK", "timeLeftInPhase": 25000},
        "bans": {"myTeamBans": [], "theirTeamBans": [], "numBans": 0},
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/lol-champ-select/v1/session"
        assert request.method == "GET"
        return httpx.Response(200, json=session_data)

    adapter = RiotLcuAdapter(
        credentials=credentials,
        transport=MockTransport(handler),
    )
    session = await adapter.get_champ_select_session()
    assert session is not None
    assert session.local_player_cell_id == 2
    active = session.get_active_action()
    assert active is not None
    assert active.id == 1
    assert active.is_pick() is True


@pytest.mark.asyncio
async def test_get_champ_select_session_404_returns_none(
    credentials: LcuCredentials,
) -> None:
    """When not in champ select, LCU returns 404, adapter returns None."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not in champ select"})

    adapter = RiotLcuAdapter(
        credentials=credentials,
        transport=MockTransport(handler),
    )
    assert await adapter.get_champ_select_session() is None


@pytest.mark.asyncio
async def test_get_champ_select_session_when_disconnected() -> None:
    """Calling get_champ_select_session when adapter is disconnected returns None without making requests."""
    adapter = RiotLcuAdapter(discoverer=lambda: None)
    assert await adapter.get_champ_select_session() is None


@pytest.mark.asyncio
async def test_get_champ_select_session_auth_failed(
    credentials: LcuCredentials,
) -> None:
    """HTTP 401/403 updates status to AUTHENTICATION_FAILED and returns None."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Unauthorized"})

    adapter = RiotLcuAdapter(
        credentials=credentials,
        transport=MockTransport(handler),
    )
    session = await adapter.get_champ_select_session()
    assert session is None
    assert await adapter.get_connection_status() == LcuConnectionStatus.AUTHENTICATION_FAILED


@pytest.mark.asyncio
async def test_patch_champ_select_action_success_204(
    credentials: LcuCredentials,
) -> None:
    """RiotLcuAdapter sends PATCH /lol-champ-select/v1/session/actions/{id} with payload."""
    payload_received: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/lol-champ-select/v1/session/actions/5"
        assert request.method == "PATCH"
        nonlocal payload_received
        payload_received = json.loads(request.content.decode("utf-8"))
        return httpx.Response(204)

    adapter = RiotLcuAdapter(
        credentials=credentials,
        transport=MockTransport(handler),
    )
    success = await adapter.patch_champ_select_action(
        action_id=5,
        champion_id=103,
        completed=True,
    )
    assert success is True
    assert payload_received == {"championId": 103, "completed": True}


@pytest.mark.asyncio
async def test_patch_champ_select_action_failure_500(
    credentials: LcuCredentials,
) -> None:
    """Non-200/204 status returns False."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Internal LCU Error"})

    adapter = RiotLcuAdapter(
        credentials=credentials,
        transport=MockTransport(handler),
    )
    success = await adapter.patch_champ_select_action(
        action_id=5,
        champion_id=103,
    )
    assert success is False


@pytest.mark.asyncio
async def test_patch_champ_select_action_disconnected() -> None:
    """Patching when disconnected returns False immediately."""
    adapter = RiotLcuAdapter(discoverer=lambda: None)
    assert await adapter.patch_champ_select_action(1, 103) is False


@pytest.mark.asyncio
async def test_get_grid_champions_success_200(
    credentials: LcuCredentials,
) -> None:
    """RiotLcuAdapter queries GET /lol-champ-select/v1/all-grid-champions."""
    champs_data = [
        {"id": 103, "name": "Ahri", "roles": ["mage"], "squarePortraitPath": "/ahri.png"},
        {"id": 84, "name": "Akali", "roles": ["assassin"], "squarePortraitPath": "/akali.png"},
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/lol-champ-select/v1/all-grid-champions"
        return httpx.Response(200, json=champs_data)

    adapter = RiotLcuAdapter(
        credentials=credentials,
        transport=MockTransport(handler),
    )
    champions = await adapter.get_grid_champions()
    assert len(champions) == 2
    assert champions[0].id == 103
    assert champions[0].name == "Ahri"
    assert champions[1].id == 84


@pytest.mark.asyncio
async def test_get_grid_champions_disconnected() -> None:
    """Retrieving grid champions when disconnected returns empty list."""
    adapter = RiotLcuAdapter(discoverer=lambda: None)
    assert await adapter.get_grid_champions() == []


@pytest.mark.asyncio
async def test_stream_champ_select_event_dispatch_to_adapter_subscribers(
    credentials: LcuCredentials,
) -> None:
    """LCU WebSocket event on /lol-champ-select/v1/session triggers adapter champ select subscribers."""
    mock_ws = MockWebSocketConnection()
    mock_connect = AsyncMock(return_value=mock_ws)

    received: list[ChampSelectSession | None] = []
    received_event = asyncio.Event()

    def on_cs(session: ChampSelectSession | None) -> None:
        received.append(session)
        received_event.set()

    with patch("websockets.connect", mock_connect):
        adapter = RiotLcuAdapter(credentials=credentials)
        adapter.subscribe_champ_select(on_cs)
        await adapter.ensure_stream_started()

        # Simulate incoming LCU WAMP event
        cs_payload = {
            "localPlayerCellId": 0,
            "myTeam": [{"cellId": 0, "championId": 103}],
            "actions": [],
        }
        wamp_event = json.dumps([
            8,
            "OnJsonApiEvent_lol-champ-select_v1_session",
            {"uri": "/lol-champ-select/v1/session", "data": cs_payload},
        ])
        mock_ws.push(wamp_event)

        # Wait for dispatch
        await asyncio.wait_for(received_event.wait(), timeout=2.0)
        assert len(received) == 1
        assert received[0] is not None
        assert received[0].local_player_cell_id == 0

        # Test unsubscribe
        assert adapter.unsubscribe_champ_select(on_cs) is True
        assert adapter.unsubscribe_champ_select(on_cs) is False

        await adapter.close()


@pytest.mark.asyncio
async def test_patch_champ_select_action_triggers_complete_endpoint(
    credentials: LcuCredentials,
) -> None:
    """When completed=True, adapter sends PATCH and then POST .../complete."""
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/lol-champ-select/v1/session":
            return httpx.Response(200, json={"actions": [], "localPlayerCellId": 0})
        if request.url.path == "/lol-champ-select/v1/session/actions/3":
            return httpx.Response(204)
        if request.url.path == "/lol-champ-select/v1/session/actions/3/complete":
            return httpx.Response(200)
        return httpx.Response(404)

    adapter = RiotLcuAdapter(credentials=credentials, transport=MockTransport(handler))
    success = await adapter.patch_champ_select_action(action_id=3, champion_id=103, completed=True)
    assert success is True
    assert "PATCH /lol-champ-select/v1/session/actions/3" in calls
    assert "POST /lol-champ-select/v1/session/actions/3/complete" in calls


@pytest.mark.asyncio
async def test_patch_champ_select_hover_triggers_my_selection_intent(
    credentials: LcuCredentials,
) -> None:
    """When completed=False, adapter updates pick intent via /my-selection."""
    calls: list[str] = []
    my_selection_payload: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/lol-champ-select/v1/session":
            return httpx.Response(200, json={"actions": [], "localPlayerCellId": 0})
        if request.url.path == "/lol-champ-select/v1/session/actions/2":
            return httpx.Response(204)
        if request.url.path == "/lol-champ-select/v1/session/my-selection":
            nonlocal my_selection_payload
            my_selection_payload = json.loads(request.content.decode("utf-8"))
            return httpx.Response(204)
        return httpx.Response(404)

    adapter = RiotLcuAdapter(credentials=credentials, transport=MockTransport(handler))
    success = await adapter.patch_champ_select_action(action_id=2, champion_id=84, completed=False)
    assert success is True
    assert "PATCH /lol-champ-select/v1/session/actions/2" in calls
    assert "PATCH /lol-champ-select/v1/session/my-selection" in calls
    assert my_selection_payload == {"championPickIntent": 84}


@pytest.mark.asyncio
async def test_patch_champ_select_auto_resolves_action_id_when_zero(
    credentials: LcuCredentials,
) -> None:
    """When action_id is 0, adapter resolves the active action ID from the session."""
    session_data = {
        "localPlayerCellId": 0,
        "actions": [
            [{"id": 7, "actorCellId": 0, "type": "ban", "completed": False, "isInProgress": True}]
        ],
    }
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/lol-champ-select/v1/session":
            return httpx.Response(200, json=session_data)
        if request.url.path == "/lol-champ-select/v1/session/actions/7":
            return httpx.Response(204)
        if request.url.path == "/lol-champ-select/v1/session/actions/7/complete":
            return httpx.Response(200)
        return httpx.Response(404)

    adapter = RiotLcuAdapter(credentials=credentials, transport=MockTransport(handler))
    success = await adapter.patch_champ_select_action(action_id=0, champion_id=51, completed=True)
    assert success is True
    assert "PATCH /lol-champ-select/v1/session/actions/7" in calls
