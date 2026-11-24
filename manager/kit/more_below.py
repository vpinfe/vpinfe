"""A scrolling region taller than its pane says so: the fade and **More below** pill of
`manager-more`, shown while there is more under the visible part and gone once the end is.

The class draws it and nothing else decides when it shows, so the check lives here, in the
browser, where the scroll position is.
"""

from __future__ import annotations

from nicegui import ui

from common.i18n import t

# Pixels of the end left unseen before it counts as unseen, since a fractional scroll
# position never quite reaches the end.
_SLACK = 2
# Where a click on the pill takes the region, as a share of its height: the last row seen
# stays in view, so the person does not lose their place.
_STEP = 0.8


def attach(scroll: ui.element) -> ui.element:
    """Draws the cue as the last child of `scroll`, the element that scrolls. `watch(...)`
    binds it to the scroll position once the element is in the page."""
    with scroll:
        more = ui.element("div").classes("manager-more").props("hidden")
        with more:
            with ui.element("div").classes("manager-more-fade"):
                with ui.element("button").classes("manager-more-pill").props("type=button"):
                    ui.icon("keyboard_arrow_down")
                    ui.label(t("word.more_below"))
    return more


def watch(scroll: ui.element, more: ui.element) -> str:
    """The script that keeps `more` in step with `scroll`: on its scroll, its resize and
    whatever is drawn into it."""
    return f"""
        (() => {{
          const scroll = document.getElementById('c{scroll.id}');
          const more = document.getElementById('c{more.id}');
          if (!scroll || !more) return;
          const check = () => {{
            more.hidden = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight <= {_SLACK};
          }};
          scroll.addEventListener('scroll', check, {{ passive: true }});
          new ResizeObserver(check).observe(scroll);
          new MutationObserver(check).observe(scroll, {{ childList: true, subtree: true }});
          more.querySelector('button').addEventListener('click', () => {{
            scroll.scrollBy({{ top: scroll.clientHeight * {_STEP}, behavior: 'smooth' }});
          }});
          check();
        }})()
    """
