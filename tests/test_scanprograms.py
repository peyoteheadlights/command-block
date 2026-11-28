"""The deep scan's separate programs: MCAntiMalware, jNeedle and ClamAV.

The programs are never run here. ``run_program`` is replaced with a fake
that answers with lines like the real programs print, and GitHub with a
small fake download, so nothing here goes on the internet.
"""

# ruff: noqa: F811  (pytest fixtures imported from test_deepscan)
from __future__ import annotations

import dataclasses
import hashlib
import io
import sys
import time
import zipfile
from pathlib import Path

import httpx
import pytest

from agent import downloads
from agent.mods import deepscan, scanprograms
from agent.mods.scanprograms import (
    CLAMAV,
    JNEEDLE,
    MCANTIMALWARE,
    ProgramError,
    ScanPrograms,
    read_clamav,
    read_jneedle,
    read_mcantimalware,
)

from .test_deepscan import BASE, CLASS, bad_mod, ctx, scan, signed_in  # noqa: F401
from .test_jarscan import mod_with


@pytest.fixture
def portal(signed_in):
    return signed_in.portal


# ------------------------------------------------------------ reading output
def test_mcantimalware_lines_are_read_with_its_own_names():
    hits = read_mcantimalware(
        [
            "[INFO]: Scanning C:/server/mods",
            "[DETECTED]: C:/server/mods/bad.jar MIGHT be infected with Spigot.MALWARE.Skyrage.A",
            "[DETECTED]: C:/server/mods/odd.jar MIGHT be infected with Spigot.INFO.Obfuscated.A",
            "[DETECTED]: C:/server/mods/pup.jar MIGHT be infected with "
            "Spigot.PotentiallyUnwantedFile.X.A",
        ]
    )
    assert [(path, f["values"]["name"], bad) for path, f, bad in hits] == [
        ("C:/server/mods/bad.jar", "Spigot.MALWARE.Skyrage.A", True),
        ("C:/server/mods/odd.jar", "Spigot.INFO.Obfuscated.A", False),
        ("C:/server/mods/pup.jar", "Spigot.PotentiallyUnwantedFile.X.A", False),
    ]
    assert hits[0][1]["scanner"] == "MCAntiMalware"


def test_jneedle_lines_are_read_for_a_folder_and_for_one_file():
    folder = read_jneedle(
        [
            "Loading database...",
            "/srv/mods/bad.jar matches [SEVERE(fractureiser, rule=stage0), WARNING(odd)]",
            "/srv/mods/ok.jar matches []",
        ],
        None,
    )
    assert [(p, f["values"]["name"], bad) for p, f, bad in folder] == [
        ("/srv/mods/bad.jar", "fractureiser", True),
        ("/srv/mods/bad.jar", "odd", False),
    ]
    one = read_jneedle(["[INFORMATION(packed)]"], "/srv/mods/x.jar")
    assert [(p, bad) for p, _, bad in one] == [("/srv/mods/x.jar", False)]
    assert read_jneedle(["[]"], "/srv/mods/x.jar") == []


def test_clamav_lines_are_read_and_every_hit_is_known_bad():
    hits = read_clamav(
        ["C:\\srv\\mods\\bad.jar: Java.Trojan.Agent-123 FOUND", "C:\\srv\\ok.jar: OK"]
    )
    assert [(p, f["values"]["name"], bad) for p, f, bad in hits] == [
        ("C:\\srv\\mods\\bad.jar", "Java.Trojan.Agent-123", True)
    ]


# ------------------------------------------------------------ download
def release(monkeypatch, served: bytes, key: str = MCANTIMALWARE, sha256: str | None = None):
    """Serve ``served`` as the program's release file, pinned to ``sha256``
    (the served bytes' own checksum by default)."""
    program = scanprograms.PROGRAMS[key]
    pinned = dataclasses.replace(
        program, sha256=sha256 or hashlib.sha256(served).hexdigest(), size=len(served)
    )
    monkeypatch.setitem(scanprograms.PROGRAMS, key, pinned)
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, content=served)

    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(answer))
    return asked


def test_a_program_is_downloaded_from_its_release_and_checked(ctx, monkeypatch, portal):
    asked = release(monkeypatch, b"PK jar bytes")
    programs = ScanPrograms(ctx.deepscan.forge_dir)
    portal.call(programs.install, MCANTIMALWARE)
    assert asked == [scanprograms.PROGRAMS[MCANTIMALWARE].url]
    assert programs.installed(MCANTIMALWARE)


