"""The world-building panel's state and edits (served by fedevtool.py)."""
import math
import threading
import fedevtool  # noqa: E402  -- the world-building panel (--devtool-port)
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import doors, mapcal, sess, spawns, territory, town, zones

#: WARNING: _SESSION IS THREAD-LOCAL. It is backed by _TLS.session, one dict per
#: connection thread, and the panel's HTTP handler runs on its OWN thread --
#: so reading _SESSION from there gets an EMPTY session and the panel would
#: have reported "not in a field" forever, with nothing obviously wrong.
#:
#: So the player's thread publishes what the panel needs, once per inbound
#: frame from gm_pump, and the panel reads the snapshot. At most one frame
#: stale (~400 ms in the field), which is well inside the panel's own 1 s poll.
#: Everything else devtool_state touches -- the stores, the shipped tables --
#: is already process-global and safe to read from either thread.
_DEVSNAP = {}
_DEVSNAP_LOCK = threading.Lock()


def devtool_snapshot():
    """Publish this connection's position for the panel. Player thread only."""
    snap = {"in_field": bool(sess._SESSION.get("in_field")),
            "field": sess._SESSION.get("field"),
            "cpos": sess._SESSION.get("cpos"),
            "arrived_via": sess._SESSION.get("arrived_via")}
    with _DEVSNAP_LOCK:
        _DEVSNAP.clear()
        _DEVSNAP.update(snap)


