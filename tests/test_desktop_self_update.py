"""Updates on a moderator's PC: the desktop app checks its
own copy once a day, only installs a newer, signed release, and asks first."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import __version__
from desktop import self_update
from desktop.agent import AgentError

from .fake_pc import FakePC
from .test_desktop_app import open_window
from .test_updates import NEXT, SETUP_BYTES, github, key, release_json  # noqa: F401

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


@pytest.fixture
def connect_copy(tmp_path, monkeypatch):
    """This copy, installed by "Connect to someone else's PC"."""
    program = tmp_path / "Local" / "Programs" / "Command Block"
    program.mkdir(parents=True)
    record = {"version": __version__, "data_root": str(tmp_path / "data"), "connect_only": True}
    (program / "install.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv("MCSC_PROGRAM_DIR", str(program))
    return program


def test_only_a_copy_for_connecting_only_checks_by_itself(tmp_path, monkeypatch, key):  # noqa: F811
    assert not self_update.wanted()  # a checkout: no install record
    program = tmp_path / "Program Files" / "Command Block"
    program.mkdir(parents=True)
    (program / "install.json").write_text(json.dumps({"data_root": str(tmp_path)}))
    monkeypatch.setenv("MCSC_PROGRAM_DIR", str(program))
    assert not self_update.wanted()  # the owner's PC: its agent updates it
    (program / "install.json").write_text(
        json.dumps({"data_root": str(tmp_path), "connect_only": True})
    )
    assert self_update.wanted()


def test_once_a_day():
    assert self_update.due(None)
    assert not self_update.due("1000", now=1000 + 3600)
    assert self_update.due(1000.0, now=1000 + 24 * 3600)


def test_a_newer_signed_release_is_downloaded_and_checked(github, connect_copy, tmp_path):  # noqa: F811
    release = self_update.newer_release()
    assert release is not None and release.version == NEXT
    setup = self_update.download(release)
    assert setup.read_bytes() == SETUP_BYTES
    assert setup.parent == tmp_path / "data" / "updates" / NEXT


def test_never_an_older_release_or_one_that_doesnt_match_its_signature(github, connect_copy):  # noqa: F811
    github.release = release_json("0.0.1")
    assert self_update.newer_release() is None
    github.release = release_json()
    github.setup = b"MZ something else"
    release = self_update.newer_release()
    assert release is not None
    with pytest.raises(AgentError, match="couldn't be installed"):
        self_update.download(release)


def test_update_now_closes_the_app_and_runs_the_checked_setup(
    qapp,
    agent_http,
    connect_copy,
    monkeypatch,
):
    from agent import updates

    pc = FakePC()
    window = open_window(qapp, agent_http, pc)
    release = updates.parse_release(release_json())
    asked = []
    monkeypatch.setattr(window, "confirm", lambda *words, **kw: asked.append(kw) or False)
    window.own_update_found(release)
    assert asked == [{"no": "Later"}] and pc.updates_run == []
    assert pc.settings["update_checked_at"]  # Later: asked again tomorrow
    setup = Path("C:/Users/sam/AppData/Local/Command Block/updates/9.0.0/setup.exe")
    monkeypatch.setattr(window, "confirm", lambda *words, **kw: True)
    monkeypatch.setattr(self_update, "download", lambda r: setup)
    window.own_update_found(release)
    from .test_desktop_app import wait_for

    wait_for(qapp, lambda: pc.updates_run == [(str(setup), str(connect_copy))])
    assert pc.settings["reopen_after_update"] in ("shown", "hidden")
