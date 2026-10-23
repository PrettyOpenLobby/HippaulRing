#!/usr/bin/env python3
"""fe_warbots_test.py -- WAR BOTS (fewarbots, 2026-10-23): a war that fills
itself with soldiers so one or two players can stand in a big one.

Run from services/:  python ../tools/fe_warbots_test.py

Two real sessions (Alice attacks for nation 3, Eve defends for nation 1)
through the real read loop and the real defaults, and:
  * --war-bots 0 (the default): a whole war with no bot anywhere -- no
    sign-up, no avatar, the counts the players' own;
  * --war-bots 3: three bots a side at war start, signed up (the 0x1127
    counts include them) and flagged, not counted by --pop-scale;
  * each session draws them as 0x1006 type-0 avatars under ids in the
    reserved block, with the war-side byte; they walk (0x2023);
  * a bot swings at Eve: pvp.hit on her thread, her HP drops, and both
    screens get ONE 0x1032 under the bot's id in a real basic attack's
    layout (its class's skill, its weapon uid, aimed at her, Alice's own
    cast clock); --war-bot-swing-anim off sends none;
  * Alice hits a bot (cast path and 0xA011): its HP drops, the last hit
    fells it, every screen gets CONDITION DEAD (0x2024 0x800000) first and
    0x1004 only after --war-bot-corpse-s; it stands up again at its keep;
  * a bot at the enemy keep takes keep_damage with nobody credited;
  * `!warbots`/set_count mid-war adds and removes bots, 0 takes all;
  * at war end every bot and bot sign-up is gone, the screens get 0x1004,
    and settle() never touched a stored character for a bot.
FLOOD CONTROL (after the first live run, 2026-10-23: 45 a side on one
screen, ~125 messages a second, the client hung and crashed). Each of these
fails on the file before the fix:
  * the bots spawn BEHIND their keep (15-30 u, away from the enemy keep)
    and march one by one (--war-bot-stagger-ms);
  * a walk record is a real one's: speed pair 1300, stamped now, at the
    bot's position plus 0.4 s of its walk (2.08 u), one per 400 ms;
  * 99 asked for (set_count, the admin route, `!warbots`, --war-bots) is
    20 a side, and the reply says why;
  * a screen adds at most 8 avatars a pump, nearest first, only within
    --war-bot-view, and removes them past view + 10;
  * at most --war-bot-msg-rate bot messages in one second, a fall before
    any walk record, the nearest bots' walks first, swings last.
"""
import struct
import sys
import time

import fe_harness as H
from fe_harness import check, ids, say

AREA = 17           # nation 1's, next to nation 3's 53


def cast_body(skill, target=0, pos=None, a=1, b=2):
    body = struct.pack(">IIBIIHBII", a, b, 0, 0, 0, skill, 1, target, 0)
    if target == 0:
        body += struct.pack(">fff", *pos)
    return body


def adds(box):
    """{obj: type byte} of the 0x1006 avatar ADDs in `box` (mask1 bit 0, the
    identity -- a facing-only update has mask1 0)."""
    out = {}
    for m, _u, b in box:
        if m == 0x1006 and len(b) >= 11:
            n, obj, typ, mask1 = struct.unpack_from(">HIBI", b, 0)
            if mask1 & 1:
                out[obj] = typ
    return out


def dels(box):
    return [u for m, u, _b in box if m == 0x1004]


#: fewarbots._players while a section hides the players from the bots
REAL_PLAYERS = [None]


