"""The new look of the pages with lists (Mods, Schedules, Backups, Players
and chat, Activity) and of the Diagnose server window: each thing in a
list is a raised box, on and off is a switch that goes back when the agent
says no, and a test run is a list of round marks. Driven against the real
agent with the fake Minecraft servers."""

from __future__ import annotations

import pytest

from desktop import theme
from desktop.diagnose import DiagnoseWindow
from desktop.overview import card, dot_of
from desktop.parts import Box, Bubble, Face, IconButton, Mark, Row, Segmented, Steps, Switch
from desktop.words import mode

from .test_desktop_app import shown_text, signed_in_on, wait_for
from .test_desktop_mods import can, mods_page, row_button, with_mods
from .test_desktop_pages import open_page, running

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")

RUN = {
    "running": False,
    "lines": [
        {"group": "Joins", "ok": True, "what": "Join from this PC", "detail": "127.0.0.1:25565"},
        {"group": "Joins", "ok": False, "what": "Join over Tailscale", "detail": "It is stopped"},
        {"group": "Joins", "ok": None, "what": "Join from the internet", "detail": "No address"},
    ],
    "changes": [
        {"safe": True, "title": "Turn Tailscale back on", "why": "It is installed but stopped."},
        {"safe": False, "title": "Open the firewall", "why": "Adds one rule."},
    ],
}


def labels(widget) -> list[str]:
    return [w.text() for w in widget.findChildren(QtWidgets.QLabel) if w.text()]


# ------------------------------------------------------------------ the parts
def test_a_list_heading_has_no_box_and_its_rows_are_boxes(qapp):
    listed, layout = card("Schedules", box=False)
    assert isinstance(listed, Box) and not listed.raised
    assert listed.contentsMargins().left() == 0  # no room kept for an edge or a shadow
    row = Row()
    layout.addWidget(row)
    assert row.raised and row.column.itemAt(0).layout() is row.line
    listed.show()
    picture = listed.grab().toImage()
    paper = theme.tokens(False)["paper"].lower()
    assert picture.pixelColor(row.geometry().center()).name() == paper  # the row is the box
    edge = theme.tokens(False)["border-strong"].lower()
    seen = {picture.pixelColor(x, 3).name() for x in range(picture.width())}
    assert edge not in seen  # nothing is drawn around the heading


def test_an_info_dot_can_be_given_other_words(qapp):
    frame, _layout = card("Bring a modpack in", words="Simple words")
    dot = dot_of(frame)
    dot.say("Technical words")
    assert dot.accessibleDescription() == "Technical words"
    frame.show()
    dot.click()
    wait_for(qapp, lambda: Bubble.words() == "Technical words")
    Bubble.away()


def test_test_marks_are_joined_and_say_why_only_when_told(qapp):
    steps = Steps()
    steps.add("ok", "Join from this PC")
    steps.add("bad", "Join over Tailscale", "It is stopped")
    steps.add("skip", "Join from the internet", "No address")
    assert [m.state for m in steps.findChildren(Mark)] == ["ok", "bad", "skip"]
    assert labels(steps) == [
        "Join from this PC",
        "Join over Tailscale",
        "It is stopped",
        "Join from the internet",
        "No address",
    ]
    steps.resize(300, 200)
    steps.show()
    picture = steps.grab().toImage()
    look = theme.tokens(False)
    first, second = steps.marks[0].geometry(), steps.marks[1].geometry()
    # green for a pass, red for a fail, and the line between them
    assert picture.pixelColor(first.center().x(), first.top() + 3).name() == look["success"].lower()
    assert (
        picture.pixelColor(second.center().x(), second.top() + 3).name() == look["danger"].lower()
    )
    between = (first.bottom() + second.top()) // 2
    assert picture.pixelColor(first.center().x(), between).alpha() > 0


def test_the_old_pills_and_segment_buttons_are_gone_from_the_style_sheet():
    sheet = theme.style_sheet(theme.tokens(True))
    assert 'kind="chip"' not in sheet and 'kind="segment"' not in sheet
    assert "QDialog" in sheet  # a dialog gets the paper color, in dark too


# ---------------------------------------------------------------------- Mods
def test_a_mod_is_a_box_with_a_switch_and_named_icon_buttons(qapp, agent_http):
    with_mods(agent_http)
    window, page = mods_page(qapp, agent_http)
    wait_for(qapp, lambda: "Alpha" in shown_text(page))
    rows = [r for r in page.rows.parentWidget().findChildren(Row) if "Alpha" in shown_text(r)]
    assert rows
    turn = row_button(page, "Turn off", "Alpha")
    assert isinstance(turn, Switch) and turn.isChecked()
    remove = row_button(page, "Remove", "Alpha")
    assert isinstance(remove, IconButton) and remove.property("hoverName") == "Remove"
    more = row_button(page, "More", "Alpha")
    assert more.property("hoverName") == "More" and more.menu().actions()
    # the list sits above the cards for sharing the modpack
    column = page.column
    assert column.indexOf(page.list_card) < column.indexOf(page.make_card.frame)
    # no Search button: the field with the magnifying glass searches on Enter
    assert not [
        b for b in page.search_card.findChildren(QtWidgets.QPushButton) if b.text() == "Search"
    ]


