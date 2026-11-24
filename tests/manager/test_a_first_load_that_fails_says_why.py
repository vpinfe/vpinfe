"""A first load that fails is worded for the install serving the page."""

from __future__ import annotations

import unittest

import requests

from common.i18n import t
from manager.kit.api import ApiError
from manager.page import why_not_loaded


def _raised(message: str, cause: BaseException | None) -> ApiError:
    try:
        raise ApiError(message) from cause
    except ApiError as exc:
        return exc


class WhyNotLoadedTests(unittest.TestCase):
    def test_a_timeout_is_this_install_taking_too_long(self) -> None:
        exc = _raised(t("device.reason.timed_out"), requests.ReadTimeout())
        self.assertEqual(why_not_loaded(exc), t("manager.page.timeout"))

    def test_an_answer_from_the_api_is_said_in_its_own_words(self) -> None:
        exc = _raised("The games folder is not there", None)
        self.assertEqual(why_not_loaded(exc), "The games folder is not there")


if __name__ == "__main__":
    unittest.main()
