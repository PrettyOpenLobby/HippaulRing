#!/usr/bin/env python3
"""fe_effects_test.py -- 0x1036 REMOVE EFFECTS, both directions.

Run from services/:  python ../tools/fe_effects_test.py

2026-10-08 (round 2). The client names effects it wants ended ([u8 n] n x
[u16 EFFECT_DATA id]; senders 0x05038cf5, 0x0506bf20, 0x0506ce8e) and keeps
each effect node until the server answers with the same body on the c0 arm
0x0503cef0 (header = the unit, then 0x0506be20 per id). This drives it
through the real read loop with two players drawing each other:

  * Hide (effect 123) named by its holder: the server's hide ends, the
    CONDITION word drops 0x2000, and 0x1036 [1][123] goes back to the holder
    (its own unit) and to the peer under the holder's charid
  * a BAD status (poison) named: echoed so the node ends, but the server's
    poison keeps its timer (--effect-remove good)
  * a short body decodes what is there; --effect-remove off answers nothing
  * the RoD DoT floor: poison/burn never take a player below 1 HP
"""
import struct
import sys
import time

import fe_harness as H
from fe_harness import check, ids, bodies, say


def main():
    H.setup()
    import feunit
    from world import status
    args = H.real_args()
    check("real defaults: --effect-remove good, --dot-floor 1, wire on",
          args.effect_remove == "good" and args.dot_floor == 1
          and args.status_effects_wire == "on")
    alice = H.Peer("Alice", 61, args, nation=2, field=30)
    bob = H.Peer("Bob", 62, args, nation=2, field=30)
    try:
        seen = H.see_each_other(alice, bob)
        check("both drawn for each other", seen == {61: [62], 62: [61]}, seen)
        now = time.monotonic()
        alice.session.setdefault("status", {})["hide"] = {
            "until": now + 60, "effect": 123, "src": 61, "skill": 140}
        alice.session["status_bits_sent"] = status.COND_HIDE
        out = alice.send(0x1036, struct.pack(">BH", 1, 123))
        echo = [(u, b) for m, u, b in out if m == 0x1036]
        check("Alice's client: 0x1036 [1][123] on her own unit",
              echo == [(61, struct.pack(">BH", 1, 123))], echo)
        check("...her Hide is over server-side",
              "hide" not in alice.session.get("status", {}))
        cond = [struct.unpack(">III", b)[2] for b in bodies(out, 0x2024)
                if struct.unpack_from(">I", b)[0] == 0x100000]
        check("...and the CONDITION word went out without 0x2000",
              cond and not cond[-1] & status.COND_HIDE, cond)
        got = H.deliver(alice, bob)
        relay = [(u, b) for m, u, b in got[bob.charid] if m == 0x1036]
        check("Bob's client: the same 0x1036 on Alice's avatar (unit 61)",
              relay == [(61, struct.pack(">BH", 1, 123))], relay)

        # a bad status: echoed (the node is the client's), kept server-side
        alice.session["status"]["poison"] = {
            "until": time.monotonic() + 60, "effect": 133, "amount": 15,
            "tick_ms": 3000, "next_at": time.monotonic() + 3}
        out = alice.send(0x1036, struct.pack(">BH", 1, 133))
        check("poison named: echoed, but the server's poison keeps running",
              bodies(out, 0x1036) == [struct.pack(">BH", 1, 133)]
              and "poison" in alice.session["status"], ids(out))
        H.deliver(alice, bob)

        # the decoder
        check("a short body yields the ids it holds",
              feunit.decode_remove_effects(b"\x03\x00\x01\x00") == [1])
        check("the body builder round-trips",
              feunit.decode_remove_effects(
                  feunit.remove_effects_body([5, 300])) == [5, 300])
        off = H.real_args(effect_remove="off")
        alice.args = off
        out = alice.send(0x1036, struct.pack(">BH", 1, 123))
        check("--effect-remove off: no echo", not bodies(out, 0x1036), ids(out))
        alice.args = args

        # the DoT floor: 20 HP left, a 43-a-tick poison
        alice.session["player_hp"] = 20
        alice.session["status"]["poison"] = {
            "until": time.monotonic() + 60, "effect": 137, "amount": 43,
            "tick_ms": 100, "next_at": time.monotonic() - 1}
        _r, out = alice.call(status.player_pump, None, None, "ecb", False,
                             args)
        check("poison at 20 HP leaves 1 HP, no death (RoD: never below 1)",
              alice.session["player_hp"] == 1
              and not alice.session.get("player_dead"),
              alice.session.get("player_hp"))
    finally:
        alice.leave()
        bob.leave()
    return H.finish("fe_effects_test")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("FAIL: %s" % e)
        sys.exit(1)
