"""Tests for Agent C — Champion Trades, Lane Swaps, and Bench Swap Protections.

Validates:
1. Trade initiation validation logic (ownership, valid champion IDs, self-trade prevention).
2. LaneSwapRequest model and position swap validation.
3. TradeState enum and ChampSelectTrade domain parsing.
4. MockLcuAdapter trade lifecycle simulation (request, accept, decline, cancel).
5. MockLcuAdapter lane swap simulation and assigned position swapping.
6. ARAM bench swap protections (ownership/availability on bench, non-zero IDs, disconnect guard).
7. RiotLcuAdapter champion trade REST endpoints via HTTPX MockTransport.
"""

from __future__ import annotations

from typing import Any
import httpx
from httpx import MockTransport
import pytest

from riftpilot.adapters.lcu_discovery import LcuCredentials
from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.riot_lcu import RiotLcuAdapter
from riftpilot.core.champ_select import ChampSelectPlayer, ChampSelectSession
from riftpilot.core.lcu_port import LcuConnectionStatus
from riftpilot.core.lobby import (
    BenchChampion,
    ChampSelectTrade,
    LaneSwapRequest,
    TradeState,
    can_initiate_trade,
    can_swap_lanes,
)


# ============================================================================
# 1. Trade Initiation and Validation Tests
# ============================================================================

class TestTradeInitiationAndValidation:
    """Verifies domain business rules for initiating trades in champion select."""

    def test_can_initiate_trade_success(self) -> None:
        """Valid trade should pass when initiator owns the target champion."""
        owned_champs = {84, 157, 22, 103}
        is_valid, msg = can_initiate_trade(
            cell_id=0,
            target_cell_id=1,
            my_champion_id=157,      # Yasuo
            target_champion_id=84,   # Akali
            owned_champion_ids=owned_champs,
        )
        assert is_valid is True
        assert "available" in msg.lower() or "valid" in msg.lower()

    def test_can_initiate_trade_target_not_owned(self) -> None:
        """Initiator cannot request a trade for a champion they do not own."""
        owned_champs = {157, 22}  # Does not own Akali (84)
        is_valid, msg = can_initiate_trade(
            cell_id=0,
            target_cell_id=1,
            my_champion_id=157,
            target_champion_id=84,
            owned_champion_ids=owned_champs,
        )
        assert is_valid is False
        assert "not owned" in msg.lower()

    def test_can_initiate_trade_self_trade_rejected(self) -> None:
        """Trading champion with oneself must be rejected."""
        owned_champs = {157, 84}
        is_valid, msg = can_initiate_trade(
            cell_id=2,
            target_cell_id=2,
            my_champion_id=157,
            target_champion_id=84,
            owned_champion_ids=owned_champs,
        )
        assert is_valid is False
        assert "yourself" in msg.lower()

    def test_can_initiate_trade_negative_cell_ids(self) -> None:
        """Invalid cell IDs (< 0) must be rejected."""
        owned_champs = {157, 84}
        is_valid1, _ = can_initiate_trade(-1, 2, 157, 84, owned_champs)
        is_valid2, _ = can_initiate_trade(2, -1, 157, 84, owned_champs)
        assert is_valid1 is False
        assert is_valid2 is False

    def test_can_initiate_trade_unselected_initiator_champion(self) -> None:
        """Trade cannot be initiated if initiator has no selected champion."""
        owned_champs = {84}
        is_valid, msg = can_initiate_trade(0, 1, 0, 84, owned_champs)
        assert is_valid is False
        assert "no champion" in msg.lower() or "initiator" in msg.lower()

    def test_can_initiate_trade_unselected_target_champion(self) -> None:
        """Trade cannot be initiated if target has no selected champion."""
        owned_champs = {84}
        is_valid, msg = can_initiate_trade(0, 1, 157, 0, owned_champs)
        assert is_valid is False
        assert "no champion" in msg.lower() or "target" in msg.lower()

    def test_can_initiate_trade_identical_champions(self) -> None:
        """Trading identical champions is meaningless and must be rejected."""
        owned_champs = {157}
        is_valid, msg = can_initiate_trade(0, 1, 157, 157, owned_champs)
        assert is_valid is False
        assert "identical" in msg.lower() or "same" in msg.lower()


