"""Where each server type's versions come from, and what to download.

One provider per type. Each one answers two questions:

    versions(type_id)  which Minecraft versions this type offers, newest
                       first, each marked stable or snapshot
    plan(type_id, minecraft_version, loader_version)  what to download and
                       install for that version

Everything is read from the type's own official source through the safe
downloader (agent/downloads.py). Nothing is guessed: when a source does not
publish a checksum, the download is recorded as unverified rather than
claimed as checked, and when a version is not in the list the source
returned, it is refused.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

from .. import downloads, savedlists
from ..downloads import DownloadError, FileSpec
from . import ServerType, get

log = logging.getLogger("msc.versions")

MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
FABRIC_META = "https://meta.fabricmc.net/v2"
QUILT_META = "https://meta.quiltmc.org/v3"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"
FILL_API = "https://fill.papermc.io/v3/projects"
PAPER_API = f"{FILL_API}/paper"
PURPUR_API = "https://api.purpurmc.org/v2/purpur"
# Leaf's API has the shape of PaperMC's older v2 API.
LEAF_API = "https://api.leafmc.one/v2/projects/leaf"

VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+\-]{0,63}$")
# Words in a version name that mark it as not finished yet.
UNSTABLE_RE = re.compile(r"(alpha|beta|pre|rc|snapshot)", re.IGNORECASE)
# Forge only writes the argument file the app launches from (and run.bat)
# from Minecraft 1.17 on; older Forge servers are launched another way.
FORGE_OLDEST = (1, 17)


def version_key(version: str) -> tuple:
    """Sort key for version names like "1.21.1", "47.3.0" or "21.1.9-beta":
    numbers compared as numbers, a finished version above its pre-releases."""
    head, _, tail = str(version).partition("-")
    numbers = tuple(int(part) if part.isdigit() else -1 for part in head.split("."))
    return (numbers, 0 if tail else 1, tail)


class VersionError(DownloadError):
    """A version could not be listed or is not offered by its source."""


@dataclass
class Version:
    """One Minecraft version a type offers."""

    minecraft: str
    stable: bool = True
    # The loader or build versions offered for it, newest first, when the
    # type picks one separately. Empty: the provider picks.
    loaders: list[str] = field(default_factory=list)
    released: str | None = None
    # False for Bedrock: no Java involved.
    java: bool = True
    # Bedrock: a copy this app downloaded earlier is kept, and which one is
    # the newest the official source offers now.
    kept: bool = False
    latest: bool = False

    def to_dict(self) -> dict[str, Any]:
        from ..minecraft.java import required_java

        return {
            "minecraft": self.minecraft,
            "stable": self.stable,
            # The oldest Java this Minecraft version runs on, so the New
            # server panel can say so before anything is created. None for
            # a version whose number can't be read (a snapshot name), and
            # for Bedrock, which doesn't use Java.
            "java_required": required_java(self.minecraft) if self.java else None,
            "loaders": self.loaders,
            "released": self.released,
            "kept": self.kept,
            "latest": self.latest,
        }


@dataclass
class Download:
    """One file to fetch as part of setting a version up."""

    spec: FileSpec
    # What it is: "server" (the launchable jar), "installer" (run once with
    # --installServer), or "library".
    role: str = "server"


@dataclass
class Plan:
    """Everything needed to put one version of one type in place."""

    type_id: str
    minecraft: str
    loader: str | None
    downloads: list[Download]
    # The jar the server is launched from afterwards, as the app names it.
    jar: str = ""
    # Set for Forge and NeoForge: run this installer with --installServer.
    installer: str | None = None
    source: str = ""
    verified: bool = True
    # Bedrock: the zip the server is unpacked from (kept in the data folder).
    archive: str | None = None
    # False when the build picked isn't marked finished (no stable build yet).
    stable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_id,
            "minecraft_version": self.minecraft,
            "loader_version": self.loader,
            "jar": self.jar,
            "installer": self.installer,
            "files": [d.spec.name for d in self.downloads],
            "source": self.source,
            "verified": self.verified,
        }


def check_version(value: Any, what: str = "version") -> str:
    """A version string from a request, kept to what version names look
    like, before it is ever used in a URL or a file name."""
    text = str(value or "").strip()
    if not VERSION_RE.match(text):
        raise VersionError(f"'{text}' isn't a {what} this app can use.")
    return text


def _is_list(data: Any) -> bool:
    return isinstance(data, list)


def _is_dict(data: Any) -> bool:
    return isinstance(data, dict)


# ----------------------------------------------------------------------
# Vanilla (Mojang)
# ----------------------------------------------------------------------
async def _mojang_manifest() -> dict[str, Any]:
    data = await savedlists.fetch_json(
        MOJANG_MANIFEST, lambda d: isinstance(d, dict) and d.get("versions")
    )
    if not isinstance(data, dict) or not data.get("versions"):
        raise VersionError("Mojang's version list couldn't be read.")
    return data


async def vanilla_versions() -> list[Version]:
    data = await _mojang_manifest()
    found = []
    for entry in data["versions"]:
        version_id = str(entry.get("id") or "")
        if not VERSION_RE.match(version_id):
            continue
        found.append(
            Version(
                minecraft=version_id,
                stable=entry.get("type") == "release",
                released=entry.get("releaseTime"),
            )
        )
    return found


async def vanilla_plan(minecraft: str) -> Plan:
    data = await _mojang_manifest()
    entry = next((v for v in data["versions"] if str(v.get("id")) == minecraft), None)
    if entry is None:
        raise VersionError(f"Mojang doesn't list Minecraft {minecraft}.")
    detail = await savedlists.fetch_json(
        downloads.check_url(str(entry["url"])), lambda d: isinstance(d, dict)
    )
    remember_java(minecraft, detail)
    server = ((detail.get("downloads") or {}).get("server")) or {}
    if not server.get("url"):
        raise VersionError(
            f"Mojang doesn't publish a server download for Minecraft {minecraft}. "
            "Pick a newer version."
        )
    spec = FileSpec(
        url=downloads.check_url(str(server["url"])),
        name="server.jar",
        sha1=server.get("sha1"),
        size=int(server["size"]) if server.get("size") else None,
        allow_unverified=False,
    )
    return Plan(
        type_id="vanilla",
        minecraft=minecraft,
        loader=None,
        downloads=[Download(spec)],
        jar="server.jar",
        source="piston-meta.mojang.com",
    )


# ----------------------------------------------------------------------
# Java, from Mojang's own version files
# ----------------------------------------------------------------------
JAVA_KEY = "mojang-java"  # every {minecraft: java} answer read so far, in one saved copy


def remember_java(minecraft: str, detail: Any) -> int | None:
    """Note the Java that Mojang's version file asks for (javaVersion.majorVersion)."""
    from ..minecraft import java

    major = (detail.get("javaVersion") or {}).get("majorVersion") if _is_dict(detail) else None
    if not isinstance(major, int) or isinstance(major, bool) or not 8 <= major <= 99:
        return None
    if java.FROM_MOJANG.get(minecraft) != major:
        java.FROM_MOJANG[minecraft] = major
        savedlists.save(JAVA_KEY, java.FROM_MOJANG)
    return major


