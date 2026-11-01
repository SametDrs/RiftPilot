"""RiftPilot Core Domain — Mode-Aware Champion Select State Machine & Profiles.

Defines:
- `GameModeContext`: Active game mode, queue ID, queue name, map ID, and mutators from LCU.
- `SelectionLayoutKind`: UI layout strategy derived from game mode and session structure.
- `ChampSelectStage`: Fine-grained state machine stage of Champion Select.
- `ActionEligibility`: Server-derived rules for Lock, Ban, and Hover availability.
- `CentralChampSelectState`: Unified state model shared across LCU, desktop, and mobile UI.
- Validation helpers for server-side command re-verification before LCU execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from riftpilot.core.champ_select import ChampSelectAction, ChampSelectSession


class SelectionLayoutKind(str, Enum):
    """Selection screen layout strategy determined by game mode and session capabilities."""

    DRAFT = "DRAFT"           # Ranked Solo/Flex, Normal Draft (Bans + Turn-based Picks)
    BLIND = "BLIND"           # Normal Blind Pick (No Bans, Simultaneous Picks)
    ARAM = "ARAM"             # ARAM / ARAM Şamata (Random assignment, Bench + Reroll, No Bans/Grid Pick)
    SAFE_GENERIC = "SAFE_GENERIC"  # Unknown or unsupported mode (Read-only safe status view)


class ChampSelectStage(str, Enum):
    """Fine-grained state machine stages for Champion Select."""

    INACTIVE = "INACTIVE"
    PLANNING = "PLANNING"
    BAN_MY_TURN = "BAN_MY_TURN"
    BAN_WAITING = "BAN_WAITING"
    PICK_MY_TURN = "PICK_MY_TURN"
    PICK_WAITING = "PICK_WAITING"
    PICK_LOCKED = "PICK_LOCKED"
    ARAM_BENCH = "ARAM_BENCH"
    FINALIZATION = "FINALIZATION"
    GAME_STARTING = "GAME_STARTING"
    UNSUPPORTED_SAFE = "UNSUPPORTED_SAFE"


# Known queue IDs and their friendly Turkish display names
QUEUE_DISPLAY_NAMES: dict[int, str] = {
    400: "Normal Sıralı Seçim",
    420: "Dereceli Tek/Çift",
    430: "Normal Kapalı Seçim",
    440: "Dereceli Esnek",
    450: "ARAM (Sonsuz Uçurum)",
    490: "Hızlı Oyun (Quickplay)",
    700: "Clash",
    830: "Yapay Zekâya Karşı (Giriş)",
    840: "Yapay Zekâya Karşı (Başlangıç)",
    850: "Yapay Zekâya Karşı (Orta)",
    900: "ARURF",
    1020: "Birimiz Hepimiz İçin",
    1300: "Merkez Baskını (Nexus Blitz)",
    1400: "Ulti Büyü Kitabı",
    1700: "Arena",
    1900: "URF",
    2400: "ARAM Şamata",
}

# Supported standard gameMode strings from LCU
KNOWN_DRAFT_QUEUE_IDS: set[int] = {400, 420, 440, 700}
KNOWN_BLIND_QUEUE_IDS: set[int] = {430, 830, 840, 850}
KNOWN_ARAM_QUEUE_IDS: set[int] = {450, 2400}
KNOWN_ARAM_MODES: set[str] = {"ARAM", "KIWI"}
KNOWN_CLASSIC_MODES: set[str] = {"CLASSIC", "URF", "ONEFORALL", "ULTBOOK", "NEXUSBLITZ", "PRACTICETOOL"}


@dataclass
class GameModeContext:
    """Represents the active game mode and queue metadata from `/lol-gameflow/v1/session`."""

    game_mode: str = "CLASSIC"
    queue_id: int = 420
    queue_name: str = ""
    map_id: int = 11
    is_custom: bool = False
    mutators: list[str] = field(default_factory=list)

    @classmethod
    def from_lcu_gameflow_session(cls, data: dict[str, Any]) -> GameModeContext:
        """Parse `GameModeContext` from raw `/lol-gameflow/v1/session` payload."""
        if not isinstance(data, dict):
            return cls(game_mode="UNKNOWN", queue_id=0, map_id=0)

        game_data = data.get("gameData")
        if not isinstance(game_data, dict):
            game_data = {}

        queue = game_data.get("queue")
        if not isinstance(queue, dict):
            queue = {}

        map_data = data.get("map")
        if not isinstance(map_data, dict):
            map_data = {}

        raw_mode = str(
            queue.get("gameMode")
            or map_data.get("gameMode")
            or game_data.get("gameMode")
            or "UNKNOWN"
        ).upper()

        try:
            queue_id = int(queue.get("id", 0) or 0)
        except (TypeError, ValueError):
            queue_id = 0

        try:
            map_id = int(
                map_data.get("id")
                or queue.get("mapId")
                or 0
            )
        except (TypeError, ValueError):
            map_id = 0

        if map_id == 0:
            if raw_mode in KNOWN_ARAM_MODES or queue_id in KNOWN_ARAM_QUEUE_IDS:
                map_id = 12
            elif raw_mode == "CLASSIC":
                map_id = 11

        queue_name = str(
            queue.get("description")
            or queue.get("name")
            or QUEUE_DISPLAY_NAMES.get(queue_id, "")
        ).strip()

        game_type_cfg = queue.get("gameTypeConfig")
        mutators: list[str] = []
        if isinstance(game_type_cfg, dict):
            cfg_name = str(game_type_cfg.get("name", "")).strip()
            if cfg_name:
                mutators.append(cfg_name)

        is_custom = bool(game_data.get("isCustomGame", False))

        return cls(
            game_mode=raw_mode,
            queue_id=queue_id,
            queue_name=queue_name,
            map_id=map_id,
            is_custom=is_custom,
            mutators=mutators,
        )

    @property
    def display_name(self) -> str:
        """Return localized Turkish display label for the current mode/queue."""
        if self.queue_name:
            # Map common English LCU descriptions to Turkish if needed
            lower = self.queue_name.lower()
            if "shamata" in lower or "şamata" in lower or "mayhem" in lower or "brawl" in lower:
                return "ARAM Şamata"
            if self.queue_id in QUEUE_DISPLAY_NAMES:
                return QUEUE_DISPLAY_NAMES[self.queue_id]
            return self.queue_name
        if self.queue_id in QUEUE_DISPLAY_NAMES:
            return QUEUE_DISPLAY_NAMES[self.queue_id]
        if self.game_mode in KNOWN_ARAM_MODES:
            return "ARAM"
        if self.game_mode == "CLASSIC":
            return "Sihirdar Vadisi"
        if self.game_mode and self.game_mode != "UNKNOWN":
            return f"Oyun Modu: {self.game_mode}"
        return "Bilinmeyen Oyun Modu"

    def resolve_layout(self, session: ChampSelectSession | None = None) -> SelectionLayoutKind:
        """Determine the appropriate UI layout and ruleset for this game mode and session."""
        mode_upper = (self.game_mode or "").upper()

        # 1. Inspect real session capabilities first when available
        if session is not None:
            if session.bench_enabled or mode_upper in KNOWN_ARAM_MODES or self.queue_id in KNOWN_ARAM_QUEUE_IDS:
                return SelectionLayoutKind.ARAM
            if session.has_ban_actions():
                return SelectionLayoutKind.DRAFT
            if session.has_pick_actions():
                return SelectionLayoutKind.BLIND
            # Session has neither bench nor pick/ban actions -> unknown or unsupported
            if mode_upper not in KNOWN_CLASSIC_MODES and mode_upper not in KNOWN_ARAM_MODES:
                return SelectionLayoutKind.SAFE_GENERIC

        # 2. Fallback to queue/mode classification
        if mode_upper in KNOWN_ARAM_MODES or self.queue_id in KNOWN_ARAM_QUEUE_IDS or self.map_id == 12:
            return SelectionLayoutKind.ARAM
        if self.queue_id in KNOWN_DRAFT_QUEUE_IDS:
            return SelectionLayoutKind.DRAFT
        if self.queue_id in KNOWN_BLIND_QUEUE_IDS:
            return SelectionLayoutKind.BLIND
        if mode_upper == "CLASSIC":
            return SelectionLayoutKind.DRAFT

        return SelectionLayoutKind.SAFE_GENERIC

    def to_dict(self) -> dict[str, Any]:
        """Serialize game mode context for JSON/WebSocket transport."""
        return {
            "gameMode": self.game_mode,
            "game_mode": self.game_mode,
            "queueId": self.queue_id,
            "queue_id": self.queue_id,
            "queueName": self.queue_name,
            "queue_name": self.queue_name,
            "displayName": self.display_name,
            "display_name": self.display_name,
            "mapId": self.map_id,
            "map_id": self.map_id,
            "isCustom": self.is_custom,
            "mutators": list(self.mutators),
        }


@dataclass
class ActionEligibility:
    """Computed UI button visibility and enablement rules derived from central state."""

    layout: SelectionLayoutKind = SelectionLayoutKind.SAFE_GENERIC
    stage: ChampSelectStage = ChampSelectStage.INACTIVE
    ban_phase_exists: bool = False
    ban_button_visible: bool = False
    ban_button_enabled: bool = False
    ban_disabled_reason: str = ""
    lock_button_visible: bool = False
    lock_button_enabled: bool = False
    lock_disabled_reason: str = ""
    hover_allowed: bool = False
    bench_visible: bool = False
    reroll_enabled: bool = False
    active_action_id: int = 0
    active_action_type: str = ""
    is_my_turn: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize eligibility rules with both camelCase and snake_case keys."""
        return {
            "layout": self.layout.value,
            "stage": self.stage.value,
            "banPhaseExists": self.ban_phase_exists,
            "ban_phase_exists": self.ban_phase_exists,
            "banButtonVisible": self.ban_button_visible,
            "ban_button_visible": self.ban_button_visible,
            "banButtonEnabled": self.ban_button_enabled,
            "ban_button_enabled": self.ban_button_enabled,
            "banDisabledReason": self.ban_disabled_reason,
            "ban_disabled_reason": self.ban_disabled_reason,
            "lockButtonVisible": self.lock_button_visible,
            "lock_button_visible": self.lock_button_visible,
            "lockButtonEnabled": self.lock_button_enabled,
            "lock_button_enabled": self.lock_button_enabled,
            "lockDisabledReason": self.lock_disabled_reason,
            "lock_disabled_reason": self.lock_disabled_reason,
            "hoverAllowed": self.hover_allowed,
            "hover_allowed": self.hover_allowed,
            "benchVisible": self.bench_visible,
            "bench_visible": self.bench_visible,
            "rerollEnabled": self.reroll_enabled,
            "reroll_enabled": self.reroll_enabled,
            "activeActionId": self.active_action_id,
            "active_action_id": self.active_action_id,
            "activeActionType": self.active_action_type,
            "active_action_type": self.active_action_type,
            "isMyTurn": self.is_my_turn,
            "is_my_turn": self.is_my_turn,
        }


