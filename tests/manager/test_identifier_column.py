"""Every grid marks the one column its rows are scanned by."""

from __future__ import annotations

import unittest

from manager.kit import appearance, grid, thumbnails


def _columns(marked: int) -> list[dict]:
    """A column set with `marked` identifier columns in it."""
    built = [grid.column("plain", "Plain"), grid.column("other", "Other")]
    for index in range(marked):
        built.insert(0, grid.identifier(f"id{index}", f"Id {index}"))
    return built


class IdentifierColumnIsMarked(unittest.TestCase):

    def test_the_marker_puts_the_class_on_the_cell(self):
        definition = grid.identifier("name", "Name")
        self.assertIn(grid.IDENTIFIER_CLASS, definition["cellClass"])
        self.assertEqual("name", definition["field"])

    def test_the_marker_keeps_a_cell_class_the_column_already_had(self):
        definition = grid.identifier("name", "Name", cellClass="manager-stars-cell")
        self.assertIn("manager-stars-cell", definition["cellClass"])
        self.assertIn(grid.IDENTIFIER_CLASS, definition["cellClass"])

    def test_a_plain_column_is_not_marked(self):
        self.assertNotIn(grid.IDENTIFIER_CLASS,
                         str(grid.column("year", "Year").get("cellClass") or ""))


class APictureLeadsTheCell(unittest.TestCase):

    def test_the_drawing_reads_the_field_it_was_given(self):
        definition = grid.identifier("name", "Theme", subtitle="said", picture="preview",
                                     frame=thumbnails.PREVIEW)
        self.assertIn(grid.PICTURED_CLASS, definition["cellClass"])
        self.assertIn("d['preview']", definition[":cellRenderer"])

    def test_the_list_frame_keeps_the_two_line_row(self):
        listed = [grid.identifier("name", "Game", subtitle="said", picture=thumbnails.FIELD)]
        self.assertEqual(grid.base_row_px(listed), grid.TWO_LINE_ROW_PX)

    def test_without_one_nothing_is_drawn_ahead(self):
        definition = grid.identifier("name", "Game", subtitle="said")
        self.assertNotIn(grid.PICTURED_CLASS, definition["cellClass"])
        self.assertIn("const art = null;", definition[":cellRenderer"])

    def test_the_row_is_as_tall_as_the_stylesheet_says(self):
        pictured = [grid.identifier("name", "Theme", subtitle="said", picture="preview",
                                    frame=thumbnails.PREVIEW)]
        self.assertEqual(grid.base_row_px(pictured), grid.PICTURED_ROW_PX)
        self.assertIn(f".manager-grid-pictured {{ --ag-row-height: {grid.PICTURED_ROW_PX}px; }}",
                      appearance.base_css())


class EveryGridDeclaresExactlyOne(unittest.TestCase):

    def _refusal(self, columns: list[dict]) -> str:
        with self.assertRaises(ValueError) as caught:
            grid.build(columns, [], "manager.test.columns")
        return str(caught.exception)

    def test_none_is_refused(self):
        message = self._refusal(_columns(0))
        self.assertIn("manager.test.columns", message)
        self.assertIn("declares 0", message)

    def test_two_is_refused_and_names_them(self):
        message = self._refusal(_columns(2))
        self.assertIn("declares 2", message)
        self.assertIn("id0", message)
        self.assertIn("id1", message)


if __name__ == "__main__":
    unittest.main()
