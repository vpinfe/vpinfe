# Testing your extension

An extension's tests live in the extension, in a `tests` folder beside its code, and go
wherever the extension goes: they are in its repository and out of what VPinFE installs. We
run them in a VPinFE checkout, because most of what an extension does happens inside VPinFE.

- [Set it up](#set-it-up)
- [Write a test](#write-a-test)
- [Run the tests](#run-the-tests)
- [What else runs over your extension](#what-else-runs-over-your-extension)
- [Run them on every push](#run-them-on-every-push)

## Set it up

1. Get a VPinFE checkout and its development dependencies:

   ```bash
   pip install -r requirements.txt -r requirements-dev.txt
   ```

2. Put your extension in the checkout's `extensions/` folder, as a clone or as a link to the
   folder you work in. The folder name is the extension's `name` from its manifest.

   ```bash
   ln -s /path/to/extension-example extensions/example
   ```

   `extensions/` is not part of VPinFE's repository, so what you put there is never committed
   to it.

3. Give `tests` an `__init__.py`, so the runner can import it as
   `extensions.example.tests`.

## Write a test

A test is a `unittest` module named `test_*.py` in `tests/`. It imports what it needs from
VPinFE and from the extension:

```python
"""Protects: (c) a score of zero is sent as zero, not left out."""

import unittest

from extensions.example import client


class ScoreTests(unittest.TestCase):
    def test_a_zero_score_is_sent(self) -> None:
        self.assertEqual({"score": 0}, client.payload(0))
```

The first line of the docstring says which of these the module guards, so the suite does not
grow by habit:

| | A failure of this test would |
|---|---|
| (a) | lose or damage a person's data or files |
| (b) | break a promise to a theme, an extension or an API consumer |
| (c) | bring back a bug that already happened |
| (d) | break first run, launch or play |
| (e) | behave differently on another system |

A test that fits none of these is not written. How a page looks and what it says are checked
by looking at the page.

Keep sample data next to the test, in `tests/fixtures/`, and find it from the file, not from
the working directory:

```python
HERE = Path(__file__).resolve().parent
```

`extensions/example` may be a link, so a path made from the checkout's root will not find your
files. Work out your own root from `__file__`.

## Run the tests

From the root of the VPinFE checkout:

```bash
python -m tests extensions/example/tests
```

One file works too:

```bash
python -m tests extensions/example/tests/test_client.py
```

A run of the whole suite, `python -m tests`, includes the tests of every extension in
`extensions/`.

A test that needs a stand-in for an extension, and not yours, can use
`tests/fixtures/extensions/sample`, which is a small extension kept for that.

## What else runs over your extension

The same checks VPinFE holds its own code to apply to yours. The ones that read every
extension are in `tests/invariants`, and a run of both shows them together:

```bash
python -m tests tests/invariants extensions/example/tests
```

Among them are the rules for wording and for the word catalog, and the `Protects:` line on
each test module. The rule that an extension imports `common.extensions.contract` and nothing
else of VPinFE is checked by `tests/extensions/test_import_boundary.py`, which a whole run
includes.

Code is checked with the tools VPinFE uses:

```bash
ruff check extensions/example
mypy --follow-imports=silent extensions/example
```

The tests of an extension are checked by these two as well, though a test function does not
need type annotations.

## Run them on every push

[The official extensions](official-extensions.md) each carry a workflow in
`.github/workflows/tests.yml` that you can copy. It checks out VPinFE's development branch,
puts the extension in its `extensions/` folder, and runs the three commands above on Linux,
macOS and Windows. Change the extension's name in it and it is yours.

`tests/` and `.github/` are left out when VPinFE installs an extension, so neither adds to
what a person has on their device.
