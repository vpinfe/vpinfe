# HTTP API

VPinFE serves a versioned HTTP API at `/api/v1`, on the same port as the Manager
(`http_port` in the `network` section, default 8001).

> **Unstable.** The API is under active development and may change without notice until v1
> is declared stable. Nothing outside this repo should depend on it yet.

## Structure

- `httpapi/__init__.py`: the app factory (`create_api_app()`) and the mount (`register(app)`).
- `httpapi/errors.py`: the error envelope — `ApiError` and the handlers that shape it.
- `httpapi/instance.py`: discovery and health — what this instance is.
- `httpapi/capabilities.py`: the capability registry discovery reads from.
- `httpapi/events.py`: the event stream — the internal bus (`common/events.py`) on the wire.

The API is not part of the Manager. It's served by the same process today, but it belongs
to the platform — the Manager, the frontend themes, the Remote, and any outside
integration are all just clients of it.

## Why it's a mounted app

`register()` mounts a separate `FastAPI` app at `/api/v1` rather than adding routes to the
NiceGUI app directly. That boundary does real work:

- The error envelope, CORS, and eventually the authorization boundary apply to `/api/v1` and to nothing
  else. The Manager's pages can't be affected by an API-level change, by construction.
- NiceGUI disables OpenAPI on its own app and installs 404/500 handlers that render HTML
  pages. Routes added there get no generated spec and no JSON errors; a mounted app gets
  both.

