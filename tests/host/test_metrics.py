from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from common import events
from common.config import path_state
from common.host import metrics
from common.i18n import t
from common.runtime import jobs
from tests.support.hung_folder import never_answering


class OneSampleTests(unittest.TestCase):
    def setUp(self) -> None:
        metrics.reset_for_tests()
        self.addCleanup(metrics.reset_for_tests)
        self.clock = [1000.0]
        patch = mock.patch.object(metrics.time, "time", side_effect=lambda: self.clock[0])
        patch.start()
        self.addCleanup(patch.stop)
        self.cpu = mock.Mock(side_effect=[46.5, 0.0, 12.0])
        fake = mock.Mock(cpu_percent=self.cpu)
        fake.virtual_memory.return_value = mock.Mock(total=100, available=40, percent=60.0)
        fake.cpu_count.return_value = 4
        fake.process_iter.return_value = []
        fake.sensors_temperatures.return_value = {}
        for name, value in (("psutil", fake), ("PER_CORE", True)):
            swap = mock.patch.object(metrics, name, value)
            swap.start()
            self.addCleanup(swap.stop)
        self.graphics = {"cards": [{"id": "1", "name": "Card", "busy": 30.0, "source": "x"},
                                   {"id": "2", "name": "Other", "source": "x"}],
                         "reason": "", "detail": ""}
        patch = mock.patch.object(metrics.graphics, "read", return_value=self.graphics)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_second_caller_gets_the_reading_already_taken(self) -> None:
        first = metrics.sample()
        self.clock[0] += 0.01
        second = metrics.sample()
        self.assertEqual(first["cpu_percent"], 46.5)
        self.assertEqual(second["cpu_percent"], 46.5)
        self.assertEqual(self.cpu.call_count, 1)

    def test_only_the_reading_actually_taken_enters_the_history(self) -> None:
        metrics.sample()
        self.clock[0] += 0.01
        metrics.sample()
        self.clock[0] += 2
        metrics.sample()
        self.assertEqual([one["cpu_percent"] for one in metrics.history()], [46.5, 0.0])

    def test_the_volumes_follow_the_caller_not_the_cache(self) -> None:
        metrics.sample([])
        self.clock[0] += 0.01
        found = metrics.sample([str(Path(__file__).parent)])
        self.assertEqual(len(found["disks"]), 1)
        self.assertEqual(self.cpu.call_count, 1)
        self.assertEqual(metrics.psutil.process_iter.call_count, 1)

    def test_a_volume_that_does_not_answer_is_reported_unreadable_without_being_waited_on(
            self) -> None:
        here = str(Path(__file__).parent)
        gone = str(Path(__file__).parent.parent)
        with mock.patch.object(path_state, "PROBE_SECONDS", 0.5), never_answering(gone), \
                mock.patch.object(metrics.shutil, "disk_usage",
                                  return_value=(10, 4, 6)) as asked:
            found = metrics.sample([here, gone])
        rows = {one["path"]: one for one in found["disks"]}
        self.assertEqual(rows[here]["free"], 6)
        self.assertEqual(t("error.path.not_answering"), rows[gone]["error"])
        self.assertIsNone(rows[gone]["free"])
        self.assertEqual([call.args[0] for call in asked.call_args_list], [Path(here)])

    def test_the_sample_carries_the_temperature_and_the_busiest_programs(self) -> None:
        metrics.psutil.sensors_temperatures.return_value = {"coretemp": [_reading("Package", 60)]}
        metrics.psutil.process_iter.return_value = [_process(5, "vpx", 40.0, 7)]
        metrics.sample()
        self.clock[0] += 2
        found = metrics.sample()
        self.assertEqual(found["cpu_temperature"], 60.0)
        self.assertEqual(found["busiest"], [{"name": "vpx", "cpu_percent": 40.0, "memory": 7}])

    def test_graphics_joins_the_sample_and_only_a_busy_reading_the_history(self) -> None:
        found = metrics.sample()
        self.assertEqual(found["graphics"], self.graphics)
        self.assertEqual(metrics.history()[-1]["gpus"], {"1": 30.0})


def _reading(label: str, current: float | None) -> mock.Mock:
    return mock.Mock(label=label, current=current)


def _process(pid: int, name: str | None, cpu: float | None, rss: int | None,
             exe: str | None = None) -> mock.Mock:
    memory = None if rss is None else mock.Mock(rss=rss)
    return mock.Mock(info={"pid": pid, "name": name, "exe": exe, "cpu_percent": cpu,
                           "memory_info": memory})


