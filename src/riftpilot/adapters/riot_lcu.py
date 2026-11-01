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
from riftpilot.core.champ_select import ChampSelectSession, ChampionSummary
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
        self._champ_select_subscribers: list[
            Callable[[ChampSelectSession | None], Any]
        ] = []

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
        self._stream.add_champ_select_callback(self._on_stream_champ_select_change)

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

    async def _on_stream_champ_select_change(
        self, session: ChampSelectSession | None
    ) -> None:
        """Internal callback invoked by LcuEventStream when champ select session updates."""
        for subscriber in list(self._champ_select_subscribers):
            try:
                res = subscriber(session)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                logger.warning("Error in champ select subscriber: %s", exc)

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

    # ========================================================================
    # ILcuPort Implementation — Champion Select
    # ========================================================================

    async def get_champ_select_session(self) -> ChampSelectSession | None:
        """Return current champion select session, or None if inactive."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return None

        try:
            response = await self._client.get("/lol-champ-select/v1/session")
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning("LCU connection failed during get_champ_select_session: %s", exc)
            self._status = LcuConnectionStatus.DISCONNECTED
            return None
        except Exception as exc:
            logger.warning("Unexpected error during get_champ_select_session: %s", exc)
            return None

        if response.status_code in (401, 403):
            logger.warning("LCU authentication failed (status %d)", response.status_code)
            self._status = LcuConnectionStatus.AUTHENTICATION_FAILED
            return None

        if response.status_code == 404:
            return None

        if response.status_code != 200:
            logger.debug("LCU returned non-200 status %d for champ select session", response.status_code)
            return None

        try:
            data = response.json()
            if isinstance(data, dict):
                return ChampSelectSession.from_lcu_dict(data)
        except Exception as exc:
            logger.debug("Failed to parse champ select session response: %s", exc)

        return None

    async def patch_champ_select_action(
        self,
        action_id: int,
        champion_id: int,
        completed: bool = True,
    ) -> bool:
        """Perform hover or lock-in for a pick or ban action.

        Sends PATCH /lol-champ-select/v1/session/actions/{action_id}.
        If completed=True, additionally calls POST /lol-champ-select/v1/session/actions/{action_id}/complete.
        If completed=False, also updates /lol-champ-select/v1/session/my-selection pick intent.

        Returns:
            True if the action succeeded (HTTP 200/204), False otherwise.
        """
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return False

        # Attempt to inspect current session to enrich payload and resolve action ID
        target_action = None
        try:
            session = await self.get_champ_select_session()
            if session is not None:
                if action_id > 0:
                    target_action = session.find_action(action_id)
                if target_action is None:
                    target_action = session.get_active_action()
                    if target_action is not None:
                        action_id = target_action.id
        except Exception as exc:
            logger.debug("Session inspection error in patch_champ_select_action: %s", exc)

        payload: dict[str, Any] = {
            "championId": champion_id,
            "completed": completed,
        }
        if target_action is not None:
            payload["actorCellId"] = target_action.actor_cell_id
            payload["type"] = target_action.type
            payload["id"] = target_action.id
            payload["isAllyAction"] = True
            payload["isInProgress"] = True

        success = False
        try:
            response = await self._client.patch(
                f"/lol-champ-select/v1/session/actions/{action_id}",
                json=payload,
            )
            if response.status_code in (200, 204):
                success = True
            elif response.status_code in (401, 403):
                logger.warning("LCU authentication failed during patch_champ_select_action: %d", response.status_code)
                self._status = LcuConnectionStatus.AUTHENTICATION_FAILED
                return False
            else:
                logger.warning(
                    "LCU patch action %d failed with status %d: %s",
                    action_id,
                    response.status_code,
                    response.text,
                )
        except httpx.ConnectError as exc:
            logger.warning("LCU connection dropped during patch_champ_select_action: %s", exc)
            self._status = LcuConnectionStatus.DISCONNECTED
            return False
        except httpx.TimeoutException as exc:
            logger.warning("LCU request timed out during patch_champ_select_action: %s", exc)
            return False
        except Exception as exc:
            logger.warning("Unexpected error during patch_champ_select_action: %s", exc)
            return False

        # If hover (completed=False), also send pick intent to my-selection
        if not completed and champion_id > 0:
            try:
                intent_resp = await self._client.patch(
                    "/lol-champ-select/v1/session/my-selection",
                    json={"championPickIntent": champion_id},
                )
                if intent_resp.status_code in (200, 204):
                    success = True
            except Exception as exc:
                logger.debug("Failed to set pick intent via my-selection: %s", exc)

        # If lock-in (completed=True), also trigger dedicated action completion endpoint
        if completed and action_id > 0:
            try:
                complete_resp = await self._client.post(
                    f"/lol-champ-select/v1/session/actions/{action_id}/complete"
                )
                if complete_resp.status_code in (200, 204):
                    success = True
            except Exception as exc:
                logger.debug("POST complete endpoint error: %s", exc)

        return success

    async def get_grid_champions(self) -> list[ChampionSummary]:
        """Return available/grid champions.

        Sends GET /lol-champ-select/v1/all-grid-champions.

        Returns:
            List of ChampionSummary models, or empty list on failure.
        """
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return []

        try:
            response = await self._client.get("/lol-champ-select/v1/all-grid-champions")
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning("LCU connection failed during get_grid_champions: %s", exc)
            self._status = LcuConnectionStatus.DISCONNECTED
            return []
        except Exception as exc:
            logger.warning("Unexpected error during get_grid_champions: %s", exc)
            return []

        if response.status_code in (401, 403):
            logger.warning("LCU authentication failed (status %d)", response.status_code)
            self._status = LcuConnectionStatus.AUTHENTICATION_FAILED
            return []

        if response.status_code != 200:
            return []

        try:
            data = response.json()
            if isinstance(data, list):
                return [
                    ChampionSummary.from_lcu_dict(item)
                    for item in data
                    if isinstance(item, dict)
                ]
        except Exception as exc:
            logger.debug("Failed to parse all-grid-champions response: %s", exc)

        return []

    def subscribe_champ_select(
        self, callback: Callable[[ChampSelectSession | None], Any]
    ) -> None:
        """Register a callback for champion select session state updates."""
        if callback not in self._champ_select_subscribers:
            self._champ_select_subscribers.append(callback)

    def unsubscribe_champ_select(
        self, callback: Callable[[ChampSelectSession | None], Any]
    ) -> bool:
        """Unregister an existing champion select session callback.

        Returns:
            True if the callback was registered and removed, False otherwise.
        """
        if callback in self._champ_select_subscribers:
            self._champ_select_subscribers.remove(callback)
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
