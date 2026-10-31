"""What the Manager does to a game: rate it, re-match it, put it in a collection."""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import shutil
import threading
import time
import unicodedata
from collections.abc import Callable, Collection, Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import apps
from common.atomic_write import staged_for
from common.config.config_store import ConfigStore
from common.config.paths import COLLECTIONS_PATH, CONFIG_DIR, VPINFE_INI_PATH, VPSDB_PATH
from common.games import (
    game_index_service,
    game_repository,
    info_maintenance,
    metadata_service,
    tables,
    ties,
)
from common.games.collection_store import CollectionStore
from common.games.formats.vpx_parser import VPXParser
from common.games.game import Game, GameRecord
from common.games.game_metadata import (
    CONFIRMED_BY_USER,
    GAME_OVERRIDES,
    MATCHED_BY_USER,
    SOURCE_KEY,
    game_vps_id,
    record_vps_match,
    vpinfe_section,
)
from common.games.game_repository import refresh_game
from common.games.info_file import VPINFE_SECTION, read_info
from common.games.info_maintenance import RestoreResult, UpgradeResult
from common.games.tables import (
    TABLES_KEY,
    default_entry,
    entry_filename,
    offered_tables,
    recorded_default,
    table_entries,
    table_names,
)
from common.games.unreadable import UnreadableGame
from common.i18n import t
from common.online import vps_formats, vps_kinds
from common.release import migration_ledger
from common.runtime import job_details, jobs
from common.runtime.jobs import LogCallback, ProgressCallback
from common.vpinfe_version import get_version

logger = logging.getLogger("vpinfe.common.games.game_service")

VPSDB_JSON_PATH = VPSDB_PATH
_vpsdb_cache: list[dict] | None = None
_release_index: dict[str, tuple[dict, dict]] = {}
_release_index_of: list[dict] | None = None
_record_index: dict[str, dict] = {}
_record_index_of: list[dict] | None = None
_entry_ids: set[str] = set()
_entry_ids_of: list[dict] | None = None


def _fresh_config() -> ConfigStore:
    return ConfigStore(str(VPINFE_INI_PATH))


def normalize_game_rating(value: Any) -> int:
    try:
        normalized = int(float(value))
    except (TypeError, ValueError):
        normalized = 0
    return max(0, min(5, normalized))


def ensure_vpsdb_downloaded() -> bool:
    global _vpsdb_cache
    from common.online.vpsdb import VPSdb
    try:
        config = _fresh_config()
        VPSdb(config)
        _vpsdb_cache = None
        return VPSDB_JSON_PATH.exists()
    except Exception as e:
        logger.error("Failed to ensure vpsdb: %s", e)
        return VPSDB_JSON_PATH.exists()


def get_game_collections_map() -> dict[str, list[str]]:
    return game_repository.collections_by_game_id()


def get_game_collections() -> list[str]:
    result = []
    try:
        collections = CollectionStore(str(COLLECTIONS_PATH))
        # Every collection can be added to. Criteria and hand-picked members are
        # combinable, so carrying a rule does not stop a collection
        # holding something somebody named.
        result.extend(collections.get_collections_name())
    except Exception:
        pass
    return result


def add_game_to_collection(game_id: str, collection_name: str) -> bool:
    try:
        collections = CollectionStore(str(COLLECTIONS_PATH))
        collections.add_member(collection_name, game_id)
        collections.save()
        return True
    except Exception as e:
        logger.error("Failed to add game to collection: %s", e)
        return False


def update_info_section(game_dir: Path, section: str, key: str, value: Any) -> None:
    """Raises OSError or ValueError when the .info cannot be read or written."""
    info_file = game_dir / f"{game_dir.name}.info"
    data = read_info(info_file)
    data.setdefault(section, {})[key] = value
    info_file.write_text(json.dumps(data, indent=4), encoding="utf-8")
    refresh_game(game_dir)


def update_vpinfe_setting(game_dir: Path, key: str, value: Any) -> None:
    update_info_section(game_dir, VPINFE_SECTION, key, value)


