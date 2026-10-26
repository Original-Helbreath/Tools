#!/usr/bin/env python
"""Convert Helbreath 2.20 client text data files to the client's JSON formats.

The 2.20 client used to tokenise its text data files with the CStrTok class. The client now reads
JSON instead (schemas: 2.20-Client-Source docs/data-formats.md). This tool produces those JSON
files from the original text files, so that the client ends up with exactly the in-memory data it
used to build from the text files:

  * each file is read the way the client read it (MSVC fopen "rt": CR LF -> LF, Ctrl-Z ends the
    file, the buffer is a C string so a NUL ends it);
  * it is tokenised with an exact model of CStrTok (or of C strtok for LOGIN.CFG);
  * the client's own parse is replayed (prefix tests, read modes, atoi, limits, failure cases);
  * the parsed result is written as JSON. Strings are decoded from the file's original encoding
    (detected: ascii, cp949, cp1252, latin-1; or forced with --encoding) and written as UTF-8; the
    encoding is recorded so the client can convert the strings back to the original bytes.

Files the original client would have rejected (a loader returning FALSE) are reported as errors
and not converted.

Usage:
    python convert_client_data.py INPUT_DIR OUTPUT_DIR [--encoding NAME] [--check]

INPUT_DIR is searched recursively; each recognised file is written to the same relative folder
under OUTPUT_DIR with its extension replaced by ".json". Unrecognised files are skipped.
--check converts in memory only and reports, writing nothing.

Standard library only. Console output is ASCII.
"""

import argparse
import hashlib
import json
import os
import re
import sys

FORMAT_VERSION = 1

# Encodings tried, in order, when none is forced. Each must decode every string strictly and
# re-encode it to the identical bytes.
DETECT_ORDER = ("ascii", "cp949", "cp1252", "latin-1")

# Windows code page the client uses to turn each recorded encoding back into bytes.
CODE_PAGES = {"ascii": 20127, "cp949": 949, "cp1252": 1252, "latin-1": 28591, "cp932": 932}

# Client limits (Client/Game.h, Client/GameMonitor.h).
DEF_MAXMENUITEMS = 140
DEF_TEXTDLGMAXLINES = 300
DEF_MAXGAMEMSGS = 300
DEF_MAXBUILDITEMS = 100
DEF_MAXMAGICTYPE = 100
DEF_MAXSKILLTYPE = 60
DEF_MAXBADWORD = 500
MAX_CURSE_STRING = 7000

# Sizes of the client's fixed char arrays (including the terminating NUL).
ITEM_NAME_SIZE = 21      # CItem::m_cName
BUILD_NAME_SIZE = 21     # CBuildItem::m_cName, m_cElementName1..6
MAGIC_NAME_SIZE = 31     # CMagic::m_cName
SKILL_NAME_SIZE = 21     # CSkill::m_cName
LOGIN_ADDR_MAX = 15      # bReadLoginConfigFile rejects longer addresses

XOR_DATA_KEY = 35        # CMisc::_iConvertFileXor key used for badword.txt and Curse.txt
XOR_HEADER_KEY = 20


class ConvertError(Exception):
    """The file is not one the original client would have accepted."""


# ---------------------------------------------------------------------------------------------
# Models of the C runtime and of CStrTok
# ---------------------------------------------------------------------------------------------

def msvc_text_read(raw):
    """Bytes as fread() returns them from a file opened with MSVC fopen(..., "rt")."""
    end = raw.find(b"\x1a")
    if end >= 0:
        raw = raw[:end]
    return raw.replace(b"\r\n", b"\n")


def msvc_text_write(data):
    """Bytes a file holds after fwrite() of data to a file opened with MSVC fopen(..., "wt")."""
    return data.replace(b"\n", b"\r\n")


def c_string(buf):
    """The buffer as the C string the client passes on: it ends at the first NUL."""
    end = buf.find(b"\0")
    return buf if end < 0 else buf[:end]


