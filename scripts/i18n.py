#!/usr/bin/env python3
"""Maintaining the translation catalogs. Run by a translator, not by the app.

A translation is a copy of `en.json` with the values rewritten. This reports what is
missing, what has gone stale because the English moved under it, and what nothing asks
for any more. Nothing here edits a translation - the file is the translator's.

Core's catalogs are one owner; each app's and each bundled extension's `i18n/` is another,
and its keys are reported under the prefix it is served at. `--record` and `--pseudo`
write every owner's.
"""

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOGS = ROOT / "common" / "i18n" / "catalogs"
SOURCE = "en"
HASHES = f"{SOURCE}.hashes.json"
PROSE_LEAVES = {"help", "description", "summary"}

# Where a key can be written. A key built at runtime - `f"filter.{name}.label"` - is
# found by its prefix, because the whole point of deriving it is that it is not typed out.
# A prefix carries at least one dot, so `f"c{...}"` is not read as a namespace: as a
# prefix, `c` matches every key beginning with it and the report goes quiet.
KEY_TEXT = re.compile(r"""["']([a-z][a-z0-9_]*(?:\.[a-z0-9_{}]+)+)["']""")
KEY_FSTRING = re.compile(r"""f["']([a-z][a-z0-9_]*(?:\.[a-z0-9_]*)+)\{""")
# `i18n.under("grid")` asks for a whole namespace at once and names no key in it.
KEY_UNDER = re.compile(r"""under\(\s*["']([a-z][a-z0-9_.]*)["']""")
# The frontend's own pages, in markup and in script.
PAGES = ROOT / "frontend" / "static"
KEY_MARKUP = re.compile(r'data-i18n="([^"]+)"')
# A theme asks from its own repository, so the doc offering it the keys is what names them.
THEME_DOC = ROOT / "docs" / "theme.md"
KEY_DOC = re.compile(r"`([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)`")


def owners():
    """`(prefix, directory)` for every catalog, core's first with no prefix."""
    yield "", CATALOGS
    for directory in sorted((ROOT / "apps").glob("*/i18n")):
        yield f"app.{directory.parent.name}.", directory
    for directory in sorted((ROOT / "extensions").glob("*/i18n")):
        yield f"ext.{directory.parent.name}.", directory


def load(name, directory=CATALOGS):
    path = directory / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def merged(name):
    """One catalog across every owner, keyed as the app serves it."""
    return {prefix + key: value for prefix, directory in owners()
            for key, value in load(name, directory).items()}


def locales():
    return sorted({p.stem for _, directory in owners() for p in directory.glob("*.json")
                   if not p.stem.endswith(".hashes")})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()[:12]


def tier(key):
    return "prose" if key.rsplit(".", 1)[-1] in PROSE_LEAVES else "chrome"


def _grid_words():
    """Every name the bundled grid contains, or None where the bundle cannot be found.

    The grid takes `grid.*` as one dictionary and ignores any entry it has no name for,
    so a key whose name is not in its script is a word nothing can show.
    """
    spec = importlib.util.find_spec("nicegui")
    if spec is None or not spec.origin:
        return None
    bundle = Path(spec.origin).parent / "elements" / "aggrid" / "dist" / "index.js"
    if not bundle.is_file():
        return None
    return set(re.findall(r"[A-Za-z_$][\w$]*", bundle.read_text(encoding="utf-8",
                                                                errors="ignore")))


