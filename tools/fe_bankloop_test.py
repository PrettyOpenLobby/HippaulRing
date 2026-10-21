#!/usr/bin/env python3
"""fe_bankloop_test.py -- the BANK end to end through the Bank_Keeper's talk.

Run from services/:  python ../tools/fe_bankloop_test.py

2026-10-08 (round 2). Prod opened the bank window twice and never sent a
deposit. This walks the whole thing through the real read loop with the
real defaults: talk to the Bank_Keeper (0x2046 -> 0x1071, 0x20A7 -> 0x1172 +
the line, 0x20A8 -> the window), what the window reads in its ctor (0x2024
maskA 0x10000000|0x40000000 = [u32 bank gold][u16 cells], one 0x107A kind 1
per banked item), the four requests and their arms (0x05058540..: OK
header-only, NG [u32 code]), the window's close, and the bank again after
the conversation ended.

Retail rules checked (round-2 research): 40 slots (SE flow06), NO gold fee
-- the 3rd-beta change list (2006-01) abolished the bank fee that the
2005-12 tutorial book (line 179) still describes; gold banked is kept.
"""
import struct
import sys

import fe_harness as H
from fe_harness import check, ids, bodies, say

FIELD = 39          # a capital half (zones: 39 = map00_01)


def place_keeper(args):
    """A Bank_Keeper row in the town file for FIELD (the panel's write path)."""
    from world import town

    def change(blob):
        g = blob.setdefault(str(FIELD), {})
        g["npcs"] = [{"name": "Bank_Keeper", "script": 3102,
                      "pos": [0.0, 0.0, 0.0]}]
        return True, True
    return town._town_change(args, change)


def bank_state(box):
    """The bank fields of every 0x2024 in `box`: (gold, cells)."""
    out = []
    for b in bodies(box, 0x2024):
        a, _b = struct.unpack_from(">II", b, 0)
        if a == 0x50000000:
            out.append(struct.unpack_from(">IH", b, 8))
    return out


def kind1_adds(box):
    """(uid, bank slot) of every 0x107A add whose item kind is 1 (the bank)."""
    out = []
    for b in bodies(box, 0x107A):
        # [u8 1 add][u8 announce][u32 slot][u32 uid] + item_fields, whose
        # +0x4c6 "which array" u16 sits 24 bytes in (itemrecords.item_fields)
        if b[0] == 1 and struct.unpack_from(">H", b, 10 + 24)[0] == 1:
            slot, uid = struct.unpack_from(">II", b, 2)
            out.append((uid, slot))
    return out


