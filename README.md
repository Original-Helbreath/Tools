# Tools

Standalone tools for Original Helbreath: anything that runs on its own against game data, for any version.
Generators that rebuild a specific document live beside that document in
[2.20-Documentation](https://github.com/Original-Helbreath/2.20-Documentation) `tools/` instead.

## Contents

| Folder | Tool |
|---|---|
| `installer/` | `hb_installer_extract.py` - extracts the files from the official 2.02 installer `Hb202full_ENG.exe` (found on the 2001 retail CD, see [Downloads-Archive](https://github.com/Original-Helbreath/Downloads-Archive)) without running it. Standard library only. `test_robust.py` runs crafted-input tests against it. |

Planned (see the project board): the `.amd` map converter, and the repo staging and board-import scripts.

## Branches

| Branch | Purpose |
|---|---|
| `development` | Integration branch. Work happens in feature branches, which merge into `development`. |
| `stable` | Default branch. `development` is merged here once it is stable. |

There is no `original` branch: this is our own work.

## Project

Part of [Original Helbreath](https://github.com/Original-Helbreath): keeping the original 1999-2005 Siementech Helbreath alive.
Issues are tracked on the [project board](https://github.com/orgs/Original-Helbreath/projects/1).
