"""RiftPilot Adapters — Mock LCU Adapter.

Provides a deterministic test double for the League Client (LCU) outbound port.
Enforces Hexagonal Architecture (ADR-0003) contracts:
- Simulates gameflow phase transitions and event subscriptions.
- Simulates ready check flows.
- Simulates champion select sessions, actions (pick/ban), and champion grids.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

from riftpilot.core.champ_select import ChampSelectSession, ChampionSummary
from riftpilot.core.lcu_port import GameflowPhase, ILcuPort, LcuConnectionStatus

logger = logging.getLogger("RiftPilot.MockLCU")


class MockLcuAdapter(ILcuPort):
    """Deterministic mock adapter implementing `ILcuPort` for testing."""

    def __init__(self) -> None:
        """Initialize mock LCU adapter in a disconnected state."""
        self._status = LcuConnectionStatus.DISCONNECTED
        self._phase = GameflowPhase.NONE
        self._subscribers: list[Callable[[GameflowPhase], Any]] = []
        self._champ_select_subscribers: list[
            Callable[[ChampSelectSession | None], Any]
        ] = []

        # Ready check state attributes
        self.player_response: str = "None"
        self.ready_check_state: str = "Invalid"
        self.ready_check: dict[str, Any] = {
            "playerResponse": "None",
            "state": "Invalid",
            "timer": 0.0,
        }

        # Champion select state attributes
        self._champ_select_session: ChampSelectSession | None = None
        self._grid_champions: list[ChampionSummary] = []

    # ========================================================================
    # ILcuPort Implementation — Core Status & Phase
    # ========================================================================

    async def get_connection_status(self) -> LcuConnectionStatus:
        """Return the current mock connection status."""
        return self._status

    async def get_gameflow_phase(self) -> GameflowPhase:
        """Return the current mock gameflow phase."""
        return self._phase

    async def accept_ready_check(self) -> bool:
        """Accept an active ready check if connected and in READY_CHECK phase.

        Returns:
            True if ready check was successfully accepted, False otherwise.
        """
        if self._status != LcuConnectionStatus.CONNECTED:
            logger.warning("accept_ready_check rejected: adapter is not connected.")
            return False

        if self._phase != GameflowPhase.READY_CHECK:
            logger.warning(
                "accept_ready_check rejected: current phase is %s (expected %s).",
                self._phase,
                GameflowPhase.READY_CHECK,
            )
            return False

        self.player_response = "Accepted"
        self.ready_check_state = "Accepted"
        self.ready_check["playerResponse"] = "Accepted"
        self.ready_check["state"] = "Accepted"
        logger.info("Mock ready check accepted successfully.")
        return True

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

    async def get_champ_select_session(self) -> ChampSelectSession | None:
        """Return current champion select session, or None if inactive."""
        if self._status != LcuConnectionStatus.CONNECTED:
            return None
        return self._champ_select_session

    async def patch_champ_select_action(
        self,
        action_id: int,
        champion_id: int,
        completed: bool = True,
    ) -> bool:
        """Perform hover or lock-in for a pick or ban action."""
        if self._status != LcuConnectionStatus.CONNECTED:
            logger.warning("patch_champ_select_action failed: adapter disconnected.")
            return False

        if self._champ_select_session is None:
            logger.warning("patch_champ_select_action failed: no active session.")
            return False

        # Find target action across all action groups
        target_action = None
        for group in self._champ_select_session.actions:
            for action in group:
                if action.id == action_id:
                    target_action = action
                    break
            if target_action is not None:
                break

        if target_action is None:
            logger.warning("patch_champ_select_action failed: action %s not found.", action_id)
            return False

        target_action.champion_id = champion_id
        if completed:
            target_action.completed = True
            target_action.is_in_progress = False

        # If action was a pick for local user, update player champion_id as well
        local_player = self._champ_select_session.get_local_player()
        if (
            local_player
            and target_action.actor_cell_id == local_player.cell_id
            and target_action.is_pick()
        ):
            local_player.champion_id = champion_id

        # Notify subscribers about session update
        await self._dispatch_champ_select(self._champ_select_session)
        return True

    async def get_grid_champions(self) -> list[ChampionSummary]:
        """Return available/grid champions."""
        if self._status != LcuConnectionStatus.CONNECTED:
            return []
        return list(self._grid_champions)

    def subscribe_champ_select(
        self, callback: Callable[[ChampSelectSession | None], Any]
    ) -> None:
        """Register a callback for champion select session state updates."""
        if callback not in self._champ_select_subscribers:
            self._champ_select_subscribers.append(callback)

    def unsubscribe_champ_select(
        self, callback: Callable[[ChampSelectSession | None], Any]
    ) -> bool:
        """Unregister a callback for champion select session updates."""
        if callback in self._champ_select_subscribers:
            self._champ_select_subscribers.remove(callback)
            return True
        return False

    async def _dispatch_champ_select(self, session: ChampSelectSession | None) -> None:
        """Dispatch session update to all registered champ select subscribers."""
        for cb in list(self._champ_select_subscribers):
            try:
                res = cb(session)
                if inspect.isawaitable(res):
                    await res
            except Exception as e:
                logger.warning("Error in champ select subscriber: %s", e)

    # ========================================================================
    # Test Simulation Helpers
    # ========================================================================

    async def simulate_connect(self) -> None:
        """Transition mock adapter to CONNECTED state."""
        self._status = LcuConnectionStatus.CONNECTED
        logger.info("Mock LCU connected.")

    async def simulate_disconnect(self) -> None:
        """Transition mock adapter to DISCONNECTED state and reset state."""
        self._status = LcuConnectionStatus.DISCONNECTED
        self._phase = GameflowPhase.NONE
        self.player_response = "None"
        self.ready_check_state = "Invalid"
        self.ready_check["playerResponse"] = "None"
        self.ready_check["state"] = "Invalid"
        self.ready_check["timer"] = 0.0
        self._champ_select_session = None
        await self._dispatch_champ_select(None)
        logger.info("Mock LCU disconnected.")

    async def simulate_phase_change(self, new_phase: GameflowPhase | str) -> None:
        """Update mock gameflow phase and notify all registered subscribers."""
        if isinstance(new_phase, str):
            new_phase = GameflowPhase(new_phase)

        self._phase = new_phase
        logger.info("Mock gameflow phase changed to: %s", new_phase.value)

        # Notify subscribers safely (one subscriber's failure shouldn't block others)
        for cb in list(self._subscribers):
            try:
                res = cb(new_phase)
                if inspect.isawaitable(res):
                    await res
            except Exception as e:
                logger.warning("Error in phase change subscriber: %s", e)

    async def simulate_ready_check(self, timer: float = 12.0) -> None:
        """Transition adapter into READY_CHECK phase with an active prompt."""
        self.player_response = "None"
        self.ready_check_state = "InProgress"
        self.ready_check = {
            "playerResponse": "None",
            "state": "InProgress",
            "timer": timer,
        }
        await self.simulate_phase_change(GameflowPhase.READY_CHECK)

    async def simulate_champ_select_session(
        self, session: ChampSelectSession | None
    ) -> None:
        """Simulate an active or ended champion select session."""
        self._champ_select_session = session
        if session is not None:
            await self.simulate_phase_change(GameflowPhase.CHAMP_SELECT)
        await self._dispatch_champ_select(session)

    def set_grid_champions(self, champions: list[ChampionSummary]) -> None:
        """Configure mock available grid champions."""
        self._grid_champions = list(champions)


__all__ = ["MockLcuAdapter"]
