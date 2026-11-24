#!/usr/bin/env python3
"""Regenerate `apps/vpx/setting_types.py` and `apps/vpx/setting_declarations.py` from
Visual Pinball's own declarations.

Visual Pinball writes a comment above every setting in its ini giving the label, the
description, the default and any enumerated answers - but only when it creates the file
itself. A file VPinFE wrote first, or that VPX has since saved, has none, so the same
declarations in the source are kept as a fallback: `setting_declarations.py` holds each
core setting's label, description, default, range and answers, and a comment in the
user's own file wins over it because it matches the build they have.

`setting_types.py` holds what the comment cannot say: the *type*. `Enable Log` and
`ImageMngPosX` both default to a bare 0 or 1, and only the source separates them. It
also holds a map of `Section.Key` to a type name, what each plugin registers its
settings with (a plugin's settings are written without the comment, so the ini has no
label, default, range or answers for them), and the Revs each setting exists in.

    ./scripts/fetch_vpx_setting_types.py [--from PATH [--from PATH]...]

Run it when Visual Pinball ships settings we do not know the type of. A key the map does
not carry falls back to what the ini implies, so a stale map degrades rather than breaks.

Given several builds it also records, for each setting not all of them declare, the
Rev it came in and the Rev it went at, so a build's own settings can be told from a
neighbour's. `--bisect` finds those Revs from the commits between two builds. The
declarations are read from the last build named.
"""

from __future__ import annotations

import argparse
import ast
import io
import math
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from typing import NamedTuple

SOURCE = ("https://raw.githubusercontent.com/vpinball/vpinball/master/"
          "src/core/Settings_properties.inl")
APPS = pathlib.Path(__file__).resolve().parent.parent / "apps" / "vpx"
OUT = APPS / "setting_types.py"
DECLARED_OUT = APPS / "setting_declarations.py"

# The macro's name says the type.
KINDS = {
    "Bool": "bool", "BoolDyn": "bool", "BoolBase": "bool",
    "Int": "int", "IntUnbounded": "int", "IntDyn": "int", "IntBase": "int",
    "Float": "number", "FloatUnbounded": "number", "FloatDyn": "number",
    "FloatStepped": "number", "FloatSteppedDyn": "number", "FloatBase": "number",
    "String": "string", "StringDyn": "string", "StringBase": "string",
    "Enum": "choice", "EnumDyn": "choice", "EnumWithMin": "choice", "Enum1": "choice",
    "EnumBase": "choice",
}
# It gathers other properties under one name.
NOT_A_SETTING = frozenset({"Array"})

DECL = re.compile(r"Prop(\w+)\(\s*(\w+)\s*,\s*(\w+)\s*,")
# Past the key of a `...Base` form: the label and the comment, each one or more string
# literals, then whether it is contextual.
_LITERALS = r'(?:"(?:[^"\\]|\\.)*"s?\s*)+'
BASE_CONTEXTUAL = re.compile(rf"\s*{_LITERALS},\s*{_LITERALS},\s*(true|false)\b")

# A plugin declares its own, and they are never in the core file: it registers them with
# the host when it loads. `MSGPI_INT_VAL_SETTING(var, "BackglassDMDX", "Backglass DMD X
# position", "DMD overlay X position", true, 0, 0xFFFF, 0)`: the variable, the key, its
# label, its description and whether it is editable, then what its type takes. A label
# that is not a literal still leaves the rest.
PLUGIN_CALL = re.compile(r"\bMSGPI_([A-Z]+)_(?:VAL_)?SETTING\s*\(")
PLUGIN_KINDS = {"BOOL": "bool", "INT": "int", "FLOAT": "number",
                "STRING": "string", "FOLDER": "string", "FILE": "string",
                "ENUM": "choice"}
# Past those five, by macro. `MSGPI_ENUM_...` numbers its answers from `minimum`. A folder
# is a string with a different picker; a file also names the files it offers.
PLUGIN_ARGS = {"FLOAT": ("minimum", "maximum", "step", "default"),
               "INT": ("minimum", "maximum", "default"),
               "ENUM": ("minimum", "count", "values", "default"),
               "BOOL": ("default",),
               "STRING": ("default", "size"),
               "FOLDER": ("default", "size"),
               "FILE": ("default", "filter", "size")}
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"s?')
_CHAR = re.compile(r"'(?:[^'\\]|\\.)'")
_NUMBER = re.compile(r"[-+]?(?:0[xX][0-9A-Fa-f]+|(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?[fF]?)")
_SIZE = re.compile(r"std::size\(\s*(\w+)\s*\)")
_DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(\w+)[ \t]+([^\n]+?)[ \t]*$", re.M)
_ENUM = re.compile(r"\benum\b[^{;]*\{")
_ARRAY = re.compile(r"\b(\w+)\s*\[\s*\w*\s*\]\s*=\s*\{")
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "'": "'"}
# `id = "B2SLegacy"` in the manifest is the section suffix: `[Plugin.B2SLegacy]`.
PLUGIN_ID = re.compile(r'^\s*id\s*=\s*"([^"]+)"', re.M)

