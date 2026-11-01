"""RiftPilot Adapters — Mock LCU Adapter.

Provides a deterministic test double for the League Client (LCU) outbound port.
Enables offline and automated CI/CD testing of match acceptance, phase transitions,
and error handling without requiring a live League of Legends client.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

from riftpilot.core.lcu_port import GameflowPhase, ILcuPort, LcuConnectionStatus

logger = logging.getLogger("RiftPilot.MockLCU")


class MockLcuAdapter(ILcuPort):
    """Deterministic mock adapter implementing `ILcuPort` for testing."""

    def __init__(self) -> None:
        """Initialize mock LCU adapter in a disconnected state."""
        self._status = LcuConnectionStatus.DISCONNECTED
        self._phase = GameflowPhase.NONE
        self._subscribers: list[Callable[[GameflowPhase], Any]] = []

        # Ready check state attributes
        self.player_response: str = "None"
        self.ready_check_state: str = "Invalid"
        self.ready_check: dict[str, Any] = {
            "playerResponse": "None",
            "state": "Invalid",
            "timer": 0.0,
        }

    # ========================================================================
    # ILcuPort Implementation
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
    # Test Simulation Helpers
    # ========================================================================

    async def simulate_connect(self) -> None:
        """Transition mock adapter to CONNECTED state."""
        self._status = LcuConnectionStatus.CONNECTED
        logger.info("Mock LCU connected.")

    async def simulate_disconnect(self) -> None:
        """Transition mock adapter to DISCONNECTED state and reset phase/ready check."""
        self._status = LcuConnectionStatus.DISCONNECTED
        self._phase = GameflowPhase.NONE
        self.player_response = "None"
        self.ready_check_state = "Invalid"
        self.ready_check["playerResponse"] = "None"
        self.ready_check["state"] = "Invalid"
        self.ready_check["timer"] = 0.0
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


__all__ = ["MockLcuAdapter"]
