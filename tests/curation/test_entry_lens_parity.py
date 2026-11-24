"""The play lens answers the same thing over REST as it does to a theme.

`GET /collections/{name}/entries` was built as the play lens - "what a frontend would
show and the order it would show it in" - with the theme payload described as the same
resolution serialized differently. They resolved identically from the start; what they
did not do was *say* the same amount, so a frontend on another machine could not have
been built from the REST answer.

These assert the two serializations agree, and name every field where they deliberately
do not.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from common.games import collection_resolver
from common.games.collection_filters import group_key
from common.games.entry_lens import entry_resource
from frontend import game_state
from tests.support.entries import entries_for
from tests.support.library import TempTree, fake_game, write_game

# Local filesystem paths. True only of the machine that answered, and the wire is read
# by machines that did not - so the theme payload keeps them and REST does not.
THEME_ONLY = {"game": {"path"}, "table": {"path"}, "top": set()}

# REST concerns. `rating` is also flat here because it shipped that way before it moved
# into `user`; `links` is a REST affordance a theme has no use for. `default` used to be
# here - a theme that lists a game's tables has to know which one the game defaults to,
# and deriving it a second way is how the two lenses would come to disagree.
WIRE_ONLY = {"game": {"rating"}, "table": set(), "top": {"links"}}

ORDER_BY = "title"

META: dict[str, Any] = {
    "Info": {"Name": "Attack from Mars", "Title": "Attack from Mars",
             "Manufacturer": "Bally", "Year": "1995", "Type": "SS",
             "Themes": ["Space", "Aliens"], "VPSId": "vps-1"},
    "User": {"Rating": 4, "Favorite": 1, "StartCount": 7, "RunTime": 12},
    "vpinfe": {"game_id": "Aaaa111111"},
}


class EntryLensParityTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        folder = write_game(self.root, "Attack from Mars (Bally 1995)", info=META)
        game = fake_game(folder, "Attack from Mars (Bally 1995)", meta=META)
        for attribute in ("pup_pack_exists", "alt_color_exists", "alt_sound_exists"):
            setattr(game, attribute, False)
        self.entries = entries_for([game])
        # Both under the same order: the group each entry carries comes from it, and two
        # lenses built under different orders would not be the same resolution.
        self.theme = json.loads(game_state.games_json(
            self.entries, contract=2, order_by=ORDER_BY))["entries"][0]
        grouping = group_key(ORDER_BY)
        assert grouping is not None
        self.wire = entry_resource(self.entries[0], grouping(self.entries[0].game))

    def test_the_two_lenses_carry_the_same_fields(self) -> None:
        """Every difference is one of the four listed above. A new field on either side
        that is not deliberate shows up here rather than as a remote frontend that
        renders half a wheel."""
        for half in ("game", "table"):
            with self.subTest(half=half):
                theme, wire = set(self.theme[half]), set(self.wire[half])
                self.assertEqual(theme - wire, THEME_ONLY[half])
                self.assertEqual(wire - theme, WIRE_ONLY[half])

        self.assertEqual(set(self.theme) - set(self.wire), THEME_ONLY["top"])
        self.assertEqual(set(self.wire) - set(self.theme), WIRE_ONLY["top"])

    def test_the_response_model_declares_everything_the_builder_produces(self) -> None:
        """The builder is not what a caller receives.

        FastAPI serializes through the declared model and silently drops any key it does
        not know, so a field can be added, pass every test that calls the builder, and be
        absent from the actual answer. That happened; this is what would have said so.
        """
        from httpapi import models

        declared = set(models.Entry.model_fields)

        self.assertEqual(set(self.wire) - declared, set())

    def test_every_field_a_record_declares_reaches_a_consumer(self) -> None:
        """A projection that lists some fields and stops is how this goes wrong.

        Fifteen declared fields reached nothing at all - the catalog id nearly every
        game carries, the high score, the script hash - not because anyone decided, but
        because nobody ever diffed what a record holds against what a lens publishes. A
        connector is what noticed. This is what notices next time.

        The three the table deliberately omits are named, with the reason, so leaving
        one out stays a decision rather than a slip.
        """
        # Measured across 162 real tables as populated in none of them, and `type`
        # would put two meanings behind one word beside the game's - see
        # `table_descriptor`.
        decided_absent = {"manufacturer", "year", "type"}

        from common.games.tables import DETECT_KEYS, PARSED_KEYS

        published = set(self.theme["table"]) | set(self.theme["table"]["overrides"])
        declared = (set(PARSED_KEYS) | {"authors", "hidden"}
                    | {k.removeprefix("detect_") for k in DETECT_KEYS})
        detects = set(self.theme["table"]["detects"])

        missing = declared - published - detects - decided_absent

        self.assertEqual(missing, set(),
                         "a table's record declares these and no lens carries them")

    def test_the_shared_fields_carry_the_same_values(self) -> None:
        """Agreeing on names and disagreeing on answers would be worse than either."""
        for half in ("game", "table"):
            shared = set(self.theme[half]) & set(self.wire[half])
            for field in sorted(shared):
                with self.subTest(field=f"{half}.{field}"):
                    self.assertEqual(self.theme[half][field], self.wire[half][field])

        for field in sorted(set(self.theme) & set(self.wire) - {"game", "table"}):
            with self.subTest(field=field):
                self.assertEqual(self.theme[field], self.wire[field])

    def test_no_filesystem_path_reaches_the_wire(self) -> None:
        """The one thing that cannot travel: it is true of one machine only."""
        serialized = json.dumps(self.wire)

        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("path", self.wire["game"])
        self.assertNotIn("path", self.wire["table"])

    def test_the_play_record_survives_the_trip(self) -> None:
        """It is what goes stale first, so it is what a remote frontend most needs."""
        self.assertEqual(self.wire["game"]["user"]["rating"], 4)
        self.assertTrue(self.wire["game"]["user"]["favorite"])
        self.assertEqual(self.wire["game"]["user"]["play_count"], 7)
        self.assertEqual(self.wire["game"]["rating"], 4,
                         "the flat field still answers for clients that read it")

    def test_the_wire_can_reproduce_the_newest_order(self) -> None:
        """The "Newest" sort is a stat of the library's filesystem, so a client sorting its
        own copy has to be told rather than look. Newest-first off the wire answer has
        to land in the same order the library resolves.
        """
        folders = [("Medieval Madness (Williams 1997)", 300.0),
                   ("Attack from Mars (Bally 1995)", 100.0),
                   ("Twilight Zone (Bally 1993)", 200.0)]
        games = []
        for name, created in folders:
            meta = {**META, "Info": {**META["Info"], "Title": name.split(" (")[0]}}
            game = fake_game(write_game(self.root, name, info=meta), name, meta=meta)
            game.creation_time = created
            games.append(game)

        entries = entries_for(games)
        # Newest first is `added` descending now that direction is not baked into the
        # sort key, which is the same list this asserted when it was.
        hub_order = [game_state.game_title(e.game) for e in
                     collection_resolver._ordered(list(entries), "added", True)]
        wire = [entry_resource(e) for e in entries]
        stamps = [row["game"]["created_at"] for row in wire]
        client_order = [game_state.game_title(e.game) for _, e in sorted(
            zip(stamps, entries, strict=True), key=lambda pair: pair[0],
            reverse=True)]

        self.assertEqual(client_order, hub_order)
        self.assertEqual(client_order, ["Medieval Madness", "Twilight Zone",
                                        "Attack from Mars"])
        # Three distinct stamps, so the order above is the timestamps deciding it. Drop
        # `created_at` from the wire and they collapse to one value and this fails.
        self.assertEqual(len(set(stamps)), 3)

    def test_media_is_named_not_located(self) -> None:
        """Kinds, so the bytes can be fetched from wherever the assets are served."""
        self.assertIsInstance(self.wire["media"], list)
        self.assertEqual(self.wire["media"], self.theme["media"])


if __name__ == "__main__":
    unittest.main()
