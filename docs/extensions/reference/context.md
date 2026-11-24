# The context

`ctx` is the whole of an extension's reach.  There is no way to get from it to the application,
and the rest of the model rests on that.

| on `ctx` | what it is |
|---|---|
| `ctx.name`, `ctx.manifest`, `ctx.host_version`, `ctx.logger`, `ctx.open`, `ctx.routers` | What this extension is, which VPinFE runs it, its log, and its own routes |
| `ctx.t`, `ctx.why`, `ctx.reaches`, `ctx.add_router` | Words, a failure in words, the tiers it may use, and an API of its own |
| `ctx.config` | `get`, `all`, `set` over its own settings |
| `ctx.events` | `subscribe` to a core event, `publish` one of its own |
| `ctx.files` | The folders it works from |
| `ctx.jobs` | Slow work, run the way core runs it |
| `ctx.os` | What an operating system's helper does for it, one fixed method at a time |
| `ctx.system` | Restart VPinFE, reboot or power off this device |
| `ctx.hardware` | The USB, serial and display devices attached to this device |
| `ctx.ui` | What it offers a person: settings, lists, actions, an account, what it holds |
| `ctx.players` | Who plays here, and the accounts it holds for them |
| `ctx.entries` | Something added to every entry a theme is handed |
| `ctx.catalogs` | Where a game, a table or a file is somewhere else |
| `ctx.tokens` | A name a user may write into a command |
| `ctx.games` | The library, and everything core offers by name |
| `ctx.uploads` | Importing files, the way a drop does |
| `ctx.sources` | Somewhere online Add and Replace can get a file from |
| `ctx.collections` | Collections: read them, make them, fill them, put them in order |
| `ctx.apps` | A way to play a table |