# The source names a section with a C++ identifier; the file it writes uses a separator.
# `PluginPinMAME` is `[Plugin.PinMAME]` on disk and `DefaultPropsBall` is
# `[DefaultProps\Ball]`. Two rules rather than a list of forty, so a plugin added later
# lands without this script changing.
SEPARATORS = (("DefaultProps", "\\"), ("Plugin", "."))


def section_on_disk(declared: str) -> str:
    for prefix, joiner in SEPARATORS:
        if declared.startswith(prefix) and len(declared) > len(prefix):
            return f"{prefix}{joiner}{declared[len(prefix):]}"
    return declared


def parsed(text: str) -> tuple[dict[str, str], set[str]]:
    """Every setting's type, and which of them are contextual.

    Contextual is the `...Dyn` half of each macro pair, or a `...Base` form that says so,
    and it decides whether a table override survives being saved:
    `LayeredINIPropertyStore::Save` drops a table value that equals the application's
    *unless* the property is contextual, in which case it is kept. Without this, a
    setting that can be held at the inherited value and one that cannot are
    indistinguishable here.

    Raises ValueError on a macro `KINDS` does not name.
    """
    flat = re.sub(r"\s+", " ", text)
    found: dict[str, str] = {}
    contextual: set[str] = set()
    unknown: set[str] = set()
    for declared in DECL.finditer(flat):
        macro, section, key = declared.groups()
        if macro in NOT_A_SETTING:
            continue
        kind = KINDS.get(macro)
        if kind is None:
            unknown.add(f"Prop{macro}")
            continue
        qualified = f"{section_on_disk(section)}.{key}"
        found[qualified] = kind
        if _contextual(macro, qualified, flat, declared.end()):
            contextual.add(qualified)
    if unknown:
        raise ValueError(f"No type for {', '.join(sorted(unknown))}; add it to KINDS.")
    return found, contextual


def _contextual(macro: str, qualified: str, text: str, after_key: int) -> bool:
    if not macro.endswith("Base"):
        return macro.endswith("Dyn")
    said = BASE_CONTEXTUAL.match(text, after_key)
    if said is None:
        raise ValueError(f"Cannot read whether {qualified} is contextual.")
    return said.group(1) == "true"


# By key, what a plugin registers a setting with: `default` as the file would store it,
# and `minimum`, `maximum` and `choices` where it has them.
Registered = dict[str, dict[str, object]]
Build = tuple[dict[str, str], set[str], dict[str, str], Registered]


def combined(builds: Iterable[Build]) -> Build:
    """Several builds as one map, oldest first. A setting more than one declares takes
    the last one's type, whether it is contextual and its label. What a plugin registers
    it with is kept from the last build that registers it, because an earlier build may
    still register a setting a later one declares in the core."""
    types: dict[str, str] = {}
    contextual: set[str] = set()
    labels: dict[str, str] = {}
    registered: Registered = {}
    for found, held, said, given in builds:
        types.update(found)
        contextual = (contextual - found.keys()) | held
        labels = {key: label for key, label in labels.items() if key not in found} | said
        registered = registered | given
    return types, contextual, labels, registered


# Where a setting exists, in the builds of one version line. `since` is the first Rev that
# declares it (0: every build we read does) and `until` the first that no longer does.
Span = tuple[int, int | None]
Sample = tuple[int, frozenset[str]]
# Declared at one position in a run of commits, for finding where a setting came or went.
Probe = Callable[[int], frozenset[str]]


def spans(samples: Sequence[Sample],
          exact: Callable[[int, frozenset[str]], dict[str, int]] | None = None,
          ) -> dict[str, Span]:
    """Where in the line each setting that not every sample declares exists.

    `samples` are builds of one line, oldest first, as their Rev and the keys they
    declare. Between two samples a setting that differs came or went somewhere, so
    without `exact` it is taken to have come at the earliest Rev and gone at the latest -
    a build in the gap is shown the setting rather than denied it. `exact(n, keys)` gives
    the Rev where each of `keys` changed between samples `n` and `n + 1`.

    Raises ValueError for a setting that comes and goes more than one way: a span is one
    interval.
    """
    events: dict[str, list[tuple[str, int]]] = {}
    for n in range(len(samples) - 1):
        (low, before), (high, after) = samples[n], samples[n + 1]
        differing = before ^ after
        found = exact(n, differing) if exact and differing else {}
        for key in sorted(differing):
            if key in after:
                events.setdefault(key, []).append(("added", found.get(key, low + 1)))
            else:
                events.setdefault(key, []).append(("removed", found.get(key, high)))
    first = samples[0][1]
    found_spans: dict[str, Span] = {}
    for key, seen in events.items():
        order = [what for what, _ in seen]
        if order not in (["added"], ["removed"], ["added", "removed"]) or (
                order[0] == "removed") != (key in first):
            raise ValueError(f"{key} comes and goes more than once ({', '.join(order)}); "
                             "a span holds one interval.")
        since = next((rev for what, rev in seen if what == "added"), 0)
        until = next((rev for what, rev in seen if what == "removed"), None)
        found_spans[key] = (since, until)
    return found_spans


