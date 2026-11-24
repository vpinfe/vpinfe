"""Loading an extension, what it is handed, and what happens when it breaks.

Driven against the fixture extensions under `tests/fixtures/extensions/`, which is the
whole of the contract exercised by something that is not core.
"""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from common import apps, i18n
from common import events as core_events
from common.extensions import contract, host, provided_apps, store
from tests.support import served
from tests.support.accepted import owner_accepts
from tests.support.identity import IDENTITY

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "extensions"


class HostCase(unittest.TestCase):
    """A registry with its own settings file, so nothing here touches this machine."""

    # Every refusal is logged, and the log is where attribution lives. Asserting on it
    # is what keeps a deliberate failure out of the suite's output as well.
    LOG = "vpinfe.common.extensions"

    def setUp(self) -> None:
        owner_accepts(self)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.store = store.ExtensionStore(self.root)
        self.registry = host.Registry(self.store)
        self.addCleanup(self.registry.clear)
        self.addCleanup(core_events.clear)

    def load(self) -> None:
        self.registry.load_from(FIXTURES)

    def held(self, name: str) -> host.Record:
        found = self.registry.get(name)
        assert found is not None
        return found

    def switched(self, name: str, on: bool) -> host.Record:
        found = self.registry.switch(name, on)
        assert found is not None
        return found

    def make(self, name: str, manifest: dict | None = None, body: str = "") -> Path:
        """An extension written for one test, where the shape under test is a broken
        one and a committed fixture would be a worked example of a mistake."""
        directory = self.root / "made" / name
        directory.mkdir(parents=True)
        raw = {"name": name, "version": "1.0.0",
               "requires_platform": contract.PLATFORM_ABI, **IDENTITY}
        raw.update(manifest or {})
        (directory / "extension.json").write_text(json.dumps(raw), encoding="utf-8")
        if body:
            (directory / "__init__.py").write_text(body, encoding="utf-8")
        return directory


