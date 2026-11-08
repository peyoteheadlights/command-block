"""Put Diagnose server's test player in build/tools/diagnose, for the installer.

    python scripts/build_bot.py

Downloads Node.js for Windows (only node.exe is kept) and the packages pinned
in bot/package-lock.json, each checked against its pinned checksum, and
unpacks them (agent/joinbot.py prepare). packaging/app.spec ships the folder
as tools/diagnose. scripts/build_installer.py runs this first; a second run
reuses the folder.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import joinbot  # noqa: E402


def main() -> Path:
    folder = asyncio.run(joinbot.prepare(joinbot.BUILT))
    print(f"The test player is ready: {folder}")
    return folder


if __name__ == "__main__":
    main()
