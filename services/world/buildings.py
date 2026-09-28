"""Buildings and keeps: the type-1 record, construction timers, keep HP and hits."""
import struct
import sys
import time
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import combat, extrun, sess, territory, town, wire, zones

# ---------------------------------------------------------------------------
# 0x1006 type 1 -- A BUILDING. The message the capital's doors are waiting for.
#
# 2026-09-04, live: walking the capital (gid 39) turned up two
# doors the minimap draws that lead nowhere. The client sent NOTHING at them
# -- not on the wire, not in its own log -- while the arms shop's EXIT door,
# reached through the --field-rooms probe, fired a real transition (0x2017 -1,
# then 0x2000 -1). So the door machinery is real and what the capital lacks is
# the OBJECT each door belongs to. The building model table says what that is:
# `febuild.py` rows 9/10/11/13/14/15 are bar_a / arms_store_a / magic_store_a /
# bathroom_a / house00_a / castle01, and each names a room pak -- and those
# numbers are EXACTLY the room indices the 0x1000 i16 selects (SHIPPED_ROOMS).
# A door is a placed building whose model row names the room.
#
# THE RECORD, read call by call off the type-1 arm 0x0503b583..0x0503b616
# (2026-09-04; readers by their own stream advance -- 0x5045e30/0x5045ec0 +2,
# 0x5045e60/0x5045ef0 +4, 0x5045dc0/0x5045e90 +1):
#
#     [u16 -> +0x380]  building TYPE   -> data_FE_BUILDING_DATA.dat (30 rows)
#     [u16 -> +0x382]  building MODEL  -> data_FE_BUILDING_MODEL_DATA.dat (32)
#     [u32 -> +0x384]  unread by anything found; 0
#     [u8  -> +0x94 ]  the SIDE byte (the avatar's ARMY/nation slot). WARNING: The
#                      static README's table MISSED this read; sending its
#                      ten fields would have been one byte short.
#     [i32 -> +0x778]  \  the pair 0x5111010 ratios as a gauge -- HP-shaped.
#     [i32 -> +0x774]  /  Which is current and which is max is UNMEASURED.
#     [u16 -> +0x388]  grid X   world X = -(gx - 128) * 2.5   (0x050650e0)
#     [u16 -> +0x38a]  grid "Y" -- unread by the placer; 0
#     [u16 -> +0x38c]  grid Z   world Z = -(gz - 128) * 2.5
#     [u8  -> +0x38e]  unknown; 0
#     [u32 stack]      a timestamp: `now - it` -> +0x77c (construction age)
#
# NO MASK TAIL. Unlike types 0 and 8, every exit of this arm (0x0503b6f3,
# 0x0503b70d, 0x0503b712->0x0503b725, 0x0503b61d) goes to the epilogue or the
# next entity, never to the shared three-mask tail at 0x0503b111. Both head
# gates ([scene+0x4fd], the registry [0x05336f9c]) are set by the field entry
# we already drive (fe-static-2026-09-04). Y comes off the heightmap.
#
# THE ORACLE is the client's own line at 0x0503b6e5:
#     `Building %d ... TYPE=%d (%f,%f,%f)`
# A building that parsed prints its resolved world position there. 'Building
# %d already exists' (0x0503b6ff) means the object id collided.
#
# PARTIAL: BUILT 2026-09-04, NOT LIVE-TESTED. What a placed building DOES -- render,
# collide, open its room when walked into -- is exactly what the first live
# run is for. The door-entry protocol (what the client sends when you walk
# into one) is still unread statically; the wire will say.
# ---------------------------------------------------------------------------
BUILDING_TYPES = {
    0: "ArrowTower", 1: "DragonAltar", 2: "Observatory", 3: "AlchemyLabo",
    4: "GateOfHades", 5: "WarCraft", 6: "Obelisk", 7: "Crystal",
    8: "DefenseTower", 9: "Bar", 10: "BlackSmith", 11: "ItemShop",
    12: "GuildTower", 13: "PublicBath", 14: "House", 15: "Castle", 16: "Keep",
    17: "Keep", 18: "Keep", 19: "ArrowTower", 20: "Castle", 21: "House",
    22: "House", 23: "House", 24: "House", 25: "House", 26: "House",
    27: "House", 28: "House", 29: "Obelisk",
}
BUILDING_MODELS = {
    0: "b_build05_a", 1: "b_build03_a", 2: "Observatory_a", 3: "b_build00_a",
    4: "gateofhades_a", 5: "giantsummons_a", 6: "crystal", 7: "bathroom_a",
    8: "obelisk_a", 9: "bar_a", 10: "arms_store_a", 11: "magic_store_a",
    12: "b_build06_b", 13: "bathroom_a", 14: "house00_a", 15: "castle01",
    16: "b_build06_a", 17: "b_build05_b", 18: "b_build06_b", 19: "house00_b",
    20: "house00_c", 21: "house01_a", 22: "house01_b", 23: "house01_c",
    24: "house02_a", 25: "house02_b", 26: "house02_c", 27: "CanonTower_a",
    28: "CanonTower_b", 29: "CanonTower_c", 30: "castle00_a", 31: "obelisk_b",
}
#: Model rows whose pak is a ROOM -- the doors. Keys match SHIPPED_ROOMS.
BUILDING_ROOM_MODELS = {9: "room_bar", 10: "room_armsshop", 11: "room_magicshop",
                        13: "room_bath", 14: "room_house00", 15: "room_castle"}
