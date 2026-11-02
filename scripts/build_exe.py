"""RiftPilot — Automated Windows Executable Builder (PyInstaller / Nuitka).

Automates packaging of RiftPilot into a standalone Windows executable.
Usage:
    python scripts/build_exe.py [--onedir | --onefile]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


def build_executable(onefile: bool = False) -> int:
    """Build the RiftPilot Windows executable."""
    print("=" * 60)
    print("  RiftPilot — Standalone Desktop Executable Packaging")
    print("=" * 60)

    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_dir = os.path.join(root_dir, "src")
    dist_dir = os.path.join(root_dir, "dist")
    build_dir = os.path.join(root_dir, "build")

    # Verify PyInstaller is installed
    try:
        import PyInstaller  # type: ignore[import-not-found]
        print(f"✓ PyInstaller version {PyInstaller.__version__} detected.")
    except ImportError:
        print("PyInstaller not found. Installing via pip...")
        res = subprocess.call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        if res != 0:
            print("ERROR: Failed to install PyInstaller.")
            return 1

    entry_point = os.path.join(src_dir, "riftpilot", "desktop_app.py")
    if not os.path.exists(entry_point):
        # Fallback to module execution entry
        entry_point = os.path.join(src_dir, "riftpilot", "__main__.py")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=RiftPilot",
        "--windowed",
        "--noconfirm",
        "--clean",
        f"--paths={src_dir}",
        f"--add-data={os.path.join(src_dir, 'riftpilot', 'adapters', 'static')}{os.pathsep}riftpilot/adapters/static",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols",
        "--hidden-import=uvicorn.protocols.http",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.websockets",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.lifespan",
        "--hidden-import=uvicorn.lifespan.on",
        "--hidden-import=starlette.responses",
        "--hidden-import=pydantic",
        "--hidden-import=websockets",
    ]

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    cmd.append(entry_point)

    print(f"Running build command: {' '.join(cmd)}")
    result = subprocess.call(cmd, cwd=root_dir)

    if result == 0:
        print("\n" + "=" * 60)
        print("✓ BUILD SUCCESSFUL!")
        print(f"Output directory: {dist_dir}")
        print("=" * 60)
    else:
        print("\n❌ Build failed with exit code:", result)

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build RiftPilot desktop binary")
    parser.add_argument("--onefile", action="store_true", help="Build single .exe file")
    args = parser.parse_args()
    sys.exit(build_executable(onefile=args.onefile))