def located(differing: frozenset[str], low: int, high: int, at: Probe) -> dict[str, int]:
    """The first position in `(low, high]` where each of `differing` has the state it has
    at `high`, for settings that each changed once between the two positions. A key that
    changed twice and ended where it began is not in `differing` and is not found."""
    if not differing:
        return {}
    if high - low == 1:
        return dict.fromkeys(differing, high)
    middle = (low + high) // 2
    before, here = at(low), at(middle)
    unchanged = frozenset(key for key in differing if (key in here) == (key in before))
    return (located(differing - unchanged, low, middle, at)
            | located(unchanged, middle, high, at))


VERSION_HEADER = pathlib.PurePosixPath("src/core/vpversion.h")
WORKFLOW = pathlib.PurePosixPath(".github/workflows/vpinball.yml")
_VERSION_PART = re.compile(r"#define\s+VP_VERSION_(MAJOR|MINOR|REV)\s+(\d+)")
_START = re.compile(r"VERSION_START_SHA:\s*(\w+)")


def line_of(header: str) -> str:
    """The digits a build's `[Version] VPinball` starts with, before its Rev: major,
    minor and patch written side by side, as `VP_VERSION_STRING_DIGITS` does."""
    parts = dict(_VERSION_PART.findall(header))
    if {"MAJOR", "MINOR", "REV"} - parts.keys():
        raise ValueError("vpversion.h does not say the version.")
    return f"{parts['MAJOR']}{parts['MINOR']}{parts['REV']}"


def start_of(workflow: str) -> str:
    """The commit a build counts its Rev from."""
    said = _START.search(workflow)
    if said is None:
        raise ValueError("The build workflow does not name where the Rev starts counting.")
    return said.group(1)


def _git(repo: pathlib.Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=False)
    if done.returncode:
        raise ValueError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def rev_of(repo: pathlib.Path, commit: str, start: str) -> int:
    return int(_git(repo, "rev-list", "--count", f"{start}..{commit}"))


def declared_at(repo: pathlib.Path, commit: str) -> frozenset[str]:
    """The settings one commit declares, read without checking it out."""
    done = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", commit,
         "src/core/Settings_properties.inl", "plugins"],
        capture_output=True, check=False)
    if done.returncode:
        raise ValueError(f"git archive {commit}: {done.stderr.decode(errors='replace')}")
    with tempfile.TemporaryDirectory() as scratch:
        with tarfile.open(fileobj=io.BytesIO(done.stdout)) as packed:
            packed.extractall(scratch, filter="data")
        return frozenset(from_checkout(pathlib.Path(scratch))[0])


def exact_between(repo: pathlib.Path, builds: Sequence[tuple[str, frozenset[str]]],
                  start: str) -> Callable[[int, frozenset[str]], dict[str, int]]:
    """For `spans`: the Rev where each setting changed between two builds, found by
    reading the commits between them. `builds` are commit ids with what each declares."""
    def exact(n: int, differing: frozenset[str]) -> dict[str, int]:
        low, high = builds[n], builds[n + 1]
        commits = [low[0], *_git(repo, "rev-list", "--reverse", "--first-parent",
                                 f"{low[0]}..{high[0]}").split()]
        known = {0: low[1], len(commits) - 1: high[1]}

        def at(position: int) -> frozenset[str]:
            if position not in known:
                known[position] = declared_at(repo, commits[position])
            return known[position]

        found = located(differing, 0, len(commits) - 1, at)
        for key, position in found.items():
            if (key in at(position)) != (key in high[1]) or (key in at(position - 1)) == (
                    key in high[1]):
                raise ValueError(f"{key} does not change once between {low[0]} and "
                                 f"{high[0]}; read the commits between by hand.")
        return {key: rev_of(repo, commits[position], start) for key, position in found.items()}
    return exact


def _registered_row(key: str, one: dict[str, object]) -> str:
    named = [repr(one["default"])]
    named += [f"{name}={one[name]!r}" for name in ("minimum", "maximum")
              if one.get(name) is not None]
    answers = one.get("choices") or ()
    if not isinstance(answers, tuple) or not answers:
        return f"    {key!r}: Registered({', '.join(named)}),\n"
    listed = "".join(f"        {answer!r},\n" for answer in answers)
    return f"    {key!r}: Registered({', '.join(named)}, choices=(\n{listed}    )),\n"


