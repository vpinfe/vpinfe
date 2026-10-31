# The library and ways to play

`ctx.games` is the library and everything core offers by name.  `ctx.uploads` imports files into
it, the way a drop onto the Manager does.  `ctx.sources` is where Add and Replace can get a
file from online.  `ctx.collections` is the collections.  `ctx.apps` is how another format of
table becomes first class.

## `ctx.games`

The library, as an extension is allowed to see it.  It is not a second implementation of the
HTTP API.  It calls the API's own route functions, so the two cannot answer differently.  An
extension in this process cannot use the API over HTTP, because a synchronous call into the
server it is running inside deadlocks, and it may not import the library.  This is the door.

It is bounded twice.  The manifest's `scopes` decide which calls an extension may make, and a
path it passes has to be inside a folder it declared through `ctx.files.set_roots`.  A call
that needs a scope the manifest lacks raises `ContractError`, and so does a path outside the
declared folders.

| call | scope it needs |
|---|---|
| `kinds`, `existing`, `folder_name_for`, `folder`, `survey`, `companions_of`, `companions_for`, `roms_of`, `reads_of`, `saved_sets`, `asset_kinds` | `games:read` |
| `create`, `add_table`, `put_saved_sets`, `put_asset`, `put_rom_alias`, `put_media` | `games:write` |
| `put_manufacturer_logos` | `config:write` |
| `reaches` | none |

