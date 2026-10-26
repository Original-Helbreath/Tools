"""Crafted-input tests for tools/hb_installer_extract.py (review findings H1..L4).

Run: python -I test_robust.py
Writes only under installer/tests/run_<ns>/ (git-ignored).
"""
import contextlib
import io
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hb_installer_extract as H

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests",
                    "run_%d" % time.time_ns())
os.makedirs(BASE)
FAST = 5.0  # seconds a "fails cleanly and fast" case may take
results = []


def check(name, fn):
    t = time.time()
    try:
        ok, detail = fn()
    except Exception as ex:  # any uncaught exception is a test failure
        ok, detail = False, "UNCAUGHT %s: %s" % (type(ex).__name__, ex)
    dt = time.time() - t
    if dt > FAST:
        ok, detail = False, detail + " (too slow)"
    results.append(ok)
    print("%-4s %-58s %6.2fs  %s" % ("PASS" if ok else "FAIL", name, dt, H.ascii_safe(detail)))


# ---------------------------------------------------------------- encoders
def put_code(w, code, nbits):
    """Huffman codes are sent MSB-first."""
    for i in range(nbits - 1, -1, -1):
        w.put((code >> i) & 1, 1)


def fixed_lit(w, sym):
    if sym < 144:
        put_code(w, 0x30 + sym, 8)
    elif sym < 256:
        put_code(w, 0x190 + sym - 144, 9)
    elif sym < 280:
        put_code(w, sym - 256, 7)
    else:
        put_code(w, 0xC0 + sym - 280, 8)


def finish(w):
    w.put(0, (8 - w.n % 8) % 8)
    return w.take()


def fixed_stream(lits=b"", copies=0, final=True):
    """Fixed block: literals, then `copies` matches of length 258 distance 1."""
    w = H._BitWriter()
    w.put((8 if final else 0) | 5, 4)
    for b in lits:
        fixed_lit(w, b)
    for _ in range(copies):
        fixed_lit(w, 285)          # length 258
        put_code(w, 0, 5)          # distance symbol 0 -> 1
    fixed_lit(w, 256)
    return finish(w)


def stored_stream(payload):
    return bytes([0x0F]) + struct.pack("<H", len(payload)) + payload


def dyn_A_forever():
    """Dynamic block whose literal code maps all-zero bits to 'A', then the
    stream simply ends (truncated).  Old code decoded zero padding forever."""
    w = H._BitWriter()
    w.put(6, 4)                    # dynamic, not final
    w.put(0, 5)                    # HLIT 257
    w.put(0, 5)                    # HDIST 1
    w.put(1, 4)                    # HCLEN 5 -> symbols 18,17,16,0,1
    for ln in (2, 2, 0, 2, 2):
        w.put(ln, 3)
    # cl codes (len 2): 0->00, 1->01, 17->10, 18->11
    def cl(sym):
        put_code(w, {0: 0, 1: 1, 17: 2, 18: 3}[sym], 2)
    cl(18); w.put(65 - 11, 7)      # 0..64 zero
    cl(1)                          # 'A' length 1
    cl(18); w.put(127, 7)          # 138 zeros
    cl(18); w.put(52 - 11, 7)      # 52 zeros -> up to 255
    cl(1)                          # 256 length 1
    cl(0)                          # distance code length 0
    return finish(w)


def dyn_header(cl_lens_in_order, body_writer=None, hlit=0, hdist=0):
    w = H._BitWriter()
    w.put(0xE, 4)
    w.put(hlit, 5)
    w.put(hdist, 5)
    w.put(len(cl_lens_in_order) - 4, 4)
    for ln in cl_lens_in_order:
        w.put(ln, 3)
    if body_writer:
        body_writer(w)
    w.put_bytes(b"\0" * 4)
    return finish(w)


def run_inflate(data, limit, pure):
    emitted = [0]

    def emit(b):
        emitted[0] += len(b)
    try:
        H.inflate_to(data, 0, limit, emit, pure)
        return None, emitted[0]
    except H.FormatError as ex:
        return str(ex), emitted[0]


# ---------------------------------------------------------------- installer builder
def entry_bytes(name, off, cs, us, crc, flags=8, times=(0x01C12A0F00000000,) * 3, raw_name=None):
    nm = raw_name if raw_name is not None else name.encode("latin-1")
    body = struct.pack("<HHH", 0, 0, 0)[:4]  # placeholder u16 len + u16
    e = bytearray(0x24)
    struct.pack_into("<IIII", e, 6, off, cs, us, crc)
    e[0x16] = flags
    e[0x1A] = 1
    struct.pack_into("<HI", e, 0x1E, 0, 0)
    if flags & 8:
        e += struct.pack("<QQQ", *times)
    e += nm + b"\0"
    struct.pack_into("<H", e, 0, len(e))
    return bytes(e)


