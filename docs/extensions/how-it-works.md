# How it works

The lessons in [Learn](tutorial.md) say what core did for you, a paragraph at a time.  This page
collects those ideas, so that each is in one place.  Every section says which lesson meets it
first.  For the exact shape of a call, see the [Reference](reference.md).

- [What core draws and what you supply](#what-core-draws-and-what-you-supply)
- [One process, so trusting it is a decision](#one-process-so-trusting-it-is-a-decision)
- [The Unofficial mark](#the-unofficial-mark)
- [Scopes and tiers](#scopes-and-tiers)
- [Private games never leave](#private-games-never-leave)
- [Load, off and on again, and reload](#load-off-and-on-again-and-reload)
- [Words and languages](#words-and-languages)
- [Where an extension's data lives](#where-an-extensions-data-lives)

## What core draws and what you supply

First met in [lesson 3](tutorial.md#lesson-3-a-list-in-community).

An extension declares what it has, and core draws it.  A list is a function that returns rows and
a description of its columns.  A dialog is an object that says what to ask and what to do.  A
pane is a function that returns facts and fields.  Core draws the page, the grid, the menu, the
dialog, the pane and the group on a game's panel, as it draws its own.

What you supply is a thing that cannot be known without you:

- **The data.**  Rows, facts, the answer to a step.
- **The words.**  Every sentence a person reads, in a catalog.
- **What a thing is.**  A column's kind says whether a value is a date, a state or a game.  Core
  chooses how a date, a state or a game looks.
- **What can be done to what.**  A verb says which rows it is for with `needs`, and which are
  dangerous with `danger`.

What core supplies is everything that would otherwise differ from page to page: the layout, the
colors, the spacing, where an action sits, how a danger is asked, what an empty list says, what a
failure looks like and how a picture is chosen.  The result is an extension that looks and behaves like VPinFE without having asked to,
and that keeps doing so when the Manager changes.

It also means there is nothing to style.  A thing that does not fit one of the declarations is
a page of its own, reached by a link (see
[a page of your own](cookbook.md#17-a-page-of-your-own)).

## One process, so trusting it is a decision

First met in [lesson 10](tutorial.md#lesson-10-shipping-it).

An extension is Python that VPinFE imports and runs in its own process, as the person who runs
VPinFE.  Whatever Python in that process can do, the extension can do: open a file, open a socket,
start a program.

That is why putting a folder in `extensions/` is a decision, and why VPinFE asks an owner to
trust an extension and does not ask an extension to prove itself.  Everything the extension
declares, its scopes and capabilities, is a statement it makes about itself, for the person who
installs it to read.  Core holds the calls that go through `ctx` to what is declared, and the
rest is trust.  See [scopes and tiers](#scopes-and-tiers) for what is held and what is not.

What an extension declares may not be what it does.  The list a person reads when they trust it
is the author's word, VPinFE cannot check it against the code, and an extension that declares
little can still do a lot.  Run only extensions you trust, and treat the list as a claim to
weigh and not as a limit that holds.

For an extension author this means two things.  Ask for what you need and nothing more, because
the list is what a person reads before they decide.  And write what a person cannot see in the
page, in the README.

## The Unofficial mark

First met in [lesson 10](tutorial.md#lesson-10-shipping-it).

VPinFE tells a person which extensions it did not ship.  An extension VPinFE ships, such as
VPinPlay or the library importer, carries no mark.  Every other extension is marked Unofficial,
including one in the extensions folder that has the name of one VPinFE ships:

- **Before it runs.**  A prompt, "Trust an Unofficial Extension?", and a row under Needs You on
  Overview while it waits.  Until an owner trusts it, it does not start.
- **In Settings, under Extensions.**  A chip beside its name, Not Trusted until trusted and
  Unofficial after, and the page's list of what it reaches reads "Reported Capabilities".  The
  page lists the author, the version, the source and the folder it was found in above it.  The
  chip's tooltip says "Not included with VPinFE.  Extensions can run programs on this device."
- **On its own pages.**  A page's header says "From {Extension}".  It carries no
  status of its own, because the rail entry that led there already did.
- **Wherever its name stands for what it added.**  A small grey shield, with the tooltip
  "Unofficial, from {Extension}".  It follows the name of its entry in the navigation rail (on
  the icon's corner when the rail is collapsed), and it leads the name over its entries in a
  game's or a table's menu, over a group it adds to a game's panel and over its rows in Needs
  You.  The chip carries it too.  A link that leaves keeps its arrow and takes no shield.

What trusting is kept in:

- **A list of its own.**  `accepted.json` in the config folder, readable by the user VPinFE runs
  as and no one else.  It is not `vpinfe.json`, so a settings write, a restored backup or a hand
  edit of the settings cannot trust anything.
- **Per extension, by what it said it used.**  An update from the same folder that declares the
  same access runs without asking again.  One that declares more stops, and the prompt, "Trust
  New Access for “{Extension}”?", names only what is new.
- **A replacement waits.**  An extension in the folder named like one VPinFE ships does not take
  its place until it is trusted.  The built-in keeps running meanwhile, and after the owner
  trusts it, the replacement starts at the next restart.
- **Never carried over.**  Extensions are new, so nothing is trusted on upgrade.

A theme works the same way, by the address it came from: a theme from the VPinFE Theme List needs
nothing, and one from another source or found in the themes folder is Unofficial and the frontend
plays the default theme until it is trusted.  A theme installed before this existed counts as
trusted.

An extension author does not do anything for it: the mark and the prompt come from core, and an
extension cannot remove, move or restyle them.  Write the README as if the prompt were there,
because the person reading it will be.  `--check-extension` loads the extension without asking,
because it exists to look at one that is not trusted.

## Scopes and tiers

First met in [lesson 9](tutorial.md#lesson-9-a-group-on-a-games-panel).

There are three different things here, and they are easy to run together.

**What the extension asks to use.**  The manifest's `scopes` are the parts of VPinFE it reads or
changes, such as `games:read` for the library.  They decide which `ctx.games`, `ctx.players` and
similar calls work, and the extension's page lists them in words.  Something that needs a scope
the manifest does not declare is refused, and the message names the scope.

**What the extension asks of the device.**  The manifest's `capabilities`: `ui:mount` for any
page it adds, `config:own` for its settings, `net:outbound` for the network and so on.  Core
enforces the ones that go through `ctx`.  The rest are advisory, because Python in the same
process can always do them: they say what the extension intends, so a person can see it, and
they become a boundary only when extensions run out of process.

**Who may reach what the extension serves.**  Everything an extension serves sits at a
*tier*: Play for what a Remote pass may reach, Library by default, Protected for an act that
changes the device.  Core puts the gate on every route.  You never attach one, so you cannot
ship a route without it.

Play is the one the Owner is asked about.  The manifest's `play` names each surface a Remote can
open, the install page shows the names under "A Remote Can Open", and a surface put at Play that is
not named fails to load.  An update that adds one asks the Owner again.

A picture in an extension that has a page a Remote can open can be fetched by a Remote, so show
nothing private there.  Pictures are served at their own address and carry no tier of their own.

A request has a tier too: how far it got.  A request that changes the device, such as
installing updates through `ctx.os`, is refused unless it reached Protected, which an Owner
reaches with a Confirm and a token never does.  Core marks each request with its tier, lowered to the tier the act is declared at, and the
mark follows the request into the function it calls.  A Library act pressed by an Owner who has
confirmed is still a Library act, so only an act declared Protected can change the device.  An event
handler, a timer or a thread the extension started has none, so a change from any of them is
refused.  It is how VPinFE keeps an act deliberate: a person pressed something.  It is not a lock
on the device.  See [`ctx.os`](reference/context.md#ctxos).

The full list of scopes and capabilities is on [Scopes and the gate](reference/scopes.md).

## Private games never leave

First met in [lesson 9](tutorial.md#lesson-9-a-group-on-a-games-panel).

Somebody can mark a game Private.  From then on its plays, its time, its scores and its rating
are not sent anywhere, and neither is the fact that the library holds it: not to a service, not
for a player, whoever was up and whatever their Share says.

For an extension that comes to these rules:

- **A Private game is never asked about.**  Core does not call a group's `read` or `save` for
  it, so the extension is not told the library holds the game.  A group declared with
  `private_ok=True` is asked, and `who` carries `private`.  `ctx.games` is not filtered, and
  lists every game, Private ones included.
- **What reaches you says whether a game is Private.**  An event carries `private`.  A row from
  `ctx.games` does not, so an extension that sends games outside VPinFE keys off the event.
- **An entry in the Games or Tables menu, or a panel group, can say it works on Private
  games.**  With `private_ok=True` core hands it Private games too, each marked `private`, for
  one that curates the library and sends nothing out.  Without it a Private game is left out.
- **You can read the flag and never set it.**  There is no `set_private`.

The practice site in the lessons is a service, and the handler in lesson 9 that logs a play
returns before it does anything when `private` is true.  An extension that posts to a real service
needs that line more than the example does.  See
[the events](reference/offers.md#what-a-game-tells-you).

## Load, off and on again, and reload

First met in [lesson 1](tutorial.md#lesson-1-an-extension-that-loads).

**Load.**  When VPinFE starts it reads each manifest, imports each package and calls `register`
once.  `register` is a moment: after it returns, anything that still needs declaring, a router
for one, is refused.  Anything that goes wrong in an extension's code costs that extension and
no other, and its page under Settings says why.

**Off and on again.**  An owner can switch an extension off on its page.  Off is at once: it is
taken out the way a broken one is, and says it was switched off.  On is at once for an extension
that declares only through `ctx.ui`, and its code is read again.  An extension with routers of
its own waits for a restart, because routes mount once.  Switching off and on is how you see a
change to your code without restarting everything.

**Reload.**  There is no reload button.  What you changed decides what to do:

| You changed | To see it |
|---|---|
| The extension's code | Switch it off and on |
| A word in `i18n/en.json` | Restart VPinFE |
| The manifest | Restart VPinFE |
| A new extension folder | Restart VPinFE |

**What survives.**  An extension's settings are in a file of their own, so they survive all of
these.  Anything the extension holds in memory is lost each time it is loaded: after off and on,
and after a restart.  If it matters, keep it in `ctx.config`.

A failure that happened once is not written down.  An extension that failed or was taken out
for the run starts again the next time VPinFE does.  A switch that an owner turned off stays off.

## Words and languages

First met in [lesson 1](tutorial.md#lesson-1-an-extension-that-loads).

Nothing in an extension's code is a sentence a person reads.  Every word is in `i18n/en.json`
beside the code, and the code asks for it by key: `ctx.t("log.started")`.  Core serves the file
under `ext.<name>.`, so an extension adds words and never changes one of core's.

A translation is the same file under the language's name, `i18n/de.json`.  A key a
language lacks is said in English, and a key English lacks answers the full key, so a gap shows.

Most keys are not asked for in code.  A setting's label, a column's header, a view's name, a
verb's confirmation and a group's title are keys that core looks up by their shape, so declaring
the thing is enough to need the word.  `./vpinfe --check-extension` names each that is missing.
The table of shapes is [Words](reference/words.md).

A number in a sentence is a slot, `{count}`, and a sentence with a count is a pair, one and
other, so it reads right for one score and for three.  A name that must read the same in every
language, a product name, is `display_name` in the manifest and is not in the catalog.

## Where an extension's data lives

First met in [lesson 2](tutorial.md#lesson-2-settings).

An extension has one place for what it keeps: `extension_settings/<name>.json` in the config
directory.  `ctx.config` reads and writes it.  The settings the extension declared are there, and
so is anything else it chose to `set`, such as lesson 8's note for each score.  Players' accounts
with the extension and what core remembers it declared are kept there too, under names an
extension cannot use for a setting.

- **It is a file of its own.**  One extension's bad file costs that extension and no other.
- **Every value is a string.**  A switch is `"true"` or `"false"`, a number is its digits and a
  list is JSON.
- **A secret is stored as it is typed.**  The page never shows it again, only that it is set.
  `ctx.config.all()` returns it, so do not log it.
- **VPinFE's own file is not yours.**  `vpinfe.json` holds what core is configured with, and only
  the names of the extensions that are switched off are recorded there.

An owner can see and edit a setting on the extension's page.  Data the extension keeps for its
own reasons, such as lesson 8's notes, is not on the page.  If a person ought to be able to see
it, put it in a pane, as lesson 8 does.
