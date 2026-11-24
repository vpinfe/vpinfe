"""The action set is declared once, and the copies that cannot import it are checked.

Ten actions, each with one binding list. The names say what the player meant rather than
which way a stick moved, and a binding names its own input - so the twelve-action,
two-key-per-action shape is gone and with it the table that translated `key*` to `joy*`.

What cannot import Python is pinned here: core.js's fallback bindings, the contract 1
name map that keeps twelve published themes working, and the gamepad binding page.
"""

from __future__ import annotations

import configparser
import re
import unittest
from pathlib import Path

from common.config import config_schema, input_registry
from frontend import input_api

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CORE_JS_PATH = REPO_ROOT / "frontend" / "static" / "common" / "vpinfe-core.js"
CORE_JS = _CORE_JS_PATH.read_text(encoding="utf-8")


class RegistryTests(unittest.TestCase):
    def test_there_are_eleven_actions(self) -> None:
        """Twelve became ten, up/down and pageup/pagedown being one intent twice, and
        Take Picture made eleven."""
        self.assertEqual([a.name for a in input_registry.actions()],
                         ["previous", "next", "page_previous", "page_next", "select",
                          "back", "menu", "collection_menu", "tutorial", "exit",
                          "take_picture"])

    def test_the_actions_heard_in_play_are_registered_and_start_unbound(self) -> None:
        """Nothing is listened for during play until the first of them is bound."""
        names = {a.name: a for a in input_registry.actions()}
        self.assertTrue(set(input_registry.IN_PLAY) <= set(names))
        self.assertEqual(names[input_registry.IN_PLAY[0]].bindings, ())

    def test_every_action_is_a_config_option(self) -> None:
        declared = {(o.section, o.key) for o in config_schema.options()}
        for action in input_registry.actions():
            self.assertIn((input_registry.SECTION, action.name), declared)

    def test_the_shipped_bindings_are_the_config_default(self) -> None:
        for action in input_registry.actions():
            option = config_schema.option(input_registry.SECTION, action.name)
            assert option is not None
            self.assertEqual(option.default, ",".join(action.bindings))

    def test_an_old_key_still_names_its_action(self) -> None:
        for old, expect in (("keyleft", "previous"), ("joyleft", "previous"),
                            ("joyup", "page_previous"), ("keypagedown", "page_next"),
                            ("joycollectionmenu", "collection_menu")):
            self.assertEqual(input_registry.action_for_legacy_key(old), expect)


class BindingProjectionTests(unittest.TestCase):
    """The Manager shows a keyboard field and a controller field over one list."""

    BINDINGS = ["key:ArrowLeft", "key:ShiftLeft", "pad:0/button:4",
                "chord(pad:0/button:4+pad:0/button:5)@hold:1000"]

    def test_each_field_shows_its_own_input(self) -> None:
        self.assertEqual(input_registry.keys_in(self.BINDINGS), ["ArrowLeft", "ShiftLeft"])
        self.assertEqual(input_registry.pad_buttons_in(self.BINDINGS), ["4"])

    def test_what_neither_field_can_show_is_kept(self) -> None:
        """Dropping it would delete a cabinet's hold-both-flippers binding on Save."""
        self.assertEqual(input_registry.unrenderable(self.BINDINGS),
                         ["chord(pad:0/button:4+pad:0/button:5)@hold:1000"])

    def test_an_old_value_becomes_selectors(self) -> None:
        self.assertEqual(input_registry.binding_for_legacy("keyleft", "ArrowLeft,ShiftLeft"),
                         ["key:ArrowLeft", "key:ShiftLeft"])
        self.assertEqual(input_registry.binding_for_legacy("joyleft", "3"),
                         ["pad:0/button:3"])


