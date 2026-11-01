"""RiftPilot Core Domain — Champion Select Domain Models and Contracts.

Defines data models representing League of Legends champion select state
in accordance with Hexagonal Architecture (ADR-0003):
- `ChampSelectActionType`: Enumeration of champ select action kinds (pick/ban/vote).
- `ChampSelectAction`: Individual pick or ban action slot.
- `ChampSelectPlayer`: Team member in the champion select lobby.
- `ChampSelectTimer`: Current timer and phase information.
- `ChampSelectBans`: Current banned champion lists.
- `ChampSelectSession`: Complete snapshot of an active champion select session.
- `ChampionSummary`: Champion metadata and availability for grid display.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ChampSelectActionType(str, Enum):
    """Types of actions available during Champion Select."""

    PICK = "pick"
    BAN = "ban"
    VOTE = "vote"


class ChampSelectPhase(str, Enum):
    """Champion select phase identifier."""

    PLANNING = "PLANNING"
    BAN_PICK = "BAN_PICK"
    FINALIZATION = "FINALIZATION"
    GAME_STARTING = "GAME_STARTING"
    UNKNOWN = "UNKNOWN"


@dataclass
class ChampSelectAction:
    """Represents a pick or ban action slot in Champion Select."""

    id: int
    actor_cell_id: int
    champion_id: int = 0
    type: str = "pick"
    completed: bool = False
    is_in_progress: bool = False
    pick_turn: int = 1

    def is_pick(self) -> bool:
        """Check if action is a champion pick."""
        return self.type.lower() == ChampSelectActionType.PICK.value

    def is_ban(self) -> bool:
        """Check if action is a champion ban."""
        return self.type.lower() == ChampSelectActionType.BAN.value

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> ChampSelectAction:
        """Parse from raw LCU action dictionary."""
        return cls(
            id=int(data.get("id", 0)),
            actor_cell_id=int(data.get("actorCellId", -1)),
            champion_id=int(data.get("championId", 0)),
            type=str(data.get("type", "pick")).lower(),
            completed=bool(data.get("completed", False)),
            is_in_progress=bool(data.get("isInProgress", False)),
            pick_turn=int(data.get("pickTurn", 1)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize action to dictionary with both camelCase and snake_case."""
        return {
            "id": self.id,
            "actorCellId": self.actor_cell_id,
            "actor_cell_id": self.actor_cell_id,
            "championId": self.champion_id,
            "champion_id": self.champion_id,
            "type": self.type,
            "completed": self.completed,
            "isInProgress": self.is_in_progress,
            "is_in_progress": self.is_in_progress,
            "pickTurn": self.pick_turn,
            "pick_turn": self.pick_turn,
        }


