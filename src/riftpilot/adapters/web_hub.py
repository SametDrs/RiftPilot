"""RiftPilot Adapters — Mobile Web Hub & Pairing Gateway (FastAPI).

Provides the LAN HTTP and WebSocket server for mobile clients:
- Serves the Hextech-themed responsive mobile web client (`GET /`).
- Enforces single-use cryptographic pairing via One-Time Tokens (`POST /api/pair`).
- Establishes authenticated, zero-trust bidirectional WebSockets (`GET /ws`).
- Broadcasts real-time LCU gameflow events and processes remote commands
  such as match acceptance (`ACCEPT_READY_CHECK`).
- Provides Mode-Aware Champion Select remote orchestration:
  - Central state model (`modeContext`, `eligibility`, `session`) shared across LCU/Desktop/Mobile.
  - Game-mode-filtered summoner spells (e.g., ARAM vs Classic) with server validation.
  - Official 3 recommended rune pages from LCU (never fabricated or auto-applied).
  - Mode-specific UI layouts (Ranked/Draft, Blind Pick, ARAM/ARAM Şamata, Safe Generic).
  - Strict server-side re-verification before every pick, ban, spell, or rune command.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import inspect
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Set

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from riftpilot.core.champ_select import ChampSelectSession, ChampionSummary
from riftpilot.core.game_mode import (
    GameModeContext,
    compute_champ_select_stage_and_eligibility,
    validate_champ_select_command,
)
from riftpilot.core.lcu_port import (
    GameflowPhase,
    ILcuPort,
    LcuConnectionStatus,
)
from riftpilot.core.lobby import (
    DRAFT_QUEUE_IDS,
    NON_DRAFT_QUEUE_IDS,
    PositionPreference,
    is_lane_selection_supported,
    resolve_lane_preferences,
)
from riftpilot.core.pairing import DeviceSession, OTTManager, SessionManager
from riftpilot.core.transport import ITunnelPort, TunnelInfo, TunnelStatus
from riftpilot.core.runes_spells import (
    RUNE_DATA,
    CustomRuneManager,
    CustomRunePage,
    RunePage,
    SummonerSpell,
    custom_rune_manager,
    get_custom_rune_manager,
    get_spells_for_mode,
)

logger = logging.getLogger("RiftPilot.WebHub")

_STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML: str = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")


class PairRequest(BaseModel):
    """Payload schema for mobile device pairing requests."""

    ott: str = Field(..., min_length=1, description="Single-use One-Time Token")
    deviceName: str = Field(..., min_length=1, description="Connecting device name")
    clientInstanceId: str | None = Field(
        default=None, description="Optional client instance identifier"
    )


class ChampSelectActionRequest(BaseModel):
    """Payload schema for performing remote champion select actions."""

    actionId: int = Field(default=0, description="Action ID to hover or lock in (0 to auto-resolve)")
    championId: int = Field(..., description="Champion ID to select")
    completed: bool = Field(default=True, description="True to lock in, False to hover")


class SetSpellsRequest(BaseModel):
    """Payload schema for updating summoner spells."""

    spell1Id: int = Field(..., description="Spell 1 (D) numeric ID")
    spell2Id: int = Field(..., description="Spell 2 (F) numeric ID")



class SetPositionPreferencesRequest(BaseModel):
    first: str | None = Field(default=None, description="First position preference (TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY, FILL, UNSELECTED)")
    firstPreference: str | None = Field(default=None, description="Alias for first position preference")
    secondPreference: str | None = Field(default=None, description="Alias for second position preference")
    queueId: int | None = Field(default=None, description="Optional queue ID context")
    second: str | None = Field(default=None, description="Second position preference")



class HonorPlayerRequest(BaseModel):
    summonerId: int = Field(..., gt=0, description="Summoner ID to honor")
    honorCategory: str = Field(..., description="HEART, SHOTCALLER, or COOL")

class BenchSwapRequest(BaseModel):
    championId: int = Field(..., gt=0, description="Champion ID on the bench to swap with")

class ApplyRunePageRequest(BaseModel):
    """Payload schema for applying a user-selected rune page."""

    name: str = Field(default="RiftPilot", description="Rune page name")
    primaryStyleId: int = Field(..., gt=0, description="Primary tree ID")
    subStyleId: int = Field(..., gt=0, description="Secondary tree ID")
    selectedPerkIds: list[int] = Field(
        ..., min_length=1, description="Selected perk IDs"
    )


class CustomRuneCreateRequest(BaseModel):
    """Payload schema for creating and saving a custom rune page."""

    name: str = Field(..., min_length=1, max_length=64, description="Rune page name")
    primaryStyleId: int = Field(..., gt=0, description="Primary tree ID")
    subStyleId: int = Field(..., gt=0, description="Secondary tree ID")
    selectedPerkIds: list[int] = Field(..., min_length=9, max_length=9, description="9 perk IDs")



class CreateLobbyRequest(BaseModel):
    queueId: int = Field(..., description="Target queue ID (e.g. 420 for Solo/Duo, 440 for Flex, 450 for ARAM)")


class StartMatchmakingRequest(BaseModel):
    queueId: int | None = Field(default=None, description="Optional target queue ID")
    firstPreference: str | None = Field(default=None, description="First position preference")
    secondPreference: str | None = Field(default=None, description="Second position preference")
    first: str | None = Field(default=None, description="Alias for firstPreference")
    second: str | None = Field(default=None, description="Alias for secondPreference")


class TradeActionRequest(BaseModel):
    tradeId: int = Field(..., description="Trade entity ID")


class LaneSwapActionRequest(BaseModel):
    cellId: int = Field(..., description="Target or sender cell ID")


DEFAULT_CHAMPION_CATALOG: list[ChampionSummary] = [
    ChampionSummary(id=64, name="Lee Sin", roles=["fighter", "assassin"]),
    ChampionSummary(id=84, name="Akali", roles=["assassin"]),
    ChampionSummary(id=103, name="Ahri", roles=["mage", "assassin"]),
    ChampionSummary(id=145, name="Kai'Sa", roles=["marksman", "assassin"]),
    ChampionSummary(id=157, name="Yasuo", roles=["fighter", "assassin"]),
    ChampionSummary(id=221, name="Zeri", roles=["marksman"]),
    ChampionSummary(id=222, name="Jinx", roles=["marksman"]),
    ChampionSummary(id=234, name="Viego", roles=["fighter", "assassin"]),
    ChampionSummary(id=236, name="Lucian", roles=["marksman"]),
    ChampionSummary(id=238, name="Zed", roles=["assassin"]),
    ChampionSummary(id=266, name="Aatrox", roles=["fighter", "tank"]),
    ChampionSummary(id=268, name="Azir", roles=["mage"]),
    ChampionSummary(id=360, name="Samira", roles=["marksman", "assassin"]),
    ChampionSummary(id=412, name="Thresh", roles=["support", "tank"]),
    ChampionSummary(id=517, name="Sylas", roles=["mage", "assassin"]),
    ChampionSummary(id=523, name="Aphelios", roles=["marksman"]),
    ChampionSummary(id=555, name="Pyke", roles=["support", "assassin"]),
    ChampionSummary(id=711, name="Vex", roles=["mage"]),
    ChampionSummary(id=777, name="Yone", roles=["fighter", "assassin"]),
    ChampionSummary(id=799, name="Ambessa", roles=["fighter", "assassin"]),
    ChampionSummary(id=875, name="Sett", roles=["fighter", "tank"]),
    ChampionSummary(id=887, name="Gwen", roles=["fighter", "mage"]),
    ChampionSummary(id=897, name="K'Sante", roles=["tank", "fighter"]),
    ChampionSummary(id=901, name="Smolder", roles=["marksman", "mage"]),
    ChampionSummary(id=910, name="Hwei", roles=["mage"]),
]


async def _perform_accept_ready_check(adapter: Any) -> bool:
    """Accept an active ready check prompt on the League Client or mock adapter."""
    if adapter is None:
        return False
    if hasattr(adapter, "accept_ready_check"):
        res = adapter.accept_ready_check()
        if inspect.iscoroutine(res):
            return bool(await res)
        return bool(res)
    client = getattr(adapter, "_client", None)
    if client is not None:
        try:
            resp = await client.post("/lol-matchmaking/v1/ready-check/accept")
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.warning("Error accepting ready check via LCU client: %s", exc)
    return False


async def _perform_decline_ready_check(adapter: Any) -> bool:
    """Decline an active ready check prompt on the League Client or mock adapter."""
    if adapter is None:
        return False

    # 1. Adapter direct method if implemented
    if hasattr(adapter, "decline_ready_check"):
        res = adapter.decline_ready_check()
        if inspect.iscoroutine(res):
            return bool(await res)
        return bool(res)

    # 2. Riot LCU HTTP client fallback
    client = getattr(adapter, "_client", None)
    if client is not None:
        try:
            resp = await client.post("/lol-matchmaking/v1/ready-check/decline")
            if resp.status_code in (200, 204):
                return True
        except Exception as exc:
            logger.warning("Error declining ready check via LCU client: %s", exc)

    # 3. Mock LCU adapter simulation fallback
    status = getattr(adapter, "_status", None)
    if status is not None and getattr(status, "value", str(status)) == "Disconnected":
        logger.warning("decline_ready_check rejected: adapter is not connected.")
        return False

    updated = False
    if hasattr(adapter, "player_response"):
        adapter.player_response = "Declined"
        updated = True
    if hasattr(adapter, "ready_check_state"):
        adapter.ready_check_state = "Declined"
        updated = True
    if hasattr(adapter, "ready_check") and isinstance(adapter.ready_check, dict):
        adapter.ready_check["playerResponse"] = "Declined"
        adapter.ready_check["state"] = "Declined"
        updated = True

    if hasattr(adapter, "simulate_phase_change"):
        try:
            await adapter.simulate_phase_change(GameflowPhase.LOBBY)
        except Exception:
            pass

    return updated


#: WebSocket close code for missing/invalid/revoked sessions (ADR-0004).
WS_CLOSE_UNAUTHORIZED: int = 4401


def create_app(
    ott_manager: OTTManager,
    session_manager: SessionManager,
    lcu_adapter: ILcuPort | None = None,
    tunnel_adapter: ITunnelPort | None = None,
    ui_bridge: Any = None,
    enable_monitor: bool = True,
    poll_interval: float = 1.0,
    server_port: int | None = None,
    debug: bool = False,
) -> FastAPI:
    """Factory creating and configuring the FastAPI Web Hub application.

    Args:
        server_port: Local port the hub is served on; used by ``/api/wan/toggle``
            (client-supplied ports are ignored). Falls back to the port the
            request was received on.
        debug: Expose ``/docs``, ``/redoc`` and ``/openapi.json`` when True.
    """
    active_connections: Set[WebSocket] = set()
    # Session token + owning event loop per live WebSocket (ADR-0004 revocation).
    connection_sessions: dict[WebSocket, tuple[str, asyncio.AbstractEventLoop]] = {}
    in_flight_action_lock = asyncio.Lock()

    def _discard_connection(ws: WebSocket) -> None:
        active_connections.discard(ws)
        connection_sessions.pop(ws, None)

    async def _close_unauthorized(ws: WebSocket, reason: str = "Session revoked or invalid") -> None:
        """Notify the client and close its socket with 4401 (ADR-0004)."""
        _discard_connection(ws)
        try:
            await ws.send_json({"type": "REVOKED", "error": reason})
        except Exception:
            pass
        try:
            await ws.close(code=WS_CLOSE_UNAUTHORIZED)
        except Exception:
            pass

    def _on_session_revoked(revoked: DeviceSession) -> None:
        """Revocation listener; may run on the Qt thread, so hop onto each socket's loop."""
        for ws, (ws_token, loop) in list(connection_sessions.items()):
            if ws_token != revoked.session_token or loop.is_closed():
                continue
            try:
                asyncio.run_coroutine_threadsafe(_close_unauthorized(ws), loop)
            except RuntimeError as exc:
                logger.debug("Could not schedule WS revocation close: %s", exc)

    session_manager.add_revocation_listener(_on_session_revoked)

    async def _broadcast(payload: dict[str, Any]) -> None:
        """Send payload to every socket whose session is still valid; close the rest."""
        for ws in list(active_connections):
            entry = connection_sessions.get(ws)
            if entry is None or session_manager.get_session(entry[0]) is None:
                await _close_unauthorized(ws)
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                _discard_connection(ws)

    async def _build_enriched_champ_select_payload(
        session: ChampSelectSession | None,
    ) -> dict[str, Any]:
        """Build centralized Champ Select state combining session, modeContext, and eligibility."""
        if lcu_adapter is not None:
            mode_ctx = await lcu_adapter.get_game_mode_context()
            grid = await lcu_adapter.get_grid_champions()
            disabled_ids = {c.id for c in grid if c.disabled}
        else:
            mode_ctx = GameModeContext()
            disabled_ids = set()

        eligibility = compute_champ_select_stage_and_eligibility(
            session=session,
            mode_context=mode_ctx,
            disabled_champion_ids=disabled_ids,
        )

        session_dict: dict[str, Any] | None = None
        if session is not None:
            session_dict = session.to_dict()
            session_dict["modeContext"] = mode_ctx.to_dict()
            session_dict["eligibility"] = eligibility.to_dict()

        return {
            "session": session_dict,
            "modeContext": mode_ctx.to_dict(),
            "eligibility": eligibility.to_dict(),
        }

    async def broadcast_phase(
        phase: GameflowPhase | str, lcu_connected: bool | None = None
    ) -> None:
        """Broadcast live gameflow phase transition to all active WebSocket clients."""
        phase_str = phase.value if hasattr(phase, "value") else str(phase)
        payload: dict[str, Any] = {
            "type": "GAMEFLOW_UPDATE",
            "phase": phase_str,
        }
        if lcu_connected is not None:
            payload["lcu_connected"] = lcu_connected

        if phase_str == "Matchmaking" and lcu_adapter is not None:
            try:
                search_obj = await lcu_adapter.get_matchmaking_search()
                if search_obj:
                    payload["search"] = search_obj.model_dump()
            except Exception:
                pass

        await _broadcast(payload)

    async def broadcast_champ_select(session: ChampSelectSession | None) -> None:
        """Broadcast live centralized champ select state to all active WebSocket clients."""
        enriched = await _build_enriched_champ_select_payload(session)
        payload: dict[str, Any] = {
            "type": "CHAMP_SELECT_UPDATE",
            "data": enriched["session"],
            "modeContext": enriched["modeContext"],
            "eligibility": enriched["eligibility"],
        }
        await _broadcast(payload)

    async def on_phase_change(phase: GameflowPhase | str) -> None:
        """Subscriber callback invoked upon LCU gameflow phase transition."""
        await broadcast_phase(phase, lcu_connected=True)

    async def on_champ_select_update(session: ChampSelectSession | None) -> None:
        """Subscriber callback invoked upon LCU champ select session update."""
        await broadcast_champ_select(session)

    if lcu_adapter is not None:
        lcu_adapter.subscribe_phase_change(on_phase_change)
        lcu_adapter.subscribe_champ_select(on_champ_select_update)

    async def _lcu_monitor_loop() -> None:
        """Background coroutine maintaining live connection to League Client."""
        last_phase: str | None = None
        last_status: LcuConnectionStatus | None = None

        while True:
            sleep_duration = poll_interval
            try:
                if lcu_adapter is not None:
                    status = await lcu_adapter.get_connection_status()

                    if status != LcuConnectionStatus.CONNECTED:
                        if hasattr(lcu_adapter, "connect"):
                            connected = await lcu_adapter.connect()
                            if connected:
                                status = LcuConnectionStatus.CONNECTED
                                if hasattr(lcu_adapter, "start_stream"):
                                    asyncio.create_task(lcu_adapter.start_stream())

                    # Notify status changes
                    if status != last_status:
                        last_status = status
                        is_conn = (status == LcuConnectionStatus.CONNECTED)
                        bridge = getattr(app.state, "ui_bridge", ui_bridge)
                        if bridge is not None and hasattr(bridge, "notify_status_change"):
                            try:
                                bridge.notify_status_change(status)
                            except Exception as exc:
                                logger.debug("Bridge status notify error: %s", exc)

                        # Broadcast status transition to mobile clients
                        status_payload = {
                            "type": "LCU_STATUS_UPDATE",
                            "lcu_connected": is_conn,
                            "phase": phase_str if (is_conn and 'phase_str' in locals()) else "None",
                        }
                        await _broadcast(status_payload)

                    if status == LcuConnectionStatus.CONNECTED:
                        # Ensure stream is running
                        if hasattr(lcu_adapter, "ensure_stream_started"):
                            await lcu_adapter.ensure_stream_started()
                        elif hasattr(lcu_adapter, "stream") and lcu_adapter.stream is not None:
                            if not lcu_adapter.stream.is_connected:
                                await lcu_adapter.start_stream()

                        # Query current phase
                        phase = await lcu_adapter.get_gameflow_phase()
                        phase_str = phase.value if hasattr(phase, "value") else str(phase)

                        if phase_str != last_phase:
                            last_phase = phase_str
                            await broadcast_phase(phase_str, lcu_connected=True)
                            if ui_bridge is not None and hasattr(ui_bridge, "notify_phase_change"):
                                try:
                                    ui_bridge.notify_phase_change(phase)
                                except Exception as exc:
                                    logger.debug("Bridge phase notify error: %s", exc)

                        # Live queue telemetry during Matchmaking
                        if phase_str == "Matchmaking":
                            sleep_duration = 1.0
                            try:
                                search = await lcu_adapter.get_matchmaking_search()
                                if search is not None:
                                    search_payload = {
                                        "type": "MATCHMAKING_SEARCH_UPDATE",
                                        "search": search.model_dump(),
                                    }
                                    await _broadcast(search_payload)
                            except Exception as exc:
                                logger.debug("Matchmaking search monitor query error: %s", exc)

                        # High-frequency synchronization during ChampSelect
                        if phase_str == "ChampSelect":
                            sleep_duration = 0.5
                            try:
                                cs = await lcu_adapter.get_champ_select_session()
                                if cs is not None:
                                    await broadcast_champ_select(cs)
                            except Exception as exc:
                                logger.debug("Champ select monitor query error: %s", exc)
                    else:
                        if last_phase is not None and last_phase != "None":
                            last_phase = "None"
                            await broadcast_phase("None", lcu_connected=False)
                            if ui_bridge is not None and hasattr(ui_bridge, "notify_phase_change"):
                                try:
                                    ui_bridge.notify_phase_change(GameflowPhase.NONE)
                                except Exception:
                                    pass

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("LCU monitor loop exception: %s", exc)

            try:
                await asyncio.sleep(sleep_duration)
            except asyncio.CancelledError:
                break

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        monitor_task = None
        if enable_monitor and lcu_adapter is not None:
            monitor_task = asyncio.create_task(_lcu_monitor_loop())
        yield
        if monitor_task is not None:
            monitor_task.cancel()
            try:
                await monitor_task
            except (asyncio.CancelledError, Exception):
                pass

    app = FastAPI(
        title="RiftPilot Web Hub",
        description="Local LAN mobile companion and pairing hub for RiftPilot",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if debug else None,
        redoc_url="/redoc" if debug else None,
        openapi_url="/openapi.json" if debug else None,
    )

    # Store references on app.state
    app.state.ott_manager = ott_manager
    app.state.session_manager = session_manager
    app.state.lcu_adapter = lcu_adapter
    app.state.ui_bridge = ui_bridge
    app.state.active_connections = active_connections
    app.state.connection_sessions = connection_sessions
    app.state.server_port = server_port

    async def require_session(
        request: Request,
        authorization: str | None = Header(default=None),
        x_session_token: str | None = Header(default=None),
    ) -> DeviceSession:
        """Zero-Trust REST guard: require a valid session token (ADR-0004).

        Accepts ``Authorization: Bearer <token>`` or legacy ``X-Session-Token``.
        """
        token: str | None = None
        if authorization:
            scheme, _, credentials = authorization.partition(" ")
            if scheme.lower() == "bearer" and credentials.strip():
                token = credentials.strip()
        if token is None and x_session_token:
            token = x_session_token.strip()
        session = session_manager.validate_session(token) if token else None
        if session is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing session token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        request.state.device_session = session
        return session

    # Every /api/* route except pairing is mounted on this authenticated router.
    api = APIRouter(dependencies=[Depends(require_session)])
    app.state.enable_monitor = enable_monitor

    async def _validate_and_execute_champ_select_action(
        action_id: int,
        champion_id: int,
        completed: bool,
    ) -> tuple[bool, int, str, str]:
        """Re-verify live LCU state before executing a pick/ban hover or lock command."""
        if lcu_adapter is None:
            return False, action_id, "LCU_UNAVAILABLE", "LCU adaptörü kullanılamıyor."

        async with in_flight_action_lock:
            session = await lcu_adapter.get_champ_select_session()
            mode_ctx = await lcu_adapter.get_game_mode_context()
            grid = await lcu_adapter.get_grid_champions()
            disabled_ids = {c.id for c in grid if c.disabled}

            validation = validate_champ_select_command(
                session=session,
                mode_context=mode_ctx,
                action_id=action_id,
                champion_id=champion_id,
                completed=completed,
                disabled_champion_ids=disabled_ids,
            )
            if not validation.allowed or validation.resolved_action is None:
                return (
                    False,
                    action_id,
                    validation.error_code,
                    validation.message,
                )

            resolved_id = validation.resolved_action.id

            if completed and validation.resolved_action.champion_id != champion_id:
                await lcu_adapter.patch_champ_select_action(
                    action_id=resolved_id,
                    champion_id=champion_id,
                    completed=False,
                )

            success = await lcu_adapter.patch_champ_select_action(
                action_id=resolved_id,
                champion_id=champion_id,
                completed=completed,
            )
            if not success:
                return (
                    False,
                    resolved_id,
                    "LCU_REJECTED",
                    "Oyun istemcisi (LCU) işlemi reddetti.",
                )
            return True, resolved_id, "", ""

    async def _validate_and_set_spells(
        spell1_id: int, spell2_id: int
    ) -> tuple[bool, str]:
        """Validate summoner spells against the active game mode and apply via LCU."""
        if lcu_adapter is None:
            return False, "LCU adaptörü kullanılamıyor."

        if spell1_id <= 0 or spell2_id <= 0 or spell1_id == spell2_id:
            return False, "İki farklı geçerli sihirdar büyüsü seçilmelidir."

        mode_ctx = await lcu_adapter.get_game_mode_context()
        available_spells = await lcu_adapter.get_available_summoner_spells(mode_ctx.game_mode)
        valid_ids = {s.id for s in available_spells}

        if spell1_id not in valid_ids or spell2_id not in valid_ids:
            return (
                False,
                f"Seçilen sihirdar büyüsü '{mode_ctx.display_name}' modunda kullanılamaz.",
            )

        success = await lcu_adapter.set_summoner_spells(spell1_id, spell2_id)
        if not success:
            return False, "Oyun istemcisi sihirdar büyüsü değişikliğini reddetti."
        return True, ""

    # =========================================================================
    # HTTP Routes
    # =========================================================================

    @app.get("/", response_class=HTMLResponse)
    async def get_root() -> HTMLResponse:
        """Serve the responsive Hextech mobile web control panel."""
        return HTMLResponse(content=INDEX_HTML, status_code=200)

    @app.post("/api/pair")
    async def pair_device(payload: PairRequest) -> dict[str, Any]:
        """Verify single-use OTT and establish an authenticated device session."""
        if not ott_manager.verify_and_burn(payload.ott):
            raise HTTPException(status_code=401, detail="Invalid or expired OTT")

        client_instance_id = payload.clientInstanceId or str(uuid.uuid4())
        session = session_manager.create_session(
            device_name=payload.deviceName,
            client_instance_id=client_instance_id,
        )

        if ui_bridge is not None and hasattr(ui_bridge, "notify_device_list_changed"):
            try:
                ui_bridge.notify_device_list_changed()
            except Exception:
                pass

        return {
            "session_token": session.session_token,
            "sessionToken": session.session_token,
            "deviceName": session.device_name,
        }

    @api.get("/api/lcu/status")
    async def get_lcu_status_endpoint() -> dict[str, Any]:
        """Query active League Client connection status and gameflow phase."""
        if lcu_adapter is None:
            return {"connected": False, "status": "DISCONNECTED", "phase": "None"}
        status = await lcu_adapter.get_connection_status()
        is_conn = (status == LcuConnectionStatus.CONNECTED)
        phase_str = "None"
        if is_conn:
            try:
                p = await lcu_adapter.get_gameflow_phase()
                phase_str = p.value if hasattr(p, "value") else str(p)
            except Exception:
                pass
        return {
            "connected": is_conn,
            "status": status.value if hasattr(status, "value") else str(status),
            "phase": phase_str,
        }

    @api.get("/api/champ-select")
    async def get_champ_select_state() -> dict[str, Any]:
        """Fetch current centralized champion select session, modeContext, and eligibility."""
        if lcu_adapter is None:
            return {"session": None, "modeContext": None, "eligibility": None}
        session = await lcu_adapter.get_champ_select_session()
        return await _build_enriched_champ_select_payload(session)

    @api.get("/api/champ-select/grid")
    async def get_champ_select_grid() -> dict[str, Any]:
        """Fetch list of selectable and bannable grid champions with live ban/disabled status."""
        champions = []
        if lcu_adapter is not None:
            champions = await lcu_adapter.get_grid_champions()
        if not champions:
            champions = DEFAULT_CHAMPION_CATALOG
        session = await lcu_adapter.get_champ_select_session()
        banned_ids = session.get_all_banned_ids() if session is not None else set()
        unavailable_ids = session.get_unavailable_pick_ids() if session is not None else set()
        result = []
        for champ in champions:
            d = champ.to_dict()
            d["banned"] = champ.id in banned_ids
            d["unavailablePick"] = champ.id in unavailable_ids
            d["selectable"] = (not champ.disabled) and (champ.id not in banned_ids) and (champ.id not in unavailable_ids)
            result.append(d)
        return {"champions": result}

    @api.post("/api/champ-select/action")
    async def perform_champ_select_action(
        payload: ChampSelectActionRequest,
    ) -> dict[str, Any]:
        """Hover or lock-in a pick or ban action after server-side state re-validation."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")

        success, resolved_id, err_code, err_msg = await _validate_and_execute_champ_select_action(
            action_id=payload.actionId,
            champion_id=payload.championId,
            completed=payload.completed,
        )
        if not success:
            status_code = 409 if err_code in (
                "NOT_YOUR_PICK_TURN",
                "NOT_YOUR_BAN_TURN",
                "ACTION_ALREADY_COMPLETED",
                "PHASE_ENDED",
                "TIMER_EXPIRED",
                "NO_ACTIVE_SESSION",
            ) else 400
            raise HTTPException(
                status_code=status_code,
                detail={"errorCode": err_code, "message": err_msg},
            )
        return {"success": True, "actionId": resolved_id}

    @api.get("/api/spells")
    async def get_summoner_spells_endpoint(
        gameMode: str | None = None,
    ) -> dict[str, Any]:
        """Fetch summoner spells valid for the active or specified game mode."""
        if lcu_adapter is not None:
            mode_ctx = await lcu_adapter.get_game_mode_context()
            effective_mode = gameMode or mode_ctx.game_mode
            spells = await lcu_adapter.get_available_summoner_spells(effective_mode)
        else:
            effective_mode = gameMode or "CLASSIC"
            spells = get_spells_for_mode(effective_mode)
        return {
            "gameMode": effective_mode,
            "spells": [s.to_dict() for s in spells],
        }

    @api.post("/api/spells")
    async def set_summoner_spells_endpoint(
        payload: SetSpellsRequest,
    ) -> dict[str, Any]:
        """Validate against active game mode and assign D/F summoner spells."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success, err_msg = await _validate_and_set_spells(
            payload.spell1Id, payload.spell2Id
        )
        if not success:
            raise HTTPException(status_code=400, detail=err_msg)
        return {
            "success": True,
            "spell1Id": payload.spell1Id,
            "spell2Id": payload.spell2Id,
        }

    @api.get("/api/runes/recommended")
    async def get_recommended_runes_endpoint(
        championId: int = 0,
        position: str = "",
        mapId: int = 0,
    ) -> dict[str, Any]:
        """Fetch up to 3 official recommended rune pages from the LCU client (no fake data)."""
        if lcu_adapter is None or championId <= 0:
            return {"championId": championId, "pages": [], "available": False}

        eff_map = mapId
        if eff_map <= 0:
            ctx = await lcu_adapter.get_game_mode_context()
            eff_map = ctx.map_id

        pages = await lcu_adapter.get_recommended_rune_pages(
            champion_id=championId,
            position=position,
            map_id=eff_map,
        )
        return {
            "championId": championId,
            "pages": [p.to_dict() for p in pages],
            "available": len(pages) > 0,
        }

    @api.get("/api/runes/current")
    async def get_current_runes_endpoint() -> dict[str, Any]:
        """Fetch current active rune page from LCU."""
        if lcu_adapter is None:
            return {"page": None}
        page = await lcu_adapter.get_current_rune_page()
        return {"page": page.to_dict() if page is not None else None}

    @api.post("/api/runes/apply")
    async def apply_runes_endpoint(
        payload: ApplyRunePageRequest,
    ) -> dict[str, Any]:
        """Apply a user-confirmed rune page to the League client."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        page = RunePage(
            name=payload.name,
            primary_style_id=payload.primaryStyleId,
            sub_style_id=payload.subStyleId,
            selected_perk_ids=payload.selectedPerkIds,
        )
        success = await lcu_adapter.apply_rune_page(page)
        if not success:
            raise HTTPException(status_code=400, detail="Rün sayfası istemciye uygulanamadı.")
        return {"success": True}

    @api.get("/api/runes/trees")
    async def get_rune_trees_endpoint() -> dict[str, Any]:
        """Return the complete League of Legends rune trees catalog and stat shards."""
        trees_list = list(RUNE_DATA["trees"].values())
        stat_shards = list(RUNE_DATA["stat_shards"])
        return {
            "trees": trees_list,
            "statShards": stat_shards,
            "stat_shards": stat_shards,
        }

    @api.get("/api/runes/custom")
    async def get_custom_runes_endpoint() -> dict[str, Any]:
        """Return all user-saved custom rune pages."""
        mgr = get_custom_rune_manager()
        pages = [p.to_dict() for p in mgr.get_all_pages()]
        return {
            "pages": pages,
            "count": len(pages),
        }

    @api.post("/api/runes/custom")
    async def create_custom_rune_endpoint(
        payload: CustomRuneCreateRequest,
    ) -> dict[str, Any]:
        """Create, validate, and persist a new custom rune page."""
        mgr = get_custom_rune_manager()
        valid, err = mgr.validate_rune_selection(
            primary_style_id=payload.primaryStyleId,
            sub_style_id=payload.subStyleId,
            selected_perk_ids=payload.selectedPerkIds,
            name=payload.name,
        )
        if not valid:
            raise HTTPException(status_code=400, detail=err)

        try:
            saved_page = mgr.save_page({
                "name": payload.name,
                "primaryStyleId": payload.primaryStyleId,
                "subStyleId": payload.subStyleId,
                "selectedPerkIds": payload.selectedPerkIds,
            })
            return {"success": True, "page": saved_page.to_dict()}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @api.delete("/api/runes/custom/{page_id}")
    async def delete_custom_rune_endpoint(page_id: str) -> dict[str, Any]:
        """Delete a saved custom rune page by ID."""
        mgr = get_custom_rune_manager()
        deleted = mgr.delete_page(page_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Özel rün sayfası bulunamadı.")
        return {"success": True, "id": page_id}


    # =========================================================================
    # Lobby & Matchmaking Endpoints (Milestone 4)
    # =========================================================================

    @api.get("/api/lobby")
    async def get_lobby_endpoint() -> dict[str, Any]:
        """Retrieve party lobby state and matchmaking status."""
        if lcu_adapter is None:
            return {"lobby": None, "search": None}
        lobby = await lcu_adapter.get_lobby()
        search = await lcu_adapter.get_matchmaking_search()
        return {
            "lobby": lobby.model_dump() if lobby else None,
            "search": search.model_dump() if search else None,
        }

    @api.post("/api/lobby/create")
    async def create_lobby_endpoint(payload: CreateLobbyRequest) -> dict[str, Any]:
        """Create or switch party lobby for a specific queue ID."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.create_lobby(payload.queueId)
        return {"success": bool(success), "queueId": payload.queueId}

    @api.post("/api/lobby/matchmaking/start")
    async def start_matchmaking_endpoint(
        payload: StartMatchmakingRequest | None = None,
    ) -> dict[str, Any]:
        """Start matchmaking for the active lobby with optional queue selection and lane preferences."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adaptörü kullanılamıyor.")

        status = await lcu_adapter.get_connection_status()
        if status != LcuConnectionStatus.CONNECTED:
            raise HTTPException(status_code=503, detail="League of Legends istemcisi bağlı değil.")

        target_queue = payload.queueId if (payload and payload.queueId and payload.queueId > 0) else None
        if target_queue:
            created = await lcu_adapter.create_lobby(target_queue)
            if not created:
                raise HTTPException(status_code=400, detail=f"Oyun modu (Kuyruk {target_queue}) için lobi oluşturulamadı.")
        else:
            existing = await lcu_adapter.get_lobby()
            if existing is None:
                await lcu_adapter.create_lobby(420)

        # Resolve and apply lane preferences for active lobby
        raw_lane_first = (payload.firstPreference or payload.first) if payload else None
        raw_lane_second = (payload.secondPreference or payload.second) if payload else None
        active_queue_id = target_queue or (existing.queue_id if existing else 420)
        pref1, pref2 = resolve_lane_preferences(active_queue_id, raw_lane_first, raw_lane_second)
        if is_lane_selection_supported(active_queue_id):
            await lcu_adapter.set_position_preferences(str(pref1.value), str(pref2.value))
        elif not is_lane_selection_supported(active_queue_id):
            await lcu_adapter.set_position_preferences(PositionPreference.UNSELECTED.value, PositionPreference.UNSELECTED.value)

        started_matchmaking = await lcu_adapter.start_matchmaking()
        success = started_matchmaking


        if not success:
            # Retry once if initial search failed
            if target_queue:
                await lcu_adapter.create_lobby(target_queue)
                if is_lane_selection_supported(target_queue):
                    await lcu_adapter.set_position_preferences(str(pref1.value), str(pref2.value))
                success = await lcu_adapter.start_matchmaking()
        















        if not success:
            raise HTTPException(
                status_code=400,
                detail="Maç araması başlatılamadı. Lobi lideri olduğunuzdan ve rol seçimlerinizin tamamlandığından emin olun."
            )

        search_obj = await lcu_adapter.get_matchmaking_search()
        return {
            "success": True,
            "search": search_obj.model_dump() if search_obj else None,



        }

    @api.post("/api/lobby/matchmaking/cancel")
    async def cancel_matchmaking_endpoint() -> dict[str, Any]:
        """Cancel ongoing matchmaking search."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.cancel_matchmaking()
        return {"success": success}

    @api.post("/api/match/accept")
    async def accept_match_endpoint(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Accept an active ready check (match found) prompt."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await _perform_accept_ready_check(lcu_adapter)
        return {"success": bool(success)}

    @api.post("/api/match/decline")
    async def decline_match_endpoint(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Decline an active ready check prompt."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await _perform_decline_ready_check(lcu_adapter)
        return {"success": bool(success)}



    @api.post("/api/lobby/preferences")
    @api.put("/api/lobby/preferences")
    async def set_preferences_endpoint(payload: SetPositionPreferencesRequest) -> dict[str, Any]:
        """Set position preferences for the local lobby member."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        raw_first = payload.firstPreference or payload.first
        raw_second = payload.secondPreference or payload.second
        q_id = payload.queueId or 0
        if q_id == 0:
            lobby = await lcu_adapter.get_lobby()
            if lobby:
                q_id = lobby.queue_id
            else:
                q_id = 420
        pref1, pref2 = resolve_lane_preferences(q_id, raw_first, raw_second)
        success = await lcu_adapter.set_position_preferences(pref1.value, pref2.value)
        return {"success": bool(success), "first": pref1.value, "second": pref2.value, "queueId": q_id}
        if False: pass
        return {"success": success}

    # =========================================================================
    # ARAM Bench & Trades Endpoints (Milestone 4)
    # =========================================================================

    @api.get("/api/champ-select/bench")
    async def get_bench_endpoint() -> dict[str, Any]:
        """Retrieve available champions on the ARAM bench."""
        if lcu_adapter is None:
            return {"bench": []}
        bench = await lcu_adapter.get_bench_champions()
        return {"bench": [b.model_dump() for b in bench]}

    @api.post("/api/champ-select/bench/swap")
    async def bench_swap_endpoint(payload: BenchSwapRequest) -> dict[str, Any]:
        """Swap champion with one on the bench."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.bench_swap(payload.championId)
        return {"success": success, "championId": payload.championId}

    @api.post("/api/champ-select/reroll")
    async def reroll_endpoint() -> dict[str, Any]:
        """Reroll champion in ARAM."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.reroll()
        return {"success": success}

    @api.get("/api/champ-select/trades")
    async def get_trades_endpoint() -> dict[str, Any]:
        """Retrieve current champion select trades."""
        if lcu_adapter is None:
            return {"trades": []}
        trades = await lcu_adapter.get_trades()
        return {"trades": [t.model_dump() for t in trades]}

    @api.post("/api/champ-select/trade/request")
    async def request_trade_endpoint(payload: TradeActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.request_trade(payload.tradeId)
        return {"success": bool(success), "tradeId": payload.tradeId}

    @api.post("/api/champ-select/trade/accept")
    async def accept_trade_endpoint(payload: TradeActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.accept_trade(payload.tradeId)
        return {"success": bool(success), "tradeId": payload.tradeId}

    @api.post("/api/champ-select/trade/decline")
    async def decline_trade_endpoint(payload: TradeActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.decline_trade(payload.tradeId)
        return {"success": bool(success), "tradeId": payload.tradeId}

    @api.post("/api/champ-select/trade/cancel")
    async def cancel_trade_endpoint(payload: TradeActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.cancel_trade(payload.tradeId)
        return {"success": bool(success), "tradeId": payload.tradeId}

    @api.get("/api/champ-select/lane-swaps")
    async def get_lane_swaps_endpoint() -> dict[str, Any]:
        if lcu_adapter is None:
            return {"laneSwaps": []}
        swaps_val = lcu_adapter.get_lane_swap_requests()
        swaps = await swaps_val if inspect.isawaitable(swaps_val) else swaps_val
        return {"laneSwaps": [s.model_dump() for s in (swaps or [])]}

    @api.post("/api/champ-select/lane-swap/request")
    async def request_lane_swap_endpoint(payload: LaneSwapActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.request_lane_swap(payload.cellId)
        return {"success": bool(success), "toCellId": payload.cellId}

    @api.post("/api/champ-select/lane-swap/accept")
    async def accept_lane_swap_endpoint(payload: LaneSwapActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.accept_lane_swap(payload.cellId)
        return {"success": bool(success), "fromCellId": payload.cellId}

    @api.post("/api/champ-select/lane-swap/decline")
    async def decline_lane_swap_endpoint(payload: LaneSwapActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.decline_lane_swap(payload.cellId)
        return {"success": bool(success), "fromCellId": payload.cellId}


    # =========================================================================
    # Post-Game Stats & Honor Endpoints (Milestone 5)
    # =========================================================================

    @api.get("/api/game/eog")
    async def get_eog_endpoint() -> dict[str, Any]:
        """Retrieve end-of-game match summary and scoreboard."""
        if lcu_adapter is None:
            return {"stats": None}
        stats = await lcu_adapter.get_end_of_game_stats()
        return {"stats": stats.model_dump() if stats else None}

    @api.get("/api/game/honor/ballot")
    async def get_honor_ballot_endpoint() -> dict[str, Any]:
        """Retrieve eligible teammates for post-game honor voting."""
        if lcu_adapter is None:
            return {"ballot": None}
        ballot = await lcu_adapter.get_honor_ballot()
        return {"ballot": ballot.model_dump() if ballot else None}

    @api.post("/api/game/honor")
    async def honor_player_endpoint(payload: HonorPlayerRequest) -> dict[str, Any]:
        """Cast honor vote for a teammate."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.honor_player(payload.summonerId, payload.honorCategory)
        return {"success": success, "summonerId": payload.summonerId, "category": payload.honorCategory}


    # =========================================================================
    # WAN Remote Relay Endpoints (Milestone 6 - ADR-0003)
    # =========================================================================

    @api.get("/api/wan/status")
    async def get_wan_status_endpoint() -> dict[str, Any]:
        """Retrieve status of the WAN reverse tunnel."""
        if tunnel_adapter is None:
            return {"tunnel": None, "available": False}
        info = await tunnel_adapter.get_tunnel_info()
        return {"tunnel": info.model_dump(), "available": True}

    @api.post("/api/wan/toggle")
    async def toggle_wan_endpoint(request: Request, enable: bool = True) -> dict[str, Any]:
        """Start or stop the WAN reverse tunnel.

        The local port is always the server's own; any client-supplied port is ignored.
        """
        if tunnel_adapter is None:
            raise HTTPException(status_code=503, detail="WAN tunnel adapter not configured")
        if enable:
            local_port = getattr(app.state, "server_port", None)
            if not local_port:
                server_addr = request.scope.get("server")
                local_port = server_addr[1] if server_addr and server_addr[1] else 8000
            info = await tunnel_adapter.start_tunnel(local_port=int(local_port))
        else:
            await tunnel_adapter.stop_tunnel()
            info = await tunnel_adapter.get_tunnel_info()
        return {"tunnel": info.model_dump()}

    # =========================================================================
    # WebSocket Route
    # =========================================================================

    @app.websocket("/ws")
    async def websocket_endpoint(
        websocket: WebSocket, token: str | None = None
    ) -> None:
        """Real-time authenticated WebSocket gateway for mobile clients."""
        await websocket.accept()

        session: DeviceSession | None = None
        if token:
            session = session_manager.validate_session(token)

        if session is None or not session.is_active:
            await websocket.send_json(
                {
                    "type": "UNAUTHORIZED",
                    "error": "Invalid or missing session token",
                }
            )
            await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
            return

        active_connections.add(websocket)
        connection_sessions[websocket] = (session.session_token, asyncio.get_running_loop())

        try:
            current_phase = "None"
            lcu_connected = False
            if lcu_adapter is not None:
                try:
                    status = await lcu_adapter.get_connection_status()
                    lcu_connected = (status == LcuConnectionStatus.CONNECTED)
                    if lcu_connected:
                        phase = await lcu_adapter.get_gameflow_phase()
                        current_phase = (
                            phase.value if hasattr(phase, "value") else str(phase)
                        )
                except Exception as exc:
                    logger.debug("Failed to read initial gameflow phase: %s", exc)

            init_search = None
            if current_phase == "Matchmaking" and lcu_adapter is not None:
                try:
                    search_obj = await lcu_adapter.get_matchmaking_search()
                    if search_obj:
                        init_search = search_obj.model_dump()
                except Exception:
                    pass

            await websocket.send_json(
                {
                    "type": "WELCOME",
                    "phase": current_phase,
                    "lcu_connected": lcu_connected,
                    "search": init_search,
                }
            )

            while True:
                text = await websocket.receive_text()

                try:
                    data = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    data = None
                if not isinstance(data, dict):
                    await websocket.send_json(
                        {
                            "type": "ERROR",
                            "error": "INVALID_MESSAGE",
                            "message": "Messages must be JSON objects.",
                        }
                    )
                    continue

                # Continuous Zero-Trust command authentication
                cmd_token = data.get("token") or token
                validated = session_manager.validate_session(cmd_token)
                if validated is None or not validated.is_active:
                    await _close_unauthorized(websocket)
                    break

                cmd_type = data.get("type")

                if cmd_type in ("ACCEPT_MATCH", "ACCEPT_READY_CHECK"):
                    success = False
                    if lcu_adapter is not None:
                        try:
                            phase = await lcu_adapter.get_gameflow_phase()
                            phase_val = (
                                phase.value
                                if hasattr(phase, "value")
                                else str(phase)
                            )
                            if (
                                phase == GameflowPhase.READY_CHECK
                                or phase_val == "ReadyCheck"
                            ):
                                success = await _perform_accept_ready_check(lcu_adapter)
                        except Exception as exc:
                            logger.warning(
                                "Error executing accept_match: %s", exc
                            )

                    await websocket.send_json(
                        {
                            "type": "READY_CHECK_ACCEPTED",
                            "success": bool(success),
                        }
                    )
                elif cmd_type in ("DECLINE_MATCH", "DECLINE_READY_CHECK"):
                    success = False
                    if lcu_adapter is not None:
                        try:
                            success = await _perform_decline_ready_check(lcu_adapter)
                        except Exception as exc:
                            logger.warning(
                                "Error executing decline_match: %s", exc
                            )

                    await websocket.send_json(
                        {
                            "type": "READY_CHECK_DECLINED",
                            "success": bool(success),
                        }
                    )
                elif cmd_type == "GET_CHAMP_SELECT":
                    cs_obj = None
                    if lcu_adapter is not None:
                        cs_obj = await lcu_adapter.get_champ_select_session()
                    enriched = await _build_enriched_champ_select_payload(cs_obj)
                    await websocket.send_json(
                        {
                            "type": "CHAMP_SELECT_UPDATE",
                            "data": enriched["session"],
                            "modeContext": enriched["modeContext"],
                            "eligibility": enriched["eligibility"],
                        }
                    )
                elif cmd_type == "GET_GRID_CHAMPIONS":
                    grid_data = []
                    if lcu_adapter is not None:
                        champs = await lcu_adapter.get_grid_champions()
                        if not champs:
                            champs = DEFAULT_CHAMPION_CATALOG
                        grid_data = [c.to_dict() for c in champs]
                    else:
                        grid_data = [c.to_dict() for c in DEFAULT_CHAMPION_CATALOG]
                    await websocket.send_json(
                        {
                            "type": "GRID_CHAMPIONS",
                            "data": grid_data,
                        }
                    )
                elif cmd_type == "CHAMP_SELECT_ACTION":
                    action_id = int(data.get("actionId", 0) or 0)
                    champion_id = int(data.get("championId", 0) or 0)
                    completed = bool(data.get("completed", True))

                    success, resolved_id, err_code, err_msg = (
                        await _validate_and_execute_champ_select_action(
                            action_id=action_id,
                            champion_id=champion_id,
                            completed=completed,
                        )
                    )

                    await websocket.send_json(
                        {
                            "type": "ACTION_RESULT",
                            "actionId": resolved_id,
                            "championId": champion_id,
                            "completed": completed,
                            "success": bool(success),
                            "errorCode": err_code,
                            "error": err_msg,
                        }
                    )
                elif cmd_type == "GET_SPELLS":
                    req_mode = data.get("gameMode")
                    if lcu_adapter is not None:
                        ctx = await lcu_adapter.get_game_mode_context()
                        eff_mode = str(req_mode or ctx.game_mode)
                        spells = await lcu_adapter.get_available_summoner_spells(eff_mode)
                    else:
                        eff_mode = str(req_mode or "CLASSIC")
                        spells = get_spells_for_mode(eff_mode)

                    await websocket.send_json(
                        {
                            "type": "SPELLS_CATALOG",
                            "gameMode": eff_mode,
                            "spells": [s.to_dict() for s in spells],
                        }
                    )
                elif cmd_type == "SET_SPELLS":
                    spell1_id = int(data.get("spell1Id", 0) or 0)
                    spell2_id = int(data.get("spell2Id", 0) or 0)
                    success, err_msg = await _validate_and_set_spells(spell1_id, spell2_id)
                    await websocket.send_json(
                        {
                            "type": "SPELLS_RESULT",
                            "spell1Id": spell1_id,
                            "spell2Id": spell2_id,
                            "success": bool(success),
                            "error": err_msg,
                        }
                    )
                elif cmd_type == "GET_RECOMMENDED_RUNES":
                    champ_id = int(data.get("championId", 0) or 0)
                    pos = str(data.get("position", "") or "")
                    map_id = int(data.get("mapId", 0) or 0)
                    pages_list = []
                    if lcu_adapter is not None and champ_id > 0:
                        if map_id <= 0:
                            ctx = await lcu_adapter.get_game_mode_context()
                            map_id = ctx.map_id
                        rec_pages = await lcu_adapter.get_recommended_rune_pages(
                            champion_id=champ_id,
                            position=pos,
                            map_id=map_id,
                        )
                        pages_list = [p.to_dict() for p in rec_pages]
                    await websocket.send_json(
                        {
                            "type": "RECOMMENDED_RUNES",
                            "championId": champ_id,
                            "pages": pages_list,
                            "available": len(pages_list) > 0,
                        }
                    )
                elif cmd_type == "APPLY_RUNES":
                    page_data = data.get("page")
                    success = False
                    err_msg = ""
                    if lcu_adapter is not None:
                        if isinstance(page_data, dict):
                            primary_id = int(page_data.get("primaryStyleId", 0) or 0)
                            sub_id = int(page_data.get("subStyleId", 0) or 0)
                            perks = [int(x) for x in (page_data.get("selectedPerkIds") or [])]
                            if primary_id > 0 and sub_id > 0 and perks:
                                page = RunePage(
                                    name=str(page_data.get("name", "RiftPilot")),
                                    primary_style_id=primary_id,
                                    sub_style_id=sub_id,
                                    selected_perk_ids=perks,
                                )
                                success = await lcu_adapter.apply_rune_page(page)
                                if not success:
                                    err_msg = "İstemci rün sayfasını reddetti."
                            else:
                                err_msg = "Geçersiz rün sayfası verisi."
                        else:
                            err_msg = "Uygulanacak rün sayfası seçilmedi (otomatik seçim yapılmaz)."

                    await websocket.send_json(
                        {
                            "type": "RUNES_RESULT",
                            "success": bool(success),
                            "error": err_msg,
                        }
                    )
                elif cmd_type == "GET_LOBBY":
                    lobby_obj = await lcu_adapter.get_lobby() if lcu_adapter else None
                    search_obj = await lcu_adapter.get_matchmaking_search() if lcu_adapter else None
                    await websocket.send_json({
                        "type": "LOBBY_UPDATE",
                        "lobby": lobby_obj.model_dump() if lobby_obj else None,
                        "search": search_obj.model_dump() if search_obj else None,



                    })
                elif cmd_type == "START_MATCHMAKING":
                    q_id = int(data.get("queueId", 0) or 0)
                    if lcu_adapter is None:
                        await websocket.send_json({
                            "type": "MATCHMAKING_STARTED",
                            "success": False,
                            "error": "LCU adaptörü kullanılamıyor.",
                        })
                        continue

                    status = await lcu_adapter.get_connection_status()
                    if status != LcuConnectionStatus.CONNECTED:
                        await websocket.send_json({
                            "type": "MATCHMAKING_STARTED",
                            "success": False,
                            "error": "League of Legends istemcisi bağlı değil.",
                        })
                        continue

                    if q_id > 0:
                        await lcu_adapter.create_lobby(q_id)
                    else:
                        existing = await lcu_adapter.get_lobby()
                        if existing is None:
                            await lcu_adapter.create_lobby(420)
                            active_queue = 420
                        else:
                            active_queue = existing.queue_id
                    if q_id > 0:
                        active_queue = q_id

                    first_raw = data.get("firstPreference") or data.get("first")
                    second_raw = data.get("secondPreference") or data.get("second")
                    pref1, pref2 = resolve_lane_preferences(active_queue, first_raw, second_raw)
                    if is_lane_selection_supported(active_queue):
                        await lcu_adapter.set_position_preferences(pref1.value, pref2.value)
                    elif not is_lane_selection_supported(active_queue):
                        await lcu_adapter.set_position_preferences("UNSELECTED", "UNSELECTED")
                    ws_started = await lcu_adapter.start_matchmaking()
                    success = ws_started  # ws_anchor
            

            

    






                    err_msg = ""
                    if not success:
                        err_msg = "Maç araması başlatılamadı. Lobi lideri olduğunuzdan ve rollerin seçildiğinden emin olun."

                    search_obj = await lcu_adapter.get_matchmaking_search() if success else None
                    await websocket.send_json({
                        "type": "MATCHMAKING_STARTED",
                        "success": bool(success),
                        "queueId": q_id,
                        "error": err_msg,
                        "search": search_obj.model_dump() if search_obj else None,



                    })
                elif cmd_type == "CREATE_LOBBY":
                    q_id = int(data.get("queueId", 0) or 0)
                    success = await lcu_adapter.create_lobby(q_id) if (lcu_adapter and q_id > 0) else False
                    await websocket.send_json({"type": "LOBBY_CREATED", "success": success, "queueId": q_id})
                elif cmd_type == "CANCEL_MATCHMAKING":
                    success = await lcu_adapter.cancel_matchmaking() if lcu_adapter else False
                    await websocket.send_json({"type": "MATCHMAKING_CANCELED", "success": success})
                elif cmd_type == "SET_POSITION_PREFERENCES":
                    first_raw = data.get("firstPreference") or data.get("first") or ""
                    second_raw = data.get("secondPreference") or data.get("second") or ""
                    q_id = int(data.get("queueId", 0) or 0)
                    if q_id == 0 and lcu_adapter:
                        cur_lobby = await lcu_adapter.get_lobby()
                        q_id = cur_lobby.queue_id if cur_lobby else 420
                    elif q_id == 0:
                        q_id = 420
                    pref1, pref2 = resolve_lane_preferences(q_id, first_raw, second_raw)
                    success = await lcu_adapter.set_position_preferences(pref1.value, pref2.value) if lcu_adapter else False
                    await websocket.send_json({"type": "POSITION_PREFERENCES_RESULT", "success": bool(success), "first": pref1.value, "second": pref2.value, "queueId": q_id})





                elif cmd_type == "BENCH_SWAP":
                    champ_id = int(data.get("championId", 0) or 0)
                    success = await lcu_adapter.bench_swap(champ_id) if lcu_adapter else False
                    await websocket.send_json({"type": "BENCH_SWAP_RESULT", "championId": champ_id, "success": success})
                elif cmd_type == "REROLL":
                    success = await lcu_adapter.reroll() if lcu_adapter else False
                    await websocket.send_json({"type": "REROLL_RESULT", "success": success})
                elif cmd_type == "TRADE_REQUEST":
                    trade_id = int(data.get("tradeId", 0) or 0)
                    success = await lcu_adapter.request_trade(trade_id) if lcu_adapter else False
                    await websocket.send_json({"type": "TRADE_RESULT", "tradeId": trade_id, "success": success})
                elif cmd_type == "TRADE_ACCEPT":
                    trade_id = int(data.get("tradeId", 0) or 0)
                    success = await lcu_adapter.accept_trade(trade_id) if lcu_adapter else False
                    await websocket.send_json({"type": "TRADE_RESULT", "tradeId": trade_id, "success": success})
                elif cmd_type == "TRADE_DECLINE":
                    trade_id = int(data.get("tradeId", 0) or 0)
                    success = await lcu_adapter.decline_trade(trade_id) if lcu_adapter else False
                    await websocket.send_json({"type": "TRADE_RESULT", "tradeId": trade_id, "success": success})
                elif cmd_type == "TRADE_CANCEL":
                    trade_id = int(data.get("tradeId", 0) or 0)
                    success = await lcu_adapter.cancel_trade(trade_id) if lcu_adapter else False
                    await websocket.send_json({"type": "TRADE_RESULT", "tradeId": trade_id, "action": "cancel", "success": success})
                elif cmd_type == "GET_LANE_SWAPS":
                    swaps = []
                    if lcu_adapter:
                        swaps_val = lcu_adapter.get_lane_swap_requests()
                        swaps = await swaps_val if inspect.isawaitable(swaps_val) else swaps_val
                    await websocket.send_json({"type": "LANE_SWAPS_UPDATE", "laneSwaps": [s.model_dump() for s in swaps]})
                elif cmd_type == "LANE_SWAP_REQUEST":
                    to_cell = int(data.get("toCellId", 0) or 0)
                    success = await lcu_adapter.request_lane_swap(to_cell) if lcu_adapter else False
                    await websocket.send_json({"type": "LANE_SWAP_RESULT", "toCellId": to_cell, "action": "request", "success": success})
                elif cmd_type == "LANE_SWAP_ACCEPT":
                    from_cell = int(data.get("fromCellId", 0) or 0)
                    success = await lcu_adapter.accept_lane_swap(from_cell) if lcu_adapter else False
                    await websocket.send_json({"type": "LANE_SWAP_RESULT", "fromCellId": from_cell, "action": "accept", "success": success})
                elif cmd_type == "LANE_SWAP_DECLINE":
                    from_cell = int(data.get("fromCellId", 0) or 0)
                    success = await lcu_adapter.decline_lane_swap(from_cell) if lcu_adapter else False
                    await websocket.send_json({"type": "LANE_SWAP_RESULT", "fromCellId": from_cell, "action": "decline", "success": success})
                elif cmd_type == "GET_EOG_STATS":
                    eog = await lcu_adapter.get_end_of_game_stats() if lcu_adapter else None
                    await websocket.send_json({
                        "type": "EOG_STATS_UPDATE",
                        "stats": eog.model_dump() if eog else None,
                    })
                elif cmd_type == "GET_HONOR_BALLOT":
                    ballot = await lcu_adapter.get_honor_ballot() if lcu_adapter else None
                    await websocket.send_json({
                        "type": "HONOR_BALLOT_UPDATE",
                        "ballot": ballot.model_dump() if ballot else None,
                    })
                elif cmd_type == "HONOR_PLAYER":
                    s_id = int(data.get("summonerId", 0) or 0)
                    cat = str(data.get("honorCategory", "HEART") or "HEART")
                    success = await lcu_adapter.honor_player(s_id, cat) if lcu_adapter else False
                    await websocket.send_json({
                        "type": "HONOR_RESULT",
                        "summonerId": s_id,
                        "category": cat,
                        "success": bool(success),
                    })
                elif cmd_type == "PING":
                    await websocket.send_json({"type": "PONG"})
                elif cmd_type == "GET_STATE":
                    current_phase = "None"
                    lcu_connected = False
                    if lcu_adapter is not None:
                        try:
                            status = await lcu_adapter.get_connection_status()
                            lcu_connected = (status == LcuConnectionStatus.CONNECTED)
                            if lcu_connected:
                                phase = await lcu_adapter.get_gameflow_phase()
                                current_phase = (
                                    phase.value
                                    if hasattr(phase, "value")
                                    else str(phase)
                                )
                        except Exception:
                            pass
                    await websocket.send_json(
                        {
                            "type": "STATE",
                            "phase": current_phase,
                            "lcu_connected": lcu_connected,
                        }
                    )
                else:
                    await websocket.send_json(
                        {
                            "type": "ERROR",
                            "error": f"Unknown command: {cmd_type}",
                        }
                    )

        except WebSocketDisconnect:
            logger.debug("WebSocket client disconnected.")
        except Exception as exc:
            logger.debug("WebSocket exception: %s", exc)
        finally:
            _discard_connection(websocket)

    app.include_router(api)
    return app


__all__ = [
    "ApplyRunePageRequest",
    "CustomRuneCreateRequest",
    "ChampSelectActionRequest",
    "INDEX_HTML",
    "PairRequest",
    "SetSpellsRequest",
    "WS_CLOSE_UNAUTHORIZED",
    "create_app",
]
