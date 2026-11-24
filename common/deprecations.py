"""Every compatibility shim 3.0 carries, declared in one place.

3.0 renames a lot of names a user or a theme already depends on, and each rename left a
shim behind so nothing breaks. Those shims grew one at a time, in the module that needed
one, and by the end nobody could answer three questions about them:

  - what is there?          eight mechanisms, in six files, found by grepping
  - is it written down?     four had a PAR id; PAR-24 was cited in a handoff and did
                            not exist, so the window messages shipped half-done
  - can it ever be removed?  only one announced itself, so there was no evidence about
                            who still relied on any of the others

This module answers the first, and `tests/invariants/test_deprecations.py` turns the second
into a failing test rather than an intention. The third needs the shims to say something when
they are used, which is `announce()`.

Nothing here changes behavior. Each entry points at the code that already does the
forwarding; this is the description of it, kept next to nothing else so it cannot drift
into being a partial list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("vpinfe.common.deprecations")


@dataclass(frozen=True)
class Shim:
    """One compatibility mechanism, and what a caller reaching it is still using.

    `par` is the ledger id in docs/compatibility-3.0.md. Everything user-visible needs
    one - that is what the ledger is - so an entry without one is a gap, not a style
    choice, and the test says so.
    """

    key: str
    surface: str
    summary: str
    implemented_in: str
    par: str | None = None
    names: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # Set where reaching the name does not work. The log has to say which, or a
    # maintainer reading it goes looking for a call that never happened.
    refused: bool = False

    @property
    def count(self) -> int:
        return len(self.names) or 1


# Ordered by how visible the surface is, not by when it was written.
SHIMS: tuple[Shim, ...] = (
    Shim(
        key="theme-payload-keys",
        surface="theme payload",
        summary="Row keys renamed at contract 2; contract 1 gets the old spellings back "
                "through the projection, so a 2.x theme is unaffected.",
        implemented_in="frontend/theme_contract.py:_LEGACY_ROW_KEYS",
        par="PAR-22",
        names=(
            ("TableImagePath", "PlayfieldImagePath"),
            ("TableVideoPath", "PlayfieldVideoPath"),
            ("fullPathTable", "fullPathGame"),
            ("tableDirName", "gameDirName"),
        ),
    ),
    Shim(
        key="theme-payload-sections",
        surface="theme payload",
        summary="meta.VPXFile and meta.VPinFE are synthesised for contract 1 from the "
                ".info's own meta.tables and meta.vpinfe.",
        implemented_in="frontend/theme_contract.py:_to_contract_1",
        par="PAR-22",
    ),
    Shim(
        key="vpin-members",
        surface="theme JavaScript",
        summary="Renamed vpin.* members stay as accessors forwarding to their "
                "replacements. There is no projection for a JS API, so aliasing is the "
                "only mechanism available - these work at every contract.",
        implemented_in="frontend/static/common/vpinfe-core.js:VPINFE_RENAMED_MEMBERS",
        par="PAR-23",
        names=(
            ("tableRotation", "playfieldRotation"),
            ("tableOrientation", "playfieldOrientation"),
        ),
    ),
    Shim(
        key="overlay-surface",
        surface="theme JavaScript",
        summary="The three overlay booleans and the six methods that named one overlay "
                "each are derived from vpin.overlay and the two methods that take an "
                "overlay's name. Derived rather than forwarded, so they are not in "
                "VPINFE_RENAMED_MEMBERS.",
        implemented_in="frontend/static/common/vpinfe-core.js:VPINFE_OVERLAY_ALIASES",
        par="PAR-86",
        names=(
            ("menuUP", "overlay"),
            ("collectionMenuUP", "overlay"),
            ("tutorialUP", "overlay"),
            ("toggleMenu", "toggleOverlay"),
            ("toggleCollectionMenu", "toggleOverlay"),
            ("toggleTutorial", "toggleOverlay"),
            ("registerInputHandlerMenu", "registerOverlayHandler"),
            ("registerInputHandlerCollectionMenu", "registerOverlayHandler"),
            ("registerInputHandlerTutorial", "registerOverlayHandler"),
        ),
    ),
    Shim(
        key="ws-methods",
        surface="theme WebSocket API",
        summary="Pre-rename method names forward to their replacements through "
                "__getattr__, so a theme calling the old name still gets an answer.",
        implemented_in="frontend/api.py:_RENAMED_METHODS",
        par="PAR-21",
        names=(
            ("get_table_rating", "get_game_rating"),
            ("set_table_rating", "set_game_rating"),
            ("get_table_orientation", "get_playfield_orientation"),
            ("get_table_rotation", "get_playfield_rotation"),
            ("get_manager_ui_port", "get_http_port"),
            ("get_hub_port", "get_http_port"),
            ("get_managerui_remote_link", "get_remote_link"),
            ("get_managerui_vpinplay_multi_link", "get_vpinplay_multi_link"),
        ),
    ),
    Shim(
        key="theme-internal-methods",
        surface="theme JavaScript",
        summary="Five sort and filter methods core's collection menu owns. Still "
                "dispatched, because that overlay is an iframe with no other route in, "
                "but vpin.call refuses them so a theme cannot reach them by name.",
        implemented_in="frontend/api.py:API_INTERNAL_METHODS "
                       "+ frontend/static/common/vpinfe-core.js:INTERNAL_METHODS",
        par="PAR-84",
        refused=True,
    ),
    Shim(
        key="ini-renamed-keys",
        surface="vpinfe.ini",
        summary="Renamed keys are read once under the old name and written back under "
                "the new one, so an existing vpinfe.ini is corrected in place.",
        implemented_in="common/config/config_store.py:_RENAMED_KEYS",
        par="PAR-25",
        names=(
            ("tablescreenid", "playfieldscreenid"),
            ("tableorientation", "playfieldorientation"),
            ("tablerotation", "playfieldrotation"),
            ("tablerootdir", "gamerootdir"),
            ("tabletype", "playfieldvariant"),
            ("tableresolution", "playfieldresolution"),
            ("tablevideoresolution", "playfieldvideoresolution"),
            ("tablemediapriority", "playfieldmediapriority"),
        ),
    ),
    Shim(
        key="ini-moved-options",
        surface="vpinfe.ini",
        summary="Options that changed section rather than name. Predates 3.0; listed "
                "here because it is the same promise to the same file.",
        implemented_in="common/config/config_store.py:_MOVED_OPTIONS",
        par="PAR-25",
        names=(
            ("Settings.cabmode", "Displays.cabmode"),
            ("Settings.enabledof", "DOF.enabledof"),
            ("Displays.splashscreen", "Settings.splashscreen"),
        ),
    ),
    Shim(
        key="cli-game-flag",
        surface="CLI",
        summary="--table is accepted as a hidden alias for --game and kept out of "
                "--help, so a script written against 2.x keeps running.",
        implemented_in="cli.py",
        par="PAR-26",
        names=(("--table", "--game"),),
    ),
)

SHIMS_BY_KEY = {shim.key: shim for shim in SHIMS}


_seen: set[tuple[str, str]] = set()


def announce(key: str, used: str) -> None:
    """Record that something reached a legacy name. Once per name, per process.

    The question a maintainer has is "is anything still on the old name", not "how
    often" - and the payload projection runs per game per refresh, so counting would
    bury the answer. First use says so; the rest are silent.

    INFO rather than WARNING: nearly all of these are working, supported paths, and
    warning about them would train people to ignore warnings. A refused one stays INFO
    too - it is evidence for a retirement, not a fault the user can act on.
    """
    pair = (key, used)
    if pair in _seen:
        return
    _seen.add(pair)

    shim = SHIMS_BY_KEY.get(key)
    replacement = dict(shim.names).get(used, "") if shim else ""
    logger.info("deprecated: %s %r %s%s (%s)",
                shim.surface if shim else key, used,
                "was called and refused" if shim and shim.refused else "is in use",
                f"; the current name is {replacement}" if replacement else "",
                shim.par if shim and shim.par else "unledgered")


def reset_for_tests() -> None:
    """Forget what has been announced, so one test cannot silence the next."""
    _seen.clear()