@dataclass
class ChampSelectPlayer:
    """Represents a summoner on a team in Champion Select."""

    cell_id: int
    champion_id: int = 0
    champion_pick_intent: int = 0
    assigned_position: str = ""
    spell1_id: int = 0
    spell2_id: int = 0
    summoner_id: int = 0
    team: int = 1

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> ChampSelectPlayer:
        """Parse from raw LCU team member dictionary."""
        return cls(
            cell_id=int(data.get("cellId", -1)),
            champion_id=int(data.get("championId", 0)),
            champion_pick_intent=int(data.get("championPickIntent", 0)),
            assigned_position=str(data.get("assignedPosition", "")),
            spell1_id=int(data.get("spell1Id", 0)),
            spell2_id=int(data.get("spell2Id", 0)),
            summoner_id=int(data.get("summonerId", 0)),
            team=int(data.get("team", 1)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize player to dictionary with both camelCase and snake_case."""
        return {
            "cellId": self.cell_id,
            "cell_id": self.cell_id,
            "championId": self.champion_id,
            "champion_id": self.champion_id,
            "championPickIntent": self.champion_pick_intent,
            "champion_pick_intent": self.champion_pick_intent,
            "assignedPosition": self.assigned_position,
            "assigned_position": self.assigned_position,
            "spell1Id": self.spell1_id,
            "spell1_id": self.spell1_id,
            "spell2Id": self.spell2_id,
            "spell2_id": self.spell2_id,
            "summonerId": self.summoner_id,
            "summoner_id": self.summoner_id,
            "team": self.team,
        }


@dataclass
class ChampSelectTimer:
    """Represents active phase timer information."""

    phase: str = "PLANNING"
    time_left_in_phase: int = 0
    total_time_in_phase: int = 0
    adjusted_time_left_in_phase: int = 0

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> ChampSelectTimer:
        """Parse from raw LCU timer dictionary."""
        return cls(
            phase=str(data.get("phase", "PLANNING")),
            time_left_in_phase=int(data.get("timeLeftInPhase", 0)),
            total_time_in_phase=int(data.get("totalTimeInPhase", 0)),
            adjusted_time_left_in_phase=int(data.get("adjustedTimeLeftInPhase", 0)),
        )

    def time_left_seconds(self) -> float:
        """Return the remaining phase time in seconds."""
        time_ms = self.adjusted_time_left_in_phase or self.time_left_in_phase
        return max(0.0, time_ms / 1000.0)

    def to_dict(self) -> dict[str, Any]:
        """Serialize timer to dictionary with both camelCase and snake_case."""
        seconds = self.time_left_seconds()
        return {
            "phase": self.phase,
            "timeLeftSeconds": seconds,
            "time_left_seconds": seconds,
            "timeLeftInPhase": self.time_left_in_phase,
            "time_left_in_phase": self.time_left_in_phase,
            "totalTimeInPhase": self.total_time_in_phase,
            "total_time_in_phase": self.total_time_in_phase,
            "adjustedTimeLeftInPhase": self.adjusted_time_left_in_phase,
            "adjusted_time_left_in_phase": self.adjusted_time_left_in_phase,
        }


@dataclass
class ChampSelectBans:
    """Represents current picks/bans information."""

    my_team_bans: list[int] = field(default_factory=list)
    their_team_bans: list[int] = field(default_factory=list)
    num_bans: int = 0

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> ChampSelectBans:
        """Parse from raw LCU bans dictionary."""
        return cls(
            my_team_bans=[int(x) for x in data.get("myTeamBans", [])],
            their_team_bans=[int(x) for x in data.get("theirTeamBans", [])],
            num_bans=int(data.get("numBans", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize bans to dictionary with both camelCase and snake_case."""
        return {
            "myTeamBans": list(self.my_team_bans),
            "my_team_bans": list(self.my_team_bans),
            "theirTeamBans": list(self.their_team_bans),
            "their_team_bans": list(self.their_team_bans),
            "numBans": self.num_bans,
            "num_bans": self.num_bans,
        }


@dataclass
class ChampionSummary:
    """Metadata for a selectable or bannable champion."""

    id: int
    name: str
    roles: list[str] = field(default_factory=list)
    square_portrait_path: str = ""
    disabled: bool = False

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> ChampionSummary:
        """Parse from raw LCU champion summary dictionary."""
        return cls(
            id=int(data.get("id", 0)),
            name=str(data.get("name", "")),
            roles=[str(r).lower() for r in data.get("roles", [])],
            square_portrait_path=str(data.get("squarePortraitPath", "")),
            disabled=bool(data.get("disabled", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize champion summary to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "roles": list(self.roles),
            "squarePortraitPath": self.square_portrait_path,
            "square_portrait_path": self.square_portrait_path,
            "disabled": self.disabled,
        }


@dataclass
class ChampSelectSession:
    """Represents a complete snapshot of an active Champion Select session."""

    local_player_cell_id: int = -1
    my_team: list[ChampSelectPlayer] = field(default_factory=list)
    their_team: list[ChampSelectPlayer] = field(default_factory=list)
    actions: list[list[ChampSelectAction]] = field(default_factory=list)
    timer: ChampSelectTimer = field(default_factory=ChampSelectTimer)
    bans: ChampSelectBans = field(default_factory=ChampSelectBans)
    is_spectating: bool = False
    bench_champions: list[dict[str, Any]] = field(default_factory=list)
    bench_enabled: bool = False
    rerolls_remaining: int = 0
    has_simultaneous_bans: bool = False
    has_simultaneous_picks: bool = False
    is_custom_game: bool = False

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> ChampSelectSession:
        """Parse from raw LCU champion select session dictionary."""
        # Parse actions
        raw_actions = data.get("actions", [])
        actions: list[list[ChampSelectAction]] = []
        for group in raw_actions:
            if isinstance(group, list):
                actions.append([ChampSelectAction.from_lcu_dict(item) for item in group])

        # Parse teams
        my_team = [
            ChampSelectPlayer.from_lcu_dict(p)
            for p in data.get("myTeam", [])
        ]
        their_team = [
            ChampSelectPlayer.from_lcu_dict(p)
            for p in data.get("theirTeam", [])
        ]

        # Parse timer & bans
        timer_dict = data.get("timer") or {}
        timer = ChampSelectTimer.from_lcu_dict(timer_dict)

        bans_dict = data.get("bans") or {}
        bans = ChampSelectBans.from_lcu_dict(bans_dict)

        return cls(
            local_player_cell_id=int(data.get("localPlayerCellId", -1)),
            my_team=my_team,
            their_team=their_team,
            actions=actions,
            timer=timer,
            bans=bans,
            is_spectating=bool(data.get("isSpectating", False)),
            bench_champions=[
                b for b in (data.get("benchChampions") or []) if isinstance(b, dict)
            ],
            bench_enabled=bool(data.get("benchEnabled", False)),
            rerolls_remaining=int(data.get("rerollsRemaining", 0) or 0),
            has_simultaneous_bans=bool(data.get("hasSimultaneousBans", False)),
            has_simultaneous_picks=bool(data.get("hasSimultaneousPicks", False)),
            is_custom_game=bool(data.get("isCustomGame", False)),
        )

    def get_local_player(self) -> ChampSelectPlayer | None:
        """Retrieve the local user's player profile in my_team."""
        for player in self.my_team:
            if player.cell_id == self.local_player_cell_id:
                return player
        return None

    def find_action(self, action_id: int) -> ChampSelectAction | None:
        """Find an action by unique action ID."""
        for group in self.actions:
            for action in group:
                if action.id == action_id:
                    return action
        return None

    def get_active_action(self) -> ChampSelectAction | None:
        """Find the currently active or pending action for the local user.

        Priority 1: Action currently in progress for the local user (ban or pick).
        Priority 2: The first uncompleted action for the local user (allows hovering before turn).
        """
        if self.local_player_cell_id < 0:
            return None

        # Priority 1: In progress right now
        for group in self.actions:
            for action in group:
                if (
                    action.actor_cell_id == self.local_player_cell_id
                    and action.is_in_progress
                    and not action.completed
                ):
                    return action

        # Priority 2: Uncompleted action for local user
        for group in self.actions:
            for action in group:
                if (
                    action.actor_cell_id == self.local_player_cell_id
                    and not action.completed
                ):
                    return action

        return None

    def iter_actions(self) -> list[ChampSelectAction]:
        """Return all actions flattened in turn order."""
        return [action for group in self.actions for action in group]

    def has_ban_actions(self) -> bool:
        """Return True if the client defined at least one ban action (ban phase exists)."""
        return any(action.is_ban() for action in self.iter_actions())

    def has_pick_actions(self) -> bool:
        """Return True if the client defined at least one pick action."""
        return any(action.is_pick() for action in self.iter_actions())

    def get_local_actions(self, action_type: str | None = None) -> list[ChampSelectAction]:
        """Return all actions owned by the local player, optionally filtered by type."""
        if self.local_player_cell_id < 0:
            return []
        return [
            action
            for action in self.iter_actions()
            if action.actor_cell_id == self.local_player_cell_id
            and (action_type is None or action.type == action_type)
        ]

    def get_in_progress_local_action(self) -> ChampSelectAction | None:
        """Return the local player's action that is currently in progress (their turn)."""
        for action in self.get_local_actions():
            if action.is_in_progress and not action.completed:
                return action
        return None

    def get_unavailable_pick_ids(self) -> set[int]:
        """Return champion IDs already locked in by other players (cannot be picked)."""
        return {
            action.champion_id
            for action in self.iter_actions()
            if action.is_pick()
            and action.completed
            and action.champion_id > 0
            and action.actor_cell_id != self.local_player_cell_id
        }

    def get_bench_champion_ids(self) -> list[int]:
        """Return champion IDs currently on the shared bench (ARAM-like modes)."""
        ids: list[int] = []
        for entry in self.bench_champions:
            try:
                champ_id = int(entry.get("championId", 0))
            except (TypeError, ValueError):
                continue
            if champ_id > 0:
                ids.append(champ_id)
        return ids

    def get_all_banned_ids(self) -> set[int]:
        """Return the combined set of all banned champion IDs."""
        banned = set(self.bans.my_team_bans + self.bans.their_team_bans)
        for group in self.actions:
            for action in group:
                if action.is_ban() and action.completed and action.champion_id > 0:
                    banned.add(action.champion_id)
        return banned

    def to_dict(self) -> dict[str, Any]:
        """Serialize the entire session state to a dictionary for JSON/WS transport."""
        active_action = self.get_active_action()
        local_player = self.get_local_player()

        return {
            "localPlayerCellId": self.local_player_cell_id,
            "local_player_cell_id": self.local_player_cell_id,
            "myTeam": [p.to_dict() for p in self.my_team],
            "theirTeam": [p.to_dict() for p in self.their_team],
            "actions": [[a.to_dict() for a in group] for group in self.actions],
            "timer": self.timer.to_dict(),
            "bans": self.bans.to_dict(),
            "isSpectating": self.is_spectating,
            "activeAction": active_action.to_dict() if active_action else None,
            "active_action": active_action.to_dict() if active_action else None,
            "localPlayer": local_player.to_dict() if local_player else None,
            "local_player": local_player.to_dict() if local_player else None,
            "allBannedIds": sorted(self.get_all_banned_ids()),
            "unavailablePickIds": sorted(self.get_unavailable_pick_ids()),
            "benchEnabled": self.bench_enabled,
            "benchChampionIds": self.get_bench_champion_ids(),
            "rerollsRemaining": self.rerolls_remaining,
        }


__all__ = [
    "ChampSelectAction",
    "ChampSelectActionType",
    "ChampSelectBans",
    "ChampSelectPhase",
    "ChampSelectPlayer",
    "ChampSelectSession",
    "ChampSelectTimer",
    "ChampionSummary",
]
