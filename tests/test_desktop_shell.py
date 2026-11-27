"""The desktop app's binder: the window with no Windows frame, the server
tabs, the sheet and a server's page list. Driven against the real agent
with the fake Minecraft servers; Windows itself is pretend (tests/fake_pc.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import colors

from .fake_pc import FakePC
from .test_desktop_app import (
    on_all_servers,
    open_window,
    owner,
    sign_in,
    signed_in_on,
    wait_for,
)

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402

from desktop import theme  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ colors
@pytest.mark.parametrize("dark", [False, True])
def test_every_server_color_stays_readable_on_the_binder(dark):
    """WCAG AA with the page as plain paper: 4.5:1 for text, 3:1 for the
    edge lines. None is a screen with no server (the copper)."""
    failed = []
    for name, hex_ in [("copper", None)] + [(name, hex_) for _id, name, hex_ in colors.PALETTE]:
        look = theme.tokens(dark, hex_)
        for fg, bg, least in (
            ("band-text", "band", colors.AA_TEXT),
            ("tab-text", "tab-fill", colors.AA_TEXT),
            ("text-primary", "paper", colors.AA_TEXT),
            ("text-secondary", "paper", colors.AA_TEXT),
            ("accent-ink", "paper", colors.AA_TEXT),
            ("accent-text", "accent", colors.AA_TEXT),
            ("border-strong", "paper", colors.AA_MARK),
            ("accent-ring", "paper", colors.AA_MARK),
        ):
            ratio = colors.contrast(colors.rgb(look[fg]), colors.rgb(look[bg]))
            if ratio < least:
                failed.append(f"{name}: {fg} on {bg} {ratio:.2f}")
    assert not failed, failed


def test_text_on_a_danger_button_is_readable_in_every_theme():
    themes = json.loads((theme.SHARED / "theme.json").read_text(encoding="utf-8"))
    for name, tokens in themes.items():
        ratio = colors.contrast(colors.rgb(tokens["on-danger"]), colors.rgb(tokens["danger"]))
        assert ratio >= colors.AA_TEXT, (name, ratio)


def test_the_page_stays_paper_whatever_the_servers_color():
    plain, blue = theme.tokens(False), theme.tokens(False, "#1967D4")
    for token in ("paper", "surface", "text-primary", "text-secondary", "success", "danger"):
        assert blue[token] == plain[token]
    assert blue["band"] != plain["band"] and blue["accent"] != plain["accent"]


# -------------------------------------------------------------------- font
def test_the_heading_font_ships_with_its_license(qapp):
    theme.load_fonts()
    assert "Archivo" in QFontDatabase.families()
    assert "SIL Open Font License" in (ROOT / "licenses" / "Archivo-OFL.txt").read_text()
    assert "licenses/Archivo-OFL.txt" in (ROOT / "THIRD-PARTY-NOTICES.md").read_text()
    # The installed program carries the font files too: they are in shared/,
    # which is packed whole.
    assert sorted(f.name for f in (ROOT / "shared" / "fonts").glob("*.ttf")) == [
        "Archivo-Bold.ttf",
        "Archivo-Medium.ttf",
        "Archivo-SemiBold.ttf",
    ]
    assert '(str(ROOT / "shared"), "shared")' in (ROOT / "packaging" / "app.spec").read_text()


# ------------------------------------------------------------- server tabs
def tab_color(binder, index):
    """The fill near the tab's bottom edge, under its words."""
    across = binder.tabRect(index).center().x()
    return binder.grab().toImage().pixelColor(across, binder.height() - 2).name().upper()


