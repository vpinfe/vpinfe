"""One media file, as large as the window will allow, with the controls to look at it.

Its own module because both the map and the slot open it, and the map cannot import the
workbench - the workbench imports the map.

Rotating here turns the view, never the file: a playfield render ships landscape while
the thing it photographs is portrait. Changing the file is the Edit row's.
"""

from __future__ import annotations

import html
import json
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from nicegui import ui

from common.i18n import t
from common.media.media_specs import media_family
from manager.kit import dialog as frame
from manager.kit import panel, verbs

# Rotated a quarter turn, the picture's visual bounds swap - so the limits that keep it
# inside the window have to swap with them, or a turned landscape frame runs off the
# top and bottom.
_UPRIGHT = "max-width:84vw; max-height:74vh;"
_TURNED = "max-width:74vh; max-height:84vw;"
json_upright = json.dumps(_UPRIGHT)
json_turned = json.dumps(_TURNED)

# One viewer is open at a time - it is a modal over the whole window - so fixed ids are
# enough to find these from the script that drives them.
_MEDIA_ID = "manager-viewer-media"
_PLAY_ID = "manager-viewer-play"
_SEEK_ID = "manager-viewer-seek"
_TIME_ID = "manager-viewer-time"
_COUNT_ID = "manager-viewer-pages"
_PREV_ID = "manager-viewer-prev"
_NEXT_ID = "manager-viewer-next"

# Native <video controls> are part of the element, so they turn with it and a rotated
# video leaves its scrubber on its side. Ours live in the bar, which never turns.
_TRANSPORT = f"""
<div class="manager-viewer-transport">
  <button type="button" id="{_PLAY_ID}" class="manager-viewer-btn">
    <span class="material-icons">pause</span></button>
  <input id="{_SEEK_ID}" class="manager-viewer-seek" type="range"
         min="0" max="1000" value="0" step="1">
  <span id="{_TIME_ID}" class="manager-viewer-clock">0:00 / 0:00</span>
</div>
"""

def _pages_bar() -> str:
    return f"""
<div class="manager-viewer-transport manager-viewer-pages">
  <button type="button" id="{_PREV_ID}" class="manager-viewer-btn"
    title="{t("manager.mediaview.step_back")}" aria-label="{t("manager.mediaview.step_back")}">
    <span class="material-icons">chevron_left</span></button>
  <span id="{_COUNT_ID}" class="manager-viewer-clock"></span>
  <button type="button" id="{_NEXT_ID}" class="manager-viewer-btn"
    title="{t("manager.mediaview.step_on")}" aria-label="{t("manager.mediaview.step_on")}">
    <span class="material-icons">chevron_right</span></button>
</div>
"""

_WIRE = f"""
(() => {{
  const v = document.getElementById('{_MEDIA_ID}');
  const play = document.getElementById('{_PLAY_ID}');
  const seek = document.getElementById('{_SEEK_ID}');
  const clock = document.getElementById('{_TIME_ID}');
  if (!v || !play || !seek || !clock) return;
  const mmss = (s) => {{
    if (!isFinite(s)) return '0:00';
    const m = Math.floor(s / 60), r = Math.floor(s % 60);
    return m + ':' + String(r).padStart(2, '0');
  }};
  const icon = () => play.firstElementChild.textContent = v.paused ? 'play_arrow' : 'pause';
  play.onclick = () => {{ v.paused ? v.play() : v.pause(); icon(); }};
  // While dragging, the slider drives the video; otherwise the video drives the slider.
  let scrubbing = false;
  seek.oninput = () => {{ scrubbing = true;
    if (isFinite(v.duration)) v.currentTime = (seek.value / 1000) * v.duration; }};
  seek.onchange = () => {{ scrubbing = false; }};
  v.ontimeupdate = () => {{
    if (!scrubbing && isFinite(v.duration) && v.duration > 0) {{
      seek.value = Math.round((v.currentTime / v.duration) * 1000);
    }}
    clock.textContent = mmss(v.currentTime) + ' / ' + mmss(v.duration);
  }};
  v.onplay = icon; v.onpause = icon;
  icon();
}})()
"""