# ============================================================================
# 2. Lane Swap Model and Validation Tests
# ============================================================================

class TestLaneSwapModelAndValidation:
    """Verifies LaneSwapRequest model and position swap business rules."""

    def test_lane_swap_request_model_instantiation(self) -> None:
        req = LaneSwapRequest(
            from_cell_id=0,
            to_cell_id=3,
            from_assigned_position="MIDDLE",
            to_assigned_position="TOP",
        )
        assert req.from_cell_id == 0
        assert req.to_cell_id == 3
        assert req.from_assigned_position == "MIDDLE"
        assert req.to_assigned_position == "TOP"
        assert req.status == "PENDING"

    def test_lane_swap_request_from_lcu_dict_camel_and_snake(self) -> None:
        # CamelCase
        raw_camel = {
            "fromCellId": 1,
            "toCellId": 4,
            "fromAssignedPosition": "JUNGLE",
            "toAssignedPosition": "UTILITY",
            "status": "ACCEPTED",
        }
        parsed = LaneSwapRequest.from_lcu_dict(raw_camel)
        assert parsed is not None
        assert parsed.from_cell_id == 1
        assert parsed.to_cell_id == 4
        assert parsed.from_assigned_position == "JUNGLE"
        assert parsed.to_assigned_position == "UTILITY"
        assert parsed.status == "ACCEPTED"

        # Snake_case
        raw_snake = {
            "from_cell_id": 2,
            "to_cell_id": 0,
            "from_assigned_position": "BOTTOM",
            "to_assigned_position": "MIDDLE",
            "status": "PENDING",
        }
        parsed_snake = LaneSwapRequest.from_lcu_dict(raw_snake)
        assert parsed_snake is not None
        assert parsed_snake.from_cell_id == 2
        assert parsed_snake.to_cell_id == 0

    def test_lane_swap_request_from_lcu_dict_invalid(self) -> None:
        assert LaneSwapRequest.from_lcu_dict({}) is None
        assert LaneSwapRequest.from_lcu_dict(None) is None  # type: ignore[arg-type]

    def test_can_swap_lanes_validation(self) -> None:
        assert can_swap_lanes(0, 1) is True
        assert can_swap_lanes(4, 2) is True
        assert can_swap_lanes(1, 1) is False  # Cannot swap with self
        assert can_swap_lanes(-1, 2) is False
        assert can_swap_lanes(2, -1) is False


# ============================================================================
# 3. TradeState and ChampSelectTrade Tests
# ============================================================================

class TestTradeStateAndTradeModel:
    """Verifies TradeState enumeration and ChampSelectTrade model parsing."""

    def test_trade_state_enum_values(self) -> None:
        assert TradeState.AVAILABLE == "AVAILABLE"
        assert TradeState.REQUESTED == "REQUESTED"
        assert TradeState.SENT == "SENT"
        assert TradeState.RECEIVED == "RECEIVED"
        assert TradeState.ACCEPTED == "ACCEPTED"
        assert TradeState.DECLINED == "DECLINED"
        assert TradeState.CANCELED == "CANCELED"

    def test_trade_state_from_str_normalization(self) -> None:
        assert TradeState.from_str("available") == TradeState.AVAILABLE
        assert TradeState.from_str("SENT") == TradeState.SENT
        assert TradeState.from_str("CANCELLED") == TradeState.CANCELED
        assert TradeState.from_str("unknown_state") == TradeState.AVAILABLE
        assert TradeState.from_str(None) == TradeState.AVAILABLE

    def test_champ_select_trade_parsing(self) -> None:
        raw = {"id": 100, "cellId": 3, "state": "RECEIVED"}
        trade = ChampSelectTrade.from_lcu_dict(raw)
        assert trade is not None
        assert trade.trade_id == 100
        assert trade.cell_id == 3
        assert trade.state == TradeState.RECEIVED
        # Direct string equality holds because TradeState is str Enum
        assert trade.state == "RECEIVED"

    def test_champ_select_trade_parsing_with_cancelled_alias(self) -> None:
        raw = {"id": 101, "cellId": 2, "state": "CANCELLED"}
        trade = ChampSelectTrade.from_lcu_dict(raw)
        assert trade is not None
        assert trade.state == TradeState.CANCELED


