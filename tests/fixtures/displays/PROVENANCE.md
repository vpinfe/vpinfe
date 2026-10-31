# Display fixtures

What each desktop prints about its screens, one file per case, read by the parsers in
`common/support/collect/displays/probes/`. Every file is listed here, and a test fails
when one is not.

**Synthetic** means laid out by hand from the program's documentation and its source, with
made-up names and no serial numbers. It shows the parser the shape of the output, not what
a particular release prints. **Captured** means recorded from a real session with
`scripts/capture-display-fixtures.sh`, which strips serial numbers and monitor names. A
captured file names the release and the hardware in its row. Replace a synthetic file with
a captured one when a device is at hand, and change its row.

| File | Source | What it covers |
| --- | --- | --- |
| `xrandr/one.txt` | Synthetic | One screen, no rotation |
| `xrandr/two_portrait_left.txt` | Synthetic | Two screens, the second turned left |
| `xrandr/mirrored.txt` | Synthetic | Two outputs showing one area |
| `xrandr/inverted_reflected_off.txt` | Synthetic | Inverted, reflected, and an output that is off |
| `xrandr/garbage.txt` | Synthetic | Text no parser can read |
| `kscreen/plasma6_two_rotated.json` | Synthetic | Plasma 6 JSON, two screens, one rotated |
| `kscreen/plasma6_text.txt` | Synthetic | The text form, with the color codes the program prints |
| `mutter/two_transformed_disabled.json` | Synthetic | Two logical screens with transforms, one monitor off |
| `mutter/fractional.json` | Synthetic | A fractional scale on one screen |
| `sway/outputs_two_rotated.json` | Synthetic | Two outputs with named transforms |
| `hyprland/monitors_two_rotated.json` | Synthetic | Two monitors with numbered transforms |
| `wlr-randr/two_rotated.json` | Synthetic | The generic wlroots listing, two outputs |
| `wayland-info/one_output.txt` | Synthetic | The generic Wayland listing, one turned output |
| `os-release/steamos.txt` | Synthetic | The appliance with a game-mode session |
| `os-release/ubuntu.txt` | Synthetic | An ordinary desktop distribution |

## Not yet read on a real device

The direction a turn goes (left, right, 90, 270) is unverified on every system. The report
prints the system's own word beside the degrees, so a wrong reading is visible rather than
silent. Each probe falls back to the same one-line gap when its program is missing, times
out, or prints something the parser does not know.
