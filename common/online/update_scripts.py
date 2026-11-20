"""The updater's four scripts: a POSIX shell script, a macOS one, a PowerShell script, and
the batch file that starts the last.

Here rather than beside the code that runs them because the text in this module is not
Python. A shell line and a batch line cannot be wrapped the way an argument list can -
a continuation is part of the script's own grammar - so the line-length rule is lifted
for this file alone, and lifting it costs nothing where there is no Python to hide.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from common.config.paths import UPDATES_DIR


def _build_posix_update_script(prepared: dict, current_pid: int, log_path: Path) -> str:
    zip_path = shlex.quote(prepared["zip_path"])
    stage_dir = shlex.quote(prepared["stage_dir"])
    install_root = shlex.quote(prepared["install_root"])
    launch_target = shlex.quote(prepared["launch_target"])
    log_file = shlex.quote(str(log_path))
    last_log = shlex.quote(prepared["last_update_log"])
    updates_root = shlex.quote(str(UPDATES_DIR))
    return f"""#!/bin/sh
set -eu

PID={current_pid}
ZIP_PATH={zip_path}
STAGE_DIR={stage_dir}
INSTALL_ROOT={install_root}
LAUNCH_TARGET={launch_target}
LOG_PATH={log_file}
LAST_LOG={last_log}
UPDATES_ROOT={updates_root}
KEEP_VERSION_DIR="$(dirname "$STAGE_DIR")"
EXTRACT_ROOT="$STAGE_DIR/extracted"
NEW_ROOT="$EXTRACT_ROOT/vpinfe"
BACKUP_ROOT="${{INSTALL_ROOT}}.bak"

mkdir -p "$(dirname "$LOG_PATH")"
: >"$LOG_PATH"
exec >>"$LOG_PATH" 2>&1
echo "[Updater] Stage log: $LOG_PATH"
echo "[Updater] Stable log: $LAST_LOG"
echo "[Updater] Stage log: $LOG_PATH" >>"$LAST_LOG"
echo "[Updater] Install root: $INSTALL_ROOT" >>"$LAST_LOG"
echo "[Updater] Launch target: $LAUNCH_TARGET" >>"$LAST_LOG"
echo "[Updater] Waiting for pid $PID to exit"
WAIT_SECONDS=0
while kill -0 "$PID" 2>/dev/null; do
    sleep 1
    WAIT_SECONDS=$((WAIT_SECONDS + 1))
    if [ "$WAIT_SECONDS" -eq 15 ]; then
        echo "[Updater] Process $PID still running after 15s; sending SIGTERM"
        kill -TERM "$PID" 2>/dev/null || true
    fi
    if [ "$WAIT_SECONDS" -eq 20 ]; then
        echo "[Updater] Process $PID still running after 20s; sending SIGKILL"
        kill -KILL "$PID" 2>/dev/null || true
    fi
done
echo "[Updater] Source process exited"

rm -rf "$EXTRACT_ROOT"
mkdir -p "$EXTRACT_ROOT"
echo "[Updater] Extracting $ZIP_PATH"

if command -v unzip >/dev/null 2>&1; then
    unzip -q "$ZIP_PATH" -d "$EXTRACT_ROOT"
elif command -v python3 >/dev/null 2>&1; then
    python3 -c "import pathlib, zipfile; zipfile.ZipFile(pathlib.Path(r'$ZIP_PATH')).extractall(pathlib.Path(r'$EXTRACT_ROOT'))"
else
    echo "[Updater] No unzip tool available"
    exit 1
fi

if [ ! -d "$NEW_ROOT" ]; then
    echo "[Updater] Extracted update missing vpinfe directory"
    exit 1
fi
echo "[Updater] Extraction complete"

