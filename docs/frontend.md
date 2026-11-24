# Frontend Runtime Architecture

The frontend starts at `main.py`, but most runtime responsibilities now live in focused modules under `frontend/` and `common/`.

## Startup Flow

1. `main.py` handles executable-only concerns such as platform terminal behavior, early config/logging setup, and command-line parsing.
2. `frontend.runtime` creates the websocket/API/browser runtime, builds static mount points, registers the theme routes on the HTTP app, runs the frontend blocking loop, and performs shutdown/restart handling.
3. `frontend.device_channel.DeviceChannel` receives JavaScript calls from theme windows and dispatches only methods listed by `frontend.api.API_ALLOWED_METHODS`.
4. `frontend.api.API` remains the JS-facing facade for theme code. It should stay thin and delegate feature behavior to service modules.

The windows come and go under the run. `run_frontend_loop` waits on the shutdown event, as a headless run does; `ChromiumManager.close_windows` closes the windows without ending it, and `reopen_windows` opens them again on the settings as they are now (screens, rotation, theme and Chromium options re-read from `vpinfe.json`, the theme's wheel set reapplied). A window closed by hand still sets the event and ends the run. A headless run opens windows the same way and closes any it opened when it quits; there, a window closed by hand and the theme's Exit (`close_app`) only close the windows and the run goes on, because `lifecycle_host.effective` reads the frontend's Quit VPinFE as Close the frontend windows (a quit from the Manager, the API or a signal still quits). The window set itself is fixed at boot, so adding or removing one needs a restart.

## Core Modules

- `common/config/paths.py`: shared config paths, `vpinfe.ini`, `collections.ini`, themes directory, and game root lookup.
- `common/games/game.py`: dataclass representation of a parsed game folder.
- `common/games/game_metadata.py`: shared metadata normalization, section lookup, rating/truthy helpers, and metadata persistence.
- `common/games/game_repository.py`: game parser/cache ownership and game row shaping.
- `common/games/collection_filters.py`: instance-based game filtering with no hidden singleton state.
- `common/games/collections_service.py`: shared collection manager access and filter-collection helpers.
- `common/games/game_play_service.py`: start count, last-played date, runtime, score update, and NVRAM cleanup.
- `common/host/display_service.py`: shared monitor discovery.
- `common/games/metadata_service.py`: build metadata orchestration shared by CLI, frontend, and the Manager.
- `common/games/game_report_service.py`: CLI-oriented missing/unknown game reports backed by the shared parser and VPSdb lookup.
- `common/host/system_actions.py`: the app restart request (kept on the install row) and re-launch, clean OS command environment, shutdown, and reboot commands.
- `frontend/game_state.py`: game JSON serialization, filtering, sorting, collections, and rating mutations for the JS API.
- `common/host/launch.py`: VPX launch lifecycle, DOF/DMD stop-start, and frontend launch events.
- `frontend/input_api.py`: input mapping reads/writes.
- `frontend/theme_api.py`: theme name/config/index URL and audio-muted helpers.
- `frontend/metadata_build_service.py`: asynchronous build metadata orchestration and progress event forwarding.
- `frontend/config_api.py`: small config getters/setters exposed through the JS API.

## Adding API Methods

1. Put feature behavior in a focused service module first.
2. Add a thin method to `frontend.api.API`.
3. Add the name to `API_PUBLISHED_METHODS` if a theme may call it, or to `API_INTERNAL_METHODS` if only the overlays VPinFE ships do. `vpin.call` refuses the second set, and `docs/theme.md` documents the first.
4. Keep websocket transport logic in `frontend.device_channel`; it should not grow feature-specific behavior.

## Guidelines

- Keep `main.py` focused on executable wiring.
- Keep `API` as a compatibility facade for JavaScript themes, not a home for business logic.
- Use `common/config/paths.py` instead of calling `user_config_dir("vpinfe", "vpinfe")` directly in new code.
- Use `common/games/game_metadata.py` for metadata reads/writes instead of repeating `Info`/`VPinFE`/`User` normalization.
- Use service modules for behavior shared by the Manager and the frontend.
- Keep `cli.py` as CLI dispatch and compatibility wrappers; put reusable app behavior in `common/`.
