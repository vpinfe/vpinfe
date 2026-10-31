"""The wire contract for /api/v1.

Every response body and every JSON request body is declared here, in one place, so
the contract is a thing you can read and import rather than something you infer by
reading handlers. FastAPI publishes these in the OpenAPI document, which is what
makes `GET /api/v1/openapi.json` worth generating a client from.

These describe the wire, not the domain. The domain shapes live under common/; a
model here is the projection of one onto the other, and the two are allowed to
differ - that is the point of having a boundary.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from common.games.collection_store import DEFAULT_DIRECTION, DEFAULT_ORDER_BY


class ApiModel(BaseModel):
    """Base for every wire model.

    populate_by_name lets a field carry an alias for names Python will not take -
    `self` in a links object being the one that forces it. FastAPI serializes by
    alias, so the alias is what a client sees.
    """

    model_config = ConfigDict(populate_by_name=True)


# --- Instance --------------------------------------------------------------

class CapabilityInfo(ApiModel):
    """One capability as discovery reports it. `reason` is set only when unavailable.

    `feature` is the switch that turns it on, or null for infrastructure that is present
    wherever the API is.
    """

    name: str
    feature: str | None = None
    description: str
    available: bool
    reason: str | None


class DiscoveryLinks(ApiModel):
    """Relative so they survive a reverse proxy. A known link this instance does not
    offer is present and null, rather than absent."""

    self_: str = Field(alias="self")
    health: str
    openapi: str
    docs: str
    collections: str | None
    events: str | None
    jobs: str | None
    manufacturers: str | None
    devices: str | None = None


class ServiceEndpoint(ApiModel):
    """One of this install's servers. The port only - the host is wherever the caller
    reached this document, which is the address already known to route here."""

    port: int


class Discovery(ApiModel):
    """`name` is the product and is the same on every install; `install_id` is which
    install this is, which is what a registry holding several of them addresses. It files
    that same value under `device_id` in its registry."""

    name: str
    install_id: str
    display_name: str
    features: list[str]
    api_version: str
    vpinfe_version: str
    capabilities: list[CapabilityInfo]
    # Servers that are not the API. `files` is where artwork is fetched from, which a
    # device cannot guess: it is a different port from the one it asked this on.
    services: dict[str, ServiceEndpoint] = Field(default_factory=dict)
    extensions: list[dict]
    links: DiscoveryLinks
    # True until the install has finished Setup, and left out of the document after.
    setup_pending: bool = False


class Health(ApiModel):
    status: str


# --- Devices ---------------------------------------------------------------

class DeviceLinks(ApiModel):
    self_: str = Field(alias="self")


class DeviceBrowser(ApiModel):
    """What a device's frontend browser plays, as it said when last asked. `state` and
    `formats` are `GET /frontend/browser`'s."""

    state: str
    name: str = ""
    formats: dict[str, bool | None] = Field(default_factory=dict)
    checked_at: str = ""
    # The install id of the library that frontend plays. Empty from a device whose version
    # does not say.
    library: str = ""


class DeviceResource(ApiModel):
    """One device this install has seen. `display_name` and `features` are what that
    install last
    reported about itself, cached so the registry reads without asking every device - they
    go stale by design, because the install owns them."""

    device_id: str
    kind: str
    display_name: str = ""
    features: list[str] = Field(default_factory=list)
    address: str = ""
    # With the address, what it takes to reach this device. 0 means it never said, which
    # is every entry written before an install sent one.
    port: int = 0
    # Whether it announced that it answers HTTPS at that address. The dialing side reads
    # the scheme from here, so a listing that drops it is dialed over plain HTTP.
    https: bool = False
    first_seen: str = ""
    # When it last announced itself, which is once per startup.
    last_seen: str = ""
    # When it was last known to be there, by either route - it announced, or this install
    # asked and got an answer. This is the one that means "available".
    last_reachable: str = ""
    browser: DeviceBrowser | None = None
    links: DeviceLinks


class DeviceList(ApiModel):
    count: int
    devices: list[DeviceResource]


class Action(ApiModel):
    """One thing an install can be told to do to itself.

    Listed only where this install performs it, which is not the same as whether this
    build's vocabulary has it: an install without a window owner performs no frontend
    action. `available` false, with `reason`, is for one that can run but not now, which
    includes a frontend action on an install that is not set up as a frontend.
    """

    scope: str
    action: str
    label: str
    # The catalog key behind `label`, for a client that resolves its own words.
    # `label` is this install's language; the key is what never moves. Additive, which
    # is why it does not move the theme contract.
    label_key: str = ""
    available: bool = True
    reason: str = ""
    reason_key: str = ""


class ActionList(ApiModel):
    count: int
    actions: list[Action]


class ActionRequest(ApiModel):
    """Which one, and why - the reason travels into the log and to every other surface,
    so "restarted from the Manager" reads better afterwards than "restarted"."""

    scope: str
    action: str
    reason: str = ""


class ActionResult(ApiModel):
    """`performed` is false where the request was declined or nothing did it. For an
    action that takes this process or this device down it means the work was handed
    over, because a process that is stopping cannot report that it stopped."""

    scope: str
    action: str
    what: str
    performed: bool


class InfoMaintenance(ApiModel):
    """What the library's own metadata files need doing to them.

    `newer_than_us` is the state that has to be told apart from the other two: a file
    written by a newer build is not one this build can upgrade, and counting it with the
    pending ones would report "I upgraded these" about files it cannot fully read.

    `unreadable` names folders whose `.info` could not be parsed at all. Those games are
    absent from the library entirely, which is the failure most worth saying out loud -
    nothing else on any screen would show them.

    `unwritten` names games whose `.info` could not take what VPinFE wrote to it, each
    with why. They are not counted in `pending_upgrade`: Upgrade cannot write them either.
    """

    pending_upgrade: int
    restorable: int
    newer_than_us: int
    newest_backup: str
    pending_games: list[str]
    restorable_games: list[str]
    unreadable: list[dict]
    unwritten: list[dict]


class ScriptPatches(ApiModel):
    """What the community index had for this library when it was last read.

    `checked_at` is when that was, and empty until the daily check has read the index once.
    `reachable` is false until then, and while the source is switched off. An index that
    cannot be reached is not an empty one, and reporting it as "nothing to do" would say the
    library is fine on the strength of a failed request.

    `checked` counts only tables whose script has actually been read. A table nothing has
    opened has no hash to match on, and calling it "nothing published" would be a
    statement about a file nobody has looked at.
    """

    reachable: bool
    offered: list[str]
    already: int
    checked: int
    checked_at: str = ""


class PatchRequest(ApiModel):
    """Which tables to fetch script fixes for: the `offered` names a check reported.
    Left out, every table the index has a fix for gets one, which is what the command
    line does."""

    model_config = ConfigDict(extra="forbid")

    games: list[str] | None = None


class DeviceGame(ApiModel):
    """One folder a device holds, and the game of this library it is, if any."""

    folder: str
    game_id: str | None = None


class DeviceGameList(ApiModel):
    """What a device that is not an install is carrying.

    Folder names, because that is all such a device knows: it has no library and no
    ids, so what it can be asked is which folders it holds. Matching them back to games
    is this end's job, and `game_id` is null for a folder that matches none.
    """

    device_id: str
    count: int
    games: list[DeviceGame]


class DeviceSendRequest(ApiModel):
    """Which games to put on it. Ids, because this end has a library and the device
    does not - the folder names are derived here rather than trusted from a caller."""

    games: list[str]
    everything: bool = False
    settings: bool = False


class DeviceSendPlan(ApiModel):
    """What a send of these games would do that a person would want to know first."""

    device_id: str
    mobile_settings: list[str]


class InputActionRequest(ApiModel):
    """One press, one release, or a tap that is both.

    `ttl_ms` is how long a press stands without a renewal. It is the caller saying how
    often it intends to say "still held", so it belongs on the press rather than in the
    install's configuration - a phone on a flaky link and a wired button board want
    different answers.

    `source` is who is pressing, and it travels into the log because "where did this
    press come from" has already been a defect once.
    """

    action: str
    phase: str = "tap"
    ttl_ms: int | None = None
    source: str = ""


class InputActionResult(ApiModel):
    """`holding` is every action the install currently believes is held down, so a
    client that reconnected can see what it is still on the hook for renewing."""

    action: str
    phase: str
    ttl_ms: int
    holding: list[str]


class DofEventRequest(ApiModel):
    """The DOF event to turn on: a letter and a number, `E900` or `S27`."""

    event: str


class DofConfigState(ApiModel):
    """When the DOF Config Tool was last asked and whether it is due again. `has_key` says
    whether a key is stored, never the key. `checked` is empty before the first ask."""

    schedule: str = "weekly"
    checked: str = ""
    due: bool = False
    has_key: bool = False


class DofEventState(ApiModel):
    """`event` is the one now on this device, or None. `blocked` says in words why one
    could not be turned on now, and is empty when one could. `no_boards` is true while an
    event is on and no board has reported in."""

    event: str | None = None
    blocked: str = ""
    no_boards: bool = False


class DofEventResult(ApiModel):
    """`event` is the one now on this device, or None when none is. `no_boards` is true
    when DOF is running and no board has reported in, so the event reached no hardware."""

    event: str | None
    no_boards: bool = False


class LogRecord(ApiModel):
    """One thing that was logged. `message` carries its continuation lines, so a
    traceback arrives whole rather than as a dozen rows with no level."""

    when: str
    level: str
    logger_name: str = Field(alias="logger")
    message: str


class LogRecords(ApiModel):
    count: int
    records: list[LogRecord]
    # Where the file is, so somebody can go and read the rest of it. Empty on an install
    # started with file logging off.
    path: str = ""
    # What else could be shown: the current log and the rotations kept beside it. Named
    # rather than spanned, because a rotation boundary is where an install restarted.
    sources: list[str] = []
    # Visual Pinball's log, by the name it is asked for with, when a launcher names where
    # it writes. Empty when none does.
    visual_pinball: str = ""


class SupportOptions(ApiModel):
    """What goes into a support package. Every part is in unless it is switched off."""

    model_config = ConfigDict(extra="forbid")

    logs: bool = True
    previous_log: bool = True
    settings: bool = True
    hide_tables: bool = False
    # Words to hide as well, for somebody who sees one the cleanup did not know.
    hide: list[str] = Field(default_factory=list, max_length=50)
    # Log files to leave out, by name. The README and the summary always stay.
    left_out: list[str] = Field(default_factory=list, max_length=10)
    # Lines to blank in a file, as (file name, 0-based line number).
    removed: list[tuple[str, int]] = Field(default_factory=list, max_length=500)


class SupportFile(ApiModel):
    name: str
    bytes: int
    lines: int
    # Log lines the size cap left out of this file; 0 when it is whole.
    omitted: int = 0


class SupportPrecaution(ApiModel):
    """Text hidden because cleanup was unsure it was private. `line` is already cleaned."""

    why: str
    line: str


class SupportPackage(ApiModel):
    """A built package as the person reviews it. What was replaced is named by kind and
    count, never by value."""

    id: str
    filename: str
    checksum: str
    bytes: int
    expires_in: int
    files: list[SupportFile]
    counts: dict[str, int]
    precautions: list[SupportPrecaution]
    summary: str

class DiscoveredInstall(ApiModel):
    """One install heard announcing itself on this network.

    Not a device: nothing here has been recorded or decided about. Anything on the LAN
    can claim to be a VPinFE install, so this is a list of what said it was there, which
    is what a picker offers and a person confirms.
    """

    install_id: str
    display_name: str = ""
    features: list[str] = Field(default_factory=list)
    address: str = ""
    port: int = 0
    url: str = ""


class DiscoveredList(ApiModel):
    count: int
    installs: list[DiscoveredInstall]


class DeviceProbe(ApiModel):
    """What one device said when asked.

    `state` is `answering`, `unreachable`, or `unaskable` - the last meaning there was
    nothing to dial, which a device being switched off never causes and switching it on
    never fixes. `version` is the version that answered, empty where it did not say.
    """

    device_id: str
    state: str
    version: str = ""
    reason: str = ""
    reason_key: str = ""
    features: list[str] = Field(default_factory=list)
    install_id: str = ""
    display_name: str = ""
    # What it said it can do, from the same response its name came in. Absent - rather
    # than empty - from anything that did not answer, and from a build too old to say.
    capabilities: list[str] | None = None
    browser: DeviceBrowser | None = None
    # Whether the token this install holds for it is one it still accepts. Absent where it
    # did not answer, which is not the same as unpaired.
    paired: bool | None = None
    # It answers HTTPS but shows a different certificate than when it was paired.
    certificate_changed: bool = False


class DeviceProbeList(ApiModel):
    probes: list[DeviceProbe]


class DeviceAnnouncement(ApiModel):
    """What a device says about itself. The address is not here: it is read off
    the socket, because a device behind a router does not know how it is reached.

    `kind` is a closed set, so an unrecognized one is a 422 rather than a string stored
    and handed to a consumer that switches on it.

    `device_id` is required: an install knows what it is called. `vpx_mobile` is refused
    here - a person lists it in `mobile_devices`, where its address is.

    `address` is ignored. See the handler.

    `port` is the other half of being reachable, and unlike the address the device is the
    only party that knows it - the socket says where a request came from, never what that
    device listens on. Without it the registry has half an address and can only ever read what
    a device chose to tell it.
    """

    device_id: str = ""
    kind: Literal["vpinfe", "vpx_mobile"] = "vpinfe"
    display_name: str = ""
    features: list[str] = Field(default_factory=list)
    address: str = ""
    port: int = 0


# --- Play ------------------------------------------------------------------

class PlayState(ApiModel):
    """Also the payload of the `play.state_changed` event, so a subscriber and a
    poller see the same shape. `source` is who asked - the frontend filters its
    own launches on it, so dropping the field breaks the remote overlay. `paused` is the
    running table saying it has paused itself and not yet that it has resumed."""

    launching: bool
    game_name: str | None
    source: str | None
    paused: bool = False
    game_id: str | None = None
    started_at: str | None = None


class PlayStopped(ApiModel):
    """`stopped` is false when there was nothing to stop, which is an answer rather
    than a failure - a caller racing the player closing it themselves is right either
    way. `game_name` is what was closed, so a UI can say so."""

    stopped: bool
    game_name: str | None


class TableControlsTable(ApiModel):
    """The table the controls are for, and the app that plays it."""

    game_id: str | None = None
    game_name: str | None = None
    app: str


class TableControl(ApiModel):
    """One control of the running table. `id` is stable and `label` is in the install's
    language. `hold_ms` is how long the install holds the key. `reason` is a sentence
    where `available` is false, and empty otherwise."""

    id: str
    group: str
    label: str
    hold_ms: int
    holdable: bool = False
    side: str = ""
    available: bool
    reason: str = ""


class TableControlsNote(ApiModel):
    """One sentence for the controls that cannot be pressed for the same cause, naming
    them, so a caller says it once. `controls` holds their ids."""

    controls: list[str]
    text: str


class TableControls(ApiModel):
    """What can be pressed on the table this install is running. `table` is null, and
    `controls` empty, when nothing plays, a recording owns the table, or its app offers
    none. `press` is whether this device can send keys at all."""

    table: TableControlsTable | None = None
    press: PlayInputAbility
    controls: list[TableControl]
    notes: list[TableControlsNote] = []


class TableControlPress(ApiModel):
    """`pressed` is that the key was sent, not that the table acted on it, and false when
    it could not be sent."""

    control_id: str
    pressed: bool


class TableControlRequest(ApiModel):
    """`source` is who is pressing, for the log. A `tap` is one short press. On a control
    listed `holdable`, `press` leaves the key down and `release` lets it go; send `press`
    again about three times within `ttl_ms` while it stays held, and the install lets go on
    its own when they stop."""

    source: str = ""
    phase: Literal["tap", "press", "release"] = "tap"
    ttl_ms: int | None = None


# --- Frontend --------------------------------------------------------------

class FrontendGameLinks(ApiModel):
    self_: str = Field(alias="self")


class FrontendGame(ApiModel):
    """A reference, as the event stream names a game: follow `links.self` for the rest.
    No links when the game has no id to address it by."""

    id: str
    name: str
    links: FrontendGameLinks | None = None


class FrontendPreview(ApiModel):
    """A recording waiting for a decision, shown in place: `showing` is `before`, the
    file serving its slot now, or `after`, the recording."""

    proposal: str
    showing: Literal["before", "after"]
    kind: str
    table_id: str
    game: FrontendGame


class FrontendState(ApiModel):
    """Also the `state` of the `frontend.state_changed` event. `collection` is "" for
    the whole library, `game` is null when nothing is on the wheel, and `preview` null
    when no recording is shown for a decision."""

    running: bool
    collection: str
    game: FrontendGame | None
    preview: FrontendPreview | None = None


class FrontendBrowserFix(ApiModel):
    """`action` is `use_chrome` where Google Chrome is installed and not in use, with its
    path in `chrome_path`; otherwise empty, and the fix is something a person does."""

    key: str
    action: str = ""
    chrome_path: str = ""


