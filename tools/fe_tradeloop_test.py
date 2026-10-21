#!/usr/bin/env python3
"""fe_tradeloop_test.py -- a player TRADE end to end, two sessions, real defaults.

Run from services/:  python ../tools/fe_tradeloop_test.py

2026-10-08 (round 2). Prod has never seen a 0x2052. Two players in one field
trade through feworld's own objects (fe_harness): the requester's popup, the
partner's permission popup, both entries, both confirms, the swap applied on
each side's own session, and every reply read the way its arm reads it:

  0x108B 0x050535ca   [u32 target][u16 tid]; the requester popup ignores it
                      unless the u32 is the unit it asked for (0x050b4346)
  0x2052 0x05053b08   [u32 face][u16 tid]; face is looked up as a class-3
                      unit = the requester's avatar (its charid, fepresence)
  0x108D 0x05053748   [u16 tid][u16 SlotCnt]
  0x108E 0x050537c0   header-only
  0x1090 0x050b3ac2   [u16 tid][u32 A][u32 B][u16 n] + item_fields
  0x2055 0x05053baa   header-only ('Trade complete.')
  0x108F / 0x2056 / 0x2057  [u16 tid][u32 code]

and A is GOLD, B is CRYSTAL -- read off the trade window's own boxes
(0x050b2047: the +0xe0 box is capped by [player+0x4ec] GOLD at 0x050b2080,
the +0xe4 box by [player+0x8ac] CRYSTAL at 0x050b210d).
"""
import struct
import sys

import fe_harness as H
from fe_harness import check, ids, bodies, say


