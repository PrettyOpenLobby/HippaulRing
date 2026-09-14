#!/usr/bin/env python3
"""fe_rod_numbers_test.py -- the REAL 2006 numbers, each pinned as the DEFAULT.

Run from the repo root:  python tools/fe_rod_numbers_test.py

2026-09-11, second pass. A researcher found three 2006 fan wikis in the
Wayback Machine (fewiki.com, the Netzawar wiki, the Hordaine war wiki) and a
2007 fewiki revision that labels RoD's numbers 旧仕様; the summons worker
decoded FE_BUILDING_DATA off the client's own parser. This suite pins every
number that REPLACED an invented one, reading back the STATE it changes (a
stored row, a pushed value, a served record) -- and every check FAILS on the
first pass's defaults (auto curve, FEZ SP, shipped-column EXP, exp-gold, inn
level x 10, 0/0 death penalty, no crystal heal, middle+quarter deposits, caps
'4=1', 100/100 buildings, no base damage, the invented Ring bonus, no war
EXP, 60 s prep):

   1. EXP to next level = the RoD table (30 ... 3,200,000)
   2. monster EXP and gold by LEVEL (fewiki `Monster`)
   3. 1 SP per level; the shipped +0xF0 rank cost stays 2
   4. the inn: free to Lv5, 10 / 20 / 50 G
   5. death: 10% of NEXT (stops at 0 into the level), 20% gold, war -3 crystals
   6. crystals: +50 HP heal drains the SAME deposit the draws do; one deposit
      beside each base; the server does not pace draws; 700 per deposit
   7. buildings: caps 25/10/1/1, HP 9,500/8,000/18,000/4,000, radius 38 cells
   8. base damage: an obelisk destroyed = 4 of 480 dots, a war death = 1
   9. war EXP + Rings: the wiki's worked examples, +1 level at most
  10. war prep 65 s
"""
import argparse
import contextlib
import io
import os
import socket as _real_socket
import struct
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
for _p in (_SERVICES, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fegamedata          # noqa: E402
import feworld             # noqa: E402
import fe_crystal_test as C    # noqa: E402  -- its Player/relay harness

OUT = sys.__stdout__
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
FAILED = []


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    say("  %-70s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + repr(detail)) if (detail != "" and not cond)
                          else ""))
    if not cond:
        FAILED.append(label)


# --- the REAL parsed defaults (feworld.main's own args) ---------------------
class _Got(Exception):
    pass


class _SockShim(object):
    def __getattr__(self, k):
        return getattr(_real_socket, k)

    def socket(self, *a, **k):
        raise _Got(sys._getframe(1).f_locals["args"])


def feworld_args():
    saved = (feworld.socket, sys.argv)
    feworld.socket, sys.argv = _SockShim(), ["feworld.py"]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            feworld.main()
    except _Got as g:
        return g.args[0]
    finally:
        feworld.socket, sys.argv = saved
    raise AssertionError("feworld.main() never reached its listener")


def ext_defaults(*mods):
    """{dest: default} of the extension modules' own add_args."""
    ap = argparse.ArgumentParser()
    for m in mods:
        m.add_args(ap)
    return vars(ap.parse_args([]))


# --- a stored character behind the session ----------------------------------
STORE = {}


def stub_store(**char):
    STORE.clear()
    STORE.update(char)
    feworld._load_char_field = lambda a, k, d=None: STORE.get(k, d)
    feworld._store_char_field = lambda a, k, v: (STORE.__setitem__(k, v), True)[1]
    feworld._self_char = lambda a: {"name": "Neo", "sex": 0,
                                    "look1": STORE.get("look1", 0)}
    for k in ("exp", "class_exp", "score", "mobs", "player_dead",
              "player_hp", "battle"):
        feworld._SESSION.pop(k, None)


SENT = []


def _send(conn, outbound, body, mode, be, echo=True, prefix=4):
    unit, mid = struct.unpack_from(">IH", body, 0)
    SENT.append((mid, unit, body[6:]))


def sent(mid):
    return [b for m, _u, b in SENT if m == mid]


