"""Protects: (d) a release is published with Mac builds that will not open, or not published at all.

The signing request ends in one of three ways and the release path acts on the name alone:
signed uses the signed builds, withdrawn publishes the unsigned ones with a warning, rejected
publishes nothing. These run the request against recorded answers from the signing
repository and the source repository, with a clock that only moves when the code waits.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "mac_signing", ROOT / "scripts" / "mac_signing.py")
    assert spec is not None and spec.loader is not None, "the signing script is gone"
    module = importlib.util.module_from_spec(spec)
    sys.modules["mac_signing"] = module
    spec.loader.exec_module(module)
    return module


ms = _script()

REQUEST_ID = "100-1"
SIGNING_REPO = "example/signing"
DRAFT = "mac-signing-100-1"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


class Fake:
    """Answers by the longest matching path prefix. A list is used in turn, the last one kept."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.posted: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str) -> tuple[int, Any]:
        for prefix, answer in sorted(self.answers.items(), key=lambda kv: -len(kv[0])):
            if path.startswith(prefix):
                if isinstance(answer, list):
                    return answer.pop(0) if len(answer) > 1 else answer[0]
                return answer
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        self.posted.append((path, body))
        return self.answers["POST"]


def _request(repo: str = SIGNING_REPO) -> Any:
    return ms.Request(signing_repo=repo, source_repo="example/vpinfe", run_id="100",
                      run_attempt="1", sha="abc", ref="refs/tags/v1", tag="v1",
                      mode="release")


RUNS = "/repos/example/signing/actions/workflows/sign-mac.yml/runs"
RUN = "/repos/example/signing/actions/runs/9"
JOBS = RUN + "/jobs"
RELEASES = "/repos/example/vpinfe/releases"

FOUND = (200, {"workflow_runs": [{"id": 8, "name": "5-1", "display_title": "5-1"},
                                 {"id": 9, "name": REQUEST_ID, "display_title": REQUEST_ID}]})
NOT_FOUND = (200, {"workflow_runs": [{"id": 8, "name": "5-1", "display_title": "5-1"}]})


def _run(conclusion: str, status: str = "completed") -> tuple[int, Any]:
    return 200, {"id": 9, "status": status, "conclusion": conclusion}


def _draft(*names: str) -> dict[str, Any]:
    return {"id": 77, "draft": True, "name": DRAFT,
            "assets": [{"name": name} for name in names]}


ALL_THREE = ("VPinFE-full.dmg", "VPinFE-slim.dmg", "result.json")


def _go(request: Any, signer: Fake | None, source: Fake) -> Any:
    clock = Clock()
    return ms.request_signing(request, signer, source, sleep=clock.sleep, clock=clock)


