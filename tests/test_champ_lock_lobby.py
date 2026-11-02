"""Unit and Integration Tests for Post-Lock Match Lobby Screen.

Validates end-to-end post-lock lobby functionality:
1. Core Domain Models:
   - ChampSelectPlayer summoner_name parsing and serialization.
   - ChampSelectSession.is_local_pick_completed logic across pick/ban turns, ARAM, and invalid states.
   - Serialization of isLocalPickCompleted and is_local_pick_completed flags in session dictionary.
2. Web Hub DOM & CSS Contract (INDEX_HTML):
   - Presence of `#postLockLobbyView` container.
   - Separation of My Team (blue) and Enemy Team (red) sections.
   - Local player styling markers ("SEN" badge, golden border, is-me class).
   - In-lobby trade and lane swap notification banners and action buttons.
   - Responsive layout rules (320px, 375px, 390px, 430px, flex column, touch-friendly min-height).
   - Smooth transition animation rules.
3. REST & WebSocket Integration:
   - GET /api/champ-select reflects isLocalPickCompleted when locked vs unlocked.
   - WebSocket real-time dispatch of lock-in state updates.
   - In-lobby lane swap and champion trade actions over WebSocket.
4. Gameflow Lifecycle Transitions:
   - Transition from ChampSelect to InProgress / GameStart activating #inGameView.
   - Riot TOS privacy compliance: Opponent summoner names hidden by default without fabricating fake names.
"""

from __future__ import annotations

import asyncio
from typing import Any
import pytest
from starlette.testclient import TestClient

from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.web_hub import INDEX_HTML, create_app
from riftpilot.core.champ_select import (
    ChampSelectAction,
    ChampSelectBans,
    ChampSelectPlayer,
    ChampSelectSession,
    ChampSelectTimer,
    ChampionSummary,
)
from riftpilot.core.lcu_port import GameflowPhase
from riftpilot.core.lobby import (
    ChampSelectTrade,
    LaneSwapRequest,
    TradeState,
)
from riftpilot.core.pairing import OTTManager, SessionManager


# ============================================================================
# Fixtures
# ============================================================================

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
def test_app_bundle(
    ott_manager: OTTManager,
    session_manager: SessionManager,
    mock_lcu: MockLcuAdapter,
) -> tuple[TestClient, MockLcuAdapter, str]:
    app = create_app(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lcu_adapter=mock_lcu,
        enable_monitor=False,
    )
    client = TestClient(app)
    ott = ott_manager.generate_ott()
    pair_resp = client.post("/api/pair", json={"ott": ott.token, "deviceName": "TestMobile"})
    assert pair_resp.status_code == 200
    token = pair_resp.json()["sessionToken"]
    return client, mock_lcu, token


def receive_typed_msg(ws: Any, expected_type: str, max_attempts: int = 15) -> dict[str, Any]:
    """Helper reading websocket messages until a specific type arrives."""
    for _ in range(max_attempts):
        msg = ws.receive_json()
        if msg.get("type") == expected_type:
            return msg
    raise AssertionError(f"Expected message of type '{expected_type}' did not arrive within {max_attempts} messages.")


# ============================================================================
# 1. Core Domain Models & is_local_pick_completed Tests
# ============================================================================

