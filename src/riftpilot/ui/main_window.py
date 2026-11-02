"""RiftPilot UI — PySide6 Modernized Desktop Main Window (LeagueCompanion Aesthetic).

Implements the modernized desktop companion GUI:
- League of Legends / LeagueCompanion inspired dark Hextech aesthetic.
- 3-Tab structured layout:
  1. 📊 Genel Bakış (Dashboard): Live LCU status, gameflow phase hero card, LAN Hub status.
  2. 📱 Mobil Eşleştirme (QR & Devices): Premium QR card, active device list, revocation.
  3. ⚙️ Sistem & Ayarlar (Settings): Network config, LCU discovery info, architecture notes.
- Thread-safe LCU UI Bridge for cross-thread async Qt signaling.
- Periodic timer synchronization for real-time device updates and LCU monitoring.
- Scalable, responsive layout with fluid window resizing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMenu,
    QSystemTrayIcon,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from riftpilot.core.lcu_port import (
    GameflowPhase,
    ILcuPort,
    LcuConnectionStatus,
)
from riftpilot.core.queue_timer import QueueTimer
from riftpilot.ui.qr_helper import generate_qr_pixmap

if TYPE_CHECKING:
    from riftpilot.core.pairing import OTTManager, SessionManager

logger = logging.getLogger("RiftPilot.UI")

MAIN_WINDOW_STYLE = """
/* Global Window & Typography */
QMainWindow {
    background-color: #070b13;
}
QWidget {
    color: #f0e6d2;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
    selection-background-color: #0ac8b9;
    selection-color: #070b13;
}

/* Tabs Navigation */
QTabWidget::pane {
    border: 1px solid rgba(200, 170, 110, 0.25);
    border-radius: 12px;
    background-color: #0b111c;
    top: -1px;
}
QTabBar::tab {
    background: #0d1527;
    color: #a09b8c;
    font-size: 13px;
    font-weight: 700;
    padding: 10px 20px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    border: 1px solid rgba(200, 170, 110, 0.15);
    border-bottom: none;
    min-width: 100px;
}
QTabBar::tab:selected {
    background: linear-gradient(180deg, #16243d 0%, #0b111c 100%);
    color: #f0e6d2;
    border-color: #c8aa6e;
    border-bottom: 2px solid #c8aa6e;
}
QTabBar::tab:hover:!selected {
    background: #14223d;
    color: #c8aa6e;
}

/* Hextech Cards & Panels */
QFrame#card {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #121a2c, stop:1 #0c1220);
    border: 1px solid rgba(200, 170, 110, 0.25);
    border-radius: 12px;
    padding: 14px;
}
QFrame#heroCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(10, 200, 185, 0.12), stop:1 rgba(200, 170, 110, 0.08));
    border: 1px solid rgba(10, 200, 185, 0.4);
    border-radius: 12px;
    padding: 16px;
}
QFrame#qrFrame {
    background-color: #060911;
    border: 2px solid rgba(200, 170, 110, 0.4);
    border-radius: 12px;
    padding: 12px;
}

/* Headers & Labels */
QLabel#brandTitle {
    color: #f0e6d2;
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 0.5px;
}
QLabel#sectionTitle {
    color: #c8aa6e;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}
QLabel#subtitle {
    color: #a09b8c;
    font-size: 12px;
    line-height: 1.4;
}
QLabel#heroValue {
    color: #0ac8b9;
    font-size: 18px;
    font-weight: 800;
}
QLabel#endpointBadge {
    background: rgba(10, 200, 185, 0.12);
    color: #0ac8b9;
    border: 1px solid rgba(10, 200, 185, 0.35);
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    font-weight: 600;
    font-family: "Cascadia Code", "Consolas", monospace;
}
QLabel#statusBadge {
    background-color: rgba(0, 255, 136, 0.12);
    color: #00ff88;
    border: 1px solid rgba(0, 255, 136, 0.35);
    border-radius: 14px;
    padding: 4px 12px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
}

