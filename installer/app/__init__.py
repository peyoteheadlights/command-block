"""The installer's window: a native Windows app (Qt, through PySide6).

``mcsc.exe setup`` (started by setup.exe) opens it. It only asks; the work
is done by ``installer/engine.py`` on a thread of its own, and the window
shows the engine's real progress. It uses the desktop app's look
(``desktop/theme.py``), in light or dark to match Windows, so setup looks
like the first screen of the app.
"""
