"""Every input action VPinFE understands, declared once.

Eleven actions, each with one ordered list of bindings. The names say what the player
*meant* rather than which way a stick moved: three surfaces - the wheel, a vertical menu
and a scrolling page - already disagreed about what "left" pointed at, and every overlay
carried a fall-through case as the evidence.

A binding names its own input - `key:<name>`, `pad:<index>/button:<n>` - so an action
needs one list rather than a key per input. Richer selectors - modifiers, axes, hold,
chord - go in this same list, so adding them is a parser change rather than another
migration.

An input has one press and one hold, so `key:Escape` and `key:Escape@hold:1500` share a
key without clashing, and two holds on one key do clash whatever their lengths.
`pad:standard/button:<n>` is the standard layout's button n, for a pad the browser reports
as standard only; the numbered form is for any pad.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import NamedTuple

from common.config import setting_groups
from common.i18n import t

SECTION = "input"

# Selector prefixes. A binding that starts with neither is not one we can read.
KEY_PREFIX = "key:"
PAD_PREFIX = "pad:"

# `pad:standard/button:<n>` is button n of the standard layout, and fires only on a pad the
# browser reports as `mapping: "standard"`. `pad:<index>/button:<n>` fires on any pad: a
# pad's index says nothing about which device it is.
STANDARD_PAD = f"{PAD_PREFIX}standard"

# How long Esc is held to quit.
QUIT_HOLD_MS = 1500


def _standard(button: int, hold: int = 0) -> str:
    return f"{STANDARD_PAD}/button:{button}" + (f"@hold:{hold}" if hold else "")


# The standard layout's buttons, by index.
_FACE_0, _FACE_1 = 0, 1
_LEFT_SHOULDER, _RIGHT_SHOULDER, _LEFT_TRIGGER, _RIGHT_TRIGGER = 4, 5, 6, 7
_SELECT, _START = 8, 9
_LEFT_STICK, _RIGHT_STICK = 10, 11
_DPAD_UP, _DPAD_DOWN, _DPAD_LEFT, _DPAD_RIGHT = 12, 13, 14, 15

# The face buttons, Select and Start are not named: a button with no name here reads by its number.
_PAD_WORDS = {
    _LEFT_SHOULDER: "input.pad.left_shoulder", _RIGHT_SHOULDER: "input.pad.right_shoulder",
    _LEFT_TRIGGER: "input.pad.left_trigger", _RIGHT_TRIGGER: "input.pad.right_trigger",
    _LEFT_STICK: "input.pad.left_stick", _RIGHT_STICK: "input.pad.right_stick",
    _DPAD_UP: "input.pad.dpad_up", _DPAD_DOWN: "input.pad.dpad_down",
    _DPAD_LEFT: "input.pad.dpad_left", _DPAD_RIGHT: "input.pad.dpad_right",
}


@dataclass(frozen=True)
class InputAction:
    """One thing a player can ask for, and what is bound to it out of the box."""

    name: str
    bindings: tuple[str, ...]
    # What this used to be called, so an existing [Input] section still resolves.
    legacy: tuple[str, ...] = ()
    # What kind of thing it does, for a page that has to show ten of these. Here rather
    # than on the settings schema because this registry is what knows what an action is
    # for - the schema is generated from it and would be restating the answer.
    group: str = ""

    @property
    def label(self) -> str:
        """What a player calls this action, for the settings page that lists them."""
        return t(f"input.{self.name}.label")

    @property
    def config_key(self) -> str:
        return self.name

    @property
    def legacy_joy_key(self) -> str:
        """The `joy*` key this action used to have, for the projections that still answer."""
        return next((k for k in self.legacy if k.startswith("joy")), "")

    @property
    def legacy_key_key(self) -> str:
        """The `key*` key this action used to have."""
        return next((k for k in self.legacy if k.startswith("key")), "")


INPUT_ACTIONS: tuple[InputAction, ...] = (
    InputAction(
        "previous",
        group=setting_groups.NAVIGATION,
        bindings=("key:ArrowLeft", "key:ShiftLeft",
                  _standard(_DPAD_LEFT), _standard(_LEFT_TRIGGER)),
        legacy=("joyleft", "keyleft"),
    ),
    InputAction(
        "next",
        group=setting_groups.NAVIGATION,
        bindings=("key:ArrowRight", "key:ShiftRight",
                  _standard(_DPAD_RIGHT), _standard(_RIGHT_TRIGGER)),
        legacy=("joyright", "keyright"),
    ),
    # up/down and pageup/pagedown were the same intent under two names: carousel-desktop
    # used up/down for a page-sized jump, which is what paging is. Named for where the
    # selection goes, not the key: "page up" has no answer on a horizontal wheel, and
    # core answered it two ways.
    InputAction(
        "page_previous",
        group=setting_groups.NAVIGATION,
        bindings=("key:PageUp", "key:ArrowUp",
                  _standard(_DPAD_UP), _standard(_LEFT_SHOULDER)),
        legacy=("joypageup", "keypageup", "joyup", "keyup"),
    ),
    InputAction(
        "page_next",
        group=setting_groups.NAVIGATION,
        bindings=("key:PageDown", "key:ArrowDown",
                  _standard(_DPAD_DOWN), _standard(_RIGHT_SHOULDER)),
        legacy=("joypagedown", "keypagedown", "joydown", "keydown"),
    ),
    InputAction(
        "select",
        group=setting_groups.NAVIGATION,
        bindings=("key:Enter", _standard(_FACE_0)),
        legacy=("joyselect", "keyselect"),
    ),
    InputAction(
        "back",
        group=setting_groups.NAVIGATION,
        bindings=("key:Escape", "key:KeyB", _standard(_FACE_1)),
        legacy=("joyback", "keyback"),
    ),
    InputAction(
        "menu",
        group=setting_groups.MENUS,
        bindings=("key:KeyM", _standard(_START)),
        legacy=("joymenu", "keymenu"),
    ),
    InputAction(
        "collection_menu",
        group=setting_groups.MENUS,
        bindings=("key:KeyC", _standard(_SELECT)),
        legacy=("joycollectionmenu", "keycollectionmenu"),
    ),
    InputAction(
        "tutorial",
        group=setting_groups.MENUS,
        bindings=("key:KeyT",),
        legacy=("joytutorial", "keytutorial"),
    ),
    InputAction(
        "exit",
        group=setting_groups.NAVIGATION,
        bindings=(f"key:Escape@hold:{QUIT_HOLD_MS}", "key:KeyQ",
                  _standard(_FACE_1, QUIT_HOLD_MS)),
        legacy=("joyexit", "keyexit"),
    ),
    # Unbound out of the box: a key bound here is listened for from outside the page
    # while a table runs, which asks for a permission on some systems.
    InputAction(
        "take_picture",
        group=setting_groups.DURING_PLAY,
        bindings=(),
    ),
)

# The actions heard while a table runs. The first is what starts anything, and the rest
# only answer it, so nothing is listened for during play until the first is bound.
IN_PLAY = ("take_picture", "back")


def actions() -> tuple[InputAction, ...]:
    return INPUT_ACTIONS


def defaults() -> dict[str, tuple[str, ...]]:
    """The bindings a fresh install ships, keyed by action."""
    return {action.name: action.bindings for action in INPUT_ACTIONS}


def bound(config: object) -> dict[str, list[str]]:
    """Every action and what is bound to it, in order, as `config` holds it.

    A parser the store has migrated holds the list directly. One it has not - a config an
    older build wrote, or one a caller assembled - still holds a key per input, and those
    are assembled here rather than read as if they were already selectors.
    """
    parser = getattr(config, "config", config)

    def raw(section: str, key: str) -> str | None:
        # Exactly this key rather than cfg_get, which answers every legacy key of an
        # action with the one option they all resolve to.
        try:
            has = parser.has_option(section, key)  # type: ignore[attr-defined]
            return parser.get(section, key) if has else None  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - a config that cannot answer holds nothing
            return None

    out: dict[str, list[str]] = {}
    for action in INPUT_ACTIONS:
        current = raw(SECTION, action.name)
        if current is not None:
            out[action.name] = [p.strip() for p in current.split(",") if p.strip()]
            continue
        found: list[str] = []
        for old in action.legacy:
            value = raw("Input", old)
            if value is not None:
                found += binding_for_legacy(old, value)
        out[action.name] = found or list(action.bindings)
    return out


def action_for_legacy_key(key: str) -> str:
    """The action an old `[Input]` key bound, or "" when it bound none."""
    wanted = str(key or "").strip().lower()
    for action in INPUT_ACTIONS:
        if wanted == action.name or wanted in action.legacy:
            return action.name
    return ""


def binding_for_legacy(key: str, value: str) -> list[str]:
    """The selectors an old `[Input]` value means.

    `keyleft = ArrowLeft,ShiftLeft` was a comma-separated list of key names; `joyleft = 3`
    was one gamepad button index and nothing said which pad. Both become selectors.
    """
    name = str(key or "").strip().lower()
    raw = str(value or "").strip()
    if not raw:
        return []
    if name.startswith("joy"):
        index = raw.split(",")[0].strip()
        return [f"{PAD_PREFIX}0/button:{index}"] if index else []
    return [f"{KEY_PREFIX}{part.strip()}" for part in raw.split(",") if part.strip()]


# What 2.x wrote into `[Input]` for a key nobody changed. Every 2.x install carries these
# on disk, so a value equal to one is indistinguishable from a choice and is read as none.
LEGACY_DEFAULTS = {
    "keyleft": "ArrowLeft,ShiftLeft", "keyright": "ArrowRight,ShiftRight",
    "keyup": "ArrowUp", "keydown": "ArrowDown",
    "keypageup": "PageUp", "keypagedown": "PageDown",
    "keyselect": "Enter", "keyback": "b", "keymenu": "m", "keyexit": "Escape,q",
    "keycollectionmenu": "c", "keytutorial": "t",
}


def same_bindings(left: Iterable[str], right: Iterable[str]) -> bool:
    """Whether two lists bind the same things, in any order and either spelling."""
    return {identity(one) for one in left} == {identity(one) for one in right}


def _shipped_by_2x(old: str, value: str) -> bool:
    shipped = LEGACY_DEFAULTS.get(old)
    return shipped is not None and same_bindings(
        binding_for_legacy(old, value), binding_for_legacy(old, shipped))


def _legacy_choice(values: Iterable[tuple[str, str]]) -> tuple[bool, list[tuple[str, list[str]]]]:
    """Whether the keys were chosen, and the values somebody chose, as bindings."""
    read = list(values)
    keys_chosen = any(not _shipped_by_2x(old, value)
                      for old, value in read if old.startswith("key"))
    kept = [(old, binding_for_legacy(old, value)) for old, value in read
            if keys_chosen or not old.startswith("key")]
    return keys_chosen, kept


def chosen_in_legacy(values: Iterable[tuple[str, str]]) -> list[str]:
    """The bindings somebody chose in one action's 2.x values, as written."""
    return [b for _, found in _legacy_choice(values)[1] for b in found]