_VIEW = f"""
(() => {{
  const el = document.getElementById('{_MEDIA_ID}');
  const stage = el && el.closest('.manager-viewer-stage');
  if (!el || !stage) return;
  const UPRIGHT = {json_upright};
  const TURNED = {json_turned};
  const st = {{ angle: 0, scale: 1, tx: 0, ty: 0 }};

  // An <img> is natively draggable, and a real press-and-move starts an HTML5 drag that
  // takes the pointer stream with it - so panning worked under synthetic events and not
  // under a hand.
  el.draggable = false;
  el.style.webkitUserDrag = 'none';

  const apply = () => {{
    el.style.transform =
      `translate(${{st.tx}}px, ${{st.ty}}px) rotate(${{st.angle}}deg) scale(${{st.scale}})`;
    stage.style.cursor = st.scale > 1 ? 'grab' : 'default';
  }};

  // The stage is sized from the untransformed fit, so zooming magnifies inside a panel
  // that holds still - only a turn changes how much room the picture needs.
  const resize = () => {{
    st.scale = 1; st.tx = 0; st.ty = 0;
    el.style.cssText = (st.angle % 180 ? TURNED : UPRIGHT)
      + ` transform: rotate(${{st.angle}}deg);`;
    el.draggable = false;
    el.style.webkitUserDrag = 'none';
    stage.style.minWidth = ''; stage.style.minHeight = '';
    requestAnimationFrame(() => {{
      const b = el.getBoundingClientRect();
      stage.style.minWidth = Math.ceil(b.width) + 'px';
      stage.style.minHeight = Math.ceil(b.height) + 'px';
      apply();
    }});
  }};

  const center = () => {{
    const b = stage.getBoundingClientRect();
    return {{ x: b.left + b.width / 2, y: b.top + b.height / 2 }};
  }};

  // Zoom about the pointer: whatever is under it stays under it. Derived from
  // screen = center + t + R(angle) * scale * q, which rearranges to this without
  // needing the rotation in the arithmetic.
  const zoomTo = (target, mx, my) => {{
    const next = Math.min(8, Math.max(1, target));
    if (next === st.scale) return;
    const c = center(), k = next / st.scale;
    st.tx = mx - c.x - k * (mx - c.x - st.tx);
    st.ty = my - c.y - k * (my - c.y - st.ty);
    st.scale = next;
    if (st.scale === 1) {{ st.tx = 0; st.ty = 0; }}
    apply();
  }};

  stage.addEventListener('wheel', (e) => {{
    e.preventDefault();
    zoomTo(st.scale * Math.exp(-e.deltaY * 0.002), e.clientX, e.clientY);
  }}, {{ passive: false }});

  // Safari reports a trackpad pinch as its own gesture events rather than ctrl+wheel,
  // so without these the gesture does nothing there at all.
  let pinchFrom = 1;
  stage.addEventListener('gesturestart', (e) => {{
    e.preventDefault(); pinchFrom = st.scale;
  }});
  stage.addEventListener('gesturechange', (e) => {{
    e.preventDefault(); zoomTo(pinchFrom * e.scale, e.clientX, e.clientY);
  }});
  stage.addEventListener('gestureend', (e) => e.preventDefault());

  // Pointer events rather than touch events: one code path covers mouse, trackpad,
  // pen and touch on every current browser. Two pointers pinch, one pans - and with
  // `touch-action: none` the browser hands us the gesture instead of scrolling.
  const down = new Map();
  let dragging = null;
  let pinch = null;

  const spread = () => {{
    const [a, b] = [...down.values()];
    return {{ dist: Math.hypot(a.x - b.x, a.y - b.y),
             x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }};
  }};

  stage.addEventListener('pointerdown', (e) => {{
    down.set(e.pointerId, {{ x: e.clientX, y: e.clientY }});
    try {{ stage.setPointerCapture(e.pointerId); }} catch (err) {{}}
    if (down.size === 2) {{
      dragging = null;
      const s = spread();
      pinch = {{ dist: s.dist, scale: st.scale }};
    }} else if (down.size === 1 && st.scale > 1) {{
      e.preventDefault();
      dragging = {{ x: e.clientX - st.tx, y: e.clientY - st.ty }};
      stage.style.cursor = 'grabbing';
    }}
  }});

  stage.addEventListener('pointermove', (e) => {{
    if (!down.has(e.pointerId)) return;
    down.set(e.pointerId, {{ x: e.clientX, y: e.clientY }});
    if (pinch && down.size === 2) {{
      e.preventDefault();
      const s = spread();
      if (pinch.dist > 0) zoomTo(pinch.scale * (s.dist / pinch.dist), s.x, s.y);
    }} else if (dragging) {{
      e.preventDefault();
      st.tx = e.clientX - dragging.x; st.ty = e.clientY - dragging.y;
      apply();
    }}
  }});

  const release = (e) => {{
    if (e && e.pointerId !== undefined) down.delete(e.pointerId);
    else down.clear();
    if (down.size < 2) pinch = null;
    if (down.size === 0) dragging = null;
    stage.style.cursor = st.scale > 1 ? 'grab' : 'default';
  }};
  stage.addEventListener('pointerup', release);
  stage.addEventListener('pointercancel', release);
  stage.addEventListener('lostpointercapture', release);
  // A press that ends outside the stage still has to end the drag.
  window.addEventListener('pointerup', release);
  stage.addEventListener('dragstart', (e) => e.preventDefault());
  stage.addEventListener('dblclick', () => {{
    st.scale = 1; st.tx = 0; st.ty = 0; apply();
  }});

  window.__hubView = {{
    turn: (step) => {{ st.angle = (st.angle + step + 360) % 360; resize(); }},
    reset: () => {{ st.scale = 1; st.tx = 0; st.ty = 0; apply(); }},
  }};
  resize();
}})()
"""


