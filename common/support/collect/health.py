"""Health Now: the problems VPinFE already knows about.

The configuration findings Overview shows under Needs You, then what this run's log says:
the count of errors and warnings, and the three that repeat most.
"""

from __future__ import annotations

from collections import Counter

from common.config.paths import get_ini_config
from common.games import launchers
from common.runtime import log_setup
from common.settings import feature_checks
from common.support.sections import Section, SectionText

# More than a run produces in practice, and the reader keeps only the newest records of a
# file anyway.
RECORDS = 5000
TOP = 3
WORSE = ("WARNING", "ERROR", "CRITICAL")


def collect() -> SectionText:
    return SectionText.ok(_findings() + [""] + _log())


def _findings() -> list[str]:
    found = feature_checks.unmet(get_ini_config(),
                                 launcher=launchers.default_launcher("vpx"))
    lines = ["### Needs You", ""]
    if not found:
        return lines + ["Nothing."]
    return lines + [f"- **{item.feature}:** {item.reason}" for item in found]


def _log() -> list[str]:
    path = log_setup.log_file()
    heading = ["### This Run's Log", ""]
    if path is None:
        return heading + ["VPinFE is not writing a log file on this run."]
    records = log_setup.read_records(path, RECORDS)
    levels = Counter(str(record["level"]) for record in records)
    lines = [f"- **Errors:** {levels['ERROR'] + levels['CRITICAL']}",
             f"- **Warnings:** {levels['WARNING']}"]
    repeated = Counter((str(record["logger"]), str(record["message"]).split("\n", 1)[0])
                       for record in records if record["level"] in WORSE)
    lines += [f"- **{count}x** {source}: {first}"
              for (source, first), count in repeated.most_common(TOP)]
    return heading + lines


SECTION = Section("health", "Health Now", 90, collect)
