## VPinFE Release Notes

### New in beta.2

#### New
- **Manager** - The Remote, the page you open on a mobile device to drive VPinFE, is redone with three tabs: Wheel to browse and launch, Games to browse the library, Now to control the running table. It can be installed as an app.
- **Manager** - Table Controls: from the Remote, press the running table's own buttons (Start, flippers, Launch Ball, coin door, service keys, Pause).
- **Manager** - Drop files, an archive or a folder onto the library. VPinFE works out where each file goes, asks you when more than one could fit, and writes nothing until you confirm.
- **Manager** - Convert pictures, sounds and videos a device cannot play, as you drop them or all at once from the Media view. Originals are backed up first.
- **Manager** - Support Package: one button gathers system details, screen details and logs, with private details removed, to attach to a bug report.
- **Manager** - The asset picker has an Online tab to find media for a game on VPS and VPinMediaDB.
- **Manager** - The browser's Back and Forward buttons now move between Manager pages, dialogs and setup steps.
- **Manager** - The frontend Menu shows the Manager's address and a QR code, to open it from a mobile device.
- **Manager** - A game's Assets lists a section for each kind of file the library collects, with Add in its heading and a row for each file, so a game can hold several of any kind. ROM files and PUP packs are a row each, with Add, Replace and Remove, and a pack's folder can be removed. A file's card says whether it belongs to the game or to a table, shows the VPS release it is matched to, and can be matched from there.
- **Manager** - The rotation check shows a test card and asks "Is the Playfield Rotated Correctly?" Looks Right then reopens the open windows, so the turn takes effect at once.
- **Manager** - Create Settings File: makes the settings file a Visual Pinball install is missing.
- **Manager** - Setup asks before it changes a screen Visual Pinball already has set. The question comes once, after you confirm the Visual Pinball program, as one dialog, "Change Visual Pinball's screens?", with Leave as Is and Match VPinFE. Screens it leaves unset are filled in without asking, and the Screens step itself shows only VPinFE's screens.
- **Manager** - Setup and What's New offer to tell you about new beta releases.
- **Manager** - Discovery shows maker and year on suggestions, and lists a mod under the table it modifies.
- **Manager** - The Record panel says which screens are asleep and checks again on its own, and can record a table's Full Single Screen view as a picture (ticked off until you tick it, since it launches the table a second time).
- **Frontend** - Show Numbers windows now open in the right place on Sway and Hyprland.

#### Improved
- **Manager** - A table is named by the release it is matched to. A game's default table is chosen from a short list with Automatic first.
- **Manager** - Plainer words: VPS release, Matched and Skip, where it said tied and Leave Out.
- **Manager** - Each problem is said once on the Game tab, and a failure across many games once.
- **Manager** - Won't play counts only the devices that play your library.
- **Manager** - The authenticator is optional on update and is no longer offered on first launch.
- **Manager** - The Input page and Setup explain how mapping works. The Controls step keeps that explanation open, cut to two sentences, and the switch for mapping a device's buttons from another device has a closed "What does this do?" under it. It says the device redirects its buttons, pauses while a table plays, and turns itself off after 2 minutes without a press.
- **Manager** - Setup's playfield screen step lists where each size came from: Your screen reports, Already set in Visual Pinball, I measured it, or Leave Visual Pinball's own size. The one Visual Pinball already holds is picked on arrival, so Next changes nothing. Each question has a picture, and the step says where to change the answers later.
- **Manager** - The playfield screen's slope is called Screen Angle in Setup and on Visual Pinball's Displays page, with Inclination in Visual Pinball as its tooltip. An angle you have not measured shows Not measured, with a hint to use a level app on the screen.
- **Manager** - Setup's card is wider and taller on every step, so steps no longer feel cramped.
- **Frontend** - The main menu's address and QR code section is clearer: the headings read Manager, Remote Control and Join as Player, the Manager addresses show their http:// or https:// start, and the text and QR codes are larger.
- **Manager** - A media file has a panel with an Edit row, and the other files of a kind, which take over if the one above is removed, each get a panel of their own.
- **Manager** - The media card is tidier: no name row, Path cut from the start, the Edit toolbar above the picture, and Open in Media is a button like the others. Used by is a chip, This table, This game or Library default, and a stand-in file says which set or kind it stands in for. A thin line separates the name from Details on the media and Assets cards.
- **Manager** - The Media list shows two tiles to a row, each in its own shape within a 240 by 120 box and centered, with the types you switch off closing the gap. Table Audio and Launch Audio fill the whole box, with a sound icon. Download Missing Media... and Record Media... stay in a strip above the tiles. Audio is now called Table Audio.
- **Manager** - On Discovery, the releases VPS lists that a game does not hold are headed Online, not Available, and a game you hold shows its Have chip beside its name on the Home shelves.
- **Manager** - Smaller touches: the Settings list keeps its place, and a patched table matches the release it came from.

