# Theme Publishing

This document explains how VPinFE discovers, installs, updates, and serves published themes so you can publish a new theme correctly.

This is the publishing side of the theme system. For authoring the actual HTML/CSS/JS theme files, see [theme.md](theme.md).

## Overview

Published themes are not stored inside the main VPinFE repository. The system is split into:

1. A theme list: a `themes.json` that names the themes on offer
2. One repository per theme
3. A local installed copy under the user's VPinFE config directory

When VPinFE starts, and when the Themes page refreshes, it:

1. Downloads each theme list the user has configured, starting with the VPinFE Theme List
2. Reads the `themes` object in each
3. Downloads each listed theme's `manifest.json`
4. Builds the available theme list shown in the Themes page
5. Installs the themes the VPinFE Theme List marks `default_install: true`

When a user installs or updates a theme, VPinFE downloads the theme repository as a ZIP, extracts it into the `frontend_themes` folder of the VPinFE config directory, and renames the extracted folder to the theme's key.

## The theme list

The VPinFE Theme List is `themes.json` in the `vpinfe/themes` repository. VPinFE reads
lists in [`common/online/themes.py`](../common/online/themes.py), and a user can add lists
of their own or remove this one, so a list is just a file anyone can host.

A list is a JSON object with a top-level `themes` object, keyed by the theme's key:

```json
{
  "themes": {
    "revolution": {
      "url": "https://github.com/vpinfe/theme-revolution",
      "name": "Revolution",
      "default_install": true
    },
    "aurora": {
      "url": "https://git.example.net/you/vpinfe-theme-aurora",
      "ref": "v2"
    }
  }
}
```

### An entry

| Field | Required | Purpose |
| --- | --- | --- |
| `url` | Yes | The theme's repository. GitHub, or a Forgejo/Gitea server. |
| `name` | No | What the Themes page shows, and nothing else: the key still decides the folder and the stored active theme. Without it the page shows the `name` in the theme's `manifest.json`. |
| `ref` | No | A branch or tag to read the theme from, instead of the repository's default branch. |
| `default_install` | No | If `true`, VPinFE installs the theme at startup and it cannot be deleted. Only the VPinFE Theme List's marks count; another list's are ignored. |

An entry names a theme and where it lives and nothing else. Its version, author, and
description come from the repository's own `manifest.json`, so publishing a new version is a
merge in your repository and the list is not involved again.

The older list shape, with `theme_base_url` and `theme_manifest_url` in place of `url`, is
still read. A `theme_manifest_url` is followed as written, unless the entry also has a `ref`,
which then decides where the manifest is read from.

### The key

The key is not just a label. It affects runtime behavior:

- It is the key VPinFE uses internally for install, update, and delete.
- It becomes the installed folder name under `frontend_themes/`.
- It is the value stored as the active theme.
- The Manager uses it when building local preview URLs like `/themes/<theme_key>/<picture>`.

Choose it carefully and keep it stable once published. It does not have to match the repository name or the `manifest.json` `name`. Keys with spaces, such as `Slider Video`, are supported, but simple stable keys are easier to maintain.

### How to get listed

Open a pull request against the `vpinfe/themes` repository that adds your entry to
`themes.json`: a key, the `url` of your repository, and a `name` if the one in your
`manifest.json` is not how you want the theme shown. `default_install` is the list maintainers' to set.