def from_legacy(action: InputAction, values: Iterable[tuple[str, str]],
                taken: Iterable[str] = ()) -> list[str]:
    """What a 2.x install's `[Input]` values for one action mean now.

    A half nobody chose takes the current default: the keys when every `key*` value is
    what 2.x shipped, the controller buttons when no `joy*` value names one. A half
    someone chose is kept as written. Keys come first, as everywhere else.

    `taken` is what other actions' values chose. A default that wants the same press as
    one of those is left out: a choice outranks a default, whichever action holds it.
    """
    keys_chosen, kept = _legacy_choice(values)
    pads_chosen = any(found for old, found in kept if old.startswith("joy"))
    wanted = {contention(one) for one in taken}

    def free(prefix: str) -> list[str]:
        return [b for b in action.bindings
                if b.startswith(prefix) and contention(b) not in wanted]

    out = [] if keys_chosen else free(KEY_PREFIX)
    out += [b for _, found in kept for b in found]
    if not pads_chosen:
        out += free(PAD_PREFIX)
    return out


def keys_in(bindings: Iterable[str] | None) -> list[str]:
    """The keyboard key names in a binding list - what the UI's keyboard field shows."""
    return [b[len(KEY_PREFIX):] for b in bindings or ()
            if str(b).startswith(KEY_PREFIX) and "+" not in b and "@" not in b]


