"""RiftPilot Adapters — Mobile Web Hub & Pairing Gateway (FastAPI).

Provides the LAN HTTP and WebSocket server for mobile clients:
- Serves the Hextech-themed responsive mobile web client (`GET /`).
- Enforces single-use cryptographic pairing via One-Time Tokens (`POST /api/pair`).
- Establishes authenticated, zero-trust bidirectional WebSockets (`GET /ws`).
- Broadcasts real-time LCU gameflow events and processes remote commands
  such as match acceptance (`ACCEPT_READY_CHECK`).
- Runs background LCU auto-discovery, connection monitoring, and synchronization.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import inspect
import json
import logging
import uuid
from typing import Any, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from riftpilot.core.lcu_port import (
    GameflowPhase,
    ILcuPort,
    LcuConnectionStatus,
)
from riftpilot.core.pairing import DeviceSession, OTTManager, SessionManager

logger = logging.getLogger("RiftPilot.WebHub")

INDEX_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>RiftPilot — Hextech Mobil Panel</title>
    <style>
        :root {
            --bg-primary: #091428;
            --bg-secondary: #0a1428;
            --card-bg: #111a2e;
            --gold-1: #f0e6d2;
            --gold-2: #c8aa6e;
            --gold-3: #785a28;
            --gold-4: #463714;
            --blue-accent: #0ac8b9;
            --text-primary: #f0e6d2;
            --text-secondary: #a09b8c;
            --success: #00ff88;
            --danger: #e84057;
            --radius: 12px;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            user-select: none;
            -webkit-user-select: none;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
        }

        body {
            background: radial-gradient(circle at top, #14223d 0%, #091428 100%);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: space-between;
            padding: 24px 16px;
        }

        header {
            width: 100%;
            max-width: 480px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--gold-4);
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .brand-icon {
            font-size: 24px;
        }

        .brand-title {
            font-size: 20px;
            font-weight: 700;
            color: var(--gold-1);
            letter-spacing: 1px;
            text-transform: uppercase;
        }

        .lcu-subbadge {
            font-size: 10px;
            color: var(--text-secondary);
            letter-spacing: 0.5px;
            margin-top: 2px;
            transition: color 0.3s ease;
        }

        .lcu-subbadge.connected {
            color: var(--blue-accent);
            font-weight: 600;
        }

        .status-badge {
            font-size: 11px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 20px;
            border: 1px solid var(--gold-3);
            background: rgba(120, 90, 40, 0.2);
            color: var(--gold-2);
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .status-badge.connected {
            border-color: var(--blue-accent);
            background: rgba(10, 200, 185, 0.15);
            color: var(--blue-accent);
        }

        .status-badge.error {
            border-color: var(--danger);
            background: rgba(232, 64, 87, 0.15);
            color: var(--danger);
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: currentColor;
        }

        main {
            width: 100%;
            max-width: 480px;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 20px;
            margin: auto 0;
        }

        .phase-card {
            width: 100%;
            background: var(--card-bg);
            border: 1px solid var(--gold-3);
            border-radius: var(--radius);
            padding: 24px 16px;
            text-align: center;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
            position: relative;
            overflow: hidden;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .phase-card.ready-check-active {
            border-color: var(--gold-2);
            background: radial-gradient(circle at center, #1e293b 0%, #111a2e 100%);
            box-shadow: 0 0 30px rgba(200, 170, 110, 0.35);
            animation: pulse-border 1.5s infinite alternate;
        }

        @keyframes pulse-border {
            0% { border-color: var(--gold-3); box-shadow: 0 0 15px rgba(200, 170, 110, 0.2); }
            100% { border-color: var(--gold-1); box-shadow: 0 0 35px rgba(200, 170, 110, 0.6); }
        }

        .phase-label {
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            color: var(--text-secondary);
            margin-bottom: 8px;
        }

        .phase-value {
            font-size: 26px;
            font-weight: 800;
            color: var(--gold-1);
            letter-spacing: 0.5px;
        }

        .action-area {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 16px;
        }

        .btn-accept {
            width: 100%;
            height: 84px;
            border-radius: var(--radius);
            font-size: 22px;
            font-weight: 800;
            letter-spacing: 2px;
            text-transform: uppercase;
            border: 2px solid var(--gold-2);
            background: linear-gradient(180deg, #1e3a5f 0%, #0d1e38 100%);
            color: var(--gold-1);
            cursor: pointer;
            outline: none;
            box-shadow: 0 6px 16px rgba(0, 0, 0, 0.3);
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
        }

        .btn-accept:active {
            transform: scale(0.97);
        }

        .btn-accept.active {
            background: linear-gradient(180deg, #c8aa6e 0%, #785a28 100%);
            color: #091428;
            border-color: #f0e6d2;
            box-shadow: 0 0 25px rgba(200, 170, 110, 0.7);
            animation: pulse-btn 1s infinite alternate;
        }

        .btn-accept.accepted {
            background: linear-gradient(180deg, #00b060 0%, #006030 100%);
            color: white;
            border-color: var(--success);
            box-shadow: 0 0 25px rgba(0, 255, 136, 0.5);
            animation: none;
        }

        .btn-accept:disabled {
            opacity: 0.45;
            cursor: not-allowed;
            animation: none;
        }

        @keyframes pulse-btn {
            0% { transform: scale(1); filter: brightness(1); }
            100% { transform: scale(1.03); filter: brightness(1.2); }
        }

        .info-card {
            width: 100%;
            font-size: 12px;
            color: var(--text-secondary);
            text-align: center;
            line-height: 1.6;
        }

        footer {
            width: 100%;
            max-width: 480px;
            text-align: center;
            font-size: 11px;
            color: var(--gold-4);
            padding-top: 16px;
        }
    </style>
</head>
<body>
    <header>
        <div class="brand">
            <span class="brand-icon">🎮</span>
            <div>
                <div class="brand-title">RiftPilot</div>
                <div id="lcuBadge" class="lcu-subbadge">○ LoL Bekleniyor</div>
            </div>
        </div>
        <div id="statusBadge" class="status-badge">
            <span class="status-dot"></span>
            <span id="statusText">Bağlanıyor...</span>
        </div>
    </header>

    <main>
        <div id="phaseCard" class="phase-card">
            <div class="phase-label">Aktif Oyun Durumu</div>
            <div id="phaseDisplay" class="phase-value">İstemci Bekleniyor</div>
        </div>

        <div class="action-area">
            <button id="acceptBtn" class="btn-accept" disabled>
                KABUL ET
            </button>
            <div id="infoMessage" class="info-card">
                League of Legends eşleşmesi bulunduğunda buton parlayacaktır.
            </div>
        </div>
    </main>

    <footer>
        RiftPilot Mobile Companion — Hextech Transport Agnostic Engine
    </footer>

    <script>
        const statusBadge = document.getElementById('statusBadge');
        const statusText = document.getElementById('statusText');
        const lcuBadge = document.getElementById('lcuBadge');
        const phaseCard = document.getElementById('phaseCard');
        const phaseDisplay = document.getElementById('phaseDisplay');
        const acceptBtn = document.getElementById('acceptBtn');
        const infoMessage = document.getElementById('infoMessage');

        let socket = null;
        let sessionToken = localStorage.getItem('riftpilot_session_token');

        function setStatus(text, type) {
            statusText.innerText = text;
            statusBadge.className = 'status-badge ' + (type || '');
        }

        function updateLcuStatus(connected) {
            if (lcuBadge) {
                if (connected) {
                    lcuBadge.innerText = '● LoL Bağlı';
                    lcuBadge.className = 'lcu-subbadge connected';
                } else {
                    lcuBadge.innerText = '○ LoL Bekleniyor';
                    lcuBadge.className = 'lcu-subbadge';
                }
            }
        }

        function playChime() {
            try {
                const AudioCtx = window.AudioContext || window.webkitAudioContext;
                if (!AudioCtx) return;
                const ctx = new AudioCtx();
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();

                osc.type = 'triangle';
                osc.frequency.setValueAtTime(523.25, ctx.currentTime); // C5
                osc.frequency.exponentialRampToValueAtTime(783.99, ctx.currentTime + 0.3); // G5
                gain.gain.setValueAtTime(0.4, ctx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.6);

                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start();
                osc.stop(ctx.currentTime + 0.6);
            } catch (e) {
                console.warn('Audio not allowed without gesture:', e);
            }
        }

        function triggerVibrate() {
            if (navigator.vibrate) {
                navigator.vibrate([200, 100, 200, 100, 400]);
            }
        }

        function updateGameflow(phase) {
            const p = String(phase || 'None');
            if (p === 'ReadyCheck') {
                phaseDisplay.innerText = '⚔️ MAÇ BULUNDU!';
                phaseCard.classList.add('ready-check-active');
                acceptBtn.disabled = false;
                acceptBtn.classList.remove('accepted');
                acceptBtn.classList.add('active');
                acceptBtn.innerText = 'KABUL ET';
                infoMessage.innerText = 'Eşleşme bulundu! Sırayı kaçırmamak için butona basın.';
                playChime();
                triggerVibrate();
            } else {
                phaseCard.classList.remove('ready-check-active');
                acceptBtn.disabled = true;
                acceptBtn.classList.remove('active');
                acceptBtn.classList.remove('accepted');

                if (p === 'Matchmaking') {
                    phaseDisplay.innerText = '⏳ Sırada...';
                    acceptBtn.innerText = 'SIRADA BEKLENİYOR';
                    infoMessage.innerText = 'Eşleşme aranıyor...';
                } else if (p === 'ChampSelect') {
                    phaseDisplay.innerText = '🏆 Şampiyon Seçimi';
                    acceptBtn.innerText = 'ŞAMPİYON SEÇİMİ';
                    infoMessage.innerText = 'Şampiyon seçimi aşaması aktif.';
                } else if (p === 'InProgress') {
                    phaseDisplay.innerText = '🔥 Oyunda';
                    acceptBtn.innerText = 'OYUNDASINIZ';
                    infoMessage.innerText = 'Karşılaşma devam ediyor.';
                } else if (p === 'Lobby') {
                    phaseDisplay.innerText = '🏠 Lobide';
                    acceptBtn.innerText = 'LOBİDE';
                    infoMessage.innerText = 'Oda liderinin sırayı başlatması bekleniyor.';
                } else {
                    phaseDisplay.innerText = '🎮 Oyun Dışı';
                    acceptBtn.innerText = 'BEKLENİYOR';
                    infoMessage.innerText = 'League Client bağlantısı bekleniyor...';
                }
            }
        }

        async function autoPair(ott) {
            setStatus('Eşleştiriliyor...', '');
            try {
                let clientInstanceId = localStorage.getItem('riftpilot_client_id');
                if (!clientInstanceId) {
                    clientInstanceId = 'client-' + Math.random().toString(36).substring(2, 10);
                    localStorage.setItem('riftpilot_client_id', clientInstanceId);
                }

                const deviceName = /iPhone|iPad|iPod/i.test(navigator.userAgent) ? 'iPhone' :
                                   /Android/i.test(navigator.userAgent) ? 'Android Cihaz' : 'Mobil Tarayıcı';

                const resp = await fetch('/api/pair', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ott: ott,
                        deviceName: deviceName,
                        clientInstanceId: clientInstanceId
                    })
                });

                if (resp.ok) {
                    const data = await resp.json();
                    sessionToken = data.session_token || data.sessionToken;
                    localStorage.setItem('riftpilot_session_token', sessionToken);
                    // Clear ?ott= from address bar
                    window.history.replaceState({}, document.title, window.location.pathname);
                    connectWebSocket();
                } else {
                    setStatus('Eşleşme Başarısız', 'error');
                    infoMessage.innerText = 'Geçersiz veya süresi dolmuş QR kod. Masaüstünden yenileyin.';
                }
            } catch (err) {
                setStatus('Bağlantı Hatası', 'error');
                infoMessage.innerText = 'Sunucuya bağlanılamadı: ' + err.message;
            }
        }

        function connectWebSocket() {
            if (!sessionToken) {
                setStatus('Yetkilendirme Gerekli', 'error');
                infoMessage.innerText = 'Lütfen masaüstündeki QR kodu tarayarak bağlanın.';
                return;
            }

            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws?token=${encodeURIComponent(sessionToken)}`;
            socket = new WebSocket(wsUrl);

            socket.onopen = () => {
                setStatus('Bağlandı', 'connected');
                // Request live state immediately upon connection
                try {
                    socket.send(JSON.stringify({ type: 'GET_STATE', token: sessionToken }));
                } catch (e) {}
            };

            socket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'WELCOME' || data.type === 'GAMEFLOW_UPDATE' || data.type === 'STATE' || data.type === 'INITIAL_STATE') {
                        if (data.phase !== undefined) {
                            updateGameflow(data.phase);
                        }
                        if (data.lcu_connected !== undefined) {
                            updateLcuStatus(data.lcu_connected);
                        }
                    } else if (data.type === 'READY_CHECK_ACCEPTED') {
                        if (data.success) {
                            acceptBtn.innerText = '✓ KABUL EDİLDİ';
                            acceptBtn.classList.remove('active');
                            acceptBtn.classList.add('accepted');
                            infoMessage.innerText = 'Maç kabul edildi! Şampiyon seçimi bekleniyor.';
                        }
                    } else if (data.type === 'REVOKED' || data.type === 'UNAUTHORIZED') {
                        localStorage.removeItem('riftpilot_session_token');
                        setStatus('Yetki İptal Edildi', 'error');
                        infoMessage.innerText = 'Masaüstünden yetki sonlandırıldı.';
                        if (socket) socket.close();
                    }
                } catch (e) {
                    console.log('WS msg:', event.data);
                }
            };

            socket.onclose = (event) => {
                if (event.code === 4001) {
                    localStorage.removeItem('riftpilot_session_token');
                    setStatus('Yetkisiz Oturum', 'error');
                } else {
                    setStatus('Bağlantı Koptu', 'error');
                    setTimeout(() => {
                        if (localStorage.getItem('riftpilot_session_token')) {
                            connectWebSocket();
                        }
                    }, 2500);
                }
            };
        }

        // Heartbeat keepalive every 8 seconds
        setInterval(() => {
            if (socket && socket.readyState === WebSocket.OPEN && sessionToken) {
                try {
                    socket.send(JSON.stringify({ type: 'PING', token: sessionToken }));
                } catch (e) {}
            }
        }, 8000);

        // Auto re-sync when browser tab or phone screen wakes up
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                if (!socket || socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING) {
                    if (localStorage.getItem('riftpilot_session_token')) {
                        connectWebSocket();
                    }
                } else if (socket.readyState === WebSocket.OPEN) {
                    try {
                        socket.send(JSON.stringify({ type: 'GET_STATE', token: sessionToken }));
                    } catch (e) {}
                }
            }
        });

        acceptBtn.addEventListener('click', () => {
            if (!acceptBtn.disabled && socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    type: 'ACCEPT_READY_CHECK',
                    token: sessionToken
                }));
                acceptBtn.innerText = 'GÖNDERİLİYOR...';
                acceptBtn.disabled = true;
            }
        });

        // Initialize: Parse ?ott= query parameter or reuse stored token
        const urlParams = new URLSearchParams(window.location.search);
        const ottParam = urlParams.get('ott');
        if (ottParam) {
            autoPair(ottParam);
        } else if (sessionToken) {
            connectWebSocket();
        } else {
            setStatus('Eşleştirme Bekleniyor', '');
            infoMessage.innerText = 'Cihazı bağlamak için masaüstü QR kodunu tarayın.';
        }
    </script>
</body>
</html>
"""


