"""Filling a field with monster groups: spawn points, scatter, population."""
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import monsters, sess, town, zones

def field_ground_height(area):
    """A standable height for `area`, or None. The same estimate the player's
    own derived spawn uses (fegamedata.area_ground_height = the median of the
    map's shipped sound emitters), with the same fallback for maps 7 and 10,
    which ship ZERO samples: the median of the other maps' medians."""
    try:
        h = fegamedata.area_ground_height(int(area))
    except (TypeError, ValueError):
        return None
    if h is not None:
        return float(h)
    meds = [v["median"] for v in fegamedata.map_heights().values()
            if v.get("median") is not None]
    if not meds:
        return None
    import statistics
    return float(statistics.median(meds))


def spawn_push(conn, outbound, mode, be, args):
    """The field's OWN monsters, from dat.pak's spawn tables (fegamedata).

    Every war field 1..90 has 7..11 shipped spawn points with a position and
    a spawner naming the monster types. One 0x1006 type-8 per point, kind 0
    (0xBBC, the target-window/level kind), the FIRST shipped modeltype the
    spawner lists. Capitals have no rows and get nothing. WARNING: What the client
    DOES with a monster past rendering and targeting is unbuilt: nothing
    fights back, and the spawn radius (the table's wander/respawn area) is
    not used -- the point is the position.
    """
    if getattr(args, "spawns", "dat") != "dat":
        return
    gid = sess._SESSION.get("field")
    if gid is None or gid in zones.CAPITAL_GROUP_IDS:
        return
    # KEY: SHARED (2026-09-13): the field's monsters are built ONCE, by the
    # first session in (register only, under the lock), and every session --
    # that one too -- is shown them as they are now. A second player walking
    # in meets the first player's monsters, dents and all.
    if monsters.mobs_shared(args) and sess._SESSION.get("mobs_field") == gid:
        fm = monsters.field_mobs(gid)
        sess._SESSION["mobs"] = fm["mobs"]
        with monsters._MOB_LOCK:
            first = not fm["built"]
            if first:
                _spawn_build(conn, outbound, mode, be, args, gid,
                             register_only=True)
                fm["built"] = True
        return monsters.mobs_replay_push(conn, outbound, mode, be, args, gid, first)
    return _spawn_build(conn, outbound, mode, be, args, gid)


