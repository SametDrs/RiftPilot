"""Tests for End-to-End Game Mode & Lane/Role Selection (feat/mode-lane-selection).

Verifies:
1. Core domain rules (`riftpilot.core.lobby`):
   - Queue ID support checking: 420 (Solo/Duo), 440 (Flex), 400 (Draft Pick) support lanes;
     450 (ARAM), 430 (Blind Pick) disable lane selection.
   - Role filtering: TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY, FILL available for draft modes; empty for non-draft.
   - Role preference resolution & intelligent defaults: Smart default to FILL if unselected in draft;
     Automatic reset to UNSELECTED when switching to or playing non-draft modes.
   - Case-insensitivity, duplicate handling, and single-role resolution.
2. Mock LCU Adapter synchronization:
   - Creation of draft lobbies initializes smart defaults.
   - Switching to non-draft lobbies clears preferences.
3. WebHub REST API:
   - POST /api/lobby/preferences and PUT /api/lobby/preferences with queueId.
   - POST /api/lobby/matchmaking/start with mode and role resolution.
4. WebHub WebSocket protocol:
   - SET_POSITION_PREFERENCES command handling and POSITION_PREFERENCES_RESULT response.
   - START_MATCHMAKING command with queueId and position preferences.
5. Desktop Companion Dashboard (PySide6 MainWindow):
   - Mode combo box items and selection handling.
   - Lane selection buttons grid state (container visibility, disabled notice).
   - Mode switching resets lane selection for 450 (ARAM) and 430 (Blind Pick).
   - Matchmaking start action trigger.
6. Mobile Web Panel (INDEX_HTML):
   - Markup contains mode chips and responsive lane chips.
   - Lane disabled notice for non-draft modes.
   - CSS responsive media queries for 430px, 390px, 375px, and 320px screens.
   - JavaScript state management and synchronization routines.
"""

from __future__ import annotations

import os

# Ensure headless Qt for PySide6 desktop UI tests
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from typing import Generator
from unittest.mock import patch
import pytest
from starlette.testclient import TestClient

from PySide6.QtWidgets import QApplication

from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.web_hub import INDEX_HTML, create_app
from riftpilot.core.lcu_port import LcuConnectionStatus
from riftpilot.core.lobby import (
    DRAFT_QUEUE_IDS,
    NON_DRAFT_QUEUE_IDS,
    STANDARD_LANE_POSITIONS,
    LobbyData,
    LobbyMember,
    PositionPreference,
    get_available_lanes_for_mode,
    is_lane_selection_supported,
    resolve_lane_preferences,
)
from riftpilot.core.pairing import OTTManager, SessionManager
from riftpilot.ui.main_window import MainWindow


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Headless QApplication singleton instance."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def web_bundle() -> tuple[TestClient, MockLcuAdapter, str]:
    """Provides TestClient, MockLcuAdapter (connected), and authenticated session token."""
    ott_mgr = OTTManager()
    session_mgr = SessionManager()
    lcu = MockLcuAdapter()
    lcu._status = LcuConnectionStatus.CONNECTED
    app = create_app(ott_manager=ott_mgr, session_manager=session_mgr, lcu_adapter=lcu)
    client = TestClient(app)

    ott = ott_mgr.generate_ott()
    pair_resp = client.post("/api/pair", json={"ott": ott.token, "deviceName": "MobileTester"})
    token = pair_resp.json()["session_token"]
    return client, lcu, token


@pytest.fixture
def desktop_window(qapp: QApplication) -> Generator[MainWindow, None, None]:
    """Provides an initialized MainWindow desktop window instance."""
    ott_mgr = OTTManager()
    session_mgr = SessionManager()
    lcu = MockLcuAdapter()
    lcu._status = LcuConnectionStatus.CONNECTED
    window = MainWindow(
        ott_manager=ott_mgr,
        session_manager=session_mgr,
        lan_ip="127.0.0.1",
        port=8000,
        lcu_adapter=lcu,
    )
    yield window
    window.close()
    window.deleteLater()


