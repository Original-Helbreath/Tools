#!/usr/bin/env python
"""Extract files from the Helbreath v2.02 self-extracting installer (Hb202full_ENG.exe).

The stub (window class "InstItClass") is followed by a chain of records:

    u16 tag, u16 flags, u32 size, <size bytes>

flags bit 0 = compressed; then the body is u32 unpacked_size + a modified
deflate stream (see _Inflater below).  Tag 0x7F7F starts the file-data area:
u32 data_size, then the packed files.  Tag 0x123A holds the file table.
Each file is a modified-deflate stream at data_base + entry.offset and carries
a 32-bit rotate-add checksum XORed with a key derived from the stub and title.

File names in the table are 8-bit strings of unknown code page; they are
decoded as latin-1 (byte-preserving).  Names are refused, not rewritten, when
unsafe (traversal, drive or stream colons, reserved device names, trailing dot
or space, wildcard/control characters, duplicates under case folding).

The input is treated as untrusted: every read is bounds-checked, output is
streamed to disk under per-file, total and ratio caps, and one bad entry fails
only that entry.

Known limits (left as-is: this is a one-time tool for one official installer):
failed entries are not charged to the total cap and data offsets may repeat
(CPU/disk churn on a crafted table); records are all kept in memory (up to
MAX_RECORD each); entry count is uncapped and failed entries can leave empty
folders; ascii_safe passes ESC/BEL/DEL through; makedirs runs before the
realpath check.  Do not run it on untrusted installers without fixing these.

Usage: python hb_installer_extract.py <installer.exe> <output_dir> [--listing FILE]
           [--list-only] [--pure-python] [--max-file-size N] [--max-total-size N]
           [--max-ratio R]

The output dir must be new or empty.  Standard library only.  Prints ASCII only.
Never writes outside output_dir.
"""
import argparse
import datetime
import ntpath
import os
import struct
import sys
import time
import zlib

# --------------------------------------------------------------------------
# Modified deflate
#   block header: 4 bits LSB-first; bit3 = final; low 3 bits: 5 fixed, 6 dynamic,
#   7 stored.  Dynamic: HLIT(5)+257, HDIST(5)+1, HCLEN(4)+4, code-length order
#   18,17,16,0..15.  Stored: align to byte, u16 length (no complement), bytes.
#   Huffman codes, length/distance tables and the 32K window are standard.
# --------------------------------------------------------------------------
LBASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59,
         67, 83, 99, 115, 131, 163, 195, 227, 258]
LEXT = [0] * 8 + [1] * 4 + [2] * 4 + [3] * 4 + [4] * 4 + [5] * 4 + [0]
DBASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513,
         769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577]
DEXT = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10,
        11, 11, 12, 12, 13, 13]
CLORDER = [18, 17, 16] + list(range(16))
STD_CLORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]
WINDOW = 32768
FLUSH = 1 << 20          # emit decoded output in pieces of about this size
ZCHUNK = 4096            # source bytes fed to zlib per step
PAD = 16                 # zero bytes appended so bit peeks never index past the end

DEFAULT_MAX_FILE = 1 << 30          # 1 GiB per file
DEFAULT_MAX_TOTAL = 8 << 30         # 8 GiB for the whole run
DEFAULT_MAX_RATIO = 1100            # deflate cannot exceed ~1032:1
MAX_RECORD = 64 << 20               # metadata records
MAX_RECORD_COUNT = 64               # record chain length (real installer: 12)


class FormatError(Exception):
    pass


class _ZlibEarly(Exception):
    """zlib rejected a block before producing any output (fallback allowed)."""


def build_table(lengths):
    """Build an LSB-first lookup table.  Returns (table, maxbits); table is
    None when no symbol has a code.  Over-subscribed code sets are refused;
    incomplete ones are allowed (unused slots stay None and fail on use)."""
    maxl = max(lengths) if lengths else 0
    if maxl == 0:
        return None, 0
    if maxl > 15:
        raise FormatError("code length > 15")
    bl_count = [0] * (maxl + 1)
    for ln in lengths:
        if ln:
            bl_count[ln] += 1
    left = 1
    for b in range(1, maxl + 1):
        left = (left << 1) - bl_count[b]
        if left < 0:
            raise FormatError("over-subscribed Huffman code")
    next_code = [0] * (maxl + 1)
    code = 0
    for b in range(1, maxl + 1):
        code = (code + bl_count[b - 1]) << 1
        next_code[b] = code
    size = 1 << maxl
    table = [None] * size
    for sym, ln in enumerate(lengths):
        if not ln:
            continue
        c = next_code[ln]
        next_code[ln] += 1
        r = 0
        for _ in range(ln):
            r = (r << 1) | (c & 1)
            c >>= 1
        entry = (sym, ln)
        for j in range(r, size, 1 << ln):
            table[j] = entry
    return table, maxl