def _spawn_build(conn, outbound, mode, be, args, gid, register_only=False):
    """spawn_push's body: register the field's monsters and, unless
    register_only, send each as it goes (the per-session path)."""
    # KEY: 2026-09-12: each point holds its generator's WHOLE population (about
    # nine, not one) -- see fegamedata.spawn_populations. `one` is the old path.
    if str(getattr(args, "spawn_population", "dat") or "dat") == "dat":
        return _spawn_push_population(conn, outbound, mode, be, args, gid,
                                      register_only=register_only)
    pts = fegamedata.spawn_points(gid)
    cap = int(getattr(args, "spawns_max", 12))
    base = int(getattr(args, "monster_base", 400)) + 1000
    # WARNING: THE SHIPPED SPAWN TABLE HAS NO WORLD Y. 714 of fet_npc_generator_pos'
    # 721 rows carry y = 0 exactly (the other 7 carry 32) -- SE's server
    # resolved the ground itself, and ours was serving the 0 verbatim. Every
    # map that ships height samples has its LOWEST anywhere at 8 and medians
    # running 8..62, so a monster at y=0 is 8..62 units UNDER the terrain.
    #
    # What it broke is SERVER-side: our registry held every monster tens of
    # units below the player, so the vertical reach test refused every attack
    # ("Duke_Orc is 23.0 u above/below the player ... it cannot reach them").
    # WARNING: RETRACTED the same evening: this comment first blamed the monsters'
    # "walking in place" on bit 0x800000 ("no ground beneath the unit"). That
    # bit cannot stop a monster -- 0x0504A4A0 returns [obj+0x2b4] only when
    # the kind is 3 (the player) and a hard 0 otherwise, which
    # monster_chase_tick's own docstring had already recorded. Raising the
    # height did not unstick them; the move DURATION did (see
    # monster_chase_tick).
    #
    # The height is the same estimate the player's derived spawn already uses.
    # --monster-height table restores the raw table value.
    gh = field_ground_height(gid)
    mode_h = str(getattr(args, "monster_height", "dat") or "dat")
    lifted = 0
    for k, (x, y, z, rad, mtype, name, gen, tid) in enumerate(pts[:cap]):
        guessed = False
        if mode_h != "table" and gh is not None and y <= 0.0:
            y, lifted, guessed = gh, lifted + 1, True
        # The npc TYPE id rides along so the monster gets ITS OWN hit points
        # and level out of fet_npc_type instead of --monster-level for all.
        st = fegamedata.npc_stats(tid)
        town.npc_send(conn, outbound, mode, be, args, base + k, mtype, 0,
                 st.get("level") or int(getattr(args, "monster_level", 1)),
                 x, y, z, name=name, type_id=tid,
                 send_it=not register_only,
                 why="(dat.pak spawner %d: %s L%s hp %s, r=%g)"
                     % (gen, name, st.get("level", "?"), st.get("hp", "?"), rad))
        if guessed:
            # our Y for this monster is a per-MAP median (the table ships
            # none), so the vertical reach test must not trust it
            (sess._SESSION.get("mobs", {}).get(base + k) or {})["y_guess"] = True
    print("[feworld]    dat.pak spawns for field %d: %d point(s) served%s%s"
          % (gid, min(len(pts), cap),
             " (of %d, --spawns-max)" % len(pts) if len(pts) > cap else "",
             ("; %d of them had the table's y=0 and were raised to the map's "
              "ground height %.1f -- at y=0 they are UNDER the terrain and "
              "the reach test reads them tens of units off" % (lifted, gh))
             if lifted else ""), flush=True)