class FrontendBrowser(ApiModel):
    """What this device's frontend browser can play. `state` is `plays`, `no_h264`,
    `no_video`, `no_browser` or `unknown` - nothing has reported since this browser was
    chosen, which is not a no. A format is true, false, or null for not known."""

    state: str
    browser: str
    path: str
    bundled: bool
    # What an empty Program setting stands for: the browser VPinFE finds on its own, and
    # whether that is the copy this build ships.
    default_path: str = ""
    default_bundled: bool = False
    # `full` where this build ships a browser of its own, `slim` where it does not.
    variant: str = ""
    formats: dict[str, bool | None]
    plays: list[str]
    does_not_play: list[str]
    reported_at: str | None = None
    finding: str = ""
    fix: FrontendBrowserFix | None = None
    fix_text: str = ""
    # The install id of the library this frontend plays: this install's own where it holds
    # the library, the named install's where `network.library_url` is set, empty where that
    # install does not answer.
    library: str = ""


class ShowCollectionRequest(ApiModel):
    name: str = ""


class MoveWheelRequest(ApiModel):
    id: str


class ShowPreviewRequest(ApiModel):
    proposal: str
    showing: Literal["before", "after"] = "after"


# --- Players ---------------------------------------------------------------

class PlayerResource(ApiModel):
    """One player. `shares_initials_with` names the others holding the same initials,
    whose scores then go to nobody by initials."""

    id: str
    name: str = ""
    initials: str = ""
    owner: bool = False
    guest: bool = False
    up: bool = False
    shares_initials_with: list[str] = Field(default_factory=list)
    picture_version: str | None = Field(
        None, description="The `v` for `/players/{id}/picture`; null when they have none")


class PlayerRoster(ApiModel):
    """Also the `state` of the `players.changed` event. The owner first, then the kept
    players, then the guests as they joined."""

    players: list[PlayerResource]


class PlayerAddRequest(ApiModel):
    name: str = ""
    initials: str = ""


class GuestAddRequest(ApiModel):
    initials: str
    name: str = ""


class PlayerChangeRequest(ApiModel):
    """A patch: a field left out is left alone."""

    name: str | None = None
    initials: str | None = None


class PlayerUpRequest(ApiModel):
    up: bool


class PlayersUpRequest(ApiModel):
    ids: list[str]


class AccountAct(ApiModel):
    key: str
    label: str = ""
    description: str = ""


class PlayerAccount(ApiModel):
    """One account a player can hold, with the extension that holds it. `claimed` says
    whether `user_id` is registered with the service - free to change or drop until then,
    read-only after. `page` is the public page for a claimed account, empty otherwise.
    `fields` are as an extension's settings are, a secret one carrying `set` and never its
    value; with `fields` and no `user_id` the account is drawn as those fields alone, each
    saved as it is left. `card` says whether a card can be
    made for this player now. `error` is why the extension could not answer, and the rest
    is then empty. `share_help` is what the extension says Share does with this account,
    empty where it says nothing. `consent` is what Share tells the player becomes public;
    `needs_consent` says whether it still has to be shown before Share's next turn-on,
    true until it has been, once, whether or not the account is claimed. `check` is the
    extension's own route for checking a candidate id, asked at
    `/ext/{extension}{check}?candidate=…` - empty where it offers none. `reads_cards`
    says whether a card can be used for it. `waiting` is the same fact `waiting_count`
    puts as a number, as a plain boolean. `picture` says whether the service has a
    picture for this player to use."""

    extension: str
    label: str = ""
    share: bool = False
    share_help: str = ""
    consent: list[str] = Field(default_factory=list)
    check: str = ""
    user_id: str = ""
    claimed: bool = False
    needs_consent: bool = False
    page: str = ""
    fields: list[dict[str, Any]] = Field(default_factory=list)
    status: str = ""
    waiting: bool = False
    waiting_count: int = 0
    acts: list[AccountAct] = Field(default_factory=list)
    card: bool = False
    picture: bool = False
    reads_cards: bool = False
    error: str = ""


class PlayerAccounts(ApiModel):
    accounts: list[PlayerAccount]


class AccountValuesRequest(ApiModel):
    values: dict[str, Any]


class AccountShareRequest(ApiModel):
    share: bool


class GuestCardRequest(ApiModel):
    """A card file's contents, or the card's text on its own."""

    card: str = Field(max_length=1_000_000)


class PlayerBestScore(ApiModel):
    """A player's best entry on a game's high score table, shaped as `HighScoreEntry`,
    with the ROM whose table it was on and when it was made."""

    rom: str = ""
    # The machine's own words, `GRAND CHAMPION` - data, not a label.
    section: str = ""
    scored_at: str | None = None
    rank: int | None = None
    initials: str = ""
    score: int | None = None
    prefix: str = ""
    suffix: str = ""
    text: str = ""


class PlayerGameRecord(ApiModel):
    game_id: str
    play_count: int = 0
    play_time_seconds: int = 0
    last_played: str | None = None
    best_score: PlayerBestScore | None = None
    # 0-5, 0 unrated.
    rating: int = 0


class PlayerRecord(ApiModel):
    """What a player other than the owner has done with each game they played or rated,
    most recently played first."""

    player: str
    games: list[PlayerGameRecord]


# --- Games -----------------------------------------------------------------

class AssetFileBinding(ApiModel):
    """One asset file, attributed: dedicated to a table, shared (folder-named),
    or orphaned - stem-named for a table that is no longer there."""

    file: str
    binding: str
    table: str | None = None


class AssetEntry(ApiModel):
    """One asset kind on a game. The list endpoint carries presence only; the
    detail endpoint adds the attributed files; alt_color carries its formats."""

    present: bool
    formats: list[str] | None = None
    files: list[AssetFileBinding] | None = None


class Guide(ApiModel):
    """Material that explains a game. `url` always opens it; `youtube_id` is there
    for a surface that plays the video in place. `source` is where it lives, for
    showing. `origin` is `vps` for one the catalog supplied and `user` for a person's
    own, and `hidden` is only ever true on a game's own list - an entry and the theme
    payload leave hidden ones out."""

    kind: str
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    url: str = ""
    youtube_id: str = ""
    source: str = ""
    origin: str = ""
    hidden: bool = False


class GuideWrite(ApiModel):
    """One guide in a list being set. A `url` the game already holds names that guide;
    any other is a new one, and then `kind` and `title` describe it."""

    url: str
    hidden: bool = False
    kind: str = ""
    title: str = ""


class GuidesRequest(ApiModel):
    guides: list[GuideWrite]


class Guides(ApiModel):
    guides: list[Guide]


class GameLinks(ApiModel):
    self_: str = Field(alias="self")
    tables: str
    media: str
    archive: str
    launch: str
    rating: str


class ConfigOptionInfo(ApiModel):
    """One setting as a client should render it. `type` says how to read the value, not
    how it is stored; `choices` is non-empty only for a choice."""

    section: str
    key: str
    type: str
    default: str
    label: str
    # The catalog key behind `label`, for a client that resolves its own words.
    # `label` is this install's language; the key is what never moves. Additive, which
    # is why it does not move the theme contract.
    label_key: str = ""
    description: str = ""
    choices: list[str] = []
    choice_labels: dict[str, str] = {}
    # What each choice does, in this install's language, for a choice that needs more than its
    # name. Empty for the rest.
    choice_help: dict[str, str] = {}
    # Whether the choices read as a row of buttons rather than a menu; a client may ignore it.
    as_buttons: bool = False
    # The smallest and largest a number may be, or None where it has no bound.
    min: int | None = None
    max: int | None = None
    # The line under this setting's group heading, repeated on each option of the group.
    # Empty where the group has none.
    group_note: str = ""
    writable: bool = True
    # What this string names on disk, when it names something: file, dir or exe. Empty
    # for everything that is only text, so a client need not match on the key's name.
    path: str = ""
    # A live list worth offering beside this setting, named rather than included: it
    # changes while the install runs, so a client asks for it when it draws. Empty for
    # everything a person simply types.
    suggest: str = ""
    # What to gather it under within its section, or empty for the ones that sit above
    # the first heading. Served so the grouping is the schema's answer rather than one
    # each surface invents.
    group: str = ""
    # The heading `group` is drawn as, in this install's language.
    group_label: str = ""
    # A named tool this setting is edited with, where a control cannot do the job -
    # `binding` is a key or button captured by pressing it. `type` still says what the
    # value is, so a client with no such editor renders the ordinary control and edits
    # the value badly rather than not at all.
    editor: str = ""
    # How many rows a `text` value wants. Zero is one line, which is nearly all of them;
    # a list of commands is three. Served because the control is decided from it, and a
    # multi-line setting that arrives without it draws as a single line.
    lines: int = 0
    # Whose the default is - `community`, or `vpinfe` where no standard exists - for a
    # client that reminds a person of it; empty where none does. `unit` is what a number
    # counts: `seconds`, `port`, or empty.
    default_is: str = ""
    unit: str = ""
    # Whether a change can put this install's Manager out of reach of the device making it.
    cuts_off_manager: bool = False
    # What the files in this setting's folder are (`backups`), where a surface asks whether
    # they go across before it saves a new folder. Empty for every other setting.
    moves_with: str = ""
    # When a saved change reaches what it governs: `now`, `table` (the next table to
    # start), `windows` (the next time the frontend's windows open) or `restart`.
    applies: str = "now"
    # What an empty value stands for, in this install's language, for a client to show in
    # the empty field; empty where it needs no words.
    blank: str = ""
    home: str = ""


class ConfigSection(ApiModel):
    section_name: str = Field(alias="name")
    writable: bool = True
    options: list[ConfigOptionInfo]


class ConfigListChoice(ApiModel):
    value: str
    label: str


class ConfigListFieldInfo(ApiModel):
    """One field of a list's items."""

    key: str
    type: str
    default: str = ""
    label: str
    description: str = ""
    choices: list[ConfigListChoice] = []
    lines: int = 0
    minimum: float | None = None
    maximum: float | None = None
    path: str = ""
    suffixes: list[str] = []


class ConfigListKind(ApiModel):
    kind: str
    fields: list[ConfigListFieldInfo]


class ConfigListVariant(ApiModel):
    """Items of different kinds: `field` holds the kind, and each kind's own fields sit
    under the item's `settings`."""

    field: str
    kinds: list[ConfigListKind]


class ConfigListInfo(ApiModel):
    """A top-level list of objects, such as launchers. Every item also has an `id`."""

    key: str
    label: str
    home: str = ""
    fields: list[ConfigListFieldInfo]
    variant: ConfigListVariant | None = None


class ConfigSchema(ApiModel):
    """What this install has, not what a client thinks it has - a settings page built
    from this cannot offer a setting the install does not carry."""

    sections: list[ConfigSection]
    count: int
    lists: list[ConfigListInfo] = []


class ConfigPathCheck(ApiModel):
    """One path setting against this device's disk.

    `state` is `unset` when the value is blank, which is not a failure - most of these
    are optional and blank means the default. `reason` is written for the person who
    typed it and says what was found, because the path is right there to compare against.
    `default` is where a blank one points on this device, for a setting whose blank means a
    folder VPinFE chooses; empty for every other.

    A setting is named by `section` and `key`; a field of a list's item by `list`, the
    item's `id` and the `field`. The other three are left out.
    """

    section: str | None = None
    key: str | None = None
    list_key: str | None = Field(default=None, alias="list")
    item_id: str | None = Field(default=None, alias="id")
    field: str | None = None
    path: str
    state: str
    reason: str = ""
    default: str = ""


class ConfigPathChecks(ApiModel):
    checks: list[ConfigPathCheck]


class Said(ApiModel):
    """Words as a catalog key and the values for its slots, rendered where they are read
    so each reader says them in its own language."""

    key: str
    params: dict[str, str] = {}


class ToolGet(ApiModel):
    """What VPinFE would download in a missing Tool's place: its version, who publishes
    it, and its size in bytes."""

    version: str
    source: str
    size: int


class ToolRemedy(Said):
    """What to do about a Tool, and the setting that points at one instead. The hint's
    `setting` and `section` slots are that setting's label and the Settings section.
    `get` is there where VPinFE can get the Tool itself, with `POST
    /config/tools/{id}/get`."""

    setting: str
    get: ToolGet | None = None


class PlayInputRemedy(Said):
    """What to do about it. `setting` is the one that points at a Tool, where one does."""

    setting: str = ""


class PlayInputReason(Said):
    """`fix` is `user` where a person has to act, and then `remedy` says how."""

    fix: str
    remedy: PlayInputRemedy | None = None


class PlayInputAbility(ApiModel):
    """`via` is what does it here: `wtype`, `ydotool` or `pynput` to press, `evdev` or
    `pynput` to hear. Empty, with a `reason`, where nothing can."""

    available: bool
    via: str = ""
    reason: PlayInputReason | None = None


class PlayInput(ApiModel):
    """What this device can do with keys while a table runs: `press` them into it, as
    Take Picture presses the table's own Pause, and `hear` them from outside the
    frontend's page, as a key bound to Take Picture needs."""

    press: PlayInputAbility
    hear: PlayInputAbility


class ConfigTool(ApiModel):
    """One Tool on this device, as discovery finds it when asked.

    `state` is `found`, `missing`, `unusable` (found, and it does not run) or `not_here`
    (nothing to look for on this platform). `set_here` says the setting is what found it, and
    `default_path` and `default_version` are what would be found with no setting.
    `fix` is `auto` where VPinFE can get it once a person agrees, `user` where a person has
    to act and `none` otherwise.
    """

    id: str
    name: str
    setting: str
    state: str
    path: str = ""
    version: str = ""
    set_here: bool = False
    default_path: str = ""
    default_version: str = ""
    reason: Said | None = None
    fix: str
    remedy: ToolRemedy | None = None


class ConfigTools(ApiModel):
    tools: list[ConfigTool]


# --- Recording ---------------------------------------------------------------

class CaptureRemedy(Said):
    """What to do about it: a Tool's remedy, with its setting and any `get`, or a step
    that is no Tool's, which has neither."""

    setting: str = ""
    get: ToolGet | None = None


class CaptureReason(Said):
    """Why something cannot be recorded. `fix` is `user` where a person has to act, and
    then `remedy` says how; `auto` where VPinFE can do it once a person agrees - the
    remedy's `get` where it is a Tool to download, and Choose Screens where the desktop
    has not been told which screens VPinFE may record; `none` where nobody can."""

    fix: str
    remedy: CaptureRemedy | None = None


class CaptureAbility(ApiModel):
    available: bool
    reason: CaptureReason | None = None


class CaptureScreen(ApiModel):
    """One window and the output it is on. `size` is the output as the desktop lays it
    out, and `surface` its shape; all three are null where no output is found."""

    window: str
    output: str | None = None
    size: list[int] | None = None
    surface: str | None = None
    picture: CaptureAbility
    video: CaptureAbility


class CaptureCommands(ApiModel):
    """VPinFE's own Record and Encode Commands on this device, tokens unexpanded. `record`
    is empty where this device has none."""

    record: str
    encode: str


class CaptureReport(ApiModel):
    """What this device can record, computed when asked. `adapter` is how it reaches its
    screens; `reason` is the first thing stopping it where nothing can be recorded;
    `video_codec` is what Automatic records here; `at_once` says every screen records in
    the same moment rather than one after another."""

    observed_at: str
    adapter: str
    available: bool
    reason: CaptureReason | None = None
    screens: list[CaptureScreen]
    sound: CaptureAbility
    video_codec: str
    at_once: bool
    tools: list[ConfigTool]
    commands: CaptureCommands


class CaptureTestRequest(ApiModel):
    """Recording settings to test with, in place of this device's, as a run takes
    them."""

    settings: dict[str, Any] = {}


class CaptureTestReason(ApiModel):
    key: str
    params: dict[str, str] = {}


class CaptureTest(ApiModel):
    """What the commands made of a few seconds of the playfield. Where `ok` is false,
    `step` is the command that failed, `record` or `encode`, and `detail` the last thing
    it said. `size`, `fps` and `frames` are the stored file's; `picture` is a small JPEG of
    it as a data URL. `record` and `encode` are the commands as run."""

    ok: bool
    step: str | None = None
    reason: CaptureTestReason | None = None
    detail: str = ""
    size: list[int] | None = None
    fps: float | None = None
    frames: int
    seconds: float
    picture: str
    record: str
    encode: str


class CaptureReplaces(ApiModel):
    """The file a proposal would take the place of, whose it is, and whether it goes."""

    path: str
    source: str
    goes: bool


class CaptureProposal(ApiModel):
    """A recording waiting for a decision. `url` is where it plays; `size` is in bytes;
    `replaces` is null where its slot is empty."""

    id: str
    game_id: str
    table_id: str
    kind: str
    name: str
    file: str
    created: str
    size: int
    replaces: CaptureReplaces | None = None
    url: str


class CaptureProposals(ApiModel):
    count: int
    bytes: int
    proposals: list[CaptureProposal]


class CaptureProposalUse(ApiModel):
    use: bool


class CaptureProposalUsed(ApiModel):
    """The kind placed, null where it was thrown away, and the files that went with it."""

    placed: str | None = None
    removed: list[str] = []


class CaptureProposalsDiscarded(ApiModel):
    discarded: int


class CaptureTable(ApiModel):
    game: str
    table: str


class CaptureConfirmed(ApiModel):
    count: int


