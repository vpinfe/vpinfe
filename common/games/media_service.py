"""Finding a game's media for the pages, and forgetting it when the files change."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

from common.config.config_access import MediaConfig, cfg_get
from common.config.paths import CACHE_DIR, get_ini_config
from common.games import asset_origin, media_probe
from common.games.game import GameRecord
from common.games.game_metadata import reorder_leading_article, vpinfe_section
from common.games.game_repository import all_games
from common.games.info_file import MetaConfig
from common.media import sized_media
from common.media.media_specs import (
    IMAGE_FAMILY,
    MEDIA_SPECS,
    active_set_for,
    list_media_sets,
    media_attr_kind_map,
    media_family,
    media_filename_map,
    media_label_map,
    resolve_media_entries,
    resolve_media_files,
    resolve_media_pages,
)

logger = logging.getLogger("vpinfe.common.games.media_service")

_media_cache: list[dict] | None = None
_thumb_request_state: set[tuple[str, str, str]] = set()

THUMB_CACHE_ROOT = CACHE_DIR / "media_thumbs"
THUMB_SIZE = (512, 512)
THUMB_WARM_ROW_BATCH_SIZE = 25
THUMB_WARM_CHUNK_SIZE = 8

MEDIA_FILENAME_BY_KIND = media_filename_map("table")
MEDIA_LABEL_BY_KIND = media_label_map()
IMAGE_EXTENSIONS = set(IMAGE_FAMILY)

GAME_ATTR_TO_MEDIA_KIND = media_attr_kind_map("table")


def get_media_cache() -> list[dict] | None:
    return _media_cache


def set_media_cache(rows: list[dict]) -> None:
    global _media_cache
    _media_cache = rows


def invalidate_media_cache() -> None:
    global _media_cache
    _media_cache = None


def media_url(*parts: str) -> str:
    encoded = [quote(part.strip("/")) for part in parts if part]
    return "/" + "/".join(encoded)


def is_image_media_kind(kind: str) -> bool:
    filename = MEDIA_FILENAME_BY_KIND.get(kind, "")
    return Path(filename).suffix.lower() in IMAGE_EXTENSIONS


def media_contents(game_dir: Path, listing: tuple[list[str], list[str]] | None = None
                   ) -> tuple[set[str], set[str]]:
    """What is in the folder and in medias/, as the resolver wants it.

    medias/ comes back with relative paths, because a media set is a subfolder and the
    resolver matches it by "wheels/<set>/<name>". `listing` is the folder's
    `folder_listing` where the caller already holds it.
    """
    # Locally: the asset registry reads this module's image extensions at import.
    from common.games import asset_resolver

    files, subdirs = listing if listing is not None else asset_resolver.folder_listing(
        game_dir)
    medias: set[str] = set()
    if "medias" in {name.lower() for name in subdirs}:
        medias_dir = game_dir / "medias"
        try:
            for dirpath, _dirs, filenames in os.walk(medias_dir):
                rel = os.path.relpath(dirpath, medias_dir)
                for fname in filenames:
                    medias.add(fname if rel == "." else
                               f"{rel}/{fname}".replace(os.sep, "/"))
        except OSError:
            medias = set()
    return set(files), medias


def wheel_sets() -> list[str]:
    """Every wheel set the library has a folder for, and the reserved `logo`."""
    return list_media_sets((game.full_path_game for game in all_games()
                            if game.full_path_game), "wheel")


def media_settings() -> tuple[str, dict[str, str] | None]:
    """The playfield variant and any active media set, as the resolver takes them."""
    media_cfg = MediaConfig.from_config(get_ini_config())
    wheelset = active_set_for("wheel", media_cfg.wheelset)
    return media_cfg.playfield_variant, ({"wheel": wheelset} if wheelset else None)


def resolved_media(game_dir: Path, table_stem: str | None = None) -> dict:
    """Every media kind against the folder as it is right now."""
    files, medias = media_contents(game_dir)
    variant, active_sets = media_settings()
    return resolve_media_entries(game_dir, files, medias, variant,
                                 table_stem, active_sets)


def resolved_pages(game_dir: Path, table_stem: str | None = None) -> dict[str, list[Path]]:
    """Each paged kind with two or more pages, as its files in page order."""
    files, medias = media_contents(game_dir)
    variant, active_sets = media_settings()
    resolved = resolve_media_entries(game_dir, files, medias, variant, table_stem,
                                     active_sets)
    return resolve_media_pages(game_dir, files, medias, resolved, variant, table_stem)


def page_file(game_dir: Path, kind: str, page: int,
              table_stem: str | None = None) -> Path | None:
    """The file page `page` of a kind shows, or None where the kind has no such page."""
    hit = resolved_media(game_dir, table_stem).get(kind)
    if hit is None or hit.path is None:
        return None
    if page == 1:
        return hit.path
    pages = resolved_pages(game_dir, table_stem).get(kind, [])
    return pages[page - 1] if 1 <= page <= len(pages) else None


class ShownArt(NamedTuple):
    kind: str
    version: str


CABINET_FALLS_TO = {"playfield": ("playfield_fss",)}


def shown_media(game_dir: Path, kind: str, table_stem: str | None = None, *,
                contents: tuple[set[str], set[str]] | None = None,
                settings: tuple[str, dict[str, str] | None] | None = None
                ) -> ShownArt | None:
    """The file the cabinet shows for `kind`, as the kind its media route serves it
    under and its version, or None.

    `contents` and `settings` are `media_contents` and `media_settings` where the caller
    reads them once for many tables.
    """
    files, medias = contents if contents is not None else media_contents(game_dir)
    variant, active_sets = settings if settings is not None else media_settings()
    shown = (kind, *CABINET_FALLS_TO.get(kind, ()))
    resolved = resolve_media_entries(game_dir, files, medias, variant,
                                     table_stem, active_sets, only=shown)
    for one in shown:
        hit = resolved.get(one)
        version = sized_media.version(hit.path) if hit is not None else None
        if version:
            return ShownArt(one, version)
    return None


def media_map(game_dir: Path, prefix: str, table_stem: str | None = None, *,
              codecs: bool = False) -> dict:
    """Every kind described for one game, or for one of its tables. Resolve and
    describe together, so no caller can resolve for one table and describe another."""
    pages = resolved_pages(game_dir, table_stem)
    return media_entries(resolved_media(game_dir, table_stem), game_dir, prefix,
                         codecs=codecs, pages={kind: len(found) for kind, found in pages.items()})


def media_entries(resolved: dict, game_dir: Path, prefix: str, *,
                  codecs: bool = False, pages: dict[str, int] | None = None) -> dict:
    """What a curator asks of a slot: does it resolve, how specific, and where from.

    `via` is why this file is the one being used - "table", "game", "default",
    "set:<name>" or "fallback:<kind>". `origin` is who put it there, which is a
    different question with a different source: the .info ledger, read once per call
    here rather than once per kind. Neither answer implies the other.

    `codecs` reads each video's header for its `video_codec`, which is null otherwise.
    `pages` is a paged kind's count where it has two or more; every other kind present is
    one page.
    """
    recorded = asset_origin.sources(game_dir)
    hosts = {key: str(source.get("host", "") or "").strip()
             for key, source in recorded.items()
             if str(source.get("host", "") or "").strip()}
    return {
        key: {
            "present": hit.path is not None,
            "file": hit.path.name if hit.path is not None else None,
            "path": asset_origin.path_of(game_dir, hit.path) or None,
            "via": hit.tier,
            "origin": asset_origin.origin_of(hosts, game_dir, hit.path) or None,
            "matched_to": asset_origin.match_of(recorded, game_dir, hit.path) or None,
            "version": sized_media.version(hit.path),
            "pages": (pages or {}).get(key, 1 if hit.path is not None else 0),
            "video_codec": (media_probe.probe(hit.path)["video_codec"]
                            if codecs and hit.path is not None
                            and media_family(key) == "video" else None),
            "links": {"self": f"{prefix}/{key}"} if hit.path is not None else {"self": None},
        }
        for key, hit in resolved.items()
    }


_SPEC_BY_KIND = {spec.kind: spec for spec in MEDIA_SPECS}


def source_media_path(game_dir: Path, kind: str,
                      table_stem: str | None = None) -> str | None:
    """The file serving a media kind, through the one resolution chain - so the
    Manager and the scan can never disagree about which file that is."""
    if kind not in _SPEC_BY_KIND:
        return None
    root = Path(game_dir)
    try:
        with os.scandir(root) as entries:
            game_contents = {e.name for e in entries if e.is_file()}
    except OSError:
        return None
    medias_dir = root / "medias"
    try:
        with os.scandir(medias_dir) as entries:
            medias_contents = {e.name for e in entries if e.is_file()}
    except OSError:
        medias_contents = set()
    resolved = resolve_media_files(root, game_contents, medias_contents,
                                   "table", table_stem)
    path = resolved.get(kind)
    return str(path) if path is not None else None


def _build_thumb_sig(source_path: str) -> str:
    st = os.stat(source_path)
    return f"{st.st_mtime_ns}_{st.st_size}"


def thumb_file_path(game_dir_name: str, kind: str, source_path: str) -> Path:
    return THUMB_CACHE_ROOT / game_dir_name / f"{kind}_{_build_thumb_sig(source_path)}.png"


def thumb_url(path: Path) -> str:
    rel = path.relative_to(THUMB_CACHE_ROOT).as_posix()
    return f"/media_thumbs/{rel}"


def get_cached_thumb_url(game_dir_name: str, kind: str, source_path: str) -> str | None:
    if not is_image_media_kind(kind) or not os.path.exists(source_path):
        return None
    try:
        path = thumb_file_path(game_dir_name, kind, source_path)
        if path.exists():
            os.utime(path, None)
            return thumb_url(path)
    except Exception:
        return None
    return None


def thumb_request_key(game_dir_name: str, kind: str, source_path: str) -> tuple[str, str, str]:
    try:
        signature = _build_thumb_sig(source_path)
    except Exception:
        signature = ""
    return game_dir_name, kind, signature


def mark_thumb_requested(game_dir_name: str, kind: str, source_path: str) -> bool:
    """Return True if this thumbnail request is new."""
    key = thumb_request_key(game_dir_name, kind, source_path)
    if key in _thumb_request_state:
        return False
    _thumb_request_state.add(key)
    return True


def clear_thumb_request(game_dir_name: str, kind: str, source_path: str) -> None:
    _thumb_request_state.discard(thumb_request_key(game_dir_name, kind, source_path))


def ensure_thumb(game_dir_name: str, kind: str, source_path: str) -> str | None:
    if not is_image_media_kind(kind) or not os.path.exists(source_path):
        return None
    try:
        from PIL import Image, ImageOps
    except Exception:
        return None

    try:
        path = thumb_file_path(game_dir_name, kind, source_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            os.utime(path, None)
            return thumb_url(path)

        for old in path.parent.glob(f"{kind}_*.png"):
            if old != path:
                old.unlink(missing_ok=True)
        for old in path.parent.glob(f"{kind}_*.jpg"):
            if old != path:
                old.unlink(missing_ok=True)

        with Image.open(source_path) as opened:
            img = ImageOps.exif_transpose(opened)
            has_alpha = (
                img.mode in ("RGBA", "LA")
                or (img.mode == "P" and "transparency" in img.info)
            )
            img = img.convert("RGBA" if has_alpha else "RGB")
            img.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
            img.save(path, format="PNG", optimize=True)
        os.utime(path, None)
        trim_thumb_cache(thumb_cache_limit_bytes())
        return thumb_url(path)
    except Exception:
        return None


def thumb_cache_limit_bytes() -> int:
    """`thumb_cache_max_mb` in bytes; 0 or an unreadable value means no limit."""
    raw = cfg_get(get_ini_config(), "manager", "thumb_cache_max_mb", "500")
    try:
        return max(0, int(raw.strip())) * 1024 * 1024
    except ValueError:
        return 0


def trim_thumb_cache(limit_bytes: int) -> int:
    """Delete the least recently used thumbnails until the cache fits `limit_bytes`.

    Reading a thumbnail touches its mtime, so the oldest mtime is the one nobody has
    shown lately. Returns the number of files removed."""
    if limit_bytes <= 0:
        return 0
    found = []
    total = 0
    for path in THUMB_CACHE_ROOT.rglob("*.png"):
        try:
            stat = path.stat()
        except OSError:
            continue
        found.append((stat.st_mtime_ns, stat.st_size, path))
        total += stat.st_size
    removed = 0
    for _, size, path in sorted(found):
        if total <= limit_bytes:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        removed += 1
    return removed


def _game_meta_sections(game: GameRecord) -> tuple[dict, dict]:
    raw = game.meta_config or {}
    if not isinstance(raw, dict):
        raw = {}
    info = raw.get("Info", {}) if isinstance(raw.get("Info", {}), dict) else {}
    vpinfe = vpinfe_section(raw)
    return info, vpinfe


def media_url_from_path(game_dir_name: str, source_path: str) -> str | None:
    if not source_path:
        return None
    source = Path(source_path)
    if source.parent.name == "medias":
        return media_url("media_games", game_dir_name, "medias", source.name)
    return media_url("media_games", game_dir_name, source.name)


def scan_media_games(reload: bool = False) -> list[dict]:
    rows = []
    for game in all_games(reload=reload):
        root = game.full_path_game or ""
        if not root:
            continue
        current_dir = Path(root).name
        info, vpinfe = _game_meta_sections(game)
        name = ((vpinfe.get("alt_title") or "").strip()
                or reorder_leading_article(info.get("Title") or current_dir))

        media_info = {}
        thumb_info = {}
        for attr_name, kind in GAME_ATTR_TO_MEDIA_KIND.items():
            source_path = getattr(game, attr_name, None)
            if source_path:
                media_info[kind] = media_url_from_path(current_dir, source_path)
                thumb_info[kind] = get_cached_thumb_url(current_dir, kind, source_path)
            else:
                media_info[kind] = None
                thumb_info[kind] = None

        row = {
            "name": name,
            "game_dir_name": current_dir,
            "game_dir": root,
            "manufacturer": info.get("Manufacturer", ""),
            "year": info.get("Year", ""),
            "type": info.get("Type", ""),
            "themes": info.get("Themes", []),
            "media": media_info,
            "thumbs": thumb_info,
            "thumb_errors": {},
        }
        # Every kind, not the ones a page happens to show: a sortable column binds to
        # has_<kind>, and which kinds get a column is the page's choice to make.
        for kind in MEDIA_FILENAME_BY_KIND:
            row[f"has_{kind}"] = media_info.get(kind) is not None
        rows.append(row)

    set_media_cache(rows)
    return rows


def replace_media_file(game_dir: Path, game_dir_name: str, kind: str,
                       uploaded_path: str) -> str:
    """Install an uploaded file as a game's media, keeping its real extension.

    The name keeps the source extension when the kind's family accepts it: copying
    bytes to the canonical name unchanged puts JPEG bytes inside wheel.png, a file that
    lies. Any family sibling with the same stem is removed from medias/ and the folder
    root, since an earlier-family leftover would shadow the new file in resolution
    order.
    """
    spec = _SPEC_BY_KIND[kind]
    canonical = MEDIA_FILENAME_BY_KIND[kind]
    stem, canonical_ext = os.path.splitext(canonical)
    ext = os.path.splitext(uploaded_path)[1].lower()
    if ext not in spec.family:
        ext = canonical_ext
    target_filename = stem + ext

    medias_dir = os.path.join(game_dir, "medias")
    os.makedirs(medias_dir, exist_ok=True)
    target_path = os.path.join(medias_dir, target_filename)

    for sibling_ext in spec.family:
        for base in (medias_dir, game_dir):
            sibling = os.path.join(base, stem + sibling_ext)
            if sibling != target_path and os.path.exists(sibling):
                os.remove(sibling)

    shutil.copy2(uploaded_path, target_path)

    info_file = os.path.join(game_dir, f"{game_dir_name}.info")
    if os.path.exists(info_file):
        mc = MetaConfig(info_file)
        # No hash: one is only meaningful as a comparison against a remote, and there
        # is no remote here - the user handed us the bytes.
        mc.add_asset(target_path, "user")

    return target_path


def update_cache_entry(game_dir_name: str, kind: str, url_path: str,
                       thumb: str | None = None) -> None:
    if _media_cache is None:
        return
    for row in _media_cache:
        if row["game_dir_name"] == game_dir_name:
            row["media"][kind] = url_path
            row.setdefault("thumbs", {})[kind] = thumb
            row.setdefault("thumb_errors", {}).pop(kind, None)
            row[f"has_{kind}"] = url_path is not None
            break
