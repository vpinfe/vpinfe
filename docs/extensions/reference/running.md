# Running one

This page covers where an extension keeps its settings, how to switch it off and on, where its
log is, and what you see when it breaks.

## Config

There are two homes, because there are two owners.

| file | holds |
|---|---|
| `vpinfe.json`, `extensions.switched_off` | the names of the extensions switched off |
| `extension_settings/<name>.json` | that extension's own settings, its players' `accounts`, and what core remembers it `declared` |

Core never touches the rest of `vpinfe.json`.  That file holds what core is configured with,
and it is where a token would go.  `accounts` and `declared` are reserved, so they cannot be
setting names.

A setting a 2.x file held for an extension is copied across once, while that file is
converted, and removed from it.  Core keeps the old names in its schema so the file still
converts, and nothing writes them again.

Each extension gets a file of its own instead of a namespace inside one, because a namespace
in somebody else's file is a weaker form of "own" than a file.  One extension's bad file costs
that extension and no other.  Settings do not sit beside an extension's code either: one the
build ships has no directory in the config dir, so they need a home that does not depend on
where the code came from.

### Switching one on or off

Whether an extension is switched off is core's setting, because core has to know before it
loads anything.  A person switches one with the switch on its page under Settings >
Extensions, or with `PUT /api/v1/extensions/{name}/enabled` (needs `config:write`;
`GET /api/v1/extensions` lists them and needs `instance:read`).  Either writes
`extensions.switched_off`.

- **Off is at once.**  The extension goes the way a broken one does, below, and says it was
  switched off rather than that it broke.
- **On is at once for an extension that only declares through `ctx.ui`.**  Its lists,
  settings, actions and account are available with no restart, because core serves them.
- **On waits for a restart for an extension with routers of its own** (`ctx.add_router`).
  Routes mount once at startup, and one that was off never registered any.  It says "Starts at
  the next restart" until then.

## Logs

An extension logs to `vpinfe.ext.<name>`, which you get as `ctx.logger`.  It never logs into a
core namespace, because the namespace is how "which extension did this?" stays answerable.
Everywhere below, "the log" means these lines in VPinFE's log.

## When one breaks

Reading the manifest, importing the package and calling `register` are each somebody else's
code, and any of them failing costs one extension and no other.  Every failure below says why,
on the extension's page under Settings > Extensions and in `GET /api/v1/extensions`.  The log
has the traceback.

| what failed | what happens | where it shows |
|---|---|---|
| The manifest is missing, unreadable, names a bad name, an unknown capability or feature or a platform that is not a name, or says no platform | Not loaded. State *failed* | The extension's page, with the reason in words (for instance "Its folder has no extension.json") |
| The manifest's `name` is not the folder's | Not loaded | "Its manifest calls it X, so its folder has to be called that too" |
| `requires_platform` is not offered | Not loaded | "Built for extension platform 2, and this build offers 1" |
| The manifest names a platform this device is not, or a feature this install lacks | Not started, listed as *off* | "It does not run on Windows", or the missing features, with a button to open this device's settings |
| `scopes` names a scope that does not exist | Taken out at startup | "Asks for scopes that do not exist: ..." |
| The package will not import, `register` is missing, or `register` raises | *failed*. What it had offered by then is taken back | "The log says why it did not start". The exception is in the log |
| A `ctx` call raises `ContractError` during `register` (a setting without `ui:mount`, a list title that clashes) | The same: *failed* | The log has the `ContractError` line, which says what to change |
| A function core calls raises (a list's `rows`, a status, an act, a dialog step, a setting's `check`, an account function) | That request alone fails with the line `ctx.why` words. The extension keeps running | On the page that asked: the list says it could not be read, a setting shows the refusal under the field |
| A function raises `Unavailable`, or raises `from` a connection failure | The same, but served as `502` with the error code `upstream_unavailable` | The page says it "could not reach its service" with the line given |
| `rows()` returns something JSON cannot carry, or a result that is not a list of dicts | That request fails | The list says what it cannot show |
| An unhandled error out of one of its own routers (`ctx.add_router`) | Taken out for this run. State *disabled*. Its subscriptions and apps are withdrawn, its scopes go, and its routes answer `501` naming the extension and the reason | The page: "Unhandled error serving /api/v1/ext/..." |
| An event handler it subscribed raises | The same: taken out for this run | The page: "Failed while handling table.play_recorded" |
| Switched off | Withdrawn like the above, but says so | "Switched off" |
| Switched on, with routers of its own | *off* until the next start | "Starts at the next restart" |

