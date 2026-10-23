#!/usr/bin/env python3
"""fe_warbots_monsters_test.py -- MONSTERS AND WAR BOTS FIGHT EACH OTHER
(fewarbots + world/monsters.py, operator 2026-10-23).

Run from services/:  python ../tools/fe_warbots_monsters_test.py

Two real sessions (Alice attacks, Eve defends) in a war on field 17 with
war bots, both shown the field's ONE shared set of monsters, and:
  * a monster inside --monster-aggro of a bot (no player near) walks to it;
  * a monster in reach of a bot swings at it on the field's monster driver:
    the bot's HP drops by the monster's swing, and a monster kills a bot;
  * the monster's swing sends nothing of its own (a hit on a player is only
    that player's 0x2024): the bot's HP is drawn by its 0x2024 in the budget;
  * a bot hit by a monster fights back with its own swing (0x1032 through
    the budget), the monster's HP drops through combat's own HP path;
  * a bot's kill of a monster pays nobody: no EXP, no gold, no chest, no
    ledger, no kill tally -- and its 0x1004 reaches every screen;
  * a bot ignores a monster outside --war-bot-monster-radius that has not
    hit it (no hunting), and turns on the one that did;
  * --war-bot-monsters off: they ignore each other;
  * no 0x1006 to a bot or a monster already on a screen during all of it,
    and never more than --war-bot-msg-rate bot records in a second.
"""
import struct
import sys
import time

import fe_harness as H
from fe_harness import check, ids, say

AREA = 17
MOB = 450               # a monster id (>= --monster-base)
MOB2 = 451