def cstrtok(data, seps):
    """All tokens CStrTok(data, seps) returns via pGet(), in order (exact model of the class).

    Rules: a character is a separator if it is NUL, '\\r' followed by '\\n', or in seps. A token is
    returned as soon as a separator is met, even when empty; after it, the run of separators is
    skipped, but the skip stops once the position reaches the last character. NULL (the end of
    the list) is returned only when the input is used up with nothing collected.
    """
    data = c_string(data)
    n = len(data)
    sepset = set(seps)
    pos = 0
    out = []

    def nxt(p):
        return data[p + 1] if p <= n - 2 else 0

    def is_sep(p):
        c = data[p]
        if c == 0:
            return True
        if c == 0x0D and nxt(p) == 0x0A:
            return True
        return c in sepset

    while True:
        tok = bytearray()
        returned = False
        while pos < n:
            if not is_sep(pos):
                tok.append(data[pos])
                pos += 1
                continue
            done = False
            while not done:
                if is_sep(pos):
                    pos += 1
                else:
                    done = True
                if pos >= n - 1:
                    done = True
            out.append(bytes(tok))
            returned = True
            break
        if returned:
            continue
        if not tok:
            return out
        out.append(bytes(tok))


def c_strtok(data, seps):
    """All tokens of the C strtok(data, seps) loop (used by bReadLoginConfigFile)."""
    data = c_string(data)
    out = []
    tok = bytearray()
    for c in data:
        if c in seps:
            if tok:
                out.append(bytes(tok))
                tok = bytearray()
        else:
            tok.append(c)
    if tok:
        out.append(bytes(tok))
    return out


def atoi(tok):
    """C atoi() on a byte string (32-bit int result)."""
    i = 0
    n = len(tok)
    while i < n and tok[i] in b" \t\n\v\f\r":
        i += 1
    neg = False
    if i < n and tok[i] in b"+-":
        neg = tok[i] == 0x2D
        i += 1
    v = 0
    while i < n and 0x30 <= tok[i] <= 0x39:
        v = v * 10 + (tok[i] - 0x30)
        i += 1
    if neg:
        v = -v
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v >= 0x80000000 else v


def memcmp_prefix(tok, prefix):
    """memcmp(token, prefix, len(prefix)) == 0, where token is a NUL-terminated string."""
    return tok.startswith(prefix)


def is_number(tok):
    """CGame::_bGetIsStringIsNumber: every character is '-' or a digit (empty passes)."""
    return all(c == 0x2D or 0x30 <= c <= 0x39 for c in tok)


def is_clean_int(tok):
    return re.fullmatch(rb"[+-]?[0-9]+", tok) is not None


# ---------------------------------------------------------------------------------------------
# Replays of the client's parsers (each returns plain Python data with byte strings)
# ---------------------------------------------------------------------------------------------

def parse_contents_lines(buf):
    """_LoadTextDlgContents / _iLoadTextDlgContents2 / _LoadAgreementTextContents: seps "\\n"."""
    return cstrtok(buf, b"\n")


SHOP_FIELDS = ("item_type", "equip_pos", "effect_type", "effect_value1", "effect_value2",
               "effect_value3", "effect_value4", "effect_value5", "effect_value6",
               "max_life_span", "max_fix_count", "sprite", "sprite_frame", "price", "weight",
               "appr_value", "speed", "level_limit")


def parse_shop(buf):
    """__bDecodeContentsAndBuildItemForSaleList. Returns (items, ok, started) where started says
    whether any item record was opened at all."""
    items = []
    mode_a = mode_b = 0
    cur = None
    started = False
    for tok in cstrtok(buf, b"= ,\t\n"):
        if mode_a != 0:
            if mode_b == 1:
                cur = {"name": tok}
                mode_b = 2
            else:
                cur[SHOP_FIELDS[mode_b - 2]] = tok
                if mode_b == 19:
                    items.append(cur)
                    cur = None
                    mode_a = mode_b = 0
                else:
                    mode_b += 1
        elif memcmp_prefix(tok, b"Item"):
            started = True
            if len(items) >= DEF_MAXMENUITEMS:
                return items, False, started
            mode_a = mode_b = 1
    return items, (mode_a == 0 and mode_b == 0), started


