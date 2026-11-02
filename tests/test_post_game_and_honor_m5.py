"""Tests for Milestone 5: Post-Game Scoreboard, Match Summary & Teammate Honor Voting.

Verifies:
- Domain models and parsing logic in `riftpilot.core.post_game`
- `MockLcuAdapter` and `RiotLcuAdapter` implementations of post-game contracts
- `WebHub` REST endpoints and authenticated WebSocket commands for live post-game and honor interactions
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
from riftpilot.core.lcu_port import GameflowPhase
from riftpilot.core.pairing import OTTManager, SessionManager
from riftpilot.core.post_game import (
    EndOfGameStats,
    HonorBallot,
    HonorCandidate,
    HonorCategory,
    PlayerGameStats,
)


class MockTransport(httpx.AsyncBaseTransport):
    """Custom HTTPX mock transport dispatching requests to an async handler."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.handler(request)


# ============================================================================
# 1. Domain Model Unit Tests
# ============================================================================

class TestPostGameDomainModels:
    """Unit tests for post-game scoreboard and honor models."""

    def test_honor_category_titles(self) -> None:
        assert "İyi Kalpli" in HonorCategory.HEART.title_tr
        assert "Lider" in HonorCategory.SHOTCALLER.title_tr
        assert "Sakin" in HonorCategory.COOL.title_tr

    def test_player_game_stats_kda_and_ratio(self) -> None:
        player = PlayerGameStats(
            summoner_id=1,
            summoner_name="Faker",
            champion_id=103,
            kills=10,
            deaths=2,
            assists=6,
            damage_dealt=28500,
            gold_earned=14200,
            minions_killed=210,
            items=[3089, 3020, 3157],
        )
        assert player.kda_formatted == "10/2/6"
        assert player.kda_ratio == 8.0

        # Zero deaths edge case
        perfect_player = PlayerGameStats(kills=5, deaths=0, assists=5)
        assert perfect_player.kda_ratio == 10.0

    def test_end_of_game_stats_parsing_from_lcu(self) -> None:
        raw_lcu_eog = {
            "gameId": 987654321,
            "gameMode": "CLASSIC",
            "gameLength": 1845,  # 30:45
            "teams": [
                {
                    "teamId": 100,
                    "isPlayerTeam": True,
                    "isWinningTeam": True,
                    "players": [
                        {
                            "summonerId": 101,
                            "summonerName": "Faker",
                            "championId": 103,
                            "championName": "Ahri",
                            "isLocalPlayer": True,
                            "stats": {
                                "CHAMPIONS_KILLED": 12,
                                "NUM_DEATHS": 2,
                                "ASSISTS": 8,
                                "TOTAL_DAMAGE_DEALT_TO_CHAMPIONS": 34500,
                                "GOLD_EARNED": 16000,
                                "MINIONS_KILLED": 240,
                                "ITEM0": 3089,
                                "ITEM1": 3020,
                            },
                        },
                        {
                            "summonerId": 102,
                            "summonerName": "Oner",
                            "championId": 64,
                            "championName": "LeeSin",
                            "isLocalPlayer": False,
                            "stats": {
                                "CHAMPIONS_KILLED": 4,
                                "NUM_DEATHS": 3,
                                "ASSISTS": 14,
                                "TOTAL_DAMAGE_DEALT_TO_CHAMPIONS": 18000,
                                "GOLD_EARNED": 12000,
                                "MINIONS_KILLED": 160,
                            },
                        },
                    ],
                },
                {
                    "teamId": 200,
                    "isPlayerTeam": False,
                    "isWinningTeam": False,
                    "players": [
                        {
                            "summonerId": 201,
                            "summonerName": "Chovy",
                            "championId": 238,
                            "championName": "Zed",
                            "isLocalPlayer": False,
                            "stats": {
                                "CHAMPIONS_KILLED": 5,
                                "NUM_DEATHS": 6,
                                "ASSISTS": 2,
                                "TOTAL_DAMAGE_DEALT_TO_CHAMPIONS": 22000,
                            },
                        }
                    ],
                },
            ],
        }

        eog = EndOfGameStats.from_lcu_dict(raw_lcu_eog)
        assert eog is not None
        assert eog.game_id == 987654321
        assert eog.game_mode == "CLASSIC"
        assert eog.game_length_seconds == 1845
        assert eog.duration_formatted == "30:45"
        assert eog.is_victory is True
        assert eog.result_title_tr == "ZAFER"

        assert eog.local_player is not None
        assert eog.local_player.summoner_name == "Faker"
        assert eog.local_player.kills == 12
        assert eog.local_player.is_mvp is True  # highest damage in winning team
        assert len(eog.blue_team) == 2
        assert len(eog.red_team) == 1

    def test_honor_ballot_parsing(self) -> None:
        raw_ballot = {
            "gameId": 987654321,
            "eligiblePlayers": [
                {
                    "summonerId": 102,
                    "summonerName": "Oner",
                    "championId": 64,
                    "championName": "Lee Sin",
                    "role": "JUNGLE",
                },
                {
                    "summonerId": 103,
                    "summonerName": "Gumayusi",
                    "championId": 222,
                    "championName": "Jinx",
                    "role": "BOTTOM",
                },
            ],
        }
        ballot = HonorBallot.from_lcu_dict(raw_ballot)
        assert ballot is not None
        assert ballot.game_id == 987654321
        assert len(ballot.candidates) == 2
        assert ballot.candidates[0].summoner_name == "Oner"
        assert ballot.candidates[1].champion_name == "Jinx"


