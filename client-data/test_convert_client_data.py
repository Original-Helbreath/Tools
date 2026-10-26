"""Tests for convert_client_data.py.

Real-file tests read the original client files from 2.20-Client-Files (branch `original` or
`development`). Point HB_CLIENT_FILES at that checkout; by default the sibling folder
../../2.20-Client-Files is used. They are skipped when no checkout is found.

    python -m unittest test_convert_client_data -v
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import convert_client_data as c  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_FILES = os.environ.get("HB_CLIENT_FILES",
                              os.path.normpath(os.path.join(HERE, "..", "..", "2.20-Client-Files")))


def real(rel):
    return os.path.join(CLIENT_FILES, rel)


def have_real(rel):
    return os.path.isfile(real(rel))


def read(rel):
    with open(real(rel), "rb") as f:
        return f.read()


def roundtrip(doc):
    """Serialise and parse back, as a consumer of the JSON file would."""
    return json.loads(c.dump_json(doc).decode("utf-8"))


class TokenizerModel(unittest.TestCase):
    def test_cstrtok_quirks(self):
        self.assertEqual(c.cstrtok(b"a b", b" "), [b"a", b"b"])
        self.assertEqual(c.cstrtok(b" a", b" "), [b"", b"a"])          # leading separator
        self.assertEqual(c.cstrtok(b"a ", b" "), [b"a"])               # one trailing
        self.assertEqual(c.cstrtok(b"a  ", b" "), [b"a", b""])         # two trailing
        # The CR of CR LF always separates; the LF only when it is in seps.
        self.assertEqual(c.cstrtok(b"a\r\nb", b" "), [b"a", b"\nb"])
        self.assertEqual(c.cstrtok(b"a\r\nb", b"\n"), [b"a", b"b"])
        self.assertEqual(c.cstrtok(b"a\rb", b" "), [b"a\rb"])          # lone CR does not
        self.assertEqual(c.cstrtok(b"a\0b", b" "), [b"a"])             # C string ends at NUL
        self.assertEqual(c.cstrtok(b"", b" "), [])

    def test_text_mode(self):
        self.assertEqual(c.msvc_text_read(b"a\r\nb\rc\x1ad"), b"a\nb\rc")

    def test_atoi(self):
        self.assertEqual([c.atoi(t) for t in (b"12", b" -3x", b"x", b"+7", b"")], [12, -3, 0, 7, 0])

    def test_c_strtok(self):
        self.assertEqual(c.c_strtok(b"  a==b ", b"= "), [b"a", b"b"])


class Synthetic(unittest.TestCase):
    def test_contents_text_and_shop(self):
        raw = (b";-Name-Type\r\nItem = Map 9 0 10 1 0 0 0 0 0 35 0 6 9 30 1 -1 0 0\r\n"
               b"\r\nhello\r\n")
        fmt, doc, _ = c.convert_bytes("contents7.txt", raw)
        doc = roundtrip(doc)
        self.assertEqual(fmt, "contents")
        self.assertEqual(doc["lines"][0], ";-Name-Type")
        self.assertEqual(doc["lines"][-1], "hello")
        item = doc["shop"]["items"][0]
        self.assertEqual((item["name"], item["item_type"], item["price"], item["level_limit"]),
                         ("Map", 9, 30, 0))

    def test_prose_is_not_a_shop(self):
        fmt, doc, warnings = c.convert_bytes("contents8.txt", b"Items are sold here.\r\n")
        self.assertNotIn("shop", doc)
        self.assertTrue(warnings)

    def test_cp949(self):
        name = "고화력탄".encode("cp949")
        raw = b"BuildItem = " + name + b" 20 a 1 b 1 c 1 xxx 0 xxx 0 xxx 0 6 88 30\r\n"
        fmt, doc, _ = c.convert_bytes("BItemcfg.txt", raw)
        doc = roundtrip(doc)
        self.assertEqual(doc["encoding"], "cp949")
        self.assertEqual(doc["items"][0]["name"].encode("cp949"), name)
        self.assertEqual(len(doc["items"][0]["elements"]), 6)

    def test_magic_rejects_duplicates(self):
        raw = b"magic = 1 A 1 2 3 4 5 6 1\nmagic = 1 B 1 2 3 4 5 6 0\n"
        with self.assertRaises(c.ConvertError):
            c.convert_bytes("MAGICCFG.TXT", raw)

    def test_login(self):
        fmt, doc, _ = c.convert_bytes("LOGIN.CFG", b"[CONFIG]\r\n\r\nlog-server-address = 1.2.3.4\r\n"
                                                    b"log-server-port    = 2848\r\n")
        self.assertEqual((doc["log_server_address"], doc["log_server_port"]), ("1.2.3.4", 2848))

    def test_badword_xor_header_form(self):
        plain = "남자\n사타\nfoo\n".encode("cp949")
        fmt, doc, warnings = c.convert_bytes("badword.txt", c.xor_encode(plain))
        self.assertEqual(roundtrip(doc)["words"], ["남자", "사타", "foo"])
        self.assertEqual(doc["source"]["xor"], "header")
        self.assertFalse(warnings)

    def test_curse(self):
        plain = b"; comment\n$skip\nbad\tgood\nworse\n"
        fmt, doc, _ = c.convert_bytes("Curse.txt", c.xor_encode(plain))
        self.assertEqual(doc["lines"], ["bad\tgood", "worse"])

    def test_unknown_file(self):
        self.assertIsNone(c.classify("ItemName.cfg"))


@unittest.skipUnless(os.path.isdir(CLIENT_FILES), "2.20-Client-Files checkout not found")
class RealFiles(unittest.TestCase):
    """Round-trip real files: JSON -> original-encoding bytes must equal the model's parse of the
    original text file, and counts must match independent counts taken from the raw text."""

    def check_strings(self, doc, key, expected):
        got = [s.encode(doc["encoding"]) for s in roundtrip(doc)[key]]
        self.assertEqual(got, expected)

    @unittest.skipUnless(have_real("CONTENTS/contents10.txt"), "file missing")
    def test_contents10(self):
        raw = read("CONTENTS/contents10.txt")
        _, doc, _ = c.convert_bytes("contents10.txt", raw)
        self.check_strings(doc, "lines", c.cstrtok(c.msvc_text_read(raw), b"\n"))
        # Independent count: non-empty lines (CStrTok drops empty ones; the file has no leading
        # empty line).
        self.assertEqual(len(doc["lines"]), len([l for l in raw.split(b"\r\n") if l]))

    @unittest.skipUnless(have_real("CONTENTS/contents1.txt"), "file missing")
    def test_shop_contents1(self):
        raw = read("CONTENTS/contents1.txt")
        _, doc, _ = c.convert_bytes("contents1.txt", raw)
        n_item_lines = len([l for l in raw.split(b"\r\n") if l.startswith(b"Item")])
        self.assertEqual(len(doc["shop"]["items"]), n_item_lines)

    @unittest.skipUnless(have_real("CONTENTS/GameMsgList.txt"), "file missing")
    def test_game_messages(self):
        raw = read("CONTENTS/GameMsgList.txt")
        _, doc, _ = c.convert_bytes("GameMsgList.txt", raw)
        self.check_strings(doc, "messages", c.cstrtok(c.msvc_text_read(raw), b";\n"))
        self.assertGreater(len(doc["messages"]), 0)

    @unittest.skipUnless(have_real("CONTENTS/MAGICCFG.TXT"), "file missing")
    def test_magic(self):
        raw = read("CONTENTS/MAGICCFG.TXT")
        _, doc, _ = c.convert_bytes("MAGICCFG.TXT", raw)
        n = len([l for l in c.msvc_text_read(raw).split(b"\n") if l.startswith(b"magic")])
        self.assertEqual(len(doc["magic"]), n)
        self.assertEqual(len({m["id"] for m in doc["magic"]}), n)

    @unittest.skipUnless(have_real("CONTENTS/SKILLCFG.TXT"), "file missing")
    def test_skill(self):
        raw = read("CONTENTS/SKILLCFG.TXT")
        _, doc, _ = c.convert_bytes("SKILLCFG.TXT", raw)
        n = len([l for l in c.msvc_text_read(raw).split(b"\n") if l.startswith(b"skill")])
        self.assertEqual(len(doc["skills"]), n)

    @unittest.skipUnless(have_real("CONTENTS/BItemcfg.txt"), "file missing")
    def test_build_items(self):
        raw = read("CONTENTS/BItemcfg.txt")
        _, doc, _ = c.convert_bytes("BItemcfg.txt", raw)
        doc = roundtrip(doc)
        n = len([l for l in c.msvc_text_read(raw).split(b"\n") if l.startswith(b"BuildItem")])
        self.assertEqual(len(doc["items"]), n)
        items, _ = c.parse_build_items(c.msvc_text_read(raw))
        self.assertEqual([i["name"].encode("cp949") for i in doc["items"]], [i["name"] for i in items])

    @unittest.skipUnless(have_real("CONTENTS/badword.txt"), "file missing")
    def test_badword(self):
        raw = read("CONTENTS/badword.txt")
        _, doc, _ = c.convert_bytes("badword.txt", raw)
        plain = bytes(b ^ 35 for b in raw).split(b"\0")[0]
        self.assertEqual(len(doc["words"]), min(len([w for w in plain.split(b"\n") if w]), 500))

    def test_whole_tree(self):
        converted, skipped, errors = c.convert_tree(CLIENT_FILES, None, check=True, log=lambda m: None)
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(converted), 50)


if __name__ == "__main__":
    unittest.main()
