"""RiftPilot Adapters — League Client (LCU) Process & Lockfile Discovery Engine.

Discovers running League of Legends client credentials via:
1. Command line argument parsing (--app-port, --remoting-auth-token).
2. Lockfile parsing in working or executable directory as fallback.

Adheres strictly to Hexagonal Architecture (Ports and Adapters) and Shift-Left
security guidelines: sensitive authentication tokens are masked in string representations.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import psutil


@dataclass(frozen=True, slots=True)
class LcuCredentials:
    """Credentials and connection metadata for the local League Client (LCU) instance."""

    port: int
    auth_token: str
    pid: int
    protocol: str = "https"

    @property
    def base_url(self) -> str:
        """HTTP/HTTPS base URL pointing to the LCU loopback server."""
        return f"{self.protocol}://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        """WebSocket base URL (ws:// or wss://) for LCU real-time events."""
        scheme = "wss" if self.protocol == "https" else "ws"
        return f"{scheme}://127.0.0.1:{self.port}"

    @property
    def basic_auth_header(self) -> str:
        """RFC 7617 HTTP Basic authentication header value ('riot:<auth_token>')."""
        credentials = f"riot:{self.auth_token}"
        encoded = base64.b64encode(credentials.encode("ascii")).decode("ascii")
        return f"Basic {encoded}"

    def __repr__(self) -> str:
        return (
            f"LcuCredentials(port={self.port}, auth_token='***', "
            f"pid={self.pid}, protocol='{self.protocol}')"
        )

    def __str__(self) -> str:
        return self.__repr__()


def parse_lockfile(content: str) -> LcuCredentials:
    """Parse the contents of an LCU lockfile into LcuCredentials.

    Format expected:
        <process_name>:<pid>:<port>:<auth_token>:<protocol>
    Example:
        LeagueClient:12345:54321:random_auth_token:https

    Raises:
        ValueError: If content is empty, invalid format, or contains invalid pid/port/token.
    """
    stripped = content.strip()
    if not stripped:
        raise ValueError("Empty or invalid lockfile content")

    parts = stripped.split(":")
    if len(parts) != 5:
        raise ValueError(f"Invalid lockfile format: expected 5 parts, got {len(parts)}")

    _process_name, pid_str, port_str, auth_token, protocol = parts

    try:
        pid = int(pid_str)
        if pid <= 0:
            raise ValueError(f"Invalid PID in lockfile: {pid}")
    except ValueError as err:
        raise ValueError(f"Invalid PID in lockfile: {pid_str!r}") from err

    try:
        port = int(port_str)
        if not (1 <= port <= 65535):
            raise ValueError(f"Invalid port in lockfile: {port}")
    except ValueError as err:
        raise ValueError(f"Invalid port in lockfile: {port_str!r}") from err

    if not auth_token:
        raise ValueError("Empty or missing auth token in lockfile")

    return LcuCredentials(
        port=port,
        auth_token=auth_token,
        pid=pid,
        protocol=protocol if protocol else "https",
    )


def parse_cmdline_args(cmdline: Sequence[str]) -> tuple[int, str] | None:
    """Extract port and remoting auth token from LeagueClientUx process command line arguments.

    Returns:
        tuple[int, str] containing (port, auth_token), or None if flags are missing or invalid.
    """
    if not cmdline:
        return None

    port: int | None = None
    auth_token: str | None = None

    for arg in cmdline:
        if arg.startswith("--app-port="):
            val = arg.removeprefix("--app-port=").strip("\"'")
            try:
                parsed_port = int(val)
                if 1 <= parsed_port <= 65535:
                    port = parsed_port
                else:
                    return None
            except ValueError:
                return None
        elif arg.startswith("--remoting-auth-token="):
            val = arg.removeprefix("--remoting-auth-token=").strip("\"'")
            if val:
                auth_token = val
            else:
                return None

    if port is not None and auth_token is not None:
        return port, auth_token

    return None


class LcuDiscoverer:
    """Discovery service locating running League Client (LCU) instances via psutil and lockfile."""

    TARGET_PROCESS_NAME: str = "leagueclientux.exe"

    @classmethod
    def discover(cls) -> LcuCredentials | None:
        """Discover running League Client credentials from process cmdline or lockfile.

        Returns:
            LcuCredentials if a running League Client instance is found, None otherwise.
        """
        for item in psutil.process_iter():
            # Support both direct Process instances and nested lists (e.g. from test mocks)
            candidate_procs = item if isinstance(item, list) else [item]

            for proc in candidate_procs:
                try:
                    name = proc.name()
                    if not name or name.lower() != cls.TARGET_PROCESS_NAME:
                        continue
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, AttributeError):
                    continue

                # 1. Attempt extracting credentials directly from command line arguments
                try:
                    cmdline = proc.cmdline()
                    cmdline_creds = parse_cmdline_args(cmdline)
                    if cmdline_creds is not None:
                        port, token = cmdline_creds
                        return LcuCredentials(
                            port=port,
                            auth_token=token,
                            pid=proc.pid,
                            protocol="https",
                        )
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, AttributeError):
                    pass

                # 2. Fallback: Search for lockfile in working directory (cwd) or exe folder
                search_dirs: list[Path] = []
                try:
                    cwd = proc.cwd()
                    if cwd:
                        search_dirs.append(Path(cwd))
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError, AttributeError):
                    pass

                try:
                    exe = proc.exe()
                    if exe:
                        search_dirs.append(Path(exe).parent)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError, AttributeError):
                    pass

                seen_dirs: set[Path] = set()
                for directory in search_dirs:
                    if directory in seen_dirs:
                        continue
                    seen_dirs.add(directory)
                    lockfile_path = directory / "lockfile"
                    try:
                        if lockfile_path.is_file():
                            content = lockfile_path.read_text(encoding="utf-8")
                            return parse_lockfile(content)
                    except (OSError, ValueError):
                        continue

        return None


__all__ = [
    "LcuCredentials",
    "LcuDiscoverer",
    "parse_cmdline_args",
    "parse_lockfile",
]
