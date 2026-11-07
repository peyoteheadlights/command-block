"""Run by the live test's Task Scheduler task (S4U, like the agent at boot
with nobody signed in): can the agent start a program as the separate
Windows user through the starter service? Writes the answer as JSON.
Arguments: the service's pipe, the answer file."""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import serveruser
from agent.winproc import NO_WINDOW


async def probe() -> dict:
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    process = await serveruser.launch_as(
        [str(system32 / "whoami.exe")],
        system32,
        {"stdout": asyncio.subprocess.PIPE, "stderr": asyncio.subprocess.PIPE,
         "creationflags": NO_WINDOW},
    )  # fmt: skip
    out, err = await process.communicate()
    return {
        "run_as": out.decode(errors="replace").strip(),
        # If it printed nothing, the exit code says why.
        "exit_code": hex((process.returncode or 0) & 0xFFFFFFFF),
        "stderr": err.decode(errors="replace").strip(),
    }


def main() -> None:
    serveruser.PIPE, out = sys.argv[1], Path(sys.argv[2])
    try:
        answer = asyncio.run(probe())
    except Exception as exc:  # the test wants any answer, never silence
        answer = {"crashed": repr(exc), "winerror": getattr(exc, "winerror", None)}
    out.write_text(json.dumps(answer), encoding="utf-8")


if __name__ == "__main__":
    main()
