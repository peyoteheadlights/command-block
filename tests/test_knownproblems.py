"""Diagnose server's known problems list (agent/knownproblems.py): built-in
ones, and new ones from the signed notes file that can only point at
changes the app already has."""

# ruff: noqa: F811
# (pytest fixtures from tests/test_diagnose.py: "core" is used by name)
from __future__ import annotations

import pytest

from agent import diagnose, diagnosefix, knownproblems, notes

from .test_diagnose import NOT_LISTED, core, diagnose_round, signed_in, stand_in  # noqa: F401
from .test_keeping_current import key, load, notes_body  # noqa: F401

TICKING = {
    "id": "ticking-entity",
    "where": "console",
    "pattern": r"Ticking entity",
    "say": "An entity in the world keeps crashing the server.",
    "link": "https://example.org/ticking-entity",
}
EULA = {
    "id": "eula-line",
    "where": "console",
    "pattern": r"agree to the EULA",
    "say": "The server waits for the EULA.",
    "change": "eula",
}


@pytest.fixture(autouse=True)
def no_notes_problems():
    yield
    knownproblems.FROM_NOTES.clear()


def test_problems_from_the_notes_file_are_read(key):
    found = load(notes_body(diagnose_problems=[TICKING, EULA]), key)
    assert [p.id for p in found.diagnose_problems] == ["ticking-entity", "eula-line"]
    assert found.diagnose_problems[1].change == "eula"


@pytest.mark.parametrize(
    "change",
    [
        {"change": "delete-world"},  # not a change the app has
        {"where": "network"},
        {"pattern": "(unclosed"},
        {"pattern": "x" * 201},
        {"link": "http://example.org/x"},
        {"say": "<b>run this</b>"},
    ],
)
def test_a_bad_problem_refuses_the_whole_file(key, change):
    with pytest.raises(notes.NotesRefused):
        load(notes_body(diagnose_problems=[{**TICKING, **change}]), key)


def test_a_notes_problem_cant_replace_a_built_in_one():
    knownproblems.FROM_NOTES[:] = [
        knownproblems.read({**TICKING, "id": "banned", "change": ""}),
        knownproblems.read(TICKING),
    ]
    ids = [p.id for p in knownproblems.problems()]
    assert ids.count("banned") == 1 and ids[-1] == "ticking-entity"
    assert next(p for p in knownproblems.problems() if p.id == "banned").change == "unban"


async def test_a_console_line_is_explained_and_its_change_offered(core):
    knownproblems.FROM_NOTES[:] = [knownproblems.read(TICKING), knownproblems.read(EULA)]
    ctx = core.get_server("survival")
    ctx.server.console.append("[Server thread/WARN]: Something before the run")
    out = diagnose.Run(ctx, "admin")
    ctx.server.console.append("[Server thread/ERROR]: Ticking entity at 10 64 -3")
    ctx.server.console.append("[Server thread/INFO]: You need to agree to the EULA")
    ctx.server.eula_required = True
    diagnosefix.offer(ctx, out.result)
    assert out.result["known"] == [
        {
            "id": "ticking-entity",
            "say": TICKING["say"],
            "link": TICKING["link"],
            "change": None,
        },
        {
            "id": "eula-line",
            "say": EULA["say"],
            "link": None,
            "change": "Accept the Minecraft EULA",
        },
    ]
    assert [c["key"] for c in out.result["changes"]] == ["eula"]


async def test_a_console_line_from_before_the_run_is_left_out(core):
    knownproblems.FROM_NOTES[:] = [knownproblems.read(TICKING)]
    ctx = core.get_server("survival")
    ctx.server.console.append("[Server thread/ERROR]: Ticking entity at 10 64 -3")
    out = diagnose.Run(ctx, "admin")
    diagnosefix.offer(ctx, out.result)
    assert out.result["known"] == []


async def test_a_kick_text_finds_the_built_in_whitelist_problem(core, monkeypatch, tmp_path):
    monkeypatch.setattr(diagnose, "LEAVE_GRACE", 0)
    stand_in(monkeypatch, tmp_path, NOT_LISTED)
    signed_in(core)
    ctx = core.get_server("survival")
    result = await diagnose_round(core, ctx)
    assert result["known"] == [
        {
            "id": "not-whitelisted",
            "say": "The server turned the account away because it isn't on the whitelist.",
            "link": None,
            "change": "Add Steve to the whitelist",
        }
    ]
