# Contract 1 reference

The payload shape 2.x themes were written against, kept for every theme that does not
ask for 3.0. Nothing here is going to change — that is the point of a contract. If you
are writing a new theme, read [theme.md](theme.md) instead; everything below is the older
shape and none of it is served at contract 2.

A theme gets contract 1 by saying nothing at all, or by naming a VPinFE older than 3.0:

```json
{ "name": "My Theme" }
```

The `vpin.*` API, the window messages, input handling and CSS are identical at both
contracts, and [theme.md](theme.md) is the reference for all of it. Only the payload and
the window names differ, and both are covered here.

## Windows

Three, named `table`, `bg` and `dmd`. The main screen is `table`, so the file is
`index_table.html` and the page is loaded with `?window=table`. At contract 2 that window
is called `playfield`.

## The game list

`vpin.tableData` is a flat array — one entry per game, addressed by integer index. There is
no collection wrapper and no per-table entry; a game with three `.vpx` files appears once,
represented by its default table.

### Identity and paths

Every path here is a **filesystem** path, not a URL. `vpin.getImageURL(index, kind)` and
its siblings are what turn one into something a browser can load, and are what a theme
should normally call.

| Property | Type | Description |
|----------|------|-------------|
| `tableDirName` | `string` | The game's directory name. |
| `fullPathTable` | `string` | Absolute path to the game folder. |
| `fullPathVPXfile` | `string` | Absolute path to the default `.vpx`. |

### Media paths

`null` when the game has no file of that kind.

| Property | File | Description |
|----------|------|-------------|
| `TableImagePath` | `table.png` | Playfield image. |
| `FSSImagePath` | `fss.png` | Full-single-screen playfield image, when the game has one. |
| `BGImagePath` | `bg.png` | Backglass image. |
| `DMDImagePath` | `dmd.png` | DMD image. |
| `WheelImagePath` | `wheel.png` | Wheel image. |
| `CabImagePath` | `cab.png` | Cabinet image. |
| `LogoImagePath` | `logo.png` | Game logo, used as the wheel's fallback. |
| `FlyerImagePath` | `flyer.png` | Flyer art. |
| `InstructionCardImagePath` | `instructioncard.png` | Apron instruction card. |
| `TopperPath` | `topper.png` | Topper image. |
| `realDMDImagePath` | `realdmd.png` | Real-DMD frame. |
| `realDMDColorImagePath` | `realdmd-color.png` | Colorized real-DMD frame. |
| `TableVideoPath` | `table.mp4` | Playfield video. |
| `BGVideoPath` | `bg.mp4` | Backglass video. |
| `DMDVideoPath` | `dmd.mp4` | DMD video. |
| `TopperVideoPath` | `topper.mp4` | Topper video. |
| `LoadingVideoPath` | `loading.mp4` | Loading-screen video. |
| `AudioPath` | `audio.mp3` | Per-game audio. |
| `AudioLaunchPath` | `audiolaunch.mp3` | Audio played on launch. |
| `RuleSheetPath` | `rulesheet.pdf` | Rule sheet document. |
| `ManufacturerLogoPath` | — | **Web** path, `/manufacturers/<slug>/logo`, unlike every other `*Path` here. Use `vpin.getManufacturerLogoURL(index)`. |

### Addon flags

| Property | Type | Description |
|----------|------|-------------|
| `altSoundExists` | `boolean` | An AltSound pack is installed for this game. |
| `altColorExists` | `boolean` | An AltColor pack is installed. |
| `pupPackExists` | `boolean` | A PuP-Pack is installed. |

### `vpinplay`