def compute_champ_select_stage_and_eligibility(
    session: ChampSelectSession | None,
    mode_context: GameModeContext,
    disabled_champion_ids: set[int] | None = None,
) -> ActionEligibility:
    """Derive the state machine stage and button eligibility from live session and mode."""
    if session is None:
        return ActionEligibility(
            layout=mode_context.resolve_layout(None),
            stage=ChampSelectStage.INACTIVE,
        )

    layout = mode_context.resolve_layout(session)
    disabled_ids = disabled_champion_ids or set()
    banned_ids = session.get_all_banned_ids()
    unavailable_picks = session.get_unavailable_pick_ids()
    time_left = session.timer.time_left_seconds()
    raw_phase = (session.timer.phase or "").upper()

    # timer_expired is True only when timer explicitly reports 0 in a timed phase
    # Note: in some LCU states or unit tests total_time_in_phase may be 0 while active;
    # treat timer as expired only when total_time_in_phase > 0 and time_left <= 0.
    timer_expired = (
        session.timer.total_time_in_phase > 0 and time_left <= 0.0
    )

    if layout == SelectionLayoutKind.SAFE_GENERIC:
        return ActionEligibility(
            layout=layout,
            stage=ChampSelectStage.UNSUPPORTED_SAFE,
            ban_phase_exists=False,
            ban_button_visible=False,
            ban_button_enabled=False,
            ban_disabled_reason="Bu oyun modu için seçim kuralları desteklenmiyor.",
            lock_button_visible=False,
            lock_button_enabled=False,
            lock_disabled_reason="Güvenli görünüm: yanlış seçimi önlemek için seçim devre dışı.",
            hover_allowed=False,
        )

    if layout == SelectionLayoutKind.ARAM:
        stage = (
            ChampSelectStage.GAME_STARTING
            if raw_phase == "GAME_STARTING"
            else ChampSelectStage.ARAM_BENCH
        )
        return ActionEligibility(
            layout=layout,
            stage=stage,
            ban_phase_exists=False,
            ban_button_visible=False,
            ban_button_enabled=False,
            ban_disabled_reason="ARAM modunda yasaklama aşaması yoktur.",
            lock_button_visible=False,
            lock_button_enabled=False,
            lock_disabled_reason="ARAM modunda şampiyonlar otomatik atanır.",
            hover_allowed=False,
            bench_visible=True,
            reroll_enabled=(session.rerolls_remaining > 0 and not timer_expired and stage == ChampSelectStage.ARAM_BENCH),
        )

    ban_phase_exists = session.has_ban_actions()
    all_actions = session.iter_actions()
    any_ban_in_progress = any(a.is_ban() and a.is_in_progress and not a.completed for a in all_actions)
    any_pick_in_progress = any(a.is_pick() and a.is_in_progress and not a.completed for a in all_actions)

    in_progress_local = session.get_in_progress_local_action()
    active_local = session.get_active_action()
    local_picks = session.get_local_actions("pick")
    all_local_picks_completed = bool(local_picks) and all(a.completed for a in local_picks)

    # Determine fine-grained stage
    if raw_phase == "GAME_STARTING":
        stage = ChampSelectStage.GAME_STARTING
    elif raw_phase == "FINALIZATION":
        stage = ChampSelectStage.FINALIZATION
    elif raw_phase == "PLANNING":
        stage = ChampSelectStage.PLANNING
    elif in_progress_local is not None and in_progress_local.is_ban():
        stage = ChampSelectStage.BAN_MY_TURN
    elif any_ban_in_progress:
        stage = ChampSelectStage.BAN_WAITING
    elif in_progress_local is not None and in_progress_local.is_pick():
        stage = ChampSelectStage.PICK_MY_TURN
    elif all_local_picks_completed:
        stage = ChampSelectStage.PICK_LOCKED
    elif any_pick_in_progress or raw_phase == "BAN_PICK":
        stage = ChampSelectStage.PICK_WAITING
    else:
        stage = ChampSelectStage.FINALIZATION

    # Ban button rules:
    # - Hidden completely if mode has no ban phase
    # - Visible during ban phases (or when ban phase exists), enabled ONLY on BAN_MY_TURN with valid unbanned champ
    ban_button_visible = ban_phase_exists and stage in (
        ChampSelectStage.PLANNING,
        ChampSelectStage.BAN_MY_TURN,
        ChampSelectStage.BAN_WAITING,
    )
    ban_button_enabled = False
    ban_reason = ""

    if not ban_phase_exists:
        ban_reason = "Bu oyun modunda yasaklama aşaması bulunmuyor."
    elif stage != ChampSelectStage.BAN_MY_TURN or in_progress_local is None:
        ban_reason = "Yasaklama sıranız bekleniyor."
    elif timer_expired:
        ban_reason = "Yasaklama süresi doldu."
    else:
        champ_id = in_progress_local.champion_id
        if champ_id <= 0:
            ban_reason = "Yasaklamak için bir şampiyon seçin."
        elif champ_id in banned_ids:
            ban_reason = "Bu şampiyon zaten yasaklanmış."
        elif champ_id in disabled_ids:
            ban_reason = "Bu şampiyon geçerli değil veya devre dışı."
        else:
            ban_button_enabled = True

    # Lock (Pick) button rules:
    # - Visible in DRAFT and BLIND modes
    # - Enabled ONLY on PICK_MY_TURN with valid, unbanned, unpicked champion and time left > 0
    lock_button_visible = layout in (SelectionLayoutKind.DRAFT, SelectionLayoutKind.BLIND)
    lock_button_enabled = False
    lock_reason = ""

    if stage == ChampSelectStage.PICK_LOCKED:
        lock_reason = "Şampiyon seçiminiz kilitlendi."
    elif stage in (ChampSelectStage.FINALIZATION, ChampSelectStage.GAME_STARTING):
        lock_reason = "Seçim aşaması tamamlandı."
    elif stage != ChampSelectStage.PICK_MY_TURN or in_progress_local is None:
        lock_reason = "Şampiyon seçme sıranız bekleniyor."
    elif timer_expired:
        lock_reason = "Seçim süresi doldu."
    else:
        champ_id = in_progress_local.champion_id
        if champ_id <= 0:
            lock_reason = "Kilitlemek için bir şampiyon seçin."
        elif champ_id in banned_ids:
            lock_reason = "Yasaklanan bir şampiyon seçilemez."
        elif champ_id in unavailable_picks:
            lock_reason = "Bu şampiyon başka bir oyuncu tarafından seçildi."
        elif champ_id in disabled_ids:
            lock_reason = "Bu şampiyon bu modda kullanılamaz."
        else:
            lock_button_enabled = True

    hover_allowed = (
        active_local is not None
        and not active_local.completed
        and stage
        in (
            ChampSelectStage.PLANNING,
            ChampSelectStage.BAN_MY_TURN,
            ChampSelectStage.BAN_WAITING,
            ChampSelectStage.PICK_MY_TURN,
            ChampSelectStage.PICK_WAITING,
        )
    )

    return ActionEligibility(
        layout=layout,
        stage=stage,
        ban_phase_exists=ban_phase_exists,
        ban_button_visible=ban_button_visible,
        ban_button_enabled=ban_button_enabled,
        ban_disabled_reason=ban_reason,
        lock_button_visible=lock_button_visible,
        lock_button_enabled=lock_button_enabled,
        lock_disabled_reason=lock_reason,
        hover_allowed=hover_allowed,
        bench_visible=False,
        reroll_enabled=False,
        active_action_id=in_progress_local.id if in_progress_local else (active_local.id if active_local else 0),
        active_action_type=in_progress_local.type if in_progress_local else (active_local.type if active_local else ""),
        is_my_turn=(in_progress_local is not None),
    )


