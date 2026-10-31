# Themes

VPinFE uses an embedded Chromium frontend with a WebSocket bridge to communicate between the browser and Python backend.

Themes interact with the backend through `vpinfe-core.js`, so theme code calls `vpin.call(...)` without handling transport details directly.

### Windows

VPinFE runs up to 3 browser windows, one per monitor:

- `playfield` - The main screen. Controller for all other screens and input. Handles gamepad/keyboard input and hosts the in-theme menu overlays.
- `backglass` - Backglass screen. Receives events from the controller.
- `scoreview` - DMD screen (not a "real DMD" like ZeDMD). Receives events from the controller.

Contract 1 calls them `table`, `bg` and `dmd`.

Each window has its own webpage but shares an instance of the VPinFE API ([frontend/api.py](https://github.com/vpinfe/vpinfe/blob/master/frontend/api.py)), accessed via [vpinfe-core.js](#vpinfe-corejs).

---

## Theme Structure

Themes are installed in the user config directory: `~/.config/vpinfe/frontend_themes/<THEME NAME>/` (Linux) or the equivalent `platformdirs` location on other platforms. 2.x reads `themes/` and nothing else, so 3.0 keeps its own folder beside it: an upgraded install has its themes copied across once, and the `themes/` folder is left as it was for anyone going back to 2.x.

```
<THEME NAME>
├── manifest.json
├── config.json          (optional - what the theme turns on or off in core, see below)
├── options.json         (optional - the settings a person can change; theme.json at contract 1)
├── preview.png          (optional - shown in the Manager, can be .png or .gif)
├── index_playfield.html  (one per declared window: index_<window>.html)
├── index_backglass.html  (optional at contract 2 - core draws the backglass when absent)
├── index_scoreview.html  (optional at contract 2 - core draws the score view when absent)
├── style.css
├── theme.js
├── locales/             (optional - the theme's own words, one <language>.json each)
│   └── en.json
└── fonts/               (optional - custom font files)
    └── MyFont.otf
```

A theme at contract 2 can be as small as `manifest.json`, `index_playfield.html`, a stylesheet
and a script that draws the wheel. Everything else in the list above is core's to do, and
[What core does for you](#what-core-does-for-you) says what that is and how to take any
of it back.

### manifest.json

Every theme must include a `manifest.json`:
```json
{
  "name": "My Theme",
  "version": "1.0",
  "author": "Your Name",
  "description": "A brief description of the theme.",
  "layouts": {"cab": "preview-cabinet.png", "desktop": "preview-desktop.png"},
  "change_log": "Initial release.",
  "min_vpinfe": "3.0",
  "windows": ["playfield", "backglass", "scoreview"]
}
```

| Field | Description |
|-------|-------------|
| `name` | Display name shown in the Manager. |
| `version` | Version string for tracking updates. |
| `author` | Theme author name. |
| `description` | Brief description shown in the Manager. |
| `layouts` | The layouts your theme is made for, each with its preview picture: `"cab"` for a cabinet, `"desktop"` for a flat screen. Name one or both. The Manager shows a picture for each layout side by side, and the layout you name first stands for the theme where there is room for one. A picture is a filename (`.png` or `.gif`) or an absolute `http...` address. |
| `preview_image`, `type` | Legacy, from 2.x: one picture, and `"desktop"`, `"cab"` or `"both"`. Still read when `layouts` is absent. A theme that should also appear on a 2.x install keeps both alongside `layouts`: 2.x skips a manifest without them, and 3.x ignores them when `layouts` is there. |
| `change_log` | What changed in this version. Optional. Shown as "What's new" in the Manager, and only to someone who has not installed your theme or has an update waiting — so it is worth writing for them, not as a running history. |
| `min_vpinfe` | The oldest VPinFE your theme runs on. Optional; absent means it runs on 2.x, which serves contract 1. Declaring `"3.0"` is what gets you the contract 2 surface. See [Theme contract](#theme-contract). |
| `windows` | The windows your theme wants, **controller first**. Required unless the manifest has the legacy `supported_screens` instead. See [Declaring windows](#declaring-windows). |
| `supported_screens` | Legacy. A count of screens, shown in the Manager and nothing more - it never decided which windows opened. `windows` replaces it and names them. Still accepted. |

`version` is your theme's own release number. `min_vpinfe` is the oldest VPinFE it runs
on. They are different questions and they move independently.

You do not declare a contract number. VPinFE knows which contract each of its own
versions serves, so the version you need already says which surface you get.

### Declaring windows

A manifest that has no `windows` gets three, named for the contract it declares: `table`,
`bg` and `dmd` at contract 1, `playfield`, `backglass` and `scoreview` at contract 2. A
manifest has to carry `windows` or the legacy `supported_screens`, so one without `windows`
is a manifest with `supported_screens`, and it gets the default windows for its contract.

Leaving out `windows` and shipping fewer pages also works: a default window whose
`index_<name>.html` is missing is not opened, so a single-screen theme that ships only its
main page gets one window rather than two that 404. That only applies to the default — a
window you declare is opened whether or not its page is there, because a declaration is
intent and hiding a missing page would hide your bug.

At contract 2 that is rarely a loss. A device with a backglass or score view screen gets a
window on it whichever theme is active, and core draws the page when the theme ships none
(see [What core does for you](#what-core-does-for-you)).

```json
"min_vpinfe": "3.0",
"windows": ["playfield", "backglass", "scoreview", "topper"]
```

Everything about a window follows from its name:

| | |
|---|---|
| the file it loads | `index_<name>.html` |
| what it is passed | `?window=<name>` |
| the monitor it opens on | the screen picked for it under Hardware › Displays in the Manager |

**The first window is the controller.** It owns input, audio and the selection, and
`vpin.isController()` is how a window knows. A window with no monitor set is not opened,
so declaring four on a two-monitor device is fine.

**The topper is the device's, not the theme's.** Someone with a topper screen sets it once
under Hardware › Displays, and VPinFE opens a `topper` window on it whichever theme is
active. If your theme ships `index_topper.html` that page is used, so declaring `"topper"`
and supplying the page keeps full control, as in the example above. If it does not, core
draws a page of its own that shows the selected game's topper video, else its topper image,
on black. A theme has nothing to declare for that, and `get_theme_windows()` still lists
only what the theme declared. A theme that declares `"topper"` but ships no page gets
core's page as well. Core's page is drawn by `core_media_window`, so a theme that sets
`media_window.enabled` to `false` leaves the topper screen black; a contract 1
theme shows the topper image only, as contract 1 has no topper video field.

Name a window after a media kind and `vpin.getImageURL(index, windowName)` gives you that
kind's art. Several published contract 1 themes already rely on it for `bg` and `dmd`.

### Theme contract

VPinFE serves the game payload in the shape your theme's `min_vpinfe` implies, so a theme
keeps working when the data behind it is reshaped. You never name a contract yourself: each
VPinFE version serves one, and saying which version you need says which you get.

| Contract | Declared by | What the payload looks like |
|---|---|---|
| `1` (default) | no `min_vpinfe`, or one below `3.0` | An **array of game rows**. Each row is one game, with its default table folded into `meta.VPXFile` and a media path per kind at the top level. This is what every theme written before 3.0 reads, and it is unchanged. |
| `2` | `"min_vpinfe": "3.0"` or newer | An **object with an `entries` array**. Each entry is one *table*, with the game it belongs to attached. A game that offers several tables can appear more than once. |

These are different shapes, not the same shape with different key names — asking for 3.0
changes how you iterate the payload, not just what you call things. See
[Contract 2 payload](#contract-2-payload).

**You do not need to raise `min_vpinfe` when VPinFE adds things.** New media kinds, new fields
and new `vpin.*` methods are visible at every contract — check for what you want and use it
if it is there:

```javascript
if (typeof vpin.someNewMethod === "function") {
    vpin.someNewMethod();
}
```

A contract only goes up when something a theme already reads is **removed or reshaped**, so
bumps are rare. If you declare a contract newer than the VPinFE you are running on, you get
the newest that build has and a warning in the log.

### What core does for you

A theme on contract 2 draws what is its own - the wheel, the look, the screens it cares
about - and core does what is the same in every theme. Each of those is a *capability*:
core does it by default, and a theme that wants to do it differently turns it off and does
it itself.

| Capability | `config.json` key | Contract 1 | Contract 2 | What core does | If you turn it off |
|---|---|---|---|---|---|
| `core_navigation` | `navigation.enabled` | off | on | Moves the selection by a step or a page, wraps it and announces where it went. | Previous, next and the page actions reach your `handleInput`, and you move the wheel. |
| `core_paging` | `paging.enabled` | on | - | Contract 1 only: handles the page actions. At contract 2 `core_navigation` covers it. | Page actions reach your handler. |
| `core_audio` | `audio.enabled` | off | on | Plays, fades and mutes each game's audio. | You play your own with `vpin.getAudioURL()`. |
| `core_layout` | `layout.enabled` | off | on | Turns the UI and the playfield art to suit the device. | You read `vpin.layout` and lay yourself out. |
| `core_preload` | `preload.enabled` | off | on | Fetches the media around the selection once the wheel stops. | Your own preloading is the only one. |
| `core_launch_indicator` | `launch_indicator.enabled` | off | on | Draws a spinner in every window while a game launches. | You draw your own, see [Loading Overlay During Game Launch](#loading-overlay-during-game-launch). |
| `core_attract` | `attract.enabled` | off | on | Moves the selection on its own once nobody has pressed anything. | You run your own idle timer, see [Attract Mode](#attract-mode). |
| `core_launch_dim` | `launch_dim.enabled` | on | on | Dims each window while a game launches. | Your windows stay as they are. |
| `core_media_window` | `media_window.enabled` | on | on | Shows each display window the media named for it. | A window with no element for it shows nothing of its own. |
| `core_waiting` | `waiting.enabled` | on | on | Covers each window with what an empty library is waiting for. | You read `vpin.libraryWaiting` and draw it. |
| `core_hold_ring` | `hold_ring.enabled` | on | on | Draws a ring while a key with a hold is kept down. | You draw your own from `vpin.enabled("core_hold_ring")`. |

Every key is an `enabled` flag in a block named after the capability, in `config.json`:

```json
{
  "attract":          { "enabled": false },
  "launch_indicator": { "enabled": false }
}
```

A theme names only what it changes. Contract 2 turns on the capabilities marked on, and a
theme that never mentions one gets it. A contract 1 theme gets none of the ones marked off
until it asks, so a theme written for 2.x keeps doing everything itself. It can ask for
any of them the same way, with `"enabled": true`.

`vpin.enabled("core_attract")` tells a theme which it has, and
[`vpin.capabilities`](#public-properties) lists all of them. Audio and paging can also be
changed while the theme runs, with `vpin.enableCoreAudio(false)` and
`vpin.enableCorePaging(false)`.

A person has the last word on attract mode. It has its own switch under Attract Mode in
the Manager's Behavior settings, and it is off if either that or the theme's says so, so a
theme opting in cannot force it on someone who has turned it off.

### Contract 2 payload

At `contract: 2` the payload is an object, and the list you iterate is `entries`:

```json
{
  "collection": "Friday Night",
  "expanded": false,
  "count": 3,
  "entries": [
    {
      "game": {
        "id": "tuF3WogthK", "vps_id": "9Paf7-CL",
        "name": "Attack from Mars", "manufacturer": "Bally",
        "year": "1995", "type": "SS", "themes": ["Aliens"],
        "dir_name": "Attack from Mars (Bally 1995)",
        "manufacturer_logo": "/manufacturers/bally/logo",
        "created_at": "2026-08-01T09:30:00Z",
        "path": "/games/Attack from Mars (Bally 1995)",
        "user": { "rating": 4, "favorite": false, "tags": [],
                  "last_played": "2026-08-01T20:14:00Z",
                  "play_count": 12, "play_time_seconds": 5400 }
      },
      "table": {
        "id": "Ls3JyWq7Fm", "filename": "Attack from Mars VPW Mod 1.2.vpx",
        "path": "/games/.../Attack from Mars VPW Mod 1.2.vpx",
        "version": "1.3.0", "rom": "afm_113b", "authors": ["jpsalas"],
        "detects": { "ssf": true, "nfozzy": false, "fleep": false }
      },
      "assets": { "pup_pack": true, "alt_color": false, "alt_sound": false },
      "siblings": 2,
      "media": ["playfield", "backglass", "wheel"]
    }
  ]
}
```

**An entry is a table, not a game.** A game folder can hold several `.vpx` — a desktop
build, a VR build, a patched variant — and they are peers. One entry is one of them, with
the game it belongs to attached.

| Field | What it is |
|---|---|
| `collection` | The collection being shown, or `""` for an ad-hoc filtered view. |
| `expanded` | `false` means one entry per game — its default table. `true` means one entry per table, so a game with three tables contributes three. The user sets this; your theme does not have to do anything differently either way. |
| `count` | How many entries. The same as `entries.length`. |
| `entries[].game` | Identity and metadata for the machine. The same names `/api/v1/games` uses. |
| `entries[].game.user` | What this user did with the game: `rating`, `favorite`, `tags`, `last_played`, `play_count`, `play_time_seconds`, `high_scores`. Timestamps are ISO 8601 UTC and durations name their unit, whatever the `.info` stores. |
| `entries[].game.user.high_scores` | The machine's high score table as last read, for the default table's ROM, or `null` until one has been read. `sections` hold the machine's own groups (`name`, `entries`); each entry has `rank`, `initials` as the machine wrote them, `score`, `text` (the value as the machine shows it, ready to draw) and `new` (not on the table before the last game). The whole shape is in `docs/http-api.md`, "High scores". A theme may show it; nothing requires it. |
| `entries[].game.derived_tags` | Tags an extension's Community list puts on the game, kept apart from the user's own in `user.tags`. Read-only. |
| `entries[].table` | The `.vpx` this entry is. `id` is stable across renames; `filename` is not. |
| `entries[].table.file_hash` | The sha256 of the `.vpx`. Two installs sharing a filesystem can agree they hold the same file without comparing paths, which differ by mount point. |
| `entries[].table.hidden` | The user chose not to be offered this table. A wheel skips these; the library has already dropped them from what it serves. |
| `entries[].table.release_date` | When this build was published, which is the table's own answer rather than the game's. Null where the `.vpx` did not say. |
| `entries[].table.default` | Whether this is the table its game defaults to. A game offering several appears once per table when expanded; this says which one is the game's own. |
| `entries[].table.user` | The same counters for this table alone — `last_played`, `play_count`, `play_time_seconds`. A game and its tables accumulate independently, so deleting a table does not un-play the game's hours. |
| `entries[].table.user.tags` | The user's tags on this table rather than its game. |
| `entries[].table.derived_tags` | Tags a Community list puts on this table alone - a list naming one build tags that build, not its game. |
| `entries[].assets` | What the game needs to play as intended, as booleans. |
| `entries[].siblings` | How many tables this entry's game offers. `1` means there is nothing to switch to. |
| `entries[].media` | The media kinds this game **has a file for**: `playfield`, `backglass`, `wheel` and the rest, the same names `vpin.getMedia(index, kind)` takes. Names, not paths: fetch one from `/media/<table id>/<kind>`. |
| `entries[].game.manufacturer_logo` | Web path to the manufacturer's logo, `/manufacturers/<slug>/logo`, or `null`. Art about the manufacturer rather than about this game, which is why it is not a media kind. |
| `entries[].game.created_at` | When the game's folder appeared, ISO 8601 UTC, or `null` where the filesystem gave no answer. What a "Newest" sort orders on. |

**`detects` loses the `detect_` prefix.** `table.detects.ssf`, not `detect_ssf` — the
prefix was storage, not vocabulary.

**`vpin.entries` is the list you iterate.** `vpin.tableData` holds the same array, but at
contract 2 its items are entries rather than games, so `entries` is the name that
describes them. The view they came from travels alongside as `vpin.collection` and
`vpin.expanded`. Every ordinal helper — `getTableCount()`, `getCurrentTableIndex()`,
`getTableMeta(index)` — counts and addresses entries.

**Media is named, not located.** `entries[].media` lists the kinds this game has a file
for. To show one, request `/media/<table id>/<kind>` from `vpin.endpoints.files` — the same
server your theme is loaded from. Contract 1 carried a filesystem path per kind and left
every theme to turn it into a URL; contract 2 does not put the filesystem in a web page,
and on a large library it keeps several hundred kilobytes off the wire each time the list
is rebuilt. Responses carry an `ETag` and ask to be revalidated, so replacing art in the
Manager appears without a hard refresh.

**Pages.** The flyer and the instruction card can be several pictures. `entries[].pages` maps
a kind to its page count, for the kinds that have two or more; a kind with one page is not
in it. Page 1 is `/media/<table id>/<kind>` as always, and page *n* is the same address with
`?page=n`. `vpin.getMediaPages(index, kind)` returns them in order.

**There is no `meta` at contract 2.** `meta` was the `.info` file passed through, so a
storage change reached themes whether or not it meant anything to them. Contract 2 serves
a payload of its own instead, and `.info` can be reshaped without touching your theme.
Everything `meta` carried that a theme actually reads has a home above.

**There is no entry id.** `table.id` is the identity — a table appears at most once in a
collection, so nothing else is needed.

### Names that changed in 3.0

3.0 takes its nouns from the Virtual Pinball Spreadsheet: the machine is a **game**, the
`.vpx` is a **table**, and the main screen is the **playfield**. Nothing was removed, and a
2.x theme needs no edits. Two different mechanisms keep it working, and which one you are
leaning on decides whether asking for 3.0 changes anything for you.

**The payload follows the contract your `min_vpinfe` implies.** At `1` — which is what you
get by declaring nothing — VPinFE builds the row shape 2.x themes read, including the names 2.x
used:

| the 3.0 name | what contract 1 serves |
|---|---|
| `gameDirName` | `tableDirName` |
| `fullPathGame` | `fullPathTable` |
| `PlayfieldImagePath` | `TableImagePath` |
| `PlayfieldVideoPath` | `TableVideoPath` |
| `meta.tables` | `meta.VPXFile` |
| `meta.vpinfe` | `meta.VPinFE` |

**The `vpin.*` surface does not follow the contract.** The projection reshapes the payload
and has never covered the JavaScript API, so the old names are aliases on the same object
instead. They work at every contract, for reads, writes and calls alike:

| use | still works |
|---|---|
| `vpin.playfieldRotation` | `vpin.tableRotation` |
| `vpin.playfieldOrientation` | `vpin.tableOrientation` |

**The selection members and the window messages need no alias.** `vpin.tableData`,
`getTableData()`, `getTableCount()`, `getCurrentTableIndex()`, `getTableMeta()`,
`playTableAudio()`, `stopTableAudio()`, `launchTable()`, and the `TableIndexUpdate`,
`TableDataChange`, `TableLaunching`, `TableRunning` and `TableLaunchComplete` messages,
are the names 2.x published and the names 3.0 uses. They address a row, and a row is a
table — one game may offer several.

The aliases are not a second API. Write new themes against the `playfield` names for the
screens and the `table` names for the wheel — those are what the rest of this document
uses.

---

## HTML Files

Each window has its own HTML file, named after the window: `index_<window>.html`. So a
theme's files follow from the windows it declares, and a theme that declares a `topper`
ships `index_topper.html` without anything else changing.

Leave out `windows` and you get three, named for your contract:

| Contract | Files |
|---|---|
| 2 | `index_playfield.html`, `index_backglass.html`, `index_scoreview.html` |
| 1 | `index_table.html`, `index_bg.html`, `index_dmd.html` |

**The main screen is `index_playfield.html` here.** Contract 1 called that window `table`,
and a contract 2 theme shipping `index_table.html` gets a 404 — nothing looks for it.

### index_playfield.html

This is the main HTML file. It displays the primary UI and hosts the in-theme menu overlays.
The worked example is [Template](https://github.com/vpinfe/theme-template), a small theme
that is read end to end: its `index_playfield.html` is the minimum structure, with each part
commented.

> Core's own files are served at `/core/`, against `/themes/` for what a theme provides.
> Themes written before 3.0 ask for them at `/web/`, which still serves the same files —
> nothing needs updating, and both spellings reach the same place.

The page links `/core/common/vpinfe-style.css` and `/core/common/vpinfe-core.js`, then its
own `style.css` and `theme.js`. Put `vpinfe-upright` on the outermost element so the page
turns to face the player when the screen is mounted sideways, and style that element
by class, since an id outranks the rules that turn it. Mark an element
`data-vpin-media="playfield"` and core fills it with the selected table's art.

#### Required Includes

```html
<link rel="stylesheet" href="/core/common/vpinfe-style.css">
<script src="/core/common/vpinfe-core.js"></script>
```

These are served by VPinFE's HTTP server, the same port the Manager is on (8001 by default). `vpinfe-core.js` provides all API calls, media URL helpers, gamepad/keyboard input, and event handling. `vpinfe-style.css` is required for the in-theme menu system styling.

Your theme's own `style.css` and `theme.js` can be named whatever you want.

#### HTML Elements

| Element | Purpose |
|---------|---------|
| `<div id="overlay-root">` | Optional. VPinFECore injects the overlay iframes here, and creates the element when the page has none. Add it to choose where the overlays sit. Harmless on the other windows, and nothing is ever injected into them. |

### Overlays

An overlay is a page VPinFE hosts above your theme: the main menu, the collection menu and
the tutorial. Core owns the hosting — it creates the iframe once, fades it in, hides rather
than destroys it, and closes any other overlay first, so **at most one is ever open**.

```javascript
vpin.overlay                      // "menu", "collectionMenu", "tutorial", or null
vpin.toggleOverlay("menu")        // open it, or close it if it is the one that is open
```

`vpin.overlay` is the whole state. A theme that dims itself while a menu is up reads it:

```javascript
if (vpin.overlay) document.body.classList.add("dimmed");
```

**An open overlay owns every action.** While one is up, core sends input to that overlay's
own handler and your theme's `handleInput` is not called. Nothing has to check for this;
it is what the guard already does.

Bindings are unchanged: `menu`, `collection_menu` and `tutorial` are input actions the user
configures, and core toggles the matching overlay when one fires.

| 2.x name | now |
|---|---|
| `vpin.menuUP` / `collectionMenuUP` / `tutorialUP` | `vpin.overlay` |
| `vpin.toggleMenu()` / `toggleCollectionMenu()` / `toggleTutorial()` | `vpin.toggleOverlay(name)` |
| `vpin.registerInputHandlerMenu(fn)` and its two siblings | `vpin.registerOverlayHandler(name, fn)` |

All nine old names keep working — the booleans read the string, and the methods call the
new pair with the overlay's name filled in.

While the device records its own screens into a run of games' media, core draws one line at
the top of the controller window, *Recording media - 3 of 24*, turned to face the player,
and hides it while each table is up. It is not an overlay and takes no input; a theme does
nothing for it. Exit asks *Stop recording?* for as long as the run goes.

The main menu offers **Record Missing Media** or **Record Media** for the game on the wheel
where the device can record, and a recording kept for a decision is shown in place over
your windows, on the screen it belongs to, until the player keeps it or throws it away.
Core draws both and owns the buttons while they are up.

#### Optional HTML Elements

| Element | Purpose |
|---------|---------|
| `<div id="fadeContainer">` | Wrap your content for fade-to-black transitions on game launch/return. Style with `transition: opacity` in CSS. |
| `<div id="fadeOverlay">` | Alternative fade pattern: a fixed full-screen black overlay that fades in/out via a CSS class (e.g., `.show { opacity: 1 }`). |
| `<div id="remote-launch-overlay">` | Overlay shown when the Manager triggers a remote game launch. Include `<div id="remote-launch-table-name">` inside for the game name. |

### Playfield Geometry: Mounting, Rotation And Cab Mode

Three settings describe one physical fact, and they are easy to confuse. This is what each
one means and how they combine.

| setting | section | what it turns |
|---|---|---|
| `playfieldorientation` | `[Displays]` | nothing — it states how the monitor is **mounted** |
| `playfieldrotation` | `[Displays]` | the **UI**, so it faces the player |
| `playfieldmediarotation` | `[Media]` | the **art**, so it fills the surface |
| `cabmode` | `[Displays]` | nothing — **context** only: type scale, target size, affordances |

#### The three setups

| | monitor mounted | does the OS rotate it? | window arrives | what has to turn |
|---|---|---|---|---|
| **A** | portrait | yes | portrait | the art only — the page is already upright |
| **B** | portrait | no | landscape | the whole page, then the art |
| **C** | landscape | n/a | landscape | nothing |

A is `portrait` + `0`. B is `portrait` + `90` or `270` — which one depends on which way the
panel was turned in the device. C is `landscape` + `0`.

**B is the case people miss.** If the desktop appears sideways on the playfield screen, or
the taskbar runs up the side of it, the OS is not rotating and VPinFE has to.

#### What your theme reads

```javascript
vpin.layout = {
  cabinet: false,        // context, never geometry
  uprightRotation: 0,    // turn the whole UI this far to face the player
  surface: "portrait",   // the shape to design for, AFTER that turn
}
```

**`surface` is the one you want.** It is identical in setups A and B — a portrait device
reads `"portrait"` whether the OS turned the screen or VPinFE turns the UI — so one layout
serves both. Design for `surface` and the difference stops being yours to handle.

`vpin.playfieldOrientation` and `vpin.playfieldRotation` remain as the raw ini values, but a
theme should not need them.

Do **not** infer any of this from `window.innerWidth` and `window.innerHeight`. Those move
with OS orientation, DPI, monitor placement and any transform your own theme has applied, so
a layout that reads them ends up fighting its own output.

#### Letting core do it

Set this in your `config.json` and core turns both the UI and the art for you:

```json
{ "layout": { "enabled": true } }
```

Then mark the element that turns the UI. `vpinfe-style.css`, which your theme already links,
carries the rules:

```html
<div class="vpinfe-upright">…your whole UI…
  <div data-vpin-media="playfield"></div>
</div>
```

Core draws the selected game's playfield into that element, marks the picture or video with
`vpinfe-playfield-media`, and turns it to fit, on every selection. The element needs
`position: relative` and has to fill the surface, because turned art is sized from the
screen. That is the whole integration — no rotation arithmetic anywhere in your theme.

Art your theme draws itself is marked the same way and passed to `vpin.applyPlayfieldMediaRotation` once it is in the page:

```html
<img class="vpinfe-playfield-media" id="playfield">
```

```javascript
vpin.applyPlayfieldMediaRotation(document.getElementById("playfield"));
```

A video takes the same two lines. Core measures the art once it knows its size, so it works
for a picture that is still loading.

**The art is measured, not assumed.** There is no reliable convention for how playfield
captures are authored: a library may be landscape desktop shots, portrait FSS renders, or a
mix. Core compares each image's own aspect against `surface` and turns it only when they
disagree, so FSS art on a portrait device is correctly left alone. `[Media]
playfieldmediarotation` is `auto` for that reason; set it to `0`, `90`, `180` or `270` only
for what measuring cannot see — art that is upside down, or art you would rather letterbox
than turn.

#### Doing it yourself

If your theme lays itself out, leave `core_layout` off and read `vpin.layout` directly. Two
rules matter:

- **Rotate the media, not the page**, unless the page genuinely has to turn — rotating the
  whole surface stands your text and controls on their side too.
- **Size a rotated element from the viewport** (`100vh` × `100vw` for a quarter turn), never
  from `getBoundingClientRect()`. That method reports the box *after* your transform, so
  feeding it back shrinks the image a little more on every pass.

Good questions to answer up front when starting a new theme:

- Should the theme declare `cab`, `desktop` or both under `layouts`?
- Should portrait mode use a different layout, or just rotate the landscape one?
- Should only the main playfield UI rotate, or should playfield-only overlays rotate too?

#### A portrait device, end to end

With `layout.enabled` on, a theme written for a landscape screen serves all three setups
with one page. Nothing in it reads the rotation setting.

```html
<body>
  <div class="vpinfe-upright">
    <div class="stage" data-vpin-media="playfield"></div>
    <!-- the wheel, the title, the rest of the theme -->
  </div>
</body>
```

```css
html, body { margin: 0; background: black; }
.vpinfe-upright { width: 100vw; height: 100vh; overflow: hidden; }
.stage { position: relative; width: 100%; height: 100%; }
```

- **`vpinfe-upright` on the outermost element** turns the whole page to face the player when
  VPinFE has to do the turning, and is an ordinary element when it does not. Give it the
  full viewport as above: when core turns it, core's own rules replace that size with the
  viewport on its side.
- **`data-vpin-media="playfield"`** is core drawing the art. It marks the picture or video it
  creates with `vpinfe-playfield-media`, measures it, and turns it to fill the surface, so
  landscape captures lie on their side on a portrait device and FSS renders are left as
  they are. The element it draws into needs `position: relative` and has to fill the surface, because
  turned art is sized from the screen.
- **Design for `vpin.layout.surface`** for anything that depends on shape, as above.

Core's own menus are mounted in `#overlay-root`, which core creates when the page has none
and does not turn. A theme whose page turns and whose menus must turn with it turns that
element itself.

Art the theme draws itself takes the class and one call instead, as in
[Letting core do it](#letting-core-do-it).

### index_backglass.html & index_scoreview.html

Same structure as above but with simpler content. These windows only display media and respond to events — they don't handle input.

Important: theme code for these windows should support both static images and videos. In practice that means:

- `backglass` windows should prefer `bg.mp4` and fall back to `bg.png`
- `scoreview` windows should prefer `dmd.mp4` and fall back to `dmd.png`

Do not hardcode these windows to image-only rendering with `getImageURL()` alone, or `bg.mp4` / `dmd.mp4` will never appear even when the files exist.

**Or leave it to core.** At contract 2 a theme need not ship these pages: core draws the
selected game's backglass or score view, video first and the still otherwise, on black. A
theme that wants its own page gets the same result from one attribute, with no script:

```html
<div class="stage" data-vpin-media></div>
```

Core fills every element carrying `data-vpin-media` on each selection. An empty value means
this window's own media; a value names a kind, `data-vpin-media="playfield"`, to show that
kind in any window. A value that is not a kind draws nothing. `media_window.enabled` set to
`false` turns it off.

The playfield, the backglass and the score view each have a still and a video. Which one an
element shows follows the player's media priority for that kind, and falls back to the other
when the game lacks it; the playfield falls back to the FSS picture after both. To insist on
one for an element, say so on it:

```html
<div data-vpin-media="playfield" data-vpin-media-prefer="image"></div>
```

`image` shows the still and `video` shows the video, whatever the priority is, and either
falls back to the other when the game has only one. The same element keeps its picture while
the selection stays on one game, so a playing video is not restarted.

With `layout.enabled`, playfield art drawn this way is marked `vpinfe-playfield-media` and
turned to fit the surface, as in [Letting core do it](#letting-core-do-it).

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <title>VPinFE - Backglass</title>
  <link rel="stylesheet" href="/core/common/vpinfe-style.css">
  <link rel="stylesheet" href="style.css">
  <script src="/core/common/vpinfe-core.js"></script>
  <script src="theme.js"></script>
</head>
<body>
  <div id="fadeContainer">
    <div id="bgImageContainer">
      <!-- Backglass or score view image inserted here by theme.js -->
    </div>
  </div>
  <div id="overlay-root"></div>
</body>
</html>
```

Typical JS pattern for these windows:

```javascript
function hasUsableMedia(url) {
  return Boolean(url) && !String(url).includes('file_missing');
}

function renderWindowMedia(container, imageUrl, videoUrl, altText) {
  const existingMedia = container.querySelector('video, img');
  const wantsVideo = hasUsableMedia(videoUrl);

  if (existingMedia) {
    if (existingMedia.tagName === 'VIDEO') {
      existingMedia.pause();
      existingMedia.removeAttribute('src');
      existingMedia.load();
    }
    existingMedia.remove();
  }

  if (wantsVideo) {
    const video = document.createElement('video');
    video.src = videoUrl;
    video.poster = hasUsableMedia(imageUrl) ? imageUrl : '';
    video.autoplay = true;
    video.loop = true;
    video.muted = true;
    video.playsInline = true;
    video.style.cssText = 'width: 100%; height: 100%; object-fit: cover;';
    video.onerror = () => {
      if (!hasUsableMedia(imageUrl)) return;
      const fallback = document.createElement('img');
      fallback.src = imageUrl;
      fallback.alt = altText;
      fallback.style.cssText = 'width: 100%; height: 100%; object-fit: cover;';
      video.replaceWith(fallback);
    };
    container.appendChild(video);
    return;
  }

  const img = document.createElement('img');
  img.src = hasUsableMedia(imageUrl) ? imageUrl : '';
  img.alt = altText;
  img.style.cssText = 'width: 100%; height: 100%; object-fit: cover;';
  container.appendChild(img);
}

function updateBackglassWindow() {
  const container = document.getElementById('rootContainer');
  const imageUrl = vpin.getImageURL(currentGameIndex, 'backglass');
  const videoUrl = vpin.getVideoURL(currentGameIndex, 'backglass');
  renderWindowMedia(container, imageUrl, videoUrl, 'Backglass');
}

function updateScoreViewWindow() {
  const container = document.getElementById('rootContainer');
  const imageUrl = vpin.getImageURL(currentGameIndex, 'scoreview');
  const videoUrl = vpin.getVideoURL(currentGameIndex, 'scoreview');
  renderWindowMedia(container, imageUrl, videoUrl, 'Score View');
}
```

### Custom Fonts

You can bundle custom fonts with your theme. Place font files in the theme directory (or a `fonts/` subfolder) and reference them with `@font-face` in your CSS:

```css
@font-face {
  font-family: 'MyFont';
  src: url('fonts/MyFont.otf') format('opentype');
}
```

You can also load web fonts (e.g., Google Fonts) via `<link>` in your HTML:
```html
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500&display=swap" rel="stylesheet">
```

---

## Setting the Theme

The user selects a theme by setting this in `vpinfe.ini`:
```ini
[Settings]
theme = <THEME NAME>
```

---

## theme.js

The main JS file for interacting with VPinFE and controlling the theme UI. Every window loads the same `theme.js`, so use `vpin.windowName` to branch logic per window - or `vpin.isController()`, which does not care what the controller is called. Older themes read a global `windowName` that they set themselves from `get_my_window_name`; `vpin.windowName` is the same answer without the round trip.

VPinFE also passes the current window identity in the page URL as `?window=playfield`, `?window=backglass`, or `?window=scoreview`. For a high-DPI setup, the `backglass` and `scoreview` windows may also get an optional `override` query parameter in the form `x,y,width,height`. Theme authors can read that value when they need to use the configured bounds instead of the auto-detected browser window size.

The worked example is [Template](https://github.com/vpinfe/theme-template). Its `theme.js`
has four parts, in this order in every theme: read its options, draw the list, draw the
selection, and hand every message to core with `await vpin.handleEvent(message)`, then
redraw. It has no input switch for stepping or paging, no media URLs, no audio, no
preloading, no loading overlay and no attract timer, because core does those on contract 2
(see [What core does for you](#what-core-does-for-you)). The one input action it answers is
`select`, to launch.

A theme on contract 1 still does its own, as it always did. Its `handleInput` receives
`joyleft`, `joyright`, `joyselect` and the rest of the names in the table below, and its
`receiveEvent` fades and draws its own loading overlay on `TableLaunching`.

> **Important:** Call `await vpin.handleEvent(message)` at the top of your `receiveEvent` function. This lets VPinFECore handle `TableDataChange` events automatically (collection changes, filter/sort updates) so you don't have to manage that logic yourself.

> **Important:** Set `window.vpin = vpin` so the in-theme menu system can call back into your VPinFECore instance.

### Strong Recommendation: Keep The Playfield DOM Persistent

For anything beyond a very simple theme, especially carousel-style playfield screens, avoid rebuilding the entire playfield window DOM on every game change.

A much smoother pattern is:

1. Create the playfield view scaffold once
2. Keep references to the important nodes
3. Update wheel art, title text, media, and tags in place
4. Only swap the specific media layer or text nodes that actually changed

This matters a lot for:

- smoother wheel navigation
- less layout jitter while images load
- cleaner image/video fades
- reduced browser work in Chromium

If a theme feels choppy, a full-screen rebuild on every selection change is one of the first things to remove.

### Media And Transition Performance Tips

The fastest-looking theme is usually the one doing the least work during browsing.

Things that helped in practice:

- preload nearby media such as the current, previous, and next game images
- prefer updating existing `<img>` / `<video>` nodes or swapping a small media layer instead of rerendering the whole screen
- keep fades simple; a plain crossfade is usually smoother than blur-heavy "dissolve" effects
- be careful with simultaneous animation systems; CSS transitions plus a JS animation library or canvas effects can stack up quickly
- if wheel browsing feels sluggish, test without heavy motion libraries first

For playfield video specifically, image-first browsing with delayed video start is often smoother than immediately starting video while the user is rapidly scrolling.

### Carousel Motion Guidance

If you want a wheel carousel to feel smooth instead of "slotty":

- keep a persistent wheel strip instead of recreating wheel nodes every move
- use a buffered strip with offscreen items if you want real scrolling motion
- anchor any selection halo or highlight to the selected position, not to the moving wheel artwork
- keep the selected/non-selected size difference moderate during motion so the eye follows the scroll instead of the scale jump
- tune motion duration generously; motion that is technically correct but too fast still reads like hopping

### Event Types

Events are sent between windows via `receiveEvent()`. These are the built-in event types:

| Event Type | Properties | Description |
|------------|------------|-------------|
| `TableIndexUpdate` | `index`, `previous`, `direction`, `reason`, `source`, `moving`, `group`, `groupKind`, `list`, `kind` | The selection moved. Sent by the controller to every window, on every path, and to one window alone when it asks with `SelectionRequest`. Also sent to the other windows, with `reason` `"restore"`, when a data change the backend raised takes the controller's game out of its list and the wheel lands on another. |
| `TableLaunching` | — | A game is about to launch. Frontend keyboard/gamepad routing is suspended until `TableLaunchComplete`; use this to fade out, stop audio, etc. |
| `TableRunning` | — | The launched game has finished loading and is now running. Sent when the table process outputs "Startup done". |
| `TableLaunchComplete` | — | The launched game has exited and frontend input routing is restored. Use this to fade back in, resume audio. |
| `PlayersChanged` | `state` | The roster changed - a player added, removed, or who is up. `state` is the same shape `vpin.players` holds, and core updates that property from it before your handler runs, so reading `vpin.players` again is enough; nothing needs to come from the message itself. |

**What `TableIndexUpdate` carries.** Every one of these is on every index message — a step,
a page and a startup restore all announce themselves the same way:

| Field | Meaning |
|---|---|
| `index` | where the selection is now, in your window's own list. Every window loads and orders its own list, so core rewrites this to where the game named by `id` sits in the list of the window the message reaches |
| `id` | which game that is: the table id (`table.id` on contract 2, `meta.VPXFile.id` on contract 1), or the game id or folder name for a row with no table id. Present on the wheel and absent for a picker, and for a row with no id. A window that is not the controller finds the game by this and not by `index`; when its list does not hold the game, core draws nothing, marks the window held (`data-vpinfe-held`) and hands it no `TableIndexUpdate` until a reload brings it |
| `previous` | where it was. A local diff cannot tell a wrap from a jump: 149 → 0 is either one step forward or 149 back |
| `direction` | `"previous"` or `"next"`, empty when the move had no direction |
| `reason` | how far and why — `"step"` for one item, `"page"` for a page press, `"restore"` at startup and when a window opens after the wheel has moved, `"enter"` and `"leave"` when core descends into a list or comes back out. A page press moves to the next group when group paging is on and a fixed number of rows otherwise; both report `"page"`, because both want the same treatment |
| `source` | who moved it — `"user"`, or `"attract"` when core's [attract mode](#attract-mode) moved it on its own. A theme that animates differently for a move nobody pressed reads this |
| `moving` | true while the wheel is still settling, so you can defer full-resolution art. Time-based, so a single distant move reports `false` — use `reason` to tell a page from a step |
| `group` | the group the selection is in - `"T"`, `"1985"`, `"#"`. Empty when the list's order has no groups |
| `groupKind` | what kind of group that is — `"letter"`, `"year"`, `"rating"`. Empty alongside `group`, so you can tell "no grouping here" from "the group happens to be empty" |
| `list` | which list this index is in — the collection name for the wheel, `"collections"` for the picker |
| `kind` | what that list holds — `"table"` or `"collection"`. Read this instead of keeping a mode flag: an index message can no longer be mistaken for "a game was picked" |

If you animate between positions, read `reason`: sliding one item is right for a `"step"`
and wrong for a `"page"`, which should cut. A page can move the selection a long way —
group paging jumps to the next letter, year or rating — so sliding through it is what
produces the two-wheels-stacked artifact.

`group` and `groupKind` are what you draw a "now in the Ts" badge from. They ride on every
index message, not just a page, so the badge stays right when the user steps across a
boundary too. Both are empty while a picker is open — the groups belong to the wheel.

### Lists core holds

Core can descend into a list of its own and move that instead of the wheel. Today that is
the collection picker, opened with `vpin.openCollectionPicker()`.

While a list is open, `list` and `kind` on every index message say which list moved, and
core owns `select` and `back` — select applies what the cursor is on and closes, back
closes without applying. It has to own them: your theme cannot pop a stack it does not
know about.

The picker's items are the rows `get_collections_metadata` returns, with *All Games*
first. Every item has a `label` to draw - the collection's name, or "All Games" in the
player's language - and `showing` is true on the one the frontend is showing now, which
is where the picker opens. *All Games* has an empty `name` and `image_url`, and its own
`table_count` and `game_wheel_urls`. `vpin.openCollectionPicker()` returns the list, and
its `items` are what to draw; it returns `null`, and opens nothing, when there are no
collections to offer.

**Nothing that follows the wheel fires while a picker is open.** The selected game does not
change, `onSelection` listeners do not run, and window media is not re-rendered. So a theme
does not need a mode flag, and does not need to undo anything when the picker closes — the
wheel is exactly where the player left it.

`vpin.activeList()` is the list the input actions are moving: the wheel until a list is
open, then the top one. It answers with an object holding `items`, `cursor`, `current`,
`length`, `id` and `kind`, so a handler that has to act on whatever is on screen reads it
instead of tracking a mode. `vpin.atRoot` is `true` while the wheel is what the cursor is
on and `false` while a list is open over it. Move through the input actions rather than
writing to the list.

```js
// Render whichever list core is moving.
window.receiveEvent = (message) => {
  if (message.type !== "TableIndexUpdate") return;
  if (message.kind === "collection") highlightCollection(message.index);
  else moveWheelTo(message.index);
};
```
| `RemoteLaunching` | `game_name`, `table_name` | The Manager triggered a remote game launch. Both names carry the same value; `table_name` is the 2.x spelling. Frontend keyboard/gamepad routing is suspended until `RemoteLaunchComplete`; show an overlay. Not sent for a launch that records a table's media: `TableLaunching` arrives for it, as for every launch, and core says what is happening itself (see Overlays). |
| `RemoteLaunchComplete` | — | The remote-launched game has exited and frontend input routing is restored. Hide the overlay. |
| `ProposalPreview` | `preview` | Core's own. A recording waiting for a decision is shown over your windows, and core takes the input until it is decided; `vpin.handleEvent()` draws it and your registered handlers never see it. Nothing for a theme to do. |
| `SelectionRequest` | `window` | Core's own. A window that is not the controller asks which game is selected when it opens, every 400 ms for the first 5 s and every 5 s after that until it is told; the controller answers once its own startup restore has finished, with a `TableIndexUpdate` with `reason` `"restore"` sent to that window alone. Until then core draws nothing into that window's media elements and sets `data-vpinfe-held` on the document element, which hides the page's body whatever it draws itself. The mark is not set while the window's list is empty. It never shows a game the controller did not name, so a window can show nothing but never another game than the playfield. `vpin.handleEvent()` takes it and your registered handlers never see it. Nothing for a theme to do. |
| `TableDataChange` | `index`, `id?`, `collection?`, `filters?`, `sort?` | Game data changed (collection switch, filter/sort update, a finished game's play data, a Manager edit). Handled automatically by `vpin.handleEvent()`. A collection switch from core's picker carries `id`, the game the controller opens on, and a window that is not the controller finds it in its own list and rewrites `index` to where it sits there. |

`TableDataChange` also arrives unprompted: when a game exits, and when the Manager
changes a game or a collection. Those are raised by the backend, which has no wheel
index to send, so `vpin.handleEvent()` fills `index` in before your handler sees it —
and it fills in where the game you were on has *moved to*, not the slot it used to
occupy. Assigning `message.index` to your wheel is therefore still correct after a
refresh reorders the list, which is what a `LastRun` sort does when a game is played.

You can also define custom event types and send them with `vpin.sendMessageToAllWindows()`.

### Loading Overlay During Game Launch

**Core draws one for you.** On contract 2, every window shows a spinner from `TableLaunching`
until `TableRunning`, or `TableLaunchComplete` for a launch that never got as far as
running. It is a `div.vpinfe-launch-indicator`, added to `<html>` and removed again, so a
theme restyles it in CSS and has nothing to wire up. A theme that wants a loading image of
its own sets `launch_indicator.enabled` to `false` in its `config.json` and draws it as
below.

Themes that draw their own can show a loading image or animation while VPX is starting. Use the built-in launch lifecycle instead of guessing with timers:

- show the overlay on `TableLaunching`
- hide it on `TableRunning`
- also hide it on `TableLaunchComplete` as a cleanup fallback

Add the overlay markup to every theme page that should show it (`index_playfield.html`, `index_backglass.html`, and/or `index_scoreview.html`):

```html
<div id="table-loading-overlay" aria-hidden="true">
  <img src="img/loading.gif" alt="" class="table-loading-spinner">
</div>
```

Keep the overlay transparent if you want the normal screen fade to remain visible underneath:

```css
#table-loading-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  pointer-events: none;
  transition: opacity 180ms ease;
}

#table-loading-overlay.is-visible {
  opacity: 1;
}

.table-loading-spinner {
  width: min(34vw, 34vh, 500px);
  height: min(34vw, 34vh, 500px);
  object-fit: contain;
}
```

Then drive it from `receiveEvent(message)`:

```js
function showTableLoadingOverlay() {
  const overlay = document.getElementById("table-loading-overlay");
  if (!overlay) return;

  overlay.classList.add("is-visible");
  overlay.setAttribute("aria-hidden", "false");
}

function hideTableLoadingOverlay() {
  const overlay = document.getElementById("table-loading-overlay");
  if (!overlay) return;

  overlay.classList.remove("is-visible");
  overlay.setAttribute("aria-hidden", "true");
}

async function receiveEvent(message) {
  await vpin.handleEvent(message);

  if (message.type === "TableLaunching") {
    showTableLoadingOverlay();
    await fadeOut();
  } else if (message.type === "TableRunning") {
    hideTableLoadingOverlay();
  } else if (message.type === "TableLaunchComplete") {
    hideTableLoadingOverlay();
    fadeIn();
  }
}
```

If the controller launches the game from local input, remember that `vpin.sendMessageToAllWindows(...)` excludes the sender. Call `showTableLoadingOverlay()` directly in the local `joyselect` path before `await vpin.launchTable(...)`, or send the event with `vpin.sendMessageToAllWindowsIncSelf(...)`.

### Attract Mode

**Core runs it for you.** On contract 2, once nobody has pressed anything for a while, the
controller steps the selection on its own, and the next press puts it back and does
nothing else. A theme has nothing to write: the wheel moves through the same
`TableIndexUpdate` path as any other move, with `source: "attract"` on it.

What a person can set, under Attract Mode in the Manager's Frontend Behaviors settings. A
change reaches a running frontend at once:

| Setting | Default | |
|---|---|---|
| Start After | 1 Minute | How long nobody has pressed anything, or Never to turn attract mode off whatever the theme says. |
| Show Each Game For | 8 seconds | The time between moves. |
| Order | Random | Random visits every other game before repeating one; In Order walks the list. |
| When a Button Is Pressed | Return to the Selected Game | Or stay on the game attract mode had reached. |
| Play Game Sound | off | Plays each game's sound as it is reached. Off while Mute Frontend Audio is on. |

Core holds off by itself while a game is launching or running, while a menu, picker or
confirmation is up, while input is disabled and while the list shows fewer than two games.
A press that ends attract is swallowed, whether it puts the selection back or leaves it,
because the game on screen was never the person's choice and a press that went on to act on
it could launch one they did not pick. Presses from a keyboard or controller end attract;
a touch or mouse on a theme's own page does not, so a theme with those holds attract off
while they are in use.

A theme that opens something core cannot see - its own picker, a video it wants to finish -
holds attract off for as long as it is up, and lets go when it closes:

```javascript
vpin.suspendAttract("picker");
// ...
vpin.resumeAttract("picker");
```

Holds are kept by reason, so attract runs again only when every reason that held it has let
go. `vpin.attractActive` is `true` while attract is moving the wheel.

A theme that runs its own attract mode sets `attract.enabled` to `false` in its
`config.json`. A contract 1 theme is not given attract mode, so its own runs alone. Either
way its own has to meet the rule about launching, below.

### Attract Mode During Game Launch

If your theme implements attract mode, treat game launch as a hard suspension boundary. Clearing the current timer is not enough, because user-activity listeners, menu events, or `TableRunning` handling can accidentally schedule a new idle timer while VPX is still open.

Use a separate launch/remote-launch suspension flag:

- set `attractSuspended = true` and clear both idle and advance timers on `TableLaunching` and `RemoteLaunching`
- keep `shouldPauseAttractMode()` returning `true` while `attractSuspended` is set
- make user-activity handlers clear timers and return without scheduling a new idle timer while suspended
- clear `attractSuspended` only on `TableLaunchComplete` or `RemoteLaunchComplete`
- after launch completion, restart the idle countdown instead of immediately starting attract mode
- in a local `joyselect` launch path, suspend attract mode before calling `vpin.launchTable(...)` so the sender is protected before backend events arrive

Example pattern:

```javascript
let attractIdleTimer = null;
let attractAdvanceTimer = null;
let attractSuspended = false;

function clearAttractTimers() {
  clearTimeout(attractIdleTimer);
  clearTimeout(attractAdvanceTimer);
  attractIdleTimer = null;
  attractAdvanceTimer = null;
}

function suspendAttractMode() {
  attractSuspended = true;
  clearAttractTimers();
}

function markUserActivity() {
  if (attractSuspended) {
    clearAttractTimers();
    return;
  }
  clearAttractTimers();
  attractIdleTimer = setTimeout(startAttractMode, ATTRACT_IDLE_MS);
}

async function receiveEvent(message) {
  await vpin.handleEvent(message);

  if (message.type === "TableLaunching" || message.type === "RemoteLaunching") {
    suspendAttractMode();
  } else if (message.type === "TableLaunchComplete" || message.type === "RemoteLaunchComplete") {
    attractSuspended = false;
    markUserActivity();
  }
}
```

### Input Actions

The following input actions are passed to your `handleInput` function (controller window only):

| Action | Gamepad | Keyboard |
|--------|---------|----------|
| `joyleft` | Mapped button | `[Input] keyleft` (default `ArrowLeft,ShiftLeft`) |
| `joyright` | Mapped button | `[Input] keyright` (default `ArrowRight,ShiftRight`) |
| `joyup` | Mapped button | `[Input] keyup` (default `ArrowUp`) |
| `joydown` | Mapped button | `[Input] keydown` (default `ArrowDown`) |
| `joyselect` | Mapped button | `[Input] keyselect` (default `Enter`) |
| `joyback` | Mapped button | `[Input] keyback` (default `Escape,b`) |
| `joytutorial` | Mapped button | `[Input] keytutorial` when routed to handlers |

The following actions are handled internally by VPinFECore and do **not** reach your handler:

| Action | Gamepad | Keyboard | Effect |
|--------|---------|----------|--------|
| `joymenu` | Mapped button | `[Input] keymenu` (default `m`) | Toggles the main menu overlay |
| `joycollectionmenu` | Mapped button | `[Input] keycollectionmenu` (default `c`) | Toggles the collection menu overlay |
| `joytutorial` | Mapped button | `[Input] keytutorial` (default `t`) | Toggles the Pinball Primer tutorial overlay |
| `joyexit` | Mapped button | `[Input] keyexit` (default `q`, and `Escape` held for 1.5 seconds) | Closes the application; while this device is recording a run, asks *Stop recording?* instead. With a menu or the tutorial up it closes that instead |
| `joypageup` / `joypagedown` | Mapped button | `[Input] keypageup`/`keypagedown` (defaults `PageUp`/`PageDown`) | Pages the game wheel (see below) |
| `take_picture` | Mapped button | `[input] take_picture` (unbound) | While a table runs: pauses it, and a second press takes a picture of every screen |

#### Taps and holds

A key or a button can carry a hold, written `key:Escape@hold:1500`: kept down for that many
milliseconds it does the hold's action, and let go sooner it does its ordinary one. An input
has one of each, so one that has a hold says nothing until it is released or the hold is
reached. Out of the box `Escape` is `joyback` when tapped and `joyexit` when held for 1.5
seconds, and button 1 of a standard-layout controller does the same.

On a contract 2 theme with core navigation on, a tap of `back` with nothing to go back from
does nothing, and your handler is not called for it. Leaving is the hold, `q` and the menu's
Quit.

Controller defaults are written against the standard layout, as `pad:standard/button:<n>`,
and apply only to a controller the browser reports as `mapping: "standard"`. A binding
written `pad:<index>/button:<n>` applies to any pad, which is what a pinball encoder is.

#### The hold ring

While a held input is kept down core draws a ring in the middle of the window that fills over
the hold's length, so the player can see how long is left. It appears after a quarter of a
second, so a tap never flashes it. It is a `div.vpinfe-hold` holding an SVG, added to
`<body>` and removed when the input is let go or the hold is reached:

```css
/* The element, to restyle it. Core sets --vpinfe-hold-ms to the hold's length. */
.vpinfe-hold { width: 10vmin; height: 10vmin; }
.vpinfe-hold-track { stroke: #333; }
.vpinfe-hold-fill  { stroke: #f2a900; animation-duration: var(--vpinfe-hold-ms); }
```

A theme that draws its own sets `hold_ring.enabled` to `false` in its `config.json` and reads
`vpin.enabled("core_hold_ring")` to know which it has. The hold itself still works with the
ring off; only the drawing goes.

#### Wheel Paging

By default the core handles `joypageup`/`joypagedown` itself: it asks the backend
where the press should land (`get_page_index`) and broadcasts a `TableIndexUpdate`
to every window. Your theme moves its wheel through the same `receiveEvent` path it
already uses for external index updates, so paging works with no theme changes.

The user controls the behavior with two `[frontend]` settings:

- `paging_group` — `sort` (default) jumps to the next boundary in whatever the list is
  ordered by: the next letter under title order (numbers and symbols share one `#`
  group), the next year under year order, the next rating under rating order. `count`
  jumps a fixed number of games. Orders where every value is its own — last played,
  date added, play count, play time — have no groups, and neither does a curated
  collection's manual order; a press moves a fixed number there, and so does a list
  that is all one group. The 2.x names `alpha` and `numeric` still resolve, to `sort`
  and `count`.
- `paging_size` — how many games a `count` jump moves (default `10`). All paging wraps
  around.

A collection may override `paging_group` for itself, or say nothing and follow the
setting. Which it resolved to is what the wheel actually does.

A theme that wants its own paging behavior calls `vpin.enableCorePaging(false)`;
the actions are then routed to `handleInput` like any other, and
`vpin.getPageIndex(direction)` is available if you want the config-aware target
index while animating the move yourself. While a core overlay (menu, collection
menu, tutorial) is up, these actions bypass core paging and go to the overlay's
handler regardless.

**Core paging is decided before your handler runs.** While it is on, a paging action
never reaches `handleInput`, so a `case "joypageup"` added without the
`enableCorePaging(false)` call never fires. A theme whose wheel shows something other
than the game list wants the call: core pages the game list and broadcasts the move
either way.

---

## vpinfe-core.js

The JavaScript interface to the VPinFE API. Must be loaded in your theme:
```html
<script src="/core/common/vpinfe-core.js"></script>
```

### Public Properties

These properties are available on the `vpin` instance after `vpin.ready` resolves:

| Property | Type | Description |
|----------|------|-------------|
| `vpin.tableData` | `array` | The current (possibly filtered) game list. At contract 2 each element is an entry (see [Entry Data Object](#entry-data-object)); at contract 1 it is a game row. |
| `vpin.monitors` | `array` | List of monitor objects with `name`, `x`, `y`, `width`, `height`. Loaded during init. |
| `vpin.playfieldOrientation` | `string` | Playfield orientation from config: `"landscape"` or `"portrait"`. |
| `vpin.playfieldRotation` | `number` | Playfield rotation in degrees from config (default `0`). |
| `vpin.layout` | `object` | The resolved layout answers — `{ cabinet, uprightRotation, surface }`. This is what a theme should read; the two properties above it are the raw ini values. See [Playfield geometry](#playfield-geometry-mounting-rotation-and-cab-mode). |
| `vpin.themeAssetsPort` | `number` | The port the files are served on, the HTTP server (default `8001`). Prefer `vpin.endpoints.files`, which is the whole base URL. |
| `vpin.endpoints` | `object` | Where the things this page talks to are — `{ library, device, files, frontend_channel }`. The first three are addresses you add a path to: `library` is the catalog and what's known about it, `device` is this machine (launching, play state, hardware), `files` is the files themselves (theme packages, table media, manufacturer logos). `frontend_channel` is not an address but a line held open — how this page and VPinFE talk to each other, both ways — so nothing is appended to it. Build URLs from these rather than assuming a host or a port: the halves can be separate machines, and only this knows where they are. When the library is on another install, `files` is this device's own `/library`, which fetches the picture from the library signed in as this device - a request from a page straight to the library would be refused. |
| `vpin.menuUP` | `boolean` | Whether the main menu overlay is currently visible. |
| `vpin.libraryWaiting` | `string[]` | While the library is empty because every folder it reads from is away - a NAS asleep or down - the servers or folders it is waiting on; empty otherwise. Core covers every window with them (`core_waiting`, on by default) and loads the list the moment one answers, raising `TableDataChange`. A theme that draws its own waiting state sets `waiting.enabled` false and reads this after an empty `getTableData()`. |
| `vpin.capabilities` | `object` | What this build does on your behalf, and whether each is on — `{ core_paging: true, core_audio: false, core_preload: false }`. A name that is absent is a behavior this build does not have, so check before relying on it. Reading it gives you a copy; use `enableCorePaging()` / `enableCoreAudio()` to change anything. At contract 2 `core_navigation` covers stepping *and* paging, so `enableCorePaging(false)` turns off both — a page is a bigger step, not a separate feature. |
| `vpin.contract` | `number` | Which contract you are being served. |
| `vpin.windowName` | `string` | This window's name — which page it loaded, and its media kind when it has one. Known before the socket opens, so there is nothing to await. |
| `vpin.collectionMenuUP` | `boolean` | Whether the collection menu overlay is currently visible. |
| `vpin.players` | `object` | Everyone on this install and who is up: `{ players: [{ id, name, initials, owner, guest, up, shares_initials_with, picture }] }`. `owner` is the one player the library's own ratings and history belong to; `guest` is forgotten when VPinFE closes; `up` is who the next game counts for - several may be at once. `shares_initials_with` lists the ids of any other player with the same initials, which is how a score could go to nobody. `picture` is an address to put in an `<img>`, such as `/player_pictures/<id>.webp?v=3f9a1c`, or null where the player has none - a guest never does. It is a centre-cropped square, at most 1024px, and the address changes whenever the picture does. Draw the player's initials, or a glyph of your own, where it is null or fails to load. Loaded during init and kept current by `PlayersChanged`. |

### API Reference

#### t(key, english, params)

What a key says, in the language this install speaks. Core's own words, the handful
every theme was writing out for itself, and your theme's own words from its `locales/`
folder.

```js
wheel.textContent = vpin.t("frontend.theme.no_tables_found", "No tables found");
status.textContent = vpin.t("frontend.theme.launching", "Launching...");
credit.textContent = vpin.t("theme.authors", "Authors");
```

`english` is what shows when the catalog has no entry. The catalog is in by the time
`await vpin.ready` returns, so a word drawn after it reads in the right language. A key
the catalog does not have answers with the English, and with the key when there is none,
so it is never blank.

`params` fills the entry's slots by name: `{count}` from `count`. A `count` also picks
the form, so an entry written as `{ "one": "{count} game", "other": "{count} games" }`
says "1 game" and "12 games". The English can take the same shape. It comes before the
parameters, so a call with parameters and no English passes `""` in its place.

A slot written `{count:n}` is a number, and it is written the way this install's language
writes one: `1,234` or `1.234`, and `1.5` or `1,5`. Write it that way for any count that
can pass a thousand. A value that is not a number is left as it is. `vpin.number(1234)`
does the same for a number your theme draws without an entry.

Core ships these, and they are the ones seven themes had each written their own copy
of:

| key | English |
|---|---|
| `frontend.theme.no_tables_found` | No tables found |
| `frontend.theme.waiting_for_tables` | Waiting for your tables |
| `frontend.theme.loading_table` | Loading Table... |
| `frontend.theme.launching` | Launching... |
| `frontend.theme.unknown_table` | Unknown Table |
| `frontend.theme.unknown_author` | Unknown Author |
| `frontend.theme.my_rating` | My Rating |
| `frontend.theme.detected_features` | Detected Features |

##### Your theme's own words

A theme that writes its own English keeps working as it does: `t()` is there when you
want your theme to follow the user's language, and nothing breaks if you never call it.

To translate your own words, put one file per language in `locales/`:

```
locales/en.json
locales/de.json
```

```json
{
  "authors": "Authors",
  "tables": { "one": "{count} table", "other": "{count} tables" }
}
```

A key in the file is read as `theme.<key>`, so the file above is `vpin.t("theme.authors",
"Authors")` and `vpin.t("theme.tables", "", { count })`. The file holds the words as plain
keys; the `theme.` is added by core. Core's own keys never start with `theme.`, so a
theme adds words and cannot change one of core's.

The language is the install's, and a name like `de-AT` is looked for as `de-AT`, then
`de`, then `en`. A language the theme does not ship, or a key missing from the one it
does, falls back to `en.json` and then to the `english` in the call. A missing,
malformed or empty `locales/` folder is the same as none. Ship `en.json` even if the
English is already in your code: it is the file a translator starts from.

Feature names are not on that list on purpose. `Nfozzy`, `Fleep`, `SSF`, `PuP-Pack` and
the rest are what the community calls those things in every language, and translating
them would make a theme harder to read, not easier.

#### enabled(name)
`true` when core is doing that for you right now. `vpin.enabled("core_audio")`. An unknown
name is `false` rather than an error, so a theme can ask about something a build might not
have.

The default of each capability, at each contract, is in the table under
[What core does for you](#what-core-does-for-you).

Set one in your **`config.json`** — the author's file. A capability's settings live in
a block named after it, and `enabled` is one of those settings:

```json
{
  "audio":   { "enabled": true, "max_volume": 0.8 },
  "preload": { "enabled": true, "kinds": ["playfield", "backglass", "wheel"] }
}
```

That shape is the one to write, because it is the only one that carries a capability's
other settings — there is no flat spelling of `preload.kinds`. Core audio also answers to
`use_core_audio` and `audio.use_core_audio`, which accumulated before anything said which
was meant; both still work and neither is worth adding to a new theme.

#### Core preloading

With `core_preload` on, core fetches the media for the selection and its two neighbors —
but only once the wheel has stopped, after about 180 ms of quiet. That delay is the point.
A held control walks the wheel and speeds up as it goes — down to a step every 70 ms — so a
theme preloading on every step asks for images that are obsolete before they decode, and the
browser is still draining that queue long after the control is released. Waiting for the wheel to settle turns a
two-second hold into one batch.

`preload.kinds` chooses what gets fetched; the default is `["playfield", "backglass", "wheel"]`.
A theme showing a cabinet shot wants `cab` in there, and one with no wheel should drop it.

```json
{ "preload": { "enabled": true, "kinds": ["playfield", "backglass", "wheel"] } }
```

**If core preloads, delete your own preloading.** Running both just doubles the requests.
It is on for a contract 2 theme, so one that preloads for itself sets `preload.enabled` to
`false`; a contract 1 theme is not given it until it asks.

#### init()
Sets up keyboard event listener and connects to the backend over the WebSocket bridge.

#### registerInputHandler(handler)
Registers an input handler for the playfield screen. Only works on the controller - the first window your theme declares, `playfield` unless you say otherwise. The handler receives a single string argument (the action name).

#### registerInputHandlerMenu(handler)
Registers an input handler for the main menu overlay.

#### registerInputHandlerCollectionMenu(handler)
Registers an input handler for the collection menu overlay.

#### toggleMenu()
Programmatically toggles the main menu overlay open/closed.

#### toggleCollectionMenu()
Programmatically toggles the collection menu overlay open/closed.

#### call(method, ...args)
Invokes a backend API method over the WebSocket bridge. Returns a Promise.

The following methods are available via `vpin.call()`:

##### Window & App

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `get_my_window_name` | — | `string` | Returns the window name for this instance (`"playfield"`, `"backglass"`, or `"scoreview"` by default). |
| `close_app` | — | — | Shuts down all browser windows and exits the application. |
| `shutdown_system` | — | — | Powers off the device. |
| `lifecycle_request` | `scope`, `action`, `reason`, `confirmed` | `bool` | Starts, stops or restarts something. Returns whether it is going ahead. |
| `lifecycle_needs_confirmation` | `scope`, `action` | `object` | `{confirm, description}` — whether to ask the user first, and the wording to ask with. |
| `get_monitors` | — | `array` | Returns list of monitor objects with `name`, `x`, `y`, `width`, `height`. |
| `console_out` | `output` | `string` | Sends a message to VPinFE's log, where it appears as `[<window>] your message`. Useful for debugging on the VPinFE device, where nothing reads the browser log. Returns the same string. Your uncaught errors and unhandled rejections are forwarded automatically — you do not need to call this for those. |

###### Starting, stopping and restarting

`scope` is what the action applies to and `action` is what happens to it:

| | `frontend` | `vpinfe` | `system` |
|---|---|---|---|
| `start` | yes | — | — |
| `stop` | yes | yes | powers off |
| `restart` | yes | yes | yes |

`close_app` and `shutdown_system` still work and are unchanged — they are `vpinfe`/`stop`
and `system`/`stop` under the old names.

Use `vpin.requestLifecycle(scope, action)` rather than calling the two methods directly.
It confirms first when the user has turned that on for the scope, and resolves `false`
when they say no, so a menu can stay open:

```js
if (await vpin.requestLifecycle("system", "restart")) {
  // going ahead - the machine is restarting
}
```

Core draws the confirmation, so a theme gets it for free. A theme that wants its own
asks the user itself, then calls `lifecycle_request` with `confirmed` set to `true`.

The user chooses which scopes get confirmed with the `lifecycle.confirm` setting, which
is empty by default. The question is always put on the surface that asked; every other
window is told through the `lifecycle.acting` event and cannot block it.

##### Game Data

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `get_tables` | `reset=false` | `string` (JSON) | Returns JSON string of the current (filtered) game list. Pass `true` to reset to the full unfiltered list. Each game object includes paths, media paths, addon flags, and metadata. |
| `launch_table` | `index` | — | Launches the game at the given index. Blocks until the table exits. Records the play against the game — start count, last-played date and runtime — which is what the "Last Played" collection is built from. Sends `TableLaunching` before launch, `TableRunning` when the table finishes loading, and `TableLaunchComplete` when it exits. |
| `build_metadata` | `download_media=true`, `update_all=false` | `object` | Triggers a background metadata build/refresh. Sends progress events (`buildmeta_progress`, `buildmeta_log`, `buildmeta_complete`, `buildmeta_error`) to all windows. Returns `{success, message}`. |

##### Collections

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `get_collections` | — | `array` | Returns the names of the collections `get_collections_metadata` returns. |
| `get_collections_metadata` | — | `array` | Returns the collections the frontend offers, in the order they are arranged, each with `name`, `type`, `is_filter`, `image`, `image_url`, `in_frontend`, `table_count` and `game_wheel_urls`. `image_url` is a theme-server URL such as `/collection_icons/favorites.png`, or an empty string when no image is set. `table_count` is how many entries the collection shows, rules and limit included. `game_wheel_urls` is up to four wheel URLs of the games it holds, the same shape as `image_url`, for drawing a collection with no image of its own. A collection kept out of the frontend is left out unless it is the one showing, which comes with `in_frontend` false. On a device that reads its library from another (`network.library_url`), these are that device's collections, counted there, and both URLs are paths on this device's own `/library`, which fetches the picture from that library. |
| `get_collection_image_url` | `collection` | `string` | Returns the image URL for one collection, or an empty string when no image is set. |
| `set_tables_by_collection` | `collection` | — | Shows the named collection: what it holds, in the order it stores. Works for a hand-picked collection and a filter-based one alike. |
| `save_filter_collection` | `name`, `letter`, `theme`, `game_type`, `manufacturer`, `year`, `order_by`, `rating`, `rating_or_higher`, `direction` | `object` | Saves the current filter settings as a named collection. Every filter defaults to `"All"`. `order_by` is the sort (default `"title"`) and `direction` is `"asc"` or `"desc"` (default `"asc"`); `"Ascending"` and `"Descending"` are read the same way. Returns `{success, message}`, where `message` is in the player's language. |
| `get_current_collection` | — | `string` | Returns the name of the currently active collection, or `"None"`. |

##### Filters & Sorting

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `reset_filters` | — | — | Resets all filters back to the full game list. |
| `get_filter_letters` | — | `array` | Returns available starting letters from all games (for filter UI). |
| `get_filter_themes` | — | `array` | Returns available themes/categories from all games. |
| `get_filter_types` | — | `array` | Returns available game types (SS, EM, PM, etc.) from all games. |
| `get_filter_manufacturers` | — | `array` | Returns available manufacturers from all games. |
| `get_filter_years` | — | `array` | Returns available years from all games. |

Applying a filter or a sort is not on this list. The collection menu VPinFE ships owns
those controls, so `apply_filters`, `apply_sort`, `get_current_filter_state`,
`get_current_sort_state` and `get_current_order_state` are core's own and `vpin.call`
refuses them (PAR-84). Show the library the way a collection stores it and let the menu
change it — `set_tables_by_collection` and the lists above are what a theme needs for that.

##### Events & Messaging

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `send_event_all_windows` | `message` | — | Sends an event to all windows except the caller. |
| `send_event_all_windows_incself` | `message` | — | Sends an event to all windows including the caller and iframes. |
| `send_event` | `window_name`, `message` | — | Sends an event to a specific window by name (`"playfield"`, `"backglass"`, or `"scoreview"` by default). |

##### Input

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `get_joymaping` | — | `object` | Returns the gamepad button mapping from `vpinfe.ini`. Keys: `joyleft`, `joyright`, `joyup`, `joydown`, `joypageup`, `joypagedown`, `joyselect`, `joymenu`, `joyback`, `joytutorial`, `joyexit`, `joycollectionmenu`. Values are button index strings. |
| `get_keymapping` | — | `object` | Returns the keyboard mapping from `vpinfe.ini`. Keys: `keyleft`, `keyright`, `keyup`, `keydown`, `keypageup`, `keypagedown`, `keyselect`, `keymenu`, `keyback`, `keytutorial`, `keyexit`, `keycollectionmenu`. Values are comma-separated browser key names or key codes. |
| `set_button_mapping` | `button_name`, `button_index` | `object` | Sets a gamepad button mapping and saves to config. Returns `{success, message}`. |
| `get_page_index` | `index`, `direction` | `number` | Returns the wheel index a page press should land on, from `index` in the given `direction` (`"next"` or `"prev"`). Honors `[frontend] paging_group`/`paging_size`, any override the collection carries, and the order on screen. See [Wheel Paging](#wheel-paging). |

##### Theme & Display Config

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `get_theme_name` | — | `string` | Returns the active theme name from `vpinfe.ini`. |
| `get_theme_config` | — | `object\|null` | Loads and returns the theme's current configuration values. The theme's `config.json`, the defaults in `options.json` and what the person chose, merged into one object. See [Values Returned To Theme Code](#values-returned-to-theme-code). |
| `get_theme_assets_port` | — | `number` | Returns the HTTP server port (default `8001`). |
| `get_theme_index_page` | — | `string` | Returns the full URL for this window's theme index page. |
| `get_playfield_orientation` | — | `string` | Returns the playfield orientation from config (`"landscape"` or `"portrait"`). |
| `get_playfield_rotation` | — | `number` | Returns the playfield rotation angle in degrees from config (default `0`). |

##### URL Query Parameters

Theme pages receive the current window name in the `window` query parameter:

- `?window=playfield`
- `?window=backglass`
- `?window=scoreview`

For `backglass` and `scoreview`, VPinFE can also pass an optional high-DPI display override:

- `?override=x,y,width,height`

This is intended for setups where the detected Chromium window bounds are not the values the theme should use, usually on high-DPI screens. The `override` value is a comma-separated string containing:

- `x`: left position
- `y`: top position
- `width`: window width
- `height`: window height

Example:

```javascript
const params = new URLSearchParams(window.location.search);
const windowName = params.get('window') || 'unknown';
const override = params.get('override');

let overrideBounds = null;
if (override) {
  const [x, y, width, height] = override.split(',').map(Number);
  overrideBounds = { x, y, width, height };
}
```

If `override` is present, themes that position or scale backglass or score view content based on window bounds should prefer those values over `window.innerWidth`, `window.innerHeight`, or other automatically detected measurements.

##### Core Audio Helpers

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `playTableAudio` | `indexOrUrl`, `retries=3` | — | Plays game audio using VPinFECore's centralized audio manager. Pass a game index (recommended) or URL string. |
| `stopTableAudio` | `options={}` | — | Stops audio via centralized manager. Supports fade-out; pass `{ immediate: true }` for an immediate stop. |
| `enableCoreAudio` | `enabled=true` | — | Enables or disables centralized audio handling for the current window. Core audio is on by default for a contract 2 theme and off for a contract 1 theme. |
| `isCoreAudioEnabled` | — | `boolean` | Returns whether centralized audio handling is currently enabled. |
| `setAudioOptions` | `options` | — | Sets runtime audio options. Supported keys: `maxVolume`/`max_volume`/`volume`, `fadeDuration`/`fade_duration_ms`/`fadeMs`, `loop`. |

#### getImageURL(index, kind)
Returns an HTTP URL for a table's image, the still, whatever the player's media priority is. For `"playfield"` that is the picture and never the video, which `getVideoURL` or `getMediaURL` returns. `kind` can be `"playfield"`, `"backglass"`, `"scoreview"`, `"wheel"`, or `"cab"`. Returns a fallback `/core/images/file_missing.png` URL if the file doesn't exist or `index` is outside the list, never `null`.

#### getVideoURL(index, kind)
Returns an HTTP URL for a table's video. `kind` can be `"playfield"`, `"backglass"`, or `"scoreview"`. Returns a fallback `/core/images/file_missing.png` URL if no video exists or `index` is outside the list, never `null`. See [Video Support](#video-support).

#### getMediaURL(index, kind)
Returns an HTTP URL using the user's configured media priority from Settings > Frontend > Presentation in the Manager (Playfield, Backglass, Score View and Real DMD Media Priority). For `"playfield"`, `"backglass"`, and `"scoreview"`, VPinFE chooses image or video first based on the setting and falls back to the alternate when the preferred file is missing. So `getMediaURL(i, "playfield")` returns the video when the game has one and the priority is video, which is the default, and `getImageURL(i, "playfield")` returns the still. A `<img>` given the first of those shows nothing for a game with a video; ask `getMedia` which it got, or use `getImageURL` for an `<img>`. For `"real_dmd"`, VPinFE chooses `realdmd-color.png` or `realdmd.png` first based on the setting and falls back to the other frame. It answers with a picture; a theme that wants the video asks for `"real_dmd_video"` or `"real_dmd_color_video"` by their own names. Returns `/core/images/file_missing.png` when neither exists or `index` is outside the list, never `null`.

Kind names are snake_case, the same strings the payload and `/api/v1` use, and at contract 2 they are the only names that answer. A contract 1 theme can still use the spellings earlier builds accepted: `bg`, `bg_video`, `dmd`, `dmd_video`, `table`, `table_video`, `fss`, `realdmd`, `realdmd-color`, `rulecard`, `audiolaunch` and `rulesheet`.

#### getMediaPages(index, kind)
Returns the URLs of every page of a kind, page 1 first, or `[]` when there is none. Only the flyer and the instruction card have more than one page; any other kind answers with its one URL. At contract 2 page 2 onward is the kind's URL with `?page=n` on it, and `entries[].pages` carries the count for a kind with two or more pages, so a theme can draw "1 / 3" without asking for them. Contract 1 has page 1 only. A page past the last answers 404.

#### getMedia(index, kind)
Returns the same priority-aware selection with metadata: `{ url, kind, priority, path }`. Real DMD selections also include `variant` with `"color"` or `"standard"`. When there is nothing to show, including for an `index` outside the list, `kind` is `"missing"` and `url` is `/core/images/file_missing.png`.

#### getAudioURL(index)
Returns an HTTP URL for a game's audio file, or `null` if no audio exists. See [Audio Support](#audio-support).

#### getManufacturerLogoURL(index)
Returns an HTTP URL for the game manufacturer's logo, or `null` if none is installed. Logos live in the manufacturer logos folder (`media.manufacturer_logos_dir`) and are matched to the game's `Info.Manufacturer` metadata, so "Williams Electronics" and "Williams" find the same file. Always handle `null`: a fresh install has no logos.

#### playTableAudio(indexOrUrl, retries=3)
Plays game audio via VPinFECore's centralized audio manager. Normally you pass `currentGameIndex`; passing a URL string is also supported.

#### stopTableAudio(options={})
Stops centralized audio playback. Default behavior is fade-out, or pass `{ immediate: true }` for an immediate stop.

#### enableCoreAudio(enabled=true)
Turns centralized core audio handling on or off for the current window.

#### isCoreAudioEnabled()
Returns `true` when centralized core audio handling is enabled.

#### setAudioOptions(options)
Updates centralized audio options at runtime: volume (`maxVolume`, `max_volume`, or `volume`), fade duration (`fadeDuration`, `fade_duration_ms`, or `fadeMs`), and `loop`.

#### enableCorePaging(enabled=true)
Turns core-handled wheel paging (`joypageup`/`joypagedown`) on or off. Disable it if your theme does its own paging; the actions then arrive in `handleInput`. Call it before you rely on those cases — while core paging is on it answers first, so the case never fires. See [Wheel Paging](#wheel-paging).

#### isCorePagingEnabled()
Returns `true` when core-handled wheel paging is enabled.

#### getPageIndex(direction="next", index=current)
Asks the backend where a page press should land and returns the target index. Convenience wrapper around the `get_page_index` API method for themes doing their own paging animation.

#### getTableMeta(index)
Returns the full object for a given index - an entry at contract 2, a game row at contract 1. The same object as `vpin.tableData[index]`. See [Entry Data Object](#entry-data-object).

#### getTableCount()
Returns the number of games in the current (possibly filtered) game list.

#### sendMessageToAllWindows(message)
Sends an event to all windows except the current one. Convenience wrapper around `vpin.call("send_event_all_windows", message)`.

#### sendMessageToAllWindowsIncSelf(message)
Sends an event to all windows including the current one and forwarding to iframes.

#### launchTable(index)
Suspends frontend keyboard/gamepad routing, calls backend to launch the selected game, then restores input after the launch lifecycle completes. The launch lifecycle is `TableLaunching` before the process starts, `TableRunning` when the table finishes loading, and `TableLaunchComplete` when it exits.

#### getTableData(reset=false)
Loads game data from the backend into `vpin.tableData`. Pass `reset=true` to reload from the full unfiltered game list.

#### handleEvent(message)
Handles incoming events with built-in logic for:
- `TableDataChange` (collection/filter/sort changes)
- centralized audio transitions on `TableIndexUpdate`, `TableLaunching`, `RemoteLaunching`, `TableLaunchComplete`, and `RemoteLaunchComplete`

Call this at the top of your `receiveEvent` function to get automatic data refresh and default audio behavior.

#### registerEventHandler(eventType, handler)
Registers a custom event handler for a specific event type. The handler is called whenever that event type is received via `handleEvent()`.

---

## Entry Data Object

Each element of `vpin.entries` is one table with the game it belongs to attached. The
fields are listed under [Contract 2 payload](#contract-2-payload); this section is about
reading them.

```javascript
const entry = vpin.getTableMeta(currentGameIndex);
const { game, table, media, assets, siblings } = entry;
```

> **Contract 1 serves a different shape** — a flat game row with the `.info` passed
> through as `meta`. None of it is served here. See
> [theme-contract-1.md](theme-contract-1.md).

### Reading game info

Everything a theme displays has a named home, so there is nothing to resolve or fall back
through:

```javascript
const entry = vpin.getTableMeta(currentGameIndex);

const title = entry.game.name;
const manufacturer = entry.game.manufacturer;
const year = entry.game.year;
const authors = entry.table.authors.join(', ');
const rating = entry.game.user.rating;
const plays = entry.game.user.play_count;
```

`game.name` is display-ready — a user-set alternate title has already replaced it, and a
leading "The " has already moved to the end so themes sort by the second word.

### Play stats

`game.user` counts the machine; `table.user` counts one `.vpx` of it. They accumulate
independently, so deleting a table does not un-play the game's hours.

```javascript
const { play_count, play_time_seconds, last_played } = entry.game.user;

const hours = Math.round(play_time_seconds / 360) / 10;
const lastPlayed = last_played ? new Date(last_played).toLocaleDateString() : 'Never';
```

Durations name their unit and timestamps are ISO 8601 UTC, whatever the `.info` happens to
store.

### Feature indicators

```javascript
const { detects } = vpin.getTableMeta(currentGameIndex).table;
const { alt_sound, alt_color, pup_pack } = vpin.getTableMeta(currentGameIndex).assets;

const lights = [
    ['Nfozzy', detects.nfozzy], ['Fleep', detects.fleep], ['SSF', detects.ssf],
    ['FastFlips', detects.fastflips], ['LUT', detects.lut],
    ['Scorbit', detects.scorbit], ['FlexDMD', detects.flex],
    ['PinMAME', detects.pinmame],
    ['AltSound', alt_sound], ['AltColor', alt_color], ['PuP-Pack', pup_pack],
];

lights.forEach(([label, on]) => {
    // Real booleans, so no coercion. `detects` describes the .vpx, `assets` the folder.
});
```

### Media

`entry.media` names the kinds this game has a file for. It does not carry paths — request
one from `vpin.endpoints.files`, the same server your theme was loaded from:

```javascript
const entry = vpin.getTableMeta(currentGameIndex);

function mediaURL(kind) {
    if (!entry.media.includes(kind)) return null;
    return `${vpin.endpoints.files}/media/${entry.table.id}/${kind}`;
}

const playfield = mediaURL('playfield_video') || mediaURL('playfield');
if (playfield) showMedia(playfield);
```

Checking `media` first is what tells you whether to show a video, an image, or nothing —
there is no request to make and no placeholder to detect. Responses carry an `ETag` and
ask to be revalidated, so replacing art in the Manager shows up without a hard refresh.

`vpin.getMedia(index, kind)`, `getImageURL` and `getVideoURL` still work and still honor
the user's media priority. Use them when you want that behavior; use `media` when you want
to know what exists.

### A game's other tables

`siblings` is how many tables the entry's game offers. `1` means there is nothing to
switch to, which is the common case — so a "other versions" affordance can hide itself
without asking the backend anything:

```javascript
const entry = vpin.getTableMeta(currentGameIndex);
if (entry.siblings > 1) {
    showVersionBadge(`${entry.siblings} versions`);
}
```

Whether a game contributes one entry or all of its tables is the user's `expanded` setting,
read as `vpin.expanded`. A theme does not have to do anything differently either way.

### VPinPlay Rating

While the VPinPlay extension runs, each game carries VPinPlay's cumulative rating. VPinFE answers it from VPinPlay's table list, which it reads every 30 minutes and keeps between runs, so a theme never waits on VPinPlay and a rating is never more than half an hour old. A game VPinPlay does not list has none.

| Method | Returns | Description |
|--------|---------|-------------|
| `await vpin.getVPinPlayRating(index?)` | `object\|null` | Returns the rating already attached to the table. |
| `await vpin.refreshVPinPlayRating(index?)` | `object\|null` | Asks VPinFE again, which answers from the latest list. |
| `vpin.getCachedVPinPlayRating(index?)` | `object\|null` | Returns only the cached value already attached to the table. |

The returned object matches the API payload shape and is also stored on the table entry as `table.vpinplay`:

```javascript
const table = vpin.getTableMeta(currentGameIndex);
const rating = table.vpinplay?.cumulativeRating ?? null;
const votes = table.vpinplay?.ratingCount ?? 0;
```

---

## Media Files

Media lives in a game's folder, in `medias/` or at the folder root. `medias/` is canonical
and the root is the fallback, at every tier below.

### The kinds

Twenty-two. `entry.media` lists the ones a game has a file for, using exactly these names, and
`/media/<table id>/<kind>` serves one.

| Kind | Default filename | Spec token | Also accepted |
|---|---|---|---|
| `playfield` | `table.png` | `(Playfield)` | |
| `playfield_fss` | `fss.png` | `(FSS)` | |
| `backglass` | `bg.png` | `(Backglass)` | |
| `scoreview` | `dmd.png` | `(DMD)` | |
| `wheel` | `wheel.png` | `(Wheel)` | |
| `logo` | `logo.png` | `(Logo)` | |
| `cab` | `cab.png` | `(Cabinet)` | |
| `flyer` | `flyer.png` | `(Flyer)` | `(GameInfo)` |
| `instruction_card` | `instructioncard.png` | `(InstructionCard)` | `(RuleCard)`, `(GameHelp)` |
| `topper` | `topper.png` | `(Topper)` | |
| `real_dmd` | `realdmd.png` | `(RealDMD)` | |
| `real_dmd_color` | `realdmd-color.png` | `(RealColorDMD)` | |
| `real_dmd_video` | `realdmd.mp4` | `(RealDMD)` | |
| `real_dmd_color_video` | `realdmd-color.mp4` | `(RealColorDMD)` | |
| `playfield_video` | `table.mp4` | `(Playfield)` | |
| `backglass_video` | `bg.mp4` | `(Backglass)` | |
| `scoreview_video` | `dmd.mp4` | `(DMD)` | |
| `topper_video` | `topper.mp4` | `(Topper)` | |
| `loading` | `loading.mp4` | `(Loading)` | |
| `audio` | `audio.mp3` | `(Audio)` | |
| `audio_launch` | `audiolaunch.mp3` | `(AudioLaunch)` | |
| `rule_sheet` | `rulesheet.pdf` | `(RuleSheet)` | |

`wheel` falls back to `logo` when the game has no wheel of its own. A game set to
full-single-screen serves `fss.png` as its `playfield` — the filename changes, the kind
name does not.

### How a file is chosen

Three tiers per kind, most specific first:

1. `(Token) <table stem>.<ext>` — art for one `.vpx`
2. `(Token) <folder name>.<ext>` — shared by every table in the folder
3. the default filename — what VPinMediaDB writes

```
Attack from Mars (Bally 1995)/
├── medias/
│   ├── (Wheel) Attack from Mars VR.png   tier 1 - only the VR table
│   ├── (Wheel) Attack from Mars.png      tier 2 - the folder's tables
│   ├── wheel.png                         tier 3 - the default
│   ├── table.png
│   ├── bg.png
│   └── audio.mp3
├── Attack from Mars.vpx
└── Attack from Mars VR.vpx
```

Within a tier the kind's extension family is tried in order, first hit wins:

| Family | Extensions |
|---|---|
| image | `.png` `.jpg` `.jpeg` `.webp` `.bmp` `.gif` |
| video | `.mp4` |
| audio | `.mp3` `.ogg` |
| document | `.pdf` `.md` `.txt` `.html` |

Matching is case-insensitive. A hand-placed `wheel.jpg` resolves; it does not have to be a
`.png`.

### Wheel sets

`wheel` supports named sets — a folder of alternate wheel art under
`medias/wheels/<set>/`. When a set is active it slots between tiers 1 and 2, so a
table-specific wheel still wins and a media refresh never beats the set. A theme picks the
set it wants with `wheelSet` in its `options.json`.

### Manufacturer logos

Manufacturer logos are art about the manufacturer, not about a game, so they are not a
media kind and are served at `/manufacturers/<slug>/logo`. `vpin.getManufacturerLogoURL(index)`
returns one, and `entry.game.manufacturer_logo` carries the web path. Neither says which
layer a logo came from or what kind of image it is, so use the URL as given.

### Reaching media from a theme

Two ways, and they answer different questions.

`entry.media` tells you **what exists**, with no request:

```javascript
const entry = vpin.getTableMeta(currentGameIndex);
const url = (kind) => entry.media.includes(kind)
    ? `${vpin.endpoints.files}/media/${entry.table.id}/${kind}`
    : null;

showMedia(url('playfield_video') || url('playfield'));
```

`vpin.getMedia(index, kind)` **resolves what to show**, honoring the user's Manager
media priority and falling back on its own:

```javascript
const media = vpin.getMedia(currentGameIndex, 'playfield');
// media.kind is "video", "image", or "missing"; media.url is always something to put in src
```

Use the first when your theme decides; use the second when the user's priority should.

## Video Support

Themes can display looping videos for table, backglass, and DMD screens in addition to (or instead of) static images.

For new themes, prefer `vpin.getMedia(index, kind)` or `vpin.getMediaURL(index, kind)` when you want to honor the user's media priority from the Manager. The default priority is video for table, backglass, and DMD media, and colorized for Real DMD frames. If the preferred file is missing, VPinFE automatically falls back to the available alternate.

Priority-aware example:
```javascript
const media = vpin.getMedia(currentGameIndex, 'backglass');
const preview = document.createElement(media.kind === 'video' ? 'video' : 'img');
preview.className = 'preview';
preview.src = media.url;

if (media.kind === 'video') {
    preview.autoplay = true;
    preview.loop = true;
    preview.muted = true;
    preview.playsInline = true;
}

container.appendChild(preview);
```

Use `vpin.getVideoURL(index, type)` to get the video URL. The method returns a fallback `file_missing` URL if no video file exists, so check for this before creating a `<video>` element.

Example with image fallback:
```javascript
const videoUrl = vpin.getVideoURL(currentGameIndex, 'playfield');
const imageUrl = vpin.getImageURL(currentGameIndex, 'playfield');

if (videoUrl && !videoUrl.includes('file_missing')) {
    const preview = document.createElement('video');
    preview.className = 'preview';
    preview.poster = imageUrl;  // stable dimensions while video loads
    preview.src = videoUrl;
    preview.autoplay = true;
    preview.loop = true;
    preview.muted = true;
    preview.playsInline = true;
    // Fall back to image if video fails to load
    preview.onerror = () => {
        const fallback = document.createElement('img');
        fallback.className = 'preview';
        fallback.src = imageUrl;
        preview.replaceWith(fallback);
    };
    container.appendChild(preview);
} else {
    const preview = document.createElement('img');
    preview.className = 'preview';
    preview.src = imageUrl;
    container.appendChild(preview);
}
```

For `backglass` and `scoreview` windows, use the same pattern with:

- `vpin.getVideoURL(currentGameIndex, 'backglass')` plus `vpin.getImageURL(currentGameIndex, 'backglass')`
- `vpin.getVideoURL(currentGameIndex, 'scoreview')` plus `vpin.getImageURL(currentGameIndex, 'scoreview')`

Recommended rule for theme authors:

- `playfield` window: optionally prefer `table.mp4` over `table.png` / `fss.png`
- `backglass` window: prefer `bg.mp4`, fall back to `bg.png`
- `scoreview` window: prefer `dmd.mp4`, fall back to `dmd.png`

If you only use `getImageURL()` in `backglass` or `scoreview` renderers, those windows will remain image-only even when the matching video files exist.

Key points:
- Set `muted = true` — browsers require this for autoplay to work without user gesture.
- Set `poster = imageUrl` — gives the video element proper dimensions before metadata loads, preventing layout shifts.
- The `onerror` handler provides a graceful fallback to the static image.
- You can check `vpin.tableData[index].PlayfieldVideoPath` directly to decide whether to create a video or image element.

---

## Audio Support

VPinFECore now includes a centralized per-game audio manager. Theme code can use it directly and no longer needs to implement its own `Audio`/fade/retry logic.

### Audio File

Place an `audio.mp3` file in the table's `medias/` folder (or root folder). `vpin.getAudioURL(index)` returns the URL, or `null` if no audio file exists.

### Core Behavior

On the controller, `await vpin.handleEvent(message)` automatically manages audio transitions when core audio is enabled.

Core audio is on by default for a contract 2 theme, and off for a contract 1 theme until it enables it in its `config.json` (or calls `vpin.enableCoreAudio(true)` at runtime). A theme that plays its own audio sets `audio.enabled` to `false`.

When enabled, these transitions are handled automatically:
- `TableIndexUpdate` -> play selected game audio
- `TableLaunching` and `RemoteLaunching` -> fade/stop audio
- `TableLaunchComplete` and `RemoteLaunchComplete` -> resume audio for current selection
- `TableDataChange` (with `index`) -> play audio for that index

### Backend vs Frontend Responsibility

`api.py` knows when table launch starts/completes and emits lifecycle events, but it does not own the browser `Audio` object. Actual playback, fading, retries, and autoplay-policy handling must run in frontend JavaScript (`VPinFECore`/theme code), which owns the in-memory audio state.

### Practical Note: Self-Event Caveat

`vpin.sendMessageToAllWindows(...)` excludes the sender. If your playfield window sends `TableLaunching`, it might not receive that same event back, so backend-emitted lifecycle events are the reliable source of truth for launch state.

For robust behavior, it is valid to also call:
- `vpin.stopTableAudio()` directly in your local `joyselect`/launch path
- `vpin.playTableAudio(currentGameIndex)` directly on local launch-complete handling

This explicit local stop/resume acts as a safety net while still using centralized core audio.

Defaults:
- volume: `0.8`
- fade duration: `500ms`
- loop: `true`

### Usage in Your Theme

```javascript
function updateScreen() {
    // ... update images, carousel, etc ...
    if (vpin.isController()) {
        vpin.playTableAudio(currentGameIndex);
    }
}

async function receiveEvent(message) {
    // Keep this call at the top for built-in table-data refresh and core audio handling
    await vpin.handleEvent(message);
    // ... theme-specific event handling ...
}

// Optional immediate stop:
// vpin.stopTableAudio({ immediate: true });
```

### Configuration Schema (`options.json`)

If your theme wants options a person can change, add an `options.json` file to the theme root.
A theme on contract 1 keeps the same file under its old name, `theme.json`; a contract 2
theme that ships only `theme.json` has no options, and the log says so once.

The file declares the options and their defaults, and nothing else. VPinFE builds the
configuration dialog from it and keeps what a person chooses in their own settings, outside
the theme, so updating the theme never resets a choice.

Example:

```json
{
  "title": "Carousel Desktop Options",
  "description": "These options control layout and audio behavior.",
  "options": [
    {
      "key": "wheel.scale",
      "name": "Wheel Scale",
      "description": "Controls the wheel image scale multiplier.",
      "type": "number",
      "default": 1,
      "min": 0.5,
      "max": 2,
      "step": 0.1
    },
    {
      "key": "showClock",
      "name": "Show Clock",
      "description": "Show the clock overlay in the playfield window.",
      "type": "boolean",
      "default": true
    },
    {
      "key": "audio.mode",
      "name": "Audio Mode",
      "description": "Select how the theme should handle table audio.",
      "type": "select",
      "default": "core",
      "options": ["off", "core", "theme"]
    }
  ]
}
```

Full sample `options.json` for quick testing:

```json
{
  "title": "Sample Theme Options",
  "description": "Example configurable options exposed through the VPinFE Themes page.",
  "options": [
    {
      "key": "showClock",
      "name": "Show Clock",
      "description": "Show a clock overlay on the playfield screen.",
      "type": "boolean",
      "default": true
    },
    {
      "key": "headerTitle",
      "name": "Header Title",
      "description": "Text displayed in the theme header.",
      "type": "text",
      "default": "My Custom Theme"
    },
    {
      "key": "footerMessage",
      "name": "Footer Message",
      "description": "Multi-line text shown at the bottom of the screen.",
      "type": "textarea",
      "default": "Welcome to VPinFE\nPress Start to Play"
    },
    {
      "key": "wheel.scale",
      "name": "Wheel Scale",
      "description": "Scale multiplier for wheel art.",
      "type": "number",
      "default": 1,
      "min": 0.5,
      "max": 2,
      "step": 0.1
    },
    {
      "key": "themeMode",
      "name": "Theme Mode",
      "description": "Choose the overall layout style.",
      "type": "select",
      "default": "arcade",
      "options": [
        "minimal",
        "arcade",
        "modern"
      ]
    },
    {
      "key": "accentColor",
      "name": "Accent Color",
      "description": "Hex color used for highlights.",
      "type": "text",
      "default": "#ffd84d"
    },
    {
      "key": "audio.enabled",
      "name": "Enable Audio",
      "description": "Turn theme-controlled audio behavior on or off.",
      "type": "boolean",
      "default": true
    },
    {
      "key": "audio.maxVolume",
      "name": "Audio Max Volume",
      "description": "Maximum playback volume for theme audio.",
      "type": "number",
      "default": 0.8,
      "min": 0,
      "max": 1,
      "step": 0.05
    },
    {
      "key": "advancedRules",
      "name": "Advanced Rules JSON",
      "description": "Raw JSON for advanced theme behavior.",
      "type": "json",
      "default": {
        "showTop10": true,
        "animateWheel": false,
        "videoFadeMs": 750
      }
    }
  ]
}
```

Supported field types in `options.json`:

- `text`
- `textarea`
- `number`
- `boolean`
- `select`
- `json`

Notes:

- `key` is required and identifies the value returned through `get_theme_config()`. Dot notation such as `audio.enabled` creates nested objects in the returned config.
- `name` is the display label shown in the Manager. If omitted, the `key` is shown.
- `description` is shown as help text in the dialog.
- `default` is what the option is until a person changes it. Their choice is kept in their own settings and is never written back into this file.
- `value` is the older spelling of `default`, from when this file also held the chosen values. It is still read as the default when `default` is absent.
- `select` options may be simple scalar values or `{label, value}` objects.
- A `select` may set `"source": "wheelsets"` instead of a static `options` list. The Manager then fills the dropdown from the library: every wheel set folder found under any table's `medias/wheels/`, plus the built-in `logo` set, plus a Default entry (saved as `""`, meaning no set). Use the key `wheelSet`; a non-empty saved value overrides the `[Media] wheelset` setting from `vpinfe.ini` while this theme is active.

```json
{
  "key": "wheelSet",
  "name": "Wheel Set",
  "description": "Which wheel art set this theme uses.",
  "type": "select",
  "source": "wheelsets",
  "default": ""
}
```

### Values Returned To Theme Code

Themes read their configuration through `get_theme_config()`, which returns one plain values object built from three sources, each narrowing the one before:

1. `config.json`, the author's fixed settings, including the [capability](#what-core-does-for-you) blocks.
2. The defaults declared in `options.json` (`theme.json` at contract 1).
3. What the person chose, kept in their own settings.

A theme with a `config.json` and no options still gets its `config.json` values back.

For the sample schema above, `get_theme_config()` would return an object like this:

```json
{
  "showClock": true,
  "headerTitle": "My Custom Theme",
  "footerMessage": "Welcome to VPinFE\nPress Start to Play",
  "wheel": {
    "scale": 1
  },
  "themeMode": "arcade",
  "accentColor": "#ffd84d",
  "audio": {
    "enabled": true,
    "maxVolume": 0.8
  },
  "advancedRules": {
    "showTop10": true,
    "animateWheel": false,
    "videoFadeMs": 750
  }
}
```

Core audio can be configured from theme config:

```json
{
  "use_core_audio": true,
  "audio": {
    "enabled": true,
    "maxVolume": 0.8,
    "fadeDuration": 500,
    "loop": true
  }
}
```

If omitted, core audio is on for a contract 2 theme and off for a contract 1 theme. Set
`audio.enabled` to `false` to play your own.

Also accepted for compatibility:
- `useCoreAudio` (camelCase)
- `audio.max_volume` or `audio.volume`
- `audio.fade_duration_ms` or `audio.fadeMs`

---

## CSS Patterns

### Fade Transitions

Two common patterns for fade-to-black transitions during table launch:

**Pattern 1: Fade container** (used by Carousel Desktop) — wrap all content in a container with `opacity` transition:
```css
#fadeContainer {
  position: fixed;
  top: 0; left: 0;
  width: 100vw; height: 100vh;
  transition: opacity 4.5s ease-in-out;
  opacity: 1;
}
```

**Pattern 2: Fade overlay** (used by Slider Video) — a fixed black overlay toggled via CSS class:
```css
#fadeOverlay {
  position: fixed;
  top: 0; left: 0;
  width: 100%; height: 100%;
  background: black;
  opacity: 0;
  transition: opacity 0.8s ease-in-out;
  pointer-events: none;
  z-index: 9999;
}
#fadeOverlay.show {
  opacity: 1;
}
```

### Base Styles

Recommended base styles to prevent white flash and scrollbars:
```css
html, body {
  margin: 0;
  padding: 0;
  height: 100%;
  overflow: hidden;
  background-color: black;
  color: white;
}
```

### Secondary Windows

For `backglass` and `scoreview` windows that show a single fullscreen image:
```css
.fullscreen-image-container {
  width: 100%;
  height: 100%;
  position: relative;
  overflow: hidden;
}
.fullscreen-image-container img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: opacity 0.5s ease-in-out;
}
```
