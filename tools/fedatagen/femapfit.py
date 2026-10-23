"""Fit each capital half's minimap projection from its PAINTED doors.

Prints the per-half projection fits and the anchor block to ship. Input is
the fe-shop-icons.tsv that feicons.py generates (pass --icons to point at a
different copy).

WARNING: WHY THIS EXISTS. fegamedata.MINIMAP_A*/C* are ONE linear fit, and there are
TWO scales among the ten halves. Held against the shipped portal table, the
doors painted on map00 and map02 span ~1.45x more art than those constants
project them to -- those maps are drawn at ~1.7 world units per pixel while
map01/03/04 really are at ~2.46. One constant was sub-pixel on six halves and
15-33 px out on the other four, observed in live testing as "everything on
the generated map is further up".

The pairing between a painted blob and a shipped door is NOT known, and some
painted blobs are not doors at all, so this is RANSAC: every pair of
(painted, shipped) correspondences defines a candidate projection; the
candidate with the most painted icons landing on SOME shipped door wins; then
least squares on the inliers. A half is only emitted when at least two painted
doors agree on one projection.

WARNING: Two inliers is an EXACT fit with zero residual, which is not evidence of
quality -- it is the absence of a test. map02 was in that state: feworld's
solver saw the anchors were 29 px apart, took the offset, refused the scale,
and Liberberg drew at the wrong scale. So a half with fewer than
REG_MIN_INLIERS is now also registered on its collision floor (femapreg.py)
and shipped from that when the floor fit is decisively better.
"""

import argparse
import csv
import io
import itertools
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
#: The server's services/ directory, which holds fegamedata and fedata/.
SERVICES = os.path.normpath(os.path.join(HERE, "..", "..", "services"))
sys.path.insert(0, SERVICES)
import fegamedata as G

#: Default input: the icon TSV feicons.py writes into services/fedata/.
DEFAULT_ICONS = os.path.join(SERVICES, "fedata", "fe-shop-icons.tsv")

#: The painted-icon rows; loaded by main() (or load_rows()) before solve().
ROWS = []


def load_rows(path=DEFAULT_ICONS):
    global ROWS
    ROWS = list(csv.DictReader(io.open(path, encoding="utf-8"),
                               delimiter="\t"))
    return ROWS


TOL = 2.5          # px, an inlier
#: a half with fewer painted doors than this is checked against its floor
REG_MIN_INLIERS = 3
#: ...and shipped from the floor when that scores this many times better
REG_WIN = 1.5
MIN_DX = 20.0      # world units the two seed pairs must differ by
MIN_DZ = 5.0


def fit(pairs):
    """least squares px = w/a + c on [(w, p)] -> (a, c) or None"""
    n = len(pairs)
    mw = sum(w for w, _ in pairs) / n
    mp = sum(p for _, p in pairs) / n
    var = sum((w - mw) ** 2 for w, _ in pairs)
    cov = sum((w - mw) * (p - mp) for w, p in pairs)
    if var <= 0 or cov == 0:
        return None
    u = cov / var
    if u == 0:
        return None
    return 1.0 / u, mp - u * mw