def main():
    H.setup()
    import feworld
    import fecampaign
    import fewarbots
    from world import buildings, combat, drops, monsters, progression
    # soldiers only: the phase 3 economy has its own suite
    args = H.real_args(combat="on", monster_attack="on",
                       war_bot_economy="off")
    check("defaults: --war-bot-monsters on, --war-bot-monster-radius 9 u",
          (args.war_bot_monsters, args.war_bot_monster_radius) == ("on", 9.0),
          (args.war_bot_monsters, args.war_bot_monster_radius))
    fewarbots.SEED[0] = 20261023
    args.war_bot_pause = 0.0
    args.war_bot_view, args.war_bot_msg_rate = 5000.0, 100000
    fewarbots._claim = lambda area, now: False      # the test drives the bots
    real_players = fewarbots._players
    fewarbots._players = lambda *a_: []             # bots fight monsters only
    paid = []
    real = (combat.kill_rewards, drops.drop_on_kill, progression.combat_exp_push,
            drops.kill_gold_credit)
    combat.kill_rewards = lambda *a, **k: paid.append(("kill_rewards", a[5]))
    drops.drop_on_kill = lambda *a, **k: paid.append(("drop",))
    progression.combat_exp_push = lambda *a, **k: paid.append(("exp",))
    drops.kill_gold_credit = lambda *a, **k: paid.append(("gold",))
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
    over = []
    shown = {}          # peer charid -> {bot id: gen} drawn before the pump

    def watch(p):
        """p's pump, noting any 0x1006 to a bot or monster already on its
        screen, and the bot budget."""
        seen = {b: st["gen"] for b, st in (p.session.get("wb_seen") or {}).items()}
        gens = {b: v["gen"] for b, v in fewarbots.bots_of(AREA).items()}
        out = p.tick()
        for m, u, _b in out:
            if m == 0x1006 and ((u in seen and seen[u] == gens.get(u))
                                or u in (MOB, MOB2)):
                bad1006.append((p.name, u))
        sent = len(p.session.get("wb_sent") or ())
        if sent > args.war_bot_msg_rate:
            over.append((p.name, sent))
        return out

    def ticks():
        return watch(alice) + watch(eve)

    try:
        alice.stand(gw(grids["atk"][0]), 20.0, gw(grids["atk"][1]))
        eve.stand(gw(grids["def"][0]), 20.0, gw(grids["def"][1]))
        H.see_each_other(*peers)
        args.war_bots = 2
        row = fecampaign.declare(args, AREA, 3)
        check("a war declared on 17", row is not None)
        for p in peers:
            p.tick()
        for p, army in ((alice, 3), (eve, 1)):
            p.send(0x2018, struct.pack(">I", army))
        with fecampaign._LOCK:
            fecampaign._STATE[AREA]["until"] = time.time() - 1
        for p in peers:
            p.tick()
        bots = fewarbots.bots_of(AREA)
        check("At war, 2 bots a side", fecampaign.phase_of(AREA) == fecampaign.WAR
              and len(bots) == 4, sorted(bots))
        foot = sorted(b for b, v in bots.items() if v["cls"] == 0)
        bid = foot[0]
        others = [b for b in bots if b != bid]
        # the fight happens half-way between the keeps, ~75 u from each player
        ka, kd = fewarbots.keep_pos(AREA, "atk"), fewarbots.keep_pos(AREA, "def")
        P = ((ka[0] + kd[0]) / 2.0, ka[1], (ka[2] + kd[2]) / 2.0)
        clock = [time.monotonic() + 50.0]

        def put_bot(pos, **kw):
            with fewarbots._LOCK:
                v = fewarbots._BOTS[AREA][bid]
                v.update(pos=tuple(pos), dead_until=None, died_at=None,
                         retreat=False, pause_until=0.0, wait_until=0.0,
                         target=None, moving=False, goal=None, mob_foe=None,
                         swing_at=0.0, hp=v["hpmax"])
                v.update(**kw)
                for o in others:            # the rest out of the way, down
                    fewarbots._BOTS[AREA][o].update(dead_until=clock[0] + 1e6,
                                                    died_at=clock[0] - 1e3)

        # ONE shared set of monsters for field 17, shown to both screens
        fm = monsters.field_mobs(AREA)
        fm["built"] = True
        for p in peers:
            p.session.update(mobs_field=AREA, mobs=fm["mobs"], mobs_shown=AREA)

        def put_mob(obj, pos, hp=500, **kw):
            with monsters._MOB_LOCK:
                fm["mobs"][obj] = {
                    "type": 1, "level": 10, "name": "Grub %d" % obj,
                    "type_id": None, "hp": hp, "hpmax": 500, "attack": 0,
                    "defence": 0, "pos": tuple(pos), "home": tuple(pos),
                    "dead": False, "hits": 0}
                fm["mobs"][obj].update(**kw)

        def mob(obj):
            with monsters._MOB_LOCK:
                return dict(fm["mobs"][obj])

        def bot():
            return fewarbots.bots_of(AREA)[bid]

        def bot_tick(dt=0.25):
            """One bot driver tick on Alice's thread (as the field's bot
            driver runs), noting any 0x1006 it sends to a bot or monster."""
            clock[0] += dt
            did, sent = alice.call(fewarbots.tick, args, AREA, clock[0])
            bots_ = fewarbots.bots_of(AREA)
            bad1006.extend(("driver", u) for m, u, _b in sent if m == 0x1006
                           and (u in (MOB, MOB2) or u in bots_))
            return did

        put_bot(P)
        for _ in range(3):
            ticks()
        check("the bot is drawn on both screens",
              bid in alice.session.get("wb_seen", {})
              and bid in eve.session.get("wb_seen", {}))

        # ------------------------------------------------ monsters target bots
        say("a monster targets a bot")
        put_mob(MOB, (P[0] + 12.0, P[1], P[2]))
        _r, out = alice.call(monsters.monster_chase_tick, None, None, None,
                             None, args)
        m1 = mob(MOB)
        check("a monster 12 u from a bot (no player within 70 u) walks to it: "
              "its server copy moved toward the bot",
              fewarbots._xz(m1["pos"], P) < 12.0 - 1.0, m1["pos"])
        check("...and the move went out as its 0x2023 under the monster's id",
              any(m == 0x2023 and u == MOB for m, u, _b in out), ids(out))
        put_mob(MOB, (P[0] + 5.0, P[1], P[2]))
        hp0 = bot()["hp"]
        n0 = len(alice.out)
        hits, _ = alice.call(monsters.monster_attack_bots, args, clock[0])
        swing = combat.damage.monster_swing_damage(args, mob(MOB))[0]
        check("a monster 5 u from the bot (in its reach) swings at it on the "
              "monster driver: HP %d -> %d (its swing %d)"
              % (hp0, bot()["hp"], swing),
              bot()["hp"] == hp0 - swing and hits
              and hits[0][:3] == (MOB, bid, swing), hits)
        check("...nothing is sent for the monster's swing itself (a hit on a "
              "player is only that player's own 0x2024)",
              alice.out[n0:] == [], ids(alice.out[n0:]))
        out = ticks()
        check("...the bot's new HP reaches the screens as its 0x2024 (the "
              "budgeted bot draw)",
              any(m == 0x2024 and u == bid for m, u, _b in out), ids(out))
        hits, _ = alice.call(monsters.monster_attack_bots, args, clock[0] + 0.5)
        check("...one swing per --monster-interval", not hits, hits)
        put_mob(MOB, (P[0] + 5.0, P[1], P[2]))
        alice.stand(P[0] + 5.0, P[1], P[2] + 3.0)
        hits, _ = alice.call(monsters.monster_attack_bots, args, clock[0] + 10)
        check("...a real player in the monster's reach comes first (their own "
              "session's swing), the bot is left alone", not hits, hits)
        alice.stand(gw(grids["atk"][0]), 20.0, gw(grids["atk"][1]))

        say("a monster kills a bot")
        put_bot(P, hp=30)
        put_mob(MOB, (P[0] + 5.0, P[1], P[2]))
        clock[0] += 5.0
        hits, _ = alice.call(monsters.monster_attack_bots, args, clock[0])
        b = bot()
        check("the bot at 30 HP takes the swing and falls (dead_until set)",
              hits and hits[0][3] and b["hp"] == 0 and b["dead_until"], hits)
        out = ticks()
        check("...its fall (0x2024 CONDITION 0x800000) reaches the screens",
              any(m == 0x2024 and u == bid and len(bb) == 12
                  and struct.unpack_from(">I", bb, 8)[0] & 0x800000
                  for m, u, bb in out), ids(out))
        did = bot_tick(b["dead_until"] - clock[0] + 0.1)
        check("...and it stands up again at its keep after its respawn time",
              ("respawn", bid) in did and bot()["pos"] == bot()["home"], did)
        for _ in range(2):
            ticks()             # its new spawn drawn (0x1004 old, 0x1006 new)

        # ------------------------------------------------ bots fight back
        say("a bot fights back")
        args.war_bot_monster_radius = 2.0
        put_bot(P)
        put_mob(MOB, (P[0] + 2.8, P[1], P[2]))
        did = bot_tick(0.0)
        check("(radius 2) a monster 2.8 u off that has NOT hit the bot: ignored",
              not [e for e in did if e[0] == "mob"], did)
        clock[0] += 5.0
        alice.call(monsters.monster_attack_bots, args, clock[0])
        check("(the monster hits the bot)", bot()["hp"] < bot()["hpmax"])
        mhp = mob(MOB)["hp"]
        did = bot_tick()
        dmg = bot()["dmg"]
        check("the bot it hit turns on it although it is outside the radius: "
              "its swing lands, monster HP %d -> %d (the bot's own %d)"
              % (mhp, mob(MOB)["hp"], dmg),
              ("mob", bid, MOB, dmg) in did and mob(MOB)["hp"] == mhp - dmg,
              did)
        args.war_bot_monster_radius = 9.0
        out = ticks()
        sw = [bb for m, u, bb in out if m == 0x1032 and u == bid]
        check("...drawn as its normal swing: one 0x1032 under the bot on each "
              "screen, aimed at the monster",
              len(sw) == 2 and all(abs(struct.unpack_from(">f", x, 28)[0]
                                       - (P[0] + 2.8)) < 0.01 for x in sw),
              ids(out))
        check("...turned to it by 0x2023 ACTION 1 (36 B), never a 0x1006",
              any(m == 0x2023 and u == bid and len(bb) == 36
                  for m, u, bb in out)
              and not [u for m, u, _b in out if m == 0x1006], ids(out))
        check("...no 0x1006 HP record on the monster (it is on the screens)",
              not [u for m, u, _b in out if m == 0x1006 and u == MOB])
        did = bot_tick(0.25)
        check("...one swing per its own swing time",
              not [e for e in did if e[0] == "mob"], did)

        say("a bot fights a monster in its way")
        put_bot(P)
        put_mob(MOB, (P[0] + 7.0, P[1], P[2]))
        did = bot_tick()
        check("a monster 7 u off (inside the 9 u radius, outside a Footman's "
              "reach): the bot walks to it", bot()["moving"]
              and fewarbots._xz(bot()["goal"], (P[0] + 7.0, P[1], P[2])) < 4.0,
              (bot()["moving"], bot()["goal"]))

        say("a bot ignores a monster outside the radius (no hunting)")
        # holding (its march not yet due), so any step is one toward it
        put_bot(P, wait_until=clock[0] + 1e6)
        put_mob(MOB, (P[0] + 12.0, P[1], P[2]))
        did = []
        for _ in range(8):
            did += bot_tick()
        check("a monster 12 u off (inside the bot's war aggro, outside the "
              "radius) that never hit it: no swing, no step toward it",
              not [e for e in did if e[0] == "mob"] and not bot()["moving"]
              and bot()["pos"] == tuple(P), (did, bot()["pos"]))
        put_bot(P, wait_until=clock[0] + 1e6,
                mob_foe=(MOB, clock[0] - 7.0))
        did = bot_tick()
        check("...nor one that hit it longer than %g s ago (12 u is inside "
              "the 1.5 x radius a fresh attacker is followed to)"
              % fewarbots.MOB_FOE_SECS,
              not [e for e in did if e[0] == "mob"] and not bot()["moving"]
              and bot()["pos"] == tuple(P), (did, bot()["pos"]))

        say("a bot kills a monster: nobody is paid")
        put_bot(P)
        put_mob(MOB, (P[0] + 3.0, P[1], P[2]), hp=10,
                ledger={91: 40}, first={91: 0})
        for p in peers:
            p.session["battle"] = {}
        exp0 = {p.charid: p.session.get("exp") for p in peers}
        paid[:] = []
        did = bot_tick()
        m2 = mob(MOB)
        check("the bot's swing takes the monster to 0: dead, HP 0",
              ("mob_kill", bid, MOB) in did and m2["dead"] and m2["hp"] == 0,
              did)
        check("...no reward path ran: no kill_rewards, EXP, gold or chest",
              paid == [], paid)
        check("...the damage ledger is untouched (only the player's earlier 40)",
              m2.get("ledger") == {91: 40} and m2.get("first") == {91: 0},
              m2.get("ledger"))
        got = H.deliver(*peers)
        check("...no kill tally, no EXP on any player",
              all(not p.session["battle"].get("kills") for p in peers)
              and {p.charid: p.session.get("exp") for p in peers} == exp0,
              {p.charid: p.session["battle"] for p in peers})
        check("...its death reaches BOTH screens: 0x1004 under the monster",
              all(MOB in [u for m, u, _b in got[p.charid] if m == 0x1004]
                  for p in peers), {p.charid: ids(got[p.charid]) for p in peers})
        did = bot_tick()
        check("...and the bot goes back to its role (no target, it marches)",
              not [e for e in did if e[0] == "mob"] and bot()["moving"],
              (did, bot()["moving"]))
        args.respawn_secs = 1.0
        with monsters._MOB_LOCK:
            fm["mobs"][MOB]["died_at"] = time.monotonic() - 2.0
        alice.call(monsters.mob_respawn_tick, None, None, None, None, args)
        check("...the field's driver respawns it as usual (full HP)",
              not mob(MOB)["dead"] and mob(MOB)["hp"] == mob(MOB)["hpmax"])
        args.respawn_secs = 0.0
        H.deliver(*peers)       # the respawn's 0x1006 ADD follows its 0x1004
        with monsters._MOB_LOCK:
            fm["mobs"].pop(MOB, None)

        say("monsters do not count for the war")
        r0 = fecampaign.state_of(AREA)
        keeps0 = {s: tuple(r0["keeps"][s]) for s in r0["keeps"]}
        put_bot(P, hp=30)
        put_mob(MOB2, (P[0] + 3.0, P[1], P[2]))
        clock[0] += 5.0
        alice.call(monsters.monster_attack_bots, args, clock[0])
        r1 = fecampaign.state_of(AREA)
        check("a monster's kill of a bot moves no keep and no count",
              {s: tuple(r1["keeps"][s]) for s in r1["keeps"]} == keeps0
              and fecampaign.counts_of(AREA, args)[:2] == (3, 3),
              fecampaign.counts_of(AREA, args))

        say("--war-bot-monsters off")
        args.war_bot_monsters = "off"
        put_bot(P)
        put_mob(MOB2, (P[0] + 3.0, P[1], P[2]))
        clock[0] += 5.0
        hits, _ = alice.call(monsters.monster_attack_bots, args, clock[0])
        did = bot_tick()
        check("off: the monster does not swing at the bot, the bot does not "
              "swing at it", not hits and not [e for e in did if e[0] == "mob"],
              (hits, did))
        args.war_bot_monsters = "on"
        ticks()

        say("the screens")
        check("no 0x1006 to a bot or a monster already on a screen, all along",
              not bad1006, bad1006)
        check("never more than --war-bot-msg-rate bot records in a second "
              "(budget as set: %d)" % args.war_bot_msg_rate, not over, over)
        # a tight budget: the swing waits its turn behind due walk records
        args.war_bot_msg_rate = 8
        for p in peers:
            p.session.pop("wb_sent", None)
        put_bot(P)
        put_mob(MOB2, (P[0] + 3.0, P[1], P[2]))
        dbg = bot_tick()
        box, per = [], []
        for _ in range(6):
            got_ = watch(alice)
            box += got_
            per.append(len([u for m, u, _b in got_ if u == bid]))
        mine = [u for m, u, _b in box if u == bid]
        check("budget 8 a second (2 a pump): the fight's bot records to one "
              "screen (a stop, a turn, a swing) go out at most 2 a pump and 8 "
              "over 6 quick pumps, the swing among them",
              0 < len(mine) <= 8 and max(per) <= 2 and not over
              and 0x1032 in [m for m, u, _b in box if u == bid],
              (per, ids(box), over, dbg))
        args.war_bot_msg_rate = 100000
    finally:
        (combat.kill_rewards, drops.drop_on_kill, progression.combat_exp_push,
         drops.kill_gold_credit) = real
        fewarbots._players = real_players
        with monsters._MOB_LOCK:
            monsters._FIELD_MOBS.pop(AREA, None)
        for p in peers:
            p.leave()
    return H.finish("fe_warbots_monsters_test")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("FAIL: %s" % e)
        sys.exit(1)
