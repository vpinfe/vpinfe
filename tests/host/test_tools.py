"""The Tools registry: what it finds, in what order, and what it says when it finds nothing."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from common.host import tools, vpinos

POSIX = not sys.platform.startswith("win")


def _program(folder: Path, name: str, script: str = "exit 0") -> Path:
    """An executable in `folder` that runs `script` under sh."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _works(_path: Path) -> tools.Probe:
    return tools.Probe(True, "1.0")


def _fails(_path: Path) -> tools.Probe:
    return tools.Probe(False, reason=tools.FAILED)


def _tool(probe: Callable[[Path], tools.Probe] = _works,
          names: tuple[str, ...] = ("thing",)) -> tools.Tool:
    return tools.Tool(id="thing", option="tools.thing_path", name="Thing",
                      names={tools.LINUX: names}, probe=probe,
                      hint={tools.LINUX: "tools.rar.hint.linux",
                            tools.VPINOS: "tools.hint.vpinos"})


@unittest.skipUnless(POSIX, "the programs here are shell scripts")
class _OnLinux(unittest.TestCase):
    """A Linux device whose PATH and whose places beyond it are folders of this test's,
    with nothing probed yet and no setting."""

    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.root = Path(held.name)
        self.path = self.root / "path"
        self.place = self.root / "place"
        self.path.mkdir()
        self.place.mkdir()
        for patch in (mock.patch.object(tools, "here", return_value=tools.LINUX),
                      mock.patch.object(vpinos, "detected", return_value=False),
                      mock.patch.dict(os.environ, {"PATH": str(self.path)}),
                      mock.patch.dict(tools._PLACES, {tools.LINUX: (str(self.place),)}),
                      mock.patch.dict(tools._probes, clear=True)):
            patch.start()
            self.addCleanup(patch.stop)


class ResolveTests(_OnLinux):
    def test_a_tool_with_nothing_to_run_here_is_not_here(self) -> None:
        asked = mock.Mock(side_effect=_works)
        with mock.patch.object(tools, "here", return_value=tools.WINDOWS):
            found = tools.resolve(_tool(asked), "")

        self.assertIs(found.state, tools.State.NOT_HERE)
        asked.assert_not_called()

    def test_found_on_path(self) -> None:
        program = _program(self.path, "thing")

        found = tools.resolve(_tool(), "")

        self.assertIs(found.state, tools.State.FOUND)
        self.assertEqual(found.path, program)
        self.assertFalse(found.set_here)

    def test_found_where_a_short_path_does_not_look(self) -> None:
        program = _program(self.place, "thing")

        self.assertEqual(tools.resolve(_tool(), "").path, program)

    def test_the_first_name_wins_over_where_a_copy_is(self) -> None:
        _program(self.path, "worse")
        better = _program(self.place, "better")

        self.assertEqual(tools.resolve(_tool(names=("better", "worse")), "").path, better)

    def test_the_setting_wins_when_its_program_runs(self) -> None:
        _program(self.path, "thing")
        mine = _program(self.root / "mine", "thing")

        found = tools.resolve(_tool(), str(mine))

        self.assertEqual(found.path, mine)
        self.assertTrue(found.set_here)

    def test_a_setting_whose_program_does_not_run_is_passed_over(self) -> None:
        found_on_path = _program(self.path, "thing")
        broken = _program(self.root / "mine", "thing")

        def probe(path: Path) -> tools.Probe:
            return _fails(path) if path == broken else _works(path)

        found = tools.resolve(_tool(probe), str(broken))

        self.assertIs(found.state, tools.State.FOUND)
        self.assertEqual(found.path, found_on_path)
        self.assertFalse(found.set_here)

    def test_a_setting_naming_nothing_is_passed_over(self) -> None:
        program = _program(self.path, "thing")

        self.assertEqual(tools.resolve(_tool(), str(self.root / "gone")).path, program)

    def test_found_but_not_running_is_unusable(self) -> None:
        program = _program(self.path, "thing")

        found = tools.resolve(_tool(_fails), "")

        self.assertIs(found.state, tools.State.UNUSABLE)
        self.assertEqual(found.path, program)

    def test_nothing_anywhere_is_missing(self) -> None:
        self.assertIs(tools.resolve(_tool(), "").state, tools.State.MISSING)