def main():
    H.setup()
    args = H.real_args()
    check("real defaults: --bank on, 40 slots, events on",
          args.bank == "on" and args.bank_slots == 40 and args.events == "on",
          (args.bank, args.bank_slots))
    check("the keeper is placed in the town file", place_keeper(args))
    npc = int(args.monster_base) + 100
    p = H.Peer("Banker", 51, args, nation=1, field=FIELD, char={
        "gold": 500, "bank_gold": 0, "bag_size": 30,
        "items": [[7001, 4, 0, 5], [7002, 7, 0, 1]], "bank": []})

    def talk():
        out = p.send(0x2046, struct.pack(">II", npc, 0))
        ok = bodies(out, 0x1071)
        check("0x2046 -> 0x1071 [npc][0][0][script 3102]",
              ok == [struct.pack(">IHHI", npc, 0, 0, 3102)], out)
        out = p.send(0x20A7, struct.pack(">II", npc, 3102))
        check("0x20A7 -> 0x1172 then the keeper's line (0x1174 op 3)",
              ids(out)[:2] == [0x1172, 0x1174]
              and struct.unpack_from(">IH", bodies(out, 0x1174)[0])[1] == 3,
              ids(out))
        out = p.send(0x20A8, struct.pack(">IHI", npc, 6, 0))
        win = [b for b in bodies(out, 0x1174)
               if struct.unpack_from(">IH", b)[1] == 8]
        check("the click -> bank state + items BEFORE the op-8 window 0x18",
              win and struct.unpack_from(">IHH", win[0])[2] == 0x18
              and ids(out).index(0x2024) < ids(out).index(0x1174), ids(out))
        return out

    def close():
        p.send(0x2085, struct.pack(">I", 0))
        out = p.send(0x20A8, struct.pack(">IHI", npc, 8, 0))
        check("the window closes -> the END window (op 8 type 0x13)",
              any(struct.unpack_from(">IHH", b)[1:] == (8, 0x13)
                  for b in bodies(out, 0x1174)), ids(out))
        # the END op's close arrives as a bare OUTER 0x2099
        inbox = [(0x2099, struct.pack(">I", npc))]
        saved = H.fenet.recv_frame
        H.fenet.recv_frame = lambda conn: inbox.pop(0) if inbox else None
        n = len(p.out)
        try:
            with p.current(), H.quiet():
                H.feworld._serve_loop(H._Conn(), args, None, None, "ecb", False)
        finally:
            H.fenet.recv_frame = saved
        out = p.out[n:]
        check("0x2099 -> 0x1152 [npc][''] then 0x1175 (event mode released)",
              [m for m in ids(out) if m in (0x1152, 0x1175)] == [0x1152, 0x1175],
              ids(out))

    try:
        out = p.send(0x1149, struct.pack(">I", 10))
        check("a deposit with NO bank window open is not answered (the reply "
              "arm derefs the window unchecked)",
              not [m for m in ids(out) if m in (0x208D, 0x208E)], ids(out))
        out = talk()
        check("the window's ctor reads gold 0 and 40 cells",
              bank_state(out) == [(0, 40)], bank_state(out))
        # ---- gold
        out = p.send(0x1149, struct.pack(">I", 100))
        check("deposit 100 -> wallet + bank state + 0x208D header-only",
              bodies(out, 0x208D) == [b""] and bank_state(out) == [(100, 40)],
              ids(out))
        check("stored: 400 on hand, 100 banked",
              (p.char["gold"], p.char["bank_gold"]) == (400, 100))
        out = p.send(0x1149, struct.pack(">I", 1000))
        check("deposit more than on hand -> 0x208E [u32 5]",
              bodies(out, 0x208E) == [struct.pack(">I", 5)], out)
        out = p.send(0x114A, struct.pack(">I", 101))
        check("withdraw more than banked -> 0x2090 [u32 5]",
              bodies(out, 0x2090) == [struct.pack(">I", 5)], out)
        # ---- items
        out = p.send(0x114B, struct.pack(">IH", 7001, 2))
        adds = kind1_adds(out)
        check("deposit 2 of 5 bread -> a NEW bank object in slot 0 + 0x2091",
              len(adds) == 1 and adds[0][1] == 0 and adds[0][0] not in (7001, 7002)
              and bodies(out, 0x2091) == [b""], (adds, ids(out)))
        banked_uid = adds[0][0]
        out = p.send(0x114B, struct.pack(">IH", 7002, 1))
        check("deposit the whole cheese -> the SAME uid re-filed, slot 1",
              kind1_adds(out) == [(7002, 1)] and bodies(out, 0x2091) == [b""],
              kind1_adds(out))
        out = p.send(0x114B, struct.pack(">IH", 9999, 1))
        check("deposit an item not held -> 0x2092 [u32 9]",
              bodies(out, 0x2092) == [struct.pack(">I", 9)], out)
        check("stored: bag holds 3 bread, bank holds 2 bread + the cheese",
              [tuple(r)[1:4] for r in p.char["items"]] == [(4, 0, 3)]
              and sorted(tuple(r)[1] for r in p.char["bank"]) == [4, 7],
              (p.char["items"], p.char["bank"]))
        close()

        # ---- the bank again: what was stored comes back with the window
        out = talk()
        check("reopened: gold 100, and both banked items re-filed (kind 1)",
              bank_state(out) == [(100, 40)]
              and sorted(kind1_adds(out)) == sorted([(banked_uid, 0), (7002, 1)]),
              (bank_state(out), kind1_adds(out)))
        out = p.send(0x114A, struct.pack(">I", 100))
        check("withdraw all 100 -> 0x208F, NO fee (abolished in the 3rd beta)",
              bodies(out, 0x208F) == [b""]
              and (p.char["gold"], p.char["bank_gold"]) == (500, 0),
              (p.char["gold"], p.char["bank_gold"]))
        out = p.send(0x114C, struct.pack(">IH", 7002, 1))
        check("withdraw the cheese -> back into the bag + 0x2093",
              bodies(out, 0x2093) == [b""]
              and 7 in [tuple(r)[1] for r in p.char["items"]], out)
        # ---- capacity: fill the remaining 39 cells, then one more
        p.char["bank"] = [list(r) for r in p.char["bank"]] + [
            [8000 + i, 4, 0, 1, i + 1] for i in range(39)]
        out = p.send(0x114B, struct.pack(">IH", 7002, 1))
        check("the 41st deposit -> 0x2092 [u32 8] 'bank slots are full'",
              bodies(out, 0x2092) == [struct.pack(">I", 8)], out)
        close()
        out = p.send(0x114A, struct.pack(">I", 1))
        check("after the close, no window: a request is not answered",
              not [m for m in ids(out) if m in (0x208F, 0x2090)], ids(out))
    finally:
        p.leave()
    return H.finish("fe_bankloop_test")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("FAIL: %s" % e)
        sys.exit(1)