def solve(area):
    gates = [(p["gate"][0], p["gate"][1], p["portal"])
             for p in G.area_portals(area)]
    icons = [(float(r["px"]), float(r["py"]))
             for r in ROWS if int(r["area"]) == area and r["kind"] == "door"]
    if len(icons) < 2 or len(gates) < 2:
        return None
    best = None
    for (i1, i2) in itertools.combinations(range(len(icons)), 2):
        for g1 in range(len(gates)):
            for g2 in range(len(gates)):
                if g1 == g2:
                    continue
                gx1, gz1, _ = gates[g1]
                gx2, gz2, _ = gates[g2]
                if abs(gx1 - gx2) < MIN_DX or abs(gz1 - gz2) < MIN_DZ:
                    continue
                px1, py1 = icons[i1]
                px2, py2 = icons[i2]
                ax = (gx1 - gx2) / (px1 - px2) if px1 != px2 else None
                az = (gz1 - gz2) / (py1 - py2) if py1 != py2 else None
                if not ax or not az:
                    continue
                cx = px1 - gx1 / ax
                cz = py1 - gz1 / az
                # sanity: a plausible map scale
                if not (0.8 <= abs(ax) <= 6.0 and 0.8 <= abs(az) <= 6.0):
                    continue
                if ax < 0 or az > 0:          # x grows right, z grows UP
                    continue
                inl = []
                for (ipx, ipy) in icons:
                    hit = None
                    for gx, gz, pid in gates:
                        d = ((gx / ax + cx - ipx) ** 2
                             + (gz / az + cz - ipy) ** 2) ** 0.5
                        if d <= TOL and (hit is None or d < hit[0]):
                            hit = (d, gx, gz, pid)
                    if hit:
                        inl.append((hit[1], hit[2], ipx, ipy, hit[3]))
                if len(inl) < 2:
                    continue
                err = 0.0
                for gx, gz, ipx, ipy, _ in inl:
                    err += ((gx / ax + cx - ipx) ** 2
                            + (gz / az + cz - ipy) ** 2) ** 0.5
                key = (len(inl), -err)
                if best is None or key > best[0]:
                    best = (key, ax, cx, az, cz, inl)
    if best is None:
        return None
    _, ax, cx, az, cz, inl = best
    fx = fit([(gx, ipx) for gx, _, ipx, _, _ in inl])
    fz = fit([(gz, ipy) for _, gz, _, ipy, _ in inl])
    if fx:
        ax, cx = fx
    if fz:
        az, cz = fz
    worst = max(((gx / ax + cx - ipx) ** 2 + (gz / az + cz - ipy) ** 2) ** 0.5
                for gx, gz, ipx, ipy, _ in inl)
    return {"ax": ax, "cx": cx, "az": az, "cz": cz,
            "inliers": inl, "worst": worst,
            "n_icons": len(icons), "n_gates": len(gates)}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="fit each capital half's minimap projection from its "
                    "painted doors (RANSAC against the shipped portal table)")
    ap.add_argument("--icons", default=DEFAULT_ICONS,
                    help="the fe-shop-icons.tsv feicons.py generated "
                         "(default: services/fedata/fe-shop-icons.tsv)")
    ap.add_argument("--out",
                    help="also write the anchor rows as fe-minimap-cal.tsv "
                         "at this path")
    ap.add_argument("--no-register", action="store_true",
                    help="doors only: skip registering weakly fitted halves "
                         "on their collision floor (femapreg.py)")
    args = ap.parse_args(argv)
    load_rows(args.icons)

    print("shipped fit: ax=%.4f cx=%.2f az=%.4f cz=%.2f\n"
          % (G.MINIMAP_AX, G.MINIMAP_CX, G.MINIMAP_AZ, G.MINIMAP_CZ))
    print("%-5s %-10s %-6s %-7s %8s %8s %8s %8s %7s" %
          ("area", "stem", "icons", "inlier", "ax", "cx", "az", "cz", "worst"))
    out = {}
    for area in sorted(G.MINIMAP_FILE):
        r = solve(area)
        stem = G.MINIMAP_FILE[area]
        if not r:
            print("%-5s %-10s  -- not enough painted doors to fit --"
                  % (area, stem))
            continue
        print("%-5s %-10s %-6d %-7d %8.4f %8.2f %8.4f %8.2f %7.2f" %
              (area, stem, r["n_icons"], len(r["inliers"]),
               r["ax"], r["cx"], r["az"], r["cz"], r["worst"]))
        out[stem] = r

    # KEY: A HALF THE DOORS CANNOT PIN IS REGISTERED ON ITS FLOOR (2026-10-23).
    # Fewer than REG_MIN_INLIERS painted doors is an exact fit with no test,
    # and on Liberberg (map02) it left the art at the wrong scale. For such a
    # half, fit the collision floor onto the art (femapreg.py) and ship that
    # instead -- but only when it is DECISIVE: REG_WIN times the edge score
    # of the projection the doors (or the shipped constants) give. On the
    # low-contrast halves the search's peak is not trustworthy, and it does
    # not clear that bar.
    reg = {}
    if not args.no_register:
        import femapreg
        from world import mapcal
        for area in sorted(G.MINIMAP_FILE):
            stem = G.MINIMAP_FILE[area]
            r = out.get(stem)
            if r and len(r["inliers"]) >= REG_MIN_INLIERS:
                continue
            e, pts = femapreg.edge_map(stem), femapreg.floor_boundary(stem)
            if not e or not pts:
                continue
            rows = [{"x": gx, "z": gz, "px": ipx, "py": ipy}
                    for gx, gz, ipx, ipy, _ in (r["inliers"] if r else [])]
            p = mapcal.proj_from_rows(rows, stem)
            s0 = femapreg.score(e, pts, p["ax"], p["cx"], p["az"], p["cz"])
            fr = femapreg.register(stem, e, pts)
            win = fr and fr["score"] >= REG_WIN * max(s0, 1e-9)
            print("%-10s floor registration: ax=%.4f cx=%.2f cz=%.2f score %.2f; "
                  "%s fit scores %.2f -> %s"
                  % (stem, fr["ax"], fr["cx"], fr["cz"], fr["score"],
                     "the doors'" if r else "the shipped", s0,
                     "SHIP IT" if win else "keep"))
            if win:
                reg[stem] = (fr, s0, pts)
                out.pop(stem, None)

    print()
    print("=== anchors to ship (world -> pixel, from the inliers) ===")
    import json
    anch = {}
    for stem, r in out.items():
        anch[stem] = [{"x": round(gx, 2), "z": round(gz, 2),
                       "px": round(ipx, 2), "py": round(ipy, 2),
                       "note": "shipped door %d, painted on the art" % pid}
                      for gx, gz, ipx, ipy, pid in r["inliers"]]
    print(json.dumps(anch, indent=1, sort_keys=True))

    if args.out:
        rows = []
        for stem, r in out.items():
            for gx, gz, ipx, ipy, pid in r["inliers"]:
                rows.append((stem, gx, gz, ipx, ipy, pid, len(r["inliers"])))
        rows.sort(key=lambda t: (t[0], -t[5]))
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write("stem\tx\tz\tpx\tpy\tnote\n")
            for stem, gx, gz, ipx, ipy, pid, n in rows:
                fh.write("%s\t%.2f\t%.2f\t%.2f\t%.2f\t"
                         "shipped door %d, painted on the art; %d inliers\n"
                         % (stem, gx, gz, ipx, ipy, pid, n))
            # a registered half: its projection, as four anchors at the
            # corners of its floor (far apart, so the solver takes the scale)
            for stem in sorted(reg):
                fr, s0, pts = reg[stem]
                xs = [x for x, _ in pts]
                zs = [z for _, z in pts]
                for x, z in ((min(xs), min(zs)), (max(xs), min(zs)),
                             (min(xs), max(zs)), (max(xs), max(zs))):
                    fh.write("%s\t%.2f\t%.2f\t%.2f\t%.2f\t"
                             "floor registered on the art (score %.2f, the "
                             "doors' fit %.2f)\n"
                             % (stem, x, z, x / fr["ax"] + fr["cx"],
                                z / fr["az"] + fr["cz"], fr["score"], s0))
        print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
