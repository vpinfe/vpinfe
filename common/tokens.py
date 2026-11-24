"""What a user's command can say, and what each thing stands for.

Declared, the way settings are: each token says what it is, what it resolves to, and
where it is available. Declared rather than assembled at the call site because a surface
that offers them has to be able to list them - the difference between a feature people
use and one they guess at.

Three rules matter more than the list.

**Substituted into an argument, never into the line.** A line is split into arguments
first and each argument is resolved on its own, so a path with a space in it is one
argument. Resolving first and splitting after is how the same path becomes two arguments,
or worse.

**An unknown name refuses.** Resolving it to nothing turns `--config {settings_file}` into
a dangling flag that the program reads as whatever comes next. Not knowing a name and
having no value for one are different answers and both are said out loud.

**A name an extension brings carries its id.** `{scores_site.player}`. The registry builds
the dotted half from the manifest; core's own names stay bare.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from common import i18n

logger = logging.getLogger("vpinfe.common.tokens")

# Where a command runs, which decides what there is to talk about. VPinFE starting has no
# game and no launcher; a table starting has both.
VPINFE = "vpinfe"
TABLE = "table"
CONTEXTS = frozenset({VPINFE, TABLE})

# `{name}` or `{extension.name}`. Braces are free: media specs use `(Wheel)`.
_WORD = r"[A-Za-z_][A-Za-z0-9_]*"
TOKEN = re.compile(r"\{(" + _WORD + r"(?:\." + _WORD + r")?)\}")
BARE = re.compile(r"\A" + _WORD + r"\Z")


@dataclass(frozen=True)
class Token:
    name: str
    contexts: frozenset[str]
    # Empty is looked up: `token.<name>.says` for core's, the extension's own catalog for
    # one it brings.
    says: str = ""
    # Only means anything once something has finished.
    after_only: bool = False
    # Empty for core's own.
    extension: str = ""


TOKENS: tuple[Token, ...] = (
    Token("game_dir", frozenset({TABLE})),
    # `table`, not `game_file`: the launchable artifact is a table everywhere else here.
    Token("table", frozenset({TABLE})),
    Token("table_stem", frozenset({TABLE})),
    Token("game_name", frozenset({TABLE})),
    Token("id", frozenset({TABLE})),
    Token("key", frozenset({TABLE})),
    Token("rom", frozenset({TABLE})),
    Token("program", frozenset({TABLE})),
    Token("settings_file", frozenset({TABLE})),
    Token("location", frozenset({TABLE})),
    Token("player", frozenset({TABLE})),
    Token("exit_code", frozenset({TABLE}), after_only=True),
    Token("duration", frozenset({TABLE}), after_only=True),
)


def _says_key(name: str, extension: str) -> str:
    if not extension:
        return f"token.{name}.says"
    return f"ext.{extension}.token.{name.removeprefix(extension + '.')}.says"


def stands_for(token: Token) -> str:
    """What a token stands for, in the language now set."""
    return i18n.literal_or(token.says, _says_key(token.name, token.extension))[0]


@dataclass(frozen=True)
class Contributed:
    """One extension's token, and how to ask it what the name stands for now."""

    token: Token
    value: Callable[[dict[str, str]], str] = field(repr=False)


class UnknownTokenError(ValueError):
    """A name no token declares. Its own type because the surface says something
    different for it than for a command that simply failed."""


_lock = threading.RLock()
_contributed: dict[str, Contributed] = {}


