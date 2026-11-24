"""Protects: (b) a support package is not made while an unofficial extension runs, in the
package and over the API.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from common import extensions
from common.support import package
from tests.supportpackage.test_api import _Served


def _running(*names: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(name=one) for one in names]


class WhileOneRuns(_Served):
    def test_the_package_is_refused_and_says_which(self) -> None:
        with patch.object(extensions, "unofficial_running", return_value=_running("beta")):
            with self.assertRaises(package.UnofficialRunningError) as raised:
                package.build_package(package.Options())

        self.assertEqual(raised.exception.names, ("beta",))

    def test_the_api_answers_a_409_naming_it(self) -> None:
        with patch.object(extensions, "unofficial_running", return_value=_running("beta")):
            answer = self.client.post("/support", json={})

        self.assertEqual(answer.status_code, 409)
        self.assertIn("beta", answer.text)

    def test_with_none_running_a_package_is_made(self) -> None:
        with patch.object(extensions, "unofficial_running", return_value=[]):
            self.assertEqual(self.client.post("/support", json={}).status_code, 200)
