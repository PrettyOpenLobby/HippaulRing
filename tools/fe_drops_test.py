#!/usr/bin/env python3
"""fe_drops_test.py -- monster drops as retail had them: chests on the ground.

Run from services/:  python ../tools/fe_drops_test.py

WHY. Design chosen in live testing 2026-09-12: drops "just like retail",
pickup "only the killer or their party", rates 25% / 10% / 0.5%. Retail
drops were treasure chests
(0x1006 type 2) picked up with right-click (0x201F); the grant is 0x107A with
announce = 1 (the client's own "Got %s."), the effect 0x1023, the removal
0x1004, a refusal 0x1024 [u16 code].

Drives the real functions: drop_table (validation), drop_roll, drop_on_kill,
drop_pickup (through a real Ctx), the relays, drop_pump, and feitems'
on_gather fall-through with the u16 NG fix.
"""
import os
import struct
import sys
import tempfile
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
    a = dict(drops="on", drop_rate_mult=1.0, drop_lifetime=90.0, drop_range=20.0,
             drop_obj_base=900000, drop_table=None, gather="ng:2",
             seq_mode="count", world_prefix=4, unit_id="1")
    a.update(kw)
    return types.SimpleNamespace(**a)


class Rng(object):
    """random() returns `v`; choice() the first element."""
    def __init__(self, v):
        self.v = v

    def random(self):
        return self.v

    def choice(self, xs):
        return xs[0]


