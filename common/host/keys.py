"""The keys of a keyboard, by the name a binding gives them, and by every other name a
program on this device knows them by.

A binding names a key by `KeyboardEvent.code` - `KeyP`, `Escape` - which says which key
it is rather than what it types. Linux input devices number the same key (`evdev`), an
XKB keymap names it (`keysym`, what wtype takes), and the key simulator had its own name
for it (`key_id`). One row per key holds all four, so pressing a key and hearing one are
the same table read in two directions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Key:
    code: str
    evdev: int
    keysym: str
    # The key simulator's own name, where it has one.
    key_id: str = ""


def _letters() -> list[Key]:
    evdev = dict(zip("qwertyuiop", range(16, 26), strict=True))
    evdev.update(zip("asdfghjkl", range(30, 39), strict=True))
    evdev.update(zip("zxcvbnm", range(44, 51), strict=True))
    return [Key(f"Key{letter.upper()}", evdev[letter], letter, letter)
            for letter in "abcdefghijklmnopqrstuvwxyz"]


def _digits() -> list[Key]:
    return [Key(f"Digit{digit}", 2 + (int(digit) - 1) % 10, digit, digit)
            for digit in "1234567890"]


def _function_keys() -> list[Key]:
    evdev = {n: 58 + n for n in range(1, 11)} | {11: 87, 12: 88}
    evdev |= {n: 170 + n for n in range(13, 25)}
    return [Key(f"F{n}", evdev[n], f"F{n}", f"f{n}" if n <= 12 else "")
            for n in range(1, 25)]


_NAMED = [
    Key("Escape", 1, "Escape", "esc"),
    Key("Minus", 12, "minus", "-"),
    Key("Equal", 13, "equal", "="),
    Key("Backspace", 14, "BackSpace", "backspace"),
    Key("Tab", 15, "Tab", "tab"),
    Key("BracketLeft", 26, "bracketleft", "["),
    Key("BracketRight", 27, "bracketright", "]"),
    Key("Enter", 28, "Return", "enter"),
    Key("ControlLeft", 29, "Control_L", "ctrl_l"),
    Key("Semicolon", 39, "semicolon", ";"),
    Key("Quote", 40, "apostrophe", "'"),
    Key("Backquote", 41, "grave", "`"),
    Key("ShiftLeft", 42, "Shift_L", "shift_l"),
    Key("Backslash", 43, "backslash", "\\"),
    Key("Comma", 51, "comma", ","),
    Key("Period", 52, "period", "."),
    Key("Slash", 53, "slash", "/"),
    Key("ShiftRight", 54, "Shift_R", "shift_r"),
    Key("NumpadMultiply", 55, "KP_Multiply"),
    Key("AltLeft", 56, "Alt_L", "alt_l"),
    Key("Space", 57, "space", "space"),
    Key("CapsLock", 58, "Caps_Lock"),
    Key("NumLock", 69, "Num_Lock"),
    Key("ScrollLock", 70, "Scroll_Lock"),
    Key("Numpad7", 71, "KP_7"),
    Key("Numpad8", 72, "KP_8"),
    Key("Numpad9", 73, "KP_9"),
    Key("NumpadSubtract", 74, "KP_Subtract"),
    Key("Numpad4", 75, "KP_4"),
    Key("Numpad5", 76, "KP_5"),
    Key("Numpad6", 77, "KP_6"),
    Key("NumpadAdd", 78, "KP_Add"),
    Key("Numpad1", 79, "KP_1"),
    Key("Numpad2", 80, "KP_2"),
    Key("Numpad3", 81, "KP_3"),
    Key("Numpad0", 82, "KP_0"),
    Key("NumpadDecimal", 83, "KP_Decimal"),
    Key("IntlBackslash", 86, "less"),
    Key("NumpadEnter", 96, "KP_Enter"),
    Key("ControlRight", 97, "Control_R", "ctrl_r"),
    Key("NumpadDivide", 98, "KP_Divide"),
    Key("PrintScreen", 99, "Print", "print_screen"),
    Key("AltRight", 100, "Alt_R", "alt_r"),
    Key("Home", 102, "Home", "home"),
    Key("ArrowUp", 103, "Up", "up"),
    Key("PageUp", 104, "Prior", "page_up"),
    Key("ArrowLeft", 105, "Left", "left"),
    Key("ArrowRight", 106, "Right", "right"),
    Key("End", 107, "End", "end"),
    Key("ArrowDown", 108, "Down", "down"),
    Key("PageDown", 109, "Next", "page_down"),
    Key("Insert", 110, "Insert", "insert"),
    Key("Delete", 111, "Delete", "delete"),
    Key("Pause", 119, "Pause", "pause"),
    Key("MetaLeft", 125, "Super_L", "cmd"),
    Key("MetaRight", 126, "Super_R", "cmd_r"),
    Key("ContextMenu", 127, "Menu"),
]

KEYS: tuple[Key, ...] = (*_letters(), *_digits(), *_function_keys(), *_NAMED)

BY_CODE = {key.code: key for key in KEYS}
BY_EVDEV = {key.evdev: key for key in KEYS}
BY_KEY_ID = {key.key_id: key for key in KEYS if key.key_id}


def code_of_evdev(number: int) -> str:
    """The code of a Linux key number, or "" for one this table does not hold."""
    key = BY_EVDEV.get(int(number))
    return key.code if key else ""
