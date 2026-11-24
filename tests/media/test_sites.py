"""What a site that hosts a file is called where a link names it."""

from __future__ import annotations

import unittest

from common.online import sites


class SiteName(unittest.TestCase):
    def test_a_known_host_reads_as_the_site_is_written(self) -> None:
        self.assertEqual("VPUniverse", sites.site_name("https://vpuniverse.com/files/file/1/"))
        self.assertEqual("Google Drive", sites.site_name("https://drive.google.com/file/d/x"))

    def test_www_does_not_make_it_another_site(self) -> None:
        self.assertEqual("VPForums", sites.site_name("https://www.vpforums.org/index.php"))

    def test_the_case_of_the_host_does_not_matter(self) -> None:
        self.assertEqual("Mega", sites.site_name("https://MEGA.nz/file/abc"))

    def test_a_host_it_has_no_name_for_is_named_by_itself(self) -> None:
        self.assertEqual("example.org", sites.site_name("https://www.example.org/a"))

    def test_an_address_without_a_host_names_no_site(self) -> None:
        self.assertEqual("", sites.site_name(""))
        self.assertEqual("", sites.site_name("not a link"))


class IsWeb(unittest.TestCase):
    def test_an_http_or_https_address_with_a_host_is(self) -> None:
        self.assertTrue(sites.is_web("https://vpuniverse.com/files/1/"))
        self.assertTrue(sites.is_web(" http://example.org "))

    def test_a_path_a_bare_id_and_a_host_without_a_scheme_are_not(self) -> None:
        for said in ("/files/file/26493-mata-hari/", "oO-Hofw7", "example.org/a", "", "ftp://example.org/a"):
            with self.subTest(said=said):
                self.assertFalse(sites.is_web(said))

    def test_an_address_that_does_not_parse_is_not_rather_than_an_error(self) -> None:
        for said in ("http://[x", "http://[::1", "http://exa]mple.com", "https://a\uff03b.com"):
            with self.subTest(said=said):
                self.assertFalse(sites.is_web(said))


if __name__ == "__main__":
    unittest.main()
