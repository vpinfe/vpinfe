"""What a site that hosts a file is called, for a link that names where to go."""

from __future__ import annotations

from urllib.parse import urlsplit

# Keyed by the host without `www.`. A host not in here is named by itself.
FILE_SITES = {
    "vpuniverse.com": "VPUniverse",
    "vpforums.org": "VPForums",
    "mega.nz": "Mega",
    "pinballnirvana.com": "Pinball Nirvana",
    "vpdb.io": "VPDB",
    "drive.google.com": "Google Drive",
    "dropbox.com": "Dropbox",
    "mediafire.com": "MediaFire",
    "github.com": "GitHub",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
}


def is_web(url: str) -> bool:
    """Whether `url` is an address a browser opens as written: http or https, with a host.
    An address that does not parse is not."""
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def host(url: str) -> str:
    """The address's host, lowercased and without `www.`, or `""` where it has none."""
    return urlsplit(str(url or "").strip()).netloc.lower().removeprefix("www.")


def site_name(url: str) -> str:
    """The site `url` is on, as a person would call it; `""` for an address without one."""
    named = host(url)
    return FILE_SITES.get(named, named)
