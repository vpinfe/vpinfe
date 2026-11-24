# Example Scores

Lists, flags, removes and posts the scores on Example Scores, shows the best score on a
game's panel, and writes a line to its log when a game ends.

Put this folder, `example_scores`, in `extensions/` under the VPinFE config directory.

It asks for:

- `config:own`: it keeps a site address, a key and a note for each score.
- `net:outbound`: it reads and writes the scores on the site.
- `ui:mount`: it adds a list under Community and a group on a game's panel.
- `games:read`: the group on a game's panel needs to know which game it is on.

It runs on every platform VPinFE does.
