"""Windows antivirus (AMSI): looking inside jars, the test that turns it
off when nothing is found, and the install paths that refuse a jar it
finds something in.

AMSI only exists on Windows, so a fake stands in for amsi.dll: it "finds"
any piece of memory holding MARK.
"""

from __future__ import annotations

import hashlib
import io
import zipfile

import httpx
import pytest

from agent import downloads, modpack
from agent.database.db import Database
from agent.mods.manager import ModError
from agent.security import antivirus

from .test_modpack import make_pack
from .test_mods import make_jar, manager  # noqa: F401 (fixture)

MARK = b"FAKE-BAD-BYTES"


class FakeAmsi:
    def __init__(self, bad: bytes = MARK, broken: bool = False):
        self.bad = bad
        self.broken = broken
        self.names: list[str] = []
        self.open_sessions = 0

    def session(self):
        self.open_sessions += 1
        return object()

    def end(self, session):
        self.open_sessions -= 1

    def found(self, data, name, session):
        if self.broken:
            raise OSError("AmsiScanBuffer failed")
        self.names.append(name)
        return self.bad in data


@pytest.fixture
def amsi(monkeypatch):
    fake = FakeAmsi()
    monkeypatch.setattr(antivirus, "_engine", fake)
    monkeypatch.setattr(antivirus, "_test", None)
    return fake


def zipped(files: dict[str, bytes], packed: bool = True) -> bytes:
    """A zip; ``packed`` compresses it, so MARK isn't seen in the whole file."""
    out = io.BytesIO()
    method = zipfile.ZIP_DEFLATED if packed else zipfile.ZIP_STORED
    with zipfile.ZipFile(out, "w", method) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return out.getvalue()


def bad_mod(path):
    """A real mod jar with MARK in a class inside a jar inside it."""
    make_jar(path, "sneaky")
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("META-INF/jars/lib.jar", zipped({"a/B.class": b"\xca\xfe" + MARK}))
    return path


def test_it_looks_inside_jars_inside_jars(amsi):
    inner = zipped({"a/B.class": b"\xca\xfe" + MARK, "a/C.class": b"\xca\xfe"})
    jar = zipped({"fabric.mod.json": b"{}", "META-INF/jars/lib.jar": inner})
    assert antivirus.scan_jar(jar, "mod.jar") == ["META-INF/jars/lib.jar!/a/B.class"]
    assert "mod.jar:META-INF/jars/lib.jar!/a/C.class" in amsi.names
    assert amsi.open_sessions == 0


def test_a_hit_in_the_whole_file_is_said_as_the_file(amsi):
    jar = zipped({"a/B.class": MARK}, packed=False)
    found = antivirus.scan_jar(jar, "mod.jar")
    assert found[0] == ""
    assert antivirus.where_words(found, "mod.jar").startswith("mod.jar")


def test_nothing_found_is_empty_and_not_checked_is_none(amsi, monkeypatch):
    assert antivirus.scan_jar(zipped({"a/B.class": b"\xca\xfe"}), "mod.jar") == []
    amsi.broken = True
    assert antivirus.scan_jar(zipped({"a/B.class": b"\xca\xfe"}), "mod.jar") is None
    assert amsi.open_sessions == 0
    monkeypatch.setattr(antivirus, "_engine", False)
    assert antivirus.status() == "off_windows"
    assert antivirus.scan_jar(zipped({"a/B.class": MARK}), "mod.jar") is None


def test_an_empty_file_inside_is_not_handed_over(amsi):
    jar = zipped({"empty.txt": b"", "a/B.class": b"\xca\xfe" + MARK})
    assert antivirus.scan_jar(jar, "mod.jar") == ["a/B.class"]
    assert "mod.jar:empty.txt" not in amsi.names


def test_a_test_that_couldnt_ask_keeps_the_last_result(amsi, tmp_path):
    db = Database(tmp_path / "db.sqlite")
    amsi.bad = b"EICAR-STANDARD-ANTIVIRUS"
    assert antivirus.run_test(db) == {"status": "on"}
    amsi.broken = True
    assert antivirus.run_test(db) == {"status": "couldnt"}
    assert antivirus.status() == "on"


