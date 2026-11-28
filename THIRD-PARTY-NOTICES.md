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

## Archivo (the heading font)

Headings and the server tabs in every app, and the desktop app's page list, use Archivo
(https://github.com/Omnibus-Type/Archivo), Copyright 2020 The Archivo
Project Authors, under the SIL Open Font License 1.1
(`licenses/Archivo-OFL.txt`). Three weights ship unchanged in
`shared/fonts` (Medium, SemiBold and Bold), and the desktop app and both
phone apps carry them. Nothing is downloaded when an app runs.

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

## Router port (UPnP)

"Let friends join from the internet" asks the home router to open the game
port with async-upnp-client (https://github.com/StevenLooman/async_upnp_client),
Copyright Steven Looman, under the Apache License 2.0
(`licenses/Apache-2.0.txt`). It brings aiohttp and its helpers (Apache 2.0),
voluptuous (BSD), python-didl-lite (Apache 2.0) and defusedxml (PSF), with the
versions in `requirements-build.lock`.

## YARA-X and YARA Forge

Deep scan checks files with YARA-X 1.25 by VirusTotal
(https://github.com/VirusTotal/yara-x), Copyright (c) 2024 The YARA-X
Authors, under the BSD 3-Clause License (`licenses/YARA-X-BSD-3-Clause.txt`).
It ships as the `yara_x` Python package, not changed.

The YARA Forge rule set (https://github.com/YARAHQ/yara-forge) isn't
shipped. It is downloaded from its GitHub releases, checked against the
release's checksum, only when the owner ticks it in Deep scan. Each rule in
it keeps the license its author gave it, which is written inside the rule.

## MCAntiMalware, jNeedle and ClamAV

Deep scan can run these as separate programs. They aren't shipped and none
of their code is in this repo. Each is downloaded from its GitHub release,
checked against a pinned checksum, only when someone ticks it in Deep scan:

- MCAntiMalware 15.16 by OpticFusion1
  (https://github.com/OpticFusion1/MCAntiMalware), GPL-3.0;
- jNeedle 1.1.0 by KosmX (https://github.com/KosmX/jneedle), MIT;
- ClamAV 1.5.5 by Cisco Talos (https://github.com/Cisco-Talos/clamav),
  GPL-2.0. Its signatures are downloaded by its own `freshclam`.

