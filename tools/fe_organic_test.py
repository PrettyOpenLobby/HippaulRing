#!/usr/bin/env python3
"""fe_organic_test.py -- scattered packs and idle wandering for dat.pak spawns.

Run from services/:  python ../tools/fe_organic_test.py

WHY. Reported in live testing 2026-09-12: "you can tell where the spawn
points are because it's just clusters around them. It feels a bit
inorganic." Each point's
population stood on an even golden-angle disc inside 0.8 x its radius, and
a monster only ever moved when chasing.

Drives the real functions: spawn_scatter, _spawn_push_population on real
dat.pak field data, and monster_wander_pump through the thread-local session.
"""
import math
import os
import struct
import sys
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import fegamedata   # noqa: E402
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
    say("  %-66s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label + (": " + detail if detail else ""))


def _args(**kw):
    a = dict(combat="on", monster_wander="on", monster_wander_secs="6:15",
             monster_wander_radius=12.0, monster_aggro=15.0,
             spawn_layout="scatter", spawn_spread=2.0, spawns="dat",
             spawn_population="dat", spawns_max=12, spawn_mobs_max=200,
             monster_base=400, monster_height="dat", monster_level=1,
             monster_level_source="shipped", monster_hp=0, npc_names="on",
             seq_mode="count", world_prefix=4, unit_id="1")
    a.update(kw)
    return types.SimpleNamespace(**a)


def main():
    sent = []
    real_inner = feworld.inner_msg

    def inner(mid, payload=b"", *a, **k):
        sent.append((mid, bytes(payload), a[0] if a else None))
        return real_inner(mid, payload, *a, **k)

    saved = (feworld.send, feworld.inner_msg)
    feworld.send = lambda *a, **k: None
    feworld.inner_msg = inner

    def fresh(**kw):
        feworld._TLS.session = dict(kw)
        del sent[:]
        return feworld._TLS.session

    try:
        say("spawn_scatter")
        a = feworld.spawn_scatter(12, 44, 0, 9, 30.0, 2.0)
        b = feworld.spawn_scatter(12, 44, 0, 9, 30.0, 2.0)
        c = feworld.spawn_scatter(12, 44, 1, 9, 30.0, 2.0)
        check("nine offsets for nine monsters", len(a) == 9)
        check("SEEDED: the same point gives the same layout (every player)",
              a == b)
        check("another point gives another layout", a != c)
        far = max(math.hypot(dx, dz) for dx, dz in a)
        check("reaches past the old 0.8 x radius disc (24 u)", far > 24.0,
              "%.1f" % far)
        check("...and stays inside spread x radius + a pack's 5 u",
              far <= 30.0 * 2.0 + 5.0, "%.1f" % far)

        say("_spawn_push_population on real field data (field 12)")
        fresh(field=12, in_field=True)
        n1 = feworld._spawn_push_population(None, None, "ecb", False, _args(), 12)
        pos1 = {o: m["pos"] for o, m in feworld._SESSION["mobs"].items()}
        fresh(field=12, in_field=True)
        n2 = feworld._spawn_push_population(None, None, "ecb", False, _args(), 12)
        pos2 = {o: m["pos"] for o, m in feworld._SESSION["mobs"].items()}
        check("the population count is unchanged by the layout (%d)" % n1,
              n1 == n2 and n1 > 0)
        check("two entries place every monster in the same spot", pos1 == pos2)
        fresh(field=12, in_field=True)
        feworld._spawn_push_population(None, None, "ecb", False,
                                       _args(spawn_layout="spiral"), 12)
        pos3 = {o: m["pos"] for o, m in feworld._SESSION["mobs"].items()}
        check("'spiral' still gives the old even discs (a different layout)",
              pos3 != pos1 and len(pos3) == len(pos1))
        check("home == the placed spot (a respawn goes back there)",
              all(m["home"] == m["pos"]
                  for m in feworld._SESSION["mobs"].values()))

        say("monster_wander_pump")
        mt = next(iter(fegamedata.model_speeds()))
        walk = fegamedata.model_speed(mt)[0]
        home = (100.0, 20.0, 100.0)

        def mob(**kw):
            m = {"type": mt, "pos": home, "home": home, "dead": False}
            m.update(kw)
            return m

        S = fresh(in_field=True, field_ready=True, cpos=(400.0, 20.0, 400.0),
                  mobs={1401: mob(), 1402: mob(dead=True),
                        1403: mob(), 1404: mob()})
        args = _args()
        check("off: nothing", feworld.monster_wander_pump(
            None, None, "ecb", False, _args(monster_wander="off")) == 0)
        check("first pass only staggers (no one moves in unison)",
              feworld.monster_wander_pump(None, None, "ecb", False, args) == 0
              and all("wander_due" in m for o, m in S["mobs"].items()
                      if not m["dead"]))
        past = time.monotonic() - 1
        for o in (1401, 1403, 1404):
            S["mobs"][o]["wander_due"] = past
        S["mobs"][1403]["chase_at"] = time.monotonic()          # being chased
        S["mobs"][1404]["pos"] = (398.0, 20.0, 398.0)           # near the player
        S["mobs"][1404]["home"] = (398.0, 20.0, 398.0)
        n = feworld.monster_wander_pump(None, None, "ecb", False, args)
        moves = [(o, p) for m_, p, o in sent if m_ == 0x2023]
        check("only the idle, alive, un-chased, out-of-aggro one strolls",
              n == 1 and [o for o, _ in moves] == [1401], repr(moves))
        body = moves[0][1]
        a_, b_ = struct.unpack_from(">II", body, feworld._MV_A)
        tx, ty, tz = (v / 10.0 for v in struct.unpack_from(">hhh", body,
                                                           feworld._MV_POS))
        d = math.hypot(tx - home[0], tz - home[2])
        check("the target is within the leash of HOME (12 u)", d <= 12.0 + 0.2,
              "%.2f" % d)
        check("...and at least 1.5 u away (the client drops < 1.0)", d >= 1.4,
              "%.2f" % d)
        dur = (b_ - a_) & 0xFFFFFFFF
        check("END - START = the walk's duration at the model's WALK speed",
              abs(dur - d / walk * 1000.0) < 60.0, "%d vs %.0f" % (dur, d / walk * 1000))
        check("the registry follows the monster",
              abs(S["mobs"][1401]["pos"][0] - tx) < 0.2)
        check("the next stroll waits for arrival + a 6..15 s pause",
              S["mobs"][1401]["wander_due"] - time.monotonic()
              >= dur / 1000.0 + 5.9)
        del sent[:]
        check("no second stroll before it is due",
              feworld.monster_wander_pump(None, None, "ecb", False, args) == 0)
        S["field_ready"] = False
        S["mobs"][1401]["wander_due"] = past
        check("not during field load (field_ready unset)",
              feworld.monster_wander_pump(None, None, "ecb", False, args) == 0)
    finally:
        feworld.send, feworld.inner_msg = saved

    say("\n%d/%d checks passed" % (sum(ok for _, ok in CHECKS), len(CHECKS)))


if __name__ == "__main__":
    main()
