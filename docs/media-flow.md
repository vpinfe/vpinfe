# Media Flow

> Per-table media resolution is not covered here yet. A game folder can hold several
> tables, each able to have its own art, and `common/games/media_lookup.py` is what
> resolves that. This page still describes the game-level flow correctly.


This document explains how per-game images, videos, and audio move from the filesystem into a VPinFE theme.

## Overview

Themes do not receive image or video bytes directly over the WebSocket bridge. Instead, the backend sends media file paths as part of the game metadata payload, and `vpinfe-core.js` converts those filesystem paths into HTTP URLs that theme code can use in `<img>`, `<video>`, and `<audio>` elements.

High-level flow:

1. `common/games/game_parser.py` scans each game folder for standard media filenames.
2. `frontend/api.py` includes the discovered file paths in the game JSON returned to the browser.
3. `frontend/static/common/vpinfe-core.js` stores that game data in `this.tableData`.
4. Theme code calls helper methods such as `vpin.getImageURL(index, type)` or `vpin.getVideoURL(index, type)`.
5. `vpinfe-core.js` converts the local path into a URL under `/games/...`.
6. The local HTTP server serves the file to the theme.

## Media Discovery

`common/games/game_parser.py` checks the game's `medias/` subfolder first, then falls back to the game folder root.

Standard filenames include:

- Images: `table.png`, `fss.png`, `bg.png`, `dmd.png`, `wheel.png`, `logo.png`, `cab.png`, `realdmd.png`, `realdmd-color.png`, `flyer.png`, `instructioncard.png`, `topper.png`
- Videos: `table.mp4`, `fss.mp4`, `bg.mp4`, `dmd.mp4`, `realdmd.mp4`, `realdmd-color.mp4`, `topper.mp4`, `loading.mp4`
- Audio: `audio.mp3`, `audiolaunch.mp3`
- Documents: `rulesheet.pdf` (also `.md`, `.txt`, `.html`, or a picture of the sheet such as `rulesheet.png`)

A device can also record its own screens into these slots (`POST /api/v1/capture/runs`,
`docs/http-api.md` "Recording"): the playfield, backglass and DMD as pictures and videos,
and the sound as `audio.mp3`, written through the same placement as an upload, with the
origin `capture` in the `.info` ledger. Recorded videos are silent `.mp4`, H.264 or, where
the device's browser plays no H.264, VP9; the playfield is stored with its bottom at the
right unless Playfield Orientation says otherwise.

Each kind also accepts the rest of its extension family (for images: `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.gif`, `.apng`), and spec-named files outrank the fixed names: `(Wheel) <table name>.png` beats `(Wheel) <folder name>.png` beats `wheel.png`. The full precedence rules live in `common/media/media_specs.py`.

### Media tokens