def rendered(types: dict[str, str], contextual: set[str] | None = None,
             labels: dict[str, str] | None = None,
             registered: Registered | None = None,
             line: str = "", where: dict[str, Span] | None = None) -> str:
    # Escaped: a `DefaultProps\\Ball` section carries a backslash, and written raw it
    # is an invalid escape in the file this generates.
    rows = "".join(f'    {key!r}: "{kind}",\n' for key, kind in sorted(types.items()))
    marks = "".join(f"    {key!r},\n" for key in sorted(contextual or ()))
    names = "".join(f"    {key!r}: {label!r},\n"
                    for key, label in sorted((labels or {}).items()))
    given = "".join(_registered_row(key, one)
                    for key, one in sorted((registered or {}).items()))
    held = "".join(f"    {key!r}: Span({since}, {until}),\n"
                   for key, (since, until) in sorted((where or {}).items()))
    return (
        '"""What type each Visual Pinball setting is, and what a plugin registers its own\n'
        "with.\n"
        "\n"
        "Generated by `scripts/fetch_vpx_setting_types.py` from Visual Pinball's own\n"
        "property declarations. The label, the description, the default and any\n"
        "enumerated answers of the rest are written into the ini by Visual Pinball\n"
        "itself and are read from the user's own file at runtime; where the file has\n"
        "no comment, `setting_declarations.py` has them.\n"
        "\n"
        "It is here because the ini cannot say what a type is. `Enable Log` and\n"
        "`ImageMngPosX` both default to a bare 0 or 1, and without this every switch in\n"
        "the program renders as a number field.\n"
        "\n"
        "A key this does not carry falls back to what the ini implies, so a version of\n"
        "Visual Pinball newer than this file degrades rather than breaks.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from typing import NamedTuple\n"
        "\n"
        "\n"
        "class Registered(NamedTuple):\n"
        '    """What a plugin registers one of its settings with. `default` is as the file\n'
        '    stores it: a switch is 1 or 0, and an enumerated setting is its number."""\n'
        "\n"
        "    default: str\n"
        "    minimum: float | None = None\n"
        "    maximum: float | None = None\n"
        "    choices: tuple[tuple[str, str], ...] = ()\n"
        "\n"
        "\n"
        "class Span(NamedTuple):\n"
        '    """The builds of one line that declare a setting: from the Rev `since`, and\n'
        '    before the Rev `until` if it was taken out again."""\n'
        "\n"
        "    since: int = 0\n"
        "    until: int | None = None\n"
        "\n"
        "\n"
        f"# {len(types)} declarations.\n"
        "TYPES: dict[str, str] = {\n"
        f"{rows}"
        "}\n"
        "\n"
        "# The ones a table can hold at the application's own value.\n"
        "#\n"
        "# Saving a table's settings drops any value equal to the application's, so a\n"
        "# table cannot be pinned to what it already inherits - except for these, which\n"
        "# are kept. Declared by the `...Dyn` half of each macro pair.\n"
        f"# {len(contextual or ())} of them.\n"
        "CONTEXTUAL: frozenset[str] = frozenset({\n"
        f"{marks}"
        "})\n"
        "\n"
        "# A plugin setting's label, where it is not the key. The ini writes every plugin\n"
        "# setting but `Enable` bare, so it has none to give.\n"
        f"# {len(labels or {})} of them.\n"
        "LABELS: dict[str, str] = {\n"
        f"{names}"
        "}\n"
        "\n"
        "# What each plugin setting is registered with. The ini has none of it: VPX writes\n"
        "# a plugin's settings bare, and only once it has run that plugin.\n"
        f"# {len(registered or {})} of them.\n"
        "REGISTERED: dict[str, Registered] = {\n"
        f"{given}"
        "}\n"
        "\n"
        "# The digits a build's `[Version] VPinball` starts with, before its Rev: major,\n"
        "# minor and patch side by side. A Rev only compares with one from the same line.\n"
        f"LINE = {line!r}\n"
        "\n"
        "# Where in that line a setting exists, for the ones not every build we read\n"
        "# declares. A setting not here is declared by all of them.\n"
        f"# {len(where or {})} of them.\n"
        "SPANS: dict[str, Span] = {\n"
        f"{held}"
        "}\n"
    )


# The editor's, never a setting somebody plays with; the Manager leaves them out as well.
EDITOR_SECTIONS = ("DefaultProps\\", "CVEdit")
# Named in a default, and what the source gives each. A default that names anything else
# is left unknown rather than guessed.
CONSTANTS = {"EDITOR_BG_WIDTH": 1000, "DEFAULT_SECURITY_LEVEL": 0, "M_PI": math.pi,
             "g_isMobile": 0}
