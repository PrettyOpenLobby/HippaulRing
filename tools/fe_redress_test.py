#!/usr/bin/env python3
"""fe_redress_test.py -- --self-redress: the armour back on before 0x100E.

Run from services/:  python ../tools/fe_redress_test.py

WHY. Measured 2026-09-12 in a live client's log: at every field entry
the 0x1000 bag reader dresses the unit, our self 0x1006 then rebuilds its
face/hair/body (unit vtable +0x4c -> 0x0507dc00 -> model builder 0x5079f90)
and strips that armour, and the field-ready 0x107A replay put it back 1.5 s
after the loading screen -- the "base outfit for a split second" report.

This drives the real feworld functions: self_redress_rows,
self_redress_push (through equip_reflect_push and its in-field guard) and
replay_rows_after_redress, with the character store stubbed to fixed rows.
"""
import os
import struct
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
    say("  %-66s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label + (": " + detail if detail else ""))


def _args(**kw):
    a = dict(self_redress="armour", field_equip="on", field_items="bag",
             equip_reflect="request", add_self="on", unit_id="1",
             seq_mode="count", world_prefix=4)
    a.update(kw)
    return types.SimpleNamespace(**a)


# (uid, item, bag slot, worn index, count) -- the live character this bug
# was measured on: a wand in worn 0 (uid 1001), three armour pieces and a
# pocket item
ROWS = [(1001, 531, 4, 0, 1), (1002, 1666, 1, 5, 1), (1003, 1668, 2, 6, 1),
        (1004, 1667, 3, 7, 1), (1009, 19, 6, 11, 1), (1005, 532, 5, None, 1)]


def main():
    sent = []
    real_inner = feworld.inner_msg

    def inner(mid, payload=b"", *a, **k):
        sent.append((mid, bytes(payload)))
        return real_inner(mid, payload, *a, **k)

    saved = (feworld.send, feworld.inner_msg, feworld.stored_equip_rows)
    feworld.send = lambda *a, **k: None
    feworld.inner_msg = inner
    feworld.stored_equip_rows = lambda args, only_uid=None: list(ROWS)

    def fresh(**kw):
        feworld._TLS.session = dict(kw)
        del sent[:]
        return feworld._TLS.session

    def uids():
        # 0x107A add form: [u8 1][u8 announce][u32 slot][u32 uid]...
        return [struct.unpack_from(">I", p, 6)[0] for m, p in sent if m == 0x107A]

    def push(args):
        return feworld.self_redress_push(None, None, "ecb", False, args)

    try:
        say("which rows")
        got = [r[0] for r in feworld.self_redress_rows(_args())]
        check("the armour and the pocket item: every worn row but worn 0/1",
              got == [1002, 1003, 1004, 1009], repr(got))

        say("off / preconditions")
        fresh(in_field=True)
        check("--self-redress off: nothing sent",
              push(_args(self_redress="off")) == [] and not sent)
        check("...and nothing marked for the replay to skip",
              feworld._SESSION.get("self_redressed") == set())
        fresh(in_field=True)
        check("--field-equip off: the bag reader never dressed it -- nothing",
              push(_args(field_equip="off")) == [] and not sent)
        fresh(in_field=True)
        check("--equip-reflect off: refused like every other 0x107A",
              push(_args(equip_reflect="off")) == [] and not sent)
        fresh(in_field=False)
        check("not in a field: equip_reflect_push's null-unit guard refuses",
              push(_args()) == [] and not sent
              and feworld._SESSION.get("self_redressed") == set())

        say("armour")
        S = fresh(in_field=True)
        rows = push(_args())
        check("four 0x107A, in stored order, the wand NOT among them",
              uids() == [1002, 1003, 1004, 1009], repr(uids()))
        check("the rows sent keep their worn index (the marker the Equip "
              "arm needs)", [r[3] for r in rows] == [5, 6, 7, 11],
              repr([r[3] for r in rows]))
        check("the uids are recorded for the field-ready replay",
              S.get("self_redressed") == {1002, 1003, 1004, 1009})

        say("the field-ready replay afterwards")
        worn = [r for r in ROWS if r[3] is not None]
        left = feworld.replay_rows_after_redress(worn)
        check("only the WEAPON is left for it (no second Equip = no flicker)",
              [r[0] for r in left] == [1001], repr(left))
        S["self_redressed"] = set()
        check("with nothing re-dressed it replays everything, as before",
              feworld.replay_rows_after_redress(worn) == worn)
        feworld._SESSION.pop("self_redressed", None)
        check("...and with the key absent (--add-self client) the same",
              feworld.replay_rows_after_redress(worn) == worn)
    finally:
        feworld.send, feworld.inner_msg, feworld.stored_equip_rows = saved

    say("\n%d/%d checks passed" % (sum(ok for _, ok in CHECKS), len(CHECKS)))


if __name__ == "__main__":
    main()
