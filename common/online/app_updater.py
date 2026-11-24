"""Checking for a new VPinFE release, and staging one to install on restart."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import requests

from common import service_errors, timestamps
from common.atomic_write import STAGED_PREFIX, STAGED_SUFFIX, staged_for, write_atomic
from common.config.config_access import cfg_get
from common.config.paths import (
    LOG_DIR,
    UPDATE_CHECK_PATH,
    UPDATES_DIR,
    bundled,
    get_ini_config,
)
from common.i18n import t
from common.online.update_scripts import (
    _build_macos_update_script,
    _build_posix_update_script,
    _build_windows_bootstrap_script,
    _build_windows_update_script,
)
from common.release import release_notes
from common.security.http_client import download_file, get_json, unreachable
from common.vpinfe_version import get_version

logger = logging.getLogger("vpinfe.common.online.app_updater")


LAST_UPDATE_LOG = LOG_DIR / "last_update.log"
LATEST_RELEASE_URL = "https://api.github.com/repos/vpinfe/vpinfe/releases/latest"
# GitHub's "latest" leaves prereleases out, so the beta channel reads the list instead.
RELEASES_URL = "https://api.github.com/repos/vpinfe/vpinfe/releases?per_page=10"
STABLE, BETA = "stable", "beta"
CHANNEL_SETTING = ("device", "update_channel")
USER_AGENT = "VPinFE-Updater"
CHECK_EVERY_SECONDS = 24 * 60 * 60
RETRY_AFTER_FAILURE_SECONDS = 60 * 60

_checking = threading.Lock()
_downloading = threading.Lock()


class UpdateError(service_errors.BlockedError):
    """Raised when an update cannot be prepared or applied."""


def _triplet_candidates(triplet: str) -> list[str]:
    t = (triplet or "").strip()
    if not t:
        return []

    slim_suffix = "-slim"
    is_slim = t.endswith(slim_suffix)
    base = t[:-len(slim_suffix)] if is_slim else t

    aliases = {
        "linux-arm64": "linux-aarch64",
        "linux-aarch64": "linux-arm64",
    }
    candidates = [t]
    alias_base = aliases.get(base)
    if alias_base:
        candidates.append(f"{alias_base}{slim_suffix}" if is_slim else alias_base)
    return candidates


def _resolve_manifest_asset(manifest: dict, triplet: str | None) -> tuple[str | None, dict | None]:
    assets = manifest.get("assets") or {}
    if not isinstance(assets, dict):
        return None, None
    for candidate in _triplet_candidates(triplet or ""):
        asset = assets.get(candidate)
        if asset:
            return candidate, asset
    return None, None


def channel() -> str:
    """Which releases this install follows: `stable`, or `beta` to take a prerelease too."""
    kept = cfg_get(get_ini_config(), *CHANNEL_SETTING, STABLE).strip().lower()
    return kept if kept in (STABLE, BETA) else STABLE


def _highest(releases: object) -> dict:
    """The newest published release in a `/releases` listing, by version. GitHub orders
    that list by commit date, so a fix tagged on an older line can sit above a newer one."""
    best: dict | None = None
    for candidate in releases if isinstance(releases, list) else []:
        if not isinstance(candidate, dict) or candidate.get("draft"):
            continue
        tag = str(candidate.get("tag_name") or "")
        if release_notes.is_release(tag) and (
                best is None or release_notes.newer(tag, str(best["tag_name"]))):
            best = candidate
    if best is None:
        raise UpdateError(t("error.instance.latest_unreadable"))
    return best


def _request_json(url: str) -> Any:
    logger.debug("Fetching JSON from %s", url)
    payload = get_json(url, timeout=15, headers={"User-Agent": USER_AGENT})
    if isinstance(payload, dict):
        logger.debug("Fetched JSON from %s with keys=%s", url, sorted(payload.keys()))
    else:
        logger.debug("Fetched JSON from %s with type=%s", url, type(payload).__name__)
    return payload


def _download_file(url: str, dest: Path) -> None:
    download_file(url, dest, timeout=60, headers={"User-Agent": USER_AGENT})


def _append_log_line(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(message.rstrip() + "\n")


def _prune_old_update_dirs(keep_dir: Path) -> None:
    """Remove staged update directories other than the active one."""
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    keep_dir = keep_dir.resolve()
    for candidate in UPDATES_DIR.iterdir():
        if not candidate.is_dir():
            continue
        try:
            candidate_resolved = candidate.resolve()
            keep_dir.relative_to(candidate_resolved)
            continue
        except ValueError:
            pass
        shutil.rmtree(candidate, ignore_errors=True)


def _clear_abandoned_downloads(stage_dir: Path) -> None:
    """A download whose process was killed leaves its staged file, which `staged_for`
    only removes on a clean unwind. Call it holding `_downloading`, so no live download
    is in `stage_dir`."""
    for leftover in stage_dir.glob(f"{STAGED_PREFIX}*{STAGED_SUFFIX}"):
        leftover.unlink(missing_ok=True)


def _get_windows_powershell() -> str:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidates = [
        system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        Path("powershell.exe"),
    ]
    for candidate in candidates:
        if candidate.name.lower() == "powershell.exe" and str(candidate) == "powershell.exe":
            resolved = shutil.which(str(candidate))
            if resolved:
                return resolved
            continue
        if candidate.exists():
            return str(candidate)
    return "powershell.exe"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_release_asset(release_payload: dict, asset_name: str) -> dict | None:
    for asset in release_payload.get("assets", []):
        if asset.get("name") == asset_name:
            return asset
    return None


def _bundled_chromium_exists() -> bool:
    # `bundled()` is where the build puts data, and it is the first root checked. The
    # other two stay: this decides whether an update may proceed, and a wrong "no" here
    # is a user stuck on an old version. macOS is the case that earns the caution -
    # Chromium.app is copied into the bundle after PyInstaller runs, by the workflow
    # rather than by the spec.
    roots: list[Path] = [bundled()]

    exe_dir = Path(sys.executable).resolve().parent
    roots.append(exe_dir)
    roots.append(exe_dir / "_internal")

    system = platform.system()
    candidates: list[Path] = []
    if system == "Linux":
        candidates = [Path("chromium/linux/chrome/chrome")]
    elif system == "Windows":
        candidates = [
            Path("chromium/windows/chrome-win/chrome.exe"),
            Path("chromium/windows/chrome.exe"),
        ]
    elif system == "Darwin":
        candidates = [Path("chromium/Chromium.app/Contents/MacOS/Chromium")]

    for root in roots:
        if not root:
            continue
        for candidate in candidates:
            if (root / candidate).exists():
                return True
    return False


def _app_bundle(exe_path: Path) -> Path | None:
    """The .app this executable runs from, or None when it is not inside one."""
    for parent in exe_path.parents:
        if parent.suffix == ".app":
            return parent
    return None


def _mac_blocker(bundle: Path | None) -> str | None:
    """Why this .app cannot be swapped for the new one, or None when it can.

    A copy macOS runs from a randomized read-only mount (Gatekeeper's translocation of an
    app that was never moved) is not the installed app: a swap there would be lost. A
    disk image mounts read-only, so the folder check catches it.
    """
    if bundle is None or "AppTranslocation" in bundle.parts:
        return "macos_not_installed"
    if not os.access(bundle.parent, os.W_OK):
        return "macos_folder_not_writable"
    return None


def get_install_context() -> dict:
    version = get_version()
    system = platform.system()
    machine = platform.machine().lower()

    context = {
        "current_version": version,
        "supported": False,
        "reason": None,
        "triplet": None,
        "install_root": None,
        "launch_target": None,
        "platform": system,
        "is_frozen": bool(getattr(sys, "frozen", False)),
    }

    if not getattr(sys, "frozen", False):
        context["reason"] = "source_build"
        return context

    if not release_notes.is_release(version):
        context["reason"] = "non_release_build"
        return context

    exe_path = Path(sys.executable).resolve()
    slim = not _bundled_chromium_exists()

    if system == "Linux":
        if machine in {"x86_64", "amd64"}:
            triplet = "linux-x64"
        elif machine in {"arm64", "aarch64"}:
            triplet = "linux-arm64"
        else:
            context["reason"] = "unsupported_architecture"
            return context
        install_root = exe_path.parent
        launch_target = install_root / "vpinfe"
    elif system == "Windows":
        triplet = "win-x64"
        install_root = exe_path.parent
        bat_launcher = install_root / "vpinfe.bat"
        launch_target = bat_launcher if bat_launcher.exists() else exe_path
    elif system == "Darwin":
        if machine not in {"arm64", "aarch64"}:
            context["reason"] = "unsupported_architecture"
            return context
        bundle = _app_bundle(exe_path)
        blocked = _mac_blocker(bundle)
        if bundle is None or blocked:
            context["reason"] = blocked or "macos_not_installed"
            return context
        triplet = "macos-arm64"
        install_root = bundle
        launch_target = bundle
    else:
        context["reason"] = "unsupported_platform"
        return context

    if slim:
        triplet = f"{triplet}-slim"

    context.update(
        {
            "supported": True,
            "triplet": triplet,
            "install_root": install_root,
            "launch_target": launch_target,
            "slim": slim,
        }
    )
    logger.info(
        "Install context resolved: version=%s frozen=%s platform=%s triplet=%s "
        "supported=%s reason=%s install_root=%s launch_target=%s slim=%s",
        context["current_version"],
        context["is_frozen"],
        context["platform"],
        context["triplet"],
        context["supported"],
        context["reason"],
        context["install_root"],
        context["launch_target"],
        context.get("slim"),
    )
    return context


def _get_release_payload(followed: str) -> dict:
    if followed == BETA:
        payload = _highest(_request_json(RELEASES_URL))
    else:
        payload = _request_json(LATEST_RELEASE_URL)
        # GitHub leaves out what is flagged as a prerelease; one tagged as such and not
        # flagged still has to stay off the stable channel.
        if payload.get("prerelease") or release_notes.is_prerelease(
                str(payload.get("tag_name") or "")):
            raise UpdateError(t("error.instance.latest_unreadable"))
    logger.debug("Latest %s release payload tag_name=%s", followed, payload.get("tag_name"))
    return payload


def _get_release_manifest(release_payload: dict) -> dict:
    manifest_asset = _find_release_asset(release_payload, "manifest.json")
    if not manifest_asset:
        logger.warning("The latest release has no manifest.json")
        raise UpdateError(t("error.instance.release_incomplete"))
    manifest_url = manifest_asset.get("browser_download_url")
    if not manifest_url:
        logger.warning("The latest release's manifest.json has no download URL")
        raise UpdateError(t("error.instance.release_incomplete"))
    manifest = _request_json(manifest_url)
    logger.debug(
        "Release manifest version=%s assets=%s", manifest.get("version"),
        sorted((manifest.get("assets") or {}).keys()))
    return manifest


def _blank(context: dict) -> dict:
    return {
        "update_available": False,
        "error": None,
        "current_version": context["current_version"],
        "latest_version": None,
        "update_supported": False,
        "support_reason": context["reason"],
        "triplet": context["triplet"],
        "asset_name": None,
        "checked_at": None,
    }


def _judged(context: dict, release: dict, manifest: dict | None) -> dict | None:
    """What `release` means to this install. None when it needs a manifest it was not
    given. Logs nothing: it runs on every read of the kept answer."""
    result = _blank(context)
    latest_tag = str(release.get("tag_name") or "").strip()
    if not latest_tag:
        result["error"] = "missing_latest_tag"
        return result
    result["latest_version"] = latest_tag

    if not release_notes.is_release(context["current_version"]):
        result["update_available"] = None
        return result
    if not release_notes.is_release(latest_tag):
        result["error"] = "latest_tag_unparseable"
        return result

    result["update_available"] = release_notes.newer(latest_tag, context["current_version"])
    if not result["update_available"] or not context["supported"]:
        return result
    if manifest is None:
        return None

    _, asset_info = _resolve_manifest_asset(manifest, context["triplet"])
    if not asset_info:
        result["support_reason"] = "no_matching_asset"
        return result
    asset_name = asset_info.get("file")
    if not asset_name:
        result["support_reason"] = "asset_missing_file_name"
        return result
    if not _find_release_asset(release, asset_name):
        result["support_reason"] = "asset_not_attached_to_release"
        return result

    result["update_supported"] = True
    result["support_reason"] = None
    result["asset_name"] = asset_name
    return result


def _kept_release(payload: dict) -> dict:
    return {"tag_name": payload.get("tag_name"),
            "assets": [{"name": asset.get("name"),
                        "browser_download_url": asset.get("browser_download_url")}
                       for asset in payload.get("assets") or []]}


def _saved() -> dict | None:
    try:
        held = json.loads(UPDATE_CHECK_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Could not read the kept update check; asking again", exc_info=True)
        return None
    return held if isinstance(held, dict) else None


def _keep(record: dict) -> None:
    try:
        UPDATE_CHECK_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(UPDATE_CHECK_PATH, lambda out: json.dump(record, out, indent=2))
    except OSError:
        logger.warning("Could not keep the update check", exc_info=True)


def _fresh(record: dict, now: float, followed: str) -> bool:
    if record.get("channel", STABLE) != followed:
        return False
    if record.get("error"):
        since, within = record.get("attempted_at"), RETRY_AFTER_FAILURE_SECONDS
    else:
        since, within = record.get("checked_at"), CHECK_EVERY_SECONDS
    then = timestamps.iso_to_epoch(since)
    return then is not None and 0 <= now - then < within


def _answer(context: dict, record: dict) -> dict | None:
    release = record.get("release")
    answer = (_judged(context, release, record.get("manifest"))
              if isinstance(release, dict) else _blank(context))
    if answer is None:
        return None
    answer["checked_at"] = record.get("checked_at") or None
    answer["error"] = record.get("error") or answer["error"]
    return answer


def _kept_for(before: dict | None, followed: str) -> dict:
    """What was kept, unless it answered for the other channel: that release is no answer
    to this one."""
    if before is None or before.get("channel", STABLE) != followed:
        return {"channel": followed}
    return before


def _ask(context: dict, before: dict | None, followed: str) -> dict:
    """Asks GitHub and returns the record it kept."""
    now = timestamps.utc_now_iso()
    try:
        release = _kept_release(_get_release_payload(followed))
        manifest = (_get_release_manifest(release)
                    if _judged(context, release, None) is None else None)
        record = {"release": release, "manifest": manifest, "channel": followed,
                  "checked_at": now, "attempted_at": now, "error": None}
    except UpdateError as exc:
        logger.debug("Could not check for updates: %s", exc)
        record = {**_kept_for(before, followed), "attempted_at": now,
                  "error": "remote_check_failed"}
    except requests.RequestException as exc:
        unreachable(logger, "Could not check for updates", exc)
        record = {**_kept_for(before, followed), "attempted_at": now,
                  "error": "remote_check_failed"}
    except Exception:
        logger.exception("Could not check for updates")
        record = {**_kept_for(before, followed), "attempted_at": now,
                  "error": "remote_check_failed"}
    _keep(record)
    return record


def prepare_update() -> dict:
    context = get_install_context()
    if not context["supported"]:
        logger.warning("Cannot update in place: %s", context["reason"])
        raise UpdateError(t("error.instance.no_self_update"))

    release_payload = _get_release_payload(channel())
    latest_tag = (release_payload.get("tag_name") or "").strip()

    if not release_notes.is_release(latest_tag):
        logger.warning("The latest release's tag does not read as a version: %r",
                       latest_tag)
        raise UpdateError(t("error.instance.latest_unreadable"))
    if not release_notes.is_release(context["current_version"]):
        raise UpdateError(t("error.instance.unpublished"))
    if not release_notes.newer(latest_tag, context["current_version"]):
        raise UpdateError(t("error.instance.already_latest"))

    manifest = _get_release_manifest(release_payload)
    resolved_triplet, asset_info = _resolve_manifest_asset(manifest, context["triplet"])
    if not asset_info:
        logger.warning("The latest release has no asset for %s", context["triplet"])
        raise UpdateError(t("error.instance.no_package"))
    if resolved_triplet and resolved_triplet != context["triplet"]:
        logger.info(
            "Preparing update using compatible manifest triplet=%s for context triplet=%s",
            resolved_triplet,
            context["triplet"],
        )

    asset_name = asset_info.get("file")
    expected_sha = (asset_info.get("sha256") or "").strip().lower()
    if not asset_name or not expected_sha:
        logger.warning("The latest release's manifest has no file name or checksum for %s",
                       context["triplet"])
        raise UpdateError(t("error.instance.release_incomplete"))

    asset = _find_release_asset(release_payload, asset_name)
    if not asset:
        logger.warning("The latest release does not have %s attached", asset_name)
        raise UpdateError(t("error.instance.release_incomplete"))

    asset_url = asset.get("browser_download_url")
    if not asset_url:
        logger.warning("The latest release's %s has no download URL", asset_name)
        raise UpdateError(t("error.instance.release_incomplete"))

    stage_dir = UPDATES_DIR / latest_tag / (context["triplet"] or "unknown")
    stage_dir.mkdir(parents=True, exist_ok=True)
    _append_log_line(
        LAST_UPDATE_LOG, f"[Updater] Preparing update {latest_tag} for {context['triplet']}")

    zip_path = stage_dir / asset_name
    with _downloading:
        if zip_path.exists() and _sha256_file(zip_path) != expected_sha:
            _append_log_line(LAST_UPDATE_LOG, f"[Updater] Removing stale cached asset {zip_path}")
            zip_path.unlink()

        if not zip_path.exists():
            _clear_abandoned_downloads(stage_dir)
            with staged_for(zip_path) as temp_path:
                _append_log_line(LAST_UPDATE_LOG,
                                 f"[Updater] Downloading {asset_name} to {temp_path}")
                _download_file(asset_url, temp_path)
                actual_sha = _sha256_file(temp_path)
                _append_log_line(LAST_UPDATE_LOG, f"[Updater] Downloaded asset sha256={actual_sha}")
                if actual_sha != expected_sha:
                    raise UpdateError(t("error.instance.download_damaged"))
            _append_log_line(LAST_UPDATE_LOG, f"[Updater] Cached verified asset at {zip_path}")
        else:
            _append_log_line(LAST_UPDATE_LOG, f"[Updater] Reusing cached asset {zip_path}")

    return {
        "latest_version": latest_tag,
        "zip_path": str(zip_path),
        "stage_dir": str(stage_dir),
        "install_root": str(context["install_root"]),
        "launch_target": str(context["launch_target"]),
        "launch_exe": str(Path(context["install_root"]) / "vpinfe.exe"),
        "platform": context["platform"],
        "triplet": context["triplet"],
        "asset_name": asset_name,
        "last_update_log": str(LAST_UPDATE_LOG),
    }




def force_exit_after_handoff(delay_seconds: int = 8) -> None:
    """Stop this process lingering after the updater has been handed the install.

    The staged script waits on this pid before it swaps any files, so a shutdown that
    hangs does not delay the update - it prevents it. Every surface that hands off wants
    this, so it lives beside the handoff rather than in whichever UI called it.
    """
    def _worker() -> None:
        time.sleep(delay_seconds)
        logger.warning("Forcing process exit after update handoff; graceful shutdown "
                       "did not complete in %ss", delay_seconds)
        os._exit(0)

    threading.Thread(target=_worker, daemon=True, name="update-force-exit").start()


def launch_prepared_update(prepared: dict) -> None:
    stage_dir = Path(prepared["stage_dir"])
    stage_dir.mkdir(parents=True, exist_ok=True)
    _prune_old_update_dirs(stage_dir)
    current_pid = os.getpid()
    log_path = stage_dir / "apply_update.log"
    _append_log_line(
        Path(prepared["last_update_log"]),
        f"[Updater] Launching detached updater for {prepared['latest_version']}")

    if platform.system() == "Windows":
        script_path = stage_dir / "apply_update.ps1"
        bootstrap_log_path = stage_dir / "bootstrap.log"
        script_path.write_text(
            _build_windows_update_script(prepared, current_pid, log_path), encoding="utf-8")
        _append_log_line(
            Path(prepared["last_update_log"]),
            f"[Updater] PowerShell script written to {script_path}")
        powershell_exe = _get_windows_powershell()
        _append_log_line(
            Path(prepared["last_update_log"]), f"[Updater] Using PowerShell at {powershell_exe}")
        bootstrap_path = stage_dir / "launch_update.cmd"
        bootstrap_path.write_text(
            _build_windows_bootstrap_script(
                powershell_exe=powershell_exe,
                script_path=script_path,
                stable_log=Path(prepared["last_update_log"]),
                bootstrap_log=bootstrap_log_path,
            ),
            encoding="utf-8",
        )
        _append_log_line(
            Path(prepared["last_update_log"]),
            f"[Updater] Bootstrap script written to {bootstrap_path}")
        _append_log_line(
            Path(prepared["last_update_log"]), f"[Updater] Bootstrap log path {bootstrap_log_path}")
        cmd_exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        _append_log_line(
            Path(prepared["last_update_log"]),
            f"[Updater] Launching bootstrap via {cmd_exe} /c {bootstrap_path}")
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(
            [
                cmd_exe,
                "/c",
                str(bootstrap_path),
            ],
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        return

    script_path = stage_dir / "apply_update.sh"
    build_script = (_build_macos_update_script if platform.system() == "Darwin"
                    else _build_posix_update_script)
    script_path.write_text(build_script(prepared, current_pid, log_path), encoding="utf-8")
    script_path.chmod(0o755)
    _append_log_line(
        Path(prepared["last_update_log"]), f"[Updater] Shell script written to {script_path}")
    subprocess.Popen(
        ["/bin/sh", str(script_path)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )


def check_now(refresh: bool = False) -> dict:
    """What this install could become, and whether it can get there itself.

    From the answer kept under `cache/` while it is fresh, else from GitHub; `refresh`
    asks GitHub now. `checked_at` is the last success and `error` the last attempt's.
    Never raises: not knowing whether an update exists is not a reason to fail the
    question, and `error` carries it.
    """
    try:
        context = get_install_context()
        followed = channel()
        with _checking:
            record = _saved()
            if not refresh and record is not None and _fresh(record, time.time(), followed):
                kept = _answer(context, record)
                if kept is not None:
                    return kept
            record = _ask(context, record, followed)
        answer = _answer(context, record) or {
            **_blank(context), "checked_at": record.get("checked_at") or None,
            "error": record.get("error") or "check_failed"}
        logger.info("Update check: latest=%s available=%s supported=%s reason=%s error=%s",
                    answer["latest_version"], answer["update_available"],
                    answer["update_supported"], answer["support_reason"], answer["error"])
        return answer
    except Exception:
        logger.exception("Could not check for updates")
        return {"update_available": False, "error": "check_failed",
                "current_version": get_version(), "latest_version": None,
                "update_supported": False, "support_reason": "check_failed",
                "triplet": None, "asset_name": None, "checked_at": None}


def take_published(*, stop_table: bool = False) -> dict:
    """Stage the published build and hand off to the staged updater.

    Order matters: the download happens before anything is stopped, so a failed or
    unavailable update costs nobody their game. Only once there is a verified package does
    a running table get closed.
    """
    from common.devices import device_client
    from common.host import launch_state
    from common.runtime import lifecycle

    context = get_install_context()
    if not context["supported"]:
        raise service_errors.UnavailableError(
            t("error.instance.no_self_update"),
            details={"support_reason": context["reason"]})

    playing = launch_state.current()
    if playing.launching and not stop_table:
        raise service_errors.BlockedError(t("error.instance.table_running"),
                                          details={"game_name": playing.game_name})

    try:
        prepared = prepare_update()
    except requests.RequestException as exc:
        unreachable(logger, "Could not take the published build", exc)
        raise UpdateError(t("error.instance.latest_unreachable")) from exc

    stopped_table = None
    if playing.launching and device_client.local().request(
            lifecycle.TABLE, lifecycle.STOP,
            origin=lifecycle.Origin(lifecycle.SURFACE_API),
            reason="making way for an update"):
        stopped_table = playing.game_name

    launch_prepared_update(prepared)
    force_exit_after_handoff()
    return {"latest_version": prepared["latest_version"],
            "stopped_table": stopped_table}


def quit_for_update() -> None:
    """Go down the ordinary way, so the services shut down and the windows close."""
    from common.devices import device_client
    from common.runtime import lifecycle

    device_client.local().request(
        lifecycle.VPINFE, lifecycle.STOP,
        origin=lifecycle.Origin(lifecycle.SURFACE_API),
        reason="an update is staged")