def parse_game_messages(buf):
    """_LoadGameMsgTextContents: seps ";\\n"."""
    return cstrtok(buf, b";\n")


def parse_build_items(buf):
    """__bDecodeBuildItemContents."""
    items = []
    mode_a = mode_b = 0
    cur = None
    for tok in cstrtok(buf, b"= ,\t\n"):
        if mode_a != 0:
            if mode_b == 1:
                cur = {"name": tok, "elements": []}
            elif mode_b == 2:
                cur["skill_limit"] = tok
            elif 3 <= mode_b <= 14:
                if mode_b % 2 == 1:
                    cur["elements"].append({"name": tok})
                else:
                    cur["elements"][-1]["count"] = tok
            elif mode_b == 15:
                cur["sprite"] = tok
            elif mode_b == 16:
                cur["sprite_frame"] = tok
            elif mode_b == 17:
                cur["max_skill"] = tok
                items.append(cur)
                cur = None
                mode_a = mode_b = 0
                continue
            mode_b += 1
        elif memcmp_prefix(tok, b"BuildItem"):
            mode_a = mode_b = 1
    return items, (mode_a == 0 and mode_b == 0)


MAGIC_FIELDS = (None, "name", "value1", "value2", "value3", "value4", "value5", "value6",
                "visible")


def parse_magic(buf):
    """bInitMagicCfgList. Raises ConvertError where the client returned FALSE."""
    entries = []
    used = set()
    mode_a = mode_b = 0
    cur = None
    for tok in cstrtok(buf, b"= ,\t\n"):
        if mode_a != 0:
            if mode_b == 1:
                if not is_number(tok):
                    raise ConvertError("magic number %r is not a number" % tok)
                idx = atoi(tok)
                if idx in used:
                    raise ConvertError("magic number %d defined twice" % idx)
                if not 0 <= idx < DEF_MAXMAGICTYPE:
                    raise ConvertError("magic number %d is outside 0..%d" % (idx, DEF_MAXMAGICTYPE - 1))
                used.add(idx)
                cur = {"id": idx}
            else:
                if mode_b in (3, 4, 5, 9) and not is_number(tok):
                    raise ConvertError("magic %d field %s %r is not a number"
                                       % (cur["id"], MAGIC_FIELDS[mode_b - 1], tok))
                cur[MAGIC_FIELDS[mode_b - 1]] = tok
            if mode_b == 9:
                entries.append(cur)
                cur = None
                mode_a = mode_b = 0
            else:
                mode_b += 1
        elif memcmp_prefix(tok, b"magic"):
            mode_a = mode_b = 1
    if mode_a or mode_b:
        raise ConvertError("file ends inside a magic record")
    return entries


def parse_skill(buf):
    """bInitSkillCfgList. Raises ConvertError where the client returned FALSE."""
    entries = []
    used = set()
    mode_a = mode_b = 0
    cur = None
    names = (None, "name", "useable", "use_method")
    for tok in cstrtok(buf, b"= ,\t\n"):
        if mode_a != 0:
            if mode_b == 1:
                if not is_number(tok):
                    raise ConvertError("skill number %r is not a number" % tok)
                idx = atoi(tok)
                if idx in used:
                    raise ConvertError("skill number %d defined twice" % idx)
                if not 0 <= idx < DEF_MAXSKILLTYPE:
                    raise ConvertError("skill number %d is outside 0..%d" % (idx, DEF_MAXSKILLTYPE - 1))
                used.add(idx)
                cur = {"id": idx}
            else:
                if mode_b in (3, 4) and not is_number(tok):
                    raise ConvertError("skill %d field %s %r is not a number"
                                       % (cur["id"], names[mode_b - 1], tok))
                cur[names[mode_b - 1]] = tok
            if mode_b == 4:
                entries.append(cur)
                cur = None
                mode_a = mode_b = 0
            else:
                mode_b += 1
        elif memcmp_prefix(tok, b"skill"):
            mode_a = mode_b = 1
    if mode_a or mode_b:
        raise ConvertError("file ends inside a skill record")
    return entries


