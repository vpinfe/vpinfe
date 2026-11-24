"""The sources that hand files over, behind one registry.

VPinMediaDB was the first and is not the shape of the feature, so what is asserted
here is the registry's behavior rather than any one source's: a source declares what
it can serve, a disabled one is not asked, and one that is down costs its own results
and nothing else.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from common.config.config_store import ConfigStore
from common.games import library_policy
from common.online import sources, vpinmediadb
from common.online.sources import Offer, Source
from tests.support.library import TempTree
from tests.support.sources import built_ins_afresh, only


def _fake(source_id: str = "fake", kinds: frozenset[str] = frozenset({"wheel"})) -> Source:
    """A source with a known answer, so the registry is what is under test."""
    url = "https://example.invalid/"

    def offers(who: dict, kind: str) -> list[Offer]:
        return [Offer(source=source_id, name=f"{source_id}.png",
                      url=f"{url}{who['vps_id']}.png", kind=kind)]

    return Source(id=source_id, name=source_id.title(), url=url, kinds=kinds, offers=offers)


def _broken() -> Source:
    def offers(who: dict, kind: str) -> list[Offer]:
        raise RuntimeError("that catalog is down")

    return Source(id="broken", name="Broken", kinds=frozenset({"wheel"}), offers=offers)


class RegistryTests(unittest.TestCase):
    def test_every_source_that_serves_the_kind_is_asked(self) -> None:
        with only(_fake("one"), _fake("two")):
            found = sources.offers("wheel", "vps-1")
        self.assertEqual([offer.source for offer in found], ["one", "two"])

    def test_a_source_that_does_not_serve_the_kind_is_skipped(self) -> None:
        """Asked about a kind it has never had, a source should cost nothing - not a
        request, and not a row saying it had nothing."""
        with only(_fake()):
            self.assertEqual(sources.offers("topper", "vps-1"), [])

    def test_a_source_that_is_down_costs_only_its_own_results(self) -> None:
        """One catalog being unreachable is not the others failing."""
        with only(_broken(), _fake("works")):
            found = sources.offers("wheel", "vps-1")
        self.assertEqual([offer.source for offer in found], ["works"])

    def test_only_the_configured_sources_are_asked(self) -> None:
        with only(_fake("one"), _fake("two")):
            found = sources.offers("wheel", "vps-1", ("two",))
        self.assertEqual([offer.source for offer in found], ["two"])

    def test_a_source_that_takes_its_own_files_is_not_a_catalog(self) -> None:
        """Core fetches only what a source offers as addresses. A source that places its
        own files answers with rows, which core does not read as `Offer`s."""
        own = Source(id="own.site", name="Own", kinds=frozenset({"wheel"}),
                     extension="own", key="site",
                     offers=lambda who, kind: [], take=lambda who, kind, row: None)
        with only(_fake("one"), own):
            self.assertEqual([one.id for one in sources.catalogs()], ["one"])

    def test_the_built_in_sources_register_the_way_a_contributed_one_does(self) -> None:
        with built_ins_afresh():
            found = [one.id for one in sources.all_sources()]
        self.assertEqual(found, ["vps", "vpinmediadb"])

    def test_a_kind_core_has_no_slot_for_is_refused(self) -> None:
        with only():
            with self.assertRaises(ValueError):
                sources.register(_fake("odd", frozenset({"hologram"})))

    def test_a_source_that_lists_kinds_it_cannot_offer_is_refused(self) -> None:
        with only():
            with self.assertRaises(ValueError):
                sources.register(Source(id="mute", name="Mute", kinds=frozenset({"wheel"})))

    def test_a_contributed_id_carries_its_extension(self) -> None:
        with only():
            sources.contribute("shop", "site", "Shop site", frozenset({"wheel"}),
                               offers=lambda who, kind: [], take=lambda who, kind, row: None)
            self.assertIsNotNone(sources.named("shop", "site"))
            sources.forget("shop")
            self.assertIsNone(sources.named("shop", "site"))


class SwitchedOffTests(TempTree):
    """Settings > Online Sources, which the library policy holds as what is off."""

    def setUp(self) -> None:
        super().setUp()
        self.policy = library_policy.reset_for_tests(ConfigStore(str(self.root / "vpinfe.ini")))
        self.addCleanup(library_policy.reset_for_tests)
        registry = only(_fake("one"), _fake("two"))
        registry.__enter__()
        self.addCleanup(registry.__exit__, None, None, None)

    def test_nothing_switched_off_asks_every_source(self) -> None:
        """A fresh install should find artwork without discovering a list first."""
        self.assertEqual(sources.enabled_ids(), ("one", "two"))

    def test_one_switched_off_is_not_asked(self) -> None:
        self.policy.set("hidden_sources", ["one"])

        found = sources.offers("wheel", "vps-1", sources.enabled_ids())
        self.assertEqual([offer.source for offer in found], ["two"])

    def test_every_source_switched_off_asks_none(self) -> None:
        self.policy.set("hidden_sources", ["one", "two"])

        found = sources.offers("wheel", "vps-1", sources.enabled_ids())
        self.assertEqual(found, [])

    def test_a_name_that_is_not_a_source_switches_nothing_off(self) -> None:
        self.policy.set("hidden_sources", ["nonesuch"])

        self.assertEqual(sources.enabled_ids(), ("one", "two"))

    def test_the_spreadsheet_has_no_switch(self) -> None:
        """Every shelf starts from it, so switching it off would leave nothing to show."""
        self.policy.set("hidden_sources", ["vps"])
        spreadsheet = Source(id="vps", name="Spreadsheet", always_on=True)

        self.assertTrue(sources.enabled(spreadsheet))


class UrlTests(unittest.TestCase):
    """A URL is produced by a named source, never accepted from a caller."""

    def test_a_named_source_produces_its_own_link(self) -> None:
        with only(_fake("one"), _fake("two")):
            offer = sources.url_for("two", "wheel", "vps-9")
        assert offer is not None
        self.assertEqual(offer.url, "https://example.invalid/vps-9.png")

    def test_a_source_nobody_ships_produces_nothing(self) -> None:
        with only(_fake("one")):
            self.assertIsNone(sources.url_for("elsewhere", "wheel", "vps-9"))

    def test_a_disabled_source_produces_nothing(self) -> None:
        """Turning a catalog off has to stop the fetch too, not just the listing."""
        with only(_fake("one")):
            self.assertIsNone(sources.url_for("one", "wheel", "vps-9", enabled_ids=("two",)))


class LoadingTheShippedSources(unittest.TestCase):
    def test_an_import_that_fails_is_tried_again_by_the_next_call(self) -> None:
        import builtins

        real = builtins.__import__

        def refuse(name: str, globals_: Any = None, locals_: Any = None,
                   fromlist: Any = (), level: int = 0) -> Any:
            if name == "common.online" and "vpinmediadb" in (fromlist or ()):
                raise ImportError("not this time")
            return real(name, globals_, locals_, fromlist, level)

        with patch.object(sources, "_sources", {}), \
                patch.object(sources, "_built_in_loaded", False):
            with patch.object(builtins, "__import__", refuse), self.assertRaises(ImportError):
                sources.all_sources()
            self.assertFalse(sources._built_in_loaded)

            found = [one.id for one in sources.all_sources()]

        self.assertIn("vpinmediadb", found)
        self.assertIn("vps", found)


class VPinMediaDBTests(unittest.TestCase):
    """The one shipped source that has variants, since that is what the size means."""

    ENTRY = {"vps-1": {
        "1k": {"table": "https://example.invalid/1k/table.png"},
        "4k": {"table": "https://example.invalid/4k/table.png"},
        "wheel": "https://example.invalid/wheel.png",
    }}

    def test_every_size_is_offered_largest_first(self) -> None:
        with patch.object(vpinmediadb, "_manifest", return_value=self.ENTRY):
            found = vpinmediadb.SOURCE.offers({"vps_id": "vps-1"}, "playfield")  # type: ignore[misc]
        self.assertEqual([offer.size for offer in found], ["4k", "1k"])

    def test_a_size_can_be_asked_for_by_name(self) -> None:
        with patch.object(vpinmediadb, "_manifest", return_value=self.ENTRY), \
                only(vpinmediadb.SOURCE):
            offer = sources.url_for("vpinmediadb", "playfield", "vps-1", "1k")
        assert offer is not None
        self.assertEqual(offer.url, "https://example.invalid/1k/table.png")


if __name__ == "__main__":
    unittest.main()