_FIXED = None


def _fixed_tables():
    global _FIXED
    if _FIXED is None:
        _FIXED = (build_table([8] * 144 + [9] * 112 + [7] * 24 + [8] * 8),
                  build_table([5] * 30))
    return _FIXED


class _BitWriter:
    """Accumulates bits LSB-first and hands out whole bytes."""

    def __init__(self, value=0, nbits=0):
        self.v = value
        self.n = nbits

    def put(self, value, nbits):
        self.v |= value << self.n
        self.n += nbits

    def put_bytes(self, b):
        self.v |= int.from_bytes(b, "little") << self.n
        self.n += 8 * len(b)

    def take(self):
        nb = self.n >> 3
        if nb == 0:
            return b""
        out = (self.v & ((1 << (nb * 8)) - 1)).to_bytes(nb, "little")
        self.v >>= nb * 8
        self.n -= nb * 8
        return out


class _Inflater:
    """Bounded, streaming decoder.  Calls emit(bytes) with output pieces and
    raises FormatError if the input is malformed, truncated, or would produce
    more than `limit` bytes."""

    def __init__(self, data, start, limit, emit, pure_python=False, nbytes=None):
        # If nbytes is given, `data` is already followed by >= PAD zero bytes
        # (so no padded copy is made); only data[:nbytes] is stream content.
        if nbytes is None:
            nbytes = len(data)
            data = bytes(data) + b"\0" * PAD
        elif len(data) < nbytes + PAD:
            raise ValueError("buffer lacks padding")
        self.nbytes = nbytes
        self.nbits = 8 * nbytes
        self.d = data
        self.bp = 8 * start
        self.limit = limit
        self.emit = emit
        self.pure = pure_python
        self.out = bytearray()  # window history (already emitted) + pending
        self.pend = 0           # index in out where pending output starts
        self.done = 0           # bytes already emitted
        self._set_lim()

    # -- bit access ------------------------------------------------------
    def peek(self, bp, n):
        i = bp >> 3
        return (int.from_bytes(self.d[i:i + 8], "little") >> (bp & 7)) & ((1 << n) - 1)

    def need(self, bp, n):
        if bp + n > self.nbits:
            raise FormatError("stream truncated at bit %d" % bp)

    # -- output ----------------------------------------------------------
    def _set_lim(self):
        self.lim = self.pend + min(FLUSH - 1, self.limit - self.done)

    def _spill(self, final=False):
        pending = len(self.out) - self.pend
        if self.done + pending > self.limit:
            raise FormatError("output exceeds limit of %d bytes" % self.limit)
        if pending and (final or pending >= FLUSH):
            self.emit(bytes(self.out[self.pend:]))
            self.done += pending
            if len(self.out) > WINDOW:
                del self.out[:len(self.out) - WINDOW]
            self.pend = len(self.out)
        self._set_lim()

    def total(self):
        return self.done + len(self.out) - self.pend

    # -- decoding --------------------------------------------------------
    def run(self):
        fixed = _fixed_tables()
        while True:
            self.need(self.bp, 4)
            hdr = self.peek(self.bp, 4)
            self.bp += 4
            final = hdr >> 3
            btype = hdr & 7
            if btype == 7:
                self.bp = (self.bp + 7) & ~7
                self.need(self.bp, 16)
                n = self.peek(self.bp, 16)
                self.bp += 16
                i = self.bp >> 3
                if i + n > self.nbytes:
                    raise FormatError("stored block past end of stream")
                self.out += self.d[i:i + n]
                self.bp += 8 * n
                if len(self.out) > self.lim:
                    self._spill()
            elif btype == 5:
                self._huffman(0b011, 3, self.bp, self.bp, fixed[0], fixed[1])
            elif btype == 6:
                hlit, hdist, cl, body, llen, dlen, data_bp = self._dynamic_header()
                lt = build_table(llen)
                if lt[0] is None:
                    raise FormatError("empty literal/length code")
                if not llen[256]:
                    raise FormatError("no end-of-block code")
                dt = build_table(dlen)
                w = _BitWriter()
                w.put(1, 1)          # BFINAL
                w.put(2, 2)          # BTYPE = dynamic
                w.put(hlit - 257, 5)
                w.put(hdist - 1, 5)
                w.put(15, 4)         # HCLEN = 19 codes
                for sym in STD_CLORDER:
                    w.put(cl[sym], 3)
                self._huffman(w.v, w.n, body, data_bp, lt, dt)
            else:
                raise FormatError("bad block type %d at bit %d" % (btype, self.bp - 4))
            if final:
                break
        self._spill(final=True)
        return (self.bp + 7) >> 3

    def _dynamic_header(self):
        bp = self.bp
        self.need(bp, 14)
        hlit = self.peek(bp, 5) + 257
        hdist = self.peek(bp + 5, 5) + 1
        hclen = self.peek(bp + 10, 4) + 4
        bp += 14
        if hlit > 286 or hdist > 30:
            raise FormatError("bad HLIT/HDIST")
        self.need(bp, 3 * hclen)
        cl = [0] * 19
        for i in range(hclen):
            cl[CLORDER[i]] = self.peek(bp, 3)
            bp += 3
        body = bp
        ctab, cmax = build_table(cl)
        if ctab is None:
            raise FormatError("empty code-length code")
        lens = []
        prev = None
        total = hlit + hdist
        nbits = self.nbits
        while len(lens) < total:
            e = ctab[self.peek(bp, cmax)]
            if e is None:
                raise FormatError("bad code-length code at bit %d" % bp)
            s, ln = e
            bp += ln
            if s < 16:
                lens.append(s)
                prev = s
            elif s == 16:
                if prev is None:
                    raise FormatError("repeat code with no previous length")
                lens.extend([prev] * (3 + self.peek(bp, 2)))
                bp += 2
            elif s == 17:
                lens.extend([0] * (3 + self.peek(bp, 3)))
                bp += 3
                prev = 0
            else:
                lens.extend([0] * (11 + self.peek(bp, 7)))
                bp += 7
                prev = 0
            if bp > nbits:
                raise FormatError("stream truncated in code lengths")
        if len(lens) > total:
            raise FormatError("code lengths overrun")
        return hlit, hdist, cl, body, lens[:hlit], lens[hlit:], bp

    def _huffman(self, prefix_val, prefix_bits, body_bp, data_bp, lt, dt):
        if not self.pure:
            try:
                self.bp = self._zlib_block(prefix_val, prefix_bits, body_bp)
                return
            except _ZlibEarly:
                pass  # zlib is stricter about some code sets; decode in Python
        self.bp = self._py_codes(data_bp, lt, dt)

    def _py_codes(self, bp, lt, dt):
        ltab, lmax = lt
        dtab, dmax = dt
        lmask = (1 << lmax) - 1
        dmask = (1 << dmax) - 1
        d = self.d
        out = self.out
        nbits = self.nbits
        lim = self.lim
        while True:
            if bp > nbits:
                raise FormatError("stream truncated in block data")
            if len(out) > lim:
                self._spill()
                lim = self.lim
            i = bp >> 3
            v = int.from_bytes(d[i:i + 8], "little") >> (bp & 7)
            e = ltab[v & lmask]
            if e is None:
                raise FormatError("bad literal/length code at bit %d" % bp)
            s, ln = e
            bp += ln
            if s < 256:
                out.append(s)
                continue
            if s == 256:
                if bp > nbits:
                    raise FormatError("stream truncated at end of block")
                if len(out) > lim:
                    self._spill()
                return bp
            s -= 257
            if s >= 29:
                raise FormatError("bad length symbol")
            v >>= ln
            eb = LEXT[s]
            length = LBASE[s] + (v & ((1 << eb) - 1))
            bp += eb
            if dtab is None:
                raise FormatError("match with empty distance code")
            i = bp >> 3
            v = int.from_bytes(d[i:i + 8], "little") >> (bp & 7)
            e = dtab[v & dmask]
            if e is None:
                raise FormatError("bad distance code at bit %d" % bp)
            ds, ln = e
            if ds >= 30:
                raise FormatError("bad distance symbol")
            v >>= ln
            eb = DEXT[ds]
            dist = DBASE[ds] + (v & ((1 << eb) - 1))
            bp += ln + eb
            start = len(out) - dist
            if start < 0:
                raise FormatError("distance too far back")
            if dist >= length:
                out += out[start:start + length]
            else:
                for k in range(length):
                    out.append(out[start + k])

    def _zlib_block(self, prefix_val, prefix_bits, body_bp):
        """Re-emit one Huffman block as a final standard-deflate block (prefix
        bits + original bits from body_bp to EOB) and decode it with zlib.
        Returns the source bit position just after EOB."""
        src = self.d
        end = self.nbytes
        a = body_bp >> 3
        sb = body_bp & 7
        w = _BitWriter(prefix_val, prefix_bits)
        if a < end:
            w.put(src[a] >> sb, 8 - sb)
        pos = a + 1
        delta = body_bp - prefix_bits
        window = bytes(self.out[-WINDOW:])
        dec = zlib.decompressobj(-15, zdict=window) if window else zlib.decompressobj(-15)
        fed = 0
        produced = 0
        pad_left = 64
        while True:
            if pos < end:
                w.put_bytes(src[pos:min(pos + ZCHUNK, end)])
                pos += ZCHUNK
            else:
                if pad_left <= 0:
                    raise FormatError("stream ended inside a block")
                w.put_bytes(b"\0" * 8)
                pad_left -= 8
            piece = w.take()
            room = self.limit - self.total()
            cp = dec.copy()
            try:
                o = dec.decompress(piece, room + 1)
            except zlib.error as ex:
                if produced == 0:
                    raise _ZlibEarly(str(ex))
                raise FormatError("zlib: %s" % ex)
            if len(o) > room:
                raise FormatError("output exceeds limit of %d bytes" % self.limit)
            if dec.eof:
                break
            if dec.unconsumed_tail:
                raise FormatError("decoder stalled")
            if o:
                produced += len(o)
                self.out += o
                if len(self.out) > self.lim:
                    self._spill()
            fed += len(piece)
        # locate the exact end bit inside the last consumed byte
        used = len(piece) - len(dec.unused_data)
        last = used - 1
        cap = len(o) + 1
        try:
            o0 = cp.decompress(piece[:last], cap)
        except zlib.error as ex:
            raise FormatError("zlib: %s" % ex)
        lastbyte = piece[last]
        end_bit = None
        for k in range(1, 9):
            mask = (1 << k) - 1
            ok = True
            for v in (lastbyte & mask, (lastbyte & mask) | (0xFF & ~mask)):
                t = cp.copy()
                try:
                    o1 = t.decompress(bytes([v]), cap)
                except zlib.error:
                    ok = False
                    break
                if not t.eof or o0 + o1 != o:
                    ok = False
                    break
            if ok:
                end_bit = (fed + last) * 8 + k
                break
        if end_bit is None:
            raise FormatError("could not locate end of block")
        src_end = end_bit + delta
        if src_end > self.nbits:
            raise FormatError("stream truncated inside a block")
        if o:
            self.out += o
            if len(self.out) > self.lim:
                self._spill()
        return src_end