def update_table_vpinfe_setting(game_dir: Path, table_id: str, key: str,
                                value: Any) -> None:
    """One table's own override, beside what was discovered about it, not on top of it.

    Under the table entry's own `vpinfe` key. A rebuild refreshes only what the parser
    produces and leaves the rest of an entry alone, so this survives a rescan without
    being named anywhere - the same reason play stats can live there.

    Per table rather than per game because these govern one file: which binary runs it,
    which ini it launches with, whose nvram is its own. A folder holding a VPX table and
    a Future Pinball one cannot answer for both with a single value.

    Raises OSError or ValueError when the .info cannot be read or written, and
    LookupError when it holds no such table.
    """
    info_file = game_dir / f"{game_dir.name}.info"
    data = read_info(info_file)
    entry = (data.get(TABLES_KEY) or {}).get(table_id)
    if entry is None:
        raise LookupError(t("error.games.table_id_unknown", table_id=table_id,
                            game=game_dir.name))
    entry.setdefault(VPINFE_SECTION, {})[key] = value
    info_file.write_text(json.dumps(data, indent=4), encoding="utf-8")
    refresh_game(game_dir)


def update_user_setting(game_dir: Path, key: str, value: Any) -> None:
    update_info_section(game_dir, "User", key, value)


def load_vpsdb() -> list[dict]:
    global _vpsdb_cache
    if _vpsdb_cache is not None:
        return _vpsdb_cache
    try:
        data = json.loads(VPSDB_JSON_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            _vpsdb_cache = data
        else:
            _vpsdb_cache = data.get("games") or data.get("items") or []
    except FileNotFoundError:
        # Before the first download finishes. A download that fails says so itself.
        logger.info("vpsdb.json is not downloaded yet")
        _vpsdb_cache = []
    except Exception as e:
        logger.error("Failed to load vpsdb.json: %s", e)
        _vpsdb_cache = []
    return _vpsdb_cache


_NOT_A_WORD = re.compile(r"\W+", re.UNICODE)


def _releases_by_id() -> dict[str, tuple[dict, dict]]:
    """Every release in the catalog by its id, with the entry it is listed under.

    Keyed on the list `load_vpsdb` returns, not on a flag: a reload hands back a new
    list, and so does a test standing in its own catalog, and either rebuilds this.
    """
    global _release_index, _release_index_of
    catalog = load_vpsdb()
    if catalog is not _release_index_of:
        index: dict[str, tuple[dict, dict]] = {}
        for entry in catalog:
            for release in (entry.get("tableFiles") or []):
                if release.get("id"):
                    index.setdefault(str(release["id"]), (release, entry))
        _release_index, _release_index_of = index, catalog
    return _release_index


def find_vps_record(vps_file_id: str) -> dict:
    """One file VPS lists, of any kind - a table build, a backglass, a PUP pack - by its own
    id, or {}. `find_vps_release` is the table builds alone."""
    global _record_index, _record_index_of
    catalog = load_vpsdb()
    if catalog is not _record_index_of:
        index: dict[str, dict] = {}
        for entry in catalog:
            for listing in ("tableFiles", *vps_kinds.BY_LISTING):
                for record in entry.get(listing) or []:
                    if record.get("id"):
                        index.setdefault(str(record["id"]), record)
        _record_index, _record_index_of = index, catalog
    return _record_index.get((vps_file_id or "").strip(), {})


def holds_vps_entry(entry_id: str) -> bool:
    """Whether the catalog lists a machine under this id. False for any id while the
    catalog is empty."""
    global _entry_ids, _entry_ids_of
    catalog = load_vpsdb()
    if catalog is not _entry_ids_of:
        _entry_ids = {str(entry.get("id") or "") for entry in catalog} - {""}
        _entry_ids_of = catalog
    return (entry_id or "").strip() in _entry_ids


def find_vps_release_and_entry(vps_file_id: str) -> tuple[dict, dict]:
    """One build of one machine and the machine it belongs to, or two empty dicts."""
    return _releases_by_id().get((vps_file_id or "").strip(), ({}, {}))


def find_vps_release(vps_file_id: str) -> dict:
    """One build of one machine, by its own id, across the whole catalog."""
    return find_vps_release_and_entry(vps_file_id)[0]


def release_fits(release: dict, entry: dict) -> bool:
    """Whether the app that plays this table entry can play this VPS release.

    A release with no `tableFormat` fits any app, and an app that declares no formats
    fits every release. An entry no app claims is matched as the default app's.
    """
    formats = apps.vps_formats(tables.app_of(entry) or apps.default_app().id)
    return vps_formats.fits(release.get("tableFormat"), formats)


def matched_vps_entry(game: GameRecord) -> dict:
    """The catalog entry this game is matched to, effective id first."""
    wanted = game_vps_id(game)
    if not wanted:
        return {}
    return next((e for e in load_vpsdb() if str(e.get("id") or "") == wanted), {})


def classify_vps_id(vps_id: str) -> str:
    """Whether an id names a machine, one of its builds, or nothing the catalog holds.

    `"entry"`, `"release"`, or `""` - and `""` also where the catalog is not loaded,
    which a caller has to tell apart from an id that is genuinely unknown.
    """
    wanted = (vps_id or "").strip()
    if not wanted:
        return ""
    for entry in load_vpsdb():
        if str(entry.get("id") or "") == wanted:
            return "entry"
    return "release" if find_vps_release(wanted) else ""


def route_legacy_match(data: dict) -> str:
    """Put an `alt_vpsid` where the id it holds belongs, and say what was done.

    Returns `"kept"`, `"bound"`, `"dropped"`, or `""` where there was nothing to route.
    Mutates `data`. A no-op while the catalog is empty.
    """
    vpinfe = data.get(VPINFE_SECTION)
    if not isinstance(vpinfe, dict):
        return ""
    if "alt_vpsid" in vpinfe and vpinfe["alt_vpsid"] is None:
        return "kept"
    wanted = str(vpinfe.get("alt_vpsid", "") or "").strip()
    if not wanted or not load_vpsdb():
        return ""

    found = classify_vps_id(wanted)
    if found == "entry":
        return "kept"

    vpinfe["alt_vpsid"] = ""
    if found != "release":
        return "dropped"

    entries = table_entries(data)
    _, entry = default_entry(entries, recorded_default(vpinfe))
    release = find_vps_release(wanted)
    if not entry or not release_fits(release, entry):
        return "dropped"
    entry[SOURCE_KEY] = ties.tie(dict(entry.get(SOURCE_KEY) or {}), wanted,
                                 CONFIRMED_BY_USER, release)
    data[TABLES_KEY] = entries
    return "bound"


def _plain(text: str) -> str:
    """Lowercased, with every run of punctuation as a space, for comparing names."""
    return _NOT_A_WORD.sub(" ", text.lower()).strip()


def search_vpsdb(term: str, limit: int = 50) -> list[dict]:
    """Catalog entries matching every word, in a neutral order.

    Every word has to appear somewhere in the name, maker or year, so "attack bally"
    narrows rather than widens - which is what a person typing two words means.

    Punctuation is not a word and does not have to line up. A library title is stored
    with its article moved for sorting - "Addams Family, The" - and that comma made the
    game's own name fail to find the game, which is the query a surface seeds by default.

    Sorted before the window is taken, and by name then year. Two reasons, and the
    second is the one that matters: stopping at `limit` in catalog order cuts off the
    answer somebody wanted by where it happens to sit in the file. And there is no
    ranking, deliberately - a scorer over this question is confidently wrong more often
    than not, so an order implying "best first" would carry a confidence nothing
    supports.
    """
    wanted = _plain(term or "").split()
    if not wanted:
        return []
    found = []
    for item in load_vpsdb():
        haystack = _plain(" ".join(str(item.get(key) or "")
                                   for key in ("name", "manufacturer", "year")))
        if all(word in haystack for word in wanted):
            found.append(item)
    found.sort(key=lambda item: (str(item.get("name") or "").lower(),
                                 str(item.get("year") or "")))
    return found[:limit]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_upload_bytes(dest_file: Path, content: bytes) -> None:
    ensure_dir(dest_file.parent)
    dest_file.write_bytes(content)


def _safe_upload_name(filename: str) -> str:
    safe_name = Path(filename or "").name
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError(t("error.games.unusable_name"))
    return safe_name


def _stored_meta(game_dir: Path) -> dict:
    """The folder's record, or {} where it has none that reads."""
    from common.games.info_file import MetaConfig

    meta_path = game_dir / f"{game_dir.name}.info"
    if not meta_path.exists():
        return {}
    try:
        return MetaConfig(str(meta_path)).data
    except Exception:
        return {}


def _find_vpx_file(game_dir: Path, preferred_filename: str = "") -> Path:
    """The file named, else the folder's default game file, else its first."""
    names = [path.name for path in game_dir.iterdir() if path.is_file()]
    stored = _stored_meta(game_dir)
    recorded = Path(preferred_filename or "").name or recorded_default(vpinfe_section(stored))
    offered = offered_tables(table_entries(stored), recorded, names)
    chosen = next((entry_filename(entry) for _id, entry in offered
                   if entry_filename(entry) in names), "")
    chosen = chosen or next(iter(table_names(names)), "")
    if not chosen:
        raise FileNotFoundError(t("error.games.folder_empty"))
    return game_dir / chosen


def _find_directb2s_file(game_dir: Path, preferred_stem: str = "") -> Path | None:
    b2s_files = sorted(path for path in game_dir.iterdir()
                       if path.is_file() and path.suffix.lower() == ".directb2s")
    if not b2s_files:
        return None

    preferred_stem_lower = preferred_stem.lower()
    if preferred_stem_lower:
        for path in b2s_files:
            if path.stem.lower() == preferred_stem_lower:
                return path
    return b2s_files[0]


def _find_ini_file(game_dir: Path, preferred_stem: str = "") -> Path | None:
    ini_files = sorted(path for path in game_dir.iterdir()
                       if path.is_file() and path.suffix.lower() == ".ini")
    if not ini_files:
        return None

    preferred_stem_lower = preferred_stem.lower()
    if preferred_stem_lower:
        for path in ini_files:
            if path.stem.lower() == preferred_stem_lower:
                return path
    return ini_files[0]


def _write_replace(dest_file: Path, content: bytes) -> None:
    ensure_dir(dest_file.parent)
    with staged_for(dest_file) as staged:
        staged.write_bytes(content)


def replace_table(game_dir: Path, filename: str, content: bytes, file_kind: str,
                  current_vpx_filename: str = "") -> dict[str, str]:
    game_dir = game_dir.expanduser()
    if not game_dir.exists() or not game_dir.is_dir():
        raise FileNotFoundError(t("error.games.folder_missing"))

    safe_name = _safe_upload_name(filename)
    ext = Path(safe_name).suffix.lower()

    if file_kind == "vpx":
        if ext != ".vpx":
            raise ValueError(t("error.uploads.table_kind"))

        old_vpx = _find_vpx_file(game_dir, current_vpx_filename)
        new_vpx = game_dir / safe_name
        old_b2s = _find_directb2s_file(game_dir, old_vpx.stem)
        old_ini = _find_ini_file(game_dir, old_vpx.stem)

        if old_vpx.resolve() == new_vpx.resolve():
            _write_replace(new_vpx, content)
        else:
            with staged_for(new_vpx) as staged:
                staged.write_bytes(content)
                old_vpx.unlink()

        renamed_b2s = ""
        if old_b2s and old_b2s.exists():
            new_b2s = game_dir / f"{new_vpx.stem}.directb2s"
            if old_b2s.resolve() != new_b2s.resolve():
                if new_b2s.exists():
                    new_b2s.unlink()
                os.replace(old_b2s, new_b2s)
            renamed_b2s = new_b2s.name

        renamed_ini = ""
        if old_ini and old_ini.exists():
            new_ini = game_dir / f"{new_vpx.stem}.ini"
            if old_ini.resolve() != new_ini.resolve():
                if new_ini.exists():
                    new_ini.unlink()
                os.replace(old_ini, new_ini)
            renamed_ini = new_ini.name

        refresh_game(game_dir)
        return {
            "file_kind": "vpx",
            "filename": new_vpx.name,
            "game_dir": str(game_dir),
            "directb2s_filename": renamed_b2s,
            "ini_filename": renamed_ini,
        }

    if file_kind == "directb2s":
        if ext != ".directb2s":
            raise ValueError(t("error.uploads.backglass_kind"))

        current_vpx = _find_vpx_file(game_dir, current_vpx_filename)
        old_b2s = _find_directb2s_file(game_dir, current_vpx.stem)
        target_b2s = old_b2s if old_b2s else game_dir / f"{current_vpx.stem}.directb2s"
        _write_replace(target_b2s, content)

        refresh_game(game_dir)
        return {
            "file_kind": "directb2s",
            "filename": target_b2s.name,
            "game_dir": str(game_dir),
        }

    raise RuntimeError(f"replace_table has no {file_kind!r} kind")


def associate_vps_to_folder(
    game_dir: Path,
    vps_entry: dict,
    download_media: bool = False,
    matched_by: str = MATCHED_BY_USER,
) -> None:
    from common.games.info_file import MetaConfig

    if not game_dir.exists():
        raise FileNotFoundError(t("error.games.folder_missing"))

    meta_path = game_dir / f"{game_dir.name}.info"
    vpx_file = _find_vpx_file(game_dir)
    parser = VPXParser()
    vpxdata = parser.single_file_extract(str(vpx_file))

    meta = MetaConfig(str(meta_path))
    record_vps_match(meta.data, matched_by)
    meta.write_config_meta({"vpsdata": vps_entry, "vpxdata": vpxdata})

    if download_media:
        from common.online.vpsdb import VPSdb

        config = _fresh_config()
        vps = VPSdb(config)

        # A Game with nothing resolved yet: the downloader reads the media paths to
        # see what is already on disk, and for a folder being associated the answer is
        # nothing. Every one of those fields already defaults to None.
        fresh = Game(game_dir_name=game_dir.name, full_path_game=str(game_dir),
                     full_path_vpx_file=str(vpx_file))
        vps.download_media_for_game(fresh, str(vps_entry.get("id") or ""),
                                    meta_config=meta)

    from common.games.media_service import invalidate_media_cache
    invalidate_media_cache()
    refresh_game(game_dir)


def scan_game_rows(reload: bool = False) -> list[dict]:
    return game_index_service.scan_rows(reload=reload)


def scan_missing_game_rows(reload: bool = False) -> list[dict]:
    return game_index_service.scan_missing_rows(reload=reload)


def extract_vbs(game_dir: Path, vpx_filename: str, table_id: str = "") -> dict:
    """Run the VPX binary with -extractvbs to extract a table's .vbs script.

    VPX writes the extracted .vbs next to the .vpx file (the game's root dir)
    automatically, so we only need to invoke the binary and report the result.

    Returns {'vbs_path': str} on success. Raises on failure.
    """
    import subprocess

    from common.host import bundled_libraries
    from common.host.launch import binary_for

    # Through the same resolution a launch uses, so the program that extracts a script is
    # the one that would have played the table.
    vpxbin_path = Path(binary_for(table_id, vpx_filename))

    vpx_file = game_dir / vpx_filename
    if not vpx_file.is_file():
        raise FileNotFoundError(t("error.games.file_gone"))

    launch_env = bundled_libraries.for_child()

    cmd = [str(vpxbin_path), "-extractvbs", str(vpx_file)]
    logger.info("Extracting VBS: %s", cmd)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=launch_env,
        )
    except OSError as exc:
        logger.warning("Could not run %s: %s", vpxbin_path, exc)
        raise ChildProcessError(t("error.games.vpx_failed")) from exc
    if result.returncode != 0:
        logger.warning("Extracting %s: VPX exited with code %s: %s", vpx_file.name,
                       result.returncode, (result.stderr or result.stdout or "").strip())
        raise ChildProcessError(t("error.games.vpx_no_script"))

    vbs_file = vpx_file.with_suffix('.vbs')
    return {'vbs_path': str(vbs_file), 'vbs_exists': vbs_file.is_file()}


