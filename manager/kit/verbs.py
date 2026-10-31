"""One icon per verb, named for the verb rather than for the drawing.

Every button takes its drawing from here. A new verb that means what one of these means
takes that one's drawing. The marks at the end say what kind a thing is, and no button
wears one.
"""

from __future__ import annotations

# Leaving without taking what was offered.
CANCEL = "close"
CLOSE = "close"
SKIP = "close"
DISCARD = "close"

# Taking what was offered.
ACCEPT = "check"
DONE = "check"
CHOOSE = "check"

# Bringing something into the library.
ADD = "add"
CREATE = "add"
NEW_FOLDER = "create_new_folder"
IMPORT = "upload"
COPY = "content_copy"
DUPLICATE = "content_copy"

# Changing something that is already here.
EDIT = "edit"
RENAME = "edit"
SAVE = "save"
MAKE_DEFAULT = "check_circle"
SPLIT = "call_split"
SHARE = "folder_shared"
SWITCH_OFF = "toggle_off"

# Binding a record to a catalog, and letting it go. The chain is this and nothing else -
# a link that navigates is an anchor and carries no icon at all.
MATCH = "link"
UNMATCH = "delete_outline"

# Taking something away. Emptying a field is not deleting a thing, and the three stay apart.
CLEAR = "backspace"
REMOVE = "remove"
DELETE = "delete_outline"
UNINSTALL = "delete_outline"
CLEAN = "cleaning_services"
# Keeping out what a rule would bring back.
EXCLUDE = "close"

# Keeping something out of what is offered, and letting it back in. Nothing is removed.
HIDE = "visibility_off"
UNHIDE = "visibility"

# Holding a pick where it is, and letting it follow again.
LOCK = "lock"
UNLOCK = "lock_open"

# Answering a question a dialog asked.
KEEP = "check"
FORGET = "remove"
REPLACE = "swap_horiz"
FETCH = "download"
ACTIVATE = "check_circle"
SEND = "send"
RESET = "restart_alt"

# What an extension asked for, where it declares a label and no drawing.
RUN = "play_arrow"

# Moving, looking and running.
BACK = "arrow_back"
NEXT = "arrow_forward"
# A place in an order, one step either way.
EARLIER = "chevron_left"
LATER = "chevron_right"
# A page at a time, one step either way.
PAGE_EARLIER = "keyboard_double_arrow_left"
PAGE_LATER = "keyboard_double_arrow_right"
# The frontend's own screens, reached by a key.
PICK = "radio_button_checked"
OPEN_MENU = "menu"
OPEN_COLLECTIONS = "collections_bookmark"
GO = "arrow_forward"
SEARCH = "search"
FIT = "fit_screen"
FILTER = "filter_list"
STOP = "stop"
POWER = "power_settings_new"
RESTART = "restart_alt"
PAUSE = "pause"
REVIEW = "fact_check"
GET_HELP = "support"
TAKE_PICTURE = "photo_camera"
RESTORE = "restore"
EXTRACT = "unarchive"
BROWSE = "folder_open"
MERGE = "merge"
OPEN_OUT = "open_in_new"
SHOW_MORE = "expand_more"
SHOW_PICTURES = "photo_library"

# Giving what one thing holds to another, so the first no longer has it.
MOVE = "arrow_forward"

# What a row of Needs You is about, drawn in the tone its cost sets.
CANNOT_PLAY = "error"
NO_MATCH = "link_off"
NO_ART = "image_not_supported"
NO_MEDIA = "hide_image"
NO_YEAR = "event_busy"
NO_TABLE = "layers_clear"
HAD_ERRORS = "warning"
NOT_SAVED = "sync_problem"
OLDER_FORMAT = "history"
NEWER_BUILD = "new_releases"

# How a job went, where the list shows a glyph and no word.
JOB_RUNNING = "autorenew"
JOB_FAILED = "error"

# Asking for the current state of something held elsewhere.
REFRESH = "refresh"
UPDATE = "system_update_alt"

# Bringing art or a file in, by where it comes from.
FROM_FILE = "upload_file"
FROM_FOLDER = "drive_folder_upload"
FROM_HOST = "devices"
FROM_ONLINE = "cloud_download"
FROM_BACKGLASS_FILE = "tv"
WITHOUT_FILE = "tag"
RECORD = "fiber_manual_record"
ADD_ART = "add_photo_alternate"
ADD_TO_LIST = "playlist_add"
WANTED = "bookmark"

# Saying who is holding the phone.
THIS_IS_ME = "person"
NOT_ME = "person_off"
SIGN_OUT = "logout"
IGNORE = "block"
JOIN = "qr_code_2"
INITIALS_ONLY = "badge"

# Working on what is on screen.
ENLARGE = "open_in_full"
OPEN_WINDOW = "open_in_browser"
TUNE = "tune"
ROTATE_LEFT = "rotate_left"
ROTATE_RIGHT = "rotate_right"
REVERT = "replay"
PIN = "push_pin"
CONTAIN = "south_west"

# Saying which way a screen's top points, and what to do with a screen on a picture.
FACES_UP = "arrow_upward"
FACES_LEFT = "arrow_back"
FACES_RIGHT = "arrow_forward"
FACES_DOWN = "arrow_downward"
SHOW_NUMBERS = "filter_1"
UNPLACE = "close"
DRAG = "drag_indicator"

# Moving through a list or opening one row of it.
MORE = "more_vert"
DRILL = "chevron_right"
UP = "arrow_upward"
PLACES = "arrow_drop_down"
DOWN = "arrow_downward"
COLLAPSE = "keyboard_arrow_up"
EXPAND = "keyboard_arrow_down"

# What a running table can be asked to do, by what the control is. A control with none of
# its own takes its group's.
VOLUME_DOWN = "volume_down"
VOLUME_UP = "volume_up"
COIN = "paid"
SERVICE = "build"
DEBUG = "bug_report"
RULES = "menu_book"
FIRE = "bolt"
LEFT_FLIPPER = "turn_left"
RIGHT_FLIPPER = "turn_right"
LAUNCH_BALL = "sports_baseball"
MAGNA_SAVE = "shield"
NUDGE_LEFT = "west"
NUDGE_CENTER = "north"
NUDGE_RIGHT = "east"
COIN_DOOR = "sensor_door"
GAME_CONTROL = "sports_esports"

# What was pressed, on the line that says what it does, and a row waiting for a press.
PRESS = "touch_app"
KEYBOARD = "keyboard"
CONTROLLER = "sports_esports"
LISTEN = "hearing"

# For a verb with no drawing of its own, by what kind of act it is.
FALLBACK = {"open": OPEN_OUT, "change": EDIT, "add": ADD, "remove": CANCEL}

# A collection whose rules add games. The plain gear is Settings'.
SMART = "settings_suggest"
OPENS_ON = "home"
# The picker's places: Home, Downloads, the folder the last file came from, and a drive or
# the root.
HOME = "home"
DOWNLOADS = "download"
LAST_USED = "history"
DRIVE = "storage"
# A collection's row held to one table.
LOCKED = "lock"
SAYS_MORE = "info_outline"


def declared() -> frozenset[str]:
    """Every drawing this module allows, for the check that nothing else is used."""
    return frozenset(
        value for name, value in globals().items()
        if name.isupper() and isinstance(value, str)
    ) | frozenset(FALLBACK.values())