class OutcomeTests(unittest.TestCase):
    def _signer(self, **answers: Any) -> Fake:
        return Fake({"POST": (204, None), RUNS: FOUND, **answers})

    def test_a_returned_draft_with_both_builds_is_signed(self) -> None:
        signer = self._signer(**{RUN: [_run("success", "in_progress"), _run("success")],
                                     JOBS: (200, {"jobs": [{"name": "sign",
                                                            "conclusion": "success"}]})})
        source = Fake({RELEASES: (200, [_draft(*ALL_THREE)])})

        result = _go(_request(), signer, source)

        self.assertEqual((result.outcome, result.draft_id), ("signed", 77))
        path, body = signer.posted[0]
        self.assertEqual(body["inputs"]["request_id"], "100-1")
        self.assertEqual(body["inputs"]["source_repo"], "example/vpinfe")
        self.assertTrue(path.endswith("/sign-mac.yml/dispatches"))

    def test_the_withdrawn_marker_job_is_withdrawn_not_a_failure(self) -> None:
        signer = self._signer(**{RUN: _run("success"),
                                     JOBS: (200, {"jobs": [{"name": "signing-withdrawn",
                                                            "conclusion": "success"}]})})

        result = _go(_request(), signer, Fake({RELEASES: (200, [])}))

        self.assertEqual(result.outcome, "withdrawn")

    def test_a_skipped_withdrawn_marker_is_not_withdrawn(self) -> None:
        signer = self._signer(**{RUN: _run("success"),
                                     JOBS: (200, {"jobs": [{"name": "signing-withdrawn",
                                                            "conclusion": "skipped"}]})})

        result = _go(_request(), signer, Fake({RELEASES: (200, [_draft(*ALL_THREE)])}))

        self.assertEqual(result.outcome, "signed")

    def test_a_failed_run_is_rejected(self) -> None:
        signer = self._signer(**{RUN: _run("failure"), JOBS: (200, {"jobs": []})})

        result = _go(_request(), signer, Fake({RELEASES: (200, [_draft(*ALL_THREE)])}))

        self.assertEqual(result.outcome, "rejected")

    def test_a_success_with_no_draft_is_rejected(self) -> None:
        signer = self._signer(**{RUN: _run("success"), JOBS: (200, {"jobs": []})})

        result = _go(_request(), signer, Fake({RELEASES: (200, [])}))

        self.assertEqual(result.outcome, "rejected")

    def test_a_draft_missing_a_build_is_rejected(self) -> None:
        signer = self._signer(**{RUN: _run("success"), JOBS: (200, {"jobs": []})})

        result = _go(_request(), signer,
                     Fake({RELEASES: (200, [_draft("VPinFE-full.dmg", "result.json")])}))

        self.assertEqual(result.outcome, "rejected")
        self.assertIn("VPinFE-slim.dmg", result.reason)

    def test_a_published_release_by_that_name_is_not_the_draft(self) -> None:
        signer = self._signer(**{RUN: _run("success"), JOBS: (200, {"jobs": []})})
        published = {**_draft(*ALL_THREE), "draft": False}

        result = _go(_request(), signer, Fake({RELEASES: (200, [published])}))

        self.assertEqual(result.outcome, "rejected")

    def test_a_run_that_never_finishes_is_rejected_after_an_hour(self) -> None:
        signer = self._signer(**{RUN: _run("", "in_progress")})
        clock = Clock()

        result = ms.request_signing(_request(), signer, Fake({}), sleep=clock.sleep,
                                    clock=clock)

        self.assertEqual(result.outcome, "rejected")
        self.assertGreaterEqual(clock.now, ms.RUN_WAIT)

    def test_no_run_within_ten_minutes_is_withdrawn(self) -> None:
        clock = Clock()

        result = ms.request_signing(_request(), self._signer(**{RUNS: NOT_FOUND}), Fake({}),
                                    sleep=clock.sleep, clock=clock)

        self.assertEqual(result.outcome, "withdrawn")
        self.assertGreaterEqual(clock.now, ms.FIND_WAIT)

    def test_a_refused_dispatch_is_withdrawn(self) -> None:
        for status in (401, 403, 404, 410, 422):
            with self.subTest(status=status):
                signer = Fake({"POST": (status, {"message": "no"})})

                result = _go(_request(), signer, Fake({}))

                self.assertEqual(result.outcome, "withdrawn")

    def test_a_dispatch_that_fails_for_another_reason_is_rejected(self) -> None:
        for status in (0, 429, 500, 502):
            with self.subTest(status=status):
                result = _go(_request(), Fake({"POST": (status, None)}), Fake({}))

                self.assertEqual(result.outcome, "rejected")

    def test_a_missing_token_or_repository_is_withdrawn_without_asking_anyone(self) -> None:
        self.assertEqual(_go(_request(), None, Fake({})).outcome, "withdrawn")
        self.assertEqual(_go(_request(""), Fake({}), Fake({})).outcome, "withdrawn")

    def test_every_message_says_how_to_turn_signing_off(self) -> None:
        for outcome in ("withdrawn", "rejected"):
            with self.subTest(outcome=outcome):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    ms.announce(ms.Outcome(outcome, "a reason"))
                line = out.getvalue()

                self.assertIn("MAC_SIGNING", line)
                self.assertIn("Remove Mac signing", line)

    def test_no_message_names_the_signing_repository(self) -> None:
        scenarios = [
            self._signer(**{RUN: _run("success"), JOBS: (200, {"jobs": []})}),
            self._signer(**{RUN: _run("failure"), JOBS: (200, {"jobs": []})}),
            self._signer(**{RUNS: NOT_FOUND}),
            Fake({"POST": (404, None)}),
            Fake({"POST": (500, None)}),
        ]
        for number, signer in enumerate(scenarios):
            with self.subTest(scenario=number):
                result = _go(_request(), signer, Fake({RELEASES: (200, [])}))
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    ms.announce(result)

                self.assertNotIn(SIGNING_REPO, result.reason + out.getvalue())

    def test_the_signing_repository_is_read_from_a_secret_so_logs_mask_it(self) -> None:
        build = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        called = (ROOT / ".github" / "workflows" / "mac-signing.yml").read_text(encoding="utf-8")

        self.assertNotIn("vars.MAC_SIGNING_REPO", build + called)
        self.assertNotIn("inputs.signing_repo", called)
        self.assertIn("MAC_SIGNING_REPO: ${{ secrets.MAC_SIGNING_REPO }}", build + called)

    def test_an_unset_off_switch_does_not_read_as_zero(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")

        self.assertNotIn("vars.MAC_SIGNING !=", workflow)
        self.assertIn("(vars.MAC_SIGNING || '1') != '0'", workflow)

    def test_the_signed_builds_replace_the_artifacts_the_release_reads(self) -> None:
        build = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        called = (ROOT / ".github" / "workflows" / "mac-signing.yml").read_text(encoding="utf-8")

        for name in ("build-macos-arm64", "build-macos-arm64-slim"):
            self.assertIn(f"artifact: {name}\n", build)
            self.assertRegex(
                called, rf"name: {name}\n\s+path: \S+\n\s+retention-days: \d+\n\s+overwrite: true")
        self.assertIn("name: ${{ matrix.artifact }}", build)
        self.assertNotIn("signed_artifact", build)


class ResultTests(unittest.TestCase):
    def _returned(self, directory: Path, **overrides: Any) -> None:
        full, slim = b"full dmg", b"slim dmg"
        (directory / "VPinFE-full.dmg").write_bytes(full)
        (directory / "VPinFE-slim.dmg").write_bytes(slim)
        result = {"request_id": "100-1", "source_sha": "abc", "team_id": "TEAM123456",
                  "sha256": {"VPinFE-full.dmg": hashlib.sha256(full).hexdigest(),
                             "VPinFE-slim.dmg": hashlib.sha256(slim).hexdigest()},
                  **overrides}
        (directory / "result.json").write_text(json.dumps(result), encoding="utf-8")

    def test_a_result_that_matches_the_request_has_no_problems(self) -> None:
        with TemporaryDirectory() as tmp:
            self._returned(Path(tmp))

            self.assertEqual(ms.check_result(Path(tmp), "100-1", "abc"), [])

    def test_a_result_for_another_request_or_commit_is_refused(self) -> None:
        cases = {"request_id": "99-1", "source_sha": "def", "team_id": ""}
        for key, wrong in cases.items():
            with self.subTest(key=key), TemporaryDirectory() as tmp:
                self._returned(Path(tmp), **{key: wrong})

                self.assertTrue(ms.check_result(Path(tmp), "100-1", "abc"))

    def test_a_build_changed_after_it_was_hashed_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            self._returned(Path(tmp))
            (Path(tmp) / "VPinFE-slim.dmg").write_bytes(b"swapped")

            problems = ms.check_result(Path(tmp), "100-1", "abc")

            self.assertEqual(len(problems), 1)
            self.assertIn("VPinFE-slim.dmg", problems[0])

    def test_the_layout_the_build_hands_over_has_every_key_the_script_checks(self) -> None:
        layout = json.loads((ROOT / "packaging" / "dmg-layout.json").read_text(encoding="utf-8"))

        for key, kind in ms.LAYOUT_KEYS.items():
            self.assertIsInstance(layout.get(key), kind, key)


if __name__ == "__main__":
    unittest.main()