class LoadTests(HostCase):
    def test_both_fixtures_load(self) -> None:
        self.load()

        self.assertEqual([one.name for one in self.registry.records()],
                         ["bystander", "sample"])
        self.assertTrue(all(one.running for one in self.registry.records()))

    def test_a_record_carries_what_the_manifest_declared(self) -> None:
        self.load()
        found = self.held("sample").as_dict()

        self.assertEqual(found["state"], host.LOADED)
        self.assertEqual(found["scopes"], ["games:read", "players:read"])
        self.assertEqual(found["routes"], ["ext:sample:library", "ext:sample:library"])
        self.assertEqual(found["capabilities"], ["config:own", "ui:mount"])

    def test_a_directory_whose_name_is_not_the_manifests_is_refused(self) -> None:
        """The folder is the name. Two answers to what an extension is called would
        each address it somewhere - one in a URL, the other in a config file."""
        directory = self.make("elsewhere", {"name": "sample"}, "def register(ctx): pass\n")

        with self.assertLogs(self.LOG, "ERROR"):
            record = self.registry.load(directory)

        self.assertEqual(record.state, host.FAILED)
        self.assertEqual(record.as_dict()["reason_key"],
                         "extension.reason.manifest_names_another")
        self.assertEqual(record.reason,
                         i18n.t("extension.reason.manifest_names_another", name="sample"))

    def test_a_refused_manifest_is_read_in_the_language_set_when_it_is_shown(self) -> None:
        directory = self.make("unreadable")
        (directory / "extension.json").write_text("{", encoding="utf-8")
        with self.assertLogs(self.LOG, "ERROR") as logged:
            record = self.registry.load(directory)
        english = record.reason

        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language("qps")

        self.assertEqual(english, "Its manifest cannot be read")
        self.assertIn("JSONDecodeError", logged.output[0])
        self.assertNotEqual(record.reason, english)

    def test_a_package_with_no_register_is_refused(self) -> None:
        with self.assertLogs(self.LOG, "ERROR") as logged:
            record = self.registry.load(self.make("silent", body="VALUE = 1\n"))

        self.assertEqual(record.state, host.FAILED)
        self.assertIn("register(ctx)", logged.output[0])

    def test_a_directory_with_no_package_is_refused(self) -> None:
        with self.assertLogs(self.LOG, "ERROR") as logged:
            record = self.registry.load(self.make("empty"))

        self.assertEqual(record.state, host.FAILED)
        self.assertIn("__init__.py", logged.output[0])

    def test_a_register_that_raises_takes_only_that_extension(self) -> None:
        self.load()
        with self.assertLogs(self.LOG, "ERROR") as logged:
            record = self.registry.load(self.make(
                "hostile", body='def register(ctx):\n    raise RuntimeError("halted")\n'))

        self.assertEqual(record.state, host.FAILED)
        self.assertIn("RuntimeError('halted')", logged.output[0])
        self.assertTrue(self.registry.running("sample"))

    def test_what_its_code_raised_stays_off_the_screen(self) -> None:
        with self.assertLogs(self.LOG, "ERROR"):
            record = self.registry.load(self.make(
                "hostile", body='def register(ctx):\n    raise KeyError("token")\n'))

        self.assertNotIn("token", record.reason)
        self.assertEqual(record.reason, "The log says why it did not start")

    def test_why_is_read_in_the_language_set_when_it_is_shown(self) -> None:
        self.store.set_enabled("sample", False)
        self.load()
        record = self.held("sample")
        self.assertEqual(record.reason, "Switched off")

        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language("qps")

        self.assertNotIn("Switched", record.reason)
        self.assertNotIn("Switched", record.as_dict()["reason"])

    def test_an_extension_the_user_switched_off_is_not_loaded(self) -> None:
        self.store.set_enabled("sample", False)

        self.load()

        self.assertEqual(self.held("sample").state, host.OFF)
        self.assertTrue(self.registry.running("bystander"))

    def test_an_extension_for_another_platform_is_not_loaded(self) -> None:
        other = "windows" if host.this_platform() != "windows" else "linux"
        directory = self.make("elsewhere", {"platforms": [other]},
                              "def register(ctx): pass\n")

        with unittest.mock.patch.object(host, "this_distribution", return_value=("", "")):
            record = self.registry.load(directory)

        self.assertEqual(record.state, host.OFF)
        self.assertIn(i18n.t(host.PLATFORM_NAMES[host.this_platform()]), record.reason)

    def test_an_extension_for_this_distribution_runs_on_it(self) -> None:
        directory = self.make("vpinos_only", {"platforms": ["vpinos"]},
                              "def register(ctx): pass\n")
        with unittest.mock.patch.object(host, "this_platform", return_value="linux"), \
                unittest.mock.patch.object(host, "this_distribution",
                                           return_value=("vpinos", "VPinOS")):
            record = self.registry.load(directory)

        self.assertEqual(record.state, host.LOADED)

    def test_the_family_matches_whatever_distribution_this_is(self) -> None:
        directory = self.make("any_linux", {"platforms": ["linux"]},
                              "def register(ctx): pass\n")
        with unittest.mock.patch.object(host, "this_platform", return_value="linux"), \
                unittest.mock.patch.object(host, "this_distribution",
                                           return_value=("ubuntu", "Ubuntu")):
            record = self.registry.load(directory)

        self.assertEqual(record.state, host.LOADED)

    def test_an_extension_for_another_distribution_names_the_one_this_is(self) -> None:
        directory = self.make("vpinos_only", {"platforms": ["vpinos"]},
                              "def register(ctx): pass\n")
        with unittest.mock.patch.object(host, "this_platform", return_value="linux"), \
                unittest.mock.patch.object(host, "this_distribution",
                                           return_value=("ubuntu", "Ubuntu")):
            record = self.registry.load(directory)

        self.assertEqual(record.state, host.OFF)
        self.assertEqual(record.why, host.WRONG_PLATFORM)
        self.assertEqual(record.reason, "It does not run on Ubuntu")

    def test_an_extension_for_another_platform_says_so_in_one_log_line(self) -> None:
        directory = self.make("vpinos_only", {"platforms": ["vpinos"]},
                              "def register(ctx): pass\n")
        with unittest.mock.patch.object(host, "this_platform", return_value="linux"), \
                unittest.mock.patch.object(host, "this_distribution",
                                           return_value=("ubuntu", "Ubuntu")), \
                self.assertLogs("vpinfe.common.extensions.host", "INFO") as logged:
            self.registry.load(directory)

        self.assertEqual(["INFO:vpinfe.common.extensions.host:Extension vpinos_only: "
                          "It does not run on Ubuntu"], logged.output)

    def test_a_linux_with_no_os_release_is_named_as_linux(self) -> None:
        directory = self.make("vpinos_only", {"platforms": ["vpinos"]},
                              "def register(ctx): pass\n")
        with unittest.mock.patch.object(host, "this_platform", return_value="linux"), \
                unittest.mock.patch.object(host, "this_distribution", return_value=("", "")):
            record = self.registry.load(directory)

        self.assertEqual(record.reason, "It does not run on Linux")

    def test_a_distribution_id_means_nothing_off_linux(self) -> None:
        directory = self.make("vpinos_only", {"platforms": ["vpinos"]},
                              "def register(ctx): pass\n")
        with unittest.mock.patch.object(host, "this_platform", return_value="macos"), \
                unittest.mock.patch.object(host, "this_distribution",
                                           return_value=("vpinos", "VPinOS")):
            record = self.registry.load(directory)

        self.assertEqual(record.reason, "It does not run on macOS")

    def test_the_distribution_is_read_from_os_release(self) -> None:
        release = {"ID": "VPinOS", "NAME": "VPinOS"}
        host._os_release.cache_clear()
        self.addCleanup(host._os_release.cache_clear)
        with unittest.mock.patch.object(host.sys, "platform", "linux"), \
                unittest.mock.patch.object(host, "freedesktop_os_release",
                                           return_value=release):
            self.assertEqual(("vpinos", "VPinOS"), host.this_distribution())

    def test_a_missing_os_release_is_no_distribution(self) -> None:
        host._os_release.cache_clear()
        self.addCleanup(host._os_release.cache_clear)
        with unittest.mock.patch.object(host.sys, "platform", "linux"), \
                unittest.mock.patch.object(host, "freedesktop_os_release",
                                           side_effect=OSError):
            self.assertEqual(("", ""), host.this_distribution())

    def test_a_platform_reads_as_its_name(self) -> None:
        directory = self.make("elsewhere", {"platforms": ["windows"]},
                              "def register(ctx): pass\n")
        with unittest.mock.patch.object(host, "this_platform", return_value="macos"):
            record = self.registry.load(directory)

        self.assertEqual(record.reason, "It does not run on macOS")

    def test_a_platform_with_no_name_is_shown_as_it_arrived(self) -> None:
        directory = self.make("elsewhere", {"platforms": ["windows"]},
                              "def register(ctx): pass\n")
        with unittest.mock.patch.object(host, "this_platform", return_value="freebsd"):
            record = self.registry.load(directory)

        self.assertEqual(record.why, host.WRONG_PLATFORM)
        self.assertEqual(record.reason, "It does not run on freebsd")

    def test_an_extension_needing_a_feature_this_install_lacks_is_not_loaded(self) -> None:
        directory = self.make("watcher", {"requires_features": ["overview", "devices"]},
                              "def register(ctx): pass\n")
        with unittest.mock.patch.object(host, "_features", return_value=("library",)):
            record = self.registry.load(directory)

        self.assertEqual(record.state, host.OFF)
        self.assertEqual(record.why, host.LACKS_FEATURES)
        self.assertEqual(record.reason, "It needs the Device Management, Overview "
                                        "features, which are off on this device")

    def test_one_missing_feature_is_said_in_the_singular(self) -> None:
        directory = self.make("watcher", {"requires_features": ["overview", "library"]},
                              "def register(ctx): pass\n")
        with unittest.mock.patch.object(host, "_features", return_value=("library",)):
            record = self.registry.load(directory)

        self.assertEqual(record.reason, "It needs the Overview feature, "
                                        "which is off on this device")

    def test_the_names_follow_the_language_set(self) -> None:
        directory = self.make("elsewhere", {"platforms": ["windows"]},
                              "def register(ctx): pass\n")
        with unittest.mock.patch.object(host, "this_platform", return_value="macos"):
            record = self.registry.load(directory)
        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language("qps")

        self.assertNotIn("macOS", record.reason)


