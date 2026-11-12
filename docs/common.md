# Common Architecture

The `common/` package is the shared application layer used by the frontend,
the Manager, CLI, startup/shutdown flow, and game metadata jobs. Code in this
folder should stay UI-independent and should expose stable service functions or
small facade classes for older call sites.

## Layout

Domain packages over a ranked shared layer, grouped by what a module knows about. The
boundaries were drawn from the import graph rather than manually: 92% of the
domain-to-domain edges stay inside one package.

**`common/` itself is the shared layer.** Nothing here knows about games, hardware or any
outside service, so anything may depend on it - and nothing in it may import from a domain
package. That rule is the point of the layer; breaking it is how `config_access` ended up
importing `game_metadata` to read a boolean.

### Layers

Inside the shared layer the units are ranked, and a unit imports its own rank or below:

| Rank | Unit | What it holds |
|---|---|---|
| 0 | core (the flat modules) | what knows nothing else: errors, values, ids, events, timestamps |
| 0 | `i18n/`, `apps/` | the catalog and the app registry |
| 1 | config | the config store, schema, paths and what reads them |
| 2 | security, media, runtime | keys and trust; media specs; jobs and the process's lifecycle |
| 3 | devices | other installs: the client, the registry, pairing |
| 4 | players, release, settings | the roster; release notes and upgrade needs; the settings service |
| 5 | `games/`, `online/`, `host/`, `uploads/`, `capture/`, `backups/`, `extensions/`, `support/` | the domains, which may import each other |

`tests/invariants/test_layering.py` holds the table and names each import that runs
against the ranks. That list only shrinks. A module that needs something above it is handed
the thing instead of importing it: `accepted` is told by `online` whether an address is the
VPinFE Theme List, and reads the themes folder from `paths`.

- `values.py`: value coercion (`is_truthy`) shared by config, metadata and filters.
- `ids.py`: how VPinFE mints an opaque local id, for games and tables alike.
- `events.py`: the in-process event bus. Hooks are part of an operation; subscribers are told about it.
- `vpinfe_version.py`: the build's version string.
- `config_bootstrap.py`: reads `--configdir` into the environment before anything under `common/` is imported.
- `kept_copies.py`: how a copy VPinFE keeps of a file is named, found and capped; the one place the name of a kept copy is built.

**`common/config/`** - where the install's settings and files are, and how they are read.