FUNCTIONS: dict[str, Callable[..., float]] = {
    "RGB": lambda r, g, b: int(r) | int(g) << 8 | int(b) << 16,
    "CMTOVPU": lambda x: x * (50 / (2.54 * 1.0625)),
}
# Settings Visual Pinball defaults from the loaded table before it reads them, so the
# declared number is not what an unset one plays with. VPinFE does not read the table's
# own values, so these carry no default.
FROM_THE_TABLE = frozenset({"TableOverride.Difficulty", "TableOverride.Exposure",
                            "TableOverride.ToneMapper", "Player.EmissionScale"})
# And a window left without a position is centered on its display, not put at 0.
CENTERED = frozenset(f"{section}.{window}Wnd{axis}" for section, window in (
    ("Player", "Playfield"), ("Backglass", "Backglass"), ("ScoreView", "ScoreView"),
    ("Topper", "Topper")) for axis in "XY")
_HEX = re.compile(r"0[xX][0-9A-Fa-f]+[uU]?")
_FLOAT_SUFFIX = re.compile(r"(?<![\w.])(\d+\.\d*|\.\d+|\d+)[fF]\b")
_CAST = re.compile(r"\((?:int|float)\)")
_MOBILE = re.compile(r"\bg_isMobile\s*\?[^:]*:")


class Declaration(NamedTuple):
    """One core setting as the source declares it. `default` is as the file stores it,
    and None where the source computes it from something this script cannot read."""

    label: str
    description: str
    default: str | None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[tuple[str, str], ...] = ()


