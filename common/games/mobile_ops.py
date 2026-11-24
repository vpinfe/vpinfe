"""What a VPX Mobile device is carrying, and putting games on it or taking them off.

Asked of the device, never remembered. A phone is taken away, filled up and emptied by
hand, so anything this end recorded about it would be a claim rather than a fact.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from common import service_errors
from common.config.config_access import cfg_bool, cfg_get
from common.config.paths import get_ini_config
from common.devices import device_client, device_ops
from common.games import export_bundle, game_identity, game_repository, mobile_transfer
from common.i18n import t
from common.runtime import jobs as job_registry


def carried_by(device_id: str) -> dict[str, Any]:
    """It is a short question and the answer is only true at the moment it is given."""
    device = device_ops.mobile_or_refuse(device_id)
    found = mobile_transfer.carried(device.address, device.port)
    games = game_repository.catalog()
    named = {id(game): game_id for game_id, game in games.items()}
    ids: dict[str, str] = {}
    for game in game_identity.in_priority(games.values()):
        ids.setdefault(str(game.game_dir_name or ""), named[id(game)])
    return {"device_id": device_id, "count": len(found),
            "games": [{"folder": name, "game_id": ids.get(name)} for name in found]}


def send(device_id: str, game_ids: Iterable[str], everything: bool = False,
         settings: bool = False) -> job_registry.Job:
    """A job, not an action.

    The lifecycle vocabulary is for things that happen at once - stop the table, reboot.
    A multi-file transfer with per-file progress over a phone's wifi is slow work, and
    slow work has a shape here already.
    """
    device = device_ops.mobile_or_refuse(device_id)
    folders = [_folder_or_refuse(game_id) for game_id in game_ids]
    if not folders:
        raise service_errors.RefusedError(t("error.devices.games_needed"))
    heard = device_ops.probe_one(device)
    if heard["state"] != device_client.ANSWERING:
        raise mobile_transfer.DeviceUnreachableError(heard["reason"])
    chunk_bytes, word = _sending()

    def work(job: job_registry.Job) -> None:
        reporter = job.reporter()

        # The whole transfer's progress, not one game's: what a person watching wants is
        # how far through the twelve they are. `at` comes in by call so it is this
        # folder's position and not wherever the loop has reached.
        def reporting(at: int) -> mobile_transfer.Progress:
            def sent(done: int, total: int, said: str) -> None:
                reporter.progress(at * 100 + int(100 * done / max(total, 1)),
                                  len(folders) * 100, said)
            return sent

        for index, folder in enumerate(folders):
            reporter.progress(index, len(folders), t("said.sending_game", game=folder.name))
            mobile_transfer.send(folder, device.address, device.port,
                                 chunk_bytes=chunk_bytes, everything=everything,
                                 settings=settings, mobile_word=word,
                                 on_progress=reporting(index))
        sent = t("said.send_result", count=len(folders),
                 device=device.display_name or device_id)
        reporter.progress(len(folders), len(folders), sent)
        job.detail = sent

    return job_registry.submit(job_registry.KIND_DEVICE_SEND, work)


def plan(device_id: str, game_ids: Iterable[str], everything: bool = False) -> dict[str, Any]:
    """Which of these games would send a mobile settings file, for a confirm to say so.

    The Manager cannot read a game folder, and the folder is where the answer is.
    """
    device_ops.mobile_or_refuse(device_id)
    _chunk, word = _sending()
    ids = list(game_ids)
    mobile = [] if everything else [
        game_id for game_id in ids
        if export_bundle.mobile_settings_file(_folder_or_refuse(game_id), word)]
    return {"device_id": device_id, "mobile_settings": mobile}


def remove(device_id: str, name: str) -> None:
    device = device_ops.mobile_or_refuse(device_id)
    mobile_transfer.remove(name, device.address, device.port)


def _sending() -> tuple[int, str]:
    """The chunk size and the mobile settings word, which are one value for every device.

    The word is empty while the switch is off, which is what turns the mobile copy off.
    """
    config = get_ini_config()
    try:
        chunk = int(cfg_get(config, "vpxmobile", "chunk_size", "") or mobile_transfer.CHUNK_BYTES)
    except ValueError:
        chunk = mobile_transfer.CHUNK_BYTES
    word = ""
    if cfg_bool(config, "vpxmobile", "send_masked_config"):
        word = cfg_get(config, "vpxmobile", "config_mask", "").strip().strip(".")
        if word.lower().endswith(".ini"):
            word = word[:-4].strip().strip(".")
    return max(chunk, 1), word


def _folder_or_refuse(game_id: str) -> Path:
    folder = game_repository.game_folder(game_id)
    if folder is None:
        raise service_errors.NotFoundError(
            t("error.games.unknown_game", game_id=(game_id)))
    return folder
