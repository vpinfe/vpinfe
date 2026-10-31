"""Hides what is private in text before it leaves the device.

The engine is handed an `Identity` (who and what is private on this install) and replaces
those values literally, longest first, then sweeps what it was not told with patterns.
It reads nothing from the device and never logs a value: the only record of an original
is the `Ledger`, which lives in memory for as long as the person is reviewing.

A Windows log is read on a Linux runner and the reverse, so nothing here touches `os.path`
or the local filesystem. Paths are taken apart with `PureWindowsPath` and `PurePosixPath`
by name, and separators are normalized for matching only - a replacement keeps the log's
own separator.

Unsure means hidden: a string that might be a secret is replaced and listed under
`Ledger.precautions`, never kept because it might be harmless.
"""

from __future__ import annotations

import ipaddress
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import quote, quote_plus, unquote

HOME = "home"
USER = "user"
HOST = "host"
ADDRESS = "address"
SECRET = "secret"
IDENTIFIER = "identifier"
PERSON = "person"
TABLE = "table"
COMMAND = "command"
PATH = "path"
CUSTOM = "custom"
KINDS = (HOME, USER, HOST, ADDRESS, SECRET, IDENTIFIER, PERSON, TABLE, COMMAND, PATH, CUSTOM)

_SLOT_KIND = {
    "user": USER, "host": HOST, "device": HOST,
    "ip": ADDRESS, "ipv6": ADDRESS, "mac": ADDRESS, "domain": ADDRESS,
    "id": IDENTIFIER, "player": PERSON,
    "volume": PATH, "share": PATH, "library": PATH,
    "table": TABLE, "hidden": CUSTOM,
}
_PLAIN_KIND = {
    "~": HOME, "[[secret]]": SECRET, "[[code]]": SECRET, "[[fingerprint]]": SECRET,
    "[[email]]": PERSON, "[[value]]": COMMAND,
}
# Every shape the engine writes in place of a value: a numbered slot, or one of the plain
# replacements. A `~` only counts at the start of a path.
PLACEHOLDER = re.compile(
    r"\[\[(?:secret|code|fingerprint|email|value)\]\]|\[\[(?:" + "|".join(_SLOT_KIND)
    + r")-\d+\]\]|(?<![\w~])~(?=[/\\]|$|\s)")


def kind_of(placeholder: str) -> str | None:
    """What kind of value a placeholder stands for, or None when it is not one."""
    if placeholder in _PLAIN_KIND:
        return _PLAIN_KIND[placeholder]
    if not (placeholder.startswith("[[") and placeholder.endswith("]]")):
        return None
    slot, _, number = placeholder[2:-2].rpartition("-")
    return _SLOT_KIND.get(slot) if number.isdigit() else None

# Logins a person did not choose and that are ordinary words on their own, so replacing them
# everywhere would turn "Steam Deck" into "Steam [[user-1]]". They are still hidden inside a
# path, where they are a folder name.
GENERIC_LOGINS = frozenset({
    "deck", "pi", "admin", "administrator", "user", "root", "steam", "guest", "default",
    "vpinfe", "pinball", "vpin", "owner", "home",
})

# Words that are the product's own. A device left at its default name, or a cabinet called
# Pinball, would otherwise turn every "VPinFE" and "Visual Pinball" in the package into a
# placeholder.
PRODUCT_WORDS = frozenset({
    "vpinfe", "pinball", "visual pinball", "vpinball", "vpx", "frontend", "this device",
    "device", "localhost",
})

# Folders under the users folder that name nobody.
_SHARED_ACCOUNTS = frozenset({"public", "default", "default user", "all users", "shared",
                              "guest", ".localized"})

# Hosts VPinFE itself contacts, so a failing download still reads. A subdomain of one is one.
KEPT_HOSTS = frozenset({
    "github.com", "githubusercontent.com", "pinballprimer.github.io",
    "virtualpinballspreadsheet.github.io", "ffmpeg.martin-riedl.de",
    "martin-riedl.de", "gyan.dev", "ipdb.org", "vpdb.io", "vpforums.org", "vpuniverse.com",
    "vpinplay.com", "vpinball.com", "pinballnirvana.com", "pinballfx.com", "zenstudios.com",
    "youtube.com", "google.com", "dropbox.com", "mediafire.com", "patreon.com",
    "facebook.com", "discord.com", "python.org", "pypi.org", "npmjs.org",
})

