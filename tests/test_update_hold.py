"""Update hold: a version its site put out in the last few days (3 by
default, the owner picks 0 to 14) is left out of Update all and of the
update notices, so a hijacked release has time to be found and pulled.
Update now on one mod or plugin skips the wait."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from agent.security import permissions as P

from .test_hangar_and_links import paper, plugin_jar, sites  # noqa: F401 (fixtures)
from .test_helpers import add_helper
from .test_moderators import give, owner  # noqa: F401

HOUR = 3600


def fresh(sites, monkeypatch, version_ids, hours_ago=1):  # noqa: F811
    """Hangar says these versions came out ``hours_ago``."""
    real = sites.version
    when = datetime.fromtimestamp(time.time() - hours_ago * HOUR, UTC)

    def version(pid, entry):
        found = real(pid, entry)
        if entry[0] in version_ids:
            found["createdAt"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        return found

    monkeypatch.setattr(sites, "version", version)
    return when.timestamp()


def two_plugins(client, folder, sites):  # noqa: F811
    (folder / "ViaVersion-5.1.jar").write_bytes(sites.jar(300))
    (folder / "Vault-1.6.jar").write_bytes(plugin_jar("Vault", "1.6"))
    client.app.state.core.mods.db.set_setting(
        "mod_source:Vault", {"source": "hangar", "project": "hangar:50"}
    )


def plan_rows(client, headers):
    plan = client.get("/api/mods/update-all", headers=headers)
    assert plan.status_code == 200, plan.text
    return {r["mod_id"]: r for r in plan.json()["updates"]}


def update_all(client, headers, rows):
    picks = [{"filename": r["filename"], "version_id": r["version_id"]} for r in rows.values()]
    return client.post("/api/mods/update-all", json={"mods": picks}, headers=headers)


def test_a_new_version_is_held_for_three_days(paper, sites, monkeypatch):  # noqa: F811
    client, headers, folder = paper
    two_plugins(client, folder, sites)
    out = fresh(sites, monkeypatch, {301})
    rows = plan_rows(client, headers)
    assert abs(rows["ViaVersion"]["held_until"] - (out + 3 * 24 * HOUR)) < 5
    assert rows["Vault"]["held_until"] is None  # out since October

    mods = {m["mod_id"]: m for m in client.get("/api/mods", headers=headers).json()["mods"]}
    assert mods["ViaVersion"]["update_available"]["held_until"] == rows["ViaVersion"]["held_until"]


def test_update_all_leaves_held_ones_out_and_names_them(paper, sites, monkeypatch):  # noqa: F811
    client, headers, folder = paper
    two_plugins(client, folder, sites)
    fresh(sites, monkeypatch, {301})
    done = update_all(client, headers, plan_rows(client, headers))
    assert done.status_code == 200, done.text
    assert [u["name"] for u in done.json()["updated"]] == ["Vault"]
    assert done.json()["held"] == ["ViaVersion"]
    assert (folder / "ViaVersion-5.1.jar").is_file()


def test_update_all_with_only_held_ones_says_to_use_update_now(paper, sites, monkeypatch):  # noqa: F811
    client, headers, folder = paper
    two_plugins(client, folder, sites)
    fresh(sites, monkeypatch, {301, 500})
    refused = update_all(client, headers, plan_rows(client, headers))
    assert refused.status_code == 400
    assert "Update now" in refused.json()["detail"]
    assert (folder / "ViaVersion-5.1.jar").is_file() and (folder / "Vault-1.6.jar").is_file()


def test_update_now_skips_the_wait(paper, sites, monkeypatch):  # noqa: F811
    client, headers, folder = paper
    two_plugins(client, folder, sites)
    fresh(sites, monkeypatch, {301})
    row = plan_rows(client, headers)["ViaVersion"]
    done = client.post(
        "/api/mods/update",
        json={"filename": row["filename"], "version_id": row["version_id"]},
        headers=headers,
    )
    assert done.status_code == 200, done.text
    assert (folder / "ViaVersion-5.2.jar").is_file()


def test_held_updates_are_not_told_or_recommended(paper, sites, monkeypatch):  # noqa: F811
    client, headers, folder = paper
    two_plugins(client, folder, sites)
    fresh(sites, monkeypatch, {301})
    core = client.app.state.core
    told = []
    core.bus.subscribe(lambda e: told.append(e) if e.type == "mod_updates_available" else None)
    client.portal.call(core.default._tell_updates)
    assert [u["mod_id"] for u in told[0].data["updates"]] == ["Vault"]

    found = client.get("/api/servers/test/recommendations", headers=headers).json()
    names = next(r for r in found["recommendations"] if r["id"] == "mod_updates")["details"]
    assert names["mods"] == ["Vault"]


def test_nothing_is_told_when_every_update_is_held(paper, sites, monkeypatch):  # noqa: F811
    client, headers, folder = paper
    two_plugins(client, folder, sites)
    fresh(sites, monkeypatch, {301, 500})
    core = client.app.state.core
    told = []
    core.bus.subscribe(lambda e: told.append(e) if e.type == "mod_updates_available" else None)
    client.portal.call(core.default._tell_updates)
    assert told == []


def test_the_owner_picks_0_to_14_days(paper, sites, monkeypatch):  # noqa: F811
    client, headers, folder = paper
    two_plugins(client, folder, sites)
    out = fresh(sites, monkeypatch, {301}, hours_ago=24 * 5)
    url = "/api/mods/scan/options"
    assert client.post(url, json={"update_hold_days": 15}, headers=headers).status_code == 422
    assert client.post(url, json={"update_hold_days": -1}, headers=headers).status_code == 422
    assert plan_rows(client, headers)["ViaVersion"]["held_until"] is None  # 5 days > 3

    saved = client.post(url, json={"update_hold_days": 7}, headers=headers)
    assert saved.status_code == 200 and saved.json()["update_hold_days"] == 7
    assert abs(plan_rows(client, headers)["ViaVersion"]["held_until"] - (out + 7 * 24 * HOUR)) < 5

    client.post(url, json={"update_hold_days": 0}, headers=headers)
    assert plan_rows(client, headers)["ViaVersion"]["held_until"] is None


def test_only_the_owner_changes_the_hold(multi_client, owner):  # noqa: F811
    sam = add_helper(multi_client, owner)
    give(multi_client, owner, {"survival": P.RUN_IT | {P.DEEP_SCAN}})
    url = "/api/servers/survival/mods/scan/options"
    assert multi_client.post(url, json={"update_hold_days": 0}, headers=sam).status_code == 403
    assert multi_client.post(url, json={"update_hold_days": 0}, headers=owner).status_code == 200
