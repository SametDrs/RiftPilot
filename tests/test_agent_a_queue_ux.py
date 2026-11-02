"""RiftPilot — Agent A Queue Timer & Companion UX Tests.

Verifies:
- Domain model `QueueTimer`: start, tick, custom delta, elapsed/estimated formatting ("01:24 / 02:15"),
  countdown, overdue detection, display text, and reset.
- `MainWindow` companion GUI:
  - Phase transition to MATCHMAKING activates queue timer.
  - Periodic `_on_poll_tick` advances timer and updates `gameflow_label`.
  - Phase transition away from MATCHMAKING stops and resets timer.
  - String phase support and custom estimated time formatting.
"""

from __future__ import annotations

import os

# Configure headless Qt offscreen platform before PySide6 initializes
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from typing import Generator
import pytest
from PySide6.QtWidgets import QApplication

from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.core.lcu_port import GameflowPhase
from riftpilot.core.pairing import OTTManager, SessionManager
from riftpilot.core.queue_timer import QueueTimer, format_duration
from riftpilot.ui.main_window import MainWindow


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Fixture providing a headless QApplication instance."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def ott_manager() -> OTTManager:
    """Fresh in-memory OTTManager fixture."""
    return OTTManager()


@pytest.fixture
def session_manager() -> SessionManager:
    """Fresh in-memory SessionManager fixture."""
    return SessionManager()


@pytest.fixture
def main_window(
    qapp: QApplication,
    ott_manager: OTTManager,
    session_manager: SessionManager,
) -> Generator[MainWindow, None, None]:
    """Fixture providing an initialized MainWindow instance with cleanup."""
    lcu = MockLcuAdapter()
    window = MainWindow(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lan_ip="192.168.1.50",
        port=8000,
        lcu_adapter=lcu,
    )
    yield window
    window.close()
    window.deleteLater()


# ==============================================================================
# 1. QueueTimer Domain Model Tests
# ==============================================================================


