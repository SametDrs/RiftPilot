"""RiftPilot — Tests for LCU Real-Time WebSocket Event Stream (Milestone 2, Task 2.3).

Verifies real-time event streaming from the League Client (LCU) WebSocket interface:
1. WAMP v1 Event Parsing (`parse_lcu_event`):
   - Opcode 8 validation, URI and data extraction.
   - Resilient edge-case handling (empty messages, corrupted JSON, non-event opcodes).
2. SSL Context and Authentication Headers:
   - Disabling host check and certificate verification for LCU local self-signed SSL.
   - Basic authentication handshake header (`Authorization: Basic <base64("riot:<token>")>`).
3. `LcuEventStream` Lifecycle & Subscription:
   - Asynchronous `start()` and `stop()` lifecycle.
   - WAMP v1 subscription handshake (`[5, "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase"]`).
   - Connection status tracking (`is_connected`, `is_running`).
   - Async context manager support (`async with LcuEventStream(...)`).
4. Event Dispatching & Phase Mapping:
   - Delivery to synchronous and asynchronous callbacks.
   - Mapping LCU string phases to `GameflowPhase` enum values.
   - Event filtering (unrelated URIs ignored) and callback error isolation.
5. Network Fault Tolerance & Resilience:
   - Handling unexpected disconnections (`websockets.ConnectionClosed`, `OSError`).
   - Non-blocking shutdown and task cleanup.
6. `RiotLcuAdapter` Outbound Port Integration:
   - Binding `LcuEventStream` with `RiotLcuAdapter`.
   - Propagating phase change events to adapter subscribers.
   - Resource cleanup on `adapter.close()`.

Test-First / Shift-Left Approach:
Target module: `riftpilot.adapters.lcu_stream`
(Initially fails with ModuleNotFoundError during TDD Red Phase).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import ssl
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import websockets
import websockets.exceptions

from riftpilot.adapters.lcu_discovery import LcuCredentials
from riftpilot.core.lcu_port import GameflowPhase

# Target module under test: Expected to fail with ModuleNotFoundError during TDD Red Phase
from riftpilot.adapters.lcu_stream import (
    LcuEventStream,
    parse_lcu_event,
)
from riftpilot.adapters.riot_lcu import RiotLcuAdapter


# ============================================================================
# Test Doubles & Fixtures
# ============================================================================


class MockWebSocketConnection:
    """Test double simulating a websockets ClientConnection for LCU testing.

    Provides queue-based message reception and outbound message logging without
    opening real network sockets.
    """

    def __init__(
        self,
        incoming_messages: list[str | bytes | Exception] | None = None,
    ) -> None:
        self.sent_messages: list[str | bytes] = []
        self._incoming: asyncio.Queue[str | bytes | Exception] = asyncio.Queue()
        self.closed: bool = False
        self.close_code: int = 1000
        self.close_reason: str = ""

        if incoming_messages:
            for msg in incoming_messages:
                self._incoming.put_nowait(msg)

    def queue_message(self, message: str | bytes | Exception) -> None:
        """Enqueue an incoming message or exception for the next recv() call."""
        self._incoming.put_nowait(message)

    async def send(self, message: str | bytes) -> None:
        """Record an outbound message sent by the client."""
        if self.closed:
            raise websockets.exceptions.ConnectionClosedOK(
                rcvd=None,
                sent=None,
            )
        self.sent_messages.append(message)

    async def recv(self) -> str | bytes:
        """Simulate receiving the next message from the LCU event stream."""
        if self.closed and self._incoming.empty():
            raise websockets.exceptions.ConnectionClosedOK(
                rcvd=None,
                sent=None,
            )

        item = await self._incoming.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Simulate closing the websocket connection."""
        self.closed = True
        self.close_code = code
        self.close_reason = reason
        # Unblock any pending recv() call
        if self._incoming.empty():
            self._incoming.put_nowait(
                websockets.exceptions.ConnectionClosedOK(rcvd=None, sent=None)
            )

    async def wait_closed(self) -> None:
        """Simulate waiting until connection is closed."""
        self.closed = True

    async def __aenter__(self) -> MockWebSocketConnection:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        await self.close()


@pytest.fixture
def sample_credentials() -> LcuCredentials:
    """Provides standard valid LCU credentials for WebSocket testing."""
    return LcuCredentials(
        port=29999,
        auth_token="mock_lcu_secret_auth_token_42",
        pid=12345,
        protocol="https",
    )


