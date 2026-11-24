"""Installing a theme, and remembering enough to update or remove it later.

A theme the user edited is not silently overwritten: what was installed is recorded
so an update can tell its own files from theirs.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import zipfile
from io import BytesIO

from common.config.paths import ASIDE_SUFFIX
from common.i18n import t
from common.online import theme_refs
from common.online.theme_registry_client import ThemeRegistryError
from common.values import newer_version, parse_version


class ThemeInstallStore:
    """What was installed and from where, so an update can tell our files from the user's."""

    def __init__(self, themes_dir: str) -> None:
        self.themes_dir = themes_dir
        os.makedirs(self.themes_dir, exist_ok=True)

    def _path(self, folder: str) -> str:
        """A folder inside the themes folder, never outside it: a key comes from a source
        the Owner may not have read."""
        if not folder or folder in (".", "..") or os.path.basename(folder) != folder \
                or "\\" in folder:
            raise ValueError(t("error.themes.folder_name_unusable", folder=folder))
        return os.path.join(self.themes_dir, folder)

    @staticmethod
    def repo_name(base_url: str) -> str:
        return base_url.rstrip("/").split("/")[-1]

    @staticmethod
    def is_version_newer(remote: str, local: str) -> bool:
        return newer_version(parse_version(remote), parse_version(local))

    @staticmethod
    def build_zip_url(base_url: str, ref: str = "HEAD") -> str:
        """The source archive for a ref. `HEAD` is left alone rather than rewritten to
        master, which is wrong on a repo whose default branch is `main`."""
        return f"{base_url}/archive/{theme_refs.bare_ref(ref)}.zip"

    def installed_folder(self, theme_key: str, base_url: str | None = None) -> str | None:
        if os.path.isdir(os.path.join(self.themes_dir, theme_key)):
            return theme_key

        if not base_url:
            return None

        repo_name = self.repo_name(base_url)
        for folder in os.listdir(self.themes_dir):
            # A set-aside folder is the previous install, never the current one - and
            # it shares the theme's name, so the prefix match would otherwise claim it.
            if folder.startswith(repo_name) and not folder.endswith(ASIDE_SUFFIX):
                return folder
        return None

    def installed_version(self, theme_key: str, base_url: str | None = None) -> str | None:
        folder_name = self.installed_folder(theme_key, base_url)
        if not folder_name:
            return None

        manifest_path = os.path.join(self.themes_dir, folder_name, "manifest.json")
        if os.path.exists(manifest_path):
            with open(manifest_path, encoding="utf-8") as handle:
                data = json.load(handle)
                return data.get("version")
        return None

    def _set_aside(self, theme_key: str) -> str | None:
        """Move an installed theme out of the way. Returns where it went, or None."""
        installed = self._path(theme_key)
        if not os.path.lexists(installed):
            return None
        aside = self._path(theme_key + ASIDE_SUFFIX)
        if os.path.lexists(aside):
            shutil.rmtree(aside, ignore_errors=True)
            if os.path.lexists(aside):
                os.remove(aside)
        os.rename(installed, aside)
        with contextlib.suppress(OSError):
            os.utime(aside)
        return aside

    def install_zip(self, theme_key: str, base_url: str, zip_data: BytesIO) -> None:
        """Install over any existing copy, without deleting anything.

        Two ways an installer can destroy a folder the user owns: removing every entry whose
        name merely *starts with* the repo's, so installing `Reference` takes
        `Reference-mine` with it, and rmtree'ing the destination outright, so a registry
        key colliding with a local theme erases it.

        So the folder being replaced moves to <name>.previous, and the folder promoted is
        the one this extraction actually created - found by diffing the directory rather
        than by matching a name, since a name match cannot tell those apart.
        """
        aside = self._set_aside(theme_key)
        before = set(os.listdir(self.themes_dir))
        try:
            with zipfile.ZipFile(zip_data) as archive:
                archive.extractall(self.themes_dir)
            extracted = self._extracted_folder(before)
            if extracted is None:
                raise ThemeRegistryError(t("error.themes.empty_download",
                                           key=theme_key))
            os.rename(self._path(extracted), self._path(theme_key))
        except Exception:
            # Leave the user with their old theme rather than with neither.
            if aside and not os.path.lexists(self._path(theme_key)):
                os.rename(aside, self._path(theme_key))
            raise

    def _extracted_folder(self, before: set[str]) -> str | None:
        appeared = sorted(set(os.listdir(self.themes_dir)) - before)
        return next((name for name in appeared if os.path.isdir(self._path(name))), None)

    def delete(self, folder: str) -> None:
        shutil.rmtree(os.path.join(self.themes_dir, folder))