def devtool_state(args, area=None):
    """Everything the world-building panel draws, as one dict.

    Built HERE rather than in fedevtool because every piece of it is feworld's:
    the session, the stores, the shipped tables. fedevtool owns the socket and
    the page and knows nothing about the world.
    """
    with _DEVSNAP_LOCK:
        snap = dict(_DEVSNAP)
    fld = snap.get("field")
    pos = snap.get("cpos")
    st = {"in_field": bool(snap.get("in_field")), "area": fld,
          "area_name": "", "hmap": None, "ground": None, "holder": None,
          "pos": list(pos) if pos else None,
          "roster_total": 0, "roster_placed": 0, "missing": [],
          "doors_total": 0, "doors_mapped": 0, "nearest_door": None,
          "monsters": 0, "spawns": [], "destinations": [],
          "minimap": None, "proj": None, "icons": [], "npcs": [], "gates": [],
          "arrived_via": snap.get("arrived_via"),
          "pending": fedevtool.pending()}
    # the capital the editor is showing: asked for, else where the player is
    try:
        st["focus"] = int(area) if area not in (None, "") else fld
    except (TypeError, ValueError):
        st["focus"] = fld
    if st["focus"] is not None and not fegamedata.minimap_name(st["focus"]):
        st["focus"] = None
    st["capitals"] = devtool_capitals(fld)
    if fld is None:
        st["halves"] = _devtool_halves(args, st["focus"], fld)
        return st
    exp = fegamedata.area_expected(int(fld))
    if exp is None:
        st["area_name"] = "area %s" % fld
        st["halves"] = _devtool_halves(args, st["focus"], fld)
        return st
    st["area_name"] = fegamedata.area_names_en().get(int(fld)) or exp["name"]
    st["hmap"] = exp["hmap"]
    st["ground"] = exp["ground"]
    st["holder"] = args.force_table.get(territory.territory_owner(fld, args), {}).get(
        "name") or territory.territory_owner(fld, args)
    st["monsters"] = len(exp["monsters"])
    placed = town.town_get(args, int(fld), "npcs")
    have_s = {int(n.get("script") or 0) for n in placed}
    have_n = {str(n.get("name") or "") for n in placed}
    st["roster_total"] = len(exp["roster"])
    for r in exp["roster"]:
        if r["script"] in have_s or r["name"] in have_n:
            st["roster_placed"] += 1
        else:
            st["missing"].append({"name": r["name"], "script": r["script"],
                                  "model": r["modeltype"]})
    mapped = town.town_get(args, int(fld), "doors")
    st["doors_total"] = len(exp["doors"])
    near = []
    for d in exp["doors"]:
        if any(abs(float(m.get("x", 1e9)) - d["gate"][0]) <= args.door_radius
               and abs(float(m.get("z", 1e9)) - d["gate"][1]) <= args.door_radius
               for m in mapped):
            st["doors_mapped"] += 1
        if pos:
            near.append(((d["gate"][0] - pos[0]) ** 2
                         + (d["gate"][1] - pos[2]) ** 2) ** 0.5)
    if near:
        st["nearest_door"] = min(near)
    st["spawns"] = [dict(r) for r in spawns.spawn_rows_for(int(fld))]
    # KEY: WHERE COULD THIS DOOR GO? An id box is a memory test -- "was Azurwood's
    # other half 92 or 93?" -- and getting it wrong is not an error message,
    # it is a fall into the void (a capital's partner id read one row off once
    # put a player at y=21128 with nothing but skybox). So offer the answers
    # the tables already know, named, in the order they are likely:
    #   1. the OTHER HALF of this capital -- what a capital door is actually
    #      for, proved live 2026-09-06
    #   2. this area's own neighbours from fet_area
    #   3. wherever the shipped portal table says its doors go
    # A room is not in this list: rooms are --field-rooms, not a destination.
    seen_d, dests = set(), []

    def _offer(a, why):
        a = int(a)
        if a in seen_d or a not in fegamedata.areas():
            return
        seen_d.add(a)
        row = fegamedata.areas()[a]
        dests.append({"area": a, "why": why,
                      "name": fegamedata.area_names_en().get(a) or row["name"],
                      "capital": bool(row["capital"])})

    twin = dict(list(zones.INNER_CAPITAL_PAIRS.items())
                + [(v, k) for k, v in zones.INNER_CAPITAL_PAIRS.items()]).get(int(fld))
    if twin:
        _offer(twin, "the other half of this capital")
    for d in exp["doors"]:
        p = fegamedata.portal(d["dest"])
        if p:
            _offer(p["area"], "a shipped door here goes there")
    for n in fegamedata.areas()[int(fld)]["neighbours"]:
        _offer(n, "borders this area")
    st["destinations"] = dests
    # ---- everything the panel needs to DRAW this area's minimap ----------
    # KEY: The in-game minimap is too small to tell a sword from a ring, and
    # those icons are the best evidence of what belongs where. Serving the art
    # plus the projection lets the panel show it at any size with the
    # recovered positions, the shipped doors and the player all on it.
    st["minimap"] = fegamedata.minimap_name(fld)
    st["proj"] = mapcal.mapcal_proj(st["minimap"])
    st["icons"] = _devtool_icons(fld)
    st["npcs"] = _devtool_npcs(placed)
    st["gates"] = _devtool_gates(exp, st["proj"])
    # KEY: A CAPITAL IS TWO MAPS AND THE PANEL SHOWED ONE. The roster, the doors
    # and the painted shop icons all live per HALF, so standing in 21 hid
    # everything about 91 -- including the half whose alignment you might be
    # trying to fix. Serve both, each with its OWN projection, and let the page
    # switch. The live area is first so the default view is where you are.
    st["halves"] = _devtool_halves(args, st["focus"], fld)
    return st