class WordsTests(HostCase):
    """What an extension says is looked up in its own `i18n/`, when it is read."""

    OFFERS = ('class Sync:\n'
              '    def ask(self, step):\n'
              '        return {"fields": []}\n'
              'def register(ctx):\n'
              '    ctx.ui.action("sync", Sync())\n'
              '    ctx.ui.settings([{"key": "account", "type": "string"}])\n'
              '    ctx.ui.list("scores", lambda: [], columns=[{"field": "name"}],\n'
              '                views=[{"key": "top", "columns": ["name"]}], area="community")\n')

    def worded(self, name: str, words: dict, manifest: dict | None = None,
               body: str = "def register(ctx): pass\n") -> Path:
        directory = self.make(name, manifest, body)
        (directory / "i18n").mkdir()
        (directory / "i18n" / "en.json").write_text(json.dumps(words), encoding="utf-8")
        self.addCleanup(i18n.disown, f"ext.{name}")
        return directory

    def test_a_name_left_out_is_its_folder_name(self) -> None:
        record = self.registry.load(self.make("plain", body="def register(ctx): pass\n"))

        self.assertEqual(record.display_name, "plain")
        self.assertEqual(record.as_dict()["description"], "")

    def test_its_catalog_names_it(self) -> None:
        record = self.registry.load(self.worded(
            "named", {"name": "Named", "description": "Does one thing"}))

        self.assertEqual(record.as_dict()["display_name"], "Named")
        self.assertEqual(record.as_dict()["description"], "Does one thing")

    def test_a_name_in_the_manifest_is_shown_as_written(self) -> None:
        """A product name, which is the same in every language."""
        record = self.registry.load(self.worded(
            "branded", {"name": "Translated"}, {"display_name": "VPinThing"}))

        self.assertEqual(record.display_name, "VPinThing")

    def test_one_switched_off_is_still_named_by_its_catalog(self) -> None:
        """It is still listed, and switching it back on is done by name."""
        self.store.set_enabled("named", False)

        record = self.registry.load(self.worded("named", {"name": "Named"}))

        self.assertEqual(record.state, host.OFF)
        self.assertEqual(record.display_name, "Named")

    def test_what_it_offers_is_worded_by_its_catalog(self) -> None:
        record = self.registry.load(self.worded("worded", {
            "action.sync.label": "Sync now",
            "action.sync.description": "Sends what was played",
            "settings.account.label": "Account",
            "list.scores.title": "Top scores",
            "list.scores.column.name.header": "Table",
            "list.scores.view.top.name": "Most played",
        }, {"capabilities": ["ui:mount"]}, self.OFFERS))
        found = record.as_dict()

        self.assertEqual(found["actions"][0]["label"], "Sync now")
        self.assertEqual(found["actions"][0]["label_key"], "ext.worded.action.sync.label")
        self.assertEqual(found["actions"][0]["description"], "Sends what was played")
        asked = served.client_for(self, self.registry, record).get("/ext/worded/settings")
        self.assertEqual(asked.json()["fields"][0]["label"], "Account")
        listing = found["lists"][0]
        self.assertEqual(listing["title"], "Top scores")
        self.assertEqual(listing["columns"][0]["header"], "Table")
        self.assertEqual(listing["views"][0]["name"], "Most played")

    def test_what_its_catalog_leaves_out_is_called_by_its_key(self) -> None:
        """A setting with none is called by its key, which the Manager shows as written."""
        record = self.registry.load(self.worded(
            "bare", {}, {"capabilities": ["ui:mount"]}, self.OFFERS))
        found = record.as_dict()

        self.assertEqual(found["actions"][0]["label"], "sync")
        self.assertEqual(found["actions"][0]["label_key"], "")
        asked = served.client_for(self, self.registry, record).get("/ext/bare/settings")
        self.assertEqual(asked.json()["fields"][0]["label"], "account")
        listing = found["lists"][0]
        self.assertEqual(listing["title"], "scores")
        self.assertEqual(listing["columns"][0]["header"], "name")
        self.assertEqual(listing["views"][0]["name"], "top")

    def test_it_reads_its_own_words_by_key(self) -> None:
        directory = self.worded("speaker", {"greeting": "Hello, {who}"}, body=(
            'from pathlib import Path\n'
            'def register(ctx):\n'
            '    Path(__file__).with_name("said.txt").write_text(ctx.t("greeting", who="Pat"))\n'))

        self.registry.load(directory)

        self.assertEqual((directory / "said.txt").read_text(), "Hello, Pat")
        self.assertEqual(contract.words("speaker")("greeting", who="Sam"), "Hello, Sam")

    def test_it_words_a_failure_the_way_core_does(self) -> None:
        directory = self.make("failing", body=(
            'import errno, urllib.error\n'
            'from pathlib import Path\n'
            'def register(ctx):\n'
            '    gone = FileNotFoundError(errno.ENOENT, "gone", "/tables/a.vpx")\n'
            '    quiet = urllib.error.URLError(ConnectionRefusedError())\n'
            '    Path(__file__).with_name("said.txt").write_text("\\n".join((\n'
            '        ctx.why(gone), ctx.why(quiet, at="https://vp.example/api/v1"))))\n'))

        self.registry.load(directory)

        self.assertEqual((directory / "said.txt").read_text().splitlines(),
                         ["Nothing is at /tables/a.vpx", "Nothing answers at vp.example"])
        self.assertEqual(contract.why(TimeoutError(), at="https://vp.example/"),
                         "vp.example did not answer in time")


