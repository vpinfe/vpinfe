"""What Add asks before a launcher exists."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from manager.frontend import launchers

ASKED: list[dict[str, Any]] = [
    {"key": "bin_path", "label": "Program", "path": "exe", "blank": ""},
    {"key": "ini_path", "label": "Settings File", "path": "file",
     "suffixes": [".ini"], "blank": "Its own"}]


class _Cancelled:
    """The dialog, answered Cancel."""

    def __enter__(self) -> _Cancelled:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def submit(self, _answer: Any) -> None:
        return None

    def __await__(self) -> Any:
        return _answered(False).__await__()


async def _answered(value: bool) -> bool:
    return value


def _element() -> MagicMock:
    """`ui.element` as a container that is still on the page."""
    element = MagicMock()
    element.return_value.classes.return_value.__enter__.return_value.is_deleted = False
    return element


def _facts(_target: Any, entries: list[tuple[Any, Any]]) -> None:
    for _label, draw in entries:
        draw()


class _Drawn:
    """What each field was drawn with, by the label it sits under, while a dialog that is
    cancelled at once opens."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.controls: list[Mock] = []
        self.keep: Any = None
        self.write = AsyncMock()

    def field(self, value: str = "", **kwargs: Any) -> Mock:
        self.calls.append({"value": value, **kwargs})
        self.controls.append(Mock(value=value))
        return self.controls[-1]

    async def ask(self, **kwargs: Any) -> Any:
        with patch.object(launchers.frame, "opened", return_value=_Cancelled()), \
                patch.object(launchers.frame, "field", side_effect=self.field), \
                patch.object(launchers.frame, "footer"), \
                patch.object(launchers.frame, "cancel"), \
                patch.object(launchers.frame, "answer",
                             side_effect=lambda _label, keep, **_k: setattr(self, "keep", keep)), \
                patch.object(launchers.frame, "focus"), \
                patch.object(launchers.frame, "enter_presses"), \
                patch.object(launchers.ui, "element", _element()), \
                patch.object(launchers.panel, "facts", side_effect=_facts):
            return await launchers.ask_name("New", "Add", [], self.write, asked=ASKED,
                                            placeholder=kwargs.pop("placeholder", "VPX"),
                                            **kwargs)


class EmptyPaths(unittest.IsolatedAsyncioTestCase):
    async def test_an_empty_path_reads_as_the_word_its_app_gives_it(self) -> None:
        drawn = _Drawn()
        await drawn.ask()

        self.assertEqual([one["placeholder"] for one in drawn.calls], ["VPX", "", "Its own"])


class Browse(unittest.IsolatedAsyncioTestCase):
    async def _asked(self, **kwargs: Any) -> dict[str, Any]:
        """The `browse` each field was drawn with, by the placeholder it carries."""
        drawn = _Drawn()
        await drawn.ask(placeholder="name", **kwargs)
        return {one["placeholder"]: one["browse"] for one in drawn.calls}

    async def test_the_path_fields_browse_for_what_they_are_and_the_name_does_not(self) -> None:
        listing = Mock()
        browsing = await self._asked(looking=listing)

        self.assertIsNone(browsing["name"])
        self.assertIsNotNone(browsing[""])
        with patch.object(launchers.folder_picker, "pick_path",
                          AsyncMock(return_value="/x/vpx")) as pick:
            self.assertEqual(await browsing[""]("/typed"), "/x/vpx")
            pick.assert_awaited_with(listing, "/typed", kind="exe", suffixes=())
            await browsing["Its own"]("")
            pick.assert_awaited_with(listing, "", kind="file", suffixes=(".ini",))

    async def test_without_a_listing_they_are_typed_only(self) -> None:
        browsing = await self._asked()

        self.assertEqual(set(browsing.values()), {None})


