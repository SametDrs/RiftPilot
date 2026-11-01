"""RiftPilot — Tests for LCU Process & Lockfile Discovery Engine (Milestone 2, Task 2.1).

This module verifies the behavior of the League Client (LCU) discovery engine:
- `LcuCredentials`: Dataclass/model storing port, auth token, pid, base_url, ws_url,
  basic_auth_header, and masking sensitive credentials in repr.
- `parse_lockfile`: Parses LCU lockfile format (process:pid:port:token:protocol)
  with strict validation on invalid/corrupted payloads.
- `parse_cmdline_args`: Extracts port and remoting auth token from LeagueClientUx
  process command line flags.
- `LcuDiscoverer`: Auto-discovers running League of Legends client via process inspection
  (psutil cmdline) or lockfile fallback.

Test-First / Shift-Left:
Target module: `riftpilot.adapters.lcu_discovery`
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock

import psutil
import pytest

# Target imports (expected to fail with ModuleNotFoundError in TDD Red Phase)
from riftpilot.adapters.lcu_discovery import (
    LcuCredentials,
    LcuDiscoverer,
    parse_cmdline_args,
    parse_lockfile,
)


# ============================================================================
# Test Helpers & Fixtures
# ============================================================================


def _create_mock_process(
    name: str = "LeagueClientUx.exe",
    pid: int = 12345,
    cmdline: list[str] | None = None,
    cwd: str = "C:\\Riot Games\\League of Legends",
    exe: str = "C:\\Riot Games\\League of Legends\\LeagueClientUx.exe",
) -> MagicMock:
    """Helper creating a psutil.Process test double supporting both methods and .info dict."""
    proc = MagicMock(spec=psutil.Process)
    proc.pid = pid
    proc.name.return_value = name
    proc.cmdline.return_value = cmdline if cmdline is not None else []
    proc.cwd.return_value = cwd
    proc.exe.return_value = exe
    proc.is_running.return_value = True
    proc.info = {
        "pid": pid,
        "name": name,
        "cmdline": proc.cmdline.return_value,
        "cwd": cwd,
        "exe": exe,
    }
    return proc


@pytest.fixture
def discoverer() -> LcuDiscoverer:
    """Provides a fresh LcuDiscoverer instance for tests."""
    return LcuDiscoverer()


# ============================================================================
# 1. LcuCredentials Tests
# ============================================================================


class TestLcuCredentials:
    """Tests for LcuCredentials data structure, properties, and sensitive masking."""

    def test_credentials_initialization_defaults(self) -> None:
        """Credentials must initialize with required fields and default protocol to https."""
        creds = LcuCredentials(
            port=54321,
            auth_token="test_secret_token_123",
            pid=9876,
        )
        assert creds.port == 54321
        assert creds.auth_token == "test_secret_token_123"
        assert creds.pid == 9876
        assert creds.protocol == "https"

    def test_credentials_explicit_protocol(self) -> None:
        """Explicit protocol assignment must be preserved."""
        creds = LcuCredentials(
            port=8080,
            auth_token="auth_abc",
            pid=1111,
            protocol="http",
        )
        assert creds.protocol == "http"

    def test_credentials_base_url_format(self) -> None:
        """base_url must point to loopback address with appropriate protocol and port."""
        creds = LcuCredentials(port=54321, auth_token="secret", pid=100)
        assert creds.base_url == "https://127.0.0.1:54321"

    def test_credentials_ws_url_format(self) -> None:
        """ws_url must construct secure websocket URL with loopback address and port."""
        creds = LcuCredentials(port=54321, auth_token="secret", pid=100)
        assert creds.ws_url == "wss://127.0.0.1:54321"

    def test_credentials_basic_auth_header_rfc_compliance(self) -> None:
        """basic_auth_header must encode 'riot:<auth_token>' according to RFC 7617 Basic Auth."""
        auth_token = "secret_riot_pass_xyz"
        creds = LcuCredentials(port=54321, auth_token=auth_token, pid=100)

        expected_encoded = base64.b64encode(f"riot:{auth_token}".encode("ascii")).decode("ascii")
        expected_header = f"Basic {expected_encoded}"

        assert creds.basic_auth_header == expected_header

        # Verify round-trip decoding
        scheme, b64_part = creds.basic_auth_header.split(" ", 1)
        assert scheme == "Basic"
        decoded = base64.b64decode(b64_part).decode("utf-8")
        assert decoded == f"riot:{auth_token}"

    def test_credentials_repr_masks_sensitive_auth_token(self) -> None:
        """auth_token must NOT appear in plain text inside repr() or str() output."""
        sensitive_token = "super_confidential_token_99999"
        creds = LcuCredentials(port=54321, auth_token=sensitive_token, pid=12345)

        repr_output = repr(creds)
        str_output = str(creds)

        # Plaintext token must never leak in string representation
        assert sensitive_token not in repr_output, f"Plaintext auth_token leaked in repr: {repr_output}"
        assert sensitive_token not in str_output, f"Plaintext auth_token leaked in str: {str_output}"

        # Safe diagnostic fields should remain visible for logging/troubleshooting
        assert "54321" in repr_output
        assert "12345" in repr_output

    def test_credentials_equality(self) -> None:
        """Two LcuCredentials with matching properties must be equal."""
        creds1 = LcuCredentials(port=5000, auth_token="token_a", pid=100)
        creds2 = LcuCredentials(port=5000, auth_token="token_a", pid=100)
        creds3 = LcuCredentials(port=5001, auth_token="token_a", pid=100)

        assert creds1 == creds2
        assert creds1 != creds3


# ============================================================================
# 2. parse_lockfile Tests
# ============================================================================


class TestParseLockfile:
    """Tests for parse_lockfile content decoding and error boundary handling."""

    def test_parse_lockfile_valid_standard_format(self) -> None:
        """Standard 5-part lockfile string must correctly extract all fields."""
        content = "LeagueClient:12345:54321:random_auth_token:https"
        creds = parse_lockfile(content)

        assert creds.pid == 12345
        assert creds.port == 54321
        assert creds.auth_token == "random_auth_token"
        assert creds.protocol == "https"
        assert creds.base_url == "https://127.0.0.1:54321"
        assert creds.ws_url == "wss://127.0.0.1:54321"

    def test_parse_lockfile_with_trailing_newlines_and_spaces(self) -> None:
        """Lockfile parser must tolerate leading/trailing newlines and carriage returns."""
        content = "\r\n  LeagueClient:9999:49876:token_with_whitespace:https\n\r"
        creds = parse_lockfile(content)

        assert creds.pid == 9999
        assert creds.port == 49876
        assert creds.auth_token == "token_with_whitespace"
        assert creds.protocol == "https"

    def test_parse_lockfile_with_special_characters_in_token(self) -> None:
        """Auth tokens containing hyphens, underscores, or base64 equal signs must be parsed."""
        content = "LeagueClientUx:3456:61234:aBcD_123-xyz==:https"
        creds = parse_lockfile(content)

        assert creds.pid == 3456
        assert creds.port == 61234
        assert creds.auth_token == "aBcD_123-xyz=="
        assert creds.protocol == "https"

    @pytest.mark.parametrize(
        "empty_input",
        [
            "",
            "   ",
            "\n",
            "\r\n",
            "\t  \n",
        ],
    )
    def test_parse_lockfile_raises_on_empty_or_whitespace_content(
        self, empty_input: str
    ) -> None:
        """Empty or whitespace-only lockfile content must raise ValueError."""
        with pytest.raises(ValueError, match=r"(?i)empty|invalid"):
            parse_lockfile(empty_input)

    @pytest.mark.parametrize(
        "malformed_content",
        [
            "LeagueClient",
            "LeagueClient:12345",
            "LeagueClient:12345:54321",
            "LeagueClient:12345:54321:token",  # 4 parts, missing protocol
            "LeagueClient:12345:54321:token:https:extra_part",  # 6 parts, too many
            "::::",
            "LeagueClient:::token:https",
        ],
    )
    def test_parse_lockfile_raises_on_invalid_part_count(
        self, malformed_content: str
    ) -> None:
        """Lockfiles not containing exactly 5 parts must raise ValueError."""
        with pytest.raises(ValueError, match=r"(?i)format|parts|invalid"):
            parse_lockfile(malformed_content)

    @pytest.mark.parametrize(
        "bad_port_content",
        [
            "LeagueClient:12345:not_a_port:token:https",
            "LeagueClient:12345:5432a:token:https",
            "LeagueClient:12345:0:token:https",  # Port 0 is invalid
            "LeagueClient:12345:-100:token:https",  # Negative port
            "LeagueClient:12345:70000:token:https",  # Port exceeds 65535
        ],
    )
    def test_parse_lockfile_raises_on_invalid_port(
        self, bad_port_content: str
    ) -> None:
        """Non-numeric or out-of-range port numbers must raise ValueError."""
        with pytest.raises(ValueError, match=r"(?i)port"):
            parse_lockfile(bad_port_content)

    @pytest.mark.parametrize(
        "bad_pid_content",
        [
            "LeagueClient:not_a_pid:54321:token:https",
            "LeagueClient:-50:54321:token:https",
            "LeagueClient:0:54321:token:https",
        ],
    )
    def test_parse_lockfile_raises_on_invalid_pid(
        self, bad_pid_content: str
    ) -> None:
        """Non-numeric or invalid PID must raise ValueError."""
        with pytest.raises(ValueError, match=r"(?i)pid"):
            parse_lockfile(bad_pid_content)

    def test_parse_lockfile_raises_on_empty_token(self) -> None:
        """Missing/empty auth token part must raise ValueError."""
        with pytest.raises(ValueError, match=r"(?i)token"):
            parse_lockfile("LeagueClient:12345:54321::https")


# ============================================================================
# 3. parse_cmdline_args Tests
# ============================================================================


class TestParseCmdlineArgs:
    """Tests for parse_cmdline_args CLI flag extraction."""

    def test_parse_cmdline_args_standard_pair(self) -> None:
        """Extract port and auth token from canonical command line flags."""
        cmdline = ["--app-port=54321", "--remoting-auth-token=abc123token"]
        result = parse_cmdline_args(cmdline)

        assert result is not None
        port, token = result
        assert port == 54321
        assert token == "abc123token"

    def test_parse_cmdline_args_reversed_order(self) -> None:
        """Argument order must not affect extraction."""
        cmdline = ["--remoting-auth-token=xyz987token", "--app-port=49152"]
        result = parse_cmdline_args(cmdline)

        assert result is not None
        port, token = result
        assert port == 49152
        assert token == "xyz987token"

    def test_parse_cmdline_args_in_full_argument_list(self) -> None:
        """Flags embedded among realistic LeagueClientUx process arguments must be discovered."""
        cmdline = [
            "C:\\Riot Games\\League of Legends\\LeagueClientUx.exe",
            "--app-name=LeagueClient",
            "--app-port=54321",
            "--install-directory=C:\\Riot Games\\League of Legends",
            "--remoting-auth-token=secret_auth_token_456",
            "--no-proxy-server",
            "--crash-reporting-enabled=false",
        ]
        result = parse_cmdline_args(cmdline)

        assert result == (54321, "secret_auth_token_456")

    @pytest.mark.parametrize(
        ("port_flag", "token_flag", "expected_port", "expected_token"),
        [
            ('--app-port="54321"', '--remoting-auth-token="quoted_token"', 54321, "quoted_token"),
            ("--app-port='54321'", "--remoting-auth-token='single_quoted'", 54321, "single_quoted"),
        ],
    )
    def test_parse_cmdline_args_handles_enclosing_quotes(
        self,
        port_flag: str,
        token_flag: str,
        expected_port: int,
        expected_token: str,
    ) -> None:
        """Quoted argument values must be properly stripped."""
        cmdline = [port_flag, token_flag]
        result = parse_cmdline_args(cmdline)

        assert result == (expected_port, expected_token)

    def test_parse_cmdline_args_missing_port_flag_returns_none(self) -> None:
        """If --app-port is missing, must return None."""
        cmdline = ["--remoting-auth-token=abc123token", "--other-flag=1"]
        assert parse_cmdline_args(cmdline) is None

    def test_parse_cmdline_args_missing_token_flag_returns_none(self) -> None:
        """If --remoting-auth-token is missing, must return None."""
        cmdline = ["--app-port=54321", "--other-flag=1"]
        assert parse_cmdline_args(cmdline) is None

    def test_parse_cmdline_args_empty_list_returns_none(self) -> None:
        """Empty command line argument list must return None."""
        assert parse_cmdline_args([]) is None

    def test_parse_cmdline_args_unrelated_flags_returns_none(self) -> None:
        """List with no matching flags must return None."""
        cmdline = ["--foo=123", "--bar=baz", "LeagueClient.exe"]
        assert parse_cmdline_args(cmdline) is None

    @pytest.mark.parametrize(
        "invalid_port_flag",
        [
            "--app-port=not_an_int",
            "--app-port=",
            "--app-port=0",
            "--app-port=-5",
            "--app-port=70000",
        ],
    )
    def test_parse_cmdline_args_invalid_port_returns_none(
        self, invalid_port_flag: str
    ) -> None:
        """Invalid port in arguments must return None instead of raising an unhandled error."""
        cmdline = [invalid_port_flag, "--remoting-auth-token=abc123token"]
        assert parse_cmdline_args(cmdline) is None

    def test_parse_cmdline_args_empty_token_returns_none(self) -> None:
        """Empty auth token flag must return None."""
        cmdline = ["--app-port=54321", "--remoting-auth-token="]
        assert parse_cmdline_args(cmdline) is None


# ============================================================================
# 4. LcuDiscoverer Tests
# ============================================================================


class TestLcuDiscoverer:
    """Tests for LcuDiscoverer client auto-discovery across processes and lockfiles."""

    def test_discover_via_cmdline_success(
        self,
        discoverer: LcuDiscoverer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Discover credentials from running LeagueClientUx.exe cmdline arguments."""
        mock_proc = _create_mock_process(
            name="LeagueClientUx.exe",
            pid=12345,
            cmdline=["--app-port=54321", "--remoting-auth-token=cmdline_secret_token"],
        )
        monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: [mock_proc])

        creds = discoverer.discover()

        assert creds is not None
        assert isinstance(creds, LcuCredentials)
        assert creds.port == 54321
        assert creds.auth_token == "cmdline_secret_token"
        assert creds.pid == 12345
        assert creds.protocol == "https"
        assert creds.base_url == "https://127.0.0.1:54321"

    def test_discover_via_cmdline_case_insensitive_process_name(
        self,
        discoverer: LcuDiscoverer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Process name matching must be case-insensitive (e.g. leagueclientux.exe)."""
        mock_proc = _create_mock_process(
            name="leagueclientux.exe",
            pid=4321,
            cmdline=["--app-port=50000", "--remoting-auth-token=lowercase_token"],
        )
        monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: [mock_proc])

        creds = discoverer.discover()

        assert creds is not None
        assert creds.port == 50000
        assert creds.auth_token == "lowercase_token"

    def test_discover_ignores_unrelated_processes(
        self,
        discoverer: LcuDiscoverer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unrelated processes in process list must be skipped until LoL client is reached."""
        proc_chrome = _create_mock_process(name="chrome.exe", pid=1001)
        proc_explorer = _create_mock_process(name="explorer.exe", pid=1002)
        proc_lcu = _create_mock_process(
            name="LeagueClientUx.exe",
            pid=9999,
            cmdline=["--app-port=51111", "--remoting-auth-token=target_token"],
        )
        monkeypatch.setattr(
            psutil,
            "process_iter",
            lambda *args, **kwargs: [proc_chrome, proc_explorer, proc_lcu],
        )

        creds = discoverer.discover()

        assert creds is not None
        assert creds.pid == 9999
        assert creds.port == 51111
        assert creds.auth_token == "target_token"

    def test_discover_via_lockfile_fallback_when_cmdline_flags_absent(
        self,
        discoverer: LcuDiscoverer,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fallback to lockfile in process cwd/exe folder when cmdline has no port/token flags."""
        # Create lockfile on disk in temporary directory
        lockfile_path = tmp_path / "lockfile"
        lockfile_path.write_text("LeagueClient:8888:52222:lockfile_fallback_token:https", encoding="utf-8")

        mock_proc = _create_mock_process(
            name="LeagueClientUx.exe",
            pid=8888,
            cmdline=["LeagueClientUx.exe"],  # No port/token flags
            cwd=str(tmp_path),
            exe=str(tmp_path / "LeagueClientUx.exe"),
        )
        monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: [mock_proc])

        creds = discoverer.discover()

        assert creds is not None
        assert creds.port == 52222
        assert creds.auth_token == "lockfile_fallback_token"
        assert creds.pid == 8888
        assert creds.protocol == "https"

    def test_discover_via_lockfile_fallback_when_cmdline_raises_access_denied(
        self,
        discoverer: LcuDiscoverer,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When proc.cmdline() raises AccessDenied, fallback to lockfile should still succeed."""
        lockfile_path = tmp_path / "lockfile"
        lockfile_path.write_text("LeagueClient:7777:53333:access_denied_token:https", encoding="utf-8")

        mock_proc = _create_mock_process(
            name="LeagueClientUx.exe",
            pid=7777,
            cwd=str(tmp_path),
            exe=str(tmp_path / "LeagueClientUx.exe"),
        )
        mock_proc.cmdline.side_effect = psutil.AccessDenied(pid=7777)
        monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: [mock_proc])

        creds = discoverer.discover()

        assert creds is not None
        assert creds.port == 53333
        assert creds.auth_token == "access_denied_token"

    def test_discover_returns_none_when_no_league_process_exists(
        self,
        discoverer: LcuDiscoverer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Return None when no League of Legends client process is running."""
        procs = [
            _create_mock_process(name="explorer.exe", pid=10),
            _create_mock_process(name="svchost.exe", pid=20),
        ]
        monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: [procs])

        creds = discoverer.discover()
        assert creds is None

    def test_discover_returns_none_when_process_list_is_empty(
        self,
        discoverer: LcuDiscoverer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Return None when psutil.process_iter yields empty list."""
        monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: [])

        assert discoverer.discover() is None

    def test_discover_returns_none_when_cmdline_empty_and_no_lockfile(
        self,
        discoverer: LcuDiscoverer,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Return None if process has no cmdline flags and no lockfile is present."""
        mock_proc = _create_mock_process(
            name="LeagueClientUx.exe",
            pid=1234,
            cmdline=[],
            cwd=str(tmp_path),
            exe=str(tmp_path / "LeagueClientUx.exe"),
        )
        # Ensure lockfile does not exist in tmp_path
        monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: [mock_proc])

        assert discoverer.discover() is None

    def test_discover_handles_nosuchprocess_during_iteration(
        self,
        discoverer: LcuDiscoverer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Processes terminating during psutil.process_iter iteration must be safely handled."""
        dying_proc = _create_mock_process(name="LeagueClientUx.exe", pid=5555)
        dying_proc.name.side_effect = psutil.NoSuchProcess(pid=5555)

        monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: [dying_proc])

        assert discoverer.discover() is None

    def test_discover_handles_zombie_process_during_iteration(
        self,
        discoverer: LcuDiscoverer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Zombie processes encountered during iteration must not crash discovery."""
        zombie_proc = _create_mock_process(
            name="LeagueClientUx.exe",
            pid=6666,
            cwd="C:\\nonexistent_zombie_path",
            exe="C:\\nonexistent_zombie_path\\LeagueClientUx.exe",
        )
        zombie_proc.cmdline.side_effect = psutil.ZombieProcess(pid=6666)
        zombie_proc.cwd.side_effect = psutil.ZombieProcess(pid=6666)
        zombie_proc.exe.side_effect = psutil.ZombieProcess(pid=6666)

        monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: [zombie_proc])

        assert discoverer.discover() is None

    def test_discover_handles_corrupted_lockfile_gracefully(
        self,
        discoverer: LcuDiscoverer,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Corrupted lockfile content must be caught gracefully and return None."""
        corrupted_lockfile = tmp_path / "lockfile"
        corrupted_lockfile.write_text("invalid:content", encoding="utf-8")

        mock_proc = _create_mock_process(
            name="LeagueClientUx.exe",
            pid=2222,
            cmdline=[],
            cwd=str(tmp_path),
            exe=str(tmp_path / "LeagueClientUx.exe"),
        )
        monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: [mock_proc])

        # Must not raise ValueError, should return None
        assert discoverer.discover() is None

    def test_discover_handles_lockfile_os_error_gracefully(
        self,
        discoverer: LcuDiscoverer,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OSError / PermissionError when reading lockfile must be caught gracefully."""
        lockfile_path = tmp_path / "lockfile"
        lockfile_path.write_text("content", encoding="utf-8")

        mock_proc = _create_mock_process(
            name="LeagueClientUx.exe",
            pid=3333,
            cmdline=[],
            cwd=str(tmp_path),
            exe=str(tmp_path / "LeagueClientUx.exe"),
        )
        monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: [mock_proc])

        # Simulate read permission failure
        def _failing_read_text(*args, **kwargs):
            raise PermissionError("Access is denied to lockfile")

        monkeypatch.setattr(Path, "read_text", _failing_read_text)

        assert discoverer.discover() is None

    def test_discover_callable_from_instance_and_class_interface(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify discover() can be invoked on LcuDiscoverer instances or directly if implemented as classmethod."""
        mock_proc = _create_mock_process(
            name="LeagueClientUx.exe",
            pid=1234,
            cmdline=["--app-port=55555", "--remoting-auth-token=token_1"],
        )
        monkeypatch.setattr(psutil, "process_iter", lambda *args, **kwargs: [mock_proc])

        discoverer_instance = LcuDiscoverer()
        creds_instance = discoverer_instance.discover()
        assert creds_instance is not None
        assert creds_instance.port == 55555