A *disabled* extension is not written down.  A fault that happened once must not take an
extension away until somebody notices a setting they never set, so it starts again on the next
run.

Taking one out withdraws four things:

- its subscriptions, so it is told nothing more
- its scopes, so nothing holds a grant into something that is not running
- the apps it provided, with their words, and the services it answered, so no suffix stays
  claimed by it and core asks it nothing
- its routes keep their paths but answer `501` rather than `404`, because a `404` reads as a
  typo

### Unavailable

Raise `Unavailable` when a service did not answer, or answered with something unreadable.  Any
function core calls may raise it, with the message a person reads.

```python
from common.extensions.contract import Unavailable

try:
    rows = fetch(address)
except (OSError, ValueError) as exc:
    raise Unavailable(ctx.why(exc, at=address)) from exc
```

Core serves it as `502` with `upstream_unavailable` rather than as a mistake in the request,
and the Manager draws it as "could not reach its service" with that message under it.  A
failure to connect raised without `Unavailable` counts too, as long as the error is in the
cause chain.  The extension is not disabled, and the next visit or Refresh asks again.

### Checking one

`python main.py --check-extension <folder>` loads one extension the way core does, in a
scratch store, and reports.  Exit status 0 means it passed and 1 means it did not.  Only read
functions are called (a list's `rows`, a status, a dialog's first step, the settings), and
nothing is sent or saved.  The manifest's `platforms` do not stop it: an extension for one
distribution is checked on any device, and a `platforms` line says whether this one would run
it.

```
=== extensions/vpinplay
loads      vpinplay 1.0.0
author     VPinFE
home       https://github.com/vpinfe/extension-vpinplay
source     https://github.com/vpinfe/extension-vpinplay
settings   endpoint (string, default 'https://api.vpinplay.com:8888')
list       tables in community: 10 columns, 5 views
account    a player can hold one
reads      list tables: could not reach its service - Tunnel connection failed: 403 Forbidden
reads      settings: 1 fields
=== extensions/library_importer
loads      library_importer 0.1.0
author     VPinFE
home       https://github.com/vpinfe/extension-library-importer
source     https://github.com/vpinfe/extension-library-importer
dialog     import: ask check run progress
routes     its own, on ext:library_importer:library (2 routes)
routes     its own, on ext:library_importer:library (1 routes)
reads      dialog import: first step has 2 fields
```

| line | meaning |
|---|---|
| `loads` | It registered: name and version |
| `author`, `home`, `source`, `picture` | The identity the manifest gives it. `home` and `picture` appear only when it has them, and a `picture` that does not decode is a failure |
| `settings`, `list`, `dialog`, `account` | What it declared. A dialog lists the methods the object has |
| `routes` | Routers of its own, with the scope and route count |
| `reads` | What a read function answered. A service that is down gives `reads      <what>: could not reach its service - <message>` and is not a failure |
| `missing` | A word the extension shows as its own key. See below |
| `fails` | Why it did not load: the line that the page would show, then the exception |

A word the catalog lacks, for a name, a description, a setting's label, a select choice, a
list title, a column header, a view name or an action label:

```
missing    ext.bad_scores.settings.api_key.label - a setting's label
missing    ext.bad_scores.list.top.column.score.header - a column's header
missing    ext.bad_scores.list.top.view.best.name - a view's name
```

A failure:

```
fails      The log says why it did not start - ContractError: worse_scores offers settings, which needs the ui:mount capability its manifest does not declare
fails      Its manifest calls it example_scores, so its folder has to be called that too
```

### The command line

| flag | meaning |
|---|---|
| `--check-extension <folder>` | Load and report one extension, as above. Repeat for more |
| `--configdir <dir>` | Use this configuration directory, where installed extensions are searched. Also `VPINFE_CONFIG_DIR` |