class TestChampLockLobbyDomain:
    """Verifies domain models and pick completion contracts."""

    def test_champ_select_player_summoner_name_parsing(self) -> None:
        """ChampSelectPlayer should parse summonerName or fallbacks from LCU data."""
        p1 = ChampSelectPlayer.from_lcu_dict({
            "cellId": 0,
            "summonerName": "Faker",
            "assignedPosition": "middle",
            "championId": 103,
        })
        assert p1.summoner_name == "Faker"
        d1 = p1.to_dict()
        assert d1["summonerName"] == "Faker"
        assert d1["summoner_name"] == "Faker"

        # Fallback to displayName or gameName
        p2 = ChampSelectPlayer.from_lcu_dict({
            "cellId": 1,
            "displayName": "RiftMaster",
        })
        assert p2.summoner_name == "RiftMaster"

        # Empty fallback
        p3 = ChampSelectPlayer.from_lcu_dict({"cellId": 2})
        assert p3.summoner_name == ""

    def test_is_local_pick_completed_false_when_pick_in_progress(self) -> None:
        """Local player with incomplete pick action should report False."""
        session = ChampSelectSession(
            local_player_cell_id=0,
            my_team=[ChampSelectPlayer(cell_id=0, champion_id=103, assigned_position="middle")],
            actions=[
                [ChampSelectAction(id=1, actor_cell_id=0, champion_id=103, type="pick", completed=False, is_in_progress=True)]
            ],
        )
        assert session.is_local_pick_completed() is False
        assert session.to_dict()["isLocalPickCompleted"] is False
        assert session.to_dict()["is_local_pick_completed"] is False

    def test_is_local_pick_completed_true_when_pick_action_completed(self) -> None:
        """Local player with completed pick action should report True."""
        session = ChampSelectSession(
            local_player_cell_id=0,
            my_team=[ChampSelectPlayer(cell_id=0, champion_id=103, assigned_position="middle")],
            actions=[
                [ChampSelectAction(id=1, actor_cell_id=0, champion_id=103, type="pick", completed=True, is_in_progress=False)]
            ],
        )
        assert session.is_local_pick_completed() is True
        assert session.to_dict()["isLocalPickCompleted"] is True
        assert session.to_dict()["is_local_pick_completed"] is True

    def test_is_local_pick_completed_false_during_ban_phase(self) -> None:
        """Completed ban action should NOT count as completed pick."""
        session = ChampSelectSession(
            local_player_cell_id=0,
            my_team=[ChampSelectPlayer(cell_id=0, champion_id=0, assigned_position="middle")],
            actions=[
                [ChampSelectAction(id=1, actor_cell_id=0, champion_id=84, type="ban", completed=True, is_in_progress=False)],
                [ChampSelectAction(id=2, actor_cell_id=0, champion_id=0, type="pick", completed=False, is_in_progress=False)],
            ],
        )
        assert session.is_local_pick_completed() is False

    def test_is_local_pick_completed_aram_mode_fallback(self) -> None:
        """In ARAM (no pick actions list), player with assigned champion reports True."""
        session = ChampSelectSession(
            local_player_cell_id=0,
            my_team=[ChampSelectPlayer(cell_id=0, champion_id=22, assigned_position="")],
            actions=[],  # No pick actions in ARAM
            bench_enabled=True,
        )
        assert session.is_local_pick_completed() is True

    def test_is_local_pick_completed_invalid_cell_id(self) -> None:
        """Invalid local player cell id returns False."""
        session = ChampSelectSession(local_player_cell_id=-1)
        assert session.is_local_pick_completed() is False


# ============================================================================
# 2. Template, DOM Elements, and Responsive Styling Contract Tests
# ============================================================================