def rec(tag, body, flags=0):
    return struct.pack("<HHI", tag, flags, len(body)) + body


def make_installer(path, files, title=b"Test", table=None, cut=None, uninst=b"MZUNINST"):
    """files: list of dicts name, stream, usize, crc(optional), flags, times, raw_name."""
    key = H.cksum(title, 0)
    data = bytearray()
    ents = []
    for i, fd in enumerate(files):
        off = len(data)
        data += fd["stream"]
        crc = fd.get("crc")
        if crc is None:
            crc = H.cksum(fd.get("payload", b"")) ^ key
        ents.append(entry_bytes(fd["name"], off, len(fd["stream"]), fd["usize"], crc,
                                fd.get("flags", 8), fd.get("times", (0x01C12A0F00000000,) * 3),
                                fd.get("raw_name")))
    if table is None:
        table = struct.pack("<I", len(ents)) + b"".join(ents)
    start = 0x40
    stub = bytearray(start)
    stub[0:2] = b"MZ"
    stub[0x10:0x16] = b"wwgT)H"
    struct.pack_into("<I", stub, 0x16, start)
    blob = bytes(stub) + rec(0x1237, title + b"\0") + rec(0x123F, uninst) + rec(0x123A, table)
    blob += struct.pack("<HHI", 0x7F7F, 0, len(data)) + struct.pack("<I", len(data)) + bytes(data)
    if cut is not None:
        blob = blob[:cut]
    with open(path, "wb") as f:
        f.write(blob)
    return path


def plain(name, payload, **kw):
    d = {"name": name, "stream": stored_stream(payload), "usize": len(payload), "payload": payload}
    d.update(kw)
    return d


_n = [0]


def run_main(files=None, args=(), installer=None, outdir=None, **mk):
    _n[0] += 1
    tag = "t%03d" % _n[0]
    inst = installer or make_installer(os.path.join(BASE, tag + ".exe"), files or [], **mk)
    out = outdir or os.path.join(BASE, tag + "_out")
    lst = os.path.join(BASE, tag + "_list.txt")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = H.main([inst, out, "--listing", lst] + list(args))
    listing = open(lst, "rb").read() if os.path.exists(lst) else b""
    return rc, buf.getvalue(), listing, out


# ================================================================ tests
# H1: truncated input must not be read as endless zero bits
A = dyn_A_forever()
for pure in (True, False):
    def t(pure=pure):
        err, n = run_inflate(A, 1 << 30, pure)
        return (err is not None and "truncat" in err or "ended" in (err or "")), "%s; emitted %d" % (err, n)
    check("H1 truncated dynamic block (%s)" % ("pure" if pure else "zlib"), t)


def t_h1_record():
    body = struct.pack("<I", 1000) + A
    inst = os.path.join(BASE, "h1rec.exe")
    make_installer(inst, [plain("a.txt", b"x")])
    blob = open(inst, "rb").read()
    # replace title record by a compressed one carrying the hang stream
    i = blob.find(b"\x37\x12")
    t_old = struct.unpack_from("<I", blob, i + 4)[0]
    blob = blob[:i] + rec(0x1237, body, 1) + blob[i + 8 + t_old:]
    open(inst, "wb").write(blob)
    rc, out, _, _ = run_main(installer=inst)
    return rc == 2 and "error:" in out, "rc=%d %s" % (rc, out.strip().splitlines()[-1])
check("H1 truncated stream inside a compressed record", t_h1_record)

for pure in (True, False):
    def t(pure=pure):
        s = fixed_stream(b"A" * 5000)
        err, n = run_inflate(s, 10, pure)
        return err is not None and "limit" in err and n <= 10, "%s; emitted %d" % (err, n)
    check("H1 limit enforced inside literal loop (%s)" % ("pure" if pure else "zlib"), t)

# H2: decompression bomb
BOMB = fixed_stream(b"A", copies=32000)   # ~52 KB -> ~8 MB
for pure in (True, False):
    def t(pure=pure):
        err, n = run_inflate(BOMB, 1000, pure)
        return err is not None and n <= 1000, "%d-byte stream: %s; emitted %d" % (len(BOMB), err, n)
    check("H2 bomb vs output limit 1000 (%s)" % ("pure" if pure else "zlib"), t)


def t_bomb_claim():
    rc, out, lst, _ = run_main([{"name": "bomb.bin", "stream": BOMB, "usize": 0xFFFFFFFF}],
                               args=["--max-file-size", str(1 << 40)])
    rc2, _, lst2, _ = run_main([{"name": "bomb.bin", "stream": BOMB, "usize": 0xFFFFFFFF}])
    return (rc == 1 and b"ratio" in lst and rc2 == 1 and b"cap" in lst2), \
        lst.splitlines()[-1].decode() + " | default caps: " + lst2.splitlines()[-1].decode()[-40:]