# The page being shown and the arrow keys live in the browser: stepping is a swap of the
# picture's address, which a round trip per page would only slow down. The keys are
# taken off the window while the viewer is open and given back when it closes.
def _pager(urls: Sequence[str], start: int, said: str) -> str:
    return f"""
(() => {{
  const el = document.getElementById('{_MEDIA_ID}');
  const count = document.getElementById('{_COUNT_ID}');
  const prev = document.getElementById('{_PREV_ID}');
  const next = document.getElementById('{_NEXT_ID}');
  if (!el || !count || !prev || !next) return;
  const urls = {json.dumps(list(urls))};
  const said = {json.dumps(said)};
  let at = {start - 1};
  const show = () => {{
    el.src = urls[at];
    count.textContent = said.replace('{{page}}', at + 1).replace('{{count}}', urls.length);
    prev.disabled = at === 0;
    next.disabled = at === urls.length - 1;
  }};
  // A new picture has its own size, so the fit and the zoom start over.
  el.onload = () => window.__hubView && window.__hubView.turn(0);
  const go = (step) => {{
    const to = Math.min(urls.length - 1, Math.max(0, at + step));
    if (to !== at) {{ at = to; show(); }}
  }};
  prev.onclick = () => go(-1);
  next.onclick = () => go(1);
  const key = (e) => {{
    if (e.key === 'ArrowLeft') {{ go(-1); e.preventDefault(); }}
    else if (e.key === 'ArrowRight') {{ go(1); e.preventDefault(); }}
  }};
  if (window.__hubPageKeys) window.removeEventListener('keydown', window.__hubPageKeys);
  window.__hubPageKeys = key;
  window.addEventListener('keydown', key);
  show();
}})()
"""