class CaptureRequest(ApiModel):
    """Games by id in `games`, and tables as a game and its table id in `tables`, recorded
    in that order, each once. `kinds` are media kinds; empty is every kind this device can
    record, with `audio`
    where `sound` - or, where that is left out, the Sound setting - says so. `settings`
    holds Recording settings for this run only. `existing` is `fill`,
    `replace_downloaded`, `replace_all` or `choose`; `review` proposes every recording,
    an empty slot's too. `confirmed.count` must equal the plan's `replacing` where that
    is more than none."""

    games: list[str] = []
    tables: list[CaptureTable] = []
    kinds: list[str] = []
    existing: str = "fill"
    settings: dict[str, Any] = {}
    sound: bool | None = None
    confirmed: CaptureConfirmed | None = None
    review: bool = False


class CapturePlanKind(ApiModel):
    """`does` is `fill`, `replace`, `propose` or `leave`. `source` is whose file serves the
    slot and `file` its path in the game's folder; `goes` says that file is deleted once a
    recording is placed. `reason` says why this device cannot record the kind at all."""

    kind: str
    does: str
    source: str | None = None
    reason: CaptureReason | None = None
    file: str | None = None
    goes: bool = False


class CapturePlanTarget(ApiModel):
    """One game or table: what becomes of each kind's slot, and its own counts."""

    game_id: str
    table_id: str
    name: str
    kinds: list[CapturePlanKind]
    recording: list[str]
    replacing: int
    replacing_by_source: dict[str, int]
    estimate_seconds: int


class CapturePlanTotal(ApiModel):
    """One kind over every target: how many slots have no file and how many have one.
    `reason` says why this device cannot record it at all."""

    kind: str
    reason: CaptureReason | None = None
    missing: int
    have: int


class CapturePlan(ApiModel):
    """`fills` counts slots a recording is placed in without deleting anything, `asks`
    those kept for a decision; `launches` the tables that will be started."""

    games: int
    targets: list[CapturePlanTarget]
    kinds: list[CapturePlanTotal]
    recording: list[str]
    fills: int
    asks: int
    replacing: int
    replacing_by_source: dict[str, int]
    launches: int
    at_once: bool
    estimate_seconds: int


class CaptureRunGame(ApiModel):
    id: str
    table_id: str
    name: str


class CaptureRun(ApiModel):
    """A run in hand. `state` is `running` or `paused`; `reason` is why it paused, null
    where a person paused it, and `capture.run.interrupted` where VPinFE closed while it
    ran. `game` is the one being recorded, or next; `done` of `of` have been. The counts
    are tables by outcome, and `proposed` the recordings kept for a decision.
    `estimate_seconds` is what is left."""

    id: str
    state: str
    reason: CaptureTestReason | None = None
    job_id: str | None = None
    done: int
    of: int
    game: CaptureRunGame
    existing: str
    recorded: int
    failed: int
    closed: int
    skipped: int
    proposed: int
    estimate_seconds: int


class CaptureRunCurrent(ApiModel):
    """The run in hand, null where there is none."""

    run: CaptureRun | None = None


class ConfigValues(ApiModel):
    """The file's own shape: `settings` keyed section then key, typed as the store types
    them (a setting the file omits answers its default, which is what the install is
    running on), and beside it each list the schema declares, under its key, and
    `theme_options`, what was chosen on each theme by its folder name."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    settings: dict[str, dict[str, object]]


class ConfigWaitingEntry(ApiModel):
    """One change that is saved and not yet in effect. `kind` is `setting` or `extension`;
    for an extension `key` is its name and `set` is that it is on."""

    kind: str
    section: str
    key: str
    label: str
    label_key: str
    waits_for: str
    in_effect: object
    set: object


class ConfigWaiting(ApiModel):
    """What is waiting for a restart or for the windows to reopen. `reopen` is whether
    this install can reopen them; `http_port` is where the next start listens."""

    waiting: list[ConfigWaitingEntry]
    reopen: bool
    http_port: int


class GameOverrides(ApiModel):
    """What the user said, against what was discovered about the game.

    Kept beside the discovered values rather than written onto them: the fields VPS
    supplies are rebuilt wholesale on every scan, so a value written on top does not
    survive one. Empty means no override - the discovered value stands and is still
    there to go back to.

    These are the game's because they are about the game itself: what it is, which VPS
    record it is, and the effect it asks for when somebody browses to it. The ones that
    govern a single file are on the table - see `TableOverrides`.
    """

    alt_title: str = ""
    # `null` says this machine is in no catalog, which is not the same as "" - that is
    # nobody having looked, and the scan's answer stands.
    alt_vps_id: str | None = ""
    frontend_dof_event: str = ""
    alt_manufacturer: str = ""
    alt_year: str = ""
    alt_type: str = ""
    alt_themes: list[str] = Field(default_factory=list)
    alt_ipdb_id: str = ""


class GameDiscovered(ApiModel):
    """What each overridable field would say with no override.

    On the wire because a surface offering to undo an override has to name what it
    undoes to, and the effective value has already resolved that away. Only the fields
    something outside VPinFE supplies appear here - the rest are the user's alone and
    have nothing underneath.
    """

    name: str = ""
    vps_id: str = ""
    # As `GameResource.vps_matched_by`, for the entry underneath any override.
    vps_matched_by: str = ""
    manufacturer: str = ""
    year: str = ""
    type: str = ""
    themes: list[str] = Field(default_factory=list)
    ipdb_id: str = ""


class TableOverrides(ApiModel):
    """What the user said about one launchable file.

    Per table, because each governs one file: which binary runs it, which ini it
    launches with, and whose nvram is its own. One folder can hold a VPX table and a
    Future Pinball one, and a single value cannot answer for both.
    """

    alt_launcher: str = ""
    plugin_profile: str = ""
    delete_nvram_on_close: bool = False


class OverridesPatch(ApiModel):
    """A change to some overrides. Every field is optional and only what is sent is
    written, so a client can set one without restating the others. Sending `""` (or
    false) clears an override, which is how a value goes back to what was discovered.

    One model for both levels: the route decides which keys it accepts, and a key that
    does not belong to that level is refused rather than quietly dropped.
    """

    alt_title: str | None = None
    alt_vps_id: str | None = None
    alt_manufacturer: str | None = None
    alt_year: str | None = None
    alt_type: str | None = None
    alt_themes: list[str] | None = None
    alt_ipdb_id: str | None = None
    frontend_dof_event: str | None = None
    alt_launcher: str | None = None
    plugin_profile: str | None = None
    delete_nvram_on_close: bool | None = None


class PlayRecordUpdate(ApiModel):
    """Counters a library arriving from somewhere else brings with it.

    A patch: a field left out is left alone, so a source that knows how often a game was
    played but not for how long does not silently zero the time.
    """

    play_count: int | None = None
    play_time_seconds: int | None = None
    # Epoch seconds, the same as the file keeps. Null clears it.
    last_played: int | None = None


class HighScoreEntry(ApiModel):
    """One entry on a high score table, as the ROM wrote it."""

    # None for an entry the machine does not rank, a Grand Champion.
    rank: int | None = None
    # Blank where the machine recorded none.
    initials: str = ""
    # None where the entry is not a number - a lap time, a date, a line of words.
    score: int | None = None
    # The machine's words either side of `score`, spacing included: `$ `, ` LOOPS`.
    prefix: str = ""
    suffix: str = ""
    # The value as the machine shows it: prefix, score and suffix, or its lines joined
    # with a newline where it holds no number.
    text: str = ""
    # Not on the table before the last game.
    new: bool = False
    # The name of the player this entry is for, where it can be told: whoever has its
    # initials, or whose record holds a blank one. Blank where nobody is.
    player: str = ""


class HighScoreSection(ApiModel):
    # The machine's own words, `GRAND CHAMPION`, `HIGH SCORES` - data, not a label.
    name: str = ""
    entries: list[HighScoreEntry] = Field(default_factory=list)


class HighScores(ApiModel):
    """A ROM's high score table, as VPinFE last read it."""

    rom: str
    # The table whose ROM this is.
    table_id: str = ""
    # When it was read. Null for one read before VPinFE recorded that.
    read_at: str | None = None
    # `read`, or where nothing is shown: `none` (nothing saved yet), `unsupported` (a
    # machine the score map cannot read), `unreadable` (the read failed, `reason` says why).
    state: str = "read"
    reason: str = ""
    # The score file's name where the table was read from a text file, not a ROM's own file.
    file: str = ""
    sections: list[HighScoreSection] = Field(default_factory=list)


class Picture(ApiModel):
    """A picture of every screen, taken during play, in the game's `pictures/` folder."""

    # The file's name, which is how the routes below name it.
    name: str
    # When it was taken, in UTC.
    taken: str
    # The table that was playing. Empty where the picture does not say.
    table_id: str = ""
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    # Changes whenever the file does: the file route's `v`.
    version: str = ""


class PictureList(ApiModel):
    pictures: list[Picture]


class PictureRemoved(ApiModel):
    removed: str


class PlayRecord(ApiModel):
    """What a person did with this, in a consumer's units rather than the file's - the
    `.info` keeps LastRun as an epoch integer and RunTime in minutes."""

    rating: int = 0
    favorite: bool = False
    tags: list[str] = Field(default_factory=list)
    last_played: str | None = None
    # The default table's machine. Null where nothing has been read.
    high_scores: HighScores | None = None
    play_count: int = 0
    play_time_seconds: int = 0


class GameResource(ApiModel):
    """A game: the pinball-machine concept, not a launchable file. vps_id correlates
    with VPSdb and anything keyed by it; `id` is what identifies the game here."""

    id: str
    vps_id: str
    # Who made the match in `vps_id`: "import" for a pick on import, "user" for one a
    # person made or a no-match they declared, "" for VPinFE's guess from the folder name
    # and for no match.
    vps_matched_by: str = ""
    name: str
    manufacturer: str
    year: str
    type: str
    themes: list[str]
    authors: list[str]
    # Both read off the game's default table, so both are one of possibly several.
    # `table_count` is what says whether there was a choice to make.
    rom: str
    version: str
    table_count: int = 0
    # Its tables are hidden and none is left to offer, so no frontend lists it.
    hidden: bool = False
    # Never sent to a community service, whoever plays it.
    private: bool = False
    rating: int
    collections: list[str]
    # The folder on disk. Reported because it is the one thing a user can act on
    # outside VPinFE, and because two games can read identically without it.
    folder: str = ""
    overrides: GameOverrides = GameOverrides()
    discovered: GameDiscovered = GameDiscovered()
    assets: dict[str, AssetEntry]
    # The play record - rating, favorite, tags and the counters. It reached only the
    # play lens (`EntryGame`), so the surface that manages a library could not show
    # what somebody thought of a game or how much they had played it.
    user: PlayRecord = Field(default_factory=PlayRecord)
    # Tags an extension's Community list puts on this, beside the user's own in `user`.
    derived_tags: list[str] = Field(default_factory=list)
    # The catalog's own id for the machine, and the tutorial it lists. Stored on nearly
    # every game and published nowhere until now.
    ipdb_id: str = ""
    tutorial: str = ""
    guides: list[Guide] = Field(default_factory=list)
    links: GameLinks


class GameList(ApiModel):
    """`total` counts what matched before limit and offset were applied."""

    total: int
    offset: int
    count: int
    games: list[GameResource]


class ResolvedAsset(ApiModel):
    """What this table would use for one kind: dedicated, shared, or none,
    plus the winning file."""

    resolution: str
    file: str | None = None


class NvramState(ApiModel):
    """Play state keyed by the effective rom, stat-ed per request so modified_at
    is live."""

    present: bool
    file: str | None = None
    modified_at: int | None = None


class PinmameChain(ApiModel):
    """declared -> alias -> effective -> required -> catalog/audit -> installed.
    Every unknown is null, never a guess."""

    declared: str | None
    alias_of: str | None
    effective: str | None
    required: bool | None
    catalog: bool | None
    clone_of: str | None
    description: str | None = None
    audit: str | None
    installed: bool | None
    nvram: NvramState


class FlexDmdState(ApiModel):
    detected: bool | None
    declared: str | None
    installed: bool
    content: list[str]


class Dependencies(ApiModel):
    """Script-declared, content-satisfied - a different mechanism from an asset
    found by naming rule."""

    pinmame: PinmameChain
    flexdmd: FlexDmdState


class TablePlayRecord(ApiModel):
    """One table's own play record: its counters, and the tags somebody gave this table
    rather than its game. Nothing sets a per-table rating here."""

    last_played: str | None = None
    play_count: int = 0
    play_time_seconds: int = 0
    tags: list[str] = Field(default_factory=list)


class ModOf(ApiModel):
    """The release a mod is based on, as VPS records it.

    `vps_file_id` is empty where VPS does not say which release: tagged `MOD` with no
    link, or a link the catalog cannot find. `note` is then VPS's own comment, and empty
    too where VPS has none, which is a mod of something unknown. `game` names the game
    only where it is another game than the mod's. `url` is that game's VPS page.
    `game_id` and `table_id` are the table in this library matched to that release, empty
    where there is none.
    """

    vps_file_id: str = ""
    version: str = ""
    authors: list[str] = []
    game: str = ""
    url: str = ""
    note: str = ""
    game_id: str = ""
    table_id: str = ""


class MadeFrom(ApiModel):
    """The file a patch was applied to, to make this table.

    `table_id` is the game's table recorded as those exact bytes, whether or not its file
    is still there, and empty where the game has none. `available` is whether that file
    is on disk.
    """

    file: str = ""
    table_id: str = ""
    available: bool = False


class TableSource(ApiModel):
    """Which upstream release a table is, and what established that.

    `confirmed_by` says what established the tie - `user` where a person picked from a
    list, `declared` where whatever delivered the bytes said which record it fetched,
    `construction` where we built the file ourselves, `exact` where VPinFE found exactly
    one release with the file's authors and version. A caller that inferred an identity
    sends nothing and the file stays unclaimed.
    """

    # Empty on a table with no release picked. With `confirmed_by` `user` it is a person
    # saying the table is none of them, which keeps VPinFE from tying it again.
    vps_file_id: str = ""
    confirmed_by: str = ""
    # The version VPS listed for the release when it was tied, which is what an update is
    # measured from. Empty where the catalog listed none or the tie predates recording it.
    vps_version: str = ""
    # The release named, resolved here because the catalog is already open on this side
    # and the alternative is a client fetching every build of the machine to render one
    # row. Empty where the id names nothing the catalog still holds.
    version: str = ""
    authors: list[str] = []
    # Null where the release is not a mod, or the catalog no longer holds it.
    mod_of: ModOf | None = None
    # Null on every table but one this install made by applying a patch.
    base: MadeFrom | None = None


class TableSourceRequest(ApiModel):
    """An empty id unbinds. There is no field for how sure the caller is: the basis is
    what the endpoint records, and this one always records a person."""

    vps_file_id: str = ""


class AssetSourceRequest(ApiModel):
    """Which VPS record the file at `path` is. `path` is folder-relative, as the ledger
    keys it; an empty id unbinds. Same basis rule as `TableSourceRequest`."""

    path: str
    vps_file_id: str = ""


class AssetSource(ApiModel):
    """One ledger entry's `source`, after a write.

    `host` and `hash` say who placed the file and what they published it as; the other
    two say which upstream record it is, and `vps_version` what VPS listed for it when it
    was tied. The pair come apart in both directions - a hand-placed file can be matched,
    a downloaded one can be unidentified.
    """

    host: str = ""
    hash: str = ""
    vps_file_id: str = ""
    confirmed_by: str = ""
    vps_version: str = ""