class ProbeOnceTests(_OnLinux):
    def test_a_program_is_asked_once_until_the_file_changes(self) -> None:
        program = _program(self.path, "thing")
        asked = mock.Mock(side_effect=_works)
        tool = _tool(asked)

        tools.resolve(tool, "")
        tools.resolve(tool, "")
        self.assertEqual(asked.call_count, 1)

        _program(self.path, "thing", "echo a newer build\nexit 0")
        tools.resolve(tool, "")
        self.assertEqual(asked.call_count, 2)
        self.assertEqual(asked.call_args.args, (program,))

    def test_a_program_that_did_not_work_is_asked_again(self) -> None:
        _program(self.path, "thing")
        asked = mock.Mock(side_effect=_fails)

        tools.resolve(_tool(asked), "")
        tools.resolve(_tool(asked), "")

        self.assertEqual(asked.call_count, 2)


class RowTests(_OnLinux):
    def test_found(self) -> None:
        program = _program(self.path, "thing")

        row = tools.row(tools.resolve(_tool(), ""))

        self.assertEqual(row, {
            "id": "thing", "name": "Thing", "setting": "tools.thing_path",
            "state": "found", "path": str(program), "version": "1.0", "set_here": False,
            "default_path": str(program), "default_version": "1.0",
            "reason": None, "fix": "none", "remedy": None})

    def test_a_set_program_leaves_the_default_what_is_found_without_it(self) -> None:
        found = _program(self.path, "thing")
        mine = _program(self.path.parent / "mine", "thing")

        row = tools.row(tools.resolve(_tool(), str(mine)))

        self.assertEqual((row["set_here"], row["path"]), (True, str(mine)))
        self.assertEqual(row["default_path"], str(found))

    def test_missing_carries_its_remedy(self) -> None:
        row = tools.row(tools.resolve(_tool(), ""))

        self.assertEqual((row["state"], row["fix"]), ("missing", "user"))
        self.assertEqual(row["remedy"], {"key": "tools.rar.hint.linux",
                                         "params": {"tool": "Thing"},
                                         "setting": "tools.thing_path"})

    def test_unusable_says_why_and_how_to_fix_it(self) -> None:
        _program(self.path, "thing")

        row = tools.row(tools.resolve(_tool(_fails), ""))

        self.assertEqual(row["state"], "unusable")
        self.assertEqual(row["reason"], {"key": tools.FAILED, "params": {}})
        self.assertEqual(row["fix"], "user")
        self.assertIsNotNone(row["remedy"])


class HintTests(_OnLinux):
    def test_on_vpinos_the_hint_is_vpinos_own(self) -> None:
        with mock.patch.object(vpinos, "detected", return_value=True):
            said = tools.hint(_tool())

        self.assertTrue(said.startswith("VPinOS doesn't include Thing yet"), said)
        self.assertNotIn("package manager", said)

    def test_elsewhere_on_linux_it_is_the_distribution_s(self) -> None:
        self.assertIn("package manager", tools.hint(_tool()))

    def test_a_hint_names_the_setting_that_points_at_one(self) -> None:
        self.assertIn("RAR Tool Path", tools.hint(tools.RAR))


