"""RiftPilot — Mode-Aware Champion Select End-to-End Integration Tests.

Verifies end-to-end champion select orchestration across Web Hub, WebSocket gateway,
and LCU adapter:
- GET /api/champ-select (with centralized `modeContext` and `eligibility`)
- GET /api/champ-select/grid (with `banned`, `unavailablePick`, `selectable` flags)
- POST /api/champ-select/action (with server-side turn, phase, ban, and timer re-validation)
- Mode-filtered summoner spells (GET/POST /api/spells, rejecting invalid mode spells)
- Official 3 recommended rune pages (GET /api/runes/recommended, user-confirmed apply, no fake data)
- Real-time WebSocket lifecycle and zero-trust token validation
"""

from __future__ import annotations

from typing import Any, Generator

import pytest
from starlette.testclient import TestClient

from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.web_hub import create_app
from riftpilot.core.champ_select import (
    ChampSelectAction,
    ChampSelectBans,
    ChampSelectPlayer,
    ChampSelectSession,
    ChampSelectTimer,
    ChampionSummary,
)
from riftpilot.core.game_mode import GameModeContext
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
        timer=ChampSelectTimer(phase="BAN_PICK", time_left_in_phase=30000, total_time_in_phase=30000),
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
    body = resp.json()
    assert body["session"] is None
    assert body["eligibility"]["stage"] == "INACTIVE"


@pytest.mark.asyncio
async def test_http_get_champ_select_active_with_central_state(
    client: TestClient,
    mock_lcu: MockLcuAdapter,
    sample_session: ChampSelectSession,
) -> None:
    """GET /api/champ-select returns serialized session, modeContext, and eligibility."""
    await mock_lcu.simulate_connect()
    await mock_lcu.simulate_champ_select_session(sample_session)

    resp = client.get("/api/champ-select")
    assert resp.status_code == 200
    body = resp.json()
    data = body["session"]
    assert data is not None
    assert data["localPlayerCellId"] == 0
    assert data["activeAction"]["id"] == 1
    assert data["localPlayer"]["assigned_position"] == "middle"
    assert body["modeContext"]["gameMode"] == "CLASSIC"
    assert body["eligibility"]["stage"] == "PICK_MY_TURN"
    assert body["eligibility"]["isMyTurn"] is True


@pytest.mark.asyncio
async def test_http_grid_champions_marks_banned_unselectable(
    client: TestClient,
    mock_lcu: MockLcuAdapter,
    sample_session: ChampSelectSession,
    sample_grid: list[ChampionSummary],
) -> None:
    """GET /api/champ-select/grid marks banned champions as unselectable."""
    await mock_lcu.simulate_connect()
    sample_session.bans = ChampSelectBans(my_team_bans=[84])  # Ban Akali
    await mock_lcu.simulate_champ_select_session(sample_session)
    mock_lcu.set_grid_champions(sample_grid)

    resp = client.get("/api/champ-select/grid")
    assert resp.status_code == 200
    champions = resp.json()["champions"]
    assert len(champions) == 3

    ahri = next(c for c in champions if c["id"] == 103)
    akali = next(c for c in champions if c["id"] == 84)
    assert ahri["banned"] is False
    assert ahri["selectable"] is True
    assert akali["banned"] is True
    assert akali["selectable"] is False


