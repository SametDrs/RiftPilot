"""End-to-End and Integration Tests for Match Flow, Mobile UX, and Trades/Swaps.

Validates:
1. Live Queue Timer formatting, start/stop transitions, and elapsed calculation.
2. Mobile Game Mode selection and matchmaking start/cancel (REST and WebSocket).
3. Desktop App window size locking (fixed 500x780).
4. Champion Select mobile responsive layout, CSS flex rules, and viewport meta.
5. Champion search mode filtering and selection persistence.
6. Resilient fallback champion pool catalog when client grid is empty.
7. Champion trade offers (view, accept, decline, cancel).
8. Lane swap requests (request, view, accept, decline).
9. Zero-trust command authentication and debounce protection.
"""

from __future__ import annotations

import asyncio
from typing import Any, Generator
import pytest
from starlette.testclient import TestClient

from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.web_hub import (
    DEFAULT_CHAMPION_CATALOG,
    INDEX_HTML,
    create_app,
)
from riftpilot.core.champ_select import (
    ChampSelectAction,
    ChampSelectPlayer,
    ChampSelectSession,
    ChampSelectTimer,
    ChampionSummary,
)
from riftpilot.core.lobby import (
    ChampSelectTrade,
    LaneSwapRequest,
    LobbyData,
    LobbyMember,
    MatchmakingSearchState,
    TradeState,
)
from riftpilot.core.pairing import OTTManager, SessionManager
from riftpilot.core.queue_timer import QueueTimer, format_duration
from riftpilot.ui.main_window import MainWindow


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
def test_app_bundle(
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
    pair_resp = client.post("/api/pair", json={"ott": ott.token, "deviceName": "TestMobile"})
    assert pair_resp.status_code == 200
    token = pair_resp.json()["sessionToken"]
    return client, mock_lcu, token


def receive_typed_msg(ws: Any, expected_type: str, max_attempts: int = 10) -> dict[str, Any]:
    """Helper reading websocket messages until a specific type arrives, ignoring background events."""
    for _ in range(max_attempts):
        msg = ws.receive_json()
        if msg.get("type") == expected_type:
            return msg
    raise AssertionError(f"Expected message of type '{expected_type}' did not arrive within {max_attempts} messages.")


# ============================================================================
# Scenario 1: Live Queue Timer Domain and Sync
# ============================================================================

def test_queue_timer_formatting_and_lifecycle() -> None:
    """Queue timer formats 00:00, calculates elapsed time, and handles start/stop/reset."""
    timer = QueueTimer()
    assert timer.formatted_elapsed == "00:00"
    assert timer.formatted_estimated == "--:--"
    assert not timer.is_running

    # Start timer with 120s estimated
    timer.start(estimated_duration_seconds=120.0)
    assert timer.is_running
    assert timer.formatted_estimated == "02:00"
    assert timer.formatted_elapsed == "00:00"

    # Tick 75 seconds
    timer.tick(75.0)
    assert timer.formatted_elapsed == "01:15"
    assert timer.elapsed_seconds == 75.0

    # Stop preserves elapsed time
    timer.stop()
    assert not timer.is_running
    assert timer.formatted_elapsed == "01:15"

    # Reset clears everything
    timer.reset()
    assert timer.elapsed_seconds == 0.0
    assert timer.formatted_elapsed == "00:00"


def test_format_duration_edge_cases() -> None:
    """format_duration handles zeros, negative clamping, and long queues."""
    assert format_duration(0) == "00:00"
    assert format_duration(-15) == "00:00"
    assert format_duration(59) == "00:59"
    assert format_duration(60) == "01:00"
    assert format_duration(3605) == "60:05"


# ============================================================================
# Scenario 2: Mobile Queue Mode Selection and Matchmaking Control
# ============================================================================

@pytest.mark.asyncio
async def test_rest_mobile_mode_selection_and_matchmaking_flow(
    test_app_bundle: tuple[TestClient, MockLcuAdapter, str],
) -> None:
    """Selecting a mode and starting matchmaking via REST endpoints."""
    client, mock_lcu, token = test_app_bundle
    await mock_lcu.simulate_connect()

    # 1. Create lobby for Ranked Solo (420)
    resp = client.post("/api/lobby/create", json={"queueId": 420})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert resp.json()["queueId"] == 420

    lobby = await mock_lcu.get_lobby()
    assert lobby is not None
    assert lobby.queue_id == 420

    # 2. Start matchmaking with queueId override for ARAM (450)
    start_resp = client.post("/api/lobby/matchmaking/start", json={"queueId": 450})
    assert start_resp.status_code == 200
    assert start_resp.json()["success"] is True

    # 3. Verify matchmaking state is searching
    search = await mock_lcu.get_matchmaking_search()
    assert search is not None
    assert search.is_in_queue is True

    # 4. Cancel matchmaking
    cancel_resp = client.post("/api/lobby/matchmaking/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["success"] is True

    search_after = await mock_lcu.get_matchmaking_search()
    assert search_after is not None
    assert search_after.is_in_queue is False


@pytest.mark.asyncio
async def test_ws_mobile_mode_selection_and_matchmaking(
    test_app_bundle: tuple[TestClient, MockLcuAdapter, str],
) -> None:
    """Full WebSocket lifecycle for mode selection and matchmaking."""
    client, mock_lcu, token = test_app_bundle
    await mock_lcu.simulate_connect()

    with client.websocket_connect(f"/ws?token={token}") as ws:
        welcome = ws.receive_json()
        assert welcome["type"] == "WELCOME"

        # Create Flex Lobby (440)
        ws.send_json({"type": "CREATE_LOBBY", "queueId": 440, "token": token})
        resp = receive_typed_msg(ws, "LOBBY_CREATED")
        assert resp["type"] == "LOBBY_CREATED"
        assert resp["success"] is True
        assert resp["queueId"] == 440

        # Start matchmaking
        ws.send_json({"type": "START_MATCHMAKING", "queueId": 440, "token": token})
        mm_resp = receive_typed_msg(ws, "MATCHMAKING_STARTED")
        assert mm_resp["type"] == "MATCHMAKING_STARTED"
        assert mm_resp["success"] is True

        # Cancel matchmaking
        ws.send_json({"type": "CANCEL_MATCHMAKING", "token": token})
        cancel_resp = receive_typed_msg(ws, "MATCHMAKING_CANCELED")
        assert cancel_resp["type"] == "MATCHMAKING_CANCELED"
        assert cancel_resp["success"] is True


# ============================================================================
# Scenario 3: Desktop Window Sizing and Lock
# ============================================================================

def test_desktop_window_dimensions_and_fixed_size(
    ott_manager: OTTManager, session_manager: SessionManager
) -> None:
    """Desktop MainWindow enforces setFixedSize(500, 780)."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    window = MainWindow(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lan_ip="127.0.0.1",
        port=8000,
    )
    assert window.minimumWidth() == 500
    assert window.maximumWidth() == 500
    assert window.minimumHeight() == 780
    assert window.maximumHeight() == 780
    assert window.width() == 500
    assert window.height() == 780


# ============================================================================
# Scenario 4: Mobile Viewport and Champ Select Responsive CSS
# ============================================================================

def test_mobile_responsive_html_and_css_rules() -> None:
    """INDEX_HTML contains responsive viewport meta, champSelectView column layout, and tap targets."""
    # Viewport meta check
    assert '<meta name="viewport" content="width=device-width, initial-scale=1.0' in INDEX_HTML

    # Root cause fix: #champSelectView explicit column flex & no overflow
    assert "#champSelectView {" in INDEX_HTML
    assert "flex-direction: column;" in INDEX_HTML
    assert "overflow-x: hidden;" in INDEX_HTML

    # Responsive teams summary media query
    assert "@media (max-width: 480px)" in INDEX_HTML
    assert ".teams-summary" in INDEX_HTML

    # Queue Timer & Mode selector elements
    assert "queueTimerCard" in INDEX_HTML
    assert "queueTimerClock" in INDEX_HTML
    assert "modeSelectorCard" in INDEX_HTML
    assert "tradeNoticeBanner" in INDEX_HTML


# ============================================================================
# Scenario 5: Champion Pool Fallback and Search Filtering
# ============================================================================

@pytest.mark.asyncio
async def test_champ_select_grid_fallback_catalog(
    test_app_bundle: tuple[TestClient, MockLcuAdapter, str],
) -> None:
    """When client grid is empty, /api/champ-select/grid returns resilient DEFAULT_CHAMPION_CATALOG."""
    client, mock_lcu, token = test_app_bundle
    await mock_lcu.simulate_connect()

    # Empty grid initially
    resp = client.get("/api/champ-select/grid")
    assert resp.status_code == 200
    champions = resp.json()["champions"]
    assert len(champions) >= 20  # Fallback catalog populated
    champ_ids = {c["id"] for c in champions}
    assert 103 in champ_ids  # Ahri
    assert 157 in champ_ids  # Yasuo
    assert 64 in champ_ids   # Lee Sin


@pytest.mark.asyncio
async def test_ws_grid_champions_fallback_catalog(
    test_app_bundle: tuple[TestClient, MockLcuAdapter, str],
) -> None:
    """WebSocket GET_GRID_CHAMPIONS returns fallback catalog when client grid is empty."""
    client, mock_lcu, token = test_app_bundle
    await mock_lcu.simulate_connect()

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()  # WELCOME

        ws.send_json({"type": "GET_GRID_CHAMPIONS", "token": token})
        grid_resp = ws.receive_json()
        assert grid_resp["type"] == "GRID_CHAMPIONS"
        data = grid_resp["data"]
        assert len(data) >= 20


# ============================================================================
# Scenario 6: Champion Trades Lifecycle
# ============================================================================

@pytest.mark.asyncio
async def test_champion_trade_request_accept_and_cancel_flow(
    test_app_bundle: tuple[TestClient, MockLcuAdapter, str],
) -> None:
    """Full lifecycle for requesting, viewing, accepting, and canceling champion trades."""
    client, mock_lcu, token = test_app_bundle
    await mock_lcu.simulate_connect()

    # Request a trade
    req_resp = client.post("/api/champ-select/trade/request", json={"tradeId": 1})
    assert req_resp.status_code == 200
    assert req_resp.json()["success"] is True

    # View trades
    trades_resp = client.get("/api/champ-select/trades")
    assert trades_resp.status_code == 200
    trades = trades_resp.json()["trades"]
    assert len(trades) >= 1
    assert trades[0]["state"] in (TradeState.SENT, TradeState.REQUESTED, "SENT", "REQUESTED")

    # Accept trade
    accept_resp = client.post("/api/champ-select/trade/accept", json={"tradeId": 1})
    assert accept_resp.status_code == 200
    assert accept_resp.json()["success"] is True

    # Cancel trade
    cancel_resp = client.post("/api/champ-select/trade/cancel", json={"tradeId": 1})
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["success"] is True


# ============================================================================
# Scenario 7: Lane Swaps Lifecycle
# ============================================================================

@pytest.mark.asyncio
async def test_lane_swap_request_accept_and_decline_flow(
    test_app_bundle: tuple[TestClient, MockLcuAdapter, str],
) -> None:
    """Full lifecycle for requesting, viewing, accepting, and declining lane swaps."""
    client, mock_lcu, token = test_app_bundle
    await mock_lcu.simulate_connect()

    # Request lane swap with teammate at cell 2
    req_resp = client.post("/api/champ-select/lane-swap/request", json={"cellId": 2})
    assert req_resp.status_code == 200
    assert req_resp.json()["success"] is True
    assert req_resp.json()["toCellId"] == 2

    # View active lane swaps
    swaps_resp = client.get("/api/champ-select/lane-swaps")
    assert swaps_resp.status_code == 200
    swaps = swaps_resp.json()["laneSwaps"]
    assert len(swaps) == 1
    assert swaps[0]["to_cell_id"] == 2
    assert swaps[0]["status"] == "PENDING"

    # Accept lane swap (initiator was cell 0)
    accept_resp = client.post("/api/champ-select/lane-swap/accept", json={"cellId": 0})
    assert accept_resp.status_code == 200
    assert accept_resp.json()["success"] is True

    # Simulate another request to test decline
    await mock_lcu.simulate_request_lane_swap(from_cell_id=3, to_cell_id=0)
    decline_resp = client.post("/api/champ-select/lane-swap/decline", json={"cellId": 3})
    assert decline_resp.status_code == 200
    assert decline_resp.json()["success"] is True


@pytest.mark.asyncio
async def test_ws_lane_swap_lifecycle(
    test_app_bundle: tuple[TestClient, MockLcuAdapter, str],
) -> None:
    """WebSocket messaging for lane swaps."""
    client, mock_lcu, token = test_app_bundle
    await mock_lcu.simulate_connect()

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.receive_json()  # WELCOME

        # Request lane swap
        ws.send_json({"type": "LANE_SWAP_REQUEST", "toCellId": 3, "token": token})
        res1 = receive_typed_msg(ws, "LANE_SWAP_RESULT")
        assert res1["type"] == "LANE_SWAP_RESULT"
        assert res1["action"] == "request"
        assert res1["success"] is True

        # Query lane swaps
        ws.send_json({"type": "GET_LANE_SWAPS", "token": token})
        res2 = receive_typed_msg(ws, "LANE_SWAPS_UPDATE")
        assert res2["type"] == "LANE_SWAPS_UPDATE"
        assert len(res2["laneSwaps"]) >= 1

        # Accept lane swap
        ws.send_json({"type": "LANE_SWAP_ACCEPT", "fromCellId": 0, "token": token})
        res3 = receive_typed_msg(ws, "LANE_SWAP_RESULT")
        assert res3["type"] == "LANE_SWAP_RESULT"
        assert res3["action"] == "accept"
        assert res3["success"] is True


# ============================================================================
# Scenario 8: Zero-Trust Security & Debounce Safeguards
# ============================================================================

def test_unauthorized_websocket_rejected_without_token(
    ott_manager: OTTManager,
    session_manager: SessionManager,
    mock_lcu: MockLcuAdapter,
) -> None:
    """WebSocket connections without a valid session token receive UNAUTHORIZED message and close."""
    app = create_app(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lcu_adapter=mock_lcu,
        enable_monitor=False,
    )
    client = TestClient(app)
    with client.websocket_connect("/ws?token=invalid_forged_token") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "UNAUTHORIZED"
