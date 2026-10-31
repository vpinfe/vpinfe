"""Reading and writing a game's `.info` file.

The one place that knows the on-disk shape, so a schema change lands here rather than
at every caller. A file is upgraded when it is read, and the old copy is set aside
rather than overwritten.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

from common.games import identity_claims, ties, unwritten
from common.games.info_migration import (
    FROM_CATALOG,
    GUIDE_ORIGIN,
    GUIDES_KEY,
    migrate,
    needs_migration,
    write_json_atomic,
)
from common.games.tables import (
    ABSENT_SINCE_KEY,
    ADDED_KEY,
    DEFAULT_TABLE_KEY,
    DETECT_KEYS,
    FORM_CONTAINED,
    PARSED_KEYS,
    TABLE_FILENAME_KEY,
    TABLE_ID_KEY,
    TABLE_PATH_KEY,
    TABLES_KEY,
    adopted_entry,
    contained_entry,
    entry_filename,
    entry_for_filename,
    entry_form,
    entry_from_parsed,
    entry_key,
    entry_native_key,
    entry_reference,
    keyed_entry,
    referenced_entry,
    stamp_added,
    stored_reference,
    table_entries,
)
from common.i18n import t
from common.ids import new_id
from common.kept_copies import write_backup
from common.timestamps import utc_now_iso

logger = logging.getLogger("vpinfe.common.games.info_file")

# Schema version for the VPinFE section only - we own those keys outright, so their
# shape can be reasoned about from a version. Other sections stay shape-driven.
#   1  original shape (deletedNVRamOnClose, altlauncher, pluginprofile, alttitle,
#      altvpsid). Implied when no version is recorded.
#   2  adds `game_id`, the stable local game id (see common/games/game_identity.py).
INFO_SCHEMA = 2
VPINFE_SCHEMA_KEY = "schema"

# Named for what it identifies, because this section holds ids at more than one scope:
# `default_table` beside it is a *table* id, and the source block will carry VPS and host
# ids too. A payload can say `game: {"id": ...}` because the object states the scope; this
# section does not.
GAME_ID_KEY = "game_id"

# The section we own outright: app configuration and game-level bookkeeping. 2.x wrote
# it as `VPinFE`; the migration renames it, since the sections we own are snake_case -
# which it now has.
VPINFE_SECTION = "vpinfe"

# One entry per file VPinFE placed, keyed by the path it was written to. Supersedes
# Medias, which was keyed by media kind and so held at most one entry per kind - it
# could not say which table's wheel it meant once artwork could belong to a
# specific one, and the same question applies to backglasses, ROMs and colorizations.
ASSETS_KEY = "assets"

_warned_newer_schema: set[int] = set()


def migrate_vpinfe_section(vpinfe: object) -> dict[str, Any]:
    """Upgrade the VPinFE section to the current schema, in memory.

    Idempotent; the stamp reaches disk on the next write. A section written by a newer
    build is left as-is rather than downgraded.
    """
    if not isinstance(vpinfe, dict):
        return {VPINFE_SCHEMA_KEY: INFO_SCHEMA}

    try:
        version = int(vpinfe.get(VPINFE_SCHEMA_KEY, 1) or 1)
    except (TypeError, ValueError):
        version = 1

    if version > INFO_SCHEMA:
        if version not in _warned_newer_schema:
            _warned_newer_schema.add(version)
            logger.warning(
                "Game metadata uses VPinFE schema %s, newer than this build's %s. "
                "Leaving it untouched; unknown settings are preserved.",
                version, INFO_SCHEMA,
            )
        return vpinfe

    if version < 2:
        vpinfe.setdefault(GAME_ID_KEY, "")  # declare only; minting is a writer's job

    vpinfe[VPINFE_SCHEMA_KEY] = INFO_SCHEMA
    return vpinfe


ALT_VPSID_KEY = "alt_vpsid"


class InvalidMetaConfigError(ValueError):
    """Raised when a game's .info file exists but cannot be read as metadata."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        super().__init__(reason)


PINBALL_PRIMER_PREFIX = "https://pinballprimer.github.io/"


