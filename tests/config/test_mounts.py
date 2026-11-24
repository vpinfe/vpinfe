"""Which share a folder is on, read from mount tables in each OS's shape."""

from __future__ import annotations

import unittest
from unittest import mock

from common import mounts
from common.mounts import NFS, SMB, Mount, Origin, Where

LINUX = [
    Mount("/dev/sda2", "/", "ext4"),
    Mount("nas.lan:/export/pinball", "/mnt/nas", "nfs4"),
    Mount("//nas.lan/Tables", "/mnt/smb", "cifs"),
    Mount("systemd-1", "/mnt/auto", "autofs"),
    Mount("nas.lan:/export/auto", "/mnt/auto", "nfs"),
    Mount("[fd00::5]:/export/v6", "/mnt/v6", "nfs"),
]

MACOS = [
    Mount("/dev/disk3s1s1", "/", "apfs"),
    Mount("//guest@NAS._smb._tcp.local./Pinball%20Tables", "/Volumes/share", "smbfs"),
    Mount("/dev/disk4s1", "/Volumes/media", "exfat"),
]

NAS = Origin(NFS, "nas.lan", "/export/pinball", "/mnt/nas")


class OriginTests(unittest.TestCase):
    def test_a_folder_on_an_nfs_mount_names_its_server_and_export(self) -> None:
        self.assertEqual(mounts.origin_of("/mnt/nas/tables", LINUX), NAS)

    def test_a_folder_on_an_smb_mount_names_its_server_and_share(self) -> None:
        self.assertEqual(mounts.origin_of("/mnt/smb/games", LINUX),
                         Origin(SMB, "nas.lan", "Tables", "/mnt/smb"))

    def test_the_mount_itself_is_on_its_share(self) -> None:
        self.assertEqual(mounts.origin_of("/mnt/nas", LINUX), NAS)

    def test_a_folder_on_a_local_disk_is_on_no_share(self) -> None:
        self.assertIsNone(mounts.origin_of("/home/cab/tables", LINUX))
        self.assertIsNone(mounts.origin_of("/Volumes/media/tables", MACOS))

    def test_a_folder_whose_name_starts_like_a_mount_is_not_on_it(self) -> None:
        self.assertIsNone(mounts.origin_of("/mnt/nasty/tables", LINUX))

    def test_an_automount_answers_for_the_share_mounted_over_it(self) -> None:
        self.assertEqual(mounts.origin_of("/mnt/auto/tables", LINUX),
                         Origin(NFS, "nas.lan", "/export/auto", "/mnt/auto"))

    def test_an_nfs_server_given_by_ipv6_address(self) -> None:
        origin = mounts.origin_of("/mnt/v6", LINUX)
        assert origin is not None
        self.assertEqual(origin.server, "fd00::5")

    def test_a_macos_share_found_through_bonjour_is_named_as_finder_names_it(self) -> None:
        self.assertEqual(mounts.origin_of("/Volumes/share/games", MACOS),
                         Origin(SMB, "NAS", "Pinball Tables", "/Volumes/share"))

    def test_each_protocol_writes_its_share_its_own_way(self) -> None:
        self.assertEqual(NAS.source, "nas.lan:/export/pinball")
        self.assertEqual(Origin(SMB, "nas", "Tables", "/mnt/smb").source, "//nas/Tables")

    def test_an_origin_survives_being_stored(self) -> None:
        self.assertEqual(Origin.from_dict(NAS.as_dict()), NAS)

    def test_a_stored_origin_missing_its_server_is_none(self) -> None:
        self.assertIsNone(Origin.from_dict({"protocol": NFS, "point": "/mnt/nas"}))
        self.assertIsNone(Origin.from_dict("nas.lan:/export"))


