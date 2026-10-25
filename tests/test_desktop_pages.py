"""The desktop app's server pages (Players and chat, Console,
Performance, Schedules, Activity), driven like a person would against the
real agent with the fake Minecraft servers."""

from __future__ import annotations

import pytest

from .fake_pc import FakePC
from .test_desktop_app import (
    button,
    on_all_servers,
    open_server,
    open_window,
    owner,
    run_on_agent,
    server_state,
    shown_text,
    sign_in,
    signed_in_on,
    wait_for,
)

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


def running(qapp, agent_http, server_id="survival") -> dict:
    headers = owner(agent_http)
    run_on_agent(agent_http, server_id, "start", headers)
    wait_for(qapp, lambda: server_state(agent_http, server_id, headers) == "ONLINE", 30, 0.2)
    return headers


def command(agent_http, headers, words, server_id="survival") -> None:
    run_on_agent(agent_http, server_id, "command", headers, command=words, confirm=True)


def open_page(qapp, window, page):
    window.open_page(page)
    wait_for(qapp, lambda: window.server_screen.current_page == page)
    return window.server_screen.page


def buttons_named(widget, words) -> list:
    return [
        b
        for b in widget.findChildren(QtWidgets.QPushButton)
        if b.isVisibleTo(widget) and b.text() == words
    ]


# ---------------------------------------------------------------- Players and chat
def test_players_shows_who_is_online_and_kick_asks_then_is_confirmed(qapp, agent_http):
    headers = running(qapp, agent_http)
    command(agent_http, headers, "fakejoin Alex")
    window = signed_in_on(qapp, agent_http)
    page = open_page(qapp, window, "Players and chat")
    wait_for(qapp, lambda: "Online now (1 of" in shown_text(page) and "Alex" in shown_text(page))
    asked = []

    def answer(title, body, yes, field):
        asked.append(title)
        return "be nice"

    window.ask = answer
    buttons_named(page, "Kick")[0].click()
    assert asked == ["Kick Alex?"]
    wait_for(qapp, lambda: "Kick Alex: Done" in shown_text(page), 20)
    wait_for(qapp, lambda: "Nobody is online" in shown_text(page), 20)


def test_cancelling_the_question_sends_nothing(qapp, agent_http):
    headers = running(qapp, agent_http)
    command(agent_http, headers, "fakejoin Alex")
    window = signed_in_on(qapp, agent_http)
    page = open_page(qapp, window, "Players and chat")
    wait_for(qapp, lambda: "Alex" in shown_text(page))
    window.ask = lambda *words: None
    buttons_named(page, "Ban")[0].click()
    qapp.processEvents()
    actions = agent_http.get("/api/servers/survival/players", headers=headers).json()["actions"]
    assert actions == []


def test_adding_a_name_whitelists_it_from_minecrafts_own_file(qapp, agent_http):
    running(qapp, agent_http)
    window = signed_in_on(qapp, agent_http)
    page = open_page(qapp, window, "Players and chat")
    wait_for(qapp, lambda: any(b.isEnabled() for b in buttons_named(page, "Add to whitelist")))
    name = page.findChild(QtWidgets.QLineEdit, "addName")
    name.setText("not a name!")
    button(page, "Add to whitelist").click()
    assert "isn't a Minecraft name" in shown_text(page)
    name.setText("Steve")
    button(page, "Add to whitelist").click()
    wait_for(qapp, lambda: "Add to whitelist Steve: Done" in shown_text(page), 20)
    wait_for(qapp, lambda: buttons_named(page, "Remove from whitelist"), 20)