class Table(ApiModel):
    """A launchable artifact.

    `available` is false for a file the metadata records but which is not on disk -
    worth reporting rather than hiding. `absent_since` says how long that has been
    true, which is the difference between a share that has not mounted and a deletion.

    `hidden` is the user's choice not to be offered this table in the frontend; the
    file stays on disk, because a patch base has to (the patched table cannot be
    rebuilt without it) and a variant may be wanted back. Consumers listing what to
    play should skip these; consumers managing a library should not.

    `default` names the file the table's metadata was derived from, not the one to
    launch - every visible table is independently launchable.
    """

    id: str
    format: str
    app: str
    # What to call that app on screen. Ids are for the wire.
    app_name: str = ""
    # Which of VPS's table formats that app plays. Empty where it declares none, and every
    # release is then a candidate.
    vps_formats: list[str] = Field(default_factory=list)
    # The same four the row lens carries, so the two cannot describe one table
    # differently. Resolved, not read off the assignment.
    launcher: str = ""
    launcher_name: str = ""
    launcher_set_here: bool = False
    launcher_falls_back: bool = False
    # Whether the program that launcher runs has settings of its own. A launcher whose
    # app has none opens onto nothing, so the row that leads there is simply absent.
    launcher_app_configurable: bool = False
    # How many settings the one file that program reads for this table changes, counted
    # under whose file it is: the length of `launcher_settings_keys`.
    launcher_settings_here: int = 0
    launcher_settings_from_folder: int = 0
    launcher_point_of_view: bool = False
    # Which settings that file changes, by the program's own key, a saved camera as the
    # one entry `point_of_view`.
    launcher_settings_keys: list[str] = Field(default_factory=list)
    filename: str
    # `contained` for something in the game's folder, `referenced` for a file elsewhere
    # that it points at, `keyed` for something with no file at all that its app finds by
    # a name instead. Derived from the record rather than stored, so it cannot disagree
    # with it.
    form: str = "contained"
    # The name its app knows it by. Empty for everything with a file, which is nearly
    # everything - and where it is set, `filename` is empty because there is no file.
    key: str = ""
    # Where the file is, on a referenced entry. Null on every other, where `filename`
    # already says and the game folder is the answer to "where".
    reference: TableReference | None = None
    default: bool
    # Why it is the default, not only that it is: `user` where somebody chose it,
    # `auto` where the resolver picked one, the newest added. Empty on a table that is
    # not the default. A reader needs the two apart: a choice does not move, and a
    # derived pick changes when a table is added.
    default_kind: str = ""
    # The table the default is with no choice recorded: where clearing one moves it.
    automatic: bool = False
    # Null on almost every table, and that is the honest answer rather than a gap:
    # nothing has looked, which is a different state from having looked and found
    # nothing. Only a matcher can produce the second, and there is no matcher.
    source: TableSource | None = None
    # Whether VPS lists a later version of that release than `version`. Null where
    # nothing can be weighed - no release, or a version either side that does not read
    # as one - which is not the same as up to date.
    update_available: bool | None = None
    hidden: bool
    # The table's own, 0 where it has none - which is most of them. The game's rating is
    # the headline and this refines it, so 0 means "the game's stands", not "poor".
    rating: int = 0
    available: bool
    absent_since: str | None = None
    # Read out of the .vpx itself, so a table nobody has matched still says who built
    # it and which revision it is. Both are advisory: a table's embedded metadata is
    # only as careful as its author was, and a VPS match is the better answer wherever
    # there is one. Empty is common and means nothing was recorded.
    version: str = ""
    authors: list[str] = []
    # Of the .vpx and of its script. The pair is how a client tells "the same table
    # again" from "a different build with the same name", which no filename does.
    file_hash: str = ""
    vbs_hash: str = ""
    # When the author released it, and when the file itself was last saved. Two
    # different dates: a mod saved today can be a release from years ago, and a catalog
    # tells them apart by both. Recorded since schema 2 and published nowhere until now.
    release_date: str = ""
    save_date: str = ""
    save_rev: str = ""
    # What the file itself declares about the machine, which is not always what the game
    # resolves to: the game's answer prefers a catalog match, and these are the author's
    # own words. A consumer describing the file wants these.
    manufacturer: str = ""
    year: str = ""
    type: str = ""
    # The ROM the script names, empty where it names none. Each table answers for itself;
    # the game's `rom` is the default table's.
    rom: str = ""
    # What the script was seen to use. Three-valued per feature: true, false, and
    # null for a table nothing has parsed yet - which is not the same as "no".
    features: dict[str, bool | None] = {}
    overrides: TableOverrides = TableOverrides()
    assets: dict[str, ResolvedAsset]
    # Whether every required-to-launch asset resolves for this file. Three-valued like
    # the rom it is built from: null is a table nothing has parsed, and only false is
    # a fault. Computed here so a second client does not have to re-derive it.
    launchable: bool | None = None
    # This table's own counters. The game's record is the headline; a table that has
    # been played and a sibling that has not is the thing a game's total cannot say.
    user: TablePlayRecord = Field(default_factory=TablePlayRecord)
    derived_tags: list[str] = Field(default_factory=list)
    # Null on an entry with no file. Both of these come out of reading one, so an entry
    # that has none cannot have either - and reporting them as unknown would put a
    # dependency on something that can never carry one.
    dependencies: Dependencies | None = None


class TableReference(ApiModel):
    """Where an entry's file is, when it is not in the game's folder."""

    # As stored: relative to the game folder, or absolute. Relative is what survives the
    # library being moved or shared as one piece, so it is used wherever it can be.
    path: str
    # As it comes out on this machine. A relative path cannot be read without it.
    resolved: str = ""
    # Whether the file is there right now. **False is not "missing"** - the usual cause
    # is a share that has not mounted, the record and the media are still here, and the
    # answer is to make the location reachable rather than to forget the entry.
    reachable: bool = False


class GameDetails(ApiModel):
    """What the game is, where no catalog has matched it.

    A patch: a field left out is left alone, so a caller filling in a year need not
    restate a title it never knew. Sent empty, it is cleared.
    """

    title: str | None = None
    manufacturer: str | None = None
    year: str | None = None
    type: str | None = None
    themes: list[str] | None = None
    ipdb_id: str | None = None


class BackupRequest(ApiModel):
    """What one Back up now press covers. `games` names the games for the `games` scope and
    `launcher` the program for the `launcher` scope; the others take neither."""

    scope: Literal["games", "all_games", "records", "launcher", "everything"]
    games: list[str] = Field(default_factory=list)
    launcher: str = ""
    name: str = ""
    note: str = ""


class BackupFolderMove(ApiModel):
    """The value to give the backups folder setting, blank for the default, and to carry the
    backups in the folder now across to it."""

    folder: str


class BackupFolderChange(ApiModel):
    """What changing the backups folder would do. `moving` is how many backups are in the
    folder now, `already` how many the new one holds, and `problem` why the new one cannot
    be written to, empty when it can."""

    old: str
    new: str
    moving: int
    already: int
    problem: str


class BackupNote(ApiModel):
    """The note a backup you took carries. Empty clears it."""

    note: str


class NewGameRequest(ApiModel):
    """A game to create. `name` is the folder's name; `location` names where it goes,
    and left out it goes wherever new games are set to go."""

    name: str
    location: str = ""


class NewGameFromVpsRequest(ApiModel):
    """A game to create from a catalog entry: named as an import names it, matched to the
    entry, with no tables. `location` is as `NewGameRequest.location`."""

    vps_id: str
    location: str = ""


class TableImport(ApiModel):
    """A game file elsewhere on this device to copy into the game, as an absolute path.
    `/games/{id}/tables/import` refuses one that is not under a browsable root;
    `/folders/games/{id}/tables` takes any path on this device, from this device."""

    path: str


class NewTableRequest(ApiModel):
    """Something a game holds that a scan of its folder cannot find.

    Either a file somewhere else, by `path` - the full path to it, stored relative where
    that survives the library moving and absolute where it would not. Or something with
    no file at all, by `app` and `key`, where the key is whatever its program calls it
    and is never resolved here: the program already looks it up, and better.
    """

    path: str = ""
    app: str = ""
    key: str = ""


class TableList(ApiModel):
    tables: list[Table]


class LaunchApp(ApiModel):
    """A program that plays a table, and the files it claims.

    It carries no path. Where a program lives is a fact about one device, and it is a
    launcher that holds it - `GET /launchers` answers what this install can actually run
    with, and this list answers what kinds of file exist.
    """

    id: str
    name: str
    suffixes: list[str]
    # Whether it can play something named rather than pointed at - a rom its emulator
    # looks up, a store's own id. An app that only plays files says no, and offering it
    # where a name is being typed would be offering something that cannot work.
    accepts_keys: bool = False


class LaunchAppList(ApiModel):
    apps: list[LaunchApp]


class TableRow(ApiModel):
    """A table seen as a row in the library rather than as one game's child.

    Carries its game's name and maker so the row can be read on its own. `rom` is the
    resolved pinmame rom for this file, which is one of the few things that genuinely
    differs between two tables of the same game.
    """

    id: str
    game_id: str
    game: str
    manufacturer: str = ""
    year: str = ""
    filename: str
    # `contained` for something in the game's folder, `referenced` for a file elsewhere
    # it points at, `keyed` for something with no file at all that its app finds by a
    # name instead - in which case `filename` is empty and `key` is what it is called.
    form: str = "contained"
    key: str = ""
    # Where a referenced entry's file is, as stored. `available` already says whether it
    # is reachable; where it resolves to on this machine is a panel's question.
    reference: str = ""
    version: str = ""
    authors: list[str] = []
    source: TableSource | None = None
    update_available: bool | None = None
    # This table's own rating, not its game's. 0 is unrated, which is most of them.
    rating: int = 0
    game_rating: int = 0
    # The VPS entry the game is matched to, empty for a game that is not or whose entry
    # the catalog no longer holds. A table with a value here and no `source.vps_file_id`
    # is one whose release nobody has picked.
    game_vps_id: str = ""
    # What the script was seen to use, three-valued per feature: true, false, and null
    # for a table nothing has parsed - which is not the same as no.
    features: dict[str, bool | None] = {}
    # What this table would use per kind, the launch lens `GET .../tables` already
    # answers for a single game. The list projection named its fields by hand and
    # left this one out, so a client showing assets had to ask per game.
    assets: dict[str, ResolvedAsset] = {}
    # Asked for with `?media=`: the kind the table's media route serves the shown file
    # under, and that file's version. Null without `media` or where nothing resolves.
    media_kind: str | None = None
    media_version: str | None = None
    rom: str = ""
    # Whether that rom is actually installed. **Null when the table declares none** -
    # a table with no rom is not required to have one, and reporting that as missing
    # would call every EM table broken. Absence of a declaration and absence of a file
    # are different facts and stay apart.
    rom_installed: bool | None = None
    # Whether every required-to-launch asset resolves. Three-valued like the rom it is
    # built from: null is a table nothing has parsed, and only false is a fault.
    launchable: bool | None = None
    user: TablePlayRecord = Field(default_factory=TablePlayRecord)
    derived_tags: list[str] = Field(default_factory=list)
    default: bool = False
    # Why it is the default, not only that it is: `user` where somebody chose it,
    # `auto` where the resolver picked one, the newest added. Empty on a table that is
    # not the default. A reader needs the two apart: a choice does not move, and a
    # derived pick changes when a table is added.
    default_kind: str = ""
    # The table the default is with no choice recorded: where clearing one moves it.
    automatic: bool = False
    hidden: bool = False
    available: bool = True
    absent_since: str | None = None
    app: str = ""
    # Which launcher plays it, resolved rather than read off the assignment: a table
    # naming one that is switched off falls back, and a reader has to be shown what will
    # actually happen. Empty on an install with no launcher at all.
    launcher: str = ""
    launcher_name: str = ""
    # Whether this table chose it or is following the default. The thing an override
    # mask can never show - which tables were deliberately pointed somewhere, and so
    # what changing the default will and will not move.
    launcher_set_here: bool = False
    # Set here and not in effect: the one it names is switched off or gone.
    launcher_falls_back: bool = False
    # As one game's tables carry them: `Table` says what each means.
    launcher_app_configurable: bool = False
    launcher_settings_here: int = 0
    launcher_settings_from_folder: int = 0
    launcher_point_of_view: bool = False
    launcher_settings_keys: list[str] = Field(default_factory=list)
    # What to call the app on screen. Ids are for the wire.
    app_name: str = ""
    # `overrides.delete_nvram_on_close` as the table reads it.
    delete_nvram_on_close: bool = False


class TableRowList(ApiModel):
    total: int
    offset: int
    count: int
    tables: list[TableRow]


class MediaSlot(ApiModel):
    """One media file the library holds, or the absence of one.

    `table` is empty on the **shared** row - the file every table in the folder falls
    through to - and names a table on the rows that have a file of their own. Absence
    is only ever reported on the shared row: an empty table row would assert that a
    table-specific file ought to exist.
    """

    id: str
    game_id: str
    game: str
    manufacturer: str = ""
    year: str = ""
    kind: str
    label: str
    # The catalog key behind `label`, for a client that resolves its own words.
    # `label` is this install's language; the key is what never moves. Additive, which
    # is why it does not move the theme contract.
    label_key: str = ""
    # The page of a paged kind this row is: 1 for the first and for a kind that does not
    # page. A later page is a row of its own, with `:n` on its id and the first page's
    # scope. `named` is what a person calls the row - the label, and "Flyer, page 2" for
    # a later page - where `label` stays the kind's on every page of it.
    page: int = 1
    named: str = ""
    table: str = ""
    table_file: str = ""
    vps_id: str = ""
    # How many tables this file answers for: 1 on a table row, the tables with no file of
    # their own of that kind on a shared row, 0 on an unused or orphaned file. Null only
    # where the shared row has no file.
    serves: int | None = None
    present: bool = False
    file: str | None = None
    path: str | None = None
    # Why this file is the one being used, or why it is not being used at all:
    # "orphan" names a table the folder does not have, and "unused" is correctly named
    # but covered by something more specific - the fallback, which resolves again the
    # moment what covers it is removed.
    via: str | None = None
    # Who put the file here, as far as the .info ledger recorded. "unknown" is the
    # honest answer for anything placed before the ledger or by another tool.
    origin: str | None = None
    matched_to: str | None = None
    # The `v` for the file this row's route serves, where one does.
    version: str | None = None
    # What is filling this slot on the cabinet while the slot itself is empty - a set,
    # or a cross-kind fallback. Another slot's file, so it does not make this row
    # present, but a curator reading "Missing" against a machine that shows something
    # needs to know why.
    standing_in: str = ""


class AssetSlot(ApiModel):
    """One asset file the library holds, or the absence of one.

    `binding` is who the file answers for: "table" where it is named for one .vpx,
    "game" where it is the folder-named file every table falls back to, "orphaned"
    where it is named for a table that is not there, and "none" on the row that stands
    for a file the folder does not have.
    """

    id: str
    game_id: str
    game: str
    manufacturer: str = ""
    year: str = ""
    kind: str
    label: str
    # The catalog key behind `label`, for a client that resolves its own words.
    # `label` is this install's language; the key is what never moves. Additive, which
    # is why it does not move the theme contract.
    label_key: str = ""
    table: str = ""
    table_file: str = ""
    vps_id: str = ""
    binding: str
    present: bool = False
    # How many tables this file answers for. Null on a file named for one table, and
    # **0 on a folder-named file for a kind VPX resolves stem-only** - a `.vbs` or a
    # `.pov` named for the folder is inert, which is a thing worth being able to see.
    serves: int | None = None
    file: str | None = None
    path: str | None = None
    origin: str | None = None
    matched_to: str | None = None


class OutsideLink(ApiModel):
    key: str
    name: str
    url: str


class OutsideLinks(ApiModel):
    links: list[OutsideLink]


class AssetDisplay(ApiModel):
    """A DMD the backglass file places, in pixels of the art counted from its top left.
    `target` is `Backglass` or `ScoreView`."""

    target: str
    x: int
    y: int
    w: int
    h: int


class AssetDisplayPlace(ApiModel):
    """Set one window's DMD in the backglass file. `target` is `Backglass` or `ScoreView`;
    `x` and `y` are the top left corner, 0 to 100000, and `w` and `h` the size, 1 to 100000,
    in pixels of the art from its top left. Leave all four out to take the DMD out of the
    file. A key not named here is refused."""

    model_config = ConfigDict(extra="forbid")

    target: str
    x: int | None = Field(default=None, ge=0, le=100000)
    y: int | None = Field(default=None, ge=0, le=100000)
    w: int | None = Field(default=None, ge=1, le=100000)
    h: int | None = Field(default=None, ge=1, le=100000)


class TableOrderFailed(ApiModel):
    """A table whose own settings could not be written for the order its backglass and score
    display draw in: its id, the file it plays, and why."""

    table: str
    name: str
    reason: str


class AssetDisplaysWritten(ApiModel):
    """The DMDs the file now places, its new `version`, and `undo`, the token that takes the
    change back, empty where nothing changed or where the change was too big to keep, which
    `undo_refused` then says as `too_large`. `order_failed` names the tables of the game whose
    draw order could not be brought to what the file now places."""

    displays: list[AssetDisplay]
    version: str
    undo: str
    undo_refused: str = ""
    order_failed: list[TableOrderFailed] = []


class AssetDisplaysUndo(ApiModel):
    undo: str


class AssetBackglass(ApiModel):
    """What a backglass file says about itself. `size` and `scoreview_art` are
    [width, height] in pixels, `grill` the height of the bottom strip the renderer
    cuts, `lamps` how many lamps it holds, `still` that nothing in it can light,
    `problems` what could not be
    read, `damaged` whether the picture cannot be drawn, `displays` the DMDs the file
    places, and `version` the token that changes with the file."""

    author: str
    artwork: str
    rom: str
    players: int | None = None
    grill: int
    two_versions: bool
    size: list[int] | None = None
    lamps: int
    still: bool
    scoreview_art: list[int] | None = None
    problems: list[str]
    damaged: bool
    displays: list[AssetDisplay] = []
    version: str


class AssetDetail(ApiModel):
    """One asset file, or one folder of them. `files` counts a folder's contents and is
    null for a file; `head` is a text file's first lines and null for anything else;
    `backglass` is a backglass file's own facts."""

    path: str
    file: str
    folder: bool = False
    files: int | None = None
    size_bytes: int | None = None
    modified: str | None = None
    format: str | None = None
    head: str | None = None
    backglass: AssetBackglass | None = None


class AssetPlacement(ApiModel):
    """One name a file of this kind could take, and what is already there under it.
    `table` is empty for the folder's own name, which every table without its own
    reads; `label` is then empty too."""

    table: str
    label: str
    base: str
    displaces: list[str]


class AssetPlacementList(ApiModel):
    placements: list[AssetPlacement]
    extensions: list[str]


class AssetImport(ApiModel):
    """A file elsewhere on this device, as an absolute path, and the table it is for -
    empty for the folder's own name. Refused unless it is under a browsable root."""

    path: str
    table: str = ""


class AssetWritten(ApiModel):
    """The name the file was given, and the file that name replaced, if any."""

    written: str
    displaced: list[str]


