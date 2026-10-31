## vpinfe.json Definition

VPinFE stores its settings as JSON in a platform-specific configuration directory. On
first run it writes a complete `vpinfe.json` there, so a new install never needs the file
opened manually:

- **Linux**: `~/.config/vpinfe/vpinfe.json`
- **macOS**: `~/Library/Application Support/vpinfe/vpinfe.json`
- **Windows**: `C:\Users\<username>\AppData\Local\vpinfe\vpinfe\vpinfe.json`

A `vpinfe.ini` from an earlier build is read once, converted, and kept, so downgrading
still works. Settings live under a `settings` object beside a `schema` version, and each
heading below is a key in it - `windows.playfield` is a `playfield` object inside a
`windows` object.

Every name a setting has ever had keeps resolving, so an older file and a hand-edit that
uses an old spelling both still load.

<!-- generated from common/config/config_schema.py by tests/support/config_reference.py -->

### `windows.playfield`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `screen_id` | int | `0` | Screen |
| `orientation` | choice (landscape, portrait) | `landscape` | How the playfield screen is physically mounted in the cabinet. This rotates nothing by itself - it tells themes what shape to lay out for. |
| `rotation` | choice (0, 90, 180, 270) | `0` | How far VPinFE turns its own display so it faces the player, on top of any turn your operating system already makes |

### `windows.backglass`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `screen_id` | int |  | Screen |
| `override` | string |  | Position and size for this window, as x,y,width,height. Empty means no override. |

### `windows.scoreview`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `screen_id` | int |  | Screen |
| `override` | string |  | Position and size for this window, as x,y,width,height. Empty means no override. |

### `windows.topper`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `screen_id` | int |  | Screen |

### `general`

Runtime state written by VPinFE, not shown as a setting.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `game_root_dir` | string |  |  |

### `library`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `hidden_media_kinds` | list |  | Kinds of media this library does not collect. VPinFE stops showing and counting them; the files stay where they are. |
| `hidden_asset_kinds` | list |  | Kinds of supporting file this library does not collect - an all-EM library has no ROMs. A table that will not launch still says so. |
| `hidden_sources` | list |  | Online sources VPinFE does not search for files |
| `hidden_checks` | list |  | Library checks that stay quiet. A check left on reports every table it finds a gap in. |

### `extensions`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `switched_off` | list |  | Extensions that stay unloaded. One installed later starts switched on. |

### `commands`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `timeout` | int | `15` | Seconds to wait for a command before giving up |
| `on_vpinfe_start` | text |  | One command per line, run as VPinFE starts and before its services do. Arguments split the way a shell splits them; for a pipe, point at a script. |
| `on_vpinfe_exit` | text |  | Run last. These run even if VPinFE is stopping because something went wrong. |

### `table_commands`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `on_start` | text |  | Run before every table, whichever launcher plays it, and before that launcher's own commands |
| `on_exit` | text |  | Run after every table. They run whenever the ones above ran, even if the table never started. |
| `start_required` | bool | `false` | On, a command that fails before a table starts stops it launching. Off, the failure is noted and the table starts anyway. |

### `chromium`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `path` | string |  | The browser the frontend's windows open in |
| `defaults` | bool | `true` | Full screen, autoplay, no Chrome prompts. Off leaves only what positions a window. |
| `options_exclude` | text |  | Built-in options to drop, named without their value |
| `options` | text |  | Added after the defaults. Where one sets the same option, yours is used. For example, --ozone-platform=x11. |

