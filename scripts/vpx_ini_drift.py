#!/usr/bin/env python3
"""What a new Visual Pinball build changes for the settings VPinFE names.

Two questions, both answered from the source of builds you have checked out:

  * which settings does VPinFE's own code name that a build does not declare, and
  * what did a build add, take out or retype since the one before it.

    python scripts/vpx_ini_drift.py --from OLDER --from NEWER [--from NEWEST ...]

Each `--from` is a checkout holding `src/core/Settings_properties.inl` and `plugins/`, oldest
first, as `scripts/fetch_vpx_setting_types.py` takes them. Exits 1 when the last one lacks a
setting the code names and `KNOWN_UNDECLARED` does not account for.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import pathlib
import re
import sys
from collections.abc import Iterable, Mapping, Sequence

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apps.vpx.setting_types import SPANS, TYPES  # noqa: E402

# Where VPinFE writes a Visual Pinball setting's name as a string.
SCANNED = ("apps/vpx", "manager", "common")
_QUALIFIED = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9_\\]+)+$")

# Named in the code, with no type in the generated map. The nudge and plumb settings are
# declared by none of the 10.8.1 builds it was read from; an earlier build writes them, so an
# ini that carries one still needs its scope. `Input.Mapping` is the family of key bindings,
# which `apps/vpx/bindings.py` names one at a time.
KNOWN_UNDECLARED = frozenset({
    "Input.Mapping",
    "Player.EnableLegacyNudge", "Player.LegacyNudgeStrength",
    "Player.NudgeFilter0", "Player.NudgeFilter1",
    "Player.NudgeOrientation0", "Player.NudgeOrientation1",
    "Player.PlumbInertia", "Player.PlungerLinearSensor",
    "Player.VisualLatencyCorrection",
})


def _generator():
    spec = importlib.util.spec_from_file_location(
        "fetch_vpx_setting_types", ROOT / "scripts" / "fetch_vpx_setting_types.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def named_keys(root: pathlib.Path = ROOT, sections: Iterable[str] | None = None,
               ) -> dict[str, list[str]]:
    """Every string in the code that reads as `Section.Key` in a section Visual Pinball
    has, with the files that write it. A string is a setting's name when its section is
    one the generated map holds, so file names and catalog keys are not."""
    known = set(sections if sections is not None else
                (key.rsplit(".", 1)[0] for key in TYPES))
    found: dict[str, set[str]] = {}
    for where in SCANNED:
        for path in sorted((root / where).rglob("*.py")):
            if path.name in ("setting_types.py", "setting_declarations.py"):
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and _QUALIFIED.match(node.value)
                        and node.value.rsplit(".", 1)[0] in known):
                    found.setdefault(node.value, set()).add(path.relative_to(root).as_posix())
    return {key: sorted(files) for key, files in sorted(found.items())}


def missing_from(named: Iterable[str], declared: Iterable[str]) -> list[str]:
    """The names that are neither a declared setting nor the start of one. A name that
    only begins one - `Controller.DOF` - is how the code matches a family."""
    held = sorted(declared)
    return [key for key in named
            if key not in KNOWN_UNDECLARED and not any(one.startswith(key) for one in held)]


def accounted(types: Iterable[str], registered: Iterable[str]) -> list[str]:
    """What a build declares, plus the settings a build we hold dropped: the generated map
    already hides those from a program that no longer has them."""
    return [*types, *registered, *(key for key, span in SPANS.items() if span.until is not None)]


def changes(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, list[str]]:
    """What the later build did to the settings of the earlier one."""
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "retyped": sorted(f"{key} ({before[key]} -> {after[key]})"
                          for key in set(before) & set(after) if before[key] != after[key]),
    }


def report(paths: Sequence[pathlib.Path]) -> tuple[str, bool]:
    """The text to print for these checkouts, and whether the newest one declares every
    setting the code names."""
    generator = _generator()
    builds = [(path, generator.from_checkout(path)) for path in paths]
    named = named_keys()
    lines = [f"{len(named)} settings named in {', '.join(SCANNED)}"]
    for path, (types, _contextual, _labels, registered) in builds:
        absent = missing_from(named, [*types, *registered])
        lines.append(f"\n{path.name}: declares {len(types)}; "
                     f"{len(absent)} named settings it does not")
        lines += [f"  {key}  <- {', '.join(named[key][:2])}" for key in absent]
    for (older, (before, *_)), (newer, (after, *_)) in zip(builds, builds[1:], strict=False):
        lines.append(f"\n{older.name} -> {newer.name}")
        for what, keys in changes(before, after).items():
            lines.append(f"  {what} ({len(keys)})")
            lines += [f"    {key}" for key in keys]
    partial = [key for key in named if key in SPANS]
    lines.append(f"\nnamed in the code but not in every build ({len(partial)}):")
    lines += [f"  {key}  {tuple(SPANS[key])}  <- {', '.join(named[key][:2])}"
              for key in partial]
    last = builds[-1][1]
    clear = not missing_from(named, accounted(last[0], last[3]))
    return "\n".join(lines), clear


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--from", dest="checkouts", action="append", type=pathlib.Path,
                        required=True, help="a checkout, oldest first; repeat for each")
    text, clear = report(parser.parse_args().checkouts)
    print(text)
    return 0 if clear else 1


if __name__ == "__main__":
    sys.exit(main())