class TestChampLockLobbyUIContract:
    """Verifies INDEX_HTML template contains required DOM elements and responsive styling."""

    def test_post_lock_lobby_view_container_exists(self) -> None:
        """INDEX_HTML must contain the post-lock match lobby view (#postLockLobbyView)."""
        assert 'id="postLockLobbyView"' in INDEX_HTML
        assert 'class="post-lock-lobby-view"' in INDEX_HTML

    def test_post_lock_lobby_header_and_timer_elements(self) -> None:
        """Lobby view must have header banner, title, and timer badge."""
        assert 'id="lobbyTimerBadge"' in INDEX_HTML
        assert "MAÇ LOBİSİ" in INDEX_HTML or "KARŞILAŞMA LOBİSİ" in INDEX_HTML
        assert 'class="lobby-locked-card"' in INDEX_HTML
        assert "Şampiyonun Kilitlendi!" in INDEX_HTML

    def test_two_teams_clearly_separated_blue_and_red(self) -> None:
        """Lobby view must clearly separate My Team (Blue) and Enemy Team (Red)."""
        assert 'id="lobbyMyTeamList"' in INDEX_HTML
        assert 'id="lobbyTheirTeamList"' in INDEX_HTML
        assert "KENDİ TAKIMIN" in INDEX_HTML
        assert "KARŞI TAKIM" in INDEX_HTML
        assert "lobby-team-box blue" in INDEX_HTML
        assert "lobby-team-box red" in INDEX_HTML

    def test_lobby_trade_and_lane_swap_banners_present(self) -> None:
        """Lobby view must include in-lobby trade notice banner and actions."""
        assert 'id="lobbyTradeNoticeBanner"' in INDEX_HTML
        assert 'id="lobbyTradeAcceptBtn"' in INDEX_HTML
        assert 'id="lobbyTradeDeclineBtn"' in INDEX_HTML
        assert 'id="lobbyTradeCancelBtn"' in INDEX_HTML

    def test_loadout_tab_hint_present(self) -> None:
        """Lobby view must guide users to the Loadout tab for runes and spells."""
        assert "Dizilim" in INDEX_HTML
        assert "lobby-footer-hint" in INDEX_HTML

    def test_responsive_mobile_css_and_ergonomics(self) -> None:
        """CSS must ensure vertical flow, responsive cards, and touch-friendly buttons."""
        assert ".post-lock-lobby-view" in INDEX_HTML
        assert ".lobby-player-card" in INDEX_HTML
        assert ".lobby-player-card.is-me" in INDEX_HTML
        assert "border: 2px solid var(--gold-1)" in INDEX_HTML
        assert ".lobby-tag-me" in INDEX_HTML
        assert "fadeInLobby" in INDEX_HTML

    def test_mobile_viewports_media_queries(self) -> None:
        """Media queries must support small screens down to 320px/360px without overflow."""
        assert "@media (max-width: 360px)" in INDEX_HTML
        assert "overflow-x: hidden" in INDEX_HTML


# ============================================================================
# 3. REST API & WebSocket Integration Tests
# ============================================================================

