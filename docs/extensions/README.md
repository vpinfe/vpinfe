# Writing an extension

An extension adds a feature to VPinFE without being part of it. It is a folder with a
manifest, a `register` function and its words. You hand VPinFE data and functions, and VPinFE
draws every page and writes every route. It runs inside VPinFE, so installing one is trusting
it.

## What an extension can add

- **A setting**, drawn on the extension's page in Settings: [the smallest extension](cookbook.md#1-the-smallest-extension).
- **A dialog**, a verb somebody presses: [the smallest dialog](cookbook.md#3-the-smallest-dialog), and
  [one with steps and a check](cookbook.md#4-a-dialog-with-steps-and-a-check).
- **A long job**, followed in Jobs and stoppable: [a long job with progress](cookbook.md#5-a-long-job-with-progress).
- **A reaction to something VPinFE did**, such as a game ending: [listening to events](cookbook.md#6-listening-to-events).
- **A list**, shown in the rail: [a list](cookbook.md#7-a-list).

More arrive with the recipes for them. The [Reference](reference.md) points to a page for
everything an extension can do today.

## What you need

- **VPinFE**, running. An extension is loaded at startup, so restart it after adding one.
- **A folder to put it in.** Under the config folder, in `extensions/`:

  | Linux | macOS | Windows |
  |---|---|---|
  | `~/.config/vpinfe/extensions` | `~/Library/Application Support/vpinfe/extensions` | `%LOCALAPPDATA%\vpinfe\vpinfe\extensions` |

  With `--configdir DIR` it is `DIR/extensions`.
- **Python**, which is the one VPinFE runs on. An extension imports from it and nothing
  else of VPinFE: `common.extensions.contract` is the whole of what it may import.
- **The practice site**, for the recipes that talk to a service. It is a few lines of
  Python playing a score-sharing service called Example Scores:

  ```bash
  python docs/extensions/examples/practice_site.py
  ```

  It answers on `http://127.0.0.1:8790` and accepts the key `practice-key`.

## Try one

Copy [examples/recipes/smallest](examples/recipes/smallest) into the extensions folder and
restart VPinFE. Open Settings, then Extensions, then Smallest.

Before restarting, ask VPinFE what an extension declares, without running it:

```bash
./vpinfe --check-extension path/to/your/extension
```

It loads the extension as VPinFE would, says each thing it declared and each read it
answered, and names any word its `i18n/en.json` is missing. It sends nothing and changes
nothing, and it exits with 1 when something would not pass.

## The way through

- **[Learn](tutorial.md)** to see the pieces fit. It grows one extension, Example Scores, from an
  empty folder in ten lessons: settings, a list, a menu of actions, a dialog, a pane and a group
  on a game's panel. Each lesson says what you will have, shows it, and says what core did.
- **[How it works](how-it-works.md)** for the ideas the lessons keep returning to: what core draws,
  what trusting an extension means, scopes, Private games and what a restart is for.
- **[Good extensions](good-extensions.md)** for the habits that make an extension feel like
  part of VPinFE, ending in a list to go through before you give one to anybody.
- **The [Cookbook](cookbook.md)** once you know what you want. Each recipe is a small complete
  extension that does one thing, can be copied whole, and says where it appears and what
  triggers it.
- **The [Reference](reference.md)** once you know the call. It is an index to one page for each
  part of `ctx`, with every call, declaration and field type, for lookup.
