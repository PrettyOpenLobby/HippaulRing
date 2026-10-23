#!/usr/bin/env python3
"""fe_timed_test.py -- SE's 2006 timed events on a schedule (world/timedevents.py).

Run from the repo root:  python tools/fe_timed_test.py

WHY. SE ran its 2006 events as patch-day switches with an end time (the
archived polnews): a drop boost in June, a sale in every shop in September,
the Fantasy Earth Double Up Campaign (2x kill EXP, 2x war EXP, 2x drops) and
the Dragon feast (the Dragon for both armies from the war's start, 5 per
army) in October. fedata/fe-timed-events.tsv carries them, every year on
SE's dates (OURS), and each effect is applied where the server pays it.

Pins: the shipped table (rows, effects, JP names, the quests NOT served);
the windows in JST and a New Year wrap; the hooks themselves, through the
real functions: drops.drop_roll, combat.kill_rewards, fecampaign.settle,
shops.shop_stock's sale, feunit._war_rules and dragon_item; the Calendar
entries and events_between for the news; and a log line per applied effect.
The clock is the module's own `time`, replaced, so the suite does not depend
on the day it runs.
"""
import calendar as _cal
import contextlib
import io
import os
import sys
import tempfile
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
for _p in (_SERVICES, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import feworld      # noqa: E402
from world import combat, devtool, drops, shops, timedevents   # noqa: E402

FAILS = []
OUT = sys.__stdout__
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    say("  %-66s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + repr(detail)) if (detail != "" and not cond) else ""))
    if not cond:
        FAILS.append(label)


def jst(y, mo, d, h=0, mi=0):
    """Unix seconds of a JST wall time."""
    return _cal.timegm((y, mo, d, h, mi, 0)) - 9 * 3600


class Clock(object):
    """timedevents.time, held at `t`."""
    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t

    def gmtime(self, *a):
        return time.gmtime(*a)


def at(t):
    timedevents.time = Clock(t)


def captured(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = fn(*a, **k)
    return res, buf.getvalue()


ON = types.SimpleNamespace(timed_events="table")
OFF = types.SimpleNamespace(timed_events="off")
DOUBLE_UP = jst(2026, 10, 10, 12)       # inside the Double Up
FEAST = jst(2026, 10, 22, 12)           # inside the Dragon feast
QUIET = jst(2026, 8, 1, 12)             # nothing runs


def part_table():
    say("the shipped table (fedata/fe-timed-events.tsv)")
    rows = timedevents.load_table()
    ev = sorted({(r["event"], r["effect"]) for r in rows})
    check("eight rows: drop boost, sale, two quests, Double Up x3, Dragon feast",
          ev == [("alchemy-quest", "quest"), ("alchemy-quest-2", "quest"),
                 ("double-up", "drop_rate"), ("double-up", "kill_exp"),
                 ("double-up", "war_exp"), ("dragon-feast", "dragon_feast"),
                 ("drop-boost", "drop_rate"), ("shop-sale", "shop_sale")], ev)
    q = [r for r in rows if r["effect"] == "quest"]
    check("the limited-time quests are recorded and NOT served",
          len(q) == 2 and not any(r["served"] for r in q), q)
    feast = [r for r in rows if r["event"] == "dragon-feast"][0]
    check("the Dragon feast keeps SE's own name, 5 per army, the Ruby (45) as its item",
          feast["name_jp"] == "錬金成功！？戦竜の饗宴" and feast["value"] == 5
          and set(feast["items"]) == {45}, feast)
    check("...served and announced (a Dragon was seen on a console 2026-10-23), as every "
          "row is", feast["served"] and all(r["announce"] for r in rows), feast)
    du = [r for r in rows if r["event"] == "double-up"]
    check("Double Up: SE's dates 10-05 16:00 .. 10-19 14:00 JST, all x2",
          all(r["start"] == (10, 5, 16, 0) and r["end"] == (10, 19, 14, 0)
              and r["value"] == 2 for r in du), du)
    tmp = tempfile.mkdtemp(prefix="fe-timed-")
    bad = os.path.join(tmp, "bad.tsv")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("event\tname\tstart\tend\teffect\tvalue\n"
                "a\tA\t13-01 00:00\t12-31 00:00\tkill_exp\t2\n"
                "b\tB\t01-01 00:00\t01-02 00:00\tfly\t2\n"
                "c\tC\t01-01 00:00\t01-02 00:00\tkill_exp\tlots\n"
                "d\tD\t01-01 00:00\t01-02 00:00\tkill_exp\t3\n")
    got, log = captured(timedevents.load_table, bad)
    check("a bad month, an unknown effect, a bad number are refused and logged",
          [r["event"] for r in got] == ["d"] and "REFUSED 3" in log, (got, log))
    got, log = captured(timedevents.load_table, os.path.join(tmp, "none.tsv"))
    check("no table: nothing runs, said in the log", got == [] and "none run" in log, log)
    return tmp


def part_windows(tmp):
    say("windows, JST, every year")
    du = [r for r in timedevents.load_table() if r["effect"] == "kill_exp"][0]
    check("Double Up is on at 10-05 16:00 JST, off a minute before",
          timedevents.event_on(du, jst(2026, 10, 5, 16))
          and not timedevents.event_on(du, jst(2026, 10, 5, 15, 59)))
    check("...and off at 10-19 14:00 JST (the end is exclusive)",
          timedevents.event_on(du, jst(2026, 10, 19, 13, 59))
          and not timedevents.event_on(du, jst(2026, 10, 19, 14)))
    check("...in any year (SE ran it once, in 2006)",
          timedevents.event_on(du, jst(2006, 10, 10)) and timedevents.event_on(du, jst(2031, 10, 10)))
    ny = os.path.join(tmp, "ny.tsv")
    with open(ny, "w", encoding="utf-8") as f:
        f.write("event\tname\tstart\tend\teffect\tvalue\n"
                "ny\tNew Year\t12-31 18:00\t01-02 12:00\tkill_exp\t3\n")
    row = timedevents.load_table(ny)[0]
    check("a window across New Year wraps",
          timedevents.event_on(row, jst(2026, 12, 31, 20)) and timedevents.event_on(row, jst(2027, 1, 1))
          and not timedevents.event_on(row, jst(2027, 1, 2, 12)))
    ents = timedevents.entries(jst(2026, 12, 1), jst(2027, 2, 1), [row])
    check("...and is ONE calendar entry ending in the next year",
          [(e["id"], e["start"], e["end"]) for e in ents]
          == [("fe-ny-2026", jst(2026, 12, 31, 18), jst(2027, 1, 2, 12))], ents)


def part_rates():
    say("multipliers and their log line")
    at(DOUBLE_UP)
    f, hit = timedevents.multiplier(ON, "kill_exp")
    check("in the Double Up: kill EXP x2", f == 2.0 and len(hit) == 1, (f, hit))
    got, log = captured(timedevents.scale, ON, "war_exp", 1234, "war EXP for x")
    check("scale: war EXP 1234 -> 2468, logged 'DOUBLE UP: war EXP ... x2'",
          got == 2468 and "DOUBLE UP: war EXP for x x2 (1234 -> 2468)" in log, log)
    got, log = captured(timedevents.scale, OFF, "war_exp", 1234, "war EXP")
    check("--timed-events off: 1234, no line", got == 1234 and not log, log)
    got, log = captured(timedevents.scale, types.SimpleNamespace(), "war_exp", 1234, "war EXP")
    check("an args with no timed_events (a test's namespace) is off",
          got == 1234 and not log, log)
    at(QUIET)
    got, log = captured(timedevents.scale, ON, "war_exp", 1234, "war EXP")
    check("outside every window: 1234, no line", got == 1234 and not log, log)
    at(jst(2026, 6, 18))
    f, _ = timedevents.multiplier(ON, "drop_rate")
    check("06-18: SE's drop boost, drop rate x2", f == 2.0, f)


class Rng(object):
    def __init__(self, v):
        self.v = v

    def random(self):
        return self.v

    def choice(self, xs):
        return xs[0]


def part_drops():
    say("drops.drop_roll: the drop rate")
    row = {"family": "Goblin*", "lo": 0, "hi": 999, "item": 10, "sex": "-",
           "ppm": 100000, "source": "test"}
    saved = drops.drop_table
    drops.drop_table = lambda path=None: [row]
    try:
        m = {"name": "Goblin_Novice", "level": 3}
        rng = Rng(0.15)                 # 150000: misses 10%, hits 20%
        for t, args, want, why in (
                (QUIET, ON, None, "a 15% roll misses a 10% row on a quiet day"),
                (DOUBLE_UP, ON, 10, "...and hits it in the Double Up (x2 = 20%)"),
                (DOUBLE_UP, OFF, None, "...unless --timed-events off")):
            at(t)
            got, log = captured(drops.drop_roll, dict_args(args), m, "M", rng)
            check(why, (got or {}).get("item") == want, (got, log))
            if want:
                check("...logged 'DOUBLE UP: drop rate x2 for Goblin_Novice'",
                      "DOUBLE UP: drop rate x2 for Goblin_Novice (L3)" in log, log)
        at(DOUBLE_UP)
        got, _ = captured(drops.drop_roll, dict_args(ON, drop_rate_mult=0.0), m, "M", rng)
        check("--drop-rate-mult 0 still turns drops off in an event", got is None, got)
    finally:
        drops.drop_table = saved


def dict_args(base, **kw):
    a = dict(vars(base), drops="on", drop_rate_mult=1.0, drop_table=None)
    a.update(kw)
    return types.SimpleNamespace(**a)


def part_kills():
    say("combat.kill_rewards: kill EXP")
    paid = []
    saved = (combat._pay_exp, drops.kill_gold_credit, drops.drop_on_kill,
             combat._my_key, drops.mob_exp, drops.kill_gold)
    combat._pay_exp = lambda conn, ob, mode, be, args, exp: paid.append(exp)
    drops.kill_gold_credit = lambda *a, **k: 0
    drops.drop_on_kill = lambda *a, **k: None
    combat._my_key = lambda: 7
    drops.mob_exp = lambda args, m: 41
    drops.kill_gold = lambda args, m: 0
    try:
        m = {"name": "Goblin_Novice", "level": 3}
        for t, args, want, why in (
                (QUIET, ON, 41, "a quiet day: the kill's own 41 EXP"),
                (DOUBLE_UP, ON, 82, "the Double Up: 82, the whole kill doubled"),
                (DOUBLE_UP, OFF, 41, "...41 with --timed-events off")):
            at(t)
            paid[:] = []
            a = dict_args(args, loot_rule="last", kill_reward="on")
            _, log = captured(combat.kill_rewards, None, None, "ecb", False, a, 9, m, killer=7)
            check(why, paid == [want], (paid, log))
            if t == DOUBLE_UP and args is ON:
                check("...logged 'DOUBLE UP: kill EXP for Goblin_Novice x2 (41 -> 82)'",
                      "DOUBLE UP: kill EXP for Goblin_Novice x2 (41 -> 82)" in log, log)
    finally:
        (combat._pay_exp, drops.kill_gold_credit, drops.drop_on_kill,
         combat._my_key, drops.mob_exp, drops.kill_gold) = saved


def part_war():
    say("fecampaign.settle: war EXP")
    import fecampaign as F
    import fe_crystal_test as C
    tmp = tempfile.mkdtemp(prefix="fe-timed-war-")
    saved = (F._update_char, timedevents.time)

    def upd(account, charid, mutate):
        c = C.CHARS.get((account, charid))
        if c is None:
            return False
        mutate(c)
        return True
    F._update_char = upd
    got = {}
    try:
        for label, t in (("quiet", QUIET), ("double", DOUBLE_UP)):
            args = C._args(campaign_file=os.path.join(tmp, "c-%s.json" % label),
                           territory_file=os.path.join(tmp, "t-%s.json" % label),
                           crystal=0, war_rewards="rod", timed_events="table")
            C.CHARS[("acct-Alice", 41)] = {"charid": 41, "look1": 0,
                                           "class_levels": {"0": 20},
                                           "class_exp": {"0": 0}, "exp": 0,
                                           "ring": 0, "war_rank": 1}
            now = time.time()
            with F._LOCK:
                F._STATE[5] = F._row_from({
                    "phase": F.WAR, "atk": 3, "war_at": now - 600,
                    "keeps": {"def": [0, 3000], "atk": [3000, 3000]},
                    "members": {"acct-Alice|41": {"a": "acct-Alice", "c": 41,
                                                  "side": "atk", "dmg": 3100,
                                                  "t": now - 600}}})
            at(t)
            _, log = captured(F.settle, args, 5, "atk", 1)
            got[label] = (C.CHARS[("acct-Alice", 41)]["exp"], log)
            with F._LOCK:
                F._STATE.pop(5, None)
    finally:
        F._update_char, timedevents.time = saved
    q, d = got["quiet"][0], got["double"][0]
    check("Lv20, a full war won: the Double Up credits twice the war EXP",
          q > 0 and d == 2 * q, (q, d))
    check("...logged 'DOUBLE UP: war EXP for acct-Alice|41 x2'",
          "DOUBLE UP: war EXP for acct-Alice|41 x2 (%d -> %d)" % (q, d) in got["double"][1],
          got["double"][1][-600:])


def part_shop():
    say("shops: the sale")
    stock = [(1, 100, 0), (2, 0, 57), (3, 1, 0)]
    at(jst(2026, 9, 25))
    got, log = captured(shops.shop_sale, ON, stock)
    check("09-25: every price x0.8, Rings too, never below 1",
          got == [(1, 80, 0), (2, 0, 45), (3, 1, 0)], got)
    check("...logged 'SHOP SALE: 3 shop price(s) x0.8'", "SHOP SALE: 3 shop price(s) x0.8" in log, log)
    at(jst(2026, 10, 6))
    got, log = captured(shops.shop_sale, ON, stock)
    check("10-06 (the sale ended at the 10-05 version-up): the table's prices",
          got == stock and not log, (got, log))


def part_dragon():
    say("feunit: the Dragon feast")
    import feunit
    camp = types.SimpleNamespace(summon_count=lambda area, side, fid: 0)
    saved = (feunit.alive_count, feunit.dragon_losing)
    alive = [0]
    feunit.alive_count = lambda area, side, fid: alive[0]
    feunit.dragon_losing = lambda ctx, camp, area, side: (False, "the atk side is winning")
    try:
        def rules(t, args):
            at(t)
            ctx = types.SimpleNamespace(args=args)
            return captured(feunit._war_rules, ctx, camp, 17, "atk", 1)
        base = dict(summon_alive_cap="WRAITH=1,DRAGON=3,CHIMERA=1", summon_war_cap="CHIMERA=2",
                    summon_items="DRAGON=901", summon_bars=3)
        a_on = types.SimpleNamespace(timed_events="table", **base)
        a_off = types.SimpleNamespace(timed_events="off", **base)
        (code, why), _ = rules(QUIET, a_on)
        check("a quiet day: the winning side gets no Dragon (SE 6/8 rule)",
              code is not None and "winning" in why, why)
        alive[0] = 4
        (code, why), log = rules(FEAST, a_on)
        check("the feast: the winning side may, 4 standing of 5",
              code is None and "DRAGON FEAST" in log and "4 of 5" in log, (code, why, log))
        alive[0] = 5
        (code, why), _ = rules(FEAST, a_on)
        check("...and not a sixth (5 per army)", code is not None and "5" in why, why)
        alive[0] = 0
        (code, why), _ = rules(FEAST, a_off)
        check("--timed-events off: the 2006 rule again", code is not None and "winning" in why, why)
        at(FEAST)
        check("the item: the feast row's own (45, the Ruby) before --summon-items DRAGON=",
              feunit.dragon_item(a_on) == 45)
        at(QUIET)
        check("...outside the feast, --summon-items DRAGON= again",
              feunit.dragon_item(a_on) == 901)
        check("...and none named anywhere: no item, so no Dragon at all",
              feunit.dragon_item(types.SimpleNamespace(timed_events="table", summon_items="")) is None)
    finally:
        feunit.alive_count, feunit.dragon_losing = saved

    say("feunit: the client's revive 0x2045 does not unsummon a risen Dragon")
    import time as _time
    ended = []
    saved_end = feunit.morph_end
    feunit.morph_end = lambda ctx, why, **k: ended.append(why)
    try:
        def release(m):
            ctx = types.SimpleNamespace(args=types.SimpleNamespace(metamorphosis="rules"),
                                        session={"morph": m})
            captured(feunit.on_metamorphosis_release, ctx, b"")
            return ctx
        release({"form": 1, "rose_at": _time.monotonic()})
        check("a 0x2045 a moment after the Dragon rose is the revive's: kept", ended == [], ended)
        m = {"form": 1, "rose_at": _time.monotonic()}
        release(m)
        release(m)
        check("...the grace is spent once: the next 0x2045 is a real Unsummon",
              ended == ["0x2045 from the client"], ended)
        ended[:] = []
        release({"form": 1, "rose_at": _time.monotonic() - feunit.RISE_GRACE - 1})
        check("...and one after RISE_GRACE seconds unsummons", ended == ["0x2045 from the client"], ended)
        ended[:] = []
        release({"form": 3})
        check("...as it always does for a building's summon", ended == ["0x2045 from the client"], ended)
    finally:
        feunit.morph_end = saved_end

    say("shops: the feast's item in the Ring Shop")
    at(FEAST)
    feast_items = shops.feast_items(ON)
    check("the feast names the Ruby for the Ring Shop", feast_items == [45], feast_items)
    ring = dict_args(ON, shop_prices="table", shop_level_max=40)
    stock = [r for r in shops.shop_stock(ring, "ring") if r[0] == 45]
    check("...and the Ring Shop lists it for FEAST_RING_PRICE, one Ring",
          stock == [(45, 0, 1)], stock)
    check("...not the gold shops", all(not [r for r in shops.shop_stock(ring, k) if r[0] == 45]
                                       for k in ("weapon", "armor", "item")))
    at(QUIET)
    check("...and nothing outside the feast",
          shops.feast_items(ON) == [] and shops.feast_items(OFF) == []
          and not [r for r in shops.shop_stock(ring, "ring") if r[0] == 45])


def part_calendar():
    say("the Calendar and the news")
    lo, hi = jst(2026, 1, 1), jst(2027, 1, 1)
    ents = timedevents.entries(lo, hi, timedevents.load_table())
    got = [(e["id"], e["kind"], e["start"], e["end"]) for e in ents]
    check("2026: drop boost, shop sale, Double Up, Dragon feast; no quests",
          got == [("fe-drop-boost-2026", "event", jst(2026, 6, 15, 15, 45), jst(2026, 6, 22, 14)),
                  ("fe-shop-sale-2026", "shop", jst(2026, 9, 21, 15, 15), jst(2026, 10, 5, 16)),
                  ("fe-double-up-2026", "event", jst(2026, 10, 5, 16), jst(2026, 10, 19, 14)),
                  ("fe-dragon-feast-2026", "event", jst(2026, 10, 19, 15, 30), jst(2026, 10, 26, 14))],
          got)
    du = [e for e in ents if e["id"] == "fe-double-up-2026"][0]
    check("Double Up's about names all three effects and SE's own name",
          "kill EXP x2" in du["about"] and "war EXP x2" in du["about"]
          and "drop rates x2" in du["about"] and "ダブルアップ" in du["about"]
          and du["times"] == [[du["start"], du["end"]]] and len(du["about"]) <= 300, du)
    news = timedevents.events_between(jst(2026, 10, 1), jst(2026, 11, 1))
    check("events_between: the news list, the Calendar's shape plus game fe",
          [e["id"] for e in news] == ["fe-shop-sale-2026", "fe-double-up-2026",
                                      "fe-dragon-feast-2026"]
          and all(e["game"] == "fe" and set(e) == {"id", "title", "start", "end", "kind",
                                                     "about", "times", "game"} for e in news),
          news)
    st, res = devtool.admin_calendar(types.SimpleNamespace(timed_events="table", campaign="off"),
                                     {"from": [str(jst(2026, 10, 20))],
                                      "to": [str(jst(2026, 10, 21))]}, "test")
    check("calendar.json carries the feast with the campaign off",
          st == 200 and [e["id"] for e in res["events"]] == ["fe-dragon-feast-2026"], res)


def main():
    feworld.load_extensions(["fecampaign", "feunit"])
    real = timedevents.time
    try:
        tmp = part_table()
        part_windows(tmp)
        for fn in (part_rates, part_drops, part_kills, part_war, part_shop,
                   part_dragon, part_calendar):
            try:
                fn()
            except Exception as e:                     # noqa: BLE001
                import traceback
                traceback.print_exc(file=OUT)
                check("part %s ran to the end" % fn.__name__, False, "%s: %s" % (type(e).__name__, e))
    finally:
        timedevents.time = real
    if FAILS:
        say("[fe_timed_test] FAILED: %s" % FAILS)
        sys.exit(1)
    say("[fe_timed_test] OK")


if __name__ == "__main__":
    main()
