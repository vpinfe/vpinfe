# Cookbook

Small, complete extensions, one task each. Every recipe is the smallest extension that does
one thing, so you can copy its folder whole into your extensions folder and change it. The
folders are under [examples/recipes](examples/recipes), and each block of code below is the
file of the same name there.

New to this? Read [Start here](README.md) first. Want to see how the pieces fit into one
extension? [Learn](tutorial.md) grows one from an empty folder. Once you know the call
you want, the [Reference](reference.md) lists every one.

Recipes that talk to a service use the practice site, a few lines of standard-library Python
that plays a score-sharing service called Example Scores. Start it in a terminal and leave it
running:

```bash
python docs/extensions/examples/practice_site.py
```

It answers on `http://127.0.0.1:8790` and accepts the key `practice-key`. Stopping it forgets
what was posted.

Each recipe has the same parts: when to use it, the code, where it appears and what triggers
it, what you will see, a few variations and the mistake people make with it.

- [1. The smallest extension](#1-the-smallest-extension)
- [2. A key kept secret](#2-a-key-kept-secret)
- [3. The smallest dialog](#3-the-smallest-dialog)
- [4. A dialog with steps and a check](#4-a-dialog-with-steps-and-a-check)
- [5. A long job with progress](#5-a-long-job-with-progress)
- [6. Listening to events](#6-listening-to-events)
- [7. A list](#7-a-list)
- [8. A list you can add to](#8-a-list-you-can-add-to)
- [9. Acting on rows](#9-acting-on-rows)
- [10. States, chips, links and pictures in a column](#10-states-chips-links-and-pictures-in-a-column)
- [11. A game in a list](#11-a-game-in-a-list)
- [12. A pane beside a list](#12-a-pane-beside-a-list)
- [13. An account per player](#13-an-account-per-player)
- [14. A group on a game's panel](#14-a-group-on-a-games-panel)
- [15. An entry in a game's menu](#15-an-entry-in-a-games-menu)
- [16. A page of one subject](#16-a-page-of-one-subject)
- [17. A page of your own](#17-a-page-of-your-own)
- [18. Asking for attention on Overview](#18-asking-for-attention-on-overview)
- [19. An extension for one Linux distribution](#19-an-extension-for-one-linux-distribution)
- [20. Asking the operating system to change something](#20-asking-the-operating-system-to-change-something)
- [21. Putting games in a collection](#21-putting-games-in-a-collection)
- [22. Listing what is plugged in](#22-listing-what-is-plugged-in)
- [23. A page a Remote can open](#23-a-page-a-remote-can-open)
- [24. Importing a folder](#24-importing-a-folder)

## Basics

### 1. The smallest extension

**Use it when:** you want to see an extension load, or you need a place to start from.

The folder holds the manifest, the `register` function core calls once at startup, and the
words it shows.

`recipes/smallest/extension.json`

```json
{
  "name": "smallest",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/smallest",
  "requires_platform": 1,
  "capabilities": ["config:own", "ui:mount"]
}
```

`recipes/smallest/__init__.py`

```python
def register(ctx):
    ctx.ui.settings([{"key": "greeting", "type": "string", "default": "Hello"}])
    ctx.logger.info("%s", ctx.config.get("greeting"))
```

`recipes/smallest/i18n/en.json`

```json
{
  "name": "Smallest",
  "description": "The least an extension can be: one setting.",
  "settings.greeting.label": "Greeting"
}
```

**Where it appears:** on the extension's own page, Settings, then Extensions, then Smallest, and
in the log. **What triggers it:** VPinFE starting. `register` runs once, declares the setting
and writes the greeting to the log. After that, nothing runs until somebody opens the page.

**What you'll see:** Settings, then Extensions, then Smallest: a Greeting field with Hello
shown faintly in it. Until somebody types something, `ctx.config.get("greeting")` answers
Hello, the default. After they do, it answers what they typed.

**Variations:**

- Add `"type": "switch"` to make the setting a toggle. It is stored and read as the text
  `"true"` or `"false"`.
- Use `"type": "secret"` for a key. A secret has no default, and core never sends it back to
  the page.
- Give it a `check` function, `ctx.ui.settings(fields, check=function)`. It is handed every
  setting as it would be after the change and raises to refuse it. The refusal shows under
  the field.

**Watch out:** the folder name, the `name` in the manifest and the words' prefix are one
name. It is lowercase letters, digits and underscores, starting with a letter. The words file
has no `ext.smallest.` in front of its keys, core adds that.

### 2. A key kept secret

**Use it when:** the extension needs a password, a token or a key, and it must never be shown again once it is entered.

A `secret` is a field like any other, in a setting and in a dialog, with two differences. The box is masked and drawn empty, with *Set* or *Not set* in it, because core never sends the value back to the page. And leaving it empty keeps the one held, so the person does not have to type it again to change something else. Here one key is entered either way: in Settings, or from a dialog that asks for it.

`recipes/secret_key/extension.json`

```json
{
  "name": "secret_key",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/secret_key",
  "requires_platform": 1,
  "capabilities": ["config:own", "ui:mount"]
}
```

`recipes/secret_key/__init__.py`

```python
class Connect:
    def __init__(self, ctx):
        self.ctx = ctx

    def ask(self, step):
        return {"fields": [{"key": "key", "label": self.ctx.t("dialog.key"),
                            "type": "secret",
                            "set": bool(self.ctx.config.get("key"))}]}

    def run(self, values):
        typed = values.get("key")
        if typed:
            self.ctx.config.set("key", typed)
            return self.ctx.t("dialog.saved")
        return self.ctx.t("dialog.kept")


def register(ctx):
    ctx.ui.settings([{"key": "key", "type": "secret"}])
    ctx.ui.action("connect", Connect(ctx))
```

`recipes/secret_key/i18n/en.json`

```json
{
  "name": "Secret Key",
  "description": "Keeps a key out of sight, set from a setting or from a dialog.",
  "settings.key.label": "Key",
  "action.connect.label": "Enter the Key",
  "dialog.key": "Key",
  "dialog.saved": "Saved the key",
  "dialog.kept": "Kept the key as it was"
}
```

**Where it appears:** on the extension's page in Settings, as a field and as a button.
**What triggers it:** the person typing in the field, or pressing the Enter the Key button. Nothing
runs in between.

**What you'll see:** Settings, then Extensions, then Secret Key: a masked Key field saying Not set. Type a key and leave the field, and it says Set, with Clear beside it. Clear asks first, then empties it. The Enter the Key button opens a dialog with the same field, and pressing the Enter the Key button with it empty leaves the key as it was.

**Variations:**

- Read it anywhere in the extension with `ctx.config.get("key")`. It is the one place the value is available, and it is empty where none is held.
- Put a secret in a dialog's first step and use it in `check` to see whether the service accepts it, as the dialog in recipe 4 does.
- Give a secret a `check` on the settings, `ctx.ui.settings(fields, check=function)`, to refuse a key the service does not know. The refusal shows under the field.

**Watch out:** a secret has no default, and `values.get("key")` in a dialog is `None` where the person left it empty. Treat that as keep, and never write an empty value over a key.

### 3. The smallest dialog

**Use it when:** the person has to be asked something before anything happens.

An action is an object with an `ask` method. It answers the fields to draw, and `run` does the
work once the person presses the button. With no `check` method there is no step after this one,
so the button is the action's own name.

`recipes/hello_dialog/extension.json`

```json
{
  "name": "hello_dialog",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/hello_dialog",
  "requires_platform": 1,
  "capabilities": ["ui:mount"]
}
```

`recipes/hello_dialog/__init__.py`

```python
class Hello:
    def __init__(self, ctx):
        self.ctx = ctx

    def ask(self, step):
        return {"fields": [{"key": "who", "label": self.ctx.t("dialog.who"),
                            "type": "string"}]}

    def run(self, values):
        return self.ctx.t("dialog.done", who=values.get("who") or "world")


def register(ctx):
    ctx.ui.action("hello", Hello(ctx))
```

`recipes/hello_dialog/i18n/en.json`

```json
{
  "name": "Hello Dialog",
  "description": "The smallest dialog: one question and a line to say when it is done.",
  "action.hello.label": "Say Hello",
  "dialog.who": "Who is it for?",
  "dialog.done": "Hello, {who}"
}
```

**Where it appears:** as a button on the extension's page in Settings. **What triggers it:**
the person pressing Say Hello. Nothing in VPinFE changes, so the only effect is the line
`run` returns.

**What you'll see:** Settings, then Extensions, then Hello Dialog, with a Say Hello button. It
opens a dialog with one question, and the line `run` returns is shown when it is done.

**Variations:**

- Make `who` a `select` with `"choices": [["a", "Alice"], ["b", "Bob"]]` for a fixed list.
- Add `"help": "text"` to the step for a line under the title.
- Return `{"message": ..., "summary": [...]}` from `run` instead of text to show facts under
  the message.

**Watch out:** the label of every field is text you pass in, so it comes from `ctx.t` like
every other word. A field with no label shows its key.

### 4. A dialog with steps and a check

**Use it when:** what to ask next depends on the last answer, or an answer has to be checked
before going on.

`ask(step)` answers the question for a step. `check(values, step)` is called on Next with
everything answered so far, and answers the step to go to. Answering the step it was given,
with `errors`, a line for each field it refuses, keeps the person where they are and says
why, under the field. A step with a `summary` and a
`confirm` is the last one: its button runs the action.

`recipes/post_score/extension.json`

```json
{
  "name": "post_score",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/post_score",
  "requires_platform": 1,
  "capabilities": ["config:own", "net:outbound", "ui:mount"]
}
```

`recipes/post_score/__init__.py`

```python
import json
import urllib.error
import urllib.request

from common.extensions.contract import Unavailable


class PostScore:
    def __init__(self, ctx):
        self.ctx = ctx

    def _send(self, key, path, body=None):
        request = urllib.request.Request(
            self.ctx.config.get("site") + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-API-Key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10):
                return True
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return False
            raise Unavailable(self.ctx.why(exc, at=request.full_url)) from exc
        except OSError as exc:
            raise Unavailable(self.ctx.why(exc, at=request.full_url)) from exc

    def ask(self, step):
        if step == "score":
            return {"step": "score", "title": self.ctx.t("dialog.score.title"),
                    "fields": [
                        {"key": "player", "label": self.ctx.t("dialog.player"),
                         "type": "string"},
                        {"key": "table", "label": self.ctx.t("dialog.table"),
                         "type": "string"},
                        {"key": "score", "label": self.ctx.t("dialog.points"),
                         "type": "number"}]}
        return {"step": "key", "title": self.ctx.t("dialog.key.title"),
                "help": self.ctx.t("dialog.key.help"),
                "fields": [{"key": "key", "label": self.ctx.t("dialog.key"),
                            "type": "secret"}]}

    def check(self, values, step):
        if step == "key":
            if not self._send(values.get("key", ""), "/check"):
                return {**self.ask("key"),
                        "errors": {"key": self.ctx.t("dialog.key.refused")}}
            return self.ask("score")
        return {"step": "sure", "title": self.ctx.t("dialog.sure.title"),
                "confirm": self.ctx.t("dialog.sure.confirm"),
                "summary": [[self.ctx.t("dialog.player"), values["player"]],
                            [self.ctx.t("dialog.table"), values["table"]],
                            [self.ctx.t("dialog.points"), f"{int(values['score']):,}"]]}

    def run(self, values):
        self._send(values["key"], "/scores", {
            "player": values["player"], "table": values["table"],
            "score": int(values["score"])})
        return self.ctx.t("dialog.posted", table=values["table"])


def register(ctx):
    ctx.ui.settings([{"key": "site", "type": "string",
                      "default": "http://127.0.0.1:8790"}])
    ctx.ui.action("post", PostScore(ctx))
```

`recipes/post_score/i18n/en.json`

```json
{
  "name": "Post Score",
  "description": "Checks a key against Example Scores, then posts a score to it.",
  "settings.site.label": "Site",
  "action.post.label": "Post a Score",
  "dialog.key.title": "Sign In",
  "dialog.key.help": "The practice site's key is practice-key",
  "dialog.key": "Key",
  "dialog.key.refused": "The site did not accept that key",
  "dialog.score.title": "The Score",
  "dialog.player": "Player",
  "dialog.table": "Table",
  "dialog.points": "Score",
  "dialog.sure.title": "Post this score?",
  "dialog.sure.confirm": "Post",
  "dialog.posted": "Posted a score for {table}"
}
```

**Where it appears:** as a button on the extension's page in Settings. **What triggers it:**
the person pressing Post a Score. Each press of Next calls `check`. Only the Post button on the
summary calls `run`, and that is the one step that changes anything, by posting to the site.

**What you'll see:** Settings, then Extensions, then Post Score, with a Post a Score button.
It asks for a key and refuses a wrong one under the field. A right one leads to the score, and
then to a summary with a Post button. The practice site has the score afterward.

**Variations:**

- Return `"ready": False` from a step to keep Next disabled until `check` is happy.
- Add `"acts": [{"key": "test", "label": "Test"}]` to a step and handle `act(key, values)` to
  give it a button that does something without moving on.
- Answer a step with `"step": "other"` from `run` when the path turned out longer than the
  summary said.

**Watch out:** `check` is called again each time the person goes Back and forward, so keep it
free of side effects. Anything that changes something belongs in `run`.

### 5. A long job with progress

**Use it when:** the work takes longer than a person will wait with a dialog open.

`ctx.jobs.submit` runs a function on its own thread and returns the job. Handing back its `id`
closes the dialog, and the job is followed in Jobs. `job.progress(done, total, message)` moves
the bar. A job started `stoppable` offers a Stop, which sets `job.halt`: look at it between
units of work and end early.

`recipes/slow_sync/extension.json`

```json
{
  "name": "slow_sync",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/slow_sync",
  "requires_platform": 1,
  "capabilities": ["ui:mount"]
}
```

`recipes/slow_sync/__init__.py`

```python
import time

STEPS = 20


class Sync:
    def __init__(self, ctx):
        self.ctx = ctx

    def ask(self, step):
        return {"fields": [], "summary": [[self.ctx.t("dialog.steps"), str(STEPS)]],
                "confirm": self.ctx.t("dialog.start")}

    def run(self, values):
        def work(job):
            for done in range(1, STEPS + 1):
                if job.halt.is_set():
                    job.stopped = True
                    return {"done": done - 1}
                time.sleep(0.5)
                job.progress(done, STEPS, self.ctx.t("job.step", done=done, total=STEPS))
            return {"done": STEPS}

        job = self.ctx.jobs.submit("sync", work, stoppable=True)
        return {"job_id": job.id}


def register(ctx):
    ctx.ui.action("sync", Sync(ctx))
```

`recipes/slow_sync/i18n/en.json`

```json
{
  "name": "Slow Sync",
  "description": "A job that takes ten seconds, reports how far it has got and can be stopped.",
  "action.sync.label": "Start the Sync",
  "dialog.steps": "Steps",
  "dialog.start": "Start",
  "job.step": "Step {done} of {total}"
}
```

**Where it appears:** as a button on the extension's page in Settings, and then in Jobs.
**What triggers it:** the person pressing Start on the summary. The work then runs on its own
thread until it finishes or the person presses Stop.

**What you'll see:** a Start the Sync button on the extension's page. It opens a summary with
a Start button, and the job appears in Jobs with its step count and a Stop button.

**Variations:**

- A dialog that finishes in the call itself can report with the `progress` it is handed:
  `def run(self, values, progress)`, then `progress.stage("Working", total=10)` and
  `progress.advance()`.
- Start the job with `keep_result=True` to keep what the function returned, so Jobs can open
  it after a restart.
- Set `job.problems = n` for the parts that failed. The job still finishes
  done.

**Watch out:** a job that never looks at `job.halt` cannot be stopped, whatever Stop says.
Check it once per unit, not once at the top.

### 6. Listening to events

**Use it when:** something should happen when VPinFE does something, such as a game ending.

`ctx.events.subscribe(name, handler)` calls the handler with the event's fields as keyword
arguments. Take what you need and `**_rest` for the others, so a field added later does not
break you.

`recipes/play_log/extension.json`

```json
{
  "name": "play_log",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/play_log",
  "requires_platform": 1,
  "scopes": ["games:read"],
  "capabilities": ["config:own"]
}
```

`recipes/play_log/__init__.py`

```python
def register(ctx):
    def played(game_id="", seconds=0, private=False, **_rest):
        if private:
            return
        ctx.logger.info("%s", ctx.t("log.played", game=game_id, count=round(seconds / 60)))

    ctx.events.subscribe("table.play_recorded", played)
```

`recipes/play_log/i18n/en.json`

```json
{
  "name": "Play Log",
  "description": "Writes a line to the log each time a game ends.",
  "log.played": {"one": "{game} was played for a minute", "other": "{game} was played for {count} minutes"}
}
```

**Where it appears:** in the extension's log and nowhere else. It adds no page and no button.
**What triggers it:** a game ending on the VPinFE device, once VPinFE has written the play.

**What you'll see:** a line in the extension's log each time a game ends, such as
`medieval-madness was played for 14 minutes`. The log is under Logs in the Manager.

**Variations:**

- `game.selected` fires when the wheel stops on a game and nothing may wait on it, so keep
  its handler quick.
- `table.launched` and `table.exited` mark the start and end of a play.
- Publish your own with `ctx.events.publish("name", ...)` after naming it under `events` in
  the manifest. Others hear it as `<your name>.name`.

**Watch out:** a handler that raises is logged and the extension is marked as having failed,
but VPinFE carries on. A handler is not where to wait for a service: start a job instead.

## Lists

### 7. A list

**Use it when:** you have rows to show, such as the scores a service keeps.

`ctx.ui.list` hands core the rows and the columns, and core draws the page, the sorting and
the search. The list is shown in the rail under the area you name: `library`, `frontend`,
`community` or `system`.

`recipes/top_scores/extension.json`

```json
{
  "name": "top_scores",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/top_scores",
  "requires_platform": 1,
  "capabilities": ["config:own", "net:outbound", "ui:mount"]
}
```

`recipes/top_scores/__init__.py`

```python
import json
import urllib.error
import urllib.request

from common.extensions.contract import Unavailable


def register(ctx):
    ctx.ui.settings([
        {"key": "site", "type": "string", "default": "http://127.0.0.1:8790"},
        {"key": "key", "type": "secret"},
    ])

    def rows():
        request = urllib.request.Request(ctx.config.get("site") + "/top",
                                         headers={"X-API-Key": ctx.config.get("key")})
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                return json.loads(answer.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise Unavailable(ctx.t("error.refused")) from exc
            raise Unavailable(ctx.why(exc, at=request.full_url)) from exc
        except OSError as exc:
            raise Unavailable(ctx.why(exc, at=request.full_url)) from exc

    ctx.ui.list("top", rows, area="community", columns=[
        {"field": "player"},
        {"field": "table"},
        {"field": "score", "kind": "number"},
    ])
```

`recipes/top_scores/i18n/en.json`

```json
{
  "name": "Top Scores",
  "description": "Lists the best scores on Example Scores.",
  "settings.site.label": "Site",
  "settings.key.label": "Key",
  "error.refused": "The site did not accept the key",
  "list.top.title": "Top Scores",
  "list.top.column.player.header": "Player",
  "list.top.column.table.header": "Table",
  "list.top.column.score.header": "Score"
}
```

**Where it appears:** as its own page in the rail, under Community. **What triggers it:** the
person opening the page or pressing refresh. Core calls `rows` each time.

**What you'll see:** Top Scores under Community in the rail. Until the key is set under
Settings, then Extensions, the page says the site did not accept it. With `practice-key`
there, it lists the three sample scores, best first.

**Variations:**

- Add `status=function` to say a line above the list, such as when it was last read.
- Add `acts={"refresh": function, "docs": "https://..."}` for the title menu: a function runs
  when pressed, and an address is opened.
- Add `views=[{"key": "best", "name": ..., "columns": [...], "sort": [...]}]` for named
  orderings the person can pick.

**Watch out:** a list's title may not be the name of one of VPinFE's own pages, and two lists
in one area may not share a title. The extension is refused at load, and the reason is in its
page.

### 8. A list you can add to

**Use it when:** the person should be able to put something new into a list, such as a score
on a site, and see it appear.

A list's `add` names a dialog you declared. Core puts a button for it above the list, opens
the dialog when it is pressed, and reads the list again once the dialog has run. The dialog is
the one you already know from recipe 3, so the list only points at it.

`recipes/add_score/extension.json`

```json
{
  "name": "add_score",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/add_score",
  "requires_platform": 1,
  "capabilities": ["config:own", "net:outbound", "ui:mount"]
}
```

`recipes/add_score/__init__.py`

```python
import json
import urllib.error
import urllib.request

from common.extensions.contract import Unavailable


def register(ctx):
    ctx.ui.settings([
        {"key": "site", "type": "string", "default": "http://127.0.0.1:8790"},
        {"key": "key", "type": "secret"},
    ])

    def call(path, body=None):
        request = urllib.request.Request(
            ctx.config.get("site") + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-API-Key": ctx.config.get("key"), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                return json.loads(answer.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise Unavailable(ctx.t("error.refused")) from exc
            raise Unavailable(ctx.why(exc, at=request.full_url)) from exc
        except OSError as exc:
            raise Unavailable(ctx.why(exc, at=request.full_url)) from exc

    class Post:
        def ask(self, step):
            return {"step": "score", "title": ctx.t("dialog.post.title"), "fields": [
                {"key": "player", "label": ctx.t("dialog.player"), "type": "string"},
                {"key": "table", "label": ctx.t("dialog.table"), "type": "string"},
                {"key": "score", "label": ctx.t("dialog.points"), "type": "number"}]}

        def run(self, values):
            call("/scores", {"player": values["player"], "table": values["table"],
                             "score": int(values["score"])})
            return ctx.t("dialog.posted", table=values["table"])

    ctx.ui.action("post", Post())
    ctx.ui.list("scores", lambda: call("/top"), area="community", columns=[
        {"field": "player"},
        {"field": "table"},
        {"field": "score", "kind": "number"},
    ], add="post")
```

`recipes/add_score/i18n/en.json`

```json
{
  "name": "Add Score",
  "description": "Lists the scores on Example Scores and adds one from a button above the list.",
  "settings.site.label": "Site",
  "settings.key.label": "Key",
  "error.refused": "The site did not accept the key",
  "action.post.label": "Add a Score",
  "dialog.post.title": "The Score",
  "dialog.player": "Player",
  "dialog.table": "Table",
  "dialog.points": "Score",
  "dialog.posted": "Posted a score for {table}",
  "list.scores.title": "Add Score",
  "list.scores.column.player.header": "Player",
  "list.scores.column.table.header": "Table",
  "list.scores.column.score.header": "Score"
}
```

**Where it appears:** on the list's page under Community in the rail, in the bar above the
list. **What triggers it:** the person pressing the button. The dialog's `run` is called with
what they typed, and core reads the list again afterwards.

**What you'll see:** Add Score under Community in the rail, with an **Add a Score** button
beside the search box. While the list is empty the button is there in full words, so a new
list never looks broken. Press it, type a player, a table and a score, and the dialog closes
with "Posted a score for Attack from Mars". The new score is in the list.

**Variations:**

- Name more than one dialog, `add=["post", "import"]`, and the button becomes a **+** with a
  menu of your dialogs' labels.
- A verb can open a dialog too, over the rows the person picked: give it `"action": "post"`
  in place of `run`. Your dialog's `ask(step, rows)` is handed the picked ids on its first
  call and gets them back as `values["rows"]` from then on.
- Add `"each": True` to that verb and the dialog opens once for each picked row, with **Skip**
  and **Stop** where Cancel would be.
- An act in the list's title menu can do the same: `{"key": "sign_in", "action": "connect"}`.

**Watch out:** the dialog named by `add`, a verb or an act has to be declared with
`ctx.ui.action`, in the same extension. An unknown one is refused at load, with its name in the
reason.

### 9. Acting on rows

**Use it when:** the person should be able to do something to the rows they pick, such as
flag a score, open its page or remove it.

`verbs` on a list are the things that can be done to a row. Core draws them in the row's
menu and, when the person selects several rows, in the menu under the list. You say what each
one does, and core asks first when it is dangerous, dims it where it does not fit, and reads
the list again afterwards.

This one builds on the practice site's scores, which now have an `id`, a `flagged` flag and a
page of their own. A list's rows need an `id` for the person to be able to pick them.

`recipes/score_rows/extension.json`

```json
{
  "name": "score_rows",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/score_rows",
  "requires_platform": 1,
  "capabilities": ["config:own", "net:outbound", "ui:mount"]
}
```

`recipes/score_rows/__init__.py`

```python
import json
import urllib.error
import urllib.request

from common.extensions.contract import Unavailable


def register(ctx):
    ctx.ui.settings([
        {"key": "site", "type": "string", "default": "http://127.0.0.1:8790"},
        {"key": "key", "type": "secret"},
    ])

    def call(path, method="GET", body=None):
        request = urllib.request.Request(
            ctx.config.get("site") + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-API-Key": ctx.config.get("key"), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                return json.loads(answer.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise Unavailable(ctx.t("error.refused")) from exc
            raise Unavailable(ctx.why(exc, at=request.full_url)) from exc
        except OSError as exc:
            raise Unavailable(ctx.why(exc, at=request.full_url)) from exc

    def rows():
        return [{**one, "page": f"{ctx.config.get('site')}/scores/{one['id']}",
                 "can_flag": not one["flagged"]} for one in call("/top")]

    def flag(picked):
        for one in picked:
            call(f"/scores/{one['id']}/flag", "POST")
        return ctx.t("message.flagged", count=len(picked))

    def remove(picked):
        for one in picked:
            call(f"/scores/{one['id']}", "DELETE")

    ctx.ui.list("scores", rows, area="community", columns=[
        {"field": "player"},
        {"field": "table"},
        {"field": "score", "kind": "number"},
    ], verbs=[
        {"key": "open", "link": "page"},
        {"key": "flag", "run": flag, "needs": "can_flag"},
        {"key": "remove", "run": remove, "danger": True},
    ])
```

`recipes/score_rows/i18n/en.json`

```json
{
  "name": "Score Rows",
  "description": "Lists the scores on Example Scores and acts on the ones you pick.",
  "settings.site.label": "Site",
  "settings.key.label": "Key",
  "error.refused": "The site did not accept the key",
  "message.flagged": {"one": "Flagged the score", "other": "Flagged {count} scores"},
  "list.scores.title": "Score Rows",
  "list.scores.column.player.header": "Player",
  "list.scores.column.table.header": "Table",
  "list.scores.column.score.header": "Score",
  "list.scores.verb.open.label": "Open on the Site",
  "list.scores.verb.flag.label": "Flag",
  "list.scores.verb.flag.needs": "Already flagged",
  "list.scores.verb.remove.label": "Remove",
  "list.scores.verb.remove.confirm": {
    "one": "Remove this score from the site?",
    "other": "Remove these {count} scores from the site?"
  }
}
```

**Where it appears:** on the list's page under Community in the rail, in the menu on each row
and in the menu above the list when rows are selected. **What triggers it:** the person picking rows and pressing a verb. Your
function is handed the rows they picked, and core reads the list again afterwards.

**What you'll see:** Score Rows under Community in the rail, with the three sample scores
once the key is set. Right-click a row for Open on the Site, Flag and, below a rule, Remove.
Select two rows and the same menu is under the list, with Open on the Site dimmed as One at a
time. Flag a score and its Flag is dimmed with Already flagged. Remove asks "Remove this score
from the site?" first, or "Remove these 2 scores from the site?" for two.

**Variations:**

- A verb that answers `{"message": ..., "url": ...}` shows the message and offers the address.
- Add `"one": True` to a run verb that only makes sense for a single row.
- A verb with no `needs` acts on every row picked. With `needs`, it acts only on the rows where
  that field is true. It is dimmed, with the `.needs` words, when the field is true on none.

**Watch out:** the `id` of a row is a string and must be different for every row of the list.
Two rows with the same id make the list fail with the reason, and a row with none can be read
but not picked.

### 10. States, chips, links and pictures in a column

**Use it when:** a column holds something the person should see at a glance: whether a
challenge is open, which tags a table carries, where its rules are, what it looks like.

A column is text unless you give it a `kind`. Four kinds draw a value as more than text:
`state` for one of a few fixed values, `chips` for a list, `link` for a web address and
`picture` for a small image. You hand core the values and say which kind a column is, and core
draws it the same way on every page. A state names its own words and what it looks like.

This one needs no service. It lists five weekly challenges that are written into the file, so
you can see all four kinds at once. The state column has five states, one of each look:

- `open` is `{"tier": "on"}`, `closing` is `warn` and `closed` is `off`. A tier is a colored
  chip. The tiers are `on`, `off`, `unknown`, `warn` and `bad`.
- `cancelled` is `{"icon": "block"}`, a Material icon drawn before the word.
- `planned` is `{}`, a quiet word with nothing around it. Most states should look like this one.

`recipes/weekly_board/extension.json`

```json
{
  "name": "weekly_board",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/weekly_board",
  "requires_platform": 1,
  "capabilities": ["ui:mount"]
}
```

`recipes/weekly_board/__init__.py`

```python
CHALLENGES = [
    {"name": "Medieval Madness", "status": "open", "tags": ["Williams", "Classic"],
     "page": "https://scores.example.com/weeks/41", "art": "art/flipper.png"},
    {"name": "Attack from Mars", "status": "closing", "tags": ["Bally"],
     "page": "https://scores.example.com/weeks/40", "art": "art/flipper.png"},
    {"name": "The Addams Family", "status": "closed", "tags": [],
     "page": "https://scores.example.com/weeks/39", "art": ""},
    {"name": "Twilight Zone", "status": "cancelled", "tags": ["Bally"],
     "page": "", "art": ""},
    {"name": "Theatre of Magic", "status": "planned", "tags": [],
     "page": "", "art": ""},
]


def register(ctx):
    ctx.ui.list("board", lambda: CHALLENGES, area="community", columns=[
        {"field": "name"},
        {"field": "status", "kind": "state", "states": {
            "open": {"tier": "on"},
            "closing": {"tier": "warn"},
            "closed": {"tier": "off"},
            "cancelled": {"icon": "block"},
            "planned": {},
        }},
        {"field": "tags", "kind": "chips"},
        {"field": "page", "kind": "link"},
        {"field": "art", "kind": "picture"},
    ])
```

`recipes/weekly_board/i18n/en.json`

```json
{
  "name": "Weekly Board",
  "description": "Lists the weekly challenges, with a state, tags, a link and a picture for each.",
  "list.board.title": "Weekly Board",
  "list.board.column.name.header": "Challenge",
  "list.board.column.status.header": "Status",
  "list.board.column.status.state.open": "Open",
  "list.board.column.status.state.open.why": "Scores are being taken",
  "list.board.column.status.state.closing": "Closing",
  "list.board.column.status.state.closing.why": "Ends within a day",
  "list.board.column.status.state.closed": "Closed",
  "list.board.column.status.state.cancelled": "Cancelled",
  "list.board.column.status.state.planned": "Planned",
  "list.board.column.tags.header": "Tags",
  "list.board.column.page.header": "Rules",
  "list.board.column.art.header": "Picture"
}
```

The first row's picture is a real file, `recipes/weekly_board/art/flipper.png`, in the
extension's folder. Copy the folder whole and it comes with it.

**Where it appears:** on the list's page under Community in the rail, as the Status, Tags,
Rules and Picture columns. **What triggers it:** the person opening the page. Core reads the
rows, draws each column by its kind and serves the picture from VPinFE's own address.

**What you'll see:** Weekly Board under Community in the rail with five rows. Status shows
Open in green, Closing in amber, Closed in grey, Cancelled with a stop sign before it and
Planned as plain text. Hover Open and it says "Scores are being taken". The Status filter
offers all five words. Tags shows Williams and Classic as two chips on the first row, and
nothing on the rows with none. Rules is a link marked as leaving the Manager, with the
column's header as its text. Picture shows the first two rows' picture at a small size, and
nothing on the others.

**Variations:**

- `{"field": "page", "kind": "link", "name_from": "title"}` reads the link as the row's `title`
  instead of the header, so each row's link says what it opens.
- A picture can be a web address: `"art": "https://example.com/a.png"`. Add `net:outbound` to
  `capabilities`, because core fetches it for you. Without it the list does not read, and says
  why.
- Leave `states` out of a state column and the extension does not start. The words for each
  state are `list.<key>.column.<field>.state.<value>`, and `--check-extension` tells you which
  you have not written.

**Watch out:** chips are for exceptions. A tag on every row says nothing, and a column of
chips where each row has one is a text column wearing a costume. Use `chips` for what some rows
have and most do not. The same goes for a tier: when most rows are `on`, give that state `{}`
and let only the ones worth noticing be colored.

### 11. A game in a list

**Use it when:** a row is about a game or a table, such as the machine a challenge is played
on, and the person should see it as they see it in the Games and Tables lists.

Give the column `"kind": "game"` and put a VPS id in the field.  Core draws the cell: where
your library holds the game, its picture, name, maker and year, with the name linking to its
page; where it does not, VPSdb's name and line; and where VPSdb does not know the id either, the
row field `name_from` names.  You write no picture and no link.  Use `"kind": "table"` for the id of
one build, a VPS release, and `"keys": "vpinfe"` for the ids VPinFE gave a game or a table in
this library.

This one needs no service.  It lists three games people voted for, written into the file.

`recipes/game_board/extension.json`

```json
{
  "name": "game_board",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/game_board",
  "requires_platform": 1,
  "capabilities": ["ui:mount"]
}
```

`recipes/game_board/__init__.py`

```python
PICKS = [
    {"vps_id": "vps-medieval", "name": "Medieval Madness", "votes": 41},
    {"vps_id": "vps-mars", "name": "Attack from Mars", "votes": 33},
    {"vps_id": "vps-homebrew", "name": "A table nobody catalogued", "votes": 2},
]


def register(ctx):
    ctx.ui.list("picks", lambda: PICKS, area="community", columns=[
        {"field": "vps_id", "kind": "game", "name_from": "name"},
        {"field": "votes", "kind": "number"},
    ])
```

`recipes/game_board/i18n/en.json`

```json
{
  "name": "Game Board",
  "description": "Lists the games people voted for, each drawn as the Games list draws it.",
  "list.picks.title": "Game Picks",
  "list.picks.column.vps_id.header": "Game",
  "list.picks.column.votes.header": "Votes"
}
```

**Where it appears:** on the list's page under Community in the rail, as the Game column.
**What triggers it:** the person opening the page.  Core reads the rows, asks the library and
VPSdb about the ids, and draws each cell.

**What you'll see:** Game Picks under Community in the rail with three rows.  A game the
library holds shows its picture, its name as a link to the game, and its maker and year.  A game
it does not hold but VPSdb knows shows VPSdb's name and line, with no link.  An id that neither
knows shows the row's own `name`.  The Game column is sorted and filtered by the name it shows,
so searching for a name finds the row.

**Variations:**

- `{"field": "build", "kind": "table"}` names one build of a machine.  A build the library
  does not hold, of a machine it does, says so under its name and links to the build it has.
- Add `tag="Weekly Challenge"` to the list and core puts that tag on the games, or the tables, the
  library holds from it.
- A game can also be a fact in a pane, a page or a group:
  `{"kind": "game", "value": vps_id, "says": "Medieval Madness"}`.  A fact's `says` is the
  words themselves, not a row field.

**Watch out:** the identifier, the first column that draws its own text (not a state, chips, link or picture) unless another says `"identifier": True`, is what
the list relates to in this library, so it is the one that gets a picture and a link.  A game or
table column that is not the identifier draws the name and line alone.

### 12. A pane beside a list

**Use it when:** a row has more to say than its columns can hold, or something the person
should be able to change without leaving the page: a note, a switch, a priority.

Give the list a `detail` function and clicking a row opens a pane beside it.  You answer with a
title, a line under it and sections of facts and fields, and core draws them as it draws
every other panel.  Give the list a `save` function too and the fields can be changed: each one
is written as soon as it is set, and core asks `detail` again to draw what was kept.  There is
no Save button, and nothing for you to draw.

This one needs no service.  It lists two weekly challenges written into the file, and the pane
for each has two sections, so you can see the rail they sit in:

- **About** holds facts, lines the person reads: a state (`tier` colors it, as in a column), some
  chips and a link.
- **Yours** holds fields, lines the person changes: a text, a switch, a choice and a number.

`recipes/challenge_pane/extension.json`

```json
{
  "name": "challenge_pane",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/challenge_pane",
  "requires_platform": 1,
  "capabilities": ["ui:mount"]
}
```

`recipes/challenge_pane/__init__.py`

```python
WEEK = {
    "mm": {"name": "Medieval Madness", "week": 41, "status": "open",
           "tags": ["Williams", "Classic"], "page": "https://scores.example.com/weeks/41",
           "note": "", "remind": False, "level": "normal", "goal": 2},
    "afm": {"name": "Attack from Mars", "week": 40, "status": "closed",
            "tags": ["Bally"], "page": "https://scores.example.com/weeks/40",
            "note": "Beat the saucer first", "remind": False, "level": "high", "goal": 5},
}

TIERS = {"open": "on", "closed": "off"}


def register(ctx):
    def rows():
        return [{"id": key, "name": one["name"]} for key, one in WEEK.items()]

    def detail(row_id):
        one = WEEK.get(row_id)
        if one is None:
            return None
        return {
            "title": one["name"],
            "under": ctx.t("pane.under", week=one["week"]),
            "sections": [
                {"key": "about", "label": ctx.t("pane.about"), "facts": [
                    {"label": ctx.t("pane.status"), "kind": "state",
                     "value": ctx.t(f"pane.status.{one['status']}"),
                     "tier": TIERS[one["status"]]},
                    {"label": ctx.t("pane.tags"), "kind": "chips", "value": one["tags"]},
                    {"label": ctx.t("pane.rules"), "kind": "link",
                     "value": ctx.t("pane.rules.open"), "to": one["page"]},
                ]},
                {"key": "mine", "label": ctx.t("pane.mine"), "fields": [
                    {"key": "note", "type": "string", "label": ctx.t("pane.note"),
                     "value": one["note"]},
                    {"key": "remind", "type": "switch", "label": ctx.t("pane.remind"),
                     "value": one["remind"], "help": ctx.t("pane.remind.help")},
                    {"key": "level", "type": "select", "label": ctx.t("pane.level"),
                     "value": one["level"],
                     "choices": [["normal", ctx.t("pane.level.normal")],
                                 ["high", ctx.t("pane.level.high")]]},
                    {"key": "goal", "type": "number", "label": ctx.t("pane.goal"),
                     "value": one["goal"], "min": 0, "max": 10},
                ]},
            ],
        }

    def save(row_id, values):
        WEEK[row_id].update(values)

    ctx.ui.list("board", rows, area="community", columns=[{"field": "name"}],
                detail=detail, save=save)
```

`recipes/challenge_pane/i18n/en.json`

```json
{
  "name": "Challenge Pane",
  "description": "Opens a pane beside the weekly challenges, with facts and fields you can change.",
  "list.board.title": "Challenge Pane",
  "list.board.column.name.header": "Challenge",
  "pane.under": "Week {week}",
  "pane.about": "About",
  "pane.status": "Status",
  "pane.status.open": "Open",
  "pane.status.closed": "Closed",
  "pane.tags": "Tags",
  "pane.rules": "Rules",
  "pane.rules.open": "Open the Rules",
  "pane.mine": "Yours",
  "pane.note": "Note",
  "pane.remind": "Remind Me",
  "pane.remind.help": "Before the week closes",
  "pane.level": "Priority",
  "pane.level.normal": "Normal",
  "pane.level.high": "High",
  "pane.goal": "Plays to Try"
}
```

The words in a pane are yours to give, so they go through `ctx.t` like any other, and the
catalog holds them.  A fact's `value` is the word the person reads, which is why the status is
looked up and not passed as `open`.

**Where it appears:** beside the list, on its page under Community in the rail.  **What
triggers it:** the person clicking a row.  Core calls `detail` with the row's `id` each time,
and again after every change.

**What you'll see:** Challenge Pane under Community with two rows.  Click Medieval Madness and
the pane opens on its right, headed by the name with "Week 41" under it, and a rail of About and
Yours.  About shows Open in green, Williams and Classic as two chips, and "Open the Rules" as a
link that leaves the Manager.  Yours has a text field, a switch, a choice and a number.  Type a
note and leave the field, and the pane redraws with the note kept.  Click Attack from Mars and
the pane is about it.  Reload the page and the pane opens on the row you left, because the
address names it.

**Variations:**

- A section always has its header: give each a `label`.  A lone section with none is headed
  Details.
- A `secret` field never shows its value.  The person sees that one is set, can type a new one,
  and leaves it empty to keep what is there.  `None` in a `save` means they pressed Clear.
- `save` can refuse.  Raise with the reason and the person sees it under the field, which keeps
  what they typed.
- `rows` in a section are plain labelled lines for a short list of things the row holds, such
  as a table's last three plays.

**Watch out:** `detail` is asked for any id, including one you never listed, so look it up and
return None when it is not there; the person is told the row is missing.  And a field key is
unique in the whole pane, not only in its section: two sections that both have a `note` leave
`save` unable to tell them apart, and core refuses the pane.

## Players and games

### 13. An account per player

**Use it when:** each player has something of their own with the extension, such as a handle or a key for a service, and it should sit on that player's panel.

An account whose `read` answers `fields` and no `user_id` is drawn by core as a settings page is, one control for each field, on the player's panel under the extension's name. Each is saved as it is left with `write(player_id, values)`, which is handed just the field that changed. A secret is never answered: `read` says `set` and core draws it empty. Core adds the Share switch under the fields.

`recipes/player_handle/extension.json`

```json
{
  "name": "player_handle",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/player_handle",
  "requires_platform": 1,
  "capabilities": ["ui:mount"]
}
```

`recipes/player_handle/__init__.py`

```python
def register(ctx):
    def read(player_id):
        held = ctx.players.account(player_id)
        return {"fields": [
            {"key": "handle", "type": "string", "label": ctx.t("account.handle"),
             "value": held.get("handle", "")},
            {"key": "api_key", "type": "secret", "label": ctx.t("account.key"),
             "set": bool(held.get("api_key"))}]}

    def write(player_id, values):
        held = dict(ctx.players.account(player_id))
        for key, value in values.items():
            if value is None:
                held.pop(key, None)
            else:
                held[key] = value
        ctx.players.set_account(player_id, held)

    ctx.ui.account(read=read, write=write)
```

`recipes/player_handle/i18n/en.json`

```json
{
  "name": "Player Handle",
  "description": "Keeps a handle and a key for each player.",
  "account.handle": "Handle",
  "account.key": "Key"
}
```

**Where it appears:** on a player's panel, under the extension's name. **What triggers it:**
the panel opening, which reads the fields, and the person leaving a field, which writes that
one for that player.

**What you'll see:** Frontend, then Players, then a player, then Player Handle: a Handle field, a masked Key field and a Share switch. Type a handle and leave the field, and it is kept for that player and no other. A key says Set once entered, with Clear beside it.

**Variations:**

- Make a field a `select` or a `switch` with `choices`, as in a settings page. The Fields table in the reference lists what an account draws.
- Raise an error from `write` to refuse a value. The refusal shows under the field, and the value is not kept.
- Answer `user_id` as well to have core draw the id, the claim and the card instead, which is the shape an account with a service of its own takes.

**Watch out:** `write` is handed `None` under a key when a secret is cleared, so remove the key and do not store the word None. A guest's account is held in memory and never written to disk, so a handle set on a guest is gone when they sign out.

### 14. A group on a game's panel

**Use it when:** something you know about a game or a table should sit on its panel, among the
rest of what the person sees there: a note, a score from a service, a version your service has.

Call `panel_group` with a `read` function and core draws what it answers in Play, after High
Scores.  You answer with a section of facts, fields and rows, the same as a pane's, and add a
`save` function to make the fields changeable.  The game, or the table, is handed to `read` as
ids, a name and the VPS ids.  A game marked Private is never asked about, so there is nothing
for you to check, unless the group is declared `private_ok=True`.

This one keeps a note for each game and shows the version of each table.  It needs no service,
and it needs `games:read` in the manifest, since a group is about the library.

`recipes/game_notes/extension.json`

```json
{
  "name": "game_notes",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/game_notes",
  "requires_platform": 1,
  "scopes": ["games:read"],
  "capabilities": ["ui:mount", "config:own"]
}
```

`recipes/game_notes/__init__.py`

```python
def register(ctx):
    def game_group(who):
        return {
            "facts": [{"label": ctx.t("group.about"), "value": who["name"]}],
            "fields": [{"key": "note", "type": "string", "label": ctx.t("group.note"),
                        "value": ctx.config.get(f"note.{who['game_id']}")}],
        }

    def save_game(who, values):
        ctx.config.set(f"note.{who['game_id']}", values["note"] or "")

    def table_group(who):
        return {"facts": [
            {"label": ctx.t("group.version"), "value": who["version"] or "-"},
            {"label": ctx.t("group.file"), "value": who["vps_file_id"] or "-"},
        ]}

    ctx.ui.panel_group("game", game_group, section="play", save=save_game)
    ctx.ui.panel_group("table", table_group, section="play")
```

`recipes/game_notes/i18n/en.json`

```json
{
  "name": "Game Notes",
  "description": "Keeps a note for each game and shows a table's version on its panel.",
  "group.game.title": "Game Notes",
  "group.table.title": "This Table",
  "group.about": "Game",
  "group.note": "Note",
  "group.version": "Version",
  "group.file": "File"
}
```

The heading over each group comes from `group.<subject>.title` in the catalog.

**Where it appears:** at the end of Play on a game's panel, and on a table's panel
too.  The table group is only on a table's.  **What triggers it:** the panel opening.  Core
calls `read` then, and again after each change to a field.

**What you'll see:** open a game in the library.  At the end of the Play section a Game Notes heading has the
game's name and a Note field.  Type a note and leave the field, and it is kept for that game.
Open one of its tables and the same group is there with a This Table group under it, with the
table's version and its VPS file.  Open a game marked Private and neither is drawn.

**Variations:**

- Answer None when there is nothing to say.  The group is not drawn, and takes no room in the
  panel.
- Drop `save` and the fields are shown and cannot be changed.
- Declare one group of each subject, as here.  A game can have one of each, and no more.
- Name no `section` and the group is a section of its own, after core's, with `title` as its
  name and an `icon` beside it.  `section` can also be `details`, `collections` or `guides`.

**Watch out:** `read` runs each time the panel opens, so a slow service makes the group late.
It never holds the panel back, but keep what you can in memory.  And the group needs
`games:read`; without it the extension is turned down with the reason when it starts.

### 15. An entry in a game's menu

**Use it when:** something you can do to a game or a table belongs next to Launch and Back Up
Now, where a person already is when they think of it: send it somewhere, mark it, hand it to
your service.

Call `verb` with the subject, a key and a function.  Core draws the entry in the Games grid's
row menu, in its selection menu and in the panel's header menu, in a block under your
extension's name after core's own.  The function is handed the games or tables chosen, so one
entry works on a row and on a selection without a second code path.  A game marked Private is
left out of what you are handed, unless the entry passes `private_ok=True` to say it sends
nothing out of VPinFE.

This one flags games, with a second entry that asks first, and shows a table's version.  It
needs `games:read` in the manifest, since an entry is about the library.

`recipes/game_verbs/extension.json`

```json
{
  "name": "game_verbs",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/game_verbs",
  "requires_platform": 1,
  "scopes": ["games:read"],
  "capabilities": ["ui:mount", "config:own"]
}
```

`recipes/game_verbs/__init__.py`

```python
def register(ctx):
    def flagged():
        return set((ctx.config.get("flagged") or "").split(",")) - {""}

    def keep(ids):
        ctx.config.set("flagged", ",".join(sorted(ids)))

    def flag(who):
        keep(flagged() | {one["game_id"] for one in who})
        return ctx.t("message.flagged", count=len(who))

    def unflag(who):
        keep(flagged() - {one["game_id"] for one in who})
        return ctx.t("message.unflagged", count=len(who))

    def version(who):
        return ctx.t("message.version", version=who[0]["version"] or "-")

    ctx.ui.verb("game", "flag", flag)
    ctx.ui.verb("game", "unflag", unflag, danger=True)
    ctx.ui.verb("table", "version", version, one=True)
```

`recipes/game_verbs/i18n/en.json`

```json
{
  "name": "Game Verbs",
  "description": "Adds Flag and Unflag to the Games menu and Show Version to the Tables menu.",
  "verb.game.flag.label": "Flag",
  "verb.game.flag.tip": "Keep these games in the extension's list of flagged games",
  "verb.game.unflag.label": "Unflag",
  "verb.game.unflag.confirm": "Unflag these games?",
  "verb.table.version.label": "Show Version",
  "message.flagged": "{count} flagged",
  "message.unflagged": "{count} unflagged",
  "message.version": "This table is version {version}"
}
```

Each entry's label, hover and question come from `verb.<subject>.<key>.label`, `.tip` and
`.confirm` in the catalog.

**Where it appears:** in the Games grid, in the menu at the end of a row, in the menu over a
selection and in the header menu of a game's panel; Show Version is on the Tables grid the
same way.  The entries sit under a Game Verbs heading, after core's.  **What triggers it:**
choosing the entry.

**What you'll see:** open a game's menu and, under the Game Verbs heading, Flag, then Unflag
under a rule.  Choose Flag and a message says how many games were flagged.  Select three games
and the selection menu has the same entries, and Flag is handed all three.  Unflag asks first.
Show Version is dimmed on a selection of several tables, and says so.

**Variations:**

- Answer `{"message": ..., "url": ...}` to open an address as well, such as a page on your
  service for the game.
- Give `label` to `verb` when you do not want a catalog entry for it.  It is used as written,
  in every language.
- Declare an entry for each subject with the same key.  A key is unique within a subject.

**Watch out:** the function runs once for everything chosen, not once per game, so do not
assume `who` has one entry unless you passed `one=True`.  And an entry needs `games:read`;
without it the extension is turned down with the reason when it starts.

## Beyond the Manager

### 16. A page of one subject

**Use it when:** the extension has one thing to show and not a list of them, such as this machine's start up, one board, or one account, and a grid of one row would be a click charged for nothing.

`ctx.ui.page` declares an entry in the rail that opens a panel.  You answer `read` with a title, a line under it and sections of facts and fields, as a [pane](#12-a-pane-beside-a-list) does, and core draws them full width.  Give it `save` and the fields can be changed, each written as it is set.  `acts` is the menu at the end of the page title.

`recipes/machine_page/extension.json`

```json
{
  "name": "machine_page",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/machine_page",
  "requires_platform": 1,
  "capabilities": ["ui:mount"]
}
```

`recipes/machine_page/__init__.py`

```python
MACHINE = {"boot": "play", "checked": "never"}


def register(ctx):
    def read():
        return {
            "title": ctx.t("page.machine.heading"),
            "under": ctx.t("page.machine.under"),
            "sections": [
                {"key": "state", "label": ctx.t("page.machine.state"), "facts": [
                    {"label": ctx.t("page.machine.checked"), "value": MACHINE["checked"]},
                ]},
                {"key": "boot", "label": ctx.t("page.machine.boot"), "fields": [
                    {"key": "boot", "type": "select", "label": ctx.t("page.machine.boot.mode"),
                     "value": MACHINE["boot"],
                     "choices": [["play", ctx.t("page.machine.boot.play")],
                                 ["desk", ctx.t("page.machine.boot.desk")]]},
                ]},
            ],
        }

    def save(values):
        MACHINE.update(values)

    def check():
        MACHINE["checked"] = ctx.t("page.machine.checked.now")
        return ctx.t("page.machine.checked.said")

    ctx.ui.page("machine", read, area="system", save=save,
                acts={"check": check, "manual": "https://machine.example.com/manual"})
```

`recipes/machine_page/i18n/en.json`

```json
{
  "name": "Machine Page",
  "description": "A page in the rail for the machine itself: a fact, a choice and an act.",
  "page.machine.title": "Machine",
  "page.machine.act.check.label": "Check Now",
  "page.machine.act.manual.label": "Manual",
  "page.machine.heading": "This Machine",
  "page.machine.under": "How it starts",
  "page.machine.state": "State",
  "page.machine.checked": "Last Checked",
  "page.machine.checked.now": "Just now",
  "page.machine.checked.said": "Checked",
  "page.machine.boot": "Start Up",
  "page.machine.boot.mode": "Starts Into",
  "page.machine.boot.play": "Play",
  "page.machine.boot.desk": "Desktop"
}
```

**Where it appears:** in the rail under System, above About.  **What triggers it:** the person opening it.  Core reads the page then, and again after each change and each act.

**What you'll see:** Machine in System, just above About.  The page is headed Machine, with This Machine and "How it starts" under it as the subject.  State shows Last Checked: never, and Start Up holds a choice, Starts Into, set to Play.  Change it and the page redraws with the choice kept.  The menu at the end of the title holds Check Now, which runs and says "Checked" and shows Just now in State, and Manual, which opens the address in a new tab.

**Variations:**

- Return one section with a title that says what it holds, or none: the page is then its heading alone.
- A `picture` in the answer is drawn above the sections.
- An act can open a dialog, as a list's can: give it `{"action": "<dialog key>"}`.

**Watch out:** the page is read each time it opens, so a slow service makes it late; keep what you can in memory.  The title in the rail is `page.<key>.title` and is held to the same rule as a list's: not a core page's name, and not another list's or page's in the same area.

### 17. A page of your own

**Use it when:** the thing you are adding already has a web page of its own, such as a hardware tool's console, a manual or a dashboard on another port, and a person should reach it from the Manager in one click.

`ctx.ui.link` declares an address and core draws it as a link.  The address is a string, or a function that returns one, so a link can follow a setting.  `{host}` is filled in with the host the Manager was opened at, so a tool that runs beside VPinFE is reached by the name the person already used, from a laptop or from the VPinFE device.  The label and the tip are words in the extension's catalog, named for the link.

`recipes/tool_link/extension.json`

```json
{
  "name": "tool_link",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/tool_link",
  "requires_platform": 1,
  "capabilities": ["config:own", "ui:mount"]
}
```

`recipes/tool_link/__init__.py`

```python
def register(ctx):
    ctx.ui.settings([{"key": "port", "type": "number", "default": 9090, "min": 1, "max": 65535}])

    def console():
        return f"http://{{host}}:{ctx.config.get('port')}/"

    ctx.ui.link("console", console, area="system")
    ctx.ui.link("manual", "https://tool.example.com/manual")
```

`recipes/tool_link/i18n/en.json`

```json
{
  "name": "Tool Link",
  "description": "Links to a hardware tool's own web page, and to its manual.",
  "settings.port.label": "Port",
  "settings.port.help": "The port the tool's page answers on",
  "link.console.label": "Tool Console",
  "link.console.tip": "The tool's own page, in a new tab",
  "link.manual.label": "Manual"
}
```

**Where it appears:** a link with an `area` is drawn on the Manager's rail in that area, and every link is in a menu at the end of the extension's page title.  The manual has no `area`, so it is in that menu only.  **What triggers it:** the person clicking it.  Nothing is fetched until they do, and nothing is drawn while the extension is switched off.

**What you'll see:** System on the rail shows Tool Manager with a small arrow-out mark, and the extension's page has a menu holding both links.  Both open in a new tab.  Change the Port setting and the next click goes to the new port.

**Variations:**

- Leave the `area` out of a link that belongs only on the extension's page.
- Return a different address from the function for each situation.  An answer that is not an `http://` or `https://` address leaves out that link, and `--check-extension` fails the extension.
- Link to a page you serve from VPinFE with `ctx.add_router` only if it can run with no sign-in: core answers every page under `/api/v1/ext/` sandboxed, as no origin of its own.  See [`ctx.ui.link`](reference/ui.md#ctxuilink).

**Watch out:** write `{host}` with doubled braces inside an f-string, as above, or Python fills it in and the host is lost.  A link is never drawn with another scheme: `file:`, `javascript:` and the rest are refused when it is declared, or dropped when a function answers one.

### 18. Asking for attention on Overview

**Use it when:** something the extension watches needs a person to act, such as scores flagged for review, a sync that stopped or an update that is ready, and they should see it where the Manager already collects such things, without opening the extension first.

`ctx.ui.needs` declares the kinds of thing you may ask for, and what a click on each opens: a list, a page or a dialog the extension offers.  A function you give it is called each time Overview is drawn, and answers the kinds that have something to ask for now, with the ids they cover.  Core draws the rows among its own and keeps what a person set aside.  The line is a word in the extension's catalog that is given the count, so it can say one and many.

`recipes/review_queue/extension.json`

```json
{
  "name": "review_queue",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/review_queue",
  "requires_platform": 1,
  "capabilities": ["config:own", "ui:mount"]
}
```

`recipes/review_queue/__init__.py`

```python
import json
import urllib.error
import urllib.request

from common.extensions.contract import Unavailable


def register(ctx):
    ctx.ui.settings([
        {"key": "site", "type": "string", "default": "http://127.0.0.1:8790"},
        {"key": "key", "type": "secret"},
    ])

    def scores():
        request = urllib.request.Request(ctx.config.get("site") + "/top",
                                         headers={"X-API-Key": ctx.config.get("key")})
        try:
            with urllib.request.urlopen(request, timeout=10) as answer:
                return json.loads(answer.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise Unavailable(ctx.t("error.refused")) from exc
            raise Unavailable(ctx.why(exc, at=request.full_url)) from exc
        except OSError as exc:
            raise Unavailable(ctx.why(exc, at=request.full_url)) from exc

    def flagged():
        return [one for one in scores() if one["flagged"]]

    def waiting():
        return [{"kind": "to_review", "ids": [str(one["id"]) for one in flagged()]}]

    ctx.ui.list("queue", flagged, area="community", columns=[
        {"field": "player"},
        {"field": "table"},
        {"field": "score", "kind": "number"},
    ])
    ctx.ui.needs({"to_review": {"list": "queue", "tone": "danger"}}, waiting)
```

`recipes/review_queue/i18n/en.json`

```json
{
  "name": "Review Queue",
  "description": "Asks on Overview for the scores flagged on Example Scores, and lists them.",
  "settings.site.label": "Site",
  "settings.key.label": "Key",
  "error.refused": "The site did not accept the key",
  "list.queue.title": "Flagged Scores",
  "list.queue.column.player.header": "Player",
  "list.queue.column.table.header": "Table",
  "list.queue.column.score.header": "Score",
  "need.to_review.label": {"one": "{count} flagged score to review", "other": "{count} flagged scores to review"},
  "need.to_review.says": "Flagged on Example Scores"
}
```

**Where it appears:** under Needs You on Overview, among core's own rows, with the extension's name where a count of items would be.  **What triggers it:** Overview being drawn, which asks `waiting` once.  Nothing is asked while the extension is switched off.

**What you'll see:** flag a score on the practice site and Overview gains a red row, 1 flagged score to review.  Clicking it opens the Flagged Scores list.  Dismiss hides the row until another score is flagged, Undo reverses the Dismiss, and Dismissed under the rows offers Show Again.

**Variations:**

- Open a page with `"page": "week"`, or a dialog with `"action": "update"`.  A dialog opens over no rows, and Overview is drawn again once it closes.
- Answer `{"kind": "update_ready", "count": 1, "token": "2.1"}` where there are no ids to list.  The row stays away until the token is another.
- Give a kind `"tone": "muted"` for something to know, not to do.  See [`ctx.ui.needs`](reference/ui.md#ctxuineeds).

**Watch out:** answer only the kinds that have something to ask for.  A kind that answers a count of nothing is left out, but a `read` that raises, or that names a kind it never declared, leaves out all of that extension's rows and Overview says so.

### 19. An extension for one Linux distribution

**Use it when:** the extension manages something only one kind of machine has, such as a cabinet's boot mode on VPinOS, and it should not be offered anywhere else.

`platforms` in the manifest names where it runs.  Beside `linux`, `windows` and `macos` it takes a Linux distribution's `ID`, the `ID=` line of `/etc/os-release`: `vpinos`, `ubuntu`, `debian`, `fedora`.  `linux` matches every Linux, and an `ID` matches that distribution only.  An extension runs where any name in the list matches.  Core does not keep a list of distributions, so any `ID` is accepted as long as it is written the way os-release writes it: lowercase letters, digits, `.`, `_` and `-`.

`recipes/vpinos_only/extension.json`

```json
{
  "name": "vpinos_only",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/vpinos_only",
  "requires_platform": 1,
  "platforms": ["vpinos"],
  "capabilities": ["config:own", "ui:mount"]
}
```

`recipes/vpinos_only/__init__.py`

```python
def register(ctx):
    ctx.ui.settings([{"key": "boot", "type": "select", "default": "play",
                      "choices": [["play", "Play"], ["desktop", "Desktop"]]}])
```

`recipes/vpinos_only/i18n/en.json`

```json
{
  "name": "VPinOS Only",
  "description": "Settings for something only a VPinOS cabinet has.",
  "settings.boot.label": "Start In",
  "settings.boot.help": "What the cabinet comes up in after it is switched on"
}
```

**Where it appears:** on a VPinOS cabinet it is a page like any other, with the Start In setting.  Anywhere else it has no page in Settings, and the log says once which platforms it asked for.  **What triggers it:** core reads the machine's os-release the first time it loads an extension, and compares its `ID` to the list.  Nothing in the extension runs on a machine it is not for, not even `register`.

**What you'll see:** on a Mac the page says "It does not run on macOS", and on Ubuntu "It does not run on Ubuntu".  On a Linux with no `/etc/os-release` it says "It does not run on Linux".  On VPinOS it runs, and the page shows Start In.

**Variations:**

- Name several, `["vpinos", "ubuntu"]`, for an extension that runs on either.
- Name the family, `["linux"]`, to run on every distribution and keep Windows and macOS out.  A distribution built on another is its own `ID`: `ubuntu` does not match Linux Mint, so name both.
- Run `--check-extension` on your own device.  It loads the extension whatever its `platforms` say, so you can try the code anywhere, and a `platforms` line tells you whether this machine would run it: `platforms  vpinos - not this machine: It does not run on macOS`.

**Watch out:** the `ID` is lowercase in `/etc/os-release` and here: `VPinOS` is refused, `vpinos` is not.  The name in the reason, "Ubuntu", is the distribution's `NAME`, which is for people to read, and it is not what `platforms` matches.

### 20. Asking the operating system to change something

**Use it when:** the extension manages something only root can change, such as the operating system's updates on a VPinOS cabinet.

VPinFE never runs as root.  Where the system ships a helper, `ctx.os` is the one way to ask it, and the helper has a short list of methods, each fixed: no command, no path, no package name.  Each method belongs to an area, and an area is a capability, so `os:updates` is what lets this extension check for updates and install them.  A call to a method outside the declared areas raises `ContractError`.

A method that only reads, such as `updates.check`, can be called from anywhere.  A method that changes the machine is refused unless the request it is called from reached Protected, and a job started from that request carries the mark.  A change from an event handler, a timer or a thread the extension started has no request behind it and is refused, so it fails closed even if the declaration is wrong.

A request reaches Protected when an Owner makes it under a live Confirm, and only an act declared Protected keeps that mark.  So the extension declares its acts `tier=PROTECTED` and lists `protected` under `tiers`: the Manager then asks the Owner to confirm before it runs the install, and a token or a Remote pass is refused.  The restart that finishes the updates is declared the same way.  `system:admin` in `scopes` is what lets `ctx.system` restart VPinFE or the device.

`recipes/os_update/extension.json`

```json
{
  "name": "os_update",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/os_update",
  "requires_platform": 1,
  "platforms": ["vpinos"],
  "scopes": ["system:admin"],
  "capabilities": ["os:updates", "ui:mount"],
  "tiers": ["protected"]
}
```

`recipes/os_update/__init__.py`

```python
import os

from common.extensions.contract import PROTECTED

REBOOT_FILE = "/run/reboot-required"


class Updates:
    def __init__(self, ctx):
        self.ctx = ctx
        self.upgraded = False

    def says(self, method):
        return next(one["says"] for one in self.ctx.os.methods if one["name"] == method)

    def ask(self, step):
        if not self.ctx.os.available:
            return {"ready": False, "reason": self.ctx.t("dialog.no_helper"),
                    "confirm": self.ctx.t("dialog.install")}
        waiting = self.ctx.os.call("updates.check")
        count = waiting.get("count", 0) if isinstance(waiting, dict) else 0
        return {"summary": [[self.ctx.t("dialog.waiting"), str(count)],
                            [self.ctx.t("dialog.will"), self.says("updates.apply")]],
                "confirm": self.ctx.t("dialog.install")}

    def run(self, values):
        def install(job):
            done = self.ctx.os.call("updates.apply")
            if isinstance(done, dict) and done.get("vpinfe_upgraded") is True:
                self.upgraded = True
            return done

        job = self.ctx.jobs.submit("install", install)
        return {"job_id": job.id}


class Restart:
    def __init__(self, ctx, updates):
        self.ctx = ctx
        self.updates = updates

    def ask(self, step):
        wording = "dialog.reboot" if os.path.exists(REBOOT_FILE) else "dialog.restart"
        return {"summary": [[self.ctx.t("dialog.will"), self.ctx.t(wording)]],
                "confirm": self.ctx.t("dialog.restart_now")}

    def run(self, values):
        if os.path.exists(REBOOT_FILE):
            self.ctx.system.reboot("system updates")
        else:
            self.ctx.system.restart_vpinfe("system updates")
        self.updates.upgraded = False
        return self.ctx.t("dialog.restarting")


def register(ctx):
    updates = Updates(ctx)
    ctx.ui.action("updates", updates, tier=PROTECTED)
    ctx.ui.action("restart", Restart(ctx, updates), tier=PROTECTED)

    def waiting():
        if os.path.exists(REBOOT_FILE) or updates.upgraded:
            return [{"kind": "restart", "count": 1,
                     "token": "reboot" if os.path.exists(REBOOT_FILE) else "vpinfe"}]
        return []

    ctx.ui.needs({"restart": {"action": "restart", "tone": "warm"}}, waiting)
```

`recipes/os_update/i18n/en.json`

```json
{
  "name": "OS Update",
  "description": "Installs the operating system's updates on a VPinOS cabinet, after asking.",
  "action.updates.label": "System Updates",
  "action.restart.label": "Restart",
  "dialog.waiting": "Updates Waiting",
  "dialog.will": "This Will",
  "dialog.install": "Install",
  "dialog.no_helper": "This device has no system helper",
  "dialog.reboot": "Restart this device",
  "dialog.restart": "Restart VPinFE",
  "dialog.restart_now": "Restart",
  "dialog.restarting": "Restarting now",
  "need.restart.label": "Restart to finish the updates",
  "need.restart.says": "The updates are installed and take effect after a restart"
}
```

**Where it appears:** on a VPinOS cabinet, as two actions on the extension's page, System Updates and Restart.  Restart is also a Needs You row, which shows while the system says it needs a restart (`/run/reboot-required` exists) or after an install that upgraded VPinFE itself.  **What triggers it:** pressing System Updates.  `ask` runs when the dialog opens and reads `updates.check`, which changes nothing.  `run` runs when Install is pressed, in that request, and starts a job that calls `updates.apply`.  The helper answers `installed`, `reboot_required` and `vpinfe_upgraded`; the job keeps the last in memory, and reads each as true only when it is `true`.

**What you'll see:** the dialog lists how many updates are waiting and, under This Will, core's own sentence for the change: "Install system updates on this device".  The extension chose the label and the layout, and core chose the sentence, so the extension cannot word a change that needs a Confirm as a harmless one.  Pressing Install asks the Owner to confirm, then closes the dialog and the job runs in Jobs.  When it is done the Needs You row reads "Restart to finish the updates", and pressing it opens a dialog that says whether it will restart this device or restart VPinFE.  Where no helper answers, the System Updates dialog says "This device has no system helper" and holds Install.  A token or a Remote pass that tries the install is refused before `run` starts, with "Only this device, or an Owner with a Confirm, can do this".

**Variations:**

- Call `updates.apply` straight from `run` when it is quick.  It still takes a Protected request, since `run` answers one.
- Leave `tier=PROTECTED` off an act and the Manager runs it without a Confirm, and `ctx.os.call` refuses the change, even from an Owner holding a live Confirm, so the install fails rather than runs.
- Read `ctx.os.methods` to build the dialog from what the manifest allows rather than naming methods: each entry has `name`, `changes` and `says`.
- Run `--check-extension` on a Mac.  It loads the extension whatever its `platforms` say, and `ctx.os.available` is false there, so the dialog shows the no-helper line.

**Watch out:** a change called from `register`, an event handler or a timer is refused, however the manifest reads.  Start it from an action, a verb, an act or a route, and from a job those start.  And the helper is part of the operating system, not of VPinFE: on a machine without one the method list is still there and `ctx.os.available` is false.

## Collections, devices and the Remote

### 21. Putting games in a collection

**Use it when:** the games a person chose belong in a collection, such as the ones to play this weekend, and typing them in again on the Collections page is the slow part.

`ctx.collections` is core's own collection functions, and the manifest decides which of them the extension may call.  `collections:read` lists and reads collections.  `collections:write` makes, fills, empties and orders them.  A game entry also needs `games:read` and `ui:mount`, as in [recipe 15](#15-an-entry-in-a-games-menu).

This extension adds two entries to the game menu.  The first puts the chosen games in one collection and makes the collection the first time.  The second takes them out again.  The collection's name is a setting, so a person can point it somewhere else.

`recipes/weekend_picks/extension.json`

```json
{
  "name": "weekend_picks",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/weekend_picks",
  "requires_platform": 1,
  "scopes": ["games:read", "collections:read", "collections:write"],
  "capabilities": ["config:own", "ui:mount"]
}
```

`recipes/weekend_picks/__init__.py`

```python
def register(ctx):
    ctx.ui.settings([{"key": "collection", "type": "string", "default": "Weekend Picks"}])

    def exists(name):
        listed = ctx.collections.list_collections()["collections"]
        return any(one["name"] == name for one in listed)

    def holds(name):
        return ctx.t("message.holds", name=name,
                     count=len(ctx.collections.collection_members(name)["members"]))

    def pick(who):
        name = ctx.config.get("collection")
        if not exists(name):
            ctx.collections.create_collection(name, description=ctx.t("collection.about"))
        for one in who:
            ctx.collections.add_to_collection(name, one["game_id"])
        return holds(name)

    def unpick(who):
        name = ctx.config.get("collection")
        if not exists(name):
            return ctx.t("message.none", name=name)
        named = {one["game"] for one in ctx.collections.collection_members(name)["members"]
                 if one["origin"] == "named"}
        for one in who:
            if one["game_id"] in named:
                ctx.collections.remove_from_collection(name, one["game_id"])
        return holds(name)

    ctx.ui.verb("game", "pick", pick)
    ctx.ui.verb("game", "unpick", unpick)
```

`recipes/weekend_picks/i18n/en.json`

```json
{
  "name": "Weekend Picks",
  "description": "Adds the games chosen in the Games grid to a collection, and takes them out again.",
  "settings.collection.label": "Collection",
  "settings.collection.help": "The collection the picks go in, made the first time a game is picked",
  "verb.game.pick.label": "Add to Picks",
  "verb.game.pick.tip": "Put these games in the picks collection",
  "verb.game.unpick.label": "Take Out of Picks",
  "collection.about": "Games picked for the weekend",
  "message.holds": "{name} now holds {count}",
  "message.none": "There is no collection called {name} yet"
}
```

`list_collections()` answers `{"collections": [...]}`, and each entry has the `name`, the `type` and the `description`.  `collection_members(name)` answers `{"members": [...]}`, one for each row, and a row has the `game` id and an `origin` that reads `named` for a game somebody put in.  `remove_from_collection` raises for a game that is not named in the collection, so Take Out of Picks checks first and a selection that includes a game that was never picked still works.  `create_collection(name, description=...)` makes a hand-picked collection.  `add_to_collection(name, game_id)` adds one game, and adding a game already in it succeeds and changes nothing, so a person can pick the same game twice.

**Where it appears:** in the Games grid, in the same menus as [recipe 15](#15-an-entry-in-a-games-menu), under a Weekend Picks heading.  The setting is on the extension's page.  **What triggers it:** choosing the entry.

**What you'll see:** choose Add to Picks on three games and a message says "Weekend Picks now holds 3".  Collections lists Weekend Picks with the description "Games picked for the weekend", and the three games are in it.  Choose it again on one of the three and the message still says 3.  Take Out of Picks on one of them says 2.  Change the Collection setting to another name and the next pick makes that collection, and leaves the old one as it was.

**Variations:**

- Call `set_collection_order(name, game_ids)` to put the games in an order you choose.  It takes the id of every member, once for each row.
- Call `collection_games(name)` to read what a collection holds before changing it.
- Leave `collections:write` out and the extension still starts.  Reaching for a function that needs it, as in `ctx.collections.create_collection`, raises `ContractError` and names the scope.

**Watch out:** the collection you make is hand-picked.  Rules, ordering by a field, exclusions and pictures are set in the Manager and are not offered here.  And a Private game is left out of what the entry is handed, so it is never added.  See [`ctx.collections`](reference/games.md#ctxcollections) for every call.

### 22. Listing what is plugged in

**Use it when:** your extension works with a device, such as a controller board or a display, and a person needs to see whether VPinFE can find it, and may open it, before anything else goes wrong.

`ctx.hardware` lists the USB devices, serial ports and displays attached to this device.  The manifest needs `hardware:read` in `scopes`.  The answer is the same on Windows, macOS and Linux, and it never holds a serial number.

This extension draws the answer as a list.  Each row is a device, with the vendor and product ids and whether VPinFE may open it.  A line under the list says what lets a device open on this operating system, and shows only when some device cannot be opened.

`recipes/plugged_in/extension.json`

```json
{
  "name": "plugged_in",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/plugged_in",
  "requires_platform": 1,
  "scopes": ["hardware:read"],
  "capabilities": ["ui:mount"]
}
```

`recipes/plugged_in/__init__.py`

```python
ACCESS = {True: "yes", False: "no"}


def register(ctx):
    def devices():
        return [{
            "name": one["name"],
            "kind": one["kind"],
            "ids": f"{one['vendor_id']}:{one['product_id']}" if one["vendor_id"] else "",
            "access": ACCESS.get(one["accessible"], "unknown"),
        } for one in ctx.hardware.devices()]

    def status():
        if any(one["access"] == "no" for one in devices()):
            return ctx.hardware.access_help()
        return ""

    ctx.ui.list("devices", devices, area="system", status=status, columns=[
        {"field": "name"},
        {"field": "kind", "kind": "state", "states": {"usb": {}, "serial": {}, "display": {}}},
        {"field": "ids"},
        {"field": "access", "kind": "state", "states": {
            "yes": {}, "no": {"tier": "bad"}, "unknown": {}}},
    ])
```

`recipes/plugged_in/i18n/en.json`

```json
{
  "name": "Plugged In",
  "description": "Lists the USB devices, serial ports and displays attached to this device, and whether VPinFE may open each.",
  "list.devices.title": "Plugged In",
  "list.devices.column.name.header": "Device",
  "list.devices.column.kind.header": "Kind",
  "list.devices.column.kind.state.usb": "USB",
  "list.devices.column.kind.state.serial": "Serial Port",
  "list.devices.column.kind.state.display": "Display",
  "list.devices.column.ids.header": "Vendor and Product",
  "list.devices.column.access.header": "Can Open",
  "list.devices.column.access.state.yes": "Yes",
  "list.devices.column.access.state.no": "No",
  "list.devices.column.access.state.no.why": "VPinFE may not open this device",
  "list.devices.column.access.state.unknown": "Not Known"
}
```

A device is a dict of `kind`, `vendor_id`, `product_id`, `vendor`, `product`, `name`, `port` and `accessible`.  The ids are four lowercase hex digits, or `""` where the device has none.  `accessible` is `True` or `False` when the operating system can say whether VPinFE may open the device, and `None` when it cannot say without opening it, so the list has a third word for it.

**Where it appears:** on the rail under System, as Plugged In.  **What triggers it:** the person opening the page.  USB devices and serial ports are read each time, so plugging a board in and opening the page again shows it.

**What you'll see:** Plugged In lists each device with its kind as USB, Serial Port or Display.  A display has no ids, so that cell is empty.  Can Open reads Yes, No in red, or Not Known.  Where a device reads No, a line under the list says what to change on this operating system: a udev rule on Linux, a Privacy setting on macOS, a driver on Windows.  Hovering the red No says "VPinFE may not open this device".

**Variations:**

- Ask for one kind with `ctx.hardware.devices("usb")`.  The kinds are `usb`, `serial` and `display`, and any other word raises `ValueError`.
- A display also has `x`, `y`, `width` and `height`, so a row can say where it sits.
- Declare the `hardware:usb` capability when the extension opens a device itself.  Core lists devices and opens none, and sets no permission on one, so reaching it is the extension's own work.

**Watch out:** a kind this device cannot list is left out of the answer, and nothing is raised.  Where this device has no serial support, the list shows no serial rows and does not say so.  And `hardware:read` is not `devices:read`: that scope is about the other VPinFE installs a player has.

### 23. A page a Remote can open

**Use it when:** something belongs on a Remote, such as a note for whoever plays next, and the program holding the Remote's pass should not need the Owner's sign-in to read or change it.

A Remote pass reaches only what an extension puts at the Play tier, and the Owner is asked about that when the extension is installed.  VPinFE's own Remote screens do not draw an extension's pages.  A page at Play is reached at its address by whatever holds a Remote pass, such as a tool you write.  Two lines say it.  The manifest's `play` list names the surface, as `page:note`, and the surface is declared with `tier=PLAY`.  Each without the other fails: a surface at Play that the list does not name is refused when it is declared, and a name on the list that is not at Play fails the load.  An update that adds a name asks the Owner again.

The `play` list names a page as `page:<key>`, a list as `list:<key>`, an action as `action:<key>`, a section on a game's panel as `group:<subject>`, an entry in a game's menu as `verb:<subject>:<key>`, and `router` for the web addresses of an extension's own router.

`recipes/next_player_note/extension.json`

```json
{
  "name": "next_player_note",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/next_player_note",
  "requires_platform": 1,
  "capabilities": ["config:own", "ui:mount"],
  "play": ["page:note"]
}
```

`recipes/next_player_note/__init__.py`

```python
from common.extensions.contract import PLAY


def register(ctx):
    def read():
        return {
            "title": ctx.t("page.note.heading"),
            "sections": [
                {"key": "note", "label": ctx.t("page.note.section"), "fields": [
                    {"key": "note", "type": "string", "label": ctx.t("page.note.field"),
                     "value": ctx.config.get("note") or ""},
                ]},
            ],
        }

    def save(values):
        ctx.config.set("note", values["note"])

    def clear():
        ctx.config.set("note", "")
        return ctx.t("page.note.cleared")

    ctx.ui.page("note", read, area="community", save=save, acts={"clear": clear}, tier=PLAY)
```

`recipes/next_player_note/i18n/en.json`

```json
{
  "name": "Next Player Note",
  "description": "A note for whoever plays next, which a Remote can read and change.",
  "page.note.title": "Next Player Note",
  "page.note.act.clear.label": "Clear Note",
  "page.note.heading": "Next Player Note",
  "page.note.section": "Leave a Note",
  "page.note.field": "Note",
  "page.note.cleared": "Cleared"
}
```

The page is read, saved and cleared at `/api/v1/ext/next_player_note/pages/note` and the addresses under it, as [what core serves](reference/served.md) lists.  A request with a Remote pass is answered there, and a request with a Remote pass to anything the extension declared at the Library tier is refused.

**Where it appears:** in the Manager as Next Player Note under Community, like any page.  On the extension's page, under A Remote Can Open, a chip reads "the note page".  **What triggers it:** the person opening the page, changing the field or pressing Clear Note.

**What you'll see:** the page is headed Next Player Note, with a Leave a Note section holding one field, Note.  Type a note and leave the field, and it is there the next time the page opens.  The menu at the end of the title holds Clear Note, which empties the field and says "Cleared".  When the extension is installed from a folder, the dialog that asks the Owner to accept it lists "the note page" under A Remote Can Open, so the Owner sees before accepting that a Remote can change the note.

**Variations:**

- Put a list at Play to let a Remote read its rows: declare it with `tier=PLAY` and name it `list:<key>` in `play`.  The list's verbs are gated with the list, so they need no name of their own.
- Keep a surface at the default tier when only the Owner should reach it.  Most surfaces are, so name only what a Remote has a reason to open.

**Watch out:** a picture is served at its own address and carries no tier, so one in an extension with a page a Remote can open can be fetched by a Remote.  Show nothing private in a picture there.

## Importing files

### 24. Importing a folder

**Use it when:** files are waiting in a folder, such as a table with its backglass and its ROM, and core should file them in the library the way a drop onto the Manager does.

`ctx.uploads` is core's own import, asked for by an extension.  `plan` says what the import would do and writes nothing, so a dialog can show it first.  `execute` does it.  The manifest names `uploads:write`, and `fs:read` as well, because the folder has to be one the extension declared with `ctx.files.set_roots`.

This extension has one setting, the folder, and one action.  The action is a single step: `ask` plans the import and shows what would be imported and where, and its button runs the import as a job, because copying a table takes as long as it takes.

`recipes/import_folder/extension.json`

```json
{
  "name": "import_folder",
  "version": "1.0.0",
  "author": "Example Author",
  "source": "https://github.com/example/import_folder",
  "requires_platform": 1,
  "scopes": ["uploads:write"],
  "capabilities": ["config:own", "fs:read", "ui:mount"]
}
```

`recipes/import_folder/__init__.py`

```python
class ImportFolder:
    def __init__(self, ctx):
        self.ctx = ctx

    def folder(self):
        folder = self.ctx.config.get("folder")
        self.ctx.files.set_roots([folder])
        return folder

    def ask(self, step):
        folder = self.folder()
        coming, notes = [], []
        if folder:
            plan = self.ctx.uploads.plan(folder, allow_new_game=True)
            coming = [[item["label"], item["destination"]] for item in plan["items"]]
            notes = [one["reason"] for one in plan["blocked"]]
        return {"summary": coming, "notes": notes, "ready": bool(coming),
                "reason": self.ctx.t("dialog.nothing" if folder else "dialog.no_folder"),
                "confirm": self.ctx.t("dialog.confirm")}

    def run(self, values):
        folder = self.folder()

        def work(job):
            report = self.ctx.uploads.execute(folder, allow_new_game=True)
            job.problems = len(report["skipped"])
            return {"game_dir": report["game_dir"], "imported": report["imported"]}

        job = self.ctx.jobs.submit("import_folder", work, keep_result=True)
        return {"job_id": job.id}


def register(ctx):
    ctx.ui.settings([{"key": "folder", "type": "path", "wants": "dir"}])
    ctx.ui.action("import_folder", ImportFolder(ctx))
```

`recipes/import_folder/i18n/en.json`

```json
{
  "name": "Import Folder",
  "description": "Imports a folder of downloaded files into the library as a new game.",
  "settings.folder.label": "Folder",
  "settings.folder.help": "The folder whose files are imported",
  "action.import_folder.label": "Import the Folder",
  "dialog.no_folder": "Choose a folder in the extension's settings first",
  "dialog.nothing": "Nothing in that folder can be imported",
  "dialog.confirm": "Import"
}
```

The plan lists each thing it found with where it will go, and `blocked` is what it will not do and why, which goes in as notes.  `execute` imports every item the plan lists unless `selected=` names which.  `set_roots` runs each time rather than once in `register`, so a changed setting is followed.  `execute` with the same arguments does what the summary showed, and it reads the folder again when it runs, so it imports what is there then.

**Where it appears:** as a button on the extension's page in Settings, with the setting beside it.  **What triggers it:** the person pressing Import the Folder.

**What you'll see:** with no folder chosen, the step says "Choose a folder in the extension's settings first" and its Import button is held.  With a folder holding a table and its backglass, the step lists each with where it will go.  Pressing Import closes the dialog, the job appears in Jobs, and the new game appears in the library.

**Variations:**

- Name a game with `game_id=` and `add_table=True` to add the table to a game already in the library instead of making a new one.  The manifest then needs `games:read` as well.
- Without `add_table`, a table for a game that has one takes the place of its default table and keeps its entry.  Pass `replace_table=` with a table id to replace another, and `carry=[...]`, the kinds of file named for the old table (`backglass`, `ini`, `pov`...) that follow it to the new one; each plan item that replaces a table says what is carried in `swap`.  The old files go to Backups.
- Name a table of the game with `table_id=` and the media and the files named for a table (`backglass`, `ini`, `script`, `pov`, `scv`, `nix`) are named for that table.  `scope=` (`"game"` or `"table"`) overrides what media is named for, where left out a game with one table is named for the game, and `page=` is the page a paged media kind is written as, from 1.
- Call `analyze(folder)` first to say what a folder holds before planning.
- Pass `selected=[...]` to `execute`, the `index` of each plan item to import, to import some of what the plan lists.
- Pass `declared={"Taxi 1.2.vpx": {"host": "example", "host_item_id": "t-9", "confirmed_by": "declared"}}` to `execute` to record where each file came from.  A claim that names a record has to say how it is known.

**Watch out:** the source is read where it is and is left there.  Move or delete it yourself once the job is done.  See [`ctx.uploads`](reference/games.md#ctxuploads) for every key a request takes.