def _evaluated(node: ast.expr) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return node.value
    if isinstance(node, ast.Name) and node.id in CONSTANTS:
        return CONSTANTS[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        value = _evaluated(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = _evaluated(node.left), _evaluated(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in FUNCTIONS and not node.keywords):
        return FUNCTIONS[node.func.id](*(_evaluated(arg) for arg in node.args))
    raise ValueError("not a number this script can work out")


def _number_of(item: str) -> float | None:
    """What a C++ default works out to, or None where it names something unknown. The
    desktop branch of `g_isMobile ? a : b`; an `(int)` cast wraps to 32 bits."""
    text = _CAST.sub("", _MOBILE.sub("", item.strip()))
    text = _HEX.sub(lambda m: str(int(m.group(0).rstrip("uU"), 16)), text)
    text = _FLOAT_SUFFIX.sub(r"\1", text)
    if text in ("true", "false"):
        return text == "true"
    try:
        value = _evaluated(ast.parse(text, mode="eval").body)
    except (ValueError, SyntaxError, ZeroDivisionError):
        return None
    if "(int)" in item and value > 2**31 - 1:
        value -= 2**32
    return value


def _stored(value: float, kind: str) -> str:
    """A default the way the ini writes it: a switch as 1 or 0, a float to the digits a
    float32 keeps."""
    if kind == "bool":
        return "1" if value else "0"
    if kind == "number":
        return format(value, ".7g")
    return str(int(value))


def declarations(text: str) -> dict[str, Declaration]:
    """Every core setting's label, description, default, range and answers, by the
    qualified name it has on disk.

    A setting the source declares twice, one per renderer, is read from the first. The
    editor's sections are left out.
    """
    found: dict[str, Declaration] = {}
    for call in re.finditer(r"\bProp(\w+)\(", text):
        macro = call.group(1)
        kind = KINDS.get(macro)
        line = text[text.rfind("\n", 0, call.start()) + 1:call.start()]
        if kind is None or macro.endswith("Base") or "#" in line or "//" in line:
            continue
        items = _items(text, call.end())
        if len(items) < 5:
            continue
        qualified = f"{section_on_disk(items[0])}.{items[1]}"
        if qualified.startswith(EDITOR_SECTIONS) or qualified in found:
            continue
        one = _declaration(macro, kind, items)
        if qualified in FROM_THE_TABLE | CENTERED:
            one = one._replace(default=None)
        found[qualified] = one
    return found


def _declaration(macro: str, kind: str, items: list[str]) -> Declaration:
    label, description = _literal(items[2]) or "", _literal(items[3]) or ""
    if kind == "choice":
        # `EnumWithMin` gives the number its first answer has, and the default after it.
        with_min = macro == "EnumWithMin"
        base = _number_of(items[5]) if with_min else 1 if macro == "Enum1" else 0
        said = _number_of(items[6] if with_min else items[5])
        words = [_literal(item) for item in items[7 if with_min else 6:]]
        answers = tuple((str(int((base or 0) + n)), word) for n, word in enumerate(words)
                        if word is not None)
        return Declaration(label, description, None if said is None else str(int(said)),
                           choices=answers if len(answers) == len(words) else ())
    if kind == "string":
        return Declaration(label, description, _literal(items[4]))
    low = high = None
    where = 4
    if macro in ("Int", "IntDyn", "Float", "FloatDyn"):
        low, high, where = _number_of(items[4]), _number_of(items[5]), 6
    elif macro.startswith("FloatStepped"):
        low, high, where = _number_of(items[4]), _number_of(items[5]), 7
    if len(items) <= where:
        return Declaration(label, description, None)
    said = _number_of(items[where])
    return Declaration(
        label, description, None if said is None else _stored(said, kind),
        None if low is None else float(low), None if high is None else float(high))


def _quoted(text: str, indent: int) -> list[str]:
    """A string as source, split between words so no line passes the file's limit."""
    chunks: list[str] = []
    current = ""
    for word in re.findall(r"\S+\s*|\s+", text) or [""]:
        if current and len(repr(current + word)) > 94 - indent:
            chunks.append(current)
            current = word
        else:
            current += word
    chunks.append(current)
    return [repr(chunk) for chunk in chunks]


def _argument(text: str) -> list[str]:
    parts = _quoted(text, 8)
    if len(parts) == 1:
        return [f"{parts[0]},"]
    return [f"({parts[0]}", *(f" {part}" for part in parts[1:-1]), f" {parts[-1]}),"]


def _declared_row(key: str, one: Declaration) -> str:
    pad = " " * 8
    lines = [f"    {key!r}: Declaration("]
    for text in (one.label, one.description):
        lines += [f"{pad}{line}" for line in _argument(text)]
    lines.append(f"{pad}{one.default!r},")
    for name in ("minimum", "maximum"):
        if getattr(one, name) is not None:
            lines.append(f"{pad}{name}={getattr(one, name)!r},")
    if one.choices:
        lines.append(f"{pad}choices=(")
        lines += [f"{pad}    {answer!r}," for answer in one.choices]
        lines.append(f"{pad}),")
    lines.append("    ),")
    return "\n".join(lines) + "\n"


def declarations_rendered(found: dict[str, Declaration]) -> str:
    rows = "".join(_declared_row(key, one) for key, one in sorted(found.items()))
    return (
        '"""Each core setting as Visual Pinball declares it: label, description, default,\n'
        "range and answers.\n"
        "\n"
        "Generated by `scripts/fetch_vpx_setting_types.py` from Visual Pinball's own\n"
        "property declarations. The ini carries the same in a comment above each setting,\n"
        "but only when Visual Pinball wrote the file itself; where the comment is missing\n"
        "this stands in for it, and where it is there it wins, because it matches the\n"
        "build the person has.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from typing import NamedTuple\n"
        "\n"
        "\n"
        "class Declaration(NamedTuple):\n"
        '    """One setting. `default` is as the file stores it, and None where the source\n'
        '    computes it from something not readable here."""\n'
        "\n"
        "    label: str\n"
        "    description: str\n"
        "    default: str | None\n"
        "    minimum: float | None = None\n"
        "    maximum: float | None = None\n"
        "    choices: tuple[tuple[str, str], ...] = ()\n"
        "\n"
        "\n"
        f"# {len(found)} settings.\n"
        "DECLARED: dict[str, Declaration] = {\n"
        f"{rows}"
        "}\n"
    )


def from_plugins(root: pathlib.Path) -> tuple[dict[str, str], dict[str, str], Registered]:
    """Every setting the shipped plugins register, by the section they land in; the
    label of each one whose label is not its key; and what each is registered with.

    A plugin names itself in its manifest and its settings in its sources, so the two
    are read together. Without this the switches somebody actually reaches for - turn
    the backglass DMD overlay off, turn a plugin on - are typeless and draw as text.

    Raises ValueError on a declaration whose default or answers it cannot work out.
    """
    found: dict[str, str] = {}
    labels: dict[str, str] = {}
    registered: Registered = {}
    for manifest in sorted(root.rglob("plugin.cfg")):
        named = PLUGIN_ID.search(manifest.read_text(encoding="utf-8", errors="replace"))
        if named is None:
            continue
        section = f"Plugin.{named.group(1)}"
        # Every plugin has one and no plugin declares it: the host creates it so that a
        # plugin can be switched off, and it is the switch somebody actually reaches for.
        found[f"{section}.Enable"] = "bool"
        sources = [source.read_text(encoding="utf-8", errors="replace")
                   for source in sorted(manifest.parent.rglob("*"))
                   if source.suffix in (".cpp", ".h")]
        names = _Names(sources)
        for text in sources:
            for macro, args in _declarations(text):
                kind = PLUGIN_KINDS.get(macro)
                if kind is None:
                    raise ValueError(f"No type for MSGPI_{macro}_VAL_SETTING in {section}; "
                                     "add it to PLUGIN_KINDS and PLUGIN_ARGS.")
                key = _literal(args[1]) if len(args) > 1 else None
                if key is None:
                    continue
                qualified = f"{section}.{key}"
                found[qualified] = kind
                label = _literal(args[2])
                if label and label != key:
                    # The Manager's heading for the window is "Score View".
                    labels[qualified] = label.replace("ScoreView", "Score View")
                registered[qualified] = names.registered(qualified, macro, args[5:])
    return found, labels, registered


def _declarations(text: str) -> Iterable[tuple[str, list[str]]]:
    """Each plugin setting declared in a source, as its macro and its arguments. The
    macros' own definitions are not declarations."""
    for call in PLUGIN_CALL.finditer(text):
        line = text[text.rfind("\n", 0, call.start()) + 1:call.start()]
        if "#" not in line:
            yield call.group(1), _items(text, call.end())


def _items(text: str, start: int) -> list[str]:
    """The comma-separated items from `start` to the bracket that closes the one before
    it, each stripped, with the commas inside strings, brackets and comments left alone.
    """
    found: list[str] = []
    depth, begin, at = 0, start, start
    while at < len(text):
        two = text[at:at + 2]
        if two == "//":
            at = text.find("\n", at)
            at = len(text) if at < 0 else at
            continue
        if two == "/*":
            at = text.find("*/", at)
            at = len(text) if at < 0 else at + 2
            continue
        char = text[at]
        if char in "\"'":
            quoted = (_STRING if char == '"' else _CHAR).match(text, at)
            at = quoted.end() if quoted else at + 1
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                found.append(_uncommented(text[begin:at]))
                return [one for one in found if one]
            depth -= 1
        elif char == "," and depth == 0:
            found.append(_uncommented(text[begin:at]))
            begin = at + 1
        at += 1
    raise ValueError(f"Unclosed bracket from: {text[start:start + 60]!r}")


def _items_or_none(text: str, start: int) -> list[str] | None:
    try:
        return _items(text, start)
    except ValueError:
        return None


def _uncommented(item: str) -> str:
    return re.sub(r"//[^\n]*|/\*.*?\*/", "", item, flags=re.S).strip()


def _literal(item: str) -> str | None:
    """The text of one string literal, or of several written side by side; None where
    the item is not one."""
    parts = list(_STRING.finditer(item))
    if not parts or _STRING.sub("", item).strip():
        return None
    return "".join(re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)),
                          part.group(1)) for part in parts)


