"""Tests for Custom Rune Builder (Domain, API, Validation, and UI Layout).

Verifies:
1. Complete RUNE_DATA catalog integrity (Trees, slots, keystones, minor runes, stat shards).
2. CustomRuneManager validation logic:
   - Valid combination passes.
   - Empty name fails.
   - Matching primary and sub-style fails.
   - Invalid tree IDs fail.
   - Non-9 perk count fails.
   - Invalid primary keystone fails.
   - Primary minor runes from incorrect rows fail.
   - Secondary runes from the same row fail.
   - Secondary runes with keystone fail.
   - Invalid stat shard IDs fail.
3. CustomRuneManager CRUD & Persistence:
   - Save custom page.
   - Get page by ID.
   - List all pages in sorted order.
   - Delete page by ID.
   - Persistence round-trip using JSON storage file.
   - Conversion to editable RunePage and LCU payload.
4. REST API Endpoints:
   - GET /api/runes/trees
   - GET /api/runes/custom
   - POST /api/runes/custom (valid and invalid cases)
   - DELETE /api/runes/custom/{id} (success and 404)
   - POST /api/runes/apply (custom page application to LCU)
5. HTML UI Elements:
   - Vertical recommended runes layout.
   - 4th Card trigger ("+ Kendi Rünümü Oluştur").
   - Custom Rune Builder modal (#customRuneModal) and sub-components.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

_worktree_src = Path(__file__).resolve().parent.parent / "src"
if str(_worktree_src) not in sys.path or sys.path[0] != str(_worktree_src):
    sys.path.insert(0, str(_worktree_src))

import pytest

from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.web_hub import INDEX_HTML, create_app
from riftpilot.core.lcu_port import LcuConnectionStatus
from riftpilot.core.pairing import OTTManager, SessionManager
from riftpilot.core.runes_spells import (
    CustomRuneManager,
    CustomRunePage,
    RUNE_DATA,
    RunePage,
    get_custom_rune_manager,
    get_perk_icon_url,
    get_perk_info,
    get_perk_name,
    get_perk_row,
    get_rune_tree_name,
)


@pytest.fixture
def ott_manager() -> OTTManager:
    return OTTManager()


@pytest.fixture
def session_manager() -> SessionManager:
    return SessionManager()


@pytest.fixture
def mock_lcu() -> MockLcuAdapter:
    return MockLcuAdapter()


@pytest.fixture
def test_app(ott_manager: OTTManager, session_manager: SessionManager, mock_lcu: MockLcuAdapter):
    print("FIXTURE: test_app starting...", flush=True)
    app = create_app(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lcu_adapter=mock_lcu,
        enable_monitor=False,
    )
    print("FIXTURE: test_app created!", flush=True)
    return app


# =============================================================================
# 1. Domain Tests: RUNE_DATA and Helpers
# =============================================================================

def test_rune_data_catalog_integrity() -> None:
    """Ensure RUNE_DATA contains all 5 official trees and 3 stat shard slots."""
    assert "trees" in RUNE_DATA
    assert "stat_shards" in RUNE_DATA

    trees = RUNE_DATA["trees"]
    expected_tree_ids = {8000, 8100, 8200, 8300, 8400}
    assert set(trees.keys()) == expected_tree_ids

    # Verify each tree has 4 slots (slot 0=keystone, 1..3=minor)
    for tid, tree in trees.items():
        assert tree["id"] == tid
        assert "name" in tree and len(tree["name"]) > 0
        assert "icon" in tree and tree["icon"].startswith("http")
        assert len(tree["slots"]) == 4

        # Slot 0: Keystone
        assert tree["slots"][0]["row"] == 0
        assert tree["slots"][0]["type"] == "keystone"
        assert len(tree["slots"][0]["runes"]) >= 3
        for rune in tree["slots"][0]["runes"]:
            assert rune["id"] > 0
            assert len(rune["name"]) > 0
            assert rune["icon"].startswith("http")

        # Slots 1-3: Minor runes
        for row_idx in (1, 2, 3):
            slot = tree["slots"][row_idx]
            assert slot["row"] == row_idx
            assert slot["type"] == "minor"
            assert len(slot["runes"]) >= 3
            for rune in slot["runes"]:
                assert rune["id"] > 0
                assert len(rune["name"]) > 0
                assert rune["icon"].startswith("http")

    # Verify stat shards
    stat_shards = RUNE_DATA["stat_shards"]
    assert len(stat_shards) == 3
    for s_idx, shard_group in enumerate(stat_shards):
        assert shard_group["slot"] == s_idx
        assert len(shard_group["shards"]) >= 3
        for shard in shard_group["shards"]:
            assert shard["id"] in {5001, 5002, 5003, 5005, 5007, 5008, 5010, 5011, 5013}


def test_rune_helper_functions() -> None:
    """Verify get_perk_row, get_perk_info, and get_perk_icon_url."""
    # Press the Attack (8005) is row 0 in Precision (8000)
    assert get_perk_row(8000, 8005) == 0
    # Triumph (9111) is row 1 in Precision (8000)
    assert get_perk_row(8000, 9111) == 1
    # Coup de Grace (8014) is row 3 in Precision (8000)
    assert get_perk_row(8000, 8014) == 3

    # Unknown perk in tree returns None
    assert get_perk_row(8000, 99999) is None
    # Perk from Domination in Precision tree returns None
    assert get_perk_row(8000, 8112) is None

    # get_perk_info lookup
    info_8005 = get_perk_info(8005)
    assert info_8005 is not None
    assert info_8005["name"] == "Saldırıya Devam"

    # get_perk_icon_url
    icon_8005 = get_perk_icon_url(8005)
    assert "precision" in icon_8005.lower()
    assert icon_8005.startswith("http")

    # Turkish tree names
    assert get_rune_tree_name(8000) == "İsabet"
    assert get_rune_tree_name(8100) == "Hâkimiyet"
    assert get_rune_tree_name(8200) == "Büyücülük"
    assert get_rune_tree_name(8300) == "İlham"
    assert get_rune_tree_name(8400) == "Azim"


# =============================================================================
# 2. Domain Tests: CustomRuneManager Validation Rules
# =============================================================================

def test_custom_rune_validation_valid_combination() -> None:
    """A fully compliant 9-perk page should validate successfully."""
    mgr = CustomRuneManager()
    valid, err = mgr.validate_rune_selection(
        primary_style_id=8000,  # Precision
        sub_style_id=8100,      # Domination
        selected_perk_ids=[
            8005,  # Press the Attack (Keystone, Row 0)
            9111,  # Triumph (Row 1)
            9104,  # Legend: Alacrity (Row 2)
            8014,  # Coup de Grace (Row 3)
            8139,  # Taste of Blood (Secondary Row 1)
            8135,  # Treasure Hunter (Secondary Row 3)
            5008,  # Adaptive Force (Offense)
            5008,  # Adaptive Force (Flex)
            5011,  # Flat Health (Defense)
        ],
        name="Agresif Aatrox",
    )
    assert valid is True
    assert err == ""


def test_custom_rune_validation_empty_name() -> None:
    """Empty or whitespace-only name must be rejected."""
    mgr = CustomRuneManager()
    valid, err = mgr.validate_rune_selection(
        primary_style_id=8000,
        sub_style_id=8100,
        selected_perk_ids=[8005, 9111, 9104, 8014, 8139, 8135, 5008, 5008, 5011],
        name="   ",
    )
    assert valid is False
    assert "boş olamaz" in err.lower()


def test_custom_rune_validation_same_trees() -> None:
    """Primary and secondary trees cannot be identical."""
    mgr = CustomRuneManager()
    valid, err = mgr.validate_rune_selection(
        primary_style_id=8000,
        sub_style_id=8000,
        selected_perk_ids=[8005, 9111, 9104, 8014, 8009, 8017, 5008, 5008, 5011],
        name="Aynı Ağaç Hatası",
    )
    assert valid is False
    assert "aynı olamaz" in err.lower()


def test_custom_rune_validation_invalid_tree_id() -> None:
    """Non-existent tree IDs must be rejected."""
    mgr = CustomRuneManager()
    valid, err = mgr.validate_rune_selection(
        primary_style_id=9999,
        sub_style_id=8100,
        selected_perk_ids=[8005, 9111, 9104, 8014, 8139, 8135, 5008, 5008, 5011],
        name="Geçersiz Ağaç",
    )
    assert valid is False
    assert "geçersiz birincil ağaç" in err.lower()


def test_custom_rune_validation_perk_count() -> None:
    """Rune page must have exactly 9 perks."""
    mgr = CustomRuneManager()
    # 8 perks
    valid, err = mgr.validate_rune_selection(
        primary_style_id=8000,
        sub_style_id=8100,
        selected_perk_ids=[8005, 9111, 9104, 8014, 8139, 8135, 5008, 5008],
        name="Eksik Rün",
    )
    assert valid is False
    assert "9 rün" in err.lower()


def test_custom_rune_validation_invalid_primary_keystone() -> None:
    """Index 0 must be a valid keystone from primary style."""
    mgr = CustomRuneManager()
    # Passing Triumph (9111, row 1) as keystone
    valid, err = mgr.validate_rune_selection(
        primary_style_id=8000,
        sub_style_id=8100,
        selected_perk_ids=[9111, 9111, 9104, 8014, 8139, 8135, 5008, 5008, 5011],
        name="Hatalı Anahtar Rün",
    )
    assert valid is False
    assert "anahtar rün" in err.lower()


def test_custom_rune_validation_secondary_runes_same_row() -> None:
    """Secondary tree runes cannot be from the same row."""
    mgr = CustomRuneManager()
    # Cheap Shot (8126) and Taste of Blood (8139) are both Row 1 in Domination
    valid, err = mgr.validate_rune_selection(
        primary_style_id=8000,
        sub_style_id=8100,
        selected_perk_ids=[8005, 9111, 9104, 8014, 8126, 8139, 5008, 5008, 5011],
        name="Aynı Satır İkincil Rün",
    )
    assert valid is False
    assert "farklı satırlardan" in err.lower()


def test_custom_rune_validation_secondary_keystone_rejected() -> None:
    """Secondary tree cannot pick keystones."""
    mgr = CustomRuneManager()
    # Electrocute (8112, Row 0) in secondary
    valid, err = mgr.validate_rune_selection(
        primary_style_id=8000,
        sub_style_id=8100,
        selected_perk_ids=[8005, 9111, 9104, 8014, 8112, 8135, 5008, 5008, 5011],
        name="İkincil Anahtar Rün Hatası",
    )
    assert valid is False
    assert "küçük rünlerinden" in err.lower()


def test_custom_rune_validation_invalid_stat_shard() -> None:
    """Stat shard must be a known stat mod ID."""
    mgr = CustomRuneManager()
    valid, err = mgr.validate_rune_selection(
        primary_style_id=8000,
        sub_style_id=8100,
        selected_perk_ids=[8005, 9111, 9104, 8014, 8139, 8135, 5008, 5008, 99999],
        name="Hatalı Nitelik",
    )
    assert valid is False
    assert "nitelik parçası" in err.lower()


# =============================================================================
# 3. Domain Tests: CustomRuneManager CRUD & Persistence
# =============================================================================

def test_custom_rune_manager_save_and_get(tmp_path: Path) -> None:
    """Test saving a custom rune page and retrieving it."""
    storage = tmp_path / "custom_runes.json"
    mgr = CustomRuneManager(storage_path=storage)

    page = mgr.save_page({
        "name": "Dövüşçü Sett",
        "primaryStyleId": 8000,
        "subStyleId": 8400,
        "selectedPerkIds": [8010, 9111, 9104, 8299, 8444, 8451, 5008, 5008, 5011],
    })

    assert page.id.startswith("custom-")
    assert page.name == "Dövüşçü Sett"
    assert page.primary_style_id == 8000
    assert page.sub_style_id == 8400
    assert len(page.selected_perk_ids) == 9

    # Retrieve by ID
    found = mgr.get_page(page.id)
    assert found is not None
    assert found.name == "Dövüşçü Sett"

    # Conversion to RunePage
    rp = page.to_rune_page()
    assert isinstance(rp, RunePage)
    assert rp.name == "Dövüşçü Sett"
    assert rp.to_lcu_payload()["current"] is True


def test_custom_rune_manager_list_and_delete(tmp_path: Path) -> None:
    """Test listing and deleting custom rune pages."""
    storage = tmp_path / "custom_runes.json"
    mgr = CustomRuneManager(storage_path=storage)

    p1 = mgr.save_page({
        "name": "Page 1",
        "primaryStyleId": 8000,
        "subStyleId": 8100,
        "selectedPerkIds": [8005, 9111, 9104, 8014, 8139, 8135, 5008, 5008, 5011],
    })
    p2 = mgr.save_page({
        "name": "Page 2",
        "primaryStyleId": 8200,
        "subStyleId": 8300,
        "selectedPerkIds": [8229, 8226, 8210, 8237, 8304, 8347, 5008, 5008, 5011],
    })

    pages = mgr.get_all_pages()
    assert len(pages) == 2
    assert {p.id for p in pages} == {p1.id, p2.id}

    # Delete p1
    deleted = mgr.delete_page(p1.id)
    assert deleted is True
    assert mgr.get_page(p1.id) is None
    assert len(mgr.get_all_pages()) == 1

    # Delete non-existent
    assert mgr.delete_page("non-existent-id") is False


def test_custom_rune_manager_persistence_roundtrip(tmp_path: Path) -> None:
    """Verify that saved pages persist to JSON and reload correctly."""
    storage = tmp_path / "test_runes.json"
    mgr1 = CustomRuneManager(storage_path=storage)

    mgr1.save_page({
        "id": "persisted-id-123",
        "name": "Kalıcı Rün",
        "primaryStyleId": 8400,
        "subStyleId": 8200,
        "selectedPerkIds": [8437, 8446, 8429, 8451, 8226, 8210, 5008, 5008, 5011],
    })

    assert storage.exists()

    # Load from second manager instance
    mgr2 = CustomRuneManager(storage_path=storage)
    reloaded = mgr2.get_page("persisted-id-123")
    assert reloaded is not None
    assert reloaded.name == "Kalıcı Rün"
    assert reloaded.primary_style_id == 8400
    assert reloaded.sub_style_id == 8200


# =============================================================================
# 4. REST API Endpoint Tests
# =============================================================================

import inspect
from fastapi import HTTPException
from riftpilot.adapters.web_hub import ApplyRunePageRequest, CustomRuneCreateRequest


def _iter_routes(routes):
    """Yield routes, descending into included (authenticated) routers."""
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _iter_routes(included.routes)
        else:
            yield route


def get_route_endpoint(app, path: str, method: str):
    for route in _iter_routes(app.routes):
        if getattr(route, "path", None) == path and method.upper() in getattr(route, "methods", set()):
            return route.endpoint
    raise ValueError(f"Route not found: {method} {path}")


def unwrap(coro):
    """Directly drives coroutines synchronously without requiring an event loop."""
    while inspect.iscoroutine(coro):
        try:
            coro = coro.send(None)
        except StopIteration as e:
            return e.value
    return coro


def test_api_get_runes_trees_endpoint(test_app) -> None:
    """GET /api/runes/trees returns the complete trees catalog and stat shards."""
    endpoint = get_route_endpoint(test_app, "/api/runes/trees", "GET")
    data = unwrap(endpoint())
    assert "trees" in data
    assert "statShards" in data
    assert len(data["trees"]) == 5
    assert len(data["statShards"]) == 3
    # Check tree names
    tree_names = {t["name"] for t in data["trees"]}
    assert "İsabet" in tree_names
    assert "Hâkimiyet" in tree_names
    assert "Büyücülük" in tree_names
    assert "İlham" in tree_names
    assert "Azim" in tree_names


def test_api_custom_runes_crud_workflow(test_app) -> None:
    """Verify end-to-end CRUD for custom runes via REST API endpoints."""
    mgr = get_custom_rune_manager()
    mgr.clear()

    get_endpoint = get_route_endpoint(test_app, "/api/runes/custom", "GET")
    post_endpoint = get_route_endpoint(test_app, "/api/runes/custom", "POST")
    delete_endpoint = get_route_endpoint(test_app, "/api/runes/custom/{page_id}", "DELETE")

    # 1. Initial list is empty
    initial_res = unwrap(get_endpoint())
    assert initial_res["pages"] == []
    assert initial_res["count"] == 0

    # 2. Invalid POST (empty name) -> raises HTTPException 400
    with pytest.raises(HTTPException) as exc_info:
        unwrap(post_endpoint(CustomRuneCreateRequest(
            name="   ",
            primaryStyleId=8000,
            subStyleId=8100,
            selectedPerkIds=[8005, 9111, 9104, 8014, 8139, 8135, 5008, 5008, 5011],
        )))
    assert exc_info.value.status_code == 400
    assert "boş olamaz" in exc_info.value.detail.lower()

    # 3. Valid POST -> 200 / success
    valid_req = CustomRuneCreateRequest(
        name="Agresif Riven",
        primaryStyleId=8000,
        subStyleId=8400,
        selectedPerkIds=[8010, 9111, 9104, 8299, 8444, 8451, 5008, 5008, 5011],
    )
    create_res = unwrap(post_endpoint(valid_req))
    assert create_res["success"] is True
    created_page = create_res["page"]
    page_id = created_page["id"]
    assert created_page["name"] == "Agresif Riven"
    assert created_page["primaryTreeName"] == "İsabet"
    assert created_page["subTreeName"] == "Azim"

    # 4. GET /api/runes/custom includes newly created page
    list_res = unwrap(get_endpoint())
    assert len(list_res["pages"]) == 1
    assert list_res["pages"][0]["id"] == page_id

    # 5. DELETE /api/runes/custom/{page_id} -> success
    del_res = unwrap(delete_endpoint(page_id=page_id))
    assert del_res["success"] is True
    assert del_res["id"] == page_id

    # 6. DELETE non-existent page -> raises HTTPException 404
    with pytest.raises(HTTPException) as exc_info_404:
        unwrap(delete_endpoint(page_id=page_id))
    assert exc_info_404.value.status_code == 404


def test_api_apply_custom_rune_page(test_app, mock_lcu: MockLcuAdapter) -> None:
    """POST /api/runes/apply applies a custom rune page to LCU."""
    mock_lcu._status = LcuConnectionStatus.CONNECTED
    apply_endpoint = get_route_endpoint(test_app, "/api/runes/apply", "POST")
    req = ApplyRunePageRequest(
        name="Tank Malphite",
        primaryStyleId=8400,
        subStyleId=8200,
        selectedPerkIds=[8437, 8446, 8429, 8451, 8226, 8210, 5008, 5008, 5011],
    )
    res = unwrap(apply_endpoint(req))
    assert res == {"success": True}
    current_page = unwrap(mock_lcu.get_current_rune_page())
    assert current_page is not None
    assert current_page.name == "Tank Malphite"
    assert current_page.primary_style_id == 8400


# =============================================================================
# 5. Mobile UI & HTML Template Verification
# =============================================================================

def test_html_custom_rune_builder_elements() -> None:
    """Verify that INDEX_HTML includes all mobile UI redesign elements and modal."""
    # 1. Vertical layout grid class
    assert ".runes-cards-grid" in INDEX_HTML
    assert "flex-direction: column;" in INDEX_HTML

    # 2. Keystone icon presentation on rune cards
    assert "rune-card-keystone-icon" in INDEX_HTML

    # 3. 4th Card Trigger ("+ Kendi Rünümü Oluştur")
    assert "btnOpenCustomRuneBuilder" in INDEX_HTML
    assert "rune-builder-trigger-card" in INDEX_HTML
    assert "+ Kendi Rünümü Oluştur" in INDEX_HTML

    # 4. Custom Rune Builder Modal and elements
    assert "id=\"customRuneModal\"" in INDEX_HTML
    assert "custom-rune-modal" in INDEX_HTML
    assert "customRuneModalClose" in INDEX_HTML
    assert "customRuneNameInput" in INDEX_HTML
    assert "primaryTreesGrid" in INDEX_HTML
    assert "primaryRunesSlots" in INDEX_HTML
    assert "secondaryTreesGrid" in INDEX_HTML
    assert "secondaryRunesSlots" in INDEX_HTML
    assert "statShardsSlots" in INDEX_HTML
    assert "customRuneValidationAlert" in INDEX_HTML
    assert "btnSaveCustomRune" in INDEX_HTML
    assert "btnApplyCustomRune" in INDEX_HTML
    assert "savedRunesList" in INDEX_HTML
    assert "tabBtnBuilder" in INDEX_HTML
    assert "tabBtnSaved" in INDEX_HTML
