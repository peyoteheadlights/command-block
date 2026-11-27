"""VirusTotal in the deep scan: the owner's own key, lookups by SHA-256
only, the free key's limits, and how many engines make a file Known bad.

VirusTotal is replaced with a small fake, so nothing here goes on the
internet and no test waits a real minute.
"""

# ruff: noqa: F811  (pytest fixtures imported from test_deepscan)
from __future__ import annotations

import asyncio
import hashlib
import json
import os

import httpx
import pytest

from agent import downloads
from agent.mods import deepscan, virustotal
from agent.mods.virustotal import read
from agent.security import permissions as P

from .test_deepscan import BASE, CLASS, ctx, scan, signed_in  # noqa: F401
from .test_helpers import add_helper
from .test_jarscan import STRINGS, mod_with
from .test_moderators import give, owner  # noqa: F401
from .test_scanprograms import no_downloads

KEY = "a" * 64


def answer(*bad: str, total: int = 70) -> dict:
    results = {name: {"category": "malicious", "result": "Trojan.X"} for name in bad}
    results["Quiet"] = {"category": "undetected", "result": None}
    stats = {"malicious": len(bad), "undetected": total - len(bad)}
    return {
        "data": {"attributes": {"last_analysis_stats": stats, "last_analysis_results": results}}
    }


