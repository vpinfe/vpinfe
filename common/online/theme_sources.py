"""Where themes come from: the catalogs and the single-theme repos a user lists.

A registry is a `themes.json` naming many themes; a repository is one theme standing in
for a registry entry of its own, which since PAR-42 holds only a name and a url. Only
the VPinFE Theme List installs a theme on its own.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from common import i18n
from common.config import config_schema
from common.config.config_access import cfg_get, cfg_list

logger = logging.getLogger("vpinfe.common.online.theme_sources")

SECTION = "themes"

# What a person calls a source, by the address it is stored under.
NAMES_SECTION = "theme_source_names"
NAME_LIMIT = 80

# Set on an entry whose name is not known until its manifest is read.
NAMED_BY_MANIFEST = "named_by_manifest"


@dataclass(frozen=True)
class ThemeSources:
    """Every place this install looks for themes, in the order it trusts them."""

    registries: tuple[str, ...] = ()
    repositories: tuple[str, ...] = ()


def from_config(source: Any) -> ThemeSources:
    return ThemeSources(
        registries=tuple(cfg_list(source, SECTION, "registries")),
        repositories=tuple(cfg_list(source, SECTION, "repositories")),
    )


def _name_key(address: str) -> str:
    """The key a source's name is kept under: its parsed address, lower case as option
    keys are."""
    where = parse(address)
    return (where.url if where is not None else str(address or "").strip()).lower()


def claims_stock_title(name: str) -> bool:
    """Whether `name` is the VPinFE Theme List's title, which only that list may carry:
    a source called that would borrow the trust the title implies."""
    wanted = {i18n.t_source("manager.theme_sources.stock"), i18n.t("manager.theme_sources.stock")}
    return clean_name(name).casefold() in {one.casefold() for one in wanted}


def name_of_source(source: Any, address: str) -> str:
    """What the person called this source, or "" where they have not. A name that is the
    VPinFE Theme List's title is ignored on any other address, wherever it was written."""
    named = cfg_get(source, NAMES_SECTION, _name_key(address)).strip()
    return "" if named and claims_stock_title(named) and not is_stock(address) else named


def clean_name(text: str) -> str:
    """A name as it is kept: one line, spaces collapsed, at most NAME_LIMIT characters."""
    return " ".join(str(text or "").split())[:NAME_LIMIT].strip()


def keep_names(store: Any, names: dict[str, str]) -> None:
    """Stage `names` (address to name, "" meaning none) on `store`. A name outlives its
    source's removal, so Undo and adding the address again find it."""
    if not store.config.has_section(NAMES_SECTION):
        store.config.add_section(NAMES_SECTION)
    for address, name in names.items():
        key = _name_key(address)
        if clean_name(name) and not (claims_stock_title(name) and not is_stock(address)):
            store.config.set(NAMES_SECTION, key, clean_name(name))
        else:
            store.config.remove_option(NAMES_SECTION, key)


def split_ref(url: str) -> tuple[str, str]:
    """A repo url, and the ref pinned onto it with `#` if there is one.

    `#` cannot occur in a repo path, and `repo#ref` is npm's spelling for the same idea.
    """
    base, _, ref = str(url or "").strip().partition("#")
    return base.strip().rstrip("/"), ref.strip()


GITHUB = "github.com"
GITHUB_RAW = "raw.githubusercontent.com"

# The file a list of themes is.
LIST_FILE = "themes.json"

# Where a Forgejo or Gitea host shows a ref in a browsable path, and where it serves the file.
_FORGEJO_SHOWN = {"branch", "tag"}


@dataclass(frozen=True)
class Address:
    """A pasted address, read as the address VPinFE fetches.

    `url` is what is written to the config: a repository with its pin after a `#`, or the
    raw file of a list. `repo` is the repository page, empty for a file on a host that
    shows none.
    """

    url: str
    is_file: bool
    repo: str
    ref: str
    display: str

    @property
    def link(self) -> str:
        return self.repo or self.url


def _repo_name(segment: str) -> str:
    return segment[:-4] if segment.endswith(".git") else segment


def _described(parts: list[str], host: str) -> str:
    return "/".join(parts[:2]) if len(parts) >= 2 else host


