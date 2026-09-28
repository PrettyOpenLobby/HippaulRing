"""The minimap calibration per capital half: anchors and the fitted projection."""
import json
import os
import threading
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import deps

# ---------------------------------------------------------------------------
# THE MINIMAP CALIBRATION, per capital half.
#
# ONE LINEAR FIT CANNOT SERVE TEN DIFFERENT MAPS. fegamedata.MINIMAP_A*/C* were
# fitted once, against icons measured by eye on two halves. They are genuinely
# sub-pixel on six of the ten -- and 15 to 33 px out on 21, 91, 57 and 93.
# Every half is its own map with its own world origin, so the mapping is
# per-map DATA, not a constant.
#
# It is stored as ANCHORS rather than as the four numbers, because an anchor is
# EVIDENCE and the numbers are a conclusion: "the door the portal table puts at
# world (117.2, -18.2) is painted at pixel (197.0, 139.2)". Anchors survive a
# better solver, can be read by a human, and can be thrown out one at a time
# when one turns out to be a mis-drag. The four numbers are re-derived on every
# read.
#
# The anchor a capital half gives you for free is a SHIPPED DOOR: the portal
# table says where it is in the world, and SE painted it on the art. That is
# why the panel lets you drag a door marker and nothing else -- an NPC you
# placed yourself is not evidence of where anything is.
_MAPCAL = {}
_MAPCAL_LOCK = threading.Lock()

#: How far apart two anchors must be ON THE ART before their SCALE is
#: believed -- in PIXELS, which is the quantity that actually decides it. Two
#: doors on the same wall have nearly the same x (area 39's shipped pair have
#: IDENTICAL x, 186.5 both) and fitting a scale to those is a divide by
#: nothing. At ~1 px of measurement noise, a 40 px span pins the scale to
#: about 2.5%, which is ~3 px of drift across a whole 256 px map. Below it we
#: do not guess: two points that close pin a POSITION, not a stretch.
MAPCAL_MIN_PX_SPAN = 40.0

#: A solved scale this far from the shipped one is a bad anchor, not a
#: discovery -- the ten maps are drawn at one nominal scale and it is the
#: ORIGIN that really moves. Outside the band we keep the shipped scale.
MAPCAL_SCALE_BAND = (0.5, 2.0)


def mapcal_path(args):
    p = getattr(args, "mapcal_file", None)
    if p is None:
        d = os.path.normpath(os.path.join(deps._HERE, os.pardir, "data"))
        p = os.path.join(d if os.path.isdir(d) else deps._HERE,
                         "fe_minimap_cal.json")
    return p


def _mapcal_anchor_rows(v):
    out = []
    for r in v or []:
        try:
            out.append({"x": float(r["x"]), "z": float(r["z"]),
                        "px": float(r["px"]), "py": float(r["py"]),
                        "note": str(r.get("note", "") or "")})
        except (TypeError, ValueError, KeyError):
            continue
    return out


def mapcal_load(args):
    global _MAPCAL
    out = {}
    try:
        with open(mapcal_path(args), encoding="utf-8") as fh:
            for k, v in (json.load(fh) or {}).items():
                rows = _mapcal_anchor_rows(v)
                if rows:
                    out[str(k)] = rows
    except (OSError, ValueError):
        pass
    with _MAPCAL_LOCK:
        _MAPCAL = out
    return out


def mapcal_save(args):
    p = mapcal_path(args)
    # os.devnull means "do not persist". Writing a temp file beside it and
    # renaming onto `nul` fails on Windows and strands nul.tmp.<pid> in the
    # working directory -- 33 of them had piled up from test runs.
    if not p or p == os.devnull:
        return
    with _MAPCAL_LOCK:
        snap = {k: list(v) for k, v in _MAPCAL.items()}
    tmp = "%s.tmp.%d" % (p, os.getpid())
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(snap, indent=1, sort_keys=True))
        os.replace(tmp, p)
    except OSError as e:
        print("[feworld] minimap calibration save failed: %s" % e, flush=True)


def mapcal_anchors(stem):
    """The shipped default anchors for this half, with the admin's on top.

    An anchor the admin placed REPLACES a shipped one at the same world
    point and otherwise adds to them: somebody standing in the town is better
    evidence than a door matched by shape, and more evidence is better than
    less.
    """
    if not stem:
        return []
    rows = list(fegamedata.minimap_anchors(stem))
    with _MAPCAL_LOCK:
        mine = list(_MAPCAL.get(str(stem), []))
    for m in mine:
        for i, r in enumerate(rows):
            if abs(r["x"] - m["x"]) < 0.05 and abs(r["z"] - m["z"]) < 0.05:
                rows[i] = m
                break
        else:
            rows.append(m)
    return rows


def _fit_axis(pairs, a_def, c_def):
    """Solve `p = w / a + c` for one axis. Returns (a, c, how).

    Conservative in the order a measurement beats a guess: nothing -> the
    shipped fit; one anchor -> shipped SCALE with a solved OFFSET, which is the
    parameter that actually differs between maps; two or more that span enough
    of the map -> both, by least squares.
    """
    if not pairs:
        return a_def, c_def, "shipped"
    if len(pairs) == 1:
        w, p = pairs[0]
        return a_def, p - w / a_def, "offset from 1 anchor"
    ws = [w for w, _ in pairs]
    ps = [p for _, p in pairs]
    span = max(ps) - min(ps)
    off = sum(p - w / a_def for w, p in pairs) / len(pairs)
    if span < MAPCAL_MIN_PX_SPAN:
        return a_def, off, ("offset from %d anchors (they are only %.0f px "
                            "apart on the art -- too close to fit a scale)"
                            % (len(pairs), span))
    mw = sum(ws) / len(ws)
    mp = sum(p for _, p in pairs) / len(pairs)
    var = sum((w - mw) ** 2 for w in ws)
    cov = sum((w - mw) * (p - mp) for w, p in pairs)
    if var <= 0 or cov == 0:
        return a_def, off, "offset only (degenerate anchors)"
    u = cov / var                      # u = 1/a
    if u == 0:
        return a_def, off, "offset only (degenerate anchors)"
    a = 1.0 / u
    lo, hi = MAPCAL_SCALE_BAND
    ratio = (a / a_def) if a_def else 0.0
    if ratio < lo or ratio > hi:
        return a_def, off, ("offset only (a scale of %.4f is %.2fx the shipped"
                            " %.4f -- that is a bad anchor, not a discovery)"
                            % (a, ratio, a_def))
    return a, mp - u * mw, "scale+offset from %d anchors" % len(pairs)