@unittest.skipUnless(POSIX, "the programs here are shell scripts")
class AskTests(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.root = Path(held.name)

    def test_both_streams_are_read(self) -> None:
        program = _program(self.root, "talks", 'echo "out $1"\necho "err" >&2')

        said = tools.ask(program, "-version")

        self.assertIn("out -version", said)
        self.assertIn("err", said)

    def test_a_program_that_fails_is_not_the_tool(self) -> None:
        program = _program(self.root, "fails", "exit 3")

        self.assertEqual(tools.probing(lambda path: tools.Probe(True, tools.ask(path)))(
            program).reason, tools.FAILED)

    def test_a_program_that_does_not_finish_timed_out(self) -> None:
        program = _program(self.root, "waits", "sleep 5")

        with mock.patch.object(tools, "TIMEOUT", 0.2):
            probe = tools.probing(lambda path: tools.Probe(True, tools.ask(path)))(program)

        self.assertEqual(probe.reason, tools.TIMED_OUT)

    def test_a_file_that_cannot_be_run_did_not_start(self) -> None:
        text = self.root / "notes"
        text.write_text("not a program", encoding="utf-8")

        probe = tools.probing(lambda path: tools.Probe(True, tools.ask(path)))(text)

        self.assertEqual(probe.reason, tools.DID_NOT_START)


class VersionTests(unittest.TestCase):
    def test_the_first_dotted_number_on_the_first_line(self) -> None:
        for said, version in (
            ("ffmpeg version 9.0.1 Copyright (c) 2000-2026 the FFmpeg developers\n"
             "built with Apple clang version 21.0.0", "9.0.1"),
            ("ffmpeg version 7.1-full_build-www.gyan.dev Copyright (c) 2000-2024", "7.1"),
            ("ffmpeg version N-118000-g1a2b3c4d Copyright (c) 2000-2025\n"
             "built with gcc 14.2.0", ""),
            ("bsdtar 3.5.3 - libarchive 3.7.4 zlib/1.2.12", "3.5.3"),
            ("v1.10.8\n", "1.10.8"),
            ("\n7-Zip [64] 16.02 : Copyright (c) 1999-2016 Igor Pavlov", "16.02"),
            ("", ""),
        ):
            with self.subTest(said=said[:30]):
                self.assertEqual(tools.version_in(said), version)


class RarTests(unittest.TestCase):
    def test_each_program_is_the_rarfile_backend_its_name_says(self) -> None:
        for name, backend in (("unrar", "UNRAR"), ("UnRAR.exe", "UNRAR"),
                              ("unar", "UNAR"), ("7zz", "SEVENZIP2"),
                              ("7z.exe", "SEVENZIP"), ("bsdtar", "BSDTAR"),
                              ("rar-extractor", "UNRAR")):
            with self.subTest(name=name):
                self.assertEqual(tools.rar_backend(Path(name)), backend)

    @unittest.skipUnless(POSIX, "the programs here are shell scripts")
    def test_it_is_probed_with_the_check_rarfile_runs(self) -> None:
        import rarfile

        with tempfile.TemporaryDirectory() as held:
            check = " ".join(rarfile.UNAR_CONFIG["check_cmd"][1:])
            unar = _program(Path(held), "unar",
                            f'[ "$*" = "{check}" ] || exit 1\necho v1.10.8')

            probe = tools.RAR.probe(unar)

        self.assertTrue(probe.works)
        self.assertEqual(probe.version, "1.10.8")

    @unittest.skipUnless(POSIX, "the places are Windows folders named from a variable")
    def test_on_windows_it_looks_where_winrar_and_7zip_install(self) -> None:
        with tempfile.TemporaryDirectory() as held:
            seven = _program(Path(held) / "7-Zip", "7z")
            with (mock.patch.object(tools, "here", return_value=tools.WINDOWS),
                  mock.patch.dict(os.environ, {"PATH": "", "ProgramFiles": held}),
                  mock.patch.object(tools, "probed", return_value=tools.Probe(True))):
                self.assertEqual(tools.resolve(tools.RAR, "").path, seven)


FFMPEG_ENCODERS = """Encoders:
 V..... = Video
 A..... = Audio
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC (codec h264)
 V....D h264_videotoolbox    VideoToolbox H.264 Encoder (codec h264)
 VF...D png                  PNG (Portable Network Graphics) image
 V....D libvpx-vp9           libvpx VP9 (codec vp9)
 A....D libmp3lame           libmp3lame MP3 (MPEG audio layer 3) (codec mp3)
"""

FFMPEG_DEVICES = """Devices:
 D. = Demuxing supported
 .E = Muxing supported
 ---
  E audiotoolbox    AudioToolbox output device
 D  avfoundation    AVFoundation input device
 DE pulse           Pulse audio output
 D  lavfi           Libavfilter virtual input device
"""

# The key's own lines are a filter's shape too, and name no filter.
FFMPEG_FILTERS = """Filters:
  T.. = Timeline support
  .S. = Slice threading
  A = Audio input/output
  V = Video input/output
  N = Dynamic number and/or type of input/output
  | = Source or sink filter
 .. ddagrab           |->V       Grab Windows Desktop images using Desktop Duplication API
 .. hwdownload        V->V       Download a hardware frame to a normal frame
 .S signalstats       V->V       Generate statistics from video analysis.
 TSC overlay          VV->V      Overlay a video source on top of the input.
 .. amix              N->A       Audio mixing.
"""

# gst-inspect-1.0 with no argument, as a pipe reads it: `plugin:  element: name`, and a
# type finder, `plugin: type: extensions`, with one space.
GST_INSPECT = """coreelements:  capsfilter: CapsFilter
coreelements:  filesink: File Sink
matroska:  matroskamux: Matroska muxer
pipewire:  pipewiresrc: PipeWire source
png:  pngenc: PNG image encoder
typefindfunctions: video/x-matroska: mkv, mka, mk3d, webm
videoconvertscale:  videoconvert: Colorspace converter
videorate:  videorate: Video rate adjuster
x264:  x264enc: x264 H.264 Encoder

Total count: 8 plugins, 9 features
"""

GRIM_HELP = """Usage: grim [options...] [output-file]

  -h              Show help message and quit.
  -s <factor>     Set the output image's scale factor.
  -o <output>     Set the output name to capture.
  -c              Include cursors in the screenshot.
"""


@unittest.skipUnless(POSIX, "the programs here are shell scripts")
class _Answering(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.root = Path(held.name)
        self.asked = self.root / "asked"

    def _answering(self, name: str, **said: str) -> Path:
        """A program that notes each argument list it is given, and prints what `said`
        holds for that list's first argument."""
        cases = "\n".join(f"  {flag.replace('_', '-')}) cat <<'EOF'\n{text}EOF\n  ;;"
                          for flag, text in said.items())
        return _program(self.root, name,
                        f'echo "$*" >> "{self.asked}"\ncase "$1" in\n{cases}\n'
                        "  *) exit 1 ;;\nesac")

    def _args(self) -> list[str]:
        return self.asked.read_text(encoding="utf-8").splitlines()


@unittest.skipUnless(POSIX, "the programs here are shell scripts")
class RecordingToolTests(_Answering):
    def test_ffmpeg_says_what_it_encodes_and_reads_from(self) -> None:
        ffmpeg = _program(self.root, "ffmpeg", f"""case "$2" in
  -version) echo "ffmpeg version 9.0.1 Copyright (c) 2000-2026 the FFmpeg developers" ;;
  -encoders) cat <<'EOF'
{FFMPEG_ENCODERS}EOF
  ;;
  -devices) cat <<'EOF'
{FFMPEG_DEVICES}EOF
  ;;
  -filters) cat <<'EOF'
{FFMPEG_FILTERS}EOF
  ;;
  *) exit 1 ;;
esac""")

        probe = tools.FFMPEG.probe(ffmpeg)

        self.assertTrue(probe.works)
        self.assertEqual(probe.version, "9.0.1")
        self.assertEqual(probe.can[tools.ENCODERS],
                         {"libx264", "h264_videotoolbox", "png", "libvpx-vp9", "libmp3lame"})
        self.assertEqual(probe.can[tools.INPUTS], {"avfoundation", "pulse", "lavfi"})
        self.assertEqual(probe.can[tools.FILTERS],
                         {"ddagrab", "hwdownload", "signalstats", "overlay", "amix"})

    def test_a_program_that_lists_no_encoder_is_not_ffmpeg(self) -> None:
        says_nothing = _program(self.root, "ffmpeg")

        self.assertEqual(tools.FFMPEG.probe(says_nothing).reason, tools.FAILED)

    def test_what_grim_can_do_is_what_its_help_lists(self) -> None:
        grim = self._answering("grim", **{"-h": GRIM_HELP})

        probe = tools.GRIM.probe(grim)

        self.assertTrue(probe.works)
        self.assertTrue(probe.has(tools.OPTIONS, "-o"))
        self.assertFalse(probe.has(tools.OPTIONS, "-D"))
        self.assertEqual(probe.version, "")
        self.assertEqual(self._args(), ["-h"])

    def test_the_version_is_asked_for_only_where_the_help_lists_it(self) -> None:
        wf = self._answering("wf-recorder", **{
            "-h": "Usage: wf-recorder [OPTION]...\n  -D, --no-damage   Record every frame\n"
                  "  -v, --version     Prints the version of wf-recorder.\n",
            "__version": "wf-recorder 0.5.0\n"})

        probe = tools.WF_RECORDER.probe(wf)

        self.assertTrue(probe.has(tools.OPTIONS, "--no-damage"))
        self.assertEqual(probe.version, "0.5.0")
        self.assertEqual(self._args(), ["-h", "--version"])

    def test_help_that_lists_no_option_is_not_the_tool(self) -> None:
        other = self._answering("grim", **{"-h": "hello\n"})

        self.assertEqual(tools.GRIM.probe(other).reason, tools.FAILED)

    def test_grim_and_wf_recorder_are_not_here_off_linux(self) -> None:
        for where in (tools.DARWIN, tools.WINDOWS):
            with (self.subTest(where=where),
                  mock.patch.object(tools, "here", return_value=where)):
                self.assertIs(tools.resolve(tools.GRIM, "").state, tools.State.NOT_HERE)
                self.assertIs(tools.resolve(tools.WF_RECORDER, "").state,
                              tools.State.NOT_HERE)
                self.assertIs(tools.resolve(tools.GSTREAMER, "").state,
                              tools.State.NOT_HERE)

    def test_gstreamers_elements_are_what_the_inspect_beside_it_lists(self) -> None:
        launch = self._answering("gst-launch-1.0", **{
            "__version": "gst-launch-1.0 version 1.24.2\nGStreamer 1.24.2\n"})
        _program(self.root, "gst-inspect-1.0", f"cat <<'EOF'\n{GST_INSPECT}EOF")

        probe = tools.GSTREAMER.probe(launch)

        self.assertTrue(probe.works)
        self.assertEqual(probe.version, "1.24.2")
        self.assertEqual(probe.can[tools.ELEMENTS],
                         {"capsfilter", "filesink", "matroskamux", "pipewiresrc", "pngenc",
                          "videoconvert", "videorate", "x264enc"})
        self.assertEqual(self._args(), ["--version"])

    def test_an_inspect_that_lists_no_element_is_not_gstreamers(self) -> None:
        launch = self._answering("gst-launch-1.0", **{"__version": "1.24.2\n"})
        _program(self.root, "gst-inspect-1.0", "echo 'Total count: 0 plugins'")

        self.assertEqual(tools.GSTREAMER.probe(launch).reason, tools.FAILED)


# ydotool's own help, as its client prints it.
YDOTOOL_HELP = """Usage: ydotool <cmd> <args>
Available commands:
  click
  mousemove
  type
  key
  debug
  bakers
"""


@unittest.skipUnless(POSIX, "the programs here are shell scripts")
class KeyToolTests(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.root = Path(held.name)
        self.asked = self.root / "asked"

    def _noting(self, name: str, body: str) -> Path:
        return _program(self.root, name, f'echo "[$*]" >> "{self.asked}"\n{body}')

    def _args(self) -> list[str]:
        return self.asked.read_text(encoding="utf-8").splitlines()

    def test_wtype_is_asked_with_nothing_to_type(self) -> None:
        wtype = self._noting("wtype", 'echo "Usage: wtype <text-to-type>" >&2\nexit 1')

        probe = tools.WTYPE.probe(wtype)

        self.assertTrue(probe.works)
        self.assertEqual(self._args(), ["[]"])

    def test_a_program_that_prints_no_usage_is_not_wtype(self) -> None:
        other = self._noting("wtype", "exit 1")

        self.assertEqual(tools.WTYPE.probe(other).reason, tools.FAILED)

    def test_ydotool_lists_its_commands_from_its_help(self) -> None:
        ydotool = self._noting("ydotool", f"cat <<'EOF'\n{YDOTOOL_HELP}EOF")

        probe = tools.YDOTOOL.probe(ydotool)

        self.assertTrue(probe.works)
        self.assertTrue(probe.has(tools.COMMANDS, "key"))
        self.assertEqual(self._args(), ["[help]"])

    def test_a_ydotool_that_cannot_press_a_key_is_not_usable(self) -> None:
        ydotool = self._noting(
            "ydotool", "echo 'Usage: ydotool <cmd> <args>'\necho 'Available commands:'\n"
                       "echo '  type'")

        self.assertEqual(tools.YDOTOOL.probe(ydotool).reason, tools.FAILED)

    def test_both_are_linux_only(self) -> None:
        for where in (tools.DARWIN, tools.WINDOWS):
            with (self.subTest(where=where),
                  mock.patch.object(tools, "here", return_value=where)):
                self.assertIs(tools.resolve(tools.WTYPE, "").state, tools.State.NOT_HERE)
                self.assertIs(tools.resolve(tools.YDOTOOL, "").state, tools.State.NOT_HERE)


NVTOP_HELP = """Available options:
  -d --delay : Select the refresh rate (1 == 0.1s)
  -v --version : Print the version and quit
  -s --snapshot : Output the current gpu information and quit
"""

NVTOP_OLD_HELP = """Available options:
  -d --delay : Select the refresh rate (1 == 0.1s)
  -v --version : Print the version and quit
"""


@unittest.skipUnless(POSIX, "the programs here are shell scripts")
class GraphicsToolTests(_Answering):
    def test_nvtop_can_snapshot_where_its_help_lists_s(self) -> None:
        nvtop = self._answering("nvtop", **{"-h": NVTOP_HELP, "__version": "nvtop version 3.2.0\n"})

        probe = tools.NVTOP.probe(nvtop)

        self.assertTrue(probe.works)
        self.assertEqual(probe.version, "3.2.0")
        self.assertEqual(self._args(), ["-h", "--version"])

    def test_an_nvtop_without_a_snapshot_is_unusable_whatever_its_version(self) -> None:
        nvtop = self._answering("nvtop", **{"-h": NVTOP_OLD_HELP})

        probe = tools.NVTOP.probe(nvtop)

        self.assertEqual((probe.works, probe.reason), (False, tools.NO_SNAPSHOT))

    def test_nvidia_smi_works_where_it_lists_a_driver(self) -> None:
        smi = _program(self.root, "nvidia-smi", "echo 535.154.05")

        probe = tools.NVIDIA_SMI.probe(smi)

        self.assertTrue(probe.works)
        self.assertEqual(probe.version, "535.154.05")

    def test_nvidia_smi_that_lists_nothing_or_fails_is_not_usable(self) -> None:
        for script in ("echo", "exit 9"):
            with self.subTest(script=script):
                smi = _program(self.root, "nvidia-smi", script)

                self.assertFalse(tools.NVIDIA_SMI.probe(smi).works)

    def test_nvidia_smi_is_here_on_linux_and_windows_and_nvtop_on_linux_only(self) -> None:
        for where, smi, top in ((tools.LINUX, True, True), (tools.WINDOWS, True, False),
                                (tools.DARWIN, False, False)):
            with self.subTest(where=where), mock.patch.object(tools, "here", return_value=where):
                self.assertEqual(tools.resolve(tools.NVIDIA_SMI, "").state
                                 is not tools.State.NOT_HERE, smi)
                self.assertEqual(tools.resolve(tools.NVTOP, "").state
                                 is not tools.State.NOT_HERE, top)

    def test_a_tool_nothing_needs_is_not_here_in_the_report_but_a_reader_still_resolves_it(
            self) -> None:
        tool = tools.Tool("thing", "tools.thing_path", "Thing", {tools.LINUX: ("thing",)},
                          _works, {tools.LINUX: "tools.hint.linux"}, needed=lambda: False)
        with mock.patch.object(tools, "here", return_value=tools.LINUX), \
                mock.patch.object(tools, "TOOLS", (tool,)):
            self.assertEqual(tools.report()[0]["state"], tools.State.NOT_HERE.value)
            self.assertIsNot(tools.resolve(tool, "").state, tools.State.NOT_HERE)

    def test_what_does_not_run_on_this_system_is_not_asked_whether_it_is_needed(self) -> None:
        asked = mock.Mock(return_value=True)
        tool = tools.Tool("thing", "tools.thing_path", "Thing", {tools.LINUX: ("thing",)},
                          _works, {tools.LINUX: "tools.hint.linux"}, needed=asked)
        with mock.patch.object(tools, "here", return_value=tools.WINDOWS), \
                mock.patch.object(tools, "TOOLS", (tool,)):
            self.assertEqual(tools.report()[0]["state"], tools.State.NOT_HERE.value)
        asked.assert_not_called()

    def test_the_nvidia_driver_is_what_the_hint_names_on_each_platform(self) -> None:
        for where in (tools.LINUX, tools.WINDOWS):
            with self.subTest(where=where), mock.patch.object(tools, "here", return_value=where):
                self.assertIn("NVIDIA driver", tools.hint(tools.NVIDIA_SMI))


class VPinOSTests(unittest.TestCase):
    def setUp(self) -> None:
        vpinos.detected.cache_clear()
        self.addCleanup(vpinos.detected.cache_clear)

    def _on_linux_reading(self, read: mock.Mock) -> bool:
        with (mock.patch.object(vpinos.sys, "platform", "linux"),
              mock.patch.object(vpinos.platform, "freedesktop_os_release", read)):
            return vpinos.detected()

    def test_vpinos_names_itself(self) -> None:
        self.assertTrue(self._on_linux_reading(
            mock.Mock(return_value={"ID": "vpinos", "ID_LIKE": "debian"})))

    def test_another_distribution_is_not(self) -> None:
        self.assertFalse(self._on_linux_reading(mock.Mock(return_value={"ID": "debian"})))

    def test_no_os_release_is_not(self) -> None:
        self.assertFalse(self._on_linux_reading(mock.Mock(side_effect=OSError)))

    def test_not_linux_is_not_asked(self) -> None:
        with (mock.patch.object(vpinos.sys, "platform", "darwin"),
              mock.patch.object(vpinos.platform, "freedesktop_os_release") as read):
            self.assertFalse(vpinos.detected())
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
