# Third-party notices

Command Block is under the MIT License (`LICENSE`). The Windows program and
its setup file also include the libraries below, each under its own
license. The Python packages are listed with their versions in
`requirements-build.lock`.

## Qt for Python (PySide6 and Shiboken6) and Qt 6

The desktop app and the setup window use Qt 6 through PySide6-Essentials
and Shiboken6 6.8.3, Copyright (C) The Qt Company Ltd. and other
contributors. They are used under the GNU Lesser General Public License
version 3 (`licenses/LGPL-3.0.txt`, which builds on `licenses/GPL-3.0.txt`).

- They are not changed. Their source is at
  https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/
  and https://download.qt.io/official_releases/qt/6.8/.
- They are separate files (DLLs and Python modules) in the program folder's
  `_internal` folder, loaded when the program starts. You can replace them
  with your own build of the same version: nothing checks them when the
  program runs (only downloaded updates are checked).
- Qt includes other open-source parts (for example zlib, PCRE2, FreeType,
  HarfBuzz and libpng), listed with their licenses at
  https://doc.qt.io/qt-6.8/licenses-used-in-qt.html.
