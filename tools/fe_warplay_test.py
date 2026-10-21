#!/usr/bin/env python3
"""fe_warplay_test.py -- what players DO in a war: draw crystals, build,
summon, fight. Four sessions, the real defaults, the real read loop.

Run from services/:  python ../tools/fe_warplay_test.py

2026-10-08 (round 2). Since 09-23 prod has logged no crystal draw, no build,
no summon and no PvP damage: no war ever started with players in it. The
war loop itself (vote, phases, gauge, settlement) is fe_warloop_test's; this
suite takes a war at its WAR phase and plays it:

  CRYSTALS (0x2081 / 0x20AD): one per draw at the client's own pace (5 s,
    10 s above 10 carried) -- a sooner draw gets nothing; 20 per war; the
    deposit drains; +50 HP per heal tick while crouched; a war kill +3, a
    war death -3 (fewiki WAR/crystal 2006-06-14)
  BUILDINGS (0x2007): the 2006 cost (Obelisk 15) debited, the building
    served to everybody in the field, its HP and age; a swing from outside
    your own sphere lands nothing (book 59); the builder's Built column
  SUMMONS (0x2044 / 0x2045): the Knight at your keep for 40, HP% carried,
    the summoner's form shown to EVERY peer (0x203D under the charid, new
    2026-10-08) and the release too; the Knight's lance hits for the
    client's own 10 % + 300 (fegamedata.skill_flat)
  PvP (0x1031): the caster's own cast settles the hit, skill reach, ATTACK x
    POWER with NO level row (RoD: the correction is a monster rule); a kill
    credits the killer's kills and PC damage, costs the victim 3 crystals
"""
import struct
import sys
import time

import fe_harness as H
from fe_harness import check, ids, bodies, say

AREA = 17           # nation 1's, next to nation 3's 53


def crystal_of(box):
    """The 0x2035 CRYSTAL values in `box` (mask bit 1, u32)."""
    out = []
    for b in bodies(box, 0x2035):
        if len(b) >= 8 and struct.unpack_from(">I", b)[0] & 0x2:
            out.append(struct.unpack_from(">I", b, 4)[0])
    return out


def cast_body(skill, target=0, pos=None, a=1, b=2):
    body = struct.pack(">IIBIIHBII", a, b, 0, 0, 0, skill, 1, target, 0)
    if target == 0:
        body += struct.pack(">fff", *pos)
    return body