def test_each_server_tab_has_its_color_and_a_dot_only_while_it_runs(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    rows = agent_http.get("/api/servers", headers=owner(agent_http)).json()["servers"]
    ids = {r["name"]: r["id"] for r in rows}
    color = {r["name"]: r["color"] for r in rows}
    binder = window.binder
    for name in ("Survival", "Creative"):
        assert binder.looks[ids[name]]["tab-fill"] == theme.tokens(False, color[name])["tab-fill"]
    # Survival is open, so its tab runs into the band. All servers stays neutral.
    assert tab_color(binder, 0) == window.look["tab-neutral"].upper()
    assert tab_color(binder, 1) == window.look["band"].upper()
    assert tab_color(binder, 2) == binder.looks[ids["Creative"]]["tab-fill"].upper()
    assert len({tab_color(binder, i) for i in range(3)}) == 3
    assert binder.running == set()
    quiet = binder.tabRect(1).width()
    agent_http.post(f"/api/servers/{ids['Survival']}/server/start", headers=owner(agent_http))
    window.refresh()
    wait_for(qapp, lambda: binder.running == {ids["Survival"]}, pause=0.2)
    assert binder.tabRect(1).width() > quiet  # room for the dot
    assert binder.tabText(1) == "Survival"


def test_a_click_on_the_current_tab_comes_back_from_pc_settings(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    window.tools["gear"].click()
    wait_for(qapp, lambda: window.screen_name == "PC settings")
    assert window.binder.none_open  # no tab is drawn as the open one
    index = window.binder.currentIndex()
    assert window.binder.tabText(index) == "Survival"
    window.binder.tabBarClicked.emit(index)
    wait_for(qapp, lambda: window.screen_name == "Server")
    assert not window.binder.none_open


def test_the_open_tab_follows_the_screen_however_it_was_opened(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    binder = window.binder
    assert binder.tabData(binder.currentIndex()) == window.server_id
    window.show_screen("New server")  # not by a click on "+"
    assert binder.tabData(binder.currentIndex()) == "new"
    window.show_screen("All servers")
    assert binder.tabData(binder.currentIndex()) == "all"
    assert window.screen_name == "All servers"


# --------------------------------------------------------------- page list
def marker_top(window):
    tabs = window.server_screen.page_list.tabs
    return None if tabs.open is None else tabs.open.y() if tabs.sliding is None else tabs.sliding


def page_button(window, page):
    return next(b for b in window.server_screen.tabs.buttons() if b.property("page") == page)


def test_the_marker_is_on_the_open_page_and_gone_on_activity(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    assert window.server_screen.page_list.isVisibleTo(window)
    assert marker_top(window) == page_button(window, "Overview").y()
    page_button(window, "Backups").click()
    wait_for(qapp, lambda: window.server_screen.current_page == "Backups")
    assert marker_top(window) == page_button(window, "Backups").y()  # at once: animations are off
    window.open_activity()  # a page with no button
    assert marker_top(window) is None
    assert not any(b.isChecked() for b in window.server_screen.tabs.buttons())
    window.binder.setCurrentIndex(0)
    wait_for(qapp, lambda: window.screen_name == "All servers")
    assert not window.server_screen.page_list.isVisibleTo(window)


def test_the_marker_slides_when_windows_shows_animations(qapp, agent_http):
    pc = FakePC()
    pc.animated = True
    window = signed_in_on(qapp, agent_http, pc=pc)
    page_list = window.server_screen.page_list
    start, end = page_button(window, "Overview").y(), page_button(window, "Settings").y()
    page_button(window, "Settings").click()
    assert page_list.slide.state() == page_list.slide.State.Running
    assert marker_top(window) != end
    seen = set()
    wait_for(qapp, lambda: seen.add(marker_top(window)) or page_list.tabs.sliding is None)
    assert marker_top(window) == end
    assert any(start < y < end for y in seen)  # it passed through the places between


def test_the_page_list_grows_with_windows_text_size(qapp, agent_http):
    normal = signed_in_on(qapp, agent_http).server_screen.page_list.width()
    pc = FakePC()
    pc.scale = 2.0
    big = signed_in_on(qapp, agent_http, pc=pc).server_screen.page_list
    assert big.width() > normal
    widest = max(b.sizeHint().width() for b in big.group.buttons())
    assert big.width() >= widest + big.LEFT  # no page name is cut off


# --------------------------------------------------------- frameless window
def test_the_window_has_no_frame_and_is_see_through_around_the_binder(qapp, agent_http):
    window = open_window(qapp, agent_http)
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    picture = window.grab().toImage()
    assert picture.pixelColor(2, window.height() - 2).alpha() < 40  # only the soft shadow
    band = window.band.mapTo(window, QPoint(20, 6))
    assert picture.pixelColor(band).name().upper() == window.look["band"]
    # The row above the band looks empty but still takes the mouse.
    gap = QPoint(window.binder.geometry().right() + 40, window.binder.geometry().center().y())
    assert picture.pixelColor(window.topbar.mapTo(window, gap)).alpha() == 1


def test_over_remote_desktop_it_is_grey_behind_the_binder(qapp, agent_http):
    pc = FakePC()
    pc.remote = True
    window = open_window(qapp, agent_http, pc)
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint  # still its own buttons
    assert not window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    picture = window.grab().toImage()
    assert picture.pixelColor(2, window.height() - 2).name().upper() == window.look["desk"]
    assert not pc.animations()


def test_the_windows_own_buttons_minimize_maximize_and_close(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    buttons = window.window_buttons
    assert [b.accessibleName() for b in buttons.values()] == ["Minimize", "Maximize", "Close"]
    assert all(b.isVisibleTo(window) for b in buttons.values())
    buttons["maximize"].click()
    wait_for(qapp, window.isMaximized)
    assert buttons["maximize"].accessibleName() == "Restore Down"
    assert not any(grip.isVisible() for grip in window.grips)  # nothing to resize
    buttons["maximize"].click()
    wait_for(qapp, lambda: not window.isMaximized())
    assert all(grip.isVisible() for grip in window.grips)
    buttons["minimize"].click()
    wait_for(qapp, window.isMinimized)
    window.showNormal()
    buttons["close"].click()  # as before: hidden in the notification area, not closed
    wait_for(qapp, lambda: not window.isVisible())
    assert pc.alerts and not window.quitting


def test_the_window_buttons_stay_on_the_sign_in_screen(qapp, agent_http):
    window = open_window(qapp, agent_http)
    assert window.screen_name == "Sign-in"
    assert window.window_buttons["close"].isVisibleTo(window)
    assert not window.binder.isVisibleTo(window)
    assert not any(tool.isVisibleTo(window) for tool in window.tools.values())
    # Tab starts in the form and reaches the window's buttons last.
    stops, at = [], window.nextInFocusChain()
    while at is not window:
        if at.isVisibleTo(window) and at.focusPolicy() & Qt.FocusPolicy.TabFocus:
            stops.append(at.objectName())
        at = at.nextInFocusChain()
    assert stops[0] == "username"
    assert stops[-3:] == ["minimize", "maximize", "close"]


def test_the_window_drags_by_the_top_row_and_the_band_not_by_the_page(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    paper = window.sheet.paper().toRect().translated(window.sheet.mapTo(window, QPoint(0, 0)))
    assert not window.can_drag(paper.center())
    assert window.can_drag(window.topbar.mapTo(window, QPoint(window.topbar.width() // 2, 4)))
    assert window.can_drag(window.band.mapTo(window, QPoint(8, window.band.height() // 2)))
    # The grips sit on the binder's edges, inside the see-through room.
    room = window.centralWidget().layout().contentsMargins()
    left = next(g for g in window.grips if g.edges == Qt.Edge.LeftEdge)
    assert left.x() == room.left() and left.width() <= 8
