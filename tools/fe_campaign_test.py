#!/usr/bin/env python3
"""fe_campaign_test.py -- the WAR CYCLE driven the way prod drives it.

Run from services/:  python ../tools/fe_campaign_test.py

WHY. fecampaign shipped 2026-09-10 with a unit test that called declare() by
hand and a fake ctx that had a send() method. Prod has neither: nothing
declared a war (so --campaign on meant every field sat at peace while feworld's
own pump stood down), and the seam's real Ctx has reply(), not send() (so the
two requests the module answers raised into the seam's catch and the client
got nothing). This file drives the module through the SAME objects prod uses:
feworld.Ctx, the thread-local session, the pump, the module's own handlers.

  * a player standing in a peace war field DECLARES a war (--campaign-auto),
    as the attacker when their nation does not hold it
  * the client's countdown is the CAMPAIGN's clock -- 0x1018 carries the
    phase's remaining time, not --war-deadline-ms
  * a capital, a room, and a field somebody merely passes through do not
  * 0x2084 -> 0x1127 and 0x2018 -> 0x1020 go out through ctx.reply and the
    army choice is a sign-up
  * prep -> war sends 0x1015 + the side notify; war -> truce sends 0x1016 and
    the land notify and MOVES the territory; truce -> peace sends 0x1017 and
    the cycle restarts on its own, now with the player defending
  * a session entering mid-war gets its 0x1018 BEFORE the 0x1015
  * --capital-staff places the nine castle roles once, around the anchor,
    and every role has a line that fits one balloon
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

import fegamedata   # noqa: E402
import feworld      # noqa: E402

CHECKS = []
OUT = sys.__stdout__
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")      # feworld logs Japanese labels
    except (AttributeError, ValueError):
        pass


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-64s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label + (": " + detail if detail else ""))


def _args(**kw):
    a = dict(campaign="on", campaign_auto="on", war_cap=60, war_prep_ms=250,
             war_length_ms=250, war_truce_ms=250, war_decide="signups",
             campaign_file=None, territory_file=None, territory="",
             war="offensive", war_fields="0,0,0,0", war_deadline_ms=60000,
             war_clock="off", war_trace="off", war_start_fields="0,0",
             war_cycle="on", seq_mode="count", world_prefix=4, unit_id="0",
             campaign_notify_all=False, capital_staff="off", town_file="",
             events="on", event_greeting=None, capital="1:39",
             shop_types="weapon=1,armor=2,item=3,ring=4")
    a.update(kw)
    return types.SimpleNamespace(**a)


def main():
    feworld.load_extensions()
    import fecampaign
    tmp = os.environ.get("TEMP", ".")
    cf = os.path.join(tmp, "fe_camp_t_%d.json" % os.getpid())
    tf = os.path.join(tmp, "fe_terr_t_%d.json" % os.getpid())
    town = os.path.join(tmp, "fe_town_t_%d.json" % os.getpid())
    sf = os.path.join(tmp, "fe_spawn_t_%d.json" % os.getpid())
    for p in (cf, tf, town, sf):
        if os.path.exists(p):
            os.remove(p)
    args = _args(campaign_file=cf, territory_file=tf)
    sent = []
    saved = (feworld.send, feworld.nation_of)
    feworld.send = (lambda conn, outbound, body, mode, be, echo=True, prefix=4:
                    sent.append(struct.unpack_from(">IH", body, 0)[1]))
    feworld.nation_of = lambda a: 3            # the player fights for nation 3
    ctx = feworld.Ctx(None, None, "ecb", False, args)

    def session(**kw):
        feworld._TLS.session = dict(kw)
        return feworld._TLS.session

    def ids():
        return list(sent)

    try:
        feworld.territory_load(args)
        fecampaign.load(args)
        S = feworld._SESSION

        say("auto-declare")
        # 17 is nation 1's and borders 53 (nation 3's) -- a real front
        session(in_field=True, field=17, room=-1)
        fecampaign.pump(ctx)
        row = fecampaign.state_of(17)
        check("a player in a peace war field declares a war there",
              row["phase"] == fecampaign.PREP, repr(row))
        check("...as the ATTACKER, since nation 3 does not hold 17",
              row["atk"] == 3, repr(row))
        check("the client got its 0x1018 through feworld's own path",
              sent.count(0x1018) == 1, repr(ids()))
        check("...armed as prewar on the CAMPAIGN's clock, not --war-deadline-ms",
              S.get("war_phase") == "prewar"
              and S.get("war_deadline") is not None and S["war_deadline"] <= 250,
              "%r %r" % (S.get("war_phase"), S.get("war_deadline")))
        check("campaign_deadline_ms reads the same remaining time",
              feworld.campaign_deadline_ms(args, 17) is not None
              and feworld.campaign_deadline_ms(args, 17) <= 250)
        fecampaign.pump(ctx)
        check("a second pump in the same phase re-presents nothing",
              sent.count(0x1018) == 1, repr(ids()))

        # Reported in live testing 2026-09-11: "the second I spawn into the
        # world, I get the declaration of war screen". `!campaign auto off`
        # has to stop that live, without a server reconfigure + restart.
        say("!campaign auto")
        fecampaign.gm(ctx, "!campaign end 17")
        check("!campaign end puts 17 back to peace",
              fecampaign.phase_of(17) == fecampaign.PEACE)
        session(in_field=True, field=17, room=-1)
        fecampaign.pump(ctx)
        check("...but --campaign-auto declares it again on the next pump",
              fecampaign.phase_of(17) == fecampaign.PREP)
        fecampaign.gm(ctx, "!campaign end 17")
        check("!campaign auto off is answered",
              fecampaign.gm(ctx, "!campaign auto off") is True
              and args.campaign_auto == "off")
        session(in_field=True, field=17, room=-1)
        fecampaign.pump(ctx)
        check("...and now standing in a peaceful field declares NOTHING",
              fecampaign.phase_of(17) == fecampaign.PEACE)
        check("!campaign declare still starts one by hand",
              fecampaign.gm(ctx, "!campaign declare 17 3 force") is True
              and fecampaign.phase_of(17) == fecampaign.PREP)
        fecampaign.gm(ctx, "!campaign end 17")
        # WARNING: THE TRAP THAT COST THE FIRST TWO-PLAYER SESSION (live 2026-09-11):
        # feworld armed the War Prepare Window's countdown on field entry in a
        # field the campaign holds at PEACE, and its own pump stands down for
        # any campaign field -- so the countdown hit 00:00:00 and both players
        # sat in a modal window they could not leave.
        fecampaign.gm(ctx, "!campaign end 17")
        session(in_field=True, field=17, room=-1)
        del sent[:]
        feworld.war_notify(None, None, "ecb", False, args)
        check("a PEACE campaign field gets NO 0x1018 (no window to be stuck in)",
              sent == [], repr(ids()))
        fecampaign.gm(ctx, "!campaign declare 17 3 force")
        del sent[:]
        session(in_field=True, field=17, room=-1)
        feworld.war_notify(None, None, "ecb", False, args)
        check("...but a PREP field still arms it (a real war announces itself)",
              0x1018 in sent, repr(ids()))
        fecampaign.gm(ctx, "!campaign end 17")
        del sent[:]

        check("!campaign auto on puts it back",
              fecampaign.gm(ctx, "!campaign auto on") is True
              and args.campaign_auto == "on")
        session(in_field=True, field=17, room=-1)
        fecampaign.pump(ctx)
        check("...and the next pump declares again",
              fecampaign.phase_of(17) == fecampaign.PREP)

        session(in_field=True, field=39, room=-1)
        fecampaign.pump(ctx)
        check("a capital is never a front", fecampaign.phase_of(39) == fecampaign.PEACE)
        session(in_field=True, field=25, room=10)
        fecampaign.pump(ctx)
        check("a room is not a field", fecampaign.phase_of(25) == fecampaign.PEACE)
        session(in_field=False)
        fecampaign.pump(ctx)
        check("not in a field: nothing happens, nothing raised", True)

        say("the two requests, through the seam's real Ctx")
        session(in_field=True, field=17, room=-1)
        del sent[:]
        ok = fecampaign.on_proclamation(ctx, struct.pack(">HI", 0x2084, 17))
        check("0x2084 -> 0x1127 via ctx.reply (the module said ctx.send)",
              ok is True and ids() == [0x1127], repr(ids()))
        check("...with zero sign-ups so far",
              fecampaign.counts_of(17, args) == (0, 0, 60, 60))
        del sent[:]
        ok = fecampaign.on_decide_country(ctx, struct.pack(">HI", 0x2018, 0))
        check("0x2018 -> 0x1020 via ctx.reply", ok is True and ids() == [0x1020], repr(ids()))
        check("...and the army choice counted as an ATTACKER sign-up",
              fecampaign.counts_of(17, args) == (1, 0, 60, 60),
              repr(fecampaign.counts_of(17, args)))

        say("the phases, in the client's own message family")
        del sent[:]
        fecampaign.pump(ctx)                      # this session is presented
        time.sleep(0.3)
        fecampaign.pump(ctx)
        check("prep runs out into war", fecampaign.phase_of(17) == fecampaign.WAR)
        check("...0x1015 advanced the client and 0x101D named its side",
              0x1015 in sent and 0x101D in sent, repr(ids()))
        check("...the client's phase is war on the campaign's deadline",
              S.get("war_phase") == "war" and S.get("war_deadline") is not None
              and S["war_deadline"] <= 250, "%r" % S.get("war_deadline"))
        del sent[:]
        time.sleep(0.3)
        fecampaign.pump(ctx)
        check("war runs out into truce", fecampaign.phase_of(17) == fecampaign.TRUCE)
        check("...0x1016 TRUCE went to the client", 0x1016 in sent, repr(ids()))
        check("the attacker (1 sign-up to 0) took the field",
              feworld.territory_owner(17, args) == 3)
        check("...and the DEPRIVE land notify went to the winner",
              0x3036 in sent, repr(ids()))
        del sent[:]
        time.sleep(0.3)
        fecampaign.pump(ctx)
        row = fecampaign.state_of(17)
        # WARNING: 2026-09-11: this used to restart the cycle with a hostile
        # neighbour attacking the player's OWN nation -- a war nobody declared
        check("truce runs out: 0x1017 PEACE, and --campaign-auto does NOT "
              "declare against the player's own nation",
              0x1017 in sent and row["phase"] == fecampaign.PEACE
              and 0x1018 not in sent, "%r %r" % (ids(), row))
        check("...the war is over: nobody is enlisted any more",
              row["members"] == {} and row["grid"] is None, repr(row))

        say("entering mid-war")
        fecampaign._STATE[17]["phase"] = fecampaign.WAR
        fecampaign._STATE[17]["until"] = time.time() + 30
        session(in_field=True, field=17, room=-1)   # a fresh session, unarmed
        del sent[:]
        fecampaign.pump(ctx)
        check("an unarmed session gets 0x1018 first and the advance is held",
              ids() == [0x1018] and S.get("campaign_start_due") is True, repr(ids()))
        fecampaign.pump(ctx)
        check("...the next pump sends 0x1015 once the window is armed",
              ids()[:2] == [0x1018, 0x1015] and not S.get("campaign_start_due"),
              repr(ids()))

        say("the keeps")
        import fewar
        args.keeps, args.war_decide, args.combat, args.hit_damage = "on", "keeps", "on", 1600
        with fecampaign._LOCK:
            fecampaign._STATE.pop(17, None)
        fecampaign.save(args)
        session(in_field=True, field=17, room=-1)   # field NOT ready yet
        del sent[:]
        fecampaign.declare(args, 17)              # nation 3 holds 17 now: a
        fecampaign.pump(ctx)                      # neighbour attacks, we DEFEND
        row = fecampaign.state_of(17)
        check("a declared war seeds two keeps at --keep-hp",
              row["phase"] == fecampaign.PREP and row["atk"] != 3
              and row["keeps"] == {"def": [3000, 3000], "atk": [3000, 3000]}, repr(row))
        check("keeps are WITHHELD until the field is ready (building.pak mounted)",
              sent.count(0x1006) == 0 and not S.get("keeps"),
              "%r %r" % (ids(), S.get("keeps")))
        S["field_ready"] = True                   # first clock sample landed
        fecampaign.pump(ctx)
        check("...then both go out as type-1 buildings once the field is ready",
              sent.count(0x1006) == 2 and set(S.get("keeps", {})) == {2900, 2901},
              "%r %r" % (ids(), S.get("keeps")))
        check("the castle stands on SE's own grid for the map",
              feworld.keep_grids(17)["def"] == tuple(fegamedata.castle(fegamedata.areas()[17]["hmap"])))
        del sent[:]
        check("a hit in PREP is swallowed, nothing takes damage",
              feworld.keep_hit(None, None, "ecb", False, args, 2901) is True
              and 0x2024 not in sent, repr(ids()))
        with fecampaign._LOCK:
            fecampaign._STATE[17]["until"] = time.time() - 1
        fecampaign.pump(ctx)
        check("prep -> war with the keeps standing",
              fecampaign.phase_of(17) == fecampaign.WAR and 0x1015 in sent)
        del sent[:]
        time.sleep(0.3)
        feworld.keep_hit(None, None, "ecb", False, args, 2901)
        check("a hit on the attacker's keep pushes its NEW hp on 0x2024",
              ids() == [0x2024] and fecampaign.state_of(17)["keeps"]["atk"][0] == 1400,
              "%r %r" % (ids(), fecampaign.state_of(17)["keeps"]))
        feworld.keep_hit(None, None, "ecb", False, args, 2901)
        check("...a second notify of the same swing (<250 ms) is one swing",
              fecampaign.state_of(17)["keeps"]["atk"][0] == 1400)
        time.sleep(0.3)
        body = struct.pack(">IIHBI", 1, 2901, 1, 1, 0) + struct.pack(">fff", 0, 0, 0)
        fewar.on_hit_notify(ctx, struct.pack(">H", 0x2010) + body)
        check("the 0x2010 swing notify reaches the keep too",
              fecampaign.state_of(17)["keeps"]["atk"][0] == 0 and 0x1004 in sent, repr(ids()))
        del sent[:]
        fecampaign.pump(ctx)
        check("a fallen keep ends the war NOW: truce, the defender won",
              fecampaign.phase_of(17) == fecampaign.TRUCE
              and feworld.territory_owner(17, args) == 3 and 0x1016 in sent, repr(ids()))
        # 2026-09-12: this asserted the castle was 0x1004'd at truce. The
        # castle is PERMANENT (client book 52/53, the peacetime radar); only
        # the keep goes, and it already fell above.
        check("...and the surviving castle STAYS (it is permanent); the fallen "
              "keep is not re-served",
              0x1004 not in sent and 0x1006 not in sent
              and set(S.get("keeps") or {}) == {2900}
              and S.get("keeps_area") == 17,
              "%r %r" % (ids(), S.get("keeps")))
        with fecampaign._LOCK:
            fecampaign._STATE[17]["phase"] = fecampaign.PEACE
        del sent[:]
        fecampaign.pump(ctx)
        check("at PEACE the castle alone still stands, nothing re-sent",
              0x1004 not in sent and 0x1006 not in sent
              and set(S.get("keeps") or {}) == {2900},
              "%r %r" % (ids(), S.get("keeps")))
        real_owner = feworld.territory_owner
        feworld.territory_owner = (lambda a, ar=None:
                                   1 if int(a) == 17 else real_owner(a, ar))
        # this session (nation 3) is no longer the holder, so --campaign-auto
        # would DECLARE on this pump and serve the keep too -- not the point
        auto_was = getattr(args, "campaign_auto", None)
        args.campaign_auto = "off"
        try:
            del sent[:]
            fecampaign.pump(ctx)
            check("a NEW holder gets the castle re-served under ITS nation",
                  sent.count(0x1004) == 1 and sent.count(0x1006) == 1
                  and (S.get("keeps") or {}).get(2900, {}).get("nation") == 1,
                  "%r %r" % (ids(), S.get("keeps")))
        finally:
            feworld.territory_owner = real_owner
            args.campaign_auto = auto_was
        args.castle_always = "off"
        del sent[:]
        fecampaign.pump(ctx)
        check("--castle-always off takes it down at peace (the 09-11 rule)",
              0x1004 in sent and not S.get("keeps")
              and S.get("keeps_area") is None, "%r %r" % (ids(), S.get("keeps")))
        args.castle_always = "on"
        session(in_field=True, field=17, room=-1)
        fecampaign.pump(ctx)                       # field not ready: withheld
        withheld = not S.get("keeps")
        S["field_ready"] = True
        del sent[:]
        fecampaign.pump(ctx)
        check("a fresh session entering a PEACE field is served the castle "
              "(only), once the field is ready",
              withheld and sent.count(0x1006) == 1
              and set(S.get("keeps") or {}) == {2900},
              "%r %r %r" % (withheld, ids(), S.get("keeps")))
        with fecampaign._LOCK:
            fecampaign._STATE[17] = {"phase": fecampaign.WAR, "until": 0, "atk": 1,
                                     "signups": {}, "keeps": {"def": [2000, 3000], "atk": [3000, 3000]}}
        # WARNING: 2026-09-11 (audit item 5): this asserted the OPPOSITE -- a hurt
        # castle fell to a healthier keep at time-up. SE warsystem03: if
        # neither base falls before End, the DEFENDER wins.
        check("at time-up the DEFENDER wins, even with its castle hurt worse",
              fecampaign.decide(args, 17)[0] == "def")
        with fecampaign._LOCK:
            fecampaign._STATE[17]["keeps"] = {"def": [3000, 3000], "atk": [1, 3000]}
        check("...and a keep at 1 hp is not a fallen keep: still the defender",
              fecampaign.decide(args, 17)[0] == "def")
        with fecampaign._LOCK:
            fecampaign._STATE[17]["keeps"] = {"def": [0, 3000], "atk": [3000, 3000]}
        check("...only a castle at 0 gives the attacker the field",
              fecampaign.decide(args, 17)[0] == "atk")
        feworld.territory_set(17, 3, args)
        with fecampaign._LOCK:
            fecampaign._STATE.pop(17, None)
        feworld.territory_set(17, 1, args)
        args.keeps, args.war_decide = "off", "signups"
        fecampaign.declare(args, 17)              # a war for the office to report

        say("the arrival points")
        args.spawn_file, args.spawn_height, args.spawn_xyz = sf, "dat", (0.0, 0.0, 0.0)
        args.spawn_derive = "castle"
        feworld.spawn_load(args)
        check("a LIVE war's side outranks who holds the land (we attack 17 now)",
              fecampaign.state_of(17)["atk"] == 3 and feworld.spawn_side(args, 17) == "atk")
        with fecampaign._LOCK:
            fecampaign._STATE.pop(17, None)           # no war: the holder decides
        g = feworld.keep_grids(17)
        h = fegamedata.area_ground_height(17)
        feworld.territory_set(17, 1, args)
        # 2026-09-11: at PEACE everyone arrives on the castle side (early
        # FEZ); the holder rule these checks pin is now --peace-arrival holder
        check("at peace, nation 3 in nation 1's field arrives on the CASTLE side",
              feworld.spawn_side(args, 17) == "def")
        args.peace_arrival = "holder"
        check("nation 3 in nation 1's field is the ATTACKER (--peace-arrival holder)",
              feworld.spawn_side(args, 17) == "atk")
        exp_atk = (feworld.grid_to_world(g["atk"][0] + (-5 if g["atk"][0] > 128 else 5)),
                   float(h), feworld.grid_to_world(g["atk"][1]))
        check("...and arrives behind the attacker's keep at the map's height",
              feworld.spawn_for(args, 17) == exp_atk,
              "%r vs %r" % (feworld.spawn_for(args, 17), exp_atk))
        feworld.territory_set(17, 3, args)
        exp_def = (feworld.grid_to_world(g["def"][0] + (-5 if g["def"][0] > 128 else 5)),
                   float(h), feworld.grid_to_world(g["def"][1]))
        check("holding the field, they DEFEND and arrive beside the castle",
              feworld.spawn_side(args, 17) == "def" and feworld.spawn_for(args, 17) == exp_def,
              repr(feworld.spawn_for(args, 17)))
        del args.peace_arrival                    # back to the default (castle)
        # the keeps are at opposite ends now (reflected through centre), so the
        # two arrival points are far apart, not the old 24-cell straight line
        sep_cells = (abs(g["atk"][0] - g["def"][0]) ** 2
                     + abs(g["atk"][1] - g["def"][1]) ** 2) ** 0.5
        check("castle and keep are at opposite ends (>= 60 cells apart)",
              sep_cells >= 60, "%.0f cells" % sep_cells)
        check("both arrival points sit on the same map height",
              exp_atk[1] == exp_def[1])
        check("a capital is never derived",
              feworld.spawn_for(args, 39)[0] == 0.0 and feworld.derived_spawn(args, 39, "def") is None)
        check("a map with no height sample (the starting land, hmap 10) still derives",
              fegamedata.area_ground_height(5) is None
              and feworld.derived_spawn(args, 5, "def") is not None
              and feworld.derived_spawn(args, 5, "def")[1] == 44.0
              and feworld.derived_spawn(args, 5, "def")[0] == feworld.grid_to_world(33 + 5),
              repr(feworld.derived_spawn(args, 5, "def")))
        feworld.spawn_add(args, 17, (1.0, 2.0, 3.0), tag="def")
        check("a `!spawn` row for the side still wins",
              feworld.spawn_for(args, 17) == (1.0, 2.0, 3.0))
        feworld.spawn_drop(args, 17)
        args.spawn_derive = "off"
        check("--spawn-derive off restores the one-point fallback",
              feworld.spawn_for(args, 17) == (0.0, float(h), 0.0))
        args.spawn_derive = "castle"
        feworld.territory_set(17, 1, args)
        fecampaign.declare(args, 17)              # a war again, for the office

        say("the knob")
        args.campaign_auto = "off"
        session(in_field=True, field=28, room=-1)
        fecampaign.pump(ctx)
        check("--campaign-auto off: a peace field stays at peace",
              fecampaign.phase_of(28) == fecampaign.PEACE)
        args.campaign_auto = "on"

        say("the war office")
        rep = fecampaign.report(args)
        name17 = fegamedata.area_names_en().get(17, "")
        check("the report names the field and its phase",
              name17 and name17 in rep and "war" in rep.lower(), rep)
        karin = feworld.event_script_for(args, {"name": "Manager_Karin", "script": 3301})
        check("Manager_Karin says the report and ends the conversation",
              karin[0][0] == "text" and rep in karin[0][1]
              and karin[-1] == ("window", feworld.EV_WIN_END, 0), repr(karin))

        say("the castle staff")
        sargs = _args(capital_staff="on", town_file=town, campaign_file=cf,
                      territory_file=tf)
        check("no anchor row: refuses rather than guesses",
              feworld.capital_staff_fill(sargs) == 0)
        feworld.town_add(sargs, "room:15", "npcs",
                         {"model": 238, "kind": 1, "level": 1, "x": 10.0,
                          "y": 2.5, "z": 5.0, "yaw": 90, "name": "Item_Shop",
                          "script": 2111, "event": "say:out;goto:39"})
        n = feworld.capital_staff_fill(sargs)
        rows = feworld.town_get(sargs, "room:15", "npcs")
        check("nine roles placed around the anchor", n == 9 and len(rows) == 10, "%d %d" % (n, len(rows)))
        check("...on the anchor's floor, each at its own spot",
              all(abs(r["y"] - 2.5) < 1e-6 for r in rows[1:])
              and len({(r["x"], r["z"]) for r in rows}) == 10)
        check("...idempotent", feworld.capital_staff_fill(sargs) == 0
              and len(feworld.town_get(sargs, "room:15", "npcs")) == 10)
        names = {r["name"] for r in rows[1:]}
        want = {r["name"] for r in fegamedata.capital_roster(39)
                if "Shop" not in r["name"] and r["name"] != "Inn_Master"}
        check("the nine are --capital 39's non-shop roles, tavern excluded",
              names == want and "Jade" in names, repr(names))
        other = feworld.event_script_for(sargs, {"name": "Lesley", "script": 2101})
        check("another capital's greeter (a different NAME, same slot) has the line",
              other[0][1] == feworld.ROLE_LINES[(2, 1)], repr(other))
        for r in rows[1:]:
            steps = feworld.event_script_for(sargs, r)
            check("%s has a script that fits one balloon and ends" % r["name"],
                  steps and steps[0][0] == "text"
                  and len(steps[0][1].encode("cp932", "replace")) <= feworld.EV_TEXT_MAX
                  and steps[-1][1] in (feworld.EV_WIN_END,),
                  repr(steps))
        check("nobody in the castle says the placeholder 'Good day.'",
              all(feworld.event_script_for(sargs, r)[0][1] != "Good day."
                  for r in rows[1:]))
    finally:
        feworld.send, feworld.nation_of = saved
        for p in (cf, tf, town, sf):
            try:
                os.remove(p)
            except OSError:
                pass

    bad = [l for l, ok in CHECKS if not ok]
    say("[fe_campaign_test] %s -- %d checks" % ("FAIL" if bad else "OK", len(CHECKS)))
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("[fe_campaign_test] FAIL: %s" % e)
        sys.exit(1)
