"""What a media file's own header says it is."""

from __future__ import annotations

import io
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from common.games.media_probe import probe
from common.host import tools


def _box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), kind) + body


def _mvhd(scale: int, length: int, version: int = 0) -> bytes:
    if version == 1:
        body = bytes([1, 0, 0, 0]) + bytes(16) + struct.pack(">IQ", scale, length)
    else:
        body = bytes(4) + bytes(8) + struct.pack(">II", scale, length)
    return _box(b"mvhd", body + bytes(80))


def _tkhd(width: int, height: int) -> bytes:
    body = bytes(4) + bytes(20) + bytes(52) + struct.pack(">II", width << 16, height << 16)
    return _box(b"tkhd", body)


def _stsd(entry: bytes) -> bytes:
    """A track's sample description, down through the boxes that hold it."""
    stsd = _box(b"stsd", bytes(4) + struct.pack(">I", 1) + _box(entry, bytes(78)))
    return _box(b"mdia", _box(b"hdlr", bytes(24))
                + _box(b"minf", _box(b"stbl", stsd)))


def _mp4(*, moov_last: bool = False, version: int = 0, sound: bytes = b"mp4a",
         picture: bytes = b"avc1") -> bytes:
    moov = _box(b"moov", _mvhd(1000, 30_500, version)
                + _box(b"trak", _tkhd(0, 0) + _stsd(sound))
                + _box(b"trak", _tkhd(1920, 1080) + _stsd(picture)))
    head = _box(b"ftyp", b"isom" + bytes(4))
    media = _box(b"mdat", bytes(4096))
    return head + (media + moov if moov_last else moov + media)


# MPEG-1 Layer III, 128 kbps, 44.1 kHz, stereo: 417 bytes a frame, 1,152 samples.
_FRAME = b"\xff\xfb\x90\x00"
_FRAME_BYTES = 417


def _mp3(frames: int, *, xing: bool, id3: bool = False) -> bytes:
    first = bytearray(_FRAME + bytes(_FRAME_BYTES - 4))
    if xing:
        first[4 + 32:4 + 32 + 12] = b"Xing" + struct.pack(">II", 1, frames)
    audio = bytes(first) + (_FRAME + bytes(_FRAME_BYTES - 4)) * (frames - 1)
    tag = b"ID3\x04\x00\x00" + bytes([0, 0, 0, 20]) + bytes(20) if id3 else b""
    return tag + audio


def _ogg_page(granule: int, packet: bytes) -> bytes:
    return (b"OggS" + bytes(2) + struct.pack("<q", granule) + bytes(12)
            + bytes([1, len(packet)]) + packet)