# ---------------------------------------------------------------------------
def part_exp(wa):
    say("\n1. EXP to next level = the RoD table (fewiki Guide/メモ, 旧仕様)")
    check("--exp-curve defaults to rod (was auto = 16*L*(L+5), OURS)",
          wa.exp_curve == "rod", wa.exp_curve)
    got = [feworld.exp_need(wa, L) for L in (1, 2, 4, 10, 19, 20, 30, 34, 35,
                                             39, 40)]
    check("NEXT 30/45/100/350 at Lv1/2/4/10, 5200 at Lv19, 7000 at Lv20",
          got[:6] == [30, 45, 100, 350, 5200, 7000], got)
    check("...150,000 at Lv30, 500k/700k at Lv34/35 (the 2006-03 edits), "
          "2.4M at Lv39, 3.2M at Lv40",
          got[6:] == [150000, 500000, 700000, 2400000, 3200000], got)
    check("the table is 40 rows and rises every level",
          len(feworld.ROD_EXP_NEXT) == 40 and all(
              a < b for a, b in zip(feworld.ROD_EXP_NEXT, feworld.ROD_EXP_NEXT[1:])))
    check("`auto` stays selectable (96 at Lv1)",
          feworld.exp_need(argparse.Namespace(exp_curve="auto"), 1) == 96)


def part_kills(wa):
    say("\n2. monster EXP and gold by LEVEL (fewiki Monster 2006-05-26)")
    check("--mob-exp rod and --kill-gold rod by default (were the shipped "
          "column / `exp`)", (wa.mob_exp, wa.kill_gold) == ("rod", "rod"),
          (wa.mob_exp, wa.kill_gold))
    lv = lambda L: {"level": L, "type": 0, "type_id": 1849}
    exps = [feworld.mob_exp(wa, lv(L)) for L in (1, 10, 20, 21, 25, 30, 35, 40)]
    check("EXP = the level to Lv20, then 30, 72, 195, 641, 2258 (L21/25/30/35/40)",
          exps == [1, 10, 20, 30, 72, 195, 641, 2258], exps)
    check("...Lv42-44 3880/5090/6567; Lv41 interpolated (2960), >44 held",
          [feworld.mob_exp(wa, lv(L)) for L in (41, 42, 43, 44, 60)]
          == [2960, 3880, 5090, 6567, 6567])
    golds = [feworld.kill_gold(wa, lv(L)) for L in (1, 2, 10, 20, 30, 40, 48, 60)]
    check("gold 5, 9, 44, 126, 536, 3735, 34426 (L1/2/10/20/30/40/48), held above",
          golds == [5, 9, 44, 126, 536, 3735, 34426, 34426], golds)
    check("--mob-exp shipped = the npc_type column (Duke Orc 823)",
          feworld.mob_exp(argparse.Namespace(mob_exp="shipped"), lv(53)) == 823)
    # a real kill through combat_hit: a L40 monster pays 2258 EXP, 3735 gold
    real = feworld.send
    feworld.send = _send
    try:
        stub_store(look1=0, gold=100, class_levels={"0": 1}, class_exp={"0": 0})
        a = argparse.Namespace(**vars(wa))
        a.combat, a.hit_damage, a.gold, a.kill_reward = "on", 99999, 0, "on"
        # the one-hit kill needs --hit-damage itself, not the 2026-09-12
        # attack x skill power model (fe_combat_test pins that)
        a.player_damage = "flat"
        a.seq_mode, a.world_prefix, a.unit_id = "count", 4, "1"
        # This block pins the rod EXP/GOLD CURVE at a chosen level, so it asks
        # for the level it passes rather than the type's own: type 1849
        # (Duke_Orc) ships level 53, and since 2026-09-12 the shipped level
        # wins by default (--monster-level-source shipped, because a global
        # --monster-level paid a level-1 reward for a level-47 kill). 'knob'
        # is what "I am testing the curve at L40" means.
        a.monster_level_source = "knob"
        del SENT[:]
        feworld.mob_register(a, 77, 0, 0, 40, 0.0, 0.0, 0.0, name="Duke_Orc",
                             type_id=1849)
        body = struct.pack(">IIII", 77, 1, 2, 1001) + struct.pack(">HB", 0, 1)
        feworld.combat_hit(None, None, None, None, a, body)
        check("a L40 kill: +3735 gold in the store (100 -> 3835)",
              STORE.get("gold") == 3835, STORE.get("gold"))
        check("...and +2258 EXP into the level (stored class_exp)",
              STORE.get("class_exp") == {"0": 2258}, STORE.get("class_exp"))
        b = sent(0x1075)[-1] if sent(0x1075) else b""
        check("...pushed as 0x1075 mask 0x6 with NEXT 30 (Lv1, RoD) and 2258",
              b[:4] == struct.pack(">I", 0x6)
              and struct.unpack_from(">I", b, 4)[0] == 30
              and struct.unpack_from(">BBI", b, 8) == (1, 0, 2258), b.hex())
    finally:
        feworld.send = real


