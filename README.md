# Tools

Standalone tools for Original Helbreath: anything that runs on its own against game data, for any version.
Generators that rebuild a specific document live beside that document in
[2.20-Documentation](https://github.com/Original-Helbreath/2.20-Documentation) `tools/` instead.

## Contents

| Folder | Tool |
|---|---|
| `installer/` | `hb_installer_extract.py` - extracts the files from the official 2.02 installer `Hb202full_ENG.exe` (found on the 2001 retail CD, see [Downloads-Archive](https://github.com/Original-Helbreath/Downloads-Archive)) without running it. Standard library only. `test_robust.py` runs crafted-input tests against it. |
| `client-data/` | `convert_client_data.py` - converts the 2.20 client's original text data files (LOGIN.CFG, contents, shop lists, BItemcfg, magic/skill cfg, badword.txt, Curse.txt) into the JSON files the client now reads, replaying the old loaders exactly. Standard library only. `test_convert_client_data.py` holds its tests. |
| `pak/` | `pak_tool.py` - extracts a sprite `.pak` into `<name>.pak/<name>.pak` plus `<name>.pak/extracted/` (one PNG and one JSON of frames and pivots per sheet) and builds a pak back from that folder; an untouched folder rebuilds byte for byte, an edited PNG becomes a 24bpp BMP. Standard library only. `test_pak_tool.py` round-trips every 2.20 pak. |
| `deploy/` | `deploy_client.py` - lays out a playable client folder from a 2.20-Client-Files checkout, putting each `SPRITES/<name>.pak/<name>.pak` flat in `SPRITES/` and leaving out `extracted/`; `--build-paks` builds the paks from `extracted/` first. Standard library only. |
| `amd/` | `amd_tool.py` - converts a `.amd` map to JSON (header text, and tile sprite/frame, object sprite/frame and flag grids) and back, byte for byte. Standard library only. `test_amd_tool.py` round-trips every 2.20 map. |

Planned (see the project board): the repo staging and board-import scripts.

## Branches

| Branch | Purpose |
|---|---|
| `development` | Integration branch. Work happens in feature branches, which merge into `development`. |
| `stable` | Default branch. `development` is merged here once it is stable. |

There is no `original` branch: this is our own work.

## Project

Part of [Original Helbreath](https://github.com/Original-Helbreath): keeping the original 1999-2005 Siementech Helbreath alive.
Issues are tracked on the [project board](https://github.com/orgs/Original-Helbreath/projects/1).
