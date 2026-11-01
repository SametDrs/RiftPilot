"""RiftPilot UI — PySide6 Desktop Main Window.

Implements the desktop companion GUI:
- Displays local LAN pairing QR code with auto-refresh / manual refresh.
- Displays active LAN hub endpoint (IP:Port).
- Manages connected mobile device sessions with live revocation controls.
- Provides dark-themed modern desktop aesthetic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from riftpilot.ui.qr_helper import generate_qr_pixmap

if TYPE_CHECKING:
    from riftpilot.core.pairing import OTTManager, SessionManager

logger = logging.getLogger("RiftPilot.UI")

DARK_STYLE = """
QMainWindow {
    background-color: #0b0e14;
}
QWidget {
    color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
QFrame#card {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 12px;
}
QLabel#title {
    color: #58a6ff;
    font-size: 16px;
    font-weight: bold;
}
QLabel#subtitle {
    color: #8b949e;
    font-size: 12px;
}
QLabel#statusBadge {
    background-color: rgba(46, 160, 67, 0.15);
    color: #3fb950;
    border: 1px solid #2ea043;
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}
QPushButton#refreshBtn {
    background-color: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#refreshBtn:hover {
    background-color: #30363d;
}
QPushButton#revokeBtn {
    background-color: rgba(248, 81, 73, 0.15);
    color: #f85149;
    border: 1px solid #da3633;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 11px;
    font-weight: 600;
}
QPushButton#revokeBtn:hover {
    background-color: #da3633;
    color: white;
}
QListWidget {
    background-color: #0d1117;
    border: 1px solid #21262d;
    border-radius: 6px;
    padding: 4px;
}
QListWidget::item {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    margin-bottom: 4px;
    padding: 6px;
}
"""


class MainWindow(QMainWindow):
    """Main desktop application window for RiftPilot."""

    def __init__(
        self,
        ott_manager: OTTManager,
        session_manager: SessionManager,
        lan_ip: str,
        port: int,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize the desktop companion window.

        Args:
            ott_manager: Core domain One-Time Token manager.
            session_manager: Core domain device session manager.
            lan_ip: Active local IPv4 address of the host machine.
            port: Local HTTP/WS port.
            parent: Optional parent QWidget.
        """
        super().__init__(parent)
        self.ott_manager = ott_manager
        self.session_manager = session_manager
        self.lan_ip = lan_ip
        self.port = port

        self.setWindowTitle("RiftPilot — League Remote Companion")
        self.resize(420, 640)
        self.setStyleSheet(DARK_STYLE)

        self._setup_ui()
        self.refresh_qr()
        self.update_device_list()

    def _setup_ui(self) -> None:
        """Construct child layout and widgets."""
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 1. Header (Logo & LAN Status)
        header_layout = QHBoxLayout()
        title_box = QVBoxLayout()
        title_label = QLabel("🎮 RiftPilot", self)
        title_label.setObjectName("title")
        subtitle_label = QLabel("League of Legends Uzaktan Yönetim", self)
        subtitle_label.setObjectName("subtitle")
        title_box.addWidget(title_label)
        title_box.addWidget(subtitle_label)

        status_badge = QLabel("● LAN Hub Aktif", self)
        status_badge.setObjectName("statusBadge")
        header_layout.addLayout(title_box)
        header_layout.addStretch()
        header_layout.addWidget(status_badge)
        main_layout.addLayout(header_layout)

        # 2. LAN Endpoint Info
        self.lan_info_label = QLabel(
            f"Adres: http://{self.lan_ip}:{self.port}/", self
        )
        self.lan_info_label.setObjectName("subtitle")
        self.lan_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.lan_info_label)

        # 3. QR Code Card
        qr_card = QFrame(self)
        qr_card.setObjectName("card")
        qr_layout = QVBoxLayout(qr_card)
        qr_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.qr_label = QLabel(self)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(240, 240)
        qr_layout.addWidget(self.qr_label)

        hint_label = QLabel("Telefon kamerasıyla QR kodu tarayın", self)
        hint_label.setObjectName("subtitle")
        hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(hint_label)

        self.refresh_button = QPushButton("🔄 QR Kodu Yenile", self)
        self.refresh_button.setObjectName("refreshBtn")
        self.refresh_button.clicked.connect(self.refresh_qr)
        qr_layout.addWidget(self.refresh_button)
        main_layout.addWidget(qr_card)

        # 4. Connected Devices Section
        devices_header_layout = QHBoxLayout()
        devices_title = QLabel("📱 Bağlı Cihazlar", self)
        devices_title.setObjectName("title")
        devices_header_layout.addWidget(devices_title)
        devices_header_layout.addStretch()
        main_layout.addLayout(devices_header_layout)

        self.empty_state_label = QLabel("Henüz bağlı cihaz yok.", self)
        self.empty_state_label.setObjectName("subtitle")
        self.empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.empty_state_label)

        self.device_list = QListWidget(self)
        self.device_list.setMaximumHeight(160)
        main_layout.addWidget(self.device_list)

    def refresh_qr(self) -> None:
        """Generate a new OTT token and update the displayed QR pixmap."""
        ott = self.ott_manager.generate_ott(ttl_seconds=90.0)
        target_url = f"http://{self.lan_ip}:{self.port}/?ott={ott.token}"
        pixmap = generate_qr_pixmap(target_url, size=240)
        self.qr_label.setPixmap(pixmap)
        logger.debug(
            "QR kodu güncellendi: http://%s:%s/?ott=%s...",
            self.lan_ip,
            self.port,
            ott.token[:6],
        )

    def update_device_list(self) -> None:
        """Synchronize the UI device list with active sessions in SessionManager."""
        self.device_list.clear()
        active_sessions = self.session_manager.list_active_sessions()

        if not active_sessions:
            self.empty_state_label.show()
            self.device_list.hide()
            return

        self.empty_state_label.hide()
        self.device_list.show()

        for session in active_sessions:
            item = QListWidgetItem()
            # Store device name in item text for inspection / accessibility
            item.setText(session.device_name)

            # Custom row widget with revoke button
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(8, 4, 8, 4)

            name_lbl = QLabel(f"📱 {session.device_name}", row_widget)
            font = QFont()
            font.setBold(True)
            name_lbl.setFont(font)
            row_layout.addWidget(name_lbl)
            row_layout.addStretch()

            revoke_btn = QPushButton("Yetkiyi Kaldır", row_widget)
            revoke_btn.setObjectName("revokeBtn")
            # Capture session_token in closure
            token = session.session_token
            revoke_btn.clicked.connect(lambda _, t=token: self.revoke_session(t))
            row_layout.addWidget(revoke_btn)

            item.setSizeHint(row_widget.sizeHint())
            self.device_list.addItem(item)
            self.device_list.setItemWidget(item, row_widget)

    def revoke_session(self, session_token: str) -> bool:
        """Revoke a session and update the UI."""
        result = self.session_manager.revoke_session(session_token)
        self.update_device_list()
        return result

    def revoke_device(self, session_token_or_client_id: str) -> bool:
        """Alias / helper to revoke by session token or client id."""
        # First attempt by token
        if self.session_manager.revoke_session(session_token_or_client_id):
            self.update_device_list()
            return True
        # Then attempt by device id
        result = self.session_manager.revoke_device(session_token_or_client_id)
        self.update_device_list()
        return result


__all__ = ["MainWindow"]