class AssetDisplaced(ApiModel):
    displaced: list[str]


class AssetText(ApiModel):
    """A text file as an editor holds it: lines end in "\\n" here whatever the file uses.
    `version` is what a save sends back. `editable` is false with `why` in words where the
    file cannot be written from this text."""

    text: str
    version: str
    encoding: str
    newline: str
    final_newline: bool
    editable: bool
    why: str


class AssetTextSave(ApiModel):
    """`replace_changed` writes over a file that is no longer the version read, and
    `keep_copy` false writes without the copy of the file as it was."""

    text: str
    version: str
    replace_changed: bool = False
    keep_copy: bool = True


class AssetTextSaved(ApiModel):
    """The new version, and where the copy of the file as it was went, if one was kept."""

    version: str
    kept: str | None = None


class AssetRemoved(ApiModel):
    removed: list[str]


class GameFolderDeleted(ApiModel):
    deleted: bool
    folder: str


class AssetSlotList(ApiModel):
    total: int
    offset: int
    count: int
    assets: list[AssetSlot]


class SharedMediaSlot(ApiModel):
    """A `MediaSlot` cut to what counting a library's media covers reads. Same names, same
    meaning."""

    game_id: str
    kind: str
    page: int = 1
    present: bool = False
    file: str | None = None
    path: str | None = None
    via: str | None = None
    origin: str | None = None
    matched_to: str | None = None
    version: str | None = None


class SharedMediaList(ApiModel):
    media: list[SharedMediaSlot]


class MediaSlotList(ApiModel):
    total: int
    offset: int
    count: int
    media: list[MediaSlot]


class TableVisibility(ApiModel):
    """Whether the frontend should offer this table. Hiding never touches the file: a
    patch base has to stay on disk, and a variant may be wanted back."""

    hidden: bool


class ExtensionSwitch(ApiModel):
    enabled: bool


class ExtensionValues(ApiModel):
    """The values a settings or account form sends back, by field key."""

    values: dict[str, Any] = Field(default_factory=dict)


class ExtensionVerb(ApiModel):
    """The rows a list's verb is pressed on, by their `id`."""

    ids: list[str] = Field(default_factory=list)


class ExtensionTarget(ApiModel):
    """A game, or one of its tables, an entry in the Games or Tables menu is pressed on."""

    game_id: str
    table_id: str = ""


class ExtensionLibraryVerb(ApiModel):
    """The games or tables an extension's entry in their menu is pressed on."""

    targets: list[ExtensionTarget] = Field(default_factory=list)


class ExtensionWho(ApiModel):
    """The game a dialog was opened for, which the library may not hold yet, and the table
    a file is saved for where the person chose one."""

    game_id: str = ""
    vps_id: str = ""
    name: str = ""
    table_id: str = ""


class ExtensionSourceTake(ApiModel):
    """The row of an extension's online source a person picked, for the slot of `kind` that
    `who.game_id` holds."""

    kind: str
    row: str
    who: ExtensionWho = Field(default_factory=ExtensionWho)


class ExtensionSourcePick(ApiModel):
    """One file ticked in Discovery: the slot `kind` it fills and the source's `key` for it."""

    kind: str
    key: str


class ExtensionSourceTakeMany(ApiModel):
    """Every file ticked in Discovery for one game, handed to the source at once."""

    picks: list[ExtensionSourcePick]
    who: ExtensionWho = Field(default_factory=ExtensionWho)


class ExtensionSourceAct(ApiModel):
    """A button an online source drew on the row `row` of `kind`, or on the pane itself where
    both are empty."""

    act: str
    kind: str = ""
    row: str = ""
    who: ExtensionWho = Field(default_factory=ExtensionWho)


class ExtensionAsk(ApiModel):
    """The step to ask for, the rows of a list the dialog was opened over, by `id`, and
    the game it was opened for."""

    step: str = ""
    rows: list[str] = Field(default_factory=list)
    who: ExtensionWho = Field(default_factory=ExtensionWho)


class ExtensionStep(ExtensionValues):
    """A dialog's values, the step they were entered on where it has steps, and the game
    it was opened for."""

    step: str = ""
    who: ExtensionWho = Field(default_factory=ExtensionWho)


class ExtensionAct(ExtensionStep):
    """One of a dialog's own buttons, pressed with the values on screen."""

    act: str


class ExtensionCard(ApiModel):
    """A card as a player pastes it, for the account to read."""

    card: Any = None


class TableHidden(ApiModel):
    """The table, and the one its game now offers first: null when it offers none."""

    table: Table
    default: Table | None = None


class TableDefault(ApiModel):
    """Which table a game offers first. Empty clears the choice, which puts it back to
    being resolved from what is in the folder."""

    table: str = ""


class TableForgotten(ApiModel):
    """The id whose record went. No file is named because none was touched - the entry
    described a .vpx that is not on disk."""

    forgotten: str


class TableDeleted(ApiModel):
    """What a table's deletion took off the disk, and the copies of each file in Backups."""

    deleted: bool
    table: str
    filename: str
    kept: list[dict]


class TableSwapBack(ApiModel):
    """The copies in Backups the table is swapped back to, from `kept` of the swap or the
    deletion that put them there."""

    copies: list[str]
    source: dict | None = None


class TableSwapped(ApiModel):
    """The table as it is now, and the copies of what the swap took out of the game.
    `source` is the release tie the table had before, which `swap_back` gives back."""

    table: Table
    kept: list[dict]
    source: dict | None = None


class EntryOverrides(ApiModel):
    """What the user said, against what was discovered. Kept beside the discovered
    values rather than written onto them, so what a scan rebuilds does not lose them."""

    alt_title: str = ""
    alt_vps_id: str = ""
    frontend_dof_event: str = ""


class EntryTableOverrides(ApiModel):
    """The ones that govern a single file rather than the game.

    The third a table can hold is not here: it names something one program does with one
    kind of hardware state, and a descriptor every app answers is the wrong place for
    that. It stays settable on the overrides route.
    """

    alt_launcher: str = ""
    plugin_profile: str = ""


class EntryGame(ApiModel):
    """The game half of an entry: enough to show it without a second request, which is
    what the play lens is for. `links.game` has the rest.

    No filesystem path - where a game lives is true only of the device that answered.
    """

    id: str
    vps_id: str
    name: str
    manufacturer: str
    year: str
    type: str
    themes: list[str] = Field(default_factory=list)
    dir_name: str = ""
    manufacturer_logo: str | None = None
    # When the folder appeared, which is what "Newest" sorts on. A stat of the library's
    # filesystem, so a client sorting its own copy has to be told rather than look.
    created_at: str | None = None
    # Flat, and again inside `user`. The flat one shipped first and clients may read it;
    # `user.rating` is where it belongs beside the rest of the play record.
    rating: int
    user: PlayRecord = Field(default_factory=PlayRecord)
    derived_tags: list[str] = Field(default_factory=list)
    ipdb_id: str = ""
    tutorial: str = ""
    guides: list[Guide] = Field(default_factory=list)
    overrides: EntryOverrides = Field(default_factory=EntryOverrides)


class EntryTable(ApiModel):
    """The table half. `default` is the game's own default, not this entry's position.

    `release_date` is when this table was published, which is the table's own answer and
    not the game's. The .vpx also records a company name, a company year and a playfield
    variant; none is here, because none is populated in practice and the first two would
    duplicate the game's.

    `id` is the same id `GET /games/{id}/tables` reports, so a client can hold one table
    across both lenses.
    """

    id: str
    filename: str
    version: str
    rom: str
    file_hash: str = ""
    default: bool
    hidden: bool = False
    release_date: str | None = None
    authors: list[str] = Field(default_factory=list)
    detects: dict[str, bool] = Field(default_factory=dict)
    user: TablePlayRecord = Field(default_factory=TablePlayRecord)
    derived_tags: list[str] = Field(default_factory=list)
    # The sha256 of the script sidecar, and what the authoring tool recorded about the
    # last save. Part of a table's identity the way `file_hash` and `version` are.
    vbs_hash: str = ""
    save_date: str | None = None
    save_rev: str | None = None
    overrides: EntryTableOverrides = Field(default_factory=EntryTableOverrides)


class EntryAssets(ApiModel):
    """What the game needs to play as intended - not the artwork it is browsed by."""

    pup_pack: bool = False
    alt_color: bool = False
    alt_sound: bool = False


class EntryLinks(ApiModel):
    game: str
    launch: str
    media: str


class Entry(ApiModel):
    """One row of a resolved collection: a table, with the game it belongs to.

    There is no entry id - `table.id` is the identity, and a table appears at most once
    in a collection. `siblings` is how many tables its game offers, so a client can tell
    whether there is anything to switch to without walking the list.
    """

    game: EntryGame
    table: EntryTable
    siblings: int
    assets: EntryAssets = Field(default_factory=EntryAssets)
    # Which media exists, not where it lives: the names of the kinds that resolved. The
    # bytes come from `endpoints.files`, so naming files here would put a filesystem
    # path on the wire and several hundred kilobytes with it.
    media: list[str] = Field(default_factory=list)
    # How many pages a kind has, for the kinds with two or more; a kind with one is absent.
    pages: dict[str, int] = Field(default_factory=dict)
    # Which group this entry falls in under the collection's order - a letter, a year, a
    # rating. Absent when the order has no groups; `EntryList.group_by` says which.
    group: str | None = None
    # What extensions have contributed about the game, keyed by what each declared.
    # Present and empty until one answers, so a client reading `ext.rating` is correct
    # without knowing there is a waiting state.
    ext: dict[str, Any] = Field(default_factory=dict)
    links: EntryLinks


class EntryList(ApiModel):
    """One entry per game. `collection` is empty for the whole library."""

    collection: str
    count: int
    # What kind of group each entry carries, empty when the order has none - so a client
    # can tell "no grouping here" from "the group happens to be empty".
    group_by: str = ""
    entries: list[Entry]


# --- Media -----------------------------------------------------------------

class MediaEntryLinks(ApiModel):
    self_: str | None = Field(alias="self")


class MediaEntry(ApiModel):
    """Every kind is listed, present or not, so a client enumerates what is
    possible instead of guessing from omissions. `via` says why this file is the one
    being used; `origin` says who put it there, which nothing can derive from the
    other - "unknown" is the honest answer for a file nobody recorded."""

    present: bool
    file: str | None
    # Folder-relative, as the ledger keys it. A name cannot be the address: the same
    # name resolves in two places and which is not a client's to guess.
    path: str | None = None
    via: str | None = None
    origin: str | None = None
    # Null means nobody has said, not that a look came back empty.
    matched_to: str | None = None
    # The `v` that lets `links.self` be cached for good; null when nothing is there.
    version: str | None = None
    # A format id of `GET /frontend/browser`'s; null unless asked with `?codecs=true`.
    video_codec: str | None = None
    # How many pages the kind has: 0 for nothing, 1 for a kind that does not page.
    pages: int = 0
    links: MediaEntryLinks


class MediaOverride(ApiModel):
    """A table that has its own file for a kind the game also has one for.

    Named per table rather than counted, because "which one" is the question - a game
    with four tables and one odd backglass is looking for that one.
    """

    table: str
    filename: str
    version: str = ""
    file: str


class MediaOverrideList(ApiModel):
    """Keyed by media kind. A kind no table overrides is absent rather than empty."""

    overrides: dict[str, list[MediaOverride]]


class MediaTier(ApiModel):
    """One tier that holds a file for this kind, and whether it is the one being used.

    The losers are listed too, because they are the answer to the only hard question a
    curator asks: media was replaced and nothing changed, which is always a more specific
    file sitting above the one that was edited.
    """

    tier: str
    file: str
    wins: bool
    # How many of the game's tables are shown this file, whichever lens asked.
    serves: int = 0


class BackglassFileOffer(ApiModel):
    """The backglass file whose picture could fill a backglass slot."""

    path: str
    file: str
    grill: bool
    version: str


class MediaDetail(ApiModel):
    """One slot in the detail curation needs and playing a game never asks for.

    Its own route rather than more fields on MediaEntry: this costs a stat and a header
    read per candidate file, and the media list is on the path a frontend walks every
    time the player changes game - on a library over the network that is the difference
    between free and not. `width`/`height` are an image's or a video's, `duration_s` a
    video's or a sound's, `format` is what the file's header says it is, and
    `video_codec` a video's codec as `GET /frontend/browser` names the format.
    """

    kind: str
    family: str
    # The page this answers for, and how many the kind has.
    page: int = 1
    pages: int = 0
    present: bool
    file: str | None
    path: str | None = None
    via: str | None = None
    origin: str | None = None
    matched_to: str | None = None
    size_bytes: int | None = None
    modified: str | None = None
    width: int | None = None
    height: int | None = None
    format: str | None = None
    duration_s: float | None = None
    video_codec: str | None = None
    tiers: list[MediaTier] = []
    # The file this lens is shown once a DELETE here has taken the files at its tier;
    # null when nothing would be.
    after_remove: str | None = None
    backglass_file: BackglassFileOffer | None = None
    links: MediaEntryLinks


class FilesystemRoot(ApiModel):
    """A folder browsing may start from. `source` says why it is allowed - this game's
    own folder, the game library, or a folder the owner listed - so a client can
    explain the boundary rather than just enforcing it."""

    path: str
    name: str
    source: str


class FilesystemRootList(ApiModel):
    roots: list[FilesystemRoot]


class FilesystemEntry(ApiModel):
    """One folder or media file. `family` is empty for a folder, which is also how a
    client tells the two apart without reading `kind` twice."""

    name: str
    path: str
    kind: str
    family: str = ""
    size_bytes: int | None = None


class FilesystemListing(ApiModel):
    """`parent` is null at a root, so a client knows where "up" stops without having
    to know which folders are allowed."""

    path: str
    parent: str | None = None
    entries: list[FilesystemEntry]


# --- Folders -----------------------------------------------------------------

class FolderEntry(ApiModel):
    name: str
    path: str


class FolderListing(ApiModel):
    """Unbounded, unlike `FilesystemListing`: a Browse button is choosing something on
    this device's disk, and `roots` is where it can jump to start again."""

    path: str
    parent: str
    folders: list[FolderEntry]
    files: list[FolderEntry]
    roots: list[FolderEntry]


class MediaSource(ApiModel):
    """An online source. `kinds` is what it can serve, so a client can tell a source
    with nothing for this slot from one that is switched off. `always_on` says it has no
    switch, and `versions` that it names the version of what it lists."""

    id: str
    name: str
    url: str
    enabled: bool
    kinds: list[str]
    extension: str = ""
    key: str = ""
    always_on: bool = False
    versions: bool = False


class MediaSourceList(ApiModel):
    sources: list[MediaSource]


class MediaSourceChanges(ApiModel):
    """The newest `version`, and the VPS ids whose online offers moved after the version
    asked about; a single empty id means any of them may have."""

    version: int
    vps_ids: list[str]


class WheelSetList(ApiModel):
    sets: list[str]


class MediaOffer(ApiModel):
    """One file a catalog will hand over. `size` is that source's own word for a
    variant - "4k" against VPinMediaDB - and is empty for a source that publishes one
    of a thing; only the source that produced it has to understand it again. `md5` is the
    hash of the file where the source publishes one, and empty where it does not."""

    source: str
    name: str
    url: str
    kind: str
    size: str = ""
    md5: str = ""


class MediaOfferList(ApiModel):
    offers: list[MediaOffer]


class GameOfferRow(ApiModel):
    """One file an extension's source will hand over. `vps_file_id` ties it to a release VPS
    lists, `current` says the game holds it already, and `state` and `acts` are where it
    stands and what can be pressed on it."""

    key: str
    name: str
    site: str = ""
    page: str = ""
    note: str = ""
    tag: str = ""
    picture: str = ""
    current: bool = False
    vps_file_id: str = ""
    state: dict[str, str] | None = None
    acts: list[dict[str, str]] = Field(default_factory=list)


class GameKindOffers(ApiModel):
    """What one source said for one kind, under the name that source knows the kind by.
    A source core fetches from fills `offers`, and one that hands the file over itself fills
    `rows` with the `note` and `link` it draws under them. `error` is why it could not
    answer, and then the rest are empty."""

    kind: str
    offers: list[MediaOffer] = Field(default_factory=list)
    rows: list[GameOfferRow] = Field(default_factory=list)
    note: str = ""
    link: dict[str, Any] | None = None
    error: str = ""


class GameSourceOffers(ApiModel):
    id: str
    name: str
    extension: str = ""
    key: str = ""
    kinds: list[GameKindOffers]


class GameOffers(ApiModel):
    """What every enabled source has for one game, asked together. `sources` holds the
    ones asked about at least one kind, in the order the registry lists them."""

    game_id: str
    vps_id: str
    sources: list[GameSourceOffers]


class EntryOffers(ApiModel):
    """What every enabled source has for one catalog entry, asked together: the answer of
    `GameOffers` for an entry asked about by its VPS id alone."""

    vps_id: str
    sources: list[GameSourceOffers]


class MediaPlacement(ApiModel):
    """One place a file could land. `table` is empty for the folder's own name, which
    every table resolves, and `label` is then empty too. `base` has no extension, which
    the file decides, and `displaces` is the whole family at that tier."""

    table: str
    label: str
    base: str
    displaces: list[str]


