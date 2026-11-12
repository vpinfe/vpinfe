# More to offer

Six things an extension can offer beyond a page of its own: a way to play a table, something
added to every entry, an outside link, an Online source, a name for a command, and a word on
what a game tells you.

## A way to play a table

VPinFE plays Visual Pinball, and through the generic app anything a person can point at a
binary.  An extension is how another format becomes first class.  It claims its own suffixes,
so a library of them is worth importing rather than read and dropped.  `ctx.apps.provide` needs
`apps:provide` in the manifest's `scopes`.

```python
ctx.apps.provide(
    id="fp",
    name="Future Pinball",
    suffixes=(".fpt",),
    fields=({"key": "bam_path", "path": "dir"},),
    command=lambda entry, settings: ["/opt/fp", "--play", entry["table"]],
)
```

```json
{
  "app.fp.field.bam_path.label": "BAM folder",
  "app.fp.field.bam_path.description": "Where BAM is installed, if it is"
}
```

The app's words are in the extension's `i18n/en.json` under `app.<id>.`, keyed the way an
app's own file is (see "An app's words" in `docs/conventions.md`).  A field that file does not
name takes core's words, so Program reads the same on every launcher.  `name` is given in code
here because Future Pinball is a product name, and is shown as written.

The app is described in plain data because an extension cannot import the app contract.  Its
one door is `common.extensions.contract`, which is what makes the import boundary checkable.
`command` answers with a list of arguments, never a string.  Splitting a string is how a path
with a space in it becomes a crash or an injection, and only the extension knows where its own
arguments end, so a string raises.  Answering with nothing runs the launcher's configured
binary and arguments instead.

Apps this build ships are offered first, so a provided one cannot take `.vpx` out from under
Visual Pinball by loading before somebody looks.  An id already in use is refused, because ids
are stored in a table's record and read back long afterwards.  What an extension provides is
taken back when it unloads, so a disabled extension leaves no suffix claimed by something no
longer there.

A provided app does not get the rest of the app contract: parsing a table, resolving a ROM, a
settings surface, a launch of its own for a recording.  Those are declared absent rather than
half answered, the same way the generic app declares them.

## Something to an entry

A theme reads `entry.ext.<key>`.  It never learns which extension answered, and no extension
gets a method of its own on the theme surface, since that would need a new call for every
connector that follows.

```python
def rating_for(game):
    # game is {game_id, vps_id, name, manufacturer, year, ipdb_id}: a description,
    # never core's object.
    return {"stars": look_it_up(game["vps_id"])}


ctx.entries.contribute("rating", rating_for)
```

Each value in the description is the effective one, so an extension sees the match a user
corrected, not the one the scan found.

**Core makes the call; the browser makes none.**  `fetch` runs on core's thread when the player
moves to a game, so it may block, but it must not raise for a game it simply has no answer
about.  One that raises costs its own key and nothing else.

Core does three more things that no theme author sees:

- The answer is held per process, so one fetch serves every window and survives a reload.
- The games either side are fetched on the same signal.  What somebody sees is the answer
  fetched a step ago, and the gap only shows on the first game of a cold start.
- The message that carries an answer names the game it is about, so one that arrives after the
  wheel has moved is applied to the entry it belongs to.

The slot is always present and empty at library load, since a list of four hundred games
cannot wait on four hundred calls to somebody else's server.  A theme written as
`if (entry.ext.rating)` is correct throughout without knowing there is a waiting state.

An answer is held for the rest of the run.  When what an extension knows changes (a fresh list
read, say), `ctx.entries.stale("rating")` drops what core holds under that key, and each game
is asked again the next time it is reached.

An extension that answers from one of its own lists makes no call per game at all.  It holds
the rows its list last answered and reads them back at start with `ctx.ui.kept(key)`.
VPinPlay's rating works that way.

## An outside link

A game, a table or a file can have a page somewhere else, and an extension can say where.

```python
ctx.catalogs.contribute(
    "scores", "Example Scores", "game",
    lambda game: f"https://scores.example.com/tables?vpsid={game['vps_id']}")
```

`subject` is `game`, `table` or `file`, and anything else is refused at registration.  `link`
is handed a plain description of the subject and answers its address there, or `""`.  Core
draws it as a row named for the place, with an open-in-new icon.  A link that raises, or
answers anything but an http or https address, is left out.

## An Online source

Add and Replace have an Online tab, and VPinMediaDB fills it for a media slot.  An extension can
fill it for any slot that takes a file: a media kind, an asset, a whole folder, or a table.

```python
ctx.sources.contribute(
    "files", "Example Files", ["playfield_video", "pup_pack", "table"],
    offers=files_for, take=take_file)
```

`offers(who, kind)` answers what the source has of that kind for one game, and `take(who, kind,
key)` is called with the key of the row a person picked.  `who` is `{game_id, vps_id, name,
table_id}`: the game the slot belongs to, and the entry whose files are asked for.  They differ
after Search Another Game, which asks for the files of a different entry and still fills this
game's slot.

