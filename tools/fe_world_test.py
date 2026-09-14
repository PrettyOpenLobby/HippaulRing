"""fe_world_test.py -- does the world we SERVE let a player reach enemy land?

Every other check in this tree asks whether a message parses. This one asks
whether the game works, by reimplementing the two client functions that decide
where a player may go and running them against the records feworld actually
builds:

    0x05009870  frontier(group)  -- group.def_id == my nation, OR any id in
                                    group's neighbour array is defended by it
    0x050098f0  can_move(a, b)   -- b is IN a's neighbour array, AND
                                    (b.def_id == mine OR frontier(a) and frontier(b))

Those are transcribed from the disassembly, not from what feworld believes, so
a change to feworld that breaks the rule fails here rather than on a live
player's screen. The refusal the player would see is the client's own string at
0x052e4cd0: "このフィールドは所属国と隣接していないので入ることができません"
-- "this field is not adjacent to your country".

Run: python tools/fe_world_test.py
"""
import inspect
import os
import types
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "services"))

import fegamedata  # noqa: E402
import feworld  # noqa: E402


class _Args(object):
    """Only what the 0x3031 path and the world helpers read."""

    def __init__(self, **kw):
        self.world = "dat"
        self.world_neighbours = "on"
        self.world_inner_capitals = "on"
        self.territory = ""
        self.territory_file = os.devnull
        self.field_home = "capital"
        self.spawn_height = "dat"
        self.spawn_xyz = (0.0, 0.0, 0.0)
        self.spawn_file = os.devnull
        self.spawn_area = ""
        self.groups = 1
        self.islands = 6
        self.__dict__.update(kw)


class _LooseArgs(_Args):
    """_Args, but any option nobody set reads as None.

    Only for driving a big feworld entry point (enter_area takes ~20 options
    and this test cares about one line of it). Everything the test actually
    asserts on is set explicitly; None makes the rest take their "not
    configured" branch, which is what a bare feworld does anyway.
    """

    def __getattr__(self, k):        # only called when normal lookup fails
        if k.startswith("__"):
            raise AttributeError(k)
        return None


# --------------------------------------------------------------------------
# The client, transcribed.
# --------------------------------------------------------------------------
def frontier(world, gid, my_nation):
    g = world.get(gid)
    if not g:
        return False
    if g["def_id"] == my_nation:
        return True
    for nid in g["neighbours"]:
        n = world.get(nid)
        if n and n["def_id"] == my_nation:
            return True
    return False


def can_move(world, src, dst, my_nation):
    s = world.get(src)
    if not s or dst not in s["neighbours"]:
        return False
    d = world.get(dst)
    if not d:
        return False
    if d["def_id"] == my_nation:
        return True
    return frontier(world, src, my_nation) and frontier(world, dst, my_nation)


# --------------------------------------------------------------------------
# Build the world the way the wire does -- records out, records back in.
# --------------------------------------------------------------------------
KL = {"u8": 1, "u32": 4, "u16": 2, "i8": 1, "2i16": 4}


def decode_record(buf, off):
    gid, mask = struct.unpack_from(">II", buf, off)
    p, out = off + 8, {"gid": gid, "neighbours": [], "def_id": 0, "g8c": 0}
    for bit, (kind, key, _o, _n) in enumerate(feworld.GROUP_FIELDS):
        if not (mask >> bit) & 1:
            continue
        if kind == "cstr":
            e = buf.index(b"\x00", p)
            out[key] = buf[p:e].decode("cp932", "replace")
            p = e + 1
        elif kind == "u32arr":
            n = struct.unpack_from(">I", buf, p)[0]
            p += 4
            out[key] = list(struct.unpack_from(">%dI" % n, buf, p)) if n else []
            p += 4 * n
        elif kind == "2i16":
            out[key] = struct.unpack_from(">hh", buf, p)
            p += 4
        else:
            out[key] = struct.unpack_from(
                {"u8": ">B", "i8": ">b", "u16": ">H", "u32": ">I"}[kind], buf, p)[0]
            p += KL[kind]
    return out, p + 1 + 4


def serve_world(args):
    """Every island's 0x3031 payload, decoded back into a {gid: record} world.

    Going out through build_group_record and back through a client-shaped
    decoder is the point: a world assembled from fegamedata directly would
    pass even if the wire form were wrong.
    """
    world, per_island = {}, {}
    for island in fegamedata.island_ids():
        gids = feworld.group_ids_for(island, args)
        payload = struct.pack(">HH", island, len(gids))
        for gid in gids:
            row = fegamedata.areas()[gid]
            vals = {"name": row["name"], "status": 0,
                    "def_id": feworld.territory_owner(gid, args),
                    "coords": (row["map_x"], row["map_y"]),
                    "g8c": row["flags"]}
            if row["hmap"]:
                vals["hmap"] = row["hmap"]
            if args.world_neighbours == "on":
                vals["neighbours"] = row["neighbours"]
            payload += feworld.build_group_record(gid, vals)
        # Consume it exactly as the client does; a desync fails right here.
        off = 4
        got = []
        for _ in range(len(gids)):
            rec, off = decode_record(payload, off)
            world[rec["gid"]] = rec
            got.append(rec["gid"])
        assert off == len(payload), (
            "island %d: 0x3031 desync, %d of %d bytes consumed"
            % (island, off, len(payload)))
        assert got == gids, "island %d: ids changed on the wire" % island
        per_island[island] = gids
    return world, per_island


