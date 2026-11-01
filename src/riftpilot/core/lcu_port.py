"""RiftPilot Core Domain — Outbound LCU Port Interface and Models.

Defines the contract for League Client (LCU) communication in accordance
with Hexagonal Architecture (ADR-0003):
- `GameflowPhase`: Official Riot League Client game flow phases.
- `LcuConnectionStatus`: Connection state of the LCU adapter.
- `ILcuPort`: Outbound port abstract base class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from riftpilot.core.champ_select import ChampSelectSession, ChampionSummary


class GameflowPhase(str, Enum):
    """Official League of Legends client gameflow lifecycle phases."""

    NONE = "None"
    LOBBY = "Lobby"
    MATCHMAKING = "Matchmaking"
    READY_CHECK = "ReadyCheck"
    CHAMP_SELECT = "ChampSelect"
    IN_PROGRESS = "InProgress"
    WAITING_FOR_STATS = "WaitingForStats"
    PRE_END_OF_GAME = "PreEndOfGame"
    END_OF_GAME = "EndOfGame"

    # Alias attributes to allow case-insensitive access if accessed dynamically
    None_ = "None"
    Lobby = "Lobby"
    Matchmaking = "Matchmaking"
    ReadyCheck = "ReadyCheck"
    ChampSelect = "ChampSelect"
    InProgress = "InProgress"
    WaitingForStats = "WaitingForStats"
    PreEndOfGame = "PreEndOfGame"
    EndOfGame = "EndOfGame"


class LcuConnectionStatus(str, Enum):
    """Connection states for LCU adapters."""

    DISCONNECTED = "Disconnected"
    CONNECTING = "Connecting"
    CONNECTED = "Connected"
    AUTHENTICATION_FAILED = "AuthenticationFailed"


class ILcuPort(ABC):
    """Abstract outbound port contract for League Client (LCU) adapters."""

    @abstractmethod
    async def get_connection_status(self) -> LcuConnectionStatus:
        """Return the current connection status of the LCU interface."""
        ...

    @abstractmethod
    async def get_gameflow_phase(self) -> GameflowPhase:
        """Return the active gameflow phase of the League Client."""
        ...

    @abstractmethod
    async def accept_ready_check(self) -> bool:
        """Accept an active ready check (match found) prompt."""
        ...

    @abstractmethod
    def subscribe_phase_change(
        self, callback: Callable[[GameflowPhase], Any]
    ) -> None:
        """Register a callback for gameflow phase transition events."""
        ...

    @abstractmethod
    def unsubscribe_phase_change(
        self, callback: Callable[[GameflowPhase], Any]
    ) -> bool:
        """Unregister an existing phase change callback."""
        ...

    # ========================================================================
    # Champion Select Contract
    # ========================================================================

    @abstractmethod
    async def get_champ_select_session(self) -> ChampSelectSession | None:
        """Return current champion select session state, or None if inactive."""
        ...

    @abstractmethod
    async def patch_champ_select_action(
        self,
        action_id: int,
        champion_id: int,
        completed: bool = True,
    ) -> bool:
        """Perform hover or lock-in for a pick or ban action."""
        ...

    @abstractmethod
    async def get_grid_champions(self) -> list[ChampionSummary]:
        """Retrieve the list of selectable/bannable grid champions."""
        ...

    @abstractmethod
    def subscribe_champ_select(
        self, callback: Callable[[ChampSelectSession | None], Any]
    ) -> None:
        """Register a callback for champion select session state updates."""
        ...

    @abstractmethod
    def unsubscribe_champ_select(
        self, callback: Callable[[ChampSelectSession | None], Any]
    ) -> bool:
        """Unregister a callback for champion select session state updates."""
        ...


__all__ = [
    "GameflowPhase",
    "ILcuPort",
    "LcuConnectionStatus",
]