GRID_ORIGIN, GRID_CELL = 128.0, 2.5


def world_to_grid(w):
    """grid = 128 + world / 2.5, clamped to the u16 and the 256-cell field.

    WARNING: THE SIGN WAS MEASURED LIVE 2026-09-05 AND THE STATIC NOTE WAS WRONG.
    fe-static-2026-09-04 read the placer 0x050650e0 as `world = -(g-128)*2.5`.
    The first `!build` at the player's reported (183.7, ?, -133.6) used that
    inverse, sent grid (55, 181), and the client's own oracle printed
    `building TYPE=10 added at (-182.5, 0.0, 132.5)` -- the MIRROR of where
    the player stood. So the client computes world = (g-128)*2.5, no
    negation, and this is its inverse. (Or the telemetry space is the
    negation of the placer's; the observable is the same and this is what
    puts a building where the player is standing.)
    """
    return max(0, min(255, int(round(GRID_ORIGIN + float(w) / GRID_CELL))))


def grid_to_world(g):
    return (float(g) - GRID_ORIGIN) * GRID_CELL


# ---------------------------------------------------------------------------
# KEY: THE CONSTRUCTION TIMER -- and the last field of the type-1 record is
# its input. Read 2026-09-12 off the dump; nothing here is a guess.
#
# The arm stores the wire u32 SUBTRACTED FROM NOW (0x0503b616..0x0503b639):
#
#     0503b616  call 0x5045e60          ; the u32 -> [esp+0x40]
#     0503b625  call 0x5070530          ; the building ctor
#     0503b62a  mov ecx, [0x52ffad0]
#     0503b630  call 0x4faf890          ; eax = now  ([that + 0x12c], ms)
#     0503b635  sub eax, [esp+0x40]     ; now - wire
#     0503b639  mov [esi+0x77c], eax
#
# and the per-building worker reads it back against the SHIPPED build time
# (0x05072770, called from the arm at 0x0503b68a):
#
#     05072776  mov ecx, [0x52ffad0]
#     0507277c  call 0x4faf890          ; eax = now
#     05072781  mov ecx, [esi+0x77c]
#     05072787  sub eax, ecx            ; elapsed
#     05072789  mov ecx, [esi+0x3e8]    ; this building's FE_BUILDING_DATA row
#     0507278f  cmp eax, [ecx+0xac]     ; the BUILD TIME in ms
#     05072795  jae done                ; finished -- no effect
#     0507279d  call 0x5072860
#     050727a2  cmp word [eax+0xf2], 7  ; the Crystal's group: never a site
#     050727aa  je  done
#     ...       build the construction effect (0x5013400) into [esi+0x3d8]
#
# So elapsed = wire + (time since the ADD): KEY: **THE WIRE FIELD IS THE
# BUILDING'S AGE IN MILLISECONDS AT THE MOMENT IT IS ADDED**, not an absolute
# timestamp -- which is the good news, because it is a DIFFERENCE and needs no
# shared clock base. (Our note called it "a timestamp: `now - it`", which is
# the same arithmetic read from the other end.)
#
# WARNING: WE SENT 0 EVERY TIME. 0 means "started just now", so EVERY building
# played its full construction effect at EVERY player who was sent it: the
# attacker's keep (type 16, 30 s) on every field entry, the town's shops (10 s)
# on every entry, and every tower already standing in a war replayed its 40 s
# site to each arriving player. The only record that should carry 0 is the one
# a player has this second paid for.
#
# +0xac for all 30 rows (dat.pak FE_BUILDING_DATA, streamed in the client's own
# read order -- feparse 0x051906f8 -- and the same pass reproduces +0xa4, +0xb0,
# +0xb4, +0xd0, +0xd4, +0xda, +0xe1 and +0xe2 at the values already recorded
# for them, which is what says the walk is aligned).
BUILD_TIME_MS = {
    0: 40000, 1: 60000, 2: 60000, 3: 60000, 4: 60000, 5: 60000, 6: 30000,
    7: 15000, 8: 30000, 9: 10000, 10: 10000, 11: 10000, 12: 10000, 13: 10000,
    14: 10000, 15: 0, 16: 30000, 17: 30000, 18: 30000, 19: 40000, 20: 0,
    21: 10000, 22: 10000, 23: 10000, 24: 10000, 25: 10000, 26: 10000,
    27: 10000, 28: 10000, 29: 30000,
}
#: FE_BUILDING_DATA +0xf2 -- a CANONICAL type (ArrowTower 19 -> 0, Obelisk 29
#: -> 6, Keep 17/18 -> 16, every House -> 14). Only one value is behaviour:
#: group 7 (Crystal) is the row 0x050727a2 exempts from the effect entirely.
BUILD_EFFECT_EXEMPT = 7
BUILD_CANON_TYPE = {
    0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9, 10: 10,
    11: 11, 12: 12, 13: 13, 14: 14, 15: 15, 16: 16, 17: 16, 18: 16, 19: 0,
    20: 15, 21: 14, 22: 14, 23: 14, 24: 14, 25: 14, 26: 14, 27: 14, 28: 14,
    29: 6,
}


