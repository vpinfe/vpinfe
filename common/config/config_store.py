"""VPinFE's settings, stored as JSON and read from an ini once.

The file is `vpinfe.json`; a `vpinfe.ini` beside it is read on the first run that finds
one, converted, and kept. Every other config file VPinFE owns is already JSON, and this
was the only one that was both hand-edited and machine-written - which is exactly why it
was the only one whose comments `configparser.write()` destroyed.

In memory it is still a ConfigParser, so nothing above this module changed. What is on
disk carries real booleans and integers, and a schema version.
"""

from __future__ import annotations

import configparser
import copy
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from common.atomic_write import write_atomic
from common.config import config_schema, input_registry
from common.deprecations import announce
from common.i18n import t
from common.ids import new_id
from common.kept_copies import REASON_2X, copy_aside
from common.values import is_truthy

logger = logging.getLogger("vpinfe.common.config.config_store")

SCHEMA_KEY = "schema"
SETTINGS_KEY = "settings"

# Generations of the settings file, counting from the first one that shipped.
#   1  the ini. Implied - it is recognized by being an ini, not by anything written in
#      it, so a schema key never appears in one.
#   2  JSON. Keys are snake_case and each window has a section of its own.
#
# The JSON side counted 1, 2, 3 during 3.0 development, one bump per shape change while
# the format was being designed. None of those shipped, so they collapse into the single
# generation users will actually receive.
CONFIG_SCHEMA = 2

# (from, to, key) for options that changed section. Applied on every read, so an ini
# written by any earlier build lands in the right place.
_MOVED_OPTIONS = (
    # What the frontend shows, not what a screen is. Two hops because a 3.0 build already
    # relocated it once, and the more recent location is listed first: `_move_option`
    # keeps what it finds at the destination, so whichever runs first wins.
    ('Displays', 'presentation', 'cabmode'),
    ('Settings', 'presentation', 'cabmode'),
    ('Settings', 'DOF', 'enabledof'),
    ('Displays', 'Settings', 'splashscreen'),
    # `input` is which button does what; how far a press moves the wheel is what the
    # frontend does when one is pressed. Moved rather than declared afresh: a file
    # already holding these keeps its values, and the old entries go rather than
    # lingering as a second copy the settings page would render beside the new one.
    ('input', 'behavior', 'paging_group'),
    ('input', 'behavior', 'paging_size'),
    ('lifecycle', 'behavior', 'confirm'),
)


# The values that changed vocabulary, not just the key that holds them. Renaming a key
# is handled below; renaming what it may contain was not, so a file kept the retired word
# and the schema's own choices said otherwise. One entry today - `paging_group` is the
# only choice option of twelve whose values moved - so this is a lookup, not a framework.
_RENAMED_VALUES = {
    ('behavior', 'paging_group'): config_schema.PAGING_GROUP_ALIASES,
}

# The same, for a setting that holds several values at once. Separate because a list is
# rewritten item by item and a choice is replaced whole, and because an unmigrated item
# here is not a stale spelling that still resolves - `roles` is filtered against a known
# set, so a retired word is dropped and the install stops claiming what it dropped.
# A retired word inside a list. `player` named the install that launches games before
# `device` did; both are now the `frontend` feature, which the roles migration below
# expands - this pass only normalizes the spelling first so one map handles it.
_RENAMED_LIST_VALUES = {
    ('device', 'roles'): {'player': 'device'},
}


# (section, old key, new key) - see the migration in _migrate below.
_RENAMED_KEYS = (
    ('Displays', 'tablescreenid', 'playfieldscreenid'),
    ('Displays', 'tableorientation', 'playfieldorientation'),
    ('Displays', 'tablerotation', 'playfieldrotation'),
    ('Settings', 'tablerootdir', 'gamerootdir'),
    ('Media', 'tabletype', 'playfieldvariant'),
    ('Media', 'tableresolution', 'playfieldresolution'),
    ('Media', 'tablevideoresolution', 'playfieldvideoresolution'),
    ('Media', 'tablemediapriority', 'playfieldmediapriority'),
    # `console` is what 2.x called terminal logging; `terminal` says which of the two it means.
    ('logger', 'console', 'terminal'),
)

