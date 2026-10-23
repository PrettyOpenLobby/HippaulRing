#!/usr/bin/env python3
"""fe_warbots_economy_test.py -- WAR BOTS phase 3 (fewarbots, 2026-10-23):
bots draw crystals, hand them on, build and summon.

Run from services/:  python ../tools/fe_warbots_economy_test.py

Two real sessions (Alice attacks for nation 3, Eve defends for nation 1)
in a real war with --war-bots and the real defaults, and:
  * a miner walks to the giant crystal on its own half and draws at the
    real client's pace (5 s after it arrives, every 5 s, every 10 s once
    carrying more than 10, 20 a war), and never takes a deposit below
    --war-bot-deposit-reserve;
  * a miner's crystals move to the builder, within the carry cap of 50;
  * the builder puts up a legal building (own territory, outside the
    enemy's, clear of the keep) that every session is served, credited to
    nobody, and no more than --war-bot-buildings a side;
  * the builder summons a Knight at the --summon-cost price, within
    --war-bot-summons and the players' own alive cap (counted together),
    drawn as 0x203D + 0x2024 (or only the HP with --war-bot-summon-anim
    off), and reverts on death;
  * a bot tower's kill, a bot obelisk's drain, a bot Knight's keep swings
    and a bot Giant's felled building never take a keep below half;
  * no player is credited for any of it; no 0x1006 ever goes to a bot
    already on a screen; 45 a side with summons stay inside the budget;
  * --war-bot-economy off: the miners are soldiers again.
HOOKS: a list of fn(args) run once the args are set, so a check can be run
with its feature taken out (each must then FAIL).
"""
import math
import random
import struct
import sys
import time

import fe_harness as H
from fe_harness import check, say

AREA = 17           # nation 1's, next to nation 3's 53
HOOKS = []


