"""RiftPilot Adapters — Real-Time LCU WebSocket Event Stream.

Outbound adapter maintaining a persistent WebSocket connection to the League Client Update (LCU)
event stream over local WSS with self-signed SSL verification disabled.

Implements real-time gameflow phase, champion select, and party lobby event streaming according to
Hexagonal Architecture (ADR-0003) and Shift-Left security specifications (ADR-0004).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import ssl
from enum import Enum
from typing import Any, Callable

import websockets
import websockets.exceptions

from riftpilot.adapters.lcu_discovery import LcuCredentials
from riftpilot.core.champ_select import ChampSelectSession
from riftpilot.core.lcu_port import GameflowPhase
from riftpilot.core.lobby import LobbyData

logger = logging.getLogger("RiftPilot.LcuStream")


def parse_lcu_event(raw_message: str | bytes) -> tuple[str, Any] | None:
    """Parse a raw Riot WAMP v1 event message into a (uri, data) tuple.

    Riot's LCU WebSocket delivers events serialized as JSON arrays formatted according
    to the WAMP v1 protocol specification: `[8, topic, payload]`, where opcode 8 indicates
    an event publication. The payload is a dictionary containing `uri` and `data`.

    Args:
        raw_message: Raw text or bytes payload received from the WebSocket.

    Returns:
        A tuple of (uri, data) if the message is a valid opcode 8 event with a dictionary
        payload containing a 'uri' field; otherwise None.
    """
    if isinstance(raw_message, bytes):
        try:
            text = raw_message.decode("utf-8")
        except UnicodeDecodeError:
            return None
    elif isinstance(raw_message, str):
        text = raw_message
    else:
        return None

    if not text.strip():
        return None

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(parsed, list) or len(parsed) != 3:
        return None

    opcode = parsed[0]
    # Opcode 8 is WAMP v1 EVENT (must be int 8, not bool True)
    if opcode != 8 or isinstance(opcode, bool):
        return None

    payload = parsed[2]
    if not isinstance(payload, dict):
        return None

    if "uri" not in payload:
        return None

    uri = payload["uri"]
    if not isinstance(uri, str):
        return None
    data = payload.get("data")
    return (uri, data)


class StreamState(str, Enum):
    """Connection state of the LCU event stream."""

    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STOPPED = "stopped"


SUBSCRIPTION_TOPICS: tuple[str, ...] = (
    "OnJsonApiEvent",
    "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase",
    "OnJsonApiEvent_lol-champ-select_v1_session",
    "OnJsonApiEvent_lol-lobby_v2_lobby",
)


class LcuEventStream:
    """Outbound adapter providing a persistent WebSocket event stream from LCU."""

    def __init__(
        self,
        credentials: LcuCredentials,
        on_phase_change: Callable[[GameflowPhase], Any] | None = None,
        credentials_provider: Callable[[], Any] | None = None,
        on_state_change: Callable[[StreamState], Any] | None = None,
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
        jitter: float = 0.25,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        """Initialize event stream adapter with LCU credentials and optional initial phase callback."""
        self._credentials = credentials
        self._credentials_provider = credentials_provider
        self._on_state_change = on_state_change
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._jitter = jitter
        self._sleep = sleep
        self._state = StreamState.STOPPED
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._callbacks: list[Callable[[GameflowPhase], Any]] = []
        if on_phase_change is not None:
            self._callbacks.append(on_phase_change)
        self._champ_select_callbacks: list[
            Callable[[ChampSelectSession | None], Any]
        ] = []
        self._lobby_callbacks: list[
            Callable[[LobbyData | None], Any]
        ] = []
        self._is_connected = False
        self._is_running = False

    @property
    def state(self) -> StreamState:
        """Return the current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Return True if WebSocket connection is open and active."""
        return self._is_connected and self._ws is not None

    @property
    def is_running(self) -> bool:
        """Return True if the stream loop is currently running or starting."""
        return self._is_running

    def add_phase_callback(
        self, callback: Callable[[GameflowPhase], Any]
    ) -> None:
        """Register a callback for gameflow phase transition events."""
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def remove_phase_callback(
        self, callback: Callable[[GameflowPhase], Any]
    ) -> bool:
        """Unregister an existing phase change callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
            return True
        return False

    def add_champ_select_callback(
        self, callback: Callable[[ChampSelectSession | None], Any]
    ) -> None:
        """Register a callback for champion select session state updates."""
        if callback not in self._champ_select_callbacks:
            self._champ_select_callbacks.append(callback)

    def remove_champ_select_callback(
        self, callback: Callable[[ChampSelectSession | None], Any]
    ) -> bool:
        """Unregister an existing champion select session callback."""
        if callback in self._champ_select_callbacks:
            self._champ_select_callbacks.remove(callback)
            return True
        return False

    def add_lobby_callback(
        self, callback: Callable[[LobbyData | None], Any]
    ) -> None:
        """Register a callback for party lobby updates."""
        if callback not in self._lobby_callbacks:
            self._lobby_callbacks.append(callback)

    def remove_lobby_callback(
        self, callback: Callable[[LobbyData | None], Any]
    ) -> bool:
        """Unregister an existing lobby update callback."""
        if callback in self._lobby_callbacks:
            self._lobby_callbacks.remove(callback)
            return True
        return False

    def _set_state(self, state: StreamState) -> None:
        """Update connection state and notify the optional observer."""
        if state == self._state:
            return
        self._state = state
        if self._on_state_change is not None:
            try:
                self._on_state_change(state)
            except Exception as exc:
                logger.warning("Error in stream state callback: %s", exc)

    async def _open(self) -> bool:
        """Open the WebSocket and send all subscriptions. Returns True on success."""
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        try:
            ws = await websockets.connect(
                self._credentials.ws_url,
                ssl=ssl_ctx,
                additional_headers={
                    "Authorization": self._credentials.basic_auth_header,
                },
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            )
        except Exception as exc:
            logger.debug("Failed to connect to LCU WebSocket at %s: %s", self._credentials.ws_url, exc)
            self._is_connected = False
            self._ws = None
            return False

        try:
            # Opcode 5: WAMP v1 SUBSCRIBE (all events + explicit topic fallbacks)
            for topic in SUBSCRIPTION_TOPICS:
                await ws.send(json.dumps([5, topic]))
        except Exception as exc:
            logger.warning("Failed to send subscription handshake: %s", exc)
            try:
                await ws.close()
            except Exception:
                pass
            self._is_connected = False
            self._ws = None
            return False

        self._ws = ws
        self._is_connected = True
        self._set_state(StreamState.CONNECTED)
        return True

    async def start(self) -> bool:
        """Connect to the LCU WebSocket and start listening (with auto-reconnect)."""
        if self._is_running:
            return True

        if not await self._open():
            self._is_running = False
            self._set_state(StreamState.STOPPED)
            return False

        self._is_running = True
        self._listener_task = asyncio.create_task(self._run())
        return True

    def _next_delay(self, attempt: int) -> float:
        """Exponential backoff delay (with jitter) for the given 0-based attempt."""
        base = min(self._backoff_max, self._backoff_initial * (2 ** attempt))
        if self._jitter > 0:
            base += base * self._jitter * random.random()
        return base

    async def _refresh_credentials(self) -> bool:
        """Re-discover credentials via the provider. False if client not found."""
        if self._credentials_provider is None:
            return True
        try:
            result = self._credentials_provider()
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            logger.debug("LCU credentials refresh failed: %s", exc)
            return False
        if result is None:
            return False
        self._credentials = result
        return True

    async def _run(self) -> None:
        """Supervisor: listen, and on drop reconnect with exponential backoff."""
        try:
            while self._is_running:
                await self._listen_loop()
                if not self._is_running:
                    break
                self._set_state(StreamState.RECONNECTING)
                attempt = 0
                while self._is_running:
                    await self._sleep(self._next_delay(attempt))
                    attempt += 1
                    if not self._is_running:
                        break
                    if await self._refresh_credentials() and await self._open():
                        break
        except asyncio.CancelledError:
            pass
        finally:
            self._is_connected = False
            self._is_running = False
            self._set_state(StreamState.STOPPED)

    async def _listen_loop(self) -> None:
        """Receive messages on the current socket until it drops."""
        while self._is_running and self._ws is not None:
            try:
                msg = await self._ws.recv()
            except websockets.exceptions.ConnectionClosed as exc:
                logger.debug("LCU WebSocket closed: %s", exc)
                self._is_connected = False
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("LCU WebSocket error during recv: %s", exc)
                self._is_connected = False
                return

            parsed = parse_lcu_event(msg)
            if parsed is not None:
                uri, data = parsed
                if uri == "/lol-gameflow/v1/gameflow-phase":
                    phase_str = str(data) if data is not None else "None"
                    try:
                        phase = GameflowPhase(phase_str)
                    except ValueError:
                        phase = GameflowPhase.NONE
                    await self._dispatch_phase(phase)
                elif uri == "/lol-champ-select/v1/session":
                    if isinstance(data, dict):
                        session = ChampSelectSession.from_lcu_dict(data)
                    else:
                        session = None
                    await self._dispatch_champ_select(session)
                elif uri == "/lol-lobby/v2/lobby":
                    if isinstance(data, dict):
                        lobby = LobbyData.from_lcu_dict(data)
                    else:
                        lobby = None
                    await self._dispatch_lobby(lobby)

        self._is_connected = False

    async def _dispatch_phase(self, phase: GameflowPhase) -> None:
        """Dispatch a gameflow phase change to all registered callbacks."""
        for cb in list(self._callbacks):
            try:
                res = cb(phase)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                logger.warning("Error in phase callback: %s", exc)

    async def _dispatch_champ_select(
        self, session: ChampSelectSession | None
    ) -> None:
        """Dispatch a champ select session update to all registered callbacks."""
        for cb in list(self._champ_select_callbacks):
            try:
                res = cb(session)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                logger.warning("Error in champ select stream callback: %s", exc)

    async def _dispatch_lobby(
        self, lobby: LobbyData | None
    ) -> None:
        """Dispatch a lobby update to all registered callbacks."""
        for cb in list(self._lobby_callbacks):
            try:
                res = cb(lobby)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                logger.warning("Error in lobby stream callback: %s", exc)

    async def stop(self) -> None:
        """Gracefully stop listener task and close WebSocket connection."""
        self._is_running = False
        self._is_connected = False

        if self._listener_task is not None:
            if not self._listener_task.done():
                self._listener_task.cancel()
                try:
                    await self._listener_task
                except asyncio.CancelledError:
                    pass
            self._listener_task = None

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._set_state(StreamState.STOPPED)

    async def __aenter__(self) -> LcuEventStream:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Async context manager exit."""
        await self.stop()


__all__ = ["LcuEventStream", "StreamState", "parse_lcu_event"]
