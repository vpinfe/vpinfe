"""VPinFE's palette, expressed as Quasar brand tokens rather than a stylesheet."""

from __future__ import annotations

import json
import re
import weakref
from typing import Any

from nicegui import ui

from common.config.paths import bundled
from manager.kit import link_tab, place, script_failed

LOGO = "/static/img/vpinfe-logo.png"


# The 2.x visual treatment, from manager.css: a synthwave grid over the page, and the
# table's own row colors. AG Grid reads its palette from --ag-* custom properties, so
# the brand values are mapped onto those rather than restyling any of its parts.
# The design tokens. Everything below refers to these rather than to a hex or a pixel
# count, so a decision about the palette or the type scale is made once.
# The Synthwave palette: every color and every effect, and nothing else. A mode is
# this block and no other change - `_STRUCTURE` below is what a mode may not touch.
_SYNTHWAVE = """

  --ink: #eef9ff;        /* primary text            18.7:1 */
  --ink-2: #cbb8ea;      /* secondary text          11.1:1 */
  --ink-3: #9b8bbd;      /* help and hints           6.5:1 */
  --ink-default: #8878a8; /* a value left at its default  4.8:1 */
  --said-mix: 42%;   /* hue a line under a name keeps */
  --accent: #00d9ff;     /* interactive text        11.8:1 */
  --positive: #00ff9f;   /* present, installed, in use */
  /* Fills, borders and gradients only: magenta measures 4.4:1, under the 4.5:1 text
     needs. Text that must look interactive takes --accent. */
  --flair: #b429f9;
  --danger: #ff6b9d;     /* destructive, and absent in a way that costs you */
  /* Hover lifts the same hue. It used to move to orange, which was a transposition of
     the resting value rather than a decision. */
  --danger-hover: #ff8fb8;
  /* The one a *fill* can carry white on, which --danger cannot: Quasar fills an error
     toast with it and writes white on top. Measured 4.81:1, where --danger is 2.68:1. */
  --danger-fill: #e00068;

  /* What a state looks like when it is a fill or an edge rather than a word. Each is
     its own value, not the family color at an opacity: the same percentage over a dark
     panel and over a white one are not the same design, so the value is the decision
     and a mode restates it. */
  --positive-wash: rgba(0, 255, 159, 0.12);
  --positive-edge: rgba(0, 255, 159, 0.45);
  --danger-wash: rgba(255, 107, 157, 0.12);
  --danger-edge: rgba(255, 107, 157, 0.45);
  --warm-wash: rgba(255, 192, 97, 0.12);
  --warm-edge: rgba(255, 192, 97, 0.45);
  /* The off and unknown states, which share the help color's lilac. */
  --quiet-edge: rgba(155, 139, 189, 0.55);
  --quiet-wash: rgba(155, 139, 189, 0.08);

  /* A row under the pointer. Translucent, because the rows it lands on are not all the
     same color underneath. */
  --wash-hover: rgba(255, 255, 255, 0.06);
  /* A small panel that steps up from whatever it sits on. It has to stay translucent:
     the same tile appears over the workbench's gradient and over the bare page, and an
     opaque value that reads as a surface against one composites to the other exactly,
     leaving an empty slot as a bare outline. */
  --surface-lift: rgba(26, 15, 53, 0.6);
  /* Laid over artwork so a control or a label on top of it stays readable. The ground
     here is a photograph, so this one is translucent by nature rather than by choice. */
  --scrim-media: rgba(10, 5, 24, 0.72);
  --scrim-media-hover: rgba(10, 5, 24, 0.9);
  /* What covers the whole page to put it out of reach - behind a dialog, and behind
     the prompt that has taken the keyboard. Darker than the media plate, which is
     there to keep a picture legible rather than to interrupt. */
  --scrim-page: rgba(4, 2, 12, 0.78);

  /* The accent as a fill: a control that is pointed at, focused, or about to take a
     drop. One step, not two - a focused field carries a ring as well, so the fill was
     not the only thing saying so, and the fainter of the two was hard to see at all. */
  --accent-wash: rgba(0, 217, 255, 0.10);
  /* Louder again, for a section that is the one you are in. Laid over whatever ground
     the row already has rather than replacing it, which is why it is written as a
     gradient of one color. */
  --accent-wash-on: rgba(0, 217, 255, 0.22);
  /* Louder again, and only while a drag is actually over the target. */
  --drop-lit: rgba(0, 217, 255, 0.16);

  /* The faint plate a bare control sits on, and the zebra stripe down a list. Both are
     always there, which is why they sit this far under --wash-hover: a stripe level
     with the hover leaves a pointed-at row indistinguishable from an unpointed one. */
  --wash-lift: rgba(255, 255, 255, 0.02);
  /* The unfilled part of a progress bar - a track, not a wash. */
  --bar-track: rgba(255, 255, 255, 0.08);

  --surface-0: #0a0518;
  --surface-1: #150a2e;
  --surface-2: #1a0f35;
  /* Below the panels rather than above them: the grid's own ground, and the bed a
     thumbnail is shown against. */
  --surface-3: #140a2b;
  /* Deeper still, and the one ground below the panel: the grid header, the foot of the
     nav rail, the region the workbench works in, and the plate a picture is shown on.
     Those were three values a unit and a half apart, which is not three decisions. */
  --surface-sunken: #0f0722;
  /* The region the work happens in, and the plate a picture is shown on. Both were
     folded into --surface-sunken while Synthwave was the only palette, where all three
     composite to the same color. They are separate again because they stop agreeing the
     moment the ground is light: "sunken" means recessed on a dark page and merely
     dirtier on a white one. */
  --surface-work: #0f0722;
  --surface-viewer: #0b0520;
  /* Darker than the page, because a picture reads best against something that is not
     competing with it. */
  --surface-art: #06030f;
  --art-ground: var(--surface-art);
  /* Which way round the grid alternates. Dark grounds put the darker color under and
     the lighter stripe on top; Light has to do the reverse, or the grid is a grey slab
     next to a white page. */
  --grid-ground: var(--surface-3);
  --grid-stripe: var(--surface-2);
  /* The row you are on. Opaque on purpose - a translucent selection composites over
     the two alternating bands differently, so a run of selected rows stops reading as
     one block. */
  --row-select: #14314a;
  /* The brand magenta as a fill, for a row or an item under the pointer. Cyan answers
     "which one" and is not available for this. */
  --flair-wash: rgba(180, 41, 249, 0.14);
  /* The same fill over media rather than over a panel, where the ground is a picture
     and a fainter wash disappears into it. */
  --flair-wash-strong: rgba(180, 41, 249, 0.18);

  /* A notice: amber across a whole block, not a chip. A wide area takes a lower alpha
     than a small one to carry the same weight, which is why these are not the chip's
     --warm-wash and --warm-edge. */
  --notice-wash: rgba(255, 192, 97, 0.07);
  --notice-edge: rgba(255, 192, 97, 0.35);
  /* Borrowed media, and its own amber: this is the one state the picture cannot tell
     you, so it is louder than the warm the rest of the palette uses. */
  --borrowed-edge: rgba(255, 176, 32, 0.65);
  /* The media plate, warmed, for the file a table owns itself. */
  --scrim-media-warm: rgba(40, 24, 8, 0.85);
  --line: #2b1a4d;
  /* The visible edge, against --line's hairline: a menu, a dropped-file target and a
     panel header all need to be seen as an edge rather than felt as one.
     3.1:1 against the lightest surface a control sits on, which is the floor WCAG 1.4.11
     puts under the boundary you identify a control by. The #3d2461 that held this job
     measured 1.21 to 1.55 against those same surfaces - visible as a shape, and not a
     boundary anybody was required to be able to see. */
  --line-strong: #6f6a8c;
  --line-soft: #1f1338;
  /* Structure, not a hairline: the rule between stacked section bands has to be seen
     across a dark panel, and --line-soft sits close enough to the background that it
     read as nothing at all. */
  --line-band: rgba(203, 184, 234, 0.22);
  /* A row you are pointing at, and the place you are actually on. Two steps, because
     they are two states - and the second has to be the louder one. It was not: the
     nav's current entry sat below the hover it competes with. */
  --surface-hover: #2a1a4a;
  --surface-current: #332057;

  /* What a draggable divider looks like, wherever one appears - a border color here
     reads as an edge rather than a handle. A divider is something you grab, so it takes
     WCAG 1.4.11's 3:1 like any other control: at .28 it measured 2.37 to 2.46 against the
     surfaces it lies on, and .38 is the smallest step that clears the floor on all of
     them. Higher starts competing with the content either side of it. */
  /* The one warm color in this palette, and the reason both uses below read at a
     glance: nothing else here is warm. Named for what it is rather than for either
     use, so the second one did not have to invent a second amber.
     Measured against the surfaces it sits on: 11.1:1 and 5.9:1. */
  --warm: #ffc061;
  /* The amber a *fill* uses, and the ink that sits on one. A third instance of the
     same split as --flair and --danger-fill: the color that reads as a word is not the
     color that carries a word. Here they happen to agree; in Light they do not. */
  --warm-fill: #ffc061;
  --ink-on-fill: #0a0518;

  /* Who owns a media file. Amber is what makes "one table's own" legible in a map of
     twenty tiles; the folder-wide case is the norm and stays quiet. */
  --tier-table: var(--warm);
  --tier-quiet: var(--ink-3);
  --tag-red: #ff5c7a;
  --tag-orange: #ff9a4d;
  --tag-amber: #ffc061;
  --tag-green: #3ddc84;
  --tag-teal: #2ed3c6;
  --tag-blue: #4da3ff;
  --tag-purple: #b98cff;
  --tag-pink: #ff7ad9;
  --tag-gray: #9b8bbd;
  --resize-line: rgba(255, 255, 255, 0.38);

  /* --- The effects layer -----------------------------------------------------------
     Synthwave carries three things the neutral modes will not: the backdrop grid, the
     glows, and the gradients behind the header, the rail and the workbench. None of
     them is a color, so none can be swapped by changing one. They are declared whole so
     a mode can resolve them to `none` - a glow rendered grey is still a glow, and on
     white it is a smudge. */
  --fx-backdrop:
      linear-gradient(0deg, rgba(0, 217, 255, 0.2) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0, 217, 255, 0.2) 1px, transparent 1px);
  --fx-glow-panel: 0 2px 8px rgba(180, 41, 249, 0.2);
  --fx-glow-nav: 0 0 4px rgba(180, 41, 249, 0.5), 0 0 8px rgba(180, 41, 249, 0.3);
  /* Two, because they are two jobs. The brand in the rail is the loudest text in the
     Manager and says so with a double glow; a panel's title is a heading and takes a
     single soft one. Collapsing them would make every panel heading shout. */
  --fx-glow-brand: 0 0 4px rgba(0, 217, 255, 0.5), 0 0 8px rgba(0, 217, 255, 0.3);
  --fx-glow-text: 0 0 6px rgba(0, 217, 255, 0.45);

  /* Elevation, as against glow: these say "above the page", not "brand". A neutral mode
     keeps them and only changes the color. */
  --viewer-shadow: 0 24px 60px rgba(0, 0, 0, 0.55);
  --shadow-drag: 0 8px 24px rgba(0, 0, 0, 0.55);
  --shadow-tooltip: 0 6px 20px rgba(0, 0, 0, 0.45);
  /* Synthwave and Dark separate the work region by lightness, which a light ground
     cannot do - so there the well is drawn and here it is not. */
  --well-shadow: none;

  /* The three grounds that are a gradient rather than a surface. Each resolves to a flat
     surface in a neutral mode. */
  --header-bg: linear-gradient(135deg, var(--flair) 0%, #4a1e7c 50%,
               var(--surface-0) 100%);
  /* Two color pairs, one fade. The hues differ per pane on purpose; the stops do not -
     0px, the 48px header's foot, the pane's surface 24px later. Each keeps only the
     tail its own pane needs. */
  --nav-bg: linear-gradient(180deg, var(--flair) 0px, #4a1e7c 48px, var(--surface-2) 72px,
            var(--surface-sunken) 100%);
  --workbench-bg: linear-gradient(180deg, #4a1e7c 0px, #2a1a52 48px,
                  var(--surface-1) 72px, var(--surface-1) 80px, transparent 80px);
  /* The same band without the transparent tail, for the rail's copy, which has nothing
     below it to show through to. */
  --workbench-bg-rail: linear-gradient(180deg, #4a1e7c 0px, #2a1a52 48px,
                       var(--surface-1) 72px);
  /* A card is a panel with a little depth in it rather than a flat plate. */
  --card-bg: linear-gradient(180deg, rgba(26, 15, 53, 0.75) 0%, rgba(15, 7, 34, 0.75) 100%);
  --scrollbar-thumb: rgba(155, 139, 189, 0.32);
  --scrollbar-thumb-hover: rgba(155, 139, 189, 0.62);
"""

