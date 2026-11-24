"""Whether a link the catalog lists is a file you can go and get.

The failure this guards is one-directional and expensive: reporting a folder, a
storefront or a video as a missing download tells somebody an asset is a click away
when it is somewhere to rummage. On a 17,425-URL snapshot about 13% of links are not a
file, and one shortened link stands behind 750 of the 791 point-of-view records.
"""

from __future__ import annotations

import unittest

from common.online import obtainability as ob

MEGA_FOLDER = "https://mega.nz/#F!p3hREDgB!pEWMJvVQ7t3Sv_TmrxdU-w!52AzHIDR"
MEGA_FILE = "https://mega.nz/file/abc123#key"
VPF = "https://www.vpforums.org/index.php?app=downloads&showfile={}"


class ClassifyTests(unittest.TestCase):
    def test_a_folder_is_somewhere_to_browse(self) -> None:
        """By its shape, not its host: 805 of 825 Mega links are folders and the other
        20 are files, so a host-wide rule would be wrong about every one of those."""
        self.assertEqual(ob.classify(MEGA_FOLDER), ob.COLLECTION)
        self.assertEqual(ob.classify(MEGA_FILE), ob.AVAILABLE)

    def test_a_drive_folder_is_too(self) -> None:
        self.assertEqual(
            ob.classify("https://drive.google.com/drive/folders/14ymA2"), ob.COLLECTION)
        self.assertEqual(
            ob.classify("https://drive.google.com/file/d/1TaRSU/view"), ob.AVAILABLE)

    def test_a_bare_host_downloads_nothing(self) -> None:
        """116 records point at a storefront's front page."""
        for said in ("https://www.pinballfx.com/", "https://zenstudios.com",
                     "https://fss-pinball.com/"):
            with self.subTest(url=said):
                self.assertEqual(ob.classify(said), ob.REFERENCE)

    def test_a_video_is_a_reference(self) -> None:
        self.assertEqual(
            ob.classify("https://www.youtube.com/watch?v=QwHt2YKJJk4"), ob.REFERENCE)

    def test_nonsense_is_unknown_rather_than_available(self) -> None:
        for said in ("", "not a url", "ftp://host/file.zip"):
            with self.subTest(url=said):
                self.assertEqual(ob.classify(said), ob.UNKNOWN)

    def test_a_link_behind_many_records_is_a_collection(self) -> None:
        """The signal of last resort. A shortener says nothing about its destination,
        and one stands behind 750 records that a shape rule cannot see."""
        short = "https://bit.ly/POVs_RaJo"
        shared = ob.crowded([short] * ob.SHARED_BY)

        self.assertEqual(ob.classify(short, shared), ob.COLLECTION)

    def test_a_page_serving_a_handful_of_tables_is_not(self) -> None:
        """At seven records `vpuniverse.com/files/file/5489-acdc` is one release page
        legitimately shared by several tables. Calling that somewhere to browse is the
        same overstatement pointing the other way."""
        page = "https://vpuniverse.com/files/file/5489-acdc"
        shared = ob.crowded([page] * 7)

        self.assertEqual(ob.classify(page, shared), ob.AVAILABLE)

    def test_the_query_is_part_of_the_destination(self) -> None:
        """Dropping it collapsed four thousand distinct release pages onto one key, and
        a third of the catalog reported as somewhere to browse."""
        pages = [VPF.format(n) for n in range(ob.SHARED_BY * 2)]
        shared = ob.crowded(pages)

        self.assertEqual(shared, frozenset())
        self.assertEqual(ob.classify(pages[0], shared), ob.AVAILABLE)

    def test_the_same_page_written_two_ways_counts_once(self) -> None:
        """`fss-pinball.com` appears 56 times bare and 52 with a trailing slash. Counted
        apart neither reaches the threshold that both together clear twice over."""
        both = ["https://HOST.com/downloads", "https://www.host.com/downloads/"]
        shared = ob.crowded(both * (ob.SHARED_BY // 2))

        self.assertEqual(len(shared), 1)


class BestOfTests(unittest.TestCase):
    def test_a_record_offering_a_file_and_a_thread_offers_the_file(self) -> None:
        self.assertEqual(
            ob.best_of([MEGA_FOLDER, "https://vpuniverse.com/files/file/1-x"]),
            ob.AVAILABLE)

    def test_a_record_with_no_links_is_unknown(self) -> None:
        self.assertEqual(ob.best_of([]), ob.UNKNOWN)

    def test_a_folder_beats_a_storefront(self) -> None:
        """Somewhere to browse is at least somewhere the file is."""
        self.assertEqual(ob.best_of([MEGA_FOLDER, "https://zenstudios.com/"]),
                         ob.COLLECTION)


if __name__ == "__main__":
    unittest.main()
