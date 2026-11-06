"""Live test of the separate Windows user (ticket "boot test"). Runs only on
Windows with MCSC_LIVE_WINDOWS_TESTS=1 and a CI task name, as Administrator
(the GitHub Windows runner). It makes a throwaway account with the
installer's real code, starts programs as it the way the agent does, and
then asks the question the design depends on: does it still work from a
Task Scheduler task with S4U (how the agent runs at boot with nobody signed
in)? If not, the answer printed here decides whether the small SYSTEM
starter service from ADR 0007 is needed. The account is always removed."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt"
    or os.environ.get("MCSC_LIVE_WINDOWS_TESTS") != "1"
    or not os.environ.get("MCSC_TASK_NAME", "").startswith("MCSC CI"),
    reason="live Windows test (CI only)",
)

PROJECT = Path(__file__).resolve().parents[2]
ACCOUNT = "CBLiveTest"
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"


@pytest.fixture
def account():
    from installer import serveruser as setup
    from installer.system import System

    system = System()
    assert system.is_admin() is True, "the GitHub Windows runner is expected to be elevated"
    password = setup.new_password()
    system.make_server_user(ACCOUNT, password)
    try:
        yield password
    finally:
        system.remove_server_user(ACCOUNT)
    from agent import serveruser

    assert not serveruser.account_exists(ACCOUNT)


def test_the_account_is_made_hidden_and_programs_start_as_it(account):
    import winreg

    import psutil

    from agent import serveruser
    from agent.winproc import NO_WINDOW

    assert serveruser.account_exists(ACCOUNT)
    key = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as opened:
        assert winreg.QueryValueEx(opened, ACCOUNT)[0] == 0

    async def run():
        who = await serveruser.launch_as(
            ACCOUNT, account, [str(SYSTEM32 / "whoami.exe")], SYSTEM32,
            {"stdout": asyncio.subprocess.PIPE, "creationflags": NO_WINDOW},
        )  # fmt: skip
        name = (await who.communicate())[0].decode(errors="replace").strip().lower()
        # The console pipe both ways, and the agent watching it by its id.
        echo = await serveruser.launch_as(
            ACCOUNT, account, [str(SYSTEM32 / "findstr.exe"), "^"], SYSTEM32,
            {"stdin": asyncio.subprocess.PIPE, "stdout": asyncio.subprocess.PIPE,
             "creationflags": NO_WINDOW},
        )  # fmt: skip
        memory = psutil.Process(echo.pid).memory_info().rss
        said = (await echo.communicate(b"stop\r\n"))[0].decode(errors="replace").strip()
        return name, memory, said

    name, memory, said = asyncio.run(run())
    assert name.endswith("\\" + ACCOUNT.lower())
    assert memory > 0
    assert said == "stop"

    async def wrong():
        await serveruser.launch_as(
            ACCOUNT, "not-the-password", [str(SYSTEM32 / "whoami.exe")], SYSTEM32, {}
        )

    with pytest.raises(serveruser.ServerUserMissing):
        asyncio.run(wrong())


def test_a_boot_task_can_read_the_password_and_start_as_the_account(account):
    from agent import serveruser
    from installer import autostart

    work = Path(tempfile.mkdtemp(prefix="cb-live-"))
    secret, out = work / "server-user.bin", work / "answer.json"
    serveruser.write_password(secret, account)
    name = os.environ["MCSC_TASK_NAME"] + " server user"
    probe = PROJECT / "tests" / "windows" / "server_user_probe.py"
    xml = autostart.build_task_xml(
        "boot",
        autostart.current_user(),
        sys.executable,
        f'"{probe}" {ACCOUNT} "{secret}" "{out}"',
        str(PROJECT),
    )
    xml_path = work / "task.xml"
    xml_path.write_text(xml, encoding="utf-16")
    try:
        made = subprocess.run(
            ["schtasks.exe", "/Create", "/TN", name, "/XML", str(xml_path), "/F"],
            capture_output=True, text=True,
        )  # fmt: skip
        assert made.returncode == 0, made.stdout + made.stderr
        subprocess.run(["schtasks.exe", "/Run", "/TN", name], check=True, capture_output=True)
        deadline = time.monotonic() + 120
        while not out.exists() and time.monotonic() < deadline:
            time.sleep(1)
        assert out.exists(), "the S4U task never answered"
        answer = json.loads(out.read_text(encoding="utf-8"))
        print("BOOT TEST ANSWER:", answer)  # the answer for the part's notes
        assert answer.get("dpapi") == "works", answer
        assert str(answer.get("run_as", "")).lower().endswith(ACCOUNT.lower()), answer
    finally:
        subprocess.run(["schtasks.exe", "/Delete", "/TN", name, "/F"], capture_output=True)