### `manager`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `appearance` | choice (synthwave, dark, light, system) | `synthwave` | How the Manager and remote look |
| `thumbnail` | choice (none, wheel, backglass, playfield, logo) | `wheel` | The picture beside a game, table or collection's name in the Manager's lists |
| `thumbnail_shape` | choice (auto, square, wide) | `auto` | Automatic draws wheels square and the rest wide |
| `thumbnail_height` | choice (small, medium, large) | `small` | Taller grid rows show bigger pictures and fit fewer on a screen |
| `thumbnail_frame` | bool | `true` | A dark tile behind each picture keeps light pictures readable in the light theme |
| `thumb_cache_max_mb` | int | `500` | Disk space the Manager may use for the small pictures in its lists |
| `dates` | choice (language, iso, mm/dd/yyyy, dd/mm/yyyy, dd.mm.yyyy, yyyy/mm/dd) | `language` | How a day is written, wherever the Manager shows one |
| `times` | choice (24h, 12h) | `24h` | How a time of day is written, where the Manager shows one |
| `relative_dates` | bool | `true` | Anything from the last month says how long ago it was, whatever the format |
| `units` | choice (region, cm, in) | `region` | How the Manager writes a length. By default this device's country decides |
| `open_links` | choice (new, reuse) | `new` | Where a link to another site opens. Your browser picks tab or window. |
| `hidden_overview` | list |  | Overview rows that stay quiet. A row left on reports whatever it finds. |
| `discovery_shelf_order` | list |  | Top to bottom order of Discovery's Home shelves. Set on the Discovery page. |
| `hidden_discovery_shelves` | list |  | Shelves Discovery's Home leaves out. Set on the Discovery page. |

### `tools`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `rar_path` | string |  | Needed to import RAR archives: unar, unrar, 7-Zip or bsdtar |
| `ffmpeg_path` | string |  | Needed to record media |
| `grim_path` | string |  | Needed to record pictures of the screens |
| `wf_recorder_path` | string |  | Needed to record video of the screens |
| `gstreamer_path` | string |  | Needed to record on KDE Plasma and GNOME |
| `wtype_path` | string |  | Needed on Wayland to press a table's keys |
| `ydotool_path` | string |  | Needed on Wayland to press a table's keys |
| `nvidia_smi_path` | string |  | Needed to read NVIDIA graphics cards |
| `nvtop_path` | string |  | Needed to read graphics cards VPinFE cannot read itself |

### `capture`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `length` | int | `20` | Seconds of each screen to record |
| `wait` | int | `15` | Seconds after the table has started before recording begins |
| `picture_at` | int | `5` | Seconds into the recording that each picture is taken |
| `fps` | choice (30, 60) | `30` | Frame Rate |
| `size` | choice (1920, screen) | `1920` | Size |
| `video_codec` | choice (auto, h264, vp9) | `auto` | Automatic records a format this device's frontend can play |
| `quality` | choice (standard, high) | `standard` | High is sharper, in larger files |
| `playfield_orientation` | choice (bottom_right, bottom_left, upright) | `bottom_right` | Which way round the playfield is stored |
| `sound` | bool | `false` | Record the table's sound as a file of its own |
| `sound_source` | string |  | A sound source by name |
| `record_command` | string |  | The command that records each screen |
| `encode_command` | string |  | Turns each recording into the stored file |

### `updates`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `refresh_minutes` | choice (0, 60, 360, 1440) | `0` | Reloads every game folder on this schedule, so games added or removed outside VPinFE show up on their own. Slow over a network share. |
| `match_new_games` | bool | `true` | When you add a game, finds it on the Virtual Pinball Spreadsheet by its folder name and fills in its title, maker, year, type and themes |
| `update_game_details` | bool | `false` | When a newer spreadsheet downloads, copies its changes to the title, maker, year, type, themes and guides of your matched games. Your own edits stay. |
| `get_media_for_new_games` | bool | `true` | When you add a game, downloads the media it is missing once it is matched, and takes its backglass picture from its backglass file. Never replaces your media. |
| `update_downloaded_media` | bool | `true` | Once a day, replaces media VPinFE downloaded when VPinMediaDB has a newer version. Never touches media you added or changed. |
| `ask_where_new_games_go` | bool | `true` | On, adding a game asks which folder it goes to. Off, it goes to the folder marked for them. |
| `new_games_location` | string |  | The folder a new game is created in. Set from the library's folders. |
| `download_spreadsheet` | choice (never, daily, weekly, monthly) | `daily` | Downloads the latest copy on this schedule. Matching, game details and new VPS releases all read it. |
| `show_update_count` | bool | `true` | Discovery in the menu shows how many of your games have an update |

### `presentation`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `cab_mode` | bool | `false` | Presents VPinFE for playing standing at a cabinet: larger text and targets, and no controls that need a mouse. It does not rotate anything. |
| `playfield_media_rotation` | choice (auto, 0, 90, 180, 270) | `auto` | How far to turn playfield media so it fills the screen |
| `playfield_media_priority` | choice (video, image) | `video` | Playfield Media Priority |
| `backglass_media_priority` | choice (video, image) | `video` | Backglass Media Priority |
| `scoreview_media_priority` | choice (video, image) | `video` | Score View Media Priority |
| `real_dmd_media_priority` | choice (color, standard) | `color` | Real DMD Media Priority |

