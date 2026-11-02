"""RiftPilot — Connected Devices UI & InProgress Phase Tests.

Validates the desktop companion connected devices list:
- Elimination of QListWidgetItem text delegate overlap (item text must be empty).
- Custom row widget structure:
  - Left column: Device icon (📱), bold device name with truncation for long names, full tooltip.
  - Subtitle: '🟢 Mobil Web Paneli • Çevrimiçi' with hextech styling.
  - Right column: Revoke / Disconnect button with text '✕ Bağlantıyı Kes', objectName 'revokeBtn',
    minimum dimensions (115x28), and proper pseudo-classes in stylesheet.
- Explicit item row height (58px).
- InProgress phase display: '⚔️ Maçtasın! (Maç Devam Ediyor)' with optional duration formatting.
"""

from __future__ import annotations

import os

# Headless Qt platform setup before PySide6 initialization
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from typing import Generator
import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QWidget,
)

from riftpilot.core.lcu_port import GameflowPhase
from riftpilot.core.pairing import OTTManager, SessionManager
from riftpilot.ui.main_window import (
    HEXTECH_STYLE,
    MAIN_WINDOW_STYLE,
    MainWindow,
)


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Fixture providing headless QApplication singleton."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def ott_manager() -> OTTManager:
    return OTTManager()


@pytest.fixture
def session_manager() -> SessionManager:
    return SessionManager()


@pytest.fixture
def main_window(
    qapp: QApplication,
    ott_manager: OTTManager,
    session_manager: SessionManager,
) -> Generator[MainWindow, None, None]:
    window = MainWindow(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lan_ip="192.168.1.50",
        port=8000,
    )
    window.show()
    yield window
    window.close()
    window.deleteLater()


class TestConnectedDevicesList:
    """Tests for connected devices listing, custom row widgets, and text overlap bug fix."""

    def test_update_device_list_empty_state(
        self,
        main_window: MainWindow,
        session_manager: SessionManager,
    ) -> None:
        """When no devices are connected, empty state label is displayed and list is hidden."""
        main_window.update_device_list()
        assert not main_window.empty_state_label.isHidden()
        assert main_window.device_list.isHidden()
        assert "0" in main_window.active_count_label.text()

    def test_update_device_list_row_widget_no_background_text(
        self,
        main_window: MainWindow,
        session_manager: SessionManager,
    ) -> None:
        """Fix overlapping text: QListWidgetItem text MUST be empty string when setItemWidget is used."""
        session = session_manager.create_session("Pixel 8 Pro", "dev-pixel-8")
        main_window.update_device_list()

        assert main_window.device_list.count() == 1
        item = main_window.device_list.item(0)
        assert item is not None

        # Verify QListWidgetItem text is explicitly empty so Qt doesn't paint behind the custom widget
        assert item.text() == "", "QListWidgetItem must have empty text to prevent background rendering overlap"

        # Verify explicit row height hint
        assert item.sizeHint().height() == 58, "Item height should be 58px to prevent collapsing"

        # Verify row widget was attached
        row_widget = main_window.device_list.itemWidget(item)
        assert row_widget is not None
        assert isinstance(row_widget, QWidget)

    def test_device_row_labels_and_structure(
        self,
        main_window: MainWindow,
        session_manager: SessionManager,
    ) -> None:
        """Device row must contain title with icon and subtitle with online status."""
        session_manager.create_session("iPhone 15", "dev-iphone-15")
        main_window.update_device_list()

        item = main_window.device_list.item(0)
        row_widget = main_window.device_list.itemWidget(item)
        assert row_widget is not None

        labels = row_widget.findChildren(QLabel)
        assert len(labels) >= 2, "Row widget must contain at least title and subtitle labels"

        # Find title and subtitle labels
        title_lbl = next((lbl for lbl in labels if "iPhone 15" in lbl.text()), None)
        assert title_lbl is not None, "Title label with device name not found"
        assert title_lbl.text() == "📱 iPhone 15"
        assert title_lbl.font().bold(), "Device title label font must be bold"
        assert title_lbl.toolTip() == "iPhone 15"

        subtitle_lbl = next((lbl for lbl in labels if "Mobil Web Paneli" in lbl.text()), None)
        assert subtitle_lbl is not None, "Subtitle status label not found"
        assert "🟢 Mobil Web Paneli • Çevrimiçi" in subtitle_lbl.text()
        assert subtitle_lbl.objectName() == "deviceSubtitle"

    def test_revoke_button_attributes_and_styling(
        self,
        main_window: MainWindow,
        session_manager: SessionManager,
    ) -> None:
        """Revoke button must have exact text, objectName, min dimensions, and functional click."""
        session = session_manager.create_session("iPad Air", "dev-ipad-air")
        main_window.update_device_list()

        item = main_window.device_list.item(0)
        row_widget = main_window.device_list.itemWidget(item)
        assert row_widget is not None

        revoke_btn = row_widget.findChild(QPushButton, "revokeBtn")
        assert revoke_btn is not None, "QPushButton with objectName 'revokeBtn' not found"
        assert revoke_btn.text() == "✕ Bağlantıyı Kes", "Button text must be '✕ Bağlantıyı Kes'"
        assert revoke_btn.minimumWidth() >= 115, "Button min-width must be at least 115px"
        assert revoke_btn.minimumHeight() >= 28, "Button min-height must be at least 28px"

        # Test clicking revoke button terminates the session
        revoke_btn.click()
        assert not session.is_active
        assert main_window.device_list.count() == 0
        assert not main_window.empty_state_label.isHidden()
        assert main_window.device_list.isHidden()

    def test_multiple_devices_rendering(
        self,
        main_window: MainWindow,
        session_manager: SessionManager,
    ) -> None:
        """Multiple active devices should each get structured row widgets without overlapping text."""
        devices = [
            ("iPhone 15 Pro", "dev-1"),
            ("Samsung Galaxy S24", "dev-2"),
            ("Xiaomi Pad 6", "dev-3"),
        ]
        for name, cid in devices:
            session_manager.create_session(name, cid)

        main_window.update_device_list()

        assert main_window.device_list.count() == 3
        assert "3" in main_window.active_count_label.text()

        for i in range(main_window.device_list.count()):
            item = main_window.device_list.item(i)
            assert item.text() == "", f"Item {i} must have empty text"
            assert item.sizeHint().height() == 58

            row_widget = main_window.device_list.itemWidget(item)
            assert row_widget is not None
            btn = row_widget.findChild(QPushButton, "revokeBtn")
            assert btn is not None
            assert btn.text() == "✕ Bağlantıyı Kes"

    def test_long_device_name_truncation_and_tooltip(
        self,
        main_window: MainWindow,
        session_manager: SessionManager,
    ) -> None:
        """Very long device names must be truncated with ellipsis and display full name in tooltip."""
        long_name = "Samsung Galaxy S24 Ultra Max Enterprise Edition 5G"
        session_manager.create_session(long_name, "dev-long-name")
        main_window.update_device_list()

        item = main_window.device_list.item(0)
        assert item.text() == ""

        row_widget = main_window.device_list.itemWidget(item)
        assert row_widget is not None

        labels = row_widget.findChildren(QLabel)
        title_lbl = labels[0]

        # Must have ellipsis
        assert "..." in title_lbl.text(), "Long device name should be truncated with ellipsis"
        assert len(title_lbl.text()) < len(f"📱 {long_name}")

        # Must have tooltip with full original name
        assert title_lbl.toolTip() == long_name, "Tooltip must contain the full untruncated device name"


