#!/usr/bin/env python3
"""fe_summon_test.py -- SUMMONS (the client's METAMORPHOSIS) under the 2006 rules.

Run from services/:  python ../tools/fe_summon_test.py

2026-09-11. Until today every 0x2044 got 0x106B "Conditions unmet", and the
`ok` test mode read the request's u16 as a form id when it is the building's
SUMMON SKILL (FE_BUILDING_DATA +0xde: 951..955), released a summon with a form
0 that changes no model and clears HUM, and fed nothing to HP or crystals.
This drives feunit's rules through the objects prod uses (feworld.Ctx, the
thread-local sessions, fecampaign's war row and keeps, fewar's real 0x2007
builds, the pump) and decodes every body the way the client's arms read it:

  * 0x2044 [u16 skill][u8 lv][u32 building] -> 0x106B [u32 bld][u32 code]
    with the client's own codes (2 fallen, 5 already, 6 conditions, 7 wrong
    monster), or 0x2035 crystal (debited) + 0x106A [u32 bld] + 0x203D
    [u32 0x10][u16 1<<form] + 0x2024 [u32 0x6][u32 0][i16 max][i16 cur]
  * the rules: your side's building, at WAR only, in range, the crystal price,
    Wraith 1 alive per side, the Dragon Altar/Alchemy Labo summon nothing, the
    Chimera at the keep with Chimera Blood (one per base bar lost), the Dragon
    rising on death with the Dragon Soul on the losing side
  * HP% carries over both ways; the end: 0x2045 -> 0x106C + 0x203D 0x200 +
    the human HP; death, a fallen building and a left field end it too
  * 0x1070 [u16 N]{u16 form, u32 gold, u32 crystal} per field entry
  * the per-war tally lives in the campaign row, survives a restart and is
    cleared when the war is over
"""
import os
import queue
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
                          ("  " + detail) if (detail and not cond) else ""))


def _args(**kw):
    a = dict(campaign="on", campaign_auto="off", war_cap=50, war_prep_ms=60000,
             war_length_ms=600000, war_truce_ms=60000, war_decide="keeps",
             campaign_file=None, territory_file=None, territory="",
             war="offensive", war_fields="0,0,0,0", war_deadline_ms=60000,
             war_clock="off", war_trace="off", war_start_fields="0,0",
             war_cycle="on", seq_mode="count", world_prefix=4, unit_id="0",
             campaign_notify_all=False, keeps="on", keep_hp=3000,
             keep_types="20:16", keep_models="4:16",
             declare_accepts=0, declare_rejects=3, declare_timeout_ms=60000,
             beginner_fields="on", keep_min_cells=30, foreign_join="smaller",
             war_ring_win=2, war_ring_dmg=1000, king_message=[],
             force_population="on", war_crystals=0, crystal=100, ring=1,
             # this suite tests SUMMON rules against a stored 100 crystals;
             # under the 2006 economy (--crystal-reset war, the default) a
             # player only holds what they DREW in the live war, which
             # fe_crystal_test covers -- so the economy is held still here
             crystal_reset="off",
             gold=None, total_score=None, crystal_draw=1, crystal_war_max=20,
             crystal_carry_max=50, build="real", build_costs="off",
             build_phase="war", build_caps="4=1", building_base=3000,
             building_hp=(100, 100), combat="on", hit_damage=400, world="dat",
             islands=6, groups=1, field_nations=None, capital="1:39",
             player_hp=1000, monster_attack="on", revive_secs=8,
             # feunit's own, at their defaults (the parser owns the real ones)
             metamorphosis="rules", metamorphosis_ng=6, metamorphosis_form=None,
             metamorphosis_cost="", summon_cost="2006",
             summon_hp="GIANT=5400,WRAITH=2300,CHIMERA=4300,DRAGON=8000",
             summon_knight_hp="1800:2900:10", summon_altars="off",
             summon_bars=3, summon_dragon_death=0.25, summon_dragon_floor=0.5,
             summon_phase="war", summon_range=40.0,
             summon_alive_cap="WRAITH=1,DRAGON=3,CHIMERA=1",
             summon_war_cap="CHIMERA=2", summon_dragon_gap=0.8,
             summon_chimera_drain=10.0, summon_items="", crystal_heal=0)
    a.update(kw)
    return types.SimpleNamespace(**a)