def parse(text: str) -> Address | None:
    """Read what somebody pasted, or None where it is no address to a repository or file.

    A hand-written address the table does not recognize is kept as it is, so the check
    can say what is at it.
    """
    base, ref = split_ref(text)
    try:
        parts_of = urlsplit(base)
    except ValueError:
        return None
    if parts_of.scheme not in ("http", "https") or not parts_of.netloc:
        return None
    host = parts_of.netloc.lower()
    if host == f"www.{GITHUB}":
        host = GITHUB
    scheme = parts_of.scheme
    parts = [one for one in parts_of.path.split("/") if one]

    if host == GITHUB_RAW:
        if len(parts) < 3:
            return None
        return Address(base, True, f"https://{GITHUB}/{parts[0]}/{_repo_name(parts[1])}", "",
                       _described(parts, host))

    if host == GITHUB:
        if len(parts) < 2:
            return None
        owner, repo = parts[0], _repo_name(parts[1])
        root = f"https://{GITHUB}/{owner}/{repo}"
        if len(parts) >= 5 and parts[2] in ("blob", "raw"):
            return Address(f"https://{GITHUB_RAW}/{owner}/{repo}/{parts[3]}/"
                           + "/".join(parts[4:]), True, root, "", f"{owner}/{repo}")
        if len(parts) >= 4 and parts[2] == "tree":
            ref = ref or "/".join(parts[3:])
        return Address(f"{root}#{ref}" if ref else root, False, root, ref,
                       f"{owner}/{repo}")

    if len(parts) >= 6 and parts[2] == "raw" and parts[3] in _FORGEJO_SHOWN:
        return Address(base, True, f"{scheme}://{host}/{parts[0]}/{_repo_name(parts[1])}",
                       "", _described(parts, host))
    root = f"{scheme}://{host}/" + "/".join(parts[:2]) if len(parts) >= 2 else ""
    if root:
        root = root[:-4] if root.endswith(".git") else root
    if len(parts) >= 6 and parts[2] == "src" and parts[3] in _FORGEJO_SHOWN:
        return Address(f"{root}/raw/{parts[3]}/{parts[4]}/" + "/".join(parts[5:]), True,
                       root, "", _described(parts, host))
    if len(parts) == 5 and parts[2] == "src" and parts[3] in _FORGEJO_SHOWN:
        ref = ref or parts[4]
    elif parts and parts[-1].endswith(".json"):
        return Address(base, True, "", "", _described(parts, host))
    elif len(parts) > 2:
        # Deeper than owner and repository: a host with groups, kept as it was pasted.
        root = f"{scheme}://{host}/" + "/".join(parts)
        root = root[:-4] if root.endswith(".git") else root
    if not root:
        return None
    return Address(f"{root}#{ref}" if ref else root, False, root, ref,
                   _described(parts, host))


def list_file(where: Address) -> str:
    """The `themes.json` a repository would hold, as the address a list is stored under."""
    ref = where.ref or "HEAD"
    if where.repo.startswith(f"https://{GITHUB}/"):
        return f"https://{GITHUB_RAW}/{where.repo.removeprefix(f'https://{GITHUB}/')}/{ref}/{LIST_FILE}"
    return f"{where.repo}/raw/{ref}/{LIST_FILE}"


def stock_list() -> str:
    """The address of the VPinFE Theme List, which `themes.registries` defaults to."""
    declared = config_schema.option(SECTION, "registries")
    default = str(declared.default if declared else "").strip()
    where = parse(default)
    return where.url if where is not None else default


def is_stock(address: str) -> bool:
    """Whether `address`, once rewritten to the address VPinFE fetches, is the VPinFE Theme List."""
    where = parse(address)
    return where is not None and where.url == stock_list()


def installs_on_its_own(entry: dict, source: str) -> bool:
    """A theme installs when VPinFE starts, and cannot be deleted, only when the VPinFE
    Theme List marks it `default_install`. Another list's mark is ignored."""
    return bool(entry.get("default_install")) and is_stock(source)


def repository_entry(url: str) -> dict:
    base, ref = split_ref(url)
    entry = {"url": base, NAMED_BY_MANIFEST: True}
    if ref:
        entry["ref"] = ref
    return entry


def key_of(index_key: str, entry: dict, manifest: dict) -> str:
    """What a theme installs as: the key its list gave it, or - for a url, which names
    nothing - the name in the author's `manifest.json`. An entry's `name` never decides it."""
    if (entry or {}).get(NAMED_BY_MANIFEST):
        return str((manifest or {}).get("name") or "").strip() or index_key
    return index_key


def title_of(key: str, entry: dict, manifest: dict) -> str:
    """What the Themes page calls a theme: the entry's `name`, else the manifest's, else its key."""
    for named in ((entry or {}).get("name"), (manifest or {}).get("name")):
        if str(named or "").strip():
            return str(named).strip()
    return key


def merge(parts: Iterable[tuple[str, dict | None]], origins: dict | None = None,
          lost: dict[str, list[str]] | None = None) -> dict:
    """One index from many sources, first mention winning and the loser logged.

    Settles registry keys only. A repository is keyed by its url until its manifest
    arrives, so `ThemeRegistry.load_theme_manifests` runs the same contest again.

    `origins`, when given, is filled with the source each kept key came from, and `lost`
    with the keys each source offered that an earlier one already held.
    """
    index: dict = {}
    origins = {} if origins is None else origins
    lost = {} if lost is None else lost
    for origin, themes in parts:
        for key, entry in (themes or {}).items():
            if key in index:
                logger.warning("Theme '%s' from %s is already provided by %s - keeping the first",
                               key, origin, origins[key])
                lost.setdefault(origin, []).append(key)
                continue
            index[key] = entry
            origins[key] = origin
    return index
