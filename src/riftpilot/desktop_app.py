"""RiftPilot — Modern Desktop Companion Application & Orchestration Entry Point.

Orchestrates the PySide6 desktop GUI and the asynchronous FastAPI Web Hub:
- Detects the local LAN IP address for mobile QR pairing.
- Runs the Uvicorn ASGI server in a dedicated background worker thread.
- Connects to the live League Client (LCU) via RiotLcuAdapter (or MockLcuAdapter).
- Displays the LeagueCompanion-styled Hextech desktop GUI (MainWindow).
- Minimizes to system tray and provides native Windows notifications.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys
import threading
import time
from typing import Sequence

# Ensure 'src' is in sys.path when running as a script or module
_PROJECT_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_SRC not in sys.path:
    sys.path.insert(0, _PROJECT_SRC)

import uvicorn

from riftpilot.adapters.mock_lcu import MockLcuAdapter
from riftpilot.adapters.riot_lcu import RiotLcuAdapter
from riftpilot.adapters.tunnel_adapter import CloudflareTunnelAdapter
from riftpilot.adapters.web_hub import create_app
from riftpilot.core.lcu_port import ILcuPort
from riftpilot.core.pairing import OTTManager, SessionManager

logger = logging.getLogger("RiftPilot.Desktop")


def get_lan_ip() -> str:
    """Detect local LAN IP address for QR code pairing and mobile access.

    Returns the outgoing network interface IP, falling back to 127.0.0.1.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to public DNS address without sending actual packets
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def _ensure_stdio() -> None:
    """Give windowed (console=False) builds real stdout/stderr streams.

    PyInstaller's windowed bootloader leaves ``sys.stdout``/``sys.stderr`` as ``None``,
    which crashes uvicorn's log formatter (``isatty()`` on ``None``). Redirect both to a
    log file under ``%LOCALAPPDATA%\\RiftPilot`` (falling back to ``os.devnull``).
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    stream = None
    try:
        log_dir = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "RiftPilot")
        os.makedirs(log_dir, exist_ok=True)
        stream = open(os.path.join(log_dir, "riftpilot.log"), "a", encoding="utf-8", buffering=1)
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def run_desktop_app(
    host: str = "0.0.0.0",
    port: int = 8000,
    use_mock_lcu: bool = False,
    headless: bool = False,
    enable_tunnel: bool = False,
    debug: bool = False,
) -> int:
    """Initialize and run RiftPilot desktop application and background Web Hub server.

    Args:
        host: Host IP to bind Uvicorn (default '0.0.0.0' for LAN access).
        port: Port to bind Uvicorn (default 8000).
        use_mock_lcu: Use MockLcuAdapter instead of live RiotLcuAdapter.
        headless: Run Web Hub server only without PySide6 GUI.
        enable_tunnel: Auto-enable WAN tunnel on startup.
        debug: Enable verbose debug logging.

    Returns:
        Exit code (0 for success, non-zero for error).
    """
    _ensure_stdio()

    # Configure logging
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    lan_ip = get_lan_ip()
    logger.info("Initializing RiftPilot Desktop Companion...")
    logger.info("LAN IP detected: %s (Web Hub port: %d)", lan_ip, port)

    # 1. Initialize Domain Managers
    ott_manager = OTTManager()
    session_manager = SessionManager()

    # 2. Initialize LCU Port Adapter
    lcu_adapter: ILcuPort
    if use_mock_lcu:
        logger.info("Starting with MockLcuAdapter (Simulated LCU mode)...")
        lcu_adapter = MockLcuAdapter()
    else:
        logger.info("Starting with RiotLcuAdapter (Live League Client mode)...")
        lcu_adapter = RiotLcuAdapter()

    # 3. Initialize WAN Tunnel Port (Milestone 6)
    tunnel_adapter = CloudflareTunnelAdapter()

    # 4. Construct FastAPI Application
    app = create_app(
        ott_manager=ott_manager,
        session_manager=session_manager,
        lcu_adapter=lcu_adapter,
        tunnel_adapter=tunnel_adapter,
        server_port=port,
    )

    # 5. Start Uvicorn Server in Background Thread
    server_config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="debug" if debug else "warning",
        access_log=debug,
    )
    server = uvicorn.Server(server_config)

    server_thread = threading.Thread(
        target=server.run,
        daemon=True,
        name="RiftPilot-WebHub-Thread",
    )
    server_thread.start()
    logger.info("FastAPI Web Hub running in background thread at http://%s:%d", lan_ip, port)

    tunnel_loop: asyncio.AbstractEventLoop | None = None
    if enable_tunnel:
        # Long-lived loop in a daemon thread so cloudflared reader tasks outlive the start call.
        tunnel_loop = asyncio.new_event_loop()
        threading.Thread(target=tunnel_loop.run_forever, daemon=True, name="RiftPilot-Tunnel-Loop").start()
        asyncio.run_coroutine_threadsafe(tunnel_adapter.start_tunnel(local_port=port), tunnel_loop)

    def _stop_tunnel() -> None:
        if tunnel_loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(tunnel_adapter.stop_tunnel(), tunnel_loop).result(timeout=8.0)
        except Exception:
            pass
        tunnel_loop.call_soon_threadsafe(tunnel_loop.stop)

    # 6. Run GUI or Headless Event Loop
    if headless:
        logger.info("Running in headless mode (no GUI). Press Ctrl+C to terminate.")
        try:
            while server_thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            logger.info("Termination signal received. Shutting down...")
        finally:
            _stop_tunnel()
            server.should_exit = True
            server_thread.join(timeout=3.0)
        return 0

    # 7. Start PySide6 Desktop GUI
    try:
        from PySide6.QtWidgets import QApplication
        from riftpilot.ui.main_window import MainWindow

        qapp = QApplication.instance()
        if qapp is None:
            qapp = QApplication(sys.argv)
        qapp.setApplicationName("RiftPilot")
        qapp.setOrganizationName("RiftPilot")

        window = MainWindow(
            ott_manager=ott_manager,
            session_manager=session_manager,
            lan_ip=lan_ip,
            port=port,
            lcu_adapter=lcu_adapter,
        )
        app.state.ui_bridge = window.bridge

        if hasattr(window, "_init_system_tray"):
            window._init_system_tray()

        window.show()

        def _cleanup() -> None:
            logger.info("Qt application exiting. Stopping Web Hub server...")
            _stop_tunnel()
            server.should_exit = True
            if hasattr(lcu_adapter, "close") and callable(getattr(lcu_adapter, "close")):
                try:
                    asyncio.run(lcu_adapter.close())
                except Exception:
                    pass

        qapp.aboutToQuit.connect(_cleanup)
        exit_code = qapp.exec()

        server.should_exit = True
        server_thread.join(timeout=2.0)
        return int(exit_code)

    except Exception as exc:
        logger.error("Failed to start PySide6 desktop GUI: %s", exc, exc_info=True)
        _stop_tunnel()
        server.should_exit = True
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for RiftPilot Desktop Companion."""
    parser = argparse.ArgumentParser(
        prog="riftpilot",
        description="RiftPilot — Hextech League of Legends Companion & Remote Orchestration System",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host address to bind the Mobile Web Hub server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind the Mobile Web Hub server (default: 8000)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run with MockLcuAdapter for development/testing without League of Legends open",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Web Hub server in headless mode without desktop PySide6 GUI",
    )
    parser.add_argument(
        "--wan",
        action="store_true",
        help="Auto-start Cloudflare WAN tunnel for remote internet access",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging",
    )

    args = parser.parse_args(argv)

    return run_desktop_app(
        host=args.host,
        port=args.port,
        use_mock_lcu=args.mock,
        headless=args.headless,
        enable_tunnel=args.wan,
        debug=args.debug,
    )


if __name__ == "__main__":
    sys.exit(main())