def build_time_ms(btype):
    """FE_BUILDING_DATA +0xac for `btype` -- how long its construction site
    stands, in ms. 0 for a type with no row (and for the Castle, which ships
    0: a base is never under construction)."""
    return int(BUILD_TIME_MS.get(int(btype), 0))


def building_age(args, btype, age_ms=None):
    """The u32 the type-1 record ends with: this building's AGE IN MS.

    `age_ms=None` means FINISHED -- the caller is placing something that was
    already standing (the keeps, the town, --building, a crystal, a tower
    another player built long ago), and it gets an age past its build time so
    no construction site is drawn. A caller that has a real age passes it.

    --build-age zero restores the old always-0 wire value; `done` forces every
    building finished, a fresh build included.
    """
    mode = str(getattr(args, "build_age", "real") or "real")
    done = build_time_ms(btype) + 1000
    if mode == "zero":
        return 0
    if mode == "done" or age_ms is None:
        return done
    return max(0, min(int(age_ms), done))


def building_record(obj, btype, model, gx, gz, side=0, hp=(100, 100), gy=0,
                    f384=0, f38e=0, stamp=0):
    """One 0x1006 entity, type 1, in the arm's read order (see above).

    `stamp` is the building's AGE IN MS at this moment -- see the
    CONSTRUCTION TIMER block. Callers should get it from building_age()
    rather than pass a number; 0 means "started this instant"."""
    rec = struct.pack(">HHIB", btype & 0xFFFF, model & 0xFFFF,
                      f384 & 0xFFFFFFFF, side & 0xFF)
    rec += struct.pack(">ii", int(hp[0]), int(hp[1]))       # +0x778, +0x774
    rec += struct.pack(">HHHB", gx & 0xFFFF, gy & 0xFFFF, gz & 0xFFFF,
                       f38e & 0xFF)
    rec += struct.pack(">I", stamp & 0xFFFFFFFF)
    return struct.pack(">HIB", 1, obj & 0xFFFFFFFF, 1) + rec


