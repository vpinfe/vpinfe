# Good extensions

An extension that feels like part of VPinFE does a handful of things the same way core does.
[Conventions](../conventions.md) has the reasoning, and this page says what each convention comes
to for an extension and points to it.  It ends in a list to go through before you give one to
anybody.

## Words and marks

Every sentence a person reads is a key in `i18n/en.json`, asked for with `ctx.t`.  See
[Words a person reads](../conventions.md#words-a-person-reads) and the
[shapes core looks up on its own](reference/words.md).  Beyond that:

- **A label is the shortest true word.**  A state reads `Missing`, not "Not in this library".  The
  longer sentence goes in the tooltip, the key ending `.why` or `.help`.  See
  [Type](../conventions.md#type).
- **A fragment takes no full stop.**  A header, a state, a label, and a help line of one sentence
  have none.  A second sentence is what makes the first worth marking.
- **Labels and buttons take title case**: `Open the Site`, `Post a Score`.  Sentences and
  questions stay in sentence case.
- **A state is a word, and its tier says what the absence costs.**  `on` is present, `off` is
  absent and unremarkable, `warn` is absent and worth fixing, `bad` is absent and breaking.  See
  [Chips say what the absence costs](../conventions.md#chips-say-what-the-absence-costs).  A column
  where every row would carry a chip is text.
- **A name that has to read the same everywhere is `display_name`**, not a key.
- **A token is not a label.**  A row field holds `pending`, and the state's word is `Pending`.  See
  [A token is not a label](../conventions.md#a-token-is-not-a-label).

## Where an action sits

An action sits with what it acts on, and the declarations are how you say which that is.  See
[An action sits with what it acts on](../conventions.md#an-action-sits-with-what-it-acts-on).

| It acts on | Declare it as |
|---|---|
| Making a new thing | `add=` on the list, which puts the plus above it |
| The list or the whole extension | `acts`, in the menu at the end of the title |
| Rows | `verbs`, in the menu for a selection and for a row |
| A value | A field in a pane, edited in place |
| The extension's own page | A setting |

A verb has one word.  **Remove** takes it out and **Delete** means removed permanently, so name the one you
do.  A link that leaves the Manager is an address, and core marks it as leaving and opens it in a
new tab.  See [A link says whether it leaves](../conventions.md#a-link-says-whether-it-leaves).

## Danger, empty states and needs

- **A verb that cannot be taken back is `danger`.**  Core puts it last, in red, and asks first.  The
  question is yours, so make it say the consequence, and give it a singular and a plural.  See
  [One confirmation, and one field](../conventions.md#one-confirmation-and-one-field).
- **A verb that cannot work on a row says so with `needs`.**  It is dimmed, with the reason under it,
  so nobody wonders where it went.  The reason is a word you write.  Core checks it again when the
  verb is pressed.
- **A list with nothing in it returns an empty list.**  Core draws it.  Do not add a row that says
  there is nothing.
- **Offer an act only while it can work.**  `acts` is a function, so leave out an entry that would fail:
  with no key there is nothing to check, so there is no Check the Key.  A verb that only some rows can
  take is `needs`, not left out, because the rows differ and the person is looking at them.
- **A failure is a sentence and a reason.**  Raise `Unavailable` from a service that did not answer
  and word the cause with `ctx.why(exc, at=address)`.  Core says what could not be done and puts the
  reason under it.  The message never carries the exception's own text in a slot you wrote.  See
  [A failure leads with words](../conventions.md#a-failure-leads-with-words).

## Secrets

- **A key, token or password is a setting of type `secret`.**  Core never sends it to the page.  It is
  shown as set or not set, and an empty field keeps what is held.
- **Never echo it.**  Not in a log line, not in a message, not in an error.  `ctx.config.all()`
  returns the secret with everything else, so do not log what it returns.  See
  [Never log secrets](../conventions.md#never-log-secrets).
- **Never put it in an address.**  An address ends up in logs and histories.  Send it in a header, as
  the practice site expects, or in a body.
- **Ask for it only where it is used.**  A secret in a dialog is the same field as in a setting, and
  a person who has set it is not asked again.

## A picture, an address and a game

- **Give a picture's address and core serves the picture.**  A `picture` cell holds an address or a
  file in your folder, and core downloads it once, checks it, makes a copy at the size of a list and
  serves only that copy from VPinFE's own address.  The page never reaches a server of yours, so do not
  put a third-party address in anything you hand to a page.  A web address needs `net:outbound`.
  A picture has no tier of its own, so one in an extension with a page a Remote can open can be
  fetched by a Remote.  Show nothing private in a picture there.
- **A game or a table in a list is a `game` or `table` column, first.**  Give it the id and core draws
  the picture, the name, the maker and the link, as Games does.  It also relates each row to the
  library, adds the In Your Library view and lets a view rank a collection.  A name in a text column
  does none of that.
- **Say what a link reads as.**  A `link` cell is the address, and `name_from` names the row field that is the
  words.  Without it the link reads as the column's header.

## A page of your own

A thing that is more than a list, a pane, a dialog or a group has a page of its own, on a site and
a port of its own, and the Manager links to it.  See
[a page of your own](cookbook.md#17-a-page-of-your-own).

- **Anything that changes something goes behind its own sign-in.**  A page core serves under
  `/api/v1/ext/` is answered sandboxed, as no origin of its own, so it cannot carry a sign-in.  A
  page that can change what is on the device needs one.
- **Never ask to run as root.**  VPinFE does not run as root and an extension that wants it is wrong.
  A change that needs it is a fixed act of the operating system's own helper: `ctx.os.call`, from a
  function core calls on a person's press.  It is refused from an event handler, a timer or a thread,
  because those did not come from somebody pressing something.  See
  [asking the operating system to change something](cookbook.md#20-asking-the-operating-system-to-change-something).

## Dependencies

A dependency is a package from pypi.org.  It runs inside VPinFE with everything the
extension was trusted to do.  See [Dependencies](reference/manifest.md#dependencies).

- **Reach for the standard library first.**  Each dependency is something a person is asked to
  trust, and VPinFE downloads it onto their device.
- **List what you import, and what it imports.**  Exact versions, the whole set, nothing VPinFE
  already ships.
- **Prefer a pure-Python dependency.**  A compiled one is built for one system and one Python, so
  it may be missing for a device and the extension then waits with a reason.
- **Test it with nothing else installed.**  A device holds only what the manifest lists.

## Before you ship

- [ ] Every sentence is a key in `i18n/en.json`, and `./vpinfe --check-extension` reports no
  `missing` line.
- [ ] A label is the shortest true word in title case, and a fragment has no full stop.
- [ ] A column whose header does not say it all has a `help`, so its tooltip adds what the header cannot.
- [ ] An action sits where its declaration puts it: `add`, `acts` or `verbs`.
- [ ] Anything that cannot be undone is `danger`, and its question says what is lost.
- [ ] A verb that is not for every row has `needs` and a reason.
- [ ] An act that cannot work now is left out of `acts`.
- [ ] A secret is `secret`, never logged, never in an address.
- [ ] A picture is an address or a file in the folder, and `net:outbound` is declared if it is a web
  address.
- [ ] A game or a table in a list is the identifier column and a `game` or `table` kind.
- [ ] A failure is raised with `ctx.why`, and a service that did not answer raises `Unavailable`.
- [ ] A Private game is never sent anywhere.  See [Private games never
  leave](how-it-works.md#private-games-never-leave).
- [ ] The manifest asks for what is used and nothing more, and the README says why for each.
- [ ] The README is written for the person running the extension: what it does, how to start,
  each setting and action, the common problems and where to ask.  `--check-extension` has no
  `warns` line.
- [ ] Nothing asks to run as root, and a change to the device goes through `ctx.os`.
- [ ] `display_name`, `version` and `requires_platform` are set, and `platforms` is there if it
  runs on one system.
- [ ] Every package it imports is listed with an exact version, and `--check-extension` reports
  no import it cannot place.
