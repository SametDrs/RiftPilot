"""RiftPilot Unit Tests — Runes, Mode-Aware Summoner Spells, and Game Mode State Machine.

Verifies:
- Mode-filtered summoner spells (ARAM vs CLASSIC vs ARAM Şamata/KIWI)
- RunePage and official LCU RecommendedRunePage parsing (never fabricated)
- GameModeContext layout resolution (Draft, Blind, ARAM, Safe Generic)
- ChampSelectStage state machine and ActionEligibility button rules
- Server-side command validation (`validate_champ_select_command`)
"""

from __future__ import annotations

from riftpilot.core.champ_select import (
    ChampSelectAction,
    ChampSelectBans,
    ChampSelectPlayer,
    ChampSelectSession,
    ChampSelectTimer,
)
from riftpilot.core.game_mode import (
    ChampSelectStage,
    GameModeContext,
    SelectionLayoutKind,
    compute_champ_select_stage_and_eligibility,
    validate_champ_select_command,
)
from riftpilot.core.runes_spells import (
    RecommendedRunePage,
    RunePage,
    SummonerSpell,
    get_all_summoner_spells,
    get_spells_for_mode,
    get_summoner_spell,
)


def test_summoner_spells_catalog_and_mode_filtering() -> None:
    """Verify summoner spells filtering by active game mode (ARAM vs CLASSIC)."""
    all_spells = get_all_summoner_spells()
    assert len(all_spells) >= 10

    flash = get_summoner_spell(4)
    assert flash is not None
    assert flash.is_available_in_mode("CLASSIC") is True
    assert flash.is_available_in_mode("ARAM") is True

    # Classic mode: Smite (11) & Teleport (12) available, Snowball (32) & Clarity (13) excluded
    classic_ids = {s.id for s in get_spells_for_mode("CLASSIC")}
    assert 4 in classic_ids
    assert 11 in classic_ids
    assert 12 in classic_ids
    assert 32 not in classic_ids
    assert 13 not in classic_ids

    # ARAM & ARAM Şamata (KIWI): Snowball (32) & Clarity (13) available, Smite (11) & Teleport (12) excluded
    for aram_mode in ("ARAM", "KIWI"):
        aram_ids = {s.id for s in get_spells_for_mode(aram_mode)}
        assert 4 in aram_ids
        assert 32 in aram_ids
        assert 13 in aram_ids
        assert 11 not in aram_ids
        assert 12 not in aram_ids


def test_summoner_spell_from_lcu_dict() -> None:
    """Verify parsing raw `/lol-game-data/assets/v1/summoner-spells.json` entries."""
    raw = {
        "id": 32,
        "name": "Mark",
        "description": "Throw a snowball.",
        "gameModes": ["ARAM", "FIRSTBLOOD"],
        "iconPath": "/lol-game-data/assets/DATA/Spells/Icons2D/Summoner_Mark.png",
    }
    sp = SummonerSpell.from_lcu_dict(raw)
    assert sp.id == 32
    assert sp.is_available_in_mode("ARAM") is True
    assert sp.is_available_in_mode("CLASSIC") is False


def test_rune_page_and_official_recommended_rune_page_parsing() -> None:
    """Verify RunePage and RecommendedRunePage parsing from official LCU structures."""
    raw_page = {
        "id": 55,
        "name": "My Custom Page",
        "primaryStyleId": 8100,
        "subStyleId": 8200,
        "selectedPerkIds": [8112, 8143, 8138, 8106, 8226, 8233, 5008, 5008, 5003],
        "current": True,
        "isEditable": True,
    }
    page = RunePage.from_lcu_dict(raw_page)
    assert page.id == 55
    assert page.primary_style_id == 8100
    assert page.to_lcu_payload()["current"] is True

    # Parse official recommended page
    rec = RecommendedRunePage.from_lcu_dict(raw_page, champion_id=103)
    assert rec is not None
    assert rec.champion_id == 103
    assert rec.primary_style_id == 8100
    assert rec.sub_style_id == 8200
    assert "Elektrik Ver" in rec.keystone_name
    assert rec.primary_tree_name == "Hâkimiyet"
    assert rec.sub_tree_name == "Büyücülük"
    assert len(rec.perk_names) == 9

    # Invalid/empty dict returns None (never fabricates fake data)
    assert RecommendedRunePage.from_lcu_dict({}) is None


