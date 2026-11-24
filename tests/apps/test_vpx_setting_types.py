"""What type each Visual Pinball setting is, and the script that reads it from the source."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType

from apps.vpx.setting_types import TYPES

ROOT = Path(__file__).resolve().parents[2]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "fetch_vpx_setting_types", ROOT / "scripts" / "fetch_vpx_setting_types.py")
    assert spec is not None and spec.loader is not None, "the generator is gone"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _script()

DECLARED = """\
PropBoolBase(Player, PlaySound, "Enable Playfield"s, "Mechanical sounds, on or off"s,
   false, true);
PropBoolBase(Player, Held, "Held"s, "Kept, even at the same value"s, true, false);
PropEnum1(DefaultPropsGate, GateType, "GateType"s, ""s, GateType, GateWireW,
   "GateWireW"s, "GatePlate"s);
PropFloatStepped(Player, Rotation, "Rotation"s, ""s, 0.f, 360.f, 90.f, 0.f);
PropEnum(Player, SyncMode, "Synchronization"s,
   "No Sync: nothing waits.\\nVertical Sync: waits, "
   "for the display."s,
   int, 3, "No Sync"s, "Vertical Sync"s);
PropArray(Window, Mode, int, Enum, Int, m_propInvalid, m_propBackglass_BackglassOutput);
"""


class ParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.types, self.contextual = script.parsed(DECLARED)

    def test_every_form_of_declaration_is_typed(self) -> None:
        self.assertEqual(self.types, {
            "Player.PlaySound": "bool",
            "Player.Held": "bool",
            "DefaultProps\\Gate.GateType": "choice",
            "Player.Rotation": "number",
            "Player.SyncMode": "choice",
        })

    def test_a_base_form_says_for_itself_whether_it_is_contextual(self) -> None:
        self.assertIn("Player.Held", self.contextual)
        self.assertNotIn("Player.PlaySound", self.contextual)

    def test_a_form_it_does_not_know_stops_the_run(self) -> None:
        with self.assertRaisesRegex(ValueError, "PropColor"):
            script.parsed('PropColor(Player, Tint, "Tint"s, ""s, 0x000000);')


class PluginTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        plugin = self.root / "pinmame"
        plugin.mkdir()
        (plugin / "plugin.cfg").write_text('[configuration]\nid = "PinMAME"\n')
        (plugin / "plugin.cpp").write_text(
            'MSGPI_STRING_VAL_SETTING(pathProp, "PinMAMEPath", "PinMAME Path",\n'
            '   "Folder that contains PinMAME subfolders", true, "", 1024);\n'
            'MSGPI_BOOL_VAL_SETTING(zeProp, "ZeDMD", "ZeDMD", "", true, false);\n'
            'MSGPI_INT_VAL_SETTING(portProp, "Port", PORT_LABEL, "", true, 0, 9, 0);\n')

    def test_each_setting_lands_in_its_plugin_s_section(self) -> None:
        types, _labels, _registered = script.from_plugins(self.root)

        self.assertEqual(types, {"Plugin.PinMAME.Enable": "bool",
                                 "Plugin.PinMAME.PinMAMEPath": "string",
                                 "Plugin.PinMAME.ZeDMD": "bool",
                                 "Plugin.PinMAME.Port": "int"})

    def test_a_label_is_kept_where_it_is_not_the_key(self) -> None:
        _types, labels, _registered = script.from_plugins(self.root)

        self.assertEqual(labels, {"Plugin.PinMAME.PinMAMEPath": "PinMAME Path"})

    def test_the_score_view_window_is_spelled_as_its_heading_is(self) -> None:
        (self.root / "pinmame" / "plugin.cpp").write_text(
            'MSGPI_BOOL_VAL_SETTING(prop, "ScoreViewDMDOverlay", "ScoreView DMD Overlay", "",\n'
            '   true, false);\n')

        _types, labels, _registered = script.from_plugins(self.root)

        self.assertEqual(labels, {"Plugin.PinMAME.ScoreViewDMDOverlay": "Score View DMD Overlay"})

    def test_each_setting_s_default_is_as_the_file_stores_it(self) -> None:
        _types, _labels, registered = script.from_plugins(self.root)

        self.assertEqual(registered, {
            "Plugin.PinMAME.PinMAMEPath": {"default": ""},
            "Plugin.PinMAME.ZeDMD": {"default": "0"},
            "Plugin.PinMAME.Port": {"default": "0", "minimum": 0, "maximum": 9},
        })


    def test_a_folder_and_a_file_are_strings(self) -> None:
        (self.root / "pinmame" / "plugin.cpp").write_text(
            'MSGPI_FOLDER_VAL_SETTING(a, "LayoutFolder", "Layouts", "", true, "", 1024);\n'
            'MSGPI_FILE_VAL_SETTING(b, "Rom", "Rom", "", true, "", "zip", 1024);\n')

        types, _labels, registered = script.from_plugins(self.root)

        self.assertEqual(types["Plugin.PinMAME.LayoutFolder"], "string")
        self.assertEqual(types["Plugin.PinMAME.Rom"], "string")
        self.assertEqual(registered["Plugin.PinMAME.Rom"], {"default": ""})

    def test_a_kind_it_has_no_type_for_stops_the_run(self) -> None:
        (self.root / "pinmame" / "plugin.cpp").write_text(
            'MSGPI_COLOR_VAL_SETTING(a, "Tint", "Tint", "", true, 0);\n')

        with self.assertRaisesRegex(ValueError, "MSGPI_COLOR_VAL_SETTING in Plugin.PinMAME"):
            script.from_plugins(self.root)


NAMED = """\
#define MSGPI_BOOL_VAL_SETTING(var, id, name, desc, editable, def) \\
   static MsgPluginSettingDef var = { id, name, desc, editable, def }
