"""The town store (festate "town"): placed NPCs, door links, pushing the town."""
import struct
import threading
import festate  # noqa: E402  -- the world state in PostgreSQL
from . import buildings, chat, combat, sess, wire

# ---------------------------------------------------------------------------
# THE TOWN -- the `town` store (fe_world_state, formerly data/fe_town.json).
# What SE's server knew and the client does not ship: where the town's NPCs
# stand, which door is which, which buildings are placed. Authored from the minimap by walking it: `!npc`,
# `!build` and `!door` PERSIST here, and every entry of that group serves it.
#
#   {"39": {"npcs":      [{"model":233,"kind":1,"level":1,"x":..,"y":..,"z":..,"name":"Warrior_Weapon_Shop","script":2102}],
#           "buildings": [{"type":10,"model":10,"gx":201,"gz":75}],
#           "doors":     [{"x":186.5,"z":-132.5,"room":10}]}}
# ---------------------------------------------------------------------------
_TOWN_LOCK = threading.Lock()


# Areas whose town NPCs changed since a client was last shown them. Written
# by any thread (the editor runs on the panel's HTTP thread), consumed by the
# player's own thread in town_live_refresh -- the only thread that may talk
# to that client.
_TOWN_DIRTY = set()
_TOWN_DIRTY_LOCK = threading.Lock()


def town_mark_dirty(gid):
    with _TOWN_DIRTY_LOCK:
        _TOWN_DIRTY.add(str(gid))


def town_path(args):
    """Where the town lives: the `town` row of fe_world_state unless
    --town-file names a file (festate.location). "" turns the town off."""
    return festate.location("town", getattr(args, "town_file", None))


def town_load(args):
    p = town_path(args)
    if not p:
        return {}
    blob = festate.read(p)
    return blob if isinstance(blob, dict) else {}


def town_get(args, gid, kind):
    return list((town_load(args).get(str(gid)) or {}).get(kind) or [])


def _town_change(args, change):
    """Apply `change(blob)` to the town and write it back, in one step:
    `change` returns (changed, result). In the database this is one
    transaction under the store's advisory lock, so an edit from the panel
    and one from a player's `!npc` cannot lose each other. Returns `result`,
    or None when the town could not be read or written (festate logs why)."""
    with _TOWN_LOCK:
        return festate.update(town_path(args), change, sort_keys=True)


def town_add(args, gid, kind, rec):
    """Append one record for group `gid` and write the town."""
    p = town_path(args)
    if not p:
        return False

    def change(blob):
        blob.setdefault(str(gid), {}).setdefault(kind, []).append(rec)
        return True, True

    if not _town_change(args, change):
        return False
    print("[feworld]    town file: %s += %s %s" % (p, kind, rec), flush=True)
    if kind == "npcs":
        town_mark_dirty(gid)
    return True


def town_undo(args, gid, kind=None):
    """Drop the LAST row added for group `gid` (any kind, or one kind).
    Returns the row removed, or None. Exists because the file belongs to the
    container's user and a misplaced `!npc` (the admin's character moved
    while they typed) could not be fixed from a shell."""
    p = town_path(args)
    if not p:
        return None

    def change(blob):
        g = blob.get(str(gid)) or {}
        kinds = [kind] if kind else ["npcs", "buildings", "doors"]
        best = None
        for k in kinds:
            if g.get(k):
                best = k if best is None else best
        # no timestamps in the rows; "last" = the last row of the first
        # non-empty kind asked for, npcs first
        if best is None:
            return False, None
        return True, (best, g[best].pop())

    got = _town_change(args, change)
    if got is None:
        return None
    best, row = got
    print("[feworld]    town file: %s -= %s %s" % (p, best, row), flush=True)
    if best == "npcs":
        town_mark_dirty(gid)
    return row


def town_del(args, gid, kind, idx):
    """Delete row `idx` of `kind` for group `gid`. Returns the row, or None.

    `town_undo` only ever drops the LAST row, which is the right tool the
    moment after a mistake and the wrong one an hour later -- by then the
    misplaced keeper is three rows back and undoing to it throws away two good
    ones. This deletes exactly the row asked for.

    KEY: Nothing else has to happen for the role to come back: the panel's
    missing list is a live diff of the shipped roster against what is placed,
    so a deleted keeper reappears as placeable on the next poll.
    """
    p = town_path(args)
    if not p:
        return None

    def change(blob):
        rows = (blob.get(str(gid)) or {}).get(kind) or []
        if not (0 <= int(idx) < len(rows)):
            return False, None
        return True, rows.pop(int(idx))

    row = _town_change(args, change)
    if row is None:
        return None
    print("[feworld]    town file: %s -= %s[%d] %s" % (p, kind, int(idx), row),
          flush=True)
    if kind == "npcs":
        town_mark_dirty(gid)
    return row