def main():
    H.setup()
    import fetrade
    # crystals count only during a war (fecampaign._crystal_expire zeroes
    # them anywhere else, SE's economy page); the war itself is
    # fe_warloop_test's. Here the carried crystal stays put.
    args = H.real_args(bag_size=30, crystal_reset="off")
    check("real defaults: --trade on, phantom off, 9 slots, unit ids auto",
          args.trade == "on" and args.trade_phantom == "off"
          and args.trade_slots == 9 and args.unit_id == "auto")
    alice = H.Peer("Alice", 41, args, nation=1, field=21, char={
        "gold": 1000, "crystal": 10, "bag_size": 30,
        "items": [[5001, 4, 0, 3], [5002, 7, 0, 1]]})
    bob = H.Peer("Bob", 42, args, nation=1, field=21, char={
        "gold": 200, "crystal": 45, "bag_size": 30,
        "items": [[6001, 1836, 0, 2]]})
    eve = H.Peer("Eve", 43, args, nation=1, field=22, char={"gold": 5})
    seen = H.see_each_other(alice, bob, eve)
    check("fepresence draws Alice and Bob for each other (Eve is elsewhere)",
          seen[alice.charid] == [bob.charid] and seen[bob.charid]
          == [alice.charid], seen)
    try:
        # ---- the request
        out = alice.send(0x2052, struct.pack(">I", bob.charid))
        ok = bodies(out, 0x108B)
        check("Alice: 0x108B [u32 Bob's unit][u16 tid] (the popup's own id)",
              len(ok) == 1 and len(ok[0]) == 6
              and struct.unpack(">IH", ok[0])[0] == bob.charid, out)
        tid = struct.unpack(">IH", ok[0])[1]
        got = H.deliver(alice, bob)
        req = bodies(got[bob.charid], 0x2052)
        check("Bob: 0x2052 [u32 face = Alice's avatar][u16 tid]",
              req == [struct.pack(">IH", alice.charid, tid)], req)
        # ---- a third player in another field cannot be asked
        out = alice.send(0x2052, struct.pack(">I", eve.charid))
        check("a second request while trading -> 0x108C [u32 12]",
              bodies(out, 0x108C) == [struct.pack(">I", 12)], out)
        # ---- Bob accepts: both get 0x108D
        out = bob.send(0x2053, struct.pack(">H", tid))
        got = H.deliver(alice, bob)
        check("Bob and Alice: 0x108D [u16 tid][u16 9]",
              bodies(out, 0x108D) == [struct.pack(">HH", tid, 9)]
              and bodies(got[alice.charid], 0x108D)
              == [struct.pack(">HH", tid, 9)], (out, got))
        # ---- entries: Alice 2 bread + 100 gold; Bob 1 goblin book + 5 crystal
        out = alice.send(0x2054, struct.pack(">HIIH", tid, 100, 0, 1)
                         + struct.pack(">IH", 5001, 2))
        check("Alice's entry -> 0x108E header-only",
              bodies(out, 0x108E) == [b""], out)
        got = H.deliver(alice, bob)
        off = bodies(got[bob.charid], 0x1090)
        check("Bob sees Alice's offer: 0x1090 [tid][A=100][B=0][n=1]+fields",
              len(off) == 1 and struct.unpack_from(">HIIH", off[0])
              == (tid, 100, 0, 1), off)
        # an offer he cannot cover is refused in the client's words
        out = bob.send(0x2054, struct.pack(">HIIH", tid, 0, 99, 0))
        check("Bob offers 99 crystal holding 45 -> 0x108F [tid][u32 10]",
              bodies(out, 0x108F) == [struct.pack(">HI", tid, 10)], out)
        out = bob.send(0x2054, struct.pack(">HIIH", tid, 0, 5, 1)
                       + struct.pack(">IH", 6001, 1))
        check("Bob's entry -> 0x108E", bodies(out, 0x108E) == [b""], out)
        H.deliver(alice, bob)
        # ---- both confirm
        out = alice.send(0x2055, struct.pack(">H", tid))
        check("Alice confirms first: nothing yet (waits for Bob)",
              not bodies(out, 0x2055), ids(out))
        out = bob.send(0x2055, struct.pack(">H", tid))
        got = H.deliver(alice, bob)
        check("both get 0x2055 header-only ('Trade complete.')",
              bodies(out, 0x2055) + bodies(got[bob.charid], 0x2055) == [b""]
              and bodies(got[alice.charid], 0x2055) == [b""], (ids(out), got))
        a_items = {r[1]: r[3] for r in map(tuple, alice.char["items"])}
        b_items = {r[1]: r[3] for r in map(tuple, bob.char["items"])}
        check("Alice: 1 bread + cheese left, a goblin book in",
              a_items == {4: 1, 7: 1, 1836: 1}, a_items)
        check("Bob: his other goblin book, 2 bread in",
              b_items == {1836: 1, 4: 2}, b_items)
        check("gold 1000-100 / 200+100, crystal 10+5 / 45-5",
              (alice.char["gold"], bob.char["gold"], alice.char["crystal"],
               bob.char["crystal"]) == (900, 300, 15, 40),
              (alice.char["gold"], bob.char["gold"], alice.char["crystal"],
               bob.char["crystal"]))
        check("no trade left open, both sessions free",
              fetrade.TRADES == {} and not alice.session.get("trade")
              and not bob.session.get("trade"))

        # ---- the 50-crystal cap (SE warsystem01; NG 14 'partner's crystal')
        out = alice.send(0x2052, struct.pack(">I", bob.charid))
        tid = struct.unpack(">IH", bodies(out, 0x108B)[0])[1]
        H.deliver(alice, bob)
        bob.send(0x2053, struct.pack(">H", tid))
        H.deliver(alice, bob)
        out = alice.send(0x2054, struct.pack(">HIIH", tid, 0, 15, 0))
        check("15 crystal to Bob holding 40 (55 > 50) -> 0x108F [tid][u32 14]",
              bodies(out, 0x108F) == [struct.pack(">HI", tid, 14)], out)
        # ---- the window's CANCEL: the partner reads 'Partner cancelled'
        out = alice.send(0x2056, struct.pack(">HI", tid, 0))
        got = H.deliver(alice, bob)
        check("Alice cancels -> Bob gets 0x2056 [tid][u32 1]",
              bodies(got[bob.charid], 0x2056) == [struct.pack(">HI", tid, 1)],
              got)
        check("...and nothing moved", alice.char["crystal"] == 15)

        # ---- a request declined from the permission popup (0x2057)
        out = alice.send(0x2052, struct.pack(">I", bob.charid))
        tid = struct.unpack(">IH", bodies(out, 0x108B)[0])[1]
        H.deliver(alice, bob)
        bob.send(0x2057, struct.pack(">HI", tid, 0))
        got = H.deliver(alice, bob)
        check("Bob declines -> Alice's popup gets 0x2057 [tid][u32 2]",
              bodies(got[alice.charid], 0x2057) == [struct.pack(">HI", tid, 2)],
              got)

        # ---- the partner logs out with the window open
        out = alice.send(0x2052, struct.pack(">I", bob.charid))
        tid = struct.unpack(">IH", bodies(out, 0x108B)[0])[1]
        H.deliver(alice, bob)
        bob.send(0x2053, struct.pack(">H", tid))
        H.deliver(alice, bob)
        bob.call(fetrade.session_end, "logout")
        got = H.deliver(alice, bob)
        check("Bob logs out mid-trade -> Alice gets 0x2056 [tid][u32 1]",
              bodies(got[alice.charid], 0x2056) == [struct.pack(">HI", tid, 1)],
              got)
        # ---- another field
        out = alice.send(0x2052, struct.pack(">I", eve.charid))
        check("a player in another field -> 0x108C [u32 5]",
              bodies(out, 0x108C) == [struct.pack(">I", 5)], out)
    finally:
        for p in (alice, bob, eve):
            try:
                p.call(fetrade.session_end, "test end")
            except Exception:                          # noqa: BLE001
                pass
            p.leave()
    return H.finish("fe_tradeloop_test")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("FAIL: %s" % e)
        sys.exit(1)
