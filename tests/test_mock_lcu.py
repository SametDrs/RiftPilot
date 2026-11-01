"""RiftPilot — Tests for Outbound LCU Port Contract & Mock LCU Server (Milestone 1, Task 1.5).

This module defines comprehensive TDD tests for:
- `GameflowPhase`: Enum containing all official League of Legends client phases.
- `LcuConnectionStatus`: Enum representing client connection states.
- `ILcuPort`: Outbound port contract in Hexagonal Architecture (ADR-0003).
- `MockLcuAdapter`: Test double adapter simulating the League Client (LCU)
  without requiring a running League of Legends game client.

Shift-Left / Test-First Approach:
Target modules:
- `src/riftpilot/core/lcu_port.py`
- `src/riftpilot/adapters/mock_lcu.py`
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable

import pytest

# Target imports (expected to fail with ModuleNotFoundError until Task 1.5 is implemented)
from riftpilot.core.lcu_port import (
    GameflowPhase,
    ILcuPort,
    LcuConnectionStatus,
)
from riftpilot.adapters.mock_lcu import MockLcuAdapter


# ============================================================================
# Pytest Asyncio Compatibility Hook
# ============================================================================
# If pytest-asyncio is not installed in the current environment, provide a fallback
# test runner so that async tests marked with @pytest.mark.asyncio run transparently.
try:
    import pytest_asyncio  # noqa: F401
except ImportError:

    def pytest_pyfunc_call(pyfuncitem: Any) -> bool | None:
        if inspect.iscoroutinefunction(pyfuncitem.obj):
            testargs = {
                arg: pyfuncitem.funcargs[arg]
                for arg in pyfuncitem._fixtureinfo.argnames
            }
            asyncio.run(pyfuncitem.obj(**testargs))
            return True
        return None


# ============================================================================
# Test Helpers
# ============================================================================


async def _maybe_await(val_or_coro: Any) -> Any:
    """Helper to resolve either synchronous return values or coroutines."""
    if inspect.isawaitable(val_or_coro):
        return await val_or_coro
    return val_or_coro


def _extract_ready_check_status_str(adapter: MockLcuAdapter) -> str | None:
    """Helper to read player response or status from various adapter property conventions."""
    for attr in (
        "ready_check_status",
        "ready_check_state",
        "player_response",
        "ready_check_response",
    ):
        if hasattr(adapter, attr):
            val = getattr(adapter, attr)
            if isinstance(val, str):
                return val
            if hasattr(val, "value"):
                return str(val.value)

    rc = getattr(adapter, "ready_check", None)
    if isinstance(rc, dict):
        return rc.get("playerResponse") or rc.get("status") or rc.get("state")
    if hasattr(rc, "player_response"):
        return str(rc.player_response)
    if hasattr(rc, "status"):
        return str(rc.status)
    return None


async def _assert_ready_check_marked_accepted(adapter: MockLcuAdapter) -> None:
    """Verifies that ready check state in the adapter is marked as Accepted."""
    status_str = _extract_ready_check_status_str(adapter)
    if status_str is None and hasattr(adapter, "get_ready_check"):
        rc = await _maybe_await(adapter.get_ready_check())
        if isinstance(rc, dict):
            status_str = (
                rc.get("playerResponse") or rc.get("status") or rc.get("state")
            )
        elif hasattr(rc, "player_response"):
            status_str = str(rc.player_response)
        elif hasattr(rc, "status"):
            status_str = str(rc.status)

    assert status_str is not None, (
        "MockLcuAdapter should record the ready check response state."
    )
    assert "accept" in status_str.lower(), (
        f"Expected ready check status to be 'Accepted', got '{status_str}'."
    )


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_lcu() -> MockLcuAdapter:
    """Provides a fresh MockLcuAdapter instance."""
    return MockLcuAdapter()


# ============================================================================
# 1. GameflowPhase & LcuConnectionStatus Enum Tests
# ============================================================================


class TestLcuDomainEnums:
    """Verifies GameflowPhase and LcuConnectionStatus domain enums."""

    def test_gameflow_phase_all_official_phases_defined(self) -> None:
        """All official Riot LCU gameflow phases must be defined in GameflowPhase enum."""
        required_phases = [
            "None",
            "Lobby",
            "Matchmaking",
            "ReadyCheck",
            "ChampSelect",
            "InProgress",
            "WaitingForStats",
            "EndOfGame",
        ]

        # Check members can be accessed by uppercase attribute or exact name
        for phase_name in required_phases:
            # Check uppercase member name (e.g. GameflowPhase.READY_CHECK, GameflowPhase.NONE)
            snake_name = (
                phase_name.replace("None", "NONE")
                .replace("Lobby", "LOBBY")
                .replace("Matchmaking", "MATCHMAKING")
                .replace("ReadyCheck", "READY_CHECK")
                .replace("ChampSelect", "CHAMP_SELECT")
                .replace("InProgress", "IN_PROGRESS")
                .replace("WaitingForStats", "WAITING_FOR_STATS")
                .replace("EndOfGame", "END_OF_GAME")
            )

            has_attr = hasattr(GameflowPhase, snake_name) or hasattr(
                GameflowPhase, phase_name
            )
            assert has_attr, (
                f"GameflowPhase enum is missing member for '{phase_name}' (expected '{snake_name}')"
            )

    def test_gameflow_phase_values_match_official_lcu_strings(self) -> None:
        """Enum values should match the official string representations used by Riot LCU."""
        expected_mappings = {
            "NONE": "None",
            "LOBBY": "Lobby",
            "MATCHMAKING": "Matchmaking",
            "READY_CHECK": "ReadyCheck",
            "CHAMP_SELECT": "ChampSelect",
            "IN_PROGRESS": "InProgress",
            "WAITING_FOR_STATS": "WaitingForStats",
            "END_OF_GAME": "EndOfGame",
        }

        for attr_name, expected_value in expected_mappings.items():
            if hasattr(GameflowPhase, attr_name):
                enum_item = getattr(GameflowPhase, attr_name)
            else:
                enum_item = getattr(GameflowPhase, expected_value)

            assert enum_item.value == expected_value or str(enum_item) == expected_value

    def test_gameflow_phase_instantiation_from_value(self) -> None:
        """GameflowPhase should be instantiable from its official LCU string value."""
        assert GameflowPhase("None") in (
            getattr(GameflowPhase, "NONE", None),
            getattr(GameflowPhase, "None", None),
        )
        assert GameflowPhase("ReadyCheck") in (
            getattr(GameflowPhase, "READY_CHECK", None),
            getattr(GameflowPhase, "ReadyCheck", None),
        )
        assert GameflowPhase("ChampSelect") in (
            getattr(GameflowPhase, "CHAMP_SELECT", None),
            getattr(GameflowPhase, "ChampSelect", None),
        )

    def test_lcu_connection_status_members(self) -> None:
        """LcuConnectionStatus must define DISCONNECTED, CONNECTING, and CONNECTED states."""
        assert hasattr(LcuConnectionStatus, "DISCONNECTED")
        assert hasattr(LcuConnectionStatus, "CONNECTING")
        assert hasattr(LcuConnectionStatus, "CONNECTED")

        # Ensure states are distinct
        assert (
            LcuConnectionStatus.DISCONNECTED != LcuConnectionStatus.CONNECTED
        )
        assert (
            LcuConnectionStatus.CONNECTING != LcuConnectionStatus.CONNECTED
        )


# ============================================================================
# 2. ILcuPort Contract & Inheritance Tests
# ============================================================================


class TestILcuPortContract:
    """Verifies that ILcuPort enforces outbound port contract in Hexagonal Architecture."""

    def test_ilcu_port_is_abstract(self) -> None:
        """ILcuPort must be an abstract interface and cannot be directly instantiated."""
        with pytest.raises(TypeError):
            ILcuPort()  # type: ignore[abstract]

    def test_mock_adapter_implements_ilcu_port(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """MockLcuAdapter must inherit from and implement ILcuPort."""
        assert issubclass(MockLcuAdapter, ILcuPort)
        assert isinstance(mock_lcu, ILcuPort)

    def test_mock_adapter_has_required_port_methods(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """MockLcuAdapter must expose all essential port methods."""
        required_methods = [
            "get_connection_status",
            "get_gameflow_phase",
            "accept_ready_check",
            "subscribe_phase_change",
            "get_champ_select_session",
            "patch_champ_select_action",
            "get_grid_champions",
            "subscribe_champ_select",
            "unsubscribe_champ_select",
            "simulate_connect",
            "simulate_disconnect",
            "simulate_phase_change",
            "simulate_ready_check",
            "simulate_champ_select_session",
            "set_grid_champions",
        ]
        for method_name in required_methods:
            assert hasattr(mock_lcu, method_name), (
                f"MockLcuAdapter is missing method '{method_name}'"
            )
            assert callable(getattr(mock_lcu, method_name)), (
                f"MockLcuAdapter attribute '{method_name}' is not callable"
            )


# ============================================================================
# 3. MockLcuAdapter Connection Lifecycle Tests
# ============================================================================


class TestMockLcuConnectionLifecycle:
    """Tests connection state management and lifecycle of MockLcuAdapter."""

    @pytest.mark.asyncio
    async def test_initial_state_disconnected_and_none(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Initially, MockLcuAdapter should be DISCONNECTED and in GameflowPhase.NONE."""
        status = await _maybe_await(mock_lcu.get_connection_status())
        phase = await _maybe_await(mock_lcu.get_gameflow_phase())

        assert status == LcuConnectionStatus.DISCONNECTED
        assert (
            phase == getattr(GameflowPhase, "NONE", None)
            or phase == getattr(GameflowPhase, "None", None)
            or phase == "None"
        )

    @pytest.mark.asyncio
    async def test_simulate_connect_transitions_to_connected(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """simulate_connect() transitions status from DISCONNECTED to CONNECTED."""
        await _maybe_await(mock_lcu.simulate_connect())
        status = await _maybe_await(mock_lcu.get_connection_status())

        assert status == LcuConnectionStatus.CONNECTED

    @pytest.mark.asyncio
    async def test_simulate_disconnect_transitions_to_disconnected(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """simulate_disconnect() transitions status back to DISCONNECTED."""
        await _maybe_await(mock_lcu.simulate_connect())
        assert (
            await _maybe_await(mock_lcu.get_connection_status())
            == LcuConnectionStatus.CONNECTED
        )

        await _maybe_await(mock_lcu.simulate_disconnect())
        status = await _maybe_await(mock_lcu.get_connection_status())

        assert status == LcuConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_idempotent_connection_calls(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Calling simulate_connect() or simulate_disconnect() repeatedly should be idempotent."""
        await _maybe_await(mock_lcu.simulate_connect())
        await _maybe_await(mock_lcu.simulate_connect())
        assert (
            await _maybe_await(mock_lcu.get_connection_status())
            == LcuConnectionStatus.CONNECTED
        )

        await _maybe_await(mock_lcu.simulate_disconnect())
        await _maybe_await(mock_lcu.simulate_disconnect())
        assert (
            await _maybe_await(mock_lcu.get_connection_status())
            == LcuConnectionStatus.DISCONNECTED
        )


# ============================================================================
# 4. MockLcuAdapter Gameflow Phase Transitions
# ============================================================================


class TestMockLcuPhaseTransitions:
    """Tests phase changes and updates within MockLcuAdapter."""

    @pytest.mark.asyncio
    async def test_simulate_phase_change_updates_phase(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """simulate_phase_change updates the gameflow phase returned by get_gameflow_phase()."""
        await _maybe_await(mock_lcu.simulate_connect())

        target_phase = getattr(
            GameflowPhase, "MATCHMAKING", getattr(GameflowPhase, "Matchmaking")
        )
        await _maybe_await(mock_lcu.simulate_phase_change(target_phase))

        current_phase = await _maybe_await(mock_lcu.get_gameflow_phase())
        assert current_phase == target_phase

    @pytest.mark.asyncio
    async def test_sequential_phase_transitions(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Verifies a full match lifecycle sequence of gameflow phases."""
        await _maybe_await(mock_lcu.simulate_connect())

        phases = [
            getattr(GameflowPhase, "LOBBY", getattr(GameflowPhase, "Lobby")),
            getattr(
                GameflowPhase,
                "MATCHMAKING",
                getattr(GameflowPhase, "Matchmaking"),
            ),
            getattr(
                GameflowPhase,
                "READY_CHECK",
                getattr(GameflowPhase, "ReadyCheck"),
            ),
            getattr(
                GameflowPhase,
                "CHAMP_SELECT",
                getattr(GameflowPhase, "ChampSelect"),
            ),
            getattr(
                GameflowPhase,
                "IN_PROGRESS",
                getattr(GameflowPhase, "InProgress"),
            ),
            getattr(
                GameflowPhase,
                "WAITING_FOR_STATS",
                getattr(GameflowPhase, "WaitingForStats"),
            ),
            getattr(
                GameflowPhase,
                "END_OF_GAME",
                getattr(GameflowPhase, "EndOfGame"),
            ),
        ]

        for phase in phases:
            await _maybe_await(mock_lcu.simulate_phase_change(phase))
            current = await _maybe_await(mock_lcu.get_gameflow_phase())
            assert current == phase


# ============================================================================
# 5. MockLcuAdapter Event Subscription & Notification Tests
# ============================================================================


class TestMockLcuEventSubscription:
    """Tests subscribing to phase change events with async callbacks."""

    @pytest.mark.asyncio
    async def test_async_callback_triggered_on_phase_change(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """An async callback registered with subscribe_phase_change should be invoked

        with the new phase parameter whenever simulate_phase_change is called.
        """
        received_phases: list[GameflowPhase] = []

        async def on_phase_changed(new_phase: GameflowPhase) -> None:
            received_phases.append(new_phase)

        mock_lcu.subscribe_phase_change(on_phase_changed)
        await _maybe_await(mock_lcu.simulate_connect())

        target_phase = getattr(
            GameflowPhase, "MATCHMAKING", getattr(GameflowPhase, "Matchmaking")
        )
        await _maybe_await(mock_lcu.simulate_phase_change(target_phase))

        assert len(received_phases) == 1
        assert received_phases[0] == target_phase

    @pytest.mark.asyncio
    async def test_multiple_subscribers_all_notified(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """All registered callbacks must be notified of a phase change."""
        log1: list[GameflowPhase] = []
        log2: list[GameflowPhase] = []

        async def cb1(phase: GameflowPhase) -> None:
            log1.append(phase)

        async def cb2(phase: GameflowPhase) -> None:
            log2.append(phase)

        mock_lcu.subscribe_phase_change(cb1)
        mock_lcu.subscribe_phase_change(cb2)

        lobby_phase = getattr(
            GameflowPhase, "LOBBY", getattr(GameflowPhase, "Lobby")
        )
        await _maybe_await(mock_lcu.simulate_phase_change(lobby_phase))

        assert len(log1) == 1
        assert log1[0] == lobby_phase
        assert len(log2) == 1
        assert log2[0] == lobby_phase

    @pytest.mark.asyncio
    async def test_subscriber_error_does_not_break_subsequent_subscribers(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Shift-Left: If one subscriber callback raises an unhandled exception,

        subsequent subscribers must still receive the notification.
        """
        successful_calls: list[GameflowPhase] = []

        async def faulty_subscriber(phase: GameflowPhase) -> None:
            raise RuntimeError("Faulty subscriber crashed!")

        async def reliable_subscriber(phase: GameflowPhase) -> None:
            successful_calls.append(phase)

        mock_lcu.subscribe_phase_change(faulty_subscriber)
        mock_lcu.subscribe_phase_change(reliable_subscriber)

        target = getattr(
            GameflowPhase, "MATCHMAKING", getattr(GameflowPhase, "Matchmaking")
        )
        # Should not crash unhandled or swallow remaining notifications
        try:
            await _maybe_await(mock_lcu.simulate_phase_change(target))
        except RuntimeError:
            # Even if exception propagates, let's verify reliable subscriber got notified
            pass

        assert len(successful_calls) == 1
        assert successful_calls[0] == target

    @pytest.mark.asyncio
    async def test_sync_callback_support(self, mock_lcu: MockLcuAdapter) -> None:
        """Non-coroutine synchronous callbacks should also be supported gracefully."""
        received: list[GameflowPhase] = []

        def sync_cb(phase: GameflowPhase) -> None:
            received.append(phase)

        mock_lcu.subscribe_phase_change(sync_cb)  # type: ignore[arg-type]
        target = getattr(
            GameflowPhase, "LOBBY", getattr(GameflowPhase, "Lobby")
        )
        await _maybe_await(mock_lcu.simulate_phase_change(target))

        assert len(received) == 1
        assert received[0] == target

    @pytest.mark.asyncio
    async def test_unsubscribe_phase_change_stops_notifications(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Unregistered callback should not receive subsequent phase notifications."""
        received: list[GameflowPhase] = []

        def cb(phase: GameflowPhase) -> None:
            received.append(phase)

        mock_lcu.subscribe_phase_change(cb)
        assert mock_lcu.unsubscribe_phase_change(cb) is True
        # Second call returns False
        assert mock_lcu.unsubscribe_phase_change(cb) is False

        target = getattr(GameflowPhase, "LOBBY", getattr(GameflowPhase, "Lobby"))
        await _maybe_await(mock_lcu.simulate_phase_change(target))
        assert len(received) == 0


# ============================================================================
# 6. MockLcuAdapter Ready Check Flow Tests
# ============================================================================


class TestMockLcuReadyCheckFlow:
    """Tests the ready check trigger and accept flow."""

    @pytest.mark.asyncio
    async def test_simulate_ready_check_sets_ready_check_phase(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """simulate_ready_check(timer=12.0) must transition gameflow phase to READY_CHECK."""
        await _maybe_await(mock_lcu.simulate_connect())
        await _maybe_await(mock_lcu.simulate_ready_check(timer=12.0))

        current_phase = await _maybe_await(mock_lcu.get_gameflow_phase())
        expected_phase = getattr(
            GameflowPhase,
            "READY_CHECK",
            getattr(GameflowPhase, "ReadyCheck"),
        )
        assert current_phase == expected_phase

    @pytest.mark.asyncio
    async def test_accept_ready_check_succeeds_in_ready_check_phase(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """When in READY_CHECK phase, accept_ready_check() returns True and marks status Accepted."""
        await _maybe_await(mock_lcu.simulate_connect())
        await _maybe_await(mock_lcu.simulate_ready_check(timer=12.0))

        result = await _maybe_await(mock_lcu.accept_ready_check())
        assert result is True

        # State should be marked as Accepted
        await _assert_ready_check_marked_accepted(mock_lcu)

    @pytest.mark.asyncio
    async def test_accept_ready_check_fails_when_not_in_ready_check_phase(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """When NOT in READY_CHECK phase (e.g. Lobby or Matchmaking), accept_ready_check() returns False."""
        await _maybe_await(mock_lcu.simulate_connect())

        # 1. In GameflowPhase.NONE / LOBBY
        lobby = getattr(GameflowPhase, "LOBBY", getattr(GameflowPhase, "Lobby"))
        await _maybe_await(mock_lcu.simulate_phase_change(lobby))

        result_lobby = await _maybe_await(mock_lcu.accept_ready_check())
        assert result_lobby is False

        # 2. In GameflowPhase.MATCHMAKING
        matchmaking = getattr(
            GameflowPhase,
            "MATCHMAKING",
            getattr(GameflowPhase, "Matchmaking"),
        )
        await _maybe_await(mock_lcu.simulate_phase_change(matchmaking))

        result_matchmaking = await _maybe_await(mock_lcu.accept_ready_check())
        assert result_matchmaking is False

    @pytest.mark.asyncio
    async def test_multiple_accept_ready_check_calls(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Subsequent accept_ready_check() calls while already accepted should remain valid/True."""
        await _maybe_await(mock_lcu.simulate_connect())
        await _maybe_await(mock_lcu.simulate_ready_check(timer=10.0))

        first_accept = await _maybe_await(mock_lcu.accept_ready_check())
        assert first_accept is True

        second_accept = await _maybe_await(mock_lcu.accept_ready_check())
        assert second_accept is True

        await _assert_ready_check_marked_accepted(mock_lcu)


# ============================================================================
# 7. MockLcuAdapter Error & Disconnection Simulation Tests
# ============================================================================


class TestMockLcuDisconnectAndErrorHandling:
    """Tests edge cases, network dropouts, and command rejection when disconnected."""

    @pytest.mark.asyncio
    async def test_accept_ready_check_fails_when_disconnected(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Commands like accept_ready_check() should return False or be rejected if DISCONNECTED."""
        # Adapter is disconnected initially
        assert (
            await _maybe_await(mock_lcu.get_connection_status())
            == LcuConnectionStatus.DISCONNECTED
        )

        result = await _maybe_await(mock_lcu.accept_ready_check())
        assert result is False

    @pytest.mark.asyncio
    async def test_disconnect_drops_ready_check_flow(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Disconnecting during ready check should prevent accepting ready check."""
        await _maybe_await(mock_lcu.simulate_connect())
        await _maybe_await(mock_lcu.simulate_ready_check(timer=12.0))

        # Client drops connection
        await _maybe_await(mock_lcu.simulate_disconnect())
        assert (
            await _maybe_await(mock_lcu.get_connection_status())
            == LcuConnectionStatus.DISCONNECTED
        )

        result = await _maybe_await(mock_lcu.accept_ready_check())
        assert result is False

    @pytest.mark.asyncio
    async def test_phase_change_notification_on_reconnect(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Subscribers continue to receive events after reconnecting."""
        history: list[GameflowPhase] = []

        async def record_phase(phase: GameflowPhase) -> None:
            history.append(phase)

        mock_lcu.subscribe_phase_change(record_phase)

        await _maybe_await(mock_lcu.simulate_connect())
        lobby = getattr(GameflowPhase, "LOBBY", getattr(GameflowPhase, "Lobby"))
        await _maybe_await(mock_lcu.simulate_phase_change(lobby))

        await _maybe_await(mock_lcu.simulate_disconnect())
        await _maybe_await(mock_lcu.simulate_connect())

        champ_select = getattr(
            GameflowPhase,
            "CHAMP_SELECT",
            getattr(GameflowPhase, "ChampSelect"),
        )
        await _maybe_await(mock_lcu.simulate_phase_change(champ_select))

        assert lobby in history
        assert champ_select in history


# ============================================================================
# 8. Champion Select Simulation Tests
# ============================================================================


class TestMockLcuChampSelect:
    """Verifies Champion Select behavior, actions, and event subscriptions on MockLcuAdapter."""

    @pytest.mark.asyncio
    async def test_initial_champ_select_is_none(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """When disconnected or initial, champ select session must be None."""
        assert await mock_lcu.get_champ_select_session() is None
        assert await mock_lcu.get_grid_champions() == []

    @pytest.mark.asyncio
    async def test_simulate_champ_select_session_notifies_subscribers(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Simulating a champ select session transitions phase and notifies subscribers."""
        from riftpilot.core.champ_select import (
            ChampSelectAction,
            ChampSelectPlayer,
            ChampSelectSession,
        )

        received_sessions: list[ChampSelectSession | None] = []

        def on_champ_select(session: ChampSelectSession | None) -> None:
            received_sessions.append(session)

        mock_lcu.subscribe_champ_select(on_champ_select)
        await mock_lcu.simulate_connect()

        session = ChampSelectSession(
            local_player_cell_id=0,
            my_team=[ChampSelectPlayer(cell_id=0, champion_id=0, team=1)],
            actions=[
                [ChampSelectAction(id=1, actor_cell_id=0, type="pick", is_in_progress=True)]
            ],
        )
        await mock_lcu.simulate_champ_select_session(session)

        assert await mock_lcu.get_gameflow_phase() == GameflowPhase.CHAMP_SELECT
        assert await mock_lcu.get_champ_select_session() == session
        assert len(received_sessions) == 1
        assert received_sessions[0] == session

    @pytest.mark.asyncio
    async def test_patch_champ_select_action_updates_state_and_locks_in(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Patching an action updates champion_id, completed status, and player profile."""
        from riftpilot.core.champ_select import (
            ChampSelectAction,
            ChampSelectPlayer,
            ChampSelectSession,
        )

        await mock_lcu.simulate_connect()
        session = ChampSelectSession(
            local_player_cell_id=0,
            my_team=[ChampSelectPlayer(cell_id=0, champion_id=0, team=1)],
            actions=[
                [ChampSelectAction(id=1, actor_cell_id=0, type="pick", is_in_progress=True)]
            ],
        )
        await mock_lcu.simulate_champ_select_session(session)

        # Lock in champion 103 (Ahri)
        success = await mock_lcu.patch_champ_select_action(action_id=1, champion_id=103, completed=True)
        assert success is True

        active = session.actions[0][0]
        assert active.champion_id == 103
        assert active.completed is True
        assert active.is_in_progress is False

        local_p = session.get_local_player()
        assert local_p is not None
        assert local_p.champion_id == 103

    @pytest.mark.asyncio
    async def test_patch_champ_select_action_fails_when_disconnected(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Patching action when disconnected returns False."""
        success = await mock_lcu.patch_champ_select_action(action_id=1, champion_id=103)
        assert success is False

    @pytest.mark.asyncio
    async def test_grid_champions_configuration(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Setting and retrieving grid champions."""
        from riftpilot.core.champ_select import ChampionSummary

        await mock_lcu.simulate_connect()
        champs = [
            ChampionSummary(id=103, name="Ahri", roles=["mage"]),
            ChampionSummary(id=84, name="Akali", roles=["assassin"]),
        ]
        mock_lcu.set_grid_champions(champs)
        retrieved = await mock_lcu.get_grid_champions()
        assert len(retrieved) == 2
        assert retrieved[0].name == "Ahri"

    @pytest.mark.asyncio
    async def test_unsubscribe_champ_select(
        self, mock_lcu: MockLcuAdapter
    ) -> None:
        """Unsubscribing champ select callback stops notifications."""
        from riftpilot.core.champ_select import ChampSelectSession

        calls: list[Any] = []

        def callback(session: ChampSelectSession | None) -> None:
            calls.append(session)

        mock_lcu.subscribe_champ_select(callback)
        assert mock_lcu.unsubscribe_champ_select(callback) is True
        assert mock_lcu.unsubscribe_champ_select(callback) is False

        await mock_lcu.simulate_connect()
        await mock_lcu.simulate_champ_select_session(ChampSelectSession())
        assert len(calls) == 0

