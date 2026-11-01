"""RiftPilot — Tests for Real Riot LCU HTTPX Adapter (Milestone 2, Task 2.2).

Verifies the outbound LCU adapter connecting to the live League Client (LCU)
REST API via HTTPX over local HTTPS with self-signed SSL verification disabled.

Covers:
1. ILcuPort Contract & Inheritance (ADR-0003 Hexagonal Architecture).
2. Initialization & Process / Lockfile Discovery (LcuDiscoverer).
3. Self-Signed SSL & HTTPX Client Configuration (verify=False, Basic Auth).
4. Gameflow Phase Retrieval (/lol-gameflow/v1/gameflow-phase).
5. Ready Check Acceptance (/lol-matchmaking/v1/ready-check/accept).
6. Lifecycle, Context Manager, and Event Subscriptions.
7. Resilience & Network Fault Injection (Timeouts, 401/403, 500, Connect Errors).

Test-First / Shift-Left:
Target module: `riftpilot.adapters.riot_lcu.RiotLcuAdapter`
(Initially fails with ModuleNotFoundError during TDD Red Phase).
"""

from __future__ import annotations

import base64
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Target port and adapter contracts
from riftpilot.adapters.lcu_discovery import LcuCredentials, LcuDiscoverer
from riftpilot.core.lcu_port import (
    GameflowPhase,
    ILcuPort,
    LcuConnectionStatus,
)

# Target module under test (Expected to fail with ModuleNotFoundError in TDD Red Phase)
from riftpilot.adapters.riot_lcu import RiotLcuAdapter


# ============================================================================
# Fixtures & Test Doubles
# ============================================================================


@pytest.fixture
def sample_credentials() -> LcuCredentials:
    """Provides standard valid LCU credentials for unit testing."""
    return LcuCredentials(
        port=29999,
        auth_token="mock_lcu_secret_auth_token_42",
        pid=12345,
        protocol="https",
    )


@pytest.fixture
def mock_discoverer(sample_credentials: LcuCredentials) -> MagicMock:
    """Provides a mock discoverer returning predefined credentials."""
    mock = MagicMock(spec=LcuDiscoverer)
    mock.discover.return_value = sample_credentials
    return mock


