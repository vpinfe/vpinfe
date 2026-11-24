"""PinMAME's catalog and audit, borrowed from the VPX install.

The locator and the chain-folding are pure logic, tested everywhere. The last
class drives the real shipped library and self-skips on machines without one -
which is exactly the availability story the feature itself tells.
"""

from __future__ import annotations

import io
import json
import os
import struct
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common.games.asset_resolver import apply_audit, resolve_rom_chain
from common.host import pinmame_catalog, pinmame_worker, tools


class LocatorTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop(pinmame_catalog.ENV_OVERRIDE, None)
        self.addCleanup(os.environ.pop, pinmame_catalog.ENV_OVERRIDE, None)

    def test_no_launcher_means_no_library(self) -> None:
        self.assertIsNone(pinmame_catalog.find_library(""))

    def test_linux_layout_finds_the_so_beside_the_binary(self) -> None:
        with TemporaryDirectory() as tmp:
            binary = Path(tmp) / "VPinballX_BGFX"
            binary.write_bytes(b"elf")
            (Path(tmp) / "libpinmame.so.3.7").write_bytes(b"so")

            found = pinmame_catalog.find_library(str(binary))

        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.name, "libpinmame.so.3.7")

    def test_macos_bundle_layout_finds_the_frameworks_dylib(self) -> None:
        with TemporaryDirectory() as tmp:
            macos = Path(tmp) / "VPinballX.app" / "Contents" / "MacOS"
            frameworks = Path(tmp) / "VPinballX.app" / "Contents" / "Frameworks"
            macos.mkdir(parents=True)
            frameworks.mkdir(parents=True)
            binary = macos / "VPinballX_BGFX"
            binary.write_bytes(b"macho")
            (frameworks / "libpinmame.dylib").write_bytes(b"dylib")

            found = pinmame_catalog.find_library(str(binary))

        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.parent.name, "Frameworks")

    def test_a_library_added_or_removed_after_a_look_is_seen_by_the_next(self) -> None:
        with TemporaryDirectory() as tmp:
            binary = Path(tmp) / "VPinballX_BGFX"
            binary.write_bytes(b"elf")
            self.assertIsNone(pinmame_catalog.find_library(str(binary)))

            library = Path(tmp) / "libpinmame.so"
            library.write_bytes(b"so")
            os.utime(tmp, (1, 1))
            self.assertEqual(pinmame_catalog.find_library(str(binary)), library)

            library.unlink()
            os.utime(tmp, (2, 2))
            self.assertIsNone(pinmame_catalog.find_library(str(binary)))

    def test_a_second_look_at_unchanged_directories_does_not_search_again(self) -> None:
        with TemporaryDirectory() as tmp:
            binary = Path(tmp) / "VPinballX_BGFX"
            binary.write_bytes(b"elf")
            (Path(tmp) / "libpinmame.so").write_bytes(b"so")
            first = pinmame_catalog.find_library(str(binary))

            with mock.patch.object(Path, "glob", side_effect=AssertionError("searched again")):
                self.assertEqual(pinmame_catalog.find_library(str(binary)), first)

    def test_the_env_override_wins_and_a_bad_override_is_none(self) -> None:
        with TemporaryDirectory() as tmp:
            lib = Path(tmp) / "libpinmame.so"
            lib.write_bytes(b"so")
            os.environ[pinmame_catalog.ENV_OVERRIDE] = str(lib)
            self.assertEqual(pinmame_catalog.find_library(""), lib)

            os.environ[pinmame_catalog.ENV_OVERRIDE] = str(lib) + ".missing"
            self.assertIsNone(pinmame_catalog.find_library(""))

    def test_availability_says_why_not(self) -> None:
        available, reason = pinmame_catalog.availability("")
        self.assertFalse(available)
        assert reason is not None
        self.assertIn("launcher", reason)

        with TemporaryDirectory() as tmp:
            binary = Path(tmp) / "VPinballX_BGFX"
            binary.write_bytes(b"elf")
            available, reason = pinmame_catalog.availability(str(binary))
        self.assertFalse(available)
        assert reason is not None
        self.assertIn("libpinmame", reason)


