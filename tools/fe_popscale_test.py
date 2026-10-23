#!/usr/bin/env python3
"""fe_popscale_test.py -- --pop-scale: a small war scales toward SE's values.

Run from services/:  python ../tools/fe_popscale_test.py

  * the factor is a side's players online / --pop-scale-full, floored at
    --pop-scale-floor, and exactly 1.0 (SE's values) at full strength
  * --pop-scale off, or a value left out of --pop-scale-values, changes nothing
  * a side is its enlisted members with a live session; before anybody has
    enlisted, its nation online; --pop-scale-extra adds pretend players
  * the summon price (rounded up), the keeps at the declare, the war length
    (the two sides' average) and the crystals per draw / kill (up to 3x)
"""
import os
import sys
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
    say("  %-64s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))


def _args(**kw):
    a = dict(campaign="on", pop_scale="on", pop_scale_full=20,
             pop_scale_floor=0.34, pop_scale_values="summon,hp,length,crystal",
             pop_scale_extra="", war_length_ms=3600000, keeps="on",
             keep_hp=3000, summon_cost="2006")
    a.update(kw)
    return types.SimpleNamespace(**a)


def main():
    feworld.load_extensions()
    import fecampaign
    import feunit
    saved = (fecampaign._nation_online, fecampaign._live_charids)
    online = {1: 2, 3: 9}                    # nation -> players online
    live = set()
    fecampaign._nation_online = lambda n: online.get(int(n), 0)
    fecampaign._live_charids = lambda: set(live)
    area = 17
    try:
        say("the factor")
        a = _args()
        f = lambda n: fecampaign.pop_factor(a, area, "atk", n=n)   # noqa: E731
        check("10 of 20 a side: x0.5", f(10) == 0.5, repr(f(10)))
        check("2 a side: the floor, x0.34", f(2) == 0.34, repr(f(2)))
        check("20 a side: SE's values (x1.0)", f(20) == 1.0)
        check("40 a side: still x1.0, never above", f(40) == 1.0)
        off = _args(pop_scale="off")
        check("--pop-scale off: x1.0 whatever the count",
              fecampaign.pop_factor(off, area, "atk", n=1) == 1.0)
        check("no side (not in this war): x1.0",
              fecampaign.pop_factor(a, area, None, n=1) == 1.0)
        check("a value left out of --pop-scale-values is not scaled",
              not fecampaign.pop_scales(_args(pop_scale_values="summon"), "hp")
              and fecampaign.pop_scales(_args(pop_scale_values="summon"), "summon"))

        say("the summon price")
        k = feunit.form_id("KNIGHT")
        check("Knight at x1.0 = 40 (SE)", feunit.summon_cost(a, k)[0] == 40)
        check("Knight at x0.5 = 20: one full draw pays it",
              feunit.summon_cost(a, k, 0.5)[0] == 20)
        check("Knight at the floor = 14 (rounded UP)",
              feunit.summon_cost(a, k, 0.34)[0] == 14,
              repr(feunit.summon_cost(a, k, 0.34)))
        check("the 0x1070 table carries the scaled price",
              (k, 0, 20) in feunit._cost_rows(a, 0.5), repr(feunit._cost_rows(a, 0.5)))
        check("the Dragon's 0 stays 0", feunit.summon_cost(a, 1, 0.34)[0] == 0)

        say("who counts")
        with fecampaign._LOCK:
            fecampaign._STATE[area] = fecampaign._row_from(
                {"phase": fecampaign.WAR, "until": 0, "atk": 1})
        owner = fecampaign.fw.territory_owner
        fecampaign.fw.territory_owner = lambda ar, args=None: 3
        try:
            check("nobody enlisted: the attacker side is nation 1 online (2)",
                  fecampaign.side_players(a, area, "atk") == 2)
            check("...and the defender side nation 3 online (9)",
                  fecampaign.side_players(a, area, "def") == 9)
            check("--pop-scale-extra 8:2 adds pretend players per side",
                  fecampaign.side_players(_args(pop_scale_extra="8:2"), area, "atk") == 10
                  and fecampaign.side_players(_args(pop_scale_extra="8:2"), area, "def") == 11)
            check("--pop-scale-extra 5 adds to both",
                  fecampaign.side_players(_args(pop_scale_extra="5"), area, "def") == 14)
            with fecampaign._LOCK:
                fecampaign._STATE[area]["members"] = {
                    "x|11": {"c": 11, "side": "atk"}, "x|12": {"c": 12, "side": "atk"},
                    "y|21": {"c": 21, "side": "def"}}
            live.update({11, 21})
            check("enlisted: only members ONLINE count (1 of 2 attackers)",
                  fecampaign.side_players(a, area, "atk") == 1)
            check("...1 defender", fecampaign.side_players(a, area, "def") == 1)

            say("the war length")
            check("1 v 1: the floor, 60 min x0.34 = 20.4 min",
                  abs(fecampaign.war_length_s(a, area) - 3600 * 0.34) < 0.01,
                  repr(fecampaign.war_length_s(a, area)))
            check("pretend 19:19 -> 20 a side: the full hour",
                  fecampaign.war_length_s(_args(pop_scale_extra="19"), area) == 3600)
            check("--pop-scale-values without length: the full hour",
                  fecampaign.war_length_s(_args(pop_scale_values="summon"), area) == 3600)
            # operator 2026-10-23: a short war only hurts the attacker
            dflt = _args()
            del dflt.pop_scale_values
            check("the DEFAULT values leave the war its full hour, even 1 v 1",
                  fecampaign.war_length_s(dflt, area) == 3600
                  and fecampaign.pop_scales(dflt, "summon")
                  and fecampaign.pop_scales(dflt, "hp")
                  and fecampaign.pop_scales(dflt, "crystal"),
                  repr(fecampaign.war_length_s(dflt, area)))

            say("the keeps at the declare")
            with fecampaign._LOCK:
                fecampaign._STATE.pop(area, None)
            shield = fecampaign.fw.castle_shield_hp
            save = fecampaign.save
            fecampaign.fw.castle_shield_hp = lambda args, holder, hp: hp
            fecampaign.save = lambda args: None
            try:
                row = fecampaign.declare(a, area, attacker=1)
                check("2 attackers online: their keep at 1020 HP (the floor)",
                      row and row["keeps"]["atk"] == [1020, 1020],
                      repr(row and row["keeps"]))
                check("9 defenders online: theirs at 1350 (x0.45)",
                      row and row["keeps"]["def"] == [1350, 1350])
                with fecampaign._LOCK:
                    fecampaign._STATE.pop(area, None)
                row = fecampaign.declare(_args(pop_scale="off"), area, attacker=1)
                check("--pop-scale off: both keeps at SE's 3000",
                      row and row["keeps"] == {"atk": [3000, 3000], "def": [3000, 3000]},
                      repr(row and row["keeps"]))
            finally:
                fecampaign.fw.castle_shield_hp = shield
                fecampaign.save = save
        finally:
            fecampaign.fw.territory_owner = owner
            with fecampaign._LOCK:
                fecampaign._STATE.pop(area, None)
    finally:
        fecampaign._nation_online, fecampaign._live_charids = saved

    bad = [l for l, ok in CHECKS if not ok]
    say("[fe_popscale_test] %s -- %d checks%s" % (
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