def pad_buttons_in(bindings: Iterable[str] | None) -> list[str]:
    """The plain gamepad button indexes - what the UI's controller field shows."""
    out = []
    for b in bindings or ():
        text = str(b)
        if not text.startswith(PAD_PREFIX) or "@" not in text and "/button:" not in text:
            continue
        if "chord(" in text or "@" in text or "/axis:" in text:
            continue
        # A numbered pad only: `pad:standard` is a layout, and the field this feeds writes
        # `pad:0` back, which would turn a default for standard pads into one for any.
        if not text[len(PAD_PREFIX):].split("/", 1)[0].isdigit():
            continue
        out.append(text.rsplit("/button:", 1)[-1])
    return out


# What no gesture can produce. A chord is pressing two things and a hold is keeping one
# down, so both came off this list when capture learned to watch until release; a
# modifier and an axis have no gesture yet - one because Shift is a flipper on a cabinet
# and must stay bindable on its own, the other because it is a tuned value rather than
# a press.
_UNCAPTURABLE = ("ctrl+", "shift+", "alt+", "meta+", "/axis:")


def capturable(binding: str) -> bool:
    """Whether pressing something could produce this binding again.

    Asked before offering to delete one: a surface that removes what nothing can rebuild
    is a door that only opens one way.
    """
    text = str(binding or "").lower()
    return not any(mark in text for mark in _UNCAPTURABLE)


