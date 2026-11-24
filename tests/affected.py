"""`python -m tests --affected`: the test modules a change reaches.

Four sources:

- the invariants, always, because they read the whole tree;
- a changed test file selects itself;
- a changed source or `tests/support` file selects the modules that import it, and the modules
  that import its direct importers. Importing `a.b.c` also imports the packages `a.b` and `a`;
- `affected.toml`, for what imports cannot see: the browser drives start the app in a
  subprocess, and data files such as the catalogs are read, not imported.

A changed file nothing accounts for selects the whole suite. Chains longer than one hop are
not followed: through `common` they reach nearly everything.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from tests.support import trees

MAP = Path(__file__).resolve().parent / "affected.toml"
INVARIANTS = "tests/invariants"
# A test tree: VPinFE's own, or the `tests` folder an extension carries.
_TEST_TREE = re.compile(r"(?:tests|extensions/[^/]+/tests)/")
# Files no test outside the invariants reads.
NO_TEST = ("releases/*", "packaging/*", ".github/*", ".git*", "LICENSE", "requirements*.txt",
           "tests/affected.toml")


@dataclass
class Selection:
    paths: list[str] = field(default_factory=list)
    whole_suite: bool = False
    notes: list[str] = field(default_factory=list)


# The variables that point git at a repository other than the one `-C` names. A hook sets them
# to its own scratch tree, and a command run with them in place reads and writes that one.
_GIT_LOCATION = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                 "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE", "GIT_PREFIX")


def git_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in _GIT_LOCATION}


def _git(repo: Path, *args: str) -> list[str]:
    run = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                         check=False, env=git_env())
    if run.returncode != 0:
        raise ValueError(run.stderr.strip() or f"git {args[0]} failed")
    return [line for line in run.stdout.splitlines() if line]


def _extension_clones(repo: Path) -> list[Path]:
    """The extensions under `extensions/` that are a git repository of their own."""
    return [one for one in sorted((repo / "extensions").glob("*")) if (one / ".git").exists()]


def changed_files(repo: Path, since: str) -> list[str]:
    """Tracked changes since `since`, including the working tree, plus untracked files.

    An extension that is a repository of its own is not tracked here, so its working tree
    changes, against its own HEAD, are added under `extensions/<name>/`. `since` does not
    apply to it: a commit of its own is not a revision of this repository.

    Raises ValueError, carrying git's own words, when `since` names nothing.
    """
    names = _git(repo, "diff", "--name-only", "--no-renames", since, "--")
    names += _git(repo, "ls-files", "--others", "--exclude-standard")
    for clone in _extension_clones(repo):
        inside = _git(clone, "diff", "--name-only", "--no-renames", "HEAD", "--")
        inside += _git(clone, "ls-files", "--others", "--exclude-standard")
        names += [f"extensions/{clone.name}/{name}" for name in inside]
    return sorted(set(names))


def read_map(path: Path = MAP) -> list[dict]:
    with path.open("rb") as handle:
        return tomllib.load(handle).get("area", [])


def _imports(path: Path, module: str) -> set[str]:
    """Every dotted name `path` imports, relative imports made absolute against `module`."""
    try:
        tree = trees.tree_for(path)
    except (SyntaxError, OSError):
        return set()
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[: len(parts) - (node.level - 1)]
                base = ".".join(part for part in (*parts, base) if part)
            if not base:
                continue
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def _module_name(relative: str) -> str:
    name = relative[:-3].replace("/", ".")
    return name[: -len(".__init__")] if name.endswith(".__init__") else name


def _resolve(names: set[str], known: set[str]) -> set[str]:
    """The known modules `names` reach: the longest match of each, and its parent packages."""
    found: set[str] = set()
    for name in names:
        parts = name.split(".")
        for end in range(len(parts), 0, -1):
            if ".".join(parts[:end]) in known:
                found.update(".".join(parts[:stop]) for stop in range(1, end + 1)
                             if ".".join(parts[:stop]) in known)
                break
    return found


def python_files(repo: Path) -> list[str]:
    names = _git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "*.py")
    for folder in sorted((repo / "extensions").glob("*")):
        for found in sorted(folder.rglob("*.py")):
            relative = found.relative_to(repo / "extensions").parts
            if not any(part.startswith(".") for part in relative):
                names.append("/".join(("extensions", *relative)))
    return [name for name in dict.fromkeys(names) if not name.startswith("docs/")]


def select(repo: Path, changed: list[str], files: list[str] | None = None,
           areas: list[dict] | None = None) -> Selection:
    files = python_files(repo) if files is None else files
    areas = read_map() if areas is None else areas
    sources = {_module_name(f): f for f in files if not _TEST_TREE.match(f)}
    tests = {_module_name(f): f for f in files
             if _TEST_TREE.match(f) and Path(f).name.startswith("test_")}
    helpers = {_module_name(f): f for f in files
               if _TEST_TREE.match(f) and not Path(f).name.startswith("test_")}
    known = set(sources) | set(helpers)

    imports_of = {name: _resolve(_imports(repo / path, name), known) - {name}
                  for name, path in (sources | helpers).items()}
    test_imports = {name: _resolve(_imports(repo / path, name), known)
                    for name, path in tests.items()}
    dependents: dict[str, set[str]] = defaultdict(set)
    for name, deps in imports_of.items():
        for dep in deps:
            dependents[dep].add(name)

    chosen: set[str] = set()
    result = Selection(paths=[INVARIANTS])
    for relative in changed:
        if any(fnmatch(relative, pattern) for pattern in NO_TEST):
            continue
        matched = False
        for area in areas:
            if any(fnmatch(relative, pattern) for pattern in area["paths"]):
                chosen.update(area["tests"])
                matched = matched or not area.get("floor")
        if relative.endswith(".py"):
            name = _module_name(relative)
            if name in tests:
                chosen.add(tests[name])
                matched = True
            elif _TEST_TREE.match(relative) and Path(relative).name.startswith("test_"):
                matched = True  # a test module that is gone has nothing to run
            elif name in known:
                near = {name} | dependents[name]
                reached = {path for test, path in tests.items() if test_imports[test] & near}
                chosen.update(reached)
                matched = matched or bool(reached)
        if not matched:
            result.whole_suite = True
            result.notes.append(f"{relative}: no test is mapped to it")
    result.paths += sorted(path for path in chosen - {INVARIANTS} if (repo / path).exists())
    return result
