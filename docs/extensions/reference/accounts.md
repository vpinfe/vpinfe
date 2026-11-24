# Accounts

`ctx.ui.account(...)`.  Who plays is core's; what a service calls them is the extension's.  An
extension can hold an account for any player: a user id and key at a scores site, a name a
leaderboard posts under.  Like settings, you declare an account and core shows it.  Core asks
the extension's functions with the player's id, and shows the same panel for every extension
that offers one.  A user id is chosen freely, claimed deliberately, then read only.

An extension has one account.  The functions:

| argument | called as | returns |
|---|---|---|
| `read` (required) | `read(player_id)` | The account as the panel shows it, below |
| `write` | `write(player_id, values)` | Nothing, or the account.  Saves what was chosen.  Never mints a key: claiming is its own act.  Left out, the account is read only |
| `acts` | `{key: fn(player_id)}` | None (the account again), a message string, or `{"message", "url"}` |
| `card` | `card(player_id)` | `{"card": {...}, "filename": "..."}`, or None while the player has none |
| `read_card` | `read_card(card)` | `{"name", "initials", "values"}` |
| `available` | `available(candidate)` | Truthy if the id is free.  Core asks live as one is typed.  Raise to say it could not be reached |

```python
ctx.ui.account(
    read=read_account, write=write_account,
    acts={"send_now": send_now, "claim": claim, "consent": consent, "disconnect": disconnect},
    available=is_free, card=make_card, read_card=read_card,
    cards=("scores_card",), consent=(ctx.t("account.consent.id"),),
    sends_picture=True)
```

**The answer of `read`.**  A dict:

| key | meaning |
|---|---|
| `user_id` | The id chosen, lower case as the service will keep it, or `""` before one is chosen |
| `claimed` | Whether it is registered with the service.  Until then it is free to change or drop with a plain write; after, read only.  Where an extension cannot vouch for a key it holds, answering true is the safe side of that guess |
| `needs_consent` | Whether Share still owes the player a look at what it makes public before it next turns on.  True until a `claim` or a `consent` act settles it, independently of `claimed` |
| `page` | The account's public page once claimed, `""` before |
| `status` | One line in words (*1 game waiting to send*), or `""` while unclaimed |
| `waiting`, `waiting_count` | The same fact as a boolean and a number |
| `acts` | `[{key, label, description}]`.  `send_now` is the one core shows on its own, only while claimed and something waits |
| `card` | Whether a card can be made for this player now |
| `picture` | True if the service holds a picture for this player |
| `fields` | The account as fields, below.  `[]` where the account is a user id |

**Claiming is deliberate, never a side effect.**  The Manager's own dialog checks `available`
live as an id is typed, saves it with a plain write (nothing sent, no key made), and makes the
key and registers the pair only when the player turns Share on.  The acts it uses:

| act key | what it should do |
|---|---|
| `claim` | Check the id is still free, mint the key, register the pair, answer the account.  Raise an ordinary error where the id was taken in the meantime, the player has no initials, or the account already holds a key; the Manager shows what it says and leaves Share off |
| `consent` | Settle `needs_consent` with no send of any kind.  Use it instead of `claim` where `claimed` is already true |
| `disconnect` | Forget the id and key here, `ctx.players.set_account(player_id, {})`.  The account stays with the service, whatever was already shared |
| `send_now` | Send what is waiting.  Answers a message |

**Consent.**  `consent` is what Share tells the player becomes public, asked once, the first
time it is turned on.  Give each line in its own words from `ctx.t`, and core shows them as what
the account shares.  Core adds that the service can neither rename nor delete an account, since
this shape never offers either.

**Share.**  Core's switch, the same on every account.  `ctx.players.sharing(player_id)` reads
it, and `account.share_changed` says when it moves; see
[What a game tells you](offers.md#what-a-game-tells-you).  **Turning Share off drops whatever
the account holds back to send**, games that failed to go or a queue of scores, so nothing
played while it was on is sent later manually or when it is turned on again.  Core's words under
the switch are `account.share.help` in the catalog when the extension has them, else the
Manager's own naming of a played game, a rating and a high score.

**An account that is only fields.**  Where `read` answers `fields` and no `user_id`, core shows
the fields on the player's panel the way a settings page shows them, in the types of the
"Fields" table: `[{key, type, label, help, value, choices, ...}]`, and for a `secret` a `set`
and never a `value`.  Core saves each one as it is left with `write(player_id, {key: value})`,
and a secret cleared with *Clear* arrives as `None`.  Share is core's switch under them, and
`needs_consent` and a `consent` act work as they do for an id.  A key per player looks like
this:

```python
def read_account(player_id):
    held = ctx.players.account(player_id)
    return {"fields": [{"key": "api_key", "type": "secret", "label": ctx.t("account.key"),
                        "set": bool(held.get("api_key"))}]}
```

**A secret in an account** is accepted on a write and never answered.  Core takes the value out
of every answer from an extension's routes, settings included, and puts `set`, true or false,
in its place.  The user id and key pair above does not use `fields`: the key never crosses the
wire in either direction, only `claimed`.

**A picture.**  An account that answers `picture: true` gets a *Use {Service}'s Picture* button
in the player's picture dialog, named for the extension's display name.  Pressing it calls the
extension's own route `GET /api/v1/ext/<name>/accounts/{player_id}/picture`.  You serve that
with `ctx.add_router`, returning image bytes with a content type of `image/png`, `image/jpeg`,
`image/webp`, `image/gif` or `image/bmp`, or a `404` while there is none.  Core keeps the bytes
exactly as it keeps an upload (center square, at most 1024 pixels, WebP, Undo).  It never
follows an address an extension hands it.  A guest has none to set.

An extension that sends a player's picture to its service declares `sends_picture=True` and
reads the file with `ctx.players.picture(player_id)`, which answers only while Share is on.
Core adds its own line to `consent`.  **An extension that starts sending after a player has
already consented owes the dialog again:** its `needs_consent` answers true until a consent
that named the picture.  That is yours to track; core cannot know which line a past consent
showed.

## Cards

A card is a player's account as a file they can carry to another install.  It holds a QR code
of the card's text, made by core as an SVG, and the text is also hidden in the file in a
comment, a `<metadata>` and a `<desc>`, named for the extension: `<!--SCORES_SITE_PAYLOAD:...-->`,
`id="scores_site-payload"` and `id="scores_site-payload-desc"`.  `marker=` names them
otherwise, and has to be a plain name.  Core reads a card from that text and never from the
picture.

The card an extension answers is a JSON object with a `type`, written compact with its keys
sorted.  `cards=` lists the `type`s it reads.  A card arriving at
`POST /api/v1/players/guests/card` goes to the extension that declared its `type`, and that
extension answers who it is for.  The guest joins up alone with the card's initials (one to
three characters, as they are), and their account shares.  The same card again puts that guest
up rather than adding a second.  A card used for a player's existing account,
`POST /api/v1/players/{id}/accounts/{extension}/card`, is read the same way, and core writes the
`values` it answers to that account with `write`.

VPinPlay's card is the one 2.x's *Download QR Code* saved, byte for byte: the same text, the
same QR settings, the same hiding places.  Cards are on people's mobile devices, so a card from either
version joins on the other.

Words: `account.label` (the heading over the account; left out, the extension's name) and
`account.share.help`.
