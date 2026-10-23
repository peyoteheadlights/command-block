"""Make the app icons from the logo (docs/images/logo.png).

    python scripts/make_icons.py

Writes ``desktop/images/icon-192.png`` (the window and notification area),
``app.ico`` for the Windows programs and installer (the ICO format holds
PNG pictures as they are, so it is the logo at 16 to 256 pixels), and the
iPhone app's 1024 pixel icon. The Android icon is the same drawing as
shapes (mobile/android/app/src/main/res/drawable/ic_launcher_foreground.xml).

Run this again if the logo changes, and commit the files it writes.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "docs" / "images" / "logo.png"
OUT = ROOT / "desktop" / "images"
IPHONE = ROOT / "mobile" / "ios" / "ServerController" / "Assets.xcassets" / "AppIcon.appiconset"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(logo: QImage, size: int) -> bytes:
    picture = logo.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    picture.save(buffer, "PNG")
    return bytes(buffer.data().data())


def write_ico(path: Path, logo: QImage) -> None:
    """An .ico holding one PNG per size (Windows Vista and later read these)."""
    images = [png_bytes(logo, size) for size in ICO_SIZES]
    header = struct.pack("<3H", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for size, data in zip(ICO_SIZES, images, strict=True):
        side = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += struct.pack("<4B2H2I", side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(header + entries + b"".join(images))


def main() -> int:
    logo = QImage(str(LOGO))
    if logo.isNull():
        print(f"can't read {LOGO.relative_to(ROOT)}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "icon-192.png").write_bytes(png_bytes(logo, 192))
    write_ico(OUT / "app.ico", logo)
    # The App Store refuses an icon with transparency.
    IPHONE.mkdir(parents=True, exist_ok=True)
    logo.convertToFormat(QImage.Format.Format_RGB888).save(str(IPHONE / "icon-1024.png"))
    for path in (OUT / "icon-192.png", OUT / "app.ico", IPHONE / "icon-1024.png"):
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
