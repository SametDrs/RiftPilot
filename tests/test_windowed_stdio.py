"""Windowed (console=False) PyInstaller builds start with sys.stdout/stderr = None."""

from __future__ import annotations

import sys

import pytest
import uvicorn

from riftpilot.desktop_app import _ensure_stdio


def test_ensure_stdio_replaces_none_streams(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    _ensure_stdio()

    assert sys.stdout is not None and sys.stderr is not None
    assert (tmp_path / "RiftPilot" / "riftpilot.log").exists()
    # uvicorn's log formatter calls isatty() on the stream; this used to crash.
    uvicorn.Config(app=lambda *a: None, log_level="warning")
    sys.stdout.close()


def test_ensure_stdio_keeps_existing_streams() -> None:
    out, err = sys.stdout, sys.stderr
    _ensure_stdio()
    assert sys.stdout is out and sys.stderr is err
