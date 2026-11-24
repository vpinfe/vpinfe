"""Whether a link VPS lists is a file you can go and get.

A listing is not a download. Three answers - `available` is one artifact, `reference` a
page that is not a download, `collection` somewhere to browse.

**`restricted` is deliberately absent.** Whether a host will serve this account cannot
be told without asking it, so `available` means "the catalog lists a file" and never
"it is yours to take".

Signals in the order they are trusted: the shape a host writes a folder in, then a bare
site root, then repetition across records - which is the backstop for what no shape
says, and is kept at a high threshold because a release page legitimately shared by a
handful of tables is not a collection.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

AVAILABLE = "available"
REFERENCE = "reference"
COLLECTION = "collection"
UNKNOWN = "unknown"

# Hosts that publish pages rather than files. A storefront and a video are both worth
# following and neither is an asset.
NOT_A_DOWNLOAD = frozenset({
    "youtube.com", "youtu.be", "ipdb.org", "pinballfx.com", "zenstudios.com",
    "facebook.com", "patreon.com", "twitch.tv",
})

# Written into the URL by the two hosts that distinguish them.
FOLDER_MARKS = ("#f!", "/folder/", "/drive/folders/")

# Above this many records behind one link, it is somewhere to browse. High on purpose:
# the band below it is release pages legitimately serving a handful of tables.
SHARED_BY = 20


def canonical(url: str) -> str:
    """A URL as the identity of a destination, for asking how many records share it.

    Host case, `www.` and a trailing slash are noise; the query is not. Dropping it
    collapses every `index.php?showfile=NNNN` onto one key, which reads a whole site's
    release pages as a single crowded link.
    """
    parts = urlsplit((url or "").strip())
    if not parts.netloc:
        return ""
    host = parts.netloc.lower().removeprefix("www.")
    query = f"?{parts.query}" if parts.query else ""
    tail = f"#{parts.fragment}" if parts.fragment else ""
    return f"{host}{parts.path.rstrip('/')}{query}{tail}"


def crowded(urls: Iterable[str]) -> frozenset[str]:
    """The canonical links standing behind enough records to be somewhere to browse.

    Over the whole catalog, because the question is about the corpus rather than about
    any one link.
    """
    seen: dict[str, int] = {}
    for url in urls:
        key = canonical(url)
        if key:
            seen[key] = seen.get(key, 0) + 1
    return frozenset(key for key, count in seen.items() if count >= SHARED_BY)


def classify(url: str, shared: frozenset[str] | None = None) -> str:
    """What this link leads to. `shared` is `crowded()` over the corpus, where the
    caller has one - without it the repetition signal is simply not applied."""
    parts = urlsplit((url or "").strip())
    if not parts.netloc or parts.scheme not in ("http", "https"):
        return UNKNOWN
    host = parts.netloc.lower().removeprefix("www.")
    whole = url.lower()
    if any(mark in whole for mark in FOLDER_MARKS):
        return COLLECTION
    if not parts.path.strip("/"):
        return REFERENCE
    if host in NOT_A_DOWNLOAD or any(
            host.endswith("." + known) for known in NOT_A_DOWNLOAD):
        return REFERENCE
    if shared and canonical(url) in shared:
        return COLLECTION
    return AVAILABLE


def best_of(urls: Iterable[str] | None, shared: frozenset[str] | None = None) -> str:
    """One answer for a record that lists several links: the most obtainable of them.

    A record offering a direct file and a forum thread offers the file - the thread
    does not make it less gettable.
    """
    order = (AVAILABLE, COLLECTION, REFERENCE, UNKNOWN)
    found = {classify(url, shared) for url in (urls or [])}
    return next((answer for answer in order if answer in found), UNKNOWN)
