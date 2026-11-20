"""The desktop app's Mods page (the installed list, Modrinth
search, missing mods, Update all, modpacks, files from this PC, a Bedrock
server's add-ons and drag and drop), driven like a person would against the
real agent with the fake Minecraft servers. Modrinth is the fake from
test_dependency_resolution, whose downloads are real jar files."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from .conftest import PASSWORD, LiveAgent, build_bedrock_config
from .fake_pc import FakePC
from .test_bedrock import manifest, mcpack
from .test_dependency_resolution import FakeModrinth
from .test_desktop_app import (
    button,
    open_server,
    open_window,
    owner,
    page_tabs,
    shown_text,
    sign_in,
    signed_in_on,
    wait_for,
)
from .test_desktop_backups import asked, menu_item, undo_titles
from .test_desktop_pages import buttons_named, core_of, open_page, running
from .test_mods import make_jar
from .test_phone_content import PROJECTS, BrokenModrinth

QtCore = pytest.importorskip("PySide6.QtCore", reason="PySide6 is in requirements-dev")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


def mods_folder(agent_http, server_id="survival"):
    return core_of(agent_http).servers[server_id].config.mods_dir


def with_mods(agent_http, modrinth=None):
    """Alpha (on), Beta (turned off) and Gamma, all 1.0, and Modrinth with
    Alpha and Beta 2.0 and Gamma 1.0."""
    folder = mods_folder(agent_http)
    folder.mkdir(exist_ok=True)
    make_jar(folder / "alpha.jar", "alpha", version="1.0", name="Alpha")
    make_jar(folder / "beta.jar.disabled", "beta", version="1.0", name="Beta")
    make_jar(folder / "gamma.jar", "gamma", version="1.0", name="Gamma")
    use(agent_http, modrinth or FakeModrinth(PROJECTS))
    return folder


def use(agent_http, modrinth):
    core_of(agent_http).servers["survival"].mods.modrinth = modrinth
    return modrinth


def mods_page(qapp, agent_http, pc=None):
    window = signed_in_on(qapp, agent_http, pc=pc)
    page = open_page(qapp, window, "Mods")
    wait_for(qapp, lambda: page.data)
    return window, page


def row_buttons(page, words, mod) -> list:
    return [
        b
        for b in page.findChildren(QtWidgets.QPushButton)
        if b.isVisibleTo(page) and b.accessibleName() == f"{words}: {mod}"
    ]


def row_button(page, words, mod):
    found = row_buttons(page, words, mod)
    assert found, f"no {words!r} button for {mod}"
    return found[0]


def can(page, words, mod) -> bool:
    """A row's button is shown and may be pressed (rows are rebuilt as the
    agent answers, so it can be missing for a moment)."""
    return any(b.isEnabled() for b in row_buttons(page, words, mod))


def ticks(window, pick=None) -> list[tuple[str, str, list]]:
    """Answers the window's ticked lists (all as shown, or the ``pick``
    indexes), and records them as (title, body, choices)."""
    seen: list[tuple[str, str, list]] = []

    def tick(title, body, choices, yes):
        seen.append((title, body, choices))
        return pick if pick is not None else [i for i, (_w, on) in enumerate(choices) if on]

    window.tick = tick
    return seen


def drop(qapp, window, paths) -> bool:
    """Drops local files on the window, as Explorer does; True when taken."""
    data = QtCore.QMimeData()
    data.setUrls([QtCore.QUrl.fromLocalFile(str(p)) for p in paths])
    where = QtCore.QPointF(50, 50)
    actions = QtCore.Qt.DropAction.CopyAction
    buttons, keys = QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier
    from PySide6.QtGui import QDragEnterEvent, QDropEvent

    enter = QDragEnterEvent(where.toPoint(), actions, data, buttons, keys)
    window.dragEnterEvent(enter)
    if not enter.isAccepted():
        return False
    window.dropEvent(QDropEvent(where, actions, data, buttons, keys))
    qapp.processEvents()
    return True


# ---------------------------------------------------------------- the page
def test_the_tab_is_named_by_type_and_hidden_for_vanilla(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    assert "Mods" in page_tabs(window)
    config = core_of(agent_http).servers["survival"].config
    config.set("server.type", "paper")
    window.refresh()
    wait_for(qapp, lambda: "Plugins" in page_tabs(window) and "Mods" not in page_tabs(window))
    config.set("server.type", "vanilla")
    window.refresh()
    wait_for(qapp, lambda: "Plugins" not in page_tabs(window) and "Mods" not in page_tabs(window))
    window.load_server_parts()
    overview = window.server_screen.simple_overview
    assert not overview.mods_card.isVisibleTo(window)
    world = overview.cards[3]  # alone on the last row, so it takes the whole row
    assert overview.grid.getItemPosition(overview.grid.indexOf(world)) == (1, 0, 1, 2)


def test_the_list_shows_each_mod_and_changes_wait_until_the_server_stops(qapp, agent_http):
    with_mods(agent_http)
    running(qapp, agent_http)
    window, page = mods_page(qapp, agent_http)
    # The rows are shown on the next turn of the event loop, after the count.
    wait_for(qapp, lambda: "3 mods installed" in shown_text(page) and "Alpha" in shown_text(page))
    text = shown_text(page)
    assert "Alpha" in text and "Beta" in text and "Off" in text and "Working" in text
    assert "Stop the server first" in text
    assert not row_button(page, "Turn off", "Alpha").isEnabled()
    assert not button(page, "Update all").isEnabled()
    button(page, "Stop").click()
    wait_for(qapp, lambda: can(page, "Turn off", "Alpha"), 30)
    assert "Stop the server first" not in shown_text(page)
    window.set_mode(True)  # Technical words, and the mod ids
    wait_for(qapp, lambda: "Change history" in shown_text(page) and "alpha" in shown_text(page))


def test_turn_off_has_an_undo_that_turns_it_back_on(qapp, agent_http):
    folder = with_mods(agent_http)
    window, page = mods_page(qapp, agent_http)
    wait_for(qapp, lambda: can(page, "Turn off", "Alpha"))
    row_button(page, "Turn off", "Alpha").click()
    wait_for(qapp, lambda: "Alpha turned off" in page.note.text())
    assert (folder / "alpha.jar.disabled").exists()
    page.note.undo.click()
    wait_for(qapp, lambda: (folder / "alpha.jar").exists(), 20)
    wait_for(qapp, lambda: "Alpha turned on" in page.note.text())


def test_an_update_asks_and_undo_puts_the_old_version_back(qapp, agent_http):
    with_mods(agent_http)
    headers = owner(agent_http)
    agent_http.get("/api/servers/survival/mods/update-all", headers=headers)  # saves what's newer
    window, page = mods_page(qapp, agent_http)
    questions = asked(window)
    wait_for(qapp, lambda: "Update: 2.0" in shown_text(page))
    row_button(page, "Update", "Alpha").click()
    wait_for(qapp, lambda: "Updated to 2.0" in page.note.text(), 30)
    assert questions[0] == (
        "Update Alpha?",
        "1.0 → 2.0\n\nThe old version is kept so you can go back.",
    )
    page.note.undo.click()
    wait_for(qapp, lambda: "Now on 1.0" in page.note.text(), 30)


def test_remove_says_what_needs_it_first(qapp, agent_http):
    folder = with_mods(agent_http)
    make_jar(folder / "needs.jar", "needs", depends={"alpha": "*"}, name="Needs Alpha")
    window, page = mods_page(qapp, agent_http)
    questions = asked(window)
    wait_for(qapp, lambda: can(page, "Turn off", "Alpha"))
    menu_item(page, "Remove").trigger()  # the first row's menu: Alpha
    wait_for(qapp, lambda: "Alpha removed" in page.note.text())
    assert questions[0][0] == "Remove Alpha?" and "Needs Alpha needs it" in questions[0][1]
    assert not (folder / "alpha.jar").exists()


def test_versions_goes_back_to_a_kept_one(qapp, agent_http):
    with_mods(agent_http)
    headers = owner(agent_http)
    agent_http.post(
        "/api/servers/survival/mods/update",
        json={"filename": "alpha.jar", "version_id": "alpha-2.0"},
        headers=headers,
        timeout=30,
    )
    window, page = mods_page(qapp, agent_http)
    picked = []
    window.pick = lambda title, body, choices: picked.append((title, choices)) or 0
    wait_for(qapp, lambda: "2.0" in shown_text(page))
    menu_item(page, "Versions").trigger()
    wait_for(qapp, lambda: "Now on 1.0" in page.note.text(), 30)
    assert picked[0][0] == "Versions of Alpha" and picked[0][1][0].startswith("1.0")


# ---------------------------------------------------------------- Modrinth
def test_search_and_install_with_the_mods_it_needs(qapp, agent_http):
    projects = {
        "lithium": {
            "project_id": "LITH0001",
            "title": "Lithium",
            "versions": [("0.13", ["CLOUD001"])],
        },
        "cloud": {"project_id": "CLOUD001", "title": "Cloud", "versions": [("2.0", [])]},
    }
    mods_folder(agent_http).mkdir(exist_ok=True)
    modrinth = use(agent_http, FakeModrinth(projects))
    window, page = mods_page(qapp, agent_http)
    seen = ticks(window)
    page.search_box.setText("lith")
    button(page, "Search").click()
    wait_for(qapp, lambda: "Lithium" in shown_text(page))
    wait_for(qapp, lambda: can(page, "Install", "Lithium"))
    row_button(page, "Install", "Lithium").click()
    wait_for(qapp, lambda: "Lithium 0.13 installed" in page.note.text(), 30)
    title, body, choices = seen[0]
    assert title == "Install mod?" and "Lithium 0.13" in body
    assert choices == [("Also install the 1 needed mod", True)]
    assert modrinth.downloads == ["lithium"]


def test_missing_mods_are_listed_and_installed_with_one_click(qapp, agent_http):
    folder = mods_folder(agent_http)
    folder.mkdir(exist_ok=True)
    (folder.parent / "libraries").mkdir(exist_ok=True)  # the server has started once
    make_jar(folder / "needs.jar", "needs", depends={"cloud": "*"}, name="Needs Cloud")
    use(
        agent_http,
        FakeModrinth(
            {"cloud": {"project_id": "CLOUD001", "title": "Cloud", "versions": [("2.0", [])]}}
        ),
    )
    window, page = mods_page(qapp, agent_http)
    questions = asked(window)
    wait_for(
        qapp,
        lambda: (
            "1 needed mod is missing" in shown_text(page)
            and "Needed by Needs Cloud" in shown_text(page)
        ),
    )
    button(page, "Install missing (1)").click()
    wait_for(qapp, lambda: "Installed Cloud" in page.note.text(), 30)
    assert "Cloud 2.0" in questions[0][1]
    wait_for(qapp, lambda: "Every mod has what it needs" in shown_text(page), 20)


# ---------------------------------------------------------------- Update all
def test_update_all_shows_a_ticked_list_and_is_one_change_with_one_undo(qapp, agent_http):
    with_mods(agent_http)
    window, page = mods_page(qapp, agent_http)
    seen = ticks(window)
    wait_for(qapp, lambda: button(page, "Update all").isEnabled())
    button(page, "Update all").click()
    wait_for(qapp, lambda: "Updated 1." in page.note.text(), 60)
    title, _body, choices = seen[0]
    assert title == "Update all"
    assert choices == [
        ("Alpha   1.0 → 2.0", True),
        ("Beta   1.0 → 2.0   (Turned off, so not ticked)", False),
    ]
    assert page.note.undo.isVisible()  # the header's Undo: one entry for all of it
    headers = owner(agent_http)
    assert undo_titles(agent_http, headers)[0] == "Update all"
    wait_for(qapp, lambda: window.server_screen.header.undo.isVisible())


def test_a_failed_update_all_names_the_mod_and_changes_nothing(qapp, agent_http):
    folder = with_mods(agent_http, BrokenModrinth(PROJECTS, broken={"beta"}))
    window, page = mods_page(qapp, agent_http)
    ticks(window, pick=[0, 1])
    wait_for(qapp, lambda: button(page, "Update all").isEnabled())
    button(page, "Update all").click()
    wait_for(qapp, lambda: "Updating Beta failed" in page.note.text(), 60)
    assert (folder / "alpha.jar").exists() and not list(folder.glob("alpha-2.0*"))


def test_update_all_says_when_everything_is_up_to_date(qapp, agent_http):
    with_mods(
        agent_http, FakeModrinth({k: dict(v, versions=[("1.0", [])]) for k, v in PROJECTS.items()})
    )
    window, page = mods_page(qapp, agent_http)
    seen = ticks(window)
    wait_for(qapp, lambda: button(page, "Update all").isEnabled())
    button(page, "Update all").click()
    wait_for(qapp, lambda: "Everything is up to date" in page.note.text(), 30)
    assert seen == []


# ---------------------------------------------------------------- files from this PC
def test_add_a_file_and_drop_jar_files_on_the_window(qapp, agent_http, tmp_path):
    folder = with_mods(agent_http)
    pc = FakePC()
    window, page = mods_page(qapp, agent_http, pc)
    asked = []
    window.confirm = lambda title, *words: asked.append(title) or True  # no site lists these jars
    wait_for(qapp, lambda: button(page, "Add a file").isEnabled())
    pc.picked = str(make_jar(tmp_path / "one.jar", "one", name="One"))
    button(page, "Add a file").click()
    wait_for(qapp, lambda: "Added One" in page.note.text(), 20)
    assert (folder / "one.jar").exists() and asked

    two, three = (
        make_jar(tmp_path / "two.jar", "two", name="Two"),
        make_jar(tmp_path / "three.jar", "three", name="Three"),
    )
    window.open_page("Overview")
    assert drop(qapp, window, [two, three])
    assert window.server_screen.current_page == "Mods"
    wait_for(qapp, lambda: "Added Two, Three" in page.note.text(), 20)
    four, bad = make_jar(tmp_path / "four.jar", "four", name="Four"), tmp_path / "bad.jar"
    bad.write_bytes(b"not a zip")
    assert drop(qapp, window, [four, bad])  # the one before a refused file stays added
    wait_for(qapp, lambda: "Added Four" in page.note.text() and "bad.jar:" in page.note.text(), 20)
    notes = tmp_path / "notes.txt"
    notes.write_text("hi")
    assert not drop(qapp, window, [two, notes])  # not all add-ons: nothing taken


def test_a_modpack_is_read_first_then_imported(qapp, agent_http, tmp_path, monkeypatch):
    from agent import modpack

    from .test_modpack import make_pack

    with_mods(agent_http)
    imported = []

    async def import_into(ctx, token, user="system", own_jars=False):
        imported.append((token, own_jars))
        return {
            "job_id": "j1",
            "modpack": {"name": "Test Pack"},
            "version_change": False,
            "undo": None,
        }

    monkeypatch.setattr(modpack, "import_into", import_into)
    pc = FakePC()
    window, page = mods_page(qapp, agent_http, pc)
    questions = asked(window)
    pc.picked = str(make_pack(tmp_path / "pack.mrpack"))
    button(page, "Choose a modpack file").click()
    wait_for(qapp, lambda: "Imported Test Pack" in page.note.text(), 30)
    title, body = questions[0]
    assert title == "Import Test Pack?"
    assert "Skipped, only for the game itself: sodium.jar" in body
    assert "A backup is taken and checked first" in body
    assert len(imported) == 1 and imported[0][1] is False

    # Dropped on the window: the same question; No throws the upload away.
    questions = asked(window, answer=False)
    assert drop(qapp, window, [tmp_path / "pack.mrpack"])
    wait_for(qapp, lambda: questions)
    assert len(imported) == 1

    # Own jars are in the question, and Import is the yes to them.
    questions = asked(window)
    own = {"overrides/mods/our-tweaks.jar": b"PK own"}
    pc.picked = str(make_pack(tmp_path / "own.mrpack", overrides=own))
    page.note.say("")
    button(page, "Choose a modpack file").click()
    wait_for(qapp, lambda: "Imported Test Pack" in page.note.text(), 30)
    assert "Not checked by a site: mods/our-tweaks.jar" in questions[0][1]
    assert imported[-1][1] is True


# ---------------------------------------------------------------- Bedrock add-ons
@pytest.fixture
def bedrock_http(tmp_path, monkeypatch):
    """The real agent with a fake Bedrock server and a Java one."""
    from agent.security.auth import hash_password

    monkeypatch.setenv("MCSC_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MCSC_ADMIN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))
    monkeypatch.delenv("MCSC_API_TOKEN", raising=False)
    config = build_bedrock_config(
        tmp_path, java=True, **{"security.rate_limit_requests": 10_000, "tls.enabled": False}
    )
    agent = LiveAgent(config)
    agent.start()
    with httpx.Client(base_url=f"http://127.0.0.1:{agent.port}", timeout=15) as client:
        client.agent = agent  # type: ignore[attr-defined]
        yield client
    agent.stop()


def test_a_bedrock_server_has_add_ons_to_add_turn_off_and_remove(qapp, bedrock_http, tmp_path):
    pc = FakePC()
    window = open_window(qapp, bedrock_http, pc)
    sign_in(qapp, window)
    wait_for(qapp, lambda: window.screen_name == "All servers" and "Bedrock" in shown_text(window))
    open_server(qapp, window, "Bedrock")
    assert "Add-ons" in page_tabs(window) and "Mods" not in page_tabs(window)
    page = open_page(qapp, window, "Add-ons")
    wait_for(qapp, lambda: "No add-ons yet" in shown_text(page))
    pack = tmp_path / "mobs.mcpack"
    pack.write_bytes(mcpack(manifest()))
    pc.picked = str(pack)
    button(page, "Choose a file").click()
    wait_for(qapp, lambda: "Installed Better Mobs" in page.note.text(), 20)
    wait_for(qapp, lambda: "1 add-on" in shown_text(page) and can(page, "Turn off", "Better Mobs"))
    row_button(page, "Turn off", "Better Mobs").click()
    wait_for(qapp, lambda: "Better Mobs is off" in page.note.text())
    wait_for(qapp, lambda: can(page, "Remove", "Better Mobs"))
    questions = asked(window)
    row_button(page, "Remove", "Better Mobs").click()
    wait_for(qapp, lambda: "Better Mobs was removed" in page.note.text())
    assert questions[0][0] == "Remove Better Mobs?"

    window.open_page("Overview")
    second = tmp_path / "second.mcpack"
    second.write_bytes(mcpack(manifest(name="Second")))
    assert drop(qapp, window, [second])
    wait_for(qapp, lambda: "Installed Second" in page.note.text(), 20)
    assert not drop(qapp, window, [make_jar(tmp_path / "a.jar", "a")])  # no mods on Bedrock


def test_a_moderator_without_mods_rights_sees_no_change_buttons(qapp, agent_http):
    with_mods(agent_http)
    made = agent_http.post(
        "/api/accounts",
        json={
            "username": "sam",
            "password": "moderator password",
            "servers": ["survival"],
            "permissions": ["server.view"],
        },
        headers=owner(agent_http),
    )
    assert made.status_code == 200, made.text
    window = open_window(qapp, agent_http)
    sign_in(qapp, window, "sam", "moderator password")
    wait_for(qapp, lambda: window.screen_name == "All servers" and "Survival" in shown_text(window))
    open_server(qapp, window, "Survival")
    page = open_page(qapp, window, "Mods")
    # once /auth/me has said what sam may do and the page has loaded again
    wait_for(qapp, lambda: window.role is not None and not window._tasks)
    wait_for(qapp, lambda: "3 mods installed" in shown_text(page))
    assert not buttons_named(page, "Turn off") and not buttons_named(page, "Update all")
    assert not page.search_card.isVisibleTo(page)


def test_a_datapack_drop_and_a_github_link_are_marked_not_checked(
    qapp, agent_http, tmp_path, monkeypatch
):
    from agent import downloads

    from .test_hangar_and_links import DATAPACK, FakeSites

    class FabricRelease(FakeSites):
        def asset_bytes(self, name):
            return (tmp_path / "tree.jar").read_bytes()

    make_jar(tmp_path / "tree.jar", "treeplugin", "1.2", name="Trees")
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(FabricRelease()))
    folder = with_mods(agent_http)
    window, page = mods_page(qapp, agent_http)
    wait_for(qapp, lambda: page.read.isEnabled())
    pack = tmp_path / "trees.zip"
    pack.write_bytes(DATAPACK)
    window.open_page("Overview")
    assert drop(qapp, window, [pack])
    wait_for(qapp, lambda: "Added the datapack trees" in page.note.text(), 20)
    assert "Not checked by Modrinth" in page.note.text()
    world = core_of(agent_http).servers["survival"].config.server_dir / "world"
    assert (world / "datapacks" / "trees.zip").is_file()

    page.link_box.setText("https://www.planetminecraft.com/data-pack/trees/")
    page.read.click()
    wait_for(qapp, lambda: "Download it in your browser" in page.note.text(), 20)

    picks = []
    window.pick = lambda title, body, choices: picks.append((title, choices)) or 0
    window.confirm = lambda *words: True  # no site lists this jar: asked first
    page.link_box.setText("https://github.com/tree-maker/trees/releases/tag/v1.2")
    page.read.click()
    wait_for(qapp, lambda: "Added Trees" in page.note.text(), 20)
    assert picks[0][0] == "Which file from Trees v1.2?"
    assert picks[0][1][0].startswith("TreePlugin-1.2.jar")
    assert (folder / "TreePlugin-1.2.jar").is_file()
    wait_for(qapp, lambda: "Not checked by Modrinth" in shown_text(page))


def test_a_paper_server_searches_hangar_too_and_names_each_site(qapp, agent_http, monkeypatch):
    from agent import downloads

    from .test_hangar_and_links import FakeSites, PluginModrinth

    sites = FakeSites()
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(sites))
    ctx = core_of(agent_http).servers["survival"]
    ctx.config.set("server.type", "paper")
    use(agent_http, PluginModrinth())
    window = signed_in_on(qapp, agent_http)
    window.refresh()
    wait_for(qapp, lambda: "Plugins" in page_tabs(window))
    page = open_page(qapp, window, "Mods")
    wait_for(qapp, lambda: page.data)
    assert "Add from Modrinth or Hangar" in shown_text(page)
    page.search_box.setText("via")
    page.search()
    wait_for(qapp, lambda: "1,031 downloads on Hangar" in shown_text(page), 20)
    sites.down.add("hangar.papermc.io")
    page.search_box.setText("essentials")
    page.search()
    wait_for(qapp, lambda: "Hangar isn't available right now." in shown_text(page), 20)
    assert "EssentialsX" in shown_text(page)


def test_curseforge_results_needs_you_and_the_sha1_label(qapp, agent_http, monkeypatch):
    from agent import downloads

    from .test_curseforge import FakeCurseForge

    relay = FakeCurseForge()
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(relay))
    folder = mods_folder(agent_http)
    folder.mkdir(exist_ok=True)
    (folder / "lithium.jar").write_bytes(relay.jar(5001))
    mods = core_of(agent_http).servers["survival"].mods
    mods.db.set_setting(
        "mod_source:lithium", {"source": "curseforge", "project": "curseforge:394468"}
    )
    mods.need_you(
        {
            "project": "curseforge:333",
            "name": "Secret Mod",
            "filename": "secret-1.0.jar",
            "page": "https://www.curseforge.com/minecraft/mc-mods/m333",
        }
    )
    use(agent_http, FakeModrinth({}))
    pc = FakePC()
    window, page = mods_page(qapp, agent_http, pc=pc)
    assert "Add from Modrinth or CurseForge" in shown_text(page)
    wait_for(qapp, lambda: "Checked (sha1), a weaker check than Modrinth's" in shown_text(page))
    assert "Needs you" in shown_text(page) and "The file: secret-1.0.jar" in shown_text(page)
    row_button(page, "Open on CurseForge", "Secret Mod").click()
    assert pc.opened == ["https://www.curseforge.com/minecraft/mc-mods/m333"]

    page.search_box.setText("lithium")
    page.search()
    wait_for(qapp, lambda: "downloads on CurseForge" in shown_text(page), 20)
    page.search_box.setText("secret")
    page.search()
    wait_for(qapp, lambda: "doesn't let other apps download it" in shown_text(page), 20)
    assert not row_buttons(page, "Install", "Secret Mod"), "never a download for it"

    row_button(page, "Remove from the list", "Secret Mod").click()
    wait_for(qapp, lambda: not page.needs_you_card.isVisibleTo(page), 20)
    assert mods.needs_you() == []


FOUND = {
    "type": "fabric",
    "minecraft_version": "1.21.1",
    "problem": None,
    "read_at": 1793422800.0,
    "stale": None,
    "good_to_have": [
        {
            "name": "Chunky",
            "project": "chunky",
            "description": "Pre-generates chunks.",
            "kind": "good_to_have",
            "group": "starter",
            "changes_gameplay": False,
            "technical_only": False,
            "place": "server",
            "release_type": "release",
            "downloads": {"count": 1234, "source": "Modrinth", "read_at": 1793422800.0},
        }
    ],
    "often_used_with": [],
    "performance": [
        {"kind": "settings", "name": "Lower view and simulation distance", "values": {}},
        {"kind": "performance", "name": "Lithium", "project": "lithium"},
    ],
    "performance_reason": {"why": "lag", "evidence": "TPS was under 18 in 40 of 60 readings."},
    "for_your_players": [
        {"name": "Sodium", "page": "https://modrinth.com/mod/sodium", "also_on_server": False}
    ],
    "maps": [
        {
            "id": "bluemap",
            "name": "BlueMap",
            "port": 8100,
            "source": "config/bluemap/webserver.conf",
            "link": "http://100.64.0.9:8100/",
        }
    ],
}


def test_suggestions_map_link_and_speed_ups_are_shown(qapp, agent_http, monkeypatch):
    from agent import suggestions

    async def lists(ctx, technical):
        return FOUND

    monkeypatch.setattr(suggestions, "lists", lists)
    with_mods(agent_http)
    pc = FakePC()
    window, page = mods_page(qapp, agent_http, pc)
    wait_for(qapp, lambda: page.players_card.isVisibleTo(page))
    assert "Sodium" in shown_text(page.players_card)
    assert not page.ideas_card.isVisibleTo(page)  # the lists are Technical only
    # Qt shows a button added to a shown page on the next turn of the event loop.
    wait_for(qapp, lambda: buttons_named(page, "Open the BlueMap map"))
    buttons_named(page, "Open the BlueMap map")[0].click()
    assert pc.opened == ["http://100.64.0.9:8100/"]

    button(window, "Technical").click()
    page = open_page(qapp, window, "Mods")
    # The card can show its heading before the lists arrive.
    wait_for(
        qapp,
        lambda: page.ideas_card.isVisibleTo(page) and "Chunky" in shown_text(page.ideas_card),
    )
    assert "1,234 downloads (Modrinth" in shown_text(page.ideas_card)

    page = open_page(qapp, window, "Performance")
    wait_for(qapp, lambda: "TPS was under 18" in shown_text(page))
    assert buttons_named(page, "Apply") and buttons_named(page, "Add")


def test_speed_ups_need_a_measured_reason(qapp, agent_http, monkeypatch):
    from agent import suggestions

    async def lists(ctx, technical):
        return {**FOUND, "performance": [], "performance_reason": None}

    monkeypatch.setattr(suggestions, "lists", lists)
    window = signed_in_on(qapp, agent_http)
    button(window, "Technical").click()
    page = open_page(qapp, window, "Performance")
    wait_for(qapp, lambda: "No measured lag" in shown_text(page))
    assert not buttons_named(page, "Add")


# ---------------------------------------------------------------- guides and Edit settings
def plugin_server(agent_http):
    """Survival as a Paper server with Chunky and its settings file."""
    from .test_guides import plugin_jar

    ctx = core_of(agent_http).servers["survival"]
    ctx.config.set("server.type", "paper")
    plugin_jar(ctx.config.mods_dir, "Chunky", "1.4.40")
    (ctx.config.mods_dir / "Chunky").mkdir()
    (ctx.config.mods_dir / "Chunky" / "config.yml").write_text("language: en\n", encoding="utf-8")
    return ctx


def dialog_of(qapp, kind):
    from desktop.editor import FileEditor, GuideWindow

    wanted = {"guide": GuideWindow, "file": FileEditor}[kind]
    found: list = []
    wait_for(
        qapp,
        lambda: (
            found.extend(
                w for w in qapp.topLevelWidgets() if isinstance(w, wanted) and w.isVisible()
            )
            or found
        ),
    )
    return found[0]


def test_a_guide_button_sends_its_command_and_shows_the_console_answer(qapp, agent_http):
    plugin_server(agent_http)
    running(qapp, agent_http)
    window = signed_in_on(qapp, agent_http)
    window.refresh()
    page = open_page(qapp, window, "Mods")
    wait_for(qapp, lambda: row_buttons(page, "Guide", "Chunky"))
    row_button(page, "Guide", "Chunky").click()
    guide = dialog_of(qapp, "guide")
    assert "Loads the world ahead of time" in shown_text(guide)
    assert "chunky progress" not in shown_text(guide)  # Simple: no raw commands
    asked_titles: list = []
    window.confirm = lambda title, body, yes: asked_titles.append(title) or False
    start = [
        b for b in guide.findChildren(QtWidgets.QPushButton) if b.text() == "Load the world ahead"
    ]
    start[0].click()
    assert asked_titles == ["Load the world ahead"]  # it changes the world, so it asked
    button(guide, "Show progress").click()
    wait_for(qapp, lambda: "[Console] chunky progress" in shown_text(guide), 20, 0.1)
    guide.close()


def test_edit_settings_refuses_broken_yaml_on_its_line_then_saves_with_undo(qapp, agent_http):
    ctx = plugin_server(agent_http)
    window = signed_in_on(qapp, agent_http)
    window.refresh()
    page = open_page(qapp, window, "Mods")
    wait_for(qapp, lambda: row_buttons(page, "Guide", "Chunky"))
    menu_item(page, "Edit settings").trigger()
    editor = dialog_of(qapp, "file")
    assert editor.box.toPlainText() == "language: en\n"
    assert "Careful" in shown_text(editor)
    editor.box.setPlainText("language: en\n  bad: [\n")
    editor.save.click()
    wait_for(qapp, lambda: editor.problem.isVisible())
    assert editor.box.textCursor().blockNumber() in (1, 2)
    editor.box.setPlainText("language: de\n")
    editor.save.click()
    wait_for(qapp, lambda: not editor.isVisible())
    path = ctx.config.mods_dir / "Chunky" / "config.yml"
    assert path.read_text(encoding="utf-8") == "language: de\n"
    wait_for(qapp, lambda: "Undo puts the old one back" in page.note.text())


def test_the_settings_page_lists_the_folders_other_config_files(qapp, agent_http):
    ctx = plugin_server(agent_http)
    (ctx.config.server_dir / "bukkit.yml").write_text("a: 1\n", encoding="utf-8")
    window = signed_in_on(qapp, agent_http)
    page = open_page(qapp, window, "Settings")
    wait_for(qapp, lambda: page.findChildren(QtWidgets.QListWidget))
    listed = page.findChildren(QtWidgets.QListWidget)[0]
    names = [listed.item(i).text() for i in range(listed.count())]
    assert "bukkit.yml" in names and "plugins/Chunky/config.yml" in names
    assert "server.properties" not in names


def test_still_fit_card_changes_the_ticked_ones_as_one_update_all(qapp, agent_http):
    with_mods(agent_http)
    core_of(agent_http).servers["survival"].db.set_setting(
        "mod_fit",
        {
            "about": "Create 6.0",
            "needs_change": [
                {
                    "mod_id": "alpha",
                    "name": "Alpha",
                    "filename": "alpha.jar",
                    "why": "Rails needs Alpha 2.0 or newer",
                    "from": "1.0",
                    "to": "2.0",
                    "version_id": "alpha-2.0",
                },
            ],
            "no_fit": [
                {
                    "mod_id": "gamma",
                    "name": "Gamma",
                    "filename": "gamma.jar",
                    "why": "No version for Minecraft 1.21.1 yet",
                }
            ],
            "cant_tell": [
                {
                    "mod_id": "beta",
                    "name": "Beta",
                    "filename": "beta.jar.disabled",
                    "why": "Can't read its range",
                }
            ],
        },
    )
    window, page = mods_page(qapp, agent_http)
    wait_for(qapp, lambda: "Can't read its range" in shown_text(page))
    shown = shown_text(page)
    assert "1 mod needs a different version to go with Create 6.0." in shown
    assert [t.text() for t, _ in page.fit_ticks] == ["Alpha   1.0 → 2.0"]
    assert "No version for Minecraft 1.21.1 yet" in shown
    assert "Can't tell" in shown and "Can't read its range" in shown
    assert row_button(page, "Turn off", "Gamma")
    wait_for(qapp, lambda: button(page, "Change these").isEnabled())
    button(page, "Change these").click()
    wait_for(qapp, lambda: "Updated 1." in page.note.text(), 60)
    assert undo_titles(agent_http, owner(agent_http))[0] == "Update all"
    assert list(mods_folder(agent_http).glob("alpha-2.0*"))

    button(page, "Hide").click()
    wait_for(qapp, lambda: not page.fit_card.isVisible())


def test_the_same_mod_twice_offers_turn_off_on_the_older_copy(qapp, agent_http):
    folder = with_mods(agent_http)
    make_jar(folder / "alpha-new.jar", "alpha", version="1.1", name="Alpha")
    window, page = mods_page(qapp, agent_http)
    wait_for(qapp, lambda: "Keep 1.1, turn off 1.0" in shown_text(page))
    assert page.same_card.isVisible()
    assert "Alpha is here 2 times." in shown_text(page)
    wait_for(qapp, lambda: can(page, "Turn off", "Alpha 1.0"))
    assert not row_buttons(page, "Turn off", "Alpha 1.1")
    row_button(page, "Turn off", "Alpha 1.0").click()
    wait_for(qapp, lambda: (folder / "alpha.jar.disabled").exists())
    wait_for(qapp, lambda: not page.same_card.isVisible())


def test_a_found_modpack_is_read_from_its_own_site_with_a_site_that_is_down_said():
    from desktop.mods import find_pack

    calls, said, read = [], [], []

    class Window:
        def __init__(self):
            self.agent = self

        def call(self, method, path, body=None, slow=False):
            calls.append((method, path, body))
            if method == "GET":
                hit = {"project_id": "curseforge:900", "title": "Cozy", "source": "curseforge"}
                problems = ["Modrinth isn't available right now."]
                return {"hits": [hit | {"source_name": "CurseForge"}], "problems": problems}
            return {"pack": "read"}

        def run(self, work, done, failed):
            done(work())

        def pick(self, title, body, choices):
            assert choices == ["Cozy  ·  CurseForge"], "no download count when the site gave none"
            return 0

    find_pack(Window(), "cozy pack", "survival", said.append, read.append)
    assert calls[0][1] == "/modpacks/search?q=cozy%20pack&limit=10"
    assert calls[1] == (
        "POST",
        "/modpacks/curseforge",
        {"project": "curseforge:900", "server_id": "survival"},
    )
    assert "Modrinth isn't available right now." in said and read == [{"pack": "read"}]


def test_a_linked_modpack_card_updates_and_unlinks(qapp, agent_http, monkeypatch, tmp_path):
    from agent import linkedpack, modpack

    from .test_modpack import make_pack

    with_mods(agent_http)
    ctx = core_of(agent_http).servers["survival"]
    alpha = mods_folder(agent_http) / "alpha.jar"
    sha256 = hashlib.sha256(alpha.read_bytes()).hexdigest()
    entry = {"path": "mods/alpha.jar", "key": "a" * 128, "project": "A", "sha256": sha256}
    linkedpack._save(
        ctx,
        {
            "site": "modrinth",
            "project": "xyz",
            "version_id": "v1",
            "version": "1.2.0",
            "name": "Test Pack",
            "files": [entry],
            "overrides": {},
        },
    )
    ctx.db.set_setting(
        linkedpack.UPDATE_SETTING,
        {"for": "v1", "version_id": "v2", "version": "1.3.0", "checked_at": 9e12},
    )
    sent = []

    async def preview(ctx, version_id=None):
        sent.append(("preview", version_id))
        return {
            "token": "0" * 32,
            "pack": modpack.inspect(make_pack(tmp_path / "p.mrpack")).to_dict(),
            "changes": {
                "from_version": "1.2.0",
                "to_version": "1.3.0",
                "added": ["ferrite.jar"],
                "removed": [],
                "newer": [{"name": "alpha-2.jar", "from": "alpha.jar", "to": "alpha-2.jar"}],
                "added_by_you": ["gamma.jar"],
                "configs_changed_by_you": ["config/alpha.toml"],
                "new_own_jars": ["mods/our-tweaks.jar"],
                "version_change": False,
                "minecraft_version": "1.21.1",
                "problems": [],
            },
        }

    async def update(ctx, token, use_pack_configs=False, user="system", own_jars=False):
        sent.append(("update", use_pack_configs, own_jars))
        return {
            "job_id": "j1",
            "changes": (await preview(ctx))["changes"],
            "kept_configs": [],
            "version_change": False,
            "undo": None,
        }

    monkeypatch.setattr(linkedpack, "preview", preview)
    monkeypatch.setattr(linkedpack, "update", update)
    window, page = mods_page(qapp, agent_http)
    # The card and the list's rows come from two answers: wait for both.
    wait_for(qapp, lambda: "Version 1.3.0 is out." in shown_text(page))
    wait_for(qapp, lambda: "Added by you" in shown_text(page))
    shown = shown_text(page)
    assert "Made from Test Pack 1.2.0 on Modrinth." in shown
    assert "2 mods were added by you. Modpack update keeps them." in shown
    assert "Added by you" in shown

    seen = ticks(window, pick=[0])
    wait_for(qapp, lambda: button(page, "Modpack update").isEnabled())
    button(page, "Modpack update").click()
    wait_for(qapp, lambda: "Test Pack is now on 1.3.0" in page.note.text(), 30)
    title, body, choices = seen[0]
    assert title == "Update Test Pack to 1.3.0?"
    assert "Newer: alpha.jar → alpha-2.jar" in body and "Added: ferrite.jar" in body
    assert "Kept, added by you: gamma.jar" in body
    assert "Not checked by a site: mods/our-tweaks.jar" in body
    assert choices == [("Use the modpack's config files instead of mine", False)]
    assert sent == [("preview", "v2"), ("update", True, True), ("preview", None)]

    asked(window)
    button(page, "Unlink").click()
    wait_for(qapp, lambda: not page.linked_card.isVisible())
    assert linkedpack.read_link(ctx) is None


# ---------------------------------------------------------------- Server / Players / Both
def row_menu_item(page, mod, words):
    """An item of one mod's ⋯ menu."""
    for more in page.findChildren(QtWidgets.QToolButton):
        if more.isVisibleTo(page) and more.accessibleName().endswith(f": {mod}") and more.menu():
            for action in more.menu().actions():
                if action.text() == words:
                    return action
    raise AssertionError(f"no {words!r} in {mod}'s menu")