def make_mock_transport(
    status_code: int = 200,
    json_data: Any = None,
    text_data: str | None = None,
    side_effect: Exception | None = None,
) -> httpx.MockTransport:
    """Factory creating an httpx.MockTransport for deterministic responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        if side_effect is not None:
            raise side_effect
        if json_data is not None:
            return httpx.Response(status_code, json=json_data, request=request)
        return httpx.Response(status_code, text=text_data or "", request=request)

    return httpx.MockTransport(handler)


# ============================================================================
# 1. ILcuPort Contract & Inheritance Tests
# ============================================================================


class TestRiotLcuAdapterContract:
    """Verifies that RiotLcuAdapter adheres strictly to the ILcuPort outbound interface."""

    def test_adapter_subclasses_ilcu_port(self) -> None:
        """RiotLcuAdapter must inherit from ILcuPort in accordance with Hexagonal Architecture."""
        assert issubclass(RiotLcuAdapter, ILcuPort)

    def test_adapter_instance_is_ilcu_port(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Instance of RiotLcuAdapter must satisfy isinstance(..., ILcuPort)."""
        adapter = RiotLcuAdapter(credentials=sample_credentials)
        assert isinstance(adapter, ILcuPort)

    def test_implements_all_abstract_port_methods(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """RiotLcuAdapter must implement all abstract methods required by ILcuPort."""
        adapter = RiotLcuAdapter(credentials=sample_credentials)
        required_methods = [
            "get_connection_status",
            "get_gameflow_phase",
            "accept_ready_check",
            "subscribe_phase_change",
            "unsubscribe_phase_change",
        ]
        for method_name in required_methods:
            assert hasattr(adapter, method_name), (
                f"RiotLcuAdapter missing required interface method '{method_name}'"
            )
            assert callable(getattr(adapter, method_name)), (
                f"RiotLcuAdapter attribute '{method_name}' is not callable"
            )


# ============================================================================
# 2. Initialization & Credentials Discovery Tests
# ============================================================================


class TestRiotLcuAdapterInitialization:
    """Tests adapter initialization, manual credentials passing, and automatic discovery."""

    @pytest.mark.asyncio
    async def test_init_with_explicit_credentials_is_connected(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """When credentials are explicitly provided, adapter connects immediately without discovery."""
        adapter = RiotLcuAdapter(credentials=sample_credentials)
        assert await adapter.get_connection_status() == LcuConnectionStatus.CONNECTED
        assert adapter.credentials == sample_credentials

    @pytest.mark.asyncio
    async def test_init_with_discoverer_success_transitions_to_connected(
        self,
        sample_credentials: LcuCredentials,
        mock_discoverer: MagicMock,
    ) -> None:
        """When credentials=None, adapter queries discoverer.discover() and connects if found."""
        adapter = RiotLcuAdapter(credentials=None, discoverer=mock_discoverer)

        mock_discoverer.discover.assert_called_once()
        assert await adapter.get_connection_status() == LcuConnectionStatus.CONNECTED
        assert adapter.credentials == sample_credentials

    @pytest.mark.asyncio
    async def test_init_with_discoverer_returning_none_is_disconnected(self) -> None:
        """When discoverer returns None (LoL client not running), adapter remains DISCONNECTED."""
        mock_disc = MagicMock()
        mock_disc.discover.return_value = None

        adapter = RiotLcuAdapter(credentials=None, discoverer=mock_disc)
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED
        assert adapter.credentials is None

    @pytest.mark.asyncio
    async def test_init_with_discoverer_raising_exception_is_handled_gracefully(self) -> None:
        """If discovery raises an unexpected error (e.g. psutil AccessDenied), status is DISCONNECTED."""
        mock_disc = MagicMock()
        mock_disc.discover.side_effect = RuntimeError("Process access denied")

        adapter = RiotLcuAdapter(credentials=None, discoverer=mock_disc)
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED
        assert adapter.credentials is None

    @pytest.mark.asyncio
    async def test_connect_reattempts_discovery(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Calling connect() reattempts discovery if the adapter was initially DISCONNECTED."""
        mock_disc = MagicMock()
        mock_disc.discover.side_effect = [None, sample_credentials]

        adapter = RiotLcuAdapter(credentials=None, discoverer=mock_disc)
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED

        # Second attempt succeeds
        connected = await adapter.connect()
        assert connected is True
        assert await adapter.get_connection_status() == LcuConnectionStatus.CONNECTED
        assert adapter.credentials == sample_credentials

    @pytest.mark.asyncio
    async def test_default_discoverer_is_lcu_discoverer_class(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """When discoverer argument is omitted, LcuDiscoverer class is used as default discoverer."""
        with patch.object(LcuDiscoverer, "discover", return_value=sample_credentials) as mock_disc:
            adapter = RiotLcuAdapter(credentials=None)
            mock_disc.assert_called_once()
            assert await adapter.get_connection_status() == LcuConnectionStatus.CONNECTED


# ============================================================================
# 3. SSL Configuration & HTTP Client Setup Tests
# ============================================================================


class TestRiotLcuAdapterClientConfiguration:
    """Verifies that the underlying HTTPX client uses self-signed SSL and correct auth headers."""

    def test_client_has_verify_ssl_false(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Riot Client uses a self-signed SSL cert; internal HTTPX client must configure verify=False."""
        adapter = RiotLcuAdapter(credentials=sample_credentials)
        client = adapter._client if hasattr(adapter, "_client") else getattr(adapter, "client")

        # HTTPX client verify attribute must be False (or SSLContext without verification)
        assert client is not None
        assert client.is_closed is False
        assert client.verify is False or client.verify is False

    def test_client_base_url_matches_credentials(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Client base URL must match credentials base URL (https://127.0.0.1:<port>)."""
        adapter = RiotLcuAdapter(credentials=sample_credentials)
        client = adapter._client if hasattr(adapter, "_client") else getattr(adapter, "client")

        assert str(client.base_url).rstrip("/") == sample_credentials.base_url.rstrip("/")

    @pytest.mark.asyncio
    async def test_request_headers_include_basic_auth_and_accept_json(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """HTTP requests must supply 'Authorization: Basic <base64(riot:token)>' and 'Accept: application/json'."""
        captured_requests: list[httpx.Request] = []

        def capture_handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            return httpx.Response(200, json="Lobby", request=request)

        mock_transport = httpx.MockTransport(capture_handler)
        adapter = RiotLcuAdapter(
            credentials=sample_credentials,
            transport=mock_transport,
        )

        # Trigger request
        await adapter.get_gameflow_phase()

        assert len(captured_requests) >= 1
        req = captured_requests[0]

        # Verify Authorization header
        expected_raw = f"riot:{sample_credentials.auth_token}"
        expected_b64 = base64.b64encode(expected_raw.encode("ascii")).decode("ascii")
        expected_auth = f"Basic {expected_b64}"

        assert "authorization" in req.headers
        assert req.headers["authorization"] == expected_auth
        assert req.headers.get("accept") == "application/json"


# ============================================================================
# 4. get_gameflow_phase Tests (/lol-gameflow/v1/gameflow-phase)
# ============================================================================


class TestRiotLcuAdapterGetGameflowPhase:
    """Tests GET /lol-gameflow/v1/gameflow-phase querying, mapping, and error resilience."""

    @pytest.mark.asyncio
    async def test_get_gameflow_phase_success_matchmaking(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Valid response with 'Matchmaking' maps to GameflowPhase.MATCHMAKING."""
        transport = make_mock_transport(status_code=200, json_data="Matchmaking")
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        phase = await adapter.get_gameflow_phase()
        assert phase == GameflowPhase.MATCHMAKING

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("lcu_phase_str", "expected_phase"),
        [
            ("None", GameflowPhase.NONE),
            ("Lobby", GameflowPhase.LOBBY),
            ("Matchmaking", GameflowPhase.MATCHMAKING),
            ("ReadyCheck", GameflowPhase.READY_CHECK),
            ("ChampSelect", GameflowPhase.CHAMP_SELECT),
            ("InProgress", GameflowPhase.IN_PROGRESS),
            ("WaitingForStats", GameflowPhase.WAITING_FOR_STATS),
            ("PreEndOfGame", GameflowPhase.PRE_END_OF_GAME),
            ("EndOfGame", GameflowPhase.END_OF_GAME),
        ],
    )
    async def test_get_gameflow_phase_all_official_phases(
        self,
        sample_credentials: LcuCredentials,
        lcu_phase_str: str,
        expected_phase: GameflowPhase,
    ) -> None:
        """Every official League Client gameflow string must map to its GameflowPhase member."""
        transport = make_mock_transport(status_code=200, json_data=lcu_phase_str)
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        phase = await adapter.get_gameflow_phase()
        assert phase == expected_phase

    @pytest.mark.asyncio
    async def test_get_gameflow_phase_unknown_string_defaults_to_none(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Unknown or future gameflow string must fall back to GameflowPhase.NONE without raising."""
        transport = make_mock_transport(status_code=200, json_data="UnknownFuturePhase123")
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        phase = await adapter.get_gameflow_phase()
        assert phase == GameflowPhase.NONE

    @pytest.mark.asyncio
    async def test_get_gameflow_phase_when_disconnected_skips_request(self) -> None:
        """If adapter is DISCONNECTED, get_gameflow_phase() returns GameflowPhase.NONE without HTTP traffic."""
        mock_disc = MagicMock()
        mock_disc.discover.return_value = None
        adapter = RiotLcuAdapter(credentials=None, discoverer=mock_disc)

        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED
        phase = await adapter.get_gameflow_phase()
        assert phase == GameflowPhase.NONE

    @pytest.mark.asyncio
    async def test_get_gameflow_phase_401_sets_auth_failed(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """HTTP 401 Unauthorized updates status to AUTHENTICATION_FAILED and returns GameflowPhase.NONE."""
        transport = make_mock_transport(status_code=401, text_data="Unauthorized")
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        phase = await adapter.get_gameflow_phase()
        assert phase == GameflowPhase.NONE
        assert await adapter.get_connection_status() == LcuConnectionStatus.AUTHENTICATION_FAILED

    @pytest.mark.asyncio
    async def test_get_gameflow_phase_403_sets_auth_failed(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """HTTP 403 Forbidden updates status to AUTHENTICATION_FAILED and returns GameflowPhase.NONE."""
        transport = make_mock_transport(status_code=403, text_data="Forbidden")
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        phase = await adapter.get_gameflow_phase()
        assert phase == GameflowPhase.NONE
        assert await adapter.get_connection_status() == LcuConnectionStatus.AUTHENTICATION_FAILED

    @pytest.mark.asyncio
    async def test_get_gameflow_phase_connect_error_sets_disconnected(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """httpx.ConnectError marks adapter as DISCONNECTED and returns GameflowPhase.NONE."""
        transport = make_mock_transport(
            side_effect=httpx.ConnectError("Connection refused by target machine")
        )
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        phase = await adapter.get_gameflow_phase()
        assert phase == GameflowPhase.NONE
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_get_gameflow_phase_timeout_sets_disconnected(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """httpx.TimeoutException marks adapter as DISCONNECTED and returns GameflowPhase.NONE."""
        transport = make_mock_transport(
            side_effect=httpx.ReadTimeout("Request timed out waiting for LCU")
        )
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        phase = await adapter.get_gameflow_phase()
        assert phase == GameflowPhase.NONE
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_get_gameflow_phase_500_handled_gracefully(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """HTTP 500 Internal Server Error returns GameflowPhase.NONE without raising an exception."""
        transport = make_mock_transport(status_code=500, text_data="Internal Server Error")
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        phase = await adapter.get_gameflow_phase()
        assert phase == GameflowPhase.NONE


# ============================================================================
# 5. accept_ready_check Tests (/lol-matchmaking/v1/ready-check/accept)
# ============================================================================


class TestRiotLcuAdapterAcceptReadyCheck:
    """Tests POST /lol-matchmaking/v1/ready-check/accept execution and defensive handling."""

    @pytest.mark.asyncio
    async def test_accept_ready_check_success_200(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """HTTP 200 OK from ready check accept endpoint returns True."""
        recorded_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            recorded_requests.append(request)
            return httpx.Response(200, json={}, request=request)

        adapter = RiotLcuAdapter(
            credentials=sample_credentials,
            transport=httpx.MockTransport(handler),
        )

        result = await adapter.accept_ready_check()
        assert result is True
        assert len(recorded_requests) == 1
        req = recorded_requests[0]
        assert req.method == "POST"
        assert req.url.path == "/lol-matchmaking/v1/ready-check/accept"

    @pytest.mark.asyncio
    async def test_accept_ready_check_success_204_no_content(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """HTTP 204 No Content from ready check endpoint returns True."""
        transport = make_mock_transport(status_code=204)
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        result = await adapter.accept_ready_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_accept_ready_check_when_disconnected_returns_false(self) -> None:
        """When adapter is DISCONNECTED, accept_ready_check() returns False immediately without HTTP traffic."""
        mock_disc = MagicMock()
        mock_disc.discover.return_value = None
        adapter = RiotLcuAdapter(credentials=None, discoverer=mock_disc)

        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED
        result = await adapter.accept_ready_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_accept_ready_check_404_not_found_returns_false(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """HTTP 404 (no active ready check prompt) returns False without raising exception."""
        transport = make_mock_transport(status_code=404, text_data="No active ready check")
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        result = await adapter.accept_ready_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_accept_ready_check_500_server_error_returns_false(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """HTTP 500 returns False without raising exception."""
        transport = make_mock_transport(status_code=500, text_data="Server Error")
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        result = await adapter.accept_ready_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_accept_ready_check_connect_error_returns_false_and_disconnects(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Network drop during accept sets status to DISCONNECTED and returns False."""
        transport = make_mock_transport(
            side_effect=httpx.ConnectError("LCU process terminated unexpectedly")
        )
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        result = await adapter.accept_ready_check()
        assert result is False
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_accept_ready_check_timeout_returns_false(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Network timeout during accept returns False without unhandled exception."""
        transport = make_mock_transport(
            side_effect=httpx.ReadTimeout("Accept ready check timed out")
        )
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        result = await adapter.accept_ready_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_accept_ready_check_401_sets_auth_failed_and_returns_false(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """HTTP 401 returns False and transitions connection status to AUTHENTICATION_FAILED."""
        transport = make_mock_transport(status_code=401, text_data="Unauthorized")
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        result = await adapter.accept_ready_check()
        assert result is False
        assert await adapter.get_connection_status() == LcuConnectionStatus.AUTHENTICATION_FAILED


# ============================================================================
# 6. Event Subscriptions & Lifecycle Management
# ============================================================================


class TestRiotLcuAdapterLifecycleAndEvents:
    """Tests callback subscriptions, async context manager support, and client shutdown."""

    def test_subscribe_and_unsubscribe_callbacks(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """subscribe_phase_change and unsubscribe_phase_change must be valid and callable."""
        adapter = RiotLcuAdapter(credentials=sample_credentials)

        dummy_cb: Callable[[GameflowPhase], None] = lambda phase: None
        # Registration should not raise
        adapter.subscribe_phase_change(dummy_cb)

        # Unregistration should return bool
        unsub_res = adapter.unsubscribe_phase_change(dummy_cb)
        assert isinstance(unsub_res, bool)

    @pytest.mark.asyncio
    async def test_close_shuts_down_client_and_disconnects(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """close() closes internal HTTPX client and sets status to DISCONNECTED."""
        adapter = RiotLcuAdapter(credentials=sample_credentials)
        assert await adapter.get_connection_status() == LcuConnectionStatus.CONNECTED

        await adapter.close()
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_multiple_close_calls_are_safe(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Repeated close() calls must be idempotent and safe."""
        adapter = RiotLcuAdapter(credentials=sample_credentials)
        await adapter.close()
        await adapter.close()
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED

    @pytest.mark.asyncio
    async def test_async_context_manager_lifecycle(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Adapter supports 'async with RiotLcuAdapter(...) as adapter:' pattern."""
        transport = make_mock_transport(status_code=200, json_data="ChampSelect")

        async with RiotLcuAdapter(
            credentials=sample_credentials,
            transport=transport,
        ) as adapter:
            assert await adapter.get_connection_status() == LcuConnectionStatus.CONNECTED
            phase = await adapter.get_gameflow_phase()
            assert phase == GameflowPhase.CHAMP_SELECT

        # Exiting context block must invoke close() and disconnect
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED


# ============================================================================
# 7. Shift-Left Edge Cases & Fault Injection
# ============================================================================


class TestRiotLcuAdapterShiftLeftEdgeCases:
    """Tests extreme edge cases: malformed JSON, corrupted data, and reconnect flow."""

    @pytest.mark.asyncio
    async def test_malformed_json_response_returns_none(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Non-JSON or malformed HTML response from gameflow-phase endpoint returns GameflowPhase.NONE."""
        transport = make_mock_transport(
            status_code=200,
            text_data="<html><body>502 Bad Gateway</body></html>",
        )
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        phase = await adapter.get_gameflow_phase()
        assert phase == GameflowPhase.NONE

    @pytest.mark.asyncio
    async def test_empty_string_phase_response_returns_none(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Empty string or whitespace response returns GameflowPhase.NONE."""
        transport = make_mock_transport(status_code=200, json_data="")
        adapter = RiotLcuAdapter(credentials=sample_credentials, transport=transport)

        phase = await adapter.get_gameflow_phase()
        assert phase == GameflowPhase.NONE

    @pytest.mark.asyncio
    async def test_reconnect_recovers_from_disconnected_state(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Client starts -> disconnects on error -> reconnected via connect() -> operational again."""
        # Step 1: Initialize with failing transport
        failing_transport = make_mock_transport(
            side_effect=httpx.ConnectError("Target refused")
        )
        adapter = RiotLcuAdapter(
            credentials=sample_credentials,
            transport=failing_transport,
        )
        phase_fail = await adapter.get_gameflow_phase()
        assert phase_fail == GameflowPhase.NONE
        assert await adapter.get_connection_status() == LcuConnectionStatus.DISCONNECTED

        # Step 2: Now re-connect with new working transport / discoverer
        working_transport = make_mock_transport(status_code=200, json_data="ReadyCheck")
        adapter._transport = working_transport
        mock_disc = MagicMock()
        mock_disc.discover.return_value = sample_credentials
        adapter._discoverer = mock_disc

        reconnect_success = await adapter.connect()
        assert reconnect_success is True
        assert await adapter.get_connection_status() == LcuConnectionStatus.CONNECTED