def town_set(args, gid, kind, idx, changes):
    """Edit row `idx` of `kind` for group `gid`: numeric strings become
    numbers. Returns the row, or None."""
    p = town_path(args)
    if not p:
        return None

    def change(blob):
        rows = (blob.get(str(gid)) or {}).get(kind) or []
        if not 0 <= idx < len(rows):
            return False, None
        for k, v in changes.items():
            # WARNING: NUMBERS PASS THROUGH. The coercion below is for `!town set`,
            # where every value arrives as text off a command line. A caller
            # that already has a float -- !move, !face, !nudge, !moveto, all of
            # which pass round(...) results -- used to reach `"." in v` with a
            # float, which raises TypeError, and TypeError is not ValueError:
            # it escaped this function and killed the whole command. Every one
            # of those four did nothing at all, silently, from the day it was
            # written until 2026-09-10.
            if isinstance(v, str):
                try:
                    v = float(v) if "." in v else int(v, 0)
                except ValueError:
                    pass
            rows[idx][k] = v
        return True, rows[idx]

    row = _town_change(args, change)
    if row is None:
        return None
    if kind == "npcs":
        town_mark_dirty(gid)
    return row


def town_door_for(args, gid, x, z):
    """The room a door at (x, z) in `gid` leads to: --door entries first, then
    the town file. None when unmapped."""
    r = args.door_radius
    for g, dx, dz, room in getattr(args, "door_rows", None) or []:
        if g == gid and abs(dx - x) <= r and abs(dz - z) <= r:
            return room
    for d in town_get(args, gid, "doors"):
        try:
            if abs(float(d["x"]) - x) <= r and abs(float(d["z"]) - z) <= r:
                return int(d["room"])
        except (KeyError, TypeError, ValueError):
            continue
    return None


#: How close an arrival must be to a door to be INSIDE its trigger. Every
#: shipped door radius is 3.0 or 3.5 (fet_area_portal), and the client reports
#: a touch at the door's own centre, so this is the door's volume itself.
DOOR_TRAP_RADIUS = 3.5


def door_latch_ok(args, gid, x, z):
    """False only while a player who was put down INSIDE the door they are now
    touching has not yet stepped out of it. True otherwise, clearing the latch.

    WARNING: NARROWED 2026-09-11. This used to hold for the door's radius PLUS
    --door-radius (15) around the arrival -- written while dat_door_for read
    the portal table backwards and put people on top of doors. With the
    arrivals right, SE's land 5 to 8 units from the door you came through and
    OUTSIDE its trigger, so the old latch swallowed every ordinary return trip:
    live, a player walked back into the door they had just come out of and
    the touch was ignored. Worse, the refusal (0x1167) does not clear the
    client's outstanding door request -- its log shows `> MSG_GOTO_SEARCH_NG`
    with no `erase >>> 0x20ac` -- so the player sat on "waiting" for good.

    So: the latch only means anything when the arrival is inside the touched
    door's own trigger (DOOR_TRAP_RADIUS of the touch point), which is the one
    case where "arrive, step, re-fire" is real. `!arrive` refuses to author that
    case and no shipped arrival is one, so in practice this now never holds.
    """
    latch = sess._SESSION.get("door_latch")
    if not latch:
        return True
    la, lx, lz = latch
    if int(gid) != int(la):
        sess._SESSION.pop("door_latch", None)     # different area, it cannot apply
        return True
    if ((lx - x) ** 2 + (lz - z) ** 2) ** 0.5 > DOOR_TRAP_RADIUS:
        # we did NOT put them on this door: a deliberate touch, answer it
        sess._SESSION.pop("door_latch", None)
        return True
    pos = sess._SESSION.get("cpos")
    if pos is not None:
        moved = ((pos[0] - lx) ** 2 + (pos[2] - lz) ** 2) ** 0.5
        if moved > DOOR_TRAP_RADIUS:
            sess._SESSION.pop("door_latch", None)
            return True
    return False


