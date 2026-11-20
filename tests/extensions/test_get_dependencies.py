"""Protects: (a) no dependency is downloaded for an extension the Owner has not trusted, and (d)
fetching what a trusted extension lacks starts it without a restart.
"""

from __future__ import annotations

from unittest import mock

from common import extensions as live
from common import service_errors
from common.extensions import host
from common.online import extension_dependencies, extension_ops
from tests.extensions.test_extension_dependencies_host import BODY, DependencyCase


class GetDependenciesTests(DependencyCase):
    def setUp(self) -> None:
        super().setUp()
        previous = live.registry()
        live.set_registry(self.registry)
        self.addCleanup(live.set_registry, previous)

    def test_an_untrusted_extension_fetches_nothing(self) -> None:
        directory = self.make("wait", {"dependencies": ["probe-one==1.0"]},
                              BODY.format(module="probe_one"))
        self.registry._start(self.registry._prepare(directory))
        with (mock.patch.object(host.Record, "is_accepted", return_value=False),
              mock.patch.object(extension_dependencies, "fetch") as fetch):
            with self.assertRaises(service_errors.BlockedError):
                extension_ops.get_dependencies("wait")
        fetch.assert_not_called()

    def test_a_trusted_extension_starts_once_its_dependencies_are_there(self) -> None:
        record = self.extension("wait", "probe-one==1.0")
        self.assertEqual(record.why, host.LACKS_DEPENDENCIES)

        def arrive(_wanted: object) -> None:
            self.dependency("probe-one", "1.0", "probe_one")

        with mock.patch.object(extension_dependencies, "fetch", side_effect=arrive):
            extension_ops.get_dependencies("wait")
        self.assertTrue(record.running, record.reason)

    def test_a_failed_fetch_leaves_it_waiting_and_says_why(self) -> None:
        record = self.extension("wait", "probe-one==1.0")
        failing = extension_dependencies.DependencyError("could not reach the index")
        with mock.patch.object(extension_dependencies, "fetch", side_effect=failing):
            with self.assertRaises(extension_ops.InstallFailedError):
                extension_ops.get_dependencies("wait")
        self.assertEqual((record.state, record.why), (host.OFF, host.LACKS_DEPENDENCIES))