class MarksTests(unittest.TestCase):
    def setUp(self) -> None:
        metrics.reset_for_tests()
        jobs.reset_for_tests()
        events.clear()
        self.addCleanup(metrics.reset_for_tests)
        self.addCleanup(jobs.reset_for_tests)
        self.addCleanup(events.clear)
        self.clock = [1000.0]
        patch = mock.patch.object(metrics.time, "time", side_effect=lambda: self.clock[0])
        patch.start()
        self.addCleanup(patch.stop)

    def _job(self, kind: str, started: float) -> None:
        with jobs.track(kind):
            pass
        jobs.recent(1)[0].started_at = started

    def test_a_launch_is_marked_with_the_games_title(self) -> None:
        metrics.register()
        with mock.patch("common.games.game_metadata.game_title", return_value="Attack"):
            events.emit(events.TABLE_LAUNCHED, game=object(), private=False)

        self.assertEqual([{"at": 1000.0, "kind": "table", "name": "Attack"}], metrics.marks())

    def test_a_private_game_is_marked_without_its_name(self) -> None:
        metrics.register()
        with mock.patch("common.games.game_metadata.game_title",
                        return_value="Attack") as named:
            events.emit(events.TABLE_LAUNCHED, game=object(), private=True)

        self.assertEqual([{"at": 1000.0, "kind": "table", "name": ""}], metrics.marks())
        named.assert_not_called()

    def test_a_game_that_cannot_be_named_is_still_marked(self) -> None:
        metrics.register()
        with mock.patch("common.games.game_metadata.game_title", side_effect=ValueError):
            events.emit(events.TABLE_LAUNCHED, game=object(), private=False)

        self.assertEqual([""], [one["name"] for one in metrics.marks()])

    def test_registering_twice_marks_a_launch_once(self) -> None:
        metrics.register()
        metrics.register()
        events.emit(events.TABLE_LAUNCHED, game=None, private=False)

        self.assertEqual(1, len(metrics.marks()))

    def test_a_job_with_a_name_of_its_own_is_marked_with_it(self) -> None:
        with jobs.track(jobs.KIND_LIBRARY_SCAN, title="Auto-match"):
            pass
        self._job(jobs.KIND_TOOL_GET, 1000.0)

        found = {one["name"]: one for one in metrics.marks()}

        self.assertEqual("Auto-match", found[jobs.KIND_LIBRARY_SCAN]["title"])
        self.assertNotIn("title", found[jobs.KIND_TOOL_GET])

    def test_a_job_is_marked_when_it_started_by_its_kind(self) -> None:
        self._job("library_scan", 990.0)

        self.assertEqual([{"at": 990.0, "kind": "job", "name": "library_scan"}],
                         metrics.marks())

    def test_jobs_and_launches_come_oldest_first_and_only_from_inside_the_window(self) -> None:
        metrics.register()
        self._job("old", 100.0)
        self._job("library_scan", 900.0)
        self.clock[0] = 950.0
        events.emit(events.TABLE_LAUNCHED, game=None, private=False)
        self.clock[0] = 1000.0

        found = metrics.marks(200)

        self.assertEqual(["library_scan", ""], [one["name"] for one in found])
        self.assertEqual([900.0, 950.0], [one["at"] for one in found])
        self.assertEqual(3, len(metrics.marks()))

    def test_the_launches_kept_are_bounded(self) -> None:
        for _ in range(metrics.LAUNCHES + 5):
            metrics.on_launched(game=None)

        self.assertEqual(metrics.LAUNCHES, len(metrics.marks()))


class CpuTemperatureTests(unittest.TestCase):
    def _on(self, chips: dict | None) -> float | None:
        fake = mock.Mock()
        fake.sensors_temperatures.return_value = chips
        with mock.patch.object(metrics, "psutil", fake):
            return metrics.cpu_temperature()

    def test_an_intel_package_wins_over_its_cores(self) -> None:
        self.assertEqual(self._on({"coretemp": [_reading("Core 0", 50.0),
                                                _reading("Package id 0", 64.0)]}), 64.0)

    def test_amd_reads_its_control_temperature(self) -> None:
        self.assertEqual(self._on({"k10temp": [_reading("Tccd1", 40.0),
                                               _reading("Tctl", 55.0)]}), 55.0)

    def test_a_known_chip_with_no_preferred_label_answers_with_its_first_reading(self) -> None:
        self.assertEqual(self._on({"cpu_thermal": [_reading("", 48.5)]}), 48.5)

    def test_the_processors_chip_is_preferred_to_another_listed_first(self) -> None:
        self.assertEqual(self._on({"nvme": [_reading("Composite", 33.0)],
                                   "coretemp": [_reading("Package id 0", 61.0)]}), 61.0)

    def test_a_thermal_zone_is_the_last_resort_and_no_other_chip_is_the_processor(
            self) -> None:
        self.assertEqual(self._on({"acpitz": [_reading("", 41.0)]}), 41.0)
        self.assertEqual(self._on({"nvme": [_reading("Composite", 33.0)],
                                   "soc_thermal": [_reading("", 47.0)]}), 47.0)
        for chip in ("nvme", "mt7921_phy0", "spd5118", "nct6798", "BAT0"):
            self.assertIsNone(self._on({chip: [_reading("", 33.0)]}), chip)

    def test_a_label_earlier_in_the_list_wins_whatever_order_the_sensors_come_in(self) -> None:
        self.assertEqual(self._on({"zenpower": [_reading("Tctl", 70.0), _reading("Tdie", 60.0)]}),
                         60.0)

    def test_a_platform_without_sensors_has_no_temperature(self) -> None:
        self.assertIsNone(self._on({}))
        with mock.patch.object(metrics, "psutil", mock.Mock(spec=["cpu_percent"])):
            self.assertIsNone(metrics.cpu_temperature())