def parse_login(buf):
    """bReadLoginConfigFile (the file branch). Returns dict of the values it assigns."""
    out = {}
    mode = 0
    for tok in c_strtok(buf, b"= ,\t\n"):
        if mode == 1:
            if len(tok) > LOGIN_ADDR_MAX:
                raise ConvertError("log-server-address longer than %d characters" % LOGIN_ADDR_MAX)
            out["log_server_address"] = tok
            mode = 0
        elif mode == 2:
            port = atoi(tok)
            if port == 0:
                raise ConvertError("log-server-port is 0 or not a number")
            out["log_server_port"] = port
            mode = 0
        else:
            if memcmp_prefix(tok, b"log-server-address"):
                mode = 1
            if memcmp_prefix(tok, b"log-server-port"):
                mode = 2
    return out


def xor_decode(raw):
    """CMisc::_iConvertFileXor followed by writing the .tmp file ("wt") and the reader reading it
    back ("rt"). Returns the bytes the reader's buffer holds. Raises ConvertError where the client
    showed its "contains wrong information" error."""
    text = msvc_text_read(raw)
    size = len(raw) - 10                  # GetFileSize - 10: the on-disk size, not the text size
    header = bytearray(text[:10])
    header += b"\0" * (10 - len(header))
    body = bytearray(text[10:10 + max(size, 0)])
    body += b"\0" * (max(size, 0) - len(body))   # fread leaves the zeroed tail
    plain = bytes(c ^ XOR_DATA_KEY for c in body)
    hdr = bytearray()
    for c in header:
        if c == 0:
            break
        hdr.append(c ^ XOR_HEADER_KEY)
    if atoi(bytes(hdr)) != size:
        raise ConvertError("XOR header says %d bytes, file has %d" % (atoi(bytes(hdr)), size))
    return msvc_text_read(msvc_text_write(plain))


def xor_decode_headerless(raw):
    """The older (2.02 retail) obfuscation: the whole file XORed with the same key and no size
    header. The 2.20 client's _iConvertFileXor rejects such a file (its header check fails)."""
    return msvc_text_read(bytes(c ^ XOR_DATA_KEY for c in raw))


def xor_encode(plain):
    """Inverse of xor_decode for text without CR, LF-only lines (used by the tests)."""
    body = bytes(c ^ XOR_DATA_KEY for c in plain)
    header = str(len(body)).encode("ascii")
    header = bytes(c ^ XOR_HEADER_KEY for c in header).ljust(10, b"\0")
    return header + body


def parse_badwords(buf):
    """CGameMonitor::iReadBadWordFileList: seps "/,\\t\\n", at most DEF_MAXBADWORD entries."""
    return cstrtok(buf, b"/,\t\n")[:DEF_MAXBADWORD]


def parse_curse(buf):
    """CCurse::LoadCurse line selection: fgets() lines of up to 127 bytes, skipping lines that
    start with '$' or ';' or are empty. Returns the kept lines without their trailing LF."""
    lines = []
    total = 0
    pos = 0
    data = c_string(buf)
    while pos < len(data):
        end = data.find(b"\n", pos)
        chunk_end = len(data) if end < 0 else end + 1
        chunk_end = min(chunk_end, pos + 127)
        chunk = data[pos:chunk_end]
        pos = chunk_end
        if chunk[:1] in (b"$", b";", b"\n"):
            continue
        lines.append(chunk)
        total += len(chunk)
        if total >= MAX_CURSE_STRING:
            break
    return lines


# ---------------------------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------------------------

def _strings_of(obj):
    if isinstance(obj, bytes):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            for s in _strings_of(v):
                yield s
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            for s in _strings_of(v):
                yield s


def _round_trips(b, enc):
    try:
        return b.decode(enc).encode(enc) == b
    except UnicodeError:
        return False


