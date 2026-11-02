"""RiftPilot Unit Tests — Champion Select Pool Filtering, Tagging & Mode Eligibility.

Agent B test suite verifying:
1. ChampionRole enumeration.
2. ChampionSummary enhanced domain attributes (roles, mastery, free-to-play, owned).
3. filter_champion_pool (query, role, ownership, disabled exclusion).
4. validate_champion_pool_selection (banned, picked, disabled, and valid champions).
5. is_champion_eligible_for_mode (standard modes vs ARAM custom restrictions).
"""

from __future__ import annotations

import pytest

from riftpilot.core.champ_select import (
    ChampionRole,
    ChampionSummary,
    filter_champion_pool,
    validate_champion_pool_selection,
)
from riftpilot.core.game_mode import (
    GameModeContext,
    is_champion_eligible_for_mode,
    set_mode_restricted_champions,
)


@pytest.fixture(autouse=True)
def reset_mode_restrictions() -> None:
    """Ensure mode restrictions are cleanly reset before and after each test."""
    set_mode_restricted_champions("ARAM", set())
    yield
    set_mode_restricted_champions("ARAM", set())


@pytest.fixture
def sample_pool() -> list[ChampionSummary]:
    """Sample champion pool with varied roles, ownership, and statuses."""
    return [
        ChampionSummary(
            id=103,
            name="Ahri",
            roles=["mage", "assassin", "mid"],
            square_portrait_path="/icons/103.png",
            disabled=False,
            mastery_level=7,
            free_to_play=False,
            owned=True,
        ),
        ChampionSummary(
            id=84,
            name="Akali",
            roles=["assassin", "mid", "top"],
            square_portrait_path="/icons/84.png",
            disabled=False,
            mastery_level=5,
            free_to_play=False,
            owned=True,
        ),
        ChampionSummary(
            id=54,
            name="Malphite",
            roles=["tank", "top"],
            square_portrait_path="/icons/54.png",
            disabled=False,
            mastery_level=3,
            free_to_play=False,
            owned=True,
        ),
        ChampionSummary(
            id=22,
            name="Ashe",
            roles=["marksman", "bottom"],
            square_portrait_path="/icons/22.png",
            disabled=False,
            mastery_level=2,
            free_to_play=True,
            owned=True,
        ),
        ChampionSummary(
            id=64,
            name="Lee Sin",
            roles=["fighter", "jungle"],
            square_portrait_path="/icons/64.png",
            disabled=False,
            mastery_level=6,
            free_to_play=False,
            owned=False,
        ),
        ChampionSummary(
            id=412,
            name="Thresh",
            roles=["support", "tank"],
            square_portrait_path="/icons/412.png",
            disabled=True,
            mastery_level=4,
            free_to_play=False,
            owned=True,
        ),
        ChampionSummary(
            id=31,
            name="Cho'Gath",
            roles=["tank", "top", "mage"],
            square_portrait_path="/icons/31.png",
            disabled=False,
            mastery_level=1,
            free_to_play=False,
            owned=True,
        ),
    ]


# ============================================================================
# 1. ChampionRole Enum Tests
# ============================================================================


class TestChampionRoleEnum:
    """Verify ChampionRole enum values and characteristics."""

    def test_champion_role_values(self) -> None:
        assert ChampionRole.TOP.value == "top"
        assert ChampionRole.JUNGLE.value == "jungle"
        assert ChampionRole.MID.value == "mid"
        assert ChampionRole.BOTTOM.value == "bottom"
        assert ChampionRole.SUPPORT.value == "support"
        assert ChampionRole.ASSASSIN.value == "assassin"
        assert ChampionRole.MAGE.value == "mage"
        assert ChampionRole.TANK.value == "tank"
        assert ChampionRole.MARKSMAN.value == "marksman"
        assert ChampionRole.FIGHTER.value == "fighter"

    def test_champion_role_str_compatibility(self) -> None:
        assert isinstance(ChampionRole.MAGE, str)
        assert ChampionRole.MAGE == "mage"
        assert str(ChampionRole.TANK) == "ChampionRole.TANK" or ChampionRole.TANK.value == "tank"


# ============================================================================
# 2. ChampionSummary Model Enhancement Tests
# ============================================================================


