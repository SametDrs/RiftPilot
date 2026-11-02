"""Tests for Desktop Companion Entry Point & CLI (desktop_app.py).

Verifies:
- LAN IP resolution logic
- CLI argument parsing
- Headless and mock server launch/shutdown
- Clean module import via riftpilot.desktop_app and riftpilot.__main__
"""

from __future__ import annotations

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import re
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from riftpilot.desktop_app import get_lan_ip, main, run_desktop_app


class TestDesktopApp:
    """Unit tests for RiftPilot desktop application lifecycle."""

    def test_get_lan_ip_returns_valid_ipv4(self) -> None:
        """Verify LAN IP detection yields a valid IPv4 formatted address."""
        ip = get_lan_ip()
        assert isinstance(ip, str)
        ipv4_pattern = r"^(\d{1,3}\.){3}\d{1,3}$"
        assert re.match(ipv4_pattern, ip), f"Invalid IP format: {ip}"

    def test_main_cli_arguments_parsing(self) -> None:
        """Verify command line flags are parsed and forwarded to run_desktop_app."""
        with patch("riftpilot.desktop_app.run_desktop_app") as mock_run:
            mock_run.return_value = 0
            ret = main(["--port", "8888", "--mock", "--headless", "--wan", "--debug"])
            assert ret == 0
            mock_run.assert_called_once_with(
                host="0.0.0.0",
                port=8888,
                use_mock_lcu=True,
                headless=True,
                enable_tunnel=True,
                debug=True,
            )

    def test_run_desktop_app_headless_mock_lifecycle(self) -> None:
        """Verify headless mode starts Uvicorn server in worker thread and exits cleanly."""
        # Patch time.sleep to simulate early shutdown after one iteration
        call_count = 0

        def fake_sleep(duration: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                # Trigger keyboard interrupt to simulate user exit
                raise KeyboardInterrupt()

        with patch("riftpilot.desktop_app.time.sleep", side_effect=fake_sleep):
            exit_code = run_desktop_app(
                host="127.0.0.1",
                port=9521,
                use_mock_lcu=True,
                headless=True,
                debug=False,
            )
            assert exit_code == 0

    def test_main_module_importable(self) -> None:
        """Verify riftpilot.__main__ is importable without errors."""
        import riftpilot.__main__ as main_module
        assert hasattr(main_module, "main")
