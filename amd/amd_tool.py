"""Unpack and repack Helbreath .amd map files. Standard library only.

Format (as the 2.20 client reads it, MapData.cpp CMapData::OpenMapDataFile, and the game server, Map.cpp):

    header  256 bytes  ASCII text, NUL padded: "MAPSIZEX =  150 MAPSIZEY =  150 TILESIZE = 10   "
    tiles   X*Y records of TILESIZE (always 10) bytes, row by row (y outer, x inner):
            int16 tile_sprite, tile_frame, object_sprite, object_frame
            uint8 flags   0x80 blocked (no walking), 0x40 teleport, 0x20 farming allowed (server, v2.19)
            uint8 unused  (0 in every shipped map)
            The server also treats tile_sprite 19 as water.

unpack writes one JSON file: the header text kept verbatim, and each field as a grid of rows (grid[y][x]).
pack turns that JSON back into a byte-exact .amd.

    python amd_tool.py info   maps/arebrk11/arebrk11.amd
    python amd_tool.py unpack maps/arebrk11/arebrk11.amd out/arebrk11.json
    python amd_tool.py pack   out/arebrk11.json rebuilt/arebrk11.amd
"""
import argparse
import json
import os
import re
import struct
import sys

HEADER = 256
FIELDS = ["tile_sprite", "tile_frame", "object_sprite", "object_frame", "flags", "unused"]
FLAG_BITS = {"blocked": 0x80, "teleport": 0x40, "farming": 0x20}
WATER_SPRITE = 19


class AmdError(ValueError):
    pass


def parse_header(text):
    """Same rule as the loaders: a key token, then the next token is its number (atoi); a later key wins."""
    tokens = re.split(r"[= ,\t\n]+", text)
    out = {}
    for i, t in enumerate(tokens[:-1]):
        for key in ("MAPSIZEX", "MAPSIZEY", "TILESIZE"):
            if t.startswith(key):
                m = re.match(r"[-+]?\d+", tokens[i + 1])
                out[key] = int(m.group()) if m else 0
    return out


def read_amd(data):
    if len(data) < HEADER:
        raise AmdError("shorter than the 256-byte header")
    raw = data[:HEADER]
    text = raw.split(b"\x00")[0].decode("latin-1")
    if raw[len(text):].strip(b"\x00"):
        raise AmdError("unexpected bytes after the header text")  # never seen; refuse rather than lose them
    h = parse_header(text)
    sx, sy, ts = h.get("MAPSIZEX", 0), h.get("MAPSIZEY", 0), h.get("TILESIZE", 10)
    if sx <= 0 or sy <= 0:
        raise AmdError("header has no valid MAPSIZEX / MAPSIZEY")
    if ts != 10:
        raise AmdError("TILESIZE %d; only 10-byte tiles are known" % ts)
    need = HEADER + sx * sy * ts
    if len(data) != need:
        raise AmdError("file is %d bytes, header says %d" % (len(data), need))
    grids = {k: [] for k in FIELDS}
    pos = HEADER
    for _y in range(sy):
        rows = {k: [] for k in FIELDS}
        for _x in range(sx):
            vals = struct.unpack_from("<4hBB", data, pos)
            for k, v in zip(FIELDS, vals):
                rows[k].append(v)
            pos += ts
        for k in FIELDS:
            grids[k].append(rows[k])
    return {"header_text": text, "size_x": sx, "size_y": sy, "tile_size": ts, "grids": grids}


def build_amd(m):
    text = m["header_text"].encode("latin-1")
    if len(text) > HEADER:
        raise AmdError("header text longer than 256 bytes")
    h = parse_header(m["header_text"])
    sx, sy = m["size_x"], m["size_y"]
    if (h.get("MAPSIZEX"), h.get("MAPSIZEY"), h.get("TILESIZE", 10)) != (sx, sy, 10):
        raise AmdError("header_text sizes do not match size_x / size_y / 10-byte tiles")
    g = m["grids"]
    for k in FIELDS:
        if len(g[k]) != sy or any(len(r) != sx for r in g[k]):
            raise AmdError("grid %s is not %d rows of %d" % (k, sy, sx))
    out = [text + b"\x00" * (HEADER - len(text))]
    for y in range(sy):
        for x in range(sx):
            out.append(struct.pack("<4hBB", *(g[k][y][x] for k in FIELDS)))
    return b"".join(out)


def unpack(amd_path, json_path):
    m = read_amd(open(amd_path, "rb").read())
    doc = {"format": "helbreath-amd", "source": os.path.basename(amd_path),
           "flag_bits": {k: "0x%02x" % v for k, v in FLAG_BITS.items()}}
    doc.update(m)
    os.makedirs(os.path.dirname(os.path.abspath(json_path)), exist_ok=True)
    with open(json_path, "w", encoding="utf-8", newline="\n") as f:
        # one grid row per line keeps the file diffable and readable
        f.write("{\n")
        for k in ("format", "source", "flag_bits", "header_text", "size_x", "size_y", "tile_size"):
            f.write(" %s: %s,\n" % (json.dumps(k), json.dumps(doc[k])))
        f.write(' "grids": {\n')
        for i, k in enumerate(FIELDS):
            rows = ",\n".join("   " + json.dumps(r, separators=(",", ":")) for r in m["grids"][k])
            f.write("  %s: [\n%s\n  ]%s\n" % (json.dumps(k), rows, "," if i < len(FIELDS) - 1 else ""))
        f.write(" }\n}\n")
    return m


def pack(json_path, amd_path):
    with open(json_path, encoding="utf-8") as f:
        m = json.load(f)
    data = build_amd(m)
    os.makedirs(os.path.dirname(os.path.abspath(amd_path)), exist_ok=True)
    with open(amd_path, "wb") as f:
        f.write(data)
    return m


def info(amd_path):
    m = read_amd(open(amd_path, "rb").read())
    flags = [v for r in m["grids"]["flags"] for v in r]
    n = len(flags)
    water = sum(v == WATER_SPRITE for r in m["grids"]["tile_sprite"] for v in r)
    print("%s: %d x %d tiles" % (os.path.basename(amd_path), m["size_x"], m["size_y"]))
    for name, bit in FLAG_BITS.items():
        print("  %-9s %7d" % (name, sum(1 for v in flags if v & bit)))
    print("  %-9s %7d" % ("water", water))
    print("  %-9s %7d" % ("total", n))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Unpack and repack Helbreath .amd map files.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info", help="map size and tile flag counts")
    p.add_argument("amd")
    p = sub.add_parser("unpack", help="amd -> JSON")
    p.add_argument("amd")
    p.add_argument("json")
    p = sub.add_parser("pack", help="JSON -> amd")
    p.add_argument("json")
    p.add_argument("amd")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "info":
            info(a.amd)
        elif a.cmd == "unpack":
            m = unpack(a.amd, a.json)
            print("unpacked %d x %d map to %s" % (m["size_x"], m["size_y"], a.json))
        else:
            m = pack(a.json, a.amd)
            print("packed %d x %d map into %s" % (m["size_x"], m["size_y"], a.amd))
    except (AmdError, OSError, KeyError, TypeError, ValueError, struct.error) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