class TestChampionSummaryEnhancements:
    """Verify enhanced ChampionSummary fields, defaults, and parsing."""

    def test_defaults(self) -> None:
        champ = ChampionSummary(id=1, name="Annie")
        assert champ.id == 1
        assert champ.name == "Annie"
        assert champ.roles == []
        assert champ.square_portrait_path == ""
        assert champ.disabled is False
        assert champ.mastery_level == 0
        assert champ.free_to_play is False
        assert champ.owned is True

    def test_from_lcu_dict_camel_case(self) -> None:
        raw = {
            "id": 103,
            "name": "Ahri",
            "roles": ["Mage", "Assassin"],
            "squarePortraitPath": "/icons/103.png",
            "disabled": False,
            "masteryLevel": 7,
            "freeToPlay": True,
            "owned": False,
        }
        champ = ChampionSummary.from_lcu_dict(raw)
        assert champ.id == 103
        assert champ.name == "Ahri"
        assert champ.roles == ["mage", "assassin"]
        assert champ.mastery_level == 7
        assert champ.free_to_play is True
        assert champ.owned is False

    def test_from_lcu_dict_snake_case_and_ownership_object(self) -> None:
        raw = {
            "id": 84,
            "name": "Akali",
            "roles": ["assassin"],
            "square_portrait_path": "/icons/84.png",
            "disabled": True,
            "mastery_level": 5,
            "free_to_play": False,
            "ownership": {"owned": False},
        }
        champ = ChampionSummary.from_lcu_dict(raw)
        assert champ.id == 84
        assert champ.name == "Akali"
        assert champ.disabled is True
        assert champ.mastery_level == 5
        assert champ.free_to_play is False
        assert champ.owned is False

    def test_to_dict_includes_enhanced_fields(self) -> None:
        champ = ChampionSummary(
            id=103,
            name="Ahri",
            roles=["mage"],
            mastery_level=7,
            free_to_play=True,
            owned=True,
        )
        d = champ.to_dict()
        assert d["id"] == 103
        assert d["name"] == "Ahri"
        assert d["roles"] == ["mage"]
        assert d["masteryLevel"] == 7
        assert d["mastery_level"] == 7
        assert d["freeToPlay"] is True
        assert d["free_to_play"] is True
        assert d["owned"] is True


# ============================================================================
# 3. filter_champion_pool Tests
# ============================================================================