def _vorbis(rate: int, samples: int) -> bytes:
    ident = b"\x01vorbis" + struct.pack("<IBI", 0, 2, rate) + bytes(15)
    return _ogg_page(0, ident) + _ogg_page(samples // 2, bytes(40)) \
        + _ogg_page(samples, bytes(40))


def _opus(skip: int, samples: int) -> bytes:
    ident = b"OpusHead" + bytes([1, 2]) + struct.pack("<HI", skip, 44_100) + bytes(3)
    return _ogg_page(0, ident) + _ogg_page(samples + skip, bytes(40))


class TheHeaderSaysWhatItIs(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.root = Path(held.name)

    def _probe(self, name: str, data: bytes) -> dict:
        path = self.root / name
        path.write_bytes(data)
        return probe(path)

    def test_a_video_has_its_size_and_running_time(self) -> None:
        facts = self._probe("table.mp4", _mp4())

        self.assertEqual(("MP4", 1920, 1080, 30.5),
                         (facts["format"], facts["width"], facts["height"],
                          facts["duration_s"]))

    def test_the_movie_header_is_found_after_the_media(self) -> None:
        self.assertEqual(30.5, self._probe("bg.mp4", _mp4(moov_last=True))["duration_s"])

    def test_a_wide_movie_header_is_read(self) -> None:
        self.assertEqual(30.5, self._probe("dmd.mp4", _mp4(version=1))["duration_s"])

    def test_a_variable_rate_sound_is_timed_by_its_frame_count(self) -> None:
        facts = self._probe("audio.mp3", _mp3(100, xing=True))

        self.assertEqual("MP3", facts["format"])
        self.assertAlmostEqual(100 * 1152 / 44_100, facts["duration_s"], places=3)

    def test_a_constant_rate_sound_is_timed_by_its_size(self) -> None:
        facts = self._probe("audio.mp3", _mp3(100, xing=False, id3=True))

        self.assertAlmostEqual(100 * _FRAME_BYTES * 8 / 128_000, facts["duration_s"],
                               places=2)

    def test_a_vorbis_sound_is_timed_by_its_last_page(self) -> None:
        facts = self._probe("audio.ogg", _vorbis(44_100, 88_200))

        self.assertEqual(("OGG", 2.0), (facts["format"], facts["duration_s"]))

    def test_an_opus_sound_leaves_out_what_it_skips(self) -> None:
        self.assertEqual(1.0, self._probe("audio.ogg", _opus(312, 48_000))["duration_s"])

    def test_an_image_says_what_it_really_is_not_what_it_is_named(self) -> None:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (64, 48)).save(buffer, "JPEG")
        facts = self._probe("wheel.png", buffer.getvalue())

        self.assertEqual(("JPEG", 64, 48), (facts["format"], facts["width"],
                                            facts["height"]))

    def test_a_file_that_is_not_what_it_claims_falls_back_to_its_name(self) -> None:
        facts = self._probe("track01.mp3", b"not really a sound!!")

        self.assertEqual(("MP3", None), (facts["format"], facts["duration_s"]))

    def test_a_slot_detail_carries_them_beside_its_size(self) -> None:
        from common.games import media_ops

        path = self.root / "table.mp4"
        path.write_bytes(_mp4())
        facts = media_ops._file_facts(path)

        self.assertEqual(("MP4", 30.5, path.stat().st_size),
                         (facts["format"], facts["duration_s"], facts["size_bytes"]))

    def test_a_cut_off_video_costs_the_numbers_not_the_answer(self) -> None:
        facts = self._probe("table.mp4", _mp4()[:40])

        self.assertEqual(("MP4", None), (facts["format"], facts["duration_s"]))

    def test_a_video_names_its_codec_as_a_browser_is_asked_about_it(self) -> None:
        named = {entry: self._probe(f"{entry.decode()}.mp4", _mp4(picture=entry))
                 ["video_codec"]
                 for entry in (b"avc1", b"avc3", b"hvc1", b"hev1", b"vp09", b"av01")}

        self.assertEqual(named, {b"avc1": "h264", b"avc3": "h264", b"hvc1": "hevc",
                                 b"hev1": "hevc", b"vp09": "vp9", b"av01": "av1"})

    def test_the_codec_is_found_after_the_media_too(self) -> None:
        facts = self._probe("bg.mp4", _mp4(moov_last=True, picture=b"vp09"))

        self.assertEqual(facts["video_codec"], "vp9")

    def test_a_codec_no_browser_is_asked_about_is_not_known(self) -> None:
        self.assertIsNone(self._probe("dmd.mp4", _mp4(picture=b"mp4v"))["video_codec"])

    def test_a_sound_says_no_video_codec(self) -> None:
        self.assertIsNone(self._probe("audio.mp3", _mp3(100, xing=True))["video_codec"])

    def test_a_file_read_once_is_not_read_again_until_it_changes(self) -> None:
        path = self.root / "table.mp4"
        path.write_bytes(_mp4(picture=b"avc1"))
        probe(path)
        stamp = path.stat().st_mtime_ns
        path.write_bytes(_mp4(picture=b"vp09"))
        os.utime(path, ns=(stamp, stamp))

        self.assertEqual(probe(path)["video_codec"], "h264")

        os.utime(path, ns=(stamp + 1_000_000_000, stamp + 1_000_000_000))
        self.assertEqual(probe(path)["video_codec"], "vp9")


def _element(ident: int, body: bytes, *, unknown: bool = False) -> bytes:
    """An EBML element: its id as written, a four-byte size, and the body."""
    named = ident.to_bytes((ident.bit_length() + 7) // 8, "big")
    size = b"\x01\xff\xff\xff\xff\xff\xff\xff" if unknown else \
        struct.pack(">I", 0x10000000 | len(body))
    return named + size + body


def _uint_element(ident: int, value: int) -> bytes:
    return _element(ident, value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big"))


def _track(kind: int, codec: str, width: int = 0, height: int = 0) -> bytes:
    body = _uint_element(0x83, kind) + _element(0x86, codec.encode() + b"\0")
    if width:
        body += _element(0xE0, _uint_element(0xB0, width) + _uint_element(0xBA, height))
    return _element(0xAE, body)


def _webm(*, codec: str = "V_VP9", seconds: float | None = 1.5, scale: int | None = None,
          width: int = 128, height: int = 72, unknown_segment: bool = False,
          before_tracks: bytes = b"", sound_first: bool = False) -> bytes:
    info = b""
    if scale is not None:
        info += _uint_element(0x2AD7B1, scale)
    if seconds is not None:
        info += _element(0x4489, struct.pack(">d", seconds * 1e9 / (scale or 1_000_000)))
    tracks = (_track(2, "A_OPUS") if sound_first else b"") + _track(1, codec, width, height)
    cluster = _element(0x1F43B675, _uint_element(0xE7, 0) + bytes(512))
    ebml = _element(0x1A45DFA3, _element(0x4282, b"webm"))
    segment = _element(0x15 << 24 | 0x49A966, info) + before_tracks \
        + _element(0x1654AE6B, tracks) + cluster
    return ebml + _element(0x18538067, segment, unknown=unknown_segment)


def _ffmpeg() -> Path | None:
    found = tools.resolve(tools.FFMPEG)
    return found.path if found.state == tools.State.FOUND else None


class TheWebmHeaderSaysWhatItIs(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.root = Path(held.name)

    def _probe(self, data: bytes, name: str = "table.webm") -> dict:
        path = self.root / name
        path.write_bytes(data)
        return probe(path)

    def test_a_video_has_its_codec_size_and_running_time(self) -> None:
        facts = self._probe(_webm())

        self.assertEqual(("WEBM", "vp9", 128, 72), (facts["format"], facts["video_codec"],
                                                    facts["width"], facts["height"]))
        self.assertAlmostEqual(1.5, facts["duration_s"], places=3)

    def test_each_codec_is_named_as_a_browser_is_asked_about_it(self) -> None:
        named = {codec: self._probe(_webm(codec=codec), f"{n}.webm")["video_codec"]
                 for n, codec in enumerate(("V_VP9", "V_AV1", "V_MPEG4/ISO/AVC",
                                            "V_MPEGH/ISO/HEVC", "V_VP8", "V_THEORA"))}

        self.assertEqual(named, {"V_VP9": "vp9", "V_AV1": "av1", "V_MPEG4/ISO/AVC": "h264",
                                 "V_MPEGH/ISO/HEVC": "hevc", "V_VP8": None,
                                 "V_THEORA": None})

    def test_the_timecode_scale_sets_the_running_time(self) -> None:
        facts = self._probe(_webm(seconds=2.0, scale=500_000))

        self.assertAlmostEqual(2.0, facts["duration_s"], places=3)

    def test_a_duration_the_file_does_not_state_is_not_guessed(self) -> None:
        facts = self._probe(_webm(seconds=None))

        self.assertEqual(("vp9", None), (facts["video_codec"], facts["duration_s"]))

    def test_the_video_track_is_the_one_read_when_a_sound_comes_first(self) -> None:
        facts = self._probe(_webm(codec="V_AV1", sound_first=True))

        self.assertEqual(("av1", 128), (facts["video_codec"], facts["width"]))

    def test_tracks_are_found_after_elements_that_precede_them(self) -> None:
        cues = _element(0x1C53BB6B, bytes(300)) + _element(0x114D9B74, bytes(100)) \
            + _element(0xEC, bytes(2000))
        facts = self._probe(_webm(before_tracks=cues))

        self.assertEqual(("vp9", 128, 72), (facts["video_codec"], facts["width"],
                                            facts["height"]))

    def test_a_segment_of_unknown_size_is_read_and_ends(self) -> None:
        facts = self._probe(_webm(unknown_segment=True))

        self.assertEqual(("WEBM", "vp9", 128), (facts["format"], facts["video_codec"],
                                               facts["width"]))

    def test_a_cut_off_file_costs_the_numbers_not_the_answer(self) -> None:
        whole = _webm(before_tracks=_element(0xEC, bytes(300)))
        full = self._probe(whole, "whole.webm")
        for cut in (40, 200, 2000):
            with self.subTest(cut=cut):
                facts = self._probe(whole[:cut], f"cut{cut}.webm")

                self.assertEqual("WEBM", facts["format"])
                for field in ("width", "height", "duration_s", "video_codec"):
                    self.assertIn(facts[field], (None, full[field]))
        short = self._probe(whole[:40], "short.webm")
        self.assertEqual((None, None, None), (short["video_codec"], short["width"],
                                              short["duration_s"]))

    def test_the_magic_and_then_garbage_answers_no_numbers(self) -> None:
        for name, tail in (("zeros", bytes(500)), ("ones", b"\xff" * 500),
                           ("noise", bytes(range(256)) * 4)):
            with self.subTest(tail=name):
                facts = self._probe(b"\x1a\x45\xdf\xa3" + tail, f"{name}.webm")

                self.assertEqual((None, None, None, None),
                                 (facts["width"], facts["height"], facts["duration_s"],
                                  facts["video_codec"]))

    def test_a_file_that_is_only_the_magic_is_read_without_error(self) -> None:
        facts = self._probe(b"\x1a\x45\xdf\xa3")

        self.assertEqual(("WEBM", None), (facts["format"], facts["video_codec"]))


@unittest.skipUnless(_ffmpeg(), "FFmpeg is not installed here")
class AWebmFromFfmpegIsRead(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.root = Path(held.name)

    def _made(self, name: str, *args: str) -> Path:
        path = self.root / name
        run = subprocess.run(
            [str(_ffmpeg()), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
             "testsrc=duration=1:size=128x72:rate=10", "-pix_fmt", "yuv420p", *args,
             str(path)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if run.returncode:
            self.skipTest(f"this FFmpeg cannot make {name}: {run.stderr.strip()[-120:]}")
        return path

    def test_each_build_names_its_codec_with_its_size_and_running_time(self) -> None:
        builds = (("vp9.webm", "vp9", ("-c:v", "libvpx-vp9", "-f", "webm")),
                  ("av1.webm", "av1", ("-c:v", "libsvtav1", "-f", "webm")),
                  ("h264.webm", "h264", ("-c:v", "libx264", "-f", "matroska")),
                  ("vp8.webm", None, ("-c:v", "libvpx", "-f", "webm")))
        for name, codec, args in builds:
            with self.subTest(name=name):
                facts = probe(self._made(name, *args))

                self.assertEqual(("WEBM", codec, 128, 72),
                                 (facts["format"], facts["video_codec"], facts["width"],
                                  facts["height"]))
                self.assertAlmostEqual(1.0, facts["duration_s"], delta=0.5)

    def test_a_cut_off_file_is_read_without_error(self) -> None:
        whole = self._made("vp9.webm", "-c:v", "libvpx-vp9", "-f", "webm").read_bytes()
        for cut in (40, 200, 2000):
            with self.subTest(cut=cut):
                path = self.root / f"cut{cut}.webm"
                path.write_bytes(whole[:cut])

                facts = probe(path)

                self.assertEqual("WEBM", facts["format"])
                self.assertIn(facts["video_codec"], (None, "vp9"))
                self.assertIn(facts["width"], (None, 128))


if __name__ == "__main__":
    unittest.main()