# Neutral dark: no purple, no glow, no backdrop. Every value below was measured against
# the floors in the same way Synthwave's were - text 4.5:1 on every surface it can land
# on, a control's edge 3:1, a chip's text 4.5:1 on its own fill.
_DARK = """
  /* Ink. Cooler and quieter than Synthwave's - there is no purple under it to
     fight, so the same legibility needs less brightness. */
  --ink: #e6e9ee;
  --ink-2: #a9b1bd;
  --ink-3: #8a93a1;
  --ink-default: #7b8390;  /* a value left at its default  4.6:1 */
  --said-mix: 42%;   /* hue a line under a name keeps */
  /* Blue is the interactive color here, and `--flair` is the one a fill can carry
     white on: Quasar paints a filled button with it and writes white on top, which
     the accent at 2.3:1 cannot hold. */
  --accent: #4cb2ff;
  --positive: #3fd68c;
  --flair: #2f6fd0;
  --danger: #ff6b6b;
  --danger-hover: #ff9494;
  /* The fill, not the text. White on it is 5.62:1; on --danger it would be 2.78. */
  --danger-fill: #c62828;
  /* A hue at .14 for a fill and .45 for an edge, the same pair across all four
     families, because on a neutral ground there is no reason for them to differ. */
  --positive-wash: rgba(63, 214, 140, 0.14);
  --positive-edge: rgba(63, 214, 140, 0.45);
  --danger-wash: rgba(255, 107, 107, 0.14);
  --danger-edge: rgba(255, 107, 107, 0.45);
  --warm-wash: rgba(240, 180, 41, 0.14);
  --warm-edge: rgba(240, 180, 41, 0.45);
  --quiet-edge: rgba(138, 147, 161, 0.45);
  --quiet-wash: rgba(138, 147, 161, 0.12);
  /* Neutral washes are lighter here than in Synthwave: the ground is already grey,
     so less is needed to read as a step. */
  --wash-hover: rgba(255, 255, 255, 0.05);
  --surface-lift: rgba(255, 255, 255, 0.04);
  --scrim-media: rgba(6, 7, 9, 0.72);
  --scrim-media-hover: rgba(6, 7, 9, 0.90);
  /* What covers the page. Neutral-black rather than purple-black. */
  --scrim-page: rgba(6, 7, 9, 0.78);
  /* The accent as a fill. Synthwave's magenta hovers become blue - the brand color
     that carried them does not exist in this mode. */
  --accent-wash: rgba(76, 178, 255, 0.14);
  --accent-wash-on: rgba(76, 178, 255, 0.24);
  --drop-lit: rgba(76, 178, 255, 0.20);
  --wash-lift: rgba(255, 255, 255, 0.03);
  --bar-track: rgba(255, 255, 255, 0.09);
  /* Four steps, measured: every text token above clears 4.5:1 on all of them. */
  --surface-0: #0e0f12;
  --surface-1: #16181d;
  --surface-2: #1e2127;
  --surface-3: #121419;
  --surface-sunken: #0a0b0e;
  --surface-work: #0a0b0e;
  --surface-viewer: #0a0b0e;
  --surface-art: #08090b;
  --art-ground: var(--surface-art);
  --grid-ground: var(--surface-3);
  --grid-stripe: var(--surface-2);
  --row-select: #1b3a57;
  --flair-wash: rgba(76, 178, 255, 0.10);
  --flair-wash-strong: rgba(76, 178, 255, 0.16);
  --notice-wash: rgba(240, 180, 41, 0.08);
  --notice-edge: rgba(240, 180, 41, 0.35);
  --borrowed-edge: rgba(240, 140, 20, 0.65);
  --scrim-media-warm: rgba(46, 32, 8, 0.85);
  /* Boundaries. `--line-strong` is 3.2:1 on the lightest surface a control sits on. */
  --line: #2a2e36;
  --line-strong: #696f7b;
  --line-soft: #1e2229;
  --line-band: rgba(169, 177, 189, 0.22);
  --surface-hover: #252931;
  --surface-current: #2e333d;
  --warm: #f0b429;
  --warm-fill: #f0b429;
  --ink-on-fill: #0e0f12;
  /* Derived, so they follow whatever the mode said above. */
  --tier-table: var(--warm);
  --tier-quiet: var(--ink-3);
  --tag-red: #f26d7d;
  --tag-orange: #f0954d;
  --tag-amber: #e8b04a;
  --tag-green: #4cc38a;
  --tag-teal: #3fbfb4;
  --tag-blue: #5aa2f0;
  --tag-purple: #a98bf0;
  --tag-pink: #e57ac8;
  --tag-gray: #8b93a1;
  --resize-line: rgba(255, 255, 255, 0.34);
  /* **This is what the mode is for.** Synthwave's grid, glows and gradients resolve
     to nothing here; a glow rendered grey is still a glow. Elevation is not glow and
     stays - a shadow says "above the page", which is true in any palette. */
  --fx-backdrop: none;
  --fx-glow-panel: none;
  --fx-glow-nav: none;
  --fx-glow-brand: none;
  --fx-glow-text: none;
  --viewer-shadow: 0 24px 60px rgba(0, 0, 0, 0.55);
  --shadow-drag: 0 8px 24px rgba(0, 0, 0, 0.55);
  --shadow-tooltip: 0 6px 20px rgba(0, 0, 0, 0.45);
  --well-shadow: none;
  --header-bg: var(--surface-1);
  --nav-bg: var(--surface-1);
  --workbench-bg: var(--surface-1);
  --workbench-bg-rail: var(--surface-1);
  --card-bg: var(--surface-2);
  /* Quiet, not invisible. */
  --scrollbar-thumb: rgba(138, 147, 161, 0.32);
  --scrollbar-thumb-hover: rgba(138, 147, 161, 0.62);
"""


