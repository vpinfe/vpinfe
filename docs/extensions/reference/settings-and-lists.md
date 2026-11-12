# Settings and lists

The two declarations most extensions start with: a page of settings, and a list in a rail area.

## Settings

Declare them with `ctx.ui.settings`.  Core shows them on the extension's page and stores them
in the extension's own file.

A field is `{"key", "type", "default", "choices"}`:

| key | meaning |
|---|---|
| `key` | Required.  The name `ctx.config.get` reads it by.  Unique |
| `type` | One of the settings types below.  Left out, `string` |
| `default` | Text.  A switch defaults to `"false"` unless true.  A secret has none.  A select's default is one of its choices, and is the first when left out.  A multi's is a list of them.  A number's is a number |
| `choices` | A select's or a multi's `[[value, label], ...]` |

A settings page offers exactly `SETTINGS_TYPES`: the *settings* column of the table under
[Fields](actions.md#fields).  A `number` takes `min`, `max` and `unit`, a `path` says what it
`wants`, and a `multi` takes `choices` as a select does and may default to a list of them.

Everything is stored as a string.  Give a secret `type: "secret"`, never `string`: core takes
secrets out of every answer it gives for the extension, by that type.  Leaving a secret empty
keeps what was there; only *Clear*, which asks first, empties one.

**`check`.**  `check(values)` receives every setting as it would be after the change, with the
unset ones at their defaults.  Raise to refuse the change, and the person sees your words under
the first field that changed.  It returns nothing.

```python
def check(values):
    if not values["server"].startswith("https://"):
        raise ValueError(ctx.t("error.https"))


ctx.ui.settings([{"key": "server", "type": "string", "default": "https://example.com"}],
                check=check)
```

The words go in the extension's `i18n`:

- `settings.<key>.label` - falls back to the key.
- `settings.<key>.help` - the `{default}` slot is the default.  Falls back to empty.
- `settings.<key>.choice.<value>` - a select option.
- `settings.label` - the heading over the section.

A write for a key you did not declare (`error.extensions.no_setting`) or a select value that is
not a choice (`error.extensions.bad_choice`) is turned down, and the person sees why under the
field.

## Lists

`ctx.ui.list(key, rows, *, columns, area, title, views, status, acts, tag, verbs, add,
detail, save)`.
A list an extension holds, shown in the rail.

```python
ctx.ui.list(
    "tables", read_tables, title="Example Scores", area="community",
    columns=[{"field": "vps_id", "kind": "game", "name_from": "name"},
             {"field": "plays", "kind": "number"},
             {"field": "last_played", "kind": "date"}],
    views=[{"key": "most_played", "columns": ["vps_id", "plays"],
            "sort": [{"field": "plays", "desc": True}], "ranks": True}],
    status=lambda: {"text": ctx.t("status"), "to": "settings"},
    acts=lambda: {"send_now": send_now, "site": "https://scores.example.com"},
    verbs=[{"key": "open", "link": "page"},
           {"key": "hide", "run": hide, "danger": True},
           {"key": "rename", "action": "rename", "each": True}],
    add="new_table",
    tag="Weekly Challenge")
```

**Key and rows.**  `key` is unique within the extension.  `rows()` returns a list of dicts
that JSON can carry.  If a row cannot be carried, or the result is not a list of dicts, the
list shows that the extension could not answer and the extension keeps running
(`error.extensions.not_data`, `error.extensions.not_rows`).  When a service does not answer,
raise `Unavailable`; see [Unavailable](running.md#unavailable).

**Row ids.**  A row's `id` is a string, unique in the list.  A number is read as its text.  Two
rows with one id mean the extension could not answer
(`error.extensions.duplicate_row_id`), for any list, verbs or not.  A verb finds a row by its
`id`.  A row with no `id` shows, but it cannot be selected and has no menu.

**Area.**  Which group of the rail the list is in.  Required: an entry with no area has no home in the navigation, so the extension is refused.

| `area` | in the rail under | offered when the install has |
|---|---|---|
| `library` | Library | the `library` feature |
| `frontend` | Frontend | the `frontend` feature |
| `community` | Community | always |
| `system` | System | always |

A list is in the rail only while its extension runs.  A page still open when the extension
stops says Off, or Stopped with the reason, in place of Refresh, and the list is not asked.

**Title.**  `title` is shown as written; left out, it is `list.<key>.title` in the catalog.
It cannot match a core page's title (Overview, Games, Tables, Media, Assets, Collections,
Tags, Discovery, Players, Launchers, Themes, Settings, Devices, Metrics, Logs, Jobs,
Backups, About, Library, Frontend, Community, System), or another running extension's list
title in the same area, compared without regard to case.  Either one raises `ContractError`
from `register`, so the extension fails to start and the log names the clash.

**Columns.**  `{"field", "header", "kind", "help", "under", "states", "name_from", "keys", "picture", "icon", "identifier"}`.

| key | meaning |
|---|---|
| `field` | Required.  The row key it shows |
| `identifier` | `True` on the one column that is the row's name.  Left out, it is the first column that is not a `state`, `chips`, `link` or `picture`, wherever the columns are put.  Two marked, or a marked `state`, `chips`, `link` or `picture` column, raises `ContractError` |
| `kind` | `text` (default), `number`, `date`, `state`, `chips`, `link`, `picture`, `game` or `table`; see [Cell kinds](#cell-kinds) and [Games and tables](#games-and-tables) |
| `header` | Shown as written; left out, `list.<key>.column.<field>.header`, then the field |
| `help` | The tooltip on the header.  Left out, `...help` in the catalog, then the header |
| `under` | The identifier only.  Row fields shown on the line beneath its value, as a game's maker and year are |
| `states` | A `state` column only, and required there.  `{value: look}`, the values the field may hold |
| `name_from` | A `link`, `game` or `table` column only.  The row field the link reads as, or for a game or table the name to draw where neither the library nor VPS knows the id.  Left out, a link reads as the column's header and a game or table as its id |
| `keys` | A `game` or `table` column only.  Whose ids the field holds, `vps` (default) or `vpinfe` |
| `picture` | The identifier only.  The row field holding a picture, drawn where the library has none |
| `icon` | The identifier only.  A Material icon name or SVG path, drawn where there is no picture |

A column key core does not read stops the extension from starting, with the key in the reason.
The identifier is the row's name, so it is a `text`, `number`, `date`, `game` or `table` column, and the only one that can carry `under`, `picture` or `icon`.

### Cell kinds

The first three draw a value as text.  The other four draw it as something the person can see
at a glance.

| `kind` | the field holds | drawn as |
|---|---|---|
| `text` | a string | the text |
| `number` | a number | the number, right-aligned, sorted as a number |
| `date` | a date and time as text | the date, sorted by time |
| `state` | one of the values in `states` | a word, as a colored chip, with an icon before it, or plain |
| `chips` | a list of strings | one plain chip for each string |
| `link` | a web address | a link that opens in a new tab, marked as leaving |
| `picture` | a web address, or a file in the extension's folder | a small picture |

**State.**  `states` is `{value: look}`, and a look is one of:

- `{"tier": "on" | "off" | "unknown" | "warn" | "bad"}`, the word as a colored chip.  The colors
  are the Manager's own, so a state reads the same on every page.
- `{"icon": "block"}`, a Material icon name, drawn before the word.
- `{}`, a quiet word with nothing before it.

A look takes a tier or an icon, not both.  A row whose value is not in `states` shows the value
as it is, plain.  The column is filtered by the words, and the filter lists every state you declared,
including ones no row holds now.  Use a state for something with a few fixed values whose
difference the person should see without reading, such as Live, Late or Stopped.

**Chips.**  The value is a list of strings, drawn as plain chips with no looks of their own.  It
is filtered and sorted by what the chips say.  A chip marks an exception, so use the column
for what some rows have and most do not (a rule that applies, a flag that is set).  A column
where every row has a chip says nothing; make it text.

**Link.**  The value must start with `http://` or `https://`; anything else, or nothing, draws
nothing.  The link has the look of an act that leaves the Manager.  Say what it reads as with
`name_from`, naming a row field (`"name_from": "title"` reads as the row's `title`); left out it reads
as the column's header.

**Picture.**  The value is a web address, or a path in the extension's folder (`art/cover.png`).
Core downloads the picture, checks that it is an image, makes a copy at the size of a list and
serves only that copy, from VPinFE's own address, so the page never reaches another server.  The row you
get back from the list route holds that address in place of what you gave; the rows a verb
runs on still hold your own value.  Core only serves a
picture that appeared in a row of that extension.

- A web address needs `net:outbound` in the manifest.  Without it the list fails to read
  (`error.extensions.picture_outbound`).
- A file must be an image inside the extension's folder.  A path that leaves the folder, or a
  file that is not an image, shows nothing.
- A picture that cannot be had shows nothing, never a broken image.  A web picture that failed is not asked for again for ten minutes, and one that came is kept.  A picture downloaded from the web is limited to 8 MB.
- A picture column is not sorted or filtered.

Words for a `state` column:
`list.<key>.column.<field>.state.<value>` is the word, and is required, so `--check-extension`
reports one missing.  `list.<key>.column.<field>.state.<value>.why` is the tooltip, and is
optional.

**Views.**  `{"key", "name", "columns", "sort", "help", "ranks"}`.  A view names the columns it
shows (all when left out) and how it sorts: `[{"field", "desc"}]`, one field after another.
`name` and `help` fall back to `list.<key>.view.<viewkey>.name` and `...help`.  A view can only
name columns the list has.

**Ranks.**  `"ranks": True` offers the view as an order for a collection, named for the list's
title and the view's name (*Example Scores: Most Played*).  It puts this library's games in the
view's sort, each field in its own direction, and there is no reversing it.  A game the list
does not rank follows in title order, so the order never drops one.  A ranked view sorts on
`number` or `date` columns only, and its list needs a `game` or `table` identifier.  The order is read with the
tags, on the same schedule, and keeps the last good read if a read fails.  While the extension
is stopped, a collection in its order keeps it and shows its games in title order until the
extension runs again.

### Games and tables

A `game` or `table` column holds an id and draws the game or table it names, as the Games and
Tables lists do.  `keys` says whose ids they are: `vps` (default) for a VPS entry, which is a
game, or a VPS release, which is one build of it, or `vpinfe` for the ids VPinFE gave games and
tables in this library.

Where this library holds the id, the cell is the library's own: its name, its maker and year
(for a table, its version and author), the picture the Thumbnails settings choose, and the name
as a link to the game or table.  Where it does not, the cell is VPSdb's name and line with no
link, then the row's own picture, or an icon.  The picture and the icon show only where the
Thumbnails settings have pictures on.  Where neither knows the id, the cell is the row
field `name_from` names, or the id.  A VPS release this library holds another version of says so
under its name and links to the release.  The column is sorted and filtered by the name it
shows.

The identifier is what the list relates to in this library.  A game or table column that is not
the identifier draws the name and the line, with no picture.  A game or table can also be a fact in a pane,
page or group; see the pane below.

**Tag.**  With `tag="Weekly Challenge"` and a game or table identifier, core puts that tag on
what this library holds from the list: the game for a `game` column, the table for a `table`
one, so a challenge naming one build of a machine tags that build and not the others.  The tag belongs
to the extension.  Nobody can rename, merge or remove it, or put it on by hand, and it goes
when the extension stops.  It is written as given in every language, because it is stored on
games, which makes it data.

**Reading.**  Core asks `rows()` on every visit and on Refresh, so answer with the whole list
as it is now and keep no copy of your own.  Core keeps the last good read on disk
(`ctx.ui.kept`).  The page shows it at once, with how old it is, and asks again behind it.  If
that read fails, the last good list stays on screen, marked stale.  Tags and ranks are read
shortly after start and every 30 minutes after; if a read fails, the last good one stays.

**Status.**  `status()` returns text, or `{"text", "detail", "to"}`.  It is one quiet line in
the bar above the list, or under the reason when the list could not be read.  `detail` shows
on hover.  `to` makes the line a link: `players` opens Frontend > Players, where each player's
accounts and their Share are, and `settings` opens the extension's own page.  Any other `to`
shows no link.  An empty status shows nothing.

**Acts.**  The menu at the end of the page's title, in order, with **Settings** added after
them wherever the extension has settings.  `acts` is a dict `{key: function or address}`, or a
function returning one.  Core calls it each time the page opens and after each act, so you can
offer an act only while it can work (a Send Now with nothing waiting is left out).

- A function runs when the act is pressed.  It returns None, a message string that is said, or
  a dict `{"message", "url"}`: a `url` opens in a new tab.
- A string is an address.  The act is a link that opens that address in a new tab, marked as
  leaving, and the extension is not called.  It has to start with `http://` or `https://`, or
  it is left out.
- `{"action": "connect"}` opens the dialog declared as `connect`, with no rows.  The extension
  is not called until the dialog is.  A key that names no declared dialog stops the extension
  from starting, with the key in the reason.

An act's label is `list.<key>.act.<act>.label`, falling back to the act's key.

**Verbs.**  What a person can do to rows: one, or several selected.  Each verb is in the row's
right-click menu and in the menu the bar shows while any row is selected.  `{"key", "run",
"link" or "action", "each", "danger", "needs", "one", "label"}`:

| key | meaning |
|---|---|
| `key` | Required, unique in the list.  What the catalog finds its words by |
| `run` | A function, called with the rows as dicts.  It returns None, a message string that is said, or `{"message", "url"}`: a `url` opens in a new tab.  Core reads the list again afterwards |
| `link` | In place of `run`: the row field that holds an address.  The verb opens it in a new tab, marked as leaving, and the extension is not called.  The field has to start with `http://` or `https://`, or the verb is dimmed and says **No address**.  A link is always `one`, and takes no `danger` or `needs` |
| `action` | In place of `run`: the key of a dialog declared with [`ctx.ui.action`](actions.md).  It opens over the rows chosen, and its `ask` is handed their ids (see [Over rows](actions.md#over-rows)).  It takes no `danger`, since the dialog asks for itself.  The list is read again after the dialog runs |
| `each` | With `action`: one dialog for each row chosen, in order, with **Skip** and **Stop** where Cancel would be.  Skip leaves that row as it was and moves on; Stop ends the run.  Without it, one dialog covers every row |
| `danger` | Puts the verb last, under a rule, in red, and asks first.  The question is `list.<key>.verb.<verb>.confirm`, and `{count}` is the number of rows selected, which can be more than the rows the verb acts on when it also has `needs`.  The Manager does the asking; the route runs the verb whenever it is called |
| `needs` | A row field that is true or false.  The verb is dimmed on a row where it is not, with the words `list.<key>.verb.<verb>.needs` behind an info mark.  With several rows selected, it acts on the rows where the field is true.  Core checks this again when the verb is pressed |
| `one` | The verb makes no sense over several rows.  With several selected it stays in the menu, dimmed.  Core turns it down for any other number of rows |
| `label` | Shown as written; left out, `list.<key>.verb.<verb>.label`, then the verb's key |

The question a danger verb asks has a singular and a plural, `{"one": "Remove this row?",
"other": "Remove {count} rows?"}`.  Left out, core asks "`<label>` this row?" or "`<label>`
`<count>` rows?".  A verb is not offered while the extension is stopped.  If a verb raises, the
person sees the reason (`Unavailable` is a 502).

**Add.**  `add="new_table"`, or a list of keys, names the dialogs that put something into this
list.  Core puts a button in the bar above the list: its words in full while the list is empty,
and a **+** once it has rows.  With several keys the button opens a menu of the dialogs' labels
(`action.<key>.label`).  The dialog opens with no rows, and the list is read again after it
runs.  A key that names no declared dialog stops the extension from starting, with the key in
the reason.

**Pane.**  `detail=` opens a pane beside the list for the row a person clicks, and `save=` writes
what they change in it.  Core draws the pane, as it draws every other panel, so it looks like
the Manager wherever the extension runs.  `detail(row_id)` is handed the row's `id` and returns
the pane as a dict, or None for a row that is no longer there; the person is told the
extension no longer has it.  Core does not check the id against `rows()` first, so look it up
and do not trust it.

```python
def detail(row_id):
    return {"title": "Medieval Madness", "under": "Week 41",
            "sections": [{"key": "about", "label": "About", "facts": [...]},
                         {"key": "mine", "label": "Yours", "fields": [...]}]}


def save(row_id, values):
    store[row_id].update(values)


ctx.ui.list("board", rows, area="community", columns=[...], detail=detail, save=save)
```

The answer is `{"title", "under", "picture", "sections"}`.  `title` is required and is the
row's name in the pane's header.  `under` is the line beneath it, and `picture` is drawn at the
top, written as a [picture column](#cell-kinds) is.  A section is `{"key", "label", "facts",
"fields", "rows"}`:

| key | meaning |
|---|---|
| `key` | Unique in the pane.  Left out, `section1`, `section2` and so on |
| `label` | The section's name in the pane's rail.  Every section is shown with its header, a lone one included.  Required when there is more than one section; a lone section left without one is headed Details |
| `facts` | Read-only lines, `{"label", "value", "kind", "to", "help", "tier", "says", "keys", "picture"}` |
| `fields` | Lines the person can change, in the shape of a [settings field](#settings) |
| `rows` | Plain lines, `{"label", "under"}`, for a short list of things the row holds |

A fact's `kind` is `text` (default), `number`, `date`, `state`, `chips`, `link`, `picture`,
`game` or `table`, drawn as the same [cell kind](#cell-kinds) is.  A `state` fact carries its look in the fact
itself, `"tier"` (`on`, `off`, `unknown`, `warn` or `bad`), and no other kind may.
A `link` fact needs `to`, the address it opens, in a new tab with `noopener` and `noreferrer`.  A fact's `value` is the word the person
reads, so give it already in their language; `help` is its tooltip.

A `game` or `table` fact's `value` is an id, drawn as a [game or table column](#games-and-tables)
draws one, with its picture and line.  `keys` says whose ids they are, and `says` is the words
to show where neither the library nor VPS knows the id, as text and not the name of a row field.
`picture` is a web address or a file in the extension's folder, shown where the library has none.
No other kind takes `says`, `keys` or `picture`.

A field is `{"key", "type", "label", "value", "help", "choices", "min", "max", "unit",
"wants", "placeholder"}`, of any type a setting has.  Its `key` is unique in the whole pane,
not only in its section, because it is what `save` is handed.  A `select` or `multi` field
needs `choices`, a list of `[value, label]` pairs, and a pair of any other shape is refused.  A
`number`'s `min` and `max` are finite numbers and a `path`'s `wants` is one of the kinds a setting
has.  Give a `secret` its real value, or empty when none is held: core sends the Manager only
that one is set, never the value.

A field is written when it is set.  There is no Save button: each change calls
`save(row_id, values)` with the keys that changed, typed as a setting is (a switch is a bool,
a number is a number, a `multi` is a list).  `save` returns nothing.  Core asks `detail` again
and draws the answer, so the person sees what was kept.  A secret left empty keeps what it
was, and None clears it.  A number field cleared arrives as None too.  A change also has the
list read again, so a column that shows the field follows it.  To refuse a value, raise; the
person sees the reason under the
field, and the field keeps what they typed.  Core hands `save` only keys the pane offers now
(`error.extensions.no_field`), and a select value that is not a choice is turned down
(`error.extensions.bad_choice`).

**Several rows.**  When the list has verbs, the person can select several rows, and the pane
becomes core's own: how many are selected, and the verbs over them.  `detail` is not called for
that.

**Pane errors.**  An answer core cannot draw makes the pane say what is wrong, and the
extension keeps running: `error.extensions.pane_shape`, `pane_key`, `pane_kind`, `pane_tier`,
`pane_looks`, `pane_link`, `pane_field`, `pane_choices` and `pane_twice`.
`--check-extension` reads the first row's pane and reports the same faults.

The pane is served at `GET /api/v1/ext/<name>/lists/<key>/detail/<id>`, which needs the
list's `tier=` (Library when it says nothing), and changed with `PUT` at the same address and
`{"values": {...}}`, which needs the tier of the `save`.  The id may hold slashes.

Words: `list.<key>.title`, `list.<key>.column.<field>.header|help`,
`list.<key>.column.<field>.state.<value>` and `....state.<value>.why`,
`list.<key>.view.<viewkey>.name|help`, `list.<key>.act.<act>.label`,
`list.<key>.verb.<verb>.label|tip|confirm|needs`.  `tip` is what a hover adds to the label.
