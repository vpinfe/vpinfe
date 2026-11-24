"""Protects: (c) the Needs You row for changed extensions counts only the ones the Extension
List names and the person has not accepted, so a tick or an unlisted extension does not raise
a notice that cannot be acted on.
"""

from __future__ import annotations

import unittest

from common.security import accepted
from manager.kit.data import extension_list_held


def _record(name: str, origin: str = accepted.CHANGED, *, accepted_by_person: bool = False,
            files: int = 2) -> dict:
    return {"name": name, "origin": origin,
            "trust": {"accepted": accepted_by_person, "changed_files": ["a"] * files}}


class ChangedRowTests(unittest.TestCase):
    def held(self, records: list[dict], *listed: str) -> dict:
        return extension_list_held({"entries": [{"name": name} for name in listed]}, records)

    def test_a_listed_extension_with_changed_files_is_counted(self) -> None:
        held = self.held([_record("alpha")], "alpha")

        self.assertEqual([(one["name"], one["count"]) for one in held["changed"]],
                         [("alpha", 2)])

    def test_one_the_person_accepted_is_not(self) -> None:
        held = self.held([_record("alpha", accepted_by_person=True)], "alpha")

        self.assertEqual(held["changed"], [])

    def test_one_the_list_does_not_name_is_not(self) -> None:
        held = self.held([_record("alpha")], "beta")

        self.assertEqual(held["changed"], [])

    def test_one_that_is_not_changed_is_not(self) -> None:
        held = self.held([_record("alpha", accepted.OFFICIAL)], "alpha")

        self.assertEqual(held["changed"], [])


if __name__ == "__main__":
    unittest.main()