def mapcal_proj(stem):
    """The effective pixel<->world mapping for one map stem."""
    proj = {"ax": fegamedata.MINIMAP_AX, "cx": fegamedata.MINIMAP_CX,
            "az": fegamedata.MINIMAP_AZ, "cz": fegamedata.MINIMAP_CZ,
            "size": fegamedata.MINIMAP_SIZE, "stem": stem or "",
            "anchors": 0, "how_x": "shipped", "how_z": "shipped"}
    rows = mapcal_anchors(stem)
    if not rows:
        return proj
    ax, cx, hx = _fit_axis([(r["x"], r["px"]) for r in rows],
                           fegamedata.MINIMAP_AX, fegamedata.MINIMAP_CX)
    az, cz, hz = _fit_axis([(r["z"], r["py"]) for r in rows],
                           fegamedata.MINIMAP_AZ, fegamedata.MINIMAP_CZ)
    # KEY: BORROW A SCALE ACROSS THE AXES. A minimap does not stretch one axis:
    # measured across the halves we can fit, |az/ax| runs 0.96 to 1.045, and
    # the SHIPPED constants are themselves 2.4646 / 2.4625. So when one axis
    # has the span to be fitted and the other does not, the near-square prior
    # beats keeping a shipped scale we have just proved wrong on this map.
    # On map00 that is the difference between ~1 px and ~35 px of error:
    # its doors span 137 px in x (solid) but only 19 px in z.
    #
    # Only ever borrow ONTO an axis that fell back -- never over a scale that
    # was actually measured. On map03 the free z fit is 0.03 px and isotropy
    # would be 3.8 px, so measurement wins there, as it must.
    fit_x = "scale+offset" in hx
    fit_z = "scale+offset" in hz
    if fit_x and not fit_z:
        az = -abs(ax)
        cz = sum(r["py"] - r["z"] / az for r in rows) / len(rows)
        hz = "offset, scale borrowed from x (%s)" % hz
    elif fit_z and not fit_x:
        ax = abs(az)
        cx = sum(r["px"] - r["x"] / ax for r in rows) / len(rows)
        hx = "offset, scale borrowed from z (%s)" % hx
    proj.update({"ax": ax, "cx": cx, "az": az, "cz": cz,
                 "anchors": len(rows), "how_x": hx, "how_z": hz})
    return proj


def mapcal_residuals(stem):
    """Per-anchor error in pixels under the CURRENT solve, worst first.

    A fit that is never checked against its own evidence is decoration, and one
    mis-drag drags the whole map with it. This is what says which anchor.
    """
    rows = mapcal_anchors(stem)
    if not rows:
        return []
    p = mapcal_proj(stem)
    out = []
    for r in rows:
        px = r["x"] / p["ax"] + p["cx"]
        py = r["z"] / p["az"] + p["cz"]
        out.append({"note": r["note"], "x": r["x"], "z": r["z"],
                    "px": r["px"], "py": r["py"],
                    "d": ((px - r["px"]) ** 2 + (py - r["py"]) ** 2) ** 0.5})
    out.sort(key=lambda r: -r["d"])
    return out


def mapcal_add(args, stem, x, z, px, py, note=""):
    stem = str(stem)
    row = {"x": float(x), "z": float(z), "px": float(px), "py": float(py),
           "note": str(note or "")}
    with _MAPCAL_LOCK:
        rows = _MAPCAL.setdefault(stem, [])
        # One anchor per world point: dragging the same door twice is a
        # CORRECTION, not a second opinion, and keeping both would average the
        # mistake back in.
        for i, r in enumerate(rows):
            if abs(r["x"] - row["x"]) < 0.05 and abs(r["z"] - row["z"]) < 0.05:
                rows[i] = row
                break
        else:
            rows.append(row)
    mapcal_save(args)
    return mapcal_proj(stem)


def mapcal_drop(args, stem, idx=None):
    """Drop one anchor by index, or every anchor for the stem."""
    stem = str(stem)
    with _MAPCAL_LOCK:
        rows = _MAPCAL.get(stem, [])
        if idx is None:
            gone = len(rows)
            _MAPCAL.pop(stem, None)
        elif 0 <= int(idx) < len(rows):
            rows.pop(int(idx))
            gone = 1
            if not rows:
                _MAPCAL.pop(stem, None)
        else:
            gone = 0
    if gone:
        mapcal_save(args)
    return gone


def _mapcal_line(stem):
    """One line describing where a half's projection currently comes from."""
    p = mapcal_proj(stem)
    if not p["anchors"]:
        return ("%s has no anchors -- using the shipped fit (sub-pixel on six "
                "of the ten halves, 15-33 px out on 21/91/57/93)" % stem)
    res = mapcal_residuals(stem)
    return ("%s from %d anchor(s): x %s, z %s; worst anchor off by %.1f px"
            % (stem, p["anchors"], p["how_x"], p["how_z"],
               res[0]["d"] if res else 0.0))
