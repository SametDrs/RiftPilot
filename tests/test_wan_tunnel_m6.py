"""Tests for Milestone 6: WAN / Remote-Ready Transport Architecture (ADR-0003 Phase 2).

Verifies:
- Transport-agnostic message envelopes and protocol models in `riftpilot.core.transport`
- MockTunnelAdapter and CloudflareTunnelAdapter implementations
- WebHub WAN status and toggle REST endpoints
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.tunnel_adapter import CloudflareTunnelAdapter, MockTunnelAdapter
from riftpilot.adapters.web_hub import create_app
from riftpilot.core.pairing import OTTManager, SessionManager
from tests.conftest import authorize_client
from riftpilot.core.transport import (
    MessageEnvelope,
    TransportProtocol,
    TunnelInfo,
    TunnelStatus,
)


class TestTransportDomainModels:
    """Unit tests for transport-agnostic message envelopes and tunnel models."""

    def test_message_envelope_creation_and_defaults(self) -> None:
        envelope = MessageEnvelope(
            type="ACCEPT_READY_CHECK",
            payload={"queueId": 420},
            token="sess_token_123",
            client_id="mobile_ios",
            protocol=TransportProtocol.WAN_RELAY,
        )
        assert envelope.type == "ACCEPT_READY_CHECK"
        assert envelope.payload["queueId"] == 420
        assert envelope.token == "sess_token_123"
        assert envelope.protocol == TransportProtocol.WAN_RELAY
        assert envelope.timestamp > 0

    def test_tunnel_info_defaults(self) -> None:
        info = TunnelInfo()
        assert info.provider == "Cloudflare"
        assert info.status == TunnelStatus.DISCONNECTED
        assert info.is_active is False
        assert info.public_url == ""


class TestMockTunnelAdapter:
    """Tests for MockTunnelAdapter lifecycle and failure simulation."""

    @pytest.mark.asyncio
    async def test_initial_tunnel_status(self) -> None:
        tunnel = MockTunnelAdapter()
        info = await tunnel.get_tunnel_info()
        assert info.status == TunnelStatus.DISCONNECTED
        assert info.is_active is False
        assert info.public_url == ""

    @pytest.mark.asyncio
    async def test_start_and_stop_tunnel_lifecycle(self) -> None:
        tunnel = MockTunnelAdapter(default_url="https://remote.riftpilot.app")
        info = await tunnel.start_tunnel(local_port=8000)
        assert info.status == TunnelStatus.CONNECTED
        assert info.is_active is True
        assert "remote.riftpilot.app" in info.public_url
        assert info.latency_ms > 0

        # Stop tunnel
        ok_stop = await tunnel.stop_tunnel()
        assert ok_stop is True
        stopped_info = await tunnel.get_tunnel_info()
        assert stopped_info.status == TunnelStatus.STOPPED
        assert stopped_info.is_active is False
        assert stopped_info.public_url == ""

    @pytest.mark.asyncio
    async def test_start_tunnel_invalid_port_fails(self) -> None:
        tunnel = MockTunnelAdapter()
        info = await tunnel.start_tunnel(local_port=999999)
        assert info.status == TunnelStatus.FAILED
        assert info.is_active is False
        assert "Invalid port" in info.error_message

    @pytest.mark.asyncio
    async def test_simulate_tunnel_failure(self) -> None:
        tunnel = MockTunnelAdapter()
        await tunnel.start_tunnel(local_port=8000)
        tunnel.simulate_failure("Relay server connection reset")
        info = await tunnel.get_tunnel_info()
        assert info.status == TunnelStatus.FAILED
        assert info.is_active is False
        assert info.error_message == "Relay server connection reset"


class TestCloudflareTunnelAdapter:
    """Tests for CloudflareTunnelAdapter URL matching and missing binary resilience."""

    def test_url_regex_extraction(self) -> None:
        sample_log = (
            "2026-11-02T01:00:00Z INF +----------------------------------------------------------------+\n"
            "2026-11-02T01:00:00Z INF | Your quick Tunnel has been created! Visit it at:               |\n"
            "2026-11-02T01:00:00Z INF | https://test-sample-subdomain.trycloudflare.com              |\n"
            "2026-11-02T01:00:00Z INF +----------------------------------------------------------------+"
        )
        match = CloudflareTunnelAdapter.URL_REGEX.search(sample_log)
        assert match is not None
        assert match.group(0) == "https://test-sample-subdomain.trycloudflare.com"

    @pytest.mark.asyncio
    async def test_missing_binary_fails_gracefully_without_raising(self) -> None:
        adapter = CloudflareTunnelAdapter(binary_path="non_existent_binary_xyz_123")
        info = await adapter.start_tunnel(local_port=8000)
        assert info.status == TunnelStatus.FAILED
        assert info.is_active is False
        assert "not found" in info.error_message


class TestWebHubWanEndpoints:
    """Tests for WebHub WAN reverse tunnel management routes."""

    @pytest.fixture
    def app_bundle(self) -> tuple[TestClient, MockTunnelAdapter]:
        ott_mgr = OTTManager()
        session_mgr = SessionManager()
        lcu = MockLcuAdapter()
        tunnel = MockTunnelAdapter(default_url="https://live-wan.riftpilot.gg")
        app = create_app(
            ott_manager=ott_mgr,
            session_manager=session_mgr,
            lcu_adapter=lcu,
            tunnel_adapter=tunnel,
        )
        client = TestClient(app)
        authorize_client(client, ott_mgr)
        return client, tunnel

    def test_wan_status_endpoint(self, app_bundle: tuple[TestClient, MockTunnelAdapter]) -> None:
        client, tunnel = app_bundle
        resp = client.get("/api/wan/status")
        assert resp.status_code == 200
        assert resp.json()["available"] is True
        assert resp.json()["tunnel"]["status"] == "Disconnected"

    def test_wan_toggle_endpoint(self, app_bundle: tuple[TestClient, MockTunnelAdapter]) -> None:
        client, tunnel = app_bundle

        # 1. Enable WAN tunnel
        start_resp = client.post("/api/wan/toggle?enable=true&port=8000")
        assert start_resp.status_code == 200
        data = start_resp.json()
        assert data["tunnel"]["status"] == "Connected"
        assert "live-wan.riftpilot.gg" in data["tunnel"]["public_url"]

        # 2. Disable WAN tunnel
        stop_resp = client.post("/api/wan/toggle?enable=false")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["tunnel"]["status"] == "Stopped"
