"""Tests for pak_tool.py.

Real-file tests extract and rebuild every .pak in 2.20-Client-Files/SPRITES byte for byte. Point HB_CLIENT_FILES at
that checkout; by default the sibling folder ../../2.20-Client-Files is used. They are skipped when none is found.
Output goes to ../.test-out (gitignored), never the system temp folder.

    python -m unittest test_pak_tool -v
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
import pak_tool as t  # noqa: E402

CLIENT_FILES = os.environ.get("HB_CLIENT_FILES",
                              os.path.normpath(os.path.join(HERE, "..", "..", "2.20-Client-Files")))
SPRITES = os.path.join(CLIENT_FILES, "SPRITES")
OUT = os.path.normpath(os.path.join(HERE, "..", ".test-out", "pak"))


def rows(w, h):
    return [bytes((x * 7 + y) & 255 for x in range(w) for _ in range(3)) for y in range(h)]


def sheet(w=8, h=3):
    return {"magic": t.SPRITE_MAGIC, "header_pad": b"\x00" * 80, "pre_bitmap": b"\x00" * 4,
            "frames": [[0, 0, 4, 3, -2, -1], [4, 0, 4, 3, 0, 0]], "bmp": t.rgb_to_bmp(w, h, rows(w, h))}


def real_paks():
    return [p for p in sorted(glob.glob(os.path.join(SPRITES, "*.pak"))) if os.path.getsize(p)]


class Crafted(unittest.TestCase):
    def test_layout(self):
        data = t.build_pak(b"   ", [sheet()])
        self.assertEqual(data[:20], b"<Pak file header>   ")
        off, size = struct.unpack_from("<II", data, 24)
        self.assertEqual((off, off + size), (32, len(data)))
        self.assertEqual(data[off + 100:off + 104], struct.pack("<i", 2))
        self.assertEqual(data[off + 132:off + 134], b"BM")  # 108 + 12 * frames, as the client computes
        self.assertEqual(t.read_pak(data)["sheets"][0], sheet())

    def test_rejects(self):
        for bad in [b"", b"<Pak file header>", b"x" * 40,
                    b"<Pak file header>   " + struct.pack("<I", 5),
                    b"<Pak file header>   " + struct.pack("<III", 1, 32, 999),
                    b"<Pak file header>   " + struct.pack("<III", 1, 32, 108) + t.SPRITE_MAGIC + b"\x00" * 80
                    + struct.pack("<I", 10 ** 6) + b"\x00" * 4]:
            with self.assertRaises(t.PakError):
                t.read_pak(bad)

    def test_png_round_trip_odd_width(self):
        r = rows(5, 3)  # 15-byte rows: exercises BMP row padding
        self.assertEqual(t.png_to_rgb(t.rgb_to_png(5, 3, r)), (5, 3, r))
        self.assertEqual(t.bmp_to_rgb(t.rgb_to_bmp(5, 3, r)), (5, 3, r))

    def test_png_filters_and_types(self):
        import zlib
        r = rows(4, 2)

        def png(ctype, raw):
            return (b"\x89PNG\r\n\x1a\n" + t._chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 2, 8, ctype, 0, 0, 0))
                    + t._chunk(b"IDAT", zlib.compress(raw)) + t._chunk(b"IEND", b""))
        # filter 1 (sub) on every row
        sub = b""
        for row in r:
            enc = bytes((row[i] - (row[i - 3] if i >= 3 else 0)) & 255 for i in range(len(row)))
            sub += b"\x01" + enc
        self.assertEqual(t.png_to_rgb(png(2, sub))[2], r)
        rgba = b"".join(b"\x00" + b"".join(row[i:i + 3] + b"\xff" for i in range(0, 12, 3)) for row in r)
        self.assertEqual(t.png_to_rgb(png(6, rgba))[2], r)
        with self.assertRaises(t.PakError):
            t.png_to_rgb(b"\x89PNG\r\n\x1a\n" + t._chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 16, 2, 0, 0, 0)))

    def test_8bpp_palette_decode(self):
        pal = b"".join(bytes((i, 0, 255 - i, 0)) for i in range(4))
        pix = bytes([0, 1, 2, 3])
        info = struct.pack("<IiiHHIIiiII", 40, 4, 1, 1, 8, 0, 4, 0, 0, 4, 0)
        bmp = b"BM" + struct.pack("<IHHI", 54 + 16 + 4, 0, 0, 70) + info + pal + pix
        self.assertEqual(t.bmp_to_rgb(bmp)[2], [bytes((255, 0, 0, 254, 0, 1, 253, 0, 2, 252, 0, 3))])


@unittest.skipUnless(real_paks(), "no 2.20-Client-Files checkout")
class RealFiles(unittest.TestCase):
    def test_every_pak_round_trips_in_memory(self):
        n = 0
        for p in real_paks():
            data = open(p, "rb").read()
            pak = t.read_pak(data)
            self.assertEqual(t.build_pak(pak["header_tail"], pak["sheets"]), data, os.path.basename(p))
            n += len(pak["sheets"])
        self.assertGreater(n, 6000)

    def test_every_pak_extracts_and_rebuilds(self):
        root = os.path.join(OUT, "all")
        shutil.rmtree(root, ignore_errors=True)
        for p in real_paks():
            folder, _n = t.extract(p, root)
            out = os.path.join(OUT, "rebuilt", os.path.basename(p))
            t.build(folder, out)
            self.assertEqual(open(out, "rb").read(), open(p, "rb").read(), os.path.basename(p))

    def test_edited_sheet_becomes_24bpp(self):
        root = os.path.join(OUT, "edit")
        shutil.rmtree(root, ignore_errors=True)
        folder, _n = t.extract(os.path.join(SPRITES, "ABS.pak"), root)
        with open(os.path.join(folder, "extracted", "000.png"), "wb") as f:
            f.write(t.rgb_to_png(5, 7, rows(5, 7)))
        meta_p = os.path.join(folder, "extracted", "000.json")
        meta = json.load(open(meta_p, encoding="utf-8"))
        meta["frames"][0]["pivot_x"] = -9
        json.dump(meta, open(meta_p, "w", encoding="utf-8"))
        t.build(folder, os.path.join(root, "out.pak"))
        back = t.read_pak(open(os.path.join(root, "out.pak"), "rb").read())["sheets"]
        orig = t.read_pak(open(os.path.join(SPRITES, "ABS.pak"), "rb").read())["sheets"]
        self.assertEqual(back[0]["bmp"], t.rgb_to_bmp(5, 7, rows(5, 7)))
        self.assertEqual(back[0]["frames"][0][4], -9)
        self.assertEqual(back[1:], orig[1:])

    def test_zeroed_sheet_header_kept(self):
        folder, _n = t.extract(os.path.join(SPRITES, "MHelm5.pak"), os.path.join(OUT, "mhelm"))
        meta = json.load(open(os.path.join(folder, "extracted", "009.json"), encoding="utf-8"))
        self.assertIn("magic", meta)


if __name__ == "__main__":
    unittest.main()
