"""An upgraded install keeps its themes and runs the official ones on their 3.0 versions.

2.x's folder and ini are never written, which is what a person going back to 2.x depends on.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from unittest import mock

from common.config.config_store import ConfigStore
from common.config.paths import ASIDE_SUFFIX, THEMES_FOLDER
from common.online import (
    theme_seed,  # noqa: F401 - registers the seed
    theme_service,
    theme_sources,
    theme_upgrade,
    themes,
)
from common.release import migration_ledger
from common.settings import config_service
from tests.support.library import TempTree

STOCK = theme_sources.stock_list()
RUNNING = "3.0.0"


def _manifest(version: str, minimum: str | None = None) -> dict:
    found = {"name": "x", "version": version}
    if minimum:
        found["min_vpinfe"] = minimum
    return found


def _install(folder: Path, version: str, minimum: str | None = None) -> Path:
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps(_manifest(version, minimum)),
                                          encoding="utf-8")
    (folder / "theme.js").write_text(f"// {folder.name} {version}", encoding="utf-8")
    return folder


def _archive(version: str) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("theme-master/manifest.json",
                         json.dumps(_manifest(version, "3.0")))
        archive.writestr("theme-master/theme.js", f"// 3.0 build {version}")
    buffer.seek(0)
    return buffer


def _snapshot(folder: Path) -> str:
    """Every path under `folder` with its bytes, as one digest."""
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        digest.update(path.relative_to(folder).as_posix().encode())
        if path.is_file() and not path.is_symlink():
            digest.update(path.read_bytes())
    return digest.hexdigest()


class _Base(TempTree):
    versions: dict[str, str]

    def setUp(self) -> None:
        super().setUp()
        self.config_file = self.root / "vpinfe.json"
        self.themes = self.root / THEMES_FOLDER
        for patcher in (
                mock.patch.object(config_service, "get_ini_config",
                                  lambda: ConfigStore(self.config_file)),
                mock.patch.object(theme_service, "VPINFE_INI_PATH", self.config_file),
                mock.patch.object(themes, "CONFIG_DIR", self.root),
                mock.patch.object(themes, "get_version", return_value=RUNNING),
                mock.patch.object(theme_upgrade, "get_version", return_value=RUNNING)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ledger = mock.patch.object(migration_ledger, "record").start()
        self.addCleanup(mock.patch.stopall)

    def registry(self, *offered: tuple[str, str], source: str = STOCK) -> themes.ThemeRegistry:
        registry = themes.ThemeRegistry()
        registry.themes = {
            key: {"registry_info": {"url": f"https://example.test/{key}"}, "source": source,
                  "manifest": _manifest(version, "3.0"), "ref": "HEAD"}
            for key, version in offered}
        registry._download_zip = lambda url, max_retries=3: _archive(  # type: ignore[method-assign, misc]
            self.versions[url.rsplit("/", 3)[-3]])
        self.versions = dict(offered)
        return registry

    def choose(self, active: str, **options: dict) -> None:
        config_service.set_values({"themes": {"active": active}})
        if options:
            config_service.put_theme_options(options)

    def active(self) -> str:
        return theme_service.get_active_theme()

    def version_of(self, name: str) -> str:
        return json.loads((self.themes / name / "manifest.json").read_text())["version"]


class SeedTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.old = self.root / "themes"
        _install(self.old / "Revolution", "1.2.11")
        _install(self.old / "Slider Video", "1.0.0")
        _install(self.old / f"Revolution{ASIDE_SUFFIX}", "1.2.0")
        (self.root / "vpinfe.ini").write_text("[Settings]\ntheme = Slider Video\n",
                                              encoding="utf-8")

    def test_a_converted_ini_brings_its_themes_along(self) -> None:
        before_ini = (self.root / "vpinfe.ini").read_bytes()
        before_themes = _snapshot(self.old)

        ConfigStore(self.config_file)

        self.assertEqual(sorted(p.name for p in self.themes.iterdir()),
                         ["Revolution", "Slider Video"])
        self.assertEqual(self.version_of("Revolution"), "1.2.11")
        self.assertEqual(_snapshot(self.old), before_themes)
        self.assertEqual((self.root / "vpinfe.ini").read_bytes(), before_ini)

    def test_a_converted_file_does_not_copy_again(self) -> None:
        ConfigStore(self.config_file)
        (self.themes / "Revolution").rename(self.root / "removed")

        ConfigStore(self.config_file)

        self.assertFalse((self.themes / "Revolution").exists())

    def test_a_symlinked_theme_stays_a_symlink(self) -> None:
        clone = _install(self.root / "clone" / "Mine", "1.0.0")
        (self.old / "Mine").symlink_to(clone, target_is_directory=True)

        ConfigStore(self.config_file)

        self.assertTrue((self.themes / "Mine").is_symlink())
        self.assertEqual((self.themes / "Mine").resolve(), clone.resolve())

    def test_a_fresh_install_has_no_2x_folder_to_read(self) -> None:
        (self.root / "vpinfe.ini").unlink()

        ConfigStore(self.config_file)

        self.assertFalse(self.themes.exists())


class UpdateTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.themes.mkdir()

    def test_a_2x_build_of_an_official_theme_is_updated_in_place(self) -> None:
        _install(self.themes / "Revolution", "1.2.11")
        self.choose("Revolution", Revolution={"showFlags": True})
        registry = self.registry(("Revolution", "2.0.0"))

        changed = theme_upgrade.run(registry)

        self.assertTrue(changed)
        self.assertEqual(self.version_of("Revolution"), "2.0.0")
        self.assertEqual(self.active(), "Revolution")
        self.assertEqual(config_service.theme_options()["Revolution"], {"showFlags": True})
        self.assertEqual(json.loads((self.themes / f"Revolution{ASIDE_SUFFIX}"
                                     / "manifest.json").read_text())["version"], "1.2.11")
        self.ledger.assert_called_once_with(theme_upgrade.UPDATED, RUNNING, 1)

    def test_an_updated_theme_is_not_updated_again(self) -> None:
        _install(self.themes / "Revolution", "2.0.0", "3.0")
        registry = self.registry(("Revolution", "2.1.0"))

        self.assertFalse(theme_upgrade.run(registry))

        self.assertEqual(self.version_of("Revolution"), "2.0.0")
        self.ledger.assert_not_called()

    def test_a_list_still_offering_a_2x_build_changes_nothing(self) -> None:
        _install(self.themes / "Revolution", "1.2.11")
        registry = self.registry(("Revolution", "1.3.0"))
        registry.themes["Revolution"]["manifest"].pop("min_vpinfe")

        self.assertFalse(theme_upgrade.run(registry))

        self.assertEqual(self.version_of("Revolution"), "1.2.11")

    def test_a_theme_from_another_list_is_left_alone(self) -> None:
        _install(self.themes / "Revolution", "1.2.11")
        registry = self.registry(("Revolution", "2.0.0"), source="https://example.test/list.json")

        self.assertFalse(theme_upgrade.run(registry))

        self.assertEqual(self.version_of("Revolution"), "1.2.11")

    def test_a_symlinked_theme_is_never_replaced(self) -> None:
        clone = _install(self.root / "clone" / "Revolution", "1.2.11")
        (self.themes / "Revolution").symlink_to(clone, target_is_directory=True)
        registry = self.registry(("Revolution", "2.0.0"))

        self.assertFalse(theme_upgrade.run(registry))

        self.assertTrue((self.themes / "Revolution").is_symlink())

    def test_offline_nothing_switches_and_the_next_start_does_it(self) -> None:
        _install(self.themes / "Revolution", "1.2.11")
        _install(self.themes / "Slider Video", "1.0.0")
        self.choose("Slider Video")

        self.assertFalse(theme_upgrade.run(self.registry()))
        self.assertEqual(self.active(), "Slider Video")
        self.assertEqual(self.version_of("Revolution"), "1.2.11")
        self.assertTrue((self.themes / "Slider Video").is_dir())

        self.assertTrue(theme_upgrade.run(self.registry(("Revolution", "2.0.0"),
                                                        ("slider", "2.0.0"))))
        self.assertEqual(self.version_of("Revolution"), "2.0.0")
        self.assertEqual(self.active(), "slider")

    def test_a_download_that_fails_leaves_the_theme_and_tries_again_later(self) -> None:
        _install(self.themes / "Revolution", "1.2.11")
        registry = self.registry(("Revolution", "2.0.0"))
        registry._download_zip = mock.Mock(side_effect=OSError("offline"))  # type: ignore[method-assign]

        self.assertFalse(theme_upgrade.run(registry))

        self.assertEqual(self.version_of("Revolution"), "1.2.11")


class TwinTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.themes.mkdir()

    def test_the_active_twin_makes_its_target_active_and_writes_no_option(self) -> None:
        _install(self.themes / "Slider Video", "1.0.0")
        self.choose("Slider Video", **{"Slider Video": {"speed": 4}})
        registry = self.registry(("slider", "2.0.0"))

        self.assertTrue(theme_upgrade.run(registry))

        self.assertEqual(self.active(), "slider")
        self.assertEqual(config_service.theme_options(), {})
        self.assertFalse((self.themes / "Slider Video").exists())
        self.ledger.assert_called_once_with("theme_slider_video", RUNNING, 1)

    def test_the_active_twin_with_an_option_turns_it_on(self) -> None:
        _install(self.themes / "Texal-Flyer", "1.0.0")
        self.choose("Texal-Flyer", **{"Texal-Flyer": {"size": 2}})
        registry = self.registry(("Texal", "2.0.0"))

        self.assertTrue(theme_upgrade.run(registry))

        self.assertEqual(self.active(), "Texal")
        self.assertEqual(config_service.theme_options(),
                         {"Texal": {"size": 2, "showFlyer": True}})
        self.ledger.assert_called_once_with("theme_texal_flyer", RUNNING, 1)

    def test_the_twins_values_replace_the_targets_when_the_twin_was_active(self) -> None:
        _install(self.themes / "Texal-Flyer", "1.0.0")
        _install(self.themes / "Texal", "2.0.0", "3.0")
        self.choose("Texal-Flyer", **{"Texal-Flyer": {"size": 2}, "Texal": {"size": 9}})

        theme_upgrade.run(self.registry(("Texal", "2.0.0")))

        self.assertEqual(config_service.theme_options(),
                         {"Texal": {"size": 2, "showFlyer": True}})
        self.assertEqual(self.active(), "Texal")

    def test_an_inactive_twin_goes_and_the_targets_look_is_left_alone(self) -> None:
        _install(self.themes / "Slider Video", "1.0.0")
        _install(self.themes / "slider", "2.0.0", "3.0")
        self.choose("slider", **{"slider": {"speed": 1}, "Slider Video": {"speed": 4}})

        theme_upgrade.run(self.registry(("slider", "2.0.0")))

        self.assertEqual(self.active(), "slider")
        self.assertEqual(config_service.theme_options(), {"slider": {"speed": 1}})
        self.assertFalse((self.themes / "Slider Video").exists())

    def test_an_inactive_twin_installs_a_target_that_was_missing(self) -> None:
        _install(self.themes / "Texal-Flyer", "1.0.0")
        _install(self.themes / "Revolution", "2.0.0", "3.0")
        self.choose("Revolution")

        theme_upgrade.run(self.registry(("Texal", "2.0.0")))

        self.assertEqual(self.version_of("Texal"), "2.0.0")
        self.assertEqual(self.active(), "Revolution")
        self.assertFalse((self.themes / "Texal-Flyer").exists())
        self.assertEqual(config_service.theme_options().get("Texal"), None)

    def test_a_target_the_list_does_not_offer_leaves_the_twin_running(self) -> None:
        _install(self.themes / "Slider Video", "1.0.0")
        self.choose("Slider Video")

        self.assertFalse(theme_upgrade.run(self.registry(("Revolution", "2.0.0"))))

        self.assertEqual(self.active(), "Slider Video")
        self.assertTrue((self.themes / "Slider Video").is_dir())

    def test_a_target_still_on_its_2x_build_waits(self) -> None:
        """The option exists only in the 3.0 copy, so there is nothing to switch on yet."""
        _install(self.themes / "Slider Video", "1.0.0")
        _install(self.themes / "slider", "1.5.0")
        self.choose("Slider Video")
        registry = self.registry(("slider", "2.0.0"))
        registry._download_zip = mock.Mock(side_effect=OSError("offline"))  # type: ignore[method-assign]

        self.assertFalse(theme_upgrade.run(registry))

        self.assertEqual(self.active(), "Slider Video")

    def test_a_download_that_is_not_an_archive_does_not_stop_the_start(self) -> None:
        _install(self.themes / "Slider Video", "1.0.0")
        self.choose("Slider Video")
        registry = self.registry(("slider", "2.0.0"))
        registry._download_zip = lambda url, max_retries=3: io.BytesIO(b"<html>")  # type: ignore[method-assign, misc]

        self.assertFalse(theme_upgrade.run(registry))

        self.assertEqual(self.active(), "Slider Video")
        self.assertTrue((self.themes / "Slider Video").is_dir())

    def test_settings_that_cannot_be_read_leave_the_twin_for_the_next_start(self) -> None:
        _install(self.themes / "Slider Video", "1.0.0")
        self.choose("Slider Video")
        registry = self.registry(("slider", "2.0.0"))

        with mock.patch.object(config_service, "get_ini_config", side_effect=OSError("locked")):
            self.assertFalse(theme_upgrade.run(registry))

        self.assertTrue((self.themes / "Slider Video").is_dir())
        self.assertEqual(self.active(), "Slider Video")


class ConvertedInstallTests(_Base):
    def test_2x_is_byte_identical_after_the_whole_upgrade(self) -> None:
        old = self.root / "themes"
        _install(old / "Revolution", "1.2.11")
        _install(old / "Slider Video", "1.0.0")
        (self.root / "vpinfe.ini").write_text("[Settings]\ntheme = Slider Video\n",
                                              encoding="utf-8")
        before = _snapshot(self.root / "themes"), (self.root / "vpinfe.ini").read_bytes()

        ConfigStore(self.config_file)
        registry = self.registry(("Revolution", "2.0.0"), ("slider", "2.0.0"))
        theme_upgrade.run(registry)

        self.assertEqual((_snapshot(self.root / "themes"),
                          (self.root / "vpinfe.ini").read_bytes()), before)
        self.assertEqual(self.active(), "slider")
        self.assertEqual(self.version_of("Revolution"), "2.0.0")
        self.assertEqual(sorted(p.name for p in self.themes.iterdir()),
                         sorted(["Revolution", f"Revolution{ASIDE_SUFFIX}", "slider"]))


class StockAddressTests(_Base):
    """The retired VPinFE Theme List address is replaced, and a list the person added is not."""

    OLD = theme_upgrade.RETIRED_STOCK_LIST
    MINE = "https://example.test/mine/themes.json"

    def lists(self) -> list[str]:
        return list(theme_sources.from_config(config_service.get_ini_config()).registries)

    def hold(self, *addresses: str) -> None:
        config_service.set_values({"themes": {"registries": list(addresses)}})

    def test_the_retired_address_becomes_the_current_one_in_its_place(self) -> None:
        self.hold(self.MINE, self.OLD)
        self.assertTrue(theme_upgrade.move_stock_address())
        self.assertEqual(self.lists(), [self.MINE, STOCK])

    def test_the_retired_address_first_keeps_the_current_one_first(self) -> None:
        self.hold(self.OLD, self.MINE)
        self.assertTrue(theme_upgrade.move_stock_address())
        self.assertEqual(self.lists(), [STOCK, self.MINE])

    def test_lists_without_the_retired_address_are_never_rewritten(self) -> None:
        for held in ([self.MINE], [STOCK, STOCK], [self.MINE, self.MINE, STOCK]):
            with self.subTest(held=held):
                self.hold(*held)
                before = self.config_file.read_bytes()
                self.assertFalse(theme_upgrade.move_stock_address())
                self.assertEqual(self.config_file.read_bytes(), before)

    def test_settings_that_cannot_be_read_or_written_change_nothing_and_do_not_raise(self) -> None:
        self.hold(self.OLD)
        with mock.patch.object(config_service, "get_ini_config", side_effect=OSError("locked")):
            self.assertFalse(theme_upgrade.move_stock_address())
        with mock.patch.object(config_service, "set_values", side_effect=OSError("locked")):
            self.assertFalse(theme_upgrade.move_stock_address())
        self.assertEqual(self.lists(), [self.OLD])

    def test_another_spelling_of_the_retired_address_is_replaced_too(self) -> None:
        self.hold("https://github.com/superhac/vpinfe-themes/blob/master/themes.json")
        self.assertTrue(theme_upgrade.move_stock_address())
        self.assertEqual(self.lists(), [STOCK])

    def test_a_list_the_person_added_is_left_alone(self) -> None:
        self.hold(self.MINE, STOCK)
        before = self.config_file.read_bytes()
        self.assertFalse(theme_upgrade.move_stock_address())
        self.assertEqual(self.config_file.read_bytes(), before)

    def test_the_current_address_already_there_is_not_listed_twice(self) -> None:
        self.hold(STOCK, self.MINE, self.OLD)
        self.assertTrue(theme_upgrade.move_stock_address())
        self.assertEqual(self.lists(), [STOCK, self.MINE])

    def test_a_second_start_changes_nothing(self) -> None:
        self.hold(self.OLD)
        theme_upgrade.move_stock_address()
        before = self.config_file.read_bytes()
        self.assertFalse(theme_upgrade.move_stock_address())
        self.assertEqual(self.config_file.read_bytes(), before)