class TestQueueTimerDomain:
    """Unit tests for the QueueTimer domain class."""

    def test_queue_timer_initial_state(self) -> None:
        """Newly created QueueTimer should be stopped and zeroed."""
        timer = QueueTimer()
        assert not timer.is_running
        assert timer.elapsed_seconds == 0.0
        assert timer.estimated_seconds == 0.0
        assert timer.formatted_elapsed == "00:00"
        assert timer.formatted_estimated == "--:--"
        assert timer.display_text == "⏳ Sırada: 00:00"
        assert timer.slash_formatted_text == "00:00"
        assert timer.remaining_seconds == 0.0
        assert timer.formatted_remaining == "--:--"
        assert not timer.is_overdue
        assert timer.overdue_seconds == 0.0

    def test_queue_timer_start_with_estimate(self) -> None:
        """Starting timer with estimated seconds sets running state and formatted estimate."""
        timer = QueueTimer()
        timer.start(estimated_duration_seconds=135.0)

        assert timer.is_running
        assert timer.estimated_seconds == 135.0
        assert timer.elapsed_seconds == 0.0
        assert timer.formatted_elapsed == "00:00"
        assert timer.formatted_estimated == "02:15"
        assert timer.display_text == "⏳ Sırada: 00:00 (Tahmini 02:15)"
        assert timer.slash_formatted_text == "00:00 / Tahmini 02:15"

    def test_queue_timer_tick_advances_elapsed(self) -> None:
        """Ticking 84 times should advance elapsed to 84s and format as '01:24'."""
        timer = QueueTimer()
        timer.start(estimated_duration_seconds=135.0)

        for _ in range(84):
            timer.tick()

        assert timer.elapsed_seconds == 84.0
        assert timer.formatted_elapsed == "01:24"
        assert timer.formatted_estimated == "02:15"
        assert timer.display_text == "⏳ Sırada: 01:24 (Tahmini 02:15)"
        assert timer.slash_formatted_text == "01:24 / Tahmini 02:15"

    def test_queue_timer_tick_with_custom_delta(self) -> None:
        """tick() with explicit delta_seconds parameter advances correctly."""
        timer = QueueTimer()
        timer.start(estimated_duration_seconds=100.0)

        timer.tick(delta_seconds=30.5)
        assert timer.elapsed_seconds == 30.5

        timer.tick(delta_seconds=14.5)
        assert timer.elapsed_seconds == 45.0
        assert timer.formatted_elapsed == "00:45"

    def test_queue_timer_tick_when_not_running_does_not_advance(self) -> None:
        """tick() when timer is stopped should not increase elapsed duration."""
        timer = QueueTimer()
        assert not timer.is_running

        timer.tick()
        assert timer.elapsed_seconds == 0.0

        timer.tick(10.0)
        assert timer.elapsed_seconds == 0.0

    def test_queue_timer_without_estimated_duration(self) -> None:
        """When estimated duration is 0 or omitted, display_text excludes estimated part."""
        timer = QueueTimer()
        timer.start()

        for _ in range(84):
            timer.tick()

        assert timer.formatted_elapsed == "01:24"
        assert timer.formatted_estimated == "--:--"
        assert timer.display_text == "⏳ Sırada: 01:24"
        assert timer.slash_formatted_text == "01:24"

    def test_queue_timer_reset(self) -> None:
        """reset() clears elapsed and estimated values and stops timer."""
        timer = QueueTimer()
        timer.start(estimated_duration_seconds=135.0)
        for _ in range(50):
            timer.tick()

        assert timer.is_running
        assert timer.elapsed_seconds == 50.0

        timer.reset()

        assert not timer.is_running
        assert timer.elapsed_seconds == 0.0
        assert timer.estimated_seconds == 0.0
        assert timer.formatted_elapsed == "00:00"
        assert timer.formatted_estimated == "--:--"
        assert timer.display_text == "⏳ Sırada: 00:00"

    def test_queue_timer_update_estimated(self) -> None:
        """update_estimated() updates estimate without altering running state or elapsed time."""
        timer = QueueTimer()
        timer.start()
        timer.tick(40.0)

        assert timer.formatted_estimated == "--:--"
        timer.update_estimated(135.0)

        assert timer.is_running
        assert timer.elapsed_seconds == 40.0
        assert timer.formatted_estimated == "02:15"
        assert timer.display_text == "⏳ Sırada: 00:40 (Tahmini 02:15)"

    def test_queue_timer_stop_and_set_elapsed(self) -> None:
        """stop() pauses timer without clearing elapsed, and set_elapsed() manually sets duration."""
        timer = QueueTimer()
        timer.start(estimated_duration_seconds=100.0)
        timer.set_elapsed(84.0)

        assert timer.formatted_elapsed == "01:24"

        timer.stop()
        assert not timer.is_running

        timer.tick(10.0)
        assert timer.elapsed_seconds == 84.0

    def test_queue_timer_countdown_and_overdue(self) -> None:
        """Verifies remaining countdown and overdue threshold calculation."""
        timer = QueueTimer()
        timer.start(estimated_duration_seconds=100.0)

        timer.set_elapsed(40.0)
        assert timer.remaining_seconds == 60.0
        assert timer.formatted_remaining == "01:00"
        assert not timer.is_overdue
        assert timer.overdue_seconds == 0.0

        timer.set_elapsed(100.0)
        assert timer.remaining_seconds == 0.0
        assert not timer.is_overdue

        timer.set_elapsed(125.0)
        assert timer.remaining_seconds == 0.0
        assert timer.is_overdue is True
        assert timer.overdue_seconds == 25.0

    def test_format_duration_helper(self) -> None:
        """Unit tests for format_duration helper function."""
        assert format_duration(0.0) == "00:00"
        assert format_duration(-10.0) == "00:00"
        assert format_duration(9.0) == "00:09"
        assert format_duration(59.0) == "00:59"
        assert format_duration(60.0) == "01:00"
        assert format_duration(84.0) == "01:24"
        assert format_duration(135.0) == "02:15"
        assert format_duration(3600.0) == "60:00"

    def test_queue_timer_repr_and_str(self) -> None:
        """Verifies string representation and repr formatting."""
        timer = QueueTimer()
        timer.start(135.0)
        timer.set_elapsed(84.0)

        assert str(timer) == "⏳ Sırada: 01:24 (Tahmini 02:15)"
        assert "QueueTimer" in repr(timer)
        assert "01:24" in repr(timer)


# ==============================================================================
# 2. MainWindow Queue UX Integration Tests
# ==============================================================================