def inflate_to(data, start, limit, emit, pure_python=False, nbytes=None):
    """Decode a modified-deflate stream from data[start:], calling emit() with
    output pieces.  Returns (bytes produced, end byte offset).  Pass nbytes
    when `data` already carries PAD trailing zero bytes (avoids a copy)."""
    inf = _Inflater(data, start, limit, emit, pure_python, nbytes)
    end = inf.run()
    return inf.done, end


def inflate(data, start=0, limit=MAX_RECORD, pure_python=False):
    """Convenience wrapper returning the output as bytes."""
    parts = []
    n, end = inflate_to(data, start, limit, parts.append, pure_python)
    return b"".join(parts), end


# --------------------------------------------------------------------------
# Checksum used by the stub (0x402501): rotate-left-1 then add, per dword,
# then per trailing byte.
# --------------------------------------------------------------------------
def cksum(buf, seed=0):
    c = Checksum(seed)
    c.update(buf)
    return c.final()


class Checksum:
    def __init__(self, seed=0):
        self.a = seed & 0xFFFFFFFF
        self.pend = b""

    def update(self, b):
        if self.pend:
            b = self.pend + bytes(b)
        n = len(b) // 4 * 4
        a = self.a
        for (v,) in struct.iter_unpack("<I", memoryview(b)[:n]):
            a = (((a << 1) | (a >> 31)) + v) & 0xFFFFFFFF
        self.a = a
        self.pend = bytes(b[n:])

    def final(self):
        a = self.a
        for x in self.pend:
            a = (((a << 1) | (a >> 31)) + x) & 0xFFFFFFFF
        return a


