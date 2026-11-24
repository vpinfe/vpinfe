"""Visual Pinball's own key mappings, as the keys a binding names.

VPX keeps them under `[Input]` in its settings file, `Mapping.<Name> = Key;<scancode>`.
The scancode is SDL's, which is the USB HID usage number, so each one names the key a
browser reports as `KeyboardEvent.code`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger("vpinfe.apps.vpx.keys")

_KEY = re.compile(r"\bKey;(\d+)\b")

_NAMED = {
    40: "Enter", 41: "Escape", 42: "Backspace", 43: "Tab", 44: "Space",
    45: "Minus", 46: "Equal", 47: "BracketLeft", 48: "BracketRight", 49: "Backslash",
    51: "Semicolon", 52: "Quote", 53: "Backquote", 54: "Comma", 55: "Period",
    56: "Slash", 57: "CapsLock",
    70: "PrintScreen", 71: "ScrollLock", 72: "Pause", 73: "Insert", 74: "Home",
    75: "PageUp", 76: "Delete", 77: "End", 78: "PageDown", 79: "ArrowRight",
    80: "ArrowLeft", 81: "ArrowDown", 82: "ArrowUp", 83: "NumLock",
    84: "NumpadDivide", 85: "NumpadMultiply", 86: "NumpadSubtract", 87: "NumpadAdd",
    88: "NumpadEnter", 98: "Numpad0", 99: "NumpadDecimal", 100: "IntlBackslash",
    101: "ContextMenu",
    224: "ControlLeft", 225: "ShiftLeft", 226: "AltLeft", 227: "MetaLeft",
    228: "ControlRight", 229: "ShiftRight", 230: "AltRight", 231: "MetaRight",
}


def code_of(scancode: int) -> str:
    """The code of one of VPX's scancodes, or "" for one with no key here."""
    if 4 <= scancode <= 29:
        return f"Key{chr(ord('A') + scancode - 4)}"
    if 30 <= scancode <= 39:
        return f"Digit{(scancode - 29) % 10}"
    if 58 <= scancode <= 69:
        return f"F{scancode - 57}"
    if 89 <= scancode <= 97:
        return f"Numpad{scancode - 88}"
    if 104 <= scancode <= 115:
        return f"F{scancode - 91}"
    return _NAMED.get(scancode, "")


def entries(ini_path: str) -> dict[str, str]:
    """Every `Mapping.*` in the file's `[Input]`, by name, with its value as written.

    Nothing for a path that names no file. A file that cannot be read or decoded raises:
    VPX writes UTF-8, and a settings file saved by another tool may not be.
    """
    ini_file = Path((ini_path or "").strip()).expanduser()
    if not str(ini_path or "").strip() or not ini_file.is_file():
        return {}
    found: dict[str, str] = {}
    in_input_section = False
    with ini_file.open("r", encoding="utf-8-sig") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                in_input_section = line == "[Input]"
                continue
            if not in_input_section or not line.startswith("Mapping."):
                continue
            key, _, value = line.partition("=")
            found[key[len("Mapping."):].strip()] = value.strip()
    return found


def scancodes(ini_path: str) -> dict[str, int]:
    """Every bound `Mapping.*` in the file's `[Input]`, by name. An unbound one is left
    out, and a file holding none says so in the log."""
    mappings: dict[str, int] = {}
    unbound: list[str] = []
    ini_path = (ini_path or "").strip()
    if not ini_path:
        logger.warning("Skipping VPinballX.ini key mapping parse: no settings file is set")
        return mappings
    ini_file = Path(ini_path).expanduser()
    if not ini_file.is_file():
        logger.warning("Skipping VPinballX.ini key mapping parse: file not found at %s",
                       ini_file)
        return mappings

    for name, value in entries(ini_path).items():
        match = _KEY.search(value)
        if match:
            mappings[name] = int(match.group(1))
        else:
            # VPX writes "Mapping.LeftFlipper = " with no value until the user binds
            # that key in its own UI, so an entry existing says nothing about
            # whether it can send one.
            unbound.append(name)

    if not mappings:
        if unbound:
            logger.warning(
                "None of the %s VPX key mappings in %s are bound to a key, so no VPX "
                "button can send one. Assign them in Visual Pinball's own keyboard "
                "settings.", len(unbound), ini_file)
        else:
            logger.warning("No Mapping.* entries under [Input] in %s, so no VPX button "
                           "can send a key.", ini_file)
    return mappings


def mappings(ini_path: str) -> dict[str, str]:
    """Every bound mapping in the file, by name, as the code of its key."""
    found = scancodes(ini_path)
    out: dict[str, str] = {}
    untranslated: list[str] = []
    for name, scancode in found.items():
        code = code_of(scancode)
        if code:
            out[name] = code
        else:
            untranslated.append(f"{name} (scancode {scancode})")
    if untranslated:
        logger.debug("No key for %s of %s VPX mappings: %s", len(untranslated),
                     len(found), ", ".join(untranslated))
    return out
