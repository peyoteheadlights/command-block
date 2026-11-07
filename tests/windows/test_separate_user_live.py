"""Live test of the separate Windows user and its starter service. Runs
only on Windows with MCSC_LIVE_WINDOWS_TESTS=1 and a CI task name, as
Administrator (the GitHub Windows runner). It makes the account and a copy
of the starter service with the installer's real code, starts programs
through it the way the agent does, and then asks the question the design
depends on: does it still work from a Task Scheduler task with S4U (how the
agent runs at boot with nobody signed in)? The service and the account are
always removed."""

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
SERVICE = "CBLiveTestStarter"  # never the real service's name
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"


@pytest.fixture
def service(monkeypatch):
    """The real account (this is a throwaway CI machine) and a test copy of
    the service, run from this checkout."""
    from agent import serveruser, starter
    from installer import serveruser as setup
    from installer.system import System

    system = System()
    assert system.is_admin() is True, "the GitHub Windows runner is expected to be elevated"
    password = setup.new_password()
    settings = Path(tempfile.mkdtemp(prefix="cb-starter-")) / "starter" / "starter.json"
    entry = PROJECT / "tests" / "windows" / "starter_entry.py"
    command = f'"{sys.executable}" "{entry}" --name {SERVICE} --settings "{settings}"'
    owner = system.current_user_sid()
    system.make_server_user(starter.ACCOUNT, password)
    monkeypatch.setattr(serveruser, "PIPE", starter.pipe_name(SERVICE))
    try:
        system.install_service(SERVICE, command, settings, password, owner)

        def wrong_password():
            system.install_service(SERVICE, command, settings, "not-the-password", owner)

        yield wrong_password
    finally:
        system.remove_starter(SERVICE, settings)
        system.remove_server_user(starter.ACCOUNT)
    assert not serveruser.account_exists(starter.ACCOUNT)
    assert not settings.parent.exists()


def test_programs_start_as_the_account_through_the_starter_service(service):
    import psutil

    from agent import serveruser
    from agent.winproc import NO_WINDOW

    assert serveruser.ask({"do": "ping"}) == {"ok": True}

    async def run():
        who = await serveruser.launch_as(
            [str(SYSTEM32 / "whoami.exe")], SYSTEM32,
            {"stdout": asyncio.subprocess.PIPE, "creationflags": NO_WINDOW},
        )  # fmt: skip
        name = (await who.communicate())[0].decode(errors="replace").strip().lower()
        # The console pipe both ways, and the agent watching it by its id.
        echo = await serveruser.launch_as(
            [str(SYSTEM32 / "findstr.exe"), "^"], SYSTEM32,
            {"stdin": asyncio.subprocess.PIPE, "stdout": asyncio.subprocess.PIPE,
             "creationflags": NO_WINDOW},
        )  # fmt: skip
        memory = psutil.Process(echo.pid).memory_info().rss
        said = (await echo.communicate(b"stop\r\n"))[0].decode(errors="replace").strip()
        # A harmful mod runs as the account: it must not be able to use the
        # service itself.
        knock = await serveruser.launch_as(
            [sys.executable, "-I", "-c", KNOCK, serveruser.PIPE], SYSTEM32,
            {"stdout": asyncio.subprocess.PIPE, "stderr": asyncio.subprocess.STDOUT,
             "creationflags": NO_WINDOW},
        )  # fmt: skip
        knocked = (await knock.communicate())[0].decode(errors="replace").strip()
        return name, memory, said, knocked

    name, memory, said, knocked = asyncio.run(run())
    assert name.endswith("\\commandblockserver")
    assert memory > 0
    assert said == "stop"
    assert knocked == "5", knocked  # ERROR_ACCESS_DENIED


# Run as the account: try to open the service's pipe, print Windows' answer.
KNOCK = """
import _winapi, sys
try:
    _winapi.CreateFile(sys.argv[1], 0x120103, 0, 0, 3, 0, 0)
    print("opened")
except OSError as exc:
    print(exc.winerror)
"""


def test_a_boot_task_starts_programs_as_the_account(service):
    from agent import serveruser
    from installer import autostart

    work = Path(tempfile.mkdtemp(prefix="cb-live-"))
    out = work / "answer.json"
    name = os.environ["MCSC_TASK_NAME"] + " server user"
    probe = PROJECT / "tests" / "windows" / "server_user_probe.py"
    xml = autostart.build_task_xml(
        "boot",
        autostart.current_user(),
        sys.executable,
        f'"{probe}" "{serveruser.PIPE}" "{out}"',
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
        print("BOOT TEST ANSWER:", answer)
        assert str(answer.get("run_as", "")).lower().endswith("\\commandblockserver"), answer
    finally:
        subprocess.run(["schtasks.exe", "/Delete", "/TN", name, "/F"], capture_output=True)


def test_a_wrong_password_reads_as_missing(service):
    from agent import serveruser

    service()  # the service restarts with a password that doesn't sign in
    with pytest.raises(serveruser.ServerUserMissing) as raised:
        serveruser.ask({"do": "ping"})
    assert raised.value.detail == "Windows error 1326"
