#!/usr/bin/env python3
"""fe_shared_mobs_test.py -- ONE set of monsters per field (2026-09-13).

Requested in live testing: "we need to have monsters shared between
players". Until then every
session built its own copy of a field's monsters, so two players in one field
fought two different orcs. This drives the real functions through the
thread-local session, swapping _TLS.session to play two players:

  * the first player in builds the field's monsters ONCE; a second player is
    shown the SAME set as it is now -- not the dead, the hurt at their HP, a
    walking one where it stands along its walk
  * a kill by one player is one kill: the shared record is dead for both,
    the death is relayed to the other, a second blow on it does nothing
  * a relay reaches only a session showing that field
  * ONE session drives a field's monsters; a silent driver is replaced
  * the chase goes for the NEAREST LIVING player, not the driver's own
  * --monster-share off keeps one copy per session
"""
import os
import struct
import sys
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import feworld      # noqa: E402

CHECKS = []
OUT = sys.__stdout__
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-70s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + str(detail)) if (detail and not cond) else ""))


def _args(**kw):
    a = dict(combat="on", monster_share="on", monster_chase="on",
             monster_aggro=15.0, monster_range=8.0, monster_speed="3.0",
             monster_chase_interval=0.0, monster_chase_timing="step",
             spawns="dat", spawn_population="dat", spawns_max=12,
             spawn_mobs_max=200, monster_base=400, monster_height="dat",
             monster_level=1, monster_level_source="shipped", monster_hp=0,
             npc_names="on", spawn_layout="scatter", spawn_spread=2.0,
             seq_mode="count", world_prefix=4, unit_id="1", hit_damage=99999,
             respawn_secs=0, drops="off", kill_reward="off")
    a.update(kw)
    return types.SimpleNamespace(**a)


GID = 12