#### Fixed
- **Manager** - Matching a ROM file to its VPS release now keeps the match.
- **Manager** - A fresh install, or one with no screen, no longer gets stuck or floods the log.
- **Manager** - Opening the Remote from its QR code no longer blocks signing in with a code.
- **Manager** - The Remote's tab bar is no longer hidden under Safari's toolbar on a mobile device.
- **Manager** - On macOS the Remote's right flipper no longer presses the left one.
- **Manager** - A Windows device's file paths now read correctly when you pick a source.
- **Manager** - The main menu no longer leaves the previous page showing.
- **Manager** - A long value no longer stretches a game's panel past its edge, and long file names in the table list are cut at the front.
- **Frontend** - Backglass and other display windows no longer flash the wrong game while the frontend starts, and the splash no longer hangs on a video the browser cannot play.

#### Removed
- **Manager** - VPX Patch Scripts support is removed, since the VPX engine no longer needs them: the script fix source, its Tables column and notice, and the `--vpxpatch` option. Take `--vpxpatch` out of any command that passes it. Scripts already beside their tables run as before, and Table Patches (.dif) are unaffected.

#### For theme and API authors
- **Core** - Import stops on a drop with unanswered choices (HTTP 422, `choose_one`), and its report gains `written` and `not_written`.
- **Core** - `GET` and `POST /api/v1/play/controls` list and press the running table's controls.
- **Core** - The Remote's `?screen=` takes `wheel`, `games` or `now`.
- **Core** - A game's files and folders can be renamed, moved or removed, and a folder made, over the API. Each call is asked about first, so what it would change is known before anything is written.

### Summary
VPinFE 3.0 rebuilds and expands the Manager: one place on this device or any other to run
your library, set up your screens and controls, and keep it all backed up. Setup walks a new
install through its first run, or imports tables and settings from another front end. An
install from an earlier version upgrades on its first start and keeps a copy of everything it
rewrites, so read the next section first.

### Before you upgrade
- **Update the earlier install to its latest release first, even if you plan to stay on 3.0.**
  If you go back, only the latest release of the earlier version knows how to put 3.0's
  backups back. Older releases do not look for them.
- The first start rewrites every game's `.info` and moves your collections onto game ids.
  Your settings are read once from `vpinfe.ini` into a new `vpinfe.json` beside it, and the
  ini is left where it is. The original of each `.info` is kept beside it as
  `<name>.info.vpinfe-<timestamp>`, and collections are backed up the same way. Expect a
  burst of writes on the first start and nothing after.
- To undo it, open System > Backups and press Go back, or run `--restore-info`.
  That puts back each game's metadata and your collections. Clean up on the same page
  deletes the copies once you are sure you are staying.
- To take a 3.0 development build back off entirely, `--reset-3x-state` removes the
  settings and collections files 3.0 wrote, the `.info` files it made and every backup it
  kept, so the next start migrates from scratch. Settings you changed under 3.0 are lost,
  and so is any rating or play count on a game 3.0 added. It keeps a copy of your device
  list first, because a mobile device you added manually is the one thing nothing
  re-announces. It refuses while VPinFE is running; `--dry-run` lists what would go, and
  `--config-only` leaves the library alone.
- **Other devices sign in once, with a code.** A browser on another device asks for a
  code and VPinFE shows it on this device, where whoever is there chooses Show or Ignore.
  Settings > Security lists the browsers signed in and ends any of them. This device never
  asks. A change that can run programs or cannot be undone, such as installing an update or
  restoring a backup, asks another device for a second Confirm code, and skips it over a
  VPN, behind a proxy or on HTTPS. An authenticator app set up on the same page can give
  both codes. Themes and extensions that are not included with VPinFE stay off until you accept
  them. Traffic on your network is not encrypted, so away from home connect through a VPN.
  Set Reachable From in Settings to This Device Only to keep other devices out altogether, at
  the cost of the Manager and the Remote working from other devices.

