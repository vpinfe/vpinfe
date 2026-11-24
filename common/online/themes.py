"""Finding themes to install, from every source the user has configured."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
from collections.abc import Collection
from io import BytesIO
from typing import Any

import requests

from common.config.paths import ASIDE_SUFFIX, CONFIG_DIR, THEMES_FOLDER, get_ini_config
from common.i18n import t
from common.online import (
    theme_dates,
    theme_layouts,
    theme_refs,
    theme_source_checks,
    theme_sources,
)
from common.online.theme_installer import ThemeInstallStore
from common.online.theme_level import MIN_VERSION_KEY
from common.online.theme_registry_client import ThemeRegistryClient, ThemeRegistryError
from common.security import accepted
from common.security.http_client import get_json, get_text
from common.values import newer_version, parse_version
from common.vpinfe_version import get_version


class ThemeVersionError(ThemeRegistryError):
    """A theme that needs a newer VPinFE than the one asked to install it."""

class ThemeNotAcceptedError(ThemeRegistryError):
    """A theme from an address the Owner has not accepted it from."""


logger = logging.getLogger("vpinfe.common.online.themes")


class ThemeRegistry:
    """Every theme available to install, gathered from all configured sources."""

    def __init__(self, timeout: int = 10,
                 sources: theme_sources.ThemeSources | None = None) -> None:
        # Read at load time, not here: constructing a registry should not touch the disk,
        # and every test that builds one would otherwise need a config file.
        self.sources = sources
        self.timeout = timeout
        self.client = ThemeRegistryClient(timeout=timeout)
        self.themes_index: dict[str, Any] = {}
        self.origins: dict[str, str] = {}
        self.themes: dict[str, Any] = {}
        # What each source said on the last read, by the address it is configured under.
        self.reports: dict[str, dict[str, Any]] = {}

        self.base_dir = str(CONFIG_DIR)
        self.themes_dir = os.path.join(self.base_dir, THEMES_FOLDER)
        self.store = ThemeInstallStore(self.themes_dir)

    # =========================================================
    # NETWORK
    # =========================================================

    def _fetch_json(self, url: str) -> dict:
        return self.client.fetch_json(url)

    def _fetch_any(self, url: str) -> Any:
        """JSON that need not be an object - a host's list of commits."""
        return get_json(url, timeout=self.timeout)

    def _exists(self, url: str) -> bool:
        """Whether the host has a page at `url`. Only an answer of nothing there says no."""
        try:
            get_text(url, timeout=self.timeout)
        except requests.HTTPError as exc:
            return getattr(exc.response, "status_code", 0) not in (404, 410)
        except Exception:  # noqa: BLE001 - a host that cannot be asked has not said no
            return True
        return True

    def _download_zip(self, url: str) -> BytesIO:
        return self.client.download_zip(url)

    # =========================================================
    # REGISTRY
    # =========================================================

    def _catalog(self, url: str, previous: ThemeRegistry | None = None) -> dict:
        """The themes a registry names. A registry that cannot be read names what it
        named when it last could, with the reason logged and kept in `reports`.

        One unreachable or malformed source must not cost the user the others - that is
        the whole point of there being more than one.
        """
        checks = theme_source_checks
        try:
            data = self._fetch_json(url)
        except Exception as exc:
            logger.error("Theme registry %s: %s", url, exc)
            state, reason = checks.classify(exc, url)
            return self._catalog_kept(url, previous, state, reason)
        themes = data.get("themes")
        if not isinstance(themes, dict):
            logger.error("Theme registry %s: no themes object, so nothing to read", url)
            return self._catalog_kept(url, previous, checks.NOT_A_THEME,
                                      t("themes.source.no_themes"))
        self.reports[url] = checks.listed(url, themes)
        return themes

    def _catalog_kept(self, url: str, previous: ThemeRegistry | None, state: str,
                      reason: str) -> dict:
        kept: dict = {}
        if previous is not None:
            kept = {key: entry for key, entry in previous.themes_index.items()
                    if previous.origins.get(key) == url}
        report = theme_source_checks.listed(url, kept)
        report.update(state=state, reason=reason)
        self.reports[url] = report
        return kept

    def load_registry(self, previous: ThemeRegistry | None = None) -> None:
        """`previous` is the last read, which a source that fails now still answers from."""
        sources = self.sources
        if sources is None:
            sources = theme_sources.from_config(get_ini_config())

        # Repositories first: naming one repo is a more specific act than subscribing to
        # a catalog, so a user's own theme wins a name collision with a published one.
        # Keyed by url until its manifest says what it is really called.
        self.sources = sources
        self.reports = {url: theme_source_checks.waiting(url, theme_source_checks.THEME)
                        for url in sources.repositories if url.strip()}
        parts = [(url, {url: theme_sources.repository_entry(url)})
                 for url in sources.repositories if url.strip()]
        parts += [(url, self._catalog(url, previous))
                  for url in sources.registries if url.strip()]

        self.origins = {}
        lost: dict[str, list[str]] = {}
        index = theme_sources.merge(parts, self.origins, lost)
        for origin, names in lost.items():
            self.reports[origin]["took"] = names
        if not index and parts:
            raise theme_source_checks.NothingReadError(t("error.themes.sources_failed"),
                                                       self.reports)

        self.themes_index = index

    @staticmethod
    def _base_url(entry: dict) -> str:
        """Where a theme lives, under either registry shape.

        The new registry holds a name and a url and nothing else, so that registration
        happens once and every later release is a merge in the author's own repo. The
        old one is still read unchanged, which is what lets this ship before the
        registry moves.
        """
        return str(entry.get("url") or entry.get("theme_base_url") or "").strip()

    @staticmethod
    def _ref(entry: dict) -> str:
        """The ref a list entry pins, or `HEAD` where it names none."""
        return str(entry.get("ref") or "").strip() or "HEAD"

    @staticmethod
    def _manifest_url(base_url: str, entry: dict) -> str:
        """Where a theme's manifest is: at its pinned ref, else wherever the old list
        shape names it, else on the default branch."""
        if str(entry.get("ref") or "").strip():
            return theme_refs.raw_url(base_url, ThemeRegistry._ref(entry), "manifest.json")
        legacy_url = str(entry.get("theme_manifest_url") or "").strip()
        return legacy_url or theme_refs.raw_url(base_url, "HEAD", "manifest.json")

    def load_theme_manifests(self, default_only: bool = False, *, dates: bool = True,
                             previous: ThemeRegistry | None = None,
                             include: Collection[str] = ()) -> None:
        """`dates=False` leaves every `updated` empty. `previous` is the last read.
        `include` names themes read although `default_only` would leave them out."""
        # Reset loaded themes for this pass.
        self.themes = {}

        theme_jobs = []
        for theme_key, theme_info in self.themes_index.items():
            if default_only and theme_key not in include and not theme_sources.installs_on_its_own(
                    theme_info, self.origins.get(theme_key, "")):
                continue
            base_url = self._base_url(theme_info)
            if not base_url:
                continue
            theme_jobs.append((theme_key, theme_info, base_url))

        def _load_one(job: tuple[str, dict, str]) -> tuple[Any, ...]:
            theme_key, theme_info, base_url = job
            manifest = self._fetch_json(self._manifest_url(base_url, theme_info))
            self._validate_manifest(theme_key, manifest)
            # Only now can a repository say what it is called, so the key settles here.
            key = theme_sources.key_of(theme_key, theme_info, manifest)
            ref = self._ref(theme_info)
            updated = self._dated(key, base_url, ref, previous) if dates else ""
            return key, theme_info, manifest, ref, updated

        unreadable: dict[str, dict[str, str]] = {}
        kept: dict[str, dict] = {}

        # Network-bound workload: parallelize manifest fetches. Submitted all at once, then
        # collected in source order rather than completion order - a repository's name is
        # only known once its manifest lands, so this is where two sources can turn out to
        # mean the same theme, and which one wins must not depend on who answered first.
        max_workers = min(8, max(1, len(theme_jobs)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {job[0]: pool.submit(_load_one, job) for job in theme_jobs}
            for provisional, future in futures.items():
                origin = self.origins.get(provisional, "")
                try:
                    theme_key, theme_info, manifest, ref, updated = future.result()
                    held = theme_key in self.themes
                    self._note_theme(origin, theme_key, manifest, used=not held)
                    if held:
                        logger.warning("Two sources both provide '%s' - keeping the first, "
                                       "ignoring %s", theme_key, self._base_url(theme_info))
                        self._note_took(origin, theme_key)
                        continue
                    twin = next((k for k in self.themes if k.lower() == theme_key.lower()), None)
                    if twin:
                        logger.warning("'%s' and '%s' differ only in case, so both install",
                                       theme_key, twin)
                    self.themes[theme_key] = {
                        "registry_info": theme_info,
                        "source": self.origins.get(provisional, ""),
                        "manifest": manifest,
                        "ref": ref,
                        "updated": updated,
                    }
                except Exception as e:
                    logger.error("%s: %s", provisional, e)
                    self._note_failure(origin, provisional, e, unreadable)
                    self._keep_last_good(previous, origin, provisional, kept)

        # After every live read has had its turn, so a theme a failing source used to
        # offer never shadows the same name from a source that answered.
        for key, entry in kept.items():
            self.themes.setdefault(key, entry)

        for origin, names in unreadable.items():
            if (self.reports.get(origin) or {}).get("state") == theme_source_checks.READY:
                self._note(origin, state=theme_source_checks.SOME_UNREADABLE,
                           reason=theme_source_checks.unreadable(names))

    def _keep_last_good(self, previous: ThemeRegistry | None, origin: str,
                        provisional: str, kept: dict[str, dict]) -> None:
        """A theme that cannot be read now stays listed as it was when it last could.
        Added to `kept`, which the caller merges once every source has been read."""
        if previous is None:
            return
        single = self._is_single(origin)
        before = previous.reports.get(origin) or {}
        was = theme_source_checks.THEME if single else theme_source_checks.LIST
        if before.get("kind", was) != was:
            return
        for key, entry in previous.themes.items():
            if entry.get("source") != origin or not (single or key == provisional):
                continue
            kept.setdefault(key, dict(entry))
            if single and before.get("kind") == theme_source_checks.THEME:
                self._note(origin, name=before.get("name") or key,
                           version=before.get("version") or "")
            return

    def _note(self, origin: str, **fields: Any) -> None:
        """Add to what a source said. A registry built without reading its sources has no
        reports, and nothing to add to."""
        report = self.reports.get(origin)
        if report is not None:
            report.update(fields)

    def _is_single(self, origin: str) -> bool:
        return (self.reports.get(origin) or {}).get("kind") == theme_source_checks.THEME

    def _note_took(self, origin: str, name: str) -> None:
        report = self.reports.get(origin)
        if report is not None and name not in report["took"]:
            report["took"].append(name)

    def _note_theme(self, origin: str, name: str, manifest: dict, *, used: bool) -> None:
        """A single theme that read: what it is called, and whether it can run here."""
        if not self._is_single(origin):
            return
        checks = theme_source_checks
        needs = self._needs_newer(manifest)
        state, reason = checks.READY, ""
        if not used:
            state, reason = checks.NOT_USED, t("themes.source.not_used", name=name)
        elif needs:
            state = checks.NEEDS_VPINFE
            reason = t("themes.source.needs_version", name=name, needs=needs)
        self._note(origin, name=name, version=str(manifest.get("version") or ""),
                   state=state, reason=reason, needs=needs)

    def _note_failure(self, origin: str, provisional: str, exc: BaseException,
                      unreadable: dict[str, dict[str, str]]) -> None:
        report = self.reports.get(origin)
        if report is None:
            return
        checks = theme_source_checks
        base = self._base_url(self.themes_index.get(provisional) or {})
        state, reason = checks.classify(exc, base)
        if report["kind"] == checks.LIST:
            unreadable.setdefault(origin, {})[provisional] = reason
            return
        if state == checks.NOT_FOUND and self._exists(base):
            # The repository is there and the file in it is not: that is not a theme.
            state, reason = checks.NOT_A_THEME, t("themes.source.no_manifest")
        self._note(origin, state=state, reason=reason)

    def _needs_newer(self, manifest: dict) -> str:
        """The VPinFE a theme asks for when this one is older, else empty."""
        needs = parse_version(manifest.get(MIN_VERSION_KEY))
        running = parse_version(get_version())
        if needs and running and newer_version(needs, running):
            return str(manifest[MIN_VERSION_KEY])
        return ""

    def _dated(self, key: str, base_url: str, ref: str,
               previous: ThemeRegistry | None) -> str:
        before = (previous.themes.get(key) if previous is not None else None) or {}
        kept = str(before.get("updated") or "")
        if kept and theme_refs.is_tag(ref) and before.get("ref") == ref:
            return kept
        return theme_dates.committed_at(base_url, ref, self._fetch_any)

    # =========================================================
    # VALIDATION
    # =========================================================

    def _validate_manifest(self, theme_key: str, manifest: dict) -> None:
        required_fields = ["name", "version", "author", "description"]
        # `layouts` carries what `type` and `preview_image` said in 2.x.
        if "layouts" not in manifest:
            required_fields += ["preview_image", "type"]
        # `windows` names the screens; `supported_screens` only ever counted them. A
        # theme has to say one or the other, and a 2.x theme said the count.
        if "windows" not in manifest:
            required_fields.append("supported_screens")

        missing = [field for field in required_fields if field not in manifest]
        if missing:
            raise theme_source_checks.ManifestError(
                f"{theme_key} missing required field '{missing[0]}'", missing)

        why = theme_layouts.problem(manifest)
        if why:
            raise theme_source_checks.ManifestError(f"{theme_key} {why}", bad_layouts=True)
        if "layouts" not in manifest and manifest["type"] not in ("desktop", "cab", "both"):
            raise theme_source_checks.ManifestError(
                f"{theme_key} invalid type '{manifest['type']}'")

    # =========================================================
    # INSTALL HELPERS
    # =========================================================

    def _get_repo_name(self, base_url: str) -> str:
        return self.store.repo_name(base_url)

    def _get_installed_version(self, theme_key: str) -> str | None:
        theme_data = self.themes.get(theme_key)
        if not theme_data:
            return None

        folder_name = self.get_installed_folder(theme_key)
        if not folder_name:
            return None

        return self.store.installed_version(theme_key, self._base_url(theme_data["registry_info"]))

    def _is_version_newer(self, remote: str, local: str) -> bool:
        return self.store.is_version_newer(remote, local)

    def _build_zip_url(self, base_url: str, ref: str = "HEAD") -> str:
        return self.store.build_zip_url(base_url, ref)

    # =========================================================
    # INSTALLATION
    # =========================================================

    def installs_on_its_own(self, theme_key: str) -> bool:
        theme = self.themes.get(theme_key) or {}
        return theme_sources.installs_on_its_own(theme.get("registry_info") or {},
                                                 str(theme.get("source") or ""))

    def auto_install_defaults(self) -> None:
        """Auto-install the themes the VPinFE Theme List marks `default_install`."""
        for key in self.themes:
            if self.installs_on_its_own(key):
                self.install_theme(key)


    def install_theme(self, theme_key: str, force: bool = False) -> None:
        if theme_key not in self.themes:
            raise ThemeRegistryError(t("error.themes.not_offered", key=theme_key))

        theme_data = self.themes[theme_key]
        source = str(theme_data.get("source") or "")
        stock = theme_sources.is_stock(source)
        if not accepted.theme_accepted(theme_key, source, stock=stock):
            raise ThemeNotAcceptedError(t("error.themes.not_trusted", key=theme_key))
        manifest = theme_data["manifest"]
        base_url = self._base_url(theme_data["registry_info"])
        ref = self._ref(theme_data["registry_info"])

        remote_version = manifest["version"]
        local_version = self._get_installed_version(theme_key)

        # Before anything is downloaded, and before the up-to-date check: a theme that
        # needs a newer build than this one is not an update, and installing it over a
        # working theme leaves the frontend rendering against a contract this build does
        # not serve.
        needs = self._needs_newer(manifest)
        if needs:
            raise ThemeVersionError(t("error.themes.needs_newer_vpinfe", key=theme_key,
                                      needs=needs, running=get_version()))

        if not force and local_version:
            if not self._is_version_newer(remote_version, local_version):
                logger.debug("%s already up to date (%s)", theme_key, local_version)
                return

        logger.info("Installing %s v%s", theme_key, remote_version)

        zip_url = self._build_zip_url(base_url, ref)
        zip_data = self._download_zip(zip_url)

        self.store.install_zip(theme_key, base_url, zip_data)
        accepted.note_theme_installed(theme_key, source, stock=stock)

        logger.info("Installed %s", theme_key)


    # =========================================================
    # UPDATE & STATUS
    # =========================================================

    def check_for_updates(self, theme_keys: list[str] | None = None) -> dict[str, dict]:
        if theme_keys is None:
            theme_keys = list(self.themes.keys())

        updates = {}
        for key in theme_keys:
            if key not in self.themes:
                continue

            remote_version = self.themes[key]["manifest"]["version"]
            local_version = self._get_installed_version(key)

            updates[key] = {
                "installed_version": local_version,
                "remote_version": remote_version,
                "update_available": (
                    local_version is None or self._is_version_newer(remote_version, local_version)
                )
            }

        return updates

    def is_installed(self, theme_key: str) -> bool:
        return (self._get_installed_version(theme_key) is not None
                or theme_key in self.local_themes())

    def local_themes(self) -> dict[str, dict]:
        """{folder: manifest} for every theme in the themes folder that no source's
        theme accounts for."""
        claimed = {self.get_installed_folder(key) for key in self.themes}
        found: dict[str, dict] = {}
        for folder in sorted(os.listdir(self.themes_dir)):
            if folder in claimed or folder.endswith(ASIDE_SUFFIX) or folder.startswith("."):
                continue
            try:
                with open(os.path.join(self.themes_dir, folder, "manifest.json"),
                          encoding="utf-8") as handle:
                    manifest = json.load(handle)
            except (OSError, ValueError):
                continue
            if isinstance(manifest, dict):
                found[folder] = manifest
        return found

    def get_installed_folder(self, theme_key: str) -> str | None:
        """
        Returns the actual folder name under `frontend_themes/` for the given theme.
        None if theme is not installed.
        """
        # Check for exact theme_key match first (post-rename)
        theme_data = self.themes.get(theme_key)
        base_url = self._base_url(theme_data["registry_info"]) if theme_data else None
        return self.store.installed_folder(theme_key, base_url)

    # =========================================================
    # DELETE
    # =========================================================

    def delete_theme(self, theme_key: str) -> None:
        """Delete an installed theme. Raises for one the VPinFE Theme List installs itself."""
        if self.installs_on_its_own(theme_key):
            raise ThemeRegistryError(t("error.themes.default_stays", key=theme_key))

        folder = self.get_installed_folder(theme_key)
        if folder:
            self.store.delete(folder)
        else:
            raise ThemeRegistryError(t("error.themes.not_installed", key=theme_key))

    # =========================================================
    # GETTERS
    # =========================================================

    def get_themes(self) -> dict[str, Any]:
        return self.themes

    def snapshot(self) -> dict[str, Any]:
        """What the sources said, in a form that survives a restart."""
        return {"themes_index": self.themes_index, "origins": self.origins,
                "reports": self.reports, "themes": self.themes}

    def restore(self, held: dict[str, Any]) -> None:
        self.themes_index = dict(held.get("themes_index") or {})
        self.origins = dict(held.get("origins") or {})
        self.reports = dict(held.get("reports") or {})
        self.themes = dict(held.get("themes") or {})


# =============================================================
# MAIN TEST
# =============================================================

def main() -> None:
    logger.debug("Initializing Theme Manager...")

    registry = ThemeRegistry()

    registry.load_registry()
    registry.load_theme_manifests()

    logger.debug("Themes directory: %s", registry.themes_dir)

    logger.debug("Loaded Themes and Installation Status:")
    for key in registry.get_themes():
        installed_status = "Installed" if registry.is_installed(key) else "Not installed"
        folder_name = registry.get_installed_folder(key)
        logger.debug(" - %s (%s) -> folder: %s", key, installed_status, folder_name)

    logger.debug("Auto installing default themes...")
    registry.auto_install_defaults()

    logger.debug("Checking for updates...")
    updates = registry.check_for_updates()
    for key, info in updates.items():
        status = "UPDATE AVAILABLE" if info["update_available"] else "Up to date"
        logger.info(
            "%s: %s (installed: %s, remote: %s)",
            key,
            status,
            info['installed_version'],
            info['remote_version'],
        )

    logger.debug("After Loaded Themes and Installation Status:")
    for key in registry.get_themes():
        installed_status = "Installed" if registry.is_installed(key) else "Not installed"
        folder_name = registry.get_installed_folder(key)
        logger.debug(" - %s (%s) -> folder: %s", key, installed_status, folder_name)

    logger.info("Done.")


if __name__ == "__main__":
    main()