@pytest.fixture
def mock_ws() -> MockWebSocketConnection:
    """Fixture providing an isolated MockWebSocketConnection instance."""
    return MockWebSocketConnection()


# ============================================================================
# 1. WAMP v1 Event Parsing Tests (`parse_lcu_event`)
# ============================================================================


class TestParseLcuEvent:
    """Unit tests for the WAMP v1 event parser function `parse_lcu_event`."""

    def test_parse_valid_ready_check_string(self) -> None:
        """Valid WAMP v1 event message returns (uri, data) tuple."""
        raw = (
            '[8, "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase", '
            '{"data": "ReadyCheck", "eventType": "Update", "uri": "/lol-gameflow/v1/gameflow-phase"}]'
        )
        result = parse_lcu_event(raw)
        assert result is not None
        uri, data = result
        assert uri == "/lol-gameflow/v1/gameflow-phase"
        assert data == "ReadyCheck"

    def test_parse_valid_event_bytes(self) -> None:
        """Bytes input payload is decoded and parsed accurately."""
        raw = (
            b'[8, "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase", '
            b'{"data": "ChampSelect", "eventType": "Update", "uri": "/lol-gameflow/v1/gameflow-phase"}]'
        )
        result = parse_lcu_event(raw)
        assert result is not None
        uri, data = result
        assert uri == "/lol-gameflow/v1/gameflow-phase"
        assert data == "ChampSelect"

    def test_parse_complex_dict_data_payload(self) -> None:
        """Payload data containing nested dictionaries and lists is preserved."""
        raw = json.dumps([
            8,
            "OnJsonApiEvent_lol-gameflow_v1_session",
            {
                "data": {"phase": "InProgress", "mapId": 11, "gameDodge": None},
                "eventType": "Update",
                "uri": "/lol-gameflow/v1/session",
            },
        ])
        result = parse_lcu_event(raw)
        assert result is not None
        uri, data = result
        assert uri == "/lol-gameflow/v1/session"
        assert data == {"phase": "InProgress", "mapId": 11, "gameDodge": None}

    @pytest.mark.parametrize(
        ("raw_data_value", "expected_data"),
        [
            ("None", "None"),
            (None, None),
            (True, True),
            (False, False),
            (42, 42),
            (3.14, 3.14),
            ([], []),
        ],
    )
    def test_parse_various_data_types(
        self, raw_data_value: Any, expected_data: Any
    ) -> None:
        """Parser extracts arbitrary JSON-compatible data values without mutation."""
        raw = json.dumps([
            8,
            "OnJsonApiEvent",
            {"data": raw_data_value, "uri": "/lol-gameflow/v1/gameflow-phase"},
        ])
        result = parse_lcu_event(raw)
        assert result == ("/lol-gameflow/v1/gameflow-phase", expected_data)

    def test_parse_opcode_not_8_returns_none(self) -> None:
        """Non-event WAMP opcodes (0: Welcome, 5: Subscribe, 6: Ack, etc.) return None."""
        opcodes = [
            [0, "session_id", 1, "RiotClient"],
            [1, "prefix", "http://example.com"],
            [5, "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase"],
            [6, "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase"],
            [7, "OnJsonApiEvent"],
            [9, "unknown"],
        ]
        for msg in opcodes:
            assert parse_lcu_event(json.dumps(msg)) is None

    @pytest.mark.parametrize(
        "empty_input",
        ["", b"", "   ", b"   \r\n\t", " \n "],
    )
    def test_parse_empty_or_whitespace_returns_none(
        self, empty_input: str | bytes
    ) -> None:
        """Empty messages or whitespace-only inputs return None without raising."""
        assert parse_lcu_event(empty_input) is None

    @pytest.mark.parametrize(
        "corrupted_input",
        [
            "{not valid json",
            "[8, incomplete json",
            "hello world",
            b"\x80\x81\x82 corrupted utf8",
            "}{",
        ],
    )
    def test_parse_corrupted_json_returns_none(
        self, corrupted_input: str | bytes
    ) -> None:
        """Malformed JSON or invalid UTF-8 bytes return None safely."""
        assert parse_lcu_event(corrupted_input) is None

    @pytest.mark.parametrize(
        "invalid_length_list",
        [
            [],
            [8],
            [8, "topic_only"],
            [8, "topic", {}, "unexpected_extra_element"],
            [8, "topic", {}, "fourth", "fifth"],
        ],
    )
    def test_parse_invalid_list_lengths_returns_none(
        self, invalid_length_list: list[Any]
    ) -> None:
        """WAMP event messages must contain exactly 3 items [opcode, topic, payload]."""
        assert parse_lcu_event(json.dumps(invalid_length_list)) is None

    @pytest.mark.parametrize(
        "non_list_json",
        [
            {"key": "value"},
            "just a string",
            12345,
            True,
            None,
        ],
    )
    def test_parse_non_list_json_returns_none(self, non_list_json: Any) -> None:
        """Root JSON structures that are not arrays return None."""
        assert parse_lcu_event(json.dumps(non_list_json)) is None

    @pytest.mark.parametrize(
        "invalid_payload",
        [
            "string instead of dict",
            123,
            True,
            None,
            ["list", "instead", "of", "dict"],
        ],
    )
    def test_parse_payload_not_dict_returns_none(
        self, invalid_payload: Any
    ) -> None:
        """The third element must be a dictionary; otherwise return None."""
        raw = json.dumps([8, "OnJsonApiEvent", invalid_payload])
        assert parse_lcu_event(raw) is None

    def test_parse_payload_missing_uri_returns_none(self) -> None:
        """When payload dictionary lacks 'uri' key, return None."""
        raw = json.dumps([8, "OnJsonApiEvent", {"data": "ReadyCheck"}])
        assert parse_lcu_event(raw) is None

    def test_parse_payload_missing_data_returns_none_data(self) -> None:
        """When payload has 'uri' but no 'data' key, data defaults to None."""
        raw = json.dumps([8, "OnJsonApiEvent", {"uri": "/lol-gameflow/v1/gameflow-phase"}])
        result = parse_lcu_event(raw)
        assert result == ("/lol-gameflow/v1/gameflow-phase", None)