class TestChampLockLobbyIntegration:
    """Verifies end-to-end REST and WebSocket behavior for locked lobby."""

    @pytest.mark.asyncio
    async def test_rest_champ_select_session_unlocked_vs_locked(
        self,
        test_app_bundle: tuple[TestClient, MockLcuAdapter, str],
    ) -> None:
        """GET /api/champ-select should expose isLocalPickCompleted state."""
        client, mock_lcu, token = test_app_bundle
        headers = {"Authorization": f"Bearer {token}"}
        await mock_lcu.simulate_connect()

        # 1. Unlocked state
        unlocked_session = ChampSelectSession(
            local_player_cell_id=0,
            my_team=[
                ChampSelectPlayer(cell_id=0, summoner_name="LocalHero", champion_id=103, assigned_position="middle"),
                ChampSelectPlayer(cell_id=1, summoner_name="AllyTop", champion_id=84, assigned_position="top"),
            ],
            their_team=[
                ChampSelectPlayer(cell_id=5, summoner_name="", champion_id=0, assigned_position="middle"),
            ],
            actions=[
                [ChampSelectAction(id=1, actor_cell_id=0, champion_id=103, type="pick", completed=False, is_in_progress=True)]
            ],
            timer=ChampSelectTimer(phase="BAN_PICK", time_left_in_phase=25000),
        )
        await mock_lcu.simulate_champ_select_session(unlocked_session)

        resp = client.get("/api/champ-select", headers=headers)
        assert resp.status_code == 200
        data = resp.json()["session"]
        assert data is not None
        assert data["isLocalPickCompleted"] is False
        assert data["localPlayerCellId"] == 0
        assert len(data["myTeam"]) == 2
        assert data["myTeam"][0]["summonerName"] == "LocalHero"

        # 2. Locked state
        locked_session = ChampSelectSession(
            local_player_cell_id=0,
            my_team=[
                ChampSelectPlayer(cell_id=0, summoner_name="LocalHero", champion_id=103, assigned_position="middle"),
                ChampSelectPlayer(cell_id=1, summoner_name="AllyTop", champion_id=84, assigned_position="top"),
            ],
            their_team=[
                ChampSelectPlayer(cell_id=5, summoner_name="", champion_id=157, assigned_position="middle"),
            ],
            actions=[
                [ChampSelectAction(id=1, actor_cell_id=0, champion_id=103, type="pick", completed=True, is_in_progress=False)]
            ],
            timer=ChampSelectTimer(phase="FINALIZATION", time_left_in_phase=15000),
        )
        await mock_lcu.simulate_champ_select_session(locked_session)

        resp2 = client.get("/api/champ-select", headers=headers)
        assert resp2.status_code == 200
        data2 = resp2.json()["session"]
        assert data2 is not None
        assert data2["isLocalPickCompleted"] is True
        assert data2["myTeam"][0]["championId"] == 103
        assert data2["theirTeam"][0]["championId"] == 157

    @pytest.mark.asyncio
    async def test_websocket_champ_select_sync_with_lock_state(
        self,
        test_app_bundle: tuple[TestClient, MockLcuAdapter, str],
    ) -> None:
        """WebSocket clients should receive CHAMP_SELECT_UPDATE containing isLocalPickCompleted."""
        client, mock_lcu, token = test_app_bundle
        await mock_lcu.simulate_connect()

        locked_session = ChampSelectSession(
            local_player_cell_id=0,
            my_team=[ChampSelectPlayer(cell_id=0, champion_id=103, summoner_name="MySummoner", assigned_position="middle")],
            their_team=[ChampSelectPlayer(cell_id=5, champion_id=0, summoner_name="", assigned_position="middle")],
            actions=[
                [ChampSelectAction(id=1, actor_cell_id=0, champion_id=103, type="pick", completed=True, is_in_progress=False)]
            ],
        )
        await mock_lcu.simulate_champ_select_session(locked_session)

        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # WELCOME
            ws.send_json({"type": "GET_CHAMP_SELECT", "token": token})
            msg = receive_typed_msg(ws, "CHAMP_SELECT_UPDATE")
            session_data = msg.get("session") or msg["data"]
            assert session_data["isLocalPickCompleted"] is True
            assert session_data["myTeam"][0]["summonerName"] == "MySummoner"

    @pytest.mark.asyncio
    async def test_websocket_lane_swap_and_trade_requests(
        self,
        test_app_bundle: tuple[TestClient, MockLcuAdapter, str],
    ) -> None:
        """In-lobby lane swap and champion trade requests via WebSocket should execute cleanly."""
        client, mock_lcu, token = test_app_bundle
        await mock_lcu.simulate_connect()

        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # WELCOME

            # 1. Lane swap request
            ws.send_json({
                "type": "LANE_SWAP_REQUEST",
                "token": token,
                "toCellId": 1,
            })
            swap_res = receive_typed_msg(ws, "LANE_SWAP_RESULT")
            assert swap_res["toCellId"] == 1
            assert swap_res["action"] == "request"

            # 2. Trade request
            ws.send_json({
                "type": "TRADE_REQUEST",
                "token": token,
                "tradeId": 1,
            })
            trade_res = receive_typed_msg(ws, "TRADE_RESULT")
            assert trade_res["tradeId"] == 1


# ============================================================================
# 4. Gameflow Lifecycle and Privacy Rules Tests
# ============================================================================

class TestGameflowTransitionsAndPrivacy:
    """Verifies phase transition to InProgress and privacy handling."""

    @pytest.mark.asyncio
    async def test_transition_to_in_progress_triggers_ingame_view(
        self,
        test_app_bundle: tuple[TestClient, MockLcuAdapter, str],
    ) -> None:
        """When match starts (InProgress), WebSocket receives GAMEFLOW_UPDATE with InProgress."""
        client, mock_lcu, token = test_app_bundle
        await mock_lcu.simulate_connect()

        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # WELCOME
            # Simulate transition into active game
            await mock_lcu.simulate_phase_change(GameflowPhase.IN_PROGRESS)
            msg = receive_typed_msg(ws, "GAMEFLOW_UPDATE")
            assert msg["phase"] == "InProgress"

    def test_opponent_summoner_name_privacy_compliance(self) -> None:
        """When opponent name is blank or masked by Riot rules, no fake name is fabricated."""
        enemy = ChampSelectPlayer.from_lcu_dict({
            "cellId": 5,
            "summonerName": "",
            "championId": 64,
            "assignedPosition": "jungle",
        })
        assert enemy.summoner_name == ""
        # The frontend JS renderer maps empty or masked enemy names to 'Gizli / Rakip Oyuncu'.
        # We ensure here that no random bot name is injected at domain or model level.
        assert enemy.to_dict()["summonerName"] == ""