def describe(binding: str) -> str:
    """One binding as a person reads it.

    A selector is written for a parser - `pad:0/button:3` - and reading ten of them off a
    settings page is the thing that made bindings feel like configuration rather than a
    choice. Anything this cannot name is returned as it came, because inventing a name
    for a chord would be worse than showing the one that is stored.

    A numbered pad's button reads without the pad, because dispatch fires it on that
    button of every pad.
    """
    text = normalize(str(binding or "").strip())
    members = chord_members(text)
    if members:
        # Composed, not looked up. "Left Shift + Right Shift, held 1.5s" is built from
        # its parts, which is where the readable names stop being a table.
        said = describe(members[0])
        for one in members[1:]:
            said = t("input.chord", chord=said, key=describe(one))
        return _held(said, text)
    if text.endswith(_hold_of(text)) and _hold_of(text):
        return _held(describe(text[:-len(_hold_of(text))]), text)
    if any(mark in text for mark in ("@", "+", "/axis:")):
        # Whatever is left after chords and holds have been named above: a modifier, an
        # axis, a selector this build has never seen. Returned whole, because inventing
        # a name would say something the binding does not do.
        return text
    if text.startswith(KEY_PREFIX):
        return _key_name(text[len(KEY_PREFIX):])
    if text.startswith(PAD_PREFIX):
        rest = text[len(PAD_PREFIX):]
        pad, _, what = rest.partition("/")
        if not what.startswith("button:") or not (pad.isdigit() or pad == "standard"):
            # An axis, or something this build has never seen. Naming the pad and trailing the
            # raw rest would read as a name while saying nothing.
            return text
        button = what[len("button:"):]
        if pad == "standard":
            return pad_name(int(button)) if button.isdigit() else text
        return t("input.pad.button", button=button)
    return text


def pad_name(button: int) -> str:
    """A standard-layout button as a person reads it: its name, else its number."""
    word = _PAD_WORDS.get(button)
    return t(word) if word else t("input.pad.button", button=button)


def is_pad(binding: str) -> bool:
    """Whether pressing this means pressing something on a controller."""
    text = identity(binding)
    return any(one.startswith(PAD_PREFIX) for one in chord_members(text) or (text,))


def hold_ms(binding: str) -> int:
    """How long this binding asks to be held, or 0 for one that does not."""
    suffix = _hold_of(binding)
    return int(suffix[len(HOLD_MARK):]) if suffix else 0


def with_hold(binding: str, ms: int) -> str:
    """The same binding, held for `ms` - or not held at all when `ms` is 0.

    A hold is a *modifier on* a binding rather than a different binding, so adding one
    after the fact is editing this suffix and nothing else. Which is the point: a
    duration nobody can perform exactly is the part of the grammar an editor exists for.
    """
    text = str(binding or "").strip()
    bare = text[:-len(_hold_of(text))] if _hold_of(text) else text
    wanted = max(0, int(ms or 0))
    return f"{bare}{HOLD_MARK}{wanted}" if wanted else bare


def _hold_of(binding: str) -> str:
    """The `@hold:<ms>` suffix, or "" - the whole suffix, so it can be taken off."""
    text = str(binding or "")
    at = text.rfind(HOLD_MARK)
    return text[at:] if at != -1 and text[at + len(HOLD_MARK):].isdigit() else ""


def _held(said: str, binding: str) -> str:
    """"Esc, held 1.5s" - said in seconds, because a hold is something a person counts."""
    suffix = _hold_of(binding)
    if not suffix:
        return said
    ms = int(suffix[len(HOLD_MARK):])
    return t("input.held", binding=said, seconds=f"{ms / 1000:g}")


