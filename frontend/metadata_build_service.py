"""Kicking off a metadata build from the frontend without blocking the wheel."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from queue import Queue
from typing import TYPE_CHECKING, Any

from common.i18n import t
from common.runtime import jobs
from frontend import game_state

if TYPE_CHECKING:
    from frontend.api import API

logger = logging.getLogger("vpinfe.frontend.metadata_build_service")


def start_build(api: API, *, build_metadata_func: Callable[..., Any],
                all_games_func: Callable[..., list[Any]],
                download_media: bool = True,
                update_all: bool = False) -> dict[str, Any]:
    event_queue: Queue[dict[str, Any]] = Queue()

    def progress_callback(current: int, total: int, message: str) -> None:
        logger.debug("[buildmeta] Progress: %s/%s - %s", current, total, message)
        event_queue.put({
            "type": "buildmeta_progress",
            "current": current,
            "total": total,
            "message": message,
        })

    def log_callback(message: str) -> None:
        logger.info("[buildmeta] %s", message)
        event_queue.put({
            "type": "buildmeta_log",
            "message": message,
        })

    def run_build() -> None:
        try:
            result = build_metadata_func(
                download_media=download_media,
                update_all=update_all,
                progress_cb=progress_callback,
                log_cb=log_callback,
            )
            event_queue.put({"type": "buildmeta_complete", "result": result})
            api.all_games = all_games_func(reload=True)
            # Re-derived rather than assigned: the view is a collection resolved to
            # entries, and the build has just replaced every game object behind it.
            game_state.rebuild_view(api)
        except jobs.JobBusyError:
            event_queue.put({"type": "buildmeta_error",
                             "error": t("frontend.buildmeta.library_busy")})
        except Exception:
            logger.exception("build_metadata failed")
            event_queue.put({"type": "buildmeta_error",
                             "error": t("frontend.buildmeta.stopped")})
        finally:
            event_queue.put({"type": "buildmeta_done"})

    def process_events() -> None:
        try:
            logger.debug("[buildmeta] Event processor started")
            while True:
                event = event_queue.get(timeout=30)
                logger.debug("[buildmeta] Processing event: %s", event["type"])
                if event["type"] == "buildmeta_done":
                    break
                try:
                    api.send_event_all_windows_incself(event)
                except Exception:
                    logger.exception("Error sending event to windows")
        except Exception:
            logger.exception("Error processing buildmeta events")

    threading.Thread(target=run_build, daemon=True).start()
    threading.Thread(target=process_events, daemon=True).start()
    return {"success": True, "message": t("frontend.buildmeta.started")}
