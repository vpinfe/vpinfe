"""Importing files, as an extension asks for it: `ctx.uploads`.

The same analysis, plan and import the upload routes run. The source has to sit inside a
folder the extension declared, and a game is named by its id rather than its folder.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from common.i18n import t

from .contract import ContractError
from .games import ExtensionGames

if TYPE_CHECKING:
    from .context import ExtensionFiles

UPLOADS_WRITE = "uploads:write"

# What `plan` reads, and what `execute` reads on top. `game_id` stands where the routes
# take `game_dir`; tests/extensions/test_uploads_offered.py holds both lists to the models.
PLAN_KEYS = frozenset({"vps_id", "game_id", "rom_name", "allow_new_game", "media_kind",
                       "asset_kind", "location_id", "add_table", "new_game_dir_name",
                       "replace_table", "carry", "instead", "table_id", "scope",
                       "page", "assignments", "picks", "convert", "info_modes"})
EXECUTE_KEYS = PLAN_KEYS | {"selected"}

_TEXT = frozenset({"vps_id", "game_id", "rom_name", "media_kind", "asset_kind", "location_id",
                   "replace_table", "table_id"})
_FLAGS = frozenset({"allow_new_game", "add_table", "instead"})
_CONVERT_ROW = frozenset({"to", "video", "quality", "sound_in_video"})


class ExtensionUploads:
    def __init__(self, name: str, scopes: Iterable[str], files: ExtensionFiles,
                 games: ExtensionGames) -> None:
        self._name = name
        self._scopes = frozenset(scopes)
        self._files = files
        self._games = games

    def analyze(self, path: str | Path) -> dict[str, Any]:
        """What a file, a folder or an archive holds, as the import would read it."""
        from common.uploads import upload_ops

        self._need(UPLOADS_WRITE)
        with self._session(path) as upload:
            return upload_ops.analysis_of(upload)

    def plan(self, path: str | Path, **asked: Any) -> dict[str, Any]:
        """What `execute` with the same arguments would do, before it does any of it."""
        from common.uploads import upload_ops

        self._need(UPLOADS_WRITE)
        request = self._request(asked, PLAN_KEYS)
        with self._session(path) as upload:
            return upload_ops.plan_for(upload, request)

    def execute(self, path: str | Path, *, declared: Mapping[str, Mapping[str, str]]
                | None = None, **asked: Any) -> dict[str, Any]:
        """Import it into the library. Answers with the report, including what was left out.

        `declared` says what each file is, keyed by file name: `vps_file_id`,
        `host_item_id`, `host`, `game_id`, `table_id` and `confirmed_by`. A claim naming an
        upstream record has to say how it is known, and is refused otherwise.
        """
        from common.uploads import upload_ops

        self._need(UPLOADS_WRITE)
        request = self._request(asked, EXECUTE_KEYS)
        claims = self._claims(declared)
        with self._session(path) as upload:
            return upload_ops.execute(upload, request, claims)

    @contextmanager
    def _session(self, path: str | Path) -> Iterator[str]:
        """An upload session over `path`, ended on the way out whether or not it was used up.

        Reads the source where it is and never removes it.
        """
        from common import service_errors
        from common.uploads import upload_ops

        if not isinstance(path, (str, Path)) or not str(path).strip():
            raise ContractError(f"{self._name} asked to import from a path that is empty")
        wanted = Path(path).expanduser().resolve()
        self._files._require_inside(wanted)
        if not wanted.exists():
            raise service_errors.RefusedError(t("error.filesystem.nothing_there"),
                                              details={"path": str(wanted)})
        upload = upload_ops.begin_over(wanted)["id"]
        try:
            yield upload
        finally:
            upload_ops.abort(upload)

    def _request(self, asked: dict[str, Any], keys: frozenset[str]) -> dict[str, Any]:
        if "game_dir" in asked:
            raise ContractError(f"{self._name} names a game by its folder; name it with "
                                "game_id and core finds the folder")
        unknown = sorted(set(asked) - keys)
        if unknown:
            raise ContractError(f"{self._name} asked an upload for {', '.join(unknown)}, "
                                f"which it does not take; it takes {', '.join(sorted(keys))}")
        for key, value in asked.items():
            self._typed(key, value)
        request = dict(asked)
        game_id = request.pop("game_id", "")
        if game_id:
            if request.get("allow_new_game"):
                raise ContractError(f"{self._name} named a game and also asked for a new one; "
                                    "a source holding a table would make the new game and "
                                    "leave the named one alone")
            request["game_dir"] = self._games.folder(game_id)
        return request

    def _typed(self, key: str, value: Any) -> None:
        if key in _FLAGS:
            right = isinstance(value, bool)
        elif key == "scope":
            right = value in ("", "game", "table")
        elif key in _TEXT:
            right = isinstance(value, str)
        elif key == "page":
            right = isinstance(value, int) and not isinstance(value, bool) and value >= 1
        elif key == "new_game_dir_name":
            right = value is None or isinstance(value, str)
        elif key == "assignments":
            right = isinstance(value, list) and all(isinstance(one, dict) for one in value)
        elif key == "convert":
            self._typed_convert(value)
            return
        elif key == "info_modes":
            right = isinstance(value, dict) and all(
                isinstance(k, str) and v in ("fill", "facts", "all") for k, v in value.items())
        elif key == "picks":
            right = isinstance(value, dict) and all(
                isinstance(k, str) and isinstance(v, str) for k, v in value.items())
        elif key == "carry":
            right = value is None or (isinstance(value, list)
                                      and all(isinstance(one, str) for one in value))
        else:
            right = isinstance(value, list) and all(
                isinstance(one, int) and not isinstance(one, bool) for one in value)
        if not right:
            raise ContractError(f"{self._name} gave {key} a {type(value).__name__} that "
                                "it cannot be")
        if key == "rom_name" and value and (value in {".", ".."} or Path(value).name != value
                                           or "\\" in value):
            raise ContractError(f"{self._name} gave a rom_name that is not a plain name; it "
                                "names the folder the ROM goes in")

    def _typed_convert(self, value: Any) -> None:
        """`{"rows": {file: {to, video, quality, sound_in_video}}, "wait": bool}`, the sheet's
        answer for the files that are converted on the way in."""
        right = isinstance(value, dict) and set(value) <= {"rows", "wait"}
        if right:
            rows, wait = value.get("rows", {}), value.get("wait", True)
            right = isinstance(wait, bool) and isinstance(rows, dict) and all(
                isinstance(file, str) and isinstance(row, dict)
                and set(row) <= _CONVERT_ROW
                and all(one is None or isinstance(one, str) for one in row.values())
                for file, row in rows.items())
        if not right:
            raise ContractError(
                f"{self._name} gave convert something other than rows, a dict of file name to "
                f"{', '.join(sorted(_CONVERT_ROW))} as text or None, and wait, a bool")

    def _claims(self, declared: Mapping[str, Mapping[str, str]] | None) -> dict[str, Any]:
        from common.games.identity_claims import DeclaredIdentity

        known = {one.name for one in dataclasses.fields(DeclaredIdentity)}
        claims = {}
        for file, sent in (declared or {}).items():
            if not isinstance(sent, Mapping) or not all(
                    isinstance(value, str) for value in sent.values()):
                raise ContractError(f"{self._name} declared {file} as something other than "
                                    "a dict of text fields")
            unknown = sorted(set(sent) - known)
            if unknown:
                raise ContractError(f"{self._name} declared {', '.join(unknown)} for {file}"
                                    f", which a declaration does not take; it takes "
                                    f"{', '.join(sorted(known))}")
            claims[file] = DeclaredIdentity(**sent)
        return claims

    def _need(self, scope: str) -> None:
        if scope not in self._scopes:
            raise ContractError(
                f"{self._name} asks core to import files, which needs {scope} and its "
                "manifest does not declare it")
