"""The key prefixes of the reads that failed, which the install client and Overview share."""

from __future__ import annotations

# `away:<extension>` in the reads that failed says that extension's needs could not be
# read, `away:*` that none could.
AWAY = "away:"
AWAY_ALL = AWAY + "*"