def load_java() -> None:
    """At agent start: the answers read before, so the pickers and the start
    check know them without asking Mojang again."""
    from ..minecraft import java

    saved = savedlists.load(JAVA_KEY)
    if saved and _is_dict(saved[0]):
        java.FROM_MOJANG.update(
            {str(k): v for k, v in saved[0].items() if isinstance(v, int) and 8 <= v <= 99}
        )


async def java_for(minecraft: str) -> int | None:
    """The Java a Minecraft version needs. Mojang's version file for it is
    read (live or saved) before a server is made or changed; the built-in
    table is only the fallback. None when nobody knows."""
    from ..minecraft.java import required_java

    try:
        data = await _mojang_manifest()
        entry = next((v for v in data["versions"] if str(v.get("id")) == minecraft), None)
        if entry is not None:
            url = downloads.check_url(str(entry.get("url") or ""))
            remember_java(minecraft, await savedlists.fetch_json(url, _is_dict))
    except DownloadError as exc:
        log.info("Mojang's version file for %s couldn't be read: %s", minecraft, exc)
    return required_java(minecraft)


# ----------------------------------------------------------------------
# Fabric and Quilt (their meta APIs)
# ----------------------------------------------------------------------
async def _meta_versions(base: str) -> tuple[list[dict], list[dict]]:
    games = await savedlists.fetch_json(f"{base}/versions/game", _is_list)
    loaders = await savedlists.fetch_json(f"{base}/versions/loader", _is_list)
    if not isinstance(games, list) or not isinstance(loaders, list):
        raise VersionError("The loader's version list couldn't be read.")
    return games, loaders


