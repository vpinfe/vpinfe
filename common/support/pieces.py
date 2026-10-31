"""A cleaned file cut into plain text and the places where something was replaced.

For the person's own review screen. The original of a replaced value is read from the
package's ledger and goes only to that screen, never into the package or an API answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from common.support import package, redact


@dataclass(frozen=True)
class Piece:
    """A stretch of one line. `kind` is None for ordinary text."""

    text: str
    kind: str | None = None
    original: str = ""


def line_pieces(line: str, originals: dict[str, str]) -> list[Piece]:
    pieces: list[Piece] = []
    end = 0
    for found in redact.PLACEHOLDER.finditer(line):
        if found.start() > end:
            pieces.append(Piece(line[end:found.start()]))
        token = found.group(0)
        pieces.append(Piece(token, redact.kind_of(token), originals.get(token, "")))
        end = found.end()
    if end < len(line):
        pieces.append(Piece(line[end:]))
    return pieces


def matching(made: package.Package, needle: str, limit: int = 200) -> list[tuple[str, int]]:
    """(file, 0-based line) of each line holding the text, case-insensitively. Capped, so
    a one-letter search cannot ask the page to list a whole log."""
    if not needle.strip():
        return []
    wanted = needle.lower()
    found: list[tuple[str, int]] = []
    for one in made.parts:
        for index, line in enumerate(one.text.split("\n")):
            if wanted in line.lower():
                found.append((one.name, index))
                if len(found) >= limit:
                    return found
    return found
