# Official and unofficial extensions

An extension runs inside VPinFE, so installing one is trusting it. We tell people which ones we
stand behind. This page says what that means, how an extension gets on our list, what takes it
off, and what an extension can never do about it.

- [What official means](#what-official-means)
- [How an extension gets on the list](#how-an-extension-gets-on-the-list)
- [The rules](#the-rules)
- [What takes an extension off](#what-takes-an-extension-off)
- [What an unofficial extension can never do](#what-an-unofficial-extension-can-never-do)
- [Putting your extension on the list](#putting-your-extension-on-the-list)

## What official means

An extension is **official** when we put it there: it came with VPinFE, or VPinFE installed it
from the Extension List and its files are still the ones VPinFE wrote. An official extension runs
with no question asked and carries no mark.

Every other extension is **unofficial**. That includes one a person copied into their extensions
folder, one whose files were edited after VPinFE installed it, and one that has left the list.
An unofficial extension does not start until the person has said yes to it, and it carries a
mark wherever its name appears. [How it works](how-it-works.md#the-unofficial-mark) lists where.

Official does not mean reviewed line by line. It means we know where the extension comes from,
that it meets [the rules](#the-rules), and that what runs is what we listed.

## How an extension gets on the list

The Extension List is a file in the `vpinfe/extensions` repository. Each entry is a name, the
address of a public repository and, optionally, a `ref` (a tag or a branch) and `min_vpinfe`, the
oldest VPinFE it works with:

```json
{
  "extensions": [
    {"name": "example", "url": "https://github.com/example-author/extension-example",
     "ref": "v1.2.0", "min_vpinfe": "3.0.0"}
  ]
}
```

The entry does not say anything else about the extension. Its version, display name, author and
description are read from its own `extension.json` at that `ref`, so the repository stays the one
place an author edits. The name in the entry has to be the name in the manifest, or the entry is
ignored.

VPinFE reads the list on the Extensions page of System, and installs what a person picks into a
folder of its own, separate from the one people put their own extensions in. It keeps a record of
what it installed: the repository, the release, and a fingerprint of every file. At each start it
checks the files against the record. A device with no connection keeps the last list it read.

An entry is added by a pull request to `vpinfe/extensions`, and both maintainers approve it.

## The rules

An extension is listed when it meets all of these:

1. **Tables, media and other people's work come only from where their creators publish them, on
   those sites' terms.** Nothing acquires or re-shares them in bulk unless the site that
   publishes them offers them that way.
2. **It has its own public repository with a named maintainer.** Ours live in
   `vpinfe/extension-<name>`. An extension from another author can stay in the author's
   repository, and the list points at it.
3. **It uses only the documented extension interface and declares what it can do.** That is
   `common.extensions.contract` and the manifest's `scopes` and `capabilities`.
4. **It works on every system VPinFE runs on, or says which it does not.** The manifest's
   `platforms` is how it says so. Every dependency it lists has a download for each system it
   claims, or it leaves that system out.
5. **Its license lets us keep it working if its maintainer leaves.**

These are written down so that a "no" is about the rule and not the person.

## What takes an extension off

We take an extension off when it stops meeting the rules above: it changes what it does so that
rule 1 or 3 no longer holds, it is abandoned and rule 5 is all that is left, or its repository
goes private or away.

What a person sees when that happens:

- At the next time VPinFE reads the list, an installed extension that is no longer on it shows as
  unofficial.
- At the next start it is off, and Needs You says it left the Extension List. The person can
  **Trust and Turn On** it, as with any unofficial extension, or uninstall it.
- A device that is offline keeps the last list it read, so nothing changes for it until it reads
  again.

An author who wants off the list asks in the same repository, and the entry is removed the same
way it was added.

## What an unofficial extension can never do

The mark, the question and the checks come from VPinFE's own files, not from the extension, so an
extension cannot change them:

- **Claim to be official.** An extension is official because of the list and the record VPinFE
  keeps, never because of what its manifest says. One that names VPinFE as its author, or one of
  our repositories as its source, is not shown as ours.
- **Take the place of an official one.** An extension with the name of one we list does not
  replace it until a person trusts it, and the official copy keeps running meanwhile.
- **Start without a yes.** Until a person trusts it, an unofficial extension does not start.
  Trusting is kept in a file of its own that no settings write or restored backup can change,
  and an update that declares more access stops and asks again.
- **Remove or restyle the notices.** The question, the mark and the Needs You rows are drawn by
  core.
- **Hide from a report.** A Support Package is not made while an unofficial extension is running,
  because it could have changed what is being reported. The person can turn unofficial
  extensions off and restart from the same place, and the package opens again by itself. Once it
  is made, the package names each unofficial extension, whether it is on or off, and its
  repository, release and fingerprint.
- **Look unchanged after it was edited.** An official extension whose files no longer match
  what VPinFE installed stops until it is reinstalled or trusted as an unofficial one.

An extension runs Python in VPinFE's process, so none of this stops one a person has trusted from
doing harm. The notices exist so that the person decides knowing that.

## Putting your extension on the list

1. Read [Good extensions](good-extensions.md) and run `./vpinfe --check-extension` on it.
2. Put it in a public repository with its `extension.json` at the top, a license, and a tag for
   each release. The manifest's `version` is what people see as the version.
3. Open a pull request to `vpinfe/extensions` that adds an entry to `extension_list.json`.
4. Say in the pull request how it meets each of [the rules](#the-rules).