def town_link_for(args, gid, x, z):
    """A LINKED door: `(destination area, (x, y, z))`, or None.

    A `!link` row carries where it goes AND where you land, which a plain
    `!door` row cannot: `!door` records a destination only, so the arrival
    falls back to the area's spawn -- fine for a room, useless for the far
    side of a doorway, where you want to come out beside the door you walked
    into rather than at the middle of the map.

    Rows are written in PAIRS, so walking back through returns you to the spot
    you left from. That is the shape fet_area_portal itself uses -- all 64 of
    its records are reciprocal -- so hand-mapped doors and shipped ones behave
    the same way.
    """
    r = args.door_radius
    for d in town_get(args, gid, "doors"):
        if "dest_area" not in d:
            continue
        try:
            if abs(float(d["x"]) - x) <= r and abs(float(d["z"]) - z) <= r:
                return (int(d["dest_area"]),
                        (float(d["dest_x"]), float(d["dest_y"]),
                         float(d["dest_z"])))
        except (KeyError, TypeError, ValueError):
            continue
    return None


def yaw_to_facing(yaw):
    """Degrees -> the unit facing vector mask2 bit 2 writes to [unit+0x1e4].
    0 = +Z (what every NPC got until 2026-09-05, all of them looking down
    the road instead of at their stalls), 90 = +X, 180 = -Z, 270 = -X.
    Never all-zero -- that is the shadow-with-no-body of 08-25."""
    import math
    a = math.radians(float(yaw or 0.0))
    fx, fz = math.sin(a), math.cos(a)
    return (round(fx, 4) or 0.0, 0.0, round(fz, 4) or (1.0 if not fx else 0.0))


def town_key():
    """Which town-file bucket this session is standing in: the room when
    indoors ("room:10"), else the field's group id ("39"). Rooms ride a war
    field's gid, so their keepers cannot be keyed by gid."""
    room = sess._SESSION.get("room", -1)
    if room is not None and room != -1:
        return "room:%d" % room
    return sess._SESSION.get("field")


def npc_display_name(name):
    """The label to show over an NPC: the internal role name made readable --
    `Warrior_Weapon_Shop` -> `Warrior Weapon Shop`, clamped to the client's
    own name field width ([unit+0x389..0x3ac] = 0x23 bytes incl. the NUL)."""
    s = str(name or "").replace("_", " ").strip()
    return s.encode("cp932", "replace")[:chat._CHAT_NAME_MAX].decode("cp932", "ignore")


def npc_identity_sub(name):
    """The type-8 identity sub-record with the NAME ONLY -- mask1 bit 0, then
    a submask of 0x1 and the name cstr into [unit+0x389]. Same reader as the
    avatar's (0x5079d60), which walks a submask and reads per set bit, so a
    name-only submask writes the name and touches nothing else (NOT the model:
    that comes from the type-8 head's modeltype u16, not from here). Returns
    (mask1, sub_bytes)."""
    nm = npc_display_name(name)
    if not nm:
        return 0, b""
    return 0x1, (struct.pack(">I", 0x1)
                 + nm.encode("cp932", "replace") + b"\x00")


def npc_send(conn, outbound, mode, be, args, obj, mtype, kind, level, x, y, z,
             why="", yaw=0.0, name="", type_id=None, register=True,
             send_it=True, log=True):
    """One 0x1006 type-8 record (see monster_push for the decode).
    register=False shows an already-registered (shared) monster without
    resetting it; send_it=False only registers it (a field's first build)."""
    fx, fy, fz = yaw_to_facing(yaw)
    # KEY: 2026-09-11: give the unit a NAME. Until now every NPC/monster went out
    # with mask1 = 0 -- no identity sub-record -- so the client's name field
    # [unit+0x389] stayed empty and nothing drew over their heads (or in the
    # target window). The avatar has always sent this sub-record; NPCs now send
    # its name-only form, which does not disturb the modeltype-driven model.
    mask1, sub = ((0, b"") if getattr(args, "npc_names", "on") != "on"
                  else npc_identity_sub(name))
    rec = (struct.pack(">HHB", mtype & 0xFFFF, kind & 0xFFFF, level & 0xFF)
           + struct.pack(">I", mask1) + sub + struct.pack(">I", 0x5)
           + struct.pack(">fff", x, y, z) + struct.pack(">fff", fx, fy, fz)
           + struct.pack(">I", 0))
    body = struct.pack(">HIB", 1, obj & 0xFFFFFFFF, 8) + rec
    if send_it:
        wire.send(conn, outbound, wire.inner_msg(0x1006, body, obj), mode, be,
             args.seq_mode == "echo", args.world_prefix)
        if log:
            print("[feworld]    -> 0x1006 type 8 %s id=%d modeltype=%d level=%d "
                  "at (%g, %g, %g) yaw=%g%s"
                  % ("NPC" if kind else "MONSTER", obj, mtype, level, x, y, z,
                     float(yaw or 0), (" " + why) if why else ""), flush=True)
    # Every MONSTER (kind 0) goes in the registry so 0xA011 has a target with
    # hit points. Town NPCs (kind 1) are skipped -- the client does not swing
    # at 0xBBB. See combat_hit.
    if register:
        combat.mob_register(args, obj, mtype, kind, level, x, y, z,
                     name=name or why.strip("() "), type_id=type_id)


