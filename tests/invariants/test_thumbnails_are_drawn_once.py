"""A picture beside a name is drawn by `manager/kit/thumbnails.py` and nowhere else.

Every list shows one frame: one size, one ground, the row's own glyph where there is no
picture. The address inside it is `manager/kit/art.py`'s, which
`test_art_addresses_go_through_one_helper.py` holds to; this holds the frame.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANAGER = REPO / "manager"
HELPER = MANAGER / "kit" / "thumbnails.py"

# The frame's own classes. The stylesheet styles them, and only the helper writes them.
FRAME_CLASSES = ("manager-cell-pictured", "manager-cell-art-box", "manager-cell-noart")


def _fragments(tree: ast.Module) -> list[tuple[int, str]]:
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def _matching(pieces: list[tuple[int, str]]) -> list[int]:
    return [line for line, text in pieces if any(name in text for name in FRAME_CLASSES)]


def _marked(source: str, suffix: str) -> list[int]:
    pieces = (_fragments(trees.parse_snippet(source)) if suffix == ".py"
              else list(enumerate(source.splitlines(), 1)))
    return _matching(pieces)


def _frames(path: pathlib.Path) -> list[str]:
    if path.suffix == ".py":
        lines = _matching(_fragments(trees.tree_for(path)))
    else:
        lines = _matching(list(enumerate(path.read_text(encoding="utf-8").splitlines(), 1)))
    return [f"{path.relative_to(REPO).as_posix()}:{line}" for line in lines]


# Each shape a list's frame could be drawn in outside the helper: a grid renderer, an
# element's classes, HTML for `ui.html`, and a `ui.select` option template.
SHAPES = {
    ".js": "cell.innerHTML = '<span class=\"manager-cell-art-box\"></span>';",
    ".py": '''
ui.element("span").classes("manager-cell-pictured grow")
ui.html(f'<span class="manager-cell-art-box {sized}"><img src="{address}"></span>')
SLOT = f"""<q-item-section>{lines}<i class="manager-cell-noart"></i></q-item-section>"""
''',
}


class ThumbnailsAreDrawnOnceTests(unittest.TestCase):
    def test_no_manager_module_draws_a_name_picture(self) -> None:
        offenders = []
        for path in sorted([*MANAGER.rglob("*.py"), *MANAGER.rglob("*.js")]):
            if path != HELPER:
                offenders += _frames(path)
        self.assertEqual(offenders, [], "draw it with manager/kit/thumbnails.py")

    def test_the_scan_finds_the_frame_in_every_shape_a_list_draws(self) -> None:
        self.assertEqual(_marked(SHAPES[".js"], ".js"), [1])
        self.assertEqual(sorted(_marked(SHAPES[".py"], ".py")), [2, 3, 4])

    def test_the_scan_finds_each_of_the_helper_s_classes(self) -> None:
        """Or a scan that matches nothing would pass the test above as well."""
        written = " ".join(text for _line, text in _fragments(trees.tree_for(HELPER)))
        for name in FRAME_CLASSES:
            with self.subTest(name=name):
                self.assertIn(name, written)


if __name__ == "__main__":
    unittest.main()
