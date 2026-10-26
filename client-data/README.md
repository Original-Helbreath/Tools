# client-data

`convert_client_data.py` converts the Helbreath 2.20 client's original text data files into the JSON
files the client now reads. The JSON schemas are defined in 2.20-Client-Source `docs/data-formats.md`.
Python 3, standard library only; console output is ASCII.

## Usage

```
python convert_client_data.py INPUT_DIR OUTPUT_DIR [--encoding NAME] [--check]
```

`INPUT_DIR` is searched recursively. Each recognised file is written under `OUTPUT_DIR` at the same
relative path, with its extension replaced by `.json` (`CONTENTS/MAGICCFG.TXT` -> `CONTENTS/MAGICCFG.json`).
Other files are skipped. `--check` converts in memory and writes nothing. `--encoding` forces the
source encoding instead of detecting it. The exit code is 1 if any file failed.

| File | Format |
|---|---|
| `LOGIN.CFG` | `login-config` |
| `contents<N>.txt` | `contents` (text lines, plus a `shop` section for shop lists) |
| `GameMsgList.txt` | `game-messages` |
| `BItemcfg.txt` | `build-items` |
| `MAGICCFG.TXT` | `magic-config` |
| `SKILLCFG.TXT` | `skill-config` |
| `badword.txt` | `bad-words` (XOR decoded: 2.20 form with size header, or the older headerless form) |
| `Curse.txt` | `curse-words` (XOR decoded; the client does not read this format yet) |

`ItemName.cfg` is not converted: the client still reads it as text.

## How it stays faithful

The tool replays what the old client did with each file: MSVC text-mode reading (CR LF -> LF, Ctrl-Z
ends the file, NUL ends the buffer), an exact model of the `CStrTok` tokenizer (or C `strtok` for
`LOGIN.CFG`), and each loader's own parse (prefix tests, read modes, `atoi`, limits). The JSON holds the
parsed result. Files the client would have rejected are reported as errors; questionable input (for
example a `contents` file whose prose words start with `Item` and so parse as broken shop records, or
more lines than the client's array holds) is converted with a WARNING.

Strings are decoded from the file's encoding and written as UTF-8. The encoding is detected per file
(the first of `ascii`, `cp949`, `cp1252`, `latin-1` that decodes every string strictly and re-encodes it
to the same bytes) and recorded in the file's `encoding` key, so the client converts each string back
to the original bytes. Korean text in the shipped files is cp949.

## Tests

```
python -m unittest test_convert_client_data -v
```

Synthetic tests cover the tokenizer quirks, text mode, `atoi`, each format, cp949, both XOR forms and
error cases. Real-file tests round-trip files from a 2.20-Client-Files checkout (`HB_CLIENT_FILES`, or
the sibling folder `../../2.20-Client-Files` by default; skipped if absent): the JSON strings, encoded
back, must equal the model's tokens of the original, and the record counts must match counts taken
independently from the raw text. The whole tree must convert with no errors.