class FstabTests(unittest.TestCase):
    def test_entries_are_read_and_comments_skipped(self) -> None:
        text = ("# <file system> <mount point> <type>\n"
                "\n"
                "UUID=1234 / ext4 defaults 0 1\n"
                "nas.lan:/export/pinball /mnt/my\\040nas nfs x-systemd.automount,nofail 0 0\n")

        self.assertEqual(mounts.fstab(text), [
            Mount("UUID=1234", "/", "ext4"),
            Mount("nas.lan:/export/pinball", "/mnt/my nas", "nfs"),
        ])


class WhereTests(unittest.TestCase):
    def _where(self, path: str, live: list[Mount], configured: list[Mount],
               recorded: Origin | None = None) -> Where:
        with mock.patch.object(mounts, "mounted", return_value=live), \
                mock.patch.object(mounts, "_configured", return_value=configured), \
                mock.patch.object(mounts.sys, "platform", "linux"):
            return mounts.where(path, recorded)

    def test_a_folder_on_a_mounted_share_is_connected(self) -> None:
        self.assertEqual(self._where("/mnt/nas/tables", LINUX, []), Where(NAS))

    def test_a_folder_on_this_device_is_on_no_share(self) -> None:
        self.assertEqual(self._where("/home/cab/tables", LINUX, []), Where())

    def test_a_share_fstab_puts_here_that_is_not_mounted_is_not_connected(self) -> None:
        fstab = [Mount("nas.lan:/export/pinball", "/mnt/nas", "nfs4")]

        self.assertEqual(self._where("/mnt/nas/tables", LINUX[:1], fstab),
                         Where(NAS, connected=False))

    def test_a_share_seen_here_before_that_is_not_mounted_is_not_connected(self) -> None:
        self.assertEqual(self._where("/mnt/nas/tables", LINUX[:1], [], recorded=NAS),
                         Where(NAS, connected=False))

    def test_an_automount_not_yet_brought_up_is_not_connected(self) -> None:
        fstab = [Mount("nas.lan:/export/auto", "/mnt/auto", "nfs")]
        waiting = [LINUX[0], Mount("systemd-1", "/mnt/auto", "autofs")]

        self.assertFalse(self._where("/mnt/auto/tables", waiting, fstab).connected)

    def test_a_share_seen_somewhere_else_says_nothing_about_this_folder(self) -> None:
        self.assertEqual(self._where("/home/cab/tables", LINUX[:1], [], recorded=NAS),
                         Where())

    def test_a_local_folder_mounted_inside_a_share_is_on_this_device(self) -> None:
        live = [*LINUX, Mount("/dev/sdb1", "/mnt/nas/usb", "ext4")]
        fstab = [Mount("nas.lan:/export/pinball", "/mnt/nas", "nfs4")]

        self.assertEqual(self._where("/mnt/nas/usb/tables", live, fstab), Where())


# One share, as Windows writes it and as it also accepts it.
SHARE = "//server/share"
BACKSLASHED = SHARE.replace("/", "\\")


class WindowsTests(unittest.TestCase):
    def test_a_unc_path_names_its_server_and_share(self) -> None:
        self.assertEqual(mounts._windows_where(BACKSLASHED + "\\games"),
                         Where(Origin(SMB, "server", "share", BACKSLASHED)))

    def test_a_unc_path_written_with_forward_slashes_is_the_same_share(self) -> None:
        self.assertEqual(mounts._windows_where(SHARE + "/games"),
                         Where(Origin(SMB, "server", "share", SHARE)))

    def test_a_mapped_drive_that_is_not_connected_says_so(self) -> None:
        with mock.patch.object(mounts, "mapped_drive", return_value=(BACKSLASHED, False)):
            where = mounts._windows_where(r"Z:\games")

        self.assertEqual(where, Where(Origin(SMB, "server", "share", "Z:"), connected=False))

    def test_a_local_drive_is_on_no_share(self) -> None:
        with mock.patch.object(mounts, "mapped_drive", return_value=("", True)):
            self.assertEqual(mounts._windows_where(r"C:\games"), Where())


if __name__ == "__main__":
    unittest.main()