def parse_buildings(specs):
    """`GID:TYPE:MODEL:GX:GZ` per --building -> [(gid, type, model, gx, gz)]."""
    out = []
    for spec in specs or []:
        f = spec.split(":")
        try:
            gid, btype, model, gx, gz = (int(v, 0) for v in f[:5])
        except (ValueError, TypeError):
            raise SystemExit("--building wants GID:TYPE:MODEL:GX:GZ, got %r"
                             % spec)
        if btype not in BUILDING_TYPES:
            raise SystemExit("--building %r: type %d is not a row of "
                             "data_FE_BUILDING_DATA.dat (0..29)" % (spec, btype))
        if model not in BUILDING_MODELS:
            raise SystemExit("--building %r: model %d is not a row of "
                             "data_FE_BUILDING_MODEL_DATA.dat (0..31)"
                             % (spec, model))
        if not (0 <= gx <= 255 and 0 <= gz <= 255):
            raise SystemExit("--building %r: grid coordinates are 0..255 "
                             "(world = -(g-128)*2.5)" % spec)
        out.append((gid, btype, model, gx, gz))
    return out


def _building_side(args):
    """The u8 at +0x94: the entered field's DEFENDER, i.e. the town's own
    nation, so a shop is 'ours'. 0 when the contest is unset."""
    grp = sess._SESSION.get("field")
    for island in range(1, int(getattr(args, "islands", 5)) + 1):
        if grp in zones.group_ids_for(island, args):
            c = territory.field_contest(island, args)
            return c[0] if c else 0
    return 0


def building_send(conn, outbound, mode, be, args, obj, btype, model, gx, gz,
                  why="", hp=None, age_ms=None):
    """`hp` = (cur, max) for this building (fewar's --build-hp for a war
    build); None = --building-hp.

    `age_ms` = how long this building has stood, in ms; None (the default)
    means FINISHED, which is right for everything that is being re-placed
    rather than raised. Only a build the player is paying for right now
    passes 0. See building_age."""
    if hp is None:
        hp = getattr(args, "building_hp", (100, 100))
    side = _building_side(args)
    age = building_age(args, btype, age_ms)
    body = building_record(obj, btype, model, gx, gz, side=side, hp=hp,
                           stamp=age)
    wire.send(conn, outbound, wire.inner_msg(0x1006, body, obj), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld]    -> 0x1006 type 1 BUILDING id=%d type=%d (%s) model=%d "
          "(%s%s) grid=(%d,%d) -> world (%g, ?, %g) side=%d hp=%s%s -- CHECK "
          "THE CLIENT LOG for 'Building %d ... TYPE=%d (x,y,z)'; 'already "
          "exists' means the id collided."
          % (obj, btype, BUILDING_TYPES.get(btype, "?"), model,
             BUILDING_MODELS.get(model, "?"),
             (" = door into " + BUILDING_ROOM_MODELS[model])
             if model in BUILDING_ROOM_MODELS else "",
             gx, gz, grid_to_world(gx), grid_to_world(gz), side, list(hp),
             (" " + why) if why else "", obj, btype), flush=True)
    if age < build_time_ms(btype) and BUILD_CANON_TYPE.get(
            int(btype)) != BUILD_EFFECT_EXEMPT:
        print("[feworld]       UNDER CONSTRUCTION: age %d ms of the %d ms this "
              "type ships (+0xac), so the client draws its construction site "
              "for another %d ms (0x05072770)"
              % (age, build_time_ms(btype), build_time_ms(btype) - age),
              flush=True)