def _scan(job: jobs.Job, download_media: bool, update_all: bool,
          game_name: str | None, user_media: bool) -> dict[str, int]:
    return metadata_service.build_metadata(
        download_media=download_media, update_all=update_all, game_name=game_name,
        user_media=user_media, iniconfig=_fresh_config(),
        progress_cb=job.progress, log_cb=job.heard)


def build_metadata(download_media: bool = True, update_all: bool = True,
                   game_name: str | None = None, user_media: bool = False,
                   progress_cb: ProgressCallback | None = None,
                   log_cb: LogCallback | None = None,
                   job: jobs.Job | None = None) -> dict[str, int]:
    """A library scan is a job wherever it was started from.

    Going through the registry keeps a caller's callbacks and return value, and buys
    two things: the scan reaches the event stream, and it shares the one-at-a-time
    rule with the API instead of the two paths being able to rewrite the same .info
    files at once.

    `job` is for a caller that already registered one; without it this call owns the
    registration.
    """
    if job is not None:
        return _scan(job, download_media, update_all, game_name, user_media)
    with jobs.track(jobs.KIND_LIBRARY_SCAN, progress_cb=progress_cb, log_cb=log_cb) as tracked:
        return _scan(tracked, download_media, update_all, game_name, user_media)


def info_maintenance_counts(reload: bool = False) -> dict[str, int]:
    """What the Tables page needs to decide whether to offer upgrade or a restore."""
    return game_repository.info_maintenance_counts(reload=reload)


