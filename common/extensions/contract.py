"""What an extension declares, and what it is handed.

An extension is a directory holding `extension.json` beside a Python package whose
`register(ctx)` core calls once at startup. The context is the whole of its reach into
VPinFE - it never receives the application, and that is the guarantee the model rests on.

An implementation imports this module for the types and nothing else of ours.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from common.devices import install_identity
from common.values import parse_version

from . import dependencies
from .reach import (  # noqa: F401 - PLAY, PROTECTED are the contract's
    LIBRARY,
    PLAY,
    PROTECTED,
    TIERS,
)

if TYPE_CHECKING:
    from .context import ExtensionContext as Context
else:
    Context = Any

# The platform ABI: the register/ctx signature, the event vocabulary and the scope
# vocabulary, versioned as one thing. An extension names the version it was written
# against and core refuses one it cannot honor.
PLATFORM_ABI = 1
SUPPORTED_ABI: frozenset[int] = frozenset({PLATFORM_ABI})

MANIFEST_NAME = "extension.json"

# The name is the extension's identity in five places at once - a URL segment, a scope,
# a log namespace, a config namespace and the Python package core imports - so it is
# restricted to what all five accept. No hyphen: the package is imported by this name,
# and a name that cannot be one is a trap laid for whoever writes the second module.
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

# What an extension may ask of the machine, as opposed to of the domain. Declared here
# because the list is the consent surface: a capability nobody has heard of cannot be
# consented to.
CAPABILITIES: frozenset[str] = frozenset({
    "ui:mount", "config:own", "net:outbound", "proc:spawn",
    "hardware:usb", "fs:read", "fs:write", "os:updates",
})

# What a manifest may name in `platforms`: `linux`, `windows`, `macos`, or any Linux
# distribution's os-release `ID`. Only the shape is checked; which distributions exist is not ours.
PLATFORM_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# `home` is any web address; `source` is the repository, which is how somebody finds what was
# installed: a host and exactly owner/repo, so it can be drawn as `owner/repo`.
# The host holds no `@`: `https://github.com@elsewhere/o/r` reads as github.com and goes elsewhere.
WEB_ADDRESS = re.compile(r"^https?://[^\s/{}@?#\\]+(/[^\s{}]*)?$")
REPOSITORY = re.compile(r"^https://[^\s/{}@?#\\]+/([^\s/{}]+)/([^\s/{}]+?)(\.git)?/?$")
LONGEST_AUTHOR = 60
LONGEST_VERSION = 40
LONGEST_ADDRESS = 300
# The image files a manifest picture may be, as `pictures.py` reads them.
PICTURE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")

# How a manifest names a surface a Remote can open.
PLAY_SURFACE = re.compile(r"router|(action|list|section|group):\S(.*\S)?|verb:[^:\s]+:\S(.*\S)?")

# What an install can be meant to do, read from the one list rather than restated: a
# second copy would be right until somebody added a feature.
FEATURES: frozenset[str] = frozenset(install_identity.FEATURES)


class ManifestError(ValueError):
    """The manifest is not one. `key` is the catalog line that says why, to whoever
    installed it, and `values` fill its slots."""

    def __init__(self, key: str, **values: str) -> None:
        super().__init__(key)
        self.key, self.values = key, values


class ContractError(RuntimeError):
    """An extension asked the context for something its manifest does not declare."""


class Unavailable(RuntimeError):  # noqa: N818
    """Raise from a function core calls when the service it reaches for did not answer, or
    answered with something unreadable. Core says so as the service's failure (a 502), not
    as a mistake in the request. The message is the line a person reads."""


TIER_ATTR = "vpinfe_tier"


def tier(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark an act, a verb's `run` or a `save` as needing `name`, `PLAY`, `LIBRARY` or
    `PROTECTED`, where it differs from the list, page or group it is in:
    `@tier(PROTECTED)` over an act that restarts something. The manifest's `tiers` must
    list it, and an act marked with one it does not is refused when it is called."""
    if name not in TIERS:
        raise ValueError(f"{name!r} is not a tier; they are {', '.join(TIERS)}")

    def mark(act: Callable[..., Any]) -> Callable[..., Any]:
        setattr(act, TIER_ATTR, name)
        return act

    return mark


def tier_of(act: Any, default: str = LIBRARY) -> str:
    """The tier an act was marked with, `default` (its surface's) where it was not."""
    return str(getattr(act, TIER_ATTR, default))


def words(name: str) -> Callable[..., str]:
    """What the extension `name` says for a key in its `i18n/<language>.json`, in the
    language now set. `ctx.t`, for a module that is not handed the context."""
    from common.i18n import t

    def said(key: str, /, **params: Any) -> str:
        return t(f"ext.{name}.{key}", **params)

    return said


def why(exc: BaseException, /, at: str | os.PathLike[str] = "") -> str:
    """`ctx.why`, for a module that is not handed the context. `at` is the URL or path
    being reached, for an exception that does not carry one."""
    from common.failures import why as worded

    return worded(exc, at)