def _stable_loader(entry: dict) -> bool:
    """Fabric marks each loader stable or not; Quilt doesn't, so a name
    with "beta" in it counts as unfinished."""
    if "stable" in entry:
        return bool(entry["stable"])
    return not UNSTABLE_RE.search(str(entry.get("version") or ""))


def _loader_names(loaders: list[dict], stable_only: bool = True) -> list[str]:
    names = [
        str(entry.get("version") or "")
        for entry in loaders
        if VERSION_RE.match(str(entry.get("version") or ""))
        and (not stable_only or _stable_loader(entry))
    ]
    return names or [str(entry.get("version")) for entry in loaders if entry.get("version")]


async def meta_versions(server_type: ServerType) -> list[Version]:
    base = FABRIC_META if server_type.id == "fabric" else QUILT_META
    games, loaders = await _meta_versions(base)
    names = _loader_names(loaders)
    found = []
    for entry in games:
        version_id = str(entry.get("version") or "")
        if not VERSION_RE.match(version_id):
            continue
        found.append(
            Version(
                minecraft=version_id, stable=bool(entry.get("stable")), loaders=list(names[:40])
            )
        )
    return found


async def meta_plan(server_type: ServerType, minecraft: str, loader: str | None) -> Plan:
    """Fabric and Quilt both publish a ready-made server launcher jar for a
    (Minecraft, loader) pair, so there is nothing to run afterwards."""
    base = FABRIC_META if server_type.id == "fabric" else QUILT_META
    games, loaders = await _meta_versions(base)
    if not any(str(entry.get("version")) == minecraft for entry in games):
        raise VersionError(f"{server_type.name} doesn't support Minecraft {minecraft}.")
    offered = _loader_names(loaders, stable_only=False)
    chosen = loader or (_loader_names(loaders) or offered)[0]
    if chosen not in offered:
        raise VersionError(f"{server_type.name} has no loader version {chosen}.")
    stable = any(e.get("version") == chosen and _stable_loader(e) for e in loaders)
    installer = await savedlists.fetch_json(f"{base}/versions/installer", _is_list)
    if server_type.id == "fabric":
        # The ready-made server launcher is built by Fabric's installer, so
        # its address names the installer version as well.
        installers = [
            str(entry.get("version") or "")
            for entry in (installer if isinstance(installer, list) else [])
            if isinstance(entry, dict)
            and VERSION_RE.match(str(entry.get("version") or ""))
            and entry.get("stable", True)
        ]
        if not installers:
            raise VersionError("Fabric's installer list couldn't be read.")
        url = f"{FABRIC_META}/versions/loader/{minecraft}/{chosen}/{installers[0]}/server/jar"
        spec = FileSpec(url=downloads.check_url(url), name="fabric-server-launch.jar")
        return Plan(
            type_id=server_type.id,
            minecraft=minecraft,
            loader=chosen,
            downloads=[Download(spec)],
            jar="fabric-server-launch.jar",
            source="meta.fabricmc.net",
            # Fabric's meta API publishes no checksum for the launcher jar.
            verified=False,
            stable=stable,
        )
    if not isinstance(installer, list) or not installer:
        raise VersionError("Quilt's installer list couldn't be read.")
    newest = installer[0]
    url = str(newest.get("url") or "")
    if not url:
        version_id = check_version(newest.get("version"), "Quilt installer version")
        url = (
            f"https://maven.quiltmc.org/repository/release/org/quiltmc/quilt-installer/"
            f"{version_id}/quilt-installer-{version_id}.jar"
        )
    spec = FileSpec(url=downloads.check_url(url), name="quilt-installer.jar")
    return Plan(
        type_id=server_type.id,
        minecraft=minecraft,
        loader=chosen,
        downloads=[Download(spec, role="installer")],
        jar="quilt-server-launch.jar",
        installer="quilt-installer.jar",
        source="meta.quiltmc.org",
        verified=False,
        stable=stable,
    )