def main():
    H.setup()
    import feworld
    import fecampaign
    import fewarbots
    import feunit
    from world import buildings
    args = H.real_args()
    check("phase 3 defaults: economy on, 3 miners, reserve 0.5, 3 buildings, "
          "2 summons, summon drawn",
          (args.war_bot_economy, args.war_bot_miners,
           args.war_bot_deposit_reserve, args.war_bot_buildings,
           args.war_bot_summons, args.war_bot_summon_anim)
          == ("on", 3, 0.5, 3, 2, "on"))
    fewarbots.SEED[0] = 20261023
    args.war_bot_pause = 0.0
    args.war_bot_view, args.war_bot_msg_rate = 5000.0, 100000
    rate0 = 20
    for fn in HOOKS:
        fn(args)
    lo, hi = fewarbots.id_range(args)
    fewarbots._claim = lambda area, now: False     # the test drives the clock
    real_players = fewarbots._players
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
    bad1006 = []
    clock = [time.monotonic() + 100.0]

    def watch(p=None):
        p = p or alice
        seen = {b: st["gen"] for b, st in (p.session.get("wb_seen") or {}).items()}
        gens = {b: v["gen"] for b, v in fewarbots.bots_of(AREA).items()}
        out = p.tick()
        bad1006.extend(u for m, u, _b in out
                       if m == 0x1006 and u in seen and seen[u] == gens.get(u))
        return out

    def tick(dt=0.25, as_=None, draw=True):
        clock[0] += dt
        if as_ is not None:
            did, _ = as_.call(fewarbots.tick, args, AREA, clock[0])
        else:
            with H.quiet():
                did = fewarbots.tick(args, AREA, clock[0])
        if draw:
            watch()
        return did

    def only(keep_ids):
        with fewarbots._LOCK:
            for b, v in fewarbots._BOTS[AREA].items():
                if b in keep_ids:
                    v.update(dead_until=None, died_at=None, retreat=False,
                             pause_until=0.0, wait_until=0.0, target=None,
                             swing_at=0.0, hp=v["hpmax"], moving=False,
                             goal=None)
                else:
                    v.update(dead_until=clock[0] + 1e6, died_at=clock[0] - 1e3,
                             moving=False, goal=None)

    def put(b, pos=None, **kw):
        with fewarbots._LOCK:
            if pos is not None:
                kw["pos"] = tuple(pos)
            fewarbots._BOTS[AREA][b].update(**kw)

    def bot(b):
        return fewarbots.bots_of(AREA)[b]

    def member_scores():
        r = fecampaign.state_of(AREA)
        keys = ("built", "summons", "dmg", "destroyed", "pc_dmg", "kills")
        return {k: tuple(int(m.get(f, 0) or 0) for f in keys)
                for k, m in (r.get("members") or {}).items() if not m.get("bot")}

    def keep_hp(side, v=None):
        with fecampaign._LOCK:
            k = fecampaign._STATE[AREA]["keeps"][side]
            if v is not None:
                k[0] = int(v)
            return int(k[0]), int(k[1])

    try:
        alice.stand(gw(grids["atk"][0]), 20.0, gw(grids["atk"][1]))
        eve.stand(gw(grids["def"][0]), 20.0, gw(grids["def"][1]))
        H.see_each_other(*peers)
        args.war_bots = 20
        row = fecampaign.declare(args, AREA, 3)
        check("a war declared on 17 by nation 3", row is not None)
        for p in peers:
            p.tick()
        for p, army in ((alice, 3), (eve, 1)):
            p.send(0x2018, struct.pack(">I", army))
        with fecampaign._LOCK:
            fecampaign._STATE[AREA]["until"] = time.time() - 1
        for p in peers:
            p.tick()
        check("At war, 20 bots a side",
              fecampaign.phase_of(AREA) == fecampaign.WAR
              and len(fewarbots.bots_of(AREA)) == 40)
        fewarbots._players = lambda *a_: []
        scores0 = member_scores()

        # ---------------------------------------------------- roles
        say("roles: a builder and 3 miners a side")
        bs = fewarbots.bots_of(AREA)
        by_i = {(v["side"], v["i"]): b for b, v in bs.items()}
        miners = {s: sorted(v["i"] for v in bs.values()
                            if v["side"] == s and v["eco"] == "miner")
                  for s in ("atk", "def")}
        builders = {s: [v["i"] for v in bs.values()
                        if v["side"] == s and v["eco"] == "builder"]
                    for s in ("atk", "def")}
        check("each side: builder = bot 1, miners = bots %s" % miners["atk"],
              builders == {"atk": [1], "def": [1]}
              and miners["atk"] == miners["def"] and len(miners["atk"]) == 3,
              (builders, miners))
        M, B = by_i[("atk", miners["atk"][0])], by_i[("atk", 1)]
        D = by_i[("def", 1)]
        Md = by_i[("def", miners["def"][0])]
        deps = fecampaign.deposits(args, AREA)
        cg = fecampaign.crystal_grids(AREA, len(deps), args)
        ka, kd = fewarbots.keep_pos(AREA, "atk"), fewarbots.keep_pos(AREA, "def")
        dpos = [(gw(g[0]), ka[1], gw(g[1])) for g in cg]
        mine_i = min(range(len(dpos)), key=lambda i: fewarbots._xz(dpos[i], ka))
        amt = deps[mine_i][1]
        keep_n = int(math.ceil(args.war_bot_deposit_reserve * amt))

        # ---------------------------------------------------- economy off
        say("--war-bot-economy off: miners are soldiers")
        args.war_bot_economy = "off"
        only({M})
        for _ in range(120):
            tick(0.25)
        m = bot(M)
        check("off: in 30 s the miner draws nothing and does not stop at the "
              "crystal (it marches past it on the enemy keep)",
              m["drawn"] == 0 and m["draw_at"] is None
              and fewarbots._xz(m["pos"], kd)
              < fewarbots._xz(dpos[mine_i], kd) - 10,
              (m["drawn"], m["pos"]))
        args.war_bot_economy = "on"
        put(M, bot(M)["home"])

        # ---------------------------------------------------- the draw
        say("a miner draws at the real client's pace")
        only({M})
        put(M, bot(M)["home"], crystals=0, drawn=0, draw_at=None)
        left0 = fecampaign.deposits(args, AREA)[mine_i][0]
        arrived, draws, near = None, [], None
        for _ in range(1200):
            did = tick(0.25, draw=False)
            m = bot(M)
            if arrived is None and m["draw_at"] is not None:
                arrived = clock[0]
                near = fewarbots._xz(m["pos"], dpos[mine_i])
            draws += [(clock[0], e[3]) for e in did
                      if e[0] == "drew" and e[1] == M and e[3]]
            if len(draws) >= 20 and clock[0] - draws[-1][0] > 12:
                break
        check("the miner walked to the giant crystal on its own half and "
              "stands %.1f u from it (the client draws within 10)"
              % (near or -1), arrived is not None and near is not None
              and near <= 10.0, near)
        gaps = [round(draws[0][0] - arrived, 2)] + [
            round(draws[k][0] - draws[k - 1][0], 2) for k in range(1, len(draws))]
        want = [5.0] + [10.0 if k > 10 else 5.0 for k in range(1, len(draws))]
        check("first crystal 5 s after it stands there, then every 5 s, every "
              "10 s once it carries more than 10",
              len(gaps) == len(want) and all(abs(g - w) <= 0.3
                                             for g, w in zip(gaps, want)),
              gaps)
        m = bot(M)
        left1 = fecampaign.deposits(args, AREA)[mine_i][0]
        check("...20 a war (--crystal-war-max) and no more: it carries %d, "
              "the deposit went %d -> %d" % (m["crystals"], left0, left1),
              len(draws) == 20 and m["drawn"] == 20 and m["crystals"] == 20
              and left1 == left0 - 20, (len(draws), m["drawn"], left1))

        say("...and stops at the players' reserve")
        with fecampaign._LOCK:
            cur = fecampaign._STATE[AREA]["crystals"]
            for i, c in enumerate(cur):
                c[0] = keep_n + 3 if i == mine_i else int(math.ceil(
                    args.war_bot_deposit_reserve * c[1]))
        put(M, crystals=0, drawn=0, draw_at=None, draw_wait=False)
        n_draw = 0
        for _ in range(240):
            n_draw += len([e for e in tick(0.25, draw=False)
                           if e[0] == "drew" and e[3]])
        left2 = fecampaign.deposits(args, AREA)[mine_i][0]
        check("a deposit 3 over its reserve (%d of %d): 3 drawn, then it stays "
              "at %d" % (keep_n, amt, left2), n_draw == 3 and left2 == keep_n,
              (n_draw, left2))
        with fecampaign._LOCK:
            for c in fecampaign._STATE[AREA]["crystals"]:
                c[0] = c[1]

        # ---------------------------------------------------- hand-off
        say("hand-off: miner -> builder")
        args.war_bot_buildings, args.war_bot_summons = 0, 0
        only({M, B})
        hb = bot(B)["home"]
        put(B, hb, crystals=0)
        put(M, (hb[0] + 8.0, hb[1], hb[2]), crystals=12, drawn=20)
        got = []
        for _ in range(40):
            got += [e for e in tick(0.25) if e[0] == "handoff"]
        check("a miner with 12 and its war's 20 drawn walks to the builder: "
              "12 move", got == [("handoff", M, B, 12)]
              and bot(B)["crystals"] == 12 and bot(M)["crystals"] == 0, got)
        put(B, crystals=45)
        put(M, (hb[0] + 8.0, hb[1], hb[2]), crystals=12)
        got = []
        for _ in range(40):
            got += [e for e in tick(0.25) if e[0] == "handoff"]
        check("...the builder carrying 45: only 5 move (the carry cap 50)",
              bot(B)["crystals"] == 50 and bot(M)["crystals"] == 7, got)

        # ---------------------------------------------------- building
        say("the builder puts up a building")
        args.war_bot_buildings = 3
        only({B})
        put(B, hb, crystals=18, eco_wait=0.0)
        got = []
        for _ in range(8):
            got += [e for e in tick(0.25, as_=alice) if e[0] == "built"]
        blds = {o: r_ for o, r_ in fecampaign.buildings_of(AREA).items()
                if r_.get("bot")}
        check("18 crystals: an Arrow Tower goes up for the attackers",
              len(got) == 1 and len(blds) == 1 and bot(B)["crystals"] == 0,
              (got, blds))
        T = got[0][2]
        row = blds[T]
        g = (row["gx"], row["gz"])
        dk = math.hypot(g[0] - grids["atk"][0], g[1] - grids["atk"][1])
        de = math.hypot(g[0] - grids["def"][0], g[1] - grids["def"][1])
        check("...side-owned, no builder, type 0, HP 8000",
              row["side"] == "atk" and row["by"] is None and row["t"] == 0
              and row["hp"] == [8000, 8000], row)
        check("...LEGAL: %.0f cells from its own keep (inside its 38, clear of "
              "the keep by 6+), %.0f from the enemy's (outside its 38)"
              % (dk, de), 6 <= dk <= getattr(args, "base_radius", 38)
              and de > getattr(args, "base_radius", 38)
              and fecampaign.in_influence(args, AREA, "atk", g), (g, dk, de))
        built = (fecampaign.state_of(AREA).get("built") or {}).get("atk") or {}
        check("...counted against the side's own cap (Arrow Tower 1 of 10)",
              built.get("0") == 1, built)
        for p in peers:
            p.tick()
        served = {p.name: [u for m_, u, _b in p.out if m_ == 0x1006 and u == T]
                  for p in peers}
        check("...served to EVERY session (a type-1 0x1006 to Alice and Eve)",
              all(served.values()) and all(
                  T in (p.session.get("built_seen") or {}) for p in peers),
              served)

        say("bot building cap")
        put(B, hb, crystals=200, eco_wait=0.0)
        for _ in range(40):
            tick(0.25, as_=alice)
        blds = {o: r_ for o, r_ in fecampaign.buildings_of(AREA).items()
                if r_.get("bot") and r_["side"] == "atk"}
        types = sorted(r_["t"] for r_ in blds.values())
        check("--war-bot-buildings 3: three (tower, obelisk, tower) and no "
              "more; 18 + 15 + 18 spent", len(blds) == 3 and types == [0, 0, 6]
              and bot(B)["crystals"] == 200 - 33, (types, bot(B)["crystals"]))
        OB = [o for o, r_ in blds.items() if r_["t"] == 6][0]

        # ---------------------------------------------------- floors
        say("bot buildings never take a keep below half")
        _cur, mx = keep_hp("def")
        floor_hp = int(math.ceil(args.war_bot_keep_floor * mx))
        # the tower shoots Eve (it is finished: its build time has passed)
        with fecampaign._LOCK:
            for r_ in fecampaign._STATE[AREA]["buildings"].values():
                r_["t0"] = float(r_["t0"]) - 1000.0
        tpos = (gw(row["gx"]), 20.0, gw(row["gz"]))
        eve.stand(tpos[0] + 5.0, tpos[1], tpos[2])
        eve.session.pop("player_dead", None)
        eve.session["player_hp"] = 1
        keep_hp("def", floor_hp + 2)
        fecampaign._TOWER_CLOCK.clear()
        alice.run(lambda ctx: fecampaign.tower_fire(ctx, args,
                                                    time.monotonic() + 1e4))
        H.deliver(alice, eve)
        hp_ = keep_hp("def")[0]
        check("a bot tower kills Eve: her castle drains only to half "
              "(%d -> %d)" % (floor_hp + 2, hp_),
              eve.session.get("player_dead") and hp_ == floor_hp,
              (eve.session.get("player_dead"), hp_))
        eve.session.pop("player_dead", None)
        eve.session["player_hp"] = 1000
        eve.stand(gw(grids["def"][0]), 20.0, gw(grids["def"][1]))
        keep_hp("def", floor_hp + 1)
        dots0 = args.obelisk_drain_dots
        args.obelisk_drain_dots = 480
        with fecampaign._LOCK:
            fecampaign._STATE[AREA]["ob_tick"] = time.monotonic() - 100.0
        alice.run(lambda ctx: fecampaign.obelisk_drain(ctx, args,
                                                       time.monotonic()))
        args.obelisk_drain_dots = dots0
        hp_ = keep_hp("def")[0]
        check("a bot obelisk's drain stops at half (%d -> %d)"
              % (floor_hp + 1, hp_), hp_ == floor_hp, hp_)

        # ---------------------------------------------------- summons
        say("the builder summons a Knight")
        args.war_bot_buildings, args.war_bot_summons = 0, 1
        only({B})
        put(B, hb, crystals=39, eco_wait=0.0)
        n0 = fecampaign.summon_count(AREA, "atk", 3)
        got = []
        for _ in range(12):
            got += [e for e in tick(0.25, as_=alice) if e[0] == "summoned"]
        check("39 crystals (a Knight is 40): no summon",
              not got and bot(B)["morph"] is None and bot(B)["crystals"] == 39,
              got)
        human = bot(B)
        put(B, crystals=40)
        seen_b = dict((alice.session.get("wb_seen") or {}).get(B) or {})
        clock[0] += 0.25
        did, _ = alice.call(fewarbots.tick, args, AREA, clock[0])
        got = [e for e in did if e[0] == "summoned"]
        out = watch()
        b_ = bot(B)
        check("40: a KNIGHT, HP 2900 (the Knight's high row), paid in full",
              got == [("summoned", B, 3)] and b_["morph"] == 3
              and b_["hpmax"] == 2900 and b_["hp"] == 2900
              and b_["crystals"] == 0, (got, b_["morph"], b_["hpmax"]))
        check("...counted for the side's caps: summon_count %d -> %d, "
              "feunit.alive_count 1" % (n0, fecampaign.summon_count(AREA, "atk", 3)),
              fecampaign.summon_count(AREA, "atk", 3) == n0 + 1
              and feunit.alive_count(AREA, "atk", 3) == 1)
        mine_ = [(m_, b) for m_, u, b in out if u == B]
        check("...drawn on Alice's screen (it was drawn there: %s): 0x203D "
              "form KNIGHT and 0x2024 HP max+current, NOT a 0x1006"
              % bool(seen_b),
              seen_b and (0x203D, struct.pack(">IH", 0x10, 0x8)) in mine_
              and (0x2024, struct.pack(">IIhh", 0x6, 0, 2900, 2900)) in mine_
              and not [1 for m_, _b in mine_ if m_ == 0x1006], mine_)

        say("...hits a keep no lower than half")
        keep_hp("def", floor_hp + 3)
        put(B, (kd[0] + 3.0, kd[1], kd[2]), swing_at=0.0)
        sw = 0
        for _ in range(10):
            put(B, swing_at=0.0)
            sw += len([e for e in tick(0.25) if e[0] == "keep" and e[1] == B])
        hp_ = keep_hp("def")[0]
        check("%d Knight swings (3 a swing) at a castle 3 over half: exactly "
              "half (%d)" % (sw, hp_), sw >= 5 and hp_ == floor_hp, hp_)
        keep_hp("def", mx)

        say("...and reverts on death")
        r_ = fewarbots.hit_bot(args, B, 99999, area=AREA)
        b_ = bot(B)
        check("felled: a soldier again (form none, HP max %d back)"
              % human["hpmax"], r_ and r_["dead"] and b_["morph"] is None
              and b_["hpmax"] == human["hpmax"] and b_["dmg"] == human["dmg"]
              and feunit.alive_count(AREA, "atk", 3) == 0, b_["morph"])
        for _ in range(4):
            tick(1.0)
        put(B, dead_until=clock[0])
        tick(0.25, draw=False)
        out = watch()
        mine_ = [m_ for m_, u, _b in out if u == B]
        check("...stands up again a soldier, drawn as a NEW spawn (0x1004, then "
              "a 0x1006 add of its new generation, no 0x203D)",
              bot(B)["dead_until"] is None and bot(B)["morph"] is None
              and 0x1004 in mine_ and 0x1006 in mine_
              and mine_.index(0x1004) < mine_.index(0x1006)
              and 0x203D not in mine_, mine_)
        put(B, hb, crystals=40, eco_wait=0.0)
        got = []
        for _ in range(12):
            got += [e for e in tick(0.25, as_=alice) if e[0] == "summoned"]
        check("--war-bot-summons 1: no second summon this war",
              not got and bot(B)["morph"] is None and bot(B)["crystals"] == 40,
              got)

        say("the players' alive cap counts bots and players together")
        args.war_bot_summons = 3
        cap0 = args.summon_alive_cap
        args.summon_alive_cap = "KNIGHT=1"
        alice.session["morph"] = {"form": 3, "area": AREA, "side": "atk"}
        got = []
        for _ in range(8):
            got += [e for e in tick(0.25, draw=False) if e[0] == "summoned"]
        alice.session.pop("morph", None)
        check("KNIGHT=1 with Alice summoned as one: the bot may not",
              not got and bot(B)["morph"] is None, got)
        for _ in range(4):
            got += [e for e in tick(0.25, draw=False) if e[0] == "summoned"]
        check("...once she is not, it does (and holds the cap itself)",
              got == [("summoned", B, 3)]
              and feunit.alive_count(AREA, "atk", 3) == 1, got)
        args.summon_alive_cap = cap0
        fewarbots.hit_bot(args, B, 99999, area=AREA)

        say("a bot Giant fells a building; its side's base stops at half")
        only({D})
        put(D, bot(D)["home"], crystals=30, eco_wait=0.0)
        got = []
        for _ in range(8):
            got += [e for e in tick(0.25, as_=alice) if e[0] == "summoned"]
        check("the defenders' builder, the attackers having buildings: a GIANT "
              "(30 crystals)", got == [("summoned", D, 0)]
              and bot(D)["morph"] == 0, got)
        _cur, mxa = keep_hp("atk")
        floor_a = int(math.ceil(args.war_bot_keep_floor * mxa))
        keep_hp("atk", floor_a + 1)
        with fecampaign._LOCK:
            fecampaign._STATE[AREA]["buildings"][str(T)]["hp"][0] = 400
        put(D, (tpos[0] + 3.0, tpos[1], tpos[2]), swing_at=0.0)
        hits = []
        for _ in range(8):
            hits += [e for e in tick(0.25, as_=alice) if e[0] == "bhit"]
        check("it swings at the nearest attacker building (tower %d) and fells "
              "it" % T, hits and hits[0][2] == T
              and T not in fecampaign.buildings_of(AREA), hits)
        check("...the attackers' base pays its dots only to half (%d -> %d)"
              % (floor_a + 1, keep_hp("atk")[0]), keep_hp("atk")[0] == floor_a)
        keep_hp("atk", mxa)
        fewarbots.hit_bot(args, D, 99999, area=AREA)

        say("summon drawn or server-only")
        args.war_bot_summons = 0
        only({Md})
        put(Md, (alice.session["cpos"][0] + 4.0, ka[1], alice.session["cpos"][2]))
        for _ in range(3):
            tick(0.25)
        args.war_bot_summon_anim = "off"
        with fewarbots._LOCK:
            fewarbots._apply_form(fewarbots._BOTS[AREA][Md], 3, 2900)
        out = watch()
        mine_ = [m_ for m_, u, _b in out if u == Md]
        check("--war-bot-summon-anim off: only the HP max+current (0x2024), "
              "no 0x203D", 0x2024 in mine_ and 0x203D not in mine_, mine_)
        args.war_bot_summon_anim = "on"
        fewarbots.hit_bot(args, Md, 99999, area=AREA)

        say("nobody is credited, no 0x1006 to a drawn bot")
        check("no player's member row gained built/summons/damage/kills from "
              "any of it", member_scores() == scores0,
              (scores0, member_scores()))
        check("NO 0x1006 to a bot already on Alice's screen, all along",
              not bad1006, bad1006)

        # ---------------------------------------------------- 45 a side
        say("45 a side, economy on, summons drawn: the budget holds")
        args.war_bot_view, args.war_bot_msg_rate = 50.0, rate0
        with H.quiet():
            fewarbots.set_count(args, 0, actor="test")
            fewarbots.set_count(args, 45, actor="test")
        fewarbots._TICK.pop(AREA, None)
        ax, ay, az = alice.session["cpos"]
        rr = random.Random(3)
        with fewarbots._LOCK:
            for k_, (b, v) in enumerate(sorted(fewarbots._BOTS[AREA].items())):
                v.update(pos=(ax + rr.uniform(-8, 8), ay, az + rr.uniform(-8, 8)),
                         wait_until=0.0)
        alice.session["wb_seen"] = {}
        alice.session.pop("wb_sent", None)
        log, forms = [], 0
        t_end = time.monotonic() + 3.0
        k_ = 0
        while time.monotonic() < t_end:
            k_ += 1
            if k_ == 5:
                with fewarbots._LOCK:
                    for b, v in sorted(fewarbots._BOTS[AREA].items())[:20]:
                        if not v["dead_until"] and v.get("morph") is None:
                            fewarbots._apply_form(v, 3, 2900)
            fewarbots.tick(args, AREA)
            t0 = time.monotonic()
            seen = {b: st["gen"] for b, st in (alice.session.get("wb_seen") or {}).items()}
            gens = {b: v["gen"] for b, v in fewarbots.bots_of(AREA).items()}
            out = alice.tick()
            bad1006.extend(u for m_, u, _b in out
                           if m_ == 0x1006 and u in seen and seen[u] == gens.get(u))
            forms += len([1 for m_, u, _b in out if m_ == 0x203D and lo <= u <= hi])
            log.append((t0, len([1 for m_, u, _b in out if lo <= u <= hi])))
            time.sleep(0.05)
        worst = max(sum(n for t2, n in log if t1 - 0.98 <= t2 <= t1)
                    for t1, _n in log)
        check("90 bots within 8 u, 20 of them summoned mid-run: at most %d bot "
              "messages in any second (worst %d), %d summon forms drawn"
              % (rate0, worst, forms), worst <= rate0 and forms > 0,
              (worst, forms))
        check("...and NO 0x1006 to a bot already on her screen", not bad1006,
              bad1006)
    finally:
        fewarbots._players = real_players
        with H.quiet():
            fewarbots.set_count(args, 0, actor="test")
        for p in peers:
            p.leave()
    return H.finish("fe_warbots_economy_test")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("FAIL: %s" % e)
        sys.exit(1)