def _devtool_halves(args, focus, live_area):
    """Both halves of the capital `focus` belongs to, `focus` first.

    KEY: BUILT FOR ANY CAPITAL, NOT ONLY THE ONE A PLAYER STANDS IN. The editor
    used to need a live session for two reasons: the map was built around the
    player's area, and a click could not be given a HEIGHT. The ground grid
    (fegamedata.capital_ground) removed the second, and this removes the
    first -- so the panel can open any capital half with nobody logged in.
    `live` says whether a player is standing in that half right now; edits
    there reach the client at once (see town_live_refresh).
    """
    if focus is None or not fegamedata.minimap_name(focus):
        return []
    twin_h = dict(list(zones.INNER_CAPITAL_PAIRS.items())
                  + [(v, k) for k, v in zones.INNER_CAPITAL_PAIRS.items()])
    out = []
    for a_h in [int(focus)] + ([twin_h[int(focus)]]
                               if int(focus) in twin_h else []):
        stem_h = fegamedata.minimap_name(a_h)
        exp_h = fegamedata.area_expected(a_h)
        if not stem_h or exp_h is None:
            continue
        placed_h = town.town_get(args, a_h, "npcs")
        roster_h = exp_h["roster"]
        have_sh = {int(n.get("script") or 0) for n in placed_h}
        have_nh = {str(n.get("name") or "") for n in placed_h}
        proj_h = mapcal.mapcal_proj(stem_h)
        out.append({
            "area": a_h,
            "live": live_area is not None and a_h == int(live_area),
            "minimap": stem_h,
            "name": fegamedata.area_names_en().get(a_h) or exp_h["name"],
            "inner": a_h in zones.INNER_CAPITAL_PAIRS.values(),
            "proj": proj_h,
            "icons": _devtool_icons(a_h),
            "npcs": _devtool_npcs(placed_h),
            "gates": _devtool_gates(exp_h, proj_h),
            "spawns": [dict(r) for r in spawns.spawn_rows_for(a_h)],
            "roles": [r["name"] for r in roster_h]
                     + [t["name"] for t in fegamedata.townsfolk(a_h, capital=True)],
            "district": fegamedata.capital_district(a_h),
            "townsfolk": [{"name": t["name"], "script": t["script"],
                           "kind": t["kind"], "x": t["x"], "z": t["z"],
                           "yaw": t["yaw"], "placed": t["script"] in have_sh}
                          for t in fegamedata.townsfolk(a_h)],
            "roster_total": len(roster_h),
            "roster_placed": sum(1 for r in roster_h
                                 if r["script"] in have_sh
                                 or r["name"] in have_nh),
            "missing": [{"name": r["name"], "script": r["script"],
                         "model": r["modeltype"]} for r in roster_h
                        if r["script"] not in have_sh
                        and r["name"] not in have_nh],
            "cal": mapcal.mapcal_residuals(stem_h),
        })
    return out


_FLOOR_CACHE = {}