# Conventional light: near-white page, white panels. Measured against the same floors as
# the other two - text 4.5:1 on every surface, a control's edge 3:1, a chip's text 4.5:1
# on its own fill.
_LIGHT = """
  /* Ink is near-black now, and every surface is near-white. Nothing else about the
     type changes - the scale and the weights are `_STRUCTURE`'s and no mode touches them. */
  --ink: #14171c;
  --ink-2: #4a5361;
  --ink-3: #5f6874;
  --ink-default: #6b7480;  /* a value left at its default  4.7:1 */
  --said-mix: 70%;   /* hue a line under a name keeps */
  /* Unlike either dark mode the interactive color is dark enough to be a fill as
     well as text: white on it is 5.84:1, so Quasar's `primary` and the accent are one
     color here. Same for the error fill. */
  --accent: #0b63c4;
  --positive: #0a7a4d;
  --flair: #0b63c4;
  --danger: #c02626;
  --danger-hover: #9e1c1c;
  --danger-fill: #c02626;
  /* Solid tints, not translucency. Twelve percent of a hue over white is nearly
     nothing, where over a dark panel it is a tint that keeps its hue - so a light mode
     restates the value rather than reusing the alpha. */
  --positive-wash: #e6f4ed;
  --positive-edge: #7fb79b;
  --danger-wash: #fdeaea;
  --danger-edge: #df9494;
  --warm-wash: #fdf3e0;
  --warm-edge: #c9a05a;
  --quiet-edge: #b9c0ca;
  --quiet-wash: #eef0f3;
  /* Neutral steps go *down* from white rather than up from black. */
  --wash-hover: #f0f2f5;
  --surface-lift: rgba(16, 24, 40, 0.03);
  /* Over artwork: **light**, and this is the one that does not invert. The label on
     this plate is `--ink`, which is near-black here - the dark plate the other modes use
     would hide it. A picture reads against a quiet ground in either direction. */
  --scrim-media: rgba(255, 255, 255, 0.82);
  --scrim-media-hover: rgba(255, 255, 255, 0.93);
  /* Over the page: dark, because a scrim puts what is behind it out of reach. */
  --scrim-page: rgba(16, 24, 40, 0.45);
  /* The accent as a fill, three steps, all solid. */
  --accent-wash: #e8f1fc;
  --accent-wash-on: #d3e5fa;
  --drop-lit: #c4dcf8;
  --wash-lift: #fafbfc;
  --bar-track: #e4e7ec;
  /* `--surface-1` and `--surface-2` are both white on purpose: elevation here is a
     shadow and a hairline, not a lightness step, because a raised surface cannot go
     lighter than white. That is what makes `--fx-glow-panel` real in this mode. */
  --surface-0: #f4f6f8;
  --surface-1: #ffffff;
  --surface-2: #ffffff;
  --surface-3: #eceef2;
  --surface-sunken: #e4e7ec;
  /* White, not the tint. The content you are working on is the paper and the chrome
     around it recedes - a light mode that greys the work region and leaves the nav
     white has that the wrong way round. --surface-sunken keeps the tint for what
     really is below the panel: the grid header and the foot of the rail. */
  --surface-work: #ffffff;
  --surface-viewer: #ffffff;
  --surface-art: #f1f3f6;
  /* Dark on a light page too: the art beside a name in a list is drawn for a dark
     frontend, and light art on a light ground is not there at all. */
  --art-ground: #1b1e24;
  --grid-ground: var(--surface-1);
  --grid-stripe: #eceef2;
  --row-select: #dbe9fa;
  --flair-wash: #eef4fc;
  --flair-wash-strong: #e0ebfa;
  --notice-wash: #fdf8ec;
  --notice-edge: #dcc79a;
  --borrowed-edge: #b8791f;
  --scrim-media-warm: rgba(253, 243, 224, 0.92);
  /* `--line-strong` is 3.05:1 on the darkest surface a control sits on. */
  --line: #d7dce3;
  --line-strong: #808995;
  --line-soft: #e8ebef;
  --line-band: rgba(74, 83, 97, 0.22);
  --surface-hover: #eef0f3;
  --surface-current: #e3e8ef;
  --warm: #8a5a00;
  /* Not --warm. The brown that reads as a word on white is a dark blob as a badge,
     and on a page where everything else is dark-on-white a dark blob is one more dark
     thing. This is lower contrast against the nav than the brown and far more visible,
     because nothing else on the page is this hue. 3.19:1 against white so the shape is
     identifiable, 5.63:1 for the number on it. */
  --warm-fill: #cf7d00;
  --ink-on-fill: #14171c;
  /* Derived, so they follow what this mode said above. The one warm color becomes a
     brown here: amber on white is not readable. */
  --tier-table: var(--warm);
  --tier-quiet: var(--ink-3);
  --tag-red: #c93448;
  --tag-orange: #c4621f;
  --tag-amber: #a87800;
  --tag-green: #23874f;
  --tag-teal: #16847b;
  --tag-blue: #2766b8;
  --tag-purple: #6f4fb8;
  --tag-pink: #b23d8c;
  --tag-gray: #626b78;
  /* Black rather than white, and 3.18:1 on the darkest surface a divider lies on. */
  --resize-line: rgba(0, 0, 0, 0.44);
  /* Three of Synthwave's four still resolve to nothing. **The panel glow does not** -
     on white it is the only thing separating a tile from the panel under it, so it
     carries a real, quiet shadow instead. Elevation keeps its values and only the color
     changes. */
  --fx-backdrop: none;
  --fx-glow-panel: 0 1px 2px rgba(16, 24, 40, 0.06), 0 1px 3px rgba(16, 24, 40, 0.10);
  --fx-glow-nav: none;
  --fx-glow-brand: none;
  --fx-glow-text: none;
  --viewer-shadow: 0 24px 60px rgba(16, 24, 40, 0.18);
  --shadow-drag: 0 8px 24px rgba(16, 24, 40, 0.16);
  --shadow-tooltip: 0 6px 20px rgba(16, 24, 40, 0.14);
  /* What makes the work region read as sunken rather than merely bounded. A border
     draws a frame; depth is a soft falloff cast onto the surface, so this is an
     inset shadow on the two edges that meet the chrome. */
  --well-shadow: inset 1px 0 0 rgba(16, 24, 40, 0.10),
                 inset 0 1px 0 rgba(16, 24, 40, 0.10),
                 inset 7px 0 9px -7px rgba(16, 24, 40, 0.30),
                 inset 0 7px 9px -7px rgba(16, 24, 40, 0.30);
  --header-bg: var(--surface-1);
  /* Its own tint, not the page ground: the rail has to differ from the white content
     *and* from the grid beside it, and the page ground cannot be moved far enough to do
     that without dragging every other region with it. 8.9 from white, and --ink-3 still
     reads on it at 4.58. */
  --nav-bg: #e3e8ee;
  --workbench-bg: var(--surface-1);
  --workbench-bg-rail: var(--surface-1);
  --card-bg: var(--surface-1);
  /* Quiet, not invisible. */
  --scrollbar-thumb: rgba(74, 83, 97, 0.32);
  --scrollbar-thumb-hover: rgba(74, 83, 97, 0.58);
"""