class MediaPlacementList(ApiModel):
    """`extensions` is what this kind accepts, in the order the resolver tries them."""

    placements: list[MediaPlacement]
    extensions: list[str]


class MediaImport(ApiModel):
    """A file elsewhere on this device to copy into the slot, as an absolute path,
    and which table it should serve. Refused unless it is under a browsable root."""

    path: str
    table: str = ""
    # From 2, the page of a paged kind to add or replace.
    page: int = Field(1, ge=1)


class MediaFromFile(ApiModel):
    """Which table the backglass file's picture should serve. An empty `table` means the
    folder's shared name, which every table in it resolves."""

    table: str = ""


class CatalogOption(ApiModel):
    """One file vpinmediadb publishes for a kind. `size` is "" for the kinds it
    carries at a single size, which is most of them."""

    size: str
    url: str
    md5: str = ""


class CatalogEntry(ApiModel):
    """What vpinmediadb has for one VPS id, keyed by our media kinds. A kind it does
    not carry is absent rather than empty - there is no topper in the catalog at all,
    and offering an empty one would be a dead choice on a menu."""

    vps_id: str
    kinds: dict[str, list[CatalogOption]]


class MediaFetch(ApiModel):
    """Which source to take a file from, for which VPS entry, and at which size.

    No URL. Only links a source produced for that id and kind are followed, so a
    caller can never point it at a host of their choosing."""

    source: str
    vps_id: str
    size: str = ""
    table: str = ""


class MediaList(ApiModel):
    media: dict[str, MediaEntry]


class MediaWritten(ApiModel):
    """The name the file was given, and the slot as it stands afterwards.

    Those differ: a folder-named file is outranked by any table-named one, so a
    write can succeed and change nothing about what resolves."""

    written: str
    media: dict[str, MediaEntry]


class FileMoveTables(ApiModel):
    """The tables that are to have the game's file. A table not listed keeps what it has."""

    tables: list[str]


class FileMoveGame(ApiModel):
    """The table whose own file becomes the game's."""

    table: str


class FileMoveUndo(ApiModel):
    """The token a move returned."""

    undo: str


class FileMoveKept(ApiModel):
    """A table's own file that the move replaced, and the copy Backups keeps of it."""

    table: str
    file: str
    kept: str


class FileMoved(ApiModel):
    """What a move did. `lost` names the tables left with no such file, `uses` the tables
    that use the game's file or their new one now, and `undo` the token that puts it back."""

    moved: list[dict[str, str]]
    replaced: list[FileMoveKept]
    lost: list[str]
    uses: list[str]
    changed_for: list[str]
    stays: bool
    undo: str


class FileMoveFacts(ApiModel):
    files: list[str]
    size: int
    modified: float


class FileMoveTable(ApiModel):
    id: str
    stem: str
    filename: str
    folder_named: bool
    own: FileMoveFacts | None
    ini: bool


class FileMoveState(ApiModel):
    """What a file move needs to ask: the game's file, each table's own, whether the file
    can go to the game, and whether a table of the game is playing."""

    folder: str
    game: FileMoveFacts | None
    tables: list[FileMoveTable]
    fallback: bool
    to_game: bool
    playing: bool


class FileMoveUndone(ApiModel):
    undone: bool


class MediaMove(ApiModel):
    """Swap two pages of a slot: page `page` takes number `to` and the page there takes
    `page`. `table` is the table whose slot it is, empty for the game's shared one."""

    table: str = ""
    page: int = Field(ge=1)
    to: int = Field(ge=1)


class MediaDisplaced(ApiModel):
    """Folder-relative paths a place would overwrite or delete, answered before the
    bytes are sent. Empty means the slot is free at that tier."""

    displaced: list[str]


class MediaRemoved(ApiModel):
    """Folder-relative paths that went. Empty means there was nothing at that tier."""

    removed: list[str]


# --- Collections -----------------------------------------------------------

# What a many-valued criterion accepts on the way in. Reported as a list on the way
# out, so a client reads one shape whatever it sent.
MultiValue = str | list[str]


class YearRange(ApiModel):
    """Years from one to another, both included. Either end may be left open."""

    from_: int | None = Field(default=None, alias="from")
    to: int | None = None


class CollectionCriteria(ApiModel):
    """A filter collection's criteria, as a collection reports them. "All" means
    unconstrained on that axis - the vocabulary the filter engine already uses, kept
    rather than translated so a client sees the same values the Manager shows."""

    # The many-valued axes accept a list and are always reported as one. A criterion
    # has always been stored comma-joined and the matcher has always split it, so this
    # is the contract catching up with the behavior rather than a new capability - and
    # a bare string typed the schema as single-valued, so no generated client could
    # ever produce one. A list also carries a value containing a comma, which the
    # joined form cannot.
    letter: MultiValue = "All"
    theme: MultiValue = "All"
    game_type: MultiValue = "All"
    manufacturer: MultiValue = "All"
    year: MultiValue = "All"
    # Single by declaration: two ratings at once say nothing that the floor does not
    # say better.
    rating: str = "All"
    rating_or_higher: bool = False
    # Absent rather than false when the collection says nothing about play, because
    # false is a criterion of its own here - it selects what has never been played.
    played: bool | None = None
    favorite: bool | None = None
    tags: MultiValue = "All"
    year_range: YearRange | None = None
    theme_none_of: MultiValue = "All"
    game_type_none_of: MultiValue = "All"
    manufacturer_none_of: MultiValue = "All"
    year_none_of: MultiValue = "All"
    tags_none_of: MultiValue = "All"


class CollectionFilters(CollectionCriteria):
    """Criteria as a request sends them, which may name the order too. On disk 2.x
    spells the direction `order_by`; here `order_by` is only ever the field."""

    order_by: str = DEFAULT_ORDER_BY
    direction: str = DEFAULT_DIRECTION


class CollectionLinks(ApiModel):
    self_: str = Field(alias="self")
    games: str


class GameCollection(ApiModel):
    """A collection holding a game. `how` is `added` where the game was written into it
    and `matched` where its rule matched the game."""

    name: str
    type: str
    how: str
    links: CollectionLinks


class GameCollections(ApiModel):
    game: str
    collections: list[GameCollection]


class TakenOutOf(ApiModel):
    """A collection keeping a game out: an exclusion in it names the game or one of its
    tables."""

    name: str
    type: str
    links: CollectionLinks


class GameInCollections(GameCollections):
    """The collections holding one game, and `taken_out`, the ones keeping it out."""

    taken_out: list[TakenOutOf]


class LibraryGameCollections(ApiModel):
    games: list[GameCollections]


class CollectionRanking(ApiModel):
    """The ranked view of a Community list a collection is ordered by. `title` is the
    list's and `name` the view's, in the language now set. `read_at` is when the list
    was last read. `offered` is false when no running extension offers the view, and
    then the collection is in title order and only `extension` and `display_name` are
    known."""

    extension: str
    display_name: str
    list: str
    view: str
    title: str
    name: str
    read_at: str
    offered: bool


class CollectionResource(ApiModel):
    """`type` is derived, not stored: `filter` where the collection carries criteria,
    `manual` where it does not. The two are not kinds - a collection may hold criteria,
    hand-picked members and exclusions together, and `type` only says whether anything
    about it is dynamic.

    `game_count` counts the *stored* members, which is not what the collection resolves
    to: criteria contribute rows that are not stored, and a member naming something
    this library no longer has is counted and resolves to nothing. Ask
    /collections/{name}/members for the stored membership with the state of each, or
    /collections/{name}/games for what it currently resolves to."""

    name: str
    type: str
    # What it is for, in the owner's words. The name has to be short and is the
    # identity; this is where the reason lives.
    description: str = ""
    image: str | None
    # The `v` that lets `/collections/{name}/image` be cached for good.
    image_version: str | None = None
    in_frontend: bool = True
    # What it resolves to right now - its size, which is the number a reader means by
    # "how big is this collection". `game_count` is the stored membership and the two
    # differ by design: criteria contribute rows that are stored nowhere.
    count: int = 0
    # What it would resolve to with its limit set aside: `count` where there is none.
    before_limit: int = 0
    # Up to four wheels of the games it hands out, in its order: what stands in for a
    # collection with no image of its own.
    game_wheels: list[str] = []
    game_count: int | None
    # Games, counted before the limit: written into it, matched by its rule, and taken
    # out whole by name.
    added: int = 0
    matched: int = 0
    excluded: int = 0
    # Stored members naming a game this library does not have, or a table its game
    # does not.
    missing: int = 0
    hidden: int = 0
    filters: CollectionCriteria | None
    # The cap, and how the list is ordered. Both were settable and neither was reported,
    # so a client could apply a cap and have no way to see that one was in force.
    # `manual` order means the stored member array is the order.
    limit: int | None = None
    order_by: str = ""
    direction: str = ""
    # Null unless `order_by` names a ranked view.
    ranking: CollectionRanking | None = None
    # Which boundary the frontend pages between. Empty means the collection says
    # nothing and the player's own setting decides.
    paging_group: str = ""
    links: CollectionLinks


class CollectionList(ApiModel):
    collections: list[CollectionResource]


class CreateCollectionRequest(ApiModel):
    """Supplying `filters` makes a filter collection; supplying `games` (or neither)
    makes a manual one. Sending both is refused rather than guessed at."""

    name: str
    description: str = ""
    filters: CollectionFilters | None = None
    games: list[str] = Field(default_factory=list)
    # A collection to copy. Refused beside `description`, `filters` or `games`.
    copy_of: str | None = None


class PatchCollectionRequest(ApiModel):
    """What a collection is, changed in place. Every field is optional and only what is
    sent is written - a rename must not have to restate the criteria.

    `games` replaces the whole membership, in the order given, because the order *is*
    the membership for a manual collection. Refused on a filter collection, whose
    membership comes from its criteria.

    Sending `filters` turns a manual collection into a filter one, which discards a
    hand-picked list - so it is only ever explicit, never a side effect of another edit.
    """

    name: str | None = None
    # "" clears it, which is why this is not a bare falsy check on the way in.
    description: str | None = None
    image: str | None = None
    in_frontend: bool | None = None
    filters: CollectionFilters | None = None
    # Refused beside `filters`.
    clear_filters: bool = False
    games: list[str] | None = None
    limit: int | None = None
    # Absent leaves the cap alone; `limit: null` cannot say "lift it" because absent and
    # null are the same thing over JSON. This says it in a word.
    clear_limit: bool = False
    # How the list is handed out. Settable here for every collection, not only through
    # a filter block: a manual collection has an order too, and until this existed the
    # only way to give it one was to arrange it, so `Title` was unreachable for it.
    # `manual` means the stored member array and is refused where there is not one.
    order_by: str | None = None
    direction: str | None = None
    paging_group: str | None = None


class PreviewRequest(ApiModel):
    """Criteria to resolve against the library without storing anything.

    What an unsaved rule is: `builtin:all` plus these criteria, which is the same
    resolve every saved collection gets. Without it the only way to see what a rule
    would match is to save it first, which makes every experiment live to whoever is
    playing.
    """

    filters: CollectionFilters | None = None
    limit: int | None = None


class MemberTable(ApiModel):
    """One table of a member game, and whether this collection holds it.

    `origin` says which rule put it in or kept it out: `named` (a member names exactly
    this table), `default` (the member names the game and this is what it resolves to),
    `excluded`, `hidden`, or `missing` where the ref names a table the library does not
    have.
    """

    id: str
    # The fields a table is named from, not a name built here. One formatter, and it
    # is the client's: a label computed on both sides is two that can disagree, which
    # is how four surfaces ended up saying the same table four ways.
    version: str = ""
    authors: list[str] = Field(default_factory=list)
    filename: str = ""
    included: bool = True
    origin: str = ""


class CollectionMember(ApiModel):
    """One line of a collection's stored membership, with why it is there.

    Not the resolved list - the *stored* one. A member naming a game that is no longer
    in the library resolves to nothing and vanishes from every other lens; here it is a
    row with `origin: "missing"`, which is what lets an editor offer to clean it up.

    `origin`: `named` (somebody put it here), `filter` (the criteria matched it),
    `excluded` (kept out), `missing` (named, but not in this library).
    """

    game: str
    name: str = ""
    origin: str = ""
    included: bool = True
    # The table *this row names*, empty when it names none. Not the table it resolves
    # to, which is under `tables` - a row naming no table still resolves to one. It is
    # the row's identity: without it a client cannot address one row of a game that has
    # several, and sending the resolved table back matches no ref at all.
    ref_table: str = ""
    tables: list[MemberTable] = Field(default_factory=list)
    # Held, and past the collection's limit: the frontend does not show it.
    past_limit: bool = False


class CollectionMemberList(ApiModel):
    """`playable` counts the members that resolve to something launchable, which is
    what the collection actually hands out."""

    collection: str
    count: int
    playable: int
    members: list[CollectionMember]


class MembersPreviewRequest(ApiModel):
    """Rules to try in place of a collection's own. Absent tries it with none."""

    filters: CollectionFilters | None = None


class CollectionMemberPreview(CollectionMemberList):
    # Games the tried rules match in the library, whatever the collection does with them.
    matched: int = 0


class MemberRequest(ApiModel):
    """Which table a member or an exclusion names, if it names one.

    Absent or empty means the game: a member resolves to whichever table is its
    default, and an exclusion removes the game entire. Naming a table holds the
    collection to exactly that one.
    """

    table: str = ""
    # Where the new ref goes: directly after this game's ref naming this table, with
    # "" meaning the one that names none. Absent appends, which is what every caller
    # did before this existed. A second table landing at the end of a long collection
    # is indistinguishable from nothing having happened.
    after_table: str | None = None


class MemberTableRequest(ApiModel):
    """Which table a member should name from now on.

    Empty `table` hands the game back its default, so the member follows a replacement
    again. `was` names the ref being changed, which matters only where a game appears
    more than once; empty means the ref that names no table.
    """

    table: str = ""
    was: str = ""


class CollectionsArrangementRequest(ApiModel):
    """Every collection by name, in the order the frontend shows them."""

    order: list[str]


class CollectionOrderRequest(ApiModel):
    """The membership in the order it should be read.

    A whole list rather than per-item positions: atomic, and neither side does index
    arithmetic. Every id must already be a member - reordering is not a way to add.
    """

    games: list[str]


# --- Jobs ------------------------------------------------------------------

class JobLinks(ApiModel):
    self_: str = Field(alias="self")
    events: str
    # Where a job that can be stopped is stopped.
    stop: str | None = None


class JobResource(ApiModel):
    """One run of slow work. `pct` and `message` are the last progress reported, so
    a client that connects late is correct without having seen the events. `error`
    is set only when state is `failed`; timestamps are epoch seconds."""

    id: str
    kind: str
    state: str
    pct: int
    message: str
    error: str | None
    started_at: float
    finished_at: float | None
    # What the work answered, for the jobs that produce one. Null while it is running,
    # and null afterwards for the ones whose outcome is the thing they changed - a
    # library scan leaves a scanned library and has nothing else to say.
    result: Any | None = None
    stoppable: bool = False
    # A person ended it early. `state` is still `done`: what it did before is kept.
    stopped: bool = False
    # How many of its parts failed. `state` is still `done`: the rest did.
    problems: int = 0
    # Whether its result was written to disk, to open later and after a restart. Only a
    # job that asked to be kept has one.
    report: bool = False
    # One line on what it did, counts mostly. Empty while it runs and for kinds with
    # nothing to count.
    detail: str = ""
    # What this run is called where its kind is shared by several operations, in the
    # install's language. Empty means the kind's own name.
    title: str = ""
    links: JobLinks


class UpdateCheck(ApiModel):
    """Whether a newer build is published, and whether this install can take it.

    `update_supported` is not the same question as `update_available`: a build that
    cannot replace itself - one run from source, or on a platform with no published
    asset - still wants to be told a newer version exists. `support_reason` says which
    case it is, so a client offers a link where it cannot offer a button.
    `update_available` is null where this build's own version does not read as one, so
    there is nothing to weigh the release against.
    """

    update_available: bool | None = False
    current_version: str | None = None
    latest_version: str | None = None
    update_supported: bool = False
    support_reason: str | None = None
    triplet: str | None = None
    asset_name: str | None = None
    # Set when the last attempt could not be made. Not knowing is its own answer and is
    # never reported as "no update".
    error: str | None = None
    # The last check that succeeded; None when none has.
    checked_at: str | None = None


class UpdateRequest(ApiModel):
    """`stop_table` is the caller saying their user was asked and answered. Default
    false, so a request that says nothing never takes a table away from somebody."""

    stop_table: bool = False


class UpdateStarted(ApiModel):
    """The update is staged and this install is going down to take it.

    Answered before the handoff completes, because completing it means this process is
    gone. A client's next request failing is the update working.
    """

    latest_version: str
    stopped_table: str | None = None


class JobList(ApiModel):
    jobs: list[JobResource]


