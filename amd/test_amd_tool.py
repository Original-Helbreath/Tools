"""Tests for amd_tool.py.

Real-file tests round-trip every .amd in 2.20-Map-Files/maps byte for byte. Point HB_MAP_FILES at that checkout;
by default the sibling folder ../../2.20-Map-Files is used. They are skipped when none is found.
Output goes to ../.test-out (gitignored), never the system temp folder.

    python -m unittest test_amd_tool -v
"""
import glob
import json
import os
import shutil
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import amd_tool as t  # noqa: E402

MAP_FILES = os.environ.get("HB_MAP_FILES", os.path.normpath(os.path.join(HERE, "..", "..", "2.20-Map-Files")))
OUT = os.path.normpath(os.path.join(HERE, "..", ".test-out", "amd"))


def real_maps():
    return sorted(glob.glob(os.path.join(MAP_FILES, "maps", "*", "*.amd")))


def tiny(sx=3, sy=2, header=None):
    text = header if header is not None else "MAPSIZEX =  %d MAPSIZEY =  %d TILESIZE = 10   " % (sx, sy)
    body = b"".join(struct.pack("<4hBB", x, y, -1, 0, 0x80 if x == 0 else 0, 0) for y in range(sy) for x in range(sx))
    return text.encode() + b"\x00" * (256 - len(text)) + body


class Crafted(unittest.TestCase):
    def test_read_layout(self):
        m = t.read_amd(tiny())
        self.assertEqual((m["size_x"], m["size_y"]), (3, 2))
        self.assertEqual(m["grids"]["tile_sprite"], [[0, 1, 2], [0, 1, 2]])  # grid[y][x]
        self.assertEqual(m["grids"]["tile_frame"], [[0, 0, 0], [1, 1, 1]])
        self.assertEqual(m["grids"]["object_sprite"][1][2], -1)
        self.assertEqual(m["grids"]["flags"][0], [0x80, 0, 0])
        self.assertEqual(t.build_amd(m), tiny())

    def test_rejects(self):
        for bad in [b"", tiny()[:-1], tiny() + b"\x00", tiny(header="MAPSIZEX = 0 MAPSIZEY = 2"),
                    tiny(header="MAPSIZEX = 3 MAPSIZEY = 2 TILESIZE = 12")]:
            with self.assertRaises(t.AmdError):
                t.read_amd(bad)

    def test_build_checks_grid(self):
        m = t.read_amd(tiny())
        m["grids"]["flags"][1].pop()
        with self.assertRaises(t.AmdError):
            t.build_amd(m)
        m = t.read_amd(tiny())
        m["size_x"] = 4
        with self.assertRaises(t.AmdError):
            t.build_amd(m)


@unittest.skipUnless(real_maps(), "no 2.20-Map-Files checkout")
class RealFiles(unittest.TestCase):
    def test_every_map_round_trips(self):
        shutil.rmtree(OUT, ignore_errors=True)
        for p in real_maps():
            name = os.path.basename(p)[:-4]
            j = os.path.join(OUT, name + ".json")
            t.unpack(p, j)
            json.load(open(j, encoding="utf-8"))
            t.pack(j, os.path.join(OUT, name + ".amd"))
            self.assertEqual(open(os.path.join(OUT, name + ".amd"), "rb").read(), open(p, "rb").read(), name)
        self.assertGreaterEqual(len(real_maps()), 60)


if __name__ == "__main__":
    unittest.main()