# ---------------------------------------------------------------------------
# THE EVENT VM -- how an NPC talks, and how the SHOP and BANK windows open.
#
# Read 2026-09-05 off the interpreter at 0x5173a70, which the 0x1174 arm
# (0x0505601d) hands the stream to. ONE COMMAND PER MESSAGE:
#
#     0x1174  [u32 event][u16 opcode][args]     addressed (inner header u32)
#                                               to the NPC's unit id -- the
#                                               text handler looks that unit
#                                               up (0x504cb30(id, 3)) and
#                                               draws on it
#
#     op 1     [u32][u32]                -> 0x5174020  (object + 0x504d350)
#     op 2     [u16]                     -> 0x5174160
#     op 3     [u16 len][bytes]          -> 0x51742d0  DIALOGUE TEXT (<= 0x258)
#     op 6     [u16 len][bytes]          -> 0x5174c40  a second text box (<= 0x320)
#     op 8     [u16 type][u32]           -> 0x5174520  OPEN A WINDOW:
#                 (the window ctor's argument is the HEAD u32 of the command
#                 -- the NPC unit id, [esp+0x20] at 0x51746bb -- stored at
#                 [win+0x84]: the shop's update 0x50f0170 looks that unit up
#                 (0x504cb30) and CLOSES ITSELF when it is missing or more
#                 than [0x5289468] away. WARNING: LIVE 2026-09-05 x4 we put the
#                 SCRIPT id in the head, so the shop closed on its first
#                 frame; the op's own u32 never reached +0x84.)
#                 1..4  the SHOP (ctor 0x50efe30)
#                 WARNING: ALL FOUR ARE THE SAME WINDOW -- 2026-09-10. The arm
#                 compares ax against 1, 2, 4 and 3 in that order and every
#                 one of them jumps to the SAME block (0x0517461b..0x0517469e),
#                 which allocates 0x88 and calls the ctor with the HEAD u32
#                 (the NPC unit id, [esp+0x20]). `ax` is never read again.
#                 So the type does NOT choose buy vs sell vs item-trade;
#                 whatever distinguishes a weapon shop from a bank teller has
#                 to come from the item list WE serve once it is open, not
#                 from this number. --shop-types was written to be settled by
#                 "the first live window that names itself"; no live window
#                 can settle it, because they are one window.
#                 0x13  END the conversation: clears the talk-mode bit and
#                       sends 0x2099 (-> 0x1152)
#                 0x14  a 0xd8 window (ctor 0x50c6af0), unnamed
#                 0x18  the BANK ("Bank Stored", ctor 0x50e8390)
#     op 9     [u32]                     -> 0x5174720  (1.5 s fade, then 0x5175780)
#     0x107    [u32 len][bytes][u32 n] n x { [u32 x][u32 len][bytes] }
#                                        -> 0x5174910  a CHOICE MENU
#     0x109    [u32]                     -> 0x5174810  (2 s fade, then 0x5175780)
#     4, 5, 7 and anything else: no-op.
#
# THE ROUND TRIP. 0x2046 (the click) -> our 0x1071 [npc][u16][u16][D]. With
# D != 0 the client's starter 0x5173f70 sends 0x20A7 [npc][D] (registering
# 0x1172/0x1173) and a 0x20A8 ack; we answer 0x20A7 with 0x1172 (START OK,
# header-only -- it erases the pending entry that otherwise draws
# "waiting...", live #6) and then the FIRST command
# (every command's head u32 = the NPC UNIT id, see event_step);
# every command is acked by the window that consumed it with 0x20A8
# [u32][u16][u32] (0x5173fd0; live the text box acked (0, 0, 0) on click)
# and the ack cues the NEXT; when the script is out we send 0x1172 END. The
# shop/bank windows run their own protocol once open (0x2073 -> 0x1102 pages,
# then 0x204A the item list..) and their CLOSE is 0x2085 [mode] + 0x20A8
# (npc, 8, 0) -- measured in the shop's close 0x50f00c0 -- which cues the next
# command -- the END window (type 0x13), which closes the conversation
# through 0x2099 -> 0x1152, then our 0x1175 EVENT END (arm 0x5056039)
# releases event mode. 0x1172 (arm 0x5055f70) LOCKS: START OK only.
#
# --shop-types therefore only picks a number the client ignores among 1..4;
# what it IS good for is choosing between a shop (1..4), the bank (0x18) and
# the unnamed 0xd8 dialog (0x14), which are genuinely different windows.
# Scripts are keyed by the dat.pak type name stored
# on the town-file row, so the file that places a keeper also says what he
# does. NOT LIVE-TESTED.
# ---------------------------------------------------------------------------
def spawn_scatter(gid, gen, pidx, n, rad, spread=2.0):
    """[(dx, dz)] for the `n` monsters of one spawn point -- SMALL PACKS AT
    RANDOM SPOTS, not one disc (2026-09-12, live-test report: "you can tell where the
    spawn points are because it's just clusters around them. It feels a bit
    inorganic").

    Packs of 1..3 (weighted toward 2-3) at a random spot inside spread x the
    table's radius (area-uniform, sqrt), members 1.5..5 u around their pack.
    SEEDED by (field, generator, point index), so every player's copy of the
    field is the same layout and a respawn goes back to the same home. The
    client ground-snaps a placed monster (mask2 position -> 0x5000450), so the
    wider spread does not need a height per spot.
    """
    import math as _m
    import random as _r
    import zlib as _z
    rng = _r.Random(_z.crc32(("%d:%d:%d" % (int(gid), int(gen), int(pidx)))
                             .encode("ascii")))
    reach = max(4.0, float(rad) * float(spread))
    out = []
    while len(out) < n:
        size = min(n - len(out), rng.choice((1, 2, 2, 3, 3)))
        a = rng.uniform(0.0, 2.0 * _m.pi)
        r = reach * _m.sqrt(rng.random())
        cx, cz = r * _m.cos(a), r * _m.sin(a)
        for _ in range(size):
            b = rng.uniform(0.0, 2.0 * _m.pi)
            s = rng.uniform(1.5, 5.0)
            out.append((cx + s * _m.cos(b), cz + s * _m.sin(b)))
    return out