# `event.code` is what a browser reports and what is stored; the caption is only shown.
_KEY_WORDS = {
    "ArrowLeft": "input.key.arrow_left", "ArrowRight": "input.key.arrow_right",
    "ArrowUp": "input.key.arrow_up", "ArrowDown": "input.key.arrow_down",
    "ShiftLeft": "input.key.shift_left", "ShiftRight": "input.key.shift_right",
    "ControlLeft": "input.key.control_left", "ControlRight": "input.key.control_right",
    "AltLeft": "input.key.alt_left", "AltRight": "input.key.alt_right",
    "MetaLeft": "input.key.meta_left", "MetaRight": "input.key.meta_right",
    "PageUp": "input.key.page_up", "PageDown": "input.key.page_down",
    "Escape": "input.key.escape", "Enter": "input.key.enter", "Space": "input.key.space",
    "Tab": "input.key.tab", "Backspace": "input.key.backspace",
    "Delete": "input.key.delete", "Home": "input.key.home", "End": "input.key.end",
    "Insert": "input.key.insert", "Help": "input.key.help",
    "CapsLock": "input.key.caps_lock", "NumLock": "input.key.num_lock",
    "ScrollLock": "input.key.scroll_lock", "PrintScreen": "input.key.print_screen",
    "Pause": "input.key.pause", "ContextMenu": "input.key.context_menu",
    "Fn": "input.key.fn", "FnLock": "input.key.fn_lock",
    "IntlBackslash": "input.key.intl_backslash", "IntlRo": "input.key.intl_ro",
    "Convert": "input.key.convert", "NonConvert": "input.key.non_convert",
    "KanaMode": "input.key.kana_mode",
    "AudioVolumeUp": "input.key.volume_up", "AudioVolumeDown": "input.key.volume_down",
    "AudioVolumeMute": "input.key.volume_mute", "MediaPlayPause": "input.key.play_pause",
    "MediaTrackNext": "input.key.track_next",
    "MediaTrackPrevious": "input.key.track_previous", "MediaStop": "input.key.media_stop",
    "MediaSelect": "input.key.media_select", "Eject": "input.key.eject",
    "Power": "input.key.power", "Sleep": "input.key.sleep", "WakeUp": "input.key.wake_up",
    "BrowserBack": "input.key.browser_back", "BrowserForward": "input.key.browser_forward",
    "BrowserHome": "input.key.browser_home", "BrowserRefresh": "input.key.browser_refresh",
    "BrowserSearch": "input.key.browser_search", "BrowserStop": "input.key.browser_stop",
    "BrowserFavorites": "input.key.browser_favorites", "LaunchMail": "input.key.mail",
}
# A code names the key; a chip shows what is printed on it, in every language.
_KEY_SYMBOLS = {
    "Minus": "-", "Equal": "=", "BracketLeft": "[", "BracketRight": "]",
    "Backslash": "\\", "Semicolon": ";", "Quote": "'", "Comma": ",",
    "Period": ".", "Slash": "/", "Backquote": "`", "IntlYen": "¥",
}
_NUMPAD_SYMBOLS = {
    **{str(n): str(n) for n in range(10)},
    "Add": "+", "Subtract": "-", "Multiply": "*", "Divide": "/", "Decimal": ".",
    "Comma": ",", "Equal": "=", "ParenLeft": "(", "ParenRight": ")", "Hash": "#",
    "Star": "*", "MemoryAdd": "M+", "MemorySubtract": "M-", "MemoryClear": "MC",
    "MemoryRecall": "MR", "MemoryStore": "MS",
}
_NUMPAD_WORDS = {
    "Enter": "input.key.enter", "Backspace": "input.key.backspace",
    "Clear": "input.key.clear", "ClearEntry": "input.key.clear_entry",
}
_NUMBERED = {"Lang": ("input.key.lang", range(1, 6)),
             "LaunchApp": ("input.key.launch_app", range(1, 3))}


def key_names() -> dict[str, str]:
    """The printed names, for a surface that has to do this without Python.

    Handed over rather than reimplemented: a browser reading a gamepad cannot call
    `describe`, and a second copy of this table is a second thing to keep in step.
    """
    codes = [*_KEY_WORDS, *_KEY_SYMBOLS,
             *(f"Numpad{rest}" for rest in (*_NUMPAD_SYMBOLS, *_NUMPAD_WORDS)),
             *(f"{stem}{n}" for stem, (_, numbers) in _NUMBERED.items() for n in numbers)]
    return {code: _key_name(code) for code in codes}


