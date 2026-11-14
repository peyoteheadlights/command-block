# ruff: noqa: F811  (the fixtures imported from other tests are used by name)
"""Sign in with Modrinth, and putting a server's modpack and own jars on it.

Our Firebase function, Modrinth's API and Modrinth's sign-in page are
stand-ins (FakeModrinthSite): no network. The function's own tests are
next to it (relay.test.js and modrinth.test.js), not in this repo yet.
"""

from __future__ import annotations

import json
import re
from email.parser import BytesParser
from email.policy import HTTP

import httpx
import pytest

from agent import downloads, modpackmake, modrinthshare, recommendations
from agent.security import permissions as P

from .test_curseforge import MC, RELAY, fabric, relay  # noqa: F401
from .test_modpack_make import OWN, api, make, server  # noqa: F401
from .test_own_jars import moderator

STATE = "A" * 22 + ".2000000000.phone." + "B" * 43


def form(request: httpx.Request) -> dict:
    """A multipart form's parts: name -> (filename, bytes)."""
    head = f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
    message = BytesParser(policy=HTTP).parsebytes(head + request.read())
    return {
        part.get_param("name", header="content-disposition"): (
            part.get_filename(),
            part.get_payload(decode=True),
        )
        for part in message.iter_parts()
    }


class FakeModrinthSite:
    """Our sign-in function (on the relay's host), and Modrinth's API."""

    def __init__(self, relay) -> None:
        self.relay = relay
        self.projects: list[dict] = []
        self.versions: list[dict] = []
        self.listed: str | None = None  # the Server Project's modpack version
        self.swaps = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == RELAY and url.path.startswith("/modrinth/"):
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if token not in self.relay.tokens:
                return httpx.Response(401, json={"error": "sign in first"})
            if url.path == "/modrinth/start":
                assert re.fullmatch(r"phone|pc[1-9][0-9]{0,4}", url.params["to"])
                state = STATE.replace(".phone.", f".{url.params['to']}.")
                page = f"https://modrinth.com/auth/authorize?client_id=x&state={state}"
                return httpx.Response(200, json={"url": page, "state": state})
            body = json.loads(request.content)
            if body["code"] != "good-code":
                return httpx.Response(400, json={"error": "Modrinth said no"})
            self.swaps += 1
            return httpx.Response(200, json={"access_token": "mro_secret", "expires_in": 1209600})
        if url.host != "api.modrinth.com":
            return self.relay(request)
        signed_in = request.headers.get("authorization") == "mro_secret"
        if url.path == "/v2/user":
            assert signed_in
            return httpx.Response(200, json={"username": "mark"})
        if m := re.fullmatch(r"/v2/project/([\w-]+)/check", url.path):
            taken = m.group(1) == "taken" or any(p["slug"] == m.group(1) for p in self.projects)
            return httpx.Response(200, json={"id": "x"}) if taken else httpx.Response(404)
        if url.path == "/v2/project":
            assert signed_in and request.method == "POST"
            data = json.loads(form(request)["data"][1])
            data["id"] = f"proj{len(self.projects) + 1}"
            self.projects.append(data)
            return httpx.Response(200, json={"id": data["id"], "slug": data["slug"]})
        if url.path == "/v2/version":
            assert signed_in and request.method == "POST"
            parts = form(request)
            data = json.loads(parts["data"][1])
            data["file"] = parts["file"]
            data["id"] = f"ver{len(self.versions) + 1}"
            self.versions.append(data)
            return httpx.Response(200, json={"id": data["id"]})
        if url.path == "/v3/project/our-server":
            return httpx.Response(
                200,
                json={
                    "id": "srv1",
                    "slug": "our-server",
                    "project_types": ["minecraft_java_server"],
                    "minecraft_java_server": {
                        "address": "play.example.org",
                        "content": {"kind": "modpack", "version_id": self.listed},
                    },
                },
            )
        if url.path == "/v3/project/a-mod":
            return httpx.Response(200, json={"id": "m1", "slug": "a-mod", "project_types": ["mod"]})
        raise AssertionError(f"unexpected request to {url}")


@pytest.fixture
def site(server, relay, monkeypatch):
    fake = FakeModrinthSite(relay)
    monkeypatch.setattr(downloads, "TRANSPORT", httpx.MockTransport(fake))
    return fake