# ----------------------------------------------------------------------
# Forge and NeoForge (their Maven repositories)
# ----------------------------------------------------------------------
def _maven_versions(xml: str) -> list[str]:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise VersionError(f"The version list couldn't be read: {exc}") from exc
    found = [
        (node.text or "").strip()
        for node in root.iter("version")
        if VERSION_RE.match((node.text or "").strip())
    ]
    if not found:
        raise VersionError("The version list held no versions.")
    # Newest first. Sorted by number rather than trusting the file's order,
    # which differs between the two repositories.
    return sorted(set(found), key=version_key, reverse=True)


async def forge_versions(server_type: ServerType) -> list[Version]:
    """Forge's Maven versions are "<minecraft>-<build>"; NeoForge's are
    "<minor>.<patch>.<build>" for Minecraft 1.<minor>.<patch>."""
    if server_type.id == "forge":
        xml = await savedlists.fetch_text(f"{FORGE_MAVEN}/maven-metadata.xml", _maven_versions)
        by_game: dict[str, list[str]] = {}
        for entry in _maven_versions(xml):
            minecraft, _, build = entry.partition("-")
            if not build or version_key(minecraft)[0][:2] < FORGE_OLDEST:
                continue
            by_game.setdefault(minecraft, []).append(entry)
        return [
            Version(
                minecraft=mc,
                stable=True,
                loaders=sorted(
                    builds, key=lambda b: version_key(b.partition("-")[2]), reverse=True
                ),
            )
            for mc, builds in sorted(by_game.items(), key=lambda i: version_key(i[0]), reverse=True)
        ]
    xml = await savedlists.fetch_text(f"{NEOFORGE_MAVEN}/maven-metadata.xml", _maven_versions)
    by_game = {}
    for entry in _maven_versions(xml):
        game = _neoforge_minecraft(entry)
        if game:
            by_game.setdefault(game, []).append(entry)
    return [
        Version(
            minecraft=mc,
            # A snapshot (no dots, like 25w14a) is never stable.
            stable=not UNSTABLE_RE.search(builds[0]) and bool(re.fullmatch(r"\d+(\.\d+)+", mc)),
            loaders=builds,
        )
        for mc, builds in by_game.items()
    ]


def _neoforge_minecraft(build: str) -> str | None:
    """The Minecraft version a NeoForge build is for.

    21.1.9            Minecraft 1.21.1   (<minor>.<patch>.<build>)
    21.0.3-beta       Minecraft 1.21
    26.1.0.5          Minecraft 26.1     (year-based Minecraft versions:
    26.1.1.2          Minecraft 26.1.1    <year>.<drop>.<hotfix>.<build>)
    0.25w14a.3-beta   snapshot 25w14a
    """
    parts = build.split("-")[0].split(".")
    if len(parts) < 3:
        return None
    if parts[0] == "0":
        return parts[1] if VERSION_RE.match(parts[1]) else None
    if not all(part.isdigit() for part in parts):
        return None
    if int(parts[0]) >= 25 and len(parts) >= 4:
        return ".".join(parts[:2]) if parts[2] == "0" else ".".join(parts[:3])
    return f"1.{parts[0]}" if parts[1] == "0" else f"1.{parts[0]}.{parts[1]}"


