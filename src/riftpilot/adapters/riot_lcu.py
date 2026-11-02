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
from riftpilot.core.game_mode import GameModeContext
from riftpilot.core.lcu_port import (
    GameflowPhase,
    ILcuPort,
    LcuConnectionStatus,
)
from riftpilot.core.post_game import EndOfGameStats, HonorBallot
from riftpilot.core.lobby import (
    BenchChampion,
    ChampSelectTrade,
    LobbyData,
    MatchmakingSearchState,
)
from riftpilot.core.runes_spells import (
    RecommendedRunePage,
    RunePage,
    SummonerSpell,
    get_spells_for_mode,
)

logger = logging.getLogger("RiftPilot.RiotLCU")


def _normalize_lcu_position(position: str) -> str:
    """Normalize position string to LCU recommender format (TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY, NONE)."""
    pos = (position or "").strip().upper()
    mapping = {
        "TOP": "TOP",
        "JUNGLE": "JUNGLE",
        "MID": "MIDDLE",
        "MIDDLE": "MIDDLE",
        "BOT": "BOTTOM",
        "BOTTOM": "BOTTOM",
        "ADC": "BOTTOM",
        "SUP": "UTILITY",
        "SUPPORT": "UTILITY",
        "UTILITY": "UTILITY",
    }
    return mapping.get(pos, "NONE")


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
        self._lobby_subscribers: list[Callable[[LobbyData | None], Any]] = []
        self._eog_subscribers: list[Callable[[EndOfGameStats | None], Any]] = []
        self._cached_spells: list[SummonerSpell] | None = None

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
        self._stream.add_lobby_callback(self._on_stream_lobby_change)

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

    async def _on_stream_lobby_change(
        self, lobby: LobbyData | None
    ) -> None:
        """Internal callback invoked by LcuEventStream when lobby updates."""
        for subscriber in list(self._lobby_subscribers):
            try:
                res = subscriber(lobby)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                logger.warning("Error in lobby subscriber: %s", exc)

    async def start_stream(self) -> bool:
        """Start the real-time WebSocket event stream if credentials are configured."""
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
        """Attempt to discover running League Client instance and connect."""
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

    async def get_game_mode_context(self) -> GameModeContext:
        """Retrieve active game mode, queue ID, and map metadata from `/lol-gameflow/v1/session`."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return GameModeContext(game_mode="CLASSIC", queue_id=420, map_id=11)

        try:
            response = await self._client.get("/lol-gameflow/v1/session")
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, dict):
                    return GameModeContext.from_lcu_gameflow_session(data)
        except Exception as exc:
            logger.debug("Failed to fetch gameflow session context: %s", exc)

        return GameModeContext(game_mode="CLASSIC", queue_id=420, map_id=11)

    async def accept_ready_check(self) -> bool:
        """Accept an active ready check prompt on the League Client."""
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
        """Unregister an existing phase change callback."""
        if callback in self._subscribers:
            self._subscribers.remove(callback)
            return True
        return False

    # ========================================================================
    # ILcuPort Implementation — Champion Select
    # ========================================================================

    # ========================================================================
    # Lobby & Matchmaking Implementation (Milestone 4)
    # ========================================================================

    async def get_lobby(self) -> LobbyData | None:
        """Retrieve active party lobby data from LCU."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return None
        try:
            resp = await self._client.get("/lol-lobby/v2/lobby")
            if resp.status_code == 200:
                data = resp.json()
                return LobbyData.from_lcu_dict(data)
            return None
        except Exception as exc:
            logger.debug("Failed to get lobby data: %s", exc)
            return None

    async def start_matchmaking(self) -> bool:
        """Initiate matchmaking search for the active party lobby."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return False
        try:
            resp = await self._client.post("/lol-lobby/v2/lobby/matchmaking/search")
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Failed to start matchmaking: %s", exc)
            return False

    async def cancel_matchmaking(self) -> bool:
        """Cancel ongoing matchmaking search."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return False
        try:
            resp = await self._client.delete("/lol-lobby/v2/lobby/matchmaking/search")
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Failed to cancel matchmaking: %s", exc)
            return False

    async def set_position_preferences(self, first: str, second: str) -> bool:
        """Update role preferences for the local member."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return False
        try:
            payload = {
                "firstPreference": first.upper(),
                "secondPreference": second.upper(),
            }
            resp = await self._client.put(
                "/lol-lobby/v2/lobby/members/localMember/position-preferences",
                json=payload,
            )
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Failed to set position preferences: %s", exc)
            return False

    async def get_matchmaking_search(self) -> MatchmakingSearchState | None:
        """Retrieve real-time matchmaking queue search status."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return None
        try:
            resp = await self._client.get("/lol-matchmaking/v1/search")
            if resp.status_code == 200:
                data = resp.json()
                return MatchmakingSearchState.from_lcu_dict(data)
            return None
        except Exception as exc:
            logger.debug("Failed to get matchmaking search state: %s", exc)
            return None

    def subscribe_lobby(
        self, callback: Callable[[LobbyData | None], Any]
    ) -> None:
        """Register a callback for party lobby updates."""
        if callback not in self._lobby_subscribers:
            self._lobby_subscribers.append(callback)

    def unsubscribe_lobby(
        self, callback: Callable[[LobbyData | None], Any]
    ) -> bool:
        """Unregister an existing lobby update callback."""
        if callback in self._lobby_subscribers:
            self._lobby_subscribers.remove(callback)
            return True
        return False

    # ========================================================================
    # ARAM Bench & Trades Implementation (Milestone 4)
    # ========================================================================

    async def bench_swap(self, champion_id: int) -> bool:
        """Swap current champion with one from the ARAM bench."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return False
        try:
            resp = await self._client.post(
                f"/lol-champ-select/v1/session/bench/swap/{champion_id}"
            )
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Failed to swap bench champion %d: %s", champion_id, exc)
            return False

    async def reroll(self) -> bool:
        """Reroll current champion in ARAM."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return False
        try:
            resp = await self._client.post(
                "/lol-champ-select/v1/session/my-selection/reroll"
            )
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Failed to reroll champion: %s", exc)
            return False

    async def get_bench_champions(self) -> list[BenchChampion]:
        """Return list of champions available on the bench."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return []
        try:
            resp = await self._client.get("/lol-champ-select/v1/session")
            if resp.status_code != 200:
                return []
            data = resp.json()
            raw_bench = data.get("benchChampions", [])
            bench: list[BenchChampion] = []
            if isinstance(raw_bench, list):
                for b in raw_bench:
                    if isinstance(b, dict):
                        c_id = b.get("championId")
                        if c_id:
                            bench.append(BenchChampion(champion_id=int(c_id)))
            return bench
        except Exception as exc:
            logger.debug("Failed to get bench champions: %s", exc)
            return []

    async def get_trades(self) -> list[ChampSelectTrade]:
        """Return current champion select trades."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return []
        try:
            resp = await self._client.get("/lol-champ-select/v1/session/trades")
            if resp.status_code != 200:
                return []
            raw_trades = resp.json()
            trades: list[ChampSelectTrade] = []
            if isinstance(raw_trades, list):
                for t in raw_trades:
                    parsed = ChampSelectTrade.from_lcu_dict(t)
                    if parsed:
                        trades.append(parsed)
            return trades
        except Exception as exc:
            logger.debug("Failed to get trades: %s", exc)
            return []

    async def request_trade(self, trade_id: int) -> bool:
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return False
        try:
            resp = await self._client.post(
                f"/lol-champ-select/v1/session/trades/{trade_id}/request"
            )
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Failed to request trade %d: %s", trade_id, exc)
            return False

    async def accept_trade(self, trade_id: int) -> bool:
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return False
        try:
            resp = await self._client.post(
                f"/lol-champ-select/v1/session/trades/{trade_id}/accept"
            )
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Failed to accept trade %d: %s", trade_id, exc)
            return False

    async def decline_trade(self, trade_id: int) -> bool:
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return False
        try:
            resp = await self._client.post(
                f"/lol-champ-select/v1/session/trades/{trade_id}/decline"
            )
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Failed to decline trade %d: %s", trade_id, exc)
            return False

    # ========================================================================
    # Post-Game Stats & Honor Implementation (Milestone 5)
    # ========================================================================

    async def get_end_of_game_stats(self) -> EndOfGameStats | None:
        """Retrieve match summary and player statistics from `/lol-end-of-game/v1/eog-stats-block`."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return None
        try:
            resp = await self._client.get("/lol-end-of-game/v1/eog-stats-block")
            if resp.status_code == 200:
                data = resp.json()
                return EndOfGameStats.from_lcu_dict(data)
            return None
        except Exception as exc:
            logger.debug("Failed to get end of game stats: %s", exc)
            return None

    async def get_honor_ballot(self) -> HonorBallot | None:
        """Retrieve eligible teammates for post-game honor voting."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return None
        try:
            resp = await self._client.get("/lol-honor-v2/v1/ballot")
            if resp.status_code == 200:
                data = resp.json()
                return HonorBallot.from_lcu_dict(data)
            return None
        except Exception as exc:
            logger.debug("Failed to get honor ballot: %s", exc)
            return None

    async def honor_player(self, summoner_id: int, honor_type: str) -> bool:
        """Cast honor vote for a teammate."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return False
        try:
            payload = {
                "honorCategory": honor_type.upper(),
                "summonerId": summoner_id,
            }
            resp = await self._client.post("/lol-honor-v2/v1/honor-player", json=payload)
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Failed to honor player %d (%s): %s", summoner_id, honor_type, exc)
            return False

    def subscribe_end_of_game(
        self, callback: Callable[[EndOfGameStats | None], Any]
    ) -> None:
        """Register a callback for post-game stats updates."""
        if callback not in self._eog_subscribers:
            self._eog_subscribers.append(callback)

    def unsubscribe_end_of_game(
        self, callback: Callable[[EndOfGameStats | None], Any]
    ) -> bool:
        """Unregister a post-game stats update callback."""
        if callback in self._eog_subscribers:
            self._eog_subscribers.remove(callback)
            return True
        return False

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
        """Return available/grid champions via GET /lol-champ-select/v1/all-grid-champions."""
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
        """Unregister an existing champion select session callback."""
        if callback in self._champ_select_subscribers:
            self._champ_select_subscribers.remove(callback)
            return True
        return False

    # ========================================================================
    # ILcuPort Implementation — Runes & Summoner Spells
    # ========================================================================

    async def get_available_summoner_spells(
        self, game_mode: str | None = None
    ) -> list[SummonerSpell]:
        """Return summoner spells valid for the given or active game mode.

        Queries `/lol-game-data/assets/v1/summoner-spells.json` when available,
        and filters by `gameModes`. Falls back to mode-filtered `STANDARD_SPELLS`
        if the asset endpoint is unavailable.
        """
        effective_mode = game_mode
        if not effective_mode and self._status == LcuConnectionStatus.CONNECTED:
            ctx = await self.get_game_mode_context()
            effective_mode = ctx.game_mode

        catalog: list[SummonerSpell] | None = self._cached_spells
        if catalog is None and self._status == LcuConnectionStatus.CONNECTED and self._client is not None:
            try:
                resp = await self._client.get("/lol-game-data/assets/v1/summoner-spells.json")
                if resp.status_code == 200:
                    raw_list = resp.json()
                    if isinstance(raw_list, list):
                        parsed: list[SummonerSpell] = []
                        for entry in raw_list:
                            if isinstance(entry, dict):
                                sp = SummonerSpell.from_lcu_dict(entry)
                                # Exclude placeholder/empty spells with no gameModes or negative/zero IDs
                                if sp.id > 0 and sp.game_modes:
                                    parsed.append(sp)
                        if parsed:
                            self._cached_spells = parsed
                            catalog = parsed
            except Exception as exc:
                logger.debug("Could not fetch summoner-spells.json from LCU: %s", exc)

        return get_spells_for_mode(effective_mode or "CLASSIC", catalog=catalog)

    async def set_summoner_spells(self, spell1_id: int, spell2_id: int) -> bool:
        """Assign D and F summoner spells during champion select.

        Sends PATCH /lol-champ-select/v1/session/my-selection.
        """
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return False

        payload = {
            "spell1Id": spell1_id,
            "spell2Id": spell2_id,
        }

        try:
            response = await self._client.patch(
                "/lol-champ-select/v1/session/my-selection",
                json=payload,
            )
            return response.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Failed to assign summoner spells: %s", exc)
            return False

    async def get_current_rune_page(self) -> RunePage | None:
        """Fetch current active rune page from LCU."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return None

        try:
            response = await self._client.get("/lol-perks/v1/currentpage")
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, dict):
                    return RunePage.from_lcu_dict(data)
        except Exception as exc:
            logger.debug("Failed to get current rune page: %s", exc)
        return None

    async def apply_rune_page(self, page: RunePage) -> bool:
        """Apply rune page by updating an existing editable page or creating one."""
        if self._status != LcuConnectionStatus.CONNECTED or self._client is None:
            return False

        lcu_payload = page.to_lcu_payload()

        try:
            # 1. Check existing rune pages
            pages_resp = await self._client.get("/lol-perks/v1/pages")
            target_page_id = None
            if pages_resp.status_code == 200:
                pages_data = pages_resp.json()
                if isinstance(pages_data, list):
                    for p in pages_data:
                        if isinstance(p, dict) and p.get("isEditable"):
                            target_page_id = p.get("id")
                            break

            # 2. Update existing editable page if available
            if target_page_id:
                put_resp = await self._client.put(
                    f"/lol-perks/v1/pages/{target_page_id}",
                    json=lcu_payload,
                )
                if put_resp.status_code in (200, 204):
                    return True

            # 3. Otherwise create new page
            post_resp = await self._client.post(
                "/lol-perks/v1/pages",
                json=lcu_payload,
            )
            return post_resp.status_code in (200, 201, 204)

        except Exception as exc:
            logger.warning("Failed to apply rune page: %s", exc)
            return False

    async def get_recommended_rune_pages(
        self,
        champion_id: int,
        position: str = "",
        map_id: int = 11,
    ) -> list[RecommendedRunePage]:
        """Fetch the client's official recommended rune pages for the given champion.

        Queries `GET /lol-perks/v1/recommended-pages/champion/{championId}/position/{position}/map/{mapId}`.
        If empty for a specific lane, falls back to `position=NONE` on the same map.
        Never fabricates or substitutes fake rune pages.
        """
        if (
            self._status != LcuConnectionStatus.CONNECTED
            or self._client is None
            or champion_id <= 0
        ):
            return []

        norm_pos = _normalize_lcu_position(position)
        eff_map = map_id if map_id > 0 else 11
        positions_to_try = [norm_pos]
        if norm_pos != "NONE":
            positions_to_try.append("NONE")

        for pos_candidate in positions_to_try:
            endpoint = (
                f"/lol-perks/v1/recommended-pages/champion/{champion_id}"
                f"/position/{pos_candidate}/map/{eff_map}"
            )
            try:
                resp = await self._client.get(endpoint)
            except Exception as exc:
                logger.debug("Failed to query recommended rune pages (%s): %s", endpoint, exc)
                continue

            if resp.status_code != 200:
                continue

            try:
                raw_data = resp.json()
            except Exception:
                continue

            if not isinstance(raw_data, list) or not raw_data:
                continue

            pages: list[RecommendedRunePage] = []
            for idx, item in enumerate(raw_data[:3]):
                if isinstance(item, dict):
                    parsed = RecommendedRunePage.from_lcu_dict(
                        item, champion_id=champion_id, index=idx
                    )
                    if parsed is not None:
                        pages.append(parsed)

            if pages:
                return pages

        return []

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