class _Names:
    """What a plugin's sources name: its `#define`s, its enum members and its arrays,
    for reading a declaration that uses them."""

    def __init__(self, sources: Iterable[str]) -> None:
        self.values: dict[str, str] = {}
        self.arrays: dict[str, list[str]] = {}
        texts = list(sources)
        for text in texts:
            self.values.update(_DEFINE.findall(text))
        # Read after every `#define`, which an enum's members may be set from. What
        # cannot be read is left out rather than stopping the run: only a declaration
        # that uses it needs it, and that one stops the run itself.
        for text in texts:
            for body in _ENUM.finditer(text):
                following: int | None = 0
                for member in _items_or_none(text, body.end()) or ():
                    name, _, said = member.partition("=")
                    if said.strip():
                        following = self._number_or_none(said)
                    if following is not None:
                        self.values[name.strip()] = str(following)
                        following += 1
            for array in _ARRAY.finditer(text):
                if (items := _items_or_none(text, array.end())) is not None:
                    self.arrays[array.group(1)] = items

    def value(self, item: str, seen: frozenset[str] = frozenset()) -> str | float | bool:
        item = item.strip()
        while item.startswith("(") and item.endswith(")"):
            item = item[1:-1].strip()
        said = _literal(item)
        if said is not None:
            return said
        if item in ("true", "false"):
            return item == "true"
        if _NUMBER.fullmatch(item):
            if re.fullmatch(r"[-+]?0[xX][0-9A-Fa-f]+", item):
                return float(int(item, 16))
            return float(item.rstrip("fF"))
        sized = _SIZE.fullmatch(item)
        if sized and sized.group(1) in self.arrays:
            return float(len(self.arrays[sized.group(1)]))
        if item in self.values and item not in seen:
            return self.value(self.values[item], seen | {item})
        raise ValueError(f"Cannot read {item!r}")

    def number(self, item: str) -> int:
        said = self.value(item)
        if isinstance(said, str):
            raise ValueError(f"{item!r} is not a number")
        return int(said)

    def _number_or_none(self, item: str) -> int | None:
        try:
            return self.number(item)
        except ValueError:
            return None

    def registered(self, qualified: str, macro: str,
                   args: list[str]) -> dict[str, object]:
        wanted = PLUGIN_ARGS[macro]
        if len(args) < len(wanted):
            raise ValueError(f"{qualified} declares too little to read.")
        said = dict(zip(wanted, args, strict=False))
        try:
            return self._given(macro, said)
        except ValueError as exc:
            raise ValueError(f"{qualified}: {exc}") from exc

    def _given(self, macro: str, said: dict[str, str]) -> dict[str, object]:
        if macro == "BOOL":
            return {"default": "1" if self.value(said["default"]) else "0"}
        if PLUGIN_KINDS[macro] == "string":
            return {"default": str(self.value(said["default"]))}
        if macro == "FLOAT":
            return {"default": repr(float(self.value(said["default"]))),
                    "minimum": float(self.value(said["minimum"])),
                    "maximum": float(self.value(said["maximum"]))}
        low = self.number(said["minimum"])
        if macro == "INT":
            return {"default": str(self.number(said["default"])), "minimum": low,
                    "maximum": self.number(said["maximum"])}
        answers = self.arrays.get(said["values"].strip())
        if answers is None:
            raise ValueError(f"no array {said['values']!r}")
        count = self.number(said["count"])
        return {"default": str(self.number(said["default"])),
                "choices": tuple((str(low + n), str(self.value(answer)))
                                 for n, answer in enumerate(answers[:count]))}


