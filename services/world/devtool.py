"""The world-building panel's state and edits (served by fedevtool.py)."""
import collections
import math
import sys
import threading
import fedevtool  # noqa: E402  -- the world-building panel (--devtool-port)
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import character, doors, mapcal, sess, spawns, territory, timedevents, town, zones

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
          "arrived_via": snap.get("arrived_via"), "nation": None,
          "pending": fedevtool.pending()}
    # the capital the editor is showing: asked for, else where the player is
    try:
        st["focus"] = int(area) if area not in (None, "") else fld
    except (TypeError, ValueError):
        st["focus"] = fld
    if st["focus"] is not None and not fegamedata.minimap_name(st["focus"]):
        st["focus"] = None
    st["capitals"] = devtool_capitals(fld, args)
    st["functions"] = [{"key": k, "label": v} for k, v in NPC_FUNCTIONS]
    st["undo"] = undo_list()
    if fld is None:
        st["halves"] = _devtool_halves(args, st["focus"], fld)
        return st
    exp = fegamedata.area_expected(int(fld))
    if exp is None:
        st["area_name"] = "area %s" % fld
        st["halves"] = _devtool_halves(args, st["focus"], fld)
        return st
    st["area_name"] = fegamedata.area_names_en().get(int(fld)) or exp["name"]
    st["nation"] = capital_nation(args, fld)
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
                                  "model": r["modeltype"],
                                  "fn": npc_function(r["name"], r["script"])[0]})
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
                      "capital": bool(row["capital"]),
                      "nation": capital_nation(args, a)})

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
            "nation": capital_nation(args, a_h),
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
                           "fn": npc_function(t["name"], t["script"])[0],
                           "yaw": t["yaw"], "placed": t["script"] in have_sh,
                           "silent": not (t["line_en"] or "").strip()}
                          for t in fegamedata.townsfolk(a_h)],
            "roster_total": len(roster_h),
            "roster_placed": sum(1 for r in roster_h
                                 if r["script"] in have_sh
                                 or r["name"] in have_nh),
            "missing": [{"name": r["name"], "script": r["script"],
                         "model": r["modeltype"],
                         "fn": npc_function(r["name"], r["script"])[0]}
                        for r in roster_h
                        if r["script"] not in have_sh
                        and r["name"] not in have_nh],
            "cal": mapcal.mapcal_residuals(stem_h),
            # the missing roles' painted spots: the page draws them faint
            "ghosts": [{k: g[k] for k in ("name", "fn", "x", "z", "y", "px", "py")}
                       for g in shop_spots(args, a_h) if not g["placed"]],
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