def part_sp(wa):
    say("\n3. skill points: 1 per level in RoD; the rank cost is SHIPPED")
    check("--sp-per-level defaults to 1:1-40 (was FEZ 2:1-5,1:6-35,0:36-40)",
          wa.sp_per_level == "1:1-40", wa.sp_per_level)
    sched = feworld.sp_schedule(wa)
    check("SP earned: Lv1 1, Lv5 5, Lv10 10, Lv40 40 (the warrior page's 22+18)",
          [feworld.sp_earned(sched, L) for L in (1, 5, 10, 40)] == [1, 5, 10, 40])
    ranks = [s for s, r in fegamedata.skills().items() if 0 < r.get("sp", 0) < 100]
    check("every learnable rank's shipped [skill+0xF0] is 2 -- the RoD wiki's "
          "2-4/1-2 per rank is NOT in this client (%d ranks)" % len(ranks),
          ranks and all(feworld.skill_sp_cost(s) == 2 for s in ranks))
    check("Sonic Boom ships FIVE ranks here (40..44, all 2) against the wiki's "
          "three (2/1/1) -- a different skill tree",
          [fegamedata.skills()[s]["rank"] for s in range(40, 45)] == [1, 2, 3, 4, 5]
          and [feworld.skill_sp_cost(s) for s in range(40, 45)] == [2] * 5)


def part_inn(wa):
    say("\n4. the inn: free to Lv5, 10 G Lv6-10, 20 G Lv11-20, 50 G Lv21-40")
    check("--inn-fees defaults to rod (was level x --inn-price-per-level 10)",
          wa.inn_fees == "rod", wa.inn_fees)
    fees = {}
    for L in (1, 5, 6, 10, 11, 20, 21, 40):
        stub_store(look1=1, class_levels={"1": L})
        fees[L] = feworld.inn_fee(wa)
    check("fees by level", fees == {1: 0, 5: 0, 6: 10, 10: 10, 11: 20, 20: 20,
                                    21: 50, 40: 50}, fees)
    stub_store(look1=1, class_levels={"1": 12}, gold=500)
    a = argparse.Namespace(**vars(wa))
    a.gold = 1000
    steps = feworld.event_script_for(a, {"name": "Inn_Master", "script": 3105})
    check("the Lv12 innkeeper's script charges 20 (was 120) and heals",
          ("pay", 20) in steps and ("heal",) in steps, steps[:3])


def part_death(wa):
    say("\n5. death: 10% of NEXT, 20% gold; war: -3 crystals")
    check("defaults 10 / 20 / next / 3 (were 0 / 0: 'amount unknown')",
          (wa.death_exp_pct, wa.death_gold_pct, wa.death_exp_of,
           wa.death_war_crystals) == (10.0, 20.0, "next", 3))
    real = feworld.send
    feworld.send = _send
    try:
        stub_store(look1=0, class_levels={"0": 10}, class_exp={"0": 100},
                   exp=5000, gold=1000)
        a = argparse.Namespace(**vars(wa))
        a.gold, a.seq_mode, a.world_prefix, a.unit_id = 0, "count", 4, "1"
        a.campaign = "off"
        for k, v in (("in_field", True), ("field", 17), ("room", -1)):
            feworld._SESSION[k] = v
        lost = feworld.death_penalty(None, None, None, None, a)
        check("a Lv10 hunting death (NEXT 350): -35 EXP into the level "
              "(100 -> 65), -200 gold (1000 -> 800)",
              STORE["class_exp"] == {"0": 65} and STORE["gold"] == 800
              and lost == {"exp": 35, "gold": 200}, (STORE, lost))
    finally:
        feworld.send = real


