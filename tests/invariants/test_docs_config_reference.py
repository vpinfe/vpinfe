"""The settings reference in the docs is the schema, not a copy of it that drifts.

`docs/technical-details.md` had gone stale in four directions at once: it named the file
`vpinfe.ini` after the move to JSON, listed sections and keys under names three renames
had replaced, and documented the pre-3.0 input vocabulary. Nobody was wrong to miss it -
a rename touches the schema and a reader would have to know the doc restates it.

So the reference is generated, and this fails when the file and the schema disagree.
Regenerate with `python3 -c "from tests.support import config_reference as r; r.write()"`.
"""

from __future__ import annotations

import unittest

from common.config import config_schema
from tests.support import config_reference


class ReferenceTests(unittest.TestCase):
    def test_the_doc_matches_the_schema(self) -> None:
        self.assertEqual(
            config_reference.current(), config_reference.render(),
            "docs/technical-details.md is behind common/config/config_schema.py - regenerate it "
            'with: python3 -c "from tests.support import config_reference as r; r.write()"')

    def test_every_setting_appears(self) -> None:
        """The generator could render an empty table and still match itself."""
        rendered = config_reference.render()
        missing = [f"{o.section}.{o.key}" for o in config_schema.options()
                   if f"`{o.key}`" not in rendered]
        self.assertEqual(missing, [])
        for section in {o.section for o in config_schema.options()}:
            self.assertIn(f"### `{section}`", rendered)

    def test_the_document_still_has_its_hand_written_half(self) -> None:
        """The generator owns one span and must not eat the rest of the file."""
        text = config_reference.DOC.read_text(encoding="utf-8")
        self.assertIn(config_reference.END_MARKER, text)
        self.assertIn("run_time_seconds", text, "the .info reference should survive")

    def test_it_documents_the_file_we_actually_write(self) -> None:
        """The specific staleness this replaced. The ini is still named, because it is
        still read once and kept - what changed is which file this describes."""
        text = config_reference.current()
        self.assertIn("## vpinfe.json Definition", text)
        for line in text.splitlines():
            if line.startswith(("- **Linux**", "- **macOS**", "- **Windows**")):
                self.assertTrue(line.rstrip().endswith("vpinfe.json`"), line)


if __name__ == "__main__":
    unittest.main()