def devtool_capitals(live_area, args=None):
    """The five capitals as the editor's picker lists them, each with the
    nation it belongs to (capital_nation)."""
    out = []
    for outer, inner in sorted(zones.INNER_CAPITAL_PAIRS.items()):
        tf = fegamedata.townsfolk(outer, capital=True)
        out.append({"area": outer, "inner": inner,
                    "name": fegamedata.area_names_en().get(outer)
                    or "area %d" % outer,
                    "here": live_area in (outer, inner),
                    "nation": capital_nation(args, outer),
                    # townsfolk whose words were never recorded (they say a
                    # generic line in game): how many, of how many
                    "townsfolk": len(tf), "silent": sum(
                        1 for t in tf if not (t["line_en"] or "").strip())})
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
        fn, fn_detail = npc_function(n.get("name"), n.get("script"), n.get("event"))
        out.append({"idx": i, "fn": fn, "fn_detail": fn_detail,
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


#: What an NPC is FOR, in the order the editor's legend lists them. The key
#: is what the page colours by; the label is what it says. Every key comes
#: from a branch the talk dispatcher (events.event_script_for) really takes, so the
#: colour says what talking to the NPC DOES on this server, not what its name
#: suggests. "townsfolk" is the ambient villager who says one line; "unknown"
#: is a row whose script and name match nothing the server knows (a `!npc`
#: by model number, say).
NPC_FUNCTIONS = [("shop", "Shop"), ("bank", "Bank"), ("inn", "Inn"),
                 ("warp", "Warp"),
                 ("manager", "Manager"), ("trainer", "Trainer"),
                 ("prize", "Prize clerk"), ("quest", "Quest"),
                 ("weapon", "Free weapon"), ("guide", "Guide"),
                 ("guard", "Guard"), ("townsfolk", "Townsfolk"),
                 ("unknown", "Unknown")]
#: the townsfolk table's `kind` column -> function. A porter, a guard and a
#: gatekeeper all stand somewhere to turn people away or point the way out.
_TF_KIND = {"weapon": "weapon", "porter": "guard", "guard": "guard",
            "gate": "guard", "market": "guide", "villager": "townsfolk"}
_QUEST_WHAT = {"cheese": "wants cheese", "goblin_book": "wants a Goblin Book",
               "luck": "luck draw"}
#: the roster slot whose line is the class talk (staff.ROLE_LINES[(3, 4)])
_ROLE_TRAINER = (3, 4)


def npc_function(name, script, event=None):
    """(function key, short detail) for one NPC, by its script id and name.

    Follows events.event_script_for's order: a row's own `event` override
    first (goto = a warp, shop / bank), then a quest giver, then a townsperson
    by the townsfolk table's kind, then the shop / bank / inn names, then the
    roster slot (managers, prize clerk, trainer, the slots that say a set
    line). Anything else is "unknown" rather than a guess."""
    from . import events, staff
    try:
        s = int(script or 0)
    except (TypeError, ValueError):
        s = 0
    name = str(name or "")
    if event:
        kinds = [p.strip().partition(":") for p in str(event).split(";")]
        for k, _, v in kinds:
            if k == "goto":
                return "warp", "to %s" % v.strip()
        for k, _, v in kinds:
            if k == "shop":
                return "shop", "shop window %s" % (v.strip() or "1")
            if k == "bank":
                return "bank", ""
        return "guide", "says a set line"
    q = events.QUEST_BY_GIVER.get(s)
    if q is not None:
        return "quest", _QUEST_WHAT.get(q, q)
    tf = fegamedata.townsfolk_by_script(s) if s else None
    if tf is not None:
        fn = _TF_KIND.get(tf["kind"], "townsfolk")
        return fn, "" if fn == "townsfolk" else tf["kind"]
    low = name.lower()
    for key in ("weapon", "armor", "ring", "item"):
        if key in low:
            return "shop", ""
    if name == "Bank_Keeper":
        return "bank", ""
    if name == "Inn_Master":
        return "inn", ""
    slot = staff.role_slot({"script": s}) if s else None
    if slot in staff.ROLE_MANAGERS:
        return "manager", "the King's message"
    if slot == staff.ROLE_PRIZE_CLERK:
        return "prize", "war rewards"
    if slot == _ROLE_TRAINER:
        return "trainer", "class advice"
    if slot in staff.ROLE_LINES:
        return "guide", "says a set line"
    return "unknown", ""


def capital_nation(args, area):
    """{"id", "name", "color"} of the nation a capital half belongs to, or None
    for an area that is not a capital.

    The id is dat.pak's own (fet_area's nation column); the name is the one the
    server serves the client (--forces, the client's own names); the colour is
    the client's nation palette (femap.PALETTE, 0x5336fe8)."""
    try:
        row = fegamedata.areas().get(int(area))
    except (TypeError, ValueError):
        return None
    if not row or not row["capital"] or not row["nation"]:
        return None
    nid = int(row["nation"])
    name = ((getattr(args, "force_table", None) or {}).get(nid) or {}).get("name")
    try:
        import femap
        rgb = femap.PALETTE.get(nid)
    except Exception:                                  # noqa: BLE001
        rgb = None
    return {"id": nid, "name": name or "Nation %d" % nid,
            "color": "#%02x%02x%02x" % rgb if rgb else ""}


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


#: The editor's UNDO: the last NPC edits made through the panel, newest
#: last, each {"area", "before", "after", "label"} -- the half's whole NPC
#: list either side of the edit. Server-wide (every admin's panel shares
#: it) and in memory only: a restart forgets the list, never the town.
UNDO_KEEP = 20
_UNDO = collections.deque(maxlen=UNDO_KEEP)
_UNDO_LOCK = threading.Lock()
#: the edits that can be taken back: the ones that change a half's NPC rows
UNDOABLE = ("place", "move", "turn", "remove", "retype", "townsfolk", "shops")


def undo_list(n=10):
    """The newest undoable edits, newest first: [{"area", "label"}]."""
    with _UNDO_LOCK:
        rows = list(_UNDO)[-int(n):]
    return [{"area": r["area"], "label": r["label"]} for r in reversed(rows)]


def _undo_label(kind, op, before, after):
    area = op.get("area")

    def who(rows, i):
        r = rows[i] if 0 <= i < len(rows) else {}
        return str(r.get("name") or "an NPC").replace("_", " ")
    try:
        i = int(op.get("idx", -1))
    except (TypeError, ValueError):
        i = -1
    if kind == "place":
        return "placed %s in %s" % (str(op.get("role") or "").replace("_", " "), area)
    if kind == "move":
        return "moved %s in %s" % (who(after, i), area)
    if kind == "turn":
        return "turned %s in %s" % (who(after, i), area)
    if kind == "remove":
        return "removed %s from %s" % (who(before, i), area)
    if kind == "retype":
        return "changed %s to %s in %s" % (who(before, i), who(after, i), area)
    n = len(after) - len(before)
    if kind == "shops":
        return "placed %d shopkeeper%s in %s" % (n, "" if n == 1 else "s", area)
    return "placed %d townsfolk in %s" % (n, area)


def devtool_undo(args):
    """Take back the newest undoable edit: put that half's NPC rows back to
    what they were before it -- ONLY if they are still exactly what the edit
    left. Anything else (a `!npc` typed in game, another admin's edit since)
    means the snapshot is stale, and restoring it would silently throw that
    change away, so it is refused and dropped instead."""
    with _UNDO_LOCK:
        if not _UNDO:
            return {"ok": False, "msg": "Nothing to undo."}
        u = _UNDO.pop()
    key = str(u["area"])

    def change(blob):
        g = blob.setdefault(key, {})
        if list(g.get("npcs") or []) != u["after"]:
            return False, "stale"
        g["npcs"] = [dict(r) for r in u["before"]]
        return True, "ok"

    got = town._town_change(args, change)
    if got == "ok":
        town.town_mark_dirty(u["area"])
        return {"ok": True, "msg": "Undone: %s." % u["label"]}
    if got == "stale":
        return {"ok": False, "msg": "Could not undo (%s): the NPCs in area %s "
                "have changed since. Nothing was changed." % (u["label"], u["area"])}
    return {"ok": False, "msg": "Could not undo (%s): the town could not be "
            "read or written." % u["label"]}


#: How far (world units) a shop spot may move off its icon to find floor:
#: the icons sit on the shop buildings, and the art is good to a couple of
#: metres (tools/fedatagen/feicons.py), so a keeper a few steps out front is
#: still at that shop.
SPOT_REACH = 8


def shop_spots(args, area):
    """Where each roster role with a painted shop icon in this half should
    stand: [{name, script, modeltype, fn, px, py, x, y, z, placed}].

    The spot is the icon's pixel taken through this half's CURRENT alignment
    (so it lands under the icon as the editor draws it), moved up to
    SPOT_REACH units to the nearest floor the capital's collision has; y is
    that floor, or
    None when there is none nearby (such a spot is shown but never placed).
    Trusted icons only, like `!dress`: a half whose classifier gave one role
    two icons cannot say which is the shop."""
    stem = fegamedata.minimap_name(area)
    if not stem:
        return []
    p = mapcal.mapcal_proj(stem)
    rows = town.town_get(args, area, "npcs")
    have_s = {int(n.get("script") or 0) for n in rows}
    have_n = {str(n.get("name") or "") for n in rows}
    icons = {}
    for i in fegamedata.shop_icons(area, trusted_only=True):
        if i["role"]:
            icons.setdefault(i["role"], i)
    out = []
    for r in fegamedata.capital_roster(area):
        i = icons.get(r["name"])
        if i is None:
            continue
        x0 = (i["px"] - p["cx"]) * p["ax"]
        z0 = (i["py"] - p["cz"]) * p["az"]
        spot = (x0, None, z0)
        gy = fegamedata.capital_ground(area, x0, z0)
        if gy is not None:
            spot = (x0, gy, z0)
        else:
            # the NEAREST floor within SPOT_REACH units, on a 1-unit grid
            best = None
            for ix in range(-SPOT_REACH, SPOT_REACH + 1):
                for iz in range(-SPOT_REACH, SPOT_REACH + 1):
                    d2 = ix * ix + iz * iz
                    if d2 > SPOT_REACH * SPOT_REACH or (best and d2 >= best[0]):
                        continue
                    g2 = fegamedata.capital_ground(area, x0 + ix, z0 + iz)
                    if g2 is not None:
                        best = (d2, (x0 + ix, g2, z0 + iz))
            if best:
                spot = best[1]
        out.append({"name": r["name"], "script": r["script"],
                    "modeltype": r["modeltype"],
                    "fn": npc_function(r["name"], r["script"])[0],
                    "px": i["px"], "py": i["py"],
                    "x": round(spot[0], 1), "z": round(spot[2], 1),
                    "y": None if spot[1] is None else round(spot[1], 2),
                    "placed": r["script"] in have_s or r["name"] in have_n})
    return out


def shops_place(args, area):
    """Put every roster role that has a painted icon in this half, and is not
    placed yet, on its icon (shop_spots). Idempotent; a spot with no floor
    nearby is skipped and named."""
    placed, skipped = [], []
    for sp in shop_spots(args, area):
        if sp["placed"]:
            continue
        if sp["y"] is None:
            skipped.append("%s (no floor)" % sp["name"])
            continue
        town.town_add(args, area, "npcs",
                      {"model": sp["modeltype"], "kind": 1, "level": 1,
                       "x": sp["x"], "y": sp["y"], "z": sp["z"], "yaw": 0.0,
                       "name": sp["name"], "script": sp["script"]})
        placed.append(sp["name"])
    msg = "placed %d shopkeeper%s on their icons in %s" % (
        len(placed), "" if len(placed) == 1 else "s", area)
    if skipped:
        msg += "; skipped %s" % ", ".join(skipped)
    return {"ok": bool(placed) or not skipped, "msg": msg, "placed": placed,
            "skipped": skipped}


def devtool_edit(args, op):
    """One editor change, as _devtool_edit_apply does it, remembered for
    UNDO when it changed a half's NPC rows (UNDOABLE) -- plus the "undo" op
    itself."""
    kind = str((op or {}).get("op") or "")
    if kind == "undo":
        return devtool_undo(args)
    area = None
    if kind in UNDOABLE:
        try:
            area = int(op["area"])
        except (KeyError, TypeError, ValueError):
            area = None
    before = town.town_get(args, area, "npcs") if area is not None else None
    res = _devtool_edit_apply(args, op)
    if area is not None and res.get("ok"):
        after = town.town_get(args, area, "npcs")
        if after != before:
            with _UNDO_LOCK:
                _UNDO.append({"area": area, "before": before, "after": after,
                              "label": _undo_label(kind, op, before, after)})
    return res


def _devtool_edit_apply(args, op):
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
                    "townsfolk", "shops"):
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
        if kind == "shops":
            return shops_place(args, area)
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


# ---------------------------------------------------------------------------
# The admin panel's actions (OpenLobby adminmodules/fe.py), POSTed through its
# proxy with the same token as every other request on this port. Each answers
# {"ok", "msg"}, the msg a sentence the panel shows as it is.
#: The banner is one line on a small window; longer text is refused rather
#: than cut, so the sender knows what players will read.
SYSMSG_MAX = 200


def _number(v, lo, hi):
    """A whole number lo..hi from a JSON value, or None. Digits only: no sign,
    no spaces, no 0x, so nothing but the number reaches the game."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        n = v
    elif isinstance(v, str) and 0 < len(v) <= 3 and v.isdigit() and v.isascii():
        n = int(v)
    else:
        return None
    return n if lo <= n <= hi else None


def _ext(name):
    return sys.modules.get(name)


def admin_sysmsg(args, body, actor):
    text = body.get("text")
    if not isinstance(text, str):
        return 400, {"ok": False, "msg": "A system message needs text."}
    text = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    if not text:
        return 400, {"ok": False, "msg": "A system message needs text."}
    if len(text) > SYSMSG_MAX:
        return 400, {"ok": False, "msg": "A system message can be at most %d "
                                         "characters." % SYSMSG_MAX}
    if text == "@clear@":
        return 400, {"ok": False, "msg": "That text closes the banner instead "
                                         "of showing it."}
    fegm = _ext("fegm")
    if fegm is None:
        return 503, {"ok": False, "msg": "The GM module is not loaded on this "
                                         "server."}
    n = fegm.announce(text, actor=actor)
    if not n:
        return 200, {"ok": True, "msg": "Nobody is in Fantasy Earth right now, "
                                        "so nobody saw it."}
    return 200, {"ok": True, "msg": "System message sent to %d player%s."
                                    % (n, "" if n == 1 else "s")}


def admin_war(args, body, actor):
    area = _number(body.get("area"), 1, 999)
    nation = _number(body.get("nation"), 1, 99)
    if area is None:
        return 400, {"ok": False, "msg": "The field must be a field number."}
    if nation is None:
        return 400, {"ok": False, "msg": "The nation must be a nation number."}
    if area not in fegamedata.areas():
        return 400, {"ok": False, "msg": "There is no field %d." % area}
    if area in zones.CAPITAL_GROUP_IDS:
        return 400, {"ok": False, "msg": "Field %d is a capital, and a capital "
                                         "is never attacked." % area}
    camp = _ext("fecampaign")
    if camp is None:
        return 503, {"ok": False, "msg": "The campaign module is not loaded on "
                                         "this server."}
    ok, msg = camp.admin_declare(args, area, nation, actor=actor)
    return 200, {"ok": ok, "msg": msg}


def _online_charids():
    """{(account, charid)} of the characters in the world now (feworld's
    locked copy of the session table, as femap's board reads it)."""
    fw = _ext("feworld")
    out = set()
    try:
        for ent in (fw.ext_sessions() if fw is not None else []):
            ps = ent.get("session") or {}
            if ps.get("account") and ps.get("charid") is not None:
                out.add((ps["account"], int(ps["charid"]) & 0xFFFFFFFF))
    except Exception:                                  # noqa: BLE001 -- a picker hint only
        pass
    return out


def admin_players(args, query, actor):
    """GET /admin/players.json?q=&limit=: the panel's player picker. Stored
    characters whose name holds q (any case), online first, best match first;
    "id" is the character name, which the actions take."""
    q = " ".join(str((query.get("q") or [""])[0]).split())[:64].casefold()
    try:
        limit = max(1, min(50, int((query.get("limit") or ["20"])[0])))
    except ValueError:
        limit = 20
    live = _online_charids()
    rows = []
    for acct, charid, name in character.all_chars():
        if not name or q and q not in name.casefold():
            continue
        rows.append({"id": name, "label": name, "detail": acct,
                     "online": (acct, charid) in live})
    rows.sort(key=lambda r: (not r["online"], not r["label"].casefold().startswith(q),
                             r["label"].casefold()))
    return 200, {"ok": True, "players": rows[:limit]}


#: the most Rings one gift may give (a test tool: see admin_rings)
RINGS_GIFT_MAX = 999


def admin_rings(args, body, actor):
    """POST /admin/rings {name, amount}: add Rings to a stored character, the
    way a war's reward credits them (wallet.rings_add's column, under
    felobby.update_roster's lock, so a purchase in the world cannot race it).
    For testing: Rings come only from wars, and a quiet server holds none.
    A character in the world sees the new balance at its next wallet push."""
    name = str(body.get("name") or "").strip()
    amount = _number(body.get("amount"), 1, RINGS_GIFT_MAX)
    if not name:
        return 400, {"ok": False, "msg": "Pick a character."}
    if amount is None:
        return 400, {"ok": False, "msg": "Give between 1 and %d Rings." % RINGS_GIFT_MAX}
    hit = character.char_by_name(name)
    if hit is None:
        return 400, {"ok": False, "msg": "No character called %s." % name}
    acct, charid, cname = hit
    seed = getattr(args, "ring", None)
    try:
        import felobby
    except ImportError:
        return 503, {"ok": False, "msg": "The character store is not loaded on this server."}
    got = {}

    def mutate(roster):
        for c in roster:
            if int(c.get("charid") or -1) & 0xFFFFFFFF == charid:
                try:
                    have = int(c.get("ring")) if c.get("ring") is not None else int(seed or 0)
                except (TypeError, ValueError):
                    have = int(seed or 0)
                c["ring"] = have + amount
                got.update(before=have, after=c["ring"])
                return True
        return False

    if not felobby.update_roster(felobby._default_store(), acct, mutate):
        return 500, {"ok": False, "msg": "%s's record could not be written." % cname}
    print("[devtool] %s gave %d Ring(s) to %s (%s/%d): %d -> %d"
          % (actor or "?", amount, cname, acct, charid, got["before"], got["after"]), flush=True)
    online = (acct, charid) in _online_charids()
    return 200, {"ok": True, "msg": "%s has %d Rings now (was %d).%s" % (
        cname, got["after"], got["before"],
        " They are in the world: the new balance shows at their next purchase or "
        "field change." if online else "")}


#: SUMMON TEST MODE (2026-10-22): what one player needs to try a summon on a
#: quiet server -- a war one vote declares, summons that cost nothing, and a
#: Dragon on every death of a holder of its item. Set on the RUNNING world's
#: args (each is read where it is used: fecampaign's vote count, feunit's
#: summon_cost and dragon_on_death), so prod's compose is never edited; a
#: restart, or test mode off, puts the command line's values back.
TEST_MODE = {"declare_accepts": 1, "summon_cost": "KNIGHT=0,GIANT=0,WRAITH=0,DRAGON=0",
             "summon_dragon_death": 1.0,
             # the summon's own skills on its palette (feunit FORM_SKILLS, an experiment)
             "summon_skills": "on"}
_TEST_SAVED = {}


def admin_testmode(args, body, actor):
    """POST /admin/testmode {on: "on" | "off"}."""
    want = str(body.get("on") or "").strip().lower()
    if want not in ("on", "off"):
        return 400, {"ok": False, "msg": "Say on or off."}
    if want == "on":
        if not _TEST_SAVED:
            for k in TEST_MODE:
                _TEST_SAVED[k] = getattr(args, k, None)
        for k, v in TEST_MODE.items():
            setattr(args, k, v)
        print("[devtool] SUMMON TEST MODE ON by %s: %r (was %r)"
              % (actor or "?", TEST_MODE, _TEST_SAVED), flush=True)
        return 200, {"ok": True, "msg": "Summon test mode is on: one vote declares a war, "
                                        "summons cost nothing, and a player holding the "
                                        "Dragon item rises as the Dragon on every death; a "
                                        "summon gets its own skills on its palette (an "
                                        "experiment). Turn it off after the test (a "
                                        "restart also does)."}
    if not _TEST_SAVED:
        return 200, {"ok": True, "msg": "Summon test mode was not on."}
    for k, v in _TEST_SAVED.items():
        setattr(args, k, v)
    print("[devtool] SUMMON TEST MODE OFF by %s: restored %r" % (actor or "?", _TEST_SAVED),
          flush=True)
    _TEST_SAVED.clear()
    return 200, {"ok": True, "msg": "Summon test mode is off: the server's own settings are back."}


def admin_popscale(args, body, actor):
    """POST /admin/popscale {on, full, extra}: fecampaign's --pop-scale on the
    running world (a restart goes back to the configured values). Every key
    is optional; the reply says what each war under way scales to now."""
    camp = _ext("fecampaign")
    if camp is None or not hasattr(camp, "pop_factor"):
        return 503, {"ok": False, "msg": "The campaign is not loaded on this world."}
    on = str(body.get("on") or "").strip().lower()
    if on and on not in ("on", "off"):
        return 400, {"ok": False, "msg": "Scaling is on or off."}
    full = str(body.get("full") or "").strip()
    if full and not (full.isascii() and full.isdigit() and 1 <= int(full) <= 50):
        return 400, {"ok": False, "msg": "Full strength is 1 to 50 players a side."}
    extra = str(body.get("extra") if body.get("extra") is not None else "").strip()
    if extra:
        parts = extra.split(":")
        if len(parts) > 2 or not all(x.isascii() and x.isdigit() and int(x) <= 50
                                     for x in parts):
            return 400, {"ok": False, "msg": "Extra players are N for both sides, "
                                             "or ATK:DEF, each 0 to 50."}
    if on:
        args.pop_scale = on
    if full:
        args.pop_scale_full = int(full)
    if body.get("extra") is not None:
        args.pop_scale_extra = "" if extra in ("", "0", "0:0") else extra
    print("[devtool] POP SCALE by %s: %s, full at %s, extra %r" % (
        actor or "?", args.pop_scale, args.pop_scale_full,
        getattr(args, "pop_scale_extra", "")), flush=True)
    wars = []
    try:
        names = fegamedata.area_names_en()
    except Exception:                                  # noqa: BLE001
        names = {}
    for area in sorted(getattr(camp, "_STATE", {}) or {}):
        if camp.phase_of(area) not in (camp.PREP, camp.WAR):
            continue
        rep = camp.pop_scale_report(args, area)
        wars.append("%s: attack %d players x%.2f, defence %d players x%.2f" % (
            names.get(area) or "Field %d" % area, rep["atk"][0], rep["atk"][1],
            rep["def"][0], rep["def"][1]))
    msg = ("Scaling is %s: a side at %d or more players gets the retail values"
           % (args.pop_scale, int(args.pop_scale_full)))
    if getattr(args, "pop_scale_extra", ""):
        msg += "; %s extra players counted (attack:defence)" % args.pop_scale_extra
    msg += ". " + ("; ".join(wars) + "." if wars else "No war is under way.")
    return 200, {"ok": True, "msg": msg}


def admin_warbots(args, body, actor):
    """POST /admin/warbots {count}: fewarbots' --war-bots on the running world
    (a restart goes back to the configured value). 0 takes every bot out of
    every war; N puts N a side into every war under way and every war that
    starts after. Above the war cap less 5 (45 at the cap of 50) it is taken
    as that, and the reply says so."""
    bots = _ext("fewarbots")
    if bots is None or not hasattr(bots, "set_count"):
        return 503, {"ok": False, "msg": "War bots are not loaded on this world."}
    n = _number(body.get("count"), 0, 200)
    if n is None:
        return 400, {"ok": False, "msg": "Soldiers a side is a number from 0 to 200."}
    return 200, {"ok": True, "msg": bots.set_count(args, n, actor=actor)}


#: the widest range one calendar.json request may ask for, in seconds
CALENDAR_SPAN = 400 * 86400


def admin_calendar(args, query, actor):
    """GET calendar.json?from=&to= (unix seconds): the wars under way and the
    phases still ahead of them (fecampaign.calendar) and SE's timed events
    (timedevents.calendar), for the admin panel's Calendar. Reads only."""
    try:
        lo, hi = int(query["from"][0]), int(query["to"][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return 400, {"ok": False, "msg": "calendar.json needs from= and to=, in "
                                         "unix seconds."}
    if hi <= lo or hi - lo > CALENDAR_SPAN:
        return 400, {"ok": False, "msg": "Ask for a range of up to %d days."
                                         % (CALENDAR_SPAN // 86400)}
    camp = _ext("fecampaign")
    events = []
    if camp is not None and getattr(args, "campaign", "off") == "on":
        events = list(camp.calendar(args, lo, hi))
    # SE's timed events (Double Up, the Dragon feast...) run with or
    # without the campaign
    events = sorted(events + timedevents.calendar(args, lo, hi),
                    key=lambda e: (e["start"], e["end"], e["id"]))
    return 200, {"ok": True, "from": lo, "to": hi, "events": events}


def admin_routes(args):
    """fedevtool.start(routes=...): fn(JSON body, or a GET's query, actor) ->
    (status, body).
    Every call is printed with who asked, refusals included, so the server
    log answers "who started that war" without the panel's own log."""
    def logged(name, fn):
        def run(body, actor):
            status, res = fn(args, body, actor)
            print("[devtool] admin %s%s -> %s%s" % (
                name, " by " + actor if actor else "",
                "" if res.get("ok") else "REFUSED: ", res.get("msg")), flush=True)
            return status, res
        return run
    return {("POST", "/admin/sysmsg"): logged("sysmsg", admin_sysmsg),
            ("POST", "/admin/war"): logged("war", admin_war),
            ("POST", "/admin/rings"): logged("rings", admin_rings),
            ("POST", "/admin/testmode"): logged("testmode", admin_testmode),
            ("POST", "/admin/popscale"): logged("popscale", admin_popscale),
            ("POST", "/admin/warbots"): logged("warbots", admin_warbots),
            ("GET", "/admin/players.json"): lambda q, actor: admin_players(args, q, actor),
            ("GET", "/calendar.json"): lambda q, actor: admin_calendar(args, q, actor)}