### `behavior`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `paging_group` | choice (sort, count) | `sort` | A collection can set its own |
| `paging_size` | int | `10` | Games per Page |
| `startup_collection` | string |  | Open On |
| `restore_last_table` | bool | `true` | Select the Last Table Played |
| `splashscreen` | bool | `false` | The VPinFE intro, played while the theme loads |
| `confirm` | bool | `false` | Closing the frontend never asks |
| `hide_quit_button` | bool | `false` | Hide Quit from the Main Menu |
| `mute_audio` | bool | `false` | Tables still play their own sound |
| `attract_enabled` | bool | `true` | Move through the games on its own while nobody is pressing buttons |
| `attract_idle` | int | `60` | Start After |
| `attract_step_time` | int | `8` | Show Each Game For |
| `attract_order` | choice (random, in_order) | `random` | Random shows every game once before any repeats |
| `attract_on_return` | choice (restore, stay) | `restore` | When a Button Is Pressed |
| `attract_audio` | bool | `false` | Play Game Sound |

### `themes`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `active` | string | `Revolution` | Active Theme |
| `registries` | list | `https://raw.githubusercontent.com/vpinfe/themes/main/themes.json` | Theme Lists, most trusted first. Only themes from the VPinFE Theme List are installed on their own. |
| `repositories` | list |  | Single Themes, one repository each. They come before the Theme Lists. None is installed on its own. |
| `refresh` | choice (never, daily, weekly, monthly) | `daily` | How often to check for new themes and updates |

### `logger`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `level` | choice (debug, info, warning, error) | `debug` | Log Verbosity |
| `terminal` | bool | `true` | Log to Terminal |

### `backups`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `folder` | string |  | Where VPinFE keeps its backups, including the ones Back Up Now makes. Changing it asks whether to move the backups already made. |

### `media`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `playfield_variant` | choice (table, fss) | `table` | Which playfield picture your library uses: the standard one, or the one captured in Full Single Screen mode |
| `playfield_resolution` | choice (4k, 1k) | `4k` | The size of playfield pictures VPinFE downloads |
| `playfield_video_resolution` | choice (4k, 1k) | `1k` | The size of playfield videos VPinFE downloads |
| `manufacturer_logos_dir` | string |  | Put one image per manufacturer here, named for the manufacturer |
| `wheelset` | string |  | Shows an alternate wheel style, from a folder under medias/wheels/ in each game. A game without it keeps its own wheel. A theme can override this. |

### `device`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `setup_pending` | bool | `false` | Written true on this device's first save. Setup clears it when it finishes. |
| `name` | string |  | What to call this device where one is listed. Defaults to its hostname. |
| `features` | list | `library,frontend,devices,overview` | What this device is for. Each one you add appears in the Manager. |
| `language` | choice (auto, en) | `auto` | What language VPinFE speaks. Your library's own words - table names and the rest - are never translated. |
| `update_channel` | choice (stable, beta) | `stable` | Stable is for everyday use. Beta may be unstable but gets updates first. |

### `network`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `http_port` | int | `8001` | Where the Manager, the Remote, the API and the frontend's windows connect |
| `http_bind` | choice (0.0.0.0, 127.0.0.1) | `0.0.0.0` | Which devices can open VPinFE. This Device Only shuts out the Remote too. |
| `ws_port` | int | `8002` | Port the frontend windows and the theme use to talk to VPinFE. Reachable from this device only. |
| `library_url` | string |  | Which device this one reads its library from. Empty means it holds its own. Devices on your network are offered; type an address for one that is not. |
| `verify_shared_library` | bool | `false` | On startup, check that the library this device reads is the one on disk here, by comparing file hashes. Reports what does not match, and changes nothing. |

### `dof`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `enabled` | bool | `false` | Enable DOF |
| `config_tool_api_key` | string |  | Downloads your DOF config. Sign in to the tool and copy the key from Cabinets |
| `download_config` | choice (never, daily, weekly, monthly) | `weekly` | How often to ask the DOF Config Tool for newer config |

