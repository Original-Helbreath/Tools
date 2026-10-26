"""Extract and rebuild Helbreath sprite .pak files. Standard library only.

Format (as the 2.20 client reads it, Sprite.cpp CSprite::CSprite and Mydib.cpp CMyDib::CMyDib; PAKLib is the
full reference spec):

    pak header   20 bytes   "<Pak file header>" + 3 bytes (spaces, or NULs in some files)
                 int32      sheet count N
                 N x 8      uint32 offset, uint32 size of each sheet block (blocks are stored back to back)
    sheet block  100 bytes  "<Sprite File Header>" + 80 bytes the client skips (often stale tool memory)
                 int32      frame count F
                 F x 12     frame: int16 x, y, width, height, pivot_x, pivot_y (a rectangle in the bitmap)
                 4 bytes    skipped by the client (0, or a stale value)
                 BMP file   14-byte file header + 40-byte info header (+ palette) + pixels; the client takes the
                            pixel data straight after the palette and ignores bfOffBits

Layout, one folder per pak:

    <name>.pak/<name>.pak               the original pak
    <name>.pak/extracted/pak.json       pak-level bytes the client skips
    <name>.pak/extracted/NNN.png        sheet NNN as a PNG
    <name>.pak/extracted/NNN.json       its frames (x, y, width, height, pivot_x, pivot_y) and skipped bytes

build writes a pak from extracted/. A sheet whose PNG still has the original pixels keeps its original bitmap bytes
(taken from the pak beside extracted/), so an untouched folder rebuilds byte for byte; an edited PNG becomes a
24bpp BMP with a plain 40-byte header, which is what the client draws.

    python pak_tool.py info    SPRITES/ABS.pak
    python pak_tool.py extract SPRITES/ABS.pak out          -> out/ABS.pak/ABS.pak + out/ABS.pak/extracted/
    python pak_tool.py build   out/ABS.pak rebuilt/ABS.pak
"""
import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
import zlib

PAK_MAGIC = b"<Pak file header>"
SPRITE_MAGIC = b"<Sprite File Header>"
FRAME_KEYS = ["x", "y", "width", "height", "pivot_x", "pivot_y"]


class PakError(ValueError):
    pass


# --- pak container ------------------------------------------------------------------------------------------------

def read_pak(data):
    """Parse pak bytes: {header_tail, sheets: [{magic, header_pad, frames, pre_bitmap, bmp}]}."""
    if len(data) < 24 or data[:17] != PAK_MAGIC:
        raise PakError("not a pak (missing '<Pak file header>')")
    count = struct.unpack_from("<I", data, 20)[0]
    if 24 + 8 * count > len(data):
        raise PakError("sheet table runs past end of file")
    sheets = []
    for i in range(count):
        off, size = struct.unpack_from("<II", data, 24 + 8 * i)
        if off + size > len(data) or size < 108:
            raise PakError("sheet %d: block %d+%d outside file" % (i, off, size))
        block = data[off:off + size]
        n = struct.unpack_from("<I", block, 100)[0]
        bmp_at = 108 + 12 * n
        if bmp_at > size:
            raise PakError("sheet %d: %d frames do not fit its block" % (i, n))
        sheets.append({
            "magic": block[:20],
            "header_pad": block[20:100],
            "frames": [list(struct.unpack_from("<6h", block, 104 + 12 * f)) for f in range(n)],
            "pre_bitmap": block[bmp_at - 4:bmp_at],
            "bmp": block[bmp_at:],
        })
    return {"header_tail": data[17:20], "sheets": sheets}


def build_pak(header_tail, sheets):
    blocks = []
    for s in sheets:
        frames = b"".join(struct.pack("<6h", *f) for f in s["frames"])
        blocks.append(s["magic"] + s["header_pad"] + struct.pack("<I", len(s["frames"])) + frames
                      + s["pre_bitmap"] + s["bmp"])
    out = [PAK_MAGIC + header_tail, struct.pack("<I", len(blocks))]
    pos = 24 + 8 * len(blocks)
    for b in blocks:
        out.append(struct.pack("<II", pos, len(b)))
        pos += len(b)
    return b"".join(out + blocks)


# --- BMP <-> RGB --------------------------------------------------------------------------------------------------