class CollisionTests(unittest.TestCase):
    """A binding two actions hold only ever fires the first of them, and nothing said so.

    Dispatch resolves a key to the first action listing it, so the loser is not a
    conflict somebody is asked about - it is a binding that does nothing, made on a page
    that showed no sign.
    """

    def test_a_binding_one_action_holds_is_not_a_collision(self) -> None:
        found = input_registry.collisions(
            {"previous": ["key:ArrowLeft"], "next": ["key:ArrowRight"]})

        self.assertEqual(found, {})

    def test_a_binding_two_actions_hold_names_both(self) -> None:
        found = input_registry.collisions(
            {"back": ["key:b", "key:Escape"], "exit": ["key:Escape", "key:q"]})

        self.assertEqual(found, {"key:Escape": ["back", "exit"]})

    def test_an_action_listing_one_twice_does_not_collide_with_itself(self) -> None:
        self.assertEqual(
            input_registry.collisions({"back": ["key:b", "key:b"]}), {})

    def test_holders_answers_who_has_it_before_anything_clashes(self) -> None:
        """Refusing a binding another action already holds needs this, not the clash
        list - which by definition only names what is already broken."""
        found = input_registry.holders({"previous": ["key:F9"], "next": []})

        self.assertEqual(found, {"key:F9": ["previous"]})

    def test_the_shipped_bindings_do_not_collide(self) -> None:
        self.assertEqual(input_registry.collisions(input_registry.defaults()), {})

    def test_a_tap_and_a_hold_of_one_key_are_not_a_collision(self) -> None:
        """A key has one press and one hold; Esc is Back tapped and Quit held."""
        found = input_registry.collisions(
            {"back": ["key:Escape"], "exit": ["key:Escape@hold:1500"]})

        self.assertEqual(found, {})

    def test_a_key_has_one_hold_whatever_its_length(self) -> None:
        found = input_registry.collisions(
            {"menu": ["key:Escape@hold:800"], "exit": ["key:Escape@hold:1500"]})

        self.assertEqual(list(found.values()), [["menu", "exit"]])

    def test_holders_names_a_held_key_by_the_key(self) -> None:
        """So the Manager can refuse a second hold on a key before it is stored."""
        found = input_registry.holders({"exit": ["key:Escape@hold:1500"]})

        self.assertEqual(
            found[input_registry.contention("key:Escape@hold:300")], ["exit"])

    def test_a_numbered_pad_button_and_the_standard_button_are_one_press(self) -> None:
        """A numbered binding fires on a pad that reports the standard layout, so the two
        spellings of one button are one claim."""
        self.assertEqual(input_registry.contention("pad:0/button:0"),
                         input_registry.contention("pad:standard/button:0"))
        self.assertNotEqual(input_registry.contention("pad:0/button:0"),
                            input_registry.contention("pad:standard/button:1"))

    def test_holders_names_a_numbered_button_by_the_standard_one(self) -> None:
        found = input_registry.holders({"select": ["pad:0/button:0"]})

        self.assertEqual(
            found[input_registry.contention("pad:standard/button:0")], ["select"])

    def test_a_numbered_and_a_standard_button_on_two_actions_collide(self) -> None:
        found = input_registry.collisions(
            {"select": ["pad:0/button:0"], "back": ["pad:standard/button:0"]})

        self.assertEqual(list(found.values()), [["select", "back"]])

    def test_a_held_standard_button_is_the_held_numbered_one(self) -> None:
        self.assertEqual(
            input_registry.contention("pad:standard/button:1@hold:1500"),
            input_registry.contention("pad:3/button:1@hold:800"))

    def test_two_chords_of_different_lengths_are_two_bindings(self) -> None:
        """Each chord waits on its own timer, so a chord keeps its length."""
        self.assertNotEqual(
            input_registry.contention("chord(key:a+key:b)@hold:800"),
            input_registry.contention("chord(key:a+key:b)@hold:1500"))

    def test_an_install_that_holds_one_is_told(self) -> None:
        """The Manager refuses new ones; an install that already has one gets a line in
        the log, because the alternative is a player concluding the cabinet is broken."""
        input_api._said_collisions.clear()
        parser = configparser.ConfigParser()
        parser.add_section("input")
        parser.set("input", "back", "key:b,key:Escape")
        parser.set("input", "exit", "key:Escape")

        with self.assertLogs("vpinfe.frontend.input_api", level="WARNING") as caught:
            input_api.get_bindings(parser)

        self.assertIn("Esc", caught.output[0])
        self.assertIn("back", caught.output[0])
        self.assertIn("exit", caught.output[0])

    def test_it_is_said_once_and_not_on_every_page(self) -> None:
        input_api._said_collisions.clear()
        parser = configparser.ConfigParser()
        parser.add_section("input")
        parser.set("input", "back", "key:b,key:Escape")
        parser.set("input", "exit", "key:Escape")
        with self.assertLogs("vpinfe.frontend.input_api", level="WARNING"):
            input_api.get_bindings(parser)

        with self.assertNoLogs("vpinfe.frontend.input_api", level="WARNING"):
            input_api.get_bindings(parser)


