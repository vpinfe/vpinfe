"""What an app must answer to play an entry.

An app is the program that plays something; a launcher is a configured wrapper around
one and holds the values for the fields the app declares. This module is the whole of
the boundary: an implementation under `apps/` imports it and nothing else of ours.

Seven capability groups. `claim` and `kinds` are declarations every app makes; `format`,
`launch`, `config`, `capability` and `capture` are behavior, and None is a real answer for
each. Consumers ask before they call.

Nothing here takes or returns a VPinFE object, and nothing holds a word. An app's words
are in `i18n/<language>.json` beside it, found by what the app declares: `name`,
`field.<key>.label`, `field.<key>.description`, `field.<key>.help`, `group.<key>.label`,
`group.<key>.heading.<heading>.label` and `.note`, and `group.<key>.pair.<pair>.label`,
`.note` and `.joiner`, and for each setting an upgrade leaves behind `leftover.<word>`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# How an app fills `{name}` in an argument a person wrote: the one token syntax.
from common.tokens import fill as fill_tokens  # noqa: F401


@dataclass(frozen=True)
class Availability:
    available: bool
    # A key in the app's own catalog, required for a no: "unavailable" alone leaves a
    # person nothing to act on.
    reason: str = ""


@dataclass(frozen=True)
class Field:
    """One thing configuring an app takes, shaped like `config_schema.ConfigOption`
    minus the section so a generated editor looks like every settings page."""

    key: str
    # Empty is looked up. Set, it is shown as written, for words the program supplies.
    label: str = ""
    type: str = "string"
    default: str = ""
    description: str = ""
    # `(value, label)` where the answers are a closed set. Stored value first.
    choices: tuple[tuple[str, str], ...] = ()
    # How many rows a `text` field gets; one line otherwise.
    lines: int = 0
    minimum: float | None = None
    maximum: float | None = None
    # "file", "dir" or "exe" where this names something on disk. Declared rather than
    # guessed from the key: `bin_path` and `ini_path` end the same way and want
    # different answers.
    path: str = ""
    # For a `path="file"` field, which suffixes it may be. Empty means every file.
    suffixes: tuple[str, ...] = ()
    # Commonly set for one table rather than for all of them, so offered first there.
    per_table: bool = False


@dataclass(frozen=True)
class Entry:
    """One playable thing, as the app sees it.

    Three forms, and the form is read off what is set rather than stored: a `table`
    inside `game_dir` is contained, a `table` outside it is referenced, and a `key` with
    no file is keyed.
    """

    entry_id: str = ""
    # The folder. The launchable artifact inside it is the table.
    game_dir: str = ""
    # Absolute, and empty for a keyed entry.
    table: str = ""
    # The app's own native handle - a ROM name, a store id. Empty for a file-backed entry.
    key: str = ""


# --- claim ------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """Which files and keys belong to this app. Suffixes are lowercase, with the dot."""

    suffixes: tuple[str, ...] = ()
    # Whether this app can play an entry that has no file at all.
    accepts_keys: bool = False
    # What travels with one of its tables, sharing the table's stem: a backglass, a
    # patched script, a point of view. Declared by the app because it is the app that
    # knows - a generic list here would be one program's habits taught to everything.
    companions: tuple[str, ...] = ()

    def claims(self, filename: str) -> bool:
        lowered = str(filename or "").lower()
        return any(lowered.endswith(suffix) for suffix in self.suffixes)

    def strip_suffix(self, filename: str) -> str:
        """Not `Path.stem`: "Foo (Bar 1.2).ext" keeps everything up to an extension we
        know, and only that."""
        name = str(filename or "")
        lowered = name.lower()
        found = next((s for s in self.suffixes if lowered.endswith(s)), "")
        return name[: -len(found)] if found else name


# --- format -----------------------------------------------------------------------


@dataclass(frozen=True)
class Parsed:
    """What reading one entry told us. What core does not model goes in `extra` under
    the app's own name, and nothing else reads it."""

    entry_id: str = ""
    title: str = ""
    version: str = ""
    author: str = ""
    release_date: str = ""
    manufacturer: str = ""
    year: str = ""
    # What the entry declares it needs. Whether it is installed is a machine fact,
    # answered by `capability`.
    rom: str = ""
    detects_emulator: bool | None = None
    file_hash: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)
    # Parsing a library must not stop at the first damaged file.
    error: str = ""


@runtime_checkable
class Format(Protocol):
    """`parse` takes a list and answers one per entry in order. Core calls it across a
    threaded library scan, and a per-entry call across a boundary gives that back."""

    def parse(self, entries: Sequence[Entry]) -> list[Parsed]: ...

    def script(self, entry: Entry, settings: Mapping[str, Any]) -> str: ...