_TLDS = (
    "com", "net", "org", "io", "gg", "tv", "uk", "de", "fr", "us", "ca", "au", "nl", "se",
    "no", "fi", "dk", "es", "jp", "ru", "br", "eu", "ch", "be", "nz", "info", "xyz", "lan",
    "local", "home", "internal", "localdomain", "intranet", "corp", "cloud", "online",
    "site", "tech", "link", "live", "arpa",
)

# The first label of a dotted name that is one of ours, so a logger or a module is not a host.
_OWN_ROOTS = frozenset({
    "vpinfe", "common", "httpapi", "manager", "frontend", "apps", "nicegui", "uvicorn",
    "asyncio", "urllib3", "pil", "tests", "importer", "theming",
})


def _key(text: str) -> str:
    return unicodedata.normalize("NFC", unquote(text)).casefold()


def _spellings(text: str) -> set[str]:
    forms = set()
    for form in {text, unicodedata.normalize("NFC", text), unicodedata.normalize("NFD", text)}:
        forms.add(form)
        forms.add(form.replace("'", "\u2019"))
        forms.add(form.replace("\u2019", "'"))
    return forms


def _name_forms(name: str) -> set[str]:
    forms = set()
    for form in _spellings(name):
        forms.add(form)
        forms.add(quote(form))
        forms.add(quote_plus(form))
    return forms


def _is_windows(path: str) -> bool:
    if re.match(r"^(?:\\\\\?\\)?[A-Za-z]:", path) or path.startswith("\\\\"):
        return True
    return "\\" in path and "/" not in path


def _path_forms(path: str) -> set[str]:
    """Every way a log spells this folder: both separators, JSON-escaped, the extended
    `\\\\?\\` prefix, a `file://` URL, and each of those percent-encoded."""
    path = path.strip()
    if not path:
        return set()
    forms: set[str] = set()
    if _is_windows(path):
        bare = path[4:] if path.startswith("\\\\?\\") else path
        pure = PureWindowsPath(bare)
        backslash, slash = str(pure), pure.as_posix()
        forms |= {backslash, slash, backslash.replace("\\", "\\\\")}
        if pure.drive[1:2] == ":":
            forms |= {"\\\\?\\" + backslash, "file:///" + slash}
    else:
        pure_posix = PurePosixPath(path)
        forms |= {str(pure_posix), "file://" + str(pure_posix)}
    spelled = set()
    for form in forms:
        spelled |= _spellings(form)
        spelled.add(quote(form, safe="/:\\?"))
        spelled.add(quote(form, safe=""))
    return spelled


@dataclass(frozen=True)
class Identity:
    """Who and what is private on this install. Plain data, built in one place
    (`common.support.identity`) from the install's own stores."""

    homes: tuple[str, ...] = ()
    logins: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ()
    devices: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    players: tuple[str, ...] = ()
    identifiers: tuple[str, ...] = ()
    libraries: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()


@dataclass(frozen=True)
class Precaution:
    """A replacement made because the engine was unsure. `line` is already cleaned."""

    why: str
    line: str


class Ledger:
    """What was replaced, by kind, with stable numbering.

    `originals` maps a placeholder back to what it hid and exists for the person's own
    screen. It is never written into the package.
    """

    def __init__(self) -> None:
        self._numbers: dict[tuple[str, str], int] = {}
        self._issued: Counter[str] = Counter()
        self.originals: dict[str, str] = {}
        self.counts: Counter[str] = Counter()
        self.precautions: list[Precaution] = []

    def numbered(self, slot: str, original: str) -> str:
        key = (slot, _key(original))
        number = self._numbers.get(key)
        if number is None:
            self._issued[slot] += 1
            number = self._numbers[key] = self._issued[slot]
        placeholder = f"[[{slot}-{number}]]"
        self.originals.setdefault(placeholder, original)
        self.counts[_SLOT_KIND[slot]] += 1
        return placeholder

    def plain(self, replacement: str, kind: str) -> str:
        self.counts[kind] += 1
        return replacement


_SEP = r"(?:\\{1,4}|/)"
_NAME_END = r"""["'<>|,;)\]]"""

_AUTH = re.compile(
    r"(?i)\b((?:proxy-)?authorization\s*[:=][ \t]*+)(?!\[\[)"
    r"(?:(?:bearer|basic|token|digest)\s+)?[^\s,;\"']+")
_COOKIE = re.compile(r"(?i)\b((?:set-)?cookie\s*[:=][ \t]*+)(?!\[\[)[^\r\n]*")
_QUERY = re.compile(
    r"(?i)([?&;](?:access_token|api_key|apikey|token|key|code|secret|password|passwd|auth|sig|"
    r"signature)=)(?!\[\[)[^&\s\"'#]+")