# --------------------------------------------------------------------------
# Container
# --------------------------------------------------------------------------
TAG_DATA = 0x7F7F
TAG_TITLE = 0x1237
TAG_UNINST = 0x123F
TAG_FILES = 0x123A
TAG_NAMES = {
    0x1234: "flags", 0x1235: "res_1235", 0x1236: "res_1236", 0x1237: "title",
    0x1238: "skip_1238", 0x1239: "dialogs", 0x123A: "file_table",
    0x123B: "res_123b", 0x123C: "serials", 0x123D: "font", 0x123E: "page",
    0x123F: "uninstaller_exe", 0x1240: "res_1240", 0x1241: "registry",
    0x1242: "res_1242", 0x7F7F: "data",
}


def find_payload(f, fsize):
    f.seek(0)
    head = f.read(0x100000)
    i = head.find(b"wwgT)H")
    start = 0x13000
    if i >= 0:
        if i + 10 > len(head):
            raise FormatError("truncated payload marker")
        start = struct.unpack_from("<I", head, i + 6)[0]
    if start < 2 or start + 8 > fsize:
        raise FormatError("payload offset 0x%x outside file" % start)
    return start, head


KEEP_TAGS = (TAG_TITLE, TAG_UNINST, TAG_FILES)


def read_records(f, start, fsize, max_ratio):
    """Walk the record chain, unpacking every record (each capped at
    MAX_RECORD, at most MAX_RECORD_COUNT records)."""
    recs = []
    pos = start
    kept = 0
    while True:
        if len(recs) >= MAX_RECORD_COUNT:
            raise FormatError("more than %d records" % MAX_RECORD_COUNT)
        if pos + 8 > fsize:
            raise FormatError("record chain runs past end of file at 0x%x" % pos)
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) != 8:
            raise FormatError("short record header at 0x%x" % pos)
        tag, fl, size = struct.unpack("<HHI", hdr)
        if tag == TAG_DATA:
            raw = f.read(4)
            if len(raw) != 4:
                raise FormatError("short data header")
            dsize = struct.unpack("<I", raw)[0]
            base = pos + 8 + 4
            recs.append((pos, tag, fl, dsize, None))
            if base + dsize > fsize:
                raise FormatError("data area truncated")
            return recs, base, dsize
        if not (0x1234 <= tag <= 0x1242):
            raise FormatError("unknown record tag 0x%04x at 0x%x" % (tag, pos))
        if size > MAX_RECORD or pos + 8 + size > fsize:
            raise FormatError("record 0x%04x size %d invalid" % (tag, size))
        body = f.read(size)
        if len(body) != size:
            raise FormatError("short record body at 0x%x" % pos)
        if fl & 1:
            if size < 4:
                raise FormatError("compressed record too short")
            usize = struct.unpack_from("<I", body, 0)[0]
            if usize > MAX_RECORD or usize > max(size, 1) * max_ratio:
                raise FormatError("record 0x%04x unpacked size %d refused" % (tag, usize))
            body, _ = inflate(body, 4, usize, pure_python=True)
            if len(body) != usize:
                raise FormatError("record 0x%04x size mismatch" % tag)
        recs.append((pos, tag, fl, size, body))
        pos += 8 + size


