# `ctx.players`

Who plays is core's.  What a service calls them is the extension's.  Core keeps the roster (the
owner, the other players, the guests, who is up) and an extension holds an account for any of
them it can.  A row is plain data: `id`, `name`, `initials`, `owner`, `guest`, `up`,
`shares_initials_with`, `picture_version`.

## `ctx.players.roster`

`ctx.players.roster() -> list[dict]`.

Every player: the owner, the kept players, then the guests in the order they joined.  Names and
initials are about people, so this needs `players:read` in `scopes`.

```python
names = [one["name"] for one in ctx.players.roster()]
```

Raises `ContractError` without `players:read`.

## `ctx.players.get`

`ctx.players.get(player_id) -> dict | None`.  Required: `player_id`.

One row, or None.  Needs `players:read`.

```python
owner = next((one for one in ctx.players.roster() if one["owner"]), None)
initials = (ctx.players.get(owner["id"]) or {}).get("initials", "")
```

Raises `ContractError` without `players:read`.

## `ctx.players.up`

`ctx.players.up() -> list[dict]`.

The rows of the players the next game counts for.  Needs `players:read`.

```python
if len(ctx.players.up()) > 1:
    ctx.logger.info("several players are up")
```

Raises `ContractError` without `players:read`.

## `ctx.players.record`

`ctx.players.record(player_id, game_id) -> dict | None`.  Required: both.

What a player other than the owner has done with one game, as
`GET /api/v1/players/{id}/record` lists it: `play_count`, `play_time_seconds`, `last_played`,
`best_score`, `rating`.  You get None for the owner, whose record is the library's and is read
through `ctx.games`, and for a player nobody has.  Read only.  Needs `players:read`.

```python
record = ctx.players.record(player_id, game_id) or {}
plays = record.get("play_count", 0)
```

Raises `ContractError` without `players:read`.

## `ctx.players.sharing`

`ctx.players.sharing(player_id) -> bool`.  Required: `player_id`.

Whether this player's account with this extension is sharing.  Share is core's switch, the same
on every account.  It is off until somebody turns it on, except for a guest who joined with a
card, whose account shares.  While it is off, an extension sends nothing on its own.  Something
the person asks for manually, an act, is theirs to have asked.  Needs nothing declared.

```python
if ctx.players.sharing(player_id):
    send(player_id)
```

Does not raise.

## `ctx.players.picture`

`ctx.players.picture(player_id) -> Path | None`.  Required: `player_id`.

The path of the player's picture file.  You get it only if the extension declared
`sends_picture=True` on its account, and only while that player's Share here is on.  Otherwise
you get None: Share off, a guest, no picture, or `sends_picture` not declared.  There is no
second switch.

```python
path = ctx.players.picture(player_id)
if path is not None:
    upload(path.read_bytes())
```

Does not raise.

## `ctx.players.account`

`ctx.players.account(player_id) -> dict[str, str]`.  Required: `player_id`.

The values this extension holds for the player, secrets included, or an empty dict when it
holds none.  Values are strings.

```python
held = ctx.players.account(player_id)
user_id = held.get("user_id", "")
```

Does not raise.

## `ctx.players.set_account`

`ctx.players.set_account(player_id, values)`.  Required: both.

Replaces the account's values.  An empty `values` removes the account.  They are kept in the
extension's settings file under `accounts`, keyed by player id.  **A guest's are held in memory
and never written**, so a visitor leaves nothing on the disk and the values go when the guest
does.  Removing a player forgets their account with every extension.

```python
ctx.players.set_account(player_id, {"user_id": "ace", "key": secret})
ctx.players.set_account(player_id, {})    # disconnect
```

Does not raise.

## `ctx.players.holders`

`ctx.players.holders() -> list[str]`.

The ids of every player holding an account here, in roster order.

```python
for player_id in ctx.players.holders():
    refresh(player_id)
```

Does not raise.
