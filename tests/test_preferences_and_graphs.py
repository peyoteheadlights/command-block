"""Per-account display preferences (Simple or Technical, theme), and the
graph series behind the Performance page."""

import time

from agent.database.db import Database
from agent.events import EventBus
from agent.minecraft.process import MinecraftServer
from agent.monitoring.metrics import MetricsMonitor

from .conftest import PASSWORD


def signed_in(client):
    token = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    client.headers["Authorization"] = f"Bearer {token.json()['token']}"
    return client


# ------------------------------------------------------------------ preferences
def test_preferences_default_to_simple_and_follow_the_system(client):
    signed_in(client)
    assert client.get("/api/account/preferences").json() == {
        "mode": "simple",
        "theme": "system",
        "tips": "on",
    }
    assert client.get("/api/auth/me").json()["preferences"]["mode"] == "simple"


def test_preferences_are_saved_on_the_server(client):
    signed_in(client)
    saved = client.put("/api/account/preferences", json={"mode": "technical"})
    assert saved.json() == {"mode": "technical", "theme": "system", "tips": "on"}
    client.put("/api/account/preferences", json={"theme": "graphite"})
    # A second sign-in (another device) sees the same choices.
    del client.headers["Authorization"]
    signed_in(client)
    assert client.get("/api/account/preferences").json() == {
        "mode": "technical",
        "theme": "graphite",
        "tips": "on",
    }


def test_unknown_preference_values_are_refused(client):
    signed_in(client)
    assert client.put("/api/account/preferences", json={"mode": "expert"}).status_code == 422
    assert client.put("/api/account/preferences", json={"theme": "neon"}).status_code == 422
    assert client.get("/api/account/preferences").json()["mode"] == "simple"


def test_preferences_need_a_sign_in(client):
    assert client.get("/api/account/preferences").status_code == 401


# ------------------------------------------------------------------ first-run questions
def test_first_run_is_unanswered_until_the_owner_answers(client):
    signed_in(client)
    assert client.get("/api/account/welcome").json() == {
        "answered": False,
        "first_time": None,
        "kinds": [],
        "played_by": "",
    }


def test_a_first_timer_gets_simple_mode_and_tips(client):
    signed_in(client)
    saved = client.put(
        "/api/account/welcome", json={"first_time": True, "kinds": ["mods"], "played_by": "kids"}
    ).json()
    # Kinds are only asked after "No".
    assert saved == {"answered": True, "first_time": True, "kinds": [], "played_by": "kids"}
    prefs = client.get("/api/account/preferences").json()
    assert prefs["mode"] == "simple" and prefs["tips"] == "on"


def test_no_keeps_the_kinds_and_picks_technical_without_tips(client):
    signed_in(client)
    saved = client.put(
        "/api/account/welcome",
        json={"first_time": False, "kinds": ["bedrock", "vanilla"], "played_by": "community"},
    ).json()
    assert saved["kinds"] == ["vanilla", "bedrock"]
    prefs = client.get("/api/account/preferences").json()
    assert prefs["mode"] == "technical" and prefs["tips"] == "off"


def test_i_know_what_im_doing_is_technical_no_tips_nothing_set(client):
    signed_in(client)
    saved = client.put("/api/account/welcome", json={"first_time": None}).json()
    assert saved == {"answered": True, "first_time": None, "kinds": [], "played_by": ""}
    assert client.get("/api/account/preferences").json()["tips"] == "off"


def test_pc_settings_change_the_answers_but_not_the_mode(client):
    signed_in(client)
    client.put("/api/account/welcome", json={"first_time": True, "played_by": "kids"})
    saved = client.put(
        "/api/account/welcome",
        json={
            "first_time": None,
            "kinds": ["plugins"],
            "played_by": "friends",
            "set_defaults": False,
        },
    ).json()
    assert saved == {
        "answered": True,
        "first_time": True,
        "kinds": ["plugins"],
        "played_by": "friends",
    }
    assert client.get("/api/account/preferences").json()["mode"] == "simple"


def test_unknown_answers_are_refused(client):
    signed_in(client)
    assert client.put("/api/account/welcome", json={"kinds": ["spigot"]}).status_code == 422
    assert client.put("/api/account/welcome", json={"played_by": "everyone"}).status_code == 422
    assert client.put("/api/account/preferences", json={"tips": "maybe"}).status_code == 422


def test_the_installer_answers_for_the_owner_with_the_api_token(client, monkeypatch):
    # On an "Agent only" PC the installer asks the questions and sends them
    # with the API token; they are the owner's answers.
    monkeypatch.setenv("MCSC_API_TOKEN", "installer-token-123456")
    client.headers["Authorization"] = "Bearer installer-token-123456"
    client.put("/api/account/welcome", json={"first_time": False, "kinds": ["mods"]})
    signed_in(client)
    assert client.get("/api/account/welcome").json()["kinds"] == ["mods"]


# ------------------------------------------------------------------ kind tips
def _kind_tips(client):
    return client.get("/api/new-server/options").json()["kind_tips"]


