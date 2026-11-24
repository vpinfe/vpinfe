"""The settings reference in `docs/technical-details.md`, rendered from the schema.

The doc had drifted four ways at once - it named the file `vpinfe.ini` after the move to
JSON, listed sections and keys under names three renames had replaced, and documented the
pre-3.0 input vocabulary. Hand-editing it after every rename is what did not happen, so it
is generated and a test fails when the file and the schema disagree.

`config_schema` owns the content; this owns the table.
"""

from __future__ import annotations

from pathlib import Path

from common.config import config_schema

DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "technical-details.md"

# The generated block ends where the hand-written rest of the document begins.
END_MARKER = "## Game Metadata File"

HEADER = """## vpinfe.json Definition

VPinFE stores its settings as JSON in a platform-specific configuration directory. On
first run it writes a complete `vpinfe.json` there, so a new install never needs the file
opened manually:

- **Linux**: `~/.config/vpinfe/vpinfe.json`
- **macOS**: `~/Library/Application Support/vpinfe/vpinfe.json`
- **Windows**: `C:\\Users\\<username>\\AppData\\Local\\vpinfe\\vpinfe\\vpinfe.json`

A `vpinfe.ini` from an earlier build is read once, converted, and kept, so downgrading
still works. Settings live under a `settings` object beside a `schema` version, and each
heading below is a key in it - `windows.playfield` is a `playfield` object inside a
`windows` object.

Every name a setting has ever had keeps resolving, so an older file and a hand-edit that
uses an old spelling both still load.

<!-- generated from common/config/config_schema.py by tests/support/config_reference.py -->
"""


def _cell(text: str) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def _type_of(entry) -> str:
    if entry.choices:
        return f"{entry.type} ({', '.join(entry.choices)})"
    return entry.type


def _default_of(entry) -> str:
    return f"`{entry.default}`" if entry.default != "" else ""


INTERNAL_NOTE = "Runtime state written by VPinFE, not shown as a setting."

SECTION_NOTES = {
    "vpinplay": "Owned by the VPinPlay extension. Declared here so a 2.x file converts, "
                "read once, and written by nothing in core. `api_endpoint` is the "
                "extension's Server setting, `user_id` and `machine_id` become the "
                "owner's VPinPlay account, and `initials` the owner's initials. "
                "`sync_on_exit` is not carried over: nothing is shared until each "
                "player turns Share on.",
}


def render() -> str:
    """The whole generated block, ending just before END_MARKER."""
    sections: dict[str, list] = {}
    for entry in config_schema.options():
        sections.setdefault(entry.section, []).append(entry)

    out = [HEADER]
    for section, entries in sections.items():
        out.append(f"### `{section}`\n")
        if all(entry.internal for entry in entries):
            out.append(SECTION_NOTES.get(section, INTERNAL_NOTE) + "\n")
        out.append("| Key | Type | Default | Description |")
        out.append("| --- | --- | --- | --- |")
        for entry in entries:
            description = entry.description or entry.label
            out.append(f"| `{entry.key}` | {_type_of(entry)} | {_default_of(entry)} "
                       f"| {_cell(description)} |")
        out.append("")
    return "\n".join(out) + "\n"


def current() -> str:
    """What the doc holds today, for the same span the generator owns."""
    return DOC.read_text(encoding="utf-8").split(END_MARKER)[0]


def write() -> None:
    """Regenerate the doc in place, leaving everything after END_MARKER alone."""
    text = DOC.read_text(encoding="utf-8")
    _, marker, tail = text.partition(END_MARKER)
    DOC.write_text(render() + marker + tail, encoding="utf-8")


if __name__ == "__main__":
    write()
    print(f"regenerated {DOC}")