async def forge_plan(server_type: ServerType, minecraft: str, loader: str | None) -> Plan:
    versions = await forge_versions(server_type)
    entry = next((v for v in versions if v.minecraft == minecraft), None)
    if entry is None:
        raise VersionError(f"{server_type.name} doesn't support Minecraft {minecraft}.")
    chosen = loader or entry.loaders[0]
    if chosen not in entry.loaders:
        raise VersionError(f"{server_type.name} has no build {chosen} for Minecraft {minecraft}.")
    if server_type.id == "forge":
        base, name = FORGE_MAVEN, f"forge-{chosen}-installer.jar"
    else:
        base, name = NEOFORGE_MAVEN, f"neoforge-{chosen}-installer.jar"
    url = f"{base}/{chosen}/{name}"
    sha1 = None
    try:
        sha1 = (await downloads.fetch_text(f"{url}.sha1", max_bytes=256)).split()[0].strip()
    except (DownloadError, IndexError):
        log.info("no published sha1 for %s", name)
    spec = FileSpec(
        url=downloads.check_url(url),
        # Named by the app, never from the source's answer.
        name=f"{server_type.id}-installer.jar",
        sha1=sha1 if sha1 and re.fullmatch(r"[0-9a-fA-F]{40}", sha1) else None,
    )
    return Plan(
        type_id=server_type.id,
        minecraft=minecraft,
        loader=chosen,
        downloads=[Download(spec, role="installer")],
        jar="",
        installer=f"{server_type.id}-installer.jar",
        source=server_type.version_host,
        verified=bool(spec.sha1),
        stable=not UNSTABLE_RE.search(chosen),
    )


# ----------------------------------------------------------------------
# Paper (and types from the notes file on PaperMC's Fill API), Purpur, Leaf
# ----------------------------------------------------------------------
def _fill(project: str) -> str:
    return f"{FILL_API}/{check_version(project, 'project')}"


async def paper_versions(project: str = "paper") -> list[Version]:
    data = await savedlists.fetch_json(_fill(project), lambda d: isinstance(d, dict) and d)
    versions = (data.get("versions") or {}) if isinstance(data, dict) else {}
    found: list[str] = []
    if isinstance(versions, dict):
        for group in versions.values():  # {"1.21": ["1.21.1", "1.21"], ...}
            found.extend(str(v) for v in group or [])
    elif isinstance(versions, list):
        found = [str(v) for v in versions]
    if not found:  # the older v2 API shape
        found = [str(v) for v in (data.get("version_groups") or [])]
    if not found:
        raise VersionError("PaperMC's version list couldn't be read.")
    found = sorted({v for v in found if VERSION_RE.match(v)}, key=version_key, reverse=True)
    return [Version(minecraft=v, stable=not UNSTABLE_RE.search(v)) for v in found]


