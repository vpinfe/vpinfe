"""Asks libpinmame about ROM sets. Run as a subprocess, never imported into the app.

The library spins an emulator core with global state and its struct layout can
drift between the versions VPX ships; in a worker a mismatch is a dead subprocess
reported as "audit unavailable", not a dead VPinFE. Same isolation reasoning as
dof_service_worker.

Usage: python -m common.host.pinmame_worker <libpath> <roms_dir> <set> [<set>...]
Prints one JSON object keyed by set name.

With `--serve <libpath>` instead, it loads the library once and answers one JSON line
per request line on stdin.
"""

from __future__ import annotations

import ctypes
import json
import shutil
import sys
import tempfile
from collections.abc import Iterable
from typing import Any, TextIO

PINMAME_MAX_PATH = 512
FILE_TYPE_ROMS = 0
STATUS_OK = 0


class PinmameGame(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("clone_of", ctypes.c_char_p),
        ("description", ctypes.c_char_p),
        ("year", ctypes.c_char_p),
        ("manufacturer", ctypes.c_char_p),
        ("flags", ctypes.c_uint32),
        ("found", ctypes.c_int32),
    ]


class PinmameConfig(ctypes.Structure):
    # audioFormat, sampleRate, vpmPath, then twelve callback pointers we leave
    # null - catalog reads never start the machine.
    _fields_ = [("audioFormat", ctypes.c_int), ("sampleRate", ctypes.c_int),
                ("vpmPath", ctypes.c_char * PINMAME_MAX_PATH),
                *((f"cb{i}", ctypes.c_void_p) for i in range(12))]


class Audit:
    """The library, opened once; each lookup points it at a roms folder and asks."""

    def __init__(self, lib_path: str) -> None:
        try:
            self._lib = ctypes.CDLL(lib_path)
        except OSError as exc:
            exc.library_load = True  # type: ignore[attr-defined]
            raise
        config = PinmameConfig()
        ctypes.memset(ctypes.byref(config), 0, ctypes.sizeof(config))
        # The library insists on a writable vpm home even for catalog reads.
        self._vpm_home = tempfile.mkdtemp(prefix="vpinfe-pinmame-")
        try:
            encoded_home = self._vpm_home.encode()
            ctypes.memmove(config.vpmPath, encoded_home,
                           min(len(encoded_home), PINMAME_MAX_PATH - 1))
            self._lib.PinmameSetConfig(ctypes.byref(config))
        except BaseException:
            self.close()
            raise
        self._hit: list[dict] = []
        game_callback = ctypes.CFUNCTYPE(None, ctypes.POINTER(PinmameGame), ctypes.c_void_p)
        self._callback = game_callback(self._on_game)

    def _on_game(self, game_ptr: Any, _user: Any) -> None:
        game = game_ptr.contents
        self._hit.append({
            "clone_of": (game.clone_of or b"").decode() or None,
            "description": (game.description or b"").decode(),
            "year": (game.year or b"").decode(),
            "manufacturer": (game.manufacturer or b"").decode(),
            "found": bool(game.found),
        })

    def lookup(self, roms_dir: str, names: list[str]) -> dict:
        self._lib.PinmameSetPath(FILE_TYPE_ROMS, roms_dir.encode())
        result = {}
        for name in names:
            self._hit.clear()
            status = self._lib.PinmameGetGame(name.encode(), self._callback, None)
            if status == STATUS_OK and self._hit:
                result[name] = {"catalog": True, **self._hit[0]}
            else:
                result[name] = {"catalog": False}
        return result

    def close(self) -> None:
        shutil.rmtree(self._vpm_home, ignore_errors=True)


def lookup(lib_path: str, roms_dir: str, names: list[str]) -> dict:
    audit = Audit(lib_path)
    try:
        return audit.lookup(roms_dir, names)
    finally:
        audit.close()


def serve(lib_path: str, requests: Iterable[str], answers: TextIO) -> int:
    """One request per line, `{"roms_dir": ..., "names": [...]}`; one answer per line.

    A request that fails is answered `{"error": ...}` and the next is still served.
    """
    try:
        audit = Audit(lib_path)
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}",
                          "load_failed": getattr(exc, "library_load", False)}),
              file=answers, flush=True)
        return 1
    try:
        for line in requests:
            try:
                request = json.loads(line)
                answer = audit.lookup(request["roms_dir"], request["names"])
            except Exception as exc:
                answer = {"error": f"{type(exc).__name__}: {exc}"}
            print(json.dumps(answer), file=answers, flush=True)
    finally:
        audit.close()
    return 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        return serve(sys.argv[2], sys.stdin, sys.stdout)
    if len(sys.argv) < 4:
        print(json.dumps({"error": "usage: pinmame_worker <lib> <roms_dir> <set>..."}))
        return 2
    try:
        print(json.dumps(lookup(sys.argv[1], sys.argv[2], sys.argv[3:])))
        return 0
    except Exception as exc:  # the whole point of the worker: die informatively
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