def signed_in(client, headers):
    start = client.post("/api/modrinth/sign-in", json={"return_to": "phone"}, headers=headers)
    assert start.status_code == 200, start.text
    assert start.json()["url"].startswith("https://modrinth.com/auth/authorize?")
    state = re.search(r"state=([^&]+)", start.json()["url"]).group(1)
    done = client.post(
        "/api/modrinth/sign-in/finish", json={"code": "good-code", "state": state}, headers=headers
    )
    assert done.status_code == 200, done.text
    return done.json()


def card(client, headers) -> dict:
    got = client.get(api(client, "modrinth"), headers=headers)
    assert got.status_code == 200, got.text
    return got.json()


def upload(client, headers, **body):
    return client.post(api(client, "modrinth/upload"), json=body, headers=headers)


# ---------------------------------------------------------------- signing in
def test_sign_in_keeps_the_token_on_this_pc_and_never_shows_it(server, site):
    client, headers = server
    assert card(client, headers)["account"] == {
        "available": True,
        "signed_in": False,
        "username": None,
        "ran_out": False,
    }
    account = signed_in(client, headers)
    assert account["signed_in"] and account["username"] == "mark"
    core = client.app.state.core
    assert core.modrinth.token() == "mro_secret"
    assert (core.config.data_dir / modrinthshare.KEY_FILE).is_file()
    for answer in (account, card(client, headers)):
        assert "mro_secret" not in json.dumps(answer)

    out = client.post("/api/modrinth/sign-out", headers=headers).json()
    assert out["signed_in"] is False and core.modrinth.token() is None


def test_a_sign_in_is_finished_only_once_and_only_by_who_started_it(server, site):
    client, headers = server
    # Never started here (a code caught on its way to another app).
    caught = client.post(
        "/api/modrinth/sign-in/finish", json={"code": "good-code", "state": STATE}, headers=headers
    )
    assert caught.status_code == 400 and "Sign in again" in caught.json()["detail"]
    start = client.post("/api/modrinth/sign-in", json={"return_to": "pc50123"}, headers=headers)
    state = re.search(r"state=([^&]+)", start.json()["url"]).group(1)
    bad = client.post(
        "/api/modrinth/sign-in/finish", json={"code": "bad-code", "state": state}, headers=headers
    )
    assert bad.status_code == 400 and "Sign in again" in bad.json()["detail"]
    again = client.post(
        "/api/modrinth/sign-in/finish", json={"code": "good-code", "state": state}, headers=headers
    )
    assert again.status_code == 400  # the state was used up
    assert site.swaps == 0
    for odd in ("pc0", "pc99999", "https://evil.example", ""):
        refused = client.post("/api/modrinth/sign-in", json={"return_to": odd}, headers=headers)
        assert refused.status_code in (400, 422), odd


def test_only_the_owner_signs_in_and_out(server, site):
    client, owner = server
    sam = moderator(client, owner, {P.MODS_MANAGE})
    assert P.PC_SETTINGS not in P.SERVER_PERMISSIONS
    refused = client.post("/api/modrinth/sign-in", json={"return_to": "phone"}, headers=sam)
    assert refused.status_code == 403
    assert client.post("/api/modrinth/sign-out", headers=sam).status_code == 403
    assert card(client, sam)["account"]["signed_in"] is False