def bmp_to_rgb(bmp):
    """Decode a pak bitmap the way the client does (pixels right after the palette). Returns (w, h, rgb rows)."""
    if len(bmp) < 54 or bmp[:2] != b"BM":
        raise PakError("not a BMP")
    hsize, w, h, _planes, bpp, comp = struct.unpack_from("<IiiHHI", bmp, 14)
    used = struct.unpack_from("<I", bmp, 46)[0]
    if hsize != 40 or comp != 0 or bpp not in (1, 4, 8, 24) or w <= 0 or h == 0:
        raise PakError("unsupported BMP (header %d, %d bpp, compression %d)" % (hsize, bpp, comp))
    colours = used or (1 << bpp if bpp <= 8 else 0)
    if colours > 256 or 54 + 4 * colours > len(bmp):
        raise PakError("BMP palette of %d colours does not fit" % colours)
    pal_at = 54
    pal = [bmp[pal_at + 4 * i:pal_at + 4 * i + 3][::-1] for i in range(colours)]
    pix = pal_at + 4 * colours
    stride = ((w * bpp + 31) // 32) * 4
    rows = []
    for r in range(abs(h)):
        line = bmp[pix + r * stride:pix + (r + 1) * stride]
        if len(line) < stride:
            raise PakError("BMP pixel data truncated")
        if bpp == 24:
            out = bytearray(w * 3)
            out[0::3], out[1::3], out[2::3] = line[2:3 * w:3], line[1:3 * w:3], line[0:3 * w:3]
        else:
            per = 8 // bpp
            mask = (1 << bpp) - 1
            out = bytearray()
            for x in range(w):
                idx = (line[x // per] >> (8 - bpp * (x % per + 1))) & mask
                out += pal[idx] if idx < len(pal) else b"\x00\x00\x00"
        rows.append(bytes(out))
    if h > 0:
        rows.reverse()  # bottom-up
    return w, abs(h), rows


def rgb_to_bmp(w, h, rows):
    """24bpp bottom-up BMP with a plain 40-byte info header."""
    stride = (w * 3 + 3) & ~3
    pad = b"\x00" * (stride - 3 * w)
    body = bytearray()
    for row in reversed(rows):
        bgr = bytearray(len(row))
        bgr[0::3], bgr[1::3], bgr[2::3] = row[2::3], row[1::3], row[0::3]
        body += bgr + pad
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(body), 0, 0, 0, 0)
    return b"BM" + struct.pack("<IHHI", 54 + len(body), 0, 0, 54) + info + bytes(body)


def rgb_hash(w, h, rows):
    return hashlib.sha1(struct.pack("<II", w, h) + b"".join(rows)).hexdigest()


# --- PNG <-> RGB --------------------------------------------------------------------------------------------------

def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def rgb_to_png(w, h, rows):
    raw = b"".join(b"\x00" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))


def png_to_rgb(png):
    """Read an 8-bit, non-interlaced PNG (RGB, RGBA, grey, grey+alpha or palette). Alpha is dropped."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise PakError("not a PNG")
    pos, idat, plte, ihdr = 8, [], b"", None
    while pos + 8 <= len(png):
        n, kind = struct.unpack_from(">I4s", png, pos)
        data = png[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", data)
        elif kind == b"PLTE":
            plte = data
        elif kind == b"IDAT":
            idat.append(data)
        elif kind == b"IEND":
            break
    if ihdr is None:
        raise PakError("PNG has no IHDR")
    w, h, depth, ctype, _c, _f, interlace = ihdr
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if depth != 8 or chans is None or interlace:
        raise PakError("PNG must be 8-bit per channel and not interlaced (got depth %d, type %d)" % (depth, ctype))
    if not 0 < w <= 65535 or not 0 < h <= 65535:
        raise PakError("PNG size %d x %d out of range" % (w, h))
    if ctype == 3 and not plte:
        raise PakError("palette PNG has no PLTE")
    raw = zlib.decompress(b"".join(idat))
    bpp, stride = chans, w * chans
    if len(raw) < h * (stride + 1):
        raise PakError("PNG image data truncated")
    prev = bytearray(stride)
    rows = []
    for y in range(h):
        f = raw[y * (stride + 1)]
        cur = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = cur[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                cur[i] = (cur[i] + a) & 255
            elif f == 2:
                cur[i] = (cur[i] + b) & 255
            elif f == 3:
                cur[i] = (cur[i] + ((a + b) >> 1)) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                cur[i] = (cur[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
            elif f != 0:
                raise PakError("bad PNG filter %d" % f)
        prev = cur
        if ctype == 2:
            rows.append(bytes(cur))
        elif ctype == 6:
            rows.append(bytes(b for i, b in enumerate(cur) if i % 4 != 3))
        elif ctype == 3:
            if max(cur) * 3 + 3 > len(plte):
                raise PakError("PNG palette index past end of PLTE")
            rows.append(b"".join(plte[3 * v:3 * v + 3] for v in cur))
        else:
            grey = cur[::chans]
            rows.append(bytes(v for g in grey for v in (g, g, g)))
    return w, h, rows


# --- commands -----------------------------------------------------------------------------------------------------

def extract(pak_path, out_root):
    data = open(pak_path, "rb").read()
    pak = read_pak(data)
    name = os.path.basename(pak_path)
    folder = os.path.join(out_root, name)
    ext = os.path.join(folder, "extracted")
    os.makedirs(ext, exist_ok=True)
    if os.path.abspath(pak_path) != os.path.abspath(os.path.join(folder, name)):
        shutil.copyfile(pak_path, os.path.join(folder, name))
    _write_json(os.path.join(ext, "pak.json"),
                {"format": "helbreath-pak", "source": name, "sheets": len(pak["sheets"]),
                 "header_tail": pak["header_tail"].hex()})
    for i, s in enumerate(pak["sheets"]):
        w, h, rows = bmp_to_rgb(s["bmp"])
        with open(os.path.join(ext, "%03d.png" % i), "wb") as f:
            f.write(rgb_to_png(w, h, rows))
        meta = {"image": "%03d.png" % i, "width": w, "height": h,
                "original_bpp": struct.unpack_from("<H", s["bmp"], 28)[0],
                "original_pixels_sha1": rgb_hash(w, h, rows),
                "frames": [dict(zip(FRAME_KEYS, f)) for f in s["frames"]]}
        if s["magic"] != SPRITE_MAGIC:
            meta["magic"] = s["magic"].hex()
        if s["header_pad"].strip(b"\x00"):
            meta["header_pad"] = s["header_pad"].hex()
        if s["pre_bitmap"].strip(b"\x00"):
            meta["pre_bitmap"] = s["pre_bitmap"].hex()
        _write_json(os.path.join(ext, "%03d.json" % i), meta)
    return folder, len(pak["sheets"])


def build(folder, out_pak):
    ext = os.path.join(folder, "extracted")
    with open(os.path.join(ext, "pak.json"), encoding="utf-8") as f:
        top = json.load(f)
    original = os.path.join(folder, os.path.basename(os.path.normpath(folder)))
    if os.path.isfile(original):
        with open(original, "rb") as f:
            orig_sheets = read_pak(f.read())["sheets"]
    else:
        orig_sheets = []
        print("warning: %s not found; every sheet is rebuilt as 24bpp, not byte for byte" % original,
              file=sys.stderr)
    sheets = []
    for i in range(top["sheets"]):
        with open(os.path.join(ext, "%03d.json" % i), encoding="utf-8") as f:
            meta = json.load(f)
        w, h, rows = png_to_rgb(open(os.path.join(ext, meta["image"]), "rb").read())
        if (i < len(orig_sheets) and rgb_hash(w, h, rows) == meta.get("original_pixels_sha1")
                and bmp_to_rgb(orig_sheets[i]["bmp"]) == (w, h, rows)):
            bmp = orig_sheets[i]["bmp"]  # unchanged pixels: keep the original bytes
        else:
            bmp = rgb_to_bmp(w, h, rows)
        frames = []
        for fr in meta["frames"]:
            v = [fr[k] for k in FRAME_KEYS]
            if any(type(x) is not int or not -32768 <= x <= 32767 for x in v):
                raise PakError("sheet %d: frame values must be 16-bit integers" % i)
            frames.append(v)
        sheets.append({
            "magic": bytes.fromhex(meta["magic"]) if "magic" in meta else SPRITE_MAGIC,
            "header_pad": bytes.fromhex(meta["header_pad"]) if "header_pad" in meta else b"\x00" * 80,
            "frames": frames,
            "pre_bitmap": bytes.fromhex(meta["pre_bitmap"]) if "pre_bitmap" in meta else b"\x00" * 4,
            "bmp": bmp,
        })
        for k, n in (("header_pad", 80), ("pre_bitmap", 4), ("magic", 20)):
            if len(sheets[-1][k]) != n:
                raise PakError("sheet %d: %s must be %d bytes" % (i, k, n))
    tail = bytes.fromhex(top.get("header_tail", "202020"))
    if len(tail) != 3:
        raise PakError("header_tail must be 3 bytes")
    data = build_pak(tail, sheets)
    os.makedirs(os.path.dirname(os.path.abspath(out_pak)), exist_ok=True)
    with open(out_pak, "wb") as f:
        f.write(data)
    return len(sheets)


def info(pak_path):
    pak = read_pak(open(pak_path, "rb").read())
    print("%s: %d sheets" % (os.path.basename(pak_path), len(pak["sheets"])))
    for i, s in enumerate(pak["sheets"]):
        w, h, _planes, bpp = struct.unpack_from("<iiHH", s["bmp"], 18) if len(s["bmp"]) >= 30 else ("?",) * 4
        print("  %3d  %4s x %-4s %2sbpp  %3d frames" % (i, w, h, bpp, len(s["frames"])))


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Extract and rebuild Helbreath sprite .pak files.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info", help="list sheets")
    p.add_argument("pak")
    p = sub.add_parser("extract", help="pak -> <out>/<name>.pak/{<name>.pak, extracted/}")
    p.add_argument("pak")
    p.add_argument("out_root")
    p = sub.add_parser("build", help="<name>.pak folder -> pak")
    p.add_argument("folder")
    p.add_argument("pak")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "info":
            info(a.pak)
        elif a.cmd == "extract":
            folder, n = extract(a.pak, a.out_root)
            print("extracted %d sheets to %s" % (n, folder))
        else:
            print("built %d sheets into %s" % (build(a.folder, a.pak), a.pak))
    except (PakError, OSError, KeyError, TypeError, ValueError, zlib.error, struct.error) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
