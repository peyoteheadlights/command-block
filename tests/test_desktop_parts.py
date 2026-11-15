"""The desktop app's parts kit (desktop/parts.py and the button and tick
box rules in desktop/theme.py), tried on the gallery screen. No agent is
needed; Windows itself is pretend (tests/fake_pc.py)."""

from __future__ import annotations

import pytest

from agent import colors

from .fake_pc import FakePC
from .test_desktop_app import wait_for

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from desktop import parts, theme  # noqa: E402
from scripts.parts_gallery import Gallery  # noqa: E402

BLUE, AMBER = "#1967D4", "#F7E10D"


@pytest.fixture(autouse=True)
def close_windows(qapp):
    yield
    parts.Bubble.away()
    for window in qapp.topLevelWidgets():
        window.close()
        window.deleteLater()
    qapp.processEvents()


def gallery(qapp, dark=False, color=BLUE, animated=False, scale=1.0):
    pc = FakePC()
    pc.animated, pc.scale = animated, scale
    window = Gallery(pc, dark, color)
    window.resize(900, 760)
    window.show()
    qapp.processEvents()
    return window


def only(window, kind):
    found = [w for w in window.findChildren(kind) if w.isVisibleTo(window)]
    assert found, f"no visible {kind.__name__}"
    return found


def key(widget, which, kinds=(QEvent.Type.KeyPress, QEvent.Type.KeyRelease)):
    for kind in kinds:
        QtWidgets.QApplication.sendEvent(
            widget, QKeyEvent(kind, which, Qt.KeyboardModifier.NoModifier)
        )