/* Modern Action Buttons */
QPushButton#refreshBtn {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #c8aa6e, stop:1 #785a28);
    color: #070b13;
    border: 1px solid #f0e6d2;
    border-radius: 8px;
    padding: 10px 18px;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 0.5px;
}
QPushButton#refreshBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f0e6d2, stop:1 #a88a4e);
}
QPushButton#refreshBtn:pressed {
    background: #785a28;
}
QLabel#deviceSubtitle {
    color: #0ac8b9;
    font-size: 11px;
    font-weight: 500;
}

/* Revoke / Disconnect Button */
QPushButton#revokeBtn {
    background-color: rgba(232, 64, 87, 0.15);
    color: #ff8595;
    border: 1px solid rgba(232, 64, 87, 0.45);
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 11px;
    font-weight: 700;
    min-width: 115px;
    min-height: 28px;
}
QPushButton#revokeBtn:hover {
    background-color: #e84057;
    color: #ffffff;
    border: 1px solid #ff8595;
}
QPushButton#revokeBtn:pressed {
    background-color: #c03246;
    color: #ffffff;
    border: 1px solid #e84057;
}
QPushButton#revokeBtn:disabled {
    background-color: rgba(255, 255, 255, 0.05);
    color: rgba(255, 255, 255, 0.3);
    border: 1px solid rgba(255, 255, 255, 0.1);
}

