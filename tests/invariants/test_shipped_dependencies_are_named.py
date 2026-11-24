"""Protects: (b) an extension cannot list a dependency VPinFE already carries, so a new
requirement is named in `common/extensions/dependencies.py`.
"""

from __future__ import annotations

import importlib.metadata
import pathlib
import unittest

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from common.extensions import dependencies

REQUIREMENTS = pathlib.Path(__file__).resolve().parents[2] / "requirements.txt"


def _closure() -> set[str]:
    """Every installed distribution VPinFE's requirements reach, on this system."""
    todo = [canonicalize_name(line.split("==")[0])
            for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]
    seen: set[str] = set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            needs = importlib.metadata.distribution(name).requires or ()
        except importlib.metadata.PackageNotFoundError:
            continue
        for line in needs:
            need = Requirement(line)
            if need.marker is None or need.marker.evaluate({"extra": ""}):
                todo.append(canonicalize_name(need.name))
    return seen


class ShippedDependenciesTests(unittest.TestCase):
    def test_every_requirement_and_what_it_needs_is_named(self) -> None:
        self.assertEqual([], sorted(_closure() - dependencies.SHIPPED),
                         "Add these to SHIPPED in common/extensions/dependencies.py")

    def test_the_names_are_written_the_way_pypi_compares_them(self) -> None:
        self.assertEqual([], sorted(one for one in dependencies.SHIPPED
                                    if one != canonicalize_name(one)))


if __name__ == "__main__":
    unittest.main()
