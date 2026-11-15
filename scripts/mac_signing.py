"""Mac signing, source side: what the build hands the signing repo and what it accepts back.

Optional layer. Nothing else in the repo imports this file; .github/workflows/mac-signing.yml
and the Mac steps of build.yml are its only callers. Removing signing means deleting those
and this file - see "Remove Mac signing" in docs/mac-signing.md.

Three commands, each run by the workflow:

  prepare-input   zip the .app and gather the DMG inputs the signing repo needs
  request         ask the signing repo for a signed build and say what came of it
  check-result    check what the signing repo returned before it is used

`request` writes `outcome` (signed, withdrawn or rejected), `reason` and `draft_id` to
$GITHUB_OUTPUT and exits 1 only on a rejection. Withdrawn is not a failure: the build goes on
with its unsigned Mac builds and says so.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

DISABLE = (
    'Set the repository variable MAC_SIGNING to 0 to skip signing, or follow "Remove Mac '
    'signing" to remove it permanently.'
)

SIGN_WORKFLOW = "sign-mac.yml"
INPUT_ARTIFACTS = "mac-sign-input-full,mac-sign-input-slim"
WITHDRAWN_JOB = "signing-withdrawn"
RETURNED = ("VPinFE-full.dmg", "VPinFE-slim.dmg", "result.json")

# A refusal from the signing repo is its way of saying "not any more": the token was revoked
# or expired, the repository or workflow is gone, or the dispatch no longer validates.
REFUSED = frozenset({401, 403, 404, 410, 422})

FIND_WAIT = 10 * 60
RUN_WAIT = 60 * 60
FIND_EVERY = 10
POLL_EVERY = 30
UNREADABLE_LIMIT = 10

LAYOUT_KEYS: dict[str, type | tuple[type, ...]] = {
    "volname": str,
    "app_name": str,
    "hide_extension": bool,
    "window_pos": list,
    "window_size": list,
    "icon_size": int,
    "app_icon_pos": list,
    "app_drop_link_pos": list,
}


class Gh(Protocol):
    def get(self, path: str) -> tuple[int, Any]: ...

    def post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]: ...


class RestApi:
    """The GitHub REST API with one token. Status 0 is a request that never got an answer."""

    def __init__(self, token: str) -> None:
        self._token = token

    def get(self, path: str) -> tuple[int, Any]:
        return self._call("GET", path, None)

    def post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        return self._call("POST", path, body)

    def _call(self, method: str, path: str, body: dict[str, Any] | None) -> tuple[int, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        status, raw = 0, b""
        # A dispatch that timed out may already have started a run, so only reads are retried.
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    status, raw = response.status, response.read()
            except urllib.error.HTTPError as error:
                status, raw = error.code, error.read()
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
                status, raw = 0, b""
            if status != 0 and status < 500:
                break
            if attempt < attempts - 1:
                time.sleep(5)
        try:
            return status, json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return status, None


@dataclass(frozen=True)
class Request:
    signing_repo: str
    source_repo: str
    run_id: str
    run_attempt: str
    sha: str
    ref: str
    tag: str
    mode: str

    @property
    def request_id(self) -> str:
        return f"{self.run_id}-{self.run_attempt}"

    @property
    def draft_name(self) -> str:
        return f"mac-signing-{self.request_id}"

    def dispatch_body(self) -> dict[str, Any]:
        return {
            "ref": "main",
            "inputs": {
                "source_repo": self.source_repo,
                "source_run_id": self.run_id,
                "source_run_attempt": self.run_attempt,
                "source_sha": self.sha,
                "source_ref": self.ref,
                "tag": self.tag,
                "mode": self.mode,
                "request_id": self.request_id,
                "artifacts": INPUT_ARTIFACTS,
            },
        }


@dataclass(frozen=True)
class Outcome:
    outcome: str
    reason: str
    draft_id: int | None = None


def classify(request: Request, run: dict[str, Any], jobs: list[dict[str, Any]],
             releases: list[dict[str, Any]]) -> Outcome:
    """What a finished signing run came to. `releases` is the source repo's release list."""
    conclusion = run.get("conclusion")
    if conclusion != "success":
        return Outcome("rejected", f"the signing run ended with {conclusion}")
    if any(job.get("name") == WITHDRAWN_JOB and job.get("conclusion") == "success"
           for job in jobs):
        return Outcome("withdrawn", "signing has been withdrawn in the signing repository")
    for release in releases:
        if not release.get("draft"):
            continue
        if request.draft_name not in (release.get("tag_name"), release.get("name")):
            continue
        names = {asset.get("name") for asset in release.get("assets") or []}
        missing = [name for name in RETURNED if name not in names]
        if missing:
            return Outcome("rejected", f"the signed draft is missing {', '.join(missing)}")
        return Outcome("signed", "both Mac builds were signed and notarized",
                       int(release["id"]))
    return Outcome("rejected", f"the signing run succeeded but left no draft named "
                               f"{request.draft_name}")


