# VPinFE
<img width="1278" height="852" alt="VpinFE_V1-MF-withCredit" src="https://github.com/user-attachments/assets/914dff06-e3de-4096-8bc1-b4586d9802f6" />

**A frontend for Visual Pinball with the following features:**
- Works on Linux, Windows, Mac, and ARM (Windows must use Standalone dir structure)
- Multiscreen - table, backglass, score view and topper, plus a real DMD
- Fully remote cabinet management
- Keyboard & controller input support
- The Manager, a web app for your library, settings and media (auto download via [VPinMediaDB](https://github.com/superhac/vpinmediadb))
- Build custom table collections (VPS ID-based and filter-based)
- Automatic [vpx patching](https://github.com/jsm174/vpx-standalone-scripts) for Linux & Mac
- Fully customizable UI theming using HTML, JS and CSS
- JSON-based table metadata with VPX file parsing and feature detection
- DOF Support using [libdof-python](https://github.com/vpinfe/libdof-python)
- Real DMD Support using [libdmdutils-python](https://github.com/vpinfe/libdmdutil-python) ZeDMD, Pixelcade and PIN2DMD.
- Mobile transfer support for VPinball on Android and iOS (Web Send & VPXZ Download)
- VPinballX.ini config editor with backup/restore for multi-config enviroments
- Importing roms, pupacks, altcolor, and altsound.
- Drag and drop importing of tables, assets, and media - drop files, zip/rar/7z archives, or whole game folders
- Optional online ratings, scoring and play tracking via [VPin Play](https://www.vpinplay.com/). The [GitHub Project](https://github.com/superhac/vpinplay)
- Localization ready - every word on screen comes from a translation catalog, so another language is a JSON file rather than a code change (English is what ships today)

### Support/Feedback
Join us on VPC discord channel @ [vpinfe](https://discord.gg/SFBfA6Te2A)

Something not working? **About** in the Manager has **Create Support Package**, which gathers your version, system and logs into a zip with private details replaced, ready to attach to a Discord message or a GitHub issue.

## See it in action

<img width="1296" height="546" alt="themes" src="https://github.com/user-attachments/assets/57f3ee83-d45f-4bb0-be12-bbbcb69317da" />

Youtube Playlist
https://www.youtube.com/playlist?list=PLMRebAANedgX9YDbl0gte_ZgoYU9VQF8j

# Installing

## What you need

Visual Pinball **10.8.0**, or a recent **10.8.1** prerelease.  Builds called 10.8.1 are nightlies, so take a current one.

VPinFE starts the Visual Pinball you already have, and does not install it.  Get Visual Pinball to start a table on its own first, because a table that will not start from Visual Pinball will not start from VPinFE either.

## Download

Download the release for your platform from [Releases](https://github.com/vpinfe/vpinfe/releases).  There are two builds for each:

- A slim build (has slim in its name) is for people who already have Chrome installed locally on their machine.  (Recommended)
- A fat build (no slim in its name, and a larger download) bundles Chromium with VPinFE

### AUR (Arch Linux & Derivatives)
VPinFE is also in the AUR as [vpinfe](https://aur.archlinux.org/packages/vpinfe), maintained by @superrob.  The package installs the desktop entries for easy launching and updating.

## Running

### Linux (X64 & ARM)
Unzip the release and run:
```
cd vpinfe
./vpinfe
```

### Mac
Apple has not notarized the app, so the first start takes a few steps.  Until you allow it, macOS holds it for about a minute and then stops it, which can look like it never finishes.  Open System Settings > Privacy & Security, press **Open Anyway** beside VPinFE, then sign in.  After that it starts in about a second.  If another VPinFE is already running, close it first: macOS opens the app again once you approve it, and a second copy quietly exits.

@MajorFrenchy has put together an excellent [video](https://www.youtube.com/watch?v=YD4eZIqHypw) on setting this up for the Mac, and a written tutorial: [Major Frenchy's VPinFE on MacOS](https://www.majorfrenchy.com/blog/2026/03/05/vpinfe-macos/).  If you are a Mac user this is the best route.

### Windows
The Windows package has a `vpinfe.bat` file for launching it.  Unless you are in a terminal, always use the batch file or you will end up with window focus issues.  Double click it to launch, or from a terminal:

```
cd vpinfe
vpinfe.bat
```

### Without the frontend windows
Start VPinFE with `--headless` on a device that only looks after the library, or that you reach from another device.  It runs the Manager and its services and opens no frontend windows.  The Manager's power menu offers **Open the Frontend** while the windows are closed, and **Close the Frontend** and **Restart the Frontend** while they are open, so the windows can still be brought up when you want them.

# First run

The first time VPinFE starts it opens the Manager on Setup.  It asks how you play, how it should look, which screen is which (on a cabinet), your controls, which Visual Pinball to run, and where your games are - or imports a library from a folder or another frontend (PinUP Popper, PinballX or PinballY).  Everything it sets can be changed later under Settings.

Anytime you want to get back to the Manager, look in the terminal for the web address.  It is a line that looks like this:

```
VPinFE ready to go on http://localhost:8001, and http://192.168.1.228:8001
```

Put that address in a browser, on this device or any other on your network.  The frontend's Menu (**M**, or Button 9 on a controller) shows it too, with a QR code to scan from a mobile device.
Your game folders should follow the [VPinball table organization standard](https://github.com/vpinball/vpinball/blob/master/docs/FileLayout.md).  [Running VPinFE](docs/running.md) shows what a game folder holds and how media and matching work.

## Where VPinFE keeps what you set

By default VPinFE keeps what you set in four places, each in the folder the OS gives that kind of file:

| Kind | Holds | Linux | macOS | Windows |
|---|---|---|---|---|
| Config | `vpinfe.json`, `frontend_themes/`, your collections and backups | `~/.config/vpinfe` | `~/Library/Application Support/vpinfe` | `%LOCALAPPDATA%\vpinfe\vpinfe` |
| State | which device is this install, the devices it has seen, and the reports of recent imports | `~/.local/state/vpinfe` | `state` inside the config folder | `State` inside the config folder |
| Logs | `vpinfe.log` | `~/.local/state/vpinfe/log` | `~/Library/Logs/vpinfe` | `Logs` inside the config folder |
| Cache | what it can download again: the VPS database, the theme list, thumbnails | `~/.cache/vpinfe` | `~/Library/Caches/vpinfe` | `Cache` inside the config folder |

Copying or backing up the config folder carries your settings and nothing else, so a folder copied to another machine does not make that machine this one, and a cache is never copied along.  To keep everything somewhere else, such as a portable copy or separate config profiles, set the `VPINFE_CONFIG_DIR` environment variable or pass `--configdir DIR`.  Both point the config folder at `DIR`, with the cache in `DIR/cache`, the state in `DIR/state` and the logs in `DIR/logs`; the environment variable wins if you set both.

# More

- [Running VPinFE](docs/running.md) - games and tables, media, keyboard and gamepad, DOF and real DMDs, command line options, the ports it listens on, VPinPlay
- [Technical Details](docs/technical-details.md) - every setting in `vpinfe.json`, the `.info` file, patches and mobile transfer, and the shutdown rule for Linux
- [Making a Theme](docs/theme.md)
- [HTTP API](docs/http-api.md)

## Acknowledgements
VPinFE was created by **@superhac**, who owns the project.  **@MizterB** is a maintainer.

- A special thanks to **@jsm174** for making VPX available to all these other platforms.  He's also a great mentor.  I learned a tremendous amount working with him on the vpx project and he is the epitome of the VPinball community.
- **@MajorFrenchy** is another great example of what the VPinball community is all about. He jumped in right away and provided invaluable testing and feedback.  And we can't forget his great video on using [VPinFE on the MAC](https://www.youtube.com/watch?v=YD4eZIqHypw)!  He also made the VPinFE logo and splash video!  The favicon is derived from his logo.  Thank you, thank you!!!
- Huge thanks to **@Gonzonia** for all his work on the Mac app bundle. This simply wouldn't have happened without his knowledge and contributions.
- A big high five to @evilwraith for making the ARM build!
- A big thank you to all the hard work and dedication the [VPS Team](https://virtualpinballspreadsheet.github.io/): (**@Dux, @Fraesh and @Studlygoorite**) has put into creating this great table finding resource! And they made it "open" so others can leverage it as they want.
- A big thank you to [Pinball Primer](https://pinballprimer.github.io/) for creating and sharing such a great rules and tutorial resource for the pinball community.
