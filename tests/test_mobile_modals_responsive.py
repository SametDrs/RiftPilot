"""Tests for Mobile Modals, In-Game View, Responsive CSS, and Timer Stability.

Validates requirements:
1. Ready Check Modal Popup (`#readyCheckModal`):
   - Centered overlay modal displayed when `phase === 'ReadyCheck'`.
   - Title "⚔️ MAÇ BULUNDU!", countdown timer, glowing "Kabul Et" (`#readyAcceptBtn`),
     and clean "Reddet" (`#readyDeclineBtn`).
   - Permanent accept button removed from idle card.
2. In-Game Match View (`#inGameView`):
   - Shows when `phase === 'InProgress'` (or `'GameStart'`).
   - Title "⚔️ Maçtasın!", subtitle "Maç devam ediyor.", live MM:SS clock, champion/role info.
   - Smoothly resets to idle/lobby view when game ends.
3. Timer Stability:
   - Separate `queueStartTime` / `queueTimerInterval` and `inGameStartTime` / `inGameTimerInterval`.
   - Timestamp-based elapsed calculation: `Math.max(0, Math.floor((Date.now() - startTime) / 1000))`.
   - `visibilitychange` and `window.focus` listeners refresh clocks on tab foregrounding.
4. Endpoints & WebSocket commands:
   - `POST /api/match/decline` and `POST /api/match/accept`.
   - WebSocket commands `DECLINE_MATCH` and `ACCEPT_MATCH`.
5. Mobile Responsive CSS:
   - Viewport media queries for 320px, 375px, 390px, 430px.
   - `overflow-x: hidden` preventing horizontal scroll.
   - Minimum touch target 44px on all buttons.
"""

from __future__ import annotations

import re
from typing import Any, Generator
import pytest
from starlette.testclient import TestClient

from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.web_hub import INDEX_HTML, create_app
from riftpilot.core.lcu_port import GameflowPhase
from riftpilot.core.pairing import OTTManager, SessionManager


@pytest.fixture
def ott_manager() -> OTTManager:
    return OTTManager()


@pytest.fixture
def session_manager() -> SessionManager:
    return SessionManager()


@pytest.fixture
def mock_lcu() -> MockLcuAdapter:
    return MockLcuAdapter()


@pytest.fixture
def app_bundle(
    ott_manager: OTTManager,
    session_manager: SessionManager,
    mock_lcu: MockLcuAdapter,
) -> tuple[TestClient, MockLcuAdapter, str]:
    app = create_app(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lcu_adapter=mock_lcu,
        enable_monitor=False,
    )
    client = TestClient(app)
    ott = ott_manager.generate_ott()
    pair_resp = client.post("/api/pair", json={"ott": ott.token, "deviceName": "TestMobileUX"})
    assert pair_resp.status_code == 200
    token = pair_resp.json()["sessionToken"]
    return client, mock_lcu, token


# ============================================================================
# 1. Match HTTP Endpoints (Accept & Decline)
# ============================================================================

class TestMatchHttpEndpoints:
    """Tests for POST /api/match/accept and POST /api/match/decline."""

    @pytest.mark.asyncio
    async def test_post_match_decline_success(
        self,
        app_bundle: tuple[TestClient, MockLcuAdapter, str],
    ) -> None:
        """POST /api/match/decline declines the active ready check and returns success."""
        client, mock_lcu, token = app_bundle
        await mock_lcu.simulate_connect()
        await mock_lcu.simulate_ready_check(timer=10.0)
        assert await mock_lcu.get_gameflow_phase() == GameflowPhase.READY_CHECK

        # Post decline without body
        resp = client.post("/api/match/decline")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert mock_lcu.player_response == "Declined"

    @pytest.mark.asyncio
    async def test_post_match_decline_with_payload(
        self,
        app_bundle: tuple[TestClient, MockLcuAdapter, str],
    ) -> None:
        """POST /api/match/decline accepts optional JSON body containing token."""
        client, mock_lcu, token = app_bundle
        await mock_lcu.simulate_connect()
        await mock_lcu.simulate_ready_check(timer=10.0)

        resp = client.post("/api/match/decline", json={"token": token})
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert mock_lcu.player_response == "Declined"

    def test_post_match_decline_503_when_no_adapter(
        self,
        ott_manager: OTTManager,
        session_manager: SessionManager,
    ) -> None:
        """POST /api/match/decline raises 503 if LCU adapter is unavailable."""
        app = create_app(
            ott_manager=ott_manager,
            session_manager=session_manager,
            lcu_adapter=None,
            enable_monitor=False,
        )
        client = TestClient(app)
        resp = client.post("/api/match/decline")
        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_post_match_accept_success(
        self,
        app_bundle: tuple[TestClient, MockLcuAdapter, str],
    ) -> None:
        """POST /api/match/accept accepts the active ready check and returns success."""
        client, mock_lcu, token = app_bundle
        await mock_lcu.simulate_connect()
        await mock_lcu.simulate_ready_check(timer=10.0)

        resp = client.post("/api/match/accept")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert mock_lcu.player_response == "Accepted"

    def test_post_match_accept_503_when_no_adapter(
        self,
        ott_manager: OTTManager,
        session_manager: SessionManager,
    ) -> None:
        """POST /api/match/accept raises 503 if LCU adapter is unavailable."""
        app = create_app(
            ott_manager=ott_manager,
            session_manager=session_manager,
            lcu_adapter=None,
            enable_monitor=False,
        )
        client = TestClient(app)
        resp = client.post("/api/match/accept")
        assert resp.status_code == 503


