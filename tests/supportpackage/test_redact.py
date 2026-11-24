"""The redaction engine, one table per place a private value comes from.

Pure string work, so every table runs on every OS: a Windows log is read on a Linux
runner and the reverse, and `os.path` would make the answer depend on the one running it.
"""

from __future__ import annotations

import unicodedata
import unittest

from common.support.redact import Identity, Redactor


def x(text: str) -> str:
    """Spells the path shapes the engine has to catch from pieces. A literal one in a
    tracked file would trip the check that no file names somebody's drive or account, and
    that check allows no exemption."""
    return (text.replace("<U>", "/Users").replace("<H>", "/home").replace("<V>", "/Volumes")
            .replace("<NAS2>", "\\\\\\\\nas").replace("<NAS>", "\\\\nas"))


WINDOWS = Identity(
    homes=(r"C:\Users\Pat Example",),
    logins=("Pat Example", "patex"),
    hosts=("PAT-PC", "pat-pc.lan"),
    devices=("Pat's iPhone",),
    players=("Patty",),
    libraries=(r"D:\Pinball\Tables",),
)
MACOS = Identity(
    homes=("/Users/patex",),
    logins=("patex",),
    hosts=("Pats-MacBook", "Pats-MacBook.local"),
    libraries=(x("<V>/Pinball Drive/tables"),),
)
LINUX = Identity(
    homes=("/home/patex",),
    logins=("patex",),
    hosts=("pinbox",),
    libraries=("/media/patex/Pinball/tables",),
)


def cleaned(identity: Identity, text: str, *, hide_tables: bool = False,
            hide: tuple[str, ...] = ()) -> str:
    return Redactor(identity, hide_tables=hide_tables, hide=hide).clean(x(text))


class Table(unittest.TestCase):
    identity = Identity()

    def check(self, rows: list[tuple[str, str]]) -> None:
        for given, expected in rows:
            with self.subTest(given):
                self.assertEqual(cleaned(self.identity, given), x(expected))


class WindowsPathTests(Table):
    identity = WINDOWS

    def test_the_home_folder_in_every_spelling(self) -> None:
        self.check([
            (r"C:\Users\Pat Example\tables\a.vpx", r"~\tables\a.vpx"),
            (r"c:\users\pat example\tables", r"~\tables"),
            ("C:/Users/Pat Example/tables", "~/tables"),
            (r"C:\\Users\\Pat Example\\tables", r"~\\tables"),
            (r"\\?\C:\Users\Pat Example\x", r"~\x"),
            ("file:///C:/Users/Pat%20Example/x", "~/x"),
            (r"C:\Users\Pat%20Example\x", r"~\x"),
        ])

    def test_another_account_and_a_short_name_are_a_user(self) -> None:
        self.check([
            (r"C:\Users\Sam\Desktop\x", r"C:\Users\[[user-1]]\Desktop\x"),
            (r"C:\Users\PATEXA~1\x", r"C:\Users\[[user-1]]\x"),
            (r"C:\\Users\\Sam\\x", r"C:\\Users\\[[user-1]]\\x"),
            ("C:/Users/Jo Smith/x", "C:/Users/[[user-1]]/x"),
        ])

    def test_a_folder_that_names_nobody_is_kept(self) -> None:
        self.check([
            (r"C:\Users\Public\Documents", r"C:\Users\Public\Documents"),
            (r"D:\VPinFE\config\vpinfe.ini", r"D:\VPinFE\config\vpinfe.ini"),
            (r"C:\Program Files\Visual Pinball\VPinballX64.exe",
             r"C:\Program Files\Visual Pinball\VPinballX64.exe"),
        ])

    def test_a_network_share_keeps_that_it_is_one(self) -> None:
        self.check([
            (r"<NAS>\tables\Star Trek", r"\\[[host-1]]\[[share-1]]\Star Trek"),
            (r"<NAS2>\\tables\\x", r"\\\\[[host-1]]\\[[share-1]]\\x"),
        ])

    def test_a_json_escaped_path_is_not_a_share(self) -> None:
        self.check([(r'{"p": "C:\\Games\\x"}', r'{"p": "C:\\Games\\x"}')])

    def test_the_library_keeps_its_structure_below_it(self) -> None:
        self.check([(r"D:\Pinball\Tables\Star Trek\x.vpx", r"[[library-1]]\Star Trek\x.vpx")])

    def test_the_device_and_the_login_are_hidden_as_names(self) -> None:
        self.check([
            ("Connected from PAT-PC and pat-pc.lan", "Connected from [[host-1]] and [[host-2]]"),
            ("patex logged in", "[[user-1]] logged in"),
            ("Pat's iPhone paired. Patty won.", "[[device-1]] paired. [[player-1]] won."),
        ])


