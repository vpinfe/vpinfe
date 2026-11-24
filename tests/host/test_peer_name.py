"""Naming a device that reached this one: its network name, else its address."""

from __future__ import annotations

import socket
import unittest
from unittest import mock

from common.host import peer_name


class ThePlaceOfAnAddress(unittest.TestCase):
    def setUp(self) -> None:
        peer_name._found.clear()
        self.addCleanup(peer_name._found.clear)

    def test_a_name_that_resolves_is_used(self) -> None:
        with mock.patch.object(socket, "gethostbyaddr", return_value=("ipad.lan", [], [])):
            self.assertEqual(peer_name.place_of("192.168.1.20"), "ipad.lan")

    def test_an_address_with_no_name_stays_the_address(self) -> None:
        with mock.patch.object(socket, "gethostbyaddr", side_effect=socket.herror):
            self.assertEqual(peer_name.place_of("192.168.1.20"), "192.168.1.20")

    def test_a_name_found_is_kept_and_a_miss_is_tried_again(self) -> None:
        with mock.patch.object(socket, "gethostbyaddr", side_effect=socket.herror) as miss:
            peer_name.place_of("192.168.1.20")
            peer_name.place_of("192.168.1.20")
        with mock.patch.object(socket, "gethostbyaddr",
                               return_value=("ipad.lan", [], [])) as hit:
            peer_name.place_of("192.168.1.20")
            peer_name.place_of("192.168.1.20")

        self.assertEqual((miss.call_count, hit.call_count), (2, 1))

    def test_something_that_is_not_an_address_is_left_alone(self) -> None:
        with mock.patch.object(socket, "gethostbyaddr") as lookup:
            self.assertEqual(peer_name.place_of("not-an-address"), "not-an-address")
        lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
