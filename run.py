#!/usr/bin/env python3
"""RiftPilot — Quickstart Launcher.

Runs the RiftPilot Desktop Companion application and background Mobile Web Hub.

Usage:
    python run.py               # Standard run (Live LoL Client)
    python run.py --mock        # Simulated LoL Client (development/testing)
    python run.py --headless    # Web Hub server only (no desktop GUI)
    python run.py --port 8080   # Custom HTTP port
    python run.py --wan         # Auto-enable Cloudflare WAN remote tunnel
"""

from __future__ import annotations

import os
import sys

# Prepend 'src' directory to sys.path so 'riftpilot' is always importable
_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(_ROOT_DIR, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from riftpilot.desktop_app import main

if __name__ == "__main__":
    sys.exit(main())
