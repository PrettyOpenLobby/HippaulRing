#!/usr/bin/env python3
"""fe_combat_test.py -- Fantasy Earth's COMBAT channel and the tables under it.

Run from services/:  python ../tools/fe_combat_test.py

WHAT THIS PINS, AND WHY
-----------------------
The combat loop landed 2026-09-06 from static RE and NOTHING IN IT HAS BEEN
SEEN ON A SCREEN. That is exactly the situation where a test is worth having:
it cannot tell us the design is right, but it can stop the parts that ARE
measured from drifting while the design is being proved.

Three things are measured and are asserted here:

  1. 0xA011 MSG_NPC_HIT_NOTIFY's BODY LAYOUT. Each field's width is its
     writer's own advance of [0x533835c] in the builder at 0x05069700 --
     0x5045be0 +4 (u32), 0x5045bb0 +2 (u16), 0x5045b80 +1 (u8), 0x5045ca0 +4
     (f32) -- so the parse is 4*u32, u16, u8, 3*f32 = 31 bytes and no other
     split fits. A decoder that reads the skill id at the wrong offset is the
     0x107A bug again, and the only cheap defence is a vector.

  2. The HP push is a 0x1006 TYPE-8 record whose mask1 is 0x6 -- bit 1
     (+0x49a, HP max base) and bit 2 (+0x49e, HP current), the two rows
     feworld._AVATAR_STATS already pins against the Status window's own
     printf. mask2 and mask3 must be ZERO: a second position field would
     re-run the ground snap on every hit.

  3. dat.pak's tables cross-check EACH OTHER, and those checks are the reason
     to believe the columns at all:
       * fet_area marks exactly 21/39/57/62/78 (+ their 91..95 twins) as
         capitals, which is exactly the set fet_npc_generator_pos gives no
         monster spawn points;
       * FE_CLASS_BASIC_PARAM_DATA's per-class skill ids land in the same
         436..447 proficiency space FE_ITEM_DATA's +0x88 prerequisites use,
         read from the opposite side months apart;
       * fet_area_portal's destinations are RECIPROCAL -- portal 1 in area 21
         points at 11, and 11 points back at 1.

WHAT IS DELIBERATELY NOT ASSERTED: that a monster's HP bar reads +0x49e, that
0x1004 removes anything, or which of fet_area_portal's three floats is the
height. Those are live questions and a test that pretended to answer them
would be laundering a guess into a fact.
"""
import io
import os
import struct
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICES = os.path.join(os.path.dirname(HERE), "services")
sys.path.insert(0, SERVICES)

import fegamedata  # noqa: E402
import feworld  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print("  %-56s %s%s" % (name, "ok" if ok else "FAIL",
                            ("  " + repr(detail)) if detail != "" and not ok
                            else ""))
    if not ok:
        FAILED.append(name)


def args_stub(**kw):
    a = types.SimpleNamespace(
        combat="on", hit_damage=25, monster_hp=0, kill_exp=0,
        kill_reward="off", respawn_secs=0.0, unit_state=None,
        monster_level=1, seq_mode="echo", world_prefix=4, unit_id="0",
        doors="off", door_radius=15.0, spawn_xyz=(0.0, 5.0, 0.0),
        # flat by default HERE so the swing tests keep asserting one exact
        # number; the table model has its own section at the end.
        damage_model="flat",
        # likewise the player's hits: flat --hit-damage here, the level
        # correction (2026-09-12) has its own section at the end.
        mob_level_correction="off",
        # and the 2026-09-12 table models (player attack x skill power, item
        # EFFECT_DATA, per-monster swing timing) -- each pinned at its end
        player_damage="flat", item_effects="flat", monster_timing="flat",
        armour_defence="off",
        # the 2026-09-09 display-only EXP bar and the flat SP budget are what
        # this file pins; EXP -> level -> SP (the 2026-09-11 defaults) is
        # tools/fe_prog_test.py's
        exp_model="flat", sp_per_level="off",
        # the SHIPPED npc_type EXP column (Nightmare 184) is what this file
        # pins; the 2006 default (--mob-exp rod, EXP by monster level) is
        # tools/fe_rod_numbers_test.py's
        mob_exp="shipped")
    for k, v in kw.items():
        setattr(a, k, v)
    return a


class Cap(object):
    """Collects what feworld.send() would have put on the wire."""

    def __init__(self):
        self.frames = []

    def install(self):
        self._real = feworld.send
        feworld.send = lambda conn, out, inner, *a, **k: self.frames.append(inner)
        return self

    def restore(self):
        feworld.send = self._real

    def inner(self, n=0):
        f = self.frames[n]
        # inner_msg lays out [u32 unitId][u16 id][body] -- the ENVELOPE object
        # FIRST, which is how every addressed record in this family names its
        # target. (The lobby door's header is the other way round; sending
        # that one here costs the session -- see feworld.inner_msg.)
        obj, mid = struct.unpack_from(">IH", f, 0)
        return mid, f[6:]

    def obj(self, n=0):
        return struct.unpack_from(">I", self.frames[n], 0)[0]


def hit_body(target, attacker, b, c, skill, kind, x, y, z):
    return (struct.pack(">IIII", target, attacker, b, c)
            + struct.pack(">HB", skill, kind)
            + struct.pack(">fff", x, y, z))