def test_marks_move_a_mod_to_players_only_and_back(qapp, agent_http, tmp_path):
    folder = with_mods(agent_http)
    make_jar(folder / "map.jar", "minimap", name="Minimap", environment="client")
    players = core_of(agent_http).servers["survival"].config.server_dir / "player-only"
    pc = FakePC()
    window, page = mods_page(qapp, agent_http, pc)
    window.confirm = lambda *words: True  # no site lists these files
    wait_for(qapp, lambda: "For: Players (from the mod's file)" in shown_text(page))
    assert "For: Both (from the mod's file)" in shown_text(page)
    assert "Players only" in shown_text(page) and "Nothing here yet." in shown_text(page)

    wait_for(qapp, lambda: can(page, "Turn off", "Minimap"))
    row_menu_item(page, "Minimap", "Only players need it").trigger()
    wait_for(qapp, lambda: "Minimap moved to Players only" in page.note.text(), 20)
    assert (players / "map.jar").is_file() and not (folder / "map.jar").exists()
    wait_for(qapp, lambda: can(page, "Move to the server", "Minimap"))

    pack = tmp_path / "faithful.zip"
    import zipfile

    with zipfile.ZipFile(pack, "w") as zf:
        zf.writestr("pack.mcmeta", "{}")
    pc.picked = str(pack)
    button(page, "Add for players").click()
    wait_for(qapp, lambda: "faithful.zip was added for players." in page.note.text(), 20)
    assert (players / "faithful.zip").is_file()

    row_button(page, "Move to the server", "Minimap").click()
    wait_for(qapp, lambda: "Minimap is now marked Both." in page.note.text(), 20)
    assert (folder / "map.jar").is_file()
    wait_for(qapp, lambda: "For: Both (set by you)" in shown_text(page))