### What's New
- **Manager** - Other devices sign in to VPinFE once, with a code shown on this device
  or with an authenticator app. This device never asks. Settings > Security lists who is
  signed in and signs any of them out, makes tokens for scripts and other programs, and
  names the networks and proxies whose traffic is encrypted. VPinFE can serve HTTPS from a
  certificate you choose.
- **Manager** - A new interface, and the page that opens first. One place for the library,
  media, devices and settings, in grids you can sort, filter and search, with a picture
  beside each name. Column layouts are kept as views of your own. Select a row and its panel
  answers for it; select several and change them together, with one Undo for the lot.
- **Manager** - Overview is the first page: the game last played, what needs you ordered by
  what leaving it costs, the size of the library and the latest jobs. Each check behind a
  row has a switch in Settings > Library > Checks, and Stop Checking on a row turns it off.
  Setup asks the first-run questions as a desktop or as a cabinet. What's New opens once
  after an update and again from About.
- **Manager** - Drop a file, a zip or a folder on the Manager to make a game, or on a game
  to add to it, and read what will happen before anything is written. Long work runs as a
  job, so you can leave the page and read how it went in Jobs.
- **Manager** - A page address opens on the same game with the same part of its panel
  showing, so a game can be bookmarked or sent to someone. The Manager can be set to System,
  Synthwave, Dark or Light.
- **Core** - Every game gets an id, minted once and kept in its `.info`. It survives
  renaming a folder and updating a `.vpx`, which a VPS id does not. Collections hold their
  games by it, so an ordinary table update no longer drops a game out of its collections.
- **Core, Manager** - A game folder can hold several tables. A desktop table and a VR
  table, or a table and a patched variant, are peers: each launches on its own, one is the
  game's default, and any of them can be hidden so it stops being offered. Games lists one
  row per game and Tables one row per `.vpx`.
- **Core, Manager** - Import a library from PinballX, PinballY, PinUP Popper or a plain
  folder of tables. The import shows each page of what it found, asks only what it cannot
  settle, and ends with a summary of exactly what it will do. It runs as a job and keeps a
  report of what needs review afterward. Games are matched to the Virtual Pinball
  Spreadsheet, and several library folders can be kept at once.
- **Manager** - Match a game to the Virtual Pinball Spreadsheet from a picker, see how the
  match was made and go back to the earlier one. Take a game's details from the spreadsheet,
  type your own over any of them, and revert a field with a click. See which tables have a
  newer version on the spreadsheet. VPinFE matches a table to its VPS release by itself when the
  spreadsheet names it exactly, and an unbind you make is kept.
- **Manager** - Add a file to a game by finding it on the Virtual Pinball Spreadsheet. Every
  asset picker has an Online tab that lists what the game has and the other files listed for
  it, with VPinMediaDB's own files first. Pick the file, drop yours, and it is saved as that
  file, so Discovery can tell when a newer one is out. Opening a file's page on the
  spreadsheet picks it too. A game that is not matched yet can be matched from the same tab.
  ROMs, PUP Packs, AltSound, Alt Colors, tables, backglasses and the media kinds all work the
  same way, and Save It As shows whether a file goes to the game or to one table.
- **Manager** - A file picker opens in the folder the last file of its kind came from. Places
  lists it first as Last Used. On the device VPinFE runs on, upload and that
  device's own files are one This Device tab; from another device the tab is On and the
  device's name, so it is clear whose files they are. A drive that is
  unplugged opens at the game's folder and says why.
- **Manager** - Discovery sets your library against every game and release on the Virtual
  Pinball Spreadsheet, and keeps the comparison current on its own. It opens on Home, a set
  of shelves that each answer a question, such as which of your games have a newer version,
  what is new on VPS, what a game of yours still lacks and which games you don't have. Each
  shelf can be switched off and put in order under Settings > Library > Discovery.
- **Manager** - All Games and Releases show the same comparison as grids, each kind of file
  reading Matched, Unmatched or Missing, with search by author, maker or theme. A game in the
  search suggestions shows its maker and year, so two games of one name can be told apart, and a
  mod sits straight after the release it is based on in Available. Ignore a game,
  a release or a missing kind to leave it off Home, mark a game or a release Wanted to put it
  on a shelf, and find both in Marks, each with an Undo.
