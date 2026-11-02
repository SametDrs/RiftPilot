"""RiftPilot Core Domain — Matchmaking Queue & In-Game Timers.

Tracks elapsed queue time, in-game match duration, estimated queue duration,
countdowns, and formatted labels for companion GUI and mobile WebSocket telemetry.
Guarantees accurate time calculation based on start timestamps that survive
background intervals and event-loop delays.
"""

from __future__ import annotations

import time
from typing import Callable


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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock: Callable[[], float] = clock
        self.estimated_seconds: float = max(0.0, float(estimated_seconds))
        self.elapsed_seconds: float = max(0.0, float(elapsed_seconds))
        self.is_running: bool = False
        self.start_timestamp: float | None = None

    def start(
        self,
        estimated_duration_seconds: float = 0.0,
        reset_elapsed: bool = True,
        initial_seconds: float = 0.0,
    ) -> None:
        """Start or update the queue timer.

        Args:
            estimated_duration_seconds: Estimated queue time from LCU in seconds.
            reset_elapsed: If True, resets elapsed duration to initial_seconds (default 0.0).
            initial_seconds: Optional initial elapsed duration if reset_elapsed is True.
        """
        if estimated_duration_seconds > 0.0 or reset_elapsed:
            self.estimated_seconds = max(0.0, float(estimated_duration_seconds))
        if reset_elapsed:
            self.elapsed_seconds = max(0.0, float(initial_seconds))
        self.start_timestamp = self._clock() - self.elapsed_seconds
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

    def sync_with_timestamp(self) -> float:
        """Synchronize elapsed duration with start timestamp to recover from background delays."""
        if self.is_running and self.start_timestamp is not None:
            clock_elapsed = max(0.0, self._clock() - self.start_timestamp)
            if clock_elapsed > self.elapsed_seconds:
                self.elapsed_seconds = clock_elapsed
        return self.elapsed_seconds

    def update_estimated(self, estimated_duration_seconds: float) -> None:
        """Update estimated duration without altering elapsed time or running state."""
        self.estimated_seconds = max(0.0, float(estimated_duration_seconds))

    def set_elapsed(self, seconds: float) -> None:
        """Directly set elapsed duration in seconds."""
        self.elapsed_seconds = max(0.0, float(seconds))
        if self.is_running:
            self.start_timestamp = self._clock() - self.elapsed_seconds

    def sync_elapsed(self, seconds: float) -> None:
        """Synchronize elapsed duration from external source (e.g. LCU timeInQueue)."""
        self.set_elapsed(seconds)

    def sync_clock(self) -> float:
        """Synchronize elapsed_seconds with start_timestamp from real-time clock."""
        if self.is_running and self.start_timestamp is not None:
            self.elapsed_seconds = max(self.elapsed_seconds, self._clock() - self.start_timestamp)
        return self.elapsed_seconds

    def stop(self) -> None:
        """Pause/stop timer without resetting elapsed duration."""
        self.is_running = False
        self.start_timestamp = None

    def reset(self) -> None:
        """Reset elapsed and estimated durations, and stop the timer."""
        self.is_running = False
        self.start_timestamp = None
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


class InGameTimer:
    """Domain model tracking elapsed in-game match duration based on start timestamp.

    Uses `time.monotonic()` (or custom clock) to guarantee that elapsed match time
    calculations remain accurate even if UI tick events or background polling
    intervals are throttled or suspended.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock: Callable[[], float] = clock
        self._start_time: float | None = None
        self._accumulated_ticks: float = 0.0
        self._initial_seconds: float = 0.0
        self._stopped_elapsed: float = 0.0
        self._is_running: bool = False

    @property
    def is_running(self) -> bool:
        """Whether the timer is actively running."""
        return self._is_running

    def start(self, initial_seconds: float = 0.0) -> None:
        """Start or restart tracking in-game duration.

        Args:
            initial_seconds: Initial elapsed duration to start from (e.g. from gameflow session).
        """
        init_sec = max(0.0, float(initial_seconds))
        self._initial_seconds = init_sec
        self._accumulated_ticks = init_sec
        self._stopped_elapsed = init_sec
        self._start_time = self._clock()
        self._is_running = True

    @property
    def elapsed_seconds(self) -> float:
        """Total elapsed in-game seconds, calculated from start timestamp when running."""
        if not self._is_running or self._start_time is None:
            return self._stopped_elapsed

        clock_elapsed = self._initial_seconds + max(0.0, self._clock() - self._start_time)
        return max(self._accumulated_ticks, clock_elapsed)

    def tick(self, delta_seconds: float = 1.0) -> float:
        """Advance timer. Uses start timestamp to survive background intervals.

        Args:
            delta_seconds: Incremental step seconds if running (default 1.0).

        Returns:
            Current elapsed seconds.
        """
        if not self._is_running:
            return self.elapsed_seconds

        self._accumulated_ticks += max(0.0, float(delta_seconds))
        return self.elapsed_seconds

    def set_elapsed(self, seconds: float) -> None:
        """Directly set or synchronize elapsed in-game seconds."""
        val = max(0.0, float(seconds))
        self._stopped_elapsed = val
        self._accumulated_ticks = val
        self._initial_seconds = val
        if self._is_running:
            self._start_time = self._clock()

    def sync_elapsed(self, seconds: float) -> None:
        """Alias for set_elapsed for domain clarity."""
        self.set_elapsed(seconds)

    def stop(self) -> None:
        """Pause or stop the timer without clearing accumulated elapsed time."""
        if self._is_running:
            self._stopped_elapsed = self.elapsed_seconds
            self._is_running = False
            self._start_time = None

    def reset(self) -> None:
        """Reset elapsed duration to zero and stop the timer."""
        self._is_running = False
        self._start_time = None
        self._initial_seconds = 0.0
        self._accumulated_ticks = 0.0
        self._stopped_elapsed = 0.0

    @property
    def formatted_elapsed(self) -> str:
        """Formatted elapsed duration string in 'MM:SS' (e.g. '14:28')."""
        return format_duration(self.elapsed_seconds)

    @property
    def display_text(self) -> str:
        """Formatted display string for companion GUI or mobile view."""
        return f"⚔️ Oyunda: {self.formatted_elapsed}"

    def __repr__(self) -> str:
        return (
            f"InGameTimer(running={self.is_running}, "
            f"elapsed={self.formatted_elapsed})"
        )

    def __str__(self) -> str:
        return self.display_text


__all__ = ["InGameTimer", "QueueTimer", "format_duration"]
