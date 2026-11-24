from __future__ import annotations

import unittest

from common.devices import install_identity
from common.devices.device_registry import KIND_VPX_MOBILE
from manager.frontend import launchers


def _device(device_id: str, *, kind: str = "vpinfe",
            features: tuple[str, ...] = (install_identity.FRONTEND,)) -> dict:
    return {"device_id": device_id, "kind": kind, "features": list(features)}


def _offered(known: list[dict]) -> list[str]:
    return [one["device_id"] for one in launchers.copy_targets(known, "Here000000")]


class CopyTargetTests(unittest.TestCase):
    def test_another_install_running_the_frontend_is_offered(self) -> None:
        self.assertEqual(_offered([_device("Cab0000000")]), ["Cab0000000"])

    def test_an_install_with_the_frontend_off_is_not(self) -> None:
        known = [_device("Lib0000000", features=(install_identity.LIBRARY,)),
                 _device("None000000", features=())]

        self.assertEqual(_offered(known), [])

    def test_this_install_is_not(self) -> None:
        self.assertEqual(_offered([_device("Here000000")]), [])

    def test_a_phone_is_not(self) -> None:
        self.assertEqual(_offered([_device("Phone00000", kind=KIND_VPX_MOBILE)]), [])


if __name__ == "__main__":
    unittest.main()
