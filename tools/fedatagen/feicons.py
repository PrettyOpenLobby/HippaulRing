"""feicons.py -- recover WHERE a capital's shops stood, off the minimap art.

KEY: WHY THIS IS POSSIBLE AT ALL. dat.pak says WHO belongs in a capital (twenty
named roles in fet_npc_type) and never where any of them stood -- that was SE's
server data and it is gone. But the minimaps are not drawn by the client from
entity data: the shop icons are PAINTED INTO `DATA/Window/MAP/mapNN_SS_mini.tex`
as pixels. SE drew each icon where its shop was. So the positions are not lost,
they are in the art, and they only need a pixel->world mapping to read back.

## The mapping, and how it was proved

    world_x =  2.4646 * (px - 127.10)
    world_z = -2.4625 * (py - 127.45)

Fitted against a GROUND TRUTH that has nothing to do with shops: the yellow
door marks. `fet_area_portal` ships the exact world (x, z) of every door in the
ten capital halves, and those doors are painted on the minimaps too. Six
(mark, door) pairs across FOUR different capitals fit the line above with a
worst error of 2.2 units in x and 4.7 in z -- one to two pixels, which is the
difference between a glyph's centroid and the doorway it marks.

WARNING: So a recovered position is accurate to a couple of METRES, not exactly. It
is "walk here and you are standing at the shop", not a survey.

## Reading an icon

Shape is what the shop SELLS; colour is which CLASS it serves.

    sword   -> weapon shop        red   -> Warrior     PARTIAL: the colour->class
    shield  -> armour shop        green -> Scout          binding is a READING
    ring    -> ring shop          blue  -> Sorcerer       (see below)

WARNING: TAN ICONS ARE NOT NAMED. A capital has two of them -- a money bag and a
beast/sign -- and they are the same colour to within one channel step
((186,161,122) vs (186,162,123)). One is the bank; which, the art does not say.
They keep their POSITION, which is solid, and no role.

so `Warrior_Weapon_Shop` is the RED SWORD and `Warrior_Ring_Shop` is the RED
RING, which is the question that started this.

PARTIAL: WHAT IS MEASURED vs READ. Measured: there are exactly three colours and
three shapes, and the roster has exactly three weapon/armour/ring shops keyed
to three models (warrior 233, scout 231, sorcerer 230). Read: that red is the
warrior's. Nothing in the art says so. To settle it, stand on the red sword in
game and see which shop the client opens once a keeper is placed there.

A ring icon is told apart by the TEAL HALO drawn around its gem -- no other
icon has one. Sword and shield are then separated by shape: a sword is drawn
diagonally, so it fills little of its bounding box; a shield is compact.

First extract the minimap PNGs from YOUR OWN client install (they are derived
art and fetex regenerates them in seconds; <CLIENT> is the install directory
that contains DATA/):

    python fetex.py extract --out . --png "<CLIENT>/DATA/Window/MAP"/map0*_mini.tex
    python feicons.py --maps .   # writes services/fedata/fe-shop-icons.tsv
"""
import argparse
import glob
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
#: Default output: the server's shipped-data directory.
DEFAULT_OUT = os.path.normpath(os.path.join(
    HERE, "..", "..", "services", "fedata", "fe-shop-icons.tsv"))

#: capital area id -> its minimap stem. From the client's own table at
#: 0x052D1858; the id is the FIRST dword of an entry (getting that backwards
#: once sent a player into the void).
PAIR = {21: "map00_00", 91: "map00_01", 39: "map01_00", 92: "map01_01",
        57: "map02_01", 93: "map02_00", 62: "map03_00", 94: "map03_01",
        78: "map04_00", 95: "map04_01"}

AX, CX = 2.4646, 127.10
AZ, CZ = -2.4625, 127.45


def world(px, py):
    return (AX * (px - CX), AZ * (py - CZ))


def _sat(c):
    return max(c) - min(c)


def _isyellow(c):
    return abs(c[0] - 0xFD) < 40 and abs(c[1] - 0xE6) < 45 and c[2] < 70


def _isteal(c):
    # the ring halo: cyan-ish, light, and clearly more blue+green than red
    r, g, b = c
    return g > 120 and b > 120 and r < g - 25 and r < b - 25


def outline_frac(comp, px, W, H):
    """How much of a blob's outside border is DARK.

    KEY: THIS IS WHAT SEPARATES AN ICON FROM TERRAIN. Every icon is drawn with a
    dark outline so it reads against the map; a red roof or a green field has
    no such border. Measured on the two extremes: the verified map (10 real
    icons) keeps 8 of 11 blobs at >=0.45, while the worst over-firing map drops
    from 28 blobs to 7. Saturation alone could not tell them apart because the
    terrain IS saturated.
    """
    cs = set(comp)
    border = set()
    for (x, y) in comp:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, y + dy)
            if n not in cs and 0 <= n[0] < W and 0 <= n[1] < H:
                border.add(n)
    if not border:
        return 0.0
    return sum(1 for b in border if sum(px[b]) / 3 < 78) / float(len(border))


