"""Tests for Milestone 4: Party Lobby Management, Matchmaking & ARAM Bench/Trade Orchestration.

Verifies:
- Domain models and parsing contracts in `riftpilot.core.lobby`
- `MockLcuAdapter` and `RiotLcuAdapter` implementations of lobby and bench contracts
- `WebHub` REST API and authenticated WebSocket commands for remote lobby and ARAM interactions
"""

from __future__ import annotations

import json
from typing import Any
import httpx
import pytest
from starlette.testclient import TestClient

from riftpilot.adapters.lcu_discovery import LcuCredentials
from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.riot_lcu import RiotLcuAdapter
from riftpilot.adapters.web_hub import create_app
from riftpilot.core.champ_select import ChampSelectPlayer, ChampSelectSession
from riftpilot.core.lcu_port import GameflowPhase, LcuConnectionStatus
from riftpilot.core.lobby import (
    BenchChampion,
    ChampSelectTrade,
    LobbyData,
    LobbyMember,
    MatchmakingSearchState,
    PositionPreference,
)
from riftpilot.core.pairing import OTTManager, SessionManager


class MockTransport(httpx.AsyncBaseTransport):
    """Custom HTTPX mock transport dispatching requests to an async handler."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.handler(request)


# ============================================================================
# 1. Domain Model Unit Tests
# ============================================================================

class TestLobbyDomainModels:
    """Unit tests for lobby data structures and serialization."""

    def test_position_preference_normalization(self) -> None:
        assert PositionPreference.from_str("MID") == PositionPreference.MIDDLE
        assert PositionPreference.from_str("middle") == PositionPreference.MIDDLE
        assert PositionPreference.from_str("top") == PositionPreference.TOP
        assert PositionPreference.from_str("JUG") == PositionPreference.JUNGLE
        assert PositionPreference.from_str("jungle") == PositionPreference.JUNGLE
        assert PositionPreference.from_str("bot") == PositionPreference.BOTTOM
        assert PositionPreference.from_str("adc") == PositionPreference.BOTTOM
        assert PositionPreference.from_str("supp") == PositionPreference.UTILITY
        assert PositionPreference.from_str("support") == PositionPreference.UTILITY
        assert PositionPreference.from_str("fill") == PositionPreference.FILL
        assert PositionPreference.from_str("unknown") == PositionPreference.UNSELECTED
        assert PositionPreference.from_str(None) == PositionPreference.UNSELECTED

    def test_position_preference_display_names(self) -> None:
        assert PositionPreference.TOP.display_name_tr == "Üst Koridor"
        assert PositionPreference.MIDDLE.display_name_tr == "Orta Koridor"
        assert PositionPreference.BOTTOM.display_name_tr == "Alt Koridor"
        assert PositionPreference.JUNGLE.display_name_tr == "Orman"
        assert PositionPreference.UTILITY.display_name_tr == "Destek"
        assert PositionPreference.FILL.display_name_tr == "Doldur"

    def test_lobby_member_from_lcu_dict(self) -> None:
        raw = {
            "summonerId": 12345,
            "summonerName": "Faker",
            "puuid": "puuid-123",
            "isLeader": True,
            "isLocalMember": True,
            "firstPositionPreference": "MID",
            "secondPositionPreference": "TOP",
            "ready": True,
        }
        member = LobbyMember.from_lcu_dict(raw)
        assert member.summoner_id == 12345
        assert member.summoner_name == "Faker"
        assert member.is_leader is True
        assert member.is_local_member is True
        assert member.first_position_preference == PositionPreference.MIDDLE
        assert member.second_position_preference == PositionPreference.TOP
        assert member.is_ready is True

    def test_lobby_data_from_lcu_dict(self) -> None:
        raw = {
            "gameConfig": {
                "queueId": 420,
                "gameMode": "CLASSIC",
                "isCustom": False,
            },
            "canStartActivity": True,
            "members": [
                {
                    "summonerId": 101,
                    "summonerName": "Captain",
                    "isLeader": True,
                    "isLocalMember": True,
                    "firstPositionPreference": "MID",
                    "secondPositionPreference": "TOP",
                },
                {
                    "summonerId": 102,
                    "summonerName": "Teammate",
                    "isLeader": False,
                    "isLocalMember": False,
                    "firstPositionPreference": "BOT",
                    "secondPositionPreference": "SUPP",
                },
            ],
        }
        lobby = LobbyData.from_lcu_dict(raw)
        assert lobby is not None
        assert lobby.queue_id == 420
        assert lobby.game_mode == "CLASSIC"
        assert lobby.can_start_activity is True
        assert lobby.is_leader is True
        assert lobby.party_size == 2
        assert lobby.local_member is not None
        assert lobby.local_member.summoner_name == "Captain"

    def test_matchmaking_search_state_parsing(self) -> None:
        raw = {
            "isCurrentlyInQueue": True,
            "searchState": "Searching",
            "timeInQueue": 14.5,
            "estimatedDelay": 60.0,
            "errors": [{"message": "High queue time"}],
        }
        search = MatchmakingSearchState.from_lcu_dict(raw)
        assert search.is_in_queue is True
        assert search.search_state == "Searching"
        assert search.time_in_queue == 14.5
        assert search.estimated_delay == 60.0
        assert "High queue time" in search.errors

    def test_champ_select_trade_parsing(self) -> None:
        raw = {"id": 10, "cellId": 3, "state": "AVAILABLE"}
        trade = ChampSelectTrade.from_lcu_dict(raw)
        assert trade is not None
        assert trade.trade_id == 10
        assert trade.cell_id == 3
        assert trade.state == "AVAILABLE"


# ============================================================================
# 2. MockLcuAdapter Lobby & Bench Tests
# ============================================================================

class TestMockLcuLobbyAndBench:
    """Tests for MockLcuAdapter Milestone 4 methods."""

    @pytest.mark.asyncio
    async def test_initial_lobby_is_none(self) -> None:
        mock = MockLcuAdapter()
        assert await mock.get_lobby() is None
        await mock.simulate_connect()
        assert await mock.get_lobby() is None

    @pytest.mark.asyncio
    async def test_simulate_lobby_and_matchmaking_lifecycle(self) -> None:
        mock = MockLcuAdapter()
        await mock.simulate_connect()

        received_lobbies: list[LobbyData | None] = []
        mock.subscribe_lobby(lambda l: received_lobbies.append(l))

        lobby = LobbyData(
            queue_id=420,
            game_mode="CLASSIC",
            is_leader=True,
            can_start_activity=True,
            members=[
                LobbyMember(
                    summoner_id=1,
                    summoner_name="Player1",
                    is_leader=True,
                    is_local_member=True,
                )
            ],
        )
        await mock.simulate_lobby(lobby)
        assert len(received_lobbies) == 1
        assert received_lobbies[0] == lobby
        assert await mock.get_gameflow_phase() == GameflowPhase.LOBBY

        # Start matchmaking
        ok_start = await mock.start_matchmaking()
        assert ok_start is True
        assert await mock.get_gameflow_phase() == GameflowPhase.MATCHMAKING
        search_state = await mock.get_matchmaking_search()
        assert search_state is not None
        assert search_state.is_in_queue is True

        # Cancel matchmaking
        ok_cancel = await mock.cancel_matchmaking()
        assert ok_cancel is True
        assert await mock.get_gameflow_phase() == GameflowPhase.LOBBY

    @pytest.mark.asyncio
    async def test_set_position_preferences_updates_local_member(self) -> None:
        mock = MockLcuAdapter()
        await mock.simulate_connect()

        lobby = LobbyData(
            queue_id=420,
            is_leader=True,
            members=[
                LobbyMember(
                    summoner_id=1,
                    is_leader=True,
                    is_local_member=True,
                    first_position_preference=PositionPreference.UNSELECTED,
                )
            ],
        )
        await mock.simulate_lobby(lobby)

        ok = await mock.set_position_preferences("MID", "TOP")
        assert ok is True
        current_lobby = await mock.get_lobby()
        assert current_lobby is not None
        assert current_lobby.local_member is not None
        assert current_lobby.local_member.first_position_preference == PositionPreference.MIDDLE
        assert current_lobby.local_member.second_position_preference == PositionPreference.TOP

    @pytest.mark.asyncio
    async def test_aram_bench_swap_and_reroll(self) -> None:
        mock = MockLcuAdapter()
        await mock.simulate_connect()

        # Initial bench setup
        mock.set_bench_champions([BenchChampion(champion_id=22, champion_name="Ashe")])
        bench = await mock.get_bench_champions()
        assert len(bench) == 1
        assert bench[0].champion_id == 22

        # Create active champ select session with local player
        session = ChampSelectSession(
            local_player_cell_id=0,
            my_team=[ChampSelectPlayer(cell_id=0, champion_id=103)],
        )
        await mock.simulate_champ_select_session(session)

        # Bench swap with champion 22
        ok_swap = await mock.bench_swap(22)
        assert ok_swap is True
        updated_bench = await mock.get_bench_champions()
        bench_ids = {b.champion_id for b in updated_bench}
        assert 22 not in bench_ids
        assert 103 in bench_ids

        # Reroll
        ok_reroll = await mock.reroll()
        assert ok_reroll is True


# ============================================================================
# 3. RiotLcuAdapter HTTPX Transport Tests
# ============================================================================

class TestRiotLcuLobbyAndBench:
    """Tests for RiotLcuAdapter Milestone 4 endpoints against HTTP mock transport."""

    @pytest.mark.asyncio
    async def test_riot_lcu_get_lobby_and_matchmaking_search(self) -> None:
        calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.url.path == "/lol-lobby/v2/lobby":
                return httpx.Response(
                    200,
                    json={
                        "gameConfig": {"queueId": 420, "gameMode": "CLASSIC"},
                        "canStartActivity": True,
                        "members": [{"summonerId": 1, "isLocalMember": True, "isLeader": True}],
                    },
                )
            if request.url.path == "/lol-matchmaking/v1/search":
                return httpx.Response(
                    200,
                    json={"isCurrentlyInQueue": True, "searchState": "Searching", "timeInQueue": 10.0},
                )
            return httpx.Response(404)

        creds = LcuCredentials(port=29999, auth_token="test", pid=1, protocol="https")
        adapter = RiotLcuAdapter(credentials=creds, transport=MockTransport(handler))

        lobby = await adapter.get_lobby()
        assert lobby is not None
        assert lobby.queue_id == 420
        assert lobby.is_leader is True

        search = await adapter.get_matchmaking_search()
        assert search is not None
        assert search.is_in_queue is True
        assert "GET /lol-lobby/v2/lobby" in calls
        assert "GET /lol-matchmaking/v1/search" in calls

    @pytest.mark.asyncio
    async def test_riot_lcu_start_and_cancel_matchmaking(self) -> None:
        calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.url.path == "/lol-lobby/v2/lobby/matchmaking/search":
                return httpx.Response(204)
            return httpx.Response(404)

        creds = LcuCredentials(port=29999, auth_token="test", pid=1, protocol="https")
        adapter = RiotLcuAdapter(credentials=creds, transport=MockTransport(handler))

        assert await adapter.start_matchmaking() is True
        assert "POST /lol-lobby/v2/lobby/matchmaking/search" in calls

        assert await adapter.cancel_matchmaking() is True
        assert "DELETE /lol-lobby/v2/lobby/matchmaking/search" in calls

    @pytest.mark.asyncio
    async def test_riot_lcu_set_position_preferences(self) -> None:
        payload_sent: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/lol-lobby/v2/lobby/members/localMember/position-preferences":
                nonlocal payload_sent
                payload_sent = json.loads(request.content.decode("utf-8"))
                return httpx.Response(204)
            return httpx.Response(404)

        creds = LcuCredentials(port=29999, auth_token="test", pid=1, protocol="https")
        adapter = RiotLcuAdapter(credentials=creds, transport=MockTransport(handler))

        ok = await adapter.set_position_preferences("MIDDLE", "TOP")
        assert ok is True
        assert payload_sent == {"firstPreference": "MIDDLE", "secondPreference": "TOP"}

    @pytest.mark.asyncio
    async def test_riot_lcu_bench_swap_and_reroll(self) -> None:
        calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.url.path == "/lol-champ-select/v1/session/bench/swap/55":
                return httpx.Response(204)
            if request.url.path == "/lol-champ-select/v1/session/my-selection/reroll":
                return httpx.Response(200)
            return httpx.Response(404)

        creds = LcuCredentials(port=29999, auth_token="test", pid=1, protocol="https")
        adapter = RiotLcuAdapter(credentials=creds, transport=MockTransport(handler))

        assert await adapter.bench_swap(55) is True
        assert "POST /lol-champ-select/v1/session/bench/swap/55" in calls

        assert await adapter.reroll() is True
        assert "POST /lol-champ-select/v1/session/my-selection/reroll" in calls


# ============================================================================
# 4. WebHub REST & WebSocket Tests
# ============================================================================

class TestWebHubLobbyAndBench:
    """Tests for WebHub REST endpoints and authenticated WebSocket commands."""

    @pytest.fixture
    def app_bundle(self) -> tuple[TestClient, MockLcuAdapter, str]:
        ott_mgr = OTTManager()
        session_mgr = SessionManager()
        lcu = MockLcuAdapter()
        app = create_app(ott_manager=ott_mgr, session_manager=session_mgr, lcu_adapter=lcu)
        client = TestClient(app)

        ott = ott_mgr.generate_ott()
        pair_resp = client.post("/api/pair", json={"ott": ott.token, "deviceName": "TestPhone"})
        token = pair_resp.json()["session_token"]
        return client, lcu, token

    @pytest.mark.asyncio
    async def test_rest_lobby_and_matchmaking_flow(
        self, app_bundle: tuple[TestClient, MockLcuAdapter, str]
    ) -> None:
        client, mock_lcu, _ = app_bundle
        await mock_lcu.simulate_connect()

        # 1. Initially empty lobby
        resp1 = client.get("/api/lobby")
        assert resp1.status_code == 200
        assert resp1.json()["lobby"] is None

        # 2. Simulate lobby
        lobby = LobbyData(
            queue_id=420,
            game_mode="CLASSIC",
            is_leader=True,
            can_start_activity=True,
            members=[LobbyMember(summoner_id=1, is_leader=True, is_local_member=True)],
        )
        await mock_lcu.simulate_lobby(lobby)

        resp2 = client.get("/api/lobby")
        assert resp2.status_code == 200
        assert resp2.json()["lobby"]["queue_id"] == 420
        assert resp2.json()["lobby"]["is_leader"] is True

        # 3. Set preferences
        pref_resp = client.put(
            "/api/lobby/preferences", json={"first": "MIDDLE", "second": "TOP"}
        )
        assert pref_resp.status_code == 200
        assert pref_resp.json()["success"] is True

        # 4. Start matchmaking
        start_resp = client.post("/api/lobby/matchmaking/start")
        assert start_resp.status_code == 200
        assert start_resp.json()["success"] is True

        # 5. Cancel matchmaking
        cancel_resp = client.post("/api/lobby/matchmaking/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["success"] is True

    @pytest.mark.asyncio
    async def test_rest_aram_bench_and_reroll(
        self, app_bundle: tuple[TestClient, MockLcuAdapter, str]
    ) -> None:
        client, mock_lcu, _ = app_bundle
        await mock_lcu.simulate_connect()

        # Bench query
        bench_resp = client.get("/api/champ-select/bench")
        assert bench_resp.status_code == 200
        assert len(bench_resp.json()["bench"]) >= 1

        # Bench swap
        swap_resp = client.post("/api/champ-select/bench/swap", json={"championId": 22})
        assert swap_resp.status_code == 200
        assert swap_resp.json()["success"] is True

        # Reroll
        reroll_resp = client.post("/api/champ-select/reroll")
        assert reroll_resp.status_code == 200
        assert reroll_resp.json()["success"] is True

    def test_websocket_lobby_commands(
        self, app_bundle: tuple[TestClient, MockLcuAdapter, str]
    ) -> None:
        client, mock_lcu, token = app_bundle

        with client.websocket_connect(f"/ws?token={token}") as ws:
            welcome = ws.receive_json()
            assert welcome["type"] == "WELCOME"

            # 1. GET_LOBBY
            ws.send_json({"type": "GET_LOBBY", "token": token})
            lobby_msg = ws.receive_json()
            assert lobby_msg["type"] == "LOBBY_UPDATE"

            # 2. START_MATCHMAKING
            ws.send_json({"type": "START_MATCHMAKING", "token": token})
            start_msg = ws.receive_json()
            assert start_msg["type"] == "MATCHMAKING_STARTED"

            # 3. CANCEL_MATCHMAKING
            ws.send_json({"type": "CANCEL_MATCHMAKING", "token": token})
            cancel_msg = ws.receive_json()
            assert cancel_msg["type"] == "MATCHMAKING_CANCELED"

            # 4. BENCH_SWAP
            ws.send_json({"type": "BENCH_SWAP", "championId": 22, "token": token})
            bench_msg = ws.receive_json()
            assert bench_msg["type"] == "BENCH_SWAP_RESULT"
            assert bench_msg["championId"] == 22

            # 5. REROLL
            ws.send_json({"type": "REROLL", "token": token})
            reroll_msg = ws.receive_json()
            assert reroll_msg["type"] == "REROLL_RESULT"
