"""RiftPilot Adapters — Real-Time LCU WebSocket Event Stream.

Outbound adapter maintaining a persistent WebSocket connection to the League Client Update (LCU)
event stream over local WSS with self-signed SSL verification disabled.

Implements real-time gameflow phase event streaming according to Hexagonal Architecture
(ADR-0003) and Shift-Left security specifications (ADR-0004).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import ssl
from typing import Any, Callable

import websockets
import websockets.exceptions

from riftpilot.adapters.lcu_discovery import LcuCredentials
from riftpilot.core.lcu_port import GameflowPhase

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


class LcuEventStream:
    """Real-time event streaming adapter for the League Client (LCU) WebSocket interface.

    Connects to the local LCU WebSocket server using WAMP v1 protocol, subscribes to
    gameflow phase events, and dispatches updates to registered callbacks.
    """

    def __init__(
        self,
        credentials: LcuCredentials,
        on_phase_change: Callable[[GameflowPhase], Any] | None = None,
    ) -> None:
        """Initialize the LCU event stream.

        Args:
            credentials: LCU credentials containing WebSocket URL and auth credentials.
            on_phase_change: Optional callback invoked when gameflow phase transitions occur.
        """
        self._credentials: LcuCredentials = credentials
        self._callbacks: list[Callable[[GameflowPhase], Any]] = []
        if on_phase_change is not None:
            self._callbacks.append(on_phase_change)
        self._is_connected: bool = False
        self._is_running: bool = False
        self._ws: Any = None
        self._listener_task: asyncio.Task[None] | None = None

    @property
    def is_connected(self) -> bool:
        """Return True if WebSocket connection is currently active."""
        return self._is_connected

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
        """Unregister an existing phase change callback.

        Returns:
            True if the callback was registered and removed, False otherwise.
        """
        if callback in self._callbacks:
            self._callbacks.remove(callback)
            return True
        return False

    async def start(self) -> bool:
        """Connect to the LCU WebSocket interface and start listening for events.

        Returns:
            True if connection and subscription succeeded, False otherwise.
        """
        if self._is_running:
            return True

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        try:
            self._ws = await websockets.connect(
                self._credentials.ws_url,
                ssl=ssl_ctx,
                additional_headers={
                    "Authorization": self._credentials.basic_auth_header
                },
            )
        except Exception as exc:
            logger.warning(
                "Failed to connect to LCU WebSocket at %s: %s",
                self._credentials.ws_url,
                exc,
            )
            self._is_connected = False
            self._is_running = False
            self._ws = None
            return False

        self._is_connected = True
        self._is_running = True

        try:
            # Opcode 5: WAMP v1 SUBSCRIBE
            subscribe_msg = json.dumps([
                5,
                "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase",
            ])
            await self._ws.send(subscribe_msg)
        except Exception as exc:
            logger.warning("Failed to send subscription handshake: %s", exc)
            await self.stop()
            return False

        self._listener_task = asyncio.create_task(self._listen_loop())
        return True

    async def _listen_loop(self) -> None:
        """Background coroutine listening for incoming WebSocket messages."""
        while self._is_running and self._ws is not None:
            try:
                msg = await self._ws.recv()
            except websockets.exceptions.ConnectionClosed as exc:
                logger.debug("LCU WebSocket closed: %s", exc)
                self._is_connected = False
                break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("LCU WebSocket error during recv: %s", exc)
                self._is_connected = False
                break

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

        self._is_connected = False
        self._is_running = False

    async def _dispatch_phase(self, phase: GameflowPhase) -> None:
        """Dispatch a gameflow phase change to all registered callbacks."""
        for cb in list(self._callbacks):
            try:
                res = cb(phase)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                logger.warning("Error in phase callback: %s", exc)

    async def stop(self) -> None:
        """Gracefully stop listener task and close WebSocket connection."""
        self._is_running = False
        self._is_connected = False
        self._is_running = False

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


__all__ = ["LcuEventStream", "parse_lcu_event"]
