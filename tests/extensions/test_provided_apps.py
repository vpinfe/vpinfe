"""An extension can add a way to play a table.

VPinFE plays Visual Pinball, and through the generic app anything a person can point at a
binary. What it could not do is treat another format as first-class - claim its suffixes,
so a library of them is worth importing rather than read and dropped.

The extension describes the app in plain data and hands over one callable. It cannot
import the app contract - its one door is `common.extensions.contract`, which is what
makes the import boundary checkable - so nothing here asks it to.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from common import apps, i18n
from common.apps.contract import App, Launch
from common.extensions import provided_apps
from common.extensions.context import ExtensionApps
from common.extensions.contract import ContractError

SCOPES = (provided_apps.APPS_PROVIDE,)
FUTURE = {"id": "fp", "name": "Future Pinball", "suffixes": ("fpt",)}


def _app(app_id: str) -> App:
    found = apps.get(app_id)
    assert found is not None
    return found


def _launch(app_id: str) -> Launch:
    launch = _app(app_id).launch
    assert launch is not None
    return launch


class ProvideTests(unittest.TestCase):
    def setUp(self) -> None:
        apps.withdraw_all()
        self.addCleanup(apps.withdraw_all)
        self.apps = ExtensionApps("future_pinball", SCOPES)

    def test_a_provided_app_joins_the_ones_this_build_ships(self) -> None:
        self.apps.provide(**FUTURE)

        self.assertIn("fp", [one.id for one in apps.all_apps()])

    def test_it_claims_its_own_files(self) -> None:
        """Which is the point: a file nothing claimed had nothing that could play it."""
        self.assertIsNone(apps.app_for("Big Bang Bar.fpt"))

        self.apps.provide(**FUTURE)

        claimed = apps.app_for("Big Bang Bar.fpt")
        assert claimed is not None
        self.assertEqual(claimed.id, "fp")

    def test_a_dot_is_added_to_a_suffix_that_came_without_one(self) -> None:
        self.apps.provide(**FUTURE)

        self.assertEqual(_app("fp").claim.suffixes, (".fpt",))

    def test_it_cannot_take_a_suffix_from_the_app_that_ships_with_this(self) -> None:
        """Built-ins are offered first and `app_for` takes the first that claims, so a
        contributed app cannot capture .vpx by loading before somebody looks."""
        self.apps.provide(id="impostor", name="Impostor", suffixes=(".vpx",))

        claimed = apps.app_for("Table.vpx")
        assert claimed is not None
        self.assertEqual(claimed.id, "vpx")

    def test_an_id_this_build_already_uses_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.apps.provide(id="vpx", name="Not really", suffixes=(".x",))

    def test_the_same_id_twice_is_refused(self) -> None:
        """Ids are stored in a table's record, so two apps under one id makes what is
        stored ambiguous long after the run that wrote it."""
        self.apps.provide(**FUTURE)

        with self.assertRaises(ValueError):
            self.apps.provide(**FUTURE)

    def test_an_app_that_plays_nothing_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.apps.provide(id="nothing", name="Nothing")

    def test_every_launcher_gets_the_program_it_runs(self) -> None:
        """Filled in when the extension forgot, because a launcher with no binary field
        is one nobody can point at anything."""
        self.apps.provide(**FUTURE)

        self.assertIn("bin_path", [one.key for one in _app("fp").fields])

    def test_an_app_declares_what_travels_with_its_tables(self) -> None:
        """Its own list. A Future Pinball table's companions are not a VPX table's, and
        a generic list would be one program's habits taught to everything."""
        from common.games.game_service import companions_beside

        self.apps.provide(**FUTURE, companions=("fpl", ".bam"))

        self.assertEqual(_app("fp").claim.companions, (".fpl", ".bam"))
        self.assertEqual(companions_beside(pathlib.Path("/nowhere/x.fpt")), [])

    def test_an_app_declares_which_vps_formats_it_plays(self) -> None:
        self.apps.provide(**FUTURE, vps_formats=("fp",))

        self.assertEqual(apps.vps_formats("fp"), ("FP",))
        self.assertEqual(apps.vps_formats("vpx"), ("VPX",))

    def test_an_app_that_declares_none_is_offered_every_release(self) -> None:
        self.apps.provide(**FUTURE)

        self.assertEqual(apps.vps_formats("fp"), ())
        self.assertEqual(apps.vps_formats("not-installed"), ())

    def test_formats_given_as_one_string_are_refused(self) -> None:
        """Iterated, "FP" would be the formats F and P and nothing would ever match."""
        with self.assertRaises(ValueError):
            self.apps.provide(**FUTURE, vps_formats="FP")

    def test_a_blank_format_is_refused(self) -> None:
        for bad in ("", "  ", None, 3):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.apps.provide(**FUTURE, vps_formats=(bad,))

    def test_providing_needs_the_scope(self) -> None:
        with self.assertRaises(ContractError):
            ExtensionApps("quiet", ()).provide(**FUTURE)

    def test_what_it_added_goes_when_it_does(self) -> None:
        """A disabled extension must not leave a suffix claimed by something that is no
        longer here."""
        self.apps.provide(**FUTURE)

        self.apps.withdraw()

        self.assertIsNone(apps.app_for("Big Bang Bar.fpt"))
        self.assertEqual(self.apps.provided(), ())


