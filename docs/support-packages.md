# Reading a support package

A person who needs help makes a package from **About** or **Logs** in the Manager (Create Support Package), or with `vpinfe --support-package`, and attaches the zip to a Discord message or a GitHub issue. This is what is in it and how to read it. The code is `common/support/` (see [common.md](common.md)).

## The files

| File | What it is |
| --- | --- |
| `README.txt` | When and by which version it was made, the files in it, how many private details were replaced by kind, and a checksum of the other files |
| `summary.md` | The device and VPinFE as a series of sections, below |
| `vpinfe.log` | The current run's log |
| `vpinfe.log.1` | The run before it, where a crash is usually found. Absent when there was none or the person left it out |

Every byte went through one redactor and the text is English whatever language the Manager was set to, so a report reads the same from any install.

## The summary

Sections come in a fixed order, and each is one collector that never raises: a section it could not read says `Not reported` with the reason, which is a finding, not a gap.

| Section | Read it for |
| --- | --- |
| VPinFE | Version, build (Full, Slim, Source, Non-release), release target |
| This Device | Operating system, architecture, windowing system, graphics |
| Displays And Layout | The screens the desktop reports beside the screens VPinFE and Visual Pinball are set to, and **Findings** where the two disagree (a rotation that was not applied, a screen that is not there), and where each window said it opened. A rule with a missing fact says nothing, so no finding is not proof there is no problem |
| Frontend Browser | The browser the frontend runs in: its version, what it plays, what it can draw and decode (WebGL, WebGPU, H.264 and VP9 at 1080p, whether drawing falls back to the processor), and which options VPinFE adds. Only what the frontend's own window reported: a browser that has not run says `Not tested`, not `Not available` |
| Launchers And Programs | Each launcher's program and settings file (present, executable, size and date, the version a bundle or file records, the Rev and plugins its settings file lists), the programs VPinFE runs itself, the libraries it ships and the last launch command from the log. Visual Pinball is never run to ask its version |
| Selected Settings | An allow-list of the settings that decide how VPinFE behaves on the device. A setting is there because it is named, files that hold secrets are never opened and a launcher's free text is left out |
| Extensions And Themes | What is installed, at which version, and whether VPinFE runs it. Names and versions only |
| Where Things Live | The folders and files the install reads, as About lists them, with the person's own folders replaced |
| Health Now | Error and warning counts in this run's log and the most repeated messages, each with a count (`3x`) |

## Stand-ins

A replaced value is `[[kind-N]]`, and the same value has the same N in every file, so a name can be followed from the summary into the log. `[[secret]]` stands for a password, code or token and carries no number.

- **Home folder and user name** are replaced everywhere they appear, including inside paths.
- **Table names** stay unless the person chose **Hide Table Names**. A report that names a table is easier to reproduce, so a package that has them hidden may need that one name asked for.
- **Cleaned Out of Caution** lines are a string the redactor could not name but that looks random. They are replaced and listed on the Privacy step, not in the zip.
- `-- line removed by you --` is a line the person took out with Inspect Everything. Line numbers after it are unchanged, so a number quoted from the log still matches.

The person's own screen can show what a stand-in hides (hover a mark in Inspect Everything). That is the only place an original appears: it is not in the zip, and the API does not serve it.

## A package that is wrong

Cleaning is thorough and not perfect, and the README says so. If a private value is in a package, say so to the person and ask for it to be made again with **Also Hide** set, and treat the file as one to delete. If the value is one VPinFE should have known, the fix is in `common/support/identity.py` (where the install's own private values are read) with a test that the value does not reach a package.