def _pe(bits: int) -> bytes:
    """The start of a Windows library, enough for its header to name a machine."""
    machine = {32: 0x14C, 64: 0x8664}[bits]
    head = bytearray(64)
    head[:2] = b"MZ"
    head[0x3C:0x40] = (64).to_bytes(4, "little")
    return bytes(head) + b"PE\0\0" + machine.to_bytes(2, "little") + bytes(32)


APP_BITS = struct.calcsize("P") * 8
OTHER_BITS = 32 if APP_BITS == 64 else 64


class LibraryChoiceTests(unittest.TestCase):
    """Protects: (d) a Windows install holding both a 32-bit and a 64-bit PinMAME gave
    every table's audit the one that cannot load."""

    def setUp(self) -> None:
        os.environ.pop(pinmame_catalog.ENV_OVERRIDE, None)
        pinmame_catalog.clear_cache()
        self.addCleanup(pinmame_catalog.clear_cache)

    def _install(self, tmp: str, **libraries: bytes) -> str:
        binary = Path(tmp) / "VPinballX_BGFX.exe"
        binary.write_bytes(b"MZ")
        for name, content in libraries.items():
            (Path(tmp) / f"{name}.dll").write_bytes(content)
        return str(binary)

    def test_the_library_for_this_bitness_wins_whatever_its_name_sorts_as(self) -> None:
        with TemporaryDirectory() as tmp:
            binary = self._install(tmp, libpinmame32=_pe(OTHER_BITS), libpinmame64=_pe(APP_BITS))

            found = pinmame_catalog.find_library(binary)

        assert found is not None
        self.assertEqual(found.name, "libpinmame64.dll")

    def test_a_library_only_for_the_other_bitness_is_none_and_is_named(self) -> None:
        with TemporaryDirectory() as tmp:
            binary = self._install(tmp, libpinmame32=_pe(OTHER_BITS))

            found = pinmame_catalog.find_library(binary)
            set_aside = pinmame_catalog.unusable(binary)
            available, reason = pinmame_catalog.availability(binary)

        self.assertIsNone(found)
        self.assertEqual(set_aside, [f"libpinmame32.dll ({OTHER_BITS}-bit)"])
        self.assertFalse(available)
        assert reason is not None
        self.assertIn("will not load", reason)

    def test_a_library_whose_header_cannot_be_read_is_still_offered(self) -> None:
        with TemporaryDirectory() as tmp:
            binary = self._install(tmp, libpinmame=b"not a header")

            found = pinmame_catalog.find_library(binary)

        assert found is not None
        self.assertEqual(found.name, "libpinmame.dll")

    def test_a_library_that_will_not_load_hands_over_to_the_next(self) -> None:
        asked: list[str] = []

        def ask(library: str, roms_dir: str, set_name: str) -> dict:
            asked.append(Path(library).name)
            if library.endswith("libpinmame1.dll"):
                return {"error": "OSError: not a valid Win32 application", "load_failed": True}
            return {set_name: {"catalog": True, "found": True}}

        with TemporaryDirectory() as tmp:
            binary = self._install(tmp, libpinmame1=b"x", libpinmame2=b"x")
            with mock.patch.object(pinmame_catalog, "_ask", side_effect=ask):
                with self.assertLogs("vpinfe.common.host.pinmame_catalog", level="WARNING"):
                    first = pinmame_catalog.lookup(binary, tmp, "afm_113b")
                second = pinmame_catalog.lookup(binary, tmp, "mm_109c")

        assert first is not None and second is not None
        self.assertEqual(asked, ["libpinmame1.dll", "libpinmame2.dll", "libpinmame2.dll"])

    def test_a_library_that_will_not_load_is_not_tried_again_for_the_next_table(self) -> None:
        fake = mock.Mock(return_value={"error": "OSError: no", "load_failed": True})
        with TemporaryDirectory() as tmp:
            binary = self._install(tmp, libpinmame=b"x")
            with mock.patch.object(pinmame_catalog, "_ask", fake):
                with self.assertLogs("vpinfe.common.host.pinmame_catalog", level="WARNING"):
                    results = [pinmame_catalog.lookup(binary, tmp, name)
                               for name in ("afm_113b", "mm_109c", "tz_92")]
            available, reason = pinmame_catalog.availability(binary)

        self.assertEqual(results, [None, None, None])
        self.assertEqual(fake.call_count, 1)
        self.assertFalse(available)
        assert reason is not None
        self.assertIn("will not load", reason)

    def test_a_helper_that_keeps_failing_is_given_up_on_after_a_few_goes(self) -> None:
        fake = mock.Mock(side_effect=BrokenPipeError)
        with TemporaryDirectory() as tmp:
            binary = self._install(tmp, libpinmame=b"x")
            with mock.patch.object(pinmame_catalog, "_ask", fake):
                with self.assertLogs("vpinfe.common.host.pinmame_catalog", level="WARNING"):
                    for index in range(pinmame_catalog.FAILURES_BEFORE_SKIP + 2):
                        pinmame_catalog.lookup(binary, tmp, f"set{index}")

        self.assertEqual(fake.call_count, pinmame_catalog.FAILURES_BEFORE_SKIP)

    def test_a_library_that_has_answered_is_not_given_up_on_for_failures_after(self) -> None:
        answer = {"afm_113b": {"catalog": True, "found": True}}
        outcomes = [answer] + [BrokenPipeError] * (pinmame_catalog.FAILURES_BEFORE_SKIP + 1)
        fake = mock.Mock(side_effect=outcomes)
        with TemporaryDirectory() as tmp:
            binary = self._install(tmp, libpinmame=b"x")
            with mock.patch.object(pinmame_catalog, "_ask", fake):
                with self.assertLogs("vpinfe.common.host.pinmame_catalog", level="WARNING"):
                    for index in range(len(outcomes)):
                        pinmame_catalog.lookup(binary, tmp, f"set{index}")
            found = pinmame_catalog.find_library(binary)

        self.assertEqual(fake.call_count, len(outcomes))
        self.assertIsNotNone(found, "one table that crashes the helper must not end the audit")


