"""Helpers the asset upload tests share.

The upload path is tested from four angles - what the analyzer recognizes, what a plan
proposes, what an import writes, and what a session holds - and each of them builds an
archive and reads the same three things back off a result. Those live here so splitting
the tests by angle did not copy them four times.
"""

from __future__ import annotations

import zipfile


def make_zip(path, names) -> None:
    """An archive with the given members, each holding filler bytes.

    The members are what every analyzer rule reads; the contents are not, so they are
    the same sixteen bytes throughout.
    """
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"x" * 16)


def kinds(result) -> list[str]:
    """The asset kinds an analysis found, sorted."""
    return sorted(asset.kind for asset in result.assets)


def plan_kinds_by_action(plan) -> dict[str, str]:
    """What the plan proposes to do with each kind."""
    return {item.asset.kind: item.action for item in plan.items}


def blocked_reasons(plan) -> dict[str, str]:
    """Why the plan refused each kind it refused."""
    return {item.asset.kind: item.reason for item in plan.blocked}