def test_game_mode_context_and_layout_resolution() -> None:
    """Verify GameModeContext resolves Ranked Draft, Blind Pick, ARAM, ARAM Şamata, and Unknown modes."""
    # Ranked Solo (420)
    ranked_ctx = GameModeContext.from_lcu_gameflow_session({
        "gameData": {"queue": {"id": 420, "gameMode": "CLASSIC", "description": "Ranked Solo/Duo"}},
        "map": {"id": 11},
    })
    assert ranked_ctx.resolve_layout() == SelectionLayoutKind.DRAFT
    assert ranked_ctx.display_name == "Dereceli Tek/Çift"

    # Blind Pick (430)
    blind_ctx = GameModeContext.from_lcu_gameflow_session({
        "gameData": {"queue": {"id": 430, "gameMode": "CLASSIC"}},
        "map": {"id": 11},
    })
    assert blind_ctx.resolve_layout() == SelectionLayoutKind.BLIND

    # ARAM Şamata (2400 / KIWI)
    shamata_ctx = GameModeContext.from_lcu_gameflow_session({
        "gameData": {"queue": {"id": 2400, "gameMode": "KIWI", "description": "ARAM Brawl"}},
        "map": {"id": 12},
    })
    assert shamata_ctx.resolve_layout() == SelectionLayoutKind.ARAM
    assert shamata_ctx.display_name == "ARAM Şamata"
    assert shamata_ctx.map_id == 12

    # Unknown mode -> SAFE_GENERIC
    unknown_ctx = GameModeContext.from_lcu_gameflow_session({
        "gameData": {"queue": {"id": 9999, "gameMode": "DOOMBOTS_V99"}},
        "map": {"id": 99},
    })
    assert unknown_ctx.resolve_layout() == SelectionLayoutKind.SAFE_GENERIC


def test_state_machine_and_command_validation_rules() -> None:
    """Verify lock/ban button eligibility and strict server-side command validation."""
    draft_ctx = GameModeContext(game_mode="CLASSIC", queue_id=420, map_id=11)

    # 1. Ban phase — local player's turn (cell 0), hovered champ 157 (Yasuo), banned 51
    session = ChampSelectSession(
        local_player_cell_id=0,
        my_team=[ChampSelectPlayer(cell_id=0, assigned_position="middle")],
        actions=[
            [
                ChampSelectAction(id=1, actor_cell_id=0, champion_id=157, type="ban", completed=False, is_in_progress=True),
                ChampSelectAction(id=2, actor_cell_id=0, champion_id=0, type="pick", completed=False, is_in_progress=False),
            ]
        ],
        bans=ChampSelectBans(my_team_bans=[51]),
        timer=ChampSelectTimer(phase="BAN_PICK", time_left_in_phase=20000, total_time_in_phase=30000),
    )

    elig = compute_champ_select_stage_and_eligibility(session, draft_ctx)
    assert elig.stage == ChampSelectStage.BAN_MY_TURN
    assert elig.ban_phase_exists is True
    assert elig.ban_button_visible is True
    assert elig.ban_button_enabled is True
    assert elig.lock_button_enabled is False

    # Cannot ban an already-banned champion (51)
    res_banned = validate_champ_select_command(session, draft_ctx, action_id=1, champion_id=51, completed=True)
    assert res_banned.allowed is False
    assert res_banned.error_code == "CHAMPION_BANNED"

    # Cannot lock a pick action (id=2) while it's not in progress
    res_pick_early = validate_champ_select_command(session, draft_ctx, action_id=2, champion_id=103, completed=True)
    assert res_pick_early.allowed is False
    assert res_pick_early.error_code == "NOT_YOUR_PICK_TURN"

    # Valid ban completes
    res_ban_ok = validate_champ_select_command(session, draft_ctx, action_id=1, champion_id=157, completed=True)
    assert res_ban_ok.allowed is True

    # 2. Blind pick mode — no ban actions exist -> ban UI hidden
    blind_ctx = GameModeContext(game_mode="CLASSIC", queue_id=430, map_id=11)
    blind_session = ChampSelectSession(
        local_player_cell_id=0,
        my_team=[ChampSelectPlayer(cell_id=0)],
        actions=[
            [ChampSelectAction(id=10, actor_cell_id=0, champion_id=103, type="pick", completed=False, is_in_progress=True)]
        ],
        timer=ChampSelectTimer(phase="BAN_PICK", time_left_in_phase=30000, total_time_in_phase=30000),
    )
    blind_elig = compute_champ_select_stage_and_eligibility(blind_session, blind_ctx)
    assert blind_elig.layout == SelectionLayoutKind.BLIND
    assert blind_elig.ban_phase_exists is False
    assert blind_elig.ban_button_visible is False
    assert blind_elig.lock_button_enabled is True