def test_a_download_with_the_wrong_checksum_is_refused(ctx, monkeypatch, portal):
    release(monkeypatch, b"not the pinned file", sha256="0" * 64)
    programs = ScanPrograms(ctx.deepscan.forge_dir)
    with pytest.raises(ProgramError) as caught:
        portal.call(programs.install, MCANTIMALWARE)
    assert caught.value.words == "deepscan.error.program_download"
    assert not programs.installed(MCANTIMALWARE)


def test_each_program_is_pinned_to_a_github_release_with_a_checksum():
    for program in scanprograms.PROGRAMS.values():
        assert program.url.startswith(f"https://github.com/{program.repo}/releases/download/")
        assert len(program.sha256) == 64 and program.size > 0


def clamav_zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, body in entries.items():
            zf.writestr(name, body)
    return out.getvalue()


def test_clamav_is_unpacked_and_its_zip_removed(ctx, monkeypatch, portal):
    release(
        monkeypatch,
        clamav_zip({"clamav-1.5.5/clamscan.exe": b"MZ", "clamav-1.5.5/freshclam.exe": b"MZ"}),
        key=CLAMAV,
    )
    programs = ScanPrograms(ctx.deepscan.forge_dir)
    portal.call(programs.install, CLAMAV)
    assert programs.installed(CLAMAV)
    assert not (programs.home(CLAMAV) / "clamav.zip").exists()


def test_a_clamav_zip_that_climbs_out_of_its_folder_is_refused(ctx, monkeypatch, portal):
    release(monkeypatch, clamav_zip({"../../evil.exe": b"MZ", "clamscan.exe": b"MZ"}), key=CLAMAV)
    programs = ScanPrograms(ctx.deepscan.forge_dir)
    with pytest.raises(ProgramError):
        portal.call(programs.install, CLAMAV)
    assert not programs.installed(CLAMAV)
    assert not (ctx.deepscan.forge_dir.parent / "evil.exe").exists()


# ------------------------------------------------------------ running
@pytest.fixture(autouse=True)
def windows(monkeypatch):
    """ClamAV is offered on Windows only, and these tests act as Windows."""
    monkeypatch.setattr(scanprograms, "WINDOWS", True)


def set_up(ctx, *keys: str) -> ScanPrograms:
    """Put the programs' files where a download would."""
    programs = ScanPrograms(ctx.deepscan.forge_dir)
    for key in keys:
        home = programs.home(key)
        home.mkdir(parents=True, exist_ok=True)
        if key == CLAMAV:
            (home / "bin").mkdir(exist_ok=True)
            for exe in ("clamscan.exe", "freshclam.exe"):
                (home / "bin" / exe).write_bytes(b"MZ")
            (home / "installed.json").write_text('{"bin": "bin"}')
            (home / "db").mkdir(exist_ok=True)
            (home / "db" / "main.cvd").write_bytes(b"x")
            (home / "signatures-at").write_text(str(time.time()))
        else:
            (home / scanprograms.PROGRAMS[key].file).write_bytes(b"PK")
    return programs


def fake_runs(monkeypatch, answer):
    """Replace the program runs. ``answer(argv)`` gives (exit code, lines)."""
    runs: list[list[str]] = []

    async def run(argv, cwd, timeout):
        runs.append(list(argv))
        return answer(argv)

    monkeypatch.setattr(scanprograms, "run_program", run)
    return runs


def no_downloads(monkeypatch):
    def answer(request):
        raise AssertionError(f"nothing is downloaded here: {request.url}")

    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(answer))