class Standing(unittest.IsolatedAsyncioTestCase):
    HELD = {"ini_path": {"default_path": "/p/VPinballX.ini", "default_whose": "VPX's default"}}

    async def test_a_field_the_app_can_name_a_file_for_shows_it_before_anything_is_typed(
            self) -> None:
        asked: list[dict] = []

        async def standing(settings: dict[str, str]) -> dict[str, dict]:
            asked.append(settings)
            return self.HELD

        drawn = _Drawn()
        await drawn.ask(looking=Mock(), standing=standing)

        self.assertEqual(asked, [{"bin_path": "", "ini_path": ""}])
        ini = drawn.calls[2]
        self.assertEqual((ini["default"], ini["whose"]), ("/p/VPinballX.ini", "VPX's default"))
        self.assertNotIn("default", drawn.calls[1])

    async def test_a_field_with_no_file_to_name_reads_the_apps_word_and_why(self) -> None:
        async def standing(_settings: dict[str, str]) -> dict[str, dict]:
            return {"ini_path": {"blank": "Not found", "left_empty": "Made on first run"}}

        drawn = _Drawn()
        await drawn.ask(looking=Mock(), standing=standing)

        ini = drawn.calls[2]
        self.assertEqual((ini["placeholder"], ini["left_empty"], ini["default"]),
                         ("Not found", "Made on first run", ""))

    async def test_a_lookup_that_fails_leaves_the_fields_as_the_app_declares_them(self) -> None:
        drawn = _Drawn()
        with self.assertLogs("vpinfe.manager.frontend.launchers"):
            await drawn.ask(looking=Mock(), standing=AsyncMock(side_effect=RuntimeError("down")))

        self.assertEqual([one["placeholder"] for one in drawn.calls], ["VPX", "", "Its own"])
        self.assertNotIn("default", drawn.calls[2])

    async def test_the_default_shown_is_not_written_as_if_it_were_typed(self) -> None:
        drawn = _Drawn()
        await drawn.ask(looking=Mock(), standing=AsyncMock(return_value=self.HELD))
        drawn.controls[2].value = "/p/VPinballX.ini"
        drawn.controls[1].value = "/x/vpx"
        drawn.controls[0].value = "Mine"

        await drawn.keep()

        drawn.write.assert_awaited_with("Mine", {"bin_path": "/x/vpx", "ini_path": ""}, None)

    async def test_choosing_a_program_asks_again_and_draws_what_it_changes(self) -> None:
        answers = [{"ini_path": {"default_path": "/old.ini"}},
                   {"ini_path": {"default_path": "/new.ini"}}]
        standing = AsyncMock(side_effect=answers)
        drawn = _Drawn()
        with patch.object(launchers.asyncio, "sleep", AsyncMock()):
            await drawn.ask(looking=Mock(), standing=standing)
            drawn.controls[1].value = "/x/vpx"
            refresh = drawn.controls[1].on_value_change.call_args.args[0]
            with patch.object(launchers.frame, "field", side_effect=drawn.field), \
                    patch.object(launchers.ui, "element", _element()):
                await refresh()

        standing.assert_awaited_with({"bin_path": "/x/vpx", "ini_path": ""})
        self.assertEqual(drawn.calls[-1]["default"], "/new.ini")

    async def test_a_path_typed_in_the_field_survives_a_new_default(self) -> None:
        answers = [{"ini_path": {"default_path": "/old.ini"}},
                   {"ini_path": {"default_path": "/new.ini"}}]
        drawn = _Drawn()
        with patch.object(launchers.asyncio, "sleep", AsyncMock()):
            await drawn.ask(looking=Mock(), standing=AsyncMock(side_effect=answers))
            drawn.controls[2].value = "/mine.ini"
            refresh = drawn.controls[1].on_value_change.call_args.args[0]
            with patch.object(launchers.frame, "field", side_effect=drawn.field), \
                    patch.object(launchers.ui, "element", _element()):
                await refresh()

        self.assertEqual(drawn.calls[-1]["value"], "/mine.ini")

    async def test_an_app_with_nothing_to_say_is_never_asked_again(self) -> None:
        drawn = _Drawn()
        await drawn.ask(looking=Mock(), standing=AsyncMock(return_value={}))

        for control in drawn.controls[1:]:
            control.on_value_change.assert_not_called()

    async def test_an_unchanged_answer_draws_nothing_again(self) -> None:
        drawn = _Drawn()
        with patch.object(launchers.asyncio, "sleep", AsyncMock()):
            await drawn.ask(looking=Mock(), standing=AsyncMock(return_value=self.HELD))
            before = len(drawn.calls)
            await drawn.controls[1].on_value_change.call_args.args[0]()

        self.assertEqual(len(drawn.calls), before)

    async def test_a_second_change_before_the_pause_ends_drops_the_first(self) -> None:
        standing = AsyncMock(return_value=self.HELD)
        drawn = _Drawn()
        with patch.object(launchers.asyncio, "sleep", AsyncMock()):
            await drawn.ask(looking=Mock(), standing=standing)
        refresh = drawn.controls[1].on_value_change.call_args.args[0]
        gate = asyncio.Event()
        yielding = asyncio.sleep

        async def pause(_seconds: float) -> None:
            await gate.wait()

        with patch.object(launchers.asyncio, "sleep", pause):
            first = asyncio.ensure_future(refresh())
            second = asyncio.ensure_future(refresh())
            await yielding(0)
            gate.set()
            await asyncio.gather(first, second)

        self.assertEqual(standing.await_count, 2)


