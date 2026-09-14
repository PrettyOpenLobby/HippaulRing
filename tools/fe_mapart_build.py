#!/usr/bin/env python3
"""fe_mapart_build.py -- pull the continent-map art out of a Fantasy Earth
install into services/fedata/mapart/, for the live dominion map (femap.py).

Everything here is SE's own art from `DATA/Window/mapselect.tex` (an IMG0
container of raw TGAs, see tools/fedatagen/fetex.py):

    world.png        MAP_BG0 cropped to the 800x600 the client lays out in --
                     the parchment world map with all six continents on it
    continent1..6    MAP1..MAP6, 512x512, the continent close-ups the
                     "choose a field" screen draws (field map_x/map_y are in
                     this image's pixel space)
    mark.png         `Mark`: circle / shield, large and small (the field dot;
                     the shield is a CAPITAL)
    mark2.png        `Mark2`: the population silhouettes and the crown

Run on a machine with a client install (the server host needs none), then
commit the PNGs:

    python tools/fe_mapart_build.py --install DIR

Uses the ORIGINAL texture (`mapselect.tex.orig` when present): a patched
install may carry an English repaint of the Mark2 caption row, which the map
does not need either way.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, os.pardir, "services", "fedata", "mapart")
FETEX = os.path.join(HERE, "fedatagen", "fetex.py")
#: Default install dir: the current directory (it must hold DATA/).
INSTALL = "."


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--install", default=INSTALL,
                    help="your FantasyEarth client install directory "
                         "(holds DATA/); default: the current directory")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args(argv)
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("needs Pillow (pip install pillow)")
    win = os.path.join(a.install, "DATA", "Window")
    if not os.path.isdir(win):
        win = a.install          # flat disc/dump layout
    src = os.path.join(win, "mapselect.tex.orig")
    if not os.path.isfile(src):
        src = os.path.join(win, "mapselect.tex")
    if not os.path.isfile(src):
        raise SystemExit("no mapselect.tex under %s" % win)
    tmp = tempfile.mkdtemp(prefix="fe-mapart-")
    try:
        subprocess.run([sys.executable, FETEX, "extract", "--png", "--out", tmp,
                        src], check=True, stdout=subprocess.DEVNULL)

        def one(entry):
            hits = glob.glob(os.path.join(tmp, "**", "*__%s.png" % entry),
                             recursive=True)
            if len(hits) != 1:
                raise SystemExit("mapselect entry %r: %d files" % (entry, len(hits)))
            return Image.open(hits[0]).convert("RGBA")

        os.makedirs(a.out, exist_ok=True)
        wrote = []

        def save(img, name):
            p = os.path.join(a.out, name)
            img.save(p, optimize=True)
            wrote.append((name, img.size, os.path.getsize(p)))

        # the texture is 1024x1024 with the 800x600 layout in its top-left;
        # the rest is padding the client never shows
        save(one("MAP_BG0").crop((0, 0, 800, 600)).convert("RGB"), "world.png")
        for n in range(1, 7):
            save(one("MAP%d" % n), "continent%d.png" % n)
        save(one("Mark"), "mark.png")
        save(one("Mark2"), "mark2.png")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for name, size, nbytes in wrote:
        print("  %-16s %4dx%-4d %8d B" % (name, size[0], size[1], nbytes))
    print("wrote %d files to %s" % (len(wrote), os.path.normpath(a.out)))


if __name__ == "__main__":
    main()