SENT = []            # (charid of the sending session, mid, body)
CHARS = {}           # (account, charid) -> stored character


def _send(conn, outbound, body, mode, be, echo=True, prefix=4):
    _unit, mid = struct.unpack_from(">IH", body, 0)
    SENT.append((feworld._SESSION.get("charid"), mid, body[6:]))


def _char():
    s = feworld._SESSION
    return CHARS.setdefault((s.get("account"), s.get("charid")), {})


class Player:
    """One session in feworld's chat room (so ext_sessions sees it)."""

    def __init__(self, name, charid, nation, area, args):
        self.name, self.charid, self.args = name, charid, args
        self.key = ("fake", charid)
        self.session = {"account": "acct-" + name, "charid": charid,
                        "in_field": True, "field": area, "room": -1,
                        "campaign_nation": nation, "field_ready": True,
                        "player_hp": 1000, "morphcost_sent": True}
        CHARS[("acct-" + name, charid)] = {"charid": charid, "force": nation,
                                           "crystal": 100}
        with feworld._CHAT_ROOM_LOCK:
            feworld._CHAT_ROOM[self.key] = {"q": queue.Queue(),
                                            "session": self.session,
                                            "name": name, "ext": queue.Queue()}

    def act(self, fn, *a):
        saved = getattr(feworld._TLS, "session", None)
        feworld._TLS.session = self.session
        try:
            return fn(feworld.Ctx(None, None, "ecb", False, self.args), *a)
        finally:
            feworld._TLS.session = saved

    def send(self, mid, body=b""):
        del SENT[:]
        self.act(lambda ctx: feworld.EXT_HANDLERS[mid](
            ctx, struct.pack(">H", mid) + body))
        return self.out()

    def pump(self):
        import feunit
        del SENT[:]
        self.act(feunit.pump)
        return self.out()

    def out(self):
        return [(m, b) for c, m, b in SENT if c == self.charid]

    def stand_at(self, grid):
        self.session["cpos"] = (feworld.grid_to_world(grid[0]), 20.0,
                                feworld.grid_to_world(grid[1]))

    def crystal(self):
        return CHARS[("acct-" + self.name, self.charid)].get("crystal")

    def leave(self):
        with feworld._CHAT_ROOM_LOCK:
            feworld._CHAT_ROOM.pop(self.key, None)


def summon(p, skill, bld):
    return p.send(0x2044, struct.pack(">HBI", skill, 1, bld))


def ng(out):
    """The 0x106B code in `out`, or None."""
    for m, b in out:
        if m == 0x106B:
            assert len(b) == 8, len(b)
            return struct.unpack(">II", b)[1]
    return None


def ids(out):
    return [m for m, _b in out]


def body(out, mid):
    return [b for m, b in out if m == mid]


def form_of(out):
    b = body(out, 0x203D)[-1]
    assert len(b) == 6, len(b)
    mask, word = struct.unpack(">IH", b)
    assert mask == 0x10
    return word


def hp_of(out):
    """The last 0x2024 HP push as {bit: value} (maskA 0x2 max, 0x4 cur)."""
    b = [x for x in body(out, 0x2024) if struct.unpack_from(">I", x, 0)[0] & 0x6][-1]
    ma, mb = struct.unpack_from(">II", b, 0)
    vals, o = {}, 8
    for bit in (0x2, 0x4):
        if ma & bit:
            vals[bit] = struct.unpack_from(">h", b, o)[0]
            o += 2
    assert o == len(b) and mb == 0, (o, len(b))
    return vals


def set_keep(fecampaign, area, side, hp):
    with fecampaign._LOCK:
        k = fecampaign._STATE[area]["keeps"][side]
        k[0] = hp