def unreadable_games() -> list[UnreadableGame]:
    return game_repository.unreadable_games()


def pending_upgrade_game_names() -> list[str]:
    return game_repository.pending_upgrade_game_names()


def newest_backup_stamp() -> str:
    return game_repository.newest_backup_stamp()


def collections_restorable() -> bool:
    """Whether a newer VPinFE left a collections file this build can put back."""
    from common.games.collection_store import restorable_collections_backup

    return bool(restorable_collections_backup(CONFIG_DIR))


def restorable_game_names() -> list[str]:
    return game_repository.restorable_game_names()


def _as_a_job[T](work: Callable[[jobs.Job], T], progress_cb: ProgressCallback | None,
                 log_cb: LogCallback | None, job: jobs.Job | None, title: str = "") -> T:
    """Run one pass over the library's `.info` files, as a job either way.

    `job` is for a caller that started one already - the API submits to the registry and
    owns the job before the work begins, so registering a second here would refuse itself
    as busy. Without one, this registers its own, which is what an in-process caller
    needs.

    A library scan rather than a kind of its own, in both cases: the point of the kind is
    that two things rewriting the same `.info` files must not overlap, and these rewrite
    exactly the files a scan does.
    """
    if job is not None:
        result = work(job)
    else:
        with jobs.track(jobs.KIND_LIBRARY_SCAN, progress_cb=progress_cb, log_cb=log_cb,
                        title=title) as owned:
            result = work(owned)
    game_repository.refresh_games()
    return result


