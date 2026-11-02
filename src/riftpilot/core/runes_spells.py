"""RiftPilot Core Domain — Runes (Perks) and Summoner Spells Models.

Defines contracts and data structures for managing:
- Game-mode-aware Summoner spells selection (Flash, Ignite, Teleport, Smite, Mark/Snowball, etc.)
- Rune pages, rune trees, and perk metadata
- Official League Client recommended rune pages (`RecommendedRunePage`)
in accordance with Hexagonal Architecture (ADR-0003).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SummonerSpell:
    """Represents a selectable League of Legends summoner spell with mode eligibility."""

    id: int
    name: str
    key: str
    description: str = ""
    game_modes: list[str] = field(default_factory=list)
    icon_path: str = ""

    @property
    def icon_url(self) -> str:
        """CommunityDragon / DataDragon CDN icon URL."""
        return f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/data/spells/icons2d/{self.key.lower()}.png"

    def is_available_in_mode(self, game_mode: str) -> bool:
        """Return True if this spell is allowed in the specified LCU gameMode."""
        if not game_mode:
            return True
        normalized = game_mode.strip().upper()
        # ARAM Şamata / KIWI shares ARAM summoner spell pool if KIWI not explicitly listed
        if not self.game_modes:
            return True
        modes_upper = {m.upper() for m in self.game_modes}
        if normalized in modes_upper:
            return True
        if normalized in ("KIWI", "ARAM_BRAWL") and "ARAM" in modes_upper:
            return True
        return False

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> SummonerSpell:
        """Parse a `SummonerSpell` from `/lol-game-data/assets/v1/summoner-spells.json` entry."""
        spell_id = int(data.get("id", 0) or 0)
        icon_path = str(data.get("iconPath", "") or "")
        # Extract canonical spell key from iconPath or fallback to STANDARD_SPELLS
        key = str(data.get("key", "") or "")
        if not key and icon_path:
            filename = icon_path.rsplit("/", 1)[-1]
            stem = filename.split(".")[0]
            if stem:
                key = stem
        if not key and spell_id in STANDARD_SPELLS:
            key = STANDARD_SPELLS[spell_id].key
        if not key:
            key = f"SummonerSpell{spell_id}"

        raw_modes = data.get("gameModes") or []
        game_modes = [str(m).upper() for m in raw_modes if isinstance(m, str) and m.strip()]

        name = str(data.get("name", "") or "").strip()
        if not name and spell_id in STANDARD_SPELLS:
            name = STANDARD_SPELLS[spell_id].name

        description = str(data.get("description", "") or "").strip()
        if not description and spell_id in STANDARD_SPELLS:
            description = STANDARD_SPELLS[spell_id].description

        return cls(
            id=spell_id,
            name=name or f"Büyü #{spell_id}",
            key=key,
            description=description,
            game_modes=game_modes,
            icon_path=icon_path,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize summoner spell to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "key": self.key,
            "description": self.description,
            "gameModes": list(self.game_modes),
            "game_modes": list(self.game_modes),
            "iconUrl": self.icon_url,
            "icon_url": self.icon_url,
        }


# Standard Summoner Spells Catalog with official LCU gameModes metadata
STANDARD_SPELLS: dict[int, SummonerSpell] = {
    4: SummonerSpell(
        id=4,
        name="Sıçra",
        key="SummonerFlash",
        description="Şampiyonunu hedef konuma doğru kısa bir mesafe ışınlar.",
        game_modes=["CLASSIC", "ARAM", "KIWI", "URF", "ONEFORALL", "NEXUSBLITZ", "ULTBOOK", "PRACTICETOOL", "TUTORIAL"],
    ),
    14: SummonerSpell(
        id=14,
        name="Tutuştur",
        key="SummonerDot",
        description="Hedef rakip şampiyonu yakarak gerçek hasar verir ve Ağır Yara açar.",
        game_modes=["CLASSIC", "ARAM", "KIWI", "URF", "ONEFORALL", "NEXUSBLITZ", "ULTBOOK", "PRACTICETOOL"],
    ),
    12: SummonerSpell(
        id=12,
        name="Işınlan",
        key="SummonerTeleport",
        description="Kısa bir odaklanmanın ardından hedef dost yapıya veya minyona ışınlar.",
        game_modes=["CLASSIC", "ONEFORALL", "ULTBOOK", "PRACTICETOOL", "TUTORIAL"],
    ),
    11: SummonerSpell(
        id=11,
        name="Çarp",
        key="SummonerSmite",
        description="Hedef canavara veya minyona gerçek hasar verir.",
        game_modes=["CLASSIC", "URF", "ONEFORALL", "NEXUSBLITZ", "ULTBOOK", "PRACTICETOOL"],
    ),
    7: SummonerSpell(
        id=7,
        name="Şifa",
        key="SummonerHeal",
        description="Şampiyonunu ve hedef bir dost şampiyonu iyileştirir ve hareket hızı sağlar.",
        game_modes=["CLASSIC", "ARAM", "KIWI", "URF", "ONEFORALL", "NEXUSBLITZ", "ULTBOOK", "PRACTICETOOL", "TUTORIAL"],
    ),
    21: SummonerSpell(
        id=21,
        name="Bariyer",
        key="SummonerBarrier",
        description="Şampiyonunu kısa süreliğine hasardan koruyan bir kalkan verir.",
        game_modes=["CLASSIC", "ARAM", "KIWI", "URF", "ONEFORALL", "NEXUSBLITZ", "ULTBOOK", "PRACTICETOOL"],
    ),
    1: SummonerSpell(
        id=1,
        name="Arındır",
        key="SummonerBoost",
        description="Şampiyonunun üzerindeki tüm kitle kontrol etkilerini kaldırır.",
        game_modes=["CLASSIC", "ARAM", "KIWI", "URF", "ONEFORALL", "NEXUSBLITZ", "ULTBOOK", "PRACTICETOOL"],
    ),
    3: SummonerSpell(
        id=3,
        name="Bitkinlik",
        key="SummonerExhaust",
        description="Hedef rakip şampiyonun hareket hızını ve hasarını azaltır.",
        game_modes=["CLASSIC", "ARAM", "KIWI", "URF", "ONEFORALL", "NEXUSBLITZ", "ULTBOOK", "PRACTICETOOL"],
    ),
    6: SummonerSpell(
        id=6,
        name="Hayalet",
        key="SummonerHaste",
        description="Büyük miktarda hareket hızı sağlar ve birimlerin içinden geçebilmeyi sağlar.",
        game_modes=["CLASSIC", "ARAM", "KIWI", "URF", "ONEFORALL", "NEXUSBLITZ", "ULTBOOK", "PRACTICETOOL", "TUTORIAL"],
    ),
    13: SummonerSpell(
        id=13,
        name="Berraklık",
        key="SummonerMana",
        description="Şampiyonunun ve yakındaki dostların manasını doldurur.",
        game_modes=["ARAM", "KIWI"],
    ),
    32: SummonerSpell(
        id=32,
        name="İşaret / Atıl (Kartopu)",
        key="SummonerSnowball",
        description="Hedefe bir kartopu fırlatır ve çarparsa hedefe atılmanı sağlar.",
        game_modes=["ARAM", "KIWI"],
    ),
}


def get_all_summoner_spells() -> list[SummonerSpell]:
    """Retrieve full catalog of standard summoner spells."""
    return list(STANDARD_SPELLS.values())


def get_spells_for_mode(
    game_mode: str,
    catalog: list[SummonerSpell] | None = None,
) -> list[SummonerSpell]:
    """Filter summoner spells to only those valid for the given `game_mode`."""
    source = catalog if catalog is not None else list(STANDARD_SPELLS.values())
    if not game_mode or game_mode.upper() == "UNKNOWN":
        return list(source)
    filtered = [spell for spell in source if spell.is_available_in_mode(game_mode)]
    return filtered


def get_summoner_spell(spell_id: int) -> SummonerSpell | None:
    """Find a summoner spell by numeric ID."""
    return STANDARD_SPELLS.get(spell_id)


# Primary Rune Trees
RUNE_TREES: dict[int, str] = {
    8000: "İsabet",
    8100: "Hâkimiyet",
    8200: "Büyücülük",
    8300: "İlham",
    8400: "Azim",
}

# Known Perk / Keystone Names for rich mobile display
PERK_NAMES: dict[int, str] = {
    # Precision Keystones & Perks
    8005: "Saldırıya Devam",
    8008: "Ölümcül Tempo",
    8021: "Ayağı Çabuk",
    8010: "Yenilmez",
    9101: "Şifa Taşkını",
    9111: "Zafer",
    8009: "Pratik Zekâ",
    9104: "Efsane: Çeviklik",
    9105: "Efsane: Sıvışma",
    9103: "Efsane: Kan",
    9107: "Efsane: İvme",
    8014: "Son Darbe",
    8017: "Biç Devir",
    8299: "Son Direniş",
    # Domination Keystones & Perks
    8112: "Elektrik Ver",
    8128: "Kara Hasat",
    9923: "Keskin Sağanak",
    8126: "Belden Aşağı",
    8139: "Kan Tadı",
    8143: "Ani Darbe",
    8137: "Altıncı His",
    8140: "Grisly Mementos",
    8141: "Deep Ward",
    8138: "Gözyuvarı Koleksiyonu",
    8135: "Hazine Avcısı",
    8105: "İnsafsız Avcı",
    8106: "Üstün Avcı",
    # Sorcery Keystones & Perks
    8214: "Aery'yi Çağır",
    8229: "Sihirli Yıldız",
    8230: "Sürat Coşkusu",
    8224: "Soğuran Küre",
    8226: "Mana Akışı Yüzüğü",
    8275: "Bulut Pelerini",
    8210: "Yücelik",
    8234: "Sürat",
    8233: "Mutlak Odak",
    8237: "Kızart",
    8232: "Suda Yürüyen",
    8236: "Yaklaşan Fırtına",
    # Inspiration Keystones & Perks
    8351: "Buzul Takviyesi",
    8360: "Dizginsiz Büyü Kitabı",
    8369: "İlk Vuruş",
    8306: "Hextech Sıçratıcı",
    8304: "Sihirli Pabuçlar",
    8321: "Nakit İadesi",
    8313: "Üçlü Tonik",
    8352: "Zaman Büken Karışım",
    8345: " Peksimet Teslimatı",
    8347: "Kozmik Sezgi",
    8410: "Yaklaşım Hızı",
    8316: "Her Şeyin Çaresi",
    # Resolve Keystones & Perks
    8437: "Hortlağın Pençesi",
    8439: "Artçı Şok",
    8465: "Muhafız",
    8446: "Tahrip",
    8463: "Yaşam Kaynağı",
    8401: "Kalkan Darbesi",
    8429: "Kondisyon",
    8444: "İkinci Şans",
    8473: "Kemik Zırh",
    8451: "Aşırı Büyüme",
    8453: "Canlandır",
    8242: "Metanet",
    # Stat Mods
    5008: "Değişken Kuvvet",
    5005: "Saldırı Hızı",
    5007: "Yetenek Hızı",
    5010: "Hareket Hızı",
    5001: "Taban Can",
    5002: "Zırh",
    5003: "Büyü Direnci",
    5011: "Büyüyen Can",
    5013: "Sıvışma ve Yavaşlatma Direnci",
}


def get_rune_tree_name(style_id: int) -> str:
    """Return human-readable Turkish rune tree name."""
    return RUNE_TREES.get(style_id, f"Yol #{style_id}")


def get_perk_name(perk_id: int) -> str:
    """Return human-readable Turkish perk/keystone name if known."""
    return PERK_NAMES.get(perk_id, f"Rün #{perk_id}")


@dataclass
class RunePage:
    """Represents a complete League of Legends rune page."""

    id: int = 0
    name: str = "RiftPilot Rün Sayfası"
    primary_style_id: int = 8000
    sub_style_id: int = 8100
    selected_perk_ids: list[int] = field(default_factory=list)
    current: bool = True
    is_editable: bool = True

    @classmethod
    def from_lcu_dict(cls, data: dict[str, Any]) -> RunePage:
        """Parse from raw LCU /lol-perks/v1 page dictionary."""
        return cls(
            id=int(data.get("id", 0) or 0),
            name=str(data.get("name", "RiftPilot") or "RiftPilot"),
            primary_style_id=int(data.get("primaryStyleId", 8000) or 8000),
            sub_style_id=int(data.get("subStyleId", 8100) or 8100),
            selected_perk_ids=[int(x) for x in (data.get("selectedPerkIds") or [])],
            current=bool(data.get("current", True)),
            is_editable=bool(data.get("isEditable", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize rune page with both camelCase and snake_case keys."""
        return {
            "id": self.id,
            "name": self.name,
            "primaryStyleId": self.primary_style_id,
            "primary_style_id": self.primary_style_id,
            "primaryStyleName": get_rune_tree_name(self.primary_style_id),
            "subStyleId": self.sub_style_id,
            "sub_style_id": self.sub_style_id,
            "subStyleName": get_rune_tree_name(self.sub_style_id),
            "selectedPerkIds": list(self.selected_perk_ids),
            "selected_perk_ids": list(self.selected_perk_ids),
            "current": self.current,
            "isEditable": self.is_editable,
            "is_editable": self.is_editable,
        }

    def to_lcu_payload(self) -> dict[str, Any]:
        """Format as strict LCU PUT/POST payload."""
        return {
            "name": self.name,
            "primaryStyleId": self.primary_style_id,
            "subStyleId": self.sub_style_id,
            "selectedPerkIds": self.selected_perk_ids,
            "current": True,
        }