class BusiestTests(unittest.TestCase):
    def setUp(self) -> None:
        metrics.reset_for_tests()
        self.addCleanup(metrics.reset_for_tests)
        self.fake = mock.Mock()
        self.fake.cpu_count.return_value = 4
        for name, value in (("psutil", self.fake), ("PER_CORE", True)):
            patch = mock.patch.object(metrics, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def _list(self, *processes: mock.Mock) -> list[dict] | None:
        self.fake.process_iter.return_value = list(processes)
        return metrics.busiest()

    def _listed(self, *processes: mock.Mock) -> list[dict]:
        found = self._list(*processes)
        assert found is not None
        return found

    def test_the_first_listing_is_nothing_because_every_process_reads_zero_once(self) -> None:
        self.assertIsNone(self._list(_process(10, "chrome", 0.0, 100)))
        self.assertEqual(self._list(_process(10, "chrome", 80.0, 100)),
                         [{"name": "chrome", "cpu_percent": 80.0, "memory": 100}])

    def test_processes_with_one_name_are_one_row_and_the_share_is_per_core(self) -> None:
        self._list()
        found = self._list(_process(10, "chrome", 40.0, 100), _process(11, "chrome", 20.0, 50),
                           _process(12, "vpx", 20.0, 500))
        self.assertEqual(found, [{"name": "chrome", "cpu_percent": 60.0, "memory": 150},
                                 {"name": "vpx", "cpu_percent": 20.0, "memory": 500}])

    def test_where_the_platform_reads_the_whole_machine_the_share_is_divided_by_the_cores(
            self) -> None:
        self._list()
        with mock.patch.object(metrics, "PER_CORE", False):
            found = self._list(_process(10, "chrome", 40.0, 100), _process(11, "chrome", 20.0, 50))
        self.assertEqual(found, [{"name": "chrome", "cpu_percent": 15.0, "memory": 150}])

    def test_an_apps_helpers_are_one_row_under_the_apps_name(self) -> None:
        app = "/Applications/Claude.app/Contents"
        self._list()
        found = self._listed(
            _process(10, "Claude", 10.0, 100, f"{app}/MacOS/Claude"),
            _process(11, "Claude Helper (Renderer)", 30.0, 50,
                     f"{app}/Frameworks/Claude Helper (Renderer).app/Contents/MacOS/Claude Helper"),
            _process(12, "claude", 5.0, 7, "/opt/tools/bin/claude"),
            _process(13, "vpx", 1.0, 9, "C:\\Games\\vpx.exe"),
            _process(14, "kworker", 0.5, 1, ""),
            _process(15, "hidden", 0.4, 2, None),
            _process(16, "bare", 0.3, 3, "/opt/.app/tool"))
        self.assertEqual([(one["name"], one["cpu_percent"], one["memory"]) for one in found],
                         [("Claude", 40.0, 150), ("claude", 5.0, 7), ("vpx", 1.0, 9),
                          ("kworker", 0.5, 1), ("hidden", 0.4, 2)])

    def test_the_busiest_five_are_listed_and_the_ties_by_size(self) -> None:
        self._list()
        found = self._listed(*(_process(10 + n, f"p{n}", float(n), n) for n in range(8)),
                              _process(30, "big", 0.0, 10 ** 9))
        self.assertEqual([one["name"] for one in found], ["p7", "p6", "p5", "p4", "p3"])
        self.assertEqual(self._listed(_process(31, "small", 0.0, 1),
                                      _process(32, "big", 0.0, 9))[0]["name"], "big")

    def test_this_process_is_named_for_the_app_and_not_the_interpreter(self) -> None:
        self._list()
        found = self._listed(_process(os.getpid(), "python3.12", 40.0, 100))
        self.assertEqual(found[0]["name"], metrics.OWN)

    def test_a_process_the_os_will_not_let_us_read_is_skipped(self) -> None:
        self._list()
        found = self._listed(_process(10, "root-owned", None, 100), _process(11, "also", 8.0, None),
                              _process(0, "System Idle Process", 400.0, 0),
                              _process(12, "mine", 8.0, 5))
        self.assertEqual([one["name"] for one in found], ["mine"])

    def test_a_listing_that_fails_is_no_list(self) -> None:
        self.fake.process_iter.side_effect = OSError
        self.assertIsNone(metrics.busiest())


class AVolumeThatFailsWhenRead(unittest.TestCase):
    def test_it_says_why_in_words(self) -> None:
        here = str(Path(__file__).parent)
        with mock.patch.object(metrics.shutil, "disk_usage",
                               side_effect=PermissionError(13, "Permission denied")):
            rows = metrics._disks([here])
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0]["error"])
        self.assertNotIn("Errno", rows[0]["error"])


if __name__ == "__main__":
    unittest.main()