# ============================================================================
# 4. MockLcuAdapter Trade Lifecycle & Lane Swap Tests
# ============================================================================

class TestMockLcuTradeSimulation:
    """Verifies champion trade and lane swap simulations in MockLcuAdapter."""

    @pytest.mark.asyncio
    async def test_initial_trade_state_and_simulation(self) -> None:
        mock = MockLcuAdapter()
        await mock.simulate_connect()

        trades = await mock.get_trades()
        assert trades == []

        # Simulate incoming trade request from teammate at cell 2
        trade = await mock.simulate_request_trade(trade_id=10, cell_id=2)
        assert trade.trade_id == 10
        assert trade.cell_id == 2
        assert trade.state == TradeState.RECEIVED

        trades_after = await mock.get_trades()
        assert len(trades_after) == 1
        assert trades_after[0].trade_id == 10

    @pytest.mark.asyncio
    async def test_trade_request_and_cancel_lifecycle(self) -> None:
        mock = MockLcuAdapter()
        await mock.simulate_connect()

        # Send/request trade
        ok_req = await mock.request_champ_trade(trade_id=25)
        assert ok_req is True

        trades = await mock.get_trades()
        assert len(trades) == 1
        assert trades[0].trade_id == 25
        assert trades[0].state == TradeState.SENT

        # Cancel the trade
        ok_cancel = await mock.cancel_champ_trade(trade_id=25)
        assert ok_cancel is True

        updated_trades = await mock.get_trades()
        assert updated_trades[0].state == TradeState.CANCELED

    @pytest.mark.asyncio
    async def test_trade_accept_lifecycle_swaps_champions_in_session(self) -> None:
        mock = MockLcuAdapter()
        await mock.simulate_connect()

        # Set up active champ select session with local player and teammate
        session = ChampSelectSession(
            local_player_cell_id=0,
            my_team=[
                ChampSelectPlayer(cell_id=0, champion_id=157, champion_pick_intent=157),  # Yasuo
                ChampSelectPlayer(cell_id=3, champion_id=84, champion_pick_intent=84),    # Akali
            ],
        )
        await mock.simulate_champ_select_session(session)

        # Teammate 3 sends trade
        await mock.simulate_request_trade(trade_id=50, cell_id=3)

        # Accept trade
        ok_accept = await mock.accept_champ_trade(trade_id=50)
        assert ok_accept is True

        # Trade state must be ACCEPTED
        trades = await mock.get_trades()
        assert trades[0].state == TradeState.ACCEPTED

        # Champion IDs in session must have swapped between cell 0 and cell 3
        active_session = await mock.get_champ_select_session()
        assert active_session is not None
        p0 = next((p for p in active_session.my_team if p.cell_id == 0), None)
        p3 = next((p for p in active_session.my_team if p.cell_id == 3), None)
        assert p0 is not None and p3 is not None
        assert p0.champion_id == 84   # Local player now has Akali
        assert p3.champion_id == 157  # Teammate now has Yasuo

    @pytest.mark.asyncio
    async def test_trade_decline_lifecycle(self) -> None:
        mock = MockLcuAdapter()
        await mock.simulate_connect()

        await mock.simulate_request_trade(trade_id=60, cell_id=1)
        ok_decline = await mock.decline_champ_trade(trade_id=60)
        assert ok_decline is True

        trades = await mock.get_trades()
        assert trades[0].state == TradeState.DECLINED

    @pytest.mark.asyncio
    async def test_simulate_lane_swap_updates_assigned_positions(self) -> None:
        mock = MockLcuAdapter()
        await mock.simulate_connect()

        session = ChampSelectSession(
            local_player_cell_id=0,
            my_team=[
                ChampSelectPlayer(cell_id=0, assigned_position="MIDDLE"),
                ChampSelectPlayer(cell_id=2, assigned_position="TOP"),
            ],
        )
        await mock.simulate_champ_select_session(session)

        # Simulate lane swap request between cell 0 and cell 2
        req = await mock.simulate_request_lane_swap(from_cell_id=0, to_cell_id=2)
        assert req.from_cell_id == 0
        assert req.to_cell_id == 2
        assert req.from_assigned_position == "MIDDLE"
        assert req.to_assigned_position == "TOP"

        # Check recorded swap requests
        all_reqs = mock.get_lane_swap_requests()
        assert len(all_reqs) == 1
        assert all_reqs[0] == req

        # Check that session teammate positions swapped
        active_session = await mock.get_champ_select_session()
        assert active_session is not None
        p0 = next((p for p in active_session.my_team if p.cell_id == 0), None)
        p2 = next((p for p in active_session.my_team if p.cell_id == 2), None)
        assert p0 is not None and p2 is not None
        assert p0.assigned_position == "TOP"
        assert p2.assigned_position == "MIDDLE"

    @pytest.mark.asyncio
    async def test_disconnected_trade_methods_return_safe_defaults(self) -> None:
        mock = MockLcuAdapter()
        # Disconnected state
        assert await mock.get_trades() == []
        assert await mock.request_champ_trade(1) is False
        assert await mock.accept_champ_trade(1) is False
        assert await mock.decline_champ_trade(1) is False
        assert await mock.cancel_champ_trade(1) is False