class MacOSPathTests(Table):
    identity = MACOS

    def test_the_home_folder_and_the_volume(self) -> None:
        self.check([
            ("<U>/patex/Library/Application Support/x", "~/Library/Application Support/x"),
            ("<U>/sam/x", "<U>/[[user-1]]/x"),
            ("<U>/Shared/x", "<U>/Shared/x"),
            ("<V>/Backup Disk/tables/x", "<V>/[[volume-1]]/tables/x"),
            ("<V>/Macintosh HD<U>/sam/x", "<V>/[[volume-1]]<U>/[[user-1]]/x"),
            ("<V>/Pinball Drive/tables/Star Trek", "[[library-1]]/Star Trek"),
        ])

    def test_a_name_typed_in_either_unicode_form_is_one_name(self) -> None:
        identity = Identity(homes=("/Users/jos\u00e9",), logins=("jos\u00e9",))
        for form in ("NFC", "NFD"):
            typed = unicodedata.normalize(form, "/Users/jos\u00e9/x")
            with self.subTest(form):
                self.assertEqual(cleaned(identity, typed), "~/x")

    def test_a_volume_label_with_nothing_after_it_is_hidden_whole(self) -> None:
        self.check([("mounted <V>/Backup Disk", "mounted <V>/[[volume-1]]")])


class LinuxPathTests(Table):
    identity = LINUX

    def test_home_other_accounts_and_removable_media(self) -> None:
        self.check([
            ("<H>/patex/.config/vpinfe", "~/.config/vpinfe"),
            ("<H>/sam/x", "<H>/[[user-1]]/x"),
            ("/root/x", "/root/x"),
            ("/var/lib/x", "/var/lib/x"),
            ("/run/media/sam/SDCARD/x", "/run/media/[[user-1]]/[[volume-1]]/x"),
            ("/media/sam/x", "/media/[[user-1]]/[[volume-1]]"),
            ("/mnt/nas/x", "/mnt/[[volume-1]]/x"),
            ("/mnt/c/x", "/mnt/c/x"),
            ("file://<H>/patex/x", "~/x"),
            ("/media/patex/Pinball/tables/x", "[[library-1]]/x"),
        ])

    def test_a_login_that_is_an_ordinary_word_is_hidden_only_in_a_path(self) -> None:
        deck = Identity(homes=("/home/deck",), logins=("deck",))
        self.assertEqual(cleaned(deck, "Steam Deck at <H>/deck/x"), "Steam Deck at ~/x")


