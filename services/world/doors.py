"""Doors: where a door puts you down (the arrivals store) and the shipped portal table."""
import os
import threading
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
import festate  # noqa: E402  -- the world state in PostgreSQL
from . import spawns

# ---------------------------------------------------------------------------
# WHERE A DOOR PUTS YOU DOWN, per destination portal.
#
# The shipped table carries an arrival point for every portal -- SE's own --
# and it is right far more often than anything we would invent, so it stays the
# default. But it is two floats with no height, it was authored for SE's
# collision and not ours, and one bad landing is not a cosmetic problem: an
# arrival inside the door's own match radius is a TRAP (arrive, step, re-fire,
# arrive), which is the loop the door latch now catches.
#
# So: an override, keyed by the portal you walk THROUGH -- that is what an
# arrival belongs to (it is that portal's own shipped `spawn` that the door
# uses; see dat_door_for). A door pair is two portals, so the two directions
# are two keys and are edited independently.
#
# WARNING: STORE VERSION 2 (2026-09-11). Version 1 keyed an arrival by the portal you
# came out NEXT TO, because dat_door_for read the table backwards. Every pair
# is reciprocal, so a v1 row keyed D is exactly a v2 row keyed portal(D).dest
# with the same position -- same area, same spot. doorarr_load migrates, drops
# any row whose spot has no ground in the destination's collision (landing
# there hangs the client), and keeps the v1 store (a file beside it, or the
# door_arrive.v1 row).
_DOORARR = {}
DOORARR_VERSION = 2
_DOORARR_LOCK = threading.Lock()


def doorarr_path(args):
    """Where the door arrivals live: the `door_arrive` row of fe_world_state
    unless args.door_arrive_file names a file (festate.location)."""
    return festate.location("door_arrive", getattr(args, "door_arrive_file", None))


def doorarr_load(args):
    """The store, migrating a version-1 file in place (see DOORARR_VERSION)."""
    global _DOORARR
    out, raw, migrated = {}, {}, False
    path = doorarr_path(args)
    raw = festate.read(path) or {}
    raw_v1 = dict(raw) if isinstance(raw, dict) else {}
    version = raw.pop("_version", 1) if isinstance(raw, dict) else 1
    for k, v in (raw or {}).items():
        try:
            key, x, z = int(k), float(v["x"]), float(v["z"])
        except (TypeError, ValueError, KeyError):
            continue
        if version < 2:
            # v1 key = the portal you came out next to; the arrival belongs to
            # the portal whose destination that is -- its partner.
            src = fegamedata.portal(key)
            if src is None or fegamedata.portal(src["dest"]) is None:
                print("[feworld] door arrivals: v1 row for portal %d dropped "
                      "-- not in the shipped table" % key, flush=True)
                continue
            new_key, dest_area = src["dest"], src["area"]
            migrated = True
        else:
            new_key = key
            p = fegamedata.portal(key)
            d = fegamedata.portal(p["dest"]) if p else None
            dest_area = d["area"] if d else None
        if dest_area is not None and fegamedata.minimap_name(dest_area) \
                and fegamedata.capital_ground(dest_area, x, z) is None:
            print("[feworld] door arrivals: DROPPED the arrival for portal %d "
                  "at (%.1f, %.1f) -- area %d's collision has no ground there, "
                  "and landing on nothing hangs the client at y = 10000"
                  % (new_key, x, z, dest_area), flush=True)
            migrated = True
            continue
        out[new_key] = {"x": x, "z": z}
    with _DOORARR_LOCK:
        _DOORARR = out
    if migrated and festate.persists(path):
        # a file keeps its version-1 copy beside it; in the database the
        # version-1 document is kept in its own row, door_arrive.v1
        kept = "not kept"
        try:
            if version < 2 and festate.in_database(path):
                if festate.write(festate.location("door_arrive.v1", None), raw_v1,
                                 sort_keys=True):
                    kept = festate.location("door_arrive.v1", None)
            elif version < 2 and os.path.exists(path):
                os.replace(path, path + ".v1")
                kept = path + ".v1"
            doorarr_save(args)
            print("[feworld] door arrivals: migrated to version %d (%d kept); "
                  "the previous store is %s"
                  % (DOORARR_VERSION, len(out), kept), flush=True)
        except OSError as e:
            print("[feworld] door arrivals: migration not written: %s" % e,
                  flush=True)
    return out


def doorarr_save(args):
    p = doorarr_path(args)
    # os.devnull means "do not persist". Writing a temp file beside it and
    # renaming onto `nul` fails on Windows and strands nul.tmp.<pid> in the
    # working directory -- 33 of them had piled up from test runs.
    if not festate.persists(p):
        return
    with _DOORARR_LOCK:
        snap = {str(k): dict(v) for k, v in _DOORARR.items()}
    snap["_version"] = DOORARR_VERSION
    festate.write(p, snap, sort_keys=True)