Not present until something fetches it. `vpin.getVPinPlayRating(index)` attaches the
cached rating payload to the row; before that the property does not exist. See
[theme.md](theme.md#vpinplay-rating).

## `meta` — the game's `.info`

`meta` is the game's `.info` file passed through, with one adjustment: `Title` is display-ready
rather than the stored value. A table's own `.info` beside its `.vpx` is read into the game's record and
not passed through as a file of its own. Contract 2 does not serve `meta` at all, because
passing storage through meant a storage change reached themes whether or not it meant
anything to them.

### meta.Info

| Property | Type | Description |
|----------|------|-------------|
| `Title` | `string` | Display name. **Not the stored title** — a user-set alternate replaces it, and otherwise a leading "The " moves to the end so themes sort by the second word ("The Addams Family" arrives as "Addams Family, The"). |
| `Manufacturer` | `string` | e.g. "Williams", "Bally". |
| `Year` | `string` | Year of manufacture. |
| `Type` | `string` | `"SS"` solid state, `"EM"` electro-mechanical, `"PM"` pure mechanical. |
| `Authors` | `array` | VPX table author names. |
| `Theme` | `string` | Game theme or category. |
| `Rom` | `string` | ROM name. |

### meta.User

Per-user stats. The `.info` stores them in the units Visual Pinball's `FileLayout.md` names (rating 0 to 10, an ISO date, seconds); contract 1 serves them as 2.x did, in the units below.

| Property | Type | Description |
|----------|------|-------------|
| `Rating` | `number` | `0` to `5`. |
| `Favorite` | `number` | `0` or `1`. |
| `LastRun` | `number\|null` | Unix timestamp in seconds, or `null` if never played. |
| `StartCount` | `number` | Times launched. |
| `RunTime` | `number` | Accumulated play time in minutes. |
| `Tags` | `array` | User-defined tags. |

### meta.VPXFile

The game's default table, and only that one. A game folder holding several `.vpx` files
still reports one here — which is the limitation contract 2's entry list exists to remove.

| Property | Type | Description |
|----------|------|-------------|
| `filename` | `string` | VPX filename. |
| `id` | `string` | The table's id, stable across renames. |
| `rom` | `string` | ROM name. Also copied to `Info.Rom`. |
| `version` | `string` | Table version from the VPX metadata. |
| `manufacturer` | `string` | Manufacturer from the VPX metadata, which may disagree with `Info`. |
| `year` | `string` | Year from the VPX metadata. |
| `type` | `string` | Table type from the VPX metadata. |

Everything except `filename` and the detection flags is passed through from storage, so a
key is absent when the `.vpx` did not carry it. Read defensively.

Detection flags ride on `VPXFile` too, spelled without a separator. Contract 2 spells the
same flags snake_case under `entries[].table.detects`.

| Property | Description |
|----------|-------------|
| `detectnfozzy` | Nfozzy physics. |
| `detectfleep` | Fleep sound package. |
| `detectssf` | Surround sound feedback. |
| `detectfastflips` | FastFlips. |
| `detectlut` | LUT color correction. |
| `detectscorebit` | ScoreBit integration. |
| `detectflex` | FlexDMD. |
| `detectpinmame` | PinMAME. |

The three addon flags appear here as well as on the row, because they rode along with
`VPXFile` historically. They describe the folder, not the file.

### meta.VPinFE

VPinFE's own bookkeeping.

| Property | Type | Description |
|----------|------|-------------|
| `game_id` | `string` | The game's id. |
| `default_table` | `string` | Which `.vpx` the single-table view represents. |
| `schema` | `number` | The `.info` storage schema. Storage, not a theme concern — it is not the theme contract, and the two numbers move independently. |

Keys VPinFE writes for its own use appear here too, spelled as 2.x spelled them
(`alttitle`, `altlauncher`, `pluginprofile` and so on).

## Examples

### Reading game info

```javascript
const game = vpin.getTableMeta(currentGameIndex);
const info = game.meta.Info || {};
const user = game.meta.User || {};
const vpx = game.meta.VPXFile || {};

const title = info.Title || vpx.filename || game.tableDirName || 'Unknown Game';
const manufacturer = info.Manufacturer || vpx.manufacturer || 'Unknown';
const year = info.Year || vpx.year || '';
const authors = Array.isArray(info.Authors) && info.Authors.length
    ? info.Authors.join(', ') : 'Unknown';
const rating = Number(user.Rating || 0);
const plays = Number(user.StartCount || 0);
```

### Feature indicator lights

```javascript
const vpx = vpin.getTableMeta(currentGameIndex).meta.VPXFile || {};

const features = [
    { key: "detectnfozzy", label: "Nfozzy" },
    { key: "detectfleep", label: "Fleep" },
    { key: "detectssf", label: "SSF" },
    { key: "detectfastflips", label: "FastFlips" },
    { key: "detectlut", label: "LUT" },
    { key: "detectscorebit", label: "ScoreBit" },
    { key: "detectflex", label: "FlexDMD" },
    { key: "altSoundExists", label: "AltSound" },
    { key: "altColorExists", label: "AltColor" },
    { key: "pupPackExists", label: "PuP-Pack" },
];

features.forEach(({ key, label }) => {
    // The flags arrive as real booleans now, but older builds wrote strings.
    const isOn = vpx[key] === true || vpx[key] === "true" || vpx[key] === 1;
    // Light the indicator based on isOn.
});
```

### Deciding between video and image

```javascript
// The path properties are worth reading directly for exactly this - asking whether a
// file exists, rather than building a URL by hand.
if (game.TableVideoPath) {
    showVideo(vpin.getVideoURL(currentGameIndex, 'table'));
} else {
    showImage(vpin.getImageURL(currentGameIndex, 'table'));
}
```

## Moving to contract 2

Declaring `"min_vpinfe": "3.0"` changes three things: the payload becomes an entry list, `meta`
is removed in favor of named fields, and the main window is called `playfield`. The
renamed `vpin.*` members work at both contracts, so a theme can be renamed first and
switched afterwards. [theme.md](theme.md) is the reference.