class TestMainWindowQueueUX:
    """Integration tests for MainWindow queue timer display and poll synchronization."""

    def test_main_window_has_queue_timer(self, main_window: MainWindow) -> None:
        """MainWindow should initialize a QueueTimer instance."""
        assert hasattr(main_window, "queue_timer")
        assert isinstance(main_window.queue_timer, QueueTimer)
        assert not main_window.queue_timer.is_running

    def test_phase_change_to_matchmaking_starts_timer(
        self, main_window: MainWindow
    ) -> None:
        """Transitioning to MATCHMAKING starts queue timer and updates label."""
        main_window.on_gameflow_phase_changed(GameflowPhase.MATCHMAKING)

        assert main_window.queue_timer.is_running
        assert main_window.current_phase == GameflowPhase.MATCHMAKING
        assert "⏳ Sırada: 00:00" in main_window.gameflow_label.text()

    def test_phase_change_to_matchmaking_with_estimated_time(
        self, main_window: MainWindow
    ) -> None:
        """Transitioning to MATCHMAKING with estimated duration formats tahmini."""
        main_window.on_gameflow_phase_changed(
            GameflowPhase.MATCHMAKING, estimated_duration=135.0
        )

        assert main_window.queue_timer.is_running
        assert main_window.queue_timer.formatted_estimated == "02:15"
        assert (
            main_window.gameflow_label.text()
            == "⏳ Sırada: 00:00 (Tahmini 02:15)"
        )

    def test_poll_tick_advances_queue_timer_when_matchmaking(
        self, main_window: MainWindow
    ) -> None:
        """Each _on_poll_tick should tick the timer and update the UI label."""
        main_window.on_gameflow_phase_changed(
            GameflowPhase.MATCHMAKING, estimated_duration=135.0
        )
        assert main_window.queue_timer.elapsed_seconds == 0.0

        main_window._on_poll_tick()
        assert main_window.queue_timer.elapsed_seconds == 1.0
        assert (
            main_window.gameflow_label.text()
            == "⏳ Sırada: 00:01 (Tahmini 02:15)"
        )

        main_window._on_poll_tick()
        assert main_window.queue_timer.elapsed_seconds == 2.0
        assert (
            main_window.gameflow_label.text()
            == "⏳ Sırada: 00:02 (Tahmini 02:15)"
        )

    def test_poll_tick_formats_elapsed_at_84_seconds(
        self, main_window: MainWindow
    ) -> None:
        """Simulate reaching 84 seconds and verify UI label format '01:24 / Tahmini 02:15'."""
        main_window.on_gameflow_phase_changed(
            GameflowPhase.MATCHMAKING, estimated_duration=135.0
        )
        main_window.queue_timer.set_elapsed(83.0)

        main_window._on_poll_tick()

        assert main_window.queue_timer.elapsed_seconds == 84.0
        assert main_window.queue_timer.formatted_elapsed == "01:24"
        assert (
            main_window.gameflow_label.text()
            == "⏳ Sırada: 01:24 (Tahmini 02:15)"
        )

    def test_phase_change_away_from_matchmaking_resets_timer(
        self, main_window: MainWindow
    ) -> None:
        """Transitioning away from MATCHMAKING to READY_CHECK stops and resets timer."""
        main_window.on_gameflow_phase_changed(
            GameflowPhase.MATCHMAKING, estimated_duration=135.0
        )
        main_window.queue_timer.tick(45.0)
        assert main_window.queue_timer.is_running
        assert main_window.queue_timer.elapsed_seconds == 45.0

        main_window.on_gameflow_phase_changed(GameflowPhase.READY_CHECK)

        assert not main_window.queue_timer.is_running
        assert main_window.queue_timer.elapsed_seconds == 0.0
        assert main_window.queue_timer.estimated_seconds == 0.0
        assert "MAÇ BULUNDU" in main_window.gameflow_label.text()

    def test_poll_tick_does_not_advance_timer_when_not_matchmaking(
        self, main_window: MainWindow
    ) -> None:
        """_on_poll_tick when in LOBBY phase should not tick queue timer."""
        main_window.on_gameflow_phase_changed(GameflowPhase.LOBBY)
        assert "Lobide" in main_window.gameflow_label.text()

        main_window._on_poll_tick()

        assert main_window.queue_timer.elapsed_seconds == 0.0
        assert not main_window.queue_timer.is_running
        assert "Lobide" in main_window.gameflow_label.text()

    def test_phase_change_string_matchmaking_support(
        self, main_window: MainWindow
    ) -> None:
        """Supports string phase representation 'Matchmaking' gracefully."""
        main_window.on_gameflow_phase_changed("Matchmaking", estimated_duration=60.0)

        assert main_window.queue_timer.is_running
        assert (
            main_window.gameflow_label.text()
            == "⏳ Sırada: 00:00 (Tahmini 01:00)"
        )

        main_window._on_poll_tick()
        assert (
            main_window.gameflow_label.text()
            == "⏳ Sırada: 00:01 (Tahmini 01:00)"
        )

    def test_start_and_stop_queue_timer_helpers(
        self, main_window: MainWindow
    ) -> None:
        """Direct helper methods start_queue_timer and stop_queue_timer work cleanly."""
        main_window.start_queue_timer(estimated_duration=90.0)

        assert main_window.queue_timer.is_running
        assert main_window.current_phase == GameflowPhase.MATCHMAKING
        assert (
            main_window.gameflow_label.text()
            == "⏳ Sırada: 00:00 (Tahmini 01:30)"
        )

        main_window.stop_queue_timer()
        assert not main_window.queue_timer.is_running
        assert main_window.queue_timer.elapsed_seconds == 0.0
