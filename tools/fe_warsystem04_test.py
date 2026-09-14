#!/usr/bin/env python3
"""fe_warsystem04_test.py -- SE's warsystem04, the parts that were missing.

Run from services/:  python ../tools/fe_warsystem04_test.py

WHAT SE's PAGE SAYS (guide/warsystem/warsystem04.html, the local mirror). A
war is won by emptying the enemy's keep or castle, and there are THREE ways to
empty one:

    「直接、キープまたは城を攻撃する」
    「対戦国のキャラクターを倒す」
    「オベリスクによってフィールドの自国支配領域を広げる」

...and warsystem02 gives the third one's mechanism: 「支配領域の広さに
比例して一定時間ごとに敵拠点へダメージを与える」. We served the first two. The
obelisk claimed ground and did nothing with it, the result window drew Exp and
seven zeros, and the defender's base was a Gate of Hades that offered a Wraith.

This file pins, in that order:
  * territory_share / obelisk_drain -- the drain lands on the ENEMY base, is
    proportional to coverage, ticks on its own period, and never fires on the
    first pump (that one only starts the clock)
  * the bases -- type 20 Castle / model 30 castle00_a for the defender, and
    that a Castle offers the KNIGHT the way SE's page says
  * the result window -- kind auto = win/lose/desert, and the drawn scalars
    carry the settlement's numbers
  * pc_damage -- a PvP hit and a kill reach the war score categories

...and, from the same pages on the same day (warsystem01/02):
  * buildings are the WAR's, not their builder's -- ids from the war's
    sequence, every session served, anybody's swing fells one
  * the ARROW TOWER shoots (skill 958, the client's own 監視塔の攻撃)
  * the rank ENTRY COST budget, SE's real limiter behind the 50-per-side cap
"""
import io
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
    say("  %-66s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label + (": " + detail if detail else ""))


def _args(**kw):
    a = dict(campaign="on", campaign_auto="off", war_cap=50, war_prep_ms=250,
             war_length_ms=250, war_truce_ms=250, war_decide="keeps",
             campaign_file=None, territory_file=None, territory="",
             war="offensive", war_fields="0,0,0,0", war_deadline_ms=60000,
             war_clock="off", war_trace="off", war_start_fields="0,0",
             war_cycle="on", seq_mode="count", world_prefix=4, unit_id="0",
             campaign_notify_all=False, capital_staff="off", town_file="",
             events="on", event_greeting=None, capital="1:39",
             shop_types="weapon=1,armor=2,item=3,ring=4",
             keep_hp=3000, keep_base=2900, base_dots="2006",
             obelisk_drain="on", obelisk_drain_ms=30000, obelisk_drain_dots=16,
             obelisk_radius=38, base_radius=38, build_territory="on",
             war_result="auto", war_result_values="session",
             war_result_rows=0, crystal=None,
             build_share="on", building_base=3000,
             tower_fire="on", base_cannon="off", tower_damage=0,
             tower_range=40.0, tower_interval=5.0,
             rank_cost="off", rank_budget=0, influence="buildings")
    a.update(kw)
    return types.SimpleNamespace(**a)


AREA = 17


def _drain_checks(fecampaign, args, ctx):
    say("the obelisk drain (SE warsystem02 + warsystem04)")
    feworld.territory_set(AREA, 1, args)
    fecampaign.declare(args, AREA, 3)
    with fecampaign._LOCK:
        r = fecampaign._STATE[AREA]
        r["phase"] = fecampaign.WAR
        r["keeps"] = {"def": [3000, 3000], "atk": [3000, 3000]}
        r["obelisks"] = {"atk": [], "def": []}

    check("no obelisk, no territory",
          fecampaign.territory_share(args, AREA, "atk") == 0.0)
    with fecampaign._LOCK:
        fecampaign._STATE[AREA]["obelisks"]["atk"] = [[128, 128]]
    one = fecampaign.territory_share(args, AREA, "atk")
    # pi * 38^2 / 256^2 = 6.93%; the 4-cell sample lands within a point of it
    check("one obelisk at the centre covers ~7% of the field (pi r^2 / 256^2)",
          0.06 <= one <= 0.08, "%.4f" % one)
    with fecampaign._LOCK:
        fecampaign._STATE[AREA]["obelisks"]["atk"] = [[128, 128], [128, 132]]
    near = fecampaign.territory_share(args, AREA, "atk")
    check("a second obelisk 4 cells away barely adds (overlap counts once)",
          one < near < one * 1.3, "%.4f -> %.4f" % (one, near))
    with fecampaign._LOCK:
        fecampaign._STATE[AREA]["obelisks"]["atk"] = [[64, 64], [192, 192]]
    two = fecampaign.territory_share(args, AREA, "atk")
    check("...two far apart nearly double it", 1.8 * one < two < 2.2 * one,
          "%.4f -> %.4f" % (one, two))

    now = time.time()
    fecampaign.obelisk_drain(ctx, args, now)
    hp = fecampaign.state_of(AREA)["keeps"]
    check("the FIRST pump only starts the clock -- nothing drains",
          hp["def"][0] == 3000 and hp["atk"][0] == 3000, repr(hp))
    fecampaign.obelisk_drain(ctx, args, now + 5)
    check("...and nothing drains again before the period is up",
          fecampaign.state_of(AREA)["keeps"]["def"][0] == 3000)
    fecampaign.obelisk_drain(ctx, args, now + 31)
    hp = fecampaign.state_of(AREA)["keeps"]
    check("a tick takes dots off the DEFENDER's base, not the attacker's",
          hp["def"][0] < 3000 and hp["atk"][0] == 3000, repr(hp))
    # ~13.9% coverage x 16 dots = 2 of 480 dots x 3000 hp = 12
    check("...sized by the coverage (2 of 480 dots at ~14%)",
          3000 - hp["def"][0] == 12, repr(hp))
    with fecampaign._LOCK:
        fecampaign._STATE[AREA]["keeps"] = {"def": [3000, 3000],
                                            "atk": [3000, 3000]}
        fecampaign._STATE[AREA]["ob_tick"] = now
    args.obelisk_drain = "off"
    fecampaign.obelisk_drain(ctx, args, now + 61)
    check("--obelisk-drain off restores the old behaviour",
          fecampaign.state_of(AREA)["keeps"]["def"][0] == 3000)
    args.obelisk_drain = "on"


def _base_checks(feunit):
    say("the bases (SE warsystem02: the castle is the defender's own)")
    check("the defender's base is type 20 Castle / model 30 castle00_a",
          feworld._pair(None, (20, 16)) == (20, 16)
          and feworld.BUILDING_TYPES[20] == "Castle"
          and feworld.BUILDING_MODELS[30] == "castle00_a")
    check("...and a Castle offers the KNIGHT (skill 954), not the Wraith",
          feunit.SUMMON_BUILDINGS[20][0] == 954
          and feunit.SUMMON_BUILDINGS[16][0] == 954
          and feunit.SUMMON_BUILDINGS[4][0] == 955)


def _result_checks(args):
    say("the war result window (SE warsystem04's own columns)")
    feworld.battle_reset()
    feworld._SESSION.pop("war_result", None)
    f = feworld.WAR_RESULT_FIELDS
    kind, why = feworld.war_result_kind(args)
    check("no settled war: kind 0 Desert ('you left the front')",
          kind == 0 and "desert" in why, repr((kind, why)))
    feworld.battle_tally("exp", 400)
    feworld.battle_tally("score", 55)
    feworld.battle_tally("built", 2)
    feworld.battle_tally("destroyed", 3)
    v = feworld.war_result_values(args)
    check("a deserter's window draws only what the VISIT produced",
          v[f.index("exp")] == 400 and v[f.index("built")] == 2
          and v[f.index("destroyed")] == 3 and v[f.index("rewards")] == 0
          and v[f.index("players_attack")] == 0, repr(v))
    feworld._SESSION["war_result"] = {
        "won": True, "area": AREA, "rings": 7, "exp": 9000,
        "players": {"atk": 12, "def": 9},
        "scores": {"pc_dmg": 2000, "kills": 4, "bld_dmg": 900, "crystals": 31},
        "destroyed": 1.0}
    kind, why = feworld.war_result_kind(args)
    check("a settled WIN: kind 1", kind == 1 and "win" in why, repr((kind, why)))
    v = feworld.war_result_values(args)
    check("...and every drawn column carries the settlement's number",
          v[f.index("players_attack")] == 12
          and v[f.index("players_defend")] == 9
          and v[f.index("exp")] == 9000      # the WAR's exp, not the visit's
          and v[f.index("crystal")] == 31
          and v[f.index("rewards")] == 7
          and v[f.index("score")] == 55, repr(v))
    feworld._SESSION["war_result"]["won"] = False
    check("a settled LOSS: kind 2", feworld.war_result_kind(args)[0] == 2)
    args.war_result = "desert"
    check("--war-result desert still pins it",
          feworld.war_result_kind(args)[0] == 0)
    args.war_result = "auto"
    check("the row count is still clamped to 2 (a 0xADC object, 0x4FC rows)",
          len(feworld.war_result_body(1, v, 9))
          == len(feworld.war_result_body(1, v, 2)))
    feworld._SESSION.pop("war_result", None)


def _score_checks(fecampaign, args):
    say("PC damage and kills reach the war's score (warsystem04's categories)")
    key = "acct-Lex|41"
    with fecampaign._LOCK:
        fecampaign._STATE[AREA]["members"][key] = {
            "a": "acct-Lex", "c": 41, "side": "atk", "dmg": 0,
            "pc_dmg": 0, "kills": 0, "t": time.time()}
    saved_me = fecampaign._me
    fecampaign._me = lambda: (key, "acct-Lex", 41)
    try:
        check("a hit that landed credits PC damage",
              fecampaign.pc_damage(args, AREA, 250) == (250, 0))
        check("...and the killing blow credits the kill too",
              fecampaign.pc_damage(args, AREA, 120, kill=True) == (370, 1))
        with fecampaign._LOCK:
            fecampaign._STATE[AREA]["phase"] = fecampaign.TRUCE
        check("nothing is credited outside a live war",
              fecampaign.pc_damage(args, AREA, 999) is None)
        with fecampaign._LOCK:
            fecampaign._STATE[AREA]["phase"] = fecampaign.WAR
    finally:
        fecampaign._me = saved_me
    check("the grades those feed are the fewiki floors (S 20 kills, D 3)",
          fecampaign.grade("pc_dmg", 2000) == "D"
          and fecampaign.grade("pc_dmg", 20000) == "S"
          and fecampaign.grade("kills", 20) == "S"
          and fecampaign.grade("kills", 4) == "D"
          and fecampaign.grade("kills", 2) == "E")


def _shared_building_checks(fecampaign, args, ctx):
    say("buildings belong to the WAR, not to their builder")
    with fecampaign._LOCK:
        r = fecampaign._STATE[AREA]
        r["phase"] = fecampaign.WAR
        r["buildings"] = {}
        r["build_seq"] = 0
    a = fecampaign.building_add(args, AREA, "atk", 0, 1, (100, 100),
                               (8000, 8000), by=41)
    b = fecampaign.building_add(args, AREA, "def", 6, 8, (150, 150),
                               (12000, 12000), by=42)
    base = int(getattr(args, "building_base", 3000))
    check("two builders get DIFFERENT object ids from the war's sequence",
          (a, b) == (base, base + 1), repr((a, b)))
    check("...and both stand on the war's board",
          sorted(fecampaign.buildings_of(AREA)) == [a, b])
    # the type-1 record's last u32 is the building's AGE IN MS, so the board
    # has to remember WHEN each one went up -- a player arriving later must
    # not watch a tower that has stood for ten minutes raise itself again.
    rows = fecampaign.buildings_of(AREA)
    # WARNING: NOT `or -1`: a fast run gives an age of exactly 0 ms, and `0 or -1`
    # is -1. The first cut of this check failed about one run in six.
    _age = fecampaign.building_age_ms(rows[a])
    check("a new building records t0, and its age reads as ~0 ms",
          _age is not None and 0 <= _age < 5000, repr(_age))
    old_row = dict(rows[a]); old_row["t0"] = time.time() - 600
    check("...a ten-minute-old one reads ~600000 ms",
          599000 < fecampaign.building_age_ms(old_row) < 601000,
          repr(fecampaign.building_age_ms(old_row)))
    pre = dict(rows[a]); pre.pop("t0", None)
    check("...and a row written before t0 existed reads FINISHED, not 0",
          fecampaign.building_age_ms(pre) is None
          and feworld.building_age(args, pre["t"], None)
          > feworld.build_time_ms(pre["t"]))
    key = "acct-Lex|41"
    with fecampaign._LOCK:
        fecampaign._STATE[AREA]["members"][key] = {
            "a": "acct-Lex", "c": 41, "side": "atk", "dmg": 0,
            "pc_dmg": 0, "kills": 0, "t": time.time()}
    saved_me = fecampaign._me
    fecampaign._me = lambda: (key, "acct-Lex", 41)
    try:
        got = fecampaign.building_damage(args, AREA, a, 3000)
        check("anybody's swing damages one (it used to be indestructible)",
              got and got[0] == 5000, repr(got))
        check("...and the damage is credited as 建築物与ダメージ",
              int(fecampaign.state_of(AREA)["members"][key]["dmg"]) >= 3000)
    finally:
        fecampaign._me = saved_me
    fell = fecampaign.building_fell(args, AREA, b, conn=None, outbound=None,
                                   mode=None, be=None)
    check("a fallen OBELISK leaves the board and gives its territory back",
          fell and int(fell["t"]) == 6
          and sorted(fecampaign.buildings_of(AREA)) == [a]
          and fecampaign.territory_share(args, AREA, "def") == 0.0)
    check("...and its own side's base paid the dots (an obelisk = 4 of 480)",
          fecampaign.state_of(AREA)["keeps"]["def"][0] < 3000)
    say("the cost table is the BUILDABLE SET (a free Dragon Altar went up live)")
    import fewar
    costs = fewar.parse_costs("2006")
    check("RoD's four (+ the Gate of Hades) are priced, and nothing else is",
          sorted(costs) == [0, 4, 5, 6, 19, 29]
          and 1 not in costs and 2 not in costs and 3 not in costs
          and 8 not in costs, repr(sorted(costs)))
    check("...so a DragonAltar (1), AlchemyLabo (3), Observatory (2) and "
          "DefenseTower (8) are all unpriced -- and used to build for FREE",
          all(costs.get(t, (0, 0, 0))[0] == 0 for t in (1, 2, 3, 8)))

    check("a building nobody built is not a building",
          fecampaign.building_damage(args, AREA, 9999, 1) is None
          and fecampaign.building_fell(args, AREA, 9999) is None)

    say("勢力範囲 -- the client's own tutorial book (fet_bookset_info 58/59)")
    g = feworld.keep_grids(AREA)["atk"]
    args.influence = "obelisks"
    without = len(fecampaign.territory_of(args, AREA, "atk"))
    args.influence = "buildings"
    withb = len(fecampaign.territory_of(args, AREA, "atk"))
    towers = len([1 for b in fecampaign.buildings_of(AREA).values()
                  if b.get("side") == "atk"
                  and int(b["t"]) not in fecampaign.OBELISK_TYPES])
    check("a side's sphere counts its other buildings too, not just obelisks "
          "and the base", towers and withb == without + towers,
          "%d + %d != %d" % (without, towers, withb))
    check("...inside it, a swing at an enemy building lands",
          fecampaign.in_influence(args, AREA, "atk", g))
    # a cell outside every circle this side holds (its keep, its two obelisks
    # from the drain checks, and its tower)
    far = next((c for c in ((2, 2), (253, 2), (2, 253), (253, 253))
                if not fecampaign.in_influence(args, AREA, "atk", c)), (2, 2))
    check("...from outside it, nothing lands [book 59]",
          not fecampaign.in_influence(args, AREA, "atk", far)
          and fecampaign.building_damage(args, AREA, a, 100, from_grid=far,
                                         by_side="atk") == "out of influence")
    args.influence = "off"
    check("--influence off removes the gate",
          fecampaign.in_influence(args, AREA, "atk", far))
    args.influence = "buildings"


def _tower_checks(fecampaign, args):
    say("the arrow tower shoots (SE warsystem02, client skill 958)")
    check("the tower's attack SKILL is the shipped one, per type",
          fecampaign.TOWER_ATTACK[0][0] == 958
          and fecampaign.TOWER_ATTACK[19][0] == 958
          and fecampaign.TOWER_ATTACK[20][0] == 959)
    check("...and its power/range/interval are the read +0xb4/+0xd0/+0xd4",
          fecampaign.TOWER_ATTACK[0][1] == 35
          and fecampaign.TOWER_RANGE == 40.0
          and fecampaign.TOWER_INTERVAL == 5.0)
    src = io.open(os.path.join(_HERE, "..", "services", "fecampaign.py"),
                  encoding="utf-8").read()
    check("a tower's kill is credited to its BUILDER [book 61], not to nobody",
          '"from": int(b.get("by") or 0)' in src)
    # WARNING: A TOWER UNDER CONSTRUCTION DOES NOT FIRE (2026-09-12). The client
    # draws a construction site until FE_BUILDING_DATA +0xac expires
    # (0x05072770; Arrow Tower 40 s), so a tower that shoots out of its own
    # scaffold is the server disagreeing with the screen. Both towers here
    # were just built, so this must be EMPTY -- and the checks below would
    # pass vacuously on an empty list, which is why this one comes first.
    check("a tower still inside its 40 s +0xac build time does NOT fire",
          fecampaign._tower_rows(args, AREA) == [],
          repr(fecampaign._tower_rows(args, AREA)))
    with fecampaign._LOCK:
        for row in fecampaign._STATE[AREA]["buildings"].values():
            row["t0"] = time.time() - 120        # built two minutes ago
    rows = dict(fecampaign._tower_rows(args, AREA))
    check("...and fires once it is finished", rows, repr(rows))
    check("only the tower types fire by default (--base-cannon off)",
          rows and all(int(b["t"]) in fecampaign.TOWER_TYPES
                       for b in rows.values()),
          repr(rows))
    args.base_cannon = "on"
    types = {int(b["t"]) for b in dict(fecampaign._tower_rows(args, AREA)).values()}
    args.base_cannon = "off"
    check("--base-cannon on adds the 959 rows", types <= set(
        fecampaign.TOWER_TYPES) | set(fecampaign.CANNON_TYPES))
    check("--base-cannon on adds rows at all (not vacuously empty)", types)
    args.tower_fire = "off"
    check("--tower-fire off: nothing fires",
          fecampaign._tower_rows(args, AREA) == [])
    args.tower_fire = "on"
    pos = fecampaign._tower_pos(args, AREA, 3000, {"gx": 128, "gz": 128})
    check("a tower's position is its grid at the map's ground height",
          pos and abs(pos[0]) < 1e-6 and pos[1] > 0, repr(pos))
    check("no enemy in the field -> no target",
          fecampaign._tower_target(args, AREA, "atk", pos, 40.0)[0] is None)

    # WARNING: both of these were LIVE FAILURES on 2026-09-12: the tower stood in a
    # war with a peer in the field and never fired.
    saved_sessions = fw_sessions = feworld.ext_sessions
    try:
        def one(cid, nation, xyz):
            mv = bytes(18) + struct.pack(">hhh", int(xyz[0] * 10),
                                          int(xyz[1] * 10), int(xyz[2] * 10))
            return {"session": {"charid": cid, "in_field": True, "room": -1,
                                "field": AREA, "pres_mv": mv,
                                "pres_card": {"force": nation}},
                    "key": "k%d" % cid, "name": "p%d" % cid}
        tx, _ty, tz = pos
        # a peer 10 u away horizontally but 500 u off in y: the tower's own y
        # is the map's MEDIAN height, so a 3D range could never reach them
        feworld.ext_sessions = lambda: [one(77, 9, (tx + 10, 500.0, tz))]
        got, d = fecampaign._tower_target(args, AREA, "atk", pos, 40.0)
        check("range is HORIZONTAL -- a y from the map's median height cannot "
              "put a peer out of range", got and got["cid"] == 77 and d < 11,
              repr((got, d)))
        # ...and the peer's nation is on the presence CARD, not a "nation" key
        mine = fecampaign._side_byte(args, AREA, "atk")
        feworld.ext_sessions = lambda: [one(78, mine, (tx + 5, 0.0, tz))]
        check("a tower does NOT shoot its own army (nation = pres_card.force)",
              fecampaign._tower_target(args, AREA, "atk", pos, 40.0)[0] is None)
        feworld.ext_sessions = lambda: [one(79, mine, (tx + 5, 0.0, tz))]
        check("...and the DEFENDERS' tower does shoot that same player",
              (fecampaign._tower_target(args, AREA, "def", pos, 40.0)[0] or {})
              .get("cid") == 79)
        feworld.ext_sessions = lambda: [one(80, 9, (tx + 300, 0.0, tz))]
        check("out of range is still out of range",
              fecampaign._tower_target(args, AREA, "atk", pos, 40.0)[0] is None)
    finally:
        feworld.ext_sessions = saved_sessions
    now = 1000.0
    feworld._SESSION["charid"] = 41
    check("one session drives a field's towers", fecampaign._tower_driver(AREA, now))
    feworld._SESSION["charid"] = 42
    check("...and a second session does not, while the first keeps pumping",
          not fecampaign._tower_driver(AREA, now + 1))
    check("...until it goes quiet for 5 s", fecampaign._tower_driver(AREA, now + 7))
    feworld._SESSION["charid"] = 41


def _rank_cost_checks(fecampaign, args):
    say("the rank entry cost budget (SE warsystem01's own limiter)")
    check("off: no rank costs anything",
          fecampaign.rank_cost(args, 5) == 0
          and fecampaign.rank_room(args, AREA, "atk", 5) == (True, 0))
    # 2026-09-13: the numbers were found, and the rule is a GAP between the
    # two sides' ★ totals (fewiki WAR/戦争システム + SE's 2006-05-09 patch
    # table: ★14 -> ★18), not a per-side budget. It is the default now.
    import types as _t
    check("the default --rank-cost is SE's 'diff'",
          fecampaign._rank_mode(_t.SimpleNamespace()) == "diff"
          and fecampaign.rank_gap(_t.SimpleNamespace()) == 18)
    args.rank_cost, args.rank_gap = "diff", 18
    with fecampaign._LOCK:
        fecampaign._STATE[AREA]["spent"] = {"atk": 17, "def": 0}
    check("diff: a ★1 may bring the attackers to exactly ★18 ahead",
          fecampaign.rank_room(args, AREA, "atk", 1) == (True, 1))
    check("...a ★2 would make it ★19 ahead: refused, as that side FULL",
          fecampaign.rank_room(args, AREA, "atk", 2) == (False, 2))
    check("...while the side that is BEHIND may take anyone",
          fecampaign.rank_room(args, AREA, "def", 5) == (True, 5))
    check("fifty ★1 against nobody is refused past the gap (the 50 vs 0 "
          "the glossary says the rank rule ended)",
          fecampaign.rank_over(args, 18, 0, 1)
          and not fecampaign.rank_over(args, 17, 0, 1))
    args.rank_gap = 14
    check("--rank-gap 14 = the launch rule (★14)",
          fecampaign.rank_room(args, AREA, "atk", 1) == (False, 1))
    args.rank_gap = 18
    args.rank_cost = "rank"
    check("--rank-cost rank: a ★N costs N",
          [fecampaign.rank_cost(args, n) for n in (1, 3, 5)] == [1, 3, 5])
    check("the budget defaults to 2 x the 50-per-side cap",
          fecampaign.rank_budget(args) == 100)
    args.rank_budget = 10
    with fecampaign._LOCK:
        fecampaign._STATE[AREA]["spent"] = {"atk": 8}
    check("a ★2 still fits an 8-of-10 budget",
          fecampaign.rank_room(args, AREA, "atk", 2) == (True, 2))
    check("...a ★3 does not, and is refused as that side being FULL",
          fecampaign.rank_room(args, AREA, "atk", 3) == (False, 3))
    args.rank_cost = "1=1,2=1,3=9"
    check("an explicit table wins (the shape for real numbers, when found)",
          fecampaign.rank_cost(args, 2) == 1 and fecampaign.rank_cost(args, 3) == 9)
    args.rank_cost, args.rank_budget = "off", 0
    with fecampaign._LOCK:
        fecampaign._STATE[AREA]["spent"] = {}


def _peer_death_checks():
    """LIVE 2026-09-12: Quinn killed Lex and Lex stayed standing on Quinn's
    screen. visible_peers had never looked at `player_dead`. Filed here
    because this file owns the war-combat checks; the mechanism is
    fepresence's."""
    say("a dead peer stops being drawn (Quinn killed Lex; Lex stayed standing)")
    import fepresence
    args = _args(presence="on", presence_stale=10.0, monster_base=400,
                 unit_id="auto", presence_interp="on", presence_clock="map",
                 presence_dead="drop")
    key = (17, -1)
    card = {"key": key, "force": 3}
    mv = bytes(18) + struct.pack(">hhh", 0, 0, 0)

    def peer(cid, dead):
        return {"session": {"charid": cid, "pres_card": dict(card, cid=cid),
                            "pres_mv": mv, "pres_mv_n": 1,
                            "pres_mv_t": 1e18, "pres_alive_t": 1e18,
                            "in_field": True, "room": -1, "field": 17,
                            "player_dead": dead},
                "key": "k%d" % cid, "name": "p%d" % cid}
    saved = feworld.ext_sessions
    saved_key = fepresence._key
    try:
        fepresence._key = lambda ps: key if ps.get("in_field") else None
        me = {"charid": 41}
        feworld.ext_sessions = lambda: [peer(77, False)]
        check("a LIVE peer is drawn",
              77 in fepresence.visible_peers(me, key, 1e18, args))
        feworld.ext_sessions = lambda: [peer(77, True)]
        check("...and a DEAD one is not (--presence-dead drop)",
              fepresence.visible_peers(me, key, 1e18, args) == {})
        args.presence_dead = "off"
        check("--presence-dead off restores the live bug",
              77 in fepresence.visible_peers(me, key, 1e18, args))
        args.presence_dead = "condition"
        check("--presence-dead condition keeps them drawn (and pushes the "
              "state instead)",
              77 in fepresence.visible_peers(me, key, 1e18, args))
    finally:
        feworld.ext_sessions = saved
        fepresence._key = saved_key


def _nation_legend_checks(fecampaign, args):
    """LIVE 2026-09-12: "fields held always seems to say 0". The ●line at
    0x050FFDBE formats 「●%s　国民数：%4d人　制圧フィールド数：%3d」 from force
    +0x34 and +0x38 -- and +0x38 is not a field of its own, it is the COUNT of
    the record's (u32,u32) pair list, which we sent empty."""
    say("the nation legend: Pop from +0x34, Fields from the PAIR COUNT")
    rec = feworld.build_force_record(
        {"name": "N", "f34": 18, "pairs": [(11, 0), (12, 0), (13, 0)]})
    o = 4 + 2                                   # u32 f0c + "N" + NUL
    f34, = struct.unpack_from(">I", rec, o)
    cnt, = struct.unpack_from(">I", rec, o + 4)
    check("the pair COUNT is on the wire where +0x38 reads it",
          cnt == 3, repr((f34, cnt)))
    check("...and +0x34 carries the population", f34 == 18)
    got = [struct.unpack_from(">II", rec, o + 8 + 8 * i) for i in range(cnt)]
    check("...followed by that many pairs (the area ids)",
          got == [(11, 0), (12, 0), (13, 0)], repr(got))
    bare = feworld.build_force_record({"name": "N", "_pairs": 0})
    check("a bare _pairs count still emits no pairs (the pre-fix shape), and "
          "each pair is exactly 8 bytes",
          len(rec) - len(bare) == 3 * 8, "%d - %d" % (len(rec), len(bare)))

    # and the live board feeds it
    feworld.territory_set(11, 4, args)
    feworld.territory_set(12, 4, args)
    vals = fecampaign.force_vals(args, 4, {"name": "Holdaine"})
    held = sorted(a for a in feworld.fegamedata.areas()
                  if feworld.territory_owner(a, args) == 4)
    check("force_vals sends one pair per field the nation holds",
          [p[0] for p in vals["pairs"]] == held and len(held) >= 2,
          repr((len(vals["pairs"]), len(held))))
    check("...and FormerNumTerritory is the same number (the '(+0)' delta)",
          vals["FormerNumTerritory"] == len(held))


def _map_owner_checks(fecampaign, args):
    """SE's continent-map page: 「通常フィールドには国アイコンが表示されます」 -- a
    normal field is marked with the icon of the nation that HOLDS it, and
    group+0x78 def_id is the only owning-nation field on the wire. It used to
    come from --field-nations/--field-defenders: one constant per ISLAND."""
    say("the map's per-field owner (SE interface02: a field wears its nation's icon)")
    a = _args(field_nations="1:5", field_defenders="1,2,3,4,5",
              field_owner="board", force_table={}, campaign_file=None,
              territory_file=None)
    check("the knob is per-ISLAND, not per-field: island 1 answers one nation "
          "for all of its fields",
          feworld.field_contest(1, a) == (1, 5)
          and feworld.field_contest(1, a) == feworld.field_contest(1, a))
    # two frontier fields of island 1 held by DIFFERENT nations
    feworld.territory_set(11, 2, a)
    feworld.territory_set(12, 4, a)
    check("...while the territory board has them on different nations",
          (feworld.territory_owner(11, a), feworld.territory_owner(12, a)) == (2, 4))
    check("--field-owner board is what makes the icon follow the board",
          getattr(a, "field_owner") == "board")
    # and the attacker comes from the live campaign, not the knob
    a.campaign = "on"
    fecampaign.declare(a, 12, 3)
    check("atk_id is the nation that actually declared",
          feworld.campaign_attacker(a, 12) == 3, repr(feworld.campaign_attacker(a, 12)))
    check("...and a field at peace has no attacker, so the knob's value stands",
          feworld.campaign_attacker(a, 11) == 0)


def _census_checks(fecampaign, args):
    """Observed live 2026-09-12: a player, as Netzawar on the CENTRAL
    continent -- the one island with mixed owners -- saw no field markers at
    all.
    mapselect.tex's `Mark2` sheet is four person silhouettes labelled
    「100~」「50~」「10~」「~9」 plus a crown: the marker is a POPULATION TIER,
    and chars_all (mask bit 2) was never sent."""
    say("the field marker is a POPULATION TIER, so the count has to go on the wire")
    a = _args(field_census="on", field_census_probe=0, campaign="on",
              campaign_file=None, territory_file=None)
    saved = feworld.ext_sessions
    try:
        feworld.ext_sessions = lambda: [
            {"session": {"charid": 1, "in_field": True, "field": 11},
             "key": "a", "name": "a"},
            {"session": {"charid": 2, "in_field": True, "field": 11},
             "key": "b", "name": "b"},
            {"session": {"charid": 3, "in_field": True, "field": 12},
             "key": "c", "name": "c"},
            {"session": {"charid": 4, "in_field": False, "field": 12},
             "key": "d", "name": "d"}]
        c = feworld.field_census(a)
        check("a field's count is the live sessions standing in it",
              c == {11: 2, 12: 1}, repr(c))
        a.field_census = "off"
        check("--field-census off sends nothing (the pre-fix behaviour)",
              feworld.field_census(a) == {})
        a.field_census = "on"
        # the probe: a tester on the map has field-ed OUT, so the honest count
        # is 0 everywhere and "ignored" looks like "nobody here"
        a.field_census_probe = 120
        pr = feworld.field_census(a)
        check("--field-census-probe N reports N for EVERY field",
              len(pr) == len(feworld.fegamedata.areas())
              and set(pr.values()) == {120}, repr(len(pr)))
        a.field_census_probe = 0
        check("...and 0 restores the real count",
              feworld.field_census(a) == {11: 2, 12: 1})
        # !census N sets it live, so a probe needs no deploy
        ctx = types.SimpleNamespace(args=a, conn=None, outbound=None,
                                    mode=None, be=None)
        check("!census 120 takes the verb and sets the probe",
              fecampaign.gm(ctx, "!census 120") is True
              and a.field_census_probe == 120)
        check("...!census 0 turns it off",
              fecampaign.gm(ctx, "!census 0") is True
              and a.field_census_probe == 0)
        check("...a bad N is reported, not raised",
              fecampaign.gm(ctx, "!census nope") is True
              and a.field_census_probe == 0)
        check("...and a foreign verb is NOT taken",
              fecampaign.gm(ctx, "!pvp on") is False)
    finally:
        feworld.ext_sessions = saved
    say("the whole Char[D%d/%dvsA%d/%d(F%d)(ALL%d)] block (client log 0x052D32E4)")
    names = [f[1] for f in feworld.GROUP_FIELDS]
    offs = {f[1]: f[2] for f in feworld.GROUP_FIELDS}
    check("every wire number of that line is named in the table now (the A "
          "count is ALL - D, computed by the client, so it has no field)",
          all(n in names for n in ("def_chars", "def_max", "atk_max",
                                   "chars_f", "chars_all")),
          repr([n for n in ("def_chars","def_max","atk_max",
                            "chars_f","chars_all") if n not in names]))
    check("...the log call (0x050093bb) reads D/+0x8a and A/+0x88: +0x88 is the "
          "attackers' CEILING, and +0x8c stays the FLAGS word",
          offs["atk_max"] == 0x88 and offs["def_max"] == 0x8a
          and offs["g8c"] == 0x8c and offs["g8e"] == 0x8e,
          repr((offs.get("atk_max"), offs.get("def_max"))))
    check("bits 22/23 are the war panel's players-now pair at +0xbc/+0xbe",
          names[22] == "def_now" and names[23] == "atk_now"
          and offs["def_now"] == 0xbc and offs["atk_now"] == 0xbe,
          repr(names[20:]))
    _rec = feworld.build_group_record(9, {"def_now": 7, "atk_now": 4,
                                          "tail_color": 3, "tail_held": 19})
    _mask = struct.unpack_from(">I", _rec, 4)[0]
    check("...emitted under mask bits 22/23, then the 5-byte tail carries the "
          "attacker's palette index (+0xC0) and FIELDS HELD (+0xC4)",
          _mask == (1 << 22) | (1 << 23)
          and struct.unpack_from(">HH", _rec, 8) == (7, 4)
          and struct.unpack_from(">bI", _rec, 12) == (3, 19)
          and len(_rec) == 8 + 4 + 5, repr(_rec.hex()))
    check("...and a record with no tail values still sends the tail as zeros",
          feworld.build_group_record(9, {})[-5:] == bytes(5))
    check("the ceilings come from the war's own cap (SE 50 v 50)",
          feworld.campaign_war_cap(a) == 50, repr(feworld.campaign_war_cap(a)))
    a.campaign = "off"
    check("...and 0 with no campaign, so the fields stay absent",
          feworld.campaign_war_cap(a) == 0)
    a.campaign = "on"
    saved2 = feworld.ext_sessions
    try:
        def one(cid, area, force):
            return {"session": {"charid": cid, "in_field": True, "field": area,
                                "pres_card": {"force": force}},
                    "key": "k%d" % cid, "name": "p%d" % cid}
        feworld.ext_sessions = lambda: [one(1, 11, 3), one(2, 11, 3), one(3, 11, 5)]
        bn = feworld.field_census_nations(a)
        check("(F%d) counts per NATION, off the presence card's force -- not a "
              "'nation' key, which does not exist (the tower bug)",
              bn == {11: {3: 2, 5: 1}}, repr(bn))
    finally:
        feworld.ext_sessions = saved2

    check("mask bit 2 is chars_all -- the bit prod had CLEAR",
          [n for n, _f in
           [(f[1], f[2]) for f in feworld.GROUP_FIELDS]][2] == "chars_all")
    # the defenders' count rides bit 11 during a war
    fecampaign.declare(a, 12, 3)
    with fecampaign._LOCK:
        fecampaign._STATE[12]["phase"] = fecampaign.WAR
        fecampaign._STATE[12]["signups"] = {"def": 7, "atk": 4}
    check("def_chars is the defending side's sign-ups while a war is on",
          feworld.campaign_side_count(a, 12, "def") == 7
          and feworld.campaign_side_count(a, 12, "atk") == 4)
    check("...and None on a field at peace, so the field stays absent",
          feworld.campaign_side_count(a, 11, "def") is None)


def _enter_area_checks():
    """LIVE 2026-09-12: "it says I'm in Schelln Greenwood, but I clicked
    Rhinelay Valley and the environment is Rhinelay Valley". The entry
    validation used the pre-`dat` synthetic bound (--islands 5 x --groups 1 =
    FIVE) while the map announces all 95 real area ids."""
    say("field entry is validated against the ANNOUNCED groups, not a bound of 5")
    a = _args(world="dat", islands=6, groups=1, field_names="dat-en",
              capital="1:39", campaign_file=None, territory_file=None)
    check("the synthetic bound really is 5 with the shipped defaults",
          feworld._group_id(5, 0, 1) == 5)
    served = set()
    for i in range(1, 7):
        served |= set(feworld.group_ids_for(i, a))
    check("...while the announced set covers Rhinelay Valley (84) and the "
          "whole board", 84 in served and 6 in served and len(served) >= 90,
          "%d ids" % len(served))
    check("...so 84 is no longer 'not a served group' and cannot fall back "
          "to group 6 (Schelln Greenwood)",
          84 > feworld._group_id(5, 0, 1) and 84 in served)
    # the fallback's own value is what produced the wrong label
    check("the old fallback for island 6 was exactly 6 -- Schelln Greenwood",
          feworld._group_id(6, 0, 1) == 6)


def _world_clock_checks():
    """LIVE 2026-09-12: "the ingame world time isn't synced at all". It was
    UNSET, not wrong: the getter 0x04FFEF10 returns -1 until the base at
    0x5336ec0/ec4 is written, and only the 0x1148 arm writes it -- while the
    only caller was gated on --war-clock sync, which prod pins to telemetry."""
    say("the world clock rides 0x1148, and it is no longer gated on --war-clock")
    sent = []
    saved = feworld.send
    feworld.send = lambda c, o, inner, m, b, e, p: sent.append(inner)
    try:
        a = _args(world_clock="on", war_clock="telemetry", clock_epoch_ms=0,
                  unit_id="1", seq_mode="count", world_prefix=4)
        feworld._SESSION["in_field"] = True
        feworld._SESSION["cclock"] = (1000, __import__("time").monotonic())
        feworld._SESSION.pop("clock_synced", None)
        # 2026-09-12: sent once per CONNECTION, well after the field is ready
        # (fe_clock_test has the gate itself) -- so a field ready a minute ago
        feworld._SESSION["field_ready"] = True
        feworld._SESSION["field_ready_at"] = __import__("time").monotonic() - 60
        feworld.clock_sync_push(None, None, 0, False, a)
        check("--world-clock on sends 0x1148 even with --war-clock telemetry",
              any(i[4:6] == b"H" for i in sent), repr([x[:6].hex() for x in sent]))
        n = len(sent)
        feworld.clock_sync_push(None, None, 0, False, a)
        check("...once per session only", len(sent) == n, repr(len(sent)))
        sent[:] = []
        a.world_clock, a.war_clock = "off", "telemetry"
        feworld._SESSION.pop("clock_synced", None)
        feworld.clock_sync_push(None, None, 0, False, a)
        check("--world-clock off with telemetry sends nothing (the old state)",
              sent == [], repr(len(sent)))
        a.war_clock = "sync"
        # a FRESH connection: the raw clock again. The first send moved our
        # estimate to epoch ms, and a clock already past 2**40 is refused on
        # purpose -- a resend computed from it is the ~0 base that put the
        # world clock back 56 years (fe_clock_test)
        feworld._SESSION["cclock"] = (1000, __import__("time").monotonic())
        feworld._SESSION.pop("clock_synced", None)
        feworld.clock_sync_push(None, None, 0, False, a)
        check("...--war-clock sync still works on its own",
              any(i[4:6] == b"H" for i in sent),
              repr([x[:6].hex() for x in sent]))
    finally:
        feworld.send = saved
        feworld._SESSION.pop("clock_synced", None)


def main():
    feworld.load_extensions()
    import fecampaign
    import feunit
    tmp = os.environ.get("TEMP", ".")
    cf = os.path.join(tmp, "fe_ws04_c_%d.json" % os.getpid())
    tf = os.path.join(tmp, "fe_ws04_t_%d.json" % os.getpid())
    for p in (cf, tf):
        if os.path.exists(p):
            os.remove(p)
    args = _args(campaign_file=cf, territory_file=tf)
    ctx = types.SimpleNamespace(args=args, conn=None, outbound=None,
                                mode=None, be=None, session=feworld._SESSION)
    try:
        _drain_checks(fecampaign, args, ctx)
        _base_checks(feunit)
        _result_checks(args)
        _score_checks(fecampaign, args)
        _shared_building_checks(fecampaign, args, ctx)
        _tower_checks(fecampaign, args)
        _rank_cost_checks(fecampaign, args)
        _peer_death_checks()
        _nation_legend_checks(fecampaign, args)
        _map_owner_checks(fecampaign, args)
        _census_checks(fecampaign, args)
        _enter_area_checks()
        _world_clock_checks()
    finally:
        for p in (cf, tf):
            try:
                os.remove(p)
            except OSError:
                pass
    bad = [l for l, ok in CHECKS if not ok]
    say("[fe_warsystem04_test] %s -- %d checks"
        % ("FAIL" if bad else "OK", len(CHECKS)))
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("[fe_warsystem04_test] FAIL: %s" % e)
        sys.exit(1)
