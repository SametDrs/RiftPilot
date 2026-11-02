"""RiftPilot — Tests for Match Domain Timers and Ready Check Decline.

Covers:
1. QueueTimer domain model lifecycle, custom step, estimate, overdue, and timestamp sync.
2. InGameTimer domain model timestamp-based calculation, background interval survival,
   start/stop/tick/reset lifecycle, and formatting.
3. decline_ready_check implementation on MockLcuAdapter (LOBBY/NONE reset).
4. decline_ready_check implementation on RiotLcuAdapter (/lol-matchmaking/v1/ready-check/decline).
"""

from __future__ import annotations

from typing import Any
import httpx
import pytest

from riftpilot.adapters.lcu_discovery import LcuCredentials
from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.riot_lcu import RiotLcuAdapter
from riftpilot.core.lcu_port import GameflowPhase, ILcuPort, LcuConnectionStatus
from riftpilot.core.queue_timer import InGameTimer, QueueTimer, format_duration


# ============================================================================
# Helpers & Fixtures
# ============================================================================


class FakeClock:
    """Deterministic clock controllable in unit tests."""

    def __init__(self, current_time: float = 1000.0) -> None:
        self.time = current_time

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


@pytest.fixture
def sample_credentials() -> LcuCredentials:
    return LcuCredentials(
        port=29999,
        auth_token="mock_auth_token_secret",
        pid=12345,
        protocol="https",
    )