class Fake:
    """VirusTotal: answers by SHA-256, 404 for any other file."""

    def __init__(self, monkeypatch, known=None, status=None):
        self.known = known or {}
        self.status = status
        self.asked: list[httpx.Request] = []
        monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(self.answer))

    def answer(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        if self.status:
            return httpx.Response(self.status)
        sha = request.url.path.rsplit("/", 1)[-1]
        if sha in self.known:
            return httpx.Response(200, json=self.known[sha])
        return httpx.Response(404, json={"error": {"code": "NotFoundError"}})


@pytest.fixture
def waits(monkeypatch):
    """A fake clock: sleeping moves it on, so no test waits for real."""
    now = [1000.0]
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(virustotal, "clock", lambda: now[0])
    monkeypatch.setattr(virustotal, "sleep", sleep)
    return slept


def with_key(ctx):
    vt = ctx.deepscan.virustotal
    virustotal.write_secrets(vt.key_path, {"key": KEY})
    return vt


def sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jar(ctx, name: str):
    return mod_with(ctx.config.mods_dir / name, {"a/B.class": CLASS + name.encode()})


# ------------------------------------------------------------ reading answers
def test_an_answer_counts_only_malicious_engines_of_those_that_answered():
    seen = read(answer("Beta", "Alpha", total=60))
    assert seen == {"known": True, "count": 2, "total": 60, "engines": ["Alpha", "Beta"]}


@pytest.mark.parametrize(
    "bad, kind",
    [((), None), (("A",), False), (("A", "B"), False), (("A", "B", "C"), True)],
)
def test_three_engines_make_known_bad_and_one_or_two_worth_a_look(bad, kind):
    found = virustotal.finding(read(answer(*bad)))
    if kind is None:
        assert found is None
        return
    words, known_bad = found
    assert known_bad is kind
    assert words["scanner"] == "VirusTotal" and words["words"] in STRINGS
    assert words["values"]["count"] == len(bad) and words["values"]["total"] == 70
    assert words["values"]["engines"] == ", ".join(bad)


def test_a_file_virustotal_never_saw_is_not_a_finding():
    assert virustotal.finding({"known": False}) is None


# ------------------------------------------------------------ the key
def test_the_owner_saves_a_key_once_virustotal_takes_it_and_it_is_never_sent_back(
    signed_in, ctx, monkeypatch
):
    fake = Fake(monkeypatch)
    saved = signed_in.post(f"{BASE}/mods/deepscan/virustotal", json={"key": KEY})
    assert saved.status_code == 200, saved.text
    assert KEY not in saved.text
    assert fake.asked[0].headers["x-apikey"] == KEY
    assert ctx.deepscan.virustotal.key() == KEY
    if os.name == "nt":  # DPAPI only exists on Windows
        assert KEY.encode() not in ctx.deepscan.virustotal.key_path.read_bytes()
    row = next(s for s in saved.json()["scanners"] if s["id"] == "virustotal")
    assert row["key"] is True and row["available"] is True
    removed = signed_in.delete(f"{BASE}/mods/deepscan/virustotal")
    row = next(s for s in removed.json()["scanners"] if s["id"] == "virustotal")
    assert row["key"] is False and row["available"] is False
    assert ctx.deepscan.virustotal.key() is None


def test_a_key_virustotal_refuses_is_not_saved(signed_in, ctx, monkeypatch):
    Fake(monkeypatch, status=401)
    saved = signed_in.post(f"{BASE}/mods/deepscan/virustotal", json={"key": KEY})
    assert saved.status_code == 400
    assert "didn't take that key" in saved.json()["detail"]
    assert ctx.deepscan.virustotal.key() is None


def test_a_key_with_odd_characters_is_refused_before_anything_is_sent(signed_in, monkeypatch):
    fake = Fake(monkeypatch)
    saved = signed_in.post(f"{BASE}/mods/deepscan/virustotal", json={"key": "a" * 40 + "\nX: y"})
    assert saved.status_code == 422 and fake.asked == []


def test_only_the_owner_saves_or_removes_the_key(multi_client, owner, monkeypatch):
    fake = Fake(monkeypatch)
    sam = add_helper(multi_client, owner)
    give(multi_client, owner, {"survival": P.RUN_IT | {P.DEEP_SCAN}})
    url = "/api/servers/survival/mods/deepscan/virustotal"
    assert multi_client.post(url, json={"key": KEY}, headers=sam).status_code == 403
    assert multi_client.delete(url, headers=sam).status_code == 403
    assert fake.asked == []


# ------------------------------------------------------------ the deep scan
def test_without_a_key_virustotal_is_left_out(signed_in, ctx):
    report = scan(signed_in, scope="mods", scanners=["behaviour", "virustotal"])
    assert report["last"]["scanners"] == ["behaviour"]
    assert deepscan.VIRUSTOTAL not in ctx.deepscan.installed()


def test_a_deep_scan_sends_only_each_jars_sha256(signed_in, ctx, monkeypatch):
    with_key(ctx)
    jars = [jar(ctx, "one.jar"), jar(ctx, "two.jar")]
    fake = Fake(monkeypatch)
    report = scan(signed_in, scope="mods", scanners=["virustotal"])
    assert sorted(r.url.path for r in fake.asked) == sorted(f"/api/v3/files/{sha(p)}" for p in jars)
    assert all(r.method == "GET" and r.content == b"" for r in fake.asked)
    last = report["last"]
    assert last["scanners"] == ["virustotal"] and last["found"] == []
    assert last["errors"] == [
        {"words": "deepscan.vt_unknown", "values": {"name": "VirusTotal", "count": 2}}
    ]
    row = next(s for s in report["scanners"] if s["id"] == "virustotal")
    assert row["took"]["files"] == 2


def test_three_engines_quarantine_a_jar_and_say_n_of_m(signed_in, ctx, monkeypatch):
    with_key(ctx)
    bad, odd = jar(ctx, "bad.jar"), jar(ctx, "odd.jar")
    Fake(monkeypatch, {sha(bad): answer("A", "B", "C"), sha(odd): answer("A")})
    last = scan(signed_in, scope="mods", scanners=["virustotal"])["last"]
    by_name = {item["path"].removeprefix("mods/"): item for item in last["found"]}
    assert by_name["bad.jar"]["kind"] == "known_bad"
    assert by_name["bad.jar"]["state"] == "quarantined" and not bad.exists()
    assert by_name["bad.jar"]["findings"][0]["values"] == {
        "count": 3,
        "total": 70,
        "engines": "A, B, C",
    }
    assert by_name["odd.jar"]["kind"] == "worth_a_look" and odd.exists()


def test_an_answer_is_kept_so_the_next_scan_asks_again_only_after_a_week(
    signed_in, ctx, monkeypatch
):
    with_key(ctx)
    jar(ctx, "one.jar")
    fake = Fake(monkeypatch)
    scan(signed_in, scope="mods", scanners=["virustotal"])
    scan(signed_in, scope="mods", scanners=["virustotal"])
    assert len(fake.asked) == 1


def make_older(vt, days: float) -> None:
    saved = json.loads(vt.state_path.read_text())
    for seen in saved["seen"].values():
        seen["at"] -= days * 86400
    vt.state_path.write_text(json.dumps(saved))


def test_a_known_answer_is_asked_again_after_a_week(ctx, monkeypatch, signed_in):
    vt = with_key(ctx)
    sha256 = "1" * 64
    fake = Fake(monkeypatch, known={sha256: answer("A")})
    signed_in.portal.call(vt.lookup, sha256)
    make_older(vt, 6.9)
    signed_in.portal.call(vt.lookup, sha256)
    assert len(fake.asked) == 1
    make_older(vt, 0.2)
    signed_in.portal.call(vt.lookup, sha256)
    assert len(fake.asked) == 2


def test_a_file_virustotal_did_not_know_is_asked_again_after_a_day(ctx, monkeypatch, signed_in):
    vt = with_key(ctx)
    fake = Fake(monkeypatch)
    signed_in.portal.call(vt.lookup, "2" * 64)
    make_older(vt, 0.9)
    signed_in.portal.call(vt.lookup, "2" * 64)
    assert len(fake.asked) == 1
    make_older(vt, 0.2)
    signed_in.portal.call(vt.lookup, "2" * 64)
    assert len(fake.asked) == 2


def test_two_servers_asking_about_one_file_at_once_spend_one_lookup(ctx, monkeypatch, signed_in):
    vt = with_key(ctx)
    fake = Fake(monkeypatch)
    fetch = downloads.fetch_json

    async def slow_fetch(*args, **kwargs):
        await asyncio.sleep(0)  # like a real request, let the other server run
        return await fetch(*args, **kwargs)

    monkeypatch.setattr(downloads, "fetch_json", slow_fetch)

    async def both():
        await asyncio.gather(vt.lookup("3" * 64), vt.lookup("3" * 64))

    signed_in.portal.call(both)
    assert len(fake.asked) == 1


def test_a_fifth_lookup_in_a_minute_waits_for_a_free_slot(ctx, waits, monkeypatch, signed_in):
    vt = with_key(ctx)
    fake = Fake(monkeypatch)
    for n in range(5):
        signed_in.portal.call(vt.lookup, f"{n:064x}")
    assert len(fake.asked) == 5
    assert waits == [60]


def test_a_lookup_that_would_wait_past_the_time_limit_stops(ctx, waits, monkeypatch, signed_in):
    vt = with_key(ctx)
    fake = Fake(monkeypatch)
    for n in range(4):
        signed_in.portal.call(vt.lookup, f"{n:064x}")
    with pytest.raises(virustotal.LimitReached) as caught:
        signed_in.portal.call(vt.lookup, f"{9:064x}", virustotal.clock() + 30)
    assert caught.value.words == "deepscan.error.vt_time"
    assert len(fake.asked) == 4 and waits == []


def test_after_500_lookups_in_a_day_nothing_more_is_sent(signed_in, ctx, monkeypatch):
    vt = with_key(ctx)
    saved = vt.state()
    saved["used"] = virustotal.PER_DAY
    vt._save(saved)
    jar(ctx, "one.jar")
    fake = Fake(monkeypatch)
    last = scan(signed_in, scope="mods", scanners=["virustotal"])["last"]
    assert fake.asked == []
    assert last["errors"] == [
        {
            "words": "deepscan.error.vt_day",
            "values": {"name": "VirusTotal", "limit": 500, "left": 1},
        }
    ]


def test_a_key_that_stopped_working_says_so_and_stops_asking(signed_in, ctx, monkeypatch):
    with_key(ctx)
    jar(ctx, "one.jar")
    jar(ctx, "two.jar")
    fake = Fake(monkeypatch, status=401)
    last = scan(signed_in, scope="mods", scanners=["virustotal"])["last"]
    assert len(fake.asked) == 1
    assert last["errors"][0]["words"] == "deepscan.error.vt_key"
    assert last["errors"][0]["values"]["left"] == 2


def test_runs_before_a_start_and_on_the_schedule_never_use_virustotal(ctx, monkeypatch, signed_in):
    with_key(ctx)
    no_downloads(monkeypatch)
    jar(ctx, "one.jar")
    fake = Fake(monkeypatch)
    assert ctx.deepscan.installed() == [deepscan.BEHAVIOUR]
    signed_in.portal.call(ctx.deepscan.run_now, "mods", "owner")
    assert fake.asked == []


def test_a_folder_scan_looks_up_jars_and_programs_but_not_other_files(signed_in, ctx, monkeypatch):
    with_key(ctx)
    server = ctx.config.server_dir
    (server / "tool.exe").write_bytes(b"MZ tool")
    (server / "notes.txt").write_text("hello")
    fake = Fake(monkeypatch)
    scan(signed_in, scope="folder", scanners=["virustotal"])
    asked = {r.url.path.rsplit("/", 1)[-1] for r in fake.asked}
    assert sha(server / "tool.exe") in asked
    assert hashlib.sha256(b"hello").hexdigest() not in asked


# ------------------------------------------------------------ the network
def test_the_key_never_follows_a_redirect(signed_in, monkeypatch):
    sent: list[httpx.Request] = []

    def answer_(request):
        sent.append(request)
        if request.url.host == "www.virustotal.com":
            return httpx.Response(302, headers={"location": "https://api.modrinth.com/v2/x"})
        return httpx.Response(200, json={})

    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(answer_))
    signed_in.portal.call(
        lambda: downloads.fetch_json(virustotal.API + "0" * 64, headers={"x-apikey": KEY})
    )
    assert sent[0].headers["x-apikey"] == KEY
    assert "x-apikey" not in sent[1].headers


def test_only_a_sha256_is_ever_looked_up(ctx, monkeypatch):
    with_key(ctx)
    fake = Fake(monkeypatch)
    for odd in ("upload_url", "0" * 63, "../x" + "0" * 60):
        with pytest.raises(virustotal.VirusTotalError):
            asyncio.run(ctx.deepscan.virustotal.lookup(odd))
    assert fake.asked == []


def test_only_virustotals_file_lookups_are_allowed_there():
    downloads.check_url(virustotal.API + "0" * 64)
    with pytest.raises(downloads.DownloadError):
        downloads.check_url("https://www.virustotal.com/gui/home")