# ==============================================================================
# 1. Core Domain Tests (riftpilot.core.lobby)
# ==============================================================================


class TestCoreModeLaneSelection:
    """Core domain rules for game mode and lane filtering."""

    def test_is_lane_selection_supported_for_draft_modes(self) -> None:
        for q_id in (420, 440, 400):
            assert is_lane_selection_supported(q_id) is True

    def test_is_lane_selection_disabled_for_non_draft_and_unknown_modes(self) -> None:
        for q_id in (450, 430, 0, 999, -1):
            assert is_lane_selection_supported(q_id) is False

    def test_get_available_lanes_for_mode_draft(self) -> None:
        expected = [
            PositionPreference.TOP,
            PositionPreference.JUNGLE,
            PositionPreference.MIDDLE,
            PositionPreference.BOTTOM,
            PositionPreference.UTILITY,
            PositionPreference.FILL,
        ]
        for q_id in (420, 440, 400):
            assert get_available_lanes_for_mode(q_id) == expected

    def test_get_available_lanes_for_mode_non_draft_empty(self) -> None:
        for q_id in (450, 430, 0, 999):
            assert get_available_lanes_for_mode(q_id) == []

    def test_resolve_lane_preferences_draft_both_specified(self) -> None:
        p1, p2 = resolve_lane_preferences(420, "TOP", "JUNGLE")
        assert p1 == PositionPreference.TOP
        assert p2 == PositionPreference.JUNGLE

    def test_resolve_lane_preferences_draft_primary_only(self) -> None:
        p1, p2 = resolve_lane_preferences(400, "MIDDLE", None)
        assert p1 == PositionPreference.MIDDLE
        assert p2 == PositionPreference.FILL

    def test_resolve_lane_preferences_draft_fill_primary(self) -> None:
        p1, p2 = resolve_lane_preferences(440, "FILL", "BOTTOM")
        assert p1 == PositionPreference.FILL
        assert p2 == PositionPreference.UNSELECTED

    def test_resolve_lane_preferences_draft_intelligent_default(self) -> None:
        p1, p2 = resolve_lane_preferences(420, None, None)
        assert p1 == PositionPreference.FILL
        assert p2 == PositionPreference.UNSELECTED

    def test_resolve_lane_preferences_draft_duplicate_roles(self) -> None:
        p1, p2 = resolve_lane_preferences(420, "UTILITY", "UTILITY")
        assert p1 == PositionPreference.UTILITY
        assert p2 == PositionPreference.FILL

    def test_resolve_lane_preferences_draft_case_insensitive(self) -> None:
        p1, p2 = resolve_lane_preferences(420, "mid", "bot")
        assert p1 == PositionPreference.MIDDLE
        assert p2 == PositionPreference.BOTTOM

    def test_resolve_lane_preferences_non_draft_always_resets(self) -> None:
        for q_id in (450, 430):
            p1, p2 = resolve_lane_preferences(q_id, "TOP", "JUNGLE")
            assert p1 == PositionPreference.UNSELECTED
            assert p2 == PositionPreference.UNSELECTED

    def test_resolve_lane_preferences_mode_switch_clears_invalid_roles(self) -> None:
        p1_draft, p2_draft = resolve_lane_preferences(420, "MIDDLE", "TOP")
        assert p1_draft == PositionPreference.MIDDLE

        p1_aram, p2_aram = resolve_lane_preferences(450, p1_draft.value, p2_draft.value)
        assert p1_aram == PositionPreference.UNSELECTED
        assert p2_aram == PositionPreference.UNSELECTED


# ==============================================================================
# 2. Mock LCU Adapter Integration Tests
# ==============================================================================