def main():
    fails = []

    def check(name, cond, detail=""):
        print("  %-58s %s" % (name, "PASS" if cond else "FAIL"))
        if not cond:
            fails.append("%s %s" % (name, detail))

    print("the shipped world -- fet_area over six islands")
    args = _Args()
    feworld.territory_load(args)
    world, per_island = serve_world(args)

    check("all 95 areas are served", len(world) == 95, str(len(world)))
    check("six islands", sorted(per_island) == [1, 2, 3, 4, 5, 6])
    check("island 1 is the shared frontier continent (15 areas)",
          len(per_island[1]) == 15)
    check("each home continent carries 16 (15 + the inner capital)",
          all(len(per_island[i]) == 16 for i in range(2, 7)))
    check("every group id is inside the client's 1..0x5F bound",
          all(1 <= g <= 0x5F for g in world))
    check("every war field's hmap is a shipped map01..map14",
          all(1 <= r["hmap"] <= 14 for g, r in world.items()
              if g not in feworld.CAPITAL_GROUP_IDS))
    check("every capital carries the CAPITAL bit (g8c bit 3)",
          all(world[g]["g8c"] & 8 for g in feworld.CAPITAL_GROUP_IDS))
    check("no war field claims to be a capital",
          not any(world[g]["g8c"] & 8 for g in world
                  if g not in feworld.CAPITAL_GROUP_IDS))

    # The graph itself. A one-way edge is a field you can walk into and not
    # back out of, which is worse than one you cannot reach.
    oneway = [(a, b) for a, r in world.items() for b in r["neighbours"]
              if b in world and a not in world[b]["neighbours"]]
    check("every edge is reciprocal except the door-only inner capitals",
          all(a in feworld.INNER_CAPITAL_GROUP_IDS for a, _ in oneway),
          str(oneway[:4]))
    check("every neighbour id is an area we actually serve",
          all(b in world for r in world.values() for b in r["neighbours"]))

    # ---- the rule ------------------------------------------------------
    print("\nthe client's frontier rule, on SE's starting board")
    # SE's board: everybody holds their own continent, island 1 is split 3/3/3.
    check("a player can reach their own capital from its border field",
          can_move(world, 18, 21, 1))
    check("nation 2 cannot walk into nation 1's capital",
          not can_move(world, 18, 21, 2))
    check("nation 2 cannot enter nation 1's inland field",
          not can_move(world, 28, 18, 2))
    # 11 (nation 1) borders 12 (nation 2) on the frontier continent, so a
    # nation-1 player standing on 11 IS on the front and 12 is adjacent to it.
    check("a border field of the enemy IS enterable from your own",
          can_move(world, 11, 12, 1))
    check("...and symmetrically the other way",
          can_move(world, 12, 11, 2))
    check("a field two steps into enemy land is NOT enterable",
          not can_move(world, 12, 36, 1))

    print("\nconquest moves the map")
    a2 = _Args(territory="18:2")          # nation 2 has taken 18
    feworld.territory_load(a2)
    w2, _ = serve_world(a2)
    check("the captured field is served as the new holder's",
          w2[18]["def_id"] == 2)
    check("nation 2 can now enter nation 1's CAPITAL from it",
          can_move(w2, 18, 21, 2))
    check("nation 3, who took nothing, still cannot",
          not can_move(w2, 18, 21, 3))
    check("nation 1 can still move within its own land",
          can_move(w2, 28, 22, 1))
    check("nation 1 can counter-attack the field it lost",
          can_move(w2, 22, 18, 1))

    print("\nthe A/B: with bit 12 unserved, nobody crosses anything")
    a3 = _Args(world_neighbours="off", territory="18:2")
    feworld.territory_load(a3)
    w3, _ = serve_world(a3)
    check("no field carries an adjacency list",
          all(r["neighbours"] == [] for r in w3.values()))
    check("EVERY move is refused, including into your own capital",
          not any(can_move(w3, a, b, n)
                  for a in (11, 12, 18, 22, 28) for b in range(1, 96)
                  for n in range(1, 6)),
          "this is the state prod has been in")
    # ...and that is discriminating: the same query on the real world passes.
    check("...while the served world permits a great many",
          sum(1 for a in world for b in world[a]["neighbours"]
              for n in range(1, 6) if can_move(world, a, b, n)) > 100)

    print("\nthe territory store round-trips")
    feworld.territory_load(args)
    was = feworld.territory_set(18, 4)
    check("a capture reports the previous holder", was == 1, repr(was))
    check("and takes effect immediately", feworld.territory_owner(18) == 4)
    check("an area outside the 95 is refused, not invented",
          feworld.territory_set(999, 2) is None)

    print("\nthe war-outcome notifies")
    # All six arms read [u32][u32 nation][u32 area] and then do
    # group_by_id(#3) / force_by_id(#2). Decode our body the way they do.
    body = feworld.land_notify_body(2, 1, 18)
    a, b, c = struct.unpack(">III", body)
    check("the body is three u32 in the client's read order",
          len(body) == 12 and (a, b, c) == (2, 1, 18))
    check("the AREA is the THIRD word -- what group_by_id gets",
          c in fegamedata.areas())
    check("all six kinds have an id, and they are the six SE arms",
          sorted(feworld.LAND_NOTIFY_IDS.values())
          == [0x3036, 0x3037, 0x3038, 0x3039, 0x303A, 0x303B])
    check("a nation id survives unmasked", struct.unpack(
        ">III", feworld.land_notify_body(5, 5, 95))[2] == 95)
    # The two war-start notifies are EMPTY-bodied: their arms (0x05054a68 /
    # 0x05054cbb) make no stream-reader call at all, checked against the land
    # notifies as a positive control. So there is nothing to pack -- only the
    # ids, and they must not collide with the six that do carry a body.
    check("the war-start pair is 0x101D / 0x101E",
          sorted(feworld.WAR_START_NOTIFY_IDS.values()) == [0x101D, 0x101E])
    check("war-start and land notifies share no id",
          not (set(feworld.WAR_START_NOTIFY_IDS.values())
               & set(feworld.LAND_NOTIFY_IDS.values())))
    check("...and no kind NAME either, since !land dispatches on the name",
          not (set(feworld.WAR_START_NOTIFY_IDS) & set(feworld.LAND_NOTIFY_IDS)))

    print("\nwhere a player arrives")
    # --spawn-pos is ONE position for 95 areas and prod never set it, so it
    # stayed 0:0:0 -- the centre of the terrain grid at height zero, which on
    # most maps is under it. The per-area store is what fixes that, so the
    # thing to assert is that a row actually beats the global default and that
    # an area WITHOUT a row still falls back rather than returning nothing.
    sp = _Args(spawn_file=os.path.join(
        os.environ.get("TEMP", "."), "fe_spawn_test_%d.json" % os.getpid()))
    sp.spawn_xyz = (0.0, 0.0, 0.0)
    sp.self_xyz = (2.16, 24.76, -60.51)
    sp.spawn_area = "39:186.5:21.0:-132.5"
    feworld.spawn_load(sp)
    check("--spawn-area gives that area its own arrival point",
          feworld.spawn_for(sp, 39) == (186.5, 21.0, -132.5))
    # An area with no row must NOT land at y=0: every battle map that ships
    # heights has its terrain above that, so the origin is under all of them.
    sp.spawn_height = "off"
    check("--spawn-height off falls back to --spawn-pos verbatim",
          feworld.spawn_for(sp, 1) == (0.0, 0.0, 0.0))
    sp.spawn_height = "dat"
    fb = feworld.spawn_for(sp, 1)
    check("--spawn-height dat lifts it onto that area's map instead",
          fb[1] == fegamedata.area_ground_height(1) and fb[1] > 0, repr(fb))
    was = feworld.spawn_add(sp, 1, (12.5, 24.76, -60.5))
    check("!spawn records a measured position", was is None
          and feworld.spawn_for(sp, 1) == (12.5, 24.76, -60.5))
    check("...and reports the previous one when overwriting the same tag",
          feworld.spawn_add(sp, 1, (0, 1, 2))["y"] == 24.76)
    # A battlefield has two sides that do not arrive together.
    feworld.spawn_add(sp, 1, (100.0, 30.0, -100.0), tag="atk")
    feworld.spawn_add(sp, 1, (-100.0, 31.0, 100.0), tag="def")
    check("a tagged point is picked by its tag",
          feworld.spawn_for(sp, 1, tag="def") == (-100.0, 31.0, 100.0))
    check("...and the other side gets the other one",
          feworld.spawn_for(sp, 1, tag="atk") == (100.0, 30.0, -100.0))
    check("an unknown tag falls back to the untagged point, not a side's",
          feworld.spawn_for(sp, 1, tag="zzz") == (0.0, 1.0, 2.0))
    check("three points now live on that area", len(feworld.spawn_rows_for(1)) == 3)
    check("dropping one tag leaves the rest",
          feworld.spawn_drop(sp, 1, "atk") == 1
          and len(feworld.spawn_rows_for(1)) == 2)
    feworld.spawn_load(sp)     # reload from disk: it must have persisted
    check("the store round-trips through the file",
          feworld.spawn_for(sp, 1) == (0.0, 1.0, 2.0))
    check("...tags included", feworld.spawn_for(sp, 1, tag="def")
          == (-100.0, 31.0, 100.0))
    try:
        os.remove(feworld.spawn_path(sp))
    except OSError:
        pass
    # The height that broke doors, against the SHIPPED portal table: stand on
    # a real door in a real capital and see what height comes back.
    d = _Args(doors="dat", door_radius=15.0, spawn_area="", spawn_file=os.devnull)
    d.spawn_xyz = (0.0, 0.0, 0.0); d.self_xyz = (0.0, 0.0, 0.0)
    feworld.spawn_load(d)
    rows = fegamedata.area_portals(39)
    check("area 39 has shipped doors to stand on", bool(rows), str(len(rows)))
    if rows:
        gate = rows[0]["gate"]
        d.door_height = "client"
        hit = feworld.dat_door_for(d, 39, gate[0], gate[1], 21.0)
        check("a shipped door resolves from its own gate position", hit is not None)
        check("--door-height client arrives at the height the CLIENT reported",
              hit is not None and abs(hit[1][1] - 21.0) < 1e-6,
              repr(hit))
        # WARNING: THE TRAP HIT IN LIVE TESTING, 2026-09-10. `client` used the height at
        # the doorway you LEFT. A capital's two halves are DIFFERENT MAPS at
        # different heights, so the player arrived in the air, fell, and the
        # FALLING y went back on the wire at the door they fell past -- the log
        # shows 10041, then 10064. The height must come from the destination,
        # and a wild y must never propagate.
        d.door_height = "client"
        fall = feworld.dat_door_for(d, 39, gate[0], gate[1], 10041.15)
        check("a falling y is never handed back out",
              fall is not None and abs(fall[1][1]) < 500.0, repr(fall))
        d.door_height = "dest"
        feworld.spawn_add(d, 92, (0.0, 21.0, 0.0))
        dst_ = feworld.dat_door_for(d, 39, gate[0], gate[1], 10041.15)
        # The destination's own COLLISION now outranks even a spawn row: it is
        # a measurement of the arrival spot itself, where a spawn row is a
        # measurement of somewhere else in the area.
        want_y = fegamedata.capital_ground(92, dst_[1][0], dst_[1][2])             if dst_ else None
        check("the arrival height comes from the DESTINATION's own collision",
              dst_ is not None and want_y is not None
              and abs(dst_[1][1] - want_y) < 1e-6 and dst_[1][1] != 21.0,
              repr((dst_, want_y)))
        feworld.spawn_drop(d, 92)
        d.door_height = "18.25"
        pin = feworld.dat_door_for(d, 39, gate[0], gate[1], 21.0)
        check("a literal --door-height pins the height",
              pin is not None and abs(pin[1][1] - 18.25) < 1e-6)
        check("...and the horizontals are SE's own whatever the height rule",
              hit is not None and dst_ is not None and pin is not None
              and hit[1][0] == dst_[1][0] == pin[1][0]
              and hit[1][2] == dst_[1][2] == pin[1][2])

    print("\nwhat is supposed to be in an area")
    # dat.pak never says where a person stands, but it does say WHO belongs in
    # a capital -- so "is this town finished?" is answerable. The check that
    # matters is that it is the same twenty in all five, because that is what
    # makes a checklist trustworthy rather than a per-capital guess.
    rosters = {c: fegamedata.capital_roster(c) for c in (21, 39, 57, 62, 78)}
    check("every capital ships exactly 20 roles",
          all(len(r) == 20 for r in rosters.values()),
          str({c: len(r) for c, r in rosters.items()}))
    shapes = {c: tuple(sorted(x["script"] % 100 for x in r))
              for c, r in rosters.items()}
    check("...and the same script shape in all five",
          len(set(shapes.values())) == 1)
    check("an inner half inherits its capital's roster",
          [r["name"] for r in fegamedata.capital_roster(92)]
          == [r["name"] for r in rosters[39]])
    check("a war field has no roster", fegamedata.capital_roster(1) == [])
    e21, e1 = fegamedata.area_expected(21), fegamedata.area_expected(1)
    check("a capital expects a roster and doors, not monsters",
          len(e21["roster"]) == 20 and e21["doors"] and not e21["monsters"])
    check("a war field expects monsters and a castle, not a roster",
          e1["monsters"] and e1["castle"] and not e1["roster"])
    check("...and knows its own ground height",
          e1["ground"] == fegamedata.area_ground_height(1))
    check("an id outside the 95 returns nothing rather than an empty shell",
          fegamedata.area_expected(999) is None)

    print("\nplacing a role, then taking it back")
    # The point of !remove is not that a row disappears -- it is that the role
    # becomes PLACEABLE again. The missing list is a live diff, so this walks
    # the whole loop: missing -> placed -> removed -> missing.
    rt = os.path.join(os.environ.get("TEMP", "."),
                      "fe_town_rt_%d.json" % os.getpid())
    rr = _LooseArgs(town_file=rt, door_radius=15.0, spawn_file=os.devnull,
                    territory_file=os.devnull, spawn_area="",
                    force_table={}, devtool_port=1)
    rr.spawn_xyz = (0.0, 0.0, 0.0); rr.self_xyz = (0.0, 0.0, 0.0)
    feworld.territory_load(rr); feworld.spawn_load(rr)
    feworld._TLS.session = {"in_field": True, "field": 39, "cpos": (1.0, 2.0, 3.0)}
    feworld.devtool_snapshot()
    role = fegamedata.capital_roster(39)[1]          # a real shipped role
    before = feworld.devtool_state(rr)
    check("the role starts out missing",
          any(m["name"] == role["name"] for m in before["missing"]))
    feworld.town_add(rr, 39, "npcs",
                     {"model": role["modeltype"], "kind": 1, "level": 1,
                      "x": 1.0, "y": 2.0, "z": 3.0, "yaw": 0,
                      "name": role["name"], "script": role["script"]})
    placed = feworld.devtool_state(rr)
    check("placing it takes it off the missing list",
          not any(m["name"] == role["name"] for m in placed["missing"])
          and placed["roster_placed"] == before["roster_placed"] + 1)
    gone = feworld.town_del(rr, 39, "npcs", 0)
    check("removing it returns the row", gone is not None
          and gone["name"] == role["name"], repr(gone))
    after = feworld.devtool_state(rr)
    check("...and the role is placeable again",
          any(m["name"] == role["name"] for m in after["missing"])
          and after["roster_placed"] == before["roster_placed"])
    check("an out-of-range index deletes nothing rather than the wrong row",
          feworld.town_del(rr, 39, "npcs", 7) is None
          and feworld.town_del(rr, 39, "npcs", -1) is None)
    try:
        os.remove(rt)
    except OSError:
        pass

    print("\nthe whole world, area by area")
    # An audit rather than a spot check: every one of the 95 has to be
    # coherent on its own, because a single bad row is a field that crashes or
    # strands whoever walks into it, and nothing else here would notice.
    bad = {"hmap": [], "holder": [], "coords": [], "name": [], "island": [],
           "nbr": [], "mobs": []}
    withspawns = set(fegamedata.fields_with_spawns())
    for gid, rec in sorted(world.items()):
        row = fegamedata.areas()[gid]
        cap = gid in feworld.CAPITAL_GROUP_IDS
        # WARNING: an hmap the client's table does not know is a NULL DEREF on the
        # outdoor entry path, not a blank field.
        if not (cap or 1 <= rec["hmap"] <= 14):
            bad["hmap"].append(gid)
        if not 1 <= rec["def_id"] <= 5:
            bad["holder"].append(gid)
        # coords below 0x28 are CLAMPED by the client, stacking markers in the
        # corner -- which is what "the map looked empty" turned out to be once
        if not (row["map_x"] >= 40 and row["map_y"] >= 40):
            bad["coords"].append(gid)
        if not rec.get("name"):
            bad["name"].append(gid)
        if not 1 <= row["island"] <= 6:
            bad["island"].append(gid)
        if not cap and not rec["neighbours"]:
            bad["nbr"].append(gid)
        # a war field with no monsters is a field with nothing to do in it
        if not cap and gid not in withspawns and gid != 5:
            bad["mobs"].append(gid)
    check("every war field's hmap is one the client's table knows",
          not bad["hmap"], str(bad["hmap"]))
    check("every area is held by a real nation", not bad["holder"], str(bad["holder"]))
    check("no marker lands in the clamp zone", not bad["coords"], str(bad["coords"]))
    check("every area is named", not bad["name"], str(bad["name"]))
    check("every area sits on one of the six islands",
          not bad["island"], str(bad["island"]))
    check("every war field has neighbours to walk to",
          not bad["nbr"], str(bad["nbr"]))
    check("every war field but the starting land has monsters",
          not bad["mobs"], str(bad["mobs"]))
    check("every area resolves a spawn position",
          all(feworld.spawn_for(args, g) is not None for g in world))
    check("the five capitals are exactly the ones with a roster",
          {g for g in world if fegamedata.capital_roster(g)}
          == set(feworld.CAPITAL_GROUP_IDS))

    print("\nthe war cycle, end to end")
    import time as _time
    import fecampaign
    feworld.load_extensions()
    cf = os.path.join(os.environ.get("TEMP", "."), "fe_camp_%d.json" % os.getpid())
    tf2 = os.path.join(os.environ.get("TEMP", "."), "fe_terr_%d.json" % os.getpid())
    for _p in (cf, tf2):
        if os.path.exists(_p):
            os.remove(_p)
    cw = _LooseArgs(campaign="on", war_cap=60, war_prep_ms=5, war_length_ms=5,
                    war_truce_ms=5, war_decide="signups", campaign_file=cf,
                    war_start_fields="0,0", war_clock="off", war_trace="off",
                    territory_file=tf2, territory="", spawn_file=os.devnull,
                    spawn_area="", town_file="", door_radius=15.0,
                    force_table={})
    cw.spawn_xyz = (0.0, 0.0, 0.0); cw.self_xyz = (0.0, 0.0, 0.0)
    feworld.territory_load(cw)
    fecampaign.load(cw)
    real_send2, feworld.send = feworld.send, (lambda *a, **k: None)

    class _Ctx(object):
        args = cw
        conn = outbound = None
        mode, be = 0, False

        def send(self, *a, **k):
            pass

    try:
        # 17 is nation 1's and borders 53, which is nation 3's -- a real front.
        check("a field with no enemy neighbour cannot be attacked",
              fecampaign.declare(cw, 18) is None,
              "18's neighbours are all nation 1")
        row = fecampaign.declare(cw, 17)
        check("a border field can, and the attacker is the neighbour's nation",
              row is not None and row["atk"] == 3 and row["phase"] == fecampaign.PREP,
              repr(row))
        check("the war map's status byte follows the phase, not a constant",
              feworld.campaign_phase(cw, 17) == fecampaign.PREP)
        for _ in range(3):
            fecampaign.sign_up(cw, 17, "atk")
        fecampaign.sign_up(cw, 17, "def")
        check("0x1127 carries the real sign-ups against the cap",
              fecampaign.counts_of(17, cw) == (3, 1, 60, 60),
              repr(fecampaign.counts_of(17, cw)))
        check("a bad side is refused rather than counted",
              fecampaign.sign_up(cw, 17, "spectator") is None)
        check("a field at peace takes no sign-ups",
              fecampaign.sign_up(cw, 30, "atk") is None)
        # KEY: ONE CLOCK. feworld's own war pump must stand down for a field the
        # campaign owns, or the client's countdown and the war map run on two
        # unrelated timers and can disagree.
        feworld._TLS.session = {"in_field": True, "field": 17}
        check("feworld's war pump defers to the campaign for this field",
              feworld.campaign_phase(cw, 17) is not None)
        check("...and does not for a field at peace",
              feworld.campaign_phase(cw, 30) == fecampaign.PEACE)
        fecampaign.drive_client(_Ctx(), cw, 17, fecampaign.PREP,
                                fecampaign.state_of(17)["until"])
        check("the campaign arms the CLIENT's countdown from its own clock",
              feworld._SESSION.get("war_phase") == "prewar"
              and feworld._SESSION.get("war_deadline") is not None,
              repr(feworld._SESSION.get("war_phase")))
        _time.sleep(0.02); fecampaign.pump(_Ctx())
        check("prep runs out into war", fecampaign.phase_of(17) == fecampaign.WAR)
        check("...and the client's phase followed it",
              feworld._SESSION.get("war_phase") == "war",
              repr(feworld._SESSION.get("war_phase")))
        _time.sleep(0.02); fecampaign.pump(_Ctx())
        check("war runs out into truce", fecampaign.phase_of(17) == fecampaign.TRUCE)

        # KEY: THE POINT OF ALL OF IT. Territory moved, and because the client's
        # rule reads def_id off the group record, the conquest changes what
        # the CLIENT will permit -- checked with the same can_move transcribed
        # from the disassembly at the top of this file.
        check("the attacker took the field", feworld.territory_owner(17, cw) == 3)
        w2, _ = serve_world(cw)
        check("nation 3 could NOT reach 25 before and can now",
              can_move(w2, 17, 25, 3),
              "17 is theirs, 25 borders it")
        check("...and nation 1 can counter-attack the field it lost",
              can_move(w2, 28, 17, 1))
        check("a nation with no stake still cannot walk in",
              not can_move(w2, 28, 17, 5))
        _time.sleep(0.02); fecampaign.pump(_Ctx())
        check("truce runs out back to peace",
              fecampaign.phase_of(17) == fecampaign.PEACE)
        # the outcome rule is OURS -- so check the knob that admits it
        fecampaign.declare(cw, 17, 1)
        fecampaign.sign_up(cw, 17, "def")
        cw.war_decide = "defender"
        win, old, new = fecampaign.decide(cw, 17)
        check("--war-decide defender hands it to the holder whatever the counts",
              win == "def" and new == old)
    finally:
        feworld.send = real_send2
        for _p in (cf, tf2):
            try:
                os.remove(_p)
            except OSError:
                pass
    feworld.territory_load(args)      # put the shared board back

    print("\nthe recovered shop positions")
    icons = fegamedata.shop_icons(92)
    named = [r for r in icons if r["role"]]
    check("area 92 carries its recovered icons", len(named) >= 7, str(len(named)))
    check("...and no role appears twice in one capital",
          len({r["role"] for r in named}) == len(named))
    check("every trusted row is inside the map's own coordinate span",
          all(-320 <= r["x"] <= 320 and -320 <= r["z"] <= 320
              for r in fegamedata.shop_icons()),
          "the minimap is 256px at 2.5 units")
    # KEY: ASSERT THE MECHANISM, NOT AN EXAMPLE. This used to name area 39 as
    # the conflicted half; a better classifier fixed 39 and the test failed
    # for being out of date rather than for anything being wrong. What has to
    # hold is the invariant: nothing withheld is trusted, and no trusted half
    # contradicts itself.
    _all = fegamedata.shop_icons(trusted_only=False)
    _ok = fegamedata.shop_icons()
    check("trust is one of the three values it can be",
          {r["trust"] for r in _all} <= {"eye", "auto", "noisy"},
          str({r["trust"] for r in _all}))
    check("nothing marked noisy survives the default filter",
          not any(r["trust"] == "noisy" for r in _ok))
    _dup = {}
    for r in _ok:
        if r["role"]:
            _dup[(r["area"], r["role"])] = _dup.get((r["area"], r["role"]), 0) + 1
    check("no trusted half claims two of the same shop",
          not [k for k, v in _dup.items() if v > 1],
          str([k for k, v in _dup.items() if v > 1]))
    check("every recovered role is a role the capital actually has",
          all(r["role"] in {x["name"] for x in fegamedata.capital_roster(r["area"])}
              for r in fegamedata.shop_icons() if r["role"]))
    # The doors recovered from the art must agree with the shipped table --
    # that is the same ground truth the pixel->world fit was made against, so
    # it is the check that the fit still holds after any detector change.
    worst = 0.0
    for r in fegamedata.shop_icons(92, trusted_only=False):
        if r["kind"] != "door":
            continue
        near = min((((p["gate"][0] - r["x"]) ** 2
                     + (p["gate"][1] - r["z"]) ** 2) ** 0.5)
                   for p in fegamedata.area_portals(92))
        worst = max(worst, near)
    check("recovered door marks still land on the SHIPPED doors",
          worst < 8.0, "worst %.1f world units" % worst)

    print("\ndoors: the two names that used to be one")
    # WARNING: THE REGRESSION THIS FILE EXISTS FOR NOW. `--doors` is a MODE string
    # and `--door` is a list of rows; main() used to park the parsed rows in
    # args.doors, clobbering the mode, so dat_door_for's `!= "dat"` guard
    # compared a list to a string, was always true, and the shipped table was
    # never consulted once. Assert they are separate names AND that each
    # consumer reads its own.
    dd = _Args(doors="dat", door_rows=[(39, 1.0, 2.0, 10)], door_radius=15.0,
               door_height="client", spawn_area="", spawn_file=os.devnull)
    dd.spawn_xyz = (0.0, 0.0, 0.0); dd.self_xyz = (0.0, 0.0, 0.0)
    feworld.spawn_load(dd)
    check("a --door row still resolves to its room",
          feworld.town_door_for(dd, 39, 1.0, 2.0) == 10)
    rows2 = fegamedata.area_portals(39)
    check("...while --doors dat still reaches the SHIPPED table",
          rows2 and feworld.dat_door_for(
              dd, 39, rows2[0]["gate"][0], rows2[0]["gate"][1], 9.0) is not None)
    # The exact shape of the old bug: a list where the mode belongs.
    broken = _Args(doors=[(39, 1.0, 2.0, 10)], door_rows=[], door_radius=15.0,
                   door_height="client", spawn_area="", spawn_file=os.devnull)
    broken.spawn_xyz = (0.0, 0.0, 0.0); broken.self_xyz = (0.0, 0.0, 0.0)
    check("a list in `doors` is exactly what silenced the table",
          rows2 and feworld.dat_door_for(
              broken, 39, rows2[0]["gate"][0], rows2[0]["gate"][1], 9.0) is None,
          "this is the state prod ran in")

    print("\nwhat a door does after it resolves")
    # WARNING: THREE BUGS THAT ONLY SHOWED UP ON A LIVE RUN, 2026-09-10.
    #
    # 1. ROOM INDICES AND AREA IDS OVERLAP. The empty-room guard decided
    #    "room or area?" from the NUMBER -- inside 9..30 meant room -- so a
    #    shipped door pointing at AREA 21 (a capital) was read as room 21, hit
    #    the guard and answered NG. The player stood in the doorway. The same
    #    door the other way pointed at 91, outside the window, and worked --
    #    which is why it only ever failed in one direction.
    dr = _LooseArgs(doors="dat", door_rows=[], door_radius=15.0,
               door_height="client", spawn_area="", spawn_file=os.devnull,
               territory_file=os.devnull, town_file="", unit_id="1",
               seq_mode="count", world_prefix=4, spawn_height="dat",
               field_items="off", bag_size=30, self_unit="none",
               enter_room=-1, field_rooms="", territory="")
    dr.spawn_xyz = (0.0, 0.0, 0.0); dr.self_xyz = (0.0, 0.0, 0.0)
    dr.spawn_dir_xyz = (0.0, 0.0, 1.0)            # --spawn-dir's default
    feworld.spawn_load(dr)
    collide = [a for a in fegamedata.areas() if a in feworld.ROOM_INDEX_WINDOW]
    check("area ids and room indices really do overlap",
          bool(collide), "%d areas sit inside the room window" % len(collide))
    hit21 = feworld.dat_door_for(dr, 91, -5.0, -51.7, 34.99)
    check("the shipped door in 91 leads to an AREA inside the room window",
          hit21 is not None and hit21[0] in feworld.ROOM_INDEX_WINDOW,
          repr(hit21))

    # 2. A DOOR'S ARRIVAL POSITION WAS DROPPED for a cross-area move: 0x1166's
    #    position only applies to a same-field warp, and the entry that
    #    follows asks spawn_for(). It has to be handed forward.
    feworld._TLS.session = {"in_field": True, "field": 91, "cpos": (0, 35, 0)}
    feworld.spawn_add(dr, 21, (99.0, 9.0, 99.0))          # 21's own spawn point
    sent = []
    real = feworld.send
    feworld.send = lambda *a, **k: sent.append(a)
    try:
        feworld.goto_push(None, None, 0, False, dr, 21, pos=(-5.0, 35.0, -46.0))
    finally:
        feworld.send = real
    feworld._TLS.session["field"] = 21                     # the client arrives
    check("the door's arrival point wins over the area's spawn on entry",
          feworld.spawn_for(dr, 21) == (-5.0, 35.0, -46.0),
          repr(feworld.spawn_for(dr, 21)))
    feworld.send = lambda *a, **k: None
    feworld.goto_push(None, None, 0, False, dr, 21)        # a plain !goto
    feworld.send = real
    check("...and a goto with no position of its own clears it again",
          feworld.spawn_for(dr, 21) == (99.0, 9.0, 99.0),
          repr(feworld.spawn_for(dr, 21)))
    feworld.spawn_drop(dr, 21)

    # 2b. WHICH DOOR YOU CAME OUT OF. The panel pre-selects it, so fixing a bad
    #     landing is: walk through, walk to the right spot, press one button.
    #     It must be recorded where the move COMMITS -- a door that is looked
    #     up and then refused by the latch must never be reported as the door
    #     you came through.
    feworld._TLS.session = {"in_field": True, "field": 91, "cpos": (0, 35, 0)}
    hitv = feworld.dat_door_for(dr, 91, -5.0, -51.7, 34.99)
    check("dat_door_for names the portal you come OUT of",
          hitv is not None and len(hitv) == 4
          and fegamedata.portal(hitv[2])["area"] == hitv[0], repr(hitv))

    # WARNING:WARNING: THE DOOR BUG, 2026-09-11. Every capital door put the player OFF the
    # destination map's collision: dat_door_for read the spawn of the portal
    # you come out NEXT TO, when a portal's shipped spawn is where walking
    # through THAT portal puts you. The client stores y = 10000 on entry, asks
    # its terrain for the ground under (x, z), finds nothing, skips the height
    # write (0x04ffa10b), and the player hangs frozen over empty space. The
    # live trip that hit it: through portal 12 in 91, into 21.
    p12 = fegamedata.portal(12)
    p2 = fegamedata.portal(2)
    check("through portal 12 you land at portal 12's OWN spawn, not portal 2's",
          abs(hitv[1][0] - p12["spawn"][0]) < 1e-6
          and abs(hitv[1][2] - p12["spawn"][1]) < 1e-6
          and (hitv[1][0], hitv[1][2]) != tuple(p2["spawn"]),
          "got %r, portal 12 ships %r, portal 2 ships %r"
          % ((hitv[1][0], hitv[1][2]), p12["spawn"], p2["spawn"]))
    check("...which has ground in area 21's collision (the old spot had none)",
          fegamedata.capital_ground(21, hitv[1][0], hitv[1][2]) is not None
          and fegamedata.capital_ground(21, *p2["spawn"]) is None)
    check("...and you arrive facing the way portal 12 ships, away from the door",
          tuple(hitv[3]) == tuple(p12["face"]), repr(hitv[3]))
    # The invariant that makes EVERY door work, not just the reported one.
    stuck = []
    n_caps = 0
    for a_ in sorted(fegamedata.MINIMAP_FILE):
        for pp in fegamedata.area_portals(a_):
            dd = fegamedata.portal(pp["dest"])
            if dd is None or not fegamedata.minimap_name(dd["area"]):
                continue
            n_caps += 1
            hh = feworld.dat_door_for(dr, a_, pp["gate"][0], pp["gate"][1],
                                      30.0)
            if hh is None or fegamedata.capital_ground(
                    hh[0], hh[1][0], hh[1][2]) is None:
                stuck.append(pp["portal"])
    check("EVERY door between capital halves lands on ground (%d doors)"
          % n_caps, n_caps == 64 and not stuck, "no ground for %r" % stuck)
    # Facing is a direction vector the client adds to the position to get a
    # look-at target (0x05078e40) -- it must be unit length and never zero.
    feworld.send = lambda *a, **k: None
    try:
        feworld.goto_push(None, None, 0, False, dr, 21, pos=(0.0, 30.0, 0.0),
                          face=(-1.0, 1.0))
        feworld._TLS.session["field"] = 21
        fd = feworld.arrival_dir(dr)
        check("a shipped facing like (-1, 1) is served as a unit vector",
              abs(fd[0] + 0.70711) < 1e-4 and fd[1] == 0.0
              and abs(fd[2] - 0.70711) < 1e-4, repr(fd))
        feworld.goto_push(None, None, 0, False, dr, 21)
        check("...and a plain !goto goes back to --spawn-dir",
              feworld.arrival_dir(dr) == tuple(dr.spawn_dir_xyz),
              repr(feworld.arrival_dir(dr)))
    finally:
        feworld.send = real
    feworld._TLS.session = {"in_field": True, "field": 91, "cpos": (0, 35, 0)}
    check("...and merely LOOKING a door up records nothing",
          "arrived_via" not in feworld._TLS.session)
    feworld.send = lambda *a, **k: None
    try:
        feworld.goto_push(None, None, 0, False, dr, hitv[0], pos=hitv[1],
                          via=hitv[2])
        check("committing the move records the door you came out of",
              feworld._TLS.session.get("arrived_via") == hitv[2])
        feworld.devtool_snapshot()
        with feworld._DEVSNAP_LOCK:
            pub = feworld._DEVSNAP.get("arrived_via")
        check("...and publishes it for the panel's thread", pub == hitv[2],
              repr(pub))
        feworld.goto_push(None, None, 0, False, dr, 21)     # a plain !goto
        check("...and a plain !goto forgets it",
              "arrived_via" not in feworld._TLS.session)
    finally:
        feworld.send = real

    # 3. `last_door` survived an area change, so the second !link compared the
    #    new area's door against a door in the area you just left and called
    #    them "the same door".
    # WARNING: THE THIRD FIX IS NOT TESTED HERE, and that is a deliberate choice
    # rather than an oversight. `last_door` is dropped on area change by one
    # line in enter_area, immediately beside the identical `cpos` line that
    # has been there for months. Driving enter_area from this harness needs
    # ~20 options it does not otherwise care about, and every stub I tried
    # either crashed on an int(None) or would have passed without exercising
    # the line. A green check that proves nothing is worse than no check, so
    # this one is verified by the diff and by the live run instead.

    print("\nauthoring from the map instead of from the client")
    # The panel draws the minimap with the recovered positions on it, so a
    # keeper can be placed by clicking rather than by walking there. What the
    # map CANNOT give is a height -- it is a plan view -- and getting that
    # wrong is the falling loop. So the refusal is the behaviour under test,
    # not an edge case: !place must decline until the area has a spawn row.
    import io as _io
    import contextlib as _ctx
    pt = os.path.join(os.environ.get("TEMP", "."), "fe_place_%d.json" % os.getpid())
    sp2 = os.path.join(os.environ.get("TEMP", "."), "fe_psp_%d.json" % os.getpid())
    for _p in (pt, sp2):
        if os.path.exists(_p):
            os.remove(_p)
    pa = _LooseArgs(town_file=pt, spawn_file=sp2, territory_file=os.devnull,
                    spawn_area="", door_radius=15.0, force_table={},
                    gmcmd_file=None, devtool_port=0)
    pa.spawn_xyz = (0.0, 0.0, 0.0); pa.self_xyz = (0.0, 0.0, 0.0)
    feworld.territory_load(pa); feworld.spawn_load(pa)
    feworld._TLS.session = {"in_field": True, "field": 92, "cpos": (0.0, 21.0, 0.0)}

    def _gm(line):
        """Run one operator line the way the panel's queue does."""
        import fedevtool as _fdt
        _fdt.enqueue(line)
        buf = _io.StringIO()
        real = feworld.send
        feworld.send = lambda *a, **k: None
        try:
            with _ctx.redirect_stdout(buf):
                feworld.gm_pump(None, None, 0, False, pa)
        finally:
            feworld.send = real
        return buf.getvalue()

    out = _gm("!place -225.9:-36.9 Warrior_Weapon_Shop:1:1:180")
    check("!place REFUSES while the area has no height to place at",
          "no spawn row" in out and not feworld.town_get(pa, 92, "npcs"),
          out.strip()[:90])
    feworld.spawn_add(pa, 92, (0.0, 21.0, 0.0))
    out = _gm("!place -225.9:-36.9 Warrior_Weapon_Shop:1:1:180")
    rows_p = feworld.town_get(pa, 92, "npcs")
    check("...and places it once there is one, at that height",
          len(rows_p) == 1 and rows_p[0]["x"] == -225.9
          and rows_p[0]["z"] == -36.9 and rows_p[0]["y"] == 21.0
          and rows_p[0]["yaw"] == 180.0, repr(rows_p))
    check("the model and script come from the shipped roster, not the click",
          rows_p[0]["script"] == 2302 and rows_p[0]["model"] == 233,
          repr(rows_p[0]))
    out = _gm("!place 0:0 Not_A_Real_Shop")
    check("a role the capital does not have is refused",
          "not one of area" in out and len(feworld.town_get(pa, 92, "npcs")) == 1)
    out = _gm("!place nonsense Warrior_Ring_Shop")
    check("a malformed coordinate is refused rather than placed at 0,0",
          "wants X:Z" in out and len(feworld.town_get(pa, 92, "npcs")) == 1)
    _gm("!spawnat -100.0:25.0 def")
    check("!spawnat sets a tagged arrival from map coordinates",
          feworld.spawn_for(pa, 92, tag="def") == (-100.0, 21.0, 25.0),
          repr(feworld.spawn_for(pa, 92, tag="def")))
    for _p in (pt, sp2):
        try:
            os.remove(_p)
        except OSError:
            pass

    print("\nthe re-entry latch")
    # WARNING: NARROWED 2026-09-11. The latch was written while arrivals were read
    # backwards and landed on top of doors. With them right, SE's arrivals sit
    # OUTSIDE the door you came through -- and the old latch (door radius +
    # --door-radius 15 around the arrival) swallowed every ordinary return
    # trip. Live: through portal 16 into 21, arrived at (-112, -33), walked
    # back into portal 6 at (-113, -39), touch IGNORED -- and the refusal does
    # not clear the client's pending door, so the player sat on "waiting".
    la = _LooseArgs(door_radius=15.0, doors="dat", door_height="dest",
                    spawn_file=os.devnull, spawn_area="",
                    territory_file=os.devnull, town_file="")
    la.spawn_xyz = (0.0, 0.0, 0.0)
    feworld._TLS.session = {"in_field": True, "field": 21,
                            "cpos": (-112.0, 20.6, -36.0),
                            "door_latch": (21, -112.0, -33.0)}
    check("walking straight back into the door you came out beside is ANSWERED",
          feworld.door_latch_ok(la, 21, -113.0, -39.0),
          "the live case: 6.1 units from the arrival, outside the door")
    check("...and that touch clears the latch",
          "door_latch" not in feworld._TLS.session)
    # The one case the latch is still for: put down INSIDE the door touched.
    feworld._TLS.session = {"in_field": True, "field": 91,
                            "cpos": (-5.0, 35.0, -50.5),
                            "door_latch": (91, -5.0, -50.5)}
    check("a door you were put down INSIDE does not fire until you step out",
          not feworld.door_latch_ok(la, 91, -5.0, -51.7))
    feworld._TLS.session["cpos"] = (-5.0, 35.0, -44.0)
    check("...and arms again once you are out of its trigger",
          feworld.door_latch_ok(la, 91, -5.0, -51.7))
    feworld._TLS.session["door_latch"] = (91, -5.0, -50.5)
    check("a latch from another area never blocks this one's doors",
          feworld.door_latch_ok(la, 21, -5.0, -51.7))
    # and no shipped arrival is a trap at all -- which is why it never holds
    traps = []
    for a_ in sorted(fegamedata.MINIMAP_FILE):
        for pp in fegamedata.area_portals(a_):
            dd = fegamedata.portal(pp["dest"])
            if dd and ((pp["spawn"][0] - dd["gate"][0]) ** 2 + (pp["spawn"][1]
                       - dd["gate"][1]) ** 2) ** 0.5 <= feworld.DOOR_TRAP_RADIUS:
                traps.append(pp["portal"])
    check("no shipped arrival is inside the door it lands beside", not traps,
          repr(traps))

    print("\n!link writes both sides")
    tf = os.path.join(os.environ.get("TEMP", "."),
                      "fe_town_link_%d.json" % os.getpid())
    lk = _Args(town_file=tf, door_radius=15.0)
    A_, B_ = {"gid": 39, "dx": 186.5, "dz": -132.5, "ax": 180.0, "ay": 21.0, "az": -128.0}, \
             {"gid": 92, "dx": -271.5, "dz": -51.2, "ax": -265.0, "ay": 18.5, "az": -46.0}
    for src, dst in ((A_, B_), (B_, A_)):
        feworld.town_add(lk, src["gid"], "doors",
                         {"x": src["dx"], "z": src["dz"], "dest_area": dst["gid"],
                          "dest_x": dst["ax"], "dest_y": dst["ay"], "dest_z": dst["az"]})
    fwd = feworld.town_link_for(lk, 39, 186.5, -132.5)
    back = feworld.town_link_for(lk, 92, -271.5, -51.2)
    check("the door leads where you walked", fwd == (92, (-265.0, 18.5, -46.0)), repr(fwd))
    check("and walking back returns you to where you left",
          back == (39, (180.0, 21.0, -128.0)), repr(back))
    check("a link carries a HEIGHT, which a plain !door row cannot",
          fwd is not None and fwd[1][1] == 18.5)
    check("an unlinked spot in the same area matches nothing",
          feworld.town_link_for(lk, 39, 0.0, 0.0) is None)
    try:
        os.remove(tf)
    except OSError:
        pass

    print("\nthe world-building panel")
    import json as _json
    import urllib.request as _url
    import urllib.error as _uerr
    import fedevtool

    dv = _Args(town_file="", door_radius=15.0, devtool_port=8791,
               gmcmd_file=None, spawn_file=os.devnull,
               territory_file=os.devnull, spawn_area="", spawn_height="dat",
               force_table={2: {"name": "Cathedira"}})
    dv.spawn_xyz = (0.0, 0.0, 0.0); dv.self_xyz = (0.0, 0.0, 0.0)
    feworld.territory_load(dv); feworld.spawn_load(dv)

    # -----------------------------------------------------------------
    # WARNING: A MONSTER AT y=0 IS UNDER THE TERRAIN (2026-09-12, live)
    #
    # fet_npc_generator_pos ships NO world height: 714 of its 721 rows carry
    # y = 0 exactly. Every map that ships samples has its lowest ground at 8
    # and medians 8..62, so the table's 0 is metres under the floor, and the
    # server's own reach test read every monster tens of units off.
    # (WARNING: This comment first claimed the buried Y was why monsters walked in
    # place, via bit 0x800000. RETRACTED: that bit cannot stop a monster --
    # 0x0504A4A0 returns 0 for anything but the player. The walking-in-place
    # was the move DURATION; see fe_combat_test.)
    ys = [y for _f in fegamedata.fields_with_spawns()
          for (_x, y, _z, _r, _m, _n, _g, _t) in fegamedata.spawn_points(_f)]
    check("the shipped spawn table really has no height (>95% of rows y=0)",
          ys and sum(1 for y in ys if y == 0.0) > 0.95 * len(ys),
          "%d of %d rows are y=0" % (sum(1 for y in ys if y == 0.0), len(ys)))
    lows = [v["min"] for v in fegamedata.map_heights().values()]
    check("...and no map's ground ever reaches 0, so y=0 is always under it",
          lows and min(lows) > 0, "lowest sampled ground = %s" % (min(lows) if lows else None))
    hs = [feworld.field_ground_height(a) for a in (1, 6, 8, 12, 84)]
    check("field_ground_height answers for every area, maps 7/10 included",
          all(h is not None and h > 0 for h in hs), repr(hs))

    # 2026-09-12: the monster speeds, NPC_ModelType +0x41c (walk) / +0x420
    # (run). Read out of dat.pak in the client's own record order.
    sp = fegamedata.model_speeds()
    check("NPC_ModelType speeds ship for all 271 models",
          len(sp) == 271, len(sp))
    check("...walk <= run on EVERY row -- what says the two floats are "
          "named right",
          sp and all(w <= r for w, r in sp.values()),
          [k for k, (w, r) in sp.items() if w > r][:5])
    check("...Duke_Orc (17) walks 1.8 and runs 3.7 u/s",
          sp.get(17) == (1.8, 3.7), sp.get(17))
    check("...every monster served from the spawn table has a row",
          all(fegamedata.model_speed(m) for _f in fegamedata.fields_with_spawns()
              for (_x, _y, _z, _r, m, _n, _g, _t) in fegamedata.spawn_points(_f)))

    # 2026-09-12: MONSTER DENSITY. We served ONE monster per spawn point; the
    # generator record carries a population (+0x10), per-type weights (+0x28)
    # and per-type caps (+0x3c), read from dat.pak in the client's own order.
    pops = fegamedata.spawn_populations()
    check("the spawner population table ships all 192 generators",
          len(pops) == 192, len(pops))
    nine = sum(1 for g in pops.values() if g["pop"] == 9)
    tied = [g for g in pops.values() if g["pop2"] != 255]
    exact = sum(1 for g in tied
                if g["pop"] == sum(c for _t, _w, c in g["types"]))
    check("...+0x10 is 9 on most rows, and where +0x11 is not 255 it EQUALS "
          "the caps' sum on most -- what says +0x10 is a COUNT",
          nine >= 150 and exact >= 25, (nine, exact, len(tied)))
    w100 = sum(1 for g in pops.values()
               if round(sum(w for _t, w, _c in g["types"])) == 100)
    check("...and the weights sum to 100 on nearly every row", w100 >= 190, w100)
    al = fegamedata.allocate_population(
        9, [(1, 40, 5), (2, 20, 3), (3, 20, 3), (4, 10, 1), (5, 10, 1)])
    check("allocation: 9 by weight 40/20/20/10/10, never past caps 5/3/3/1/1",
          sum(al.values()) == 9
          and all(al.get(t, 0) <= c for t, c in ((1, 5), (2, 3), (3, 3),
                                                  (4, 1), (5, 1))), al)
    check("...and the total stops at the caps' sum when the population is larger",
          sum(fegamedata.allocate_population(50, [(1, 60, 2), (2, 40, 1)]).values()) == 3)
    grp = fegamedata.spawn_groups(12)
    tot = sum(n for (_x, _y, _z, _r, _g, members) in grp
              for (_t, _m, _nm, n) in members)
    check("field 12: every point gets a GROUP -- %d monsters at %d points, not "
          "one each" % (tot, len(grp)), len(grp) >= 7 and tot > 3 * len(grp),
          (tot, len(grp)))
    check("...every member's model is a shipped one",
          all(m in fegamedata.SHIPPED_MODELTYPES
              for (_x, _y, _z, _r, _g, members) in grp
              for (_t, m, _nm, _n) in members))
    sent = []
    real_send = feworld.npc_send

    def _cap(conn, out, mode, be, args, obj, mtype, kind, level, x, y, z, **kw):
        sent.append((obj, mtype, x, y, z, kw.get("type_id")))
    saved_tls = getattr(feworld._TLS, "session", None)
    feworld.npc_send = _cap
    try:
        feworld._TLS.session = {"field": 12, "mobs": {}}
        sa = types.SimpleNamespace(spawns="dat", spawns_max=12, monster_base=400,
                                   monster_height="dat", spawn_population="dat",
                                   spawn_mobs_max=200, monster_level=1)
        feworld.spawn_push(None, None, None, None, sa)
        ids = [e[0] for e in sent]
        check("spawn_push serves the groups: %d monsters, unique ids, all below "
              "the keeps' 2900" % len(sent),
              len(sent) == min(tot, 200) and len(set(ids)) == len(ids)
              and ids and max(ids) < 2900, (len(sent), ids and max(ids)))
        # 2026-09-12: the default layout is now SCATTER (seeded packs within
        # --spawn-spread 2.0 x the radius -- live testing found the discs
        # "inorganic"); the old inside-the-radius property is the SPIRAL's,
        # asserted under that layout below
        check("...each within its point's scatter reach (2 x radius + a pack's 5 u)",
              all(any(((e[2] - px) ** 2 + (e[4] - pz) ** 2) ** 0.5
                      <= 2.0 * rad + 5.0 + 1e-6
                      for (px, _py, pz, rad, _g, _m) in grp) for e in sent))
        del sent[:]
        feworld._TLS.session = {"field": 12, "mobs": {}}
        feworld.spawn_push(None, None, None, None,
                           types.SimpleNamespace(**dict(vars(sa),
                                                        spawn_layout="spiral")))
        check("--spawn-layout spiral keeps each inside its point's own radius",
              sent and all(any(((e[2] - px) ** 2 + (e[4] - pz) ** 2) ** 0.5
                               <= rad + 1e-6
                               for (px, _py, pz, rad, _g, _m) in grp)
                           for e in sent))
        del sent[:]
        feworld._TLS.session = {"field": 12, "mobs": {}}
        feworld.spawn_push(None, None, None, None,
                           types.SimpleNamespace(**dict(vars(sa),
                                                        spawn_population="one")))
        check("--spawn-population one restores one monster per point",
              len(sent) == len(fegamedata.spawn_points(12)),
              (len(sent), len(fegamedata.spawn_points(12))))
        del sent[:]
        feworld._TLS.session = {"field": 12, "mobs": {}}
        feworld.spawn_push(None, None, None, None,
                           types.SimpleNamespace(**dict(vars(sa),
                                                        spawn_mobs_max=10)))
        check("--spawn-mobs-max caps the monsters", len(sent) == 10, len(sent))
    finally:
        feworld.npc_send = real_send
        feworld._TLS.session = saved_tls if saved_tls is not None else {}

    # -----------------------------------------------------------------
    # WARNING: THE FIELD-READY GRACE MUST NEVER OUTLIVE ITS FIELD (2026-09-12)
    #
    # field_ready_pump releases the held batch --field-ready-ms after 0x100E,
    # counted from _SESSION["add_complete_at"]. The first cut set that stamp
    # and never cleared it, so on a SECOND field entry it still held the
    # PREVIOUS field's 0x100E -- already seconds old -- and the batch fired
    # the instant the new field's 0x2000 armed, 2.5 s BEFORE that field's own
    # 0x100E. Live: the 0x107A Equip landed DURING field load and the
    # player's character could not move in the second area. That is the
    # 2026-09-05 #14 freeze ("equip DURING field entry ... froze movement"),
    # reintroduced by a stamp that was set but never cleared.
    #
    # The invariant, and it is the whole point: the pump is a NO-OP until
    # THIS field's 0x100E has been sent.
    import time as _t
    S = feworld._TLS.session = {}
    ra = types.SimpleNamespace(field_ready_ms=1500)
    # (no *_replay_due here: field_ready_release then sends nothing and only
    #  flips the flag, so these checks are about the DECISION, not the batch)
    S.update(in_field=True, field_ready=False)
    check("no 0x100E yet -> the batch is held, however long the field takes",
          feworld.field_ready_pump(None, None, "ecb", False, ra) is False
          and not S.get("field_ready"))
    S["add_complete_at"] = _t.monotonic() - 9.0      # a PREVIOUS field's stamp
    check("...and a stale stamp from the last field DOES release it early -- "
          "which is the live bug, and makes the next check non-vacuous",
          feworld.field_ready_pump(None, None, "ecb", False, ra) is True
          and S.get("field_ready"))
    # now the real sequence: entering a new field must drop that stamp
    S.clear()
    S.update(in_field=True, add_complete_at=_t.monotonic() - 9.0)
    S["field_ready"] = False
    S.pop("add_complete_at", None)                   # what the 0x2000 arm does
    check("a new field entry CLEARS the grace clock, so the batch waits for "
          "THIS field's 0x100E",
          feworld.field_ready_pump(None, None, "ecb", False, ra) is False
          and not S.get("field_ready"))
    src = inspect.getsource(feworld._serve_loop)
    check("...and the 0x2000 arm really does clear it beside field_ready",
          '_SESSION.pop("add_complete_at", None)' in src
          and src.index('_SESSION["field_ready"] = False')
          < src.index('_SESSION.pop("add_complete_at", None)'))
    S.clear()
    S.update(in_field=True, field_ready=False, add_complete_at=_t.monotonic())
    check("...and once 0x100E lands, the batch still waits out the grace",
          feworld.field_ready_pump(None, None, "ecb", False, ra) is False)
    S["add_complete_at"] = _t.monotonic() - 2.0
    check("...then fires without any movement sample",
          feworld.field_ready_pump(None, None, "ecb", False, ra)
          and S.get("field_ready"))
    check("--field-ready-ms 0 restores the movement-only trigger",
          feworld.field_ready_pump(
              None, None, "ecb", False,
              types.SimpleNamespace(field_ready_ms=0)) is False)
    feworld._TLS.session = {}

    # WARNING: THE TRAP THIS PINS. _SESSION is THREAD-LOCAL (_TLS.session, one dict
    # per connection), and the panel's HTTP handler runs on its own thread --
    # so a state builder that read _SESSION directly would report "not in a
    # field" forever, from an empty session, with nothing visibly wrong. The
    # player's thread publishes a snapshot instead. Assert the snapshot is
    # what carries it, by building the state with the session set and then
    # again from a thread that has none.
    feworld._TLS.session = {"in_field": True, "field": 39,
                            "cpos": (186.5, 21.0, -132.5)}
    feworld.devtool_snapshot()
    st = feworld.devtool_state(dv)
    check("the panel sees the area the player is in", st["area"] == 39)
    check("...and its shipped roster", st["roster_total"] == 20)
    check("...and its shipped doors", st["doors_total"] == 4)
    out = {}

    def _other_thread():
        out["st"] = feworld.devtool_state(dv)

    t = __import__("threading").Thread(target=_other_thread)
    t.start(); t.join()
    check("a DIFFERENT thread sees the same state (the snapshot, not _SESSION)",
          out["st"]["area"] == 39 and out["st"]["in_field"],
          "this is the thread-local trap")

    # An id box is a memory test with a fall-into-the-void failure mode, so the
    # panel offers named destinations. The one that must be first is a
    # capital's OTHER HALF -- that is what a capital door is for.
    check("a capital offers its own other half first",
          st["destinations"] and st["destinations"][0]["area"] == 92
          and "other half" in st["destinations"][0]["why"],
          repr(st["destinations"][:1]))
    check("...and then real neighbours, by name",
          all(d["area"] in fegamedata.areas() for d in st["destinations"])
          and all(d["name"] for d in st["destinations"]))
    check("no destination is offered twice",
          len({d["area"] for d in st["destinations"]}) == len(st["destinations"]))
    feworld._TLS.session = {"in_field": True, "field": 1, "cpos": (0.0, 20.0, 0.0)}
    feworld.devtool_snapshot()
    plain = feworld.devtool_state(dv)
    check("a war field offers only what it borders",
          [d["area"] for d in plain["destinations"]]
          == fegamedata.areas()[1]["neighbours"])
    feworld._TLS.session = {"in_field": True, "field": 39,
                            "cpos": (186.5, 21.0, -132.5)}
    feworld.devtool_snapshot()

    # ---- the minimap the panel draws ------------------------------------
    # The in-game minimap is too small to tell a sword from a ring, and those
    # icons are the best evidence of what belongs where -- so the panel serves
    # the art and everything projects onto it. The check that matters is that
    # the projection is the INVERSE of the one the positions were recovered
    # with: a shop's world position must land back on the pixel its icon is
    # drawn at, or the overlay is decorative.
    mm = feworld.devtool_state(dv)
    check("a capital carries its minimap and the projection",
          mm["minimap"] == "map01_00" and mm["proj"]
          and mm["proj"]["size"] == 256, repr(mm["minimap"]))
    for _lbl, _wx, _wz, _px, _py in (
            # measured off the art by eye on 2026-09-09, before any of this
            ("the blue ring", -160.7, 52.7, 62.0, 105.9),
            ("the red sword", -225.9, -36.9, 35.4, 142.4)):
        _gx, _gy = fegamedata.world_to_pixel(_wx, _wz)
        check("%s projects back onto its own art" % _lbl,
              abs(_gx - _px) < 1.5 and abs(_gy - _py) < 1.5,
              "(%.1f,%.1f) vs (%.1f,%.1f)" % (_gx, _gy, _px, _py))
    check("every overlay lands inside the 256 px image",
          all(0 <= fegamedata.world_to_pixel(i["x"], i["z"])[0] < 256
              and 0 <= fegamedata.world_to_pixel(i["x"], i["z"])[1] < 256
              for i in fegamedata.shop_icons(trusted_only=False)))
    check("the art ships for all ten capital halves",
          all(os.path.isfile(os.path.join(
              os.path.dirname(os.path.abspath(feworld.__file__)),
              "fedata", "minimaps", fegamedata.minimap_name(c) + ".png"))
              for c in feworld.CAPITAL_GROUP_IDS))
    feworld._TLS.session = {"in_field": True, "field": 1, "cpos": (0.0, 20.0, 0.0)}
    feworld.devtool_snapshot()
    check("a war field has no minimap and says so rather than 404ing",
          feworld.devtool_state(dv)["minimap"] is None)
    feworld._TLS.session = {"in_field": True, "field": 39,
                            "cpos": (186.5, 21.0, -132.5)}
    feworld.devtool_snapshot()

    srv = fedevtool.start(dv.devtool_port, "127.0.0.1", "tok",
                          lambda: feworld.devtool_state(dv))
    try:
        base = "http://127.0.0.1:%d" % dv.devtool_port
        # The shell is gated like everything else on this port: on a LAN
        # bind an unauthenticated scanner meets a 403, not the authoring
        # UI. The browser always arrives carrying ?t=, so the operator
        # never sees the difference.
        shut = None
        try:
            _url.urlopen(base + "/", timeout=5)
        except _uerr.HTTPError as e:
            shut = e.code
        check("the page shell without a token is refused", shut == 403,
              repr(shut))
        check("the page serves with the token",
              _url.urlopen(base + "/?t=tok", timeout=5).status == 200)
        refused = False
        try:
            _url.urlopen(base + "/state", timeout=5)
        except _uerr.HTTPError as e:
            refused = (e.code == 403)
        check("state without a token is refused", refused)
        got = _json.loads(_url.urlopen(base + "/state?t=tok", timeout=5).read())
        check("state with the token carries the missing roles",
              len(got["missing"]) == 20 and got["missing"][0]["script"] == 2301)
        # Both halves have to survive the JSON, not just devtool_state: the
        # browser is what draws them and it only ever sees the wire.
        check("...and BOTH halves of the capital, over the wire",
              [h["area"] for h in got.get("halves", [])] == [39, 92]
              and got["halves"][1]["minimap"] == "map01_01",
              repr([h.get("area") for h in got.get("halves", [])]))
        check("...each half carrying the projection the page draws with",
              all(h["proj"].get("stem") and "ax" in h["proj"]
                  for h in got["halves"]))
        check("...and each shipped door carrying the arrival it hands out",
              all("ax" in d and "portal" in d
                  for h in got["halves"] for d in h["gates"]))
        check("...and a NAME for where it comes out, not just an id",
              all(d["dest_name"] for h in got["halves"] for d in h["gates"]),
              "a yellow dot among yellow dots cannot be linked to anything")
        req = _url.Request(base + "/cmd?t=tok", data=b"!spawn atk", method="POST")
        check("a command queues", _json.loads(
            _url.urlopen(req, timeout=5).read())["queued"] is True)
        check("...and gm_pump is what would run it",
              fedevtool.drain() == ["!spawn atk"])
        bad = False
        img = _url.urlopen(base + "/map/map01_00.png?t=tok", timeout=5)
        check("the minimap art serves as a PNG",
              img.status == 200 and img.headers.get("Content-Type") == "image/png")
        for _p, _lbl, _want in (("/map/../feworld.py?t=tok", "a traversal", 404),
                                ("/map/nope.png?t=tok", "a missing map", 404),
                                ("/map/map01_00.png", "no token", 403)):
            _got = None
            try:
                _url.urlopen(base + _p, timeout=5)
            except _uerr.HTTPError as e:
                _got = e.code
            check("%s is refused" % _lbl, _got == _want, repr(_got))
        req = _url.Request(base + "/cmd?t=nope", data=b"!goto 1", method="POST")
        try:
            _url.urlopen(req, timeout=5)
        except _uerr.HTTPError as e:
            bad = (e.code == 403)
        check("a wrong token cannot run a command", bad)
    finally:
        srv.shutdown()
    wide = False
    try:
        fedevtool.start(8792, "0.0.0.0", "", lambda: {})
    except SystemExit:
        wide = True
    check("binding wide with no token is refused, not warned about", wide)

    # ---- the minimap calibration, per half -------------------------------
    # KEY: ONE LINEAR FIT CANNOT SERVE TEN MAPS. The shipped MINIMAP_A*/C* are
    # sub-pixel on six halves and 15-33 px out on 21, 91, 57 and 93, because
    # each half is its own map with its own origin. The fix is per-map anchors,
    # and the checks below are all about the solver REFUSING to over-fit --
    # a projection that quietly invents a scale drags the whole overlay with
    # it, and the only visible symptom is "it looks a bit off".
    print("\nthe minimap calibration")
    import tempfile as _tmp
    _cald = _tmp.mkdtemp()
    ca = _Args(town_file=os.path.join(_cald, "town.json"), door_radius=15.0,
               devtool_port=8793, gmcmd_file=None,
               spawn_file=os.path.join(_cald, "spawn.json"),
               territory_file=os.devnull, spawn_area="", spawn_height="dat",
               mapcal_file=os.path.join(_cald, "cal.json"),
               door_arrive_file=os.path.join(_cald, "arr.json"),
               doors="dat", door_height="dest", force_table={},
               seq_mode="count", world_prefix=4, unit_id="1")
    ca.spawn_xyz = (0.0, 0.0, 0.0); ca.self_xyz = (0.0, 0.0, 0.0)
    feworld.territory_load(ca); feworld.spawn_load(ca)
    feworld.mapcal_load(ca); feworld.doorarr_load(ca)

    def _cgm(line):
        _fdt2 = __import__("fedevtool")
        _fdt2.enqueue(line)
        buf = _io.StringIO()
        real = feworld.send
        feworld.send = lambda *a, **k: None
        try:
            with _ctx.redirect_stdout(buf):
                feworld.gm_pump(None, None, 0, False, ca)
        finally:
            feworld.send = real
        return buf.getvalue()

    # The solver is exercised on stems that ship NO anchors, so these test the
    # arithmetic and nothing else. The shipped defaults get their own checks
    # below, against the halves they were fitted for.
    A, B, C, D = "tcal_a", "tcal_b", "tcal_c", "tcal_d"
    check("a stem with no anchors uses the shipped fit",
          feworld.mapcal_proj(A)["ax"] == fegamedata.MINIMAP_AX
          and feworld.mapcal_proj(A)["anchors"] == 0)

    # ONE anchor moves the origin and nothing else -- one point cannot see a
    # scale, and the origin is what actually differs between two maps.
    feworld.mapcal_add(ca, A, 117.2, -18.2, 197.0, 139.2, "east gate")
    one = feworld.mapcal_proj(A)
    check("one anchor solves the OFFSET and keeps the shipped scale",
          one["ax"] == fegamedata.MINIMAP_AX
          and one["az"] == fegamedata.MINIMAP_AZ
          and "offset from 1 anchor" in one["how_x"])
    check("...and the anchored point lands exactly where it was put",
          abs(117.2 / one["ax"] + one["cx"] - 197.0) < 1e-9
          and abs(-18.2 / one["az"] + one["cz"] - 139.2) < 1e-9)

    # WARNING: THE GUARD THAT MATTERS, AND IT IS MEASURED IN PIXELS. Area 39's two
    # shipped doors have IDENTICAL x (186.5 both): zero px apart on the art.
    # Least-squares on those is a divide by nothing and would fling the whole
    # overlay off the map while looking confident.
    feworld.mapcal_add(ca, B, 186.5, -95.8, 202.3, 167.0)
    feworld.mapcal_add(ca, B, 186.5, -136.0, 202.3, 183.0)
    tight = feworld.mapcal_proj(B)
    check("two anchors at the same x CANNOT invent an x scale",
          "too close to fit a scale" in tight["how_x"], tight["how_x"])
    check("...and the px span is what decided it, not the world span",
          "0 px apart" in tight["how_x"], tight["how_x"])

    # Anchors far enough apart ON THE ART do fit a scale -- prove it by
    # recovering a projection chosen in advance, exactly.
    for wx, wz in ((-300.0, -200.0), (300.0, 200.0), (0.0, 50.0)):
        feworld.mapcal_add(ca, C, wx, wz, wx / 3.0 + 100.0, wz / -2.0 + 120.0)
    fit = feworld.mapcal_proj(C)
    check("anchors spanning the art recover the scale AND the offset",
          abs(fit["ax"] - 3.0) < 1e-6 and abs(fit["cx"] - 100.0) < 1e-6
          and abs(fit["az"] + 2.0) < 1e-6 and abs(fit["cz"] - 120.0) < 1e-6,
          repr((fit["ax"], fit["cx"], fit["az"], fit["cz"])))
    check("...and every anchor then sits at zero residual",
          max(r["d"] for r in feworld.mapcal_residuals(C)) < 1e-6)

    # KEY: BORROWING A SCALE ACROSS AXES. One axis wide enough to fit, the other
    # not: the near-square prior beats keeping a shipped scale already proved
    # wrong on this map. It must NEVER overwrite an axis that was measured.
    for wx, wz in ((-300.0, -10.0), (300.0, 10.0)):
        feworld.mapcal_add(ca, D, wx, wz, wx / 1.5 + 128.0, wz / -1.5 + 128.0)
    bor = feworld.mapcal_proj(D)
    check("a wide x and a narrow z borrows the x scale for z",
          abs(bor["ax"] - 1.5) < 1e-6 and abs(bor["az"] + 1.5) < 1e-6
          and "borrowed from x" in bor["how_z"], repr(bor["how_z"]))
    check("...and borrowing never overwrites a MEASURED axis",
          "borrow" not in fit["how_x"] and "borrow" not in fit["how_z"],
          "map03's free z fit is 0.03 px and isotropy would be 3.8 px")

    # A scale wildly unlike the shipped one is a bad drag, not a discovery.
    feworld.mapcal_add(ca, "tcal_e", -300.0, 0.0, 120.0, 128.0)
    feworld.mapcal_add(ca, "tcal_e", 300.0, 0.0, 130.0, 128.0)
    wild = feworld.mapcal_proj("tcal_e")
    check("an absurd solved scale is refused, not adopted",
          wild["ax"] == fegamedata.MINIMAP_AX, wild["how_x"])

    check("calibrating one stem does not move another",
          feworld.mapcal_proj("tcal_f")["anchors"] == 0
          and feworld.mapcal_proj(C)["anchors"] == 3)

    # Dragging the same door twice is a CORRECTION. Keeping both would average
    # the mistake back in, so the second drag replaces the first.
    feworld.mapcal_add(ca, C, 0.0, 50.0, 111.0, 111.0)
    check("re-dragging one point REPLACES its anchor, it does not average",
          feworld.mapcal_proj(C)["anchors"] == 3
          and any(abs(r["px"] - 111.0) < 1e-9
                  for r in feworld.mapcal_residuals(C)))
    res = feworld.mapcal_residuals(C)
    check("residuals name the worst anchor first",
          res == sorted(res, key=lambda r: -r["d"]) and res[0]["d"] > 0)
    feworld.mapcal_load(ca)
    check("anchors survive a reload", feworld.mapcal_proj(C)["anchors"] == 3)

    # A grid reference is the NEAREST PAINTED LABEL -- how a person reads the
    # map ("the doors at E5"), measured off the art, identical on every map.
    check("a grid reference names the painted label nearest a pixel",
          fegamedata.minimap_grid(125.0, 157.7) == "E5"
          and fegamedata.minimap_grid(6.5, 25.0) == "A1"
          and fegamedata.minimap_grid(230.5, 248.0) == "H8",
          repr([fegamedata.minimap_grid(125.0, 157.7),
                fegamedata.minimap_grid(6.5, 25.0),
                fegamedata.minimap_grid(230.5, 248.0)]))
    check("...and clamps to the grid rather than inventing a row I or col 9",
          fegamedata.minimap_grid(-40, -40) == "A1"
          and fegamedata.minimap_grid(400, 400) == "H8")

    # ---- the SHIPPED per-half fits ---------------------------------------
    # WARNING: THE SHIPPED CONSTANTS ARE ONE FIT AND THERE ARE TWO SCALES. Held
    # against the portal table, map00's painted doors span 1.47x more art than
    # MINIMAP_AX projects them to: that half is drawn at ~1.68 units per pixel.
    # This is the bug seen live as "everything is further up",
    # and no amount of offset would have fixed it.
    for stem, want_ax, worst in (("map00_00", 1.68, 1.5),
                                 ("map00_01", 1.68, 1.5),
                                 ("map03_00", 2.42, 0.6),
                                 ("map03_01", 2.42, 0.6)):
        p = feworld.mapcal_proj(stem)
        r = feworld.mapcal_residuals(stem)
        check("%s is fitted from its own painted doors" % stem,
              abs(p["ax"] - want_ax) < 0.02 and p["anchors"] >= 3
              and r and r[0]["d"] < worst,
              "ax=%.4f n=%d worst=%.2f" % (p["ax"], p["anchors"],
                                           r[0]["d"] if r else -1))
    check("map00 is drawn at a DIFFERENT scale from map03",
          abs(feworld.mapcal_proj("map00_00")["ax"]
              - feworld.mapcal_proj("map03_00")["ax"]) > 0.6,
          "one constant could never have served both")
    check("a half with too few painted doors keeps the shipped fit",
          feworld.mapcal_proj("map01_00")["anchors"] == 0
          and feworld.mapcal_proj("map04_00")["anchors"] == 0)
    # map02 has only two painted doors, 29 px apart: not enough to fit a
    # scale, so it takes the offset and SAYS it did not take a scale. That
    # honesty is the check -- a silent scale there would be a guess.
    p2 = feworld.mapcal_proj("map02_00")
    check("map02 takes the offset only, and says so",
          p2["anchors"] == 2 and p2["ax"] == fegamedata.MINIMAP_AX
          and "too close to fit a scale" in p2["how_x"], p2["how_x"])

    # WARNING: THE ICON POSITIONS ARE PIXELS, AND CALIBRATION MUST NOT MOVE THEM.
    # x/z were DERIVED from px/py through the shipped fit. A panel that
    # re-projects x/z through a CALIBRATED fit slides every shop icon off the
    # art it was measured from -- so aligning a map would visibly make it
    # worse, which is exactly what live testing reported. The pixel has to
    # travel with the row.
    ic = [i for i in fegamedata.shop_icons(21, trusted_only=False) if i["role"]]
    check("a recovered icon carries the PIXEL it was measured at",
          ic and "px" in ic[0] and "py" in ic[0])
    worst = max(abs(i["x"] / fegamedata.MINIMAP_AX + fegamedata.MINIMAP_CX
                    - i["px"])
                + abs(i["z"] / fegamedata.MINIMAP_AZ + fegamedata.MINIMAP_CZ
                      - i["py"]) for i in ic)
    # 0.2 px is the FILE's own precision, not a fudge: the TSV stores x/z to
    # one decimal, and 0.1 world unit is ~0.04 px, so a perfect round trip
    # still lands ~0.13 px out on the sum of both axes. Measured worst: 0.13.
    check("...and x/z are that pixel through the SHIPPED fit, nothing else",
          worst < 0.2, "worst %.4f px" % worst)
    before = [(i["px"], i["py"]) for i in feworld._devtool_icons(21)]
    feworld.mapcal_add(ca, "map00_00", 0.0, 0.0, 10.0, 10.0, "deliberate shove")
    after = [(i["px"], i["py"]) for i in feworld._devtool_icons(21)]
    check("calibrating a half does NOT move its painted icons",
          before == after and before,
          "the icons are the evidence; the projection is the conclusion")
    feworld.mapcal_drop(ca, "map00_00")   # drops the operator row, not the ship
    check("...and dropping an operator anchor leaves the SHIPPED ones",
          feworld.mapcal_proj("map00_00")["anchors"] == 5)

    # ---- the anchor that needs no door-spotting -------------------------
    # Dragging a door onto its painted self assumes you can TELL which blob is
    # that door. On the four misaligned halves the icon classifier could not
    # (area 21 has three "doors" in a 1 px diagonal row that are scenery), so
    # neither can a person. Where the player is STANDING has no such ambiguity.
    # area 39 / map01_00 ships no anchors, so one operator anchor is the whole
    # evidence and the arithmetic is checkable by hand
    feworld._TLS.session = {"in_field": True, "field": 39, "cpos": None}
    out = _cgm("!mapcal here 100:100")
    check("!mapcal here REFUSES without a reported position",
          "take one step" in out
          and feworld.mapcal_proj("map01_00")["anchors"] == 0, out)
    feworld._TLS.session = {"in_field": True, "field": 39,
                            "cpos": (-5.0, 24.0, -51.7)}
    p0 = feworld.mapcal_proj("map01_00")
    drawn = (-5.0 / p0["ax"] + p0["cx"], -51.7 / p0["az"] + p0["cz"])
    # the command takes the pixel to one decimal, so compare against what was
    # actually SENT, not against the full-precision value it was made from
    said = (round(drawn[0], 1), round(drawn[1] + 9.9, 1))
    out = _cgm("!mapcal here %.1f:%.1f" % said)
    p1 = feworld.mapcal_proj("map01_00")
    check("!mapcal here anchors the player's own position",
          p1["anchors"] == 1 and "offset from 1 anchor" in p1["how_z"], out)
    check("...and the map now draws them exactly where they said they were",
          abs(-5.0 / p1["ax"] + p1["cx"] - said[0]) < 1e-6
          and abs(-51.7 / p1["az"] + p1["cz"] - said[1]) < 1e-6,
          repr((-5.0 / p1["ax"] + p1["cx"], -51.7 / p1["az"] + p1["cz"], said)))
    check("...moving only the offset, never the scale",
          p1["ax"] == p0["ax"] and p1["az"] == p0["az"])
    check("...and it reports the shift it applied",
          "shift of" in out, out)
    feworld.mapcal_drop(ca, "map01_00")
    feworld._TLS.session = {"in_field": True, "field": 39,
                            "cpos": (186.5, 21.0, -132.5)}
    feworld.devtool_snapshot()

    # ---- both halves of a capital ---------------------------------------
    # A capital is TWO maps. Standing in one hid everything about the other,
    # including the half whose alignment you might be trying to fix.
    feworld._TLS.session = {"in_field": True, "field": 39,
                            "cpos": (186.5, 21.0, -132.5)}
    feworld.devtool_snapshot()
    hs = feworld.devtool_state(ca)["halves"]
    check("a capital serves BOTH halves", [h["area"] for h in hs] == [39, 92],
          repr([h["area"] for h in hs]))
    check("...the live one first, and flagged",
          hs[0]["live"] and not hs[1]["live"])
    check("...each with its own art and its own projection",
          hs[0]["minimap"] == "map01_00" and hs[1]["minimap"] == "map01_01"
          and hs[0]["proj"]["stem"] != hs[1]["proj"]["stem"])
    check("...and the far half carries the icons that were invisible before",
          len(hs[1]["icons"]) > 0 and len(hs[1]["gates"]) > 0)
    check("...and its own roster count, not the live half's",
          hs[1]["roster_total"] == 20)
    # Each half resolves its OWN stem's calibration -- anchoring one must not
    # move the other, which is the whole reason the store is keyed by stem.
    feworld.mapcal_add(ca, "map01_01", -271.5, -7.0, 16.6, 131.0, "one half")
    hs2 = feworld.devtool_state(ca)["halves"]
    check("the far half's calibration is the one stored for ITS stem",
          hs2[0]["proj"]["anchors"] == 0 and hs2[1]["proj"]["anchors"] == 1,
          repr((hs2[0]["proj"]["anchors"], hs2[1]["proj"]["anchors"])))
    feworld.mapcal_drop(ca, "map01_01")
    feworld._TLS.session = {"in_field": True, "field": 1, "cpos": (0.0, 20.0, 0.0)}
    feworld.devtool_snapshot()
    check("a war field has no halves rather than a broken one",
          feworld.devtool_state(ca)["halves"] == [])
    feworld._TLS.session = {"in_field": True, "field": 39,
                            "cpos": (186.5, 21.0, -132.5)}
    feworld.devtool_snapshot()

    # ---- where a door puts you down --------------------------------------
    print("\nthe door arrival override")

    # A door is keyed by the portal you walk THROUGH: `src` in 39, whose
    # destination is `dst` in 92. Walking through src puts you down in 92 at
    # src's OWN shipped spawn, beside dst.
    src = fegamedata.area_portals(39)[0]
    dst = fegamedata.portal(src["dest"])
    B = dst["area"]
    shipped = feworld.dat_door_for(ca, 39, src["gate"][0], src["gate"][1], 21.0)
    check("with no override a door hands out SE's own arrival -- src's spawn",
          shipped and abs(shipped[1][0] - src["spawn"][0]) < 1e-6
          and abs(shipped[1][2] - src["spawn"][1]) < 1e-6, repr(shipped))

    # a spot in B with ground, clear of dst's doorway: the kind a person picks
    def _ground_spot(area, cx, cz, avoid, clear):
        for r_ in range(6, 60, 2):
            for dx_, dz_ in ((r_, 0), (-r_, 0), (0, r_), (0, -r_),
                             (r_, r_), (-r_, -r_), (r_, -r_), (-r_, r_)):
                x_, z_ = cx + dx_, cz + dz_
                if (fegamedata.capital_ground(area, x_, z_) is not None
                        and ((x_ - avoid[0]) ** 2
                             + (z_ - avoid[1]) ** 2) ** 0.5 > clear + 1):
                    return (x_, z_)
        return None
    good = _ground_spot(B, src["spawn"][0], src["spawn"][1], dst["gate"],
                        dst["radius"])
    check("the fixture found a ground spot in the destination to use", good)

    # KEY: REFUSALS FIRST, both of them. On the far door is the arrive/step/
    # re-fire trap; off the collision is the hang this whole fix is about.
    out = _cgm("!arrive %d %.1f:%.1f"
               % (src["portal"], dst["gate"][0], dst["gate"][1]))
    check("!arrive REFUSES landing on the door you appear next to",
          "trap" in out and feworld.doorarr_get(src["portal"]) is None, out)
    out = _cgm("!arrive %d %.1f:%.1f" % (src["portal"], 5000.0, 5000.0))
    check("!arrive REFUSES a spot with no ground in the destination",
          "no ground" in out and feworld.doorarr_get(src["portal"]) is None,
          out)
    out = _cgm("!arrive %d %.1f:%.1f" % (src["portal"], good[0], good[1]))
    check("...and accepts a spot on the floor, clear of the door",
          feworld.doorarr_get(src["portal"]) == good, out)
    moved = feworld.dat_door_for(ca, 39, src["gate"][0], src["gate"][1], 21.0)
    check("the door now puts you down at the override",
          moved and abs(moved[1][0] - good[0]) < 1e-6
          and abs(moved[1][2] - good[1]) < 1e-6, repr(moved))
    check("...at the height the destination's collision gives THAT spot",
          abs(moved[1][1] - fegamedata.capital_ground(B, *good)) < 1e-6,
          "the map is a plan view; the drag never supplies y")

    # A stored arrival with no ground (hand-edited, or from before this fix)
    # must not be able to strand anyone: the door falls back to SE's.
    feworld.doorarr_set(ca, src["portal"], 5000.0, 5000.0)
    fb = feworld.dat_door_for(ca, 39, src["gate"][0], src["gate"][1], 21.0)
    check("a stored arrival with NO ground is ignored for SE's own",
          fb and abs(fb[1][0] - src["spawn"][0]) < 1e-6
          and abs(fb[1][2] - src["spawn"][1]) < 1e-6, repr(fb))
    feworld.doorarr_drop(ca, src["portal"])

    # KEY: `!arrive PORTAL here`: walk through, stand where you should have
    # appeared, press it. Refusals first.
    feworld._TLS.session = {"in_field": True, "field": 39,
                            "cpos": (100.0, 21.0, -100.0)}
    out = _cgm("!arrive %d here" % src["portal"])
    check("!arrive here REFUSES before you have walked through the door",
          "walk through it first" in out
          and feworld.doorarr_get(src["portal"]) is None, out)
    feworld._TLS.session = {"in_field": True, "field": B,
                            "cpos": (dst["gate"][0], 20.0, dst["gate"][1])}
    out = _cgm("!arrive %d here" % src["portal"])
    check("...and REFUSES standing on the doorway itself (the re-fire trap)",
          "trap" in out and feworld.doorarr_get(src["portal"]) is None, out)
    feworld._TLS.session = {"in_field": True, "field": B,
                            "cpos": (good[0], 20.0, good[1])}
    out = _cgm("!arrive %d here" % src["portal"])
    got_h = feworld.doorarr_get(src["portal"])
    check("...and puts the arrival exactly where you stand",
          got_h and abs(got_h[0] - good[0]) < 0.06
          and abs(got_h[1] - good[1]) < 0.06, "%r / %s" % (got_h, out))
    feworld._TLS.session = {"in_field": True, "field": 39,
                            "cpos": (186.5, 21.0, -132.5)}
    out = _cgm("!arrive drop %d" % src["portal"])
    check("dropping the override restores the shipped arrival",
          feworld.doorarr_get(src["portal"]) is None
          and abs(feworld.dat_door_for(ca, 39, src["gate"][0], src["gate"][1],
                                       21.0)[1][0] - src["spawn"][0]) < 1e-6)

    # WARNING: THE STORE MIGRATION. Version 1 keyed an arrival by the portal you came
    # out NEXT TO. Every pair is reciprocal, so a v1 row keyed D is a v2 row
    # keyed portal(D).dest at the same spot -- and a spot with no ground is
    # dropped, because keeping it would strand whoever used that door.
    import json as _js
    v1 = {str(dst["portal"]): {"x": good[0], "z": good[1]},       # has ground
          "2": {"x": -6.1, "z": -61.7}}      # portal 2: area 21, no ground there
    with open(ca.door_arrive_file, "w", encoding="utf-8") as fh:
        _js.dump(v1, fh)
    buf_m = _io.StringIO()
    with _ctx.redirect_stdout(buf_m):
        feworld.doorarr_load(ca)
    check("a v1 row is re-keyed to the portal you walk THROUGH, same spot",
          feworld.doorarr_get(src["portal"]) == good
          and feworld.doorarr_get(dst["portal"]) is None,
          buf_m.getvalue())
    check("...a v1 row with no ground under it is DROPPED, and says so",
          feworld.doorarr_get(fegamedata.portal(2)["dest"]) is None
          and "no ground" in buf_m.getvalue())
    check("...the v1 file is kept beside the new one",
          os.path.exists(ca.door_arrive_file + ".v1"))
    with open(ca.door_arrive_file, encoding="utf-8") as fh:
        check("...and the new file says which version it is",
              _js.load(fh).get("_version") == 2)
    feworld.doorarr_load(ca)
    check("...and loading it again changes nothing",
          feworld.doorarr_get(src["portal"]) == good)
    feworld.doorarr_drop(ca)

    # ---- dragging an NPC --------------------------------------------------
    print("\ndragging a placed NPC")
    feworld.spawn_add(ca, 39, (186.5, 21.0, -132.5), "")
    _cgm("!place 100.0:-50.0 Warrior_Weapon_Shop")
    rows = feworld.town_get(ca, 39, "npcs")
    check("a role is placed to drag", len(rows) == 1 and rows[0]["x"] == 100.0)
    out = _cgm("!moveto 39:7 120.0:-60.0")
    check("!moveto REFUSES a row that is not there",
          "no row 7" in out and feworld.town_get(ca, 39, "npcs")[0]["x"] == 100.0,
          out)
    out = _cgm("!moveto 39:0 120.0:-60.0 90")
    r0 = feworld.town_get(ca, 39, "npcs")[0]
    check("!moveto moves the row the panel dragged, by INDEX",
          r0["x"] == 120.0 and r0["z"] == -60.0 and r0["yaw"] == 90.0, repr(r0))
    check("...and keeps the height the row already had",
          abs(float(r0["y"]) - 21.0) < 1e-6,
          "a plan-view drag must never change y")

    # WARNING: THE THREE THAT WERE SHIPPED DEAD. !face, !move and !nudge all pass
    # round(...) floats into town_set, which type-tested with `"." in v` --
    # TypeError on a float, and the handler caught only ValueError, so the
    # exception escaped and the command did NOTHING. Silently. From the day
    # each was written. They are the rotate/nudge commands live testing asked
    # for, and no test called one, which is exactly how all three stayed dead
    # through a day of "tests pass". Every one of them now runs here.
    feworld._TLS.session = {"in_field": True, "field": 39,
                            "cpos": (150.0, 22.0, -70.0)}
    out = _cgm("!face 270")
    check("!face actually turns the NPC",
          feworld.town_get(ca, 39, "npcs")[0]["yaw"] == 270.0, out)
    out = _cgm("!nudge 2.5:-1.5")
    r1 = feworld.town_get(ca, 39, "npcs")[0]
    check("!nudge actually moves the NPC",
          abs(r1["x"] - 122.5) < 1e-6 and abs(r1["z"] + 61.5) < 1e-6, repr(r1))
    out = _cgm("!move")
    r2 = feworld.town_get(ca, 39, "npcs")[0]
    check("!move actually puts the NPC where you are standing",
          abs(r2["x"] - 150.0) < 1e-6 and abs(r2["z"] + 70.0) < 1e-6
          and abs(float(r2["y"]) - 22.0) < 1e-6, repr(r2))
    # ...and the string path town_set was written for still coerces.
    feworld.town_set(ca, 39, "npcs", 0, {"level": "7", "yaw": "45.5"})
    r3 = feworld.town_get(ca, 39, "npcs")[0]
    check("a STRING value is still coerced to a number (`!town set`)",
          r3["level"] == 7 and abs(r3["yaw"] - 45.5) < 1e-6, repr(r3))
    # WARNING: !npc BY NAME took the lowest type id -- always Beinwatt's (21xx) --
    # so every other capital's keeper ran Beinwatt's script. It must take
    # the role from the capital you are standing in.
    n0 = len(feworld.town_get(ca, 39, "npcs"))
    _cgm("!npc Item_Shop")
    rows39 = feworld.town_get(ca, 39, "npcs")
    own39 = next(r["script"] for r in fegamedata.capital_roster(39)
                 if r["name"] == "Item_Shop")
    check("!npc Item_Shop in Azurwood takes AZURWOOD's Item_Shop (script %d)"
          % own39, len(rows39) == n0 + 1 and rows39[-1]["script"] == own39
          and own39 // 100 in (23, 33), repr(rows39[-1:]))
    feworld.town_undo(ca, 39, "npcs")

    # ---- the offline editor: any capital, no session --------------------
    print("\nthe offline editor")
    # KEY: The editor needed a live session for two reasons: the map was built
    # around the player's area, and a click had no HEIGHT. The ground grid
    # removed the second; the state builder now opens any capital.
    feworld._TLS.session = {}
    feworld.devtool_snapshot()
    st_off = feworld.devtool_state(ca, area=62)
    check("a capital opens in the editor with NOBODY logged in",
          [h["area"] for h in st_off["halves"]] == [62, 94]
          and not st_off["in_field"], repr([h["area"] for h in st_off["halves"]]))
    check("...neither half is marked live", not any(h["live"]
                                                    for h in st_off["halves"]))
    check("...and the picker lists all five capitals",
          [c["area"] for c in st_off["capitals"]] == [21, 39, 57, 62, 78])
    check("a non-capital area gives no halves rather than a broken map",
          feworld.devtool_state(ca, area=5)["halves"] == [])

    E = lambda **op: feworld.devtool_edit(ca, op)
    feworld.town_undo(ca, 62, "npcs")
    spot62 = next((x_, z_) for x_ in range(-60, 60, 4) for z_ in range(-60, 60, 4)
                  if fegamedata.capital_ground(62, x_, z_) is not None)
    r0 = E(op="place", area=62, x=5000, z=5000, role="Warrior_Weapon_Shop")
    check("placing on a spot with NO floor is refused", not r0["ok"]
          and "no floor" in r0["msg"], r0["msg"])
    r0 = E(op="place", area=62, x=spot62[0], z=spot62[1], role="Not_A_Role")
    check("placing a role this capital does not have is refused",
          not r0["ok"] and "not one of" in r0["msg"], r0["msg"])
    r1 = E(op="place", area=62, x=spot62[0], z=spot62[1],
           role="Warrior_Weapon_Shop", yaw=90)
    row62 = feworld.town_get(ca, 62, "npcs")
    check("placing on the floor stores the NPC -- no session needed",
          r1["ok"] and len(row62) == 1
          and row62[0]["name"] == "Warrior_Weapon_Shop", r1["msg"])
    check("...at the height the capital's collision gives that spot",
          abs(float(row62[0]["y"])
              - fegamedata.capital_ground(62, *spot62)) < 0.01, repr(row62[0]))
    check("...facing as asked", float(row62[0]["yaw"]) == 90.0)
    r2 = E(op="turn", area=62, idx=0, yaw=-45)
    check("turning an NPC stores its new facing, normalised to 0..360",
          r2["ok"] and float(feworld.town_get(ca, 62, "npcs")[0]["yaw"])
          == 315.0, r2["msg"])
    spot62b = next((x_, z_) for x_ in range(-60, 60, 4)
                   for z_ in range(-60, 60, 4)
                   if fegamedata.capital_ground(62, x_, z_) is not None
                   and (x_, z_) != spot62)
    r3 = E(op="move", area=62, idx=0, x=spot62b[0], z=spot62b[1])
    rr = feworld.town_get(ca, 62, "npcs")[0]
    check("moving an NPC takes the NEW spot's floor height",
          r3["ok"] and rr["x"] == spot62b[0] and abs(float(rr["y"])
          - fegamedata.capital_ground(62, *spot62b)) < 0.01, repr(rr))
    r3b = E(op="move", area=62, idx=0, x=5000, z=5000)
    check("...and refuses a spot with no floor, leaving it where it was",
          not r3b["ok"] and feworld.town_get(ca, 62, "npcs")[0]["x"]
          == spot62b[0], r3b["msg"])
    rb = E(op="retype", area=62, idx=0, role="Bank_Keeper")
    rr2 = feworld.town_get(ca, 62, "npcs")[0]
    bk = next(r for r in fegamedata.capital_roster(62) if r["name"] == "Bank_Keeper")
    check("changing an NPC's type swaps model, name and script from the roster",
          rb["ok"] and rr2["name"] == "Bank_Keeper"
          and rr2["model"] == bk["modeltype"] and rr2["script"] == bk["script"],
          repr(rr2))
    check("...and keeps its spot and facing",
          rr2["x"] == spot62b[0] and float(rr2["yaw"]) == 315.0, repr(rr2))
    check("changing to a role the capital does not have is refused",
          not E(op="retype", area=62, idx=0, role="Castle_Guard")["ok"])
    r4 = E(op="remove", area=62, idx=0)
    check("removing an NPC deletes the row and says it is placeable again",
          r4["ok"] and not feworld.town_get(ca, 62, "npcs")
          and "placeable" in r4["msg"], r4["msg"])
    check("an edit naming a row that is not there is refused",
          not E(op="turn", area=62, idx=9, yaw=0)["ok"])
    r5 = E(op="arrive", portal=src["portal"], x=5000, z=5000)
    check("the editor applies the SAME arrival rules as !arrive",
          not r5["ok"] and "no ground" in r5["msg"], r5["msg"])
    check("an unknown edit is refused, not guessed at",
          not E(op="teleport", area=62)["ok"])

    # ---- the floor map the editor tints (and pre-checks clicks with) -----
    import base64
    fl = feworld.devtool_floor(ca, 91)
    bits = base64.b64decode(fl["bits"])
    ax_, cx_, az_, cz_ = fl["proj"]
    on = [(i % 256, i // 256) for i in range(256 * 256) if bits[i >> 3] & (1 << (i & 7))]
    check("the floor map marks the pixels with floor under them",
          len(on) == fl["floor_px"] and len(on) > 100, fl["floor_px"])
    bad = [(px, py) for px, py in on[::37]
           if fegamedata.capital_ground(91, (px + .5 - cx_) * ax_,
                                        (py + .5 - cz_) * az_) is None]
    check("...and every marked pixel has ground in the collision", not bad, bad[:3])
    off_ = [(i % 256, i // 256) for i in range(0, 256 * 256, 97)
            if not bits[i >> 3] & (1 << (i & 7))]
    wrong = [(px, py) for px, py in off_
             if fegamedata.capital_ground(91, (px + .5 - cx_) * ax_,
                                          (py + .5 - cz_) * az_) is not None]
    check("...and no unmarked pixel does", not wrong, wrong[:3])
    check("a non-capital area has no floor map", feworld.devtool_floor(ca, 5) is None)

    # ---- the townsfolk: SE's second NPC band, placed on the floor -------
    print("\nthe townsfolk")
    import math as _m
    tfs = fegamedata.townsfolk()
    check("189 townsfolk, and no camera markers or nation stones among them",
          len(tfs) == 189 and not [t for t in tfs if t["name"] == "Point"
                                   or t["name"].startswith("Earth")], len(tfs))
    pair_ = {91: 21, 92: 39, 93: 57, 94: 62, 95: 78}
    abroad = [(t["name"], t["script"], t["area"]) for t in tfs
              if pair_.get(t["area"], t["area"]) != t["capital"]]
    check("everyone lives in their OWN capital (two Selmas once both went to Beinwatt)",
          not abroad, abroad)
    check("...and nobody is listed twice", len({t["script"] for t in tfs}) == len(tfs))
    bad = [t["name"] for t in tfs if t["x"] is None
           or fegamedata.capital_ground(t["area"], t["x"], t["z"]) is None]
    check("every townsperson's spot has floor under it", not bad, bad[:5])
    near = [(t["name"], p["portal"]) for t in tfs for p in fegamedata.area_portals(t["area"])
            if _m.hypot(t["x"] - p["gate"][0], t["z"] - p["gate"][1])
            < float(p.get("radius") or 3.5) + 2]
    check("...and none stands in a doorway", not near, near[:5])
    li = fegamedata.townsfolk_by_script(306)
    check("Lionel stands at Azurwood's castle gate, in the Forest Ward",
          li and li["area"] == 39 and (li["x"], li["z"]) == (-186.8, 40.5)
          and li["district"] == "Forest Ward", repr(li))
    st_ = feworld.event_script_for(ca, {"name": "Lionel", "script": 306, "model": 246})
    check("...and turns you away with SE's line -- he is NOT a way in",
          st_[0] == ("text", li["line_en"]) and "Ordinary folk may not enter" in st_[0][1]
          and not any(x_[0] == "goto" for x_ in st_), repr(st_))
    er = feworld.event_script_for(ca, {"name": "Ernest", "script": 101})
    check("a villager says their own line", er[0][1].startswith("They say a witch"),
          repr(er))
    check("the 2006 districts name the halves",
          fegamedata.capital_district(91) == "Outer"
          and fegamedata.capital_district(92) == "Wall Ward"
          and fegamedata.capital_district(95) == "Slums")
    # the free weapon (Kakutas / Aiai / Roland)
    real_ = (feworld.item_rows, feworld.self_class_id)
    try:
        feworld.self_class_id = lambda a: 0
        feworld.item_rows = lambda a: []
        k0 = feworld.event_script_for(ca, {"name": "Kakutas", "script": 134})
        check("Kakutas gives a warrior with no weapon the warrior's starting weapon (301)",
              k0[0] == ("give", 301) and k0[1] == ("text", feworld.FREE_WEAPON_GIVE),
              repr(k0))
        feworld.self_class_id = lambda a: 2
        check("...Aiai gives a sorcerer a wand (531)",
              feworld.event_script_for(ca, {"name": "Aiai", "script": 333})[0] == ("give", 531))
        feworld.item_rows = lambda a: [(1, 483, 1, 1)]
        k1 = feworld.event_script_for(ca, {"name": "Roland", "script": 934})
        check("...anyone who already has a weapon gets advice, not another",
              k1[0][0] == "text" and not any(x_[0] == "give" for x_ in k1), repr(k1))
        feworld.item_rows = lambda a: [(i, 1666, 1, 1) for i in range(96)]
        check("...a full pack is told to sort it out first",
              feworld.event_script_for(ca, {"name": "Kakutas", "script": 134})[0]
              == ("text", feworld.FREE_WEAPON_FULL))
    finally:
        feworld.item_rows, feworld.self_class_id = real_
    out_, stored_, pushed_ = [], [], []
    real2 = (feworld.send, feworld._store_char_field, feworld.bag_layout_push,
             feworld.item_rows)
    try:
        feworld.send = lambda *a, **k: out_.append(a[2])
        feworld._store_char_field = lambda a, k, v: stored_.append((k, v))
        feworld.bag_layout_push = lambda *a: pushed_.append((a[6], a[7]))
        feworld.item_rows = lambda a: [(5, 1666, 1, 1)]
        feworld._SESSION["event"] = {"npc": 600, "script": 134, "pc": 0, "name": "Kakutas",
                                     "steps": [("give", 301), ("text", "x"),
                                               ("window", feworld.EV_WIN_END, 0)]}
        feworld.event_step(None, None, 0, False, ca)
        check("the give step stores the weapon, pushes the bag, and goes on to the line",
              stored_ and stored_[0][0] == "items"
              and [r_[1] for r_ in stored_[0][1]] == [1666, 301]
              and pushed_ and feworld._SESSION["event"]["pc"] == 2 and len(out_) == 1,
              repr((stored_, pushed_, len(out_))))
        del out_[:]
        feworld._SESSION["player_hp"] = 3
        feworld._SESSION["event"] = {"npc": 601, "script": 3105, "pc": 0, "name": "Inn_Master",
                                     "steps": feworld.event_script_for(
                                         ca, {"name": "Inn_Master", "script": 3105})}
        feworld.event_step(None, None, 0, False, ca)
        check("the inn puts you back to full HP, pushed, then welcomes you",
              feworld._SESSION["player_hp"] == feworld.player_hp_max(ca) and len(out_) == 2
              and feworld._SESSION["event"]["steps"][0] == ("heal",), repr(len(out_)))
    finally:
        feworld.send, feworld._store_char_field, feworld.bag_layout_push, \
            feworld.item_rows = real2
        feworld._SESSION.pop("event", None)
        feworld._SESSION.pop("player_hp", None)
    # ---- the balloon: never wraps, 4 lines tall, sizes itself short --------
    # (all three measured live 09-11 with test NPCs) -> <br/> breaks, at most
    # 4 lines, every line padded with trailing spaces
    W_ = feworld.talk_wrap
    cols_ = feworld._talk_cols
    bad_ = []
    for t_ in tfs:
        for ln_ in (t_["line_en"], t_["line_ja"]):
            if not ln_:
                continue
            out_ = W_(ln_, 48, 4)
            parts_ = out_.split("<br/>")
            if len(parts_) > 4 or max(cols_(p_.rstrip()) for p_ in parts_) > 140                     or not all(p_.endswith("   ") for p_ in parts_)                     or "".join(p_.strip() for p_ in parts_).replace(" ", "").replace("　", "")                     != ln_.replace(" ", "").replace("　", ""):
                bad_.append(t_["name"])
            if len(out_.encode("cp932", "replace")) > feworld.EV_TEXT_MAX:
                bad_.append(t_["name"] + " (too long)")
    check("every townsperson's line fits the balloon: <= 4 lines, padded, "
          "nothing lost, inside the client's text buffer", not bad_, bad_[:5])
    check("...a line with its own <br/> keeps its breaks (padded); 0 turns it off",
          [x.rstrip() for x in W_("a b<br/>c", 1).split("<br/>")] == ["a b", "c"]
          and W_("x " * 80, 0) == "x " * 80)
    check("...and a short line is padded too (Evelyn's 94-column line lost "
          "'ds.' past the border)", W_("short", 48).startswith("short   "))
    out_w = []
    real_w = feworld.send
    try:
        feworld.send = lambda *a, **k: out_w.append(a[2])
        feworld._SESSION["event"] = {"npc": 602, "script": 110, "pc": 0, "name": "Dean",
                                     "steps": feworld.event_script_for(
                                         ca, {"name": "Dean", "script": 110})}
        feworld.event_step(None, None, 0, False, ca)
    finally:
        feworld.send = real_w
        feworld._SESSION.pop("event", None)
    check("talking to Dean SENDS his line broken with <br/> (he ran off the "
          "balloon's edge live, 09-11)",
          out_w and b"<br/>" in out_w[0], repr(out_w[:1])[:120])
    check("every Manager gives the King's message and the nation's news",
          all(feworld.event_script_for(ca, {"name": n_, "script": s_})[0][1]
              .startswith("A message from the King.")
              for n_, s_ in (("Manager_Ancel", 3101), ("Manager_Anack", 3102),
                             ("Manager_Ankey", 3103))))
    check("nobody talks about five classes or a war office in the castle any more",
          "fencer" not in feworld.ROLE_LINES[(3, 4)].lower()
          and "castle" not in feworld.ROLE_LINES[(2, 1)].lower())
    check("Aiando's slot says SE's line (atwiki 243)",
          feworld.ROLE_LINES[(2, 12)] == fegamedata.roster_line(12)[0])
    # the editor: place a half's townsfolk in one go
    E2 = lambda **op: feworld.devtool_edit(ca, op)
    n39 = len(feworld.town_get(ca, 39, "npcs"))
    r_ = E2(op="townsfolk", area=39)
    rows39 = feworld.town_get(ca, 39, "npcs")
    lion = [r for r in rows39 if r.get("script") == 306]
    check("placing the Forest Ward's townsfolk puts all 18 down, Lionel at the gate",
          r_["ok"] and len(r_["placed"]) == 18 and len(rows39) == n39 + 18
          and lion and (lion[0]["x"], lion[0]["z"]) == (-186.8, 40.5), r_["msg"])
    check("...each at the floor's own height",
          all(abs(float(r["y"]) - fegamedata.capital_ground(39, r["x"], r["z"])) < 0.01
              for r in rows39[n39:]))
    r2_ = E2(op="townsfolk", area=39)
    check("...and placing again adds nobody",
          not r2_["placed"] and "18 already here" in r2_["msg"], r2_["msg"])
    ai = fegamedata.townsfolk_by_script(333)
    n92 = len(feworld.town_get(ca, 92, "npcs"))
    E2(op="place", area=92, x=ai["x"], z=ai["z"], role="Jade")
    r3_ = E2(op="townsfolk", area=92)
    check("a spot someone has taken since is SKIPPED and named, not stacked",
          any(x_.startswith("Aiai") for x_ in r3_["skipped"])
          and not any(r.get("script") == 333 for r in feworld.town_get(ca, 92, "npcs")),
          r3_["msg"])
    st39 = feworld.devtool_state(ca, area=39)
    h39 = [h for h in st39["halves"] if h["area"] == 39][0]
    check("the editor names the half's district and lists its townsfolk, placed",
          h39["district"] == "Forest Ward" and len(h39["townsfolk"]) == 18
          and all(t["placed"] for t in h39["townsfolk"]))
    check("...shows what each one says",
          any(n["says"].startswith("Beyond this point") for n in h39["npcs"]))
    check("...and offers townsfolk as types to place or change to",
          "Lionel" in h39["roles"] and "Aiai" in h39["roles"]
          and "Kakutas" not in h39["roles"])
    spot91 = next((x_, z_) for x_ in range(-20, 20, 2) for z_ in range(-170, -60, 2)
                  if fegamedata.capital_ground(91, x_, z_) is not None)
    rp = E2(op="place", area=91, x=spot91[0], z=spot91[1], role="Porter_Bai")
    check("one townsperson can be placed by hand from the editor", rp["ok"], rp["msg"])
    # tidy: take back what this section added
    for a_, n_ in ((39, n39), (92, n92), (91, None)):
        rows_ = feworld.town_get(ca, a_, "npcs")
        for i_ in range(len(rows_) - 1, (n_ if n_ is not None else 0) - 1, -1):
            if n_ is not None or rows_[i_].get("name") == "Porter_Bai":
                feworld.town_del(ca, a_, "npcs", i_)

    # ---- live: the change reaches a player standing there ---------------
    print("\nlive NPC edits")
    sent_del, sent_add = [], []
    real_kill, real_npc = feworld.mob_kill_push, feworld.npc_send
    feworld.mob_kill_push = lambda *a, **k: sent_del.append(a[5])
    feworld.npc_send = lambda *a, **k: sent_add.append((a[5], a[9], a[11]))
    try:
        feworld._TLS.session = {"in_field": True, "field": 62,
                                "cpos": (0.0, 8.0, 0.0), "npc_here_n": 0}
        E(op="place", area=62, x=spot62[0], z=spot62[1],
          role="Warrior_Weapon_Shop")
        E(op="place", area=62, x=spot62b[0], z=spot62b[1],
          role="Heavy_Armor_Shop")
        check("an editor change in another thread sends nothing by itself",
          not sent_del and not sent_add)
        feworld.gm_pump(None, None, 0, False, ca)   # the player's thread wakes
        base_ = int(getattr(ca, "monster_base", None) or 400) + 100
        check("the player's thread then SERVES the area's NPCs, live",
              [a_[0] for a_ in sent_add] == [base_, base_ + 1], repr(sent_add))
        check("...at the rows' current positions",
              sent_add and sent_add[0][1] == spot62[0]
              and sent_add[0][2] == spot62[1], repr(sent_add))
        sent_del.clear(); sent_add.clear()
        feworld.gm_pump(None, None, 0, False, ca)
        check("...once: a second wake with no change sends nothing",
              not sent_del and not sent_add)
        E(op="turn", area=62, idx=1, yaw=180)
        feworld.gm_pump(None, None, 0, False, ca)
        check("a change REMOVES every NPC it served, by id...",
              sent_del == [base_, base_ + 1], repr(sent_del))
        check("...and serves the current rows again under the same ids",
              [a_[0] for a_ in sent_add] == [base_, base_ + 1], repr(sent_add))
        sent_del.clear(); sent_add.clear()
        E(op="remove", area=62, idx=0)
        feworld.gm_pump(None, None, 0, False, ca)
        check("after a removal the remaining NPC is served as row 0 -- no id "
              "left pointing at the wrong row",
              sent_del == [base_, base_ + 1]
              and [a_[0] for a_ in sent_add] == [base_], repr((sent_del, sent_add)))
        sent_del.clear(); sent_add.clear()
        E(op="place", area=39, x=src["gate"][0], z=src["gate"][1] - 20,
          role="Warrior_Weapon_Shop")
        feworld.gm_pump(None, None, 0, False, ca)
        check("a change in an area the player is NOT in pushes nothing",
              not sent_del and not sent_add)
    finally:
        feworld.mob_kill_push, feworld.npc_send = real_kill, real_npc
        while feworld.town_get(ca, 62, "npcs"):
            feworld.town_del(ca, 62, "npcs", 0)
        while feworld.town_get(ca, 39, "npcs"):
            feworld.town_del(ca, 39, "npcs", 0)
        feworld._TLS.session = {"in_field": True, "field": 39,
                                "cpos": (186.5, 21.0, -132.5)}

    print()
    if fails:
        print("FAILURES:")
        for f in fails:
            print("  " + f)
        return 1
    print("[fe_world_test] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