# ---------------------------------------------------------------------------
# THE KEEPS -- what a war is fought over (2026-09-11).
#
# A retail war ended when one side's keep fell. We served no keep, so the
# campaign decided by sign-up counts. Now every war field gets TWO type-1
# buildings when a war is on: the DEFENDER's castle where dat.pak's
# fet_castle_info puts it (one grid position per Hmap map -- SE's own spot),
# and the ATTACKER's keep 24 cells from it toward the map's centre (retail's
# attackers BUILT theirs where they chose; this is the one CHOSEN number).
#
# HP rides the building record's +0x778/+0x774 pair, and damage rides the
# SAME channel the player's does -- 0x2024 maskA bit 0x4 with the BUILDING's
# id in the header: the setter's kind-5 arm (0x04ff3eb0, read off the dump
# 2026-09-11) does `si=[edi+0x778]; ax=[esp+0x38]; sub si,ax; movsx eax,ax;
# 0x5071210(this, eax)` -- a u16 on the wire, the NEW value, the difference
# drawn as the number. So +0x778 is CURRENT (it is what the arm overwrites)
# and +0x774 is the max. At 0 the keep is deleted with 0x1004 like a monster,
# and fecampaign ends the war for the side that still stands.
#
# WARNING: MODELS ARE CHOSEN: type 20 Castle / model 30 castle00_a for the castle,
# type 16 Keep / model 16 b_build06_a for the keep -- and the TYPE is not
# cosmetic: a building's +0xde is the summon its window offers, which is 954
# KNIGHT for Castle and Keep alike (SE warsystem02) and 955 WRAITH for the
# GateOfHades the default briefly pointed at.
#
# The castle model went 30 -> 4 (gateofhades_a) on 2026-09-11 because a field
# EXIT crashed for a missing `castle00_a_stand.mdl`, and came BACK to 30 on
# 2026-09-12 because that reading was retracted the same day: `_stand` lands in
# an embedded KcMODEL and 0x0507087e skips it when absent; the only unchecked
# model pointer is slot 0, the base mesh, and castle00_a ships one. The crash
# was the building.pak mount racing the placement, which the field_ready gate
# fixed. PARTIAL: Live bar: enter a war field, leave it, and enter a SECOND one
# in the same session with the castle on screen.
# ---------------------------------------------------------------------------
def building_hp_push(conn, outbound, mode, be, args, obj, hp):
    """0x2024 maskA bit 0x4 addressed to a BUILDING: the NEW hp (u16)."""
    wire.send(conn, outbound,
         wire.inner_msg(0x2024, struct.pack(">IIh", 0x4, 0, max(0, int(hp)) & 0x7FFF),
                   obj),
         mode, be, args.seq_mode == "echo", args.world_prefix)


def _pair(spec, default):
    try:
        a, _, b = str(spec or default).partition(":")
        return int(a, 0), int(b, 0)
    except ValueError:
        return default


def keep_grids(area):
    """{"def": (gx, gz), "atk": (gx, gz)} for `area`, or None when the
    shipped castle table has no row for its map.

    WARNING: 2026-09-11, LIVE: the first keeps stood 24 cells apart on one Z row --
    "right next to one another", as live testing put it, and a war was over in a few
    swings. A siege wants the two ends of the field. The defender keeps SE's
    own castle grid; the attacker's keep is REFLECTED through the map centre
    (128,128) -- the far end -- clamped to an interior band so it stays on the
    mesh, with a minimum 60-cell separation so a near-centre castle still gets
    a keep across the field, not on top of it. Still CHOSEN: the reflection is
    a guess at where the far end's ground is, the same class of guess as the
    24-cell offset it replaces, but it reads as a battlefield instead of a
    courtyard.
    """
    row = fegamedata.areas().get(int(area))
    if not row:
        return None
    c = fegamedata.castle(row.get("hmap"))
    if not c:
        return None
    gx, gz = int(c[0]), int(c[1])
    ax, az = 256 - gx, 256 - gz                 # reflect through the centre
    sep = 60
    if abs(ax - gx) < sep:
        ax = gx - sep if gx >= GRID_ORIGIN else gx + sep
    if abs(az - gz) < sep:
        az = gz - sep if gz >= GRID_ORIGIN else gz + sep
    clamp = lambda v: max(30, min(225, int(v)))
    out = {"def": (gx, gz), "atk": (clamp(ax), clamp(az))}
    # 2026-09-11: a war DECLARED by building a keep (fecampaign's 0xF501
    # vote) has its attacker keep where the player put it -- and so the
    # attackers' arrival point (derived_spawn) follows it too.
    mod = sys.modules.get("fecampaign")
    if mod is not None:
        try:
            g = mod.declared_grid(area)
        except Exception:                              # noqa: BLE001
            g = None
        if g:
            out["atk"] = (int(g[0]), int(g[1]))
    return out