class _Library:
    """What `_add` and `duplicate` read and write, with every call recorded."""

    def __init__(self) -> None:
        self.put_launcher = Mock()
        self.launchers = Mock(return_value={"launchers": []})
        self.launcher_settings_file_places = Mock(return_value=[])
        self.folders = Mock()


async def _inline(callback: Any, *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


class AppsWithoutASettingsFile(unittest.IsolatedAsyncioTestCase):
    """A Generic launcher is refused its settings file, so Add must not ask for one."""

    async def _asked(self, call: Any, *, confirm: bool = True,
                     ) -> tuple[_Library, dict[str, Any], Any]:
        library = _Library()
        seen: dict[str, Any] = {}

        async def ask_name(_title: str, _answer: str, _names: list[str], write: Any,
                           **kwargs: Any) -> bool:
            seen.update(kwargs)
            chosen = kwargs["choosing"](lambda: "Mine") if kwargs["choosing"] else None
            if not confirm:
                return False
            return bool(await write("Mine", {"bin_path": "/x/fp"}, chosen))

        with patch("manager.kit.on_page.ui"), \
                patch.object(launchers, "ask_name", ask_name), \
                patch.object(launchers.offload, "io", _inline), \
                patch.object(launchers, "_open", AsyncMock()):
            await call(library)
        return library, seen, None

    async def test_add_draws_no_settings_file_choice_and_stores_the_launcher(self) -> None:
        app = {"id": "generic", "name": "Generic", "has_config": False,
               "fields": [{"key": "bin_path", "label": "Program", "path": "exe"}]}

        library, seen, _ = await self._asked(
            lambda lib: launchers._add(lib, {}, Mock(), app))

        self.assertIsNone(seen["choosing"])
        library.launcher_settings_file_places.assert_not_called()
        library.put_launcher.assert_called_once()
        body = library.put_launcher.call_args.args[1]
        self.assertEqual((body["app"], body["display_name"], body["settings"]),
                         ("generic", "Mine", {"bin_path": "/x/fp"}))

    async def test_add_still_asks_where_an_apps_settings_come_from_when_it_keeps_a_file(
            self) -> None:
        app = {"id": "vpx", "name": "Visual Pinball X", "has_config": True, "fields": []}

        library, seen, _ = await self._asked(
            lambda lib: launchers._add(lib, {}, Mock(), app), confirm=False)

        self.assertIsNotNone(seen["choosing"])
        library.launcher_settings_file_places.assert_called_once()

    async def test_duplicate_of_such_a_launcher_asks_nothing_about_a_file_either(self) -> None:
        original = {"launcher_id": "wrap", "app": "generic", "app_name": "Generic",
                    "display_name": "Wrap", "enabled": True, "has_config": False,
                    "settings": {"bin_path": "/x/fp"}}

        library, seen, _ = await self._asked(
            lambda lib: launchers.duplicate(lib, {}, Mock(), [original]))

        self.assertIsNone(seen["choosing"])
        body = library.put_launcher.call_args.args[1]
        self.assertEqual((body["display_name"], body["owns_ini"]), ("Mine", False))
        self.assertNotEqual(library.put_launcher.call_args.args[0], "wrap")


if __name__ == "__main__":
    unittest.main()