### `real_dmd`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `enabled` | bool | `false` | Enable Real DMD |
| `pin2dmd_enabled` | bool | `false` | Enable PIN2DMD |
| `pixelcade_serial_port` | string |  | Pixelcade Serial Port |
| `zedmd_serial_port` | string |  | ZeDMD Serial Port |
| `zedmd_wifi_address` | string |  | ZeDMD Wi-Fi Address |

### `vpxmobile`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `chunk_size` | int | `1048576` | Files go across in pieces this size; lower it if sends fail on a weak connection |
| `send_masked_config` | bool | `false` | Where a game has a settings file made for mobile devices, named with the word below, that file is sent to the device instead of its own settings |
| `config_mask` | string | `mobile` | The word that marks a settings file made for the device. Copy a table's settings file, add the word before .ini, and the device gets that copy |

### `vpinplay`

Owned by the VPinPlay extension. Declared here so a 2.x file converts, read once, and written by nothing in core. `api_endpoint` is the extension's Server setting, `user_id` and `machine_id` become the owner's VPinPlay account, and `initials` the owner's initials. `sync_on_exit` is not carried over: nothing is shared until each player turns Share on.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `sync_on_exit` | bool | `false` |  |
| `api_endpoint` | string | `https://api.vpinplay.com:8888` |  |
| `user_id` | string |  |  |
| `initials` | string |  |  |
| `machine_id` | string |  |  |

### `input`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `previous` | list | `key:ArrowLeft,key:ShiftLeft,pad:standard/button:14,pad:standard/button:6` | Previous |
| `next` | list | `key:ArrowRight,key:ShiftRight,pad:standard/button:15,pad:standard/button:7` | Next |
| `page_previous` | list | `key:PageUp,key:ArrowUp,pad:standard/button:12,pad:standard/button:4` | Page Previous |
| `page_next` | list | `key:PageDown,key:ArrowDown,pad:standard/button:13,pad:standard/button:5` | Page Next |
| `select` | list | `key:Enter,pad:standard/button:0` | Select |
| `back` | list | `key:Escape,key:KeyB,pad:standard/button:1` | Back |
| `menu` | list | `key:KeyM,pad:standard/button:9` | Menu |
| `collection_menu` | list | `key:KeyC,pad:standard/button:8` | Collection Menu |
| `tutorial` | list | `key:KeyT` | Tutorial |
| `exit` | list | `key:Escape@hold:1500,key:KeyQ,pad:standard/button:1@hold:1500` | Quit |
| `take_picture` | list |  | Pauses the game. Press again to take a picture of the screens; Back resumes. |

## Game Metadata File (based on the Zero install table format)
When you run VPinFE with the `--buildmeta` option it recursively scans your game directory and attempts to match your games to their VPSDB id.  When matched, it will then parse the VPX for the game for more meta information and produce a `GAME FOLDER NAME(manufactuer year).info` in that game's directory.  Heres an example for the game 1-2-3:

```
{
    "Info": {
        "IPDBId": "5247",
        "Title": "1-2-3",
        "Manufacturer": "Automaticos",
        "Year": 1973,
        "Type": "EM",
        "Themes": [
            "TV Show",
            "Game Show"
        ],
        "VPSId": "HhMnyw53"
    },
    "User": {
        "Rating": 0,
        "Favorite": 0,
        "LastRun": null,
        "StartCount": 0,
        "RunTime": 0,
        "Tags": []
    },
    "vpinfe": {
        "schema": 2,
        "id": "tuF3WogthK",
        "default_table": "123(Talleres de Llobregat 1973) v601.vpx",
        "delete_nvram_on_close": false,
        "alt_launcher": "",
        "plugin_profile": "",
        "alt_title": "",
        "alt_vpsid": "",
        "frontend_dof_event": "",
        "run_time_seconds": 0
    },
    "tables": {
        "123(Talleres de Llobregat 1973) v601.vpx": {
            "file_hash": "d685ce54d659fadcafd90a296473fb126754aa23b1145f457c6626aa5baa75d9",
            "vbs_hash": "bd6dcb7e0c618e4553d230095e73c7ca8e17f31def4595c38a8439b279977b45",
            "version": "6.0.1",
            "release_date": "2026-01-25",
            "save_date": "2026-01-25T22:24:36",
            "save_rev": "91",
            "manufacturer": "",
            "year": "",
            "type": "",
            "rom": "TlD_123",
            "authors": [
                "jpsalas",
                "akiles50000",
                "Loserman76"
            ],
            "detect_nfozzy": false,
            "detect_fleep": false,
            "detect_ssf": true,
            "detect_lut": true,
            "detect_scorbit": false,
            "detect_fastflips": false,
            "detect_flex": false,
            "user": {
                "last_run": null,
                "start_count": 0,
                "run_time_seconds": 0
            }
        }
    },
    "assets": {
        "medias/bg.png": {
            "source": {
                "host": "vpinmediadb",
                "hash": "d80f67a370ebce2edd19febdc3fd7636"
            }
        },
        "medias/wheel.png": {
            "source": {
                "host": "vpinmediadb",
                "hash": "a88bcaf2ade6b9614417fc18a8782f78"
            }
        },
        "medias/cab.png": {
            "source": {"host": "user"}
        }
    }
}
```
### Sections Explained

