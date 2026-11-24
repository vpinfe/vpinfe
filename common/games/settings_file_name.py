"""What a settings file may be called. Pure, so a client checks a name as it is typed with
the same rule the server refuses by."""

from __future__ import annotations

from common.i18n import t

SUFFIX = ".ini"
_BAD_CHARACTERS = '/\\:*?"<>|'
_DEVICE_NAMES = ("CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)),
                 *(f"LPT{n}" for n in range(1, 10)))


def problem(name: str) -> str:
    """Why this cannot be a settings file's name on any system, or "". Windows is held to
    on every system, so a file made here still opens on a cabinet that runs it."""
    text = str(name or "").strip()
    stem = text[:-len(SUFFIX)] if text.lower().endswith(SUFFIX) else text
    if not stem.strip():
        return t("error.launchers.name_empty")
    for one in _BAD_CHARACTERS:
        if one in stem:
            return t("error.launchers.name_character", character=one)
    if not stem.isprintable():
        return t("error.launchers.name_character", character="a control character")
    device = stem.split(".", 1)[0].strip()
    for reserved in _DEVICE_NAMES:
        if device.upper() == reserved:
            return t("error.launchers.name_device", name=reserved)
    return ""


def with_suffix(name: str) -> str:
    """The name as a settings file is called: always ending `.ini`, so a name typed
    or sent without it cannot land on a file of another kind."""
    text = str(name or "").strip()
    return text if text.lower().endswith(SUFFIX) else f"{text}{SUFFIX}"


def from_display_name(display_name: str) -> str:
    """The file name a launcher's own settings get: its name, with the characters a file
    cannot have swapped for a hyphen, and a hyphen after a Windows device name (Aux-.ini).
    "" where nothing usable is left."""
    cleaned = "".join("-" if one in _BAD_CHARACTERS else one for one in str(display_name or ""))
    cleaned = cleaned.strip().rstrip(".")
    if not cleaned:
        return ""
    head, dot, rest = cleaned.partition(".")
    if head.strip().upper() in _DEVICE_NAMES:
        cleaned = f"{head.rstrip()}-{dot}{rest}"
    name = f"{cleaned}{SUFFIX}"
    return "" if problem(name) else name