def devtool_floor(args, area):
    """Which of a half's 256x256 minimap pixels have FLOOR under them.

    KEY: WHY THE EDITOR NEEDS TO SEE THIS. A click on the art is refused when the
    capital's collision has nothing to stand on there -- correctly, since a
    landing on nothing hangs the client -- and live testing reported it as
    "sometimes it just doesn't place, kind of random". It is not random: the
    art shows a road, and the exact pixel under the click is off the walkable
    mesh (an edge, a wall top, decoration), or the half's alignment puts the
    art a few pixels away from the world. Drawing the floor over the art shows
    both at once: where a click will succeed, and where the art and the world
    disagree.

    Sampled at each pixel's CENTRE through the half's CURRENT projection, so
    it moves with the alignment. Cached per projection; ~65k grid reads.
    """
    import base64
    area = int(area)
    stem = fegamedata.minimap_name(area)
    if not stem:
        return None
    p = mapcal.mapcal_proj(stem)
    key = (area, round(p["ax"], 6), round(p["cx"], 6), round(p["az"], 6),
           round(p["cz"], 6))
    if key in _FLOOR_CACHE:
        return _FLOOR_CACHE[key]
    size = int(p.get("size") or 256)
    bits = bytearray(size * size // 8)
    n = 0
    for py in range(size):
        z = (py + 0.5 - p["cz"]) * p["az"]
        for px in range(size):
            x = (px + 0.5 - p["cx"]) * p["ax"]
            if fegamedata.capital_ground(area, x, z) is not None:
                i = py * size + px
                bits[i >> 3] |= 1 << (i & 7)
                n += 1
    res = {"area": area, "stem": stem, "size": size,
           "proj": [p["ax"], p["cx"], p["az"], p["cz"]],
           "floor_px": n, "bits": base64.b64encode(bytes(bits)).decode("ascii")}
    _FLOOR_CACHE.clear() if len(_FLOOR_CACHE) > 40 else None
    _FLOOR_CACHE[key] = res
    return res


def devtool_capitals(live_area):
    """The five capitals as the editor's picker lists them."""
    out = []
    for outer, inner in sorted(zones.INNER_CAPITAL_PAIRS.items()):
        out.append({"area": outer, "inner": inner,
                    "name": fegamedata.area_names_en().get(outer)
                    or "area %d" % outer,
                    "here": live_area in (outer, inner)})
    return out


def _devtool_icons(area):
    """The painted shop icons, carrying the PIXEL they were measured at.

    WARNING: The panel must draw these at px/py and never re-project x/z. x/z were
    DERIVED from px/py through the shipped fit; re-projecting them through a
    calibrated fit moves every icon off the art it was read from, which is the
    opposite of what aligning the map is for.
    """
    return [{"role": i["role"], "kind": i["kind"], "colour": i["colour"],
             "x": i["x"], "z": i["z"], "px": i["px"], "py": i["py"],
             "trust": i["trust"]}
            for i in fegamedata.shop_icons(area, trusted_only=False)]


def _devtool_npcs(placed):
    """The placed NPCs, WITH their row index -- the index is what the panel
    sends back when you drag one, because `!move` picks the nearest to where
    you are STANDING and a drag has no standing."""
    out = []
    for i, n in enumerate(placed):
        if n.get("x") is None or n.get("z") is None:
            continue
        tf = fegamedata.townsfolk_by_script(n.get("script"))
        out.append({"idx": i,
                    "name": n.get("name") or "model %s" % n.get("model"),
                    "x": float(n.get("x", 0)), "z": float(n.get("z", 0)),
                    "y": float(n.get("y", 0) or 0),
                    "yaw": float(n.get("yaw", 0) or 0),
                    "model": n.get("model"), "script": n.get("script"),
                    "says": (n.get("event") or (tf and tf["line_en"]) or ""),
                    "home": tf and tf["district"] or ""})
    return out


def _devtool_gates(exp, proj=None):
    """The shipped doors of one half, each with BOTH journeys through it.

    A door is walked through in two directions, and each lands somewhere
    different -- and since 2026-09-11 we know which is which: walking through a
    portal puts you down at THAT portal's shipped spawn, in its destination
    area (see dat_door_for). So for a door D standing in this half:

      ax/az     where you land IN THIS HALF when you come through D's partner
                (portal D.dest, on the other side) -- beside D, on this map,
                on this map's collision. Edited under `in_key` = D.dest.
      out_*     where walking through D puts you, in the OTHER half. Shown as
                text; it is drawn on the other half's map as that side's `ax`.

    x/z is the doorway itself -- shipped, and the anchor the calibration is
    dragged against. Only the landings are editable.
    """
    out = []
    for d in exp["doors"]:
        # Name where it comes out. A yellow dot among yellow dots is not a
        # door anybody can identify.
        dp = fegamedata.portal(d["dest"])
        dn = ""
        if dp is not None:
            dn = (fegamedata.area_names_en().get(dp["area"])
                  or (fegamedata.areas().get(dp["area"]) or {}).get("name")
                  or "area %d" % dp["area"])
        grid = ""
        if proj:
            grid = fegamedata.minimap_grid(d["gate"][0] / proj["ax"] + proj["cx"],
                                           d["gate"][1] / proj["az"] + proj["cz"])
        # into this half: the partner's journey
        ov_in = doors.doorarr_get(dp["portal"]) if dp is not None else None
        ship_in = dp["spawn"] if dp is not None else d["gate"]
        land_in = ov_in if ov_in is not None else ship_in
        # out of this half: this door's own journey
        ov_out = doors.doorarr_get(d["portal"])
        land_out = ov_out if ov_out is not None else d["spawn"]
        out.append({"x": d["gate"][0], "z": d["gate"][1], "dest": d["dest"],
                    "portal": d["portal"], "radius": d["radius"],
                    "grid": grid,
                    "dest_area": None if dp is None else dp["area"],
                    "dest_name": dn,
                    "in_key": None if dp is None else dp["portal"],
                    "ax": land_in[0], "az": land_in[1],
                    "sax": ship_in[0], "saz": ship_in[1],
                    "moved": ov_in is not None,
                    "in_ground": fegamedata.capital_ground(d["area"],
                                                           land_in[0],
                                                           land_in[1]),
                    "out_ax": land_out[0], "out_az": land_out[1],
                    "out_moved": ov_out is not None})
    return out


def capital_people(area):
    """Everyone who may stand in this capital: the 20 roster roles, then the
    townsfolk (both halves) -- [{name, script, modeltype}]."""
    return (list(fegamedata.capital_roster(area))
            + fegamedata.townsfolk(area, capital=True))


def townsfolk_place(args, area):
    """Put this half's townsfolk on their spots -- the ones not already here.

    The spots are CHOSEN (fe-townsfolk.tsv: SE's positions did not ship) and
    were picked clear of the NPCs that stood here when the table was built;
    one that has since gained a neighbour within 2 units, or lost its floor, is
    SKIPPED and named rather than stacked. Idempotent: a townsperson already
    in this half (by script) is left where the admin put them."""
    rows = town.town_get(args, area, "npcs")
    have = {int(r.get("script") or 0) for r in rows}
    spots = [(float(r["x"]), float(r["z"])) for r in rows
             if r.get("x") is not None and r.get("z") is not None]
    placed, skipped, already = [], [], 0
    for t in fegamedata.townsfolk(area):
        if t["script"] in have:
            already += 1
            continue
        if t["x"] is None:
            skipped.append("%s (no spot)" % t["name"])
            continue
        gy = fegamedata.capital_ground(area, t["x"], t["z"])
        if gy is None or any(math.hypot(t["x"] - sx, t["z"] - sz) < 2.0
                             for sx, sz in spots):
            skipped.append("%s (%s)" % (t["name"], "no floor" if gy is None
                                        else "spot taken"))
            continue
        town.town_add(args, area, "npcs",
                 {"model": t["modeltype"], "kind": 1, "level": 1,
                  "x": round(t["x"], 1), "y": round(gy, 2),
                  "z": round(t["z"], 1), "yaw": float(t["yaw"] or 0),
                  "name": t["name"], "script": t["script"]})
        spots.append((t["x"], t["z"]))
        placed.append(t["name"])
    msg = "placed %d townsfolk in %s (%s)" % (
        len(placed), fegamedata.capital_district(area) or area, area)
    if already:
        msg += "; %d already here" % already
    if skipped:
        msg += "; skipped %s" % ", ".join(skipped)
    return {"ok": bool(placed) or not skipped, "msg": msg, "placed": placed,
            "skipped": skipped}


def devtool_edit(args, op):
    """Apply one editor change DIRECTLY -- no game session needed.

    `op` is a dict from the panel: {"op": ..., ...}. Returns {"ok", "msg"}.

    KEY: THE HEIGHT COMES FROM THE CAPITAL'S OWN COLLISION. That is what made
    offline editing possible: the minimap is a plan view, and before
    fe-capital-ground.json the only way to know the floor under a click was
    a spawn row somebody had stood on. A click with no floor under it is
    REFUSED -- an NPC floating over nothing is merely wrong, but the same
    spot as a door landing hangs the client (see capital_ground), so the
    editor never learns to accept one.

    Runs on the panel's HTTP thread. The stores all take their own locks; the
    one thing this thread cannot do is talk to a client, so an NPC change in
    an area a player is standing in is flagged, and that player's own thread
    pushes it (town_live_refresh) the next time it wakes.
    """
    kind = str(op.get("op") or "")

    def fail(msg):
        return {"ok": False, "msg": msg}

    def num(k):
        try:
            return float(op[k])
        except (KeyError, TypeError, ValueError):
            raise ValueError(k)
    try:
        if kind in ("place", "move", "turn", "remove", "spawn", "retype",
                    "townsfolk"):
            area = int(op["area"])
            if not fegamedata.minimap_name(area):
                return fail("area %s is not a capital half -- the editor only "
                            "knows capitals' floors" % area)
        if kind in ("place", "move", "spawn"):
            x, z = num("x"), num("z")
            gy = fegamedata.capital_ground(area, x, z)
            if gy is None:
                return fail("no floor at (%.1f, %.1f) in area %d -- pick a "
                            "spot on the ground" % (x, z, area))
        if kind == "townsfolk":
            return townsfolk_place(args, area)
        if kind == "place":
            role = next((r for r in capital_people(area)
                         if r["name"] == op.get("role")), None)
            if role is None:
                return fail("%r is not one of area %d's roles"
                            % (op.get("role"), area))
            yaw = float(op.get("yaw") or 0.0) % 360.0
            town.town_add(args, area, "npcs",
                     {"model": role["modeltype"], "kind": 1, "level": 1,
                      "x": round(x, 1), "y": round(gy, 2), "z": round(z, 1),
                      "yaw": yaw, "name": role["name"],
                      "script": role["script"]})
            return {"ok": True, "msg": "placed %s at (%.1f, %.1f), floor %.1f"
                    % (role["name"], x, z, gy)}
        if kind in ("move", "turn", "remove", "retype"):
            rows = town.town_get(args, area, "npcs")
            idx = int(op["idx"])
            if not 0 <= idx < len(rows):
                return fail("area %d has no NPC row %d" % (area, idx))
            who = rows[idx].get("name") or "model %s" % rows[idx].get("model")
            if kind == "move":
                town.town_set(args, area, "npcs", idx,
                         {"x": round(x, 1), "y": round(gy, 2),
                          "z": round(z, 1)})
                return {"ok": True, "msg": "moved %s to (%.1f, %.1f), floor %.1f"
                        % (who, x, z, gy)}
            if kind == "retype":
                # Same spot, same facing -- only WHO it is changes: the model,
                # the name and the script id all come from the shipped roster,
                # never from the request, so a retype cannot invent an NPC.
                role = next((r for r in capital_people(area)
                             if r["name"] == op.get("role")), None)
                if role is None:
                    return fail("%r is not one of area %d's roles"
                                % (op.get("role"), area))
                town.town_set(args, area, "npcs", idx,
                         {"model": role["modeltype"], "name": role["name"],
                          "script": role["script"]})
                return {"ok": True, "msg": "%s is now %s (model %d, script %d)"
                        % (who, role["name"], role["modeltype"],
                           role["script"])}
            if kind == "turn":
                yaw = num("yaw") % 360.0
                town.town_set(args, area, "npcs", idx, {"yaw": round(yaw, 1)})
                return {"ok": True, "msg": "%s now faces %g°" % (who, yaw)}
            town.town_del(args, area, "npcs", idx)
            return {"ok": True, "msg": "removed %s -- it is placeable again"
                    % who}
        if kind == "spawn":
            tag = str(op.get("tag") or "")
            spawns.spawn_add(args, area, (x, gy, z), tag)
            return {"ok": True, "msg": "area %d%s arrives at (%.1f, %.1f, %.1f)"
                    % (area, " [%s]" % tag if tag else "", x, gy, z)}
        if kind == "arrive":
            ok, msg = doors.arrive_set(args, int(op["portal"]), num("x"), num("z"))
            return {"ok": ok, "msg": msg}
        if kind == "arrive_drop":
            n = doors.doorarr_drop(args, int(op["portal"]))
            return {"ok": True, "msg": "portal %s hands out SE's arrival again"
                    % op["portal"] if n else "nothing to reset"}
        return fail("unknown edit %r" % kind)
    except ValueError as e:
        return fail("missing or bad field %s" % e)
    except (KeyError, TypeError) as e:
        return fail("missing field %s" % e)