def _key_name(code: str) -> str:
    if code in _KEY_WORDS:
        return t(_KEY_WORDS[code])
    if code in _KEY_SYMBOLS:
        return _KEY_SYMBOLS[code]
    if code.startswith("Key") and len(code) == 4:
        return code[3]
    if code.startswith("Digit") and len(code) == 6:
        return code[5]
    rest = code[len("Numpad"):] if code.startswith("Numpad") else ""
    if rest in _NUMPAD_SYMBOLS:
        return t("input.key.numpad", key=_NUMPAD_SYMBOLS[rest])
    if rest in _NUMPAD_WORDS:
        return t("input.key.numpad", key=t(_NUMPAD_WORDS[rest]))
    for stem, (key, numbers) in _NUMBERED.items():
        number = code[len(stem):]
        if code.startswith(stem) and number.isdigit() and int(number) in numbers:
            return t(key, number=number)
    # A function key's code is what is printed on it, and an unknown code is more use
    # shown than replaced with a guess.
    return code


CHORD_PREFIX = "chord("
HOLD_MARK = "@hold:"


def chord_members(binding: str) -> tuple[str, ...]:
    """What a chord is made of, in a settled order, or () for anything else.

    Sorted and de-duplicated because a chord is a *set* of inputs held together -
    `chord(a+b)` and `chord(b+a)` are one binding written two ways, and comparing them as
    text would call them different.

    The members are not looked at. The grammar's own example writes action names where
    selectors would also read, and settling which it is is not this function's to do;
    whichever it turns out to be, a chord is still its members.
    """
    text = str(binding or "").strip()
    if not text.startswith(CHORD_PREFIX) or ")" not in text:
        return ()
    inside = text[len(CHORD_PREFIX):text.rindex(")")]
    return tuple(sorted({part.strip() for part in inside.split("+") if part.strip()}))


# A key selector has two spellings, because a browser reports two things: `event.key` is
# what the key produces - "c" - and `event.code` is which key it is - "KeyC". The defaults
# were written in the first and capture stores the second, and both dispatch, so a config
# can hold `key:c` and `key:KeyC` for one key and nothing notices.
#
# The code wins. It survives a layout where the same physical key produces something else,
# which is the whole reason the browser reports both.
_PUNCTUATION_CODES = {
    "-": "Minus", "=": "Equal", "[": "BracketLeft", "]": "BracketRight",
    "\\": "Backslash", ";": "Semicolon", "'": "Quote", ",": "Comma",
    ".": "Period", "/": "Slash", "`": "Backquote", " ": "Space",
}


def normalize(binding: str) -> str:
    """One spelling for a key selector: the code a browser reports.

    Only a selector naming a single character is rewritten - `key:c` becomes `key:KeyC`.
    Anything already spelled as a code is left as it is, and so is a binding this does not
    understand, because guessing at one would invent a key nobody pressed.
    """
    text = str(binding or "").strip()
    if not text.startswith(KEY_PREFIX):
        return text
    name = text[len(KEY_PREFIX):]
    if len(name) != 1:
        return text
    if name.isalpha():
        return f"{KEY_PREFIX}Key{name.upper()}"
    if name.isdigit():
        return f"{KEY_PREFIX}Digit{name}"
    found = _PUNCTUATION_CODES.get(name)
    return f"{KEY_PREFIX}{found}" if found else text


def identity(binding: str) -> str:
    """The form two bindings are compared in.

    A chord written in either order is one binding, so comparison happens on the settled
    order rather than on what somebody typed. Everything else is already its own
    identity.

    **A chord does not fold into its members, and that is the decision rather than an
    oversight**: both flippers fire their own actions *and* the chord, so a chord and a
    plain binding on one of its members are two bindings that both work. What repeat
    does while a chord is held is dispatch's to answer.
    """
    text = str(binding or "").strip()
    members = chord_members(text)
    if not members:
        suffix = _hold_of(text)
        return f"{normalize(text[:-len(suffix)])}{suffix}" if suffix else normalize(text)
    suffix = text[text.rindex(")") + 1:]
    return f"{CHORD_PREFIX}{'+'.join(normalize(one) for one in members)}){suffix}"