def test_known_bad_from_mcantimalware_goes_to_quarantine_with_its_name(signed_in, ctx, monkeypatch):
    set_up(ctx, MCANTIMALWARE)
    bad = mod_with(ctx.config.mods_dir / "bad.jar", {"a/B.class": CLASS})
    mod_with(ctx.config.mods_dir / "ok.jar", {"a/B.class": CLASS})
    runs = fake_runs(
        monkeypatch,
        lambda argv: (0, [f"[DETECTED]: {bad} MIGHT be infected with Spigot.MALWARE.Test.A"]),
    )
    events = []
    ctx.bus.subscribe(events.append)

    report = scan(signed_in, scope="mods", scanners=["mcantimalware"])
    item = report["last"]["found"][0]
    assert (item["path"], item["kind"], item["state"]) == (
        "mods/bad.jar",
        "known_bad",
        "quarantined",
    )
    assert item["findings"][0]["scanner"] == "MCAntiMalware"
    assert item["findings"][0]["values"]["name"] == "Spigot.MALWARE.Test.A"
    assert not bad.exists()
    assert report["last"]["scanners"] == ["mcantimalware"]
    found = next(e for e in events if e.type == "jar_scan_found")
    assert "MCAntiMalware found something" in found.message
    # One fixed argument list, and the flags that keep it from updating itself.
    argv = runs[0]
    assert argv[1:3] == ["-jar", str(set_up(ctx).home(MCANTIMALWARE) / "MCAntiMalware.jar")]
    assert argv[3:5] == ["--scanDirectory", str(Path(ctx.config.mods_dir).resolve())]
    assert argv[5:] == ["--singleScan", "true", "--disableAutoUpdate", "true"]


def test_a_worth_a_look_hit_joins_the_behaviour_finding_on_the_same_jar(
    signed_in, ctx, monkeypatch
):
    set_up(ctx, JNEEDLE)
    bad = bad_mod(ctx)
    fake_runs(monkeypatch, lambda argv: (0, [f"{bad} matches [WARNING(odd-thing)]"]))
    monkeypatch.setattr(ScanPrograms, "check_jneedle_list", lambda self: _nothing())
    last = scan(signed_in, scope="mods", scanners=["behaviour", "jneedle"])["last"]
    assert len(last["found"]) == 1
    item = last["found"][0]
    assert item["kind"] == "worth_a_look" and bad.is_file()
    assert [f["scanner"] for f in item["findings"]] == ["Command Block behaviour check", "jNeedle"]


def test_a_known_bad_hit_on_a_flagged_jar_comes_first_and_is_named(signed_in, ctx, monkeypatch):
    set_up(ctx, MCANTIMALWARE)
    bad = bad_mod(ctx)
    fake_runs(
        monkeypatch,
        lambda argv: (0, [f"[DETECTED]: {bad} MIGHT be infected with Spigot.MALWARE.Test.A"]),
    )
    events = []
    ctx.bus.subscribe(events.append)
    last = scan(signed_in, scope="mods", scanners=["behaviour", "mcantimalware"])["last"]
    item = last["found"][0]
    assert item["kind"] == "known_bad" and item["state"] == "quarantined"
    assert [f["scanner"] for f in item["findings"]] == [
        "MCAntiMalware",
        "Command Block behaviour check",
    ]
    found = next(e for e in events if e.type == "jar_scan_found")
    assert "MCAntiMalware found something" in found.message
    assert "It was moved to quarantine" in found.message  # the server is stopped


async def _nothing() -> None:
    return None


def test_a_program_is_downloaded_on_the_first_clicked_run(signed_in, ctx, monkeypatch):
    asked = release(monkeypatch, b"PK jar bytes")
    fake_runs(monkeypatch, lambda argv: (0, []))
    report = scan(signed_in, scope="mods", scanners=["mcantimalware"])
    assert asked and report["last"]["errors"] == []
    row = next(s for s in report["scanners"] if s["id"] == "mcantimalware")
    assert row["installed"] is True and row["size"] is None
    assert row["took"]["seconds"] >= 0


def test_a_program_not_yet_downloaded_says_its_size(signed_in, ctx):
    report = signed_in.get(f"{BASE}/mods/deepscan").json()
    row = next(s for s in report["scanners"] if s["id"] == "clamav")
    assert row["installed"] is False
    assert row["size"] == scanprograms.PROGRAMS[CLAMAV].size
    assert row["speed"] == "deepscan.speed.slowest"


def test_scheduled_and_before_start_runs_use_only_programs_already_set_up(
    signed_in, ctx, monkeypatch, portal
):
    set_up(ctx, MCANTIMALWARE)
    no_downloads(monkeypatch)
    runs = fake_runs(monkeypatch, lambda argv: (0, []))
    assert ctx.deepscan.installed() == [deepscan.BEHAVIOUR, MCANTIMALWARE]
    portal.call(ctx.deepscan.run_now, "mods", "schedule")
    last = ctx.deepscan.store()["last"]
    assert last["scanners"] == [deepscan.BEHAVIOUR, MCANTIMALWARE]
    assert len(runs) == 1 and last["errors"] == []