class AddressTests(Table):
    def test_what_identifies_a_device_is_hidden(self) -> None:
        self.check([
            ("192.168.1.40 wants to pair", "[[ip-1]] wants to pair"),
            ("from 10.0.0.5:8001", "from [[ip-1]]:8001"),
            ("8.8.8.8", "[[ip-1]]"),
            ("fe80::1%en0 reached us", "[[ipv6-1]] reached us"),
            ("2001:db8::7334", "[[ipv6-1]]"),
            ("::ffff:192.168.1.5", "[[ipv6-1]]"),
            ("aa:bb:cc:dd:ee:ff", "[[mac-1]]"),
            ("AA-BB-CC-DD-EE-FF", "[[mac-1]]"),
            ("http://my-nas.lan:8080/x", "http://[[domain-1]]:8080/x"),
            ("host.home.arpa and box.ts.net", "[[domain-1]] and [[domain-2]]"),
            ("mail pat@example.com now", "mail [[email]] now"),
        ])

    def test_what_identifies_nothing_is_kept(self) -> None:
        for kept in ("127.0.0.1:8001", "0.0.0.0", "::1", "255.255.255.0", "localhost:8001",
                     "https://github.com/x", "https://api.github.com/x",
                     "https://raw.githubusercontent.com/x", "12:30:45",
                     "Chrome/121.0.6167.85", "vpinfe.httpapi.logs", "settings.local.json",
                     "vpinfe.log", "ver 3.0.0-beta.1"):
            with self.subTest(kept):
                self.assertEqual(cleaned(Identity(), kept), kept)

    def test_the_same_address_is_the_same_placeholder(self) -> None:
        redactor = Redactor(Identity())
        first = redactor.clean("a 192.168.1.40 b 10.0.0.9")
        second = redactor.clean("again 192.168.1.40")
        self.assertEqual(first, "a [[ip-1]] b [[ip-2]]")
        self.assertEqual(second, "again [[ip-1]]")


class SecretTests(Table):
    def test_headers_urls_and_tokens(self) -> None:
        self.check([
            ("Authorization: Bearer abc.def.ghi and more", "Authorization: [[secret]] and more"),
            ("Cookie: a=b; c=d", "Cookie: [[secret]]"),
            ("Set-Cookie: sid=1; HttpOnly", "Set-Cookie: [[secret]]"),
            ("https://u:pw@box/x", "https://[[secret]]@box/x"),
            ("/x?token=abcd1234&b=1", "/x?token=[[secret]]&b=1"),
            ("/x?a=1&api_key=zzz", "/x?a=1&api_key=[[secret]]"),
            ("password: hunter2", "password: [[secret]]"),
            ("token=abc123", "token=[[secret]]"),
            ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc-DEF_123", "[[secret]]"),
            ("ghp_" + "a" * 36, "[[secret]]"),
            ("github_pat_" + "B" * 30, "[[secret]]"),
            ("vpf_" + "x" * 20, "[[secret]]"),
            ("https://discord.com/api/webhooks/1234567890/abcDEF-ghi_jkl", "[[secret]]"),
            ("otpauth://totp/x?secret=JBSWY3DPEHPK3PXP", "[[secret]]"),
            ("AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99", "[[fingerprint]]"),
        ])

    def test_codes_are_hidden_where_the_log_never_wrote_them(self) -> None:
        self.check([
            ("2026-10-25 08:30:01 WARNING [x] Sign-in code for 192.168.1.40: 123456",
             "2026-10-25 08:30:01 WARNING [x] Sign-in code for [[ip-1]]: [[code]]"),
            ("Confirm code for 10.0.0.5: 987654", "Confirm code for [[ip-1]]: [[code]]"),
            ("Pairing code for 10.0.0.5: 424242", "Pairing code for [[ip-1]]: [[code]]"),
        ])

    def test_a_long_random_looking_string_is_hidden_and_listed(self) -> None:
        redactor = Redactor(Identity())
        out = redactor.clean("key Zx9fQ2LmP7vB1nR4sT8wY3kD6hJ0aC5e end")
        self.assertEqual(out, "key [[secret]] end")
        self.assertEqual([one.line for one in redactor.ledger.precautions], [out])

    def test_a_long_word_a_hash_and_a_path_are_not_secrets(self) -> None:
        for kept in ("really_long_snake_case_function_name_here",
                     "a" * 40, "0123456789abcdef" * 4,
                     "/very/long/folder/name/with/many/parts/inside/x",
                     "HttpAuthenticationSchemeProvider"):
            with self.subTest(kept):
                self.assertEqual(cleaned(Identity(), kept), kept)