class HelperCommandTests(unittest.TestCase):
    def test_a_frozen_build_asks_its_own_executable_for_the_helper(self) -> None:
        with mock.patch.object(sys, "frozen", True, create=True):
            command = pinmame_catalog._helper_command("/vpx/libpinmame.so")

        self.assertEqual(command, [sys.executable, "--pinmame-helper", "--serve",
                                   "/vpx/libpinmame.so"])

    def test_a_source_run_names_the_module(self) -> None:
        command = pinmame_catalog._helper_command("/vpx/libpinmame.so")

        self.assertEqual(command, [sys.executable, "-m", "common.host.pinmame_worker",
                                   "--serve", "/vpx/libpinmame.so"])

    def test_the_flag_reaches_the_worker_and_does_not_start_the_app(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        proc = subprocess.run(
            [sys.executable, str(repo / "main.py"), "--pinmame-helper", "--serve",
             "/no/such/libpinmame.so"],
            input="", capture_output=True, text=True, timeout=60, cwd=str(repo))

        self.assertIn("error", json.loads(proc.stdout.splitlines()[0]))


class AuditFoldTests(unittest.TestCase):
    def _chain(self):
        return resolve_rom_chain("afm_113b", {}, [], required=True)

    def test_no_answer_leaves_the_name_match_standing(self) -> None:
        chain = apply_audit(self._chain(), None)

        self.assertEqual(chain["audit"], "unavailable")
        self.assertIsNone(chain["installed"])

    def test_a_full_set_makes_installed_true(self) -> None:
        chain = apply_audit(self._chain(),
                            {"catalog": True, "clone_of": "afm_113",
                             "description": "Attack From Mars (1.13b)", "found": True})

        self.assertEqual(chain["audit"], "ok")
        self.assertTrue(chain["installed"])
        self.assertEqual(chain["clone_of"], "afm_113")

    def test_the_audit_finally_makes_installed_false_sayable(self) -> None:
        """The name-match never says False; PinMAME's own audit may."""
        chain = apply_audit(self._chain(),
                            {"catalog": True, "clone_of": None,
                             "description": "x", "found": False})

        self.assertEqual(chain["audit"], "missing")
        self.assertFalse(chain["installed"])

    def test_an_unknown_set_is_not_declared_missing(self) -> None:
        """nfl_pat is not in the catalog: players reach it through an alias. Not
        being known is not the same as not being installed."""
        chain = apply_audit(self._chain(), {"catalog": False})

        self.assertEqual(chain["audit"], "unknown_set")
        self.assertIsNone(chain["installed"])


class WorkerContractTests(unittest.TestCase):
    def test_lookup_survives_a_dead_worker(self) -> None:
        with TemporaryDirectory() as tmp:
            lib = Path(tmp) / "libpinmame.so"
            lib.write_bytes(b"not really a library")
            binary = Path(tmp) / "VPinballX_BGFX"
            binary.write_bytes(b"elf")

            pinmame_catalog.clear_cache()
            self.addCleanup(pinmame_catalog.clear_cache)
            with self.assertLogs("vpinfe.common.host.pinmame_catalog", level="WARNING"):
                entry = pinmame_catalog.lookup(str(binary), tmp, "afm_113b")

        self.assertIsNone(entry, "a crashed worker is no answer, not a wrong one")

    def test_answers_are_cached_per_roms_folder_state(self) -> None:
        with TemporaryDirectory() as tmp:
            binary = Path(tmp) / "VPinballX_BGFX"
            binary.write_bytes(b"elf")
            (Path(tmp) / "libpinmame.so").write_bytes(b"so")

            pinmame_catalog.clear_cache()
            fake = mock.Mock(return_value={"afm_113b": {"catalog": True, "found": False}})
            with mock.patch.object(pinmame_catalog, "_ask", fake):
                first = pinmame_catalog.lookup(str(binary), tmp, "afm_113b")
                second = pinmame_catalog.lookup(str(binary), tmp, "afm_113b")

        self.assertEqual(first, second)
        self.assertEqual(fake.call_count, 1, "the second answer came from the cache")

    def test_many_lookups_share_one_helper_process(self) -> None:
        answers = iter(json.dumps({name: {"catalog": True, "found": True}}) + "\n"
                       for name in ("afm_113b", "mm_109c", "tz_92"))
        process = mock.Mock()
        process.poll.return_value = None
        process.stdout.readline.side_effect = lambda: next(answers)
        with TemporaryDirectory() as tmp:
            binary = Path(tmp) / "VPinballX_BGFX"
            binary.write_bytes(b"elf")
            (Path(tmp) / "libpinmame.so").write_bytes(b"so")

            pinmame_catalog.clear_cache()
            self.addCleanup(pinmame_catalog.clear_cache)
            with mock.patch.object(pinmame_catalog.subprocess, "Popen",
                                   return_value=process) as popen:
                found = [pinmame_catalog.lookup(str(binary), tmp, name)
                         for name in ("afm_113b", "mm_109c", "tz_92")]

        self.assertTrue(all(entry is not None and entry["found"] for entry in found))
        self.assertEqual(popen.call_count, 1)

    def test_the_helper_starts_without_a_window_of_its_own(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        process.stdout.readline.return_value = json.dumps(
            {"afm_113b": {"catalog": True, "found": True}}) + "\n"
        with TemporaryDirectory() as tmp:
            binary = Path(tmp) / "VPinballX_BGFX"
            binary.write_bytes(b"elf")
            (Path(tmp) / "libpinmame.so").write_bytes(b"so")

            pinmame_catalog.clear_cache()
            self.addCleanup(pinmame_catalog.clear_cache)
            with mock.patch.object(pinmame_catalog.subprocess, "Popen",
                                   return_value=process) as popen:
                pinmame_catalog.lookup(str(binary), tmp, "afm_113b")

        self.assertEqual(popen.call_args.kwargs["creationflags"], tools.NO_WINDOW)

    def test_a_helper_that_stops_answering_is_replaced_for_the_next_lookup(self) -> None:
        dead = mock.Mock()
        dead.poll.return_value = None
        dead.stdout.readline.return_value = ""
        live = mock.Mock()
        live.poll.return_value = None
        live.stdout.readline.return_value = json.dumps(
            {"mm_109c": {"catalog": True, "found": True}}) + "\n"
        with TemporaryDirectory() as tmp:
            binary = Path(tmp) / "VPinballX_BGFX"
            binary.write_bytes(b"elf")
            (Path(tmp) / "libpinmame.so").write_bytes(b"so")

            pinmame_catalog.clear_cache()
            self.addCleanup(pinmame_catalog.clear_cache)
            with mock.patch.object(pinmame_catalog.subprocess, "Popen",
                                   side_effect=[dead, live]):
                with self.assertLogs("vpinfe.common.host.pinmame_catalog", level="WARNING"):
                    first = pinmame_catalog.lookup(str(binary), tmp, "afm_113b")
                second = pinmame_catalog.lookup(str(binary), tmp, "mm_109c")

        self.assertIsNone(first)
        assert second is not None
        self.assertTrue(second["found"])


    def test_an_answer_that_is_not_an_object_is_no_answer(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        process.stdout.readline.return_value = "42\n"
        with TemporaryDirectory() as tmp:
            binary = Path(tmp) / "VPinballX_BGFX"
            binary.write_bytes(b"elf")
            (Path(tmp) / "libpinmame.so").write_bytes(b"so")

            pinmame_catalog.clear_cache()
            self.addCleanup(pinmame_catalog.clear_cache)
            with mock.patch.object(pinmame_catalog.subprocess, "Popen",
                                   return_value=process):
                with self.assertLogs("vpinfe.common.host.pinmame_catalog", level="WARNING"):
                    entry = pinmame_catalog.lookup(str(binary), tmp, "afm_113b")

        self.assertIsNone(entry)


class ServeTests(unittest.TestCase):
    def _serve(self, requests: list[str], audit: mock.Mock | Exception) -> list[dict]:
        out = io.StringIO()
        patch = (mock.patch.object(pinmame_worker, "Audit", side_effect=audit)
                 if isinstance(audit, Exception)
                 else mock.patch.object(pinmame_worker, "Audit", return_value=audit))
        with patch:
            pinmame_worker.serve("ignored", requests, out)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_one_answer_per_request_line_over_one_open_library(self) -> None:
        audit = mock.Mock()
        audit.lookup.side_effect = lambda roms, names: {names[0]: {"catalog": True, "roms": roms}}

        answers = self._serve(
            [json.dumps({"roms_dir": "/a", "names": ["afm_113b"]}),
             json.dumps({"roms_dir": "/b", "names": ["mm_109c"]})], audit)

        self.assertEqual(answers, [{"afm_113b": {"catalog": True, "roms": "/a"}},
                                   {"mm_109c": {"catalog": True, "roms": "/b"}}])
        audit.close.assert_called_once()

    def test_a_bad_request_is_answered_and_the_next_still_served(self) -> None:
        audit = mock.Mock()
        audit.lookup.return_value = {"afm_113b": {"catalog": False}}

        answers = self._serve(["not json",
                               json.dumps({"roms_dir": "/a", "names": ["afm_113b"]})], audit)

        self.assertIn("error", answers[0])
        self.assertEqual(answers[1], {"afm_113b": {"catalog": False}})

    def test_a_library_that_will_not_open_is_one_error_line(self) -> None:
        error = OSError("no such library")
        error.library_load = True  # type: ignore[attr-defined]
        answers = self._serve([json.dumps({"roms_dir": "/a", "names": ["x"]})], error)

        self.assertEqual(len(answers), 1)
        self.assertIn("no such library", answers[0]["error"])
        self.assertTrue(answers[0]["load_failed"], "the app skips a library that says this")

    def test_a_setup_failure_after_the_library_opened_does_not_blame_the_library(self) -> None:
        answers = self._serve([json.dumps({"roms_dir": "/a", "names": ["x"]})],
                              OSError("temp folder is read-only"))

        self.assertFalse(answers[0]["load_failed"])

    def test_a_failed_request_does_not_say_the_library_would_not_load(self) -> None:
        audit = mock.Mock()
        audit.lookup.side_effect = RuntimeError("bad set")

        answers = self._serve([json.dumps({"roms_dir": "/a", "names": ["x"]})], audit)

        self.assertNotIn("load_failed", answers[0])


def _shipped_library() -> str | None:
    for pattern in ("/Applications/VPinballX*.app/Contents/Frameworks/libpinmame*.dylib",):
        import glob
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


def _required_library() -> str:
    library = _shipped_library()
    assert library is not None
    return library


@unittest.skipUnless(_shipped_library(), "no VPX install with libpinmame on this machine")
class RealLibraryTests(unittest.TestCase):
    """Runs against the actual shipped library where one exists, and self-skips
    elsewhere - CI has no VPX install, and that is the availability story."""

    def _run_worker(self, roms_dir: str, *names: str,
                    env: dict[str, str] | None = None) -> dict:
        repo = Path(__file__).resolve().parents[2]
        proc = subprocess.run(
            [sys.executable, "-m", "common.host.pinmame_worker",
             _required_library(), roms_dir, *names],
            capture_output=True, text=True, timeout=60, cwd=str(repo),
            env={**os.environ, **env} if env else None,
        )
        return json.loads(proc.stdout)

    def test_the_catalog_knows_real_sets_and_rejects_dof_keys(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_worker(tmp, "mm_109c", "GTB2001_1971")

        self.assertTrue(result["mm_109c"]["catalog"])
        self.assertEqual(result["mm_109c"]["clone_of"], "mm_10")
        self.assertIn("Medieval Madness", result["mm_109c"]["description"])
        self.assertFalse(result["GTB2001_1971"]["catalog"])

    def test_a_lookup_removes_its_vpm_home_when_it_ends(self) -> None:
        with TemporaryDirectory() as roms, TemporaryDirectory() as temp_home:
            result = self._run_worker(roms, "afm_113b", env={"TMPDIR": temp_home})
            left = os.listdir(temp_home)

        self.assertTrue(result["afm_113b"]["catalog"])
        self.assertEqual([], left)

    def test_a_served_lookup_answers_as_the_one_shot_does_across_folders(self) -> None:
        with TemporaryDirectory() as stocked, TemporaryDirectory() as empty:
            with zipfile.ZipFile(Path(stocked) / "mm_109c.zip", "w") as archive:
                archive.writestr("mm_109c.u6", b"\0")
            repo = Path(__file__).resolve().parents[2]
            asked = [(stocked, "mm_109c"), (empty, "mm_109c"), (stocked, "mm_109c")]
            requests = "".join(json.dumps({"roms_dir": roms, "names": [name]}) + "\n"
                               for roms, name in asked)
            proc = subprocess.run(
                [sys.executable, "-m", "common.host.pinmame_worker", "--serve",
                 _required_library()],
                input=requests, capture_output=True, text=True, timeout=60, cwd=str(repo))
            one_shot = [self._run_worker(roms, name) for roms, name in asked]

        self.assertEqual([json.loads(line) for line in proc.stdout.splitlines()], one_shot)
        self.assertEqual([item["mm_109c"]["found"] for item in one_shot],
                         [one_shot[0]["mm_109c"]["found"], False,
                          one_shot[0]["mm_109c"]["found"]])

    def test_an_empty_roms_folder_audits_as_not_found(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self._run_worker(tmp, "afm_113b")

        self.assertTrue(result["afm_113b"]["catalog"])
        self.assertFalse(result["afm_113b"]["found"])


class WorkerVpmHomeTests(unittest.TestCase):
    """No shipped library needed: the whole of `ctypes` is faked, so this runs
    everywhere - and never builds a real callback trampoline against `PinmameConfig`,
    which native code out of Python's sight is free to hold onto past this test."""

    def test_a_raised_lookup_still_removes_its_vpm_home(self) -> None:
        made: list[str] = []
        real_mkdtemp = pinmame_worker.tempfile.mkdtemp

        def spying_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            made.append(path)
            return path

        fake_lib = mock.Mock()
        fake_lib.PinmameGetGame.side_effect = RuntimeError("worker crashed")
        with mock.patch.object(pinmame_worker, "ctypes", mock.MagicMock()), \
                mock.patch.object(pinmame_worker.ctypes, "CDLL", return_value=fake_lib), \
                mock.patch.object(pinmame_worker.tempfile, "mkdtemp",
                                  side_effect=spying_mkdtemp), \
                self.assertRaises(RuntimeError):
            pinmame_worker.lookup("ignored", "ignored", ["afm_113b"])

        self.assertEqual(1, len(made))
        self.assertFalse(os.path.exists(made[0]))


if __name__ == "__main__":
    unittest.main()