rm -rf "$BACKUP_ROOT"
mv "$INSTALL_ROOT" "$BACKUP_ROOT"
echo "[Updater] Moved current install to backup: $BACKUP_ROOT"
if mv "$NEW_ROOT" "$INSTALL_ROOT"; then
    echo "[Updater] Installed new version into $INSTALL_ROOT"
    chmod +x "$LAUNCH_TARGET" 2>/dev/null || true
    if [ "$(uname -s)" = "Linux" ]; then
        CHROMIUM_ROOT="$INSTALL_ROOT/_internal/chromium/linux/chrome"
        if [ -d "$CHROMIUM_ROOT" ]; then
            echo "[Updater] Repairing bundled Chromium permissions at $CHROMIUM_ROOT"
            find "$CHROMIUM_ROOT" -type d -exec chmod a+rx {{}} +
            for helper in chrome chrome_crashpad_handler chrome-sandbox nacl_helper nacl_helper_bootstrap; do
                if [ -e "$CHROMIUM_ROOT/$helper" ]; then
                    chmod a+x "$CHROMIUM_ROOT/$helper" 2>/dev/null || true
                fi
            done
        fi
    fi
    cd "$INSTALL_ROOT"
    echo "[Updater] Relaunch environment: DISPLAY=${{DISPLAY:-<unset>}} WAYLAND_DISPLAY=${{WAYLAND_DISPLAY:-<unset>}} XDG_RUNTIME_DIR=${{XDG_RUNTIME_DIR:-<unset>}}"
    # Relaunch with a clean runtime env so we don't inherit stale PyInstaller
    # library paths from the old process (which can point at INSTALL_ROOT.bak).
    if command -v setsid >/dev/null 2>&1; then
        setsid env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONHOME -u PYTHONPATH -u _MEIPASS2 "$LAUNCH_TARGET" </dev/null >/dev/null 2>&1 &
    else
        env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONHOME -u PYTHONPATH -u _MEIPASS2 "$LAUNCH_TARGET" </dev/null >/dev/null 2>&1 &
    fi
    NEW_PID=$!
    echo "[Updater] Relaunch started with pid $NEW_PID"
    sleep 3
    if kill -0 "$NEW_PID" 2>/dev/null; then
        echo "[Updater] Relaunch appears to be running"
    else
        echo "[Updater] Relaunch process exited quickly"
    fi
    echo "[Updater] Pruning old staged updates"
    find "$UPDATES_ROOT" -mindepth 1 -maxdepth 1 ! -path "$KEEP_VERSION_DIR" -exec rm -rf {{}} +
    rm -rf "$BACKUP_ROOT"
    rm -rf "$EXTRACT_ROOT"
    echo "[Updater] Update applied successfully"
else
    echo "[Updater] Swap failed, restoring backup"
    rm -rf "$INSTALL_ROOT"
    mv "$BACKUP_ROOT" "$INSTALL_ROOT"
    exit 1
fi
"""


def _build_macos_update_script(prepared: dict, current_pid: int, log_path: Path) -> str:
    """Swaps the running .app for the one inside the release's disk image.

    The new app is copied in beside the old one and only then renamed into place, so the
    swap is two renames on one volume and the old app is put back if either fails.
    """
    zip_path = shlex.quote(prepared["zip_path"])
    stage_dir = shlex.quote(prepared["stage_dir"])
    install_root = shlex.quote(prepared["install_root"])
    log_file = shlex.quote(str(log_path))
    last_log = shlex.quote(prepared["last_update_log"])
    updates_root = shlex.quote(str(UPDATES_DIR))
    return f"""#!/bin/sh
set -eu

PID={current_pid}
ZIP_PATH={zip_path}
STAGE_DIR={stage_dir}
INSTALL_ROOT={install_root}
LOG_PATH={log_file}
LAST_LOG={last_log}
UPDATES_ROOT={updates_root}
KEEP_VERSION_DIR="$(dirname "$STAGE_DIR")"
EXTRACT_ROOT="$STAGE_DIR/extracted"
MOUNT_POINT="$STAGE_DIR/volume"
APP_NAME="$(basename "$INSTALL_ROOT")"
PARENT_DIR="$(dirname "$INSTALL_ROOT")"
NEW_ROOT="$PARENT_DIR/.$APP_NAME.new"
BACKUP_ROOT="$PARENT_DIR/.$APP_NAME.bak"

mkdir -p "$(dirname "$LOG_PATH")"
: >"$LOG_PATH"
exec >>"$LOG_PATH" 2>&1
echo "[Updater] Stage log: $LOG_PATH"
echo "[Updater] Stage log: $LOG_PATH" >>"$LAST_LOG"
echo "[Updater] Install root: $INSTALL_ROOT" >>"$LAST_LOG"

mounted=0
applied=0
detach() {{
    if [ "$mounted" -eq 1 ]; then
        hdiutil detach "$MOUNT_POINT" -quiet -force || true
        mounted=0
    fi
}}
finish() {{
    detach
    rmdir "$MOUNT_POINT" 2>/dev/null || true
    if [ "$applied" -eq 0 ] && [ -d "$INSTALL_ROOT" ]; then
        echo "[Updater] Update not applied; reopening the installed app"
        open "$INSTALL_ROOT" || true
    fi
}}
trap finish EXIT