_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)(?!\[\[)[^\s/@\"']+@")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")
_VPINFE_TOKEN = re.compile(r"\bvpf_[A-Za-z0-9_-]{16,}")
_WEBHOOK = re.compile(r"(?i)https?://(?:[\w-]+\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+")
_OTPAUTH = re.compile(r"(?i)otpauth://\S+")
_NAMED_SECRET = re.compile(
    r"(?i)(?<![A-Za-z0-9])(password|passwd|secret|token|api[_-]?key|apikey|totp)"
    r"([\"']?[ \t]*+[=:][ \t]*+)(?!\[\[)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s\"',;]+)")
_CODE_LINE = re.compile(r"(?im)(\b(?:sign-in|confirm|pairing) code\b)(.*)$")
_DIGITS = re.compile(r"\b\d{4,10}\b")
_FINGERPRINT = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){15,}[0-9a-f]{2}\b")
_FINGERPRINT_WORD = re.compile(r"(?i)(fingerprint\W{0,4})[0-9a-f]{32,}")

_UUID = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_HEX32 = re.compile(r"(?i)(?<![\w-])[0-9a-f]{32}(?![\w-])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_MAC = re.compile(r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b")
_IPV6 = re.compile(
    r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}(?:(?:\d{1,3}\.){3}\d{1,3}|[0-9A-Fa-f]{0,4})"
    r"(?:%[\w.-]+)?(?![\w:])")
_IPV4 = re.compile(
    r"(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\w|\.\d)")
_DOMAIN = re.compile(
    r"(?i)(?<![\w.@-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:"
    + "|".join(sorted(_TLDS, key=len, reverse=True)) + r")(?![\w-])(?!\.\w)")

_WIN_USERS = re.compile(
    r"(?i)(?<!\w)((?:[a-z]:|\\\\\?\\[a-z]:)" + _SEP + r"+users" + _SEP + r"+)(?!\[\[)"
    r"(?:([^\\/:*?\"<>|\r\n\[]{1,64})(?=" + _SEP + r")|([^\r\n\[]{1,64}?)(?=" + _NAME_END + r"|$))")
_POSIX_ACCOUNT = re.compile(
    r"(?<![\w/])(/(?:Users|home)/)(?!\[\[)([^/\s:\[\"'<>|]+)")
_LABEL = (r"(?:([^/\r\n:\"'<>|\[]{1,64})(?=/)|([^\r\n\[]{1,64}?)(?=" + _NAME_END + r"|$)"
          r"|([^\s/:\"'<>|\[]+))")
_RUN_MEDIA = re.compile(
    r"(?<![\w/])(/(?:run/)?media/)(?!\[\[)([^/\s:\[\"'<>|]+)(?:(/)" + _LABEL + r")?")
_MNT = re.compile(r"(?<![\w/])(/mnt/)(?!\[\[)([^/\s:\[\"'<>|]{2,})")
_VOLUMES = re.compile(r"(?<![\w/])(/Volumes/)(?!\[\[)" + _LABEL)
_UNC = re.compile(
    r"(?<![\w\\:])(\\{2,4})(?![?\[])([^\\/\s\"'<>|:*?\[]+)(\\{1,4})([^\\/\s\"'<>|:*?\[]+)")

_ENV_LINE = re.compile(r"(?im)^.*(?:launch| --).*$")
_ENV_ASSIGNMENT = re.compile(
    r"(?<![\w-])([A-Z_][A-Z0-9_]{2,})=(?![\"']?\[\[)(?:\"[^\"]*\"|'[^']*'|\S+)")