def contention(binding: str) -> str:
    """The form two bindings are compared in to ask whether they want the same press.

    A key has one press and one hold, at whatever length, so `Esc` held 0.8s and `Esc`
    held 1.5s are one claim on the key where `identity` keeps them apart. A chord keeps
    its length: each chord waits on its own timer. Which controller a button is on does
    not count, and a button of the standard layout is the button of that number.
    """
    text = identity(binding)
    members = chord_members(text)
    if members:
        suffix = text[text.rindex(")") + 1:]
        return f"{CHORD_PREFIX}{'+'.join(sorted(_any_pad(one) for one in members))}){suffix}"
    suffix = _hold_of(text)
    if suffix:
        text = text[:-len(suffix)] + HOLD_MARK.rstrip(":")
    return _any_pad(text)


def holders(bound: Mapping[str, Iterable[str] | None] | None) -> dict[str, list[str]]:
    """Every binding, and which actions hold it, keyed by `contention`.

    A chord written in either order is one binding and a key written either way - `key:c`
    or `key:KeyC` - is one key. Dispatch matches on both of an event's tokens, so those
    two really are the same binding, and reading them as different leaves a clash sitting
    on the page unreported.
    """
    seen: dict[str, list[str]] = {}
    for name, bindings in dict(bound or {}).items():
        for binding in bindings or ():
            text = contention(binding)
            if not text:
                continue
            # An action listing the same binding twice holds it once.
            if name not in seen.setdefault(text, []):
                seen[text].append(name)
    return seen


def collisions(bound: Mapping[str, Iterable[str] | None] | None) -> dict[str, list[str]]:
    """Bindings more than one action holds.

    Dispatch resolves a key to the *first* action that lists it in declaration order, so
    the second binding is not a conflict a player is asked about - it is a binding that
    silently does nothing, on a settings page with no sign of it.
    """
    return {binding: names for binding, names in holders(bound).items()
            if len(names) > 1}


def _any_pad(selector: str) -> str:
    """A pad selector with which pad dropped.

    Dispatch ignores the index, and a numbered binding fires on a pad in the standard
    layout as well as the standard one does, so `pad:0/button:3`, `pad:1/button:3` and
    `pad:standard/button:3` all want the same press.
    """
    head, slash, rest = selector.partition("/")
    which = head[len(PAD_PREFIX):] if head.startswith(PAD_PREFIX) else ""
    layout = which.isdigit() or head == STANDARD_PAD
    return f"{PAD_PREFIX}*/{rest}" if slash and layout else selector


def press(binding: str) -> str:
    """What has to go down for a binding, however long it is then held.

    The key a chip and a row are matched to a press by: `key:Escape` and
    `key:Escape@hold:1500` are one press, and `pad:0/button:3` is the same press on any
    pad, or in the standard layout.
    """
    text = with_hold(identity(binding), 0)
    members = chord_members(text)
    if members:
        return f"{CHORD_PREFIX}{'+'.join(sorted(_any_pad(one) for one in members))})"
    return _any_pad(text)


def replaced(bindings: Iterable[str], old: str, new: str) -> list[str]:
    """`old` swapped for `new` where it stands, and any other binding `new` competes with
    dropped - so changing how long a key is held never leaves it held two ways."""
    return [new if one == old else one for one in bindings
            if one == old or contention(one) != contention(new)]


def without(bindings: Iterable[str], taken: str) -> list[str]:
    """What is left of `bindings` once `taken`, at whatever length, is somebody else's."""
    return [one for one in bindings if contention(one) != contention(taken)]


# The actions that step along a list while their input is kept down. The frontend's
# `REPEATING_ACTIONS` names the same four.
SCROLLING = ("previous", "next", "page_previous", "page_next")


class Stall(NamedTuple):
    """A scrolling action's press that moves one step at a time instead of repeating."""

    action: str
    binding: str
    holder: str
    hold: str


def stalled(bound: Mapping[str, Iterable[str] | None]) -> list[Stall]:
    """Each scrolling press that shares its input with a hold.

    A key with a hold acts on release, and an action on release cannot repeat, so a
    scrolling action on that key steps once per tap. A chord keeps its own timer and is
    left out.
    """
    held = [(name, one) for name, bindings in dict(bound or {}).items()
            for one in bindings or () if hold_ms(one) and not chord_members(one)]
    out = []
    for action in SCROLLING:
        for one in (bound.get(action) or ()):
            if hold_ms(one) or chord_members(one):
                continue
            out += [Stall(action, one, holder, hold) for holder, hold in held
                    if _unheld(hold) == _unheld(one)]
    return out


