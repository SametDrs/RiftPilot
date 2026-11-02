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
from typing import Any, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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

INDEX_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <meta name="theme-color" content="#060910">
    <title>RiftPilot — Hextech League Companion</title>
    <style>
        :root {
            /* Palette Design Tokens (LeagueCompanion / Hextech Dark) */
            --bg-abyss: #060910;
            --bg-primary: #0a101d;
            --bg-secondary: #0e172a;
            --card-bg: rgba(15, 23, 42, 0.85);
            --card-border: rgba(200, 170, 110, 0.22);
            --card-border-active: rgba(10, 200, 185, 0.55);

            --gold-1: #f0e6d2;
            --gold-2: #c8aa6e;
            --gold-3: #785a28;
            --gold-4: rgba(200, 170, 110, 0.15);

            --arcane-cyan: #0ac8b9;
            --arcane-cyan-glow: rgba(10, 200, 185, 0.35);
            --blue-team: #2480e6;
            --red-team: #e84057;

            --text-primary: #f0e6d2;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;

            --success: #00ff88;
            --danger: #e84057;

            --radius-sm: 8px;
            --radius-md: 12px;
            --radius-lg: 16px;
            --radius-pill: 9999px;

            --nav-height: 64px;
            --transition-fast: 0.18s cubic-bezier(0.4, 0, 0.2, 1);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            user-select: none;
            -webkit-user-select: none;
            -webkit-tap-highlight-color: transparent;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
        }

        body {
            background: radial-gradient(circle at 50% 0%, #111c33 0%, var(--bg-abyss) 85%);
            color: var(--text-primary);
            min-height: 100vh;
            min-height: -webkit-fill-available;
            display: flex;
            flex-direction: column;
            align-items: center;
            overflow-x: hidden;
            padding: 12px 12px calc(var(--nav-height) + 16px) 12px;
        }

        /* Top App Bar Header */
        header {
            width: 100%;
            max-width: 600px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 4px 14px 4px;
            border-bottom: 1px solid var(--card-border);
            margin-bottom: 12px;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .brand-icon {
            font-size: 24px;
            filter: drop-shadow(0 0 8px var(--arcane-cyan-glow));
        }

        .brand-title {
            font-size: 19px;
            font-weight: 800;
            letter-spacing: 0.5px;
            background: linear-gradient(180deg, var(--gold-1) 0%, var(--gold-2) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .lcu-subbadge {
            font-size: 10px;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            gap: 4px;
            font-weight: 600;
        }

        .lcu-subbadge.connected {
            color: var(--success);
        }

        .status-badge {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 11px;
            font-weight: 700;
            padding: 5px 12px;
            border-radius: var(--radius-pill);
            background: rgba(14, 23, 42, 0.8);
            border: 1px solid var(--card-border);
            color: var(--text-secondary);
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #64748b;
        }

        .status-badge.connected .status-dot {
            background: var(--arcane-cyan);
            box-shadow: 0 0 10px var(--arcane-cyan);
        }

        .status-badge.connected {
            border-color: var(--arcane-cyan);
            color: var(--gold-1);
        }

        .status-badge.error .status-dot {
            background: var(--danger);
            box-shadow: 0 0 10px var(--danger);
        }

        main {
            width: 100%;
            max-width: 600px;
            display: flex;
            flex-direction: column;
            gap: 14px;
            flex: 1;
        }

        /* Tab Content Containers */
        .tab-panel {
            width: 100%;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        /* Standard Gameflow View */
        #mainView {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 18px;
            padding: 8px 0;
        }

        .phase-card {
            width: 100%;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-lg);
            padding: 24px;
            text-align: center;
            box-shadow: 0 8px 30px rgba(0, 0, 0, 0.45);
            transition: all 0.3s ease;
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
        }

        .phase-card.ready-check-active {
            border-color: var(--gold-2);
            box-shadow: 0 0 35px rgba(200, 170, 110, 0.45);
            animation: pulse-border 1.5s infinite alternate;
        }

        @keyframes pulse-border {
            0% { border-color: var(--gold-3); box-shadow: 0 0 16px rgba(200, 170, 110, 0.25); }
            100% { border-color: var(--gold-1); box-shadow: 0 0 40px rgba(200, 170, 110, 0.7); }
        }

        .phase-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 2px;
            color: var(--gold-2);
            font-weight: 700;
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
            gap: 14px;
        }

        .btn-accept {
            width: 100%;
            height: 80px;
            border-radius: var(--radius-lg);
            font-size: 20px;
            font-weight: 800;
            letter-spacing: 2px;
            text-transform: uppercase;
            border: 2px solid var(--gold-2);
            background: linear-gradient(180deg, #16243d 0%, #0d1527 100%);
            color: var(--gold-1);
            cursor: pointer;
            outline: none;
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.4);
            transition: transform 0.15s ease, box-shadow 0.2s ease;
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
            color: #070b13;
            border-color: #f0e6d2;
            box-shadow: 0 0 30px rgba(200, 170, 110, 0.8);
            animation: pulse-btn 1s infinite alternate;
        }

        .btn-accept.accepted {
            background: linear-gradient(180deg, #00b060 0%, #006030 100%);
            color: white;
            border-color: var(--success);
            box-shadow: 0 0 30px rgba(0, 255, 136, 0.6);
            animation: none;
        }

        .btn-accept:disabled {
            opacity: 0.45;
            cursor: not-allowed;
            animation: none;
        }

        @keyframes pulse-btn {
            0% { transform: scale(1); filter: brightness(1); }
            100% { transform: scale(1.02); filter: brightness(1.25); }
        }

        .info-card {
            width: 100%;
            font-size: 12px;
            color: var(--text-secondary);
            text-align: center;
            line-height: 1.6;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-md);
            padding: 12px;
        }

        /* Mode-Aware Top Banner */
        .game-mode-banner {
            width: 100%;
            background: linear-gradient(90deg, rgba(200, 170, 110, 0.15) 0%, rgba(10, 200, 185, 0.12) 100%);
            border: 1px solid var(--gold-2);
            border-radius: var(--radius-md);
            padding: 10px 14px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            backdrop-filter: blur(8px);
        }

        .game-mode-name {
            font-weight: 800;
            color: var(--gold-1);
            letter-spacing: 0.8px;
            text-transform: uppercase;
            font-size: 13px;
        }

        .game-mode-stage {
            font-size: 11px;
            color: var(--arcane-cyan);
            font-weight: 800;
            letter-spacing: 0.5px;
            text-transform: uppercase;
            background: rgba(10, 200, 185, 0.14);
            border: 1px solid var(--arcane-cyan);
            border-radius: var(--radius-pill);
            padding: 3px 10px;
        }

        .error-toast {
            width: 100%;
            background: rgba(232, 64, 87, 0.22);
            border: 1px solid var(--danger);
            border-radius: var(--radius-md);
            padding: 10px 14px;
            color: #ff9aa8;
            font-size: 12px;
            font-weight: 700;
            text-align: center;
            display: none;
            animation: fadeIn 0.2s ease-out;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(-4px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* Champion Select Top Bar */
        .cs-top-bar {
            width: 100%;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-md);
            padding: 10px 14px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            backdrop-filter: blur(8px);
        }

        .cs-badges {
            display: flex;
            gap: 8px;
            align-items: center;
        }

        .cs-badge {
            font-size: 11px;
            font-weight: 800;
            padding: 4px 10px;
            border-radius: var(--radius-pill);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .cs-badge.pick {
            background: rgba(10, 200, 185, 0.18);
            border: 1px solid var(--arcane-cyan);
            color: var(--arcane-cyan);
        }

        .cs-badge.ban {
            background: rgba(232, 64, 87, 0.2);
            border: 1px solid var(--danger);
            color: var(--danger);
        }

        .cs-badge.pos {
            background: rgba(200, 170, 110, 0.15);
            border: 1px solid var(--gold-2);
            color: var(--gold-1);
        }

        .cs-timer-badge {
            font-size: 22px;
            font-weight: 800;
            color: var(--gold-1);
            min-width: 48px;
            text-align: right;
            font-family: "Cascadia Code", "Consolas", monospace;
        }

        .cs-progress-container {
            width: 100%;
            height: 4px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 2px;
            overflow: hidden;
            margin-top: -6px;
        }

        .cs-progress-bar {
            height: 100%;
            width: 100%;
            background: var(--gold-2);
            transition: width 0.3s linear, background-color 0.3s ease;
        }

        /* Teams Summary Grid */
        .teams-summary {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
            width: 100%;
        }

        .team-box {
            background: rgba(15, 23, 42, 0.7);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-md);
            padding: 8px 10px;
            font-size: 11px;
        }

        .team-box.blue {
            border-left: 3px solid var(--blue-team);
        }

        .team-box.red {
            border-left: 3px solid var(--red-team);
        }

        .team-header-title {
            font-weight: 800;
            margin-bottom: 6px;
            color: var(--gold-2);
            text-transform: uppercase;
            font-size: 10px;
            letter-spacing: 0.5px;
        }

        .member-row {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 4px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
        }

        .member-row:last-child {
            border-bottom: none;
        }

        .member-pos {
            font-size: 9px;
            font-weight: 700;
            color: var(--text-muted);
            width: 18px;
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

        .member-row.is-me .member-champ {
            color: var(--arcane-cyan);
            font-weight: 800;
        }

        /* Search Box & Role Filter Pills */
        .champ-search-box {
            width: 100%;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .champ-search-input {
            width: 100%;
            background: var(--card-bg);
            border: 1px solid var(--gold-3);
            border-radius: var(--radius-md);
            padding: 12px 16px;
            color: var(--text-primary);
            font-size: 14px;
            font-weight: 600;
            outline: none;
            box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.3);
            transition: border-color var(--transition-fast);
        }

        .champ-search-input:focus {
            border-color: var(--arcane-cyan);
            box-shadow: 0 0 12px var(--arcane-cyan-glow);
        }

        .role-filters-row {
            display: flex;
            gap: 6px;
            overflow-x: auto;
            padding-bottom: 4px;
            scrollbar-width: none;
        }
        .role-filters-row::-webkit-scrollbar {
            display: none;
        }

        .role-pill {
            background: rgba(15, 23, 42, 0.9);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-pill);
            color: var(--text-secondary);
            font-size: 11px;
            font-weight: 700;
            padding: 4px 10px;
            cursor: pointer;
            white-space: nowrap;
            transition: all var(--transition-fast);
        }

        .role-pill.active {
            background: rgba(10, 200, 185, 0.2);
            border-color: var(--arcane-cyan);
            color: var(--arcane-cyan);
        }

                /* Mobile-First Champion Select Container Fix */
        #champSelectView {
            display: none;
            flex-direction: column;
            width: 100%;
            max-width: 100%;
            gap: 12px;
            box-sizing: border-box;
            overflow-x: hidden;
        }

        @media (max-width: 480px) {
            .teams-summary {
                grid-template-columns: 1fr !important;
            }
        }

        /* Queue Mode Selector Card */
        .mode-selector-card {
            background: rgba(15, 23, 42, 0.75);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-md);
            padding: 14px;
            margin-top: 12px;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }

        .mode-card-title {
            font-size: 11px;
            font-weight: 800;
            color: var(--gold-2);
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }

        .mode-chips-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
        }

        .mode-chip {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            color: var(--text-secondary);
            border-radius: var(--radius-sm);
            padding: 10px 8px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            text-align: center;
            min-height: 44px;
            touch-action: manipulation;
        }

        .mode-chip.active {
            background: rgba(10, 200, 185, 0.15);
            border-color: var(--arcane-cyan);
            color: var(--arcane-cyan);
            box-shadow: 0 0 10px var(--arcane-cyan-glow);
        }

        .lane-selector-section {
            margin-top: 12px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .lane-section-header {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            padding: 0 2px;
        }

        .lane-section-title {
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 1.2px;
            color: var(--gold-1);
            text-transform: uppercase;
        }

        .lane-section-hint {
            font-size: 10px;
            color: var(--text-muted);
        }

        .lane-chips-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 6px;
        }

        .lane-chip {
            position: relative;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid var(--card-border);
            color: var(--text-secondary);
            border-radius: var(--radius-sm);
            padding: 8px 4px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            text-align: center;
            min-height: 44px;
            touch-action: manipulation;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 2px;
            box-sizing: border-box;
            overflow: hidden;
        }

        .lane-chip:hover {
            border-color: rgba(200, 155, 60, 0.4);
            background: rgba(255, 255, 255, 0.07);
        }

        .lane-chip-icon {
            font-size: 13px;
            line-height: 1;
        }

        .lane-chip-label {
            font-size: 10px;
            letter-spacing: 0.2px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 100%;
        }

        .lane-chip-badge {
            position: absolute;
            top: 2px;
            right: 4px;
            font-size: 8px;
            font-weight: 800;
            padding: 1px 4px;
            border-radius: 8px;
            display: none;
        }

        .lane-chip.primary {
            background: rgba(200, 155, 60, 0.18);
            border-color: var(--gold-1);
            color: var(--gold-1);
            box-shadow: 0 0 10px rgba(200, 155, 60, 0.25);
        }

        .lane-chip.primary .lane-chip-badge {
            display: block;
            background: var(--gold-1);
            color: #03060a;
        }

        .lane-chip.secondary {
            background: rgba(10, 200, 185, 0.15);
            border-color: var(--arcane-cyan);
            color: var(--arcane-cyan);
            box-shadow: 0 0 10px var(--arcane-cyan-glow);
        }

        .lane-chip.secondary .lane-chip-badge {
            display: block;
            background: var(--arcane-cyan);
            color: #03060a;
        }

        .lane-disabled-notice {
            margin-top: 10px;
            padding: 10px 12px;
            background: rgba(255, 255, 255, 0.03);
            border: 1px dashed rgba(255, 255, 255, 0.15);
            border-radius: var(--radius-sm);
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 11px;
            color: var(--text-muted);
        }

        .lane-disabled-notice .notice-icon {
            font-size: 14px;
        }

        .btn-start-queue {
            background: linear-gradient(135deg, var(--gold-1), var(--gold-2));
            color: #03060a;
            border: none;
            border-radius: var(--radius-md);
            padding: 14px;
            font-size: 15px;
            font-weight: 800;
            cursor: pointer;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            min-height: 48px;
            touch-action: manipulation;
            transition: transform 0.1s ease, box-shadow 0.2s ease;
        }

        .btn-start-queue:active {
            transform: scale(0.98);
        }

        /* Live Queue Timer Card */
        .queue-timer-card {
            background: rgba(10, 16, 29, 0.85);
            border: 1px solid var(--arcane-cyan);
            box-shadow: 0 0 20px rgba(10, 200, 185, 0.2);
            border-radius: var(--radius-md);
            padding: 16px;
            margin-top: 12px;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 8px;
            text-align: center;
        }

        .queue-timer-header {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 11px;
            font-weight: 800;
            color: var(--arcane-cyan);
            letter-spacing: 1px;
        }

        .queue-pulse-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--arcane-cyan);
            box-shadow: 0 0 8px var(--arcane-cyan);
            animation: pulse-dot 1.5s infinite;
        }

        @keyframes pulse-dot {
            0% { transform: scale(0.9); opacity: 0.7; }
            50% { transform: scale(1.3); opacity: 1; }
            100% { transform: scale(0.9); opacity: 0.7; }
        }

        .queue-timer-clock {
            font-size: 36px;
            font-weight: 900;
            letter-spacing: 2px;
            color: #ffffff;
            font-variant-numeric: tabular-nums;
            text-shadow: 0 0 12px var(--arcane-cyan-glow);
        }

        .queue-estimated-clock {
            font-size: 12px;
            color: var(--text-muted);
            font-variant-numeric: tabular-nums;
        }

        .btn-cancel-queue {
            background: rgba(232, 64, 87, 0.15);
            border: 1px solid #e84057;
            color: #e84057;
            border-radius: var(--radius-sm);
            padding: 10px 20px;
            font-size: 13px;
            font-weight: 700;
            cursor: pointer;
            margin-top: 6px;
            min-height: 44px;
            width: 100%;
            touch-action: manipulation;
        }

        .btn-cancel-queue:active {
            background: rgba(232, 64, 87, 0.3);
        }

        /* Trade & Lane Swap Notification Banner */
        .trade-notice-banner {
            background: rgba(15, 23, 42, 0.95);
            border: 1px solid var(--gold-2);
            box-shadow: 0 0 16px rgba(200, 155, 60, 0.25);
            border-radius: var(--radius-md);
            padding: 12px 14px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            animation: slideDown 0.25s ease;
        }

        .trade-notice-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .trade-notice-type {
            font-size: 11px;
            font-weight: 800;
            color: var(--gold-1);
            letter-spacing: 0.5px;
        }

        .trade-notice-sender {
            font-size: 11px;
            color: var(--text-secondary);
        }

        .trade-notice-text {
            font-size: 13px;
            color: var(--text-primary);
            font-weight: 600;
        }

        .trade-notice-actions {
            display: flex;
            gap: 8px;
        }

        .trade-btn-accept {
            flex: 1;
            background: linear-gradient(135deg, var(--arcane-cyan), #0a998c);
            color: #03060a;
            border: none;
            border-radius: var(--radius-sm);
            padding: 10px;
            font-weight: 800;
            font-size: 12px;
            cursor: pointer;
            min-height: 44px;
            touch-action: manipulation;
        }

        .trade-btn-decline {
            flex: 1;
            background: rgba(232, 64, 87, 0.2);
            border: 1px solid #e84057;
            color: #e84057;
            border-radius: var(--radius-sm);
            padding: 10px;
            font-weight: 800;
            font-size: 12px;
            cursor: pointer;
            min-height: 44px;
            touch-action: manipulation;
        }

        .trade-btn-cancel {
            width: 100%;
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid var(--card-border);
            color: var(--text-secondary);
            border-radius: var(--radius-sm);
            padding: 10px;
            font-weight: 700;
            font-size: 12px;
            cursor: pointer;
            min-height: 44px;
            touch-action: manipulation;
        }

        /* Quick Swap Buttons in Member Rows */
        .member-swap-actions {
            display: flex;
            gap: 4px;
            margin-left: auto;
        }

        .btn-member-swap {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid var(--card-border);
            color: var(--text-secondary);
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 10px;
            font-weight: 700;
            cursor: pointer;
            min-height: 32px;
            touch-action: manipulation;
            transition: all 0.15s ease;
        }

        .btn-member-swap:hover, .btn-member-swap:active {
            border-color: var(--arcane-cyan);
            color: var(--arcane-cyan);
        }

        /* Champion Grid */
        .champ-grid-container {
            width: 100%;
            max-height: 250px;
            overflow-y: auto;
            border: 1px solid var(--card-border);
            border-radius: var(--radius-md);
            background: rgba(10, 16, 29, 0.75);
            padding: 8px;
        }

        .champ-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(64px, 1fr));
            gap: 6px;
        }

        .champ-item {
            position: relative;
            display: flex;
            flex-direction: column;
            align-items: center;
            background: rgba(15, 23, 42, 0.95);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-sm);
            padding: 4px;
            cursor: pointer;
            transition: all 0.15s ease;
        }

        .champ-item:active {
            transform: scale(0.94);
        }

        .champ-item.selected {
            border-color: var(--arcane-cyan);
            box-shadow: 0 0 14px var(--arcane-cyan-glow);
            background: rgba(10, 200, 185, 0.15);
        }

        .champ-item.banned, .champ-item.unavailable {
            opacity: 0.35;
            filter: grayscale(1);
            cursor: not-allowed;
            pointer-events: none;
        }

        .champ-ban-badge {
            position: absolute;
            top: 2px;
            right: 2px;
            background: rgba(232, 64, 87, 0.95);
            color: white;
            font-size: 8px;
            font-weight: 800;
            padding: 1px 4px;
            border-radius: 3px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .champ-img {
            width: 48px;
            height: 48px;
            border-radius: 6px;
            object-fit: cover;
            background: #080d16;
        }

        .champ-name {
            font-size: 10px;
            color: var(--text-primary);
            text-align: center;
            margin-top: 4px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            width: 100%;
            font-weight: 600;
        }

        /* Bottom Floating / Fixed Action Panel (Ergonomic Thumb Reach) */
        .cs-action-panel {
            width: 100%;
            display: flex;
            flex-direction: column;
            gap: 8px;
            background: rgba(15, 23, 42, 0.95);
            border: 1px solid var(--gold-2);
            border-radius: var(--radius-lg);
            padding: 10px 14px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
            backdrop-filter: blur(12px);
        }

        .cs-action-row {
            width: 100%;
            display: flex;
            gap: 10px;
            align-items: center;
        }

        .cs-selected-preview {
            display: flex;
            align-items: center;
            gap: 10px;
            flex: 1;
            overflow: hidden;
        }

        .cs-preview-img {
            width: 44px;
            height: 44px;
            border-radius: var(--radius-sm);
            border: 1px solid var(--gold-2);
            object-fit: cover;
            background: #080d16;
        }

        .cs-preview-name {
            font-size: 14px;
            font-weight: 800;
            color: var(--gold-1);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .cs-btn-lock, .cs-btn-ban {
            height: 48px;
            min-width: 110px;
            padding: 0 18px;
            border-radius: var(--radius-md);
            font-size: 15px;
            font-weight: 800;
            letter-spacing: 1px;
            text-transform: uppercase;
            cursor: pointer;
            outline: none;
            transition: all var(--transition-fast);
        }

        .cs-btn-ban {
            background: linear-gradient(180deg, #e84057 0%, #8a1f2e 100%);
            border: 1px solid #ff7888;
            color: white;
            box-shadow: 0 0 15px rgba(232, 64, 87, 0.45);
        }

        .cs-btn-lock {
            background: linear-gradient(180deg, #0ac8b9 0%, #00605a 100%);
            border: 1px solid #6efff4;
            color: white;
            box-shadow: 0 0 15px rgba(10, 200, 185, 0.45);
        }

        .cs-btn-lock:active:not(:disabled), .cs-btn-ban:active:not(:disabled) {
            transform: scale(0.96);
        }

        .cs-btn-lock:disabled, .cs-btn-ban:disabled {
            opacity: 0.35;
            cursor: not-allowed;
            background: #1e293b;
            border-color: var(--card-border);
            color: var(--text-muted);
            box-shadow: none;
        }

        .cs-action-reason {
            font-size: 11px;
            color: var(--text-secondary);
            text-align: right;
            font-weight: 600;
        }

        /* Loadout (Spells & 3 Official Recommended Rune Pages) */
        .loadout-bar {
            width: 100%;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-lg);
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            backdrop-filter: blur(10px);
        }

        .loadout-top-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            width: 100%;
        }

        .spells-container {
            display: flex;
            gap: 10px;
            align-items: center;
        }

        .spell-slot {
            position: relative;
            width: 48px;
            height: 48px;
            border-radius: var(--radius-md);
            border: 2px solid var(--gold-2);
            background: #000;
            cursor: pointer;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all var(--transition-fast);
        }

        .spell-slot:active {
            transform: scale(0.92);
            border-color: var(--arcane-cyan);
        }

        .spell-img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .spell-key-badge {
            position: absolute;
            bottom: 2px;
            right: 2px;
            background: rgba(0, 0, 0, 0.8);
            color: var(--gold-1);
            font-size: 10px;
            font-weight: 800;
            padding: 1px 4px;
            border-radius: 4px;
            border: 1px solid var(--card-border);
            pointer-events: none;
        }

        /* 3 Recommended Rune Pages Section (Redesigned Vertical Stack) */
        .runes-section {
            width: 100%;
            display: flex;
            flex-direction: column;
            gap: 10px;
            border-top: 1px solid rgba(200, 170, 110, 0.2);
            padding-top: 10px;
        }

        .runes-header-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .runes-title {
            font-size: 11px;
            font-weight: 800;
            color: var(--gold-2);
            letter-spacing: 0.8px;
            text-transform: uppercase;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .runes-cards-grid {
            display: flex;
            flex-direction: column;
            gap: 10px;
            width: 100%;
        }

        .rune-card {
            background: linear-gradient(135deg, rgba(16, 25, 46, 0.95) 0%, rgba(9, 15, 28, 0.95) 100%);
            border: 1.5px solid rgba(200, 170, 110, 0.22);
            border-radius: 14px;
            padding: 12px 14px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 12px;
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
            min-height: 68px;
            user-select: none;
            -webkit-tap-highlight-color: transparent;
            box-shadow: 0 4px 18px rgba(0, 0, 0, 0.4);
            position: relative;
            overflow: hidden;
        }

        .rune-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 3px;
            height: 100%;
            background: transparent;
            transition: background 0.2s ease;
        }

        .rune-card:active {
            transform: scale(0.985);
        }

        .rune-card.selected {
            border-color: #0ac8b9;
            background: linear-gradient(135deg, rgba(10, 200, 185, 0.16) 0%, rgba(13, 24, 40, 0.96) 100%);
            box-shadow: 0 6px 24px rgba(10, 200, 185, 0.28), inset 0 0 14px rgba(10, 200, 185, 0.08);
        }

        .rune-card.selected::before {
            background: #0ac8b9;
            box-shadow: 0 0 10px #0ac8b9;
        }

        .rune-card-left {
            position: relative;
            flex-shrink: 0;
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(200, 170, 110, 0.18) 0%, #060b14 100%);
            border: 2px solid rgba(200, 170, 110, 0.45);
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.6);
            transition: all 0.2s ease;
        }

        .rune-card.selected .rune-card-left {
            border-color: #0ac8b9;
            box-shadow: 0 0 12px rgba(10, 200, 185, 0.6);
            background: radial-gradient(circle, rgba(10, 200, 185, 0.25) 0%, #060b14 100%);
        }

        .rune-card-keystone-icon {
            width: 40px;
            height: 40px;
            object-fit: contain;
            border-radius: 50%;
            filter: drop-shadow(0 1px 3px rgba(0, 0, 0, 0.8));
        }

        .rune-card-body {
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 4px;
            min-width: 0;
        }

        .rune-card-top-line {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }

        .rune-card-keystone-title {
            font-size: 13.5px;
            font-weight: 800;
            color: #f0e6d2;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            letter-spacing: 0.3px;
        }

        .rune-card.selected .rune-card-keystone-title {
            color: #ffffff;
            text-shadow: 0 0 10px rgba(10, 200, 185, 0.6);
        }

        .rune-card-tree-badge {
            font-size: 10px;
            font-weight: 800;
            color: #0ac8b9;
            background: rgba(10, 200, 185, 0.12);
            padding: 3px 8px;
            border-radius: 12px;
            border: 1px solid rgba(10, 200, 185, 0.35);
            white-space: nowrap;
            flex-shrink: 0;
            letter-spacing: 0.4px;
        }

        .rune-card-summary {
            font-size: 11px;
            color: #94a3b8;
            line-height: 1.4;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .rune-card-radio {
            width: 24px;
            height: 24px;
            border-radius: 50%;
            border: 2px solid rgba(200, 170, 110, 0.3);
            flex-shrink: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 13px;
            font-weight: 900;
            color: transparent;
            transition: all 0.2s ease;
            background: rgba(14, 23, 42, 0.5);
        }

        .rune-card.selected .rune-card-radio {
            border-color: #0ac8b9;
            background: #0ac8b9;
            color: #040910;
            box-shadow: 0 0 10px rgba(10, 200, 185, 0.6);
        }

        /* 4th Card: + Kendi Rünümü Oluştur (Özel Rün) */
        .rune-builder-trigger-card {
            background: linear-gradient(135deg, rgba(22, 34, 58, 0.75) 0%, rgba(13, 21, 38, 0.85) 100%);
            border: 1.5px solid rgba(200, 170, 110, 0.45);
            border-radius: 14px;
            padding: 12px 16px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 12px;
            transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
            min-height: 66px;
            user-select: none;
            -webkit-tap-highlight-color: transparent;
            box-shadow: 0 4px 18px rgba(0, 0, 0, 0.35);
        }

        .rune-builder-trigger-card:hover,
        .rune-builder-trigger-card:active {
            border-color: #f0e6d2;
            background: linear-gradient(135deg, rgba(32, 48, 80, 0.85) 0%, rgba(18, 28, 50, 0.9) 100%);
            box-shadow: 0 6px 22px rgba(200, 170, 110, 0.35);
            transform: scale(0.985);
        }

        .rune-builder-trigger-icon {
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(200, 170, 110, 0.25) 0%, rgba(10, 200, 185, 0.1) 100%);
            border: 1.5px solid #c8aa6e;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 19px;
            flex-shrink: 0;
            color: #f0e6d2;
            box-shadow: 0 0 12px rgba(200, 170, 110, 0.3);
        }

        .rune-builder-trigger-content {
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 2px;
            min-width: 0;
        }

        .rune-builder-trigger-title {
            font-size: 13.5px;
            font-weight: 800;
            color: #f0e6d2;
            letter-spacing: 0.3px;
        }

        .rune-builder-trigger-subtitle {
            font-size: 11px;
            color: #94a3b8;
            line-height: 1.3;
        }

        .rune-builder-trigger-arrow {
            font-size: 16px;
            color: #c8aa6e;
            font-weight: 800;
            padding-right: 4px;
            transition: transform 0.2s ease;
        }

        .rune-builder-trigger-card:hover .rune-builder-trigger-arrow {
            transform: translateX(3px);
        }

        /* Custom Rune Builder Modal Overlay (#customRuneModal) */
        .custom-rune-modal {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(3, 7, 18, 0.88);
            z-index: 1200;
            display: flex;
            align-items: flex-end;
            justify-content: center;
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
        }

        @media (min-width: 768px) {
            .custom-rune-modal {
                align-items: center;
                padding: 20px;
            }
        }

        .custom-rune-modal-content {
            background: #070d18;
            border-top: 2px solid #c8aa6e;
            border-radius: 22px 22px 0 0;
            width: 100%;
            max-width: 680px;
            max-height: 94vh;
            display: flex;
            flex-direction: column;
            box-shadow: 0 -8px 40px rgba(0, 0, 0, 0.85);
            animation: slideUp 0.24s cubic-bezier(0.16, 1, 0.3, 1);
            overflow: hidden;
        }

        @media (min-width: 768px) {
            .custom-rune-modal-content {
                border-radius: 18px;
                border: 2px solid #c8aa6e;
                max-height: 88vh;
            }
        }

        .custom-rune-modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 14px 18px;
            border-bottom: 1px solid var(--card-border);
            background: rgba(14, 23, 42, 0.7);
            flex-shrink: 0;
        }

        .custom-rune-header-title-box {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .custom-rune-header-icon {
            font-size: 18px;
            color: var(--gold-1);
        }

        .custom-rune-modal-title {
            font-size: 15px;
            font-weight: 800;
            color: var(--gold-1);
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }

        .custom-rune-tabs {
            display: flex;
            border-bottom: 1px solid var(--card-border);
            background: #0a1120;
            flex-shrink: 0;
        }

        .custom-rune-tab {
            flex: 1;
            background: transparent;
            border: none;
            border-bottom: 2px solid transparent;
            color: var(--text-secondary);
            font-size: 12px;
            font-weight: 700;
            padding: 11px 14px;
            cursor: pointer;
            transition: all var(--transition-fast);
            text-align: center;
        }

        .custom-rune-tab.active {
            color: var(--gold-1);
            border-bottom-color: var(--gold-1);
            background: rgba(200, 170, 110, 0.08);
        }

        .custom-rune-view {
            padding: 16px 18px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 14px;
            flex: 1;
        }

        .custom-rune-field {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .custom-rune-label {
            font-size: 11px;
            font-weight: 800;
            color: var(--gold-2);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .custom-rune-input {
            background: #0d1728;
            border: 1px solid var(--card-border);
            border-radius: 8px;
            padding: 10px 14px;
            font-size: 13px;
            color: var(--text-primary);
            font-family: inherit;
            outline: none;
            transition: border-color var(--transition-fast);
        }

        .custom-rune-input:focus {
            border-color: var(--arcane-cyan);
            box-shadow: 0 0 8px var(--arcane-cyan-glow);
        }

        .custom-rune-section {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .custom-rune-section-title {
            font-size: 10px;
            font-weight: 800;
            color: var(--gold-2);
            letter-spacing: 0.7px;
            text-transform: uppercase;
        }

        .trees-grid {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 6px;
        }

        .tree-btn {
            background: rgba(14, 23, 42, 0.88);
            border: 1.5px solid rgba(255, 255, 255, 0.08);
            border-radius: 10px;
            padding: 8px 3px;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 4px;
            cursor: pointer;
            transition: all var(--transition-fast);
            color: var(--text-secondary);
            min-height: 58px;
        }

        .tree-btn:active {
            transform: scale(0.95);
        }

        .tree-btn.selected {
            border-color: var(--tree-color, #c8aa6e);
            background: rgba(200, 170, 110, 0.18);
            color: #ffffff;
            box-shadow: 0 0 14px rgba(200, 170, 110, 0.35);
        }

        .tree-btn.disabled {
            opacity: 0.3;
            cursor: not-allowed;
            pointer-events: none;
        }

        .tree-btn-icon {
            width: 28px;
            height: 28px;
            object-fit: contain;
            filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.5));
        }

        .tree-btn-name {
            font-size: 9.5px;
            font-weight: 800;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 100%;
            text-align: center;
            letter-spacing: 0.2px;
        }

        .rune-slots-container {
            display: flex;
            flex-direction: column;
            gap: 10px;
            background: rgba(10, 17, 30, 0.65);
            border: 1px solid rgba(200, 170, 110, 0.15);
            border-radius: 12px;
            padding: 12px 10px;
        }

        .rune-slot-row {
            display: flex;
            align-items: center;
            justify-content: space-around;
            gap: 8px;
            padding: 4px 0;
        }

        .rune-item-btn {
            width: 46px;
            height: 46px;
            border-radius: 50%;
            background: #0d1728;
            border: 1.5px solid rgba(200, 170, 110, 0.25);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            position: relative;
            transition: all var(--transition-fast);
            padding: 4px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.5);
        }

        .rune-item-btn.keystone {
            width: 54px;
            height: 54px;
            border: 2px solid rgba(200, 170, 110, 0.45);
        }

        .rune-item-btn:active {
            transform: scale(0.92);
        }

        .rune-item-btn.selected {
            border-color: #f0e6d2;
            box-shadow: 0 0 16px rgba(200, 170, 110, 0.65);
            background: radial-gradient(circle, rgba(200, 170, 110, 0.28) 0%, #0d1728 100%);
        }

        .rune-item-img {
            width: 100%;
            height: 100%;
            object-fit: contain;
            border-radius: 50%;
        }

        .shard-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            padding: 4px 6px;
        }

        .shard-row-title {
            font-size: 11px;
            font-weight: 700;
            color: var(--text-secondary);
            width: 65px;
            flex-shrink: 0;
        }

        .shard-options {
            display: flex;
            gap: 12px;
            flex: 1;
            justify-content: flex-end;
        }

        .shard-btn {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: #0d1728;
            border: 1.5px solid var(--card-border);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all var(--transition-fast);
            padding: 4px;
        }

        .shard-btn.selected {
            border-color: var(--arcane-cyan);
            background: rgba(10, 200, 185, 0.2);
            box-shadow: 0 0 8px var(--arcane-cyan-glow);
        }

        .shard-img {
            width: 24px;
            height: 24px;
            object-fit: contain;
        }

        .custom-rune-alert {
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid #ef4444;
            border-radius: 8px;
            padding: 10px 12px;
            font-size: 11px;
            color: #fca5a5;
            line-height: 1.4;
            font-weight: 600;
        }

        .custom-rune-actions {
            display: flex;
            gap: 10px;
            margin-top: 4px;
            padding-bottom: 12px;
        }

        .btn-save-rune {
            flex: 1;
            background: #111d33;
            border: 1.5px solid var(--gold-2);
            border-radius: 8px;
            color: var(--gold-1);
            font-size: 12px;
            font-weight: 800;
            padding: 12px;
            cursor: pointer;
            transition: all var(--transition-fast);
            text-align: center;
        }

        .btn-save-rune:active {
            transform: scale(0.98);
        }

        .btn-apply-custom-rune {
            flex: 1;
            background: linear-gradient(135deg, #c8aa6e 0%, #785a28 100%);
            border: 1px solid var(--gold-1);
            border-radius: 8px;
            color: #070b13;
            font-size: 12px;
            font-weight: 800;
            padding: 12px;
            cursor: pointer;
            transition: all var(--transition-fast);
            text-align: center;
        }

        .btn-apply-custom-rune:active {
            transform: scale(0.98);
        }

        .saved-runes-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }

        .saved-rune-card {
            background: rgba(14, 23, 42, 0.9);
            border: 1px solid var(--card-border);
            border-radius: 10px;
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            transition: all var(--transition-fast);
        }

        .saved-rune-card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .saved-rune-card-title {
            font-size: 13px;
            font-weight: 800;
            color: var(--gold-1);
        }

        .saved-rune-trees {
            font-size: 10px;
            color: var(--arcane-cyan);
            font-weight: 700;
        }

        .saved-rune-perks {
            font-size: 11px;
            color: var(--text-secondary);
            line-height: 1.35;
        }

        .saved-rune-actions {
            display: flex;
            justify-content: flex-end;
            gap: 8px;
            margin-top: 4px;
        }

        .btn-saved-apply {
            background: rgba(10, 200, 185, 0.2);
            border: 1px solid var(--arcane-cyan);
            color: var(--arcane-cyan);
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            padding: 6px 12px;
            cursor: pointer;
        }

        .btn-saved-delete {
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid #ef4444;
            color: #fca5a5;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            padding: 6px 10px;
            cursor: pointer;
        }

        .saved-runes-empty {
            text-align: center;
            font-size: 12px;
            color: var(--text-secondary);
            padding: 30px 10px;
            line-height: 1.5;
        }

        .runes-empty-msg {
            font-size: 12px;
            color: var(--text-secondary);
            text-align: center;
            padding: 10px 0;
            line-height: 1.5;
        }

        .btn-apply-runes {
            background: linear-gradient(135deg, #c8aa6e 0%, #785a28 100%);
            border: 1px solid var(--gold-1);
            border-radius: var(--radius-sm);
            color: #070b13;
            font-weight: 800;
            font-size: 11px;
            padding: 9px 12px;
            cursor: pointer;
            white-space: nowrap;
            transition: all var(--transition-fast);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 4px;
        }

        .btn-apply-runes:disabled {
            opacity: 0.45;
            cursor: not-allowed;
            filter: grayscale(1);
        }

        .btn-apply-runes:active:not(:disabled) {
            transform: scale(0.96);
        }

        .btn-apply-runes.applied {
            background: linear-gradient(135deg, #00ff88 0%, #008844 100%);
            border-color: #00ff88;
            color: #070b13;
        }

        /* ARAM & Safe Mode Boxes */
        .aram-bench-box, .safe-mode-box {
            width: 100%;
            background: var(--card-bg);
            border: 1px solid var(--gold-2);
            border-radius: var(--radius-lg);
            padding: 14px;
            display: none;
            flex-direction: column;
            gap: 10px;
        }

        .aram-bench-title, .safe-mode-title {
            font-size: 12px;
            font-weight: 800;
            color: var(--gold-2);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .aram-bench-list {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }

        /* Spells Modal / Bottom Drawer */
        .spells-modal {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.8);
            z-index: 1000;
            display: flex;
            align-items: flex-end;
            justify-content: center;
            backdrop-filter: blur(6px);
            -webkit-backdrop-filter: blur(6px);
        }

        .spells-modal-content {
            background: #0b1322;
            border-top: 2px solid var(--gold-2);
            border-radius: 20px 20px 0 0;
            width: 100%;
            max-width: 600px;
            padding: 18px;
            box-shadow: 0 -8px 30px rgba(0, 0, 0, 0.6);
            animation: slideUp 0.22s ease-out;
        }

        @keyframes slideUp {
            from { transform: translateY(100%); }
            to { transform: translateY(0); }
        }

        .spells-modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--card-border);
        }

        .spells-modal-title {
            font-weight: 800;
            color: var(--gold-1);
            font-size: 15px;
        }

        .modal-close-btn {
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 26px;
            cursor: pointer;
            line-height: 1;
            padding: 4px;
        }

        .spells-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 10px;
            max-height: 320px;
            overflow-y: auto;
            padding-bottom: 8px;
        }

        .spell-select-item {
            display: flex;
            flex-direction: column;
            align-items: center;
            background: rgba(15, 23, 42, 0.95);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-md);
            padding: 10px 4px;
            cursor: pointer;
            transition: all var(--transition-fast);
            gap: 6px;
        }

        .spell-select-item:active {
            transform: scale(0.92);
            border-color: var(--arcane-cyan);
        }

        .spell-select-item.active {
            border-color: var(--gold-2);
            background: rgba(200, 170, 110, 0.15);
        }

        .spell-select-item img {
            width: 42px;
            height: 42px;
            border-radius: 8px;
            border: 1px solid var(--gold-3);
        }

        .spell-select-item span {
            font-size: 11px;
            color: var(--gold-1);
            font-weight: 700;
            text-align: center;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            width: 100%;
        }

        /* Modern Bottom Navigation Bar (Mobile Ergonomics) */
        nav.mobile-nav-bar {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            height: var(--nav-height);
            background: rgba(8, 13, 24, 0.95);
            border-top: 1px solid var(--card-border);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            display: flex;
            justify-content: space-around;
            align-items: center;
            z-index: 900;
            padding-bottom: env(safe-area-inset-bottom, 0px);
        }

        .nav-tab-btn {
            background: none;
            border: none;
            color: var(--text-secondary);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 3px;
            font-size: 11px;
            font-weight: 700;
            cursor: pointer;
            width: 33.33%;
            height: 100%;
            transition: color var(--transition-fast);
        }

        .nav-tab-icon {
            font-size: 18px;
        }

        .nav-tab-btn.active {
            color: var(--arcane-cyan);
        }

        .nav-tab-btn.active .nav-tab-icon {
            filter: drop-shadow(0 0 6px var(--arcane-cyan-glow));
        }

        /* Station / Status Panel */
        #stationView {
            display: none;
            width: 100%;
            flex-direction: column;
            gap: 12px;
        }

        .station-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-lg);
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }

        .station-card-title {
            font-size: 12px;
            font-weight: 800;
            color: var(--gold-2);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .station-info-row {
            display: flex;
            justify-content: space-between;
            font-size: 13px;
            padding: 4px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }
        .station-info-row:last-child { border-bottom: none; }

        .btn-action-outline {
            background: rgba(10, 200, 185, 0.1);
            border: 1px solid var(--arcane-cyan);
            border-radius: var(--radius-md);
            color: var(--arcane-cyan);
            font-size: 13px;
            font-weight: 700;
            padding: 10px;
            cursor: pointer;
            text-align: center;
        }

        /* Tablet & Landscape Responsive Optimizations */
        @media (min-width: 680px) {
            body { padding-top: 24px; }
            .teams-summary { gap: 14px; }
            .champ-grid { grid-template-columns: repeat(auto-fill, minmax(72px, 1fr)); }
            .champ-img { width: 54px; height: 54px; }
            .spells-grid { grid-template-columns: repeat(6, 1fr); }
        }

        @media (orientation: landscape) and (max-height: 500px) {
            body { padding-bottom: calc(var(--nav-height) + 8px); }
            .champ-grid-container { max-height: 140px; }
            .phase-card { padding: 14px; }
            .btn-accept { height: 60px; }
        }

        /* Ready Check Modal Overlay (#readyCheckModal) */
        .ready-check-modal {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            width: 100vw;
            height: 100vh;
            z-index: 9999;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 16px;
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            background: rgba(6, 9, 16, 0.88);
            animation: modalFadeIn 0.25s cubic-bezier(0.16, 1, 0.3, 1);
            overflow: hidden;
            box-sizing: border-box;
        }

        .ready-check-card {
            position: relative;
            background: radial-gradient(circle at 50% 20%, #152238 0%, #0a111e 90%);
            border: 2px solid var(--gold-2);
            border-radius: var(--radius-lg);
            padding: 26px 20px;
            width: 100%;
            max-width: 400px;
            display: flex;
            flex-direction: column;
            align-items: center;
            text-align: center;
            box-shadow: 0 0 50px rgba(200, 170, 110, 0.45), 0 20px 40px rgba(0, 0, 0, 0.8);
            animation: modalPopIn 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
            overflow: hidden;
            box-sizing: border-box;
        }

        .ready-check-glow {
            position: absolute;
            top: -50px;
            left: 50%;
            transform: translateX(-50%);
            width: 220px;
            height: 100px;
            background: radial-gradient(ellipse, rgba(10, 200, 185, 0.4) 0%, transparent 75%);
            pointer-events: none;
        }

        .ready-check-icon {
            font-size: 34px;
            margin-bottom: 6px;
            filter: drop-shadow(0 0 12px var(--arcane-cyan));
            animation: bounce-icon 1.2s infinite ease-in-out;
        }

        @keyframes bounce-icon {
            0%, 100% { transform: translateY(0); }
            50% { transform: translateY(-4px); }
        }

        .ready-check-title {
            font-size: 24px;
            font-weight: 900;
            letter-spacing: 2px;
            color: var(--gold-1);
            text-shadow: 0 0 16px rgba(240, 230, 210, 0.6);
            margin-bottom: 14px;
        }

        .ready-check-timer-wrapper {
            position: relative;
            width: 84px;
            height: 84px;
            margin: 4px 0 14px 0;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .ready-check-timer-ring {
            transform: rotate(-90deg);
        }

        .ring-track {
            fill: none;
            stroke: rgba(200, 170, 110, 0.15);
            stroke-width: 6;
        }

        .ring-bar {
            fill: none;
            stroke: var(--gold-2);
            stroke-width: 6;
            stroke-linecap: round;
            stroke-dasharray: 226;
            stroke-dashoffset: 0;
            transition: stroke-dashoffset 1s linear, stroke 0.3s ease;
            filter: drop-shadow(0 0 6px var(--gold-2));
        }

        .ready-check-countdown {
            position: absolute;
            font-size: 26px;
            font-weight: 800;
            color: var(--gold-1);
            font-variant-numeric: tabular-nums;
            text-shadow: 0 0 10px rgba(240, 230, 210, 0.5);
        }

        .ready-check-status {
            font-size: 13px;
            color: var(--text-secondary);
            margin-bottom: 20px;
            min-height: 20px;
        }

        .ready-check-actions {
            width: 100%;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .btn-ready-accept {
            width: 100%;
            height: 56px;
            min-height: 44px;
            min-width: 44px;
            border-radius: var(--radius-md);
            font-size: 18px;
            font-weight: 800;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            border: 2px solid #f0e6d2;
            background: linear-gradient(180deg, #c8aa6e 0%, #785a28 100%);
            color: #060910;
            cursor: pointer;
            box-shadow: 0 0 25px rgba(200, 170, 110, 0.7);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            touch-action: manipulation;
            transition: all var(--transition-fast);
            animation: pulse-ready-btn 1.2s infinite alternate ease-in-out;
            box-sizing: border-box;
        }

        @keyframes pulse-ready-btn {
            0% { transform: scale(1); box-shadow: 0 0 20px rgba(200, 170, 110, 0.6); }
            100% { transform: scale(1.02); box-shadow: 0 0 32px rgba(200, 170, 110, 0.95); }
        }

        .btn-ready-accept:active {
            transform: scale(0.97);
        }

        .btn-ready-accept.accepted {
            background: linear-gradient(180deg, #00b060 0%, #006030 100%);
            color: #ffffff;
            border-color: var(--success);
            box-shadow: 0 0 25px rgba(0, 255, 136, 0.6);
            animation: none;
        }

        .btn-ready-accept:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            animation: none;
            transform: none;
        }

        .btn-ready-decline {
            width: 100%;
            height: 44px;
            min-height: 44px;
            min-width: 44px;
            border-radius: var(--radius-md);
            font-size: 14px;
            font-weight: 700;
            letter-spacing: 1px;
            text-transform: uppercase;
            border: 1px solid rgba(232, 64, 87, 0.4);
            background: rgba(232, 64, 87, 0.1);
            color: #ff7588;
            cursor: pointer;
            touch-action: manipulation;
            transition: all var(--transition-fast);
            box-sizing: border-box;
        }

        .btn-ready-decline:hover {
            background: rgba(232, 64, 87, 0.2);
            border-color: rgba(232, 64, 87, 0.7);
        }

        .btn-ready-decline:active {
            transform: scale(0.97);
        }

        .btn-ready-decline:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }

        @keyframes modalFadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        @keyframes modalPopIn {
            from { opacity: 0; transform: scale(0.9) translateY(12px); }
            to { opacity: 1; transform: scale(1) translateY(0); }
        }

        /* ==========================================================================
           Post-Lock Match Lobby Screen (#postLockLobbyView)
           ========================================================================== */
        .post-lock-lobby-view {
            display: none;
            flex-direction: column;
            width: 100%;
            max-width: 100%;
            gap: 12px;
            box-sizing: border-box;
            overflow-x: hidden;
            animation: fadeInLobby 0.35s ease forwards;
        }

        @keyframes fadeInLobby {
            from {
                opacity: 0;
                transform: translateY(8px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        .lobby-header-banner {
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.9), rgba(10, 16, 29, 0.85));
            border: 1px solid var(--card-border);
            border-radius: var(--radius-md);
            padding: 10px 14px;
            box-sizing: border-box;
        }

        .lobby-header-status {
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .lobby-status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--gold-1);
            box-shadow: 0 0 8px var(--gold-1);
            animation: pulse-dot 1.5s infinite;
        }

        .lobby-header-title {
            font-size: 13px;
            font-weight: 800;
            color: var(--gold-1);
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }

        .lobby-timer-badge {
            background: rgba(200, 155, 60, 0.15);
            border: 1px solid var(--gold-2);
            color: var(--gold-1);
            font-size: 12px;
            font-weight: 800;
            padding: 4px 10px;
            border-radius: var(--radius-pill);
            min-width: 48px;
            text-align: center;
        }

        /* Locked notification card */
        .lobby-locked-card {
            display: flex;
            align-items: center;
            gap: 12px;
            background: linear-gradient(135deg, rgba(10, 200, 185, 0.12), rgba(15, 23, 42, 0.85));
            border: 1px solid var(--arcane-cyan);
            box-shadow: 0 0 16px var(--arcane-cyan-glow);
            border-radius: var(--radius-md);
            padding: 12px 14px;
            box-sizing: border-box;
        }

        .lobby-locked-icon {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: var(--arcane-cyan);
            color: #03060a;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            font-weight: 900;
            flex-shrink: 0;
        }

        .lobby-locked-text-wrap {
            display: flex;
            flex-direction: column;
            gap: 2px;
            overflow: hidden;
        }

        .lobby-locked-title {
            font-size: 14px;
            font-weight: 800;
            color: #ffffff;
        }

        .lobby-locked-subtitle {
            font-size: 11px;
            color: var(--text-secondary);
            line-height: 1.35;
        }

        /* Teams Container: Column on Mobile */
        .lobby-teams-container {
            display: flex;
            flex-direction: column;
            gap: 14px;
            width: 100%;
            box-sizing: border-box;
        }

        .lobby-team-box {
            background: rgba(15, 23, 42, 0.75);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-md);
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            box-sizing: border-box;
        }

        .lobby-team-box.blue {
            border-left: 4px solid var(--blue-team);
        }

        .lobby-team-box.red {
            border-left: 4px solid var(--red-team);
        }

        .lobby-team-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding-bottom: 6px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
        }

        .lobby-team-title {
            font-size: 12px;
            font-weight: 800;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }

        .lobby-team-title.blue {
            color: var(--blue-team);
        }

        .lobby-team-title.red {
            color: var(--red-team);
        }

        .lobby-team-count {
            font-size: 10px;
            font-weight: 700;
            color: var(--text-muted);
        }

        .lobby-roster-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        /* Individual Player Card in Lobby */
        .lobby-player-card {
            display: flex;
            align-items: center;
            gap: 10px;
            background: rgba(10, 16, 29, 0.65);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-sm);
            padding: 8px 10px;
            box-sizing: border-box;
            transition: all 0.2s ease;
            min-height: 54px;
        }

        /* Highlight Local Player */
        .lobby-player-card.is-me {
            border: 2px solid var(--gold-1) !important;
            background: linear-gradient(90deg, rgba(200, 155, 60, 0.18), rgba(15, 23, 42, 0.85));
            box-shadow: 0 0 16px rgba(200, 155, 60, 0.3);
        }

        .lobby-avatar-container {
            position: relative;
            width: 40px;
            height: 40px;
            flex-shrink: 0;
        }

        .lobby-champ-img {
            width: 40px;
            height: 40px;
            border-radius: 6px;
            object-fit: cover;
            border: 1px solid var(--card-border);
            background: #03060a;
            display: block;
        }

        .lobby-avatar-mystery {
            width: 40px;
            height: 40px;
            border-radius: 6px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px dashed rgba(255, 255, 255, 0.2);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            color: var(--text-muted);
        }

        .lobby-role-pill-on-avatar {
            position: absolute;
            bottom: -3px;
            right: -3px;
            font-size: 8px;
            font-weight: 800;
            padding: 1px 4px;
            border-radius: 3px;
            background: rgba(10, 16, 29, 0.9);
            border: 1px solid var(--card-border);
            color: var(--arcane-cyan);
            text-transform: uppercase;
        }

        .lobby-player-info {
            display: flex;
            flex-direction: column;
            gap: 2px;
            flex: 1;
            min-width: 0;
            overflow: hidden;
        }

        .lobby-player-name-row {
            display: flex;
            align-items: center;
            gap: 6px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .lobby-player-name {
            font-size: 12px;
            font-weight: 700;
            color: var(--text-primary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .lobby-tag-me {
            font-size: 9px;
            font-weight: 900;
            padding: 1px 5px;
            border-radius: 3px;
            background: var(--gold-1);
            color: #03060a;
            letter-spacing: 0.5px;
            flex-shrink: 0;
        }

        .lobby-champ-title {
            font-size: 11px;
            font-weight: 600;
            color: var(--gold-2);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .lobby-champ-title.hidden-enemy {
            color: var(--text-muted);
            font-style: italic;
        }

        /* Trade / Swap Buttons on Roster Cards */
        .lobby-card-actions {
            display: flex;
            gap: 6px;
            flex-shrink: 0;
        }

        .lobby-btn-swap, .lobby-btn-trade {
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid var(--card-border);
            color: var(--text-secondary);
            border-radius: var(--radius-sm);
            padding: 6px 8px;
            font-size: 10px;
            font-weight: 700;
            cursor: pointer;
            min-height: 36px;
            touch-action: manipulation;
            transition: all 0.15s ease;
            white-space: nowrap;
        }

        .lobby-btn-swap:hover, .lobby-btn-swap:active {
            border-color: var(--arcane-cyan);
            color: var(--arcane-cyan);
            background: rgba(10, 200, 185, 0.12);
        }

        .lobby-btn-trade:hover, .lobby-btn-trade:active {
            border-color: var(--gold-1);
            color: var(--gold-1);
            background: rgba(200, 155, 60, 0.12);
        }

        .lobby-status-pill {
            font-size: 10px;
            font-weight: 800;
            color: var(--arcane-cyan);
            background: rgba(10, 200, 185, 0.1);
            border: 1px solid rgba(10, 200, 185, 0.3);
            border-radius: var(--radius-pill);
            padding: 3px 8px;
            white-space: nowrap;
            flex-shrink: 0;
        }

        .lobby-footer-hint {
            font-size: 11px;
            color: var(--text-muted);
            text-align: center;
            padding: 8px 12px;
            background: rgba(15, 23, 42, 0.5);
            border-radius: var(--radius-sm);
            border: 1px dashed rgba(255, 255, 255, 0.08);
            line-height: 1.4;
        }

        @media (max-width: 360px) {
            .lobby-card-actions {
                flex-direction: column;
                gap: 4px;
            }
            .lobby-btn-swap, .lobby-btn-trade {
                min-height: 30px;
                padding: 4px 6px;
                font-size: 9px;
            }
            .lobby-player-card {
                padding: 6px 8px;
            }
        }

        /* In-Game Match Screen (#inGameView) */
        .in-game-view {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 16px;
            animation: fadeIn 0.3s ease;
            box-sizing: border-box;
        }

        .in-game-card {
            width: 100%;
            max-width: 500px;
            background: radial-gradient(circle at 50% 0%, #152540 0%, #0a111e 95%);
            border: 1px solid var(--card-border-active);
            border-radius: var(--radius-lg);
            padding: 26px 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
            text-align: center;
            box-shadow: 0 8px 30px rgba(0, 0, 0, 0.5), 0 0 35px var(--arcane-cyan-glow);
            box-sizing: border-box;
        }

        .in-game-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(10, 200, 185, 0.12);
            border: 1px solid rgba(10, 200, 185, 0.4);
            border-radius: var(--radius-pill);
            padding: 4px 12px;
            font-size: 11px;
            font-weight: 700;
            color: var(--arcane-cyan);
            letter-spacing: 1px;
            margin-bottom: 12px;
        }

        .in-game-pulse-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--arcane-cyan);
            box-shadow: 0 0 8px var(--arcane-cyan);
            animation: pulse-dot 1.5s infinite;
        }

        .in-game-title {
            font-size: 26px;
            font-weight: 900;
            color: var(--gold-1);
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }

        .in-game-subtitle {
            font-size: 14px;
            color: var(--text-secondary);
            margin-bottom: 20px;
        }

        .in-game-clock-container {
            width: 100%;
            background: rgba(6, 9, 16, 0.6);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-md);
            padding: 16px;
            margin-bottom: 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
            box-sizing: border-box;
        }

        .in-game-clock-label {
            font-size: 11px;
            font-weight: 700;
            color: var(--gold-2);
            letter-spacing: 1.5px;
            margin-bottom: 4px;
        }

        .in-game-clock {
            font-size: 44px;
            font-weight: 900;
            color: var(--gold-1);
            font-variant-numeric: tabular-nums;
            letter-spacing: 2px;
            text-shadow: 0 0 15px rgba(240, 230, 210, 0.4);
        }

        .in-game-champ-info {
            display: flex;
            align-items: center;
            gap: 14px;
            background: rgba(14, 23, 42, 0.7);
            border: 1px solid var(--card-border);
            border-radius: var(--radius-md);
            padding: 10px 16px;
            width: 100%;
            max-width: 340px;
            margin-bottom: 18px;
            text-align: left;
            box-sizing: border-box;
        }

        .in-game-champ-img {
            width: 48px;
            height: 48px;
            border-radius: 50%;
            border: 2px solid var(--gold-2);
            object-fit: cover;
            flex-shrink: 0;
        }

        .in-game-champ-details {
            display: flex;
            flex-direction: column;
            gap: 2px;
            overflow: hidden;
        }

        .in-game-champ-name {
            font-size: 16px;
            font-weight: 800;
            color: var(--gold-1);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .in-game-role {
            font-size: 12px;
            font-weight: 600;
            color: var(--arcane-cyan);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .in-game-status-hint {
            font-size: 11px;
            color: var(--text-muted);
            line-height: 1.5;
            max-width: 320px;
        }

        /* Mobile Ergonomics & Viewport Adaptations (320px, 375px, 390px, 430px) */
        button, .btn-action-outline, .btn-start-queue, .btn-cancel-queue,
        .btn-ready-accept, .btn-ready-decline, .nav-tab-btn, .mode-chip, .role-pill {
            min-height: 44px;
            min-width: 44px;
            box-sizing: border-box;
        }

        @media (max-width: 430px) {
            html, body {
                overflow-x: hidden;
                width: 100%;
            }
            main {
                width: 100%;
                max-width: 100%;
                overflow-x: hidden;
            }
            .ready-check-card {
                width: calc(100% - 20px);
                max-width: 390px;
                padding: 24px 16px;
            }
            .in-game-card {
                width: 100%;
                padding: 22px 16px;
            }
        }

        @media (max-width: 390px) {
            .ready-check-title {
                font-size: 20px;
            }
            .in-game-title {
                font-size: 22px;
            }
            .in-game-clock {
                font-size: 38px;
            }
            .btn-ready-accept {
                font-size: 16px;
                height: 52px;
            }
        }

        @media (max-width: 430px) {
            .lane-chips-grid {
                grid-template-columns: repeat(3, 1fr);
                gap: 5px;
            }
            .lane-chip {
                padding: 6px 2px;
                min-height: 42px;
            }
            .lane-chip-label {
                font-size: 9.5px;
            }
        }

        @media (max-width: 390px) {
            .lane-chips-grid {
                grid-template-columns: repeat(3, 1fr);
                gap: 4px;
            }
            .lane-chip {
                padding: 5px 2px;
                min-height: 40px;
            }
            .lane-chip-label {
                font-size: 9px;
            }
        }

        @media (max-width: 375px) {
            .lane-chips-grid {
                grid-template-columns: repeat(3, 1fr);
                gap: 4px;
            }
            .lane-chip {
                padding: 5px 2px;
                min-height: 40px;
            }
            .lane-chip-label {
                font-size: 8.5px;
            }
            .mode-chips-grid {
                grid-template-columns: 1fr;
            }
            .ready-check-timer-wrapper {
                width: 76px;
                height: 76px;
            }
            .ready-check-timer-ring {
                width: 76px;
                height: 76px;
            }
            .ready-check-countdown {
                font-size: 22px;
            }
            .in-game-clock {
                font-size: 34px;
            }
            .in-game-champ-info {
                max-width: 100%;
            }
        }

        @media (max-width: 320px) {
            .lane-chips-grid {
                grid-template-columns: repeat(2, 1fr);
                gap: 4px;
            }
            .lane-chip {
                padding: 4px 2px;
                min-height: 38px;
            }
            .lane-chip-label {
                font-size: 8px;
            }
            body {
                padding: 8px 6px calc(var(--nav-height) + 12px) 6px;
            }
            header {
                padding: 6px 2px 10px 2px;
            }
            .brand-title {
                font-size: 16px;
            }
            .ready-check-card {
                padding: 18px 12px;
            }
            .ready-check-title {
                font-size: 18px;
                letter-spacing: 1px;
            }
            .btn-ready-accept {
                height: 46px;
                min-height: 44px;
                font-size: 14px;
            }
            .btn-ready-decline {
                height: 44px;
                min-height: 44px;
                font-size: 13px;
            }
            .in-game-card {
                padding: 16px 10px;
            }
            .in-game-title {
                font-size: 19px;
            }
            .in-game-clock {
                font-size: 30px;
            }
            .in-game-subtitle {
                font-size: 12px;
            }
        }
    </style>
</head>
<body>
    <header>
        <div class="brand">
            <span class="brand-icon">⚡</span>
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
        <!-- Tab 1: Game & Champ Select Panel -->
        <div id="gameTabPanel" class="tab-panel">
            <!-- Standard Gameflow View -->
            <div id="mainView">
                <div id="phaseCard" class="phase-card">
                    <div class="phase-label">Aktif Oyun Durumu</div>
                    <div id="phaseDisplay" class="phase-value">İstemci Bekleniyor</div>
                </div>

                <div class="action-area">
                    <div id="infoMessage" class="info-card">
                        League of Legends istemcisi bağlı olduğunda maç durumu burada görünür.
                    </div>
                </div>

                <!-- Live Queue Timer Card -->
                <div id="queueTimerCard" class="queue-timer-card" style="display: none;">
                    <div class="queue-timer-header">
                        <span class="queue-pulse-dot"></span>
                        <span class="queue-timer-title">SIRA BEKLENİYOR</span>
                    </div>
                    <div id="queueTimerClock" class="queue-timer-clock">00:00</div>
                    <div id="queueEstimatedClock" class="queue-estimated-clock">Tahmini: --:--</div>
                    <button id="cancelQueueBtn" class="btn-cancel-queue">Sırayı İptal Et</button>
                </div>

                <!-- Game Mode Selector Card -->
                <div id="modeSelectorCard" class="mode-selector-card">
                    <div class="mode-card-title">OYUN MODU SEÇİMİ</div>
                    <div class="mode-chips-grid">
                        <button class="mode-chip active" data-queue-id="420">Dereceli Tek/Çift</button>
                        <button class="mode-chip" data-queue-id="440">Dereceli Esnek</button>
                        <button class="mode-chip" data-queue-id="400">Normal Sıralı</button>
                        <button class="mode-chip" data-queue-id="430">Kapalı Seçim</button>
                        <button class="mode-chip" data-queue-id="450">ARAM</button>
                    </div>
                    <!-- Lane Selector Section (Filtered by Mode) -->
                    <div id="laneSelectorSection" class="lane-selector-section">
                        <div class="lane-section-header">
                            <span class="lane-section-title">KORİDOR SEÇİMİ</span>
                            <span id="laneSelectionHint" class="lane-section-hint">Birincil & İkincil rol seçin</span>
                        </div>
                        <div id="laneChipsGrid" class="lane-chips-grid">
                            <button type="button" class="lane-chip primary" data-lane="FILL" title="Doldur">
                                <span class="lane-chip-icon">🎲</span>
                                <span class="lane-chip-label">Doldur</span>
                                <span class="lane-chip-badge">1.</span>
                            </button>
                            <button type="button" class="lane-chip" data-lane="TOP" title="Üst Koridor">
                                <span class="lane-chip-icon">🛡️</span>
                                <span class="lane-chip-label">Üst Koridor</span>
                                <span class="lane-chip-badge"></span>
                            </button>
                            <button type="button" class="lane-chip" data-lane="JUNGLE" title="Orman">
                                <span class="lane-chip-icon">🌲</span>
                                <span class="lane-chip-label">Orman</span>
                                <span class="lane-chip-badge"></span>
                            </button>
                            <button type="button" class="lane-chip" data-lane="MIDDLE" title="Orta Koridor">
                                <span class="lane-chip-icon">⚡</span>
                                <span class="lane-chip-label">Orta Koridor</span>
                                <span class="lane-chip-badge"></span>
                            </button>
                            <button type="button" class="lane-chip" data-lane="BOTTOM" title="Alt Koridor">
                                <span class="lane-chip-icon">🏹</span>
                                <span class="lane-chip-label">Alt Koridor</span>
                                <span class="lane-chip-badge"></span>
                            </button>
                            <button type="button" class="lane-chip" data-lane="UTILITY" title="Destek">
                                <span class="lane-chip-icon">💖</span>
                                <span class="lane-chip-label">Destek</span>
                                <span class="lane-chip-badge"></span>
                            </button>
                        </div>
                    </div>

                    <!-- Notice when lane selection is not supported in active mode -->
                    <div id="laneDisabledNotice" class="lane-disabled-notice" style="display: none;">
                        <span class="notice-icon">ℹ️</span>
                        <span class="notice-text">Bu modda koridor seçimi bulunmamaktadır.</span>
                    </div>

                    <button id="startQueueBtn" class="btn-start-queue">Maç Ara</button>
                </div>
            </div>

            <!-- Champion Select View -->
            <div id="champSelectView" style="display: none;">
                <!-- Game Mode Banner -->
                <div id="gameModeBanner" class="game-mode-banner">
                    <span id="gameModeName" class="game-mode-name">SİHİRDAR VADİSİ</span>
                    <span id="gameModeStage" class="game-mode-stage">HAZIRLIK</span>
                </div>

                <!-- Validation / Client Error Toast -->
                <div id="csErrorToast" class="error-toast"></div>

                <!-- Trade / Lane Swap In-App Notification Banner -->
                <div id="tradeNoticeBanner" class="trade-notice-banner" style="display: none;">
                    <div class="trade-notice-header">
                        <span id="tradeNoticeType" class="trade-notice-type">ŞAMPİYON TAKASI</span>
                        <span id="tradeNoticeSender" class="trade-notice-sender">Takım Arkadaşı</span>
                    </div>
                    <div id="tradeNoticeText" class="trade-notice-text">Takas teklifi gönderildi.</div>
                    <div class="trade-notice-actions">
                        <button id="tradeAcceptBtn" class="trade-btn-accept">KABUL ET</button>
                        <button id="tradeDeclineBtn" class="trade-btn-decline">REDDET</button>
                        <button id="tradeCancelBtn" class="trade-btn-cancel" style="display: none;">İPTAL ET</button>
                    </div>
                </div>

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

                <!-- ARAM / ARAM Şamata Bench Panel -->
                <div id="aramBenchBox" class="aram-bench-box">
                    <div class="aram-bench-title">🎲 ARAM Yedek Kulübesi & Durum</div>
                    <div id="aramBenchInfo" style="font-size:11px;color:var(--text-secondary);">
                        Bu modda şampiyonlar rastgele atanır; yasaklama ve klasik tablo seçimi yoktur.
                    </div>
                    <div id="aramBenchList" class="aram-bench-list"></div>
                </div>

                <!-- Unsupported / Unknown Mode Safe Generic Box -->
                <div id="safeModeBox" class="safe-mode-box">
                    <div class="safe-mode-title">🛡️ Güvenli Genel Durum Ekranı</div>
                    <div id="safeModeInfo" style="font-size:11px;color:var(--text-secondary);">
                        Bu oyun modu için özel seçim kuralları doğrulanamadığından yanlış seçimi önlemek adına manuel seçim devre dışı bırakılmıştır.
                    </div>
                </div>

                <div id="champSearchBox" class="champ-search-box">
                    <input type="text" id="champSearchInput" class="champ-search-input" placeholder="🔍 Şampiyon ara (Ahri, Yasuo, Lee Sin)...">
                    <div class="role-filters-row" id="roleFiltersRow">
                        <button class="role-pill active" data-role="">Tümü</button>
                        <button class="role-pill" data-role="fighter">Dövüşçü</button>
                        <button class="role-pill" data-role="mage">Büyücü</button>
                        <button class="role-pill" data-role="assassin">Suikastçı</button>
                        <button class="role-pill" data-role="marksman">Nişancı</button>
                        <button class="role-pill" data-role="support">Destek</button>
                        <button class="role-pill" data-role="tank">Tank</button>
                    </div>
                </div>

                <div id="champGridContainer" class="champ-grid-container">
                    <div id="champGrid" class="champ-grid"></div>
                </div>

                <div id="csActionPanel" class="cs-action-panel">
                    <div class="cs-action-row">
                        <div class="cs-selected-preview">
                            <img id="csPreviewImg" class="cs-preview-img" src="" alt="" style="display:none;">
                            <div id="csPreviewName" class="cs-preview-name">Şampiyon Seçin</div>
                        </div>
                        <button id="csBanBtn" class="cs-btn-ban" style="display:none;" disabled>YASAKLA</button>
                        <button id="csLockBtn" class="cs-btn-lock" disabled>KİLİTLE</button>
                    </div>
                    <div id="csActionReason" class="cs-action-reason"></div>
                </div>
            </div>

            <!-- Post-Lock Match Lobby View -->
            <div id="postLockLobbyView" class="post-lock-lobby-view" style="display: none;">
                <!-- Header Banner -->
                <div class="lobby-header-banner">
                    <div class="lobby-header-status">
                        <span class="lobby-status-dot"></span>
                        <span class="lobby-header-title">🛡️ MAÇ LOBİSİ</span>
                    </div>
                    <div id="lobbyTimerBadge" class="lobby-timer-badge">--</div>
                </div>

                <!-- Locked status info banner -->
                <div class="lobby-locked-card">
                    <div class="lobby-locked-icon">✓</div>
                    <div class="lobby-locked-text-wrap">
                        <div class="lobby-locked-title">Şampiyonun Kilitlendi!</div>
                        <div class="lobby-locked-subtitle">Diğer oyuncuların seçimleri ve karşılaşmanın başlaması bekleniyor.</div>
                    </div>
                </div>

                <!-- Trade / Lane Swap Notice Banner in Lobby -->
                <div id="lobbyTradeNoticeBanner" class="trade-notice-banner" style="display: none;">
                    <div class="trade-notice-header">
                        <span id="lobbyTradeNoticeType" class="trade-notice-type">ŞAMPİYON TAKASI</span>
                        <span id="lobbyTradeNoticeSender" class="trade-notice-sender">Takım Arkadaşı</span>
                    </div>
                    <div id="lobbyTradeNoticeText" class="trade-notice-text">Takas teklifi gönderildi.</div>
                    <div class="trade-notice-actions">
                        <button id="lobbyTradeAcceptBtn" class="trade-btn-accept">KABUL ET</button>
                        <button id="lobbyTradeDeclineBtn" class="trade-btn-decline">REDDET</button>
                        <button id="lobbyTradeCancelBtn" class="trade-btn-cancel" style="display: none;">İPTAL ET</button>
                    </div>
                </div>

                <!-- Teams Section: Blue Team (My Team) vs Red Team (Enemy Team) -->
                <div class="lobby-teams-container">
                    <!-- Blue / My Team -->
                    <div class="lobby-team-box blue">
                        <div class="lobby-team-header">
                            <span class="lobby-team-title blue">🔵 KENDİ TAKIMIN</span>
                            <span class="lobby-team-count" id="lobbyMyTeamCount">5 Oyuncu</span>
                        </div>
                        <div id="lobbyMyTeamList" class="lobby-roster-list"></div>
                    </div>

                    <!-- Red / Enemy Team -->
                    <div class="lobby-team-box red">
                        <div class="lobby-team-header">
                            <span class="lobby-team-title red">🔴 KARŞI TAKIM</span>
                            <span class="lobby-team-count" id="lobbyTheirTeamCount">5 Oyuncu</span>
                        </div>
                        <div id="lobbyTheirTeamList" class="lobby-roster-list"></div>
                    </div>
                </div>

                <!-- Footer Hint -->
                <div class="lobby-footer-hint">
                    💡 İpucu: Rünlerini ve Sihirdar Büyülerini üst kısımdaki <b>Dizilim</b> sekmesinden maç başlayana kadar düzenleyebilirsin.
                </div>
            </div>

            <!-- In-Game Match View -->
            <div id="inGameView" class="in-game-view" style="display: none;">
                <div class="in-game-card">
                    <div class="in-game-badge">
                        <span class="in-game-pulse-dot"></span>
                        <span>CANLI KARŞILAŞMA</span>
                    </div>
                    <h2 id="inGameTitle" class="in-game-title">⚔️ Maçtasın!</h2>
                    <div id="inGameSubtitle" class="in-game-subtitle">Maç devam ediyor.</div>

                    <div class="in-game-clock-container">
                        <div class="in-game-clock-label">OYUN SÜRESİ</div>
                        <div id="inGameClock" class="in-game-clock">00:00</div>
                    </div>

                    <!-- Player Champion & Role Info -->
                    <div id="inGameChampInfo" class="in-game-champ-info" style="display: none;">
                        <img id="inGameChampImg" class="in-game-champ-img" src="" alt="Şampiyon" />
                        <div class="in-game-champ-details">
                            <div id="inGameChampName" class="in-game-champ-name">--</div>
                            <div id="inGameRole" class="in-game-role">--</div>
                        </div>
                    </div>

                    <div class="in-game-status-hint">
                        Karşılaşma tamamlandığında ekran otomatik olarak maç sonu özetine veya lobiye dönecektir.
                    </div>
                </div>
            </div>
        </div>

        <!-- Tab 2: Loadout (Spells & Runes) Panel -->
        <div id="loadoutTabPanel" class="tab-panel" style="display: none;">
            <div class="loadout-bar">
                <div class="loadout-top-row">
                    <div class="spells-container">
                        <div id="spellSlotD" class="spell-slot" title="D Tuşu Büyüsü">
                            <img id="spellImgD" class="spell-img" src="https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/data/spells/icons2d/summonerflash.png" alt="D">
                            <span class="spell-key-badge">D</span>
                        </div>
                        <div id="spellSlotF" class="spell-slot" title="F Tuşu Büyüsü">
                            <img id="spellImgF" class="spell-img" src="https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/data/spells/icons2d/summonerdot.png" alt="F">
                            <span class="spell-key-badge">F</span>
                        </div>
                    </div>
                    <button id="applyRunesBtn" class="btn-apply-runes" disabled title="Seçili Rün Sayfasını İstemciye Uygula">⚡ SEÇİLİ RÜNÜ UYGULA</button>
                </div>

                <div class="runes-section">
                    <div class="runes-header-row">
                        <span class="runes-title">İSTEMCİ ÖNERİLEN RÜN SAYFALARI (3 SEÇENEK)</span>
                        <span id="runesKeystoneName" style="font-size:10px;color:var(--gold-1);font-weight:700;">Şampiyon bekleniyor...</span>
                    </div>
                    <div id="runesCardsGrid" class="runes-cards-grid" style="display:none;"></div>
                    <div id="runesEmptyMsg" class="runes-empty-msg">Şampiyon seçildiğinde istemcinin 3 rün önerisi burada listelenir.</div>

                    <!-- 4th Card: + Kendi Rünümü Oluştur -->
                    <div id="btnOpenCustomRuneBuilder" class="rune-builder-trigger-card">
                        <div class="rune-builder-trigger-icon">⚡</div>
                        <div class="rune-builder-trigger-content">
                            <div class="rune-builder-trigger-title">+ Kendi Rünümü Oluştur</div>
                            <div class="rune-builder-trigger-subtitle">Özel rün sayfası tasarla, kaydet ve istemciye uygula</div>
                        </div>
                        <div class="rune-builder-trigger-arrow">➔</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Tab 3: Station / Connection Details Panel -->
        <div id="stationView">
            <div class="station-card">
                <div class="station-card-title">İSTASYON BAĞLANTISI</div>
                <div class="station-info-row">
                    <span style="color:var(--text-secondary);">LoL İstemcisi</span>
                    <span id="stationLcuStatus" style="font-weight:700;color:var(--gold-1);">Bekleniyor</span>
                </div>
                <div class="station-info-row">
                    <span style="color:var(--text-secondary);">Aktif Aşama</span>
                    <span id="stationPhase" style="font-weight:700;color:var(--arcane-cyan);">-</span>
                </div>
                <div class="station-info-row">
                    <span style="color:var(--text-secondary);">WebSocket Durumu</span>
                    <span id="stationWsStatus" style="font-weight:700;color:var(--success);">Bağlı</span>
                </div>
                <div class="station-info-row">
                    <span style="color:var(--text-secondary);">Oturum Türü</span>
                    <span style="font-weight:700;color:var(--gold-2);">Zero-Trust LAN Token</span>
                </div>
            </div>

            <button id="btnResync" class="btn-action-outline">🔄 İstemci Durumunu Yenile</button>
        </div>
    </main>

    <!-- Spells Modal / Bottom Drawer -->
    <div id="spellsModal" class="spells-modal" style="display: none;">
        <div class="spells-modal-content">
            <div class="spells-modal-header">
                <span class="spells-modal-title" id="spellsModalTitle">Sihirdar Büyüsü Seç</span>
                <button id="spellsModalClose" class="modal-close-btn">&times;</button>
            </div>
            <div id="spellsGrid" class="spells-grid"></div>
        </div>
    </div>

    <!-- Custom Rune Builder Modal (#customRuneModal) -->
    <div id="customRuneModal" class="custom-rune-modal" style="display: none;">
        <div class="custom-rune-modal-content">
            <div class="custom-rune-modal-header">
                <div class="custom-rune-header-title-box">
                    <span class="custom-rune-header-icon">⚡</span>
                    <span class="custom-rune-modal-title">Özel Rün Oluşturucu</span>
                </div>
                <button id="customRuneModalClose" class="modal-close-btn">&times;</button>
            </div>

            <!-- Modal Navigation Tabs -->
            <div class="custom-rune-tabs">
                <button id="tabBtnBuilder" class="custom-rune-tab active" type="button">🛠️ Rün Tasarla</button>
                <button id="tabBtnSaved" class="custom-rune-tab" type="button">📁 Kayıtlı Rünlerim (<span id="savedRunesCount">0</span>)</button>
            </div>

            <!-- Tab 1: Builder View -->
            <div id="customRuneBuilderView" class="custom-rune-view">
                <!-- Page Name -->
                <div class="custom-rune-field">
                    <label class="custom-rune-label" for="customRuneNameInput">Rün Sayfası İsmi</label>
                    <input id="customRuneNameInput" class="custom-rune-input" type="text" maxlength="32" placeholder="Örn: Agresif Aatrox, Tank Malphite" value="Özel Rün Sayfası">
                </div>

                <!-- Primary Tree Selector -->
                <div class="custom-rune-section">
                    <div class="custom-rune-section-title">1. BİRİNCİL AĞAÇ (ANA YOL)</div>
                    <div id="primaryTreesGrid" class="trees-grid"></div>
                </div>

                <!-- Primary Runes Slots -->
                <div class="custom-rune-section">
                    <div class="custom-rune-section-title">BİRİNCİL RÜNLER (1 ANAHTAR + 3 KÜÇÜK)</div>
                    <div id="primaryRunesSlots" class="rune-slots-container"></div>
                </div>

                <!-- Secondary Tree Selector -->
                <div class="custom-rune-section">
                    <div class="custom-rune-section-title">2. İKİNCİL AĞAÇ (YAN YOL — FARKLI BİR AĞAÇ)</div>
                    <div id="secondaryTreesGrid" class="trees-grid"></div>
                </div>

                <!-- Secondary Runes Slots -->
                <div class="custom-rune-section">
                    <div class="custom-rune-section-title">İKİNCİL RÜNLER (FARKLI SATIRLARDAN 2 KÜÇÜK RÜN)</div>
                    <div id="secondaryRunesSlots" class="rune-slots-container"></div>
                </div>

                <!-- Stat Shards Slots -->
                <div class="custom-rune-section">
                    <div class="custom-rune-section-title">3. NİTELİK PARÇALARI (SALDIRI / ESNEK / SAVUNMA)</div>
                    <div id="statShardsSlots" class="rune-slots-container"></div>
                </div>

                <!-- Validation Alert Message -->
                <div id="customRuneValidationAlert" class="custom-rune-alert" style="display: none;"></div>

                <!-- Action Buttons -->
                <div class="custom-rune-actions">
                    <button id="btnSaveCustomRune" class="btn-save-rune" type="button">💾 RÜNÜ KAYDET</button>
                    <button id="btnApplyCustomRune" class="btn-apply-custom-rune" type="button">⚡ İSTEMCİYE UYGULA</button>
                </div>
            </div>

            <!-- Tab 2: Saved Runes View -->
            <div id="customRuneSavedView" class="custom-rune-view" style="display: none;">
                <div id="savedRunesList" class="saved-runes-list"></div>
                <div id="savedRunesEmpty" class="saved-runes-empty" style="display: none;">
                    Henüz kayıtlı özel rününüz yok. "Rün Tasarla" sekmesinden yeni bir rün oluşturup kaydedebilirsiniz!
                </div>
            </div>
        </div>
    </div>

    <!-- Ready Check Modal Popup -->
    <div id="readyCheckModal" class="ready-check-modal" style="display: none;">
        <div class="ready-check-card">
            <div class="ready-check-glow"></div>
            <div class="ready-check-icon">⚔️</div>
            <h2 id="readyCheckTitle" class="ready-check-title">⚔️ MAÇ BULUNDU!</h2>
            <div class="ready-check-timer-wrapper">
                <svg class="ready-check-timer-ring" width="84" height="84" viewBox="0 0 84 84">
                    <circle class="ring-track" cx="42" cy="42" r="36" />
                    <circle id="readyCheckRingProgress" class="ring-bar" cx="42" cy="42" r="36" />
                </svg>
                <div id="readyCheckCountdown" class="ready-check-countdown">10</div>
            </div>
            <div id="readyCheckStatus" class="ready-check-status">Kabul bekleniyor...</div>
            <div class="ready-check-actions">
                <button id="readyAcceptBtn" class="btn-ready-accept" type="button">
                    <span>⚔️</span> KABUL ET
                </button>
                <button id="readyDeclineBtn" class="btn-ready-decline" type="button">
                    REDDET
                </button>
            </div>
        </div>
    </div>

    <!-- Mobile Bottom Navigation Bar (LeagueCompanion One-Handed Ergonomics) -->
    <nav class="mobile-nav-bar">
        <button id="navTabGame" class="nav-tab-btn active">
            <span class="nav-tab-icon">🎮</span>
            <span>Oyun</span>
        </button>
        <button id="navTabLoadout" class="nav-tab-btn">
            <span class="nav-tab-icon">⚡</span>
            <span>Rün & Büyü</span>
        </button>
        <button id="navTabStation" class="nav-tab-btn">
            <span class="nav-tab-icon">📡</span>
            <span>İstasyon</span>
        </button>
    </nav>

    <script>
        const statusBadge = document.getElementById('statusBadge');
        const statusText = document.getElementById('statusText');
        const lcuBadge = document.getElementById('lcuBadge');
        const mainView = document.getElementById('mainView');
        const phaseCard = document.getElementById('phaseCard');
        const phaseDisplay = document.getElementById('phaseDisplay');
        const acceptBtn = document.getElementById('acceptBtn');
        const infoMessage = document.getElementById('infoMessage');

        // Ready Check Modal DOM references
        const readyCheckModal = document.getElementById('readyCheckModal');
        const readyAcceptBtn = document.getElementById('readyAcceptBtn');
        const readyDeclineBtn = document.getElementById('readyDeclineBtn');
        const readyCheckCountdown = document.getElementById('readyCheckCountdown');
        const readyCheckRingProgress = document.getElementById('readyCheckRingProgress');
        const readyCheckStatus = document.getElementById('readyCheckStatus');

        // In-Game View DOM references
        const inGameView = document.getElementById('inGameView');
        const inGameClock = document.getElementById('inGameClock');
        const inGameChampInfo = document.getElementById('inGameChampInfo');
        const inGameChampImg = document.getElementById('inGameChampImg');
        const inGameChampName = document.getElementById('inGameChampName');
        const inGameRole = document.getElementById('inGameRole');

        // Champ select DOM references
        const champSelectView = document.getElementById('champSelectView');
        const gameModeBanner = document.getElementById('gameModeBanner');
        const gameModeName = document.getElementById('gameModeName');
        const gameModeStage = document.getElementById('gameModeStage');
        const csErrorToast = document.getElementById('csErrorToast');
        const csModeBadge = document.getElementById('csModeBadge');
        const csPositionBadge = document.getElementById('csPositionBadge');
        const csTimerBadge = document.getElementById('csTimerBadge');
        const csProgressBar = document.getElementById('csProgressBar');
        const myTeamList = document.getElementById('myTeamList');
        const theirTeamList = document.getElementById('theirTeamList');
        const champSearchBox = document.getElementById('champSearchBox');
        const champSearchInput = document.getElementById('champSearchInput');
        const roleFiltersRow = document.getElementById('roleFiltersRow');
        const champGridContainer = document.getElementById('champGridContainer');
        const champGrid = document.getElementById('champGrid');
        const aramBenchBox = document.getElementById('aramBenchBox');
        const aramBenchInfo = document.getElementById('aramBenchInfo');
        const aramBenchList = document.getElementById('aramBenchList');
        const safeModeBox = document.getElementById('safeModeBox');
        const csActionPanel = document.getElementById('csActionPanel');
        const csPreviewImg = document.getElementById('csPreviewImg');
        const csPreviewName = document.getElementById('csPreviewName');
        const csBanBtn = document.getElementById('csBanBtn');
        const csLockBtn = document.getElementById('csLockBtn');
        const csActionReason = document.getElementById('csActionReason');

        // Post-Lock Lobby DOM references
        const postLockLobbyView = document.getElementById('postLockLobbyView');
        const lobbyTimerBadge = document.getElementById('lobbyTimerBadge');
        const lobbyMyTeamList = document.getElementById('lobbyMyTeamList');
        const lobbyTheirTeamList = document.getElementById('lobbyTheirTeamList');
        const lobbyMyTeamCount = document.getElementById('lobbyMyTeamCount');
        const lobbyTheirTeamCount = document.getElementById('lobbyTheirTeamCount');
        const lobbyTradeNoticeBanner = document.getElementById('lobbyTradeNoticeBanner');
        const lobbyTradeNoticeType = document.getElementById('lobbyTradeNoticeType');
        const lobbyTradeNoticeSender = document.getElementById('lobbyTradeNoticeSender');
        const lobbyTradeNoticeText = document.getElementById('lobbyTradeNoticeText');
        const lobbyTradeAcceptBtn = document.getElementById('lobbyTradeAcceptBtn');
        const lobbyTradeDeclineBtn = document.getElementById('lobbyTradeDeclineBtn');
        const lobbyTradeCancelBtn = document.getElementById('lobbyTradeCancelBtn');

        // Navigation DOM references
        const gameTabPanel = document.getElementById('gameTabPanel');
        const queueTimerCard = document.getElementById('queueTimerCard');
        const queueTimerClock = document.getElementById('queueTimerClock');
        const queueEstimatedClock = document.getElementById('queueEstimatedClock');
        const cancelQueueBtn = document.getElementById('cancelQueueBtn');
        const modeSelectorCard = document.getElementById('modeSelectorCard');
        const startQueueBtn = document.getElementById('startQueueBtn');
        const modeChips = document.querySelectorAll('.mode-chip');
        const laneSelectorSection = document.getElementById('laneSelectorSection');
        const laneDisabledNotice = document.getElementById('laneDisabledNotice');
        const laneChips = document.querySelectorAll('.lane-chip');
        const tradeNoticeBanner = document.getElementById('tradeNoticeBanner');
        const tradeNoticeType = document.getElementById('tradeNoticeType');
        const tradeNoticeSender = document.getElementById('tradeNoticeSender');
        const tradeNoticeText = document.getElementById('tradeNoticeText');
        const tradeAcceptBtn = document.getElementById('tradeAcceptBtn');
        const tradeDeclineBtn = document.getElementById('tradeDeclineBtn');
        const tradeCancelBtn = document.getElementById('tradeCancelBtn');
        const loadoutTabPanel = document.getElementById('loadoutTabPanel');
        const stationView = document.getElementById('stationView');
        const navTabGame = document.getElementById('navTabGame');
        const navTabLoadout = document.getElementById('navTabLoadout');
        const navTabStation = document.getElementById('navTabStation');

        const stationLcuStatus = document.getElementById('stationLcuStatus');
        const stationPhase = document.getElementById('stationPhase');
        const stationWsStatus = document.getElementById('stationWsStatus');
        const btnResync = document.getElementById('btnResync');

        let socket = null;
        let sessionToken = localStorage.getItem('riftpilot_session_token');

        // State variables
        let currentChampSelect = null;
        let currentModeContext = null;
        let currentEligibility = null;
        let gridChampions = [];
        let selectedChampId = 0;
        let selectedChampName = '';
        let isSubmittingAction = false;
        let lastFetchedRunesChampId = 0;
        let selectedRoleFilter = '';
        let selectedQueueId = 420;
        let selectedFirstLane = 'FILL';
        let selectedSecondLane = 'UNSELECTED';
        const DRAFT_QUEUE_IDS = [420, 440, 400];
        const NON_DRAFT_QUEUE_IDS = [430, 450];

        const spellSlotD = document.getElementById('spellSlotD');
        const spellSlotF = document.getElementById('spellSlotF');
        const spellImgD = document.getElementById('spellImgD');
        const spellImgF = document.getElementById('spellImgF');
        const runesKeystoneName = document.getElementById('runesKeystoneName');
        const runesCardsGrid = document.getElementById('runesCardsGrid');
        const runesEmptyMsg = document.getElementById('runesEmptyMsg');
        const applyRunesBtn = document.getElementById('applyRunesBtn');
        const spellsModal = document.getElementById('spellsModal');
        const spellsModalTitle = document.getElementById('spellsModalTitle');
        const spellsModalClose = document.getElementById('spellsModalClose');
        const spellsGrid = document.getElementById('spellsGrid');

        let currentSpell1Id = 4;
        let currentSpell2Id = 14;
        let activeSpellPickerSlot = 'D';
        let recommendedRunePages = [];
        let selectedRunePageIndex = -1;
        let spellsCatalog = [];

        // Navigation Tabs Handling
        function switchTab(tabName) {
            gameTabPanel.style.display = (tabName === 'game') ? 'flex' : 'none';
            loadoutTabPanel.style.display = (tabName === 'loadout') ? 'flex' : 'none';
            stationView.style.display = (tabName === 'station') ? 'flex' : 'none';

            navTabGame.classList.toggle('active', tabName === 'game');
            navTabLoadout.classList.toggle('active', tabName === 'loadout');
            navTabStation.classList.toggle('active', tabName === 'station');
        }

        navTabGame.addEventListener('click', () => switchTab('game'));
        navTabLoadout.addEventListener('click', () => switchTab('loadout'));
        navTabStation.addEventListener('click', () => switchTab('station'));

        if (btnResync) {
            btnResync.addEventListener('click', () => {
                if (socket && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({ type: 'GET_STATE', token: sessionToken }));
                    socket.send(JSON.stringify({ type: 'GET_CHAMP_SELECT', token: sessionToken }));
                    socket.send(JSON.stringify({ type: 'GET_SPELLS', token: sessionToken }));
                }
            });
        }

        // Role Filter Pills
        if (roleFiltersRow) {
            roleFiltersRow.querySelectorAll('.role-pill').forEach(btn => {
                btn.addEventListener('click', () => {
                    roleFiltersRow.querySelectorAll('.role-pill').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    selectedRoleFilter = btn.getAttribute('data-role') || '';
                    renderChampGrid();
                });
            });
        }

        function showErrorToast(msg) {
            if (!csErrorToast || !msg) return;
            csErrorToast.innerText = '⚠️ ' + msg;
            csErrorToast.style.display = 'block';
            setTimeout(() => {
                csErrorToast.style.display = 'none';
            }, 4000);
        }

        function getSpellIconUrl(key) {
            if (!key) return '';
            return `https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/data/spells/icons2d/${key.toLowerCase()}.png`;
        }

        function updateSpellsUI(s1, s2) {
            if (s1 && s1 > 0) currentSpell1Id = s1;
            if (s2 && s2 > 0) currentSpell2Id = s2;

            const spell1 = spellsCatalog.find(s => s.id === currentSpell1Id) || { key: 'SummonerFlash' };
            const spell2 = spellsCatalog.find(s => s.id === currentSpell2Id) || { key: 'SummonerDot' };

            spellImgD.src = getSpellIconUrl(spell1.key);
            spellImgD.onerror = () => { spellImgD.src = `https://ddragon.leagueoflegends.com/cdn/14.24.1/img/spell/${spell1.key}.png`; };

            spellImgF.src = getSpellIconUrl(spell2.key);
            spellImgF.onerror = () => { spellImgF.src = `https://ddragon.leagueoflegends.com/cdn/14.24.1/img/spell/${spell2.key}.png`; };
        }

        function openSpellsPicker(slot) {
            activeSpellPickerSlot = slot;
            const modeLabel = (currentModeContext && currentModeContext.displayName) ? ` (${currentModeContext.displayName})` : '';
            spellsModalTitle.innerText = ((slot === 'D') ? 'D Tuşu Büyüsü Seç' : 'F Tuşu Büyüsü Seç') + modeLabel;
            renderSpellsGrid();
            spellsModal.style.display = 'flex';
        }

        function closeSpellsPicker() {
            spellsModal.style.display = 'none';
        }

        function renderSpellsGrid() {
            spellsGrid.innerHTML = '';
            const currentSelected = activeSpellPickerSlot === 'D' ? currentSpell1Id : currentSpell2Id;

            spellsCatalog.forEach(sp => {
                const item = document.createElement('div');
                const isSelected = (sp.id === currentSelected);
                item.className = 'spell-select-item' + (isSelected ? ' active' : '');

                const iconUrl = getSpellIconUrl(sp.key);
                item.innerHTML = `
                    <img src="${iconUrl}" onerror="this.src='https://ddragon.leagueoflegends.com/cdn/14.24.1/img/spell/${sp.key}.png'" alt="${sp.name}">
                    <span>${sp.name}</span>
                `;

                item.addEventListener('click', () => {
                    selectSpell(sp.id);
                });

                spellsGrid.appendChild(item);
            });
        }

        function selectSpell(chosenId) {
            let newSpell1 = currentSpell1Id;
            let newSpell2 = currentSpell2Id;

            if (activeSpellPickerSlot === 'D') {
                if (chosenId === currentSpell2Id) {
                    newSpell2 = currentSpell1Id;
                }
                newSpell1 = chosenId;
            } else {
                if (chosenId === currentSpell1Id) {
                    newSpell1 = currentSpell2Id;
                }
                newSpell2 = chosenId;
            }

            closeSpellsPicker();
            if (navigator.vibrate) navigator.vibrate(20);

            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    type: 'SET_SPELLS',
                    token: sessionToken,
                    spell1Id: newSpell1,
                    spell2Id: newSpell2,
                }));
            }
        }

        function fetchRecommendedRunes(championId, position, mapId) {
            if (!championId || championId <= 0) return;
            lastFetchedRunesChampId = championId;
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    type: 'GET_RECOMMENDED_RUNES',
                    token: sessionToken,
                    championId: championId,
                    position: position || '',
                    mapId: mapId || 11,
                }));
            }
        }

        function renderRecommendedRunes(pages) {
            recommendedRunePages = Array.isArray(pages) ? pages : [];
            selectedRunePageIndex = -1;
            applyRunesBtn.disabled = true;
            applyRunesBtn.innerText = '⚡ SEÇİLİ RÜNÜ UYGULA';
            applyRunesBtn.classList.remove('applied');

            if (recommendedRunePages.length === 0) {
                runesCardsGrid.style.display = 'none';
                runesCardsGrid.innerHTML = '';
                runesEmptyMsg.style.display = 'block';
                runesEmptyMsg.innerText = 'İstemcide bu şampiyon için önerilen rün sayfası bulunamadı.';
                runesKeystoneName.innerText = 'Öneri Yok';
                return;
            }

            runesEmptyMsg.style.display = 'none';
            runesCardsGrid.style.display = 'grid';
            runesCardsGrid.innerHTML = '';
            runesKeystoneName.innerText = `${recommendedRunePages.length} öneri mevcut — birini seçin`;

            recommendedRunePages.forEach((rec, idx) => {
                const card = document.createElement('div');
                const isSelected = (idx === selectedRunePageIndex);
                card.className = 'rune-card' + (isSelected ? ' selected' : '');
                const details = Array.isArray(rec.perkNames) ? rec.perkNames.slice(1, 6).join(', ') : '';
                const keystoneIcon = rec.keystoneIcon || 'https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodsadaptiveforceicon.png';
                card.innerHTML = `
                    <div class="rune-card-left">
                        <img class="rune-card-keystone-icon" src="${keystoneIcon}" onerror="this.src='https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodsadaptiveforceicon.png'" alt="keystone">
                    </div>
                    <div class="rune-card-body">
                        <div class="rune-card-top-line">
                            <span class="rune-card-keystone-title">${rec.keystoneName || rec.name}</span>
                            <span class="rune-card-tree-badge">${rec.primaryTreeName || ''} + ${rec.subTreeName || ''}</span>
                        </div>
                        <div class="rune-card-summary">${details}</div>
                    </div>
                    <div class="rune-card-radio">${isSelected ? '✓' : ''}</div>
                `;
                card.addEventListener('click', () => {
                    selectedRunePageIndex = idx;
                    Array.from(runesCardsGrid.children).forEach((el, i) => {
                        const isSel = (i === idx);
                        el.classList.toggle('selected', isSel);
                        const radio = el.querySelector('.rune-card-radio');
                        if (radio) radio.innerText = isSel ? '✓' : '';
                    });
                    runesKeystoneName.innerText = `Seçilen: ${rec.keystoneName || rec.name}`;
                    applyRunesBtn.disabled = false;
                });
                runesCardsGrid.appendChild(card);
            });
        }

        function applySelectedRunePage() {
            if (selectedRunePageIndex < 0 || selectedRunePageIndex >= recommendedRunePages.length) return;
            const chosen = recommendedRunePages[selectedRunePageIndex];
            if (!chosen || !chosen.page) return;

            if (socket && socket.readyState === WebSocket.OPEN) {
                applyRunesBtn.disabled = true;
                applyRunesBtn.innerText = 'UYGULANIYOR...';
                socket.send(JSON.stringify({
                    type: 'APPLY_RUNES',
                    token: sessionToken,
                    page: chosen.page,
                    championId: chosen.championId || selectedChampId,
                }));
            }
        }

        if (spellSlotD) spellSlotD.addEventListener('click', () => openSpellsPicker('D'));
        if (spellSlotF) spellSlotF.addEventListener('click', () => openSpellsPicker('F'));
        if (spellsModalClose) spellsModalClose.addEventListener('click', closeSpellsPicker);
        if (spellsModal) spellsModal.addEventListener('click', (e) => {
            if (e.target === spellsModal) closeSpellsPicker();
        });
        if (applyRunesBtn) applyRunesBtn.addEventListener('click', applySelectedRunePage);

        // =====================================================================
        // Custom Rune Builder Logic
        // =====================================================================
        let runeTreesCatalog = null;
        let savedCustomRunes = [];
        let customRuneState = {
            name: 'Özel Rün Sayfası',
            primaryStyleId: 8000,
            subStyleId: 8100,
            primaryRunes: [8005, 9111, 9104, 8014],
            secondaryRunes: [8139, 8135],
            statShards: [5008, 5008, 5011]
        };

        const customRuneModal = document.getElementById('customRuneModal');
        const customRuneModalClose = document.getElementById('customRuneModalClose');
        const btnOpenCustomRuneBuilder = document.getElementById('btnOpenCustomRuneBuilder');
        const tabBtnBuilder = document.getElementById('tabBtnBuilder');
        const tabBtnSaved = document.getElementById('tabBtnSaved');
        const customRuneBuilderView = document.getElementById('customRuneBuilderView');
        const customRuneSavedView = document.getElementById('customRuneSavedView');
        const savedRunesCount = document.getElementById('savedRunesCount');
        const customRuneNameInput = document.getElementById('customRuneNameInput');
        const primaryTreesGrid = document.getElementById('primaryTreesGrid');
        const primaryRunesSlots = document.getElementById('primaryRunesSlots');
        const secondaryTreesGrid = document.getElementById('secondaryTreesGrid');
        const secondaryRunesSlots = document.getElementById('secondaryRunesSlots');
        const statShardsSlots = document.getElementById('statShardsSlots');
        const customRuneValidationAlert = document.getElementById('customRuneValidationAlert');
        const btnSaveCustomRune = document.getElementById('btnSaveCustomRune');
        const btnApplyCustomRune = document.getElementById('btnApplyCustomRune');
        const savedRunesList = document.getElementById('savedRunesList');
        const savedRunesEmpty = document.getElementById('savedRunesEmpty');

        async function fetchRuneTrees() {
            if (runeTreesCatalog) return runeTreesCatalog;
            try {
                const res = await fetch('/api/runes/trees');
                if (res.ok) {
                    runeTreesCatalog = await res.json();
                }
            } catch (err) {
                console.error('Failed to fetch rune trees:', err);
            }
            return runeTreesCatalog;
        }

        async function fetchSavedRunes() {
            try {
                const res = await fetch('/api/runes/custom');
                if (res.ok) {
                    const data = await res.json();
                    savedCustomRunes = data.pages || [];
                    if (savedRunesCount) savedRunesCount.innerText = savedCustomRunes.length;
                    renderSavedRunesList();
                }
            } catch (err) {
                console.error('Failed to fetch saved runes:', err);
            }
        }

        function switchCustomRuneTab(tab) {
            if (tab === 'builder') {
                tabBtnBuilder.classList.add('active');
                tabBtnSaved.classList.remove('active');
                customRuneBuilderView.style.display = 'flex';
                customRuneSavedView.style.display = 'none';
            } else {
                tabBtnBuilder.classList.remove('active');
                tabBtnSaved.classList.add('active');
                customRuneBuilderView.style.display = 'none';
                customRuneSavedView.style.display = 'flex';
                fetchSavedRunes();
            }
        }

        async function openCustomRuneBuilderModal() {
            if (customRuneModal) customRuneModal.style.display = 'flex';
            switchCustomRuneTab('builder');
            await fetchRuneTrees();
            await fetchSavedRunes();
            renderBuilderInterface();
        }

        function closeCustomRuneBuilderModal() {
            if (customRuneModal) customRuneModal.style.display = 'none';
        }

        function renderBuilderInterface() {
            if (!runeTreesCatalog) return;
            renderPrimaryTrees();
            renderPrimaryRunes();
            renderSecondaryTrees();
            renderSecondaryRunes();
            renderStatShards();
            validateCustomRuneState();
        }

        function renderPrimaryTrees() {
            if (!primaryTreesGrid || !runeTreesCatalog || !runeTreesCatalog.trees) return;
            primaryTreesGrid.innerHTML = '';
            runeTreesCatalog.trees.forEach(tree => {
                const btn = document.createElement('div');
                const isSelected = (tree.id === customRuneState.primaryStyleId);
                btn.className = 'tree-btn' + (isSelected ? ' selected' : '');
                btn.style.setProperty('--tree-color', tree.color || '#c8aa6e');
                btn.innerHTML = `
                    <img class="tree-btn-icon" src="${tree.icon}" onerror="this.src='https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/7201_precision.png'" alt="${tree.name}">
                    <span class="tree-btn-name">${tree.name}</span>
                `;
                btn.addEventListener('click', () => {
                    if (customRuneState.primaryStyleId !== tree.id) {
                        customRuneState.primaryStyleId = tree.id;
                        // If secondary tree is the same as new primary, change secondary tree
                        if (customRuneState.subStyleId === tree.id) {
                            const other = runeTreesCatalog.trees.find(t => t.id !== tree.id);
                            customRuneState.subStyleId = other ? other.id : 8100;
                        }
                        // Reset primary runes to defaults for this tree
                        customRuneState.primaryRunes = tree.slots.map(s => s.runes[0] ? s.runes[0].id : 0);
                        renderBuilderInterface();
                    }
                });
                primaryTreesGrid.appendChild(btn);
            });
        }

        function renderPrimaryRunes() {
            if (!primaryRunesSlots || !runeTreesCatalog) return;
            primaryRunesSlots.innerHTML = '';
            const tree = runeTreesCatalog.trees.find(t => t.id === customRuneState.primaryStyleId);
            if (!tree || !tree.slots) return;

            tree.slots.forEach((slot, slotIdx) => {
                const row = document.createElement('div');
                row.className = 'rune-slot-row';
                const isKeystone = (slotIdx === 0);

                slot.runes.forEach(rune => {
                    const btn = document.createElement('div');
                    const isSelected = (customRuneState.primaryRunes[slotIdx] === rune.id);
                    btn.className = 'rune-item-btn' + (isKeystone ? ' keystone' : '') + (isSelected ? ' selected' : '');
                    btn.title = `${rune.name} — ${rune.desc || ''}`;
                    btn.innerHTML = `<img class="rune-item-img" src="${rune.icon}" onerror="this.src='https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodsadaptiveforceicon.png'" alt="${rune.name}">`;
                    btn.addEventListener('click', () => {
                        customRuneState.primaryRunes[slotIdx] = rune.id;
                        renderPrimaryRunes();
                        validateCustomRuneState();
                    });
                    row.appendChild(btn);
                });
                primaryRunesSlots.appendChild(row);
            });
        }

        function renderSecondaryTrees() {
            if (!secondaryTreesGrid || !runeTreesCatalog || !runeTreesCatalog.trees) return;
            secondaryTreesGrid.innerHTML = '';
            runeTreesCatalog.trees.forEach(tree => {
                const isPrimary = (tree.id === customRuneState.primaryStyleId);
                const isSelected = (tree.id === customRuneState.subStyleId);
                const btn = document.createElement('div');
                btn.className = 'tree-btn' + (isPrimary ? ' disabled' : '') + (isSelected ? ' selected' : '');
                btn.style.setProperty('--tree-color', tree.color || '#c8aa6e');
                btn.innerHTML = `
                    <img class="tree-btn-icon" src="${tree.icon}" onerror="this.src='https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/7201_precision.png'" alt="${tree.name}">
                    <span class="tree-btn-name">${tree.name}</span>
                `;
                if (!isPrimary) {
                    btn.addEventListener('click', () => {
                        if (customRuneState.subStyleId !== tree.id) {
                            customRuneState.subStyleId = tree.id;
                            // Pick default 2 runes from row 1 & row 2
                            const secSlots = tree.slots.slice(1);
                            customRuneState.secondaryRunes = [
                                secSlots[0] && secSlots[0].runes[0] ? secSlots[0].runes[0].id : 0,
                                secSlots[1] && secSlots[1].runes[0] ? secSlots[1].runes[0].id : 0
                            ];
                            renderSecondaryTrees();
                            renderSecondaryRunes();
                            validateCustomRuneState();
                        }
                    });
                }
                secondaryTreesGrid.appendChild(btn);
            });
        }

        function renderSecondaryRunes() {
            if (!secondaryRunesSlots || !runeTreesCatalog) return;
            secondaryRunesSlots.innerHTML = '';
            const tree = runeTreesCatalog.trees.find(t => t.id === customRuneState.subStyleId);
            if (!tree || !tree.slots) return;

            // Secondary slots: Row 1, Row 2, Row 3
            const minorSlots = tree.slots.slice(1);

            minorSlots.forEach((slot, localSlotIdx) => {
                const rowIdx = localSlotIdx + 1; // 1, 2, 3
                const row = document.createElement('div');
                row.className = 'rune-slot-row';

                slot.runes.forEach(rune => {
                    const btn = document.createElement('div');
                    const isSelected = customRuneState.secondaryRunes.includes(rune.id);
                    btn.className = 'rune-item-btn' + (isSelected ? ' selected' : '');
                    btn.title = `${rune.name} — ${rune.desc || ''}`;
                    btn.innerHTML = `<img class="rune-item-img" src="${rune.icon}" onerror="this.src='https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodsadaptiveforceicon.png'" alt="${rune.name}">`;

                    btn.addEventListener('click', () => {
                        handleSecondaryRuneClick(rune.id, rowIdx, minorSlots);
                    });
                    row.appendChild(btn);
                });
                secondaryRunesSlots.appendChild(row);
            });
        }

        function handleSecondaryRuneClick(runeId, rowIdx, minorSlots) {
            // Find which rows are currently selected
            const secTree = runeTreesCatalog.trees.find(t => t.id === customRuneState.subStyleId);
            if (!secTree) return;

            const getRuneRow = (id) => {
                for (let r = 1; r < secTree.slots.length; r++) {
                    if (secTree.slots[r].runes.some(rn => rn.id === id)) return r;
                }
                return 0;
            };

            const sel0 = customRuneState.secondaryRunes[0];
            const sel1 = customRuneState.secondaryRunes[1];
            const row0 = getRuneRow(sel0);
            const row1 = getRuneRow(sel1);

            if (sel0 === runeId || sel1 === runeId) {
                // Clicking an already selected rune: no-op or leave it
                return;
            }

            if (row0 === rowIdx) {
                // Clicked in row 0: replace sel0
                customRuneState.secondaryRunes[0] = runeId;
            } else if (row1 === rowIdx) {
                // Clicked in row 1: replace sel1
                customRuneState.secondaryRunes[1] = runeId;
            } else {
                // Clicked in the 3rd row: replace the 2nd selection so user has row0 + new row
                customRuneState.secondaryRunes[1] = runeId;
            }

            renderSecondaryRunes();
            validateCustomRuneState();
        }

        function renderStatShards() {
            if (!statShardsSlots || !runeTreesCatalog || !runeTreesCatalog.statShards) return;
            statShardsSlots.innerHTML = '';

            runeTreesCatalog.statShards.forEach((group, sIdx) => {
                const row = document.createElement('div');
                row.className = 'shard-row';

                const label = document.createElement('span');
                label.className = 'shard-row-title';
                label.innerText = group.name || `Slot ${sIdx + 1}`;
                row.appendChild(label);

                const opts = document.createElement('div');
                opts.className = 'shard-options';

                group.shards.forEach(sh => {
                    const btn = document.createElement('div');
                    const isSelected = (customRuneState.statShards[sIdx] === sh.id);
                    btn.className = 'shard-btn' + (isSelected ? ' selected' : '');
                    btn.title = `${sh.name} (${sh.desc})`;
                    btn.innerHTML = `<img class="shard-img" src="${sh.icon}" alt="${sh.name}">`;
                    btn.addEventListener('click', () => {
                        customRuneState.statShards[sIdx] = sh.id;
                        renderStatShards();
                        validateCustomRuneState();
                    });
                    opts.appendChild(btn);
                });

                row.appendChild(opts);
                statShardsSlots.appendChild(row);
            });
        }

        function validateCustomRuneState() {
            const name = (customRuneNameInput ? customRuneNameInput.value : '').trim();
            customRuneState.name = name || 'Özel Rün';

            let valid = true;
            let errMsg = '';

            if (!name) {
                valid = false;
                errMsg = 'Rün sayfası ismi boş olamaz.';
            } else if (customRuneState.primaryStyleId === customRuneState.subStyleId) {
                valid = false;
                errMsg = 'Birincil ve ikincil rün ağaçları aynı olamaz.';
            } else if (customRuneState.primaryRunes.length !== 4 || customRuneState.primaryRunes.some(id => !id)) {
                valid = false;
                errMsg = 'Lütfen birincil ağaçtan 1 Anahtar Rün ve 3 Küçük Rün seçin.';
            } else if (customRuneState.secondaryRunes.length !== 2 || customRuneState.secondaryRunes.some(id => !id)) {
                valid = false;
                errMsg = 'Lütfen ikincil ağaçtan 2 Küçük Rün seçin.';
            } else if (customRuneState.statShards.length !== 3 || customRuneState.statShards.some(id => !id)) {
                valid = false;
                errMsg = 'Lütfen 3 nitelik parçasını da seçin.';
            }

            if (!valid && customRuneValidationAlert) {
                customRuneValidationAlert.style.display = 'block';
                customRuneValidationAlert.innerText = errMsg;
                if (btnSaveCustomRune) btnSaveCustomRune.disabled = true;
                if (btnApplyCustomRune) btnApplyCustomRune.disabled = true;
            } else if (customRuneValidationAlert) {
                customRuneValidationAlert.style.display = 'none';
                if (btnSaveCustomRune) btnSaveCustomRune.disabled = false;
                if (btnApplyCustomRune) btnApplyCustomRune.disabled = false;
            }
            return valid;
        }

        function getFullPerkIds() {
            return [
                ...customRuneState.primaryRunes,
                ...customRuneState.secondaryRunes,
                ...customRuneState.statShards
            ];
        }

        async function saveCustomRune() {
            if (!validateCustomRuneState()) return;

            const payload = {
                name: customRuneState.name,
                primaryStyleId: customRuneState.primaryStyleId,
                subStyleId: customRuneState.subStyleId,
                selectedPerkIds: getFullPerkIds()
            };

            try {
                if (btnSaveCustomRune) btnSaveCustomRune.innerText = 'KAYDEDİLİYOR...';
                const res = await fetch('/api/runes/custom', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    await fetchSavedRunes();
                    switchCustomRuneTab('saved');
                } else {
                    const err = await res.json();
                    alert(err.detail || 'Rün kaydedilemedi.');
                }
            } catch (exc) {
                alert('Bağlantı hatası: ' + exc);
            } finally {
                if (btnSaveCustomRune) btnSaveCustomRune.innerText = '💾 RÜNÜ KAYDET';
            }
        }

        async function applyCustomRuneDirectly() {
            if (!validateCustomRuneState()) return;
            const payload = {
                name: customRuneState.name,
                primaryStyleId: customRuneState.primaryStyleId,
                subStyleId: customRuneState.subStyleId,
                selectedPerkIds: getFullPerkIds()
            };

            if (btnApplyCustomRune) {
                btnApplyCustomRune.disabled = true;
                btnApplyCustomRune.innerText = 'UYGULANIYOR...';
            }

            try {
                if (socket && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        type: 'APPLY_RUNES',
                        token: sessionToken,
                        page: payload,
                        championId: selectedChampId
                    }));
                } else {
                    const res = await fetch('/api/runes/apply', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    if (!res.ok) {
                        const err = await res.json();
                        alert(err.detail || 'İstemciye uygulanamadı.');
                    }
                }
                closeCustomRuneBuilderModal();
            } catch (err) {
                alert('Uygulama hatası: ' + err);
            } finally {
                if (btnApplyCustomRune) {
                    btnApplyCustomRune.disabled = false;
                    btnApplyCustomRune.innerText = '⚡ İSTEMCİYE UYGULA';
                }
            }
        }

        function renderSavedRunesList() {
            if (!savedRunesList || !savedRunesEmpty) return;
            savedRunesList.innerHTML = '';
            if (savedCustomRunes.length === 0) {
                savedRunesEmpty.style.display = 'block';
                return;
            }
            savedRunesEmpty.style.display = 'none';

            savedCustomRunes.forEach(page => {
                const card = document.createElement('div');
                card.className = 'saved-rune-card';
                const perksSummary = Array.isArray(page.perkNames) ? page.perkNames.slice(0, 6).join(', ') : '';

                card.innerHTML = `
                    <div class="saved-rune-card-header">
                        <span class="saved-rune-card-title">${page.name}</span>
                        <span class="saved-rune-trees">${page.primaryTreeName} + ${page.subTreeName}</span>
                    </div>
                    <div class="saved-rune-perks">${perksSummary}</div>
                    <div class="saved-rune-actions">
                        <button class="btn-saved-apply" type="button">⚡ İstemciye Uygula</button>
                        <button class="btn-saved-delete" type="button">🗑️ Sil</button>
                    </div>
                `;

                const applyBtn = card.querySelector('.btn-saved-apply');
                applyBtn.addEventListener('click', async () => {
                    applyBtn.innerText = 'Uygulanıyor...';
                    try {
                        const payload = {
                            name: page.name,
                            primaryStyleId: page.primaryStyleId,
                            subStyleId: page.subStyleId,
                            selectedPerkIds: page.selectedPerkIds
                        };
                        if (socket && socket.readyState === WebSocket.OPEN) {
                            socket.send(JSON.stringify({
                                type: 'APPLY_RUNES',
                                token: sessionToken,
                                page: payload,
                                championId: selectedChampId
                            }));
                        } else {
                            await fetch('/api/runes/apply', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify(payload)
                            });
                        }
                        closeCustomRuneBuilderModal();
                    } catch (e) {
                        alert('Uygulanamadı: ' + e);
                    } finally {
                        applyBtn.innerText = '⚡ İstemciye Uygula';
                    }
                });

                const delBtn = card.querySelector('.btn-saved-delete');
                delBtn.addEventListener('click', async () => {
                    if (!confirm(`"${page.name}" rün sayfasını silmek istiyor musunuz?`)) return;
                    try {
                        const res = await fetch(`/api/runes/custom/${page.id}`, { method: 'DELETE' });
                        if (res.ok) {
                            await fetchSavedRunes();
                        }
                    } catch (e) {
                        alert('Silinemedi: ' + e);
                    }
                });

                savedRunesList.appendChild(card);
            });
        }

        // Event Listeners for Custom Rune Builder
        if (btnOpenCustomRuneBuilder) btnOpenCustomRuneBuilder.addEventListener('click', openCustomRuneBuilderModal);
        if (customRuneModalClose) customRuneModalClose.addEventListener('click', closeCustomRuneBuilderModal);
        if (customRuneModal) {
            customRuneModal.addEventListener('click', (e) => {
                if (e.target === customRuneModal) closeCustomRuneBuilderModal();
            });
        }
        if (tabBtnBuilder) tabBtnBuilder.addEventListener('click', () => switchCustomRuneTab('builder'));
        if (tabBtnSaved) tabBtnSaved.addEventListener('click', () => switchCustomRuneTab('saved'));
        if (customRuneNameInput) customRuneNameInput.addEventListener('input', validateCustomRuneState);
        if (btnSaveCustomRune) btnSaveCustomRune.addEventListener('click', saveCustomRune);
        if (btnApplyCustomRune) btnApplyCustomRune.addEventListener('click', applyCustomRuneDirectly);


        let timerSecondsRemaining = 0;
        let timerTotalSeconds = 30;
        let timerCountdownInterval = null;

        const POSITION_NAMES = {
            'top': 'ÜST KORİDOR',
            'jungle': 'ORMAN',
            'middle': 'ORTA KORİDOR',
            'mid': 'ORTA KORİDOR',
            'bottom': 'ALT KORİDOR',
            'bot': 'ALT KORİDOR',
            'utility': 'DESTEK',
            'support': 'DESTEK',
            '': 'SERBEST'
        };

        const STAGE_LABELS = {
            'PLANNING': 'HAZIRLIK EVRESİ',
            'BAN_MY_TURN': 'YASAKLAMA SIRASI SİZDE',
            'BAN_WAITING': 'YASAKLAMA AŞAMASI',
            'PICK_MY_TURN': 'SEÇİM SIRASI SİZDE',
            'PICK_WAITING': 'SEÇİM AŞAMASI',
            'PICK_LOCKED': 'ŞAMPİYON KİLİTLENDİ',
            'ARAM_BENCH': 'ARAM HAZIRLIK',
            'FINALIZATION': 'SON HAZIRLIKLAR',
            'GAME_STARTING': 'OYUN BAŞLIYOR',
            'UNSUPPORTED_SAFE': 'GÜVENLİ GÖRÜNÜM'
        };

        let isLcuConnected = false;
        let currentPhaseStr = 'None';
        let queueStartTime = 0;
        let queueTimerInterval = null;
        let inGameStartTime = 0;
        let inGameTimerInterval = null;
        let isQueueActionInFlight = false;
        let cachedPlayerChamp = null;

        function formatDuration(sec) {
            const total = Math.max(0, Math.floor(sec || 0));
            const m = Math.floor(total / 60).toString().padStart(2, '0');
            const s = (total % 60).toString().padStart(2, '0');
            return `${m}:${s}`;
        }

        function formatRoleName(role) {
            if (!role) return '';
            const map = {
                'top': 'Üst Koridor',
                'jungle': 'Orman',
                'middle': 'Orta Koridor',
                'mid': 'Orta Koridor',
                'bottom': 'Alt Koridor',
                'bot': 'Alt Koridor',
                'utility': 'Destek',
                'support': 'Destek'
            };
            return map[role.toLowerCase()] || role.toUpperCase();
        }

        function updateInGamePlayerInfo() {
            if (!inGameChampInfo) return;
            if (cachedPlayerChamp && (cachedPlayerChamp.name || cachedPlayerChamp.role)) {
                inGameChampInfo.style.display = 'flex';
                if (inGameChampName) inGameChampName.innerText = cachedPlayerChamp.name || 'Bilinmiyor';
                if (inGameRole) inGameRole.innerText = formatRoleName(cachedPlayerChamp.role);
                if (inGameChampImg) {
                    if (cachedPlayerChamp.icon) {
                        inGameChampImg.src = cachedPlayerChamp.icon;
                        inGameChampImg.style.display = 'block';
                    } else {
                        inGameChampImg.style.display = 'none';
                    }
                }
            } else {
                inGameChampInfo.style.display = 'none';
            }
        }

        function updateQueueClock() {
            if (!queueStartTime) return;
            const elapsed = Math.max(0, Math.floor((Date.now() - queueStartTime) / 1000));
            if (queueTimerClock) queueTimerClock.innerText = formatDuration(elapsed);
        }

        function startQueueTimer(initialSeconds = 0) {
            if (queueTimerInterval) {
                clearInterval(queueTimerInterval);
                queueTimerInterval = null;
            }

            if (initialSeconds > 0) {
                queueStartTime = Date.now() - (Math.max(0, initialSeconds) * 1000);
            } else if (!queueStartTime) {
                queueStartTime = Date.now();
            }

            if (queueTimerCard) queueTimerCard.style.display = 'flex';
            if (modeSelectorCard) modeSelectorCard.style.display = 'none';

            updateQueueClock();
            queueTimerInterval = setInterval(updateQueueClock, 1000);
        }

        function stopQueueTimer() {
            if (queueTimerInterval) {
                clearInterval(queueTimerInterval);
                queueTimerInterval = null;
            }
            queueStartTime = 0;
            if (queueTimerCard) queueTimerCard.style.display = 'none';
            if (queueTimerClock) queueTimerClock.innerText = '00:00';
            if (queueEstimatedClock) queueEstimatedClock.innerText = 'Tahmini: --:--';
            if (modeSelectorCard) modeSelectorCard.style.display = 'flex';
        }

        function updateInGameClock() {
            if (!inGameStartTime) return;
            const elapsed = Math.max(0, Math.floor((Date.now() - inGameStartTime) / 1000));
            if (inGameClock) inGameClock.innerText = formatDuration(elapsed);
        }

        function startInGameTimer(initialSeconds = 0) {
            if (inGameTimerInterval) {
                clearInterval(inGameTimerInterval);
                inGameTimerInterval = null;
            }

            if (initialSeconds > 0) {
                inGameStartTime = Date.now() - (Math.max(0, initialSeconds) * 1000);
            } else if (!inGameStartTime) {
                inGameStartTime = Date.now();
            }

            if (inGameView) inGameView.style.display = 'flex';
            updateInGamePlayerInfo();
            updateInGameClock();
            inGameTimerInterval = setInterval(updateInGameClock, 1000);
        }

        function stopInGameTimer() {
            if (inGameTimerInterval) {
                clearInterval(inGameTimerInterval);
                inGameTimerInterval = null;
            }
            inGameStartTime = 0;
            if (inGameView) inGameView.style.display = 'none';
            if (inGameClock) inGameClock.innerText = '00:00';
        }

        function refreshActiveTimers() {
            if (queueStartTime && queueTimerClock) {
                updateQueueClock();
            }
            if (inGameStartTime && inGameClock) {
                updateInGameClock();
            }
        }

        // Ready Check Modal Lifecycle
        let readyCheckTimerInterval = null;
        let readyCheckRemaining = 10;
        const READY_CHECK_MAX = 10;
        const READY_RING_CIRCUMFERENCE = 226;
        let isReadyCheckActionInFlight = false;

        function startReadyCheckModalTimer(seconds = 10) {
            if (readyCheckTimerInterval) {
                clearInterval(readyCheckTimerInterval);
                readyCheckTimerInterval = null;
            }
            readyCheckRemaining = Math.max(1, seconds);
            const ringProgress = document.getElementById('readyCheckRingProgress');
            const countdownEl = document.getElementById('readyCheckCountdown');

            const updateRing = () => {
                if (countdownEl) {
                    countdownEl.innerText = Math.max(0, readyCheckRemaining);
                }
                if (ringProgress) {
                    const frac = Math.max(0, readyCheckRemaining) / READY_CHECK_MAX;
                    const offset = READY_RING_CIRCUMFERENCE * (1 - frac);
                    ringProgress.style.strokeDashoffset = offset;
                    if (frac <= 0.3) {
                        ringProgress.style.stroke = 'var(--danger)';
                    } else {
                        ringProgress.style.stroke = 'var(--gold-2)';
                    }
                }
            };
            updateRing();
            readyCheckTimerInterval = setInterval(() => {
                readyCheckRemaining -= 1;
                updateRing();
                if (readyCheckRemaining <= 0) {
                    clearInterval(readyCheckTimerInterval);
                    readyCheckTimerInterval = null;
                    if (readyCheckStatus) {
                        readyCheckStatus.innerText = 'Süre doldu!';
                    }
                    if (readyAcceptBtn) readyAcceptBtn.disabled = true;
                    if (readyDeclineBtn) readyDeclineBtn.disabled = true;
                }
            }, 1000);
        }

        function openReadyCheckModal() {
            if (!readyCheckModal) return;
            isReadyCheckActionInFlight = false;
            readyCheckModal.style.display = 'flex';
            if (readyAcceptBtn) {
                readyAcceptBtn.disabled = false;
                readyAcceptBtn.classList.remove('accepted');
                readyAcceptBtn.innerHTML = '<span>⚔️</span> KABUL ET';
            }
            if (readyDeclineBtn) {
                readyDeclineBtn.disabled = false;
                readyDeclineBtn.innerText = 'REDDET';
            }
            if (readyCheckStatus) {
                readyCheckStatus.innerText = 'Kabul bekleniyor...';
            }
            startReadyCheckModalTimer(10);
            playChime();
            triggerVibrate();
        }

        function closeReadyCheckModal() {
            if (!readyCheckModal) return;
            readyCheckModal.style.display = 'none';
            if (readyCheckTimerInterval) {
                clearInterval(readyCheckTimerInterval);
                readyCheckTimerInterval = null;
            }
            isReadyCheckActionInFlight = false;
        }

        function setStatus(text, type) {
            statusText.innerText = text;
            statusBadge.className = 'status-badge ' + (type || '');
            if (stationWsStatus) stationWsStatus.innerText = text;
        }

        function updateLcuStatus(connected) {
            isLcuConnected = Boolean(connected);
            if (lcuBadge) {
                if (isLcuConnected) {
                    lcuBadge.innerText = '● LoL Bağlı';
                    lcuBadge.className = 'lcu-subbadge connected';
                } else {
                    lcuBadge.innerText = '○ LoL Bekleniyor';
                    lcuBadge.className = 'lcu-subbadge';
                }
            }
            if (stationLcuStatus) {
                stationLcuStatus.innerText = isLcuConnected ? 'Bağlandı' : 'İstemci Bekleniyor';
                stationLcuStatus.style.color = isLcuConnected ? 'var(--success)' : 'var(--danger)';
            }
            if (currentPhaseStr) {
                updateGameflow(currentPhaseStr);
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
            } catch (e) {}
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

        function updateGameflow(phase, search) {
            const p = String(phase || 'None');
            currentPhaseStr = p;
            if (stationPhase) stationPhase.innerText = p;

            if (p === 'ChampSelect') {
                closeReadyCheckModal();
                stopInGameTimer();
                stopQueueTimer();
                mainView.style.display = 'none';
                if (inGameView) inGameView.style.display = 'none';
                const isLocked = Boolean(currentChampSelect && (currentChampSelect.isLocalPickCompleted || currentChampSelect.is_local_pick_completed));
                if (isLocked) {
                    if (champSelectView) champSelectView.style.display = 'none';
                    if (postLockLobbyView) postLockLobbyView.style.display = 'flex';
                } else {
                    if (champSelectView) champSelectView.style.display = 'flex';
                    if (postLockLobbyView) postLockLobbyView.style.display = 'none';
                }
                if (socket && socket.readyState === WebSocket.OPEN) {
                    try {
                        socket.send(JSON.stringify({ type: 'GET_CHAMP_SELECT', token: sessionToken }));
                        socket.send(JSON.stringify({ type: 'GET_GRID_CHAMPIONS', token: sessionToken }));
                        socket.send(JSON.stringify({ type: 'GET_SPELLS', token: sessionToken }));
                    } catch (e) {}
                }
            } else if (p === 'InProgress' || p === 'GameStart') {
                closeReadyCheckModal();
                stopQueueTimer();
                mainView.style.display = 'none';
                if (champSelectView) champSelectView.style.display = 'none';
                if (postLockLobbyView) postLockLobbyView.style.display = 'none';
                if (inGameView) inGameView.style.display = 'flex';
                startInGameTimer();
                phaseDisplay.innerText = '🔥 Oyunda';
                if (infoMessage) infoMessage.innerText = 'Karşılaşma devam ediyor.';
            } else {
                stopInGameTimer();
                if (inGameView) inGameView.style.display = 'none';
                if (champSelectView) champSelectView.style.display = 'none';
                if (postLockLobbyView) postLockLobbyView.style.display = 'none';
                mainView.style.display = 'flex';
                clearInterval(timerCountdownInterval);
                lastFetchedRunesChampId = 0;

                if (p === 'ReadyCheck') {
                    stopQueueTimer();
                    phaseDisplay.innerText = '⚔️ MAÇ BULUNDU!';
                    phaseCard.classList.add('ready-check-active');
                    if (infoMessage) infoMessage.innerText = 'Eşleşme bulundu! Sırayı kaçırmamak için onaylayın.';
                    openReadyCheckModal();
                } else {
                    closeReadyCheckModal();
                    phaseCard.classList.remove('ready-check-active');

                    if (p === 'Matchmaking') {
                        phaseDisplay.innerText = '⏳ Sırada...';
                        if (infoMessage) infoMessage.innerText = 'Eşleşme aranıyor...';
                        const timeSec = (search && search.time_in_queue) || 0;
                        startQueueTimer(timeSec);
                        if (search && search.estimated_delay > 0 && queueEstimatedClock) {
                            queueEstimatedClock.innerText = 'Tahmini: ' + formatDuration(search.estimated_delay);
                        }
                    } else if (p === 'Lobby') {
                        stopQueueTimer();
                        phaseDisplay.innerText = '🏠 Lobide';
                        if (infoMessage) infoMessage.innerText = 'Oda liderinin sırayı başlatması bekleniyor.';
                    } else if (p === 'WaitingForStats' || p === 'PreEndOfGame' || p === 'EndOfGame') {
                        stopQueueTimer();
                        phaseDisplay.innerText = '🏁 Oyun Sonu';
                        if (infoMessage) infoMessage.innerText = 'Karşılaşma tamamlandı. İstatistikler bekleniyor.';
                    } else {
                        // Phase is 'None'
                        stopQueueTimer();
                        if (isLcuConnected) {
                            phaseDisplay.innerText = '🎮 İstemci Hazır (Boşta)';
                            if (infoMessage) infoMessage.innerText = 'League of Legends istemcisi bağlı. Lobi oluşturabilir veya maç arayabilirsiniz.';
                        } else {
                            phaseDisplay.innerText = '🎮 Oyun Dışı';
                            if (infoMessage) infoMessage.innerText = 'League Client bağlantısı bekleniyor...';
                        }
                    }
                }
            }
        }

        function syncTradeBanners(type, sender, text, showAcceptDecline, showCancel) {
            [
                { banner: tradeNoticeBanner, typeEl: tradeNoticeType, senderEl: tradeNoticeSender, textEl: tradeNoticeText, acceptEl: tradeAcceptBtn, declineEl: tradeDeclineBtn, cancelEl: tradeCancelBtn },
                { banner: lobbyTradeNoticeBanner, typeEl: lobbyTradeNoticeType, senderEl: lobbyTradeNoticeSender, textEl: lobbyTradeNoticeText, acceptEl: lobbyTradeAcceptBtn, declineEl: lobbyTradeDeclineBtn, cancelEl: lobbyTradeCancelBtn }
            ].forEach(b => {
                if (!b.banner) return;
                b.banner.dataset.type = type;
                if (b.typeEl) b.typeEl.innerText = (type === 'lane') ? 'KORİDOR TAKASI' : 'ŞAMPİYON TAKASI';
                if (b.senderEl) b.senderEl.innerText = sender;
                if (b.textEl) b.textEl.innerText = text;
                if (b.acceptEl) b.acceptEl.style.display = showAcceptDecline ? 'block' : 'none';
                if (b.declineEl) b.declineEl.style.display = showAcceptDecline ? 'block' : 'none';
                if (b.cancelEl) b.cancelEl.style.display = showCancel ? 'block' : 'none';
                b.banner.style.display = 'flex';
            });
        }

        function hideTradeBanners(type) {
            [tradeNoticeBanner, lobbyTradeNoticeBanner].forEach(banner => {
                if (banner && (!type || banner.dataset.type === type)) {
                    banner.style.display = 'none';
                }
            });
        }

        function handleLaneSwapsUpdate(swaps) {
            if (!Array.isArray(swaps) || swaps.length === 0) {
                hideTradeBanners('lane');
                return;
            }
            const localCellId = currentChampSelect ? (currentChampSelect.localPlayerCellId ?? currentChampSelect.local_player_cell_id) : -1;
            const incoming = swaps.find(s => s.to_cell_id === localCellId && s.status === 'PENDING');
            if (incoming) {
                activeSwapCellId = incoming.from_cell_id;
                syncTradeBanners('lane', `Oyuncu ${incoming.from_cell_id}`, `${incoming.from_assigned_position || 'Koridor'} ile ${incoming.to_assigned_position || 'Koridor'} takası teklif edildi.`, true, false);
                return;
            }
            const outgoing = swaps.find(s => s.from_cell_id === localCellId && s.status === 'PENDING');
            if (outgoing) {
                activeSwapCellId = outgoing.to_cell_id;
                syncTradeBanners('lane', `Oyuncu ${outgoing.to_cell_id}`, 'Koridor takası isteği gönderildi. Yanıt bekleniyor...', false, true);
                return;
            }
            hideTradeBanners('lane');
        }

        function checkIncomingTrades(trades) {
            if (!Array.isArray(trades) || trades.length === 0) {
                hideTradeBanners('champ');
                return;
            }
            const received = trades.find(t => (t.state || '').toUpperCase() === 'RECEIVED');
            if (received) {
                activeTradeId = received.trade_id || received.id;
                syncTradeBanners('champ', `Takım Arkadaşı (${received.cell_id})`, 'Şampiyon takası teklif edildi.', true, false);
                return;
            }
            const sent = trades.find(t => (t.state || '').toUpperCase() === 'SENT');
            if (sent) {
                activeTradeId = sent.trade_id || sent.id;
                syncTradeBanners('champ', `Takım Arkadaşı (${sent.cell_id})`, 'Şampiyon takası teklifi gönderildi. Bekleniyor...', false, true);
                return;
            }
            hideTradeBanners('champ');
        }

        const POSITION_SHORT_NAMES = {
            top: 'TOP',
            jungle: 'JNG',
            jug: 'JNG',
            middle: 'MID',
            mid: 'MID',
            bottom: 'BOT',
            bot: 'BOT',
            utility: 'SUP',
            support: 'SUP',
            supp: 'SUP',
        };

        function renderPostLockLobby(data) {
            if (!data) return;

            // 1. Update Timer
            if (data.timer && lobbyTimerBadge) {
                const secondsLeft = Number(
                    data.timer.timeLeftSeconds !== undefined
                    ? data.timer.timeLeftSeconds
                    : (data.timer.adjustedTimeLeftInPhase || data.timer.timeLeftInPhase || 30000) / 1000
                );
                const s = Math.max(0, Math.floor(secondsLeft));
                lobbyTimerBadge.innerText = s > 0 ? (s + 's') : '0s';
                if (s <= 5) {
                    lobbyTimerBadge.style.color = 'var(--danger)';
                    lobbyTimerBadge.style.borderColor = 'var(--danger)';
                } else {
                    lobbyTimerBadge.style.color = 'var(--gold-1)';
                    lobbyTimerBadge.style.borderColor = 'var(--gold-2)';
                }
            }

            const localCellId = data.localPlayerCellId !== undefined ? data.localPlayerCellId : (data.local_player_cell_id ?? -1);
            const myTeam = Array.isArray(data.myTeam) ? data.myTeam : [];
            const theirTeam = Array.isArray(data.theirTeam) ? data.theirTeam : [];

            if (lobbyMyTeamCount) lobbyMyTeamCount.innerText = `${myTeam.length} Oyuncu`;
            if (lobbyTheirTeamCount) lobbyTheirTeamCount.innerText = `${theirTeam.length} Oyuncu`;

            // 2. Render My Team
            if (lobbyMyTeamList) {
                lobbyMyTeamList.innerHTML = '';
                myTeam.forEach((member, idx) => {
                    const card = createLobbyMemberCard(member, true, localCellId, idx);
                    lobbyMyTeamList.appendChild(card);
                });
            }

            // 3. Render Their Team (Enemy Team)
            if (lobbyTheirTeamList) {
                lobbyTheirTeamList.innerHTML = '';
                theirTeam.forEach((member, idx) => {
                    const card = createLobbyMemberCard(member, false, -1, idx);
                    lobbyTheirTeamList.appendChild(card);
                });
            }
        }

        function createLobbyMemberCard(member, isMyTeam, localCellId, idx) {
            const cellId = member.cellId !== undefined ? member.cellId : member.cell_id;
            const isMe = (cellId === localCellId);
            const champId = member.championId || member.champion_id || 0;
            const pickIntent = member.championPickIntent || member.champion_pick_intent || 0;

            const card = document.createElement('div');
            card.className = `lobby-player-card ${isMyTeam ? 'team-blue' : 'team-red'}${isMe ? ' is-me' : ''}`;
            card.dataset.cellId = cellId;

            // Display Name resolution
            const rawName = (member.summonerName || member.summoner_name || '').trim();
            let displayName = '';
            if (isMyTeam) {
                if (isMe) {
                    displayName = rawName || 'Sen';
                } else {
                    displayName = rawName || `Dost Sihirdar ${idx + 1}`;
                }
            } else {
                // Enemy Team: Riot rules hide summoner names during draft to prevent dodging.
                // If LCU provides a legitimate name (custom game / unmasked), display it.
                // Otherwise show 'Gizli / Rakip Oyuncu' - NEVER fabricate fake names!
                if (rawName && !rawName.startsWith('Summoner ') && rawName !== '0') {
                    displayName = rawName;
                } else {
                    displayName = 'Gizli / Rakip Oyuncu';
                }
            }

            // Champion Name and Icon resolution
            let champName = '';
            let avatarHtml = '';
            if (isMyTeam) {
                const effChampId = champId > 0 ? champId : pickIntent;
                if (effChampId > 0) {
                    champName = findChampionName(effChampId);
                    const iconUrl = getChampionIconUrl(effChampId);
                    avatarHtml = `<img class="lobby-champ-img" src="${iconUrl}" onerror="this.onerror=null;this.className='lobby-avatar-mystery';this.innerText='?';" alt="${champName}">`;
                } else {
                    champName = 'Seçim Yapılıyor...';
                    avatarHtml = `<div class="lobby-avatar-mystery">?</div>`;
                }
            } else {
                // Enemy Team: Only show champion if locked in (champId > 0). If 0, it is hidden/not locked.
                if (champId > 0) {
                    champName = findChampionName(champId);
                    const iconUrl = getChampionIconUrl(champId);
                    avatarHtml = `<img class="lobby-champ-img" src="${iconUrl}" onerror="this.onerror=null;this.className='lobby-avatar-mystery';this.innerText='?';" alt="${champName}">`;
                } else {
                    champName = 'Gizli / Henüz Seçilmedi';
                    avatarHtml = `<div class="lobby-avatar-mystery">?</div>`;
                }
            }

            // Role / Position
            const posRaw = (member.assignedPosition || member.assigned_position || '').toLowerCase();
            const posShort = POSITION_SHORT_NAMES[posRaw] || (posRaw ? posRaw.substring(0, 3).toUpperCase() : '');
            const roleBadgeOnAvatar = posShort ? `<span class="lobby-role-pill-on-avatar">${posShort}</span>` : '';

            // 'Sen' Badge for local player
            const meBadgeHtml = isMe ? `<span class="lobby-tag-me">SEN</span>` : '';

            // Actions for teammates (Role swap / Champ trade)
            let actionHtml = '';
            if (isMyTeam && !isMe) {
                actionHtml = `
                    <div class="lobby-card-actions">
                        <button class="lobby-btn-swap" onclick="requestLaneSwap(${cellId})" title="Koridor Takası Teklif Et">Rol Takası</button>
                        <button class="lobby-btn-trade" onclick="requestChampTrade(${cellId})" title="Şampiyon Takası Teklif Et">Takas</button>
                    </div>
                `;
            } else if (isMe) {
                actionHtml = `
                    <div class="lobby-card-status-badge">
                        <span class="lobby-status-pill">KİLİTLENDİ ✓</span>
                    </div>
                `;
            }

            const champClass = (!isMyTeam && champId <= 0) ? 'lobby-champ-title hidden-enemy' : 'lobby-champ-title';

            card.innerHTML = `
                <div class="lobby-avatar-container">
                    ${avatarHtml}
                    ${roleBadgeOnAvatar}
                </div>
                <div class="lobby-player-info">
                    <div class="lobby-player-name-row">
                        ${meBadgeHtml}
                        <span class="lobby-player-name" title="${displayName}">${displayName}</span>
                    </div>
                    <div class="${champClass}">${champName}</div>
                </div>
                ${actionHtml}
            `;

            return card;
        }

        function requestLaneSwap(cellId) {
            if (!sessionToken || cellId === undefined || cellId < 0) return;
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    type: 'LANE_SWAP_REQUEST',
                    toCellId: cellId,
                    token: sessionToken,
                }));
                showErrorToast(`Koridor takası teklifi gönderildi.`);
            }
        }

        function requestChampTrade(cellId) {
            if (!sessionToken || cellId === undefined || cellId < 0) return;
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    type: 'TRADE_REQUEST',
                    tradeId: cellId,
                    token: sessionToken,
                }));
                showErrorToast(`Şampiyon takası teklifi gönderildi.`);
            }
        }

        function renderChampSelect(data, modeCtx, eligibility) {
            if (!data) return;
            currentChampSelect = data;
            if (modeCtx) currentModeContext = modeCtx;
            if (eligibility) currentEligibility = eligibility;

            // Auto-transition to Post-Lock Lobby if local pick action is completed
            const isLocked = Boolean(data.isLocalPickCompleted || data.is_local_pick_completed);
            if (isLocked) {
                if (champSelectView) champSelectView.style.display = 'none';
                if (postLockLobbyView) postLockLobbyView.style.display = 'flex';
                renderPostLockLobby(data);
            } else {
                if (champSelectView) champSelectView.style.display = 'flex';
                if (postLockLobbyView) postLockLobbyView.style.display = 'none';
            }

            // Cache local player champion & role for in-game view
            if (data && Array.isArray(data.myTeam)) {
                const localId = data.localPlayerCellId !== undefined ? data.localPlayerCellId : data.local_player_cell_id;
                const me = data.myTeam.find(p => (p.cellId !== undefined ? p.cellId : p.cell_id) === localId);
                if (me) {
                    const cid = me.championId || me.champion_id || 0;
                    const pos = me.assignedPosition || me.assigned_position || '';
                    let cname = '';
                    let cicon = '';
                    if (cid > 0) {
                        const found = gridChampions.find(c => c.id === cid);
                        cname = found ? found.name : findChampionName(cid);
                        cicon = (found && found.squarePortraitPath) || `https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/champion-icons/${cid}.png`;
                    }
                    if (cname || pos) {
                        cachedPlayerChamp = { name: cname, role: pos, icon: cicon };
                    }
                }
            }

            const effMode = currentModeContext || data.modeContext || {};
            const effElig = currentEligibility || data.eligibility || {};

            // 0. Update Game Mode Banner & Layout Visibility
            gameModeName.innerText = effMode.displayName || effMode.gameMode || 'SİHİRDAR VADİSİ';
            gameModeStage.innerText = STAGE_LABELS[effElig.stage] || effElig.stage || '';

            const layout = effElig.layout || 'DRAFT';
            if (layout === 'SAFE_GENERIC') {
                safeModeBox.style.display = 'flex';
                aramBenchBox.style.display = 'none';
                champSearchBox.style.display = 'none';
                champGridContainer.style.display = 'none';
                csActionPanel.style.display = 'none';
            } else if (layout === 'ARAM') {
                safeModeBox.style.display = 'none';
                aramBenchBox.style.display = 'flex';
                champSearchBox.style.display = 'none';
                champGridContainer.style.display = 'none';
                csActionPanel.style.display = 'none';
                const benchIds = data.benchChampionIds || [];
                aramBenchInfo.innerText = `Yeniden deneme (Zar) hakkı: ${data.rerollsRemaining || 0} | Yedek Kulübesi: ${benchIds.length} şampiyon`;
                aramBenchList.innerHTML = '';
                benchIds.forEach(cid => {
                    const tag = document.createElement('span');
                    tag.className = 'cs-badge pos';
                    tag.innerText = findChampionName(cid);
                    aramBenchList.appendChild(tag);
                });
            } else {
                safeModeBox.style.display = 'none';
                aramBenchBox.style.display = 'none';
                champSearchBox.style.display = 'flex';
                champGridContainer.style.display = 'block';
                csActionPanel.style.display = 'flex';
            }

            // 1. Update Timer
            if (data.timer) {
                const totalSeconds = Number(data.timer.totalTimeInPhase || data.timer.total_time_in_phase || 30000) / 1000;
                const secondsLeft = Number(
                    data.timer.timeLeftSeconds !== undefined
                    ? data.timer.timeLeftSeconds
                    : (data.timer.adjustedTimeLeftInPhase || data.timer.timeLeftInPhase || 30000) / 1000
                );

                timerTotalSeconds = Math.max(1, Math.round(totalSeconds));
                timerSecondsRemaining = Math.max(0, Math.floor(secondsLeft));
                csTimerBadge.innerText = timerSecondsRemaining + 's';

                const pct = Math.max(0, Math.min(100, (timerSecondsRemaining / timerTotalSeconds) * 100));
                csProgressBar.style.width = pct + '%';

                clearInterval(timerCountdownInterval);
                timerCountdownInterval = setInterval(() => {
                    if (timerSecondsRemaining > 0) {
                        timerSecondsRemaining--;
                        csTimerBadge.innerText = timerSecondsRemaining + 's';
                        const curPct = Math.max(0, Math.min(100, (timerSecondsRemaining / timerTotalSeconds) * 100));
                        csProgressBar.style.width = curPct + '%';
                        if (timerSecondsRemaining <= 5) {
                            csProgressBar.style.backgroundColor = 'var(--danger)';
                            csTimerBadge.style.color = 'var(--danger)';
                        } else {
                            csProgressBar.style.backgroundColor = 'var(--gold-2)';
                            csTimerBadge.style.color = 'var(--gold-1)';
                        }
                    }
                }, 1000);
            }

            // 2. Update Local Player Position & Spells/Runes
            const localPlayer = data.localPlayer || data.local_player;
            let assignedPos = '';
            if (localPlayer) {
                assignedPos = (localPlayer.assignedPosition || localPlayer.assigned_position || '').toLowerCase();
                csPositionBadge.innerText = POSITION_NAMES[assignedPos] || 'SERBEST';

                const s1 = localPlayer.spell1Id || localPlayer.spell1_id;
                const s2 = localPlayer.spell2Id || localPlayer.spell2_id;
                if (s1 && s2) {
                    updateSpellsUI(s1, s2);
                }

                const activeChamp = localPlayer.championId || localPlayer.champion_id || localPlayer.championPickIntent || localPlayer.champion_pick_intent;
                if (activeChamp > 0 && activeChamp !== lastFetchedRunesChampId) {
                    fetchRecommendedRunes(activeChamp, assignedPos, effMode.mapId || 11);
                }
            }

            // 3. Reconcile selectedChampId if it became banned in client state
            const bannedSet = new Set(data.allBannedIds || []);
            if (selectedChampId > 0 && bannedSet.has(selectedChampId)) {
                selectedChampId = 0;
                selectedChampName = '';
                csPreviewName.innerText = 'Şampiyon Yasaklandı — Yeniden Seçin';
                csPreviewImg.style.display = 'none';
            }

            // 4. Derive Ban & Lock button states strictly from server eligibility + local selection
            updateActionButtonsFromState();

            // 5. Render Team Rosters & Champion Grid
            renderTeamRoster(myTeamList, data.myTeam || [], data.localPlayerCellId ?? data.local_player_cell_id);
            renderTeamRoster(theirTeamList, data.theirTeam || [], -1);
            renderChampGrid();
        }

        function updateActionButtonsFromState() {
            const effElig = currentEligibility || (currentChampSelect && currentChampSelect.eligibility) || {};
            const bannedSet = new Set(currentChampSelect ? (currentChampSelect.allBannedIds || []) : []);
            const unavailableSet = new Set(currentChampSelect ? (currentChampSelect.unavailablePickIds || []) : []);

            const activeAction = currentChampSelect ? (currentChampSelect.activeAction || currentChampSelect.active_action) : null;
            const candidateChamp = selectedChampId > 0 ? selectedChampId : (activeAction ? (activeAction.championId || 0) : 0);
            const validUnbannedChamp = candidateChamp > 0 && !bannedSet.has(candidateChamp);

            // Mode badge
            if (effElig.stage === 'BAN_MY_TURN' || effElig.stage === 'BAN_WAITING') {
                csModeBadge.innerText = 'YASAKLAMA';
                csModeBadge.className = 'cs-badge ban';
            } else {
                csModeBadge.innerText = 'SEÇİM';
                csModeBadge.className = 'cs-badge pick';
            }

            // Ban button: completely hidden if no ban phase in mode
            if (effElig.banPhaseExists && effElig.banButtonVisible) {
                csBanBtn.style.display = 'inline-block';
                const canBanNow = (
                    effElig.stage === 'BAN_MY_TURN'
                    && effElig.isMyTurn
                    && validUnbannedChamp
                    && !isSubmittingAction
                );
                csBanBtn.disabled = !canBanNow;
                csBanBtn.innerText = isSubmittingAction ? '...' : 'YASAKLA';
            } else {
                csBanBtn.style.display = 'none';
                csBanBtn.disabled = true;
            }

            // Lock button: enabled only on PICK_MY_TURN with valid unbanned & unpicked champion
            if (effElig.lockButtonVisible !== false) {
                csLockBtn.style.display = 'inline-block';
                const canLockNow = (
                    effElig.stage === 'PICK_MY_TURN'
                    && effElig.isMyTurn
                    && validUnbannedChamp
                    && !unavailableSet.has(candidateChamp)
                    && !isSubmittingAction
                );
                csLockBtn.disabled = !canLockNow;
                if (effElig.stage === 'PICK_LOCKED') {
                    csLockBtn.innerText = 'KİLİTLENDİ ✓';
                } else {
                    csLockBtn.innerText = isSubmittingAction ? 'GÖNDERİLİYOR...' : 'KİLİTLE';
                }
            } else {
                csLockBtn.style.display = 'none';
                csLockBtn.disabled = true;
            }

            // Status reason helper text
            if (effElig.stage === 'BAN_MY_TURN' || effElig.stage === 'BAN_WAITING') {
                csActionReason.innerText = effElig.banDisabledReason || '';
            } else {
                csActionReason.innerText = effElig.lockDisabledReason || '';
            }
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
            const unavailableSet = new Set(currentChampSelect ? (currentChampSelect.unavailablePickIds || []) : []);

            const filtered = gridChampions.filter(c => {
                if (selectedRoleFilter && Array.isArray(c.roles) && !c.roles.includes(selectedRoleFilter)) {
                    return false;
                }
                if (!query) return true;
                return (c.name || '').toLowerCase().includes(query);
            });

            if (filtered.length === 0) {
                const emptyNotice = document.createElement('div');
                emptyNotice.style.cssText = 'grid-column: 1 / -1; padding: 24px 12px; text-align: center; color: var(--text-muted); font-size: 13px;';
                emptyNotice.innerText = query ? `"${query}" ile eşleşen şampiyon bulunamadı.` : 'Karakter havuzunda şampiyon bulunamadı.';
                champGrid.appendChild(emptyNotice);
                return;
            }

            filtered.forEach(c => {
                const item = document.createElement('div');
                const isBanned = bannedSet.has(c.id);
                const isDisabledInMode = Boolean(c.disabled);
                const isTaken = unavailableSet.has(c.id);
                const isUnselectable = isBanned || isDisabledInMode || isTaken;
                const isSelected = (selectedChampId === c.id) && !isUnselectable;

                item.className = 'champ-item'
                    + (isSelected ? ' selected' : '')
                    + (isBanned ? ' banned' : '')
                    + ((!isBanned && isUnselectable) ? ' unavailable' : '');
                item.style.touchAction = 'manipulation';

                const badgeHtml = isBanned
                    ? '<span class="champ-ban-badge">YASAKLI</span>'
                    : (isTaken ? '<span class="champ-ban-badge" style="background:#555;">SEÇİLDİ</span>' : '');

                const iconUrl = getChampionIconUrl(c.id);
                item.innerHTML = `
                    ${badgeHtml}
                    <img class="champ-img" src="${iconUrl}" onerror="this.style.opacity='0.2'" alt="${c.name}">
                    <span class="champ-name">${c.name}</span>
                `;

                if (!isUnselectable) {
                    const doSelect = (e) => {
                        if (e) {
                            e.preventDefault();
                            e.stopPropagation();
                        }
                        selectChampion(c.id, c.name);
                    };
                    item.addEventListener('click', doSelect);
                    item.addEventListener('pointerdown', doSelect);
                }

                champGrid.appendChild(item);
            });
        }

        function selectChampion(id, name) {
            const bannedSet = new Set(currentChampSelect ? (currentChampSelect.allBannedIds || []) : []);
            if (bannedSet.has(id)) {
                showErrorToast('Yasaklanan bir şampiyon seçilemez.');
                return;
            }

            selectedChampId = id;
            selectedChampName = name;

            csPreviewName.innerText = name;
            csPreviewImg.src = getChampionIconUrl(id);
            csPreviewImg.style.display = 'block';

            updateActionButtonsFromState();

            const effElig = currentEligibility || (currentChampSelect && currentChampSelect.eligibility) || {};
            const act = (currentChampSelect && (currentChampSelect.activeAction || currentChampSelect.active_action));
            const actionId = effElig.activeActionId || (act ? act.id : 0);

            if (socket && socket.readyState === WebSocket.OPEN && effElig.hoverAllowed !== false) {
                socket.send(JSON.stringify({
                    type: 'CHAMP_SELECT_ACTION',
                    token: sessionToken,
                    actionId: actionId,
                    championId: id,
                    completed: false
                }));
            }

            // Fetch official recommended rune pages when hovering/selecting for pick
            if (!act || (act.type || '').toLowerCase() !== 'ban') {
                const localPlayer = currentChampSelect ? (currentChampSelect.localPlayer || currentChampSelect.local_player) : null;
                const pos = localPlayer ? (localPlayer.assignedPosition || localPlayer.assigned_position || '') : '';
                const mapId = (currentModeContext && currentModeContext.mapId) ? currentModeContext.mapId : 11;
                fetchRecommendedRunes(id, pos, mapId);
            }

            renderChampGrid();
        }

        function submitCompleteAction(expectedType) {
            if (isSubmittingAction || selectedChampId <= 0) return;
            const effElig = currentEligibility || (currentChampSelect && currentChampSelect.eligibility) || {};
            const act = (currentChampSelect && (currentChampSelect.activeAction || currentChampSelect.active_action));
            const actionId = effElig.activeActionId || (act ? act.id : 0);

            if (socket && socket.readyState === WebSocket.OPEN) {
                isSubmittingAction = true;
                updateActionButtonsFromState();
                socket.send(JSON.stringify({
                    type: 'CHAMP_SELECT_ACTION',
                    token: sessionToken,
                    actionId: actionId,
                    championId: selectedChampId,
                    actionType: expectedType,
                    completed: true
                }));
            }
        }

        champSearchInput.addEventListener('input', () => {
            renderChampGrid();
        });

        csLockBtn.addEventListener('click', () => {
            if (!csLockBtn.disabled) {
                submitCompleteAction('pick');
            }
        });

        // Lane Selection & Mode Logic
        function updateLaneUI() {
            const isDraft = DRAFT_QUEUE_IDS.includes(selectedQueueId);
            if (laneSelectorSection) {
                laneSelectorSection.style.display = isDraft ? 'flex' : 'none';
            }
            if (laneDisabledNotice) {
                laneDisabledNotice.style.display = isDraft ? 'none' : 'flex';
            }
            if (laneChips) {
                laneChips.forEach(chip => {
                    const lane = chip.dataset.lane;
                    const badge = chip.querySelector('.lane-chip-badge');
                    chip.classList.remove('primary', 'secondary');
                    if (badge) badge.innerText = '';
                    if (isDraft) {
                        if (lane === selectedFirstLane) {
                            chip.classList.add('primary');
                            if (badge) badge.innerText = '1.';
                        } else if (lane === selectedSecondLane) {
                            chip.classList.add('secondary');
                            if (badge) badge.innerText = '2.';
                        }
                    }
                });
            }
        }

        function syncPreferencesToServer(queueId, first, second) {
            const p1 = first || 'UNSELECTED';
            const p2 = second || 'UNSELECTED';
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    type: 'SET_POSITION_PREFERENCES',
                    token: sessionToken,
                    queueId: queueId,
                    first: p1,
                    second: p2,
                    firstPreference: p1,
                    secondPreference: p2
                }));
            } else {
                const headers = { 'Content-Type': 'application/json' };
                if (sessionToken) {
                    headers['X-Session-Token'] = sessionToken;
                    headers['Authorization'] = 'Bearer ' + sessionToken;
                }
                fetch('/api/lobby/preferences', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify({
                        queueId: queueId,
                        first: p1,
                        second: p2,
                        firstPreference: p1,
                        secondPreference: p2
                    })
                }).catch(() => {});
            }
        }

        function onLaneChipClick(lane) {
            if (!DRAFT_QUEUE_IDS.includes(selectedQueueId)) return;
            if (selectedFirstLane === lane) {
                if (selectedSecondLane && selectedSecondLane !== 'UNSELECTED') {
                    selectedFirstLane = selectedSecondLane;
                    selectedSecondLane = 'UNSELECTED';
                } else {
                    selectedFirstLane = 'FILL';
                    selectedSecondLane = 'UNSELECTED';
                }
            } else if (selectedSecondLane === lane) {
                selectedSecondLane = 'UNSELECTED';
            } else {
                if (lane === 'FILL') {
                    selectedFirstLane = 'FILL';
                    selectedSecondLane = 'UNSELECTED';
                } else if (!selectedFirstLane || selectedFirstLane === 'FILL' || selectedFirstLane === 'UNSELECTED') {
                    selectedFirstLane = lane;
                } else if (!selectedSecondLane || selectedSecondLane === 'UNSELECTED') {
                    selectedSecondLane = lane;
                } else {
                    selectedSecondLane = lane;
                }
            }
            updateLaneUI();
            syncPreferencesToServer(selectedQueueId, selectedFirstLane, selectedSecondLane);
        }

        if (laneChips) {
            laneChips.forEach(chip => {
                chip.addEventListener('click', () => {
                    const lane = chip.dataset.lane;
                    if (lane) onLaneChipClick(lane);
                });
            });
        }

        // Mode chips click
        if (modeChips) {
            modeChips.forEach(chip => {
                chip.addEventListener('click', () => {
                    modeChips.forEach(c => c.classList.remove('active'));
                    chip.classList.add('active');
                    selectedQueueId = parseInt(chip.dataset.queueId || '420', 10);
                    if (DRAFT_QUEUE_IDS.includes(selectedQueueId)) {
                        if (!selectedFirstLane || selectedFirstLane === 'UNSELECTED') {
                            selectedFirstLane = 'FILL';
                            selectedSecondLane = 'UNSELECTED';
                        }
                    } else {
                        // Non-draft modes (430 Kapalı Seçim, 450 ARAM): automatically reset lane preferences
                        selectedFirstLane = 'UNSELECTED';
                        selectedSecondLane = 'UNSELECTED';
                    }
                    updateLaneUI();
                    syncPreferencesToServer(selectedQueueId, selectedFirstLane, selectedSecondLane);
                });
            });
        }

        // Start Queue button with debounce guard, headers, and error handling
        if (startQueueBtn) {
            startQueueBtn.addEventListener('click', () => {
                if (isQueueActionInFlight) return;
                isQueueActionInFlight = true;
                startQueueBtn.disabled = true;
                startQueueBtn.innerText = 'BAŞLATILIYOR...';

                const resetBtn = () => {
                    isQueueActionInFlight = false;
                    if (startQueueBtn) {
                        startQueueBtn.disabled = false;
                        startQueueBtn.innerText = 'MAÇ ARA';
                    }
                };

                if (socket && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        type: 'START_MATCHMAKING',
                        token: sessionToken,
                        queueId: selectedQueueId,
                        firstPreference: selectedFirstLane || 'FILL',
                        secondPreference: selectedSecondLane || 'UNSELECTED',
                        first: selectedFirstLane || 'FILL',
                        second: selectedSecondLane || 'UNSELECTED'
                    }));
                    setTimeout(resetBtn, 2000);
                } else {
                    const headers = { 'Content-Type': 'application/json' };
                    if (sessionToken) {
                        headers['X-Session-Token'] = sessionToken;
                        headers['Authorization'] = 'Bearer ' + sessionToken;
                    }
                    fetch('/api/lobby/matchmaking/start', {
                        method: 'POST',
                        headers: headers,
                        body: JSON.stringify({
                            queueId: selectedQueueId,
                            firstPreference: selectedFirstLane || 'FILL',
                            secondPreference: selectedSecondLane || 'UNSELECTED',
                            first: selectedFirstLane || 'FILL',
                            second: selectedSecondLane || 'UNSELECTED'
                        })
                    })
                    .then(r => r.json().then(data => ({ ok: r.ok, status: r.status, data })))
                    .then(({ ok, data }) => {
                        resetBtn();
                        if (ok && data.success) {
                            startQueueTimer((data.search && data.search.time_in_queue) || 0);
                        } else {
                            stopQueueTimer();
                            const msg = (data && (data.detail || data.error)) || 'Maç araması başlatılamadı.';
                            showErrorToast(typeof msg === 'string' ? msg : JSON.stringify(msg));
                        }
                    })
                    .catch(err => {
                        resetBtn();
                        stopQueueTimer();
                        showErrorToast('Sunucuya bağlanılamadı: ' + err.message);
                    });
                }
            });
        }

        // Cancel Queue button
        if (cancelQueueBtn) {
            cancelQueueBtn.addEventListener('click', () => {
                stopQueueTimer();
                if (socket && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        type: 'CANCEL_MATCHMAKING',
                        token: sessionToken
                    }));
                } else {
                    fetch('/api/lobby/matchmaking/cancel', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    }).catch(() => {});
                }
            });
        }

        // Trade & Lane Swap Actions (with debounce guard)
        // Lobby trade notice banner action synchronization
        if (lobbyTradeAcceptBtn && tradeAcceptBtn) {
            lobbyTradeAcceptBtn.addEventListener('click', () => { tradeAcceptBtn.click(); });
        }
        if (lobbyTradeDeclineBtn && tradeDeclineBtn) {
            lobbyTradeDeclineBtn.addEventListener('click', () => { tradeDeclineBtn.click(); });
        }
        if (lobbyTradeCancelBtn && tradeCancelBtn) {
            lobbyTradeCancelBtn.addEventListener('click', () => { tradeCancelBtn.click(); });
        }

        if (tradeAcceptBtn) {
            tradeAcceptBtn.addEventListener('click', () => {
                if (isTradeActionInFlight) return;
                isTradeActionInFlight = true;
                const isLane = (tradeNoticeBanner && tradeNoticeBanner.dataset.type === 'lane');
                if (isLane && activeSwapCellId > 0) {
                    if (socket && socket.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({
                            type: 'LANE_SWAP_ACCEPT',
                            token: sessionToken,
                            fromCellId: activeSwapCellId
                        }));
                    }
                } else if (activeTradeId > 0) {
                    if (socket && socket.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({
                            type: 'TRADE_ACCEPT',
                            token: sessionToken,
                            tradeId: activeTradeId
                        }));
                    }
                }
                if (tradeNoticeBanner) tradeNoticeBanner.style.display = 'none';
                setTimeout(() => { isTradeActionInFlight = false; }, 800);
            });
        }

        if (tradeDeclineBtn) {
            tradeDeclineBtn.addEventListener('click', () => {
                if (isTradeActionInFlight) return;
                isTradeActionInFlight = true;
                const isLane = (tradeNoticeBanner && tradeNoticeBanner.dataset.type === 'lane');
                if (isLane && activeSwapCellId > 0) {
                    if (socket && socket.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({
                            type: 'LANE_SWAP_DECLINE',
                            token: sessionToken,
                            fromCellId: activeSwapCellId
                        }));
                    }
                } else if (activeTradeId > 0) {
                    if (socket && socket.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({
                            type: 'TRADE_DECLINE',
                            token: sessionToken,
                            tradeId: activeTradeId
                        }));
                    }
                }
                if (tradeNoticeBanner) tradeNoticeBanner.style.display = 'none';
                setTimeout(() => { isTradeActionInFlight = false; }, 800);
            });
        }

        if (tradeCancelBtn) {
            tradeCancelBtn.addEventListener('click', () => {
                if (isTradeActionInFlight) return;
                isTradeActionInFlight = true;
                if (activeTradeId > 0 && socket && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        type: 'TRADE_CANCEL',
                        token: sessionToken,
                        tradeId: activeTradeId
                    }));
                }
                if (tradeNoticeBanner) tradeNoticeBanner.style.display = 'none';
                setTimeout(() => { isTradeActionInFlight = false; }, 800);
            });
        }

        // Teammate quick swap click delegation
        const myTeamListEl = document.getElementById('myTeamList');
        if (myTeamListEl) {
            myTeamListEl.addEventListener('click', (e) => {
                const btn = e.target.closest('.btn-member-swap');
                if (!btn || isTradeActionInFlight) return;
                isTradeActionInFlight = true;
                const cell = parseInt(btn.dataset.cell || '0', 10);
                const action = btn.dataset.action;
                if (action === 'trade-lane' && cell >= 0) {
                    if (socket && socket.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({
                            type: 'LANE_SWAP_REQUEST',
                            token: sessionToken,
                            toCellId: cell
                        }));
                    }
                } else if (action === 'trade-champ' && cell >= 0) {
                    if (socket && socket.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({
                            type: 'TRADE_REQUEST',
                            token: sessionToken,
                            tradeId: cell
                        }));
                    }
                }
                btn.style.opacity = '0.5';
                setTimeout(() => {
                    btn.style.opacity = '1';
                    isTradeActionInFlight = false;
                }, 1000);
            });
        }

        csBanBtn.addEventListener('click', () => {
            if (!csBanBtn.disabled) {
                submitCompleteAction('ban');
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
                    } else if (data.type === 'POSITION_PREFERENCES_RESULT') {
                        if (data.success) {
                            if (data.first) selectedFirstLane = data.first;
                            if (data.second) selectedSecondLane = data.second;
                            if (data.queueId) selectedQueueId = data.queueId;
                            updateLaneUI();
                        }
                    } else if (data.type === 'CHAMP_SELECT_UPDATE') {
                        renderChampSelect(data.data, data.modeContext, data.eligibility);
                    } else if (data.type === 'GRID_CHAMPIONS') {
                        gridChampions = data.data || [];
                        renderChampGrid();
                    } else if (data.type === 'READY_CHECK_ACCEPTED') {
                        if (data.success) {
                            if (readyAcceptBtn) {
                                readyAcceptBtn.innerText = '✓ KABUL EDİLDİ';
                                readyAcceptBtn.classList.add('accepted');
                            }
                            if (readyCheckStatus) {
                                readyCheckStatus.innerText = 'Maç kabul edildi! Diğer oyuncular bekleniyor...';
                            }
                            if (infoMessage) {
                                infoMessage.innerText = 'Maç kabul edildi! Şampiyon seçimi bekleniyor.';
                            }
                        } else {
                            if (readyAcceptBtn) {
                                readyAcceptBtn.disabled = false;
                                readyAcceptBtn.innerHTML = '<span>⚔️</span> KABUL ET';
                            }
                            if (readyDeclineBtn) {
                                readyDeclineBtn.disabled = false;
                            }
                            isReadyCheckActionInFlight = false;
                            if (readyCheckStatus) {
                                readyCheckStatus.innerText = 'Kabul başarısız oldu.';
                            }
                        }
                    } else if (data.type === 'READY_CHECK_DECLINED') {
                        if (readyDeclineBtn) {
                            readyDeclineBtn.innerText = 'REDDEDİLDİ';
                        }
                        if (readyCheckStatus) {
                            readyCheckStatus.innerText = 'Maç reddedildi.';
                        }
                        setTimeout(() => {
                            closeReadyCheckModal();
                        }, 500);
                    } else if (data.type === 'SPELLS_CATALOG') {
                        if (Array.isArray(data.spells)) {
                            spellsCatalog = data.spells;
                            updateSpellsUI(currentSpell1Id, currentSpell2Id);
                        }
                    } else if (data.type === 'RECOMMENDED_RUNES') {
                        renderRecommendedRunes(data.pages || []);
                    } else if (data.type === 'RUNES_RESULT') {
                        if (data.success) {
                            applyRunesBtn.innerText = '✓ UYGULANDI';
                            applyRunesBtn.classList.add('applied');
                            applyRunesBtn.disabled = false;
                            setTimeout(() => {
                                applyRunesBtn.innerText = '⚡ SEÇİLİ RÜNÜ UYGULA';
                                applyRunesBtn.classList.remove('applied');
                            }, 2500);
                        } else {
                            applyRunesBtn.innerText = '⚡ SEÇİLİ RÜNÜ UYGULA';
                            applyRunesBtn.disabled = (selectedRunePageIndex < 0);
                            showErrorToast(data.error || 'Rün sayfası istemciye uygulanamadı.');
                        }
                    } else if (data.type === 'SPELLS_RESULT') {
                        if (data.success) {
                            updateSpellsUI(data.spell1Id, data.spell2Id);
                        } else {
                            showErrorToast(data.error || 'Seçilen sihirdar büyüsü bu oyun modunda kullanılamaz.');
                        }
                    } else if (data.type === 'ACTION_RESULT') {
                        isSubmittingAction = false;
                        if (data.success && data.completed) {
                            triggerVibrate();
                        } else if (!data.success && data.error) {
                            showErrorToast(data.error);
                        }
                        updateActionButtonsFromState();
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

        // Auto re-sync on tab / lock screen wake & focus
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                refreshActiveTimers();
                if (!socket || socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING) {
                    if (localStorage.getItem('riftpilot_session_token')) {
                        connectWebSocket();
                    }
                } else if (socket.readyState === WebSocket.OPEN) {
                    try {
                        socket.send(JSON.stringify({ type: 'GET_STATE', token: sessionToken }));
                        if (champSelectView && champSelectView.style.display !== 'none') {
                            socket.send(JSON.stringify({ type: 'GET_CHAMP_SELECT', token: sessionToken }));
                        }
                    } catch (e) {}
                }
            }
        });

        window.addEventListener('focus', () => {
            refreshActiveTimers();
        });

        // Ready Check Modal Button Listeners
        if (readyAcceptBtn) {
            readyAcceptBtn.addEventListener('click', async () => {
                if (isReadyCheckActionInFlight) return;
                isReadyCheckActionInFlight = true;
                readyAcceptBtn.disabled = true;
                readyDeclineBtn.disabled = true;
                readyAcceptBtn.innerText = 'İşleniyor...';
                readyDeclineBtn.innerText = 'İşleniyor...';
                if (readyCheckStatus) readyCheckStatus.innerText = 'Kabul ediliyor...';

                let wsSent = false;
                if (socket && socket.readyState === WebSocket.OPEN) {
                    try {
                        socket.send(JSON.stringify({
                            type: 'ACCEPT_MATCH',
                            token: sessionToken
                        }));
                        wsSent = true;
                    } catch (e) {}
                }
                if (!wsSent) {
                    try {
                        const resp = await fetch('/api/match/accept', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ token: sessionToken })
                        });
                        const data = await resp.json();
                        if (data && data.success) {
                            readyAcceptBtn.innerText = '✓ KABUL EDİLDİ';
                            readyAcceptBtn.classList.add('accepted');
                            if (readyCheckStatus) readyCheckStatus.innerText = 'Kabul edildi!';
                        }
                    } catch (e) {
                        if (readyCheckStatus) readyCheckStatus.innerText = 'İstek gönderilemedi.';
                    }
                }
            });
        }

        if (readyDeclineBtn) {
            readyDeclineBtn.addEventListener('click', async () => {
                if (isReadyCheckActionInFlight) return;
                isReadyCheckActionInFlight = true;
                readyAcceptBtn.disabled = true;
                readyDeclineBtn.disabled = true;
                readyAcceptBtn.innerText = 'İşleniyor...';
                readyDeclineBtn.innerText = 'İşleniyor...';
                if (readyCheckStatus) readyCheckStatus.innerText = 'Reddediliyor...';

                let wsSent = false;
                if (socket && socket.readyState === WebSocket.OPEN) {
                    try {
                        socket.send(JSON.stringify({
                            type: 'DECLINE_MATCH',
                            token: sessionToken
                        }));
                        wsSent = true;
                    } catch (e) {}
                }
                if (!wsSent) {
                    try {
                        await fetch('/api/match/decline', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ token: sessionToken })
                        });
                    } catch (e) {}
                }
                setTimeout(() => {
                    closeReadyCheckModal();
                }, 500);
            });
        }

        function fetchInitialLcuStatus() {
            fetch('/api/lcu/status')
                .then(r => r.json())
                .then(data => {
                    if (data && data.connected !== undefined) {
                        updateLcuStatus(data.connected);
                        if (data.phase && data.phase !== 'None') {
                            updateGameflow(data.phase, data.search);
                        }
                    }
                })
                .catch(() => {});
        }
        fetchInitialLcuStatus();

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


