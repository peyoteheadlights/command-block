"""Windows antivirus check for jars, through AMSI (the Windows Antimalware
Scan Interface).

AMSI hands a piece of memory to whatever antivirus is set up on the PC
(Microsoft Defender, or another one that supports AMSI) and says whether
it found something. Nothing is written to disk and no program is run:
amsi.dll is loaded into the agent with ctypes, like any Windows library.

A jar is a zip, so the whole file is handed over first and then every file
inside it, unpacked in memory (jars inside jars too, a few levels deep).
AMSI gives no threat name, so a finding names the scanner only.

Not every antivirus looks inside the memory it is given. The owner can
click a test on the Protection card: a test jar holding the standard
antivirus test file (EICAR, harmless by design) is checked, and if
nothing is found the check turns itself off and says so.
"""

from __future__ import annotations

import ctypes
import io
import logging
import sys
import threading
import time
import zipfile
import zlib
from pathlib import Path
from typing import Any

log = logging.getLogger("msc.antivirus")

NAME = "Windows antivirus"
TEST_KEY = "antivirus_test"
DETECTED = 32768  # AMSI_RESULT_DETECTED: this and above means found
MAX_DEPTH = 3
MAX_ENTRY = 64 * 1024 * 1024  # a bigger file inside is still seen by the whole-file pass
MAX_TOTAL = 1024 * 1024 * 1024  # stop unpacking a zip bomb
UNREADABLE = (
    zipfile.BadZipFile,
    NotImplementedError,
    RuntimeError,
    zlib.error,
    EOFError,
    ValueError,
)

_lock = threading.Lock()
_engine: Any = None  # the loaded AMSI wrapper, or False when it can't load
_test: dict[str, Any] | None = None  # the last test result: {"at", "flagged"}


class _Amsi:
    """The five amsi.dll calls this needs."""

    def __init__(self) -> None:
        from ctypes import wintypes

        dll = ctypes.WinDLL("amsi.dll")  # type: ignore[attr-defined]
        handle = ctypes.c_void_p
        self.init = dll.AmsiInitialize
        self.init.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(handle)]
        self.open = dll.AmsiOpenSession
        self.open.argtypes = [handle, ctypes.POINTER(handle)]
        self.scan_buffer = dll.AmsiScanBuffer
        self.scan_buffer.argtypes = [
            handle,
            ctypes.c_void_p,
            wintypes.ULONG,
            wintypes.LPCWSTR,
            handle,
            ctypes.POINTER(ctypes.c_int),
        ]
        self.close = dll.AmsiCloseSession
        self.close.argtypes = [handle, handle]
        for fn in (self.init, self.open, self.scan_buffer):
            fn.restype = ctypes.c_long  # HRESULT: below 0 is a failure
        self.close.restype = None
        self.context = handle()
        if self.init("Command Block", ctypes.byref(self.context)) < 0:
            raise OSError("AmsiInitialize failed")

    def session(self) -> Any:
        session = ctypes.c_void_p()
        if self.open(self.context, ctypes.byref(session)) < 0:
            raise OSError("AmsiOpenSession failed")
        return session

    def end(self, session: Any) -> None:
        self.close(self.context, session)

    def found(self, data: bytes, name: str, session: Any) -> bool:
        if not data:  # AMSI refuses an empty buffer
            return False
        result = ctypes.c_int(0)
        if self.scan_buffer(self.context, data, len(data), name, session, ctypes.byref(result)) < 0:
            raise OSError("AmsiScanBuffer failed")
        return result.value >= DETECTED


def load() -> Any:
    """Load amsi.dll once. The agent calls this at start, off the event loop."""
    global _engine
    with _lock:
        if _engine is None:
            try:
                _engine = _Amsi() if sys.platform == "win32" else False
            except (OSError, AttributeError) as exc:
                log.warning("Windows antivirus (AMSI) isn't available: %s", exc)
                _engine = False
        return _engine