def upgrade_info(progress_cb: ProgressCallback | None = None,
                 log_cb: LogCallback | None = None, *, job: jobs.Job | None = None,
                 game_name: str | None = None) -> UpgradeResult:
    """Upgrade every game's .info in one pass."""
    def run(owned: jobs.Job) -> UpgradeResult:
        done = info_maintenance.upgrade_library(
            game_name=game_name,
            progress_cb=owned.progress, log_cb=owned.heard,
            route_match=route_legacy_match)
        owned.detail = job_details.info_upgraded(done)
        return done

    result = _as_a_job(run, progress_cb, log_cb, job, t("said.title_info_upgrade"))
    if result["upgraded"] and not game_name:
        migration_ledger.record("info_upgrade", get_version(), result["upgraded"])
    return result


def restore_info(progress_cb: ProgressCallback | None = None,
                 log_cb: LogCallback | None = None, *, job: jobs.Job | None = None,
                 game_name: str | None = None) -> RestoreResult:
    """Put back the .info files saved before upgrade, for every game that has one."""
    def run(owned: jobs.Job) -> RestoreResult:
        done = info_maintenance.restore_library(
            game_name=game_name, config_dir=CONFIG_DIR,
            progress_cb=owned.progress, log_cb=owned.heard)
        owned.detail = job_details.info_restored(done)
        return done

    return _as_a_job(run, progress_cb, log_cb, job, t("said.title_info_restore"))


