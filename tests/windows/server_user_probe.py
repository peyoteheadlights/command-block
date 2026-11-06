"""Run by the live test's Task Scheduler task (S4U, like the agent at boot):
can it read the DPAPI-saved password and start a program as the separate
Windows user? Writes the answer as JSON. Arguments: account, password file,
answer file."""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import serveruser
from agent.winproc import NO_WINDOW


async def probe(account: str, secret: Path) -> dict:
    answer: dict = {}
    try:
        password = serveruser.read_password(secret)
        answer["dpapi"] = "works"
    except serveruser.ServerUserMissing as exc:
        answer["dpapi"] = f"refused: {exc.detail}"
        return answer
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    try:
        process = await serveruser.launch_as(
            account,
            password,
            [str(system32 / "whoami.exe")],
            system32,
            {"stdout": asyncio.subprocess.PIPE, "creationflags": NO_WINDOW},
        )
        out, _ = await process.communicate()
        answer["run_as"] = "works: " + out.decode(errors="replace").strip()
    except (OSError, serveruser.ServerUserMissing) as exc:
        answer["run_as"] = f"refused: {exc!r} winerror={getattr(exc, 'winerror', None)}"
    return answer


def main() -> None:
    account, secret, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    try:
        answer = asyncio.run(probe(account, secret))
    except Exception as exc:  # the test wants any answer, never silence
        answer = {"crashed": repr(exc)}
    out.write_text(json.dumps(answer), encoding="utf-8")


if __name__ == "__main__":
    main()