def parse_table(tbl):
    if len(tbl) < 4:
        raise FormatError("file table too short")
    count = struct.unpack_from("<I", tbl, 0)[0]
    p = 4
    entries = []
    while p < len(tbl) and len(entries) < count:
        if p + 2 > len(tbl):
            raise FormatError("truncated table entry at 0x%x" % p)
        ln = struct.unpack_from("<H", tbl, p)[0]
        if ln < 0x24 or p + ln > len(tbl):
            raise FormatError("bad table entry at 0x%x" % p)
        e = tbl[p:p + ln]
        off, csize, usize, crc = struct.unpack_from("<IIII", e, 6)
        flags = e[0x16]
        comp, index = struct.unpack_from("<HI", e, 0x1E)
        s = 0x24
        times = None
        if flags & 6:
            s += 0x14
        if flags & 8:
            if s + 0x18 > ln:
                raise FormatError("table entry at 0x%x too short for times" % p)
            times = struct.unpack_from("<QQQ", e, s)  # create, access, write
            s += 0x18
        if s >= ln:
            raise FormatError("table entry at 0x%x has no name" % p)
        strings = [x.decode("latin-1") for x in e[s:].split(b"\0")]
        entries.append({
            "index": index, "offset": off, "csize": csize, "usize": usize,
            "crc": crc, "flags": flags, "comp": comp, "times": times,
            "name": strings[0], "strings": [x for x in strings[1:] if x],
            "raw_17": e[0x17:0x1E].hex(),
        })
        p += ln
    if len(entries) != count:
        raise FormatError("table count %d but parsed %d" % (count, len(entries)))
    return entries