# ============================================================================
# 2. WebSocket Match Commands (ACCEPT_MATCH & DECLINE_MATCH)
# ============================================================================

class TestMatchWebSocketCommands:
    """Tests for DECLINE_MATCH and ACCEPT_MATCH WebSocket commands."""

    @pytest.mark.asyncio
    async def test_ws_decline_match_command(
        self,
        app_bundle: tuple[TestClient, MockLcuAdapter, str],
    ) -> None:
        """WebSocket command DECLINE_MATCH declines ready check and receives READY_CHECK_DECLINED."""
        client, mock_lcu, token = app_bundle
        await mock_lcu.simulate_connect()
        await mock_lcu.simulate_ready_check(timer=10.0)

        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # welcome / initial state

            ws.send_json({"type": "DECLINE_MATCH", "token": token})

            # Read until reply arrives
            reply = None
            for _ in range(5):
                msg = ws.receive_json()
                if msg.get("type") == "READY_CHECK_DECLINED":
                    reply = msg
                    break

            assert reply is not None
            assert reply.get("success") is True
            assert mock_lcu.player_response == "Declined"

    @pytest.mark.asyncio
    async def test_ws_accept_match_command(
        self,
        app_bundle: tuple[TestClient, MockLcuAdapter, str],
    ) -> None:
        """WebSocket command ACCEPT_MATCH accepts ready check and receives READY_CHECK_ACCEPTED."""
        client, mock_lcu, token = app_bundle
        await mock_lcu.simulate_connect()
        await mock_lcu.simulate_ready_check(timer=10.0)

        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_json()  # welcome

            ws.send_json({"type": "ACCEPT_MATCH", "token": token})

            reply = None
            for _ in range(5):
                msg = ws.receive_json()
                if msg.get("type") == "READY_CHECK_ACCEPTED":
                    reply = msg
                    break

            assert reply is not None
            assert reply.get("success") is True
            assert mock_lcu.player_response == "Accepted"


# ============================================================================
# 3. Ready Check Modal DOM Structure & Behavior in HTML
# ============================================================================

class TestReadyCheckModalStructure:
    """Verifies #readyCheckModal structure, elements, and removal of permanent accept button."""

    def test_ready_check_modal_elements_exist(self) -> None:
        """Modal container, title, countdown, accept, decline, and status exist in INDEX_HTML."""
        assert 'id="readyCheckModal"' in INDEX_HTML
        assert 'class="ready-check-modal"' in INDEX_HTML
        assert 'MAÇ BULUNDU!' in INDEX_HTML
        assert 'id="readyAcceptBtn"' in INDEX_HTML
        assert 'id="readyDeclineBtn"' in INDEX_HTML
        assert 'id="readyCheckCountdown"' in INDEX_HTML
        assert 'id="readyCheckStatus"' in INDEX_HTML
        assert 'ready-check-timer-ring' in INDEX_HTML

    def test_permanent_accept_button_removed_from_idle_card(self) -> None:
        """The permanent accept button is removed from the idle/search card view (#mainView)."""
        # Ensure #acceptBtn is not in the action-area of mainView
        pattern = r'<div class="action-area">\s*<button id="acceptBtn"'
        assert re.search(pattern, INDEX_HTML) is None, "Permanent accept button must be removed from action-area"

    def test_ready_check_modal_default_hidden(self) -> None:
        """The ready check modal must be hidden by default (style='display: none;')."""
        match = re.search(r'<div id="readyCheckModal"[^>]*style="([^"]*)"', INDEX_HTML)
        assert match is not None
        assert "display: none" in match.group(1)


# ============================================================================
# 4. In-Game Match View DOM Structure in HTML
# ============================================================================

class TestInGameViewStructure:
    """Verifies #inGameView structure, title, subtitle, clock, and champion info."""

    def test_in_game_view_elements_exist(self) -> None:
        """#inGameView has title, subtitle, clock, and player champion info placeholders."""
        assert 'id="inGameView"' in INDEX_HTML
        assert 'class="in-game-view"' in INDEX_HTML
        assert 'id="inGameTitle"' in INDEX_HTML
        assert "Maçtasın!" in INDEX_HTML
        assert 'id="inGameSubtitle"' in INDEX_HTML
        assert "Maç devam ediyor." in INDEX_HTML
        assert 'id="inGameClock"' in INDEX_HTML
        assert 'id="inGameChampInfo"' in INDEX_HTML
        assert 'id="inGameChampName"' in INDEX_HTML
        assert 'id="inGameRole"' in INDEX_HTML
        assert 'id="inGameChampImg"' in INDEX_HTML

    def test_in_game_view_default_hidden(self) -> None:
        """The in-game view must be hidden by default."""
        match = re.search(r'<div id="inGameView"[^>]*style="([^"]*)"', INDEX_HTML)
        assert match is not None
        assert "display: none" in match.group(1)