def test_player_files_backup_options_and_get_it_again(qapp, agent_http, tmp_path, monkeypatch):
    import zipfile

    from agent import downloads
    from agent.mods import sides

    with_mods(agent_http)
    ctx = core_of(agent_http).servers["survival"]
    pack = tmp_path / "faithful.zip"
    with zipfile.ZipFile(pack, "w") as zf:
        zf.writestr("pack.mcmeta", "{}")
    data = pack.read_bytes()

    async def known(mods, sent):
        return "modrinth", {"project_id": "x"}

    async def file_url(sha512):
        return "https://cdn.modrinth.com/data/x/versions/y/faithful.zip"

    async def fetched(spec):
        return data, None

    monkeypatch.setattr(sides, "known_source", known)
    monkeypatch.setattr(downloads, "download_bytes", fetched)
    ctx.mods.modrinth.file_url = file_url
    pc = FakePC()
    window, page = mods_page(qapp, agent_http, pc)
    pc.picked = str(pack)
    wait_for(qapp, lambda: button(page, "Add for players").isEnabled())
    button(page, "Add for players").click()
    wait_for(qapp, lambda: "faithful.zip was added for players." in page.note.text(), 20)

    wait_for(qapp, lambda: page.back_up_players.isChecked() and page.skip_listed.isVisible())
    page.skip_listed.click()
    wait_for(qapp, lambda: sides.options(ctx)["skip_listed"] is True)

    (ctx.config.server_dir / "player-only" / "faithful.zip").unlink()  # left out, then restored
    page.changed()
    wait_for(qapp, lambda: can(page, "Get it again", "faithful"))
    row_button(page, "Get it again", "faithful").click()
    wait_for(qapp, lambda: (ctx.config.server_dir / "player-only" / "faithful.zip").is_file(), 20)


