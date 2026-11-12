"""Frontend themes: what is available, what is installed, and which one plays.

"Theme" unqualified means the frontend's, which is a decision the tree already made - a
game's `themes` are what a machine is about.

Reading the registry reaches the network, so it is held per process and refreshed only
when a caller asks or the configured sources change. Re-reading it on every draw would
spend a round trip per render on a list that changes when somebody publishes a release.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, NamedTuple

from common import service_errors
from common.atomic_write import write_atomic
from common.config import paths
from common.config.paths import THEME_CACHE_PATH, get_ini_config
from common.failures import why
from common.i18n import t
from common.media import sized_media
from common.online import (
    theme_layouts,
    theme_previews,
    theme_service,
    theme_source_checks,
    theme_sources,
    theme_sync,
)
from common.online.theme_registry_client import ThemeRegistryError
from common.online.themes import ThemeRegistry
from common.security import accepted

logger = logging.getLogger("vpinfe.common.online.theme_ops")


class SourceUnavailableError(service_errors.ServiceError):
    """A theme source that will not load. Nothing about this install is wrong."""


class InstallFailedError(service_errors.ServiceError):
    """The registry was read and the install did not finish."""


class RemoveFailedError(service_errors.ServiceError):
    """The theme is installed and could not be taken away."""


_lock = threading.RLock()
_registry: Any = None
# The sources `_registry` was read for. Memory held for other sources is not served.
_held_for: dict[str, list[str]] | None = None
# What each source said when the last read found none of them usable. Nothing else
# holds it: that read builds no registry to keep.
_unread: dict[str, dict[str, Any]] = {}


def _loaded(refresh: bool = False) -> ThemeRegistry:
    """The registry: from memory, else from the last read kept on disk, else - or when
    asked, or when the sources have changed - from the sources, which is network work and
    not per request."""
    global _registry, _held_for, _unread
    with _lock:
        now = _sources_now()
        stale = _registry is not None and _held_for != now
        if (_registry is None or stale) and not refresh:
            cached = _cached()
            if cached is not None:
                _registry, _held_for, stale = cached, now, False
        if _registry is None or refresh or stale:
            before = _registry or _cached()
            try:
                fresh = theme_service.load_registry(previous=before)
            except theme_source_checks.NothingReadError as exc:
                _unread = exc.reports
                raise
            _unread = {}
            _carry_dates(fresh, before)
            _keep(fresh, now)
            if _answered(fresh):
                theme_sync.stamp()
            _registry, _held_for = fresh, now
        return _registry


def _answered(registry: ThemeRegistry) -> bool:
    """Whether any source gave an answer this read, as opposed to every one failing and
    the listing being what an earlier read kept."""
    states = [report["state"] for report in registry.reports.values()]
    return not states or any(state not in theme_source_checks.BAD for state in states)


def _sources_now() -> dict[str, list[str]]:
    said = theme_sources.from_config(get_ini_config())
    return {"registries": list(said.registries), "repositories": list(said.repositories)}


def _cached() -> ThemeRegistry | None:
    """The last read, unless the configured sources have changed since."""
    try:
        held = json.loads(THEME_CACHE_PATH.read_text(encoding="utf-8"))
        if held.get("sources") != _sources_now():
            return None
        registry = ThemeRegistry()
        registry.restore(held)
        return registry
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a copy this build cannot read is read again
        logger.warning("Could not use the kept theme list; reading the sources",
                       exc_info=True)
        return None


def _keep(registry: ThemeRegistry, sources: dict[str, list[str]]) -> None:
    try:
        kept = {**registry.snapshot(), "sources": sources}
        THEME_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(THEME_CACHE_PATH, lambda out: json.dump(kept, out))
    except Exception:  # noqa: BLE001 - an unwritten copy costs the next start a read
        logger.warning("Could not keep the theme list", exc_info=True)


def _ref(entry: dict[str, Any]) -> str:
    return str(entry.get("ref") or "")


def _carry_dates(fresh: ThemeRegistry, before: ThemeRegistry | None) -> None:
    """A date the host would not give this time is the one it gave last, while the
    ref it dates is still the one on offer."""
    if before is None:
        return
    for key, entry in fresh.themes.items():
        known = before.themes.get(key) or {}
        if not entry.get("updated") and known.get("updated") and _ref(known) == _ref(entry):
            entry["updated"] = known["updated"]


def _trust(key: str, source: str, installed: bool, *, in_folder: bool = False
           ) -> dict[str, Any]:
    """Where a theme came from and whether the Owner has accepted it.

    `accepted` is what an installed theme is judged by: whether it is on the list. One
    not installed is judged by its address, as installing it will be. `install_accepted`
    is that second judgement for an installed theme too, which an update from an address
    it was not accepted from fails."""
    stock = theme_sources.is_stock(source)
    origin = accepted.theme_origin(stock=stock, in_folder=in_folder)
    from_here = accepted.theme_accepted(key, source, stock=stock, in_folder=in_folder)
    on_list = accepted.theme_runs(key) if installed else from_here
    config = get_ini_config()
    return {"origin": origin, "source": source,
            "source_name": theme_sources.name_of_source(config, source) if source else "",
            "accepted": on_list,
            "install_accepted": from_here}


def _described(registry: ThemeRegistry, active: str) -> list[dict[str, Any]]:
    found = registry.get_themes()
    try:
        updates = registry.check_for_updates(list(found))
    except Exception:  # noqa: BLE001 - an update check that fails is not a broken list
        logger.warning("Could not check themes for updates", exc_info=True)
        updates = {}

    out: list[dict[str, Any]] = []
    for key, entry in found.items():
        manifest = dict(entry.get("manifest") or {})
        info = dict(entry.get("registry_info") or {})
        installed = registry.is_installed(key)
        update = updates.get(key) or {}
        schema = (theme_service.load_theme_option_schema(key, registry)
                  if installed else None)
        out.append({
            "key": key,
            "name": theme_sources.title_of(key, info, manifest),
            "author": str(manifest.get("author") or ""),
            "description": str(manifest.get("description") or ""),
            "version": str(update.get("remote_version")
                           or manifest.get("version") or ""),
            "installed_version": str(update.get("installed_version") or ""),
            # What it needs to show itself: 1 is a desktop, 3 is a cabinet.
            "screens": manifest.get("supported_screens"),
            "layouts": list(theme_layouts.supported(manifest)),
            "url": str(info.get("theme_base_url") or info.get("url") or ""),
            "registry": ("" if info.get(theme_sources.NAMED_BY_MANIFEST)
                         else str(entry.get("source") or "")),
            # Where the picture is, resolved here rather than by each client: an
            # installed theme serves its own from /themes/, and one that is not has to
            # be fetched from where its manifest lives.
            "preview": _preview(key, manifest, info, installed),
            "preview_version": preview_version(registry, key),
            "previews": _previews(registry, key, manifest, info, installed),
            "change_log": str(manifest.get("change_log") or ""),
            "updated": str(entry.get("updated") or ""),
            "installed": installed,
            "active": key == active,
            "default_install": registry.installs_on_its_own(key),
            "update_available": bool(update.get("update_available")) and installed,
            "configurable": bool(schema and schema.get("options")),
            "attract_left_to_theme": (theme_service.attract_left_to_the_theme(key, registry)
                                      if installed else ""),
            "origin": "registry",
            "trust": _trust(key, str(entry.get("source") or ""), installed),
        })
    for key, manifest in registry.local_themes().items():
        schema = theme_service.load_theme_option_schema(key, registry)
        out.append({
            "key": key,
            "name": str(manifest.get("name") or key),
            "author": str(manifest.get("author") or ""),
            "description": str(manifest.get("description") or ""),
            "version": str(manifest.get("version") or ""),
            "installed_version": str(manifest.get("version") or ""),
            "screens": manifest.get("supported_screens"),
            "layouts": list(theme_layouts.supported(manifest)),
            "url": "",
            "registry": "",
            "preview": _preview(key, manifest, {}, True),
            "preview_version": preview_version(registry, key),
            "previews": _previews(registry, key, manifest, {}, True),
            "change_log": str(manifest.get("change_log") or ""),
            "updated": "",
            "installed": True,
            "active": key == active,
            "default_install": False,
            "update_available": False,
            "configurable": bool(schema and schema.get("options")),
            "attract_left_to_theme": theme_service.attract_left_to_the_theme(key, registry),
            "origin": "local",
            "trust": _trust(key, "", True, in_folder=True),
        })
    # Active first, then installed, then the rest - the order somebody scans in.
    out.sort(key=lambda one: (not one["active"], not one["installed"],
                              one["name"].lower()))
    return out


def _preview(key: str, manifest: dict, info: dict, installed: bool,
             layout: str = "") -> str:
    name = theme_layouts.picture(manifest, layout)
    if not name:
        return ""
    if name.startswith("http"):
        return name
    if installed:
        return f"/themes/{key}/{name}"
    base = ThemeRegistry._base_url(info)
    if not base:
        return ""
    return f"{ThemeRegistry._manifest_url(base, info).rsplit('/', 1)[0]}/{name}"


def _previews(registry: ThemeRegistry, key: str, manifest: dict, info: dict,
              installed: bool) -> dict[str, dict[str, str]]:
    """Each layout that has a picture of its own, as `{"url", "version"}`. A theme with one
    picture for every layout has none here: `preview` is its picture."""
    return {layout: {"url": _preview(key, manifest, info, installed, layout),
                     "version": preview_version(registry, key, layout)}
            for layout in theme_layouts.pictures(manifest)}


class _Picture(NamedTuple):
    installed: Path | None
    url: str
    release: str
    kept_as: str


def _picture(registry: ThemeRegistry, key: str, layout: str = "") -> _Picture | None:
    """Where a theme's preview for `layout` is: in its installed folder, else at `url` on
    its host."""
    entry = registry.get_themes().get(key)
    if entry is not None:
        manifest = dict(entry.get("manifest") or {})
        info = dict(entry.get("registry_info") or {})
        folder = registry.get_installed_folder(key)
    else:
        manifest = dict(registry.local_themes().get(key) or {})
        info, folder = {}, key
    name = theme_layouts.picture(manifest, layout)
    if not name:
        return None
    installed = None
    if folder and not name.startswith("http"):
        root = Path(registry.themes_dir, folder).resolve()
        candidate = (root / name).resolve()
        installed = candidate if candidate.is_relative_to(root) and candidate.is_file() else None
    if entry:
        url = _preview(key, manifest, info, False, layout)
    else:
        url = name if name.startswith("http") else ""
    own = layout in theme_layouts.pictures(manifest) and name != theme_layouts.lead(manifest)
    return _Picture(installed, url, str(manifest.get("version") or ""),
                    f"{key}#{layout}" if own else key)


def preview_version(registry: ThemeRegistry, key: str, layout: str = "") -> str:
    """What the address of a theme's preview carries as `v`, which changes when the
    picture does: a new release of a theme not installed, a new file for one that is."""
    picture = _picture(registry, key, layout)
    if picture is None:
        return ""
    if picture.installed is not None:
        return sized_media.version(picture.installed) or ""
    if not picture.url:
        return ""
    return theme_previews.version(picture.kept_as, picture.url, picture.release)


def preview(key: str, size: int | None, layout: str = "") -> sized_media.Served | None:
    """A theme's preview for `layout` at `size`, or None when it has none or cannot be had.
    Fetches a picture not yet kept, so not for a path that cannot wait."""
    registry = _known()
    picture = _picture(registry, key, layout) if registry is not None else None
    if picture is None:
        return None
    source = picture.installed
    if source is None and picture.url:
        source = theme_previews.path(picture.kept_as, picture.url, picture.release)
    return sized_media.served(source, size) if source is not None else None


def _readable(refresh: bool = False) -> ThemeRegistry:
    """The registry, or SourceUnavailableError in words."""
    try:
        return _loaded(refresh)
    except ThemeRegistryError as exc:
        raise SourceUnavailableError(why(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - a source that will not load is news
        raise SourceUnavailableError(why(exc)) from exc


def listing(refresh: bool = False) -> dict[str, Any]:
    """Active first, then installed, then the rest.

    `refresh` re-reads the sources. Without it the answer is the last read, which is right
    for a page that draws several times a minute, unless the configured sources have
    changed since: then they are read again.
    """
    registry = _readable(refresh)
    active = theme_service.get_active_theme()
    return {"active": active, "themes": _described(registry, active),
            "checked": theme_sync.checked_at()}


def _known() -> ThemeRegistry | None:
    """The last read, from memory or from disk. Never reaches the network."""
    with _lock:
        return _registry if _registry is not None else _cached()


def sources() -> dict[str, Any]:
    """What each configured source said when it was last read, in the order they are
    configured. A source never read, or added since, is Not checked.

    Does not read the sources: the page that draws this asks on every visit.
    """
    checks = theme_source_checks
    said = theme_sources.from_config(get_ini_config())
    known = _known()
    held = _unread or (known.reports if known is not None else {})

    config = get_ini_config()

    def row(address: str, kind: str) -> dict[str, Any]:
        found = held.get(address)
        if found is None or found["kind"] != kind:
            found = checks.waiting(address, kind)
        return {**found, "level": checks.level(found["state"]),
                "label": theme_sources.name_of_source(config, address)}

    return {"registries": [row(url, checks.LIST) for url in said.registries if url.strip()],
            "repositories": [row(url, checks.THEME) for url in said.repositories
                             if url.strip()],
            "checked": theme_sync.checked_at()}


def rename_source(address: str, name: str) -> dict[str, Any]:
    """Call a configured source `name`, or - given a blank - by what it is called without
    one. Raises NotFoundError for an address that is not configured."""
    where = theme_sources.parse(str(address or ""))
    key = where.url if where is not None else str(address or "").strip()
    if key not in _configured_kinds():
        raise service_errors.NotFoundError(t("error.themes.unknown_source"))
    if theme_sources.claims_stock_title(name) and not theme_sources.is_stock(key):
        raise service_errors.RefusedError(
            t("error.themes.reserved_title", name=theme_sources.clean_name(name)))
    store = get_ini_config()
    theme_sources.keep_names(store, {key: name})
    store.save()
    return {"address": key, "label": theme_sources.name_of_source(store, key)}


def _configured_kinds() -> dict[str, str]:
    """Every configured address, as written and as parsed, with the kind it is held as."""
    said = theme_sources.from_config(get_ini_config())
    held: dict[str, str] = {}
    for kind, urls in ((theme_source_checks.LIST, said.registries),
                       (theme_source_checks.THEME, said.repositories)):
        for url in urls:
            where = theme_sources.parse(url)
            held[url] = held[where.url if where else url] = kind
    return held


def _read_alone(address: str, kind: str) -> tuple[dict[str, Any], ThemeRegistry]:
    """A source read on its own, through the path the real read takes."""
    alone = theme_sources.ThemeSources(
        registries=(address,) if kind == theme_source_checks.LIST else (),
        repositories=(address,) if kind == theme_source_checks.THEME else ())
    registry = ThemeRegistry(sources=alone)
    try:
        registry.load_registry()
        if kind == theme_source_checks.THEME:
            registry.load_theme_manifests(dates=False)
    except ThemeRegistryError:
        pass  # every source failing is raised, and what each said is kept
    return registry.reports[address], registry


def check_source(address: str) -> dict[str, Any]:
    """What adding `address` would be, found out without adding it.

    Reads the address and says what it is: its kind, its state and the address it would
    be saved under. `addable` is false for the states Add refuses. Reads JSON only; no
    theme is downloaded.
    """
    checks = theme_source_checks
    where = theme_sources.parse(str(address or ""))
    if where is None:
        bad = checks.failed(str(address or "").strip(), checks.THEME, checks.NOT_A_THEME,
                            t("themes.source.bad_address"))
        return _checked(bad)
    held = _configured_kinds()
    if where.url in held:
        return _checked(checks.failed(where.url, held[where.url], checks.ALREADY_ADDED,
                                      t("themes.source.already_added")))

    kind = checks.LIST if where.is_file else checks.THEME
    report, read = _read_alone(where.url, kind)
    if (kind == checks.THEME and report["state"] == checks.NOT_A_THEME
            and report["reason"] == t("themes.source.no_manifest")):
        # No theme here, but a repository can be a list instead.
        file = theme_sources.list_file(where)
        added = file if file in held else _list_added_from(where, held)
        if added:
            return _checked(checks.failed(added, checks.LIST, checks.ALREADY_ADDED,
                                          t("themes.source.already_added")))
        as_list, listed = _read_alone(file, checks.LIST)
        if as_list["state"] == checks.READY:
            report, read = as_list, listed
    return _checked(report, _offered_elsewhere(report, read))


def _list_added_from(where: theme_sources.Address, held: dict[str, str]) -> str:
    """The configured list that is this repository's `themes.json`, on whatever branch."""
    for address, kind in held.items():
        other = theme_sources.parse(address)
        if (kind == theme_source_checks.LIST and other is not None and other.repo == where.repo
                and address.endswith(f"/{theme_sources.LIST_FILE}")):
            return address
    return ""


