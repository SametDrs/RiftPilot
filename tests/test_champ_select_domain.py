"""RiftPilot Unit Tests — Champion Select Domain Models and Parsing.

Verifies ChampSelectAction, ChampSelectPlayer, ChampSelectTimer,
ChampSelectBans, ChampionSummary, and ChampSelectSession according to
Hexagonal Architecture specifications.
"""

from __future__ import annotations

import pytest

from riftpilot.core.champ_select import (
    ChampSelectAction,
    ChampSelectActionType,
    ChampSelectBans,
    ChampSelectPhase,
    ChampSelectPlayer,
    ChampSelectSession,
    ChampSelectTimer,
    ChampionSummary,
)


def test_champ_select_action_pick() -> None:
    """Verify action parsing for a pick action."""
    raw = {
        "id": 12,
        "actorCellId": 3,
        "championId": 103,
        "type": "pick",
        "completed": False,
        "isInProgress": True,
        "pickTurn": 2,
    }
    action = ChampSelectAction.from_lcu_dict(raw)
    assert action.id == 12
    assert action.actor_cell_id == 3
    assert action.champion_id == 103
    assert action.type == "pick"
    assert action.completed is False
    assert action.is_in_progress is True
    assert action.pick_turn == 2
    assert action.is_pick() is True
    assert action.is_ban() is False

    d = action.to_dict()
    assert d["id"] == 12
    assert d["type"] == "pick"
    assert d["completed"] is False


def test_champ_select_action_ban() -> None:
    """Verify action parsing for a ban action."""
    raw = {
        "id": 1,
        "actorCellId": 0,
        "championId": 84,
        "type": "BAN",
        "completed": True,
        "isInProgress": False,
        "pickTurn": 1,
    }
    action = ChampSelectAction.from_lcu_dict(raw)
    assert action.id == 1
    assert action.champion_id == 84
    assert action.is_ban() is True
    assert action.is_pick() is False
    assert action.completed is True


def test_champ_select_player_parsing() -> None:
    """Verify player parsing from LCU payload."""
    raw = {
        "cellId": 2,
        "championId": 103,
        "championPickIntent": 0,
        "assignedPosition": "middle",
        "spell1Id": 4,
        "spell2Id": 14,
        "summonerId": 99887766,
        "team": 1,
    }
    player = ChampSelectPlayer.from_lcu_dict(raw)
    assert player.cell_id == 2
    assert player.champion_id == 103
    assert player.assigned_position == "middle"
    assert player.spell1_id == 4
    assert player.spell2_id == 14
    assert player.summoner_id == 99887766
    assert player.team == 1

    d = player.to_dict()
    assert d["cell_id"] == 2
    assert d["assigned_position"] == "middle"


def test_champ_select_timer_calculations() -> None:
    """Verify timer calculation and seconds conversion."""
    raw = {
        "phase": "BAN_PICK",
        "timeLeftInPhase": 27000,
        "totalTimeInPhase": 30000,
        "adjustedTimeLeftInPhase": 26500,
    }
    timer = ChampSelectTimer.from_lcu_dict(raw)
    assert timer.phase == "BAN_PICK"
    assert timer.time_left_in_phase == 27000
    assert timer.total_time_in_phase == 30000
    assert timer.adjusted_time_left_in_phase == 26500
    assert timer.time_left_seconds() == 26.5

    # Fallback to timeLeftInPhase when adjusted is 0
    timer_fallback = ChampSelectTimer(phase="PLANNING", time_left_in_phase=15000)
    assert timer_fallback.time_left_seconds() == 15.0

    d = timer.to_dict()
    assert d["timeLeftSeconds"] == 26.5


def test_champ_select_bans_parsing() -> None:
    """Verify bans parsing."""
    raw = {
        "myTeamBans": [103, 84, 157],
        "theirTeamBans": [51, 64],
        "numBans": 5,
    }
    bans = ChampSelectBans.from_lcu_dict(raw)
    assert bans.my_team_bans == [103, 84, 157]
    assert bans.their_team_bans == [51, 64]
    assert bans.num_bans == 5

    d = bans.to_dict()
    assert d["num_bans"] == 5


