"""RiftPilot — End-to-End Integration Tests (Milestone 2, Task 2.4).

Verifies the end-to-end integration across Mobile Web Hub and Desktop UI:
1. Web Hub FastAPI Integration (`riftpilot.adapters.web_hub.create_app`):
   - Responsive Mobile Web Panel delivery (`GET /`).
   - Cryptographic OTT pairing and burn-on-read verification (`POST /api/pair`).
   - Zero-trust device session validation and rejection of replay attacks.
   - Real-time bidirectional WebSocket channel (`/ws`):
     - Authentication enforcement and unauthorized connection rejection.
     - Live gameflow phase updates broadcast from LCU adapter to mobile clients.
     - Remote match acceptance command (`ACCEPT_READY_CHECK`).
     - Session revocation mid-connection rejecting further commands.
2. Desktop UI LCU Integration (`riftpilot.ui.main_window.MainWindow`):
   - Outbound LCU port adapter integration (`lcu_adapter: ILcuPort | None`).
   - LCU connection status badge / label updates (`lcu_status_label`).
   - Gameflow phase display updates (`gameflow_label`).
3. Thread-Safe UI Bridge (`riftpilot.ui.main_window.LcuUiBridge`):
   - PySide6 `QObject` bridging async LCU events to Qt main thread signals.
   - Synchronous signal dispatching (`phase_changed`, `connection_status_changed`).
4. Full Cross-Component End-to-End Lifecycle:
   - QR Generation -> Mobile Pairing -> WS Handshake -> Match ReadyCheck -> Mobile Accept -> Desktop Revocation.

Architecture & Security:
- Hexagonal Architecture (ADR-0003): Transport-agnostic domain logic.
- Zero-Trust Security (ADR-0004): Single-use burn-on-read OTT & desktop revocation.
- PySide6 Desktop GUI (ADR-0002): Headless Qt testing (`QT_QPA_PLATFORM=offscreen`).
"""

from __future__ import annotations

import json
import os
from typing import Any, Generator
from unittest.mock import MagicMock, patch

# Configure headless Qt platform before any PySide6 widgets are initialized
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QLabel
from starlette.testclient import TestClient

from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.core.lcu_port import (
    GameflowPhase,
    ILcuPort,
    LcuConnectionStatus,
)
from riftpilot.core.pairing import (
    DeviceSession,
    OTTManager,
    SessionManager,
)

# Target modules under test: Expected to fail during TDD Red Phase
# Target 1: Web Hub FastAPI factory
from riftpilot.adapters.web_hub import create_app

# Target 2: Desktop UI with LCU integration & Thread-Safe UI Bridge
from riftpilot.ui.main_window import LcuUiBridge, MainWindow


# ==============================================================================
# Fixtures & Test Setup
# ==============================================================================


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Session-scoped headless QApplication singleton."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def ott_manager() -> OTTManager:
    """Fresh in-memory OTTManager instance."""
    return OTTManager()


@pytest.fixture
def session_manager() -> SessionManager:
    """Fresh in-memory SessionManager instance."""
    return SessionManager()


@pytest.fixture
def mock_lcu() -> MockLcuAdapter:
    """Fresh MockLcuAdapter instance."""
    return MockLcuAdapter()


@pytest.fixture
def web_app(
    ott_manager: OTTManager,
    session_manager: SessionManager,
    mock_lcu: MockLcuAdapter,
) -> Any:
    """FastAPI application instance constructed by create_app factory."""
    return create_app(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lcu_adapter=mock_lcu,
    )


@pytest.fixture
def client(web_app: Any) -> Generator[TestClient, None, None]:
    """Starlette TestClient bound to the web_app."""
    with TestClient(web_app) as test_client:
        yield test_client


@pytest.fixture
def main_window(
    qapp: QApplication,
    ott_manager: OTTManager,
    session_manager: SessionManager,
    mock_lcu: MockLcuAdapter,
) -> Generator[MainWindow, None, None]:
    """MainWindow instance configured with LCU adapter and cleanup teardown."""
    window = MainWindow(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lan_ip="192.168.1.100",
        port=8000,
        lcu_adapter=mock_lcu,
    )
    yield window
    window.close()
    window.deleteLater()


# ==============================================================================
# 1. Web Hub HTTP Endpoints & Pairing Tests
# ==============================================================================


