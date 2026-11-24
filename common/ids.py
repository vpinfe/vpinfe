"""How VPinFE mints an opaque local id. Games and tables use the same scheme.

Here rather than in `game_identity` so `tables` can import it: `game_identity` reaches
back through `game_metadata` to `metaconfig`, which imports `tables`.
"""

from __future__ import annotations

import hashlib
import secrets
import unicodedata

# No 0/O or I/l, so an id survives being read down a phone or retyped out of a bug report.
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
LENGTH = 10


def new_id() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def derive_id(*parts: str) -> str:
    """The id these parts always make, in the shape `new_id` makes.

    Parts are normalized to NFC so one folder read through a Mac and a Linux share names
    the same id. A name that is not valid UTF-8 (a lone surrogate) still makes one.
    """
    joined = "\0".join(unicodedata.normalize("NFC", part) for part in parts)
    digest = hashlib.sha256(joined.encode("utf-8", "surrogateescape")).digest()
    rest = int.from_bytes(digest, "big")
    chars = []
    for _ in range(LENGTH):
        rest, at = divmod(rest, len(ALPHABET))
        chars.append(ALPHABET[at])
    return "".join(chars)
