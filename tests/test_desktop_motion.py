"""The desktop app's moves: the top view, the page turn, hover lifts, the
note fading in and the status dot's pulse. All of them are off when
Windows' "Show animations" is off or over Remote Desktop (tests/fake_pc.py
is the pretend Windows)."""

from __future__ import annotations

import pytest

from .fake_pc import FakePC
from .test_desktop_app import signed_in_on, wait_for
from .test_desktop_parts import gallery

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QEnterEvent, QKeyEvent, QMouseEvent, QShortcut  # noqa: E402

from desktop import motion, parts  # noqa: E402
from desktop.ui import Note  # noqa: E402


@pytest.fixture(autouse=True)
def close_windows(qapp):
    yield
    if motion.Turn.running is not None:
        motion.Turn.running.stop()
    for window in qapp.topLevelWidgets():
        window.close()
        window.deleteLater()
    qapp.processEvents()


def moving(qapp, agent_http, remote=False):
    pc = FakePC()
    pc.animated, pc.remote = True, remote
    return signed_in_on(qapp, agent_http, pc=pc)


def mouse(widget, kind, pos, button=Qt.MouseButton.NoButton):
    held = button if kind == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton
    event = QMouseEvent(
        kind,
        QPointF(pos),
        QPointF(widget.mapToGlobal(pos.toPoint())),
        button,
        held,
        Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(widget, event)


def gap(window) -> QPointF:
    """A place on the empty part of the top row, between the tabs and the tools."""
    return QPointF(window.binder.geometry().right() + 60, window.topbar.height() / 2)


def tab_names(window) -> list[str]:
    return [window.binder.tabText(i) for i in range(window.binder.count())]


def tab_middle(window, name: str) -> QPointF:
    """Where a tab's middle is drawn while the top view is open."""
    index = tab_names(window).index(name)
    return window.top_view.tab_onto(index).map(QPointF(window.binder.tabRect(index).center()))


# ------------------------------------------------------------------ top view
def test_resting_on_the_empty_top_row_opens_the_top_view(qapp, agent_http):
    window = moving(qapp, agent_http)
    top = window.top_view
    assert not top.isVisible()
    mouse(window.topbar, QEvent.Type.MouseMove, gap(window))
    assert top.wait.isActive() and not top.isVisible()  # not at once: it waits a moment
    wait_for(qapp, lambda: top.isVisible())
    wait_for(qapp, lambda: top.at == 1.0)
    # The binder moved down: the band's top edge is drawn lower than it really is.
    band = window.band
    real = band.mapTo(window, band.rect().topLeft()).y()
    assert top.flat_onto(band).map(QPointF(0, 0)).y() > real + 100
    # And the real binder under it is not drawn meanwhile.
    assert window.centralWidget().graphicsEffect() is not None


def test_a_tab_or_a_button_never_opens_the_top_view(qapp, agent_http):
    window = moving(qapp, agent_http)
    top = window.top_view
    on_tab = QPointF(window.binder.tabRect(1).center())
    mouse(window.binder, QEvent.Type.MouseMove, on_tab)
    assert not top.wait.isActive()
    # From the empty part onto a tab before the wait is over: it stays shut.
    mouse(window.topbar, QEvent.Type.MouseMove, gap(window))
    assert top.wait.isActive()
    mouse(window.binder, QEvent.Type.MouseMove, on_tab)
    assert not top.wait.isActive() and not top.isVisible()
    # A press on the top row is for moving the window.
    mouse(window.topbar, QEvent.Type.MouseMove, gap(window))
    QtWidgets.QApplication.sendEvent(window.topbar, QEvent(QEvent.Type.Leave))
    assert not top.wait.isActive()


def test_each_tab_stands_one_step_behind_the_one_before(qapp, agent_http):
    window = moving(qapp, agent_http)
    top = window.top_view
    top.open()
    wait_for(qapp, lambda: top.at == 1.0)
    names = tab_names(window)
    open_ = window.binder.currentIndex()
    assert names[open_] == "Survival"
    # The open server is in front (lowest on the screen); each one after it is higher up.
    order = names[open_:] + names[:open_]
    heights = [tab_middle(window, name).y() for name in order]
    assert heights == sorted(heights, reverse=True)
    assert all(low - high > 20 for low, high in zip(heights, heights[1:], strict=False))
    # Bigger than in the top row.
    drawn = top.tab_onto(open_).mapRect(window.binder.tabRect(open_))
    assert drawn.width() > window.binder.tabRect(open_).width() * 1.2


def test_a_click_on_a_tab_in_the_top_view_opens_that_server(qapp, agent_http):
    window = moving(qapp, agent_http)
    top = window.top_view
    top.open()
    wait_for(qapp, lambda: top.at == 1.0)
    at = tab_middle(window, "Creative")
    mouse(top, QEvent.Type.MouseMove, at)
    assert tab_names(window)[top.pick] == "Creative"  # shown a little brighter
    mouse(top, QEvent.Type.MouseButtonPress, at, Qt.MouseButton.LeftButton)
    assert window.server_screen.row["name"] == "Survival"  # nothing on mouse down
    mouse(top, QEvent.Type.MouseButtonRelease, at, Qt.MouseButton.LeftButton)
    wait_for(qapp, lambda: window.server_screen.row["name"] == "Creative")
    wait_for(qapp, lambda: not top.isVisible())
    assert window.centralWidget().graphicsEffect() is None  # the real binder is back


def test_the_window_buttons_ride_on_the_binder_and_still_work(qapp, agent_http):
    window = moving(qapp, agent_http)
    top = window.top_view
    top.open()
    wait_for(qapp, lambda: top.at == 1.0)
    bell = window.tools["bell"]
    at = top.flat_onto(window.strip).map(QPointF(bell.geometry().center()))
    assert top.button_at(at) is bell
    mouse(top, QEvent.Type.MouseMove, at)
    assert top.open_  # on a button it holds the view it is in
    mouse(top, QEvent.Type.MouseButtonPress, at, Qt.MouseButton.LeftButton)
    mouse(top, QEvent.Type.MouseButtonRelease, at, Qt.MouseButton.LeftButton)
    wait_for(qapp, lambda: window.screen_name == "Notifications" and not top.isVisible())


def test_moving_down_onto_the_page_or_out_of_the_window_closes_it(qapp, agent_http):
    window = moving(qapp, agent_http)
    top = window.top_view
    top.open()
    wait_for(qapp, lambda: top.at == 1.0)
    mouse(top, QEvent.Type.MouseMove, QPointF(400, 200))  # still in the top part
    assert top.open_
    mouse(top, QEvent.Type.MouseMove, QPointF(400, window.height() - 80))
    assert not top.open_
    wait_for(qapp, lambda: not top.isVisible() and top.at == 0.0)
    top.open()
    QtWidgets.QApplication.sendEvent(top, QEvent(QEvent.Type.Leave))
    assert not top.open_
    wait_for(qapp, lambda: not top.isVisible())


def test_ctrl_tab_goes_through_the_tabs_and_letting_go_opens_one(qapp, agent_http):
    window = moving(qapp, agent_http)
    top = window.top_view
    names = tab_names(window)
    start = window.binder.currentIndex()
    shortcuts = {s.key().toString(): s for s in window.findChildren(QShortcut)}
    shortcuts["Ctrl+Tab"].activated.emit()
    assert top.open_ and top.keys and names[top.pick] == names[start + 1] == "Creative"
    # The mouse doesn't close it now, only the keys do.
    mouse(top, QEvent.Type.MouseMove, QPointF(400, window.height() - 80))
    assert top.open_
    shortcuts["Ctrl+Tab"].activated.emit()
    shortcuts["Ctrl+Shift+Tab"].activated.emit()
    assert names[top.pick] == "Creative"
    release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Control, Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(top, release)
    wait_for(qapp, lambda: window.server_screen.row["name"] == "Creative")
    wait_for(qapp, lambda: not top.isVisible())


def test_escape_closes_the_top_view_and_changes_nothing(qapp, agent_http):
    window = moving(qapp, agent_http)
    top = window.top_view
    top.step(1)
    escape = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(top, escape)
    wait_for(qapp, lambda: not top.isVisible())
    assert window.server_screen.row["name"] == "Survival" and window.screen_name == "Server"


def test_with_animations_off_the_mouse_never_opens_it_and_ctrl_tab_jumps(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)  # the pretend Windows has animations off
    top = window.top_view
    mouse(window.topbar, QEvent.Type.MouseMove, gap(window))
    assert not top.wait.isActive()
    top.step(1)
    assert top.isVisible() and top.at == 1.0 and top.move_.state() == top.move_.State.Stopped
    top.choose(top.pick)
    assert not top.isVisible() and top.at == 0.0
    wait_for(qapp, lambda: window.server_screen.row["name"] == "Creative")


def test_over_remote_desktop_nothing_moves(qapp, agent_http):
    window = moving(qapp, agent_http, remote=True)
    mouse(window.topbar, QEvent.Type.MouseMove, gap(window))
    assert not window.top_view.wait.isActive()
    window.open_page("Backups")
    assert motion.Turn.running is None


def test_resizing_the_window_shuts_the_top_view(qapp, agent_http):
    window = moving(qapp, agent_http)
    top = window.top_view
    top.open()
    window.resize(1000, 700)
    qapp.processEvents()
    assert not top.isVisible() and top.at == 0.0


# ----------------------------------------------------------------- page turn
def test_a_new_page_turns_in_and_takes_clicks_meanwhile(qapp, agent_http):
    window = moving(qapp, agent_http)
    wait_for(qapp, lambda: motion.Turn.running is None)
    window.open_page("Backups")
    turning = motion.Turn.running
    assert turning is not None and turning.isVisible()
    assert turning.parentWidget() is window.server_screen.pages
    assert turning.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    seen = set()
    wait_for(qapp, lambda: seen.add(round(turning.at, 2)) or motion.Turn.running is None)
    assert len(seen) > 3  # it passed through the places between
    assert not window.server_screen.pages.findChildren(motion.Turn) or not turning.isVisible()


def test_the_same_page_again_does_not_turn(qapp, agent_http):
    window = moving(qapp, agent_http)
    wait_for(qapp, lambda: motion.Turn.running is None)
    window.open_page("Overview")
    assert motion.Turn.running is None


def test_no_page_turn_with_animations_off(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    window.open_page("Backups")
    assert motion.Turn.running is None


# --------------------------------------------------------------- small moves
def hover(part, enter: bool) -> None:
    middle = QPointF(part.rect().center())
    event = QEnterEvent(middle, middle, middle) if enter else QEvent(QEvent.Type.Leave)
    QtWidgets.QApplication.sendEvent(part, event)


def a_box(window) -> parts.Box:
    return next(b for b in window.findChildren(parts.Box) if b.raised and b.isVisibleTo(window))


def test_a_box_comes_up_under_the_mouse_and_goes_back(qapp):
    window = gallery(qapp, animated=True)
    box = a_box(window)
    size, margins, paper = box.size(), box.contentsMargins(), box.paper()
    hover(box, True)
    wait_for(qapp, lambda: box.lift == 1.0)
    assert box.size() == size  # the box takes the same room
    assert box.paper().top() == paper.top() - box.LIFT  # the paper is higher
    assert box.contentsMargins().top() == margins.top() - box.LIFT  # and what is in it too
    hover(box, False)
    wait_for(qapp, lambda: box.lift == 0.0)
    assert box.contentsMargins() == margins and box.paper() == paper


def test_a_box_stays_put_with_animations_off(qapp):
    window = gallery(qapp)
    box = a_box(window)
    margins = box.contentsMargins()
    hover(box, True)
    qapp.processEvents()
    assert box.lift == 0.0 and box.contentsMargins() == margins


def test_a_tab_comes_up_under_the_mouse(qapp, agent_http):
    window = moving(qapp, agent_http)
    binder = window.binder
    mouse(binder, QEvent.Type.MouseMove, QPointF(binder.tabRect(2).center()))
    wait_for(qapp, lambda: binder.rise.get(2) == 10.0)
    assert binder.rise[1] == binder.rise[3] == 5.0  # the ones next to it, a little
    QtWidgets.QApplication.sendEvent(binder, QEvent(QEvent.Type.Leave))
    wait_for(qapp, lambda: not any(binder.rise.values()))


def test_a_note_fades_in_only_when_it_says_something_new(qapp):
    window = gallery(qapp, animated=True)
    note = Note()
    note.setParent(window)
    note.say("Saved.")
    assert note.graphicsEffect() is not None and note.graphicsEffect().opacity() < 1.0
    wait_for(qapp, lambda: note.graphicsEffect() is None)  # the fade is taken off after
    note.say("Saved.")
    assert note.graphicsEffect() is None
    window.pc.animated = False
    note.say("Saved again.")
    assert note.graphicsEffect() is None and note.text() == "Saved again."


def test_the_status_dot_grows_in_when_the_state_changes(qapp):
    window = gallery(qapp, animated=True)
    status = parts.Status("off", "Offline")
    status.setParent(window)
    status.show()
    assert status.grown == 1.0  # not when it is first shown
    status.say("on", "Online")
    assert status.grown < 1.0
    wait_for(qapp, lambda: status.grown == 1.0)
    status.say("on", "Online")
    assert status.grown == 1.0  # the same state again: nothing
    window.pc.animated = False
    status.say("bad", "Crashed")
    assert status.grown == 1.0


def test_a_diagnose_test_line_fades_in(qapp):
    window = gallery(qapp, animated=True)
    steps = parts.Steps()
    steps.setParent(window)
    steps.show()
    steps.add("ok", "Start the server")
    assert steps.marks[0].graphicsEffect() is not None
    wait_for(qapp, lambda: steps.marks[0].graphicsEffect() is None)
