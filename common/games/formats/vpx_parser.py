#!/usr/bin/env python3
"""Reading a `.vpx` file directly.

A VPX table is an OLE compound file, so this opens the container and pulls out the
script and the fields VPinFE records. Nothing else in the tree parses the format.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pathlib
import re
import sys
from typing import IO

import olefile

logger = logging.getLogger("vpinfe.common.games.formats.vpx_parser")


class VPXParser:
    """Opens a `.vpx` as the OLE compound file it is, and reads what is inside."""

    sys.setrecursionlimit(10000)  # increase recursion limit for large OLE files

    logger = None

    VPX_PATHS = {
        'table_name': 'tableinfo/tablename',
        'version': 'tableinfo/tableversion',
        'author_name': 'tableinfo/authorname',
        'release_date': 'tableinfo/releasedate',
        'game_blurb': 'tableinfo/tableblurb',
        'table_rules': 'tableinfo/tablerules',
        'save_date': 'tableinfo/tablesavedate',
        'save_rev': 'tableinfo/tablesaverev',
        'manufacturer': 'tableinfo/companyname',
        'year': 'tableinfo/companyyear',
        'type': 'tableinfo/playfieldvariant',
        'table_description': 'tableinfo/tabledescription',
    }

    VPX_PATHS_BINARY = {
        'game_data': 'gamestg/gamedata',
        # 'gameStgVersion': 'gamestg/version'
    }

    DERIVED_PATHS = {
        'rom': '',
        'filename': '',
        'vbs_hash': '',
        'file_hash': '',
        'detect_fleep': '',
        'detect_nfozzy': '',
        'detect_scorbit': '',
        'detect_ssf': '',
        'detect_fastflips': '',
        'detect_lut': '',
        'detect_flex': '',
        'detect_pinmame': '',
    }

    # -------------------------------
    # Helpers
    # -------------------------------
    def decode_bytes_to_string(self, fileio: IO[bytes]) -> str:
        text = fileio.read().decode("latin-1")
        return text.replace('\x00', '')

    def decode_bytes_to_int(self, fileio: IO[bytes]) -> None:
        # not implemented yet
        pass

    def ensure_msdos_line_endings(self, text: str) -> str:
        if "\r\n" in text and "\n" not in text.replace("\r\n", ""):
            return text  # Already correct
        return text.replace("\r\n", "\n").replace("\n", "\r\n")

    def sha256sum(self, filename: str) -> str:
        with open(filename, 'rb', buffering=0) as f:
            return hashlib.file_digest(f, 'sha256').hexdigest()

    def find_code_offset_after(self, data: bytes, word: bytes = b"CODE") -> int:
        index = data.find(word)
        return index + len(word) if index != -1 else -1

    # -------------------------------
    # Loading / extracting
    # -------------------------------
    def load_game_values(self, vpx_file_values: dict[str, str],
                         ole: olefile.OleFileIO) -> None:
        for key, path in self.VPX_PATHS.items():
            if ole.exists(path):
                with ole.openstream(path) as file:
                    vpx_file_values[key] = self.decode_bytes_to_string(file)
            else:
                vpx_file_values[key] = ""

    def load_vb_code(self, ole: olefile.OleFileIO,
                     vpx_file_values: dict[str, str]) -> None:
        with ole.openstream(self.VPX_PATHS_BINARY['game_data']) as file:
            data = file.read()

        offset = self.find_code_offset_after(data)
        if offset == -1:
            vpx_file_values['game_data'] = ""
            return

        length = int.from_bytes(data[offset:offset + 4], "little", signed=True)
        vbscript = data[offset + 4:offset + 4 + length].decode("utf-8", errors="ignore")
        vpx_file_values['game_data'] = self.ensure_msdos_line_endings(vbscript)

    def load_sidecar_vb_code(self, vpx_file: str,
                             vpx_file_values: dict[str, str]) -> None:
        vbs_path = pathlib.Path(vpx_file).with_suffix(".vbs")
        if not vbs_path.exists():
            return

        vbscript = vbs_path.read_bytes().decode("utf-8-sig", errors="ignore")
        vpx_file_values['game_data'] = self.ensure_msdos_line_endings(vbscript)

    def calc_code_hash(self, vpx_file_values: dict[str, str]) -> None:
        vpx_file_values['vbs_hash'] = hashlib.sha256(
            vpx_file_values['game_data'].encode("utf-8")
        ).hexdigest()

    def extract_file(self, file: str) -> dict[str, str]:
        vpx_file_values = {
            'filename': os.path.basename(file),
            'file_hash': self.sha256sum(file),
        }

        with olefile.OleFileIO(file) as ole:
            self.load_game_values(vpx_file_values, ole)
            self.load_vb_code(ole, vpx_file_values)

        self.load_sidecar_vb_code(file, vpx_file_values)
        self.calc_code_hash(vpx_file_values)
        self.extract_rom_name(vpx_file_values)
        self.run_detectors(vpx_file_values)

        return vpx_file_values

    # -------------------------------
    # Extraction helpers
    # -------------------------------
    @staticmethod
    def strip_vbscript_comments(script: str) -> str:
        lines = []
        for line in script.splitlines():
            code = []
            in_string = False
            i = 0
            while i < len(line):
                char = line[i]
                if char == '"':
                    code.append(char)
                    if in_string and i + 1 < len(line) and line[i + 1] == '"':
                        code.append(line[i + 1])
                        i += 2
                        continue
                    in_string = not in_string
                elif char == "'" and not in_string:
                    break
                else:
                    code.append(char)
                i += 1
            lines.append("".join(code))
        return "\n".join(lines)

    def extract_rom_name(self, vpx_file_values: dict[str, str]) -> None:
        game_data = self.strip_vbscript_comments(vpx_file_values['game_data'])
        m = re.search(r'(?i)c?gamename\s*=\s*"([^"]+)"', game_data)
        m_opt = re.search(r'(?i)c?OptRom\s*=\s*"([^\s]+)"', game_data)

        if m:
            vpx_file_values['rom'] = m.group(1)
        elif m_opt:
            vpx_file_values['rom'] = m_opt.group(1)
        else:
            vpx_file_values['rom'] = ""

    def run_detectors(self, vpx_file_values: dict[str, str]) -> None:
        game_data_lower = vpx_file_values['game_data'].lower()
        detectors = {
            'detect_nfozzy': 'class flipperpolarity',
            'detect_fleep': 'rubberstrongsoundfactor',
            'detect_ssf': 'playsoundat',
            'detect_lut': 'lut',
            'detect_scorbit': 'scorebit',
            'detect_fastflips': 'fastflips',
            'detect_flex': 'flexdmd',
        }
        for key, token in detectors.items():
            vpx_file_values[key] = "true" if token in game_data_lower else "false"

        # Whether the script drives the PinMAME emulator, as opposed to declaring a
        # rom name only as a DOF key. On the comment-stripped script, unlike the
        # detectors above: EM tables commonly carry commented-out VPM code, and a
        # dead LoadVPM must not read as a live dependency.
        stripped_lower = self.strip_vbscript_comments(vpx_file_values['game_data']).lower()
        drives_pinmame = ("loadvpm" in stripped_lower
                          or "vpminit" in stripped_lower
                          or re.search(r'createobject\s*\(\s*"vpinmame\.controller"',
                                       stripped_lower) is not None)
        vpx_file_values['detect_pinmame'] = "true" if drives_pinmame else "false"

    # -------------------------------
    # Bulk ops
    # -------------------------------
    def single_file_extract(self, vpx_file: str) -> dict[str, str] | None:
        if not os.path.exists(vpx_file):
            logger.warning("File not found: %s", vpx_file)
            return None
        if not olefile.isOleFile(vpx_file):
            logger.warning("Not an OLE file: %s", vpx_file)
            return None
        return self.extract_file(vpx_file)