async def paper_plan(minecraft: str, project: str = "paper") -> Plan:
    builds = await savedlists.fetch_json(
        f"{_fill(project)}/versions/{minecraft}/builds", lambda d: isinstance(d, (list, dict))
    )
    rows = builds if isinstance(builds, list) else (builds.get("builds") or [])
    if not rows:
        raise VersionError(f"PaperMC has no build for Minecraft {minecraft} yet.")
    newest = rows[0] if isinstance(rows[0], dict) else {}
    for row in rows:  # prefer the newest build marked stable
        if isinstance(row, dict) and str(row.get("channel", "")).upper() in ("DEFAULT", "STABLE"):
            newest = row
            break
    application = (
        ((newest.get("downloads") or {}).get("server:default"))
        or ((newest.get("downloads") or {}).get("application"))
        or {}
    )
    url = str(application.get("url") or "")
    if not url:
        raise VersionError(f"PaperMC didn't say where to download Minecraft {minecraft}.")
    checksums = application.get("checksums") or {}
    spec = FileSpec(
        url=downloads.check_url(url),
        name=f"{project}.jar",
        sha256=checksums.get("sha256") or application.get("sha256"),
        size=int(application["size"]) if str(application.get("size", "")).isdigit() else None,
        allow_unverified=False,
    )
    return Plan(
        type_id=project,
        minecraft=minecraft,
        loader=str(newest.get("id") or newest.get("build") or ""),
        downloads=[Download(spec)],
        jar=f"{project}.jar",
        source="fill.papermc.io",
        stable=str(newest.get("channel", "")).upper() in ("DEFAULT", "STABLE"),
    )


async def purpur_versions() -> list[Version]:
    data = await savedlists.fetch_json(PURPUR_API, lambda d: isinstance(d, dict) and d)
    found = [str(v) for v in (data.get("versions") or [])] if isinstance(data, dict) else []
    if not found:
        raise VersionError("Purpur's version list couldn't be read.")
    found = sorted({v for v in found if VERSION_RE.match(v)}, key=version_key, reverse=True)
    return [Version(minecraft=v, stable=not UNSTABLE_RE.search(v)) for v in found]


async def purpur_plan(minecraft: str) -> Plan:
    detail = await savedlists.fetch_json(f"{PURPUR_API}/{minecraft}", _is_dict)
    builds = (detail.get("builds") or {}) if isinstance(detail, dict) else {}
    build = str(builds.get("latest") or "")
    if not build:
        raise VersionError(f"Purpur has no build for Minecraft {minecraft} yet.")
    check_version(build, "Purpur build")
    info = await savedlists.fetch_json(f"{PURPUR_API}/{minecraft}/{build}", _is_dict)
    spec = FileSpec(
        url=downloads.check_url(f"{PURPUR_API}/{minecraft}/{build}/download"),
        name="purpur.jar",
        md5=(info.get("md5") if isinstance(info, dict) else None),
    )
    return Plan(
        type_id="purpur",
        minecraft=minecraft,
        loader=build,
        downloads=[Download(spec)],
        jar="purpur.jar",
        source="api.purpurmc.org",
        verified=bool(spec.md5),
    )


async def leaf_versions() -> list[Version]:
    data = await savedlists.fetch_json(LEAF_API, lambda d: isinstance(d, dict) and d)
    found = [str(v) for v in (data.get("versions") or [])] if isinstance(data, dict) else []
    if not found:
        raise VersionError("Leaf's version list couldn't be read.")
    found = sorted({v for v in found if VERSION_RE.match(v)}, key=version_key, reverse=True)
    return [Version(minecraft=v, stable=not UNSTABLE_RE.search(v)) for v in found]


async def leaf_plan(minecraft: str) -> Plan:
    """The newest build, preferring one marked stable ("default"). Leaf's
    list is oldest first, as PaperMC's v2 API was."""
    data = await savedlists.fetch_json(f"{LEAF_API}/versions/{minecraft}/builds", _is_dict)
    rows = [r for r in (data.get("builds") or []) if isinstance(r, dict)]
    rows = [r for r in rows if str(r.get("build", "")).isdigit()]
    if not rows:
        raise VersionError(f"Leaf has no build for Minecraft {minecraft} yet.")
    rows.sort(key=lambda r: int(r["build"]), reverse=True)
    stable = [r for r in rows if str(r.get("channel", "")).lower() == "default"]
    newest = (stable or rows)[0]
    files = newest.get("downloads") or {}
    application = files.get("primary") or files.get("application") or {}
    name = str(application.get("name") or "")
    if not re.fullmatch(r"[A-Za-z0-9._+\-]{1,100}\.jar", name):
        raise VersionError(f"Leaf didn't say which file to download for Minecraft {minecraft}.")
    build = str(newest["build"])
    spec = FileSpec(
        url=downloads.check_url(f"{LEAF_API}/versions/{minecraft}/builds/{build}/downloads/{name}"),
        name="leaf.jar",
        sha256=application.get("sha256"),
        allow_unverified=False,
    )
    return Plan(
        type_id="leaf",
        minecraft=minecraft,
        loader=build,
        downloads=[Download(spec)],
        jar="leaf.jar",
        source="api.leafmc.one",
        stable=bool(stable),
    )