def test_champion_summary_parsing() -> None:
    """Verify champion summary parsing for champion selection grid."""
    raw = {
        "id": 103,
        "name": "Ahri",
        "roles": ["Mage", "Assassin"],
        "squarePortraitPath": "/lol-game-data/assets/v1/champion-icons/103.png",
        "disabled": False,
    }
    champ = ChampionSummary.from_lcu_dict(raw)
    assert champ.id == 103
    assert champ.name == "Ahri"
    assert champ.roles == ["mage", "assassin"]
    assert champ.square_portrait_path == "/lol-game-data/assets/v1/champion-icons/103.png"
    assert champ.disabled is False

    d = champ.to_dict()
    assert d["name"] == "Ahri"
    assert d["roles"] == ["mage", "assassin"]


def test_champ_select_session_full_lifecycle() -> None:
    """Verify complete ChampSelectSession parsing, active action lookup, and team indexing."""
    raw_session = {
        "localPlayerCellId": 1,
        "myTeam": [
            {"cellId": 0, "championId": 84, "assignedPosition": "top", "team": 1},
            {"cellId": 1, "championId": 0, "assignedPosition": "middle", "team": 1},
        ],
        "theirTeam": [
            {"cellId": 5, "championId": 157, "assignedPosition": "middle", "team": 2},
        ],
        "actions": [
            [
                {"id": 1, "actorCellId": 0, "championId": 51, "type": "ban", "completed": True, "isInProgress": False},
                {"id": 2, "actorCellId": 1, "championId": 0, "type": "ban", "completed": False, "isInProgress": True},
            ],
            [
                {"id": 10, "actorCellId": 1, "championId": 0, "type": "pick", "completed": False, "isInProgress": False},
            ],
        ],
        "timer": {
            "phase": "BAN_PICK",
            "timeLeftInPhase": 25000,
            "totalTimeInPhase": 30000,
            "adjustedTimeLeftInPhase": 24000,
        },
        "bans": {
            "myTeamBans": [51],
            "theirTeamBans": [64],
            "numBans": 2,
        },
        "isSpectating": False,
    }

    session = ChampSelectSession.from_lcu_dict(raw_session)
    assert session.local_player_cell_id == 1
    assert len(session.my_team) == 2
    assert len(session.their_team) == 1
    assert len(session.actions) == 2

    # Verify local player retrieval
    local_p = session.get_local_player()
    assert local_p is not None
    assert local_p.cell_id == 1
    assert local_p.assigned_position == "middle"

    # Verify active action retrieval (should be action id 2: ban in progress)
    active = session.get_active_action()
    assert active is not None
    assert active.id == 2
    assert active.is_ban() is True
    assert active.is_in_progress is True

    # Verify banned IDs
    banned = session.get_all_banned_ids()
    assert 51 in banned
    assert 64 in banned

    # Verify serialization
    dumped = session.to_dict()
    assert dumped["localPlayerCellId"] == 1
    assert dumped["activeAction"]["id"] == 2
    assert dumped["localPlayer"]["assigned_position"] == "middle"
    assert 51 in dumped["allBannedIds"]


def test_champ_select_session_no_active_action() -> None:
    """Verify session behavior when no action is currently in progress for the local user."""
    raw_session = {
        "localPlayerCellId": 0,
        "myTeam": [{"cellId": 0}],
        "actions": [
            [
                # In progress, but for cellId 1 (not local player)
                {"id": 1, "actorCellId": 1, "type": "pick", "completed": False, "isInProgress": True},
                # For cellId 0, but already completed
                {"id": 2, "actorCellId": 0, "type": "ban", "completed": True, "isInProgress": False},
            ]
        ],
    }
    session = ChampSelectSession.from_lcu_dict(raw_session)
    assert session.get_active_action() is None


def test_champ_select_session_empty_payload() -> None:
    """Verify session gracefully handles empty raw dictionary without raising errors."""
    session = ChampSelectSession.from_lcu_dict({})
    assert session.local_player_cell_id == -1
    assert session.my_team == []
    assert session.their_team == []
    assert session.actions == []
    assert session.get_local_player() is None
    assert session.get_active_action() is None
    assert session.get_all_banned_ids() == set()
    dumped = session.to_dict()
    assert dumped["localPlayerCellId"] == -1
    assert dumped["activeAction"] is None
    assert dumped["localPlayer"] is None