def main():
    H.setup()
    import feworld
    import fecampaign
    import fewar
    import fegamedata
    from world import buildings
    args = H.real_args()
    check("real defaults: campaign on, keeps, build real, summons by the "
          "rules, PvP on at war, crystals reset outside war",
          args.campaign == "on" and args.keeps == "on" and args.build == "real"
          and args.metamorphosis == "rules" and args.pvp == "on"
          and args.pvp_war == "on" and args.crystal_reset == "war")
    with H.quiet():
        feworld.territory_load(args)
        fecampaign.load(args)
    holder = feworld.territory_owner(AREA, args)
    check("area 17 is nation 1's", holder == 1, holder)
    # each has heard the King's message from the capital Manager (the war
    # join's --king-gate, audit B10)
    base = {"crystal": 0, "crystal_epoch": 1, "look1": 0,
            "class_levels": {"0": 10}, "king_message": 1}
    alice = H.Peer("Alice", 91, args, nation=3, field=AREA, char=dict(base))
    bob = H.Peer("Bob", 92, args, nation=3, field=AREA, char=dict(base))
    eve = H.Peer("Eve", 93, args, nation=1, field=AREA, char=dict(base))
    dave = H.Peer("Dave", 94, args, nation=1, field=AREA, char=dict(base))
    peers = (alice, bob, eve, dave)
    grids = buildings.keep_grids(AREA)
    gw = buildings.grid_to_world

    def at(p, grid, dx=0.0, dz=0.0):
        p.stand(gw(grid[0]) + dx, 20.0, gw(grid[1]) + dz)

    try:
        for p in (alice, bob):
            at(p, grids["atk"])
        for p in (eve, dave):
            at(p, grids["def"])
        H.see_each_other(*peers)
        row = fecampaign.declare(args, AREA, 3)
        check("a war declared on 17 by nation 3 (the mechanism; the vote is "
              "fe_warloop_test's)", row is not None)
        for p in peers:
            p.tick()
        for p, army in ((alice, 3), (bob, 3), (eve, 1), (dave, 1)):
            out = p.send(0x2018, struct.pack(">I", army))
            check("%s signs up with army %d -> 0x1020" % (p.name, army),
                  0x1020 in ids(out), ids(out))
        with fecampaign._LOCK:
            fecampaign._STATE[AREA]["until"] = time.time() - 1
        for p in peers:
            p.tick()
        check("War Prep -> At war", fecampaign.phase_of(AREA) == fecampaign.WAR)
        H.see_each_other(*peers)

        # ------------------------------------------------------------ crystals
        say("crystals")
        left = fecampaign.deposits(args, AREA)
        obj = fecampaign._crystal_base(args)
        check("the war has giant crystals (--war-crystals %d)" % args.war_crystals,
              len(left) == int(args.war_crystals) and left[0][0] > 0, left)
        before = left[0][0]
        out = bob.send(0x2081, struct.pack(">I", obj))
        check("Bob's first draw: 0x2035 CRYSTAL 1", crystal_of(out) == [1], ids(out))
        out = bob.send(0x2081, struct.pack(">I", obj))
        check("a second draw at once (the client waits 5 s): nothing",
              crystal_of(out) == [] and bob.char["crystal"] == 1, ids(out))
        bob.session["crystal_draw_at"] -= 5.0
        out = bob.send(0x2081, struct.pack(">I", obj))
        check("5 s later: 2", crystal_of(out) == [2], ids(out))
        check("...and the deposit is 2 lower",
              fecampaign.deposits(args, AREA)[0][0] == before - 2)
        bob.char["crystal"] = 11
        bob.session["crystal_draw_at"] -= 5.0
        out = bob.send(0x2081, struct.pack(">I", obj))
        check("carrying 11, 5 s is too soon (10 s above 10)", crystal_of(out) == [],
              ids(out))
        bob.session["crystal_draw_at"] -= 10.0
        out = bob.send(0x2081, struct.pack(">I", obj))
        check("...10 s is not", crystal_of(out) == [12], ids(out))
        with fecampaign._LOCK:
            fecampaign._STATE[AREA]["drawn"]["%s|%d" % (bob.account, 92)] = 20
        bob.session["crystal_draw_at"] -= 10.0
        out = bob.send(0x2081, struct.pack(">I", obj))
        check("20 drawn this war: no more (SE warsystem01)", crystal_of(out) == [],
              ids(out))
        bob.session["player_hp"] = 500
        out = bob.send(0x20AD, struct.pack(">I", obj))
        check("crouched at the crystal: +50 HP (500 -> 550), still at the cap",
              bob.session["player_hp"] == 550, bob.session["player_hp"])

        # ------------------------------------------------------------ buildings
        say("buildings")
        alice.char["crystal"] = 40
        far = grids["atk"]
        out = alice.send(fewar.BUILD_REQUEST, struct.pack(
            ">HHHHHB", 6, 8, far[0] + 4, 0, far[1], 0) + bytes(12))
        check("Alice builds an obelisk beside her keep: 0x1009 OK + 0x1006",
              0x1009 in ids(out) and 0x1006 in ids(out), ids(out))
        check("...15 crystals debited (the 2006 cost, FE_BUILDING_DATA +0xa4)",
              alice.char["crystal"] == 25, alice.char["crystal"])
        blds = fecampaign.buildings_of(AREA)
        ob = next((o for o, b in blds.items() if int(b.get("t", -1)) == 6), None)
        check("...the war holds it, at the client's 12,000 HP",
              ob is not None and int(blds[ob]["hp"][1]) == 12000,
              {o: (b.get("t"), b.get("hp")) for o, b in blds.items()})
        got = {p.charid: p.tick() for p in (bob, eve)}
        check("Bob and Eve are served it on their next pump (0x1006)",
              all(0x1006 in ids(got[c]) for c in (92, 93)),
              {c: ids(v) for c, v in got.items()})
        m = fecampaign.state_of(AREA)["members"]["%s|91" % alice.account]
        check("Alice's Built column: 1", m.get("built") == 1, m)
        # 0x2019, the building class's on-hit (builder 0x050725B6): 31 bytes
        def swing(p, obj, seq):
            x, y, z = p.session["cpos"]
            return p.send(0x2019, struct.pack(">IIHBII", p.charid, seq, 0, 1,
                                              int(obj), 0)
                          + struct.pack(">fff", x, y, z))

        hp0 = int(fecampaign.buildings_of(AREA)[ob]["hp"][0])
        mid_cell = ((grids["def"][0] + grids["atk"][0]) // 2,
                    (grids["def"][1] + grids["atk"][1]) // 2)
        outside = None
        for dx in range(-60, 61, 3):
            for dz in range(-60, 61, 3):
                c = (mid_cell[0] + dx, mid_cell[1] + dz)
                if not (fecampaign.in_influence(args, AREA, "def", c)
                        or fecampaign.in_influence(args, AREA, "atk", c)):
                    outside = c
                    break
            if outside:
                break
        check("a cell in neither side's sphere exists between the bases",
              outside is not None)
        at(dave, outside)
        swing(dave, ob, 1)
        check("Dave swings from OUTSIDE his side's sphere: nothing lands "
              "(book 59)", int(fecampaign.buildings_of(AREA)[ob]["hp"][0]) == hp0)
        swing(bob, ob, 2)
        check("Bob swings at his own side's obelisk: nothing (no friendly fire)",
              int(fecampaign.buildings_of(AREA)[ob]["hp"][0]) == hp0)
        at(eve, grids["def"])
        out = swing(eve, ob, 3)
        hp1 = int(fecampaign.buildings_of(AREA)[ob]["hp"][0])
        check("Eve swings from inside her castle's sphere: the obelisk takes "
              "ATTACK x 100%% (%d -> %d) and its new HP goes out (0x2024)"
              % (hp0, hp1), 0 < hp1 < hp0 and 0x2024 in ids(out), ids(out))
        with fecampaign._LOCK:
            fecampaign._STATE[AREA]["buildings"][str(ob)]["hp"][0] = 10
        eve.session.pop("keep_hit_last", None)
        out = swing(eve, ob, 4)
        check("the last swing fells it: 0x1004 MSG_DEL, gone from the war",
              0x1004 in ids(out) and ob not in fecampaign.buildings_of(AREA),
              ids(out))
        got = H.deliver(*peers)
        for p in (alice, bob):
            got[p.charid] += p.tick()
        check("...and from every other screen in the field (0x1004 to Alice)",
              (ob, b"") in [(u, b) for m_, u, b in got[91] if m_ == 0x1004]
              or 0x1004 in ids(got[91]), ids(got[91]))
        me = fecampaign.state_of(AREA)["members"]["%s|93" % eve.account]
        check("Eve's ranking: the building destroyed, its damage credited",
              me.get("destroyed") == 1 and int(me.get("dmg", 0)) > 0, me)

        # ------------------------------------------------------------ summons
        say("summons")
        alice.char["crystal"] = 45
        at(alice, grids["atk"])
        alice.session["player_hp"] = 1000
        out = alice.send(0x2044, struct.pack(">HBI", 954, 1, 2901))
        check("the Knight at her keep: 0x106A [2901] and form 0x008 (KNIT)",
              bodies(out, 0x106A) == [struct.pack(">I", 2901)]
              and struct.pack(">IH", 0x10, 0x8) in bodies(out, 0x203D),
              ids(out))
        check("...40 crystals (Netz 2006 / the keep's +0xe2 row), HP 2900 "
              "(a Lv10 Knight)", alice.char["crystal"] == 5
              and alice.session.get("player_hp") == 2900,
              (alice.char["crystal"], alice.session.get("player_hp")))
        got = H.deliver(*peers)
        seen = {c: [(u, b) for m_, u, b in got[c] if m_ == 0x203D]
                for c in (92, 93, 94)}
        check("EVERY peer's client: 0x203D form 0x008 on Alice's avatar (91)",
              all(v == [(91, struct.pack(">IH", 0x10, 0x8))] for v in seen.values()),
              seen)
        # the Knight's lance on Eve (Divine Piercing 972: 10 % + 300)
        at(eve, grids["atk"], dx=2.0)
        H.see_each_other(*peers)
        eve.session["player_hp"] = 1000
        _k, _b, _w, atk = alice.call(feworld.player_attack, args)[0]
        want = int(round(atk * 10 / 100.0 + 300))
        alice.send(0x1031, cast_body(972, target=93))
        H.deliver(*peers)
        check("the lance hits Eve for ATTACK x 10%% + 300 = %d" % want,
              eve.session.get("player_hp") == 1000 - want,
              eve.session.get("player_hp"))
        out = alice.send(0x2045)
        got = H.deliver(*peers)
        check("Unsummon: 0x106C to her, and 0x106C + form 0x200 to the peers",
              0x106C in ids(out)
              and (91, b"") in [(u, b) for m_, u, b in got[93] if m_ == 0x106C]
              and (91, struct.pack(">IH", 0x10, 0x200))
              in [(u, b) for m_, u, b in got[93] if m_ == 0x203D],
              ids(got[93]))
        # a summoner drawn LATER shows as the summon
        alice.char["crystal"] = 40
        at(alice, grids["atk"])
        alice.send(0x2044, struct.pack(">HBI", 954, 1, 2901))
        H.deliver(*peers)
        dave.session["pres_seen"].pop(91, None)
        out = dave.tick()
        late = [(u, b) for m_, u, b in out if m_ == 0x203D]
        check("Dave's client redraws her: 0x1006 then the Knight's form",
              0x1006 in ids(out) and (91, struct.pack(">IH", 0x10, 0x8)) in late,
              ids(out))
        alice.send(0x2045)
        H.deliver(*peers)

        # ------------------------------------------------------------ PvP
        say("PvP")
        at(bob, grids["atk"], dx=1.0)
        at(eve, grids["atk"], dx=3.0)
        H.see_each_other(*peers)
        eve.session["player_hp"] = 1000
        eve.char["crystal"] = 5
        _k, _b, _w, atk = bob.call(feworld.player_attack, args)[0]
        want = int(round(atk * fegamedata.skill_power(0) / 100.0))
        bob.send(0x1031, cast_body(0, target=93, a=11, b=12))
        H.deliver(*peers)
        check("Bob's basic attack on Eve: ATTACK x 100%%, no level row = %d"
              % want, eve.session.get("player_hp") == 1000 - want,
              eve.session.get("player_hp"))
        eve.session["player_hp"] = 5
        bob.char["crystal"] = 12
        bob.send(0x1031, cast_body(0, target=93, a=13, b=14))
        H.deliver(*peers)
        check("the finishing blow: Eve is DEAD", eve.session.get("player_dead"))
        check("...her war death costs 3 crystals (5 -> 2)",
              eve.char.get("crystal") == 2, eve.char.get("crystal"))
        check("...Bob's kill pays him 3 (12 -> 15)", bob.char.get("crystal") == 15,
              bob.char.get("crystal"))
        mb = fecampaign.state_of(AREA)["members"]["%s|92" % bob.account]
        check("...his ranking row: 1 kill, PC damage = what landed",
              mb.get("kills") == 1 and mb.get("pc_dmg") == want + 5, mb)
        out = bob.send(0x1031, cast_body(0, target=93, a=15, b=16))
        check("a dead Eve takes nothing more",
              not eve.session.get("player_hp"), eve.session.get("player_hp"))
        # same nation: no damage
        alice.session["player_hp"] = 1000
        at(alice, grids["atk"], dx=2.0)
        bob.send(0x1031, cast_body(0, target=91, a=17, b=18))
        H.deliver(*peers)
        check("Bob on Alice (same nation): nothing",
              alice.session.get("player_hp") == 1000)
    finally:
        for p in peers:
            p.leave()
    return H.finish("fe_warplay_test")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("FAIL: %s" % e)
        sys.exit(1)