def remember(db: Any) -> None:
    """Read the last test result from the database (at agent start)."""
    global _test
    _test = db.get_setting(TEST_KEY)


def status() -> str:
    """off_windows (not Windows, or AMSI wouldn't load), not_flagging (the
    last test found nothing, so the check is off), untested or on."""
    if not load():
        return "off_windows"
    if _test is None:
        return "untested"
    return "on" if _test["flagged"] else "not_flagging"


def is_on() -> bool:
    return status() in ("untested", "on")


def scan_jar(data: bytes, name: str) -> list[str] | None:
    """Where in the jar the antivirus found something: "" for the whole
    file, else the path inside it ("libs/a.jar!/b.class"). Empty when it
    found nothing; None when it couldn't check (off, or AMSI failed)."""
    engine = load() if is_on() else None
    if not engine:
        return None
    try:
        session = engine.session()
    except OSError as exc:
        log.warning("Windows antivirus couldn't check %s: %s", name, exc)
        return None
    try:
        found = [""] if engine.found(data, name, session) else []
        budget = [MAX_TOTAL]
        found += _inside(engine, session, data, name, "", 0, budget)
        return found
    except OSError as exc:
        log.warning("Windows antivirus couldn't check %s: %s", name, exc)
        return None
    finally:
        engine.end(session)


def _inside(
    engine: Any, session: Any, data: bytes, name: str, prefix: str, depth: int, budget: list[int]
) -> list[str]:
    try:
        jar = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        return []
    found = []
    with jar:
        for entry in jar.infolist():
            if entry.is_dir() or entry.file_size > MAX_ENTRY:
                continue
            if entry.file_size > budget[0]:
                break
            try:
                inner = jar.read(entry)
            except UNREADABLE:
                continue
            budget[0] -= len(inner)
            if not inner:
                continue
            where = prefix + entry.filename
            if engine.found(inner, f"{name}:{where}", session):
                found.append(where)
            elif depth < MAX_DEPTH and inner.startswith(b"PK"):
                found += _inside(engine, session, inner, name, where + "!/", depth + 1, budget)
    return found


def scan_file(path: Path, name: str | None = None) -> list[str] | None:
    """scan_jar for a file on disk. Runs in a thread."""
    return scan_jar(path.read_bytes(), name or path.name)


def where_words(found: list[str], name: str) -> str:
    """The places a finding was, for a message: "x.jar" or "x.jar (a/b.class)"."""
    inside = [f for f in found if f]
    return f"{name} ({', '.join(inside[:3])})" if inside else name


def refusal(found: list[str], name: str, by_hand: bool = False) -> str:
    """The message for a refused jar. ``by_hand``: say how an owner who
    trusts the file can still add it (a mod or plugin)."""
    words = f"{NAME} found something in {where_words(found, name)}, so it wasn't installed."
    if by_hand:
        words += (
            " If you trust this file, drop it into the folder by hand: the jar check "
            "will move it to quarantine, and Undo there puts it back."
        )
    return words


def _test_file() -> bytes:
    """A jar holding the EICAR test file. Built from pieces so this source
    file isn't itself flagged."""
    text = "".join(
        [
            "X5O!P%@AP[4\\PZX54(P^)7CC)7}",
            "$EICAR-STANDARD-ANTIVIRUS",
            "-TEST-FILE!$H+H*",
        ]
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as jar:
        jar.writestr("test/test.txt", text)
    return out.getvalue()


def run_test(db: Any) -> dict[str, Any]:
    """Check the test jar and keep the result. Runs in a thread."""
    global _test
    if not load():
        return {"status": "off_windows"}
    with _lock:
        before, _test = _test, None  # a test after a failed one checks again for real
    found = scan_jar(_test_file(), "antivirus-test.jar")
    if found is None:
        _test = before
        return {"status": "couldnt"}
    _test = {"at": time.time(), "flagged": bool(found)}
    db.set_setting(TEST_KEY, _test)
    return {"status": status()}