The token in brackets names the kind. Visual Pinball publishes these in its own [FileLayout.md](https://github.com/vpinball/vpinball/blob/master/docs/FileLayout.md), under "Zero install table deployment guidelines", and VPinFE reads them:

| Kind | Token | Kind | Token |
|---|---|---|---|
| Playfield | `(Playfield)` | Wheel | `(Wheel)` |
| Backglass | `(Backglass)` | Logo | `(Logo)` |
| DMD | `(DMD)` | Instruction card | `(InstructionCard)` |
| Real DMD | `(RealDMD)` | Game flyer | `(Flyer)` |
| Real color DMD | `(RealColorDMD)` | Rulesheet | `(RuleSheet)` |
| Topper | `(Topper)` | Loading video | `(Loading)` |
| Cabinet | `(Cabinet)` | Audio | `(Audio)` |
| FSS | `(FSS)` | Launch audio | `(AudioLaunch)` |

A video uses its image counterpart's token and is told apart by extension, so `(Topper) Name.png` and `(Topper) Name.mp4` are different kinds.

Two of these differ from the published names. Visual Pinball calls the instruction card `(GameHelp)` and the game flyer `(GameInfo)`; VPinFE leads with `(InstructionCard)` and `(Flyer)` because they say what the file is, and **still accepts `(GameHelp)` and `(GameInfo)`** so media packaged either way works. `(RuleCard)`, which VPinFE recommended earlier in 3.0 development, is accepted too. If both are present the preferred name wins, but a table-specific file always beats a folder-level one whichever token it uses. `(Cabinet)`, `(FSS)`, `(Logo)` and `(RuleSheet)` have no published equivalent and are VPinFE's own.

The playfield is one kind with two variants. `playfield` resolves `table.png` or `fss.png` depending on `[Media] playfieldvariant`; `playfield_fss` is the FSS render on its own, addressable whichever variant is active, and is what the playfield falls back to when it has no render of its own. Full Single Screen is a Visual Pinball view mode, so an `fss.png` is the same subject as a `table.png` photographed differently - not a separate kind.

### Manufacturer logos

Manufacturer logos are not per-game media: they live in their own folder (`media.manufacturer_logos_dir`, defaulting to `manufacturer_logos/` under the config dir) and are served at `/manufacturers/<slug>/logo`. The folder is flat: one image per manufacturer, named for it. Lookup ignores case, punctuation and spaces and drops corporate suffixes (Inc, Co, GmbH, LLC, Ltda, S.A. and the like), so a game whose `Info.Manufacturer` is "Williams Electronics" finds `williams.png`, `Williams.PNG` or `Williams Electronics.png`, and "Game Plan" finds `GamePlan.png`; a `manufacturers.json` alias map covers the exceptions (one ships in the code, Taito do Brasil to Taito, and a line in the file overrides it). The game payload carries the result as `ManufacturerLogoPath`, and themes call `vpin.getManufacturerLogoURL(index)`.

You never have to guess a filename: VPinFE generates `manufacturers-reference.json` in that folder from VPSdb (refreshed at startup and on every VPSdb sync) listing every known manufacturer with its computed slug, the alias currently applied, and the logo that resolves — `logo: null` rows are what's missing. The same data is served at `GET /api/v1/manufacturers`, with per-manufacturer library game counts. To supply your own logo, either name the file after the manufacturer and drop it in the folder, or name it however you like and add one line to `manufacturers.json` beside it (`{"Premier Technology": "my-file-stem"}`). Alias map keys accept the full name or its slug, and keys that differ only in spaces, hyphens or case are the same key (the last one wins); values are file stems; empty values are ignored.

### Wheel sets

A game can carry alternate wheel art in named folders under `medias/wheels/<set name>/`, each set resolving through the same precedence chain. The active set is **Wheel Set** in Settings (`media.wheelset` in `vpinfe.json`), which the active theme may override through a `wheelSet` option (see [theme.md](theme.md)). The reserved set name `logo` fills the wheel slot from the game's logo instead of a wheels folder. A spec-named wheel the user placed always beats the active set, and a game with no art for the set keeps its plain wheel.

Relevant code:

- [common/games/game_parser.py](../common/games/game_parser.py)

## Backend To Browser Payload

When the browser requests game data, `frontend/api.py` serializes the discovered media paths into each game entry. These are plain string paths such as:

- `PlayfieldImagePath`
- `BGImagePath`
- `DMDImagePath`
- `PlayfieldVideoPath`
- `BGVideoPath`
- `DMDVideoPath`
- `AudioPath`

Relevant code:

- [frontend/api.py](../frontend/api.py)

## Theme Access In vpinfe-core.js

After loading the game JSON, `vpinfe-core.js` exposes helper methods for themes:

- `vpin.getImageURL(index, type)`
- `vpin.getVideoURL(index, type)`
- `vpin.getAudioURL(index)`
- `vpin.getTableMeta(index)`

Relevant code:

- [frontend/static/common/vpinfe-core.js](../frontend/static/common/vpinfe-core.js)
- [frontend/static/common/vpinfe-core.js](../frontend/static/common/vpinfe-core.js)
- [frontend/static/common/vpinfe-core.js](../frontend/static/common/vpinfe-core.js)

## URL Conversion

`vpinfe-core.js` converts a local media path into a URL that points at the built-in HTTP server.

Examples:

- `/games/Addams Family/medias/dmd.mp4`
- `http://127.0.0.1:<httpport>/tables/Addams%20Family/medias/dmd.mp4`

If the file lives directly in the game folder instead of `medias/`, the URL becomes:

- `http://127.0.0.1:<httpport>/tables/<gameDir>/<file>`

Relevant code:

- [frontend/static/common/vpinfe-core.js](../frontend/static/common/vpinfe-core.js)

The HTTP server mount that makes this work is configured here:

- [main.py](../main.py)

## DMD Video Flow

For `dmd.mp4` support specifically, the key flow is:

- [common/games/game_parser.py](../common/games/game_parser.py)
- [frontend/api.py](../frontend/api.py)
- [frontend/static/common/vpinfe-core.js](../frontend/static/common/vpinfe-core.js)
- [frontend/static/common/vpinfe-core.js](../frontend/static/common/vpinfe-core.js)

That means:

1. `common/games/game_parser.py` finds `dmd.mp4` and stores it as `DMDVideoPath`.
2. `frontend/api.py` includes `DMDVideoPath` in the game payload.
3. `vpin.getVideoURL(index, "dmd")` reads `game.DMDVideoPath`.
4. `#convertPathToURL()` maps that path to `http://127.0.0.1:<port>/tables/<game>/medias/dmd.mp4`.

## Playfield Video Flow

For playfield video support, the key flow is:

- [common/games/game_parser.py](../common/games/game_parser.py)
- [frontend/api.py](../frontend/api.py)
- [frontend/static/common/vpinfe-core.js](../frontend/static/common/vpinfe-core.js)
- [frontend/static/common/vpinfe-core.js](../frontend/static/common/vpinfe-core.js)

That means:

1. `common/games/game_parser.py` finds `table.mp4` or `fss.mp4` and stores it as `PlayfieldVideoPath`.
2. `frontend/api.py` includes `PlayfieldVideoPath` in the game payload.
3. `vpin.getVideoURL(index, "table")` reads `game.PlayfieldVideoPath`.
4. `#convertPathToURL()` maps that path to a `/tables/.../medias/<file>.mp4` URL.

## Backglass Video Flow

For `bg.mp4` support specifically, the key flow is:

- [common/games/game_parser.py](../common/games/game_parser.py)
- [frontend/api.py](../frontend/api.py)
- [frontend/static/common/vpinfe-core.js](../frontend/static/common/vpinfe-core.js)
- [frontend/static/common/vpinfe-core.js](../frontend/static/common/vpinfe-core.js)

That means:

1. `common/games/game_parser.py` finds `bg.mp4` and stores it as `BGVideoPath`.
2. `frontend/api.py` includes `BGVideoPath` in the game payload.
3. `vpin.getVideoURL(index, "bg")` reads `game.BGVideoPath`.
4. `#convertPathToURL()` maps that path to `http://127.0.0.1:<port>/tables/<game>/medias/bg.mp4`.

## Fallback Behavior

`vpinfe-core.js` does not automatically choose between video and image for a theme. It only exposes both URLs.

That means fallback behavior is the theme's responsibility:

- Playfield screen: prefer `table.mp4` or `fss.mp4`, fall back to `table.png` or `fss.png`
- BG screen: prefer `bg.mp4`, fall back to `bg.png`
- DMD screen: prefer `dmd.mp4`, fall back to `dmd.png`

If the theme only calls `vpin.getImageURL()`, it will remain image-only even when the matching video file exists.

If the theme uses `vpin.getVideoURL()`, it should check whether the returned URL is usable. When no video exists, `getVideoURL()` returns the fallback missing-file URL rather than `null`.

## Theme Pattern: Prefer Video, Fall Back To Image

Typical theme usage is to ask for both the video and image for a screen and then decide what to render.

```js
const index = vpin.getCurrentTableIndex();
const playfieldVideoUrl = vpin.getVideoURL(index, "table");
const playfieldImageUrl = vpin.getImageURL(index, "table");
const bgVideoUrl = vpin.getVideoURL(index, "bg");
const bgImageUrl = vpin.getImageURL(index, "bg");
const dmdVideoUrl = vpin.getVideoURL(index, "dmd");
const dmdImageUrl = vpin.getImageURL(index, "dmd");
```

### DMD Example

Typical theme usage is to ask for both the DMD video and DMD image and then decide what to render.

```js
const index = vpin.getCurrentTableIndex();
const dmdVideoUrl = vpin.getVideoURL(index, "dmd");
const dmdImageUrl = vpin.getImageURL(index, "dmd");
```

A practical pattern is:

```html
<div class="dmd-stage">
  <video id="dmd-video" muted autoplay loop playsinline hidden></video>
  <img id="dmd-image" alt="DMD preview">
</div>
```

```js
function updateDmdMedia(index) {
  const videoEl = document.getElementById("dmd-video");
  const imageEl = document.getElementById("dmd-image");

  const dmdVideoUrl = vpin.getVideoURL(index, "dmd");
  const dmdImageUrl = vpin.getImageURL(index, "dmd");

  videoEl.onerror = () => {
    videoEl.hidden = true;
    imageEl.hidden = false;
    imageEl.src = dmdImageUrl;
  };

  if (dmdVideoUrl && !dmdVideoUrl.endsWith("/file_missing.png")) {
    videoEl.src = dmdVideoUrl;
    videoEl.hidden = false;
    imageEl.hidden = true;
    videoEl.load();
  } else {
    videoEl.removeAttribute("src");
    videoEl.hidden = true;
    imageEl.hidden = false;
    imageEl.src = dmdImageUrl;
  }
}
```

### Reusable Pattern For Playfield, BG, And DMD

```js
function hasUsableMedia(url) {
  return Boolean(url) && !String(url).includes("file_missing");
}

function renderWindowMedia(container, imageUrl, videoUrl, altText) {
  const existingMedia = container.querySelector("video, img");
  const wantsVideo = hasUsableMedia(videoUrl);

  if (existingMedia) {
    if (existingMedia.tagName === "VIDEO") {
      existingMedia.pause();
      existingMedia.removeAttribute("src");
      existingMedia.load();
    }
    existingMedia.remove();
  }

  if (wantsVideo) {
    const video = document.createElement("video");
    video.src = videoUrl;
    video.poster = hasUsableMedia(imageUrl) ? imageUrl : "";
    video.autoplay = true;
    video.loop = true;
    video.muted = true;
    video.playsInline = true;
    video.onerror = () => {
      if (!hasUsableMedia(imageUrl)) return;
      const fallback = document.createElement("img");
      fallback.src = imageUrl;
      fallback.alt = altText;
      video.replaceWith(fallback);
    };
    container.appendChild(video);
    return;
  }

  const img = document.createElement("img");
  img.src = hasUsableMedia(imageUrl) ? imageUrl : "";
  img.alt = altText;
  container.appendChild(img);
}

function updatePlayfieldMedia(index) {
  const container = document.getElementById("playfield-root");
  renderWindowMedia(
    container,
    vpin.getImageURL(index, "table"),
    vpin.getVideoURL(index, "table"),
    "Playfield"
  );
}

function updateBgMedia(index) {
  const container = document.getElementById("bg-root");
  renderWindowMedia(
    container,
    vpin.getImageURL(index, "bg"),
    vpin.getVideoURL(index, "bg"),
    "Backglass"
  );
}

function updateDmdMedia(index) {
  const container = document.getElementById("dmd-root");
  renderWindowMedia(
    container,
    vpin.getImageURL(index, "dmd"),
    vpin.getVideoURL(index, "dmd"),
    "DMD"
  );
}
```

## Files A Kind Does Not Read

Everything above assumes the file is already in a format its kind reads. Each kind has a family of extensions it reads, in `common/media/media_specs.py`:

| Family | Reads |
|---|---|
| `IMAGE_FAMILY` | `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.gif`, `.apng` |
| `VIDEO_FAMILY` | `.mp4`, `.webm` |
| `AUDIO_FAMILY` | `.mp3`, `.ogg` |

A file outside its kind's family is not media to the parser, however it is named. `CONVERTIBLE_IMAGE`, `CONVERTIBLE_AUDIO` and `CONVERTIBLE_VIDEO` list what the converter tries, and `convertible_family(family)` answers the list for a kind. A file on those lists becomes a file in the family with the same stem and token and a new extension, so `wheel.tga` becomes `wheel.png` and the parser finds it as it would any other.

Themes are unaffected: they see only the family's formats, and `docs/theme.md` is unchanged.

### The converter

`common/games/media_encode.py` holds the encoders (Pillow for pictures, FFmpeg for sound and video, found through `common/host/tools.py`) and the quality presets. `common/games/media_convert.py` turns a list of files and a set of choices into a plan, and runs it as the job `media.convert`. Every choice has one recommended option and a sentence per option, and these come from the server so the Manager and an API client say the same thing. The plan never promises a duration or an output size.

A file that is already in a format the chosen devices play is repackaged into the new container without re-encoding. Pictures keep their color profile and resolution, and become 8-bit RGB or RGBA. A JPG has no see-through areas, so they are filled with black and the plan says so.

### Converting on drop

A file dropped on the Manager is converted before it is placed, in the same import. The pieces:

1. The arrival engine (`common/uploads/arrival.py`) claims a convertible file for a slot the drop declares, the name gives, or the sheet suggests, and carries a `Conversion` on the detected asset (`common/uploads/conversion.py`). A file whose slot takes another kind of file is left out with the reason, and a file nothing claims is offered as an unchecked asset, saved in the game folder as it is.
2. The plan puts a `convert` description on each such item, with the source format, the options and their recommendations, and the settings behind the row. `POST /uploads/{id}/plan` answers it, and the choices go back in the plan and import requests as `convert`.
3. `POST /uploads/{id}/import/start` runs the import as a job. `common/uploads/drop_convert.py` converts every staged file first, one at a time and reporting each in the job's `parts`, and only then places any of them. A stop (`POST /uploads/import/stop`) or a failure before placement adds nothing and leaves the session as it was.
4. `GET /uploads/{id}/picture` renders a PNG for a convertible picture the browser cannot draw, so the sheet can show a thumbnail of a TGA.

The choice that converts nothing, `asis`, writes the file untouched under its slot's name. The library then holds a file its kind does not read, which shows as unsupported until it is converted.

Routes and payloads are in [http-api.md](http-api.md).

## Events And Refresh Behavior

Media is not pushed into the theme as binary event data. Instead, events tell the theme that game state changed, and the theme should refresh its media URLs for the new game index.

Common cases:

- `TableIndexUpdate`
- `TableDataChange`

Relevant code:

- [frontend/static/common/vpinfe-core.js](../frontend/static/common/vpinfe-core.js)

In practice, when a new game becomes active, theme code should:

1. Read the current index.
2. Call `getImageURL()` and `getVideoURL()` again.
3. Update the DOM elements for that screen.