def test_without_a_download_a_missing_program_is_said_not_fetched(ctx, monkeypatch, portal):
    no_downloads(monkeypatch)
    programs = ScanPrograms(ctx.deepscan.forge_dir)
    with pytest.raises(ProgramError) as caught:
        portal.call(programs.scan, CLAMAV, "java", [Path(ctx.config.mods_dir)], [], False)
    assert caught.value.words == "deepscan.error.not_set_up"


def test_jneedle_is_hidden_for_a_day_when_its_list_cant_be_downloaded(signed_in, ctx, monkeypatch):
    set_up(ctx, JNEEDLE)

    def answer(request):
        assert request.url.host == "maven.kosmx.dev"
        return httpx.Response(503)

    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(answer))
    runs = fake_runs(monkeypatch, lambda argv: (0, []))
    mod_with(ctx.config.mods_dir / "x.jar", {"a/B.class": CLASS})
    last = scan(signed_in, scope="mods", scanners=["behaviour", "jneedle"])["last"]
    assert [e["words"] for e in last["errors"]] == ["deepscan.error.jneedle_list"]
    assert runs == [] and last["scanners"] == ["behaviour"]

    report = signed_in.get(f"{BASE}/mods/deepscan").json()
    assert next(s for s in report["scanners"] if s["id"] == "jneedle")["available"] is False
    assert JNEEDLE not in ctx.deepscan.installed()
    refused = signed_in.post(
        f"{BASE}/mods/deepscan", json={"scope": "mods", "scanners": ["jneedle"]}
    )
    assert refused.status_code == 400

    failed = ScanPrograms(ctx.deepscan.forge_dir).home(JNEEDLE) / "list-failed"
    failed.write_text(str(time.time() - scanprograms.DAY - 1))
    assert JNEEDLE in ctx.deepscan.installed()  # back after a day


def test_jneedle_without_its_list_is_hidden_too(ctx, monkeypatch, portal):
    programs = set_up(ctx, JNEEDLE)
    fake_runs(monkeypatch, lambda argv: (1, ["Failed to fetch database: timeout"]))
    with pytest.raises(ProgramError) as caught:
        portal.call(programs.scan, JNEEDLE, "java", [Path(ctx.config.mods_dir)], [], False)
    assert caught.value.words == "deepscan.error.jneedle_list"
    assert not programs.available(JNEEDLE)


def test_a_program_that_fails_says_its_last_line(signed_in, ctx, monkeypatch):
    set_up(ctx, MCANTIMALWARE)
    fake_runs(
        monkeypatch,
        lambda argv: (1, ["Error: java.lang.UnsupportedClassVersionError: needs 61.0", ""]),
    )
    last = scan(signed_in, scope="mods", scanners=["behaviour", "mcantimalware"])["last"]
    assert last["errors"] == [
        {
            "words": "deepscan.error.program_failed",
            "values": {
                "name": "MCAntiMalware",
                "why": "Error: java.lang.UnsupportedClassVersionError: needs 61.0",
            },
        }
    ]
    assert last["scanners"] == ["behaviour"]


def test_clamav_skips_world_regions_and_our_folders(signed_in, ctx, monkeypatch):
    set_up(ctx, CLAMAV)
    server = Path(ctx.config.server_dir)
    region = server / "world" / "region"
    region.mkdir(parents=True)
    (region / "r.0.0.mca").write_bytes(b"x")
    bad = server / "evil.dat"
    bad.write_bytes(b"x")
    ctx.config.backup_dir.mkdir(parents=True, exist_ok=True)
    (ctx.config.backup_dir / "old.zip").write_bytes(b"x")
    runs = fake_runs(
        monkeypatch,
        lambda argv: (
            1,
            [
                f"{region / 'r.0.0.mca'}: Region.Test FOUND",
                f"{bad}: Win.Test.EICAR FOUND",
                f"{ctx.config.backup_dir / 'old.zip'}: Win.Test.EICAR FOUND",
            ],
        ),
    )
    last = scan(signed_in, scope="folder", scanners=["clamav"])["last"]
    assert [(f["path"], f["kind"]) for f in last["found"]] == [("evil.dat", "known_bad")]
    assert [e["words"] for e in last["errors"]] == ["deepscan.error.no_java"]
    argv = runs[0]
    assert r"--exclude=\.(mca|mcr|mcc)$" in argv
    assert any(a.startswith("--exclude-dir=^") for a in argv)
    assert argv[-1] == str(server.resolve())