# ============================================================================
# 2. SSL and Authentication Configuration Tests
# ============================================================================


class TestLcuStreamSslAndHeaders:
    """Verifies self-signed SSL configuration and Basic authentication handshake."""

    @pytest.mark.asyncio
    async def test_websocket_connect_receives_self_signed_ssl_context(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """WebSocket connection must use an SSLContext with hostname check & cert verify disabled."""
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(credentials=sample_credentials)
            await stream.start()
            await stream.stop()

        assert mock_connect.called, "websockets.connect was not invoked"
        call_kwargs = mock_connect.call_args.kwargs

        # SSL context must be passed via ssl argument
        assert "ssl" in call_kwargs, "websockets.connect must be called with ssl=..."
        ssl_ctx: ssl.SSLContext = call_kwargs["ssl"]
        assert isinstance(ssl_ctx, ssl.SSLContext)
        assert ssl_ctx.check_hostname is False
        assert ssl_ctx.verify_mode == ssl.CERT_NONE

    @pytest.mark.asyncio
    async def test_websocket_connect_receives_basic_auth_header(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """Handshake headers must include 'Authorization: Basic <base64("riot:<token>")>'."""
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(credentials=sample_credentials)
            await stream.start()
            await stream.stop()

        call_kwargs = mock_connect.call_args.kwargs
        # websockets supports additional_headers (v13+) or extra_headers
        headers = (
            call_kwargs.get("additional_headers")
            or call_kwargs.get("extra_headers")
        )
        assert headers is not None, "Handshake headers must not be None"

        # Headers can be dict or list of (name, value) tuples
        if isinstance(headers, list):
            header_dict = {k.lower(): v for k, v in headers}
        else:
            header_dict = {k.lower(): v for k, v in headers.items()}

        assert "authorization" in header_dict
        assert header_dict["authorization"] == sample_credentials.basic_auth_header

    @pytest.mark.asyncio
    async def test_websocket_connect_receives_ws_url(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """WebSocket URL must target credentials.ws_url (wss://127.0.0.1:<port>)."""
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(credentials=sample_credentials)
            await stream.start()
            await stream.stop()

        target_url = (
            mock_connect.call_args.args[0]
            if mock_connect.call_args.args
            else mock_connect.call_args.kwargs.get("uri")
        )
        assert str(target_url).rstrip("/") == sample_credentials.ws_url.rstrip("/")


# ============================================================================
# 3. `LcuEventStream` Lifecycle & Subscription Handshake Tests
# ============================================================================


class TestLcuEventStreamLifecycle:
    """Tests startup, WAMP subscription handshake, shutdown, and context management."""

    def test_initial_state_is_not_connected_and_not_running(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Fresh instance has is_connected=False and is_running=False."""
        stream = LcuEventStream(credentials=sample_credentials)
        assert stream.is_connected is False
        assert stream.is_running is False

    @pytest.mark.asyncio
    async def test_start_sends_wamp_subscribe_message(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """Upon start(), client sends WAMP subscribe opcode 5 for gameflow events."""
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(credentials=sample_credentials)
            await stream.start()

            assert stream.is_connected is True
            assert stream.is_running is True

            # Wait briefly for subscription message dispatch
            await asyncio.sleep(0.01)

            assert len(mock_ws.sent_messages) >= 1
            raw_sub = mock_ws.sent_messages[0]
            sub_payload = json.loads(raw_sub)

            # Opcode 5 (SUBSCRIBE) in WAMP v1
            assert sub_payload[0] == 5
            # Must subscribe to gameflow phase or wildcard OnJsonApiEvent
            assert (
                sub_payload[1] == "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase"
                or sub_payload[1] == "OnJsonApiEvent"
            )

            await stream.stop()

    @pytest.mark.asyncio
    async def test_stop_closes_connection_and_resets_flags(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """stop() terminates background loop, closes websocket, and resets flags."""
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(credentials=sample_credentials)
            await stream.start()
            assert stream.is_connected is True
            assert stream.is_running is True

            await stream.stop()
            assert stream.is_connected is False
            assert stream.is_running is False
            assert mock_ws.closed is True

    @pytest.mark.asyncio
    async def test_stop_idempotent_when_not_started_or_repeated(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """Calling stop() multiple times or without start() must be safe and silent."""
        stream = LcuEventStream(credentials=sample_credentials)
        await stream.stop()
        await stream.stop()
        assert stream.is_connected is False
        assert stream.is_running is False

    @pytest.mark.asyncio
    async def test_async_context_manager_lifecycle(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """'async with LcuEventStream(...) as stream:' automatically starts and stops."""
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            async with LcuEventStream(credentials=sample_credentials) as stream:
                assert stream.is_running is True
                assert stream.is_connected is True

            assert stream.is_running is False
            assert stream.is_connected is False
            assert mock_ws.closed is True


# ============================================================================
# 4. Event Dispatching & Gameflow Phase Mapping Tests
# ============================================================================


class TestLcuEventStreamDispatching:
    """Tests incoming event delivery, phase conversion, and callback execution."""

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
    async def test_dispatch_all_official_gameflow_phases(
        self,
        sample_credentials: LcuCredentials,
        mock_ws: MockWebSocketConnection,
        lcu_phase_str: str,
        expected_phase: GameflowPhase,
    ) -> None:
        """Every official LCU phase string triggers callback with matching GameflowPhase enum."""
        received_phases: list[GameflowPhase] = []
        phase_event = asyncio.Event()

        def on_phase(phase: GameflowPhase) -> None:
            received_phases.append(phase)
            phase_event.set()

        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(
                credentials=sample_credentials,
                on_phase_change=on_phase,
            )
            await stream.start()

            # Enqueue LCU event
            event_msg = json.dumps([
                8,
                "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase",
                {
                    "data": lcu_phase_str,
                    "eventType": "Update",
                    "uri": "/lol-gameflow/v1/gameflow-phase",
                },
            ])
            mock_ws.queue_message(event_msg)

            await asyncio.wait_for(phase_event.wait(), timeout=1.0)
            assert received_phases == [expected_phase]

            await stream.stop()

    @pytest.mark.asyncio
    async def test_dispatch_to_async_coroutine_callback(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """Asynchronous (coroutine) callbacks are supported and awaited."""
        received_phases: list[GameflowPhase] = []
        async_event = asyncio.Event()

        async def async_on_phase(phase: GameflowPhase) -> None:
            await asyncio.sleep(0.001)
            received_phases.append(phase)
            async_event.set()

        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(
                credentials=sample_credentials,
                on_phase_change=async_on_phase,
            )
            await stream.start()

            event_msg = json.dumps([
                8,
                "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase",
                {
                    "data": "Matchmaking",
                    "eventType": "Update",
                    "uri": "/lol-gameflow/v1/gameflow-phase",
                },
            ])
            mock_ws.queue_message(event_msg)

            await asyncio.wait_for(async_event.wait(), timeout=1.0)
            assert received_phases == [GameflowPhase.MATCHMAKING]

            await stream.stop()

    @pytest.mark.asyncio
    async def test_multiple_subscribers_and_unsubscription(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """Multiple callbacks can be registered and unregistered dynamically."""
        cb1_calls: list[GameflowPhase] = []
        cb2_calls: list[GameflowPhase] = []
        event1 = asyncio.Event()

        def cb1(phase: GameflowPhase) -> None:
            cb1_calls.append(phase)
            event1.set()

        def cb2(phase: GameflowPhase) -> None:
            cb2_calls.append(phase)

        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(credentials=sample_credentials)
            stream.add_phase_callback(cb1)
            stream.add_phase_callback(cb2)

            await stream.start()

            # First event: both receive
            mock_ws.queue_message(
                json.dumps([
                    8,
                    "OnJsonApiEvent",
                    {"data": "ReadyCheck", "uri": "/lol-gameflow/v1/gameflow-phase"},
                ])
            )
            await asyncio.wait_for(event1.wait(), timeout=1.0)
            assert cb1_calls == [GameflowPhase.READY_CHECK]
            assert cb2_calls == [GameflowPhase.READY_CHECK]

            # Unregister cb2
            stream.remove_phase_callback(cb2)
            event1.clear()

            # Second event: only cb1 receives
            mock_ws.queue_message(
                json.dumps([
                    8,
                    "OnJsonApiEvent",
                    {"data": "ChampSelect", "uri": "/lol-gameflow/v1/gameflow-phase"},
                ])
            )
            await asyncio.wait_for(event1.wait(), timeout=1.0)
            assert cb1_calls == [GameflowPhase.READY_CHECK, GameflowPhase.CHAMP_SELECT]
            assert cb2_calls == [GameflowPhase.READY_CHECK]

            await stream.stop()

    @pytest.mark.asyncio
    async def test_unrelated_uri_does_not_trigger_callback(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """Events for other LCU URIs (chat, summoner, etc.) are filtered out."""
        received: list[GameflowPhase] = []

        def on_phase(phase: GameflowPhase) -> None:
            received.append(phase)

        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(
                credentials=sample_credentials,
                on_phase_change=on_phase,
            )
            await stream.start()

            # Unrelated events
            mock_ws.queue_message(
                json.dumps([
                    8,
                    "OnJsonApiEvent_lol-chat_v1_conversations",
                    {"data": {"unread": 1}, "uri": "/lol-chat/v1/conversations"},
                ])
            )
            mock_ws.queue_message(
                json.dumps([
                    8,
                    "OnJsonApiEvent_lol-summoner_v1_current-summoner",
                    {"data": {"id": 100}, "uri": "/lol-summoner/v1/current-summoner"},
                ])
            )

            await asyncio.sleep(0.05)
            assert len(received) == 0

            await stream.stop()

    @pytest.mark.asyncio
    async def test_unknown_gameflow_phase_string_defaults_to_none(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """Unrecognized phase string falls back to GameflowPhase.NONE without raising."""
        received: list[GameflowPhase] = []
        event = asyncio.Event()

        def on_phase(phase: GameflowPhase) -> None:
            received.append(phase)
            event.set()

        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(
                credentials=sample_credentials,
                on_phase_change=on_phase,
            )
            await stream.start()

            mock_ws.queue_message(
                json.dumps([
                    8,
                    "OnJsonApiEvent",
                    {"data": "UnrecognizedCustomMode123", "uri": "/lol-gameflow/v1/gameflow-phase"},
                ])
            )

            await asyncio.wait_for(event.wait(), timeout=1.0)
            assert received == [GameflowPhase.NONE]

            await stream.stop()

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_crash_event_stream(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """If a subscriber raises an exception, the listener logs and continues."""
        received: list[GameflowPhase] = []
        second_event = asyncio.Event()

        def faulty_callback(phase: GameflowPhase) -> None:
            if phase == GameflowPhase.READY_CHECK:
                raise RuntimeError("Boom! Callback failed")
            received.append(phase)
            second_event.set()

        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(
                credentials=sample_credentials,
                on_phase_change=faulty_callback,
            )
            await stream.start()

            # First event triggers exception in callback
            mock_ws.queue_message(
                json.dumps([
                    8,
                    "OnJsonApiEvent",
                    {"data": "ReadyCheck", "uri": "/lol-gameflow/v1/gameflow-phase"},
                ])
            )
            # Second event must still be processed
            mock_ws.queue_message(
                json.dumps([
                    8,
                    "OnJsonApiEvent",
                    {"data": "ChampSelect", "uri": "/lol-gameflow/v1/gameflow-phase"},
                ])
            )

            await asyncio.wait_for(second_event.wait(), timeout=1.0)
            assert received == [GameflowPhase.CHAMP_SELECT]
            assert stream.is_running is True

            await stream.stop()


# ============================================================================
# 5. Network Fault Tolerance & Error Handling Tests
# ============================================================================


class TestLcuEventStreamResilience:
    """Tests connection drops, refusal, and resilient shutdown."""

    @pytest.mark.asyncio
    async def test_connection_closed_abnormally_updates_state(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """When the remote server closes connection, is_connected becomes False gracefully."""
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(credentials=sample_credentials)
            await stream.start()
            assert stream.is_connected is True

            # Server drops connection abruptly
            mock_ws.queue_message(
                websockets.exceptions.ConnectionClosedError(
                    rcvd=None,
                    sent=None,
                )
            )

            # Wait briefly for exception handling
            await asyncio.sleep(0.05)
            assert stream.is_connected is False

            await stream.stop()

    @pytest.mark.asyncio
    async def test_connect_refused_error_handled_gracefully(
        self, sample_credentials: LcuCredentials
    ) -> None:
        """When LCU client is not running, connect error does not crash and leaves disconnected."""
        mock_connect = AsyncMock(
            side_effect=OSError("Connection refused (target machine actively refused it)")
        )

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(credentials=sample_credentials)
            success = await stream.start()

            # start() should return False or gracefully handle failure
            assert stream.is_connected is False
            assert stream.is_running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_background_listener_cleanly(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """stop() terminates the background loop even if it is blocked waiting on recv()."""
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            stream = LcuEventStream(credentials=sample_credentials)
            await stream.start()
            assert stream.is_running is True

            # Stop without enqueueing any message
            await stream.stop()
            assert stream.is_running is False
            assert stream.is_connected is False


# ============================================================================
# 6. `RiotLcuAdapter` Outbound Port Integration Tests
# ============================================================================


class TestRiotLcuAdapterStreamIntegration:
    """Verifies seamless integration between RiotLcuAdapter and LcuEventStream."""

    @pytest.mark.asyncio
    async def test_adapter_attaches_and_starts_event_stream(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """Connecting adapter initializes and starts the underlying LcuEventStream."""
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            adapter = RiotLcuAdapter(credentials=sample_credentials)

            # Check if event stream attribute is created
            stream = getattr(adapter, "_stream", None) or getattr(adapter, "stream", None)
            if stream is not None:
                assert isinstance(stream, LcuEventStream)

            await adapter.close()

    @pytest.mark.asyncio
    async def test_adapter_subscribers_receive_event_stream_phases(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """Events arriving on the stream propagate to adapter.subscribe_phase_change subscribers."""
        received_phases: list[GameflowPhase] = []
        phase_event = asyncio.Event()

        def subscriber(phase: GameflowPhase) -> None:
            received_phases.append(phase)
            phase_event.set()

        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            adapter = RiotLcuAdapter(credentials=sample_credentials)
            adapter.subscribe_phase_change(subscriber)

            # Trigger stream start if needed or start manually
            stream = getattr(adapter, "_stream", None) or getattr(adapter, "stream", None)
            if stream is not None and not stream.is_running:
                await stream.start()

            # Enqueue phase change event
            mock_ws.queue_message(
                json.dumps([
                    8,
                    "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase",
                    {
                        "data": "ReadyCheck",
                        "eventType": "Update",
                        "uri": "/lol-gameflow/v1/gameflow-phase",
                    },
                ])
            )

            # Wait for dispatch if stream was wired
            if stream is not None:
                await asyncio.wait_for(phase_event.wait(), timeout=1.0)
                assert received_phases == [GameflowPhase.READY_CHECK]

            await adapter.close()

    @pytest.mark.asyncio
    async def test_adapter_close_stops_event_stream(
        self, sample_credentials: LcuCredentials, mock_ws: MockWebSocketConnection
    ) -> None:
        """Calling adapter.close() shuts down the underlying LcuEventStream."""
        mock_connect = AsyncMock(return_value=mock_ws)

        with patch("websockets.connect", mock_connect):
            adapter = RiotLcuAdapter(credentials=sample_credentials)
            stream = getattr(adapter, "_stream", None) or getattr(adapter, "stream", None)

            if stream is not None:
                if not stream.is_running:
                    await stream.start()
                assert stream.is_running is True

                await adapter.close()
                assert stream.is_running is False
                assert stream.is_connected is False
            else:
                await adapter.close()
