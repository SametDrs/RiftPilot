"""Pytest configuration ensuring worktree src takes precedence over site-packages."""
import sys
from pathlib import Path

worktree_root = Path(__file__).resolve().parent.parent
worktree_src = worktree_root / "src"
src_str = str(worktree_src)

if src_str not in sys.path or sys.path[0] != src_str:
    sys.path.insert(0, src_str)