def _spawn_push_population(conn, outbound, mode, be, args, gid,
                           register_only=False):
    """Every spawn point on field `gid` with its generator's population.

    Live-test report 2026-09-12: "should there be more monsters? As is, they're pretty
    sparse" -- one per point where the data says about nine. Types come by the
    generator's own weights, capped per type (fegamedata.allocate_population);
    each monster stands on a golden-angle spiral INSIDE its point's own radius
    (the table's wander area, unused until now), so the layout is the same for
    every player and a respawn returns to that spot (mob_register's home).
    --spawns-max still caps POINTS; --spawn-mobs-max caps monsters so the ids
    stay far below the keeps' --keep-base."""
    import math as _m
    groups = fegamedata.spawn_groups(gid)
    cap_pts = int(getattr(args, "spawns_max", 12))
    cap_mobs = max(1, int(getattr(args, "spawn_mobs_max", 200) or 200))
    base = int(getattr(args, "monster_base", 400)) + 1000
    gh = field_ground_height(gid)
    mode_h = str(getattr(args, "monster_height", "dat") or "dat")
    k = lifted = 0
    per_point = []
    # --spawn-layout scatter (default): seeded packs spread past the point's
    # own radius (spawn_scatter); 'spiral' is the first cut, an even disc
    layout = str(getattr(args, "spawn_layout", "scatter") or "scatter")
    spread = float(getattr(args, "spawn_spread", 2.0) or 2.0)
    for pidx, (x, y, z, rad, gen, members) in enumerate(groups[:cap_pts]):
        guessed = False
        if mode_h != "table" and gh is not None and y <= 0.0:
            y, guessed = gh, True
        n = sum(m[3] for m in members)
        offs = (spawn_scatter(gid, gen, pidx, n, rad, spread)
                if layout == "scatter" else None)
        i = 0
        for tid, mtype, name, count in members:
            st = fegamedata.npc_stats(tid)
            for _c in range(count):
                if k >= cap_mobs:
                    break
                if offs:
                    mx, mz = x + offs[i][0], z + offs[i][1]
                else:
                    rr = 0.8 * rad * _m.sqrt((i + 0.5) / max(1, n))
                    th = i * 2.399963229728653
                    mx, mz = x + rr * _m.cos(th), z + rr * _m.sin(th)
                town.npc_send(conn, outbound, mode, be, args, base + k, mtype, 0,
                         st.get("level") or int(getattr(args, "monster_level", 1)),
                         mx, y, mz, name=name, type_id=tid,
                         send_it=not register_only,
                         why="(dat.pak spawner %d: %s L%s, %d of %d here, r=%g)"
                             % (gen, name, st.get("level", "?"), i + 1, n, rad))
                if guessed:
                    (sess._SESSION.get("mobs", {}).get(base + k) or {})["y_guess"] = True
                    lifted += 1
                k += 1
                i += 1
        per_point.append(i)
    print("[feworld]    dat.pak spawns for field %d: %d monster(s) at %d point(s) "
          "%s -- each point's generator population (+0x10), types by its own "
          "weights, capped per type%s%s. --spawn-population one = the old one "
          "per point."
          % (gid, k, len(per_point), per_point,
             " (stopped at --spawn-mobs-max %d)" % cap_mobs if k >= cap_mobs else "",
             "; %d on the map's median ground height %.1f (the table ships no Y)"
             % (lifted, gh) if lifted else ""), flush=True)
    return k
