"""The scope vocabulary: `<resource>:<action>`, extensions `ext:<name>:<action>`.

Most are reserved rather than used - settling a name is cheap, renaming one after
callers depend on it is not. See docs/http-api.md.
"""

from __future__ import annotations

from common.extensions import reach

# What this instance is: discovery, health, version, what it can do. Not domain
# data - the answer to "what am I talking to", which is what discovery exists for.
INSTANCE_READ = "instance:read"

GAMES_READ = "games:read"
GAMES_WRITE = "games:write"
# Deliberately not part of games:read: reading the library and extracting
# complete game folders are different permissions.
GAMES_EXPORT_FULL = "games:export_full"

COLLECTIONS_READ = "collections:read"
COLLECTIONS_WRITE = "collections:write"

# Play host. Reading what is happening is not the same as causing it to happen.
PLAY_READ = "play:read"
LAUNCH_INVOKE = "launch:invoke"
# Ending a table someone may be mid-game on. Not launch:invoke - starting a table for
# somebody and taking one away from them are not the same permission.
PLAY_STOP = "play:stop"

UPLOADS_WRITE = "uploads:write"
CAPTURE_RUN = "capture:run"
# Separate from games:read on purpose: this one makes an outbound call to VPSdb
# on the caller's behalf, which is not the same permission as reading local games.
VPS_READ = "vps:read"

# Walking the machine's own directories to find artwork. Deliberately not games:read -
# reading the library is a different permission from reading the disk it sits on, and
# this one is bounded by an allowlist rather than by the library root.
FILESYSTEM_READ = "filesystem:read"

CONFIG_READ = "config:read"
CONFIG_WRITE = "config:write"

SYSTEM_READ = "system:read"
# Restart, reboot, shutdown. Deliberately its own scope: it is the one that can
# end someone's game.
SYSTEM_ADMIN = "system:admin"

# The devices an install knows. Reading the registry is not the same as joining it: a device
# announces itself, which is a write, and anything asking who is out there is a read.
DEVICES_READ = "devices:read"
DEVICES_WRITE = "devices:write"

# The USB, serial and display hardware attached to this device. Not devices:*, which are other
# VPinFE installs.
HARDWARE_READ = "hardware:read"

# How a user arranged a UI, kept on the install that holds the library so it follows
# them between machines. Not
# config:* - a column layout is not the same permission as reading where VPX lives.
PREFERENCES_READ = "preferences:read"
PREFERENCES_WRITE = "preferences:write"

PLAYERS_READ = "players:read"
PLAYERS_WRITE = "players:write"

EVENTS_SUBSCRIBE = "events:subscribe"

# Asking what slow work is running. Starting it carries the scope of what it does -
# a library scan writes game metadata, so it is games:write - because the right to
# watch a job is not the right to cause one.
JOBS_READ = "jobs:read"

# Acting as the player: pressing what a cabinet button presses. Its own scope because it
# is neither reading nor launching - it is the first capability that lets a caller drive
# the frontend somebody is standing in front of. Declared now and not enforced, because
# adding a scope to a live route later is churn and declaring one is free.
INPUT_ACT = "input:act"

# Declared by an extension that provides an app, whether or not it serves routes of its own.
APPS_PROVIDE = "apps:provide"

# Who is signed in, and signing them out. Not config:* - a list of browsers is not a setting.
ACCESS_READ = "access:read"
ACCESS_WRITE = "access:write"

# The third tier: what runs, who gets in, what cannot be undone, and the power. Held by this
# device, and by an Owner while a Confirm is live. A token or a Remote pass never holds it.
PROTECTED = "protected:write"
# Rating and favoriting, which a Remote pass may do and may not do more than.
PLAY_RATE = "play:rate"
# Joining as a guest, signing a guest out and saying who is up: what a phone does to the
# roster without touching a kept player.
PLAY_PLAYERS = "play:players"
# Opening the frontend's windows on this device, from an Owner's browser. Not play:*, so a
# Remote pass never holds it, and not system:admin, so it needs no Confirm: it puts the
# screen back and takes nothing away.
FRONTEND_OPEN = "frontend:open"

CORE = frozenset({
    INSTANCE_READ,
    GAMES_READ, GAMES_WRITE, GAMES_EXPORT_FULL,
    COLLECTIONS_READ, COLLECTIONS_WRITE,
    PLAY_READ, LAUNCH_INVOKE, PLAY_STOP,
    UPLOADS_WRITE, VPS_READ, FILESYSTEM_READ, CAPTURE_RUN,
    CONFIG_READ, CONFIG_WRITE,
    SYSTEM_READ, SYSTEM_ADMIN,
    EVENTS_SUBSCRIBE, JOBS_READ, INPUT_ACT,
    DEVICES_READ, DEVICES_WRITE, HARDWARE_READ,
    PREFERENCES_READ, PREFERENCES_WRITE,
    PLAYERS_READ, PLAYERS_WRITE,
    APPS_PROVIDE,
    ACCESS_READ, ACCESS_WRITE,
    PROTECTED, PLAY_RATE, PLAY_PLAYERS, FRONTEND_OPEN,
})

# Read the library, collections, players and what is playing; launch, stop, press buttons,
# capture, rate and favorite. Not settings, not the account card, not power.
PLAY = frozenset({
    INSTANCE_READ, GAMES_READ, COLLECTIONS_READ, PLAYERS_READ, PLAY_READ, LAUNCH_INVOKE,
    PLAY_STOP, JOBS_READ, EVENTS_SUBSCRIBE, INPUT_ACT, CAPTURE_RUN, PREFERENCES_READ,
    DEVICES_READ, PLAY_RATE, PLAY_PLAYERS,
})

# What a token is not given with its access: who is signed in, the Protected tier and opening
# the frontend.  The power switch adds system:admin, which opens it too.
NEVER_A_TOKEN = frozenset({ACCESS_READ, ACCESS_WRITE, PROTECTED, SYSTEM_ADMIN, FRONTEND_OPEN})
# Everything that changes what VPinFE knows or shows. A Library caller is never asked for a Confirm.
LIBRARY = CORE - NEVER_A_TOKEN
# What a Confirm adds to an Owner: the Protected scope and the power.
CONFIRMED = frozenset({PROTECTED, SYSTEM_ADMIN})

EXTENSION_PREFIX = "ext:"


def extension_scope(extension: str, action: str) -> str:
    """A scope belonging to an extension, which can never collide with a core one."""
    return f"{EXTENSION_PREFIX}{extension}:{action}"


def is_extension_scope(scope: str) -> bool:
    return scope.startswith(EXTENSION_PREFIX)


def extension_tier(scope: str) -> str:
    """The tier an `ext:<name>:<tier>` scope is, or "" for any other scope."""
    tier = scope.rsplit(":", 1)[-1]
    return tier if is_extension_scope(scope) and tier in reach.TIERS else ""


def is_confirmed(scope: str) -> bool:
    """Whether using `scope` asks an Owner for a Confirm: core's Protected scopes, and an
    extension's own Protected tier."""
    return scope in CONFIRMED or extension_tier(scope) == reach.PROTECTED


def is_known(scope: str) -> bool:
    return scope in CORE or is_extension_scope(scope)
