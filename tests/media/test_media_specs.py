"""The shared media spec table, and the payload built from it."""

from __future__ import annotations

import mimetypes
import os
import unittest

from common.media.media_specs import (
    IMAGE_FAMILY,
    MEDIA_SPECS,
    apply_media_specs,
    canonical_kind,
    game_media_payload,
    media_family,
    media_filename_map,
    register_mime_types,
)
from tests.support.library import fake_game


class MediaSpecTests(unittest.TestCase):
    def test_media_specs_apply_and_payload_use_shared_specs(self) -> None:
        root = os.path.join(os.sep, "tmp", "Table")
        game = fake_game(root, playfield_image_path=None, bg_image_path=None)

        apply_media_specs(
            game,
            game_contents={"bg.png"},
            medias_contents={"fss.png"},
            playfield_variant="fss",
        )

        self.assertEqual(game.bg_image_path, os.path.join(root, "bg.png"))
        self.assertEqual(game.playfield_image_path, os.path.join(root, "medias", "fss.png"))
        self.assertEqual(media_filename_map("fss")["playfield"], "fss.png")
        self.assertEqual(game_media_payload(game)["PlayfieldImagePath"],
                         os.path.join(root, "medias", "fss.png"))


class AnApngIsAnImage(unittest.TestCase):
    def test_it_is_in_the_image_family_and_served_as_an_image(self) -> None:
        self.assertIn(".apng", IMAGE_FAMILY)
        mimetypes.init()
        register_mime_types()
        self.assertEqual(mimetypes.guess_type("wheel.apng")[0], "image/apng")
        self.assertEqual(mimetypes.guess_extension("image/apng"), ".apng")


class MediaFamilyTests(unittest.TestCase):
    """What a kind's files are, which is what decides the element that presents one.

    Named for the bug it exists to stop: the workbench slot painted every present file
    with an <img>, so a video downloaded in full and drew nothing.
    """

    def test_an_image_kind_is_an_image(self) -> None:
        self.assertEqual(media_family("playfield"), "image")

    def test_a_video_kind_is_a_video(self) -> None:
        self.assertEqual(media_family("playfield_video"), "video")

    def test_loading_is_video_despite_its_name(self) -> None:
        """The reason this is derived from the family and not the spelling: a caller
        testing for a `_video` suffix gets this one wrong."""
        self.assertEqual(media_family("loading"), "video")

    def test_audio_and_documents_are_themselves(self) -> None:
        self.assertEqual(media_family("audio"), "audio")
        self.assertEqual(media_family("rule_sheet"), "doc")

    def test_an_alias_resolves_before_the_family_is_read(self) -> None:
        self.assertEqual(media_family(canonical_kind("rulesheet")), "doc")

    def test_an_unknown_kind_says_so_rather_than_guessing(self) -> None:
        self.assertEqual(media_family("not_a_kind"), "")

    def test_every_declared_kind_has_a_family(self) -> None:
        """A kind with no family would fall through to the document branch and be
        offered as a link, whatever it actually is."""
        for spec in MEDIA_SPECS:
            with self.subTest(kind=spec.kind):
                self.assertNotEqual(media_family(spec.kind), "")


if __name__ == "__main__":
    unittest.main()