# ----------------------------------------------------------------------
# One way in
# ----------------------------------------------------------------------
async def list_versions(type_id: str) -> list[Version]:
    """Every Minecraft version this type offers, newest first."""
    server_type = get(type_id)
    if server_type.id == "bedrock":
        from . import bedrock

        return (await bedrock.versions())[0]
    if server_type.id == "vanilla":
        return await vanilla_versions()
    if server_type.id in ("fabric", "quilt"):
        return await meta_versions(server_type)
    if server_type.id in ("forge", "neoforge"):
        return await forge_versions(server_type)
    if server_type.id == "paper" or server_type.like == "paper":
        return await paper_versions(server_type.id)
    if server_type.id == "leaf":
        return await leaf_versions()
    return await purpur_versions()


async def make_plan(type_id: str, minecraft: str, loader: str | None = None) -> Plan:
    """What to download and install for one version of one type."""
    server_type = get(type_id)
    minecraft = check_version(minecraft, "Minecraft version")
    if loader:
        loader = check_version(loader, f"{server_type.name} version")
    if server_type.id == "bedrock":
        from . import bedrock

        return await bedrock.plan(minecraft)
    if server_type.id == "vanilla":
        return await vanilla_plan(minecraft)
    if server_type.id in ("fabric", "quilt"):
        return await meta_plan(server_type, minecraft, loader)
    if server_type.id in ("forge", "neoforge"):
        return await forge_plan(server_type, minecraft, loader)
    if server_type.id == "paper" or server_type.like == "paper":
        return await paper_plan(minecraft, server_type.id)
    if server_type.id == "leaf":
        return await leaf_plan(minecraft)
    return await purpur_plan(minecraft)


def latest_stable(versions: list[Version]) -> Version | None:
    return next((v for v in versions if v.stable), versions[0] if versions else None)


async def report(type_id: str) -> dict[str, Any]:
    """The version list as the version pickers show it. It says when a site
    couldn't be reached and a saved list is shown (Bedrock: kept copies)."""
    server_type = get(type_id)
    if server_type.id == "bedrock":
        from . import bedrock

        found, problem = await bedrock.versions()
        return {
            **versions_payload(server_type.id, found),
            "source_problem": problem,
            "saved_at": None,
        }
    with savedlists.watch() as used:
        found = await list_versions(server_type.id)
    saved_at, problem = savedlists.problem(used)
    # saved_at is set when a site couldn't be reached and its saved list
    # is shown; problem says how old it is.
    return {
        **versions_payload(server_type.id, found),
        "source_problem": problem,
        "saved_at": saved_at,
    }


def versions_payload(type_id: str, versions: list[Version]) -> dict[str, Any]:
    server_type = get(type_id)
    newest = latest_stable(versions)
    return {
        # Only the newest is offered by the source; older ones only where
        # this app kept a copy it downloaded.
        "latest_only": server_type.latest_only,
        "type": server_type.id,
        "type_name": server_type.name,
        "source": server_type.version_source,
        "source_host": server_type.version_host,
        "snapshots": server_type.snapshots and any(not v.stable for v in versions),
        "picks_loader": server_type.picks_loader,
        "loader_name": server_type.loader_name,
        "latest_stable": newest.minecraft if newest else None,
        "versions": [v.to_dict() for v in versions],
    }