`table_id` is the table the person chose in Save It As, and is empty for every table in the game
and for a slot with no such choice.  `take` gets the choice that stands when the row is picked.
`offers` is asked once for the slot whatever the choice, so it always gets an empty one.  When it
is not empty, save the file for that table.  Like the rest of `who` it is what the client sent, so
check that the table belongs to the game before you write anything for it.

```python
def files_for(who, kind):
    found = look_up(who["vps_id"], kind)
    return {
        "rows": [{"key": one.id, "name": one.title, "site": "example.com",
                  "page": one.page, "picture": one.preview, "tag": one.variant}
                 for one in found],
        "note": "" if found else "Nothing here yet",
    }


def take_file(who, kind, key):
    return {"job": start_download(who, kind, key)}
```

Core draws what the source answers and keeps the rest to itself:

- The tab is there on every slot whose kind some enabled source offers.  A slot nothing offers
  has no Online tab, so an install without such an extension looks as it did.
- The source is named first in each row's line, then `site`, which is a link to `page`, the
  file's page on that site.
- A row with `current` set is drawn as Current.
- Settings lists the source under Sources on the Library, Discovery page, with a switch.  A
  source switched off is not asked, and with every source off the tab says so.
- A Private game is never asked about in Add and Replace.  Discovery asks by VPS id alone, as
  it does for any game the library does not hold, and says nothing of the library.
- A pick that fills a slot already holding a file is confirmed first, naming what is there
  now.  A folder or a table is taken as picked.
- The words for an empty or failing tab name only the sources that offer this kind.

A row is `key` and `name`, both required, and `site`, `page`, `note`, `picture`, `tag` and
`current`, which is true or false.  `page` is an http or https address, and `picture` is a file
path or a web address, which needs `net:outbound` in the manifest's `capabilities`.  `note` under
the rows is a line of words, and `link` is `{"text", "to"}` for an address, `{"text", "page"}` for
one of the extension's pages, or `{"text", "settings": True}` for its Settings page.

A pick answers one of three things.  `{"job": id}` is a job the extension started, so the
dialog closes and says so, in the `message` beside it if there is one.  `{"action": dialog_key}`
opens one of the extension's own dialogs over Add or Replace, with the row picked as `rows` and
the game as `who`, for a pick that needs steps of its own.  `{"message": text}` is said and
nothing else happens.  A `message` of nothing but spaces counts as none.  An answer that is none
of them, an `action` beside a `job` or a `message`, a `job` that is not an id, or an `action` the
extension did not declare is refused.

An `offers` that raises, or answers a shape that is not this, costs that source's rows and
nothing else: the tab says it could not reach the source, with the reason, and the others are
still drawn.  A `take` that raises or answers wrongly is said as a failed fetch, with the
reason, and the dialog stays open.

A backglass has two kinds.  `backglass` is the picture, and `backglass_file` is the `.directb2s`
file a table loads.

### In Discovery

Discovery lists the files VPS has for a game, and an Online source can offer to get them.  A
row is drawn there, beside the VPS file it stands for, when it carries `vps_file_id`: the id VPS
gives that file.  Add and Replace ignore the id, and Discovery ignores a row without one.  Where
two sources offer the same file the first one registered owns the row.  Discovery asks by VPS
id alone, so `who["game_id"]` is empty for a game that is not in the library, and `offers`
should answer from `vps_id`.

A row may also say how it stands, and what can be done to it:

- `state` is `{"text", "level", "hint"}`.  `level` is `on`, `off`, `unknown`, `warn` or `bad`,
  and one with none is `unknown`.  The row shows `text` in place of the tick once the extension
  has supplied the file, and `hint` is its tooltip.  `unknown` reads as in progress; `warn` and `bad` need
  the person.
- `acts` is `[{"key", "label"}]`, one button for each, with no two sharing a key.

Three more functions, each optional, go beside `offers` and `take`:

```python
ctx.sources.contribute(
    "files", "Example Files", ["table", "pup_pack"],
    offers=files_for, take=take_file,
    take_many=take_ticked, act=press, look=page_of)
```

- `take_many(who, picks)` is called once with `[{"kind", "key"}]` for every file selected for that
  source, after the game is made if it was not in the library.  It answers `{"message": text}`
  or nothing.  A source without it can be selected in Discovery, and the download fails with the
  reason, so a source that sets `vps_file_id` gives this too.
- `act(who, kind, key, act)` is called when a button is pressed, and answers what `take` does,
  or nothing.  Discovery shows a `message` and opens an `action` dialog, and does not follow a
  `job`.  A press with an empty `kind` and `key` is the pane's own button; today that is `stop`, pressed once for each source with a row under
  way.  Core knows nothing of what a source is getting, so the source ends it.
