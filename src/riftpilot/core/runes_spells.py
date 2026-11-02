"""RiftPilot Core Domain — Runes (Perks) and Summoner Spells Models.

Defines contracts and data structures for managing:
- Game-mode-aware Summoner spells selection (Flash, Ignite, Teleport, Smite, Mark/Snowball, etc.)
- Rune pages, rune trees, and perk metadata
- Official League Client recommended rune pages (`RecommendedRunePage`)
in accordance with Hexagonal Architecture (ADR-0003).
"""

from __future__ import annotations

import json
from pathlib import Path
import secrets
import time
import uuid
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
    8136: "Zombi Totem",
    8120: "Hayalet Poro",
    8134: "Usta Avcı",
    8124: "Yırtıcı",
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
    8345: "Peksimet Teslimatı",
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



# Complete League of Legends Runes Reforged Catalog (Trees, Slots, Keystones, Minors & Stat Shards)
RUNE_DATA: dict[str, Any] = {
    "trees": {
        8000: {
            "id": 8000,
            "name": "İsabet",
            "key": "Precision",
            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/7201_precision.png",
            "color": "#c8aa6e",
            "slots": [
                {
                    "row": 0,
                    "type": "keystone",
                    "runes": [
                        {
                            "id": 8005,
                            "name": "Saldırıya Devam",
                            "key": "PressTheAttack",
                            "desc": "Hedefe ardışık 3 normal saldırı ek hasar verir ve hedefin aldığı hasarı arttırır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/presstheattack/presstheattack.png",
                        },
                        {
                            "id": 8008,
                            "name": "Ölümcül Tempo",
                            "key": "LethalTempo",
                            "desc": "Saldırı yaptıkça saldırı hızı kazandırır, azami yüke ulaşınca ilave menzil sağlar.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/lethaltempo/lethaltempotemp.png",
                        },
                        {
                            "id": 8021,
                            "name": "Ayağı Çabuk",
                            "key": "FleetFootwork",
                            "desc": "Saldırı ve hareket ile Enerji birikir. Enerji dolunca saldırmak iyileştirir ve hareket hızı verir.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/fleetfootwork/fleetfootwork.png",
                        },
                        {
                            "id": 8010,
                            "name": "Yenilmez",
                            "key": "Conqueror",
                            "desc": "Düşman şampiyonlara hasar vererek Değişken Kuvvet yükü kazandırır. Azami yükte iyileşme sağlar.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/conqueror/conqueror.png",
                        },
                    ],
                },
                {
                    "row": 1,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 9101,
                            "name": "Şifa Taşkını",
                            "key": "Overheal",
                            "desc": "Fazlalık iyileşme veya can çalma kalkan sağlar.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/absorblife/absorblife.png",
                        },
                        {
                            "id": 9111,
                            "name": "Zafer",
                            "key": "Triumph",
                            "desc": "Şampiyon alt etmeler eksik canının %2.5'ini ve 20 ilave altın yeniler.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/triumph.png",
                        },
                        {
                            "id": 8009,
                            "name": "Pratik Zekâ",
                            "key": "PresenceOfMind",
                            "desc": "Düşman şampiyonlara hasar vermek mana/enerji yenilenmesini hızlandırır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/presenceofmind/presenceofmind.png",
                        },
                    ],
                },
                {
                    "row": 2,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 9104,
                            "name": "Efsane: Çeviklik",
                            "key": "LegendAlacrity",
                            "desc": "Alt etmeler kalıcı saldırı hızı kazandırır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/legendalacrity/legendalacrity.png",
                        },
                        {
                            "id": 9105,
                            "name": "Efsane: Sıvışma",
                            "key": "LegendTenacity",
                            "desc": "Alt etmeler kalıcı sıvışma sağlar.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/legendtenacity/legendtenacity.png",
                        },
                        {
                            "id": 9103,
                            "name": "Efsane: Kan",
                            "key": "LegendBloodline",
                            "desc": "Alt etmeler kalıcı can çalma ve azami can sağlar.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/legendbloodline/legendbloodline.png",
                        },
                        {
                            "id": 9107,
                            "name": "Efsane: İvme",
                            "key": "LegendHaste",
                            "desc": "Alt etmeler temel yetenekler için yetenek hızı kazandırır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/legendhaste/legendhaste.png",
                        },
                    ],
                },
                {
                    "row": 3,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 8014,
                            "name": "Son Darbe",
                            "key": "CoupDeGrace",
                            "desc": "Canı düşük düşman şampiyonlara %8 daha fazla hasar ver.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/coupdegrace/coupdegrace.png",
                        },
                        {
                            "id": 8017,
                            "name": "Biç Devir",
                            "key": "CutDown",
                            "desc": "Canı %60'ın üzerindeki rakiplere ilave hasar verir.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/cutdown/cutdown.png",
                        },
                        {
                            "id": 8299,
                            "name": "Son Direniş",
                            "key": "LastStand",
                            "desc": "Kendi canın düşükken (%60 altı) şampiyonlara ilave hasar verir.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/precision/laststand/laststand.png",
                        },
                    ],
                },
            ],
        },
        8100: {
            "id": 8100,
            "name": "Hâkimiyet",
            "key": "Domination",
            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/7200_domination.png",
            "color": "#dc2626",
            "slots": [
                {
                    "row": 0,
                    "type": "keystone",
                    "runes": [
                        {
                            "id": 8112,
                            "name": "Elektrik Ver",
                            "key": "Electrocute",
                            "desc": "3 saniye içinde 3 ayrı saldırı veya yetenek isabet ettirmek ilave değişken hasar verir.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/domination/electrocute/electrocute.png",
                        },
                        {
                            "id": 8128,
                            "name": "Kara Hasat",
                            "key": "DarkHarvest",
                            "desc": "Canı düşük şampiyonlara hasar vermek değişken hasar uygular ve ruhlarını biçer.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/domination/darkharvest/darkharvest.png",
                        },
                        {
                            "id": 9923,
                            "name": "Keskin Sağanak",
                            "key": "HailOfBlades",
                            "desc": "Rakip şampiyonlara saldırdığında sonraki 3 normal saldırın için büyük oranda saldırı hızı sağlar.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/domination/hailofblades/hailofblades.png",
                        },
                    ],
                },
                {
                    "row": 1,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 8126,
                            "name": "Belden Aşağı",
                            "key": "CheapShot",
                            "desc": "Hareketi veya eylemleri kısıtlanan şampiyonlara ilave gerçek hasar ver.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/domination/cheapshot/cheapshot.png",
                        },
                        {
                            "id": 8139,
                            "name": "Kan Tadı",
                            "key": "TasteOfBlood",
                            "desc": "Bir rakip şampiyona hasar verdiğinde iyileş.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/domination/tasteofblood/greenterror_tasteofblood.png",
                        },
                        {
                            "id": 8143,
                            "name": "Ani Darbe",
                            "key": "SuddenImpact",
                            "desc": "Atılma, sıçrama kullandıktan veya görünmezlikten çıktıktan sonra ilave gerçek hasar ver.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/domination/suddenimpact/suddenimpact.png",
                        },
                    ],
                },
                {
                    "row": 2,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 8136,
                            "name": "Zombi Totem",
                            "key": "ZombieWard",
                            "desc": "Rakip totemleri yok ettikten sonra yerine dost Zombi Totem belirir.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/domination/zombieward/zombieward.png",
                        },
                        {
                            "id": 8120,
                            "name": "Hayalet Poro",
                            "key": "GhostPoro",
                            "desc": "Totemlerin süresi dolduğunda görüş sağlayan sevimli Hayalet Poro bırakır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/domination/ghostporo/ghostporo.png",
                        },
                        {
                            "id": 8138,
                            "name": "Gözyuvarı Koleksiyonu",
                            "key": "EyeballCollection",
                            "desc": "Şampiyon alt etmelerinde göz yuvarı toplayarak kalıcı Değişken Kuvvet kazanırsın.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/domination/eyeballcollection/eyeballcollection.png",
                        },
                    ],
                },
                {
                    "row": 3,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 8135,
                            "name": "Hazine Avcısı",
                            "key": "TreasureHunter",
                            "desc": "Benzersiz alt etmeler ilave altın kazandırır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/domination/treasurehunter/treasurehunter.png",
                        },
                        {
                            "id": 8134,
                            "name": "Usta Avcı",
                            "key": "IngeniousHunter",
                            "desc": "Benzersiz alt etmeler aktif eşya ve ziynet hızını arttırır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/domination/ingenioushunter/ingenioushunter.png",
                        },
                        {
                            "id": 8105,
                            "name": "İnsafsız Avcı",
                            "key": "RelentlessHunter",
                            "desc": "Benzersiz alt etmeler çatışma dışı hareket hızı sağlar.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/domination/relentlesshunter/relentlesshunter.png",
                        },
                        {
                            "id": 8106,
                            "name": "Üstün Avcı",
                            "key": "UltimateHunter",
                            "desc": "Benzersiz alt etmeler ulti yeteneğinin bekleme süresini azaltır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/domination/ultimatehunter/ultimatehunter.png",
                        },
                    ],
                },
            ],
        },
        8200: {
            "id": 8200,
            "name": "Büyücülük",
            "key": "Sorcery",
            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/7202_sorcery.png",
            "color": "#3b82f6",
            "slots": [
                {
                    "row": 0,
                    "type": "keystone",
                    "runes": [
                        {
                            "id": 8214,
                            "name": "Aery'yi Çağır",
                            "key": "SummonAery",
                            "desc": "Saldırı ve yeteneklerin rakiplere hasar verir veya dostlara kalkan sunar.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/sorcery/summonaery/summonaery.png",
                        },
                        {
                            "id": 8229,
                            "name": "Sihirli Yıldız",
                            "key": "ArcaneComet",
                            "desc": "Bir şampiyona yetenek isabet ettirmek konumuna bir kuyrukluyıldız fırlatır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/sorcery/arcanecomet/arcanecomet.png",
                        },
                        {
                            "id": 8230,
                            "name": "Sürat Coşkusu",
                            "key": "PhaseRush",
                            "desc": "3 ayrı saldırı veya yetenek isabet ettirmek büyük hareket hızı ve yavaşlatma direnci verir.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/sorcery/phaserush/phaserush.png",
                        },
                    ],
                },
                {
                    "row": 1,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 8224,
                            "name": "Soğuran Küre",
                            "key": "NullifyingOrb",
                            "desc": "Büyü hasarı canını azalttığında koruyucu büyü kalkanı kazanırsın.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/sorcery/nullifyingorb/pokeshield.png",
                        },
                        {
                            "id": 8226,
                            "name": "Mana Akışı Yüzüğü",
                            "key": "ManaflowBand",
                            "desc": "Rakip şampiyona bir yetenek isabet ettirmek kalıcı olarak azami mananı arttırır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/sorcery/manaflowband/manaflowband.png",
                        },
                        {
                            "id": 8275,
                            "name": "Bulut Pelerini",
                            "key": "NimbusCloak",
                            "desc": "Bir sihirdar büyüsü kullandıktan sonra kısa süreliğine hareket hızı kazanırsın.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/sorcery/nimbuscloak/6361.png",
                        },
                    ],
                },
                {
                    "row": 2,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 8210,
                            "name": "Yücelik",
                            "key": "Transcendence",
                            "desc": "Seviye atladıkça yetenek hızı kazandırır. Alt etmeler bekleme sürelerini azaltır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/sorcery/transcendence/transcendence.png",
                        },
                        {
                            "id": 8234,
                            "name": "Sürat",
                            "key": "Celerity",
                            "desc": "Tüm hareket hızı ilavelerini daha etkili kılar ve hareket hızı sağlar.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/sorcery/celerity/celerity.png",
                        },
                        {
                            "id": 8233,
                            "name": "Mutlak Odak",
                            "key": "AbsoluteFocus",
                            "desc": "Canın %70'in üzerindeyken ilave Değişken Kuvvet verir.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/sorcery/absolutefocus/absolutefocus.png",
                        },
                    ],
                },
                {
                    "row": 3,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 8237,
                            "name": "Kızart",
                            "key": "Scorch",
                            "desc": "İsabet eden yetenekler rakipleri yakarak ilave büyü hasarı verir.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/sorcery/scorch/scorch.png",
                        },
                        {
                            "id": 8232,
                            "name": "Suda Yürüyen",
                            "key": "Waterwalking",
                            "desc": "Nehirdeyken hareket hızı ve Değişken Kuvvet kazanırsın.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/sorcery/waterwalking/waterwalking.png",
                        },
                        {
                            "id": 8236,
                            "name": "Yaklaşan Fırtına",
                            "key": "GatheringStorm",
                            "desc": "Oyun süresince her 10 dakikada bir artan miktarda Değişken Kuvvet kazanırsın.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/sorcery/gatheringstorm/gatheringstorm.png",
                        },
                    ],
                },
            ],
        },
        8300: {
            "id": 8300,
            "name": "İlham",
            "key": "Inspiration",
            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/7203_whimsy.png",
            "color": "#06b6d4",
            "slots": [
                {
                    "row": 0,
                    "type": "keystone",
                    "runes": [
                        {
                            "id": 8351,
                            "name": "Buzul Takviyesi",
                            "key": "GlacialAugment",
                            "desc": "Düşman şampiyonu hareketsiz kılmak donuk ışınlar yayarak alanı yavaşlatır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/inspiration/glacialaugment/glacialaugment.png",
                        },
                        {
                            "id": 8360,
                            "name": "Dizginsiz Büyü Kitabı",
                            "key": "UnsealedSpellbook",
                            "desc": "Çatışma dışındayken tek kullanımlık farklı bir sihirdar büyüsü kuşanabilirsin.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/inspiration/unsealedspellbook/unsealedspellbook.png",
                        },
                        {
                            "id": 8369,
                            "name": "İlk Vuruş",
                            "key": "FirstStrike",
                            "desc": "Rakiple çatışmayı ilk başlatan sen olursan ek hasar ve altın kazanırsın.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/inspiration/firststrike/firststrike.png",
                        },
                    ],
                },
                {
                    "row": 1,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 8306,
                            "name": "Hextech Sıçratıcı",
                            "key": "HextechFlashtraption",
                            "desc": "Sıçra bekleme süresindeyken yoğunlaşarak Hex-Sıçra kullan.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/inspiration/hextechflashtraption/hextechflashtraption.png",
                        },
                        {
                            "id": 8304,
                            "name": "Sihirli Pabuçlar",
                            "key": "MagicalFootwear",
                            "desc": "12. dakikada bedelsiz Çizme edinirsin (alt etmeler süreyi 45 sn kısaltır).",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/inspiration/magicalfootwear/magicalfootwear.png",
                        },
                        {
                            "id": 8321,
                            "name": "Nakit İadesi",
                            "key": "CashBack",
                            "desc": "Efsanevi eşya satın aldığında altın bedelinin %6'sını geri alırsın.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/inspiration/cashback/cashback.png",
                        },
                    ],
                },
                {
                    "row": 2,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 8313,
                            "name": "Üçlü Tonik",
                            "key": "TripleTonic",
                            "desc": "3, 6 ve 9. seviyelerde sana özel iksirler kazandırır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/inspiration/tripletonic/tripletonic.png",
                        },
                        {
                            "id": 8352,
                            "name": "Zaman Büken Karışım",
                            "key": "TimeWarpTonic",
                            "desc": "İksirler anında bir miktar can yeniler.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/inspiration/timewarptonic/timewarptonic.png",
                        },
                        {
                            "id": 8345,
                            "name": "Peksimet Teslimatı",
                            "key": "BiscuitDelivery",
                            "desc": "Düzenli aralıklarla can ve mana yenileyen peksimetler edinirsin.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/inspiration/biscuitdelivery/biscuitdelivery.png",
                        },
                    ],
                },
                {
                    "row": 3,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 8347,
                            "name": "Kozmik Sezgi",
                            "key": "CosmicInsight",
                            "desc": "Sihirdar büyüsü hızı ve eşya hızı kazandırır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/inspiration/cosmicinsight/cosmicinsight.png",
                        },
                        {
                            "id": 8410,
                            "name": "Yaklaşım Hızı",
                            "key": "ApproachVelocity",
                            "desc": "Hareketi kısıtlanmış rakiplere doğru koşarken ilave hareket hızı elde et.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/inspiration/approachvelocity/approachvelocity.png",
                        },
                        {
                            "id": 8316,
                            "name": "Her Şeyin Çaresi",
                            "key": "JackOfAllTrades",
                            "desc": "Satın aldığın her farklı eşya niteliği için yetenek hızı ve değişken kuvvet alırsın.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/inspiration/jackofalltrades/jackofalltrades.png",
                        },
                    ],
                },
            ],
        },
        8400: {
            "id": 8400,
            "name": "Azim",
            "key": "Resolve",
            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/7204_resolve.png",
            "color": "#10b981",
            "slots": [
                {
                    "row": 0,
                    "type": "keystone",
                    "runes": [
                        {
                            "id": 8437,
                            "name": "Hortlağın Pençesi",
                            "key": "GraspOfTheUndying",
                            "desc": "Çatışmada her 4 sn'de normal saldırın ek hasar verir, iyileştirir ve kalıcı can kazandırır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/resolve/graspoftheundying/graspoftheundying.png",
                        },
                        {
                            "id": 8439,
                            "name": "Artçı Şok",
                            "key": "Aftershock",
                            "desc": "Bir şampiyonu hareketsiz bıraktığında savunma kazanır ve patlayarak hasar verirsin.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/resolve/veteranaftershock/veteranaftershock.png",
                        },
                        {
                            "id": 8465,
                            "name": "Muhafız",
                            "key": "Guardian",
                            "desc": "Yakındaki bir dost şampiyon hasar aldığında ikinize de kalkan sağlar.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/resolve/guardian/guardian.png",
                        },
                    ],
                },
                {
                    "row": 1,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 8446,
                            "name": "Tahrip",
                            "key": "Demolish",
                            "desc": "Kulelerin yakınındayken kuleye ağır hasar veren bir darbe yüklersin.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/resolve/demolish/demolish.png",
                        },
                        {
                            "id": 8463,
                            "name": "Yaşam Kaynağı",
                            "key": "FontOfLife",
                            "desc": "Rakipleri yavaşlatmak dostların onlara saldırırken can yenilemesini sağlar.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/resolve/fontoflife/fontoflife.png",
                        },
                        {
                            "id": 8401,
                            "name": "Kalkan Darbesi",
                            "key": "ShieldBash",
                            "desc": "Kalkan kazandığında direnç elde eder ve bir sonraki saldırına ek hasar eklersin.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/resolve/mirror_shell/shieldbash.png",
                        },
                    ],
                },
                {
                    "row": 2,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 8429,
                            "name": "Kondisyon",
                            "key": "Conditioning",
                            "desc": "12. dakikadan sonra ilave Zırh ve Büyü Direnci kazanırsın.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/resolve/conditioning/conditioning.png",
                        },
                        {
                            "id": 8444,
                            "name": "İkinci Şans",
                            "key": "SecondWind",
                            "desc": "Hasar aldıktan sonra eksik canının bir kısmını zamanla yenilersin.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/resolve/secondwind/secondwind.png",
                        },
                        {
                            "id": 8473,
                            "name": "Kemik Zırh",
                            "key": "BonePlating",
                            "desc": "Hasar aldıktan sonraki 3 rakip vuruşundan daha az hasar alırsın.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/resolve/boneplating/boneplating.png",
                        },
                    ],
                },
                {
                    "row": 3,
                    "type": "minor",
                    "runes": [
                        {
                            "id": 8451,
                            "name": "Aşırı Büyüme",
                            "key": "Overgrowth",
                            "desc": "Yakınında minyonlar öldükçe kalıcı azami can kazanırsın.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/resolve/overgrowth/overgrowth.png",
                        },
                        {
                            "id": 8453,
                            "name": "Canlandır",
                            "key": "Revitalize",
                            "desc": "Kullandığın ve aldığın iyileştirme ile kalkanlar %5 güçlenir.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/resolve/revitalize/revitalize.png",
                        },
                        {
                            "id": 8242,
                            "name": "Metanet",
                            "key": "Unflinching",
                            "desc": "Hareketsiz bırakıldığında zırh ve büyü direnci kazandırır.",
                            "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/styles/resolve/unflinching/unflinching.png",
                        },
                    ],
                },
            ],
        },
    },
    "stat_shards": [
        {
            "slot": 0,
            "name": "Saldırı",
            "type": "offense",
            "shards": [
                {
                    "id": 5008,
                    "name": "Değişken Kuvvet",
                    "desc": "+9 Değişken Kuvvet",
                    "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodsadaptiveforceicon.png",
                },
                {
                    "id": 5005,
                    "name": "Saldırı Hızı",
                    "desc": "+%10 Saldırı Hızı",
                    "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodsattackspeedicon.png",
                },
                {
                    "id": 5007,
                    "name": "Yetenek Hızı",
                    "desc": "+8 Yetenek Hızı",
                    "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodscdrscalingicon.png",
                },
            ],
        },
        {
            "slot": 1,
            "name": "Esnek",
            "type": "flex",
            "shards": [
                {
                    "id": 5008,
                    "name": "Değişken Kuvvet",
                    "desc": "+9 Değişken Kuvvet",
                    "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodsadaptiveforceicon.png",
                },
                {
                    "id": 5010,
                    "name": "Hareket Hızı",
                    "desc": "+%2 Hareket Hızı",
                    "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodsmovementspeedicon.png",
                },
                {
                    "id": 5001,
                    "name": "Büyüyen Can",
                    "desc": "+10-180 Seviyeye Göre Can",
                    "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodshealthscalingicon.png",
                },
            ],
        },
        {
            "slot": 2,
            "name": "Savunma",
            "type": "defense",
            "shards": [
                {
                    "id": 5011,
                    "name": "Taban Can",
                    "desc": "+65 Taban Can",
                    "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodshealthscalingicon.png",
                },
                {
                    "id": 5013,
                    "name": "Sıvışma ve Yavaşlatma Direnci",
                    "desc": "+%10 Sıvışma ve Yavaşlatma Direnci",
                    "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodstenacityicon.png",
                },
                {
                    "id": 5001,
                    "name": "Büyüyen Can",
                    "desc": "+10-180 Seviyeye Göre Can",
                    "icon": "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodshealthscalingicon.png",
                },
            ],
        },
    ],
}