- Info

  Contains the core game metadata sourced from VPSdb and the VPX file:
  - IPDBId: Internet Pinball Database ID (if available)
  - Title: Game name
  - Manufacturer, Year, Type (EM, SS, etc.)
  - Themes: Array of themes
  - VPSId: Internal VPS database ID
  - Description: Game description/blurb

  Authors and Rom used to live here too. Both are properties of a table rather than of
  the machine — a folder can hold several, and they can disagree — so they moved to
  `tables`.

- User

  Stores per-user data for the game. Preserved across `--buildmeta --update-all`:
  - Rating: User rating (0–10)
  - Favorite: Favorite flag (0/1)
  - LastRun: Timestamp of last play
  - StartCount: How many times played
  - RunTime: Total playtime in minutes, rounded down. Derived from
    `vpinfe.run_time_seconds`, which is what actually accumulates — a key fixed by the
    VPX spec cannot hold the seconds, and adding whole minutes per session charged a
    few seconds at a table the same as a few minutes.
  - Tags: Array of custom tags
  - HighScores: the machine's high score table as last read, one record per ROM, since a
    ROM is the machine and two tables on it share one table. Each is `read_at`,
    `score_kind`, the reading's `entries` (or `value`, for a machine that keeps one number)
    with initials as the machine wrote them, and `new`, the positions of the entries the
    last game put there. 2.x kept one reading as `Score`; the migration moves it under the
    ROM it names.

- tables

  One entry per `.vpx` in the folder, keyed by filename. A game folder can hold a desktop
  build, a VR build and a patched one, and every visible entry is independently launchable —
  they are peers, not a primary with alternates. Each entry holds what that file says about
  itself:
  - file_hash, vbs_hash, version
  - release_date, save_date, save_rev — normalized to ISO 8601, at whatever precision the
    author actually gave. An unreadable or ambiguous date degrades to the year rather than
    being guessed at.
  - manufacturer, year, type — the `.vpx`'s own claim, which can differ from what VPS says
    in `Info`. Both are kept: the disagreement is a signal.
  - rom, authors
  - detect_* flags: what was found in the table's script
  - hidden: set to keep a table out of the frontend without deleting it — a patch base
    has to stay on disk, but should not be offered
  - added: when the library first had this table, as ISO 8601 UTC. The moment a person or
    an extension added it, or the file's own creation date for one found on disk.
  - user: play history for this table alone (last_run, start_count, run_time_seconds).
    It accumulates separately from `User`, which is the game's own history.