def doorarr_get(portal):
    with _DOORARR_LOCK:
        r = _DOORARR.get(int(portal))
    return (r["x"], r["z"]) if r else None


def doorarr_set(args, portal, x, z):
    with _DOORARR_LOCK:
        _DOORARR[int(portal)] = {"x": float(x), "z": float(z)}
    doorarr_save(args)


def arrive_set(args, portal, x, z):
    """Validate and store where walking through `portal` puts you. -> (ok, msg)

    Shared by `!arrive` and the editor so there is exactly one set of rules:
    the spot must have FLOOR in the destination (a landing on nothing hangs
    the client at y = 10000) and must not be inside the door you appear
    beside (arrive, step, re-fire).
    """
    pr = fegamedata.portal(int(portal))
    dd = fegamedata.portal(pr["dest"]) if pr else None
    if pr is None or dd is None:
        return False, "portal %s is not a door in the shipped table" % portal
    gd = ((dd["gate"][0] - x) ** 2 + (dd["gate"][1] - z) ** 2) ** 0.5
    if gd <= dd["radius"]:
        return False, ("(%.1f, %.1f) is %.1f units from portal %d's gate on "
                       "the far side, inside its radius of %.1f -- that is "
                       "the arrive/step/re-fire trap. Drop it further out."
                       % (x, z, gd, dd["portal"], dd["radius"]))
    if fegamedata.minimap_name(dd["area"]) \
            and fegamedata.capital_ground(dd["area"], x, z) is None:
        return False, ("(%.1f, %.1f) has no ground in area %d's collision -- "
                       "a player put down there hangs at y = 10000. Pick a "
                       "spot on the floor." % (x, z, dd["area"]))
    doorarr_set(args, int(portal), x, z)
    g = fegamedata.capital_ground(dd["area"], x, z)
    return True, ("walking through portal %d (area %d) now puts you down in "
                  "area %d at (%.1f, %.1f)%s; SE ships (%.1f, %.1f)"
                  % (int(portal), pr["area"], dd["area"], x, z,
                     "" if g is None else ", ground %.1f" % g,
                     pr["spawn"][0], pr["spawn"][1]))


def doorarr_drop(args, portal=None):
    with _DOORARR_LOCK:
        if portal is None:
            gone = len(_DOORARR)
            _DOORARR.clear()
        else:
            gone = 1 if _DOORARR.pop(int(portal), None) else 0
    if gone:
        doorarr_save(args)
    return gone


# ---------------------------------------------------------------------------
# THE SHIPPED DOOR TABLE -- dat.pak's fet_area_portal.
#
# 64 portals, and they cover EXACTLY the ten capital areas: 21/39/57/62/78 and
# their 91..95 twins. Nothing else in the game has a row, which is consistent
# with what the client does elsewhere -- a war field has no doors.
#
# KEY: 2026-09-09, STATIC: THE FRAME IS SETTLED, and it was settled by reading
# the CLIENT'S OWN PARSER for this table (0x0519D85A) instead of waiting for a
# door touch. The record is
#
#     [u32 portal][u32 area][f32[2] GATE x,z][f32 RADIUS]
#                           [f32[2] SPAWN x,z][f32[2] FACING x,z][u32 DEST]
#
# WARNING: RETRACTS the 2026-09-06 reading in this file, which took the array COUNT
# at +0x08 for a type tag and then read three floats from +0x0C as (x, y, z).
# Those three are (gate x, gate z, RADIUS). THE TABLE HAS NO THIRD COORDINATE
# ANYWHERE: every position is a two-float (x, z) pair and the height comes off
# the heightmap -- which is what --door authoring already assumed, so the
# hand-walked rows and the shipped rows are in the SAME frame and can be
# compared directly.
#
# What stands behind that, since "I read the parser" is not by itself a check:
# all 64 records consume exactly their own 52 bytes; all 64 destinations are
# reciprocal; the ten areas are the capital halves and their per-area counts
# mirror (21/91 have 7 each, 39/92 four, 57/93 seven, 62/94 twelve, 78/95
# two); and every radius is 3.0 or 3.5 -- a door-sized volume, which the third
# float could not have been if it were a height.
#
# So a door now answers with SE's own destination AND SE's own arrival spot,
# instead of dropping the player at the group's default spawn.
def door_table_log(args, gid):
    """Print the shipped portals for the entered area."""
    try:
        rows = fegamedata.area_portals(int(gid))
    except (TypeError, ValueError):
        return
    if not rows:
        return
    print("[feworld]    dat.pak ships %d DOOR(s) for area %s (--doors %s):"
          % (len(rows), gid, getattr(args, "doors", "off")), flush=True)
    for p in rows:
        d = fegamedata.portal(p["dest"])
        print("[feworld]       portal %-4d gate (%7.1f, %7.1f) r=%.1f -> "
              "portal %d in area %s, arrive (%7.1f, %7.1f) facing (%g, %g)"
              % (p["portal"], p["gate"][0], p["gate"][1], p["radius"],
                 p["dest"], d["area"] if d else "?",
                 d["spawn"][0] if d else 0.0, d["spawn"][1] if d else 0.0,
                 d["face"][0] if d else 0.0, d["face"][1] if d else 0.0),
              flush=True)


