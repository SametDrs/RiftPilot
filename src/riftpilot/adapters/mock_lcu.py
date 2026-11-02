"""RiftPilot Adapters — Mock LCU Adapter.

Provides a deterministic test double for the League Client (LCU) outbound port.
Enforces Hexagonal Architecture (ADR-0003) contracts:
- Simulates gameflow phase transitions and event subscriptions.
- Simulates ready check flows.
- Simulates game mode contexts (Ranked, Blind, ARAM, ARAM Şamata, Unsupported).
- Simulates champion select sessions, actions (pick/ban), and champion grids.
- Simulates mode-filtered summoner spells and official LCU recommended rune pages.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

from riftpilot.core.champ_select import ChampSelectSession, ChampionSummary
from riftpilot.core.game_mode import GameModeContext
from riftpilot.core.lcu_port import GameflowPhase, ILcuPort, LcuConnectionStatus
from riftpilot.core.lobby import (
    BenchChampion,
    ChampSelectTrade,
    LobbyData,
    LobbyMember,
    MatchmakingSearchState,
    PositionPreference,
)
from riftpilot.core.runes_spells import (
    RecommendedRunePage,
    RunePage,
    SummonerSpell,
    get_spells_for_mode,
)

logger = logging.getLogger("RiftPilot.MockLCU")


def _build_default_mock_recommended_pages(champion_id: int) -> list[RecommendedRunePage]:
    """Build 3 realistic mock LCU recommended rune pages for deterministic testing."""
    pages_raw = [
        {
            "name": "Anlık Hasar (Elektrik Ver)",
            "primaryStyleId": 8100,
            "subStyleId": 8200,
            "selectedPerkIds": [8112, 8143, 8138, 8106, 8226, 8210, 5008, 5008, 5001],
            "recommendedChampionId": champion_id,
        },
        {
            "name": "Uzun Çatışma (Yenilmez)",
            "primaryStyleId": 8000,
            "subStyleId": 8400,
            "selectedPerkIds": [8010, 9111, 9104, 8299, 8444, 8451, 5005, 5008, 5002],
            "recommendedChampionId": champion_id,
        },
        {
            "name": "Dürtme & Koridor (Sihirli Yıldız)",
            "primaryStyleId": 8200,
            "subStyleId": 8300,
            "selectedPerkIds": [8229, 8226, 8210, 8237, 8304, 8345, 5008, 5008, 5003],
            "recommendedChampionId": champion_id,
        },
    ]
    result: list[RecommendedRunePage] = []
    for idx, item in enumerate(pages_raw):
        parsed = RecommendedRunePage.from_lcu_dict(item, champion_id=champion_id, index=idx)
        if parsed is not None:
            result.append(parsed)
    return result


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
        self._lobby_subscribers: list[Callable[[LobbyData | None], Any]] = []
        self._lobby_data: LobbyData | None = None
        self._matchmaking_state: MatchmakingSearchState | None = None
        self._bench_champions: list[BenchChampion] = [
            BenchChampion(champion_id=22, champion_name="Ashe"),
            BenchChampion(champion_id=1, champion_name="Annie"),
        ]
        self._trades: list[ChampSelectTrade] = []
        self.player_response: str = "None"
        self.ready_check_state: str = "Invalid"
        self.ready_check: dict[str, Any] = {
            "playerResponse": "None",
            "state": "Invalid",
            "timer": 0.0,
        }

        # Game mode & Champion select state attributes
        self._game_mode_context: GameModeContext = GameModeContext(
            game_mode="CLASSIC",
            queue_id=420,
            queue_name="Dereceli Tek/Çift",
            map_id=11,
        )
        self._champ_select_session: ChampSelectSession | None = None
        self._grid_champions: list[ChampionSummary] = []

        # Runes and summoner spells state
        self.spell1_id: int = 4   # Default Flash
        self.spell2_id: int = 14  # Default Ignite
        self.current_rune_page: RunePage | None = RunePage()
        # Optional override map: champion_id -> list[RecommendedRunePage]
        self._recommended_pages_by_champ: dict[int, list[RecommendedRunePage]] | None = None

    # ========================================================================
    # ILcuPort Implementation — Core Status & Phase
    # ========================================================================

    async def get_connection_status(self) -> LcuConnectionStatus:
        """Return the current mock connection status."""
        return self._status

    async def get_gameflow_phase(self) -> GameflowPhase:
        """Return the current mock gameflow phase."""
        return self._phase

    async def get_game_mode_context(self) -> GameModeContext:
        """Return the configured mock game mode context."""
        return self._game_mode_context

    async def accept_ready_check(self) -> bool:
        """Accept an active ready check if connected and in READY_CHECK phase."""
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

        target_action = None
        if action_id > 0:
            target_action = self._champ_select_session.find_action(action_id)
        if target_action is None:
            target_action = self._champ_select_session.get_active_action()

        if target_action is None:
            logger.warning("patch_champ_select_action failed: action %s not found.", action_id)
            return False

        target_action.champion_id = champion_id
        if completed:
            target_action.completed = True
            target_action.is_in_progress = False
            if target_action.is_ban() and champion_id > 0:
                if champion_id not in self._champ_select_session.bans.my_team_bans:
                    self._champ_select_session.bans.my_team_bans.append(champion_id)

        # If action was a pick for local user, update player champion_id / pick_intent
        local_player = self._champ_select_session.get_local_player()
        if (
            local_player
            and target_action.actor_cell_id == local_player.cell_id
            and target_action.is_pick()
        ):
            if completed:
                local_player.champion_id = champion_id
            else:
                local_player.champion_pick_intent = champion_id

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
    # ILcuPort Implementation — Runes & Summoner Spells
    # ========================================================================

    async def get_available_summoner_spells(
        self, game_mode: str | None = None
    ) -> list[SummonerSpell]:
        """Return summoner spells valid for the given or active mock game mode."""
        mode = game_mode or self._game_mode_context.game_mode
        return get_spells_for_mode(mode)

    async def set_summoner_spells(self, spell1_id: int, spell2_id: int) -> bool:
        """Assign D and F summoner spells if valid for the active game mode."""
        if self._status != LcuConnectionStatus.CONNECTED:
            return False

        valid_ids = {
            s.id
            for s in await self.get_available_summoner_spells(
                self._game_mode_context.game_mode
            )
        }
        if spell1_id not in valid_ids or spell2_id not in valid_ids or spell1_id == spell2_id:
            return False

        self.spell1_id = spell1_id
        self.spell2_id = spell2_id

        # Update active local player spells if session is open
        if self._champ_select_session is not None:
            local_p = self._champ_select_session.get_local_player()
            if local_p is not None:
                local_p.spell1_id = spell1_id
                local_p.spell2_id = spell2_id
            await self._dispatch_champ_select(self._champ_select_session)

        return True

    async def get_current_rune_page(self) -> RunePage | None:
        """Retrieve mock active rune page."""
        if self._status != LcuConnectionStatus.CONNECTED:
            return None
        return self.current_rune_page

    async def apply_rune_page(self, page: RunePage) -> bool:
        """Apply new active rune page."""
        if self._status != LcuConnectionStatus.CONNECTED:
            return False
        self.current_rune_page = page
        return True

    async def get_recommended_rune_pages(
        self,
        champion_id: int,
        position: str = "",
        map_id: int = 11,
    ) -> list[RecommendedRunePage]:
        """Retrieve mock LCU recommended rune pages (up to 3) for the given champion."""
        if self._status != LcuConnectionStatus.CONNECTED or champion_id <= 0:
            return []
        if self._recommended_pages_by_champ is not None:
            return list(self._recommended_pages_by_champ.get(champion_id, []))
        return _build_default_mock_recommended_pages(champion_id)


    # ========================================================================
    # Lobby & Matchmaking Implementation (Milestone 4)
    # ========================================================================

    async def get_lobby(self) -> LobbyData | None:
        if self._status != LcuConnectionStatus.CONNECTED:
            return None
        return self._lobby_data

    async def start_matchmaking(self) -> bool:
        if self._status != LcuConnectionStatus.CONNECTED or self._lobby_data is None:
            return False
        self._matchmaking_state = MatchmakingSearchState(
            is_in_queue=True,
            search_state="Searching",
            time_in_queue=1.0,
            estimated_delay=45.0,
        )
        await self.simulate_phase_change(GameflowPhase.MATCHMAKING)
        return True

    async def cancel_matchmaking(self) -> bool:
        if self._status != LcuConnectionStatus.CONNECTED:
            return False
        self._matchmaking_state = MatchmakingSearchState(
            is_in_queue=False,
            search_state="Canceled",
            time_in_queue=0.0,
        )
        await self.simulate_phase_change(GameflowPhase.LOBBY)
        return True

    async def set_position_preferences(self, first: str, second: str) -> bool:
        if self._status != LcuConnectionStatus.CONNECTED or self._lobby_data is None:
            return False
        pref1 = PositionPreference.from_str(first)
        pref2 = PositionPreference.from_str(second)
        updated_members: list[LobbyMember] = []
        for member in self._lobby_data.members:
            if member.is_local_member:
                updated_members.append(
                    LobbyMember(
                        summoner_id=member.summoner_id,
                        summoner_name=member.summoner_name,
                        puuid=member.puuid,
                        is_leader=member.is_leader,
                        is_local_member=True,
                        first_position_preference=pref1,
                        second_position_preference=pref2,
                        is_ready=member.is_ready,
                    )
                )
            else:
                updated_members.append(member)
        self._lobby_data = LobbyData(
            queue_id=self._lobby_data.queue_id,
            game_mode=self._lobby_data.game_mode,
            is_leader=self._lobby_data.is_leader,
            can_start_activity=self._lobby_data.can_start_activity,
            is_custom=self._lobby_data.is_custom,
            members=updated_members,
        )
        await self._dispatch_lobby(self._lobby_data)
        return True

    async def get_matchmaking_search(self) -> MatchmakingSearchState | None:
        if self._status != LcuConnectionStatus.CONNECTED:
            return None
        return self._matchmaking_state

    def subscribe_lobby(
        self, callback: Callable[[LobbyData | None], Any]
    ) -> None:
        if callback not in self._lobby_subscribers:
            self._lobby_subscribers.append(callback)

    def unsubscribe_lobby(
        self, callback: Callable[[LobbyData | None], Any]
    ) -> bool:
        if callback in self._lobby_subscribers:
            self._lobby_subscribers.remove(callback)
            return True
        return False

    async def _dispatch_lobby(self, lobby: LobbyData | None) -> None:
        for cb in list(self._lobby_subscribers):
            try:
                res = cb(lobby)
                if inspect.isawaitable(res):
                    await res
            except Exception as e:
                logger.warning("Error in lobby subscriber callback: %s", e)

    # ========================================================================
    # ARAM Bench & Trades Implementation (Milestone 4)
    # ========================================================================

    async def bench_swap(self, champion_id: int) -> bool:
        if self._status != LcuConnectionStatus.CONNECTED:
            return False
        bench_match = next((b for b in self._bench_champions if b.champion_id == champion_id), None)
        if not bench_match:
            return False
        self._bench_champions = [b for b in self._bench_champions if b.champion_id != champion_id]
        if self._champ_select_session:
            local_p = self._champ_select_session.get_local_player()
            if local_p:
                old_id = local_p.champion_id or local_p.champion_pick_intent or 157
                self._bench_champions.append(BenchChampion(champion_id=old_id, champion_name=f"Champion #{old_id}"))
                local_p.champion_id = champion_id
                local_p.champion_pick_intent = champion_id
                await self._dispatch_champ_select(self._champ_select_session)
        return True

    async def reroll(self) -> bool:
        if self._status != LcuConnectionStatus.CONNECTED:
            return False
        return True

    async def get_bench_champions(self) -> list[BenchChampion]:
        if self._status != LcuConnectionStatus.CONNECTED:
            return []
        return list(self._bench_champions)

    async def get_trades(self) -> list[ChampSelectTrade]:
        if self._status != LcuConnectionStatus.CONNECTED:
            return []
        return list(self._trades)

    async def request_trade(self, trade_id: int) -> bool:
        if self._status != LcuConnectionStatus.CONNECTED:
            return False
        return True

    async def accept_trade(self, trade_id: int) -> bool:
        if self._status != LcuConnectionStatus.CONNECTED:
            return False
        return True

    async def decline_trade(self, trade_id: int) -> bool:
        if self._status != LcuConnectionStatus.CONNECTED:
            return False
        return True

    async def simulate_lobby(self, lobby: LobbyData | None) -> None:
        self._lobby_data = lobby
        if lobby is not None and self._phase in (GameflowPhase.NONE, GameflowPhase.MATCHMAKING):
            await self.simulate_phase_change(GameflowPhase.LOBBY)
        await self._dispatch_lobby(lobby)

    async def simulate_matchmaking(self, search: MatchmakingSearchState | None) -> None:
        self._matchmaking_state = search
        if search and search.is_in_queue:
            await self.simulate_phase_change(GameflowPhase.MATCHMAKING)

    def set_bench_champions(self, bench: list[BenchChampion]) -> None:
        self._bench_champions = list(bench)

    def set_trades(self, trades: list[ChampSelectTrade]) -> None:
        self._trades = list(trades)

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
        self.current_rune_page = None
        await self._dispatch_champ_select(None)
        logger.info("Mock LCU disconnected.")

    async def simulate_phase_change(self, new_phase: GameflowPhase | str) -> None:
        """Update mock gameflow phase and notify all registered subscribers."""
        if isinstance(new_phase, str):
            new_phase = GameflowPhase(new_phase)

        self._phase = new_phase
        logger.info("Mock gameflow phase changed to: %s", new_phase.value)

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

    def set_game_mode_context(self, context: GameModeContext) -> None:
        """Configure mock active game mode context (e.g., ARAM, Blind, Ranked, Unknown)."""
        self._game_mode_context = context

    def set_recommended_rune_pages(
        self, champion_id: int, pages: list[RecommendedRunePage]
    ) -> None:
        """Configure explicit mock recommended rune pages for a champion (can be empty)."""
        if self._recommended_pages_by_champ is None:
            self._recommended_pages_by_champ = {}
        self._recommended_pages_by_champ[champion_id] = list(pages)

    def set_grid_champions(self, champions: list[ChampionSummary]) -> None:
        """Configure mock available grid champions."""
        self._grid_champions = list(champions)


__all__ = ["MockLcuAdapter"]