_UNPAGE = """
if (window.__hubPageKeys) {
  window.removeEventListener('keydown', window.__hubPageKeys);
  window.__hubPageKeys = null;
}
"""


# A PDF at the card's size shows pages and no toolbar, fitted to the width. The large
# viewer takes the address as it is, so the browser draws its toolbar.
_PDF_IN_CARD = "#toolbar=0&view=FitH"


def _frame(src: str, label: str, *, element_id: str = "", sandboxed: bool = False) -> None:
    """A page of its own in a frame. `sandboxed` is a frame with every permission off: no
    scripts, no same-origin access, no forms, no top navigation."""
    named = f' id="{element_id}"' if element_id else ""
    held = " sandbox" if sandboxed else ""
    light = ' style="background:#fff; color-scheme:light"' if sandboxed else ""
    # `sanitize=False` because the sanitizer removes a frame. The address is the app's own.
    ui.html(f'<iframe{named} class="manager-doc-frame"{held}{light} '
            f'src="{html.escape(src)}" title="{html.escape(label)}"></iframe>',
            sanitize=False).classes("manager-doc")


# Only the browser knows whether it can draw a PDF in a frame, so the link that stands in
# for the frame is drawn hidden and shown from here. Android Chrome has no inline viewer.
def _link_if_no_viewer(frame_id: str, link_id: str) -> str:
    return f"""
(() => {{
  let tries = 0;
  const go = () => {{
    const f = document.getElementById('{frame_id}');
    const l = document.getElementById('{link_id}');
    if (!f || !l) {{ if (++tries < 30) requestAnimationFrame(go); return; }}
    if (navigator.pdfViewerEnabled === false) {{
      f.style.display = 'none'; l.style.display = '';
    }}
  }};
  go();
}})()
"""


def pdf(src: str, label: str, *, large: bool = False) -> None:
    """The file in the browser's own viewer, or an Open link where it has none."""
    token = uuid.uuid4().hex[:8]
    frame_id, link_id = f"manager-pdf-{token}", f"manager-pdf-link-{token}"
    _frame(src + ("" if large else _PDF_IN_CARD), label, element_id=frame_id)
    with ui.element("div").props(f"id={link_id}").style("display: none"):
        panel.link_out(t("manager.workbench.open", kind=label), to=src)()
    ui.run_javascript(_link_if_no_viewer(frame_id, link_id))


def web_page(src: str, label: str) -> None:
    """A web page file drawn with its scripts off."""
    _frame(src, label, sandboxed=True)


def preview(src: str, kind: str, label: str, element_id: str = "", *,
            family: str = "") -> None:
    """Present the file with an element that can actually play it.

    An <img> pointing at a .mp4 downloads the whole file and paints nothing - the slot
    reads as empty for a video that is there, and on a library that is not local the
    fetch is long enough to read as the page having stopped. `preload="metadata"` is
    what keeps the poster frame cheap: enough to show it, not the whole video.
    `element_id` names the picture or video, for a script that has to find it. `family`
    is what the file itself draws as where that differs from its kind's, a picture held
    in a rule sheet's slot.
    """
    family = family or media_family(kind)
    named = f' id="{element_id}"' if element_id else ""
    if family == "video":
        # `#t=0.1` for the same reason the map tiles use it: metadata alone can leave
        # the frame blank, and an empty box behind a play button says nothing about
        # what is in the file.
        ui.html(f'<video{named} src="{src}#t=0.1" preload="metadata" controls '
                f'playsinline></video>')
    elif family == "audio":
        ui.html(f'<audio src="{src}" preload="metadata" controls></audio>')
    elif family == "image":
        ui.html(f'<img{named} src="{src}">')
    else:
        # A broken <img> would say the file is missing. A rule sheet that is a document is
        # drawn by `rule_sheet` before it gets here.
        panel.link_out(t("manager.workbench.open", kind=label), to=src)()


