"""RiftPilot Adapters — Real Riot LCU HTTPX Adapter.

Outbound adapter communicating with the live League Client Update (LCU) REST API
over local HTTPS using HTTPX with self-signed SSL verification disabled.

Implements ILcuPort according to Hexagonal Architecture (ADR-0003) and Shift-Left
security specifications (ADR-0004).
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

import httpx

from riftpilot.adapters.lcu_discovery import LcuCredentials, LcuDiscoverer
from riftpilot.adapters.lcu_stream import LcuEventStream
from riftpilot.core.lcu_port import (
    GameflowPhase,
    ILcuPort,
    LcuConnectionStatus,
)

logger = logging.getLogger("RiftPilot.RiotLCU")


class RiotLcuAdapter(ILcuPort):
    """Outbound adapter for the Riot League Client (LCU) REST API via HTTPX."""

    def __init__(
        self,
        credentials: LcuCredentials | None = None,
        discoverer: Any | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 5.0,
    ) -> None:
        """Initialize the Riot LCU adapter.

        Args:
            credentials: Explicit LCU credentials, or None to trigger discovery.
            discoverer: Discovery service or class providing a `.discover()` method or callable.
            transport: Optional custom HTTPX transport (used for testing and mocking).
            timeout: Default timeout in seconds for HTTP requests.
        """
        self._discoverer = discoverer if discoverer is not None else LcuDiscoverer
        self._transport = transport
        self._timeout = timeout
        self._credentials: LcuCredentials | None = None
        self._status: LcuConnectionStatus = LcuConnectionStatus.DISCONNECTED
        self._client: httpx.AsyncClient | None = None
        self._stream: LcuEventStream | None = None
        self._subscribers: list[Callable[[GameflowPhase], Any]] = []

        if credentials is not None:
            self._set_connected(credentials)
        else:
            discovered = self._discover_credentials()
            if discovered is not None:
                self._set_connected(discovered)
            else:
                self._status = LcuConnectionStatus.DISCONNECTED

    def _discover_credentials(self) -> LcuCredentials | None:
        """Attempt to discover LCU credentials via the configured discoverer."""
        if self._discoverer is None:
            return None
        try:
            if hasattr(self._discoverer, "discover") and callable(self._discoverer.discover):
                return self._discoverer.discover()
            elif callable(self._discoverer):
                return self._discoverer()
        except Exception as exc:
            logger.debug("LCU credentials discovery failed: %s", exc)
            return None
        return None

    def _set_connected(self, credentials: LcuCredentials) -> None:
        """Configure internal HTTPX client and WebSocket event stream with credentials."""
        self._credentials = credentials
        self._status = LcuConnectionStatus.CONNECTED

        client_kwargs: dict[str, Any] = {
            "base_url": credentials.base_url,
            "headers": {
                "Authorization": credentials.basic_auth_header,
                "Accept": "application/json",
            },
            "verify": False,
            "timeout": self._timeout,
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        client = httpx.AsyncClient(**client_kwargs)
        setattr(client, "verify", False)
        self._client = client

        self._stream = LcuEventStream(
            credentials=credentials,
            on_phase_change=self._on_stream_phase_change,
        )

    @property
    def credentials(self) -> LcuCredentials | None:
        """Return the current LCU credentials if connected, else None."""
        return self._credentials if self._status == LcuConnectionStatus.CONNECTED else None

    @property
    def stream(self) -> LcuEventStream | None:
        """Return the active LCU event stream instance, if available."""
        return self._stream

    async def _on_stream_phase_change(self, phase: GameflowPhase) -> None:
        """Internal callback invoked by LcuEventStream when a phase transition occurs."""
        for subscriber in list(self._subscribers):
            try:
                res = subscriber(phase)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                logger.warning("Error in phase subscriber: %s", exc)

    async def start_stream(self) -> bool:
        """Start the real-time WebSocket event stream if credentials are configured.

        Returns:
            True if the stream connected and started listening, False otherwise.
        """
        if self._stream is not None:
            return await self._stream.start()
        return False

    async def ensure_stream_started(self) -> bool:
        """Ensure WebSocket event stream is running if connected."""
        if self._status == LcuConnectionStatus.CONNECTED and self._stream is not None:
            if not self._stream.is_connected:
                return await self._stream.start()
            return True
        return False

    async def connect(self) -> bool:
        """Attempt to discover running League Client instance and connect.

        Returns:
            True if connection was established, False otherwise.
        """
        creds = self._discover_credentials()
        if creds is not None:
            if self._client is not None and not self._client.is_closed:
                await self._client.aclose()
            if self._stream is not None:
                await self._stream.stop()
            self._set_connected(creds)
            if self._stream is not None:
                await self._stream.start()
            return True

        self._status = LcuConnectionStatus.DISCONNECTED
        return False

    async def get_connection_status(self) -> LcuConnectionStatus:
        """Return the current connection status of the LCU interface."""
        return self._status

    async def get_gameflow_phase(self) -> GameflowPhase:
        """Return the active gameflow phase of the League Client.

        Queries GET /lol-gameflow/v1/gameflow-phase.

        Returns:
            Active GameflowPhase enum value, or GameflowPhase.NONE on any error or disconnection.
        """
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return GameflowPhase.NONE

        try:
            response = await self._client.get("/lol-gameflow/v1/gameflow-phase")
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning("LCU connection failed during get_gameflow_phase: %s", exc)
            self._status = LcuConnectionStatus.DISCONNECTED
            return GameflowPhase.NONE
        except Exception as exc:
            logger.warning("Unexpected error during get_gameflow_phase: %s", exc)
            return GameflowPhase.NONE

        if response.status_code in (401, 403):
            logger.warning("LCU authentication failed (status %d)", response.status_code)
            self._status = LcuConnectionStatus.AUTHENTICATION_FAILED
            return GameflowPhase.NONE

        if response.status_code != 200:
            logger.debug("LCU returned non-200 status %d for gameflow-phase", response.status_code)
            return GameflowPhase.NONE

        try:
            data = response.json()
        except Exception:
            return GameflowPhase.NONE

        if not isinstance(data, str) or not data.strip():
            return GameflowPhase.NONE

        phase_str = data.strip()
        try:
            return GameflowPhase(phase_str)
        except ValueError:
            logger.debug("Unknown gameflow phase received from LCU: %r", phase_str)
            return GameflowPhase.NONE

    async def accept_ready_check(self) -> bool:
        """Accept an active ready check prompt on the League Client.

        Sends POST /lol-matchmaking/v1/ready-check/accept.

        Returns:
            True if the prompt was successfully accepted (HTTP 200/204), False otherwise.
        """
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return False

        try:
            response = await self._client.post("/lol-matchmaking/v1/ready-check/accept")
        except httpx.ConnectError as exc:
            logger.warning("LCU connection dropped during accept_ready_check: %s", exc)
            self._status = LcuConnectionStatus.DISCONNECTED
            return False
        except httpx.TimeoutException as exc:
            logger.warning("LCU request timed out during accept_ready_check: %s", exc)
            return False
        except Exception as exc:
            logger.warning("Unexpected error during accept_ready_check: %s", exc)
            return False

        if response.status_code in (200, 204):
            return True

        if response.status_code in (401, 403):
            logger.warning("LCU authentication failed during accept_ready_check (status %d)", response.status_code)
            self._status = LcuConnectionStatus.AUTHENTICATION_FAILED
            return False

        return False

    def subscribe_phase_change(
        self, callback: Callable[[GameflowPhase], Any]
    ) -> None:
        """Register a callback for gameflow phase transition events."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe_phase_change(
        self, callback: Callable[[GameflowPhase], Any]
    ) -> bool:
        """Unregister an existing phase change callback.

        Returns:
            True if the callback was registered and removed, False otherwise.
        """
        if callback in self._subscribers:
            self._subscribers.remove(callback)
            return True
        return False

    async def close(self) -> None:
        """Close internal HTTPX client and WebSocket stream, releasing resources."""
        self._status = LcuConnectionStatus.DISCONNECTED
        self._credentials = None
        if self._stream is not None:
            await self._stream.stop()
            self._stream = None
        if self._client is not None:
            if not self._client.is_closed:
                await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> RiotLcuAdapter:
        """Async context manager entry."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Async context manager exit, ensuring proper cleanup."""
        await self.close()


__all__ = ["RiotLcuAdapter"]