One consequence worth knowing: a request for exactly `/api/v1` would normally redirect to
`/api/v1/`. `register()` also serves discovery from the parent app at the un-slashed path so
the documented entry point is a plain 200. Both spellings work.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1` | Discovery — what this instance is and what it can do |
| GET | `/api/v1/health` | Liveness |
| GET | `/api/v1/openapi.json` | Generated OpenAPI spec; another device needs to be signed in |
| GET | `/api/v1/docs` | Swagger UI; another device needs to be signed in |
| POST | `/api/v1/auth/code` | Ask for a sign-in code. The device shows "{address} wants to sign in" with Show and Ignore; the answer holds a `request_id` and no digits, and sets a `vpinfe_asking` cookie (HttpOnly, SameSite Strict, ten minutes) that the browser has to send back with the code. 429 `too_many_requests` while another address's ask is too recent, 403 once the device has ignored this address, and 403 `codes_off` while sign-in codes are switched off |
| POST | `/api/v1/auth/session` | `{"request_id", "code"}` signs this browser in, from the browser that asked, and sets the `vpinfe_session` cookie: HttpOnly, SameSite Lax, 90 days unused, Secure under HTTPS. 403 `forbidden` with `details.reason` of `wrong`, `used_up`, `no_code` or `ignored` |
| GET | `/api/v1/auth/me` | Whether this browser is signed in, whether it reached the API from the device itself, and which kind of sign-in it holds |
| POST | `/api/v1/auth/sign_out` | Sign this browser out |
| GET | `/api/v1/auth/sessions` | The browsers signed in: what each is, where from, when last used, and which one is asking (`access:read`) |
| DELETE | `/api/v1/auth/sessions/{session_id}` | Sign one browser out (`access:write`) |
| DELETE | `/api/v1/auth/sessions` | Sign every browser out (`access:write`) |
| POST | `/api/v1/auth/confirm/code` | Ask for a Confirm Code. Six digits appear on this device's screen and in no answer. Only a signed-in Owner can ask; this device and a token hold no Confirm to ask for |
| POST | `/api/v1/auth/confirm` | `{"code"}` gives the Owner a Confirm for 30 minutes, extended each time a Protected route is used. 403 `forbidden` with `details.reason` of `wrong`, `used_up` or `no_code` |
| GET | `/api/v1/auth/confirm` | Whether the caller holds a Confirm, and for how long |
| POST | `/api/v1/auth/pair/code` | Ask this install for a pairing code. The digits appear on this device's screen. The answer holds a `request_id` |
| POST | `/api/v1/auth/pair` | `{"request_id", "code", "name"}` answers with a Full Control token, shown once, in `secret`. The asking install keeps it and sends it as `Authorization: Bearer` on every call to this one |
| POST | `/api/v1/auth/remote_code/renew` | Make a new Remote Code. Every mobile device that came in with the old one is signed out; answers with how many (`access:write`) |
| GET | `/api/v1/auth/tokens` | The tokens made: name, `access`, `can_power`, when made and when last used. Never the secret (`access:read`) |
| POST | `/api/v1/auth/tokens` | `{"name", "access", "can_power"}` makes a token. `access` is `full` or `play`. The secret is in this answer and cannot be read again. `protected:write` |
| PUT | `/api/v1/auth/tokens/{token_id}` | `{"can_power"}` changes the Can Shut Down and Restart switch (`access:write`, and `protected:write` to turn it on). `404` for a token that is gone |
| DELETE | `/api/v1/auth/tokens/{token_id}` | Revoke one (`access:write`) |
| GET | `/api/v1/auth/authenticator` | Whether an authenticator app is set up, and whether this device shows sign-in codes (`access:read`) |
| POST | `/api/v1/auth/authenticator/setup` | A new `secret` and the `uri` a QR carries. Nothing changes until the app's first code is sent (`protected:write`) |
| PUT | `/api/v1/auth/authenticator` | `{"code"}` finishes setting up with the first code from the app, replacing any earlier one (`protected:write`) |
| DELETE | `/api/v1/auth/authenticator` | Remove the authenticator app. Browsers already signed in stay signed in (`protected:write`) |
| PUT | `/api/v1/auth/authenticator/screen_codes` | `{"on"}` shows or stops showing sign-in codes on this device's screen. 403 `forbidden` with `details.reason` of `not_set` while no authenticator is set up (`protected:write`) |
| POST | `/api/v1/auth/authenticator/session` | `{"code"}` signs this browser in from any address, as `/auth/session` does. 403 `forbidden` with `details.reason` of `wrong`, `spent` or `not_set`; 429 `too_many_requests` with `details.wait` seconds after five wrong codes in a row from one address, or twenty from all addresses together |
| POST | `/api/v1/auth/authenticator/confirm` | `{"code"}` gives the signed-in Owner a Confirm, as `/auth/confirm` does. Same refusals as the sign-in, counted against the sign-in rather than its address |
| GET | `/api/v1/auth/encrypted` | The encrypted connections set up: `proxies`, `networks`, the `certfile` and `keyfile` chosen, whether VPinFE is answering `https` now and the `problem` when it is not, and whether this caller came `encrypted` (`access:read`) |
| POST | `/api/v1/auth/encrypted/proxies` | `{"value"}` trusts a reverse proxy by its address. Its `X-Forwarded-For` and `X-Forwarded-Proto` are believed, and a request it says came over HTTPS counts as encrypted. `protected:write` |
| DELETE | `/api/v1/auth/encrypted/proxies?address=` | Stop trusting that proxy. `protected:write` |
| POST | `/api/v1/auth/encrypted/networks` | `{"value"}` adds an address range, such as `10.8.0.0/24`. A connection from inside it counts as encrypted, judged by the address it connects from and never by a header. 400 for a range that is not one or is wider than a /24 (a /64 for IPv6). `protected:write` |
| POST | `/api/v1/auth/encrypted/networks/tailscale` | Add Tailscale's range. `protected:write` |
| DELETE | `/api/v1/auth/encrypted/networks?range=` | Remove that range. `protected:write` |
| PUT | `/api/v1/auth/encrypted/certificate` | `{"certfile", "keyfile"}` chooses the certificate VPinFE serves HTTPS with, at the next start. 400 unless the two files exist and load together. `protected:write` |
| DELETE | `/api/v1/auth/encrypted/certificate` | Serve plain HTTP again at the next start. `protected:write` |
| POST | `/api/v1/auth/asks` | `{"method", "path", "body"}` asks the Owner to approve one change. Only a Full Control token can ask, and nothing under `/auth` can be asked for. Answers with the ask's `id`, `state` and `expires_in`. Once approved, repeat that exact request with the id in an `X-VPinFE-Approval` header: it is spent by one request and lapses after an hour. 400 for a request that cannot be asked for or a body over 4096 characters, 429 `too_many_requests` while five asks are waiting (`config:write`) |
| GET | `/api/v1/auth/asks` | The asks waiting for the Owner, oldest first (`access:read`) |
| GET | `/api/v1/auth/asks/{ask_id}` | Whether this token's ask is `waiting`, `approved`, `declined`, `used` or `lapsed`. `404` for another token's ask (`config:write`) |
| DELETE | `/api/v1/auth/asks/{ask_id}` | Take this token's ask back (`config:write`) |
| POST | `/api/v1/auth/asks/{ask_id}/approve` | The Owner approves it. `404` once it is answered or has lapsed. `protected:write` |
| POST | `/api/v1/auth/asks/{ask_id}/decline` | The Owner declines it, or takes back an approval not yet used (`access:write`) |
| GET | `/api/v1/events` | Subscribe to the event stream (SSE). `?events=` filters by name |
| GET | `/api/v1/play/state` | What this play host is doing, with the running game's `game_id` and when it `started_at`. The snapshot you take once; `play.state_changed` on the stream is how you hear about it after that |
| POST | `/api/v1/play/stop` | Close the table this play host is running. `stopped` is false when there was nothing to close, which is an answer rather than a failure |
| GET | `/api/v1/play/controls` | What can be pressed on the table this play host is running: each control's `id`, `group`, `label`, `hold_ms`, `holdable`, `side` (`left`, `right`, or empty for the middle), `available` and, where it is not, a `reason`. `notes` says each cause once for the controls that share it, `{controls, text}` with the ids and a sentence naming them, so a screen need not repeat a `reason` under every control. `table` is null and `controls` empty when nothing plays, a recording owns the table, or the table's app offers none. The hand controls (flippers, magna saves, launch ball, nudge) are left out, with no note, where the table's settings map them to no single key that can be pressed. `press` is whether this device can send keys at all. The answer holds for the table's life; ask again when `play.state_changed` says another runs. `play:read` |
| POST | `/api/v1/play/controls/{control_id}` | Press one control of the running table, by the `id` the listing gave; no key code is accepted. `pressed` is that the key was sent to the window with focus, not that the table acted on it. The body's `phase` is `tap` by default, the key held for the control's `hold_ms`. On a control listed `holdable` (the flippers, the Magna Saves and Launch Ball), `press` leaves the key down and `release` lets it go; a held press is sent again about three times within its `ttl_ms` (1500 by default, 200 to 10000), and the install lets go on its own when they stop. Where the device cannot leave a key down (`wtype`), a `press` is one tap and a `release` sends nothing. `404` for an id the table's app does not declare, `409` (`conflict`) where nothing plays, a recording owns the table the control has no single key to press, or a `press` names a control that is not `holdable`, `503` where this device cannot send keys. `input:act` |
| GET | `/api/v1/frontend/state` | What the frontend is showing: whether it is up, its collection, the game on the wheel and any recording shown for a decision. `frontend.state_changed` on the stream carries the same after every change |
| GET | `/api/v1/frontend/browser` | What this device's frontend browser can play, and how to fix what it cannot. `state` is `unknown` until the frontend has reported since this browser was chosen |
| GET | `/api/v1/capture` | What this device can record of its own screens, and exactly why not. See [Recording](#recording) |
| POST | `/api/v1/capture/plan` | What recording games or tables would fill and replace, by whose file, and about how long it takes. Nothing happens |
| GET | `/api/v1/capture/proposals` | Recordings waiting for a decision: how many, their size, and each with the file it would replace |
| GET | `/api/v1/capture/proposals/{id}/file` | Play a recording waiting for a decision |
| POST | `/api/v1/capture/proposals/{id}` | `{"use": true}` places the recording, keeps the file it replaces in Backups (reason `replace`) and deletes it; `false` throws it away |
| DELETE | `/api/v1/capture/proposals` | Discard every recording waiting |
| POST | `/api/v1/capture/test` | Record 3 s of the playfield with the Record and Encode Commands, launching nothing, and say what came out: size, rate, frames and a small picture, or which command failed and what it said |
| POST | `/api/v1/capture/screens/choose` | On KDE Plasma and GNOME, ask on the device's own screen which screens VPinFE may record, and keep the answer. 202 with the job; 409 while a table runs or a recording is under way; 501 where the desktop never asks |
| POST | `/api/v1/capture/runs` | Record games or tables, one after another. 202 with the job; 409 while a table runs, a recording or media fill is under way, a run waits to be resumed, or the disk has no room for it; 501 where this device records nothing |
| GET | `/api/v1/capture/runs/current` | The current run, running or paused, or `{"run": null}`. `capture.run_changed` on the stream carries the same |
| POST | `/api/v1/capture/runs/current/pause` | Close the table being recorded, keep nothing of it, and hold the run there. 404 with no run |
| POST | `/api/v1/capture/runs/current/resume` | Carry on with a paused run from the game it was on. 202 with the job |
| POST | `/api/v1/capture/runs/current/stop` | Close the table being recorded, keep nothing of it, and end the run. What earlier games placed or kept stays |
| POST | `/api/v1/capture/runs/current/discard` | Forget a paused run, the same act as Stop on one that is going |
| PUT | `/api/v1/frontend/collection` | Show a collection on the frontend, `""` being the whole library. 202, and the switch arrives as the next `frontend.state_changed`. 409 when the frontend is not running, or when this install reads its library from another that cannot be reached; 404 when there is no collection by that name |
| PUT | `/api/v1/frontend/game` | Move the frontend's wheel to a game. 202; 409 when the frontend is not running, 404 when the collection on screen does not hold that game |
| PUT | `/api/v1/frontend/preview` | Show a recording waiting for a decision where it plays on the cabinet, `{"proposal": id, "showing": "before" or "after"}`. 202; 404 when there is no such proposal, 409 when the frontend is not running or a table is |
| DELETE | `/api/v1/frontend/preview` | Stop showing it, deciding nothing. 202, whether or not one was on show |
| POST | `/api/v1/input/actions` | Press, hold or release an input action on this install — the door a remote drives the frontend through |
| GET | `/api/v1/input/play` | Whether this device can press keys into a running table and hear them from outside the frontend, and why not |
| GET | `/api/v1/dof/event` | Which DOF event is on this device, `event` (null when none), and `blocked`, the reason in words why one could not be turned on now, empty when one could, and `no_boards`, true while an event is on and no board has reported in. Under `config:read` |
| POST | `/api/v1/dof/event` | Turn a DOF event on this device's cabinet until it is stopped, `{"event": "E900"}`, firing it again if it is already on. The answer carries `event` and `no_boards`, true when DOF is running and no board has reported in, so the event reached no hardware. 400 when the event is not a letter and a number, 409 while a table is playing, when there is no DOF config yet or when DOF would not start, 501 when DOF is off, not installed here or installed and would not load. Under `input:act` |
| DELETE | `/api/v1/dof/event` | Turn the event off. 200 whether or not one was on |
| GET | `/api/v1/dof/config` | When the DOF config was last downloaded: `schedule`, `checked` (empty before the first ask), `due` and `has_key`, never the key. Under `config:read` |
| POST | `/api/v1/dof/config` | Download the DOF config from the DOF Config Tool with the stored API key, as a `202` job, into the folder Visual Pinball's DOF plugin reads. Always downloads, whatever version the tool is at; the job's result says whether any file in the folder changed, and its detail reads DOF config updated or Already up to date. `400` without a key, `501` where the downloader or the folder is not available, `409` while one is running. Under `config:write` |
| GET | `/api/v1/update` | Whether a newer build is published, and whether this install can take it. `update_supported` is the second question, and `support_reason` says which case it is. `update_available` is null where this build's own version does not read as one, so nothing can be weighed. Answered from the last check while it is under a day old, or under an hour after a failed one; `refresh=true` asks GitHub now, unless GitHub has said to wait. `checked_at` is when a check last succeeded, null if none has, and `error` is set when the last attempt failed |
| POST | `/api/v1/update` | Stage the published build and go down to take it. 501 when this install cannot replace itself, 409 when a table is running and `stop_table` was not set |
| GET | `/api/v1/collections` | List collections |
| GET | `/api/v1/collections/{name}` | One collection |
| GET | `/api/v1/collections/{name}/games` | Its games, resolved — works for both kinds |
| GET | `/api/v1/collections/{name}/entries` | What it resolves to, one entry per game, in the order a frontend shows them |
| POST | `/api/v1/collections` | Create one. `filters` makes it filter-based, `games` makes it manual. `copy_of` starts it as a copy of that collection - its games, criteria, exclusions, order, limit, description and image, under the new name - and is refused beside `filters`, `games` or `description` |
| DELETE | `/api/v1/collections/{name}` | Delete it |
| PATCH | `/api/v1/collections/{name}` | Change one. Only what you send is written — a rename need not restate the rest |
| PATCH | `/api/v1/collections` | Rearrange them. `{"order": [...]}` names every collection once, in the order the frontend shows them, and the answer is the list in that order. A name missing, repeated or unknown is refused and nothing is written |
| GET | `/api/v1/collections/{name}/members` | Its **stored** membership, and why each entry is there. `ref_table` is the table a row *names* (empty when it names none) — its identity, and not the table it resolves to |
| POST | `/api/v1/collections/{name}/members/preview` | The stored membership as it would be with other criteria in place of the collection's own, storing nothing. `{"filters": {…}}`; leaving `filters` out tries it with none. `matched` is how many games those criteria match in the library |
| PUT | `/api/v1/collections/{name}/games/{id}` | Add a game (idempotent). `{"table": "…"}` names one of its tables; `{"after_table": "…"}` puts the new row beside that sibling instead of at the end |
| DELETE | `/api/v1/collections/{name}/games/{id}` | Remove one row: `?table=abc` the one naming it, `?table=` the one naming none. Omit it entirely to remove every row for the game |
| PUT | `/api/v1/collections/{name}/games/{id}/table` | Change which table it names, in place. `{"table": ""}` hands back the game's default; `was` picks the ref when a game appears twice |
| PUT | `/api/v1/collections/{name}/excluded/{id}` | Keep a game out. `{"table": "…"}` keeps out one table |
| DELETE | `/api/v1/collections/{name}/excluded/{id}` | Lift one exclusion: `?table=abc` the one naming it, `?table=` the one naming none. Omit it entirely to lift every exclusion for the game |
| POST | `/api/v1/collections/{name}/members/from_filters` | Keep what the criteria match, and drop the criteria and the exclusions. Each game is written once, naming no table, so it follows its default; a member that already named a table keeps it |
| PUT | `/api/v1/collections/{name}/order` | Arrange a collection. The whole ordered list, atomically - one entry per row, so a game holding two named tables is named twice |
| PUT/GET/DELETE | `/api/v1/collections/{name}/image` | Its icon |
| GET | `/api/v1/games/{id}/collections` | The collections holding a game, each `added` (written into it) or `matched` (its rule matched the game), and under `taken_out` every other collection an exclusion keeps it out of. A game the limit cuts is held by none |
| GET | `/api/v1/library/game_collections` | The same answer for every game a collection holds, in one read. A game no collection holds is not listed |
| POST | `/api/v1/library/preview` | What a rule would match, storing nothing |
| GET | `/api/v1/jobs` | Slow work, running first. `?kind=` filters; `?limit=` is how many finished jobs, 20 by default and 500 at most |
| GET | `/api/v1/jobs/{id}` | One job — state, last progress, outcome, and `result` where the work produced one. Not on the listing: a result can be a row per game |
| GET | `/api/v1/library/entries` | The play lens over the whole library |
| GET | `/api/v1/library/filters` | Every filter axis, with the values this library holds |
| GET | `/api/v1/library/tags` | Every tag - carried by games or tables, or only written down - with how many carry it, its description and its color. A tag an extension's Community list puts on names that list in `sources`, with when it was last read, whether that read is `stale` and the `error` that made it so; it cannot be renamed, merged or removed |
| PUT | `/api/v1/library/tags/{tag}` | Describe a tag and pick its color, writing it down if nothing has. `color` is one of `red orange amber green teal blue purple pink gray`; empty goes back to the one derived from its name |
| POST | `/api/v1/library/tags/merge` | Fold `{"sources": [...], "into": "..."}` across the library. A rename is one source into a name nothing uses. Answers `changed`; `409` with nothing written when a game carrying a source is in a folder that cannot be written to, `details` naming each |
| DELETE | `/api/v1/library/tags/{tag}` | Take a tag off everything in the library that carries it. Answers `changed`; `409` with nothing written when a carrier is in a folder that cannot be written to |
| POST | `/api/v1/library/owned` | Which of `{"ids": [...]}` this library holds: VPS entry or release ids, or with `"keys": "vpinfe"` its own game or table ids, and `"kind": "game"` or `"table"` to ask for one only. A game comes back with its id, a table with its game's and its own. A release this library holds another version of comes back under `other_versions`, with that table's `version` and the release's `url`. With a `kind` and VPS ids, `known` has VPSdb's `name` for each id the library does not hold, with a game's `manufacturer` and `year` or a table's `version` and `authors` |
| POST | `/api/v1/library/refresh` | Find tables added or removed on disk, and read what nothing has read yet. Never reaches the network. Returns `202` and a job |
| POST | `/api/v1/library/scan` | Rebuild game metadata from VPSdb. Returns `202` and a job; optional `{"download_media": bool, "update_all": bool}` |
| GET | `/api/v1/library/info` | What the library's `.info` files need - see [Schema version](#schema-version) |
| POST | `/api/v1/library/info/upgrade` | Upgrade every `.info` to the current schema. Returns `202` and a job |
| POST | `/api/v1/library/auto_match` | Match `{"game_ids": [...]}` again from their folder names, off the catalog on disk. A match a person made or cleared stays. A game in a folder that cannot be written to is left alone. Answers `games`, `changed`, `unmatched`, `yours` and `skipped`, those left alone for that; `409` while a scan runs |
| PUT | `/api/v1/library/private` | Mark a selection Private, or not, `{"game_ids": [...], "private": bool}`. Answers `games`, how many were named, and `changed`, how many were not already so. An id the library does not hold is a `404`, and nothing is written |
| PUT | `/api/v1/library/marks` | Set marks on a selection, `{"edits": [{"game_id", "table_id"?, ...}]}`. Accepted, not done: a `202` with a `Location` for a job of kind `marks`. An edit with a `table_id` takes `rating`, `tags`, `hidden`, `delete_nvram_on_close` and `launcher` (empty clears it, and needs `config:write` as well); one without takes its game's `rating`, `tags`, `favorite` and `private`. A field left out is not touched; a field a row cannot take is an `invalid_request` and nothing is written. The result is `{rows, written, failed}`, each entry `{game, table}` with a `reason` on a failure |
| POST | `/api/v1/library/media/missing` | What downloading missing media for `{"game_ids": [...]}` would download, downloading nothing; leaving `game_ids` out asks about the whole library. Per kind the library keeps and an enabled source publishes, and per kind a game's own files can supply (`backglass`, from its backglass file, with no source on and no match): `missing` games with no file for it, `available` those of them a source or the game's own file has one for, and `local` how many of those are the game's own file. `unmatched` counts the games with no VPS match. `sources` names the enabled sources, and `unreachable` those of them that could not be reached, which count nothing available |
| GET | `/api/v1/library/media/offered` | The games whose slot for a media kind is empty and an enabled source has a file for it, `{"kinds": {kind: [game id]}}`. A kind nothing can be fetched for is left out, and so is every kind when no source can be reached. The same answer Discovery's `missing` shelf is drawn from and Needs You leaves out of its counts. Makes no request: until the sources' index has been loaded by a fill or an update, it is empty |
| POST | `/api/v1/library/media/fill` | Download missing media. Returns `202` and a job. `{"game_ids": [...], "kinds": [...]}`, either left out meaning all of them, or `{"slots": [{"game_id", "kind"}]}` for exact slots. Fills gaps only, never replaces a file, and never fetches a kind the library does not keep. A backglass picture is drawn from the game's backglass file before anything is downloaded, so it fills with no source on and for a game with no VPS match. The job's result counts `games`, `filled`, `unmatched` and `failed`; `409` while another fill or the downloaded-media update runs |
| POST | `/api/v1/library/media/rotate` | Turn pictures and videos where they sit. Returns `202` and a job. `{"files": [{"game_id", "path"}], "turn": "left" \| "right" \| "around", "keep": true}`, `path` being the file's place in the game's folder: directly in it or anywhere under `medias/`, never through a link, and a file named twice is turned once. Each file is turned whole or left as it was; `keep` (default on) takes a copy first, listed in `/backups` as `media`, and is refused with `409` when there is no backups folder to write to. A file that cannot be turned is skipped with its reason and left as it was: animated pictures, files that are not pictures or videos, files that are missing, outside the game's media or changed while being turned, and videos when FFmpeg is missing or fails. A picture's orientation tag is applied before it is turned. Pictures and videos are encoded again. The job's result counts `turned`, `games` and `kept`, and lists `skipped` as `{game_id, path, reason}`; `409` while another turn runs |
| GET | `/api/v1/library/patches` | Which script fixes the daily check found published for this library's tables, with `checked_at` for when it read the index. Answers from the cache and asks nobody, so before the first check, or with the source switched off, it is `reachable: false` with nothing offered. Changes nothing |
| POST | `/api/v1/library/patches` | Download them. Returns `202` and a job. `{"games": [...]}` names the folders a check offered and limits the download to those; leaving it out looks at every game. A table with a `.vbs` beside it already is left alone |
| GET | `/api/v1/library/vps_state` | What the catalog lists across the library, as last compared. `computed` empty means it has never been compared |
| POST | `/api/v1/library/vps_state` | Compare the library with VPS now. Returns `202` and a job, which can run beside a scan. The sources watch starts the same job at start-up and whenever the spreadsheet changes, so a client need not |
| GET | `/api/v1/library/discovery` | Every catalog entry against what the library holds, as last compared: `listed` and `held` kinds, `in_library` and the entry's `img_url`. `counted` empty means never compared, and `in_library` is then null. Each row's `releases` lists every release under the catalog's keys, each with its `by` (positions in the response's `people`, in the order VPS credits them; a person has `aliases` and a count of `releases`), `mod`, `features`, `format`, `version`, `added` and `held`; `held` stays false until the library has been compared |
| GET | `/api/v1/discovery/home?days=` | Discovery's front page in one read, from the last comparison. `days` (1 to 366, default 7) is how far back `new` looks. `new` is always keyed by every chip - `all`, `yours` (games in the library) and the five kinds `tableFiles`, `b2sFiles`, `wheelArtFiles`, `topperFiles` and `pupPackFiles`, with a `count` of 0 for one that has nothing in the window - each `{count, releases}`, the newest twelve with `id`, `vps_id`, `game`, `img_url`, `listing`, `added`, `version`, `was` (the version a release held before VPS last changed it, empty if it never has), `authors`, `mod`, `held` and `in_library`. `authors` are the people whose work the library holds most of, with `held` and `new`. `missing` is keyed by the kind a game of the library lacks (`b2sFiles`, `romFiles`, `povFiles`, `wheelArtFiles`, `topperFiles`, those the library keeps), each `{count, on_vps, game_ids, games}`: `count` the games lacking it (a game once, however many of its sources name it), `on_vps` how many of those VPS lists, `game_ids` all of them by the library's id, and `games` the ones to draw, each with `vps` (releases VPS lists) and `source` (files an enabled source offers), the games with most first. Each of those in a media tab also carries `picture`, the address of the file a source offers for the slot (empty for a video or a kind with no picture), and `take`, `{kind, source, size}` naming the file that fills the slot as it is, or `null` where it does not (a playfield wanted under the full-screen variant is filled from another kind); `take` is what `POST /api/v1/games/{id}/media/{kind}/fetch` is sent. A game a source has a file for is in `games` whether or not the last comparison counted it as held, with its `game_id`; of the games only VPS lists, the first eight are. The media kind of the same thing (`backglass` for `b2sFiles`, `wheel` for `wheelArtFiles`, `topper` for `topperFiles`) is that tab, a game once; a media kind with no VPS kind (`playfield`, `playfield_fss` and the others) is keyed by its own name after them. That part is read from the library as it is now and from the sources' index as the process already holds it. The read makes no request: until a fill or an update has loaded the index no media kind appears and every gap stays in Needs You. `counted` empty means the library has never been compared, and nothing then reads as held or lacking Whatever is marked Ignored is on no shelf of it: a game takes its releases and its gaps with it, a release only itself, a `gap` only that tab for that game. `wanted` is `{count, items}`, the newest twelve things marked Wanted, each `{scope, id, vps_id, game, img_url, in_library, listing, version, authors, held, since}` (`scope` is `game` or `release`; a game has no `listing`, `version` or `authors`). `dont_have` is `{count, games}`, the games VPS lists that the library has none of, the ones the most people made a table for first, then by name, games of one name and count in the catalog's order (the order All Games keeps them in): `count` is every game the shelf covers and `games` the first twelve, as `{vps_id, game, img_url, manufacturer, year, authors}`; only a game with a table in a format an enabled launcher plays counts (every format, where no launcher is enabled or one declares no formats), and `authors` is the number of people named first on one of those tables. It is empty until the library has been compared; an ignored game is not on it, and a Wanted game stays on it. `family` is `{count, games}` in the same order, the part of that shelf which shares a title with a game the library holds (accents, case, punctuation and a parenthesised part are ignored): each game is `{vps_id, game, img_url, manufacturer, year, authors, held}`, `held` being the library's entries of that title as `{vps_id, game, manufacturer, year}`, the oldest first. `health` is the games of the library that need a look: `duplicates` is `{count, games}`, the entries two or more of its games are matched to, the ones with the most games first, then by name, entries that tie on both in the catalog's order and ones the catalog does not list after the rest (`count` is entries, not games), each `{vps_id, game, img_url, manufacturer, year, game_ids}` (`game_ids` those games in the library's order, `vps_id` empty for an entry the catalog does not list); `no_tables` is `{count, games}`, the games with no table at all by name, each `{game_id, vps_id, game, img_url, manufacturer, year}`, with `vps_id`, the picture, `manufacturer` and `year` empty for a game matched to no entry or one the catalog does not list. `games` is the first twelve of each. Neither needs the library to have been compared. A game matched to an ignored entry is on neither; that rule is the shelf's alone, and the Games views a See All opens list every game on the entry, ignored or not. `opened` is `{count, games}`, every game VPS lists that a release page was opened for in the last 24 hours (the opened-pages record keeps the 50 latest) and the library still has none of, the one opened last first, each `{vps_id, game, img_url, manufacturer, year, at}` with `at` the time its page was last opened; a game opened for several releases is one, a Wanted game stays on it, and a game that has since been imported, ignored or dropped from VPS is not. It is empty until the library has been compared. `unmatched` is `{count, games}`, the library's games with no VPS match that have a likely entry, by name: each game is `{game_id, game, manufacturer, year, suggestion}`, `suggestion` being `{vps_id, game, img_url, manufacturer, year}` of the one entry of the same title (accents, case, punctuation and a parenthesised part ignored) whose year is the game's or a year either side, the nearest year first, then the same maker, then the lowest entry id, so several versions of a game in one year by one maker give the same one every time. An entry the marks ignore is never offered, and one another game already holds still is. A game with no year gets none, and one declared to be in no catalog is not on it. It reads the catalog and the library as they are, so it is there before the library is first compared. It is the part of the Games grid's Unmatched view that has a likely entry, and `count` is that part, not the view's, so the page's header shows no count beside See All. `updates` is the first twelve of `GET /discovery/updates` in the same shape, with the count and ids of all of them |
| GET | `/api/v1/discovery/missing` | What `GET /discovery/home` answers under `missing`, without the games to draw: `{counted, missing, offered}` with `missing` keyed as there, each `{count, on_vps, game_ids}`, and `offered` the ids of the games a source can fill, by kind, as `kinds` of `/library/media/offered` answers. For a page that counts what the library lacks and draws none of Home. `games:read` |
| GET | `/api/v1/discovery/pictures?game_id=` | The pictures VPinMediaDB has replaced since VPinFE placed them, from the last daily check, which only looks while `update_downloaded_media` is off: `{checked_at, pictures}`, `checked_at` empty before the first check. Each picture is `{id, game_id, vps_id, name, kind, source, size, md5}`, `id` being the entry's id and the media kind as the marks keep it, `md5` the hash of the new file. `game_id` narrows it to one game. A picture is left out once the file on disk is no longer the one VPinFE placed, and once Keep Mine (a `version` mark with `md5` as its `detail`), Ignore Updates (an `updates` mark) or an ignored game sets it aside. Empty while VPinMediaDB is off and while `update_downloaded_media` is on, since the next check replaces them. No request is made. `games:read` |
| GET | `/api/v1/discovery/updates?game_id=` | The games of the library with a later version of something they hold, `{count, game_ids, games}`, by name and a game once however much of it moved. `game_id` narrows it to that one game, and only that game is looked at. A game's `moves` each name `source`, `kind`, `release_id`, `vps_id`, `file`, `then` and `now`: for `vps`, a file matched to a VPS release (a table in the game's `.info`, or a file in its assets ledger that is still in the folder; two files matched to the same VPS release at the same version are one move) whose version VPS lists at `now` where it listed `then` when the match was made; for another source, what it says is later (`kind` the kind of file, `file` the folder of the game, `now` its version where it names one, `release_id` and `then` empty, `vps_id` the game's). A picture VPinMediaDB has replaced is such a move: `kind` the media kind and `md5` the hash of the new file, the one `GET /discovery/pictures` answers, so a game's replaced pictures are moves of one tile. A match that recorded no version has no update. A table's build its app cannot play is none. Whatever is marked Ignored is left off: a game, a VPS release, `updates` on a VPS release (every later version) or `version` (that one); for a picture, `updates` on `<vps id>:<kind>` or `version` there with the `md5` as its `detail`. Read from the library as it is now and the index as the process already holds it; nothing is asked of any site. `games:read` |
| GET | `/api/v1/discovery/marks` | Every mark on this install, newest first: `{"marks": [{state, scope, id, detail, since}]}`. `state` is `wanted` or `ignored`; `scope` is `game` or `release` (the entry's or the release's VPS id), `gap` (the entry's id, `detail` the key of the Home missing tab it is left off: `b2sFiles`, `wheelArtFiles` and `topperFiles` for the tabs VPS and a source share, `romFiles` and `povFiles`, or the media kind for a tab only a source fills), `updates` (a release; every later version of it leaves Updates) or `version` (a release, `detail` the version skipped; only that one leaves it); an `updates` or `version` mark on a picture has the entry's id and the media kind as its `id` (`E1:wheel`), and it is read for the pictures `GET /api/v1/discovery/pictures` answers; they are stored as sent. A thing is unmarked, wanted or ignored, never two. `games:read` |
| PUT | `/api/v1/discovery/marks` | Put a state on a thing, `{"state", "scope", "id", "detail"?}`. Answers the marks and `replaced`, the state the thing held before (empty if none), which is what an undo puts back. Marking a thing with the state it has changes nothing. `id` and `detail` are stored without surrounding blanks. `400` for an unknown state or scope, a blank `id`, `wanted` on anything but a game or release, and a `detail` on a scope that takes none or none (or a blank one) on a scope that needs it; `422` for a missing field. `games:write` |
| DELETE | `/api/v1/discovery/marks?scope=&id=&detail=` | Take the mark off a thing. Answers the marks and `replaced`, the state it held, empty if it held none; unmarking an unmarked thing is not an error. The thing is named as in `PUT`: `400` for an unknown scope, a blank `id` and a `detail` that does not suit the scope; `422` when `scope` or `id` is missing. `games:write` |
| GET | `/api/v1/library/dismissed` | The Overview rows set aside on this install, each with the ids it counted |
| PUT | `/api/v1/library/dismissed/{row}` | Replace what a row was dismissed over, `{"ids": [...]}`; none brings it back |
| GET | `/api/v1/devices` | The devices this install knows about |
| PUT | `/api/v1/devices` | Record an install (idempotent), for one mDNS cannot reach. `port` is declared by the caller - the address is read off the socket, which never says what that device listens on. A `vpx_mobile` body is refused with `400`: a VPX Mobile device is listed at `/config/mobile_devices` |
| GET | `/api/v1/devices/discovered` | Installs announcing themselves on this network right now. Announcements, not records: nothing here has been decided about |
| GET | `/api/v1/devices/{id}` | One device |
| POST | `/api/v1/devices/probe` | Ask every device whether it is there, and record the ones that answer, with what a frontend device's browser plays as `browser` (see Devices). `unaskable` means there was nothing to dial, which is not the same as down. One that did not answer carries `reason`, in this install's language, and `reason_key`, the catalog key it was read from |
| POST | `/api/v1/devices/{id}/probe` | Ask one device the same, answering with that one probe. For showing each device as it answers rather than all of them once the slowest has |
| DELETE | `/api/v1/devices/{id}` | Forget an install. A VPX Mobile device is refused with `400`; it is removed at `/config/mobile_devices/{id}` |
| GET | `/api/v1/devices/{id}/games` | What a VPX Mobile device is carrying, asked of the device every time: each `{"folder", "game_id"}`, the id null where the folder is none of this library's games |
| POST | `/api/v1/devices/{id}/games` | Send games to it, `{"games": [...], "everything": false, "settings": false}`: each game's default table and what it needs, or with `everything` its whole folder. The table's settings file stays home unless `settings` is true; a mobile settings copy (`<table>.<word>.ini`, where the Sending settings turn it on) goes in its place either way. Asks the device first and answers `409` with its reason when it does not answer. Returns `202` and a job |
| POST | `/api/v1/devices/{id}/games/plan` | The same body, and what it would do that is worth saying first → `{"mobile_settings": [...]}`, the ids of the games that would send a mobile settings file. Changes nothing |
| DELETE | `/api/v1/devices/{id}/games/{name}` | Remove a game from it. `204` |
| GET | `/api/v1/actions` | What this install can be asked to do to itself. Only the pairs it performs. The window ones (`frontend` start, stop, restart) are listed whether or not the run started with `--headless`, and are unavailable, with a `reason`, on an install without the `frontend` feature. One that can run but not now stays, with `available` false and a `reason`, in this install's language, and `reason_key`, the catalog key it was read from |
| POST | `/api/v1/actions` | Do one. `{"scope","action","reason"}`. One that takes this process or this device down answers before it goes, so `performed` means the work was handed over. `frontend` start needs `frontend:open`, which an Owner holds without a Confirm and a token or a Remote pass never does; the rest need `system:admin` |
| GET | `/api/v1/logs` | Recent records from this install's own log, oldest last (`limit`, `level`, `contains`, `source`). A record carries its continuation lines, so a traceback arrives whole. `source` names the current log, a rotation beside it, or Visual Pinball's (`visual_pinball` says what it is called when a launcher names where it writes); its records read the same way, with its `WARN` as `WARNING` and the function that wrote the line as `logger` |
| GET | `/api/v1/logs/file` | One of those files whole, for attaching to a report (`source`, empty for the current log). A name that is not one of them is a 404, and a file over 32 MB is a 413 |
| GET | `/api/v1/manufacturers` | Every manufacturer VPSdb or the library knows: computed slug, effective alias, resolved logo (or `null`), library game count. The reference for logo packs and alias maps |
| GET | `/api/v1/games` | List games (`q`, `limit`, `offset`). `hidden` is true on a game whose tables are hidden with none left to offer, which no frontend lists. `private` is true on a game marked Private |
| POST | `/api/v1/games` | Create one. A folder with a record in it, in the location new games go to; `location` overrides that for this one. The only way to bring an entry into being without a file arriving |
| POST | `/api/v1/games/from_vps` | Create one from a catalog entry, `{"vps_id": "…"}` with an optional `location`. A folder named as an import names it, `Name (Maker Year)`, matched to the entry and holding no table. Nothing is downloaded; media is fetched only if the automatic fill is on. `404` for an entry the catalog does not hold; `409` when the folder is already there |
| POST | `/api/v1/games/{id}/tables/import` | Copy a game file on this device into the game. A copy, not a move, and refused unless the file is under a browsable root. The game's tables are then settled for the draw order their backglass files call for; a table that cannot be set is named in the log and set again the next time its game is settled |
| POST | `/api/v1/games/{id}/tables` | Add a table the game holds with no file, `{"app", "key"}` - a ROM, a Pinball FX table, anything its program finds by name - or with `{"path"}`, a file the game points at without holding. `201`. The game's tables are then settled for the draw order their backglass files call for; a table that cannot be set is named in the log and set again the next time its game is settled |
| POST | `/api/v1/games/{id}/tables/{table_id}/contain` | Copy a table the game points at into its folder, and stop pointing. The table keeps its id. The game's tables are then settled for the draw order their backglass files call for, since the table's settings and backglass are now the ones beside the copy |
| DELETE | `/api/v1/games/{id}/tables/{table_id}` | Forget a table whose file is gone. Nothing on disk is deleted, and a table whose file is still there is refused. The draw order recorded for the table is dropped with it |
| DELETE | `/api/v1/games/{id}/folder` | Delete the game's whole folder from the disk: tables, media, .info and everything else in it, with no trash and no undo. The draw order recorded for its tables is dropped. Protected. 409 while that game is running, and 400 for a folder that is, or holds, a location the library reads |
| GET | `/api/v1/games/{id}/tables/{table_id}/swap` | What replacing or deleting a table moves: the table named by version and author (`label`), its `filename`, each file named for it (`files`: `kind`, `file`, `carry`, `brought`) with whether it goes with the new file by default, and `patched`, the game's tables made from it. A table kept outside the game's folder is refused |
| DELETE | `/api/v1/games/{id}/tables/{table_id}/file` | Delete one table from disk: its file and every file named for it, each after a copy goes to Backups; a file named for the folder stays. The entry goes with it, a copy of the entry going to Backups beside the file's, and restoring the file from there brings the entry back with its id. The entry's copy is not listed as a copy of its own, and deleting the file's copy deletes it. The game plays its next table, and a collection that named it plays the game's default. Refused while the game is running. Answers with `kept`, the copies |
| POST | `/api/v1/games/{id}/tables/{table_id}/swap_back` | Put the file a replacement took out back in the table's place, `{"copies"}` being the `kept` of that replacement and `source` the match that replacement answered with, which the table gets back. The entry is the same one throughout |
| PUT | `/api/v1/games/{id}/tables/{table_id}/source` | Say which VPS release a table is, `{"vps_file_id"}`. Empty takes the claim back, and records that a person did, so VPinFE does not match the table again. A VPS release filed under a format the table's app does not play (its `vps_formats`) is refused with 400 |
| PUT | `/api/v1/games/{id}/details` | Say what the game is, where no catalog matched it. A patch - a field left out is left alone, a field sent empty is cleared. Not where a VPS id goes; that is the `alt_vps_id` override |
| PUT | `/api/v1/games/{id}/guides` | The game's guides, in order. An entry naming a stored guide by its address keeps it and sets `hidden`; any other is a new guide of the person's own. Leaving out a guide VPS lists is refused - hide it instead |
| PUT | `/api/v1/games/{id}/overrides` | The game's overrides. A patch - a field left out is left alone, a field sent empty clears it |
| PUT | `/api/v1/games/{id}/tables/{table_id}/overrides` | One table's, the same way |
| DELETE | `/api/v1/games/{id}/vps_match` | Say the game is in no catalog, so the scan's match is not used. Clearing `alt_vps_id` instead puts the scan's match back |
| GET | `/api/v1/games/{id}/vps_details` | Where the game's details and its VPS entry disagree |
| PUT | `/api/v1/games/{id}/vps_details` | Take the entry's details: `{"fields": [...]}` for some, no body for all. Answers with what still disagrees |
| GET | `/api/v1/games/{id}/offers?kind=&vps_id=&name=` | What every enabled source has for the game, asked together: `{"game_id", "vps_id", "sources"}`, one entry per source asked, in the order `/media-sources` lists them. Each has `id`, `name`, `extension`, `key` and `kinds`, one answer per kind under the name that source knows it by: `offers` for a source core fetches from (`source`, `name`, `url`, `kind`, `size`, `md5`), or `rows` for one that hands the file over itself, with the `note` and `link` it draws under them, and `error` in words where it could not answer, the rest then being empty - one source down costs its own kind and nothing else. A source that has not answered 10 seconds after the call is reported the same way, with the others' answers still returned. `kind` is a slot's name and repeats; none asks every kind each source serves, and a source that serves none of those asked is left out, so a kind no source serves answers 200 with `sources` empty. `vps_id` (with `name`) asks about another entry than the game's own. A game with no VPS entry, and none named, is asked of no built-in source; an extension's source is still asked. A Private game is asked of no extension's source, and a source of an extension the caller may not use is left out. Extensions answer at the Library tier at most. A game the library does not hold answers 404. Needs `vps:read` and `games:read` |
| GET | `/api/v1/games/{id}/vps_state` | What the catalog lists for the game, kind by kind, with the files held under each kind and the tables on disk, each naming the record it is matched to |
| PUT | `/api/v1/games/{id}/asset_source` | Say which VPS record one file in the game's folder is, `{"path", "vps_file_id"}`. The answer is the file's `source`, with `vps_version` set to the version VPS lists for that record now |
| GET | `/api/v1/games/{id}` | One game. `vps_matched_by` says who made the match in `vps_id`: `import` for an entry picked on import, `user` for one a person picked or a no-match they declared, and empty for VPinFE's guess from the folder name or no match. `discovered.vps_matched_by` says the same of the entry under an override |
| GET | `/api/v1/games/{id}/tables` | The game's tables, with resolved assets and dependencies. `update_available` is true where VPS now lists a later version of the VPS release a table is matched to than it listed when the match was made (`source.vps_version`), false where it does not, and null where nothing can be weighed - no match, no version recorded with it, or a version on either side that does not read as one. The file's own `version` never decides it. `launcher_settings_keys` lists the settings the program's file for the table changes, by the program's own key, with a saved camera as the one entry `point_of_view`. `launcher_settings_here` and `launcher_settings_from_folder` count that list, under whichever of the two files it is, and `launcher_point_of_view` says whether that file holds a camera |
| GET | `/api/v1/tables` | Every table in the library (`game`, `limit`, `offset`, `media`), each row carrying `source`, the named release, `game_vps_id`, the VPS entry its game is matched to (empty for a game that is not, or whose entry the catalog no longer holds), `update_available`, `launcher_settings_here`, `launcher_settings_from_folder`, `launcher_settings_keys`, `launcher_point_of_view` and `launcher_app_configurable` as a game's tables do, `delete_nvram_on_close` as the table's overrides read it, `derived_tags`, and `game_rating`, its game's rating beside its own `rating`. The launcher counts read each table's settings file. `?media=` - `wheel`, `backglass`, `playfield` or `logo`, anything else an `invalid_request` - fills each row's `media_kind` and `media_version` with the file the frontend shows for that table: its own file, the game's, the active wheel set, the fixed name, then the kind's fallback. `media_kind` is the kind the table's media route serves it under, `playfield_fss` where a playfield falls back to the FSS render, so `.../tables/{id}/media/{media_kind}?v={media_version}` is kept for good. Both are null where nothing resolves, and on every row without `media`. A keyed table has no file to name media after, so its media is the game's and `/games/{id}/media/{media_kind}` serves it. A game whose tables have no id yet has no row |
| GET | `/api/v1/tables/apps` | The programs that can launch something in this library, and which files each one claims |
| GET | `/api/v1/media` | Every media file in the library (`game`, `kind`, `limit`, `offset`). A game has a shared row per kind, and a table a row of its own only where a file is named for it. A flyer or an instruction card with several pages has a row for each page after the first, its id ending `:n`, with `page` and `named` (*Flyer, page 2*) beside the kind's `label`; every other row is `page: 1` |
| GET | `/api/v1/media/shared` | `/media` for each game's shared rows alone, with `game_id`, `kind`, `page`, `present`, `file`, `path`, `via`, `origin`, `matched_to` and `version` and nothing else, unsorted. A file named for one table, and `orphan` and `unused` files, are not in it. For a client that counts coverage |
| GET | `/api/v1/media/wheel-sets` | The wheel sets any game has media in, as folder names under `medias/wheels/`, with the reserved `logo`, which needs no folder. What `[Media] wheelset` can name |
| GET | `/api/v1/assets` | Every asset file in the library, the same way. A file named for no table gets a row too, and says so |
| GET | `/api/v1/games/{id}/links` | Where the game is elsewhere, as extensions have contributed. `?table=` for one table, `?path=` for one of its files |
| GET | `/api/v1/games/{id}/media` | Every media kind, present or not. `?codecs=true` reads each video's header for its `video_codec`, which is null otherwise |
| GET | `/api/v1/games/{id}/media/{kind}` | Stream one media file. `?size=` sends a picture smaller; `?v=` lets it be kept. `?page=n` streams page *n* of a flyer or an instruction card; page 1 is the kind itself and a page past the last is a 404 |
| GET | `/api/v1/games/{id}/media/{kind}/detail` | What that file is. `format` is what the file's header says it is, `width`/`height` are an image's or a video's, `duration_s` is a video's or a sound's running time, and `video_codec` is a video's codec as `GET /frontend/browser` names the format - `h264`, `hevc`, `vp9` or `av1`, null for any other or where the header does not say. A `backglass` slot's detail also carries `backglass_file`, the `.directb2s` the table reads when it has a readable one: its `path` and `file`, whether the renderer in effect cuts the `grill`, and the `version` its picture is addressed by. Null otherwise |
| GET | `/api/v1/games/{id}/media/overrides` | The kinds where a table has media of its own |
| GET | `/api/v1/games/{id}/media/{kind}/placements` | Where a file of that kind could go, and what each place would replace |
| GET | `/api/v1/games/{id}/media/{kind}/displaced?filename=` | What placing that file under the folder's name would replace, asked before the bytes are sent |
| PUT | `/api/v1/games/{id}/media/{kind}` | Place a file every table shares, named for the folder (multipart: `file`). `?page=n` replaces page *n* of a flyer or an instruction card, or adds it when *n* is one past the last; it is named at the tier of page 1. A gap, or a page on a kind that does not page, is refused. A file it replaces is copied to Backups first (reason `replace`), and the write is refused with `409` when the backups folder cannot be written to |
| DELETE | `/api/v1/games/{id}/media/{kind}` | Remove the file every table shares. A table's own file stays, and so does the default while the game has a file of its own; with none, the default-named file (`wheel.png`) is the one that goes. With `?page=n`, that page alone goes, if it is at this tier, and the pages after it move down a number; with none, the kind goes with all its pages |
| POST | `/api/v1/games/{id}/media/backglass/from-file` | Draw the game's backglass file as its backglass picture, `{"table"}`: lit at start and cut as the renderer in effect cuts it. It is recorded as drawn from the file, and drawn again when the file or the grill setting changes, for as long as nobody has replaced it |
| POST | `/api/v1/games/{id}/media/{kind}/import` | Place a file from this device, `{"path", "table"}` with an optional `"page"`, under a browsable root. A file it replaces is kept as `PUT` keeps it |
| POST | `/api/v1/games/{id}/media/{kind}/fetch` | Place a file from an online catalog, `{"source", "vps_id"}` with optional `size` and `table` |
| GET | `/api/v1/games/{id}/moves/{family}/{kind}` | What a move of the game's file to its tables starts from: the game's file, each table's own file, whether the file can go to the game and whether a table of the game is playing. `family` is `media` or `asset`, which keeps the two kinds named `backglass` apart: the picture, and the `.directb2s` file |
| POST | `/api/v1/games/{id}/moves/{family}/{kind}/to-tables` | Give the game's file to the tables in `{"tables"}`. The file stays on the game when every table without its own is chosen, replacing the own file of any chosen table that has one. Otherwise it is renamed for the one table chosen, or copied for each when several are, over their own files. A replaced file is kept in Backups. For a backglass, the game's tables are then settled for the draw order their files call for; a table that cannot be set is named in the log and set again the next time its game is settled. A table named like the folder cannot be chosen, and a result that leaves a file in use now with no table is refused. Answers what moved, what was replaced, which tables lost the file, which use it now, and an `undo` token |
| POST | `/api/v1/games/{id}/moves/{family}/{kind}/to-game` | Make a table's own file, `{"table"}`, the game's. The game's file it replaces is kept in Backups. A script or a POV cannot go to the game. Answers as `to-tables` does, and settles a backglass the same way |
| POST | `/api/v1/games/{id}/moves/undo` | Put a move back, `{"undo"}`, once and only until the server restarts. Both files are as they were, and so is the ledger of where each came from; a backglass move settles the game's tables again |
| POST | `/api/v1/games/{id}/media/{kind}/move` | Swap two pages of a flyer or instruction card. `{"table", "page", "to"}`: page `page` takes number `to` and the page there takes `page`; `table` is empty for the game's shared slot |
| GET | `/api/v1/games/{id}/tables/{table_id}/media` | One table's media. `?codecs=true` as for the game's |
| GET | `/api/v1/games/{id}/tables/{table_id}/media/{kind}` | Stream one table's media file. `?size=` and `?v=` as for the game's |
| GET | `/api/v1/games/{id}/tables/{table_id}/media/{kind}/detail` | What that file is, as for the game's |
| GET | `/api/v1/games/{id}/tables/{table_id}/media/{kind}/displaced?filename=` | What placing that file for one table would replace |
| PUT | `/api/v1/games/{id}/tables/{table_id}/media/{kind}` | Place a file for one table, named for its `.vpx` (multipart: `file`) |
| DELETE | `/api/v1/games/{id}/tables/{table_id}/media/{kind}` | Remove one table's file. A table named as its folder is the game's, and takes what the game's `DELETE` takes |
| GET | `/api/v1/games/{id}/assets/detail?path=` | One asset file or folder: size, date and format, a folder's file count, and a text file's first lines (`lines=`, 0 for all). A `.directb2s` file also carries `backglass`, what the file says about itself, among it `displays`: the DMDs the file places, each `{target, x, y, w, h}` with `target` `Backglass` or `ScoreView` and the numbers in pixels of the art from its top left (a DMD the file gives a `Source` is left out). A path out of the game's folder is refused |
| GET | `/api/v1/games/{id}/assets/backglass/picture?path=&look=&version=&grill=` | The picture a `.directb2s` draws: `look` is `on` (default, the lamps lit at start), `off` or `every`, `version` is `authentic` or `fantasy` for a two-version file, and `grill=true` cuts it as the cabinet's renderer does. `path` names a file in the game's folder; without it, the file the folder reads, or the default table's where the folder has none of its own. A file with no picture to draw is not found. `size=` and `v=` as for media |
| GET | `/api/v1/games/{id}/tables/{table_id}/assets/backglass/picture?look=&version=&grill=` | The same, for the file that table reads |
| GET | `/api/v1/games/{id}/assets/backglass/scoreview?path=` | The art a `.directb2s` draws on the score view window, with `path`, `size=` and `v=` as for the picture. A file with none is not found |
| GET | `/api/v1/games/{id}/tables/{table_id}/assets/backglass/scoreview` | The same, for the file that table reads |
| PUT | `/api/v1/games/{id}/assets/backglass/displays?path=` | Set where a `.directb2s` file places the DMD: `{"target", "x", "y", "w", "h"}` with `target` `Backglass` or `ScoreView`, in pixels of the art from its top left (`x` and `y` from 0 to 100000, `w` and `h` from 1 to 100000, negative values refused), adds the file's `<DMD>` for that window or moves the one it has; leaving all four numbers out takes it out of the file, which turns the DMD off, and takes the `<Displays>` element with it when that leaves it empty and without attributes. Only the file's `<Displays>` element changes, every other byte stays, and a DMD the file gives a `Source`, or a `Target` other than the two windows, is not touched. A key the body does not name is refused, so a misspelled number cannot read as a request to take the DMD out. Changes to one file run one at a time. It is written beside the file and renamed over it. Answers `displays` as the file now holds them, its new `version`, `undo`, the token that takes the change back (empty where nothing changed), and `undo_refused`, `too_large` where a change was written but is too big to keep for Undo. Each table of the game whose launcher's program lets it draw its own order is then brought to what the file places: where Nixie draws over B2S for the launcher, a table whose file places a Score View DMD ranks B2S over Nixie in its own settings, so the file's art shows with the live DMD on it, and the order is taken out again when the file places none. Only a pair this install wrote is changed or taken out, from a record kept in the state folder; a rank the table holds that was not written is left alone. Answers `order_failed` too, the tables that could not be written as `[{table, name, reason}]`, empty when none; the change to the file stands either way. 404 for a file or game that is not there; 409 while a table of the game is playing or where the file is read-only; 400 for part of a box, a window that does not exist, or a file that is not a backglass or cannot be spliced into; 422 for a number outside its range or a key the body does not name |
| POST | `/api/v1/games/{id}/assets/backglass/displays/undo` | Take back the change with `{"undo"}`, byte for byte: the bytes it put in are taken out and the bytes it took out are put back. It is done once, and only while the file is as that change left it. 409 where it is not, with `details.reason` `changed` for a file edited since or `superseded` for a change followed by a newer one to the same file, which has to be undone first; and 409 while a table of the game is playing or where the file is read-only. 404 once used, for another game's token, or after a restart. Tokens are kept for the newest 32 changes and about 8 MB in all. Answers as the PUT does |
| GET | `/api/v1/games/{id}/assets/text?path=` | A text file of the game to edit (`.vbs .ini .pov .scv .nix .dif .txt .md .nfo .cfg`): `text` with "\n" line ends, the `version` (SHA-256 of the bytes) a save sends back, its `encoding` (`utf-8`, `utf-8-sig`, `cp1252`), `newline` (`crlf` or `lf`), `final_newline`, and `editable` with `why` in words where it is not. A file with a NUL byte is refused; over 4 MB comes back cut and read-only |
| PUT | `/api/v1/games/{id}/assets/text?path=` | Write that file from `{"text", "version", "replace_changed", "keep_copy"}`, in the encoding and line ending it had, after keeping the file as it was in Backups (kind `asset`, reason `edit`). Answers the new `version` and `kept`, where the copy went (null for none). 409 with `details.refused` `changed` where the file is no longer `version` (`replace_changed` writes over it), or `no_copy` where the copy cannot be kept (`keep_copy: false` writes without). 400 where a line holds a character the file's encoding cannot, naming the line |
| GET | `/api/v1/games/{id}/assets/{kind}/placements` | Where a backglass, ini, script, point of view or score view could go - the folder's own name, or one table's - and what each would replace. A point of view has no folder name |
| GET | `/api/v1/games/{id}/assets/{kind}/displaced?filename=&table=` | What placing that file would replace, asked before the bytes are sent |
| PUT | `/api/v1/games/{id}/assets/{kind}` | Place a file under the folder's own name, which every table without its own reads. A backglass placed, here or by the two routes below, settles the game's tables for the draw order its file calls for; a table that cannot be set is named in the log and set again the next time its game is settled |
| PUT | `/api/v1/games/{id}/tables/{table_id}/assets/{kind}` | Place a file under one table's name |
| POST | `/api/v1/games/{id}/assets/{kind}/import` | The same, from `{"path", "table"}` on this device, under a browsable root |
| DELETE | `/api/v1/games/{id}/assets?path=` | Remove one of those five kinds of file by its path in the game's folder. Removing a `.directb2s`, like placing one, settles the game's tables for the draw order their backglass files call for |
| GET | `/api/v1/games/{id}/archive` | Download a game as `.vpxz` — one table by default; `?file=` picks which table. `?full=true` (whole folder) carries its own scope, `games:export_full` |
| POST | `/api/v1/games/{id}/launch` | Launch a game here. Optional `{"file": "..."}` picks which table |
| PUT | `/api/v1/games/{id}/rating` | Rate a game, `{"rating": 0-5}`. `0` is unrated |
| PUT | `/api/v1/games/{id}/tables/{table_id}/rating` | Rate one table, same body. Refines the game's rather than replacing it; returns the table |
| PUT | `/api/v1/games/{id}/favorite` | Mark a game a favorite, or not, `{"favorite": bool}` |
| PUT | `/api/v1/games/{id}/private` | Mark a game Private, or not, `{"private": bool}`. A Private game is never sent to a community service, whoever plays it - see `docs/extensions/reference/offers.md` |
| PUT | `/api/v1/games/{id}/tags` | The game's tags, the whole set |
| PUT | `/api/v1/games/{id}/play_record` | Set a game's play counters, for a library that arrives already played. `play_count`, `play_time_seconds` and `last_played`; one left out is left alone |
| DELETE | `/api/v1/games/{id}/play_record` | Reset them |
| GET | `/api/v1/games/{id}/high_scores` | The machine's high score table for the default table's ROM, read again first where its score file is newer than the read kept. See [High scores](#high-scores) |
| GET | `/api/v1/games/{id}/tables/{table_id}/high_scores` | The same, for one table's ROM |
| GET | `/api/v1/games/{id}/pictures` | The pictures taken of every screen during play, newest first. `?table=` keeps one table's. See [Pictures](#pictures) |
| GET | `/api/v1/games/{id}/pictures/{name}` | One picture. `?size=` and `?v=` as for media |
| PUT | `/api/v1/games/{id}/pictures/{name}` | A PNG as `file`, under a name no picture of the game has; `409` where one does. Answers with the picture as listed |
| DELETE | `/api/v1/games/{id}/pictures/{name}` | Delete one picture |
| DELETE | `/api/v1/games/{id}/tables/{table_id}/play_record` | Reset one table's |
| PUT | `/api/v1/games/{id}/tables/{table_id}/tags` | One table's own tags, the whole set |
| PUT | `/api/v1/games/{id}/tables/{table_id}/hidden` | Hide one table, or show it again, `{"hidden": bool}`. The file stays on disk. Answers with `table` and `default`, the table the game now offers first, or `null` when it offers none. Hiding the table chosen as the default clears the choice |
| PUT | `/api/v1/games/{id}/default_table` | Which table the game offers first, `{"table": "<table id>"}`; empty clears the choice. A hidden table is refused |
| POST | `/api/v1/games/{id}/tables/{table_id}/script` | Extract the table's script to a `<table>.vbs` beside it. **VPX then runs that instead of the one inside the .vpx.** Needs Visual Pinball on the device called, like `/launch`; `501` where there is none |
| DELETE | `/api/v1/games/{id}/tables/{table_id}/script` | Remove the sidecar, putting the table back on its own script. `404` if there is none |
| POST | `/api/v1/uploads` | Begin an upload session → `{"id": ...}` |
| POST | `/api/v1/uploads/from_path` | Begin a session over a file, a folder or an archive already on this device, `{"path": ...}`. Bounded like `/filesystem`, and needs both `uploads:write` and `filesystem:read`. Analysis, plan and import read it in place; the session's end never removes it, and nothing can be uploaded into it |
| POST | `/api/v1/uploads/{id}/files` | Add a file (multipart: `relpath`, `file`) |
| GET | `/api/v1/uploads/{id}` | Session summary → `{"file_count", "total_bytes"}` |
| DELETE | `/api/v1/uploads/{id}` | Abort a session |
| GET | `/api/v1/uploads/{id}/analysis` | Analyze what was uploaded |
| GET | `/api/v1/uploads/{id}/picture` | A loose image of the session, by `name` as it was dropped, at `size` 256 or 1024 when given. A file that is not an image, or is inside an archive (which is not unpacked until the import), is a 404 |
| POST | `/api/v1/uploads/{id}/plan` | Build an import plan. `asset_kind`, the asset lens's name for a kind (`pup_pack`, `alt_color`, `backglass`...), plans only that kind, and everything else the upload holds comes back under `blocked`. `add_table` with `game_dir` makes a table in the upload one more for that game instead of replacing its table; without it, `replace_table` names the table the drop takes the place of (the game's default where left out), keeping its entry, and `carry` lists the kinds of file named for the old table that follow it, where left out the defaults for an update, or with `instead` those for another release; an item that replaces a table carries `swap`, as the `swap` route answers with `carry` set for each file, and the plan's `choice` lists the tables of the game that can be replaced, which is `table` and whether `replace` is on; one with a filename the game already has comes back under `blocked`. `new_game_dir_name` plans a new game under that folder name, as the import would write it, and a name the import would refuse is refused here too. An item's `replaces` says what it would replace, and where that is a table, `made_from_it` names the game's tables a patch made from it, by filename |
| POST | `/api/v1/uploads/{id}/import` | Execute the plan. Takes `asset_kind` and `add_table` the same way. Takes `replace_table`, `carry` and `instead` the same way. `added_tables` lists the ids of the tables it added, and `replaced_tables` the tables it replaced, each with the `kept` copies in Backups that `swap_back` takes. When an item fails after a table was replaced, the replacement stands and the refusal's `details.replaced_tables` carries the same entries, so `swap_back` can still put the old table back. For a new game, `vps_associated` says whether the `vps_id` sent with it was saved, and without one `vps_matched` says whether VPinFE matched the game from its folder name |
| GET | `/api/v1/filesystem/entries` | What is in one folder. With `kind`, the files that asset kind takes are listed beside the media (`backglass` lists `.directb2s`); `kind` is the registry's name or the asset lens's, so `alt_color` lists both Serum and VNI files. `archives=true` lists archives too. `/filesystem/file` still serves media only |
| GET | `/api/v1/filesystem/roots` | Where browsing may start. `game` puts that game's folder first. Empty when nothing is configured |
| GET | `/api/v1/filesystem/file?path=` | Serve one media file under a browsable root |
| GET | `/api/v1/vps/search?q=&limit=` | VPSdb lookup |
| GET | `/api/v1/vps/entry/{id}` | One VPSdb entry, in the shape a search result has |
| GET | `/api/v1/vps/entry/{id}/releases?listed_as=` | The releases VPSdb lists for one entry, in the order it holds them. `listed_as` picks the list: `tableFiles` by default, or another such as `b2sFiles`. Each release carries `mod_of`, what it is a mod of, or `null` where it is not one - see [Which VPS release a table is](#which-vps-release-a-table-is) - and `urls`, every address VPSdb gives for it with a `broken` flag where VPSdb marks one dead. `url` is the first of those not marked broken, or the first where all are. A value that is not an http or https address with a host is left out |
| GET | `/api/v1/vps/entry/{id}/offers?kind=` | What every enabled source has for one catalog entry, asked together, for an entry the library need not hold: `{"vps_id", "sources"}`, each source as `GET /games/{id}/offers` answers it, and asked in the same way - by the entry's VPS id and name alone, so no game of the library is read, and an extension's source is told no `game_id`. `kind` is a slot's name and repeats; none asks every kind each source serves. A source of an extension the caller may not use is left out, and extensions answer at the Library tier at most. `404` for an entry the catalog does not list. Needs `vps:read` |
| GET | `/api/v1/vps/sync` | When the catalog was last checked, and whether a check is due |
| POST | `/api/v1/vps/sync` | Check VPSdb for a newer catalog now, whatever the schedule |
| GET | `/api/v1/media-sources` | The online sources this install knows, a switched-off one included: the spreadsheet first (`always_on`, with no switch and no kinds), then those that ship, then those extensions contribute. Each has `kinds`, `enabled` and whether it names `versions` |
| GET | `/api/v1/media-sources/offers?vps_id=&kind=` | What they have for one game and kind: `offers`, each `{source, name, url, kind, size, md5}`, `md5` empty where a source publishes no hash |
| GET | `/api/v1/media-sources/changes?since=` | Which VPS entries an extension's source said it has something new for after version `since`, as `{"version", "vps_ids"}`; an empty id means a reader too far behind to be told which. Needs `vps:read` |
| GET | `/api/v1/launchers` | Every launcher this install has, the tables that deviate from the default, and the fields each launcher's app takes. `has_config` says whether its app has settings of its own for `/config` to read. `tables` is how many tables each one plays, by launcher id: the ones that name it and, for a default, the ones that fall to it. Each of `apps` lists its `fields`, so a client can ask for a new launcher's paths before one exists, and its own `has_config`. The launchers themselves are the `launchers` list of `/config`: add or replace one with `PUT /config/launchers/{id}` and forget one with `DELETE`, which sends its tables back to the default. A name another launcher has is refused, whatever its app, compared ignoring case and the spaces around it; a blank name is the app's. Switching one off is refused when the tables it plays would land on a launcher with no program, or on none. What the disk makes of its paths is in `/config/paths`, by `list`, `id` and `field` |
| POST | `/api/v1/launchers/standing` | What an app's path fields would read left empty, before any launcher holds them: `{"app", "settings"}` → `{field: {...}}` for the fields the app can name a file for, each with `default_path` and `default_exists`, or `blank` and `left_empty` where there is none. A read; nothing is written. An app with nothing to say answers `{}` |
| POST | `/api/v1/launchers/{id}/default` | Make it the default for its app, by moving it to the front of the list. A switched-off launcher, or one with no program, is refused |
| GET | `/api/v1/launchers/{id}/fallback` | What switching it off would do: `tables` it plays now, `fallbacks` naming where each group of them would go (`launcher_id` empty for nowhere) and whether that one `has_program`, and `refused`, the refusal a switch-off would get, or `""` |
| GET | `/api/v1/launchers/{id}/config` | The settings of the program it runs, and every value as it stands at one `scope` - `launcher` (the default), `folder` or `entry`. `table=` names the table for the last two. `files` names what a copy of the launcher's settings takes |
| PUT | `/api/v1/launchers/{id}/config` | Set values at one scope, `{"scope", "table", "values"}` → `{"written", "cleared"}`, and `settling` where the write started a job. `launcher` or `entry`: a write at `folder` is refused. `tables` in place of `table` writes into each, as a job (see Launcher settings). A write at `launcher` scope of a key the draw order of backglass and score display depends on starts a job of kind `backglass.order` that brings each table whose backglass file places a Score View DMD to the new order, and the answer carries its id as `settling`; the write is refused with `409`, before anything is written, while such a job runs, and two such writes at once are taken one after the other. A table the job could not set is in its result under `failed` as `[{table, name, reason}]` and counted in its `problems`. A write of such a key at `entry` scope settles that table before it answers; a table that cannot be set is named in the log and set again the next time its game is settled. A write of such a key into several tables with `tables` settles each as part of its job, its failures in the result's `failed`. A file made with `"create": true` settles like a write to a file that was there. A file or folder that cannot be written to is a `409`. A program that has never run has no file to write to, and the write is refused; `"create": true` at `launcher` scope makes its file in the preferences folder of the newest release, holding only the values sent |
| POST | `/api/v1/launchers/{id}/config/file` | Run the program once with the flag that makes it write its full settings file and exit → `{"files"}`. Needs `config:write`. Refused with `400` where the app has no such command, the program is not set, a file is already there, no folder can take the program's preferences on Linux, or the program ran and left none, and with `409` while a table is playing or another request is already running the program; a program that does not exit within 30 seconds is stopped, and the answer is whatever file it had written by then. A launcher in `/launchers` carries `has_settings_file`, `can_make_settings_file` (the program is there, can run and has not already run for it and written none) and `settings_file_not_made` (it did, so the offer ends until the launcher's program changes; held until VPinFE restarts) for a client to decide whether to offer it |
| GET | `/api/v1/launchers/{id}/leftovers` | What an upgrade of the launcher's program left in its settings, `{"launcher", "items"}`. Each item is `{key, action, to, value, files, text}`: `action` is `move` (the value has a new name, `to`), `place` (a window's DMD box, carried into the backglass files that place none for that window, `files` of them), `turn_on` (a switched-off renderer, which `leftovers/turn-on` switches on) or `name` (read by nothing now and replaced by nothing, listed and never written). `text` is the app's words. A program whose installed plugins cannot be read, or whose app keeps no settings, answers `items: []`. Makes no write |
| POST | `/api/v1/launchers/{id}/leftovers/move` | Write every `move` item and carry every `place` item into its backglass files → `{"moved", "held_back", "undo", "order_failed"}`, and `settling` where the settings written started a job (see the config route), in which case `order_failed` is empty and the tables that could not be set are in that job's result; otherwise the games the move wrote files into are settled before it answers and `order_failed` names the tables that could not be, as `[{table, name, reason}]`. Refused with `409`, before anything is written, while a job of kind `backglass.order` runs. Everything that can refuse is asked before anything is written, then the files are written and the settings once, last; if the settings cannot be written the files are put back and the error is returned. A box is carried into all of its files or none: when any one of them refuses (read-only, or a table of the game playing) none is written, the box stays in the settings whole, and it is named in `held_back` as `{key, files: [{path, reason}]}` with the files that refused. A box that would take the move past the 32 files the undo journal keeps is left out the same way, whole, and named in `held_back` with a `limit` of 32 and no files; the boxes go in order, so the first that does not fit and every box after it stay, and the ones before it are moved. A change over 8 MiB, or changes together over it, is refused with `409` and writes nothing. `undo` is the token for the next route, empty where nothing was written. Refused with `409` where the launcher's settings cannot be written |
| POST | `/api/v1/launchers/{id}/leftovers/turn-on` | `{"key"}` writes the value that turns on the `turn_on` item of that key → `{"moved", "held_back", "undo", "order_failed"}`, and `settling` where the write started a job; a turn-on settles only through that job, so `order_failed` is empty and its failures are in the job's result. `404` where the launcher has no such item. Refused with `409`, before anything is written, while a job of kind `backglass.order` runs |
| POST | `/api/v1/launchers/{id}/leftovers/undo` | `{"undo"}` puts back what the move or turn-on with that token wrote: the backglass files byte for byte, newest change first, then the settings → `{"restored", "order_failed"}`, and `settling` where the settings it wrote back started a job (see the move route): the tables whose draw order could not be written as `[{table, name, reason}]`, or empty where the job answers for them. Refused with `409`, before anything is touched, while a job of kind `backglass.order` runs. The settings the move wrote come back as the values they held, and the keys it filled in come back blank, which the program reads as unset, so the settings file is not byte for byte as it was. A token works once, is not kept across a restart, and is one of the last 16. `404` when it is unknown or not this launcher's. `409`, with nothing touched and the token still good, when a setting it wrote has been changed since, when any one backglass file has been changed since or cannot be written (`details.files`) |
| GET | `/api/v1/backups` | Every copy VPinFE has kept, newest first, or those of one game, launcher or theme with `subject_kind` (`game`, `launcher` or `theme`) and `subject_id`: a game's `.info`, a picture or video kept before a turn, a new file or a restore replaced it (kind `media`, with the file's place in the game's folder as `file`), a script, settings file or note kept before an edit or a restore replaced it (kind `asset`, reason `edit` or `restore`, `file` as for media), a table and the files named for it kept before another file took its place or it was deleted (kind `table`, reason `replace` or `delete`, `file` as for media, and `label` for the table file's copy, the version and authors the table went by, empty where none was recorded), the settings and records in the config folder, a launcher's settings file, and a theme's `.previous` folder. Each carries an opaque `id`, what it is a copy of (`subject_kind`, `subject_id`, `subject_name`), its `kind`, when it was `taken`, who `written_by`, the `reason` it was taken, an optional `note`, its `size`, the `version` of VPinFE that wrote it where it is a backup or a file in one (empty otherwise), and `gone` where the thing it is a copy of no longer exists. A backup from `POST /backups` is a row of its own, kind `backup`, with a row for each file in it that carries the backup's id as `within`. `pending` lists the ids that go in when VPinFE next closes, and `folder` is `{path, problem}` for where a backup taken now would go - `problem` is why it could not be written to, empty when it can. Backups are read from that folder alone, so changing `backups.folder` stops listing the ones in the old folder unless they were moved with `POST /backups/folder/move` |
| GET | `/api/v1/backups/folder/change` | What making the folder `to` (blank for the default) the backups folder would do: `old` and `new` as paths, `moving`, how many backups the folder now holds, `already`, how many `new` holds, and `problem`, why `new` cannot be written to, empty when it can. Nothing is changed; `moving` is `0` when the old folder is empty or does not answer |
| POST | `/api/v1/backups/folder/move` | Set `backups.folder` to the folder named, `{"folder": "..."}`, and move the backups in the old folder into it. Returns `202` and a job; its result counts `moved` and lists `left`, the names already taken in the new folder, which stay where they are, and `failed` as `{name, reason}`. Each file is copied, checked for size, then removed from the old folder. Refused with `409` when the new folder cannot be written to or a move is running, and nothing is changed |
| POST | `/api/v1/backups` | Back up now: one zip, with a `manifest.json`, of the files a `scope` names - `games` (the ids in `games`), `all_games`, `records`, `launcher` (the id in `launcher`) or `everything` - and an optional `name` and `note`. The `name` is what the backup is listed under as `subject_name`; left blank, it is the game's name for one game, the first by name and a count of the others for several, and the scope's own name for the rest. A game's backup is its `.info`, never the rest of its folder. Returns the backup as a copy. Written to the folder `backups.folder` names, or `backups/` in the config folder while it is blank. Never removed by the cap, and refused with `400` when there is nothing to put in it, and with `409` when the folder cannot be written to |
| PATCH | `/api/v1/backups/{id}` | Change a backup's `note`; an empty one clears it. Rewrites the zip's manifest and keeps its name, so the id and `taken` stay. Returns the backup as listed. Only a backup from `POST /backups` has a note: the id of a file in one, or of a copy VPinFE took, is refused with `400` |
| POST | `/api/v1/backups/{id}/restore` | Put a copy back. What it replaces is kept as copies and returned as `kept`, so an undo is this call on that id. A game's, a launcher's, a theme's, a media copy's, an asset copy's and a table copy's go in at once; the config folder's wait for VPinFE to close, and say so with `pending`. A launcher's is refused with `409` while a table plays. A whole backup puts back each file in it and lists as `skipped` the ones with nowhere to go, such as a game no longer in the library; a game is found by its id, then by the name of its folder. Put back the id of one file in a backup and only that file goes. A backglass put back settles the game's tables for the draw order its file calls for |
| DELETE | `/api/v1/backups/{id}` | Delete one copy, or a whole backup. A file in a backup is not deleted on its own and is refused |
| GET | `/api/v1/backups/going-back` | What going back to 2.x would restore: `games`, how many hold a 2.x copy, and `collections`, whether yours do |
| POST | `/api/v1/backups/going-back` | Go back to 2.x. Answers with the same counts and then quits VPinFE, which puts each game's newest 2.x copy and the 2.x collections back as it exits, with what they replace kept as copies. Nothing else is touched, so 3.0's settings are as they were for a return. Refused with `400` where there is no 2.x copy, and with `501` where this install cannot quit itself |
| PUT | `/api/v1/launchers/mappings/{table_id}` | Point one table at a launcher, `{"launcher_id": "..."}`. An empty `launcher_id` puts it back on the default |
| GET | `/api/v1/players` | Every player on this install and who is up - see [Players](#players). The same as `players.changed` carries |
| GET | `/api/v1/players/{id}` | One player, as the list has them |
| POST | `/api/v1/players` | Add a player, `{"name", "initials"}`. `201`. Not put up |
| POST | `/api/v1/players/guests` | Add a guest from their initials, `{"initials"}`, and put them up alone. `201` |
| PATCH | `/api/v1/players/{id}` | Rename a player or change their initials, `{"name", "initials"}`. A field left out is left alone |
| DELETE | `/api/v1/players/{id}` | Remove a player, or sign a guest out. `204`. Their accounts and record go with them. The owner cannot be removed |
| GET | `/api/v1/players/{id}/picture` | A player's picture, a WEBP. `?size=` takes 256 or 1024, and `?v=` the player's `picture_version`, which is kept for good. `404` while they have none |
| PUT | `/api/v1/players/{id}/picture` | Set it, as multipart `file`. Cropped to the centre square and kept at most 1024 pixels; a GIF keeps its first frame. Answers the player. `400` for a file that is not an image, and for a guest, who has none |
| DELETE | `/api/v1/players/{id}/picture` | Take it away. Answers the player |
| PUT | `/api/v1/players/{id}/up` | Put one player up beside whoever is, `{"up": true}`, or take them down. Answers with the whole list |
| PUT | `/api/v1/players/up` | Say exactly who is up, `{"ids": [...]}`. An id nobody has is a `404` and changes nothing. Answers with the whole list |
| GET | `/api/v1/players/{id}/record` | What a player other than the owner has done with each game - see [Records](#records) |
| PUT | `/api/v1/players/{id}/ratings/{game_id}` | A player's rating of a game, `{"rating": 3}`, 0-5 as `PUT /games/{id}/rating` takes it. Answers `{"rating"}`. Not the owner's, which is the library's |
| POST | `/api/v1/players/guests/card` | Add a guest from their card, `{"card": "..."}`: the card file's contents, or the card's text on its own. `201`, up alone. The same card again puts that guest up rather than adding another - see [Accounts and cards](#accounts-and-cards) |
| GET | `/api/v1/players/{id}/accounts` | Every account this player can hold here, one per running extension that offers one |
| GET | `/api/v1/players/{id}/accounts/{extension}` | One of them |
| PUT | `/api/v1/players/{id}/accounts/{extension}` | Change it, `{"values": {...}}`, as the extension's settings are changed. Answers the account |
| PUT | `/api/v1/players/{id}/accounts/{extension}/share` | Say whether it shares, `{"share": true}`. Answers the account |
| POST | `/api/v1/players/{id}/accounts/{extension}/acts/{act}` | Do one of the account's acts. Answers what the extension says came of it |
| GET | `/api/v1/players/{id}/accounts/{extension}/card` | The player's card, an SVG file. `404` while the extension cannot make one |
| POST | `/api/v1/players/{id}/accounts/{extension}/card` | Use a card for this account, `{"card": "..."}`, taken as `POST /players/guests/card` takes one. Its values replace the account's; the player's name, initials and Share stay. Answers the account. `400` for a card this extension does not read |
| POST | `/api/v1/players/{id}/accounts/{extension}/picture` | Use the picture the account's service has as this player's. Copied once, kept as `PUT /players/{id}/picture` keeps an upload, and never followed. Answers the player. `404` while the service has none, `400` for a guest. The account says whether there is one: `picture` on `GET .../accounts` |
| GET | `/api/v1/metrics` | What this device is doing now: `now` holds the processor, memory, `disks` and `graphics`, with `cpu_temperature` in degrees C where the platform reports one (Linux) and `busiest`, the five programs using most of the processor as `{name, cpu_percent, memory}`, a program being an app bundle's every process (`Claude Helper (Renderer)` is Claude) or, with no bundle, every process of one name, `cpu_percent` per core as Activity Monitor and `top` read it (100 is one core, so a program on several cores passes 100) except on Windows, where it is the share of the whole device as Task Manager reads it, and a process the OS will not let this one read, such as root's on macOS, left out; `busiest` is null until a second reading, since a process's first reads 0. Each of `disks` is a volume, carrying `holds`: what is watched on it, as `role` (`library`, `config` or `cache`) and, for a library, the folder's `name`; a volume that cannot be read carries `error`, the reason in words. `graphics` is `{cards, reason, detail}`: one entry per card with `id`, `name` (a number follows the name of cards that share one, `RTX 1` and `RTX 2`), `vendor`, `source` (the reader that answered) and the readings that source has - `busy`, `memory_used`, `memory_total`, `memory_percent`, `temperature`, `power`, `clock`, `fan_percent`, `fan_rpm` - where a reading the source lacks is absent, never 0. A card nothing reads carries a `reason` and no `source`, plus `detail` where the program said more and `tool`, the id of the Tool whose Settings row would fix it; no cards at all is an empty list with the `reason`. `history_seconds` adds as much of this session as you ask for, each point carrying `gpus`, each card's `busy` by `id`, and `marks`, what happened in that time, oldest first, as `{at, kind, name}`: each job started (`kind` `job`, `name` its kind, and `title` too where it has a name of its own) and each table launched (`kind` `table`, `name` the game's title, empty when the game is private); 0 means no history and no marks. Two callers within a second get the same reading |
| GET | `/api/v1/about` | What this install and this device *are* - version, build, OS, browser, and where files live. Each fact carries `path`, true where the value is a path on disk, which a client draws as a path. `text` is the same answer as something to paste into a report |
| GET | `/api/v1/config/schema` | Every setting this install has. An option's `group` is a token (`navigation`, `connections`...) and `group_label` beside it is the heading in this install's language. `path` says what a string names on disk (`file`, `dir` or `exe`). `default_is` says whose the default is, `community` or `vpinfe`, for a client that reminds a person of it from `default`, `unit` what a number counts (`seconds`, `port`), `min` and `max` the bounds of a number, which a write outside them is refused with `400`, `as_buttons` that a choice reads as a row of buttons with `choice_help` saying what each one does, `group_note` a line for the whole group and `cuts_off_manager` that a change can put this Manager out of reach of the device making it and `moves_with` (`backups`) that the folder it names holds files a client should offer to carry across before saving a new one. `applies` says when a saved change takes effect: `now`, `table` (the next table to start), `windows` (the next time the frontend's windows open) or `restart`. A window the active theme declares beyond the three every theme has is a `windows.<name>` section of its own, with the `screen_id` it opens on, after `windows.scoreview`. `lists` describes each top-level list of objects the file holds, each with its `fields` and, where its items differ by kind, a `variant` naming the field that holds the kind and the fields each kind has now; it is empty until a list is declared |
| GET | `/api/v1/config/waiting` | What is saved and not yet in effect. `waiting` lists each change as `{kind, section, key, label, label_key, waits_for, in_effect, set}`: `kind` is `setting` or `extension`, `waits_for` is `restart` or `reopen`, `in_effect` is the value this run is using and `set` the value saved. A setting declared `restart` is compared with the value VPinFE started on, a `windows` one with the value the windows last opened on, and changing a value back clears it. A `windows` setting waits for a restart instead of a reopen while the set of windows has changed - a window added or removed, or a theme declaring others - and never waits on an install with no windows open. An extension switched on since the run began is listed as `extension` with `waits_for: restart`. `reopen` says whether this install can reopen its windows; `http_port` is the port the next start listens on |
| GET | `/api/v1/config` | What this install is set to: `{settings, theme_options, <list>...}`, `settings` by section and key, `theme_options` by theme folder and one array for each list the schema declares |
| GET | `/api/v1/config/theme_options` | What was chosen on each theme, `{<theme folder>: {<option>: value}}`. A theme nobody has changed is absent |
| GET | `/api/v1/config/theme_options/{theme}` | One theme's chosen values, `404` for a theme with none |
| GET | `/api/v1/config/settings` | The settings alone, by section and key. `dof.config_tool_api_key` reads `true` or `false`, whether a key is set, on this and every other read of the config; it is written as the key itself, and a boolean written to it is refused with `400` |
| GET | `/api/v1/config/settings/{section}` | One section's settings, `404` for one that does not exist |
| PUT | `/api/v1/config` | Change settings and lists, `{settings: {section: {key: value}}, <list>: [...]}`. A patch: only what is sent is written, and a list sent replaces that list. `theme_options` is `{<theme folder>: {<option>: value}}`: each theme named takes the values sent, checked against the options that theme declares (a theme that is not installed keeps them as sent), an empty object takes that theme's choices away, and the themes not named are left alone. A key that is neither `settings`, `theme_options` nor a declared list is refused, and a refused setting or list writes nothing. A port that is out of range, that something else is listening on, or that another port setting holds is refused with `400`; `details.settings` says why for each, by `section.key` |
| GET | `/api/v1/config/{list}` | Every item of a declared list, in order. `404` for a name that is not a list. `schema`, `paths`, `tools`, `waiting` and `settings` are never list names |
| PUT | `/api/v1/config/{list}` | Replace the list, in the order sent; an item without an `id` is given one. A field the list does not declare, or a value of the wrong type, is refused with `400` and nothing is written |
| POST | `/api/v1/config/{list}` | Add one item, `201`. The install mints its `id`; a body carrying one is refused |
| GET | `/api/v1/config/{list}/{id}` | One item, `404` where there is none |
| PUT | `/api/v1/config/{list}/{id}` | Replace the item with that id where it stands, or add it under that id. A body `id` that differs is refused |
| DELETE | `/api/v1/config/{list}/{id}` | Remove it, `204`; `404` where there is none. Every write here is announced as `settings.changed`, naming the list |
| GET | `/api/v1/config/paths` | Whether each path setting, and each path in a list's items, finds anything on this device. A setting's check carries `section` and `key`; an item's carries `list`, `id` and `field`. A setting whose blank means a folder VPinFE chooses, the backups folder and the manufacturer logos folder, carries `default`: the path a blank one points to here |
| GET | `/api/v1/config/tools` | Each Tool - a program VPinFE runs and does not ship, such as unar or ffmpeg - as this device finds it when asked: `state` (`found`, `missing`, `unusable`, `not_here`), the `path` and `version` found, `set_here` where its `setting` is what found it, and `default_path` and `default_version` for the program found with no setting - the same as `path` and `version` unless `set_here`. A missing or unusable one carries `fix: "user"` and a `remedy` of `key`, `params` and `setting`, rendered with the setting's label and the Settings section as its `setting` and `section` slots; an unusable one also a `reason` of `key` and `params`. Where VPinFE can get the Tool itself - FFmpeg, on Windows and macOS - `fix` is `"auto"` and the remedy's `get` says what it would download: `version`, `source` and `size` in bytes |
| POST | `/api/v1/config/tools/{id}/get` | Download a Tool VPinFE can download itself, as a `202` job; the request is the consent. The build pinned for this device is downloaded into this install's own folder, kept only where its size and SHA-256 are the pin's, and run once, after which discovery finds it. `404` for a Tool VPinFE cannot get, `501` on a device it has no build for, `409` while one is being got. `config:write` |
| GET | `/api/v1/preferences/{scope}` | A stored UI arrangement |
| PUT | `/api/v1/preferences/{scope}` | Store one. The body is the whole value |
| GET | `/api/v1/locations` | Every location this install looks in, in order, each with what the disk says about it now and how many game folders it holds (`games`). `write_to` is the one a new game goes to, even while it is away |
| GET | `/api/v1/locations/destination` | Where a new game would be created, or why it could not, and where else it could go |
| GET | `/api/v1/locations/{id}/shadowed` | Game folders here whose id another folder answers for, with both sides |
| POST | `/api/v1/locations/{id}/shadowed/adopt` | Give one of them, `{"path"}`, an id of its own |
| GET | `/api/v1/folders?path=` | This device's folders under a path, for a folder field's Browse button. Unbounded, unlike `/filesystem` - empty `path` starts at the home folder, `roots` names where else browsing can start (home, drives, `/`, `Volumes` on macOS), each once - places of one name are named by their path, with `~` for home where the system has one. `config:write`, since picking where a library folder should be is itself a config-writing act. Answers only this device's own Manager - a caller reaching the API from elsewhere is refused - and never lists a folder that is private (`.ssh`, browser profiles, keychains, and their platform equivalents), nor shows one as a child of its parent |
| GET | `/api/v1/folders/file?path=` | One image, video or sound anywhere on this device, so a picker can show it before it is taken. `config:write`, and this device's own Manager only, like the listing. Held to media by extension, and judged on what the path resolves to: a link into a private folder, or to a file that is not media, is refused. `/filesystem/file` stays the bounded one for API clients |
| POST | `/api/v1/folders` | Make a folder, `{"parent", "name"}`, and answer with its own listing. `config:write`, and this device's own Manager only, like the listing. Refuses a name that holds a slash or a control character, a name already taken, no parent, and a private parent |
| POST | `/api/v1/folders/uploads` | Begin an upload from a file or folder anywhere on this device, `{"path"}` - the same session as `/uploads/from_path`, which stays held to the library folders for API clients. `config:write` and `uploads:write`, this device's own Manager only, and a private folder is refused. Answers the upload's id; the upload then runs and is read like any other |
| POST | `/api/v1/folders/games/{game_id}/tables` | Add a table file from anywhere on this device to a game, `{"path"}` - the same body and answer as `/games/{game_id}/tables/import`, which stays held to the library folders. `config:write` and `games:write`, this device's own Manager only, a private folder is refused, and the file still has to be one a program plays |
| GET | `/api/v1/themes` | Every frontend theme this install knows, active first. `refresh=true` re-reads the sources, which reaches the network. Each has `registry`, the address of the registry that offers it - empty for one listed by its own repository or placed by hand - `updated`, the date of the newest commit on the release this build would install, and `preview_version`, which changes when the picture does. `layouts` lists the layouts it is made for (`cab`, `desktop`), and `previews` has `{"url", "version"}` for each layout with a picture of its own, empty for a 2.x theme whose one picture stands for both layouts. `preview` is where the main picture is at its source; a client that draws it asks `/themes/{key}/preview`, with `layout` for one layout's. `checked` is when the sources were last read |
| GET | `/api/v1/themes/sources` | The configured sources in their order, as `registries` (lists of themes) and `repositories` (single themes), each with its `kind`, a `state` (`ready`, `not_checked`, `not_answering`, `not_found`, `not_a_theme`, `needs_vpinfe`, `some_unreadable`, `not_used`), a `level` to color it by, a `reason` in words, what it `took`, and the `label` the person gave it (empty where none). Reads nothing: a source never read is `not_checked` |
| POST | `/api/v1/themes/sources/check` | What adding `{"address"}` would be, without saving it: the `kind` found, the `address` it would be stored under, `addable`, and the same fields as a source. `config:write`. Nothing is installed. Saving is `PUT /config` |
| PUT | `/api/v1/themes/sources/name` | Call an added source something, `{"address", "name"}`: its `label`, shown in place of the name it has. A blank `name` takes it back. `config:write`. Kept apart from the address lists, which stay addresses. `404` for an address that is not added. Answers with the `address` and `label` |
| POST | `/api/v1/themes/{key}/install` | Install or update. Installing over an existing copy is what an update is. Refused (`error.themes.not_accepted`) for a theme from a source other than the VPinFE Theme List until it is accepted |
| GET | `/api/v1/themes/unaccepted` | The installed themes nobody has accepted, `{"themes": [{"key", "name"}]}`. Reads the themes folder alone, never the network. The frontend plays the default theme in place of one of these |
| POST | `/api/v1/themes/{key}/accept` | Accept a theme from the address this install knows it by - a source other than the VPinFE Theme List, or the themes folder for one no source offers. Answers with its `trust`. A theme from a different address asks again. `protected:write`. `404` for a theme nothing offers |
| DELETE | `/api/v1/themes/{key}` | Remove an installed theme. Refused for the active one - the frontend would come up with no theme at all |
| PUT | `/api/v1/themes/active` | Choose which theme the frontend plays. Takes effect when the frontend next starts |
| GET | `/api/v1/themes/{key}/preview` | The picture the theme's manifest names, at `size` 256 or 1024 when given. `layout` (`cab` or `desktop`) asks for that layout's picture, and a layout with none of its own gets the main one. One not installed is fetched from its host once per release and kept here, so a new release is a new copy; the same release is asked about again after a day. `v` is the listing's `preview_version`, and a request that carries the current one may be kept for good. `404` for a theme with no picture or one that cannot be fetched |
| GET | `/api/v1/themes/{key}/options` | What a theme declares it can be configured with, and what it is set to |
| PUT | `/api/v1/themes/{key}/options` | Change them. Kept in `theme_options` in `vpinfe.json`, not inside the theme, which an update deletes |
| GET | `/api/v1/extensions` | Every extension this install looked at, running or not, and what each declared. `enabled` is the switch, which is not the same as running. One that is not running carries `reason`, in this install's language, and `reason_key`, the catalog key it was read from. Each carries `author`, `home` (empty when it has none), `source`, `picture` (the path its manifest names, empty when none), `path` (the extension's own folder) and `picture_shown`, true once the picture may be drawn: the extension ships with VPinFE or an owner accepted it |
| GET | `/api/v1/extensions/{name}/picture` | The picture the extension's manifest names, at list size, as a webp. `?v=` is the extension's version and only makes a new version a new address. `404` for a name nothing answers to, one with no picture, one that is not shipped and not accepted, and a file that does not decode. `instance:read` |
| GET | `/api/v1/extensions/needs` | What the running extensions ask for on Overview, `{"needs": [...], "away": [...], "failed": [...]}`. Each need carries its `key`, `label`, `says`, `count`, `covered` (what a dismissal is kept against), `tone`, and what it opens. `away` lists the extensions that did not answer, a stopped one included, so what was set aside against them is kept, and `failed` the ones that raised. See `docs/extensions/reference/ui.md` |
| PUT | `/api/v1/extensions/{name}/enabled` | Switch one on or off, `{"enabled": bool}`, written to `extensions.switched_off`. Answers with the extension. Off takes it out at once, as a fault would, and its `reason_key` says it was switched off. On waits for the next start, because routes mount once: until then it stays `off`, with `extension.reason.pending_restart`, or the reason this install cannot run it |
| POST | `/api/v1/extensions/{name}/accept` | Accept an extension VPinFE did not ship, as it declares itself now (its `scopes`, `capabilities` and `tiers`), and start it. A later version that declares more stops until accepted again. A copy that replaces a built-in starts at the next start, and the built-in runs until then. Answers with the extension. `protected:write`. `404` for a name nothing answers to |
| any | `/api/v1/ext/{name}/…` | An extension's own routes, gated on the scope of the tier each declared (`ext:<name>:play`, `:library` or `:protected`). `501` once it has stopped or been switched off, naming which one and why. One that never started, switched off before this start or not for this install, has no routes, so `404`. See `docs/extensions/reference/served.md` |

Discovery is the entry point: an integrator learns what an instance offers by asking it,
rather than matching a version number against a document.

```json
{
  "name": "VPinFE",
  "install_id": "7Kq2mVx9Ab",
  "display_name": "basement cab",
  "features": ["core", "library", "frontend", "devices", "overview"],
  "api_version": "v1",
  "vpinfe_version": "2.5.0",
  "capabilities": [],
  "extensions": [],
  "links": {
    "self": "/api/v1",
    "health": "/api/v1/health",
    "openapi": "/api/v1/openapi.json",
    "docs": "/api/v1/docs",
    "events": "/api/v1/events"
  }
}
```

Artwork and themes are served on the same port as the API, under `/media`, `/tables` and
`/themes`, so the document names no other server.

Links are relative so they stay correct behind a reverse proxy. A link that is present but
`null` is a known part of the contract that this instance doesn't offer — clients should
branch on that rather than on its absence.

`name` is the product and reads the same on every install, so it can't tell two apart.
`install_id` can: it's minted once on first start, opaque, and stable for the life of the
install. Address an install by it and nothing else — `display_name` is for showing a
person, defaults to the device's hostname, and is meant to be renamed, so anything
resolving through it breaks the first time somebody does.

`features` says what this install is for: `library` curates the game library, `frontend`
launches games on that device, `devices` manages the other installs on the network, and
`overview` is a front page for what needs attention. It defaults to all four. An install
that wrote its own list keeps exactly what it wrote.

`core` is always in the list and is never one of them. It is what every install has
whatever else it is for — its identity, its features, the network and logging — so an
install reporting `core` alone is one that is not currently for anything and can still be
configured into being for something. It is synthesized rather than stored, so a client
must not expect to find it in the config.

`setup_pending` is `true` while this install has not finished Setup, the questions asked on
a first run, and is left out of the document once it has. A client that finds the key
absent treats the install as set up, which is also what it finds on a build from before
Setup existed.

## Conventions

Field names are `snake_case` (see `docs/conventions.md`), matching Python's own convention (PEP 8) so nothing has to be
translated on the way out, and matching the repo's existing JSON. Plenty of JSON APIs use
snake_case for the same reason — GitHub's and Stripe's among them.

A game resource carries `id` (this install's id) and `vps_id` (correlation with VPSdb and
friends). Sub-resources are linked from `links` rather than assembled by the client.

### `label` and `label_key`

A resource that carries a `label` carries a `label_key` beside it — the filter axes, the
config schema, media and asset slots, actions (an install's and an extension's), the upload
plan, a launcher's fields and settings groups, and a graphics card's readings. A label that is a program's own words,
such as a Visual Pinball setting read out of its ini, has an empty `label_key`, because
there is no key behind it.

`label` is the words themselves, in whatever language this install speaks. It is what a
client that does nothing new keeps rendering, and it will not change *meaning*.
`label_key` names the same string in the translation catalog, for a client that would
rather resolve its own words - the key never moves, whatever the label says:

```json
{ "name": "manufacturer", "label": "Manufacturer", "label_key": "filter.manufacturer.label" }
```

Which language the server answers in comes from `[general] language`. A client that reads
`label` gets that language; a client that reads `label_key` decides for itself. Neither is
a compatibility change — `label_key` is additive, so a theme or script that has never
heard of it is unaffected.

Table names, manufacturers and everything else your library holds are content and are
never translated. Sorting follows the language being served, so do not assume row order
is stable across languages.

**Resources gain fields without a version bump, so ignore ones you don't recognize.**
Adding a field is additive and happens routinely — three of the 3.0 changes did it. A
client that rejects unknown keys, or round-trips a resource by enumerating them, breaks on
the next release for no reason. Renaming or retyping a field is the opposite: that is a
compatibility change, it gets a `docs/compatibility-3.0.md` entry, and it does not happen
inside a version.

Path segments are hyphenated (`/games`); JSON field names are `snake_case`
(`links.tables`). Different namespaces, different conventions — hyphens read better in a
URL and nothing has to be translated on the way into Python.

**Media and assets are not the same thing** (`docs/conventions.md`). Media is the artwork
VPinFE shows you while browsing; assets are what the game needs to *play* as intended.

Media is served under `GET /games/{id}/media`: every kind from `common/media/media_specs.py`,
present or not, so a client enumerates what is possible instead of guessing from omissions.
A present kind links to `/media/{kind}`, which streams the file with its real content type.
Each entry carries `pages`: 0 for nothing, 1 for a single file, and for a flyer or an
instruction card the number of pictures it has. `?page=n` on the file, `detail`, `displaced`,
`PUT` and `DELETE` routes, on a game's and on a table's, addresses one of them. Pages
follow page 1: they are named at its tier and appear for the tables it appears for.
Resolution is the same three-tier chain the scan uses, applied to the folder as it is at
request time: a spec-named file for the launching table (`(Wheel) <table>.png`) beats a
folder-named one (`(Wheel) <folder>.png`) beats the fixed default (`wheel.png`), each kind
trying its extension family in order, `medias/` before the folder root throughout.
Consumers never learn these rules — every kind still reports exactly one winning file.
One cross-kind rule: a game with a `logo` and no wheel serves the logo in the wheel slot,
below every real wheel tier; such an entry carries `via: "logo"` so a client that cares can
tell a fallback from the real thing. An unknown kind is an
`invalid_request` naming the known kinds; a known-but-absent kind is a `not_found`.

**Draw media at the size you draw it.** `?size=256` or `?size=1024` sends a picture no
longer than that on its longest edge, as WebP, made once and kept until the file behind the
slot changes. A picture is never made larger. An animated one is its first frame, still, at
256, and sent as it is at 1024; one that cannot be read is sent as it is. Any other size is
an `invalid_request` naming the two, and so is a size on a video or a sound.

**Keep media for good by naming its version.** Each present entry, each row of
`GET /media`, a table row's `media_version` and a collection's `image_version` carry a
`version` that changes whenever the file does. A request whose `?v=` matches the current one is sent as `immutable` with a
year's `max-age`, so a browser never asks again; anything else, including no `v` at all,
is sent `no-cache` and answered `304` while the file is unchanged. A stale `v` is never
kept, so an address from an old listing still gets the new file.

Assets have two lenses, both computed from the folder at request time:

- **The launch lens** — each entry in `GET .../tables` reports what *that* table
  would use on launch, mirroring VPX's own lookup order per kind: `dedicated` (a file named
  for the table), `shared` (the folder-named fallback, which a table named as its folder
  reports too), or `none` — plus the winning filename. A `.pov` never falls back to the
  folder name, because VPX doesn't.
- **The inventory lens** — `GET /games/{id}` attributes every asset file in the folder:
  `dedicated` to the table it serves, `shared`, or `orphaned` (stem-named for a table
  that is no longer there — what an audit wants to see). The list endpoint carries a
  presence summary only.

Games also carry `dependencies` — things the *script* declares and content on disk
satisfies, which is a different mechanism from an asset found by naming rule:

- `pinmame` is a chain: `declared` (the script's ROM name) → `alias_of` (rewritten by the
  game's `pinmame/alias.txt`, the way PinMAME itself does) → `effective` → `installed`,
  with `required` saying whether the script actually drives the emulator — measured from
  the script, not guessed from the name's shape. `required: true` with `installed: null`
  is the case worth surfacing; `required: false` means the declared name is a DOF key;
  `null` means the game's metadata predates the detector and a rebuild will fill it in.

  When the device's own VPX install ships `libpinmame` (discovery declares this as the
  `rom_audit` capability), the chain also carries PinMAME's own answer: `catalog` (the
  engine knows this set), `clone_of` (the parent set an unmerged clone needs),
  `description` (the version label), and `audit` — `ok` upgrades `installed` to true with
  chip-level certainty, `missing` finally makes `installed: false` sayable,
  `unknown_set` means the name isn't in this PinMAME's catalog (an alias may be needed,
  or the set is newer than the shipped library), and `unavailable` leaves the name-match
  conclusion standing. The library is borrowed from the configured launcher's install,
  never bundled — a device that can't launch has no use for the audit.
  `installed` is true or null, never false: the name may be a DOF key on a ROM-less EM
  game, an unmerged set may need a parent zip, and global ROM locations aren't searched —
  so "not found here" is not "missing". `nvram` rides the chain (`present`, `file`,
  `modified_at`, stat-ed live) because a competition harvester's question is "is there a
  score newer than my last visit".
- `flexdmd` reports whether the script uses FlexDMD and what `.UltraDMD` content exists;
  `declared` stays null until the project-folder extraction is built.

Each table answers for itself. One nothing has read yet reports `null` throughout, its
`features` included, rather than inheriting another table's answer.

`rom` on the game resource is the recorded table's **declared** ROM name kept as plain
metadata; the full chain (alias, effective, installed, nvram) lives on the table's
`dependencies.pinmame`.

## Errors

Every failure under `/api/v1` is returned in one shape, with a real HTTP status:

```json
{"error": {"code": "not_found", "message": "No such game", "details": null}}
```

`code` is the contract. Branch on it; don't parse `message`, which is meant for humans and
may be reworded. `details` is optional structured context — for a validation failure it
carries the offending fields.

Raise, don't build responses manually:

```python
from httpapi.errors import NotFoundError, InvalidRequestError, FeatureUnavailableError

