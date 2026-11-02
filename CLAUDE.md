# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

RiftPilot is a Windows desktop companion (PySide6) that runs an embedded FastAPI "Web Hub" so a phone (paired by QR) can remote-control the League of Legends client (LCU). README.md is written in Turkish.

## Commands

```powershell
pip install -e ".[dev]"            # requires Python >= 3.14 (pyproject), PySide6, FastAPI, etc.
python run.py                      # desktop GUI + web hub against the live LoL client
python run.py --mock               # MockLcuAdapter instead of a real client (dev/testing)
python run.py --headless           # web hub only, no GUI
python run.py --port 8080 --wan    # custom port / Cloudflare tunnel
python -m pytest                   # whole suite (no LoL client needed)
python -m pytest tests/test_pairing.py::test_name -v   # single test
python scripts/build_exe.py        # PyInstaller build (uses riftpilot.spec)
```

Tests run with `asyncio_mode = "auto"`; CI sets `QT_QPA_PLATFORM=offscreen` for the Qt tests, so set it too when running UI tests headless. `tests/conftest.py` forces `src/` to the front of `sys.path`.

CI runs Python 3.14 only, plus a non-blocking `ruff check src tests`.

## Architecture

Hexagonal (ports & adapters), see `docs/adr/0003-*`. Business logic is transport-agnostic.

- `src/riftpilot/core/` — pure domain models and contracts (champ select, game mode detection, lobby/ARAM bench, post-game/honor, runes & spells, queue timer, `MessageEnvelope`/tunnel port in `transport.py`, OTT + session management in `pairing.py`). The outbound port is `ILcuPort` in `lcu_port.py`; core must not import from adapters.
- `src/riftpilot/adapters/`
  - `riot_lcu.py` (live HTTPX client, self-signed SSL), `lcu_discovery.py` (lockfile/process args), `lcu_stream.py` (LCU WebSocket events), `mock_lcu.py` (deterministic simulator implementing `ILcuPort`, used by almost all tests and by `--mock`).
  - `web_hub.py` — the mobile-facing FastAPI/WebSocket server via `create_app(...)`. All `/api/*` routes live on an `APIRouter` guarded by the `require_session` dependency (Bearer / `X-Session-Token`); only `/` and `/api/pair` are public. Tests get an authorized client via `authorize_client()` in `tests/conftest.py`.
  - `static/index.html` — the entire mobile web UI (HTML/CSS/JS in one file), loaded into `web_hub.INDEX_HTML` at import (tests assert on its ids/strings). All REST calls must go through the JS `apiFetch()` helper. Shipped via `package-data` and `riftpilot.spec` `datas`.
  - `tunnel_adapter.py` — `CloudflareTunnelAdapter` / `MockTunnelAdapter` for WAN access.
- `src/riftpilot/desktop_app.py` — entry point: detects LAN IP, runs Uvicorn in a background thread, wires the LCU adapter (live or mock), launches `ui/main_window.py` (Hextech-styled window, system tray, notifications; `qr_helper.py` renders pairing QR).
- `run.py` just prepends `src/` to `sys.path` and calls `desktop_app.main`.

Game-mode awareness is central: `game_mode.py` classifies the queue (ranked draft, blind, ARAM, Brawl, unsupported) and drives which champ-select layout/actions/spells the hub allows; the server validates and rejects invalid actions (wrong turn, banned champ, mode-invalid spells), not just the UI.

## Project rules

- Security: pairing token (OTT) is single-use and burned on read; every mobile command needs a valid `SessionToken`; the desktop must be able to revoke devices. Only the official LCU REST/WebSocket API is used — never read/write game memory (Riot TOS).
- Type hints required everywhere; never block the Qt main thread (network/LCU work goes in asyncio/`QThread`); all tests must pass without a real LoL client (use the mock).
- Git: GitHub Flow with Conventional Commits (`feat(scope): ...`), work on `feat/*`, `fix/*`, etc. branches rather than committing to `main`.
- Never commit `.env`, session tokens, or user data.

## Misc

`scripts/kmyai_key_manager.py` (untracked) is an unrelated Playwright automation script for an external site, not part of the app.