def part_crystals(args, players, base):
    say("\n6. crystals: the heal drains the SAME deposit; one beside each base")
    alice, bob = players
    import fecampaign
    check("--crystal-heal 50 (was 0 = log only), --crystal-heal-deplete 1, "
          "700 per deposit (shipped; was FFSKY's 532)",
          (args.crystal_heal, args.crystal_heal_deplete, args.war_crystal_hp)
          == (50, 1, 700), (args.crystal_heal, args.war_crystal_hp))
    g = feworld.keep_grids(17)
    cells = fecampaign.crystal_grids(17, 3, args)

    def dist(c, side):
        return ((c[0] - g[side][0]) ** 2 + (c[1] - g[side][1]) ** 2) ** 0.5
    check("deposits: the middle, then one beside the castle and one beside "
          "the keep (within 32 cells; the first pass put them a QUARTER out)",
          dist(cells[1], "def") <= 32 and dist(cells[2], "atk") <= 32
          and min(dist(cells[0], "def"), dist(cells[0], "atk")) > 32,
          (cells, g))
    alice.pump()
    bob.pump()
    obj = base + 1                                  # the castle-side deposit
    alice.session["player_hp"] = 500
    del C.SENT[:]
    alice.send(0x20AD, struct.pack(">I", obj))
    hp = [struct.unpack(">IIh", b) for b in C.got(41, 0x2024, unit=1)]
    check("0x20AD at a war deposit heals +50 (500 -> 550)",
          alice.session.get("player_hp") == 550, alice.session.get("player_hp"))
    check("...and drains that deposit 700 -> 699 (building channel, 0x2024 "
          "bit 0x4 on the crystal)",
          fecampaign.state_of(17)["crystals"][1] == [699, 700]
          and struct.unpack(">IIh", C.got(41, 0x2024, unit=obj)[-1]) == (4, 0, 699),
          fecampaign.state_of(17)["crystals"])
    check("...and NO legacy 0x209D drain on top (no double drain)",
          not C.got(41, 0x209D), C.got(41, 0x209D))
    C.deliver([alice, bob])
    check("...the field sees 699 too (Bob, on his own thread)",
          struct.unpack(">IIh", C.got(42, 0x2024, unit=obj)[-1]) == (4, 0, 699))
    alice.send(0x2081, struct.pack(">I", obj))
    alice.send(0x2081, struct.pack(">I", obj))
    check("two draws back to back both credit -- the CLIENT paces draws "
          "(5 s / 10 s above 10), the server does not; ONE counter: 699 -> 697",
          alice.char.get("crystal") == 2
          and fecampaign.state_of(17)["crystals"][1] == [697, 700],
          (alice.char.get("crystal"), fecampaign.state_of(17)["crystals"]))
    with fecampaign._LOCK:
        fecampaign._STATE[17]["crystals"][1] = [0, 700]
    alice.session["player_hp"] = 500
    del C.SENT[:]
    alice.send(0x20AD, struct.pack(">I", obj))
    check("a SPENT deposit heals nobody (HP stays 500, nothing sent)",
          alice.session.get("player_hp") == 500 and not C.got(41, 0x2024),
          alice.session.get("player_hp"))
    with fecampaign._LOCK:
        fecampaign._STATE[17]["crystals"][1] = [697, 700]