class PairRequest(BaseModel):
    """Payload schema for mobile device pairing requests."""

    ott: str = Field(..., min_length=1, description="Single-use One-Time Token")
    deviceName: str = Field(..., min_length=1, description="Connecting device name")
    clientInstanceId: str | None = Field(
        default=None, description="Optional client instance identifier"
    )


def create_app(
    ott_manager: OTTManager,
    session_manager: SessionManager,
    lcu_adapter: ILcuPort | None = None,
    ui_bridge: Any = None,
    enable_monitor: bool = True,
    poll_interval: float = 1.5,
) -> FastAPI:
    """Factory creating and configuring the FastAPI Web Hub application.

    Args:
        ott_manager: Core domain One-Time Token manager for burn-on-read pairing.
        session_manager: Core domain session manager for zero-trust validation.
        lcu_adapter: Optional outbound LCU port adapter for League Client interaction.
        ui_bridge: Optional LcuUiBridge for cross-thread Qt signaling to desktop window.
        enable_monitor: Whether to run background LCU discovery and state sync loop.
        poll_interval: Polling frequency in seconds for background LCU health check.

    Returns:
        Configured FastAPI application instance with HTTP and WebSocket routes.
    """
    active_connections: Set[WebSocket] = set()

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

        dead_connections: list[WebSocket] = []

        for ws in list(active_connections):
            try:
                await ws.send_json(payload)
            except Exception:
                dead_connections.append(ws)

        for ws in dead_connections:
            active_connections.discard(ws)

    async def on_phase_change(phase: GameflowPhase | str) -> None:
        """Subscriber callback invoked upon LCU gameflow phase transition."""
        await broadcast_phase(phase, lcu_connected=True)

    if lcu_adapter is not None:
        lcu_adapter.subscribe_phase_change(on_phase_change)

    async def _lcu_monitor_loop() -> None:
        """Background coroutine maintaining live connection to League Client."""
        last_phase: str | None = None
        last_status: LcuConnectionStatus | None = None

        while True:
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
                        if ui_bridge is not None and hasattr(ui_bridge, "notify_status_change"):
                            try:
                                ui_bridge.notify_status_change(status)
                            except Exception as exc:
                                logger.debug("Bridge status notify error: %s", exc)

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
                await asyncio.sleep(poll_interval)
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
    )

    # Store references on app.state
    app.state.ott_manager = ott_manager
    app.state.session_manager = session_manager
    app.state.lcu_adapter = lcu_adapter
    app.state.ui_bridge = ui_bridge
    app.state.active_connections = active_connections
    app.state.enable_monitor = enable_monitor

    # =========================================================================
    # HTTP Routes
    # =========================================================================

    @app.get("/", response_class=HTMLResponse)
    async def get_root() -> HTMLResponse:
        """Serve the responsive Hextech mobile web control panel."""
        return HTMLResponse(content=INDEX_HTML, status_code=200)

    @app.post("/api/pair")
    async def pair_device(payload: PairRequest) -> dict[str, Any]:
        """Verify single-use OTT and establish an authenticated device session.

        Enforces burn-on-read security: the token is purged immediately.
        """
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

    # =========================================================================
    # WebSocket Route
    # =========================================================================

    @app.websocket("/ws")
    async def websocket_endpoint(
        websocket: WebSocket, token: str | None = None
    ) -> None:
        """Real-time authenticated WebSocket gateway for mobile clients.

        Enforces zero-trust token authentication and continuous validation.
        """
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
            await websocket.close(code=4001)
            return

        active_connections.add(websocket)

        try:
            # Transmit initial connection welcome packet with current gameflow phase & LCU status
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

            await websocket.send_json(
                {
                    "type": "WELCOME",
                    "phase": current_phase,
                    "lcu_connected": lcu_connected,
                }
            )

            while True:
                text = await websocket.receive_text()

                try:
                    data = json.loads(text)
                    if not isinstance(data, dict):
                        await websocket.send_text("ECHO: " + text)
                        continue
                except (json.JSONDecodeError, TypeError):
                    await websocket.send_text("ECHO: " + text)
                    continue

                # Continuous Zero-Trust command authentication
                cmd_token = data.get("token") or token
                validated = session_manager.validate_session(cmd_token)
                if validated is None or not validated.is_active:
                    await websocket.send_json(
                        {
                            "type": "REVOKED",
                            "error": "Session revoked or invalid",
                        }
                    )
                    break

                cmd_type = data.get("type")

                if cmd_type == "ACCEPT_READY_CHECK":
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
                                success = await lcu_adapter.accept_ready_check()
                        except Exception as exc:
                            logger.warning(
                                "Error executing accept_ready_check: %s", exc
                            )

                    await websocket.send_json(
                        {
                            "type": "READY_CHECK_ACCEPTED",
                            "success": bool(success),
                        }
                    )
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
            active_connections.discard(websocket)

    return app


__all__ = ["INDEX_HTML", "PairRequest", "create_app"]
