"""RiftPilot Adapters — Mobile Web Hub & Pairing Gateway (FastAPI).

Provides the LAN HTTP and WebSocket server for mobile clients:
- Serves the Hextech-themed responsive mobile web client (`GET /`).
- Enforces single-use cryptographic pairing via One-Time Tokens (`POST /api/pair`).
- Establishes authenticated, zero-trust bidirectional WebSockets (`GET /ws`).
- Broadcasts real-time LCU gameflow events and processes remote commands
  such as match acceptance (`ACCEPT_READY_CHECK`).
- Provides Champion Select remote orchestration: live session updates,
  champion grid search/filtering, hover, and one-tap lock-in / ban.
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

from riftpilot.core.champ_select import ChampSelectSession, ChampionSummary
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
            --blue-team: #1a78c2;
            --red-team: #c83232;
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
            padding: 16px 12px;
        }

        header {
            width: 100%;
            max-width: 500px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 12px;
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
            letter-spacing: 1px;
            background: linear-gradient(180deg, var(--gold-1) 0%, var(--gold-2) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .lcu-subbadge {
            font-size: 11px;
            color: var(--text-secondary);
            margin-top: 2px;
            display: flex;
            align-items: center;
            gap: 4px;
        }

        .lcu-subbadge.connected {
            color: var(--success);
        }

        .status-badge {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 11px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 20px;
            background: rgba(10, 20, 40, 0.8);
            border: 1px solid var(--gold-4);
            color: var(--text-secondary);
        }

        .status-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: #666;
        }

        .status-badge.connected .status-dot {
            background: var(--blue-accent);
            box-shadow: 0 0 8px var(--blue-accent);
        }

        .status-badge.connected {
            border-color: var(--blue-accent);
            color: var(--gold-1);
        }

        .status-badge.error .status-dot {
            background: var(--danger);
        }

        main {
            width: 100%;
            max-width: 500px;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 16px;
            margin: auto 0;
            padding: 10px 0;
        }

        /* Standard Gameflow View */
        #mainView {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 20px;
        }

        .phase-card {
            width: 100%;
            background: var(--card-bg);
            border: 1px solid var(--gold-4);
            border-radius: var(--radius);
            padding: 24px;
            text-align: center;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
            transition: all 0.3s ease;
        }

        .phase-card.ready-check-active {
            border-color: var(--gold-2);
            box-shadow: 0 0 30px rgba(200, 170, 110, 0.4);
            animation: pulse-border 1.5s infinite alternate;
        }

        @keyframes pulse-border {
            0% { border-color: var(--gold-3); box-shadow: 0 0 15px rgba(200, 170, 110, 0.2); }
            100% { border-color: var(--gold-1); box-shadow: 0 0 35px rgba(200, 170, 110, 0.6); }
        }

        .phase-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 2px;
            color: var(--gold-2);
            margin-bottom: 8px;
        }

        .phase-value {
            font-size: 24px;
            font-weight: 700;
            color: var(--gold-1);
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

        /* ====================================================================
           Champion Select Mobile View
           ==================================================================== */
        #champSelectView {
            width: 100%;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .cs-top-bar {
            width: 100%;
            background: var(--card-bg);
            border: 1px solid var(--gold-4);
            border-radius: var(--radius);
            padding: 10px 14px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .cs-badges {
            display: flex;
            gap: 8px;
            align-items: center;
        }

        .cs-badge {
            font-size: 11px;
            font-weight: 700;
            padding: 4px 8px;
            border-radius: 4px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .cs-badge.pick {
            background: rgba(10, 200, 185, 0.2);
            border: 1px solid var(--blue-accent);
            color: var(--blue-accent);
        }

        .cs-badge.ban {
            background: rgba(232, 64, 87, 0.2);
            border: 1px solid var(--danger);
            color: var(--danger);
        }

        .cs-badge.pos {
            background: rgba(200, 170, 110, 0.15);
            border: 1px solid var(--gold-3);
            color: var(--gold-2);
        }

        .cs-timer-badge {
            font-size: 20px;
            font-weight: 800;
            color: var(--gold-1);
            min-width: 44px;
            text-align: right;
        }

        .cs-progress-container {
            width: 100%;
            height: 4px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 2px;
            overflow: hidden;
            margin-top: -6px;
        }

        .cs-progress-bar {
            height: 100%;
            width: 100%;
            background: var(--gold-2);
            transition: width 0.3s linear;
        }

        .teams-summary {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
            width: 100%;
        }

        .team-box {
            background: rgba(10, 20, 40, 0.6);
            border: 1px solid var(--gold-4);
            border-radius: 8px;
            padding: 6px 8px;
            font-size: 11px;
        }

        .team-box.blue {
            border-left: 3px solid var(--blue-team);
        }

        .team-box.red {
            border-left: 3px solid var(--red-team);
        }

        .team-header-title {
            font-weight: 700;
            margin-bottom: 4px;
            color: var(--gold-2);
            text-transform: uppercase;
            font-size: 10px;
        }

        .member-row {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 3px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }

        .member-row:last-child {
            border-bottom: none;
        }

        .member-pos {
            font-size: 9px;
            color: var(--text-secondary);
            width: 16px;
            text-transform: uppercase;
        }

        .member-champ {
            font-weight: 600;
            color: var(--gold-1);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            flex: 1;
        }

        .member-row.is-me {
            color: var(--blue-accent);
            font-weight: bold;
        }

        .member-row.is-me .member-champ {
            color: var(--blue-accent);
        }

        .champ-search-box {
            width: 100%;
            position: relative;
        }

        .champ-search-input {
            width: 100%;
            background: var(--card-bg);
            border: 1px solid var(--gold-3);
            border-radius: 8px;
            padding: 10px 14px;
            color: var(--text-primary);
            font-size: 14px;
            outline: none;
            box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.3);
        }

        .champ-search-input:focus {
            border-color: var(--blue-accent);
        }

        .champ-grid-container {
            width: 100%;
            max-height: 230px;
            overflow-y: auto;
            border: 1px solid var(--gold-4);
            border-radius: var(--radius);
            background: rgba(10, 20, 40, 0.7);
            padding: 8px;
        }

        .champ-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(62px, 1fr));
            gap: 6px;
        }

        .champ-item {
            display: flex;
            flex-direction: column;
            align-items: center;
            background: rgba(17, 26, 46, 0.9);
            border: 1px solid var(--gold-4);
            border-radius: 6px;
            padding: 4px;
            cursor: pointer;
            transition: all 0.15s ease;
        }

        .champ-item:active {
            transform: scale(0.95);
        }

        .champ-item.selected {
            border-color: var(--gold-1);
            box-shadow: 0 0 10px rgba(240, 230, 210, 0.8);
            background: rgba(30, 58, 95, 0.8);
        }

        .champ-item.banned {
            opacity: 0.3;
            filter: grayscale(1);
            pointer-events: none;
        }

        .champ-img {
            width: 44px;
            height: 44px;
            border-radius: 4px;
            object-fit: cover;
            background: #111a2e;
        }

        .champ-name {
            font-size: 10px;
            color: var(--text-primary);
            text-align: center;
            margin-top: 3px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            width: 100%;
        }

        .cs-action-panel {
            width: 100%;
            display: flex;
            gap: 10px;
            align-items: center;
            background: var(--card-bg);
            border: 1px solid var(--gold-3);
            border-radius: var(--radius);
            padding: 8px 12px;
        }

        .cs-selected-preview {
            display: flex;
            align-items: center;
            gap: 8px;
            flex: 1;
            overflow: hidden;
        }

        .cs-preview-img {
            width: 38px;
            height: 38px;
            border-radius: 6px;
            border: 1px solid var(--gold-3);
            object-fit: cover;
            background: #111a2e;
        }

        .cs-preview-name {
            font-size: 13px;
            font-weight: 700;
            color: var(--gold-1);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .cs-btn-lock {
            height: 48px;
            padding: 0 20px;
            border-radius: 8px;
            font-size: 15px;
            font-weight: 800;
            letter-spacing: 1px;
            text-transform: uppercase;
            border: 1px solid var(--gold-2);
            background: linear-gradient(180deg, #c8aa6e 0%, #785a28 100%);
            color: #091428;
            cursor: pointer;
            outline: none;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
            transition: all 0.2s;
        }

        .cs-btn-lock.ban {
            background: linear-gradient(180deg, #e84057 0%, #8a1f2e 100%);
            border-color: #ff7888;
            color: white;
            box-shadow: 0 0 15px rgba(232, 64, 87, 0.5);
        }

        .cs-btn-lock.pick {
            background: linear-gradient(180deg, #0ac8b9 0%, #00605a 100%);
            border-color: #6efff4;
            color: white;
            box-shadow: 0 0 15px rgba(10, 200, 185, 0.5);
        }

        .cs-btn-lock:disabled {
            opacity: 0.4;
            cursor: not-allowed;
            background: #1e3a5f;
            border-color: var(--gold-4);
            color: var(--text-secondary);
            box-shadow: none;
        }

        footer {
            width: 100%;
            max-width: 500px;
            text-align: center;
            font-size: 11px;
            color: var(--gold-4);
            padding-top: 10px;
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
        <!-- Standard Gameflow View -->
        <div id="mainView">
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
        </div>

        <!-- Champion Select View -->
        <div id="champSelectView" style="display: none;">
            <div class="cs-top-bar">
                <div class="cs-badges">
                    <span id="csModeBadge" class="cs-badge pick">SEÇİM</span>
                    <span id="csPositionBadge" class="cs-badge pos">ORTA</span>
                </div>
                <div id="csTimerBadge" class="cs-timer-badge">30s</div>
            </div>
            <div class="cs-progress-container">
                <div id="csProgressBar" class="cs-progress-bar"></div>
            </div>

            <div class="teams-summary">
                <div class="team-box blue">
                    <div class="team-header-title">Mavi Takım (Dost)</div>
                    <div id="myTeamList"></div>
                </div>
                <div class="team-box red">
                    <div class="team-header-title">Kırmızı Takım (Rakip)</div>
                    <div id="theirTeamList"></div>
                </div>
            </div>

            <div class="champ-search-box">
                <input type="text" id="champSearchInput" class="champ-search-input" placeholder="🔍 Şampiyon ara (Ahri, Yasuo, Lee Sin)...">
            </div>

            <div class="champ-grid-container">
                <div id="champGrid" class="champ-grid"></div>
            </div>

            <div class="cs-action-panel">
                <div class="cs-selected-preview">
                    <img id="csPreviewImg" class="cs-preview-img" src="" alt="" style="display:none;">
                    <div id="csPreviewName" class="cs-preview-name">Şampiyon Seçin</div>
                </div>
                <button id="csLockBtn" class="cs-btn-lock" disabled>KİLİTLE</button>
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
        const mainView = document.getElementById('mainView');
        const phaseCard = document.getElementById('phaseCard');
        const phaseDisplay = document.getElementById('phaseDisplay');
        const acceptBtn = document.getElementById('acceptBtn');
        const infoMessage = document.getElementById('infoMessage');

        // Champ select DOM references
        const champSelectView = document.getElementById('champSelectView');
        const csModeBadge = document.getElementById('csModeBadge');
        const csPositionBadge = document.getElementById('csPositionBadge');
        const csTimerBadge = document.getElementById('csTimerBadge');
        const csProgressBar = document.getElementById('csProgressBar');
        const myTeamList = document.getElementById('myTeamList');
        const theirTeamList = document.getElementById('theirTeamList');
        const champSearchInput = document.getElementById('champSearchInput');
        const champGrid = document.getElementById('champGrid');
        const csPreviewImg = document.getElementById('csPreviewImg');
        const csPreviewName = document.getElementById('csPreviewName');
        const csLockBtn = document.getElementById('csLockBtn');

        let socket = null;
        let sessionToken = localStorage.getItem('riftpilot_session_token');

        // State variables
        let currentChampSelect = null;
        let gridChampions = [];
        let selectedChampId = 0;
        let selectedChampName = '';
        let timerSecondsRemaining = 0;
        let timerTotalSeconds = 30;
        let timerCountdownInterval = null;

        // Position translation mapping
        const POSITION_NAMES = {
            'top': 'ÜST KORİDOR',
            'jungle': 'ORMAN',
            'middle': 'ORTA KORİDOR',
            'mid': 'ORTA KORİDOR',
            'bottom': 'ALT KORİDOR',
            'bot': 'ALT KORİDOR',
            'utility': 'DESTEK',
            'support': 'DESTEK',
            '': 'KORİDOR'
        };

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
                osc.frequency.setValueAtTime(523.25, ctx.currentTime);
                osc.frequency.exponentialRampToValueAtTime(783.99, ctx.currentTime + 0.3);
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

        function getChampionIconUrl(id) {
            if (!id || id <= 0) return '';
            return `https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/champion-icons/${id}.png`;
        }

        function updateGameflow(phase) {
            const p = String(phase || 'None');
            if (p === 'ChampSelect') {
                mainView.style.display = 'none';
                champSelectView.style.display = 'flex';
                // Request champ select state and champion catalog immediately
                if (socket && socket.readyState === WebSocket.OPEN) {
                    try {
                        socket.send(JSON.stringify({ type: 'GET_CHAMP_SELECT', token: sessionToken }));
                        socket.send(JSON.stringify({ type: 'GET_GRID_CHAMPIONS', token: sessionToken }));
                    } catch (e) {}
                }
            } else {
                mainView.style.display = 'flex';
                champSelectView.style.display = 'none';
                clearInterval(timerCountdownInterval);

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
        }

        function renderChampSelect(data) {
            if (!data) return;
            currentChampSelect = data;

            // 1. Update Timer
            if (data.timer) {
                const totalMs = data.timer.totalTimeInPhase || 30000;
                const leftMs = data.timer.adjustedTimeLeftInPhase || data.timer.timeLeftInPhase || 30000;
                timerTotalSeconds = Math.max(1, Math.round(totalMs / 1000));
                timerSecondsRemaining = Math.max(0, Math.round(leftMs / 1000));
                csTimerBadge.innerText = timerSecondsRemaining + 's';

                clearInterval(timerCountdownInterval);
                timerCountdownInterval = setInterval(() => {
                    if (timerSecondsRemaining > 0) {
                        timerSecondsRemaining--;
                        csTimerBadge.innerText = timerSecondsRemaining + 's';
                        const pct = Math.max(0, Math.min(100, (timerSecondsRemaining / timerTotalSeconds) * 100));
                        csProgressBar.style.width = pct + '%';
                        if (timerSecondsRemaining <= 5) {
                            csProgressBar.style.background = 'var(--danger)';
                            csTimerBadge.style.color = 'var(--danger)';
                        } else {
                            csProgressBar.style.background = 'var(--gold-2)';
                            csTimerBadge.style.color = 'var(--gold-1)';
                        }
                    }
                }, 1000);
            }

            // 2. Update Local Player Lane & Active Action
            const localPlayer = data.localPlayer;
            if (localPlayer) {
                const pos = (localPlayer.assignedPosition || localPlayer.assigned_position || '').toLowerCase();
                csPositionBadge.innerText = POSITION_NAMES[pos] || 'KORİDOR';
            }

            const activeAction = data.activeAction;
            if (activeAction) {
                const isBan = (activeAction.type || '').toLowerCase() === 'ban';
                csModeBadge.innerText = isBan ? 'YASAKLAMA' : 'SEÇİM';
                csModeBadge.className = 'cs-badge ' + (isBan ? 'ban' : 'pick');

                csLockBtn.disabled = (selectedChampId <= 0);
                csLockBtn.innerText = isBan ? 'YASAKLA' : 'KİLİTLE';
                csLockBtn.className = 'cs-btn-lock ' + (isBan ? 'ban' : 'pick');
            } else {
                csModeBadge.innerText = 'BEKLENİYOR';
                csModeBadge.className = 'cs-badge pos';
                csLockBtn.disabled = true;
                csLockBtn.innerText = 'SIRA BEKLENİYOR';
                csLockBtn.className = 'cs-btn-lock';
            }

            // 3. Render Team Rosters
            renderTeamRoster(myTeamList, data.myTeam || [], data.localPlayerCellId);
            renderTeamRoster(theirTeamList, data.theirTeam || [], -1);

            // 4. Update Champion Grid (gray out banned champions)
            renderChampGrid();
        }

        function findChampionName(id) {
            if (!id || id <= 0) return 'Seçim Yapılmadı';
            const found = gridChampions.find(c => c.id === id);
            return found ? found.name : `Şampiyon #${id}`;
        }

        function renderTeamRoster(container, members, localCellId) {
            container.innerHTML = '';
            members.forEach(m => {
                const row = document.createElement('div');
                const isMe = (m.cellId === localCellId || m.cell_id === localCellId);
                row.className = 'member-row' + (isMe ? ' is-me' : '');

                const pos = (m.assignedPosition || m.assigned_position || '').substring(0, 3).toUpperCase();
                const champId = m.championId || m.champion_id || m.championPickIntent || m.champion_pick_intent || 0;
                const champName = findChampionName(champId);

                row.innerHTML = `
                    <span class="member-pos">${pos || '—'}</span>
                    <span class="member-champ">${champName}</span>
                `;
                container.appendChild(row);
            });
        }

        function renderChampGrid() {
            const query = (champSearchInput.value || '').trim().toLowerCase();
            champGrid.innerHTML = '';

            const bannedSet = new Set(currentChampSelect ? (currentChampSelect.allBannedIds || []) : []);

            const filtered = gridChampions.filter(c => {
                if (!query) return true;
                return (c.name || '').toLowerCase().includes(query);
            });

            filtered.forEach(c => {
                const item = document.createElement('div');
                const isBanned = bannedSet.has(c.id);
                const isSelected = (selectedChampId === c.id);

                item.className = 'champ-item' + (isSelected ? ' selected' : '') + (isBanned ? ' banned' : '');

                const iconUrl = getChampionIconUrl(c.id);
                item.innerHTML = `
                    <img class="champ-img" src="${iconUrl}" onerror="this.style.opacity='0.2'" alt="${c.name}">
                    <span class="champ-name">${c.name}</span>
                `;

                if (!isBanned) {
                    item.addEventListener('click', () => {
                        selectChampion(c.id, c.name);
                    });
                }

                champGrid.appendChild(item);
            });
        }

        function selectChampion(id, name) {
            selectedChampId = id;
            selectedChampName = name;

            csPreviewName.innerText = name;
            csPreviewImg.src = getChampionIconUrl(id);
            csPreviewImg.style.display = 'block';

            // Send hover intent to LCU if active action exists
            if (currentChampSelect && currentChampSelect.activeAction) {
                csLockBtn.disabled = false;
                if (socket && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        type: 'CHAMP_SELECT_ACTION',
                        token: sessionToken,
                        actionId: currentChampSelect.activeAction.id,
                        championId: id,
                        completed: false
                    }));
                }
            }

            renderChampGrid();
        }

        champSearchInput.addEventListener('input', () => {
            renderChampGrid();
        });

        csLockBtn.addEventListener('click', () => {
            if (csLockBtn.disabled || !currentChampSelect || !currentChampSelect.activeAction || selectedChampId <= 0) {
                return;
            }

            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    type: 'CHAMP_SELECT_ACTION',
                    token: sessionToken,
                    actionId: currentChampSelect.activeAction.id,
                    championId: selectedChampId,
                    completed: true
                }));
                csLockBtn.innerText = 'KİLİTLENDİ ✓';
                csLockBtn.disabled = true;
            }
        });

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
                    } else if (data.type === 'CHAMP_SELECT_UPDATE') {
                        renderChampSelect(data.data);
                    } else if (data.type === 'GRID_CHAMPIONS') {
                        gridChampions = data.data || [];
                        renderChampGrid();
                    } else if (data.type === 'READY_CHECK_ACCEPTED') {
                        if (data.success) {
                            acceptBtn.innerText = '✓ KABUL EDİLDİ';
                            acceptBtn.classList.remove('active');
                            acceptBtn.classList.add('accepted');
                            infoMessage.innerText = 'Maç kabul edildi! Şampiyon seçimi bekleniyor.';
                        }
                    } else if (data.type === 'ACTION_RESULT') {
                        if (data.success && data.completed) {
                            csLockBtn.innerText = 'KİLİTLENDİ ✓';
                            csLockBtn.disabled = true;
                            triggerVibrate();
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

        // Auto re-sync on tab / lock screen wake
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                if (!socket || socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING) {
                    if (localStorage.getItem('riftpilot_session_token')) {
                        connectWebSocket();
                    }
                } else if (socket.readyState === WebSocket.OPEN) {
                    try {
                        socket.send(JSON.stringify({ type: 'GET_STATE', token: sessionToken }));
                        if (champSelectView.style.display !== 'none') {
                            socket.send(JSON.stringify({ type: 'GET_CHAMP_SELECT', token: sessionToken }));
                        }
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


class ChampSelectActionRequest(BaseModel):
    """Payload schema for performing remote champion select actions."""

    actionId: int = Field(..., description="Action ID to hover or lock in")
    championId: int = Field(..., description="Champion ID to select")
    completed: bool = Field(default=True, description="True to lock in, False to hover")


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

    async def broadcast_champ_select(session: ChampSelectSession | None) -> None:
        """Broadcast live champ select session state to all active WebSocket clients."""
        payload: dict[str, Any] = {
            "type": "CHAMP_SELECT_UPDATE",
            "data": session.to_dict() if session is not None else None,
        }
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

    @app.get("/api/champ-select")
    async def get_champ_select_state() -> dict[str, Any]:
        """Fetch current champion select session state."""
        if lcu_adapter is None:
            return {"session": None}
        session = await lcu_adapter.get_champ_select_session()
        return {"session": session.to_dict() if session is not None else None}

    @app.get("/api/champ-select/grid")
    async def get_champ_select_grid() -> dict[str, Any]:
        """Fetch list of selectable and bannable grid champions."""
        if lcu_adapter is None:
            return {"champions": []}
        champions = await lcu_adapter.get_grid_champions()
        return {"champions": [c.to_dict() for c in champions]}

    @app.post("/api/champ-select/action")
    async def perform_champ_select_action(
        payload: ChampSelectActionRequest,
    ) -> dict[str, Any]:
        """Hover or lock-in a pick or ban action."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.patch_champ_select_action(
            action_id=payload.actionId,
            champion_id=payload.championId,
            completed=payload.completed,
        )
        return {"success": bool(success)}

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
                elif cmd_type == "GET_CHAMP_SELECT":
                    cs_data = None
                    if lcu_adapter is not None:
                        cs = await lcu_adapter.get_champ_select_session()
                        if cs is not None:
                            cs_data = cs.to_dict()
                    await websocket.send_json(
                        {
                            "type": "CHAMP_SELECT_UPDATE",
                            "data": cs_data,
                        }
                    )
                elif cmd_type == "GET_GRID_CHAMPIONS":
                    grid_data = []
                    if lcu_adapter is not None:
                        champs = await lcu_adapter.get_grid_champions()
                        grid_data = [c.to_dict() for c in champs]
                    await websocket.send_json(
                        {
                            "type": "GRID_CHAMPIONS",
                            "data": grid_data,
                        }
                    )
                elif cmd_type == "CHAMP_SELECT_ACTION":
                    action_id = int(data.get("actionId", 0))
                    champion_id = int(data.get("championId", 0))
                    completed = bool(data.get("completed", True))
                    success = False
                    if lcu_adapter is not None and action_id > 0:
                        success = await lcu_adapter.patch_champ_select_action(
                            action_id=action_id,
                            champion_id=champion_id,
                            completed=completed,
                        )
                    await websocket.send_json(
                        {
                            "type": "ACTION_RESULT",
                            "actionId": action_id,
                            "championId": champion_id,
                            "completed": completed,
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


__all__ = ["INDEX_HTML", "ChampSelectActionRequest", "PairRequest", "create_app"]