raise NotFoundError(f"No game with id {game_id}")
raise InvalidRequestError("sort must be one of: name, year", details={"got": sort})
raise FeatureUnavailableError("DOF is not configured on this instance")
```

For anything else, `ApiError(code, message, status_code=..., details=...)`. An `OSError`
that reaches the top is returned with the reason in words as the message. One from this
device, such as a read-only folder or a full disk, is `409` `conflict`: *VPinFE does not
have permission for /path/to/the/folder*. One from another device, a host that did not
answer, could not be reached or answered with an error, is `502` `upstream_unavailable`:
*Nothing answers at 192.168.1.20*, and is worth retrying. Any other uncaught exception
becomes a logged `internal_error` with no detail in the response — put anything the user
needs into an explicit `ApiError`.

Codes defined so far: `not_found`, `invalid_request`, `method_not_allowed`, `conflict`,
`upstream_unavailable`, `feature_unavailable`, `too_many_requests`, `internal_error`, plus
`unauthorized` and `forbidden` for the authorization boundary. Add new codes to `httpapi/errors.py` rather than inventing them at a call site.

## Authorization

Every request into `/api/v1` passes one middleware that stamps an identity on it, and every
route declares the scope it needs:

```python
@router.get("/games", dependencies=[requires(scopes.TABLES_READ)])
```

**Who gets which scopes is `SignInPolicy`.** A request from this device holds every core
scope. A request from another device holds `instance:read` alone, which is enough to ask
what this instance is and to ask for a sign-in code, until it has signed in or presents a
token. Signing in is a six-digit code shown on the device and typed on the other device,
or, once the Owner has set one up, the six digits an authenticator app shows (RFC 6238,
SHA-1, 30 seconds, one step either side, a step used once). The `/auth` routes above are the
whole of it. A cookie that is not signed in, expired or signed out holds none.

Wrong codes are counted per address, per sign-in for a Confirm or a new setup, and across
all addresses together (20), so a second address does not buy more tries. A locked address
answers `429` `too_many_requests` with the seconds to wait in `details.wait`.

**Three tiers.** A route's scope puts it in one of them.

| Tier | What it covers | Who holds it |
|---|---|---|
| Play | Reading the library, collections, players and what is playing; launching, stopping, pressing buttons, capturing with the configured command, rating and favoriting, joining as a guest, signing a guest out and saying who is up | The Remote's pass, a Play token, any Owner, this device |
| Library | Everything that changes what VPinFE knows or shows. Opening the frontend's windows (`frontend:open`) sits beside it, for an Owner and this device only | A signed-in Owner, a Full Control token, this device |
| Protected | What runs (commands, launcher programs, tools, theme and extension sources and acceptance, updating), what destroys the way back (restoring a backup, the backup folder, going back to 2.x, deleting a game's folder from the disk), what hands out access (making a token, the Trusted Proxies and Trusted Networks lists) and what takes the device down (`protected:write`, `system:admin`) | This device, or an Owner holding a Confirm. Never a token, except `system:admin` on a token whose Can Shut Down and Restart switch is on |

An Owner that reaches a Protected route without a Confirm is answered `403` with
`details.reason` of `confirm_needed` and the `scope`; a token or a Remote pass is answered
`details.reason` of `protected`, because nothing it can do changes that. A client that can
show the Confirm asks `/auth/confirm/code`, then `/auth/confirm`, and repeats the call. The
Manager does exactly this.

**Tokens** are named, shown once, kept hashed and revocable. Send one as
`Authorization: Bearer <secret>`. Full Control is Play and Library; Play is Play. Neither
holds `access:*` or `protected:write`. A token made by pairing is Full Control.

**The Manager acts for a browser.** The Manager draws pages for browsers on other devices
but calls this API from the same device over loopback. Each such call carries
`x-vpinfe-acting`, a 60-second HMAC naming the browser's sign-in (or `-` for a browser that
is no longer signed in), and is held to that sign-in's tier. A loopback call with no such
header is this device. The header is honored only from loopback and only with the
install's own signature.

An identity carries `origin` (`local` or `network`), decided by the request's own socket,
`Host` and `Origin` and never by `X-Forwarded-For`, which the caller writes. A signed-in
identity also carries `session_id` and `kind`. A cookie on a write that the browser says
another site began (`Sec-Fetch-Site: cross-site`) is ignored, so a page of someone else's
cannot act with a sign-in it never held.

Three properties keep it honest:

- The middleware runs before any route, so no route is reachable without passing it.
- **Startup fails if a route declares no scope.** A boundary you can forget to use is not a
  boundary, so forgetting is made impossible rather than discouraged.
- Core services never learn about any of this. Authorization stays at the edge; nothing under
  `common/` takes an identity argument.

"Public" is not something a route asserts about itself. Discovery and health carry
`instance:read` like everything else, and a policy decides whether to grant it to a caller who
presented nothing.

Scopes are `<resource>:<action>`. Extensions get `ext:<name>:<action>`, which is what stops an
extension ever claiming a core scope. The vocabulary is in `httpapi/scopes.py`; most entries
are reserved for endpoints that don't exist yet, because settling a name is cheap and renaming
one after callers depend on it is not.

## What this API assumes about your network

**It answers on every interface by default, and another device gets in with a code.**
Anything that can reach the port may ask what this instance is and ask for a code. The code
is shown on the device, never sent back to the asker, so someone who can reach the port and
cannot see the screen gets nothing else. Once typed, the browser holds Play and Library until it
is signed out or goes unused for 90 days; a Protected change asks for a Confirm on top.

Over plain HTTP the cookie and everything after it cross the network unencrypted, so sign in
from a network you trust. A home LAN behind a router is the assumption. A shared or public
network is not, and neither is port-forwarding it to the internet. Away from home, connect
through a VPN and reach the device by its VPN name.

**An encrypted connection stands in for the Confirm.** A request counts as encrypted when
VPinFE itself serves HTTPS, when it comes through a Trusted Proxy that says `https` in
`X-Forwarded-Proto`, or when its socket address is inside a Trusted Network (Tailscale's
range is one click). A Protected change from an encrypted connection needs no Confirm. A
proxy is believed only by its own single address, never a range, and a proxy on the same
device counts only once HTTPS is on, because otherwise every visitor would look like this
device. Trusted Networks are judged by the socket address and never by a header.

`network.http_bind` (Reachable From in Settings) decides reachability. It defaults to
`0.0.0.0` and serves the API, the Manager, themes and the table library's media.

It answers everywhere because the Remote and the sign-in page are the point - a
mobile device administering a VPinFE device is a supported workflow, not an accident. Set it to `127.0.0.1`
if you want the API reachable only from the device itself; the Remote and the Manager on
other devices stop working, which is the trade.

Table media is the one thing on that port that is not the API's own. A caller that is not
this device needs `games:read` for `/media` and `/tables`, and a file outside the default
export bundle needs `games:export_full`.

Set it to `127.0.0.1` and the device is the only one that can sign in at all.

## Capabilities

Discovery reports what this instance can actually do, so a feature that needs hardware or
configuration it doesn't have isn't advertised as if it worked:

```python
from httpapi import capabilities

