# Remote

A surface made for a mobile device, for driving an install from across the room: what is playing, pick something else, and control it with a thumb. It is served by the Manager process at `/remote` and is a separate shell from the Manager, not a narrow rendering of it.

The Manager at `/manager` is desk-first and organized as a workbench, a grid beside an inspector. That shape does not fit one hand, so `/remote` is its own page with its own layout and the same palette, tokens and API client.

## Getting in

A mobile device gets in by scanning the Remote Code, which is the QR on the sheet that **On your mobile device** opens at the foot of the Manager rail, and on the frontend's main menu under **Remote Control**. The code carries a pass that holds Play and nothing else, so a mobile device can browse, launch, stop, press buttons and rate, and cannot change the library or anything Protected (`docs/http-api.md`, Authorization).

- Anyone who can see the main menu can scan it, which is how a visitor joins (see Join below).
- **New Remote Code**, on the same sheet, makes the old pass stop working. Mobile devices that used it are signed out.
- The Manager opened on a mobile device is a browser like any other. It signs in with a code from the screen of the device running VPinFE or from the authenticator app, and a Protected change asks for a Confirm.

### On the Home Screen

Safari keeps its address and navigation bars on a page opened from a link, and only hides them for a web app added to the Home Screen. `/remote` is installable: it links `GET /remote.webmanifest` (`display: standalone`, start at `/remote`, scope `/`) and a 180px touch icon, and carries the `apple-mobile-web-app-*` tags iOS reads. The manifest is linked with `crossorigin="use-credentials"` so the sign-in cookie goes with it, and the gate lets a Remote pass fetch it and the icons under `/manager-static/img/`.

- Open the page in Safari, **Share**, **Add to Home Screen**. It opens full screen with the status bar over the Remote's own header.
- The page sends a single viewport tag, with `viewport-fit=cover`; the framework's own plainer tag no longer sits beside it.
- The status bar style is `default`, an opaque bar above the page. `black-translucent` with `viewport-fit=cover` makes iOS 26 lay a Home Screen app out one status bar short, leaving that much of the screen empty under the tab bar, and nothing the page draws reaches it. iOS reads the style when the app is added, so an icon added before this needs removing and adding again.
- A web app has no reload or back. The Remote already reconnects on its own, which is what a locked mobile device needs, and **Sign Out** is on the identity sheet.
- Whether the installed app keeps Safari's sign-in is up to iOS. If it opens at the sign-in page, sign in there.

## Who is playing

Players are per install, not per mobile device, so the Remote asks who is playing before it writes anything on anybody's behalf.

A mobile device can say who it is, remembered per browser and per target in `manager/kit/remembered.py` - a mobile device aimed at two installs can answer differently on each:

- **Nobody said** (the ordinary case, and the owner's own mobile device never has to do anything else): the mobile device behaves exactly as it always has. A rating goes to the library, Favorite is offered, adding to a collection is offered.
- **A kept player**, chosen with **This Is Me** on the identity sheet (the person icon in the header): a rating goes to their own record instead of the library's, Favorite is not offered - it is the owner's alone - and adding to a collection still is, since collections are the household's. **Not Me** clears it.
- **A guest**, set by Join: the same as a kept player, and adding to a collection is not offered either, since a guest is gone at shutdown. **Sign Out** removes them.

The header names the target on every screen; a kept player's initials sit beside it. A guest's does not - "not shown" here is not security, since a pass that holds Play lets anyone say they are anybody, but it keeps a visitor's screen about them without dressing up a claim nobody can back with a key.

A player's circle leads their name on This Is Me's list and on Now's Up rows, and the header shows the picture in place of the person icon. It is their picture where they have one and their initials where they do not, or where the image does not load. The mobile device fetches `GET /players/{id}/picture` itself, as an `<img>`: from the page's own origin for the install serving the Remote, and from the target's own address for another device, so a target the mobile device cannot reach shows initials. The route is Play, which is all the Remote's pass holds, so the picture loads with no more access than the page itself has.

### Join

Reached from the main menu's **Join as Player** QR (`/remote?screen=join`) or from the identity sheet's own way in for a mobile device already on the page. Two choices, under both the one line said once - *you're a guest here until VPinFE closes*:

- **Use My Card** picks a file and reads the text 2.x's Download QR Code hid inside it - never the QR picture, so there is no decoder and no PNG to carry. A card's account already shares; joining puts the guest up alone.
- **Just Initials** - three letters, no account, up alone. A guest who wants to share afterwards types a user id from the identity sheet, which makes them a key the same way the Manager's Players page does; **Save Card** then downloads what core draws for it, the same file the Manager's Save Card would.

### On Now

Who is up is a switch per player, shown only with more than one player in the roster - the same gate the cabinet's own Player menu uses. A kept player who has not yet put themselves up gets **I'm Up** alone rather than the whole roster; once they are up, or if they were already, they see the same switches everyone else does. A guest only ever sees their own.

The idle card - *Last played*, with a rating - is superseded the moment a play is recorded this session: the result card then shows, for whichever of who was up belongs on this mobile device (everyone for nobody said or the owner, theirs first for a kept player, theirs alone for a guest), the game, their new high score entry where they made one, and what each of their sharing accounts did with it - *Sent to {service}*, *Waiting to send*, or *Not sent: {service} takes one player per game* where more than one player was up and this was not the one credited. The mobile device's own row carries the rating control. The idle card offers *Play again* whether or not the frontend is open, and *On the wheel* beside it only while it is.

## The three screens

`manager/remote/remote.py` defines them in `SCREENS`, and `?screen=wheel`, `games` or `now` links to one directly. A name it does not know opens where nothing was said. With nothing said, a launching table opens Now, a closed frontend or a Visual Pinball target opens Games, and anything else opens the Wheel. The tab you are on is the one with the lit pill, and the Now tab carries a green dot while a table is launching or up. A dots button in the header opens a sheet named for the target, holding what a mobile device seldom needs:

- **Tutorial**, **Who's Playing** (the identity sheet, which the person icon in the header opens too) and **Restart VPinFE** / **Quit VPinFE**, each of the last two asking once.
- **Reboot** and **Power off** under *The Device*, each asking once.

The restart, quit, reboot and power-off rows are drawn from the target's `GET /actions` and only where it lists them as available. A mobile device that joined with a Remote pass may only close the table, so those rows are not drawn for it.

- **Now** - what is playing, on which target, how long, plus any running job and anything wanting attention. Quit table. When nothing is playing it shows the last game played with its rating control.
  While a table is played, and not recorded, Now shows the table's playfield picture with the game's name over it and a Playing or Paused badge, a clock counting the session on the mobile device, and **Take Picture**, with **Back** while the table is paused. Each is a tap of the input action of that name, which the frontend hands to core during play, so a player holding a mobile device takes a picture with no button bound.
  **Pause**, or **Resume** while paused, leads that row. **Table Controls** opens a sheet of the keys the running table's app declares, grouped as the target lists them: Play, Game, Coins and Sound, with Nudge, Service Menu, Service Switches and Debug folded under **Nudge, Service and Debug**. Play leads the sheet with **Left Flipper**, **Launch Ball** and **Right Flipper** across the top, and **Left Magna Save** and **Right Magna Save** under the flippers: the left controls sit in the left column, the right ones in the right, and Launch Ball in the middle, as the table's app lists each control's `side`. Nudge opens the fold with **Nudge Left**, **Nudge Center** and **Nudge Right**; each is drawn only where the table's settings map it to one key the install can press, and left out otherwise, not dimmed. The Play controls stay down for as long as a thumb is on them, so a flipper stays up and the plunger pulls back until the thumb lifts; the page renews the press while it is held, and the install lets go on its own if the page stops, a mobile device locking its screen among them. Where an install can only tap a key (Wayland through `wtype`), they are one short tap. Nudge and every other key is one short tap. Service Menu follows Nudge in the fold: **Coin Door**, then the four keys that move through the menu (**Menu Up**, **Menu Down**, **Menu Enter** and **Menu Cancel**), ahead of the eight Service Switches. The sheet is capped below the top of the screen and scrolls, with its title and a close button held at the top. The Remote holds no list of its own and knows no key names; a press names the control and the install sends the key. A control that cannot be pressed is dimmed with its reason under it, and every one is dimmed with the install's reason where it cannot send keys at all. **Volume Down** and **Volume Up** press again about five times a second for as long as a thumb stays on them. A target that does not know the route draws none of this, and neither does a table a recording holds.
  Idle, Now is the last game played: its picture, when it was played, the mobile device's rating control, **Play Again** and **Show on Wheel**.
  While a recording run goes, Now shows it in place of what is playing: the game in hand, how far the run has come, **Pause** and **Stop**. A paused run shows why, with **Resume** and **Stop**. The Wheel shows the same card while a recording's table is up.
  With recordings waiting for a decision and no run going, Now says how many, with **Review** (see [Reviewing recordings](#reviewing-recordings)).
- **Games** - a search field that says how many it searches, collections as a row of chips with **All Games** first, and the games in it. A **Games | Tables** switch above the search lists one row per game or one per file; a table row shows its own picture and rating and the sheet it opens launches that file. Tables lists only files that can be started from here: not hidden, not known to be unlaunchable, and of a game the target offers. Each row has the game's picture (frame, shape and height as the Media settings of this install have them), its name, maker and year, small stars where it is rated and a heart for a Favorite. **List | Tiles** at the end of the search row shows two across with the picture first, and the mobile device remembers the choice. With the frontend up, the row the wheel is on has a lit edge and **On Wheel**, and a tap on another row moves the wheel to that game, keeping the list where it is. A tap on the row already there, or with no frontend to move, opens the sheet, and so does the chevron. The sheet's primary button is Launch. A row does not launch on tap.
  Where the target records, the sheet carries **Record Media** above Launch, with **Missing Only** and **Replace**, and a line under the game's name saying what it lacks: *No Playfield Video or DMD Video*. A kind the target cannot record for that game is left out of both, such as a DMD that Visual Pinball showed no window for when the game was last recorded there. Replace keeps each recording for a decision rather than replacing what is there at once. Where the target cannot record, Record Media is dimmed with the reason under it.
- **Wheel** - the game on the frontend's wheel as a picture with its name over it, a heart for the owner's favorites, the star rating and a filled **Launch**. Beneath are the held keys over the input seam: **Page Back** and **Page Forward**, then **Previous**, a round **Select** and **Next**, then **Back**, **Menu** and **Collections**. Tapping the picture opens the game's sheet. A held key keeps sending for as long as the finger is on it.

## Following the frontend

While the target's frontend is up, the mobile device and the screen show the same collection and the same game.

- The Remote opens on the collection the frontend is showing, and a collection picked on the mobile device switches the frontend.
- A switch or a wheel move at the screen reaches the mobile device over the target's event stream. Each open page follows `frontend.state_changed`, `play.state_changed`, `capture.run_changed`, `players.changed` and `table.play_recorded` on a thread of its own, because a read that waits for the next event would otherwise hold the Manager's event loop. The thread stops when the page goes. Who is up and the result card live on this same thread, tied to the frontend the way everything else here is - a target too old to report its frontend follows none of these either.
- The game on the wheel is on the Wheel screen only, with Launch. It is not drawn while a table is up.
- With the frontend closed, Now says so and draws no Take Picture, and the Wheel draws no keys, only **Open the Frontend** and **Open Games**. Open the Frontend also sits on Now and in the dots sheet beside the note, and it is drawn only where the target lists the action as ready and this browser may run it, so a Remote pass sees the note alone. An Owner's browser opens the frontend without a Confirm (`frontend:open`); restarting it still asks for one. A press goes to the frontend's windows and nowhere else, so with none up every button would report success and nothing would hear it.
- A target too old to report its frontend answers `GET /frontend/state` with 404. The Remote then follows nothing and behaves as it did before.

A tap moves the wheel and waits for the frontend to say it moved, rather than marking the row at once. A theme that does not act on the move leaves the mobile device showing what the screen shows.

## Reviewing recordings

A recording kept for a decision is best judged where it will play, so Review plays it on the frontend, on the screen it belongs to, and the mobile device is the controller. It shows the game, the kind and how far through it is, with **Before** and **After** (the file there now, and the recording), **Use This**, **Discard**, **Skip** and **Stop**. Nothing asks for confirmation: the choice is made watching the file itself.

- Recordings go a game at a time, and each game's window by window.
- Skip leaves a recording waiting; Stop ends the frontend's preview and leaves the rest waiting.
- A recording decided at the frontend itself moves the mobile device on, and Before and After turned there are turned on the mobile device.
- The review ends where a table is launched or the frontend closes.

## Targets

The target picker is in the header on every screen, because every action's meaning depends on which install it is aimed at. It is not drawn when only one device is registered.

Changing the target re-reads everything below the header. The library has to come from the target as well as the commands, since a list read from this install offers ids the target has never heard of. Writes go there too: a rating, a favorite or an add to a collection lands on the install the mobile device is aimed at.

A target is shown as reachable by asking it, not by reading `last_reachable`. A device recorded a moment ago carries a fresh timestamp whether or not it is switched on.

The row for the device serving the page is decided by the data rather than by comparing install ids: an install records itself with no address, and every other row is written from an address it was heard at.

## What it can write

Rating, favorite, and add-to-collection; starting, pausing and stopping a recording, and deciding what one kept; who is up, joining and leaving, and a guest's own account. Everything else is read, launch, or control.

Add-to-collection offers manual collections only. `GET /collections` already carries `type`, derived from whether the collection has criteria. With no manual collections the action is shown disabled with the reason rather than hidden.

A rating always goes to the library for the owner and to a player's own record for anyone else, never the other way round. Favorite and add-to-collection follow who is holding the mobile device the way [Who is playing](#who-is-playing) describes.

## Routes it uses

Every one of these already existed for the Manager and the API; Remote adds none.

| Job | Route |
|---|---|
| What is playing, quit | `GET /play/state`, `POST /play/stop` |
| Launch | `POST /games/{id}/launch` |
| Search, collections | `GET /games`, `GET /collections` |
| Tables, and which picture each shows | `GET /tables?media=<kind>` |
| A game's or a table's picture | `GET /games/{id}/media/{kind}?size=256`, and the same under `/tables/{table_id}` |
| Lifecycle actions | `GET`/`POST /actions` |
| Control's buttons, Take Picture and Back | `POST /input/actions` |
| What the running table answers to, and pressing it | `GET /play/controls`, `POST /play/controls/{id}` |
| Jobs, live | `GET /jobs`, `GET /events` |
| Whether the target records | `GET /api/v1` (its `capture` capability) |
| Record Media, and what a game lacks | `POST /capture/runs`, `POST /capture/plan` |
| A run, live | `GET /capture/runs/current`, and `capture.run_changed` on the stream below |
| Pause, Resume, Stop | `POST /capture/runs/current/pause`, `.../resume`, `.../stop` |
| Recordings waiting, and deciding one | `GET /capture/proposals`, `POST /capture/proposals/{id}` |
| Play one on the frontend | `PUT`/`DELETE /frontend/preview` |
| What the frontend shows | `GET /frontend/state`, and `GET /events?events=frontend.state_changed,play.state_changed,capture.run_changed,players.changed,table.play_recorded` |
| Switch the frontend, move the wheel | `PUT /frontend/collection`, `PUT /frontend/game` |
| Targets | `GET /devices`, `POST /devices/probe` |
| Rate, favorite | `PUT /games/{id}/rating`, `/favorite` |
| Add to collection | `PUT /collections/{name}/games/{game_id}` |
| The roster, who is up | `GET /players`, `PUT /players/{id}/up` |
| A player's circle | `GET /players/{id}/picture` (from the mobile device, as an image) |
| Join with a card, or with initials | `POST /players/guests/card`, `POST /players/guests` |
| Sign out, or leave a kept player's identity | `DELETE /players/{id}` (Not Me is local - it forgets nothing on the target) |
| A player's rating of a game | `PUT /players/{id}/ratings/{game_id}` |
| A player's record, for the rating shown while browsing | `GET /players/{id}/record` |
| A guest's accounts, for the identity sheet | `GET /players/{id}/accounts` |
| Share with an account, Save Card | `PUT /players/{id}/accounts/{ext}`, `/share`, `GET .../card` |

`GET /actions` lists only what the install performs, so Remote offers no button for the rest. An action that can run but not now comes with `available` false and a `reason`; Remote dims its button and shows that sentence rather than deciding for itself what is possible.

## Where it lives

```
manager/page.py      Manager shell, /manager
manager/remote/remote.py    Remote shell, /remote
manager/remote/remote_record.py  its part in recording media
manager/kit/api.py       shared client
manager/kit/data.py      shared reads
manager/kit/appearance.py     shared tokens
manager/kit/panel.py     shared fact list
```

Two shells in one package. The shared modules are not extracted to a neutral home; see `docs/conventions.md` on the panel being a shape rather than a place.

`reconnect_timeout` is set on the route, at 300. A mobile device locking its screen is the most common disconnect this surface sees.

## Density

Density is a per-surface token in `manager/kit/appearance.py`, not a second stylesheet. `--target-min` is the Manager's 32px floor and is raised to 44px where the pointer is a finger; `--star-size` is sized on its own, because five 44px boxes is a row 220px wide.

Palette and treatment stay shared. A second stylesheet is how one product starts looking like two.

## Remote is not Mobile

They are unrelated and the names suggest otherwise.

- `/remote` is this surface: a mobile device driving an install.
- Mobile means VPX Mobile, a mobile device we send tables **to**. `KIND_VPX_MOBILE` and the `mobile` config section mean the same thing.

Nothing about this surface uses the word "mobile".