def test_kind_tips_follow_the_first_run_answers(client):
    signed_in(client)
    client.put("/api/account/welcome", json={"first_time": True})
    assert _kind_tips(client) == ["vanilla", "mods", "plugins", "bedrock"]  # tips on
    client.put("/api/account/welcome", json={"first_time": False, "kinds": ["vanilla", "mods"]})
    assert _kind_tips(client) == ["plugins", "bedrock"]  # only kinds new to them
    client.put("/api/account/welcome", json={"first_time": None})
    assert _kind_tips(client) == []  # "I know what I'm doing"


def test_a_kinds_tips_show_once_after_no(client):
    signed_in(client)
    client.put("/api/account/welcome", json={"first_time": False, "kinds": ["vanilla"]})
    auth = client.app.state.core.auth
    auth.made_kind("admin", "plugins")  # a Paper server was made
    assert _kind_tips(client) == ["mods", "bedrock"]
    assert client.get("/api/account/welcome").json()["kinds"] == ["vanilla", "plugins"]


# ------------------------------------------------------------------ don't show again
def test_dont_show_again_is_kept_per_account_until_shown_again(client):
    signed_in(client)
    assert client.get("/api/auth/me").json()["hidden"] == []
    client.post("/api/account/hidden", json={"key": "new.farms_warning"})
    saved = client.post("/api/account/hidden", json={"key": "new.farms_warning"}).json()
    assert saved == {"hidden": ["new.farms_warning"]}
    client.put("/api/account/welcome", json={"first_time": True})
    client.post("/api/account/hidden", json={"key": "tip.kind.bedrock"})
    assert _kind_tips(client) == ["vanilla", "mods", "plugins"]
    assert client.delete("/api/account/hidden").json() == {"hidden": []}
    assert client.get("/api/auth/me").json()["hidden"] == []


def test_hidden_keys_must_be_words_keys(client):
    signed_in(client)
    assert client.post("/api/account/hidden", json={"key": "Not a key!"}).status_code == 422
    assert client.post("/api/account/hidden", json={"key": "x" * 81}).status_code == 422
    del client.headers["Authorization"]
    assert client.post("/api/account/hidden", json={"key": "a.b"}).status_code == 401


# ------------------------------------------------------------------ graph series
def _metrics(config):
    db = Database(config.database_path)
    db.register_server("test", "Test", str(config.server_dir))
    server = MinecraftServer(config, EventBus(), db)
    return db, MetricsMonitor(config, EventBus(), db, server)


def _sample(db, ts, **values):
    db.insert("metrics", {"server_id": "test", "ts": ts, **values})


def test_a_week_is_averaged_into_a_bounded_number_of_points(config):
    db, metrics = _metrics(config)
    now = time.time()
    for i in range(0, 7 * 24 * 60, 10):  # a sample every 10 minutes for a week
        _sample(db, now - i * 60, cpu_percent=50.0, tps=20.0)
    series = metrics.series(hours=168, points=200)
    assert 100 < len(series["points"]) <= 201
    assert series["bucket_seconds"] >= 168 * 3600 / 200
    assert all(p["cpu_percent"] == 50.0 for p in series["points"])


def test_unmeasured_values_stay_null_and_missing_time_stays_missing(config):
    db, metrics = _metrics(config)
    size = metrics.series(hours=1, points=360)["bucket_seconds"]
    start = (int(time.time() / size) - 50) * size  # the start of a bucket 50 buckets ago
    _sample(db, time.time() - 3500, cpu_percent=10.0, tps=None, proc_ram_mb=None)  # server off
    _sample(db, start + 0.1, cpu_percent=20.0, tps=19.5, proc_ram_mb=900.0)
    _sample(db, start + 0.2, cpu_percent=30.0, tps=18.5, proc_ram_mb=1100.0)
    series = metrics.series(hours=1, points=360)
    points = series["points"]
    # Two buckets with data; the time between them is not filled in.
    assert len(points) == 2
    assert points[0]["tps"] is None and points[0]["proc_ram_mb"] is None
    assert points[1]["tps"] == 19.0 and points[1]["tps_min"] == 18.5
    assert points[1]["proc_ram_max"] == 1100.0
    assert points[1]["ts"] - points[0]["ts"] > 10 * series["bucket_seconds"]


def test_the_performance_route_returns_the_series(client):
    signed_in(client)
    data = client.get("/api/servers/test/performance?hours=24").json()
    assert data["series"]["hours"] == 24
    assert "bucket_seconds" in data["series"]
    assert data["sample_interval"] > 0


def test_the_overview_can_skip_the_folder_walk(client, monkeypatch):
    signed_in(client)
    core = client.app.state.core
    walks = []
    for ctx in core.servers.values():
        monkeypatch.setattr(ctx.metrics, "storage_breakdown", lambda: walks.append(1) or {})
    data = client.get("/api/servers/test/performance?hours=1&storage=false").json()
    assert data["storage"] is None
    assert walks == []