def choose_encoding(text, parsed, forced=None):
    """The encoding whose strict decode/encode round-trips the whole text and every string."""
    candidates = (forced,) if forced else DETECT_ORDER
    for enc in candidates:
        if _round_trips(text, enc) and all(_round_trips(s, enc) for s in _strings_of(parsed)):
            return enc
    raise ConvertError("no encoding in %s round-trips this file" % (candidates,))


def to_json_value(obj, enc, int_fields=()):
    """Byte strings -> str; fields named in int_fields -> atoi() of their token."""
    if isinstance(obj, bytes):
        return obj.decode(enc)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in int_fields and isinstance(v, bytes):
                out[k] = atoi(v)
            else:
                out[k] = to_json_value(v, enc, int_fields)
        return out
    if isinstance(obj, list):
        return [to_json_value(v, enc, int_fields) for v in obj]
    return obj


# ---------------------------------------------------------------------------------------------
# File types
# ---------------------------------------------------------------------------------------------

def classify(filename):
    """Format name for a client data file name, or None when the file is not converted."""
    low = filename.lower()
    if low == "login.cfg":
        return "login-config"
    if re.fullmatch(r"contents\d+\.txt", low):
        return "contents"
    if low == "gamemsglist.txt":
        return "game-messages"
    if low == "bitemcfg.txt":
        return "build-items"
    if low == "magiccfg.txt":
        return "magic-config"
    if low == "skillcfg.txt":
        return "skill-config"
    if low == "badword.txt":
        return "bad-words"
    if low == "curse.txt":
        return "curse-words"
    return None


def _check_len(name, value, size, what):
    if len(value) > size - 1:
        raise ConvertError("%s %r is %d bytes; the client's field holds %d"
                           % (what, value, len(value), size - 1))


def _warn_ints(warnings, records, fields, what):
    for r in records:
        for f in fields:
            v = r.get(f)
            if isinstance(v, bytes) and not is_clean_int(v):
                warnings.append("%s %r field %s token %r is not an integer; stored as atoi() = %d"
                                % (what, r.get("name", r.get("id")), f, v, atoi(v)))