# --------------------------------------------------------------------------
# Paths and times
# --------------------------------------------------------------------------
FT_EPOCH = 116444736000000000      # 1970-01-01 as FILETIME
FT_MAX = 2650467743999999999       # 9999-12-31 23:59:59.9999999
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
for _p in ("COM", "LPT"):
    for _d in "0123456789¹²³":
        _RESERVED.add(_p + _d)
_BAD_CHARS = set('<>:"|?*\\/')


def _is_reserved(seg):
    stem = seg.split(".")[0].rstrip(" ").upper()
    if stem in _RESERVED or seg.rstrip(" .").upper() in _RESERVED:
        return True
    fn = getattr(ntpath, "isreserved", None)
    if fn is not None:
        try:
            if fn(seg):
                return True
        except (ValueError, TypeError):
            return True
    return False


def safe_join(root, name):
    """Map an archive name to a path under root, or raise FormatError."""
    parts = []
    for seg in name.replace("/", "\\").split("\\"):
        if seg == "":
            continue
        if seg.startswith("#") and seg.endswith("#") and len(seg) > 2:
            seg = "_" + seg[1:-1] + "_"
        if seg.strip(". ") == "":
            raise FormatError("unsafe path segment %r" % seg)
        if seg[-1] in ". ":
            raise FormatError("segment ends in dot or space: %r" % seg)
        if any(c in _BAD_CHARS or ord(c) < 32 for c in seg):
            raise FormatError("illegal character in %r" % seg)
        if _is_reserved(seg):
            raise FormatError("reserved device name %r" % seg)
        parts.append(seg)
    if not parts:
        raise FormatError("empty path %r" % name)
    root_abs = os.path.abspath(root)
    full = os.path.abspath(os.path.join(root_abs, *parts))
    if os.path.commonpath([root_abs, full]) != root_abs or full == root_abs:
        raise FormatError("path escapes output dir: %r" % name)
    return full


def ascii_safe(s):
    return str(s).encode("ascii", "replace").decode("ascii")


def say(msg):
    try:
        print(ascii_safe(msg))
    except Exception:
        pass


def fmt_time(ft):
    if not ft:
        return "-"
    if not (0 < ft <= FT_MAX):
        return "invalid"
    try:
        dt = datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=ft // 10)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, ValueError):
        return "invalid"


def set_times(path, times):
    """Apply access/write FILETIMEs.  Returns False if they were skipped."""
    _, at, wt = times
    if not (0 < wt <= FT_MAX):
        return False
    if not (0 < at <= FT_MAX):
        at = wt
    try:
        os.utime(path, ns=((at - FT_EPOCH) * 100, (wt - FT_EPOCH) * 100))
    except (OSError, OverflowError, ValueError):
        return False
    return True


class _Sink:
    def __init__(self, fh, checksum):
        self.fh = fh
        self.n = 0
        self.head = b""
        self.ck = Checksum() if checksum else None

    def __call__(self, b):
        if len(self.head) < 32:
            self.head += b[:32 - len(self.head)]
        if self.ck is not None:
            self.ck.update(b)
        if self.fh is not None:
            self.fh.write(b)
        self.n += len(b)


