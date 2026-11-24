"""What `tools.resolve` answers, for a test that stands in for a program on this device."""

from __future__ import annotations

from pathlib import Path

from common.host import tools


def found(tool: tools.Tool) -> tools.Found:
    return tools.Found(tool, tools.State.FOUND, Path("/usr/bin") / tool.names[tools.LINUX][0])


def missing(tool: tools.Tool) -> tools.Found:
    return tools.Found(tool, tools.State.MISSING)


def unusable(tool: tools.Tool, reason: str) -> tools.Found:
    return tools.Found(tool, tools.State.UNUSABLE, Path("/usr/bin") / tool.names[tools.LINUX][0],
                       tools.Probe(False, reason=reason))