def apply_vpx_patches(progress_cb: ProgressCallback | None = None,
                      only: Collection[str] | None = None) -> None:
    metadata_service.apply_vpx_patches(progress_cb=progress_cb, only=only)


# The fields a caller may answer, by the name the wire uses. Where each is stored is
# `GAME_OVERRIDES`, so the two cannot drift.
DETAIL_FIELDS = tuple(GAME_OVERRIDES)


def set_details(game_dir: Path, values: dict) -> dict:
    """Describe the machine in the user's own words, into the section we own.

    A patch: what is not sent is left alone, so a caller filling in a year does not have
    to restate a title it never knew. Sending a key empty clears the answer, which
    returns the field to whatever the entry said.

    Writes no `Info`, so a rebuild and an adopt both leave what is stored here alone.
    """
    record = game_dir / f"{game_dir.name}.info"
    if not record.is_file():
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(record))

    held = read_info(record)
    ours = held.setdefault(VPINFE_SECTION, {})
    if not isinstance(ours, dict):
        ours = held[VPINFE_SECTION] = {}
    for name in DETAIL_FIELDS:
        if name not in values:
            continue
        key = GAME_OVERRIDES[name][0]
        given = values[name]
        answered: Any = ([str(one).strip() for one in given if str(one).strip()]
                         if name == "themes"
                         else str(given if given is not None else "").strip())
        if answered:
            ours[key] = answered
        else:
            ours.pop(key, None)

    record.write_text(json.dumps(held, indent=4), encoding="utf-8")
    refresh_game(game_dir)
    return {name: ours.get(GAME_OVERRIDES[name][0], "")
            for name in DETAIL_FIELDS}


