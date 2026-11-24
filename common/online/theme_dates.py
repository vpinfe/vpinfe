"""When a theme last changed, asked of the host that keeps its repository."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlparse

from common import timestamps
from common.online import theme_refs

logger = logging.getLogger("vpinfe.common.online.theme_dates")

GITHUB = "github.com"


def committed_at(base_url: str, ref: str, fetch_json: Callable[[str], Any]) -> str:
    return timestamps.epoch_to_iso(timestamps.iso_to_epoch(_asked(base_url, ref, fetch_json)))


def _asked(base_url: str, ref: str, fetch_json: Callable[[str], Any]) -> str:
    """The date of the newest commit on `ref`, as the host gives it, or "" where it
    cannot be had - a host that is neither GitHub nor Forgejo, a refusal, a rate limit."""
    parsed = urlparse(str(base_url or ""))
    parts = [part for part in parsed.path.split("/") if part]
    if not parsed.netloc or len(parts) < 2:
        return ""
    owner, repo = parts[0], parts[1]
    wanted = theme_refs.bare_ref(ref)
    try:
        if parsed.netloc == GITHUB:
            body = fetch_json(f"https://api.github.com/repos/{owner}/{repo}/commits/"
                              f"{quote(wanted, safe='')}")
            return str(((body or {}).get("commit") or {}).get("committer", {})
                       .get("date") or "")
        api = f"{parsed.scheme or 'https'}://{parsed.netloc}/api/v1/repos/{owner}/{repo}"
        if wanted == "HEAD":
            wanted = str((fetch_json(api) or {}).get("default_branch") or "")
            if not wanted:
                return ""
        found = fetch_json(f"{api}/commits?sha={quote(wanted, safe='')}&limit=1"
                           "&stat=false&verification=false&files=false")
        first = found[0] if isinstance(found, list) and found else {}
        return str(((first.get("commit") or {}).get("committer") or {}).get("date") or "")
    except Exception:
        logger.debug("No commit date for %s at %s", base_url, ref, exc_info=True)
        return ""