@dataclass
class CommandValidationResult:
    """Outcome of server-side command validation against current LCU state."""

    allowed: bool
    error_code: str = ""
    message: str = ""
    resolved_action: ChampSelectAction | None = None


def validate_champ_select_command(
    session: ChampSelectSession | None,
    mode_context: GameModeContext,
    action_id: int,
    champion_id: int,
    completed: bool,
    disabled_champion_ids: set[int] | None = None,
) -> CommandValidationResult:
    """Re-validate a pick/ban hover or lock command against live LCU session state."""
    if session is None:
        return CommandValidationResult(
            allowed=False,
            error_code="NO_ACTIVE_SESSION",
            message="Aktif bir şampiyon seçim oturumu bulunamadı.",
        )

    layout = mode_context.resolve_layout(session)
    if layout == SelectionLayoutKind.SAFE_GENERIC:
        return CommandValidationResult(
            allowed=False,
            error_code="UNSUPPORTED_GAME_MODE",
            message="Bu oyun modu desteklenmediği için seçim işlemi engellendi.",
        )

    if layout == SelectionLayoutKind.ARAM:
        return CommandValidationResult(
            allowed=False,
            error_code="MODE_NO_MANUAL_PICK",
            message="ARAM modunda manuel şampiyon kilitleme veya yasaklama yapılamaz.",
        )

    if champion_id <= 0:
        return CommandValidationResult(
            allowed=False,
            error_code="INVALID_CHAMPION",
            message="Geçerli bir şampiyon seçilmedi.",
        )

    banned_ids = session.get_all_banned_ids()
    if champion_id in banned_ids:
        return CommandValidationResult(
            allowed=False,
            error_code="CHAMPION_BANNED",
            message="Bu şampiyon yasaklandığı için seçilemez veya tekrar yasaklanamaz.",
        )

    if disabled_champion_ids and champion_id in disabled_champion_ids:
        return CommandValidationResult(
            allowed=False,
            error_code="CHAMPION_DISABLED",
            message="Seçilen şampiyon bu oyun modunda devre dışı veya kullanılamaz.",
        )

    # Resolve target action
    target_action: ChampSelectAction | None = None
    if action_id > 0:
        target_action = session.find_action(action_id)
    if target_action is None:
        target_action = (
            session.get_in_progress_local_action()
            if completed
            else session.get_active_action()
        )

    if target_action is None:
        return CommandValidationResult(
            allowed=False,
            error_code="ACTION_NOT_FOUND",
            message="Sıranıza ait aktif bir seçim veya yasaklama işlemi bulunamadı.",
        )

    if target_action.actor_cell_id != session.local_player_cell_id:
        return CommandValidationResult(
            allowed=False,
            error_code="NOT_LOCAL_PLAYER_ACTION",
            message="Başka bir oyuncunun seçim/yasaklama sırasına müdahale edilemez.",
        )

    if target_action.completed:
        return CommandValidationResult(
            allowed=False,
            error_code="ACTION_ALREADY_COMPLETED",
            message="Bu seçim veya yasaklama işlemi zaten tamamlanmış.",
        )

    if target_action.is_ban() and not session.has_ban_actions():
        return CommandValidationResult(
            allowed=False,
            error_code="NO_BAN_PHASE",
            message="Mevcut oyun modunda yasaklama aşaması bulunmuyor.",
        )

    if target_action.is_pick() and champion_id in session.get_unavailable_pick_ids():
        return CommandValidationResult(
            allowed=False,
            error_code="CHAMPION_ALREADY_PICKED",
            message="Bu şampiyon başka bir oyuncu tarafından kilitlendi.",
        )

    # If locking in (completed=True), strictly require that it is currently the player's turn
    # and the phase timer has not expired.
    if completed:
        raw_phase = (session.timer.phase or "").upper()
        if raw_phase in ("FINALIZATION", "GAME_STARTING"):
            return CommandValidationResult(
                allowed=False,
                error_code="PHASE_ENDED",
                message="Seçim aşaması sona erdiği için işlem yapılamaz.",
            )

        if not target_action.is_in_progress:
            if target_action.is_ban():
                return CommandValidationResult(
                    allowed=False,
                    error_code="NOT_YOUR_BAN_TURN",
                    message="Henüz yasaklama sıranız gelmedi.",
                )
            return CommandValidationResult(
                allowed=False,
                error_code="NOT_YOUR_PICK_TURN",
                message="Henüz şampiyon seçme sıranız gelmedi.",
            )

        if session.timer.total_time_in_phase > 0 and session.timer.time_left_seconds() <= 0.0:
            return CommandValidationResult(
                allowed=False,
                error_code="TIMER_EXPIRED",
                message="Aşama süresi dolduğu için işlem gerçekleştirilemedi.",
            )

    return CommandValidationResult(
        allowed=True,
        resolved_action=target_action,
    )


__all__ = [
    "ActionEligibility",
    "ChampSelectStage",
    "CommandValidationResult",
    "GameModeContext",
    "QUEUE_DISPLAY_NAMES",
    "SelectionLayoutKind",
    "compute_champ_select_stage_and_eligibility",
    "validate_champ_select_command",
]
