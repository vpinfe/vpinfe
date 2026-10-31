# Coding Conventions

These follow the standard conventions for each language rather than anything invented here.
Where the existing code disagrees, the existing code is the thing that changes — but only when
it is being touched anyway, not in a sweep.

`ruff` enforces the mechanical half. See [Linting](#linting) for what is advisory and what is
blocking.

## Naming

### Python — PEP 8

| Thing | Convention | Example |
|-------|-----------|---------|
| Functions, methods, variables | `snake_case` | `table_id`, `ensure_unique_ids` |
| Modules and packages | `snake_case` | `game_identity.py`, `httpapi/` |
| Classes | `CapWords` | `MetaConfig`, `TableParser` |
| Exceptions | `CapWords` ending in `Error` | `NotFoundError`, `InvalidRequestError` |
| Constants | `UPPER_SNAKE_CASE` | `API_PREFIX`, `CURRENT_VPINFE_SCHEMA` |
| Internal helpers | leading underscore | `_catalog()`, `_resource()` |

Python does not use camelCase. PEP 8 permits it "only in contexts where that's already the
prevailing style, to retain backwards compatibility", which was the argument for the names
2.x left behind. They no longer exist as of 3.0, and `tests/invariants/test_python_is_snake_case.py`
keeps it that way.

The distinction that decides these: a camelCase name **a theme reads** is a contract and
stays, because renaming it costs every theme a change. A camelCase name that never leaves
Python is just old. So `tableDirName` is still a key in the contract 1 payload and always
will be, `gameDirName` is the same value in contract 2's, and the attribute both are
published from is `game.game_dir_name`. The keys and the attribute are separate things —
`frontend/game_state.py` maps the attribute to the contract 2 key, `_LEGACY_ROW_KEYS` in
`frontend/theme_contract.py` gives contract 1 its spelling back, and `MediaSpec` carries
an attribute and its key, as `attr` and `payload_key`.

### JavaScript

`camelCase` for variables and functions, `PascalCase` for classes. JS reading a snake_case
JSON field is normal and needs no translation layer:

```javascript
const uploadId = (await begin.json()).id;
const summary = await (await fetch(`/api/v1/uploads/${uploadId}`)).json();
console.log(summary.total_bytes);
```

### JSON over HTTP

`snake_case`. There is no single industry standard here — camelCase is common for
JavaScript-facing APIs, and snake_case is equally standard (GitHub, Stripe and Slack all use
it). snake_case wins for this codebase because it matches Python, so nothing is translated at
the boundary, and it matches the JSON the app already returned.

### Spelling

US English, in identifiers, comments and docs alike — `color`, `analyze`, `behavior`,
`authorization`. The codebase is already consistent about it, and `config_access.py` even
maps `colour` → `color` on the way in, so the choice is settled rather than open.

It matters most in a field name: `alt_color` is part of the API contract, and a spelling
change there would be a breaking change for a caller.

### Config keys

`vpinfe.ini` keys are lowercase and unseparated (`gamerootdir`, `manageruiport`). That is
established and user-facing; leave it alone.

`.info` keys are `snake_case` in the sections we own — `vpinfe`, `tables`, `assets`,
and so are those section names.
`Info` and `User` keep PascalCase: other frontends read them, so their shape is a contract
rather than a style choice.

### The nouns, in identifiers

`## Vocabulary` below defines them. In code: `game_dir` is a game's folder and
`game_dir_name` is that folder's name; `game_root_dir` is the library holding them;
`vpx_path` is a table file. A playfield is never `table`.

`tests/invariants/test_game_and_table_names.py` checks this by meaning rather than by
spelling - it calls the launch builder to confirm the argument named for a table really is
what the launcher is handed. Two passes got it backwards before that test existed, and the
second did it while fixing the first, because both stated the rule as a shape - which names
refer to the folder - and a shape cannot see the case that contradicts it.

### Kind, not type

A closed set of named variants is a **kind** — media kinds, job kinds, asset kinds, the
kind of control a filter wants. `kind` in identifiers, in the JSON we write, and in the
fields we serve.

`type` is for the places somebody else already chose the word, and only those:

- Python's own: `type()`, `isinstance`, `typing`, `TypeVar`.
- HTTP and HTML: `Content-Type`, Starlette's `media_type=`, `<input type=>`.
- A library's parameter: NiceGUI's `ui.notify(type=...)`.
- A key already on disk. `Info.Type` is in every `.info` a user has, so `game_type` reads
  it and keeps that word end to end. Translating at the boundary would leave a reader
  converting in their head at every call site, which is worse than one name that says
  where it came from.

The cost of not picking one is measurable rather than theoretical. `MEDIA_SPECS` and
`MEDIA_TYPES` were the same closed set of media kinds under two words, one module
importing the other, and the second had fallen seven kinds behind before anything noticed
- which cost a KeyError on upload and an empty filename on lookup, in the two kinds people
replace most. There is one list now.

### A token in a command a person writes is `{name}`

Wherever a person writes a command line that VPinFE fills in - a launcher's arguments, the
table commands, a recording's Record and Encode Commands - a token is written `{name}`,
`snake_case`, and an extension's own is `{extension.name}`. One syntax, so a token learned
on one page works on the next.

- **`common/tokens.py` holds it**: `TOKEN` finds a token, `fill` puts values in,
  `resolve` refuses a name its context does not offer, and `written` is how a page shows
  one. An app reaches `fill` through the app contract.
- **Braces, not square brackets**: FFmpeg names its filter streams `[v]`, and a bracket
  syntax would have to escape them in the very commands it is for.
- `tests/invariants/test_one_token_syntax.py` fails on a token pattern or a token replace
  written anywhere else.

### Documentation files

A page under `docs/` is lowercase words joined by dashes: `theme-publishing.md`,
`http-api.md`. Underscores stay in Python module names, where an import needs them. The
GitHub wiki already names its pages with dashes, so the docs and the wiki that links to them
read the same.

The exceptions are the conventional uppercase files, `README.md` and `RELEASE_NOTES.md`, and
the wiki's own `Title-Case-With-Dashes` page names.

## Things we version

Three, each versioning a different kind of thing, each with its own word:

| what | versions | where |
|---|---|---|
| `.info` | a **file's shape** we write and read back | `vpinfe.schema` |
| HTTP surface | a **wire protocol** we serve | `/api/v1` |
| theme surface | an **interface handed to somebody else's code** | the contract, implied by `min_vpinfe` in a theme's `manifest.json` |

They stay separate words because they answer different questions and move on different
triggers — a `.info` reshape says nothing about the HTTP API. `schema` is deliberately not
reused for themes: a schema describes data, and the theme surface is data plus methods plus
events.

One rule across all three, which is the consistency worth having:

> **Additive changes never bump a version. Only a removal or a reshape does.**

New fields, new endpoints, new methods are visible at every version and consumers
feature-detect them. That is what keeps bumps rare enough to be worth doing properly.

## Layout

- A new subsystem gets its own top-level package, not another module in `common/`. `httpapi/`
  is the current example.
- Keep `common/` UI-independent.
- `common/` is a layer, not a bucket. Domain code goes in `common/games/`, `common/online/`
  or `common/host/`; `common/` itself holds only what knows nothing about any of them, and may
  never import from those packages. A module used by one domain lives in that domain. Within
  the shared layer the units are ranked and one imports its own rank or below; see
  `docs/common.md`, Layers, and `tests/invariants/test_layering.py`.
- `manager/` is a stack, not a pile: every module belongs to one area, the areas are ranked
  (the shared kit, then the area screens, then the workbench and the remote, then the shell),
  and an import goes to the same rank or below. A screen that needs something from above
  registers with it or takes it as an argument. The exceptions are named in
  `tests/invariants/test_manager_layering.py`, and a new module is listed there first.
- Route handlers stay thin: a path, a scope, one service call, a model. Logic belongs in a
  service under `common/` where the other callers can reach it, and nothing calls a handler.
  A service refuses with `common/service_errors.py`; the API turns that into its envelope.
- Files we ship that are not Python live in `<owner>/static/` — `frontend/static/` for what a
  browser fetches, `manager/static/` for the Manager's, `common/host/static/` for what
  goes to hardware. Named for who owns them, so a directory does not become the home for
  anything that has no other one.
- `scripts/` is what the build *runs*; `packaging/` is what it *ships*. The fetch scripts are
  the first, the icon and the PyInstaller spec are the second.
- Reach a shipped file through `common.config.paths.bundled()`. It answers for a source checkout and
  for both kinds of frozen build, so nothing needs its own chain of guesses.
- A file the app reads that is not Python is under a root `packaging/vpinfe.spec` lists, or
  it is absent from the build. `tests/invariants/test_build_ships_its_data.py` fails on one
  that does not. What a bundled extension imports, the spec finds and names itself.

## Comments and docstrings

Write code that reads without them, and then add the ones that earn their place. There is no
target ratio: a module whose two public functions each state a real contract carries more
prose than one that reads straight through, and both are right.

What earns its place is a trap, a contract, or a decision that reads as a mistake without one
line of reason. What does not is the argument behind the decision, the alternatives that were
weighed, or the measurements that settled it — those go in the commit message, which is where
somebody looks when they ask why.

- **Module docstring: one line.** A second only if the module's *existence* is non-obvious.
  Long rationale goes in `docs/` with a pointer, not in the file.
- **Comment for guardrails** — where a plausible, well-meaning edit would break something.
  These earn their place:

  ```python
  # Outside the filehash check below on purpose: the id must survive the table
  # changing, which is exactly when alt_vpsid is cleared.
  ```

- **No docstring where the name and signature already say it.** Write one when there is a real
  contract: what it raises, what it writes, what it deliberately does not do.
- **Prose does not outweigh the code it describes.** A docstring longer than the function
  under it is a sign the argument has been written down rather than the contract. Five lines
  of code do not carry eleven lines of prose. Keep the contract, move the reasoning to the
  commit message.
- Reference data — a schema version history beside its constant, a lookup table — is fine.
  That is data, not prose.

## Words a person reads

Every one of them comes from the catalog. `common/i18n/catalogs/en.json` holds the English,
`t("key")` fetches it, and nothing renders a string written where it is shown.

```python
ui.label(t("manager.games.no_table_selected"))
ui.notify(t("said.action_failed"), caption=why(exc), type="negative")
```

`tests/invariants/test_i18n_catalog.py` fails the suite on a literal in a display position,
so this is enforced rather than remembered.

### A failure leads with words

The message says what could not be done: "Could not install it". What went wrong goes under
it, in a toast's `caption` or a line's tooltip, from `common.failures.why(exc)`. That words
the failures a person can act on - no permission, a full disk, nothing at a path, a host
that did not answer - and hands back the exception's own text for anything else. The log
keeps the traceback.

No message key takes the exception or its text, so no entry has an `{exc}` slot. A server
error raised from one carries `why(exc)` as its message, and the Manager puts that under its
own lead in turn. `tests/invariants/test_a_failure_leads_with_words.py` holds the whole tree
to it.

### A bulk act says its failures once

An act over several items collects what failed and says it once, after the last item: one
item named as it would be alone, several counted (*3 launchers could not be removed*) with
each distinct reason once in the caption. `manager.kit.failed_together.Failures` does it: the
loop hands each failure to `add` and carries on, and `say` is called after the loop with a
plural entry whose `one` form takes `{name}` and whose `other` form takes `{count}`. A walk
that asks about each item keeps its toast per step, because each answers the step just
taken. `tests/invariants/test_a_bulk_act_says_its_failures_once.py` holds `manager/` to it.

### Help says what a control does, or what to do next

A help line, hint, caption or tooltip does one of two jobs. It says what the control does
when it is used, or what to do next. A line that does neither is cut.

- **Say what happens, not why it was built that way.** What the control changes, what it
  deletes, what stays on disk, what needs a restart. How it works inside, what it used to
  do and why the default is what it is belong in the commit message.
- **A consequence stays, a defense goes.** "Anything changed since, such as a rating, is
  lost" tells a person what happens to their data. "Nothing is addressed by it, so renaming
  is safe" tells them how the code is wired.
- **No words the person never meets elsewhere.** A line that needs an id, a resolver, a
  record or a file format to make sense is describing the code.
- **Do not answer a question nobody asked.** "Nothing is lost, so this is usually a drive
  that has not come back" replies to a worry the person has not voiced. Say what happens
  and the worry is answered.
- **Do not restate.** Not the label, not the page title, not what the control already
  shows (its state, its default), and not a line beside it.
- **One sentence by default.** A second only for a consequence the first does not carry.

To check a line, cover the label and read it as the person at the control. If it tells them
nothing they could not see, or tells them something they did not need to decide, it goes.

### What is not a word

Left in the code, and the checks know to leave them alone:

- CSS classes, anything handed to `run_javascript`, and Quasar props - except the ones
  Quasar draws as text: `error-message`, `hint`, `label`, `placeholder`, `prefix`,
  `suffix`, the `no-option`, `no-data`, `no-results` and `loading` labels, `title` and
  `aria-label`
- log lines — a log says the same thing on every install, which is the point of it
- API paths, event names, config keys, `.info` keys, theme folder names
- **identifiers from somewhere else** — VPX's `vpinball.ini` section names, the fields the
  VPS catalog uses, a device's declared capability. Those are their words, not ours
- product names: VPinFE, VPX Mobile, and the community's own — Nfozzy, SSF, PuP-Pack
- **table names, manufacturers, and everything else the library holds.** A Japanese table is
  called what it is called

### A token is not a label

Where a string is both — a grid filter matches on it *and* a person reads it — the cell keeps
the token and the display resolves it:

```python
_REACH = {device_client.ANSWERING: ("manager.devices.answering", "on", "positive")}
```

`grid.choice_filter` puts the label on screen through `valueFormatter`. Translating the value
instead would move what a saved view filters on.

### A default is read from its setting

A number written into prose rots, and so does a default repeated beside the control that
shows it: the words stay as they were when the default moves. So a default named on screen
is rendered from the setting's own `default`, through a catalog string with a slot, and is
never typed into a description.

- A setting declares whose its default is with `default_is` in `common/config/config_schema.py`:
  `community` for the community's standard, `vpinfe` where no standard exists. `unit` says
  what a number counts.
- Left at the default, the line under the field says only whose it is - *Community
  standard*, *Default* - because the field already shows the value. Changed, it names the
  default: *Community standard: 20 s*, and clearing the field puts it back.
- A default the device resolves, such as Automatic, says what it comes to there: *H.264,
  the community standard*.

`tests/invariants/test_a_default_is_read_from_its_setting.py` fails on any setting whose
description holds its default number or text.

### A setting says when it takes effect

A person who changes a setting is owed an answer to *when*, and a setting that quietly waits
for a restart reads as broken. Each setting declares `applies` in
`common/config/config_schema.py`, from where it is read:

- `now` is the default: read each time it is used, or acted on as it is saved. DOF and the
  real DMD are the second kind - `common/host/peripherals.py` hears `settings.changed` and
  restarts the helper.
- `table` is read when a table starts. `windows` is read when the frontend's windows open,
  and a change shows once they reopen. `restart` is read at startup.
- A page never writes the moment itself. For a `restart` setting, and a `windows` one while
  the windows are open, two things say it, on every page: a chip beside the field that was
  changed - *Reopen needed* or *Restart needed* - and the bar over the page, which asks for
  the windows to be reopened or VPinFE restarted. The server holds the answer
  (`GET /config/waiting`) and `manager/settings/settings.py` and `manager/settings/waiting.py` only draw it.
  Both clear when the value is changed back.
- A `table` setting has no chip and no bar, so its label or description says when it runs
  (*When Any Table Starts*). A description of any other setting never states the moment.

Declaring `applies` is a claim about the setting's read site, and the chip and the bar read
it. Find where it is read, not where it is declared: a setting read once at startup that
says `now` shows no chip, and looks broken.
`tests/invariants/test_a_setting_read_at_startup_does_not_apply_now.py` fails on one whose
every read runs once, at module level or in a function called only from there. It sees
only reads written with a literal section and key. A read through a variable, a wrapper
such as `config_access`, or a function reached by string is not checked.

### Naming a key

`<owner>.<module>.<name>`, named for what owns the string, never for the screen
showing it. A label moves between screens constantly and never moves between owners.

Three shared namespaces exist so a translator writes a word once:

- `word.*` — one-sense vocabulary: `word.cancel`, `word.save`, `word.year`
- `said.*` — a failure said the same way in several places
- `help.*` — one column's explanation, wherever that column is drawn

**Share a word only where the *sense* is the same, not the spelling.** `Table`, `Match`,
`Media` and `Device` each carry several senses here, and folding them would force one
translation on all of them.

**A leaf names the thing or its role, in one or two words**: `empty`, `confirm`, `failed`,
`not_found`, `unknown_kind`. Three words only to keep two apart. Then a kind suffix where
there is one: `.label`, `.help`, `.description`, `.blank`. Those four are the only suffixes
set aside, so `title` and `intro` are names in their own right. Prose keys end in `.help`,
which is what marks them as the tier a locale may leave in English.

The test is rewording. If the English changes and the key is now wrong, the key was made
from the text, so say what the string is for instead of taking words from the sentence. An
error is named for what went wrong (`error.games.unknown_kind`), not for its sentence.
Variants of one thing share a stem and differ by a role word (`restored`, `restored_some`,
`restore_failed`). Column help is named for the column (`manager.assets.reason.help`).

`tests/invariants/test_a_key_names_a_place_not_a_sentence.py` fails on a leaf of more than
three words, and on a three-word leaf whose words all appear, in order, in its own text. A
one or two word leaf that matches its text is fine: it is short enough to stay true.

Exempt are the keys whose leaf is data the code declares: `config.*`, `filter.*`,
`media.kind.*`, the VPX app's field and group ids, two of its own ids
(`from_the_screen.help`, `match_the_display`) and the playfield window tip. Also
`frontend.theme.*`, which theme authors read and `docs/theme.md` documents. A single exempt
key that would pass anyway fails the test, so those only shrink.

### Rewording a string

A key is a translator's only view of what a string is for. When the English of a label,
header or sentence changes, read its key again against the new words and decide:

- **The key still says what the string is.** Keep it. A key is a role, not a copy of the
  words, so `word.save` survives "Save" becoming "Keep".
- **The key names a word or an idea the English has dropped.** Rename it in the same
  commit: the key in `en.json`, every place that asks for it, and the tests. A translator
  who finds `manager.page.game_details` over the word "Game" has been told something
  false.

A new word for the same thing, such as art becoming media or a location becoming a library
folder, reaches every key built on the old one, so sweep for it instead of fixing the one
string in front of you.

`python3 scripts/i18n.py --record` makes the decision visible. It refuses to record while
a key's English differs from the last record, and lists each one. Rename the key, or pass
the ones that still fit: `--record --fits word.save`. A renamed key is a new
key and needs no answer. Name the keys kept in the commit message.

### Adding a string

1. Call `t("...")` with a key and add the English to `en.json`.
2. If it takes a value, name the slot: `"{count} tables"`, filled as `t(key, count=n)`.
   Never build a sentence by adding fragments — the join is different in most languages.
   An entry with a space at either end is one of those fragments, and the catalog
   invariant refuses it; a joiner such as `manager.collection_rules.list_join` is the one
   allowance.
3. `python3 scripts/i18n.py --record --pseudo`, which keeps the staleness hashes and the
   render-check locale in step. Changing the English of a key that already exists is
   *Rewording a string*, above.

`scripts/i18n.py` also answers `--missing`, `--stale`, `--unused` and `--coverage`.

### An app's words

An app keeps its own in `i18n/en.json` beside its code, served under `app.<id>.`: the
`name` in `apps/vpx/i18n/en.json` is `app.vpx.name` to everything else. A prefix is all an
app can hold, so it adds words and never changes one of core's.

- `name`, `field.<key>.label`, `field.<key>.description`, `field.<key>.blank` (what an
  empty field reads as) and `group.<key>.label` are found by what the app declares. Any other key is one its code hands back, such as the reason
  in an `Availability`.
- A field the app's file does not name takes core's `launcher.field.<key>`, so Program
  reads the same on every launcher.
- A label set in code is shown as written. That is for the program's own words, like the
  ones VPX writes above each setting in its ini, and for a product name.
- Where the program labels every member of a set alike, such as a display profile's
  settings, the app's config names the set with `set_word`, and the label reads with
  that word after it.

`--record` and `--pseudo` write every owner's files, and the invariants hold each app's
file to what the app declares, both ways.

An extension keeps its words the same way, served under `ext.<name>.`, with an app it
provides under `app.<id>.` in the same file. `docs/extensions/reference/words.md` lists the
keys.

## Logging

Logs are read by one person trying to work out what happened, usually from the Logs page in
the Manager. Everything below serves that.

### Levels

The level is a promise about who needs to read the line.

- **ERROR** — the operation failed and the user is affected. Always actionable.
- **WARNING** — degraded, unavailable, or ignored, but we carried on. A configured feature
  that silently does nothing belongs here, not at INFO.
- **INFO** — state changes worth having in a timeline afterwards: startup and shutdown, a
  game launched or exited, an import finished, an extension loaded, config changed.
- **DEBUG** — everything else, including per-item detail and progress narration.

**Summarise bulk work at INFO, never one line per item.** Assigning ids to a library once
wrote a line per game and buried the rest of the log; it is one line with a count now, and
the per-game detail is DEBUG.

The bar to hold: an idle startup produces tens of INFO lines, not hundreds. Measure it rather
than guess — start the app and count.

### Logger names

`vpinfe.<area>.<module>`, matching where the code lives:

```python
logger = logging.getLogger("vpinfe.common.games.game_identity")
logger = logging.getLogger("vpinfe.httpapi.games")
```

Areas are `apps`, `common`, `manager`, `frontend`, `httpapi` and `manager`. Extensions get
`vpinfe.ext.<name>`, issued by the extension context — an extension never logs into a core
namespace, because the namespace is how "which extension did this?" stays answerable.

The rest of the name is the path to the module, so a logger says where to look and not
merely which area to search. `tests/invariants/test_logging_conventions.py` checks that,
because a module that moves and keeps its old name reports the wrong area from then on.

### The browser side

A theme and the overlays run in Chromium, and their browser logs go nowhere anyone reads on a
cabinet - an overlay is an iframe with a browser log of its own. Core forwards both to the
same log: whatever a page sends through `console_out`, and every uncaught error and
unhandled rejection without being asked. They arrive at INFO as `[<window>] ...`, or `[<window>/<overlay>] ...` when an
overlay is what threw. The window comes from the connection, so it cannot be
claimed; the overlay comes from the caller, because only it knows which frame it is.

The automatic half is the one that matters. A page that throws stops responding and
reports nothing, which reads as a dead button rather than a fault, and the deliberate
half cannot help - the code that would have called it is what threw.

### Exceptions

Use `logger.exception` inside an `except` block. It keeps the traceback;
`logger.error(str(exc))` discards the only part that locates the fault.

```python
except OSError:
    logger.exception("Failed to enumerate game directory: %s", game_dir)
```

### Say which thing

When a message is about one unit of work, name it — game id, job id, upload id. Someone
reporting "my import failed" has to be findable in the file.

### Never log secrets

No tokens, API keys, or raw config dumps, at any level. Configuration holds an API key once
the auth seam exists, and a log is the easiest way to leak one.

### Formatting

Pass arguments to the logger rather than formatting into it, so the work is skipped when the
level is off:

```python
logger.debug("Assigned game id %s to %s", minted, game.game_dir_name)   # yes
logger.debug(f"Assigned game id {minted} to {game.game_dir_name}")      # no
```

### What we deliberately do not do

**Structured (JSON) logging.** This is a single-user appliance whose Logs page reads a text
file. JSON would break that page and serve nobody. Revisit only if remote diagnostics becomes
a goal.

**Per-request access logs.** `uvicorn.access` is filtered to warnings and above. A theme
polling once a second would drown the file. The API logs failures; successes are the caller's
business.

Worth knowing when moving an endpoint: NiceGUI logs every unmatched URL as a warning, so a
client still polling a path that has moved writes one warning per poll. Retiring a polled
endpoint means moving its callers in the same change, not leaving them to 404.

### Files

One `vpinfe.log` in the log folder (`~/Library/Logs/vpinfe`, `~/.local/state/vpinfe/log`, `Logs` inside the config folder on Windows), rotated at 2 MB with 3 backups, and rolled once at
startup so each run begins in a fresh file with the previous run kept. Restarting to reproduce
a problem no longer destroys the log of the run that showed it.

## Vocabulary

These follow the Virtual Pinball Spreadsheet, because VPS is where a user goes to identify
what they have. Ours used to say the opposite of VPS on both nouns, which meant anyone
reading both had to invert twice.

- **Game** — the pinball-machine concept: folder, identity, metadata, media, assets.
  VPS's top-level entry. One game, one folder. On screen it is also the word for the
  pinball machine itself; *machine* is not a word the screens use.
- **Table** — a launchable artifact for a specific launcher (`.vpx` today). VPS's
  `tableFiles`: a game has several, by different authors, at different versions. A game
  is not permanently one table.
- **Release** - anything an author publishes through VPS for a game: a table, and equally
  a backglass, wheel art, a ROM, a POV, a PuP pack or a media pack. Several authors can each
  release their own of any kind for one game, and a mod or an edition is a release of its
  own. What VPinFE and a theme publish is a release in the same sense. On screen a release
  VPS lists is a **VPS release** in a sentence or a tooltip (plural *VPS releases*), and a
  heading outside Discovery says **VPS Release**. Discovery shows no other kind of release,
  so it keeps **Release** in its headings, tabs and verbs. A file is **matched** to a VPS release, never
  *tied*, and a table matched with no record of how reads **Matched**. A release of VPinFE
  or of a theme, and the year a game was released, keep the bare word.
- **Version** - an update to a release by the same authors. VPS keeps the release's `id`
  across versions, even a major rework, and records only the current one. A table on screen
  is your file of a table release at one version.
- **Mod** - a release VPS links to the release it is based on, or tags `MOD`. On screen it
  is *Mod* and *Mod of*, which VPS and file names already use. Not *parent*, *child* or
  *fork*, and not *Original*, which VPS uses as the manufacturer of an original design.
- **Build** - a build of a program: VPinFE's own ("This build", "a newer build") or an
  app's, and the verb. Never a table.
- **Playfield** — the main screen, and the media shown on it. Not "table": the playfield is
  a facet of a game, and `table` now means the file.
- **Media** — artwork VPinFE shows *about* a game while you browse: playfield, backglass,
  DMD, wheel, logo, cab, FSS, flyer, instruction card, topper, loading video, launch audio,
  rulesheet, their video variants, audio. The logo is the game's title art - usually what a
  wheel is derived from, which is why a wheel-less game shows its logo in the wheel slot. This is `common/media/media_specs.py` and nothing else. Instruction card
  (the apron card image), game flyer (promo art) and rulesheet (a document you
  read) are three different things - keep the words apart.
- **Asset** — content a game needs to *play* as intended, beyond its table:
  `.directb2s`, ROM, alt color, alt sound, PUP pack, music, the per-table `.ini`.
  The per-table `.ini` is config-shaped but is still an asset: without it the table
  plays differently than intended, which is the whole definition.

A **declared ROM name is not a ROM asset.** A table's script sets `cGameName`, and
`vpxparser` records it, but that name means one of two different things:

- **A PinMAME dependency (hard).** The script drives the emulator, and without the ROM
  set in `<game>/pinmame/roms` the table does not run.
- **A DOF key (soft).** The table needs no emulator — EM games have no ROM to have —
  and the name exists only so DOF can map feedback effects to it.

Nothing recorded today tells the two apart, and the shape of the name is a hint rather
than a rule: PinMAME sets look like `mm_109c`, DOF keys like `GTB2001_1971`. Measured
across a large library, **most tables that declare a name have no ROM file, and they are
overwhelmingly EM games that never needed one.** So "declares a ROM, has no ROM file" is
a normal, healthy state for most of a library, and reporting ROM presence as a plain
missing/present flag would call hundreds of working tables broken.

That is why ROM is a *dependency*, not an asset: assets are found by naming rule with no
help from the script (the way VPX finds a `.directb2s` or a table `.ini`), while a
dependency is declared by the script and satisfied by content on disk. FlexDMD is the
second member of that family. The API reports the chain — declared, alias-rewritten,
effective, installed — and `installed` is true-or-null, never false, for the reasons
above. `required` on the chain comes from the `detectpinmame` flag: the script drives
the emulator when it calls `LoadVPM`, `vpmInit`, or creates `VPinMAME.Controller`
directly — measured on the comment-stripped script, because EM tables commonly carry
dead VPM code, and a commented-out `LoadVPM` is not a dependency. Measured against a
validation set covering both cases: every ROM-installed table hits, every DOF-key EM
table misses. One known ambiguity: some EM/PM recreations *conditionally* drive PinMAME for
chime sounds (the `cOptRom` pattern), and read as required when the player may run
happily without the ROM. The flag is set on the table on the next metadata rebuild;
until then the chain reports `required: null`.
- **App** — a program that runs a table file: Visual Pinball X today, `generic` for one
  we only know how to start. Code, not configuration - `apps/` holds them, and
  `common/apps/` is the contract they implement and the registry that finds them.
- **Launcher** — a configured way of running an app: a name, a binary, an ini, its own
  `launcher.<id>` section. User data, and what a table names when it plays differently
  from the rest. One app, as many launchers as somebody wants.
- **Install** — one VPinFE installation: its files, its config, its `install_id`. It
  survives restarts, and it is what an install is addressed as. The word is internal:
  code, config sections and the wire keep it, and a screen never shows it. On screen the
  machine VPinFE runs on is *this device* and another one is a *device*. The verb stays
  (*Install* a theme, *Could not install*), and VPX's own folder is *VPX's folder*.
- **VPinFE** — VPinFE running. What a lifecycle request starts, stops or restarts, as
  against the `frontend` it opens and the `system` it runs on. The install is still there
  when VPinFE is not. Never "app" for this: that word belongs to the thing that plays a
  table, and one word cannot be both.
- **Frontend** - the software a player picks a table and plays in: ours, or another one an
  import reads from. On screen it is the word for anything shown, offered or opened there
  (*In the Frontend*, *The frontend opens on this collection*), and the Manager area that sets
  it up is named for it. A collection's `in_frontend` is the same word on the wire.
- **Cabinet** - the physical cabinet only: Cabinet Mode, how a screen is mounted in it, a
  theme made for a cabinet rather than a desktop, and VPX's own labels. Never the frontend
  that runs on it.
- **Theme** — a player-facing frontend package.
- **Device** - a thing on the network: a VPinFE install, whatever it is switched on for, or
  a mobile device running VPX Mobile, which never runs our code. Not "something that can play": an
  install with only `library` on is a device and plays nothing, so a picker that targets
  play filters on the `frontend` feature and keeps the word. On screen the machine VPinFE
  runs on is *this device*, and the one the reader is on is *your device*.
- **Your device** - the one the browser is on, or the one an import came from (*the old
  device*). "Computer" is not a word a screen shows.
- **Player** — a human who plays. Reserved for that and used for nothing else:
  it meant the machine until 2026-08-19, and every pinball machine ever built
  prints PLAYER 1 on its display. The game's own player is a slot, Player 1 to 4, that
  belongs to the machine; VPinFE's player is the person who takes one, known before the
  game and remembered after it. *Multi*, *alternate* and *profile* are not used on screen,
  and *person* stays in prose and is never a noun on a screen.
  - **Owner** - the one player the library's own record belongs to. A chip, on one row.
  - **Guest** - a player for this session only, forgotten when VPinFE closes. A chip.
  - **Up** - a player the next game counts for, pinball's own word (*PLAYER 1 UP*). Not the
    folder picker's Up, which goes to the parent folder.
  - **Account** - a player's identity with one service. Its fields are the extension's,
    and a player has a section per service.
  - **Card** - the file that carries an account to another cabinet: *Show Card*, *Save
    Card*, *Use My Card*. Not *QR code*, which is the format and also what the main menu
    shows for something that is not a sign-in.
  - **Roster** - the internal word for the list of players. It never reaches a screen.
- **Extension** — a feature extending VPinFE under a manifest. Never "plugin", which is
  reserved for VPX standalone plugins.
- **Third-party** — a library VPinFE bundles and loads at runtime, written by someone else
  and not installed by the user: DOF and libdmdutil today. They ship in `third_party/` and
  are loaded through `common/host/third_party.py`. They are neither extensions (no manifest,
  nothing declared) nor plugins (that word belongs to VPX standalone), so say "third-party"
  and not "integration" or "external service".
- **Tool** - a program on a device that VPinFE runs but neither ships nor plays tables
  with: unar, ffmpeg. The person installs it, and `common/host/tools.py` finds it. Not
  *third-party*, which is what VPinFE bundles, and not *dependency*, which is what a table
  declares.

A catalog string that says *cabinet*, *machine* or *build* fails
`tests/invariants/test_screen_words.py` unless its key is listed there with what the word
names in it. The same check reads every summary and description in the API's OpenAPI
document and each capability description discovery serves, listed by where each sits. One that says *install* as a noun fails `tests/invariants/test_install_word.py`:
the verb passes when it reads as one (*Install unar from your package manager.*, *Could not
install*), and anything else is listed there with why.

A game is not permanently one `.vpx`; say "table" when the file is what is meant.

Media and assets are not the same thing, and the line is what each is *for*: media is what
you look at while browsing, assets are what the table consumes while playing. A backglass is
the case worth knowing — the `.directb2s` is an asset, because the B2S server runs it, while
the backglass *art* the wheel renders is media. Do not call an asset "media" because it
happens to be a file in the game folder.

`asset_registry.ASSET_SPECS` uses "asset" more broadly, as the lifecycle role of anything
being imported — including the table and its media. That is the import pipeline's word for
"a thing being added", not a content category, and it never reaches the HTTP contract, whose
endpoints are `/uploads/*`. Prefer the definitions above anywhere the distinction matters.

Two game identifiers exist and are not interchangeable:

- `vpinfe_id` — this install's stable id. Addresses a game in the API, in events, in jobs.
- `vpsid` / `altvpsid` — correlation with VPSdb and services keyed by it. Note this
  identifies the VPS **game**, not which of its tables you have.

## Manager

The stylesheet is the design system. `manager/static/manager-base.css` holds every class the
Manager uses and `manager/kit/appearance.py` holds the palette tokens, and there is no third place a
treatment is defined. **Grep the stylesheet before inventing one** — a heading, a chip, a state, a control. Something that looks like it needs a new class
usually has one already, and two spellings of the same idea is how a surface stops looking
like one product.

### Appearance

Four modes: `synthwave` (the default), `dark`, `light`, and `system`, which follows the
operating system and resolves to one of the two neutral palettes. Set with
`manager.kit.appearance`.

A mode is a palette and nothing else. Structure, spacing and the type scale are shared, and
a mode may not touch them: that is what keeps four modes from becoming four layouts. A
palette states custom properties, and the rules that use them never name a color.

**And grep the surface next door.** The class is half the answer; the other half is how the
neighbouring vocabulary already decided the same question — which shape means "nothing here",
which color means "present", whether a legend names a blank. Those live in the module that
owns the fact (`manager/kit/media_ownership.py`, `manager/kit/table_features.py`, `manager/kit/game_tables.py`) and in
this file, not in the stylesheet. **The tell is proposing a *new* treatment at all**: if the
Manager already shows this kind of fact anywhere, the answer exists and the job is to find it.

### The panel is a shape, not a place

A rail of named destinations, one of them open and addressable, and a list of facts about
one subject. That is the panel, and it is the same whether it is in the details pane or
fills the content column. Settings is this shape with the install as its subject; it had
been built as a language of its own, and the two were eight treatments apart before anyone
put them side by side — a save bar against per-fact writes, a label over its control
against a label beside it, and two classes used on screen that the stylesheet never
defined.

**The rail row names the section.** The lit row says where you are, and the headings
under it name what is in it. A rail long enough to need grouping takes group headings.

**A section always shows its header, even when it is the only one.** A panel with one section
draws its rail row like any other; there is no bare panel with the row left out.

**Every group in a section is headed.** The first takes a heading as the others do: content
left unnamed above the first rule reads as a stray rather than as the start of the section.
A section that is one group is headed too, a group the app gave no name is headed by its
section's, and a heading that says what the row says is still drawn. A search or filter
that acts on the whole section is not a group: it goes above the first heading, in a
`manager-section-bar`.

**One implementation, in `manager/kit/panel.py`.** The fact list and one constructor per kind of
value live there, and every surface renders through it. Reaching for `ui.switch` or
`ui.input` directly forks the design system — which is how the same field ended up with a
resting edge on one surface and Quasar's default on another. Every constructor hands back
the callable the fact list takes as a value, so a control drops into an entry list as the
second half of a pair.

**Help belongs to the control, not to the row.** A config key is the one place that carries
an explanation on screen rather than in a tooltip: its name says what it is called, not what
turning it off costs. It sits in the value column under the control it explains — across
both columns it starts at the label's edge and reads as a caption for the label. What a
whole page cannot say row by row is said once, above the rows, at the panel's width.

**What a section is for is a lede, under its heading.** `panel.lede` sits tight under the
heading it explains and belongs to that group. `panel.intro` is a loose line across the panel
for what no one group owns. A line that explains a heading is a lede, whichever it was
drawn as first. A rule that holds for a value over time ("unused for 90 days signs out")
goes in the lede of the section that owns the value, not in an aside under the control.

### One shape per kind of value

A panel that shows facts uses the same control for the same kind of value, everywhere:

| Value | Control |
|---|---|
| Binary the user can set | a switch, `color=positive` |
| A list to pick from | a select |
| A state the user cannot set | a chip |
| Free text the user can set | a field, with a resting edge so it does not read as text |
| A color the user can set | a swatch of it that opens a picker, its `#RRGGBB` on hover |
| A choice between a few named states | a segmented control with an edge (`panel.segmented`) |
| A fact the user can only read | a row of `panel.READ`, as tall as its words |

**A segmented control has a visible boundary.** A bare run of text buttons reads as a label
until it is hovered, so the edge, the divider between segments and the filled segment for the
one in force are drawn by `panel.segmented` and never by a call site. No module calls
`ui.toggle` itself.

Mixing them — a checkbox here, a text state and a button there — makes the reader work out
three times what one convention says once.

**A select hides its options, which is right until the options are the information.** Where
the choice is between named things a reader already knows — a theme, a sort order, a table —
one line showing the current answer is what a panel wants. Where the label of each option is
itself the thing being decided, a closed control shows one at a time and makes the reader
open it to compare. Then the set goes on screen whole, as radios: the media source dialog
picks the *filename* a file will be saved under, and the two candidates differ in the middle
of a long name. Reading them against each other is the choice. The test is not how many
options there are — it is whether the reader has to hold one in their head to judge the next.

**There is one dropdown, `manager.kit.dropdown.Select`, in two looks.** `FIELD` is the value in
a row of a panel or dialog: a bordered box one row tall with the text centered in it.
`OUTLINED` is a control that stands on its own, such as a toolbar's picker or a box that
fills a panel. A call site names the look it wants and never writes `dense`, `borderless` or
`outlined` itself; `panel.select`, `multi_select` and `combo` are the field look for a fact
row, and every other picker class in `panel.py` and `tag_chips.py` derives from `Select`.
Quasar sizes the text of a select for its own 40px box, so every other height has to
correct it, and a copy styled manually at the call site drifts. A select that carries a label
shows it over the value by design, so its text is not centered alone.
`tests/manager/test_every_dropdown_is_one.py` fails on a raw `ui.select` in `manager/`, and
the pages drive measures the text of every one-line, unlabeled dropdown on the pages it
visits against the middle of its box.

**A chip field offers only what is not chosen, and the x on a chip is the only way out.**
Any control that takes several values from a list is a `dropdown.Select` with
`multiple=True`, which draws the chosen ones as chips and drops them from the open list. A
list that kept them would make picking one a second time remove it, so a click in the list
means add and the x means remove. `Select` does this itself, so the tags in a panel, the
ignored Chrome flags, Skip and the wizard's multi pickers all behave alike, and a new
chip field gets it by being one. `tests/manager/test_a_chosen_chip_leaves_the_list.py`
fails on a chip field that offers a chosen option, and on a module that draws chips
without going through `Select`.

**The row is the box, not the value.** Every value centers in a row of `--fact-row`
whatever it holds, so the air around one does not depend on its kind. At 26px a text
value sat 21px in the row while a chip carried its own padding and read roomier — the
same rhythm, felt as two. Content that wraps is the only thing that grows a row.

**Fact rows keep one rhythm everywhere they are drawn** - 8px between rows, each row as
tall as what it holds. `tests/invariants/test_fact_rows_keep_one_rhythm.py` fails on a
rule that spaces fact rows for one surface only.

**A picker's options say what they are for.** Where a view, a preset or a mode has a
description, it goes on the option as a tooltip rather than only on the one already
chosen - the reader is picking, which is the moment the difference between two of them
matters. `panel.DescribedSelect` is the control. The wrapping is the standing tooltip
treatment, 340px and `pre-line`, so a long line wraps rather than running off the screen.

**A dialog is the size it opened at.** Nothing in a modal may change its height after it
renders. The reader has already looked at the buttons and started moving; a field that
grows when it finds something to say moves the one they were going to press. Quasar draws
the row under an input only while something is in it, so a field that can show an error -
or a line about what was typed - declares `bottom-slots` and keeps the row from the start.
The error and that line share it, which is right: while something is wrong, the thing that
is wrong is what the row is for.

**A path can be typed, or found with Browse.** Typing stays the answer for a share this
device cannot see; the mark is what stands between a typo and a record that never worked.
`panel.path_field` is the control: a tick or a cross in the input's own append slot,
updated as it is typed, the reason on hover, and nothing at all while the box is empty.
`wants` names what should be at the end of it - `dir`, `file` or `exe` - because "that is
a folder, and this wants a file" is the useful half of the answer. Browse opens
`manager/kit/folder_picker.py` on this device's own disk, listing what `wants` asks for and,
for a `file` field, narrowed to `suffixes`. The same mark answers for a stored path once
it has been written, from the same vocabulary.

A path field shows its **end** while it is not focused: the folder you are in matters more
than the drive it hangs from, and a cut start is marked with an ellipsis. Focused, it scrolls
back to the caret like any input. Hover over its text answers the whole path, then whose it
is, and **Copy path** sits beside Browse. Each control answers with one tooltip, so the
path's own sits on the input rather than on the field around Copy and Browse. A picker that
draws a tick or cross in its end slot does the same: whose it is sits on the value, and the
mark keeps the one that says why (`panel.value_hover`).

A path with a default shows it, quiet, as the field's text, and whose it is on hover. Typing
the default or choosing it clears the setting; any other path draws the dot before it and
Reset after it. Browse opens at the field's path, else at the default, else at the nearest
folder above a default that is not there yet, saying nothing of the climb.

A path drawn as text, not typed into, is its end with the start ellipsised, the whole path
on hover, and **Copy path** after it. `panel.path_text`, `panel.path_value` (for a fact row)
and `panel.copy_text` draw it; a bare `ui.label(path)` in a fact row does not.

The picker's path is a field, not a face to open: beside Up, the folder you are in as text
with that folder's icon, the check or cross of the path fields on the right, then **Copy
path** and a caret that opens **Places**. Clicking in selects the whole path. Typing or
pasting a path and pressing Enter goes there - a folder opens, a file of the wanted kind
opens its folder with the file chosen, `~` is Home, a bare name is looked for in the folder
you are in, and the quotes Windows' Copy as path adds are dropped. The mark says whether
Enter can go there, with the reason on hover. Enter on the path as shown takes it, **Choose**
takes a typed path, and Escape puts the shown path back (with nothing typed it closes the
dialog). Places lists where browsing can start over, each once, the one you are in ticked
and no folders above it; places of one name are named by their path. Home reads **Home**,
not the account's name.

The picker's **New folder** is the dialog's aside, beside Cancel: it adds a row named
*New folder* to the listing, selected for typing, the way Finder and Explorer do, and
Enter makes it and goes in. It is offered wherever the picker lists folders through a client
that can make one (`ApiClient.make_folder`), and making one takes the permission adding a
library folder takes.

A field that names something *to be created* is not this. The import dialog asks what to
call a new game's folder, where finding something already there is the failure, not the
success.

**A failed write shows what the API said, never what HTTP said.** `raise_for_status`
discards the body, so "No Visual Pinball on this device…" reached a user as
`501 Server Error: Not Implemented for url: /api/v1/games/…/script` — a status line and
our own route. `HubClient` raises `HubError` carrying the API's message, and a surface
shows that.

### One name and one direction per fact

A fact keeps **one name and one polarity on every surface**. The surface picks the
control — a column, a switch, a chip, a menu verb — never the vocabulary.

Three facts about a table were being described three ways, two of them inverted:
`Hidden` in the grid was `Frontend visible` in the panel (flipped again on its way to the
API) and "Hide from the frontend" in the row menu; `Missing` in the grid was `File on
disk: Present` in the panel. Crossing between two surfaces meant turning the question
over twice, and the column picker offered a third set of words for the same columns.

- The **name** is the fact, in the shortest true word: `Default`, `Hidden`, `File`.
- The **direction** is notable-true, which is the grid's rule — a tick on every ordinary
  row says nothing. A switch reading "on means restricted" is fine; `Clear NVRAM on exit`
  already does.
- The **words belong to the module that owns the fact**, and every surface reads them:
  `manager/kit/game_tables.py`, `manager/kit/media_ownership.py`, `manager/kit/table_features.py`.
- A **menu item is a verb that produces the state word** — `Hide`, `Clear choice` — not a
  sentence explaining the mechanism.
- **Never index a word pair by a boolean at the call site.** Ask the vocabulary for the
  word; a `pair[not present]` is how a file that was on disk came to read `Missing`.

A control that cannot hold every state of its fact is the tell that the vocabulary is
about to drift: a switch stood for a three-state default — chosen, automatic, not the
default — so it said something else, and the drift started there.

### The bar above a grid is two rows

Two rows, two questions. The top is **the lens**: which view, the menu that configures it,
and the search that narrows it. The bottom is **the subject**: what the view is for, how
much it holds, and what you can do to it. A control goes in the row whose question it
answers.

- **The view's description is text, never a field.** It reads the same whether the view is
  ours or one you saved, and it stretches to take whatever width the controls beside it do
  not. A name and a description are set where a view is saved and changed from its menu,
  so the bar never carries a form control.
- **Each row has a right-aligned end.** Search ends the top row; the count, then the
  selection actions, then the page's own actions end the bottom one.
- **Discovery's grids have no box in their bar.** Its search finds authors, makers, themes
  and games as well as rows, so it sits at the right end of the tab row, above the line, on
  every tab and on an author's page (`discovery_search.SearchBox`). Their bar is one row
  while the view, its description and the count fit, and two when they do not.
- **A key to the marks a view draws sits on the description line**, right after it, and
  the free width falls after the pair. It says how to read what is on screen, which is
  the same question the description answers; in the right-hand end it reads as one more
  control beside the count. Each entry still carries its own explanation on hover, and so
  does each mark in the grid - the key is what says a vocabulary exists at all, which a
  tooltip cannot do, on a touch screen or anywhere else.
- **What gives way is decided, not incidental.** The search box shrinks first and
  furthest - it is the only control here that loses nothing by being narrower. The view
  picker follows, and never below the width where a view name stops being readable.
  Nothing else shrinks.
- **Two menus, two glyphs.** `tune` is everything about the view: its columns, its name,
  its description, how it draws, and saving or deleting it. `more_vert` is what you can do
  to the rows you have selected, and it appears only when some are. The same glyph for
  both is how a reader learns that neither is worth reading.
- **The count is what the selection's actions act on.** A row that a search, a filter or
  a view hides stays selected, because selecting across several searches is how a selection
  gets built, and the count says so: *5 selected, 2 hidden*. With none hidden it counts
  against the rows on screen: *3 of 9 selected*.
- **A setting that changes how the view draws lives in the view menu**, not on the bar.
  Ticks against thumbnails is the case: it is chosen once and left, so a click's depth
  costs nothing and the bar keeps the width.
- **A choice about how every list draws lives in Settings > Manager**, beside Date Format.
  A choice about one view lives in that view's menu, which may carry a link to the former
  but never a copy. `panel.menu_link` draws that row: an item's look, because accent in a
  menu is the current value, and an address like any link.
- **An action is an icon and a tooltip, until the page is empty.** Three spellings of one
  verb fit no rule and cost the width the description needs. The empty state keeps the
  full text button: that is where somebody meets the page for the first time, and an icon
  there is a guess.

### An action sits with what it acts on

Where a verb goes is decided by what it changes. Nothing is gathered under a heading
called Actions, which names the mechanism and says nothing about the subject.

| It acts on | It goes |
|---|---|
| the whole grid | the bar above the grid: add, the selection's `more_vert`, refresh |
| the panel's subject | the `more_vert` in the panel header, the same list as that row's menu in the grid |
| a section | a strip closing the section, with no heading |
| one row | an icon at the row's end, repeated in the row's menu |
| one value | inline, beside the value |
| a dialog | the footer |

- **A Needs You row is the exception to "one row".** Its fix (Download Media, Update, Download) is
  the first entry of the row's menu and the row's end carries only `more_vert`.
- **One list per subject.** Each subject's module has an `acts()` returning `panel.Verb`s;
  `panel.verb_menu` draws it on the grid row and `panel.subject_menu` in the header. A
  verb that only makes sense in one of them says so with `in_panel=False`, as Rename does
  where the panel already edits the name in place.
- **The selection's `more_vert` draws that list too.** `panel.selection_menu` builds it
  from the subject's `acts()` given the rows chosen, then adds *Clear selection* under a
  rule of its own; no page fills one manually. A right-click on a selected row, while several
  are selected, acts on the selection under a header that counts it (*3 copies*); on an
  unselected row it acts on that row alone and leaves the selection. A verb with no meaning over
  several (`Verb.one`) stays in place, dimmed and reading *One at a time*; one that asks
  something about each row walks the selection, a dialog per row (`walks.each`). A grid
  draws checkboxes only when it is given a selection handler, and has a header menu
  wherever it has a row menu.
- **Danger goes last, under a rule, in red.** A verb that cannot run right now stays in
  the list, dimmed, with the reason under it, so nobody wonders where it went. One this
  install can never perform is not in the list at all.
- **One word per act.** **Delete** means removed from disk, or removed permanently where there is
  no disk. **Remove** takes it out of the library and keeps the files. **Forget** stops
  tracking something VPinFE does not own. **Uninstall** is for a theme.

A verb in a panel follows the value it changes — same column, one gap after it. It is not
pushed to the panel's edge for a tidy column of verbs: that assumes the value fills the
row, and a chip does not, so the verb ends a panel-width from the state it changes and
drifts further as the pane is widened. Fields still line up under this rule, because they
stretch to the same width.

- **One action per fact.** A second belongs in the row's context menu.
- **The verb produces the state word** — `Chosen` is cleared by "Clear choice" and
  reached by "Choose this one". A verb naming a mechanism ("Let VPinFE pick the default")
  is a sentence, and a menu item is a name you read and click.
- **Weight follows the target.** An action on a *field* or a *section* takes
  `.manager-action`; one sitting beside a **state** takes `.manager-action--inline`, the same
  control at the chip's type scale. It keeps its resting edge: a verb with no edge reads
  as a second value, and this panel is worked in rather than read. What made the pair
  feel wrong was the type, not the border — 12px/500 beside a chip at 11px/400.
  It hugs the chip at `--target-inline` rather than taking `--target-min`, which is the
  only place in the Manager below the pointer floor; `@media (pointer: coarse)` gives the
  floor back where the pointer is a finger.
- **Section actions sit in their own strip** under the content, as a media slot's do.
  Position is then what tells a row action from a section one.

`tests/invariants/test_actions_sit_with_their_subject.py` fails on a section or heading
called Actions, on a subject whose header menu has no matching row menu, and when the
count of buttons built outside `panel.py` rises.

### A link says whether it leaves

- **Inside the Manager, a link is an anchor in `--accent`, with no icon.** `panel.link`
  builds it and its address comes from `deeplink.query()`, so opening it in a new tab,
  middle-clicking it and copying its address all work. A click handler on a label can do
  none of those.
- **A link that leaves the Manager is marked with `open_in_new`** after its words, and
  opens a new tab. `panel.link_out` builds it. Unmarked stays in the app; marked leaves it.
- **The chain is not a link.** `link` is the icon for binding two records - a game to its
  VPS entry, a table to its release - so Match and Change match wear it. A link that goes
  somewhere never does.

`tests/invariants/test_links_go_through_the_constructors.py` fails on an anchor built
anywhere but `panel.py`, and the nav rail in `page.py`.

### Every button carries an icon

- **An icon and its words.** The words may go only where the control repeats on every row
  and the icon is one everybody knows - `more_vert`, `close`, `play_arrow` - and then it
  carries a tooltip. Dialogs are no exception: the confirm verb takes its icon and Cancel
  takes `close`. Menu items take none.
- **One icon per verb, from `manager/kit/verbs.py`.** A verb that means what another means
  takes that one's drawing: Accept, Done, Choose and Keep all draw `check`, and Cancel,
  Close, Skip and Discard all draw `close`, because they are the same act in different
  words. The label is what tells them apart.
- **A verb nothing draws falls back by what it does** - open `open_in_new`, change `edit`,
  add `add`, remove `remove` - rather than getting an icon invented for it. The words carry
  the meaning; the icon only says this is a button.
- **A hint says what pressing it does.** Every `panel.action` and `panel.remote_action`
  carries `hint=`, drawn as the button's tooltip and worded from what the handler does
  rather than a restatement of the label.
  `tests/invariants/test_every_button_says_what_pressing_it_does.py` fails on one with
  no hint.

`tests/invariants/test_every_button_carries_an_icon.py` fails on a button with words and
no icon, and on an icon name `verbs.py` does not declare.

### A dialog is drawn by the shared frame

- **Every dialog opens through `dialog.opened(title, ...)`**, which gives it the panel's
  ground and a title and nothing else. What goes inside is what a panel holds: rows from
  `panel.facts`, fields from `dialog.field` (the panel's own, answering nothing until the
  dialog does), lists from `panel.select`, section names as `panel.HEADING`.
- **The title names the act and its subject**, as a fragment with no full stop, and a
  name in it is quoted: Import into “Attack from Mars”.
- **Every action is in the footer**, `dialog.footer()`: Cancel first and quiet, then the
  one answering verb, filled. A second act the dialog offers, like Clear on a picker, is
  `dialog.aside` and sits on the far left. An act on one row of the body belongs to the
  row and is a `panel.action`.
- **Enter presses the answer and focus moves to the first field** - `dialog.enter_presses`
  and `dialog.focus`, because Quasar's own autofocus does not reach into a dialog.
- **Typed input takes `ignore_outside_click`**, so a stray click on the backdrop cannot
  throw it away and Escape still cancels. `persistent` blocks Escape as well and is for a
  run in progress or an answer that cannot be left unsaid. A dialog that does something
  other than close when Escape leaves it registers that with `dialog.on_escape`, which
  needs `ignore_outside_click` too, since a `persistent` dialog fires no Escape event.
- **`wide=True`** for a list or rows of several facts, **`full=True`** for an editor with
  a toolbar of its own.
- **A wizard keeps its frame and redraws its body and footer at each step**: Back where
  there is history, Cancel where there is not, Next, and the act's own verb on the last
  step. It draws its own title, since the title changes. Its size, its step row, its
  welcome and its picture choices are the next four entries.
- **A flow that can be left and returned to passes `aside` to `wizard.open_dialog`**: a
  quiet verb on the far left of every step, taking Cancel's place, and `open_dialog`
  answers whether the flow ran. Setup's is Finish later, and Close when it is asked again.
- **A step that answers `confirm` with no `summary` is the last one**, and its answering
  button reads the `confirm`. A run that answers at once ends on "Done" over a Close
  button; `close_when_done=True` closes the dialog instead, for a flow like Setup whose
  page already shows the result.

`tests/invariants/test_dialogs_are_drawn_by_the_frame.py` fails on any `ui.dialog()`
outside `manager/kit/dialog.py`. The enlarged picture in `mediaview.py` and the mobile device
remote's sheets are the exceptions, because neither asks anything.

### A wizard is one size on every step

- **The card is `manager-wizard-card`, with a height of its own that no step changes.**
  A frame that fits each step moves the footer from one to the next, under a pointer that
  was on it a moment ago. The height gives way only to a short window. The body scrolls
  inside the card; the footer is pinned under a rule.
- **The title is the step's question at the title size, and `help` is the one sentence of
  why under it** (`manager-wizard-lead`). The frame has no title bar, so nothing else says
  what is being asked.
- **Every flow through `manager/extensions/ext_action.py` is drawn this way**: the Library Importer
  and any extension action. A flow does not ask for it and cannot opt out.

### A wizard says where the person is in a row of segments

- **One segment per named step, with its name under it,** across the top of the frame. The
  open step is lit with its name in ink, a done step is a `manager-link` and reopens, and
  a step still ahead waits in the track color and does nothing when clicked.
- **Drawn only where an answer carries `steps`.** A flow that cannot say how many steps it
  has sends none and draws no row, and no flow shows a count: "step 2 of 4" is wrong the
  moment a choice drops a step.
- **Narrow, a name is cut with an ellipsis** rather than wrapped, so the row is the same
  height on every step.

### A wizard greets once, centered

- **A step that answers `welcome: true` is centered.** The title is at display size
  (`--fs-display`), `help` is the lead under it, and the question - the label of its
  choice - is a region title over the tiles. It is meant for a flow's first step; core does
  not check that it is.
- **VPinFE's mark is core's to place.** `wizard.open_dialog(mark=True)` draws it over the
  welcome; `ext_action` never passes it, so an extension's welcome is centered without it.
- **The welcome sits in the middle of the frame and the steps after it start at the top**,
  and a welcome taller than the frame scrolls from its top rather than losing it.

### A choice that carries pictures is tiles or rows

- **An option's fourth element names its picture.** Core draws `desktop` and `cabinet`
  itself; any other name is a Material icon. A flow names a picture and never supplies a
  drawing, so a name that is not a plain icon name draws nothing.
- **The layout follows the step.** On a welcome the options are tiles in a grid, large art
  over the name and its line, with the question over them. On any other step they are rows
  with a small picture and a check mark. Alone on a step, the step's own title is the
  question; beside other fields, the choice's label heads the rows so it can be told from
  them. The group is named for assistive technology either way.
- **Both are the pick row** (`manager-source-row--pick`): the edge and the wash of
  `manager-source-row--chosen` say which is picked, the check mark says it again where
  color alone would not. A choice with no pictures stays a radio group.

### What a step finds, and where a value came from

Setup draws in this process, so a step may carry callables that cannot travel as JSON.

- **A choice's option can ask the next thing under its own row** (`details`), shown only
  while it is the one picked, and `gate(values)` holds Next until the answer is complete.
  A path asked under "Somewhere else" is a step's detail, not a page of its own.
- **A line saying what a step found, or that it found nothing,** is `manager-wizard-found`:
  an icon, the finding, and what it looked in. It is drawn above the rows by `before`.
- **A value that was not typed says where it came from, under its field** with
  `manager-wizard-src`: an icon and a few words, with one line of how to do better in the
  help color. Typing over the value drops the label, because it is no longer the source's.
  A better value on offer is the same line with an action beside it
  (`manager-wizard-src--offer`).
- **Pages that answer one question share a name in the step row** (`holds`), so the row
  does not grow a segment for each page a choice can add or drop.

### One confirmation, and one field

- **Anything that cannot be undone asks first, through `manager/kit/confirm.py`.** Four dialogs
  had been written to the same shape with different spellings — one awaited a bool and
  one took a callback, the buttons swapped `no-caps`, the help lines used two classes.
  The question is a sentence in sentence case (never the section-heading treatment: a
  dialog that shouts its question is not asking), the detail line says the consequence,
  and named files are listed where a count would hide which ones. The confirm button is
  **the verb that does the thing**, never "OK", so it reads without the question.
  A dialog that collects a *value* — a name, a rating — is not this and keeps its own shape.
- **A text field carries a full edge, like an action.** It reads as something you can act
  on, and it hovers and focuses the same way. A single bottom rule was the quiet version
  of that idea and read as a section divider — full width, between two rows, which is
  exactly what a divider is. It sits at `--field-h`, in the fact rhythm rather than above
  it, and `@media (pointer: coarse)` raises it with the inline action.

### An act that can be taken back says so, with Undo

A reversible act does not ask first. It does the thing and says what it did, with Undo on
the message. `manager/kit/undo.py` draws that message and nothing else does: the ordinary green,
or amber with a mark where the act wants a second look - a game added to a smart collection
becomes an exception to its rules - and each act hands it the reversal that puts back
exactly what it changed. What cannot be undone still asks first, through
`manager/kit/confirm.py`.

### A file moves between a game and its tables, one way each

A game's file can be handed to its tables, and a table's own can be handed to the game.
Both move the file in its folder, and both say what moved with Undo.

- **A game's file** has **Choose Tables...**; with one table to choose it reads **Move to a
  Table** and moves at once. **A table's file** has **Move to Game**, with no dialog.
- **Offered for** media on every page, the backglass, the ScoreView file and the settings
  file. A script goes to a table only, since at the game level every table without its own
  would run it; a POV, since only the table named like the folder reads one there.
  PuP packs, alt color, alt sound, music, ROMs and patches have no item.
- **Choose Tables...** is one checkbox per table under **Used by**, each table once, with
  what it ends up with in words under its name. Saving makes the ticks true. The file stays on
  the game when every table without its own is ticked, replacing the own file of any table
  ticked with one. Otherwise it leaves the game: renamed for the one table ticked, copied for
  each when several are. A game with a single table has nobody to share with, so it is given
  the file under its own name. A replaced file goes to Backups with no confirm, and Undo puts
  both back.
- **A backglass at the game level** is found by folder name by VPX's B2S plugin (from
  10.8.1-5436) and its B2S Legacy plugin (in the source since 2026-09-04, after that tag).
  The classic B2S Server 2.1.6 on Windows does not look for it.