# --- launch -----------------------------------------------------------------------

# How the end of a session is known. `NONE` is an answer rather than a gap: play stats
# have to say "we cannot tell when this ended" instead of recording three seconds.
SESSION_CHILD = "child"
SESSION_CHILD_WITH_READINESS = "child_with_readiness"
SESSION_DETACHED = "detached"
SESSION_NONE = "none"


@dataclass(frozen=True)
class Session:
    kind: str = SESSION_CHILD
    # For `child_with_readiness`: what the app writes once it is actually up.
    readiness_marker: str = ""


@runtime_checkable
class Launch(Protocol):
    """argv, never a shell string: substituting a path into a string that is then split
    is how a filename with a space becomes a crash or an injection."""

    def command(self, entry: Entry, settings: Mapping[str, Any]) -> list[str]: ...

    def session(self, settings: Mapping[str, Any]) -> Session: ...


# --- config -----------------------------------------------------------------------

# Where a value is written. A person reads these as "everything this launcher plays",
# "this folder" and "this table"; which file is behind each is the app's business.
SCOPE_LAUNCHER = "launcher"
SCOPE_FOLDER = "folder"
SCOPE_ENTRY = "entry"


@dataclass(frozen=True)
class Pair:
    """Two of a heading's rows drawn as one, in this order: an X and a Y, a width and a
    height."""

    key: str
    keys: tuple[str, str]


@dataclass(frozen=True)
class Switched:
    """Rows of a heading drawn only while `enabled_by`, another of its rows, is on - or is
    off, where `on` is false."""

    enabled_by: str
    keys: tuple[str, ...]
    on: bool = True


@dataclass(frozen=True)
class Heading:
    """Curated rows under one sub-heading of a group. Without a label they are headed by
    the group's own."""

    key: str = ""
    keys: tuple[str, ...] = ()
    # One of `keys`, a switch: while it is off, the heading's other rows change nothing
    # and are not drawn.
    enabled_by: str = ""
    # The program's own words for what the heading names, as `Field` carries them.
    label: str = ""
    description: str = ""
    # Other headings' switches: while this one's and a rival's are both on, the program
    # uses only one of them.
    rivals: tuple[str, ...] = ()
    # Each pair's keys are two of `keys`.
    pairs: tuple[Pair, ...] = ()
    # Each one's switch and rows are among `keys`.
    switched: tuple[Switched, ...] = ()
    # Core's asset kinds these settings are about. A file of one of them draws the
    # heading too, at the table the file is for.
    kinds: tuple[str, ...] = ()
    # Rows of these settings a file draws only when it holds two versions of its content.
    # Not among `keys`, so a table's settings page never draws them.
    versions: tuple[str, ...] = ()
    # Names a second note in the app's catalog, `heading.<key>.note.<variant>`, drawn in
    # place of the heading's own where the program is of a build that needs it.
    variant: str = ""


@dataclass(frozen=True)
class ConfigGroup:
    """Declared rather than derived from the sections an app's file happens to have: a
    group can span sections and a section can feed two groups.

    `settings` is every member. `curated` names the few drawn first; the rest are found
    by search.
    """

    key: str
    label: str = ""
    settings: tuple[Field, ...] = ()
    curated: tuple[Heading, ...] = ()
    # Said as one line - whether anything in it is set - and never listed key by key.
    summarized: bool = False
    # Listed as the file holds them, each removable, never edited: settings whose range
    # and meaning only something outside the program's own ini declares.
    read_only: bool = False


@dataclass(frozen=True)
class ConfigValue:
    """One setting at a scope: what the app will use, and which layer answered.

    Four facts rather than a value, because somebody editing one layer of several has to
    see which layer is answering.
    """

    value: str = ""
    # Empty where nothing has set it and the app's own default wins.
    scope: str = ""
    set_here: bool = False
    # False with `set_here` true is a value another layer is shadowing - invisible
    # otherwise, and the bug report we would get.
    in_effect: bool = True
    # What would win if this scope stopped naming it, and which layer would supply it.
    # Clearing a value has to be able to say what it will follow instead, or somebody
    # has to change it to find out.
    fallback: str = ""
    fallback_scope: str = ""


@dataclass(frozen=True)
class Standing:
    """What a launcher field left empty stands for on this machine.

    `path` is "" where the app cannot say where it would be. `exists` is False for a path
    the app will make the first time it runs, which a launch cannot use yet.
    """

    path: str = ""
    exists: bool = False


