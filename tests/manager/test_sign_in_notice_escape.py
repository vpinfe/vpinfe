"""Escape on a sign-in notice does what its Close button does, and a request still waiting
for Show or Ignore has no way out but those two."""

from __future__ import annotations

import unittest

from nicegui import ui

from common.security import approvals, sign_in
from manager.security import sign_in_notice
from tests.support.clicks import said

# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()
ADDRESS = "192.168.1.20"


class EscapeOnTheNotice(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = sign_in.SignIn(path=None)
        sign_in.use(self.signer)
        self.asks = approvals.Approvals()
        approvals.use(self.asks)
        self.addCleanup(sign_in.use, sign_in.SignIn())
        self.addCleanup(approvals.use, approvals.Approvals())

    def test_escape_on_a_code_dismisses_the_request(self) -> None:
        request = self.signer.reveal_now(ADDRESS)
        with _PAGE:
            sheet = sign_in_notice._ask(request)
        self.addCleanup(sheet.delete)

        said(sheet, "escapeKey")

        self.assertIsNone(self.signer.pending())

    def test_a_request_still_waiting_cannot_be_left_with_escape(self) -> None:
        self.signer.ask(ADDRESS)
        request = self.signer.pending()
        assert request is not None
        with _PAGE:
            sheet = sign_in_notice._ask(request)
        self.addCleanup(sheet.delete)

        said(sheet, "escapeKey")

        self.assertIn("persistent", sheet.props)
        self.assertEqual(request.id, getattr(self.signer.pending(), "id", None))

    def test_escape_on_a_confirm_code_closes_it_for_good(self) -> None:
        code = sign_in.Confirm(id="c1", session_id="session-1", address=ADDRESS,
                               asked=0.0, code="123456")
        closed: set[str] = set()
        with _PAGE:
            sheet = sign_in_notice._show(code, closed)
        self.addCleanup(sheet.delete)

        said(sheet, "escapeKey")

        self.assertEqual({code.id}, closed)

    def test_escape_on_a_program_asking_takes_it_off_the_screen(self) -> None:
        ask = self.asks.ask("t1", "Home", "DELETE", "/games/a/folder")
        with _PAGE:
            sheet = sign_in_notice._approve(ask)
        self.addCleanup(sheet.delete)

        said(sheet, "escapeKey")

        self.assertIsNone(self.asks.on_screen())
        self.assertEqual([ask.id], [one.id for one in self.asks.waiting()])



class WatchingTheManager(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = sign_in.SignIn(path=None)
        sign_in.use(self.signer)
        self.addCleanup(sign_in.use, sign_in.SignIn())
        self.addCleanup(sign_in_notice._watching.clear)

    def test_the_last_page_closing_reveals_a_request_left_waiting(self) -> None:
        sign_in_notice._watching.add("page")
        self.signer.screen = sign_in_notice.watching
        self.signer.ask(ADDRESS)

        sign_in_notice._gone("page")

        pending = self.signer.pending()
        assert pending is not None
        self.assertEqual(pending.state, sign_in.SHOWN)

    def test_another_page_still_open_keeps_the_request_waiting(self) -> None:
        sign_in_notice._watching.update({"one", "two"})
        self.signer.screen = sign_in_notice.watching
        self.signer.ask(ADDRESS)

        sign_in_notice._gone("one")

        pending = self.signer.pending()
        assert pending is not None
        self.assertEqual(pending.state, sign_in.WAITING)


if __name__ == "__main__":
    unittest.main()