# A listing is reused while the folder's modification time and `_LISTING_KEPT` hold.
_LISTING_KEPT = 20.0
_LISTINGS = 8
_listed: dict[Path, tuple[int, float, tuple[Path, ...]]] = {}
_listed_lock = threading.Lock()


def files_in(folder: Path) -> tuple[Path, ...]:
    """The files directly in a folder, sorted. Nothing where it cannot be read."""
    try:
        stamp = folder.stat().st_mtime_ns
    except OSError:
        return ()
    now = time.monotonic()
    held = _listed.get(folder)
    if held is not None and held[0] == stamp and now - held[1] < _LISTING_KEPT:
        return held[2]
    try:
        with os.scandir(folder) as entries:
            files = tuple(sorted(Path(entry.path) for entry in entries if entry.is_file()))
    except OSError:
        return ()
    with _listed_lock:
        if len(_listed) >= _LISTINGS:
            _listed.pop(next(iter(_listed)))
        _listed[folder] = (stamp, now, files)
    return files


def companions_beside(source: Path, beside: Iterable[Path] | None = None) -> list[Path]:
    """The files that belong to this table, where it currently sits.

    Matched on the exact stem rather than a prefix: a table called `Taxi` must not
    collect what belongs to `Taxi 2`.

    Which extensions count is the app's answer, not one written down here: what belongs
    beside a table is a fact about the program that plays it, and the next app's list
    will differ. A file nothing here plays has no companions, because nothing can say
    what they would be.

    `beside` is the files of the table's folder where the caller already has them.
    """
    from common import apps

    app = apps.app_for(source.name)
    wanted = set(app.claim.companions) if app else set()
    if not wanted:
        return []

    stem = source.stem.lower()
    return [one for one in (files_in(source.parent) if beside is None else beside)
            if one != source and one.stem.lower() == stem
            and one.suffix.lower() in wanted]


def _already(path: Path) -> FileExistsError:
    return FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(path))


@dataclass(frozen=True)
class Arrived:
    """What a table that has just landed says about itself."""

    rom: str = ""
    named: tuple[str, ...] = ()


def add_table_file(game_dir: Path, source: Path, table_id: str, move: bool = False,
                   names: Iterable[str] = ()) -> dict:
    """Copy a game file into a folder and record it, as one operation.

    Both halves or neither: a file in the folder with nothing describing it becomes a
    table with no id on the next scan, which is a worse state than the copy not having
    happened.

    **Its companions come with it.** A table without its backglass is not the table, and
    a table without its settings is not set up; bringing the game file alone leaves both
    behind. They are copied after the table is recorded, and one that fails is reported rather than
    losing the table that did arrive.

    Answers with what landed: the table's filename, the id it is recorded under, and the
    companions that came too. The id is the one passed in unless the .info already lists
    a file of this name that the folder has lost; the file then returns under that entry.

    `move` takes the files out of where they were instead of copying them: the one
    case where this writes to its source. A file that cannot be recorded goes back to
    where it came from, because it is the only copy.

    `names` are looked for in the table's script, in the same read that finds its ROM;
    the answer's `named` holds those the script mentions.
    """
    landing = game_dir / source.name
    if landing.exists():
        raise _already(landing)

    from common.games.info_file import MetaConfig

    listed = MetaConfig(str(game_dir / f"{game_dir.name}.info")).listed_table_id(source.name)
    carry = shutil.move if move else shutil.copy2
    companions = companions_beside(source)
    try:
        carry(source, landing)
    except Exception:
        # Only while the source is still there: if it is gone, the landing is the file.
        if source.exists():
            landing.unlink(missing_ok=True)
        raise
    try:
        arrived = record_arrived_table(game_dir, landing, listed or table_id, keep=move,
                                       listed=bool(listed), names=names)
    except Exception:
        if move and landing.exists():
            shutil.move(landing, source)
        raise

    brought = []
    for one in companions:
        beside = game_dir / one.name
        # Never over something already here. A second run, or a file somebody put there
        # on purpose, is not ours to replace - the same rule the import applies to games.
        if beside.exists():
            continue
        try:
            carry(one, beside)
            brought.append(one.name)
        except OSError as exc:
            logger.warning("%s did not come with %s: %s", one.name, source.name, exc)

    refresh_game(game_dir)
    return {"table": source.name, "table_id": listed or table_id,
            "companions": brought, "rom": arrived.rom, "named": arrived.named}