_CANDIDATE = re.compile(r"(?<![\w/\\.@-])[A-Za-z0-9_+-]{24,}={0,2}(?![\w/\\.@=-])")
_HASH = re.compile(r"(?i)^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_MIN_ENTROPY = 4.0


def _entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _looks_random(token: str) -> bool:
    if _HASH.match(token):
        return False
    if all(word.isalpha() for word in re.split(r"[_+-]", token)) and _entropy(token) < 4.3:
        return False
    classes = sum(bool(re.search(pattern, token)) for pattern in ("[a-z]", "[A-Z]", "[0-9]"))
    return classes >= 2 and _entropy(token) >= _MIN_ENTROPY


def _ip_kept(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return True
    return parsed.is_loopback or parsed.is_unspecified or address.startswith("255.")


def _host_kept(name: str) -> bool:
    lowered = name.lower()
    if lowered.split(".")[0] in _OWN_ROOTS:
        return True
    return any(lowered == kept or lowered.endswith("." + kept) for kept in KEPT_HOSTS)


# A name is a whole word where a letter is concerned and not where a digit, an underscore
# or an encoded separator is, so `dale_backup` and `dale1` are hidden and `dalesville` is not.
_NOT_AFTER_A_LETTER = r"(?:(?<![^\W\d_])|(?<=%[0-9A-Fa-f]{2}))"
_NOT_BEFORE_A_LETTER = r"(?![^\W\d_])"


def _alternation(forms: Iterable[str]) -> str:
    """The forms as a prefix tree, longest match first. Built on lower case so `PAT-PC` and
    `pat-pc.lan` share a branch and the longer one wins."""
    tree: dict[str, dict] = {}
    for form in forms:
        node = tree
        for char in form.lower():
            node = node.setdefault(char, {})
        node[""] = {}

    def emit(node: dict[str, dict]) -> str:
        branches = [re.escape(char) + emit(child) for char, child in sorted(node.items())
                    if char]
        if not branches:
            return ""
        body = branches[0] if len(branches) == 1 else "(?:" + "|".join(branches) + ")"
        return "(?:" + body + ")?" if "" in node else body

    return emit(tree)


class Redactor:
    """Cleans text against one identity. One instance per package, so a value gets the
    same placeholder in every file."""

    def __init__(self, identity: Identity, *, hide_tables: bool = False,
                 hide: Iterable[str] = ()) -> None:
        self.ledger = Ledger()
        self._lookup: dict[str, tuple[str, str]] = {}
        self._forms: list[str] = []
        self._literals = self._compile(identity, hide_tables, tuple(hide))

    def clean(self, text: str) -> str:
        if self._literals is not None:
            text = self._literals.sub(self._literal, text)
        for step in (self._secrets, self._codes, self._identifiers, self._addresses,
                     self._paths, self._domains, self._commands, self._precautions):
            text = step(text)
        return text

    def _add(self, forms: Iterable[str], slot: str, original: str) -> None:
        for form in forms:
            if form:
                self._lookup.setdefault(_key(form), (slot, original))
                self._forms.append(form)

    def _compile(self, identity: Identity, hide_tables: bool,
                 hide: tuple[str, ...]) -> re.Pattern[str] | None:
        for home in identity.homes:
            self._add(_path_forms(home), "~", home)
        for library in identity.libraries:
            self._add(_path_forms(library), "library", library)
        for login in identity.logins:
            if len(login) >= 3 and login.casefold() not in GENERIC_LOGINS:
                self._add(_name_forms(login), "user", login)
        named = [("host", identity.hosts), ("device", identity.devices),
                 ("domain", identity.domains), ("player", identity.players),
                 ("id", identity.identifiers),
                 ("table", identity.tables if hide_tables else ()),
                 ("hidden", tuple(word.strip() for word in hide))]
        for slot, names in named:
            for name in names:
                wanted = slot == "hidden" and name
                if wanted or (len(name) >= 3 and name.casefold() not in PRODUCT_WORDS):
                    self._add(_name_forms(name), slot, name)
        if not self._forms:
            return None
        return re.compile(_NOT_AFTER_A_LETTER + "(?:" + _alternation(self._forms) + ")"
                          + _NOT_BEFORE_A_LETTER, re.IGNORECASE)

    def _literal(self, found: re.Match[str]) -> str:
        # IGNORECASE and casefold disagree on a few letters (a dotless i), so a match can
        # have no entry; it is hidden as a word the person asked for rather than failing.
        slot, original = self._lookup.get(_key(found.group(0)), ("hidden", found.group(0)))
        if slot == "~":
            return self.ledger.plain("~", HOME)
        return self.ledger.numbered(slot, original)

    def _secrets(self, text: str) -> str:
        plain = self.ledger.plain

        def whole(label: str) -> Callable[[re.Match[str]], str]:
            return lambda found: plain(label, SECRET)

        def after_prefix(found: re.Match[str]) -> str:
            return found.group(1) + plain("[[secret]]", SECRET)

        text = _OTPAUTH.sub(whole("[[secret]]"), text)
        text = _WEBHOOK.sub(whole("[[secret]]"), text)
        for pattern in (_AUTH, _COOKIE, _QUERY, _FINGERPRINT_WORD):
            text = pattern.sub(after_prefix, text)
        text = _USERINFO.sub(lambda found: found.group(1) + plain("[[secret]]", SECRET) + "@",
                             text)
        text = _NAMED_SECRET.sub(
            lambda found: found.group(1) + found.group(2) + plain("[[secret]]", SECRET), text)
        for pattern in (_JWT, _GITHUB_TOKEN, _VPINFE_TOKEN):
            text = pattern.sub(whole("[[secret]]"), text)
        return _FINGERPRINT.sub(whole("[[fingerprint]]"), text)

    def _codes(self, text: str) -> str:
        def digits(found: re.Match[str]) -> str:
            return found.group(1) + _DIGITS.sub(
                lambda _: self.ledger.plain("[[code]]", SECRET), found.group(2))

        return _CODE_LINE.sub(digits, text)

    def _identifiers(self, text: str) -> str:
        def number(found: re.Match[str]) -> str:
            return self.ledger.numbered("id", found.group(0))

        text = _UUID.sub(number, text)
        text = _HEX32.sub(number, text)
        return _EMAIL.sub(lambda _: self.ledger.plain("[[email]]", PERSON), text)

    def _addresses(self, text: str) -> str:
        def mac(found: re.Match[str]) -> str:
            return self.ledger.numbered("mac", found.group(0))

        def ipv6(found: re.Match[str]) -> str:
            address = found.group(0)
            try:
                ipaddress.ip_address(address.split("%")[0])
            except ValueError:
                return address
            return address if _ip_kept(address) else self.ledger.numbered("ipv6", address)

        def ipv4(found: re.Match[str]) -> str:
            address = found.group(0)
            return address if _ip_kept(address) else self.ledger.numbered("ip", address)

        return _IPV4.sub(ipv4, _IPV6.sub(ipv6, _MAC.sub(mac, text)))

    def _paths(self, text: str) -> str:
        user, volume = self._person_in_path, self._volume_in_path

        def volumes(found: re.Match[str]) -> str:
            return found.group(1) + volume(found.group(2) or found.group(3) or found.group(4))

        def unc(found: re.Match[str]) -> str:
            return (found.group(1) + self.ledger.numbered("host", found.group(2))
                    + found.group(3) + self.ledger.numbered("share", found.group(4)))

        def windows(found: re.Match[str]) -> str:
            return found.group(1) + user(found.group(2) or found.group(3))

        def account(found: re.Match[str]) -> str:
            return found.group(1) + user(found.group(2))

        def run_media(found: re.Match[str]) -> str:
            hidden = found.group(1) + user(found.group(2))
            label = found.group(4) or found.group(5) or found.group(6)
            if label:
                hidden += found.group(3) + volume(label)
            return hidden

        def mounted(found: re.Match[str]) -> str:
            return found.group(1) + volume(found.group(2))

        text = _VOLUMES.sub(volumes, text)
        text = _UNC.sub(unc, text)
        text = _WIN_USERS.sub(windows, text)
        text = _POSIX_ACCOUNT.sub(account, text)
        text = _RUN_MEDIA.sub(run_media, text)
        return _MNT.sub(mounted, text)

    def _person_in_path(self, name: str) -> str:
        if _key(name) in _SHARED_ACCOUNTS:
            return name
        return self.ledger.numbered("user", name)

    def _volume_in_path(self, name: str) -> str:
        return self.ledger.numbered("volume", name)

    def _domains(self, text: str) -> str:
        def domain(found: re.Match[str]) -> str:
            name = found.group(0)
            return name if _host_kept(name) else self.ledger.numbered("domain", name)

        return _DOMAIN.sub(domain, text)

    def _commands(self, text: str) -> str:
        def value(found: re.Match[str]) -> str:
            return found.group(1) + "=" + self.ledger.plain("[[value]]", COMMAND)

        return _ENV_LINE.sub(lambda found: _ENV_ASSIGNMENT.sub(value, found.group(0)), text)

    def _precautions(self, text: str) -> str:
        def hide(found: re.Match[str]) -> str:
            if not _looks_random(found.group(0)):
                return found.group(0)
            return _MARK

        marked = _CANDIDATE.sub(hide, text)
        if _MARK not in marked:
            return text
        lines = []
        for line in marked.split("\n"):
            if _MARK in line:
                cleaned = line.replace(_MARK, "[[secret]]")
                self.ledger.counts[SECRET] += line.count(_MARK)
                if len(self.ledger.precautions) < MAX_PRECAUTIONS:
                    self.ledger.precautions.append(
                        Precaution(RANDOM_STRING, cleaned))
                line = cleaned
            lines.append(line)
        return "\n".join(lines)


_MARK = ""
MAX_PRECAUTIONS = 200
# What the page words through `t()`: the engine names the reason, it does not word it.
RANDOM_STRING = "random-string"