def request_signing(request: Request, signer: Gh | None, source: Gh,
                    sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic) -> Outcome:
    if not request.signing_repo:
        return Outcome("withdrawn", "the secret MAC_SIGNING_REPO is not set")
    if signer is None:
        return Outcome("withdrawn", "the secret MAC_SIGNING_TOKEN is not set")

    repo = request.signing_repo
    status, _ = signer.post(f"/repos/{repo}/actions/workflows/{SIGN_WORKFLOW}/dispatches",
                              request.dispatch_body())
    if status in REFUSED:
        return Outcome("withdrawn", f"the signing repository refused the request ({status})")
    if status != 204:
        return Outcome("rejected", f"the request to the signing repository failed ({status})")

    run = _find_run(request, signer, sleep, clock)
    if run is None:
        return Outcome("withdrawn", "no signing run appeared within "
                                    f"{FIND_WAIT // 60} minutes")

    deadline = clock() + RUN_WAIT
    unreadable = 0
    while True:
        status, current = signer.get(f"/repos/{repo}/actions/runs/{run['id']}")
        if status == 200 and isinstance(current, dict):
            unreadable = 0
            if current.get("status") == "completed":
                return _finished(request, signer, source, current)
        else:
            unreadable += 1
            if unreadable >= UNREADABLE_LIMIT:
                return Outcome("rejected", f"the signing run could not be read ({status})")
        if clock() >= deadline:
            return Outcome("rejected", f"the signing run did not finish within "
                                       f"{RUN_WAIT // 60} minutes")
        sleep(POLL_EVERY)


def _find_run(request: Request, signer: Gh, sleep: Callable[[float], None],
              clock: Callable[[], float]) -> dict[str, Any] | None:
    deadline = clock() + FIND_WAIT
    path = (f"/repos/{request.signing_repo}/actions/workflows/{SIGN_WORKFLOW}/runs"
            "?event=workflow_dispatch&per_page=50")
    while True:
        status, payload = signer.get(path)
        if status == 200 and isinstance(payload, dict):
            for run in payload.get("workflow_runs") or []:
                if request.request_id in (run.get("name"), run.get("display_title")):
                    return dict(run)
        if clock() >= deadline:
            return None
        sleep(FIND_EVERY)


def _finished(request: Request, signer: Gh, source: Gh, run: dict[str, Any]) -> Outcome:
    status, payload = signer.get(
        f"/repos/{request.signing_repo}/actions/runs/{run['id']}/jobs?per_page=100")
    if status != 200 or not isinstance(payload, dict):
        return Outcome("rejected", f"the signing run's jobs could not be read ({status})")
    status, releases = source.get(f"/repos/{request.source_repo}/releases?per_page=100")
    if status != 200 or not isinstance(releases, list):
        return Outcome("rejected", f"this repository's releases could not be read ({status})")
    return classify(request, run, payload.get("jobs") or [], releases)