def record_arrived_table(game_dir: Path, landing: Path, table_id: str,
                         keep: bool = False, listed: bool = False,
                         names: Iterable[str] = ()) -> Arrived:
    """Record a game file that has just landed in its game's folder, under `table_id`,
    and describe it from what it says about itself. Answers with the ROM it names and
    which of `names` its script mentions.

    Raises ValueError, having removed the file unless `keep`, where the game already
    holds one by that name. `listed` says the record already holds the entry and only the
    file was missing, so nothing is added and the file is described under that id. Leaves
    the refresh to the caller.
    """
    from common.games.info_file import MetaConfig

    meta = MetaConfig(str(game_dir / f"{game_dir.name}.info"))
    if not listed and not meta.add_contained_table(landing.name, table_id):
        if not keep:
            landing.unlink(missing_ok=True)
        raise ValueError(t("error.games.file_exists"))

    # Read now rather than left for the sweep that enriches a whole library: anything
    # keyed on the ROM - a ROM set, a sound bank, a color set - cannot be placed beside a
    # table that has not said which ROM it wants.
    try:
        from common.games.library_enrichment import read_one

        parsed = read_one(landing)
        if parsed:
            meta.replace_table("", landing.name, parsed)
            from common.games.formats.script_names import named_in

            return Arrived(str(parsed.get("rom") or "").strip(),
                           tuple(sorted(named_in(parsed.get("game_data") or "", names))))
    except Exception:
        logger.warning("Imported %s but could not read what it says about itself",
                       landing.name, exc_info=True)
    return Arrived()


def sanitize_dir_name(name: str) -> str:
    """Strip filesystem-reserved characters from a proposed game folder name.

    Also what a share does not keep as it was given: the replacement character, which is
    what a broken encoding leaves where a letter was and which a network share stores as
    other bytes, and control, surrogate and private-use characters. The name is composed
    (NFC), because a name sent decomposed comes back from a share as a different one.
    """
    kept = "".join(c for c in unicodedata.normalize("NFC", name or "")
                   if c not in '<>:"/\\|?*�'
                   and unicodedata.category(c) not in ("Cc", "Cs", "Co"))
    return kept.strip()


def create_game(name: str, location_id: str = "") -> Path:
    """Make a game folder with a record in it, and return the folder.

    A folder is an entry because it holds a record, so the record is written here rather
    than left to the next scan: an entry with no game file - a ROM, something a program
    looks up by name - has nothing else to be found by.

    Raises ValueError with the sentence to show when there is nowhere to create it, and
    FileExistsError when something is already there.
    """
    from common.games import library_folders
    from common.games.info_file import MetaConfig

    wanted = sanitize_dir_name(name)
    if not wanted:
        raise ValueError(t("error.games.name_needed"))

    where = library_folders.destination(location_id)
    if where.location is None:
        raise ValueError(where.reason)

    folder = Path(where.path) / wanted
    if folder.exists():
        raise _already(folder)

    folder.mkdir(parents=True)
    try:
        MetaConfig(str(folder / f"{wanted}.info")).write_config_meta({})
    except Exception:
        # A folder with no record is not an entry, so leaving one behind would put a
        # directory in the library that nothing can see and nothing will clean up.
        with suppress(OSError):
            folder.rmdir()
        raise

    refresh_game(folder)
    logger.info("Created game folder %s", folder)
    return folder
