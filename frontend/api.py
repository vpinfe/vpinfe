"""What a theme is allowed to ask for, and what only core is.

The WebSocket bridge dispatches by name and reaches nothing outside
`API_ALLOWED_METHODS`, which is two sets. `API_PUBLISHED_METHODS` is the theme
surface - docs/theme.md documents it and the parity gate holds it to that, so adding a
name there is adding to the theme contract. `API_INTERNAL_METHODS` is core's own: the
overlays VPinFE ships call them across an iframe, which is why they stay dispatchable,
and `vpin.call` refuses them so they are not theme API.

Renamed methods keep their old spelling as an alias rather than breaking a published
theme.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from common import events
from common.capture import freeze
from common.capture import preflight as capture_preflight
from common.capture import run as capture_run
from common.deprecations import announce
from common.extensions import temporary_profiles
from common.failures import why
from common.games import game_identity
from common.games.collection_store import (
    BUILTIN_ALL,
    DEFAULT_DIRECTION,
    DEFAULT_ORDER_BY,
    normalize_direction,
    public_name,
)
from common.games.game_metadata import game_rating, normalize_meta, set_game_rating
from common.games.game_repository import all_games
from common.games.player_records import get_records
from common.host import frontend_browser, frontend_state, launch, launch_state
from common.host.display_service import monitors_as_dicts
from common.i18n import t
from common.players.players import get_roster
from common.runtime import lifecycle
from common.service_errors import NotFoundError, ServiceError
from frontend import (
    config_api,
    game_state,
    input_api,
    last_game,
    lifecycle_host,
    metadata_build_service,
    preview,
    sign_in_prompt,
    theme_api,
    theme_windows,
)
from frontend import library_resolver as frontend_library
from frontend.theme_contract import CURRENT_CONTRACT, declared_contract

if TYPE_CHECKING:
    from common.config.config_store import ConfigStore
    from common.games.collection_resolver import Entry
    from common.games.game import Game
    from frontend.chromium_manager import ChromiumManager
    from frontend.device_channel import DeviceChannel


logger =logging.getLogger("vpinfe.frontend.api")

# A button or key in the form a Manager stores: `pad:standard/button:4`, `pad:2/button:0`,
# `key:KeyN`.
_INPUT_TOKEN = re.compile(r"pad:(?:standard|\d{1,3})/button:\d{1,3}|key:[A-Za-z0-9]{1,32}")

_FILTER_OPTION_KEYS = {
    "letters": "letters",
    "themes": "themes",
    "types": "types",
    "manufacturers": "manufacturers",
    "years": "years",
}


API_PUBLISHED_METHODS = {
    'get_my_window_name',
    'close_app',
    'shutdown_system',
    'lifecycle_request',
    'lifecycle_needs_confirmation',
    'get_monitors',
    'get_tables',
    'get_initial_table_index',
    'get_collections',
    'get_collections_metadata',
    'get_collection_image_url',
    'set_tables_by_collection',
    'save_filter_collection',
    'get_current_collection',
    'get_filter_letters',
    'get_filter_themes',
    'get_filter_types',
    'get_filter_manufacturers',
    'get_filter_years',
    'get_page_index',
    'reset_filters',
    'console_out',
    'get_bindings',
    'get_joymaping',
    'get_keymapping',
    'get_mainmenu_config',
    'set_button_mapping',
    'launch_table',
    'notify_table_selected',
    'get_game_rating',
    'set_game_rating',
    'build_metadata',
    'get_theme_config',
    'get_theme_name',
    'get_media_priorities',
    'get_vpinplay_endpoint',
    'refresh_entry_data',
    'get_temporary_vpinplay_profile',
    'set_temporary_vpinplay_profile',
    'clear_temporary_vpinplay_profile',
    'get_playfield_orientation',
    'get_playfield_rotation',
    'get_splashscreen_enabled',
    'get_audio_muted',
    'set_audio_muted',
    'get_cab_mode',
    'get_playfield_media_rotation',
    'get_theme_assets_port',
    'get_http_port',
    'get_remote_link',
    'get_manager_link',
    'get_vpinplay_multi_link',
    'get_theme_index_page',
    # Additive: the roster and who is up, for a theme that wants to show it. Kept live
    # by the PlayersChanged window message rather than re-asked for.
    'get_players',
    # Additive: the contract a theme declared, so vpinfe-core.js can serve the surface
    # that theme asked for rather than every surface at once.
    'get_theme_contract',
    # The windows the theme declared, controller first, so the browser knows which
    # window it is without a hardcoded name.
    'get_theme_windows',
    'send_event',
    'send_event_all_windows',
    'send_event_all_windows_incself',
    # Additive, so no contract bump: a theme that never calls it is unaffected.
    'report_deprecated_use',
}


# The collection menu's own controls. Core ships that overlay, it runs as its own iframe,
# and this channel is the only way it can reach the library - so the names stay
# dispatchable while `vpin.call` refuses them.
API_INTERNAL_METHODS = {
    'apply_filters',
    'library_waiting',
    'get_collection_picker_items',
    'apply_sort',
    'get_current_filter_state',
    'get_current_sort_state',
    'get_current_order_state',
    'get_paging_state',
    'keep_filter_collection',
    # Core's own probe of the browser it runs in. A theme reporting here would be
    # rewriting what the Manager says the cabinet can play.
    'report_browser',
    # Take Picture and Back while a table runs. A theme calling them would pause a game
    # nobody asked to pause.
    'take_picture',
    'resume_play',
    # Exit's Stop recording? on the cabinet. A theme calling it would end a recording
    # run nobody at the cabinet asked to end.
    'stop_recording',
    # The main menu's Record Media, and the buttons' answers to a recording shown for a
    # decision. A theme calling them would record, replace or throw away media.
    'recording_offer',
    'record_media',
    'switch_preview',
    'decide_preview',
    'end_preview',
    # The sign-in code on the machine's screen. A theme calling them would read the digits
    # or press Show for somebody who asked for a code from the other side of the house.
    'signin_pending',
    'signin_show',
    'signin_ignore',
    'signin_dismiss',
    'approval_approve',
    'approval_decline',
    # The main menu's Player list and its Rating dialog asking whose. A theme reads who
    # is up through get_players/PlayersChanged; only core's own overlay writes it.
    'set_player_up',
    'set_player_rating',
    'get_player_rating',
    # What the person set for attract mode, asked by core when it starts its timers.
    # A theme reads its own `attract` block from its config.json instead.
    'get_attract_config',
    # The controller window's report of a button pressed while a Manager on another
    # device is listening. A theme calling it would type into that Manager's field.
    'input_capture',
    # A button held at the cabinet taking the controller back from every Manager page.
    'input_capture_release',
}


# Themes written before the vocabulary rename call these names. The allowlist carries
# both spellings and __getattr__ forwards the old one, so an existing theme keeps working
# without a contract bump - the payload it gets back is identical either way.
#
# The selection surface is not in here: a row is a table, so `get_tables`, `launch_table`
# and their siblings are the current names rather than forwarded ones.
_RENAMED_METHODS = {
    'get_table_rating': 'get_game_rating',
    'set_table_rating': 'set_game_rating',
    'get_table_orientation': 'get_playfield_orientation',
    'get_table_rotation': 'get_playfield_rotation',
    # The port serves the API, the Manager and the Remote, so it is named for the protocol
    # rather than one thing listening on it. Two retired spellings rather than one: it was
    # named for 2.x's Manager UI, then for a role that no longer exists. Neither reached a
    # release, but a theme built against a 3.0 dev build has seen the second.
    'get_manager_ui_port': 'get_http_port',
    'get_hub_port': 'get_http_port',
    # The two QR codes on the main menu; named for the page they opened in 2.x.
    'get_managerui_remote_link': 'get_remote_link',
    'get_managerui_vpinplay_multi_link': 'get_vpinplay_multi_link',
}

API_PUBLISHED_METHODS |= set(_RENAMED_METHODS)

# What the channel dispatches. An overlay's call arrives the same way a theme's does, so
# the gate that separates them is in the browser rather than here.
API_ALLOWED_METHODS = API_PUBLISHED_METHODS | API_INTERNAL_METHODS


class API:

    """One instance per frontend window. Only methods in API_ALLOWED_METHODS are reachable."""

    def __getattr__(self, name: str) -> Any:
        """Forward a pre-rename method name to its replacement.

        Only reached when normal lookup fails, so it costs nothing for current names.
        """
        renamed = _RENAMED_METHODS.get(name)
        if renamed is None:
            raise AttributeError(name)
        announce("ws-methods", name)
        return getattr(self, renamed)

    def report_deprecated_use(self, key: object, name: object) -> None:
        """Let the browser tell the log it used a legacy name.

        A theme runs in Chromium, so its use of a vpin.* alias is only visible in a
        browser log nobody reads on a cabinet. The WebSocket methods announce themselves
        here already; without this the eleven JS aliases are the one surface that
        cannot be judged from the machine, which is where they would be retired from.

        vpinfe-core.js calls this once per name. Untrusted input, so it only ever
        reaches announce(), which looks both up in the registry and logs.
        """
        announce(str(key), str(name))
    def __init__(self, ini_config: ConfigStore, window_name: str | None = None,
                 ws_bridge: DeviceChannel | None = None,
                 frontend_browser: ChromiumManager | None = None,
                 library: frontend_library.LibraryResolver | None = None) -> None:
        self._ini_config = ini_config
        self.window_name = window_name          # whatever the theme declared
        self.ws_bridge = ws_bridge              # WebSocketBridge instance
        self.frontend_browser = frontend_browser  # ChromiumManager instance
        self._waited = False
        # The wheel's state, shared with every other window onto the same library.
        # Given one when the frontend builds the windows; a caller that constructs an
        # API on its own - a test, the gamepad diagnostic - gets a view of its own.
        if library is not None:
            self.library = library
        self.js_game_dict_data: str | None = None
        # Check for startup collection
        startup_collection = last_game.opening_collection(self._ini_config)
        if startup_collection:
            try:
                self.set_tables_by_collection(startup_collection)
            except KeyError:
                logger.warning("Startup collection '%s' no longer exists; showing All Games",
                               startup_collection)
                last_game.remember_collection(self._ini_config, BUILTIN_ALL)
            except Exception:
                logger.exception("Could not load startup collection '%s'", startup_collection)

    # The library's state, reached through the window that is showing it. Properties
    # rather than a move, so every existing caller - and `game_state`, which mutates
    # these by name - keeps working against one shared object instead of a copy per
    # window.
    @property
    def library(self) -> frontend_library.LibraryResolver:
        """The shared resolver, made on demand for a caller that never gave one.

        `API.__new__(API)` is a real pattern here: paging arithmetic and input mapping
        are tested against a bare instance with no library behind it. Those get a view
        of their own rather than an AttributeError routed through `__getattr__`, which
        reports the wrong name entirely.
        """
        existing = self.__dict__.get("_library")
        if existing is None:
            # Loaded through this module's name, which is the one callers already patch
            # to stand a library up for a test - the resolver is an implementation
            # detail of where the games are held, not of how they are found.
            existing = frontend_library.LibraryResolver(getattr(self, "_ini_config", None),
                                          games=all_games())
            self.__dict__["_library"] = existing
        return existing

    @library.setter
    def library(self, value: frontend_library.LibraryResolver) -> None:
        self.__dict__["_library"] = value

    @property
    def all_games(self) -> list[Any]:
        return self.library.all_games

    @all_games.setter
    def all_games(self, value: list[Any]) -> None:
        self.library.all_games = value

    @property
    def filtered_games(self) -> list[Any]:
        return self.library.filtered_games

    @filtered_games.setter
    def filtered_games(self, value: list[Any]) -> None:
        self.library.filtered_games = value

    @property
    def current_filters(self) -> dict[str, Any]:
        return self.library.current_filters

    @current_filters.setter
    def current_filters(self, value: dict[str, Any]) -> None:
        self.library.current_filters = value

    @property
    def current_collection(self) -> str:
        return self.library.current_collection

    @current_collection.setter
    def current_collection(self, value: str) -> None:
        self.library.current_collection = value
        if (ini_config := getattr(self, "_ini_config", None)) is not None:
            last_game.remember_collection(ini_config, value)

    @property
    def current_sort(self) -> str:
        return self.library.current_sort

    @current_sort.setter
    def current_sort(self, value: str) -> None:
        self.library.current_sort = value

    @property
    def current_order(self) -> str:
        return self.library.current_order

    @current_order.setter
    def current_order(self, value: str) -> None:
        self.library.current_order = value

    ####################
    ## Private Functions
    ####################

    def _finish_setup(self) -> None:
        pass

    def _normalize_game_meta(self, game: Game) -> dict[str, Any]:
        return normalize_meta(game.meta_config)

    def _theme_contract(self) -> int:
        """Which shape the active theme asked for. Read per payload rather than cached,
        so switching themes does not need a restart to take effect."""
        theme_dir = theme_api.resolve_theme_dir(theme_api.get_theme_name(self._ini_config.config))
        return declared_contract(theme_dir) if theme_dir else CURRENT_CONTRACT

    def _reset_to_default_view(self) -> None:
        """Reset the shared view to its default order. See `View.reset_to_default`."""
        self.library.reset_to_default()
        self.current_collection = BUILTIN_ALL

    def _rebuild_entries(self) -> None:
        """Recompute the shared view. Called whenever the list or its order changes."""
        self.library.rebuild_entries()

    @property
    def entries(self) -> list[Entry]:
        """What the wheel steps through, and what an index from a theme addresses.

        The view's list, not this window's: every window onto the same library steps
        through the same entries, and only the controller can change them.
        """
        return self.library.entries

    def entry_at(self, index: Any) -> Entry | None:
        """The entry a theme's index names, or None when it names nothing.

        An index is a position in *this window's* filtered list, so it means nothing
        anywhere else - two windows filtered differently give the same number to
        different games. Anything leaving this process converts through here first.
        """
        try:
            position = int(index)
        except (TypeError, ValueError):
            return None
        # Not `entries[-1]`: a theme counts up from zero, so a negative number is one
        # that went wrong, and Python would quietly hand back the end of the list.
        if position < 0:
            return None
        try:
            return self.entries[position]
        except IndexError:
            return None

    def game_id_at(self, index: Any) -> str:
        """The id of the game an index names, or "" - the addressable form of an index."""
        entry = self.entry_at(index)
        return game_identity.game_id(entry.game) if entry is not None else ""


    ###################
    ## Public Functions
    ###################

    def get_my_window_name(self) -> str:
        return self.window_name or "unknown"

    def _origin(self) -> lifecycle.Origin:
        """This window is the address a confirmation goes back to."""
        return lifecycle.Origin(lifecycle.SURFACE_FRONTEND, self.window_name or "")

    def close_app(self) -> bool:
        """Quit VPinFE. The 2.x spelling of `lifecycle_request('vpinfe', 'stop')`."""
        return self.lifecycle_request(lifecycle.VPINFE, lifecycle.STOP)

    def shutdown_system(self) -> bool:
        """Power off the host. The 2.x spelling of `lifecycle_request('system', 'stop')`."""
        return self.lifecycle_request(lifecycle.SYSTEM, lifecycle.STOP)

    def lifecycle_needs_confirmation(self, scope: object,
                                     action: object) -> dict[str, Any]:
        """Whether to ask the user first, and what to ask - the browser draws the dialog.

        The question is put where the request came from, and the bridge to a window only
        goes one way, so the confirmation happens in the browser and calls back with the
        answer. The wording comes from here so every surface asks the same thing.
        """
        scope, action = lifecycle_host.effective(
            str(scope), str(action), lifecycle.SURFACE_FRONTEND)
        return {
            "confirm": lifecycle_host.wants_confirmation(str(scope)),
            # Already sentence-cased - capitalize() here would lowercase "VPinFE".
            "description": lifecycle.label(str(scope), str(action)),
        }

    def lifecycle_request(self, scope: str, action: str, reason: str = "",
                          confirmed: object = False) -> bool:
        """Start, stop or restart the frontend, VPinFE or the machine.

        `confirmed` is the theme reporting that it already asked. A theme that never
        asks - every 2.x theme - is answered by the core's own fallback, so turning the
        setting on is never defeated by an old theme.

        Returns whether it is going ahead, so a theme can leave its own menu open when
        the user says no.
        """
        try:
            return lifecycle_host.request(
                scope, action, origin=self._origin(), reason=reason,
                already_confirmed=bool(confirmed))
        except ValueError:
            logger.warning("Window '%s' asked to %s the %s, which is not a thing",
                           self.window_name, action, scope)
            return False

    def get_monitors(self) -> list[dict[str, Any]]:
        return monitors_as_dicts()

    def send_event_all_windows(self, message: dict[str, Any]) -> None:
        if self.ws_bridge:
            self.ws_bridge.send_event_all(message, exclude=self.window_name)

    def send_event(self, window_name: str, message: dict[str, Any],
                   forward_iframe: bool = False) -> None:
        if self.ws_bridge:
            self.ws_bridge.send_event(window_name, message, forward_iframe)

    def send_event_all_windows_incself(self, message: dict[str, Any]) -> None:
        if self.ws_bridge:
            self.ws_bridge.send_event_all_with_iframe(message)

    def get_tables(self, reset: bool = False) -> str:
        if reset:
            self._reset_to_default_view()
        else:
            # Re-derived once per change, not once per window: all three ask after the
            # same TableDataChange broadcast, and the second and third were rebuilding a
            # view the first had just rebuilt.
            self.library.refresh_if_stale(lambda: game_state.refresh_view(self))
        # Built once for the view, not once per window: three windows onto the same
        # library were each serializing an identical answer.
        self.js_game_dict_data = self.library.payload(
            self._theme_contract(), collection=public_name(self.current_collection))
        if not self.entries:
            frontend_state.showing(public_name(self.current_collection))
        return self.js_game_dict_data

    def get_initial_table_index(self) -> int:
        # Position the wheel on the last-launched game at startup. Resolved
        # against the current (possibly filtered) view; 0 when disabled or unfound.
        return last_game.resolve_last_table_index(self._ini_config, self.entries)


    def get_collections(self) -> list[str]:
        return [row["name"] for row in
                self.library.offered(public_name(self.current_collection))]

    def get_collections_metadata(self) -> list[dict]:
        return self._glanced(self.library.offered(public_name(self.current_collection)))

    def get_collection_picker_items(self) -> list[dict]:
        """All Games, then what `get_collections_metadata` offers."""
        showing = public_name(self.current_collection)
        whole = {"name": "", "image": "", "image_url": ""}
        rows = self._glanced([whole, *self.library.offered(showing)])
        return [row | {"showing": row["name"] == showing} for row in rows]

    def _glanced(self, rows: list[dict]) -> list[dict]:
        names = [row["name"] or BUILTIN_ALL for row in rows]
        glances = self.library.glance(names)
        # The row wins: a library's rows arrive counted by the install that holds it.
        return [glances[name] | row for row, name in zip(rows, names, strict=True)]

    def get_collection_image_url(self, collection: str) -> str:
        return self.library.image_url(collection)

    def set_tables_by_collection(self, collection: str) -> None:
        """Set filtered games based on collection from collections.ini."""
        game_state.apply_collection(self, collection)

    def save_filter_collection(
        self,
        name: str,
        letter: str = "All",
        theme: str = "All",
        game_type: str = "All",
        manufacturer: str = "All",
        year: str = "All",
        order_by: str = DEFAULT_ORDER_BY,
        rating: str = "All",
        rating_or_higher: object = False,
        direction: str = DEFAULT_DIRECTION,
    ) -> dict[str, Any]:
        """Save current filter settings as a named collection."""
        try:
            return game_state.save_current_filter_collection(
                self, name, letter, theme, game_type, manufacturer, year, order_by,
                rating, rating_or_higher, direction,
            )
        except (ValueError, ServiceError) as e:
            return {"success": False, "message": why(e)}

    def keep_filter_collection(
        self,
        name: str,
        letter: str = "All",
        theme: str = "All",
        game_type: str = "All",
        manufacturer: str = "All",
        year: str = "All",
        order_by: str = DEFAULT_ORDER_BY,
        rating: str = "All",
        rating_or_higher: object = False,
        direction: str = DEFAULT_DIRECTION,
    ) -> dict[str, Any]:
        """`save_filter_collection` once per set of rules, under a free name: answers the
        name it is kept under, and whether a collection already held those rules."""
        try:
            return game_state.keep_filter_collection(
                self, name, letter, theme, game_type, manufacturer, year, order_by,
                rating, rating_or_higher, direction,
            )
        except (ValueError, ServiceError) as e:
            return {"success": False, "message": why(e)}

    def get_current_filter_state(self) -> dict[str, Any]:
        """Return current filter state for UI synchronization."""
        return self.current_filters

    def get_current_sort_state(self) -> str:
        """Return current sort state for UI synchronization."""
        return self.current_sort

    def get_current_order_state(self) -> str:
        """Return current sort order for UI synchronization."""
        return self.current_order

    def get_current_collection(self) -> str:
        """Return current collection name for UI synchronization. The whole library has
        always answered 'None' here, and that is what `builtin:all` is."""
        return public_name(self.current_collection) or 'None'

    def _filter_option(self, key: str) -> list[str]:
        return game_state.filter_options(self.library.whole())[key]

    def get_filter_letters(self) -> list[str]:
        return self._filter_option(_FILTER_OPTION_KEYS["letters"])

    def get_filter_themes(self) -> list[str]:
        return self._filter_option(_FILTER_OPTION_KEYS["themes"])

    def get_filter_types(self) -> list[str]:
        return self._filter_option(_FILTER_OPTION_KEYS["types"])

    def get_filter_manufacturers(self) -> list[str]:
        return self._filter_option(_FILTER_OPTION_KEYS["manufacturers"])

    def get_filter_years(self) -> list[str]:
        return self._filter_option(_FILTER_OPTION_KEYS["years"])

    def apply_filters(self, letter: str | None = None, theme: str | None = None,
                      game_type: str | None = None, manufacturer: str | None = None,
                      year: str | None = None, rating: str | None = None,
                      rating_or_higher: object = None) -> int:
        """
        Apply VPSdb filters to the full game list.
        These filters work independently of collections.
        Returns the count of filtered games.
        """
        logger.debug(
            "Applying filters: letter=%s, theme=%s, type=%s, manufacturer=%s, "
            "year=%s, rating=%s, rating_or_higher=%s",
            letter,
            theme,
            game_type,
            manufacturer,
            year,
            rating,
            rating_or_higher,
        )
        count = game_state.apply_filters(self, letter, theme, game_type, manufacturer, year, rating,
                rating_or_higher)
        logger.debug("Filtered games count: %s", count)
        return count

    def reset_filters(self) -> None:
        """Reset all VPSdb filters back to full game list."""
        self.current_filters = game_state.default_filter_state()
        self._reset_to_default_view()

    def apply_sort(self, order_by: str, direction: str | None = None) -> int:
        """
        Sort the current filtered games.
        order_by: one of the orders a collection can carry - 'title', 'year', 'added',
        'last_played', 'play_count', 'play_time_seconds', 'rating'. The 2.x names still
        arrive from a stored filter and resolve.
        direction: 'asc' or 'desc'.

        A stored filter writes the 2.x key `order_by` for a direction, which is the
        opposite of what the word means here and one layer below in
        game_state.apply_sort. It is normalized on the way in; nothing in code repeats it.
        Returns the count of sorted games.
        """
        self.current_sort = order_by
        # No direction asked for means descending, which is what the menu offers first
        # and what a bare apply_sort has always done.
        self.current_order = normalize_direction(direction or "desc")
        logger.debug("Applying sort: %s %s", order_by, self.current_order)

        count = game_state.apply_sort(self.filtered_games, order_by, self.current_order)
        self._rebuild_entries()
        logger.debug("Sorted %s games by %s %s", count, order_by, self.current_order)
        return count

    def paging_state(self) -> dict[str, Any]:
        """What a page press does on the list showing now.

        The collection's own choice if it made one, otherwise the player's. Resolved in
        one place because two answers to this question is how the wheel and the menu
        would come to disagree about what a press just did.
        """
        default, page_size = input_api.get_paging_config(self._ini_config.config)
        name = self.current_collection
        chosen = self.library.paging_group(name) if name else None
        group = chosen or default
        kind = game_state.group_kind(self.current_sort)
        # Asking for the sort's groups where the sort has none gets a count anyway.
        if group == "sort" and not kind:
            group = "count"
        return {"group": group, "kind": kind if group == "sort" else "",
                "size": page_size}

    def get_paging_state(self) -> dict[str, Any]:
        """The same, for a surface that wants to say what a press will do."""
        return self.paging_state()

    def get_page_index(self, index: Any, direction: str) -> int:
        """
        Compute the target wheel index for a page next/prev request.
        Paging behavior comes from [input] paging_group/paging_size and the order
        on screen, which is what decides the groups; see game_state.page_jump_index.
        """
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = 0
        paging = self.paging_state()
        return game_state.page_jump_index(
            [e.game for e in self.entries], index, direction, self.current_sort,
            paging["group"], paging["size"]
        )

    def report_browser(self, report: Any) -> dict[str, Any]:
        """What this browser plays, as the controller window probed it."""
        return frontend_browser.record(report)

    def take_picture(self) -> dict[str, Any]:
        """Take Picture pressed while a table runs: freezes it, or takes the picture."""
        return freeze.take_picture()

    def input_capture(self, token: Any, phase: Any) -> dict[str, Any]:
        """A controller button or key went down or up while a Manager listens."""
        token = str(token or "")
        if not _INPUT_TOKEN.fullmatch(token) or phase not in ("down", "up"):
            return {"relayed": False}
        events.emit(events.INPUT_CAPTURE_PRESS, token=token, phase=phase)
        return {"relayed": True}

    def input_capture_release(self) -> dict[str, Any]:
        """A button was held at the cabinet long enough to take the controller back."""
        events.emit(events.INPUT_CAPTURE_RELEASED)
        return {"released": True}

    def resume_play(self) -> dict[str, Any]:
        """Back pressed while a table runs: resumes it where it is frozen."""
        return freeze.resume()

    def stop_recording(self) -> dict[str, Any]:
        """Ends the recording run, keeping what earlier games placed."""
        try:
            return {"run": capture_run.stop()}
        except NotFoundError:
            return {"run": None}

    def recording_offer(self, index: Any) -> dict[str, Any]:
        """The main menu's Record item for the game at `index`: its label and the
        choices under it, or {} where this device records nothing for it."""
        entry = self.entry_at(index)
        game = entry.game if entry is not None else None
        if game is None:
            return {}
        report = capture_preflight.report(probe_hardware=False)
        if not report["available"]:
            return {}
        game_id = game_identity.game_id(game)
        try:
            planned = capture_run.plan(capture_run.Request(games=[game_id]), report)
        except ServiceError:
            logger.exception("Could not plan a recording of %s", game.game_dir_name)
            return {}
        open_kinds = [kind for kind in planned["kinds"] if not kind["reason"]]
        missing = any(kind["missing"] for kind in open_kinds)
        choices = ([{"existing": capture_run.FILL,
                     "label": t("frontend.mainmenu.missing_only")}] if missing else []) + \
            ([{"existing": capture_run.CHOOSE, "label": t("frontend.mainmenu.replace")}]
             if any(kind["have"] for kind in open_kinds) else [])
        if not choices:
            return {}
        return {"game_id": game_id, "choices": choices,
                "label": t("frontend.mainmenu.record_missing" if missing
                           else "frontend.mainmenu.record_media")}

    def record_media(self, index: Any, existing: str) -> dict[str, Any]:
        """Record the game at `index` with this device's settings: its empty slots, or
        every slot with each file there kept for a decision."""
        entry = self.entry_at(index)
        if entry is None or entry.game is None or existing not in (capture_run.FILL,
                                                                   capture_run.CHOOSE):
            return {}
        job = capture_run.start(capture_run.Request(
            games=[game_identity.game_id(entry.game)], existing=existing))
        preview.follow(job.id)
        return {"job_id": job.id}

    def switch_preview(self, showing: str) -> dict[str, Any]:
        """Left and right on a recording shown for a decision."""
        return preview.switch(str(showing))

    def decide_preview(self, use: Any) -> dict[str, Any]:
        """Select keeps the recording on show, Back throws it away."""
        return preview.decide(bool(use))

    def end_preview(self) -> dict[str, Any]:
        """Exit leaves the recordings on show waiting."""
        preview.end()
        return {}

    def signin_pending(self) -> dict[str, Any] | None:
        """The sign-in request the screen should be showing, or None."""
        return sign_in_prompt.pending()

    def signin_show(self, request_id: Any) -> dict[str, Any] | None:
        """Show: reveal the digits. None when the request has gone."""
        return sign_in_prompt.show(str(request_id))

    def signin_ignore(self, request_id: Any) -> dict[str, Any]:
        """Ignore: refuse this address's requests until restart."""
        sign_in_prompt.ignore(str(request_id))
        return {}

    def signin_dismiss(self, request_id: Any) -> dict[str, Any]:
        sign_in_prompt.dismiss(str(request_id))
        return {}

    def approval_approve(self, ask_id: Any) -> dict[str, Any]:
        """Approve: let the program repeat the one request it asked about."""
        sign_in_prompt.approve(str(ask_id))
        return {}

    def approval_decline(self, ask_id: Any) -> dict[str, Any]:
        sign_in_prompt.decline(str(ask_id))
        return {}

    def console_out(self, output: Any, frame: str = "") -> Any:
        """A line from the browser. `frame` names an overlay within this window.

        The window comes from the connection rather than the caller, so it cannot be
        claimed; the frame is the caller's, because only it knows which iframe it is.
        """
        where = f"{self.window_name}/{frame}" if frame else self.window_name
        logger.info("[%s] %s", where, output)
        return output

    def get_bindings(self) -> dict[str, list[str]]:
        return input_api.get_bindings(self._ini_config.config)

    def get_joymaping(self) -> dict[str, str]:
        return input_api.get_joymapping(self._ini_config.config)

    def get_keymapping(self) -> dict[str, str]:
        return input_api.get_keymapping(self._ini_config.config)

    def get_attract_config(self) -> dict[str, Any]:
        return config_api.get_attract_config(self._ini_config.config)

    def get_mainmenu_config(self) -> dict[str, bool]:
        try:
            return config_api.get_mainmenu_config(self._ini_config)
        except Exception:
            logger.exception("Failed to reload ini before get_mainmenu_config")
            return {"hideQuitButton": False}

    def set_button_mapping(self, button_name: str, button_index: int) -> dict[str, Any]:
        """Set a gamepad button mapping and save to config."""
        return input_api.set_button_mapping(self._ini_config, button_name, button_index)

    def library_waiting(self) -> list[str]:
        """The folders an empty library on this device is waiting on, or none."""
        from common.games import game_repository
        from frontend import play_events

        if self.library.remote:
            return []
        waiting = game_repository.waiting_for()
        if self._waited and not waiting:
            play_events.library_arrived()
        self._waited = bool(waiting)
        return waiting

    def launch_table(self, index: Any) -> dict[str, Any]:
        """Launch what the wheel is sitting on.

        The windows hear about it through the bus like everyone else, so nothing
        here has to tell them.
        """
        entry = self.entry_at(index)
        if entry is None:
            logger.warning("Ignoring launch for invalid index: %s", index)
            return {"success": False, "reason": "invalid_index"}

        game = entry.game
        try:
            # The entry names the table, so the table the collection chose launches
            # rather than whatever the game defaults to.
            launch.launch_game(launch.this_devices_copy(game), self._ini_config,
                               source=launch_state.SOURCE_FRONTEND,
                               table=entry.filename)
        except launch.LaunchUnavailableError as exc:
            logger.warning("Cannot launch %s: %s", game.game_dir_name, exc)
            return {"success": False, "reason": why(exc)}
        return {"success": True}

    def notify_table_selected(self, index: Any) -> dict[str, Any]:
        """Announce that the player moved to this game.

        Whatever reacts - a DOF effect, the real DMD, something not written yet -
        subscribes to the event. Nothing is reported back, because none of it can
        fail in a way the wheel should care about.
        """
        entry = self.entry_at(index)
        if entry is None:
            logger.debug("Ignoring game selection for invalid index: %s", index)
            return {"success": False, "reason": "invalid_index"}
        game = entry.game
        if game is None:
            frontend_state.showing(public_name(self.current_collection))
        else:
            frontend_state.showing(public_name(self.current_collection),
                                   game_identity.game_id(game), game.game_dir_name)

        # Where they are heading, for anything that can usefully get ahead of them. A
        # subscriber that does not care ignores it, the way every subscriber already
        # ignores what it was not written for.
        events.emit(events.GAME_SELECTED, game=game, ini_config=self._ini_config,
                    neighbors=self._neighbors(index))
        return {"success": True}

    def _neighbors(self, index: Any) -> list[Any]:
        """The games either side of the one just selected.

        One step each way and no further: the wheel is turned a step at a time, so the
        next selection is almost always one of these two, and fetching a wider window
        would ask somebody else's server for games nobody is walking towards.
        """
        found = []
        for offset in (1, -1):
            entry = self.entry_at(index + offset)
            if entry is not None and entry.game is not None:
                found.append(entry.game)
        return found

    def refresh_entry_data(self, game_id: object) -> dict:
        """Ask the extensions about this game again, and answer with what they say.

        For a theme that has just changed something an extension reports on - rating a
        table changes what a ratings connector would now say, and the held answer is the
        one from before.
        """
        from common.extensions import contributions, descriptors

        wanted = str(game_id or "").strip()
        entry = next((one for one in self.entries
                      if game_identity.game_id(one.game) == wanted), None)
        if entry is None:
            return {}
        contributions.forget_game(wanted)
        return contributions.refresh(descriptors.game(entry.game))

    def get_vpinplay_endpoint(self) -> str:
        return config_api.get_vpinplay_endpoint(self._ini_config.config)

    def get_game_rating(self, index: Any) -> int:
        """Get User.Rating for a game index in the current filtered list."""
        entry = self.entry_at(index)
        return game_rating(entry.game) if entry is not None else 0

    def set_game_rating(self, index: Any, rating: Any) -> dict[str, Any]:
        """Set User.Rating (0-5) for a game index in the current filtered list.

        The index is converted here; the write itself is the one in common/, so a
        rating set from a theme and one set over HTTP are the same operation.
        """
        entry = self.entry_at(index)
        if entry is None:
            logger.warning("Ignoring rating for invalid index: %s", index)
            return {"success": False, "reason": "invalid_index"}

        stored = set_game_rating(entry.game, rating)
        logger.info("Updated User.Rating for %s -> %s", entry.game.game_dir_name, stored)
        return {"success": True, "rating": stored}

    def get_players(self) -> dict[str, Any]:
        """Everyone on this install and who is up, as `PlayersChanged` carries it."""
        from frontend.play_events import players_for_themes

        return players_for_themes(get_roster().state())

    def set_player_up(self, player_id: Any, up: Any = True) -> dict[str, Any]:
        """Put one player up, or take them down. The main menu's Player list; a theme
        reads who is up rather than setting it."""
        from frontend.play_events import players_for_themes

        get_roster().set_up(str(player_id or "").strip(), bool(up))
        return players_for_themes(get_roster().state())

    def get_player_rating(self, player_id: Any, index: Any) -> int:
        """A player's own rating (0-5) for a game index, for the main menu's Rating
        dialog to preview. The owner's is get_game_rating's, read the same way."""
        entry = self.entry_at(index)
        if entry is None:
            return 0
        player = get_roster().get(str(player_id or "").strip())
        if player is None:
            return 0
        if player.owner:
            return game_rating(entry.game)
        return get_records().game(player, game_identity.game_id(entry.game)).get("rating", 0)

    def set_player_rating(self, player_id: Any, index: Any, rating: Any) -> dict[str, Any]:
        """A non-owner player's own rating (0-5) for a game index - their own record,
        never the library's. The owner writes through set_game_rating instead."""
        entry = self.entry_at(index)
        if entry is None:
            logger.warning("Ignoring rating for invalid index: %s", index)
            return {"success": False, "reason": "invalid_index"}
        player = get_roster().player(str(player_id or "").strip())
        stored = get_records().set_rating(player, game_identity.game_id(entry.game), rating)
        return {"success": True, "rating": stored}

    def build_metadata(self, download_media: bool = True,
                       update_all: bool = False) -> dict[str, Any]:
        """
        Trigger build_metadata from the frontend.
        This runs in a background thread and returns progress/log updates via window events.

        Args:
            download_media: Whether to download media files
            update_all: Whether to update all games (even if the .info exists)

        Returns:
            dict with success status and message
        """
        from common.games import game_service

        return metadata_build_service.start_build(
            self,
            build_metadata_func=game_service.build_metadata,
            all_games_func=all_games,
            download_media=download_media,
            update_all=update_all,
        )

    def get_theme_config(self) -> dict | None:
        return theme_api.get_theme_config(self._ini_config.config)

    ###################
    ### For splash page
    ###################

    def get_splashscreen_enabled(self) -> str:
        return config_api.get_splashscreen_enabled(self._ini_config.config)

    def get_audio_muted(self) -> bool:
        return theme_api.get_audio_muted(self._ini_config.config)

    def set_audio_muted(self, muted: Any) -> bool:
        return config_api.set_audio_muted(self, muted)

    def get_theme_name(self) -> str:
        return theme_api.get_theme_name(self._ini_config.config)

    def get_media_priorities(self) -> dict[str, str]:
        return config_api.get_media_priorities(self._ini_config.config)

    def get_temporary_vpinplay_profile(self) -> dict[str, Any]:
        """The guests holding a VPinPlay account, in 2.x's shape."""
        return temporary_profiles.state()

    def set_temporary_vpinplay_profile(self, payload: Any,
                                       source_name: str = "") -> dict[str, Any]:
        """A guest joins from a VPinPlay card. A card refused leaves the guests as they
        were."""
        try:
            temporary_profiles.join(payload if isinstance(payload, str)
                                    else json.dumps(payload))
        except ServiceError as exc:
            logger.warning("Not joining a guest from a VPinPlay card: %s", exc)
        return self._vpinplay_profiles_changed()

    def clear_temporary_vpinplay_profile(self) -> dict[str, Any]:
        """Every guest signed out."""
        temporary_profiles.clear()
        return self._vpinplay_profiles_changed()

    def _vpinplay_profiles_changed(self) -> dict[str, Any]:
        result = temporary_profiles.state()
        self.send_event_all_windows_incself({
            "type": "VPinPlayAlternateProfileChanged",
            "profile": result,
        })
        return result

    def get_playfield_orientation(self) -> str:
        return config_api.get_playfield_orientation(self._ini_config.config)

    def get_playfield_rotation(self) -> int:
        return config_api.get_playfield_rotation(self._ini_config.config)

    def get_playfield_media_rotation(self) -> str:
        return config_api.get_playfield_media_rotation(self._ini_config.config)

    def get_cab_mode(self) -> bool:
        return config_api.get_cab_mode(self._ini_config.config)

    def get_theme_assets_port(self) -> int:
        return config_api.get_theme_assets_port(self._ini_config.config)

    def get_http_port(self) -> int:
        return config_api.get_http_port(self._ini_config.config)

    def get_remote_link(self) -> dict[str, Any]:
        return config_api.get_remote_link(self._ini_config.config)

    def get_manager_link(self) -> dict[str, Any]:
        return config_api.get_manager_link(self._ini_config.config)

    def get_vpinplay_multi_link(self) -> dict[str, Any]:
        return config_api.get_vpinplay_multi_link(self._ini_config.config)

    def get_theme_contract(self) -> int:
        return self._theme_contract()

    def get_theme_windows(self) -> list[str]:
        theme_dir = theme_api.resolve_theme_dir(theme_api.get_theme_name(self._ini_config.config))
        return list(theme_windows.declared_windows(theme_dir, self._theme_contract()))

    def get_theme_index_page(self) -> str:
        return theme_api.get_theme_index_page(self._ini_config.config, self.get_my_window_name())