class FilterAxis(ApiModel):
    """One thing a collection can filter on.

    `values` are the ones this library actually has, so a client offers a choice that
    matches something. `rating` carries its scale instead, the same on every install.
    """

    name: str
    scope: str
    kind: str
    # The name a reader sees. `name` is the stored key and never changes; this may be
    # reworded freely, and a client that derives its own from the key gets "Game type"
    # where the rest of the app says "Type".
    label: str = ""
    # The catalog key behind `label`, for a client that resolves its own words.
    # `label` is this install's language; the key is what never moves. Additive, which
    # is why it does not move the theme contract.
    label_key: str = ""
    summary: str
    # Whether a criterion on this axis may hold several values, which is an OR across
    # them. A client renders a multi-select from this rather than from a list of axis
    # names it holds itself, which is what keeps adding an axis free.
    many: bool = False
    values: list[str] | None = None
    # How many games hold each of `values`. Absent where the values are a scale.
    counts: dict[str, int] | None = None
    # The axis whose field this one is asked under, or "" where it is its own.
    field: str = ""


class FilterAxisList(ApiModel):
    axes: list[FilterAxis]


class AutoMatchRequest(ApiModel):
    game_ids: list[str]


class AutoMatchResult(ApiModel):
    """`changed` counts the games whose match moved, `unmatched` those still without one,
    and `yours` those left as they were because a person made or cleared their match.
    `skipped` counts the games left alone because their folder cannot be written to."""

    games: int = 0
    changed: int = 0
    unmatched: int = 0
    yours: int = 0
    skipped: int = 0


class MissingMediaRequest(ApiModel):
    """No `game_ids` is the whole library."""

    game_ids: list[str] | None = None


class MissingMediaKind(ApiModel):
    """`missing` counts the games with no file of this kind, and `available` those of
    them an enabled source publishes one for or the game's own files can supply, which
    `local` counts of them."""

    kind: str
    missing: int = 0
    available: int = 0
    local: int = 0


class MissingMedia(ApiModel):
    """Every kind the library keeps that an enabled source publishes, and every kind a
    game's own files can supply, a backglass picture from its backglass file. `unmatched`
    games have no VPS match, so nothing can be looked up for them, and nothing is
    counted available from a source in `unreachable`."""

    games: int = 0
    unmatched: int = 0
    sources: list[str] = []
    unreachable: list[str] = []
    kinds: list[MissingMediaKind] = []


class OfferedMedia(ApiModel):
    """The games whose slot for a kind is empty and an enabled source has a file for it,
    by media kind. A kind nothing can be fetched for is left out, and so is every kind when
    no source can be reached."""

    kinds: dict[str, list[str]] = {}


class GameKind(ApiModel):
    game_id: str
    kind: str


class GameFile(ApiModel):
    game_id: str
    path: str


class MediaFillRequest(ApiModel):
    """`slots` names exact game and kind pairs, and when sent the other two are not
    read. No `game_ids` is the whole library, and no `kinds` every kept kind."""

    game_ids: list[str] | None = None
    kinds: list[str] | None = None
    slots: list[GameKind] = []


class MediaRotateRequest(ApiModel):
    """`files` are media rows' own keys: a game and a path inside its folder. `turn` is
    `left`, `right` or `around`, and `keep` copies each file into the backups folder first."""

    files: list[GameFile]
    turn: str
    keep: bool = True


class ScanRequest(ApiModel):
    """Absent body means both default to true, which is what the Manager's own
    scan does."""

    download_media: bool = True
    update_all: bool = True


# --- Manufacturers ---------------------------------------------------------

class ManufacturerEntry(ApiModel):
    """One manufacturer string and what the logo lookup does with it. `slug` is
    the filename stem the name computes; `aliased_to` is the effective alias
    redirecting it, or null; `logo` is the /assets/ web path that resolves, or
    null; `tables` counts library tables carrying exactly this string."""

    name: str
    slug: str
    aliased_to: str | None
    logo: str | None
    games: int


class ManufacturerList(ApiModel):
    manufacturers: list[ManufacturerEntry]


# --- Launch ----------------------------------------------------------------

class RatingRequest(ApiModel):
    """0-5, where 0 means unrated. A value outside that is refused rather than
    clamped: silently storing 5 for a caller that sent 9 hides its bug."""

    rating: int = Field(ge=0, le=5)


class Rating(ApiModel):
    rating: int


class FavoriteRequest(ApiModel):
    favorite: bool


class TagsRequest(ApiModel):
    tags: list[str]


class TagMerge(ApiModel):
    """`sources` fold into `into`. Renaming is one source into a name nothing uses."""

    sources: list[str]
    into: str


class OwnedRequest(ApiModel):
    """`keys` says whose ids these are, `vps` or `vpinfe`. `kind` says `game` or `table`;
    left out, a VPS id may be either."""

    ids: list[str]
    keys: Literal["vps", "vpinfe"] = "vps"
    kind: Literal["", "game", "table"] = ""


class Owned(ApiModel):
    """`table_id` is empty where the id named a game rather than one of its tables."""

    game_id: str
    table_id: str = ""
    name: str = ""


class OtherVersion(Owned):
    """The table held where another version of the release was asked about. `version`
    is that table's; `url` is where the release asked about is published."""

    version: str = ""
    url: str = ""


class Known(ApiModel):
    """What VPSdb calls an id the library does not hold. A game has a `manufacturer` and
    a `year`, a table a `version` and its `authors`."""

    name: str = ""
    manufacturer: str = ""
    year: str = ""
    version: str = ""
    authors: list[str] = []


class OwnedMap(ApiModel):
    owned: dict[str, Owned]
    other_versions: dict[str, OtherVersion] = {}
    known: dict[str, Known] = {}


class TagSource(ApiModel):
    """A Community list that derives a tag. `stale` is a last read that failed, so the
    tag still stands on what the one before it found."""

    extension: str
    display_name: str = ""
    list: str
    title: str = ""
    read_at: str = ""
    stale: bool = False
    error: str = ""


class TagResource(ApiModel):
    """`color` is one of the names the palettes resolve; `chosen` is false where it was
    derived from the name because nobody picked one. `games` is 0 for a tag only written
    down."""

    name: str
    games: int = 0
    tables: int = 0
    description: str = ""
    color: str
    chosen: bool = False
    # Where an extension derives it. A tag with a source is that extension's to put on
    # and take off; its description and color are still the user's.
    sources: list[TagSource] = Field(default_factory=list)


class TagList(ApiModel):
    tags: list[TagResource]


class TagUpdate(ApiModel):
    """Left out is left alone. An empty `color` goes back to the derived one."""

    description: str | None = None
    color: str | None = None


class TagSweep(ApiModel):
    """How many games changed - not how many carried a source, since one that already
    held the survivor is not a change."""

    changed: int


class Tags(ApiModel):
    """What was stored, which is not always what was sent: each is trimmed and internal
    whitespace collapsed, and the set keeps the first spelling of a repeat."""

    tags: list[str]


class Favorite(ApiModel):
    favorite: bool


class PrivateRequest(ApiModel):
    private: bool


class Private(ApiModel):
    private: bool


class PrivateSelection(ApiModel):
    game_ids: list[str]
    private: bool


class PrivateSweep(ApiModel):
    """`games` counts the games named, and `changed` those that were not already so."""

    private: bool
    games: int = 0
    changed: int = 0


class MarkEdit(ApiModel):
    """The marks to set on one table, or on one game where `table_id` is left out. A field
    left out is not touched."""

    game_id: str
    table_id: str | None = None
    rating: int | None = Field(default=None, ge=0, le=5)
    tags: list[str] | None = None
    hidden: bool | None = None
    delete_nvram_on_close: bool | None = None
    launcher: str | None = None
    favorite: bool | None = None
    private: bool | None = None


class MarksRequest(ApiModel):
    edits: list[MarkEdit]


class LaunchRequest(ApiModel):
    """`file` picks one of the game's tables; absent means the default."""

    file: str | None = None


class LaunchLinks(ApiModel):
    state: str
    events: str


class LaunchAccepted(ApiModel):
    """202: the launch is under way, not finished - watch /events for the rest."""

    launching: bool
    game_id: str
    file: str
    links: LaunchLinks


# --- Uploads ---------------------------------------------------------------

class UploadBegun(ApiModel):
    id: str


class UploadFromPath(ApiModel):
    path: str


class UploadSummary(ApiModel):
    file_count: int
    total_bytes: int


class Acknowledged(ApiModel):
    ok: bool


class FileStored(ApiModel):
    bytes: int


class DetectedAssetInfo(ApiModel):
    kind: str
    label: str
    # The catalog key behind `label`, for a client that resolves its own words.
    # `label` is this install's language; the key is what never moves. Additive, which
    # is why it does not move the theme contract.
    label_key: str = ""
    media_kind: str
    root: str
    size: int
    detail: str
    preview: str = ""


class Analysis(ApiModel):
    """`unrecognized` lists source-relative paths no rule claimed; `bundle_info` is
    the parsed .info when the upload carried one."""

    source_kind: str
    source_name: str
    has_game: bool
    assets: list[DetectedAssetInfo]
    notes: list[str]
    error: str
    unrecognized: list[str]
    bundle_info: dict | None


class BlockedAsset(ApiModel):
    kind: str
    reason: str


class SwapFile(ApiModel):
    """A file named for the table being replaced. `brought` is set where the new file
    arrives with one of its own of that kind."""

    kind: str
    file: str
    carry: bool
    brought: bool


class SwapPlan(ApiModel):
    """`label` names the table by version and author, `patched` the tables made from it."""

    table: str
    filename: str
    label: str
    files: list[SwapFile]
    patched: list[str] = []


class ChoiceTable(ApiModel):
    id: str
    filename: str
    label: str
    default: bool


class ImportChoice(ApiModel):
    """What the confirmation offers a game that already has a table. `table` is the one
    `replace` is about; `tables` lists every table the game holds that can be replaced."""

    tables: list[ChoiceTable]
    replace: bool
    table: str


class PlanItem(ApiModel):
    """`index` addresses this item in the `selected` list of an import request."""

    index: int
    kind: str
    label: str
    # The catalog key behind `label`, for a client that resolves its own words.
    # `label` is this install's language; the key is what never moves. Additive, which
    # is why it does not move the theme contract.
    label_key: str = ""
    detail: str
    destination: str
    action: str
    default_enabled: bool
    size: int
    media_kind: str
    # What the incoming file is called, or a count where one asset is several files.
    name: str = ""
    # What this does to what is already there, empty where it replaces nothing. Answered
    # here because it is a question about the install's disk, which a client cannot see -
    # and it is the question somebody is really answering when they confirm.
    replaces: str = ""
    # The tables in the folder a patch made from the file this replaces, by filename.
    made_from_it: list[str] = []
    # Set on an item that replaces a table: the table, and each file named for it with
    # whether it goes with the new one. Null on every other item.
    swap: SwapPlan | None = None


class ImportPlanResource(ApiModel):
    game_dir: str
    new_game_dir_name: str
    rom_name: str
    items: list[PlanItem]
    blocked: list[BlockedAsset]
    choice: ImportChoice | None = None


class ImportReport(ApiModel):
    """vps_associated and vps_error report a post-import association that is allowed
    to fail without failing the import - the files are already on disk by then.
    vps_associated answers for a record the sender picked; vps_matched answers for
    one the import looked for itself. None means that step did not run."""

    imported: list[str]
    skipped: list[str]
    game_dir: str
    new_game: bool
    media_kinds: list[str]
    blocked: list[BlockedAsset]
    vps_associated: bool | None = None
    vps_matched: bool | None = None
    vps_error: str | None = None
    added_tables: list[str] = []
    patched_tables: list[str] = []
    replaced_tables: list[dict] = []


class DeclaredIdentity(ApiModel):
    """What the sender says one uploaded file is.

    Whatever delivered the bytes knows what it asked for, and re-deriving that from the
    bytes afterwards is the guess this exists to replace. Every field is optional: a
    caller that inferred an identity says nothing and the file joins the manual queue.

    `confirmed_by` is how the caller knows, not what it wants done about it. The accepted
    values are `declared` - it fetched the file from that record - and `user`, a person in
    that client picked it. There is no value meaning "I guessed", which is what stops a
    client asserting a confidence it did not earn.
    """

    vps_file_id: str = ""
    host_item_id: str = ""
    host: str = ""
    game_id: str = ""
    table_id: str = ""
    confirmed_by: str = ""


class PlanRequest(ApiModel):
    """Every field optional: an empty body plans the upload as it stands."""

    vps_id: str = ""
    game_dir: str = ""
    rom_name: str = ""
    allow_new_game: bool = False
    # Set where the drop named a slot rather than a game. The slot decides the media
    # key, so nothing is read off the filename: any image works on an image slot and
    # is written under that slot's own name. Needs `game_dir`, and refuses more than
    # one file - a slot holds one thing.
    media_kind: str = ""
    asset_kind: str = ""
    # A one-time destination for a new game, naming a location. Left out, it goes to the
    # one marked for new games - and if that one cannot be written to, the import is
    # refused rather than quietly landing somewhere else.
    location_id: str = ""
    # Needs `game_dir`. The drop's table is one more for that game rather than its
    # replacement; a filename the game already has comes back blocked.
    add_table: bool = False
    # Without `add_table`, the table the drop takes the place of. Left out, the game's
    # default table. A game with no table of its own gets the drop as one more.
    replace_table: str = ""
    # The kinds of file named for the replaced table that go with the new one. Left out,
    # the defaults for an update, or for another release with `instead`.
    carry: list[str] | None = None
    instead: bool = False
    # A new game's folder. Omitted falls back to the VPS-derived name, then the vpx stem.
    new_game_dir_name: str | None = None


class ImportRequest(PlanRequest):
    """`selected` picks plan items by index; omitted means the plan's own defaults.

    `declared` is keyed by the uploaded file's name, because a bundle carrying a .vpx, a
    backglass and a ROM is one VPS game and three VPS file records - the binding cannot
    live on the session the way `vps_id` does.
    """

    selected: list[int] | None = None
    declared: dict[str, DeclaredIdentity] | None = None


# --- VPS -------------------------------------------------------------------

class VpsHeldFile(ApiModel):
    """A file the game holds, by its name on disk; a kind held as a folder, by the folder's
    usual name. `vps_file_id` is the catalog record it is bound to, empty where nobody has
    said which it is."""

    name: str
    vps_file_id: str = ""


class VpsKindState(ApiModel):
    """One kind of file, for one game: what we hold and what the catalog lists.

    `obtainable` counts the records pointing at a file rather than a page, and says
    the catalog lists one - never that it is yours to take. A host can hold something
    this account may not see, and nothing here can tell without asking it.

    `why_not` names what the rest are, so a consumer can say "a folder to browse"
    rather than reporting them as missing downloads.
    """

    kind: str
    ours: list[str] = []
    # Which inventory answers whether one is here - `asset` or `media`. On the wire
    # because the two vocabularies share names for different things: `backglass` is a
    # .directb2s among assets and a picture among media, and VPS lists the first.
    held_in: str = ""
    held: bool = False
    # What `held` is made of: each file, or the folder for a kind kept as one.
    files: list[VpsHeldFile] = []
    # Whether anything here is bound to one of this kind's records.
    identified: bool = False
    listed: int = 0
    obtainable: int = 0
    why_not: list[str] = []


class VpsState(ApiModel):
    """State, not findings: which of it is worth surfacing is the consumer's call, made
    with the library in front of it."""

    matched: bool = False
    vps_id: str = ""
    kinds: list[VpsKindState] = []
    # The game's tables, which `kinds` leaves out: a table is what VPS calls the game.
    tables: list[VpsHeldFile] = []


class DismissedRow(ApiModel):
    row: str
    ids: list[str] = []


class Dismissals(ApiModel):
    """The Overview rows set aside on this install, each with the ids it counted when it
    was. A row absent from the list is not dismissed."""

    rows: list[DismissedRow] = []


class DismissalRequest(ApiModel):
    """What the row is dismissed over, replacing what it was. No ids brings it back."""

    ids: list[str] = []


class LibraryVpsKindTally(ApiModel):
    """One kind, counted in games rather than files. Games, because the question the
    rollup exists to answer is about the collection: a kind held by no game at all is
    a category the collector never engaged with, not a gap in one."""

    kind: str
    ours: list[str] = []
    held_in: str = ""
    holding: int = 0
    identified: int = 0
    listed: int = 0
    obtainable: int = 0


class LibraryVpsState(ApiModel):
    """The rollup, as last compared. `computed` empty means never - which is not the
    same as every count being zero, and reading it as such would report a library
    nobody has looked at as one holding nothing."""

    computed: str = ""
    games: int = 0
    matched: int = 0
    kinds: list[LibraryVpsKindTally] = []


class DiscoveryPerson(ApiModel):
    """One author, once. `aliases` are the other spellings and credits VPS files the same
    work under, kept so a search for any of them finds the person."""

    name: str
    aliases: list[str] = []
    releases: int = 0


class DiscoveryRelease(ApiModel):
    """One release VPS lists. `by` is positions in the response's `people`, `added` is when
    VPS added it (empty where it does not say), `format` is a table's program and empty on
    other kinds, `version` is the one VPS lists, and `held` says a file the library holds is
    bound to it."""

    id: str
    by: list[int] = []
    mod: bool = False
    features: list[str] = []
    format: str = ""
    version: str = ""
    added: str = ""
    held: bool = False


class DiscoveryRow(ApiModel):
    """One catalog entry. `listed` and `held` use the catalog's own keys (`b2sFiles`,
    `tableFiles`), so a kind VPS lists and the library lacks is `listed` and not `held`.
    `in_library` null means the library has not been compared yet. `releases` is every
    release the entry lists, under the same keys."""

    vps_id: str
    name: str | None = None
    manufacturer: str | None = None
    year: int | str | None = None
    type: str | None = None
    themes: list[str] = []
    url: str = ""
    img_url: str = ""
    listed: list[str] = []
    in_library: bool | None = None
    held: list[str] = []
    releases: dict[str, list[DiscoveryRelease]] = {}