def main():
    feworld.load_extensions()
    import feitems
    store = {"items": []}
    sent, posts = [], []
    real_inner = feworld.inner_msg

    def inner(mid, payload=b"", *a, **k):
        sent.append((mid, bytes(payload), a[0] if a else None))
        return real_inner(mid, payload, *a, **k)

    saved = {n: getattr(feworld, n) for n in (
        "send", "inner_msg", "item_rows", "_store_char_field", "_self_char",
        "ext_post", "party_charids", "_DROP_RNG")}
    feworld.send = lambda *a, **k: None
    feworld.inner_msg = inner
    feworld.item_rows = lambda args: [tuple(r) for r in store["items"]]

    def _store(args, key, value):
        store[key] = value
        return True
    feworld._store_char_field = _store
    feworld._self_char = lambda args: {"sex": 0}             # male
    feworld.ext_post = (lambda kind, payload, to=None, include_me=False:
                        posts.append((kind, payload, to)) or 1)

    def last(mid):
        return [x for x in sent if x[0] == mid]

    try:
        args = _args()
        say("the table")
        rows = feworld.drop_table()
        check("the shipped table loads (%d rows)" % len(rows), len(rows) > 40)
        check("every row names an item the client has",
              all(r["item"] in feworld.fegamedata.items() for r in rows))
        tmp = os.path.join(tempfile.gettempdir(), "fe_drops_t_%d.tsv" % os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("family\tlv_min\tlv_max\titem_no\tsex\trate_ppm\tsource\n"
                     "Venomous\t0\t999\t99999\t-\t250000\tfake\n"
                     "Venomous\t0\t999\t1541\tF\t250000\tcaster shoes as F\n"
                     "Venomous\t0\t999\t10\t-\t250000\tapple\n")
        t = feworld.drop_table(tmp)
        check("an item the client does not have is REFUSED (the crash)",
              all(r["item"] != 99999 for r in t))
        check("a sex the item has no model for is REFUSED",
              not any(r["item"] == 1541 and r["sex"] == "F" for r in t))
        check("...and a good row survives", [r["item"] for r in t] == [10])
        os.remove(tmp)

        say("the roll")
        baron = {"name": "Baron_Orc", "level": 40}
        r = feworld.drop_roll(args, baron, "M", Rng(0.0))
        check("everything hits -> the RAREST wins (a heroic piece, 0.5%)",
              r and r["ppm"] == 5000, repr(r))
        check("...and for a MALE killer only the M rows",
              r["item"] in (1349, 1343), repr(r))
        r = feworld.drop_roll(args, baron, "F", Rng(0.0))
        check("a FEMALE killer gets the F rows", r["item"] in (849, 843), repr(r))
        check("nothing hits on a high roll",
              feworld.drop_roll(args, baron, "M", Rng(0.999)) is None)
        r = feworld.drop_roll(args, {"name": "Orc_Peon", "level": 20}, "M",
                              Rng(0.2))
        check("an Orc at a 20% roll: cheese or bacon (25% rows)",
              r and r["item"] in (7, 6), repr(r))
        check("an unlisted monster drops nothing",
              feworld.drop_roll(args, {"name": "Nobody", "level": 1}, "M",
                                Rng(0.0)) is None)
        check("--drop-rate-mult 0 turns every roll off",
              feworld.drop_roll(_args(drop_rate_mult=0.0), baron, "M",
                                Rng(0.0)) is None)

        say("a kill puts a chest on the ground")
        feworld._DROP_RNG = Rng(0.0)
        feworld.party_charids = lambda cid: {int(cid)}
        me = {"charid": 3, "field": 12, "room": -1, "in_field": True,
              "cpos": (10.0, 20.0, 10.0)}
        feworld._TLS.session = dict(me)
        del sent[:]
        c = feworld.drop_on_kill(None, None, "ecb", False, args,
                                 {"name": "Goblin_Novice", "level": 8,
                                  "pos": (12.0, 20.0, 11.0)})
        check("a chest registered, id in the chest range",
              c and c["obj"] >= 900000 and c["obj"] in feworld._CHESTS)
        rec = last(0x1006)
        check("0x1006 went out addressed to the chest",
              rec and rec[-1][2] == c["obj"])
        body = rec[-1][1]
        count, obj, typ = struct.unpack_from(">HIB", body, 0)
        check("...type 2, the chest's own id", (count, obj, typ) == (1, c["obj"], 2))
        name_end = body.index(b"\0", 7)
        item, f3ae = struct.unpack_from(">HB", body, name_end + 1)
        check("...its item after the NUL-terminated name, +0x3ae = 0",
              item == c["item"] and f3ae == 0 and name_end - 7 <= 32)
        check("--drops off: no chest", feworld.drop_on_kill(
            None, None, "ecb", False, _args(drops="off"),
            {"name": "Goblin_Novice", "level": 8, "pos": (0, 0, 0)}) is None)

        say("picking it up")
        ctx = feworld.Ctx(None, None, "ecb", False, args)
        feworld._TLS.session["charid"] = 55                 # not the killer
        del sent[:]
        check("someone outside the party: NG 2 as a u16",
              feworld.drop_pickup(ctx, c["obj"]) is True
              and last(0x1024)[-1][1] == struct.pack(">H", 2))
        feworld._TLS.session["charid"] = 3
        feworld._TLS.session["cpos"] = (60.0, 20.0, 10.0)
        del sent[:]
        feworld.drop_pickup(ctx, c["obj"])
        check("48 u away: NG 3 (the server enforces the range)",
              last(0x1024)[-1][1] == struct.pack(">H", 3))
        feworld._TLS.session["cpos"] = (10.0, 20.0, 10.0)
        store["items"] = [[i, 10, 1, 1] for i in range(96)]
        del sent[:]
        feworld.drop_pickup(ctx, c["obj"])
        check("a full bag: NG 1, and the chest stays",
              last(0x1024)[-1][1] == struct.pack(">H", 1)
              and c["obj"] in feworld._CHESTS)
        store["items"] = [[3001, 1666, 1, 1]]
        del sent[:], posts[:]
        c["shown"].add(77)                                   # a party member sees it
        check("the killer, in range, room in the bag: picked up",
              feworld.drop_pickup(ctx, c["obj"]) is True)
        add = last(0x107A)
        check("0x107A went out with announce = 1 ('Got %s.')",
              add and add[-1][1][0] == 1 and add[-1][1][1] == 1, repr(add))
        slot, uid = struct.unpack_from(">II", add[-1][1], 2)
        check("...into the next bag slot, a fresh uid",
              slot == 1 and uid not in (3001,), repr((slot, uid)))
        check("...then 0x1023 (the effect) and 0x1004 on the chest",
              last(0x1023)[-1][1] == struct.pack(">I", c["obj"])
              and last(0x1004)[-1][2] == c["obj"])
        check("the item is in the stored bag",
              [r[1] for r in store["items"]] == [1666, c["item"]], repr(store))
        check("the chest is gone from the registry", c["obj"] not in feworld._CHESTS)
        check("...and the party member's screen is told (drop_gone)",
              posts and posts[-1][0] == "drop_gone")
        check("a second click on it: not a chest any more (falls through)",
              feworld.drop_pickup(ctx, c["obj"]) is None)

        say("first come, first served")
        c2 = feworld.drop_on_kill(None, None, "ecb", False, args,
                                  {"name": "Goblin_Novice", "level": 8,
                                   "pos": (12.0, 20.0, 11.0)})
        c2["taken"] = True
        del sent[:]
        feworld.drop_pickup(ctx, c2["obj"])
        check("already taken by another: NG 4",
              last(0x1024)[-1][1] == struct.pack(">H", 4))
        feworld._CHESTS.pop(c2["obj"], None)

        say("the party")
        feworld.party_charids = lambda cid: {int(cid), 77}
        del posts[:]
        c3 = feworld.drop_on_kill(None, None, "ecb", False, args,
                                  {"name": "Goblin_Novice", "level": 8,
                                   "pos": (12.0, 20.0, 11.0)})
        check("a party kill relays the chest (drop_chest)",
              posts and posts[-1][0] == "drop_chest")
        to = posts[-1][2]
        check("...to a party member in the same field",
              to({"charid": 77, "field": 12, "room": -1, "in_field": True}, "B"))
        check("...not to one in another field",
              not to({"charid": 77, "field": 13, "room": -1, "in_field": True}, "B"))
        check("...not to a stranger",
              not to({"charid": 88, "field": 12, "room": -1, "in_field": True}, "C"))
        feworld._TLS.session = {"charid": 77, "field": 12, "room": -1,
                                "in_field": True, "cpos": (12.0, 20.0, 11.0)}
        del sent[:]
        feworld._drop_relay_chest(ctx, {"obj": c3["obj"]})
        check("the member's session draws the chest and is remembered",
              last(0x1006) and 77 in c3["shown"])
        store["items"] = []
        check("...and a party member may pick it up",
              feworld.drop_pickup(ctx, c3["obj"]) is True and store["items"])

        say("expiry")
        feworld._TLS.session = dict(me)
        c4 = feworld.drop_on_kill(None, None, "ecb", False, args,
                                  {"name": "Goblin_Novice", "level": 8,
                                   "pos": (12.0, 20.0, 11.0)})
        c4["expires"] = time.monotonic() - 1
        del sent[:]
        n = feworld.drop_pump(None, None, "ecb", False, args)
        check("an unclaimed chest past its lifetime: 0x1004, and forgotten",
              n == 1 and last(0x1004) and c4["obj"] not in feworld._CHESTS)

        say("feitems.on_gather")
        feworld._TLS.session = dict(me)
        del sent[:]
        feitems.on_gather(ctx, struct.pack(">HI", 0x201F, 12345))
        ng = last(0x1024)
        check("a non-chest object falls through to the probe (ng:2)",
              ng and ng[-1][1] == struct.pack(">H", 2), repr(ng))
        check("...and its NG is 2 bytes now (the client reads a u16)",
              len(ng[-1][1]) == 2)
    finally:
        for n, f in saved.items():
            setattr(feworld, n, f)
        feworld._CHESTS.clear()

    say("\n%d/%d checks passed" % (sum(ok for _, ok in CHECKS), len(CHECKS)))


if __name__ == "__main__":
    main()