# Sizes, not colors. Density is a feature of this surface rather than a preference,
# so a mode that changed the type scale would turn a designed density into somebody's
# accident. These are stated once and every mode gets them.
_STRUCTURE = """

  --fs-caption: 12px;
  --fs-body: 14px;
  --fs-title: 20px;
  /* A stat, or a welcome's title - one line that is the content, not prose. */
  --fs-display: 30px;

  /* The DMD a preview draws is a lit orange display in every mode, so none of these
     follow the palette. */
  --dmd-ground: #0b0500;
  --dmd-dot: #3a1a00;
  --dmd-edge: #5a2a00;
  --dmd-lit: #ff8a1f;
  --dmd-glow: #ff6a00;
  --dmd-label: rgba(0, 0, 0, 0.7);

  /* 44px where a finger is in scope; the Manager is desk-first, so this is the floor. */
  --target-min: 32px;
  /* An action that sits beside a value rather than owning its row - see
     `.manager-action--inline`. Below the floor deliberately, and raised back to it where the
     pointer is a finger. */
  --target-inline: 22px;
  /* A field sits in the fact rhythm rather than standing above it. Raised on touch
     with the rest, below. */
  --field-h: 26px;
  /* An icon control at the end of a row - a way out, a verb about that row. One size
     whatever the control is: two of them side by side at different sizes read as a
     ranking. The box around it is what makes the target, there being no words to. */
  --icon-row: 15px;
  /* A glyph beside the words of a button in a dialog's footer. */
  --icon-button: 20px;
  /* A state drawn as a glyph in a menu row, beside the words it qualifies. */
  --icon-menu: 16px;
  /* A state drawn as a glyph beside a value, in a panel or a list. */
  --icon-mark: 18px;
  /* A glyph that is the whole of a control - a nav row, a tab. */
  --icon-nav: 19px;
  /* Art standing in for a picture that is not there, sized to the box it fills. */
  --icon-slot: 32px;
  --icon-slot-small: 22px;
  /* A key on the Remote's wheel: the glyph is most of the key, and a thumb reads it from
     above. */
  --icon-key: 34px;
  --icon-key-small: 26px;

  /* Text and fades laid over a table picture. Fixed across themes: the picture is the
     ground, so the ink has to answer it rather than the palette. */
  --on-art: #fff;
  --on-art-soft: #e4dcf7;
  --art-fade: rgba(0, 0, 0, 0.82);
  --art-fade-clear: rgba(0, 0, 0, 0);
  --art-chip: rgba(0, 0, 0, 0.55);
  --art-shadow: #000;

  /* A rating is five adjacent targets on one line, so it is sized on its own rather
     than off `--target-min` - five 44px boxes is a row 220px wide, which is most of a
     phone. Its own token so a surface can answer it without restyling the control. */
  --star-size: 13px;
  --star-gap: 2px;

  /* One row of facts, sized as text. A row that *is* a field keeps the field's own
     height; since such a field is never a swapped-in box, nothing jumps. */
  /* The row, not the value: every kind of value - text, a chip, a field, a switch -
     centers in a box this tall, so the air around one does not depend on which kind it
     is. At 26 a text value sat 21px in 26 while a chip carried its own padding and read
     roomier, which is the mismatch a reader sees as inconsistent spacing. */
  --fact-row: 30px;

  /* How far a label column may grow before a long label wraps instead. One long label
     should cost its own row two lines, not push every control on the page away from the
     label naming it. The pane's labels sit under this and never wrap. */
  --label-max: 200px;

  /* How wide the content of a panel or a page may be: everything drawn in the scroller,
     in the facts grid or not, ends here, so a section has one right edge and this is
     the one place to change it. In rem, not ch: `ch` is the element's own font size, so
     the same count is two different widths on a control and on the caption under it. */
  --content-width: 36rem;
  /* The floor under a select, for the caret and the longest option name a set happens
     to hold. Text stretches because what you type has no length; a closed list of named
     things does not. */
  --select-min: 200px;

  /* The gutter a panel keeps from whatever it sits against. One value, so the browse
     region and the work region do not each pick their own and land 4px apart. */
  --panel-gutter: 16px;
  /* The right gutter is the room a path field's Reset hangs in, so a narrow panel does not
     scroll sideways for it. */
  --panel-gutter-end: max(var(--panel-gutter), calc(var(--target-inline) + 4px));

  /* Quiet, not invisible: a scrollbar says a region has more in it, so hiding one
     hides that there is more to see. */
  --scrollbar-size: 10px;
"""


