"""An app an extension provides, and how one is assembled from what it hands over.

**An extension cannot import the app contract**, and should not have to: its one door is
`common.extensions.contract`, which is what makes the boundary checkable. So a provided
app is described in plain data - an id, a name, the suffixes it plays, the fields a
launcher of it holds - plus one callable that says how to run something.

That keeps the contract free to grow. A dataclass an extension constructs is a shape it
is pinned to; a dict core reads is one core can add to without breaking anybody.

What a provided app does not get is the rest of the app contract - parsing a table,
resolving a ROM, a configuration surface. Those are declared as absent rather than
half-answered, the same way `apps/generic` declares them, because an app that claims to
parse and does not is worse than one that says it cannot.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from common.apps.contract import App, Entry, Session

logger = logging.getLogger("vpinfe.common.extensions.provided_apps")

APPS_PROVIDE = "apps:provide"


class _ExtensionLaunch:
    """Turns what an extension answered into argv.

    The extension is asked for a command and may answer with a list, which is used as it
    is, or with nothing, in which case the launcher's own binary and arguments are used
    the way the generic app does it. Answering with a string is refused rather than split
    here: a path with a space in it is how that becomes a crash or an injection, and the
    extension is the only thing that knows where its own arguments end.
    """

    def __init__(self, name: str,
                 command: Callable[[dict, dict], Any] | None) -> None:
        self._name = name
        self._command = command

    def command(self, entry: Entry, settings: Mapping[str, Any]) -> list[str]:
        found = None
        if self._command is not None:
            found = self._command(_entry_as_data(entry), dict(settings))
        if isinstance(found, str):
            raise TypeError(
                f"{self._name} answered with a string; a command is a list of arguments")
        if found:
            return [str(one) for one in found]
        return _generic_command(entry, settings)

    def session(self, settings: Mapping[str, Any]) -> Session:
        from common.apps.contract import SESSION_NONE, Session

        return Session(kind=SESSION_NONE)


def _entry_as_data(entry: Entry) -> dict:
    """The entry as plain data, for the same reason the app is described as plain data."""
    return {"entry_id": entry.entry_id, "game_dir": entry.game_dir,
            "table": entry.table, "key": entry.key}


def _generic_command(entry: Entry, settings: Mapping[str, Any]) -> list[str]:
    """What a launcher runs when its extension did not say: the generic app's command."""
    from apps.generic import GenericLaunch

    return GenericLaunch().command(entry, settings)


def build(name: str, described: dict) -> App:
    """Assemble an `App` from what an extension described. Raises ValueError on nonsense.

    Validated here rather than trusted, because a bad app is not a bad request that
    fails once - it is stored in tables' records and read back long afterwards.
    """
    from common.apps.contract import App, Claim, Field, Kinds

    app_id = str(described.get("id") or "").strip()
    if not app_id:
        raise ValueError("an app needs an id")

    suffixes = tuple(_suffix(one) for one in described.get("suffixes") or ())
    # What sits beside one of its tables and belongs to it. Its own list, because the
    # next format's companions are not Visual Pinball's.
    companions = tuple(_suffix(one) for one in described.get("companions") or ())
    accepts_keys = bool(described.get("accepts_keys"))
    if not suffixes and not accepts_keys:
        raise ValueError(f"{app_id} plays nothing: give it suffixes or accepts_keys")

    given = described.get("vps_formats") or ()
    if isinstance(given, str):
        raise ValueError(f"{app_id} gives vps_formats as one string; give a list of them")
    vps_formats = tuple(_vps_format(one) for one in given)

    fields = tuple(_field(Field, one) for one in described.get("fields") or ())
    if not any(one.key == "bin_path" for one in fields):
        # Every launcher needs the program it runs, and an extension that forgot would
        # ship a launcher nobody can point at anything.
        fields = (Field("bin_path", path="exe"), *fields)

    kinds = described.get("kinds")
    return App(
        id=app_id,
        name=str(described.get("name") or "").strip(),
        claim=Claim(suffixes=suffixes, accepts_keys=accepts_keys,
                    companions=companions),
        vps_formats=vps_formats,
        fields=fields,
        kinds=Kinds(frozenset(kinds)) if kinds else Kinds(),
        launch=_ExtensionLaunch(name, described.get("command")),
    )


def _suffix(value: object) -> str:
    """Lowercase, with the dot, because that is what `Claim` compares against."""
    found = str(value or "").strip().lower()
    if not found:
        raise ValueError("a suffix cannot be empty")
    return found if found.startswith(".") else f".{found}"


def _vps_format(value: object) -> str:
    """Uppercase, because that is how VPS spells `tableFormat`."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("a VPS format is a non-empty string")
    return value.strip().upper()


def _field(field_type: Any, described: object) -> Any:
    if not isinstance(described, dict):
        raise ValueError(f"a field is described with a dict, not {type(described).__name__}")
    key = str(described.get("key") or "").strip()
    if not key:
        raise ValueError("a field needs a key")
    return field_type(
        key=key,
        label=str(described.get("label") or ""),
        type=str(described.get("type") or "string"),
        default=str(described.get("default") or ""),
        description=str(described.get("description") or ""),
        choices=tuple((str(a), str(b)) for a, b in described.get("choices") or ()),
        lines=int(described.get("lines") or 0),
        path=str(described.get("path") or ""),
    )
