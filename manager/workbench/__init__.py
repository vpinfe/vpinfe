"""The panel that follows the selection.

Sections are declared, grouped by what they belong to, and remember which of them are
open. That last part is what makes stepping down a list a comparison rather than a run
of unrelated screens: the panel is rebuilt for every selection, so without a record the
shape you arranged is gone on the next arrow key.

The grouping is the answer to how a game and its tables sit together. Not a toggle
between them - which things are whose. A section under "This table" follows the lens
above it; the rest are the game's whatever build you are looking at.
"""
