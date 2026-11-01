"""RiftPilot — Champion Select End-to-End Integration Tests.

Verifies end-to-end champion select orchestration across Web Hub, WebSocket gateway,
and LCU adapter:
- GET /api/champ-select and GET /api/champ-select/grid
- POST /api/champ-select/action
- Real-time WebSocket GET_CHAMP_SELECT, GET_GRID_CHAMPIONS, and CHAMP_SELECT_ACTION
- Automated broadcast on session state updates
- Zero-trust token authentication for champ select actions
"""

from __future__ import annotations

import json
from typing import Any, Generator

import pytest
from starlette.testclient import TestClient

from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.web_hub import create_app
from riftpilot.core.champ_select import (
    ChampSelectAction,
    ChampSelectPlayer,
    ChampSelectSession,
    ChampSelectTimer,
    ChampionSummary,
)
from riftpilot.core.pairing import OTTManager, SessionManager


@pytest.fixture
def ott_manager() -> OTTManager:
    return OTTManager()


@pytest.fixture
def session_manager() -> SessionManager:
    return SessionManager()


@pytest.fixture
def mock_lcu() -> MockLcuAdapter:
    return MockLcuAdapter()


@pytest.fixture
def sample_session() -> ChampSelectSession:
    return ChampSelectSession(
        local_player_cell_id=0,
        my_team=[
            ChampSelectPlayer(cell_id=0, champion_id=0, assigned_position="middle", team=1),
            ChampSelectPlayer(cell_id=1, champion_id=0, assigned_position="top", team=1),
        ],
        their_team=[
            ChampSelectPlayer(cell_id=5, champion_id=0, assigned_position="middle", team=2),
        ],
        actions=[
            [ChampSelectAction(id=1, actor_cell_id=0, champion_id=0, type="pick", completed=False, is_in_progress=True)]
        ],
        timer=ChampSelectTimer(phase="BAN_PICK", time_left_in_phase=30000),
    )


@pytest.fixture
def sample_grid() -> list[ChampionSummary]:
    return [
        ChampionSummary(id=103, name="Ahri", roles=["mage"]),
        ChampionSummary(id=84, name="Akali", roles=["assassin"]),
        ChampionSummary(id=64, name="Lee Sin", roles=["fighter"]),
    ]


@pytest.fixture
def web_app(
    ott_manager: OTTManager,
    session_manager: SessionManager,
    mock_lcu: MockLcuAdapter,
) -> Any:
    return create_app(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lcu_adapter=mock_lcu,
        enable_monitor=False,
    )


@pytest.fixture
def client(web_app: Any) -> Generator[TestClient, None, None]:
    with TestClient(web_app) as test_client:
        yield test_client


@pytest.fixture
def paired_token(ott_manager: OTTManager, client: TestClient) -> str:
    token_obj = ott_manager.generate_ott()
    resp = client.post("/api/pair", json={"ott": token_obj.token, "deviceName": "TestPhone"})
    assert resp.status_code == 200
    return resp.json()["sessionToken"]


def test_http_get_champ_select_inactive(client: TestClient) -> None:
    """GET /api/champ-select returns session=None when disconnected or no session."""
    resp = client.get("/api/champ-select")
    assert resp.status_code == 200
    assert resp.json()["session"] is None


@pytest.mark.asyncio
async def test_http_get_champ_select_active(
    client: TestClient,
    mock_lcu: MockLcuAdapter,
    sample_session: ChampSelectSession,
) -> None:
    """GET /api/champ-select returns serialized session when active."""
    await mock_lcu.simulate_connect()
    await mock_lcu.simulate_champ_select_session(sample_session)

    resp = client.get("/api/champ-select")
    assert resp.status_code == 200
    data = resp.json()["session"]
    assert data is not None
    assert data["localPlayerCellId"] == 0
    assert data["activeAction"]["id"] == 1
    assert data["localPlayer"]["assigned_position"] == "middle"