@pytest.mark.asyncio
async def test_http_post_champ_select_action_and_rejections(
    client: TestClient,
    mock_lcu: MockLcuAdapter,
    sample_session: ChampSelectSession,
) -> None:
    """POST /api/champ-select/action enforces turn, ban status, and duplicate lock rules."""
    await mock_lcu.simulate_connect()
    sample_session.bans = ChampSelectBans(their_team_bans=[84])  # Akali banned
    await mock_lcu.simulate_champ_select_session(sample_session)

    # 1. Attempting to lock a banned champion (84) is rejected with 400
    banned_resp = client.post(
        "/api/champ-select/action",
        json={"actionId": 1, "championId": 84, "completed": True},
    )
    assert banned_resp.status_code == 400
    assert banned_resp.json()["detail"]["errorCode"] == "CHAMPION_BANNED"

    # 2. Valid lock-in succeeds
    resp = client.post(
        "/api/champ-select/action",
        json={"actionId": 1, "championId": 103, "completed": True},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert sample_session.actions[0][0].champion_id == 103
    assert sample_session.actions[0][0].completed is True

    # 3. Duplicate lock-in on an already completed action is rejected with 409
    dup_resp = client.post(
        "/api/champ-select/action",
        json={"actionId": 1, "championId": 103, "completed": True},
    )
    assert dup_resp.status_code == 409
    assert dup_resp.json()["detail"]["errorCode"] in ("ACTION_ALREADY_COMPLETED", "ACTION_NOT_FOUND")


@pytest.mark.asyncio
async def test_http_post_action_rejected_when_not_my_turn(
    client: TestClient,
    mock_lcu: MockLcuAdapter,
) -> None:
    """Lock-in is rejected with 409 when local player's action is not in progress."""
    await mock_lcu.simulate_connect()
    waiting_session = ChampSelectSession(
        local_player_cell_id=0,
        my_team=[ChampSelectPlayer(cell_id=0, assigned_position="middle")],
        actions=[
            [
                ChampSelectAction(id=1, actor_cell_id=1, champion_id=0, type="pick", completed=False, is_in_progress=True),
                ChampSelectAction(id=2, actor_cell_id=0, champion_id=103, type="pick", completed=False, is_in_progress=False),
            ]
        ],
        timer=ChampSelectTimer(phase="BAN_PICK", time_left_in_phase=25000, total_time_in_phase=30000),
    )
    await mock_lcu.simulate_champ_select_session(waiting_session)

    # Hovering (completed=False) before turn is allowed
    hover_resp = client.post(
        "/api/champ-select/action",
        json={"actionId": 2, "championId": 103, "completed": False},
    )
    assert hover_resp.status_code == 200

    # Locking in (completed=True) before turn is strictly rejected with 409
    lock_resp = client.post(
        "/api/champ-select/action",
        json={"actionId": 2, "championId": 103, "completed": True},
    )
    assert lock_resp.status_code == 409
    assert lock_resp.json()["detail"]["errorCode"] == "NOT_YOUR_PICK_TURN"


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
        welcome = ws.receive_json()
        assert welcome["type"] == "WELCOME"

        ws.send_json({"type": "GET_GRID_CHAMPIONS", "token": paired_token})
        grid_msg = ws.receive_json()
        assert grid_msg["type"] == "GRID_CHAMPIONS"
        assert len(grid_msg["data"]) == 3

        await mock_lcu.simulate_champ_select_session(sample_session)

        received_types = []
        for _ in range(2):
            msg = ws.receive_json()
            received_types.append(msg["type"])
            if msg["type"] == "CHAMP_SELECT_UPDATE":
                assert msg["data"]["activeAction"]["id"] == 1
                assert msg["eligibility"]["stage"] == "PICK_MY_TURN"

        assert "CHAMP_SELECT_UPDATE" in received_types

        ws.send_json({
            "type": "CHAMP_SELECT_ACTION",
            "token": paired_token,
            "actionId": 1,
            "championId": 103,
            "completed": True,
        })

        # Hover + Lock dispatches session updates and final ACTION_RESULT
        action_result = None
        for _ in range(4):
            m = ws.receive_json()
            if m["type"] == "ACTION_RESULT":
                action_result = m
                break

        assert action_result is not None
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
        session_manager.revoke_session(paired_token)

        ws.send_json({
            "type": "CHAMP_SELECT_ACTION",
            "token": paired_token,
            "actionId": 1,
            "championId": 103,
        })

        revoked_msg = ws.receive_json()
        assert revoked_msg["type"] == "REVOKED"


# ============================================================================
# Mode-Aware Summoner Spells & Official 3 Recommended Rune Pages E2E Tests
# ============================================================================


@pytest.mark.asyncio
async def test_rest_mode_filtered_summoner_spells(
    client: TestClient, mock_lcu: MockLcuAdapter
) -> None:
    """GET /api/spells and POST /api/spells filter and validate spells by active game mode."""
    await mock_lcu.simulate_connect()

    # 1. Default CLASSIC mode: Smite (11) & Teleport (12) valid, Snowball (32) invalid
    resp_classic = client.get("/api/spells")
    assert resp_classic.status_code == 200
    classic_ids = {s["id"] for s in resp_classic.json()["spells"]}
    assert 12 in classic_ids
    assert 32 not in classic_ids

    ok_classic = client.post("/api/spells", json={"spell1Id": 12, "spell2Id": 4})
    assert ok_classic.status_code == 200
    assert ok_classic.json()["success"] is True

    # 2. Switch to ARAM mode: Snowball (32) valid, Teleport (12) & Smite (11) rejected with 400
    mock_lcu.set_game_mode_context(GameModeContext(game_mode="ARAM", queue_id=450, map_id=12))
    resp_aram = client.get("/api/spells")
    aram_ids = {s["id"] for s in resp_aram.json()["spells"]}
    assert 32 in aram_ids
    assert 12 not in aram_ids
    assert 11 not in aram_ids

    bad_aram = client.post("/api/spells", json={"spell1Id": 12, "spell2Id": 4})
    assert bad_aram.status_code == 400

    good_aram = client.post("/api/spells", json={"spell1Id": 32, "spell2Id": 4})
    assert good_aram.status_code == 200
    assert mock_lcu.spell1_id == 32


@pytest.mark.asyncio
async def test_rest_three_recommended_rune_pages_and_no_fake_data(
    client: TestClient, mock_lcu: MockLcuAdapter
) -> None:
    """GET /api/runes/recommended returns LCU's 3 pages or empty list when unavailable."""
    await mock_lcu.simulate_connect()

    resp = client.get("/api/runes/recommended?championId=103&position=middle")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert len(data["pages"]) == 3
    assert data["pages"][0]["primaryTreeName"] != ""
    assert data["pages"][0]["subTreeName"] != ""

    # When LCU has no recommendations for a champion, returns empty list (no fake data)
    mock_lcu.set_recommended_rune_pages(999, [])
    empty_resp = client.get("/api/runes/recommended?championId=999")
    assert empty_resp.status_code == 200
    assert empty_resp.json()["available"] is False
    assert empty_resp.json()["pages"] == []


@pytest.mark.asyncio
async def test_rest_get_and_apply_runes(
    client: TestClient, mock_lcu: MockLcuAdapter
) -> None:
    """GET /api/runes/current and POST /api/runes/apply manage client rune pages."""
    await mock_lcu.simulate_connect()

    resp = client.get("/api/runes/current")
    assert resp.status_code == 200
    assert resp.json()["page"] is not None

    apply_payload = {
        "name": "E2E Runes",
        "primaryStyleId": 8200,
        "subStyleId": 8300,
        "selectedPerkIds": [8214, 8224, 8210, 8237, 8304, 8345, 5008, 5008, 5003],
    }
    resp = client.post("/api/runes/apply", json=apply_payload)
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert mock_lcu.current_rune_page is not None
    assert mock_lcu.current_rune_page.name == "E2E Runes"
    assert mock_lcu.current_rune_page.primary_style_id == 8200


@pytest.mark.asyncio
async def test_ws_spells_and_three_recommended_runes_flow(
    client: TestClient, paired_token: str, mock_lcu: MockLcuAdapter
) -> None:
    """WebSocket client receives mode-valid spells, 3 recommended rune pages, and applies chosen page."""
    await mock_lcu.simulate_connect()

    with client.websocket_connect(f"/ws?token={paired_token}") as ws:
        ws.receive_json()  # WELCOME

        # 1. Query Spells
        ws.send_json({"type": "GET_SPELLS", "token": paired_token})
        spells_msg = ws.receive_json()
        assert spells_msg["type"] == "SPELLS_CATALOG"
        assert len(spells_msg["spells"]) >= 8

        # 2. Assign Spells
        ws.send_json({
            "type": "SET_SPELLS",
            "token": paired_token,
            "spell1Id": 11,
            "spell2Id": 4,
        })
        spells_res = ws.receive_json()
        assert spells_res["type"] == "SPELLS_RESULT"
        assert spells_res["success"] is True
        assert mock_lcu.spell1_id == 11

        # 3. Query 3 Official Recommended Rune Pages
        ws.send_json({
            "type": "GET_RECOMMENDED_RUNES",
            "token": paired_token,
            "championId": 157,
            "position": "middle",
        })
        runes_rec = ws.receive_json()
        assert runes_rec["type"] == "RECOMMENDED_RUNES"
        assert runes_rec["available"] is True
        assert len(runes_rec["pages"]) == 3

        # 4. User manually selects page #2 (index 1) and confirms apply
        chosen_page = runes_rec["pages"][1]["page"]
        ws.send_json({
            "type": "APPLY_RUNES",
            "token": paired_token,
            "page": chosen_page,
            "championId": 157,
        })
        runes_res = ws.receive_json()
        assert runes_res["type"] == "RUNES_RESULT"
        assert runes_res["success"] is True
        assert mock_lcu.current_rune_page is not None
        assert mock_lcu.current_rune_page.primary_style_id == chosen_page["primaryStyleId"]


def test_ws_spells_rejected_on_revoked_session(
    client: TestClient, session_manager: SessionManager, paired_token: str
) -> None:
    """Zero-trust: SET_SPELLS with revoked token triggers immediate session termination."""
    with client.websocket_connect(f"/ws?token={paired_token}") as ws:
        ws.receive_json()  # WELCOME
        session_manager.revoke_session(paired_token)

        ws.send_json({
            "type": "SET_SPELLS",
            "token": paired_token,
            "spell1Id": 4,
            "spell2Id": 14,
        })
        revoked_msg = ws.receive_json()
        assert revoked_msg["type"] == "REVOKED"