def part_buildings(args, alice):
    say("\n7. buildings: caps, HP, territory radius (Hordaine 建築物 + the "
        "client's FE_BUILDING_DATA)")
    import fecampaign
    import fewar
    check("--build-caps 2006 = Obelisk 25, Arrow Tower 10, War Craft 1, Gate 1 "
          "(shipped +0xe1; was '4=1')",
          args.build_caps == "2006" and all(
              fecampaign.parse_caps("2006").get(t) == n
              for t, n in ((6, 25), (29, 25), (0, 10), (19, 10), (5, 1), (4, 1))))
    check("--build-hp 2006: Obelisk 9500, Arrow Tower 8000, War Craft 18000, "
          "Gate 4000 (was 100/100)",
          [fewar.build_hp(args, t) for t in (6, 0, 5, 4)]
          == [(9500, 9500), (8000, 8000), (18000, 18000), (4000, 4000)])
    check("--build-hp shipped = +0xb0: Obelisk 12000, Gate 18000",
          [fewar.build_hp(argparse.Namespace(build_hp="shipped"), t)
           for t in (6, 4)] == [(12000, 12000), (18000, 18000)])
    check("obelisk radius 38 cells = 95 u = ~1.2 minimap squares (was 40)",
          args.obelisk_radius == 38 and fecampaign.OBELISK_RADIUS_CELLS == 38)
    k = feworld.keep_grids(17)["atk"]
    step = 1 if k[0] < 128 else -1
    code39, _ = alice.act(lambda ctx: fecampaign.build_check(
        args, 17, 6, grid=(k[0] + 39 * step, k[1])))
    code37, _ = alice.act(lambda ctx: fecampaign.build_check(
        args, 17, 6, grid=(k[0] + 37 * step, k[1])))
    check("39 cells from the keep is OUTSIDE its circle (NG 16), 37 inside",
          code39 == 16 and code37 is None, (code39, code37))

    def build(btype, model, gx, gz):
        body = struct.pack(">HHHHHB", btype, model, gx, 0, gz, 0) + \
            struct.pack(">fff", 0.0, 0.0, 0.0)
        del C.SENT[:]
        alice.send(0x2007, body)
        return [(m, b) for c, u, m, b in C.SENT if c == alice.charid]
    spot = (k[0] + 20 * step, k[1])
    with fecampaign._LOCK:
        fecampaign._STATE[17].setdefault("built", {})["atk"] = {"0": 9}
    r = build(0, 1, spot[0], spot[1])
    adds = [b for m, b in r if m == 0x1006]
    check("the 10th arrow tower goes up, served with 8000/8000 HP",
          adds and struct.unpack_from(">ii", adds[0], 16) == (8000, 8000),
          [(hex(m), b.hex()) for m, b in r])
    r = build(0, 1, spot[0], spot[1] + 3)
    check("...the 11th is refused: NG 40 'Build limit reached' (cap 10)",
          r == [(0x100A, struct.pack(">I", 40))], r)
    r = build(6, 8, spot[0], spot[1] - 3)
    adds = [b for m, b in r if m == 0x1006]
    check("an obelisk is served with 9500/9500 HP",
          adds and struct.unpack_from(">ii", adds[0], 16) == (9500, 9500))
    return [o for o, b in alice.session.get("war_buildings", {}).items()
            if b["type"] == 6][-1]


def part_base(args, alice, bob, obelisk):
    say("\n8. base damage: an obelisk destroyed = 4 of 480 dots, a death = 1")
    import fecampaign
    import fewar
    check("--base-dots 2006", args.base_dots == "2006"
          and [fecampaign.base_dots(args, c) for c in (6, 0, 5, 4, "death")]
          == [4, 2, 4, 0, 1])
    before = fecampaign.state_of(17)["keeps"]["atk"][0]
    del C.SENT[:]
    alice.act(lambda ctx: fewar.destroy_building(ctx, obelisk))
    after = fecampaign.state_of(17)["keeps"]["atk"][0]
    check("destroying the attackers' obelisk: their KEEP loses 3000 x 4/480 "
          "= 25 (%d -> %d)" % (before, after), before - after == 25)
    C.deliver([alice, bob])
    kobj = int(getattr(args, "keep_base", 2900)) + 1
    check("...shown to the field on the keep's own channel (Bob gets %d)" % after,
          any(struct.unpack(">IIh", b) == (4, 0, after)
              for b in C.got(42, 0x2024, unit=kobj)))
    hp = alice.act(lambda ctx: fecampaign.base_hit(args, 17, "atk", "death"))
    check("a war death drains the side's base 3000 x 1/480 = 6",
          hp == after - 6, (hp, after))