def _nest(sections: dict) -> dict:
    """`windows.playfield` becomes a playfield object inside a windows object.

    A ConfigParser section is a flat string, so the hierarchy is spelled with dots in
    memory and is real on disk - which is the point of giving each window a section.
    """
    out: dict = {}
    for name, values in sections.items():
        node = out
        parts = name.split('.')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = values
    return out


def _flatten(tree: dict, prefix: str = '') -> dict:
    """The inverse of _nest: nested objects back to dotted section names."""
    out: dict = {}
    for name, value in (tree or {}).items():
        path = f"{prefix}.{name}" if prefix else name
        if isinstance(value, dict) and any(isinstance(v, dict) for v in value.values()):
            out.update(_flatten(value, path))
        else:
            out[path] = value
    return out


_SAVING = threading.Lock()
_Sections = tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
_PARSED: dict[Any, tuple[tuple[int, int, int], tuple[int, _Sections, dict]]] = {}


def _by_id(items: Any) -> dict[str, dict] | None:
    """A list of objects that each carry a distinct `id`, keyed by it; anything else is None."""
    if not isinstance(items, list):
        return None
    keyed: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            return None
        keyed[item["id"]] = item
    return keyed if len(keyed) == len(items) else None


def _same(a: Any, b: Any) -> bool:
    """JSON equality: unlike `==`, 1 and True differ."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _merged_list(ours: Any, theirs: Any, saved: Any) -> Any:
    """Per item: ours where it differs from `saved` (a removal too), else the file's."""
    mine, file, base = _by_id(ours), _by_id(theirs), _by_id(saved if saved is not None else [])
    if mine is None or file is None or base is None:
        return ours
    merged: dict[str, dict] = {}
    for item_id in {*mine, *file, *base}:
        taken = mine.get(item_id) if not _same(mine.get(item_id), base.get(item_id)) \
            else file.get(item_id)
        if taken is not None:
            merged[item_id] = taken
    ours_order = [i for i in mine if i in base]
    reordered = ours_order != [i for i in base if i in mine]
    first, second = (mine, file) if reordered else (file, mine)
    order = [*(i for i in first if i in merged),
             *(i for i in second if i in merged and i not in first)]
    return [merged[i] for i in order]


def _merged_map(ours: dict, theirs: dict, saved: Any) -> dict:
    """Per entry: ours where it differs from `saved` (a removal too), else the file's."""
    base = saved if isinstance(saved, dict) else {}
    merged: dict = {}
    for name in dict.fromkeys([*theirs, *ours]):
        taken = ours.get(name) if not _same(ours.get(name), base.get(name)) \
            else theirs.get(name)
        if taken is not None:
            merged[name] = taken
    return merged


def _merged_top_level(ours: dict, theirs: dict, saved: dict) -> dict:
    absent = object()
    out: dict = {}
    for key in dict.fromkeys([*theirs, *ours]):
        if (key in ours) == (key in saved) and _same(ours.get(key), saved.get(key)):
            value = theirs.get(key, absent)
        elif key == config_schema.THEME_OPTIONS and isinstance(ours.get(key), dict) \
                and isinstance(theirs.get(key), dict):
            value = _merged_map(ours[key], theirs[key], saved.get(key))
        elif key in ours and key in theirs:
            value = _merged_list(ours[key], theirs[key], saved.get(key))
        else:
            value = ours.get(key, absent)
        if value is not absent:
            out[key] = value
    return out


def _options(parser: configparser.ConfigParser) -> dict[tuple[str, str], str]:
    return {(section, key): value for section in parser.sections()
            for key, value in parser.items(section, raw=True)}