@dataclass
class RecommendedRunePage:
    """Represents one of the official rune page recommendations returned by the LCU client.

    Note: The application NEVER fabricates or auto-applies these pages; they are parsed
    directly from the League Client's perk recommender endpoints.
    """

    name: str
    primary_style_id: int
    sub_style_id: int
    selected_perk_ids: list[int] = field(default_factory=list)
    keystone_id: int = 0
    keystone_name: str = ""
    primary_tree_name: str = ""
    sub_tree_name: str = ""
    perk_names: list[str] = field(default_factory=list)
    position: str = ""
    champion_id: int = 0

    @classmethod
    def from_lcu_dict(
        cls,
        data: dict[str, Any],
        champion_id: int = 0,
        index: int = 0,
    ) -> RecommendedRunePage | None:
        """Parse an official LCU recommended rune page dict.

        Supports both direct page payloads (`primaryStyleId`, `subStyleId`, `selectedPerkIds`)
        and nested recommender payloads (`perks` list of objects or IDs, `primaryPerkStyleId`, etc.).
        Returns None if required fields are missing or invalid.
        """
        if not isinstance(data, dict):
            return None

        primary_id = int(
            data.get("primaryStyleId")
            or data.get("primaryPerkStyleId")
            or 0
        )
        sub_id = int(
            data.get("subStyleId")
            or data.get("secondaryPerkStyleId")
            or data.get("subPerkStyleId")
            or 0
        )

        # Extract perk IDs and optional names from LCU payload
        raw_perks = data.get("selectedPerkIds") or data.get("perks") or []
        perk_ids: list[int] = []
        perk_names: list[str] = []

        for item in raw_perks:
            if isinstance(item, int):
                perk_ids.append(item)
                perk_names.append(get_perk_name(item))
            elif isinstance(item, dict):
                pid = int(item.get("id") or item.get("perkId") or 0)
                if pid > 0:
                    perk_ids.append(pid)
                    pname = str(item.get("name") or item.get("shortDesc") or "").strip()
                    perk_names.append(pname or get_perk_name(pid))

        if primary_id <= 0 or not perk_ids:
            return None

        keystone_obj = data.get("keystone")
        keystone_id = perk_ids[0]
        keystone_name = ""
        if isinstance(keystone_obj, dict):
            keystone_id = int(keystone_obj.get("id") or keystone_id)
            keystone_name = str(keystone_obj.get("name") or "").strip()
        if not keystone_name:
            keystone_name = perk_names[0] if perk_names else get_perk_name(keystone_id)

        primary_tree_name = str(
            data.get("primaryStyleName")
            or get_rune_tree_name(primary_id)
        )
        sub_tree_name = str(
            data.get("subStyleName")
            or get_rune_tree_name(sub_id)
        )

        raw_name = str(data.get("name") or "").strip()
        if not raw_name:
            raw_name = f"{keystone_name} ({primary_tree_name} + {sub_tree_name})"

        position = str(data.get("position") or "").strip().upper()
        rec_champ_id = int(data.get("recommendedChampionId") or champion_id or 0)

        return cls(
            name=raw_name,
            primary_style_id=primary_id,
            sub_style_id=sub_id,
            selected_perk_ids=perk_ids,
            keystone_id=keystone_id,
            keystone_name=keystone_name,
            primary_tree_name=primary_tree_name,
            sub_tree_name=sub_tree_name,
            perk_names=perk_names,
            position=position,
            champion_id=rec_champ_id,
        )

    def to_rune_page(self) -> RunePage:
        """Convert this recommendation into an editable `RunePage` ready for LCU application."""
        return RunePage(
            name=self.name,
            primary_style_id=self.primary_style_id,
            sub_style_id=self.sub_style_id,
            selected_perk_ids=list(self.selected_perk_ids),
            current=True,
            is_editable=True,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize recommended rune page with full details for mobile UI."""
        return {
            "name": self.name,
            "primaryStyleId": self.primary_style_id,
            "primary_style_id": self.primary_style_id,
            "primaryTreeName": self.primary_tree_name,
            "primary_tree_name": self.primary_tree_name,
            "subStyleId": self.sub_style_id,
            "sub_style_id": self.sub_style_id,
            "subTreeName": self.sub_tree_name,
            "sub_tree_name": self.sub_tree_name,
            "selectedPerkIds": list(self.selected_perk_ids),
            "selected_perk_ids": list(self.selected_perk_ids),
            "keystoneId": self.keystone_id,
            "keystone_id": self.keystone_id,
            "keystoneName": self.keystone_name,
            "keystone_name": self.keystone_name,
            "perkNames": list(self.perk_names),
            "perk_names": list(self.perk_names),
            "position": self.position,
            "championId": self.champion_id,
            "champion_id": self.champion_id,
            "page": self.to_rune_page().to_dict(),
        }


__all__ = [
    "PERK_NAMES",
    "RUNE_TREES",
    "RecommendedRunePage",
    "RunePage",
    "STANDARD_SPELLS",
    "SummonerSpell",
    "get_all_summoner_spells",
    "get_perk_name",
    "get_rune_tree_name",
    "get_spells_for_mode",
    "get_summoner_spell",
]