def dat_door_for(args, gid, x, z, y=None):
    """The shipped destination for the door the client just touched.

    Returns `(area, (x, y, z), portal, (face_x, face_z))` -- the destination
    AREA, the position to arrive at (the spawn of the portal you walked
    THROUGH, see below), the id of the portal you come out NEXT TO (what the
    panel pre-selects), and the facing to arrive with -- or None when no
    shipped door is near enough.

    Matching is against the door's OWN radius plus --door-radius as slack,
    not a blanket radius: the client reports the door's computed centre, and
    the capitals have doors four units apart (area 21's portals 1/2/3 sit at
    x = -9, -5, -1 on the same z), so a generous radius would pick the wrong
    one of a row of three.

    The table carries NO height -- every position in it is a two-float (x, z)
    pair and FE takes the height off the heightmap -- so the arrival Y has to
    come from somewhere. See --door-height.

    WARNING: RETRACTED 2026-09-09: this docstring used to say "the client ground-snaps
    (0x5000450) on the way in", which would have made the height harmless.
    0x05000450 is not a ground snap -- it calls 0x04FF7EF0, the CAPITAL
    PREDICATE, and is the field-load path. Whether the client snaps a served
    position to the terrain at all is UNVERIFIED, and until it is, a wrong Y
    has to be assumed to matter.
    """
    if getattr(args, "doors", "off") != "dat":
        return None
    try:
        rows = fegamedata.area_portals(int(gid))
    except (TypeError, ValueError):
        return None
    if not rows:
        return None
    slack = float(getattr(args, "door_radius", 15.0))
    best = None
    for p in rows:
        d = ((p["gate"][0] - x) ** 2 + (p["gate"][1] - z) ** 2) ** 0.5
        if d <= p["radius"] + slack and (best is None or d < best[0]):
            best = (d, p)
    if best is None:
        print("[feworld]    --doors dat: no shipped door within %.1f + r of "
              "(%.1f, %.1f) in area %s; the nearest is %.1f away"
              % (slack, x, z, gid,
                 min((((p["gate"][0] - x) ** 2 + (p["gate"][1] - z) ** 2) ** 0.5)
                     for p in rows)), flush=True)
        return None
    d, p = best
    dest = fegamedata.portal(p["dest"])
    if dest is None:
        print("[feworld]    --doors dat: portal %d names destination %d, "
              "which is not in the table -- ignoring it"
              % (p["portal"], p["dest"]), flush=True)
        return None
    # WARNING: THE HEIGHT USED TO COME FROM --spawn-pos, i.e. 0.0 in prod. The
    # table gives a door's (x, z) and nothing else -- FE takes height off the
    # heightmap, which we cannot read -- so the arrival y has to come from
    # somewhere, and the origin is the one answer guaranteed to be wrong.
    #
    # The CLIENT just told us: 0x20AC carries the y of the doorway the player
    # is standing in. Both portals of a pair are two halves of one town, so
    # that height is metres-accurate for the far side and is a MEASUREMENT
    # rather than a constant. --door-height spawn restores the old behaviour
    # and --door-height N pins a literal.
    # WARNING: RETRACTED 2026-09-10, AND IT IS MY OWN REASONING THAT WAS WRONG.
    # `client` took the y the player was standing at in the doorway, on the
    # argument that "both portals of a pair are two halves of one town, so
    # that height is within metres of the far side". The two halves are
    # DIFFERENT MAPS -- 21 is map00_00 and 91 is map00_01 -- and their ground
    # is at different heights. Serving the source's height put the player in
    # the air on the far side; they fell, the falling y was then reported at
    # the door they were falling past, and the next door reply carried
    # y = 10041, then 10064. That is the loop seen live: arrive, fall,
    # re-touch, arrive higher.
    #
    # The height has to come from the DESTINATION, and in the order that a
    # measurement beats a guess:
    #   1. the destination area's own spawn row -- ground somebody stood on
    #   2. its battle map's height (fegamedata.area_ground_height)
    #   3. the client's reported y, and only if it is plausible
    #   4. --spawn-pos
    # WARNING: THE ARRIVAL IS THE SPAWN OF THE PORTAL YOU WALK *THROUGH*.
    # Until 2026-09-11 this read dest["spawn"] -- the spawn of the portal you
    # come out NEXT TO -- and that is backwards. Held against the game's own
    # collision (DATA/capital/*_hit.oct, via tools/fedatagen/feoct.py), the
    # two readings are not close: reading the SOURCE portal's spawn lands on
    # ground in the destination map for 64 of the 64 doors between capital
    # halves; reading the destination's lands for 4, and those four are
    # coincidences in an overlap. The facings agree: portal 12 (area 91) faces
    # (0, 1), north, into area 21's northern map; portal 2 faces (0, -1), south,
    # down 91's road.
    #
    # And it is not cosmetic. The client stores y = 10000.0 on every entry and
    # asks its terrain for the ground under (x, z) (0x05000450 from mode 0xb
    # substate 0); with no ground there it SKIPS the height write (0x04ffa10b)
    # and the player hangs at 10000 over nothing -- frozen, until the client
    # bails out to a field. That was every capital door, both directions, in
    # every capital: the 2026-09-10 "door loop", "it puts me slightly behind",
    # and "it keeps me where I am" were all this.
    mode_ = getattr(args, "door_height", "dest")
    dest_area = dest["area"]
    ov = doorarr_get(p["portal"])
    if ov is not None and fegamedata.minimap_name(dest_area) \
            and fegamedata.capital_ground(dest_area, ov[0], ov[1]) is None:
        # A hand-placed arrival with nothing under it recreates exactly the
        # hang above. SE's own arrival is known to have ground; prefer it.
        print("[feworld]    --doors dat: IGNORING the arrival override for "
              "portal %d at (%.1f, %.1f): area %d's collision has no ground "
              "there, and landing on nothing hangs the client at y = 10000. "
              "Using SE's (%.1f, %.1f). `!arrive drop %d` clears it."
              % (p["portal"], ov[0], ov[1], dest_area, p["spawn"][0],
                 p["spawn"][1], p["portal"]), flush=True)
        ov = None
    ax_, az_ = ov if ov is not None else (p["spawn"][0], p["spawn"][1])
    face = p["face"]
    ay = None
    # The capital's own collision is a measurement of exactly this point; it
    # outranks every fallback below. (The client re-derives the height itself
    # on entry, so this matters for the logs and the stash, not for landing.)
    if mode_ == "dest":
        ay = fegamedata.capital_ground(dest_area, ax_, az_)
    if ay is None and mode_ not in ("client", "spawn"):
        try:
            ay = float(mode_)                       # a literal pins it
        except ValueError:
            ay = None
    if ay is None and mode_ != "client":
        if spawns.spawn_rows_for(dest_area):
            ay = spawns.spawn_for(args, dest_area)[1]
        else:
            g = fegamedata.area_ground_height(dest_area)
            ay = float(g) if g is not None else None
    if ay is None and y is not None and abs(float(y)) < 500.0:
        # KEY: A PLAUSIBILITY GATE, not a preference. Every shipped map height is
        # between 8 and 65; a y in the thousands is a player in freefall, and
        # propagating it is what turned one bad landing into a trap.
        ay = float(y)
    if ay is None:
        ay = spawns.spawn_for(args, dest_area)[1]
    pos = (ax_, ay, az_)
    print("[feworld]    --doors dat: portal %d (gate %.1f, %.1f, r=%.1f) hit "
          "at %.1f units -> AREA %d beside portal %d, arriving (%.1f, %.1f) "
          "facing (%g, %g)"
          % (p["portal"], p["gate"][0], p["gate"][1], p["radius"], d,
             dest["area"], dest["portal"], ax_, az_, face[0], face[1]),
          flush=True)
    g_ = fegamedata.capital_ground(dest_area, ax_, az_)
    print("[feworld]    --doors dat: arriving at height %.2f (--door-height %s)"
          "%s" % (ay, mode_,
                  "" if not fegamedata.minimap_name(dest_area)
                  else "; area %d's collision puts ground there at %s"
                  % (dest_area, "%.1f" % g_ if g_ is not None
                     else "NOTHING -- the client will hang")), flush=True)
    if ov is not None:
        print("[feworld]    --doors dat: arrival (%.1f, %.1f) is an OVERRIDE "
              "for portal %d -- SE ships (%.1f, %.1f). `!arrive drop %d` "
              "restores it."
              % (ax_, az_, p["portal"], p["spawn"][0], p["spawn"][1],
                 p["portal"]), flush=True)
    return dest["area"], pos, dest["portal"], (face[0], face[1])