# What kind of thing a guide is. The catalog carries tutorials only; a person may add
# the other two.
GUIDE_TUTORIAL = "tutorial"
GUIDE_KINDS = (GUIDE_TUTORIAL, "rule_sheet", "manual")


def _guide(record: dict, url: object) -> dict[str, Any] | None:
    """One catalog tutorial as a guide, or None where it names nothing to open.

    A record with no url and only a `youtubeId` is ordinary, not malformed.
    """
    said = str(url or "").strip()
    video = str(record.get("youtubeId") or "").strip()
    if not said and not video:
        return None
    authors = record.get("authors")
    return {
        "kind": GUIDE_TUTORIAL,
        GUIDE_ORIGIN: FROM_CATALOG,
        "title": str(record.get("title") or "").strip(),
        "authors": [str(one).strip() for one in authors if str(one).strip()]
                   if isinstance(authors, list) else [],
        "url": said,
        "youtube_id": video,
    }


def guides_from_vps(vpsdata: object) -> list[dict[str, Any]]:
    """Everything an entry offers that explains the machine, in the order it lists them.

    A `tutorialFiles` record either carries one `url` or a `urls` list of them, and the
    nested form is one guide per url rather than one per record.
    """
    if not isinstance(vpsdata, dict):
        return []
    out: list[dict[str, Any]] = []
    for record in vpsdata.get("tutorialFiles", []):
        if not isinstance(record, dict):
            continue
        nested = [one for one in (record.get("urls") or []) if isinstance(one, dict)]
        found = ([_guide(record, one.get("url")) for one in nested] if nested
                 else [_guide(record, record.get("url"))])
        out += [one for one in found if one is not None]
    return out


def ipdb_of(vps_entry: dict | None) -> str:
    """The IPDB number a catalog entry's IPDB link carries, or "" where it has none."""
    link = (vps_entry or {}).get("ipdbUrl", "") or ""
    return parse_qs(urlparse(link).query).get("id", [""])[0]


def info_from_vps(vps_entry: dict | None) -> dict[str, Any]:
    """The `Info` block for one catalog entry.

    Info is wholly what VPS knows about the machine. Rom and Authors are per-table values
    and live on the tables entry, not here.

    A function rather than a method because associating a folder is not the only time it
    is needed: correcting a match leaves every one of these describing the entry they
    were taken from, and adopting the new entry's details has to build the same block
    the association would have built.
    """
    entry = vps_entry or {}
    info = {
        "IPDBId": ipdb_of(entry),
        "Title": entry.get("name", ""),
        "Manufacturer": entry.get("manufacturer", ""),
        "Year": entry.get("year", ""),
        "Type": entry.get("type", ""),
        "Themes": entry.get("theme", []),
        "VPSId": entry.get("id", ""),
    }
    return info


def read_record(path: str | os.PathLike[str]) -> tuple[str, dict[str, Any]]:
    """A `.info` as stored: its text, and the object the text holds.

    Raises InvalidMetaConfigError, with the reason in words, for a file that is empty, is
    not UTF-8, is not JSON or does not hold an object, and OSError where it cannot be
    opened. Not upgraded: a writer puts back what it found.
    """
    where = os.fspath(path)
    try:
        if os.path.getsize(where) == 0:
            raise InvalidMetaConfigError(where, t("error.games.info_empty"))
        with open(where, encoding="utf-8") as f:
            text = f.read()
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidMetaConfigError(
            where, t("error.games.info_bad_line", line=exc.lineno)) from exc
    except UnicodeDecodeError as exc:
        raise InvalidMetaConfigError(where, t("error.games.info_not_utf8")) from exc
    if not isinstance(data, dict):
        raise InvalidMetaConfigError(where, t("error.games.info_wrong_shape"))
    return text, data


def read_info(path: str | os.PathLike[str]) -> dict[str, Any]:
    """What `read_record` finds, without its text."""
    return read_record(path)[1]