# Every appearance mode this surface has values for.
PALETTES = {"synthwave": _SYNTHWAVE, "dark": _DARK, "light": _LIGHT}
DEFAULT_MODE = "synthwave"
# The fourth mode, which has no values of its own: it asks the browser which of the two
# neutral palettes to use. Synthwave is a deliberate pick rather than something an
# operating system can ask for, so it is on neither side of this.
SYSTEM = "system"
SYSTEM_PALETTES = {"light": "light", "dark": "dark"}
# In the order a person is offered them: the one a new install starts on first.
MODES = (SYSTEM, *PALETTES)
# Whether Quasar should style its own components dark. Ours and Quasar's are two switches
# on one decision: the tokens paint what we wrote, and this paints what the framework
# draws for us. Synthwave is a dark palette even though it is not "Dark", and `system`
# is None, which is what NiceGUI calls auto - so Quasar reads the same signal we do.
QUASAR_DARK = {"synthwave": True, "dark": True, "light": False, SYSTEM: None}

# Where the install's choice is stored. Config rather than the browser: it belongs to
# the install as a whole and the Manager has no accounts to hang it on.
MODE_SECTION, MODE_KEY = "manager", "appearance"


def mode_or_default(name: str) -> str:
    """The mode a stored or requested value asks for, or the default.

    Tolerant on purpose. This reads a config file somebody may have edited and a query
    parameter anybody may type, and neither is a reason for the Manager not to draw.
    """
    said = str(name or "").strip().lower()
    return said if said in MODES else DEFAULT_MODE


