"""Lay out a playable client folder from a 2.20-Client-Files checkout. Standard library only.

2.20-Client-Files keeps each sprite pak in its own folder, SPRITES/<name>.pak/<name>.pak, with its extracted PNG and
JSON beside it in SPRITES/<name>.pak/extracted/. The client opens sprites\\<name>.pak as a file, so this copies
every file across unchanged except that each pak goes flat into SPRITES/ and the extracted/ folders are left out.
Git metadata and README.md are not copied.

    python deploy_client.py <Client-Files checkout> <install folder>
    python deploy_client.py <Client-Files checkout> <install folder> --build-paks

--build-paks builds each pak from its extracted/ folder with pak/pak_tool.py instead of copying the committed pak,
so edited PNGs or frame JSON take effect (untouched sheets keep their original bytes).

The install folder must not be inside the checkout. Existing files there are overwritten; nothing is deleted.
MAPDATA and the binaries come from 2.20-Map-Files and 2.20-Reference and are not handled here.
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pak"))
import pak_tool  # noqa: E402

SKIP_TOP = {".git", ".gitignore", ".gitattributes", "README.md"}


def deploy(src, dst, build_paks=False):
    src, dst = os.path.realpath(src), os.path.realpath(dst)  # realpath: junctions and symlinks cannot dodge the guard
    rs, rd = os.path.normcase(src), os.path.normcase(dst)
    common = os.path.commonpath([rs, rd]) if os.path.splitdrive(rs)[0] == os.path.splitdrive(rd)[0] else None
    if common in (rs, rd):
        raise ValueError("install folder and checkout must not contain each other")
    sprites = [d for d in os.listdir(src) if d.upper() == "SPRITES" and os.path.isdir(os.path.join(src, d))]
    if not sprites:
        raise ValueError(src + " has no SPRITES folder; is it a 2.20-Client-Files checkout?")
    for d in os.listdir(os.path.join(src, sprites[0])):  # check the layout before writing anything
        folder = os.path.join(src, sprites[0], d)
        if not os.path.isdir(folder):
            continue
        if not os.path.isfile(os.path.join(folder, d)):
            raise ValueError("%s has no %s inside it" % (folder, d))
        for extra in os.listdir(folder):
            if extra not in (d, "extracted"):
                print("note: %s is not deployed" % os.path.join(folder, extra), file=sys.stderr)
    files = paks = built = 0
    for base, dirs, names in os.walk(src):
        rel = os.path.relpath(base, src)
        parts = [] if rel == "." else rel.split(os.sep)
        if not parts:
            dirs[:] = [d for d in dirs if d not in SKIP_TOP]
            names = [n for n in names if n not in SKIP_TOP]
        if parts and parts[0].upper() == "SPRITES":
            if len(parts) == 1:
                for n in names:  # a loose file in SPRITES (not expected): copy as is
                    _copy(os.path.join(base, n), os.path.join(dst, rel, n))
                    files += 1
                continue
            # parts == [SPRITES, <name>.pak]: the pak folder
            dirs[:] = []
            name = parts[1]
            target = os.path.join(dst, parts[0], name)
            pak = os.path.join(base, name)
            if not os.path.isfile(pak):
                raise ValueError("%s has no %s inside it" % (base, name))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if build_paks and os.path.isdir(os.path.join(base, "extracted")):
                pak_tool.build(base, target)
                built += 1
            else:
                if build_paks:
                    print("note: %s has no extracted/; copied as committed" % name, file=sys.stderr)
                shutil.copyfile(pak, target)
            paks += 1
            continue
        for n in names:
            _copy(os.path.join(base, n), os.path.join(dst, rel, n))
            files += 1
    return files, paks, built


def _copy(a, b):
    os.makedirs(os.path.dirname(b), exist_ok=True)
    shutil.copyfile(a, b)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lay out a playable client folder from 2.20-Client-Files.")
    ap.add_argument("client_files")
    ap.add_argument("install_dir")
    ap.add_argument("--build-paks", action="store_true", help="build paks from extracted/ instead of copying")
    a = ap.parse_args(argv)
    try:
        files, paks, built = deploy(a.client_files, a.install_dir, a.build_paks)
    except (ValueError, OSError, pak_tool.PakError) as e:
        print("error: %s (the install folder may be partly written)" % e, file=sys.stderr)
        return 1
    print("copied %d files and %d paks (%d built from extracted/) to %s" % (files, paks, built, a.install_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
