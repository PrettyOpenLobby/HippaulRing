"""Read FE's capital collision (DATA/capital/*_hit.oct) and ask it for the ground.

Why this exists: after a door into another area the client drops the player at
y = 10000 and asks its terrain manager for the ground under (x, z)
(0x05000450, called from mode 0xb substate 0 at 0x04ffa0f4). If that query
finds nothing, the height write at 0x04ffa10b is SKIPPED and the player hangs at
10000. This tool answers the same question from the same files, so a door's
arrival point can be checked -- and chosen -- against the geometry the client
will actually load.

Format, as far as it is needed here (measured on map00_00/map00_01):
  chunks are  [4cc tag][u32 size][u32 count][u32 0] then `size` bytes of data,
  each chunk starting on a 16-byte boundary
  per collision object:  HED0 OBJ0 GRP0 WGP0 HIT0 PRM0 VTX0 IDX0
  VTX0: `count` vertices, 52-byte stride, float x,y,z first (world space)
  IDX0: `count` u16 indices, a plain triangle list

The *_hit.oct files come from YOUR OWN client install: pass its DATA/capital
directory with --data (default: ./DATA/capital).

    python feoct.py map00_01 -4.5 -167          # ground under one point
    python feoct.py map00_00 --scan -5 -60 12   # a grid around it
    python feoct.py --grid                      # bake services/fedata/fe-capital-ground.json
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
#: Default input: the DATA/capital directory of a client install, relative to
#: the current directory. Override with --data.
CAPITAL = os.path.join("DATA", "capital")
#: Default --grid output: the server's shipped-data directory.
DEFAULT_GRID = os.path.normpath(os.path.join(
    HERE, "..", "..", "services", "fedata", "fe-capital-ground.json"))
TAGS = {b"OCT0", b"MTR0", b"NOD0", b"HED0", b"OBJ0", b"GRP0", b"WGP0", b"HIT0",
        b"PRM0", b"VTX0", b"IDX0", b"FRM0"}


def chunks(b):
    """Yield (tag, offset, size, count) walking the file chunk by chunk."""
    o = 0
    while o + 16 <= len(b):
        tag = b[o:o + 4]
        if tag not in TAGS:
            raise ValueError("unknown chunk %r at %d" % (tag, o))
        size, count = struct.unpack_from("<II", b, o + 4)
        yield tag, o, size, count
        o = (o + 16 + size + 15) & ~15       # chunks are 16-byte aligned


def hit_file(stem, folder=CAPITAL):
    """<stem>_hit.oct, or the capital's shared mapNN_hit.oct when the half has
    none of its own (map03_00 ships only map03_hit.oct)."""
    own = os.path.join(folder, stem + "_hit.oct")
    if os.path.exists(own):
        return own
    return os.path.join(folder, stem.split("_")[0] + "_hit.oct")


def triangles(stem, folder=CAPITAL):
    """Every collision triangle in <stem>_hit.oct, as ((x,y,z),(x,y,z),(x,y,z)).

    Finds VTX0/IDX0 on 16-byte boundaries instead of walking every chunk: PRM0's
    size field does not give its real extent (it says 14 and spans 240), so a
    strict walk derails. Each header is checked for self-consistency instead --
    VTX0 size == count * 52, IDX0 size == count * 2 -- and an IDX0 is paired with
    the VTX0 immediately before it.
    """
    b = open(hit_file(stem, folder), "rb").read()
    tris, verts = [], None
    for o in range(0, len(b) - 16, 16):
        tag = b[o:o + 4]
        if tag not in (b"VTX0", b"IDX0"):
            continue
        size, count, zero = struct.unpack_from("<III", b, o + 4)
        if zero != 0 or count == 0:
            continue
        if tag == b"VTX0" and size == count * 52 and o + 16 + size <= len(b):
            verts = [struct.unpack_from("<fff", b, o + 16 + i * 52)
                     for i in range(count)]
        elif tag == b"IDX0" and size == count * 2 and verts is not None:
            idx = struct.unpack_from("<%dH" % count, b, o + 16)
            for i in range(0, count - 2, 3):
                a, c, d = idx[i], idx[i + 1], idx[i + 2]
                if max(a, c, d) < len(verts):
                    tris.append((verts[a], verts[c], verts[d]))
            verts = None
    return tris


def _y_at(tri, x, z):
    """Height of triangle `tri` straight above/below (x, z), or None."""
    (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = tri
    den = (z1 - z2) * (x0 - x2) + (x2 - x1) * (z0 - z2)
    if abs(den) < 1e-9:
        return None                      # vertical wall: no floor here
    a = ((z1 - z2) * (x - x2) + (x2 - x1) * (z - z2)) / den
    c = ((z2 - z0) * (x - x2) + (x0 - x2) * (z - z2)) / den
    d = 1.0 - a - c
    if a < -1e-6 or c < -1e-6 or d < -1e-6:
        return None
    return a * y0 + c * y1 + d * y2


class Ground:
    """A 2D bucket grid over the triangles so a query is not O(all)."""
    def __init__(self, stem, cell=8.0, folder=CAPITAL):
        self.stem, self.cell, self.folder = stem, cell, folder
        self.tris = triangles(stem, folder)
        self.grid = {}
        for t in self.tris:
            xs = [p[0] for p in t]; zs = [p[2] for p in t]
            for gx in range(int(min(xs) // cell), int(max(xs) // cell) + 1):
                for gz in range(int(min(zs) // cell), int(max(zs) // cell) + 1):
                    self.grid.setdefault((gx, gz), []).append(t)
        xs = [p[0] for t in self.tris for p in t]
        ys = [p[1] for t in self.tris for p in t]
        zs = [p[2] for t in self.tris for p in t]
        self.bounds = ((min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs)))

    def heights(self, x, z):
        """Every surface height at (x, z), highest first."""
        cand = self.grid.get((int(x // self.cell), int(z // self.cell)), [])
        hs = sorted({round(h, 3) for h in (_y_at(t, x, z) for t in cand)
                     if h is not None}, reverse=True)
        return hs

    def ground(self, x, z, from_y=10000.0):
        """What a ray cast straight down from `from_y` hits first, or None."""
        hs = [h for h in self.heights(x, z) if h <= from_y]
        return hs[0] if hs else None


def write_grid(out_path, stems, folder=CAPITAL):
    """Bake a 1-unit ground grid per map for the server (which has no game files).

    One JSON object per map stem:
      {"x0", "z0", "nx", "nz", "cell": 1.0,
       "data": base64(zlib(int16 LE[nz][nx]))}
    Each cell holds round(ground * 10) sampled at the cell CENTRE by the same
    downward ray the client casts (from y = 10000), or -32768 for no ground.
    Measured 2026-09-11: 28,041 triangles across ten maps -> ~19 KB compressed.
    """
    import base64
    import json
    import zlib
    out = {}
    for stem in stems:
        g = Ground(stem, folder=folder)
        (x0, x1), _, (z0, z1) = g.bounds
        x0, z0 = float(int(x0) - 1), float(int(z0) - 1)
        nx, nz = int(x1 - x0) + 2, int(z1 - z0) + 2
        buf = bytearray()
        for iz in range(nz):
            for ix in range(nx):
                h = g.ground(x0 + ix + 0.5, z0 + iz + 0.5)
                v = -32768 if h is None else max(-32767, min(32767,
                                                              int(round(h * 10))))
                buf += struct.pack("<h", v)
        out[stem] = {"x0": x0, "z0": z0, "nx": nx, "nz": nz, "cell": 1.0,
                     "source": os.path.basename(hit_file(stem, folder)),
                     "data": base64.b64encode(zlib.compress(bytes(buf), 9))
                     .decode("ascii")}
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=0, sort_keys=True)
    return out


ALL_STEMS = ["map00_00", "map00_01", "map01_00", "map01_01",
             "map02_00", "map02_01", "map03_00", "map03_01",
             "map04_00", "map04_01"]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="query FE capital collision (*_hit.oct) for the ground, "
                    "or bake a 1-unit ground grid for the server")
    ap.add_argument("--data", default=CAPITAL,
                    help="the DATA/capital directory of your client install "
                         "(default: ./DATA/capital)")
    ap.add_argument("--grid", nargs="?", const=DEFAULT_GRID, metavar="OUT",
                    help="bake the ground grid JSON for every capital half "
                         "(default output: services/fedata/"
                         "fe-capital-ground.json)")
    ap.add_argument("--scan", nargs=3, type=float,
                    metavar=("CX", "CZ", "R"),
                    help="print a height grid around (CX, CZ), radius R")
    ap.add_argument("stem", nargs="?", help="map stem, e.g. map00_01")
    ap.add_argument("coords", nargs="*", type=float,
                    help="x z of one point to query")
    args = ap.parse_args(argv)
    if args.grid:
        stems = [args.stem] if args.stem else ALL_STEMS
        out = write_grid(args.grid, stems, folder=args.data)
        print("wrote %s: %d maps" % (args.grid, len(out)))
        return 0
    if not args.stem:
        ap.error("a map stem is required unless --grid is given")
    g = Ground(args.stem, folder=args.data)
    (x0, x1), (y0, y1), (z0, z1) = g.bounds
    print("%s: %d triangles, x %.0f..%.0f  y %.1f..%.1f  z %.0f..%.0f"
          % (args.stem, len(g.tris), x0, x1, y0, y1, z0, z1))
    if args.scan:
        cx, cz, r = args.scan
        step = max(1.0, r / 6)
        zz = cz + r
        while zz >= cz - r - 1e-6:
            row = []
            xx = cx - r
            while xx <= cx + r + 1e-6:
                h = g.ground(xx, zz)
                row.append("  .  " if h is None else "%5.1f" % h)
                xx += step
            print("z=%7.1f %s" % (zz, " ".join(row)))
            zz -= step
        return 0
    if len(args.coords) >= 2:
        x, z = args.coords[0], args.coords[1]
        print("ground under (%.1f, %.1f): %s   all surfaces: %s"
              % (x, z, g.ground(x, z), g.heights(x, z)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