def create_app(
    ott_manager: OTTManager,
    session_manager: SessionManager,
    lcu_adapter: ILcuPort | None = None,
    tunnel_adapter: ITunnelPort | None = None,
    ui_bridge: Any = None,
    enable_monitor: bool = True,
    poll_interval: float = 1.0,
) -> FastAPI:
    """Factory creating and configuring the FastAPI Web Hub application."""
    active_connections: Set[WebSocket] = set()
    in_flight_action_lock = asyncio.Lock()

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

        dead_connections: list[WebSocket] = []

        for ws in list(active_connections):
            try:
                await ws.send_json(payload)
            except Exception:
                dead_connections.append(ws)

        for ws in dead_connections:
            active_connections.discard(ws)

    async def broadcast_champ_select(session: ChampSelectSession | None) -> None:
        """Broadcast live centralized champ select state to all active WebSocket clients."""
        enriched = await _build_enriched_champ_select_payload(session)
        payload: dict[str, Any] = {
            "type": "CHAMP_SELECT_UPDATE",
            "data": enriched["session"],
            "modeContext": enriched["modeContext"],
            "eligibility": enriched["eligibility"],
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
                        dead_ws = []
                        for ws in list(active_connections):
                            try:
                                await ws.send_json(status_payload)
                            except Exception:
                                dead_ws.append(ws)
                        for ws in dead_ws:
                            active_connections.discard(ws)

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
                                    dead_ws = []
                                    for ws in list(active_connections):
                                        try:
                                            await ws.send_json(search_payload)
                                        except Exception:
                                            dead_ws.append(ws)
                                    for ws in dead_ws:
                                        active_connections.discard(ws)
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
    )

    # Store references on app.state
    app.state.ott_manager = ott_manager
    app.state.session_manager = session_manager
    app.state.lcu_adapter = lcu_adapter
    app.state.ui_bridge = ui_bridge
    app.state.active_connections = active_connections
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

    @app.get("/api/lcu/status")
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

    @app.get("/api/champ-select")
    async def get_champ_select_state() -> dict[str, Any]:
        """Fetch current centralized champion select session, modeContext, and eligibility."""
        if lcu_adapter is None:
            return {"session": None, "modeContext": None, "eligibility": None}
        session = await lcu_adapter.get_champ_select_session()
        return await _build_enriched_champ_select_payload(session)

    @app.get("/api/champ-select/grid")
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

    @app.post("/api/champ-select/action")
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

    @app.get("/api/spells")
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

    @app.post("/api/spells")
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

    @app.get("/api/runes/recommended")
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

    @app.get("/api/runes/current")
    async def get_current_runes_endpoint() -> dict[str, Any]:
        """Fetch current active rune page from LCU."""
        if lcu_adapter is None:
            return {"page": None}
        page = await lcu_adapter.get_current_rune_page()
        return {"page": page.to_dict() if page is not None else None}

    @app.post("/api/runes/apply")
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

    @app.get("/api/runes/trees")
    async def get_rune_trees_endpoint() -> dict[str, Any]:
        """Return the complete League of Legends rune trees catalog and stat shards."""
        trees_list = list(RUNE_DATA["trees"].values())
        stat_shards = list(RUNE_DATA["stat_shards"])
        return {
            "trees": trees_list,
            "statShards": stat_shards,
            "stat_shards": stat_shards,
        }

    @app.get("/api/runes/custom")
    async def get_custom_runes_endpoint() -> dict[str, Any]:
        """Return all user-saved custom rune pages."""
        mgr = get_custom_rune_manager()
        pages = [p.to_dict() for p in mgr.get_all_pages()]
        return {
            "pages": pages,
            "count": len(pages),
        }

    @app.post("/api/runes/custom")
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

    @app.delete("/api/runes/custom/{page_id}")
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

    @app.get("/api/lobby")
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

    @app.post("/api/lobby/create")
    async def create_lobby_endpoint(payload: CreateLobbyRequest) -> dict[str, Any]:
        """Create or switch party lobby for a specific queue ID."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.create_lobby(payload.queueId)
        return {"success": bool(success), "queueId": payload.queueId}

    @app.post("/api/lobby/matchmaking/start")
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

    @app.post("/api/lobby/matchmaking/cancel")
    async def cancel_matchmaking_endpoint() -> dict[str, Any]:
        """Cancel ongoing matchmaking search."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.cancel_matchmaking()
        return {"success": success}

    @app.post("/api/match/accept")
    async def accept_match_endpoint(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Accept an active ready check (match found) prompt."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await _perform_accept_ready_check(lcu_adapter)
        return {"success": bool(success)}

    @app.post("/api/match/decline")
    async def decline_match_endpoint(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Decline an active ready check prompt."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await _perform_decline_ready_check(lcu_adapter)
        return {"success": bool(success)}



    @app.post("/api/lobby/preferences")
    @app.put("/api/lobby/preferences")
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

    @app.get("/api/champ-select/bench")
    async def get_bench_endpoint() -> dict[str, Any]:
        """Retrieve available champions on the ARAM bench."""
        if lcu_adapter is None:
            return {"bench": []}
        bench = await lcu_adapter.get_bench_champions()
        return {"bench": [b.model_dump() for b in bench]}

    @app.post("/api/champ-select/bench/swap")
    async def bench_swap_endpoint(payload: BenchSwapRequest) -> dict[str, Any]:
        """Swap champion with one on the bench."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.bench_swap(payload.championId)
        return {"success": success, "championId": payload.championId}

    @app.post("/api/champ-select/reroll")
    async def reroll_endpoint() -> dict[str, Any]:
        """Reroll champion in ARAM."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.reroll()
        return {"success": success}

    @app.get("/api/champ-select/trades")
    async def get_trades_endpoint() -> dict[str, Any]:
        """Retrieve current champion select trades."""
        if lcu_adapter is None:
            return {"trades": []}
        trades = await lcu_adapter.get_trades()
        return {"trades": [t.model_dump() for t in trades]}

    @app.post("/api/champ-select/trade/request")
    async def request_trade_endpoint(payload: TradeActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.request_trade(payload.tradeId)
        return {"success": bool(success), "tradeId": payload.tradeId}

    @app.post("/api/champ-select/trade/accept")
    async def accept_trade_endpoint(payload: TradeActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.accept_trade(payload.tradeId)
        return {"success": bool(success), "tradeId": payload.tradeId}

    @app.post("/api/champ-select/trade/decline")
    async def decline_trade_endpoint(payload: TradeActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.decline_trade(payload.tradeId)
        return {"success": bool(success), "tradeId": payload.tradeId}

    @app.post("/api/champ-select/trade/cancel")
    async def cancel_trade_endpoint(payload: TradeActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.cancel_trade(payload.tradeId)
        return {"success": bool(success), "tradeId": payload.tradeId}

    @app.get("/api/champ-select/lane-swaps")
    async def get_lane_swaps_endpoint() -> dict[str, Any]:
        if lcu_adapter is None:
            return {"laneSwaps": []}
        swaps_val = lcu_adapter.get_lane_swap_requests()
        swaps = await swaps_val if inspect.isawaitable(swaps_val) else swaps_val
        return {"laneSwaps": [s.model_dump() for s in (swaps or [])]}

    @app.post("/api/champ-select/lane-swap/request")
    async def request_lane_swap_endpoint(payload: LaneSwapActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.request_lane_swap(payload.cellId)
        return {"success": bool(success), "toCellId": payload.cellId}

    @app.post("/api/champ-select/lane-swap/accept")
    async def accept_lane_swap_endpoint(payload: LaneSwapActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.accept_lane_swap(payload.cellId)
        return {"success": bool(success), "fromCellId": payload.cellId}

    @app.post("/api/champ-select/lane-swap/decline")
    async def decline_lane_swap_endpoint(payload: LaneSwapActionRequest) -> dict[str, Any]:
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.decline_lane_swap(payload.cellId)
        return {"success": bool(success), "fromCellId": payload.cellId}


    # =========================================================================
    # Post-Game Stats & Honor Endpoints (Milestone 5)
    # =========================================================================

    @app.get("/api/game/eog")
    async def get_eog_endpoint() -> dict[str, Any]:
        """Retrieve end-of-game match summary and scoreboard."""
        if lcu_adapter is None:
            return {"stats": None}
        stats = await lcu_adapter.get_end_of_game_stats()
        return {"stats": stats.model_dump() if stats else None}

    @app.get("/api/game/honor/ballot")
    async def get_honor_ballot_endpoint() -> dict[str, Any]:
        """Retrieve eligible teammates for post-game honor voting."""
        if lcu_adapter is None:
            return {"ballot": None}
        ballot = await lcu_adapter.get_honor_ballot()
        return {"ballot": ballot.model_dump() if ballot else None}

    @app.post("/api/game/honor")
    async def honor_player_endpoint(payload: HonorPlayerRequest) -> dict[str, Any]:
        """Cast honor vote for a teammate."""
        if lcu_adapter is None:
            raise HTTPException(status_code=503, detail="LCU adapter unavailable")
        success = await lcu_adapter.honor_player(payload.summonerId, payload.honorCategory)
        return {"success": success, "summonerId": payload.summonerId, "category": payload.honorCategory}


    # =========================================================================
    # WAN Remote Relay Endpoints (Milestone 6 - ADR-0003)
    # =========================================================================

    @app.get("/api/wan/status")
    async def get_wan_status_endpoint() -> dict[str, Any]:
        """Retrieve status of the WAN reverse tunnel."""
        if tunnel_adapter is None:
            return {"tunnel": None, "available": False}
        info = await tunnel_adapter.get_tunnel_info()
        return {"tunnel": info.model_dump(), "available": True}

    @app.post("/api/wan/toggle")
    async def toggle_wan_endpoint(enable: bool = True, port: int = 8000) -> dict[str, Any]:
        """Start or stop the WAN reverse tunnel."""
        if tunnel_adapter is None:
            raise HTTPException(status_code=503, detail="WAN tunnel adapter not configured")
        if enable:
            info = await tunnel_adapter.start_tunnel(local_port=port)
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
            await websocket.close(code=4001)
            return

        active_connections.add(websocket)

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
            active_connections.discard(websocket)

    return app


__all__ = [
    "ApplyRunePageRequest",
    "CustomRuneCreateRequest",
    "ChampSelectActionRequest",
    "INDEX_HTML",
    "PairRequest",
    "SetSpellsRequest",
    "create_app",
]