# ============================================================================
# 2. MockLcuAdapter Post-Game & Honor Tests
# ============================================================================

class TestMockLcuPostGame:
    """Tests for MockLcuAdapter Milestone 5 methods."""

    @pytest.mark.asyncio
    async def test_simulate_eog_and_subscribers(self) -> None:
        mock = MockLcuAdapter()
        await mock.simulate_connect()

        received_stats: list[EndOfGameStats | None] = []
        mock.subscribe_end_of_game(lambda s: received_stats.append(s))

        eog = EndOfGameStats(
            game_id=123,
            game_length_seconds=1500,
            is_victory=True,
            players=[
                PlayerGameStats(
                    summoner_id=1,
                    summoner_name="Player1",
                    is_local_player=True,
                    kills=8,
                    deaths=1,
                    assists=5,
                )
            ],
        )

        await mock.simulate_end_of_game(eog)
        assert len(received_stats) == 1
        assert received_stats[0] == eog
        assert await mock.get_gameflow_phase() == GameflowPhase.END_OF_GAME

        current_eog = await mock.get_end_of_game_stats()
        assert current_eog is not None
        assert current_eog.game_id == 123

        # Unsubscribe test
        assert mock.unsubscribe_end_of_game(lambda s: None) is False

    @pytest.mark.asyncio
    async def test_mock_honor_voting(self) -> None:
        mock = MockLcuAdapter()
        await mock.simulate_connect()

        ballot = HonorBallot(
            game_id=123,
            candidates=[HonorCandidate(summoner_id=2, summoner_name="Teammate")],
        )
        mock.set_honor_ballot(ballot)

        fetched_ballot = await mock.get_honor_ballot()
        assert fetched_ballot is not None
        assert len(fetched_ballot.candidates) == 1

        ok = await mock.honor_player(2, "HEART")
        assert ok is True


# ============================================================================
# 3. RiotLcuAdapter HTTPX Transport Tests
# ============================================================================