# ---------------------------------------------------------------- Make a modpack
def test_make_a_modpack_then_save_it_and_post_it_in_discord(
    qapp, agent_http, monkeypatch, tmp_path
):
    import zipfile

    from agent import modpackmake
    from desktop import make_modpack

    with_mods(agent_http)
    ctx = core_of(agent_http).servers["survival"]
    (ctx.config.server_dir / "config").mkdir(exist_ok=True)
    (ctx.config.server_dir / "config" / "alpha.toml").write_text("speed = 2\n")

    async def no_sites(ctx, items):  # the sites themselves have their own tests
        return None

    async def versions(ctx, items, players_loader):
        return {"minecraft": "1.21.1", "fabric-loader": "0.16.5"}

    posted = []

    async def post(ctx, token, user="system"):
        posted.append(token)
        return {"posted": 1}

    monkeypatch.setattr(modpackmake, "_look_up", no_sites)
    monkeypatch.setattr(modpackmake, "_dependencies", versions)
    monkeypatch.setattr(modpackmake, "_chat_channels", lambda ctx: ["c1"])
    monkeypatch.setattr(modpackmake, "post_to_discord", post)
    asked_with = []

    def answer(dialog):
        asked_with.append(dialog)
        dialog.configs[0][0].setChecked(True)
        return 1

    monkeypatch.setattr(make_modpack.MakeDialog, "exec", answer)
    pc = FakePC()
    pc.save_to = str(tmp_path / "saved.mrpack")
    window, page = mods_page(qapp, agent_http, pc)
    assert page.make_card.frame.isVisibleTo(page)
    button(page, "Make a modpack…").click()
    wait_for(qapp, lambda: "is ready" in shown_text(page), 30)
    dialog = asked_with[0]
    assert dialog.leave_out.isChecked(), "ticked by default for friends"
    assert dialog.body()["configs"] == ["config/alpha.toml"]
    assert not dialog.required.isEnabled(), "no Companion on this server"
    assert not dialog.chat_on.isVisibleTo(dialog)
    shown = shown_text(page)
    assert "Only share it with people you trust." in shown, "Alpha and Gamma are own jars"
    assert "Share it from the Modrinth App" in shown

    button(page, "Save…").click()
    wait_for(qapp, lambda: "Saved to" in page.note.text(), 20)
    with zipfile.ZipFile(tmp_path / "saved.mrpack") as zf:
        names = zf.namelist()
    assert "overrides/config/alpha.toml" in names and "overrides/mods/alpha.jar" in names
    assert "overrides/mods/beta.jar" not in names, "a turned-off mod stays out"

    button(page, "Post in Discord").click()
    wait_for(qapp, lambda: "Posted in 1 Discord" in page.note.text(), 20)
    assert posted == [page.make_card.result["token"]]