def main():
    inner_log = []          # (mid, unit)
    shows = []              # (obj, x, z, register) from npc_send
    posts = []              # (kind, payload, to)
    others = []             # the OTHER sessions ext_sessions reports
    rewards = []
    real = dict(inner_msg=feworld.inner_msg, send=feworld.send,
                npc_send=feworld.npc_send, ext_post=feworld.ext_post,
                ext_sessions=feworld.ext_sessions,
                kill_gold_credit=feworld.kill_gold_credit,
                drop_on_kill=feworld.drop_on_kill,
                battle_tally=feworld.battle_tally,
                store=feworld._store_char_field, load=feworld._load_char_field)

    def inner(mid, payload=b"", *a, **k):
        inner_log.append((mid, a[0] if a else None))
        return real["inner_msg"](mid, payload, *a, **k)

    def npc(conn, outbound, mode, be, args, obj, mtype, kind, level, x, y, z,
            **k):
        if k.get("send_it", True):
            shows.append((obj, x, z, k.get("register", True)))
        return real["npc_send"](conn, outbound, mode, be, args, obj, mtype,
                                kind, level, x, y, z, **k)

    feworld.inner_msg = inner
    feworld.send = lambda *a, **k: None
    feworld.npc_send = npc
    feworld.ext_post = lambda kind, payload, to=None, include_me=False: (
        posts.append((kind, payload, to)) or 1)
    feworld.ext_sessions = lambda: [{"key": i, "session": s, "name": "p%d" % i}
                                    for i, s in enumerate(others)]
    feworld.kill_gold_credit = lambda *a, **k: rewards.append("gold")
    feworld.drop_on_kill = lambda *a, **k: None
    feworld.battle_tally = lambda *a, **k: None
    feworld._store_char_field = lambda *a, **k: True
    feworld._load_char_field = lambda a, k, d=None: d

    def enter(**kw):
        """A session entering field GID outdoors -- the field-entry lines."""
        s = dict(in_field=True, field=GID, room=-1, field_ready=True, **kw)
        feworld._TLS.session = s
        if feworld.mobs_shared(args):
            s["mobs"] = feworld.field_mobs(GID)["mobs"]
            s["mobs_field"] = GID
        else:
            s["mobs"] = {}
        return s

    def reset():
        del inner_log[:], shows[:], posts[:]

    try:
        args = _args()
        ctx = feworld.Ctx(None, None, "ecb", False, args)
        feworld._FIELD_MOBS.clear()
        feworld._MOB_DRIVER.clear()

        say("the first player in builds the field ONCE")
        A = enter(charid=1, cpos=(0.0, 0.0, 0.0))
        reset()
        feworld.spawn_push(None, None, "ecb", False, args)
        world = feworld.field_mobs(GID)
        n = len(world["mobs"])
        check("the field's monsters are built into the SHARED registry",
              n > 0 and world["built"] and A["mobs"] is world["mobs"], n)
        check("...and this client is shown every one of them (%d)" % n,
              len(shows) == n and all(not r for _o, _x, _z, r in shows),
              len(shows))
        check("...then the relays open to it (mobs_shown)",
              A.get("mobs_shown") == GID)

        objs = sorted(world["mobs"])
        dead_o, hurt_o, walk_o = objs[0], objs[1], objs[2]
        world["mobs"][dead_o]["dead"] = True
        world["mobs"][hurt_o]["hp"] = max(1, world["mobs"][hurt_o]["hpmax"] // 2)
        wm = world["mobs"][walk_o]
        start = feworld.mob_pos(wm)
        feworld.mob_move_note(wm, (start[0] + 40.0, start[1], start[2]), 20000)

        say("a second player walks in and meets the SAME monsters")
        B = enter(charid=2, cpos=(500.0, 0.0, 500.0))
        reset()
        feworld.spawn_push(None, None, "ecb", False, args)
        check("nothing is rebuilt: the hurt one keeps its HP",
              B["mobs"] is world["mobs"] and len(world["mobs"]) == n
              and world["mobs"][hurt_o]["hp"] < world["mobs"][hurt_o]["hpmax"])
        shown = {o: (x, z) for o, x, z, _r in shows}
        check("the dead one is NOT shown, every living one is",
              dead_o not in shown and len(shown) == n - 1, len(shown))
        check("the hurt one goes out with its HP (a stat record after it)",
              sum(1 for mid, u in inner_log if mid == 0x1006 and u == hurt_o) == 2)
        check("the walking one is shown where it STANDS along its walk, not "
              "at the walk's end",
              abs(shown[walk_o][0] - start[0]) < 2.0, (shown[walk_o], start))

        say("one kill is one kill")
        target = objs[3]
        # one hit from death: the damage model is the skill tables and the
        # level correction now, not --hit-damage, so pin the HP instead
        world["mobs"][target]["hp"] = 1
        reset()
        rewards[:] = []
        body = struct.pack(">IIII", target, 2, 0, 0) + struct.pack(">HB", 270, 1)
        feworld.combat_hit(None, None, "ecb", False, args, body)
        m = world["mobs"][target]
        check("B's killing blow kills the SHARED record (A's too)",
              m["dead"] and A["mobs"][target]["dead"])
        dels = [p for p in posts if p[0] == "mob_del"]
        check("...and the death is relayed with the field",
              len(dels) == 1 and dels[0][1] == {"obj": target, "field": GID},
              posts)
        to = dels[0][2]
        check("...to a session showing this field, not to one elsewhere",
              to(A, "a") and not to(dict(A, field=GID + 1), "x")
              and not to(dict(A, mobs_shown=None), "y"))
        feworld._TLS.session = A
        reset()
        feworld.EXT_RELAY["mob_del"](ctx, dels[0][1])
        check("A's session applies it: 0x1004 for that monster",
              inner_log == [(0x1004, target)], inner_log)
        feworld._TLS.session = dict(A, mobs_shown=None)
        reset()
        feworld.EXT_RELAY["mob_del"](ctx, dels[0][1])
        check("a session not yet shown the field ignores it", not inner_log)
        feworld._TLS.session = A
        reset()
        feworld.combat_hit(None, None, "ecb", False, args, body)
        check("A's blow a moment later lands on nothing: no second reward",
              rewards == ["gold"] and not [i for i in inner_log
                                           if i[0] in (0x1004, 0x1006)],
              (rewards, inner_log))

        say("one session drives the field")
        feworld._MOB_DRIVER.clear()
        check("the first to ask drives", feworld.mob_driver(args))
        feworld._MOB_DRIVER[GID] = (-1, time.monotonic())
        check("...another live driver keeps it", not feworld.mob_driver(args))
        feworld._MOB_DRIVER[GID] = (-1, time.monotonic() - 10.0)
        check("...a silent one is replaced", feworld.mob_driver(args))

        say("the chase goes for the NEAREST LIVING player")
        live = [o for o in objs if not world["mobs"][o]["dead"]]
        chaser = live[-1]
        cm = world["mobs"][chaser]
        cm.pop("mv", None)
        cx, cy, cz = cm["pos"]
        A["player_dead"] = True                  # the driver's own is down
        A["cpos"] = (cx + 300.0, cy, cz)
        B2 = dict(B, cpos=(cx + 12.0, cy, cz))   # B is 12 u away, alive
        others[:] = [B2]
        for o in live:
            world["mobs"][o]["chase_at"] = 0
        reset()
        feworld.monster_chase_tick(None, None, "ecb", False, args,
                                   bytes(feworld._MV_LEN))
        # the step's END: mob_pos right after the send is still its start
        moved = world["mobs"][chaser]["pos"]
        check("with the driver's own player dead, a monster still walks at "
              "the living one", moved[0] > cx, (moved, cx))
        check("...and the move is relayed to the others",
              any(k == "mob_move" and p["obj"] == chaser for k, p, _t in posts))
        feworld._MOB_DRIVER[GID] = (-1, time.monotonic())
        reset()
        feworld.monster_chase_tick(None, None, "ecb", False, args,
                                   bytes(feworld._MV_LEN))
        check("a session that is not the driver moves nothing",
              not [i for i in inner_log if i[0] == 0x2023])
        others[:] = []

        say("--monster-share off")
        off = _args(monster_share="off")
        args = off
        feworld._FIELD_MOBS.clear()
        C = enter(charid=3, cpos=(0.0, 0.0, 0.0))
        reset()
        feworld.spawn_push(None, None, "ecb", False, off)
        check("each session builds its own copy, nothing shared",
              C["mobs"] and GID not in feworld._FIELD_MOBS
              and "mobs_field" not in C, len(C["mobs"]))
    finally:
        feworld.inner_msg = real["inner_msg"]
        feworld.send = real["send"]
        feworld.npc_send = real["npc_send"]
        feworld.ext_post = real["ext_post"]
        feworld.ext_sessions = real["ext_sessions"]
        feworld.kill_gold_credit = real["kill_gold_credit"]
        feworld.drop_on_kill = real["drop_on_kill"]
        feworld.battle_tally = real["battle_tally"]
        feworld._store_char_field = real["store"]
        feworld._load_char_field = real["load"]
        feworld._FIELD_MOBS.clear()
        feworld._MOB_DRIVER.clear()

    bad = [c for c, ok in CHECKS if not ok]
    say("\n[fe_shared_mobs_test] %s -- %d checks"
        % ("OK" if not bad else "FAILED %d" % len(bad), len(CHECKS)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
