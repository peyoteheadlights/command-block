"""Jar changed outside the app: a jar that is new or different since the
last check, when nothing in the app changed the folder, gets a warning
line and a notification. Changes the agent made itself never warn."""

from __future__ import annotations

import shutil

from agent.protection import Facts, lines

from .test_jarscan import (  # noqa: F401 (fixture)  # noqa: F401 (fixtures)
    BASE,
    bad_jar,
    ctx,
    row,
    signed_in,
    sites,
)
from .test_mods import make_jar


def changed_events(ctx):  # noqa: F811
    events = []
    ctx.bus.subscribe(lambda e: events.append(e) if e.type == "jar_changed" else None)
    return events


def scan(client):
    response = client.post(f"{BASE}/mods/scan")
    assert response.status_code == 200, response.text
    return response.json()


def test_the_first_check_never_warns(signed_in, ctx, sites):  # noqa: F811
    make_jar(ctx.config.mods_dir / "a.jar", "a")
    events = changed_events(ctx)
    report = scan(signed_in)
    assert not row(report, "a.jar")["changed_outside"]
    assert report["summary"]["changed"] == 0
    assert events == []


def test_a_jar_copied_in_by_hand_warns(signed_in, ctx, sites):  # noqa: F811
    make_jar(ctx.config.mods_dir / "a.jar", "a")
    scan(signed_in)
    events = changed_events(ctx)
    make_jar(ctx.config.mods_dir / "dropped.jar", "dropped")

    report = scan(signed_in)
    assert row(report, "dropped.jar")["changed_outside"]
    assert not row(report, "a.jar")["changed_outside"]
    assert report["summary"]["changed"] == 1
    assert len(events) == 1
    assert events[0].level == "warn"
    assert events[0].data["filenames"] == ["dropped.jar"]

    scan(signed_in)
    assert len(events) == 1  # told once, the line stays until someone says OK


def test_a_jar_whose_bytes_changed_warns(signed_in, ctx, sites):  # noqa: F811
    path = make_jar(ctx.config.mods_dir / "a.jar", "a")
    scan(signed_in)
    make_jar(path, "a", "2.0")
    assert row(scan(signed_in), "a.jar")["changed_outside"]


def test_a_check_before_start_warns_too(signed_in, ctx, sites):  # noqa: F811
    scan(signed_in)
    events = changed_events(ctx)
    make_jar(ctx.config.mods_dir / "dropped.jar", "dropped")
    signed_in.portal.call(ctx.jarscan.before_start)
    assert row(ctx.jarscan.report(), "dropped.jar")["changed_outside"]
    assert [e.data["filenames"] for e in events] == [["dropped.jar"]]


def test_a_jar_added_by_the_app_does_not_warn(signed_in, ctx, sites, tmp_path):  # noqa: F811
    scan(signed_in)
    data = make_jar(tmp_path / "added.jar", "added").read_bytes()
    added = signed_in.post(
        "/api/mods/upload?confirm=true",
        files={"file": ("added.jar", data, "application/octet-stream")},
    )
    assert added.status_code == 200, added.text
    report = scan(signed_in)
    assert not row(report, "added.jar")["changed_outside"]


def test_a_jar_written_during_a_change_job_does_not_warn(signed_in, ctx, sites, tmp_path):  # noqa: F811
    """A restore or modpack install writes jars without noting each one."""
    scan(signed_in)
    path = make_jar(tmp_path / "restored.jar", "restored")

    async def restore():
        shutil.copy(path, ctx.config.mods_dir / "restored.jar")

    signed_in.portal.call(ctx.mod_change, "Restoring a backup", restore)
    assert not row(scan(signed_in), "restored.jar")["changed_outside"]


def test_a_jar_the_app_installed_before_does_not_warn(signed_in, ctx, sites, tmp_path):  # noqa: F811
    """Long after the job: its SHA-256 is in the add-on history."""
    scan(signed_in)
    path = make_jar(tmp_path / "old.jar", "old")
    from agent.mods.jarscan import check_jar

    ctx.mods.record("install", "owner", mod_name="old", sha256=check_jar(path)["sha256"])
    ctx.db.execute("UPDATE jobs SET finished_at = 0")
    shutil.copy(path, ctx.config.mods_dir / "old.jar")
    assert not row(scan(signed_in), "old.jar")["changed_outside"]


def test_a_jar_put_back_with_undo_or_renamed_does_not_warn(signed_in, ctx, sites):  # noqa: F811
    make_jar(ctx.config.mods_dir / "a.jar", "a")
    bad_jar(ctx)
    item = scan(signed_in)["quarantine"][0]
    assert signed_in.post(f"{BASE}/mods/scan/undo", json={"id": item["id"]}).status_code == 200
    ctx.db.execute("UPDATE jobs SET finished_at = 0")  # as if the Undo was long ago
    (ctx.config.mods_dir / "a.jar").rename(ctx.config.mods_dir / "renamed.jar")
    report = scan(signed_in)
    assert not row(report, "bad.jar")["changed_outside"]
    assert not row(report, "renamed.jar")["changed_outside"]


def test_that_was_me_clears_the_warning(signed_in, ctx, sites):  # noqa: F811
    scan(signed_in)
    make_jar(ctx.config.mods_dir / "one.jar", "one")
    make_jar(ctx.config.mods_dir / "two.jar", "two")
    scan(signed_in)

    cleared = signed_in.post(f"{BASE}/mods/scan/changed", json={"filename": "one.jar"})
    assert cleared.status_code == 200, cleared.text
    assert not row(cleared.json(), "one.jar")["changed_outside"]
    assert row(cleared.json(), "two.jar")["changed_outside"]

    everything = signed_in.post(f"{BASE}/mods/scan/changed", json={}).json()
    assert everything["summary"]["changed"] == 0


def test_protection_says_a_jar_changed_outside_the_app():
    summary = {"checked": 1, "jars": 2, "found": 0, "unknown": 0, "couldnt": 0, "checked_at": 1}
    props = {"online-mode": "true", "white-list": "true"}

    def changed(count):
        found = lines(Facts(properties=props, jar_scan=summary | {"changed": count}))
        return [(x["id"], x["level"], x["values"]) for x in found if x["id"] == "jar_changed"]

    assert changed(0) == []
    assert changed(2) == [("jar_changed", "problem", {"count": 2})]


def test_a_backup_since_the_last_check_does_not_hide_a_dropped_jar(signed_in, ctx, sites):  # noqa: F811
    """Only jobs that write jars count; a backup or a settings change
    leaves the folder alone."""
    scan(signed_in)
    make_jar(ctx.config.mods_dir / "dropped.jar", "dropped")

    async def backup():
        return None

    signed_in.portal.call(
        lambda: ctx.core.jobs.run(
            "backup", "Backing up", lambda _h: backup(), server_id=ctx.server_id, risky=True
        )
    )
    assert row(scan(signed_in), "dropped.jar")["changed_outside"]


def test_a_flagged_jar_turned_off_keeps_its_warning(signed_in, ctx, sites):  # noqa: F811
    scan(signed_in)
    make_jar(ctx.config.mods_dir / "dropped.jar", "dropped")
    scan(signed_in)
    (ctx.config.mods_dir / "dropped.jar").rename(ctx.config.mods_dir / "dropped.jar.disabled")
    report = scan(signed_in)
    assert row(report, "dropped.jar.disabled")["changed_outside"]