class TestFilterChampionPool:
    """Verify champion pool search, role filtering, ownership, and disabled rules."""

    def test_query_filter_exact_and_partial(self, sample_pool: list[ChampionSummary]) -> None:
        # Exact match
        result = filter_champion_pool(sample_pool, query="Ahri")
        assert len(result) == 1
        assert result[0].name == "Ahri"

        # Case-insensitive partial match
        result = filter_champion_pool(sample_pool, query="ak")
        assert [c.name for c in result] == ["Akali"]

        # Substring matching multiple champions (note: Thresh is disabled and excluded by default)
        result = filter_champion_pool(sample_pool, query="a")
        # Ahri, Akali, Malphite, Ashe, Cho'Gath all contain 'a'
        names = [c.name for c in result]
        assert "Ahri" in names
        assert "Akali" in names
        assert "Malphite" in names
        assert "Ashe" in names
        assert "Cho'Gath" in names
        assert "Thresh" not in names  # Thresh is disabled

    def test_query_filter_punctuation_and_whitespace(self, sample_pool: list[ChampionSummary]) -> None:
        # Punctuation tolerance: "chogath" matches "Cho'Gath"
        result1 = filter_champion_pool(sample_pool, query="chogath")
        assert len(result1) == 1
        assert result1[0].name == "Cho'Gath"

        result2 = filter_champion_pool(sample_pool, query="cho'gath")
        assert len(result2) == 1
        assert result2[0].name == "Cho'Gath"

        # Whitespace trimming
        result3 = filter_champion_pool(sample_pool, query="  lee sin  ", only_owned=False)
        assert len(result3) == 1
        assert result3[0].name == "Lee Sin"

    def test_query_filter_no_match(self, sample_pool: list[ChampionSummary]) -> None:
        result = filter_champion_pool(sample_pool, query="nonexistent123")
        assert result == []

    def test_role_filtering(self, sample_pool: list[ChampionSummary]) -> None:
        # Filter by string 'mage' -> Ahri and Cho'Gath
        mages = filter_champion_pool(sample_pool, role="mage")
        assert {c.name for c in mages} == {"Ahri", "Cho'Gath"}

        # Filter by ChampionRole enum
        tanks = filter_champion_pool(sample_pool, role=ChampionRole.TANK)
        assert {c.name for c in tanks} == {"Malphite", "Cho'Gath"}

        # Filter by 'assassin'
        assassins = filter_champion_pool(sample_pool, role=ChampionRole.ASSASSIN)
        assert {c.name for c in assassins} == {"Ahri", "Akali"}

        # Position aliases: 'mid' matches Ahri, Akali
        mid_laners = filter_champion_pool(sample_pool, role="mid")
        assert {c.name for c in mid_laners} == {"Ahri", "Akali"}

        # Position aliases: 'bottom' or 'bot' matches Ashe
        bot_laners = filter_champion_pool(sample_pool, role="bot")
        assert {c.name for c in bot_laners} == {"Ashe"}

    def test_only_owned_filter(self, sample_pool: list[ChampionSummary]) -> None:
        # Lee Sin is owned=False
        all_champs = filter_champion_pool(sample_pool, only_owned=False)
        assert any(c.name == "Lee Sin" for c in all_champs)

        owned_champs = filter_champion_pool(sample_pool, only_owned=True)
        assert not any(c.name == "Lee Sin" for c in owned_champs)
        assert all(c.owned for c in owned_champs)

    def test_exclude_disabled_filter(self, sample_pool: list[ChampionSummary]) -> None:
        # Thresh has disabled=True
        default_filtered = filter_champion_pool(sample_pool, exclude_disabled=True)
        assert not any(c.name == "Thresh" for c in default_filtered)

        with_disabled = filter_champion_pool(sample_pool, exclude_disabled=False)
        assert any(c.name == "Thresh" for c in with_disabled)

    def test_combined_filters(self, sample_pool: list[ChampionSummary]) -> None:
        # Search query 'a', role 'mage', only owned, exclude disabled
        result = filter_champion_pool(
            sample_pool,
            query="a",
            role="mage",
            only_owned=True,
            exclude_disabled=True,
        )
        assert {c.name for c in result} == {"Ahri", "Cho'Gath"}

        # Search query 'lee', only owned -> empty because Lee Sin is not owned
        result2 = filter_champion_pool(sample_pool, query="lee", only_owned=True)
        assert result2 == []

        # Search query 'lee', only_owned=False -> Lee Sin found
        result3 = filter_champion_pool(sample_pool, query="lee", only_owned=False)
        assert len(result3) == 1
        assert result3[0].name == "Lee Sin"


# ============================================================================
# 4. validate_champion_pool_selection Tests
# ============================================================================


class TestValidateChampionPoolSelection:
    """Verify validation logic for picks, bans, disabled status, and valid champions."""

    def test_valid_champion_selection(self, sample_pool: list[ChampionSummary]) -> None:
        valid, reason = validate_champion_pool_selection(
            champion_id=103,
            champions=sample_pool,
            disabled_ids=set(),
            banned_ids=set(),
            picked_ids=set(),
        )
        assert valid is True
        assert reason == ""

    def test_invalid_champion_id(self, sample_pool: list[ChampionSummary]) -> None:
        valid, reason = validate_champion_pool_selection(0, sample_pool)
        assert valid is False
        assert "invalid" in reason.lower() or "geçersiz" in reason.lower()

        valid_neg, _ = validate_champion_pool_selection(-10, sample_pool)
        assert valid_neg is False

    def test_champion_not_in_pool(self, sample_pool: list[ChampionSummary]) -> None:
        valid, reason = validate_champion_pool_selection(99999, sample_pool)
        assert valid is False
        assert "pool" in reason.lower() or "havuz" in reason.lower()

    def test_disabled_on_champion_summary(self, sample_pool: list[ChampionSummary]) -> None:
        # Thresh (id=412) has disabled=True in sample_pool
        valid, reason = validate_champion_pool_selection(412, sample_pool)
        assert valid is False
        assert "disabled" in reason.lower() or "devre dışı" in reason.lower()

    def test_disabled_via_disabled_ids_set(self, sample_pool: list[ChampionSummary]) -> None:
        # Ahri (id=103) is not disabled in summary, but disabled_ids contains 103
        valid, reason = validate_champion_pool_selection(
            103,
            sample_pool,
            disabled_ids={103},
        )
        assert valid is False
        assert "disabled" in reason.lower() or "devre dışı" in reason.lower()

    def test_banned_champion(self, sample_pool: list[ChampionSummary]) -> None:
        valid, reason = validate_champion_pool_selection(
            103,
            sample_pool,
            banned_ids={103, 84},
        )
        assert valid is False
        assert "banned" in reason.lower() or "yasak" in reason.lower()

    def test_picked_champion(self, sample_pool: list[ChampionSummary]) -> None:
        valid, reason = validate_champion_pool_selection(
            103,
            sample_pool,
            picked_ids={103, 54},
        )
        assert valid is False
        assert "picked" in reason.lower() or "seçil" in reason.lower()

    def test_multiple_restrictions_priority(self, sample_pool: list[ChampionSummary]) -> None:
        # When champion is disabled and banned, it should fail immediately
        valid, _ = validate_champion_pool_selection(
            412,
            sample_pool,
            disabled_ids={412},
            banned_ids={412},
        )
        assert valid is False


