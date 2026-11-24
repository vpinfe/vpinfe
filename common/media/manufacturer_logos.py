"""Manufacturer logos: media keyed by a game's manufacturer, not by the game or a theme.

A logo belongs to hundreds of tables and outlives any one theme, so it lives in a folder
of its own - `media.manufacturer_logos_dir`, defaulting to manufacturer_logos/ under the
config dir - served at /manufacturers/<slug>/logo. The folder is flat: image files named
for the manufacturer. Nothing ships in the tree.

A file and a manufacturer meet at a slug of the name with case, punctuation and corporate
suffixes dropped, so "Williams Electronics.PNG", "williams.png" and a table whose
manufacturer is "Williams" all find each other. Slugs are compared with their hyphens
removed as well, so "Game Plan" finds GamePlan.png. The exceptions no rule can cover
("D. Gottlieb & Co.") belong in an alias map beside the images: manufacturers.json,
{"name or slug": "canonical-slug"}. One alias ships in the code, Taito do Brasil to Taito;
a logo of the name's own comes before it, and a name in manufacturers.json overrides it.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterable
from pathlib import Path

from common.i18n import t
from common.media.media_specs import IMAGE_FAMILY

_ALIAS_FILE = "manufacturers.json"
_REFERENCE_FILE = "manufacturers-reference.json"
DEFAULT_FOLDER = "manufacturer_logos"

# Corporate boilerplate that varies between VPSdb entries for one brand.
_SUFFIX_TOKENS = {
    "mfg", "manufacturing", "corp", "corporation", "co", "company",
    "inc", "incorporated", "ltd", "limited", "electronics", "industries",
    "llc", "gmbh", "ltda",
}
# "S.A." and "S.A.L." as a word of their own that ends the name or a clause. Anywhere else
# the letters belong to the name ("U.S.A.", "S.A.R.L.").
_DOTTED_SUFFIX = re.compile(r"(?<!\S)s\.\s*a(?:\.\s*l)?\b\.?(?=$|[\s,;])")

# A maker whose name is another maker's plus a place, which no rule can see. Used only
# when the name has no logo of its own.
_BUILTIN_ALIASES = {"taito-do-brasil": "taito"}

_folder: Path | None = None


def configure_manufacturer_logos(folder: str | Path | None) -> None:
    global _folder
    _folder = Path(folder) if folder else None


def resolve_logos_dir(configured: str, config_dir: str | Path) -> Path:
    return Path(configured) if configured.strip() else Path(config_dir) / DEFAULT_FOLDER


def manufacturer_slug(name: str) -> str:
    lowered = str(name or "").lower()
    words = [w for w in re.split(r"[^a-z0-9]+", _DOTTED_SUFFIX.sub(" ", lowered)) if w]
    kept = [w for w in words if w not in _SUFFIX_TOKENS]
    if kept or words:
        return "-".join(kept or words)
    return "-".join(w for w in re.split(r"[^a-z0-9]+", lowered) if w)


def _key(slug: str) -> str:
    """What two slugs are compared by: the slug without its hyphens."""
    return slug.replace("-", "")


def _alias_map(folder: Path | None) -> dict[str, str]:
    """The folder's own aliases, by key."""
    if folder is None:
        return {}
    try:
        data = json.loads((folder / _ALIAS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Empty values are placeholders, not aliases - honoring one would erase a working slug.
    return {_key(manufacturer_slug(k)): str(v) for k, v in data.items()
            if isinstance(v, str) and v.strip()}


def _logos(folder: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    """The folder's images by slug and by key, read on every call so a file dropped in
    shows without a restart."""
    rank = {ext: place for place, ext in enumerate(IMAGE_FAMILY)}
    try:
        files = sorted((one for one in folder.iterdir()
                        if one.suffix.lower() in rank and one.is_file()),
                       key=lambda one: (rank[one.suffix.lower()], one.name))
    except OSError:
        return {}, {}
    by_slug: dict[str, Path] = {}
    by_key: dict[str, Path] = {}
    for one in files:
        slug = manufacturer_slug(one.stem)
        by_slug.setdefault(slug, one)
        by_key.setdefault(_key(slug), one)
    return by_slug, by_key


def manufacturer_logo_file(slug: str) -> Path | None:
    """The image a slug's logo is, or None. A slug is what `manufacturer_slug` returns."""
    if _folder is None or not slug or slug != manufacturer_slug(slug):
        return None
    by_slug, by_key = _logos(_folder)
    return by_slug.get(slug) or by_key.get(_key(slug))


def _entry(name: str, aliases: dict[str, str]) -> dict:
    slug = manufacturer_slug(name)
    target = manufacturer_slug(aliases.get(_key(slug), "")) or None
    if not target and slug and manufacturer_logo_file(slug) is None:
        target = _BUILTIN_ALIASES.get(slug)
    effective = target or slug
    found = manufacturer_logo_file(effective) if effective else None
    return {"name": name, "slug": slug, "aliased_to": target,
            "logo": f"/manufacturers/{effective}/logo" if found else None}


def manufacturer_logo_url(name: str) -> str | None:
    """The URL a manufacturer's logo is served at, or None when there is no logo."""
    if _folder is None or not str(name or "").strip():
        return None
    return _entry(name, _alias_map(_folder))["logo"]


def manufacturer_report(names: Iterable[object]) -> list[dict]:
    """One row per distinct name: slug, effective alias, resolved logo.

    This is the lookup made visible - the answer to "what filename would this
    manufacturer find" without running the algorithm in your head, and the only
    way to see that an alias is redirecting past a file you named for the manufacturer.
    """
    aliases = _alias_map(_folder)
    distinct = sorted({str(n).strip() for n in names if str(n or "").strip()},
                      key=str.lower)
    return [_entry(name, aliases) for name in distinct]


def _wanted_keys(name: str, aliases: dict[str, str]) -> set[str]:
    """The keys of the image names that serve a maker, by the lookup `_entry` makes: the
    alias a person wrote, else the name's own, else the built-in alias for a name with no
    logo of its own."""
    slug = manufacturer_slug(name)
    if not slug:
        return set()
    aliased = manufacturer_slug(aliases.get(_key(slug), ""))
    if aliased:
        return {_key(aliased)}
    keys = {_key(slug)}
    if slug in _BUILTIN_ALIASES:
        keys.add(_key(_BUILTIN_ALIASES[slug]))
    return keys


def add_logos(makers: Iterable[object],
              candidates: Iterable[tuple[str, str | Path]]) -> dict[str, list[str]]:
    """Copy into the logos folder each image that serves one of `makers`, replacing none.

    A candidate is (the maker name the image stands for, the image). It is copied when
    its name finds a maker by the lookup a theme makes, and when the folder has no logo
    for that name yet. Answers {"placed": [...], "held": [...]} by file name: an image
    no maker asks for is in neither, and `held` are the ones the folder already served.
    """
    if _folder is None:
        raise FileNotFoundError(t("error.games.no_logo_folder"))
    aliases = _alias_map(_folder)
    wanted: set[str] = set()
    for name in {str(one or "").strip() for one in makers}:
        wanted |= _wanted_keys(name, aliases)
    placed: list[str] = []
    held: list[str] = []
    for name, path in candidates:
        source = Path(path)
        slug = manufacturer_slug(name)
        if not slug or source.suffix.lower() not in IMAGE_FAMILY or _key(slug) not in wanted:
            continue
        if manufacturer_logo_file(slug) is not None:
            held.append(source.name)
            continue
        landing = _folder / f"{slug}{source.suffix.lower()}"
        if landing.exists():
            held.append(source.name)
            continue
        _folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, landing)
        placed.append(source.name)
    return {"placed": placed, "held": held}


def vps_manufacturer_names(vpsdb_path: str | Path) -> list[str]:
    """Every distinct manufacturer string in a cached vpsdb.json."""
    try:
        data = json.loads(Path(vpsdb_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return sorted({str(t.get("manufacturer", "")).strip() for t in data
                   if isinstance(t, dict) and str(t.get("manufacturer", "")).strip()},
                  key=str.lower)


def write_manufacturer_reference(names: Iterable[object]) -> Path | None:
    """Generate manufacturers-reference.json in the logos folder.

    The reference is for people: open it to learn what slug a name computes,
    what an alias currently redirects, and which names have no logo yet. The
    lookup never reads it, so it can never break anything.
    """
    if _folder is None:
        return None
    report = manufacturer_report(names)
    if not report:
        return None
    path = _folder / _REFERENCE_FILE
    payload = {
        "about": ("Generated by VPinFE from VPSdb. Do not edit - regenerated on "
                  "sync, and never read by the logo lookup. To alias a name, "
                  "copy it into manufacturers.json in this folder."),
        "manufacturers": report,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        return None
    return path
