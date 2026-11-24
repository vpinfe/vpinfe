"""The lines a Manager surface draws, read as somebody reading it would."""

from __future__ import annotations

from nicegui import ui


def details(body: ui.element) -> dict[str, str]:
    """Each line under `body`, with the detail its hover shows, or "" for none.

    A line with a detail carries the mark that opens the same words, and one without
    carries none; either failing is an AssertionError here.
    """
    tips = {one.props["target"]: one.text
            for one in body.descendants() if isinstance(one, ui.tooltip)}
    opened = {said.id for menu in body.descendants() if isinstance(menu, ui.menu)
              for said in menu.descendants()}
    found: dict[str, str] = {}
    for line in body.descendants():
        if not isinstance(line, ui.label) or line.id in opened:
            continue
        hover = tips.get(f"#{line.html_id}", "")
        marked = [said.text for menu in line.descendants() if isinstance(menu, ui.menu)
                  for said in menu.descendants() if isinstance(said, ui.label)]
        assert marked == ([hover] if hover else []), f"{line.text!r}: its mark opens {marked}"
        found[line.text] = hover
    return found


def folders(body: ui.element) -> dict[str, str]:
    """Each folder a Needs You folder list names, with the line drawn under it on screen,
    or "" for none. The list's intro and title are not folders and are left out."""
    found: dict[str, str] = {}
    last = ""
    for line in body.descendants():
        if not isinstance(line, ui.label):
            continue
        if "manager-setting" in line.classes:
            last = line.text
            found[last] = ""
        elif "manager-help" in line.classes and last:
            found[last] = line.text
    return found