A theme does not have to be listed to be installable. See
[Single themes](#single-themes) below.

### Serving more than one contract

Theme contract 2 arrives with 3.0, and a theme that moves to it stops working on 2.x
builds. There are two ways to handle that, and both are yours to choose:

- **Keep contract 1 on your default branch and develop contract 2 on a branch.** 2.x
  installs keep working and keep receiving your contract 1 updates. List the branch as its
  own entry with a `ref`, in the 3.0 list:

  ```json
  "aurora": { "url": "https://git.example.net/you/vpinfe-theme-aurora", "ref": "v2" }
  ```

- **Move your default branch to contract 2.** Simpler to maintain, and 2.x installs that
  update from it break. Nothing in a list can prevent that.

The manifest decides whether a build runs a theme. A theme declaring `"min_vpinfe": "3.0"`
is refused by a 3.0 or newer build that is older than that, before anything is downloaded,
with a message naming the version it needs. A 2.x build does not read `min_vpinfe`.

## Per-Theme Repository

Each published theme lives in its own repository. VPinFE expects to install directly from the repository root.

The install code assumes:

- The repository is hosted on GitHub or on a Forgejo/Gitea server
- The theme files live at the repository root
- The repository serves `<url>/raw/<ref>/<path>` and `<url>/archive/<ref>.zip`

This behavior comes from [`common/online/theme_refs.py`](../common/online/theme_refs.py)
and [`common/online/theme_installer.py`](../common/online/theme_installer.py), which build URLs as:

```text
<url>/raw/<ref>/manifest.json
<url>/archive/<ref>.zip
```

Without a `ref` the ref is `HEAD`, which both hosts resolve to whatever the repository's
default branch actually is, so a repository defaulting to `main` works without saying
anything.

**Write refs bare**: `v2`, not `refs/heads/v2`. GitHub and Forgejo spell fully qualified
refs differently and each returns 404 for the other's form, so VPinFE strips `refs/heads/`
and `refs/tags/` before building a URL. A fully qualified ref still works; it is just
rewritten. The one thing to avoid is a branch and a tag with the same name in one
repository, because a bare ref cannot then say which you meant.

### Single themes

A theme does not have to be in a list to be installable. A user can add your repository as
a Single Theme, and VPinFE treats it as a theme in its own right. Stored in the config file
it looks like this:

```json
{
  "settings": {
    "themes": {
      "repositories": ["https://git.example.net/you/vpinfe-theme-aurora"]
    }
  }
}
```

**The theme is called whatever `manifest.json` says its `name` is**, not the repository's
name. A repository url names nothing, so the author's own choice is the one that counts,
and it is the name the theme installs under. Updates are found by comparing versions.

This works for any repository, including one already in a list, so it is also how you
install a fork, or hold a published theme at a particular version. Because the name comes
from the manifest, a fork of Revolution is called `Revolution` too, and the listed copy and
yours are the same theme: yours wins, and VPinFE logs which one it dropped. Where a list's
key and a manifest name differ only in case, such as `cab` and `Cab`, they are two names
and both install, with a warning.

#### Pinning a ref

Add `#<ref>` to hold a repository at one branch or tag:

```json
"repositories": ["https://git.example.net/you/vpinfe-theme-aurora#v1.4"]
```

A list entry pins the same way, with its `ref` field. A tag does not move, so a theme pinned
to one is never offered an update; move the pin to take one. A pinned branch is offered an
update whenever its manifest version goes up.

### Lists a user adds

The same file takes `registries`, a list of `themes.json` addresses. The VPinFE Theme List
is an ordinary entry in it, so it can be reordered, replaced with a mirror, or removed on an
install that should not reach the internet. Repositories are resolved before lists, so
naming a repository directly wins if a name appears in both.

Both are edited in the Manager under Settings > Themes, where they are called Theme Lists
and Single Themes. A theme runs inside VPinFE, so add sources you trust.

### Required repository contents

At minimum, the repository should contain:

```text
<repo root>/
├── manifest.json
├── index_playfield.html
├── style.css
├── theme.js
└── preview.png
```

[theme.md](theme.md) says which windows exist, which pages are optional, and what each page
may rely on. Optional files are allowed, such as:

- `fonts/`
- additional images, videos, or assets used by the theme
- `README.md`

## `manifest.json`

Each published theme repository must include a `manifest.json` at the repository root.

VPinFE validates the manifest in [`common/online/themes.py`](../common/online/themes.py).

### Required manifest fields

| Field | Purpose |
| --- | --- |
| `name` | Display name shown in the Manager, unless the list entry sets its own `name`. |
| `version` | Version string used for update checks. |
| `author` | Theme author name. |
| `description` | Description shown in the Manager. |
| `layouts` | The layouts the theme is made for, each with its preview filename or URL: `{"cab": "preview-cabinet.png", "desktop": "preview-desktop.png"}`. Keys are `cab` and `desktop`; name one or both. A 2.x theme that says `preview_image` and `type` (`desktop`, `cab` or `both`) instead is still read. |
| `windows` | The windows the theme draws. A 2.x theme that says `supported_screens`, a number, is still read. |

Also read:

| Field | Purpose |
| --- | --- |
| `min_vpinfe` | The oldest VPinFE the theme runs on. Declaring `"3.0"` is what gets a theme the contract 2 surface. |
| `change_log` | Optional text shown as "What's new" for uninstalled themes or when an update is available. |

### Version format requirements

Update checks use numeric dot-separated version parsing, so versions should be simple
numeric values like `1.0`, `1.4`, or `2.0.3`. Avoid `v1.0`, `1.0-beta`, and
`2026.03-release`.

## Install And Update Flow

1. VPinFE reads the entry for a theme.
2. It downloads the remote `manifest.json`.
3. It refuses a theme whose `min_vpinfe` is newer than the running build.
4. It compares the remote `manifest.version` to the locally installed version.
5. It downloads the repository archive at the entry's `ref`.
6. It extracts the ZIP into `frontend_themes/`.
7. It renames the extracted folder, such as `repo-name-main`, to the theme's key.

Important consequences:

- Publishing is branch-based, not release-based.
- The default branch, or the `ref` in the entry, is the published artifact.
- Updating `manifest.json` there is what makes a new version visible.
- A repository rename does not change the key used locally.

## Local Install Location

Installed themes are stored in the `frontend_themes/` folder of the user's VPinFE config
directory, which is `platformdirs.user_config_dir("vpinfe", "vpinfe")` unless the user
moved it. At runtime, VPinFE mounts that folder as `/themes/` in the local HTTP server from
[`main.py`](../main.py), so an installed theme is served from URLs like:

- `/themes/<theme_key>/index_playfield.html`
- `/themes/<theme_key>/preview.png`

## How Preview Images Are Resolved

The Manager shows the picture each layout in the manifest's `layouts` names, side by side
when there are two. It resolves each one in the same ways:

- If the theme is installed, it serves the preview locally from `/themes/<theme_key>/<picture>`
- If the theme is not installed, it reads the preview from the same ref of the repository
- If the picture is already an absolute `http...` URL, it uses that directly

For the simplest publishing flow, keep each picture as a filename in the repository root, for example `"layouts": {"cab": "preview-cabinet.png", "desktop": "preview-desktop.png"}`. A theme made for one layout names one.

The Manager downloads a picture it has not kept once per release and shows it at the size it is drawn, so a new release is the way to have a changed picture used everywhere at once. Keep it under about 300 KB: the Manager downloads it in full, once per release.

## Publishing Checklist

1. Create a repository for the theme.
2. Put the theme files in the repository root, including `manifest.json`.
3. Make sure `manifest.json` has all required fields, and `min_vpinfe` if it needs 3.0.
4. Use a numeric version string such as `1.0`.
5. Push to your default branch, or to the branch you will list with a `ref`.
6. Open a pull request against `vpinfe/themes` adding your entry to `themes.json`.
7. In VPinFE, refresh the Themes page. Before the entry is merged, add the repository as a Single Theme to see it.

## Updating An Existing Theme

1. Update the repository contents on the branch your entry reads
2. Increment `manifest.json` `version`
3. Optionally update `change_log`
4. Push

You do not need to change the list unless the repository URL, the key, or the `ref` changes.

The update badge is driven by comparing the remote manifest version to the installed local manifest version.

## Common Publishing Pitfalls

### Putting files in a subdirectory

The installer extracts the repository and expects `manifest.json` and the theme HTML files at the repository root. If everything is inside a nested folder, the active theme URLs will not line up with the installed layout.

### Using non-numeric versions

The current version comparison only supports dot-separated integers.

### Changing the key after publishing

The key is the installed folder name and the stored active theme, so changing it is effectively a theme identity change.

### Forgetting `manifest.json`

If the entry's repository has no `manifest.json`, or it is missing required fields, the theme is skipped when VPinFE loads manifests.

## Recommended Conventions

- Keep the key, repository name, and manifest `name` closely related
- Store all theme runtime files in the repository root
- Use `preview.png` at the root unless you need something else
- Use `change_log` for update notes
- Keep versions numeric and increment them for every published update
- Test install, update, activate, and delete from the Themes page before announcing the theme

## Related Files In This Repo

- List reading and install logic: [common/online/themes.py](../common/online/themes.py)
- Ref and URL spelling: [common/online/theme_refs.py](../common/online/theme_refs.py)
- Startup auto-install: [main.py](../main.py)
- Active theme page URL generation: [frontend/api.py](../frontend/api.py)
- Theme authoring guide: [docs/theme.md](theme.md)
