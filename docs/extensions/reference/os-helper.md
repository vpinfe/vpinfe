# The operating system's helper

`ctx.os` asks a helper that the operating system ships.  VPinFE never runs as root, so what
needs root is a short list of fixed methods, written by the system's maintainers and run by a
service of theirs.  This page is for them: what VPinFE sends, and what it expects back.  An
extension's author wants [`ctx.os`](context.md#ctxos).

## The socket

The helper listens on a Unix socket at `/run/vpinos/helper.sock`.  VPinFE does not send
anything to find out whether it is there: it checks that the socket exists, so a helper that a
connection starts is not started by a question about whether it is installed.  Where there is
no socket, `ctx.os.available` is false and a call raises `Unavailable`.

The helper decides who may ask from the socket's peer credentials (`SO_PEERCRED`) and answers
only the user VPinFE runs as.  Nothing in a request is a basis for permission.

VPinFE gives up on a helper that has not finished its answer in the time for the method, however
slowly the answer comes, and treats an answer it cannot read as no answer.  That is JSON that
does not parse, nests too deeply, holds a number JSON has no word for, or is not an object with
an `ok` of true or false.

## The wire

One request and one answer, each a single line of JSON ending in a newline, then the
connection closes.  A line is at most 65536 bytes.

```json
{"method": "updates.apply", "args": {}, "by": "os_update", "origin": "this device"}
{"ok": true, "result": {"installed": 12, "reboot_required": true, "vpinfe_upgraded": false}}
```

| key | what it holds |
|---|---|
| `method` | One of the methods below |
| `args` | An object holding only the arguments that method takes, none today |
| `by` | The extension that asked, for the journal |
| `origin` | `this device` or the address the request came from, for the journal |

An answer of no has the same shape:

```json
{"ok": false, "error": "busy", "message": "An update is already running"}
```

`message` is a sentence a person reads.  VPinFE cuts it to 200 characters and removes anything
that does not print, since it comes from a process VPinFE does not control.  A change takes as
long as it takes, and VPinFE waits up to the time in the table below before it gives up.

## The methods

| method | changes the device | takes | answers | waits up to |
|---|---|---|---|---|
| `updates.check` | No | nothing | `{"count": n}`, the packages an update would install | 2 minutes |
| `updates.apply` | Yes | nothing | `{"installed": n, "reboot_required": bool, "vpinfe_upgraded": bool}` | 1 hour |

`updates.apply` answers how many packages it installed, whether the device needs a reboot
for them (`/run/reboot-required` exists) and whether VPinFE itself was upgraded, which needs a
restart of VPinFE and no more.  VPinFE hands the answer on as the helper sent it, and an
extension reads each flag as true only when it is `true`.

Both use every package source the system has configured, under the system's own pins, and
nothing else.  Every method has an area an extension declares as the capability `os:<area>`:
both of these belong to `os:updates`.

## What a method may be

- Each takes a closed set of arguments, each checked against a closed list or a pattern, and
  never handed to a shell.
- None takes a command, a path, a package name or an address.
- Every call goes to the journal with the method, who asked and the outcome, never a secret.
- None writes a file the caller names, installs a package the caller names, or changes users,
  passwords, sudo or polkit rules.

A method that did not fit these would be a way to run anything, and an extension that means
harm runs as the same user that can open the socket.  The helper's list is what bounds it, so
the list stays short.

Adding a method means adding it to `METHODS` in `common/host/os_helper.py` with its area, whether
it changes the device and how long it may take, and giving it a sentence in the catalog under
`os.method.<name>.says`.  A new area also means a capability in `common/extensions/contract.py`
and its words on the install screen.