def configured_mode() -> str:
    """The mode this install is set to. Every Manager surface asks this."""
    from common.config.config_access import cfg_get
    from common.config.paths import get_ini_config

    return mode_or_default(cfg_get(get_ini_config(), MODE_SECTION, MODE_KEY))


def palette_css(mode: str = DEFAULT_MODE) -> str:
    """One mode's `:root` block: its palette, plus the sizes no mode may change.

    A palette is one string rather than a dict of 65 values because the reasoning lives
    in the comments beside them - why a wash is its own value, why two glows are not one
    - and that is the half worth keeping when somebody writes the next mode.

    `system` is the one mode that is not a plain `:root`. Media queries resolve before
    first paint, so the page opens in the right palette; resolving it off the class
    Quasar stamps at mount would flash the default palette on every load.
    """
    if mode == SYSTEM:
        # The sizes sit outside the queries. No mode may change them, so a copy in each
        # branch would be two statements of a value that cannot differ.
        blocks = [":root {" + _STRUCTURE + "}\n"]
        blocks += [f"@media (prefers-color-scheme: {prefers}) {{\n:root {{"
                   + PALETTES[palette] + "}\n" + _quasar_vars(palette) + "}\n"
                   for prefers, palette in SYSTEM_PALETTES.items()]
        return "".join(blocks)
    # The brand as CSS for every mode, not only the one that cannot be handed a value.
    # One delivery path is what lets a mode change under a live page.
    return ":root {" + PALETTES[mode] + _STRUCTURE + "}\n" + _quasar_vars(mode)