- **Manager** - A column that answers one question about each row, such as whether a game is
  matched or a ROM is installed, reads the same way on every page. A green tick is in place,
  a question mark is not known yet, amber is something VPinFE needs you to fix, red stops
  play, and grey is a choice you made or a gap that costs nothing. Rows sort from most in
  place to least, and a filter lists each state with how many rows hold it. VPS Match now
  tells Matched from Cleared, a match you took off, from Unmatched, and VPS Entry reads
  Shared where two games are matched to one entry.
- **Manager** - Assets lists every ROM file a game holds, with Add, Replace and Remove, so a
  game keeps more than one ROM file, and a ROM that matches a table says so.
- **Manager** - A video slot marked Won't play names the codec and the device, such as
  Won't play on Den (H.264), and counts only the devices that play this library. A device
  that is only paired, or that plays another library, is left out, and the hover says when
  each device last reported.
- **Manager** - Collections are smart (filled by rules on letter, theme, type, manufacturer,
  year, rating, tags, favorite and played) or hand-picked, ordered and capped as you choose.
  Tags are your own words on games and tables, with rename, merge, color and a clear-out for
  the unused.
- **Manager, Core** - Media has 22 kinds, including instruction cards, toppers and topper
  videos, loading videos, launch audio, rule sheets and logos, and a game's coverage is laid
  out like the machine. Flyers and instruction cards can have several pages. Pictures and
  videos turn a quarter or a half turn with the original kept. Gaps fill from VPinMediaDB,
  from a game's backglass file, from a file of your own, or by recording the table running.
  Remove takes the default-named file (a wheel.png, say) when a game has none of its own,
  and says which tables lose it. A file that a new one replaces, by a drop, an import, a
  download or a recording, is kept in Backups first.
- **Manager** - Assets lists each supporting file a table needs (backglass, settings, script,
  point of view, score view layout, Nixie layout) and what is missing or left over. A table's
  INI, script, point of view or score view layout can be edited there, with a copy kept in Backups each
  time you save. A table's script can be extracted to a `.vbs` to patch. A backglass,
  table INI or score view layout can be moved between a game and its tables, with Undo.
- **Manager** - A table's file can be swapped without losing the table. Replace Table File...,
  and Update... and Use Instead... from Discovery, put another file in its place and keep its
  plays, rating and collections, with the old file kept in Backups and Undo to bring it back.
  Delete from Disk... takes a table's file off the disk the same way, and Backups lists the
  table copies and restores them.
- **Core** - `.dif` patches apply directly, with no external tool. The base file stays on
  disk because the patched table cannot be rebuilt without it, and VPinFE records which base
  and which patch produced the result, so a patched table ends Matched to its release. The
  base is chosen from what was recorded for the folder first, and size only decides where
  nothing was recorded.
- **Manager, Core** - Players: a list of the people who play, each with a name, initials and
  a picture. Say who is up and the next game counts for them. A high score finds its player
  by the initials entered at the machine, a visitor can join as a guest, and each player has
  plays, time and best scores game by game.
- **Manager** - Back and Forward walk the Manager's places. A click in the left menu, a
  Settings page, a Logs view or a Discovery tab is a step; moving between rows or a panel's
  sections is not, so Back goes to the page before and not through every game you arrowed
  past. Discovery's tab is in the address, so a reload keeps it.
- **Manager** - A game's tab says each problem once. A ROM missing from several sets, a
  folder that is away, a table that cannot be played or a base file gone from several patches
  is one notice with the tables named under it, not one per table. A bulk action that fails
  on several items says so once, with the count and the reasons, rather than a message for
  each.
- **Manager** - Launchers run beside each other, each with its own program and settings, and
  can be chosen for a table. Launcher settings can be given to all tables, to one game or
  to one table, and the Manager shows which layer a value comes from.
- **Manager** - Visual Pinball settings follow the program you run. The settings offered, and
  what Enable defaults to, follow the program's build and the renderer plugins in its plugins
  folder (B2S, B2SLegacy, Nixie, ScoreView, PUP), and a setting the build does not have is
  hidden. After an upgrade a notice offers to move the settings the program no longer reads
  to where it reads them now, such as a renamed plugin setting or a DMD box that now lives in
  the backglass file, with an Undo and a Settings Left Behind row in Overview. On build 6267
  and later, Full DMD draws Nixie over B2S for a table whose art carries its own DMD, and
  VPinFE keeps each table's draw order in step with the launcher's. A program that has not
  written its settings file yet offers Create Settings File, which runs it once to make one,
  and says so plainly when that version cannot.