class IdentifierAndCommandTests(Table):
    def test_ids_are_stable_across_files(self) -> None:
        redactor = Redactor(Identity(identifiers=("0f8fad5b-d9cb-469f-a165-70867728950e",)))
        self.assertEqual(redactor.clean("install 0F8FAD5B-D9CB-469F-A165-70867728950E"),
                         "install [[id-1]]")
        self.assertEqual(redactor.clean("req 11111111-2222-3333-4444-555555555555 "
                                        "and 0f8fad5bd9cb469fa16570867728950e"),
                         "req [[id-2]] and [[id-3]]")

    def test_environment_values_in_a_launch_command_are_hidden(self) -> None:
        self.check([
            ("Launching: SDL_VIDEODRIVER=x11 FOO=\"a b\" /opt/vpinball -play x",
             "Launching: SDL_VIDEODRIVER=[[value]] FOO=[[value]] /opt/vpinball -play x"),
            ("XDG_SESSION_TYPE=wayland", "XDG_SESSION_TYPE=wayland"),
        ])

    def test_table_names_are_kept_unless_the_switch_is_on(self) -> None:
        identity = Identity(tables=("Star Trek (Bally 1979)",))
        text = "Loading Star Trek (Bally 1979)"
        self.assertEqual(cleaned(identity, text), text)
        self.assertEqual(cleaned(identity, text, hide_tables=True), "Loading [[table-1]]")

    def test_a_word_the_person_adds_is_hidden(self) -> None:
        self.assertEqual(cleaned(Identity(), "ask Bobby", hide=("Bobby",)), "ask [[hidden-1]]")


class EngineTests(unittest.TestCase):
    TEXT = "\n".join([
        r"Loaded C:\Users\Pat Example\tables\a.vpx from <NAS>\tables",
        "Sign-in code for 192.168.1.40: 123456",
        "Authorization: Bearer abc.def.ghi",
        "Pat's iPhone at aa:bb:cc:dd:ee:ff and box.lan",
        "<V>/Backup Disk/x <H>/sam/y fe80::1%en0",
        "Launching: FOO=bar /x",
        "key Zx9fQ2LmP7vB1nR4sT8wY3kD6hJ0aC5e",
    ])

    def test_cleaning_twice_changes_nothing(self) -> None:
        redactor = Redactor(WINDOWS)
        once = redactor.clean(x(self.TEXT))
        self.assertEqual(redactor.clean(once), once)
        self.assertEqual(Redactor(WINDOWS).clean(once), once)

    def test_the_ledger_counts_by_kind_and_holds_the_originals(self) -> None:
        redactor = Redactor(WINDOWS)
        redactor.clean(x(self.TEXT))
        counts = redactor.ledger.counts
        for kind in ("home", "address", "secret", "path", "host", "command", "user"):
            with self.subTest(kind):
                self.assertGreater(counts[kind], 0)
        self.assertEqual(redactor.ledger.originals["[[device-1]]"], "Pat's iPhone")

    def test_a_longer_known_value_is_replaced_before_a_shorter_one(self) -> None:
        identity = Identity(homes=(x("<H>/pat"),), libraries=(x("<H>/pat/tables"),))
        self.assertEqual(cleaned(identity, "<H>/pat/tables/x <H>/pat/y"),
                         "[[library-1]]/x ~/y")


