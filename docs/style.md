# Style guide

How Command Block looks in the desktop app, the setup window and the phone
apps. Read this before changing anything people see.

## Look

### Colors

- All colors live in `shared/theme.json` (light, dark, graphite, high
  contrast). The desktop app reads them in `desktop/theme.py`, the phones in
  `ServerColors.binder()` (Kotlin and Swift). `shared/look-cases.json` holds
  the expected results, and the Python, Kotlin and Swift tests all check
  against it, so the three apps can't drift apart.
- Light mode is plain cool grey and white. No cream or warm off-white.
- Each server has its own color. It colors the band, the main buttons and the
  edges of raised boxes, never the whole page. With no server open
  (All servers, PC settings) the color is copper (`#C8794A`).
- Text uses three greys only: `text-primary`, `text-secondary` and
  `text-tertiary`. All of them pass contrast in every theme. Don't add
  see-through text.
- No gradients and no glows. Raised boxes get a soft shadow, that's all.
- No purple-blue "AI" accents.

### Fonts

- Headings and the server tabs: **Archivo** (`shared/fonts/`, under the SIL
  Open Font License). Body text: Segoe UI on Windows, the system font on the
  phones.
- Normal letter spacing, no italics, no display serifs.
- A mono font only where the exact characters matter: the console, addresses,
  file names, fingerprints and raw config text. Never for style.
- Sentence case everywhere: titles, buttons, headings, tabs. No ALL CAPS
  labels, and no full stop at the end of a title or button.

### Shapes and spacing

- Corners stay small: 8 px on boxes, buttons and fields, 12 to 14 px on the
  sheet. No pills and no squircles. Round only for dots and color swatches.
- A raised box holds one thing: a server, a backup, a mod, a card on the
  Overview or one group of settings. Don't put boxes inside boxes.
- No divider lines between rows. Space and the hover color separate them. In
  the binder, the tabs are the dividers.

### Parts

The desktop parts are in `desktop/parts.py` (see `scripts/parts_gallery.py`),
the phone ones in `ui/Common.kt` and `SharedUI/Look.swift`.

- **Status mark**: a steady dot and a word ("Online", "Offline"). A new
  state's dot grows in once; it never loops and is never a pill.
- **Info dot** (i): sentences that only explain go behind it. Hover on the
  desktop, tap on the phones.
- **Icon buttons** for common actions, always with a hover name and an
  accessible name.
- **Player face**: a plain grey head until real skins exist.
- Loading: a plain bar with the real percentage. No pixel grids.

### Icons

- Line icons, 24 by 24, drawn in `desktop/icons.py` in the color asked for.
  The phones use their platform icons (SF Symbols, Material).
- No emoji in the app, the alerts, the feed or notifications. No sparkle
  icons on "suggest" or "fix" buttons.
- Plain type marks are fine where they help reading: `•` for a list in a
  sentence, `→` between two versions, `✓` for a step that is done.
- The app icon stays flat.

### Motion

- One curve, eased out, no bounce. The binder moves take 560 to 620 ms,
  small things stay under about 300 ms.
- Nothing loops while idle and nothing moves while you drag it.
- Everything turns off when Windows' "Show animations" is off, over Remote
  Desktop, or when the phone's reduce motion setting is on.

### Things to leave out

From the AI design slop checklist: tags on every label, eyebrow labels (a
small capitals word above a heading), section numbers like 01 and 02,
code-style labels like `// section`, ASCII art, dithered pictures, see-through
blurred panels as the main look, endless scrolling text, and screens that
show everything at once (Simple mode shows less).
