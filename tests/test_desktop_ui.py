"""RiftPilot — PySide6 Desktop UI Unit & Integration Tests (Milestone 1, Task 1.4).

Implements Test-Driven Development (TDD) tests for the desktop UI components:
- `src/riftpilot/ui/qr_helper.py`: QR code pixmap generation for mobile pairing.
- `src/riftpilot/ui/main_window.py`: PySide6 QMainWindow displaying pairing QR code,
  LAN endpoint information, active device sessions, and session revocation controls.

Follows Hexagonal Architecture, ADR-0002 (PySide6 Desktop), and ADR-0004 (Pairing & Revocation).
"""

from __future__ import annotations

import os

# Configure headless Qt platform before any PySide6 widgets are initialized
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QMainWindow, QPushButton

from riftpilot.core.pairing import OTTManager, SessionManager
from riftpilot.ui.main_window import MainWindow
from riftpilot.ui.qr_helper import generate_qr_pixmap


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Fixture providing a headless QApplication singleton instance.

    Reuses existing instance if one is already active in the process,
    otherwise initializes a new QApplication with offscreen rendering.
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def ott_manager() -> OTTManager:
    """Fixture providing a fresh in-memory OTTManager instance."""
    return OTTManager()


@pytest.fixture
def session_manager() -> SessionManager:
    """Fixture providing a fresh in-memory SessionManager instance."""
    return SessionManager()


@pytest.fixture
def main_window(
    qapp: QApplication,
    ott_manager: OTTManager,
    session_manager: SessionManager,
) -> Generator[MainWindow, None, None]:
    """Fixture providing an initialized MainWindow instance with teardown cleanup."""
    window = MainWindow(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lan_ip="192.168.1.100",
        port=8000,
    )
    yield window
    window.close()
    window.deleteLater()


# ==============================================================================
# 1. QR Code Pixmap Generation Tests (qr_helper.py)
# ==============================================================================


class TestQrHelper:
    """Tests for `generate_qr_pixmap` utility function."""

    def test_generate_qr_pixmap_returns_valid_pixmap(self, qapp: QApplication) -> None:
        """generate_qr_pixmap should return a non-null, valid QPixmap instance."""
        data_url = "http://192.168.1.100:8000/?ott=sample_one_time_token"
        pixmap = generate_qr_pixmap(data_url)

        assert isinstance(pixmap, QPixmap)
        assert not pixmap.isNull()
        assert pixmap.width() > 0
        assert pixmap.height() > 0

    def test_generate_qr_pixmap_default_size(self, qapp: QApplication) -> None:
        """Default size for generate_qr_pixmap should be 240x240 pixels."""
        data_url = "http://192.168.1.100:8000/?ott=sample_token"
        pixmap = generate_qr_pixmap(data_url)

        assert pixmap.width() == 240
        assert pixmap.height() == 240

    @pytest.mark.parametrize("target_size", [120, 200, 300, 480])
    def test_generate_qr_pixmap_custom_sizes(
        self, qapp: QApplication, target_size: int
    ) -> None:
        """generate_qr_pixmap should accurately scale output pixmap to requested dimensions."""
        data_url = "http://192.168.1.100:8000/?ott=scaling_test"
        pixmap = generate_qr_pixmap(data_url, size=target_size)

        assert pixmap.width() == target_size
        assert pixmap.height() == target_size
        assert not pixmap.isNull()

    def test_generate_qr_pixmap_handles_arbitrary_payloads(
        self, qapp: QApplication
    ) -> None:
        """generate_qr_pixmap should handle empty strings, unicode characters, and long payloads."""
        payloads = [
            "",
            "https://riftpilot.lan/connect?token=çok_gizli_şifre_123",
            "A" * 500,
        ]
        for payload in payloads:
            pixmap = generate_qr_pixmap(payload, size=180)
            assert isinstance(pixmap, QPixmap)
            assert not pixmap.isNull()
            assert pixmap.width() == 180
            assert pixmap.height() == 180

    def test_generate_qr_pixmap_invalid_size_raises_value_error(
        self, qapp: QApplication
    ) -> None:
        """generate_qr_pixmap should raise ValueError when non-positive size is provided."""
        with pytest.raises(ValueError):
            generate_qr_pixmap("http://192.168.1.100:8000", size=0)

        with pytest.raises(ValueError):
            generate_qr_pixmap("http://192.168.1.100:8000", size=-50)


# ==============================================================================
# 2. MainWindow Initialization & Layout Tests
# ==============================================================================