Every call that needs a capability or a scope the manifest did not declare raises
`ContractError`, from `common.extensions.contract`.  During `register` that fails the extension
(see [When one breaks](running.md#when-one-breaks)).

An extension imports `common.extensions.contract` and nothing else of VPinFE's.  It holds
`Unavailable`, `ContractError`, `words(name)` and `why(exc, at="")`.  The last two are `ctx.t`
and `ctx.why` for a module that is not handed `ctx`.

## The extension itself

### `ctx.name`

The manifest's `name`, a string.  It is also the Python package name and the URL segment.

```python
ctx.logger.info("%s is registering", ctx.name)
```

### `ctx.manifest`

The parsed manifest, frozen: `name`, `display_name`, `version`, `description`,
`requires_platform`, `scopes`, `capabilities`, `requires_features`, `platforms`, `events`,
`tiers`, `play`.  The lists are tuples.

```python
if "net:outbound" in ctx.manifest.capabilities:
    ctx.logger.debug("version %s may reach the network", ctx.manifest.version)
```

### `ctx.host_version`

The version of the VPinFE running the extension, as a string.

```python
headers = {"User-Agent": f"{ctx.name}/{ctx.manifest.version} VPinFE/{ctx.host_version}"}
```

### `ctx.logger`

A `logging.Logger` named `vpinfe.ext.<name>`.  See [Logs](running.md#logs).

```python
ctx.logger.warning("score server answered slowly: %s seconds", 4)
```

### `ctx.open`

True while `register` runs and False after.  You rarely need to branch on it.  It is why a
router added later is refused.

```python
assert ctx.open
```

### `ctx.routers`

The routers added so far, as `(router, scope)` pairs, the scope being `ext:<name>:<tier>`.  Core reads it after `register`, and an
extension has no reason to touch it.  Use `ctx.add_router`.

```python
ctx.add_router(router)
assert (router, "ext:example_scores:library") in ctx.routers
```

### `ctx.t`

`ctx.t(key, /, **params) -> str`.  Required: `key`.

What the extension's own `i18n/<language>.json` says for `key`, in the language now set.  Slots
in braces are filled from `params`, and a plural entry is chosen by `count`.

```python
line = ctx.t("sent", count=3)    # "3 scores sent"
```

The user sees the sentence in their language.  A key the catalog lacks answers the full key,
`ext.<name>.<key>`, so the gap is visible and `--check-extension` reports it.  See
[Words](words.md).

### `ctx.why`

`ctx.why(exc, /, at="") -> str`.  Required: `exc`.

Why something failed, in the words core uses under its own failures: a missing file, a refused
permission, a host that did not answer.  For an exception core has no words for, you get the
exception's own text, so word that one yourself.  `at` is the URL or path being reached, for an
exception that does not carry it, such as the standard library's `URLError`.

```python
try:
    urllib.request.urlopen(address, timeout=10)
except (urllib.error.URLError, TimeoutError) as exc:
    raise Unavailable(ctx.why(exc, at=address)) from exc
```

The user sees a line like `scores.example.com did not answer in time`.  It does not raise.

### `ctx.reaches`

A tuple of the tiers the extension may use, lowest first.  `library` is always in it,
`protected` is in it when the manifest's `tiers` lists it, and `play` is in it when the
manifest's `play` names at least one surface.

```python
if "play" in ctx.reaches:
    ctx.ui.section("scores", read_scores, tier=PLAY)
```

The manifest names the section under `play`: `"play": ["section:scores"]`.

Nothing is drawn.  A list, section, group, action, verb or router at a tier that is not in it
raises `ContractError` when it is declared, and an act marked `@tier(...)` at one is refused
when it is called.  See [Scopes](scopes.md).

### `ctx.add_router`

`ctx.add_router(router, *, tier=LIBRARY)`.  Required: `router`.

Serves routes of the extension's own under `/api/v1/ext/<name>/`.  Core attaches the gate, and
the extension cannot choose to have none.  `tier` is `PLAY`, `LIBRARY` or `PROTECTED`, from
`common.extensions.contract`, and says who may call the routes: `PLAY` also a Remote pass or a
Play token, `LIBRARY` an Owner or a Full token, `PROTECTED` only an Owner with a Confirm.  It
says Library when it says nothing.  Use it for an API that is the extension's own, such as a
route a theme calls or a picture the service serves.  Everything declared through `ctx.ui` is
served by core and needs no router.

```python
from fastapi import APIRouter

from common.extensions.contract import PLAY

router = APIRouter()


@router.get("/hello")
def hello() -> dict:
    return {"name": ctx.name}


ctx.add_router(router, tier=PLAY)
```

Nothing is drawn.  A theme or another program calls `GET /api/v1/ext/<name>/hello`.  It raises
`ContractError` if called after `register` returned, or if `tier` is not one the manifest's
`tiers` allows.  A router at `PLAY` needs `"router"` in the manifest's `play`.

Routers mount once, at startup.  An extension that adds routers and is switched on after the
run began shows "Starts at the next restart" rather than starting.  See
[Switching one on or off](running.md#switching-one-on-or-off).

## `ctx.config`

An extension's settings, under its own name.  See [Config](running.md#config) for where they
are kept.

### `ctx.config.get`

`ctx.config.get(key, default="") -> str`.  Required: `key`.

What is stored under `key`.  Where that is empty, you get the default the setting declares, and
where there is neither, `default`.  Always a string: a switch is `"true"` or `"false"`, a number
is its digits, and a multi is a JSON list that you read with `ctx.config.get_list`.

```python
address = ctx.config.get("server")
retries = int(ctx.config.get("retries", "3"))
```

The user sees the value they typed in the extension's settings.  Does not raise.

### `ctx.config.get_list`

`ctx.config.get_list(key) -> list[str]`.  Required: `key`.

The keys selected in a `multi` setting, in the order they were selected.  Where none has been saved
you get the setting's declared default, and `[]` where that is empty.

```python
regions = ctx.config.get_list("regions")
```

Does not raise.

### `ctx.config.all`

`ctx.config.all() -> dict[str, str]`.

Everything stored, as strings, secrets included.  Settings never written are absent, and
defaults are not mixed in.

```python
held = ctx.config.all()
ctx.logger.debug("keys set: %s", sorted(held))
```

Does not raise.

### `ctx.config.set`

`ctx.config.set(key, value)`.  Required: `key`, `value`.

Stores `value` under `key`.  The key need not be a declared setting.  One that is not declared
is kept but never drawn, and `accounts` and `declared` are reserved names.  Values are text.

```python
ctx.config.set("last_week", "42")
```

The user sees the new value if the key is a declared setting.  The file is written at once.

## `ctx.events`

The bus, seen from one extension.  It is told what core did, and it publishes under its own
namespace so nothing it emits can be mistaken for a core event.

### `ctx.events.subscribe`

`ctx.events.subscribe(event, handler)`.  Required: `event`, `handler`.

Calls `handler(**payload)` whenever core emits `event`.  See
[What a game tells you](offers.md#what-a-game-tells-you) for the events and what each carries.
A handler takes keyword arguments and ignores what it does not use.  It runs on the thread that
emitted the event, so it must return quickly.

```python
def on_played(**payload):
    if not payload["private"]:
        ctx.logger.info("played for %s seconds", payload["seconds"])


ctx.events.subscribe("table.play_recorded", on_played)
```

A handler that raises is contained and logged, and takes the extension out for this run with
the reason "Failed while handling table.play_recorded".  Its subscriptions are dropped and its
routes answer 501.

### `ctx.events.publish`

`ctx.events.publish(event, **payload)`.  Required: `event`.

Emits `<name>.<event>` with `payload`.  The `event` has to be named in the manifest's `events`.

```python
ctx.events.publish("scored", game_id="g1", score=10)    # emits "example_scores.scored"
```

Other extensions and core subscribers hear it.  It raises `ContractError` for an event the
manifest does not declare.

## `ctx.files`

The folders an extension works from, so core accepts a path inside one.  Nothing here stops an
extension reading a file, since in-process Python cannot be prevented from opening one.  What
it does is let core's own routes take a path the extension is working with.  An importer
converting an old library has to hand core files nowhere near VPinFE's, and without this every
one is refused.  The folders are declared rather than assumed, so what an install will read is
something a person agreed to and can be shown.

### `ctx.files.set_roots`

`ctx.files.set_roots(paths)`.  Required: `paths`.

Replaces the set of folders.  Paths are expanded and resolved.  Call it again when the user
points somewhere else, and the set follows rather than accumulates.  Needs the `fs:read`
capability.

```python
ctx.files.set_roots([ctx.config.get("source")])
```

The user sees, on the extension's page, that it reads files.  Raises `ContractError` without
`fs:read`.

### `ctx.files.roots`

`ctx.files.roots() -> tuple[str, ...]`.

The folders now declared, resolved.

```python
inside = any(path.startswith(root) for root in ctx.files.roots())
```

Does not raise.

### `ctx.files.may_write`

`ctx.files.may_write() -> bool`.

Whether the manifest declared `fs:write`, which is what lets `ctx.games.add_table(...,
move=True)` take a file out of one of these folders instead of copying it.

```python
if not ctx.files.may_write():
    ctx.logger.info("copying, not moving")
```

Does not raise.

## `ctx.jobs`

Slow work, run the way core runs it.  The job id is `<name>.<kind>`, so a job somebody is
watching says which extension is doing it.  There is one at a time per kind, since two imports
of one library at once would race each other into the same folders.

### `ctx.jobs.submit`

`ctx.jobs.submit(kind, work, *, stoppable=False, keep_result=False) -> Job`.
Required: `kind`, `work`.

Runs `work(job)` on its own thread.  The job appears in the Jobs view and on `/api/v1/jobs`.
`work` receives the job:

| on the job | what it does |
|---|---|
| `job.progress(done, total, message)` | Says how far it is |
| `job.log(message)` | Adds a line to what the job reports |
| `job.detail` | A line beside the progress |
| `job.halt` | A `threading.Event`, set when somebody asks the job to stop.  Look at it between units |
| `job.stopped` | Set it to True when the work ended early because `halt` was set |
| `job.problems` | How many parts failed |
| `job.id` | The id a run answers with as `job_id` |

With `stoppable=True` the job offers a **Stop** link, which calls
`POST /api/v1/ext/<name>/jobs/<kind>/stop` at the Library tier.  With `keep_result=True` what
`work` returns is written to disk, the latest 20 with the oldest going first, so the Jobs view
can open it after the page that started the job has closed, and after a restart too.  A result
may list `rows`.  Every row with an `error` is shown under what failed, by its
`name`, and `error` takes a string or `{text, detail}`.

A job is shown only to callers who reach the tier of the request that started it, so one a
Library request starts is not listed for a Remote, and its progress and errors are not streamed
to one.  A job started with no request behind it counts as Library's.

```python
def work(job):
    for index, game in enumerate(games, start=1):
        if job.halt.is_set():
            job.stopped = True
            break
        job.progress(index, len(games), game["name"])
    return {"rows": []}


job = ctx.jobs.submit("import", work, stoppable=True, keep_result=True)
```

The user sees the job in the Jobs view with its progress, and a Stop link when `stoppable`.  An
exception out of `work` ends the job as failed with that message.  It does not take the
extension out.

### `ctx.jobs.stop`

`ctx.jobs.stop(kind) -> bool`.  Required: `kind`.

Asks this extension's running job of that kind to end early, by setting `job.halt`.  False
where there is none, or where it was not started `stoppable`.  It does not wait.

```python
if not ctx.jobs.stop("import"):
    ctx.logger.info("nothing to stop")
```

Does not raise.

### `ctx.jobs.active`

`ctx.jobs.active() -> tuple[str, ...]`.

The ids of this extension's running jobs.

```python
busy = bool(ctx.jobs.active())
```

Does not raise.

## `ctx.os`

What an operating system's helper does for an extension.  It reaches the helper an operating
system ships, and today only VPinOS ships one; where none stands, `ctx.os.available` is false
and everything an extension needs on every system is in core.  VPinFE never runs as root.  Where the
system ships a helper, with fixed methods that take no command, no path, no package name and no
address, this is the one way to ask it.  `ctx.os.available` says whether one stands on this
device, and `ctx.os.methods` lists the methods this extension may call.  Each entry has:

| key | what it holds |
|---|---|
| `name` | What to pass to `call`, such as `updates.check` |
| `changes` | True where the method changes the device, False where it only reads |
| `says` | Core's sentence for it, in the language now set |

A method belongs to an area, and an area is a capability: `os:updates` today.  An extension
declares the areas it uses, and the install screen tells the person in words, such as "install
system updates on this device".  `ctx.os.methods` is empty without one.

### `ctx.os.call`

`ctx.os.call(method, **args) -> Any`.  Required: `method`.

Asks the helper for `method` and returns what it answers, as JSON would carry it.  A method
that only reads, such as `updates.check`, can be called from anywhere.  A method that changes
the device, such as `updates.apply`, is refused unless the request it is called from reached
Protected.  Core marks each request with the tier it reached, lowered to the tier the act is
declared at, and the mark follows the request into the function core calls and into a job started from it, and it ends when the request does.
An event handler, a timer or a thread the extension started has no mark, and neither has a task
that is still running after the request was answered, so a change from any of them is refused.
A request reaches Protected when an Owner makes it under a live Confirm.  The Manager asks for
the Confirm when the person presses the act, and retries; a token or a Remote pass never reaches
Protected.  Declare the act `tier=PROTECTED` and core holds the request to it before `run` is
called.  The mark is how VPinFE keeps an act deliberate, and it is not a lock on the machine: an
accepted extension runs as the same user and could open the helper's socket itself.

```python
def apply(request):
    ctx.jobs.submit("apply", lambda job: ctx.os.call("updates.apply"))
```

The person sees the sentence in `ctx.os.methods` as the last step before a change: build the
act's `summary` from it, and do not word an act that needs a Confirm in the extension's own
terms.  VPinFE's log names the extension, the method and where the request came from.

A change can take as long as an hour, and `call` waits for it on the thread that called.  Start
one from a job, as above, and never call it from `async` code, where it would stop the whole
server until the helper answered.  Raises `ContractError` for a method no helper offers or an
area the manifest did not declare, `ValueError` for an argument the method does not take or JSON
cannot carry, `PermissionError` for a change without a Protected request, `Unavailable` where no
helper answered, answered too late or gave an answer that cannot be read, and `RuntimeError`
with the helper's own reason where it said no.

## `ctx.system`

Restarting VPinFE, rebooting and powering off this device, the same on Windows, macOS and
Linux.  Core runs the system's own command, so an extension never sees the differences.  It
needs `system:admin` in the manifest's `scopes`, and it is refused unless the request it is
called from reached Protected, as `ctx.os.call` is for a change.

Core does not ask the person.  By the time it runs the request is already made, so ask first
in the extension, in the act's `summary`.  Each call answers at once and acts about a second
later, so the answer to the request that asked is not lost with the process.  VPinFE's log,
under the extension's own name, says where the request came from.

Each call raises `ContractError` where `system:admin` is not declared and `PermissionError`
for a call outside a Protected request, or for an action this install cannot perform.

### `ctx.system.restart_vpinfe`

`ctx.system.restart_vpinfe(reason="") -> dict`.

Restarts VPinFE on this device.  Answers `{scope, action, what, scheduled}`, where `what` is
the action's name in the language now set.

```python
ctx.system.restart_vpinfe(reason="settings changed")
```

### `ctx.system.reboot`

`ctx.system.reboot(reason="") -> dict`.

Reboots this device, with the same answer.  On macOS the first call may ask the person for
Automation permission.

### `ctx.system.shut_down`

`ctx.system.shut_down(reason="") -> dict`.

Powers this device off, with the same answer.  It has to be switched on again by hand.

## `ctx.hardware`

The USB, serial and display devices attached to this device, read the same way on Windows, macOS and Linux.  It needs
`hardware:read` in the manifest's `scopes`.  That is the scope for asking core what is plugged
in; the `hardware:usb` capability is the declaration for an extension that opens a USB device
itself.  Core only lists devices.  It opens none, installs no udev rule and sets no device
permission, so reaching one is the extension's own work.

A serial number is never read.  A device's own interfaces are left out, and so is a hub
where the OS says it is one.  A macOS hub with nothing plugged into it is listed.  A serial
port is a USB one: `ttyUSB` and `ttyACM` on Linux, `cu.usb*` and the common adapter drivers'
names on macOS, a COM port with USB ids on Windows.  Its name can carry the adapter's serial
number, because the OS puts it there, and it is the path the extension opens.  Displays come
from a list VPinFE keeps, which the Manager's Screens page reads again; USB and serial are read
on each call.

| name | answers |
|---|---|
| `devices(kind=None)` | Every device of `kind`, which is `"usb"`, `"serial"` or `"display"`, or all three when it is left out |
| `access_help()` | One line, in the language now set, on what lets a device open on this operating system.  Show it where a device is listed as not accessible |

Each device is a dict of `kind`, `vendor_id` and `product_id` (four lowercase hex digits, `""`
where there are none), `vendor`, `product`, `name`, `port` and `accessible`.  `port` is the
serial port: a path such as `/dev/ttyACM0`, or `COM3`.  `accessible` is whether VPinFE may
open the device, `None` where the OS cannot say without opening it.  A display also has `x`,
`y`, `width` and `height`.

A kind this device cannot list is left out of the answer, never raised.  An unknown `kind`
raises `ValueError`.

```python
for one in ctx.hardware.devices("usb"):
    if (one["vendor_id"], one["product_id"]) == ("1209", "eaea"):
        ...
```

### `ctx.hardware.reaches`

`ctx.hardware.reaches() -> tuple[str, ...]`.  The names above this extension may call, which is
none without `hardware:read`.
