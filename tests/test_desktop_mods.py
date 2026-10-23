"""The desktop app's Mods page (the installed list, Modrinth
search, missing mods, Update all, modpacks, files from this PC, a Bedrock
server's add-ons and drag and drop), driven like a person would against the
real agent with the fake Minecraft servers. Modrinth is the fake from
test_dependency_resolution, whose downloads are real jar files."""

from __future__ import annotations

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
    wait_for(qapp, lambda: "3 mods installed" in shown_text(page))
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
    wait_for(qapp, lambda: button(page, "Add a file").isEnabled())
    pc.picked = str(make_jar(tmp_path / "one.jar", "one", name="One"))
    button(page, "Add a file").click()
    wait_for(qapp, lambda: "Added One" in page.note.text(), 20)
    assert (folder / "one.jar").exists()

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

    async def import_into(ctx, token, user="system"):
        imported.append(token)
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
    assert len(imported) == 1

    # Dropped on the window: the same question; No throws the upload away.
    questions = asked(window, answer=False)
    assert drop(qapp, window, [tmp_path / "pack.mrpack"])
    wait_for(qapp, lambda: questions)
    assert len(imported) == 1


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
    wait_for(qapp, lambda: "3 mods installed" in shown_text(page))
    assert not buttons_named(page, "Turn off") and not buttons_named(page, "Update all")
    assert not page.search_card.isVisibleTo(page)
