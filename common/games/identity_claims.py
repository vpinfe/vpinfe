"""What a file claims to be, and how much that claim is worth.

    construction  ==  declared   >   user   >   exact

The two at the top rank together because neither inferred anything, and they never collide:
either VPinFE built the file or something handed it over. `exact` is the one basis that is
inferred, and only VPinFE's own sweep writes it, so it is not among what a caller may send:
a caller that inferred sends nothing and the file joins the manual queue, which is what
makes this safe to accept over HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.i18n import t

# VPinFE built the file from a known base and a known patch.
CONSTRUCTION = "construction"
# Something fetched the file from a named upstream record and said which.
DECLARED = "declared"
# A person picked the record this file matches.
USER = "user"
# VPinFE found exactly one release with the file's authors and version.
EXACT = "exact"

# What a caller may send. `construction` and `exact` are ours to write and nobody else's.
ACCEPTED_FROM_CALLERS = (DECLARED, USER)

# Most trusted first. Position is the comparison; the two at the top are equal.
_RANK = {CONSTRUCTION: 0, DECLARED: 0, USER: 1, EXACT: 2}


def rank(confirmed_by: str) -> int:
    """How much a claim is worth, lower being better. An unknown value ranks last."""
    return _RANK.get(str(confirmed_by or "").strip().lower(), len(_RANK))


def outranks(new: str, existing: str) -> bool:
    """Whether a new claim may overwrite one already recorded. Equal does not: the first
    witness keeps it, so re-importing the same file does not churn the `.info`."""
    return rank(new) < rank(existing)


@dataclass(frozen=True)
class DeclaredIdentity:
    """What the sender says a file is. Every field optional; saying nothing is allowed."""

    vps_file_id: str = ""
    host_item_id: str = ""
    host: str = ""
    game_id: str = ""
    table_id: str = ""
    confirmed_by: str = ""

    @property
    def names_a_record(self) -> bool:
        return bool(self.vps_file_id or self.host_item_id)

    @property
    def is_empty(self) -> bool:
        return not any((self.vps_file_id, self.host_item_id, self.host,
                        self.game_id, self.table_id, self.confirmed_by))

    def problems(self) -> list[str]:
        """Why this claim cannot be accepted, or an empty list. Here rather than at the
        endpoint so every caller answers to the same rules."""
        found = []
        basis = str(self.confirmed_by or "").strip().lower()
        if basis and basis not in ACCEPTED_FROM_CALLERS:
            found.append(t("error.identity.basis_not_accepted", basis="confirmed_by",
                           accepted=", ".join(ACCEPTED_FROM_CALLERS)))
        if self.names_a_record and not basis:
            found.append(t("error.identity.no_basis", basis="confirmed_by"))
        if basis and not (self.names_a_record or self.game_id or self.table_id):
            found.append(t("error.identity.orphan_basis", basis="confirmed_by"))
        if self.vps_file_id and not self.host_item_id:
            found.append(t("error.identity.no_item", file_id="vps_file_id",
                           item_id="host_item_id"))
        return found


def source_block(identity: DeclaredIdentity, *, md5: str = "") -> dict:
    """Which upstream record this is - never how the bytes arrived."""
    block: dict = {}
    for key, value in (("host", identity.host), ("host_item_id", identity.host_item_id),
                       ("vps_file_id", identity.vps_file_id),
                       ("confirmed_by", str(identity.confirmed_by or "").strip().lower())):
        if value:
            block[key] = value
    if md5:
        block["hash"] = md5
    return block
