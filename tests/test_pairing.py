"""RiftPilot — Tests for Cryptographic Pairing Engine (Milestone 1, Task 1.3).

This module verifies the behavior of the pairing and session management domain
as specified in ADR-0004:
- OTTManager: Single-use cryptographic One-Time Token (OTT) generation,
  burn-on-read validation, replay attack prevention, and TTL expiration.
- SessionManager: Persistent device session management, validation,
  last-seen timestamp tracking, and granular/bulk revocation.

Test-First / Shift-Left:
Target module: `riftpilot.core.pairing`
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

import pytest

# Target domain imports (expected to fail / raise ImportError until Task 1.3 is implemented)
from riftpilot.core.pairing import (
    DeviceSession,
    OTTManager,
    PairingToken,
    SessionManager,
)


def _to_timestamp(val: datetime | float | int) -> float:
    """Helper to convert either datetime or numeric timestamp to float seconds."""
    if isinstance(val, datetime):
        return val.timestamp()
    return float(val)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def ott_manager() -> OTTManager:
    """Provides a fresh OTTManager instance for each test."""
    return OTTManager()


@pytest.fixture
def session_manager() -> SessionManager:
    """Provides a fresh SessionManager instance for each test."""
    return SessionManager()


# ============================================================================
# 1. OTTManager Tests
# ============================================================================


class TestOTTManagerGenerate:
    """Tests for OTTManager.generate_ott token generation."""

    def test_generate_ott_returns_pairing_token_instance(
        self, ott_manager: OTTManager
    ) -> None:
        """generate_ott() must return a PairingToken domain object."""
        token_obj = ott_manager.generate_ott()
        assert isinstance(token_obj, PairingToken)
        assert hasattr(token_obj, "token")
        assert hasattr(token_obj, "created_at")
        assert hasattr(token_obj, "expires_at")

    def test_generate_ott_token_length_and_url_safe(
        self, ott_manager: OTTManager
    ) -> None:
        """Token string must be at least 32 characters long and URL-safe."""
        token_obj = ott_manager.generate_ott()
        raw_token = token_obj.token

        assert isinstance(raw_token, str)
        assert len(raw_token) >= 32, f"Token length {len(raw_token)} < 32 characters"
        # Must be safe for URLs (alphanumeric, -, _, ., ~)
        assert re.match(
            r"^[A-Za-z0-9_\-\.~]+$", raw_token
        ), f"Token contains non-URL-safe characters: {raw_token}"

    def test_generate_ott_entropy_and_uniqueness(
        self, ott_manager: OTTManager
    ) -> None:
        """Consecutively generated OTTs must be cryptographically unique with high entropy."""
        generated = [ott_manager.generate_ott().token for _ in range(100)]
        # All 100 tokens must be completely distinct
        assert len(set(generated)) == 100

        # Character diversity across tokens should reflect cryptographic randomness
        all_chars = set("".join(generated))
        assert len(all_chars) >= 20, "Generated tokens have suspiciously low character variety"

    def test_generate_ott_default_ttl_and_timestamps(
        self, ott_manager: OTTManager
    ) -> None:
        """Default TTL must be 90.0 seconds and expires_at > created_at."""
        token_obj = ott_manager.generate_ott()

        created_ts = _to_timestamp(token_obj.created_at)
        expires_ts = _to_timestamp(token_obj.expires_at)

        assert expires_ts > created_ts
        diff = expires_ts - created_ts
        assert pytest.approx(diff, rel=1e-2) == 90.0

    def test_generate_ott_custom_ttl(self, ott_manager: OTTManager) -> None:
        """Custom ttl_seconds must be honored."""
        custom_ttl = 45.0
        token_obj = ott_manager.generate_ott(ttl_seconds=custom_ttl)

        created_ts = _to_timestamp(token_obj.created_at)
        expires_ts = _to_timestamp(token_obj.expires_at)

        diff = expires_ts - created_ts
        assert pytest.approx(diff, rel=1e-2) == custom_ttl


class TestOTTManagerVerifyAndBurn:
    """Tests for OTTManager.verify_and_burn verification and single-use logic."""

    def test_verify_valid_token_first_call_succeeds(
        self, ott_manager: OTTManager
    ) -> None:
        """Verifying a freshly generated, unexpired token must return True."""
        token_obj = ott_manager.generate_ott(ttl_seconds=90.0)
        assert ott_manager.verify_and_burn(token_obj.token) is True

    def test_replay_attack_prevention_burn_on_read(
        self, ott_manager: OTTManager
    ) -> None:
        """A token must be burned on first use; subsequent calls with the SAME token must return False."""
        token_obj = ott_manager.generate_ott(ttl_seconds=90.0)
        raw_token = token_obj.token

        # First call succeeds (burns the token)
        first_call = ott_manager.verify_and_burn(raw_token)
        assert first_call is True

        # Second call with identical token MUST fail (Replay attack prevention)
        second_call = ott_manager.verify_and_burn(raw_token)
        assert second_call is False, "Replay attack detected: token was not burned on first read!"

        # Third attempt should also fail
        third_call = ott_manager.verify_and_burn(raw_token)
        assert third_call is False

    def test_expired_token_rejected(self, ott_manager: OTTManager) -> None:
        """A token whose TTL has expired must be rejected (return False)."""
        # Create token with very short TTL
        token_obj = ott_manager.generate_ott(ttl_seconds=0.03)
        time.sleep(0.06)

        assert ott_manager.verify_and_burn(token_obj.token) is False

    def test_non_existent_token_rejected(self, ott_manager: OTTManager) -> None:
        """Verifying a token that was never issued must return False."""
        bogus_token = "totally_fake_pairing_token_0123456789abcdef"
        assert ott_manager.verify_and_burn(bogus_token) is False

    def test_empty_and_whitespace_tokens_rejected(
        self, ott_manager: OTTManager
    ) -> None:
        """Empty or whitespace strings must return False without crashing."""
        assert ott_manager.verify_and_burn("") is False
        assert ott_manager.verify_and_burn("   ") is False
        assert ott_manager.verify_and_burn("\n\t") is False

    def test_multiple_tokens_independent_lifecycle(
        self, ott_manager: OTTManager
    ) -> None:
        """Burning token A must not affect the validity of unburned token B."""
        token_a = ott_manager.generate_ott(ttl_seconds=90.0)
        token_b = ott_manager.generate_ott(ttl_seconds=90.0)

        # Burn token A
        assert ott_manager.verify_and_burn(token_a.token) is True
        assert ott_manager.verify_and_burn(token_a.token) is False

        # Token B must still be valid
        assert ott_manager.verify_and_burn(token_b.token) is True
        assert ott_manager.verify_and_burn(token_b.token) is False


# ============================================================================
# 2. SessionManager Tests
# ============================================================================


class TestSessionManagerCreate:
    """Tests for SessionManager.create_session."""

    def test_create_session_returns_device_session_instance(
        self, session_manager: SessionManager
    ) -> None:
        """create_session() must return a DeviceSession instance."""
        session = session_manager.create_session(
            device_name="iPhone 15 Pro",
            client_instance_id="client-uuid-001",
        )
        assert isinstance(session, DeviceSession)

    def test_create_session_token_length_and_entropy(
        self, session_manager: SessionManager
    ) -> None:
        """Session token must be at least 32 characters long and URL-safe."""
        session = session_manager.create_session(
            device_name="Pixel 8",
            client_instance_id="client-uuid-002",
        )
        token = session.session_token

        assert isinstance(token, str)
        assert len(token) >= 32, f"Session token length {len(token)} < 32 characters"
        assert re.match(
            r"^[A-Za-z0-9_\-\.~]+$", token
        ), f"Session token contains non-URL-safe characters: {token}"

    def test_create_session_stores_metadata_correctly(
        self, session_manager: SessionManager
    ) -> None:
        """Session metadata (device_name, client_instance_id, is_active) must be correctly set."""
        device_name = "Samsung Galaxy S24"
        client_id = "device-uuid-999"

        session = session_manager.create_session(
            device_name=device_name,
            client_instance_id=client_id,
        )

        assert session.device_name == device_name
        assert session.client_instance_id == client_id
        assert session.is_active is True

    def test_create_session_initializes_timestamps(
        self, session_manager: SessionManager
    ) -> None:
        """created_at and last_seen_at must be populated on session creation."""
        session = session_manager.create_session(
            device_name="iPad Air",
            client_instance_id="client-ipad-1",
        )

        assert hasattr(session, "created_at")
        assert hasattr(session, "last_seen_at")

        created_ts = _to_timestamp(session.created_at)
        last_seen_ts = _to_timestamp(session.last_seen_at)

        assert created_ts > 0
        assert last_seen_ts > 0
        # On creation, last_seen_at is equal or very close to created_at
        assert abs(last_seen_ts - created_ts) < 1.0

    def test_create_multiple_sessions_unique_tokens(
        self, session_manager: SessionManager
    ) -> None:
        """Multiple sessions for the same or different devices must yield unique tokens."""
        s1 = session_manager.create_session("Device A", "inst-1")
        s2 = session_manager.create_session("Device A", "inst-1")
        s3 = session_manager.create_session("Device B", "inst-2")

        assert s1.session_token != s2.session_token
        assert s2.session_token != s3
        assert s1.session_token != s3


class TestSessionManagerValidate:
    """Tests for SessionManager.validate_session."""

    def test_validate_valid_session_success(
        self, session_manager: SessionManager
    ) -> None:
        """validate_session() with a valid token returns the DeviceSession."""
        created = session_manager.create_session(
            device_name="Xiaomi 14",
            client_instance_id="inst-x",
        )

        validated = session_manager.validate_session(created.session_token)
        assert validated is not None
        assert validated.session_token == created.session_token
        assert validated.device_name == "Xiaomi 14"
        assert validated.is_active is True

    def test_validate_session_updates_last_seen_at(
        self, session_manager: SessionManager
    ) -> None:
        """Validating a session must update its last_seen_at timestamp."""
        created = session_manager.create_session(
            device_name="OnePlus 12",
            client_instance_id="inst-op",
        )
        initial_last_seen_ts = _to_timestamp(created.last_seen_at)

        # Brief sleep to ensure time difference
        time.sleep(0.02)

        validated = session_manager.validate_session(created.session_token)
        assert validated is not None

        updated_last_seen_ts = _to_timestamp(validated.last_seen_at)
        assert (
            updated_last_seen_ts >= initial_last_seen_ts
        ), "validate_session must update or maintain last_seen_at"

    def test_validate_unknown_token_returns_none(
        self, session_manager: SessionManager
    ) -> None:
        """Validating an unknown/bogus session token must return None."""
        bogus_token = "st_unknown_token_9876543210_abcdef123456"
        assert session_manager.validate_session(bogus_token) is None

    def test_validate_empty_token_returns_none(
        self, session_manager: SessionManager
    ) -> None:
        """Validating an empty or blank token string must return None."""
        assert session_manager.validate_session("") is None
        assert session_manager.validate_session("   ") is None

    def test_validate_revoked_session_returns_none(
        self, session_manager: SessionManager
    ) -> None:
        """Validating a revoked session must return None."""
        session = session_manager.create_session(
            device_name="Revoked Device",
            client_instance_id="inst-rev",
        )
        session_manager.revoke_session(session.session_token)

        assert session_manager.validate_session(session.session_token) is None


class TestSessionManagerListActive:
    """Tests for SessionManager.list_active_sessions."""

    def test_list_active_sessions_initially_empty(
        self, session_manager: SessionManager
    ) -> None:
        """Initially, active sessions list must be empty."""
        assert session_manager.list_active_sessions() == []

    def test_list_active_sessions_returns_all_active(
        self, session_manager: SessionManager
    ) -> None:
        """All created active sessions must appear in list_active_sessions()."""
        s1 = session_manager.create_session("Device 1", "inst-1")
        s2 = session_manager.create_session("Device 2", "inst-2")

        active = session_manager.list_active_sessions()
        assert len(active) == 2
        active_tokens = [s.session_token for s in active]
        assert s1.session_token in active_tokens
        assert s2.session_token in active_tokens

    def test_list_active_sessions_excludes_revoked(
        self, session_manager: SessionManager
    ) -> None:
        """Revoked sessions must be excluded from list_active_sessions()."""
        s1 = session_manager.create_session("Device 1", "inst-1")
        s2 = session_manager.create_session("Device 2", "inst-2")

        # Revoke s1
        session_manager.revoke_session(s1.session_token)

        active = session_manager.list_active_sessions()
        assert len(active) == 1
        assert active[0].session_token == s2.session_token


class TestSessionManagerRevoke:
    """Tests for SessionManager.revoke_session."""

    def test_revoke_existing_active_session_returns_true(
        self, session_manager: SessionManager
    ) -> None:
        """Revoking an existing active session must return True and set is_active=False."""
        session = session_manager.create_session(
            device_name="Device to Revoke",
            client_instance_id="inst-del",
        )

        result = session_manager.revoke_session(session.session_token)
        assert result is True

        # Validation must now fail
        assert session_manager.validate_session(session.session_token) is None

    def test_revoke_already_revoked_session_returns_false(
        self, session_manager: SessionManager
    ) -> None:
        """Revoking an already revoked session must return False."""
        session = session_manager.create_session(
            device_name="Device Double Revoke",
            client_instance_id="inst-double",
        )

        assert session_manager.revoke_session(session.session_token) is True
        # Second revocation attempt returns False (not active anymore)
        assert session_manager.revoke_session(session.session_token) is False

    def test_revoke_unknown_token_returns_false(
        self, session_manager: SessionManager
    ) -> None:
        """Revoking a non-existent token must return False."""
        assert session_manager.revoke_session("non_existent_token_1234567890") is False

    def test_revoke_empty_token_returns_false(
        self, session_manager: SessionManager
    ) -> None:
        """Revoking an empty token must return False."""
        assert session_manager.revoke_session("") is False
        assert session_manager.revoke_session("   ") is False


class TestSessionManagerRevokeDevice:
    """Tests for SessionManager.revoke_device bulk revocation."""

    def test_revoke_device_revokes_all_sessions_for_instance(
        self, session_manager: SessionManager
    ) -> None:
        """revoke_device() must revoke all active sessions belonging to that client_instance_id."""
        client_id = "device-bulk-001"

        # Create multiple sessions for the same client_instance_id (e.g. reconnects, multiple tabs)
        s1 = session_manager.create_session("Tablet Tab 1", client_id)
        s2 = session_manager.create_session("Tablet Tab 2", client_id)

        # Another device's session
        other_session = session_manager.create_session("Other Phone", "device-other-999")

        # Bulk revoke client_id
        revocation_result = session_manager.revoke_device(client_id)
        assert revocation_result is True

        # Both sessions of client_id must be invalid
        assert session_manager.validate_session(s1.session_token) is None
        assert session_manager.validate_session(s2.session_token) is None

        # Other device session must remain active
        assert session_manager.validate_session(other_session.session_token) is not None

        # Active list must only contain other_session
        active = session_manager.list_active_sessions()
        assert len(active) == 1
        assert active[0].session_token == other_session.session_token

    def test_revoke_device_unknown_or_already_revoked_returns_false(
        self, session_manager: SessionManager
    ) -> None:
        """Revoking a device with no active sessions must return False."""
        # Non-existent device
        assert session_manager.revoke_device("completely-unknown-device") is False

        # Device that was already revoked
        s = session_manager.create_session("Device Once", "inst-once")
        assert session_manager.revoke_device("inst-once") is True
        # Calling again should return False since no active sessions remain
        assert session_manager.revoke_device("inst-once") is False


class TestSecurityHardening:
    """Security hardening tests recommended by the Security Audit."""

    def test_purge_expired_tokens(self, ott_manager: OTTManager) -> None:
        """purge_expired should remove expired tokens from storage."""
        # 1. Create token with 0.05s TTL
        t1 = ott_manager.generate_ott(ttl_seconds=0.05)
        # 2. Create token with 60s TTL
        t2 = ott_manager.generate_ott(ttl_seconds=60.0)

        # Wait for t1 to expire
        time.sleep(0.06)

        # Purge
        purged = ott_manager.purge_expired()
        assert purged >= 1

        # t1 should be gone, t2 still valid
        assert ott_manager.verify_and_burn(t1.token) is False
        assert ott_manager.verify_and_burn(t2.token) is True

    def test_sensitive_tokens_not_in_repr(
        self, ott_manager: OTTManager, session_manager: SessionManager
    ) -> None:
        """Pairing and session tokens must not be exposed in dataclass repr (log safety)."""
        ott = ott_manager.generate_ott()
        session = session_manager.create_session("Test Device", "inst-123")

        assert ott.token not in repr(ott)
        assert session.session_token not in repr(session)
