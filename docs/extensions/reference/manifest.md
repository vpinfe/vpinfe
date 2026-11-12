# A whole extension and its manifest

This page shows a whole extension in one place, says where an extension lives, and lists every
key `extension.json` takes.

## A whole extension

This one has a setting, a secret and a list of scores it reads from a server.

```
example_scores/
    extension.json
    __init__.py
    i18n/
        en.json
```

`extension.json`:

```json
{
  "name": "example_scores",
  "display_name": "Example Scores",
  "version": "0.1.0",
  "author": "Example Author",
  "source": "https://github.com/example/example_scores",
  "description": "Lists the top scores from an example scores server.",
  "requires_platform": 1,
  "capabilities": ["ui:mount", "config:own", "net:outbound"]
}
```

`__init__.py`:

```python
import json
import urllib.error
import urllib.request

from common.extensions.contract import Unavailable


def register(ctx):
    ctx.ui.settings([
        {"key": "server", "type": "string", "default": "https://scores.example.com"},
        {"key": "api_key", "type": "secret"},
    ])

    def top():
        address = ctx.config.get("server")
        key = ctx.config.get("api_key")
        request = urllib.request.Request(
            f"{address}/top", headers={"Authorization": f"Bearer {key}"} if key else {})
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                return json.load(answer)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise Unavailable(ctx.why(exc, at=address)) from exc

    def send_now():
        return ctx.t("sent", count=3)

    ctx.ui.list(
        "top", top, title="Example Scores", area="community",
        columns=[{"field": "name", "under": ["manufacturer"]},
                 {"field": "manufacturer"},
                 {"field": "score", "kind": "number"},
                 {"field": "played", "kind": "date"}],
        views=[{"key": "best", "columns": ["name", "score"],
                "sort": [{"field": "score", "desc": True}]}],
        acts={"send_now": send_now, "site": "https://scores.example.com"},
    )
```

`i18n/en.json`:

```json
{
  "description": "Lists the top scores from an example scores server.",
  "settings.server.label": "Server",
  "settings.server.help": "Where scores are read from. Blank uses {default}.",
  "settings.api_key.label": "API key",
  "list.top.column.name.header": "Table",
  "list.top.column.manufacturer.header": "Maker",
  "list.top.column.score.header": "Score",
  "list.top.column.played.header": "Played",
  "list.top.view.best.name": "Best Scores",
  "list.top.act.send_now.label": "Send Now",
  "list.top.act.site.label": "Open the Site",
  "sent": {"one": "{count} score sent", "other": "{count} scores sent"}
}
```