def keep_push(conn, outbound, mode, be, args, area, keeps, nations,
              sides=None):
    """Serve the bases of `area`: keeps = {"def": [hp, max], "atk": ..},
    nations = {"def": holder, "atk": attacker}; `sides` limits it to those
    (the castle alone at peace -- fecampaign._keeps_only). Registers them on
    the session, with the nation each was served for, so a hit on their id is
    a hit on the keep. Returns {side: obj}."""
    grids = keep_grids(area)
    if not grids:
        print("[feworld]    keeps: area %s has no castle row in fet_castle_info "
              "-- none served" % area, flush=True)
        return {}
    types = _pair(getattr(args, "keep_types", None), (20, 16))
    models = _pair(getattr(args, "keep_models", None), (30, 16))
    base = int(getattr(args, "keep_base", 2900) or 2900)
    reg = sess._SESSION.setdefault("keeps", {})
    out = {}
    for i, side in enumerate(("def", "atk")):
        if sides is not None and side not in sides:
            continue
        obj = base + i
        hp = keeps.get(side) or [0, 0]
        gx, gz = grids[side]
        # WARNING: A BASE IS NOT UNDER CONSTRUCTION. The attacker's keep is type
        # 16, whose +0xac is 30000, and the record used to carry age 0 -- so
        # every field entry raised the enemy keep from a construction site in
        # front of the player. (The Castle's own +0xac is 0, so only the keep
        # ever showed it.) building_age(None) = finished.
        body = building_record(obj, types[i], models[i], gx, gz,
                               side=int(nations.get(side) or 0) & 0xFF,
                               hp=(int(hp[0]), int(hp[1])),
                               stamp=building_age(args, types[i]))
        wire.send(conn, outbound, wire.inner_msg(0x1006, body, obj), mode, be,
             args.seq_mode == "echo", args.world_prefix)
        reg[obj] = {"side": side, "area": int(area),
                    "nation": int(nations.get(side) or 0)}
        out[side] = obj
        print("[feworld] -> 0x30 inner 0x1006 type 1 KEEP (%s) id=%d type=%d "
              "(%s) model=%d (%s) grid=(%d,%d) -> world (%g, ?, %g) nation=%s "
              "hp=%d/%d -- CHECK THE CLIENT LOG for 'Building %d ... TYPE=%d'"
              % (side, obj, types[i], BUILDING_TYPES.get(types[i], "?"),
                 models[i], BUILDING_MODELS.get(models[i], "?"), gx, gz,
                 grid_to_world(gx), grid_to_world(gz), nations.get(side),
                 int(hp[0]), int(hp[1]), obj, types[i]), flush=True)
    return out


def keep_del_push(conn, outbound, mode, be, args, why="", sides=None):
    """0x1004 every keep this session was shown (or only those of `sides`);
    forget them."""
    reg = sess._SESSION.get("keeps") or {}
    drop = sorted(o for o, k in reg.items()
                  if sides is None or k["side"] in sides)
    for obj in drop:
        k = reg.pop(obj)
        combat.mob_kill_push(conn, outbound, mode, be, args, obj)
        print("[feworld] -> 0x30 inner 0x1004 MSG_DEL keep %d (%s, area %d)%s"
              % (obj, k["side"], k["area"], (" " + why) if why else ""),
              flush=True)
    if not reg:
        sess._SESSION.pop("keeps", None)
    return len(drop)


