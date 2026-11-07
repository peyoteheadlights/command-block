"""Build Chunker's command-line jar from source, at the pinned version.

    python scripts/build_chunker.py            (needs Git and Java 17 or newer)

Chunker (github.com/HiveGamesOSS/Chunker, by Hive Games, MIT License)
changes worlds between Bedrock and Java. The app ships its command-line jar
as tools/chunker-cli.jar (packaging/app.spec), built here from the tag and
commit pinned in agent/servertypes/chunker.py, so nothing is downloaded when
it runs. scripts/build_installer.py runs this first. The jar ends up in
build/tools/ (git-ignored); a second run reuses it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.servertypes.chunker import COMMIT, VERSION  # noqa: E402

REPO = "https://github.com/HiveGamesOSS/Chunker.git"
SOURCE = ROOT / "build" / "chunker-src"
OUT = ROOT / "build" / "tools" / "chunker-cli.jar"


def main() -> Path:
    if OUT.is_file():
        print(f"Chunker {VERSION} is already built: {OUT}")
        return OUT
    shutil.rmtree(SOURCE, ignore_errors=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", VERSION, REPO, str(SOURCE)], check=True
    )
    head = subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if head != COMMIT:
        raise SystemExit(f"Chunker's tag {VERSION} is at {head}, not the pinned {COMMIT}.")
    gradle = SOURCE / ("gradlew.bat" if os.name == "nt" else "gradlew")
    subprocess.run(
        [str(gradle), ":cli:shadowJar", "-x", "test", "--no-daemon"], cwd=SOURCE, check=True
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE / "cli" / "build" / "libs" / f"chunker-cli-{VERSION}.jar", OUT)
    print(f"Built Chunker {VERSION}: {OUT} ({OUT.stat().st_size // 1024} KB)")
    return OUT


if __name__ == "__main__":
    main()