# Where the rules live, and where they are served from. Through `bundled` rather than
# off __file__: a frozen build keeps its Python in an archive and its data files in a
# directory beside it, so the two are not in the same place and only one of them is
# where this file appears to be.
BASE_CSS = bundled("manager", "static", "manager-base.css")
BASE_MOUNT = "/manager-static"


def base_css() -> str:
    """Every rule, as text. For anything that has to read them rather than serve them."""
    return BASE_CSS.read_text(encoding="utf-8")


def _base_href() -> str:
    """The URL, carrying a digest of what is behind it.

    The file is served with an hour's max-age, which is the point of having it - but a
    browser holding a copy will not ask again inside that hour, whatever the etag says,
    so an upgrade would leave the old stylesheet on screen for up to an hour. A different
    URL is the only way to make a cached copy irrelevant, and deriving it from the bytes
    means it changes exactly when they do.
    """
    from hashlib import sha256

    try:
        digest = sha256(BASE_CSS.read_bytes()).hexdigest()[:12]
    except OSError:
        # Served or not, the page still has to draw. An unversioned URL caches badly;
        # no stylesheet at all is a blank Manager.
        return f"{BASE_MOUNT}/manager-base.css"
    return f"{BASE_MOUNT}/manager-base.css?v={digest}"


BASE_HREF = _base_href()


# The one element the palette lives in, so a change rewrites it rather than stacking a
# second block that has to outrank the first.
PALETTE_ID = "manager-palette"

# Per client: the switch that tells Quasar to style its own components dark.
_DARK_SWITCH: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()


def apply_mode(mode: str = DEFAULT_MODE) -> None:
    """The palette, the rules and Quasar's dark switch. Once, as a page is built."""
    _DARK_SWITCH[ui.context.client] = ui.dark_mode(QUASAR_DARK[mode])
    apply_flair(mode)


def repaint(mode: str) -> None:
    """Change the mode under a live page, with nothing else redrawn.

    The palette is custom properties and the brand is `--q-*`, so every surface already
    reading them follows without being rebuilt.
    """
    dark = _DARK_SWITCH.get(ui.context.client)
    if dark is not None:
        dark.value = QUASAR_DARK[mode]
    ui.run_javascript(
        f"document.getElementById({json.dumps(PALETTE_ID)}).textContent = "
        f"{json.dumps(palette_css(mode))};")


# A path field that is not focused shows the end of its path, with `data-path-cut` on the
# field while the start is out of view. The mark is an attribute because Vue rewrites a
# field's `class` whenever the server redraws it, and a class set here is lost with it. A
# field settles when it arrives, when its width changes, when its value is set (by Vue, as
# a property, which no event reports) and when focus leaves; `managerPathEnd` is the
# server's own call after it sets one.
PATH_ENDS_JS = """
(() => {
  const INPUT = 'input.manager-path-input';
  const watched = new WeakSet();
  const settle = (input) => {
    const field = input.closest('.q-field');
    if (!field) return;
    field.removeAttribute('data-path-cut');
    if (document.activeElement === input) return;
    input.scrollLeft = input.scrollWidth;
    if (input.scrollLeft > 0) {
      field.setAttribute('data-path-cut', '');
      input.scrollLeft = input.scrollWidth;
    }
  };
  const later = (input) => requestAnimationFrame(() => settle(input));
  const sized = new ResizeObserver((entries) => entries.forEach((e) => later(e.target)));
  const adopt = (input) => {
    if (watched.has(input)) return;
    watched.add(input);
    const own = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    Object.defineProperty(input, 'value', {
      configurable: true,
      get() { return own.get.call(this); },
      set(text) { own.set.call(this, text); later(this); },
    });
    sized.observe(input);
    later(input);
  };
  const every = () => document.querySelectorAll(INPUT).forEach(adopt);
  window.managerPathEnd = (id) => setTimeout(() => {
    const input = document.getElementById(id);
    if (input) settle(input);
  }, 60);
  for (const type of ['focusin', 'focusout']) {
    document.addEventListener(type, (event) => {
      if (event.target.matches && event.target.matches(INPUT)) {
        setTimeout(() => settle(event.target), 0);
      }
    }, true);
  }
  let waiting = false;
  const brings = (node) => node.nodeType === 1 &&
    (node.matches(INPUT) || node.querySelector(INPUT) !== null);
  new MutationObserver((records) => {
    if (waiting || !records.some((r) => [...r.addedNodes].some(brings))) return;
    waiting = true;
    requestAnimationFrame(() => { waiting = false; every(); });
  }).observe(document.body, {childList: true, subtree: true});
  every();
})();
"""