def _open_exclusive(outdir, full):
    parent = os.path.dirname(full)
    os.makedirs(parent, exist_ok=True)
    rroot = os.path.realpath(outdir)
    rfull = os.path.realpath(full)
    if os.path.commonpath([rroot, rfull]) != rroot or rfull == rroot:
        raise FormatError("resolved path escapes output dir")
    return open(full, "xb")


def extract_entry(f, ent, ctx):
    """Extract one entry.  Returns (status, size, head)."""
    name = ent["name"]
    if ent["flags"] & 0x36:
        raise FormatError("unsupported entry flags 0x%02x" % ent["flags"])
    if ent["offset"] + ent["csize"] > ctx["data_size"]:
        raise FormatError("entry outside data area")
    usize = ent["usize"]
    if usize > ctx["max_file"]:
        raise FormatError("size %d over per-file cap" % usize)
    if usize > max(ent["csize"], 1) * ctx["max_ratio"]:
        raise FormatError("ratio %d/%d over cap" % (usize, ent["csize"]))
    if ctx["total"] + usize > ctx["max_total"]:
        raise FormatError("total output cap reached")
    full = None
    if not ctx["list_only"]:
        full = safe_join(ctx["outdir"], name)
        key = os.path.normcase(full)
        if key in ctx["seen"]:
            raise FormatError("duplicate path")
        ctx["seen"].add(key)
    f.seek(ctx["data_base"] + ent["offset"])
    comp = f.read(ent["csize"])
    if len(comp) != ent["csize"]:
        raise FormatError("short read")
    uninst = bool(ent["flags"] & 1)
    fh = None
    status = "ok"
    try:
        if full is not None:
            fh = _open_exclusive(ctx["outdir"], full)
        sink = _Sink(fh, not uninst)
        extra = 0
        if uninst:
            exe = ctx["uninst_exe"]
            if fh is not None:
                fh.write(exe)
            extra = len(exe)
            status = "ok (uninstaller, no checksum)"
        inflate_to(comp, 0, usize, sink, ctx["pure"])
        if sink.n != usize:
            raise FormatError("size %d != %d" % (sink.n, usize))
        if not uninst:
            c = sink.ck.final()
            if c != (ent["crc"] ^ ctx["key"]):
                raise FormatError("checksum 0x%08x != 0x%08x" % (c, ent["crc"] ^ ctx["key"]))
        if fh is not None:
            fh.close()
            fh = None
            if ent["times"] and not set_times(full, ent["times"]):
                status = "ok (times skipped)" if status == "ok" else status + " (times skipped)"
    except BaseException:
        if fh is not None:
            fh.close()
            try:
                os.remove(full)  # remove the partial file this run just created
            except OSError:
                pass
        raise
    return status, sink.n + extra, sink.head


