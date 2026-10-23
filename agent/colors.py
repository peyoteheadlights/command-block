"""Each server's color.

A server is told apart from the others by its color: its tab, the band on
its page and its buttons all use it. The palette is
twelve hues spread around the color wheel, chosen so any two stay clearly
different side by side for people with normal color vision and with the
common forms of color blindness (protanopia, deuteranopia, tritanopia).
``tests/test_server_colors.py`` simulates each and fails if two colors get
too close. A new server gets the first palette color no other server uses;
anyone can pick another palette color or their own hex instead.

These are the base colors. The apps derive darker or lighter shades of
each for text and buttons so they stay readable on every theme
(``derive_from`` below); the base color itself is only ever a fill.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

# (id, name, hex). The order is the order new servers get them in.
PALETTE: tuple[tuple[str, str, str], ...] = (
    ("blue", "Blue", "#1967D4"),
    ("orange", "Orange", "#F87906"),
    ("green", "Green", "#30C067"),
    ("crimson", "Crimson", "#D7213E"),
    ("violet", "Violet", "#B78AF6"),
    ("amber", "Amber", "#F7E10D"),
    ("teal", "Teal", "#0D4B48"),
    ("magenta", "Magenta", "#A61888"),
    ("cyan", "Cyan", "#54E7F0"),
    ("olive", "Olive", "#4F610A"),
    ("rose", "Rose", "#FCA1B1"),
    ("indigo", "Indigo", "#18185F"),
)

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ColorError(ValueError):
    """Not a color the apps can use."""


def normalise(value: str) -> str:
    """A palette id or a "#RRGGBB" hex, as the hex stored on the server row."""
    text = (value or "").strip()
    for color_id, _name, hex_ in PALETTE:
        if text.lower() == color_id:
            return hex_
    if _HEX.match(text):
        return text.upper()
    raise ColorError("Pick a color from the list, or type one as # and six hex digits")


def next_unused(taken: Iterable[str | None]) -> str:
    """The first palette color no other server has. When all twelve are
    taken, colors are shared, least-used first."""
    used = [t.upper() for t in taken if t]
    counts = {hex_: used.count(hex_) for _id, _name, hex_ in PALETTE}
    fewest = min(counts.values())
    return next(hex_ for _id, _name, hex_ in PALETTE if counts[hex_] == fewest)


def palette() -> list[dict[str, str]]:
    return [{"id": color_id, "name": name, "hex": hex_} for color_id, name, hex_ in PALETTE]


# ------------------------------------------------- tinting (colors.js in Python)
# How a server's color tints a theme, for the desktop app. The phone apps do
# the same sums; tests/test_server_colors.py checks them against
# shared/color-cases.json, like the phone apps' own tests.
AA_TEXT = 4.5
AA_MARK = 3.0
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RGB = tuple[int, int, int]


def _round(x: float) -> int:
    return math.floor(x + 0.5)  # JavaScript's Math.round, not Python's banker's rounding


def rgb(value: str | None) -> RGB | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"#?([0-9A-Fa-f]{6})", text)
    if not match:
        return None
    n = int(match.group(1), 16)
    return ((n >> 16) & 255, (n >> 8) & 255, n & 255)


def to_hex(color: RGB) -> str:
    return "#" + "".join(f"{_round(min(255, max(0, c))):02X}" for c in color)


def luminance(color: RGB) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: RGB, b: RGB) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: RGB, b: RGB, amount: float) -> RGB:
    return (
        _round(a[0] + (b[0] - a[0]) * amount),
        _round(a[1] + (b[1] - a[1]) * amount),
        _round(a[2] + (b[2] - a[2]) * amount),
    )


def readable(color: RGB, backgrounds: list[RGB], least: float = AA_TEXT) -> RGB:
    """color moved toward black or white until it reaches least contrast
    with every background."""
    target = BLACK if min(luminance(bg) for bg in backgrounds) > 0.18 else WHITE
    for step in range(21):
        candidate = mix(color, target, step / 20)
        if all(contrast(candidate, bg) >= least for bg in backgrounds):
            return candidate
    return target


def fill_with_text(color: RGB) -> tuple[RGB, RGB]:
    """A fill text can sit on, and that text (white or near-black)."""
    dark = (17, 17, 17)
    text = WHITE if contrast(color, WHITE) >= contrast(color, dark) else dark
    away = BLACK if text == WHITE else WHITE
    for step in range(21):
        fill = mix(color, away, step / 20)
        if contrast(fill, text) >= AA_TEXT:
            return fill, text
    return away, text


def _keep_readable(
    color: RGB | None, before: list[RGB], after: list[RGB], floor: float = AA_TEXT
) -> RGB | None:
    if color is None:
        return None
    was = min(contrast(color, bg) for bg in before)
    return readable(color, after, min(floor, was))


def derive_from(base_hex: str, theme_tokens: dict, theme: str) -> dict[str, str] | None:
    """Every token a server's color sets on a theme (theme.json's tokens for
    that theme), as hex, named as in theme.json."""
    base = rgb(base_hex)
    if base is None:
        return None
    k = {name: rgb(value) for name, value in theme_tokens.items() if isinstance(value, str)}
    sheet0, surface0 = k.get("sheet") or WHITE, k.get("surface") or WHITE
    sunken0, float0 = k.get("surface-sunken") or surface0, k.get("surface-float") or surface0
    desk = k.get("desk") or WHITE
    dark = luminance(sheet0) < 0.18
    if theme == "contrast":
        amount = {"sheet": 0.1, "surface": 0.06, "sunken": 0.1, "band": 0.2}
    elif dark:
        amount = {"sheet": 0.18, "surface": 0.13, "sunken": 0.16, "band": 0.3}
    else:
        amount = {"sheet": 0.2, "surface": 0.07, "sunken": 0.18, "band": 0.32}
    sheet = mix(sheet0, base, amount["sheet"])
    surface = mix(surface0, base, amount["surface"])
    sunken = mix(sunken0, base, amount["sunken"])
    float_ = mix(float0, base, amount["surface"])
    band = mix(sheet0, base, amount["band"])
    before, after = [sheet0, surface0, sunken0], [sheet, surface, sunken, band]

    def text_on(name: str) -> RGB | None:
        return _keep_readable(k.get(name), before, after)

    fill, on_fill = fill_with_text(base)
    hover, hover_text = fill_with_text(mix(fill, WHITE if dark else BLACK, 0.12))
    tab = mix(desk, base, 0.32 if dark else 0.24)
    tokens: dict[str, RGB | None] = {
        "sheet": sheet,
        "surface": surface,
        "surface-sunken": sunken,
        "surface-float": float_,
        "text-primary": text_on("text-primary"),
        "text-secondary": text_on("text-secondary"),
        "text-tertiary": text_on("text-tertiary"),
        "success": text_on("success"),
        "warning": text_on("warning"),
        "danger": text_on("danger"),
        "accent": fill,
        "accent-text": on_fill,
        "accent-hover": hover,
        "accent-hover-text": hover_text,
        "accent-ink": readable(base, [surface, sheet, band]),
        "accent-band": band,
        "accent-edge": base,
        "accent-ring": readable(base, [surface, sheet], AA_MARK),
        "tab-fill": tab,
        "tab-text": readable(k.get("text-primary") or BLACK, [tab]),
    }
    out = {name: to_hex(value) for name, value in tokens.items() if value}
    out["accent-soft"] = f"rgba({base[0]}, {base[1]}, {base[2]}, {0.2 if dark else 0.13})"
    return out
