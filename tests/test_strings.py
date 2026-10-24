"""The words every app shows (shared/strings.json), edited by hand.

Every label has a Simple and a Technical version with the same
{placeholders}. Keys the apps build at run time (color.<id>, cause.<name>)
are checked against the list they come from, so a new crash category,
alert event or palette color without wording fails here instead of showing
a raw key. The desktop app's words all exist, and its own are all used.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import agent
from agent import colors, keepawake, servertypes
from agent.checklist import ITEMS
from agent.config import NotificationSettings
from agent.minecraft.analyzer import CATEGORIES
from agent.scheduler.scheduler import TASKS
from agent.security.auth import AuthManager

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "shared"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def table() -> dict[str, list[str]]:
    data: dict[str, list[str]] = json.loads((SHARED / "strings.json").read_text(encoding="utf-8"))
    return data


def families() -> dict[str, set[str]]:
    """Every key prefix the apps complete at run time, with the suffixes it can take."""
    events = NotificationSettings().events
    return {
        "color.": {c["id"] for c in colors.palette()},
        "alert_event.": set(events),
        "task.": set(TASKS),
        "cause.": set(CATEGORIES) - {"Unknown"},
        "theme.": set(AuthManager.PREFERENCE_CHOICES["theme"]),
        # The getting-started checklist: each item's name and its button.
        "start.": {
            *(item.__name__.lstrip("_") for item in ITEMS),
            *(f"{item.__name__.lstrip('_')}_action" for item in ITEMS),
        },
        "tps.state_": {"active", "detecting", "unavailable", "disabled", "idle"},
        "serverset.keep_": {"daily", "weekly", "monthly"},
        # one line per server type in the "+" comparison table
        "types.": {f"{type_id}.best" for type_id in servertypes.TYPES},
        "new.ease_": {t.ease for t in servertypes.TYPES.values()},
        # what can still put the PC to sleep while it is kept awake
        "awake.still_": set(keepawake.STILL_SLEEPS),
    }


def test_every_entry_has_simple_and_technical_text():
    strings = table()
    assert len(strings) > 300
    for key, entry in strings.items():
        assert isinstance(entry, list) and len(entry) == 2, key
        simple, technical = entry
        assert isinstance(simple, str) and simple.strip(), f"{key}: Simple text is empty"
        assert isinstance(technical, str) and technical.strip(), f"{key}: Technical text is empty"
        assert sorted(PLACEHOLDER.findall(simple)) == sorted(PLACEHOLDER.findall(technical)), (
            f"{key}: Simple and Technical use different placeholders"
        )


def test_keys_are_well_formed_and_written_once():
    text = (SHARED / "strings.json").read_text(encoding="utf-8")
    keys = re.findall(r'^"([^"]+)":', text, re.M)
    assert len(keys) == len(table()), "a key is in shared/strings.json twice"
    for key in keys:
        assert re.fullmatch(r"[a-z_]+(\.[A-Za-z0-9_]+)+", key), key


def test_every_runtime_key_family_is_complete():
    strings = table()
    for prefix, suffixes in families().items():
        missing = sorted(f"{prefix}{s}" for s in suffixes if f"{prefix}{s}" not in strings)
        assert not missing, f"no wording for {missing}"


def test_every_alert_in_the_feed_has_words():
    feed = json.loads((SHARED / "feed.json").read_text(encoding="utf-8"))
    assert len(feed) > 10
    strings = table()
    assert not [key for key in feed.values() if key not in strings]


def test_every_word_the_desktop_app_uses_exists_and_its_own_are_all_used():
    strings = table()
    source = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "desktop").glob("*.py"))
    used = set(re.findall(r"""\btext\(\s*f?["']([\w.]+)["']""", source))
    used |= set(
        re.findall(r"""["']((?:desktop|page|state|action|mode|changes)\.[\w.]+)["']""", source)
    )
    missing = sorted(k for k in used if k not in strings and "{" not in k)
    assert not missing, f"desktop/ uses words that aren't in shared/strings.json: {missing}"
    unused = sorted(k for k in strings if k.startswith("desktop.") and k not in used)
    assert not unused, f"shared/strings.json has desktop words nothing uses: {unused}"


def test_the_phone_apps_work_with_this_agents_api():
    version = json.loads((SHARED / "version.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d+\.\d+\.\d+", version["app_version"])
    assert version["agent_api"]["min"] <= agent.API_VERSION <= version["agent_api"]["max"], (
        "shared/version.json: update the phone apps and their agent_api range together"
    )


def test_every_copy_of_a_link_matches_shared_links():
    """shared/links.json is where the support and form addresses are set.
    The README and the phone apps can't read it, so their copies must match."""
    links = json.loads((SHARED / "links.json").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in ("sponsors", "kofi", "report", "suggest"):
        assert links[name] in readme, f"README.md: update the {name} link from shared/links.json"
    kotlin = (
        ROOT / "mobile/android/app/src/main/kotlin/io/github/peyoteheadlights/mcsc/ui/Common.kt"
    )
    swift = ROOT / "mobile/ios/ServerController/PairingFlowView.swift"
    android = kotlin.read_text(encoding="utf-8")
    repo = re.search(r'const val REPO = "([^"]+)"', android)
    assert repo
    android = android.replace("$REPO", repo[1])
    iphone = swift.read_text(encoding="utf-8")
    for name in ("report", "suggest", "sponsors", "kofi"):
        assert f'{name.upper()} = "{links[name]}"' in android, f"{kotlin.name}: update {name}"
        assert f'{name} = "{links[name]}"' in iphone, f"{swift.name}: update {name}"
