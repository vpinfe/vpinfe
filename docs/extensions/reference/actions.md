# Actions and dialogs

`ctx.ui.action(key, dialog, *, label, description, icon, section, add, menu, private_ok)`.  An extension does not draw.  It declares
an **action** and core shows it as a line in the extension's panel (see [`ctx.ui.action`](ui.md#ctxuiaction)), so every action looks like the Manager and keeps working if
that extension later runs out of process.

The dialog is any object.  It must have `ask`; core uses the rest if they are there:

| method | called when | returns |
|---|---|---|
| `ask(step[, rows][, who])` | The dialog opens (`step` is `""`), or a done step is reopened | The step: what to ask, if anything |
| `check(values[, step][, who])` | Next is pressed, or a value changes | The step again, with `ready`, `summary`, `notes`, `errors`.  None means ask again |
| `act(key, values[, step][, who])` | A step's own quiet button is pressed | The step redrawn.  None means ask again |
| `run(values[, step][, who][, progress])` | The last step's button is pressed | A message string, a dict, or `{"job_id": ...}` |
| `progress()` | Core polls it while a step is slow | `{stage, message, done, total}` |

Core hands `step`, `rows`, `who` and `progress` only to a method that names them.  The default tracker
given as `progress` has `stage(name, message="", total=0)`, `advance(done=None, message=None)`
and `clear()`, and answers the same shape for `GET .../progress` when the dialog defines no
`progress` of its own.  An action that is one call and a sentence does not need it.

```python
class Import:
    def ask(self, step):
        return {"help": ctx.t("action.import.help"),
                "fields": [{"key": "folder", "type": "path", "wants": "dir",
                            "label": ctx.t("action.import.folder")}],
                "confirm": ctx.t("action.import.confirm")}

    def check(self, values):
        if not values.get("folder"):
            return {"ready": False, "reason": ctx.t("action.import.no_folder")}
        return {"ready": True, "summary": [["Folder", values["folder"]]]}

    def run(self, values, progress):
        progress.stage("reading", total=2)
        progress.advance()
        return {"message": ctx.t("action.import.done")}


ctx.ui.action("import", Import())
```

**How many steps an action has comes from what it answers.**  You never declare it.  No
`fields` means press it and it happens.  `fields` means fill them in first.  A `confirm` means
a step that shows what would happen before it runs.  A declared mode would say the same thing
twice, and the two could drift apart.

A `confirm` with no `summary` is the last step with nothing to show first: its forward button
reads the `confirm`, and pressing it runs.  A run can answer with a `step` instead of an
outcome, and core shows it the way it shows a `check` answer.  That lets an answer that changes
the path move on to a question that was not there when the last step was shown.  Each step gets
up to 15 minutes to answer, so a step that reads a slow source is waited for.

**A step answer** is a dict.  Keys:

