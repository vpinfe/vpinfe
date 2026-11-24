# Entries, catalogs and tokens

Three small ways an extension adds to what a theme is handed: something on every entry, a link
to where a game lives somewhere else, and a name a user may write into a command.

## `ctx.entries`

Adds something to every entry a theme is handed.  See
[Something to an entry](offers.md#something-to-an-entry).

### `ctx.entries.contribute`

`ctx.entries.contribute(key, fetch)`.  Required: both.

Answer about one game at a time.  `fetch(game)` receives `{game_id, vps_id, name,
manufacturer, year, ipdb_id}`, each the effective value, and returns whatever a theme should
read, or None where there is nothing to say.  Core calls it on its own thread when the wheel
stops, so it may block.  It must not raise for a game it simply has no answer about.

```python
ctx.entries.contribute("rating", lambda game: {"stars": 4} if game["vps_id"] else None)
```

A theme reads `entry.ext.rating`.  An empty key raises `ContractError`.

### `ctx.entries.stale`

`ctx.entries.stale(key)`.  Required: `key`.

Tell core that what this extension answered under `key` is out of date.  Core drops what it
holds, and each game is asked again the next time it is reached.  Only the extension that
contributes a key can drop it.

```python
ctx.entries.stale("rating")
```

Does not raise.

## `ctx.catalogs`

Links a game, a table or a file to where it lives somewhere else.  See
[An outside link](offers.md#an-outside-link).

### `ctx.catalogs.contribute`

`ctx.catalogs.contribute(key, name, subject, link)`.  Required: all four.

`subject` is `game`, `table` or `file`.  `link(item)` receives a plain description of one and
returns its address there, or `""`.

```python
ctx.catalogs.contribute("scores", "Example Scores", "game",
                        lambda game: f"https://scores.example.com/t/{game['vps_id']}")
```

The user sees a row named `name` with an open-in-new icon.  A `link` that raises, or answers
anything but an http or https address, is left out.  Any other `subject` raises
`ContractError`.

## `ctx.tokens`

Offers a name a user may write into a command.  See
[A name a command can use](offers.md#a-name-a-command-can-use).

### `ctx.tokens.VPINFE`

The string `"vpinfe"`: the context of the pair of commands around VPinFE itself.

```python
ctx.tokens.offer("season", (ctx.tokens.VPINFE, ctx.tokens.TABLE), lambda values: "spring")
```

### `ctx.tokens.TABLE`

The string `"table"`: the context of the pair of commands around every table.

```python
ctx.tokens.offer("season", (ctx.tokens.TABLE,), lambda values: "spring")
```

### `ctx.tokens.offer`

`ctx.tokens.offer(name, contexts, value, *, says="", after_only=False) -> str`.
Required: `name`, `contexts`, `value`.

Offers `{<extension>.<name>}`.  It answers the full name, which is what a user types.
`value(values)` receives what the command context has resolved so far and answers a string;
empty is an answer.

```python
full = ctx.tokens.offer("season", (ctx.tokens.TABLE,), lambda values: "spring")
# full == "example_scores.season"
```

The user sees it in the list of names a command may use, with what it stands for.  It raises
`ContractError` for a name with nothing saying what it stands for (`says`, or
`token.<name>.says` in the catalog), an unknown context, or a name already taken.