check("H2 bomb claiming 4 GiB refused by ratio cap", t_bomb_claim)


def t_bomb_filecap():
    rc, out, lst, _ = run_main([{"name": "bomb.bin", "stream": BOMB, "usize": 8256001}],
                               args=["--max-file-size", "1000000"])
    return rc == 1 and b"per-file cap" in lst, lst.splitlines()[-1].decode()
check("H2 per-file cap", t_bomb_filecap)


def t_totalcap():
    files = [plain("f%d.bin" % i, b"x" * 600) for i in range(3)]
    rc, out, lst, _ = run_main(files, args=["--max-total-size", "1500"])
    lines = lst.splitlines()[4:]
    return rc == 1 and b"total output cap" in lines[2] and b"FAILED" not in lines[0], lines[2].decode()
check("H2 total cap (third file refused, first two kept)", t_totalcap)


def t_bomb_underclaim():
    # usize claims 1000 but stream would give 8 MB: stops at the limit
    rc, out, lst, od = run_main([{"name": "bomb.bin", "stream": BOMB, "usize": 1000}])
    gone = not os.path.exists(os.path.join(od, "bomb.bin"))
    return rc == 1 and b"exceeds limit" in lst and gone, lst.splitlines()[-1].decode() + "; partial removed=%s" % gone
check("H2 under-claimed bomb stops at usize, partial removed", t_bomb_underclaim)

# M1: reserved device names
def t_reserved():
    bad = ["CON", "con.txt", "COM1.wav", "LPT1", "CONIN$", "CONOUT$", "COM\u00b9", "lpt\u00b2.pak",
           "a\\CON\\b.txt", "nul .txt", "AUX.tar.gz", "PRN", "x\\com9"]
    leaked = []
    for n in bad:
        try:
            H.safe_join(BASE, n)
            leaked.append(n)
        except H.FormatError:
            pass
    good = H.safe_join(BASE, "SPRITES\\console.pak")
    return not leaked, "refused %d/%d, leaked %r; 'console.pak' allowed=%s" % (
        len(bad) - len(leaked), len(bad), leaked, bool(good))
check("M1 reserved names refused in any segment", t_reserved)

# M2
def t_nonempty():
    od = os.path.join(BASE, "nonempty")
    os.makedirs(od)
    open(os.path.join(od, "keep.txt"), "w").close()
    rc, out, _, _ = run_main([plain("a.txt", b"x")], outdir=od)
    return rc == 2 and "new or empty" in out, "rc=%d" % rc
check("M2 non-empty output dir refused", t_nonempty)


def t_dots():
    bad = ["a.", "a ", "dir.\\x", "...", ". .", "a\\..\\b", "a\\.\\b", "x:y", "a*b", "a\tb"]
    leaked = []
    for n in bad:
        try:
            H.safe_join(BASE, n)
            leaked.append(n)
        except H.FormatError:
            pass
    return not leaked, "refused %d/%d, leaked %r" % (len(bad) - len(leaked), len(bad), leaked)
check("M2/L2 trailing dot/space, dot-only, colon, wildcard refused", t_dots)


def t_dupes():
    rc, out, lst, od = run_main([plain("Dir\\A.txt", b"first"), plain("dir\\a.TXT", b"second")])
    lines = lst.splitlines()[4:]
    content = open(os.path.join(od, "Dir", "A.txt"), "rb").read()
    return (rc == 1 and b"FAILED" not in lines[0] and b"duplicate" in lines[1] and content == b"first"), \
        "second: %s; kept=%r" % (lines[1].decode(), content)
check("M2 duplicate normcase path fails only the duplicate", t_dupes)

# M3 / L3: bad entries fail alone; bad times are skipped not failed
def t_times():
    files = [plain("t1.txt", b"one", times=(1, 1, 1)),
             plain("t2.txt", b"two", times=(2**64 - 1,) * 3),
             plain("t3.txt", b"three")]
    rc, out, lst, od = run_main(files)
    lines = lst.splitlines()[4:]
    # FILETIME 1 is a valid 1601 date: it may be applied or skipped, never FAILED
    ok = (rc == 0 and b"FAILED" not in lst and lines[1].endswith(b"[ok (times skipped)]")
          and b"invalid" in lines[1] and os.path.getsize(os.path.join(od, "t1.txt")) == 3
          and os.path.getsize(os.path.join(od, "t2.txt")) == 3)
    return ok, " | ".join(l.decode() for l in lines)
check("M3/L3 FILETIME 1 and 2^64-1: extracted, times skipped", t_times)