class WordsTests(unittest.TestCase):
    """A provided app's words are in its extension's `i18n/`, under `app.<id>.`."""

    def setUp(self) -> None:
        apps.withdraw_all()
        self.addCleanup(apps.withdraw_all)
        self.addCleanup(i18n.disown, "app.fp")
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        directory = pathlib.Path(folder.name)
        (directory / "i18n").mkdir()
        (directory / "i18n" / "en.json").write_text(json.dumps({
            "name": "Future Pinball connector",
            "app.fp.name": "Future Pinball",
            "app.fp.field.bin_path.description": "The Future Pinball program",
        }), encoding="utf-8")
        self.apps = ExtensionApps("future_pinball", SCOPES, directory)

    def test_its_name_comes_from_the_extensions_catalog(self) -> None:
        self.apps.provide(id="fp", suffixes=("fpt",))

        self.assertEqual(apps.app_name("fp"), "Future Pinball")

    def test_its_fields_are_worded_there_too(self) -> None:
        self.apps.provide(id="fp", suffixes=("fpt",))
        field = next(one for one in _app("fp").fields if one.key == "bin_path")

        self.assertEqual(apps.field_words("fp", field)["description"],
                         "The Future Pinball program")

    def test_its_words_go_when_it_does(self) -> None:
        self.apps.provide(id="fp", suffixes=("fpt",))

        self.apps.withdraw()

        self.assertEqual(i18n.first_key("app.fp.name"), "")


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        apps.withdraw_all()
        self.addCleanup(apps.withdraw_all)

    def _entry(self, **fields):
        from common.apps.contract import Entry

        return Entry(**fields)

    def test_the_extension_says_how_to_run_it(self) -> None:
        seen = {}

        def command(entry, settings):
            seen.update(entry)
            return ["/opt/fp", "--play", entry["table"]]

        ExtensionApps("fp", SCOPES).provide(**FUTURE, command=command)
        found = _launch("fp").command(
            self._entry(table="/games/BBB.fpt"), {})

        self.assertEqual(found, ["/opt/fp", "--play", "/games/BBB.fpt"])
        self.assertEqual(seen["table"], "/games/BBB.fpt")

    def test_answering_with_nothing_runs_the_launcher_as_configured(self) -> None:
        """So an extension that only wants to claim a format need not reimplement
        argument handling it has no opinion about."""
        ExtensionApps("fp", SCOPES).provide(**FUTURE, command=lambda entry, settings: None)

        found = _launch("fp").command(
            self._entry(table="/games/BBB.fpt"), {"bin_path": "/opt/fp", "args": "-open"})

        self.assertEqual(found, ["/opt/fp", "-open", "/games/BBB.fpt"])

    def test_a_string_is_refused_rather_than_split(self) -> None:
        """Splitting a command someone built as a string is how a path with a space in
        it becomes a crash or an injection, and only the extension knows where its own
        arguments end."""
        ExtensionApps("fp", SCOPES).provide(
            **FUTURE, command=lambda entry, settings: "/opt/fp --play thing")

        with self.assertRaises(TypeError):
            _launch("fp").command(self._entry(table="/x.fpt"), {})


if __name__ == "__main__":
    unittest.main()