def main():
    feworld.load_extensions()
    import fecampaign
    import fewar
    import feunit
    tmp = os.environ.get("TEMP", ".")
    cf = os.path.join(tmp, "fe_summ_c_%d.json" % os.getpid())
    tf = os.path.join(tmp, "fe_summ_t_%d.json" % os.getpid())
    for p in (cf, tf):
        if os.path.exists(p):
            os.remove(p)
    args = _args(campaign_file=cf, territory_file=tf)
    saved = (feworld.send, feworld._load_char_field, feworld._store_char_field,
             fecampaign._update_char)
    feworld.send = _send
    feworld._load_char_field = lambda a, k, d=None: _char().get(k, d)
    feworld._store_char_field = lambda a, k, v: (_char().__setitem__(k, v), True)[1]
    fecampaign._update_char = lambda account, charid, mutate: False
    players = []
    try:
        feworld.territory_load(args)
        fecampaign.load(args)
        area = 17
        holder = feworld.territory_owner(area, args)
        atk_nation = 3 if holder != 3 else 2
        alice = Player("Alice", 11, atk_nation, area, args)
        bob = Player("Bob", 12, atk_nation, area, args)
        eve = Player("Eve", 15, holder, area, args)
        players = [alice, bob, eve]
        row = fecampaign.declare(args, area, atk_nation)
        check("setup: a war declared on area 17 (attacker %d, holder %d)"
              % (atk_nation, holder), row is not None and row["keeps"])
        for p in players:
            p.act(fecampaign.pump)
        check("setup: every session was shown both bases (castle 2900, keep 2901)",
              all(sorted(p.session.get("keeps") or {}) == [2900, 2901]
                  for p in players), repr([p.session.get("keeps") for p in players]))
        grids = feworld.keep_grids(area)
        alice.stand_at(grids["atk"])
        bob.stand_at(grids["atk"])
        eve.stand_at(grids["def"])

        say("the Knight at your own base")
        out = summon(alice, 954, 2901)
        check("WAR PREP: the keep refuses -> 0x106B code 6 (--summon-phase war)",
              ng(out) == 6 and alice.crystal() == 100
              and not alice.session.get("morph"), repr(out))
        with fecampaign._LOCK:
            fecampaign._STATE[area]["phase"] = fecampaign.WAR
            fecampaign._STATE[area]["until"] = time.time() + 600
        alice.session["cpos"] = (alice.session["cpos"][0] + 80.0, 20.0,
                                 alice.session["cpos"][2])
        out = summon(alice, 954, 2901)
        check("at war but 80u away -> code 6 (--summon-range 40; the client "
              "itself stops at 35)", ng(out) == 6 and alice.crystal() == 100,
              repr(out))
        alice.stand_at(grids["atk"])
        alice.session["player_hp"] = 500                  # half the human's
        out = summon(alice, 954, 2901)
        check("at war, beside the keep: 0x2035, 0x106A, 0x203D, 0x2024 -- in "
              "that order", ids(out) == [0x2035, 0x106A, 0x203D, 0x2024],
              repr([hex(m) for m in ids(out)]))
        check("...40 crystals debited: stored 100 -> 60, served on 0x2035 bit 1",
              alice.crystal() == 60
              and struct.unpack(">II", body(out, 0x2035)[0]) == (0x2, 60))
        check("...0x106A = [u32 2901] (the summon's building, -> [player+0x136c])",
              body(out, 0x106A) == [struct.pack(">I", 2901)])
        check("...0x203D form 0x008 = KNIT (the keep's +0xe2 = 3)",
              form_of(out) == 0x8)
        check("...HP: a Lv1 Knight has 1800, and 50% of it (900) carried over",
              hp_of(out) == {0x2: 1800, 0x4: 900}, repr(hp_of(out)))
        check("...the session is the Knight; its HP max reads 1800 everywhere",
              alice.session["morph"]["form"] == 3
              and alice.act(lambda ctx: feworld.player_hp_max(args)) == 1800)
        check("...and the war row counts one attacker Knight",
              fecampaign.summon_count(area, "atk", 3) == 1)
        out = summon(alice, 954, 2901)
        check("a second request while summoned -> code 5 'Already summoning'",
              ng(out) == 5 and alice.crystal() == 60)
        out = summon(eve, 954, 2901)
        check("a DEFENDER at the attackers' keep -> code 6 (not her side)",
              ng(out) == 6 and eve.crystal() == 100, repr(out))
        out = summon(bob, 955, 2901)
        check("the keep asked for the Wraith's skill 955 -> code 7 (the client's "
              "'building offers a different monster')", ng(out) == 7)
        out = summon(bob, 954, 4242)
        check("a building this session was never shown -> code 2", ng(out) == 2)
        alice.session["player_hp"] = 900                   # still 50%
        out = alice.send(0x2045)
        check("0x2045 'Unsummon' -> 0x106C, 0x203D, 0x2024",
              ids(out) == [0x106C, 0x203D, 0x2024], repr(ids(out)))
        check("...0x106C is header-only (the client's REVERT TO HUMAN 0x0506c930)",
              body(out, 0x106C) == [b""])
        check("...0x203D form 0x200 = HUM, not 0 (0 changed no model, cleared HUM)",
              form_of(out) == 0x200)
        check("...the human max 1000 and the Knight's 50% carried back: 500",
              hp_of(out) == {0x2: 1000, 0x4: 500} and not alice.session.get("morph"))
        out = summon(eve, 954, 2900)
        check("the defender at HER castle (--keep-types 20:16, a Castle row): the "
              "Knight", ng(out) is None and form_of(out) == 0x8
              and eve.session["morph"]["side"] == "def")
        eve.send(0x2045)
        args.keep_types = "4:16"
        out = summon(eve, 955, 2900)
        check("with --keep-types 4:16 the castle IS a Gate of Hades row -> the "
              "Wraith (the default was 4:16 until 2026-09-12; SE warsystem02 "
              "gives the defender a CASTLE, and a Castle offers the Knight)",
              ng(out) is None and form_of(out) == 0x10)
        eve.send(0x2045)
        args.keep_types = "20:16"

        say("a War Craft and a Gate of Hades, built with fewar's real 0x2007")

        def build(p, btype, model):
            g = feworld.world_to_grid(p.session["cpos"][0]), \
                feworld.world_to_grid(p.session["cpos"][2])
            b = struct.pack(">HHHHHB", btype, model, g[0], 0, g[1], 0) + \
                struct.pack(">fff", 0.0, 0.0, 0.0)
            out = p.send(0x2007, b)
            new = [o for o in (p.session.get("war_buildings") or {})
                   if p.session["war_buildings"][o]["type"] == btype]
            return out, (max(new) if new else None)
        out, craft = build(alice, 5, 5)
        check("Alice builds a War Craft: OK, registered for the attackers",
              ids(out)[:2] == [0x1009, 0x1006] and craft is not None
              and alice.session["war_buildings"][craft]["side"] == "atk", repr(out))
        out = summon(alice, 951, craft)
        check("the Giant from it: 30 crystals (60 -> 30), form 0x001 GIA",
              ng(out) is None and alice.crystal() == 30 and form_of(out) == 0x1)
        check("...HP 5400, 50% carried (2700)", hp_of(out) == {0x2: 5400, 0x4: 2700})
        alice.send(0x2045)
        out, gate = build(alice, 4, 4)
        check("a Gate of Hades", gate is not None and gate != craft)
        out = summon(alice, 955, gate)
        check("the Wraith costs 50 and Alice carries 30 -> code 6, nothing taken",
              ng(out) == 6 and alice.crystal() == 30 and not alice.session.get("morph"))
        CHARS[("acct-Alice", 11)]["crystal"] = 100
        out = summon(alice, 955, gate)
        check("with 100: the Wraith (100 -> 50), form 0x010, HP 2300 at 50%",
              ng(out) is None and alice.crystal() == 50 and form_of(out) == 0x10
              and hp_of(out) == {0x2: 2300, 0x4: 1150})
        bob.session.setdefault("war_buildings", {})[gate] = \
            dict(alice.session["war_buildings"][gate])
        out = summon(bob, 955, gate)
        check("Bob, same side, a second Wraith while hers stands -> code 6 "
              "(SE 4/25: one at a time)", ng(out) == 6 and bob.crystal() == 100)
        del SENT[:]
        alice.act(lambda ctx: fewar.destroy_building(ctx, gate))
        out = alice.pump()
        check("her Gate destroyed: the pump ends the Wraith (0x106C + form 0x200 "
              "+ HP) -- 'the Wraith returns to the underworld'",
              ids(out) == [0x106C, 0x203D, 0x2024] and not alice.session.get("morph"),
              repr(ids(out)))
        out = alice.send(0x2045)
        check("...the client's own 0x2045 after it (the building's destructor "
              "sends one) finds nothing left to end", out == [])
        out = summon(bob, 955, gate)
        check("now Bob's Wraith is allowed", ng(out) is None and form_of(out) == 0x10)
        bob.send(0x2045)

        say("the Dragon Altar and Alchemy Labo; the Chimera; the Dragon")
        out, altar = build(alice, 1, 1)
        out = summon(alice, 952, altar)
        check("the Dragon Altar summons nothing in 2006 (fewiki: 未実装) -> code 6",
              ng(out) == 6 and alice.crystal() == 50)
        args.summon_altars = "on"
        out = summon(alice, 952, altar)
        check("--summon-altars on: its row applies, but the attackers are not "
              "losing -> code 6", ng(out) == 6)
        args.summon_altars = "off"
        args.summon_items = "CHIMERA=900,DRAGON=901"
        CHARS[("acct-Alice", 11)]["items"] = [[70001, 900, 1, 1]]
        out = summon(alice, 954, 2901)
        check("Chimera Blood at a full-HP keep: no bar lost, so the KNIGHT",
              ng(out) is None and form_of(out) == 0x8
              and CHARS[("acct-Alice", 11)]["items"] == [[70001, 900, 1, 1]])
        alice.send(0x2045)
        set_keep(fecampaign, area, "atk", 1800)            # 40% lost = 1 bar
        CHARS[("acct-Alice", 11)]["crystal"] = 100
        out = summon(alice, 954, 2901)
        check("one bar of the keep lost + Chimera Blood: the CHIMERA at the "
              "keep (form 0x004), 40 crystals, the Blood used up (0x107A)",
              ng(out) is None and form_of(out) == 0x4 and alice.crystal() == 60
              and 0x107A in ids(out)
              and not CHARS[("acct-Alice", 11)].get("items"), repr(ids(out)))
        check("...HP 4300 at 50%", hp_of(out) == {0x2: 4300, 0x4: 2150})
        m = alice.session["morph"]
        m["drain_t"] -= 10.0
        m["drain_pushed"] -= 10.0
        out = alice.pump()
        check("the Chimera melts: 10 s at 10 HP/s -> 0x2024 current 2050",
              hp_of(out) == {0x4: 2050}, repr(out))
        alice.send(0x2045)
        CHARS[("acct-Alice", 11)]["items"] = [[70002, 900, 1, 1]]
        out = summon(alice, 954, 2901)
        check("a second Chimera with only one bar lost: the Knight instead, the "
              "Blood kept", form_of(out) == 0x8
              and CHARS[("acct-Alice", 11)]["items"] == [[70002, 900, 1, 1]])
        alice.send(0x2045)
        args.summon_dragon_death = 1.0
        CHARS[("acct-Alice", 11)]["items"] = [[70003, 901, 1, 1]]
        alice.session["player_dead"] = True
        alice.session["revive_at"] = time.monotonic() + 8
        out = alice.pump()
        check("DEAD with the Dragon Soul, the keep 1.2 bars behind: rises as the "
              "DRAGON -- alive (CONDITION), the Soul used, form 0x002, full 8000",
              not alice.session.get("player_dead") and form_of(out) == 0x2
              and hp_of(out) == {0x2: 8000, 0x4: 8000} and 0x107A in ids(out)
              and body(out, 0x2024)[0][:4] == struct.pack(">I", 0x100000),
              repr(ids(out)))
        check("...counted for the attackers (Dragon: 3 per side)",
              fecampaign.summon_count(area, "atk", 1) == 1)
        alice.session["player_dead"] = True
        out = alice.pump()
        check("the Dragon KILLED: the form ends (0x106C, 0x200) and only the HP "
              "MAX goes back (the revive restores the rest)",
              ids(out) == [0x106C, 0x203D, 0x2024]
              and hp_of(out) == {0x2: 1000} and not alice.session.get("morph"))
        out = alice.pump()
        check("...and the same death does not roll the Dragon again", out == [])
        alice.session["player_dead"] = False
        alice.session["player_hp"] = 1000
        args.summon_items = ""

        say("the field, the war's end, the table, the escape hatch")
        CHARS[("acct-Alice", 11)]["crystal"] = 100
        summon(alice, 954, 2901)
        alice.session["in_field"] = False
        out = alice.pump()
        check("leaving the field while a Knight: 0x106C + form 0x200, no HP push "
              "off-field", ids(out) == [0x106C, 0x203D], repr(ids(out)))
        alice.session["in_field"] = True
        out = alice.pump()
        check("...the human HP max follows on the next field (with that field's "
              "0x1070)", ids(out) == [0x2024, 0x1070] and hp_of(out) == {0x2: 1000},
              repr(ids(out)))
        alice.session.pop("morphcost_sent", None)
        out = alice.pump()
        rows = body(out, 0x1070)
        n = struct.unpack_from(">H", rows[0], 0)[0] if rows else 0
        got = [struct.unpack_from(">HII", rows[0], 2 + 10 * i) for i in range(n)]
        check("0x1070 on field entry: {form, gold 0, crystal} for GIANT 30, "
              "DRAGON 0, CHIMERA 40, KNIGHT 40, WRAITH 50",
              got == [(0, 0, 30), (1, 0, 0), (2, 0, 40), (3, 0, 40), (4, 0, 50)]
              and len(rows[0]) == 2 + 10 * n, repr(got))
        fecampaign.save(args)
        fecampaign.load(args)
        check("the per-war tally survives a restart (the campaign store)",
              fecampaign.summon_count(area, "atk", 3) >= 3
              and fecampaign.summon_count(area, "atk", 2) == 1)
        with fecampaign._LOCK:
            fecampaign._STATE[area]["phase"] = fecampaign.TRUCE
        alice.act(lambda ctx: fecampaign._advance(ctx, args, area, time.time()))
        check("...and is cleared when the war is over (Truce -> Peace)",
              fecampaign.summon_count(area, "atk", 3) == 0
              and fecampaign.state_of(area).get("summoned") == {})
        args.metamorphosis = "ng"
        out = summon(alice, 954, 2901)
        check("--metamorphosis ng (the escape hatch): code 6, always", ng(out) == 6)
        args.metamorphosis = "rules"
        check("--summon-knight-hp: 2900 from Lv10",
              feunit.summon_hp(_args(summon_knight_hp="1800:2900:1"), 3) == 2900)
    finally:
        (feworld.send, feworld._load_char_field, feworld._store_char_field,
         fecampaign._update_char) = saved
        for p in players:
            p.leave()
        for p in (cf, tf):
            try:
                os.remove(p)
            except OSError:
                pass

    bad = [l for l, ok in CHECKS if not ok]
    say("[fe_summon_test] %s -- %d checks%s" % (
        "FAIL" if bad else "OK", len(CHECKS),
        (": " + "; ".join(bad)) if bad else ""))
    return 1 if bad else 0


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