def make_mock_transport(
    status_code: int = 200,
    json_data: Any = None,
    text_data: str | None = None,
    side_effect: Exception | None = None,
    captured_requests: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured_requests is not None:
            captured_requests.append(request)
        if side_effect is not None:
            raise side_effect
        if json_data is not None:
            return httpx.Response(status_code, json=json_data, request=request)
        return httpx.Response(status_code, text=text_data or "", request=request)

    return httpx.MockTransport(handler)


# ============================================================================
# 1. format_duration Tests
# ============================================================================


class TestFormatDuration:
    """Tests for the format_duration MM:SS formatter."""

    def test_zero_and_negative_seconds(self) -> None:
        assert format_duration(0.0) == "00:00"
        assert format_duration(-15.0) == "00:00"

    def test_under_one_minute(self) -> None:
        assert format_duration(9.0) == "00:09"
        assert format_duration(59.0) == "00:59"

    def test_over_one_minute(self) -> None:
        assert format_duration(60.0) == "01:00"
        assert format_duration(84.0) == "01:24"
        assert format_duration(135.0) == "02:15"

    def test_extended_duration(self) -> None:
        assert format_duration(3600.0) == "60:00"
        assert format_duration(4525.0) == "75:25"


# ============================================================================
# 2. QueueTimer Domain Model Tests
# ============================================================================


class TestQueueTimerDomain:
    """Unit tests for the enhanced QueueTimer domain model."""

    def test_initial_state(self) -> None:
        timer = QueueTimer()
        assert not timer.is_running
        assert timer.elapsed_seconds == 0.0
        assert timer.estimated_seconds == 0.0
        assert timer.formatted_elapsed == "00:00"
        assert timer.formatted_estimated == "--:--"
        assert timer.remaining_seconds == 0.0
        assert timer.formatted_remaining == "--:--"
        assert not timer.is_overdue
        assert timer.overdue_seconds == 0.0
        assert timer.display_text == "⏳ Sırada: 00:00"
        assert timer.slash_formatted_text == "00:00"

    def test_start_with_estimate_and_initial_seconds(self) -> None:
        fake_clock = FakeClock(100.0)
        timer = QueueTimer(clock=fake_clock)
        timer.start(estimated_duration_seconds=120.0, initial_seconds=15.0)

        assert timer.is_running
        assert timer.estimated_seconds == 120.0
        assert timer.elapsed_seconds == 15.0
        assert timer.formatted_elapsed == "00:15"
        assert timer.formatted_estimated == "02:00"
        assert timer.remaining_seconds == 105.0

    def test_tick_and_custom_delta(self) -> None:
        timer = QueueTimer()
        timer.start(estimated_duration_seconds=100.0)
        assert timer.tick() == 1.0
        assert timer.tick(delta_seconds=4.0) == 5.0
        assert timer.elapsed_seconds == 5.0

    def test_tick_when_not_running(self) -> None:
        timer = QueueTimer()
        assert timer.tick() == 0.0
        assert timer.elapsed_seconds == 0.0

    def test_stop_and_reset(self) -> None:
        timer = QueueTimer()
        timer.start(estimated_duration_seconds=90.0)
        timer.tick(30.0)

        timer.stop()
        assert not timer.is_running
        assert timer.elapsed_seconds == 30.0
        timer.tick(10.0)
        assert timer.elapsed_seconds == 30.0

        timer.reset()
        assert not timer.is_running
        assert timer.elapsed_seconds == 0.0
        assert timer.estimated_seconds == 0.0

    def test_set_and_sync_elapsed(self) -> None:
        timer = QueueTimer()
        timer.start(estimated_duration_seconds=60.0)
        timer.set_elapsed(45.0)
        assert timer.elapsed_seconds == 45.0

        timer.sync_elapsed(50.0)
        assert timer.elapsed_seconds == 50.0

    def test_sync_with_timestamp_background_delay(self) -> None:
        fake_clock = FakeClock(500.0)
        timer = QueueTimer(clock=fake_clock)
        timer.start(estimated_duration_seconds=180.0)

        # Simulate 40 seconds passed in background with no ticks
        fake_clock.advance(40.0)
        assert timer.sync_with_timestamp() == 40.0
        assert timer.elapsed_seconds == 40.0

    def test_overdue_properties(self) -> None:
        timer = QueueTimer()
        timer.start(estimated_duration_seconds=60.0)
        timer.set_elapsed(80.0)

        assert timer.is_overdue
        assert timer.overdue_seconds == 20.0
        assert timer.remaining_seconds == 0.0
        assert timer.formatted_remaining == "00:00"

    def test_repr_and_str(self) -> None:
        timer = QueueTimer()
        timer.start(estimated_duration_seconds=120.0)
        assert "QueueTimer" in repr(timer)
        assert str(timer) == timer.display_text


# ============================================================================
# 3. InGameTimer Domain Model Tests
# ============================================================================


class TestInGameTimerDomain:
    """Unit tests for the InGameTimer domain model."""

    def test_initial_state(self) -> None:
        timer = InGameTimer()
        assert not timer.is_running
        assert timer.elapsed_seconds == 0.0
        assert timer.formatted_elapsed == "00:00"
        assert timer.display_text == "⚔️ Oyunda: 00:00"
        assert "InGameTimer" in repr(timer)

    def test_start_default_and_initial_seconds(self) -> None:
        fake_clock = FakeClock(100.0)
        timer = InGameTimer(clock=fake_clock)

        timer.start()
        assert timer.is_running
        assert timer.elapsed_seconds == 0.0
        assert timer.formatted_elapsed == "00:00"

        timer.start(initial_seconds=75.0)
        assert timer.is_running
        assert timer.elapsed_seconds == 75.0
        assert timer.formatted_elapsed == "01:15"

    def test_tick_advances_elapsed(self) -> None:
        timer = InGameTimer()
        timer.start()

        assert timer.tick() == 1.0
        assert timer.tick(delta_seconds=5.0) == 6.0
        assert timer.elapsed_seconds == 6.0
        assert timer.formatted_elapsed == "00:06"

    def test_tick_when_not_running_does_not_advance(self) -> None:
        timer = InGameTimer()
        assert timer.tick(5.0) == 0.0
        assert timer.elapsed_seconds == 0.0

    def test_timestamp_based_progression(self) -> None:
        fake_clock = FakeClock(200.0)
        timer = InGameTimer(clock=fake_clock)
        timer.start()

        # Advance clock without calling tick()
        fake_clock.advance(84.0)
        assert timer.elapsed_seconds == 84.0
        assert timer.formatted_elapsed == "01:24"
        assert timer.display_text == "⚔️ Oyunda: 01:24"

    def test_survives_background_intervals(self) -> None:
        """In-game timer accurately reflects duration when OS/browser suspends tick events."""
        fake_clock = FakeClock(1000.0)
        timer = InGameTimer(clock=fake_clock)
        timer.start()

        # Simulate 2 ticks
        timer.tick()
        timer.tick()
        assert timer.elapsed_seconds == 2.0

        # Simulate 180 seconds in background (e.g. mobile locked screen)
        fake_clock.advance(180.0)

        # Timer elapsed immediately reflects real time
        assert timer.elapsed_seconds >= 180.0
        assert timer.formatted_elapsed == "03:00"

        # Ticking after background wake does not jump backwards or double count
        timer.tick()
        assert timer.elapsed_seconds >= 180.0

    def test_stop_freezes_elapsed(self) -> None:
        fake_clock = FakeClock(100.0)
        timer = InGameTimer(clock=fake_clock)
        timer.start()
        fake_clock.advance(45.0)

        timer.stop()
        assert not timer.is_running
        assert timer.elapsed_seconds == 45.0

        # Subsequent clock advancement does not affect stopped timer
        fake_clock.advance(100.0)
        assert timer.elapsed_seconds == 45.0
        assert timer.tick(10.0) == 45.0

    def test_reset(self) -> None:
        fake_clock = FakeClock(100.0)
        timer = InGameTimer(clock=fake_clock)
        timer.start()
        fake_clock.advance(90.0)

        timer.reset()
        assert not timer.is_running
        assert timer.elapsed_seconds == 0.0
        assert timer.formatted_elapsed == "00:00"

    def test_set_and_sync_elapsed(self) -> None:
        fake_clock = FakeClock(100.0)
        timer = InGameTimer(clock=fake_clock)
        timer.start()

        timer.set_elapsed(125.0)
        assert timer.elapsed_seconds == 125.0
        assert timer.formatted_elapsed == "02:05"

        timer.sync_elapsed(200.0)
        assert timer.elapsed_seconds == 200.0
        assert timer.formatted_elapsed == "03:20"

    def test_formatting_edge_cases(self) -> None:
        timer = InGameTimer()
        timer.set_elapsed(3600.0)
        assert timer.formatted_elapsed == "60:00"

        timer.set_elapsed(4525.0)
        assert timer.formatted_elapsed == "75:25"


# ============================================================================
# 4. MockLcuAdapter decline_ready_check Tests
# ============================================================================


class TestMockLcuDeclineReadyCheck:
    """Tests for MockLcuAdapter.decline_ready_check."""

    @pytest.mark.asyncio
    async def test_decline_rejected_when_disconnected(self) -> None:
        adapter = MockLcuAdapter()
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED
        result = await adapter.decline_ready_check()
        assert not result

    @pytest.mark.asyncio
    async def test_decline_rejected_when_not_in_ready_check(self) -> None:
        adapter = MockLcuAdapter()
        await adapter.simulate_connect()
        assert await adapter.get_gameflow_phase() == GameflowPhase.NONE

        result = await adapter.decline_ready_check()
        assert not result

    @pytest.mark.asyncio
    async def test_decline_success_resets_to_none_when_no_lobby(self) -> None:
        adapter = MockLcuAdapter()
        await adapter.simulate_connect()
        await adapter.simulate_ready_check(timer=12.0)

        assert await adapter.get_gameflow_phase() == GameflowPhase.READY_CHECK
        assert adapter.ready_check["state"] == "InProgress"

        result = await adapter.decline_ready_check()
        assert result
        assert adapter.player_response == "Declined"
        assert adapter.ready_check["playerResponse"] == "Declined"
        assert adapter.ready_check["state"] == "Declined"
        assert await adapter.get_gameflow_phase() == GameflowPhase.NONE

    @pytest.mark.asyncio
    async def test_decline_success_resets_to_lobby_when_lobby_active(self) -> None:
        adapter = MockLcuAdapter()
        await adapter.simulate_connect()
        await adapter.create_lobby(queue_id=420)
        assert await adapter.get_gameflow_phase() == GameflowPhase.LOBBY

        # Ready check pops
        await adapter.simulate_ready_check(timer=10.0)
        assert await adapter.get_gameflow_phase() == GameflowPhase.READY_CHECK

        # Declining ready check returns player to party lobby
        result = await adapter.decline_ready_check()
        assert result
        assert adapter.player_response == "Declined"
        assert adapter.ready_check["playerResponse"] == "Declined"
        assert await adapter.get_gameflow_phase() == GameflowPhase.LOBBY

    @pytest.mark.asyncio
    async def test_decline_notifies_phase_change_subscribers(self) -> None:
        adapter = MockLcuAdapter()
        await adapter.simulate_connect()
        await adapter.simulate_ready_check()

        received_phases: list[GameflowPhase] = []
        adapter.subscribe_phase_change(lambda p: received_phases.append(p))

        result = await adapter.decline_ready_check()
        assert result
        assert GameflowPhase.NONE in received_phases


# ============================================================================
# 5. RiotLcuAdapter decline_ready_check Tests
# ============================================================================


class TestRiotLcuDeclineReadyCheck:
    """Tests for RiotLcuAdapter.decline_ready_check."""

    @pytest.mark.asyncio
    async def test_decline_rejected_when_disconnected(self) -> None:
        adapter = RiotLcuAdapter(discoverer=lambda: None)
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED
        result = await adapter.decline_ready_check()
        assert not result

    @pytest.mark.asyncio
    async def test_decline_success_status_204(
        self, sample_credentials: LcuCredentials
    ) -> None:
        captured_requests: list[httpx.Request] = []
        transport = make_mock_transport(
            status_code=204, captured_requests=captured_requests
        )
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        result = await adapter.decline_ready_check()
        assert result
        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.method == "POST"
        assert req.url.path == "/lol-matchmaking/v1/ready-check/decline"

    @pytest.mark.asyncio
    async def test_decline_success_status_200(
        self, sample_credentials: LcuCredentials
    ) -> None:
        transport = make_mock_transport(status_code=200, json_data={})
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        result = await adapter.decline_ready_check()
        assert result

    @pytest.mark.asyncio
    async def test_decline_failure_status_500(
        self, sample_credentials: LcuCredentials
    ) -> None:
        transport = make_mock_transport(status_code=500, text_data="Internal Error")
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        result = await adapter.decline_ready_check()
        assert not result

    @pytest.mark.asyncio
    async def test_decline_auth_failed_sets_status(
        self, sample_credentials: LcuCredentials
    ) -> None:
        transport = make_mock_transport(status_code=401)
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        result = await adapter.decline_ready_check()
        assert not result
        assert await adapter.get_connection_status() == LcuConnectionStatus.AUTHENTICATION_FAILED

    @pytest.mark.asyncio
    async def test_decline_connect_error_sets_disconnected(
        self, sample_credentials: LcuCredentials
    ) -> None:
        transport = make_mock_transport(
            side_effect=httpx.ConnectError("Connection refused")
        )
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        result = await adapter.decline_ready_check()
        assert not result
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_decline_timeout_returns_false(
        self, sample_credentials: LcuCredentials
    ) -> None:
        transport = make_mock_transport(
            side_effect=httpx.TimeoutException("Read timed out")
        )
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        result = await adapter.decline_ready_check()
        assert not result
        # Connection status remains CONNECTED on temporary timeout
        assert await adapter.get_connection_status() == LcuConnectionStatus.CONNECTED