# ---------------------------------------------------------------- Download link
def test_turn_on_the_download_link_and_add_a_resource_pack(qapp, agent_http, monkeypatch, tmp_path):
    import hashlib

    from agent import downloadlink

    from .test_modpack_make import PACK

    with_mods(agent_http)
    ctx = core_of(agent_http).servers["survival"]
    monkeypatch.setattr(downloadlink.LinkServer, "address", lambda self: "127.0.0.1")
    props = ctx.config.server_dir / "server.properties"
    if not props.is_file():
        props.write_text("motd=hi\n")
    pack = tmp_path / "Textures.zip"
    pack.write_bytes(PACK)
    pc = FakePC()
    pc.picked = str(pack)
    window, page = mods_page(qapp, agent_http, pc)
    wait_for(qapp, lambda: page.link_card.frame.isVisibleTo(page) and page.link_card.data, 20)
    assert "No resource pack." in shown_text(page)
    button(page, "Turn on the download link").click()
    wait_for(qapp, lambda: page.link_card.data.get("on"), 20)
    assert "Make a modpack and its link shows here." in shown_text(page)

    button(page, "Choose a pack…").click()
    wait_for(qapp, lambda: page.link_card.data.get("resource_pack"), 20)
    assert "Textures.zip" in shown_text(page) and "served by the download link" in shown_text(page)
    assert f"resource-pack-sha1={hashlib.sha1(PACK).hexdigest()}" in props.read_text()

    page.link_card.required.click()
    wait_for(qapp, lambda: "require-resource-pack=true" in props.read_text(), 20)
    button(page, "Turn off").click()
    wait_for(qapp, lambda: not page.link_card.data.get("on"), 20)
    assert "Turn on the download link so players' games can get it." in shown_text(page)
    assert "resource-pack=\n" in props.read_text()