Put the folder in `extensions/` under the config directory.  To see what core makes of it
first, run `python main.py --check-extension ./example_scores` (see
[Checking one](running.md#checking-one)).  The extension then has a page under Settings >
Extensions with its two settings, and a list called Example Scores under Community.

### Where an extension lives

The directory name is the extension's name, and the manifest has to agree.  The name appears
in a URL, a scope, a log namespace, a config file and the Python package core imports, so two
answers to what it is called would each be right somewhere.

`__init__.py` defines `register(ctx)`.  Core calls it once at startup and never again.  The
package is imported under its name, so the name has to be a legal module name.

Core looks in two places, in this order: `extensions/` under the config directory, then the
`extensions/` the build ships.  If an installed extension has the same name as a bundled one,
the installed one answers and the bundled one is left alone.

Registration is a moment.  After `register` returns, `ctx.open` is False and anything that
still needs declaring (a router, for one) is refused.

## The manifest

`extension.json`, snake_case like every other JSON VPinFE writes.  A key that is not in the
table is ignored.

| key | Required | Default | what it is |
|---|---|---|---|
| `name` | yes | none | 2 to 40 characters: `a-z`, `0-9` and `_`, starting with a letter. Matches the directory |
| `requires_platform` | yes | none | The platform ABI it was built against. This build offers `1`. Any other number is refused |
| `display_name` | no | empty | A product name, shown as written in every language. Left out, it is `name` in its own `i18n/`, and then the extension's `name` |
| `version` | yes | none | The extension's own version, starting with a number, such as `1.2.0`, at most 40 characters with no spaces. Shown and compared to the version a person trusted, never otherwise interpreted |
| `author` | yes | none | Who made it, one line of 1 to 60 characters, with no control or direction-changing characters. Shown under the name in the page header and in the trust prompt |
| `source` | yes | none | Where its code is kept: an `https` repository address of the form `host/owner/repo`, at most 300 characters, with no `@` in the host. A trailing `.git` or slash is tolerated. The page shows it as `owner/repo` for github.com, `host/owner/repo` elsewhere, as a link, so a person can read what they are about to trust |
| `home` | no | none | A web page about it, `http` or `https`, at most 300 characters. Shown as its own row, separate from Source, even when the address is the same |
| `picture` | no | none | A path inside its own folder to a `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` or `.bmp` file of at most 8 MB, written with `/` between folders. Drawn in the page header once the person has trusted the extension, never before, and never in the trust prompt |
| `description` | no | empty | One line, shown beside it. Left out, it is `description` in its own `i18n/` |
| `scopes` | no | none | Core scopes it asks to use, such as `games:read`. See [Scopes and the gate](scopes.md#scopes-and-the-gate) |
| `capabilities` | no | none | What it asks of the device. See [Scopes and the gate](scopes.md#scopes-and-the-gate) |
| `tiers` | no | none | `protected`, when something it serves asks for the Confirm. Something at a tier it does not list is refused. `play` is not listed here: see `play` below. See [Scopes and the gate](scopes.md#scopes-and-the-gate) |
| `play` | no | none | The surfaces a Remote can open, each by name: `router`, `action:<key>`, `list:<key>`, `page:<key>`, `group:<subject>` or `verb:<subject>:<key>`. Whoever installs it is shown the list before trusting it, and a surface put at Play that is not listed fails when it is declared, and a name listed that it never puts at Play fails the load. See [Scopes and the gate](scopes.md#scopes-and-the-gate) |
| `requires_features` | no | none | Install features it needs: `library`, `frontend`, `devices`, `overview` |
| `platforms` | no | every one | `linux`, `windows`, `macos`, or a Linux distribution's `ID` from `/etc/os-release`, such as `vpinos`.  It runs where any of them matches.  Empty means every one |
| `events` | no | none | Event names it publishes, without its namespace |

Everything in it is a declaration a person can be shown before installing.  That is why an
unknown capability or feature is refused rather than ignored: a name nobody has heard of is
not something anybody could have agreed to.  A platform is refused only when it is not the
shape of a name: lowercase letters, digits, `.`, `_` and `-`, starting with a letter or a digit,
which is what an os-release `ID` is.  Which distributions exist is not core's to list, so
`platforms: ["vpinos"]` is read as written and an extension for a distribution nobody has
installed simply never starts.  An extension that never starts for this reason has no page
in Settings and is not listed among its siblings, and the log says once, at start, which platforms
it asked for.  An extension that lacks only a feature stays listed, with the reason on its page.

`linux` matches any Linux, and a distribution's `ID` matches that one.  A machine that is
Ubuntu is both `linux` and `ubuntu`.  `ID` is read from `/etc/os-release`, and is lowercase
there already; a name with a capital letter is refused, so write `vpinos`, not `VPinOS`.

If the manifest names a platform this device is not, or a feature this install does not
have, the extension is not started.  It is still listed, with the reason, and on Linux the
reason names the distribution: "It does not run on Ubuntu".

### What the page shows of it

An extension that VPinFE did not ship is marked Unofficial beside its name, and Not Trusted until
the owner trusts it.  Its page header shows the author and version under the name, and the page
lists Home, Source and Folder Path (where the folder is, with a Copy Path button), then Reported
Capabilities: the scopes, capabilities and surfaces from the manifest, in words.  A line under it
says they are reported by the extension's author and VPinFE can't check them.  The trust prompt
lists Author, Version, Source and Folder Path, then Reported Capabilities.  Extensions that ship
with VPinFE have the author `VPinFE` and the source `https://github.com/vpinfe/vpinfe`, a logo as
the picture and no Folder Path.

`--check-extension` prints `author`, `home` (when set), `source` and `picture` right after the
`loads` line, and a `picture` that does not decode is a failure.  See
[Running](running.md#checking-one).

There is no `provides`, since core makes `ext:<name>:play`, `ext:<name>:library` and
`ext:<name>:protected` for every extension.  Whether an extension has settings is not declared either.  Core remembers what a
running extension registered, so its page can say the settings show while it runs.
