"""The jar check: hashes, the log4j-core check, quarantine with Undo, the
background rescans, the Protection line and the API.

Modrinth and CurseForge are replaced with small fakes, so nothing here
goes on the internet.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import threading
import zipfile
from pathlib import Path

import pytest

from agent import notes
from agent.mods import jarscan
from agent.mods.jarscan import check_jar, find_log4j, log4j_is_old
from agent.mods.sources import SourceError
from agent.protection import Facts, lines
from agent.security import antivirus

from .test_antivirus import MARK, FakeAmsi
from .test_api import auth, token_for
from .test_mods import make_jar

BASE = "/api/servers/test"
STRINGS = json.loads(
    (Path(__file__).parents[1] / "shared" / "strings.json").read_text(encoding="utf-8")
)


def jar_bytes(files: dict[str, bytes | str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()


def log4j_jar(version: str | None) -> bytes:
    files: dict[str, bytes | str] = {jarscan.JNDI: b"\xca\xfe\xba\xbe"}
    if version:
        files[jarscan.POM] = f"groupId=org.apache.logging.log4j\nversion={version}\n"
    return jar_bytes(files)


def mod_with(path: Path, inner: dict[str, bytes]) -> Path:
    files: dict[str, bytes | str] = {
        "fabric.mod.json": json.dumps({"schemaVersion": 1, "id": "big", "version": "1"}),
        **inner,
    }
    path.write_bytes(jar_bytes(files))
    return path


# ------------------------------------------------------------ log4j
@pytest.mark.parametrize(
    "version, old",
    [
        ("2.14.1", True),
        ("2.16.0", True),
        ("2.17.0", False),
        ("2.17.1", False),
        ("2.20.0", False),
        ("2.12.2", True),
        ("2.12.4", False),
        ("2.3", True),
        ("2.3.2", False),
        ("2.0-beta9", True),
        ("1.2.17", False),
        ("3.0.0", False),
        ("not a version", None),
    ],
)
def test_log4j_versions(version, old):
    assert log4j_is_old(version) is old


def test_log4j_is_found_in_a_jar_inside_a_jar_inside_a_jar():
    deepest = log4j_jar("2.14.1")
    middle = jar_bytes({"META-INF/jars/log4j-core-2.14.1.jar": deepest})
    outer = jar_bytes({"META-INF/jars/lib.jar": middle})
    assert find_log4j(outer) == [
        {"where": "META-INF/jars/log4j-core-2.14.1.jar", "version": "2.14.1"}
    ]


def test_a_nested_log4j_without_a_pom_takes_its_version_from_its_file_name():
    outer = jar_bytes({"META-INF/jars/log4j-core-2.15.0.jar": log4j_jar(None)})
    assert find_log4j(outer)[0]["version"] == "2.15.0"


def test_check_jar_finds_an_old_log4j_and_only_notes_one_with_no_version(tmp_path):
    old = mod_with(tmp_path / "old.jar", {"META-INF/jars/a.jar": log4j_jar("2.14.1")})
    unknown = mod_with(tmp_path / "unknown.jar", {"META-INF/jars/b.jar": log4j_jar(None)})
    new = mod_with(tmp_path / "new.jar", {"META-INF/jars/c.jar": log4j_jar("2.17.1")})

    found = check_jar(old)["findings"]
    assert [f["scanner"] for f in found] == [jarscan.LOG4J]
    assert found[0]["values"] == {"version": "2.14.1", "where": "META-INF/jars/a.jar"}
    assert check_jar(unknown)["findings"] == []
    assert check_jar(unknown)["notes"][0]["words"] == "jarscan.note.log4j_no_version"
    assert check_jar(new)["findings"] == []


def test_check_jar_finds_a_file_on_the_known_bad_list(tmp_path, monkeypatch):
    path = make_jar(tmp_path / "evil.jar", "evil")
    sha512 = hashlib.sha512(path.read_bytes()).hexdigest()
    monkeypatch.setitem(notes.BAD_FILES, sha512, {"name": "Evil 1.0", "why": "steals logins"})
    found = check_jar(path)["findings"]
    assert found[0]["scanner"] == jarscan.NOTES
    assert found[0]["values"] == {"name": "Evil 1.0", "why": "steals logins"}


# ------------------------------------------------------------ the scanner
@pytest.fixture
def signed_in(client):
    client.headers.update(auth(token_for(client)))
    return client


@pytest.fixture
def ctx(signed_in):
    return signed_in.app.state.core.servers["test"]


class Sites:
    """Fake Modrinth and CurseForge that know the jars added to them."""

    def __init__(self, ctx, monkeypatch):
        self.modrinth: set[str] = set()
        self.curseforge: dict[int, str] = {}
        self.down = False
        self.asked = 0

        async def by_hash(sha512):
            self.asked += 1
            if self.down:
                raise SourceError("Modrinth is down")
            return {"id": "x"} if sha512 in self.modrinth else None

        async def by_fingerprint(prints):
            if self.down:
                raise SourceError("CurseForge is down")
            return {
                p: {"file": {"sha1": self.curseforge[p]}} for p in prints if p in self.curseforge
            }

        monkeypatch.setattr(ctx.mods.modrinth, "by_hash", by_hash)
        monkeypatch.setattr(ctx.mods.curseforge, "by_fingerprint", by_fingerprint)
        monkeypatch.setattr(type(ctx.mods.curseforge), "loaders", property(lambda _: ["fabric"]))

    def know(self, path: Path, modrinth=True, curseforge=True):
        checked = check_jar(path)
        if modrinth:
            self.modrinth.add(checked["sha512"])
        if curseforge:
            self.curseforge[checked["fingerprint"]] = checked["sha1"]


@pytest.fixture
def sites(ctx, monkeypatch):
    return Sites(ctx, monkeypatch)


def bad_jar(ctx, name="bad.jar") -> Path:
    return mod_with(ctx.config.mods_dir / name, {"META-INF/jars/l.jar": log4j_jar("2.14.1")})


def row(report, filename):
    return next(r for r in report["jars"] if r["filename"] == filename)


def test_rows_say_where_each_jar_was_found(signed_in, ctx, sites):
    both = make_jar(ctx.config.mods_dir / "both.jar", "both")
    only = make_jar(ctx.config.mods_dir / "only.jar", "only")
    make_jar(ctx.config.mods_dir / "nowhere.jar", "nowhere")
    sites.know(both)
    sites.know(only, curseforge=False)

    report = signed_in.post(f"{BASE}/mods/scan").json()
    assert row(report, "both.jar")["words"] == "jarscan.row.known"
    assert row(report, "both.jar")["values"] == {"sites": "Modrinth and CurseForge"}
    assert row(report, "only.jar")["values"] == {"sites": "Modrinth"}
    assert row(report, "nowhere.jar")["words"] == "jarscan.row.unknown"
    assert row(report, "nowhere.jar")["values"] == {"sites": "Modrinth or CurseForge"}
    assert report["summary"] | {"checked_at": None} == {
        "checked": 3,
        "jars": 3,
        "found": 0,
        "recent": 0,
        "unknown": 1,
        "couldnt": 0,
        "changed": 0,
        "checked_at": None,
    }


def test_a_curseforge_fingerprint_with_another_sha1_is_not_a_match(signed_in, ctx, sites):
    path = make_jar(ctx.config.mods_dir / "twin.jar", "twin")
    sites.curseforge[check_jar(path)["fingerprint"]] = "0" * 40
    report = signed_in.post(f"{BASE}/mods/scan").json()
    assert row(report, "twin.jar")["sites"]["curseforge"] == "not_found"


def test_a_site_that_is_down_is_said_and_asked_again_later(signed_in, ctx, sites):
    make_jar(ctx.config.mods_dir / "a.jar", "a")
    sites.down = True
    report = signed_in.post(f"{BASE}/mods/scan").json()
    assert row(report, "a.jar")["words"] == "jarscan.row.couldnt"
    assert report["summary"]["couldnt"] == 1

    sites.down = False
    signed_in.portal.call(ctx.jarscan.tick)  # not an hour yet
    assert row(ctx.jarscan.report(), "a.jar")["words"] == "jarscan.row.couldnt"
    saved = ctx.jarscan.store()
    saved["jars"]["a.jar"]["looked_at"] -= jarscan.RETRY_AFTER + 1
    ctx.jarscan.save(saved)
    signed_in.portal.call(ctx.jarscan.tick)
    assert row(ctx.jarscan.report(), "a.jar")["words"] == "jarscan.row.unknown"


def test_a_broken_jar_inside_a_mod_is_noted_and_the_check_goes_on(signed_in, ctx, sites):
    inner = log4j_jar("2.14.1")
    path = mod_with(ctx.config.mods_dir / "odd.jar", {"META-INF/jars/inner.jar": inner})
    data = bytearray(path.read_bytes())
    at = data.find(inner) + len(inner) // 2
    data[at] ^= 0xFF  # the inner jar's CRC no longer matches
    path.write_bytes(bytes(data))
    make_jar(ctx.config.mods_dir / "a.jar", "a")

    report = signed_in.post(f"{BASE}/mods/scan").json()
    odd = row(report, "odd.jar")
    assert odd["notes"][0]["words"] == "jarscan.note.unreadable"
    assert row(report, "a.jar")["sites"]
    signed_in.portal.call(ctx.jarscan.before_start)  # the start isn't stopped by it


def test_a_jar_added_to_the_bad_file_list_later_is_found_on_the_next_check(
    signed_in, ctx, sites, monkeypatch
):
    path = make_jar(ctx.config.mods_dir / "a.jar", "a")
    signed_in.post(f"{BASE}/mods/scan")
    sha512 = hashlib.sha512(path.read_bytes()).hexdigest()
    monkeypatch.setitem(notes.BAD_FILES, sha512, {"name": "A", "why": "bad"})
    report = signed_in.post(f"{BASE}/mods/scan").json()
    assert not path.exists()
    assert report["quarantine"][0]["findings"][0]["scanner"] == jarscan.NOTES


def test_quarantine_never_writes_over_a_jar_there(signed_in, ctx, sites, monkeypatch):
    monkeypatch.setattr(jarscan.time, "strftime", lambda _: "20261127-120000")
    taken = ctx.config.mod_quarantine_dir / "20261127-120000-bad.jar"
    taken.parent.mkdir(parents=True, exist_ok=True)
    taken.write_bytes(b"older")
    bad_jar(ctx)
    report = signed_in.post(f"{BASE}/mods/scan").json()
    assert taken.read_bytes() == b"older"
    assert (ctx.config.mod_quarantine_dir / report["quarantine"][0]["id"]).is_file()


def test_a_start_doesnt_wait_for_a_lookup(signed_in, ctx, sites, monkeypatch):
    make_jar(ctx.config.mods_dir / "a.jar", "a")
    asking = threading.Event()
    gate: dict[str, asyncio.Event] = {}

    async def slow(sha512):
        gate["open"] = asyncio.Event()
        asking.set()
        await gate["open"].wait()
        return None

    monkeypatch.setattr(ctx.mods.modrinth, "by_hash", slow)
    scan = signed_in.portal.start_task_soon(ctx.jarscan.scan)
    assert asking.wait(5)
    signed_in.portal.call(lambda: asyncio.wait_for(ctx.jarscan.before_start(), 5))
    signed_in.portal.call(gate["open"].set)
    assert scan.result(5)["jars"]


def test_an_unchanged_jar_is_not_read_again(signed_in, ctx, sites, monkeypatch):
    make_jar(ctx.config.mods_dir / "a.jar", "a")
    signed_in.post(f"{BASE}/mods/scan")
    reads = []
    monkeypatch.setattr(jarscan, "check_jar", lambda p: reads.append(p) or check_jar(p))
    signed_in.post(f"{BASE}/mods/scan")
    assert reads == []


def test_a_jar_with_a_finding_goes_to_quarantine_and_comes_back_with_undo(signed_in, ctx, sites):
    path = bad_jar(ctx)
    events = []
    ctx.bus.subscribe(events.append)

    report = signed_in.post(f"{BASE}/mods/scan").json()
    assert not path.exists()
    assert report["summary"]["found"] == 1
    item = report["quarantine"][0]
    assert item["filename"] == "bad.jar"
    assert (ctx.config.mod_quarantine_dir / item["id"]).is_file()  # moved, never deleted
    assert item["findings"][0]["scanner"] == jarscan.LOG4J
    assert [e.type for e in events if e.type.startswith("jar_scan")] == ["jar_scan_found"]
    assert ctx.mods.history()[0]["action"] == "quarantine"

    undone = signed_in.post(f"{BASE}/mods/scan/undo", json={"id": item["id"]})
    assert undone.status_code == 200, undone.text
    assert undone.json()["restored"] == "bad.jar"
    assert path.is_file()

    again = signed_in.post(f"{BASE}/mods/scan").json()
    assert path.is_file()  # put back by the owner: not moved again
    assert row(again, "bad.jar")["words"] == "jarscan.row.kept"
    assert again["summary"]["found"] == 0


def test_undo_refuses_an_id_that_is_not_in_quarantine(signed_in, ctx, sites):
    response = signed_in.post(f"{BASE}/mods/scan/undo", json={"id": "../../config.yaml"})
    assert response.status_code == 400


def test_undo_does_not_overwrite_a_jar_with_the_same_name(signed_in, ctx, sites):
    bad_jar(ctx)
    item = signed_in.post(f"{BASE}/mods/scan").json()["quarantine"][0]
    make_jar(ctx.config.mods_dir / "bad.jar", "other")
    response = signed_in.post(f"{BASE}/mods/scan/undo", json={"id": item["id"]})
    assert response.status_code == 400
    assert (ctx.config.mod_quarantine_dir / item["id"]).is_file()


def test_while_the_server_runs_a_finding_waits_and_moves_before_the_next_start(
    signed_in, ctx, sites, monkeypatch
):
    path = bad_jar(ctx)
    monkeypatch.setattr(ctx.jarscan, "_busy", lambda: True)
    report = signed_in.post(f"{BASE}/mods/scan").json()
    assert path.is_file()
    assert row(report, "bad.jar")["words"] == "jarscan.row.waiting"

    signed_in.portal.call(ctx.jarscan.before_start)
    assert not path.exists()
    assert ctx.jarscan.report()["quarantine"][0]["filename"] == "bad.jar"


def test_before_start_checks_a_jar_added_since_the_last_check(signed_in, ctx, sites):
    signed_in.post(f"{BASE}/mods/scan")
    path = bad_jar(ctx, "new.jar")
    signed_in.portal.call(ctx.jarscan.before_start)
    assert not path.exists()


def test_a_jar_that_cannot_be_moved_stops_the_start(signed_in, ctx, sites, monkeypatch):
    from agent.minecraft.process import ServerError

    bad_jar(ctx)
    monkeypatch.setattr(ctx.jarscan, "_busy", lambda: True)
    signed_in.post(f"{BASE}/mods/scan")

    def refuse(*_):
        raise PermissionError("in use")

    monkeypatch.setattr(jarscan.shutil, "move", refuse)
    with pytest.raises(ServerError):
        signed_in.portal.call(ctx.jarscan.before_start)


def test_tick_rescans_on_change_and_everything_weekly(signed_in, ctx, sites):
    signed_in.post(f"{BASE}/mods/scan")
    path = make_jar(ctx.config.mods_dir / "late.jar", "late")
    signed_in.portal.call(ctx.jarscan.tick)
    assert row(ctx.jarscan.report(), "late.jar")["sites"]

    asked = sites.asked
    signed_in.portal.call(ctx.jarscan.tick)  # nothing changed, not a week yet
    assert sites.asked == asked

    saved = ctx.jarscan.store()
    saved["looked_up_at"] -= jarscan.WEEK + 1
    ctx.jarscan.save(saved)
    signed_in.portal.call(ctx.jarscan.tick)
    assert sites.asked == asked + 1
    assert path.is_file()


def test_the_rescans_can_be_turned_off(signed_in, ctx, sites):
    response = signed_in.post(
        f"{BASE}/mods/scan/options", json={"weekly": False, "on_change": False}
    )
    assert response.status_code == 200
    assert response.json()["weekly"] is False and response.json()["on_change"] is False
    make_jar(ctx.config.mods_dir / "a.jar", "a")
    signed_in.portal.call(ctx.jarscan.tick)
    assert sites.asked == 0


def test_get_scan_reports_without_checking(signed_in, ctx, sites):
    make_jar(ctx.config.mods_dir / "a.jar", "a")
    report = signed_in.get(f"{BASE}/mods/scan").json()
    assert report["checked_at"] is None and report["jars"] == []
    assert sites.asked == 0


# ------------------------------------------------------------ Windows antivirus
@pytest.fixture
def amsi(monkeypatch):
    fake = FakeAmsi()
    monkeypatch.setattr(antivirus, "_engine", fake)
    monkeypatch.setattr(antivirus, "_test", None)
    return fake


def test_a_jar_windows_antivirus_finds_something_in_goes_to_quarantine(signed_in, ctx, sites, amsi):
    path = mod_with(ctx.config.mods_dir / "av.jar", {"a/B.class": b"\xca\xfe" + MARK})
    report = signed_in.post(f"{BASE}/mods/scan").json()
    assert not path.exists()
    finding = report["quarantine"][0]["findings"][0]
    assert finding["scanner"] == antivirus.NAME
    assert finding["words"] == "jarscan.finding.antivirus"
    assert finding["values"] == {"where": "av.jar (a/B.class)"}


def test_the_weekly_check_asks_windows_antivirus_again_about_unchanged_jars(
    signed_in, ctx, sites, amsi
):
    path = mod_with(ctx.config.mods_dir / "later.jar", {"a/B.class": b"\xca\xfe" + MARK})
    amsi.bad = b"not there yet"
    signed_in.post(f"{BASE}/mods/scan")
    assert path.is_file()

    amsi.bad = MARK  # the antivirus got new signatures
    signed_in.portal.call(ctx.jarscan.tick)
    assert path.is_file()  # nothing changed and not a week yet: not read again

    saved = ctx.jarscan.store()
    saved["looked_up_at"] -= jarscan.WEEK + 1
    ctx.jarscan.save(saved)
    signed_in.portal.call(ctx.jarscan.tick)
    assert not path.exists()


def test_a_jar_checked_while_antivirus_was_off_is_checked_when_it_is_on(
    signed_in, ctx, sites, amsi, monkeypatch
):
    path = mod_with(ctx.config.mods_dir / "early.jar", {"a/B.class": MARK})
    monkeypatch.setattr(antivirus, "_engine", False)
    signed_in.post(f"{BASE}/mods/scan")
    assert path.is_file()
    monkeypatch.setattr(antivirus, "_engine", amsi)
    signed_in.post(f"{BASE}/mods/scan")
    assert not path.exists()


def test_a_server_start_doesnt_wait_for_antivirus_to_catch_up(
    signed_in, ctx, sites, amsi, monkeypatch
):
    path = mod_with(ctx.config.mods_dir / "early.jar", {"a/B.class": MARK})
    monkeypatch.setattr(antivirus, "_engine", False)
    signed_in.post(f"{BASE}/mods/scan")
    monkeypatch.setattr(antivirus, "_engine", amsi)
    signed_in.portal.call(ctx.jarscan.before_start)
    assert amsi.names == [] and path.is_file()


def test_a_jar_antivirus_couldnt_check_isnt_asked_again_every_tick(signed_in, ctx, sites, amsi):
    mod_with(ctx.config.mods_dir / "early.jar", {"a/B.class": b"fine"})
    amsi.broken = True
    signed_in.post(f"{BASE}/mods/scan")
    amsi.broken = False
    mod_with(ctx.config.mods_dir / "later.jar", {"a/C.class": b"fine"})
    signed_in.portal.call(ctx.jarscan.tick)
    assert amsi.names and not any(n.startswith("early.jar") for n in amsi.names)


def test_the_antivirus_test_button_keeps_its_result(signed_in, ctx, amsi):
    amsi.bad = b"EICAR-STANDARD-ANTIVIRUS"
    response = signed_in.post(f"{BASE}/protection/fix", json={"fix": "antivirus_test"})
    assert response.status_code == 200, response.text
    assert response.json()["changed"] == {"antivirus": "on"}
    amsi.bad = b"never there"
    response = signed_in.post(f"{BASE}/protection/fix", json={"fix": "antivirus_test"})
    assert response.json()["changed"] == {"antivirus": "not_flagging"}


def test_the_antivirus_test_button_says_when_it_couldnt_ask(signed_in, monkeypatch):
    monkeypatch.setattr(antivirus, "_engine", False)
    response = signed_in.post(f"{BASE}/protection/fix", json={"fix": "antivirus_test"})
    assert response.status_code >= 400
    assert "couldn't be asked" in response.text


def test_protection_says_what_windows_antivirus_does():
    summary = {"checked": 1, "jars": 1, "found": 0, "unknown": 0, "couldnt": 0, "checked_at": 1}
    props = {"online-mode": "true", "white-list": "true"}

    def av_line(status, jar_scan=summary):
        found = lines(Facts(properties=props, jar_scan=jar_scan, antivirus=status))
        return [(x["id"], x["level"], x["fix"]) for x in found if x["id"].startswith("antivirus")]

    assert av_line("off_windows") == [("antivirus_off", "info", None)]
    assert av_line("untested") == [("antivirus_untested", "suggest", "antivirus_test")]
    assert av_line("not_flagging") == [("antivirus_not_flagging", "suggest", "antivirus_test")]
    assert av_line("on") == [("antivirus_on", "info", "antivirus_test")]
    assert av_line("on", jar_scan=None) == []  # no jars: no line


# ------------------------------------------------------------ protection
def test_protection_says_what_the_jar_check_did():
    summary = {"checked": 0, "jars": 0, "found": 0, "unknown": 0, "couldnt": 0}
    props = {"online-mode": "true", "white-list": "true"}

    def jar_lines(**scan):
        found = lines(Facts(properties=props, jar_scan=summary | scan))
        return [(x["id"], x["level"]) for x in found if x["id"].startswith("jar_scan")]

    assert jar_lines(checked_at=None) == [("jar_scan_not_yet", "info")]
    assert jar_lines(checked_at=1, checked=4, unknown=1) == [("jar_scan_done", "info")]
    assert jar_lines(checked_at=1, found=2, couldnt=1) == [
        ("jar_scan_found", "worst"),
        ("jar_scan_couldnt", "info"),
    ]
    assert lines(Facts(properties=props, jar_scan=None))  # no jars: no jar line
    assert not [
        x for x in lines(Facts(properties=props, jar_scan=None)) if x["id"].startswith("jar_scan")
    ]


def test_jar_check_wording_never_promises_too_much():
    banned = re.compile(r"\b(safe|clean|secure|protected)\b", re.IGNORECASE)
    prefixes = (
        "jarscan.",
        "protect.jar",
        "alert_event.jar",
        "protect.antivirus",
        "protect.windows_user",
    )
    keys = [k for k in STRINGS if k.startswith(prefixes + ("protect.fix.antivirus",))]
    assert len(keys) > 20
    for key in keys:
        for text in STRINGS[key]:
            assert not banned.search(text), key


# ------------------------------------------------------------ fixes
def test_a_jar_that_cant_be_read_keeps_its_row_and_isnt_read_every_tick(
    signed_in, ctx, sites, monkeypatch
):
    path = make_jar(ctx.config.mods_dir / "a.jar", "a")
    signed_in.post(f"{BASE}/mods/scan")
    make_jar(path, "changed")
    reads = []

    def broken(p):
        reads.append(p)
        raise OSError("locked")

    monkeypatch.setattr(jarscan, "check_jar", broken)
    report = signed_in.post(f"{BASE}/mods/scan").json()
    assert row(report, "a.jar")  # the old entry is kept
    assert len(reads) == 1
    signed_in.portal.call(ctx.jarscan.tick)
    assert len(reads) == 1  # the folder didn't change again: no read


def test_a_fixed_jar_that_cant_be_read_yet_isnt_moved_as_the_old_bad_one(
    signed_in, ctx, sites, monkeypatch
):
    path = bad_jar(ctx)
    monkeypatch.setattr(ctx.jarscan, "_busy", lambda: True)
    signed_in.post(f"{BASE}/mods/scan")  # waits for the server to stop
    make_jar(path, "fixed")  # the owner copied a fixed version over it
    monkeypatch.setattr(ctx.jarscan, "_busy", lambda: False)

    def broken(p):
        raise OSError("still copying")

    monkeypatch.setattr(jarscan, "check_jar", broken)
    signed_in.portal.call(ctx.jarscan.before_start)
    assert path.is_file()
    assert ctx.jarscan.report()["quarantine"] == []


def test_the_protection_line_drops_a_week_after_a_jar_went_to_quarantine(signed_in, ctx, sites):
    bad_jar(ctx)
    signed_in.post(f"{BASE}/mods/scan")
    props = {"online-mode": "true", "white-list": "true"}

    def jar_ids():
        found = lines(Facts(properties=props, jar_scan=ctx.jarscan.summary()))
        return [x["id"] for x in found if x["id"].startswith("jar_scan")]

    assert jar_ids() == ["jar_scan_found"]
    saved = ctx.jarscan.store()
    saved["quarantine"][0]["at"] -= jarscan.WEEK + 1
    ctx.jarscan.save(saved)
    assert ctx.jarscan.summary()["found"] == 1  # still listed on the Mods page
    assert jar_ids() == ["jar_scan_done"]