capabilities.declare(capabilities.Capability(
    name="peripherals",
    feature="frontend",
    description="DOF and real-DMD output",
    is_available=lambda: (dof_configured(), "DOF is not configured"),
))
```

`is_available` runs per request, not once at import, so the answer stays honest after the
user changes a setting. Return `(False, reason)` rather than a bare `False` — the reason is
shown to users, so say what's missing and how to fix it.

`feature` records which feature switches a capability on, and a capability whose feature
is switched off is not served at all — it is absent from discovery rather than listed as
unavailable. "I don't do that" and "I do that and it's broken" are different answers, and
only the second is something to act on.

It is null for infrastructure. The event stream carries library events and launch events
alike, and jobs report progress for whatever ran them, so neither belongs to a feature and
no feature switch can take them away.

Do not confuse it with `available`. `feature` is what the operator chose; `available` is
whether the thing actually works given that choice. An install with `frontend` on and no
launcher configured declares `launch` and reports it unavailable, with a reason.

## Launching

`POST /api/v1/games/{id}/launch` starts a game on this play host. It returns `202` as soon
as the launch has started - not when the game exits, which can be hours later. Watch
`/api/v1/events` to find out what happens next.

```
POST /api/v1/games/6f1c9a4e.../launch
{"file": "Table Name VPW Mod.vpx"}
```

`file` is optional and names one of the game's tables (`GET .../tables` lists them);
leave it out for the game's default. A name that isn't in the game's own folder is refused,
so this can't be talked into running something else.

Every refusal happens before anything starts, and is returned synchronously:

| Status | Code | Means |
|--------|------|-------|
| 404 | `not_found` | no game with that id |
| 400 | `invalid_request` | the named `file` isn't one of this game's tables |
| 409 | `conflict` | something is already playing — two VPX processes would fight over the same hardware |
| 501 | `feature_unavailable` | this game can't be launched here: no launcher for its app, a launcher with no program or one that isn't there, a table on a share that isn't reachable, or no table at all |

The endpoint carries `launch:invoke`, which is deliberately not `games:write`: reading the
library, changing it, and making the device do something are three different permissions.

Discovery declares a `launch` capability separately from `play`, because reading what's
playing works on a device that can't start anything. Check it before offering a Play button
rather than finding out from a 501.

This is the same launch the wheel and the Remote Control page use. That matters more than it
sounds — it means a launch from the API counts as a play, records the date and the start
count, reads the score back out of NVRAM, and hands the peripherals over before VPX starts,
because all of that lives in the one path rather than in whichever caller remembered it.

## Launcher settings

`/launchers/{id}/config` reads and writes the settings of the program a launcher runs, in
that program's own files. For VPX there is one file per `scope`:

| Scope | File |
|---|---|
| `launcher` | VPX's own settings file, `VPinballX.ini` |
| `folder` | the one named after a game's folder, which every table in it without a file of its own reads |
| `entry` | the table's own, named after its `.vpx` |

`table=` names the table for the last two. A write takes `tables`, a list, in place of `table`; with more than one it returns `202` and a job of kind `table_settings`, the same values written into each table's own file. The job's result lists the ids it `written` and each table it could not write as `{table, reason}` under `failed`; the rest go on past one that fails, and `409` while another such job runs. A list is refused at the `launcher` scope. A launcher for a program whose settings VPinFE
does not read answers with no scopes, and refuses a write.

The settings in `groups` are read from VPX's own file rather than declared here, so a
setting a later VPX adds appears without VPinFE changing. They are named `Section.Key`
(`Player.FXAA`), because a key is only unique inside its section. Where VPX works out what
a blank value means rather than using the default it declares, `default` is empty and
`blank` names what it does instead, in a word that fits the box: every window's size reads
*Auto* and each view mode *The table's own*. `left_empty` says it whole where the word does
not, *From the screen* for a window's size, and is empty otherwise. `named` lists the values a number's app gives a meaning of
their own, as `[value, label]` pairs in its words: Frame Limit's `-1` is *Match the
Display* and its `0` *No Limit*, and any other value is a rate. `reported` lists the
values the program itself last said it used, the most recent first: each window's
`Display` carries the display names from VPX's log, which is how VPX will match one, and
is empty where the log names none. A setting of `type`
`color` is `#RRGGBB` - its value, its fallback and its default - and is written the same
way; VPinFE stores it as the number VPX keeps, red in its low byte.

