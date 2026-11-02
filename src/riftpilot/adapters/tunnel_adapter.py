"""RiftPilot Adapters — WAN Secure Reverse Tunnel Adapters (ADR-0003 Phase 2).

Provides:
- `MockTunnelAdapter`: Deterministic test double for testing and offline development.
- `CloudflareTunnelAdapter`: Spawns Cloudflare Quick Tunnels (`cloudflared`) to expose local Web Hub over HTTPS securely without port forwarding.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from typing import Any

from riftpilot.core.transport import (
    ITunnelPort,
    TunnelInfo,
    TunnelStatus,
)

logger = logging.getLogger("RiftPilot.TunnelAdapter")


class MockTunnelAdapter(ITunnelPort):
    """Deterministic mock WAN tunnel adapter for tests and local development."""

    def __init__(self, default_url: str = "https://mock-remote.riftpilot.app") -> None:
        self._default_url = default_url
        self._status = TunnelStatus.DISCONNECTED
        self._public_url = ""
        self._error = ""

    async def start_tunnel(self, local_port: int) -> TunnelInfo:
        if local_port <= 0 or local_port > 65535:
            self._status = TunnelStatus.FAILED
            self._error = f"Invalid port: {local_port}"
            return await self.get_tunnel_info()

        self._status = TunnelStatus.CONNECTED
        self._public_url = f"{self._default_url}:{local_port}" if "mock" in self._default_url else self._default_url
        self._error = ""
        logger.info("Mock WAN tunnel started at %s for local port %d", self._public_url, local_port)
        return await self.get_tunnel_info()

    async def stop_tunnel(self) -> bool:
        self._status = TunnelStatus.STOPPED
        self._public_url = ""
        self._error = ""
        logger.info("Mock WAN tunnel stopped.")
        return True

    async def get_tunnel_info(self) -> TunnelInfo:
        return TunnelInfo(
            provider="MockRelay",
            status=self._status,
            public_url=self._public_url,
            is_active=(self._status == TunnelStatus.CONNECTED),
            error_message=self._error,
            latency_ms=18.5 if self._status == TunnelStatus.CONNECTED else 0.0,
        )

    def simulate_failure(self, error: str = "Relay server timeout") -> None:
        self._status = TunnelStatus.FAILED
        self._error = error
        self._public_url = ""


class CloudflareTunnelAdapter(ITunnelPort):
    """Outbound adapter orchestrating Cloudflare Quick Tunnels via `cloudflared`."""

    URL_REGEX = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    def __init__(self, binary_path: str = "cloudflared") -> None:
        self._binary_path = binary_path
        self._process: asyncio.subprocess.Process | None = None
        self._status = TunnelStatus.DISCONNECTED
        self._public_url = ""
        self._error = ""
        self._listen_task: asyncio.Task[None] | None = None

    async def start_tunnel(self, local_port: int) -> TunnelInfo:
        """Start cloudflared subprocess to expose local port over trycloudflare.com."""
        if self._status == TunnelStatus.CONNECTED and self._process is not None:
            return await self.get_tunnel_info()

        executable = shutil.which(self._binary_path) or self._binary_path
        if not shutil.which(executable):
            self._status = TunnelStatus.FAILED
            self._error = "cloudflared binary not found in system PATH. Install cloudflared to enable WAN tunnels."
            logger.warning("Cloudflare tunnel failed: %s", self._error)
            return await self.get_tunnel_info()

        self._status = TunnelStatus.STARTING
        self._error = ""
        self._public_url = ""

        try:
            cmd = [
                executable,
                "tunnel",
                "--url",
                f"http://127.0.0.1:{local_port}",
                "--no-autoupdate",
            ]
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            self._status = TunnelStatus.FAILED
            self._error = f"Failed to spawn cloudflared process: {exc}"
            logger.error(self._error)
            return await self.get_tunnel_info()

        # Parse stderr/stdout for the assigned trycloudflare URL
        url_found = asyncio.Event()

        async def _read_output(stream: asyncio.StreamReader | None) -> None:
            if not stream:
                return
            while not stream.at_eof():
                try:
                    line = await stream.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="ignore")
                    match = self.URL_REGEX.search(text)
                    if match and not self._public_url:
                        self._public_url = match.group(0)
                        self._status = TunnelStatus.CONNECTED
                        url_found.set()
                except Exception:
                    break

        self._listen_task = asyncio.create_task(_read_output(self._process.stderr))

        try:
            await asyncio.wait_for(url_found.wait(), timeout=12.0)
        except asyncio.TimeoutError:
            if not self._public_url:
                self._status = TunnelStatus.FAILED
                self._error = "Timed out waiting for Cloudflare trycloudflare URL assigned"
                await self.stop_tunnel()

        return await self.get_tunnel_info()

    async def stop_tunnel(self) -> bool:
        """Terminate the cloudflared background subprocess."""
        if self._process is not None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=3.0)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        if self._listen_task is not None:
            self._listen_task.cancel()
            self._listen_task = None

        self._status = TunnelStatus.STOPPED
        self._public_url = ""
        logger.info("Cloudflare tunnel terminated cleanly.")
        return True

    async def get_tunnel_info(self) -> TunnelInfo:
        return TunnelInfo(
            provider="Cloudflare",
            status=self._status,
            public_url=self._public_url,
            is_active=(self._status == TunnelStatus.CONNECTED and bool(self._public_url)),
            error_message=self._error,
        )


__all__ = [
    "MockTunnelAdapter",
    "CloudflareTunnelAdapter",
]
