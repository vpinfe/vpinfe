# Words

An extension keeps what it says in `i18n/en.json` beside its code.  A translation is the same
file under the language's name, `i18n/de.json`.  Core serves the file under `ext.<name>.`, so
an extension adds words and never changes one of core's.  Keys are flat strings with dots in
them.

| key | names | falls back to |
|---|---|---|
| `name`, `description` | The extension, where the manifest leaves them out | The extension's `name`; nothing |
| `settings.label` | The heading over its settings | The Manager's own |
| `settings.<key>.label` | A setting | The key |
| `settings.<key>.help` | A line under the setting; `{default}` is its default | Nothing |
| `settings.<key>.choice.<value>` | A select option | The label given in `choices` |
| `list.<key>.title` | A list's title | The key |
| `list.<key>.column.<field>.header` | A column's header | The field |
| `list.<key>.column.<field>.help` | A column's tooltip | Nothing |
| `list.<key>.view.<viewkey>.name` | A view's name | The view's key |
| `list.<key>.view.<viewkey>.help` | A view's tooltip | Nothing |
| `list.<key>.act.<act>.label` | An act in a list's title menu | The act's key |
| `list.<key>.verb.<verb>.label`, `.tip`, `.confirm`, `.needs` | A verb: its label, its hover, the question a danger verb asks, why it is dimmed | The verb's key; nothing; core's question; nothing |
| `page.<key>.title` | A page's title, in the rail and over the page | The extension's name |
| `page.<key>.act.<act>.label` | An act in a page's title menu | The act's key |
| `group.<subject>.title` | The heading over a group on a game's or a table's panel | The extension's name |
| `group.<subject>.act.<act>.label` | A button in the strip closing a group | The act's key |
| `verb.<subject>.<key>.label`, `.tip`, `.confirm` | An entry in the Games or Tables menu: its label, its hover, the question a danger entry asks | The key; nothing; core's question |
| `need.<kind>.label`, `need.<kind>.says` | A row asking for something on Overview: its line, given `count`, and the line under it | The extension's name; nothing |
| `link.<key>.label`, `link.<key>.tip` | A link to the extension's own page: its text and its hover | The key; nothing |
| `action.<key>.label`, `action.<key>.description` | An action, and the button above a list that adds with it | The key; nothing |
| `action.<key>.result.<field>` | A count a run reports, beside the number | The field |
| `account.label` | The heading over a player's account with it | The extension's name |
| `account.share.help` | What its account's Share makes of what is played, under the switch | The Manager's own naming |
| `token.<name>.says` | What a name a command can use stands for | A name with none is turned down |
| `app.<id>.name`, `app.<id>.field.<key>.label`, `app.<id>.field.<key>.description` | An app it provides | Core's words for a field it does not name |

Anything else is yours to ask for with `ctx.t("wizard.title")`.  `contract.words(name)` does the
same for a module that is not handed `ctx`.  A key the file does not have answers the full key,
`ext.<name>.<key>`, so a gap shows.

Give a column a `help` when its header does not say it all.  Without one the column has no
tooltip, which is right when the header does.  A word you pass in code, like `title="VPinPlay"`, is shown as
written in every language, which suits a product name.

**Slots.**  `{name}` in a value is filled from the call's keyword arguments with `format_map`.

**Plurals.**  A value that is an object `{"one": "...", "other": "..."}` is chosen by the `count`
argument.  `ja`, `zh`, `ko`, `vi` and `th` always use `other`.

```json
{"sent": {"one": "{count} score sent", "other": "{count} scores sent"}}
```

`scripts/i18n.py` reads a bundled extension's file along with core's, and `--record` and
`--pseudo` write its hashes and pseudo-locale.  The invariants hold a bundled extension's file
to what its code asks for, both ways, and to the rules under "Type" in `docs/conventions.md`.
They fail on a sentence written anywhere in its code outside a docstring, a log line, a query
or a builtin exception.  What a `ValueError` says reaches a log and nobody else.  What a
setting's `check` or a function core calls raises is read by a person, so it comes from
`ctx.t`, or from `ctx.why` when it is the reason something failed.

**A failure with its reason.**  A note or a row's error carries a reason the same way:
`{"text": ctx.t(...), "detail": ctx.why(exc)}`.