@pytest.mark.asyncio
async def test_http_grid_champions(
    client: TestClient,
    mock_lcu: MockLcuAdapter,
    sample_grid: list[ChampionSummary],
) -> None:
    """GET /api/champ-select/grid returns available champions."""
    await mock_lcu.simulate_connect()
    mock_lcu.set_grid_champions(sample_grid)

    resp = client.get("/api/champ-select/grid")
    assert resp.status_code == 200
    champions = resp.json()["champions"]
    assert len(champions) == 3
    assert champions[0]["name"] == "Ahri"
    assert champions[1]["name"] == "Akali"


@pytest.mark.asyncio
async def test_http_post_champ_select_action(
    client: TestClient,
    mock_lcu: MockLcuAdapter,
    sample_session: ChampSelectSession,
) -> None:
    """POST /api/champ-select/action performs pick/ban lock-in."""
    await mock_lcu.simulate_connect()
    await mock_lcu.simulate_champ_select_session(sample_session)

    resp = client.post(
        "/api/champ-select/action",
        json={"actionId": 1, "championId": 103, "completed": True},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # Verify updated session
    active = sample_session.actions[0][0]
    assert active.champion_id == 103
    assert active.completed is True


@pytest.mark.asyncio
async def test_ws_champ_select_lifecycle(
    client: TestClient,
    mock_lcu: MockLcuAdapter,
    paired_token: str,
    sample_session: ChampSelectSession,
    sample_grid: list[ChampionSummary],
) -> None:
    """Full WebSocket lifecycle: get grid, receive broadcast updates, lock in champion."""
    await mock_lcu.simulate_connect()
    mock_lcu.set_grid_champions(sample_grid)

    with client.websocket_connect(f"/ws?token={paired_token}") as ws:
        # 1. Receive initial WELCOME
        welcome = ws.receive_json()
        assert welcome["type"] == "WELCOME"

        # 2. Request Grid Champions
        ws.send_json({"type": "GET_GRID_CHAMPIONS", "token": paired_token})
        grid_msg = ws.receive_json()
        assert grid_msg["type"] == "GRID_CHAMPIONS"
        assert len(grid_msg["data"]) == 3

        # 3. Trigger Champ Select Session Simulation
        await mock_lcu.simulate_champ_select_session(sample_session)

        # 4. Connected client receives GAMEFLOW_UPDATE then CHAMP_SELECT_UPDATE
        # The client might receive GAMEFLOW_UPDATE (ChampSelect) and CHAMP_SELECT_UPDATE
        received_types = []
        for _ in range(2):
            msg = ws.receive_json()
            received_types.append(msg["type"])
            if msg["type"] == "CHAMP_SELECT_UPDATE":
                assert msg["data"]["activeAction"]["id"] == 1

        assert "CHAMP_SELECT_UPDATE" in received_types

        # 5. Send Lock-in Action via WebSocket
        ws.send_json({
            "type": "CHAMP_SELECT_ACTION",
            "token": paired_token,
            "actionId": 1,
            "championId": 103,
            "completed": True,
        })

        # Lock-in action triggers both an updated CHAMP_SELECT_UPDATE broadcast and an ACTION_RESULT
        messages = [ws.receive_json(), ws.receive_json()]
        types = [m["type"] for m in messages]
        assert "ACTION_RESULT" in types
        assert "CHAMP_SELECT_UPDATE" in types

        action_result = next(m for m in messages if m["type"] == "ACTION_RESULT")
        assert action_result["success"] is True
        assert action_result["completed"] is True
        assert action_result["championId"] == 103


def test_ws_champ_select_action_rejected_on_revoked_session(
    client: TestClient,
    session_manager: SessionManager,
    paired_token: str,
) -> None:
    """Zero-trust: Action sent with a revoked token is rejected immediately."""
    with client.websocket_connect(f"/ws?token={paired_token}") as ws:
        ws.receive_json()  # WELCOME

        # Revoke session
        session_manager.revoke_session(paired_token)

        # Attempt action
        ws.send_json({
            "type": "CHAMP_SELECT_ACTION",
            "token": paired_token,
            "actionId": 1,
            "championId": 103,
        })

        revoked_msg = ws.receive_json()
        assert revoked_msg["type"] == "REVOKED"