def _town_npcs_send(conn, outbound, mode, be, args, gid, why="town file"):
    """0x1006 one record per town NPC row of `gid`, ids base + row index.
    Returns how many went out. Shared by entry and the live refresh, so the
    two can never serve an NPC differently."""
    base = int(getattr(args, "monster_base", None) or 400) + 100
    n = 0
    for r in town_get(args, gid, "npcs"):
        try:
            npc_send(conn, outbound, mode, be, args, base + n, int(r["model"]),
                     int(r.get("kind", 1)), int(r.get("level", 1)),
                     float(r["x"]), float(r["y"]), float(r["z"]),
                     why="(%s %s: %s)" % (why, gid, r.get("name", "")),
                     yaw=float(r.get("yaw", 0) or 0))
            n += 1
        except (KeyError, TypeError, ValueError) as e:
            print("[feworld]    WARNING: town npc row %r skipped: %r" % (r, e), flush=True)
    return n


def town_live_refresh(conn, outbound, mode, be, args):
    """Show a player standing in an area the NPC changes made to it, NOW.

    KEY: THE CLIENT ONLY READS TOWN NPCS ON ENTRY, so until 2026-09-11 every edit
    said "re-enter the area to see it". The server already removes and re-adds
    units live -- a monster's death is 0x1004 MSG_DEL and its respawn a fresh
    0x1006 under the SAME id (mob_kill_push / mob_respawn_tick), proved since
    the battle loop closed on 09-09 -- so a changed area gets exactly that:
    every town NPC it was served is deleted by id, and the current rows are
    served again. Twenty-odd keepers at most, so a whole-area refresh is
    simpler than diffing, and it cannot leave an id pointing at the wrong row
    after a removal renumbers them.

    Player thread only: this is the one thread that may talk to the client.
    """
    key = town_key()
    if key is None or not sess._SESSION.get("in_field"):
        return 0
    with _TOWN_DIRTY_LOCK:
        if str(key) not in _TOWN_DIRTY:
            return 0
        _TOWN_DIRTY.discard(str(key))
    base = int(getattr(args, "monster_base", None) or 400) + 100
    old = int(sess._SESSION.get("npc_here_n", 0) or 0)
    for i in range(old):
        combat.mob_kill_push(conn, outbound, mode, be, args, base + i)
    n = _town_npcs_send(conn, outbound, mode, be, args, key, why="live edit")
    sess._SESSION["npc_here_n"] = n
    print("[feworld]    town NPCs changed in %s while you stand in it: removed "
          "%d, served %d -- live, no re-entry needed" % (key, old, n),
          flush=True)
    return n


def town_push(conn, outbound, mode, be, args):
    """Serve the town file's NPCs and buildings for the entered group -- or,
    indoors, the ROOM's keepers (key "room:N"; no buildings in a room)."""
    gid = town_key()
    if gid is None:
        return
    # entry serves the rows as they are now; nothing is pending for this area
    with _TOWN_DIRTY_LOCK:
        _TOWN_DIRTY.discard(str(gid))
    n = _town_npcs_send(conn, outbound, mode, be, args, gid)
    sess._SESSION["npc_here_n"] = n
    if isinstance(gid, str) and gid.startswith("room:"):
        if n:
            print("[feworld]    town file: served %d keeper(s) in %s" % (n, gid),
                  flush=True)
        return
    bbase = int(getattr(args, "building_base", 3000)) + sess._SESSION.get("build_n", 0)
    m = 0
    for r in town_get(args, gid, "buildings"):
        try:
            buildings.building_send(conn, outbound, mode, be, args, bbase + m, int(r["type"]),
                          int(r["model"]), int(r["gx"]), int(r["gz"]), why="(town file)")
            m += 1
        except (KeyError, TypeError, ValueError) as e:
            print("[feworld]    WARNING: town building row %r skipped: %r" % (r, e), flush=True)
    sess._SESSION["build_n"] = sess._SESSION.get("build_n", 0) + m
    if n or m:
        print("[feworld]    town file: served %d NPC(s) and %d building(s) for "
              "group %d" % (n, m, gid), flush=True)
