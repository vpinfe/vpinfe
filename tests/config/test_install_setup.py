"""`device.setup_pending` is true until Setup finishes; `setup_done` on the self row is its
progress."""

from __future__ import annotations

import json
import unittest

from common.config.config_store import ConfigStore
from common.devices import device_registry, install_setup
from tests.support.library import TempTree


class PendingTests(TempTree):
    def test_a_fresh_install_is_pending(self) -> None:
        store = ConfigStore(str(self.root / "vpinfe.ini"))
        self.assertTrue(install_setup.pending(store))

    def test_a_hand_written_config_is_not_pending(self) -> None:
        (self.root / "vpinfe.json").write_text("{}", encoding="utf-8")
        store = ConfigStore(str(self.root / "vpinfe.ini"))
        self.assertFalse(install_setup.pending(store))

    def test_a_2x_ini_converted_to_json_is_not_pending(self) -> None:
        (self.root / "vpinfe.ini").write_text("[Settings]\n", encoding="utf-8")
        store = ConfigStore(str(self.root / "vpinfe.ini"))
        self.assertFalse(install_setup.pending(store))


class FinishTests(TempTree):
    def test_finish_clears_the_mark_and_saves_it(self) -> None:
        path = self.root / "vpinfe.ini"
        store = ConfigStore(str(path))
        self.assertTrue(install_setup.pending(store))

        install_setup.finish(store)

        self.assertFalse(install_setup.pending(store))
        payload = json.loads((self.root / "vpinfe.json").read_text(encoding="utf-8"))
        self.assertFalse(payload["settings"]["device"]["setup_pending"])

    def test_a_second_store_reading_the_same_file_also_sees_it_gone(self) -> None:
        path = self.root / "vpinfe.ini"
        install_setup.finish(ConfigStore(str(path)))

        self.assertFalse(install_setup.pending(ConfigStore(str(path))))

    def test_finishing_twice_writes_the_same_thing(self) -> None:
        store = ConfigStore(str(self.root / "vpinfe.ini"))
        install_setup.finish(store)
        install_setup.finish(store)

        self.assertFalse(install_setup.pending(store))


class AnsweredTests(TempTree):
    def test_nothing_is_answered_on_a_fresh_install(self) -> None:
        self.assertEqual(install_setup.answered(), [])

    def test_answers_keep_the_order_they_came_in_and_survive_a_reload(self) -> None:
        install_setup.answer("how")
        install_setup.answer("screens")

        device_registry.reset_for_tests(self.registry_path)

        self.assertEqual(install_setup.answered(), ["how", "screens"])

    def test_answers_are_on_the_self_row_and_not_in_the_settings_file(self) -> None:
        store = ConfigStore(str(self.root / "vpinfe.ini"))
        install_setup.answer("how")
        install_setup.finish(store)

        payload = json.loads((self.root / "vpinfe.json").read_text(encoding="utf-8"))
        self.assertNotIn("setup_done", payload["settings"]["device"])
        rows = json.loads((self.registry_path).read_text(encoding="utf-8"))["devices"]
        self.assertEqual([one["setup_done"] for one in rows], [["how"]])

    def test_answering_a_step_twice_records_it_once(self) -> None:
        install_setup.answer("how")
        install_setup.answer("how")

        self.assertEqual(install_setup.answered(), ["how"])

    def test_finishing_keeps_the_record_of_what_was_answered(self) -> None:
        store = ConfigStore(str(self.root / "vpinfe.ini"))
        install_setup.answer("how")
        install_setup.finish(store)

        self.assertEqual(install_setup.answered(), ["how"])


class AfterFinishTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(install_setup._after_finish.clear)
        install_setup._after_finish.clear()
        self.path = str(self.root / "vpinfe.ini")
        self.store = ConfigStore(self.path)

    def test_the_callback_runs_when_the_mark_is_cleared(self) -> None:
        ran: list[int] = []
        install_setup.after_finish(self.store, lambda: ran.append(1))
        self.assertEqual(ran, [], "it ran before Setup finished")

        install_setup.finish(ConfigStore(self.path))

        self.assertEqual(ran, [1])

    def test_it_runs_once_however_many_times_finish_is_pressed(self) -> None:
        ran: list[int] = []
        install_setup.after_finish(self.store, lambda: ran.append(1))

        install_setup.finish(ConfigStore(self.path))
        install_setup.finish(ConfigStore(self.path))

        self.assertEqual(ran, [1])

    def test_it_runs_at_once_when_the_file_already_says_setup_finished(self) -> None:
        """Finish pressed after this process read its config and before it registered."""
        install_setup.finish(ConfigStore(self.path))
        self.assertTrue(install_setup.pending(self.store), "the stale store is the case")
        ran: list[int] = []

        install_setup.after_finish(self.store, lambda: ran.append(1))

        self.assertEqual(ran, [1])

    def test_a_callback_that_ran_at_once_does_not_run_again_on_the_next_finish(self) -> None:
        install_setup.finish(ConfigStore(self.path))
        ran: list[int] = []
        install_setup.after_finish(self.store, lambda: ran.append(1))

        install_setup.finish(ConfigStore(self.path))

        self.assertEqual(ran, [1])

    def test_a_callback_that_fails_does_not_stop_the_next(self) -> None:
        ran: list[int] = []

        def fails() -> None:
            raise RuntimeError("no")

        install_setup.after_finish(self.store, fails)
        install_setup.after_finish(self.store, lambda: ran.append(1))

        with self.assertLogs("vpinfe.common.devices.install_setup", level="ERROR"):
            install_setup.finish(ConfigStore(self.path))

        self.assertEqual(ran, [1])

    def test_registering_never_writes_the_file(self) -> None:
        before = (self.root / "vpinfe.json").read_bytes()

        install_setup.after_finish(self.store, lambda: None)

        self.assertEqual((self.root / "vpinfe.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
