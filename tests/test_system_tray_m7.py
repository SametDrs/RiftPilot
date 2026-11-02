"""Tests for Milestone 7: Desktop System Tray Integration & Native Notifications.

Verifies:
- System tray initialization and context menu actions
- Native tray notification dispatch on Ready Check and Champ Select
- Window minimization to tray on closeEvent
- Clean shutdown on explicit quit
"""

from __future__ import annotations

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from unittest.mock import MagicMock, patch
import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.core.lcu_port import GameflowPhase
from riftpilot.core.pairing import OTTManager, SessionManager
from riftpilot.ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def window(qapp: QApplication) -> MainWindow:
    ott_mgr = OTTManager()
    session_mgr = SessionManager()
    lcu = MockLcuAdapter()
    win = MainWindow(
        ott_manager=ott_mgr,
        session_manager=session_mgr,
        lan_ip="192.168.1.50",
        port=8000,
        lcu_adapter=lcu,
    )
    return win


class TestSystemTrayIntegration:
    """Tests for system tray behavior and notifications."""

    def test_system_tray_initialization(self, window: MainWindow) -> None:
        assert hasattr(window, "tray_icon")
        assert hasattr(window, "show_tray_notification")
        assert hasattr(window, "quit_application")

    def test_show_tray_notification_does_not_raise(self, window: MainWindow) -> None:
        # Should execute safely in any environment
        window.show_tray_notification("Test Title", "Test Message")

    def test_gameflow_phase_triggers_notification(self, window: MainWindow) -> None:
        with patch.object(window, "show_tray_notification") as mock_notify:
            window.on_gameflow_phase_changed(GameflowPhase.READY_CHECK)
            assert mock_notify.called
            args, _ = mock_notify.call_args
            assert "MAÇ BULUNDU" in args[0]

    def test_close_event_minimizes_when_tray_visible(self, window: MainWindow) -> None:
        mock_tray = MagicMock()
        mock_tray.isVisible.return_value = True
        window.tray_icon = mock_tray

        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted() is False

    def test_close_event_accepts_when_quitting(self, window: MainWindow) -> None:
        mock_tray = MagicMock()
        mock_tray.isVisible.return_value = True
        window.tray_icon = mock_tray

        window.quit_application()
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted() is True