class ConfigStore:
    """The settings file, read once and written atomically."""

    def _move_option(self, old_section: str, new_section: str, key: str) -> bool:
        """Move one option to the section it lives in now, keeping the user's value.

        The destination section may not exist yet: this runs before the defaults are
        filled in, and filling them in is what creates sections.
        """
        if not self.config.has_option(old_section, key):
            return False
        announce('ini-moved-options', f'{old_section}.{key}')
        if not self.config.has_section(new_section):
            self.config.add_section(new_section)
        if not self.config.has_option(new_section, key):
            self.config.set(new_section, key, self.config.get(old_section, key))
        self.config.remove_option(old_section, key)
        return True

    def __init__(self, configfilepath: str | Path) -> None:

        self.defaults = config_schema.defaults()

        # No interpolation: a value is whatever was typed, and a `%` in it is not a reference.
        self.config = configparser.ConfigParser(interpolation=None)
        # Callers pass whichever name they know; both are derived so none had to change.
        base = Path(configfilepath)
        self.json_path = base.with_suffix('.json')
        self.ini_path = base.with_suffix('.ini')
        self.configfilepath = str(self.json_path)
        self._schema = CONFIG_SCHEMA
        self._converted_from_ini = False
        self._saved: dict[tuple[str, str], str] | None = None
        # Every top-level key beside `schema` and `settings`, written back as held.
        self.top_level: dict[str, Any] = {}
        self._saved_top: dict[str, Any] = {}

        if os.path.exists(self.json_path):
            self._load_json()
        elif os.path.exists(self.ini_path):
            logger.info("Converting %s to %s", self.ini_path, self.json_path)
            self.config.read(self.ini_path)
            # The old parser needed a literal % written as %%; it is one % now.
            for section in self.config.sections():
                for option, value in self.config.items(section):
                    if "%%" in value:
                        self.config.set(section, option, value.replace("%%", "%"))
            self._converted_from_ini = True
        else:
            logger.info("Generating default settings at: %s", self.json_path)
            self.format_defaults()
            # Where a new install starts, as against an upgrade, which takes the schema's
            # defaults: the Manager follows the device's light or dark rather than
            # opening in the one palette that no operating system can ask for.
            self.config.set('manager', 'appearance', 'system')
            self.config.set('device', 'setup_pending', 'true')
            self.save()

        changed = self._converted_from_ini

        # Both of these run BEFORE the defaults are filled in. Each copies only when the
        # target key is absent, and these keys have defaults - so with a default already
        # written the guard finds one, copies nothing, and remove_option then drops what
        # the user actually set. cabmode and enabledof shipped doing exactly that.

        # A table folder is a game, and the table screen is the playfield. Read the old
        # key once and write the new one, so an existing vpinfe.ini is corrected in place.
        for section, old, new in _RENAMED_KEYS:
            if self.config.has_option(section, old):
                announce('ini-renamed-keys', old)
                if not self.config.has_option(section, new):
                    self.config.set(section, new, self.config.get(section, old))
                self.config.remove_option(section, old)
                changed = True

        # `disabledefaultchromeoptions` said when the defaults were off and `chromium.defaults`
        # says when they are on, so the value is inverted on the way, not just moved.
        for section in ('Settings', 'general'):
            if not self.config.has_option(section, 'disabledefaultchromeoptions'):
                continue
            announce('ini-renamed-keys', 'disabledefaultchromeoptions')
            if not self.config.has_option('chromium', 'defaults'):
                if not self.config.has_section('chromium'):
                    self.config.add_section('chromium')
                off = is_truthy(self.config.get(section, 'disabledefaultchromeoptions'))
                self.config.set('chromium', 'defaults', 'false' if off else 'true')
            self.config.remove_option(section, 'disabledefaultchromeoptions')
            changed = True

        # Options that changed section rather than name.
        for old_section, new_section, key in _MOVED_OPTIONS:
            changed |= self._move_option(old_section, new_section, key)

        # [Input] is a merge, not a move: an action had one key per input, and both
        # become entries in its single binding list, so the generic pass below - which
        # moves one value at a time - would have the second overwrite the first.
        if self.config.has_section('Input'):
            reads: dict[str, list[tuple[str, str]]] = {}
            for action in input_registry.actions():
                read = []
                for old in action.legacy:
                    if not self.config.has_option('Input', old):
                        continue
                    read.append((old, self.config.get('Input', old)))
                    self.config.remove_option('Input', old)
                    changed = True
                reads[action.name] = read
            chosen = {name: input_registry.chosen_in_legacy(read) for name, read in reads.items()}
            for action in input_registry.actions():
                taken = [b for name, found in chosen.items() if name != action.name for b in found]
                read = reads[action.name]
                found = input_registry.from_legacy(action, read, taken) if read else []
                if not found:
                    continue
                # Keyboard first, then pads, and never the same binding twice.
                ordered = ([b for b in found if b.startswith(input_registry.KEY_PREFIX)]
                           + [b for b in found if not b.startswith(input_registry.KEY_PREFIX)])
                if not self.config.has_section(input_registry.SECTION):
                    self.config.add_section(input_registry.SECTION)
                self.config.set(input_registry.SECTION, action.name,
                                ','.join(dict.fromkeys(ordered)))

        # Every spelling a key has ever had lands on the one we store. This runs after the
        # 2.x renames above, so tablerootdir -> gamerootdir -> game_root_dir is one chain,
        # and before the defaults are filled in, so nothing is added under an old name.
        for section in list(self.config.sections()):
            for key in list(self.config.options(section)):
                new_section, new_key = config_schema.locate(section, key)
                if (new_section, new_key) == (section, key):
                    continue
                if not self.config.has_section(new_section):
                    self.config.add_section(new_section)
                self.config.set(new_section, new_key, self.config.get(section, key))
                self.config.remove_option(section, key)
                changed = True

        # What a 2.x file said that a list now holds. After every key has its current name
        # and before defaults fill in, so a seed tells a key the person set from one we
        # supplied.
        if self._converted_from_ini:
            for seed in config_schema.seeds():
                before = copy.deepcopy(self.top_level)
                try:
                    seed(self)
                except Exception:
                    self.top_level = before
                    logger.exception("A 2.x seed failed; the keys it read stay where they were")

        # A retired spelling of a *value*. It resolves on read either way, so this is
        # about what the file says rather than what it does - and the file is what the
        # settings are served as, so it should not contradict the schema's choices.
        for (section, key), aliases in _RENAMED_VALUES.items():
            if not self.config.has_option(section, key):
                continue
            current = self.config.get(section, key).strip().lower()
            if current in aliases:
                announce('ini-renamed-values', f'{section}.{key}={current}')
                self.config.set(section, key, aliases[current])
                changed = True

        # A retired word inside a list. Unlike the choice above this one does not resolve
        # on read: `roles` is filtered against a known set, so an install written before
        # the rename silently stopped claiming the role rather than claiming it under the
        # old name. Order is kept, because the schema's default reads library first.
        for (section, key), aliases in _RENAMED_LIST_VALUES.items():
            if not self.config.has_option(section, key):
                continue
            items = [v.strip() for v in self.config.get(section, key).split(',')]
            renamed = [aliases.get(v.lower(), v) for v in items if v]
            if renamed != [v for v in items if v]:
                announce('ini-renamed-values', f'{section}.{key}')
                self.config.set(section, key, ','.join(renamed))
                changed = True

        # `device.roles` became `device.features`, and one role expands into two
        # features - the old `hub` was the library and the device list together, `device`
        # frontend - so neither _RENAMED_KEYS nor _RENAMED_LIST_VALUES can carry it.
        #
        # Written against the *value* rather than the old key on purpose. `roles` is an
        # alias, so the pass above has already moved it under `features` by the time this
        # runs, and matching on the key would silently never fire. An install that said
        # only `device` must not fall through to the default, which is everything - that
        # would hand a cab a library it never had.
        if self.config.has_option('device', 'features'):
            was = [v.strip().lower()
                   for v in self.config.get('device', 'features').split(',') if v.strip()]
            spread = {'hub': ('library', 'devices'), 'device': ('frontend',),
                      'player': ('frontend',)}
            if any(role in spread for role in was):
                now: list[str] = []
                for role in was:
                    for feature in spread.get(role, (role,)):
                        if feature not in now:
                            now.append(feature)
                announce('ini-roles-to-features', ','.join(was))
                # Every feature, in the order the install declares them. Overview is in
                # the list so that an install already asking for it keeps it, and no
                # role expands into it, so migrating never acquires one.
                order = ('library', 'frontend', 'devices', 'overview')
                self.config.set('device', 'features',
                                ','.join(f for f in order if f in now))
                changed = True

        # `behavior.confirm` was a list of scopes and is a switch now. Anything naming a
        # scope meant "ask me", so it becomes on; empty meant "never", so it becomes off.
        # Read rather than coerced: a stored "app,system" is not a boolean, and letting the
        # type conversion have it would answer no to someone who asked to be asked.
        if self.config.has_option('behavior', 'confirm'):
            raw = self.config.get('behavior', 'confirm').strip()
            if raw.lower() not in ('', 'true', 'false'):
                announce('confirm-scopes-to-switch', raw)
                self.config.set('behavior', 'confirm', 'true')
                changed = True
            elif not raw:
                self.config.set('behavior', 'confirm', 'false')
                changed = True

        # Add any missing default options
        for section, defaults in self.defaults.items():
            if not self.config.has_section(section):
                self.config.add_section(section)
                changed = True
            for key, value in defaults.items():
                if not self.config.has_option(section, key):
                    self.config.set(section, key, value)
                    changed = True

        # A list the file has no key for holds its default, written now. A key that is
        # there, even empty, is what the person has.
        for spec in config_schema.lists():
            if spec.key not in self.top_level:
                self.top_level[spec.key] = self._prepared(spec, spec.default())
                changed = True

        # Remove legacy Logger.file option; logs always go to the standard config dir file.
        if self.config.has_option('logger', 'file'):
            self.config.remove_option('logger', 'file')
            changed = True

        # Normalize blank theme values back to the configured default.
        current_theme = self.config.get('themes', 'active', fallback='').strip()
        if not current_theme:
            self.config.set('themes', 'active', self.defaults['themes']['active'])
            changed = True

        # Migrate misspelled vpinplay.initals to vpinplay.initials if present.
        if self.config.has_option('vpinplay', 'initals'):
            legacy_initials = self.config.get('vpinplay', 'initals', fallback='').strip()
            current_initials = self.config.get('vpinplay', 'initials', fallback='').strip()
            if legacy_initials and not current_initials:
                self.config.set('vpinplay', 'initials', legacy_initials)
            self.config.remove_option('vpinplay', 'initals')
            changed = True

        # A section the migration emptied - [Input] once its keys merge into [input], or
        # one whose every key moved elsewhere - would sit in the file forever as a husk.
        # Defaults are filled in above, so anything still empty has no values and none
        # coming.
        for section in list(self.config.sections()):
            if not self.config.options(section):
                self.config.remove_section(section)
                changed = True

        if changed:
            self.save()
        self._saved = _options(self.config)
        self._saved_top = copy.deepcopy(self.top_level)

    def _typed(self, section: str, key: str, raw: str) -> Any:
        """The value as JSON should hold it. Unknown keys stay strings.

        An empty string stays empty rather than becoming 0 or null: several int settings
        use blank to mean "no window on this one", and that is not the same as zero.
        """
        entry = config_schema.option(section, key)
        text = '' if raw is None else str(raw)
        if entry is None:
            return text
        # Before the blank rule below: an empty list is [], which a user editing the file
        # by hand can add to. "" would leave them guessing what shape it wanted.
        if entry.type == 'list':
            return [part.strip() for part in text.split(',') if part.strip()]
        if text.strip() == '':
            return text
        if entry.type == 'bool':
            return is_truthy(text)
        if entry.type == 'int':
            try:
                return int(float(text))
            except (TypeError, ValueError):
                return text
        return text

    @staticmethod
    def _as_text(value: Any) -> str:
        """Back to what a ConfigParser holds, so nothing above this module changes."""
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, (list, tuple)):
            return ','.join(str(v) for v in value)
        return '' if value is None else str(value)

    def _read_json(self) -> tuple[int, configparser.ConfigParser, dict]:
        schema, sections, kept = self._parsed_json()
        parser = configparser.ConfigParser(interpolation=None)
        for section, options in sections:
            if not parser.has_section(section):
                parser.add_section(section)
            for key, text in options:
                parser.set(section, key, text)
        return schema, parser, copy.deepcopy(kept)

    def _parsed_json(self) -> tuple[int, _Sections, dict]:
        """The file as sections of (key, text), held while the file is the same one.

        A store is built on every `get_ini_config()`, which is several times in a request,
        so the parse is kept by the file's identity. What it holds is read, never changed:
        `_read_json` hands out a parser and a copy of the rest.
        """
        try:
            seen = os.stat(self.json_path)
            identity = (seen.st_ino, seen.st_mtime_ns, seen.st_size)
        except OSError:
            identity = None
        held = _PARSED.get(self.json_path)
        if identity is not None and held is not None and held[0] == identity:
            return held[1]
        with open(self.json_path, encoding='utf-8') as handle:
            payload = json.load(handle) or {}
        sections = tuple(
            (section, tuple((config_schema.canonical(section, key), self._as_text(value))
                            for key, value in (values or {}).items()))
            for section, values in _flatten(payload.get(SETTINGS_KEY) or {}).items())
        kept = {key: value for key, value in payload.items()
                if key not in (SCHEMA_KEY, SETTINGS_KEY)}
        parsed = (int(payload.get(SCHEMA_KEY, CONFIG_SCHEMA) or CONFIG_SCHEMA), sections, kept)
        if identity is not None:
            _PARSED[self.json_path] = (identity, parsed)
        return parsed

    def _load_json(self) -> None:
        self._schema, self.config, self.top_level = self._read_json()

    def _take_in_the_file(self, saved: dict[tuple[str, str], str]) -> None:
        """What the file gained, changed or lost since `saved`, for every key this store
        left alone. A key this store changed keeps this store's value."""
        try:
            schema, current, top_level = self._read_json()
        except (OSError, ValueError) as exc:
            logger.warning("Could not read %s before saving, so it is written whole: %s",
                           self.json_path, exc)
            return
        ours, theirs = _options(self.config), _options(current)
        for (section, key), value in theirs.items():
            if ours.get((section, key)) == saved.get((section, key)) != value:
                if not self.config.has_section(section):
                    self.config.add_section(section)
                self.config.set(section, key, value)
        for section, key in saved.keys() - theirs.keys():
            if ours.get((section, key)) == saved[(section, key)]:
                self.config.remove_option(section, key)
        self.top_level = _merged_top_level(self.top_level, top_level, self._saved_top)
        for spec in config_schema.lists():
            if isinstance(self.top_level.get(spec.key), list):
                self.top_level[spec.key] = config_schema.normalize(
                    spec.key, self.top_level[spec.key])
        self._schema = max(self._schema, schema)

    def reread(self) -> None:
        """Take in the file as it stands now. A store holds what it read when it was made,
        and the Manager writes through ones of its own, so a long-lived one is what VPinFE
        started with until it is told to look again. Anything staged and not saved is
        dropped."""
        if not os.path.exists(self.json_path):
            return
        try:
            schema, parser, top_level = self._read_json()
        except (OSError, ValueError) as exc:
            logger.warning("Could not read %s again, keeping what was read: %s",
                           self.json_path, exc)
            return
        self._schema = schema
        self.config = parser
        self.top_level = top_level
        self._saved = _options(parser)
        self._saved_top = copy.deepcopy(top_level)

    def value(self, section: str, key: str) -> Any:
        """One setting, typed the way this store would write it back.

        Public because the HTTP API needs it. The
        fallback is the schema's default rather than blank: a setting the file omits is
        still what the install is running on, and answering "" would say the opposite.
        """
        from common.config.config_access import cfg_get
        entry = config_schema.option(section, key)
        raw = cfg_get(self, section, key, fallback=entry.default if entry else "")
        return self._typed(section, key, raw)

    def set_value(self, section: str, key: str, value: Any) -> None:
        """Stage one setting under its canonical name. `save()` writes the file."""
        section = config_schema.canonical_section(section)
        key = config_schema.canonical(section, key)
        if not self.config.has_section(section):
            self.config.add_section(section)
        self.config.set(section, key, self._as_text(value))

    @staticmethod
    def _spec(key: str) -> config_schema.ConfigList:
        spec = config_schema.config_list(key)
        if spec is None:
            raise config_schema.ConfigListError(t("error.config.list.not_declared", list=key))
        return spec

    def get_list(self, key: str) -> list[dict[str, Any]]:
        """A copy of a list's items; the list's default where the file has no such key."""
        spec = self._spec(key)
        held = self.top_level.get(key)
        if isinstance(held, list):
            return copy.deepcopy(held)
        if held is not None:
            logger.warning("%s in %s is not a list, so the default is read", key, self.json_path)
            return self._prepared(spec, spec.default())
        self.top_level[key] = self._prepared(spec, spec.default())
        return copy.deepcopy(self.top_level[key])

    def set_list(self, key: str, items: list[dict[str, Any]]) -> None:
        """Stage a whole list. An item with no `id` is given one, an item with a field the
        list does not declare refuses the write unless the file already holds it exactly
        so (a newer build's field passes through), and the list's normalizer has the last
        word on what is kept. `save()` writes the file."""
        self.set_lists({key: items})

    def set_lists(self, wanted: dict[str, list[dict[str, Any]]]) -> None:
        """Stage several lists, all or none: one that is refused leaves every list as it was."""
        prepared = {}
        for key, items in wanted.items():
            held = self.top_level.get(key)
            prepared[key] = self._prepared(self._spec(key), items,
                                           held if isinstance(held, list) else [])
        self.top_level.update(prepared)

    def theme_options(self) -> dict[str, dict[str, Any]]:
        """What the person chose on each theme, by folder name. A copy; an entry that is
        not an object is not a theme's values and is left out."""
        held = self.top_level.get(config_schema.THEME_OPTIONS)
        if held is not None and not isinstance(held, dict):
            logger.warning("%s in %s is not an object, so none is read",
                           config_schema.THEME_OPTIONS, self.json_path)
        if not isinstance(held, dict):
            return {}
        return {folder: copy.deepcopy(values) for folder, values in held.items()
                if isinstance(values, dict)}

    def set_theme_options(self, options: dict[str, dict[str, Any]]) -> None:
        """Stage the whole map. A theme whose choices were taken away keeps an empty entry:
        the migration skips a theme that has one, so old package values do not come back.
        `save()` writes the file."""
        self.top_level[config_schema.THEME_OPTIONS] = {
            folder: copy.deepcopy(values) for folder, values in options.items()}

    @staticmethod
    def _prepared(spec: config_schema.ConfigList, items: Any,
                  held: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            raise config_schema.ConfigListError(t("error.config.list.items", list=spec.key))
        already = [item for item in held or [] if isinstance(item, dict)]

        def clean(item: Any) -> Any:
            if not isinstance(item, dict):
                return spec.clean(item)
            if any(_same(item, was) for was in already):
                return copy.deepcopy(item)
            return spec.clean({**item, "id": item.get("id") or new_id()})

        cleaned = [clean(item) for item in items]
        for item in cleaned:
            if isinstance(item, dict) and "/" in str(item.get("id")):
                raise config_schema.ConfigListError(t("error.config.list.id_slash", list=spec.key))
        config_schema.validate(spec.key, cleaned, already)
        kept = config_schema.normalize(spec.key, cleaned)
        if len({item["id"] for item in kept}) != len(kept):
            raise config_schema.ConfigListError(t("error.config.list.shared_id", list=spec.key))
        return kept

    def save(self) -> None:
        with _SAVING:
            if self._saved is not None and os.path.exists(self.json_path):
                self._take_in_the_file(self._saved)
            self._write()
            self._saved = _options(self.config)
            self._saved_top = copy.deepcopy(self.top_level)

    def _write(self) -> None:
        # The first save after reading an ini keeps a copy and leaves the original alone:
        # a downgrade needs the file the older build reads.
        if self._converted_from_ini and os.path.exists(self.ini_path):
            logger.info("Kept the pre-JSON settings at %s",
                        copy_aside(str(self.ini_path), REASON_2X))
            self._converted_from_ini = False
        settings = _nest({section: {key: self._typed(section, key, value)
                                    for key, value in self.config.items(section)}
                          for section in self.config.sections()})
        # Never stamp a newer file down to what this build writes - that number belongs to
        # whichever VPinFE wrote it, and claiming it would say we understood the file.
        payload = {SCHEMA_KEY: max(getattr(self, '_schema', CONFIG_SCHEMA), CONFIG_SCHEMA),
                   SETTINGS_KEY: settings}
        payload.update({key: value for key, value in self.top_level.items()
                        if key not in payload})
        write_atomic(self.json_path,
                     lambda handle: json.dump(payload, handle, indent=2, ensure_ascii=False))

    def format_defaults(self) -> None:
        for section, defaults in self.defaults.items():
            self.config.add_section(section)
            for key, value in defaults.items():
                if not self.config.has_option(section, key):  # Only set if not present
                    self.config.set(section, key, value)