# ---------------------------------------------------------------- Put it on Modrinth
def test_sign_in_with_modrinth_and_upload_the_modpack(qapp, agent_http, monkeypatch):
    import re
    import threading
    import urllib.request

    from agent import downloads, modpackmake
    from desktop import make_modpack, modrinth_card

    from .test_curseforge import FakeCurseForge
    from .test_modrinth_share import FakeModrinthSite

    with_mods(agent_http)
    site = FakeModrinthSite(FakeCurseForge())
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(site))

    async def no_sites(ctx, items):
        return None

    async def versions(ctx, items, players_loader):
        return {"minecraft": "1.21.1", "fabric-loader": "0.16.5"}

    monkeypatch.setattr(modpackmake, "_look_up", no_sites)
    monkeypatch.setattr(modpackmake, "_dependencies", versions)
    monkeypatch.setattr(make_modpack.MakeDialog, "exec", lambda dialog: 1)
    typed = ["friends-pack", "Our friends' pack"]
    monkeypatch.setattr(modrinth_card.QInputDialog, "getText", lambda *a, **k: (typed.pop(0), True))
    pc = FakePC()
    window, page = mods_page(qapp, agent_http, pc)
    card = page.modrinth_card
    wait_for(qapp, lambda: card.frame.isVisibleTo(page) and card.data, 20)
    assert "Not signed in to Modrinth." in shown_text(page)

    button(page, "Sign in with Modrinth").click()
    wait_for(qapp, lambda: pc.opened, 20)
    state = re.search(r"state=([^&]+)", pc.opened[-1]).group(1)
    assert f".pc{card.catcher.port}." in state, "the way back is this computer's own address"
    back = f"http://127.0.0.1:{card.catcher.port}/modrinth?state={state}&code=good-code"
    seen: list[bytes] = []
    threading.Thread(target=lambda: seen.append(urllib.request.urlopen(back).read())).start()
    wait_for(qapp, lambda: "Signed in to Modrinth as mark." in shown_text(page), 20)
    assert b"You can close this tab" in seen[0]

    button(page, "Make a modpack…").click()
    wait_for(qapp, lambda: "is ready" in shown_text(page), 30)
    wait_for(qapp, lambda: (card.data.get("made") or {}).get("version") == "1", 20)
    button(page, "Upload Survival 1").click()
    wait_for(qapp, lambda: "press Submit for review" in page.note.text(), 30)
    assert site.projects[0]["slug"] == "friends-pack" and site.projects[0]["is_draft"]
    assert "https://modrinth.com/modpack/friends-pack" in shown_text(page)

    button(page, "Sign out of Modrinth").click()
    wait_for(qapp, lambda: "Not signed in to Modrinth." in shown_text(page), 20)