class ContextTests(HostCase):
    def test_the_logger_is_the_extensions_own_namespace(self) -> None:
        """The namespace is how "which extension did this?" stays answerable."""
        self.load()
        with self.assertLogs("vpinfe.ext.sample", level=logging.INFO) as caught:
            logging.getLogger("vpinfe.ext.sample").info("hello")

        self.assertIn("hello", caught.output[0])

    def test_settings_land_in_a_file_of_the_extensions_own(self) -> None:
        """A file each, not a namespace inside one. One bad write took every
        extension's settings with it, and the switched-off ones came back on."""
        self.store.set_setting("sample", "greeting", "good evening")

        path = self.root / "extension_settings" / "sample.json"

        self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                         {"greeting": "good evening"})

    def test_core_keeps_only_what_core_needs_to_know(self) -> None:
        """Whether it is switched off is core's setting - it has to know before it loads
        anything. What it is configured with is nobody's business but its own."""
        self.store.set_setting("sample", "greeting", "good evening")
        self.store.set_enabled("sample", False)

        held = json.loads((self.root / "vpinfe.json").read_text(encoding="utf-8"))

        self.assertEqual(held["settings"]["extensions"]["switched_off"], ["sample"])
        self.assertFalse((self.root / "extensions.json").exists())

    def test_switching_on_takes_it_off_the_list_and_leaves_the_others(self) -> None:
        self.store.set_enabled("sample", False)
        self.store.set_enabled("bystander", False)

        self.store.set_enabled("sample", True)

        self.assertTrue(self.store.enabled("sample"))
        self.assertFalse(self.store.enabled("bystander"))

    def test_one_extensions_unreadable_settings_cost_only_that_one(self) -> None:
        """The whole reason they are not in one file together."""
        self.store.set_setting("sample", "greeting", "good evening")
        self.store.set_setting("bystander", "greeting", "hello")
        (self.root / "extension_settings" / "sample.json").write_text("{", "utf-8")

        with self.assertLogs("vpinfe.common.extensions.store", "ERROR"):
            self.assertEqual(self.store.settings("sample"), {})

        self.assertEqual(self.store.settings("bystander"), {"greeting": "hello"})
        self.assertTrue(self.store.enabled("sample"))

    def test_forgetting_one_takes_its_file_with_it(self) -> None:
        self.store.set_setting("sample", "greeting", "good evening")

        self.store.forget("sample")

        self.assertFalse((self.root / "extension_settings" / "sample.json").exists())

    def test_the_store_follows_its_folder_rather_than_this_machine(self) -> None:
        """A store told where its folder is and left pointing at this machine's own
        settings is not isolated at all - which wrote into a real config directory
        before it was caught."""
        from common.extensions import store as store_module

        self.assertEqual(self.store.settings_dir, self.root / "extension_settings")
        self.assertNotEqual(self.store.settings_dir, store_module.SETTINGS_DIR)
        self.store.set_enabled("sample", False)
        self.assertTrue((self.root / "vpinfe.json").is_file())

    def test_forgetting_one_switches_it_back_on(self) -> None:
        self.store.set_enabled("sample", False)

        self.store.forget("sample")

        self.assertTrue(self.store.enabled("sample"))

    def test_a_router_at_a_tier_the_manifest_does_not_list_is_refused(self) -> None:
        body = ("from fastapi import APIRouter\n"
                "def register(ctx):\n"
                "    ctx.add_router(APIRouter(), tier='play')\n")
        with self.assertLogs(self.LOG, "ERROR") as logged:
            record = self.registry.load(self.make("greedy", body=body))

        self.assertEqual(record.state, host.FAILED)
        self.assertIn("its tiers are library", logged.output[0])

    def test_a_router_at_a_listed_tier_is_kept(self) -> None:
        body = ("from fastapi import APIRouter\n"
                "def register(ctx):\n"
                "    ctx.add_router(APIRouter(), tier='play')\n")

        record = self.registry.load(self.make("modest", {"play": ["router"]}, body=body))

        self.assertEqual(record.state, host.LOADED)
        self.assertEqual(record.as_dict()["routes"], ["ext:modest:play"])

    def test_a_play_action_the_manifest_does_not_name_is_refused(self) -> None:
        body = ("class Dialog:\n"
                "    def ask(self, step):\n"
                "        return {}\n"
                "def register(ctx):\n"
                "    ctx.ui.action('reset', Dialog(), tier='play')\n")
        manifest = {"capabilities": ["ui:mount"], "play": ["action:other"]}
        with self.assertLogs(self.LOG, "ERROR"):
            record = self.registry.load(self.make("sly", manifest, body=body))

        self.assertEqual(record.state, host.FAILED)
        self.assertIn("does not list 'action:reset'", record.error)

    def test_a_play_router_the_manifest_does_not_name_is_refused(self) -> None:
        body = ("from fastapi import APIRouter\n"
                "def register(ctx):\n"
                "    ctx.add_router(APIRouter(), tier='play')\n")
        with self.assertLogs(self.LOG, "ERROR"):
            record = self.registry.load(self.make("sneaky", {"play": ["list:scores"]}, body=body))

        self.assertEqual(record.state, host.FAILED)
        self.assertIn("does not list 'router'", record.error)

    def test_a_play_entry_the_extension_never_declares_is_refused(self) -> None:
        body = ("def register(ctx):\n"
                "    pass\n")
        with self.assertLogs(self.LOG, "ERROR"):
            record = self.registry.load(self.make("idle", {"play": ["router", "list:scores"]},
                                                  body=body))

        self.assertEqual(record.state, host.FAILED)
        self.assertIn("never puts at play: list:scores, router", record.error)

    def test_a_play_entry_for_a_surface_left_at_library_is_refused(self) -> None:
        body = ("class Dialog:\n"
                "    def ask(self, step):\n"
                "        return {}\n"
                "def register(ctx):\n"
                "    ctx.ui.action('reset', Dialog())\n")
        manifest = {"capabilities": ["ui:mount"], "play": ["action:reset"]}
        with self.assertLogs(self.LOG, "ERROR"):
            record = self.registry.load(self.make("timid", manifest, body=body))

        self.assertEqual(record.state, host.FAILED)
        self.assertIn("never puts at play: action:reset", record.error)

    def test_only_the_play_entries_never_declared_are_named(self) -> None:
        body = ("class Dialog:\n"
                "    def ask(self, step):\n"
                "        return {}\n"
                "def register(ctx):\n"
                "    ctx.ui.action('reset', Dialog(), tier='play')\n")
        manifest = {"capabilities": ["ui:mount"], "play": ["action:reset", "section:rank"]}
        with self.assertLogs(self.LOG, "ERROR"):
            record = self.registry.load(self.make("half", manifest, body=body))

        self.assertEqual(record.state, host.FAILED)
        self.assertIn("never puts at play: section:rank", record.error)
        self.assertNotIn("action:reset", record.error)

    def test_a_play_router_the_extension_adds_satisfies_its_entry(self) -> None:
        body = ("from fastapi import APIRouter\n"
                "def register(ctx):\n"
                "    ctx.add_router(APIRouter(), tier='play')\n")

        record = self.registry.load(self.make("wired", {"play": ["router"]}, body=body))

        self.assertEqual(record.state, host.LOADED, record.error)

    def test_a_play_entry_the_extension_declares_loads(self) -> None:
        body = ("class Dialog:\n"
                "    def ask(self, step):\n"
                "        return {}\n"
                "def register(ctx):\n"
                "    ctx.ui.action('reset', Dialog(), tier='play')\n")
        manifest = {"capabilities": ["ui:mount"], "play": ["action:reset"]}

        record = self.registry.load(self.make("frank", manifest, body=body))

        self.assertEqual(record.state, host.LOADED, record.error)

    def test_publishing_an_undeclared_event_is_refused(self) -> None:
        body = ("def register(ctx):\n"
                "    ctx.events.publish('surprise')\n")
        with self.assertLogs(self.LOG, "ERROR") as logged:
            record = self.registry.load(self.make("loud", body=body))

        self.assertEqual(record.state, host.FAILED)
        self.assertIn("does not declare", logged.output[0])

    def test_a_published_event_carries_the_extensions_namespace(self) -> None:
        self.load()
        heard = []
        core_events.subscribe("sample.noticed", lambda **payload: heard.append(payload))

        core_events.emit("game.selected", game_id="abc")

        self.assertEqual(heard, [{"game_id": "abc"}])


