"""On screen, install is only ever the verb: the machine VPinFE runs on is this device."""

from __future__ import annotations

import re
import unittest
from typing import Any

from tests.support.catalogs import served

CATALOG = served()

NOUN = re.compile(r"\binstall(?:s|['’]s|ations?)?\b", re.IGNORECASE)

VERB = re.compile(
    r"\bcould(?: not)? install\b"
    r"|\binstall (?:it|them)\b"
    r"|^install$"
    r"|(?:^|(?<=[.!?] ))install (?=\S+ \S)",
    re.IGNORECASE | re.DOTALL)

ALLOWED = {
    "tools.ffmpeg.hint.darwin": "a shell command, brew install ffmpeg",
    "tools.rar.hint.darwin": "a shell command, brew install unar",
    "manager.trust.trust_install": "the verb, closing a button that also installs the theme",
}


def says_the_noun(value: Any) -> bool:
    forms = value.values() if isinstance(value, dict) else [value]
    return any(NOUN.search(VERB.sub("", str(form))) for form in forms)


def unexplained(catalog: dict[str, Any], allowed: dict[str, str]) -> list[str]:
    return sorted(key for key, value in catalog.items()
                  if says_the_noun(value) and not allowed.get(key))


class EveryCatalog(unittest.TestCase):
    def test_install_on_screen_is_the_verb(self) -> None:
        self.assertEqual(
            unexplained(CATALOG, ALLOWED), [],
            "say this device for the machine VPinFE runs on, and a device for another; "
            "if the word is the verb, write it as one (Install unar from your package "
            "manager, Could not install), or list the key in ALLOWED with why")

    def test_every_listed_key_still_says_it(self) -> None:
        self.assertEqual(sorted(key for key in ALLOWED
                                if not says_the_noun(CATALOG.get(key, ""))), [])

    def test_the_sweep_reads_every_owner(self) -> None:
        owners = {key.split(".", 2)[0] for key in CATALOG}
        self.assertLessEqual({"app", "ext", "manager", "frontend", "config"}, owners)

    def test_it_can_fail(self) -> None:
        catalog = {
            "label": "Install folder",
            "noun": "This install has no launcher",
            "plural": {"one": "{count} install", "other": "{count} installs"},
            "possessive": "This install's account",
            "imperative": "Install unar from your package manager",
            "second_sentence": "Nothing is there. Install one first.",
            "refusal": "Could not install it: {exc}",
            "offer": "Themes you could install",
            "button": "Install",
            "participle": "Installed",
            "undo": "Uninstall",
        }

        self.assertEqual(unexplained(catalog, {}), ["label", "noun", "plural", "possessive"])
        self.assertEqual(unexplained(catalog, {"label": "x", "noun": ""}),
                         ["noun", "plural", "possessive"])


if __name__ == "__main__":
    unittest.main()