#: Below this, a blob has no outline worth the name and is scenery.
OUTLINE_MIN = 0.42


def clusters(px, W, H, pred, rad=1, minpx=10):
    P = {(x, y): px[x, y] for y in range(H) for x in range(W) if pred(px[x, y])}
    seen, out = set(), []
    for s in list(P):
        if s in seen:
            continue
        st, comp = [s], []
        while st:
            q = st.pop()
            if q in seen or q not in P:
                continue
            seen.add(q)
            comp.append(q)
            for dx in range(-rad, rad + 1):
                for dy in range(-rad, rad + 1):
                    n = (q[0] + dx, q[1] + dy)
                    if n in P and n not in seen:
                        st.append(n)
        if len(comp) >= minpx:
            out.append(comp)
    return out


def halo_frac(comp, px, W, H):
    """How much of the blob has a TEAL pixel within 3 -- the ring gem's halo.

    WARNING: A RING HAS NO DARK OUTLINE. It is ringed in teal instead, so the
    outline test scored it near zero and threw away all three ring shops in
    the one capital that had been verified by eye. Icons qualify on EITHER
    signature; scenery has neither.
    """
    hits = 0
    for (x, y) in comp:
        found = False
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and 0 <= ny < H and _isteal(px[nx, ny]):
                    found = True
                    break
            if found:
                break
        hits += found
    return hits / float(len(comp))


def classify(comp, px, W, H):
    """(kind, colour) for one icon blob."""
    xs = [p[0] for p in comp]
    ys = [p[1] for p in comp]
    w, h = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    fill = len(comp) / float(w * h)
    # colour: average the saturated pixels and take the dominant channel
    r = sum(px[p][0] for p in comp) / len(comp)
    g = sum(px[p][1] for p in comp) / len(comp)
    b = sum(px[p][2] for p in comp) / len(comp)
    # KEY: TAN vs CLASS-COLOURED IS A SATURATION SPLIT, not a channel-difference
    # one. Gold is redder than it is green or blue, so "r beats g and b" called
    # the money bag a red shield -- which then collided with the real red
    # shield and, correctly, poisoned the whole capital's trust. Measured on
    # the verified map: the bag and the beast sit at 0.345 and 0.343, while
    # every class-coloured icon is 0.61 to 0.77. Nothing lands between.
    sat = (max(r, g, b) - min(r, g, b)) / max(r, g, b, 1.0)
    if sat < 0.45:
        col = "tan"
    elif r >= g and r >= b:
        col = "red"
    elif g >= b:
        col = "green"
    else:
        col = "blue"
    # KEY: SHAPE COMES FROM SIZE AND FILL, NOT FROM THE HALO. The halo test
    # ("teal within 3 px") worked on the one capital whose icons sit on grey
    # ground and failed on every capital built beside WATER, where a sword or
    # a shield is also within 3 px of teal -- which is how areas 39, 94 and 95
    # each ended up with two or three Sorcerer_Ring_Shops.
    #
    # The real icons cluster tightly, measured across the verified half and
    # the conflicting ones:
    #     ring   n 20-23   fill 0.80-0.88     (small and solid)
    #     sword  n 44-46   fill 0.44-0.46     (drawn diagonally, so half empty)
    #     shield n 64-76   fill 0.80-0.86     (big and solid)
    # Nothing an icon is lands outside that, and 100+ px is not an icon at all
    # -- area 78's "18 Cloth_Armor_Shops" were a grid of blue roofs running
    # from 21 px to 624.
    if len(comp) > 100:
        return "other", col, w, h, round(fill, 2)
    if col == "tan":
        # KEY: TWO TAN ICONS, AND NOTHING TELLS THEM APART. The verified map has
        # a money bag and a beast/sign at (186,161,122) and (186,162,123) --
        # the same colour to within one channel step. One of them is the bank;
        # which is not decidable from the art. Naming both "bank" invented a
        # duplicate that then, correctly, poisoned the whole capital's trust.
        # So a tan icon keeps its POSITION, which is solid, and gets no role.
        kind = "tan"
    # Explicit BANDS with a "matches nothing" outcome, rather than a chain of
    # elifs where the last one is a catch-all. A catch-all is what made every
    # unclassifiable blob a shield -- area 94's n=24 fill=0.67 is too small for
    # a shield and too loose for a ring, and calling it one manufactured a
    # duplicate Light_Armor_Shop out of a piece of scenery.
    n = len(comp)
    if 18 <= n <= 30 and fill >= 0.75:
        kind = "ring"
    elif 38 <= n <= 55 and fill <= 0.60:
        kind = "sword"
    elif 55 <= n <= 90 and fill >= 0.75:
        kind = "shield"
    else:
        kind = "other"
    return kind, col, w, h, round(fill, 2)