def newly_stalled(was: Mapping[str, Iterable[str] | None],
                  now: Mapping[str, Iterable[str] | None]) -> list[Stall]:
    """Only what `now` stalls that `was` did not, so a change is told what it costs."""
    before = {(one.action, _unheld(one.binding)) for one in stalled(was)}
    return [one for one in stalled(now) if (one.action, _unheld(one.binding)) not in before]


# What a cabinet's own buttons stand for, by the role an app names them under: the action,
# the role, and the shipped binding the button replaces. The key a cabinet is wired to is
# the app's say, so the shipped key is only a guess at it. Tutorial is absent on purpose -
# the tilt bob is T there.
CABINET_ROLES: dict[str, tuple[str, str]] = {
    "previous": ("left_flipper", "key:ShiftLeft"),
    "next": ("right_flipper", "key:ShiftRight"),
    "page_previous": ("left_magna", ""),
    "page_next": ("right_magna", ""),
    "select": ("start", ""),
    "menu": ("fire", ""),
    "collection_menu": ("extra_ball", ""),
    "back": ("exit", "key:Escape"),
    "exit": ("exit", f"key:Escape@hold:{QUIT_HOLD_MS}"),
}
# Buttons the app plays with that no action stands in for. Their keys belong to the app.
CABINET_AWAY = ("tilt", "extra_ball")
CABINET_NONE = ("tutorial",)


def _keys_of(bindings: Iterable[str]) -> list[str]:
    return [b for b in bindings if str(b).startswith(KEY_PREFIX)]


def _unheld(binding: str) -> str:
    return contention(with_hold(binding, 0))


def cabinet_bindings(stored: Mapping[str, Iterable[str]],
                     buttons: Mapping[str, Iterable[str]]) -> dict[str, list[str]]:
    """What a cabinet's actions hold, for the actions nobody has changed.

    `buttons` is the app's cabinet by role, each as the key codes it sends. An action whose
    keys differ from the shipped ones is the player's and is left as it is, and so are the
    keys it holds: a button key another action already has stays there. Controller bindings
    are never touched. Only an action that changes is returned.
    """
    shipped = defaults()
    held = {name: [str(one) for one in bindings] for name, bindings in stored.items()}
    untouched = [name for name in shipped if name in held and same_bindings(
        _keys_of(held[name]), _keys_of(shipped[name]))]
    chosen = {contention(b) for name, bindings in held.items() if name not in untouched
              for b in _keys_of(bindings)}
    away = {_unheld(f"{KEY_PREFIX}{code}") for role in CABINET_AWAY
            for code in buttons.get(role, ())}

    placed: dict[str, list[str]] = {}
    for name in untouched:
        role, replaces = CABINET_ROLES.get(name, ("", ""))
        sending = (with_hold(f"{KEY_PREFIX}{code}", hold_ms(replaces))
                   for code in buttons.get(role, ()))
        placed[name] = [one for one in sending if contention(one) not in chosen]
    claimed = {contention(one): name for name, found in placed.items() for one in found}

    out: dict[str, list[str]] = {}
    for name in untouched:
        _, replaces = CABINET_ROLES.get(name, ("", ""))
        kept = [] if name in CABINET_NONE else [
            one for one in _keys_of(shipped[name])
            if identity(one) != identity(replaces) and _unheld(one) not in away
            and claimed.get(contention(one), name) == name]
        keys: list[str] = []
        for one in [*placed[name], *kept]:
            if identity(one) not in {identity(k) for k in keys}:
                keys.append(one)
        rest = [b for b in held[name] if not str(b).startswith(KEY_PREFIX)]
        if same_bindings(keys, _keys_of(held[name])):
            continue
        out[name] = [*keys, *rest]
    return out


def unrenderable(bindings: Iterable[str] | None) -> list[str]:
    """Bindings neither UI field can show - chords, holds, axes, a second pad.

    Kept and written back untouched: dropping them would delete a cabinet's
    hold-both-flippers binding the first time anyone opened settings and pressed Save.
    """
    shown = set(f"{KEY_PREFIX}{k}" for k in keys_in(bindings))
    shown |= set(f"{PAD_PREFIX}0/button:{b}" for b in pad_buttons_in(bindings))
    return [str(b) for b in bindings or () if str(b) not in shown]
