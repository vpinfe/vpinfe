# -*- mode: python ; coding: utf-8 -*-
"""What a VPinFE build contains, for every platform and both variants.

PyInstaller follows imports on its own but cannot see data files, so anything that is not
Python has to be named here or it will not be in the artifact.
`tests/invariants/test_build_ships_its_data.py` fails on a tracked file no root reaches.

This replaces five near-identical `pyinstaller` command lines that between them repeated
six facts twenty-six times, in two separator dialects - `:` on POSIX and `;` on Windows,
which exists only because a shell has to squeeze a pair into one string. A spec is Python,
so a pair is a tuple and the separator problem does not exist.

Two axes, and only two:

    platform   Linux and Windows each bundle their own Chromium; macOS copies one into
               the .app after the build, so it is not listed here. Linux needs pynput's
               X11 backends named because PyInstaller ships no hook for them.
    variant    slim is the same build without Chromium. VPINFE_SLIM=1 selects it.

Run it the same way everywhere:

    pyinstaller packaging/vpinfe.spec                  # full
    VPINFE_SLIM=1 pyinstaller packaging/vpinfe.spec    # slim
"""

import ast
import os
import sys
from pathlib import Path

REPO_ROOT = Path(SPECPATH).parent
SLIM = os.environ.get("VPINFE_SLIM") == "1"

IS_LINUX = sys.platform.startswith("linux")
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

# Every directory we ship, whatever the platform or variant. Each is a data root in its
# own right - a top-level one, an <owner>/static/ directory, or the catalogs a subsystem
# reads. `third_party/` is fetched by scripts/ before the build, not committed; the rest
# are in git.
DATA_ROOTS = [
    "frontend/static",
    "manager/static",
    "common/host/static",
    "common/i18n/catalogs",
    "apps/generic/i18n",
    "apps/vpx/i18n",
    "releases",
    "third_party/dof",
    "third_party/libdmdutil",
]

# Chromium is the only difference between a full build and a slim one. macOS is absent on
# purpose: its Chromium.app is copied into the bundle's Frameworks after PyInstaller runs,
# because an .app inside --add-data loses the executable bit and the symlinks it needs.
if not SLIM:
    if IS_LINUX:
        DATA_ROOTS.append("chromium/linux/chrome")
    elif IS_WINDOWS:
        DATA_ROOTS.append("chromium/windows")

sys.path.insert(0, str(REPO_ROOT))
from common.extensions import fingerprint  # noqa: E402
from scripts import stdlib_modules  # noqa: E402

datas = [(str(REPO_ROOT / root), root) for root in DATA_ROOTS]
# Only the extensions the build includes, whatever else sits under extensions/.
datas += [(str(REPO_ROOT / "extensions" / name), f"extensions/{name}")
          for name in fingerprint.INCLUDED]
# The running release's notes, which the What's new page reads beside the archived ones.
datas.append((str(REPO_ROOT / "RELEASE_NOTES.md"), "."))

# What the extensions we ship are made of, so a changed one is known from an untouched one
# on the device. Beside them in the build, never in the tree.
_FINGERPRINTS = fingerprint.write_build_list(
    REPO_ROOT / "extensions", REPO_ROOT / "build" / "extension_fingerprints" / fingerprint.BUILD_LIST,
    only=fingerprint.INCLUDED,
)
datas.append((str(_FINGERPRINTS), "extensions"))

# PyInstaller ships no pynput hook, so the backend it loads by name at runtime is invisible
# to the import scan. Only X11 matters: the Windows and macOS backends are imported
# normally.
hiddenimports = ["pynput.keyboard._xorg", "pynput.mouse._xorg"] if IS_LINUX else []

# An extension installed later is never scanned, and neither is a library it fetches, so
# the build carries the whole standard library for them to import.
hiddenimports += stdlib_modules.names()


def _extension_imports() -> list[str]:
    """Every module a bundled extension imports that is not the standard library. Core loads
    an extension from its directory at runtime, so the import scan never reaches one."""
    found = set()
    # os.walk, because rglob does not follow the links a development tree has in extensions/.
    sources = [Path(folder) / name
               for included in fingerprint.INCLUDED
               for folder, folders, files in os.walk(REPO_ROOT / "extensions" / included,
                                                     followlinks=True)
               if "tests" not in Path(folder).relative_to(REPO_ROOT / "extensions" / included).parts
               for name in files if name.endswith(".py")]
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                found.add(node.module)
    return sorted(name for name in found if name.split(".")[0] not in sys.stdlib_module_names)


hiddenimports += _extension_imports()

analysis = Analysis(
    [str(REPO_ROOT / "main.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

# macOS ships an .app the user double-clicks, so the name is the one that shows in the
# Dock. Everywhere else the name is the binary on the command line.
APP_NAME = "VPinFE" if IS_MACOS else "vpinfe"

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # A windowed macOS build has no terminal; the other platforms keep one, and the
    # Windows launcher hides it when VPinFE is started from its .bat rather than a shell.
    console=not IS_MACOS,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(REPO_ROOT / "packaging" / "VPinFE.icns") if IS_MACOS else None,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)

if IS_MACOS:
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(REPO_ROOT / "packaging" / "VPinFE.icns"),
        bundle_identifier="com.vpinfe.main",
    )