# ---------------------------------------------------------------- uploading
def test_the_modpack_goes_up_as_a_draft_then_as_new_versions(server, site):
    client, headers = server
    assert "Sign in with Modrinth first" in upload(client, headers).json()["detail"]
    signed_in(client, headers)
    assert "Make a modpack first" in upload(client, headers).json()["detail"]
    make(client, headers)
    made = modpackmake.made(next(iter(client.app.state.core.servers.values())))

    assert "summary" in upload(client, headers, slug="our-pack").json()["detail"]
    taken = upload(client, headers, slug="taken", summary="Our friends' pack")
    assert taken.status_code == 400 and "taken" in taken.json()["detail"]
    assert "small letters" in upload(client, headers, slug="Bad Name!", summary="x" * 5).text
    assert site.projects == []

    first = upload(client, headers, slug="our-pack", summary="Our friends' pack")
    assert first.status_code == 200, first.text
    project = site.projects[0]
    assert project["project_type"] == "modpack" and project["is_draft"] is True
    assert project["slug"] == "our-pack" and project["title"] == "Friends"
    assert project["license_id"] == modrinthshare.LICENSE
    version = site.versions[0]
    assert version["project_id"] == "proj1" and version["version_number"] == "1"
    assert version["loaders"] == ["fabric"] and version["game_versions"] == [MC]
    path = modpackmake.file_path(next(iter(client.app.state.core.servers.values())), made["token"])
    assert version["file"] == ("Friends 1.mrpack", path.read_bytes())
    state = first.json()
    assert state["project"]["url"] == "https://modrinth.com/modpack/our-pack"
    assert [u["version"] for u in state["uploads"]] == ["1"]

    make(client, headers, version="2")
    second = upload(client, headers)
    assert second.status_code == 200, second.text
    assert len(site.projects) == 1 and site.versions[1]["project_id"] == "proj1"
    assert [u["version"] for u in second.json()["uploads"]] == ["2", "1"]


def test_an_own_jar_can_go_up_as_its_own_mod(server, site):
    client, headers = server
    signed_in(client, headers)
    make(client, headers)
    jars = card(client, headers)["own_jars"]
    assert [j["name"] for j in jars] == ["Ourtweaks"] and jars[0]["slug"] is None
    path = jars[0]["path"]
    body = {"path": path, "slug": "our-tweaks", "summary": "Small tweaks for our server"}
    done = client.post(api(client, "modrinth/own-jar"), json=body, headers=headers)
    assert done.status_code == 200, done.text
    project = site.projects[0]
    assert project["project_type"] == "mod" and project["is_draft"] is True
    assert (project["client_side"], project["server_side"]) == ("required", "required")
    assert site.versions[0]["version_number"] == "1.0"
    assert site.versions[0]["file"] == ("ourtweaks.jar", OWN)
    assert done.json()["own_jars"][0]["url"] == "https://modrinth.com/mod/our-tweaks"
    again = client.post(api(client, "modrinth/own-jar"), json=body, headers=headers)
    assert again.status_code == 400 and "already on Modrinth" in again.json()["detail"]
    for odd in ("../outside.jar", "mods/sodium.jar", "server.properties"):
        refused = client.post(
            api(client, "modrinth/own-jar"), json={**body, "path": odd}, headers=headers
        )
        assert refused.status_code == 400, odd


def test_an_own_jar_needs_add_own_jar_too(server, site):
    client, owner = server
    signed_in(client, owner)
    make(client, owner)
    sam = moderator(client, owner, {P.MODS_MANAGE})
    body = {"path": "mods/ourtweaks.jar", "slug": "our-tweaks", "summary": "Small tweaks"}
    refused = client.post(api(client, "modrinth/own-jar"), json=body, headers=sam)
    assert refused.status_code == 403 and "Add own .jar" in refused.json()["detail"]
    assert site.projects == []


# ---------------------------------------------------------------- the Server Project
def test_older_on_modrinth_only_when_it_lists_an_earlier_upload_of_ours(server, site):
    client, headers = server
    signed_in(client, headers)
    make(client, headers)
    upload(client, headers, slug="our-pack", summary="Our friends' pack")
    make(client, headers, version="2")
    upload(client, headers)

    def link(value):
        return client.post(
            api(client, "modrinth/server-project"), json={"link": value}, headers=headers
        )

    assert "isn't a Modrinth Server Project" in link("https://modrinth.com/mod/a-mod").text
    assert "Paste the Server Project's" in link("https://evil.example/server/x").text
    site.listed = "ver1"  # the first upload
    state = link("https://modrinth.com/server/our-server").json()
    assert state["server_project"]["url"] == "https://modrinth.com/server/our-server"
    assert state["older"] == {"listed": "1", "newest": "2"}
    ctx = next(iter(client.app.state.core.servers.values()))
    found = {r.id: r for r in recommendations.evaluate(recommendations.gather(ctx))}
    assert "version 1" in found["older_on_modrinth"].evidence

    site.listed = "ver2"
    assert link("modrinth.com/server/our-server").json()["older"] is None
    site.listed = "someone-elses"
    assert link("our-server").json()["older"] is None  # not ours: nothing to compare
    assert link("").json()["server_project"] is None