def _my_war_side(mod, args, area):
    """"atk"/"def" for this session in `area`'s war, or None (a spectator, or
    a nation that is neither). Read off the war's member row first, so a
    player who signed up keeps the side they signed up on."""
    try:
        me = mod._me()[0]
        m = (mod.state_of(area).get("members") or {}).get(me)
        if m and m.get("side") in ("atk", "def"):
            return m["side"]
        mine = int(sess._SESSION.get("nation") or 0)
        if not mine:
            return None
        if mine == int(mod.state_of(area).get("atk") or 0):
            return "atk"
        if mine == int(territory.territory_owner(area, args) or 0):
            return "def"
    except Exception:                                  # noqa: BLE001
        return None
    return None


def building_hit(conn, outbound, mode, be, args, target, why=""):
    """A swing landed on a PLAYER-BUILT building: damage it in the war's own
    row, show the new hp, and 0x1004 it at 0 -- the same channel and the same
    250 ms same-swing rule the keeps use (0xA011, 0x2010 and 0x2019 can all
    describe one hit). True when handled.

    KEY: 2026-09-12. Until today the only damageable buildings were the two
    keeps: an arrow tower or an obelisk was indestructible, because it lived
    in its builder's session and nothing looked for it. fecampaign now owns
    every war building, so anybody's swing finds one.

    A felled building costs its own side base HP (--base-dots: an obelisk 4 of
    480, an arrow tower 2, a war craft 4) and takes its territory with it,
    both through fewar.destroy_building's path -- reused here so a building
    that falls to a swing and one the admin `!destroy`s end the same way."""
    mod = sys.modules.get("fecampaign")
    if mod is None or getattr(args, "campaign", "off") != "on":
        return False
    area = sess._SESSION.get("field")
    if area is None or not hasattr(mod, "building_damage"):
        return False
    last = sess._SESSION.get("keep_hit_last")
    now = time.monotonic()
    if last and last[0] == int(target) and now - last[1] < 0.25:
        return True
    got = mod.building_damage(args, area, target, 0)     # is it one of ours?
    if got is None:
        return False
    sess._SESSION["keep_hit_last"] = (int(target), now)
    dmg = max(1, int(getattr(args, "hit_damage", 25)))
    # KEY: THE SWING HAS TO COME FROM YOUR OWN 勢力範囲 (the client's own
    # tutorial book, fet_bookset_info 59: 「建設のみならず敵の建物への攻撃も
    # 勢力範囲内のみとなっておる」). Our own side and cell say whether it does.
    here = sess._SESSION.get("cpos")
    grid = (world_to_grid(here[0]), world_to_grid(here[2])) if here else None
    got = mod.building_damage(args, area, target, dmg, from_grid=grid,
                              by_side=_my_war_side(mod, args, area))
    if got == "out of influence":
        print("[feworld]       building %d is OUTSIDE this side's own "
              "勢力範囲 -- the swing lands on nothing (the client's book 59 "
              "rule; --influence off removes the gate)" % target, flush=True)
        return True
    if got is None:
        return False
    new, row = got
    building_hp_push(conn, outbound, mode, be, args, target, new)
    print("[feworld]       -> 0x2024 bit 0x4 on BUILDING %d (type %d %s, %s): "
          "hp %d (-%d)%s"
          % (target, row.get("t", -1), BUILDING_TYPES.get(row.get("t"), "?"),
             row.get("side"), new, dmg, (" " + why) if why else ""), flush=True)
    if new <= 0:
        combat.mob_kill_push(conn, outbound, mode, be, args, target)
        mod.building_fell(args, area, int(target), conn=conn,
                          outbound=outbound, mode=mode, be=be)
        combat.battle_tally("destroyed", 1)      # the result window's "Destroyed"
        print("[feworld]       -> 0x1004 MSG_DEL: building %d has FALLEN -- "
              "its side's base pays the dots, an obelisk gives its territory "
              "back, and every other session drops it on their next pump"
              % target, flush=True)
    return True


