"""Deep scan: our behaviour rules, YARA Forge's download, the three scopes,
quarantine of Known bad files and the Quarantine button on Worth a look.

GitHub and Windows antivirus are replaced with small fakes, so nothing here
goes on the internet.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import zipfile
from pathlib import Path, PurePosixPath

import httpx
import pytest

from agent import downloads
from agent.mods import deepscan
from agent.mods.deepscan import compile_rules, file_check, walk, yara_findings
from agent.protection import Facts, lines
from agent.security import antivirus
from agent.security import permissions as P

from .test_antivirus import MARK, FakeAmsi
from .test_api import auth, token_for
from .test_first_run_safety import wait_for_job
from .test_helpers import add_helper
from .test_jarscan import STRINGS, jar_bytes, mod_with
from .test_moderators import give, owner  # noqa: F401

BASE = "/api/servers/test"
CLASS = b"\xca\xfe\xba\xbe\x00\x00\x00\x41"
BAD_CLASSES = {
    "loads_code_from_the_internet": b"java/net/URLClassLoader https://evil.example.net/x.jar "
    b"loadClass java/lang/reflect/Method",
    "starts_a_program": b"java/lang/ProcessBuilder powershell -w hidden -c x",
    "reads_login_files": b"java/io/File .minecraft/launcher_accounts.json",
    "sends_to_a_discord_webhook": b"https://discord.com/api/webhooks/123456789012345678/"
    + b"A" * 40,
    "hides_encoded_code": b"yv66vgAAAEEA",
}


def scanner():
    import yara_x

    return yara_x.Scanner(compile_rules(None))


def names(found):
    return sorted(f["values"]["rule"] for f in found)


# ------------------------------------------------------------ rules
@pytest.mark.parametrize("rule, body", BAD_CLASSES.items())
def test_each_behaviour_rule_finds_its_class(rule, body):
    found, slow = yara_findings(scanner(), CLASS + body)
    assert names(found) == [rule] and not slow
    assert found[0]["words"] in STRINGS


@pytest.mark.parametrize("body", BAD_CLASSES.values())
def test_the_same_text_outside_a_class_file_is_not_a_finding(body):
    assert yara_findings(scanner(), b"plain text " + body)[0] == []


def test_a_plain_class_is_not_a_finding():
    body = b"java/lang/Runtime exec java/net/URL https://modrinth.com loadClass"
    assert yara_findings(scanner(), CLASS + body)[0] == []


def test_a_class_inside_a_jar_inside_a_jar_says_where():
    inner = jar_bytes({"a/B.class": CLASS + BAD_CLASSES["starts_a_program"]})
    outer = jar_bytes({"META-INF/jars/lib.jar": inner})
    found, _ = yara_findings(scanner(), outer)
    assert names(found) == ["starts_a_program"]
    assert found[0]["values"]["where"] == "META-INF/jars/lib.jar!/a/B.class"


def test_a_forge_rule_is_said_with_its_description_and_a_broken_one_is_left_out(tmp_path):
    import yara_x

    forge = tmp_path / "forge.yar"
    forge.write_text(
        'rule Forge_Test { meta: description = "Test rule" author = "someone" '
        'strings: $a = "FORGE-MARK" condition: $a }\n'
        "rule Broken { condition: no_such_thing }\n",
        encoding="utf-8",
    )
    found, _ = yara_findings(yara_x.Scanner(compile_rules(forge)), b"xx FORGE-MARK xx")
    assert found == [
        {
            "scanner": "YARA Forge rules",
            "words": "deepscan.finding.forge",
            "values": {
                "rule": "Forge_Test",
                "where": "",
                "description": "Test rule",
                "author": "someone",
                "license": "",
            },
        }
    ]


def test_the_rules_find_nothing_in_a_normal_mod(tmp_path):
    path = mod_with(tmp_path / "ok.jar", {"a/B.class": CLASS + b"net/minecraft/Block"})
    assert yara_findings(scanner(), path.read_bytes())[0] == []


# ------------------------------------------------------------ files
@pytest.mark.parametrize(
    "rel, words",
    [
        ("tools/miner.exe", "deepscan.finding.program"),
        ("start.ps1", "deepscan.finding.program"),
        ("config/x/hook.BAT", "deepscan.finding.program"),
        ("run.bat", None),
        ("run.sh", None),
        ("config/run.bat", "deepscan.finding.program"),
        ("world/hidden.jar", "deepscan.finding.stray_jar"),
        ("extra.jar", "deepscan.finding.stray_jar"),
        ("server.jar", None),
        ("mods/a.jar", None),
        ("plugins/a.jar", None),
        ("plugins_old/a.jar", None),
        ("libraries/org/x.jar", None),
        ("world/region/r.0.0.mca", None),
        ("server.properties", None),
    ],
)
def test_the_file_check(rel, words):
    found = file_check(PurePosixPath(rel), "server.jar")
    assert (found and found["words"]) == words


def test_walk_skips_links_and_the_folders_it_is_told(tmp_path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "a.txt").write_text("a")
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "b.txt").write_text("b")
    (tmp_path / "link.txt").symlink_to(tmp_path / "keep" / "a.txt")
    (tmp_path / "linkdir").symlink_to(tmp_path / "keep")
    found = [p.relative_to(tmp_path).as_posix() for p in walk(tmp_path, [tmp_path / "skip"])]
    assert found == ["keep/a.txt"]


def test_every_word_the_scan_says_is_in_the_strings():
    source = Path(deepscan.__file__).read_text(encoding="utf-8")
    source += Path(deepscan.RULES).read_text(encoding="utf-8")
    keys = set(re.findall(r'"(deepscan\.[a-z_.]+)"', source))
    keys |= {f"deepscan.{k}.{s}" for k in ("scanner", "what") for s in deepscan.SCANNERS}
    keys |= {f"deepscan.speed.{s['speed']}" for s in deepscan.SCANNERS.values()}
    assert len(keys) > 15
    assert keys - set(STRINGS) == set()


def test_deep_scan_wording_never_promises_too_much():
    for key, words in STRINGS.items():
        if key.startswith(("deepscan.", "protect.deep_scan")):
            text = " ".join(words).lower()
            assert not re.search(r"\b(safe|clean|malware-free|virus-free)\b", text), key


# ------------------------------------------------------------ API
@pytest.fixture
def signed_in(client):
    client.headers.update(auth(token_for(client)))
    return client


@pytest.fixture
def ctx(signed_in, monkeypatch):
    # The PC's own Java has hundreds of files; each test that needs one
    # makes a small one.
    monkeypatch.setattr(deepscan, "java_root", lambda java: None)
    return signed_in.app.state.core.servers["test"]


def fake_java(tmp_path, monkeypatch) -> Path:
    java = tmp_path / "jdk"
    (java / "bin").mkdir(parents=True)
    (java / "release").write_text('JAVA_VERSION="25"')
    (java / "bin" / "java.exe").write_bytes(b"MZ")
    monkeypatch.setattr(deepscan, "java_root", lambda name: java)
    return java


def scan(client, **body):
    body.setdefault("scanners", ["behaviour"])
    started = client.post(f"{BASE}/mods/deepscan", json=body)
    assert started.status_code == 200, started.text
    job = wait_for_job(client, started.json()["running"]["id"])
    assert job["state"] == "succeeded", job
    return client.get(f"{BASE}/mods/deepscan").json()


def bad_mod(ctx, name="bad.jar", rule="starts_a_program") -> Path:
    return mod_with(ctx.config.mods_dir / name, {"a/B.class": CLASS + BAD_CLASSES[rule]})


def test_the_status_lists_each_scan_with_what_it_does(signed_in, ctx):
    report = signed_in.get(f"{BASE}/mods/deepscan").json()
    assert [s["id"] for s in report["scanners"]] == ["behaviour", "yara_forge"]
    assert report["scanners"][0]["speed"] == "deepscan.speed.quick"
    assert report["scanners"][0]["took"] is None  # never a guessed time
    assert report["last"] is None and report["running"] is None


def test_a_mods_scan_finds_worth_a_look_and_the_quarantine_button_moves_it(signed_in, ctx):
    path = bad_mod(ctx)
    mod_with(ctx.config.mods_dir / "ok.jar", {"a/B.class": CLASS})
    report = scan(signed_in, scope="mods")
    last = report["last"]
    assert last["files"] == 2
    assert [(f["path"], f["kind"], f["state"]) for f in last["found"]] == [
        ("mods/bad.jar", "worth_a_look", "found")
    ]
    assert path.is_file()  # Worth a look never moves on its own
    assert report["scanners"][0]["took"]["files"] == 2
    assert ctx.deepscan.summary()["worth_a_look"] == 1

    moved = signed_in.post(
        f"{BASE}/mods/deepscan/quarantine", json={"root": "server", "path": "mods/bad.jar"}
    )
    assert moved.status_code == 200, moved.text
    assert not path.exists()
    assert moved.json()["last"]["found"][0]["state"] == "quarantined"
    item = ctx.jarscan.report()["quarantine"][0]
    assert (ctx.config.mod_quarantine_dir / item["id"]).is_file()

    undone = signed_in.post(f"{BASE}/mods/scan/undo", json={"id": item["id"]})
    assert undone.status_code == 200, undone.text
    assert path.is_file()


def test_the_quarantine_button_refuses_a_file_changed_since_the_scan(signed_in, ctx):
    path = bad_mod(ctx)
    scan(signed_in, scope="mods")
    path.write_bytes(path.read_bytes() + b"x")
    response = signed_in.post(
        f"{BASE}/mods/deepscan/quarantine", json={"root": "server", "path": "mods/bad.jar"}
    )
    assert response.status_code == 400
    assert path.is_file()


def test_the_quarantine_button_refuses_a_file_not_on_the_list(signed_in, ctx):
    bad_mod(ctx)
    scan(signed_in, scope="mods")
    for body in (
        {"root": "server", "path": "mods/other.jar"},
        {"root": "server", "path": "../../config.yaml"},
    ):
        response = signed_in.post(f"{BASE}/mods/deepscan/quarantine", json=body)
        assert response.status_code == 400


def test_a_one_jar_scan_reads_only_that_jar(signed_in, ctx):
    bad_mod(ctx, "a.jar")
    bad_mod(ctx, "b.jar")
    last = scan(signed_in, scope="jar", filename="b.jar")["last"]
    assert last["files"] == 1 and last["found"][0]["path"] == "mods/b.jar"
    missing = signed_in.post(
        f"{BASE}/mods/deepscan",
        json={"scope": "jar", "filename": "c.jar", "scanners": ["behaviour"]},
    )
    assert missing.status_code == 400


def test_a_scan_needs_a_scanner(signed_in, ctx):
    response = signed_in.post(f"{BASE}/mods/deepscan", json={"scope": "mods", "scanners": []})
    assert response.status_code == 400


def test_only_one_scan_runs_at_a_time(signed_in, ctx, monkeypatch):
    class Running:
        state = "running"

        def to_dict(self):
            return {"id": "x", "state": "running"}

    ctx.deepscan.job_id = "x"
    monkeypatch.setattr(ctx.core.jobs, "get", lambda job_id: Running())
    response = signed_in.post(
        f"{BASE}/mods/deepscan", json={"scope": "mods", "scanners": ["behaviour"]}
    )
    assert response.status_code == 400
    assert "running" in response.json()["detail"]


def test_a_folder_scan_lists_programs_and_skips_regions_and_our_folders(signed_in, ctx):
    server = Path(ctx.config.server_dir)
    (server / "tools").mkdir()
    (server / "tools" / "miner.exe").write_bytes(b"MZ")
    region = server / "world" / "region"
    region.mkdir(parents=True)
    (region / "r.0.0.mca").write_bytes(CLASS + BAD_CLASSES["hides_encoded_code"])
    (server / "run.bat").write_text("java -jar server.jar")
    ctx.config.backup_dir.mkdir(parents=True, exist_ok=True)
    (ctx.config.backup_dir / "x.exe").write_bytes(b"MZ")
    last = scan(signed_in, scope="folder")["last"]
    paths = {f["path"]: f["findings"][0]["words"] for f in last["found"]}
    assert paths == {"tools/miner.exe": "deepscan.finding.program"}
    assert (server / "tools" / "miner.exe").is_file()


def test_a_folder_scan_sends_known_bad_files_to_quarantine(signed_in, ctx, amsi):
    server = Path(ctx.config.server_dir)
    (server / "config" / "deep").mkdir(parents=True)
    bad = server / "config" / "deep" / "x.dat"
    bad.write_bytes(b"data " + MARK)
    events = []
    ctx.bus.subscribe(events.append)

    last = scan(signed_in, scope="folder")["last"]
    item = next(f for f in last["found"] if f["path"] == "config/deep/x.dat")
    assert item["kind"] == "known_bad" and item["state"] == "quarantined"
    assert not bad.exists()
    assert "jar_scan_found" in [e.type for e in events]
    assert ctx.deepscan.summary()["known_bad"] == 1

    quarantined = ctx.jarscan.report()["quarantine"][0]
    undone = signed_in.post(f"{BASE}/mods/scan/undo", json={"id": quarantined["id"]})
    assert undone.status_code == 200, undone.text
    assert bad.read_bytes() == b"data " + MARK  # back in its own folder


def test_a_folder_scan_reads_the_java_the_server_runs_with(
    signed_in, ctx, amsi, tmp_path, monkeypatch
):
    java = fake_java(tmp_path, monkeypatch)
    (java / "lib" / "deep").mkdir(parents=True)
    bad = java / "lib" / "deep" / "evil.dll"
    bad.write_bytes(MARK)
    last = scan(signed_in, scope="folder")["last"]
    item = next(f for f in last["found"] if f["root"] == "java")
    assert (item["path"], item["state"]) == ("lib/deep/evil.dll", "quarantined")
    assert not any(f["path"] == "bin/java.exe" for f in last["found"])  # Java's own programs
    assert last["errors"] == []

    quarantined = ctx.jarscan.report()["quarantine"][0]
    assert (
        signed_in.post(f"{BASE}/mods/scan/undo", json={"id": quarantined["id"]}).status_code == 200
    )
    assert bad.read_bytes() == MARK


def test_a_folder_scan_says_when_it_couldnt_find_the_java(signed_in, ctx):
    last = scan(signed_in, scope="folder")["last"]
    assert [e["words"] for e in last["errors"]] == ["deepscan.error.no_java"]


def test_known_bad_waits_while_the_server_runs_and_moves_before_the_next_start(
    signed_in, ctx, amsi, monkeypatch
):
    bad = Path(ctx.config.server_dir) / "evil.dat"
    bad.write_bytes(MARK)
    monkeypatch.setattr(ctx.jarscan, "_busy", lambda: True)
    last = scan(signed_in, scope="folder")["last"]
    assert next(f for f in last["found"] if f["path"] == "evil.dat")["state"] == "waiting"
    assert bad.is_file()

    signed_in.portal.call(ctx.jarscan.before_start)
    assert not bad.exists()
    state = ctx.deepscan.store()["last"]["found"]
    assert next(f for f in state if f["path"] == "evil.dat")["state"] == "quarantined"


def test_known_bad_put_back_with_undo_is_kept_on_the_next_scan(signed_in, ctx, amsi):
    bad = Path(ctx.config.server_dir) / "evil.dat"
    bad.write_bytes(MARK)
    scan(signed_in, scope="folder")
    item = ctx.jarscan.report()["quarantine"][0]
    signed_in.post(f"{BASE}/mods/scan/undo", json={"id": item["id"]})
    last = scan(signed_in, scope="folder")["last"]
    assert next(f for f in last["found"] if f["path"] == "evil.dat")["state"] == "kept"
    assert bad.is_file()


def test_antivirus_is_only_asked_in_the_folder_scope(signed_in, ctx, amsi):
    mod_with(ctx.config.mods_dir / "x.jar", {"a/B.class": CLASS + MARK})
    amsi.names.clear()
    scan(signed_in, scope="mods")
    assert amsi.names == []


@pytest.fixture
def amsi(monkeypatch):
    fake = FakeAmsi()
    monkeypatch.setattr(antivirus, "_engine", fake)
    monkeypatch.setattr(antivirus, "_test", None)
    return fake


def test_a_moderator_who_runs_the_server_cannot_deep_scan(multi_client, owner):  # noqa: F811
    sam = add_helper(multi_client, owner)
    give(multi_client, owner, {"survival": P.RUN_IT})
    url = "/api/servers/survival/mods/deepscan"
    assert multi_client.get(url, headers=sam).status_code == 200
    body = {"scope": "mods", "scanners": ["behaviour"]}
    assert multi_client.post(url, json=body, headers=sam).status_code == 403
    quarantine = {"root": "server", "path": "mods/a.jar"}
    assert multi_client.post(f"{url}/quarantine", json=quarantine, headers=sam).status_code == 403


def test_deep_scan_is_in_no_level_and_has_its_own_group():
    for level in (P.START_AND_STOP, P.LOOK_AFTER_IT, P.RUN_IT):
        assert P.DEEP_SCAN not in level
    assert ("protection", (P.DEEP_SCAN,)) in P.GROUPS


# ------------------------------------------------------------ YARA Forge
def forge_zip(rule: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("packages/core/yara-rules-core.yar", rule)
    return out.getvalue()


FORGE_RULE = (
    b'rule Forge_Test { meta: description = "Test" strings: $a = "FORGE-MARK" condition: $a }'
)


def github(monkeypatch, archive: bytes, digest: str | None):
    asked = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.host == "api.github.com":
            asset = {
                "name": deepscan.FORGE_ASSET,
                "browser_download_url": "https://github.com/YARAHQ/yara-forge/releases/download/"
                "v1/yara-forge-rules-core.zip",
            }
            if digest is not None:
                asset["digest"] = digest
            return httpx.Response(200, json={"tag_name": "v1", "assets": [asset]})
        return httpx.Response(200, content=archive)

    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(answer))
    return asked


def test_forge_rules_are_downloaded_checked_and_used(signed_in, ctx, monkeypatch):
    archive = forge_zip(FORGE_RULE)
    github(monkeypatch, archive, "sha256:" + hashlib.sha256(archive).hexdigest())
    (ctx.config.mods_dir / "f.jar").write_bytes(jar_bytes({"x.txt": "FORGE-MARK"}))
    report = scan(signed_in, scope="mods", scanners=["behaviour", "yara_forge"])
    found = report["last"]["found"][0]["findings"]
    assert [f["values"]["rule"] for f in found] == ["Forge_Test"]
    assert report["scanners"][1]["downloaded"]["tag"] == "v1"
    assert report["last"]["errors"] == []


@pytest.mark.parametrize("digest", [None, "sha256:" + "0" * 64])
def test_forge_rules_without_a_matching_checksum_are_not_used(signed_in, ctx, monkeypatch, digest):
    github(monkeypatch, forge_zip(FORGE_RULE), digest)
    bad_mod(ctx)
    last = scan(signed_in, scope="mods", scanners=["behaviour", "yara_forge"])["last"]
    assert last["scanners"] == ["behaviour"]  # the behaviour check still ran
    assert [e["words"] for e in last["errors"]] == ["deepscan.error.forge"]
    assert last["found"][0]["path"] == "mods/bad.jar"
    assert not (ctx.deepscan.forge_dir / deepscan.FORGE_FILE).exists()


def test_an_old_copy_of_forge_rules_is_used_when_github_is_down(signed_in, ctx, monkeypatch):
    archive = forge_zip(FORGE_RULE)
    github(monkeypatch, archive, "sha256:" + hashlib.sha256(archive).hexdigest())
    signed_in.portal.call(ctx.deepscan.get_forge)
    info_file = ctx.deepscan.forge_dir / deepscan.FORGE_INFO
    info = json.loads(info_file.read_text())
    info_file.write_text(json.dumps({**info, "at": info["at"] - deepscan.WEEK - 1}))

    asked = github(monkeypatch, b"", None)
    (ctx.config.mods_dir / "f.jar").write_bytes(jar_bytes({"x.txt": "FORGE-MARK"}))
    last = scan(signed_in, scope="mods", scanners=["yara_forge"])["last"]
    assert asked  # it did try again
    assert [e["words"] for e in last["errors"]] == ["deepscan.error.forge_old"]
    assert last["found"][0]["findings"][0]["values"]["rule"] == "Forge_Test"


def test_fresh_forge_rules_are_not_downloaded_again(signed_in, ctx, monkeypatch):
    archive = forge_zip(FORGE_RULE)
    asked = github(monkeypatch, archive, "sha256:" + hashlib.sha256(archive).hexdigest())
    signed_in.portal.call(ctx.deepscan.get_forge)
    asked.clear()
    signed_in.portal.call(ctx.deepscan.get_forge)
    assert asked == []


# ------------------------------------------------------------ Protection
def test_protection_says_what_the_deep_scan_found():
    props = {"online-mode": "true", "white-list": "true"}
    summary = {"finished": 1, "files": 9, "known_bad": 0, "worth_a_look": 0}

    def deep_lines(scan):
        found = lines(Facts(properties=props, deep_scan=scan))
        return [
            (x["id"], x["level"], x["values"]) for x in found if x["id"].startswith("deep_scan")
        ]

    assert deep_lines(None) == []
    assert deep_lines(summary) == [("deep_scan_done", "info", {"files": 9})]
    assert deep_lines(summary | {"worth_a_look": 2}) == [
        ("deep_scan_look", "problem", {"count": 2})
    ]


# ------------------------------------------------------------ review fixes
def test_known_bad_moves_once_the_server_stops(signed_in, ctx, amsi, monkeypatch):
    bad = Path(ctx.config.server_dir) / "evil.dat"
    bad.write_bytes(MARK)
    monkeypatch.setattr(ctx.jarscan, "_busy", lambda: True)
    scan(signed_in, scope="folder")
    assert ctx.deepscan.waiting() and bad.is_file()
    monkeypatch.setattr(ctx.jarscan, "_busy", lambda: False)  # the server stopped
    signed_in.portal.call(ctx.jarscan.tick)
    assert not bad.exists()
    assert not ctx.deepscan.waiting()


def test_a_mods_folder_outside_the_server_folder_is_scanned_and_moved(
    signed_in, ctx, tmp_path, monkeypatch
):
    outside = tmp_path / "shared-mods"
    outside.mkdir()
    monkeypatch.setattr(type(ctx.config), "mods_dir", property(lambda _self: outside))
    ctx.mods._cache = None
    path = bad_mod(ctx)
    assert not str(path).startswith(str(ctx.config.server_dir))
    last = scan(signed_in, scope="mods")["last"]
    assert [(f["root"], f["path"]) for f in last["found"]] == [("mods", "bad.jar")]
    moved = signed_in.post(
        f"{BASE}/mods/deepscan/quarantine", json={"root": "mods", "path": "bad.jar"}
    )
    assert moved.status_code == 200, moved.text
    assert not path.exists()
    item = ctx.jarscan.report()["quarantine"][0]
    assert signed_in.post(f"{BASE}/mods/scan/undo", json={"id": item["id"]}).status_code == 200
    assert path.is_file()


def test_forge_rules_that_wont_compile_are_dropped_and_the_behaviour_check_still_runs(
    signed_in, ctx
):
    forge = ctx.deepscan.forge_dir
    forge.mkdir(parents=True, exist_ok=True)
    (forge / deepscan.FORGE_FILE).write_text("rule { broken")
    (forge / deepscan.FORGE_INFO).write_text(json.dumps({"tag": "v1", "at": 4e9}))
    assert ctx.deepscan.forge_info() is not None
    bad_mod(ctx)
    last = scan(signed_in, scope="mods", scanners=["behaviour", "yara_forge"])["last"]
    assert last["scanners"] == ["behaviour"]
    assert [e["words"] for e in last["errors"]] == ["deepscan.error.forge"]
    assert last["found"][0]["path"] == "mods/bad.jar"
    assert not (forge / deepscan.FORGE_FILE).exists()  # fetched again next time


def test_a_forge_download_without_a_rules_file_is_not_used(signed_in, ctx, monkeypatch):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("README.md", "no rules here")
    archive = out.getvalue()
    github(monkeypatch, archive, "sha256:" + hashlib.sha256(archive).hexdigest())
    bad_mod(ctx)
    last = scan(signed_in, scope="mods", scanners=["behaviour", "yara_forge"])["last"]
    assert [e["words"] for e in last["errors"]] == ["deepscan.error.forge"]
    assert last["found"][0]["path"] == "mods/bad.jar"


def test_broken_forge_notes_are_read_as_no_rules(ctx):
    forge = ctx.deepscan.forge_dir
    forge.mkdir(parents=True, exist_ok=True)
    (forge / deepscan.FORGE_FILE).write_text(FORGE_RULE.decode())
    (forge / deepscan.FORGE_INFO).write_text(json.dumps({"tag": "v1"}))
    assert ctx.deepscan.forge_info() is None


def test_starting_a_folder_scan_does_not_walk_the_folder_first(signed_in, ctx, monkeypatch):
    walked = []
    real = ctx.deepscan.targets

    def targets(scope, filename):
        walked.append(threading.current_thread() is threading.main_thread())
        return real(scope, filename)

    monkeypatch.setattr(ctx.deepscan, "targets", targets)
    scan(signed_in, scope="folder")
    assert walked == [False]  # only once, in a worker thread