- **Manager, Core** - Devices: installs find each other on your network and show their
  version, what they can do and what their log says. A device can be updated, restarted,
  powered off, or told to close the table it is playing from another device. Several
  mobile devices running VPX Mobile can be added manually and sent tables. A cabinet can play
  a library kept on another device. Windows may ask to let VPinFE through the firewall the
  first time, and a network that filters multicast still takes an address manually.
  Settings > VPinFE > This Device shows the version running and the latest, and a Channel:
  Stable for everyday use, or Beta to be offered beta builds as well.
- **Manager, Core** - Backups: VPinFE keeps a copy before it changes something it cannot
  easily undo, and System > Backups lists, restores or clears them. A backup of your whole
  setup can be taken at any time and carried to another device.
- **Manager** - Settings save as you change them, with a tag on the ones that wait for a
  restart or a reopen. Open Links In, under Manager settings, sends every link to another
  site to one tab you keep using, or to a new tab each time. Metrics shows the processor,
  memory, graphics card and disks of this device, and Logs shows what VPinFE has been doing
  as a stream or as a digest that collapses repeats.
- **Frontend** - Screens are placed by dragging each one onto the part of your cabinet it
  belongs to, including a topper. Any key or controller button can be bound to an action,
  with chords and holds, and a key can take a picture of the screens during play. A Manager
  open on another device can listen for the keys and buttons of the cabinet's own controller
  when you bind one. Buttons are named by number, since a browser reports a number and
  makers letter them differently.
- **Frontend** - The wheel pages by the groups in whatever it is sorted by: the next letter
  under title order, the next year under year order, and a fixed number where every game has
  a value of its own. The Main Menu rates the game on the wheel, chooses who is up and
  records media, and the collection menu narrows the wheel and can save the narrowing as a
  smart collection. A library on a drive or share that is not connected yet is waited for,
  and the wheel fills when it is.
- **Frontend** - The Menu (M, or Button 9 on a controller) shows where the Manager is, as an
  address and a QR code to scan from a mobile device, beside Remote and Join. The codes carry
  the device's IP address where one is usable, which a mobile device can open without help, and the address list shows
  `<name>.local` in place of the plain name where this device answers to it. The codes sit
  further apart so a scan cannot catch its neighbour. A screen never waits for the name: it
  opens on the IP address and swaps the name in when this device answers to it.
- **Frontend** - On Sway, Show Numbers puts each number window on its own screen and opens
  again any window the browser drops while it starts.
- **Manager** - Displays asks "Is the Playfield Rotated Correctly?" in place of
  four arrow buttons. Show Test Card opens a card on the playfield screen with TOP at one edge,
  and Turn Left, Turn Around and Turn Right turn it until TOP is toward the backglass. Looks
  Right ends the check and reopens the open windows so the playfield turns at once, a key or a
  click on the card closes it and the panel asks whether the card faced the right way up, and a
  chip beside the question says how the screen is turned. It sets
  Rotation and Mounting for you. Visual Pinball's launcher page now shows the same card without
  changing anything from it, and a launcher turned differently lists Playfield under Different
  from VPinFE.
- **Frontend** - VPinFE can ask before it quits or powers off the device. It is off by
  default, which is how it has always behaved; turn on Confirm Before Exit. The
  question appears on whatever you used to ask, and every other screen is told what is about
  to happen.
- **Core** - VPinFE is localization ready. Every word it puts on screen comes from a
  translation catalog: the Manager, the cabinet's menus and overlays, the settings, the
  column headers and the messages the API sends back. English is the only language that
  ships and nothing looks any different, but adding another is a JSON file rather than a
  patch. Your library's own words, such as game names and manufacturers, are never
  translated. Titles that are not plain English sort and group as you would expect.
- **Core, Themes** - A theme declares the oldest VPinFE it runs on as `min_vpinfe` in its
  `manifest.json`. Installing one that needs a newer build than yours is refused before
  anything downloads, and the message names the version it wants. Themes install, update
  and take their options from Themes, from sources you can add and order.
- **Core** - What a theme or an overlay throws now reaches `vpinfe.log`, tagged with the
  window it came from. A browser log is not shown anywhere anyone reads on a cabinet, so a theme
  that broke looked like a frontend that had stopped answering the buttons.