# ============================================================================
# 5. Timer Separation & Visibility Stability in JavaScript
# ============================================================================

class TestTimerStabilityAndLifecycle:
    """Verifies timer separation, timestamp-based calculation, and event listeners."""

    def test_timer_variables_are_separated(self) -> None:
        """queueStartTime and inGameStartTime are isolated distinct variables."""
        assert "let queueStartTime = 0;" in INDEX_HTML
        assert "let inGameStartTime = 0;" in INDEX_HTML
        assert "let queueTimerInterval = null;" in INDEX_HTML
        assert "let inGameTimerInterval = null;" in INDEX_HTML

    def test_timestamp_based_elapsed_calculation(self) -> None:
        """Timers use Math.max(0, Math.floor((Date.now() - startTime) / 1000))."""
        assert "Math.max(0, Math.floor((Date.now() - queueStartTime) / 1000))" in INDEX_HTML
        assert "Math.max(0, Math.floor((Date.now() - inGameStartTime) / 1000))" in INDEX_HTML

    def test_visibilitychange_and_focus_listeners_exist(self) -> None:
        """Document has visibilitychange and window has focus listeners to refresh active clocks."""
        assert "document.addEventListener('visibilitychange'" in INDEX_HTML
        assert "window.addEventListener('focus'" in INDEX_HTML
        assert "refreshActiveTimers()" in INDEX_HTML

    def test_timer_lifecycle_functions_exist(self) -> None:
        """startInGameTimer, stopInGameTimer, startQueueTimer, stopQueueTimer exist."""
        assert "function startInGameTimer(" in INDEX_HTML
        assert "function stopInGameTimer(" in INDEX_HTML
        assert "function startQueueTimer(" in INDEX_HTML
        assert "function stopQueueTimer(" in INDEX_HTML
        assert "function openReadyCheckModal(" in INDEX_HTML
        assert "function closeReadyCheckModal(" in INDEX_HTML

    def test_modal_disables_both_buttons_on_click(self) -> None:
        """Clicking either accept or decline disables both buttons with 'İşleniyor...'."""
        assert "readyAcceptBtn.disabled = true;" in INDEX_HTML
        assert "readyDeclineBtn.disabled = true;" in INDEX_HTML
        assert "'İşleniyor...'" in INDEX_HTML

    def test_gameflow_phase_routing_rules(self) -> None:
        """ReadyCheck opens modal; InProgress starts inGame timer; End states stop inGame timer."""
        assert "p === 'ReadyCheck'" in INDEX_HTML
        assert "openReadyCheckModal()" in INDEX_HTML
        assert "p === 'InProgress'" in INDEX_HTML
        assert "startInGameTimer()" in INDEX_HTML
        assert "stopInGameTimer()" in INDEX_HTML


# ============================================================================
# 6. Responsive CSS Rules & Mobile Touch Targets
# ============================================================================

class TestMobileResponsiveCSS:
    """Verifies responsive CSS rules for viewports 320px, 375px, 390px, 430px and touch targets."""

    def test_viewport_meta_tag(self) -> None:
        """Viewport meta tag includes mobile responsive directives."""
        assert 'name="viewport"' in INDEX_HTML
        assert "width=device-width" in INDEX_HTML
        assert "viewport-fit=cover" in INDEX_HTML

    def test_overflow_x_hidden_enforced(self) -> None:
        """overflow-x: hidden is present to prevent horizontal scrolling on mobile."""
        assert "overflow-x: hidden;" in INDEX_HTML

    def test_mobile_media_queries_exist(self) -> None:
        """CSS contains media queries for mobile viewports (320px, 375px, 390px, 430px)."""
        assert "@media (max-width: 430px)" in INDEX_HTML
        assert "@media (max-width: 390px)" in INDEX_HTML
        assert "@media (max-width: 375px)" in INDEX_HTML
        assert "@media (max-width: 320px)" in INDEX_HTML

    def test_touch_target_minimum_44px(self) -> None:
        """Buttons, chips, and interactive elements enforce min-height: 44px for touch targets."""
        assert "min-height: 44px;" in INDEX_HTML
        assert ".btn-ready-accept" in INDEX_HTML
        assert ".btn-ready-decline" in INDEX_HTML

    def test_modal_centered_styling(self) -> None:
        """.ready-check-modal has fixed overlay, backdrop-filter, and centered flex layout."""
        assert ".ready-check-modal" in INDEX_HTML
        assert "backdrop-filter: blur" in INDEX_HTML
        assert "z-index: 9999" in INDEX_HTML