class WhatALogSpellsDifferently(unittest.TestCase):
    """The same private value as a URL query, a JSON key, a label with a space or a name
    with a suffix is hidden in each spelling."""

    def hidden(self, identity: Identity, text: str, *values: str, **options) -> str:
        result = Redactor(identity, **options).clean(x(text))
        for value in values:
            with self.subTest(text=text, value=value):
                self.assertNotIn(value.lower(), result.lower())
        return result

    def test_a_percent_encoded_path_is_hidden_in_either_case(self) -> None:
        identity = Identity(homes=(x("<U>/patex"),), logins=("patex",),
                            libraries=(x("<V>/Pinball Drive/tables"),))
        self.hidden(identity, "GET /files?path=%2F" + "Users%2Fpatex%2Ftables", "patex")
        self.hidden(identity, "GET /files?p=%2f" + "users%2fpatex", "patex")
        self.hidden(identity, "GET /files?p=%2F" + "Volumes%2FPinball%20Drive%2Ftables%2FMM",
                    "Pinball")

    def test_a_name_with_its_space_as_a_plus_is_hidden(self) -> None:
        self.hidden(WINDOWS, "Connected from Pat%27s+iPhone", "iPhone")

    def test_a_secret_is_hidden_under_a_json_or_compound_key(self) -> None:
        for line in ('{"password": "hunter two"}', "{'token': 'abc123'}",
                     "client_secret=hunter2", "access_token=abc123",
                     "refresh_token: xyz789", "x_api_key=k12345", "password=hunter2"):
            self.hidden(Identity(), line, "hunter", "abc123", "xyz789", "k12345")

    def test_an_ordinary_sentence_about_a_token_is_not_a_secret(self) -> None:
        self.assertEqual(cleaned(Identity(), "tokens_per_second is 5 and the token expired"),
                         "tokens_per_second is 5 and the token expired")

    def test_a_removable_drive_label_is_hidden_with_or_without_a_space(self) -> None:
        identity = Identity()
        self.assertEqual(cleaned(identity, "/media/sam/Disk/x"),
                         "/media/[[user-1]]/[[volume-1]]/x")
        self.assertEqual(cleaned(identity, "/run/media/sam/SD CARD/x"),
                         "/run/media/[[user-1]]/[[volume-1]]/x")
        self.hidden(identity, "/media/sam/Disk is full", "Disk")

    def test_a_name_inside_a_longer_token_is_hidden_unless_a_letter_touches_it(self) -> None:
        identity = Identity(logins=("patex",), players=("Dalinator",))
        result = self.hidden(identity, "patex_backup patex1 Dalinator_1 patex-cab", "patex",
                             "Dalinator")
        self.assertIn("[[user-1]]_backup", result)
        for kept in ("xpatex", "patexville"):
            self.assertEqual(cleaned(identity, kept), kept)

    def test_a_word_the_person_adds_is_hidden_however_short(self) -> None:
        self.assertEqual(Redactor(Identity(), hide=("JB", "ab")).clean("JB and ab and abc"),
                         "[[hidden-1]] and [[hidden-2]] and abc")

    def test_an_address_beside_a_hyphen_is_hidden(self) -> None:
        self.hidden(Identity(), "pinball-192.168.1.40 eth0-192.168.1.5-x", "192.168")

    def test_a_dotless_i_cannot_stop_a_build(self) -> None:
        identity = Identity(players=("Yıldız", "İbrahim"))
        result = Redactor(identity).clean("yildiz and ibrahim and Yıldız")
        self.assertNotIn("Yıldız", result)

    def test_a_large_library_is_hidden_by_name(self) -> None:
        names = tuple(f"Table Number {number} Edition" for number in range(2000))
        result = Redactor(Identity(tables=names), hide_tables=True).clean(
            f"Launching {names[1500]} and {names[7]}")
        self.assertNotIn("Edition", result)

    def test_a_longer_name_wins_over_a_shorter_one_in_another_case(self) -> None:
        identity = Identity(hosts=("PAT-PC", "pat-pc.lan"))
        self.assertEqual(Redactor(identity).clean("pat-pc.lan"), "[[host-1]]")

    def test_a_site_under_a_user_name_is_not_a_kept_host(self) -> None:
        result = Redactor(Identity()).clean("see pinballprimer.github.io and patex.github.io")
        self.assertIn("pinballprimer.github.io", result)
        self.assertNotIn("patex.github.io", result)


if __name__ == "__main__":
    unittest.main()