def get_perk_row(tree_id: int, perk_id: int) -> int | None:
    """Return the slot/row index (0=keystone, 1..3=minor) of a perk in a tree."""
    tree = RUNE_DATA["trees"].get(tree_id)
    if not tree:
        return None
    for slot_idx, slot in enumerate(tree["slots"]):
        for rune in slot["runes"]:
            if rune["id"] == perk_id:
                return slot_idx
    return None


def get_perk_info(perk_id: int) -> dict[str, Any] | None:
    """Return full perk metadata dictionary from RUNE_DATA if found."""
    for tree in RUNE_DATA["trees"].values():
        for slot in tree["slots"]:
            for rune in slot["runes"]:
                if rune["id"] == perk_id:
                    return rune
    for shard_group in RUNE_DATA["stat_shards"]:
        for shard in shard_group["shards"]:
            if shard["id"] == perk_id:
                return shard
    return None


def get_perk_icon_url(perk_id: int) -> str:
    """Get CommunityDragon icon URL for perk or keystone."""
    info = get_perk_info(perk_id)
    if info and "icon" in info and info["icon"]:
        return str(info["icon"])
    return "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/perk-images/statmods/statmodsadaptiveforceicon.png"


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
            "keystoneIcon": get_perk_icon_url(self.keystone_id),
            "keystone_icon": get_perk_icon_url(self.keystone_id),
            "perkNames": list(self.perk_names),
            "perk_names": list(self.perk_names),
            "position": self.position,
            "championId": self.champion_id,
            "champion_id": self.champion_id,
            "page": self.to_rune_page().to_dict(),
        }