def mouse(widget, kind, x, y=None):
    at = QPointF(x, widget.height() / 2 if y is None else y)
    held = (
        Qt.MouseButton.NoButton
        if kind == QEvent.Type.MouseButtonRelease
        else Qt.MouseButton.LeftButton
    )
    QtWidgets.QApplication.sendEvent(
        widget,
        QMouseEvent(
            kind,
            at,
            widget.mapToGlobal(at),
            Qt.MouseButton.LeftButton,
            held,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def pixel(widget, x, y) -> str:
    return widget.grab().toImage().pixelColor(int(x), int(y)).name().upper()


# ------------------------------------------------------------------ colors
def over(top: str, under: str) -> colors.RGB:
    """A see-through color from the look, as it shows on ``under``."""
    color = parts.ink(top)
    return colors.mix(colors.rgb(under), (color.red(), color.green(), color.blue()), color.alphaF())


@pytest.mark.parametrize("dark", [False, True])
def test_the_parts_stay_readable_in_every_server_color(dark):
    """WCAG AA: 4.5:1 for words, 3:1 for the marks that carry a state."""
    failed = []
    for name, hex_ in [("copper", None)] + [(name, hex_) for _id, name, hex_ in colors.PALETTE]:
        look = theme.tokens(dark, hex_)
        paper = colors.rgb(look["paper"])
        tonal = over(look["accent-soft"], look["paper"])
        for what, fg, bg, least in (
            ("tonal button", colors.rgb(look["accent-ink"]), tonal, colors.AA_TEXT),
            ("status on", colors.rgb(look["success"]), paper, colors.AA_TEXT),
            ("status wait", colors.rgb(look["warning"]), paper, colors.AA_TEXT),
            ("status bad", colors.rgb(look["danger"]), paper, colors.AA_TEXT),
            ("info dot", colors.rgb(look["text-secondary"]), paper, colors.AA_TEXT),
            # The edge is what shows a switch, a slider and a tick box on
            # the paper, whatever the fill (a yellow server's is pale).
            ("part edge", colors.rgb(look["border-strong"]), paper, colors.AA_MARK),
            ("tick box edge", colors.rgb(look["accent-ring"]), paper, colors.AA_MARK),
            (
                "tick mark",
                colors.rgb(look["accent-text"]),
                colors.rgb(look["accent"]),
                colors.AA_TEXT,
            ),
        ):
            ratio = colors.contrast(fg, bg)
            if ratio < least:
                failed.append(f"{name}: {what} {ratio:.2f}")
    assert not failed, failed


def test_the_tick_mark_is_a_real_file_in_both_colors():
    for hex_, name in ((BLUE, "tick-white.svg"), (AMBER, "tick-black.svg")):
        look = theme.tokens(False, hex_)
        assert f"images/{name}" in theme.style_sheet(look)
        assert (theme.FONTS.parent / "images" / name).is_file()


def test_a_part_takes_the_colors_of_its_window(qapp):
    window = gallery(qapp)
    [search] = only(window, parts.Search)
    switch = next(s for s in only(window, parts.Switch) if s.isChecked())
    assert parts.look_of(switch) is window.look
    middle = switch.width() // 2 - 8, switch.height() // 2
    assert pixel(switch, *middle) == window.look["accent"].upper()
    tool = only(window, parts.IconButton)[0]
    icon, glass = tool.icon().cacheKey(), search.grab().toImage()
    window.show_look(True, AMBER)
    qapp.processEvents()
    assert pixel(switch, *middle) == theme.tokens(True, AMBER)["accent"].upper()
    # An icon is a picture, so it is drawn again in the dark look's color.
    assert tool.icon().cacheKey() != icon and search.grab().toImage() != glass
    assert parts.look_of(None) == theme.tokens(False)  # on no window yet: the light theme


# ------------------------------------------------------------------ buttons
def test_an_icon_button_shows_its_name_on_hover_and_to_narrator(qapp):
    window = gallery(qapp)
    button = next(b for b in only(window, parts.IconButton) if b.isEnabled())
    assert button.accessibleName() == "Performance"
    assert not button.icon().isNull() and not button.text()
    assert not button.toolTip()  # Qt's own tooltip would show a second label
    QtWidgets.QApplication.sendEvent(button, QEvent(QEvent.Type.Enter))
    assert parts.Bubble.words() == ""  # it waits a moment first
    wait_for(qapp, lambda: parts.Bubble.words() == "Performance")
    bubble = parts.Bubble.one
    under = button.mapToGlobal(QPoint(0, button.height())).y()
    assert (
        bubble.y() >= under
        and abs(bubble.geometry().center().x() - button.mapToGlobal(button.rect().center()).x())
        <= 2
    )
    QtWidgets.QApplication.sendEvent(button, QEvent(QEvent.Type.Leave))
    assert parts.Bubble.words() == ""


def test_the_info_dot_says_its_words_on_hover_on_space_and_to_narrator(qapp):
    window = gallery(qapp)
    dot = only(window, parts.InfoDot)[0]
    words = dot.accessibleDescription()
    assert len(words) > 20
    assert dot.accessibleName() == "What is this?"
    assert dot.focusPolicy() & Qt.FocusPolicy.TabFocus
    dot.setFocus()
    key(dot, Qt.Key.Key_Space)
    wait_for(qapp, lambda: parts.Bubble.words() == words)
    assert parts.Bubble.one.width() <= parts.Bubble.WIDE  # a long sentence wraps
    assert parts.Bubble.one.height() > 2 * dot.fontMetrics().height()
    dot.clearFocus()
    assert parts.Bubble.words() == ""


# ------------------------------------------------------------------- switch
def test_a_switch_flips_on_a_click_and_on_space(qapp):
    window = gallery(qapp)
    switch = next(s for s in only(window, parts.Switch) if not s.isChecked())
    flips = []
    switch.toggled.connect(flips.append)
    assert switch.accessibleName() == "Stop the server when nobody is playing"
    QTest.mouseClick(switch, Qt.MouseButton.LeftButton)
    assert flips == [True] and switch.isChecked() and switch.at == 1.0
    switch.setFocus()
    key(switch, Qt.Key.Key_Space)
    assert flips == [True, False] and switch.at == 0.0
    # The knob is on the left when off and on the right when on.
    y = switch.height() // 2
    assert pixel(switch, 14, y) == "#FFFFFF" and pixel(switch, switch.width() - 14, y) != "#FFFFFF"
    switch.setChecked(True)
    assert pixel(switch, switch.width() - 14, y) == "#FFFFFF"


def test_the_knob_slides_only_when_windows_shows_animations(qapp):
    window = gallery(qapp, animated=True)
    switch = next(s for s in only(window, parts.Switch) if not s.isChecked())
    switch.setChecked(True)
    assert switch.slide.state() == switch.slide.State.Running and switch.at < 1.0
    wait_for(qapp, lambda: switch.at == 1.0)
    window.pc.animated = False
    switch.setChecked(False)
    assert switch.at == 0.0  # at once


# ------------------------------------------------------- segmented control
def test_the_segmented_control_picks_one_and_tells_the_page(qapp):
    window = gallery(qapp)
    picked = []
    control = parts.Segmented(
        [("a", "One"), ("b", "Two hundred"), ("c", "Three")], "a", picked.append
    )
    control.setParent(window)
    control.show()
    qapp.processEvents()
    widths = {button.width() for button in control.buttons.values()}
    assert len(widths) == 1  # every choice as wide as the longest
    assert control.key() == "a"
    control.buttons["b"].click()
    assert picked == ["b"] and control.key() == "b"
    assert [b.isChecked() for b in control.buttons.values()] == [False, True, False]
    control.choose("c")  # from code: the page is not told
    assert picked == ["b"] and control.key() == "c"
    # The raised thumb is under the picked choice.
    thumb = control._thumb()
    assert thumb == control.buttons["c"].geometry()
    assert (
        pixel(control, thumb.left() + 6, thumb.center().y()) == window.look["surface-float"].upper()
    )
    control.buttons["c"].setFocus()
    key(control.buttons["c"], Qt.Key.Key_Left, (QEvent.Type.KeyPress,))
    assert control.key() == "b" and picked == ["b", "b"]  # the arrow keys move the pick


def test_the_gallery_switches_to_the_dark_look(qapp):
    window = gallery(qapp)
    window.theme.buttons["dark"].click()
    qapp.processEvents()
    assert window.dark and window.look == theme.tokens(True, BLUE)
    assert pixel(window, 5, 5) == window.look["paper"].upper()


# ------------------------------------------------------------------- slider
def test_the_handle_follows_the_mouse_and_the_value_snaps_to_a_step(qapp):
    window = gallery(qapp)
    slider = window.memory  # 2 to 16 half-GB steps
    seen = []
    slider.valueChanged.connect(seen.append)
    assert slider.value() == 9 and window.number.text() == "4.5"
    first, last = slider.handle_x(), None
    slider.setValue(16)
    last = slider.handle_x()
    slider.setValue(2)
    step = (last - slider.handle_x()) / 14
    start = slider.handle_x()
    # Press a little past step 5 (the fourth step from the left end).
    x = start + 3.3 * step
    mouse(slider, QEvent.Type.MouseButtonPress, x)
    assert slider.isSliderDown()
    assert slider.value() == 5 and window.number.text() == "2.5"
    assert slider.handle_x() == pytest.approx(x, abs=0.5)  # under the mouse, not on the step
    mouse(slider, QEvent.Type.MouseMove, start + 7.6 * step)
    assert slider.value() == 10 and seen[-3:] == [2, 5, 10]
    mouse(slider, QEvent.Type.MouseButtonRelease, start + 7.6 * step)
    assert not slider.isSliderDown()
    assert slider.handle_x() == pytest.approx(start + 8 * step, abs=0.5)  # let go: on the step
    # Past either end it stops at the end.
    mouse(slider, QEvent.Type.MouseButtonPress, -40)
    assert slider.value() == 2
    mouse(slider, QEvent.Type.MouseMove, slider.width() + 40)
    assert slider.value() == 16
    mouse(slider, QEvent.Type.MouseButtonRelease, slider.width() + 40)
    assert first > start


def test_the_handle_glides_to_the_step_when_animations_are_on(qapp):
    window = gallery(qapp, animated=True)
    slider = window.memory
    slider.glide.stop()
    slider.setValue(2)
    wait_for(qapp, lambda: slider.at == 0.0)
    start = slider.handle_x()
    mouse(slider, QEvent.Type.MouseButtonPress, start + 30)
    mouse(slider, QEvent.Type.MouseButtonRelease, start + 30)
    target = slider._share(slider.value())
    assert slider.glide.state() == slider.glide.State.Running and slider.at != target
    wait_for(qapp, lambda: slider.at == target)


def test_the_slider_works_from_the_keyboard_and_shows_who_has_it(qapp):
    window = gallery(qapp)
    slider = window.memory
    assert slider.accessibleName() == "How much memory the server may use"
    assert slider.focusPolicy() & Qt.FocusPolicy.TabFocus
    before = slider.grab().toImage()
    slider.setFocus()
    qapp.processEvents()
    assert slider.grab().toImage() != before  # the ring around it
    key(slider, Qt.Key.Key_Right, (QEvent.Type.KeyPress,))
    assert slider.value() == 10 and window.number.text() == "5"
    assert slider.at == slider._share(10)
    key(slider, Qt.Key.Key_Home, (QEvent.Type.KeyPress,))
    assert slider.value() == 2


# --------------------------------------------------------------- big number
def test_typing_in_the_big_number_moves_the_slider(qapp):
    window = gallery(qapp)
    number, slider = window.number, window.memory
    assert number.accessibleName() == slider.accessibleName()
    number.setFocus()
    number.setText("6")
    key(number, Qt.Key.Key_Return, (QEvent.Type.KeyPress,))
    assert slider.value() == 12 and number.text() == "6"
    wide = number.width()
    number.setText("12.5")
    assert number.width() > wide  # it grows with its digits


def test_the_big_number_never_keeps_what_was_not_taken(qapp):
    window = gallery(qapp)
    heard = []
    number = parts.BigNumber("Test")
    number.setParent(window)
    number.entered.connect(heard.append)
    number.show_value("4.5")
    number.show()
    number.setFocus()
    for typed in ("lots", "", "nan", "inf", "1e999"):
        number.setText(typed)
        key(number, Qt.Key.Key_Return, (QEvent.Type.KeyPress,))
        assert number.text() == "4.5" and heard == [], typed
    number.setText("7")
    key(number, Qt.Key.Key_Escape, (QEvent.Type.KeyPress,))
    assert number.text() == "4.5" and heard == []
    # A good number is passed on, but the field waits for the page to show it.
    number.setText("7")
    key(number, Qt.Key.Key_Return, (QEvent.Type.KeyPress,))
    assert heard == [7.0] and number.text() == "4.5"
    number.clearFocus()  # leaving the field does not send it a second time
    assert heard == [7.0]


def test_the_big_number_grows_with_windows_text_size(qapp):
    small = gallery(qapp).number
    big = gallery(qapp, scale=2.0).number
    assert big.font().pointSizeF() == pytest.approx(2 * small.font().pointSizeF())
    assert big.width() > 1.6 * small.width()


# ------------------------------------------------- search, tick box, status
def test_the_search_field_has_its_glass_and_a_name(qapp):
    window = gallery(qapp)
    [search] = only(window, parts.Search)
    assert search.accessibleName() == search.placeholderText() == "Search mods"
    # The glass is drawn left of the words, in the look's grey.
    before = search.grab().toImage()
    grey = QColor(window.look["text-secondary"])
    seen = [before.pixelColor(x, y) for x in range(10, 26) for y in range(search.height())]
    assert any(abs(c.red() - grey.red()) < 40 and c != before.pixelColor(40, 3) for c in seen)
    search.setText("lithium")
    assert search.textMargins().left() >= search.GLASS


def test_a_ticked_box_is_filled_and_has_its_tick(qapp):
    window = gallery(qapp)
    ticked, plain = only(window, QtWidgets.QCheckBox)
    assert ticked.isChecked() and not plain.isChecked()
    look = window.look
    option = QtWidgets.QStyleOptionButton()
    ticked.initStyleOption(option)
    box = ticked.style().subElementRect(
        QtWidgets.QStyle.SubElement.SE_CheckBoxIndicator, option, ticked
    )
    assert box.width() >= 16
    picture = ticked.grab().toImage().copy(box)
    seen = {
        picture.pixelColor(x, y).name().upper()
        for x in range(picture.width())
        for y in range(picture.height())
    }
    assert look["accent"].upper() in seen and look["accent-text"].upper() in seen
    empty = plain.grab().toImage().copy(box)
    assert (
        empty.pixelColor(box.width() // 2, box.height() // 2).name().upper()
        == look["surface"].upper()
    )


def test_each_status_has_its_color_and_fits_its_word(qapp):
    window = gallery(qapp)
    marks = only(window, parts.Status)
    assert [m.text() for m in marks] == ["Online", "Starting…", "Crashed", "Offline"]
    for mark, token in zip(marks, ("success", "warning", "danger", "neutral"), strict=True):
        assert pixel(mark, mark.DOT // 2, mark.height() // 2) == window.look[token].upper()
        assert mark.width() >= mark.sizeHint().width()  # the word is not cut off
    mark = marks[0]
    wide = mark.sizeHint().width()
    mark.say("bad", "Failed to start")
    assert mark.sizeHint().width() > wide
    qapp.processEvents()
    assert pixel(mark, mark.DOT // 2, mark.height() // 2) == window.look["danger"].upper()


def test_a_player_face_carries_the_players_name(qapp):
    window = gallery(qapp)
    alex, sam = only(window, parts.Face)
    assert (alex.accessibleName(), sam.accessibleName()) == ("Alex", "Sam")
    assert alex.size() == sam.size() and alex.width() == alex.height() == 22
    QtWidgets.QApplication.sendEvent(sam, QEvent(QEvent.Type.Enter))
    wait_for(qapp, lambda: parts.Bubble.words() == "Sam")
    # A plain grey head for everyone until there are real skins.
    assert pixel(alex, 11, 5) == window.look["neutral"].upper()
    assert alex.grab().toImage() == sam.grab().toImage()


# -------------------------------------------------------------- the whole kit
def test_every_part_has_a_name_and_can_be_reached_with_tab(qapp):
    window = gallery(qapp)
    controls = [
        w
        for kind in (QtWidgets.QAbstractButton, QtWidgets.QLineEdit, QtWidgets.QSlider)
        for w in window.findChildren(kind)
        if w.isVisibleTo(window)
        and w.isEnabled()
        # In a row where one choice is picked (the segmented control, the
        # colors) Tab goes to the picked one and the arrow keys to the rest.
        and not (getattr(w, "group", lambda: None)() and not w.isChecked())
    ]
    assert len(controls) >= 20
    for w in controls:
        name = w.accessibleName() or getattr(w, "text", lambda: "")()
        assert name.strip(), f"{type(w).__name__} {w.objectName()} has no accessible name"
        assert w.focusPolicy() & Qt.FocusPolicy.TabFocus, name


def test_a_raised_box_is_paper_with_the_servers_edge_and_a_shadow_under_it(qapp):
    window = gallery(qapp)
    box = only(window, parts.Box)[0]
    paper = box.paper()
    assert pixel(box, paper.center().x(), paper.top() + 6) == window.look["paper"].upper()
    edge = colors.rgb(pixel(box, paper.center().x(), paper.top() + 0.5))
    assert (
        colors.contrast(edge, colors.rgb(window.look["paper"])) >= 2.5
    )  # an anti-aliased 1.5 px line
    below = box.grab().toImage().pixelColor(int(paper.center().x()), int(paper.bottom()) + 3)
    assert below.alpha() > 0  # the shadow, outside the paper
    inside = box.contentsRect()
    assert inside.bottom() < paper.bottom() - 8 and inside.left() > paper.left() + 8


def test_a_part_that_is_off_is_drawn_faint_and_does_nothing(qapp):
    window = gallery(qapp)
    switch = next(s for s in only(window, parts.Switch) if s.isChecked())
    middle = switch.width() // 2 - 8, switch.height() // 2
    switch.setEnabled(False)
    qapp.processEvents()
    assert pixel(switch, *middle) != window.look["accent"].upper()
    QTest.mouseClick(switch, Qt.MouseButton.LeftButton)
    assert switch.isChecked()
    slider = window.memory
    slider.setEnabled(False)
    mouse(slider, QEvent.Type.MouseButtonPress, 10)
    assert slider.value() == 9