def main(argv=None):
    ap = argparse.ArgumentParser(description="Extract the Helbreath 2.02 installer payload.")
    ap.add_argument("installer")
    ap.add_argument("outdir")
    ap.add_argument("--listing", help="write a file listing to this path")
    ap.add_argument("--list-only", action="store_true", help="decode and list, write no files")
    ap.add_argument("--pure-python", action="store_true", help="do not use zlib for Huffman blocks (slow)")
    ap.add_argument("--max-file-size", type=int, default=DEFAULT_MAX_FILE)
    ap.add_argument("--max-total-size", type=int, default=DEFAULT_MAX_TOTAL)
    ap.add_argument("--max-ratio", type=int, default=DEFAULT_MAX_RATIO)
    args = ap.parse_args(argv)

    outdir = os.path.abspath(args.outdir)
    if not args.list_only:
        if os.path.exists(outdir):
            if not os.path.isdir(outdir) or os.listdir(outdir):
                say("error: output dir must be new or empty: %s" % outdir)
                return 2
        os.makedirs(outdir, exist_ok=True)

    try:
        fsize = os.path.getsize(args.installer)
        f = open(args.installer, "rb")
    except OSError as ex:
        say("error: cannot open installer: %s" % ex)
        return 2
    with f:
        try:
            start, head = find_payload(f, fsize)
            say("installer: %d bytes, payload at 0x%x" % (fsize, start))
            recs, data_base, data_size = read_records(f, start, fsize, args.max_ratio)
            bodies = {}
            for pos, tag, fl, size, body in recs:
                say("  record 0x%04x %-16s at 0x%08x stored=%d%s" % (
                    tag, TAG_NAMES.get(tag, "?"), pos, size,
                    "" if body is None else " unpacked=%d" % len(body)))
                if body is not None:
                    bodies.setdefault(tag, []).append(body)
            say("data area: 0x%x .. 0x%x (%d bytes)" % (data_base, data_base + data_size, data_size))
            stub = head[:start]
            seed = 0
            s = stub.find(b"wwINSS")
            e = stub.find(b"wwINSE")
            if 0 <= s < e:
                seed = cksum(stub[s:e])
            title = bodies.get(TAG_TITLE, [b""])[0].split(b"\0")[0]
            key = cksum(title, seed) if title else seed
            title_s = ascii_safe(title.decode("latin-1"))
            say("title: %s ; checksum key 0x%08x" % (title_s, key))
            entries = []
            for tbl in bodies.get(TAG_FILES, []):
                entries.extend(parse_table(tbl))
        except FormatError as ex:
            say("error: %s" % ex)
            return 2
        say("file table entries: %d" % len(entries))

        ctx = {
            "outdir": outdir, "list_only": args.list_only, "pure": args.pure_python,
            "data_base": data_base, "data_size": data_size, "key": key,
            "uninst_exe": bodies.get(TAG_UNINST, [b""])[0],
            "max_file": args.max_file_size, "max_total": args.max_total_size,
            "max_ratio": args.max_ratio, "total": 0, "seen": set(),
        }
        listing = []
        ok = failed = 0
        magic = {"pak_ok": 0, "pak_bad": 0, "wav_ok": 0, "wav_bad": 0}
        t0 = time.time()
        for i, ent in enumerate(entries):
            name = ent["name"]
            size = ent["usize"]
            try:
                status, size, hd = extract_entry(f, ent, ctx)
                low = name.lower()
                if low.endswith(".pak"):
                    magic["pak_ok" if hd.startswith(b"<Pak file header>") else "pak_bad"] += 1
                if low.endswith(".wav"):
                    magic["wav_ok" if (hd[:4] == b"RIFF" and hd[8:12] == b"WAVE") else "wav_bad"] += 1
                ok += 1
                ctx["total"] += size
            except (FormatError, zlib.error, OSError, ValueError, IndexError,
                    struct.error, OverflowError, MemoryError) as ex:
                status = "FAILED: %s" % ex
                failed += 1
            times = ent["times"] or (0, 0, 0)
            listing.append(ascii_safe("%4d  %10d  %10d  %s  %08x  %02x  %s  %s%s" % (
                ent["index"], size, ent["csize"], fmt_time(times[2]),
                ent["crc"] ^ key, ent["flags"], ent["raw_17"], name,
                "" if status == "ok" else "   [" + status + "]")))
            if status.startswith("FAILED"):
                say("  %s: %s" % (name, status))
            if (i + 1) % 25 == 0 or i + 1 == len(entries):
                say("  %d/%d files, %d bytes, %.0fs" % (i + 1, len(entries), ctx["total"], time.time() - t0))

    say("done: %d ok, %d failed, %d bytes" % (ok, failed, ctx["total"]))
    say("magic: pak %d ok / %d bad, wav %d ok / %d bad" % (
        magic["pak_ok"], magic["pak_bad"], magic["wav_ok"], magic["wav_bad"]))
    if args.listing:
        hdr = [
            "# Hb202full_ENG.exe payload listing",
            ascii_safe("# installer %s, %d bytes; title %s; checksum key 0x%08x" % (
                os.path.basename(args.installer), fsize, title_s, key)),
            "# files %d ok, %d failed; total %d bytes" % (ok, failed, ctx["total"]),
            "# columns: index  size  packed  mtime(UTC)  checksum  flags  raw[0x17:0x1e]  name",
        ]
        try:
            with open(args.listing, "w", encoding="ascii", newline="\r\n") as lf:
                lf.write("\n".join(hdr + listing) + "\n")
            say("listing written: %s" % args.listing)
        except OSError as ex:
            say("error: cannot write listing: %s" % ex)
            return 2
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