- `paths.py`: canonical user config, themes, collections, and game-root paths. `CONFIG_DIR` is resolved once at import time; set `VPINFE_CONFIG_DIR` before import (main.py maps the `--configdir` flag onto it) to relocate the whole config directory. Beside it, each kind of file that is not config has a folder of its own: `CACHE_DIR` (what a download can rebuild), `STATE_DIR` (what belongs to this device and is kept: `devices.json`, `dismissed.json`, `arrivals.json`, `marks.json`, `draw_order.json`, `updates/`, `capture/` as `CAPTURE_DIR`, `jobs/` as `JOBS_DIR` for the results of jobs started with `keep_result`, NiceGUI's `.nicegui/`) and `LOG_DIR` (`vpinfe.log`, `last_update.log`); under an override they are `cache/`, `state/` and `logs/` of it. `APP_ROOT` is where the app itself lives; reach a file the build ships through `bundled()`, which answers for a source checkout and for both kinds of frozen build.
- `config_access.py`: typed, UI-independent accessors for common INI sections.
- `config_store.py`: ini reading and writing. A list of objects in `vpinfe.json` is declared as a `ConfigList` in `config_schema.py` and read and written with `get_list` and `set_list`: an item without an `id` is given one, a field the list does not declare is refused, and the module that owns the list registers its `normalize` rule and the `seed` that fills it from a 2.x ini (`register_normalizer`, `register_seed`) before the store is made.
- `config_schema.py`: every setting declared once: its name, what it accepts, its default and what it means.
- `setting_groups.py`, `labels.py`: the headings a section's settings gather under, and a key as a person reads it.
- `input_registry.py`: every input action, its default bindings and the names it used to have. `[input]` in the config is generated from it, so the two cannot disagree.
- `path_state.py`: what the disk says about a path, asked one way for everything that names one.
- `ui_preferences.py`: how a person has arranged a page, kept with the library.

**`common/settings/`** - describing the settings, changing them, and saying what a change leaves waiting.

- `config_service.py`: every setting this install has, what it is set to and what may be changed; the one answer the config file, the Manager and HTTP all give.
- `settings_waiting.py`: what has been changed and is not yet in effect.
- `feature_checks.py`: whether the configuration is enough for the features switched on.
- `port_checks.py`: whether a port setting can be bound, asked when it is saved.
- `theme_options.py`: what a person set on a theme, kept where a theme update cannot reach it.

**`common/runtime/`** - how the running process behaves.

- `jobs.py`: slow work as a job — one at a time per kind, progress published on the bus, answerable by id after it finishes.
- `job_details.py`: a finished job's result as the one line the Jobs view shows beside it.
- `lifecycle.py`: starting, stopping and restarting the frontend, VPinFE or the machine, whichever surface asked.
- `shutdown.py`: what a kill signal does. Startup notes it and stops at the next step boundary; once the frontend is up it takes the same route as a user's own quit.
- `log_setup.py`: one logger tree, configured once, written to a file that cannot grow forever.

**`common/devices/`** - other installs, and this one's place among them.

- `device_client.py`: what one install asks of another, whether that device is this process or another machine.
- `device_registry.py`, `device_ops.py`: the devices an install knows about, keyed by `device_id`, and what it can ask them.
- `device_migration.py`: turning the one mobile device a 2.x install could hold into a listed one.
- `discovery.py`: saying what this install is on the network, and hearing what else is out there.
- `install_identity.py`, `install_presence.py`: the opaque id of this install, and how it presents itself to everything else.
- `install_setup.py`: whether this install has finished Setup.
- `launcher_wire.py`: a launcher as a caller names it, and as `/config/launchers` holds it.
- `serving.py`: the ports this process answers on.

**`common/media/`** - the art a game or a manufacturer carries, how it is named and sized, and browsing the disk for the files that hold it.

- `media_specs.py`: canonical media keys, filenames, playfield attributes, and path resolution.
- `sized_media.py`: art at the size it is drawn, and the version a browser can keep it under.
- `manufacturer_logos.py`: logos keyed by a game's manufacturer, in a folder of their own.
- `folder_browse.py`: listing this device's folders and files for a path field's Browse button.

**`common/release/`** - what an update tells a person and asks of them.

- `release_notes.py`: the notes an update carries, read back for the versions a person has not seen.
- `whats_new.py`: whether this start is the first after an update, and what there is to tell about it.
- `upgrade_needs.py`: steps an update asks a person to take, each declaring the version that introduced it.
- `migration_ledger.py`: what an update did to a person's files, kept so the review of it can say so.

**`common/players/`** - the people who play on this install.

- `players.py`: who plays on this install and who the next game counts for. Kept players are in `players.json`; guests and who is up are held in memory and gone when VPinFE closes. Every comparison of two sets of initials goes through `same_initials`, `Roster.whose_score` says whose a new score is, and every change is announced as `players.changed`.
- `player_pictures.py`: a kept player's picture, one square WebP named for the player's id.

**`common/security/`** - who may do what, and what this install trusts.

- `machine_secrets.py`, `access_keys.py`: the secrets this install makes for itself, and the two keys it holds in plain because it must hand them on (the Remote pass, a pairing token).
- `api_tokens.py`: the tokens a program holds to call the API, kept as hashes.
- `sign_in.py`, `authenticator.py`: signing a browser in with a code shown on the device, and the authenticator app's six digits as a second way.
- `approvals.py`: a program asks first to make one Protected change, and the Owner decides.
- `accepted.py`: the themes and extensions the Owner has accepted that VPinFE did not ship.
- `encrypted_connections.py`, `pinned_certificates.py`: which connections count as encrypted and the certificate this install serves HTTPS with, and the certificate a paired install showed when paired.
- `http_client.py`: shared request/download helpers, which check a paired install's pinned certificate before they connect.

**`common/extensions/`** - the extension host: loading somebody else's code, and
containing it when it breaks.

- `contract.py`: the manifest and the shape of the context. The only module of ours an
  extension imports.
- `context.py`: what `register(ctx)` is handed - a logger, a config namespace, the bus and
  a way to offer routes. Never the application.
- `host.py`: the registry, the load path and the kill switch.
- `store.py`: an extension's own settings, and the `extensions.switched_off` list that
  keeps one from loading.

Depends on the infrastructure layer only. The gate that decides whether an extension's
route may be reached is in `httpapi/extensions.py`, because that is where a scope means
something. `docs/extensions/reference.md` indexes the contract an author reads.

**`common/games/`** - games, their metadata, and the collections built from them.

- `game.py`, `game_parser.py`, `game_repository.py`: game discovery and cached game rows.
- `game_metadata.py`, `info_file.py`: `.info` file schema, defaults, display helpers, and persistence. `metaconfig` also versions the `VPinFE` section and migrates it forward on read.
- `game_identity.py`: the stable per-install game id that addresses a game everywhere.
- `tables.py`: which table in a game folder plays when nobody names one. Every caller resolves through `default_entry()` or `offered_tables()`.
- `metadata_service.py`, `game_report_service.py`, `game_play_service.py`: workflows over games and metadata.
- `player_records.py`: what each player other than the owner has done with each game - plays, time, best score and rating. A kept player's is `player_records/<id>.json` in the config directory, a guest's is held in memory. The owner's is the library's `.info`, which nothing here writes. `remove_player` is how anyone leaves: it takes them off the roster and forgets their accounts with every extension and their record.
- `collections_service.py`, `collection_store.py`, `collection_filters.py`: collection and filter logic. `collections.ini` carries its own schema version in a reserved `[VPinFE]` section.
- `formats/`: readers for the file formats a table ships in - the .vpx, the .directb2s, VPReg, script names and DMD frames. None imports a module of `common/games/` outside the package.
- `collation.py`: ordering and grouping titles nobody wrote in English. `media_probe.py`: what a media file is, read from its own header.
- `score_parser.py`: PinMAME NVRAM score extraction.
- `game_service.py`, `game_index_service.py`, `media_service.py`, `asset_registry.py`, `archive_service.py`, `export_bundle.py`: workflows over a game and its media - rating, re-matching, the cached row index, media lookup and invalidation, and packing a game for export.

**`common/backups/`** - every copy VPinFE keeps of a file, listed in one place.

- `copies.py`: a copy as a row, and the opaque id a client holds for it. The id carries where it is kept and is checked there, so one made by hand reaches nothing.
- `places.py`: where each kind is kept - beside a game's `.info`, in the config folder, under a launcher's backups, beside VPX's own ini, as a theme's `.previous` - and how each is read off the disk.
- `archive.py`: Back up now. One zip per press, written to a temporary name and renamed, holding the files and a `manifest.json`; a game's file is its `.info`. Where a file goes back is worked out when it is restored, from its id and name.
- `ops.py`: take, restore and delete. A restore keeps what it replaces as another copy. A zip restores whole or one file at a time.
- `pending.py`: restores of the config folder's files that wait for VPinFE to close, because the running app writes them back. A file from a zip is taken out when the restore is asked for, so deleting the zip does not lose it.

Depends on `common/games/`, `common/online/` and `common/host/`.

**`common/support/`** - what a person attaches to a report: a system summary and the logs, with private details replaced.

- `redact.py`: the only place text is cleaned. A `Redactor` is made from one `Identity` and cleans literals first, longest first, then patterns, with a stable numbered placeholder for each distinct value (`[[user-1]]`), so a value reads the same in every file. Originals live in its ledger in memory, for the person's own screen, and are never written into a package. Cleaning twice changes nothing. A string it cannot name but that looks random is hidden as a precaution and listed.
- `identity.py`: `build_identity()` reads who and what is private from the install's own stores (account, host, devices, players and their accounts, install id, library folders, table names). Each reader fails to nothing, never to an error.
- `sections.py`, `collect/`: the summary is a registry of `Section`s, one module and one line each. A collector reads through readers that already exist, never raises and never redacts; a failure is `not_reported` with its reason.
- `collect/displays/`: the Displays And Layout section. `desktop.py` names the session (system, display server, desktop, container, virtual machine, distribution). `probes/` holds one probe per source (`xrandr`, the sway and Hyprland sockets, `kscreen-doctor`, Mutter over D-Bus, `wlr-randr`, `wayland-info`, `os-release`), each `probe(env, run) -> Reading`, which never raises and parses by allow-list. `gather.py` runs the chosen probes in parallel with a time limit per program and in total. `compare.py` holds the rules that set VPinFE's screens, the desktop's and Visual Pinball's settings against each other. A rule with a missing fact says nothing. Fixtures for the parsers are under `tests/fixtures/displays/`, each listed in its `PROVENANCE.md` as captured or synthetic.
- `session.py`: the desktop session, from an allow-list of environment variables and never the whole environment.
- `package.py`: `build_package()` is `collect()` then `assemble()`. `collect()` reads the device once into a `Raw`. `assemble()` cleans every byte through one `Redactor` and zips it once with fixed entry names and times, in English whatever language is set (`i18n.in_source_language()`). Toggles, hidden words, left-out log files and removed lines only reassemble. A removed line is replaced by `REMOVED_LINE` so the numbers of the lines after it do not move, and the README and summary cannot be left out. `hold()` keeps a built package in memory for 15 minutes, two at most, under a random id.
- `draft.py`: a `Draft` is one person's work on a package: what `collect()` read, the choices made since, and the `Package` they produce. The Create Support Package dialog (`manager/system/support.py`) runs in the Manager's process and holds one for as long as it is open; `drop()` lets go of everything read. `Package.originals` maps a stand-in to the value it replaced and is for that person's own screen only: it is not in the zip and the API never serves it.
- `pieces.py`: a line cut where a stand-in sits, for Inspect Everything, and a search over every file of a package.
- `destinations.py`: the Discord summary (under 1,500 characters, read from the summary's own Markdown labels) and the prefilled GitHub issue link, built from an allow-list of non-identifying facts. Both are plain addresses a browser opens. Nothing is uploaded from here.
- `banner.py`: the block written at the start of every run, free of host and user names.

Depends on `common/host/`, `common/games/`, `common/extensions/` and `common/devices/`. `httpapi/support.py` and `vpinfe --support-package` are its front doors.

**`common/uploads/`** - getting files into the library.

- `working_folder.py`: where an import keeps its files, a `.import-working` folder in the New Games Folder, so the work happens on the drive the import ends up on. It checks for room before an import starts, says a full drive in plain words, and never falls back to the system temp folder.
- `upload_session_service.py`: an upload in progress. Files arrive in chunks into a session directory and every path is checked against it, so an upload naming `../` is refused rather than resolved.
- `asset_analyzer_service.py`: what a zip, folder or loose file holds, worked out without extracting it.
- `asset_import_service.py`: an analyzed drop becomes a plan, and only then files on disk - so an import that would overwrite something is declined rather than undone.
- `jdiff_patch.py`: applying a JojoDiff patch in pure Python.

Depends on `common/games/`, never the reverse: importing assets needs to know the library it is importing into.

**`common/online/`** - services reached over the internet.

- `vpsdb.py`: compatibility facade for VPS database lookup and media download.
- `vpsdb_cache.py`, `vpsdb_media.py`: VPS database cache/update and VPinMediaDB download helpers.
- `themes.py`: compatibility facade for Manager theme registry operations.
- `theme_registry_client.py`, `theme_installer.py`: theme registry network and local install helpers.
- `app_updater.py`, `pinmame_score_parser_updater.py`: update checks and downloads.
- `sources.py`: every place that offers files, behind one contract and one registry. A source answers up to seven questions - what it is (`id`, `name`, the `kinds` it serves, whether it names `versions`), `refresh(config)`, `listing()`, `offers(who, kind)`, `upgrades(who, kind)` (asked for the `kinds` it serves, or for those it names in `upgrades_for` where it serves none), `take` and its siblings, and the `automatic` settings by which it may act unasked - and only the first is required. A source with `offers` and no `take` is a catalog: it answers with `Offer`s and core fetches each address. The sources that ship (`vps_source.py`, the spreadsheet, first and with no switch; `vpinmediadb.py`) register through the same `register` as an extension's `contribute`, so a new source is the code that talks to its site and one registration, and the API, the Discovery pane and Settings read the registry and nothing else. `register` refuses a kind core has no slot for.
- `sources_watch.py`: the one daemon thread that keeps every source current. Each hourly wake asks each enabled source that can refresh, stamps what a changed source lists into `arrivals.py`, then starts the comparison of the library with the spreadsheet if one is owed. One is owed from the start of the process, again whenever the spreadsheet changes (the watch's own refresh, or a sync asked for by hand), and again after a match, an unmatch, a replaced table or a scan, which starts once those have stopped for three seconds. A game re-read with a different match or different table releases also has its entry in the stored count rebuilt at once (`library_vps_state.follow`, from every game that names the entry), so Discover does not wait for the comparison. The comparison's own tying of tables owes nothing. It is skipped while there is no catalog file, and stays owed when it could not start. It is a job (`library.vps_rollup`) that the Manager shows as *Compare the Library With VPS* and never starts.
- `arrivals.py`: `STATE_DIR/arrivals.json`, what only time can make - when a source's item was first seen and the version it held before it last changed. It exists for two jobs: dating items a source gives no date for, and naming what a re-dated release was. A source's first listing is stamped as already seen so its backlog is never news, and a source that lists nothing yet is not stamped at all.
- `vps_marks.py`: `STATE_DIR/marks.json`, what a person has said about a VPS entry or release - Wanted or Ignored, never both. A mark names its thing by scope, id and detail; for a `gap` the detail is the Home tab it is ignored from. A file that cannot be read or is from another schema is moved aside to `marks.json.unreadable` before the next write, so a write never destroys it. `vps_marked.py` draws the Wanted shelf and, in `listed`, every mark that names something the catalog lists, which is the Marks grid's rows; `vps_home.py` leaves what is ignored off the others. `vps_marks.met` takes Wanted off the releases a recount found the library holds, which `library_vps_state.recompute` calls once it has stored the count; a game marked Wanted is a plain mark and stays.
- `vps_updates.py`: Updates for Your Games from what `games/library_updates.py` found. `library_updates.found()` (`found_for(game_id)` for one game) reads the library as it stands - the `source` block of each table and of each file in the assets ledger that is still in the folder, two matched to one VPS release at one version being one move, and what `sources.upgrades` says is later - and compares the version VPS lists now with the one recorded at the match (`ties.update_available`), never the file's own. A table's move is dropped where another held file of the game is already that VPS release at the listed version or later: one matched to it, an unmatched one by its own version, and not one matched to another VPS release, declined, absent, or made by a patch. Nothing is stored, so an update clears the moment the VPS release is matched again. `vps_updates.shelf` leaves off what is ignored (a game, a VPS release, `updates` on a VPS release for every later version, `version` for that one), and a picture a source has replaced by the marks the Discovery pane writes for it (`picture_changes.set_aside`) and answers `{count, game_ids, games}`.
- `cache_stamps.py`: what each cache in `CACHE_DIR` was last told. `theme_level.py`: which contract an installed theme was written against.

The VPinPlay client is no longer here. It reached down into `games` to enumerate the
library, which is the wrong direction, and it now lives in the VPinPlay extension and is
handed those records instead of reaching for them. What core keeps is the `[vpinplay]`
config section, so an install that already had one hands its settings over and a revert
to 2.x still finds them.

**`common/host/`** - this device: attached hardware, the launcher, the running session.

- `dof_service.py`, `dof_service_worker.py`, `libdmdutil_service.py`: hardware service facades. A real-DMD video is decoded by FFmpeg to the panel's size and sent frame by frame by `libdmdutil_service.py` itself, looping until the next selection or a launch stops it.
- `dof_config.py`: where DOF's config lives, `directoutputconfig` in Visual Pinball's preferences folder. The frontend's DOF helper is given the folder above it as its base path, so it and Visual Pinball's own DOF plugin read the same files.
- `peripherals.py`: DOF and real-DMD, driven by game lifecycle events. Each device is its own handler, so a new one is a new subscriber rather than an edit.
- `real_dmd.py`: which video and picture a game shows on a real DMD panel (preferred video, the other video, preferred picture, the other picture), sent on a worker thread.
- `launch.py`, `launch_state.py`: starting a game - resolving what to launch it with, building the command, running it and recording the play - and whether a launch was requested from outside the frontend.
- `frontend_state.py`: what the frontend is showing, as it last reported, and the door a request to switch its collection or move its wheel goes through. The frontend registers what answers it.
- `frontend_browser.py`, `frontend_windows.py`: what the frontend's browser and each of its windows reported about themselves (`report_browser`, `report_window`): the formats it plays, what it can draw and decode, and where each window opened. A report is what was last said, not a promise the frontend is still up, so a reader checks `frontend_state` first. Everything a page sends is untrusted and bounded before it is kept.
- `tools.py`: the Tools registry - each program VPinFE runs on this device but does not ship, found through its setting, `PATH` and the places a short `PATH` misses, probed for what it can do, and the hint a person follows when it is missing. A new Tool comes with its `tools.*` setting, and a check holds the two lists equal.
- `vpinos.py`: whether this device runs VPinOS, for what is VPinOS's alone.
- `placement.py`: window rules for Sway and Hyprland - VPinFE's own rules file (each window to its screen by output name, no border, the playfield focused, VPX's backglass, score view and topper never), the one `include` or `source` line the person's config needs, and whether it is already there. Nothing here writes the person's config except `add`, and a Sway config is never created, since one of the person's own replaces the system's. The Hyprland syntax follows the documentation and has not been run against a Hyprland session.
- `keys.py`, `key_simulator.py`: a key by the code a binding names it with, and pressing one into the running table - through `wtype` or `ydotool` on Wayland, pynput elsewhere - with `sender()` saying whether this device can.
- `key_reader.py`: while a table plays and a key is bound to Take Picture, the keys bound to the play-time actions, heard from outside the frontend's page and pressed as those actions; `hearing()` says whether this device can.
- `third_party.py`: finding and loading the third_party libraries the build bundles.
- `input_actions.py`: input produced by something that is not the browser.
- `display_service.py`, `system_actions.py`, `vpx_log.py`.

**`common/capture/`** - recording this device's own screens into its tables' media.

- `adapters/`: how this session's screens are reached, chosen from the session's variables, and which output each window is on. `wlr.py` is sway and Hyprland.
- `geometry.py`: the turns a recorded frame takes to its stored orientation, as one set of FFmpeg filters.
- `settings.py`: the `capture` settings, with a run's own values in place.
- `preflight.py`: what this device can record and why not, the report `GET /capture` serves.
- `pipeline.py`: the FFmpeg command lines from a recording to the stored file - turn, constant rate, size, format, pictures, sound.
- `session.py`: one table - launched, waited for, recorded, closed, then encoded and placed.
- `run.py`: what a recording would fill and replace, and starting one as a job.
- `freeze.py`: Take Picture during play - the table paused with its own key, every screen taken at once, the table resumed - and `compose.py`, the screens as one picture laid out as the cabinet is. The picture goes to the game's `pictures/` folder (`common/games/pictures.py`).

Depends on `common/host/`, whose Tools it runs.

Three cross-package edges are deliberate: `games` reads VPSdb through `online`
when building metadata, and `online`'s VPinPlay client reaches into `games` to
enumerate the library. That last one is the wrong direction; VPinPlay predates the
extension model and is expected to become an extension.

## Design Rules

Naming, comment density and lint rules live in `docs/conventions.md`.

Keep constructors cheap when adding new shared classes. If a class needs network,
filesystem mutation, or long-running scans, expose explicit methods such as
`ensure_current()`, `load_registry()`, or `apply_patches()`.

Prefer facade compatibility over broad caller churn. Existing imports like
`from common.vpsdb import VPSdb` and `from common.themes import ThemeRegistry`
remain valid, while new behavior can live in smaller modules behind them.

Use `game_metadata.py` for display and fallback accessors. New game filtering,
sorting, or row-building code should use helpers like `game_title`,
`game_themes`, `game_type`, `game_manufacturer`, `game_year`, and
`game_rating` instead of repeating `Info`/legacy `VPSdb` fallback logic.
These read the game; a table's own manufacturer, year and type are its parsed
fields and are read off the table entry, never through these.

Know which game id you want. A game row carries several, and they are not
interchangeable:

- `vpinfe_id` is this install's stable local id from `game_identity.py`. It
  identifies the game - in the HTTP API, in events, in jobs, in collection
  membership, and as the row key in the Manager games grid.
- `vpsid` and `altvpsid` are VPS-derived (`Info.VPSId` and `VPinFE.altvpsid`).
  They correlate with VPSdb, VPinPlay and other services keyed by them. Read the
  one you mean; there is deliberately no combined `id` field to reach for by
  accident.

Use `game_identity.game_id()` to read one, `ensure_id()` when you need a game
to have one. Reading never mints, so game scans stay a read path.
`game_repository.get_game_rows()` is the exception and calls `ensure_unique_ids`:
a row is addressed by its id, so a game imported since startup has to be given one
rather than appear with an empty key that collides with every other such game.

Collection membership is keyed by `vpinfe_id`. A VPS id could not do the job: it is
empty for a game VPSdb never matched, it is not unique, and it is cleared when the
.vpx changes - so membership recorded under one was orphaned by an ordinary game
update.

Both membership paths tolerate VPS-keyed entries, and both have to. The migration
leaves an entry alone when no game matched it - the game may simply not be
installed yet - and it runs only once, so such an entry can stay VPS-keyed
indefinitely. `CollectionStore.is_member` covers the frontend;
`game_repository._collections_for` covers the Manager row. If only one of them
did, a game would show its collections in one place and not the other.

Launch games through `host/launch.py`. It is the only place that starts a game
file, and it is what makes a launch mean the same thing wherever it came from -
the wheel, the Remote Control page and the HTTP API all call it. When there were
two implementations they drifted, and only one of them recorded that a game had
been played.

Anything a particular caller needs around a launch is a subscriber, not an
argument. The frontend's window messages and its last-game record live in
`frontend/play_events.py`; the peripherals live in `host/peripherals.py`. Nothing
about a specific caller belongs inside the launch itself, which is how the window
messages ended up firing only for launches the wheel started.

Choose the right kind of handler. A **hook** is part of the operation: it runs in
priority order, the publisher waits, and raising stops the operation. A
**subscriber** is only told what happened; order is not promised and a failure is
logged and contained. Releasing the peripherals is a hook because launching with
them still held would be wrong; anything that merely wants to know about a launch
is a subscriber and must not be able to prevent one.

`game.selected` is deliberately subscribers-only. It fires once per wheel stop and
drives decoration - a DOF effect, the art on a DMD panel - so a handler that raises
has failed to decorate a selection, not failed to select. Registering a hook on it
would let a dead device stop the wheel.

An event payload is in-process, so it can hold whatever a handler needs - a `Game`,
the ini config. What reaches the network is a separate decision: `httpapi/events.py`
projects each streamed event into its own shape, so adding an argument here does not
change what an outside subscriber sees. Adding an event means deciding whether it is
streamed at all.

Say when the library changed. `game_repository.refresh_game` announces `game.changed`
and `CollectionStore.save` announces `collections.changed`, so anything holding a view
of the library can re-derive it. Announced at those two chokepoints rather than at each
caller, because every path that edits a game already comes through the first to be
re-read and every path that edits a collection comes through the second to be written.
Re-reading a game *replaces* the object rather than mutating it, so a holder of the old
one is stale with no way to notice - which is why the announcement has to exist at all.

Start, stop and restart things through `common/runtime/lifecycle.py`. A request names a scope
(`frontend`, `vpinfe`, `system`) and an action (`start`, `stop`, `restart`), so rebooting
the device is `restart` at system scope rather than a verb of its own, and the
frontend, the Manager, an extension and a signal all arrive at one place. Reaching
for the browser directly is what used to skip `shutdown_services` and lose a session's
play data on the way out.

A request also carries **where it came from**, not just what kind of surface it was.
A confirmation is put to the surface that asked and nowhere else - a dialog raised on
the cabinet because somebody pressed something on their mobile device is a hang on a screen
nobody is watching. Every other surface is told through `lifecycle.acting`, which is
subscribers-only for the usual reason: being told is not being asked. A request nothing
can ask, like a `SIGTERM`, proceeds rather than waiting for an answer that will never
come.

Never pick a game's `.vpx` yourself. A folder can hold several, and picking
differently from everyone else means the metadata a user sees describes a different
file than the one that launches. Use `tables.default_entry()`.

Use `config_access.py` when reading common INI values from code outside the
configuration editor itself. This keeps defaults and bool/int coercion in one
place while preserving `ConfigStore.config` for compatibility.

Use `common/media/media_specs.py` for media keys, filenames, and playfield path attributes.
Frontend payloads, parser discovery, and VPS media downloads should not each
carry their own filename table.

Use `jobs.JobReporter` for long-running workflows that need both logs and UI
progress callbacks.

Run anything slow through `jobs.submit` (start it on its own thread, hand the
caller a job) or `jobs.track` (wrap work already running on the caller's thread).
Both publish `job.progress` / `job.done` / `job.failed` in the shape `events.py`
documents, so a workflow looks the same on the event stream whether the Manager
or the HTTP API started it, and both enforce one job per kind — which is what stops
two library scans rewriting the same `.info` files. `track` chains to whatever
callbacks the caller already had, so an existing progress bar keeps working.

Use `common/security/http_client.py` for common network GET/download behavior unless a service
needs a special request shape such as POST.

Use `common/host/third_party.py` to locate and load a bundled third-party library. Where one
lives depends on whether this is a source checkout or a frozen build, and DOF and
libdmdutil should not each carry their own copy of that reasoning.

## Adding A Shared Workflow

1. Put UI-free business logic in `common/`.
2. If callers already depend on an older class or function, keep that public API
   as a facade and delegate into the new module.
3. Use `common.config.paths` for user paths and config locations.
4. Add focused tests for the helper or service boundary.
5. Update frontend or Manager docs only when their caller contract changes.

## Adding Metadata Fields

1. Add the default or normalization rule in `info_file.py` or
   `game_metadata.py`.
2. Add display/read helpers in `game_metadata.py` when multiple callers need the
   value.
3. Update row builders or filters to consume the helper rather than reading raw
   JSON directly.
4. Add a test covering both current `Info` fields and any legacy fallback.

## Adding Media Types

1. Add the media key, playfield attribute, and filename template to
   `common.media.media_specs.MEDIA_SPECS`.
2. Use the shared filename/key maps in the Manager, parser, and download code.
3. Update theme documentation if the media becomes part of the frontend API.

## Adding Networked Services

1. Keep the network client small and injectable where practical.
2. Avoid network work in constructors.
3. Store downloaded cache files under `common.config.paths.CONFIG_DIR`.
4. Catch request failures at the service boundary and return a clear empty or
   error result for UI callers.