class LibraryDiscovery(ApiModel):
    counted: str = ""
    people: list[DiscoveryPerson] = []
    rows: list[DiscoveryRow] = []


class HomeRelease(ApiModel):
    """A release on the New shelf. `was` is the version it held before VPS last changed it,
    which is what a release VPS dated again says it was; empty for one that never changed."""

    vps_id: str
    game: str
    img_url: str = ""
    in_library: bool = False
    listing: str
    id: str
    added: str = ""
    version: str = ""
    was: str = ""
    held: bool = False
    mod: bool = False
    authors: list[str] = []


class HomeNew(ApiModel):
    """How many releases a chip holds, and the newest of them."""

    count: int = 0
    releases: list[HomeRelease] = []


class HomeAuthor(ApiModel):
    """An author whose work the library holds: how many of their releases it holds, and how
    many of theirs are on the New shelf."""

    name: str
    held: int = 0
    new: int = 0


class GapTake(ApiModel):
    """The file that fills a game's empty slot, named as `POST /games/{id}/media/{kind}/fetch`
    takes it: the media `kind`, the `source` and its `size`."""

    kind: str
    source: str
    size: str = ""


class HomeGap(ApiModel):
    """A game the library holds, and what there is of the kind it lacks: `vps` releases VPS
    lists, `source` files a source offers; at least one is above 0."""

    vps_id: str
    name: str
    img_url: str = ""
    vps: int = 0
    source: int = 0
    # The library's own id for the game, set where a source offers a file for it.
    game_id: str = ""
    # The address of the picture a source offers for the slot, where the kind is a picture.
    picture: str = ""
    # What taking it is, or None where the slot cannot be filled by taking it.
    take: GapTake | None = None


class PictureChange(ApiModel):
    """A picture VPinFE placed that its source has since replaced. `kind` is the media slot,
    `md5` the hash of the replacement, and `id` what the picture is marked under: Ignore
    Updates is an `updates` mark on it, Keep Mine a `version` mark on it with `md5` as its
    detail."""

    id: str
    game_id: str
    vps_id: str
    name: str
    kind: str
    source: str
    size: str = ""
    md5: str


class PictureChanges(ApiModel):
    """The pictures the last daily check found newer, less what has changed or been set aside
    since. `checked_at` is when that check wrote its answer, empty if it never has. Empty
    `pictures` while the replacement is automatic."""

    checked_at: str = ""
    pictures: list[PictureChange] = []


class HomeGaps(ApiModel):
    """`count` games lack the kind, `games` the ones shown, `game_ids` every one of them by the
    library's id (what See All opens) and `on_vps` how many VPS lists."""

    count: int = 0
    on_vps: int = 0
    game_ids: list[str] = []
    games: list[HomeGap] = []


class HomeMissingCount(ApiModel):
    """One tab of `DiscoveryMissing`: `HomeGaps` without the rows to draw."""

    count: int = 0
    on_vps: int = 0
    game_ids: list[str] = []


class DiscoveryMissing(ApiModel):
    """Home's `missing`, by tab, as counts and ids alone. `counted` empty means the library
    has never been compared."""

    counted: str = ""
    missing: dict[str, HomeMissingCount] = {}
    # Per kind, the ids of the games a source can fill the slot of, as `/library/media/offered`.
    offered: dict[str, list[str]] = {}


class HomeWantedItem(ApiModel):
    """A game or a release marked Wanted. `scope` is `game` or `release`; `vps_id` is always
    the entry and `id` the thing itself. A game carries no `listing`, `version` or
    `authors`."""

    scope: str
    id: str
    vps_id: str
    game: str
    img_url: str = ""
    in_library: bool = False
    listing: str = ""
    version: str = ""
    authors: list[str] = []
    held: bool = False
    since: str = ""


class HomeWanted(ApiModel):
    count: int = 0
    items: list[HomeWantedItem] = []


class HomeGame(ApiModel):
    """A game on the Games You Don't Have shelf. `authors` is how many people made a table for
    it, counted by the author each release names first."""

    vps_id: str
    game: str
    img_url: str = ""
    manufacturer: str = ""
    year: str = ""
    authors: int = 0


class HomeGames(ApiModel):
    """`count` games, the ones with the most `authors` first, and `games` the first twelve."""

    count: int = 0
    games: list[HomeGame] = []


class HomeHeld(ApiModel):
    """A game of the library that shares a title with a `HomeFamilyGame`."""

    vps_id: str
    game: str
    manufacturer: str = ""
    year: str = ""


class HomeFamilyGame(HomeGame):
    """A game on the Other Versions of Your Games shelf. `held` is the entries of the same
    title the library holds, the oldest first."""

    held: list[HomeHeld] = []


class HomeFamily(ApiModel):
    """`count` games, the ones with the most `authors` first, and `games` the first twelve."""

    count: int = 0
    games: list[HomeFamilyGame] = []


class HomeDuplicate(ApiModel):
    """An entry on the Library Health shelf that two or more games of the library are matched
    to. `game_ids` are those games, in the library's order."""

    vps_id: str
    game: str
    img_url: str = ""
    manufacturer: str = ""
    year: str = ""
    game_ids: list[str] = []


class HomeDuplicates(ApiModel):
    """`count` entries, the ones with the most games first, and `games` the first twelve."""

    count: int = 0
    games: list[HomeDuplicate] = []


class HomeBare(ApiModel):
    """A game of the library with no table. `vps_id` is empty for a game matched to no entry,
    and the picture, maker and year are then too."""

    game_id: str
    vps_id: str = ""
    game: str
    img_url: str = ""
    manufacturer: str = ""
    year: str = ""


class HomeBares(ApiModel):
    """`count` games, by name, and `games` the first twelve."""

    count: int = 0
    games: list[HomeBare] = []


class HomeHealth(ApiModel):
    """The games of the library that need a look: entries two or more games are matched to,
    and games with no table at all."""

    duplicates: HomeDuplicates = HomeDuplicates()
    no_tables: HomeBares = HomeBares()


class HomeOpenedGame(ApiModel):
    """A game on the Opened but Not Imported shelf. `at` is when its release page was last
    opened, as an ISO time."""

    vps_id: str
    game: str
    img_url: str = ""
    manufacturer: str = ""
    year: str = ""
    at: str


class HomeOpened(ApiModel):
    """`count` games, the one opened last first, and `games` all of them."""

    count: int = 0
    games: list[HomeOpenedGame] = []


class HomeLikely(ApiModel):
    """The VPS entry a `HomeUnmatchedGame` is likely to be."""

    vps_id: str
    game: str
    img_url: str = ""
    manufacturer: str = ""
    year: str = ""


class HomeUnmatchedGame(ApiModel):
    """A game of the library with no VPS match, by `game_id`, and the entry it is likely to
    be: the one of the same title (accents, case, punctuation and a parenthesised part
    ignored) listed in the game's year or a year either side."""

    game_id: str
    game: str
    manufacturer: str = ""
    year: str = ""
    suggestion: HomeLikely


class HomeUnmatched(ApiModel):
    """`count` games with a likely entry, by name, and `games` the first twelve."""

    count: int = 0
    games: list[HomeUnmatchedGame] = []


class HomeUpdateMove(ApiModel):
    """One thing of a game that has a later version. `source` is `vps` for a release the
    library's file is tied to, which VPS now lists at `now` where it listed `then` when the
    tie was made, and the other source's id for what it says is later (`release_id` and
    `then` are then empty, and `now` is its version where it names one). `kind` is the VPS
    listing for `vps` and the kind of file the other source speaks of. `file` is the file
    the tie is on, and for another source the folder of the game. `md5` is the hash of the
    replacing picture where the source has replaced one of the game's pictures; `kind` is then
    the media kind."""

    source: str
    kind: str
    release_id: str = ""
    vps_id: str = ""
    file: str = ""
    then: str = ""
    now: str = ""
    md5: str = ""


class HomeUpdate(ApiModel):
    """A game of the library with a later version of something it holds, once however many
    of its files moved. `vps_id` is empty where the game is matched to no entry and its moves
    name none."""

    game_id: str
    vps_id: str = ""
    game: str
    img_url: str = ""
    moves: list[HomeUpdateMove] = []


class HomeUpdates(ApiModel):
    """`count` games have an update, `games` the ones shown and `game_ids` every one of them
    by the library's id (what See All opens)."""

    count: int = 0
    game_ids: list[str] = []
    games: list[HomeUpdate] = []


class DiscoveryHome(ApiModel):
    """Every shelf of Discovery's front page: the VPS shelves from the last comparison, the
    media a source can fill from the library as it is now and the index already held.

    `new` is keyed by chip: `all`, `yours` (games in the library) and each VPS kind with a
    release in the window. `missing` is keyed by what a game can lack: a VPS kind, which also
    holds the games an enabled source has a file for of the matching media kind, or a media kind
    (`playfield`) with no VPS kind of its own. `dont_have` is the games VPS lists that the
    library has none of, the ones the most people made a table for first. `counted` empty means
    the library has never been compared, so no release reads as held, no game as lacking and
    none as one you do not have."""

    counted: str = ""
    days: int
    since: str
    new: dict[str, HomeNew] = {}
    authors: list[HomeAuthor] = []
    missing: dict[str, HomeGaps] = {}
    wanted: HomeWanted = HomeWanted()
    dont_have: HomeGames = HomeGames()
    family: HomeFamily = HomeFamily()
    health: HomeHealth = HomeHealth()
    opened: HomeOpened = HomeOpened()
    unmatched: HomeUnmatched = HomeUnmatched()
    updates: HomeUpdates = HomeUpdates()


class DiscoveryMark(ApiModel):
    """One thing marked Wanted or Ignored. `scope` is `game` or `release` (the entry's id or
    the release's), `gap` (the entry's id and, as `detail`, the kind it is not missing),
    `updates` (a release) or `version` (a release and, as `detail`, the version skipped).
    `since` is when it was marked."""

    state: str
    scope: str
    id: str
    detail: str = ""
    since: str = ""


class DiscoveryMarks(ApiModel):
    """Every mark on this install, the newest first. `replaced` is the state the thing a
    change was made to held before it, empty if it held none."""

    marks: list[DiscoveryMark] = []
    replaced: str = ""


class DiscoveryMarkRequest(ApiModel):
    """The state to put on a thing. Only a game or a release can be Wanted. `detail` is the
    kind of a `gap` or the version of a `version`, and is empty for the rest."""

    state: str
    scope: str
    id: str
    detail: str = ""


class VpsFieldDiff(ApiModel):
    """One detail the game and its entry disagree about, both sides as one line each -
    a year is a number and themes are a list, and a comparison wants neither shape.

    `new` means the record says nothing and the entry does, so taking it fills a gap
    rather than replacing what the library already shows.
    """

    field: str
    ours: str
    theirs: str
    new: bool = False


class VpsAdoptRequest(ApiModel):
    """Which details to take. Empty takes every one that differs."""

    fields: list[str] = Field(default_factory=list)


class VpsDetails(ApiModel):
    """Empty for a game whose details came from the entry it is still matched to,
    which is every game nobody has re-matched: the details were written from the entry,
    so they agree with it by construction."""

    differs: list[VpsFieldDiff] = []


class VpsSearchResult(ApiModel):
    """Straight off a VPSdb entry, so every field is as optional as that data is."""

    vps_id: str | None
    name: str | None
    manufacturer: str | None
    year: int | str | None
    type: str | None
    folder_name: str
    # How many builds exist for this machine. Context for choosing which machine you
    # have, not a choice in itself - the release is a later question.
    releases: int = 0
    # The picture VPS's own page leads with for this machine, blank where there is none.
    img_url: str = ""
    # The entry on VPS, so a surface can link out rather than making somebody search
    # for what it has already identified.
    url: str = ""


class VpsSearchResults(ApiModel):
    results: list[VpsSearchResult]


class VpsSyncState(ApiModel):
    """How fresh the local catalog is. `checked` is when it was last asked, which is not
    when it last changed - asking is cheap and the answer is usually "no". `entries` is
    how many games it holds, and 0 means there is no catalog to answer from."""

    schedule: str = "daily"
    checked: str = ""
    due: bool = False
    entries: int = 0


class VpsSyncResult(ApiModel):
    """`checked` false means the schedule said not yet; it is not a failure. `changed`
    is the one a surface reports, because a check that found nothing new is the ordinary
    outcome and worth saying quietly."""

    checked: bool = False
    ok: bool = False
    changed: bool = False
    version: str = ""
    at: str = ""
    reason: str = ""


class VpsLink(ApiModel):
    """One address VPSdb gives for a release. `broken` is VPSdb's own mark that it no
    longer works."""

    url: str
    broken: bool = False


class VpsRelease(ApiModel):
    """One release for a game, as VPSdb lists it.

    Every field is as optional as the catalog is. `img_url` is the exception worth
    naming: it is present on nearly every release, so a surface over releases can lay
    out around having a picture.
    """

    vps_file_id: str
    version: str = ""
    authors: list[str] = []
    format: str = ""
    features: list[str] = []
    comment: str = ""
    img_url: str = ""
    updated_at: str = ""
    # The first link not marked broken, or the first there is where all are. Blank where VPS
    # gives none.
    url: str = ""
    urls: list[VpsLink] = []
    mod_of: ModOf | None = None


class VpsReleases(ApiModel):
    releases: list[VpsRelease]


# --- Signing in ------------------------------------------------------------

class SignInAsk(ApiModel):
    """A code was asked for. Its digits are on this device's screen, never in this answer."""

    request_id: str
    expires_in: int
    can_ask_again_in: int


class SignInAttempt(ApiModel):
    request_id: str
    code: str


class SignedIn(ApiModel):
    signed_in: bool = True


class WhoAmI(ApiModel):
    """`local` is this device itself, which is never asked to sign in."""

    signed_in: bool
    local: bool
    kind: str = ""
    session_id: str = ""
    confirmed: bool = False


class SessionInfo(ApiModel):
    id: str
    kind: str
    address: str
    agent: str
    created: float
    last_used: float
    current: bool
    via: str = ""


class SessionList(ApiModel):
    sessions: list[SessionInfo]


class SessionsEnded(ApiModel):
    ended: int


class ConfirmAsk(ApiModel):
    """A Confirm Code was asked for. Its digits are on this device's screen."""

    expires_in: int


class ConfirmAttempt(ApiModel):
    code: str


class ConfirmState(ApiModel):
    """Whether the caller may do Protected things now, and for how much longer."""

    confirmed: bool
    expires_in: int = 0


class AuthenticatorState(ApiModel):
    """`screen_codes` is whether this device shows a sign-in code to a browser that asks."""

    enabled: bool
    screen_codes: bool


class AuthenticatorEnrolment(ApiModel):
    """What an authenticator app needs: `secret` to type, `uri` for a QR."""

    secret: str
    uri: str


class AuthenticatorCode(ApiModel):
    code: str = Field(max_length=16)


class ScreenCodes(ApiModel):
    on: bool


class TokenMake(ApiModel):
    name: str
    access: str
    can_power: bool = False


class TokenInfo(ApiModel):
    id: str
    name: str
    access: str
    can_power: bool
    created: float
    last_used: float


class TokenList(ApiModel):
    tokens: list[TokenInfo]


class TokenMade(ApiModel):
    """The secret is in this answer and nowhere else, ever."""

    secret: str
    token: TokenInfo


class AskMake(ApiModel):
    """The one request a program wants to make. `path` is relative to the API, with any
    query, and `body` is the JSON the request will carry, or null."""

    method: str
    path: str
    body: Any = None


class AskInfo(ApiModel):
    """`state` is waiting, approved, declined, used or lapsed. Repeat the request with the
    id in `X-VPinFE-Approval` once it is approved."""

    id: str
    token: str
    method: str
    path: str
    body: Any = None
    state: str
    expires_in: int


class AskList(ApiModel):
    asks: list[AskInfo]


class TokenChange(ApiModel):
    can_power: bool


class TokensEnded(ApiModel):
    ended: int


class ConnectionEntry(ApiModel):
    """One address for Trusted Proxies, or one range for Trusted Networks."""

    value: str


class CertificateChoice(ApiModel):
    certfile: str
    keyfile: str


class EncryptedConnections(ApiModel):
    """The two lists, the certificate chosen, and whether this run answers HTTPS.

    `problem` is why a chosen certificate was not used and this run answers plain HTTP.
    `encrypted` is whether the request being answered arrived inside an encrypted
    connection."""

    proxies: list[str]
    networks: list[str]
    certfile: str = ""
    keyfile: str = ""
    https: bool = False
    problem: str = ""
    encrypted: bool = False


class PairAsk(ApiModel):
    """A pairing code is on this device's screen."""

    request_id: str
    expires_in: int


class PairAskBody(ApiModel):
    """What the asking install says about itself: the name it is configured with."""

    name: str = ""


class PairAttempt(ApiModel):
    request_id: str
    code: str
    name: str = ""


class Paired(ApiModel):
    """The token this install issued the one that paired. Shown once."""

    secret: str


class RemoteCodeRenewed(ApiModel):
    ended: int
