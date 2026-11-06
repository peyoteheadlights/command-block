"""The desktop app's Store tab, against the real agent, the fake Tebex
and the fake Modrinth: who sees the tab, the Paper-or-Fabric message, and
connecting with the key, which is never shown again."""

from __future__ import annotations

import httpx
import pytest

from agent import downloads

from .test_desktop_app import open_server, open_window, page_tabs, shown_text, sign_in, wait_for
from .test_desktop_pages import open_page
from .test_store import KEY, FakeTebex
from .test_suggestions import NEW, PROJECTS, FakeModrinth

QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="PySide6 is in requirements-dev")


def community(agent_http, kind=None):
    ctx = agent_http.agent.app.state.core.servers["survival"]
    ctx.config.set("server.played_by", "community")
    if kind:
        ctx.config.set("server.type", kind)
        ctx.server.mc_version = NEW
        ctx.mods.modrinth = FakeModrinth(ctx.config.server_type)
        folder = ctx.config.mods_dir / "bStats"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "config.yml").write_text("enabled: true\n")
    return ctx


def survival(qapp, agent_http):
    window = open_window(qapp, agent_http)
    sign_in(qapp, window)
    wait_for(qapp, lambda: "Survival" in shown_text(window))
    window.set_mode(True)
    open_server(qapp, window, "Survival")
    return window


def test_the_store_tab_is_only_on_a_community_server(qapp, agent_http):
    window = survival(qapp, agent_http)
    assert "Store" not in page_tabs(window)
    window.close()
    community(agent_http).config.set("server.type", "vanilla")
    window = survival(qapp, agent_http)
    wait_for(qapp, lambda: "Store" in page_tabs(window))
    page = open_page(qapp, window, "Store")
    # Technical's words, since the Store tab is only in Technical.
    wait_for(qapp, lambda: "needs Paper, Purpur, Leaf or Fabric" in shown_text(window))
    assert not page.connect_button.isEnabled()


def test_connecting_the_store_from_the_desktop_app(qapp, agent_http, monkeypatch):
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(FakeTebex()))
    for slug in ("tebex", "luckperms"):
        monkeypatch.setitem(PROJECTS, slug, {"builds": {NEW: "5.0"}, "loaders": ["paper"]})
    community(agent_http, "paper")
    window = survival(qapp, agent_http)
    page = open_page(qapp, window, "Store")
    wait_for(qapp, lambda: "Not connected" in shown_text(window))
    assert "lp user {id} parent add supporter" in page.checklist.toPlainText()
    page.key.setText(KEY)
    page.connect_button.click()
    wait_for(qapp, lambda: "Plugin connected" in shown_text(window), 30)
    assert "https://survival-shop.tebex.io" in shown_text(window)
    assert page.key.text() == "" and KEY not in shown_text(window)