def apply_flair(mode: str = DEFAULT_MODE) -> None:
    """The palette in the page, the rules from a file.

    The rules are the large half and they cannot vary - they name tokens and never
    colors - so they are the same bytes on every page of every mode, and worth fetching
    once. The palette is the half that changes, it is about 40 lines, and putting it in
    the page is what makes a mode arrive with no second request and no flash.

    Order does not matter between the two. The palette states custom properties and the
    file states rules, so neither can outrank the other: a custom property is resolved
    where it is used, not where it is declared.
    """
    ui.add_head_html(f'<style id="{PALETTE_ID}">{palette_css(mode)}</style>')
    ui.add_head_html(f'<link rel="stylesheet" href="{BASE_HREF}">')
    ui.add_body_html(f"<script>{PATH_ENDS_JS}</script>")
    ui.add_body_html(f"<script>{place.PLACE_JS}</script>")
    ui.add_body_html(f"<script>{link_tab.script(link_tab.configured())}</script>")
    ui.add_head_html(f"<script>{script_failed.script()}</script>")


def apply_surface(name: str) -> None:
    """Say which surface this page is, so the token layer can answer differently.

    Width picks a layout; it must never pick a surface. A tablet held in portrait is
    still a desk session and a phone in landscape is still a phone, so this is set by
    the page rather than by a media query.
    """
    ui.run_javascript(
        f"document.documentElement.dataset.surface = {json.dumps(name)}")


def _token(name: str, mode: str = DEFAULT_MODE) -> str:
    """The value a token carries, read out of the palette that declares it."""
    match = re.search(rf"^\s*{name}:\s*([^;]+);", PALETTES[mode], re.MULTILINE)
    if match is None:
        raise KeyError(name)
    return match.group(1).strip()


_REFERENCE = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*(?:,([^()]*))?\)")


def token(name: str, mode: str = DEFAULT_MODE) -> str:
    """A token's value with every `var()` inside it expanded.

    `_token` gives what the palette says, and a palette may answer with a reference -
    Dark's rail is `var(--surface-1)` and Synthwave's is a gradient between three of
    them. Inside the page that is exactly right, because the reference resolves against
    the palette that declared it. It is wrong anywhere the value has to stand on its
    own, and the appearance picker is that place: it draws all three palettes on a page
    painted in one of them, so an unexpanded reference would make every swatch show the
    colors of the mode already in use.
    """
    def expand(match: re.Match) -> str:
        try:
            return _token(match.group(1), mode)
        except KeyError:
            return (match.group(2) or "").strip()

    value = _token(name, mode)
    # Bounded rather than "until it stops changing": this reads declarations, and a
    # palette with a reference cycle in it should draw a wrong swatch, not hang.
    for _ in range(8):
        grown = _REFERENCE.sub(expand, value)
        if grown == value:
            break
        value = grown
    return value


def _quasar_brand(mode: str) -> dict[str, str]:
    """The brand set Quasar paints its own components with.

    Read off the tokens rather than kept beside them. A second copy of the palette is a
    second palette, and it drifts - a third amber beside the two the stylesheet already
    collapses into one.
    """
    return {
        "primary": _token("--flair", mode),
        "secondary": _token("--accent", mode),
        # Quasar carries three slots for a second brand color and the Manager designs
        # with one. All three take it, so a component reaching for any of them cannot
        # land on Quasar's stock purple.
        "accent": _token("--accent", mode),
        "positive": _token("--positive", mode),
        # Not --danger: Quasar fills an error toast with `negative` and writes white on
        # it, and the color that reads as text on a panel is not the one that carries
        # white on top. Two colors, one slot, so the palette names both.
        "negative": _token("--danger-fill", mode),
        "warning": _token("--warm", mode),
        "info": _token("--accent", mode),
        "dark": _token("--surface-2", mode),
        "dark_page": _token("--surface-0", mode),
    }


def _quasar_vars(mode: str) -> str:
    """The same brand set as CSS, for the mode that cannot be handed one.

    `ui.colors()` takes one value each and `system` has two, picked in the browser, so
    system states them in a media query instead.

    On `body` and `!important`, which neither of the other modes needs. Quasar writes its
    brand as an inline style on the body at boot - from NiceGUI's defaults, before any
    page of ours runs - and an inline style beats any `:root` rule whatever its
    specificity. A stylesheet `!important` is what outranks one, and it is the whole
    reason this is not simply more of the palette block above.
    """
    return "body {\n" + "".join(
        f"  --q-{name.replace('_', '-')}: {value} !important;\n"
        for name, value in _quasar_brand(mode).items()) + "}\n"


def apply_colors(mode: str = DEFAULT_MODE) -> None:
    """Hand Quasar the brand set for this mode, where there is one to hand it.

    `system` is handled in `palette_css` instead, and calling this for it would pick one
    of its two palettes and pin the framework to that one whatever the browser reports.
    """
    if mode == SYSTEM:
        return
    ui.colors(**_quasar_brand(mode))


