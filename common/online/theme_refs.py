"""A theme list entry's `ref`, spelled the way a host serves it."""

from __future__ import annotations

TAG_PREFIX = "refs/tags/"


def bare_ref(ref: str) -> str:
    """A ref spelled the one way both GitHub and Forgejo serve (PAR-43).

    A branch and a tag of the same name are ambiguous, which is a naming rule for
    authors rather than something to solve here.
    """
    reference = str(ref or "").strip() or "HEAD"
    for prefix in ("refs/heads/", TAG_PREFIX):
        if reference.startswith(prefix):
            return reference[len(prefix):].strip() or "HEAD"
    return reference


def raw_url(base_url: str, ref: str, path: str) -> str:
    """A raw file URL for a repo at a given ref, on GitHub or on Forgejo."""
    root = str(base_url or "").rstrip("/")
    return f"{root}/raw/{bare_ref(ref)}/{path}"


def is_tag(ref: str) -> bool:
    return str(ref or "").startswith(TAG_PREFIX)