def from_checkout(root: pathlib.Path) -> Build:
    types, contextual = parsed((root / "src" / "core" / "Settings_properties.inl")
                               .read_text(encoding="utf-8"))
    # The plugins come second, so a plugin that redeclares a core setting is the one
    # that answers for its own section.
    plugin_types, labels, registered = from_plugins(root / "plugins")
    # The host registers the switch of a plugin the core does not declare when the
    # player starts, off.
    registered.update({key: {"default": "0"} for key in plugin_types
                       if key.endswith(".Enable") and key not in types})
    types.update(plugin_types)
    return types, contextual, labels, registered


def measured(roots: Sequence[pathlib.Path], builds: Sequence[Build],
             bisect: bool) -> tuple[str, dict[str, Span]]:
    """The version line the checkouts are builds of, and where in it each setting not all
    of them declare exists. With `bisect`, found by reading the commits between builds
    rather than taken from the builds alone."""
    for root in roots:
        if _git(root, "status", "--porcelain", "--untracked-files=no"):
            raise ValueError(f"{root} has changes that are not committed; the Rev it is "
                             "given would not be that of the files read.")
    lines = {line_of(_git(root, "show", f"HEAD:{VERSION_HEADER}")) for root in roots}
    if len(lines) != 1:
        raise ValueError(f"The builds are of more than one line: {', '.join(sorted(lines))}.")
    start = start_of(_git(roots[-1], "show", f"HEAD:{WORKFLOW}"))
    commits = [_git(root, "rev-parse", "HEAD") for root in roots]
    revs = [rev_of(roots[0], commit, start) for commit in commits]
    if revs != sorted(revs):
        raise ValueError(f"The builds must be oldest first; their Revs are {revs}.")
    declared = [frozenset(build[0]) for build in builds]
    exact = (exact_between(roots[0], list(zip(commits, declared, strict=True)), start)
             if bisect else None)
    return lines.pop(), spans(list(zip(revs, declared, strict=True)), exact)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="sources", action="append", default=[],
                    metavar="PATH",
                    help="a checkout of vpinball, instead of fetching the sources; once "
                         "per build, oldest first")
    ap.add_argument("--bisect", action="store_true",
                    help="find the commit each setting came or went in between the "
                         "builds; without it a build between two of ours is shown a "
                         "setting rather than denied it")
    args = ap.parse_args()

    try:
        labels: dict[str, str] = {}
        registered: Registered = {}
        line = ""
        where: dict[str, Span] = {}
        if args.sources:
            roots = [pathlib.Path(source) for source in args.sources]
            builds = [from_checkout(root) for root in roots]
            types, contextual, labels, registered = combined(builds)
            line, where = measured(roots, builds, args.bisect)
            source = (roots[-1] / "src" / "core" / "Settings_properties.inl"
                      ).read_text(encoding="utf-8")
        else:
            with urllib.request.urlopen(SOURCE, timeout=30) as answer:
                source = answer.read().decode("utf-8")
            types, contextual = parsed(source)
            print("Only the core settings were read. Point --from at a checkout to "
                  "take the plugins' as well.", file=sys.stderr)
        declared = declarations(source)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not types:
        print("No declarations found; the source's shape has changed.", file=sys.stderr)
        return 1
    DECLARED_OUT.write_text(declarations_rendered(declared), encoding="utf-8")
    print(f"wrote {DECLARED_OUT.relative_to(APPS.parent.parent)}: {len(declared)} settings, "
          f"{sum(one.default is not None for one in declared.values())} with a default")
    OUT.write_text(rendered(types, contextual, labels, registered, line, where),
                   encoding="utf-8")
    counts: dict[str, int] = {}
    for kind in types.values():
        counts[kind] = counts.get(kind, 0) + 1
    print(f"wrote {OUT.relative_to(OUT.parent.parent.parent)}: {len(types)} settings")
    for kind, n in sorted(counts.items()):
        print(f"    {kind:8} {n}")
    print(f"    {'contextual':8} {len(contextual)}")
    print(f"    {'labels':8} {len(labels)}")
    print(f"    {'registered':8} {len(registered)}")
    print(f"    {'spans':8} {len(where)} (line {line or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