class FailureTests(HostCase):
    def test_a_subscriber_that_throws_disables_only_that_extension(self) -> None:
        self.load()

        with self.assertLogs(self.LOG, "ERROR"), \
                self.assertLogs("vpinfe.ext.sample", "ERROR") as blamed:
            core_events.emit("game.selected", game_id="fail")

        # The extension's own namespace carries the traceback: which extension did this
        # is the question the namespace exists to answer.
        self.assertIn("game.selected", blamed.output[0])
        self.assertEqual(self.held("sample").state, host.DISABLED)
        self.assertIn("game.selected", self.held("sample").reason)
        self.assertTrue(self.registry.running("bystander"))

    def test_a_disabled_extension_is_told_nothing_more(self) -> None:
        self.load()
        with self.assertLogs(self.LOG, "ERROR"), \
                self.assertLogs("vpinfe.ext.sample", "ERROR"):
            core_events.emit("game.selected", game_id="fail")

        heard = []
        core_events.subscribe("sample.noticed", lambda **payload: heard.append(payload))
        core_events.emit("game.selected", game_id="abc")

        self.assertEqual(heard, [])

    def test_disabling_takes_the_extensions_scopes_with_it(self) -> None:
        self.load()
        self.assertIn("ext:sample:library", self.registry.granted_scopes())

        with self.assertLogs(self.LOG, "ERROR"):
            self.registry.disable("sample", "asked to")

        self.assertNotIn("ext:sample:library", self.registry.granted_scopes())
        self.assertIn("ext:bystander:library", self.registry.granted_scopes())

    def test_a_disable_is_not_written_down(self) -> None:
        """A fault that happened once must not take the extension away until somebody
        notices a setting they never set."""
        self.load()
        with self.assertLogs(self.LOG, "ERROR"):
            self.registry.disable("sample", "asked to")

        self.assertTrue(self.store.enabled("sample"))