- **Manager, Core** - Create Support Package, on About and in the Logs menu, makes a zip to
  attach when you ask for help, in three steps. What Goes In picks the current log, the previous
  log and your settings. Privacy shows what was replaced by kind, such as your user name and the
  device's name, and Hide Table Names and Also Hide add your own words. Inspect Everything lists
  every line to read, with Remove Line, Keep Line and Skip File. Share downloads the zip,
  copies a summary for Discord, opens Discord or a new GitHub issue, and copies the summary as
  Markdown. The zip is made on this device and sent nowhere, and `vpinfe --support-package`
  makes the same one from the command line. About also gains a Displays and Layout section.
- **Core** - An HTTP API at `/api/v1` with a written contract: games and tables, launch,
  media, assets, collections, uploads, VPS search and export, one error format and a
  permission scope on every endpoint. A live event stream at `/api/v1/events` replaces
  asking once a second, and the frontend listens to it for launch state. Documented in
  `docs/`.
- **Core** - Extensions add a feature without changing VPinFE, with a page of their own under
  Extensions in Settings. The Library Importer and VPinPlay, which adds community ratings
  and top lists, are extensions.

### Changes you may notice
These are deliberate. `docs/compatibility-3.0.md` has the full list with the reasoning.

- **Manager** - On a beta release, the last page of Setup and of What's New offers Tell Me
  About New Beta Releases, switched on. An install already on a beta that chose Stable is
  switched to Beta the first time the page is drawn, because a stored Stable cannot be told
  from the default. Turn it off there, or under Settings > VPinFE > This Device.

- **Frontend** - A folder with several `.vpx` files may launch a different one than before.
  VPinFE now picks the file the game's metadata describes; it used to take whatever the
  filesystem listed first, so the metadata you saw could describe a different table than
  the one that started.
- **Core** - Launches from the Remote page and the API count as plays now. Start count, last
  played, runtime and NVRAM score were only ever recorded for wheel launches.
- **Core** - Settings live in `vpinfe.json`, read once from `vpinfe.ini` on the first start.
  A setting's older spelling keeps working, and the comments you wrote in the ini are not
  carried over.
- **Core** - An install is described by the features it has switched on: Library, Frontend,
  Devices and Overview, all on by default.
- **Core** - Media resolves through a precedence chain: `(Wheel) <game file>.png` over
  `(Wheel) <folder>.png` over `wheel.png`, and a `.jpg` counts where the old code only ever
  looked for one exact `.png`. Media you placed manually that was silently ignored now appears.
  Imported media keeps its real extension.
- **Core** - Your own media is protected by hash rather than by claiming it. A file already
  on disk is compared against the MD5 VPinMediaDB publishes; a match is ours, and anything
  else is left alone. `--claim-user-media` does nothing now.
- **Core** - Games whose folders are not all lowercase start reporting the PUP packs,
  colorizations, VNI and altsound they always had. The scan compared folder names exactly,
  so `PUPVideos`, the casing PinUP Popper itself writes, went undetected.
- **Core** - A game's export is one table: the chosen `.vpx`, its companions, `pinmame/`,
  `music/`, colorization and sound folders and the author's readme files. Readme and `.nfo`
  files are recognized on import and travel with the game.
- **Core** - Themes, table media and logos are served from the same port as the Manager,
  8001 by default, and the separate theme server on port 8000 is gone. Another device now
  needs the `games:read` scope to read media and tables. A start whose port is taken, shared
  by two settings or refused by the device stops with a line naming the setting. Settings >
  Network lists Port, Reachable From and Window Port, with the address another device opens.
- **Core** - The command line takes `--game` to name a folder. `--table` still works for a
  script that uses it.
- **Core** - A collection whose filter picked a single digit or symbol as its letter matches
  nothing now, because digits and symbols share the `#` group. Open it and save its filter
  again against `#`.
- **Core** - The log is much quieter at INFO. Routine chatter moved to DEBUG.
- **Frontend** - Page-up moves the wheel backward now, and page-down forward. The keys are
  unchanged; what they mean is. If you prefer the old feel, swap the two bindings.

### Fixes
- **Core** - A fresh or headless install no longer ends on a dead end. The folder picker opens
  at the top of the disk where the account has no Home folder, and a missing browser is one
  warning in the log, not a traceback for every window.
- **Manager** - The source picker reads a Windows device's file paths, with backslashes, drive
  letters and network shares, so the current file is recognized and a table already in the
  game's folder is not offered again.
