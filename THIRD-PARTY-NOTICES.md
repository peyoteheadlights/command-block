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

## Diagnose server's test player

Diagnose server joins with Mineflayer (https://github.com/PrismarineJS/mineflayer,
MIT License) and bedrock-protocol (https://github.com/PrismarineJS/bedrock-protocol,
MIT License), and the other npm packages pinned in `bot/package-lock.json`
(MIT, BSD, Apache 2.0, ISC, 0BSD, Unlicense, CC0, and MPL 2.0 for
mediabunny, shipped unchanged as its own source; each package's own license
file ships with it in `tools/diagnose/node_modules`). prismarine-xbox-services
(MIT) is kept unchanged in `bot/vendor` at the commit the lock file names. They run on Node.js for Windows
(https://nodejs.org, MIT License; its `LICENSE` file, which also lists the
licenses of the libraries inside node.exe, ships as `tools/diagnose/LICENSE`).

The join with all mods runs the Minecraft game through HeadlessMC
(https://github.com/headlesshq/headlessmc, MIT License). It isn't shipped:
it is downloaded (checked against a pinned checksum) only when the owner
says yes to the join with all mods or signs in for it.

## Chunker

Making a Java copy of a Bedrock world (or the other way round) uses
Chunker 1.21.0 by Hive Games (https://github.com/HiveGamesOSS/Chunker),
Copyright (c) 2024 Hive Games, under the MIT License
(`licenses/Chunker-MIT.txt`). Its command-line jar is built from that
version's source without changes and ships as `tools/chunker-cli.jar`.

The jar carries Chunker's own libraries: picocli, Caffeine, Gson, Guava,
fastutil, lz4-java, JetBrains Annotations and Hive Games' LevelDB for Java,
under the Apache License 2.0 (`licenses/Apache-2.0.txt`), and the small
libraries Guava pulls in. Their versions are in Chunker's
`gradle/libs.versions.toml`.