def test_player_buttons_wait_for_the_server_to_run(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    page = open_page(qapp, window, "Players and chat")
    wait_for(qapp, lambda: buttons_named(page, "Add to whitelist"))
    assert "Start the server to use these buttons" in shown_text(page)
    assert not button(page, "Add to whitelist").isEnabled()
    assert "Start the server to chat." in shown_text(page)
    assert not page.send.isEnabled()


def test_chat_shows_what_players_say_live_and_sends_as_server(qapp, agent_http):
    headers = running(qapp, agent_http)
    window = signed_in_on(qapp, agent_http)
    page = open_page(qapp, window, "Players and chat")
    wait_for(qapp, lambda: page.send.isEnabled())
    command(agent_http, headers, "fakechat Alex hello there")
    wait_for(qapp, lambda: "Alex: hello there" in page.chat.toPlainText())
    page.message.setText("welcome")
    page.send.click()
    wait_for(qapp, lambda: "Server: welcome" in page.chat.toPlainText())
    # Same as the console: cleared when the agent answers, maybe after the line.
    wait_for(qapp, lambda: page.message.text() == "")


def test_chat_lines_from_before_the_page_opened_are_loaded(qapp, agent_http):
    headers = running(qapp, agent_http)
    command(agent_http, headers, "fakechat Alex said earlier")
    wait_for(
        qapp,
        lambda: agent_http.get("/api/servers/survival/chat", headers=headers).json()["messages"],
    )
    window = signed_in_on(qapp, agent_http)
    page = open_page(qapp, window, "Players and chat")
    wait_for(qapp, lambda: "Alex: said earlier" in page.chat.toPlainText())


def test_a_moderator_without_op_gets_no_op_buttons(qapp, agent_http):
    headers = running(qapp, agent_http)
    command(agent_http, headers, "fakejoin Alex")
    made = agent_http.post(
        "/api/accounts",
        json={
            "username": "sam",
            "password": "moderator password",
            "servers": ["survival"],
            "permissions": ["server.view", "players.manage", "console.read"],
        },
        headers=headers,
    )
    assert made.status_code == 200, made.text
    window = open_window(qapp, agent_http)
    sign_in(qapp, window, "sam", "moderator password")
    wait_for(qapp, lambda: window.role == "helper" and "Survival" in shown_text(window))
    open_server(qapp, window, "Survival")
    page = open_page(qapp, window, "Players and chat")
    wait_for(qapp, lambda: "Alex" in shown_text(page) and buttons_named(page, "Kick"))
    assert not buttons_named(page, "Make op")
    assert not page.message.isVisible()  # no chat.send


# ---------------------------------------------------------------- Console
def technical_page(qapp, agent_http, page, pc=None):
    window = signed_in_on(qapp, agent_http, pc=pc)
    window.set_mode(True)
    wait_for(qapp, lambda: window.feed.connected)  # or live lines can come before it
    return window, open_page(qapp, window, page)


def test_the_console_shows_output_live_and_sends_commands(qapp, agent_http):
    running(qapp, agent_http)
    window, page = technical_page(qapp, agent_http, "Console")
    wait_for(qapp, lambda: "Done" in page.log.toPlainText() or page.lines)
    page.command.setText("hello world")
    page.send_button.click()
    wait_for(qapp, lambda: "[Console] hello world" in page.log.toPlainText())
    # The box is cleared when the agent answers, which can come after the line.
    wait_for(qapp, lambda: page.command.text() == "")


def test_a_dangerous_command_asks_first(qapp, agent_http):
    running(qapp, agent_http)
    window, page = technical_page(qapp, agent_http, "Console")
    asked = []
    window.confirm = lambda title, body, yes: asked.append(title) or False
    page.command.setText("op Alex")
    page.send_button.click()
    wait_for(qapp, lambda: asked == ["Run op Alex?"])
    assert page.command.text() == "op Alex"  # not sent


def test_the_filter_shows_only_matching_lines(qapp, agent_http):
    headers = running(qapp, agent_http)
    window, page = technical_page(qapp, agent_http, "Console")
    command(agent_http, headers, "apple pie")
    command(agent_http, headers, "banana split")
    wait_for(qapp, lambda: "banana split" in page.log.toPlainText())
    page.filter.setText("apple")
    assert "apple pie" in page.log.toPlainText()
    assert "banana" not in page.log.toPlainText()
    command(agent_http, headers, "banana bread")
    command(agent_http, headers, "apple tart")
    wait_for(qapp, lambda: "apple tart" in page.log.toPlainText())
    assert "banana" not in page.log.toPlainText()


def test_save_writes_the_shown_lines_with_windows_save_box(qapp, agent_http):
    pc = FakePC()
    headers = running(qapp, agent_http)
    window, page = technical_page(qapp, agent_http, "Console", pc)
    command(agent_http, headers, "save me")
    wait_for(qapp, lambda: "[Console] save me" in page.log.toPlainText())
    button(page, "Save as .log").click()
    [(name, data)] = pc.files.items()
    assert name.startswith("console-") and name.endswith(".log")
    assert b"[Console] save me" in data
    assert "Saved: C:/Users/test/Downloads/console-" in shown_text(page)


def test_lines_only_reach_the_console_while_it_is_open(qapp, agent_http):
    headers = running(qapp, agent_http)
    window, page = technical_page(qapp, agent_http, "Console")
    wait_for(qapp, lambda: page.lines)
    window.open_page("Overview")
    count = len(page.lines)
    seen = []  # the feed still brings the line; the Console just doesn't take it
    window.feed.message.connect(lambda m: seen.append(str(m)))
    command(agent_http, headers, "nobody is looking")
    wait_for(qapp, lambda: any("nobody is looking" in m for m in seen))
    assert len(page.lines) == count


def test_clear_empties_the_console(qapp, agent_http):
    headers = running(qapp, agent_http)
    window, page = technical_page(qapp, agent_http, "Console")
    command(agent_http, headers, "something to clear")
    wait_for(qapp, lambda: "[Console] something to clear" in page.log.toPlainText())
    button(page, "Clear").click()
    wait_for(qapp, lambda: page.log.toPlainText() == "")
    logs = agent_http.get("/api/servers/survival/logs", headers=headers).json()["lines"]
    assert not any("something to clear" in line["raw"] for line in logs)


# ---------------------------------------------------------------- Performance
def add_samples(agent_http, server_id="survival", minutes=50):
    """Made-up samples in the agent's database, a minute apart."""
    import time

    core = agent_http.agent.app.state.core
    now = time.time()
    for minute in range(minutes):
        core.db.insert(
            "metrics",
            {
                "server_id": server_id,
                "ts": now - minute * 60,
                "cpu_percent": 30.0,
                "tps": 19.5,
                "mspt": 21.0,
                "proc_ram_mb": 2048.0,
                "players": 2,
                "ram_used_mb": 6000.0,
                "ram_total_mb": 16000.0,
            },
        )


def test_performance_draws_every_graph_from_the_agents_samples(qapp, agent_http):
    add_samples(agent_http)
    window, page = technical_page(qapp, agent_http, "Performance")
    wait_for(qapp, lambda: page.graphs["tps"].points)
    for key in ("cpu_percent", "proc_ram_mb", "tps", "players", "mspt", "ram_used_mb"):
        assert any(v is not None for _t, v in page.graphs[key].points), key
    assert "19.5" in page.graphs["tps"].accessibleDescription()
    assert page.graphs["mspt"].guide == (50.0, "50 ms budget")
    shown = [v for _t, v in page.graphs["proc_ram_mb"].points if v is not None]
    assert shown[-1] == 2.0  # MB shown as GB


def test_a_wider_range_asks_the_agent_for_more_hours(qapp, agent_http):
    add_samples(agent_http)
    window, page = technical_page(qapp, agent_http, "Performance")
    wait_for(qapp, lambda: page.graphs["tps"].points)
    button(page, "7d").click()
    wait_for(qapp, lambda: page.graphs["tps"].end - page.graphs["tps"].start > 160 * 3600)


def test_a_stopped_server_shows_unknown_never_a_number(qapp, agent_http):
    window, page = technical_page(qapp, agent_http, "Performance")
    wait_for(qapp, lambda: page.tiles["server_memory"][0].text())
    assert page.tiles["server_memory"][0].text() == "Unknown"
    assert page.tiles["server_memory"][1].text() == "Read while running"
    assert "No samples" in shown_text(page) or "No TPS samples" in shown_text(page)


def test_speed_readings_say_how_tps_is_read_and_can_change_it(qapp, agent_http):
    running(qapp, agent_http)
    window, page = technical_page(qapp, agent_http, "Performance")
    wait_for(qapp, lambda: "Command: tick query" in shown_text(page), 30)
    window.pick = lambda title, body, choices: choices.index("Off")
    button(page, "Change command").click()
    wait_for(qapp, lambda: "Detection: Disabled" in shown_text(page))
    headers = owner(agent_http)
    assert agent_http.get("/api/servers/survival/tps", headers=headers).json()["mode"] == "disabled"


def test_the_health_checks_show_with_their_overall_result(qapp, agent_http):
    window, page = technical_page(qapp, agent_http, "Performance")
    wait_for(qapp, lambda: "VERIFIED" in shown_text(page) or "ATTENTION" in shown_text(page))


# ---------------------------------------------------------------- Schedules
def row_of(page, name):
    """The schedule's row on the page, found by its name."""
    return next(
        r
        for r in page.findChildren(QtWidgets.QFrame)
        if r.property("kind") == "row" and name in shown_text(r)
    )


def schedules_page(qapp, agent_http):
    window = signed_in_on(qapp, agent_http)
    page = open_page(qapp, window, "Schedules")
    wait_for(qapp, lambda: buttons_named(page, "Run now"))
    return window, page


def test_schedules_list_the_agents_schedules_in_plain_words(qapp, agent_http):
    window, page = schedules_page(qapp, agent_http)
    nightly = shown_text(row_of(page, "Nightly backup"))
    assert "Back up" in nightly and "Every day at 23:00" in nightly
    assert "Next run: Off" in shown_text(row_of(page, "Weekly restart"))


def test_adding_a_schedule_shows_it_and_coming_up_follows(qapp, agent_http):
    window, page = schedules_page(qapp, agent_http)
    page.name.setText("Morning restart")
    page.task.setCurrentIndex(page.tasks.index("restart"))
    page.expr.setText("07:30")
    page.save.click()
    wait_for(qapp, lambda: "Morning restart" in shown_text(page))
    wait_for(qapp, lambda: "Morning restart" in shown_text(window.server_screen.panel))
    assert page.name.text() == ""  # the form is ready for the next one


def test_a_bad_time_shows_the_agents_words_in_the_form(qapp, agent_http):
    window, page = schedules_page(qapp, agent_http)
    page.expr.setText("tea time")
    page.save.click()
    wait_for(qapp, lambda: page.problem.isVisible())
    assert "23:00" in page.problem.text()  # "A daily schedule needs a time like 23:00"


def test_editing_a_schedule_changes_it_and_keeps_it_on(qapp, agent_http):
    window, page = schedules_page(qapp, agent_http)
    buttons_named(row_of(page, "Nightly backup"), "Change")[0].click()
    assert page.expr.text() == "23:00" and page.cancel.isVisible()
    page.expr.setText("22:15")
    page.save.click()
    wait_for(qapp, lambda: "Every day at 22:15" in shown_text(page))
    assert "Next run: Off" not in shown_text(row_of(page, "Nightly backup"))


def test_turning_a_schedule_off_and_on(qapp, agent_http):
    window, page = schedules_page(qapp, agent_http)
    buttons_named(row_of(page, "Nightly backup"), "Turn off")[0].click()
    wait_for(qapp, lambda: "Next run: Off" in shown_text(row_of(page, "Nightly backup")))
    wait_for(qapp, lambda: "Nightly backup" not in shown_text(window.server_screen.panel))
    buttons_named(row_of(page, "Nightly backup"), "Turn on")[0].click()
    wait_for(qapp, lambda: "Next run: Off" not in shown_text(row_of(page, "Nightly backup")))


def test_run_now_runs_the_task_and_says_what_happened(qapp, agent_http):
    window, page = schedules_page(qapp, agent_http)
    buttons_named(row_of(page, "Log cleanup"), "Run now")[0].click()
    wait_for(qapp, lambda: "Removed 0 old log file(s)" in page.note.text())


def test_delete_asks_first(qapp, agent_http):
    window, page = schedules_page(qapp, agent_http)
    asked = []
    window.confirm = lambda title, body, yes: asked.append(title) or False
    buttons_named(row_of(page, "Log cleanup"), "Delete")[0].click()
    assert asked == ["Delete Log cleanup?"]
    window.confirm = lambda title, body, yes: True
    buttons_named(row_of(page, "Log cleanup"), "Delete")[0].click()
    wait_for(qapp, lambda: "Log cleanup" not in shown_text(page))


def test_a_moderator_without_schedules_manage_only_sees_the_list(qapp, agent_http):
    headers = owner(agent_http)
    made = agent_http.post(
        "/api/accounts",
        json={
            "username": "sam",
            "password": "moderator password",
            "servers": ["survival"],
            "permissions": ["server.view"],
        },
        headers=headers,
    )
    assert made.status_code == 200, made.text
    window = open_window(qapp, agent_http)
    sign_in(qapp, window, "sam", "moderator password")
    wait_for(qapp, lambda: window.role == "helper" and "Survival" in shown_text(window))
    open_server(qapp, window, "Survival")
    page = open_page(qapp, window, "Schedules")
    wait_for(qapp, lambda: "Nightly backup" in shown_text(page))
    assert not buttons_named(page, "Run now")
    assert not page.form.isVisible()


# ---------------------------------------------------------------- Activity
def add_crash(agent_http, confidence, category, server_id="survival", mods=()):
    """A crash row as the agent records one, for causes the fake server can't make."""
    import json
    import time

    analysis = {
        "category": category,
        "confidence": confidence,
        "summary": f"{category} summary",
        "advice": "",
        "evidence": ["line one from the log"],
        "suspect_mods": list(mods),
    }
    return agent_http.agent.app.state.core.db.insert(
        "crashes",
        {
            "server_id": server_id,
            "ts": time.time(),
            "exit_code": 1,
            "category": category,
            "confidence": confidence,
            "summary": analysis["summary"],
            "evidence": json.dumps(analysis["evidence"]),
            "context": json.dumps({"analysis": analysis, "players_online": []}),
            "restarted": 0,
        },
    )


def activity(qapp, window):
    window.server_screen.panel.see_all.click()
    wait_for(qapp, lambda: window.server_screen.current_page == "Activity")
    return window.server_screen.page


def test_see_all_opens_activity_with_plain_sentences_by_day(qapp, agent_http):
    headers = running(qapp, agent_http)
    command(agent_http, headers, "fakejoin Alex")
    window = signed_in_on(qapp, agent_http)
    page = activity(qapp, window)
    wait_for(qapp, lambda: "Alex joined." in shown_text(page))
    assert "Today" in shown_text(page) and "The server came online." in shown_text(page)
    assert "server_started" not in shown_text(page)  # raw types are Technical
    window.set_mode(True)
    page.load()
    wait_for(qapp, lambda: "server_started" in shown_text(page))
    assert window.server_screen.current_page == "Activity"  # the mode switch kept it open


def test_the_crashes_filter_shows_what_the_analyzer_found(qapp, agent_http):
    headers = running(qapp, agent_http)
    command(agent_http, headers, "crash")
    wait_for(  # the agent has recorded it
        qapp,
        lambda: agent_http.get("/api/servers/survival/crashes", headers=headers).json()["crashes"],
        30,
        0.2,
    )
    window = signed_in_on(qapp, agent_http)
    page = activity(qapp, window)
    button(page, "Crashes").click()
    wait_for(qapp, lambda: "The server ran out of memory" in shown_text(page), 30)
    assert "Certain" in shown_text(page)
    assert "couldn't tell for sure" not in shown_text(page)
    button(page, "Details").click()
    wait_for(qapp, lambda: "java.lang.OutOfMemoryError" in shown_text(page))


def test_an_unclear_crash_says_what_was_found_and_offers_help(qapp, agent_http):
    add_crash(agent_http, "possible", "WorldChunkError")
    window = signed_in_on(qapp, agent_http)
    window.open_activity(crashes=True)
    page = window.server_screen.page
    wait_for(qapp, lambda: "couldn't tell for sure" in shown_text(page))
    assert "Possible" in shown_text(page) and buttons_named(page, "Get help file")


def test_a_crash_alert_opens_activity_on_the_crashes(qapp, agent_http):
    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    sign_in(qapp, window)
    on_all_servers(qapp, window)
    headers = running(qapp, agent_http)
    command(agent_http, headers, "crash")
    wait_for(qapp, lambda: any("crashed" in title for title, _body in pc.alerts), 30)
    crashed = next(i for i, (title, _body) in enumerate(pc.alerts) if "crashed" in title)
    pc.click_alert(index=crashed)
    wait_for(qapp, lambda: window.server_screen.current_page == "Activity")
    wait_for(qapp, lambda: "The server ran out of memory" in shown_text(window.server_screen.page))


def test_get_help_saves_the_help_file_after_showing_what_is_in_it(qapp, agent_http):
    pc = FakePC()
    window = signed_in_on(qapp, agent_http, pc=pc)
    page = activity(qapp, window)
    shown = []
    window.confirm = lambda title, body, yes: shown.append(body) or True
    button(page, "Get help file").click()
    wait_for(qapp, lambda: pc.files, 30)
    assert "passwords" in shown[0]
    name, data = next(iter(pc.files.items()))
    assert name.startswith("command-block-help-") and data[:2] == b"PK"  # a zip
    wait_for(qapp, lambda: "Saved to" in page.note.text())


# ---------------------------------------------------------------- fix buttons
def core_of(agent_http):
    return agent_http.agent.app.state.core


def suggestion_button(qapp, window, words):
    overview = window.server_screen.simple_overview
    wait_for(qapp, lambda: buttons_named(overview, words), 20)
    return buttons_named(overview, words)[0]


def test_out_of_memory_suggests_more_memory_and_the_box_starts_1_gb_higher(qapp, agent_http):
    add_crash(agent_http, "confirmed", "OutOfMemoryError")
    window = signed_in_on(qapp, agent_http)
    overview = window.server_screen.simple_overview
    fix = suggestion_button(qapp, window, "Raise memory")
    assert "How sure: Certain" in shown_text(overview)
    limit = agent_http.get("/api/servers/survival/memory", headers=owner(agent_http)).json()
    started = []

    def answer(title, body, yes, field, value=""):
        started.append(value)
        return "3"

    window.ask = answer
    fix.click()
    wait_for(qapp, lambda: "Restart the server to use 3 GB" in overview.fixed.text())
    assert started == [f"{(limit['limit_mb'] + 1024) / 1024:g}"]
    jvm = core_of(agent_http).servers["survival"].config.server.jvm_args
    assert "-Xmx3G" in jvm


def test_the_eula_fix_asks_then_accepts(qapp, agent_http):
    add_crash(agent_http, "confirmed", "EulaNotAccepted")
    window = signed_in_on(qapp, agent_http)
    asked = []
    window.confirm = lambda title, body, yes: asked.append(title) or True
    suggestion_button(qapp, window, "Accept the EULA").click()
    overview = window.server_screen.simple_overview
    wait_for(qapp, lambda: "You can start the server now" in overview.fixed.text())
    assert asked == ["Accept Minecraft's EULA?"]
    folder = core_of(agent_http).servers["survival"].config.server_dir
    assert "eula=true" in (folder / "eula.txt").read_text()


def test_the_port_fix_offers_a_free_port(qapp, agent_http):
    add_crash(agent_http, "confirmed", "PortInUse")
    window = signed_in_on(qapp, agent_http)
    asked = []
    window.confirm = lambda title, body, yes: asked.append(title) or True
    suggestion_button(qapp, window, "Change port").click()
    overview = window.server_screen.simple_overview
    wait_for(qapp, lambda: "now uses port" in overview.fixed.text())
    port = int(asked[0].removeprefix("Use port ").removesuffix(" instead?"))
    folder = core_of(agent_http).servers["survival"].config.server_dir
    assert f"server-port={port}" in (folder / "server.properties").read_text()


def test_a_broken_mod_named_in_the_crash_is_turned_off_and_undo_turns_it_on(qapp, agent_http):
    from .test_mods import make_jar

    folder = core_of(agent_http).servers["survival"].config.server_dir / "mods"
    folder.mkdir(exist_ok=True)
    make_jar(folder / "sodium.jar", "sodium")
    add_crash(agent_http, "likely", "MixinError", mods=["sodium"])
    window = signed_in_on(qapp, agent_http)
    asked = []
    window.confirm = lambda title, body, yes: asked.append(title) or True
    suggestion_button(qapp, window, "Turn off").click()
    wait_for(qapp, lambda: (folder / "sodium.jar.disabled").exists(), 20)
    assert asked == ["Turn off Sodium?"]
    overview = window.server_screen.simple_overview
    wait_for(qapp, lambda: overview.fixed.undo.isVisible())
    overview.fixed.undo.click()
    wait_for(qapp, lambda: (folder / "sodium.jar").exists(), 20)
    assert not (folder / "sodium.jar.disabled").exists()


def test_install_the_right_java_opens_setup_only_on_the_agents_pc(qapp, agent_http):
    add_crash(agent_http, "confirmed", "JavaVersionIncompatible")
    pc = FakePC()
    window = signed_in_on(qapp, agent_http, pc=pc)
    suggestion_button(qapp, window, "Install Java").click()
    wait_for(qapp, lambda: pc.setups_opened == 1)
    window.agent.place = "100.64.0.7"  # a moderator's PC, reaching the agent over Tailscale
    suggestion_button(qapp, window, "Install Java").click()
    overview = window.server_screen.simple_overview
    wait_for(qapp, lambda: "on the PC the server runs on" in overview.fixed.text())
    assert pc.setups_opened == 1


def test_an_unclear_crash_suggests_the_help_file_not_a_fix(qapp, agent_http):
    add_crash(agent_http, "possible", "ServerThreadCrash")
    pc = FakePC()
    window = signed_in_on(qapp, agent_http, pc=pc)
    overview = window.server_screen.simple_overview
    window.confirm = lambda title, body, yes: True
    suggestion_button(qapp, window, "Get help file").click()
    assert "How sure: Possible" in shown_text(overview)
    wait_for(qapp, lambda: pc.files, 30)


def test_technical_shows_the_fix_on_the_newest_crash_in_activity(qapp, agent_http):
    add_crash(agent_http, "confirmed", "EulaNotAccepted")
    window = signed_in_on(qapp, agent_http)
    window.set_mode(True)
    window.open_activity(crashes=True)
    page = window.server_screen.page
    wait_for(qapp, lambda: buttons_named(page, "Accept the EULA"))
    assert not buttons_named(window.server_screen.technical_overview, "Accept the EULA")


def test_a_missing_dependency_fix_says_when_nothing_can_be_installed(qapp, agent_http):
    add_crash(agent_http, "likely", "MissingMod")
    window = signed_in_on(qapp, agent_http)
    suggestion_button(qapp, window, "Install it").click()
    overview = window.server_screen.simple_overview
    wait_for(qapp, lambda: "Nothing missing could be found" in overview.fixed.text())