def test_a_hit_on_a_file_the_scan_doesnt_list_is_said(signed_in, ctx, monkeypatch, tmp_path):
    set_up(ctx, CLAMAV)
    elsewhere = tmp_path / "elsewhere.jar"
    fake_runs(monkeypatch, lambda argv: (1, [f"{elsewhere}: Java.Test FOUND"]))
    mod_with(ctx.config.mods_dir / "x.jar", {"a/B.class": CLASS})
    last = scan(signed_in, scope="mods", scanners=["clamav"])["last"]
    assert last["found"] == []
    assert [e["words"] for e in last["errors"]] == ["deepscan.error.program_path"]


def test_clamav_uses_its_old_list_when_the_update_fails(ctx, monkeypatch, portal):
    programs = set_up(ctx, CLAMAV)
    (programs.home(CLAMAV) / "signatures-at").write_text("0")  # a day old
    runs = fake_runs(
        monkeypatch,
        lambda argv: (1, ["ERROR: Can't connect"]) if "freshclam" in argv[0] else (0, []),
    )
    hits, said = portal.call(programs.scan, CLAMAV, "java", [Path(ctx.config.mods_dir)], [], False)
    assert hits == [] and [e["words"] for e in said] == ["deepscan.error.clamav_old"]
    assert len(runs) == 2


def test_clamav_without_any_list_doesnt_run(ctx, monkeypatch, portal):
    programs = set_up(ctx, CLAMAV)
    (programs.home(CLAMAV) / "db" / "main.cvd").unlink()
    runs = fake_runs(monkeypatch, lambda argv: (1, ["ERROR: Can't connect"]))
    with pytest.raises(ProgramError) as caught:
        portal.call(programs.scan, CLAMAV, "java", [Path(ctx.config.mods_dir)], [], False)
    assert caught.value.words == "deepscan.error.clamav_none"
    assert len(runs) == 1  # freshclam only


def test_a_program_that_runs_too_long_is_stopped(tmp_path, portal):
    """A real, harmless program: this Python, waiting."""
    argv = [sys.executable, "-c", "print('started', flush=True); import time; time.sleep(60)"]
    begun = time.monotonic()
    with pytest.raises(ProgramError) as caught:
        portal.call(scanprograms.run_program, argv, tmp_path, 1)
    assert caught.value.words == "deepscan.error.program_timeout"
    assert time.monotonic() - begun < 30


def test_a_program_run_gives_its_code_and_lines(tmp_path, portal):
    argv = [sys.executable, "-c", "print('a'); print('b'); raise SystemExit(3)"]
    assert portal.call(scanprograms.run_program, argv, tmp_path, 30) == (3, ["a", "b"])


def test_clamav_is_only_offered_on_windows(signed_in, ctx, monkeypatch):
    """ClamAV comes as a Windows build only."""
    monkeypatch.setattr(scanprograms, "WINDOWS", False)
    report = signed_in.get(f"{BASE}/mods/deepscan").json()
    row = next(s for s in report["scanners"] if s["id"] == "clamav")
    assert row["available"] is False
    reply = signed_in.post(f"{BASE}/mods/deepscan", json={"scope": "mods", "scanners": ["clamav"]})
    assert reply.status_code == 400


def test_before_a_start_each_program_gets_a_short_time_limit(signed_in, ctx, monkeypatch, portal):
    """So a start isn't held for hours by a slow program."""
    set_up(ctx, MCANTIMALWARE, CLAMAV)
    (ctx.deepscan.programs.home(CLAMAV) / "signatures-at").write_text("0")
    no_downloads(monkeypatch)
    limits: list[float] = []

    async def run(argv, cwd, timeout):
        limits.append(timeout)
        return 0, []

    monkeypatch.setattr(scanprograms, "run_program", run)
    signed_in.post(f"{BASE}/mods/deepscan/options", json={"before_start": True})
    portal.call(ctx.jarscan.before_start)
    assert len(limits) == 3  # freshclam, ClamAV, MCAntiMalware
    assert all(limit <= scanprograms.BEFORE_START for limit in limits)
    limits.clear()
    portal.call(ctx.deepscan.run_now, "mods", "schedule")
    assert max(limits) == scanprograms.TIMEOUT