- `look(who, kind, key)` is called when the row is opened, and answers `{"pictures": [address],
  "description": text, "files": [{"name", "size"}], "note": text}`, all optional.  At most 8
  pictures, 100 files and 4000 characters of description are drawn.  `size` is bytes, less
  than 2^63.

The pane asks `offers` again each time the registry says an offer for the entry moved.  Say so
with `ctx.sources.changed(vps_id)` when a download starts, ends or needs the person.  A pane that
is open redraws the rows whose state changed and leaves the rest alone.

## A name a command can use

A user writes commands that run when VPinFE starts and around every table.  Core declares what
one of them may say (`{table}`, `{rom}`, `{program}`), and an extension adds to that list.

```python
def season(values):
    # values is what this context has resolved so far. Answer a string; empty is an
    # answer, and this must not raise on an install that has none.
    return ctx.config.get("season", "")


ctx.tokens.offer("season", (ctx.tokens.TABLE,), season)
```

What it stands for is `token.season.says` in the extension's `i18n/en.json`, or `says=`.  A
name with nothing saying what it stands for is refused.

**The name carries the extension's id.**  You declare `season` and a user writes
`{example_scores.season}`.  The dotted half is built from the manifest rather than spelled
here, so two extensions may offer the same idea without reaching each other, and core's own
names, which never carry a dot, can grow without reaching either.  Who is playing is one of
core's: `{player}` is the initials of the one player up, and blank with several.

`contexts` says where the command runs.  `ctx.tokens.VPINFE` is the pair around VPinFE itself
and `ctx.tokens.TABLE` the pair around every table.  Declare only the ones the name means
anything in, because a name that is always blank costs a reader more than one that was never
offered.

`value` runs while a command is being prepared, so it has to answer quickly.  One that raises
stands for nothing and is logged, and the table still launches.  Pass `after_only=True` for
something only the half that runs afterwards can know.

**A name is removed when its extension is.**  A command still holding one is refused by that name,
the same as any name nothing declares.  The alternative is a `--flag` left with nothing after
it, reading whatever came next as its value.

## What a game tells you

Core events say who played and who rated what, as plain lists and dicts. `game` and
`ini_config` ride along on some for core's own subscribers and are not part of this contract.

| event | carries |
|---|---|
| `table.launching` | `table_id`, `source`. Every hook on it has finished before the table starts |
| `table.launched` | `game_id`, `table_id`, `source`; `up`, who the game counts for, taken once as it starts, each `{id, name, initials, owner, guest}`; and `private`, whether the game is Private |
| `table.exited` | `table_id`, `source`. Announced on every path out |
| `table.play_recorded` | the same as `table.launched`, once the game has ended and its play is written, with `private` read again then; `seconds` played; `reading`, the machine's high score table as read after the game, or None; `new_entries`, `[{player, entries}]` for each player with an entry new this game |
| `game.rated` | `game_id`; `player`, whose rating it is, `{id, name, initials, owner, guest}`, or None on an install with no owner yet; and `rating`, 0 to 10 in half stars, 0 meaning unrated. The owner's rating is the library's, anyone else's is in their own record |
| `account.share_changed` | `player`, `{id, name, initials, owner, guest}`; `extension`, whose account it is; and `share`, what it is now. It carries every extension's, so one reads only its own |
| `players.changed` | `state`, the whole roster: `{"players": [...]}`, each row as `ctx.players.roster()` gives it |
| `game.selected` | The wheel stopped on a game. It carries core objects (`game`, `neighbors`) that are not part of this contract; subscribe to know that it happened |

**A Private game's data is never sent anywhere.**  Not its plays, its time, its scores or its
rating, and not the fact that the library holds it: to no service, for no player, whoever was
up and whatever their Share says.  A game is Private when somebody marks it so.  The events
carry `private`.  A game's rows from `ctx.games` do not, and that call lists every game.  An
extension reads the flag and cannot set it.

A table's events carry `source`, who started it: `frontend`, `remote`, `api`, or `capture` for
a launch that records the table's media.  A recording is not a play.  Nobody is up for it, it
writes no play data and there is no `table.play_recorded` after it, so an extension counting
plays counts on that event or leaves out a `capture` launch.

```python
def on_played(**payload):
    if payload["private"]:
        return
    for credited in payload["new_entries"]:
        post_score(credited["player"]["id"], credited["entries"])


ctx.events.subscribe("table.play_recorded", on_played)
```

**A game counts for who was up.  A new score goes to the player whose initials it has**, up or
not.  An entry with no initials, or `???`, goes to the one player up, and with several up to
nobody.  Core matches them once, so no two extensions disagree about whose a score was.  An
entry is new when the table after the game holds it more times than the table before,
whatever its rank.  A machine that keeps one number has a new entry when it changed, and a
game with no reading before it has nothing new.  `reading` is `{rom, resolved_rom,
score_kind}` with either `entries`, each `{section, rank, initials, score, ...}`, or a single
`value`.

A service that takes one player a game decides for itself what to do with several up, and its
account's status line says so rather than going quiet.
