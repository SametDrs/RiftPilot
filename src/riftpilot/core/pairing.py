"""RiftPilot Core Domain — Cryptographic Pairing & Device Session Management.

Implements the zero-trust pairing and session management domain as specified
in ADR-0004:
- One-Time Token (OTT) generation and single-use burn-on-read verification.
- Device session lifecycle: creation, continuous validation, last-seen tracking,
  and individual / bulk device revocation.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field


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
    to prevent replay attacks over local or remote networks.
    """

    def __init__(self) -> None:
        """Initialize an empty in-memory store for pairing tokens."""
        self._tokens: dict[str, PairingToken] = {}

    def purge_expired(self, now: float | None = None) -> int:
        """Purge all expired pairing tokens from internal storage to prevent memory leaks.

        Args:
            now: Optional reference timestamp (defaults to current system time).

        Returns:
            The number of purged expired tokens.
        """
        current_time = time.time() if now is None else now
        expired_keys = [
            token_key
            for token_key, pairing_token in list(self._tokens.items())
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


class SessionManager:
    """Manages persistent device sessions, access validation, and revocation."""

    def __init__(self) -> None:
        """Initialize an empty in-memory store for active and inactive device sessions."""
        self._sessions: dict[str, DeviceSession] = {}

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
        self._sessions[session_token] = session
        return session

    def validate_session(self, session_token: str) -> DeviceSession | None:
        """Validate a session token and refresh its last-seen timestamp if active.

        Args:
            session_token: Secret token presented by the client.

        Returns:
            The DeviceSession if valid and active, None otherwise.
        """
        if not session_token or not session_token.strip():
            return None

        session = self._sessions.get(session_token)
        if session is None or not session.is_active:
            return None

        session.last_seen_at = time.time()
        return session

    def list_active_sessions(self) -> list[DeviceSession]:
        """List all currently active device sessions.

        Returns:
            A list of DeviceSession objects that have is_active == True.
        """
        return [session for session in self._sessions.values() if session.is_active]

    def revoke_session(self, session_token: str) -> bool:
        """Revoke an active session by its session token.

        Args:
            session_token: The secret token of the session to revoke.

        Returns:
            True if an active session was found and revoked, False otherwise.
        """
        if not session_token or not session_token.strip():
            return False

        session = self._sessions.get(session_token)
        if session is None or not session.is_active:
            return False

        session.is_active = False
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

        revoked_count = 0
        for session in list(self._sessions.values()):
            if session.client_instance_id == client_instance_id and session.is_active:
                session.is_active = False
                revoked_count += 1

        return revoked_count > 0


__all__ = [
    "DeviceSession",
    "OTTManager",
    "PairingToken",
    "SessionManager",
]