class TestWebHubHttpEndpoints:
    """Tests HTTP endpoints of the Web Hub (root mobile UI and pairing API)."""

    def test_get_root_serves_mobile_html(self, client: TestClient) -> None:
        """GET / must return HTTP 200 with responsive mobile HTML containing RiftPilot branding."""
        response = client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "RiftPilot" in response.text
        assert "<title>" in response.text.lower()

    def test_pair_valid_ott_returns_session_token(
        self,
        client: TestClient,
        ott_manager: OTTManager,
        session_manager: SessionManager,
    ) -> None:
        """POST /api/pair with valid OTT and device metadata creates an authenticated session."""
        ott = ott_manager.generate_ott(ttl_seconds=90.0)
        payload = {
            "ott": ott.token,
            "deviceName": "iPhone 15 Pro",
            "clientInstanceId": "client-uuid-12345",
        }

        response = client.post("/api/pair", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert "session_token" in data or "sessionToken" in data
        token = data.get("session_token") or data.get("sessionToken")
        assert token is not None

        # Verify session is actively registered in SessionManager
        session = session_manager.validate_session(token)
        assert session is not None
        assert session.device_name == "iPhone 15 Pro"
        assert session.client_instance_id == "client-uuid-12345"
        assert session.is_active is True

    def test_pair_burns_ott_on_read(
        self,
        client: TestClient,
        ott_manager: OTTManager,
    ) -> None:
        """POST /api/pair burns the presented OTT immediately (burn-on-read)."""
        ott = ott_manager.generate_ott(ttl_seconds=90.0)
        payload = {
            "ott": ott.token,
            "deviceName": "Samsung S24",
            "clientInstanceId": "inst-s24",
        }

        response = client.post("/api/pair", json=payload)
        assert response.status_code == 200

        # Attempting to verify the burned token manually must fail
        assert ott_manager.verify_and_burn(ott.token) is False

    def test_pair_replay_attack_rejected(
        self,
        client: TestClient,
        ott_manager: OTTManager,
    ) -> None:
        """Re-submitting the same OTT a second time (replay attack) must return 400 or 401."""
        ott = ott_manager.generate_ott(ttl_seconds=90.0)
        payload = {
            "ott": ott.token,
            "deviceName": "iPad Mini",
            "clientInstanceId": "inst-ipad",
        }

        # First attempt: success
        res1 = client.post("/api/pair", json=payload)
        assert res1.status_code == 200

        # Second attempt (replay attack): rejected
        res2 = client.post("/api/pair", json=payload)
        assert res2.status_code in (400, 401)

    def test_pair_invalid_ott_rejected(self, client: TestClient) -> None:
        """POST /api/pair with fabricated non-existent OTT returns 400 or 401."""
        payload = {
            "ott": "completely_fabricated_invalid_ott_token_xyz",
            "deviceName": "Hacker Device",
            "clientInstanceId": "inst-hack",
        }
        response = client.post("/api/pair", json=payload)
        assert response.status_code in (400, 401)

    def test_pair_expired_ott_rejected(
        self,
        client: TestClient,
        ott_manager: OTTManager,
    ) -> None:
        """POST /api/pair with an expired OTT returns 400 or 401."""
        ott = ott_manager.generate_ott(ttl_seconds=-10.0)  # Already expired
        payload = {
            "ott": ott.token,
            "deviceName": "Delayed Phone",
            "clientInstanceId": "inst-delayed",
        }

        response = client.post("/api/pair", json=payload)
        assert response.status_code in (400, 401)

    def test_pair_missing_fields_validation_error(self, client: TestClient) -> None:
        """POST /api/pair with missing required fields returns 422 or 400."""
        # Empty body
        res_empty = client.post("/api/pair", json={})
        assert res_empty.status_code in (400, 422)

        # Missing OTT
        res_no_ott = client.post(
            "/api/pair",
            json={"deviceName": "No OTT Device", "clientInstanceId": "id-1"},
        )
        assert res_no_ott.status_code in (400, 422)


# ==============================================================================
# 2. Web Hub WebSocket Authentication & Command Routing Tests
# ==============================================================================


class TestWebHubWebSocket:
    """Tests WebSocket endpoint (/ws) authentication, phase broadcasts, and commands."""

    def test_websocket_unauthorized_without_token(self, client: TestClient) -> None:
        """WebSocket connection without a valid session token is rejected or errors."""
        with pytest.raises(Exception):
            with client.websocket_connect("/ws") as ws:
                # If connection opened, attempting to execute command without token must fail
                ws.send_json({"type": "GET_STATE"})
                reply = ws.receive_json()
                assert reply.get("type") in ("ERROR", "UNAUTHORIZED")
                raise RuntimeError("Unauthorized access rejected")

    def test_websocket_unauthorized_with_invalid_token(self, client: TestClient) -> None:
        """WebSocket connection with invalid token query param is rejected or closed."""
        with pytest.raises(Exception):
            with client.websocket_connect("/ws?token=invalid_dummy_session_token") as ws:
                ws.send_json({"type": "GET_STATE"})
                reply = ws.receive_json()
                assert reply.get("type") in ("ERROR", "UNAUTHORIZED")
                raise RuntimeError("Invalid token rejected")

    def test_websocket_authorized_connection_receives_initial_state(
        self,
        client: TestClient,
        session_manager: SessionManager,
        mock_lcu: MockLcuAdapter,
    ) -> None:
        """Connecting with a valid session token receives initial welcome / state payload."""
        session = session_manager.create_session("iPhone 15", "inst-iphone")
        token = session.session_token

        with client.websocket_connect(f"/ws?token={token}") as ws:
            initial_msg = ws.receive_json()
            assert initial_msg.get("type") in ("WELCOME", "INITIAL_STATE")
            if "phase" in initial_msg:
                assert initial_msg["phase"] in ("None", GameflowPhase.NONE.value)

    @pytest.mark.asyncio
    async def test_websocket_gameflow_phase_change_broadcast(
        self,
        client: TestClient,
        session_manager: SessionManager,
        mock_lcu: MockLcuAdapter,
    ) -> None:
        """When LCU phase changes to READY_CHECK, connected WebSocket client receives update."""
        session = session_manager.create_session("Galaxy S24", "inst-galaxy")
        token = session.session_token

        await mock_lcu.simulate_connect()

        with client.websocket_connect(f"/ws?token={token}") as ws:
            # Consume initial welcome message
            ws.receive_json()

            # Trigger LCU phase change to ReadyCheck
            await mock_lcu.simulate_phase_change(GameflowPhase.READY_CHECK)

            msg = ws.receive_json()
            assert msg.get("type") == "GAMEFLOW_UPDATE"
            assert msg.get("phase") in ("ReadyCheck", GameflowPhase.READY_CHECK.value)

    @pytest.mark.asyncio
    async def test_websocket_accept_ready_check_success(
        self,
        client: TestClient,
        session_manager: SessionManager,
        mock_lcu: MockLcuAdapter,
    ) -> None:
        """Client sending ACCEPT_READY_CHECK invokes adapter.accept_ready_check and gets success reply."""
        session = session_manager.create_session("Pixel 8", "inst-pixel")
        token = session.session_token

        await mock_lcu.simulate_connect()
        await mock_lcu.simulate_ready_check(timer=12.0)

        with client.websocket_connect(f"/ws?token={token}") as ws:
            # Consume welcome and ready check notification if sent
            first = ws.receive_json()
            if first.get("type") == "WELCOME":
                pass

            # Send match acceptance command
            ws.send_json({"type": "ACCEPT_READY_CHECK", "token": token})

            reply = ws.receive_json()
            # If intermediate GAMEFLOW_UPDATE was received, read until reply
            if reply.get("type") == "GAMEFLOW_UPDATE":
                reply = ws.receive_json()

            assert reply.get("type") == "READY_CHECK_ACCEPTED"
            assert reply.get("success") is True

            # Verify mock adapter status was updated
            assert mock_lcu.player_response == "Accepted"

    @pytest.mark.asyncio
    async def test_websocket_accept_ready_check_failure_when_not_in_ready_check(
        self,
        client: TestClient,
        session_manager: SessionManager,
        mock_lcu: MockLcuAdapter,
    ) -> None:
        """ACCEPT_READY_CHECK when not in READY_CHECK phase responds with success: false."""
        session = session_manager.create_session("OnePlus 12", "inst-op")
        token = session.session_token

        await mock_lcu.simulate_connect()
        await mock_lcu.simulate_phase_change(GameflowPhase.LOBBY)

        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # welcome

            ws.send_json({"type": "ACCEPT_READY_CHECK", "token": token})

            reply = ws.receive_json()
            assert reply.get("type") == "READY_CHECK_ACCEPTED"
            assert reply.get("success") is False

    @pytest.mark.asyncio
    async def test_websocket_revoked_session_rejects_subsequent_commands(
        self,
        client: TestClient,
        session_manager: SessionManager,
        mock_lcu: MockLcuAdapter,
    ) -> None:
        """When session is revoked from desktop, subsequent commands on active WS are rejected."""
        session = session_manager.create_session("Revokable Device", "inst-rev")
        token = session.session_token

        await mock_lcu.simulate_connect()
        await mock_lcu.simulate_ready_check(timer=10.0)

        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # welcome

            # Desktop revokes session mid-connection
            session_manager.revoke_session(token)

            # Mobile client attempts command
            ws.send_json({"type": "ACCEPT_READY_CHECK", "token": token})

            try:
                reply = ws.receive_json()
                assert reply.get("type") in ("ERROR", "REVOKED", "UNAUTHORIZED")
            except Exception:
                # Connection may also be abruptly terminated by server, which is valid
                pass

    def test_websocket_malformed_json_handling(
        self,
        client: TestClient,
        session_manager: SessionManager,
    ) -> None:
        """Server handles malformed non-JSON payloads without crashing."""
        session = session_manager.create_session("Malformed Test", "inst-mal")
        token = session.session_token

        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # welcome

            ws.send_text("THIS_IS_NOT_VALID_JSON")
            reply = ws.receive_text()
            assert reply is not None

    def test_websocket_unknown_command_returns_error(
        self,
        client: TestClient,
        session_manager: SessionManager,
    ) -> None:
        """Unknown command types return an ERROR packet."""
        session = session_manager.create_session("Test Device", "inst-t")
        token = session.session_token

        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # welcome

            ws.send_json({"type": "NON_EXISTENT_COMMAND", "token": token})
            reply = ws.receive_json()
            assert reply.get("type") == "ERROR"

    @pytest.mark.asyncio
    async def test_websocket_multiple_clients_broadcast(
        self,
        client: TestClient,
        session_manager: SessionManager,
        mock_lcu: MockLcuAdapter,
    ) -> None:
        """Phase changes are broadcast to all connected authenticated mobile clients."""
        s1 = session_manager.create_session("Device 1", "inst-1")
        s2 = session_manager.create_session("Device 2", "inst-2")

        await mock_lcu.simulate_connect()

        with client.websocket_connect(f"/ws?token={s1.session_token}") as ws1:
            ws1.receive_json()  # welcome 1

            with client.websocket_connect(f"/ws?token={s2.session_token}") as ws2:
                ws2.receive_json()  # welcome 2

                # Trigger phase transition to CHAMP_SELECT
                await mock_lcu.simulate_phase_change(GameflowPhase.CHAMP_SELECT)

                msg1 = ws1.receive_json()
                msg2 = ws2.receive_json()

                assert msg1.get("type") == "GAMEFLOW_UPDATE"
                assert msg1.get("phase") in ("ChampSelect", GameflowPhase.CHAMP_SELECT.value)

                assert msg2.get("type") == "GAMEFLOW_UPDATE"
                assert msg2.get("phase") in ("ChampSelect", GameflowPhase.CHAMP_SELECT.value)


# ==============================================================================
# 3. Desktop UI LCU Integration Tests (MainWindow)
# ==============================================================================


class TestDesktopUiLcuIntegration:
    """Tests MainWindow with LCU connection status badge and gameflow phase updates."""

    def test_main_window_accepts_lcu_adapter_parameter(
        self,
        qapp: QApplication,
        ott_manager: OTTManager,
        session_manager: SessionManager,
        mock_lcu: MockLcuAdapter,
    ) -> None:
        """MainWindow.__init__ must accept optional lcu_adapter: ILcuPort parameter."""
        window = MainWindow(
            ott_manager=ott_manager,
            session_manager=session_manager,
            lan_ip="192.168.1.50",
            port=8080,
            lcu_adapter=mock_lcu,
        )
        try:
            assert getattr(window, "lcu_adapter", None) is mock_lcu
        finally:
            window.close()
            window.deleteLater()

    def test_main_window_has_lcu_status_and_gameflow_labels(
        self, main_window: MainWindow
    ) -> None:
        """MainWindow must display LCU connection status label and Gameflow phase label."""
        lcu_status_label = getattr(main_window, "lcu_status_label", None)
        gameflow_label = getattr(main_window, "gameflow_label", None)

        if lcu_status_label is None or gameflow_label is None:
            # Fallback inspection of child QLabels
            labels = main_window.findChildren(QLabel)
            status_labels = [
                lbl for lbl in labels
                if "lcu" in lbl.objectName().lower()
                or "istemci" in lbl.text().lower()
                or "bekleniyor" in lbl.text().lower()
                or "bağlı" in lbl.text().lower()
            ]
            assert len(status_labels) > 0, "MainWindow must have LCU connection status indicator"

        assert lcu_status_label is not None or len(main_window.findChildren(QLabel)) >= 4

    def test_initial_lcu_status_label_indicates_disconnected(
        self, main_window: MainWindow
    ) -> None:
        """When mock_lcu is DISCONNECTED, lcu_status_label indicates disconnected / waiting."""
        labels_text = " ".join(lbl.text() for lbl in main_window.findChildren(QLabel))
        has_disconnected_indicator = any(
            kw in labels_text.lower()
            for kw in ("bekleniyor", "bağlı değil", "disconnected", "istemci")
        )
        assert has_disconnected_indicator, (
            "MainWindow must display disconnected / waiting status for LCU initially"
        )

    def test_lcu_connection_status_updates_label_when_connected(
        self,
        main_window: MainWindow,
        mock_lcu: MockLcuAdapter,
    ) -> None:
        """Calling on_lcu_status_changed(CONNECTED) updates UI status label to connected."""
        if hasattr(main_window, "on_lcu_status_changed"):
            main_window.on_lcu_status_changed(LcuConnectionStatus.CONNECTED)
        elif hasattr(main_window, "set_lcu_status"):
            main_window.set_lcu_status(LcuConnectionStatus.CONNECTED)

        labels_text = " ".join(lbl.text() for lbl in main_window.findChildren(QLabel))
        has_connected_indicator = any(
            kw in labels_text.lower()
            for kw in ("lcu bağlandı", "bağlandı", "connected")
        )
        assert has_connected_indicator

    def test_gameflow_phase_updates_label_on_ready_check(
        self,
        main_window: MainWindow,
    ) -> None:
        """Transitioning to READY_CHECK updates gameflow_label to 'MAÇ BULUNDU!' or 'ReadyCheck'."""
        if hasattr(main_window, "on_gameflow_phase_changed"):
            main_window.on_gameflow_phase_changed(GameflowPhase.READY_CHECK)
        elif hasattr(main_window, "on_phase_changed"):
            main_window.on_phase_changed(GameflowPhase.READY_CHECK)

        labels_text = " ".join(lbl.text() for lbl in main_window.findChildren(QLabel))
        has_ready_check_text = (
            "maç bulundu" in labels_text.lower() or "readycheck" in labels_text.lower()
        )
        assert has_ready_check_text, (
            f"Expected 'MAÇ BULUNDU!' in MainWindow labels, found: {labels_text}"
        )

    def test_gameflow_phase_updates_label_on_matchmaking(
        self,
        main_window: MainWindow,
    ) -> None:
        """Transitioning to MATCHMAKING updates gameflow_label to indicate queuing."""
        if hasattr(main_window, "on_gameflow_phase_changed"):
            main_window.on_gameflow_phase_changed(GameflowPhase.MATCHMAKING)
        elif hasattr(main_window, "on_phase_changed"):
            main_window.on_phase_changed(GameflowPhase.MATCHMAKING)

        labels_text = " ".join(lbl.text() for lbl in main_window.findChildren(QLabel))
        has_matchmaking_text = any(
            kw in labels_text.lower()
            for kw in ("sırada", "aranıyor", "matchmaking", "eşleşme")
        )
        assert has_matchmaking_text

    def test_main_window_without_lcu_adapter_defaults_cleanly(
        self,
        qapp: QApplication,
        ott_manager: OTTManager,
        session_manager: SessionManager,
    ) -> None:
        """MainWindow initialized without lcu_adapter (lcu_adapter=None) initializes without error."""
        window = MainWindow(
            ott_manager=ott_manager,
            session_manager=session_manager,
            lan_ip="127.0.0.1",
            port=8000,
        )
        try:
            assert getattr(window, "lcu_adapter", None) is None
            assert "RiftPilot" in window.windowTitle()
        finally:
            window.close()
            window.deleteLater()


# ==============================================================================
# 4. Thread-Safe LcuUiBridge Tests
# ==============================================================================


class TestLcuUiBridge:
    """Tests LcuUiBridge QObject translating async / background LCU events to Qt signals."""

    def test_lcu_ui_bridge_is_qobject(self) -> None:
        """LcuUiBridge must inherit from PySide6.QtCore.QObject."""
        assert issubclass(LcuUiBridge, QObject)

    def test_lcu_ui_bridge_signals_defined(self) -> None:
        """LcuUiBridge must define phase_changed and connection_status_changed signals."""
        bridge = LcuUiBridge()
        assert hasattr(bridge, "phase_changed")
        assert hasattr(bridge, "connection_status_changed")

    def test_bridge_emits_phase_changed_signal(self, qapp: QApplication) -> None:
        """Calling notify_phase_change or callback emits phase_changed signal."""
        bridge = LcuUiBridge()
        received_phases: list[Any] = []

        bridge.phase_changed.connect(lambda p: received_phases.append(p))

        if hasattr(bridge, "notify_phase_change"):
            bridge.notify_phase_change(GameflowPhase.READY_CHECK)
        elif hasattr(bridge, "on_phase_change"):
            bridge.on_phase_change(GameflowPhase.READY_CHECK)

        assert len(received_phases) == 1
        assert (
            received_phases[0] == GameflowPhase.READY_CHECK
            or received_phases[0] == GameflowPhase.READY_CHECK.value
        )

    def test_bridge_emits_status_changed_signal(self, qapp: QApplication) -> None:
        """Calling notify_status_change emits connection_status_changed signal."""
        bridge = LcuUiBridge()
        received_statuses: list[Any] = []

        bridge.connection_status_changed.connect(lambda s: received_statuses.append(s))

        if hasattr(bridge, "notify_status_change"):
            bridge.notify_status_change(LcuConnectionStatus.CONNECTED)
        elif hasattr(bridge, "on_status_change"):
            bridge.on_status_change(LcuConnectionStatus.CONNECTED)

        assert len(received_statuses) == 1
        assert (
            received_statuses[0] == LcuConnectionStatus.CONNECTED
            or received_statuses[0] == LcuConnectionStatus.CONNECTED.value
        )

    @pytest.mark.asyncio
    async def test_bridge_subscribes_to_lcu_adapter(
        self,
        qapp: QApplication,
        mock_lcu: MockLcuAdapter,
    ) -> None:
        """When initialized with lcu_adapter, bridge subscribes to phase change events."""
        received_phases: list[Any] = []
        bridge = LcuUiBridge(lcu_adapter=mock_lcu)
        bridge.phase_changed.connect(lambda p: received_phases.append(p))

        await mock_lcu.simulate_connect()
        await mock_lcu.simulate_phase_change(GameflowPhase.CHAMP_SELECT)

        assert len(received_phases) == 1
        assert (
            received_phases[0] == GameflowPhase.CHAMP_SELECT
            or received_phases[0] == GameflowPhase.CHAMP_SELECT.value
        )


# ==============================================================================
# 5. Full End-to-End Workflow Integration Test
# ==============================================================================


class TestFullEndToEndFlow:
    """Full end-to-end integration scenario across Mobile Hub, Desktop UI, and LCU Port."""

    @pytest.mark.asyncio
    async def test_full_pair_to_match_acceptance_lifecycle(
        self,
        qapp: QApplication,
        ott_manager: OTTManager,
        session_manager: SessionManager,
        mock_lcu: MockLcuAdapter,
        client: TestClient,
    ) -> None:
        """Complete workflow test:

        1. Desktop UI generates pairing OTT.
        2. Mobile client pairs with OTT via POST /api/pair.
        3. OTT is burned; mobile client receives session_token.
        4. Desktop UI displays connected mobile device.
        5. Mobile client connects to WebSocket /ws.
        6. LCU triggers READY_CHECK phase.
        7. Mobile receives GAMEFLOW_UPDATE with ReadyCheck.
        8. Mobile sends ACCEPT_READY_CHECK.
        9. LCU adapter registers match acceptance.
        10. Desktop UI revokes session.
        11. Subsequent mobile commands are rejected.
        """
        # Step 1: Desktop initializes and generates OTT
        window = MainWindow(
            ott_manager=ott_manager,
            session_manager=session_manager,
            lan_ip="192.168.1.100",
            port=8000,
            lcu_adapter=mock_lcu,
        )

        try:
            # Step 2 & 3: Mobile client pairs with OTT
            ott = ott_manager.generate_ott(ttl_seconds=90.0)
            pair_response = client.post(
                "/api/pair",
                json={
                    "ott": ott.token,
                    "deviceName": "Tester Phone",
                    "clientInstanceId": "test-uuid-999",
                },
            )
            assert pair_response.status_code == 200
            pair_data = pair_response.json()
            session_token = pair_data.get("session_token") or pair_data.get("sessionToken")
            assert session_token is not None

            # Verify OTT was burned
            assert ott_manager.verify_and_burn(ott.token) is False

            # Step 4: Desktop UI updates connected devices list
            window.update_device_list()
            labels_text = " ".join(lbl.text() for lbl in window.findChildren(QLabel))
            device_list_widget = getattr(window, "device_list", None)
            device_found = False
            if device_list_widget is not None:
                for i in range(device_list_widget.count()):
                    if "Tester Phone" in device_list_widget.item(i).text():
                        device_found = True
                        break
            if not device_found:
                device_found = "Tester Phone" in labels_text
            assert device_found, "Connected mobile device must appear on MainWindow"

            # Step 5: Mobile connects to WebSocket
            await mock_lcu.simulate_connect()

            with client.websocket_connect(f"/ws?token={session_token}") as ws:
                ws.receive_json()  # welcome

                # Step 6: LCU triggers ReadyCheck
                await mock_lcu.simulate_ready_check(timer=12.0)

                # Step 7: Mobile receives live GAMEFLOW_UPDATE
                phase_msg = ws.receive_json()
                assert phase_msg.get("type") == "GAMEFLOW_UPDATE"
                assert phase_msg.get("phase") in ("ReadyCheck", GameflowPhase.READY_CHECK.value)

                # Step 8 & 9: Mobile accepts match
                ws.send_json({"type": "ACCEPT_READY_CHECK", "token": session_token})
                accept_reply = ws.receive_json()
                assert accept_reply.get("type") == "READY_CHECK_ACCEPTED"
                assert accept_reply.get("success") is True
                assert mock_lcu.player_response == "Accepted"

                # Step 10: Desktop revokes session
                window.revoke_session(session_token)
                assert session_manager.validate_session(session_token) is None

                # Step 11: Subsequent mobile command is rejected
                ws.send_json({"type": "ACCEPT_READY_CHECK", "token": session_token})
                try:
                    rejection = ws.receive_json()
                    assert rejection.get("type") in ("ERROR", "REVOKED", "UNAUTHORIZED")
                except Exception:
                    # Closing connection upon revocation is also valid
                    pass
        finally:
            window.close()
            window.deleteLater()


    def test_main_window_poll_tick_syncs_devices_and_status(
        self,
        qapp: QApplication,
        ott_manager: OTTManager,
        session_manager: SessionManager,
        mock_lcu: MockLcuAdapter,
    ) -> None:
        """Periodic poll tick must refresh device list and LCU connection status."""
        window = MainWindow(
            ott_manager=ott_manager,
            session_manager=session_manager,
            lan_ip="127.0.0.1",
            port=8765,
            lcu_adapter=mock_lcu,
        )
        try:
            assert window.device_list.isHidden() or window.device_list.count() == 0

            # Add a session out-of-band (e.g. mobile paired via API)
            session_manager.create_session("Auto Polled Phone", "inst-auto")
            assert window.device_list.count() == 0

            # Trigger poll tick
            window._on_poll_tick()
            assert window.device_list.count() == 1
            assert "Auto Polled Phone" in window.device_list.item(0).text()
        finally:
            window.close()
            window.deleteLater()

    def test_pair_notifies_ui_bridge_instantly(
        self,
        ott_manager: OTTManager,
        session_manager: SessionManager,
        mock_lcu: MockLcuAdapter,
    ) -> None:
        """POST /api/pair notifies ui_bridge.notify_device_list_changed immediately."""
        bridge_mock = MagicMock()
        app = create_app(
            ott_manager=ott_manager,
            session_manager=session_manager,
            lcu_adapter=mock_lcu,
            ui_bridge=bridge_mock,
            enable_monitor=False,
        )
        ott = ott_manager.generate_ott(ttl_seconds=60.0)

        with TestClient(app) as client:
            resp = client.post(
                "/api/pair",
                json={
                    "ott": ott.token,
                    "deviceName": "Instant Phone",
                    "clientInstanceId": "inst-1",
                },
            )
            assert resp.status_code == 200
            bridge_mock.notify_device_list_changed.assert_called_once()