| key | meaning |
|---|---|
| `title` | The step's heading.  Left out, the action's label (`action.<key>.label`) |
| `help` | A line, or `{follows, says, else}` to say what `says` holds for the value the field named `follows` has now, and `else` for any other |
| `fields` | The fields to ask for.  See [Fields](#fields) |
| `confirm` | The verb at the point of no return, the action's own.  A generic "Confirm" makes every action look like every other one |
| `steps` | `[{key, label, done}]`, the path as it stands.  A `done` step reopens with `GET .../actions/<key>?step=<key>`.  An action that cannot say how many steps it has sends none and loses nothing |
| `welcome` | True centers the step, for a flow's first: the `title` at display size, the `help` as the lead, and the label of its `choice` as the question over it.  Every `check` and `act` answer replaces the step, so a step that stays a welcome repeats `welcome: true` |
| `ready`, `reason` | `ready: false` holds Next or the last step's `confirm` until it is true again, with `reason` shown beside the held button.  Any step may send it |
| `summary` | `[[label, value], ...]`: what the steps add up to.  A row of one is a heading, not a fact |
| `notes` | Lines that travel with the summary, so a count never stays quiet about what the job cannot do.  A string, or `{text, detail, to}` where `detail` shows on hover and `to` is a web address the line links to, drawn as `text` with the mark that says it leaves the Manager.  A `to` that is not an `http` or `https` address, or has no `text`, fails the answer |
| `errors` | `{field_key: text}`: turn down one field by name, shown under it.  Each value is a string, or `{text, detail}` as a note is.  An error does not link, and a `to` on one fails the answer |
| `acts` | `[{key, label}]`: quiet buttons inside the step's body.  Pressing one calls `act` and gets the same step back, redrawn.  An act never moves the step on |

Core checks the fields in an answer against the table in [Fields](#fields) when the answer is
made.  A field of any other type fails that call with a line naming the field and the types
that work.

**The run.**  `run` returns a message string (said, then the dialog closes), or a dict with an
optional `message` and `summary`, or a **job**.  For slow work, start a job and answer
`{"job_id": job.id}`: core answers `202`, hands the id to the Jobs view and closes the dialog,
and the Jobs view shows `job.progress` and the stop.

```python
def run(self, values):
    job = ctx.jobs.submit("import", lambda job: self.convert(job, values),
                          stoppable=True, keep_result=True)
    return {"job_id": job.id}
```

**stoppable.**  `ctx.jobs.submit(..., stoppable=True)` offers a stop link
(`POST /api/v1/ext/<name>/jobs/<kind>/stop`), and your work reads `job.halt` between units.  See
`ctx.jobs.submit`.

**keep_result.**  `keep_result=True` writes what the work returns to disk for the Jobs view, the
latest 20.

**Action words**, in the catalog: `action.<key>.label`, `action.<key>.description`, and
`action.<key>.result.<field>` for a count a run reports, beside the number.  A label or
description passed in code is shown as written.

## Over rows

A list opens a dialog from a verb's `action`, from the list's `add`, or from an act's
`{"action": ...}`.  The dialog is the same object whichever opens it.  When a verb opens it
over rows, the ids of those rows reach it two ways:

- `ask(step, rows)` gets them as a list of strings.  Name `rows` in the signature to receive
  them.  Every read of a step carries them.
- `check`, `act` and `run` find them in `values["rows"]`.  A field named `rows` is refused.

Treat the ids as what the client sent.  A verb's `needs` and `one` decide which verbs the
Manager offers, and the dialog is not re-checked against them: look each id up in your own
data before you act on it.

A dialog that `add` or an act opens has no rows, and `rows` is empty.

With `"each": True` on the verb, the dialog opens once for each row, so `rows` holds one id.
The person gets **Skip**, which leaves that row as it was, and **Stop**, which ends the run.
Both stand where Cancel would be, and the rows already done stay done.

The ids ride in the address of the dialog's first read, so one dialog covers about 1,500 rows.

## For a game

A dialog may be opened for a game rather than over rows, one the library may not hold yet.
An action with `menu=` is opened this way from a game's or a table's menu, once for each game
chosen.  The game reaches it as `who`, a dict of four strings: `game_id` (empty for a game not in the
library), `vps_id`, `name` and `table_id`.  `table_id` is the table a file is being saved for
where the person chose one, and is empty otherwise.

- `ask(step, rows, who)` gets it.  Name `who` in the signature to receive it.  The Manager
  sends it with every read of a step.
- `check(values, who)`, `act(key, values, who)` and `run(values, who)` get it the same way,
  beside `values` and not in it, so a field of your own may be named `who`.
- `who` is `{}` where the dialog was opened for no game.

`rows` and `who` are independent: a dialog may get either, both or neither.  Treat `who` as what
the client sent, as you treat the ids in `rows`: look the game up in your own data or with
`ctx.games` before you act on it.

## Fields

One vocabulary: a dialog's steps, the settings page and an account's panel use the same types
in the same controls.  A field is `{key, type, label, value, help}` plus what the type takes.
`help` takes the forms described above.  A dialog's field may add `shown`, `{follows, for}`:
the field is drawn only while the field named `follows` holds one of the values in `for`.  While
it is hidden its answer is sent empty, so a source that does not use a field never receives
what was typed there before.  A settings field's `label`, `help` and choices come
from the catalog (`settings.<key>.label`), so you declare a settings field with `{key, type,
default, choices}` and nothing to say.

| type | asks for | dialog | settings | account |
|---|---|---|---|---|
| `string` | a line of text | yes | yes | yes |
| `secret` | a masked line.  Shown empty, with *Set* or *Not set* in it; what is typed is kept across a dialog's steps and never filled back from an answer.  An extension answers `set` (true or false) and never the value; leaving it empty keeps the one held.  A settings page and an account also show *Clear* beside a held one, which asks first | yes | yes | yes |
| `path` | a file or folder on the device core runs on, with `wants`: `dir`, `file` or `exe`.  A dialog's may add `opens_at`, the folder its picker starts in while the field is empty | yes | yes | yes |
| `select` | one of `choices`, each `[key, label]` and optionally `[key, label, help]`, the help shown beside the option in the list, and under the field once it is chosen.  Labels within one field are different, since the help is found by label | yes | yes | yes |
| `multi` | several of `choices`.  A setting is stored as a JSON list of the keys selected, and `ctx.config.get_list(key)` reads it | yes | yes | yes |
| `switch` | on or off | yes | yes | yes |
| `number` | a number, with `unit`, `min` and `max`.  A setting is stored as its digits and, left empty, falls back to its default | yes | yes | yes |
| `choice` | one of `choices`, two to four, each `[key, label, help]` with its own line of help, and optionally a fourth element: the name of a picture, or a list of paths to a few of the person's own images, inside the folders the extension declared in `ctx.files.set_roots` | yes | no | no |
| `checklist` | any of `rows` selected, for a list too long for `multi`: each row `{key, name, note, groups}`, with `chips` as `[group, label]` pairs, a chip shown only when a row is in its group; the answer is the keys selected | yes | no | no |
| `settle` | a VPS entry for each of `rows`, `{key, name, entry, vps_id, reason, settled}`, `entry` being the `{name, manufacturer, year}` it would offer: the row says it or Not found, with the reason, and Change... opens the VPS picker; settled rows are behind Show all.  The answer maps a row's key to the id picked, with `""` for a row said to be in no catalog; a key left out is a row untouched | yes | no | no |

A `choice` option's picture is a name, and `null` is none.  Core knows `desktop` and `cabinet`
itself and reads any other name as a Material icon, so a flow never supplies a drawing.  Options
with pictures are tiles on a `welcome` step and rows on any other.  Beside other fields the
`choice`'s label heads its rows; alone on a step, its `title` is the question.

**A secret.**  On every surface, the value goes to the extension and is never returned: core
takes it out of every answer and puts `set` in its place.  Declare a setting as
`{"key": "api_key", "type": "secret"}`; a secret has no default.  A dialog's `check` and `run`
get the typed value under the key, and get no key at all where the person left it empty, so
`values.get("api_key")` being `None` means keep the one held.  *Clear* sends `null` for a
setting, and an account's `write` is handed `None` under the key.

A type that a surface does not offer is turned down when you declare it, not when somebody
opens the page.