def _offered_elsewhere(report: dict[str, Any], read: ThemeRegistry) -> dict[str, Any]:
    """What a source about to be added would meet among the themes already offered."""
    known = _known()
    sources_in_use = theme_sources.from_config(get_ini_config())
    offered = {key: str(entry.get("source") or "")
               for key, entry in (known.themes.items() if known is not None else [])}
    if report["kind"] == theme_source_checks.THEME:
        name = report["name"]
        if name not in offered:
            return {}
        if offered[name] in sources_in_use.repositories:
            return {"state": theme_source_checks.NOT_USED,
                    "reason": t("themes.source.not_used", name=name)}
        return {"replaces": [name]}
    # A list is added below every source there is, so each name it shares is one it loses.
    return {"took": [key for key in read.themes_index if key in offered]}


def _checked(report: dict[str, Any], more: dict[str, Any] | None = None) -> dict[str, Any]:
    checks = theme_source_checks
    out = {**report, **(more or {})}
    out.setdefault("replaces", [])
    out["level"] = checks.level(out["state"])
    out["group"] = "registries" if out["kind"] == checks.LIST else "repositories"
    out["addable"] = out["state"] not in checks.REFUSED
    return out


def install(key: str) -> dict[str, Any]:
    """One call for both. Installing over an existing copy is what an update is, and two
    endpoints doing it would be two names for one act."""
    registry = _readable()
    offered = registry.themes.get(key)
    offered_from = str((offered or {}).get("source") or "")
    if offered is not None and not accepted.theme_accepted(
            key, offered_from, stock=theme_sources.is_stock(offered_from)):
        raise service_errors.RefusedError(t("error.themes.not_trusted", key=key))
    try:
        theme_service.install_theme(registry, key)
    except ThemeRegistryError as exc:
        raise InstallFailedError(why(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise InstallFailedError(why(exc)) from exc
    return {"key": key, "installed": registry.is_installed(key)}


def remove(key: str) -> dict[str, Any]:
    registry = _readable()
    if not registry.is_installed(key):
        raise service_errors.NotFoundError(t("error.themes.not_installed", key=(key)))
    if key == theme_service.get_active_theme():
        # Refused rather than allowed with a warning: the frontend would come up with
        # no theme at all, and the way out of that is a config file.
        raise service_errors.RefusedError(
            t("error.themes.still_active", key=(key)))
    try:
        theme_service.delete_theme(registry, key)
    except ThemeRegistryError as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise RemoveFailedError(why(exc)) from exc
    accepted.forget_theme(key)
    return {"key": key, "installed": False}


def activate(key: str) -> dict[str, Any]:
    """Written to the config. It takes effect when the frontend next starts, which the
    caller is expected to say - this endpoint changes a setting rather than restarting
    anything."""
    key = str(key or "").strip()
    if not key:
        raise service_errors.RefusedError(t("error.themes.none_named"))
    if not _readable().is_installed(key):
        raise service_errors.RefusedError(t("error.themes.not_installed", key=key))
    if not accepted.theme_runs(key):
        raise service_errors.RefusedError(t("error.themes.not_trusted", key=key))
    theme_service.set_active_theme(key)
    return {"active": theme_service.get_active_theme()}


def unaccepted() -> dict[str, Any]:
    """The installed themes that are stopped for want of being accepted, by key and name.
    Reads the themes folder alone: no registry, so no network."""
    found = []
    for key in accepted.unaccepted_themes():
        try:
            manifest = json.loads((paths.THEMES_DIR / key / "manifest.json")
                                  .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {}
        name = manifest.get("name") if isinstance(manifest, dict) else ""
        found.append({"key": key, "name": str(name or key)})
    return {"themes": found}


def accept(key: str) -> dict[str, Any]:
    """Accept a theme from the address this install knows it by, found here and not
    taken from the caller: a theme in the themes folder that no source offers is accepted
    as found there. Raises NotFoundError for a key nothing offers."""
    key = str(key or "").strip()
    registry = _readable()
    offered = registry.themes.get(key)
    if offered is not None:
        source, in_folder = str(offered.get("source") or ""), False
    elif key in registry.local_themes():
        source, in_folder = "", True
    else:
        raise service_errors.NotFoundError(t("error.themes.not_offered", key=key))
    accepted.accept_theme(key, source)
    return _trust(key, source, registry.is_installed(key), in_folder=in_folder)


def options(key: str) -> dict[str, Any]:
    """The schema a theme declares, and what it is currently set to.

    Its own shape rather than the install's config schema: these belong to the theme,
    live in its `theme.json`, and a theme can declare a control this install has never
    heard of.
    """
    registry = _readable()
    if not registry.is_installed(key):
        raise service_errors.NotFoundError(t("error.themes.not_installed", key=(key)))
    schema = theme_service.load_theme_option_schema(key, registry)
    if not schema:
        return {"key": key, "title": "", "description": "", "options": [], "values": {}}
    return {"key": key, "title": schema.get("title", ""),
            "description": schema.get("description", ""),
            "options": schema.get("options") or [],
            "values": theme_service.get_theme_option_values(key, registry)}


def save_options(key: str, values: dict[str, Any]) -> dict[str, Any]:
    """Kept beside the config rather than inside the theme.

    The package is deleted by an update, so values written into it were reset by the
    next one - every time, with no warning. The theme still declares what its options
    *are*; this is only what somebody chose.
    """
    registry = _readable()
    if not registry.is_installed(key):
        raise service_errors.NotFoundError(t("error.themes.not_installed", key=(key)))
    try:
        theme_service.save_theme_option_values(key, dict(values or {}), registry)
    except ValueError as exc:
        raise service_errors.RefusedError(why(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise service_errors.RefusedError(why(exc)) from exc
    return {"key": key, "values": theme_service.get_theme_option_values(key, registry)}