def part_rewards(args):
    say("\n9. war EXP + Rings (fewiki WAR/報酬 2006-05-22)")
    import fecampaign as F
    check("--war-rewards rod by default (was the invented winner bonus)",
          args.war_rewards == "rod")
    ex = F.war_reward(40, True, 0.5, 1.0,
                      {"pc_dmg": 6599, "kills": 5, "bld_dmg": 29000,
                       "crystals": 50}, rank=3)
    check("the wiki's EXP example: 178770x60%x100% + 178770x(15+10+25+5)% "
          "= 205,585", ex["exp"] == 205585, ex)
    check("...its Ring example: 2 (half the war) + 0 + 1 + 3 + 0 + ★3 = 9",
          ex["rings"] == 9 and ex["grades"] == {"pc_dmg": "B", "kills": "C",
                                                "bld_dmg": "S", "crystals": "D"},
          ex)
    lo = F.war_reward(40, False, 0.5, 1.0, {"bld_dmg": 29000}, rank=3)
    check("a LOSER gets the EXP (win or lose) but only rank Rings (3)",
          lo["rings"] == 3 and lo["exp"] == 107262 + 178770 * 25 // 100, lo)
    check("base EXP: Lv1 50, Lv20 2333 (FFSKY's own example), Lv40 178770",
          [F.war_base_exp(L) for L in (1, 20, 40)] == [50, 2333, 178770])
    # +1 level at most, none at the cap
    c = {"look1": 0, "class_levels": {"0": 1}, "class_exp": {"0": 0}, "exp": 0}
    got = F.apply_war_exp(args, c, 100000)
    check("100,000 war EXP at Lv1: ONE level (Lv2), progress capped below "
          "Lv2's NEXT (44 of 45)", c["class_levels"] == {"0": 2}
          and c["class_exp"] == {"0": 44} and got == (74, 1, 2), (c, got))
    c = {"look1": 0, "class_levels": {"0": 40}, "class_exp": {"0": 5}}
    check("at Lv40 EXP stops (「経験値は入りません」)",
          F.apply_war_exp(args, c, 5000) == (0, 40, 40)
          and c["class_exp"] == {"0": 5})
    # a real settle: Alice won (full war, 3100 keep damage, 20 drawn),
    # Eve lost (joined half-way, never touched the keep)
    now = time.time()
    with F._LOCK:
        F._STATE[5] = F._row_from({
            "phase": F.WAR, "atk": 3, "war_at": now - 600,
            "keeps": {"def": [0, 3000], "atk": [3000, 3000]},
            "drawn": {"acct-Alice|41": 20},
            "members": {"acct-Alice|41": {"a": "acct-Alice", "c": 41,
                                          "side": "atk", "dmg": 3100,
                                          "t": now - 600},
                        "acct-Eve|45": {"a": "acct-Eve", "c": 45,
                                        "side": "def", "dmg": 0,
                                        "t": now - 300}}})
    a0 = C.CHARS[("acct-Alice", 41)]
    e0 = C.CHARS[("acct-Eve", 45)]
    a0.update(look1=0, class_levels={"0": 1}, class_exp={"0": 0}, exp=0,
              ring=0, war_rank=1)
    e0.update(look1=0, class_levels={"0": 10}, class_exp={"0": 0}, exp=0,
              ring=0, war_rank=1)
    F.settle(args, 5, "atk", 1)
    check("Alice (won, 100%, bld 3100 = D, 20 crystals = E): Rings 1 + 3 = 4 "
          "(the invented bonus gave 1+2+3 = 6)", a0["ring"] == 4, a0)
    check("...war EXP 50 x 100% x 100% + 50 x 5% = 52: Lv1 -> Lv2, 22 into it",
          a0["class_levels"] == {"0": 2} and a0["class_exp"] == {"0": 22}
          and a0["exp"] == 52, a0)
    check("Eve (lost, 50%, the keep untouched): rank Rings only (1), EXP 0",
          e0["ring"] == 1 and e0["class_exp"] == {"0": 0}, e0)
    with F._LOCK:
        F._STATE.pop(5, None)


def part_prep():
    say("\n10. war prep 65 s (fewiki WAR/戦争システム: preparation 1 min 5 s)")
    import fecampaign
    d = ext_defaults(fecampaign)
    check("--war-prep-ms defaults to 65000 (was 60000)",
          d["war_prep_ms"] == 65000, d["war_prep_ms"])


def main():
    feworld.load_extensions()
    import fecampaign
    import fewar
    import feunit
    wa = feworld_args()
    for name, fn in (("exp", lambda: part_exp(wa)), ("kills", lambda: part_kills(wa)),
                     ("sp", lambda: part_sp(wa)), ("inn", lambda: part_inn(wa)),
                     ("death", lambda: part_death(wa))):
        try:
            fn()
        except Exception as e:                         # noqa: BLE001
            import traceback
            traceback.print_exc(file=OUT)
            check("part '%s' ran to the end" % name, False, "%s: %s" % (type(e).__name__, e))
    feworld._SESSION.clear()

    # the war parts: fe_crystal_test's Player harness, the crystal/war knobs
    # at the extension modules' OWN parsed defaults
    tmp = os.environ.get("TEMP", ".")
    cf = os.path.join(tmp, "fe_rod_c_%d.json" % os.getpid())
    tf = os.path.join(tmp, "fe_rod_t_%d.json" % os.getpid())
    for p in (cf, tf):
        if os.path.exists(p):
            os.remove(p)
    args = C._args(campaign_file=cf, territory_file=tf, crystal=0)
    d = ext_defaults(fecampaign, fewar, feunit)
    for k in ("war_crystals", "war_crystal_hp", "war_crystal_model",
              "crystal_draw", "crystal_war_max", "crystal_carry_max",
              "crystal_reset", "crystal_deplete", "crystal_place", "crystal_pos",
              "crystal_base_gap", "crystal_heal_deplete", "build_territory",
              "obelisk_radius", "base_radius", "build_caps", "build_hp",
              "base_dots", "war_rewards", "crystal_heal", "crystal_heal_cost",
              "crystal_heal_drain"):
        setattr(args, k, d[k])
    args.player_hp = 1000
    saved = (feworld.send, feworld._load_char_field, feworld._store_char_field,
             fecampaign._update_char)
    feworld.send = C._send
    feworld._load_char_field = lambda a, k, dd=None: C._char().get(k, dd)
    feworld._store_char_field = lambda a, k, v: (C._char().__setitem__(k, v), True)[1]

    def upd(account, charid, mutate):
        c = C.CHARS.get((account, charid))
        if c is None:
            return False
        mutate(c)
        return True
    fecampaign._update_char = upd
    players = []
    try:
        feworld.territory_load(args)
        fecampaign.load(args)
        alice = C.Player("Alice", 41, 3, 17, args)
        bob = C.Player("Bob", 42, 3, 17, args)
        eve = C.Player("Eve", 45, 1, 17, args)
        players = [alice, bob, eve]
        base = int(getattr(args, "keep_base", 2900)) + 10
        fecampaign.declare(args, 17, 3)
        with fecampaign._LOCK:
            fecampaign._STATE[17]["phase"] = fecampaign.WAR
            fecampaign._STATE[17]["until"] = time.time() + 600
            fecampaign._STATE[17]["members"]["acct-Alice|41"] = {
                "a": "acct-Alice", "c": 41, "side": "atk", "dmg": 0}
        for name, fn in (
                ("crystals", lambda: part_crystals(args, (alice, bob), base)),
                ("buildings+base", lambda: part_base(
                    args, alice, bob, part_buildings(args, alice))),
                ("rewards", lambda: part_rewards(args)),
                ("prep", part_prep)):
            try:
                fn()
            except Exception as e:                     # noqa: BLE001
                import traceback
                traceback.print_exc(file=OUT)
                check("part '%s' ran to the end" % name, False,
                      "%s: %s" % (type(e).__name__, e))
    finally:
        (feworld.send, feworld._load_char_field, feworld._store_char_field,
         fecampaign._update_char) = saved
        for p in players:
            p.leave()
        with fecampaign._LOCK:
            fecampaign._STATE.pop(17, None)
        for p in (cf, tf):
            try:
                os.remove(p)
            except OSError:
                pass
    say("\n[fe_rod_numbers_test] %s -- %d failed" % ("OK" if not FAILED else "FAIL",
                                                     len(FAILED)))
    for f in FAILED:
        say("   FAILED: %s" % f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    quiet = "-v" not in sys.argv
    if quiet:
        class _Null:
            def write(self, *a):
                pass

            def flush(self):
                pass
        sys.stdout = _Null()
    sys.exit(main())