class TestMockLcuModeLaneIntegration:
    """Verifies MockLcuAdapter handles lobby queue switching and preference state."""

    @pytest.mark.asyncio
    async def test_mock_lcu_create_lobby_draft_defaults(self) -> None:
        adapter = MockLcuAdapter()
        await adapter.simulate_connect()
        assert await adapter.create_lobby(420) is True

        lobby = await adapter.get_lobby()
        assert lobby is not None
        assert lobby.queue_id == 420
        assert len(lobby.members) == 1
        member = lobby.members[0]
        assert member.first_position_preference == PositionPreference.FILL.value
        assert member.second_position_preference == PositionPreference.UNSELECTED.value

    @pytest.mark.asyncio
    async def test_mock_lcu_create_lobby_non_draft_clears_preferences(self) -> None:
        adapter = MockLcuAdapter()
        await adapter.simulate_connect()
        await adapter.create_lobby(420)
        await adapter.set_position_preferences("TOP", "JUNGLE")

        assert await adapter.create_lobby(450) is True
        lobby = await adapter.get_lobby()
        assert lobby is not None
        assert lobby.queue_id == 450
        member = lobby.members[0]
        assert member.first_position_preference == PositionPreference.UNSELECTED.value
        assert member.second_position_preference == PositionPreference.UNSELECTED.value

    @pytest.mark.asyncio
    async def test_mock_lcu_set_position_preferences(self) -> None:
        adapter = MockLcuAdapter()
        await adapter.simulate_connect()
        await adapter.create_lobby(440)

        success = await adapter.set_position_preferences("MIDDLE", "UTILITY")
        assert success is True
        lobby = await adapter.get_lobby()
        assert lobby is not None
        assert lobby.members[0].first_position_preference == "MIDDLE"
        assert lobby.members[0].second_position_preference == "UTILITY"


# ==============================================================================
# 3. WebHub REST API Tests
# ==============================================================================


