"""RiftPilot Core Domain — Post-Game Stats, Live Game & Honor Teammates.

Defines domain models and LCU parsers for:
- Live match state and game timer
- End of Game (EOG) scoreboards, team stats, victory/defeat status
- Player KDA, damage breakdown, items, gold and CS
- Teammate honor voting ballot and categories
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class HonorCategory(str, Enum):
    """Riot official teammate honor categories."""

    HEART = "HEART"               # Stayed cool / Heart
    SHOTCALLER = "SHOTCALLER"     # Great shotcaller / Leadership
    COOL = "COOL"                 # Cool under pressure / Tiltsafe

    @property
    def title_tr(self) -> str:
        names = {
            self.HEART: "❤️ İyi Kalpli",
            self.SHOTCALLER: "🧠 Harika Lider",
            self.COOL: "❄️ Sakin Kaldı",
        }
        return names.get(self, "Onurlandır")


class PlayerGameStats(BaseModel):
    """Individual player match statistics."""

    model_config = ConfigDict(frozen=True)

    summoner_id: int = Field(0, description="Riot summoner ID")
    summoner_name: str = Field("", description="Display summoner name")
    puuid: str = Field("", description="Player PUUID")
    champion_id: int = Field(0, description="Champion ID")
    champion_name: str = Field("", description="Champion display name")
    team_id: int = Field(100, description="Team ID: 100 for Blue, 200 for Red")
    is_local_player: bool = Field(False, description="True if local summoner")
    kills: int = Field(0, description="Total kills")
    deaths: int = Field(0, description="Total deaths")
    assists: int = Field(0, description="Total assists")
    damage_dealt: int = Field(0, description="Total damage dealt to champions")
    gold_earned: int = Field(0, description="Total gold earned")
    minions_killed: int = Field(0, description="CS: minions & monsters killed")
    items: list[int] = Field(default_factory=list, description="Item IDs (up to 7 items)")
    spell1_id: int = Field(4, description="Primary summoner spell")
    spell2_id: int = Field(12, description="Secondary summoner spell")
    is_mvp: bool = Field(False, description="True if highest score / impact in team")

    @property
    def kda_formatted(self) -> str:
        return f"{self.kills}/{self.deaths}/{self.assists}"

    @property
    def kda_ratio(self) -> float:
        if self.deaths == 0:
            return float(self.kills + self.assists)
        return round((self.kills + self.assists) / self.deaths, 2)


class EndOfGameStats(BaseModel):
    """Aggregated match outcome and player scoreboard from LCU."""

    model_config = ConfigDict(frozen=True)

    game_id: int = Field(0, description="Game match ID")
    game_mode: str = Field("CLASSIC", description="Game mode string")
    game_length_seconds: int = Field(0, description="Match duration in seconds")
    is_victory: bool = Field(True, description="True if local player won the match")
    local_team_id: int = Field(100, description="Local summoner team ID")
    players: list[PlayerGameStats] = Field(default_factory=list, description="All 10 player stats")

    @property
    def duration_formatted(self) -> str:
        minutes = self.game_length_seconds // 60
        seconds = self.game_length_seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

    @property
    def result_title_tr(self) -> str:
        return "ZAFER" if self.is_victory else "BOZGUN"

    @property
    def local_player(self) -> PlayerGameStats | None:
        for p in self.players:
            if p.is_local_player:
                return p
        return None

    @property
    def blue_team(self) -> list[PlayerGameStats]:
        return [p for p in self.players if p.team_id == 100]

    @property
    def red_team(self) -> list[PlayerGameStats]:
        return [p for p in self.players if p.team_id == 200]

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> EndOfGameStats | None:
        if not isinstance(data, dict):
            return None

        game_id = int(data.get("gameId", 0) or 0)
        game_mode = str(data.get("gameMode", "CLASSIC") or "CLASSIC")
        game_length = int(data.get("gameLength", 0) or 0)

        # Detect local team and victory
        local_team_id = 100
        is_victory = False

        raw_teams = data.get("teams", [])
        if isinstance(raw_teams, list):
            for t in raw_teams:
                if isinstance(t, dict):
                    if t.get("isPlayerTeam", False):
                        local_team_id = int(t.get("teamId", 100) or 100)
                        is_victory = bool(t.get("isWinningTeam", False))

        # Extract players from teams or global players list
        players: list[PlayerGameStats] = []
        raw_players = []
        if isinstance(raw_teams, list):
            for t in raw_teams:
                if isinstance(t, dict):
                    t_id = int(t.get("teamId", 100) or 100)
                    for p in t.get("players", []):
                        if isinstance(p, dict):
                            p["teamId"] = t_id
                            raw_players.append(p)

        if not raw_players and isinstance(data.get("players"), list):
            raw_players = data.get("players", [])

        highest_dmg = 0
        for p_data in raw_players:
            if not isinstance(p_data, dict):
                continue
            stats = p_data.get("stats", {})
            if not isinstance(stats, dict):
                stats = {}

            dmg = int(stats.get("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS", 0) or stats.get("totalDamageDealtToChampions", 0) or 0)
            if dmg > highest_dmg:
                highest_dmg = dmg

            items: list[int] = []
            for i in range(7):
                item_id = int(stats.get(f"ITEM{i}", 0) or stats.get(f"item{i}", 0) or 0)
                if item_id > 0:
                    items.append(item_id)

            is_local = bool(p_data.get("isLocalPlayer", False))
            if is_local:
                local_team_id = int(p_data.get("teamId", local_team_id) or local_team_id)

            player_stat = PlayerGameStats(
                summoner_id=int(p_data.get("summonerId", 0) or 0),
                summoner_name=str(p_data.get("summonerName", "") or p_data.get("gameName", "") or ""),
                puuid=str(p_data.get("puuid", "") or ""),
                champion_id=int(p_data.get("championId", 0) or 0),
                champion_name=str(p_data.get("championName", "") or ""),
                team_id=int(p_data.get("teamId", 100) or 100),
                is_local_player=is_local,
                kills=int(stats.get("CHAMPIONS_KILLED", 0) or stats.get("championsKilled", 0) or 0),
                deaths=int(stats.get("NUM_DEATHS", 0) or stats.get("numDeaths", 0) or 0),
                assists=int(stats.get("ASSISTS", 0) or stats.get("assists", 0) or 0),
                damage_dealt=dmg,
                gold_earned=int(stats.get("GOLD_EARNED", 0) or stats.get("goldEarned", 0) or 0),
                minions_killed=int(stats.get("MINIONS_KILLED", 0) or stats.get("minionsKilled", 0) or 0),
                items=items,
                spell1_id=int(p_data.get("spell1Id", 4) or 4),
                spell2_id=int(p_data.get("spell2Id", 12) or 12),
                is_mvp=False,
            )
            players.append(player_stat)

        # Mark MVP (highest damage in winning team)
        final_players: list[PlayerGameStats] = []
        for p in players:
            if highest_dmg > 0 and p.damage_dealt == highest_dmg:
                final_players.append(
                    PlayerGameStats(
                        summoner_id=p.summoner_id,
                        summoner_name=p.summoner_name,
                        puuid=p.puuid,
                        champion_id=p.champion_id,
                        champion_name=p.champion_name,
                        team_id=p.team_id,
                        is_local_player=p.is_local_player,
                        kills=p.kills,
                        deaths=p.deaths,
                        assists=p.assists,
                        damage_dealt=p.damage_dealt,
                        gold_earned=p.gold_earned,
                        minions_killed=p.minions_killed,
                        items=p.items,
                        spell1_id=p.spell1_id,
                        spell2_id=p.spell2_id,
                        is_mvp=True,
                    )
                )
            else:
                final_players.append(p)

        return cls(
            game_id=game_id,
            game_mode=game_mode,
            game_length_seconds=game_length,
            is_victory=is_victory,
            local_team_id=local_team_id,
            players=final_players,
        )


class HonorCandidate(BaseModel):
    """Teammate eligible to receive an honor vote."""

    model_config = ConfigDict(frozen=True)

    summoner_id: int = Field(..., description="Summoner ID to honor")
    summoner_name: str = Field("", description="Summoner display name")
    champion_id: int = Field(0, description="Champion played")
    champion_name: str = Field("", description="Champion name")
    skin_name: str = Field("", description="Skin name used")
    role: str = Field("", description="Role in game")


class HonorBallot(BaseModel):
    """Eligible teammates ballot for post-game honoring."""

    model_config = ConfigDict(frozen=True)

    game_id: int = Field(0, description="Active game match ID")
    candidates: list[HonorCandidate] = Field(default_factory=list, description="Eligible teammates")

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> HonorBallot | None:
        if not isinstance(data, dict):
            return None
        game_id = int(data.get("gameId", 0) or 0)
        raw_candidates = data.get("eligiblePlayers", [])
        candidates: list[HonorCandidate] = []
        if isinstance(raw_candidates, list):
            for c in raw_candidates:
                if isinstance(c, dict):
                    candidates.append(
                        HonorCandidate(
                            summoner_id=int(c.get("summonerId", 0) or 0),
                            summoner_name=str(c.get("summonerName", "") or c.get("gameName", "") or ""),
                            champion_id=int(c.get("championId", 0) or 0),
                            champion_name=str(c.get("championName", "") or ""),
                            skin_name=str(c.get("skinName", "") or ""),
                            role=str(c.get("role", "") or ""),
                        )
                    )
        return cls(game_id=game_id, candidates=candidates)


__all__ = [
    "HonorCategory",
    "PlayerGameStats",
    "EndOfGameStats",
    "HonorCandidate",
    "HonorBallot",
]