@dataclass(frozen=True)
class Program:
    """What an app's installed program is, as far as the settings it reads depend on it.

    `plugins` are the ids of the plugins the program loads, empty for a program that has
    none and None where that cannot be read. `defaults` is by key what a setting holds
    while the file leaves it blank, for the settings whose default is the build's. `build`
    is the number its app counts builds by, None where unknown. `dmd_in_file` says the
    build keeps the DMD's place in each backglass file rather than in the settings.
    """

    plugins: frozenset[str] | None = None
    defaults: Mapping[str, str] = field(default_factory=dict)
    build: int | None = None
    dmd_in_file: bool = False


MOVE, PLACE, NAME, TURN_ON = "move", "place", "name", "turn_on"


@dataclass(frozen=True)
class Leftover:
    """One setting an upgrade of the program left behind, and what offers to be done with it.

    `key` names it, and stays the same while it is there: the setting itself, or for a
    `PLACE` the prefix of the box it holds. `word` names what it is in the app's catalog,
    as `leftover.<word>`.

    `drops` are the keys it blanks, each with the value it holds, so a move can be undone.
    `MOVE` writes `new_value` at `to` and blanks `drops`. `PLACE` sets the box in each file
    the program keeps it in, then blanks `drops`: `box` is x, y, width and height as the
    settings count them, with y up from the bottom of the window, in the pixels of the art
    or, with `frame`, of a window that size; `target` names the window. `cut_if` names the
    setting that, while on as it stands before the move, means the box was counted on the
    art without its grill; without it the grill is cut as the program in effect cuts it.
    `TURN_ON` writes
    `new_value` at `to` and only on request. `NAME` is said and left.
    """

    key: str
    action: str
    word: str
    value: str = ""
    to: str = ""
    new_value: str = ""
    drops: tuple[tuple[str, str], ...] = ()
    target: str = ""
    box: tuple[int, int, int, int] | None = None
    frame: tuple[int, int] | None = None
    cut_if: str = ""


@dataclass(frozen=True)
class TableOrder:
    """What one table's own settings should hold for the order its backglass and score
    display draw in.

    `write` is the values to put in the table's settings, a blank taking a key out. `ours` is
    what the table holds of them once that is done, as an app can tell a pair it set from one
    somebody else did: the caller keeps it, and hands it back as `written` the next time. An
    empty `ours` says there is none to keep; None says the app could not tell, and what was
    kept stands.
    """

    write: dict[str, str] = field(default_factory=dict)
    ours: dict[str, str] | None = None


@runtime_checkable
class Config(Protocol):
    """An app's own configuration, and where it keeps it.

    `files` names them by identity; copying one somewhere safe is core's, which owns
    moving files for every kind already. Only the app knows which file its settings are
    in, and only core should be deciding where a copy of one goes.

    `write` returns the keys it cleared instead of writing, for holding the launcher's
    own value.
    """

    def groups(self, settings: Mapping[str, Any]) -> tuple[ConfigGroup, ...]: ...

    def scopes(self) -> tuple[str, ...]: ...

    def read(self, scope: str, target: str,
             settings: Mapping[str, Any]) -> dict[str, ConfigValue]: ...

    def write(self, scope: str, target: str, values: Mapping[str, str],
              settings: Mapping[str, Any]) -> frozenset[str]: ...

    def files(self, settings: Mapping[str, Any]) -> dict[str, str]: ...


# --- kinds ------------------------------------------------------------------------


@dataclass(frozen=True)
class Kinds:
    """Which of core's kinds mean anything for this app's entries. An app narrows the
    set and never adds to it; None means all."""

    applicable: frozenset[str] | None = None

    def applies(self, kind: str) -> bool:
        return True if self.applicable is None else str(kind or "") in self.applicable


# --- capability -------------------------------------------------------------------


@runtime_checkable
class Capability(Protocol):
    """What this app can do on this machine right now, keyed by capability name. Probed
    from evidence: a version test that two builds eighteen months apart both satisfy
    answers nothing.

    Asked only once the program is there. Whether it is, core says for every field that
    names a path."""

    def probe(self, settings: Mapping[str, Any]) -> Mapping[str, Availability]: ...


# --- capture ----------------------------------------------------------------------