# ---------------------------------------------------------------- the anti-cheat picker
def test_the_anti_cheat_picker_offers_grim_with_the_fabric_warning(qapp, agent_http):
    with_mods(agent_http)  # Alpha and Gamma say "*": mods players need too
    window, page = mods_page(qapp, agent_http)
    wait_for(qapp, lambda: "No anti-cheat is in." in shown_text(page), 20)
    words = shown_text(page)
    assert "Try it with a friend first." in words
    assert button(page, "Add Grim")


# ---------------------------------------------------------------- far view
def test_the_far_view_card_offers_each_choice_and_chunky(qapp, agent_http):
    from .test_far_view import FarModrinth

    with_mods(agent_http, FarModrinth())
    window, page = mods_page(qapp, agent_http)
    wait_for(qapp, lambda: "Far view is off." in shown_text(page), 20)
    words = shown_text(page)
    assert "Distant Horizons: friends see far land" in words
    assert "Chunky loads land around the middle of the world" in words
    assert button(page, "Add Chunky")


def test_make_dialog_offers_required_and_the_chat_line_with_the_companion(qapp):
    from desktop import make_modpack

    options = {
        "name": "Friends",
        "version": "2",
        "required_to_join": {"available": True, "on": False},
        "chat_line": {"on": True, "text": "Get {name} {version}"},
    }
    dialog = make_modpack.MakeDialog(None, options)
    assert dialog.required.isEnabled() and not dialog.required.isChecked()
    assert dialog.body()["chat_line"] == {"on": True, "text": "Get {name} {version}"}
    dialog.required.setChecked(True)
    assert not dialog.chat_text.isEnabled(), "a required modpack has no chat line"
    assert dialog.body()["required"] is True
    dialog.chat_text.setText("  ")
    assert dialog.body()["chat_line"] is None, "empty: the agent's own wording"