**The groups are the pages of VPX's own settings menu**: `displays`, `sound`, `graphics`
and `plugins`, then `point_of_view` and `table_options` at a table and `more` for
everything else. The table editor's own settings are left out. Each group's `settings` is
every member, and `curated` names the few a client draws first, under headings:

- a heading has a `key`, a `label` and a `note`, and `keys` in the order drawn. An empty
  `key` draws no heading. `description` is the program's own line about what the heading
  names, beside VPinFE's `note`, and is empty where it has none.
- `enabled_by` names one of `keys`, a switch: while it is off the heading's other rows
  change nothing. Each plugin is a heading switched by its `Enable`, taking its name and
  description from the `plugin.cfg` the installed program has for it, in order of that
  name. A plugin the file names and the program does not have is in `more`.
- `rivals` names other headings' switches, where the heading has one: while its switch
  and a rival are both on, the program uses only one of them. B2S and B2SLegacy are each
  other's. A rival is listed only where it is itself a heading's switch at this scope.
- `pairs` names two of `keys` drawn as one row, each with a `key`, a `label`, a `note`,
  its two `keys` in order and a `joiner`, what stands between the two values. Each VPX
  window pairs its `FSWidth` and `FSHeight` as *Video Mode*, its `WndX` and `WndY` as
  *Position*, joined by a comma, and its `Width` and `Height` as *Size*, the two sizes
  joined by a times sign. The VR preview is a window too, the one heading in `more`,
  which no page draws first. B2S and B2SLegacy pair each DMD box drawn on art the same
  way, `BackglassDMDX` and `Y` as *Backglass DMD Position* and `W` and `H` as *Backglass
  DMD Size*, and `ScoreViewDMDX` to `H` as *Score View DMD Position* and *Score View DMD
  Size*. A pair is listed only where the scope shows both of its keys.