@runtime_checkable
class Capture(Protocol):
    """How an entry is launched to have its screens recorded rather than to be played.

    `command` answers in place of `Launch.command`; `Launch.session` still says when it
    is up. `folder` is empty, the app's to write into, and removed with everything in it
    once the entry closes. `sound` is whether the entry's sound is being recorded.

    Windows are named as core names them: `playfield`, `backglass`, `scoreview` and
    `topper`. `outputs` answers, per window, the output the app's own settings show it on,
    by the name the desktop gives the output (`DP-1`), or "" where the app shows that
    window on no screen of its own; a window it cannot say is left out. `window` answers
    which window a desktop window is, from its app id and its title, or "". `titles` answers
    the other way for a desktop that places a window by its title: each window by the title
    the app gives it.

    `placed_by` answers what the app reads to decide which windows it shows for the entry,
    as one string that stays the same until any of it changes, or "" where it cannot say.
    """

    def command(self, entry: Entry, settings: Mapping[str, Any], *, sound: bool,
                folder: str) -> list[str]: ...

    def outputs(self, entry: Entry, settings: Mapping[str, Any]) -> Mapping[str, str]: ...

    def window(self, app_id: str, title: str) -> str: ...

    def titles(self) -> Mapping[str, str]: ...

    def placed_by(self, entry: Entry, settings: Mapping[str, Any]) -> str: ...


# --- pausing ----------------------------------------------------------------------


@runtime_checkable
class Pause(Protocol):
    """How a running entry pauses and resumes itself: the key it listens for, named as a
    binding names one (`KeyP`), and the lines it writes once it has done either. `key`
    answers "" where it has none."""

    paused_marker: str
    resumed_marker: str

    def key(self, settings: Mapping[str, Any]) -> str: ...


# --- cabinet buttons ----------------------------------------------------------------


@runtime_checkable
class Buttons(Protocol):
    """The buttons of a cabinet the app is wired for, by role, as the key codes they send.

    The roles are `left_flipper`, `right_flipper`, `left_magna`, `right_magna`, `start`,
    `fire`, `extra_ball`, `tilt` and `exit`. A role the app has no key for is left out.
    """

    def keys(self, settings: Mapping[str, Any]) -> Mapping[str, tuple[str, ...]]: ...


# --- table controls -----------------------------------------------------------------

CONTROLS_PLAY = "play"
CONTROLS_GAME = "game"
CONTROLS_COINS = "coins"
CONTROLS_SOUND = "sound"
CONTROLS_NUDGE = "nudge"
CONTROLS_MENU = "menu"
CONTROLS_SERVICE = "service"
CONTROLS_DEBUG = "debug"

SIDE_LEFT = "left"
SIDE_RIGHT = "right"

# Pausing and resuming are the same press, so one id covers both.
PAUSE = "pause"

# Why a control has no key to press: the app's settings map it to nothing, or to
# something that is not one key.
UNMAPPED = "unmapped"
NOT_A_KEY = "not_a_key"


@dataclass(frozen=True)
class Control:
    """One thing a person can press on a running entry from somewhere else.

    `id` is the app's own word and travels over the API; its label is the install's
    catalog `controls.<id>`. `hold_ms` is how long the install holds the key for a tap,
    never the caller. `holdable` lets a caller keep the key down for as long as it says,
    with a press and a release. `side` is `SIDE_LEFT` or `SIDE_RIGHT` for a control that
    belongs to one hand, and "" for the middle; a caller lays a group out by it when any
    of the group's controls has one. `when_mapped` leaves the control out of what is
    listed, and out of the notes, where the app has no key one press can send for it;
    pressing it is still refused with the reason.
    """

    id: str
    group: str
    hold_ms: int = 0
    when_mapped: bool = False
    holdable: bool = False
    side: str = ""


@runtime_checkable
class Controls(Protocol):
    """What the app lets be pressed on a running entry, and the key behind each.

    `key` answers "" where there is none, and `why_not` then says which of `UNMAPPED` or
    `NOT_A_KEY`. A control is looked up at the press, so a settings file changed while the
    entry runs is the one that counts. No key code ever leaves an app.
    """

    def offered(self) -> tuple[Control, ...]: ...

    def key(self, control_id: str, settings: Mapping[str, Any]) -> str: ...

    def why_not(self, control_id: str, settings: Mapping[str, Any]) -> str: ...


# --- the app ----------------------------------------------------------------------


@dataclass(frozen=True)
class App:
    """`fields` is what a launcher of this app holds. `config` is the app's own
    settings surface, which is a different thing."""

    id: str
    # Empty is looked up. Set, it is a product name.
    name: str = ""
    claim: Claim = Claim()
    # Which of VPS's `tableFormat` values this app plays: VPX, VP9, FP, FX, FX2, FX3 or PC.
    # A tuple because one program can play several. Empty filters nothing - every release
    # is a candidate for a table it plays.
    vps_formats: tuple[str, ...] = ()
    fields: tuple[Field, ...] = ()
    kinds: Kinds = Kinds()
    format: Format | None = None
    launch: Launch | None = None
    config: Config | None = None
    capability: Capability | None = None
    capture: Capture | None = None
    pause: Pause | None = None
    buttons: Buttons | None = None
    controls: Controls | None = None
