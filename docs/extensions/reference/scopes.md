# Scopes and the gate

Core attaches the gate.  Everything an extension serves is at one of three tiers, and core
puts the tier's scope on every route.  You cannot ship a route without a gate, because you never
attach one.

| tier | who reaches it | for |
|---|---|---|
| `play` (`ext:<name>:play`) | A Remote pass or a Play token, and everyone above | What a person at the VPinFE device or on a mobile device may see or press |
| `library` (`ext:<name>:library`) | An Owner or a Full token | The default: managing the library, the extension's settings and accounts |
| `protected` (`ext:<name>:protected`) | An Owner who has just confirmed.  Never a token | An act that changes the device or hands out access |

An extension says it with `tier=` on a list, page, panel group, action or verb, with
`@tier(...)` over a single act, and with `tier=` on `ctx.add_router`, all from
`common.extensions.contract`.  A route, page or act says Library when it says nothing.  Looking at
a list, page, panel group or dialog declared Protected needs only Library: it is changing
something there that needs the Confirm.  A router at Protected is gated as a whole, so every
method on it needs the Confirm.

The manifest's `tiers` lists `protected` when the extension uses it.  A list, page, panel
group, action, verb or router at a tier it does not list, or at one that does not exist, fails
when it is declared, and an act marked `@tier(...)` at one is refused when it is called.
Library and Protected say nothing to whoever installs it: accepting covers both.  Core serves the
extension's settings, accounts and job stop at Library, whatever the manifest says.

Play is different, because a Remote pass belongs to somebody who is not the Owner.  The manifest's
`play` names each surface a Remote can open, as `router`, `action:<key>`, `list:<key>`,
`page:<key>`, `group:<subject>` or `verb:<subject>:<key>`.  The install page shows the list as
"A Remote Can Open" before the Owner accepts.  A surface put at Play that `play` does not name fails
when it is declared, and an act marked `@tier(PLAY)` on a surface that is not Play is refused when it
is called.  An update that adds a surface asks the Owner again, and only about the one added.

What a request may change is decided by the tier the act is declared at, not by the tier of the
person who pressed it.  A Library act pressed by an Owner under a live Confirm still runs as
Library, so `ctx.os` and `ctx.system` refuse its changes.  Declare the act `@tier(PROTECTED)` for
them to work.

A scope belonging to an extension is granted only while that extension is running.

## What `scopes` in the manifest asks for

The manifest's `scopes` are the core scopes the extension asks to use.  The extension's page
lists them in plain words for whoever installs it, and they decide which `ctx.games`,
`ctx.players` and `ctx.uploads` calls work.  Asking for a scope that does not exist fails the
extension with "Asks for scopes that do not exist".

| scope | lets the extension |
|---|---|
| `games:read` | Read the library through `ctx.games` |
| `games:write` | Create games, add tables and media, change details, ratings and tags through `ctx.games` |
| `games:export_full` | Export the library in full |
| `collections:read`, `collections:write` | Read, change collections through `ctx.collections` |
| `play:read`, `launch:invoke`, `play:stop` | See what is playing, start a game, stop one.  Three separate permissions |
| `uploads:write` | Import files into the library through `ctx.uploads` |
| `vps:read` | Read the VPS catalog |
| `filesystem:read` | Browse folders |
| `capture:run` | Record media |
| `config:read`, `config:write` | Read, change VPinFE's configuration |
| `system:read`, `system:admin` | Read system state, administer it.  `system:admin` is what lets `ctx.system` restart VPinFE, reboot or power off this device |
| `events:subscribe` | Subscribe to the event stream over the API |
| `jobs:read` | Read jobs |
| `input:act` | Send input |
| `devices:read`, `devices:write` | Read, change devices |
| `hardware:read` | List the USB, serial and display devices attached to this device, through `ctx.hardware`.  Not `devices:*`, which are other VPinFE installs |
| `preferences:read`, `preferences:write` | Read, change preferences |
| `instance:read` | Read the install's identity and the list of extensions |
| `players:read`, `players:write` | Read, change players.  `ctx.players.roster` and the calls beside it need `players:read` |
| `apps:provide` | Provide an app with `ctx.apps.provide` |
| `access:read`, `access:write` | List the browsers signed in, sign one out |

## Capabilities

A capability is what an extension asks of the device, as opposed to of the domain.  The list
is what a person consents to, so a capability nobody has heard of cannot be consented to.

| capability | reaches |
|---|---|
| `ui:mount` | Needed for every `ctx.ui` call: settings, lists, actions, an account, what it holds |
| `config:own` | Its own settings file |
| `net:outbound` | The network.  A declaration for the person installing, and what lets a list column show a web picture; see below |
| `proc:spawn` | Starting programs.  A declaration for the person installing |
| `hardware:usb` | USB devices.  A declaration for the person installing |
| `fs:read` | `ctx.files.set_roots`: the folders core will take a path from |
| `fs:write` | Moving a file out of those folders with `ctx.games.add_table(..., move=True)` |
| `os:updates` | `ctx.os.call("updates.check")` and `ctx.os.call("updates.apply")`: looking for system updates and installing them.  Where the system ships a helper; see [`ctx.os`](context.md#ctxos) |

**Capabilities are advisory.**  An extension runs in VPinFE's process, and Python in the same
process can always open a file, open a socket or start a program.  What the declaration does is
make the extension say what it intends, show that to whoever installs it, and hold the calls
that go through `ctx` to it.  Core enforces `ui:mount`, `fs:read`, `fs:write`, `os:` areas and the scopes
where it is the one acting, and `net:outbound` where core downloads a web picture for a list
column.  It does not enforce `proc:spawn` or `hardware:usb` at all.  Once extensions run out of process, the same declarations become the boundary.
