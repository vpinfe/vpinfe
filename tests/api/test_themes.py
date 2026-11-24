"""Theme calls over HTTP."""

from __future__ import annotations

import errno
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import httpapi
from common.i18n import t
from common.online import theme_ops
from tests.support.library import TempTree


class SourcesThatWillNotLoadTests(TempTree):
    CALLS: tuple[tuple[str, str, dict | None], ...] = (
             ("get", "/themes", None),
             ("post", "/themes/Reference/install", None),
             ("delete", "/themes/Reference", None),
             ("put", "/themes/active", {"key": "Reference"}),
             ("get", "/themes/Reference/options", None),
             ("put", "/themes/Reference/options", {"values": {}}))

    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.enterContext(patch.object(
            theme_ops, "_loaded",
            side_effect=OSError(errno.EHOSTUNREACH, "No route to host")))

    def test_every_call_says_so_in_words_rather_than_breaking(self) -> None:
        said = t("said.why.unreachable")
        for method, path, body in self.CALLS:
            with self.subTest(method=method, path=path):
                answer = self.client.request(method, path, json=body)

                self.assertEqual(answer.status_code, 503)
                self.assertEqual(answer.json()["error"]["message"], said)


class NoThemeNamedTests(TempTree):
    def test_making_no_theme_active_asks_for_its_name(self) -> None:
        client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        with patch.object(theme_ops, "_loaded"):
            answer = client.put("/themes/active", json={"key": " "})

        self.assertEqual(answer.status_code, 400)
        self.assertEqual(answer.json()["error"]["message"],
                         t("error.themes.none_named"))


class ThemeSourceTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_the_sources_are_listed_without_reading_them(self) -> None:
        said = {"registries": [], "repositories": [], "checked": ""}
        with patch.object(theme_ops, "sources", return_value=said) as listed, \
                patch.object(theme_ops, "_loaded") as read:
            answer = self.client.get("/themes/sources")

        self.assertEqual((answer.status_code, answer.json()), (200, said))
        listed.assert_called_once_with()
        read.assert_not_called()

    def test_checking_an_address_hands_it_over_and_answers_what_it_is(self) -> None:
        said = {"address": "https://example.net/t.json", "state": "ready", "addable": True}
        with patch.object(theme_ops, "check_source", return_value=said) as checked:
            answer = self.client.post("/themes/sources/check",
                                      json={"address": "https://example.net/t.json"})

        self.assertEqual((answer.status_code, answer.json()), (200, said))
        checked.assert_called_once_with("https://example.net/t.json")

    def test_checking_no_address_is_still_an_answer(self) -> None:
        with patch.object(theme_ops, "check_source", return_value={"state": "not_a_theme"}) \
                as checked:
            answer = self.client.post("/themes/sources/check", json={})

        self.assertEqual(answer.status_code, 200)
        checked.assert_called_once_with("")

    def test_naming_a_source_hands_over_the_address_and_the_name(self) -> None:
        said = {"address": "a", "label": "Mine"}
        with patch.object(theme_ops, "rename_source", return_value=said) as named:
            answer = self.client.put("/themes/sources/name",
                                     json={"address": "a", "name": "Mine"})

        self.assertEqual((answer.status_code, answer.json()), (200, said))
        named.assert_called_once_with("a", "Mine")

    def test_naming_a_source_that_is_not_added_is_a_404(self) -> None:
        with patch.object(theme_ops, "_configured_kinds", return_value={}):
            answer = self.client.put("/themes/sources/name",
                                     json={"address": "https://example.net/t.json", "name": "x"})

        self.assertEqual(answer.status_code, 404)
        self.assertEqual(answer.json()["error"]["message"], t("error.themes.unknown_source"))


if __name__ == "__main__":
    unittest.main()