- **Frontend** - Tables no longer start paused on Windows. VPX pauses whenever its window
  loses focus, and Windows will not let one program hand the foreground to another it
  started, so a table came up paused until you alt-tabbed. The frontend windows now get out
  of the way and come back when you are done.
- **Frontend** - Holding a direction no longer freezes the wheel.
- **Frontend** - Chromium no longer leaves a profile directory behind for every window on
  every launch. On a cabinet where `/tmp` is a tmpfs, that was RAM.
- **Core** - A `.info` is written through a temporary file and renamed into place, so an
  interrupted write cannot leave a truncated one behind.
- **Core** - One unreadable `.info` no longer stops the whole library from loading. That
  game is left out and named in the log, and its file is not touched.
- **Core** - A collection from an earlier version that filters on type keeps its type. It came
  across from `collections.ini` selecting every game.
- **Core** - Remote launch failures are logged instead of failing silently, and a remote
  launch no longer raises on Linux.
- **Manager** - A browser that opened the Remote's QR code no longer lands on the Remote
  every time. Opening the Manager there asks for a code like any other device, and signing in
  makes that browser an Owner.

### For theme and API authors
- **Themes** - The selection surface keeps the names earlier versions published: `get_tables`,
  `launch_table`, `TableIndexUpdate`, `vpin.tableData` and the rest. A row on the wheel is a
  table, because a collection may hold two tables of one game and both are rows.
- **Themes** - Nothing changes unless you opt in. The payload is served in the shape your
  `manifest.json` declares as `contract`, and no declaration means the shape earlier versions served.
  `min_vpinfe` says the oldest VPinFE a theme runs on.
- **Themes** - Core owns the wheel's list and cursor, and the menus run on the same one, so a
  theme can open the collection picker and let core apply the choice and announce it.
  `vpin.enableCorePaging(false)` still hands paging back to you. Core takes the wheel's
  previous and next only from a theme that says it runs on 3.0; a theme that wants core
  navigation without declaring 3.0 sets `navigation.enabled` in its `theme.json`.
- **Themes** - Core's files are served at `/core/` now, against `/themes/` for what a theme
  provides. `/web/` still serves the same files, so an existing theme has nothing to change.
- **Themes** - The paging actions are `page_previous` and `page_next`, not `page_up` and
  `page_down`. A contract 1 theme still receives `joypageup` and `joypagedown`.
- **Themes** - Core serves a theme's files, table media and logos from the HTTP port, so
  `vpin.themeAssetsPort` reports that port, 8001 by default, and not 8000. A theme that reads
  `vpin.endpoints.files` has nothing to change; one that builds a URL on port 8000 itself
  does.
- **Themes** - `vpin.endpoints.player` is `vpin.endpoints.device`, and the window url
  carries `devicePort` rather than `playerPort`. It is the address of the device a game
  launches on, as against `endpoints.library`, which is where the catalog lives. A theme that
  read the old key gets no error, so it is worth checking.
- **Core** - Breaking: `/api/v1/players` is `/api/v1/devices`, its scopes are `devices:read`
  and `devices:write`, and a capability's residency says `device` where it said `player`. A
  device is something a game can be launched on; a player is a person.
- **Core** - Breaking: the endpoints that predate `/api/v1` are removed, not aliased.
  `/api/remote-launch`, `/api/asset-upload/*` and `/api/download-table-vpxz` are gone and
  their replacements live under `/api/v1`.
- **Core** - Breaking: the WS bridge method `update_frontend_dof_for_table` is now
  `notify_table_selected`. It drove DOF and the real DMD both, so the old name was wrong.
- **Core** - The `.info` is reshaped: `VPXFile` becomes `game_files` with one entry per
  `.vpx`, `Medias` becomes `assets`, the `VPinFE` section becomes `vpinfe` with snake_case
  keys, and `Info` gives up `Rom` and `Authors` to the game file that owns them.

### Notes
Back up your library before you upgrade and keep a copy of the earlier version's latest release to hand.

**On a Mac**, Apple has not checked the app yet, so macOS holds the first start for about a
minute and then stops it, which can look like it never finishes. Open System Settings >
Privacy & Security, press Open Anyway beside VPinFE, then sign in. After that it starts in
about a second. If another VPinFE is already running, close it first: macOS opens the app
again once you approve it, and a second copy quietly exits.