echo "[Updater] Waiting for pid $PID to exit"
WAIT_SECONDS=0
while kill -0 "$PID" 2>/dev/null; do
    sleep 1
    WAIT_SECONDS=$((WAIT_SECONDS + 1))
    if [ "$WAIT_SECONDS" -eq 15 ]; then
        echo "[Updater] Process $PID still running after 15s; sending SIGTERM"
        kill -TERM "$PID" 2>/dev/null || true
    fi
    if [ "$WAIT_SECONDS" -eq 20 ]; then
        echo "[Updater] Process $PID still running after 20s; sending SIGKILL"
        kill -KILL "$PID" 2>/dev/null || true
    fi
done
echo "[Updater] Source process exited"

rm -rf "$EXTRACT_ROOT" "$NEW_ROOT"
mkdir -p "$EXTRACT_ROOT" "$MOUNT_POINT"
echo "[Updater] Extracting $ZIP_PATH"
unzip -q "$ZIP_PATH" -d "$EXTRACT_ROOT"
DMG_PATH="$EXTRACT_ROOT/VPinFE.dmg"
if [ ! -f "$DMG_PATH" ]; then
    echo "[Updater] Extracted update missing VPinFE.dmg"
    exit 1
fi

echo "[Updater] Opening $DMG_PATH"
hdiutil attach "$DMG_PATH" -mountpoint "$MOUNT_POINT" -nobrowse -readonly -noverify -quiet
mounted=1
SOURCE_APP="$(find "$MOUNT_POINT" -maxdepth 1 -name '*.app' | head -n 1)"
if [ -z "$SOURCE_APP" ]; then
    echo "[Updater] The disk image holds no app"
    exit 1
fi
ditto "$SOURCE_APP" "$NEW_ROOT"
detach
echo "[Updater] Copied the new app to $NEW_ROOT"

if ! codesign --verify --deep "$NEW_ROOT"; then
    echo "[Updater] The new app failed its signature check; leaving the installed app alone"
    rm -rf "$NEW_ROOT"
    exit 1
fi

rm -rf "$BACKUP_ROOT"
mv "$INSTALL_ROOT" "$BACKUP_ROOT"
echo "[Updater] Moved current install to backup: $BACKUP_ROOT"
if mv "$NEW_ROOT" "$INSTALL_ROOT"; then
    applied=1
    echo "[Updater] Installed new version into $INSTALL_ROOT"
    open "$INSTALL_ROOT"
    echo "[Updater] Relaunch requested"
    echo "[Updater] Pruning old staged updates"
    find "$UPDATES_ROOT" -mindepth 1 -maxdepth 1 ! -path "$KEEP_VERSION_DIR" -exec rm -rf {{}} +
    rm -rf "$BACKUP_ROOT"
    rm -rf "$EXTRACT_ROOT"
    echo "[Updater] Update applied successfully"
else
    echo "[Updater] Swap failed, restoring backup"
    rm -rf "$NEW_ROOT"
    mv "$BACKUP_ROOT" "$INSTALL_ROOT"
    exit 1
fi
"""


def _ps_literal(value: str) -> str:
    return value.replace("'", "''")


def _cmd_literal(value: str) -> str:
    return value.replace(
        "^", "^^").replace("&", "^&").replace("<", "^<").replace(">", "^>").replace("|", "^|")


def _build_windows_update_script(prepared: dict, current_pid: int, log_path: Path) -> str:
    zip_path = _ps_literal(prepared["zip_path"])
    stage_dir = _ps_literal(prepared["stage_dir"])
    install_root = _ps_literal(prepared["install_root"])
    launch_target = _ps_literal(prepared["launch_target"])
    launch_exe = _ps_literal(prepared["launch_exe"])
    log_file = _ps_literal(str(log_path))
    updates_root = _ps_literal(str(UPDATES_DIR))
    return f"""$ErrorActionPreference = 'Stop'
$PidToWait = {current_pid}
$ZipPath = '{zip_path}'
$StageDir = '{stage_dir}'
$InstallRoot = '{install_root}'
$LaunchTarget = '{launch_target}'
$LaunchExe = '{launch_exe}'
$UpdatesRoot = '{updates_root}'
$ExtractRoot = Join-Path $StageDir 'extracted'
$NewRoot = Join-Path $ExtractRoot 'vpinfe'