# ============================================================================
# 5. ARAM Bench Swap Protections Tests
# ============================================================================

class TestAramBenchSwapProtections:
    """Verifies validation and protection checks for ARAM bench swaps."""

    @pytest.mark.asyncio
    async def test_bench_swap_success_when_champion_on_bench(self) -> None:
        mock = MockLcuAdapter()
        await mock.simulate_connect()

        mock.set_bench_champions([
            BenchChampion(champion_id=22, champion_name="Ashe"),
            BenchChampion(champion_id=1, champion_name="Annie"),
        ])

        session = ChampSelectSession(
            local_player_cell_id=0,
            my_team=[ChampSelectPlayer(cell_id=0, champion_id=103)],  # Ahri
        )
        await mock.simulate_champ_select_session(session)

        # Swap with Ashe (22)
        ok = await mock.bench_swap(22)
        assert ok is True

        bench = await mock.get_bench_champions()
        bench_ids = {b.champion_id for b in bench}
        assert 22 not in bench_ids
        assert 103 in bench_ids  # Ahri pushed to bench

        active_session = await mock.get_champ_select_session()
        assert active_session is not None
        p0 = next((p for p in active_session.my_team if p.cell_id == 0), None)
        assert p0 is not None
        assert p0.champion_id == 22

    @pytest.mark.asyncio
    async def test_bench_swap_protection_champion_not_on_bench(self) -> None:
        """Attempting to swap a champion not present on the bench must return False."""
        mock = MockLcuAdapter()
        await mock.simulate_connect()

        mock.set_bench_champions([BenchChampion(champion_id=22, champion_name="Ashe")])
        ok = await mock.bench_swap(999)  # Not on bench
        assert ok is False

    @pytest.mark.asyncio
    async def test_bench_swap_protection_invalid_champion_id(self) -> None:
        """Attempting to swap with champion_id <= 0 must immediately return False."""
        mock = MockLcuAdapter()
        await mock.simulate_connect()

        assert await mock.bench_swap(0) is False
        assert await mock.bench_swap(-5) is False

    @pytest.mark.asyncio
    async def test_bench_swap_protection_disconnected_adapter(self) -> None:
        """Attempting to bench swap while disconnected must return False."""
        mock = MockLcuAdapter()
        assert await mock.bench_swap(22) is False