# An action in the viewer's bar: its icon, its tooltip, and what it does once the viewer
# has closed.
Act = tuple[str, str, Callable[[], Awaitable[Any]]]


def open_image(src: str, label: str, acts: Sequence[Act] = ()) -> None:
    _open(src, "image", label, acts)


def open_pages(urls: Sequence[str], label: str, start: int = 1) -> None:
    """Show a paged kind's pictures large, one at a time, starting at page `start`."""
    _open(urls[start - 1], "image", label, pages=urls, start=start)


def open_document(label: str, draw: Callable[[], None]) -> None:
    """Show a document large. `draw` puts the document in the stage; the rotate control
    is for pictures and is not offered."""
    with frame.made() as dialog, ui.card().classes("manager-viewer-card manager-viewer-doc"):
        with ui.row().classes("items-center gap-2 w-full no-wrap manager-viewer-bar"):
            ui.label(label).classes("manager-card-title shrink-0")
            ui.space()
            panel.icon_only(verbs.CLOSE, dialog.close, hint=t("word.close"))
        with ui.element("div").classes("manager-viewer-stage"):
            draw()
    dialog.open()


def open_viewer(src: str, kind: str, label: str, *, family: str = "") -> None:
    """Show this file large. `family` overrides what `kind` would answer, for a source
    with no kind of its own - a card's data URI, say."""
    _open(src, family or media_family(kind), label)


def _open(src: str, family: str, label: str, acts: Sequence[Act] = (), *,
          pages: Sequence[str] = (), start: int = 1) -> None:
    if family not in ("image", "video"):
        # Audio has no frame to enlarge and a document is not ours to render.
        ui.notify(t("manager.mediaview.nothing_enlarge", label=(label)), type="info")
        return

    # The angle lives with the zoom and the pan, in the script - they compose into one
    # transform, and a round trip per turn would fight the pointer.
    def turn() -> None:
        ui.run_javascript("window.__hubView && window.__hubView.turn(90);")

    # A panel over a dimmed page rather than a takeover: the card is sized by what is
    # in it, so the media being the content is what centers it.
    with frame.made() as dialog, ui.card().classes("manager-viewer-card"):
        with ui.row().classes("items-center gap-2 w-full no-wrap manager-viewer-bar"):
            ui.label(label).classes("manager-card-title shrink-0")
            if family == "video":
                ui.html(_TRANSPORT).classes("grow min-w-0")
            elif len(pages) > 1:
                ui.space()
                ui.html(_pages_bar())
            else:
                ui.space()
            for icon, said, act in acts:
                _act(dialog, icon, said, act)
            panel.icon_only(verbs.ROTATE_VIEW, turn, hint=t("manager.mediaview.rotate_view"))
            ui.button(icon="close", on_click=dialog.close).props("flat dense round") \
                .tooltip(t("word.close"))

        with ui.element("div").classes("manager-viewer-stage"):
            # Sized by the script once it is on screen, which is also what has to
            # measure a turn - so it is the only thing that sets this style.
            if family == "video":
                ui.html(f'<video id="{_MEDIA_ID}" src="{src}" autoplay loop'
                        f' playsinline style="{_UPRIGHT}"></video>')
            else:
                ui.html(f'<img id="{_MEDIA_ID}" src="{src}" style="{_UPRIGHT}">')

    dialog.open()
    ui.run_javascript(_VIEW)
    if family == "video":
        ui.run_javascript(_WIRE)
    if len(pages) > 1:
        # Left as `{page}` and `{count}` for the script to fill in as it steps.
        ui.run_javascript(_pager(pages, start, t("manager.mediaview.page_of",
                                                 page="{page}", count="{count}")))
        dialog.on("hide", lambda: ui.run_javascript(_UNPAGE))


def _act(dialog: Any, icon: str, said: str, act: Callable[[], Awaitable[Any]]) -> None:
    async def pressed() -> None:
        dialog.close()
        await act()

    panel.icon_action(said, pressed, icon=icon, hint=said)()