class TestRiotLcuPostGame:
    """Tests for RiotLcuAdapter Milestone 5 endpoints against HTTP mock transport."""

    @pytest.mark.asyncio
    async def test_riot_lcu_get_eog_stats(self) -> None:
        calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.url.path == "/lol-end-of-game/v1/eog-stats-block":
                return httpx.Response(
                    200,
                    json={
                        "gameId": 555,
                        "gameMode": "ARAM",
                        "gameLength": 1200,
                        "teams": [{"teamId": 100, "isPlayerTeam": True, "isWinningTeam": True}],
                    },
                )
            return httpx.Response(404)

        creds = LcuCredentials(port=29999, auth_token="test", pid=1, protocol="https")
        adapter = RiotLcuAdapter(credentials=creds, transport=MockTransport(handler))

        stats = await adapter.get_end_of_game_stats()
        assert stats is not None
        assert stats.game_id == 555
        assert stats.game_mode == "ARAM"
        assert stats.is_victory is True
        assert "GET /lol-end-of-game/v1/eog-stats-block" in calls

    @pytest.mark.asyncio
    async def test_riot_lcu_honor_ballot_and_cast_vote(self) -> None:
        payload_received: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/lol-honor-v2/v1/ballot":
                return httpx.Response(
                    200,
                    json={
                        "gameId": 555,
                        "eligiblePlayers": [{"summonerId": 99, "summonerName": "SupportBro"}],
                    },
                )
            if request.url.path == "/lol-honor-v2/v1/honor-player":
                nonlocal payload_received
                payload_received = json.loads(request.content.decode("utf-8"))
                return httpx.Response(204)
            return httpx.Response(404)

        creds = LcuCredentials(port=29999, auth_token="test", pid=1, protocol="https")
        adapter = RiotLcuAdapter(credentials=creds, transport=MockTransport(handler))

        ballot = await adapter.get_honor_ballot()
        assert ballot is not None
        assert len(ballot.candidates) == 1
        assert ballot.candidates[0].summoner_name == "SupportBro"

        ok = await adapter.honor_player(99, "SHOTCALLER")
        assert ok is True
        assert payload_received == {"honorCategory": "SHOTCALLER", "summonerId": 99}


# ============================================================================
# 4. WebHub REST & WebSocket Tests
# ============================================================================

class TestWebHubPostGame:
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
    async def test_rest_post_game_and_honor_flow(
        self, app_bundle: tuple[TestClient, MockLcuAdapter, str]
    ) -> None:
        client, mock_lcu, _ = app_bundle
        await mock_lcu.simulate_connect()

        # 1. Empty EOG stats
        resp1 = client.get("/api/game/eog")
        assert resp1.status_code == 200
        assert resp1.json()["stats"] is None

        # 2. Simulate EOG
        eog = EndOfGameStats(
            game_id=777,
            game_length_seconds=1800,
            is_victory=True,
            players=[
                PlayerGameStats(
                    summoner_id=1,
                    summoner_name="Player1",
                    is_local_player=True,
                    kills=15,
                    deaths=2,
                    assists=7,
                )
            ],
        )
        await mock_lcu.simulate_end_of_game(eog)

        resp2 = client.get("/api/game/eog")
        assert resp2.status_code == 200
        assert resp2.json()["stats"]["game_id"] == 777
        assert resp2.json()["stats"]["is_victory"] is True

        # 3. Honor ballot & vote
        mock_lcu.set_honor_ballot(
            HonorBallot(game_id=777, candidates=[HonorCandidate(summoner_id=42, summoner_name="TopLaner")])
        )
        ballot_resp = client.get("/api/game/honor/ballot")
        assert ballot_resp.status_code == 200
        assert len(ballot_resp.json()["ballot"]["candidates"]) == 1

        vote_resp = client.post(
            "/api/game/honor", json={"summonerId": 42, "honorCategory": "COOL"}
        )
        assert vote_resp.status_code == 200
        assert vote_resp.json()["success"] is True

    @pytest.mark.asyncio
    async def test_websocket_post_game_commands(
        self, app_bundle: tuple[TestClient, MockLcuAdapter, str]
    ) -> None:
        client, mock_lcu, token = app_bundle
        await mock_lcu.simulate_connect()

        with client.websocket_connect(f"/ws?token={token}") as ws:
            welcome = ws.receive_json()
            assert welcome["type"] == "WELCOME"

            # 1. GET_EOG_STATS
            ws.send_json({"type": "GET_EOG_STATS", "token": token})
            eog_msg = ws.receive_json()
            assert eog_msg["type"] == "EOG_STATS_UPDATE"

            # 2. GET_HONOR_BALLOT
            ws.send_json({"type": "GET_HONOR_BALLOT", "token": token})
            ballot_msg = ws.receive_json()
            assert ballot_msg["type"] == "HONOR_BALLOT_UPDATE"

            # 3. HONOR_PLAYER
            ws.send_json(
                {"type": "HONOR_PLAYER", "summonerId": 50, "honorCategory": "HEART", "token": token}
            )
            honor_msg = ws.receive_json()
            assert honor_msg["type"] == "HONOR_RESULT"
            assert honor_msg["summonerId"] == 50
            assert honor_msg["category"] == "HEART"
            assert honor_msg["success"] is True