def keep_hit(conn, outbound, mode, be, args, target, why=""):
    """A swing landed on `target`: if it is one of this session's keeps,
    damage it through fecampaign and show the new hp. True when handled.
    Two notifies can describe one swing (0xA011 and 0x2010), so a second hit
    on the same keep inside 250 ms is the same swing."""
    k = (sess._SESSION.get("keeps") or {}).get(int(target))
    if k is None:
        return building_hit(conn, outbound, mode, be, args, target, why)
    last = sess._SESSION.get("keep_hit_last")
    now = time.monotonic()
    if last and last[0] == int(target) and now - last[1] < 0.25:
        return True
    sess._SESSION["keep_hit_last"] = (int(target), now)
    mod = sys.modules.get("fecampaign")
    if mod is None or getattr(args, "campaign", "off") != "on":
        return False
    dmg = max(1, int(getattr(args, "hit_damage", 25)))
    new = mod.keep_damage(args, k["area"], k["side"], dmg)
    if new is None:
        print("[feworld]    hit on keep %d (%s) -- no war on in area %d, "
              "nothing takes damage" % (target, k["side"], k["area"]), flush=True)
        return True
    building_hp_push(conn, outbound, mode, be, args, target, new)
    print("[feworld]       -> 0x2024 bit 0x4 on KEEP %d (%s): hp %d (-%d)%s"
          % (target, k["side"], new, dmg, (" " + why) if why else ""), flush=True)
    if new <= 0:
        combat.mob_kill_push(conn, outbound, mode, be, args, target)
        sess._SESSION["keeps"].pop(int(target), None)
        print("[feworld]       -> 0x1004 MSG_DEL: the %s keep of area %d has "
              "FALLEN -- the campaign ends the war on its next tick"
              % (k["side"], k["area"]), flush=True)
    extrun.ext_post("campaign.keep", {"area": k["area"], "obj": int(target),
                               "side": k["side"], "hp": int(new)})
    return True


def building_push(conn, outbound, mode, be, args):
    """Serve every --building whose GID is the field just entered. Rides the
    add stream before 0x100E, like the avatar, NPCs and monsters."""
    rows = [b for b in (getattr(args, "buildings", None) or [])
            if b[0] == sess._SESSION.get("field")]
    if not rows:
        return
    base = int(getattr(args, "building_base", 3000))
    for k, (_gid, btype, model, gx, gz) in enumerate(rows):
        building_send(conn, outbound, mode, be, args, base + k, btype, model,
                      gx, gz, why="(--building)")
    sess._SESSION["build_n"] = len(rows)


def build_here(conn, outbound, mode, be, args, spec):
    """`!build TYPE[:MODEL]` -- place a building where the client last said
    the player is standing (its own 0x2023 position, x0.1), so a door can be
    dropped at a minimap door without measuring the map first. Types 9..15
    share their number with their room model, so MODEL defaults to TYPE."""
    f = spec.split(":")
    try:
        btype = int(f[0], 0)
        model = int(f[1], 0) if len(f) > 1 and f[1] else btype
    except ValueError:
        print("[feworld]    !build wants TYPE[:MODEL], got %r" % spec, flush=True)
        return
    if btype not in BUILDING_TYPES or model not in BUILDING_MODELS:
        print("[feworld]    !build %r: type 0..29, model 0..31" % spec, flush=True)
        return
    if not sess._SESSION.get("in_field"):
        print("[feworld]    !build: not in a field", flush=True)
        return
    pos = sess._SESSION.get("cpos")
    if not pos:
        print("[feworld]    !build: no 28-byte 0x2023 seen yet, so the "
              "player's position is unknown; move and retry", flush=True)
        return
    gx, gz = world_to_grid(pos[0]), world_to_grid(pos[2])
    n = sess._SESSION.get("build_n", 0)
    obj = int(getattr(args, "building_base", 3000)) + n
    sess._SESSION["build_n"] = n + 1
    building_send(conn, outbound, mode, be, args, obj, btype, model, gx, gz,
                  why="(!build at the player's reported (%g, %g, %g))"
                  % tuple(pos))
    town.town_add(args, sess._SESSION.get("field"), "buildings",
             {"type": btype, "model": model, "gx": gx, "gz": gz})