def _later_of_same_release(existing: dict[str, Any], new: dict[str, Any]) -> bool:
    """Whether `new` is the release `existing` is tied to, listed at a higher version than
    `existing` recorded: the file now held is the later one, so the tie measures from it."""
    release = new.get(ties.FILE_KEY)
    return bool(release and existing.get(ties.FILE_KEY) == release
                and ties.update_available({**existing, "version": new.get(ties.VERSION_KEY)}))


class MetaConfig:
    """One game's `.info`, upgraded to the current schema as it is read."""

    PINBALL_PRIMER_PREFIX = "https://pinballprimer.github.io/"

    def __init__(self, configfilepath: str) -> None:
        self.config_file_path = configfilepath
        self.data: dict[str, Any] = {}
        # The file as it was found, held only until a write backs it up.
        self._pre_migration = ""

        if os.path.exists(configfilepath):
            original, self.data = read_record(configfilepath)
            if needs_migration(self.data):
                self._pre_migration = original
                self.data = migrate(self.data)
                logger.debug("Migrated %s to schema %s in memory", configfilepath,
                             INFO_SCHEMA)
        else:
            self.data = {}
        held = unwritten.held(configfilepath)
        if held is not None:
            self.data = held
        self._normalize_detection_flags()
        self._migrate_vpinfe()

    @property
    def pending_migration(self) -> bool:
        """Whether this file converted on read and has not been written back yet.

        The lazy path means "has the library converted" has no answer without asking every
        file. This is how the caller asks one.
        """
        return bool(self._pre_migration)

    def write_config_meta(self, configdata: dict[str, Any]) -> None:
        """Build the .info JSON structure. `vpsdata` None keeps `Info` and the guides."""
        vpsdata = configdata.get("vpsdata", {})
        if vpsdata is None:
            kept, held = self.data.get("Info"), self.data.get(GUIDES_KEY)
            info = kept if isinstance(kept, dict) else info_from_vps(None)
            guides = held if isinstance(held, list) else []
        else:
            info = info_from_vps(vpsdata)
            guides = guides_from_vps(vpsdata)

        user = self.data.get("User", {
            "Rating": 0,
            "Favorite": False,
            "LastRun": None,
            "StartCount": 0,
            "RunTime": 0,
            "Tags": [],
        })
        if not isinstance(user, dict):
            user = {}
        user.setdefault("Rating", 0)
        user.setdefault("Favorite", False)
        user.setdefault("LastRun", None)
        user.setdefault("StartCount", 0)
        user.setdefault("RunTime", 0)
        user.setdefault("Tags", [])

        vpinfe = self.data.get(VPINFE_SECTION, {})
        if not isinstance(vpinfe, dict):
            vpinfe = {}
        vpinfe = migrate_vpinfe_section(vpinfe)
        vpinfe.setdefault("delete_nvram_on_close", False)
        vpinfe.setdefault("alt_launcher", "")
        vpinfe.setdefault("plugin_profile", "")
        vpinfe.setdefault("alt_title", "")
        # Configuration, not a play record - see game_metadata.game_frontend_dof_event.
        vpinfe.setdefault("frontend_dof_event", "")
        if not str(vpinfe.get(GAME_ID_KEY, "") or "").strip():
            # Imported here because game_identity reaches back through game_metadata
            # to this module. One minting rule, one place, no cycle.
            from common.games.game_identity import new_id

            vpinfe[GAME_ID_KEY] = new_id()

        assets = self.data.get(ASSETS_KEY, {})
        tables = self._build_tables(configdata)
        vpinfe.setdefault(ALT_VPSID_KEY, "")

        # Preserve any top-level sections we don't manage (e.g. metadata written by
        # other tools sharing the .info file) instead of dropping them on rebuild.
        # VPXFile and Medias are listed because they are ours and superseded - without
        # them here the old sections would survive as "unmanaged" and be written back
        # forever.
        preserved = {
            k: v
            for k, v in self.data.items()
            if k not in ("Info", "User", "VPXFile", VPINFE_SECTION, "Medias",
                         TABLES_KEY, ASSETS_KEY, GUIDES_KEY)
        }

        self.data = {
            "Info": info,
            "User": user,
            VPINFE_SECTION: vpinfe,
            TABLES_KEY: tables,
            ASSETS_KEY: assets,
            GUIDES_KEY: guides,
            **preserved
        }
        # Imported here for the reason game_identity is below: game_metadata reaches
        # back through this module.
        from common.games.game_metadata import dissolve_agreed_overrides

        dissolve_agreed_overrides(self.data)

        self.write_config()

    def _build_tables(self, configdata: dict[str, Any]) -> dict[str, dict]:
        """One entry per parsed table, keyed by filename.

        Callers pass `gamefiles` as {filename: parsed}. `vpxdata` alone is still
        accepted for the single-table case, which is most of the library.

        Anything already recorded against a filename and not covered by the parse -
        the user's `hidden`, a `patch_applied` flag, play stats and match records -
        survives untouched, and follows the file across a rename. Parsed fields are
        refreshed, so a stale value can never outlive what the .vpx actually says.
        """
        parsed_files = configdata.get("gamefiles")
        if not isinstance(parsed_files, dict) or not parsed_files:
            single = configdata.get("vpxdata") or {}
            name = str(single.get("filename", "") or "").strip()
            parsed_files = {name: single} if name else {}

        existing = table_entries(self.data)
        built = {}
        for filename, parsed in parsed_files.items():
            entry = entry_from_parsed(parsed)
            entry[TABLE_FILENAME_KEY] = filename
            prior = adopted_entry(existing, filename, entry.get("file_hash", ""),
                                  parsed_files)
            if isinstance(prior, dict):
                # Refresh what the parse covers; leave everything else alone. Doing it
                # the other way - naming the keys worth keeping - means every field we
                # add later has to be remembered here, and forgetting one silently
                # deletes it on the next rebuild. Play stats and match records live
                # here; neither should depend on somebody updating a list.
                entry = {**prior, **entry}
            else:
                stamp_added(os.path.dirname(self.config_file_path), entry)
            entry.setdefault(TABLE_ID_KEY, new_id())
            built[entry[TABLE_ID_KEY]] = entry
        return built

    def _entries_by_id(self) -> dict:
        """The tables map keyed by id, converted in place so the next write persists it."""
        entries = table_entries(self.data)
        self.data[TABLES_KEY] = entries
        return entries

    @staticmethod
    def _entry_for(entries: dict, filename: str) -> dict:
        """The entry describing this .vpx, minting one if the table is new to us."""
        _, entry = entry_for_filename(entries, filename)
        if entry:
            return entry
        minted = new_id()
        entry = {TABLE_ID_KEY: minted, TABLE_FILENAME_KEY: filename}
        entries[minted] = entry
        return entry

    def write_config(self) -> None:
        self._normalize_detection_flags()
        os.makedirs(os.path.dirname(self.config_file_path), exist_ok=True)
        if self._pre_migration:
            # Before the first write of the new shape, and only then: one restore point
            # per schema bump, not one per rebuild. A failure here stops the write - the
            # backup exists for the person who needs to go back, so proceeding without
            # one would take away the only thing that makes this reversible.
            saved = write_backup(self.config_file_path, self._pre_migration)
            self._pre_migration = ""
            logger.info("Kept the pre-migration metadata at %s", saved)
        write_json_atomic(self.config_file_path, self.data)
        unwritten.release(self.config_file_path)

    def get_config(self) -> dict[str, Any]:
        return self.data

    def strip_all_newlines(self, text: str) -> str:
        return text.replace("\r\n", "").replace("\n", "")

    def game_file_settings(self) -> dict:
        """Per-table entries, keyed by filename. A folder can hold several tables
        of one game - desktop, VR, a patched variant - and they are peers."""
        return table_entries(self.data)

    def set_table_hidden(self, filename: str, hidden: bool) -> None:
        """Hide a table from the frontend, or unhide it.

        Hiding never deletes. A patch base has to stay on disk - the patched table
        cannot be rebuilt without it - it just should not be offered as something to
        play. The same applies to a variant someone may want back later.
        """
        settings = self._entries_by_id()
        entry = self._entry_for(settings, filename)
        if hidden:
            entry["hidden"] = True
            vpinfe = self.data.get(VPINFE_SECTION)
            names = {entry[TABLE_ID_KEY], entry.get(TABLE_FILENAME_KEY)}
            if isinstance(vpinfe, dict) and vpinfe.get(DEFAULT_TABLE_KEY) in names:
                vpinfe.pop(DEFAULT_TABLE_KEY)
        else:
            entry.pop("hidden", None)
            # An entry that only ever carried `hidden` and a name came from a table we
            # never parsed; drop it rather than leave an empty record behind.
            if set(entry) <= {TABLE_ID_KEY, TABLE_FILENAME_KEY}:
                settings.pop(entry.get(TABLE_ID_KEY), None)
        self.write_config()

    def set_default_table(self, table_id: str) -> None:
        """Record which of a game's tables is the one to offer first.

        Stored as an id rather than a filename so the choice survives a rename, and
        cleared by passing "" - absent means "resolve from what is in the folder",
        which is the right answer until somebody has an opinion.
        """
        entries = self._entries_by_id()
        wanted = str(table_id or "").strip()
        if wanted and wanted not in entries:
            raise ValueError(t("error.games.table_not_found"))
        if wanted and entries[wanted].get("hidden") is True:
            raise ValueError(t("error.games.hidden_default"))
        vpinfe = self.data.setdefault(VPINFE_SECTION, {})
        if wanted:
            vpinfe[DEFAULT_TABLE_KEY] = wanted
        else:
            vpinfe.pop(DEFAULT_TABLE_KEY, None)
        self.write_config()

    def forget_table(self, table_id: str) -> bool:
        """Drop an absent table's record, once a person has decided it is not coming back.

        Only an entry discovery has stamped absent. While the file is on disk that entry
        is the record of a table the user owns, and `set_table_hidden` is what takes one
        out of play without losing its stats and match records. Refusing the present case
        is what keeps a stale id from deleting a table that is simply there.

        There is no backup because there is a better undo: put the .vpx back and refresh,
        and discovery mints the entry again - though with a *new* id, so a collection
        that named this table by id does not reconnect to it.
        """
        entries = self._entries_by_id()
        entry = entries.get(table_id)
        if not isinstance(entry, dict) or not entry.get(ABSENT_SINCE_KEY):
            return False

        entries.pop(table_id, None)
        vpinfe = self.data.get(VPINFE_SECTION)
        if isinstance(vpinfe, dict) and str(vpinfe.get(DEFAULT_TABLE_KEY, "")) == table_id:
            # The stored default is a table id, so leaving it would name a table nothing
            # describes.
            vpinfe[DEFAULT_TABLE_KEY] = ""
        self.write_config()
        return True

    def add_keyed_table(self, app: str, key: str, table_id: str) -> bool:
        """Record something this folder holds that has no file: a ROM its emulator looks
        up, a Pinball FX table id.

        Given its id here rather than by the minting pass, because the caller has to be
        able to say which entry it just made - a pass that runs at startup answers too
        late to be told to.

        Refuses a key this game already has. Two entries with the same app and key are
        the same thing twice, and nothing downstream could tell them apart.
        """
        entries = self._entries_by_id()
        wanted = f"{str(app or '').strip()}:{str(key or '').strip()}"
        if any(entry_native_key(one) == wanted for one in entries.values()
               if isinstance(one, dict)):
            return False
        entries[table_id] = {**keyed_entry(app, key), TABLE_ID_KEY: table_id,
                             ADDED_KEY: utc_now_iso()}
        self.write_config()
        return True

    def add_referenced_table(self, path: str, table_id: str) -> bool:
        """Record a game file that lives somewhere else.

        Stored as given - relative or absolute is the caller's decision, because only it
        knows whether the target is inside the library and therefore whether a relative
        path would survive the library moving.

        Refuses a path this game already references. Two entries pointing at one file
        are the same table twice and nothing downstream could tell them apart.
        """
        entries = self._entries_by_id()
        wanted = stored_reference(path)
        if any(entry_native_key(one) == wanted for one in entries.values()
               if isinstance(one, dict)):
            return False
        entries[table_id] = {**referenced_entry(wanted), TABLE_ID_KEY: table_id,
                             ADDED_KEY: utc_now_iso()}
        self.write_config()
        return True

    def listed_table_id(self, filename: str) -> str:
        """The id of the entry this .info already holds for a game file of this name, or
        "" where it holds none. The record can list a file the folder no longer has."""
        found, _ = entry_for_filename(self._entries_by_id(), str(filename or "").strip())
        return found

    def add_contained_table(self, filename: str, table_id: str) -> bool:
        """Record a game file that has just been copied into this folder.

        A scan would find the file on its own, but not before this call returns, and the
        caller has to be able to say which entry it just made - which is the same reason
        the two above take an id rather than waiting for the minting pass.

        Refuses a filename this game already holds, because the file is the identity.
        """
        entries = self._entries_by_id()
        wanted = str(filename or "").strip()
        if any(entry_native_key(one) == wanted for one in entries.values()
               if isinstance(one, dict)):
            return False
        entries[table_id] = {**contained_entry(wanted), TABLE_ID_KEY: table_id,
                             ADDED_KEY: utc_now_iso()}
        self.write_config()
        return True

    def contain_referenced_table(self, table_id: str, filename: str) -> bool:
        """Turn a reference into a table of this game's own, once its file has been
        copied in. The copy is the caller's - this is the half that has to be one write.

        The id survives, which is the point: a collection that named this table, and its
        play record, both stay pointed at it.
        """
        entries = self._entries_by_id()
        entry = entries.get(table_id)
        if not isinstance(entry, dict) or not entry_reference(entry):
            return False
        entry.pop(TABLE_PATH_KEY, None)
        entry[TABLE_FILENAME_KEY] = str(filename or "").strip()
        self.write_config()
        return True

    def forget_keyed_table(self, table_id: str) -> bool:
        """Drop an entry the folder does not hold: one with no file at all, or one whose
        file is somewhere else. Nothing on disk here will mint either back, which is
        exactly why forgetting one is safe and forgetting a table that is there is not.
        """
        entries = self._entries_by_id()
        entry = entries.get(table_id)
        if not isinstance(entry, dict) or not (entry_key(entry)
                                               or entry_reference(entry)):
            return False
        entries.pop(table_id, None)
        vpinfe = self.data.get(VPINFE_SECTION)
        if isinstance(vpinfe, dict) and str(vpinfe.get(DEFAULT_TABLE_KEY, "")) == table_id:
            vpinfe[DEFAULT_TABLE_KEY] = ""
        self.write_config()
        return True

    def game_file_value(self, filename: str, key: str, default: Any = "") -> Any:
        """One key off a specific table's entry."""
        _, value = entry_for_filename(table_entries(self.data), filename)
        return value.get(key, default) if isinstance(value, dict) else default

    def set_table_value(self, filename: str, key: str, value: Any) -> None:
        """Record something we did to a table, against that table."""
        entry = self._entry_for(self._entries_by_id(), filename)
        entry[key] = value
        self.write_config()

    def refresh_table(self, filename: str, parsed: dict | None) -> None:
        """Refresh what one table says about itself. Everything else on the entry -
        hidden, where it came from, later play stats - survives, as it does on a full
        rebuild.
        """
        entry = self._entry_for(self._entries_by_id(), filename)
        entry.update(entry_from_parsed(parsed))
        self.write_config()

    def replace_table(self, removed: str | None, filename: str,
                      parsed: dict | None) -> None:
        """One table replaced another on disk: describe the new one, forget the old.

        A gone table's entry is not kept - its history answers nothing once the file is
        gone.
        """
        entries = table_entries(self.data)
        gone_id, _ = entry_for_filename(entries, removed) if removed else ("", {})
        dropped = bool(removed and removed != filename and entries.pop(gone_id, None))
        if parsed:
            # A failed parse leaves the entry unparsed rather than filled with empties.
            self._entry_for(entries, filename).update(entry_from_parsed(parsed))
        elif not dropped:
            return      # nothing to say; do not add an empty section to the .info

        self.data[TABLES_KEY] = entries
        self.write_config()

    def swap_table(self, table_id: str, filename: str, parsed: dict | None) -> bool:
        """Another file became this table. The entry keeps its id and everything a person
        recorded on it - plays, rating, tags, hidden, the default - and takes the new
        file's name and what it says about itself.

        The old file's tie and the patch that made it are dropped; a declined tie stays.
        A file that could not be read leaves the entry unparsed, so enrichment reads it again.
        """
        entries = self._entries_by_id()
        entry = entries.get(table_id)
        if not isinstance(entry, dict) or entry_form(entry) != FORM_CONTAINED:
            return False
        was = entry_filename(entry)
        entry[TABLE_FILENAME_KEY] = filename
        entry.pop(ABSENT_SINCE_KEY, None)
        if parsed:
            entry.update(entry_from_parsed(parsed))
        else:
            for key in (*PARSED_KEYS, *DETECT_KEYS):
                entry.pop(key, None)
        source = entry.get("source")
        if isinstance(source, dict):
            for key in ("base", "patch"):
                source.pop(key, None)
            if not ties.declined(source):
                ties.untie(source)
            if not source:
                entry.pop("source")
        vpinfe = self.data.get(VPINFE_SECTION)
        if isinstance(vpinfe, dict) and vpinfe.get(DEFAULT_TABLE_KEY) == was:
            vpinfe[DEFAULT_TABLE_KEY] = table_id
        self.write_config()
        return True

    def rename_table(self, table_id: str, filename: str) -> bool:
        """The same file under another name: the entry keeps its id, what a person recorded
        on it, what it says about itself and the release it is tied to."""
        entry = self._entries_by_id().get(table_id)
        if not isinstance(entry, dict) or entry_form(entry) != FORM_CONTAINED:
            return False
        was = entry_filename(entry)
        entry[TABLE_FILENAME_KEY] = filename
        entry.pop(ABSENT_SINCE_KEY, None)
        vpinfe = self.data.get(VPINFE_SECTION)
        if isinstance(vpinfe, dict) and vpinfe.get(DEFAULT_TABLE_KEY) == was:
            vpinfe[DEFAULT_TABLE_KEY] = table_id
        self.write_config()
        return True

    def drop_table(self, table_id: str) -> bool:
        """Take a table's record out because its file has been deleted on purpose.

        Unlike `forget_table` this does not wait for discovery to find the file gone: the
        caller has just removed it. A game whose default this was offers its next table.
        """
        entries = self._entries_by_id()
        if not isinstance(entries.get(table_id), dict):
            return False
        was = entry_filename(entries.pop(table_id))
        vpinfe = self.data.get(VPINFE_SECTION)
        if isinstance(vpinfe, dict) and str(vpinfe.get(DEFAULT_TABLE_KEY, "")) in (table_id, was):
            vpinfe[DEFAULT_TABLE_KEY] = ""
        self.write_config()
        return True

    def restore_table_source(self, table_id: str, source: dict) -> bool:
        """Give a table back the release tie it had before a swap took it away."""
        entry = self._entries_by_id().get(table_id)
        if not isinstance(entry, dict) or not source:
            return False
        entry["source"] = copy.deepcopy(source)
        self.write_config()
        return True

    def restore_table(self, entry: dict) -> bool:
        """Put back the record of a table deleted on purpose, with the id it had.

        Does nothing, and says so, when the id is taken or another record already names the
        file: a record that exists is never replaced. It is not made the default.
        """
        entries = self._entries_by_id()
        table_id = str(entry.get(TABLE_ID_KEY) or "")
        filename = entry_filename(entry)
        if not table_id or not filename or table_id in entries or any(
                entry_filename(one) == filename for one in entries.values()):
            return False
        entries[table_id] = dict(entry)
        self.write_config()
        return True

    def record_patch_source(self, filename: str, base_file: str, base_hash: str,
                            patch_format: str) -> None:
        """Record a table we made ourselves: the base it came from, and the patch that
        made it. An ordinary .vpx has no source, which is the normal case.

        The base is hashed because a .dif applies to one exact file, and the delta's
        format is recorded rather than the code that applied it.
        """
        entry = self._entry_for(self._entries_by_id(), filename)
        entry["source"] = {
            "base": {"file": base_file, "hash": base_hash},
            "patch": {"format": patch_format, "applied": utc_now_iso()},
        }
        self.write_config()

    def add_asset(self, path: str, host: str, md5: str = "",
                  identity: identity_claims.DeclaredIdentity | None = None) -> None:
        """Record a file we placed, against the path we wrote it to.

        Origin only. What kind of media it is and which table it belongs to are read
        off the filename by resolve_media_files on every run, so a stored copy could
        only ever agree with it or be wrong - and resolution wins either way.

        `identity` is what the sender said this file is, when something witnessed it
        arriving from a named upstream record. A weaker claim never overwrites a stronger
        one already recorded, and an equal one does not either - the first witness keeps
        the record, so re-importing the same file does not churn the file.
        """
        key = self._asset_key(path)
        if identity is not None and not identity.is_empty:
            source = identity_claims.source_block(identity, md5=md5)
            source.setdefault("host", host)
            if source.get("vps_file_id"):
                from common.games.game_service import find_vps_record

                if listed := ties.listed_version(find_vps_record(source["vps_file_id"])):
                    source[ties.VERSION_KEY] = listed
            existing = (self.data.get(ASSETS_KEY, {}).get(key) or {}).get("source") or {}
            if existing and not identity_claims.outranks(
                    source.get("confirmed_by", ""), existing.get("confirmed_by", "")) \
                    and not _later_of_same_release(existing, source):
                return
        else:
            source = {"host": host}
            if md5:
                source["hash"] = md5
        self.data.setdefault(ASSETS_KEY, {})[key] = {"source": source}
        self.write_config()

    def asset_entries(self, keys: list[str]) -> dict[str, dict | None]:
        """The ledger entry at each key, or None where there is none. Copies, so a caller
        can hold them while the file changes."""
        held = self.data.get(ASSETS_KEY, {})
        return {key: copy.deepcopy(held[key]) if isinstance(held.get(key), dict) else None
                for key in keys}

    def rewrite_assets(self, entries: dict[str, dict | None]) -> None:
        """Set each key's entry, or drop it where the value is None, in one write."""
        held = self.data.setdefault(ASSETS_KEY, {})
        for key, entry in entries.items():
            if entry is None:
                held.pop(key, None)
            else:
                held[key] = copy.deepcopy(entry)
        self.write_config()

    def _asset_key(self, path: str) -> str:
        """A path relative to the game folder, with forward slashes.

        The .info travels with its folder, so an absolute path stops meaning anything
        the moment the library moves. Separators are normalized because a key written
        on Windows has to match one read on Linux.
        """
        try:
            relative = os.path.relpath(str(path), os.path.dirname(self.config_file_path))
        except ValueError:
            # Different drive on Windows; there is nothing to record but the name.
            relative = os.path.basename(str(path))
        return relative.replace(os.sep, "/")


    def _migrate_vpinfe(self) -> None:
        """Apply the VPinFE section schema migration to the loaded data, in memory."""
        if not isinstance(self.data, dict):
            return
        vpinfe = self.data.get(VPINFE_SECTION)
        if isinstance(vpinfe, dict):
            self.data[VPINFE_SECTION] = migrate_vpinfe_section(vpinfe)

    def _to_bool(self, val: Any) -> bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("true", "1", "yes", "on")
        return val == 1

    def _normalize_detection_flags(self) -> None:
        """Detect flags as real booleans, on every table entry.

        The parser has handed back strings at times, and a JSON "false" is truthy to
        anything that reads it without care.
        """
        for entry in table_entries(self.data).values():
            if not isinstance(entry, dict):
                continue
            for key in DETECT_KEYS:
                if key in entry:
                    entry[key] = self._to_bool(entry[key])
