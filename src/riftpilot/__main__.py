"""RiftPilot — Module execution entry point (python -m riftpilot)."""

from __future__ import annotations

import os
import sys

# Ensure src directory is on sys.path
_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from riftpilot.desktop_app import main

if __name__ == "__main__":
    sys.exit(main())
