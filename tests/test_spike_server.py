"""RiftPilot — Tests for LAN Spike Server (Milestone 1, Task 1.1).

This module verifies the behavior of the FastAPI spike server in `scripts/network_spike.py`:
- Root endpoint serving responsive mobile HTML
- API health check endpoint
- LAN IPv4 detection logic and fallback mechanisms
- Two-way WebSocket communication (WELCOME, PING/PONG, ECHO)
"""

from __future__ import annotations

import ipaddress
import socket
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from scripts.network_spike import app, detect_lan_ip


@pytest.fixture
def client() -> TestClient:
    """Fixture providing a Starlette TestClient instance."""
    return TestClient(app)


class TestNetworkSpikeEndpoints:
    """Tests for HTTP endpoints of the spike server."""

    def test_get_root_mobile_web(self, client: TestClient) -> None:
        """GET / should return 200 OK with HTML containing RiftPilot branding."""
        response = client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "RiftPilot" in response.text
        assert "<title>RiftPilot — LAN Spike Testi</title>" in response.text
        assert "statusBadge" in response.text

    def test_get_health_check(self, client: TestClient) -> None:
        """GET /api/health should return 200 OK with status: ok and a valid timestamp."""
        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.headers.get("content-type") == "application/json"

        data: dict[str, Any] = response.json()
        assert data.get("status") == "ok"
        assert "timestamp" in data

        # Verify timestamp is in valid ISO format
        parsed_time = datetime.fromisoformat(data["timestamp"])
        assert parsed_time is not None

    def test_not_found_endpoint(self, client: TestClient) -> None:
        """Unknown endpoints should return 404 Not Found."""
        response = client.get("/api/unknown")
        assert response.status_code == 404


class TestLanIpDetection:
    """Tests for local IPv4 detection and fallback behaviors."""

    def test_detect_lan_ip_returns_valid_ipv4(self) -> None:
        """detect_lan_ip() should always return a syntactically valid IPv4 address."""
        ip_str = detect_lan_ip()

        assert isinstance(ip_str, str)
        assert len(ip_str) > 0

        # Validate with python's ipaddress module (raises ValueError if invalid)
        ip_obj = ipaddress.IPv4Address(ip_str)
        assert ip_obj.version == 4

    def test_detect_lan_ip_socket_primary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """detect_lan_ip() uses outbound UDP socket if available."""
        mock_socket_instance = MagicMock()
        mock_socket_instance.getsockname.return_value = ("192.168.1.42", 54321)

        class MockSocketContext:
            def __enter__(self) -> MagicMock:
                return mock_socket_instance

            def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
                pass

        monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: MockSocketContext())

        detected = detect_lan_ip()
        assert detected == "192.168.1.42"

    def test_detect_lan_ip_fallback_to_psutil(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When socket connection fails, detect_lan_ip() falls back to psutil interfaces."""
        # Force socket.socket to fail
        def broken_socket(*args: Any, **kwargs: Any) -> Any:
            raise OSError("Network unreachable")

        monkeypatch.setattr(socket, "socket", broken_socket)

        # Mock psutil.net_if_addrs
        mock_addr_lo = MagicMock()
        mock_addr_lo.family = socket.AF_INET
        mock_addr_lo.address = "127.0.0.1"

        mock_addr_lan = MagicMock()
        mock_addr_lan.family = socket.AF_INET
        mock_addr_lan.address = "192.168.1.120"

        mock_interfaces = {
            "Loopback": [mock_addr_lo],
            "Wi-Fi": [mock_addr_lan],
        }
        monkeypatch.setattr("psutil.net_if_addrs", lambda: mock_interfaces)

        detected = detect_lan_ip()
        assert detected == "192.168.1.120"

    def test_detect_lan_ip_fallback_to_localhost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When all detection mechanisms fail, detect_lan_ip() returns 127.0.0.1."""
        def broken_socket(*args: Any, **kwargs: Any) -> Any:
            raise OSError("Network unreachable")

        monkeypatch.setattr(socket, "socket", broken_socket)
        monkeypatch.setattr("psutil.net_if_addrs", lambda: {})

        detected = detect_lan_ip()
        assert detected == "127.0.0.1"


class TestWebSocketSpikeChannel:
    """Tests for bidirectional WebSocket communication."""

    def test_websocket_welcome_and_ping_pong(self, client: TestClient) -> None:
        """WebSocket connection should receive WELCOME packet and respond to PING with PONG."""
        with client.websocket_connect("/ws") as ws:
            # 1. Verify initial WELCOME message
            welcome = ws.receive_json()
            assert welcome.get("type") == "WELCOME"
            assert "RiftPilot LAN Hub" in welcome.get("message", "")
            assert "serverTime" in welcome

            # 2. Send PING request
            client_time = 1730472000000
            ws.send_json({"type": "PING", "clientTime": client_time})

            # 3. Receive PONG response
            pong = ws.receive_json()
            assert pong.get("type") == "PONG"
            assert pong.get("clientReported") == client_time
            assert "time" in pong

    def test_websocket_echo_custom_json(self, client: TestClient) -> None:
        """WebSocket should echo custom JSON payloads."""
        with client.websocket_connect("/ws") as ws:
            # Consume welcome
            ws.receive_json()

            # Send arbitrary JSON payload
            payload = {"type": "COMMAND", "action": "accept_match"}
            ws.send_json(payload)

            response = ws.receive_json()
            assert response.get("type") == "ECHO"
            assert response.get("received") == payload

    def test_websocket_echo_plain_text(self, client: TestClient) -> None:
        """WebSocket should handle non-JSON plain text gracefully."""
        with client.websocket_connect("/ws") as ws:
            # Consume welcome
            ws.receive_json()

            ws.send_text("RAW_TEXT_MESSAGE")
            response = ws.receive_text()
            assert response == "ECHO: RAW_TEXT_MESSAGE"

    def test_websocket_echo_non_dict_json(self, client: TestClient) -> None:
        """WebSocket should handle non-dict JSON (e.g. lists or numbers) without crashing."""
        with client.websocket_connect("/ws") as ws:
            # Consume welcome
            ws.receive_json()

            # Send a JSON list (not a dict)
            ws.send_text("[1, 2, 3]")
            response = ws.receive_json()
            assert response.get("type") == "ECHO"
            assert response.get("received") == [1, 2, 3]


class TestTerminalOutput:
    """Tests for terminal display functions."""

    def test_print_terminal_qr(self, capsys: pytest.CaptureFixture[str]) -> None:
        """print_terminal_qr should output header and URL."""
        from scripts.network_spike import print_terminal_qr

        test_url = "http://192.168.1.50:8765/"
        print_terminal_qr(test_url)
        captured = capsys.readouterr()
        assert test_url in captured.out
        assert "RIFTPILOT LAN SPIKE" in captured.out
