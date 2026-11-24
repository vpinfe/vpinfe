# Extensions: reference

An extension adds a feature to an install without being part of VPinFE.  It ships as a
folder, says what it needs in a manifest, and gets one object, `ctx`, to reach everything
through.  It never receives the application.

The word is **extension** throughout.  A *plugin* is a Visual Pinball standalone plugin, which
is a different thing with its own page.

Core writes and mounts every route.  You hand it data and Python functions - settings, rows, a
dialog, an account's functions - and it serves them under `/api/v1/ext/<name>`, shows them in
the Manager and gates them.  Add routes of your own with `ctx.add_router` only for an API that
is yours.

Use this page to look things up.  Each `ctx` call has a heading, each declaration has a
section, and each section gives the shape first and then an example.  New to this?  Start
with the [README](README.md), then [Learn](tutorial.md).  The [Cookbook](cookbook.md)
has a small, complete extension for each task.

## Find a page

| You want | Page |
|---|---|
| A whole extension on one page, where one lives, every key of `extension.json` | [A whole extension and its manifest](reference/manifest.md) |
| The name, log, words, config, events, files and jobs every extension has | [The context](reference/context.md) |
| Settings, lists, dialogs, links to a page of your own and an account, as `ctx.ui` takes them | [`ctx.ui`](reference/ui.md) |
| What a settings page and a list take: fields, columns, views, verbs | [Settings and lists](reference/settings-and-lists.md) |
| What a dialog takes: steps, checks, acts, a long job | [Actions and dialogs](reference/actions.md), with the Fields table every surface shares |
| A player's account with a service, its fields, its card | [Accounts](reference/accounts.md) and [`ctx.players`](reference/players.md) |
| Something to add to every entry, a game's link elsewhere, a name for a command | [Entries, catalogs and tokens](reference/entries.md) |
| The library, importing files into it, an online place to get one from, and a way to play a format of table | [The library and ways to play](reference/games.md) |
| A way to play, an outside link, an Online source, what a game tells you | [More to offer](reference/offers.md) |
| The words an extension shows, and their keys | [Words](reference/words.md) |
| What an extension may reach, and what it must ask for | [Scopes and the gate](reference/scopes.md) |
| What an operating system ships so `ctx.os` has something to ask | [The operating system's helper](reference/os-helper.md) |
| Config, the log, switching one off, what you see when one breaks, the command line | [Running one](reference/running.md) |
| The routes core serves for what an extension declared | [What core serves](reference/served.md) |

## Where the code is

- `common/extensions/contract.py` - the manifest and the shape of the context.  The only
  module of VPinFE's an extension imports.
- `common/extensions/context.py` - what `register(ctx)` is handed.
- `common/extensions/serving.py` - what core answers for what an extension declared.
- `common/extensions/fields.py` - the field types, as two lists.
- `common/extensions/host.py` - loading, the registry, and the kill switch.
- `common/extensions/store.py` - the switched-off list, and each extension's own file.
- `common/extensions/check.py` - `--check-extension`.
- `common/extensions/accounts.py` - where an account's values live, and the secret taken out
  of every answer.
- `common/extensions/cards.py` - making a card and reading one.
- `httpapi/extensions.py` - the gate, the mount, `GET /api/v1/extensions` and the switch.
- `httpapi/extensions_served.py` - the routes core serves for what an extension declared.
- `httpapi/player_accounts.py` - a player's accounts, Share and cards on the wire.

`tests/fixtures/extensions/sample/` is a worked example that uses the router API, and
`tests/fixtures/community/challenge/` one that uses `ctx.ui`.