def test_make_dialog_on_vanilla_offers_change_server_type(qapp):
    from desktop import make_modpack

    opened = []
    options = {"name": "Friends", "required_to_join": {"available": False, "needs_fabric": True}}
    dialog = make_modpack.MakeDialog(None, options, change_type=lambda: opened.append(1))
    assert not dialog.required.isEnabled()
    assert "Needs a Fabric server" in shown_text(dialog)
    button(dialog, "Change server type").click()
    assert opened == [1]
    assert dialog.result() == make_modpack.QDialog.DialogCode.Rejected


def test_make_dialog_sets_the_title_screen(qapp):
    from desktop import make_modpack

    options = {
        "name": "Friends",
        "version": "2",
        "required_to_join": {"available": True, "on": False},
        "title_screen": {
            "button": {"on": True, "address": "play.example.net"},
            "flavor_text": {"on": False, "text": ""},
            "background": {"on": False},
            "background_set": False,
        },
    }
    uploaded = {"title_screen": {"background_set": True}}
    dialog = make_modpack.MakeDialog(None, options, lambda: uploaded)
    assert dialog.address.isEnabled() and not dialog.flavor.isEnabled()
    assert not dialog.background_on.isEnabled(), "no picture picked yet"
    button(dialog, "Pick picture").click()
    assert dialog.background_on.isChecked()
    dialog.flavor_on.setChecked(True)
    dialog.flavor.setText(" Dragons! ")
    assert dialog.body()["title_screen"] == {
        "button": {"on": True, "address": "play.example.net"},
        "flavor_text": {"on": True, "text": "Dragons!"},
        "background": {"on": True},
    }