- `switched` names rows of `keys` that change nothing while another of them is off, each
  with its switch as `enabled_by` and those rows as `keys`, or while it is on, where `on`
  is false. Each VPX window's `FSWidth` and `FSHeight` count only while its `FullScreen`
  is on, and its `WndX`, `WndY`, `Width` and `Height` only while it is off. B2S and
  B2SLegacy switch the backglass DMD's `BackglassDMDAutoPos`, `X`, `Y`, `W` and `H` by
  `BackglassDMDOverlay`, the last four off again by `BackglassDMDAutoPos`, and the score
  view's `ScoreViewDMD` rows the same way. One is listed only where the scope shows its
  switch, with the rows it shows.
- `kinds` names the asset kinds the heading's settings are about, empty for most. A file
  of one of them draws the heading too, at the table it is the file for. B2S and
  B2SLegacy are about `backglass`.
- `versions` names rows the heading adds for a file that holds two versions of its content,
  empty for most, and never among `keys`. B2SLegacy's is `Plugin.B2SLegacy.B2SDualMode`,
  offered as Authentic (`1`) and Fantasy (`2`).
- `summarized` is true for a group said as one line rather than listed, the point of view.
  `rows` names the ones it draws as rows of their own all the same: the view mode of each
  view the table starts in, by its `Player.BGSet`, then any other view mode the table sets.
- `read_only` is true for a group listed as the file holds it, each removable and none
  edited. `table_options` is one: the options a table's file holds under `[TableOption]`,
  which the table's script declares while it runs, so VPX's file says nothing of their
  range or meaning. It appears only at a table whose file holds one.

A setting carries `help`, a line of VPinFE's saying what it is for, beside VPX's own
`description`. `per_table` marks one commonly set for one table rather than all of them.
One with `choices` carries `choice_help`, a line saying what a choice does, by its value,
for each that has one.

**A setting is offered at the scopes VPX keeps it at**, listed in its `scopes`. The input,
plunger, nudge and cabinet settings, and the ones VPX's menu writes straight to its own
file, are `launcher` only. The point of view, table options, difficulty, exposure, tone
mapper and scene lighting are `folder` and `entry` only. A scope leaves out a setting it
does not offer unless its file already holds one, and a write of one is refused.

**Every value says which layer answered.** Somebody changing one layer of several has to
see which one is in force:

- `value` is what VPX will use, and `scope` is the layer it came from - empty where nothing
  sets it and VPX's own default wins.
- `set_here` is true where this scope's file names it. `in_effect` is false where it does
  and another layer answers anyway.
- `fallback` and `fallback_scope` are what would answer if this scope stopped naming it,
  both empty where that is VPX's own default. `app_name` is whose default that is.

**The two table files do not stack.** VPX reads a table's own file where there is one and
the folder's where there is not, never both. The folder's file is read and never written:
VPinFE makes no file in a game's folder, and one made manually or by another program shows
as the `folder` scope where it reaches a table. So a table's first value of its own
creates its file with that value alone, and the folder's file stops reaching it. At the
`entry` scope, `from_game` is by key what the folder's file sets that the table's own does
not, where the table has one and the value would change what the table uses. Written to
the `entry` scope, they give the table the folder's values in its own file. It is empty at
any other scope. `follows_game` is the same list for a table with no file of its own, which
the program reads the folder's for: by key what the folder's file sets that would change what
the table uses. It is empty once the table has a file.

`program` is what the installed program is, as far as its settings depend on it, or `null`
where the app does not say: `plugins`, the ids of the plugins it loads (empty for a program
that is known to have none, `null` where they cannot be read), and `defaults`, by key what a setting holds
while the file leaves it blank, for the settings whose default is the build's, and `build`,
the number the app counts builds by (`null` where unknown), and `dmd_in_file`, true where the
build keeps the DMD's place in each backglass file rather than in the settings.

A table whose `.vpx` is named after its folder has one file for both scopes, so what it
sets reaches the game's other tables that have no file of their own. `shared_with_game` is
true for that table.

At a table's scope an empty value removes the setting from that file. So does a value
equal to what the launcher already gives, which VPX would drop itself the next time it
saved, and those are returned under `cleared` rather than `written`. The ones VPX keeps at a
table whatever their value, the windows among them, are written as sent.

The copies `/backups` keeps of a launcher are of the `launcher` scope's file only. A table's
files sit beside its `.vpx` and travel with it.

## Input

`POST /api/v1/input/actions` presses what a cabinet button presses. It's the third way an
action reaches the frontend - the keyboard and the gamepad are the other two, and all
three reach the same dispatch, so a remote isn't a second implementation of navigation.

```
POST /api/v1/input/actions
{"action": "next", "phase": "press", "ttl_ms": 1500, "source": "remote"}
```

`action` is one of the eleven in `common/config/input_registry.py` — `previous`, `next`,
`page_previous`, `page_next`, `select`, `back`, `menu`, `collection_menu`, `tutorial`,
`exit`, `take_picture`. Anything else is a 400 that lists them. `take_picture` does
something only while a table runs, and `back` answers it there; the frontend hears the
two of them during play and nothing else.

`phase` is `tap` (the default), `press` or `release`. **A hold is a press and a release,
not a discrete action on a timer.** Send `press` when the button goes down and `release`
when it comes up; the repeat curve lives in the install, so holding "next" feels the same
whether you held a flipper, a key or a thumb.

**A press expires unless you renew it.** A release can be lost — a mobile device locks its screen,
a network drops — and a lost release is a wheel that spins forever. Send `press` again with
the same action to renew; renewals aren't announced, so one gesture stays one press. `ttl_ms`
is how long a press stands without one (200–10000, default 1500); renew at roughly a third
of it. The response's `holding` lists every action the install currently believes is down,
which is what a client that reconnected mid-gesture needs to see.

It reaches this install's windows and no others. A client aimed at another device calls
that device's copy of this route — the target picker in a client is a base URL, not a new
transport.

The endpoint carries `input:act`, deliberately its own scope: this is the first capability
that lets a network caller act *as the player*, which is neither reading nor launching.

`GET /api/v1/input/play` says what this device can do with keys while a table runs:

```json
{"press": {"available": true, "via": "wtype", "reason": null},
 "hear": {"available": false, "via": "",
          "reason": {"key": "keys.hear.no_input_group", "params": {}, "fix": "user",
                     "remedy": {"key": "keys.hear.join_input_group", "params": {}}}}}
```

`press` is whether VPinFE can press a key into the running table, as Take Picture presses
the table's own Pause: through `wtype` or `ydotool` on Wayland, `pynput` elsewhere, which on
macOS needs Accessibility (Device Control and Data Access on macOS 27), which VPinFE asks
macOS for when it starts. `hear`
is whether it can hear the keyboard from outside the frontend's page, which a key bound to
Take Picture needs during play: `evdev` on Wayland, where the device's user has to be in the
`input` group, and `pynput` elsewhere, which on macOS needs Input Monitoring. Where one
can't, `reason` says why as a catalog key, with a `remedy` where a person can fix it. It
carries `play:read`.

Two producers at once isn't decided. A mobile device and a cabinet flipper holding opposite
directions is last-press-wins because that's what the dispatch already does, not because
anyone chose it. The expiry stops a lost release becoming a runaway; it doesn't arbitrate.

## Frontend

`GET /api/v1/frontend/state` is what this device's frontend is showing:

```
{"running": true, "collection": "Friday Night",
 "game": {"id": "a1b2c3d4e5f6", "name": "Medieval Madness",
          "links": {"self": "/api/v1/games/a1b2c3d4e5f6"}},
 "preview": null}
```

- `running` is whether a frontend window is up. When none is, the rest is empty.
- `collection` is `""` for the whole library, as everywhere a collection leaves core. The
  frontend's own filter menu narrows the whole library without naming a collection, so it
  is `""` then too.
- `game` is the one on the wheel, a reference like the event stream's, and null when
  nothing is: an empty collection, or a wheel that has not reported yet.
- `preview` is the recording the windows show for a decision, below, and null when none is.

The state is what the frontend reports. It holds for any theme that moves its wheel with
core's `TableIndexUpdate`, which every published theme does; a theme that keeps its
position to itself is not reported, and nothing here guesses.

`PUT /api/v1/frontend/collection` with `{"name": ...}` and `PUT /api/v1/frontend/game`
with `{"id": ...}` ask the frontend to switch. Both answer 202 with no body. The windows
apply it the way they apply core's own collection picker, and the result is the next
`frontend.state_changed` - read that rather than assuming the switch happened, since a
theme that handles its own messages may not follow. Both carry `input:act`, because
switching what is on screen is acting as the player; the read carries `play:read`.

`PUT /api/v1/frontend/preview` with `{"proposal": id, "showing": "after"}` shows a
recording waiting for a decision (see Proposals) in place, looping: the window that shows
its kind's screen plays it - the backglass window a backglass video - and the controller
window, which takes the buttons, says which kind it is and which side. `showing` is
`after`, the recording, or `before`, the file serving its slot now; `after` when left out.
`DELETE` ends it and decides nothing. While one is on show the state carries it:

```
"preview": {"proposal": "3f9c0a1b2c4d", "showing": "after", "kind": "backglass_video",
            "table_id": "", "game": {"id": "6f1c9a4e...", "name": "Attack from Mars",
                                     "links": {"self": "/api/v1/games/6f1c9a4e..."}}}
```

At the cabinet left and right show `before` and `after`, select uses the recording and back
throws it away, as `POST /capture/proposals/{id}` does. `preview` then goes back to null, and
the proposal is no longer listed by `GET /capture/proposals`. So does a decision made through that
route anywhere while the proposal is on show. A recording the frontend's own main menu
started, which leaves proposals, shows each of them in turn once it ends.

`game.selected` stays beside it for a different reader. It fires as the wheel stops and is
how in-process handlers - DOF, an extension's data - hear about it. `frontend.state_changed`
carries the whole state, so a client that missed one is right again after the next.

`GET /api/v1/frontend/browser` is what the browser the frontend runs in can play, as the
frontend's controller window found when it last opened with that browser:

```
{"state": "no_h264", "browser": "Chromium 145.0.7632.0",
 "path": "/opt/vpinfe/_internal/chromium/linux/chrome/chrome", "bundled": true,
 "default_path": "/opt/vpinfe/_internal/chromium/linux/chrome/chrome", "default_bundled": true,
 "variant": "full",
 "formats": {"h264": false, "hevc": false, "vp9": true, "av1": true,
             "aac": false, "mp3": true, "vorbis": true, "opus": true},
 "plays": ["VP9", "AV1", "MP3", "Vorbis", "Opus"],
 "does_not_play": ["H.264", "HEVC", "AAC"],
 "reported_at": "2026-09-27T20:14:02Z",
 "finding": "Most shared videos won't play in this browser",
 "fix": {"key": "frontend_browser.fix.install_chrome_linux", "action": "", "chrome_path": ""},
 "fix_text": "Install Google Chrome, and VPinFE uses it from then on"}
```

- `state` is `plays`, `no_h264`, `no_video`, `no_browser`, or `unknown`: nothing has
  reported since this browser was chosen. Unknown is never a no.
- `bundled` is whether the browser in use is the one VPinFE ships; `variant` is whether this
  build ships one at all, `full` or `slim`.
- `default_path` and `default_bundled` are the browser VPinFE finds on its own, which is
  what an empty `chromium.path` stands for. They differ from `path` when `chromium.path` names
  another browser.
- A format is `true`, `false`, or `null` where the probe could not tell. H.264 and VP9 are
  decoded from a real file, not only asked of `canPlayType`, because a file can load with no
  picture.
- `fix.action` is `use_chrome` where Google Chrome is installed and not the browser in use,
  with its path; setting `chromium.path` to it and reopening the frontend's windows is the fix.
  Otherwise the fix is something a person does, and `fix_text` says what.
- The `media_playback` capability is unavailable only where nothing plays or there is no
  browser, with `finding` as its reason.

## Recording

A device records its own screens into its tables' media: a picture and a video of each
window, and the table's sound. Only the device with the screens does it, so everything here
is asked of that device.

`GET /api/v1/capture` is what it can record, worked out when asked:

```
{"observed_at": "2026-09-27T20:14:02Z", "adapter": "wlr",
 "available": true, "reason": null,
 "screens": [
   {"window": "playfield", "output": "DP-1", "size": [1080, 1920], "surface": "portrait",
    "picture": {"available": true, "reason": null},
    "video": {"available": false,
              "reason": {"key": "capture.tool.needed", "params": {"tool": "wf-recorder"},
                         "fix": "user",
                         "remedy": {"key": "tools.hint.linux",
                                    "params": {"tool": "wf-recorder"},
                                    "setting": "tools.wf_recorder_path"}}}},
   {"window": "topper", "output": null, "size": null, "surface": null,
    "picture": {"available": false,
                "reason": {"key": "capture.screen.none", "params": {"window": "topper"},
                           "fix": "none", "remedy": null}},
    "video": {"available": false, "reason": {"key": "capture.screen.none", ...}}}],
 "sound": {"available": false,
           "reason": {"key": "capture.sound.no_server", "params": {}, "fix": "none",
                      "remedy": null}},
 "video_codec": "h264", "at_once": true,
 "tools": [{"id": "ffmpeg", "state": "found", ...}],
 "commands": {"record": "{recorder} -D {input} {hwaccel} -f {output}",
              "encode": "{ffmpeg} -hide_banner ... {input} -map 0:v:0 -an {video_filters} ..."}}
```

- `adapter` is how this session's screens are reached, read off its variables: `wlr` for
  sway and Hyprland, through grim and wf-recorder; FFmpeg's own input everywhere else -
  `x11grab` on X11, `ddagrab` on Windows (`gdigrab` where the FFmpeg has no `ddagrab`) and
  `avfoundation` on macOS. KDE Plasma and GNOME on Wayland are `portal`: the desktop's
  screen-sharing portal, read by GStreamer, one screen after another.
- On macOS every screen is `capture.permission.screen` until macOS lets VPinFE record the
  screen, read without asking, so nothing VPinFE does raises the prompt.
- On KDE Plasma and GNOME every screen is `capture.portal.not_chosen` until someone at the
  device has chosen which screens VPinFE may record. `POST /api/v1/capture/screens/choose`
  raises the desktop's own picker there, as a job, and keeps the answer; its result is
  `kept`, how many of the device's `screens` the desktop `shared`, and a `reason` where
  something is not right. Nothing else raises the picker: a recording restores what was
  chosen, and a screen the desktop did not share fails with `capture.portal.not_shared`. `sound` is
  `capture.sound.needs_loopback` on macOS and `capture.sound.not_yet` on Windows; elsewhere
  it is read from PulseAudio or PipeWire's stand-in for it.
- `screens` has a row per window - `playfield`, `backglass`, `scoreview`, `topper` - and
  the output it is on: the one the default launcher's app names for that window in its own
  settings, matched by the output's name; where the app does not say, the window's
  `screen_id`, by the output's name, then by where it is and its size. `size` is the output
  as the desktop lays it out. A window the app shows on no screen of its own - Visual
  Pinball's with its Output set to Disabled, or embedded in the playfield - is
  `capture.screen.not_shown`, with the app's name in `app`.
- Every `reason` is a catalog key and its values, rendered where it is read. `fix` is
  `user` where a person must act, and then `remedy` says how, in the shape of the Tools'
  remedy; `auto` where VPinFE can do it once a person agrees - a missing FFmpeg on Windows
  and macOS, which `POST /api/v1/config/tools/ffmpeg/get` downloads - with the remedy's
  `get` saying what, and a desktop not yet told which screens VPinFE may record, which
  `POST /api/v1/capture/screens/choose` asks; `none` where nobody can. A remedy that is no
  Tool's has no `setting`. A `window` value is a window's id, said with its media
  kind's word.
- `video_codec` is what Automatic records here: `h264`, unless this device's frontend
  browser reports it plays no H.264, then `vp9` in `.mp4`.
- `at_once` is whether every screen records in the same moment, which takes a hardware
  encoder; without one they record one after another in the same launch.
- The `capture` capability is this report reduced to one answer, whether anything can be
  recorded, with the first reason nothing can, remedy included.
- `commands` are VPinFE's own Record and Encode Commands on this device, their tokens
  unexpanded; `record` is empty where it has none.

### The Record and Encode Commands

`capture.record_command` records one screen, once per screen; `capture.encode_command`
turns one recording into the stored file. Empty is VPinFE's own. Each is one command line,
split the way a shell splits it and nothing else a shell does, and each argument is filled
in on its own, so a path with a space in it stays one argument.

| Token | Record | Encode |
|---|---|---|
| `{ffmpeg}` | the FFmpeg the Tools found | the same |
| `{recorder}` | the program that records a screen: wf-recorder on wlroots, GStreamer's `gst-launch-1.0` on KDE Plasma and GNOME, FFmpeg elsewhere | - |
| `{input}` | the screen's capture input, at its refresh and without the pointer: `-o DP-1` on wlroots, `-f lavfi -i ddagrab=output_idx=1:framerate=60:draw_mouse=0,hwdownload,format=bgra` (without the download where the hardware encoder takes the frames on the graphics card), `-f gdigrab -framerate 60 -draw_mouse 0 -offset_x 1080 -offset_y 0 -video_size 1920x1080 -i desktop`, `-f x11grab -framerate 60 -draw_mouse 0 -video_size 1920x1080 -i :0.0+1080,0`, `-f avfoundation -framerate 60 -capture_cursor 0 -i "Capture screen 1:none"`, `pipewiresrc fd=21 path=64 do-timestamp=true keepalive-time=1000` (the portal's stream through a PipeWire remote of the recorder's own, the last frame sent again each second of a still screen) | the recording, cut to the moment every screen shares: `-ss 0.412 -t 20.000 -i <file>` |
| `{output}` | the file to write | the same |
| `{window}` | `playfield`, `backglass`, `scoreview` or `topper` | the same |
| `{screen}` | the output's name, `DP-1` | the same |
| `{monitor_index}` | the capture API's own number for the screen: the portal's PipeWire node on KDE Plasma and GNOME | - |
| `{x}`, `{y}` | where the screen is on the desktop | - |
| `{width}`, `{height}` | the screen's size | the same |
| `{duration}` | Length, in seconds | the same |
| `{fps}` | the screen's refresh | Frame Rate |
| `{hwaccel}` | the hardware encoder's arguments, or nothing: VA-API on Linux, NVENC or AMF on Windows, VideoToolbox on macOS, each proved by encoding one frame | - |
| `{audio_device}` | the sound source | - |
| `{video_filters}` | - | `-vf` and VPinFE's filters: the turn to the stored orientation, the constant rate and the size |
| `{video_codec}` | - | VPinFE's arguments for the Video Format and Quality |

- `{input}`, `{video_filters}`, `{video_codec}` and `{hwaccel}` are several arguments, so each
  is an argument on its own. Tokens are `{name}`, as in a launcher's arguments and the
  table commands, so an FFmpeg filter label such as `[v]` is written as it is.