class TestMainWindowInitialization:
    """Tests for MainWindow initial state, branding, and widget presence."""

    def test_window_title_contains_riftpilot(self, main_window: MainWindow) -> None:
        """MainWindow windowTitle should contain 'RiftPilot' branding."""
        assert "RiftPilot" in main_window.windowTitle()

    def test_window_displays_qr_label_with_valid_pixmap(
        self, main_window: MainWindow
    ) -> None:
        """MainWindow should contain a QLabel with a populated, non-null QR pixmap."""
        qr_label = getattr(main_window, "qr_label", None)
        if qr_label is None or not isinstance(qr_label, QLabel):
            # Fallback inspection: search child QLabels for one holding a QR pixmap
            labels = main_window.findChildren(QLabel)
            qr_labels = [lbl for lbl in labels if lbl.pixmap() and not lbl.pixmap().isNull()]
            assert len(qr_labels) > 0, "No QLabel with non-null QPixmap found in MainWindow"
            qr_label = qr_labels[0]

        assert qr_label is not None
        pixmap = qr_label.pixmap()
        assert pixmap is not None
        assert not pixmap.isNull()
        assert pixmap.width() >= 100
        assert pixmap.height() >= 100

    def test_window_displays_lan_endpoint_info(
        self,
        qapp: QApplication,
        ott_manager: OTTManager,
        session_manager: SessionManager,
    ) -> None:
        """MainWindow should display the configured LAN IP and port in the UI."""
        lan_ip = "192.168.1.145"
        port = 9090
        window = MainWindow(
            ott_manager=ott_manager,
            session_manager=session_manager,
            lan_ip=lan_ip,
            port=port,
        )
        try:
            labels_text = [lbl.text() for lbl in window.findChildren(QLabel)]
            full_text = " ".join(labels_text)
            assert lan_ip in full_text
            assert str(port) in full_text
        finally:
            window.close()
            window.deleteLater()

    def test_initial_device_list_shows_empty_state(
        self, main_window: MainWindow
    ) -> None:
        """MainWindow initialized with no active sessions should indicate an empty state."""
        labels_text = [lbl.text().lower() for lbl in main_window.findChildren(QLabel)]
        device_list = getattr(main_window, "device_list", None)

        has_empty_indicator = False
        # Check label indication
        for text in labels_text:
            if "yok" in text or "no connected" in text or "henüz" in text or "bağlı cihaz" in text:
                has_empty_indicator = True
                break

        # Check list items indication
        if not has_empty_indicator and isinstance(device_list, QListWidget):
            if device_list.count() == 0:
                has_empty_indicator = True
            else:
                for i in range(device_list.count()):
                    item_text = device_list.item(i).text().lower()
                    if "yok" in item_text or "no device" in item_text or "bağlı" in item_text:
                        has_empty_indicator = True
                        break

        assert has_empty_indicator, (
            "MainWindow should show an empty-state warning/notice when no devices are connected"
        )


# ==============================================================================
# 3. MainWindow QR Refresh Flow
# ==============================================================================


class TestMainWindowQrRefresh:
    """Tests for QR code refresh logic and OTT generation triggers."""

    def test_refresh_qr_triggers_ott_generation(
        self,
        main_window: MainWindow,
        ott_manager: OTTManager,
    ) -> None:
        """Calling refresh_qr() must invoke ott_manager.generate_ott()."""
        with patch.object(ott_manager, "generate_ott", wraps=ott_manager.generate_ott) as spy_gen:
            main_window.refresh_qr()
            assert spy_gen.call_count >= 1

    def test_refresh_qr_updates_pixmap(self, main_window: MainWindow) -> None:
        """Calling refresh_qr() must update the QR label with a valid, non-null pixmap."""
        qr_label = getattr(main_window, "qr_label", None)
        if qr_label is None or not isinstance(qr_label, QLabel):
            labels = main_window.findChildren(QLabel)
            qr_labels = [lbl for lbl in labels if lbl.pixmap() and not lbl.pixmap().isNull()]
            assert qr_labels
            qr_label = qr_labels[0]

        main_window.refresh_qr()

        pixmap = qr_label.pixmap()
        assert pixmap is not None
        assert not pixmap.isNull()

    def test_refresh_button_triggers_refresh_qr(self, main_window: MainWindow) -> None:
        """Clicking the UI refresh button should trigger MainWindow.refresh_qr."""
        refresh_button = getattr(main_window, "refresh_button", None)
        if refresh_button is None or not isinstance(refresh_button, QPushButton):
            buttons = [
                btn for btn in main_window.findChildren(QPushButton)
                if "yenile" in btn.text().lower() or "refresh" in btn.text().lower()
            ]
            if buttons:
                refresh_button = buttons[0]

        if refresh_button is not None:
            with patch.object(main_window, "refresh_qr") as spy_refresh:
                refresh_button.click()
                spy_refresh.assert_called_once()


# ==============================================================================
# 4. MainWindow Device List & Revocation Tests
# ==============================================================================