def register(extension: str, name: str, contexts: frozenset[str],
             value: Callable[[dict[str, str]], str], *, says: str = "",
             after_only: bool = False) -> str:
    """Offer `{extension.name}` wherever a command in one of `contexts` is written.

    `value` is handed what the context has resolved so far and answers a string. It runs
    while a command is being prepared, so it must not block for long and must not raise
    for an install that simply has no answer - empty is an answer.
    """
    owner = str(extension or "").strip()
    wanted = str(name or "").strip()
    if not owner or not BARE.match(owner):
        raise ValueError(f"{extension!r} is not an extension name")
    if not BARE.match(wanted):
        raise ValueError(f"{owner} offers {name!r}, which is not a name a command can use")
    said = str(says or "").strip()
    if not said and not i18n.first_key(_says_key(wanted, owner)):
        raise ValueError(f"{owner} offers {wanted!r} without saying what it stands for")
    stray = sorted(set(contexts) - CONTEXTS)
    if stray or not contexts:
        raise ValueError(f"{owner} offers {wanted!r} where commands do not run: "
                         f"{', '.join(stray) or 'nowhere'}")
    full = f"{owner}.{wanted}"
    with _lock:
        _contributed[full] = Contributed(
            token=Token(full, frozenset(contexts), said, after_only, owner),
            value=value)
    logger.info("%s offers %r to commands", owner, "{" + full + "}")
    return full


def forget(extension: str) -> None:
    """Drop an extension's tokens. A command still naming one is then an unknown name."""
    owner = str(extension or "").strip()
    with _lock:
        gone = [name for name, one in _contributed.items()
                if one.token.extension == owner]
        for name in gone:
            _contributed.pop(name, None)
    if gone:
        logger.info("%s no longer offers %s", owner, ", ".join(sorted(gone)))


def offered(context: str, *, after: bool = False) -> tuple[Token, ...]:
    """The tokens a command in this context may use: core's in declared order, then what
    extensions bring, by name so the list does not shift with load order."""
    with _lock:
        brought = sorted((one.token for one in _contributed.values()),
                         key=lambda one: one.name)
    return tuple(one for one in (*TOKENS, *brought)
                 if context in one.contexts and (after or not one.after_only))


def contributed_values(context: str, base: dict[str, str], *,
                       after: bool = False) -> dict[str, str]:
    """What each extension token in this context stands for right now.

    One that raises resolves to empty and is logged: an extension misbehaving must not
    stop a table launching.
    """
    with _lock:
        asking = [one for one in _contributed.values()
                  if context in one.token.contexts
                  and (after or not one.token.after_only)]
    answers: dict[str, str] = {}
    for one in asking:
        try:
            answers[one.token.name] = str(one.value(base) or "")
        except Exception:  # noqa: BLE001 - see the docstring
            logger.exception("%s could not say what %r stands for",
                             one.token.extension, one.token.name)
            answers[one.token.name] = ""
    return answers


def filled(context: str, base: dict[str, str], *, after: bool = False) -> dict[str, str]:
    """`base` with every extension token in this context it does not already carry.

    What is already there wins: a value taken before a table started is the one its
    closing commands are told, not whatever the answer has become since.
    """
    return {**contributed_values(context, base, after=after), **base}


def resolve(argument: str, values: dict[str, str], *, context: str,
            after: bool = False) -> str:
    """One argument, with its names filled in.

    A name this context does not offer is refused by name, so the message says which
    word was wrong rather than that something was.
    """
    known = {one.name for one in offered(context, after=after)}

    def swap(found: re.Match[str]) -> str:
        name = found.group(1)
        if name not in known:
            raise UnknownTokenError(name)
        return str(values.get(name, ""))

    return TOKEN.sub(swap, argument)


def written(name: str) -> str:
    """A token as a person writes it."""
    return "{" + name + "}"


def fill(argument: str, values: Mapping[str, str]) -> str:
    """`argument` with each name `values` holds filled in, and any other left as written.
    For a surface that decides for itself which names are allowed."""
    return TOKEN.sub(lambda found: values.get(found.group(1), found.group(0)), argument)


def unknown_names(text: str, *, context: str, after: bool = False) -> list[str]:
    """Every name in this text that the context does not offer, for a surface that has
    to say so while somebody is still typing."""
    known = {one.name for one in offered(context, after=after)}
    return sorted({name for name in TOKEN.findall(text) if name not in known})