- A Record Command needs `{output}`; an Encode Command needs `{input}` and `{output}`.
- `PUT /api/v1/config` refuses a command with a name no token has, a token from the other
  command, a list token sharing an argument or a quotation mark never closed, as a `400`
  naming the command and the problem; so does a run whose `settings` carry one. A command
  in the settings file that cannot run is set aside, and VPinFE's own runs.
- VPinFE still keeps time: every recorder is stopped by the wall clock after Length and its
  frames counted, whatever the command says. It is stopped as Ctrl-C stops it, and on
  Windows, which has no such signal for a program VPinFE starts, by `q` on its standard
  input, as FFmpeg reads it.
- Where the Encode Command is a person's own, the pictures are cut from what it wrote, so its
  geometry and size carry through.

`POST /api/v1/capture/test` records 3 s of the playfield with the commands, launching
nothing, and encodes it. The body is `{"settings": {...}}`, Recording settings in place of
this device's as a run takes them. It answers when done:

```
{"ok": true, "step": null, "reason": null, "detail": "",
 "size": [1920, 1080], "fps": 30.0, "frames": 90, "seconds": 3.0,
 "picture": "data:image/jpeg;base64,...",
 "record": "/usr/bin/wf-recorder -D -o DP-1 ...", "encode": "/usr/bin/ffmpeg ..."}
```

`size`, `fps` and `frames` are the stored file's, and `picture` a small JPEG of it that
shows which way up the playfield came out. A command that records or writes nothing is an
answer, not an error: `ok` false, `step` `record` or `encode`, a `reason` key, and in
`detail` the last thing the program said. A device that records nothing answers `501`, and a
table or a recording already running `409`.

`POST /api/v1/capture/runs` records games, `{"games": [id, ...]}`, and tables,
`{"tables": [{"game": id, "table": table_id}, ...]}`, the games first and each in the
order named, and answers `202` with the job (`Location` is where to watch it).
`POST /api/v1/capture/plan` takes the same body and does nothing:

```
{"games": ["6f1c9a4e..."], "kinds": ["playfield", "playfield_video"],
 "existing": "replace_downloaded", "settings": {"length": 25},
 "confirmed": {"count": 1}}
```

- `kinds` are media kinds: a window's picture (`playfield`, `backglass`, `scoreview`,
  `topper`), its video (`playfield_video` and the rest) and `audio`. Left out, it is every
  kind this device can record, with `audio` where `sound` - or where that is left out too,
  the Sound setting - says so.
- `existing` is what happens to a slot that already has a file: `fill` (the default)
  records only what nothing serves; `replace_downloaded` also replaces a catalog's files;
  `replace_all` replaces whatever is there; `choose` records it and keeps the recording as
  a proposal for a person to decide on. An empty slot is filled whichever is asked.
  `review: true` proposes every recording, an empty slot's too.
- A replaced file is copied to Backups (reason `replace`) and deleted once its recording is
  placed: the file that served the slot, a catalog's fixed name such as `medias/table.mp4`
  under a game's recording included. Where the backups folder cannot be written to, that
  kind is not placed and the file stays. At a table's tier the file the game's tables
  share goes only where this table is the one it serves; one that other tables use stays.
- A run that deletes anything carries `confirmed.count` equal to the plan's `replacing`,
  and is refused with `details.replacing` otherwise - the confirm a person sees, by count.
- `settings` are Recording settings for this run only: `length`, `wait`, `picture_at`,
  `fps`, `size`, `video_codec`, `playfield_orientation`, `quality`, `sound_source`,
  `record_command`, `encode_command`.
- A game's slot is the file its tables share, and the recording is saved there; a table's
  slot is whatever serves it - its own file, else the shared one - and the recording is
  saved as the table's own.
- A game or table named twice is recorded once.

The plan has an entry in `targets` for each game or table: per kind what it `does`
(`fill`, `replace`, `propose` or `leave`), the `source` of the file serving the slot
(`vpinmediadb`, `user`, `capture`, `unknown`) and its `file`, whether that file `goes` once
a recording is placed, and a `reason` where this device cannot record the kind - on a
device that records nothing, the report's own reason for every kind named, and
`capture.screen.not_shown_last` for a window the table's last recording found the app did
not show (see below); then its `recording`, `replacing` (the files it deletes),
`replacing_by_source` and `estimate_seconds`. Over all of them it answers `games`, `kinds`
as each kind's `reason` (the report's, or the targets' own where none of them can record
it) and how many of the slots that can be recorded are `missing` a file and `have` one,
`recording`, `fills` (slots filled without deleting anything), `asks` (recordings kept for
a decision), `replacing`, `replacing_by_source`, `launches` and an `estimate_seconds` that
is always approximate.

A run is one job, recording each game in turn, and is refused where the disks it writes to
would be left with under 1 GB: the recordings of one game while it is encoded, and every
file still to come. Each game is planned again when its turn comes, so a slot filled since
the run started is not recorded twice, and a game with nothing left to record is skipped
without launching. The job's message says which game, *Recording "Attack from Mars" - 3 of
24*, and its resource carries `stoppable: true` with `links.stop`.

The job's `result` has an entry in `tables` for each game done: `state` is `recorded`,
`failed`, `closed` (the table was closed at the cabinet before its recording finished, and
nothing was placed) or `skipped` (nothing asked for needed recording, with the `reason` of
the first kind asked for that this device cannot record for it where there is one), with
the kinds `placed`, those `proposed` with their proposal's `id`, those `failed` with a
`reason`, and `at_once`, false where the screens could not keep up together and were
recorded again one at a time. `run` says how the job ended: `state` `done`, `paused` with
its `reason`, or `stopped`, and `done` of `of`.

The run is written to the device's data directory as it goes, and
`GET /api/v1/capture/runs/current` answers it:

```
{"run": {"id": "b41c07aa19e2", "state": "paused",
         "reason": {"key": "capture.outcome.closed", "params": {}},
         "job_id": null, "done": 6, "of": 24,
         "game": {"id": "6f1c9a4e...", "table_id": "", "name": "Attack from Mars"},
         "existing": "fill", "recorded": 5, "failed": 1, "closed": 0, "skipped": 0,
         "proposed": 0, "estimate_seconds": 1020}}
```

- `game` is the one being recorded, or the one it goes on with; `done` of `of` are behind
  it. The counts are games by outcome, and `proposed` the recordings kept for a decision.
  `estimate_seconds` is what is left, scaled by how the games so far compared with theirs.
- A run of more than one pauses rather than carrying on where a table is closed at the
  cabinet (`capture.outcome.closed`), where a table was launched by anyone else
  (`capture.run.launched`), and where a disk would be left with under 1 GB
  (`capture.run.space`). The current game is recorded again on Resume. A run of one ends
  there instead, as its `closed` outcome.
- A person's Pause has no `reason`. A run VPinFE was closed during reads `paused` with
  `capture.run.interrupted`, and never resumes on its own.
- Stop and Pause close the table being recorded and keep nothing of it, and a recording
  being encoded is discarded. What earlier games placed, and the proposals they kept,
  stay.

### Proposals

A proposal is a recording kept on the device, with the slot it is for, until a person uses it
or throws it away. Nothing in the library changes until it is used.

`GET /api/v1/capture/proposals` answers every one waiting, oldest first:

```
{"count": 1, "bytes": 1843200,
 "proposals": [{"id": "3f9c0a1b2c4d", "game_id": "6f1c9a4e...", "table_id": "",
                "kind": "playfield_video", "name": "Attack from Mars",
                "file": "playfield_video.mp4", "created": "2026-09-28T02:10:44Z",
                "size": 1843200,
                "replaces": {"path": "medias/table.mp4", "source": "vpinmediadb",
                             "goes": true},
                "url": "/api/v1/capture/proposals/3f9c0a1b2c4d/file"}]}
```

`replaces` is the file serving the slot now, read when asked, and null where the slot is
empty; `url` plays the recording. `POST /api/v1/capture/proposals/{id}` with
`{"use": true}` places it at its tier with the origin `capture`, copies the file it
replaces to Backups first (reason `replace`) and deletes it where `goes` says so, answering
`{"placed": kind, "removed": [path]}`; where the backups folder cannot be written to the use
is refused with `409` and the proposal stays waiting;
`{"use": false}` throws it away. `DELETE /api/v1/capture/proposals` throws every one away,
answering `{"discarded": count}`.

A run places each window by the table's own launcher's app. Once the table is up, at the end
of Wait, it asks the desktop where that app's windows are, where the desktop can say (sway
and Hyprland can) and shows the app's playfield window, and records the outputs it names. A
window the desktop shows on none of them is not recorded, and its kinds fail with
`capture.screen.not_shown`. A screen whose recording or picture is one flat color throughout,
as a desktop's background is where nothing was drawn, is not placed either, and its kinds
fail with `capture.outcome.one_color`: the net where the desktop cannot say.

A window the desktop shows on none of them is remembered for that table, in the device's
data directory, against what the app's capture hook says decides its windows there - for
Visual Pinball, its Settings File's contents, the program, the table file, and the `.ini`,
`.directb2s`, `.scv` and `.nix` beside it by the table's name or its folder's. Until any of that
changes, or a later recording of the table sees the window, a plan leaves the window's
kinds for that table with `capture.screen.not_shown_last`, *Visual Pinball X showed no DMD
window when last recorded*. It is per table because Visual Pinball shows a Backglass, Score
View or Topper window only once something draws in it, which depends on the table.

A recording launches the table with `source: "capture"`, which counts no play (see
`docs/extensions/reference/offers.md`), and each file it places is recorded in the `.info` ledger with the
origin `capture` and no hash, so no catalog update ever takes it for its own.

## Event stream

`GET /api/v1/events` is the internal bus (`common/events.py`) on the wire, as Server-Sent
Events. Anything that wants to know what's happening subscribes instead of polling for it.

```js
const stream = new EventSource("/api/v1/events?events=play.state_changed");
stream.addEventListener("play.state_changed", (message) => {
  const { launching, game_name } = JSON.parse(message.data).state;
});
```

Each event is a named SSE frame whose `data` is the payload; the `event` field is the bus
event name, so a client listens for what it cares about rather than filtering a stream of
generic messages. `?events=` is a comma-separated filter — leave it off for everything. An
unknown name is an `invalid_request`, not a silently empty stream.

What's on it, each alongside the `install_id` described below:

| Event | Payload |
|-------|---------|
| `table.launching` / `table.exited` | `{"game": {"id", "name", "links"}, "table": {"id"}, "source"}` — which game, which of its tables launched, and who started it, as `play.state_changed` says. `table` is null when the launch didn't come from the wheel, and the whole payload is `{"game": null}` when there is no game at all |
| `table.launched` | The same, and `up`: who the game counts for, taken as it starts, each `{"id", "name", "initials", "owner", "guest"}`. Empty for a recording, which counts for nobody. `private`: whether the game is Private |
| `table.play_recorded` | The same as `table.launched`, once the game has ended and its play is written, with `private` read again then, and `seconds` played; `reading`, the machine's high score table as read after the game, or null; `new_entries`, `[{"player", "entries"}]` for each player with an entry new this game - see [Players](#players) |
| `game.selected` | `{"game": {"id", "name", "links"}, "table": null}` — the wheel stops on a game, so there is no table to name |
| `game.changed` | `{"game": {"id", "name", "links"}}` — a game's metadata was rewritten, so anything holding it is stale |
| `collections.changed` | `{}` — the collections were edited, or a read of a Community list moved a ranked order; re-read them |
| `play.state_changed` | `{"state": {"launching", "game_name", "source", "paused", "game_id", "started_at"}}` — `paused` while the running table has paused itself, as Take Picture pauses it. `game_id` is the game's library id and `started_at` the UTC time the launch began; both are null while nothing runs |
| `frontend.state_changed` | `{"state": {"running", "collection", "game", "preview"}}`, `game` a reference like the others or null, `preview` the recording shown for a decision or null. The same as `GET /frontend/state` |
| `players.changed` | `{"state": {"players": [...]}}` - somebody joined, left, was renamed or went up. The whole list, the same as `GET /players` |
| `job.progress` | `{"job_id", "pct", "message"}` |
| `job.done` | `{"job_id"}` |
| `job.failed` | `{"job_id", "error"}` |
| `capture.run_changed` | `{"run": ...}` - a recording run started, moved on to its next game, paused, resumed or ended; the same as `GET /capture/runs/current`, `null` once there is none |
| `config.waiting_changed` | `{"waiting": [...], "reopen", "http_port"}` - what is saved and not yet in effect changed. The whole answer, the same as `GET /config/waiting`, so a client that missed one is right after the next |

`game.changed` and `collections.changed` carry no path. The bus does, for handlers in
this process, but a subscriber on another device has a different path for the same thing
and would be told one true only where it happened. They say a re-read is due, not where
from.

Every payload also carries `install_id` — which install the event happened on. On a
single-device install it is always the same value and can be ignored; a client watching
more than one is how it tells them apart. It is absent, not empty, when the install has no
id yet, so "didn't say" is distinguishable from an id that is the empty string.

What is deliberately *not* there is which surface asked. That names one user's browser tab
and means nothing to anyone else on the stream. Which install something happened on is the
provenance a subscriber can act on; who asked is private.

A game on the stream is a *reference*, not a resource: an id, a name to show, and
`links.self` to fetch the rest. Events stay small and there is one answer to what a game
looks like, at `GET /api/v1/games/{id}`. A game that hasn't been assigned an id yet carries
an empty one and no link rather than a broken one.

The bus carries more than that per event — `table.launching` hands its handlers the whole
`Game` object and the ini config, because its handlers are in-process. The stream projects
each event into the shape above instead of forwarding what was published, which is what makes
the wire shape a contract rather than a consequence: adding a keyword argument at a publisher
doesn't change what subscribers receive, and nothing internal leaks onto a socket. Streaming a
new event means adding it to `STREAMED_EVENTS` with the shape it takes.

The stream is always a subscriber and never a hook. A hook can stop the operation it's part
of, and nothing on the far end of a socket may do that.

### Connecting, and reconnecting

`play.state_changed` carries a `source` of `frontend`, `remote`, `api` or `capture` — who asked
for the launch, `capture` being a launch to record the table's media. The frontend uses it to
ignore its own; everything else can treat the state as a fact about the device regardless of
who caused it. A recording is not a play, and no `table.play_recorded` follows one.

On connect the stream sends a `stream.hello` frame, then the current value of any
state-carrying event it's declared for — today `play.state_changed`,
`frontend.state_changed`, `players.changed` and `capture.run_changed`. So a client that
connects mid-launch knows it, without a separate call to `/play/state` and without waiting
for the launch to end. An event whose payload doesn't describe the whole state has no
snapshot; there's nothing honest to send.

Every real event carries an `id`, and browsers replay it as `Last-Event-ID` when they
reconnect. Events since that point are replayed from a bounded buffer, and `stream.hello`
reports whether that worked:

```
event: stream.hello
data: {"seq": 118, "resumed": false}
```

`resumed: false` means the gap was too long, or the instance restarted — treat what you hold
as stale and resync. A resumed client isn't sent snapshots, because it has them already. That
distinction is why the buffer exists: `play.state_changed` carries the whole state and
corrects itself on the next event, but `job.done` doesn't happen twice.

A client is a bounded queue. One that falls further behind than the queue holds is sent what
was already queued and then disconnected, rather than being allowed to grow memory or slow a
publisher down — the bus runs its handlers on the thread that published, and a launch is one
of the things publishing. EventSource reconnects on its own, so a client that briefly stalls
recovers by itself.

## Collections

**Not two kinds.** One collection may carry criteria, hand-picked members and exclusions
at once, and they combine: criteria select, members override what the criteria said for
that game, exclusions remove. `type` is **derived** — `filter` where the collection has
criteria, `manual` where it does not — so adding a rule to a list, or keeping a rule's
result, changes the kind by changing the collection rather than by being told.

Membership edits work on any collection. Adding a game that is already a member is a
success: the caller wanted it in there, and it is.

A member **names a game, or it names a table**. Without a table it resolves to whichever
table is the game's default, so the collection follows a replacement; with one it holds
to exactly that table. The same game may appear once per table named. Exclusions take the
same shape with the opposite sign.

Three lenses, and they answer different questions:

| Route | Answers |
|---|---|
| `/collections/{name}/members` | what is **stored**, and why each entry is there — including one naming a game this library no longer has |
| `/collections/{name}/games` | what it **resolves to**, by game; a game with nothing launchable still appears |
| `/collections/{name}/entries` | what it resolves to, by table — the play lens, what a frontend shows |

`/members` lists its rows in the order the collection would hand them out with its limit
lifted, so the rows that are shown read in the same order as `/entries`, and a row past the
limit keeps its place among them. A row that produces no entry at all - a game this library
no longer has, a hidden table - follows them in the order it is stored, and what was taken
out comes last. A collection arranged manually is listed exactly as stored, which is the
order it is handed out in.

A member naming something that no longer exists is reported by the first and absent from the other
two. Nothing prunes it: a library on a share that was not mounted at scan time reports
every game missing, and cleaning up on that signal would empty every collection.

A limit cuts the play lens, not `/members`: a row the criteria find past the limit is still
listed there, with `past_limit: true`, so an editor can show what the limit keeps off the
frontend. `playable` counts only the rows a frontend shows. A row a rule matched by one
table's own values names that table in `ref_table`, with the table's `origin` `matched`.

`game_count` counts the stored members, which is not the size of the collection - criteria
contribute rows that are stored nowhere. The size is `count`, what it resolves to now, and
`before_limit` is what it would resolve to with its `limit` set aside: the same as `count`
unless the limit cuts. `missing` counts the stored members naming a game this library no
longer has, or a table its game no longer has - the rows `/members` reports as missing.
`hidden` counts the stored members whose table is hidden, the rows whose table `/members`
reports with `origin` `hidden`. A frontend leaves those out, and a member locked to that
table does not fall back to the game's default.

`game_wheels` is up to four wheel URLs of the games it hands out, in its own order and
within its limit, skipping a game with no wheel - each one this API's own
`/games/{id}[/tables/{table}]/media/wheel`. It is what to draw for a collection with no
`image` of its own; the Manager draws them as a two-by-two.

`image_version` is the `v` for `/collections/{name}/image`, and null when it has none.

`added`, `matched` and `excluded` count games before the limit: written into it, matched
by its rule, and taken out whole by name. `count` is what it hands out, which is tables.

Membership is the game's own id, not its VPS id — a game with no VPSdb match still
belongs to collections, which is why membership moved off the VPS id. The key on disk is
still `vpsids` for files written before that migration, and `type` is still `vpsid` there;
the wire uses the honest names.

**Criteria may hold several values**, and several values on one axis is an OR across them.
Send a list; you always get a list back. `GET /library/filters` says which axes take more
than one — `many: true` — along with the values this library has, so a client can render
the right control without holding a list of axis names itself. Across axes the criteria
are an AND, and there is no ordering between them: the resolver is a conjunction, so their
order cannot change the result.

`POST /library/preview` resolves a criteria block against the library and stores nothing,
which is what an unsaved rule is — `builtin:all` plus those criteria. It is how a rule can
be built while its result is on screen instead of every experiment landing on the frontend.

Every collection has an order, whichever kind it is: `order_by` names the field and
`direction` says which way. `PATCH` sets both, and only what you send is written, so a
direction on its own leaves the field alone. `order_by: "manual"` means the stored member
array is the order — which only a manual collection has, so asking for it on a filter
collection, or in the same request as criteria, is a `409`. `PUT .../order` sends the whole
arrangement at once and records `manual` as a side effect, because storing an arrangement
nothing follows would be a write you cannot see.

A `filters` block may carry `order_by` and `direction` as well, and it writes only what it
names: a block with neither leaves the order, its paging and the cap as they were, so
saving a rule changes the rule and nothing else. The one exception is a collection ordered
`manual`, which no collection with criteria can follow: the block moves it to `title`, in
the direction the block names or else `asc`. Its members keep their stored order, so
clearing the criteria and asking for `manual` again restores the arrangement. The cap
changes only through `limit` and `clear_limit`.

A ranked view of a Community list is an order too, `order_by` being
`{extension}/{list}/{view}`: `vpinplay/tables/rating`. The view says which way each of its
fields runs, so the `direction` stored with it is always `asc`, and asking for `desc` beside
it, or on its own while it is stored, is a `400`. The games the view ranks come first in its
order and the rest follow in title order, so a limit fills from ranked games first. Setting
or creating a collection with an order nothing offers is a `400` whose `details.choices`
names the ones that are: the built-in orders, each ranked view of a running extension, and
`manual`. A collection already in the order of an extension that has stopped keeps it, and
resolves in title order until the extension runs again.

`ranking` says which view orders a collection, and is null in any other order: `extension`
and its `display_name`, `list` and `view`, the `title` and `name` the Community page shows
them by, and `read_at`, when that list was last read well. `offered` is false while no
running extension offers the view, and then only `extension` and `display_name` are known.

A cap is `limit`, and lifting one needs `clear_limit: true` rather than a null: absent and
null are the same thing over JSON, so there would otherwise be no way to say it.

Taking the criteria away is `clear_filters: true`, for the same reason a cap needs
`clear_limit`. It keeps the games named manually and drops the exclusions with the criteria,
since they only said what to leave out of what the criteria found. Sending it beside
`filters` is refused.

`in_frontend` is false on a collection kept out of the frontend: its collection menu and
core's collection picker leave it out, while this API and the Manager still list it. PATCH it
with `in_frontend`; `true` removes the key rather than storing it. The collection the
frontend is showing stays in its menu even when it is off, so a setting that opens on it
still does.

Collection names are the identity, so they are URL-encoded in paths (`Last%20Played`).
`Last Played` itself is a filter collection over the games with a play on record, ordered
by date and capped at 30 — it derives from what each `.info` says, so nothing maintains it
and it never holds a game that is no longer installed.

`GET /library/entries` is the same play lens over everything, which is what a frontend
shows before a collection is chosen. The whole library is a collection internally, and one
that is synthesized rather than stored, so nothing is added to your `collections.json` to
serve it. It keeps its own route because that is the path a client would guess. Both
routes resolve the same way, so they cannot disagree about what a collection holds or what
an entry looks like.

`GET /library/filters` says what a filter collection can filter on: every axis with its
`scope`, `kind`, a one-line `summary`, and the `values` this library actually holds — so a
client offers a manufacturer somebody owns rather than every manufacturer that ever
existed. The axes are projected from the registry the resolver matches on, so the two
cannot disagree, and an axis added there appears here without a second edit. Beside
`values`, `counts` says how many games hold each one, a game counted once for each value it
holds, so a picker can say what a choice matches before it is made.

A range of years is its own axis, `year_range`: `{"from": 1990, "to": 1999}` asks for 1990
to 1999, both ends included, and either end may be left out - `{"to": 1979}` is anything
up to 1979. `year` keeps its meaning, the years it names. An axis may name another as its
`field`, which says a client should ask the two as one field: `year_range` names `year`,
and `rating_or_higher` names `rating`.

`tags` takes a list, like the other many-valued axes, and `favorite` is true, false or
absent, like `played`. The `tags` axis is table-scoped: a rule on it matches a game carrying
the tag, which matches its default table, and a table carrying it, which matches that
table.

