"""CloudflareTunnelAdapter tests with a fake subprocess (no real cloudflared)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from riftpilot.adapters import tunnel_adapter as ta
from riftpilot.adapters.tunnel_adapter import CloudflareTunnelAdapter
from riftpilot.core.transport import TunnelStatus


class FakeProc:
    def __init__(self, lines: list[bytes], hang: bool = False) -> None:
        self.stderr = asyncio.StreamReader()
        for ln in lines:
            self.stderr.feed_data(ln)
        if not hang:
            self.stderr.feed_eof()
        self.returncode: int | None = None
        self.ignore_terminate = False
        self.terminate = MagicMock(side_effect=self._term)
        self.kill = MagicMock(side_effect=self._kill)

    def _term(self) -> None:
        if not self.ignore_terminate:
            self.returncode = 0

    def _kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0.01)
        return self.returncode


@pytest.fixture
def patch_spawn(monkeypatch: pytest.MonkeyPatch):
    def _apply(proc: FakeProc) -> None:
        async def fake_exec(*a, **k):
            return proc

        monkeypatch.setattr(ta.shutil, "which", lambda x: x)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    return _apply


async def test_url_parsed_and_connected(patch_spawn) -> None:
    proc = FakeProc([b"INF boot\n", b"INF |  https://abc-def.trycloudflare.com  |\n"], hang=True)
    patch_spawn(proc)
    adapter = CloudflareTunnelAdapter()
    info = await adapter.start_tunnel(8000)
    assert info.status == TunnelStatus.CONNECTED
    assert info.public_url == "https://abc-def.trycloudflare.com"
    assert await adapter.stop_tunnel() is True
    proc.terminate.assert_called_once()
    assert (await adapter.get_tunnel_info()).status == TunnelStatus.STOPPED


async def test_timeout_preserves_failed(patch_spawn, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProc([b"nothing useful\n"], hang=True)
    patch_spawn(proc)
    monkeypatch.setattr(CloudflareTunnelAdapter, "START_TIMEOUT", 0.1)
    adapter = CloudflareTunnelAdapter()
    info = await adapter.start_tunnel(8000)
    assert info.status == TunnelStatus.FAILED
    assert "Timed out" in info.error_message
    assert not info.is_active
    proc.terminate.assert_called_once()


async def test_stop_kills_when_terminate_ignored(patch_spawn, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProc([b"https://x1.trycloudflare.com\n"], hang=True)
    proc.ignore_terminate = True
    patch_spawn(proc)
    adapter = CloudflareTunnelAdapter()
    await adapter.start_tunnel(8000)
    real_wait_for = asyncio.wait_for

    async def fast_wait_for(aw, timeout):
        return await real_wait_for(aw, min(timeout, 0.1))

    monkeypatch.setattr(ta.asyncio, "wait_for", fast_wait_for)
    await adapter.stop_tunnel()
    proc.kill.assert_called_once()


async def test_missing_binary_failed() -> None:
    info = await CloudflareTunnelAdapter(binary_path="no_such_bin_xyz").start_tunnel(8000)
    assert info.status == TunnelStatus.FAILED