def get_json(url: str, *, timeout: int = 15) -> Any:
    """The JSON at `url`, asked as core asks: a timeout, no second ask of a host that said
    to wait, and nothing outside this machine when VPINFE_OFFLINE is set. Needs
    `net:outbound`. Raises `requests.RequestException`, or ValueError for an unreadable
    answer."""
    from common.security import http_client

    return http_client.get_json(url, timeout=timeout)


def get_response(url: str, *, timeout: int = 15) -> Any:
    """`get_json`, returning the answer whatever its status."""
    from common.security import http_client

    return http_client.get_response(url, timeout=timeout)


def post_response(url: str, payload: Any = None, *, timeout: int = 15) -> Any:
    """POST a JSON body as `get_json` asks, returning the answer whatever its status."""
    from common.security import http_client

    return http_client.post_response(url, payload, timeout=timeout)


# `Context` is what `register(ctx)` receives, for an author's annotation and nothing else:
# at run time it is `Any`, so importing this module never imports the context.


@dataclass(frozen=True)
class Manifest:
    name: str
    # Empty is looked up as `ext.<name>.name`. Set, it is a product name.
    display_name: str
    version: str
    # Empty is looked up as `ext.<name>.description`.
    description: str
    requires_platform: int
    author: str
    # The repository, as written, `https://<host>/<owner>/<repo>`.
    source: str
    # Core scopes it asks to use. A consent declaration; the vocabulary that decides
    # whether one is real lives with the API, so it is checked at the seam.
    scopes: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    # Install features it needs. An extension over a library is not offered to an
    # install that holds none.
    requires_features: tuple[str, ...] = ()
    # Empty means every one.
    platforms: tuple[str, ...] = ()
    # Event names it publishes, without its namespace.
    events: tuple[str, ...] = ()
    # Protected, when it has something that asks for the Confirm. Library is every
    # extension's, and Play is never listed here: see `play`.
    tiers: tuple[str, ...] = ()
    # The surfaces a Remote can open, by name: "router", "action:<key>", "list:<key>",
    # "section:<key>", "group:<subject>" or "verb:<subject>:<key>". The Owner is shown the list
    # before accepting, and a Play surface registered without a line here fails to load.
    play: tuple[str, ...] = ()
    # Where it is described, when that is not the repository. Empty is not shown apart.
    home: str = ""
    # A path inside its own folder to an image drawn in the header of its page.
    picture: str = ""
    # Dependencies it needs from PyPI, every one as `name==version`, the name normalized.
    dependencies: tuple[str, ...] = ()

    @property
    def repository(self) -> str:
        """`owner/repo`, as `source` names it."""
        found = REPOSITORY.match(self.source)
        return f"{found.group(1)}/{found.group(2)}" if found else ""

    @property
    def reaches(self) -> tuple[str, ...]:
        """Every tier its routes may sit at, least protected first. Library is always one."""
        return tuple(one for one in TIERS
                     if one == LIBRARY or one in self.tiers or (one == PLAY and self.play))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "version": self.version,
            "description": self.description,
            "requires_platform": self.requires_platform,
            "scopes": list(self.scopes),
            "capabilities": list(self.capabilities),
            "requires_features": list(self.requires_features),
            "platforms": list(self.platforms),
            "events": list(self.events),
            "tiers": list(self.tiers),
            "play": list(self.play),
            "author": self.author,
            "home": self.home,
            "source": self.source,
            "picture": self.picture,
            "dependencies": list(self.dependencies),
        }