- **Off, with the reason as the hint:** a game with no tables, files from two games, two
  tables' files of one kind (*Keep one*).
- **A table named like the folder is not a choice.** Its file and the game's are one file,
  so its row follows the others and is never ticked by hand.
- **A failure puts both files back.** The move is refused while a table of the game plays.

### A copy is kept in one list, and every panel opens onto it

Every copy VPinFE keeps is listed once, on System > Backups, and a game, a launcher or a
theme answers for its own on its panel. The panel's **Backups** row is a summary: what is
kept ("3 copies") over when the newest was taken and why ("Newest 2 hours ago · Before a
restore"), and a click anywhere on it opens that list in a dialog narrowed to the one thing.
The dialog has **Back Up Now**, **Restore** and **Open in Backups** for the whole page, so
the verbs, the columns and the Undo on a restore are the ones System > Backups has. A row with
no copies opens it too, because that is where Back Up Now is. A new kind of copy gets a row
the same way and no list of its own.

Back Up Now asks only for a name and a note, and no copy is taken on its own before an edit:
Back Up Now and the Undo on a restore cover it. The row is drawn by `manager/backups/backup_rows.py`.

### A summary row opens its detail in a dialog

A panel field that would hold a count, a date and a verb or two holds a summary instead:
the answer to the question the person came with on the first line, a `manager-summary-sub`
detail under it, and `verbs.DRILL` at the end. The whole row is the target (`panel.Pick`),
so it takes Enter and Space and a hover band, and the verbs live in the dialog it opens.
A time on a summary is `when.ago`, and the exact one is in the dialog. `manager/backups/backup_rows.py`
is the first user, and `manager/security/signed_in_rows.py` the second.

**The control lights as a whole on hover, and the label does not change.** The band is the
summary itself, reaching 8px past its text, so it clears the label beside it. A row whose
value is a summary keeps its label at rest; lighting both says two things are being
pointed at.

### A grid in a dialog fills its card

A dialog grid declares one column to take the leftover width (`flex=1` on `grid.column` or
`grid.identifier`), so the last column does not stop short and leave a gap down the right
of the card. Its declared width is its floor. A width a person sets on that column ends
the fill, and only a person's resize is saved: a width the grid worked out to fit is
not. The copies in Backups and the browsers in Settings > Security are drawn this way.

### A press that is somebody else's asks first, and has no Undo

Giving a key to one action takes it from another, and an Undo offered after the answer would
be a second decision about the same change. So the question comes first, through
`manager/kit/confirm.py`, in the verb of the act - **Move** - with the title quoting the names it
moves between and the detail stating the cost: who has it now, and which action is left with
nothing mapped. A yes does it and the page says what it did; there is no Undo on it. Revert
on a row asks the same way when it would take a key back from another row. Where a change
costs nothing to anyone else, it does not ask.

`confirm.ask(..., second=(label, icon))` is for a question with two ways to say yes. It then
answers a `confirm.Answer` where it otherwise answers a bool.

Enter answers a `confirm.ask` that is not `danger` - Move, Revert, Add - and does not answer
one that is, so a destructive verb is never one a habit reaches.

### A drag carries addresses, and a menu does the same

A row dragged out of a grid carries each row's Manager address as `text/uri-list`, with
`application/json` beside it, so it means something wherever it is let go. The drop side
reads the ids back out of the addresses and refuses rows from another install. Every drag
has a menu entry doing the same thing, so dragging is never the only way.

A drag always shows what it holds: the row's name beside the pointer, with a count when it
holds more than one. The target is the whole panel that shows the thing, not the section
listing its members, and it is ringed while the drag is over it. Between rows a line shows
where the rows will go; let go anywhere else on the panel and they are added, at the end
when the list is in Custom Order. Anywhere that is not a target refuses the drag, so the
browser never opens the address it carries.

### A screen is put on a part of the cabinet, not picked from a list

Wherever a screen is chosen for a window, `manager/settings/screens_picture.py` draws the parts and
this device's screens, and a screen is dragged onto the part it serves. The caller hands it
the screens, what is on each part and the acts to report to, and the control writes nothing
itself, so the Displays page, Setup and a VPX page can each mount it over their own storage.

A part takes one screen and a screen is on one part, so putting a screen where another is
sends that one to where this one came from. Clicking a part opens a menu that does the same,
and an empty part reads **No screen**.

A screen is called by its model name, and by its size when it has none. It is numbered from
1, as a person counts, and a window stores that number less one. The picture asks
Is the Playfield Rotated Correctly? under the screens, answered with a test card on the
playfield screen that a key or a click closes, and three turns, and the playfield's box keeps a small card turned to the
stored answer, whatever Cabinet Mode says. **Looks Right** reopens the open windows when the
saved turn is waiting on that, so the playfield picks it up; closing the card another way keeps
the turn and leaves it waiting. A launcher's page for Visual Pinball draws only
the small card, whatever Cabinet Mode says.

Window placement is a set of rows, `manager/settings/placement_panel.py`, that a page mounts under the
screens picture: Displays does today and the Screens step of Setup can. The rows write
nothing themselves except VPinFE's own rules file. The one line in the person's compositor
config goes in on **Add it** only, and **I'll add it myself** shows it with a copy button, the
only option when the config cannot be written. Screens that cannot be read leave both actions
refused, since rules that place nothing would replace working ones.

A launcher that keeps its own screens draws the same picture over its own storage, and
**In the playfield** joins a part's menu where the program can draw a window inside the
playfield's. Where the launcher's screens are not VPinFE's, the part's name takes the
*differs* color and a line under the picture says **Different from VPinFE** beside **Match
VPinFE**. A change to VPinFE's screens moves the launcher's only where it still matched. A
topper is compared only where VPinFE has a screen for one; with none, the launcher's is as
the person set it.

A length that is a setting of the device, such as a screen's size, is shown in centimeters
or inches by Settings > Manager > Units, which follows the region until it is set. The page
that shows one puts the choice beside it as a toggle.

### A value is written when it is set

No save bar, but for two drafts, below. A control that changes a value writes it, and
anything that cannot be undone asks first. Two answers had shipped — the pane wrote on
change or on blur, Settings collected edits into a dirty set behind Save and Discard — so
whether an edit had taken effect depended on which surface you were on.

Free text settles on blur, with `debounce=0`: nicegui's model is only current if every
keystroke reaches it, and reading it on blur without that gets whatever the last sync
happened to hold. Several lines settle as you stop typing instead, because a paragraph has
no natural moment of leaving. Everything else writes on change.

The first draft is a collection's rules. A rule is several rows that mean nothing until they
are all there, so the rows wait behind a bar held at the foot of the panel - how many games
match, join and leave, with Cancel and Save Rules - while the grid marks the collection
*Not saved* and leaving the page asks. The rest of that panel, its order and limit
included, writes on change.

The second is the text of a game's file in the editor on its stage. Half a line of a script
is a table that will not start, so what is typed waits behind Save, with Cancel beside it
and *Not saved* when there is something to save. The draft is kept per file while other rows
are picked, the Assets grid marks that file *Not saved*, and leaving the view or reloading
asks, listing the files. Save is also Cmd-S or Ctrl-S inside the editor.

### Chips say what the absence costs

A chip's color is about consequence, not about whether something is merely true:

- `manager-tier--on` — present, installed, in use
- `manager-tier--off` — absent and unremarkable. Quiet, but filled and outlined enough to still
  read as a chip
- `manager-tier--unknown` — not determined yet, which is not the same as "no"
- `manager-tier--warn` — absent and worth fixing
- `manager-tier--bad` — absent and breaking

Green is present. **Amber means go and fix this, and nothing softer.** Each fact keeps its own
words: a rom is *Installed*, a script is *Extracted*, a file is *Present*. Do not flatten them
to Yes/No.

State chips are not media tiers. `manager/kit/media_ownership.py` answers "whose file is this", and its amber
means the exception worth spotting in a map of twenty.

**Chips are for panels.** A grid draws the same state as a colored word (*A state column draws
every row*), in the same color a chip of that state wears here.

### A finding leads the panel, with its fix

What is wrong with a subject comes before anything merely true about it: one
`.manager-attention` block per finding, at the top of the section it belongs to, ending in
the act that fixes it. *Add* for a ROM that is not installed, *Forget* for a file that is
missing, a link to the newer release for a table with an update.

- **Only faults.** Hidden, not the default, a slot left empty: those are choices or
  coverage, and a block that fires on them teaches everyone to skip it.
- **A finding nothing here can fix still shows, with no action.** A location that is away
  is fixed by bringing it back, not by a button.
- **Once per panel, once per cause.** A game's panel carries its tables' findings; a
  table's panel carries its own, and the game's section above it does not repeat them.
  A finding several tables share, fixed by one act, is one notice that says which tables
  it is about. A finding fixed table by table stays one notice per table.

### A kind switched off is on no surface

Settings > Library > Assets and Media say which kinds of file a library collects, and
every surface that reports a kind honors it: the Assets, Media, Games and Tables grids'
columns and rows, the coverage card with its percentage, and the table panel's Assets block
with the count in its header. A gap in a kind nobody collects is not a gap.

- **The API serves every kind either way.** A theme asking for a topper still gets one, and
  the files stay on disk. Only what the Manager shows follows the setting.
- **`table` has no switch**, and a table that will not launch still says so on the panel's
  Will run row, whatever is switched off.
- **A new surface joins the list** in
  `tests/invariants/test_every_surface_honors_the_kinds_a_library_keeps.py`.

### Marks, and what color one is

**A binary fact is a tick; a fact with more answers is a shaped circle.** Presence is a
checkmark wherever the Manager says it — asset columns, media columns, feature columns — and the
circles (`.manager-mark--full`, `--outline`, `--set`, `--dashed`) are for a vocabulary with more
than two answers to tell apart, where a reader has to distinguish them at a glance. A fact
that stops having three answers moves to a tick; one that gains a third moves the other way.

**A tick is `--positive`, never `--accent`.** Green is present, and accent is the current
value — a matrix of hundreds of true cells is the clearest case there is of a color losing
its meaning by being everywhere. Accent belongs to the *chosen* one: the menu checkmark, the
default mark, the lit star, the media tile in use. Those are different facts and take
different colors, which is the whole distinction:

| the fact | the rule |
|---|---|
| this thing is **present** | green (`--positive`) |
| this is the one **chosen** | accent |
| absent and worth fixing | amber, and nothing softer |

*Accent is doing more than that today* — headings, focus, hover — so read the rule as what
new work should follow, not as a description of every existing use.

**Do not borrow a shape that already means something else.** The marks are deliberately
shared across vocabularies, named for the shape rather than the meaning, so the words differ
and the drawings do not. That only works while each vocabulary keeps to shapes whose existing
sense it can live with: a dashed circle means **Missing** in media ownership, so a table
nobody has parsed cannot wear one — it is not missing. Where no shape fits, a character often
says it outright; `?` needs no legend at all.

**Nothing explains a blank in a legend.** A state drawn as nothing gets no legend entry — a
blank cell is not something anybody looks up, and a legend line beside empty space reads as a
mark that failed to render. The legend names what is drawn, and only that. A *state column*
is the one place a blank is named, in its header tooltip and as the last row of its filter,
because there it means one thing: the fact does not apply to the row.

**A legend names the states the library has**, not every state the vocabulary declares. Build
it from the rows on screen: a state that never occurs is a line spent on every visit
explaining something invisible, and one that appears mid-scan explains itself the moment it
does.

**A filter offers the marks the grid draws.** If a cell renders nothing for a state, its entry
in the funnel carries no mark either — otherwise the filter promises a drawing that is never
on screen. The choice itself stays: what it loses is a picture, not the ability to pick it.

### A state column draws every row

A column that holds one of a few states of one fact - a match, a ROM, a file in use - follows
one rule, so a reader who has learned one grid has learned them all. `manager/kit/states.py`
builds it from one `Table` per fact, which gives the cell, the formatter, the header tooltip,
the filter, the sort and the legend, so they cannot drift.

1. **Name the column for the fact,** in the panel's words: VPS Match, In Use, ROM State. Never
   for one of its states, and never *Status* on a page with more than one such column.
2. **One set of words per fact,** title case, owned by the module that owns the fact and used
   by every page that shows it. The act produces its state: Match gives Matched, Clear Match
   gives Cleared.
3. **Every row draws its state, the good one included.** In place is a tick, not known yet is
   a question mark, and every other state is a word.
4. **Blank means one thing per column: the fact does not apply to the row.** A two-state
   column named for its true state, Hidden or Shared, may leave false blank.
5. **A tick means in place and nothing else,** always green. A choice or a fault is a word,
   even in a two-state column.
6. **Color by consequence,** as the panel's chips already do: green for a tick; amber where
   VPinFE needs you and you can fix it; red where play breaks; quiet for a choice or for
   coverage; plain for a neutral fact. A grid colors the word, and chips stay in panels.
7. **Values run from most in place to least,** then Unknown, then blank. A lifecycle keeps
   its own order. Sort, filter and legend follow it.
8. **The filter is the checkbox list,** one row per state in that order, with the cell's tick,
   question mark and color, the whole library's count beside it (none at zero), and a last row
   for blank, named for what blank means: No ROM, Not Listed, Not Set. A state the column
   cannot hold gets no row. A count appears only inside an open filter, never in a cell, a
   header or the nav: a count that is always lit is noise.
9. **Tooltips:** the header says what the column covers, then one line per state in order,
   *Word - meaning*, with the blank's line last. A cell's tooltip is *Word - why*, on every
   tick and question mark and on any word with a reason of its own. A filter row's tooltip is
   that state's header line. The separator is a spaced hyphen everywhere.
10. **A boolean is a token.** A column's value is the state's token, never `true` or `false`,
    which AG Grid draws as a checkbox. `states.Table.column` draws no checkbox, and a page that
    builds a column some other way turns it off.

### An icon at the end of a row

**One size, `--icon-row`.** A way out, a verb about that row, a mark - whatever the control
is, it takes the same size. Two of them side by side at different sizes read as a ranking
nobody intended, and the reader has no way to learn what the ranking means because there
isn't one. The box around the glyph is what makes the target, there being no words to
widen it.

**`--ink-3` at rest, accent under the pointer.** This follows from the rule above rather
than bending it: accent is the one *chosen*, and a picker drawing forty results puts forty
of these down the right edge. A column of accent there is the same failure as a matrix of
accent ticks - a color that appears on every row has stopped saying anything, and it pulls
the eye off the names being scanned.

**A link in prose keeps accent.** The two are not in conflict. A link inside a sentence or
a field earns accent by standing out *from the words around it*; a control at the end of a
row has no words around it, and the region it sits in is already distinct. So the
treatments differ because the jobs do.

**The glyph carries the kind.** `open_in_new` says it leaves without being told to; the
tooltip says the rest, and it is not optional on a control with no words.

**The link out is last.** A row that has a way out to a page, with a verb or a download or
a mark before it, puts the way out at the right edge in every list, so the eye finds it in
one place whichever row it lands on. The verbs come first, in the order of how often they are
pressed.

**Every icon size is named, and a check reads the whole sheet.** Not one size for all of
them - a nav glyph, a state mark and the art standing in for a missing picture are
different jobs, so each job has a token and the token is where its value is decided:

| the job | the token |
|---|---|
| a control at the end of a row | `--icon-row` |
| a glyph beside the words of a dialog's footer button | `--icon-button` |
| a state glyph in a menu | `--icon-menu` |
| a state glyph beside a value | `--icon-mark` |
| a glyph that is the whole control - a nav row, a tab | `--icon-nav` |
| art filling a picture box that is empty | `--icon-slot`, `--icon-slot-small` |

`tests/invariants/test_an_icon_size_has_a_name.py` fails on a pixel count in a rule that
draws a glyph. A relative unit passes: the icon inside a text link is `1em` because it is
sized to the words it sits in, which is a decision and not a number left lying about.

### A line with more to say shows it

A line whose detail lives only in its tooltip looks exactly like a line with nothing more to
say, and a hover does nothing on touch. So a line with a detail ends in a small `info_outline`
mark (`verbs.SAYS_MORE`), `--ink-3` at rest like any icon at the end of a row. The detail
shows on hover of the line, and on a click, tap or Enter on the mark; either way it reads
the same.

Draw it with `panel.line(text, hint=...)`, which does all of that, or with `panel.note` and
`panel.intro`, which call it. A reason from `common.failures.why(exc)`, a record's `error` or
`detail`, and a caption's hover all go through it. A chip or mark that carries a reason, a
control's hint and a clipped value's full text are not lines, and keep their tooltips.

`tests/invariants/test_a_line_with_more_to_say_shows_it.py` reads the whole `manager/` tree
and fails on a tooltip drawn anywhere else that carries a failure's words or sits on a
`manager-help` caption. It does not follow a detail carried in a plain variable onto a line
that is not a caption; that half is held by this paragraph alone.

### A list taller than its pane says so

`manager-more` is the fade over the last rows and the **More below** pill that scrolls on. It
is the last child of the scrolling element, and its `hidden` attribute is set once the end is
in view. The class draws it and nothing else decides when it shows, so whatever scrolls owns
that check.

### One control per fact, and one way back

**A control that sets a value does not also unset it.** Clicking the third star of a
three-star rating used to clear it, so one click meant set or unset depending on a number the
user was not looking at — and nothing appeared to happen, because the pointer was left over a
star that had gone dark. A star sets; an explicit `×` clears.

**The way back is only offered where there is something to undo**, and it is not a further
degree of the same scale — so it sits beside the control rather than inside it.

**A hover-revealed control is revealed by the thing it belongs to.** Keying it to a container
one surface has and another does not is how the same control ends up invisible in half the
app. Gate it on the input, never on the surface: hover where there is a pointer,
`:focus-within` for a keyboard, and simply visible where hover cannot happen.

**An element with no text has no size.** A control whose glyph lives only in a tooltip
measures zero and cannot be clicked — and a scripted click still passes on it, because a click
driven at a selector does not care whether the thing is visible. Put the character in the
element.

### A reading is a tile

A live number is in a `.manager-card` in a `.manager-tiles` grid, which fills the width
with columns no narrower than 260px. A tile is a title, the number, its history and at most
two detail lines; the tooltip carries what the tile cuts, such as a full name. A thing that
happened within the history, such as a job started or a table launched, is a `.manager-spark-mark`
on every history: a 1px line at that moment, its words in a tooltip. Where a tile's
title is the kind of reading and not what it reads - Graphics Processor, over a card - the thing read is
named under the title and the number takes its unit beside it. A tile that cannot show a number shows
`-` and says why where the detail lines would be. Several tiles that come and go as a group
sit in a `.manager-tiles-group`, which is not a cell of the grid, so the group can be
redrawn without moving the tiles around it. A tile that lists rather than reads, such as
What's busy, puts them in a `.manager-tile-rows`: a name, a share and a size to a row.

### Type

- **A heading inside a panel** is `.manager-card-title` — `--accent` cyan, uppercase,
  `--fs-caption`, `0.08em`. Group headings take the same treatment plus a rule and space
  above; the separation carries the break, not extra weight.
- **Every label takes title case, with acronyms as acronyms** — a panel's fact, a column
  header, a picker entry. Small words stay down unless they lead or close it, because the
  registries already write "Point of View" manually and a rule producing "Point Of View" would
  disagree with the thing it is the fallback for. `common/config/labels.py` is that rule and there is
  one of it: `humanize` and `field_label` are the same function under two names, so a column
  picker and a panel cannot case a word two ways. **Apply it where labels pass, not at each
  call site** — three spellings arrived in one file by each caller typing its own.
- **Headings take title case. Sentences and phrases stay sentence case. The app nav is
  uppercase, alone** — a second uppercase column would rank a page's sections with the
  product.
- **A button takes title case too.** "Import a
  Library", "Needs Review". One rule covers a label, a button and a heading, so the person
  reading never has to tell which of the three they are looking at.
  `tests/invariants/test_labels_are_title_case.py` holds it.
- **A value is a name or a state, not a sentence.** Names keep their own casing
  (`Visual Pinball X`, `TAF_L7`); states are capitalized noun phrases. Show a display name
  rather than an id — an id on screen is a leak.
- **A fragment takes no full stop, and neither does a lone sentence.** Labels, states,
  counts and fragments never take one; a tooltip, a help line or a setting's description
  that is a single sentence does not either, because the stop is doing no work when nothing
  follows it. Punctuate where a second sentence makes the boundary worth marking. "Nothing
  in it yet." is a fragment wearing a period. `tests/invariants/test_copy_full_stops.py`
  holds it for every `.help`, `.label`, `.summary` and `.description` entry in every
  owner's file, and wherever the Manager draws a tooltip, a note, a state or a help line.
- **The shortest true word wins.** A chip reads `Missing`, not "Not in this library"; the
  sentence goes in the tooltip where it costs nothing.
- **A grid marks the one column its rows are scanned by, and marks it on the column.**
  The body reads at `--ink-2` and that column reads at `--ink`, so the thing you scan for
  is the brightest thing in the row — a grid is most of the text on screen, and the top of
  the ink scale applied to all of it reads relentless rather than crisp. Declare it with
  `grid.identifier()` rather than `grid.column()`; `grid.build` refuses a set that does not
  carry exactly one. **Scanned *by* is not the row's unique key**: the Media, Assets and
  Tables grids are all scanned by game and all repeat it down the column, and the tables
  grid's filename is the identifier of record precisely because it is unscannable — two
  tables of one game share forty characters and differ in two, which is why it sits last
  and widest instead. Never infer the column from where it sits: pinning is a layout
  preference the header menu offers on every column, and a hierarchy resting on one moves
  when a user rearranges their grid.
- **Every grid column explains itself in its header tooltip.** Pass `help=` to
  `grid.column`, `grid.identifier` or `grid.list_column`: what the column is and what its
  values mean, in words the header does not already carry. A short header is not a reason
  to skip it: a column that is one word to its author is not to the person reading it.
  Where the values are a few states, the tooltip names each on its own line.
  `grid.build` raises for a column without one,
  and `tests/invariants/test_every_grid_column_has_a_tooltip.py` finds the declaration
  that lacks it without drawing the page.
- **A game or table in a list reads as the grid draws it.** The name, then a line under
  it: maker and year from `game_tables.made`, then the table's version and author from
  `game_tables.table_name`, in the `manager-cell-made` and `manager-cell-built` colors.
  Where a filename stands in for version and author, a line too short for it keeps the
  end (`manager-file-name`), which is where two builds of one game differ.
  `tests/invariants/test_maker_and_year_are_joined_once.py` fails on a maker-year join
  written anywhere else.
- **A game, table or collection in a list carries its art as the Thumbnails settings
  draw it.** One frame ahead of the name holds the chosen kind as the frontend shows it, or a
  collection's own picture, or the row's own glyph where there is none. The settings
  (Settings > VPinFE > Manager) give every list the same frame: square or twice as wide,
  Automatic making a wheel square and the rest wide; 40, 48 or 72px tall in grid rows of
  56, 64 or 88px; on a ground dark in every palette, or bare. A panel's list and the game
  picker take the shape and ground at 40px. An ancestor of the frame carries it
  (`thumbnails.Look`), so one drawn frame takes every look. The name column is declared
  wider by the frame and its gap (`grid.with_art`). Where the grid is too
  narrow for that, the name gives the room back until its edge and resize handle show,
  never below its width without art; a width somebody dragged stays theirs, and a fitted
  one is never saved. `manager/kit/thumbnails.py` draws the frame, and
  `tests/invariants/test_thumbnails_are_drawn_once.py` fails on one drawn anywhere else. It
  is not a control: no tooltip, no zoom, `alt=""`.
- **Art in a list is still.** An animated file shows its first frame at list size.
- **A missing picture is the thing's own glyph, never a broken image.** A game's, a
  table's, a collection's bookmark, a theme's palette. An empty box reads as a slot to
  drop something into, and a broken image reads as a fault.
- **A badge on every row is not a badge** — where the badge is *constant*. A chip reading
  the same thing on every row, or an action identical on all of them, says nothing and
  comes off. This does **not** cover a state that varies per row: that is data, and hiding
  its common value removes a column rather than removing noise. Show varying state on every
  row and dim the ordinary value, so the scan still lands on the exception without meaning
  being read from absence.
  One exception: a lock. A row held to one thing carries a lock and *Locked*, and a row
  that is not carries nothing - the one mark whose absence nobody misreads, as a file
  manager shows a lock only on what is locked. A collection's list marks a game held to one
  of its tables this way.
- **Cut anything that only restates what is on screen.** Explanatory prose above a control
  that already explains itself is filler, and filler is what makes a panel feel unfinished.
- **Gate an affordance on the input, not on the width.** Hover-to-reveal is
  `@media (hover: hover) and (pointer: fine)`, never a breakpoint: a touchscreen laptop
  is as wide as a desk one, and hiding a control behind hover on a device that cannot
  hover makes it unreachable. Visible is the default; revealing on hover is the
  enhancement. Pair it with `:focus-within` or a keyboard never reaches it either.
- **On screen, use the word a person would use.** The wire's `filter` is **Smart** to a
  reader. A label names the thing, not the model.
- **In a menu, the group label is chrome and the item is content.** One language for every
  menu - dropdowns, grid header and cell menus, bulk actions, pickers:
  - A **group header** takes `.manager-group`'s treatment: small, uppercase, tracked, `--ink-3`.
    It is a signpost and recedes. Never larger or brighter than the items it labels.
  - An **item** is body voice: sentence case, `--ink-2`, no letter-spacing. You read a
    name and click it, and a name only survives in sentence case.
  - **Hover and focus paint a full-width band.** A color change alone is too weak to say
    *this row is the target*, and an item that sizes to its own text draws the band to the
    end of the words rather than the edge of the menu.
  - **Accent is the current value and nothing else** - the chosen item, the checkmark. When
    every item is accent-colored the color stops meaning anything.
  - **One leading slot**, fixed width, for whatever marks the item; items with no mark
    indent to it so the labels line up. `panel.menu_entry` draws an item that way.
  - **The trailing slot is state** - a checkmark, a count, a shortcut, or *In It* on an
    entry that is already true, which is then inert. Never a second action.
  - A **destructive** item colors its *text*; the band stays the ordinary one. A red row
    reads as an error that has already happened.
  - **A separator divides groups**, never decorates.
  - **An open menu suppresses tooltips.** The control that opened it usually has one, and
    it appears on top of the menu.
  - **Group a long picker by what the columns are about**, groups named in the user's
    words. The grouping answers *which columns exist*; where a column sits is the grid's
    own order and the user drags that - so a group is a bucket, not a run of neighbours.
- **A group name is declared once and read by every surface that shows it.** The panel draws
  them as headings and a grid's built-in views are named for them, so crossing from one to the
  other is not a translation. Two lists of the same words is how four group names became three
  vocabularies for one set of facts.
- **One fact block is one grid.** A stack of facts is drawn in a single grid so the groups
  separate and the label column is the width of the longest label across all of them. Splitting
  it into a call per group loses the separation and gives each group its own column width, and
  the second is the harder one to see.
- **A label comes from the registry that owns the thing, never from its key.** Every
  closed set here already carries one - `MediaSpec.label`, `AssetSpec.label`,
  `config_schema.label_for` - and each has its acronyms cased once, correctly, in the one
  place that knows how the word expands. Deriving a label with `key.replace("_", " ")` is
  what put `real dmd color` in a column picker beside `Manufacturer`. Where a key must be
  shown because nothing has named it, derive it as an explicit *fallback* after asking the
  registry, the way `config_schema.label_for` does. `humanize()` in `common/config/labels.py` is that
  fallback: it knows this project's acronyms, so `rom_audit` reads **ROM Audit**.
- **A grid column is as wide as its header unless its values are longer.** Width is
  derived, not chosen: pass one only for a title, an author or a path, and treat it as a
  floor. A hand-picked number on a column of short values is a guess that outlives whoever
  made it.
- **A cell that holds several values holds the list, never a joined string.**
  `grid.list_column` draws it as chips in alphabetical order, filters it by the values
  the rows hold - any of the picked ones, all of them with the switch, and None for a row
  holding nothing - and sorts it by the first chip as drawn, empty rows last either way.
  Filtered as text, "Night" finds "Late Night" and "Night Owl" alike.
- **A path on the wire or in a file is forward-slashed, whatever host built it.**
  `str()` on a `Path` and `os.path.relpath` both answer in the host's separator, so the
  same library described itself as `medias/bg.png` on Linux and `medias\bg.png` on
  Windows — and clients, archives and `.info` keys all have to agree across machines.
  Use `.as_posix()`, or `.replace(os.sep, "/")` where the value is already a string.
  `tests/invariants/test_wire_paths.py` enforces this, because the mistake is invisible
  on the machine that makes it — only Windows CI ever says so, an hour later.
- **Empty is not absent.** Where a field can legitimately hold "nothing" *and* be left
  out, those are two different answers and need two different values — `str | None`, not
  `str`. Collapsing them into `""` is how "the row that names no table" became "every row
  for this game", so deleting one row deleted three, and how an exclusion covering a whole
  game could not be lifted on its own. If a caller can mean both, the type has to let it
  say which.
- **A row's identity is what it *names*, never what it resolves to.** They look the same
  on screen — a row naming no table still shows one — so a client that is handed only the
  resolved value cannot address the row it is looking at. Report the identity alongside it.
- **Sort by what is shown, not by what is stored.** A list ordered on its keys while
  displaying labels reads as unsorted, because the reader cannot see the thing it is
  sorted on.

### Two traps that have both been walked into

- **Source order decides between equal specificity.** `.manager-tier` sets `border` as a
  *shorthand*, so a variant declaring `border-color` above it is silently reset to
  transparent. When adding a property to a base rule, check what overrides it later, and put
  the override below.
- **A row whose value is not text must be drawn in the same grid as the rows around it.** A
  second grid sizes a `max-content` label column of its own, and its values start somewhere
  else entirely.

### The page grid is above unpositioned backgrounds

`body::before` is a fixed `z-index: 0` layer, so it paints over any background beneath it —
"opaque" is not opaque until the element is lifted. A panel that must cover it takes
`position: relative; z-index: 1`, as `.nicegui-aggrid` does. Being above it is also what lets
a region *dim* the grid rather than sit behind it: what shows through is then the region's own
alpha.

### A handler that waits finishes on the page

NiceGUI runs a handler in the slot of the control that called it. A redraw, or leaving the
page, can free that control while the handler awaits, and a notify, a confirm or a dialog
after the `await` then raises instead of showing. So an async handler that touches `ui`
after a wait is decorated `@on_page` (`manager/kit/on_page.py`): it takes the page when called
and runs the handler under it.

A builder - an async function that draws into whatever container it is awaited in - is not
decorated. Under the page, what it draws goes to the page's end.

`tests/invariants/test_a_notice_after_a_wait_is_said_on_the_page.py` fails on either
mistake. A test calls a decorated handler from a slot, as a click does, with `press` from
`tests/support/clicks.py`.

### A region still being drawn says so

A region the Manager fills after the page is up is marked while it waits: `busy.fill(region,
work)` for a first draw put off until the browser has the page, and `with busy.held(region):`
around an awaited build. Both put `aria-busy="true"` on the region at once and take it off when
the work ends, however it ends. The spinner goes in with the mark, and `.manager-busy` keeps it
hidden for its first quarter second, so a region that fills quickly never shows one.

Content left up while what replaces it is read - a view after a rail click, the side pane after
a grid row or after something is added or removed - is held with `over`: `busy.start(region,
work)` for an act that reads before it draws again, `busy.held(region, over=True)` around a
pane build. A value change is not one of these (next section). From the same quarter
second the content dims in place and the spinner sits over its middle (`.manager-busy--over`),
so nothing moves, and the rail row lights at the click. A region filling empty space keeps the
spinner after what it has drawn. Work that overlaps on one region shares one mark and one
spinner, and the region is let go when the last of it ends.

`tests/invariants/test_a_region_filled_later_says_so.py` fails on a one-shot `ui.timer`
anywhere else in `manager/`; a timer that fills no region is listed there with its reason.

A page is drawn when its handshake is done and nothing on it is marked `aria-busy`. The loading
screen holds its region until the view is drawn (`busy.until_gone`), a linked subject's pane is
held until it is built, and a grid's saved view is filled through `busy.fill` on its wrapper.

### A value change keeps the page

Changing a value - a switch, a select, a field, a number - saves it and nothing else moves: the
page is not drawn again, nothing dims, and scroll and focus stay where they were. A part of the
page that reads the value is a `panel.region(build)`, and the handler calls its `refill()`, which
draws that part again inside the same container. A row that only the grid shows is put right
through the grid. A rebuild (`redraw`, `rerender`, the workbench's `rebuild`) is for structure: a
row added or removed, a different subject or tab. Keep the control that triggers a refill outside
the region it refills. A refill that replaces the control just touched hands focus, and any text
typed into it, to the control that takes its place.

`tests/invariants/test_a_value_handler_does_not_rebuild_the_page.py` fails on a new value control
whose handler reaches one of those verbs by name, in its file or a manager module it imports from.
It does not follow a method on an object or a handler passed through another function, so it is a
floor. `tests/theming/test_a_value_change_keeps_the_panel_drive.py` is the check for those: it
changes one control of each kind on each page and in each section of a panel, in a real browser,
and fails on a page that was taken again, dimmed, lost its landmarks, or lost focus or typed text.
A handler that rebuilds
today is listed with what converts it in the invariant, and that list only comes down.

## Typing

Annotate what you write. A signature that says what it takes and what it gives back is the
cheapest documentation there is — it sits where the reader already is, and unlike a docstring
it can be checked.

```bash
mypy                  # the packages named in pyproject.toml, nothing else
```

`mypy` is that check, and it is the reason the annotations are worth writing. **An annotation
nothing verifies is prose.** A function that claims `-> Path` and hands back `str | None` reads
as a contract and behaves as a trap, and the only thing standing between those two is a
checker that runs.

What that means in practice:

- **New code is annotated, and it is annotated honestly.** `Any` to quiet a complaint is worse
  than no annotation, because it looks like an answer.
- **A declared return type is a promise the function keeps.** Where a route declares a model,
  it returns that model rather than a shape that resembles one.
- **Silence a finding with a narrowing guard or a corrected type, not with `# type: ignore`.**
  Where an ignore is genuinely right, it carries the reason on the same line — the rule is the
  one `# noqa` follows below.
- **The checked packages are listed in `pyproject.toml`** rather than passed on the command
  line, so CI, the editor and a local run all measure the same thing.

**All six checked packages report zero, and CI fails when they do not.** `mypy` with no
arguments is the gate, so a finding anywhere in `apps`, `common`, `manager`, `extensions`,
`frontend` or `httpapi` fails the build. The API surface keeps its own step beside it, so
it still fails on its own account rather than inside a whole-tree result.

That is the whole of Tier 1 — the annotations that existed and were wrong. Annotations that
are missing are a separate job and are not gated: the checker reports nothing about an
unannotated function body, which is why zero here does not mean the tree is fully typed.

## Linting

`ruff` handles formatting-adjacent rules, import order, PEP 8 naming and common bugs.
Configuration is in `pyproject.toml`; it installs from `requirements-dev.txt`.

```bash
pip install -r requirements-dev.txt

ruff check .          # what the CI advisory run reports
ruff check . --fix    # apply the safe fixes
```

CI runs it three ways:

- **Advisory over the whole tree.** Reports and does not fail. It is a visible debt
  register, not a gate.
- **Blocking everywhere we write Python.** `tests`, `apps`, `common`, `manager`,
  `extensions`, `frontend`, `httpapi`, `cli.py` and `main.py` — every rule we select, at
  zero, every file checked whether it is new or not. Nothing leaves this step once it has
  joined, which is what stops the sweep that cleaned it from decaying. `N` does not see a
  PascalCase dataclass field, so `tests/invariants/test_python_is_snake_case.py` states
  that rule in full.
- **Blocking on newly added files.** Any `.py` file added in a PR must be clean. This is what
  "get it right going forward" means in practice: new code is born compliant, and existing
  code is cleaned up when it is touched rather than in one enormous diff.

Some rules are ignored per-file for good reasons — `main.py`
imports after executing statements because config has to be resolved before anything under
`common/` is imported. `tests/games/test_score_parser.py` does the same, because the module
it tests reads `roms.json` and the config paths at import. Those exemptions are listed in
`pyproject.toml` with their reasons.

An exemption carries its reason, not the measurements behind it. A count in a config file
goes stale and gets believed anyway. Put it in the commit that added the exemption.

### A rule needs a check that reads the whole tree

A check scoped to a diff has no memory. It sees a file at the moment something touches it
and never again, so whatever is waved through once, or written before the rule existed, is
invisible from then on — and the residue grows under a check that goes on reporting clean.
Every convention here that has a test sits at zero. The ones that had only a report had
been drifting for weeks, and nothing said so.

So a convention is not established when it is written down here, and not when an editor
warns about it. It is established when something reads the whole tree and fails. The
warning is for fast feedback; the test is what makes the rule true.

Some of a rule is not mechanizable, and that part stays a judgment — a check that fires on
code that is fine is one everybody learns to wave through, which is the same failure from
the other side. Gate the half a checker can be certain of, and let the rest be read.

### Auto-fix is not always safe

`--fix` will remove an import it thinks is unused, including one that exists purely for its
side effects. A trailing comment does not protect it; `# noqa` does:

```python
# Imported for the side effect of registering the Manager's routes.
import manager.page  # noqa: F401
```

Run the tests after `--fix`, not just the linter.

## Tests

### Running them

`python -m tests` runs the suite across processes, whole modules to a process, longest first
by how long each took last time; `-j N` sets how many (half the cores by default) and `-j 1`
runs them in the calling process. Paths after the options narrow the run to a directory or a
file under `tests/`. Each process gets its own config directory and temp directory, and a
process that leaves anything in its temp directory fails the run, naming what it left. A
process that does not pass keeps everything it printed, at the path the report names, after
the run exits; a run that passes leaves nothing behind.
`python -m unittest discover -t . -s tests` runs the same tests in one process; CI runs
`python -m tests -j 4`, because one process takes more than the job's 20 minutes on a runner.
`tests/invariants/test_parallel_runner.py` holds the runner to the single-process run.

### A test holds on Windows too

CI runs the suite on Windows, and the mistake below is invisible on the machine that makes
it. What fails there, and what to write instead:

- **An expected path is built the way the code builds it** - `Path("/usr/bin/x")`,
  `os.path.join(...)` - never a bare `"/usr/bin/x"` compared with what a `Path` returned,
  which is `\usr\bin\x`. A root-relative `Path("/tmp/x")` gains the current
  drive on Windows, so compare it with another `Path`, not a string.
- **A repo-relative path in a message, a key or a comparison is `.as_posix()`.**
  `tests/invariants/test_wire_paths.py` fails on `str(x.relative_to(...))` or an f-string
  around one, in the source and in `tests/`.
- **Text I/O names its encoding.** `read_text`, `write_text` and `open` in text mode
  default to the host's code page on Windows. CI's lint run enforces `PLW1514`
  (`ruff check --preview --select PLW1514 .`), so `encoding="utf-8"` is required. When the
  line ending matters, read and write bytes.
- **A listing is sorted before it is compared.** `iterdir`, `glob` and `os.scandir` answer
  in the filesystem's order.
- **What a handle holds, it releases before the folder goes.** Windows cannot delete or
  replace an open file, so close it, or use a `with`, before the temp directory is removed.
- **A fixture Windows cannot hold is skipped by name.** `:`, `*`, `?` and a trailing dot or
  space in a file name, two names differing only in case, and permission bits are decided
  by the platform, not the code. Add a decorator to `tests/support/skips.py` with the
  reason, rather than a bare `skipIf`.
- **A small or negative timestamp raises on Windows** from `fromtimestamp` and `strftime`.
  A test uses a realistic one, and the code that formats a stored time does not assume it.

### A source file is parsed once

A test that reads the tree as Python gets it from `tests/support/trees.py`: `tree_for(path)`
for a file in the repo, cached for the run, and `parse_snippet(source)` for a checker's own
sample. `tests/invariants/test_a_source_tree_is_parsed_once.py` fails on an `ast.parse`
anywhere else under `tests/`, and on a cached tree that no longer matches a fresh parse of
its file.

### A browser test waits on the app, never on the clock

A test that drives a browser waits on the change its action causes: `tests/support/manager_walk.py`'s
`visit`, `act(action, until=...)` and `open_pane` for the Manager, `BrowserSession.wait_for`,
or the app's own signal - a theme's `document.body.dataset.ready`, an overlay's selected row
with no bridge call outstanding, the Manager's handshake with nothing `aria-busy`. The change
waited on is false before the action, so something already on screen never answers it. Every
wait has a limit, and reaching it is a failure. A check that nothing happened waits out a named
window, and the name says what it is sized from (`grid.SAVE_THROTTLE_S`).

`tests/invariants/test_a_browser_test_waits_on_the_app.py` fails on a `sleep` of a literal
0.25s or more, or an in-page `setTimeout` with a literal delay of 250ms or more, in a test file
that imports `tests.support.browser_session` or `tests.support.live_instance`. Below that is
input pacing, a poll's step, or a `setTimeout(fn, 0)` deferral.

### An instance is booted once per class

A `LiveInstance` costs seconds, so a test class boots one in `setUpClass` and its tests share
it. A class whose tests each need their own says why in `BOOTS_PER_TEST`.
`tests/invariants/test_a_test_that_boots_its_own_instance_says_why.py` fails on a
`LiveInstance(` outside `setUpClass` or `setUpModule` without that declaration, and on a
declaration left on a class that boots once.
