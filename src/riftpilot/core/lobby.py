"""RiftPilot Core Domain — Lobby & Matchmaking Management.

Defines data models and business rules for:
- Party members and leader status
- Role/Position preferences (Top, Jungle, Mid, Bot, Support, Fill)
- Matchmaking search status, estimated wait times, and queue transitions
- ARAM bench and champion trade models
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class PositionPreference(str, Enum):
    """Summoner position preferences for draft and ranked matchmaking."""

    TOP = "TOP"
    JUNGLE = "JUNGLE"
    MIDDLE = "MIDDLE"
    BOTTOM = "BOTTOM"
    UTILITY = "UTILITY"
    FILL = "FILL"
    UNSELECTED = "UNSELECTED"

    @classmethod
    def from_str(cls, value: str | None) -> "PositionPreference":
        if not value:
            return cls.UNSELECTED
        normalized = value.strip().upper()
        if normalized in ("MID", "MIDDLE"):
            return cls.MIDDLE
        if normalized in ("BOT", "BOTTOM", "ADC"):
            return cls.BOTTOM
        if normalized in ("SUPPORT", "SUPP", "UTILITY"):
            return cls.UTILITY
        if normalized in ("TOP",):
            return cls.TOP
        if normalized in ("JUNGLE", "JUG", "JNG"):
            return cls.JUNGLE
        if normalized in ("FILL",):
            return cls.FILL
        return cls.UNSELECTED

    @property
    def display_name_tr(self) -> str:
        names = {
            self.TOP: "Üst Koridor",
            self.JUNGLE: "Orman",
            self.MIDDLE: "Orta Koridor",
            self.BOTTOM: "Alt Koridor",
            self.UTILITY: "Destek",
            self.FILL: "Doldur",
            self.UNSELECTED: "Seçilmedi",
        }
        return names.get(self, "Bilinmeyen")


class LobbyMember(BaseModel):
    """Represents a member inside the party lobby."""

    model_config = ConfigDict(frozen=True)

    summoner_id: int = Field(0, description="Riot summoner ID")
    summoner_name: str = Field("", description="Display summoner name")
    puuid: str = Field("", description="Unique Riot PUUID")
    is_leader: bool = Field(False, description="True if party lobby leader")
    is_local_member: bool = Field(False, description="True if represents the local player")
    first_position_preference: PositionPreference = Field(
        default=PositionPreference.UNSELECTED,
        description="First position preference",
    )
    second_position_preference: PositionPreference = Field(
        default=PositionPreference.UNSELECTED,
        description="Second position preference",
    )
    is_ready: bool = Field(True, description="Readiness status")

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> LobbyMember:
        return cls(
            summoner_id=int(data.get("summonerId", 0) or 0),
            summoner_name=str(data.get("summonerName", "") or data.get("gameName", "") or ""),
            puuid=str(data.get("puuid", "") or ""),
            is_leader=bool(data.get("isLeader", False)),
            is_local_member=bool(data.get("isLocalMember", False)),
            first_position_preference=PositionPreference.from_str(data.get("firstPositionPreference")),
            second_position_preference=PositionPreference.from_str(data.get("secondPositionPreference")),
            is_ready=bool(data.get("ready", True)),
        )


class MatchmakingSearchState(BaseModel):
    """Real-time matchmaking search state."""

    model_config = ConfigDict(frozen=True)

    is_in_queue: bool = Field(False, description="True if actively in matchmaking queue")
    search_state: str = Field("None", description="Raw search state string (Searching, Found, Error, etc.)")
    time_in_queue: float = Field(0.0, description="Elapsed seconds in search")
    estimated_delay: float = Field(0.0, description="Estimated wait time in seconds")
    errors: list[str] = Field(default_factory=list, description="Any queue error messages")

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> MatchmakingSearchState:
        raw_state = str(data.get("searchState", "None") or "None")
        is_queue = bool(data.get("isCurrentlyInQueue", False)) or (raw_state.lower() in ("searching", "found"))
        time_sec = float(data.get("timeInQueue", 0.0) or 0.0)
        delay_sec = float(data.get("estimatedDelay", 0.0) or 0.0)
        raw_errs = data.get("errors", [])
        errs: list[str] = []
        if isinstance(raw_errs, list):
            for e in raw_errs:
                if isinstance(e, dict):
                    errs.append(str(e.get("message", "")))
                elif isinstance(e, str):
                    errs.append(e)

        return cls(
            is_in_queue=is_queue,
            search_state=raw_state,
            time_in_queue=time_sec,
            estimated_delay=delay_sec,
            errors=errs,
        )


class LobbyData(BaseModel):
    """Complete lobby information extracted from LCU."""

    model_config = ConfigDict(frozen=True)

    queue_id: int = Field(0, description="Active queue ID (e.g. 420 for Solo/Duo, 450 for ARAM)")
    game_mode: str = Field("", description="Game mode string (e.g. CLASSIC, ARAM)")
    is_leader: bool = Field(False, description="True if local player is lobby owner")
    can_start_activity: bool = Field(False, description="True if matchmaking can be initiated")
    is_custom: bool = Field(False, description="True if custom game lobby")
    members: list[LobbyMember] = Field(default_factory=list, description="All members in lobby")

    @property
    def local_member(self) -> LobbyMember | None:
        for member in self.members:
            if member.is_local_member:
                return member
        return None

    @property
    def party_size(self) -> int:
        return len(self.members)

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> LobbyData | None:
        if not isinstance(data, dict):
            return None
        game_cfg = data.get("gameConfig", {})
        if not isinstance(game_cfg, dict):
            game_cfg = {}
        queue_id = int(game_cfg.get("queueId", 0) or 0)
        game_mode = str(game_cfg.get("gameMode", "") or "")
        is_custom = bool(game_cfg.get("isCustom", False))

        can_start = bool(data.get("canStartActivity", False))
        local_member_data = data.get("localMember", {})
        is_leader = False
        if isinstance(local_member_data, dict):
            is_leader = bool(local_member_data.get("isLeader", False))

        raw_members = data.get("members", [])
        members: list[LobbyMember] = []
        if isinstance(raw_members, list):
            for m in raw_members:
                if isinstance(m, dict):
                    member = LobbyMember.from_lcu_dict(m)
                    members.append(member)
                    if member.is_local_member and member.is_leader:
                        is_leader = True

        return cls(
            queue_id=queue_id,
            game_mode=game_mode,
            is_leader=is_leader,
            can_start_activity=can_start,
            is_custom=is_custom,
            members=members,
        )


class BenchChampion(BaseModel):
    """Champion currently available on the ARAM bench."""

    model_config = ConfigDict(frozen=True)

    champion_id: int = Field(..., description="Champion ID on the bench")
    champion_name: str = Field("", description="Champion display name")


class ChampSelectTrade(BaseModel):
    """Champion trade offer between teammates."""

    model_config = ConfigDict(frozen=True)

    trade_id: int = Field(..., description="Trade entity ID")
    cell_id: int = Field(..., description="Teammate cell ID")
    state: str = Field("AVAILABLE", description="AVAILABLE, SENT, RECEIVED, ACCEPTED, DECLINED, CANCELLED")

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> ChampSelectTrade | None:
        if not isinstance(data, dict):
            return None
        trade_id = data.get("id")
        cell_id = data.get("cellId")
        state = str(data.get("state", "AVAILABLE") or "AVAILABLE")
        if trade_id is None or cell_id is None:
            return None
        return cls(trade_id=int(trade_id), cell_id=int(cell_id), state=state)


__all__ = [
    "PositionPreference",
    "LobbyMember",
    "MatchmakingSearchState",
    "LobbyData",
    "BenchChampion",
    "ChampSelectTrade",
]