def _strings(raw: Any, field: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ManifestError("extension.reason.manifest_bad_list", field=field)
    return tuple(str(item).strip() for item in raw if str(item).strip())


def _text(raw: Any) -> str:
    return raw.strip() if isinstance(raw, str) else ""


def _plain(value: str) -> bool:
    """Whether `value` is one line of visible characters: no control, bidi or line-break
    characters, which would let a name draw as something it is not."""
    return not any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") for c in value)


def _picture_path(raw: Any) -> str:
    """The picture's path as written, or a ManifestError where it cannot name a file in the
    extension's own folder. Separators are `/`; a backslash names a different file on each
    platform, so it is refused."""
    if raw is None:
        return ""
    value = _text(raw)
    parts = value.split("/")
    if (not value or "\\" in value or not _plain(value) or value.startswith("~")
            or ":" in parts[0]
            or any(part in ("", ".", "..") for part in parts)):
        raise ManifestError("extension.reason.manifest_bad_picture")
    return value


def _dependencies(raw: Any) -> tuple[str, ...]:
    """The manifest's dependencies as `name==version`, sorted, or a ManifestError naming the
    line at fault."""
    lines = _strings(raw, "dependencies")
    if not lines:
        return ()
    if len(lines) > dependencies.MOST:
        raise ManifestError("extension.reason.manifest_dependencies_many",
                            most=str(dependencies.MOST))
    found: dict[str, dependencies.Dependency] = {}
    for line in lines:
        try:
            one = dependencies.parse_line(line)
        except dependencies.BadLineError:
            raise ManifestError("extension.reason.manifest_dependencies_pin", line=line) from None
        if one.name in dependencies.SHIPPED:
            raise ManifestError("extension.reason.manifest_dependencies_shipped", name=one.name)
        if one.name in found:
            raise ManifestError("extension.reason.manifest_dependencies_twice", name=one.name)
        found[one.name] = one
    return tuple(str(found[name]) for name in sorted(found))


def parse(raw: Any) -> Manifest:
    """Read a manifest, or say what is wrong with it in a line a user can act on."""
    if not isinstance(raw, dict):
        raise ManifestError("extension.reason.manifest_unreadable")

    name = str(raw.get("name") or "").strip()
    if not NAME_PATTERN.match(name):
        raise ManifestError("extension.reason.manifest_bad_name")

    try:
        abi = int(raw.get("requires_platform", ""))
    except (TypeError, ValueError):
        raise ManifestError("extension.reason.manifest_platform_unsaid") from None
    if abi not in SUPPORTED_ABI:
        raise ManifestError("extension.reason.platform_mismatch", abi=str(abi),
                            offered=", ".join(str(v) for v in sorted(SUPPORTED_ABI)))

    capabilities = _strings(raw.get("capabilities"), "capabilities")
    unknown = sorted(set(capabilities) - CAPABILITIES)
    if unknown:
        raise ManifestError("extension.reason.manifest_unknown_capabilities",
                            capabilities=", ".join(unknown))

    platforms = _strings(raw.get("platforms"), "platforms")
    unknown = sorted(one for one in set(platforms) if not PLATFORM_NAME.fullmatch(one))
    if unknown:
        raise ManifestError("extension.reason.manifest_unknown_platforms",
                            platforms=", ".join(unknown))

    features = _strings(raw.get("requires_features"), "requires_features")
    unknown = sorted(set(features) - FEATURES)
    if unknown:
        raise ManifestError("extension.reason.manifest_unknown_features",
                            features=", ".join(unknown))

    tiers = _strings(raw.get("tiers"), "tiers")
    unknown = sorted(set(tiers) - set(TIERS))
    if unknown:
        raise ManifestError("extension.reason.manifest_unknown_tiers", tiers=", ".join(unknown))
    if PLAY in tiers:
        raise ManifestError("extension.reason.manifest_play_tier")

    play = _strings(raw.get("play"), "play")
    unknown = sorted(one for one in set(play) if not PLAY_SURFACE.fullmatch(one))
    if unknown:
        raise ManifestError("extension.reason.manifest_unknown_play", surfaces=", ".join(unknown))

    author = raw.get("author")
    if author is None or (isinstance(author, str) and not author.strip()):
        raise ManifestError("extension.reason.manifest_no_author")
    if (not isinstance(author, str) or len(author.strip()) > LONGEST_AUTHOR
            or not _plain(author)):
        raise ManifestError("extension.reason.manifest_bad_author")

    version = _text(raw.get("version"))
    if (not parse_version(version) or version != version.lstrip("vV")
            or len(version) > LONGEST_VERSION or not _plain(version)
            or any(c.isspace() for c in version)):
        raise ManifestError("extension.reason.manifest_bad_version")

    home = _text(raw.get("home"))
    if raw.get("home") is not None and not (
            WEB_ADDRESS.match(home) and len(home) <= LONGEST_ADDRESS and _plain(home)):
        raise ManifestError("extension.reason.manifest_bad_home")

    source = _text(raw.get("source"))
    if not (REPOSITORY.match(source) and len(source) <= LONGEST_ADDRESS and _plain(source)):
        raise ManifestError("extension.reason.manifest_bad_source")

    return Manifest(
        name=name,
        display_name=str(raw.get("display_name") or "").strip(),
        version=version,
        description=str(raw.get("description") or "").strip(),
        requires_platform=abi,
        author=author.strip(),
        home=home,
        source=source,
        picture=_picture_path(raw.get("picture")),
        dependencies=_dependencies(raw.get("dependencies")),
        scopes=_strings(raw.get("scopes"), "scopes"),
        capabilities=capabilities,
        requires_features=features,
        platforms=platforms,
        events=_strings(raw.get("events"), "events"),
        tiers=tuple(one for one in TIERS if one in tiers),
        play=tuple(sorted(set(play))),
    )


def check_picture(directory: Path, manifest: Manifest) -> None:
    """Refuse a manifest picture that is not an image file inside the extension's own
    folder, within the size cap. Reads no pixels: that waits for the picture to be drawn."""
    if not manifest.picture:
        return
    from .pictures import LARGEST_BYTES

    root = Path(directory).resolve()
    try:
        here = (root / manifest.picture).resolve()
        fits = (here.is_relative_to(root) and here.is_file()
                and here.suffix.lower() in PICTURE_SUFFIXES
                and here.stat().st_size <= LARGEST_BYTES)
    except (OSError, ValueError):
        fits = False
    if not fits:
        raise ManifestError("extension.reason.manifest_bad_picture")


def read_manifest(directory: Path) -> Manifest:
    """The manifest in an extension directory."""
    path = Path(directory) / MANIFEST_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError("extension.reason.manifest_missing", file=MANIFEST_NAME) from None
    except (OSError, ValueError) as exc:
        raise ManifestError("extension.reason.manifest_unreadable") from exc
    return parse(raw)