function Invoke-WithRetry([scriptblock]$Action, [string]$Description, [int]$Attempts = 20, [int]$DelayMs = 500) {{
    for ($i = 1; $i -le $Attempts; $i++) {{
        try {{
            & $Action
            if ($i -gt 1) {{
                Write-Output "[Updater] $Description succeeded on retry $i"
            }}
            return
        }}
        catch {{
            Write-Output "[Updater] $Description failed on attempt ${{i}}: $($_.Exception.Message)"
            if ($i -eq $Attempts) {{
                throw
            }}
            Start-Sleep -Milliseconds $DelayMs
        }}
    }}
}}

Start-Transcript -Path '{log_file}' -Append | Out-Null
try {{
    Write-Output "[Updater] Stage log: {log_file}"
    Write-Output "[Updater] Install root: $InstallRoot"
    Write-Output "[Updater] Launch target: $LaunchTarget"
    Write-Output "[Updater] Waiting for pid $PidToWait to exit"
    while (Get-Process -Id $PidToWait -ErrorAction SilentlyContinue) {{
        Start-Sleep -Seconds 1
    }}
    Write-Output "[Updater] Source process exited"
    Start-Sleep -Seconds 2

    if (Test-Path -LiteralPath $ExtractRoot) {{
        Invoke-WithRetry {{ Remove-Item -LiteralPath $ExtractRoot -Recurse -Force }} "Removing previous extract root"
    }}
    Write-Output "[Updater] Extracting $ZipPath"
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $ExtractRoot -Force

    if (-not (Test-Path -LiteralPath $NewRoot)) {{
        throw "Extracted update missing vpinfe directory"
    }}
    Write-Output "[Updater] Extraction complete"

    $robocopyArgs = @(
        $NewRoot, $InstallRoot, '/MIR', '/R:5', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
    Write-Output "[Updater] Mirroring extracted install into place"
    & robocopy @robocopyArgs
    $robocopyExit = $LASTEXITCODE
    Write-Output "[Updater] Robocopy exit code: $robocopyExit"
    if ($robocopyExit -gt 7) {{
        throw "Robocopy failed with exit code $robocopyExit"
    }}

    $launchPath = $LaunchTarget
    if (-not (Test-Path -LiteralPath $launchPath) -and (Test-Path -LiteralPath $LaunchExe)) {{
        $launchPath = $LaunchExe
    }}
    Write-Output "[Updater] Installed new version into $InstallRoot"
    $proc = Start-Process -FilePath $launchPath -WorkingDirectory $InstallRoot -PassThru
    Write-Output "[Updater] Relaunch started with pid $($proc.Id)"
    Start-Sleep -Seconds 3
    if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) {{
        Write-Output "[Updater] Relaunch appears to be running"
    }}
    else {{
        Write-Output "[Updater] Relaunch process exited quickly"
    }}
    Write-Output "[Updater] Pruning old staged updates"
    Get-ChildItem -LiteralPath $UpdatesRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object {{ -not ($StageDir.StartsWith($_.FullName.TrimEnd('\') + '\')) }} |
        ForEach-Object {{ Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }}
    Invoke-WithRetry {{ Remove-Item -LiteralPath $ExtractRoot -Recurse -Force }} "Removing extract root after successful relaunch"
    Write-Output "[Updater] Update applied successfully"
}}
catch {{
    Write-Output "[Updater] ERROR: $($_.Exception.Message)"
    throw
}}
finally {{
    Stop-Transcript | Out-Null
}}
"""


def _build_windows_bootstrap_script(
    powershell_exe: str,
    script_path: Path,
    stable_log: Path,
    bootstrap_log: Path,
) -> str:
    ps_path = _cmd_literal(str(Path(powershell_exe)))
    updater_script = _cmd_literal(str(script_path))
    stable_log_path = _cmd_literal(str(stable_log))
    bootstrap_log_path = _cmd_literal(str(bootstrap_log))
    return f"""@echo off
setlocal
echo [Updater] Bootstrap starting >> "{stable_log_path}"
echo [Updater] Invoking PowerShell script {updater_script} >> "{stable_log_path}"
"{ps_path}" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{updater_script}" >> "{bootstrap_log_path}" 2>&1
set EXIT_CODE=%ERRORLEVEL%
echo [Updater] Bootstrap launched PowerShell with exit code %EXIT_CODE% >> "{stable_log_path}"
exit /b %EXIT_CODE%
"""