*Is none of* is an axis for each choice field, named for it and naming it as its `field`:
`theme_none_of`, `game_type_none_of`, `manufacturer_none_of`, `year_none_of` and
`tags_none_of`. Each takes a list, as its field does, and leaves out a game holding any
value in it; a game with no value on the field holds none of them, so it stays. Their
`kind` is `none_of`, and they carry no values of their own, since the field's are the ones
to offer. `tags_none_of` is table-scoped like `tags`: a game where neither it nor any of its
tables carries one matches its default table, and otherwise each table where neither it
nor its game carries one matches on its own.

The `rating` axis carries its scale, `["1", "2", "3", "4", "5"]`, on every install rather
than the ratings a library happens to hold: enumerating the ratings in use would offer a
different scale to two libraries, and a shrinking one as ratings change. A rule asks for
one exactly, or with `rating_or_higher: true` for that many stars or more.
`rating_or_higher` carries no values of its own.

## Devices

An install records which devices it knows, keyed by `device_id`. That is the whole of it -
there is no routing a launch to a chosen device, no aggregating their state and no
conflict resolution, because each of those needs a decision *across* devices that has not
been made.

What it buys today is attribution. Every event carries the `install_id` it happened on,
and for a VPinFE install that is the same value as its `device_id` - so the registry turns
that id into a name a person recognizes.

`device_id` rather than `install_id` because not every device is an install. A mobile device
running VPX Mobile has no install id to offer, so one is minted for it. The address is
never the key: it is the property most likely to change, and a device on DHCP would
otherwise become a new device every time its lease turned over.

`kind` says what a device is, from a closed set: `vpinfe` or `vpx_mobile`. Anything else
is a `422`, because a consumer switches on this to decide what it can ask of an entry.
Omitted, it is `vpinfe`.

A device announces itself with `PUT /devices` on startup, sending its `device_id`,
`display_name` and `features`. Announcing twice is one device heard from twice: `first_seen`
survives, everything else is refreshed, because the install owns those and the registry is
a copy that goes stale by design.

**The address is observed, not claimed** - for a device announcing itself. It is read
it off the socket and ignores any the body carries: a device behind a router does not know
how it is reached, and a caller that could name its own address could name someone else's.

A `vpx_mobile` device is the other case. The mobile device is not the caller - a person lists it
at `/config/mobile_devices` with a `name`, an `address` and a `port` (2112 unless given),
and the install mints its id from the same generator install ids use. That is what lets
several mobile devices coexist: each has an id of its own rather than one derived from an address
they will both change. `/devices` shows it joined with what the install has learned of it:
`first_seen`, `last_seen`, `last_reachable` and `browser`, empty until something answers.

An install records itself at startup, so it appears in its own registry like anything
else.

Probing a device that answers and runs the frontend also asks it `GET /frontend/browser`,
and its entry keeps what it said as `browser`: `state`, one of that route's; `name`, the
browser and its version; `formats`, that route's true, false or null for each format; and
`checked_at`. It is the last known answer, so a device that is
switched off still has one. A device that no longer runs the frontend has it cleared, and one
too old to serve the route, or that fails to answer it, keeps what was held. The probe's
answer carries the same field. An install's own entry is written by its frontend as it
reports, rather than by probing itself.

An install upgrading from 2.x has its `[vpxmobile]` address and port copied once, as
an item of `mobile_devices` named `VPX Mobile`. The keys are removed in the conversion and
nothing reads them afterwards.

An install that cannot be recorded starts anyway. Being listed costs a label, not a
capability.

### Is the library really shared?

Two installs sharing a library are usually looking at the same files over a network share, and
nothing checked that. A `game_root_dir` that is wrong or unmounted fails one game at a
time, at launch, as a file-not-found.

Set `network.verify_shared_library` and an install compares its own tables against the library's
at startup, by `table.file_hash` rather than by path — the same share is mounted at
different places on different devices, so paths would report every install as broken. It
logs what it found and changes nothing else: what to do about a mismatch is a decision
nobody has made, and an unmounted share should not become fatal on a device that was
working a moment ago.

Three outcomes, because they are three different problems: `missing` (the library has a table
this device cannot resolve), `differs` (both have it, the bytes are not the same), and
`unverifiable` (the library has not hashed it, so it says nothing either way). Nothing
verifiable is not a pass.

## Players

Who plays on this install, and who the next game counts for. Each install has its own,
and one player on it is the **owner**, made on the first start and never removable. Kept
players are written to `players.json` in the config directory. A **guest** is held in
memory and is discarded when VPinFE closes.

```json
{"players": [
  {"id": "Pq7Lm2Xa9B", "name": "", "initials": "OWN", "owner": true, "guest": false,
   "up": false, "shares_initials_with": [], "picture_version": "1b7f3c9a2e"},
  {"id": "Hc4Rt8Wv1N", "name": "", "initials": "ABC", "owner": false, "guest": true,
   "up": true, "shares_initials_with": [], "picture_version": null}
]}
```

**Up is who the next game counts for**, and it is never empty: with nobody else up, the
owner is. Several can be up at once, for a game two people share. A guest joining is put up
alone, so a visitor's game is never counted for the owner by default. Whoever leaves is
taken out of up, and the others up beside them stay up; the owner goes back up only when
nobody would be left. Who is up is held in memory and starts as the owner.

**Initials are how a score finds its player.** They are three characters, or none on a
kept player, held upper case and compared ignoring case and the spaces around them. Two
kept players with the same initials are refused, naming who has them. Initials a player
already holds stay as they are when left unchanged, even shorter ones carried over from 2.x. A
guest whose initials a kept player has joins anyway, and each lists the other under
`shares_initials_with`: while they share them, a score carrying those initials goes to
neither.

**A game counts for who was up as it started, and a new score for whose initials it has.**
The high score table is read before the launch and again after the game. An entry is new
when the second reading holds it more times than the first, whatever its rank or section,
and a game with no reading before it has nothing new. A new entry goes to the player whose
initials it carries, up or not. One with no initials, or `???`, goes to the one player up,
and with several up to nobody. A machine that keeps a single number has a new entry when
the number changed, and it goes the same way. `table.play_recorded` says who each one went
to.

The library's own counts - how often a game was played, for how long, and its score - are
the owner's. They move only for a game the owner was up for. Every other player's are in a
record of their own - see [Records](#records).

**A picture is a circle's worth of a player.** `picture_version` is the `v` for
`/players/{id}/picture`, and null while they have none. It changes when the picture does,
so an address carrying it can be kept for good. A guest has none, because a guest leaves
when they sign out; removing a player deletes theirs. Themes are not given the version but
an address - see `vpin.players` in the theme guide.

A refusal - removing the owner, initials taken or not three characters, a guest with none -
is `invalid_request` with the reason as its message. An id nobody has is `not_found`.

`players:read` and `players:write` guard them. Every change is announced as
`players.changed`, carrying the whole list.

### Records

Every player but the owner has a record of their own: per game, how often they played it,
for how long, when they last did, their best entry on its high score table, and their
rating. A kept player's is written to `player_records/<id>.json` in the config directory;
a guest's is held in memory and is discarded when they sign out or VPinFE closes. Removing a
player removes their record.

```json
{"player": "Hc4Rt8Wv1N", "games": [
  {"game_id": "Tq3Nx8Kd2P", "play_count": 2, "play_time_seconds": 1520,
   "last_played": "2026-09-28T20:14:02Z", "rating": 4,
   "best_score": {"rom": "example", "section": "HIGH SCORES",
                  "scored_at": "2026-09-28T20:39:11Z", "rank": 2, "initials": "ABC",
                  "score": 12500000, "prefix": "", "suffix": "", "text": "12,500,000"}}
]}
```

A game counts for each player up as it starts, when the library's count moves for the
owner, and its time is added as it ends. A new entry goes to the best score of the player
it went to, up or not. The best is the entry with the highest number, and a later equal one
leaves it be; `best_score` is shaped as a high score entry, with the ROM whose table it was
on and when it was made. Games are listed most recently played first, then the ones only
rated.

Both routes refuse the owner with `invalid_request`: the owner's record is the library's,
read from a game's `user`, and their rating is written by `PUT /games/{id}/rating`.

### Accounts and cards

A player can hold an account with an extension that offers one - a user id at a scores
site, say, chosen free and claimed deliberately. The account is the extension's: what can
be done with it comes from the extension, asked with the player's id, and it keeps the
values. A guest's are held in memory and discarded when they are.

```json
{"accounts": [
  {"extension": "scores_site", "label": "Scores Site", "share": false,
   "share_help": "Posts your scores to Scores Site",
   "consent": ["their id and initials", "the games they play from now on"],
   "check": "/available", "user_id": "jordan", "claimed": true,
   "page": "https://scores.example/players/jordan",
   "status": "Sent 2 minutes ago", "waiting": false, "waiting_count": 0,
   "acts": [{"key": "send_now", "label": "Send Now", "description": ""}],
   "fields": [], "card": true, "reads_cards": true, "picture": true, "error": ""}
]}
```

`claimed` says whether `user_id` is registered with the service - free to change or drop
with a plain `PUT` until then, read-only after. `page` is the account's public page once
claimed, `""` before. `check` is where a candidate id is asked about while it is being
chosen, not under this player: `GET /ext/{extension}{check}?candidate=…`, straight through
to the extension the way its settings are. `consent` is what Share tells the player
becomes public, asked once, the first time it is turned on. `acts` is empty until claimed;
`send_now` is the one this shape offers, only while something waits. `picture` says the
service has a picture for this player, which `POST /players/{id}/accounts/{extension}/picture`
copies once as an upload of it would be kept. `fields` is unused by
this shape - a `secret` field of an extension whose account is settings-like instead is
accepted on a write and read back as `set`, true or false, by this route and by every
route an extension serves, and never answered.

**Share** is whether the account sends what this install records, and it is core's, kept
per player per extension in `players.json`. It is off until someone turns it on, except for
a guest who joined with a card, whose account with that card's extension shares.
`share_help` is what the extension says Share makes of its account, empty where it says
nothing.

`error` says why an extension could not answer for its account; the rest is then empty,
and the other accounts are listed as usual.

A **card** is a player's account as a file they can carry: a QR code of the account's text,
drawn as an SVG with the text hidden inside it as well - in a comment, a `<metadata>` and a
`<desc>`. A card is read from that text and never from the picture, so a card file or its
text on its own is what `POST /players/guests/card` takes. The extension that reads that
kind of card says who it is for, and the guest keeps the card's initials, one to three
characters, as they are. A VPinPlay card is the one 2.x's *Download QR Code* saved, and a
card from either version joins on the other.

A card is also how an account moves to an install where its player already is:
`POST /players/{id}/accounts/{extension}/card` writes the card's values into that player's
account. `reads_cards` on an account says whether its extension reads any card.

Removing a player, or signing a guest out, forgets their accounts with every extension.

## Jobs

Work that takes minutes is a job: you ask for the work, get `202` and a job resource back,
and follow it on the event stream. `POST /library/scan` is the first one - a metadata
rebuild across every game folder, which is why it is under `/library` rather than
`/games/{id}`; it isn't an operation on a game.

The job resource exists because the stream isn't the only way in. A client that connected
late, missed `job.done`, or simply wants to know what is running asks `GET /jobs` or
`GET /jobs/{id}` — `pct` and `message` hold the last progress reported, so a late reader is
correct without having seen a single event. Finished jobs are kept in memory since startup,
up to 500, and a restart empties them, apart from a job that asked to keep its result: that
one is written to disk when it finishes, the latest 20 stay, and they are available again after a
restart with `report` true on them. The listing returns the last 20 unless `?limit=` asks
for more. `title` names the run where its kind is shared by several operations - a scan, a reload
from disk and an auto-match are all `library.scan`, which is the lock on rewriting `.info`
files - and is empty where the kind's own name is enough. A finished job says how it went:
`detail` is one line of counts, in the install's
language, for the kinds that have something to count, and `stopped` is true on a `done` job
whose work was stopped part way - `state` stays `done`, `failed`, or `running`. `problems`
counts the parts of a `done` job that failed, an import's games that could not
be made among them; a `done` job with `problems` above zero is the one to review.

A job an extension started is shown only to a caller who reaches the tier of the request that
started it: `GET /jobs` leaves it out and `GET /jobs/{id}` answers `404` for anyone below, and
the event stream sends its `job.progress`, `job.done` and `job.failed` to those callers only,
on replay as well as live. A job started with no request behind it, a timer's for one, counts
as Library's. Core's own jobs are for anyone holding `jobs:read`.

Starting work is never a `POST /jobs`. The permission to run something is the permission of
the thing itself, so a scan is `games:write` because that is what a scan does. `jobs:read`
covers only asking; the right to watch is not the right to cause.

**One job of a kind at a time**, and the rule is shared with the rest of the app rather than
being an API-only guard: the Manager's own Scan button goes through the same registry, so
two library scans can't rewrite the same `.info` files at once, and a scan started from the
UI appears on the stream exactly like one started here. A second request gets `409 conflict`
rather than being queued — queueing would mean a double-click costs two full scans.

A job that can be stopped part way says so: `stoppable` is true, and while it runs
`links.stop` is where to `POST` to stop it, with the permission of the work itself. A
recording run is the one today.

## Game identity

Games are addressed by an opaque local id — `common/games/game_identity.py`, stored per game in
its `.info` under `vpinfe.game_id`:

```
GET /api/v1/games/tuF3WogthK
```

The id is minted once and then stays put. It survives renames, VPSdb re-matches, and table
updates, which is what an id in a URL, an event, or a job has to do.

A folder with no id is given one made from its location's id and its path inside that
location, not drawn at random. A folder VPinFE cannot write, such as a 2.x folder on a
read-only share, has the same id on every start, and that id is the one written when the
folder can be written. Tables with no id are given theirs the same way, from the game's
place and the table's file name. An id that something in the library already holds, as a
renamed folder does, is never reused: the new folder is given a random one.

`VPSId` is not that id, and can't be. It's empty for any game VPSdb hasn't matched, it isn't
guaranteed unique, and the effective id used elsewhere (`vpinfe.alt_vpsid or Info.VPSId`) is
deliberately cleared when the .vpx file changes — so updating a game would silently change
its identity. `vpsId` is still exposed on the game resource, because correlating with VPSdb,
VPinPlay, and other outside services is exactly what it's good for. It's an attribute, not
the key.

Two consequences worth knowing:

- Copying a game folder copies its id. The duplicate is spotted and re-minted the next time
  ids are checked across the library, so an id always addresses one game.
- Deleting a game's `.info` loses that game's rating and play counts. The game is given the
  id its place in the location makes, so a game given its id that way has the same id again. A
  folder deleted and made again at the same path is that game again, with the collections
  that named it. A re-issued id is random, so the game does not get the same id again.

Existing games get an id on their next metadata rebuild, or on demand. Reading a game never
mints one — a scan is a read path and stays one.

In Manager game rows the field is `vpinfe_id`, pairing with `vpsid` so each name says who
issued the id. The API exposes it as the resource's `id`.

### Schema version

The `vpinfe` section of a game's `.info` carries a `schema` number, bumped when the shape of
the file changes. `Info` and `User` are the interop contract other frontends read and write, so
they stay shape-driven and tolerant; the sections we own carry the version.

A file with no `schema` at all was written by 2.x. It is migrated on read and, on the first
write after that, the original is kept alongside it as `<Game>.info.vpinfe-<timestamp>`.

Migration runs on read, in memory, and never writes — the stamp reaches disk on the next real
write. A section written by a *newer* VPinFE is left exactly as it is: downgrading someone's
data because they ran an older build once is worse than not understanding it. A version we
don't recognize is never a reason to refuse to read a file.

`GET /library/info` says where the library's files stand:

| Field | What it holds |
|---|---|
| `pending_upgrade`, `pending_games` | How many games are on an older schema that Upgrade can bring forward, and their folders. A game under `unwritten` is not among them: Upgrade cannot write it either |
| `restorable`, `restorable_games` | How many games have a copy saved before they were upgraded that this build can put back, and their folders |
| `newest_backup` | The newest of those copies' stamps, as `20260909T110917Z`, or `""` for none |
| `newer_than_us` | How many `.info` files a newer VPinFE wrote. This build does not upgrade them |
| `unreadable` | Each folder whose `.info` could not be read, as `folder`, `path` and `error`. Those games are left out of the library |
| `unwritten` | Each game whose `.info` could not take what VPinFE wrote to it, as `folder` and `error`. It reads as what VPinFE meant to write until a write succeeds, the file changes or VPinFE restarts |

## Which VPS release a table is

A table's `source`, on a game's tables and on `/tables`, says which VPS release the file is.
It is `null` where nothing has said.

| Field | What it holds |
|---|---|
| `vps_file_id` | The VPS release. Empty on a table nothing has matched, and on one a person took the match back from - that one carries `confirmed_by` `user`, and VPinFE does not match it again |
| `confirmed_by` | Who said so: `user` where a person picked it, `declared` where whatever delivered the file named the record it fetched, `exact` where VPinFE matched it itself because exactly one VPS release of the game lists the file's authors and its version. A caller may send `declared` or `user` and nothing else, so a caller that could only infer a VPS release sends none and the table waits for a person |
| `vps_version` | The version VPS listed for the VPS release when the match was made, empty where it listed none or the match was made before this was recorded. An update is measured from it |
| `version`, `authors` | The VPS release as the catalog lists it now, empty where the catalog no longer holds it |
| `mod_of` | What the VPS release is a mod of, or `null` where it is not one |
| `base` | The file a patch was applied to, to make this table, or `null` on a table no patch made |

A release is a mod where VPS links it to the release it is based on (`parentId`) or tags it
`MOD`. `mod_of` has the same shape here and on each release from
`/vps/entry/{id}/releases`:

- `vps_file_id`, `version` and `authors` name the release it is based on. `vps_file_id` is
  empty where VPS does not say which: a mod tagged with no link, or a link the catalog
  cannot find.
- `note` is then VPS's own comment on the mod, and empty where it has none, which makes it
  a mod of something unknown.
- `game` is the name of the base's game only where that is another game than the mod's,
  and `url` is that game's VPS page.
- `game_id` and `table_id` are the table in this library matched to the base, empty where
  there is none.

`base` is on a table this install made by applying a patch:

- `file` is the name of the file the patch was applied to.
- `table_id` is the game's table recorded as those exact bytes, whether or not its file is
  still there, and empty where the game has none. It is found by the hash taken when the
  patch was applied, so a newer file of the same name is not it.
- `available` is whether that file is on disk. The patch fits those bytes only, so without
  them the next version of the patch has nothing to apply to.

## High scores

A game's `user.high_scores` is its machine's high score table as VPinFE last read it off
the machine, for the table the game launches by default: its ROM's, or for a table with no ROM
its score file's, once one has been read for it. It is read before and
after every game, whoever was playing, and `null` until anything has been read. It is the
machine's table, not the score of the last game: that game is on it only if it got there.

| Field | What it holds |
|---|---|
| `rom` | The machine the table belongs to. Two tables on one ROM share it |
| `table_id` | The table whose ROM it is |
| `read_at` | When it was read, or `null` for one kept from VPinFE 2, which did not record that |
| `state` | `read` where there are rows. See below for the others |
| `reason` | Why a read failed, where `state` is `unreadable`; empty otherwise |
| `file` | The score file's name where the table was read from a text file its script names, and empty for a ROM's own file, which the ROM names |
| `sections` | The machine's own groups, in its order, each a `name` and its `entries` |

`name` is the machine's words - `GRAND CHAMPION`, `HIGH SCORES` - as data, not a label. A
machine that keeps one number is one section named by the score map, `HIGHEST SCORE`.

| Entry field | What it holds |
|---|---|
| `rank` | The place in its section, or `null` where the machine does not rank it |
| `initials` | As the machine wrote them, blank and spaces included. Nothing is filled in |
| `score` | The number, or `null` for an entry that is not one - a lap time, a date |
| `prefix`, `suffix` | The machine's words either side of `score`, spacing included: `$ `, ` LOOPS` |
| `text` | The value as the machine shows it: `prefix`, `score` and `suffix`, or the entry's lines joined with a newline where it holds no number |
| `new` | Not on the table before the last game |
| `player` | The name of the player the entry is for, on the two routes below and blank elsewhere. Whoever has its `initials`; for an entry with no initials, the player whose record holds that score for this ROM. Blank where no one can be named |

`GET /games/{id}/high_scores` and `GET /games/{id}/tables/{table_id}/high_scores` answer
in the same shape. Each reads the score file again first where it is newer than the read
kept, as after a game played outside VPinFE or a file copied in, and keeps what it finds,
with `new` counted against the read before it. Their `state` says what can be shown:

| `state` | When |
|---|---|
| `read` | There are rows |
| `none` | A ROM the score map knows, and nothing saved yet |
| `unsupported` | A score file the score map cannot read |
| `unreadable` | The read failed; `reason` says why, and the log has it too |

A table with no ROM, or a ROM outside the score map with no score file, answers `null`:
nothing is there, and no game will change that.

## Pictures

A player's **Take Picture** during play keeps every screen as one PNG in the game's
`pictures/` folder (see [Input](#input)). The folder is the list: a PNG put there manually is
listed too, and one deleted from it is no longer listed.

| Field | What it holds |
|---|---|
| `name` | The file's name, which the other routes take as `{name}` |
| `taken` | When it was taken, in UTC: the PNG's own `Creation Time`, or the file's time where it has none |
| `table_id` | The table that was playing, from the PNG's `VPinFE Table`. Empty where it does not say |
| `size_bytes`, `width`, `height` | The file's size and the picture's |
| `version` | Changes whenever the file does. Sent back as `v`, it lets a browser keep the file |

## Adding routes

A route is an adapter: a path, a scope, one call into a service, and a model. The service
lives under `common/` and answers with plain domain data; the route is what turns that into
the wire shape.

```python
from fastapi import APIRouter
from common.games import game_lens
from httpapi import models, scopes
from httpapi.auth import requires

router = APIRouter(prefix="/games", tags=["games"])

@router.get("/{game_id}", dependencies=[requires(scopes.GAMES_READ)])
def get_game(game_id: str) -> models.GameResource:
    return models.GameResource(**game_lens.detail(game_id))
```

**Nothing calls a route handler.** Extensions, the Manager and the other route modules all
reach the same service, so there is one implementation of every answer and no route doing
double duty. A service module named `*_ops` writes, a `*_lens` reads; both return dicts,
because `common/` may not import `httpapi` and so cannot hand back a response model.

Refusals are raised as one of the four in `common/service_errors.py` - not found, refused,
blocked, unavailable - and a handler registered in `httpapi/errors.py` turns each into its
envelope. A route only needs a `try`/`except` when it gives a status those four do not
cover: an upload too large is 413, an unreadable session 422, a theme source that will not
load 503.

## Testing

`create_api_app()` builds the API standalone, so it's testable without NiceGUI:

```python
from starlette.testclient import TestClient
import httpapi

client = TestClient(httpapi.create_api_app())
assert client.get("/health").json() == {"status": "ok"}
```

See `tests/api/test_http_api.py`.