/* Device List Widget */
QListWidget {
    background-color: #080d16;
    border: 1px solid rgba(200, 170, 110, 0.2);
    border-radius: 8px;
    padding: 6px;
}
QListWidget::item {
    background-color: #0f1726;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    margin-bottom: 6px;
    padding: 4px;
}
QListWidget::item:hover {
    border-color: rgba(200, 170, 110, 0.4);
}
"""
HEXTECH_STYLE = MAIN_WINDOW_STYLE


class LcuUiBridge(QObject):
    """PySide6 QObject bridge translating async/background LCU events to Qt signals."""

    phase_changed = Signal(object)
    connection_status_changed = Signal(object)
    device_list_changed = Signal()

    def __init__(
        self,
        lcu_adapter: ILcuPort | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.lcu_adapter = lcu_adapter
        if self.lcu_adapter is not None:
            self.lcu_adapter.subscribe_phase_change(self.notify_phase_change)

    def notify_phase_change(self, phase: GameflowPhase | str) -> None:
        self.phase_changed.emit(phase)

    def on_phase_change(self, phase: GameflowPhase | str) -> None:
        self.notify_phase_change(phase)

    def notify_status_change(self, status: LcuConnectionStatus | str) -> None:
        self.connection_status_changed.emit(status)

    def on_status_change(self, status: LcuConnectionStatus | str) -> None:
        self.notify_status_change(status)

    def notify_device_list_changed(self) -> None:
        self.device_list_changed.emit()


class MainWindow(QMainWindow):
    """Modern LeagueCompanion-styled desktop companion application for RiftPilot."""

    def __init__(
        self,
        ott_manager: OTTManager,
        session_manager: SessionManager,
        lan_ip: str,
        port: int,
        lcu_adapter: ILcuPort | None = None,
        parent: QWidget | None = None,
    ) -> None:
        if isinstance(lcu_adapter, QWidget) and parent is None:
            parent = lcu_adapter
            lcu_adapter = None

        super().__init__(parent)
        self.ott_manager = ott_manager
        self.session_manager = session_manager
        self.lan_ip = lan_ip
        self.port = port
        self.lcu_adapter = lcu_adapter
        self._is_quitting: bool = False
        self.tray_icon: QSystemTrayIcon | None = None
        self.queue_timer = QueueTimer()
        self.current_phase: GameflowPhase | str | None = None

        self.setWindowTitle("RiftPilot — Hextech League Companion")
        self.setFixedSize(500, 780)
        self.setStyleSheet(HEXTECH_STYLE)

        self._setup_ui()

        # Thread-safe UI bridge for LCU events
        self.bridge = LcuUiBridge(lcu_adapter=self.lcu_adapter, parent=self)
        self.bridge.phase_changed.connect(self.on_gameflow_phase_changed)
        self.bridge.connection_status_changed.connect(self.on_lcu_status_changed)
        self.bridge.device_list_changed.connect(self.update_device_list)

        self.refresh_qr()
        self.update_device_list()

        # Check initial connection status if adapter is already connected
        if self.lcu_adapter is not None:
            creds = getattr(self.lcu_adapter, "credentials", None)
            stat = getattr(self.lcu_adapter, "_status", None)
            if creds is not None or stat == LcuConnectionStatus.CONNECTED:
                self.on_lcu_status_changed(LcuConnectionStatus.CONNECTED)
            else:
                self.on_lcu_status_changed(LcuConnectionStatus.DISCONNECTED)

        # Periodic timer for auto-updating device list and connection sync
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(1500)
        self.poll_timer.timeout.connect(self._on_poll_tick)
        self.poll_timer.start()

    def _setup_ui(self) -> None:
        """Construct child layout and widgets with 3-tab modern structure."""
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(14)

        # 1. Header (Brand Crest & LAN Hub Status)
        header_layout = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        brand_title = QLabel("⚡ RiftPilot", self)
        brand_title.setObjectName("brandTitle")
        subtitle_label = QLabel("League of Legends Uzaktan Yönetim İstasyonu", self)
        subtitle_label.setObjectName("subtitle")
        title_box.addWidget(brand_title)
        title_box.addWidget(subtitle_label)

        status_badge = QLabel("● LAN HUB AKTİF", self)
        status_badge.setObjectName("statusBadge")
        header_layout.addLayout(title_box)
        header_layout.addStretch()
        header_layout.addWidget(status_badge)
        main_layout.addLayout(header_layout)

        # 2. Modern Tabbed Layout
        self.tabs = QTabWidget(self)

        # ---- Tab 1: Dashboard (Genel Bakış) ----
        dashboard_tab = QWidget()
        dash_layout = QVBoxLayout(dashboard_tab)
        dash_layout.setContentsMargins(14, 14, 14, 14)
        dash_layout.setSpacing(12)

        # LCU Hero Card
        lcu_hero = QFrame(self)
        lcu_hero.setObjectName("heroCard")
        lcu_hero_layout = QVBoxLayout(lcu_hero)
        lcu_hero_layout.setSpacing(8)

        lcu_title_row = QHBoxLayout()
        lcu_title_lbl = QLabel("LEAGUE CLIENT UPDATE (LCU)", self)
        lcu_title_lbl.setObjectName("sectionTitle")
        lcu_title_row.addWidget(lcu_title_lbl)
        lcu_title_row.addStretch()
        lcu_hero_layout.addLayout(lcu_title_row)

        self.lcu_status_label = QLabel("🔴 LoL İstemcisi: İstemci Bekleniyor...", self)
        self.lcu_status_label.setObjectName("heroValue")
        lcu_hero_layout.addWidget(self.lcu_status_label)

        self.gameflow_label = QLabel("🎮 Oyun Durumu: -", self)
        self.gameflow_label.setObjectName("subtitle")
        lcu_hero_layout.addWidget(self.gameflow_label)
        dash_layout.addWidget(lcu_hero)

        # LAN Endpoint & Quick Connect Card
        lan_card = QFrame(self)
        lan_card.setObjectName("card")
        lan_layout = QVBoxLayout(lan_card)
        lan_layout.setSpacing(8)

        lan_title = QLabel("YEREL AĞ ERİŞİM NOKTASI (LAN HUB)", self)
        lan_title.setObjectName("sectionTitle")
        lan_layout.addWidget(lan_title)

        self.lan_info_label = QLabel(
            f"Adres: http://{self.lan_ip}:{self.port}/", self
        )
        self.lan_info_label.setObjectName("endpointBadge")
        self.lan_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lan_layout.addWidget(self.lan_info_label)

        lan_hint = QLabel("Aynı Wi-Fi ağına bağlı mobil cihazınızın tarayıcısından bağlanabilirsiniz.", self)
        lan_hint.setObjectName("subtitle")
        lan_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lan_layout.addWidget(lan_hint)
        dash_layout.addWidget(lan_card)

        # Connected Devices Summary Card on Dashboard
        summary_card = QFrame(self)
        summary_card.setObjectName("card")
        summary_layout = QHBoxLayout(summary_card)
        self.active_count_label = QLabel("📱 Bağlı Cihaz: 0", self)
        self.active_count_label.setObjectName("sectionTitle")
        summary_layout.addWidget(self.active_count_label)
        summary_layout.addStretch()

        to_pairing_btn = QPushButton("Eşleştirmeye Git →", self)
        to_pairing_btn.setObjectName("refreshBtn")
        to_pairing_btn.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        summary_layout.addWidget(to_pairing_btn)
        dash_layout.addWidget(summary_card)

        dash_layout.addStretch()
        self.tabs.addTab(dashboard_tab, "📊 Genel Bakış")

        # ---- Tab 2: Mobil Eşleştirme (QR & Cihazlar) ----
        pairing_tab = QWidget()
        pairing_layout = QVBoxLayout(pairing_tab)
        pairing_layout.setContentsMargins(14, 14, 14, 14)
        pairing_layout.setSpacing(12)

        # Modern QR Card
        qr_card = QFrame(self)
        qr_card.setObjectName("card")
        qr_layout = QVBoxLayout(qr_card)
        qr_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.setSpacing(10)

        qr_title = QLabel("TEK KULLANIMLIK QR EŞLEŞTİRME", self)
        qr_title.setObjectName("sectionTitle")
        qr_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(qr_title)

        qr_frame = QFrame(self)
        qr_frame.setObjectName("qrFrame")
        qr_frame_layout = QVBoxLayout(qr_frame)
        qr_frame_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_frame_layout.setContentsMargins(6, 6, 6, 6)

        self.qr_label = QLabel(self)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(240, 240)
        qr_frame_layout.addWidget(self.qr_label)
        qr_layout.addWidget(qr_frame)

        hint_label = QLabel("Telefon kamerasıyla QR kodu tarayın (Burn-on-Read OTT)", self)
        hint_label.setObjectName("subtitle")
        hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(hint_label)

        self.refresh_button = QPushButton("🔄 QR Kodu Yenile", self)
        self.refresh_button.setObjectName("refreshBtn")
        self.refresh_button.clicked.connect(self.refresh_qr)
        qr_layout.addWidget(self.refresh_button)
        pairing_layout.addWidget(qr_card)

        # Connected Devices Section
        devices_card = QFrame(self)
        devices_card.setObjectName("card")
        devices_layout = QVBoxLayout(devices_card)
        devices_layout.setSpacing(8)

        devices_title = QLabel("📱 Bağlı Cihazlar & Yetkilendirme", self)
        devices_title.setObjectName("sectionTitle")
        devices_layout.addWidget(devices_title)

        self.empty_state_label = QLabel("Henüz bağlı cihaz yok.", self)
        self.empty_state_label.setObjectName("subtitle")
        self.empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        devices_layout.addWidget(self.empty_state_label)

        self.device_list = QListWidget(self)
        self.device_list.setMaximumHeight(130)
        devices_layout.addWidget(self.device_list)
        pairing_layout.addWidget(devices_card)

        self.tabs.addTab(pairing_tab, "📱 Mobil Eşleştirme")

        # ---- Tab 3: Sistem & Ayarlar (Settings) ----
        settings_tab = QWidget()
        settings_layout = QVBoxLayout(settings_tab)
        settings_layout.setContentsMargins(14, 14, 14, 14)
        settings_layout.setSpacing(12)

        config_card = QFrame(self)
        config_card.setObjectName("card")
        cfg_layout = QVBoxLayout(config_card)
        cfg_layout.setSpacing(8)

        cfg_title = QLabel("SİSTEM YAPILANDIRMASI", self)
        cfg_title.setObjectName("sectionTitle")
        cfg_layout.addWidget(cfg_title)

        net_desc = QLabel(
            f"• Yerel IP: {self.lan_ip}\n"
            f"• HTTP & WebSocket Portu: {self.port}\n"
            f"• Güvenlik Modeli: Zero-Trust Session Token & One-Time Token (OTT)\n"
            f"• LCU Bağlantı Türü: Resmi REST API & WebSocket (HTTPX / SSL By-Pass)\n"
            f"• Mimari Model: Hexagonal Architecture (ADR-0003)\n"
            f"• Riot TOS Uyumluluğu: Belleğe müdahale edilmez (%100 Güvenli)",
            self,
        )
        net_desc.setObjectName("subtitle")
        cfg_layout.addWidget(net_desc)
        settings_layout.addWidget(config_card)

        about_card = QFrame(self)
        about_card.setObjectName("card")
        about_layout = QVBoxLayout(about_card)
        about_layout.setSpacing(6)
        about_title = QLabel("RIFTPILOT HAKKINDA", self)
        about_title.setObjectName("sectionTitle")
        about_layout.addWidget(about_title)
        about_text = QLabel(
            "RiftPilot, League of Legends istemcisini yerel ağ üzerinden mobil cihazınızdan "
            "kolayca yönetmenizi sağlayan modern ve hafif masaüstü orkestrasyon aracıdır.\n"
            "Sürüm: 0.3.0 (Milestone 3: Modern UI Edition)",
            self,
        )
        about_text.setObjectName("subtitle")
        about_layout.addWidget(about_text)
        settings_layout.addWidget(about_card)

        settings_layout.addStretch()
        self.tabs.addTab(settings_tab, "⚙️ Ayarlar")

        main_layout.addWidget(self.tabs)

    def _on_poll_tick(self) -> None:
        """Periodic sync for active device sessions, connection status, and queue timer."""
        self.update_device_list()
        if self.lcu_adapter is not None:
            creds = getattr(self.lcu_adapter, "credentials", None)
            stat = getattr(self.lcu_adapter, "_status", None)
            if creds is not None or stat == LcuConnectionStatus.CONNECTED:
                self.on_lcu_status_changed(LcuConnectionStatus.CONNECTED)
            else:
                self.on_lcu_status_changed(LcuConnectionStatus.DISCONNECTED)

        phase_str = (
            self.current_phase.value
            if hasattr(self.current_phase, "value")
            else str(self.current_phase or "")
        )
        if (
            self.current_phase == GameflowPhase.MATCHMAKING
            or phase_str.lower() == "matchmaking"
        ):
            self.queue_timer.tick()
            self.gameflow_label.setText(self.queue_timer.display_text)

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

        if hasattr(self, "active_count_label"):
            self.active_count_label.setText(f"📱 Bağlı Cihaz: {len(active_sessions)}")

        if not active_sessions:
            self.empty_state_label.show()
            self.device_list.hide()
            return

        self.empty_state_label.hide()
        self.device_list.show()

        for session in active_sessions:
            item = QListWidgetItem()
            # Explicitly set empty text on item so QListWidget doesn't render background delegate text
            item.setText("")
            item.setSizeHint(QSize(0, 58))

            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(10, 6, 10, 6)
            row_layout.setSpacing(8)

            # Left column with vertical layout
            left_col = QVBoxLayout()
            left_col.setContentsMargins(0, 0, 0, 0)
            left_col.setSpacing(2)

            raw_name = session.device_name or "Bilinmeyen Cihaz"
            max_name_len = 22
            if len(raw_name) > max_name_len:
                display_name = f"{raw_name[:max_name_len - 3]}..."
            else:
                display_name = raw_name

            title_lbl = QLabel(f"📱 {display_name}", row_widget)
            font = QFont()
            font.setBold(True)
            title_lbl.setFont(font)
            title_lbl.setToolTip(raw_name)
            left_col.addWidget(title_lbl)

            subtitle_lbl = QLabel("🟢 Mobil Web Paneli • Çevrimiçi", row_widget)
            subtitle_lbl.setObjectName("deviceSubtitle")
            subtitle_lbl.setStyleSheet("color: #0ac8b9; font-size: 11px;")
            left_col.addWidget(subtitle_lbl)

            row_layout.addLayout(left_col, stretch=1)

            # Right column: Revoke / Disconnect Button (revoke_btn)
            revoke_btn = QPushButton("✕ Bağlantıyı Kes", row_widget)
            revoke_btn.setObjectName("revokeBtn")
            revoke_btn.setMinimumWidth(115)
            revoke_btn.setMinimumHeight(28)
            token = session.session_token
            revoke_btn.clicked.connect(lambda _, t=token: self.revoke_session(t))
            row_layout.addWidget(revoke_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

            row_widget.setToolTip(raw_name)

            self.device_list.addItem(item)
            self.device_list.setItemWidget(item, row_widget)

    def revoke_session(self, session_token: str) -> bool:
        """Revoke a session and update the UI."""
        result = self.session_manager.revoke_session(session_token)
        self.update_device_list()
        return result

    def revoke_device(self, session_token_or_client_id: str) -> bool:
        """Alias / helper to revoke by session token or client id."""
        if self.session_manager.revoke_session(session_token_or_client_id):
            self.update_device_list()
            return True
        result = self.session_manager.revoke_device(session_token_or_client_id)
        self.update_device_list()
        return result


    def _init_system_tray(self) -> None:
        """Initialize desktop system tray icon and context menu."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.debug("System tray is not available in current environment.")
            return

        self.tray_icon = QSystemTrayIcon(self)
        # Use application window icon or fallback
        if not self.windowIcon().isNull():
            self.tray_icon.setIcon(self.windowIcon())
        elif self.qr_label.pixmap() and not self.qr_label.pixmap().isNull():
            self.tray_icon.setIcon(QIcon(self.qr_label.pixmap()))

        tray_menu = QMenu()
        show_action = tray_menu.addAction("📊 RiftPilot'ı Aç / Göster")
        show_action.triggered.connect(self.show_and_activate)

        hide_action = tray_menu.addAction("⬇️ Sistem Tepsisine Küçült")
        hide_action.triggered.connect(self.hide)

        tray_menu.addSeparator()

        quit_action = tray_menu.addAction("✕ Tamamen Kapat")
        quit_action.triggered.connect(self.quit_application)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible():
                self.hide()
            else:
                self.show_and_activate()

    def show_and_activate(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_application(self) -> None:
        self._is_quitting = True
        if self.tray_icon:
            self.tray_icon.hide()
        self.close()

    def closeEvent(self, event: Any) -> None:
        """Minimize to tray on window close button [X] instead of terminating."""
        if getattr(self, "_is_quitting", False) or not getattr(self, "tray_icon", None) or not self.tray_icon.isVisible():
            event.accept()
        else:
            event.ignore()
            self.hide()
            self.show_tray_notification(
                "RiftPilot Arka Planda Çalışıyor",
                "Mobil bağlantı açık kaldı. Açmak için tepsi simgesine çift tıklayın.",
            )

    def show_tray_notification(self, title: str, message: str) -> None:
        """Show a native OS tray balloon/toast notification if tray is available."""
        if self.tray_icon and self.tray_icon.isVisible():
            self.tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 4000)

    def on_gameflow_phase_changed(
        self,
        phase: GameflowPhase | str,
        estimated_duration: float = 0.0,
    ) -> None:
        """Update gameflow status label based on client phase."""
        self.current_phase = phase
        phase_str = phase.value if hasattr(phase, "value") else str(phase)
        is_matchmaking = (
            phase == GameflowPhase.MATCHMAKING
            or phase_str.lower() == "matchmaking"
        )

        if is_matchmaking:
            if not self.queue_timer.is_running:
                self.queue_timer.start(estimated_duration)
            elif estimated_duration > 0.0:
                self.queue_timer.update_estimated(estimated_duration)
            self.gameflow_label.setText(self.queue_timer.display_text)
        else:
            self.queue_timer.reset()

        if phase == GameflowPhase.READY_CHECK or phase_str == "ReadyCheck":
            self.gameflow_label.setText("⚔️ MAÇ BULUNDU!")
            self.show_tray_notification("⚔️ MAÇ BULUNDU!", "Eşleşme bulundu! Kabul etmek için hazır.")
        elif phase == GameflowPhase.CHAMP_SELECT or phase_str == "ChampSelect":
            self.gameflow_label.setText("🏆 Şampiyon Seçimi")
            self.show_tray_notification("🏆 Şampiyon Seçimi", "Sıra sizde veya seçim odası aktif.")
        elif is_matchmaking:
            pass
        elif (
            phase == GameflowPhase.IN_PROGRESS
            or phase_str.lower() in ("inprogress", "in_progress")
        ):
            if estimated_duration > 0.0:
                mins = int(estimated_duration) // 60
                secs = int(estimated_duration) % 60
                self.gameflow_label.setText(
                    f"⚔️ Maçtasın! (Maç Devam Ediyor - {mins:02d}:{secs:02d})"
                )
            else:
                self.gameflow_label.setText("⚔️ Maçtasın! (Maç Devam Ediyor)")
            self.show_tray_notification("⚔️ Maçtasın!", "Oyun devam ediyor.")
        elif phase == GameflowPhase.LOBBY or phase_str == "Lobby":
            self.gameflow_label.setText("🏠 Lobide")
        elif phase == GameflowPhase.NONE or phase_str == "None":
            self.gameflow_label.setText("🎮 Oyun Durumu: -")
        else:
            self.gameflow_label.setText(f"🎮 Oyun Durumu: {phase_str}")

    def start_queue_timer(self, estimated_duration: float = 0.0) -> None:
        """Start or update matchmaking queue timer."""
        self.current_phase = GameflowPhase.MATCHMAKING
        self.queue_timer.start(estimated_duration)
        self.gameflow_label.setText(self.queue_timer.display_text)

    def stop_queue_timer(self) -> None:
        """Stop and reset matchmaking queue timer."""
        self.queue_timer.reset()

    def on_lcu_status_changed(self, status: LcuConnectionStatus | str) -> None:
        """Update LCU connection status badge."""
        status_str = status.value if hasattr(status, "value") else str(status)
        if status == LcuConnectionStatus.CONNECTED or status_str.lower() in ("connected", "true"):
            port_info = ""
            if self.lcu_adapter and getattr(self.lcu_adapter, "credentials", None):
                port_info = f" (Port: {self.lcu_adapter.credentials.port})"
            self.lcu_status_label.setText(f"🟢 LoL İstemcisi: LCU Bağlandı{port_info}")
        else:
            self.lcu_status_label.setText("🔴 LoL İstemcisi: İstemci Bekleniyor...")

    # Compatibility aliases
    on_phase_changed = on_gameflow_phase_changed
    set_lcu_status = on_lcu_status_changed


__all__ = ["LcuUiBridge", "MainWindow", "MAIN_WINDOW_STYLE", "HEXTECH_STYLE"]