def test_a_switch_the_agent_refuses_goes_back(qapp, agent_http):
    folder = with_mods(agent_http)
    window, page = mods_page(qapp, agent_http)
    wait_for(qapp, lambda: can(page, "Turn off", "Alpha"))
    (folder / "alpha.jar").unlink()  # gone behind the app's back: the agent can't turn it off
    row_button(page, "Turn off", "Alpha").click()
    wait_for(qapp, lambda: page.note.text() != "")
    assert "turned off" not in page.note.text()
    # the list is read again, so no switch is left showing "off" for a mod that isn't
    wait_for(qapp, lambda: "Alpha" not in shown_text(page.list_card))


def test_a_running_server_greys_the_switch_and_the_remove_button(qapp, agent_http):
    with_mods(agent_http)
    running(qapp, agent_http)
    window, page = mods_page(qapp, agent_http)
    wait_for(qapp, lambda: "Alpha" in shown_text(page))
    assert not row_button(page, "Turn off", "Alpha").isEnabled()
    assert not row_button(page, "Remove", "Alpha").isEnabled()


# ------------------------------------------------------------- other pages
def test_players_have_a_face_and_one_plain_line_of_facts(qapp, agent_http):
    running(qapp, agent_http)
    window = signed_in_on(qapp, agent_http)
    sid = window.servers[0]["id"]
    window.agent.on(sid, "POST", "/server/command", {"command": "fakejoin Alex", "confirm": True})
    page = open_page(qapp, window, "Players and chat")
    wait_for(qapp, lambda: page.online_card.findChildren(Row))
    row = page.online_card.findChildren(Row)[0]
    [face] = row.findChildren(Face)
    assert face.accessibleName() == "Alex"
    assert not [w for w in page.findChildren(QtWidgets.QLabel) if w.property("kind") == "chip"]


def test_activity_picks_everything_or_crashes_with_the_segmented_control(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    window.open_activity()
    page = window.server_screen.page
    wait_for(qapp, lambda: isinstance(page.filters, Segmented) and page.filters.key() == "all")
    page.filters.buttons["crashes"].click()
    wait_for(qapp, lambda: page.crashes_only and "Crash history" in shown_text(page))
    window.open_activity()  # opened again without crashes: the control follows
    wait_for(qapp, lambda: page.filters.key() == "all")


# ------------------------------------------------------------ Diagnose server
def diagnose_window(qapp, window) -> DiagnoseWindow:
    dialog = DiagnoseWindow(window, window.servers[0]["id"], "Survival")
    dialog.timer.stop()
    dialog.timer.timeout.disconnect()  # only the made-up run below is shown
    dialog.show()
    qapp.processEvents()
    return dialog


def test_a_finished_run_is_marks_one_box_of_changes_and_one_filled_button(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    dialog = diagnose_window(qapp, window)
    try:
        dialog.show_result(RUN)
        assert [m.state for m in dialog.findChildren(Mark)] == ["ok", "bad", "skip"]
        said = labels(dialog)
        # Simple: a test that passed is one line, the others say why
        assert "127.0.0.1:25565" not in said
        assert "It is stopped" in said and "No address" in said
        [box] = dialog.findChildren(Box)
        assert labels(box) == [
            "Safe changes",
            "Turn Tailscale back on",
            "It is installed but stopped.",
            "Risky changes",
            "Open the firewall",
            "Adds one rule.",
        ]
        kinds = {k: b.property("kind") for k, b in dialog.picks.items()}
        assert kinds == {"all": "primary", "safe": None, "none": "plain"}
        assert all(b.isVisible() for b in dialog.picks.values())
        mode["technical"] = True
        dialog.show_result(RUN)
        assert "127.0.0.1:25565" in labels(dialog)  # Technical keeps every detail
    finally:
        mode["technical"] = False
        dialog.close()


def test_a_run_without_changes_offers_only_to_run_again(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    dialog = diagnose_window(qapp, window)
    try:
        dialog.show_result({**RUN, "changes": []})
        assert not dialog.findChildren(Box)
        assert not any(b.isVisible() for b in dialog.picks.values())
        assert dialog.again.isVisible()
    finally:
        dialog.close()