class TestGameflowInProgressDisplay:
    """Tests for InProgress gameflow phase transitions and display text."""

    def test_inprogress_phase_enum(self, main_window: MainWindow) -> None:
        """GameflowPhase.IN_PROGRESS should update label to '⚔️ Maçtasın! (Maç Devam Ediyor)'."""
        main_window.on_gameflow_phase_changed(GameflowPhase.IN_PROGRESS)
        assert main_window.gameflow_label.text() == "⚔️ Maçtasın! (Maç Devam Ediyor)"

    def test_inprogress_phase_string(self, main_window: MainWindow) -> None:
        """String 'InProgress' should update label to '⚔️ Maçtasın! (Maç Devam Ediyor)'."""
        main_window.on_gameflow_phase_changed("InProgress")
        assert main_window.gameflow_label.text() == "⚔️ Maçtasın! (Maç Devam Ediyor)"

    def test_inprogress_phase_case_insensitive_string(self, main_window: MainWindow) -> None:
        """String 'in_progress' should update label to '⚔️ Maçtasın! (Maç Devam Ediyor)'."""
        main_window.on_gameflow_phase_changed("in_progress")
        assert main_window.gameflow_label.text() == "⚔️ Maçtasın! (Maç Devam Ediyor)"

    def test_inprogress_phase_with_duration(self, main_window: MainWindow) -> None:
        """InProgress with estimated duration should format mm:ss."""
        main_window.on_gameflow_phase_changed(GameflowPhase.IN_PROGRESS, estimated_duration=325.0)
        assert "⚔️ Maçtasın! (Maç Devam Ediyor - 05:25)" == main_window.gameflow_label.text()


class TestStylesheetRules:
    """Tests for CSS rules in MAIN_WINDOW_STYLE and HEXTECH_STYLE."""

    def test_revoke_button_css_present(self) -> None:
        """Verify #revokeBtn rules are defined with visible text colors and pseudo-classes."""
        assert "QPushButton#revokeBtn" in MAIN_WINDOW_STYLE
        assert "#ff8595" in MAIN_WINDOW_STYLE
        assert "#ffffff" in MAIN_WINDOW_STYLE
        assert "min-width: 115px" in MAIN_WINDOW_STYLE
        assert "min-height: 28px" in MAIN_WINDOW_STYLE
        assert ":hover" in MAIN_WINDOW_STYLE
        assert ":pressed" in MAIN_WINDOW_STYLE
        assert ":disabled" in MAIN_WINDOW_STYLE
        assert "border-radius: 6px" in MAIN_WINDOW_STYLE
        assert "padding: 6px 12px" in MAIN_WINDOW_STYLE

    def test_hextech_style_alias_matches_main_window_style(self) -> None:
        """HEXTECH_STYLE should be an alias of MAIN_WINDOW_STYLE."""
        assert HEXTECH_STYLE == MAIN_WINDOW_STYLE