def t_zero_lit():
    # dynamic block: cl code {0:len1, 18:len1}; 18x2 + 0 -> all zero literal lengths
    def body(w):
        # cl codes: 0 -> '0', 18 -> '1'
        put_code(w, 1, 1); w.put(127, 7)    # 138 zeros
        put_code(w, 1, 1); w.put(108, 7)    # 119 zeros -> 257 lit
        put_code(w, 0, 1)                   # dist length 0
    s = dyn_header([1, 0, 0, 1], body)
    err1, _ = run_inflate(s, 100, True)
    err2, _ = run_inflate(s, 100, False)
    rc, out, lst, _ = run_main([{"name": "z.bin", "stream": s, "usize": 5}, plain("ok.txt", b"fine")])
    lines = lst.splitlines()[4:]
    return (err1 and err2 and rc == 1 and b"FAILED" in lines[0] and b"FAILED" not in lines[1]), \
        "%s / %s" % (err1, lines[0].decode())
check("M3 all-zero literal table -> FormatError, entry-local", t_zero_lit)


def t_truncations():
    good = make_installer(os.path.join(BASE, "full.exe"), [plain("a.txt", b"abc"), plain("b.txt", b"defg")])
    blob = open(good, "rb").read()
    bad = []
    for cut in list(range(0, len(blob), 3)) + [len(blob) - 1]:
        p = os.path.join(BASE, "cut_%d.exe" % cut)
        open(p, "wb").write(blob[:cut])
        try:
            rc, out, _, _ = run_main(installer=p)
        except Exception as ex:
            bad.append("%d:%s" % (cut, type(ex).__name__))
            continue
        if rc not in (1, 2):
            bad.append("%d:rc%d" % (cut, rc))
    return not bad, "%d cut points, problems %r" % (len(range(0, len(blob), 3)) + 1, bad[:5])
check("M3 every truncation of a small installer fails cleanly", t_truncations)


def t_table_garbage():
    tbls = [b"\x01\x00", struct.pack("<I", 1) + b"\x30\x00" + b"\0" * 10,
            struct.pack("<I", 1) + struct.pack("<H", 0x24) + b"\0" * 0x14 + b"\x08" + b"\0" * 13,
            struct.pack("<I", 5) + entry_bytes("a", 0, 1, 1, 0)]
    bad = []
    for tb in tbls:
        rc, out, _, _ = run_main([plain("a.txt", b"x")], table=tb)
        if rc != 2 or "error:" not in out:
            bad.append(rc)
    return not bad, "%d malformed tables, problems %r" % (len(tbls), bad)
check("M3 malformed file tables -> clean error", t_table_garbage)

# L1: non-ASCII name and status strings
def t_ascii():
    rc, out, lst, od = run_main([plain("caf\u00e9\\na\u00efve.txt", b"x")])
    try:
        lst.decode("ascii")
        out.encode("ascii")
        ascii_ok = True
    except UnicodeError:
        ascii_ok = False
    exists = os.path.exists(os.path.join(od, "caf\u00e9", "na\u00efve.txt"))
    return rc == 0 and ascii_ok and exists, "listing/console ascii=%s, file written=%s" % (ascii_ok, exists)
check("L1 latin-1 names: ASCII listing and console", t_ascii)

# L4
def t_oversub():
    try:
        H.build_table([1, 1, 1])
        return False, "accepted"
    except H.FormatError as ex:
        s = dyn_header([1, 1, 1, 1])
        err, _ = run_inflate(s, 100, True)
        return err is not None and "over-subscribed" in err, "%s / stream: %s" % (ex, err)
check("L4 over-subscribed code refused", t_oversub)


def t_rep16():
    # cl lengths: 16 -> 1, 0 -> 1; canonical codes 0 -> '0', 16 -> '1'.
    # The first symbol sent is 16 (no previous length).
    def body(w):
        put_code(w, 1, 1); w.put(0, 2)
    s = dyn_header([0, 0, 1, 1], body)
    err1, _ = run_inflate(s, 100, True)
    err2, _ = run_inflate(s, 100, False)
    return bool(err1 and "previous" in err1 and err2 and "previous" in err2), "%s / %s" % (err1, err2)
check("L4 repeat code 16 with no previous length", t_rep16)

# sanity: a valid crafted installer round-trips through both decoders
def t_valid():
    payload = bytes(range(256)) * 40
    s = fixed_stream(payload[:3000])
    files = [plain("ok\\a.bin", payload), {"name": "ok\\b.bin", "stream": s, "usize": 3000, "payload": payload[:3000]}]
    r1 = run_main(files)
    r2 = run_main(files, args=["--pure-python"])
    return r1[0] == 0 and r2[0] == 0, "zlib rc=%d pure rc=%d" % (r1[0], r2[0])
check("sanity: valid crafted installer extracts in both modes", t_valid)

print("%d/%d passed; scratch run dir %s" % (sum(results), len(results), BASE))
sys.exit(0 if all(results) else 1)