#define DEFAULT_SCALE 2
enum Mode { MODE_NONE = 1, MODE_FAST, MODE_BEST };
static const char* modeNames[] = { "None", "Fast" /* quick, rough */, "Best" };
MSGPI_ENUM_SETTING(modeProp, "Mode", "Mode", "Select", true, 1, std::size(modeNames),
   modeNames, MODE_BEST, GetMode, SetMode);
MSGPI_INT_VAL_SETTING(scaleProp, "Scale", "Scale", "", true, 1, 0x10, DEFAULT_SCALE);
MSGPI_FLOAT_VAL_SETTING(volumeProp, "Volume", "Volume",
   "How loud, " "0 to 1", true, 0.f, 1.f, 0.1f, 0.8f);
MSGPI_BOOL_VAL_SETTING(grillProp, "ShowGrill", "Show Grill", "", true, (true));
"""


class RegisteredTests(unittest.TestCase):
    """What a plugin registers each setting with, however its source spells it."""

    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.plugin = self.root / "upscaledmd"
        self.plugin.mkdir()
        (self.plugin / "plugin.cfg").write_text('[configuration]\nid = "UpscaleDMD"\n')
        (self.plugin / "names.h").write_text(NAMED)

    def registered(self) -> dict:
        return script.from_plugins(self.root)[2]

    def test_the_names_a_declaration_uses_are_read_through(self) -> None:
        registered = self.registered()

        self.assertEqual(registered["Plugin.UpscaleDMD.Scale"],
                         {"default": "2", "minimum": 1, "maximum": 16})
        self.assertEqual(registered["Plugin.UpscaleDMD.Volume"],
                         {"default": "0.8", "minimum": 0.0, "maximum": 1.0})
        self.assertEqual(registered["Plugin.UpscaleDMD.ShowGrill"], {"default": "1"})

    def test_an_enumerated_setting_numbers_its_answers_from_its_minimum(self) -> None:
        self.assertEqual(self.registered()["Plugin.UpscaleDMD.Mode"], {
            "default": "3",
            "choices": (("1", "None"), ("2", "Fast"), ("3", "Best")),
        })

    def test_the_macro_s_own_definition_is_not_a_setting(self) -> None:
        self.assertNotIn("Plugin.UpscaleDMD.id", self.registered())

    def test_a_default_it_cannot_read_stops_the_run(self) -> None:
        (self.plugin / "more.cpp").write_text(
            'MSGPI_INT_VAL_SETTING(p, "Depth", "Depth", "", true, 0, 9, SOMEWHERE_ELSE);\n')

        with self.assertRaisesRegex(ValueError, "Plugin.UpscaleDMD.Depth"):
            self.registered()

    def test_a_switch_the_core_does_not_declare_is_off(self) -> None:
        core = self.root / "src" / "core"
        core.mkdir(parents=True)
        (core / "Settings_properties.inl").write_text(
            'PropBoolDyn(PluginPinMAME, Enable, "Enable"s, "Enable PinMAME plugin"s, '
            "g_isStandalone);\n")
        pinmame = self.root / "plugins" / "pinmame"
        pinmame.mkdir(parents=True)
        (pinmame / "plugin.cfg").write_text('[configuration]\nid = "PinMAME"\n')
        self.plugin.rename(self.root / "plugins" / "upscaledmd")

        _types, _contextual, _labels, registered = script.from_checkout(self.root)

        self.assertEqual(registered["Plugin.UpscaleDMD.Enable"], {"default": "0"})
        self.assertNotIn("Plugin.PinMAME.Enable", registered)


class CombinedTests(unittest.TestCase):
    def test_the_later_build_answers_for_a_setting_both_declare(self) -> None:
        types, contextual, _labels, _registered = script.combined([
            ({"DefaultProps\\Flasher.AddBlend": "bool", "Player.PlayMusic": "bool"},
             {"DefaultProps\\Flasher.AddBlend"}, {}, {}),
            ({"DefaultProps\\Flasher.AddBlend": "choice"}, set(), {}, {}),
        ])

        self.assertEqual(types, {"DefaultProps\\Flasher.AddBlend": "choice",
                                 "Player.PlayMusic": "bool"})
        self.assertEqual(contextual, set())

    def test_a_label_the_later_build_no_longer_gives_is_gone(self) -> None:
        _types, _contextual, labels, _registered = script.combined([
            ({"Plugin.B2S.ShowGrill": "bool", "Plugin.B2S.Old": "bool"}, set(),
             {"Plugin.B2S.ShowGrill": "Show Grill", "Plugin.B2S.Old": "Old Switch"}, {}),
            ({"Plugin.B2S.ShowGrill": "bool"}, set(), {}, {}),
        ])

        self.assertEqual(labels, {"Plugin.B2S.Old": "Old Switch"})

    def test_the_later_build_s_default_answers(self) -> None:
        _types, _contextual, _labels, registered = script.combined([
            ({"Plugin.B2S.ShowGrill": "bool", "Plugin.B2S.Old": "bool"}, set(), {},
             {"Plugin.B2S.ShowGrill": {"default": "1"}, "Plugin.B2S.Old": {"default": "0"}}),
            ({"Plugin.B2S.ShowGrill": "bool"}, set(), {},
             {"Plugin.B2S.ShowGrill": {"default": "0"}}),
        ])

        self.assertEqual(registered, {"Plugin.B2S.ShowGrill": {"default": "0"},
                                      "Plugin.B2S.Old": {"default": "0"}})

    def test_what_a_plugin_registers_renders_as_it_reads(self) -> None:
        text = script.rendered({"Plugin.X.Mode": "choice"}, registered={
            "Plugin.X.Mode": {"default": "1", "choices": (("1", "On"), ("2", "Off"))}})
        namespace: dict = {}
        exec(compile(text, "setting_types.py", "exec"), namespace)

        said = namespace["REGISTERED"]["Plugin.X.Mode"]
        self.assertEqual((said.default, said.minimum, said.choices),
                         ("1", None, (("1", "On"), ("2", "Off"))))

    def test_a_registration_survives_a_later_build_declaring_the_switch_itself(self) -> None:
        _types, _contextual, _labels, registered = script.combined([
            ({"Plugin.B2S.Enable": "bool"}, set(), {},
             {"Plugin.B2S.Enable": {"default": "0"}}),
            ({"Plugin.B2S.Enable": "bool"}, set(), {}, {}),
        ])

        self.assertEqual(registered, {"Plugin.B2S.Enable": {"default": "0"}})


def _sample(rev: int, *keys: str) -> tuple[int, frozenset[str]]:
    return rev, frozenset(keys)


class SpanTests(unittest.TestCase):
    def test_a_setting_every_build_declares_has_no_span(self) -> None:
        self.assertEqual(script.spans([_sample(10, "A.x"), _sample(20, "A.x")]), {})

    def test_a_setting_added_between_two_builds_may_be_in_the_later_one(self) -> None:
        found = script.spans([_sample(10, "A.x"), _sample(20, "A.x", "A.y")])

        self.assertEqual(found, {"A.y": (11, None)})

    def test_a_setting_taken_out_between_two_builds_is_still_in_the_earlier_one(self) -> None:
        found = script.spans([_sample(10, "A.x", "A.y"), _sample(20, "A.x")])

        self.assertEqual(found, {"A.y": (0, 20)})

    def test_a_setting_that_comes_and_goes_is_one_interval(self) -> None:
        found = script.spans([_sample(10, "A.x"), _sample(20, "A.x", "A.y"),
                              _sample(30, "A.x")])

        self.assertEqual(found, {"A.y": (11, 30)})

    def test_a_setting_that_comes_back_stops_the_run(self) -> None:
        with self.assertRaisesRegex(ValueError, "A.y comes and goes more than once"):
            script.spans([_sample(10, "A.y"), _sample(20), _sample(30, "A.y")])

    def test_an_exact_answer_replaces_the_bounds_of_the_gap(self) -> None:
        found = script.spans(
            [_sample(10, "A.x"), _sample(20, "A.x", "A.y")],
            exact=lambda n, keys: dict.fromkeys(keys, 14))

        self.assertEqual(found, {"A.y": (14, None)})

    def test_the_exact_answer_is_asked_only_where_something_differs(self) -> None:
        asked: list[int] = []

        def exact(n: int, keys: frozenset[str]) -> dict[str, int]:
            asked.append(n)
            return dict.fromkeys(keys, 12)

        script.spans([_sample(10, "A.x"), _sample(20, "A.x"), _sample(30)], exact=exact)

        self.assertEqual(asked, [1])


class LocatedTests(unittest.TestCase):
    """`located` over a run of positions where each setting appears once."""

    @staticmethod
    def _run(appears: dict[str, int]):
        probes: list[int] = []

        def at(position: int) -> frozenset[str]:
            probes.append(position)
            return frozenset(key for key, first in appears.items() if position >= first)
        return at, probes

    def test_each_setting_is_found_at_the_position_it_appears(self) -> None:
        at, _probes = self._run({"A.x": 3, "A.y": 12})

        found = script.located(frozenset({"A.x", "A.y"}), 0, 16, at)

        self.assertEqual(found, {"A.x": 3, "A.y": 12})

    def test_a_setting_that_goes_is_found_where_it_is_first_missing(self) -> None:
        def at(position: int) -> frozenset[str]:
            return frozenset({"A.x"}) if position < 5 else frozenset()

        self.assertEqual(script.located(frozenset({"A.x"}), 0, 9, at), {"A.x": 5})

    def test_the_run_is_searched_by_halving_not_read_through(self) -> None:
        at, probes = self._run({"A.x": 700})

        script.located(frozenset({"A.x"}), 0, 1000, at)

        self.assertLess(len(probes), 25)

    def test_nothing_differing_reads_nothing(self) -> None:
        at, probes = self._run({})

        self.assertEqual(script.located(frozenset(), 0, 100, at), {})
        self.assertEqual(probes, [])


class BuildIdentityTests(unittest.TestCase):
    def test_the_line_is_major_minor_and_patch_side_by_side(self) -> None:
        header = ("#define VP_VERSION_MAJOR 10\n#define VP_VERSION_MINOR 8\n"
                  "#define VP_VERSION_REV 1\n")

        self.assertEqual(script.line_of(header), "1081")

    def test_a_header_that_does_not_say_stops_the_run(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not say the version"):
            script.line_of("#define VP_VERSION_MAJOR 10\n")

    def test_the_rev_counts_from_the_commit_the_workflow_names(self) -> None:
        self.assertEqual(script.start_of("env:\n  VERSION_START_SHA: 6f9b0ae1c\n"), "6f9b0ae1c")

    def test_a_workflow_that_does_not_say_stops_the_run(self) -> None:
        with self.assertRaisesRegex(ValueError, "Rev starts counting"):
            script.start_of("env:\n  OTHER: 1\n")


class SpansRenderTests(unittest.TestCase):
    def test_the_line_and_the_spans_render_as_they_read(self) -> None:
        text = script.rendered({"A.x": "bool"}, line="1081",
                               where={"A.x": (5, None), "A.y": (0, 9)})
        namespace: dict = {}
        exec(compile(text, "setting_types.py", "exec"), namespace)

        self.assertEqual(namespace["LINE"], "1081")
        self.assertEqual(tuple(namespace["SPANS"]["A.x"]), (5, None))
        self.assertEqual(tuple(namespace["SPANS"]["A.y"]), (0, 9))


class MapTests(unittest.TestCase):
    def test_the_sound_switches_the_installed_build_writes_are_switches(self) -> None:
        for qualified in ("Player.PlaySound", "Player.PlayMusic", "Plugin.PinMAME.Sound"):
            with self.subTest(qualified=qualified):
                self.assertEqual(TYPES.get(qualified), "bool")


if __name__ == "__main__":
    unittest.main()