class SwitchTests(HostCase):
    def heard(self) -> list[dict]:
        heard: list[dict] = []
        core_events.subscribe("sample.noticed", lambda **payload: heard.append(payload))
        core_events.emit("game.selected", game_id="abc")
        return heard

    def test_off_takes_it_out_now_and_writes_it_down(self) -> None:
        self.load()

        with self.assertLogs(self.LOG, "INFO"):
            record = self.switched("sample", False)

        self.assertIs(record, self.held("sample"))
        self.assertEqual((record.state, record.why), (host.OFF, host.SWITCHED_OFF))
        self.assertFalse(record.enabled)
        self.assertFalse(self.store.enabled("sample"))
        self.assertNotIn("ext:sample:library", self.registry.granted_scopes())
        self.assertEqual(self.heard(), [])
        self.assertTrue(self.registry.running("bystander"))

    def test_off_is_not_reported_as_a_fault(self) -> None:
        self.load()

        with self.assertLogs(self.LOG, "INFO") as said:
            self.registry.switch("sample", False)

        self.assertEqual([line for line in said.records if line.levelno > logging.INFO],
                         [])

    def test_on_writes_it_down_and_waits_for_the_next_start(self) -> None:
        self.store.set_enabled("sample", False)
        self.load()

        with self.assertLogs(self.LOG, "INFO"):
            record = self.switched("sample", True)

        self.assertTrue(self.store.enabled("sample"))
        self.assertTrue(record.enabled)
        self.assertEqual((record.state, record.why), (host.OFF, host.STARTS_AT_RESTART))
        self.assertEqual(record.as_dict()["reason"], "Starts at the next restart")
        self.assertNotIn("ext:sample:library", self.registry.granted_scopes())

        again = host.Registry(self.store)
        self.addCleanup(again.clear)
        again.load_from(FIXTURES)
        self.assertTrue(again.running("sample"))

    def test_off_again_before_the_restart_says_switched_off(self) -> None:
        self.store.set_enabled("sample", False)
        self.load()

        with self.assertLogs(self.LOG, "INFO"):
            self.registry.switch("sample", True)
            record = self.switched("sample", False)

        self.assertEqual(record.why, host.SWITCHED_OFF)

    def test_one_this_device_cannot_run_keeps_saying_why(self) -> None:
        other = "windows" if host.this_platform() != "windows" else "linux"
        self.registry.load(self.make("elsewhere", {"platforms": [other]},
                                     "def register(ctx): pass\n"))

        with self.assertLogs(self.LOG, "INFO"):
            self.assertEqual(self.switched("elsewhere", False).why,
                             host.SWITCHED_OFF)
            record = self.switched("elsewhere", True)

        self.assertEqual(record.why, "extension.reason.wrong_platform")

    def test_switching_off_one_a_fault_stopped_reads_as_switched_off(self) -> None:
        self.load()
        with self.assertLogs(self.LOG, "ERROR"):
            self.registry.disable("sample", "asked to")

        with self.assertLogs(self.LOG, "INFO"):
            record = self.switched("sample", False)

        self.assertEqual((record.state, record.why), (host.OFF, host.SWITCHED_OFF))

    def test_on_leaves_a_running_one_running(self) -> None:
        self.load()

        with self.assertLogs(self.LOG, "INFO"):
            record = self.switched("sample", True)

        self.assertEqual(record.state, host.LOADED)
        self.assertIn("ext:sample:library", self.registry.granted_scopes())

    def test_a_switched_off_extension_still_says_it_has_settings(self) -> None:
        """Only a running one has `surfaces`, and it is `register` that declares
        settings, so the flag is remembered from the last run for a stopped page to read."""
        body = ('def register(ctx):\n'
                '    ctx.ui.settings([{"key": "account", "type": "string"}])\n')
        directory = self.make("declares", {"capabilities": ["ui:mount"]}, body)
        self.assertTrue(self.registry.load(directory).has_settings)
        self.store.set_enabled("declares", False)
        later = host.Registry(self.store)
        self.addCleanup(later.clear)

        found = later.load(directory).as_dict()

        self.assertEqual((found["state"], found["surfaces"]), (host.OFF, {}))
        self.assertTrue(found["has_settings"])

    def test_an_extension_with_no_settings_does_not_say_it_has_any(self) -> None:
        record = self.registry.load(self.make("plain", body="def register(ctx): pass\n"))

        self.assertFalse(record.as_dict()["has_settings"])

    def test_the_settings_flag_follows_what_register_declares_now(self) -> None:
        """A later version that drops its settings is not remembered as having them."""
        directory = self.make("declares", {"capabilities": ["ui:mount"]}, (
            'def register(ctx):\n'
            '    ctx.ui.settings([{"key": "account", "type": "string"}])\n'))
        self.registry.load(directory)
        (directory / "__init__.py").write_text("def register(ctx): pass\n", encoding="utf-8")

        record = host.Registry(self.store).load(directory)

        self.assertFalse(record.has_settings)
        self.assertEqual(self.store.declared("declares"), {"settings": False})

    def test_a_name_nothing_answers_to_is_not_written(self) -> None:
        self.assertIsNone(self.registry.switch("nothing", False))
        self.assertTrue(self.store.enabled("nothing"))

    def test_the_listing_says_what_the_switch_is_set_to(self) -> None:
        self.store.set_enabled("sample", False)
        self.load()

        self.assertFalse(self.held("sample").as_dict()["enabled"])
        self.assertTrue(self.held("bystander").as_dict()["enabled"])


