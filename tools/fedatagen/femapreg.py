"""Register a capital half's COLLISION FLOOR onto its minimap ART.

The minimap is a picture and the collision (DATA/capital/*_hit.oct, baked by
feoct.py into fe-capital-ground.json) is where a player can actually stand.
Both are the client's own data, so a half's pixel<->world projection can be
read off them directly: the walls are drawn on the art as light outlines, so
the right projection puts the floor's EDGE on the art's strongest edges.

    score = mean art edge strength (luminance gradient / its map mean)
            under the floor's boundary cells, projected

WHY THIS EXISTS (2026-10-23). femapfit.py fits each half from its painted
doors. On Liberberg (map02_00 / map02_01) only two painted doors match, 29 px
apart, and feworld's solver rightly refuses a scale from two points that
close -- so both halves kept the shipped 2.46 world units per pixel. They
are drawn at ~1.67 (like map00), and the editor showed Liberberg's floor and
NPCs shrunk into a corner of the art. Registered against the floor, both
halves peak at ~1.67 with the origin at the art's centre, at more than twice
the score of the door fit; on every other half the door fit or the shipped
fit is already within 1.45x of the best the search finds.

Pure Python (PIL for the PNG), a few seconds a half. A coarse search over scale and both offsets, then a fine one around
the best. The scale is isotropic (ax = -az): the minimaps do not stretch one
axis (mapcal.py, "borrow a scale across the axes").
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICES = os.path.normpath(os.path.join(HERE, "..", "..", "services"))
if SERVICES not in sys.path:
    sys.path.insert(0, SERVICES)
import fegamedata as G  # noqa: E402

MINIMAP_DIR = os.path.join(SERVICES, "fedata", "minimaps")
#: the search: world units per pixel, and where world (0, 0) may sit
SCALES = (1.30, 3.00)
OFFSETS = (64.0, 192.0)


def edge_map(stem, path=None):
    """The art's luminance gradient magnitude per pixel, divided by its mean
    over the map (so scores compare across maps). [[float]] rows, or None."""
    from PIL import Image
    p = path or os.path.join(MINIMAP_DIR, stem + ".png")
    if not os.path.isfile(p):
        return None
    im = Image.open(p).convert("L")
    w, h = im.size
    px = im.load()
    e = [[0.0] * w for _ in range(h)]
    tot = 0.0
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            gx = px[x + 1, y] - px[x - 1, y]
            gy = px[x, y + 1] - px[x, y - 1]
            v = (gx * gx + gy * gy) ** 0.5
            e[y][x] = v
            tot += v
    mean = tot / (w * h) or 1.0
    return [[v / mean for v in row] for row in e]


def floor_boundary(stem):
    """World (x, z) of every floor cell with a non-floor 4-neighbour."""
    g = G._ground_grid(stem)
    if g is None:
        return []
    x0, z0, nx, nz, c = g
    on = lambda ix, iz: (0 <= ix < nx and 0 <= iz < nz
                         and c[iz * nx + ix] != -32768)
    out = []
    for iz in range(nz):
        for ix in range(nx):
            if not on(ix, iz):
                continue
            if not (on(ix - 1, iz) and on(ix + 1, iz)
                    and on(ix, iz - 1) and on(ix, iz + 1)):
                out.append((x0 + ix + 0.5, z0 + iz + 0.5))
    return out


def score(e, pts, ax, cx, az, cz):
    """Mean edge strength under the projected boundary; 0 when a fifth of
    it falls off the art (a projection that throws the town off the map)."""
    h, w = len(e), len(e[0])
    s, n = 0.0, 0
    for x, z in pts:
        px = int(x / ax + cx)
        py = int(z / az + cz)
        if 0 <= px < w and 0 <= py < h:
            s += e[py][px]
            n += 1
    if not pts or n < 0.8 * len(pts):
        return 0.0
    return s / len(pts)


def _search(e, pts, scales, cxs, czs):
    best = (0.0, None)
    for a in scales:
        for cx in cxs:
            for cz in czs:
                sc = score(e, pts, a, cx, -a, cz)
                if sc > best[0]:
                    best = (sc, (a, cx, cz))
    return best


def _frange(lo, hi, step):
    out, v = [], lo
    while v <= hi + 1e-9:
        out.append(round(v, 6))
        v += step
    return out


def register(stem, e=None, pts=None):
    """The best isotropic projection for this half: {"ax", "cx", "az", "cz",
    "score"}, or None without art or floor."""
    e = e if e is not None else edge_map(stem)
    pts = pts if pts is not None else floor_boundary(stem)
    if not e or not pts:
        return None
    sub = pts[::max(1, len(pts) // 300)]
    s, best = _search(e, sub, _frange(SCALES[0], SCALES[1], 0.05),
                      _frange(OFFSETS[0], OFFSETS[1], 4.0),
                      _frange(OFFSETS[0], OFFSETS[1], 4.0))
    if best is None:
        return None
    a, cx, cz = best
    s, best = _search(e, sub, _frange(a - 0.05, a + 0.05, 0.01),
                      _frange(cx - 4, cx + 4, 1.0), _frange(cz - 4, cz + 4, 1.0))
    a, cx, cz = best
    s, best = _search(e, pts, _frange(a - 0.01, a + 0.01, 0.0025),
                      _frange(cx - 1, cx + 1, 0.5), _frange(cz - 1, cz + 1, 0.5))
    a, cx, cz = best
    return {"ax": a, "cx": cx, "az": -a, "cz": cz, "score": s}


if __name__ == "__main__":
    import time
    for stem in sys.argv[1:] or sorted(set(G.MINIMAP_FILE.values())):
        t = time.time()
        r = register(stem)
        print(stem, r and {k: round(v, 3) for k, v in r.items()},
              "%.1fs" % (time.time() - t))
