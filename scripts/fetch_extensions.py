#!/usr/bin/env python3
"""Fetching the extensions a build includes. Run by CI, not by the app.

Reads the Extension List, takes each included extension at the version the list names and
puts it under `extensions/`, where `packaging/vpinfe.spec` hashes it into the build's
fingerprint list and ships it. A build fails rather than ship without one of them.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.online import extension_list  # noqa: E402
from common.online.theme_registry_client import (  # noqa: E402
    ThemeRegistryClient,
    ThemeRegistryError,
)

INCLUDED = ("library_importer", "vpinplay")


class FetchError(Exception):
    pass


def fetch_included(client: ThemeRegistryClient, list_url: str, outdir: Path,
                   included: tuple[str, ...] = INCLUDED) -> dict[str, str]:
    """Each included extension, replaced under `outdir` by what the list names. Returns name
    to version. Nothing under `outdir` changes unless every one of them was fetched."""
    listed = {one.name: one for one in extension_list.read_list(client, list_url)}
    missing = [name for name in included if name not in listed]
    if missing:
        raise FetchError(f"the Extension List does not name {', '.join(missing)}")
    too_new = [f"{name} needs VPinFE {listed[name].needs}"
               for name in included if listed[name].needs]
    if too_new:
        raise FetchError("; ".join(too_new))

    versions: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="fetch-extensions-") as work:
        fetched: dict[str, Path] = {}
        for name in included:
            root, version = extension_list.fetch(listed[name], client, Path(work) / name)
            fetched[name] = root
            versions[name] = version
        outdir.mkdir(parents=True, exist_ok=True)
        for name, root in fetched.items():
            shutil.rmtree(outdir / name, ignore_errors=True)
            shutil.copytree(root, outdir / name)
    return versions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", default=extension_list.OFFICIAL_LIST, dest="list_url",
                        help="the Extension List to read")
    parser.add_argument("--outdir", default=str(ROOT / "extensions"),
                        help="where the extensions go")
    args = parser.parse_args(argv)
    try:
        versions = fetch_included(ThemeRegistryClient(), args.list_url, Path(args.outdir))
    except (FetchError, extension_list.ExtensionListError, ThemeRegistryError) as exc:
        print(f"fetch_extensions: {exc}", file=sys.stderr)
        return 1
    for name, version in versions.items():
        print(f"fetch_extensions: {name} {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