@dataclass
class CustomRunePage:
    """Represents a user-created custom rune page."""

    id: str
    name: str
    primary_style_id: int
    sub_style_id: int
    selected_perk_ids: list[int]
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize custom rune page with rich UI fields."""
        pids = list(self.selected_perk_ids)
        keystone_id = pids[0] if pids else 0
        return {
            "id": self.id,
            "name": self.name,
            "primaryStyleId": self.primary_style_id,
            "primary_style_id": self.primary_style_id,
            "primaryTreeName": get_rune_tree_name(self.primary_style_id),
            "primary_tree_name": get_rune_tree_name(self.primary_style_id),
            "subStyleId": self.sub_style_id,
            "sub_style_id": self.sub_style_id,
            "subTreeName": get_rune_tree_name(self.sub_style_id),
            "sub_tree_name": get_rune_tree_name(self.sub_style_id),
            "selectedPerkIds": pids,
            "selected_perk_ids": pids,
            "keystoneId": keystone_id,
            "keystone_id": keystone_id,
            "keystoneName": get_perk_name(keystone_id) if keystone_id else "",
            "keystone_name": get_perk_name(keystone_id) if keystone_id else "",
            "keystoneIcon": get_perk_icon_url(keystone_id) if keystone_id else "",
            "keystone_icon": get_perk_icon_url(keystone_id) if keystone_id else "",
            "perkNames": [get_perk_name(pid) for pid in pids],
            "perk_names": [get_perk_name(pid) for pid in pids],
            "createdAt": self.created_at,
            "created_at": self.created_at,
            "isCustom": True,
            "is_custom": True,
            "page": self.to_rune_page().to_dict(),
        }

    def to_rune_page(self) -> RunePage:
        """Convert to editable RunePage ready for LCU application."""
        return RunePage(
            id=0,
            name=self.name,
            primary_style_id=self.primary_style_id,
            sub_style_id=self.sub_style_id,
            selected_perk_ids=list(self.selected_perk_ids),
            current=True,
            is_editable=True,
        )


class CustomRuneManager:
    """Manages custom rune pages, validation rules, and persistence."""

    def __init__(self, storage_path: Path | str | None = None) -> None:
        self.storage_path = Path(storage_path) if storage_path else None
        self._pages: dict[str, CustomRunePage] = {}
        if self.storage_path and self.storage_path.exists():
            self._load_from_storage()

    def validate_rune_selection(
        self,
        primary_style_id: int,
        sub_style_id: int,
        selected_perk_ids: list[int],
        name: str = "",
    ) -> tuple[bool, str]:
        """Validate custom rune page selection according to official LoL rules.

        Returns:
            (True, "") if valid, or (False, error_message) describing the violation.
        """
        if not name or not name.strip():
            return False, "Rün sayfası ismi boş olamaz."

        trees = RUNE_DATA["trees"]
        if primary_style_id not in trees:
            return False, f"Geçersiz birincil ağaç ID: {primary_style_id}"
        if sub_style_id not in trees:
            return False, f"Geçersiz ikincil ağaç ID: {sub_style_id}"
        if primary_style_id == sub_style_id:
            return False, "Birincil ve ikincil rün ağaçları aynı olamaz."

        if len(selected_perk_ids) != 9:
            return False, (
                f"Rün sayfası tam olarak 9 rün içermelidir (1 anahtar, 3 birincil, 2 ikincil, 3 nitelik). "
                f"Seçilen: {len(selected_perk_ids)}"
            )

        # Primary Keystone (Row 0)
        keystone_id = selected_perk_ids[0]
        row_0_ids = {r["id"] for r in trees[primary_style_id]["slots"][0]["runes"]}
        if keystone_id not in row_0_ids:
            return False, "İlk rün birincil ağaçtan seçilmiş geçerli bir Anahtar Rün olmalıdır."

        # Primary Minor Runes (Rows 1, 2, 3)
        for row_idx in (1, 2, 3):
            perk_id = selected_perk_ids[row_idx]
            expected_row_ids = {r["id"] for r in trees[primary_style_id]["slots"][row_idx]["runes"]}
            if perk_id not in expected_row_ids:
                return False, f"Birincil ağaç satır {row_idx} için geçerli bir rün seçilmelidir."

        # Secondary Minor Runes (Must be from two DIFFERENT rows among 1, 2, 3)
        sec_perk1 = selected_perk_ids[4]
        sec_perk2 = selected_perk_ids[5]

        sec_row1 = get_perk_row(sub_style_id, sec_perk1)
        sec_row2 = get_perk_row(sub_style_id, sec_perk2)

        if sec_row1 is None or sec_row1 == 0:
            return False, "İkincil ağacın 1. rünü ikincil ağacın küçük rünlerinden (satır 1-3) seçilmelidir."
        if sec_row2 is None or sec_row2 == 0:
            return False, "İkincil ağacın 2. rünü ikincil ağacın küçük rünlerinden (satır 1-3) seçilmelidir."

        if sec_row1 == sec_row2:
            return False, "İkincil ağaçtan seçilen 2 rün farklı satırlardan olmalıdır."

        # Stat Shards (Indices 6, 7, 8)
        valid_shards = {5001, 5002, 5003, 5005, 5007, 5008, 5010, 5011, 5013}
        for i, s_idx in enumerate([6, 7, 8]):
            shard_id = selected_perk_ids[s_idx]
            if shard_id not in valid_shards:
                return False, f"{i+1}. nitelik parçası geçerli bir nitelik rünü olmalıdır."

        return True, ""

    def save_page(self, data: dict[str, Any] | CustomRunePage) -> CustomRunePage:
        """Validate and save a custom rune page."""
        if isinstance(data, CustomRunePage):
            page = data
        else:
            page_id = str(data.get("id") or "").strip()
            if not page_id:
                page_id = f"custom-{int(time.time() * 1000)}-{secrets.token_hex(3)}"
            page = CustomRunePage(
                id=page_id,
                name=str(data.get("name") or "").strip(),
                primary_style_id=int(data.get("primaryStyleId") or data.get("primary_style_id") or 0),
                sub_style_id=int(data.get("subStyleId") or data.get("sub_style_id") or 0),
                selected_perk_ids=[int(x) for x in (data.get("selectedPerkIds") or data.get("selected_perk_ids") or [])],
                created_at=float(data.get("created_at") or data.get("createdAt") or time.time()),
            )

        valid, err = self.validate_rune_selection(
            primary_style_id=page.primary_style_id,
            sub_style_id=page.sub_style_id,
            selected_perk_ids=page.selected_perk_ids,
            name=page.name,
        )
        if not valid:
            raise ValueError(err)

        self._pages[page.id] = page
        self._save_to_storage()
        return page

    def get_page(self, page_id: str) -> CustomRunePage | None:
        """Find custom rune page by ID."""
        return self._pages.get(page_id)

    def get_all_pages(self) -> list[CustomRunePage]:
        """Return all custom rune pages sorted by creation date (newest first)."""
        return sorted(self._pages.values(), key=lambda p: p.created_at, reverse=True)

    def delete_page(self, page_id: str) -> bool:
        """Delete custom rune page by ID. Return True if deleted, False if not found."""
        if page_id in self._pages:
            del self._pages[page_id]
            self._save_to_storage()
            return True
        return False

    def clear(self) -> None:
        """Clear all stored custom rune pages."""
        self._pages.clear()
        self._save_to_storage()

    def _save_to_storage(self) -> None:
        if not self.storage_path:
            return
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = [p.to_dict() for p in self._pages.values()]
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _load_from_storage(self) -> None:
        if not self.storage_path or not self.storage_path.is_file():
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                items = json.load(f)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and "id" in item:
                        p = CustomRunePage(
                            id=str(item["id"]),
                            name=str(item.get("name", "Özel Rün")),
                            primary_style_id=int(item.get("primary_style_id") or item.get("primaryStyleId") or 8000),
                            sub_style_id=int(item.get("sub_style_id") or item.get("subStyleId") or 8100),
                            selected_perk_ids=[int(x) for x in (item.get("selected_perk_ids") or item.get("selectedPerkIds") or [])],
                            created_at=float(item.get("created_at") or item.get("createdAt") or time.time()),
                        )
                        self._pages[p.id] = p
        except Exception:
            pass


# Global default CustomRuneManager instance
custom_rune_manager = CustomRuneManager()


def get_custom_rune_manager() -> CustomRuneManager:
    """Access the global CustomRuneManager instance."""
    return custom_rune_manager


__all__ = [
    "CustomRuneManager",
    "CustomRunePage",
    "PERK_NAMES",
    "RUNE_DATA",
    "RUNE_TREES",
    "RecommendedRunePage",
    "RunePage",
    "STANDARD_SPELLS",
    "SummonerSpell",
    "custom_rune_manager",
    "get_all_summoner_spells",
    "get_custom_rune_manager",
    "get_perk_icon_url",
    "get_perk_info",
    "get_perk_name",
    "get_perk_row",
    "get_rune_tree_name",
    "get_spells_for_mode",
    "get_summoner_spell",
]