def check_result(directory: Path, request_id: str, sha: str) -> list[str]:
    """What is wrong with the returned files, empty when nothing is."""
    problems: list[str] = []
    try:
        result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"result.json cannot be read: {error}"]
    if not isinstance(result, dict):
        return ["result.json is not an object"]
    if result.get("request_id") != request_id:
        problems.append(f"result.json is for request {result.get('request_id')}, "
                        f"not {request_id}")
    if result.get("source_sha") != sha:
        problems.append(f"result.json is for commit {result.get('source_sha')}, not {sha}")
    if not result.get("team_id"):
        problems.append("result.json names no team")
    digests = result.get("sha256")
    digests = digests if isinstance(digests, dict) else {}
    for name in RETURNED[:2]:
        actual = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        if str(digests.get(name, "")).lower() != actual:
            problems.append(f"{name} does not match the SHA-256 in result.json")
    return problems


def prepare_input(app: Path, out: Path, packaging: Path) -> None:
    layout_path = packaging / "dmg-layout.json"
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    for key, kind in LAYOUT_KEYS.items():
        if not isinstance(layout.get(key), kind):
            raise SystemExit(f"{layout_path} has no usable {key}")
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app), str(out / "VPinFE.app.zip")],
                   check=True)
    dmg = out / "dmg"
    dmg.mkdir(exist_ok=True)
    shutil.copyfile(packaging / "VPinFE.icns", dmg / "VPinFE.icns")
    shutil.copyfile(packaging / "DMG_background.png", dmg / "DMG_background.png")
    shutil.copyfile(layout_path, dmg / "layout.json")


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _append(variable: str, text: str) -> None:
    target = os.environ.get(variable)
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            handle.write(text)


def announce(result: Outcome) -> None:
    """The annotation and the summary banner for an outcome. Every one ends with how to
    turn signing off."""
    if result.outcome == "signed":
        print(f"::notice title=Mac builds signed::{_escape(result.reason)}")
        _append("GITHUB_STEP_SUMMARY", f"### Mac builds signed\n\n{result.reason}.\n")
        return
    if result.outcome == "withdrawn":
        title, level, heading = "Mac builds will be unsigned", "warning", \
            "Mac builds are unsigned: signing is not available"
    else:
        title, level, heading = "Mac signing was rejected", "error", \
            "Mac signing was rejected, nothing will be published"
    print(f"::{level} title={title}::{_escape(result.reason + '. ' + DISABLE)}")
    _append("GITHUB_STEP_SUMMARY", f"### {heading}\n\n{result.reason}.\n\n{DISABLE}\n")


def _request_from_env() -> tuple[Request, Gh | None, Gh]:
    env = os.environ
    request = Request(
        signing_repo=env.get("MAC_SIGNING_REPO", "").strip(),
        source_repo=env["GITHUB_REPOSITORY"],
        run_id=env["GITHUB_RUN_ID"],
        run_attempt=env["GITHUB_RUN_ATTEMPT"],
        sha=env["GITHUB_SHA"],
        ref=env["GITHUB_REF"],
        tag=env.get("MAC_SIGN_TAG", "-") or "-",
        mode=env["MAC_SIGN_MODE"],
    )
    token = env.get("MAC_SIGNING_TOKEN", "").strip()
    return request, RestApi(token) if token else None, RestApi(env["GITHUB_TOKEN"])


def _run_request() -> int:
    request, signer, source = _request_from_env()
    result = request_signing(request, signer, source)
    announce(result)
    _append("GITHUB_OUTPUT", f"outcome={result.outcome}\nreason={result.reason}\n"
                             f"draft_id={result.draft_id or ''}\n")
    print(f"{result.outcome}: {result.reason}")
    return 1 if result.outcome == "rejected" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-input")
    prepare.add_argument("--app", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    prepare.add_argument("--packaging", type=Path, default=Path("packaging"))
    sub.add_parser("request")
    check = sub.add_parser("check-result")
    check.add_argument("--dir", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "prepare-input":
        prepare_input(args.app, args.out, args.packaging)
        return 0
    if args.command == "request":
        return _run_request()
    problems = check_result(args.dir, os.environ["EXPECT_REQUEST_ID"], os.environ["EXPECT_SHA"])
    for problem in problems:
        print(f"::error title=The signed build does not check out::{_escape(problem)}. "
              f"{_escape(DISABLE)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