The rest of the library is offered by name, listed under [Offered by name](#offered-by-name)
below.

### `ctx.games.kinds`

`ctx.games.kinds() -> tuple[str, ...]`.

Every media kind this build stores.  It is the vocabulary an importer maps onto, so the
importer need not hard-code a list that would go stale.

```python
wanted = [kind for kind in ("wheel", "backglass", "flyer") if kind in ctx.games.kinds()]
```

### `ctx.games.existing`

`ctx.games.existing() -> list[dict]`.

Every game already here, in the little an importer needs to recognize one: `game_id`,
`folder_name`, `name`, `vps_id`, `ipdb_id`, and `table_files`, the lowercase names of the game
files it holds.  It is not the whole library.  A second run asks "have I made this one before",
and folder name, catalog id and title answer it.

```python
made = {one["folder_name"] for one in ctx.games.existing()}
```

### `ctx.games.folder_name_for`

`ctx.games.folder_name_for(name) -> str`.  Required: `name`.

The folder a game of this name would get.  Ask rather than guess: `Star Trek: The Next
Generation` and `Star Trek The Next Generation` are one folder here, and an importer that reads
them as two fails on a name already taken.

```python
folder = ctx.games.folder_name_for("Star Trek: The Next Generation")
```

### `ctx.games.folder`

`ctx.games.folder(game_id) -> str`.  Required: `game_id`.

The game's folder on disk.  Raises `LookupError` for an unknown game.

```python
where = ctx.games.folder(game_id)
```

### `ctx.games.survey`

`ctx.games.survey(path) -> dict`.  Required: `path`.

What a folder holds: `game_folders` the library would read as games, and the `loose` table
files lying in it.  A name that starts with a dot is left out.  It works only on a folder this
extension works from.

```python
found = ctx.games.survey(ctx.config.get("source"))
count = len(found["game_folders"]) + len(found["loose"])
```

Raises `ContractError` for a folder outside `ctx.files.roots()`.

### `ctx.games.create`

`ctx.games.create(name, location="") -> str`.  Required: `name`.

Makes an entry and answers with its id.  `location` names a library folder, or is empty for the
default.

```python
game_id = ctx.games.create("Taxi")
```

The user sees a new game in the library.  Raises `LookupError` if the entry is not among the
games this device reads.

### `ctx.games.add_table`

`ctx.games.add_table(game_id, path, move=False, names=()) -> dict`.
Required: `game_id`, `path`.

Copies a game file into an entry, with whatever belongs to it.  It answers `table_id`,
`companions`, `rom` (the ROM the table turns out to need) and `named`, those of `names` that
the table's script mentions as a string, whole word and any case.  `move=True` takes the files
out of where they were, which needs the `fs:write` capability.

```python
added = ctx.games.add_table(game_id, "/data/old/Taxi.vpx", names=("Taxi",))
table_id = added["table_id"]
```

Raises `ContractError` for a path outside the declared folders or `move` without `fs:write`,
and `FileNotFoundError` for a file that is not there.

### `ctx.games.companions_of`

`ctx.games.companions_of(path) -> tuple[str, ...]`.  Required: `path`.

The file names that would come with this table if it were added.  Use it for counting
beforehand, so a plan and the run that follows it agree.

```python
beside = ctx.games.companions_of("/data/old/Taxi.vpx")
```

Raises `FileNotFoundError` for a table that no longer exists, and `ContractError` outside the declared
folders.

### `ctx.games.companions_for`

`ctx.games.companions_for(paths) -> dict[str, tuple[str, ...]]`.  Required: `paths`.

`companions_of` for many tables, each folder listed once.  On a network share the listing is
the cost, so ask for a library together.  The answer is keyed by the path as given.  Where
`companions_of` raises for a table that no longer exists, this leaves it out, so a table with nothing
beside it can be told from one that no longer exists.

```python
beside = ctx.games.companions_for(["/data/old/Taxi.vpx", "/data/old/Eight Ball.vpx"])
```

Raises `ContractError` outside the declared folders.

### `ctx.games.roms_of`

`ctx.games.roms_of(paths) -> dict[str, str]`.  Required: `paths`.

`{path: rom}` for the tables whose script names a ROM.  Each table's script is read, and a
table that no longer exists, cannot be read or names none is left out.

```python
roms = ctx.games.roms_of(["/data/old/Taxi.vpx"])
```

Raises `ContractError` outside the declared folders.  Opening a table costs a read of the file,
so ask for a library in batches and keep what you are told.

### `ctx.games.reads_of`

`ctx.games.reads_of(paths, names=()) -> dict[str, tuple[str, tuple[str, ...]]]`.
Required: `paths`.

`roms_of` and, from the same read, which of `names` each script mentions.  It answers
`{path: (rom, named)}` and keeps a table that names no ROM, with `""`.

```python
read = ctx.games.reads_of(["/data/old/Taxi.vpx"], names=("Taxi", "DOF"))
rom, named = read["/data/old/Taxi.vpx"]
```

Raises `ContractError` outside the declared folders.

### `ctx.games.saved_sets`

`ctx.games.saved_sets(path) -> tuple[str, ...]`.  Required: `path`.

The names of the sets a `VPReg.stg` holds, for matching to the tables that save under them.
Empty where the file is not one.

```python
sets = ctx.games.saved_sets("/data/old/VPReg.stg")
```

### `ctx.games.put_saved_sets`

`ctx.games.put_saved_sets(game_id, path, sets) -> int`.  Required: all three.

Writes the named sets of a `VPReg.stg` as the game's own `user/VPReg.ini`, rather than copying
the file (a copy would hand every table the values every other table saved).  It answers how
many sets were written.  A set the game's ini already holds is left as it is.

```python
written = ctx.games.put_saved_sets(game_id, "/data/old/VPReg.stg", ["Taxi"])
```

### `ctx.games.asset_kinds`

`ctx.games.asset_kinds() -> tuple[str, ...]`.

The kinds that live in a folder of their own, and so can be placed whole: `nvram`, `cfg`,
`scripts` and the like.

```python
placeable = ctx.games.asset_kinds()
```

### `ctx.games.put_asset`

`ctx.games.put_asset(game_id, kind, path, rom="") -> str`.  Required: `game_id`, `kind`,
`path`.

Puts a file or a whole folder where this kind of asset belongs: a ROM set, an alternative sound
bank, a color set.  `rom` is for kinds that live under a ROM name.  `nvram` and `cfg` go under
`pinmame/`, `scripts` in the game's `scripts/`, and `dmd_content` as a file or a folder beside
the table.  It answers the path it took, relative to the game, with forward slashes.  It never
replaces.

```python
landed = ctx.games.put_asset(game_id, "nvram", "/data/old/taxi.nv", rom="taxi")
```

Raises `FileExistsError` where something is already there, `FileNotFoundError` for a missing
source, `ValueError` for a kind with no folder or one that needs a ROM, and `ContractError`
outside the declared folders.

### `ctx.games.put_rom_alias`

`ctx.games.put_rom_alias(game_id, alias, rom) -> str`.  Required: all three.

Writes the one `alias,rom` line a table needs as the game's `pinmame/alias.txt`: the name the
table's script asks for and the ROM set that answers it.  It answers the path, relative to the
game.

```python
ctx.games.put_rom_alias(game_id, "taxi_l4", "taxi")
```

Raises `FileExistsError` where the game holds a name map, and `ValueError` for an empty name or
one with a comma or a newline.

### `ctx.games.put_manufacturer_logos`

`ctx.games.put_manufacturer_logos(makers, candidates) -> dict`.  Required: both.

Copies each `(maker name, image)` of `candidates` that serves one of `makers` into the
manufacturer logos folder, which every theme shares.  It replaces none.  It answers
`{"placed": [...], "held": [...]}` by file name.  Needs `config:write`.

```python
done = ctx.games.put_manufacturer_logos(["Bally"], [("Bally", "/data/old/bally.png")])
```

Raises `ContractError` outside the declared folders.

### `ctx.games.put_media`

`ctx.games.put_media(game_id, kind, path, table_stem="", replace=True, page=1) -> str`.
Required: `game_id`, `kind`, `path`.

Puts a file in one of an entry's media slots and answers what it was saved as.  `replace=False`
fills an empty slot and raises `FileExistsError` where one is held.  `page=n` adds page n of a
flyer or instruction card beside the page 1 the slot shows, up to one past the last.

```python
ctx.games.put_media(game_id, "wheel", "/data/old/Taxi.png", replace=False)
```

Raises `ValueError` for a kind that is not in `ctx.games.kinds()`, `FileExistsError` as above,
and `ContractError` outside the declared folders.

### `ctx.games.reaches`

`ctx.games.reaches() -> tuple[str, ...]`.

The names core offers that this extension may actually call: what is offered, narrowed to the
scopes its manifest declared.  It includes the names in [Offered by name](#offered-by-name).

```python
if "rate_table" in ctx.games.reaches():
    ctx.games.rate_table(game_id, table_id, 8)
```

Does not raise.

### Offered by name

Anything else core offers is reached as `ctx.games.<name>(...)`, with the signature the API's
own function has.  Not every name is on every build, and `ctx.games.reaches()` lists what is.  A
name that is not offered raises `AttributeError`, and one that needs a scope the manifest lacks
raises `ContractError` when it is looked up.

| scope | names |
|---|---|
| `games:read` | `list_games(q="", limit=0, offset=0)`, `get_game(game_id)`, `game_tables`, `game_media(game_id, codecs=False)`, `table_media(game_id, table_id, codecs=False)`, `media_detail(game_id, kind, table_id="", page=1)`, `vps_state`, `vps_details`, `vps_catalog(downloading=None)`, `vps_settle(rows)`, `vps_entry(vps_id)`, `vps_releases(vps_id, listed_as="tableFiles")` |
| `games:write` | `set_details(game_id, **fields)`, `rate_game(game_id, rating)`, `rate_table(game_id, table_id, rating)`, `set_tags(game_id, tags)`, `set_favorite(game_id, favorite)`, `set_default_table(game_id, table_id)`, `set_play_record(game_id, play_count=None, play_time_seconds=None, last_played=None)`, `reset_play_record(game_id)`, `take_vps_details(game_id, fields=None)`, `match_game(game_id, vps_id, guess=False)`, `remove_media(game_id, kind, table_id="", page=None)`, `get_missing_media(game_ids)`, `forget_table(game_id, table_id)`, `add_keyed_table(game_id, app_id, key)` |
| `config:read` | `new_game_destination()`, `table_program(table_id)` |
| `config:write` | `use_library_folder(path)`, `write_table_settings(table_id, values)`, `use_logos_for_wheels()` |
| `launch:invoke` | `launch_game(game_id, table="")` |

A few of them need a word more:

- `vps_catalog(downloading=None)` answers `{enabled, ready}`.  `enabled` is the *Match new
  games* switch.  Where it is on and there is no catalog on disk, one is downloaded first, calling
  `downloading` just before it starts.
- `vps_settle(rows)` takes rows of what a source knows (`vps_id`, `ipdb_id`, `title`,
  `manufacturer`, `year`, `folder`) and answers each in order with the entry it is: `vps_id`,
  `how` (`vps_id`, `ipdb` or `name`) and `entry` (`id`, `name`, `manufacturer`, `year`, `type`,
  `ipdb_id` and `folder_name`, the folder VPinFE would name the game).  Where nothing fits, it
  answers `vps_id` empty with a `reason` (`not_found`, `two_entries` or `ipdb_differs`) and up
  to five `candidates`.  It reads the catalog on disk and writes nothing.
- `vps_entry(vps_id)` answers what the catalog says about one entry: `vps_id`, `name`,
  `manufacturer`, `year`, `type`, `folder_name` (the folder VPinFE would name the game),
  `releases` (how many table files it lists), `img_url` (the picture its page on VPS leads with,
  or empty) and `url` (that page).
- `vps_releases(vps_id, listed_as="tableFiles")` answers `{"releases": [...]}`, every file of
  one kind the entry lists, in the order the catalog holds them.  `listed_as` is the catalog's own
  key: `tableFiles`, `b2sFiles`, `romFiles`, `altColorFiles`, `altSoundFiles`, `pupPackFiles`,
  `povFiles`, `ruleFiles`, `wheelArtFiles` or `topperFiles`.  A file has `vps_file_id`,
  `version`, `authors`, `format`, `features`, `comment`, `img_url`, `updated_at` (ISO 8601, UTC),
  `url`, `urls` and `mod_of`.  `urls` is every link the catalog gives for the file, each as
  `{url, broken}`, in the catalog's order, with the ones it marks broken kept and marked; an
  address that is not a web address is left out.  `url` is the first of them not marked broken,
  or the first where all are, and empty where there are none.  `mod_of` is `None` where the file
  is no mod.  Where it is one, `mod_of` says what of: `vps_file_id` (the file it is based on;
  empty where VPS does not say which, or names one the catalog cannot find), `version`,
  `authors`, `game` (only where it is another game), `url`, `note` (VPS's own comment, on a mod
  tagged without a link), and `game_id` and `table_id` for the table in this library matched to
  that file, empty where there is none.
- Both read the catalog on disk and write nothing.  `vps_releases` also reads the library, to
  fill `mod_of`'s `game_id` and `table_id`.  They need `games:read`, as `vps_settle` does, and
  not `vps:read`, which is the scope the same two calls take over HTTP.  An id the catalog does
  not have raises an error whose text says so, for `ctx.why`, and so does a `listed_as` that is
  not one of the keys above.  With no catalog on disk every id is unknown; `vps_catalog()`
  downloads one where *Match new games* is on, as above.
- `match_game` matches a game to an entry as *Matched on import*, or as a plain guess a rematch
  may replace where `guess` is true.
- `table_program(table_id)` answers what the program that plays the table is, as the settings it
  reads depend on it: `{"plugins": [...], "defaults": {...}, "build": 6293, "dmd_in_file": true}`,
  or `None` where its app does not say.  `plugins` lists the plugins the program loads, empty for
  a program known to have none and `None` where they cannot be read.  `defaults` is by key what a
  setting holds while the program's file leaves it blank, for the settings whose default is the
  build's.  `build` is the number the app counts builds by, `None` where unknown.  `dmd_in_file`
  is true where the build keeps the DMD's place in each backglass file and not in the settings.
  A table with no launcher raises, as `write_table_settings` does.
- `write_table_settings` writes values as one table's own settings, never the launcher's, and
  leaves out a value the launcher already has, so a table whose values all match gets no file.
  `use_logos_for_wheels()` sets `[Media] wheelset` to the reserved `logo` set so every game's
  wheel shows its logo, and answers `{"wheelset": "logo"}`.
- `get_missing_media(game_ids)` starts the media fill for those games whatever the *Get Media
  for New Games* switch says.  It answers `{"started": true}`, or `{"started": false}` while
  another fill runs.  The fill does not queue.
- Launching is gated apart from the rest.  It takes over the device rather than editing a
  record, so `games:write` does not grant it.  An extension that starts a game asks for
  `launch:invoke` by name, and whoever installs it reads it by name.
- There is no `set_private`.  An extension can read a Private game's flag and never set it.

```python
found = ctx.games.list_games(q="taxi", limit=10, offset=0)
ctx.games.rate_table(game_id, table_id, 8)
```

## `ctx.uploads`

Importing files, the way a drop onto the Manager does.  It is the same analysis, plan and
import the upload routes run, so an extension is told what an HTTP client is told and refused
for the same reasons.  The routes are not reachable from inside the process, for the reason
given under [`ctx.games`](#ctxgames).

The source is a file, a folder or an archive already on this device.  It is read where it is
and left there.  Each call opens an upload session over it, answers and ends the session,
whether it worked or not, so nothing is held between calls: `plan` and then `execute` read the
source twice, and `execute` imports what the source holds when it runs.

It is bounded three ways:

- The `uploads:write` scope decides whether an extension may import anything.
- The source has to be inside a folder the extension declared through `ctx.files.set_roots`,
  which needs `fs:read`.  The routes bound their sources by the folders the person has
  allowed, which an extension's folders are not part of.
- A game that already exists is named by `game_id`, which needs `games:read` as well, and core
  finds the folder.  The routes take a folder, which would let an extension aim an import
  anywhere core can write, so `game_dir` is refused here.

| call | scope it needs |
|---|---|
| `analyze`, `plan`, `execute` | `uploads:write` |
| any of them with a `game_id` | `games:read` as well |

`plan` and `execute` take the same keyword arguments, and `execute` takes `selected` and
`declared` besides.  A key core does not read raises `ContractError` and lists the ones it does,
so a misspelled key is refused rather than importing with the default.  So is a value of the
wrong type: the text keys take a string, `allow_new_game` and `add_table` a bool, `selected` a
list of ints.

| key | what it says |
|---|---|
| `vps_id` | The catalog entry the files are for.  A new game takes it; `execute` refuses it for a game that already exists, since that game's entry is its own |
| `game_id` | The game they belong to.  Left out, the files make a new game, if `allow_new_game` is true |
| `allow_new_game` | Make a new game of a source that holds a table.  Not with a `game_id`, which is refused: the new game would be made and the named one left alone |
| `add_table` | With `game_id`: the table is one more for that game rather than its replacement.  A file name the game already has comes back blocked |
| `media_kind` | With `game_id`: the source is for one picture or video slot, such as `wheel`, and any image will do.  A slot holds one thing, so a source with more is refused |
| `asset_kind` | Import one kind of asset only.  Everything else the source holds is listed as blocked |
| `rom_name` | The ROM name the files go under, for the kinds that live under one.  A plain name: one with a path separator or `..` in it is refused |
| `location_id` | The library folder to make a new game in, in place of the one marked for new games.  One that names no library folder, or cannot be written to, refuses the import |
| `new_game_dir_name` | The new game's folder name.  Left out, it is the catalog's name for the entry, then the table's file name |
| `convert` | What each picture, sound or video core has to convert on the way in is made as: `{"rows": {file: {"to": ..., "video": ..., "quality": ..., "sound_in_video": ...}}, "wait": bool}`, with each file named by the `member` of its `plan` item, and every value text or `None` for no choice.  `to` is the format, or `asis` to keep the file untouched.  A file or a value left out takes the person's Preferred Formats where they hold one, else core's recommendation.  A name that matches no file is ignored.  `wait` (default true) holds a video re-encode while a table is playing, and an `execute` called with it waits until the table stops |
| `selected` | `execute` only: the `index` of each plan item to import.  Left out, every item the plan lists is imported |

### `ctx.uploads.analyze`

`ctx.uploads.analyze(path) -> dict`.  Required: `path`.

What the source holds, as the import would read it.  It answers `source_kind` and `source_name`,
`has_game` (a table is among it), `assets` (what was recognized, each with its `kind`, `label`,
`size` and `detail`), `files` (every file the source holds, as a path inside it), `notes`,
`unrecognized` (the paths no rule claimed), `placeable` (those of them that are pictures,
sounds or videos, which an import still offers places to), `bundle_info` (the
`.info` the source carried, if it did) and `error`, which is empty unless the source could not be
read.

```python
found = ctx.uploads.analyze("/data/incoming/Taxi")
if not found["has_game"]:
    return ctx.t("message.no_table")
```

Raises `ContractError` for a path outside the declared folders or without `uploads:write`, and
an error whose text says why, for `ctx.why`, where nothing is at the path.

### `ctx.uploads.plan`

`ctx.uploads.plan(path, **request) -> dict`.  Required: `path`.

What `execute` with the same arguments would do, before it does any of it.  It answers
`game_dir` (the folder the files go in, which for a new game is the one about to be made), `new_game_dir_name`, `rom_name`,
`items` and `blocked`.  Each item has an `index`, a `kind`, a `label`, its `destination`, an
`action`, `default_enabled` (the Manager's dialog selects the item when it is true; `execute` without `selected` imports every item either way), a `size`, a
`detail`, `replaces` (what it would overwrite, if anything) and `made_from_it` (the tables in the
folder a patch was made from this file).  Each blocked entry has a `kind` and a `reason`.  It
writes nothing, so it can be called as often as a step is shown.

```python
plan = ctx.uploads.plan("/data/incoming/Taxi", game_id=game_id, add_table=True)
rows = [[item["label"], item["destination"]] for item in plan["items"]]
```

Raises `ContractError` as `analyze` does, and for a key the import does not read.  The refusals
the upload routes give raise an error whose text says why, for `ctx.why`: a catalog entry that
is not there, a source nothing can be made of, nowhere to put a new game.

### `ctx.uploads.execute`

`ctx.uploads.execute(path, *, declared=None, **request) -> dict`.  Required: `path`.

Imports the source into the library.  It answers `imported` (the kind of each thing that was
imported), `skipped` (the kinds left out), `game_dir`, `new_game`, `media_kinds`, `added_tables`,
`patched_tables`, `renamed`, `blocked`, `declared` and, where they apply, `vps_associated`, `vps_matched` and
`vps_error`.  The last three are the catalog step that follows an import, which may fail without
failing it: the files are already in the library by then.  This is the call that changes
anything, and it can take as long as copying the files does, so it belongs in a
[job](context.md#ctxjobssubmit) rather than in a dialog's `check`.

`declared` says what each file is, keyed by the name it has in the source.  Each value is a
dict of strings, `vps_file_id`, `host_item_id`, `host`, `game_id`, `table_id` and `confirmed_by`,
every one optional.  `game_id` and `table_id` are the library's own ids for the game and the table
the file is, which an extension that downloaded it for a game already in the library knows.  `confirmed_by` is how the extension knows: `declared` when it fetched the file from
that record, `user` when a person picked it.  A claim that names a record has to say how it is
known, or `execute` raises before it imports anything.  There is no value for a guess.  A name
the plan does not import is ignored, and the report's `declared` lists the destinations that
were recorded.

```python
report = ctx.uploads.execute(
    "/data/incoming/Taxi", game_id=game_id, add_table=True,
    declared={"Taxi 1.2.vpx": {"host": "example", "host_item_id": "t-9",
                               "confirmed_by": "declared"}})
```

Raises `ContractError` as `plan` does, and for a `declared` field it does not take.  The
refusals the routes give raise an error whose text says why, for `ctx.why`, and so does a
source with nothing in it to import.

## `ctx.sources`

An Online source in Add and Replace.  See [An Online source](offers.md#an-online-source).

### `ctx.sources.contribute`

`ctx.sources.contribute(key, name, kinds, offers, take, take_many=None, act=None, look=None)`.
Required: the first five.

`key` is lower-case words joined by underscores, and the source is stored under
`<extension>.<key>`.  `name` is what the person reads.  `kinds` is a list of the names a slot is
opened under: a media kind (`ctx.games.kinds()`), an asset kind that has a dialog (`ini`, `pov`,
`script`, `scv`, `nix`, `rom`, `readme`, `music`, `pup_pack`, `altsound`, `altcolor_serum`,
`altcolor_vni`), the name the asset lens gives a kind that is a folder of files (`alt_sound`,
`alt_color`), or `table`.  `backglass` is the picture and `backglass_file` is the `.directb2s`,
so an extension that reads a VPS kind of `backglass` for a `.directb2s` offers `backglass_file`.
A source that has both colour formats offers `alt_color`, which is asked once for the two.  A
name core has no slot for raises `ContractError`, and the message lists the names it has, and so
does a list that is not a list.

`offers(who, kind)` is called with `who`, `{game_id, vps_id, name, table_id}`, and the kind of the slot,
as spelled in `kinds`.  It answers `{"rows": [...], "note": text, "link": {...}}`, each of the
three optional.  `take(who, kind, key)` is called with the key of the row picked and answers
`{"job": id}` with an optional `message`, `{"action": dialog_key}` or `{"message": text}`, and
nothing else.

A row may add `vps_file_id`, `state` and `acts`, and the source may give `take_many`, `act`
and `look`, so Discovery can draw it: see [In Discovery](offers.md#in-discovery).  Each is
called with `who` first, and a value that is not a function raises `ContractError`.

```python
ctx.sources.contribute("files", "Example Files", ["wheel", "pup_pack"],
                       offers=files_for, take=take_file)
```

Needs `games:read` in the manifest's `scopes` and Library in its `tiers`; without them the call
raises `ContractError`, and so does a `key` used twice, a `name` or `key` that is empty, and an
`offers` or `take` that is not callable.  A source is removed when its extension is switched off.

### `ctx.sources.changed`

`ctx.sources.changed(vps_id)`.  Required: `vps_id`.

Says what this extension's sources offer for the VPS entry has moved, so a Discovery pane open
on it asks again.  A pane open on another entry does not.  The registry keeps the last 200; a
pane that fell behind further asks again whatever the id.

## `ctx.collections`

Collections, offered by name the way `ctx.games` is: the API's own functions, gated by the
scopes its routes use, with `reaches()` listing what the manifest allows.  A name that is not
offered raises `AttributeError`, and one that needs a scope the manifest lacks raises
`ContractError` when it is looked up.

| scope | names |
|---|---|
| `collections:read` | `list_collections()`, `get_collection(name)`, `collection_games(name)`, `collection_members(name)` |
| `collections:write` | `create_collection(name, games=(), description="")`, `delete_collection(name)`, `add_to_collection(name, game_id, table_id="", after_table=None)`, `remove_from_collection(name, game_id, table=None)`, `set_collection_order(name, games)`, `rename_collection(name, new_name)`, `describe_collection(name, description)` |

- `create_collection` makes a hand-picked collection.  Rules, ordering by a field, exclusions
  and pictures are not offered; they are set in the Manager.
- `add_to_collection` and `delete_collection` work on a Smart collection too, as they do over
  the API.  A game added to one is kept whatever the rules say, and deleting one discards its
  rules.
- Adding a game already in the collection succeeds and changes nothing.  `table_id` holds the
  member to one table; left out, the member is the game and follows its default table.
- `remove_from_collection` with no `table` removes every row that names the game.  `table` set
  to a table id removes the row for that table, and `""` the row that follows the game's
  default table.
- `set_collection_order` takes the game ids of a hand-picked collection in the order wanted,
  one for each row, so a game holding two named tables is listed twice.  It refuses a list that
  does not name exactly its members, or a collection whose order its rules decide.
- A Private game is not left out of these answers, as it is not from `ctx.games`.

```python
ctx.collections.create_collection("Williams", games=ids)
ctx.collections.add_to_collection("Williams", game_id)
```

### `ctx.collections.reaches`

`ctx.collections.reaches() -> tuple[str, ...]`.  The names above this extension may call, which
is what core offers narrowed to the scopes its manifest declared.  Does not raise.

## `ctx.apps`

A way to play a table.  See [A way to play a table](offers.md#a-way-to-play-a-table).

### `ctx.apps.provide`

`ctx.apps.provide(**described) -> str`.  Required: `id` and `suffixes`.

Adds an app, described in plain data, and answers the id it took.  Keys: `id`, `suffixes`, and
optionally `name`, `accepts_keys`, `companions`, `vps_formats`, `fields`, `kinds` and
`command`.  `vps_formats` is the VPS `tableFormat` values the app plays (`VPX`, `VP9`, `FP`,
`FX`, `FX2`, `FX3`, `PC`), a tuple because one program can play several.  A table of this app
can only be matched to a VPS release filed under one of them; an app that declares none is
matched to any release.  `command(entry, settings)` receives `entry` as `{entry_id, game_dir, table, key}` and
`settings` as the launcher's values, and answers a **list** of arguments, never a string.  It
answers nothing to run the launcher's configured binary and arguments instead.  Needs the
`apps:provide` scope.

```python
ctx.apps.provide(
    id="fp",
    name="Future Pinball",
    suffixes=(".fpt",),
    vps_formats=("FP",),
    fields=({"key": "bam_path", "path": "dir"},),
    command=lambda entry, settings: ["/opt/fp", "--play", entry["table"]],
)
```

The user sees a launcher type of that name, and tables of those suffixes are importable.
Raises `ContractError` without `apps:provide` in `scopes`, and refuses an id already in use or
a suffix an app this build ships already claims.

### `ctx.apps.suffixes`

`ctx.apps.suffixes() -> tuple[str, ...]`.

Every file extension this install can play, its own and any provided.  It is not gated, since
it says what the build can do and not what the user has.  An importer deciding whether a
foreign library is worth importing needs it before it has been granted anything.

```python
playable = ".fpt" in ctx.apps.suffixes()
```

### `ctx.apps.names`

`ctx.apps.names() -> tuple[str, ...]`.

The product names of the apps here.  It is for a source that says which program a system used
but not which files it holds, where a database found in a folder named after the program is
often all there is.

```python
if "Future Pinball" in ctx.apps.names():
    ctx.logger.info("Future Pinball can be played here")
```

### `ctx.apps.plays`

`ctx.apps.plays(name) -> bool`.  Required: `name`.

Whether anything here plays a file of this name, or this bare suffix.

```python
if ctx.apps.plays("Taxi.fpt"):
    queue("Taxi.fpt")
```

### `ctx.apps.provided`

`ctx.apps.provided() -> tuple[str, ...]`.

The ids this extension has added, which is what gets taken back with it.

```python
ctx.logger.info("providing %s", ", ".join(ctx.apps.provided()))
```

### `ctx.apps.withdraw`

`ctx.apps.withdraw()`.

Takes every provided app back, and their words.  Core calls this when the extension is
unloaded, and an extension has no reason to.  A disabled extension therefore leaves no suffix
claimed by something that is no longer there.

```python
ctx.apps.withdraw()    # the host does this on unload
```
