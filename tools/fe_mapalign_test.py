#!/usr/bin/env python3
"""fe_mapalign_test.py -- does each capital half's minimap projection put the
collision FLOOR on the streets drawn in the art?

Run from services/:  python ../tools/fe_mapalign_test.py

2026-10-23: Liberberg's two halves (map02_00 / map02_01) drew their floor and
NPCs shrunk into a corner of the art. Only two painted doors matched there,
29 px apart, so the solver refused their scale and both halves kept the
shipped 2.46 world units per pixel; the art is drawn at ~1.66. Nothing
checked the projection against the one thing it has to get right, so this
does, for all ten halves:

  * the floor registered on the art (tools/fedatagen/femapreg.py: the floor's
    edge on the art's drawn walls) must not beat the projection the editor
    uses by more than MAX_RATIO. Before the fix map02 scored 2.2x and 3.1x
    worse than its registration; every correct half is within 1.5x.
  * a positive control: Liberberg's old door-only anchors still FAIL that
    check, so it can fail
  * mapcal's two-axis rule: two short axes that agree give their scale; two
    that disagree keep the shipped one

Needs the generated fedata (minimaps, fe-capital-ground.json, the
calibration); without it the suite skips.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
for p in (_SERVICES, os.path.join(_HERE, "fedatagen")):
    if p not in sys.path:
        sys.path.insert(0, p)

import feworld      # noqa: E402,F401
import fegamedata   # noqa: E402
from world import mapcal  # noqa: E402

CHECKS = []
OUT = sys.__stdout__
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

#: how much better than the editor's projection the floor registration may
#: score before the half is called misaligned
MAX_RATIO = 1.6
#: Liberberg's door-only anchors as femapfit shipped them before 2026-10-23
OLD_MAP02 = {
    "map02_00": [(110.0, -22.5, 196.3, 145.0), (60.0, -57.5, 167.3, 165.0)],
    "map02_01": [(-100.0, -22.5, 63.6, 145.0), (-150.0, -57.5, 34.7, 165.0)],
}


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-66s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))


def _old_proj(stem, shipped_only=False):
    rows = [{"x": x, "z": z, "px": px, "py": py} for x, z, px, py in OLD_MAP02[stem]]
    if not shipped_only:
        return mapcal.proj_from_rows(rows, stem)
    # the solver as it was: offsets only, the shipped scale
    ax, az = fegamedata.MINIMAP_AX, fegamedata.MINIMAP_AZ
    return {"ax": ax, "az": az,
            "cx": sum(r["px"] - r["x"] / ax for r in rows) / len(rows),
            "cz": sum(r["py"] - r["z"] / az for r in rows) / len(rows)}


def test_two_axes():
    say("\nthe solver: two short axes that agree")
    rows = [{"x": x, "z": z, "px": px, "py": py}
            for x, z, px, py in OLD_MAP02["map02_00"]]
    p = mapcal.proj_from_rows(rows)
    check("map02's two doors (29 px / 20 px apart) give ~1.74 on both axes",
          1.70 < p["ax"] < 1.78 and abs(p["az"] + p["ax"]) < 1e-9
          and "agree" in p["how_x"], repr(p))
    bad = [dict(rows[0]), dict(rows[1], px=rows[1]["px"] + 12)]
    q = mapcal.proj_from_rows(bad)
    check("...two that disagree keep the shipped scale (offset only)",
          q["ax"] == fegamedata.MINIMAP_AX and q["az"] == fegamedata.MINIMAP_AZ,
          repr(q))
    one = mapcal.proj_from_rows(rows[:1])
    check("...one anchor is still an offset only", one["ax"] == fegamedata.MINIMAP_AX)


def test_floor_on_art():
    say("\nthe floor on the art, every half")
    import femapreg
    stems = sorted(set(fegamedata.MINIMAP_FILE.values()))
    if not all(os.path.isfile(os.path.join(femapreg.MINIMAP_DIR, s + ".png"))
               for s in stems) or fegamedata._ground_grid(stems[0]) is None:
        say("  SKIP: no generated minimaps / capital ground in fedata")
        return None
    cache = {}
    for stem in stems:
        e, pts = femapreg.edge_map(stem), femapreg.floor_boundary(stem)
        reg = femapreg.register(stem, e, pts)
        p = mapcal.mapcal_proj(stem)
        cur = femapreg.score(e, pts, p["ax"], p["cx"], p["az"], p["cz"])
        ratio = reg["score"] / max(cur, 1e-9)
        cache[stem] = (e, pts, reg)
        check("%s: the floor sits on the drawn streets (%.2fx of the best)"
              % (stem, ratio), ratio <= MAX_RATIO,
              "editor %.2f vs registered %.2f at ax=%.3f c=(%.1f, %.1f); "
              "editor ax=%.3f c=(%.1f, %.1f)"
              % (cur, reg["score"], reg["ax"], reg["cx"], reg["cz"],
                 p["ax"], p["cx"], p["cz"]))
    say("\npositive control: Liberberg as it was")
    for stem in sorted(OLD_MAP02):
        e, pts, reg = cache[stem]
        p = _old_proj(stem, shipped_only=True)
        old = femapreg.score(e, pts, p["ax"], p["cx"], p["az"], p["cz"])
        check("%s with the shipped scale FAILS the check (%.2fx)"
              % (stem, reg["score"] / old), reg["score"] / old > MAX_RATIO)
    say("\nLiberberg now")
    for stem in sorted(OLD_MAP02):
        p = mapcal.mapcal_proj(stem)
        check("%s: ~1.66 world units per pixel, world origin at the art's "
              "centre" % stem, 1.6 < p["ax"] < 1.72 and 1.6 < -p["az"] < 1.72
              and abs(p["cx"] - 128) < 3 and abs(p["cz"] - 128) < 3, repr(p))
    return True


def main():
    test_two_axes()
    test_floor_on_art()
    bad = [c for c in CHECKS if not c[1]]
    say("\n%d checks, %d failed" % (len(CHECKS), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