class TestMainWindowDeviceListAndRevocation:
    """Tests for connected devices listing, empty states, and revocation actions."""

    def test_update_device_list_empty_state_warning(
        self,
        main_window: MainWindow,
        session_manager: SessionManager,
    ) -> None:
        """update_device_list() with zero sessions should display an empty state warning."""
        assert len(session_manager.list_active_sessions()) == 0

        main_window.update_device_list()

        device_list = getattr(main_window, "device_list", None)
        labels_text = [lbl.text().lower() for lbl in main_window.findChildren(QLabel)]

        has_empty_indicator = False
        for text in labels_text:
            if "yok" in text or "no" in text or "bağlı cihaz" in text:
                has_empty_indicator = True
                break

        if not has_empty_indicator and isinstance(device_list, QListWidget):
            has_empty_indicator = (
                device_list.count() == 0
                or any("yok" in device_list.item(i).text().lower() for i in range(device_list.count()))
            )

        assert has_empty_indicator

    def test_update_device_list_displays_connected_device_name(
        self,
        main_window: MainWindow,
        session_manager: SessionManager,
    ) -> None:
        """When a session is created, update_device_list() should show the device name."""
        device_name = "iPhone 15"
        client_id = "dev-1"
        session_manager.create_session(device_name=device_name, client_instance_id=client_id)

        main_window.update_device_list()

        # Collect text from list items and item widgets
        device_list = getattr(main_window, "device_list", None)
        found_device = False

        if isinstance(device_list, QListWidget):
            for i in range(device_list.count()):
                item = device_list.item(i)
                if device_name in item.text():
                    found_device = True
                    break
                widget = device_list.itemWidget(item)
                if widget:
                    for lbl in widget.findChildren(QLabel):
                        if device_name in lbl.text():
                            found_device = True
                            break

        if not found_device:
            # Fallback: check all child labels in MainWindow
            all_labels = [lbl.text() for lbl in main_window.findChildren(QLabel)]
            found_device = any(device_name in text for text in all_labels)

        assert found_device, f"Device name '{device_name}' was not found in MainWindow device list"

    def test_update_device_list_shows_multiple_devices(
        self,
        main_window: MainWindow,
        session_manager: SessionManager,
    ) -> None:
        """update_device_list() should display all active device sessions."""
        session_manager.create_session("iPhone 15 Pro", "dev-iphone")
        session_manager.create_session("Samsung Galaxy S24", "dev-galaxy")

        main_window.update_device_list()

        device_list = getattr(main_window, "device_list", None)
        all_text = ""
        if isinstance(device_list, QListWidget):
            items_text = [device_list.item(i).text() for i in range(device_list.count())]
            for i in range(device_list.count()):
                widget = device_list.itemWidget(device_list.item(i))
                if widget:
                    items_text.extend(lbl.text() for lbl in widget.findChildren(QLabel))
            all_text = " ".join(items_text)
        else:
            all_text = " ".join(lbl.text() for lbl in main_window.findChildren(QLabel))

        assert "iPhone 15 Pro" in all_text
        assert "Samsung Galaxy S24" in all_text

    def test_revoke_device_action_triggers_session_manager_revoke(
        self,
        main_window: MainWindow,
        session_manager: SessionManager,
    ) -> None:
        """Revoking a device session from MainWindow must invoke session_manager.revoke_session()."""
        session = session_manager.create_session("iPad Air", "dev-ipad")
        token = session.session_token
        main_window.update_device_list()

        with patch.object(
            session_manager, "revoke_session", wraps=session_manager.revoke_session
        ) as spy_revoke:
            # Check supported revocation method names on MainWindow contract
            if hasattr(main_window, "revoke_device"):
                main_window.revoke_device(token)
            elif hasattr(main_window, "revoke_session"):
                main_window.revoke_session(token)
            else:
                # If revocation is invoked via button click on the list item widget
                device_list = getattr(main_window, "device_list", None)
                button_clicked = False
                if isinstance(device_list, QListWidget):
                    for i in range(device_list.count()):
                        widget = device_list.itemWidget(device_list.item(i))
                        if widget:
                            revoke_buttons = [
                                btn for btn in widget.findChildren(QPushButton)
                                if "kaldır" in btn.text().lower() or "revoke" in btn.text().lower()
                            ]
                            if revoke_buttons:
                                revoke_buttons[0].click()
                                button_clicked = True
                                break
                assert button_clicked, "Could not find revocation method or revoke button on MainWindow"

            spy_revoke.assert_called_once_with(token)
            assert not session.is_active

    def test_revoked_device_is_removed_from_ui(
        self,
        main_window: MainWindow,
        session_manager: SessionManager,
    ) -> None:
        """After revoking a session, it should no longer be displayed in the device list."""
        session1 = session_manager.create_session("Device To Keep", "dev-keep")
        session2 = session_manager.create_session("Device To Revoke", "dev-revoke")

        main_window.update_device_list()

        # Revoke session2
        if hasattr(main_window, "revoke_device"):
            main_window.revoke_device(session2.session_token)
        elif hasattr(main_window, "revoke_session"):
            main_window.revoke_session(session2.session_token)
        else:
            session_manager.revoke_session(session2.session_token)
            main_window.update_device_list()

        device_list = getattr(main_window, "device_list", None)
        all_text = ""
        if isinstance(device_list, QListWidget):
            all_text = " ".join(device_list.item(i).text() for i in range(device_list.count()))
        else:
            all_text = " ".join(lbl.text() for lbl in main_window.findChildren(QLabel))

        assert "Device To Keep" in all_text
        assert "Device To Revoke" not in all_text
