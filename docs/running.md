# Running VPinFE

Day-to-day use happens in the Manager.  This page covers what the Manager does not walk you through: how a game folder is laid out, the buttons and keys that drive the frontend, cabinet outputs, the command line and the ports VPinFE listens on.  Where a setting has a page in the Manager it is named here as **Settings > Group > Page**.  Every setting, with its key in `vpinfe.json`, is in [Technical Details](technical-details.md).

## Games and tables

VPinFE keeps a library of **games**.  A game is one real machine, such as Medieval Madness, and it lives in one folder.  A **table** is a `.vpx` file Visual Pinball plays.  One game can hold several tables, such as a desktop build, a VR build and a patched variant.  Each is a launchable table in its own right, and you can hide the ones you do not want offered.  Everything else in the folder belongs to the game: its ROM, sounds, pictures and the `.info` file VPinFE keeps about it.

Setup asks where your games are, and **Settings > Library > Folders** changes it later.  Game folder names and layouts should follow the [VPinball table organization standard](https://github.com/vpinball/vpinball/blob/master/docs/FileLayout.md).  The library folder holds one folder per game:

```
games/
├── 24 (Stern 2009)/
├── AC-DC LUCI Premium VR (Stern 2013)/
├── American Graffiti (Original 2024)/
├── Medieval Madness (Williams 1997)/
└── ...
```

Here is a game folder with most of what VPinFE understands.  None of it is required beyond a `.vpx`, and a game folder with one table and nothing else works too:

```
Medieval Madness (Williams 1997)/
├── Medieval Madness (Williams 1997).info    what VPinFE knows about the game
├── Medieval Madness Remake 1.2.vpx          a table
├── Medieval Madness Remake 1.2.directb2s    that table's backglass
├── Medieval Madness Remake 1.2.ini          that table's Visual Pinball settings
├── Medieval Madness Remake 1.2.vbs          that table's script
├── Medieval Madness Remake 1.2.pov          that table's point of view
├── Medieval Madness Remake VR 1.2.vpx       a second table, the VR build
├── Medieval Madness Remake VR 1.2.directb2s
├── readme.txt                               the author's notes
├── altsound/                                AltSound packs
├── music/                                   background music
├── pupvideos/                               a PinUP Player pack
├── serum/                                   color DMD files for the ROM
├── pinmame/
│   ├── cfg/
│   ├── ini/
│   ├── nvram/
│   └── roms/
│       └── mm_109c.zip
└── medias/
    ├── table.png                            the playfield, as a picture
    ├── table.mp4                            and as a video
    ├── bg.png                               the backglass
    ├── dmd.png
    ├── wheel.png
    ├── logo.png
    ├── flyer.png
    ├── audio.mp3
    └── (Wheel) Medieval Madness Remake VR 1.2.png   a wheel for the VR table alone
```

- **Files named for a table** stay with that table: the backglass, settings, script and point of view above each follow the `.vpx` they sit beside.  A backglass, settings file or script named for the game folder serves any table in it that has none of its own.
- **Moving a file between the game and its tables.**  In the Manager, a game's media, backglass, ScoreView or settings file has **Choose Tables...** to pick the tables that are to have it, and a table's own has **Move to Game**.  A script and a POV go to a table only.  A file a move replaces is kept in **Backups**, and the message that follows has **Undo**.
- **Media** is looked for in `medias/` first and then in the game folder itself.  A file named for a table, such as `(Wheel) Medieval Madness Remake VR 1.2.png`, is used for that table and beats one named for the game, which beats the plain `wheel.png`.  The full list of names is in [Media Flow](media-flow.md).
- **ROMs, sounds and packs** live inside the game.  `altsound/`, `pupvideos/`, `music/` and a `serum/` or `vni/` folder are recognized by their names.  A Serum or VNI color file is saved under the game's ROM name (`serum/<rom>/<rom>.cRZ`, `vni/<rom>/<rom>.pal`), because that is the only name VPX's plugins load; `pin2dmd.pal` and `pin2dmd.vni` keep theirs, and a `.pac` is refused.  Import them with the drag and drop importer, or let the importer convert them from another frontend's layout.
- **Hidden tables.**  A table you hide stays on disk and is left out of the frontend, which suits a patch base that a patched variant needs.
- **The `.info` file** is written and kept up to date by VPinFE.  It holds the VPS match, the details it read from each table, your ratings and play history, and which media you added.  It is safe to leave alone.

### Matching to VPS

VPinFE matches each game to its [VPS](https://virtualpinballspreadsheet.github.io/) entry from the folder name, written as Name (Maker Year).  **Auto-Match** on the Games page runs it again for every game, and a match you picked yourself stays.  A game it could not match is listed as Unmatched; open it and use **Match to VPS...**.

### Media

VPinFE downloads media for your games from [VPinMediaDB](https://github.com/superhac/vpinmediadb), and never replaces media you chose.

- **New games** have their missing media downloaded as soon as they are matched.  The switch is **Download Media for New Games**, under **Settings > Library > Automatic**.
- **Media VPinFE downloaded** stays current.  When VPinMediaDB replaces a file, the copy VPinFE downloaded for you is replaced too.  Media you added or changed is never touched.  The switch is **Update Downloaded Media**, on the same page.
- **Download Missing Media...** fills the gaps whenever you ask, in the kinds you pick.  It is in a game's **Media** section and in the **Games** and **Media** bulk menus.  On the **Overview**, **Download Media** is beside a missing playfield or backglass.
- **Record Media...** plays the table and records its own screens into its media: the playfield, backglass and DMD as videos and pictures, and its sound if you select Audio.  It is in a game's or a table's **Media** section, and **Record** beside a slot's **Add** or **Replace** records just that one, for you to review before it is used.  It fills empty slots unless you choose to replace files, asks before replacing any, keeps each file it replaces in **Backups**, and can keep each recording for you to review before it is used.  Its settings are under **Settings > Hardware > Recording**.

Only the kinds your library collects are downloaded, and the playfield is downloaded in the variant and resolution you set.  Both are under **Settings > Library > Media**.

The picture beside a game, table or collection's name in the Manager's lists is **Art in Lists**, under **Settings > VPinFE > Manager**.  It is the wheel unless you pick the backglass, the playfield, the logo or none, and it offers only the kinds your library collects.  A game or table shows the art the frontend shows for it, and its icon where it has none.  A collection shows its own picture whichever kind you pick.

To supply all your media yourself, build with `--user-media` (see [Technical Details](technical-details.md#using-your-own-media-user-media)).

## Keys and buttons

**Settings > Hardware > Input** lists everything the frontend can be told to do.  Pick a row, then press the key or gamepad button that should do it.  Press anything on that page to see what it does now.  Holding a button down can do something else from tapping it, such as a long press of Back to quit.

These are what each action starts as:

| Action          | Keyboard                  | Gamepad (standard layout)        |
|-----------------|---------------------------|----------------------------------|
| Previous        | Left arrow, Left Shift    | D-pad left, left trigger         |
| Next            | Right arrow, Right Shift  | D-pad right, right trigger       |
| Page Previous   | Page Up, Up arrow         | D-pad up, left shoulder          |
| Page Next       | Page Down, Down arrow     | D-pad down, right shoulder       |
| Select          | Enter                     | Button 0                         |
| Back            | Escape, B                 | Button 1                         |
| Menu            | M                         | Button 9                         |
| Collection Menu | C                         | Button 8                         |
| Tutorial        | T                         |                                  |
| Quit            | Q, or hold Escape         | Hold Button 1                    |

To see which number a gamepad button has, run `./vpinfe --gamepadtest`.  It opens a window that lights up each button as you press it, and lets you assign a button to an action.  Press Escape to close it.

## DOF and real DMDs

Release builds include two integrations, packaged under `third_party/dof` and `third_party/libdmdutil`:

- **DOF** drives cabinet outputs such as feedback devices and toys.
- **libdmdutil** sends the table's DMD image to a real DMD.

Both are off until you switch them on.  Running from source, download them with `./scripts/fetch_dof_bundle.sh` and `./scripts/fetch_libdmdutil_bundle.sh`.  To use a copy kept somewhere else, set `VPINFE_DOF_DIR` or `VPINFE_LIBDMDUTIL_DIR` to its folder.

### DOF

Turn on **Enable DOF** under **Settings > Hardware > Output**.  VPinFE starts the DOF runner when it starts.  If you use the online DOF Config Tool, paste your key into **DOF Config Tool API Key** on the same page.

### Real DMD

Turn on **Enable Real DMD** under **Settings > Hardware > Displays**, in the Real DMD section.  VPinFE connects to a ZeDMD by the **ZeDMD Serial Port** if one is set, and otherwise by its **ZeDMD Wi-Fi Address**.  With both blank, libdmdutil looks for a device itself.  **Enable PIN2DMD** and **Pixelcade Serial Port** are kept for libdmdutil; VPinFE reads only the two ZeDMD fields itself.

## Command line

Run `./vpinfe --help` for the list.  Options that change how it starts:

| Option | What it does |
|---|---|
| `--version` | Show the version |
| `--headless` | Run the servers and services only, with no frontend windows at start. They can still be opened from the Manager's power menu, and closing them, by hand or with the theme's Exit, leaves the run going |
| `--configdir DIR` | Keep the config in `DIR` instead of the OS default, with the cache in `DIR/cache`, the state in `DIR/state` and the logs in `DIR/logs`.  Same as the `VPINFE_CONFIG_DIR` environment variable |
| `--listres` | List your screens and their IDs |
| `--gamepadtest` | Open the gamepad button tester |
| `--check-extension DIR` | Load the extension in `DIR` as VPinFE would, and say what it declares, what it reads and any word it is missing.  It sends and changes nothing.  Exits with 1 where the extension would not pass |

Options that work on the library without starting VPinFE:

| Option | What it does |
|---|---|
| `--buildmeta` | Build the `.info` file in each game folder |
| `--game NAME` | Only the one game folder `NAME`, with `--buildmeta`, `--upgrade-info` or `--restore-info` (`--table` still works) |
| `--update-all` | With `--buildmeta`: read every game again |
| `--no-media` | With `--buildmeta`: do not download images |
| `--user-media` | With `--buildmeta`: download nothing from VPinMediaDB, and use only the media you supply |
| `--upgrade-info` | Upgrade every `.info` file to the current format, saving each one first.  This happens at startup; use it to finish an upgrade that was interrupted |
| `--restore-info` | Restore the `.info` files saved before they were upgraded.  Your current ones are kept first |
| `--listmissing` | List the games from VPS |
| `--listunknown` | List the games VPinFE cannot match to VPS |
| `--vpxpatch` | Apply the VPX patches |
| `--reset-3x-state` | Remove everything 3.x wrote, so the next start migrates from scratch: its settings and collections files, the `.info` files it made and every backup it kept.  Settings you changed in 3.x are lost, and so are ratings and play counts on games 3.x added.  Refuses while VPinFE is running.  `--dry-run` lists what would go and changes nothing, and `--config-only` leaves the game library alone |

`--claim-user-media` does nothing any more: your own media is protected automatically.

## Ports

VPinFE starts two listeners:

| Service | Default address | What it does |
|---|---|---|
| Web server | `0.0.0.0:8001` | The Manager at `/`, the remote at `/remote`, the HTTP API at `/api/v1`, and the themes and table media the frontend windows load |
| Window channel | `127.0.0.1:8002` | How the frontend windows talk to VPinFE.  It stays on this machine |

The web server answers on every interface so you can run a cabinet from another device, such as a mobile device.  A browser on another device signs in with a code from this machine's screen or from the authenticator app.  The connection is not encrypted until you set up **Encrypted Connections** under **Settings > Security**, so keep it on a network you trust.  To reach it only from the machine itself, set **Reachable From** to **This Device Only** under **Settings > VPinFE > Network**.

**Port** and **Window Port** are on the same page.  Table media is served on the web server's port but is only handed to a signed-in browser on another device that may read the library.  The window channel can start a game and power the machine off, so there is no setting to open it.  If another program holds a port, VPinFE says which setting to change instead of starting.

## VPinPlay (experimental)

VPinFE includes an experimental VPinPlay integration.  Each game is sent as it is played - its plays, time, rating and high scores - to the VPinPlay account of each player who has turned Share on.  Nothing else in the library is sent, and a game marked Private never is.

- Main site: [vpinplay.com](https://www.vpinplay.com)
- Server default: `https://api.vpinplay.com:8888`, changed with **Server** in VPinPlay's settings
