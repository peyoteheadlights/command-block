"""The same mod twice, and mods that do the same job (agent/mods/overlap.py):
which one to keep, only when the facts say so, and never one another
installed mod needs."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import notes, recommendations, signing
from agent.mods import fit, overlap

from .test_keeping_current import NOW, notes_body, signed
from .test_mods import make_jar, manager  # noqa: F401  (fixture)
from .test_recommendations import facts

MAPS = {
    "types": ["fabric", "paper"],
    "why": "They all draw a web map of the world.",
    "usual_pick": "swbUV1cr",
    "addons": [
        {"project": "swbUV1cr", "ids": ["bluemap"]},
        {"project": "fRQREgAc", "ids": ["dynmap"]},
        {"project": "PFb7ZqK6", "ids": ["squaremap"]},
    ],
}


@pytest.fixture
def maps(monkeypatch):
    monkeypatch.setattr(notes, "SAME_JOB", [notes._same_job(MAPS)])


def folder(manager):  # noqa: F811
    manager.mods_dir.mkdir(parents=True, exist_ok=True)
    return manager.mods_dir


def turned_off(row) -> list[str]:
    return [m["filename"] for m in row["mods"] if m["turn_off"]]


# ---------------------------------------------------------------- the same mod twice
def test_the_same_mod_twice_keeps_the_newer_one(manager):  # noqa: F811
    make_jar(folder(manager) / "sodium-old.jar", "sodium", "0.5.1", name="Sodium")
    make_jar(folder(manager) / "sodium-new.jar", "sodium", "0.5.2", name="Sodium")
    [row] = manager.check_all()["overlaps"]
    assert row["kind"] == "same_mod"
    assert row["keep"] == "sodium-new.jar"
    assert row["say"] == "Keep 0.5.2, turn off 0.5.1"
    assert turned_off(row) == ["sodium-old.jar"]


def test_the_same_mod_twice_keeps_the_one_another_mods_range_needs(manager):  # noqa: F811
    make_jar(folder(manager) / "sodium-old.jar", "sodium", "0.5.1")
    make_jar(folder(manager) / "sodium-new.jar", "sodium", "0.5.2")
    make_jar(folder(manager) / "extra.jar", "extra", depends={"sodium": "<0.5.2"}, name="Extra")
    [row] = manager.check_all()["overlaps"]
    assert row["keep"] == "sodium-old.jar"
    assert turned_off(row) == ["sodium-new.jar"]
    assert [m["needed_by"] for m in row["mods"] if m["filename"] == "sodium-old.jar"] == ["Extra"]


def test_versions_that_cant_be_compared_get_no_pick(manager):  # noqa: F811
    make_jar(folder(manager) / "a.jar", "sodium", "mc1.20-0.5.1")
    make_jar(folder(manager) / "b.jar", "sodium", "mc1.20-0.5.2")
    [row] = manager.check_all()["overlaps"]
    assert row["keep"] == "" and row["say"] == overlap.NO_PICK
    assert turned_off(row) == []


def test_a_turned_off_copy_isnt_counted(manager):  # noqa: F811
    make_jar(folder(manager) / "sodium-old.jar.disabled", "sodium", "0.5.1")
    make_jar(folder(manager) / "sodium-new.jar", "sodium", "0.5.2")
    assert manager.check_all()["overlaps"] == []


# ---------------------------------------------------------------- the same job
def test_mods_on_one_same_job_list_get_the_usual_pick(manager, maps):  # noqa: F811
    make_jar(folder(manager) / "bluemap.jar", "bluemap", name="BlueMap")
    make_jar(folder(manager) / "dynmap.jar", "dynmap", name="Dynmap")
    manager.remember("bluemap", "modrinth", {"project_id": "swbUV1cr"})
    [row] = manager.check_all()["overlaps"]
    assert row["kind"] == "same_job"
    assert row["why"] == MAPS["why"]
    assert row["say"] == "Keep BlueMap, turn off Dynmap"
    assert turned_off(row) == ["dynmap.jar"]


def test_the_one_built_for_this_minecraft_version_comes_before_the_usual_pick(
    manager,  # noqa: F811
    maps,
):
    make_jar(folder(manager) / "bluemap.jar", "bluemap", depends={"minecraft": "1.19.x"})
    make_jar(folder(manager) / "dynmap.jar", "dynmap", depends={"minecraft": "1.20.x"})
    [row] = manager.check_all()["overlaps"]
    assert row["keep"] == "dynmap.jar"


def test_one_another_mod_needs_is_never_the_one_to_turn_off(manager, maps):  # noqa: F811
    make_jar(folder(manager) / "bluemap.jar", "bluemap", depends={"minecraft": "1.20.x"})
    make_jar(folder(manager) / "dynmap.jar", "dynmap")
    make_jar(folder(manager) / "addon.jar", "dynmap-addon", depends={"dynmap": "*"}, name="Addon")
    [row] = manager.check_all()["overlaps"]
    assert row["keep"] == "dynmap.jar"
    assert turned_off(row) == ["bluemap.jar"]


def test_with_nothing_to_go_on_there_is_no_pick(manager, monkeypatch):  # noqa: F811
    monkeypatch.setattr(notes, "SAME_JOB", [notes._same_job({**MAPS, "usual_pick": ""})])
    make_jar(folder(manager) / "dynmap.jar", "dynmap")
    make_jar(folder(manager) / "squaremap.jar", "squaremap")
    [row] = manager.check_all()["overlaps"]
    assert row["keep"] == "" and row["say"] == overlap.NO_PICK
    assert turned_off(row) == []


def test_a_group_for_another_server_type_isnt_used(manager, monkeypatch):  # noqa: F811
    monkeypatch.setattr(notes, "SAME_JOB", [notes._same_job({**MAPS, "types": ["paper"]})])
    make_jar(folder(manager) / "bluemap.jar", "bluemap")
    make_jar(folder(manager) / "dynmap.jar", "dynmap")
    assert manager.check_all()["overlaps"] == []


PERMISSIONS = {
    "types": ["fabric"],
    "why": "They all decide who can use which commands, with ranks.",
    "usual_pick": "Vebnzrzj",
    "addons": [{"project": "Vebnzrzj", "ids": ["luckperms"]}, {"ids": ["groupmanager"]}],
}


def test_a_hand_copied_jar_listed_by_its_id_alone_is_matched(manager, monkeypatch):  # noqa: F811
    monkeypatch.setattr(notes, "SAME_JOB", [notes._same_job(PERMISSIONS)])
    make_jar(folder(manager) / "luckperms.jar", "luckperms", name="LuckPerms")
    make_jar(folder(manager) / "gm.jar", "groupmanager", name="GroupManager")
    manager.remember("luckperms", "modrinth", {"project_id": "Vebnzrzj"})
    [row] = manager.check_all()["overlaps"]
    assert row["say"] == "Keep LuckPerms, turn off GroupManager"


def test_an_id_alone_never_matches_a_jar_the_app_installed_from_a_site(manager, monkeypatch):  # noqa: F811
    monkeypatch.setattr(notes, "SAME_JOB", [notes._same_job(PERMISSIONS)])
    make_jar(folder(manager) / "luckperms.jar", "luckperms")
    make_jar(folder(manager) / "gm.jar", "groupmanager")
    manager.remember("groupmanager", "modrinth", {"project_id": "SomethingElse"})
    assert manager.check_all()["overlaps"] == []


def test_a_jar_listed_by_its_id_alone_cant_be_the_usual_pick():
    with pytest.raises(notes.NotesRefused):
        notes._same_job({**PERMISSIONS, "usual_pick": "groupmanager"})


# ---------------------------------------------------------------- the site says
def test_a_site_saying_two_mods_dont_work_together_is_flagged(manager):  # noqa: F811
    make_jar(folder(manager) / "a.jar", "alpha", name="Alpha")
    make_jar(folder(manager) / "b.jar", "beta", name="Beta")
    manager.remember(
        "alpha",
        "modrinth",
        {"project_id": "AAAA", "dependencies": [{"project_id": "BBBB", "type": "incompatible"}]},
    )
    manager.remember("beta", "modrinth", {"project_id": "BBBB", "dependencies": []})
    assert manager.record_of(manager.find("a.jar"))["incompatible"] == ["BBBB"]
    [row] = manager.check_all()["overlaps"]
    assert row["kind"] == "site_says"
    assert row["why"] == "Modrinth says Alpha doesn't work with Beta."
    assert row["say"] == overlap.NO_PICK


def test_two_on_one_same_job_list_get_one_row_even_when_the_site_flags_them_too(
    manager,  # noqa: F811
    maps,
):
    make_jar(folder(manager) / "bluemap.jar", "bluemap")
    make_jar(folder(manager) / "dynmap.jar", "dynmap")
    make_jar(folder(manager) / "squaremap.jar", "squaremap")
    manager.remember(
        "bluemap",
        "modrinth",
        {
            "project_id": "swbUV1cr",
            "dependencies": [{"project_id": "fRQREgAc", "type": "incompatible"}],
        },
    )
    manager.remember("dynmap", "modrinth", {"project_id": "fRQREgAc"})
    assert [r["kind"] for r in manager.check_all()["overlaps"]] == ["same_job"]


# ---------------------------------------------------------------- Overview
def test_the_overview_lists_them_with_open_mods():
    row = {
        "kind": "same_mod",
        "why": "Sodium is here 2 times.",
        "keep": "b.jar",
        "say": "Keep 0.5.2, turn off 0.5.1",
        "mods": [
            {"name": "Sodium", "filename": "a.jar"},
            {"name": "Sodium", "filename": "b.jar"},
        ],
    }
    found = recommendations.overlapping_mods(facts(overlaps=[row]))
    assert found.title == "Sodium is here twice"
    assert found.evidence == "Keep 0.5.2, turn off 0.5.1"
    assert found.action == {"label": "Open mods", "page": "mods"}
    assert recommendations.overlapping_mods(facts()) is None


# ---------------------------------------------------------------- the notes file
def test_same_job_groups_come_from_the_notes_file(monkeypatch):
    monkeypatch.setattr(notes, "SAME_JOB", [])
    private, public = signing.new_key()
    document, signature = signed(notes_body(same_job=[MAPS]), private)
    found = notes.check(document, signature, [public], 0, NOW, "0.9.0")
    holder = notes.SafetyNotes.__new__(notes.SafetyNotes)
    holder._apply(found)
    assert [g["why"] for g in notes.same_job("paper")] == [MAPS["why"]]
    assert notes.same_job("forge") == []


def test_the_shipped_notes_file_has_same_job_groups(monkeypatch):
    monkeypatch.setattr(notes, "SAME_JOB", [])
    monkeypatch.setattr(notes, "SHIPPED", Path(__file__).parents[1] / "data" / "catalog.json")
    notes.load_shipped()
    assert notes.same_job("paper") and notes.same_job("fabric")


@pytest.mark.parametrize(
    "bad",
    [
        {"addons": MAPS["addons"][:1]},  # one isn't a group
        {"addons": MAPS["addons"] * 4},  # over 10
        {"addons": [{"project": "../x", "ids": ["x"]}, *MAPS["addons"]]},
        {"addons": [{"project": "owner/repo/1", "ids": ["x"]}, *MAPS["addons"]]},
        {"addons": [{"project": "x", "ids": []}, *MAPS["addons"]]},
        {"addons": [{"project": "x", "ids": ["<b>"]}, *MAPS["addons"]]},
        {"types": ["bedrock"]},
        {"types": []},
        {"why": "<b>bold</b>"},
        {"why": "x" * 121},
        {"usual_pick": "not-in-the-group"},
        {"addons": [{"ids": ["groupmanager"]}, {"ids": ["permissionsex"]}]},  # no site add-on
        {"addons": [{"project": "", "ids": ["x"]}, *MAPS["addons"]]},
    ],
)
def test_a_bad_same_job_group_refuses_the_whole_file(bad):
    private, public = signing.new_key()
    document, signature = signed(notes_body(same_job=[{**MAPS, **bad}]), private)
    with pytest.raises(notes.NotesRefused):
        notes.check(document, signature, [public], 0, NOW, "0.9.0")


# ---------------------------------------------------------------- older installs
async def test_mods_installed_before_get_what_modrinth_says_in_one_call(manager):  # noqa: F811
    from .test_mod_fit import FakeModrinth, sha1_of, use, version

    alpha = make_jar(folder(manager) / "a.jar", "alpha", name="Alpha")
    make_jar(folder(manager) / "b.jar", "beta", name="Beta")
    manager.db.set_setting("mod_source:alpha", {"source": "modrinth", "modrinth_project": "AAAA"})
    manager.db.set_setting("mod_source:beta", {"source": "modrinth", "modrinth_project": "BBBB"})
    fake = use(
        manager,
        FakeModrinth(now={sha1_of(alpha): version("a1", "1.0", deps=[("BBBB", "incompatible")])}),
    )
    await fit.catch_up(manager)
    assert [path for path, _ in fake.asked] == ["/v2/version_files"]
    assert manager.record_of(manager.find("a.jar"))["incompatible"] == ["BBBB"]
    assert manager.record_of(manager.find("b.jar"))["incompatible"] == []
    [row] = manager.check_all()["overlaps"]
    assert row["why"] == "Modrinth says Alpha doesn't work with Beta."

    await fit.catch_up(manager)  # asked once, never again
    assert len(fake.asked) == 1