def convert_bytes(filename, raw, forced_encoding=None):
    """Convert one file's raw bytes. Returns (format, json_document, warnings)."""
    fmt = classify(filename)
    if fmt is None:
        raise ConvertError("not a converted file type")
    warnings = []
    xor = None

    if fmt in ("bad-words", "curse-words"):
        try:
            text = xor_decode(raw)
            xor = "header"
        except ConvertError as e:
            text = xor_decode_headerless(raw)
            xor = "headerless"
            warnings.append("%s; decoded as the headerless (2.02) XOR form, which the 2.20 "
                            "client itself would have rejected" % e)
    else:
        text = msvc_text_read(raw)

    int_fields = ()
    if fmt == "contents":
        lines = parse_contents_lines(text)
        if len(lines) > DEF_TEXTDLGMAXLINES:
            warnings.append("%d lines; the client keeps the first %d (the old loader wrote past "
                            "its array)" % (len(lines), DEF_TEXTDLGMAXLINES))
        body = {"lines": lines}
        items, ok, started = parse_shop(text)
        if started:
            clean = ok and all(is_clean_int(it[f]) for it in items for f in SHOP_FIELDS)
            if clean:
                for it in items:
                    _check_len("name", it["name"], ITEM_NAME_SIZE, "shop item name")
                body["shop"] = {"items": items}
            else:
                warnings.append("words starting with 'Item' parse as malformed shop records; "
                                "treated as a text file (no shop section)")
        int_fields = SHOP_FIELDS
    elif fmt == "game-messages":
        msgs = parse_game_messages(text)
        if len(msgs) > DEF_MAXGAMEMSGS:
            raise ConvertError("%d messages; the client holds %d" % (len(msgs), DEF_MAXGAMEMSGS))
        body = {"messages": msgs}
    elif fmt == "build-items":
        items, ok = parse_build_items(text)
        if not ok:
            raise ConvertError("file ends inside a BuildItem record")
        if len(items) > DEF_MAXBUILDITEMS:
            raise ConvertError("%d build items; the client holds %d" % (len(items), DEF_MAXBUILDITEMS))
        for it in items:
            _check_len("name", it["name"], BUILD_NAME_SIZE, "build item name")
            for el in it["elements"]:
                _check_len("name", el["name"], BUILD_NAME_SIZE, "build element name")
        _warn_ints(warnings, items, ("skill_limit", "sprite", "sprite_frame", "max_skill"), "build item")
        for it in items:
            _warn_ints(warnings, it["elements"], ("count",), "build item %r element" % it["name"])
        int_fields = ("skill_limit", "sprite", "sprite_frame", "max_skill", "count")
        body = {"items": items}
    elif fmt == "magic-config":
        entries = parse_magic(text)
        for e in entries:
            _check_len("name", e["name"], MAGIC_NAME_SIZE, "magic name")
        _warn_ints(warnings, entries, ("value4", "value5", "value6"), "magic")
        int_fields = ("value1", "value2", "value3", "value4", "value5", "value6")
        for e in entries:
            e["visible"] = atoi(e["visible"]) != 0
        body = {"magic": entries}
    elif fmt == "skill-config":
        entries = parse_skill(text)
        for e in entries:
            _check_len("name", e["name"], SKILL_NAME_SIZE, "skill name")
        int_fields = ("useable", "use_method")
        body = {"skills": entries}
    elif fmt == "bad-words":
        body = {"words": parse_badwords(text)}
    elif fmt == "curse-words":
        body = {"lines": [l[:-1] if l.endswith(b"\n") else l for l in parse_curse(text)]}
    elif fmt == "login-config":
        body = parse_login(text)
    else:  # pragma: no cover
        raise AssertionError(fmt)

    enc = choose_encoding(text, body, forced_encoding)
    doc = {
        "format": fmt,
        "version": FORMAT_VERSION,
        "encoding": enc,
        "source": {
            "file": filename,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
    }
    if xor:
        doc["source"]["xor"] = xor
    doc.update(to_json_value(body, enc, int_fields))
    return fmt, doc, warnings


def dump_json(doc):
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def output_name(filename):
    return os.path.splitext(filename)[0] + ".json"


def convert_tree(in_dir, out_dir, forced_encoding=None, check=False, log=print):
    """Convert every recognised file under in_dir. Returns (converted, skipped, errors) lists."""
    converted, skipped, errors = [], [], []
    for root, dirs, files in os.walk(in_dir):
        dirs.sort()
        for name in sorted(files):
            src = os.path.join(root, name)
            rel = os.path.relpath(src, in_dir)
            if classify(name) is None:
                skipped.append(rel)
                continue
            with open(src, "rb") as f:
                raw = f.read()
            try:
                fmt, doc, warnings = convert_bytes(name, raw, forced_encoding)
            except ConvertError as e:
                errors.append((rel, str(e)))
                log("ERROR   %s: %s" % (rel, e))
                continue
            for w in warnings:
                log("WARNING %s: %s" % (rel, w))
            dst_rel = os.path.join(os.path.dirname(rel), output_name(name))
            if not check:
                dst = os.path.join(out_dir, dst_rel)
                os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(dump_json(doc))
            converted.append((rel, dst_rel, fmt, doc["encoding"]))
            log("OK      %s -> %s (%s, %s)" % (rel, dst_rel, fmt, doc["encoding"]))
    return converted, skipped, errors


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("input_dir")
    ap.add_argument("output_dir")
    ap.add_argument("--encoding", choices=sorted(CODE_PAGES), help="force the source encoding")
    ap.add_argument("--check", action="store_true", help="convert in memory only; write nothing")
    args = ap.parse_args(argv)

    def log(msg):
        print(msg.encode("ascii", "backslashreplace").decode("ascii"))

    converted, skipped, errors = convert_tree(args.input_dir, args.output_dir, args.encoding,
                                              args.check, log)
    log("converted %d, skipped %d, errors %d" % (len(converted), len(skipped), len(errors)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