#: (kind, colour) -> the fet_npc_type role that belongs on it.
ROLE = {
    ("sword", "red"): "Warrior_Weapon_Shop",
    ("sword", "green"): "Scout_Weapon_Shop",
    ("sword", "blue"): "Sorcerer_Weapon_Shop",
    ("shield", "red"): "Heavy_Armor_Shop",
    ("shield", "green"): "Light_Armor_Shop",
    ("shield", "blue"): "Cloth_Armor_Shop",
    ("ring", "red"): "Warrior_Ring_Shop",
    ("ring", "green"): "Scout_Ring_Shop",
    ("ring", "blue"): "Sorcerer_Ring_Shop",
}


def scan(area, stem, mapdir="."):
    hits = glob.glob(os.path.join(mapdir, "%s_mini__00__*.png" % stem))
    if not hits:
        return []
    im = Image.open(hits[0]).convert("RGB")
    px, (W, H) = im.load(), im.size
    out = []
    for comp in clusters(px, W, H,
                         lambda c: _sat(c) > 55 and max(c) > 85 and not _isyellow(c)):
        if (outline_frac(comp, px, W, H) < OUTLINE_MIN
                and halo_frac(comp, px, W, H) < 0.35):
            continue
        kind, col, w, h, fill = classify(comp, px, W, H)
        if kind == "other":
            continue
        cx = sum(p[0] for p in comp) / len(comp)
        cy = sum(p[1] for p in comp) / len(comp)
        wx, wz = world(cx, cy)
        out.append({"area": area, "kind": kind, "colour": col,
                    "role": ROLE.get((kind, col), ""),
                    "x": round(wx, 1), "z": round(wz, 1),
                    "px": round(cx, 1), "py": round(cy, 1),
                    "n": len(comp), "fill": fill})
    # doors, from the yellow marks -- useful as a cross-check against the
    # shipped table and as a landmark when walking a town
    for comp in clusters(px, W, H, lambda c: _isyellow(c), minpx=8):
        cx = sum(p[0] for p in comp) / len(comp)
        cy = sum(p[1] for p in comp) / len(comp)
        wx, wz = world(cx, cy)
        out.append({"area": area, "kind": "door", "colour": "yellow", "role": "",
                    "x": round(wx, 1), "z": round(wz, 1), "px": round(cx, 1),
                    "py": round(cy, 1), "n": len(comp), "fill": 0})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--maps", default=".",
                    help="directory holding the extracted map*_mini PNGs "
                         "(default: the current directory)")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="TSV to write (default: services/fedata/"
                         "fe-shop-icons.tsv)")
    ap.add_argument("--sheet", action="store_true")
    args = ap.parse_args()
    rows = []
    for area, stem in sorted(PAIR.items()):
        got = scan(area, stem, args.maps)
        rows += got
        named = sum(1 for r in got if r["role"])
        print("area %-3d %-10s %2d icons, %d named" % (area, stem, len(got), named))
    # WARNING: THE DETECTOR OVER-FIRES ON COLOURFUL TERRAIN. Each minimap is its own
    # 8-bit quantisation, so the icon art has slightly different RGB on every
    # one and an exact-colour match does not transfer (checked: map01_00 and
    # map02_01 share ZERO colours with map01_01's verified icons). A capital
    # has twenty roles and at most ~14 of them get an icon, so a half that
    # yields more than that is finding roof tiles, not shops.
    #
    # Rather than ship 188 rows as if they were data, every row carries how
    # far to trust it:
    #   eye   -- verified by eye against the art (area 92)
    #   auto  -- the count is plausible, not verified by eye
    #   noisy -- too many blobs; treat as a lead, not a position
    EYEBALLED = {92}
    per_area = {}
    for r in rows:
        per_area[r["area"]] = per_area.get(r["area"], 0) + 1
    # KEY: THE REAL QUALITY GATE. A capital has exactly ONE Warrior_Weapon_Shop.
    # Two means the classifier put a roof tile in the same bucket as a sword,
    # and there is no way to know which row is the shop -- so the whole half is
    # a lead, not data. This catches what a blob count cannot: 21 came in under
    # the count and still had two Bank_Keepers.
    dup = {}
    for r in rows:
        if r["role"]:
            k = (r["area"], r["role"])
            dup[k] = dup.get(k, 0) + 1
    conflicted = {a for (a, _role), n in dup.items() if n > 1}
    for r in rows:
        r["trust"] = ("eye" if r["area"] in EYEBALLED and r["area"] not in conflicted
                      else "noisy" if (r["area"] in conflicted
                                       or per_area[r["area"]] > 14)
                      else "auto")
    noisy = sorted({r["area"] for r in rows if r["trust"] == "noisy"})
    if noisy:
        print("WARNING: noisy (more blobs than a capital can have): %s"
              % ", ".join(str(a) for a in noisy))
    cols = ("area", "kind", "colour", "role", "x", "z", "px", "py", "n",
            "fill", "trust")
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(cols) + "\n")
        for r in sorted(rows, key=lambda r: (r["area"], r["kind"], r["colour"])):
            fh.write("\t".join(str(r[c]) for c in cols) + "\n")
    print("wrote %s (%d rows)" % (args.out, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