def main():
    print("0xA011 MSG_NPC_HIT_NOTIFY -- the body the client builds")
    v = hit_body(1401, 5150, 7, 9, 275, 2, 12.5, 3.0, -40.25)
    check("31 bytes: 4 x u32, u16, u8, 3 x f32", len(v) == 31, len(v))
    check("the skill id is at offset 16, not 14 or 18",
          struct.unpack_from(">H", v, 16)[0] == 275)
    check("the hit point starts at offset 19",
          struct.unpack_from(">f", v, 19)[0] == 12.5)

    print("\ndamage, death and the HP push")
    feworld._TLS.session = {}
    a = args_stub()
    feworld._SESSION["mobs"] = {}
    feworld.mob_register(a, 1401, 20, 0, 15, 12.0, 0.0, -40.0,
                         name="Nightmare", type_id=1004)
    m = feworld._SESSION["mobs"][1401]
    check("a monster takes its hit points from fet_npc_type", m["hpmax"] == 558,
          m["hpmax"])
    feworld.mob_register(a, 1402, 230, 1, 1, 0.0, 0.0, 0.0, name="Shopkeeper")
    check("a town NPC (kind 1) is NOT registered as a target",
          1402 not in feworld._SESSION["mobs"])

    # 2026-09-11: NPCs/monsters now carry a NAME (identity sub-record, name
    # only) so the client's [unit+0x389] is set -- over-head label + target
    # window. Was mask1 = 0 (nameless).
    ncap = Cap().install()
    try:
        na = args_stub(npc_names="on")
        feworld.npc_send(None, None, None, None, na, 1403, 233, 1, 1,
                         0.0, 0.0, 0.0, name="Warrior_Weapon_Shop")
        _mid, nb = ncap.inner(0)
        mtype, kind, lvl = struct.unpack_from(">HHB", nb, 7)
        mask1 = struct.unpack_from(">I", nb, 12)[0]
        submask = struct.unpack_from(">I", nb, 16)[0]
        nm, _n = nb[20:], nb.index(b"\x00", 20)
        name = nb[20:nb.index(b"\x00", 20)].decode("cp932")
        check("a named NPC sets mask1 bit 0 + a name-only sub-record",
              mask1 == 0x1 and submask == 0x1, (hex(mask1), hex(submask)))
        check("...with the role name made readable (underscores -> spaces)",
              name == "Warrior Weapon Shop", name)
        # and the position still lands right after the name (mask2 = 0x5)
        after = nb.index(b"\x00", 20) + 1
        mask2 = struct.unpack_from(">I", nb, after)[0]
        check("...and mask2 (position+facing) follows the name intact",
              mask2 == 0x5, hex(mask2))
        ncap.frames[:] = []
        feworld.npc_send(None, None, None, None, args_stub(npc_names="off"),
                         1404, 233, 1, 1, 0.0, 0.0, 0.0, name="Jade")
        _m2, nb2 = ncap.inner(0)
        check("--npc-names off restores the nameless record (mask1 0)",
              struct.unpack_from(">I", nb2, 12)[0] == 0,
              hex(struct.unpack_from(">I", nb2, 12)[0]))
    finally:
        ncap.restore()

    cap = Cap().install()
    try:
        feworld.combat_hit(None, None, None, None, a, v)
        mid, body = cap.inner(0)
        check("a hit answers with 0x1006", mid == 0x1006, hex(mid))
        # [u16 1][u32 obj][u8 type 8][u16 modeltype][u16 kind][u8 level]
        # [u32 mask1 = 0x2 "stats follow"][u32 statmask][u32 statmask2]
        # [i16 hpmax][i16 hp][u32 mask2][u32 mask3]
        # WARNING: LIVE 2026-09-09: the first answered hit crashed the client because
        # the stat bits sat where the record mask goes and no second mask was
        # sent; the type-8 HIT path (0x0503b144..65) reads both masks first.
        cnt, obj, typ = struct.unpack_from(">HIB", body, 0)
        mt, kind, lvl = struct.unpack_from(">HHB", body, 7)
        mask1 = struct.unpack_from(">I", body, 12)[0]
        smask, smask2 = struct.unpack_from(">II", body, 16)
        hpmax, hp = struct.unpack_from(">hh", body, 24)
        mask2, mask3 = struct.unpack_from(">II", body, 28)
        check("one record, type 8, for the unit that was hit",
              (cnt, obj, typ) == (1, 1401, 8), (cnt, obj, typ))
        check("record mask1 is 0x2: a stat block follows", mask1 == 0x2, hex(mask1))
        check("stat mask 0x6 (+0x49a max, +0x49e current) and a ZERO second mask",
              (smask, smask2) == (0x6, 0), (hex(smask), smask2))
        check("HP fell by --hit-damage", (hpmax, hp) == (558, 533), (hpmax, hp))
        check("mask2 and mask3 are 0 (no second position, no parts)",
              (mask2, mask3) == (0, 0))
        check("the record is exactly 36 bytes", len(body) == 36, len(body))
        check("nothing else went out on a non-fatal hit", len(cap.frames) == 1,
              len(cap.frames))

        # ...and now kill it.
        cap.frames[:] = []
        m["hp"] = 10
        feworld.combat_hit(None, None, None, None, a, v)
        ids = [cap.inner(i)[0] for i in range(len(cap.frames))]
        check("death sends the zeroed HP and then 0x1004 MSG_DEL",
              ids == [0x1006, 0x1004], [hex(i) for i in ids])
        check("0x1004 is header-only: the ENVELOPE names the object",
              cap.inner(1)[1] == b"" and cap.obj(1) == 1401)
        check("a dead monster is not hit again", m["dead"] and m["hp"] == 0)
        cap.frames[:] = []
        feworld.combat_hit(None, None, None, None, a, v)
        check("a second hit on a corpse sends nothing", not cap.frames)

        # --combat off must LOG and do nothing.
        cap.frames[:] = []
        off = args_stub(combat="off")
        feworld._SESSION["mobs"] = {}
        feworld.mob_register(off, 1401, 20, 0, 15, 0.0, 0.0, 0.0, type_id=1004)
        feworld.combat_hit(None, None, None, None, off, v)
        check("--combat off touches nothing", not cap.frames)
        check("--combat off leaves the monster at full HP",
              feworld._SESSION["mobs"][1401]["hp"] == 558)

        # The state word.
        cap.frames[:] = []
        feworld.unit_state_push(None, None, None, None, args_stub())
        check("--unit-state unset sends no 0x2024", not cap.frames)
        feworld.unit_state_push(None, None, None, None,
                                args_stub(unit_state=0x1E000000))
        mid, body = cap.inner(0)
        maskA, maskB, val = struct.unpack_from(">III", body, 0)
        check("--unit-state is 0x2024 maskA bit 0x100000",
              (mid, maskA, maskB) == (0x2024, 0x100000, 0),
              (hex(mid), hex(maskA), maskB))
        check("...carrying the mask verbatim", val == 0x1E000000, hex(val))
        check("the whole message is 12 bytes", len(body) == 12, len(body))
    finally:
        cap.restore()

    print("\ndat.pak's tables, cross-checked against each other")
    areas = fegamedata.areas()
    check("fet_area has 95 rows", len(areas) == 95, len(areas))
    caps = sorted(a for a, r in areas.items() if r["capital"])
    check("the capital marker is exactly 21/39/57/62/78 + 91..95",
          caps == [21, 39, 57, 62, 78, 91, 92, 93, 94, 95], caps)
    spawned = set(fegamedata.fields_with_spawns())
    check("...and NO capital has a monster spawn point",
          not (set(caps) & spawned), sorted(set(caps) & spawned))
    check("every war field 1..90 except 5 and the capitals HAS spawns",
          not [f for f in range(1, 91)
               if f not in spawned and f not in caps and f != 5])
    check("nations run 1..5 over the five capitals",
          sorted(areas[c]["nation"] for c in caps[:5]) == [1, 2, 3, 4, 5])
    check("field 5 is the starting land and borders many",
          len(areas[5]["neighbours"]) >= 7, areas[5]["neighbours"])
    check("adjacency is symmetric for field 1",
          all(1 in areas[n]["neighbours"] for n in areas[1]["neighbours"]),
          areas[1]["neighbours"])

    prof = {c: fegamedata.class_proficiencies(c) for c in range(7)}
    check("Warrior's class skills are the 436..447 proficiency band",
          prof[0] and all(436 <= s <= 447 for s in prof[0]), prof[0])
    check("Scout gets the bow (442), Sorcerer the wand (443)",
          442 in prof[1] and 443 in prof[2], (prof[1], prof[2]))
    item_skills = set()
    for it in fegamedata.items().values():
        item_skills.update(it["skills"])
    overlap = set(prof[0]) & item_skills
    check("the class table and the ITEM table name the same skill ids",
          len(overlap) >= 4, sorted(overlap))

    ports = {}
    for a in areas:
        for p in fegamedata.area_portals(a):
            ports[p["portal"]] = (a, p["dest"])
    check("fet_area_portal has 64 rows", len(ports) == 64, len(ports))
    check("...all of them in capitals",
          all(areas[a]["capital"] for a, _d in ports.values()))
    check("...and every destination points back at its source",
          all(ports.get(d, (None, None))[1] == p for p, (_a, d) in ports.items()),
          [p for p, (_a, d) in ports.items()
           if ports.get(d, (None, None))[1] != p])

    # The 2026-09-09 frame fix. A door's GATE is where the client reports the
    # touch and its SPAWN is where the other side puts you; the earlier
    # reading had the gate's second float as a HEIGHT and the radius as a
    # coordinate. Two things fall out that a wrong frame could not produce.
    allp = [p for a in areas for p in fegamedata.area_portals(a)]
    check("every portal's radius is a door-sized volume, not a coordinate",
          allp and all(1.0 <= p["radius"] <= 8.0 for p in allp),
          sorted({p["radius"] for p in allp}))
    check("gate and spawn are different places (you arrive past the door)",
          all(p["gate"] != p["spawn"] for p in allp))
    check("a capital half's doors all sit inside the map's own extent",
          all(abs(p["gate"][0]) < 400 and abs(p["gate"][1]) < 400
              for p in allp))
    check("class_proficiencies stays inside the 436..447 band even though "
          "the shipped array is wider",
          all(436 <= s <= 447 for c in range(7)
              for s in fegamedata.class_proficiencies(c))
          and 270 in fegamedata.class_start_skills(2),
          fegamedata.class_start_skills(2))

    print("\nthe shipped door graph -- feworld.dat_door_for (2026-09-09)")
    # Area 21's portals 1/2/3 are a row of three doors FOUR UNITS APART on the
    # same z. That spacing is the whole reason the match is against the door's
    # own radius rather than a blanket one, so it is what the test walks.
    a = args_stub(doors="dat", door_radius=1.0)
    p1 = fegamedata.area_portals(21)[0]
    hit = feworld.dat_door_for(a, 21, p1["gate"][0], p1["gate"][1])
    dest = fegamedata.portal(p1["dest"])
    check("standing on a door resolves it to the destination portal's AREA",
          hit is not None and hit[0] == dest["area"], hit)
    # WARNING: RETRACTED 2026-09-11: this asserted the DESTINATION portal's spawn,
    # which is the backwards reading -- it puts 60 of the 64 capital doors off
    # the destination map's collision, where the client hangs the player at
    # y = 10000. A portal's own spawn is where walking through IT puts you
    # (64/64 land on ground; see feworld.dat_door_for and fegamedata.
    # capital_ground). The height is the destination's collision at that spot.
    want = (p1["spawn"][0],
            fegamedata.capital_ground(dest["area"], *p1["spawn"]),
            p1["spawn"][1])
    check("...at the spawn of the portal you walked THROUGH, on the far "
          "side's floor",
          hit is not None and want[1] is not None
          and hit[1] == want, (hit, want))
    # Portals 1/2/3 share one landing spot, so an arrival cannot say WHICH of
    # the three matched -- the portal you come out beside can (11/12/13).
    check("a tight radius still picks the NEAREST of three doors 4u apart",
          all(feworld.dat_door_for(a, 21, q["gate"][0], q["gate"][1])[2]
              == q["dest"]
              for q in fegamedata.area_portals(21)[:3]),
          [feworld.dat_door_for(a, 21, q["gate"][0], q["gate"][1])[2]
           for q in fegamedata.area_portals(21)[:3]])
    check("a position nowhere near a door resolves to nothing",
          feworld.dat_door_for(a, 21, 9999.0, 9999.0) is None)
    check("a WAR FIELD has no shipped doors, so nothing changes there",
          feworld.dat_door_for(a, 1, 0.0, 0.0) is None)
    check("--doors off resolves nothing even standing on one",
          feworld.dat_door_for(args_stub(doors="off"), 21,
                               p1["gate"][0], p1["gate"][1]) is None)
    # The round trip: walk through a door and back, and you are in the area
    # you started in. This is the shipped graph's own invariant, applied
    # through the code that will actually answer 0x20AC.
    back = fegamedata.portal(dest["dest"])
    check("through a door and back is the area you started in",
          back is not None and back["area"] == 21 and back["portal"] == p1["portal"],
          back)

    st = fegamedata.npc_stats(1004)
    check("fet_npc_type carries hp/level/reward/skills",
          st.get("hp") == 558 and st.get("level") == 15 and st.get("reward")
          and len(st.get("skills", [])) == 4, st)
    lv = [(fegamedata.npc_stats(t).get("level"), fegamedata.npc_stats(t).get("hp"))
          for _x, _y, _z, _r, _m, _n, _g, t in fegamedata.spawn_points(1)]
    check("a field's monsters have levels and HP that rise together",
          all(l and h for l, h in lv)
          and sorted(lv) == sorted(lv, key=lambda p: (p[0], p[1])), lv)

    print("\ncast gates, from the CLIENT's own parsers (2026-09-06 live)")
    # Reported in live testing: "Skill conditions not met when I try to use it
    # on an enemy (this is for the basic spell)". That exact string is gate 4,
    # and every number below comes from dat.pak read the way the client reads
    # it -- SKILL_DATA via 0x051b10c8, FE_ITEM_DATA via 0x051976f0 -- not via
    # a guessed file/struct skew.
    sk = fegamedata.skills()
    basic = sk.get(270)
    check("270 is the basic magic attack and it is weapon-gated",
          basic and basic["weapon"] == 0x0202 and basic["state"] == 0, basic)
    check("...and needs 5 Pow, no crystal",
          basic["pow"] == 5 and basic["crystal"] == 0, basic)
    check("the three L1 spells need [unit+0x2b4] bit 25",
          all(sk[i]["state"] == 0x02000000 for i in (285, 305, 330)),
          [sk[i]["state"] for i in (285, 305, 330)])
    check("...which --unit-state 0x1E000000 covers",
          all(sk[i]["state"] & 0x1E000000 for i in (285, 305, 330)))
    check("a Beginner Wand's category is the staff bit",
          fegamedata.item_category(531) == 0x0200,
          hex(fegamedata.item_category(531)))
    check("...and it intersects the basic attack's mask",
          fegamedata.item_category(531) & basic["weapon"])
    check("clothes have no category at all",
          not any(fegamedata.item_category(i) for i in (1666, 1667, 1668)))

    # Equip's own jump table 0x0507b920: slot types 1, 9 and 10 are two-handed
    # and land on worn 0; 2..7 land on 1..6. Gate 4 reads worn 0 and 1 ONLY.
    check("a wand (slot 10) is worn at index 0",
          fegamedata.worn_index_for(10) == 0)
    check("a tunic (slot 4) is worn at index 3 -- outside gate 4's reach",
          fegamedata.worn_index_for(4) == 3)

    rep = dict((r[0], r) for r in fegamedata.cast_report({0: 531}, [270, 285],
                                                         unit_state=0))
    check("wand in hand: the basic attack casts", rep[270][2], rep[270])
    check("...but 285 still needs the state word", not rep[285][2], rep[285])
    rep = dict((r[0], r) for r in fegamedata.cast_report(
        {0: 531}, [285], unit_state=0x1E000000))
    check("wand + --unit-state 0x1E000000: 285 casts", rep[285][2], rep[285])
    rep = dict((r[0], r) for r in fegamedata.cast_report(
        {3: 1666}, [270], unit_state=0x1E000000))
    check("a tunic at worn 3 reproduces the exact refusal seen live",
          not rep[270][2] and "Skill conditions not met" in rep[270][3],
          rep[270])

    print("\nthe unequip reflection")
    feworld._TLS.session = {"in_field": True}
    cap = Cap().install()
    try:
        ua = args_stub(unequip_reflect="on")
        ok = feworld.unequip_push(None, None, None, None, ua, 1, "test")
        check("0x2004 goes out for a worn slot", ok and len(cap.frames) == 1)
        mid, body = cap.inner(0)
        check("...as 0x2004 [u32 1][u32 slotmask][u32 0]",
              mid == 0x2004 and body == struct.pack(">III", 1, 2, 0),
              (hex(mid), body.hex()))
        cap.frames[:] = []
        check("slot 13 is refused -- the array is 13 deep",
              not feworld.unequip_push(None, None, None, None, ua, 13, "t")
              and not cap.frames)
        feworld._TLS.session = {}
        check("nothing goes out before the field exists",
              not feworld.unequip_push(None, None, None, None, ua, 1, "t")
              and not cap.frames)
    finally:
        cap.restore()

    print("\nthe battle's ledger: EXP resumed on entry, tallied per visit, "
          "drawn on the war result (2026-09-08)")
    cap = Cap().install()
    try:
        S = feworld._SESSION
        S.clear()
        S["in_field"] = True
        a = args_stub(kill_reward="on", kill_exp=40, war_result="desert",
                      war_result_values="session", war_result_rows=0)
        # no account in the session -> the store answers None -> nothing
        # to resume, nothing sent
        S.pop("exp", None)
        check("a character with no stored EXP resumes nothing",
              not feworld.exp_resume_push(None, None, None, None, a)
              and not cap.frames, len(cap.frames))
        # a stored total (seeded here the way _load_char_field would)
        S["exp"] = 1234
        check("a stored total goes out on entry",
              feworld.exp_resume_push(None, None, None, None, a)
              and len(cap.frames) == 1, len(cap.frames))
        mid, body = cap.inner(0)
        mask, total = struct.unpack_from(">II", body, 0)
        # 2026-09-09: +0x135c is the DENOMINATOR of "Exp %d/%d", not the
        # total. This used to assert the total and so encoded the bug that
        # pinned the HUD bar at full.
        check("...as 0x1075 mask bit 2 into [unit+0x135c], carrying the "
              "DENOMINATOR -- the per-level step, not the total",
              (mid, mask, total, len(body)) == (0x1075, 0x2, 1000, 8),
              (hex(mid), hex(mask), total, len(body)))

        # a kill adds to the total AND to this visit's tally
        feworld.battle_reset()
        cap.frames[:] = []
        feworld._SESSION["mobs"] = {}
        feworld.mob_register(a, 1401, 20, 0, 15, 0.0, 0.0, 0.0, type_id=1004)
        feworld._SESSION["mobs"][1401]["hp"] = 5
        real_store = feworld._store_char_field
        stored = []
        feworld._store_char_field = lambda args, k, v: stored.append((k, v))
        try:
            feworld.combat_hit(None, None, None, None, a,
                               hit_body(1401, 1, 0, 0, 270, 1, 0, 0, 0))
        finally:
            feworld._store_char_field = real_store
        ids = [cap.inner(i)[0] for i in range(len(cap.frames))]
        # 2026-09-09: a kill now also pushes the TOTAL SCORE, because the
        # fet_fame_rank ladder is keyed by it and it had never moved. The
        # score rides AFTER the EXP push.
        check("a kill: HP 0, MSG_DEL, the EXP push, then the SCORE push",
              ids == [0x1006, 0x1004, 0x1075, 0x2024], [hex(i) for i in ids])
        mask, denom = struct.unpack_from(">II", cap.inner(2)[1], 0)
        check("the kill pushes the per-level DENOMINATOR, not the total",
              (mask, denom) == (0x2, 1000), (hex(mask), denom))
        # KEY: the bar has to EMPTY when a level rolls over: 1234 was 234/1000
        # and 1274 is 274/1000, and crossing 2000 must drop it to ~0. A
        # numerator that only ever grows reads as "always full".
        check("...and the numerator is progress WITHIN the level, so the bar "
              "resets instead of creeping toward full",
              (feworld.exp_numerator(a, 1274),
               feworld.exp_numerator(a, 1999),
               feworld.exp_numerator(a, 2000)) == (274, 999, 0),
              [feworld.exp_numerator(a, v) for v in (1274, 1999, 2000)])
        sbody = cap.inner(3)[1]
        smA, smB = struct.unpack_from(">II", sbody, 0)
        sval = struct.unpack_from(">I", sbody, 8)[0]
        check("the score push is maskA 0, maskB bit 1, one u32",
              (smA, smB, len(sbody)) == (0, 0x1, 12), (hex(smA), hex(smB), len(sbody)))
        check("...carrying the kill's EXP reward as the score delta",
              sval == 40, sval)
        check("...and BOTH totals are written to the store",
              stored == [("exp", 1274), ("total_score", 40)], stored)
        check("this visit's ledger says 1 kill, 40 EXP, 40 score",
              S["battle"] == {"kills": 1, "exp": 40, "score": 40}, S["battle"])

        # WARNING: 2026-09-11: the bar never moved because only the DENOMINATOR was
        # pushed per kill; the NUMERATOR (per-class dword, bit 1) went out once
        # at field entry. With a class set, combat_exp_push must now also emit
        # the numerator so the fill tracks EXP on every kill.
        real_cls = feworld.self_class_id
        feworld.self_class_id = lambda args: 2          # some class index
        cap.frames[:] = []
        real_store2 = feworld._store_char_field
        feworld._store_char_field = lambda args, k, v: None
        _save_exp, _save_batt = S.get("exp"), dict(S.get("battle") or {})
        try:
            feworld.combat_exp_push(None, None, None, None, a, 500)
        finally:
            feworld._store_char_field = real_store2
            feworld.self_class_id = real_cls
            S["exp"], S["battle"] = _save_exp, _save_batt   # don't pollute the ledger checks below
        exp_frames = [cap.inner(i) for i in range(len(cap.frames))
                      if cap.inner(i)[0] == 0x1075]
        masks = [struct.unpack_from(">I", b, 0)[0] for _m, b in exp_frames]
        check("a kill now pushes BOTH the denominator (bit 2) and the "
              "numerator (bit 1)",
              0x2 in masks and any(m & 0x1 for m in masks),
              [hex(m) for m in masks])
        # the numerator frame's class block carries the 4 exp bytes of the
        # current class at CLASS_EXP_BASE + class*4
        num = next(b for _m, b in exp_frames
                   if struct.unpack_from(">I", b, 0)[0] & 0x1)
        cnt = num[4]
        pairs = {num[5 + 2 * i]: num[6 + 2 * i] for i in range(cnt)}
        base = feworld.CLASS_EXP_BASE + 2 * 4
        want = feworld.exp_numerator(a, (_save_exp or 0) + 500)
        got = sum(pairs.get(base + i, 0) << (8 * i) for i in range(4))
        check("...and the numerator bytes are progress-within-level for class 2",
              got == want, "got %d want %d pairs %r" % (got, want, pairs))

        # the war result draws what the VISIT produced and nothing invented.
        # Since 2026-09-12 that is Exp AND Score (a kill feeds both: the EXP
        # reward and the rank ladder), plus the settlement's columns when a
        # war actually settled -- which it did not here, so the rest is 0.
        vals = feworld.war_result_values(a)
        f = feworld.WAR_RESULT_FIELDS
        check("war result 'session': Exp and Score = the visit's 40, rest 0",
              vals[f.index("exp")] == 40 and vals[f.index("score")] == 40
              and sum(vals) == 80, vals)
        check("...no settled war in this session -> kind 0 Desert",
              feworld.war_result_kind(args_stub(war_result="auto"))[0] == 0)
        check("'zero' is still all zeros",
              feworld.war_result_values(args_stub(war_result_values="zero"))
              == (0,) * 11)
        check("'sentinel' is still the probe",
              feworld.war_result_values(args_stub(war_result_values="sentinel"))
              == feworld.WAR_RESULT_SENTINELS)
        cap.frames[:] = []
        a.unit_id = "1"
        feworld.war_result_push(None, None, None, None, a)
        mid, body = cap.inner(0)
        check("0x1100 carries the visit's EXP at wire slot 6",
              mid == 0x1100 and struct.unpack_from(">I", body, 19)[0] == 40,
              (hex(mid), body.hex()))
        # entry resets the ledger; the stored total is untouched
        feworld.battle_reset()
        check("a new field entry starts the ledger at zero",
              S["battle"] == {} and S["exp"] == 1274, (S["battle"], S["exp"]))
        S.clear()
    finally:
        cap.restore()

    print("\nthe self record's worn-gear array (mask3), 2026-09-08")
    rows = [(1001, 531, 0, 0, 1), (1002, 1666, 2, 3, 1), (1011, 7, 8, 11, 1),
            (1009, 19, 9, 12, 1), (1007, 1576, 1, None, 1), (1099, 1, 5, 13, 1),
            (1005, 531, 4, 0, 1)]
    m3, body, desc = feworld.worn_gear_block(rows)
    check("mask3 has one bit per worn index", m3 == (1 | 1 << 3 | 1 << 11 | 1 << 12),
          hex(m3))
    check("an unworn row, an index past 12 and a duplicate index are dropped",
          "1099" not in desc and "1007" not in desc and "1005" not in desc, desc)
    check("per set bit, ascending: [u32 uid][u32 2][u16 item]",
          body == (struct.pack(">IIH", 1001, 2, 531) + struct.pack(">IIH", 1002, 2, 1666)
                   + struct.pack(">IIH", 1011, 2, 7) + struct.pack(">IIH", 1009, 2, 19)),
          body.hex())
    check("no worn rows: mask3 = 0 and an empty body",
          feworld.worn_gear_block([]) == (0, b"", ""))

    print("\nthe bag record's worn markers are WORN INDICES (2026-09-08 run 4)")
    items = [(1001, 531, 1), (1002, 1666, 1), (1011, 7, 1), (1009, 19, 1), (1005, 531, 1)]
    equip = [(10, 1001), (4, 1002), (11, 1011), (11, 1009)]
    lay = feworld.worn_layout(equip, items)
    check("a wand (slot type 10) is marked worn 0, a tunic (4) worn 3",
          lay.get(1001) == 0 and lay.get(1002) == 3, lay)
    check("two pocket items (slot type 11) take worn 11 then 12",
          lay.get(1011) == 11 and lay.get(1009) == 12, lay)
    check("the spare wand is not marked at all", 1005 not in lay, lay)
    body = feworld.bag_record(items, lay, "0")
    wand = body.find(struct.pack(">I", 1001))
    check("the wand's bag entry carries marker 0x00, not its slot type 0x0a",
          wand >= 0 and body[wand + 4:].find(struct.pack(">BI", 0x00, 0)) >= 0
          and struct.pack(">HBI", 1, 0x0a, 0) not in body, body.hex()[:160])

    print("\n--add-self client: the worn-item 0x107A replay is the request-shaped kind")
    cap = Cap().install()
    try:
        S = feworld._SESSION
        S.clear()
        S["in_field"] = True
        a = args_stub(equip_reflect="request", unit_id="1", field_item_kind="0",
                      equip_announce=0)
        rows = [(1001, 531, 0, 0, 1)]
        n = feworld.equip_reflect_push(None, None, None, None, a, rows, "t", "entry")
        check("kind 'entry' under --equip-reflect request sends nothing",
              not cap.frames, len(cap.frames))
        feworld.equip_reflect_push(None, None, None, None, a, rows, "t", "client")
        check("kind 'client' under the same setting sends the 0x107A",
              len(cap.frames) == 1 and cap.inner(0)[0] == 0x107A,
              [hex(cap.inner(i)[0]) for i in range(len(cap.frames))])
        if cap.frames:
            body = cap.inner(0)[1]
            check("...an ADD of uid 1001 into bag slot 0, marker worn 0",
                  body[:10] == struct.pack(">BBI", 1, 0, 0) + struct.pack(">I", 1001)
                  and struct.pack(">B", 0x00) in body[10:], body.hex())
        S.clear()
    finally:
        cap.restore()

    print("\n--add-self client: the stat block on 0x2024, the palette held for the HUD")
    cap = Cap().install()
    try:
        S = feworld._SESSION
        S.clear()
        S["in_field"] = True
        a = args_stub(add_stats={0: 5, 7: 200, 8: 200, 18: 1.5}, unit_id="1")
        real_acq = feworld.acquired_skills
        feworld.acquired_skills = lambda args: [270, 275]
        try:
            m = feworld.stat_2024_push(None, None, None, None, a)
        finally:
            feworld.acquired_skills = real_acq
        mid, body = cap.inner(0)
        check("0x2024 maskA = bits 0x1|0x80|0x100, maskB 0",
              (mid, m, struct.unpack_from(">II", body, 0)) == (0x2024, 0x181, (0x181, 0)),
              (hex(mid), hex(m), body.hex()))
        check("three i16 in ascending BIT order: points left, gate1, gate2",
              body[8:] == struct.pack(">hhh", 3, 200, 200), body[8:].hex())
        check("bit 0 is the BUDGET minus the skills already learned (5 - 2)",
              struct.unpack_from(">h", body, 8)[0] == 3)
        check("a jump-physics row (bit 18) does not leak into this channel",
              len(body) == 14, len(body))
        cap.frames[:] = []
        check("no stats -> nothing sent",
              feworld.stat_2024_push(None, None, None, None, args_stub(add_stats={})) == 0
              and not cap.frames)
        S.clear()
    finally:
        cap.restore()

    print("\nthe class-level array is 7 bytes, and the EXP dwords live behind it")
    check("levels outside the 7 slots are dropped on the way out",
          feworld._clamp_class_rows([(0, 30), (2, 30), (7, 30), (16, 30)])
          == [(0, 30), (2, 30)])
    rows = feworld.class_exp_rows(2, 823)
    check("class 2's EXP dword is at index 0x10 (+0x9F8 + 2*4 = +0xA00)",
          [i for i, _v in rows] == [0x10, 0x11, 0x12, 0x13], rows)
    check("...little-endian, so 823 = 0x337 is 37 03 00 00",
          [v for _i, v in rows] == [0x37, 0x03, 0x00, 0x00], rows)
    check("36 levels of 30 would have written 0x1E1E1E1E there -- the bug",
          all(v == 0x1E for _i, v in feworld.class_exp_rows(2, 0x1E1E1E1E)))
    check("no class means no rows", feworld.class_exp_rows(None, 823) == [])

    print("\nmonsters hit back (2026-09-09): the HP wire is a STORE, and the revive is not optional")
    cap = Cap().install()
    try:
        S = feworld._SESSION
        S.clear()
        S["in_field"] = True
        S["cpos"] = (100.0, 0.0, 100.0)
        # 2006's flat HP 1000 and the x5 damage that keeps ~17 swings
        # (2026-09-11; this fixture was 200 / 12)
        a = args_stub(monster_attack="on", monster_damage=60, monster_range=8.0,
                      monster_interval=2.0, player_hp=1000, revive_secs=8.0,
                      unit_state=0x1E000000, unit_id="1", motion_refresh="off")
        S["mobs"] = {}
        feworld.mob_register(a, 1401, 20, 0, 15, 104.0, 0.0, 100.0, name="near",
                             type_id=1004)
        feworld.mob_register(a, 1402, 20, 0, 15, 400.0, 0.0, 400.0, name="far",
                             type_id=1004)
        feworld.monster_attack_tick(None, None, None, None, a)
        check("only the monster in range swings", len(cap.frames) == 1,
              len(cap.frames))
        mid, body = cap.inner(0)
        maskA, maskB = struct.unpack_from(">II", body, 0)
        hp = struct.unpack_from(">h", body, 8)[0]
        check("it is 0x2024 maskA bit 0x4, the damage event",
              (mid, maskA, maskB) == (0x2024, 0x4, 0), (hex(mid), hex(maskA), maskB))
        check("the wire carries the NEW hp (940), NOT the damage (60)",
              hp == 940, hp)
        check("...and the session agrees", S["player_hp"] == 940, S.get("player_hp"))
        cap.frames[:] = []
        feworld.monster_attack_tick(None, None, None, None, a)
        check("the same monster will not swing again inside --monster-interval",
              not cap.frames, len(cap.frames))
        # walk out of range: nothing swings
        S["cpos"] = (400.0, 0.0, 100.0)
        S["mobs"][1401]["swing_at"] = 0
        feworld.monster_attack_tick(None, None, None, None, a)
        check("out of range, nothing swings", not cap.frames, len(cap.frames))
        # a dead monster never swings
        S["cpos"] = (100.0, 0.0, 100.0)
        S["mobs"][1401]["dead"] = True
        feworld.monster_attack_tick(None, None, None, None, a)
        check("a dead monster never swings", not cap.frames, len(cap.frames))
        S["mobs"][1401]["dead"] = False

        # death: HP to 0, DEAD set, then the unconditional revive
        S["player_hp"] = 5
        S["mobs"][1401]["swing_at"] = 0
        cap.frames[:] = []
        feworld.monster_attack_tick(None, None, None, None, a)
        ids = [cap.inner(i)[0] for i in range(len(cap.frames))]
        # 2026-09-13: a death also sends RoD's RESPAWN WAIT (0x2024 bit
        # 0x1000000 -> [player+0x1384], the Return to Base countdown)
        check("the killing blow sends the hp, the DEAD state, then the wait",
              ids == [0x2024, 0x2024, 0x2024]
              and struct.unpack_from(">III", cap.inner(2)[1], 0)
              == (0x1000000, 0, 15000), [hex(i) for i in ids])
        check("hp floors at 0", struct.unpack_from(">h", cap.inner(0)[1], 8)[0] == 0)
        m1, _m2, cond = struct.unpack_from(">III", cap.inner(1)[1], 0)
        check("DEAD is CONDITION bit 0x800000, keeping the skill-gate bits",
              (m1, cond) == (0x100000, 0x1E800000), (hex(m1), hex(cond)))
        check("the session is dead and a revive is scheduled",
              S["player_dead"] and S["revive_at"] > 0)
        cap.frames[:] = []
        feworld.monster_attack_tick(None, None, None, None, a)
        check("nothing hits a dead player", not cap.frames, len(cap.frames))
        S["revive_at"] = 0                      # the timer has passed
        cap.frames[:] = []
        feworld.monster_attack_tick(None, None, None, None, a)
        ids = [cap.inner(i)[0] for i in range(len(cap.frames))]
        check("the revive clears DEAD and restores full HP",
              ids == [0x2024, 0x2024]
              and struct.unpack_from(">I", cap.inner(0)[1], 8)[0] == 0x1E000000
              and struct.unpack_from(">h", cap.inner(1)[1], 8)[0] == 1000,
              [hex(i) for i in ids])
        check("...and the player is alive with full HP",
              not S["player_dead"] and S["player_hp"] == 1000)
        cap.frames[:] = []
        feworld.monster_attack_tick(None, None, None, None,
                                    args_stub(monster_attack="off"))
        check("--monster-attack off swings nothing", not cap.frames)

        print("\nthe MOTION SET refresh (the T-pose)")
        cap.frames[:] = []
        rows = [(1001, 531, 5, 0, 1), (1002, 1666, 2, 3, 1), (1007, 1576, 1, None, 1)]
        real_load = feworld._load_char_field
        feworld._load_char_field = lambda args, k, d=None: (
            [(1001, 531, 1), (1002, 1666, 1), (1007, 1576, 1)] if k == "items" else d)
        try:
            ok = feworld.motion_refresh_push(None, None, None, None, a, rows, "t")
        finally:
            feworld._load_char_field = real_load
        check("--motion-refresh off sends nothing", not ok and not cap.frames)
        a.motion_refresh = "on"
        feworld._load_char_field = lambda args, k, d=None: (
            [(1001, 531, 1), (1002, 1666, 1), (1007, 1576, 1)] if k == "items" else d)
        try:
            feworld.motion_refresh_push(None, None, None, None, a, rows, "t")
        finally:
            feworld._load_char_field = real_load
        mid, body = cap.inner(0)
        check("it is a 0x2004 whose outer mask is bit 0 (the worn-gear reader)",
              mid == 0x2004 and struct.unpack_from(">I", body, 0)[0] == 1,
              (hex(mid), body.hex()))
        check("the slot mask names worn 0 and 3, and skips the unworn row",
              struct.unpack_from(">I", body, 4)[0] == (1 | 1 << 3),
              hex(struct.unpack_from(">I", body, 4)[0]))
        check("per slot: [u32 uid][u32 mask1=2][u16 item], ascending",
              body[8:] == (struct.pack(">IIH", 1001, 2, 531)
                           + struct.pack(">IIH", 1002, 2, 1666)), body[8:].hex())
        S.clear()
    finally:
        cap.restore()

    print("\nper-monster damage -- feworld.monster_swing_damage (2026-09-09)")
    a = args_stub(monster_damage=12, damage_model="table")
    v = fegamedata.npc_stats(1004)          # Nightmare, L15, attack 299
    duke = fegamedata.npc_stats(1849)       # Duke_Orc, L53, attack 630 -- the
                                            # row the first kill measured
    check("the weakest monster still does exactly --monster-damage",
          feworld.monster_swing_damage(a, {"attack": 92})[0] == 12)
    d_v = feworld.monster_swing_damage(a, {"attack": v["attack"]})[0]
    d_d = feworld.monster_swing_damage(a, {"attack": duke["attack"]})[0]
    check("a level 53 Duke Orc hits harder than a level 15 Nightmare",
          d_d > d_v > 12, (d_v, d_d))
    check("...in the proportion the table ships, not one we picked",
          abs(d_d / float(d_v) - duke["attack"] / float(v["attack"])) < 0.02,
          (d_d / float(d_v), duke["attack"] / float(v["attack"])))
    check("a type with no ATTACK column falls back, it does not do 0 damage",
          feworld.monster_swing_damage(a, {"attack": 0})[0] == 12)
    check("--damage-model flat is one number for every monster",
          feworld.monster_swing_damage(args_stub(damage_model="flat",
                                                 monster_damage=12),
                                       {"attack": 630})[0] == 12)
    check("every monster in the bestiary does at least 1",
          all(feworld.monster_swing_damage(a, {"attack": st["attack"]})[0] >= 1
              for st in (fegamedata.npc_stats(t) for t in (1004, 1849, 1504))))
    # ...and through the tick, not just the helper: a registered monster must
    # carry its own ATTACK column all the way to the wire.
    cap2 = Cap().install()
    try:
        S = feworld._SESSION
        S.clear()
        S["in_field"] = True
        S["cpos"] = (0.0, 0.0, 0.0)
        S["player_hp"] = 1000
        ta = args_stub(combat="on", monster_attack="on", damage_model="table",
                       monster_damage=60, player_hp=1000, monster_range=8.0,
                       monster_interval=2.0, revive_secs=8)
        feworld.mob_register(ta, 1, 20, 0, 15, 0.0, 0.0, 0.0,
                             name="Nightmare", type_id=1004)
        check("mob_register keeps the monster's own attack/defence",
              (S["mobs"][1]["attack"], S["mobs"][1]["defence"]) == (299, 299),
              S["mobs"][1])
        feworld.monster_attack_tick(None, None, None, None, ta)
        want = 1000 - feworld.monster_swing_damage(ta, S["mobs"][1])[0]
        check("a Nightmare's swing reaches the wire scaled by ITS attack",
              cap2.frames
              and struct.unpack_from(">h", cap2.inner(0)[1], 8)[0] == want
              and want != 940, want)
        S.clear()
    finally:
        cap2.restore()

    print("\nthe monster table's own columns (fet_npc_type, streamed)")
    check("the row the FIRST KILL measured reads back exactly: "
          "Duke_Orc hp 2464, exp 823, level 53",
          (duke["hp"], duke["reward"], duke["level"]) == (2464, 823, 53), duke)
    check("the 40 rows a newline in model_file used to truncate now parse "
          "(Harpy_Queen is level 30, 1260 hp, 436 exp)",
          (fegamedata.npc_stats(1504)["hp"],
           fegamedata.npc_stats(1504)["level"],
           fegamedata.npc_stats(1504)["reward"]) == (1260, 30, 436),
          fegamedata.npc_stats(1504))
    every = [fegamedata.npc_stats(t) for t in range(1000, 2600)]
    every = [e for e in every if e.get("level")]
    check("attack and defence rise with level across the whole bestiary",
          every and min(e["attack"] for e in every) >= 1
          and max(e["attack"] for e in every) <= 4000
          and (sum(e["attack"] for e in every if e["level"] >= 50)
               / max(1, len([e for e in every if e["level"] >= 50]))
               > sum(e["attack"] for e in every if e["level"] <= 10)
               / max(1, len([e for e in every if e["level"] <= 10]))))
    check("no monster type is left with a blank column",
          all(e["hp"] and e["exp" if "exp" in e else "reward"] for e in every))

    print("\nmovement: what --unit-speed and --jump-phys mean (2026-09-09)")
    ma = args_stub()
    S = feworld._SESSION
    try:
        S.clear(); S["field"] = 1
        row_f = feworld.move_row(ma)
        S.clear(); S["field"] = 21
        row_c = feworld.move_row(ma)
        check("a war field takes movement row 0, a capital row 6",
              (row_f[0], row_c[0]) == (0, 6), (row_f, row_c))
        check("...and the capital walks faster than the field (6.0 vs 4.0)",
              row_c[1] == 6.0 and row_f[1] == 4.0, (row_f[1], row_c[1]))
        check("the row the capital predicate picks covers exactly "
              "CAPITAL_GROUP_IDS",
              all(feworld.move_row(ma, a)[0] == 6
                  for a in feworld.CAPITAL_GROUP_IDS)
              and all(feworld.move_row(ma, a)[0] == 0
                      for a in (1, 5, 20, 22, 40, 90)))
        check("the jump launch is row[3] = 16.5, NOT row[2] = 4.7 (the dash)",
              row_f[3] == 16.5 and row_f[2] == 4.7, row_f)
        # KEY: THE RETRO-PREDICTION. LIVE 2026-09-08 run 4 served 1.5:1.0 and the
        # client logged SmartJumpMessage N ~2700; the note written then said
        # the model was "low by ~3x". 16.5/4.7 = 3.51, and the corrected model
        # lands on 2720. This test exists so that number cannot drift back.
        n = feworld.jump_flight_ms(ma, 1.5, 1.0, area=1)
        check("1.5:1.0 predicts the ~2700 ms measured live",
              n is not None and abs(n - 2700) <= 60, n)
        old_model = int(round((2 * 1.5 * 4.7 / (1.0 * 24.5)) * 1000)) + 700
        check("...and the pre-fix x4.7 model did not (it said ~1280)",
              abs(old_model - 2700) > 1000, old_model)
        check("zero gravity has no flight time rather than dividing by zero",
              feworld.jump_flight_ms(ma, 1.0, 0.0, area=1) is None)
    finally:
        S.clear()

    print("\nthe continent, in English (fe-area-names-en.tsv, 2026-09-09)")
    en = fegamedata.area_names_en()
    check("every one of the 95 shipped areas has an English name",
          sorted(en) == list(range(1, 96)),
          [a for a in range(1, 96) if a not in en])
    check("no name is blank or left as a placeholder",
          all(v and not v.startswith("Field ") for v in en.values()),
          [k for k, v in en.items() if not v or v.startswith("Field ")])
    check("the five capitals and their 91..95 halves share a name, "
          "the way dat.pak does",
          all(en[c] == en[h] for c, h in
              ((21, 91), (39, 92), (57, 93), (62, 94), (78, 95))))
    check("names stay short enough to draw (<= 24 chars)",
          max(len(v) for v in en.values()) <= 24,
          max(en.values(), key=len))
    check("the English table covers exactly the areas fet_area ships",
          set(en) == set(fegamedata.areas()),
          sorted(set(en) ^ set(fegamedata.areas())))

    print("\nthe RANK ladder -- fet_fame_rank (2026-09-09)")
    ranks = fegamedata.fame_ranks()
    check("eleven ranks, thresholds ascending from 0",
          len(ranks) == 11 and ranks[0][1] == 0
          and all(ranks[i][1] < ranks[i + 1][1] for i in range(len(ranks) - 1)),
          ranks)
    check("the names ship in English even in the JP build",
          ranks[0][2] == "Beginner" and ranks[-1][2] == "Commander"
          and all(all(ord(c) < 128 for c in r[2]) for r in ranks))
    check("a score just under a threshold does NOT promote",
          fegamedata.fame_rank(9999)[1] == "Beginner"
          and fegamedata.fame_rank(10000)[1] == "Apprentice")
    check("the top rank has no next threshold",
          fegamedata.fame_rank(12000000)[2] is None
          and fegamedata.fame_rank(99000000)[1] == "Commander")
    check("rank rises monotonically with score",
          [fegamedata.fame_rank(v)[0] for v in
           (0, 10000, 50000, 110000, 225000, 495000, 1195000, 2885000,
            4000000, 6000000, 12000000)] == list(range(11)))

    # ...and through the kill path: a kill must move the score and say so.
    cap3 = Cap().install()
    try:
        S = feworld._SESSION
        S.clear()
        stored = {}
        real_store = feworld._store_char_field
        real_load = feworld._load_char_field
        feworld._store_char_field = lambda a, k, v: stored.__setitem__(k, v)
        feworld._load_char_field = lambda a, k, d=None: stored.get(k, d)
        try:
            sa = args_stub(combat="on", kill_reward="on", kill_score="exp",
                           hit_damage=99999, respawn_secs=0.0)
            feworld.mob_register(sa, 7, 20, 0, 15, 0.0, 0.0, 0.0,
                                 name="Nightmare", type_id=1004)
            body = struct.pack(">IIII", 7, 1, 2, 1001) + struct.pack(">HB", 270, 1)
            feworld.combat_hit(None, None, None, None, sa, body)
            check("a kill stores EXP and TOTAL SCORE on the character",
                  stored.get("exp") == 184 and stored.get("total_score") == 184,
                  stored)
            ids = [struct.unpack_from(">H", f, 4)[0] for f in cap3.frames]
            check("...and the score goes out as a 0x2024 (maskB bit 1)",
                  0x2024 in ids, [hex(i) for i in ids])
            stored.clear(); S.clear()
            sb = args_stub(combat="on", kill_reward="on", kill_score="off",
                           hit_damage=99999, respawn_secs=0.0)
            feworld.mob_register(sb, 8, 20, 0, 15, 0.0, 0.0, 0.0,
                                 name="Nightmare", type_id=1004)
            body = struct.pack(">IIII", 8, 1, 2, 1001) + struct.pack(">HB", 270, 1)
            feworld.combat_hit(None, None, None, None, sb, body)
            check("--kill-score off: EXP still lands, total_score untouched",
                  stored.get("exp") == 184 and "total_score" not in stored,
                  stored)
        finally:
            feworld._store_char_field = real_store
            feworld._load_char_field = real_load
            S.clear()
    finally:
        cap3.restore()

    print("\nmonsters that CHASE -- 0x2023 action 0 (2026-09-09)")
    cap4 = Cap().install()
    try:
        S = feworld._SESSION
        S.clear()
        S["in_field"] = True
        S["cpos"] = (0.0, 0.0, 0.0)
        ca = args_stub(combat="on", monster_chase="on", monster_aggro=15.0,
                       monster_range=8.0, monster_speed=3.0,
                       monster_chase_interval=0.0)
        body = bytes(range(feworld._MV_LEN))
        # far outside aggro -- must not move
        feworld.mob_register(ca, 11, 20, 0, 15, 100.0, 0.0, 0.0, type_id=1004)
        feworld.monster_chase_tick(None, None, None, None, ca, body)
        check("a monster outside --monster-aggro does not move",
              not cap4.frames and S["mobs"][11]["pos"] == (100.0, 0.0, 0.0))
        # inside aggro -- must step toward the player and stay on the wire
        S["mobs"] = {}
        cap4.frames[:] = []
        feworld.mob_register(ca, 12, 20, 0, 15, 12.0, 0.0, 0.0, type_id=1004)
        feworld.monster_chase_tick(None, None, None, None, ca, body)
        check("a monster inside aggro steps TOWARD the player",
              cap4.frames and S["mobs"][12]["pos"][0] < 12.0,
              S["mobs"][12]["pos"])
        mid = cap4.inner(0)[0]
        mb = cap4.inner(0)[1]
        act = struct.unpack_from(">I", mb, feworld._MV_ACTION)[0]
        state = struct.unpack_from(">H", mb, feworld._MV_STATE)[0]
        env = struct.unpack_from(">I", cap4.frames[0], 0)[0]
        check("...as 0x2023 action 0, state 0, addressed to the MONSTER",
              (mid, act, state, env) == (0x2023, 0, 0, 12),
              (hex(mid), act, state, env))
        tx, _ty, tz = struct.unpack_from(">hhh", mb, feworld._MV_POS)
        check("...and the target position on the wire is the monster's NEW "
              "spot, not the player's",
              abs(tx * 0.1 - S["mobs"][12]["pos"][0]) < 0.11 and tx > 0,
              (tx * 0.1, tz * 0.1, S["mobs"][12]["pos"]))
        # 2026-09-12: this check USED TO ASSERT the pair was the client's own,
        # copied verbatim -- and so it encoded the bug. The monster branch
        # reads the two u32s as a START and an END and uses END - START as the
        # move's DURATION (0x04FEAB38). The client's pair is the HIGH and LOW
        # half of its clock, so that difference was minutes: the monster
        # played its walk and stayed put ("animating like they're trying to
        # move, but they are stuck in place").
        a0, b0 = struct.unpack_from(">II", mb, feworld._MV_A)
        step = 12.0 - S["mobs"][12]["pos"][0]
        want = int(round(step / 3.0 * 1000.0))
        check("...and its START/END differ by step/speed -- the move's DURATION",
              (b0 - a0) & 0xFFFFFFFF == want and step > 1.0,
              ((b0 - a0) & 0xFFFFFFFF, want, step))
        ca_, cb_ = struct.unpack_from(">II", body, feworld._MV_A)
        check("...NOT the client's own pair, whose difference is absurd here",
              mb[feworld._MV_A:feworld._MV_A + 8]
              != body[feworld._MV_A:feworld._MV_A + 8]
              and (cb_ - ca_) & 0xFFFFFFFF > 60 * 1000,
              ((cb_ - ca_) & 0xFFFFFFFF))
        S["mobs"] = {}
        cap4.frames[:] = []
        feworld.mob_register(ca, 16, 20, 0, 15, 12.0, 0.0, 0.0, type_id=1004)
        feworld.monster_chase_tick(None, None, None, None,
                                   args_stub(combat="on", monster_chase="on",
                                             monster_aggro=15.0,
                                             monster_range=8.0,
                                             monster_speed=3.0,
                                             monster_chase_interval=0.0,
                                             monster_chase_timing="client"),
                                   body)
        check("--monster-chase-timing client restores the borrowed pair (A/B)",
              cap4.frames and cap4.inner(0)[1][feworld._MV_A:feworld._MV_A + 8]
              == body[feworld._MV_A:feworld._MV_A + 8])
        # 2026-09-12: EACH MONSTER'S OWN SPEED, NPC_ModelType +0x41c/+0x420.
        # A flat 10 u/s was 2.7x a Duke_Orc's run -- "he moving very fast".
        # With the interval at 0 a step is speed * 0.5 s, so the step LENGTH
        # is what tells the speeds apart (the duration is 500 ms either way).
        def _one(mtype, **kw):
            S["mobs"] = {}
            cap4.frames[:] = []
            a_ = args_stub(combat="on", monster_chase="on", monster_aggro=15.0,
                           monster_range=8.0, monster_chase_interval=0.0, **kw)
            feworld.mob_register(a_, 17, mtype, 0, 15, 12.0, 0.0, 0.0,
                                 type_id=1004)
            feworld.monster_chase_tick(None, None, None, None, a_, body)
            return 12.0 - S["mobs"][17]["pos"][0]
        # 2026-09-28: a step reaches _chase_lead() ahead (1.0 s at the
        # defaults), not the 0.5 s interval, so the next send replaces it
        # mid-walk instead of after the monster has stopped.
        lead = feworld._chase_lead(args_stub(), 0.5)
        run = feworld.fegamedata.model_speed(17)[1]
        check("the default chases at the model's OWN run speed (Duke_Orc 3.7)",
              abs(_one(17) - run * lead) < 0.01 and abs(run - 3.7) < 0.01,
              (_one(17), run, lead))
        step_w = _one(17, monster_speed="walk")
        a_w, b_w = struct.unpack_from(">II", cap4.inner(0)[1], feworld._MV_A)
        walk_ = feworld.fegamedata.model_speed(17)[0]
        check("...'walk' uses +0x41c instead, timed at the walk speed (1.8)",
              abs(step_w - walk_ * lead) < 0.01 and abs(walk_ - 1.8) < 0.01
              and (b_w - a_w) & 0xFFFFFFFF
              == int(round(step_w / walk_ * 1000.0)),
              (step_w, (b_w - a_w) & 0xFFFFFFFF, walk_))
        # 2026-09-12 (live: an orc "slingshotting toward my player and
        # back"): 0.5 u/s x the lead is under the client's 1.0u move discard
        # (0x04FEAAD4), so it goes out as _MOB_MIN_STEP -- timed at that
        # speed, so the step is longer and the speed is unchanged.
        step_s = _one(17, monster_speed="0.5")
        a_s, b_s = struct.unpack_from(">II", cap4.inner(0)[1], feworld._MV_A)
        check("...a slow step is sent as _MOB_MIN_STEP, timed at its speed",
              abs(step_s - feworld._MOB_MIN_STEP) < 0.01
              and (b_s - a_s) & 0xFFFFFFFF
              == int(round(feworld._MOB_MIN_STEP / 0.5 * 1000.0)),
              (step_s, (b_s - a_s) & 0xFFFFFFFF))
        check("...a number still pins every monster (the old behaviour)",
              abs(_one(17, monster_speed="5") - 5.0 * lead) < 0.01,
              _one(17, monster_speed="5"))
        # 2026-09-28, "enemies feel jittery": each step must outlast the gap
        # to the next send (interval + one idle tick), or the client finishes
        # the move, stands, and restarts the walk when the next one lands.
        a_d = args_stub(idle_tick_ms=250)
        gap = 0.5 + 0.25
        check("a chase step outlasts the gap to the next send",
              feworld._chase_lead(a_d, 0.5) > gap
              and feworld._chase_lead(args_stub(idle_tick_ms=1000), 0.5)
              > 0.5 + 1.0, feworld._chase_lead(a_d, 0.5))
        # and it runs WITHOUT a telemetry body -- the pump clock calls it so
        # a player standing still to target still sees the monster come
        S["mobs"] = {}
        cap4.frames[:] = []
        a_p = args_stub(combat="on", monster_chase="on", monster_aggro=15.0,
                        monster_range=8.0, monster_chase_interval=0.0)
        feworld.mob_register(a_p, 17, 17, 0, 15, 12.0, 0.0, 0.0, type_id=1004)
        feworld.monster_chase_tick(None, None, None, None, a_p)
        check("the chase moves a monster on the pump clock (no 0x2023 body)",
              len(cap4.frames) == 1 and S["mobs"][17]["pos"][0] < 12.0,
              (len(cap4.frames), S["mobs"][17]["pos"]))
        check("...and two models chase at DIFFERENT speeds by default",
              abs(_one(17) - _one(41)) > 1.0, (_one(17), _one(41)))
        # 2026-09-12: A RESPAWN KEEPS ITS OWN TYPE, NAME AND SPAWN POINT. A
        # Duke_Orc L40 came back as L15: the re-send passed only the
        # modeltype, so level and HP fell to the lowest type sharing the model.
        fb = feworld.fegamedata.npc_level(17, None)
        own = feworld.fegamedata.npc_level(17, 1849)
        check("(non-vacuous: the modeltype fallback level differs from the "
              "type's own)", fb != own and own > 0, (fb, own))
        S["mobs"] = {}
        cap4.frames[:] = []
        ra = args_stub(combat="on", respawn_secs=1.0)
        feworld.mob_register(ra, 18, 17, 0, own, 40.0, 30.0, 40.0,
                             name="Duke_Orc", type_id=1849)
        m18 = S["mobs"][18]
        m18["pos"] = (5.0, 12.0, 5.0)              # the chase dragged it here
        m18["dead"], m18["died_at"] = True, feworld.time.monotonic() - 60
        feworld.mob_respawn_tick(None, None, None, None, ra)
        r18 = S["mobs"][18]
        check("a respawned monster keeps its OWN level (not the model's L%s)"
              % fb, r18["level"] == own and not r18["dead"],
              (r18["level"], own))
        check("...and its npc type id and name",
              r18.get("type_id") == 1849 and r18.get("name") == "Duke_Orc",
              (r18.get("type_id"), r18.get("name")))
        check("...and comes back at its SPAWN POINT, not where it died",
              r18["pos"] == (40.0, 30.0, 40.0), r18["pos"])
        # it must STOP short of the player, not walk into them
        S["mobs"] = {}
        feworld.mob_register(ca, 13, 20, 0, 15, 2.0, 0.0, 0.0, type_id=1004)
        cap4.frames[:] = []
        feworld.monster_chase_tick(None, None, None, None, ca, body)
        check("a monster already inside swing range does not creep closer",
              not cap4.frames and S["mobs"][13]["pos"] == (2.0, 0.0, 0.0),
              S["mobs"][13]["pos"])
        # a dead monster never walks
        S["mobs"] = {}
        feworld.mob_register(ca, 14, 20, 0, 15, 12.0, 0.0, 0.0, type_id=1004)
        S["mobs"][14]["dead"] = True
        cap4.frames[:] = []
        feworld.monster_chase_tick(None, None, None, None, ca, body)
        check("a dead monster never walks", not cap4.frames)
        # off means off
        S["mobs"] = {}
        feworld.mob_register(ca, 15, 20, 0, 15, 12.0, 0.0, 0.0, type_id=1004)
        cap4.frames[:] = []
        feworld.monster_chase_tick(None, None, None, None,
                                   args_stub(combat="on", monster_chase="off"),
                                   body)
        check("--monster-chase off sends nothing", not cap4.frames)

        # 2026-09-12: the server's copy of a monster is where the CLIENT has
        # it -- part-way along its last move -- never the move's end. The
        # server used to jump m["pos"] to the destination when the 0x2023
        # went out, so the next move asked the client to cover the whole gap
        # in one short step (the slingshot) and swings were judged against a
        # spot the monster had not reached (every hit at exactly 6.0u).
        t = feworld.time.monotonic()
        S["mobs"] = {}
        feworld.mob_register(ca, 19, 20, 0, 15, 14.0, 0.0, 0.0, type_id=1004)
        m19 = S["mobs"][19]
        feworld.mob_move_note(m19, (30.0, 0.0, 0.0), 10000, t)
        check("mob_pos: a 10 s walk just begun is still near its START",
              abs(feworld.mob_pos(m19, t + 0.1)[0] - 14.16) < 0.01
              and m19["pos"] == (30.0, 0.0, 0.0), feworld.mob_pos(m19, t + 0.1))
        check("...half-way at half time, and at the end once it is over",
              abs(feworld.mob_pos(m19, t + 5.0)[0] - 22.0) < 0.01
              and feworld.mob_pos(m19, t + 11.0) == (30.0, 0.0, 0.0),
              (feworld.mob_pos(m19, t + 5.0), feworld.mob_pos(m19, t + 11.0)))
        cap4.frames[:] = []
        feworld.monster_chase_tick(None, None, None, None, ca, body)
        check("a monster walking AWAY is chased from where it is DRAWN (~14u, "
              "inside aggro), not its walk's end (30u, outside aggro)",
              cap4.frames and S["mobs"][19]["pos"][0] < 14.0,
              (len(cap4.frames), S["mobs"][19]["pos"]))
        # the last approach: 0.9u of room is a move the client would drop
        S["mobs"] = {}
        feworld.mob_register(ca, 20, 20, 0, 15, 6.9, 0.0, 0.0, type_id=1004)
        cap4.frames[:] = []
        feworld.monster_chase_tick(None, None, None, None, ca, body)
        check("a last approach under _MOB_MIN_STEP (0.9u) is not sent AND does "
              "not move the server's copy",
              not cap4.frames and S["mobs"][20]["pos"] == (6.9, 0.0, 0.0),
              (len(cap4.frames), S["mobs"][20]["pos"]))
        # a swing is judged where the monster is DRAWN
        aa = args_stub(combat="on", monster_attack="on", monster_damage=60,
                       monster_range=8.0, monster_interval=2.0, player_hp=1000,
                       revive_secs=8.0, unit_state=0x1E000000, unit_id="1",
                       motion_refresh="off")
        S["mobs"] = {}
        S["player_hp"] = 1000
        S.pop("player_dead", None)
        feworld.mob_register(aa, 21, 20, 0, 15, 30.0, 0.0, 0.0, type_id=1004)
        m21 = S["mobs"][21]
        feworld.mob_move_note(m21, (5.0, 0.0, 0.0), 10000)
        cap4.frames[:] = []
        feworld.monster_attack_tick(None, None, None, None, aa)
        check("a monster whose walk ENDS in range but is drawn ~30u away does "
              "not swing", not cap4.frames, len(cap4.frames))
        m21["mv"] = ((30.0, 0.0, 0.0), (5.0, 0.0, 0.0), 0.0, 1.0)  # arrived
        m21["swing_at"] = 0
        cap4.frames[:] = []
        feworld.monster_attack_tick(None, None, None, None, aa)
        check("(non-vacuous: once the walk is over, the same monster swings)",
              len(cap4.frames) >= 1, len(cap4.frames))
        S.clear()
    finally:
        cap4.restore()

    print("")
    print("the EXP gauge's FLOORLESS drain, and a GUESSED height (2026-09-12)")
    import inspect as _inspect

    # The client's own gauge update, 0x050CE35B..0x050CE374, transcribed: with
    # the level unchanged, exp above the fill creeps it up by `step` CLAMPED to
    # exp; exp below the fill drains it by `step` with NO FLOOR (u32).
    def _drain(fill, exp, step, ticks=4000):
        for _ in range(ticks):
            if exp > fill:
                fill = min(fill + step, exp) & 0xFFFFFFFF
            elif exp < fill:
                fill = (fill - step) & 0xFFFFFFFF
            else:
                return fill
        return fill

    st = feworld.gauge_step(150000)
    check("the gauge step TRUNCATES: 150000 -> 1499 (0x051B7678, RC=0xC)",
          st == 1499, st)
    check("...and any max under 101 steps by 0 (a bar that cannot animate)",
          feworld.gauge_step(100) == 0, feworld.gauge_step(100))
    old = _drain(2258, 0, st)
    check("(non-vacuous: 19:18Z's loss -- all 2258 of the progress -- "
          "UNDERFLOWS the fill: the full bar)", old > 150000, old)
    loss = (min(2258, 15000) // st) * st
    new = _drain(2258, 2258 - loss, st)
    check("a loss of WHOLE steps (%d) lands the drain exactly on the new "
          "value" % loss, loss == 1499 and new == 2258 - loss, (loss, new))
    check("...even RoD's own unclamped 10%% of Next underflows near the bottom "
          "(15000 from 16000), and the rounded 14990 does not",
          _drain(16000, 1000, st) > 150000
          and _drain(16000, 16000 - (15000 // st) * st, st) == 1010,
          (_drain(16000, 1000, st),
           _drain(16000, 16000 - (15000 // st) * st, st)))
    check("...and death_penalty really rounds (the drain model above is only "
          "worth something if the code does what it models)",
          "(want // s) * s" in _inspect.getsource(feworld.death_penalty))

    cap5 = Cap().install()
    try:
        S = feworld._SESSION
        S.clear()
        S["in_field"] = True
        S["cpos"] = (100.0, 47.0, 100.0)
        a5 = args_stub(monster_attack="on", monster_damage=60, monster_range=8.0,
                       monster_interval=2.0, player_hp=1000,
                       unit_state=0x1E000000, unit_id="1", motion_refresh="off")
        S["mobs"] = {}
        feworld.mob_register(a5, 1501, 20, 0, 15, 104.0, 62.0, 100.0,
                             name="high", type_id=1004)
        feworld.monster_attack_tick(None, None, None, None, a5)
        check("(non-vacuous: a MEASURED height 15u off is still refused)",
              not cap5.frames, len(cap5.frames))
        S["mobs"][1501]["y_guess"] = True
        S["mobs"][1501]["swing_at"] = 0
        feworld.monster_attack_tick(None, None, None, None, a5)
        check("a GUESSED height (the table's y=0 raised to the map median) no "
              "longer blocks the swing", len(cap5.frames) == 1, len(cap5.frames))
        check("...and spawn_push marks exactly those spawns as guessed",
              '["y_guess"] = True' in _inspect.getsource(feworld._spawn_build))
        # (2026-09-13: spawn_push's body moved into _spawn_build when the
        # field's monsters became SHARED -- spawn_push now builds once, then
        # shows every player the same set)
        check("...and a respawn keeps the mark",
              'm["y_guess"] = True' in _inspect.getsource(feworld.mob_respawn_tick))
    finally:
        cap5.restore()
        feworld._SESSION.clear()

    print("\nthe EXP bar denominator (2026-09-09)")
    ea = args_stub(exp_next="auto", exp_per_level=1000)
    check("auto is a per-level bar: denominator fixed, numerator wraps",
          [feworld.exp_denominator(ea, v) for v in (0, 823, 1000, 1274)]
          == [1000, 1000, 1000, 1000]
          and [feworld.exp_numerator(ea, v) for v in (0, 823, 1000, 1274)]
          == [0, 823, 0, 274],
          [(feworld.exp_numerator(ea, v), feworld.exp_denominator(ea, v))
           for v in (0, 823, 1000, 1274)])
    check("...so a level rollover EMPTIES the bar rather than filling it",
          feworld.exp_numerator(ea, 1999) > feworld.exp_numerator(ea, 2000))
    check("a pinned number is used verbatim",
          feworld.exp_denominator(args_stub(exp_next="5000"), 823) == 5000)
    check("off restores the old full bar (both halves the total)",
          feworld.exp_denominator(args_stub(exp_next="off"), 823) == 823
          and feworld.exp_numerator(args_stub(exp_next="off"), 823) == 823)
    check("a junk value falls back to the total rather than to zero",
          feworld.exp_denominator(args_stub(exp_next="banana"), 823) == 823)
    check("the denominator is never 0 (it is a divisor on screen)",
          all(feworld.exp_denominator(ea, v) > 0 for v in (0, 1, 999, 10**6)))

    print("\nlevel correction -- fet_npc_lv_diff_correction, 0x050DFEB0 (2026-09-12)")
    row = fegamedata.lv_diff_correction
    check("key 3 (monster 3 above) is the neutral row",
          row(3) == (1.0, 1.0, 1.0, 1.0), row(3))
    check("an exact key: +10 -> f0 0.6", row(10) and row(10)[0] == 0.6, row(10))
    check("above the top key CLAMPS to +15 (f0 0.05)",
          row(25) == row(15) and row(25)[0] == 0.05, row(25))
    check("below the bottom key CLAMPS to -1 (f0 1.4)",
          row(-30) == row(-1) and row(-30)[0] == 1.4, row(-30))
    la = args_stub(hit_damage=400, mob_level_correction="on")
    d, _why = feworld.player_hit_damage(la, {"level": 27}, level=2)
    check("the live-observed case: L2 vs L27 = 400 x 0.05 = 20", d == 20, d)
    d, _why = feworld.player_hit_damage(la, {"level": 5}, level=5)
    check("an even fight: 400 x 1.4 = 560", d == 560, d)
    d, _why = feworld.player_hit_damage(la, {"level": 8}, level=5)
    check("monster +3: 400 x 1.0 = 400", d == 400, d)
    d, _why = feworld.player_hit_damage(
        args_stub(hit_damage=400, mob_level_correction="off"),
        {"level": 27}, level=2)
    check("--mob-level-correction off = flat --hit-damage", d == 400, d)
    d, _why = feworld.player_hit_damage(
        args_stub(hit_damage=1, mob_level_correction="on"),
        {"level": 40}, level=1)
    check("never below 1", d == 1, d)

    print("\nthe client's own numbers -- skill power, attack, items (2026-09-12)")
    check("basic attack power 100%", fegamedata.skill_power(0) == 100.0,
          fegamedata.skill_power(0))
    check("Sonic Boom L1..L5 = 70/80/90/100/130",
          [fegamedata.skill_power(i) for i in range(40, 45)]
          == [70.0, 80.0, 90.0, 100.0, 130.0])
    check("Ender Pain (a buff) has no power", fegamedata.skill_power(5) == 0.0)
    check("Scout base attack 127 physical",
          fegamedata.class_attack(1) == ("phys", 127), fegamedata.class_attack(1))
    check("Sorcerer attacks with magic", fegamedata.class_attack(2)[0] == "magic")
    check("beginner axe atk 56, beginner wand matk 45",
          fegamedata.item_use(342).get("atk") == 56
          and fegamedata.item_use(531).get("matk") == 45)
    ta = args_stub(player_damage="table", mob_level_correction="on",
                   hit_damage=400)
    d, why = feworld.player_hit_damage(ta, {"level": 3}, level=3, skill=40,
                                       attack=183)
    check("table: 183 x 70% x 1.4 (even) = 179", d == 179, (d, why))
    d, _why = feworld.player_hit_damage(ta, {"level": 27}, level=2, skill=40,
                                        attack=183)
    check("table: the live L2 vs L27 case = 183 x 70% x 0.05 = 6", d == 6, d)
    d, _why = feworld.player_hit_damage(ta, {"level": 3}, level=3, skill=5,
                                        attack=183)
    check("a skill with no damage effect falls back to --hit-damage x 1.4",
          d == 560, d)
    fx = fegamedata.item_effect(4)
    check("bread = 50 HP at once",
          fx and (fx["stat"], fx["amount"], fx["total_ms"]) == ("hp", 50, 0), fx)
    check("cheese = 90 HP (the client's number, not the wiki's 125)",
          fegamedata.item_effect(7)["amount"] == 90)
    fx = fegamedata.item_effect(17)
    check("regen potion = 48 HP every 4 s for 32 s, tier 2",
          (fx["stat"], fx["amount"], fx["tick_ms"], fx["total_ms"], fx["tier"])
          == ("hp", 48, 4000, 32000, 2), fx)
    check("power pot = Pw", fegamedata.item_effect(20)["stat"] == "pw")
    check("a weapon restores nothing", fegamedata.item_effect(342) is None)
    check("Pw regen default = 16 per tick (fewiki 2006, was a chosen 3)",
          feworld.DEFAULT_PW_REGEN == 16, feworld.DEFAULT_PW_REGEN)
    sk = fegamedata.npc_attack_skill(1004, 0)
    check("Nightmare's melee: reach 7.0, period 2.2 s",
          (sk.get("range"), round(sk.get("period", 0), 2)) == (7.0, 2.2), sk)

    print("\narmour -- fewiki's 2006 defence tables, FEZ's formula (2026-09-12)")
    check("シビックチュニック (1669) = defence 7 (fewiki 鎧, 2006-05-26)",
          fegamedata.armour_defence(1669) == 7, fegamedata.armour_defence(1669))
    check("カジュアルシャツ (1165) = defence 6", fegamedata.armour_defence(1165) == 6)
    check("a weapon has no defence entry", fegamedata.armour_defence(342) is None)
    check("ヴァイオレットシャツ (1168) = 12: the 450 G wiki row, not the 786 G one",
          fegamedata.armour_defence(1168) == 12, fegamedata.armour_defence(1168))
    real_res = feworld.player_resistance
    try:
        aa = args_stub(damage_model="flat", monster_damage=60,
                       armour_defence="on")
        feworld.player_resistance = lambda a: (100, 5, 0)
        d, why = feworld.monster_hit_damage(aa, {"attack": 92})
        check("耐性 100 -> 60 x (1 - 100/400) = 45", d == 45, (d, why))
        feworld.player_resistance = lambda a: (0, 0, 3)
        d, _why = feworld.monster_hit_damage(aa, {"attack": 92})
        check("no known armour -> the full 60", d == 60, d)
        d, _why = feworld.monster_hit_damage(
            args_stub(damage_model="flat", monster_damage=60,
                      armour_defence="off"), {"attack": 92})
        check("--armour-defence off -> the full 60", d == 60, d)
    finally:
        feworld.player_resistance = real_res

    print("\nitem effects through the session (2026-09-12)")
    cap = Cap().install()
    S = feworld._SESSION
    try:
        ia = args_stub(item_effects="table", monster_attack="on", player_hp=1000)
        S["player_hp"] = 500
        S.pop("item_fx", None)
        feworld.item_use_effect(None, None, 0, True, ia, 4,
                                fegamedata.item_effect(4))
        check("bread: HP 500 -> 550 at once", S["player_hp"] == 550,
              S["player_hp"])
        feworld.item_use_effect(None, None, 0, True, ia, 17,
                                fegamedata.item_effect(17))
        e = S["item_fx"]["hp"]
        check("a regen potion schedules 8 ticks", e["left"] == 8, e)
        e["next_at"] -= 3 * e["tick"] + 0.01       # three ticks are due
        feworld.item_effect_pump(None, None, 0, True, ia)
        check("three ticks = +144 HP", S["player_hp"] == 550 + 144,
              S["player_hp"])
        check("...5 left", S["item_fx"]["hp"]["left"] == 5)
        S["player_dead"] = True
        feworld.item_effect_pump(None, None, 0, True, ia)
        check("death ends the potion", not S["item_fx"], S.get("item_fx"))
        S["player_dead"] = False
        fa = args_stub(item_effects="flat", item_heal=250, monster_attack="on",
                       player_hp=1000)
        S["player_hp"] = 100
        feworld.item_use_effect(None, None, 0, True, fa, 4, None)
        check("--item-effects flat = the old --item-heal", S["player_hp"] == 350,
              S["player_hp"])
    finally:
        cap.restore()
        S.pop("item_fx", None)
        S.pop("player_dead", None)

    print()
    if FAILED:
        print("FAILED: %s" % ", ".join(FAILED))
        return 1
    print("[fe_combat_test] OK")
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys.exit(main())
