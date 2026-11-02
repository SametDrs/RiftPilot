"""Pytest configuration ensuring worktree src takes precedence over site-packages."""
import sys
from pathlib import Path

worktree_root = Path(__file__).resolve().parent.parent
worktree_src = worktree_root / "src"
src_str = str(worktree_src)

if src_str not in sys.path or sys.path[0] != src_str:
    sys.path.insert(0, src_str)


def authorize_client(client, ott_manager, device_name: str = "Test Device") -> str:
    """Pair ``client`` through ``POST /api/pair`` and attach the session as default auth.

    All ``/api/*`` routes except pairing require ``Authorization: Bearer <token>``
    (ADR-0004 Zero-Trust). Returns the issued session token.
    """
    ott = ott_manager.generate_ott()
    resp = client.post("/api/pair", json={"ott": ott.token, "deviceName": device_name})
    assert resp.status_code == 200, resp.text
    token: str = resp.json()["sessionToken"]
    client.headers["Authorization"] = f"Bearer {token}"
    return token
