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
    from riftpilot.core.game_mode import GameModeContext
    from riftpilot.core.runes_spells import RecommendedRunePage, RunePage, SummonerSpell


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
    # Game Mode & Champion Select Contract
    # ========================================================================

    @abstractmethod
    async def get_game_mode_context(self) -> GameModeContext:
        """Retrieve active game mode, queue ID, and map metadata from LCU."""
        ...

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

    # ========================================================================
    # Runes & Summoner Spells Contract
    # ========================================================================

    @abstractmethod
    async def get_available_summoner_spells(
        self, game_mode: str | None = None
    ) -> list[SummonerSpell]:
        """Return summoner spells valid for the given or active game mode."""
        ...

    @abstractmethod
    async def set_summoner_spells(self, spell1_id: int, spell2_id: int) -> bool:
        """Assign D and F summoner spells during champion select."""
        ...

    @abstractmethod
    async def get_current_rune_page(self) -> RunePage | None:
        """Retrieve the currently active rune page from LCU."""
        ...

    @abstractmethod
    async def apply_rune_page(self, page: RunePage) -> bool:
        """Create or update the active rune page in LCU."""
        ...

    @abstractmethod
    async def get_recommended_rune_pages(
        self,
        champion_id: int,
        position: str = "",
        map_id: int = 11,
    ) -> list[RecommendedRunePage]:
        """Fetch official recommended rune pages from LCU for the champion (no fake data)."""
        ...


__all__ = [
    "GameflowPhase",
    "ILcuPort",
    "LcuConnectionStatus",
]
