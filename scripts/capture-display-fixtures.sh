#!/bin/sh
# Records what this desktop prints about its screens, for tests/fixtures/displays/.
# Runs only the read-only programs the display probes run, from this session, and writes one
# file per program into the directory named as the first argument (default: ./display-capture).
# Serial numbers and monitor names are replaced before anything is written, and so are the
# titles and application names in the window lists.
# Programs that are not installed are skipped; review each file before copying it into the tree.

set -u

out="${1:-./display-capture}"
mkdir -p "$out"

scrub() {
    python3 -I -c '
import re, sys
text = sys.stdin.read()
text = re.sub(r"(\"(?:serial|make|model|description|name_from_edid)\"\s*:\s*)\"[^\"]*\"", "\\1\"scrubbed\"", text)
text = re.sub(r"(?im)^(\s*(?:serial|make|model|description)\s*[:=]).*$", r"\1 scrubbed", text)
text = re.sub(r"(\[\"[^\"]*\",\s*\"[^\"]*\",\s*\"[^\"]*\",\s*)\"[^\"]*\"", "\\1\"0000\"", text)
text = re.sub(r"\b[0-9A-Fa-f]{32}\b", "0" * 32, text)
sys.stdout.write(text)
'
}

# The window lists name every open window by title and application, which are the user's own.
scrub_windows() {
    python3 -I -c '
import re, sys
text = sys.stdin.read()
keys = "name|title|class|initialClass|initialTitle|app_id|instance|window_properties"
text = re.sub(r"(\"(?:" + keys + r")\"\s*:\s*)\"(?:[^\"\\]|\\.)*\"", "\\1\"scrubbed\"", text)
sys.stdout.write(text)
'
}

capture() {
    name="$1"
    shift
    command -v "$1" >/dev/null 2>&1 || return 0
    "$@" 2>/dev/null | scrub >"$out/$name"
}

capture_windows() {
    name="$1"
    shift
    command -v "$1" >/dev/null 2>&1 || return 0
    "$@" 2>/dev/null | scrub | scrub_windows >"$out/$name"
}

capture xrandr-query.txt xrandr --query
capture xrandr-listmonitors.txt xrandr --listmonitors
capture kscreen.json kscreen-doctor -j
capture kscreen.txt kscreen-doctor -o
capture mutter.json busctl --user --json=short call org.gnome.Mutter.DisplayConfig \
    /org/gnome/Mutter/DisplayConfig org.gnome.Mutter.DisplayConfig GetCurrentState
capture sway-outputs.json swaymsg -r -t get_outputs
capture_windows sway-tree.json swaymsg -r -t get_tree
capture hyprland-monitors.json hyprctl -j monitors
capture_windows hyprland-clients.json hyprctl -j clients
capture wlr-randr.json wlr-randr --json
capture wayland-info.txt wayland-info
capture os-release.txt cat /etc/os-release

echo "Wrote $(ls "$out" | wc -l | tr -d ' ') files to $out. Read each one before using it."
