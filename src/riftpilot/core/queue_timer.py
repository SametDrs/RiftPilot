"""RiftPilot Core Domain — Matchmaking Queue Timer & Formatting.

Tracks elapsed queue time, estimated queue duration, countdowns, and formatted
labels for companion GUI and mobile WebSocket telemetry.
"""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """Format total seconds into MM:SS representation.

    Examples:
        >>> format_duration(84.0)
        '01:24'
        >>> format_duration(135.0)
        '02:15'
        >>> format_duration(0.0)
        '00:00'
    """
    total_sec = max(0, int(seconds))
    minutes = total_sec // 60
    sec = total_sec % 60
    return f"{minutes:02d}:{sec:02d}"


class QueueTimer:
    """Domain model tracking active matchmaking queue time and estimated duration."""

    def __init__(
        self,
        estimated_seconds: float = 0.0,
        elapsed_seconds: float = 0.0,
    ) -> None:
        self.estimated_seconds: float = max(0.0, float(estimated_seconds))
        self.elapsed_seconds: float = max(0.0, float(elapsed_seconds))
        self.is_running: bool = False

    def start(
        self,
        estimated_duration_seconds: float = 0.0,
        reset_elapsed: bool = True,
    ) -> None:
        """Start or update the queue timer.

        Args:
            estimated_duration_seconds: Estimated queue time from LCU in seconds.
            reset_elapsed: If True, resets elapsed duration to 0.0.
        """
        if estimated_duration_seconds > 0.0 or reset_elapsed:
            self.estimated_seconds = max(0.0, float(estimated_duration_seconds))
        if reset_elapsed:
            self.elapsed_seconds = 0.0
        self.is_running = True

    def tick(self, delta_seconds: float = 1.0) -> float:
        """Advance elapsed queue duration by delta_seconds (default 1.0).

        Returns:
            Current elapsed seconds.
        """
        if not self.is_running:
            return self.elapsed_seconds
        self.elapsed_seconds += max(0.0, float(delta_seconds))
        return self.elapsed_seconds

    def update_estimated(self, estimated_duration_seconds: float) -> None:
        """Update estimated duration without altering elapsed time or running state."""
        self.estimated_seconds = max(0.0, float(estimated_duration_seconds))

    def set_elapsed(self, seconds: float) -> None:
        """Directly set elapsed duration in seconds."""
        self.elapsed_seconds = max(0.0, float(seconds))

    def stop(self) -> None:
        """Pause/stop timer without resetting elapsed duration."""
        self.is_running = False

    def reset(self) -> None:
        """Reset elapsed and estimated durations, and stop the timer."""
        self.is_running = False
        self.elapsed_seconds = 0.0
        self.estimated_seconds = 0.0

    @property
    def formatted_elapsed(self) -> str:
        """Formatted elapsed duration string in 'MM:SS' (e.g. '01:24')."""
        return format_duration(self.elapsed_seconds)

    @property
    def formatted_estimated(self) -> str:
        """Formatted estimated duration in 'MM:SS' (e.g. '02:15') or '--:--' if 0."""
        if self.estimated_seconds <= 0.0:
            return "--:--"
        return format_duration(self.estimated_seconds)

    @property
    def remaining_seconds(self) -> float:
        """Remaining duration until estimated queue time is reached, or 0.0."""
        if self.estimated_seconds <= 0.0:
            return 0.0
        return max(0.0, self.estimated_seconds - self.elapsed_seconds)

    @property
    def formatted_remaining(self) -> str:
        """Formatted remaining countdown as 'MM:SS' or '--:--' if no estimate."""
        if self.estimated_seconds <= 0.0:
            return "--:--"
        return format_duration(self.remaining_seconds)

    @property
    def is_overdue(self) -> bool:
        """Return True if elapsed time has exceeded estimated time."""
        return self.estimated_seconds > 0.0 and self.elapsed_seconds > self.estimated_seconds

    @property
    def overdue_seconds(self) -> float:
        """Number of seconds elapsed beyond the estimated time."""
        if not self.is_overdue:
            return 0.0
        return self.elapsed_seconds - self.estimated_seconds

    @property
    def display_text(self) -> str:
        """Formatted status text for companion GUI and mobile display.

        Examples:
            '⏳ Sırada: 01:24 (Tahmini 02:15)'
            '⏳ Sırada: 01:24'
        """
        if self.estimated_seconds > 0.0:
            return f"⏳ Sırada: {self.formatted_elapsed} (Tahmini {self.formatted_estimated})"
        return f"⏳ Sırada: {self.formatted_elapsed}"

    @property
    def slash_formatted_text(self) -> str:
        """Alternative formatted string 'MM:SS / Tahmini MM:SS'."""
        if self.estimated_seconds > 0.0:
            return f"{self.formatted_elapsed} / Tahmini {self.formatted_estimated}"
        return self.formatted_elapsed

    def __repr__(self) -> str:
        return (
            f"QueueTimer(running={self.is_running}, "
            f"elapsed={self.formatted_elapsed}, "
            f"estimated={self.formatted_estimated})"
        )

    def __str__(self) -> str:
        return self.display_text


__all__ = ["QueueTimer", "format_duration"]