# ============================================================================
# 5. is_champion_eligible_for_mode Tests
# ============================================================================


class TestIsChampionEligibleForMode:
    """Verify champion eligibility across standard game modes and ARAM custom rules."""

    def test_classic_draft_standard_mode(self) -> None:
        ctx = GameModeContext(game_mode="CLASSIC", queue_id=420)

        # Valid champion
        assert is_champion_eligible_for_mode(103, ctx) is True

        # Invalid ID <= 0
        assert is_champion_eligible_for_mode(0, ctx) is False
        assert is_champion_eligible_for_mode(-5, ctx) is False

        # Explicit disabled IDs
        assert is_champion_eligible_for_mode(103, ctx, disabled_champion_ids={103}) is False
        assert is_champion_eligible_for_mode(103, ctx, disabled_champion_ids={84}) is True

    def test_blind_pick_mode(self) -> None:
        ctx = GameModeContext(game_mode="CLASSIC", queue_id=430)
        assert is_champion_eligible_for_mode(84, ctx) is True
        assert is_champion_eligible_for_mode(84, ctx, disabled_champion_ids={84}) is False

    def test_unsupported_safe_generic_mode(self) -> None:
        # Unknown/unsupported mode resolves to SAFE_GENERIC layout
        ctx = GameModeContext(game_mode="UNKNOWN_CUSTOM_TEST", queue_id=99999)
        assert is_champion_eligible_for_mode(103, ctx) is False

    def test_aram_standard_and_disabled_ids(self) -> None:
        ctx = GameModeContext(game_mode="ARAM", queue_id=450, map_id=12)

        # Standard ARAM allows champions by default
        assert is_champion_eligible_for_mode(103, ctx) is True

        # Explicit disabled champions in ARAM
        assert is_champion_eligible_for_mode(103, ctx, disabled_champion_ids={103}) is False
        assert is_champion_eligible_for_mode(54, ctx, disabled_champion_ids={103}) is True

    def test_aram_custom_mutator_restrictions(self) -> None:
        # ARAM custom game with 'disable_<id>' mutator
        ctx_mutator = GameModeContext(
            game_mode="ARAM",
            queue_id=450,
            map_id=12,
            mutators=["disable_103", "some_other_mutator"],
        )
        assert is_champion_eligible_for_mode(103, ctx_mutator) is False
        assert is_champion_eligible_for_mode(84, ctx_mutator) is True

        # ARAM custom game with 'disabled_champions:103,84' mutator
        ctx_multi = GameModeContext(
            game_mode="ARAM",
            queue_id=450,
            map_id=12,
            mutators=["disabled_champions:103,84"],
        )
        assert is_champion_eligible_for_mode(103, ctx_multi) is False
        assert is_champion_eligible_for_mode(84, ctx_multi) is False
        assert is_champion_eligible_for_mode(54, ctx_multi) is True

    def test_aram_mode_restricted_champions_registry(self) -> None:
        ctx = GameModeContext(game_mode="ARAM", queue_id=450, map_id=12)

        set_mode_restricted_champions("ARAM", {999, 888})
        try:
            assert is_champion_eligible_for_mode(999, ctx) is False
            assert is_champion_eligible_for_mode(888, ctx) is False
            assert is_champion_eligible_for_mode(103, ctx) is True
        finally:
            set_mode_restricted_champions("ARAM", set())
            assert is_champion_eligible_for_mode(999, ctx) is True