# The standard `KeyboardEvent.code` values, from the UI Events code tables: the writing
# system, functional, control pad, arrow pad, numpad, function and media sections.
STANDARD_CODES = (
    "Backquote Backslash BracketLeft BracketRight Comma Equal IntlBackslash IntlRo IntlYen "
    "Minus Period Quote Semicolon Slash "
    "AltLeft AltRight Backspace CapsLock ContextMenu ControlLeft ControlRight Enter "
    "MetaLeft MetaRight ShiftLeft ShiftRight Space Tab "
    "Convert KanaMode Lang1 Lang2 Lang3 Lang4 Lang5 NonConvert "
    "Delete End Help Home Insert PageDown PageUp "
    "ArrowDown ArrowLeft ArrowRight ArrowUp "
    "NumLock NumpadAdd NumpadBackspace NumpadClear NumpadClearEntry NumpadComma "
    "NumpadDecimal NumpadDivide NumpadEnter NumpadEqual NumpadHash NumpadMemoryAdd "
    "NumpadMemoryClear NumpadMemoryRecall NumpadMemoryStore NumpadMemorySubtract "
    "NumpadMultiply NumpadParenLeft NumpadParenRight NumpadStar NumpadSubtract "
    "Escape Fn FnLock PrintScreen ScrollLock Pause "
    "BrowserBack BrowserFavorites BrowserForward BrowserHome BrowserRefresh BrowserSearch "
    "BrowserStop Eject LaunchApp1 LaunchApp2 LaunchMail MediaPlayPause MediaSelect "
    "MediaStop MediaTrackNext MediaTrackPrevious Power Sleep AudioVolumeDown "
    "AudioVolumeMute AudioVolumeUp WakeUp"
).split() + [f"Key{letter}" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"] \
    + [f"Digit{n}" for n in range(10)] + [f"Numpad{n}" for n in range(10)]


class ReadableBindingTests(unittest.TestCase):
    """A selector is written for a parser. Ten of them on a settings page is what made
    input read as configuration rather than a choice."""

    def test_a_key_is_named_as_a_keyboard_prints_it(self) -> None:
        for selector, expected in (("key:ArrowLeft", "Left Arrow"),
                                   ("key:ShiftLeft", "Left Shift"),
                                   ("key:KeyM", "M"),
                                   ("key:Digit4", "4"),
                                   ("key:Escape", "Esc"),
                                   ("key:b", "B"),
                                   ("key:Comma", ",")):
            with self.subTest(selector=selector):
                self.assertEqual(input_registry.describe(selector), expected)

    def test_a_numbered_pad_s_button_reads_without_the_pad(self) -> None:
        """Dispatch fires it on that button of every pad, so a chip that named one pad
        would say something the binding does not do."""
        for selector in ("pad:0/button:3", "pad:2/button:3"):
            with self.subTest(selector=selector):
                self.assertEqual(input_registry.describe(selector), "Button 3")

    def test_a_chord_and_a_hold_read_each_member_without_its_pad(self) -> None:
        chord = "chord(pad:0/button:3+pad:0/button:4)@hold:1500"

        self.assertEqual(input_registry.describe(chord), "Button 3 + Button 4, held 1.5s")

    def test_a_standard_button_reads_by_its_name_and_a_face_button_by_its_number(
            self) -> None:
        for button, expected in ((0, "Button 0"), (1, "Button 1"),
                                 (2, "Button 2"), (3, "Button 3"),
                                 (4, "Left Shoulder"), (5, "Right Shoulder"),
                                 (6, "Left Trigger"), (7, "Right Trigger"),
                                 (8, "Button 8"), (9, "Button 9"),
                                 (10, "Left Stick"), (11, "Right Stick"),
                                 (12, "D-pad Up"), (13, "D-pad Down"),
                                 (14, "D-pad Left"), (15, "D-pad Right")):
            with self.subTest(button=button):
                self.assertEqual(
                    input_registry.describe(f"pad:standard/button:{button}"), expected)

    def test_a_standard_button_with_no_name_is_its_number_and_never_the_layout(self) -> None:
        """The layout's name is a selector word. It must not reach the screen, in any
        language, so what has no name reads as a number."""
        said = input_registry.describe("pad:standard/button:16")

        self.assertEqual(said, "Button 16")
        self.assertNotIn("standard", said.lower())

    def test_a_standard_button_on_screen_goes_through_the_catalog(self) -> None:
        """In the pseudo-locale every word is rewritten, so one that is not is a word the
        catalog never held."""
        from common import i18n

        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language("qps")
        said = [input_registry.describe(f"pad:standard/button:{n}") for n in (0, 12, 16)]
        said.append(input_registry.describe("pad:0/button:3"))

        for words in said:
            with self.subTest(words=words):
                self.assertNotRegex(words, r"[A-Za-z]{3}")

    def test_no_standard_code_is_shown_as_it_was_stored(self) -> None:
        """In the pseudo-locale, where an English name that happens to match its code
        (Enter, Tab) still reads differently when it came from the catalog."""
        from common import i18n

        self.addCleanup(i18n.set_language, i18n.language())
        i18n.set_language("qps")
        raw = [code for code in STANDARD_CODES
               if input_registry.describe(f"key:{code}") == code]

        self.assertEqual([], raw)

    def test_a_numpad_key_is_said_by_what_is_printed_on_it(self) -> None:
        for code, expected in (("NumpadAdd", "Numpad +"), ("NumpadDecimal", "Numpad ."),
                               ("NumpadEnter", "Numpad Enter")):
            with self.subTest(code=code):
                self.assertEqual(expected, input_registry.describe(f"key:{code}"))

    def test_what_it_cannot_name_comes_back_as_it_was(self) -> None:
        """Inventing a name for a chord would be worse than showing the one stored."""
        for selector in ("pad:0/chord(1,2)", "key:ArrowLeft@hold", "nonsense"):
            with self.subTest(selector=selector):
                self.assertEqual(input_registry.describe(selector), selector)


class ChordIdentityTests(unittest.TestCase):
    """A chord is a set of inputs held together, so it is compared as one.

    And it does not fold into its members: both flippers fire their own actions *and*
    the chord, which is the decision rather than an oversight - so a chord and a plain
    binding on one of its members are two bindings that both work.
    """

    def test_a_chord_written_either_way_round_is_one_binding(self) -> None:
        self.assertEqual(
            input_registry.identity("chord(key:b+key:a)"),
            input_registry.identity("chord(key:a+key:b)"))

    def test_two_actions_holding_the_same_chord_collide(self) -> None:
        found = input_registry.collisions(
            {"exit": ["chord(key:a+key:b)"], "back": ["chord(key:b+key:a)"]})

        self.assertEqual(list(found.values()), [["exit", "back"]])

    def test_a_chord_does_not_collide_with_its_own_members(self) -> None:
        """The shipped idiom is exactly this - hold both flippers, and both flippers are
        bound. A rule that called it a collision would refuse the one example the design
        is built around."""
        self.assertEqual(
            input_registry.collisions({
                "exit": ["chord(key:ShiftLeft+key:ShiftRight)@hold:1500"],
                "previous": ["key:ShiftLeft"],
                "next": ["key:ShiftRight"]}),
            {})

    def test_a_hold_is_part_of_what_makes_two_bindings_different(self) -> None:
        self.assertNotEqual(input_registry.identity("chord(key:a+key:b)"),
                            input_registry.identity("chord(key:a+key:b)@hold:1500"))

    def test_a_repeated_member_is_held_once(self) -> None:
        self.assertEqual(input_registry.chord_members("chord(key:a+key:a+key:b)"),
                         ("key:a", "key:b"))

    def test_what_is_not_a_chord_has_no_members(self) -> None:
        for selector in ("key:a", "pad:0/button:1", "chord(", ""):
            with self.subTest(selector=selector):
                self.assertEqual(input_registry.chord_members(selector), ())


class OneSpellingPerKeyTests(unittest.TestCase):
    """A browser reports two things for a key: what it produces (`event.key`, "c") and
    which key it is (`event.code`, "KeyC").

    The defaults were written in the first and capture stores the second. Dispatch matches
    on both of an event's tokens, so both work - and a config could hold one key under two
    spellings with nothing to say so. That is the same silence B6 was about.
    """

    def test_a_single_character_becomes_its_code(self) -> None:
        for selector, expected in (("key:c", "key:KeyC"), ("key:B", "key:KeyB"),
                                   ("key:4", "key:Digit4"), ("key:,", "key:Comma")):
            with self.subTest(selector=selector):
                self.assertEqual(input_registry.normalize(selector), expected)

    def test_a_code_is_already_the_answer(self) -> None:
        for selector in ("key:KeyC", "key:ArrowLeft", "key:Escape", "key:F9"):
            with self.subTest(selector=selector):
                self.assertEqual(input_registry.normalize(selector), selector)

    def test_it_leaves_alone_what_it_does_not_understand(self) -> None:
        """Guessing would invent a key nobody pressed."""
        for selector in ("pad:0/button:1", "chord(key:a+key:b)", "", "nonsense"):
            with self.subTest(selector=selector):
                self.assertEqual(input_registry.normalize(selector), selector)

    def test_two_spellings_of_one_key_are_one_clash(self) -> None:
        found = input_registry.collisions({"back": ["key:c"], "menu": ["key:KeyC"]})

        self.assertEqual(found, {"key:KeyC": ["back", "menu"]})

    def test_a_chord_normalizes_its_members_too(self) -> None:
        self.assertEqual(input_registry.identity("chord(key:b+key:a)"),
                         input_registry.identity("chord(key:KeyA+key:KeyB)"))

    def test_both_spellings_read_the_same_on_screen(self) -> None:
        self.assertEqual(input_registry.describe("key:c"),
                         input_registry.describe("key:KeyC"))

    def test_the_shipped_defaults_are_codes(self) -> None:
        """So a captured binding and a shipped one are spelled the same way, which is
        what stops the two drifting again."""
        for action in input_registry.actions():
            for binding in action.bindings:
                with self.subTest(action=action.name, binding=binding):
                    self.assertEqual(input_registry.normalize(binding), binding)


class HoldEditingTests(unittest.TestCase):
    """A hold is a modifier on a binding rather than a different binding, so adding one
    after the fact is editing a suffix and nothing else."""

    def test_it_round_trips(self) -> None:
        for binding in ("key:Escape", "pad:0/button:3", "chord(key:a+key:b)"):
            with self.subTest(binding=binding):
                held = input_registry.with_hold(binding, 1500)
                self.assertEqual(input_registry.hold_ms(held), 1500)
                self.assertEqual(input_registry.with_hold(held, 0), binding)

    def test_a_negative_duration_is_no_hold_rather_than_a_broken_one(self) -> None:
        self.assertEqual(input_registry.with_hold("key:a", -50), "key:a")


class ComposedNameTests(unittest.TestCase):
    def test_a_chord_reads_as_its_parts(self) -> None:
        self.assertEqual(
            input_registry.describe("chord(key:ShiftLeft+key:ShiftRight)@hold:1500"),
            "Left Shift + Right Shift, held 1.5s")

    def test_a_hold_is_said_in_seconds(self) -> None:
        """A hold is something a person counts, not a number of milliseconds."""
        self.assertEqual(input_registry.describe("key:Escape@hold:800"),
                         "Esc, held 0.8s")
        self.assertEqual(input_registry.describe("key:Escape@hold:2000"),
                         "Esc, held 2s")

    def test_a_selector_it_still_cannot_name_comes_back_whole(self) -> None:
        for selector in ("pad:0/axis:1+@deadzone:0.5", "key:ctrl+KeyQ"):
            with self.subTest(selector=selector):
                self.assertEqual(input_registry.describe(selector), selector)


class WhatCaptureCannotRemakeTests(unittest.TestCase):
    """Asked before offering to delete a binding: removing what nothing can rebuild is a
    door that only opens one way.

    The set shrank when capture learned to watch until release - a chord is pressing two
    things and a hold is keeping one down, so both are gestures now.
    """

    def test_anything_a_gesture_can_make(self) -> None:
        for selector in ("key:ArrowLeft", "pad:0/button:3",
                         "chord(key:a+key:b)", "key:Escape@hold:1500",
                         "chord(key:a+key:b)@hold:1500"):
            with self.subTest(selector=selector):
                self.assertTrue(input_registry.capturable(selector))

    def test_a_modifier_and_an_axis_have_no_gesture(self) -> None:
        """Shift is a flipper on a cabinet and has to stay bindable on its own, so a
        modifier cannot be captured by pressing one; an axis is a tuned value rather
        than a press."""
        for selector in ("key:ctrl+KeyQ", "key:shift+KeyQ",
                         "pad:0/axis:1+@deadzone:0.5"):
            with self.subTest(selector=selector):
                self.assertFalse(input_registry.capturable(selector))


class WhatAPressIsTests(unittest.TestCase):
    """A row is matched to a press: which input went down, however long it then stays."""

    def test_a_hold_is_the_same_press_as_a_tap(self) -> None:
        self.assertEqual(input_registry.press("key:Escape@hold:1500"),
                         input_registry.press("key:Escape"))

    def test_a_key_is_the_same_press_however_it_is_spelled(self) -> None:
        self.assertEqual(input_registry.press("key:c"), input_registry.press("key:KeyC"))

    def test_a_numbered_pad_is_the_same_press_on_any_pad(self) -> None:
        """Dispatch ignores which pad a button is on."""
        self.assertEqual(input_registry.press("pad:0/button:3"),
                         input_registry.press("pad:2/button:3"))
        self.assertNotEqual(input_registry.press("pad:0/button:3"),
                            input_registry.press("pad:0/button:4"))

    def test_a_standard_pad_is_the_same_press_as_a_numbered_one(self) -> None:
        """A numbered binding fires for a pad in the standard layout too, so the two want
        one press - the first hands the button to whichever action is listed first."""
        self.assertEqual(input_registry.press("pad:standard/button:0"),
                         input_registry.press("pad:0/button:0"))
        self.assertNotEqual(input_registry.press("pad:standard/button:0"),
                            input_registry.press("pad:standard/button:1"))

    def test_a_chord_with_a_standard_button_is_the_chord_with_a_numbered_one(self) -> None:
        self.assertEqual(input_registry.press("chord(key:a+pad:standard/button:2)"),
                         input_registry.press("chord(pad:1/button:2+key:a)"))

    def test_a_chord_is_the_same_press_in_either_order(self) -> None:
        self.assertEqual(input_registry.press("chord(key:b+key:a)@hold:1500"),
                         input_registry.press("chord(key:a+key:b)"))

    def test_a_pad_is_told_from_a_key(self) -> None:
        self.assertTrue(input_registry.is_pad("pad:standard/button:0"))
        self.assertTrue(input_registry.is_pad("pad:1/button:2@hold:1000"))
        self.assertTrue(input_registry.is_pad("chord(key:a+pad:0/button:1)"))
        self.assertFalse(input_registry.is_pad("key:Escape"))
        self.assertFalse(input_registry.is_pad("chord(key:a+key:b)"))


class OnePressAndOneHoldTests(unittest.TestCase):
    """A key has a press and a hold, and each is somebody's. Putting a second hold on a
    key replaces the first rather than leaving it held two ways."""

    def test_a_new_length_replaces_the_old_one_where_it_stands(self) -> None:
        self.assertEqual(input_registry.replaced(
            ["key:Escape@hold:1000", "key:KeyQ"], "key:Escape@hold:1000",
            "key:Escape@hold:2000"), ["key:Escape@hold:2000", "key:KeyQ"])

    def test_another_length_of_the_same_key_is_dropped(self) -> None:
        self.assertEqual(input_registry.replaced(
            ["key:Escape@hold:500", "key:Escape@hold:1000"], "key:Escape@hold:1000",
            "key:Escape@hold:2000"), ["key:Escape@hold:2000"])

    def test_a_press_does_not_disturb_the_hold_on_the_same_key(self) -> None:
        self.assertEqual(input_registry.replaced(
            ["key:Escape", "key:Escape@hold:1500"], "key:Escape", "key:Escape"),
            ["key:Escape", "key:Escape@hold:1500"])

    def test_taking_a_binding_leaves_the_rest(self) -> None:
        self.assertEqual(input_registry.without(
            ["key:Escape@hold:1500", "key:KeyQ", "key:Escape"], "key:Escape@hold:1500"),
            ["key:KeyQ", "key:Escape"])

    def test_a_chord_keeps_its_length(self) -> None:
        """Each chord waits on its own timer, so two lengths are two bindings."""
        self.assertEqual(input_registry.without(
            ["chord(key:a+key:b)@hold:1000", "chord(key:a+key:b)@hold:2000"],
            "chord(key:a+key:b)@hold:1000"), ["chord(key:a+key:b)@hold:2000"])


class ScrollingThatWouldStepTests(unittest.TestCase):
    """A key with a hold acts on release, and an action on release cannot repeat, so a
    scrolling action sharing its key with a hold steps once per tap."""

    HELD = {"previous": ["key:ShiftLeft"], "exit": ["key:ShiftLeft@hold:1500"]}

    def test_it_names_the_press_the_hold_and_who_holds_it(self) -> None:
        self.assertEqual(
            input_registry.stalled(self.HELD),
            [input_registry.Stall("previous", "key:ShiftLeft", "exit",
                                  "key:ShiftLeft@hold:1500")])

    def test_a_hold_on_a_key_nothing_scrolls_on_stalls_nothing(self) -> None:
        self.assertEqual(input_registry.stalled(
            {"previous": ["key:ArrowLeft"], "exit": ["key:ShiftLeft@hold:1500"]}), [])

    def test_a_chord_keeps_its_own_timer_and_is_left_out(self) -> None:
        self.assertEqual(input_registry.stalled(
            {"previous": ["key:ShiftLeft"],
             "exit": ["chord(key:ShiftLeft+key:ShiftRight)@hold:1500"]}), [])

    def test_only_what_a_change_stalls_is_new(self) -> None:
        was = {"previous": ["key:ShiftLeft"], "exit": ["key:Escape@hold:1500"]}

        self.assertEqual(input_registry.newly_stalled(was, was), [])
        self.assertEqual([one.action for one in input_registry.newly_stalled(was, self.HELD)],
                         ["previous"])
        self.assertEqual(input_registry.newly_stalled(self.HELD, self.HELD), [])

    def test_the_actions_that_scroll_are_the_ones_the_frontend_repeats(self) -> None:
        block = re.search(r"const REPEATING_ACTIONS = new Set\(\s*\[(.*?)\]\);", CORE_JS, re.S)
        self.assertIsNotNone(block, "REPEATING_ACTIONS moved; this test needs updating")
        assert block is not None

        self.assertEqual(tuple(re.findall(r'"(\w+)"', block.group(1))),
                         input_registry.SCROLLING)


class JavaScriptCopiesTests(unittest.TestCase):
    def test_the_fallback_bindings_match_the_shipped_ones(self) -> None:
        block = re.search(r"this\.keyActionMap = \{(.*?)\n    \};", CORE_JS, re.S)
        self.assertIsNotNone(block, "keyActionMap moved; this test needs updating")
        assert block is not None
        js = {name: [v.strip().strip("'\"").lower() for v in vals.split(",") if v.strip()]
              for name, vals in re.findall(r"(\w+):\s*\[([^\]]*)\]", block.group(1))}

        expected = {a.name: [k.lower() for k in input_registry.keys_in(a.bindings)]
                    for a in input_registry.actions()}
        self.assertEqual(js, expected)

    def test_the_fallback_holds_match_the_shipped_ones(self) -> None:
        """Before the bridge answers, a tap of Esc must already be Back and not Quit."""
        block = re.search(r"this\.keyHoldMap = \{(.*?)\};", CORE_JS, re.S)
        self.assertIsNotNone(block, "keyHoldMap moved; this test needs updating")
        assert block is not None
        js = {name: [(token.lower(), int(ms)) for token, ms in re.findall(
                  r"token:\s*['\"](\w+)['\"],\s*ms:\s*(\d+)", entries)]
              for name, entries in re.findall(r"(\w+):\s*\[([^\]]*)\]", block.group(1))}

        expected: dict[str, list[tuple[str, int]]] = {}
        for action in input_registry.actions():
            for binding in action.bindings:
                held = input_registry.hold_ms(binding)
                if held and binding.startswith(input_registry.KEY_PREFIX):
                    token = input_registry.with_hold(binding, 0)[len(input_registry.KEY_PREFIX):]
                    expected.setdefault(action.name, []).append((token.lower(), held))
        self.assertEqual(js, expected)

    def test_a_held_esc_quits_after_a_second_and_a_half(self) -> None:
        self.assertEqual(input_registry.QUIT_HOLD_MS, 1500)
        self.assertEqual(input_registry.defaults()["exit"][0], "key:Escape@hold:1500")
        self.assertIn("ms: 1500", CORE_JS)

    def test_contract_1_gets_a_name_for_every_action(self) -> None:
        """A missing entry means a published theme's `case` silently stops matching."""
        block = re.search(r"const LEGACY_ACTION_NAMES = \{(.*?)\n\};", CORE_JS, re.S)
        self.assertIsNotNone(block, "the legacy name map moved")
        assert block is not None
        js = dict(re.findall(r"(\w+):\s*\"(\w+)\"", block.group(1)))

        self.assertEqual(js, {a.name: a.legacy_joy_key for a in input_registry.actions()
                              if a.legacy_joy_key})


class PlayActionsTests(unittest.TestCase):
    def test_the_page_hears_in_play_what_the_registry_says(self) -> None:
        block = re.search(r"const PLAY_ACTIONS = new Set\(\[(.*?)\]\);", CORE_JS, re.S)
        self.assertIsNotNone(block, "PLAY_ACTIONS moved; this test needs updating")
        assert block is not None
        self.assertEqual(re.findall(r'"(\w+)"', block.group(1)),
                         list(input_registry.IN_PLAY))


class ShippedDefaultTests(unittest.TestCase):
    """The keyboard and controller defaults as shipped. Dispatch is tested in
    `tests/js/input.test.js` and the capture in `tests/manager`."""

    def test_esc_is_back_and_a_held_esc_is_quit(self) -> None:
        shipped = input_registry.defaults()

        self.assertIn("key:Escape", shipped["back"])
        self.assertNotIn("key:Escape", shipped["exit"],
                         "a tap of Esc used to quit from the root of the list")
        self.assertIn("key:Escape@hold:1500", shipped["exit"])

    def test_the_action_is_called_quit(self) -> None:
        exit_action = next(a for a in input_registry.actions() if a.name == "exit")

        self.assertEqual(exit_action.label, "Quit")

    def test_t_is_not_tutorial_on_a_cabinet(self) -> None:
        """T is the tilt bob in Visual Pinball, so a cabinet's Tutorial has no key."""
        changed = input_registry.cabinet_bindings(
            {k: list(v) for k, v in input_registry.defaults().items()}, CABINET)

        self.assertEqual(changed["tutorial"], [])
        for name, bindings in changed.items():
            self.assertNotIn("key:KeyT", bindings, name)

    def test_start_is_select_on_a_cabinet(self) -> None:
        changed = input_registry.cabinet_bindings(
            {k: list(v) for k, v in input_registry.defaults().items()}, CABINET)

        self.assertEqual(changed["select"][0], "key:Digit1")

    def test_the_controller_defaults_are_for_the_standard_layout(self) -> None:
        shipped = input_registry.defaults()

        self.assertEqual(
            {name: [one for one in bindings if one.startswith("pad:")]
             for name, bindings in shipped.items() if name != "take_picture"},
            {"previous": ["pad:standard/button:14", "pad:standard/button:6"],
             "next": ["pad:standard/button:15", "pad:standard/button:7"],
             "page_previous": ["pad:standard/button:12", "pad:standard/button:4"],
             "page_next": ["pad:standard/button:13", "pad:standard/button:5"],
             "select": ["pad:standard/button:0"],
             "back": ["pad:standard/button:1"],
             "menu": ["pad:standard/button:9"],
             "collection_menu": ["pad:standard/button:8"],
             "tutorial": [],
             "exit": ["pad:standard/button:1@hold:1500"]})

    def test_a_standard_binding_is_not_a_numbered_pads_button(self) -> None:
        """The controller field writes `pad:0` back, which would turn a default for
        standard pads into one for any pad."""
        self.assertEqual(
            input_registry.pad_buttons_in(["pad:standard/button:0", "pad:2/button:3"]),
            ["3"])


# The key each button of Visual Pinball's own table sends.
CABINET = {
    "left_flipper": ("ShiftLeft",), "right_flipper": ("ShiftRight",),
    "left_magna": ("ControlLeft",), "right_magna": ("ControlRight",),
    "start": ("Digit1",), "fire": ("AltLeft",), "extra_ball": ("KeyB",),
    "tilt": ("KeyT",), "exit": ("Escape",),
}


class CabinetDefaultsTests(unittest.TestCase):
    """The shipped layout is a keyboard's. A cabinet sends what Visual Pinball is wired
    for, so the actions nobody has changed take those keys instead."""

    def _stored(self, **chosen: list[str]) -> dict[str, list[str]]:
        return {**{k: list(v) for k, v in input_registry.defaults().items()}, **chosen}

    def test_each_button_lands_on_the_action_it_stands_for(self) -> None:
        changed = input_registry.cabinet_bindings(self._stored(), CABINET)

        self.assertEqual(changed["page_previous"][0], "key:ControlLeft")
        self.assertEqual(changed["page_next"][0], "key:ControlRight")
        self.assertEqual(changed["select"][0], "key:Digit1")
        self.assertEqual(changed["menu"][0], "key:AltLeft")
        self.assertEqual(changed["collection_menu"][0], "key:KeyB")
        self.assertEqual(changed["back"][0], "key:Escape")

    def test_the_keyboard_keys_stay_beside_them(self) -> None:
        changed = input_registry.cabinet_bindings(self._stored(), CABINET)

        self.assertIn("key:Enter", changed["select"])
        self.assertIn("key:PageUp", changed["page_previous"])

    def test_what_is_already_right_is_left_out(self) -> None:
        """The flippers are Shift in both, and Quit is already a held Esc."""
        changed = input_registry.cabinet_bindings(self._stored(), CABINET)

        for name in ("previous", "next", "exit"):
            self.assertNotIn(name, changed)

    def test_the_controller_half_is_never_touched(self) -> None:
        changed = input_registry.cabinet_bindings(self._stored(), CABINET)

        for name, bindings in changed.items():
            shipped = [b for b in input_registry.defaults()[name] if b.startswith("pad:")]
            self.assertEqual([b for b in bindings if b.startswith("pad:")], shipped, name)

    def test_back_gives_up_b_to_the_extra_ball_button(self) -> None:
        """Visual Pinball plays Extra Ball on B, and the cabinet's button is Collection
        Menu - one key cannot be both."""
        changed = input_registry.cabinet_bindings(self._stored(), CABINET)

        self.assertNotIn("key:KeyB", changed["back"])

    def test_an_action_somebody_changed_is_left_alone(self) -> None:
        changed = input_registry.cabinet_bindings(
            self._stored(select=["key:F9"]), CABINET)

        self.assertNotIn("select", changed)

    def test_and_so_are_the_keys_it_holds(self) -> None:
        """Start's key is somebody's Menu now; the cabinet's Select does not take it."""
        changed = input_registry.cabinet_bindings(
            self._stored(menu=["key:Digit1"]), CABINET)

        self.assertNotIn("select", changed)
        self.assertNotIn("menu", changed)

    def test_a_key_somebody_tapped_can_still_be_held_for_quit(self) -> None:
        """A key has one press and one hold, so a chosen tap does not take the hold."""
        changed = input_registry.cabinet_bindings(
            self._stored(back=["key:Escape"]), CABINET)

        self.assertNotIn("exit", changed, "Quit keeps Esc held")

    def test_a_cabinet_that_sends_other_keys_gets_those(self) -> None:
        elsewhere = {**CABINET, "start": ("KeyP",), "exit": ("KeyQ",)}

        changed = input_registry.cabinet_bindings(self._stored(), elsewhere)

        self.assertEqual(changed["select"][0], "key:KeyP")
        self.assertEqual(changed["back"][0], "key:KeyQ")
        self.assertEqual(changed["exit"][0], "key:KeyQ@hold:1500")

    def test_a_button_the_app_does_not_name_changes_nothing_for_its_action(self) -> None:
        changed = input_registry.cabinet_bindings(
            self._stored(), {"left_flipper": ("ShiftLeft",)})

        self.assertNotIn("menu", changed)
        self.assertNotIn("select", changed)

    def test_a_second_pass_changes_nothing(self) -> None:
        stored = self._stored()
        stored.update(input_registry.cabinet_bindings(stored, CABINET))

        self.assertEqual(input_registry.cabinet_bindings(stored, CABINET), {})

    def test_the_result_does_not_collide_with_itself(self) -> None:
        stored = self._stored()
        stored.update(input_registry.cabinet_bindings(stored, CABINET))

        self.assertEqual(input_registry.collisions(stored), {})


class LegacyDefaultsTests(unittest.TestCase):
    """A 2.x file carries what 2.x shipped for every key nobody changed, so a value equal
    to it cannot be told from a choice and is read as none."""

    def _action(self, name: str) -> input_registry.InputAction:
        return next(a for a in input_registry.actions() if a.name == name)

    def test_the_shipped_2x_value_takes_the_current_default(self) -> None:
        found = input_registry.from_legacy(
            self._action("exit"), [("keyexit", "Escape,q")])

        self.assertEqual(found, list(self._action("exit").bindings))

    def test_a_value_somebody_chose_is_kept_as_written(self) -> None:
        found = input_registry.from_legacy(
            self._action("exit"), [("keyexit", "Escape,q,F4")])

        self.assertEqual(found[:3], ["key:Escape", "key:q", "key:F4"])
        self.assertNotIn("key:Escape@hold:1500", found)

    def test_the_same_keys_in_another_order_are_still_the_shipped_value(self) -> None:
        found = input_registry.from_legacy(
            self._action("exit"), [("keyexit", "q,Escape")])

        self.assertEqual(found, list(self._action("exit").bindings))

    def test_each_half_is_judged_on_its_own(self) -> None:
        """Keys nobody changed take the new ones; a button somebody mapped is theirs, and
        the standard layout's does not come in beside it."""
        found = input_registry.from_legacy(
            self._action("select"), [("keyselect", "Enter"), ("joyselect", "7")])

        self.assertEqual(found, ["key:Enter", "pad:0/button:7"])

    def test_keys_somebody_chose_do_not_cost_the_controller_its_defaults(self) -> None:
        found = input_registry.from_legacy(
            self._action("select"), [("keyselect", "Space"), ("joyselect", "")])

        self.assertEqual(found, ["key:Space", "pad:standard/button:0"])

    def test_a_key_that_has_no_legacy_value_is_not_invented(self) -> None:
        self.assertEqual(
            input_registry.from_legacy(self._action("tutorial"), [("joytutorial", "")]),
            ["key:KeyT"])

    def test_every_value_2x_shipped_is_known(self) -> None:
        """A key missing here is one whose default would be mistaken for a choice."""
        keys = {a.legacy_key_key for a in input_registry.actions() if a.legacy_key_key}
        keys |= {old for a in input_registry.actions() for old in a.legacy
                 if old.startswith("key")}
        self.assertEqual(set(input_registry.LEGACY_DEFAULTS), keys)


class BridgeTests(unittest.TestCase):
    def test_the_old_bridge_methods_still_answer(self) -> None:
        """Projected out of the lists, so anything built against them keeps working."""
        from configparser import ConfigParser

        parser = ConfigParser()
        parser.read_dict({"input": {"previous": "key:ArrowLeft,pad:0/button:3"}})

        self.assertEqual(input_api.get_keymapping(parser)["keyleft"], "ArrowLeft")
        self.assertEqual(input_api.get_joymapping(parser)["joyleft"], "3")


if __name__ == "__main__":
    unittest.main()
