"""The folder a file-taking picker last took a file from, per browser.

Stored as text exactly as the host named it. A Windows path is compared without regard
to case, since the file system is not particular about it.
"""

from __future__ import annotations

import sys
from typing import Any

from common.i18n import t
from manager.kit import folder_picker, remembered, verbs

_ANY = "picker.any"


def _key(kind: str) -> str:
    return f"picker.{kind}"


def same(one: str, other: str) -> bool:
    if "\\" in one or "\\" in other or sys.platform == "win32":
        return one.rstrip("\\/").casefold() == other.rstrip("\\/").casefold()
    return one.rstrip("/") == other.rstrip("/")


def record(kind: str, path: str) -> None:
    """Note that a file, or a folder taken whole, came from the folder that holds `path`."""
    folder = folder_picker.enclosing(path)
    if not folder:
        return
    if kind:
        remembered.put(_key(kind), folder)
    remembered.put(_ANY, folder)


def last(kind: str) -> str:
    """Where the last file of this kind came from, else the last of any kind, else ""."""
    return str(remembered.get(_key(kind), "") or remembered.get(_ANY, "") or "") if kind \
        else str(remembered.get(_ANY, "") or "")


def places(kind: str, kinds: str, listed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The picker's Places: the folder the last file of this kind came from, then
    Downloads, then the rest. A folder named twice is listed where it comes first. Where
    nothing of this kind was taken yet, the folder the last file of any kind came from
    stands in, since that is where the picker opens."""
    of_kind = str(remembered.get(_key(kind), "") or "") if kind else ""
    anywhere = str(remembered.get(_ANY, "") or "")
    first: list[dict[str, Any]] = []
    if of_kind:
        first.append({"name": t("manager.picker.last_used.kinds", kinds=kinds),
                      "path": of_kind, "icon": verbs.LAST_USED})
    elif anywhere:
        first.append({"name": t("manager.picker.last_used"), "path": anywhere,
                      "icon": verbs.LAST_USED})
    downloads = [one for one in listed if one.get("name") == t("word.downloads")
                 and not one.get("source")]
    rest = [one for one in listed if one not in downloads]
    out: list[dict[str, Any]] = []
    for one in (*first, *downloads, *rest):
        if not any(same(str(one["path"]), str(seen["path"])) for seen in out):
            out.append(one)
    return out