class WithdrawTests(HostCase):
    """What an extension offered goes with it, whether it stops or never finished."""

    OFFERS = ('def register(ctx):\n'
              '    ctx.events.subscribe("game.selected", lambda **_: ctx.events.publish("heard"))\n'
              '    ctx.apps.provide(id="fp", name="Future Pinball", suffixes=(".fpt",))\n')
    MANIFEST = {"scopes": [provided_apps.APPS_PROVIDE], "events": ["heard"]}

    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(apps.withdraw_all)

    def heard(self) -> list[dict]:
        heard: list[dict] = []
        core_events.subscribe("offers.heard", lambda **payload: heard.append(payload))
        core_events.emit("game.selected", game_id="abc")
        return heard

    def test_a_stopped_one_takes_back_its_app(self) -> None:
        self.registry.load(self.make("offers", self.MANIFEST, self.OFFERS))
        claimed = apps.app_for("Big Bang Bar.fpt")
        assert claimed is not None
        self.assertEqual(claimed.id, "fp")

        with self.assertLogs(self.LOG, "ERROR"):
            self.registry.disable("offers", "asked to")

        self.assertIsNone(apps.app_for("Big Bang Bar.fpt"))
        self.assertEqual(self.heard(), [])

    def test_its_apps_words_go_with_it(self) -> None:
        directory = self.make("offers", self.MANIFEST, self.OFFERS)
        (directory / "i18n").mkdir()
        (directory / "i18n" / "en.json").write_text(
            json.dumps({"app.fp.name": "Future Pinball"}), encoding="utf-8")
        self.addCleanup(i18n.disown, "ext.offers")
        self.addCleanup(i18n.disown, "app.fp")
        self.registry.load(directory)
        self.assertEqual(i18n.first_key("app.fp.name"), "app.fp.name")

        with self.assertLogs(self.LOG, "ERROR"):
            self.registry.disable("offers", "asked to")

        self.assertEqual(i18n.first_key("app.fp.name"), "")

    def test_a_register_that_raises_part_way_takes_back_what_it_had_offered(self) -> None:
        body = self.OFFERS + '    raise RuntimeError("halfway")\n'
        with self.assertLogs(self.LOG, "ERROR"):
            record = self.registry.load(self.make("offers", self.MANIFEST, body))

        self.assertEqual(record.state, host.FAILED)
        self.assertIsNone(apps.app_for("Big Bang Bar.fpt"))
        self.assertEqual(self.heard(), [])


if __name__ == "__main__":
    unittest.main()