class TestWebHubRestModeLaneApi:
    """REST endpoints for mode and position preferences."""

    @pytest.mark.asyncio
    async def test_rest_post_preferences_draft_success(
        self, web_bundle: tuple[TestClient, MockLcuAdapter, str]
    ) -> None:
        client, mock_lcu, _ = web_bundle
        await mock_lcu.create_lobby(420)

        resp = client.post(
            "/api/lobby/preferences",
            json={"queueId": 420, "firstPreference": "MIDDLE", "secondPreference": "TOP"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["first"] == "MIDDLE"
        assert data["second"] == "TOP"
        assert data["queueId"] == 420

    @pytest.mark.asyncio
    async def test_rest_put_preferences_backwards_compatibility(
        self, web_bundle: tuple[TestClient, MockLcuAdapter, str]
    ) -> None:
        client, mock_lcu, _ = web_bundle
        await mock_lcu.create_lobby(400)

        resp = client.put(
            "/api/lobby/preferences",
            json={"queueId": 400, "first": "JUNGLE", "second": "BOTTOM"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["first"] == "JUNGLE"
        assert data["second"] == "BOTTOM"

    @pytest.mark.asyncio
    async def test_rest_post_preferences_non_draft_auto_unselected(
        self, web_bundle: tuple[TestClient, MockLcuAdapter, str]
    ) -> None:
        client, mock_lcu, _ = web_bundle
        await mock_lcu.create_lobby(450)

        resp = client.post(
            "/api/lobby/preferences",
            json={"queueId": 450, "first": "TOP", "second": "JUNGLE"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["first"] == "UNSELECTED"
        assert data["second"] == "UNSELECTED"

    @pytest.mark.asyncio
    async def test_rest_start_matchmaking_draft_defaults(
        self, web_bundle: tuple[TestClient, MockLcuAdapter, str]
    ) -> None:
        client, mock_lcu, _ = web_bundle

        resp = client.post("/api/lobby/matchmaking/start", json={"queueId": 420})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        lobby = await mock_lcu.get_lobby()
        assert lobby is not None
        assert lobby.members[0].first_position_preference == "FILL"
        assert lobby.members[0].second_position_preference == "UNSELECTED"

    @pytest.mark.asyncio
    async def test_rest_start_matchmaking_aram_clears_preferences(
        self, web_bundle: tuple[TestClient, MockLcuAdapter, str]
    ) -> None:
        client, mock_lcu, _ = web_bundle

        resp = client.post(
            "/api/lobby/matchmaking/start",
            json={"queueId": 450, "firstPreference": "TOP", "secondPreference": "JUNGLE"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        lobby = await mock_lcu.get_lobby()
        assert lobby is not None
        assert lobby.members[0].first_position_preference == "UNSELECTED"
        assert lobby.members[0].second_position_preference == "UNSELECTED"


# ==============================================================================
# 4. WebHub WebSocket Protocol Tests
# ==============================================================================


class TestWebHubWebSocketModeLaneSync:
    """WebSocket commands SET_POSITION_PREFERENCES and START_MATCHMAKING."""

    def test_ws_set_position_preferences_draft(
        self, web_bundle: tuple[TestClient, MockLcuAdapter, str]
    ) -> None:
        client, mock_lcu, token = web_bundle

        with client.websocket_connect(f"/ws?token={token}") as ws:
            welcome = ws.receive_json()
            assert welcome["type"] == "WELCOME"

            ws.send_json({
                "type": "SET_POSITION_PREFERENCES",
                "token": token,
                "queueId": 420,
                "first": "TOP",
                "second": "MIDDLE",
            })
            resp = ws.receive_json()
            assert resp["type"] == "POSITION_PREFERENCES_RESULT"
            assert resp["success"] is True
            assert resp["first"] == "TOP"
            assert resp["second"] == "MIDDLE"
            assert resp["queueId"] == 420

    def test_ws_set_position_preferences_non_draft_resets(
        self, web_bundle: tuple[TestClient, MockLcuAdapter, str]
    ) -> None:
        client, mock_lcu, token = web_bundle

        with client.websocket_connect(f"/ws?token={token}") as ws:
            welcome = ws.receive_json()
            assert welcome["type"] == "WELCOME"

            ws.send_json({
                "type": "SET_POSITION_PREFERENCES",
                "token": token,
                "queueId": 450,
                "first": "BOTTOM",
                "second": "UTILITY",
            })
            resp = ws.receive_json()
            assert resp["type"] == "POSITION_PREFERENCES_RESULT"
            assert resp["success"] is True
            assert resp["first"] == "UNSELECTED"
            assert resp["second"] == "UNSELECTED"
            assert resp["queueId"] == 450

    def test_ws_start_matchmaking_with_lane_preferences(
        self, web_bundle: tuple[TestClient, MockLcuAdapter, str]
    ) -> None:
        client, mock_lcu, token = web_bundle

        with client.websocket_connect(f"/ws?token={token}") as ws:
            welcome = ws.receive_json()
            assert welcome["type"] == "WELCOME"

            ws.send_json({
                "type": "START_MATCHMAKING",
                "token": token,
                "queueId": 420,
                "firstPreference": "JUNGLE",
                "secondPreference": "TOP",
            })
            resp = ws.receive_json()
            while resp.get("type") in ("GAMEFLOW_UPDATE", "LOBBY_UPDATE"):
                resp = ws.receive_json()
            assert resp["type"] == "MATCHMAKING_STARTED"
            assert resp["success"] is True
            assert resp["queueId"] == 420


# ==============================================================================
# 5. Desktop Companion Dashboard (PySide6 MainWindow) Tests
# ==============================================================================


class TestDesktopUiModeLaneSelection:
    """Desktop Companion Dashboard mode & lane selection UI interactions."""

    def test_desktop_ui_mode_selector_items(self, desktop_window: MainWindow) -> None:
        combo = desktop_window.mode_combo
        assert combo is not None
        assert combo.count() == 5

        expected_modes = [420, 440, 400, 430, 450]
        actual_modes = [combo.itemData(i) for i in range(combo.count())]
        assert actual_modes == expected_modes

    def test_desktop_ui_draft_mode_shows_lane_selection(
        self, desktop_window: MainWindow
    ) -> None:
        desktop_window.set_selected_mode(420)
        assert desktop_window.selected_queue_id == 420
        assert desktop_window.lane_container.isHidden() is False
        assert desktop_window.lane_disabled_notice.isHidden() is True
        assert len(desktop_window.lane_buttons) == 6

    def test_desktop_ui_lane_button_click_updates_selection(
        self, desktop_window: MainWindow
    ) -> None:
        desktop_window.set_selected_mode(420)
        top_btn = desktop_window.lane_buttons.get("TOP")
        assert top_btn is not None

        top_btn.click()
        assert desktop_window.selected_lane == "TOP"
        assert top_btn.property("active") == "true"

    def test_desktop_ui_switching_to_aram_disables_lanes_and_resets(
        self, desktop_window: MainWindow
    ) -> None:
        desktop_window.set_selected_mode(420)
        desktop_window.select_lane("MIDDLE")
        assert desktop_window.selected_lane == "MIDDLE"

        # Switch to ARAM (450)
        desktop_window.set_selected_mode(450)
        assert desktop_window.selected_queue_id == 450
        assert desktop_window.selected_lane is None
        assert desktop_window.lane_container.isHidden() is True
        assert desktop_window.lane_disabled_notice.isHidden() is False

    def test_desktop_ui_switching_to_blind_pick_disables_lanes_and_resets(
        self, desktop_window: MainWindow
    ) -> None:
        desktop_window.set_selected_mode(440)
        desktop_window.select_lane("JUNGLE")
        assert desktop_window.selected_lane == "JUNGLE"

        # Switch to Blind Pick (430)
        desktop_window.set_selected_mode(430)
        assert desktop_window.selected_queue_id == 430
        assert desktop_window.selected_lane is None
        assert desktop_window.lane_container.isHidden() is True
        assert desktop_window.lane_disabled_notice.isHidden() is False

    def test_desktop_ui_start_matchmaking_invokes_adapter(
        self, desktop_window: MainWindow
    ) -> None:
        desktop_window.set_selected_mode(420)
        desktop_window.select_lane("BOTTOM")

        with patch.object(desktop_window, "_run_async") as mock_run:
            desktop_window.start_matchmaking_action()
            assert mock_run.called is True
            if mock_run.call_args and mock_run.call_args[0]:
                coro = mock_run.call_args[0][0]
                if hasattr(coro, "close"):
                    coro.close()


# ==============================================================================
# 6. Mobile Web Panel & Responsive UX Tests (INDEX_HTML)
# ==============================================================================


class TestMobileWebResponsiveUx:
    """Mobile Web Hub markup, responsive styling and JS synchronizers."""

    def test_index_html_contains_mode_chips(self) -> None:
        for q_id in ("420", "440", "400", "430", "450"):
            assert f'data-queue-id="{q_id}"' in INDEX_HTML

    def test_index_html_contains_lane_selector_and_chips(self) -> None:
        assert 'id="laneSelectorSection"' in INDEX_HTML
        assert 'id="laneChipsGrid"' in INDEX_HTML
        for lane in ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY", "FILL"):
            assert f'data-lane="{lane}"' in INDEX_HTML

    def test_index_html_contains_lane_disabled_notice(self) -> None:
        assert 'id="laneDisabledNotice"' in INDEX_HTML
        assert "Bu modda koridor seçimi bulunmamaktadır" in INDEX_HTML

    def test_index_html_contains_responsive_media_queries(self) -> None:
        assert "@media (max-width: 430px)" in INDEX_HTML
        assert "@media (max-width: 390px)" in INDEX_HTML
        assert "@media (max-width: 375px)" in INDEX_HTML
        assert "@media (max-width: 320px)" in INDEX_HTML
        assert ".lane-chips-grid" in INDEX_HTML
        assert ".lane-chip" in INDEX_HTML

    def test_index_html_js_mode_lane_sync_logic(self) -> None:
        assert "function updateLaneUI()" in INDEX_HTML
        assert "function onLaneChipClick(" in INDEX_HTML
        assert "function syncPreferencesToServer(" in INDEX_HTML
        assert "SET_POSITION_PREFERENCES" in INDEX_HTML
        assert "START_MATCHMAKING" in INDEX_HTML
        assert "POSITION_PREFERENCES_RESULT" in INDEX_HTML
        assert "selectedFirstLane" in INDEX_HTML
        assert "selectedSecondLane" in INDEX_HTML