- vpinfe

  VPinFE-specific settings for the game, preserved across `--buildmeta --update-all`:
  - schema: the version of this file's shape. Absent means a 2.x file, which is migrated on
    first read — see below.
  - id: the game's stable local identity, used in the API, in events and in collections
  - default_table: which table a consumer that can only take one should get. With none
    recorded, or when the recorded one is hidden or missing, it is the most recently added
    visible table, and a tie goes to the first by name.
  - delete_nvram_on_close: (true/false) Some games, like Taito machines, retain the game
    state when you quit. Enabling this deletes the NVRAM file on close. Default is false.
  - alt_launcher: Optional executable path override for this game alone. If set, it is used
    instead of `vpinfe.ini` `Settings.vpxbinpath`.
  - private: (true/false) Never sent to VPinPlay or any other community service, whoever
    plays the game. Absent is false.
  - run_time_seconds: total play time. This is the counter that accumulates; `User.RunTime`
    is it rounded down to whole minutes. Written on the first play after upgrading, seeded
    from whatever `User.RunTime` already held.
  - plugin_profile, alt_title, alt_vpsid, frontend_dof_event

  `alt_vpsid` is a manual VPS match: automatic matching got the game wrong, and somebody
  looked up the right record and said so. It overrides `Info.VPSId` everywhere an id is
  used - collections, media matching, the VPinPlay payload. `null` says the machine is in
  no catalog, and nothing is matched to it.

  - vps_matched_by

  How the match in `Info` was made: `import` for an entry picked when the game was
  imported, `user` for one somebody chose. Absent means VPinFE guessed it from the folder
  name. A game with an `alt_vpsid`, or a `null` one, counts as matched by somebody
  whatever this says.

  A rebuild, `--update-all` included, guesses again only for a game nobody matched. A game
  somebody matched keeps `Info` and its guides as they are: the rebuild reads its tables
  again and fetches art for the entry in force.

- assets

  One entry per file VPinFE placed in the game folder, keyed by the file's path relative to that folder, with forward slashes. Preserved across `--buildmeta --update-all`. Replaces the old `Medias` section, which was keyed by media type and so could hold only one entry per type — no way to describe artwork belonging to one specific table, and the same question applies to backglasses, ROMs and colorizations. Each entry holds a `source`:
  - host: who supplied the bytes — a remote such as "vpinmediadb", or "user" for a file uploaded through the Manager
  - hash: the MD5 the host published, when it published one. Absent for a user upload, since a hash is only meaningful compared against a remote.

  Nothing else is stored. Which media kind a file is, and which table it belongs to, are read off its name every time media resolves, so a stored copy could only agree or be wrong.

  **A file with no entry is not ours.** Ownership is not decided from this section — the downloader hashes what is already on disk and compares it to the MD5 vpinmediadb publishes, so your own artwork is safe whether or not it appears here.

#### Upgrading from a 2.x file

Files written before schema 2 have no `schema` key, and are migrated the first time VPinFE
reads them: `VPXFile` becomes the first `tables` entry, `VPinFE` becomes `vpinfe` with
snake_case keys, `User.FrontendDOFEvent` moves to `vpinfe.frontend_dof_event`, and `Medias`
is dropped.

Nothing is written until something would have written anyway, and when it does, the original
is kept first as `<Game>.info.vpinfe-<timestamp>` — for example
`Table Name (Bally 1995).info.vpinfe-20260729T143022Z`. The timestamp is UTC, so restore
points sort and accumulate rather than overwriting each other. **To go back, rename one over
the `.info`.** Which schema a backup holds is read from its contents, not its name: no
`schema` key means it is a 2.x file.