def _settings_words():
    """`(setting words, groups, lists, choices)` of every setting the schema declares."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from common.config import config_schema
    stems, choices = {config_schema.window_screen("any").keys}, {}
    for option in config_schema.options():
        stems.add(option.keys)
        choices[option.keys] = {str(value) for value in option.choices}
    groups = {option.group for option in config_schema.options() if option.group}
    return stems, groups, {spec.key for spec in config_schema.lists()}, choices


def _live_setting_words(core):
    """The `config.*` and `grid.*` keys of `core` that the schema or the bundled grid names.

    A key not returned here is asked for by nothing, whatever prefix the code builds.
    """
    stems, groups, lists, choices = _settings_words()
    grid = _grid_words()
    live = set()
    for key in core:
        if key.startswith("grid."):
            if grid is None or key[len("grid."):] in grid:
                live.add(key)
        elif key.startswith("config."):
            head, _, rest = key[len("config."):].partition(".")
            if head == "group":
                found = rest.split(".")[0] in groups
            elif head == "list":
                found = rest.split(".")[0] in lists
            else:
                stem = max((s for s in stems if key == s or key.startswith(s + ".")),
                           key=len, default=None)
                found = stem is not None
                leaf = key[len(stem) + 1:] if stem else ""
                for marker in ("choice.", "choice_help."):
                    if found and leaf.startswith(marker):
                        found = leaf[len(marker):] in choices[stem]
            if found:
                live.add(key)
    return live


def referenced():
    """Every catalog key the tree asks for, literal or built from a prefix."""
    exact, prefixes = set(), set()
    for path in ROOT.rglob("*.py"):
        if any(p in path.parts for p in (".venv", "__pycache__", "tests", "scripts")):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        exact.update(KEY_TEXT.findall(text))
        prefixes.update(KEY_FSTRING.findall(text))
        prefixes.update(f"{name.rstrip('.')}." for name in KEY_UNDER.findall(text))
    for path in PAGES.rglob("*"):
        if path.suffix in (".html", ".js") and path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            exact.update(KEY_MARKUP.findall(text), KEY_TEXT.findall(text))
    exact.update(KEY_DOC.findall(THEME_DOC.read_text(encoding="utf-8")))
    exact.update(_live_setting_words(load(SOURCE)))
    kept = {p for p in prefixes if p and not p.startswith("config.")}
    if _grid_words() is not None:
        kept.discard("grid.")
    return exact, kept


# Every ASCII letter mapped to something that looks like it and is not it. A word that
# reaches the screen still spelled in ASCII did not come through the catalog, which is
# the whole of the check - see tests/theming for the run.
_ACCENTS = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "\u00e0\u0180\u00e7\u0111\u00e8\u0192\u011d\u0125\u00ee\u0135\u0137\u0140"
    "\u0271\u00f1\u00f6\u00fe\u01eb\u0159\u0161\u0163\u00fb\u1e7d\u0175\u1e8b"
    "\u00fd\u017e"
    "\u00c0\u0181\u00c7\u0110\u00c8\u0191\u011c\u0124\u00ce\u0134\u0136\u013f"
    "\u1e3e\u00d1\u00d6\u00de\u01ea\u0158\u0160\u0162\u00db\u1e7c\u0174\u1e8a"
    "\u00dd\u017d")


def _accent(text):
    """The same sentence, unreadable to a grep for English, with its slots intact."""
    out, i = [], 0
    while i < len(text):
        if text[i] == "{":
            end = text.find("}", i)
            if end == -1:
                out.append(text[i:])
                break
            out.append(text[i:end + 1])
            i = end + 1
            continue
        out.append(text[i].translate(_ACCENTS))
        i += 1
    return "".join(out)


def reworded(directory):
    """Keys the recorded file knows whose English is not what was recorded."""
    path = directory / HASHES
    recorded = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    held = load(SOURCE, directory)
    return [k for k in sorted(held) if k in recorded and recorded[k] != digest(held[k])]


def record(fits):
    """Rewrite each hashes file to match its English, once every reworded key is answered.

    A key whose English changed has to be either renamed or named in `--fits`, which says
    its key was looked at and still fits. A key that is new, or renamed, is not reworded
    as far as this can tell: the old one is gone and the new one has nothing recorded.
    """
    owed, named = [], set(fits)
    for prefix, directory in owners():
        held = load(SOURCE, directory)
        for key in reworded(directory):
            if prefix + key in named:
                named.discard(prefix + key)
            else:
                owed.append((prefix + key, held[key]))
    for key, text in owed:
        print(f"{key}\n    now: {text}")
    if owed:
        print(f"\n{len(owed)} keys have new English and were not renamed or named in --fits. "
              "Rename the key if its name no longer says what the string is, or pass "
              "--fits KEY... for those it still fits.", file=sys.stderr)
        return 1
    if named:
        print("Not reworded since the last record: " + ", ".join(sorted(named)),
              file=sys.stderr)
        return 1
    for _, directory in owners():
        held = load(SOURCE, directory)
        path = directory / HASHES
        path.write_text(json.dumps({k: digest(v) for k, v in sorted(held.items())},
                                   indent=2) + "\n", encoding="utf-8")
        print(f"recorded {len(held)} keys in {path.relative_to(ROOT)}")
    return 0


def pseudo():
    for _, directory in owners():
        out = {k: _accent(v) if isinstance(v, str)
               else {kk: _accent(vv) for kk, vv in v.items()}
               for k, v in load(SOURCE, directory).items()}
        path = directory / "qps.json"
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}: {len(out)} entries")
    return 0


def missing(lang):
    source, other = merged(SOURCE), merged(lang)
    gaps = [k for k in sorted(source) if not other.get(k)]
    for key in gaps:
        print(f"{tier(key):7} {key}")
    print(f"\n{len(gaps)} of {len(source)} keys have no {lang}")
    return 0


def stale(lang):
    source, other, recorded = merged(SOURCE), merged(lang), merged(f"{SOURCE}.hashes")
    if not recorded:
        print(f"No {HASHES}. Run --record once to start tracking.", file=sys.stderr)
        return 1
    moved = [k for k in sorted(other)
             if k in source and recorded.get(k) not in (None, digest(source[k]))]
    for key in moved:
        print(f"{key}\n    now: {source[key]}\n    has: {other[key]}")
    print(f"\n{len(moved)} {lang} entries were written against older English")
    return 0


def unused():
    exact, prefixes = referenced()
    core = load(SOURCE)
    orphans = [k for k in sorted(core)
               if k not in exact and not any(k.startswith(p) for p in prefixes)]
    for key in orphans:
        print(key)
    print(f"\n{len(orphans)} of {len(core)} catalog keys are asked for by nothing")
    return 0


def coverage():
    source = merged(SOURCE)
    totals = {t: sum(1 for k in source if tier(k) == t) for t in ("chrome", "prose")}
    print(f"{'locale':10} {'chrome':>14} {'prose':>14}")
    for name in locales():
        other = merged(name)
        cells = []
        for t, total in totals.items():
            have = sum(1 for k in source if tier(k) == t and other.get(k))
            cells.append(f"{have:4}/{total:<4} {100*have//max(total,1):3}%")
        print(f"{name:10} {cells[0]:>14} {cells[1]:>14}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    recording = ap.add_mutually_exclusive_group()
    ap.add_argument("--fits", metavar="KEY", nargs="+", default=[],
                    help="with --record: keys whose English changed and whose name still fits")
    ap.add_argument("--missing", metavar="LANG", help="keys with no translation yet")
    recording.add_argument("--stale", metavar="LANG",
                           help="translations written against English that has since changed")
    ap.add_argument("--unused", action="store_true",
                    help="core catalog keys nothing asks for")
    ap.add_argument("--coverage", action="store_true", help="percentage per locale, per tier")
    ap.add_argument("--pseudo", action="store_true",
                    help="write the qps pseudo-locale, for the runtime leak check")
    recording.add_argument("--record", action="store_true",
                           help=f"rewrite each {HASHES} to match its {SOURCE}.json, "
                                "once each reworded key is renamed or named in --fits")
    args = ap.parse_args()

    actions = [(args.record, lambda: record(args.fits)), (args.pseudo, pseudo),
               (args.missing, lambda: missing(args.missing)),
               (args.stale, lambda: stale(args.stale)),
               (args.unused, unused), (args.coverage, coverage)]
    given = [action for asked, action in actions if asked]
    if not given:
        ap.print_help()
        return 0
    return max([action() for action in given])


if __name__ == "__main__":
    raise SystemExit(main())
