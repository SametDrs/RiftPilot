"""RiftPilot Core Domain — Cryptographic Pairing & Device Session Management.

Implements the zero-trust pairing and session management domain as specified
in ADR-0004:
- One-Time Token (OTT) generation and single-use burn-on-read verification.
- Device session lifecycle: creation, continuous validation, last-seen tracking,
  idle / absolute expiry, and individual / bulk device revocation.

Both managers are shared between the Qt main thread (desktop UI: QR generation,
device list, revocation) and the Uvicorn server thread (pairing, request auth),
so all access to their internal state is serialised with a ``threading.RLock``.
Revocation listeners are invoked *outside* the lock so they may safely call back
into the manager.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Default idle lifetime of a device session (30 days without any activity).
DEFAULT_SESSION_IDLE_TTL_SECONDS: float = 30 * 24 * 60 * 60


@dataclass
class PairingToken:
    """Represents a short-lived cryptographic one-time token for device pairing."""

    token: str = field(repr=False)
    created_at: float
    expires_at: float

    def is_expired(self, now: float | None = None) -> bool:
        """Check whether the pairing token has expired relative to a given timestamp.

        Args:
            now: Optional reference timestamp (defaults to current system time).

        Returns:
            True if the token is expired, False otherwise.
        """
        current_time = time.time() if now is None else now
        return current_time >= self.expires_at


class OTTManager:
    """Manages short-lived, single-use One-Time Tokens (OTT) for QR pairing.

    Provides high-entropy URL-safe cryptographic tokens that are burned on first read
    to prevent replay attacks over local or remote networks. Thread-safe.
    """

    def __init__(self) -> None:
        """Initialize an empty in-memory store for pairing tokens."""
        self._tokens: dict[str, PairingToken] = {}
        self._lock = threading.RLock()

    def purge_expired(self, now: float | None = None) -> int:
        """Purge all expired pairing tokens from internal storage to prevent memory leaks.

        Args:
            now: Optional reference timestamp (defaults to current system time).

        Returns:
            The number of purged expired tokens.
        """
        current_time = time.time() if now is None else now
        with self._lock:
            expired_keys = [
                token_key
                for token_key, pairing_token in self._tokens.items()
                if pairing_token.is_expired(current_time)
            ]
            for token_key in expired_keys:
                self._tokens.pop(token_key, None)
        return len(expired_keys)

    def generate_ott(self, ttl_seconds: float = 90.0) -> PairingToken:
        """Generate a cryptographically secure single-use pairing token.

        Args:
            ttl_seconds: Lifespan of the pairing token in seconds (default: 90.0).

        Returns:
            PairingToken containing the URL-safe token and timestamp metadata.
        """
        self.purge_expired()
        raw_token = secrets.token_urlsafe(32)
        created_at = time.time()
        expires_at = created_at + ttl_seconds

        pairing_token = PairingToken(
            token=raw_token,
            created_at=created_at,
            expires_at=expires_at,
        )
        with self._lock:
            self._tokens[raw_token] = pairing_token
        return pairing_token

    def verify_and_burn(self, token: str) -> bool:
        """Verify and immediately consume (burn) an OTT upon first presentation.

        If the token exists and has not expired, it is purged from storage and verified.
        If the token is invalid, missing, whitespace-only, or expired, verification fails.

        Args:
            token: The raw pairing token string to verify.

        Returns:
            True if valid and successfully burned, False otherwise.
        """
        if not token or not token.strip():
            return False

        with self._lock:
            pairing_token = self._tokens.pop(token, None)
        if pairing_token is None:
            return False

        if pairing_token.is_expired():
            return False

        return True


@dataclass
class DeviceSession:
    """Represents an authenticated client device session.

    Attributes:
        session_id: Unique UUID identifier for the session entity.
        session_token: High-entropy secret token presented in requests/WebSockets.
        device_name: Human-readable name of the connecting device (e.g. "iPhone 15").
        client_instance_id: Stable identifier for the device browser or client instance.
        created_at: Epoch timestamp when the session was created.
        last_seen_at: Epoch timestamp of the most recent activity / validation.
        is_active: Whether the session is currently authorized and active.
    """

    session_id: str
    session_token: str = field(repr=False)
    device_name: str
    client_instance_id: str
    created_at: float
    last_seen_at: float
    is_active: bool = True


RevocationListener = Callable[[DeviceSession], None]


class SessionManager:
    """Manages persistent device sessions, access validation, expiry and revocation.

    Sessions expire after ``idle_ttl_seconds`` without validation activity and/or
    ``absolute_ttl_seconds`` after creation (``None`` disables the respective limit).
    Revoked and expired sessions are purged from memory; registered revocation
    listeners are notified so transports can drop live connections.
    """

    def __init__(
        self,
        idle_ttl_seconds: float | None = DEFAULT_SESSION_IDLE_TTL_SECONDS,
        absolute_ttl_seconds: float | None = None,
    ) -> None:
        """Initialize an empty, thread-safe in-memory session store.

        Args:
            idle_ttl_seconds: Max seconds between validations before a session
                expires (default 30 days). ``None`` disables idle expiry.
            absolute_ttl_seconds: Max session lifetime in seconds since creation.
                ``None`` (default) disables absolute expiry.
        """
        self._sessions: dict[str, DeviceSession] = {}
        self._lock = threading.RLock()
        self._listeners: list[RevocationListener] = []
        self.idle_ttl_seconds = idle_ttl_seconds
        self.absolute_ttl_seconds = absolute_ttl_seconds

    # ------------------------------------------------------------------ listeners

    def add_revocation_listener(self, listener: RevocationListener) -> None:
        """Register a callback invoked (on the revoking thread) per revoked/expired session."""
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_revocation_listener(self, listener: RevocationListener) -> None:
        """Unregister a previously added revocation listener (no-op if unknown)."""
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _notify_revoked(self, sessions: list[DeviceSession]) -> None:
        """Invoke listeners outside the lock; listener errors never propagate."""
        if not sessions:
            return
        with self._lock:
            listeners = list(self._listeners)
        for session in sessions:
            for listener in listeners:
                try:
                    listener(session)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("Session revocation listener failed: %s", exc)

    # --------------------------------------------------------------------- expiry

    def _is_expired(self, session: DeviceSession, now: float) -> bool:
        """Return True if the session exceeded its idle or absolute lifetime."""
        if (
            self.idle_ttl_seconds is not None
            and now - session.last_seen_at >= self.idle_ttl_seconds
        ):
            return True
        if (
            self.absolute_ttl_seconds is not None
            and now - session.created_at >= self.absolute_ttl_seconds
        ):
            return True
        return False

    def _purge_expired_locked(self, now: float) -> list[DeviceSession]:
        """Drop expired/inactive sessions. Caller must hold the lock."""
        stale_tokens = [
            token
            for token, session in self._sessions.items()
            if not session.is_active or self._is_expired(session, now)
        ]
        removed: list[DeviceSession] = []
        for token in stale_tokens:
            session = self._sessions.pop(token)
            if session.is_active:
                session.is_active = False
                removed.append(session)
        return removed

    def purge_expired(self, now: float | None = None) -> int:
        """Remove expired (and any lingering inactive) sessions from memory.

        Args:
            now: Optional reference timestamp (defaults to current system time).

        Returns:
            The number of active sessions that expired and were removed.
        """
        current_time = time.time() if now is None else now
        with self._lock:
            removed = self._purge_expired_locked(current_time)
        self._notify_revoked(removed)
        return len(removed)

    # ------------------------------------------------------------------ lifecycle

    def create_session(
        self, device_name: str, client_instance_id: str
    ) -> DeviceSession:
        """Create and store a new active device session.

        Args:
            device_name: User-friendly name of the connecting device.
            client_instance_id: Client-side unique instance UUID or device fingerprint.

        Returns:
            A newly initialized DeviceSession.
        """
        session_id = str(uuid.uuid4())
        session_token = secrets.token_urlsafe(32)
        now = time.time()

        session = DeviceSession(
            session_id=session_id,
            session_token=session_token,
            device_name=device_name,
            client_instance_id=client_instance_id,
            created_at=now,
            last_seen_at=now,
            is_active=True,
        )
        with self._lock:
            removed = self._purge_expired_locked(now)
            self._sessions[session_token] = session
        self._notify_revoked(removed)
        return session

    def get_session(self, session_token: str) -> DeviceSession | None:
        """Return the active, unexpired session for a token WITHOUT refreshing last-seen.

        Intended for passive checks (e.g. before pushing broadcasts) that must not
        keep an otherwise idle session alive.

        Args:
            session_token: Secret token presented by the client.

        Returns:
            The DeviceSession if valid, active and unexpired, None otherwise.
        """
        if not session_token or not session_token.strip():
            return None
        now = time.time()
        with self._lock:
            session = self._sessions.get(session_token)
            if session is None or not session.is_active:
                return None
            if not self._is_expired(session, now):
                return session
            self._sessions.pop(session_token, None)
            session.is_active = False
        self._notify_revoked([session])
        return None

    def validate_session(self, session_token: str) -> DeviceSession | None:
        """Validate a session token and refresh its last-seen timestamp if active.

        Args:
            session_token: Secret token presented by the client.

        Returns:
            The DeviceSession if valid, active and unexpired, None otherwise.
        """
        session = self.get_session(session_token)
        if session is None:
            return None
        with self._lock:
            if not session.is_active:
                return None
            session.last_seen_at = time.time()
        return session

    def list_active_sessions(self) -> list[DeviceSession]:
        """List all currently active (unexpired) device sessions.

        Returns:
            A list of DeviceSession objects that have is_active == True.
        """
        self.purge_expired()
        with self._lock:
            return [session for session in self._sessions.values() if session.is_active]

    def revoke_session(self, session_token: str) -> bool:
        """Revoke an active session by its session token and purge it from memory.

        Args:
            session_token: The secret token of the session to revoke.

        Returns:
            True if an active session was found and revoked, False otherwise.
        """
        if not session_token or not session_token.strip():
            return False

        with self._lock:
            session = self._sessions.get(session_token)
            if session is None or not session.is_active:
                return False
            self._sessions.pop(session_token, None)
            session.is_active = False

        self._notify_revoked([session])
        return True

    def revoke_device(self, client_instance_id: str) -> bool:
        """Revoke all active sessions belonging to a specific client instance.

        Args:
            client_instance_id: The unique identifier of the client device instance.

        Returns:
            True if at least one active session was revoked, False otherwise.
        """
        if not client_instance_id or not client_instance_id.strip():
            return False

        revoked: list[DeviceSession] = []
        with self._lock:
            for token, session in list(self._sessions.items()):
                if session.client_instance_id == client_instance_id and session.is_active:
                    session.is_active = False
                    self._sessions.pop(token, None)
                    revoked.append(session)

        self._notify_revoked(revoked)
        return bool(revoked)


__all__ = [
    "DEFAULT_SESSION_IDLE_TTL_SECONDS",
    "DeviceSession",
    "OTTManager",
    "PairingToken",
    "RevocationListener",
    "SessionManager",
]