def soldiers(args, alice, eve, lo, hi, view0, rate0, fecampaign, fewarbots):
    """PHASE 1 + 2 (2026-10-23): soldiers, not a hivemind, and players decide
    the war. Every check here fails with its feature taken out (proved by
    hand when written: roles all 'front', spread 0, routes straight to the
    keep, no squad following, no kite, no retreat, no keep floor, no
    respawn nudge, no pause, every pick 'near')."""
    import math
    AREA_ = AREA
    view_args = (args.war_bot_view, args.war_bot_msg_rate)
    args.war_bot_view, args.war_bot_msg_rate = view0, rate0
    REAL_PLAYERS[0] = fewarbots._players
    # the players stand among the bots and watch, but the bots do not see
    # them: these sections are about the bots' own behaviour
    fewarbots._players = lambda *a_: []
    bad1006 = []
    clock = [time.monotonic() + 100.0]
    fewarbots._TICK.pop(AREA_, None)

    def watch():
        """Alice's pump, noting any 0x1006 to a bot already on her screen
        under the same spawn (a model rebuild: the 2026-10-23 crash)."""
        seen = {b: st["gen"] for b, st in (alice.session.get("wb_seen") or {}).items()}
        gens = {b: v["gen"] for b, v in fewarbots.bots_of(AREA_).items()}
        out = alice.tick()
        bad1006.extend(u for m, u, _b in out
                       if m == 0x1006 and u in seen and seen[u] == gens.get(u))
        return out

    def tick(dt):
        clock[0] += dt
        did = fewarbots.tick(args, AREA_, clock[0])
        watch()
        return did

    def foe_of(side):
        return "def" if side == "atk" else "atk"

    def only(keep_ids):
        """Every bot but `keep_ids` down for good; those up, fresh."""
        with fewarbots._LOCK:
            for b, v in fewarbots._BOTS[AREA_].items():
                if b in keep_ids:
                    v.update(dead_until=None, died_at=None, retreat=False,
                             pause_until=0.0, wait_until=0.0, target=None,
                             kite_left=fewarbots.KITE_SECS, swing_at=0.0,
                             hp=v["hpmax"], moving=False, goal=None)
                else:
                    v.update(dead_until=clock[0] + 1e6, died_at=clock[0] - 1e3,
                             moving=False, goal=None)

    def put(b, pos, **kw):
        with fewarbots._LOCK:
            fewarbots._BOTS[AREA_][b].update(pos=tuple(pos), **kw)

    def fresh(n):
        with H.quiet():
            fewarbots.set_count(args, 0, actor="test")
            fewarbots.set_count(args, n, actor="test")
        fewarbots._TICK.pop(AREA_, None)
        return fewarbots.bots_of(AREA_)

    # ------------------------------------------------ roles and stats
    say("soldiers: roles, squads, stats (20 a side)")
    sb = fresh(20)
    roles = {s: sorted(v["role"] for v in sb.values() if v["side"] == s)
             for s in ("atk", "def")}
    check("ROLES: each side a mix of front, ranged, flank and defend, the "
          "same mix on both (one pool)",
          all(set(r) == {"front", "ranged", "flank", "defend"}
              for r in roles.values()) and roles["atk"] == roles["def"],
          roles)
    check("...all three classes on each side; every ranged one a Ranger or "
          "an Adept shooting from 15-19 u, every Footman at --war-bot-reach",
          all(sorted({v["cls"] for v in sb.values() if v["side"] == s})
              == [0, 1, 2] for s in ("atk", "def"))
          and all(v["cls"] in (1, 2) and 15.0 <= v["range"] <= 18.5
                  for v in sb.values() if v["role"] == "ranged")
          and all(v["range"] == args.war_bot_reach
                  for v in sb.values() if v["cls"] == 0),
          sorted((v["role"], v["cls"], round(v["range"], 1))
                 for v in sb.values()))
    sizes = {}
    for v in sb.values():
        sizes[(v["side"], v["squad"])] = sizes.get((v["side"], v["squad"]), 0) + 1
    check("...in squads of 3-5", all(3 <= n <= 5 for n in sizes.values()),
          sizes)
    picks = {s: {v["pick"] for v in sb.values() if v["side"] == s}
             for s in ("atk", "def")}
    check("...and more than one way of picking a target on each side",
          all(len(p) >= 2 for p in picks.values()), picks)
    foot = [v for v in sb.values() if v["cls"] == 0]
    hps, dmgs = [v["hpmax"] for v in foot], [v["dmg"] for v in foot]
    spd = [v["speed"] for v in sb.values()]
    check("STATS: Footmen HP %d-%d and damage %d-%d (1000/40 +- 25%%), "
          "nearly every one its own"
          % (min(hps), max(hps), min(dmgs), max(dmgs)),
          max(hps) >= 1.2 * min(hps) and len(set(hps)) >= len(foot) - 2
          and all(750 <= h <= 1250 for h in hps)
          and max(dmgs) >= 1.15 * min(dmgs) and all(30 <= d <= 50 for d in dmgs),
          (hps, dmgs))
    check("...walking speeds %.2f-%.2f u/s: each its own, never faster than "
          "5.2" % (min(spd), max(spd)),
          all(4.6 <= x <= 5.2 for x in spd) and max(spd) - min(spd) > 0.2, spd)
    w0 = min(v["wait_until"] for v in sb.values())
    stag = {(v["side"], v["squad"]): round(v["wait_until"] - w0, 2)
            for v in sb.values()}
    check("STAGGER by squad: a squad sets off together, squad k at its "
          "first bot's number x %d ms" % args.war_bot_stagger_ms,
          all(abs(v["wait_until"] - w0 - fewarbots.squad_start(v["squad"])
                  * args.war_bot_stagger_ms / 1000.0) < 0.05
              for v in sb.values()), stag)

    # ------------------------------------------------ own paths and squads
    say("soldiers: own paths, squads together, defenders hold")
    start = {b: v["pos"] for b, v in sb.items()}
    did = []
    for _ in range(25):
        did += tick(0.5)
    nb = fewarbots.bots_of(AREA_)

    def off_line(b):
        p0, p = start[b], nb[b]["pos"]
        foe = fewarbots.keep_pos(AREA_, foe_of(nb[b]["side"]))
        dx, dz = foe[0] - p0[0], foe[2] - p0[2]
        h = math.hypot(dx, dz) or 1.0
        return abs((p[0] - p0[0]) * dz - (p[2] - p0[2]) * dx) / h
    devs = {s: sorted(round(off_line(b), 1) for b, v in nb.items()
                      if v["side"] == s and v["role"] != "defend")
            for s in ("atk", "def")}
    check("OWN PATHS: after 12 s of marching, at least 3 bots a side are "
          "more than 8 u off the straight line to the enemy keep (the "
          "flankers and their squads), others on a jittered front line",
          all(sum(d > 8.0 for d in devs[s]) >= 3 for s in devs), devs)
    pts = [v["pos"] for v in nb.values()]
    gap = min(fewarbots._xz(a, b) for i, a in enumerate(pts) for b in pts[i + 1:])
    check("...no two bots in one spot (closest pair %.1f u)" % gap, gap > 1.0)
    leaders = fewarbots._leaders(nb)
    errs, walked = {}, []
    for (s, k), (lead, _v) in leaders.items():
        walked.append(fewarbots._xz(start[lead], nb[lead]["pos"]))
        foe = fewarbots.keep_pos(AREA_, foe_of(s))
        for b, v in nb.items():
            if v["side"] == s and v["squad"] == k and b != lead:
                errs[b] = round(fewarbots._xz(
                    v["pos"], fewarbots._slot(nb[lead], v, foe)), 1)
    check("SQUADS: the leaders walked %.0f-%.0f u, and every member is within "
          "4 u of its formation slot behind its leader"
          % (min(walked), max(walked)),
          min(walked) > 40.0 and errs and max(errs.values()) < 4.0, errs)
    defs = {b: round(fewarbots._xz(v["pos"], v["post"]), 1)
            for b, v in nb.items() if v["role"] == "defend"}
    check("DEFENDERS hold their posts in front of their own keep (within 2 u "
          "of the post, none marched)", defs and max(defs.values()) < 2.0,
          defs)

    # ------------------------------------------------ pauses
    say("soldiers: short pauses")
    args.war_bot_pause = 4.0                    # x 0.5 s: every marcher
    did = tick(0.5)
    paused = [e[1] for e in did if e[0] == "pause"]
    pb = fewarbots.bots_of(AREA_)
    check("--war-bot-pause: a marching bot stops for 0.6-2 s (%d paused)"
          % len(paused),
          len(paused) >= 10 and all(
              not pb[b]["moving"] and 0.6 <= pb[b]["pause_until"] - clock[0] <= 2.0
              for b in paused), paused)
    args.war_bot_pause = 0.0
    tick(2.1)
    pb = fewarbots.bots_of(AREA_)
    check("...and walks on after it", all(pb[b]["moving"] for b in paused),
          [b for b in paused if not pb[b]["moving"]])

    # ------------------------------------------------ target choice
    say("soldiers: target choice")
    cands = [("bot", 1, (0.0, 0.0, 3.0), 1.0, 3.0),
             ("bot", 2, (0.0, 0.0, 9.0), 0.1, 9.0),
             ("bot", 3, (0.0, 0.0, 6.0), 0.5, 6.0)]
    import random as _r
    fake = {"pick": "near", "rng": _r.Random(5)}
    got = {how: fewarbots._pick(dict(fake, pick=how), cands, 0.0)[1]
           for how in ("near", "weak")}
    rb = dict(fake, pick="random", target=None, target_until=0.0)
    first = fewarbots._pick(rb, cands, 0.0)[1]
    held = fewarbots._pick(rb, cands, 1.0)[1]
    many = {fewarbots._pick(dict(rb, target=None), cands, 0.0)[1]
            for _ in range(30)}
    check("PICKS: 'near' takes the nearest (1), 'weak' the lowest HP (2), "
          "'random' any (%s) and holds it a while" % sorted(many),
          got == {"near": 1, "weak": 2} and held == first and len(many) >= 2,
          (got, first, held))

    # ------------------------------------------------ ranged keep distance
    say("soldiers: ranged bots keep their distance")
    nb = fewarbots.bots_of(AREA_)
    R = [b for b, v in nb.items() if v["side"] == "atk" and v["role"] == "ranged"][0]
    M = [b for b, v in nb.items() if v["side"] == "def" and v["cls"] == 0
         and v["role"] != "defend"][0]
    ka, kd = fewarbots.keep_pos(AREA_, "atk"), fewarbots.keep_pos(AREA_, "def")
    P = ((ka[0] + kd[0]) / 2, ka[1], (ka[2] + kd[2]) / 2)
    only({R, M})
    put(R, P)
    put(M, (P[0] + 10.0, P[1], P[2]), swing_at=1e12)
    fewarbots._TICK.pop(AREA_, None)
    did = tick(0.0)
    rb_ = fewarbots.bots_of(AREA_)
    check("a %s (range %.1f) with an enemy 10 u off stands and shoots"
          % (fewarbots.CLASS_NAMES[nb[R]["cls"]], nb[R]["range"]),
          ("hit", R, M, nb[R]["dmg"]) in did and rb_[R]["pos"] == P
          and not rb_[R]["moving"], did)
    check("...while the Footman it shoots has to walk in to swing",
          rb_[M]["moving"] and not [e for e in did if e[0] == "hit" and e[1] == M])
    put(M, (P[0] + 3.0, P[1], P[2]))
    put(R, P, swing_at=0.0)
    d0 = 3.0
    did = tick(0.5)
    rb_ = fewarbots.bots_of(AREA_)
    d1 = fewarbots._xz(rb_[R]["pos"], rb_[M]["pos"])
    check("...and with one 3 u off (inside --war-bot-kite 5) it steps BACK "
          "(%.1f -> %.1f u) instead of shooting" % (d0, d1),
          ("kite", R) in did and d1 > d0 + 1.0
          and not [e for e in did if e[0] == "hit" and e[1] == R], did)
    shot = None
    for n in range(10):
        p = fewarbots.bots_of(AREA_)[R]["pos"]
        put(M, (p[0] + 3.0, p[1], p[2]))
        did = tick(0.5)
        if [e for e in did if e[0] == "hit" and e[1] == R]:
            shot = (n + 2) * 0.5
            break
    check("...but backs off for at most %.1f s, then stands and shoots "
          "(shot after %s s)" % (fewarbots.KITE_SECS, shot),
          shot is not None and shot <= fewarbots.KITE_SECS + 1.0)

    # ------------------------------------------------ retreat and heal
    say("soldiers: fall back at low HP")
    F = [b for b, v in nb.items() if v["side"] == "atk" and v["cls"] == 0
         and v["role"] != "defend"][0]
    only({F, M})
    put(F, P, hp=int(nb[F]["hpmax"] * 0.2))
    put(M, (P[0] + 3.0, P[1], P[2]), swing_at=1e12)
    home = nb[F]["home"]
    fewarbots._TICK.pop(AREA_, None)
    did = tick(0.0)
    did += tick(0.5)
    fb = fewarbots.bots_of(AREA_)[F]
    check("under 25%% HP (%d/%d) a bot falls back: it walks home past an "
          "enemy 3 u off without a swing" % (int(nb[F]["hpmax"] * 0.2),
                                            nb[F]["hpmax"]),
          ("retreat", F) in did and fb["retreat"] and fb["moving"]
          and fewarbots._xz(fb["pos"], home) < fewarbots._xz(P, home) - 1.0
          and not [e for e in did if e[0] in ("hit", "kill") and e[1] == F],
          did)
    only({F})
    put(F, home, hp=int(fb["hpmax"] * 0.2), retreat=True)
    h0 = fewarbots.bots_of(AREA_)[F]["hp"]
    for _ in range(5):
        tick(1.0)
    fb = fewarbots.bots_of(AREA_)[F]
    check("...behind its keep it heals (%d -> %d in 5 s, --war-bot-regen "
          "%.2f a second) and stays back" % (h0, fb["hp"], args.war_bot_regen),
          fb["hp"] >= h0 + 4 * int(args.war_bot_regen * fb["hpmax"])
          and fb["retreat"], (h0, fb["hp"]))
    put(F, home, hp=int(fb["hpmax"] * 0.89))
    did = tick(1.0)
    check("...and rejoins at 90%", ("rally", F) in did
          and not fewarbots.bots_of(AREA_)[F]["retreat"], did)

    # ------------------------------------------------ the keep floor
    say("players decide the war: bots stop at half a keep")
    with fecampaign._LOCK:
        kk = fecampaign._STATE[AREA_]["keeps"]["def"]
        mx = int(kk[1])
        floor_hp = int(math.ceil(args.war_bot_keep_floor * mx))
        kk[0] = floor_hp + 3
    atk_up = [b for b, v in nb.items() if v["side"] == "atk"]
    only(set(atk_up))
    for k_, b in enumerate(atk_up):
        put(b, (kd[0] + 2.0 + (k_ % 5), kd[1], kd[2] + (k_ // 5)))
    fewarbots._TICK.pop(AREA_, None)
    swings = 0
    for _ in range(10):
        with fewarbots._LOCK:
            for b in atk_up:
                fewarbots._BOTS[AREA_][b]["swing_at"] = 0.0
        swings += len([e for e in tick(0.25) if e[0] == "keep"])
    hp = fecampaign.state_of(AREA_)["keeps"]["def"][0]
    check("KEEP FLOOR: %d bot swings at a castle 3 over half take it to "
          "exactly half (%d of %d) and no further" % (swings, hp, mx),
          swings >= 100 and hp == floor_hp, (hp, floor_hp))
    check("...the keep has not fallen, so the war does not end on it",
          fecampaign.keep_fallen(AREA_) is None)
    rule = args.war_decide
    args.war_decide = "keeps"
    try:
        won = fecampaign.decide(args, AREA_)[0]
    finally:
        args.war_decide = rule
    check("A BOT-ONLY WAR runs to time-up, and --war-decide keeps then gives "
          "it to the DEFENDER (no keep at 0): %s" % won, won == "def")
    new, _ = alice.call(fecampaign.keep_damage, args, AREA_, "def", 10)
    check("...a PLAYER's hit goes below half (%d -> %s)" % (floor_hp, new),
          new == floor_hp - 10)
    # a player's death dealt by a bot drains the base only to the floor
    with fecampaign._LOCK:
        fecampaign._STATE[AREA_]["keeps"]["def"][0] = floor_hp + 2
    fewarbots._players = REAL_PLAYERS[0]
    only({M})
    Mb = fewarbots.bots_of(AREA_)[M]
    hitter = [b for b, v in nb.items() if v["side"] == "atk" and v["cls"] == 0][0]
    only({hitter})
    put(hitter, P, swing_at=0.0)
    eve.stand(P[0] + 1.0, P[1], P[2])
    eve.session["player_hp"] = 1
    eve.session.pop("player_dead", None)
    did, _ = eve.call(fewarbots.tick, args, AREA_, clock[0] + 0.25)
    clock[0] += 0.25
    H.deliver(alice, eve)
    hp = fecampaign.state_of(AREA_)["keeps"]["def"][0]
    check("...a bot kills Eve (the defender): her death drains her castle "
          "only to half (%d -> %d, not %d)" % (floor_hp + 2, hp,
                                              floor_hp + 2 - mx // 480),
          [e for e in did if e[0] == "pc"] and eve.session.get("player_dead")
          and hp == floor_hp, (hp, did))
    with fecampaign._LOCK:
        fecampaign._STATE[AREA_]["keeps"]["def"][0] = floor_hp + 2
    eve.session["war_death_by_bot"] = False
    hp2, _ = eve.call(fecampaign.base_hit, args, AREA_, "def", "death")
    check("...while any other death in the war still drains it below "
          "(%d -> %s)" % (floor_hp + 2, hp2), hp2 is not None and hp2 < floor_hp)
    eve.session.pop("war_death_by_bot", None)
    eve.session.pop("player_dead", None)
    eve.session["player_hp"] = 1000
    with fecampaign._LOCK:
        fecampaign._STATE[AREA_]["keeps"]["def"][0] = mx
    fewarbots._players = lambda *a_: []
    del Mb

    # ------------------------------------------------ soft balance
    say("soft balance: the outnumbered side respawns faster")
    up = fewarbots.bots_of(AREA_)
    a_side = [b for b, v in up.items() if v["side"] == "atk"]
    d_side = [b for b, v in up.items() if v["side"] == "def"]
    only(set(a_side[:5]) | set(d_side))
    t_ = time.monotonic()
    ra = fewarbots.hit_bot(args, a_side[0], 99999, area=AREA_)
    rd = fewarbots.hit_bot(args, d_side[0], 99999, area=AREA_)
    up = fewarbots.bots_of(AREA_)
    wa = up[a_side[0]]["dead_until"] - t_
    wd = up[d_side[0]]["dead_until"] - t_
    base = args.war_bot_respawn_s
    check("4 attackers standing against 20: a felled attacker is back in "
          "%.1f s (--war-bot-respawn-min %.1f x %.0f), a felled defender in "
          "the full %.1f s" % (wa, args.war_bot_respawn_min, base, wd),
          ra["dead"] and rd["dead"]
          and abs(wa - base * args.war_bot_respawn_min) < 0.5
          and abs(wd - base) < 0.5, (wa, wd))
    only(set(a_side[:15]) | set(d_side[:19]))
    t_ = time.monotonic()
    fewarbots.hit_bot(args, a_side[1], 99999, area=AREA_)
    wa = fewarbots.bots_of(AREA_)[a_side[1]]["dead_until"] - t_
    check("...14 against 19: (14 + 1) / (19 + 1) of it (%.1f s)" % wa,
          abs(wa - base * 15.0 / 20.0) < 0.5, wa)
    check("NO 0x1006 to a bot already on Alice's screen through all of the "
          "above (only adds of new spawns)", not bad1006, bad1006)

    # ------------------------------------------------ 45 a side, one spot
    say("45 a side in one spot: the budget holds")
    nb = fresh(45)
    check("45 a side (the war cap less 5)",
          sum(1 for v in nb.values() if v["side"] == "atk") == 45
          and sum(1 for v in nb.values() if v["side"] == "def") == 45)
    ax, ay, az = alice.session["cpos"]
    rr = _r.Random(3)
    with fewarbots._LOCK:
        for b, v in fewarbots._BOTS[AREA_].items():
            v.update(pos=(ax + rr.uniform(-8, 8), ay, az + rr.uniform(-8, 8)),
                     wait_until=0.0)
    alice.session["wb_seen"] = {}
    alice.session.pop("wb_sent", None)
    fewarbots._TICK.pop(AREA_, None)
    log, fights = [], 0
    t_end = time.monotonic() + 3.0
    while time.monotonic() < t_end:
        fights += len([e for e in fewarbots.tick(args, AREA_)
                       if e[0] in ("hit", "kill")])
        t0 = time.monotonic()
        seen = {b: st["gen"] for b, st in (alice.session.get("wb_seen") or {}).items()}
        gens = {b: v["gen"] for b, v in fewarbots.bots_of(AREA_).items()}
        out = alice.tick()
        bad1006.extend(u for m, u, _b in out
                       if m == 0x1006 and u in seen and seen[u] == gens.get(u))
        log.append((t0, len([1 for m, u, _b in out if lo <= u <= hi])))
        time.sleep(0.05)
    worst = max(sum(n for t2, n in log if t1 - 0.98 <= t2 <= t1)
                for t1, _n in log)
    total = sum(n for _t, n in log)
    say("  (45 a side within 8 u: %d pumps in 3 s, %d bot messages, %d "
        "hits/kills among the bots, %d drawn)"
        % (len(log), total, fights, len(alice.session.get("wb_seen") or {})))
    check("90 bots fighting within 8 u of Alice for 3 s: at most %d bot "
          "messages in any one second (worst %d), still sending (%d)"
          % (rate0, worst, total),
          worst <= rate0 and total >= 2 * rate0 and fights > 0,
          (worst, total, fights))
    check("...and NO 0x1006 to a bot already on her screen", not bad1006,
          bad1006)
    fewarbots._players = REAL_PLAYERS[0]
    args.war_bot_view, args.war_bot_msg_rate = view_args


def main():
    H.setup()
    import feworld
    import fecampaign
    import fewarbots
    from world import buildings
    args = H.real_args()
    check("real defaults: --war-bots 0, presence on (--unit-id auto), campaign on",
          args.war_bots == 0 and str(args.unit_id) == "auto"
          and args.campaign == "on")
    check("flood/speed defaults: view 50 u, 20 bot messages a second, a walk "
          "record per 400 ms, 4.6-5.2 u/s (speed pair at most 1300), 500 ms "
          "stagger",
          (args.war_bot_view, args.war_bot_msg_rate, args.war_bot_move_ms,
           args.war_bot_speed, args.war_bot_speed_min, args.war_bot_stagger_ms)
          == (50.0, 20, 400, 5.2, 4.6, 500),
          (args.war_bot_view, args.war_bot_msg_rate, args.war_bot_move_ms,
           args.war_bot_speed, args.war_bot_stagger_ms))
    check("soldier/war defaults: keep floor 0.5, stat spread 0.25, range 16 u, "
          "kite 5 u, retreat under 25%, respawn nudge down to 0.4",
          (args.war_bot_keep_floor, args.war_bot_stat_spread,
           args.war_bot_range, args.war_bot_kite, args.war_bot_retreat,
           args.war_bot_respawn_min) == (0.5, 0.25, 16.0, 5.0, 0.25, 0.4))
    fewarbots.SEED[0] = 20261023           # repeatable bots
    args.war_bot_pause = 0.0               # pauses get their own check
    # phase 1-2 soldiers: the miners and the builder (phase 3, default on)
    # have their own suite, fe_warbots_economy_test.py
    check("phase 3 default: --war-bot-economy on", args.war_bot_economy == "on")
    args.war_bot_economy = "off"
    view0, rate0 = args.war_bot_view, args.war_bot_msg_rate
    lo, hi = fewarbots.id_range(args)
    check("the bot block is the top 100 ids below --monster-base (%d-%d)" % (lo, hi),
          (lo, hi) == (args.monster_base - 100, args.monster_base - 1))
    # the harness drives the bots' clock itself: no pump claims the field
    fewarbots._claim = lambda area, now: False
    rewarded = []
    real_update = fecampaign._update_char

    def spy(account, charid, mutate):
        rewarded.append(int(charid))
        return real_update(account, charid, mutate)
    fecampaign._update_char = spy
    with H.quiet():
        feworld.territory_load(args)
        fecampaign.load(args)
    base = {"crystal": 0, "crystal_epoch": 1, "look1": 0,
            "class_levels": {"0": 10}, "king_message": 1}
    alice = H.Peer("Alice", 91, args, nation=3, field=AREA, char=dict(base))
    eve = H.Peer("Eve", 93, args, nation=1, field=AREA, char=dict(base))
    peers = (alice, eve)
    grids = buildings.keep_grids(AREA)
    gw = buildings.grid_to_world

    def at(p, grid, dx=0.0, dz=0.0):
        p.stand(gw(grid[0]) + dx, 20.0, gw(grid[1]) + dz)

    def war(n_label):
        """Declare, sign both up, advance to WAR."""
        row = fecampaign.declare(args, AREA, 3)
        check("%s: a war declared on 17 by nation 3" % n_label, row is not None)
        for p in peers:
            p.tick()
        for p, army in ((alice, 3), (eve, 1)):
            out = p.send(0x2018, struct.pack(">I", army))
            check("%s: %s signs up (0x1020)" % (n_label, p.name),
                  0x1020 in ids(out), ids(out))
        with fecampaign._LOCK:
            fecampaign._STATE[AREA]["until"] = time.time() - 1
        got = {p.charid: p.tick() for p in peers}
        check("%s: War Prep -> At war" % n_label,
              fecampaign.phase_of(AREA) == fecampaign.WAR)
        return got

    def end_war():
        for _ in range(2):          # WAR -> TRUCE -> PEACE
            with fecampaign._LOCK:
                fecampaign._STATE[AREA]["until"] = time.time() - 1
            for p in peers:
                p.tick()

    def bot_ids(box):
        return sorted(o for o in adds(box) if lo <= o <= hi)

    try:
        at(alice, grids["atk"])
        at(eve, grids["def"])
        H.see_each_other(*peers)

        # ------------------------------------------------ --war-bots 0
        say("--war-bots 0: no behaviour change")
        drawn = sum(war("off").values(), [])
        for _ in range(3):
            for p in peers:
                drawn += p.tick()
        r = fecampaign.state_of(AREA)
        check("no bot in the field, no bot sign-up",
              not fewarbots.bots_of(AREA)
              and not any(m.get("bot") for m in r["members"].values()))
        check("the counts are the two players' own (1 v 1)",
              fecampaign.counts_of(AREA, args)[:2] == (1, 1),
              fecampaign.counts_of(AREA, args))
        check("no avatar served under a bot id", bot_ids(drawn) == [],
              bot_ids(drawn))
        end_war()
        check("...and the war ends as before (Peace)",
              fecampaign.phase_of(AREA) == fecampaign.PEACE)
        rewarded[:] = []

        # ------------------------------------------------ --war-bots 3
        say("--war-bots 3: spawn and sign-up at war start")
        args.war_bots = 3
        # the two keeps are ~150 u apart: until the flood-control section
        # below, every screen sees every bot, unthrottled
        args.war_bot_view, args.war_bot_msg_rate = 5000.0, 100000
        first = war("bots")
        bots = fewarbots.bots_of(AREA)
        sides = sorted(b["side"] for b in bots.values())
        check("3 bots a side at war start", sides == ["atk"] * 3 + ["def"] * 3,
              sides)
        check("...under ids in the block, from the top down",
              sorted(bots) == list(range(hi - 5, hi + 1)), sorted(bots))
        r = fecampaign.state_of(AREA)
        rows = [m for m in r["members"].values() if m.get("bot")]
        check("...each signed up: 6 member rows flagged bot, no account",
              len(rows) == 6 and all(m.get("a") is None for m in rows), rows)
        check("...so Attack/Defense count 4/4 (1 player + 3 bots a side)",
              fecampaign.counts_of(AREA, args)[:2] == (4, 4),
              fecampaign.counts_of(AREA, args))
        check("...and --pop-scale counts the players only (1 a side)",
              fecampaign.side_players(args, AREA, "atk") == 1
              and fecampaign.side_players(args, AREA, "def") == 1)
        def behind(b):
            """(how far behind its own keep, away from the enemy keep; how
            much further from the enemy keep than its own keep is)."""
            own = fewarbots.keep_pos(AREA, b["side"])
            foe = fewarbots.keep_pos(AREA, "def" if b["side"] == "atk" else "atk")
            ux, uz = own[0] - foe[0], own[2] - foe[2]
            h = (ux * ux + uz * uz) ** 0.5
            back = ((b["pos"][0] - own[0]) * ux + (b["pos"][2] - own[2]) * uz) / h
            return back, fewarbots._xz(b["pos"], foe) - fewarbots._xz(own, foe)
        rows_ = {b: behind(v) for b, v in bots.items()}
        check("...standing BEHIND their own keep: 15-30 u back from it, away "
              "from the enemy keep (not round it, where the declaring player "
              "built it)",
              fewarbots.keep_pos(AREA, "atk")
              and all(15.0 - 0.01 <= bk <= 30.0 + 0.01 and further > 14.0
                      for bk, further in rows_.values()),
              {b: (round(x, 1), round(y, 1)) for b, (x, y) in rows_.items()})
        check("...no two in the same spot",
              len({(round(v["pos"][0], 1), round(v["pos"][2], 1))
                   for v in bots.values()}) == len(bots))

        say("drawn as players")
        out = first[91] + alice.tick()
        got = adds(out)
        check("Alice's client, on the pump that started the war: six 0x1006 "
              "type-0 avatars under the bot ids",
              bot_ids(out) == sorted(bots)
              and all(got[b] == 0 for b in bots), got)
        check("...and Eve's", bot_ids(first[93] + eve.tick()) == sorted(bots))
        # LIVE 2026-10-23: a player refused at the briefing hung while bots
        # were drawn at them -- only an ENLISTED player is drawn the bots
        ivan = H.Peer("Ivan", 95, args, nation=3, field=AREA, char=dict(base))
        at(ivan, grids["atk"], dx=2.0)
        out = []
        for _ in range(3):
            out += ivan.tick()
        check("a player in the field who has NOT joined the war: no bot record "
              "at all (no 0x1006, no 0x2023, nothing under a bot id)",
              not [(m, u) for m, u, _b in out if lo <= u <= hi],
              [(hex(m), u) for m, u, _b in out if lo <= u <= hi])
        out = ivan.send(0x2018, struct.pack(">I", 3))
        out += ivan.tick() + ivan.tick()
        check("...and once he joins, the bots are drawn for him",
              bot_ids(out) == sorted(bots), bot_ids(out))
        ivan.leave()
        with fecampaign._LOCK:          # the rest of the file counts two players
            r_ = fecampaign._STATE[AREA]
            for k in [k for k, m in r_["members"].items() if m.get("c") == 95]:
                r_["signups"][r_["members"].pop(k)["side"]] -= 1
        cards = {b: v["card"] for b, v in bots.items()}
        check("...each with its war-side byte (atk 0, def 1) and nation",
              all(c["army"] == (0 if bots[b]["side"] == "atk" else 1)
                  and c["force"] == (3 if bots[b]["side"] == "atk" else 1)
                  for b, c in cards.items()), cards)
        check("...and a made-up soldier name",
              all(c["name"].split()[0] in fewarbots.CLASS_NAMES
                  for c in cards.values()))

        say("they move")
        t0 = time.monotonic()
        fewarbots.tick(args, AREA, t0)
        early = fewarbots.bots_of(AREA)
        marching = sorted(b for b, v in early.items() if v["moving"])
        firsts = sorted(b for b, v in early.items() if v["i"] == 0)
        firsts = sorted(b for b, v in early.items() if v["place"] == 0)
        check("STAGGER by squad: on the first tick squad 0's leader of each "
              "side sets off (all 3 a side are squad 0 here)",
              set(firsts) <= set(marching)
              and all(early[b]["squad"] == 0 for b in marching),
              (marching, firsts))
        fewarbots.tick(args, AREA, t0 + 1.0)
        moved = fewarbots.bots_of(AREA)
        check("one tick of 1 s later: every bot walked toward the enemy keep",
              all(fewarbots._xz(moved[b]["pos"], bots[b]["pos"]) > 1.0
                  and moved[b]["moving"] for b in bots))
        # Alice's own clock: her last walk record said B = 100000 just now
        alice.session.update(pres_mv_a=0, pres_mv_b=100000,
                             pres_mv_arr=time.monotonic())
        stamp_t = time.monotonic()
        out = alice.tick()
        mv = [u for m, u, b in out if m == 0x2023 and len(b) == 28]
        check("Alice's client: a 0x2023 action-0 record for each moving bot",
              sorted(mv) == sorted(bots), sorted(mv))
        # LIVE 2026-10-23: a facing-only 0x1006 rebuilds the avatar's models
        # on this client; 83 of them crashed one (FE_Client.dll+0x2A1D9)
        check("...and NO 0x1006 to a bot already on her screen (no turn "
              "records: --war-bot-face off)",
              not [u for m, u, _b in out if m == 0x1006 and u in bots],
              [u for m, u, _b in out if m == 0x1006 and u in bots])
        recs = {u: b for m, u, b in out if m == 0x2023 and len(b) == 28}
        pairs = {u: struct.unpack_from(">h", b, 14)[0] for u, b in recs.items()}
        check("...walk state 0, tail 0x80000, and each bot's OWN speed pair: "
              "its walk / 4.0 x 1000, between 1150 (4.6 u/s) and 1300 (5.2, "
              "a real walker's), never faster",
              all(struct.unpack_from(">H", b, 12)[0] == 0
                  and struct.unpack_from(">I", b, 24)[0] == 0x80000
                  for b in recs.values())
              and all(1150 - 1 <= p <= 1300 for p in pairs.values())
              and all(abs(p - moved[u]["walk"] / 4.0 * 1000.0) <= 1.0
                      for u, p in pairs.items()), pairs)
        lag = int((time.monotonic() - stamp_t) * 1000) + 50
        bs = [struct.unpack_from(">II", b, 4) for b in recs.values()]
        check("...stamped NOW in Alice's clock (100000 + the ms since her "
              "record, <= %d)" % lag,
              all(a == 0 and 100000 <= b <= 100000 + lag for a, b in bs), bs)
        # a REAL walk record (prod 2026-10-23, charid 1, speed pair 1300) is
        # its position plus 0.4 s of its walk (5.2 u/s: 2.08 u on). The 0.4 s
        # is pinned here rather than read off args, so a wider lookahead fails
        look = 5.2 * 0.4

        def where(b):
            return tuple(v * 0.1 for v in struct.unpack_from(">hhh", b, 18))
        errs = {}
        for u, b in recs.items():
            v = moved[u]
            want = fewarbots._step(v["pos"], v["goal"], v["walk"] * 0.4)
            got = where(b)
            errs[u] = (round(fewarbots._xz(got, want), 2),
                       round(fewarbots._xz(got, v["pos"]), 2))
        check("...each at the bot's position at the stamp plus 400 ms of its "
              "own walk (at most %.2f u on, toward where it halts), as a real "
              "record carries -- not the old 4 u target" % look,
              all(e < 0.15 and d <= look + 0.15 for e, d in errs.values()),
              errs)
        out = alice.tick()
        check("...and not again inside %d ms (a real walker's cadence)"
              % args.war_bot_move_ms,
              not [u for m, u, _b in out if m == 0x2023])
        for st in alice.session["wb_seen"].values():
            st["t"] -= args.war_bot_move_ms / 1000.0
        out = alice.tick()
        check("...but again once %d ms have gone by (the old code waited "
              "800 ms and 1.2 u)" % args.war_bot_move_ms,
              sorted(u for m, u, _b in out if m == 0x2023) == sorted(bots),
              ids(out))

        say("a bot hits a player")
        atk_bots = sorted(b for b, v in bots.items() if v["side"] == "atk")
        def_bots = sorted(b for b, v in bots.items() if v["side"] == "def")
        # a MELEE one: a ranged bot backs off from an enemy 1 u away
        hitter = [b for b in atk_bots if bots[b]["cls"] == 0][0]
        hb = fewarbots.bots_of(AREA)[hitter]
        hp_at = hb["pos"]
        eve.stand(hp_at[0] + 1.0, hp_at[1], hp_at[2])
        eve.session["player_hp"] = 1000
        eve.session.pop("player_dead", None)
        with fewarbots._LOCK:           # its squad's bow is in range of her too
            for b in atk_bots:
                if b != hitter:
                    fewarbots._BOTS[AREA][b]["swing_at"] = 1e12
        # Alice's own cast clock, from a short 0x2023 (action 15) she sends
        alice.send(0x2023, struct.pack(">IIIH", 15, 480000, 485000, 1))
        cc_t = time.monotonic()
        alice.tick()
        eve.tick()
        # Eve's own session drives the field here: the hit must still reach
        # her (ext_post skips the poster's own queue unless told otherwise)
        did, _sent = eve.call(fewarbots.tick, args, AREA, time.monotonic())
        check("the driver (Eve's own thread): bot %d swings at Eve" % hitter,
              any(e[0] == "pc" and e[1] == hitter and e[2] == 93 for e in did),
              [e[:3] for e in did])
        got = H.deliver(*peers)
        check("...pvp.hit on Eve's thread: HP 1000 -> %d (the bot's own "
              "damage)" % (1000 - hb["dmg"]),
              eve.session.get("player_hp") == 1000 - hb["dmg"],
              eve.session.get("player_hp"))
        out_a = alice.tick()
        out_e = got[93] + eve.tick()
        skill = fewarbots.BASIC_ATTACK[hb["cls"]]
        sc = feworld.fegamedata.skill_combat(skill)
        dur = sc["windup"] + sc["active"] + sc["recovery"]
        weapon = hb["weapon"]

        def swings(box):
            return [b for m, u, b in box if m == 0x1032 and u == hitter]

        def swing_ok(b, aim):
            if len(b) != 40:
                return False
            a, e, has, me, n, sk, one, tgt, wu = struct.unpack_from(
                ">IIBIIHBII", b, 0)
            xyz = struct.unpack_from(">fff", b, 28)
            return (e - a == dur and has == 1 and me == hitter and n >= 1
                    and sk == skill and one == 1 and tgt == 0 and wu == weapon
                    and all(abs(xyz[i] - aim[i]) < 0.01 for i in range(3)))
        aim = eve.session["cpos"]
        sa, se = swings(out_a), swings(out_e)
        check("the swing is drawn: ONE 0x1032 under bot %d on each screen"
              % hitter, len(sa) == 1 and len(se) == 1,
              (ids(out_a), ids(out_e)))
        check("...in a real basic attack's layout: 40 B, start/end %d ms apart "
              "(skill %d's windup+active+recovery), a position, the bot's own "
              "id, skill %d, no target unit, its weapon uid %d, aimed at Eve"
              % (dur, skill, skill, weapon),
              sa and se and swing_ok(sa[0], aim) and swing_ok(se[0], aim),
              (sa[0].hex() if sa else None))
        # the TURN (static 2026-10-23): a standing bot faces its target with
        # 0x2023 action 1 (place + face, no model work), never a 0x1006
        turns = [b for m, u, b in out_a if m == 0x2023 and u == hitter
                 and len(b) == 36]
        here_ = fewarbots.bots_of(AREA)[hitter]["pos"]

        def turn_ok(b):
            act, ta, tb = struct.unpack_from(">III", b, 0)
            x, y, z, fx, fy, fz = struct.unpack_from(">6f", b, 12)
            dx, dz = aim[0] - here_[0], aim[2] - here_[2]
            h = (dx * dx + dz * dz) ** 0.5 or 1.0
            return (act == 1 and tb > ta and fy == 0.0
                    and abs(x - here_[0]) < 0.01 and abs(z - here_[2]) < 0.01
                    and fx * dx / h + fz * dz / h > 0.95)
        check("...turned to Eve first by 0x2023 ACTION 1 (36 B: place + face "
              "at its own spot, B > A), and no 0x1006 under the bot",
              turns and all(turn_ok(b) for b in turns)
              and not [u for m, u, _b in out_a + out_e if m == 0x1006 and u == hitter],
              ([b.hex() for b in turns], ids(out_a)))
        a0 = struct.unpack_from(">I", sa[0], 0)[0] if sa else -1
        lag = int((time.monotonic() - cc_t) * 1000) + 50
        check("...stamped in Alice's own cast clock (480000 + elapsed, got %d)"
              % a0, 480000 <= a0 <= 480000 + lag, (a0, lag))
        check("...the bot wears its class's starting weapon (worn gear in its "
              "card, uid %d)" % weapon,
              weapon and struct.pack(">I", weapon) in hb["card"]["worn"]
              and hb["card"]["m3"], hb["card"]["m3"])
        check("...and is not drawn twice",
              not swings(alice.tick()) and not swings(eve.tick()))
        did = fewarbots.tick(args, AREA, time.monotonic())
        check("...no second swing inside --war-bot-swing-ms",
              not any(e[0] == "pc" and e[1] == hitter for e in did),
              [e[:3] for e in did])
        args.war_bot_swing_anim = "off"
        with fewarbots._LOCK:
            fewarbots._BOTS[AREA][hitter]["swing_at"] = 0.0
        did, _sent = eve.call(fewarbots.tick, args, AREA, time.monotonic())
        got = H.deliver(*peers)
        check("--war-bot-swing-anim off: the swing lands (pc) and no 0x1032 "
              "is drawn",
              any(e[0] == "pc" and e[1] == hitter for e in did)
              and not swings(alice.tick()) and not swings(got[93] + eve.tick()),
              [e[:3] for e in did])
        args.war_bot_swing_anim = "on"
        at(eve, grids["def"])
        with fewarbots._LOCK:
            for b in atk_bots:
                fewarbots._BOTS[AREA][b]["swing_at"] = 0.0

        say("a player kills a bot")
        victim = def_bots[0]
        vp = fewarbots.bots_of(AREA)[victim]["pos"]
        alice.stand(vp[0], vp[1], vp[2])
        H.see_each_other(*peers)
        before = fewarbots.bots_of(AREA)[victim]["hp"]
        alice.send(0x1031, cast_body(0, target=victim, a=21, b=22))
        after = fewarbots.bots_of(AREA)[victim]["hp"]
        check("Alice's basic attack (cast path) on bot %d: HP %d -> %d"
              % (victim, before, after), 0 <= after < before)
        out = alice.tick()
        check("...her client is shown its new HP (0x2024 on the bot)",
              victim in [u for m, u, _b in out if m == 0x2024], ids(out))
        same = atk_bots[1]
        sp = fewarbots.bots_of(AREA)[same]["hp"]
        alice.send(0xA011, struct.pack(">I", same) + bytes(12)
                   + struct.pack(">HB", 0, 0))
        check("a swing at her own side's bot: nothing",
              fewarbots.bots_of(AREA)[same]["hp"] == sp)
        with fewarbots._LOCK:
            fewarbots._BOTS[AREA][victim]["hp"] = 3
        alice.send(0xA011, struct.pack(">I", victim) + bytes(12)
                   + struct.pack(">HB", 0, 0))
        dead = fewarbots.bots_of(AREA)[victim]
        check("the finishing 0xA011: bot %d is down" % victim,
              dead["hp"] == 0 and dead["dead_until"])
        out_a, out_e = alice.tick(), eve.tick()

        def falls(box):
            """The 0x2024 CONDITION records on the victim with DEAD set."""
            return [b for m, u, b in box if m == 0x2024 and u == victim
                    and len(b) == 12
                    and struct.unpack_from(">I", b, 0)[0] == 0x100000
                    and struct.unpack_from(">I", b, 8)[0] & 0x800000]
        check("...it FALLS on both screens: 0x2024 CONDITION 0x800000 under "
              "its id", falls(out_a) and falls(out_e),
              (ids(out_a), ids(out_e)))
        check("...and is NOT removed yet (--war-bot-corpse-s %.0f)"
              % args.war_bot_corpse_s,
              victim not in dels(out_a) and victim not in dels(out_e),
              (dels(out_a), dels(out_e)))
        check("...the fall is pushed once",
              not falls(alice.tick()) and not falls(eve.tick()))
        with fewarbots._LOCK:
            fewarbots._BOTS[AREA][victim]["died_at"] -= args.war_bot_corpse_s + 0.1
        out_a, out_e = alice.tick(), eve.tick()
        check("...0x1004 for it on both screens once the corpse time is up",
              victim in dels(out_a) and victim in dels(out_e),
              (dels(out_a), dels(out_e)))
        check("...it is no target any more (fepvp's cast path)",
              not fewarbots.near(AREA, vp, 50.0, only=victim))
        fewarbots.tick(args, AREA, dead["dead_until"] + 0.1)
        up = fewarbots.bots_of(AREA)[victim]
        check("after --war-bot-respawn-s it stands at its keep at full HP",
              not up["dead_until"] and up["hp"] == up["hpmax"]
              and up["pos"] == up["home"])
        check("...and is drawn again (0x1006)",
              victim in adds(alice.tick()))
        at(alice, grids["atk"])

        say("bots damage a keep")
        now = dead["dead_until"] + 1.0
        kd = fewarbots.keep_pos(AREA, "def")
        eve.stand(kd[0] + 60.0, 20.0, kd[2])      # out of every bot's aggro
        with fewarbots._LOCK:
            for b in def_bots:          # nobody defends: straight at the keep
                fewarbots._BOTS[AREA][b]["dead_until"] = now + 1000.0
            fewarbots._BOTS[AREA][hitter]["pos"] = (kd[0] + 3.0, kd[1], kd[2])
            fewarbots._BOTS[AREA][hitter]["swing_at"] = 0.0
        hp0 = fecampaign.state_of(AREA)["keeps"]["def"][0]
        dmg0 = {k_: m.get("dmg", 0) for k_, m in
                fecampaign.state_of(AREA)["members"].items()}
        did = fewarbots.tick(args, AREA, now)
        hp1 = fecampaign.state_of(AREA)["keeps"]["def"][0]
        check("bot %d at the castle swings at it: %d -> %d (--war-bot-keep-damage %d)"
              % (hitter, hp0, hp1, args.war_bot_keep_damage),
              hp1 == hp0 - args.war_bot_keep_damage
              and any(e[0] == "keep" and e[1] == hitter for e in did),
              [e[:3] for e in did])
        dmg1 = {k_: m.get("dmg", 0) for k_, m in
                fecampaign.state_of(AREA)["members"].items()}
        check("...nobody's member row is credited", dmg0 == dmg1, (dmg0, dmg1))
        got = H.deliver(*peers)
        obj = args.keep_base
        check("...and the keep's new HP goes to the screens that show it "
              "(campaign.keep)",
              all(obj in [u for m, u, _b in got[p.charid] if m == 0x2024]
                  for p in peers if obj in (p.session.get("keeps") or {}))
              and any(obj in (p.session.get("keeps") or {}) for p in peers),
              {p.charid: ids(got[p.charid]) for p in peers})
        with fewarbots._LOCK:
            for b in def_bots:
                fewarbots._BOTS[AREA][b]["dead_until"] = None

        say("!warbots mid-war")
        had = set(fewarbots.bots_of(AREA))
        alice.tick()
        with H.quiet():
            msg = fewarbots.set_count(args, 5, actor="test")
        check("set to 5: 5 a side in the war under way (%s)" % msg,
              sorted(v["side"] for v in fewarbots.bots_of(AREA).values())
              == ["atk"] * 5 + ["def"] * 5)
        check("...counts 6/6", fecampaign.counts_of(AREA, args)[:2] == (6, 6),
              fecampaign.counts_of(AREA, args))
        out = alice.tick()
        new = set(fewarbots.bots_of(AREA)) - had
        check("...the four new ones drawn", len(new) == 4
              and new == set(bot_ids(out)), (sorted(new), bot_ids(out)))
        with H.quiet():
            fewarbots.set_count(args, 2, actor="test")
        check("set to 2: back to 2 a side, counts 3/3",
              len(fewarbots.bots_of(AREA)) == 4
              and fecampaign.counts_of(AREA, args)[:2] == (3, 3),
              fecampaign.counts_of(AREA, args))
        check("...the six that left are 0x1004 on Alice's screen",
              len([d for d in dels(alice.tick()) if lo <= d <= hi]) == 6)
        handled, _ =alice.call(fewarbots.gm, alice.ctx(), "!warbots 0")
        check("`!warbots 0`: every bot and bot sign-up gone",
              handled and not fewarbots.bots_of(AREA)
              and not any(m.get("bot") for m in
                          fecampaign.state_of(AREA)["members"].values())
              and fecampaign.counts_of(AREA, args)[:2] == (1, 1))
        check("...0x1004 for the last four", len([d for d in dels(alice.tick())
                                                  if lo <= d <= hi]) == 4)
        from world import devtool
        with H.quiet():
            bad = devtool.admin_warbots(args, {"count": "x1"}, "test")
            st, res = devtool.admin_warbots(args, {"count": "3"}, "test")
        check("POST /admin/warbots refuses a count that is not 0-200 (400)",
              bad[0] == 400 and not bad[1]["ok"], bad)
        check("POST /admin/warbots {count: 3}: 3 a side again (%s)" % res.get("msg"),
              st == 200 and res["ok"] and len(fewarbots.bots_of(AREA)) == 6, res)
        check("...and the route is on the tool port's table",
              ("POST", "/admin/warbots") in devtool.admin_routes(args))
        drawn = bot_ids(alice.tick()) + bot_ids(eve.tick())
        check("...drawn on both screens", len(drawn) == 12, drawn)

        # ------------------------------------------------ flood control
        # (the first live run, 2026-10-23: --war-bots 99 = 45 a side, every
        # one on one screen, ~125 messages a second, the client hung)
        say("flood control: the cap")
        with H.quiet():
            msg = fewarbots.set_count(args, 99, actor="test")
        per = sorted(v["side"] for v in fewarbots.bots_of(AREA).values())
        # (operator 2026-10-23: 20 a side ran live with no crash; the
        # temporary 20 ceiling is gone, the war cap less 5 stays)
        cap = fecampaign.war_cap(args) - 5
        check("set_count 99: the war cap less 5 a side (%d), no 20 ceiling"
              % cap,
              per == ["atk"] * cap + ["def"] * cap and args.war_bots == cap,
              (per.count("atk"), per.count("def"), args.war_bots))
        check("...and the reply says so (%s)" % msg,
              "at most the war cap less 5, %d a side" % cap in msg
              and "proven stable" not in msg, msg)
        with H.quiet():
            st_, res_ = devtool.admin_warbots(args, {"count": "99"}, "test")
        check("POST /admin/warbots {count: 99}: %d a side, the same words" % cap,
              st_ == 200 and res_["ok"] and args.war_bots == cap
              and "at most the war cap less 5" in res_["msg"], res_)
        handled, _ = alice.call(fewarbots.gm, alice.ctx(), "!warbots 30")
        check("`!warbots 30`: 30 a side (over the old 20)",
              handled and args.war_bots == 30
              and len(fewarbots.bots_of(AREA)) == 60, args.war_bots)
        args.war_bots = 99
        check("--war-bots 99 as configured: per_side %d" % cap,
              fewarbots.per_side(args) == cap, fewarbots.per_side(args))
        with H.quiet():
            fewarbots.set_count(args, 20, actor="test")
        check("(back to 20 a side for the flood checks below)",
              len(fewarbots.bots_of(AREA)) == 40)

        say("flood control: the view radius and the add stagger")
        args.war_bot_view, args.war_bot_msg_rate = view0, 100000
        ax, ay, az = alice.session["cpos"]
        live_ids = sorted(fewarbots.bots_of(AREA))
        dist = {}
        with fewarbots._LOCK:
            for k_, b in enumerate(live_ids):
                dist[b] = 3.0 + 2.5 * k_
                fewarbots._BOTS[AREA][b].update(
                    pos=(ax + dist[b], ay, az), moving=False, goal=None,
                    swing=None, dead_until=None, died_at=None, wait_until=0.0,
                    face_to=None)
        nearest = sorted(live_ids, key=dist.get)
        alice.session["wb_seen"] = {}           # a fresh screen
        a1 = bot_ids(alice.tick())
        check("the first pump adds %d avatars, the nearest ones (not all 40)"
              % fewarbots.ADDS_PER_PUMP,
              a1 == sorted(nearest[:fewarbots.ADDS_PER_PUMP]), a1)
        drawn_ = set(a1)
        for _ in range(8):
            drawn_ |= set(bot_ids(alice.tick()))
        inview = {b for b in live_ids if dist[b] <= view0}
        check("...then the rest within --war-bot-view (%.0f u), %d of them, "
              "and none beyond" % (view0, len(inview)),
              drawn_ == inview and len(inview) < len(live_ids),
              (sorted(drawn_ - inview), sorted(inview - drawn_)))
        alice.stand(ax - 15.0, ay, az)
        gone = set()
        for _ in range(3):
            gone |= {d for d in dels(alice.tick()) if lo <= d <= hi}
        far = {b for b in inview if dist[b] + 15.0 > view0 + fewarbots.VIEW_HYST}
        mid = {b for b in inview if view0 < dist[b] + 15.0
               <= view0 + fewarbots.VIEW_HYST}
        check("the player steps back 15 u: the bots now beyond view + %.0f are "
              "removed (0x1004)" % fewarbots.VIEW_HYST,
              far and gone == far, (sorted(gone), sorted(far)))
        check("...those between view and view + %.0f stay drawn (no add/remove "
              "flicker at the edge)" % fewarbots.VIEW_HYST,
              mid and mid <= set(alice.session["wb_seen"]), sorted(mid))

        say("flood control: the per-second budget, nearest first")
        alice.stand(ax, ay, az)
        args.war_bot_view = 5000.0
        alice.session["wb_seen"] = {}
        for _ in range(8):
            alice.tick()
        check("(every bot drawn with the budget lifted: 40)",
              len(alice.session["wb_seen"]) == 40, len(alice.session["wb_seen"]))
        args.war_bot_msg_rate = rate0
        with fewarbots._LOCK:
            for b in live_ids:
                v = fewarbots._BOTS[AREA][b]
                v.update(moving=True,
                         goal=(v["pos"][0] + 100.0, v["pos"][1], v["pos"][2]))
        alice.session.pop("wb_sent", None)
        t_ = time.monotonic()
        box = []
        for _ in range(12):
            box += alice.tick()
        took = time.monotonic() - t_
        mine = [u for m, u, _b in box if lo <= u <= hi]
        walked = []
        for m, u, _b in box:
            if m == 0x2023 and u not in walked:
                walked.append(u)
        check("40 bots walking in view, 12 pumps inside one second (%.2f s): "
              "%d bot messages, at most --war-bot-msg-rate %d"
              % (took, len(mine), rate0),
              took < 1.0 and 0 < len(mine) <= rate0, len(mine))
        check("...the walk records went to the nearest bots, nearest first",
              walked and walked == nearest[:len(walked)], (walked, nearest[:8]))
        alice.session.pop("wb_sent", None)
        far_b, near_b = nearest[-1], nearest[0]
        now_ = time.monotonic()
        with fewarbots._LOCK:
            fewarbots._BOTS[AREA][far_b].update(
                hp=0, dead_until=now_ + 100.0, died_at=now_, moving=False)
            fewarbots._BOTS[AREA][near_b]["swing"] = (
                999, (ax, ay, az), now_)
        for st in alice.session["wb_seen"].values():
            st["t"] -= 1.0                      # every walk record due again
        out = alice.tick()
        falls_ = [u for m, u, b in out if m == 0x2024 and len(b) == 12
                  and struct.unpack_from(">I", b, 0)[0] == 0x100000]
        mv = [u for m, u, _b in out if m == 0x2023 and u != far_b]
        check("one pump's share of the budget (%d): the FARTHEST bot's fall goes "
              "first, then walk records of the nearest bots only"
              % len([u for m, u, _b in out if lo <= u <= hi]),
              falls_ == [far_b] and mv and mv == nearest[:len(mv)],
              (falls_, mv))
        check("...and the nearest bot's swing waits behind 38 due walk records",
              not [u for m, u, _b in out if m == 0x1032], ids(out))
        with fewarbots._LOCK:
            fewarbots._BOTS[AREA][far_b].update(dead_until=None, died_at=None,
                                                hp=1000)
            fewarbots._BOTS[AREA][near_b]["swing"] = None

        soldiers(args, alice, eve, lo, hi, view0, rate0, fecampaign, fewarbots)

        # back to the 3 a side the war-end checks expect, everything drawn
        with H.quiet():
            fewarbots.set_count(args, 0, actor="test")
        args.war_bot_view, args.war_bot_msg_rate = 5000.0, 100000
        with H.quiet():
            fewarbots.set_count(args, 3, actor="test")
        at(alice, grids["atk"])
        for _ in range(3):
            alice.tick()
            eve.tick()
        check("(set back to 3 a side: 6 drawn on each screen)",
              len(alice.session["wb_seen"]) == 6
              and len(eve.session["wb_seen"]) == 6,
              (sorted(alice.session["wb_seen"]), sorted(eve.session["wb_seen"])))

        say("war end")
        rewarded[:] = []
        with fecampaign._LOCK:
            fecampaign._STATE[AREA]["until"] = time.time() - 1
        last = {p.charid: p.tick() for p in peers}
        check("At war -> Truce", fecampaign.phase_of(AREA) == fecampaign.TRUCE)
        check("every bot gone", not fewarbots.bots_of(AREA))
        out_a, out_e = last[91] + alice.tick(), last[93] + eve.tick()
        check("...0x1004 for all six on both screens",
              len([d for d in dels(out_a) if lo <= d <= hi]) == 6
              and len([d for d in dels(out_e) if lo <= d <= hi]) == 6,
              (dels(out_a), dels(out_e)))
        check("settle() rewarded the two players and no bot",
              sorted(rewarded) == [91, 93], rewarded)
        bad = [c.get("charid") for r_ in H.STORE.values() for c in r_
               if lo <= int(c.get("charid") or 0) <= hi]
        check("no stored character under a bot id", not bad, bad)
        end_war()
        check("Truce -> Peace", fecampaign.phase_of(AREA) == fecampaign.PEACE)
        check("...and a pump with bots wanted but no war spawns nothing",
              not fewarbots.bots_of(AREA))
        with H.quiet():
            line = fewarbots.set_count(args, 0, actor="test")
        check("set_count 0 says so", "off" in line, line)
    finally:
        fecampaign._update_char = real_update
        fewarbots._players = REAL_PLAYERS[0] or fewarbots._players
        for p in peers:
            p.leave()
    return H.finish("fe_warbots_test")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("FAIL: %s" % e)
        sys.exit(1)