def test_unpacking_stops_at_the_size_budget(amsi, monkeypatch):
    monkeypatch.setattr(antivirus, "MAX_TOTAL", 1000)
    jar = zipped({"a.bin": b"x" * 900, "b.bin": b"y" * 900, "c.bin": MARK})
    antivirus.scan_jar(jar, "mod.jar")
    assert "mod.jar:a.bin" in amsi.names
    assert "mod.jar:b.bin" not in amsi.names and "mod.jar:c.bin" not in amsi.names


def test_the_test_keeps_the_check_on_only_when_it_finds_the_test_file(amsi, tmp_path):
    db = Database(tmp_path / "db.sqlite")
    assert antivirus.status() == "untested" and antivirus.is_on()

    amsi.bad = b"EICAR-STANDARD-ANTIVIRUS"
    assert antivirus.run_test(db) == {"status": "on"}
    assert db.get_setting(antivirus.TEST_KEY)["flagged"] is True

    amsi.bad = b"something that is never there"
    assert antivirus.run_test(db) == {"status": "not_flagging"}
    assert not antivirus.is_on()
    assert antivirus.scan_jar(zipped({"a/B.class": b"something that is never there"}), "x") is None

    antivirus.remember(db)  # as the agent starts again
    assert antivirus.status() == "not_flagging"
    amsi.bad = b"EICAR-STANDARD-ANTIVIRUS"
    assert antivirus.run_test(db) == {"status": "on"}  # a test again checks for real


def test_the_test_file_is_not_written_whole_in_the_source():
    source = (antivirus.__file__ and open(antivirus.__file__, encoding="utf-8").read()) or ""
    assert "EICAR-STANDARD-ANTIVIRUS-TEST-FILE" not in source


# ------------------------------------------------------------ install paths
async def test_an_upload_it_finds_something_in_is_refused(amsi, manager, config, tmp_path):  # noqa: F811
    data = bad_mod(tmp_path / "sneaky.jar").read_bytes()
    with pytest.raises(ModError, match="Windows antivirus found something in sneaky.jar"):
        await manager.install_local_file("sneaky.jar", data, user="tester")
    assert not list(config.mods_dir.glob("*sneaky*"))


async def test_an_upload_it_finds_nothing_in_is_installed(amsi, manager, config, tmp_path):  # noqa: F811
    data = make_jar(tmp_path / "fine.jar", "fine").read_bytes()
    await manager.install_local_file("fine.jar", data, user="tester")
    assert (config.mods_dir / "fine.jar").is_file()


async def test_a_downloaded_jar_it_finds_something_in_leaves_nothing(amsi, tmp_path, monkeypatch):
    data = bad_mod(tmp_path / "src.jar").read_bytes()
    monkeypatch.setattr(
        downloads, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, content=data))
    )
    spec = downloads.FileSpec(
        url="https://piston-data.mojang.com/v1/server.jar",
        name="server.jar",
        sha1=hashlib.sha1(data).hexdigest(),
    )
    folder = tmp_path / "out"
    with pytest.raises(downloads.DownloadError, match="Windows antivirus found something"):
        await downloads.download(spec, folder)
    assert list(folder.iterdir()) == []


async def test_a_downloaded_file_that_isnt_a_jar_is_not_handed_over(amsi, tmp_path, monkeypatch):
    monkeypatch.setattr(
        downloads, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, content=MARK))
    )
    spec = downloads.FileSpec(
        url="https://piston-meta.mojang.com/v1/list.json",
        name="list.json",
        sha1=hashlib.sha1(MARK).hexdigest(),
    )
    await downloads.download(spec, tmp_path)
    assert amsi.names == []


def test_a_jar_in_a_modpack_overrides_is_checked(amsi, tmp_path):
    jar = bad_mod(tmp_path / "x.jar").read_bytes()
    archive = make_pack(
        tmp_path / "p.mrpack", files=[], overrides={"overrides/mods/extra.jar": jar}
    )
    pack = modpack.inspect(archive)
    folder = tmp_path / "server"
    with pytest.raises(modpack.ModpackError, match="Windows antivirus found something"):
        modpack._extract_overrides(pack, archive, folder)
    assert not list(folder.rglob("*.jar*"))