# ============================================================================
# 6. RiotLcuAdapter Champion Trade Endpoints Tests (HTTP MockTransport)
# ============================================================================

class TestRiotLcuTradeEndpoints:
    """Verifies that RiotLcuAdapter issues the correct LCU REST API calls for trades."""

    @pytest.mark.asyncio
    async def test_riot_lcu_trade_endpoints_lifecycle(self) -> None:
        calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.url.path.startswith("/lol-champ-select/v1/session/trades/"):
                return httpx.Response(204)
            return httpx.Response(404)

        creds = LcuCredentials(port=29999, auth_token="test-token", pid=1, protocol="https")
        adapter = RiotLcuAdapter(credentials=creds, transport=MockTransport(handler))

        # Test request trade
        res_req = await adapter.request_champ_trade(trade_id=101)
        assert res_req is True
        assert "POST /lol-champ-select/v1/session/trades/101/request" in calls

        # Test accept trade
        res_acc = await adapter.accept_champ_trade(trade_id=102)
        assert res_acc is True
        assert "POST /lol-champ-select/v1/session/trades/102/accept" in calls

        # Test decline trade
        res_dec = await adapter.decline_champ_trade(trade_id=103)
        assert res_dec is True
        assert "POST /lol-champ-select/v1/session/trades/103/decline" in calls

        # Test cancel trade
        res_can = await adapter.cancel_champ_trade(trade_id=104)
        assert res_can is True
        assert "POST /lol-champ-select/v1/session/trades/104/cancel" in calls

        await adapter.close()

    @pytest.mark.asyncio
    async def test_riot_lcu_trade_endpoint_aliases(self) -> None:
        """Verify that legacy/shorthand aliases route to the same endpoints."""
        calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            return httpx.Response(200)

        creds = LcuCredentials(port=29999, auth_token="test-token", pid=1, protocol="https")
        adapter = RiotLcuAdapter(credentials=creds, transport=MockTransport(handler))

        assert await adapter.request_trade(1) is True
        assert await adapter.accept_trade(2) is True
        assert await adapter.decline_trade(3) is True
        assert await adapter.cancel_trade(4) is True

        assert "POST /lol-champ-select/v1/session/trades/1/request" in calls
        assert "POST /lol-champ-select/v1/session/trades/2/accept" in calls
        assert "POST /lol-champ-select/v1/session/trades/3/decline" in calls
        assert "POST /lol-champ-select/v1/session/trades/4/cancel" in calls

        await adapter.close()

    @pytest.mark.asyncio
    async def test_riot_lcu_trade_endpoints_http_errors_handled_gracefully(self) -> None:
        """Verify 404 or 500 error responses from LCU return False without unhandled exceptions."""
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"message": "Internal Client Error"})

        creds = LcuCredentials(port=29999, auth_token="test-token", pid=1, protocol="https")
        adapter = RiotLcuAdapter(credentials=creds, transport=MockTransport(handler))

        assert await adapter.request_champ_trade(99) is False
        assert await adapter.accept_champ_trade(99) is False
        assert await adapter.decline_champ_trade(99) is False
        assert await adapter.cancel_champ_trade(99) is False

        await adapter.close()

    @pytest.mark.asyncio
    async def test_riot_lcu_trade_endpoints_disconnected(self) -> None:
        """Disconnected RiotLcuAdapter must reject all trade requests immediately."""
        adapter = RiotLcuAdapter(credentials=None, discoverer=lambda: None)
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED

        assert await adapter.request_champ_trade(1) is False
        assert await adapter.accept_champ_trade(1) is False
        assert await adapter.decline_champ_trade(1) is False
        assert await adapter.cancel_champ_trade(1) is False
        assert await adapter.bench_swap(0) is False

        await adapter.close()