After that file is created it then attempts to download the media artwork for that game from [VPinMediaDB](https://github.com/superhac/vpinmediadb). All media images are stored in a `medias/` subfolder within each game's directory:

```
Table Folder Name (Manufacturer Year)/
├── TableName.vpx
├── TableName.info
└── medias/
    ├── bg.png
    ├── dmd.png
    ├── table.png (or fss.png)
    ├── wheel.png
    ├── cab.png
    ├── flyer.png
    ├── realdmd.png
    ├── realdmd-color.png
    └── audio.mp3
```

| File Name         | Image Type                              |
| ----------------- | --------------------------------------- |
| bg.png            | Backglass Image                         |
| dmd.png           | DMD Image                               |
| table.png         | Table Image (landscape)                 |
| table.mp4         | Table Video (landscape)                 |
| fss.png           | Full Single Screen Image                |
| wheel.png         | Icon on Hud                             |
| cab.png           | A cabinet image of the pinball machine  |
| flyer.png         | Promotional flyer image                 |
| realdmd.png       | Real DMD for use with ZeDMD            |
| realdmd-color.png | Real DMD (Colorized) for use with ZeDMD |
| audio.mp3         | Table audio track for frontend playback  |

## Using Your Own Media (User Media)

By default, `--buildmeta` downloads media artwork from [VPinMediaDB](https://github.com/superhac/vpinmediadb).

**Your own artwork needs no protecting.** Before the downloader touches a file that already exists, it hashes it and compares that to the MD5 VPinMediaDB publishes. If they match it is demonstrably our file and stays managed. Anything else — your own artwork, or our file after you replaced it — is left alone. A file we cannot prove is ours is never overwritten, and VPinMediaDB having no hash for it counts as cannot prove.

So drop your artwork in and run `--buildmeta` as normal. Missing kinds get filled in from VPinMediaDB; yours stay put.

### `--user-media` (With `--buildmeta`)

A modifier for `--buildmeta` that skips VPinMediaDB downloads entirely. Use it when you are supplying the whole library yourself and would rather not wait on the network at all.

```bash
# Build metadata without fetching any media
python3 main.py --buildmeta --user-media

# Rebuild all metadata, still fetching nothing
python3 main.py --buildmeta --update-all --user-media
```

### `--claim-user-media` (Deprecated)

Marked existing media as `"Source": "user"` so VPinMediaDB would not overwrite it. The downloader proves ownership by hash now, so there is nothing to mark. The flag still parses and does nothing; it will be removed in a later release.

## VPX Table Patches
VPinFE can automaticlly pull patches from [vpx-standalone-scripts](https://github.com/jsm174/vpx-standalone-scripts) via the `--vpxpatch` CLI option if a matching patch can be found.  

`python3 main.py --vpxpatch`

## Mobile Transfer

VPinFE sends games to the mobile version of VPinball on Android and iOS from the Manager. The device has to be added once, then any game can be sent to it.

### Adding the device

1. Open VPinball on the mobile device and turn on the web server in its settings. It shows an address and a port.
2. In the Manager, open **Devices** and choose **Add VPX Mobile**.
3. Enter the **Address** and **Port**. VPinFE checks that the device is awake, VPX is open and its web server is on, and offers **Add anyway** if it cannot reach it.

The device is listed with the other devices from then on. Its pane has a **Carrying** heading that lists the games already on it, each with **Remove**, which deletes that game from the device and leaves your own copy alone.

### Sending

In **Games**, select one or more games and choose **Send to Device...** from the actions menu, then pick the device. Sending is a job, so it appears on the Jobs page with its progress. Media stays on this machine.

The confirmation offers **Send** and **Send with Settings**. **Send** carries the table, its backglass and ROM only. **Send with Settings** adds the game's settings file. Where a game has a settings file made for mobile and **Send Mobile Settings** is on (below), that file goes in place of the game's own, and the confirmation says how many games it applies to. A settings file made for mobile is a copy of the table's settings file with a word added before `.ini`, so `Attack from Mars.mobile.ini` goes across as `Attack from Mars.ini`.

### Sending settings

These are shared by every mobile device, under the **Sending** heading on a mobile device's pane (the keys are in [`vpxmobile`](#vpxmobile)):

- **Send Mobile Settings** turns on sending the settings file made for mobile in place of the game's own.
- **Mobile INI Word** is the word in `<table>.<word>.ini`. It defaults to `mobile`.
- **Chunk Size** is how many bytes go across at a time. Lower it if sends fail on a weak connection.

### Packing a game for another device

A game can be packed into a `.vpxz` archive (a zip) through the API, `GET /api/v1/games/{id}/archive`, which carries the same files a send does. The Manager has no button for it.

# Enabling the Shutdown Feature
If you plan on using the Shutdown/Reboot option in the frontend or in the remote you need to have the right permissions on some systems:

## Linux
`sudo nano /etc/polkit-1/rules.d/49-allow-poweroff.rules`

```
polkit.addRule(function(action, subject) {
    if (
        (
            action.id == "org.freedesktop.login1.power-off" ||
            action.id == "org.freedesktop.login1.power-off-multiple-sessions" ||
            action.id == "org.freedesktop.login1.power-off-ignore-inhibit" ||
            action.id == "org.freedesktop.login1.reboot" ||
            action.id == "org.freedesktop.login1.reboot-multiple-sessions" ||
            action.id == "org.freedesktop.login1.reboot-ignore-inhibit"
        ) &&
        subject.user == "superhac"
    ) {
        return polkit.Result.YES;
    }
});
```

`sudo systemctl restart polkit`

On Kubuntu/KDE, journal entries about `/run/polkit-1/rules.d` or `/usr/local/share/polkit-1/rules.d` not existing after the restart are normal and can be ignored.
