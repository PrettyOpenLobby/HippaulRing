#!/usr/bin/env python3
"""fe_questloop_test.py -- the 2006 quests as conversations, real read loop.

Run from services/:  python ../tools/fe_questloop_test.py

2026-10-08 (round 2). Prod has never logged a quest answer. Each quest is a
talk on the event VM (the client has no quest system: PE 2005-12-19, before
SE's 2006-08-03 quest log), driven here the way the client drives it:

  0x2046 [npc][0] -> 0x1071; 0x20A7 -> 0x1172 + the first 0x1174 op;
  each balloon (op 3) acked (npc, 6, 0); the choice menu (op 0x107) answered
  (npc, 0x107, pick) -- the one VM op never sent live; the END window (op 8
  type 0x13) answered by the client's bare OUTER 0x2099 -> 0x1152 + 0x1175,
  and only THEN the bag and the wallet change (event_after).

  * Cheese (Cassius, script 115): offer -> Yes -> accepted; no cheese ->
    the reminder; cheese -> Yes -> the cheese is gone and 200 G paid; again
    at once -> "come back another day" (the default cooldown, ONE IN-GAME
    DAY = 2400 s, the default); a day later the menu is back; No
    and Esc pay nothing
  * the Luck test (Ficky, script 535, 30 G): Yes -> 30 G taken, one of
    Bread / Power Pot / Steak in the bag after the close; short of gold ->
    nothing taken; a full bag -> nothing taken
"""
import random
import struct
import sys
import time

import fe_harness as H
from fe_harness import check, ids, bodies, say

FIELD = 21          # a capital half


def place(args, rows):
    from world import town

    def change(blob):
        blob.setdefault(str(FIELD), {})["npcs"] = rows
        return True, True
    return town._town_change(args, change)


def ops(box):
    """(op, extra) of every 0x1174 in `box`."""
    out = []
    for b in bodies(box, 0x1174):
        op = struct.unpack_from(">IH", b)[1]
        if op == 8:
            out.append((8, struct.unpack_from(">H", b, 6)[0]))
        elif op in (3, 0x107):
            if op == 3:
                n = struct.unpack_from(">H", b, 6)[0]
                out.append((3, b[8:8 + n].decode("cp932")))
            else:
                n = struct.unpack_from(">I", b, 6)[0]
                out.append((0x107, b[10:10 + n].decode("cp932")))
        else:
            out.append((op, None))
    return out


def converse(p, npc, script, picks=()):
    """One whole talk. `picks` answers each menu in turn (an int = the
    option; None = Esc, a non-menu ack). Returns (every op seen, all out)."""
    picks = list(picks)
    allout = []
    out = p.send(0x2046, struct.pack(">II", npc, 0))
    allout += out
    out = p.send(0x20A7, struct.pack(">II", npc, script))
    allout += out
    seen = list(ops(out))
    for _ in range(20):
        if not seen or 0x1175 in ids(allout):
            break
        op, extra = seen[-1]
        if op == 8 and extra == 0x13:
            inbox = [(0x2099, struct.pack(">I", npc))]
            saved = H.fenet.recv_frame
            H.fenet.recv_frame = lambda conn: inbox.pop(0) if inbox else None
            n = len(p.out)
            try:
                with p.current(), H.quiet():
                    H.feworld._serve_loop(H._Conn(), p.args, None, None,
                                          "ecb", False)
            finally:
                H.fenet.recv_frame = saved
            allout += p.out[n:]
            break
        if op == 0x107:
            pick = picks.pop(0) if picks else None
            ack = (struct.pack(">IHI", npc, 6, 0) if pick is None
                   else struct.pack(">IHI", npc, 0x107, pick))
        else:
            ack = struct.pack(">IHI", npc, 6, 0)
        out = p.send(0x20A8, ack)
        allout += out
        seen += ops(out)
    return seen, allout


def main():
    H.setup()
    from world import events
    args = H.real_args(bag_size=30)
    check("real defaults: quests on, cooldown ONE IN-GAME DAY (2400 s)",
          args.quests == "on" and args.quest_cooldown_secs == 2400,
          args.quest_cooldown_secs)
    check("...the cooldown the cheese quest serves is that day",
          events.quest_cooldown(args, "cheese") == 2400)
    check("place Cassius (115) and Ficky (535)", place(args, [
        {"name": "Cassius", "script": 115, "pos": [0, 0, 0]},
        {"name": "Ficky", "script": 535, "pos": [5, 0, 0]}]))
    base = int(args.monster_base) + 100
    cassius, ficky = base, base + 1
    p = H.Peer("Questor", 81, args, nation=1, field=FIELD, char={
        "gold": 1000, "bag_size": 30, "items": [[8101, 1, 0, 1]],
        "quests": {}})
    p.session["luck_rng"] = random.Random(7)
    try:
        seen, out = converse(p, cassius, 115, [0])
        kinds = [o for o, _x in seen]
        check("Cassius: offer, the Yes/No MENU (op 0x107), the accept line, END",
              kinds == [3, 0x107, 3, 8], seen)
        check("...0x2099 closes it: 0x1152 then 0x1175",
              [m for m in ids(out) if m in (0x1152, 0x1175)][-2:]
              == [0x1152, 0x1175], ids(out))
        check("...and the quest is ACCEPTED on the character",
              p.char.get("quests", {}).get("cheese", {}).get("on") is True,
              p.char.get("quests"))
        seen, out = converse(p, cassius, 115)
        check("no cheese yet: the reminder and END, no menu",
              [o for o, _x in seen] == [3, 8], seen)
        # the cheese arrives (a drop or the shop)
        p.char["items"] = p.char["items"] + [[8102, 7, 0, 1]]
        gold0 = p.char["gold"]
        seen, out = converse(p, cassius, 115, [1])
        check("holding cheese, answering No: nothing taken, nothing paid",
              [o for o, _x in seen] == [3, 0x107, 3, 8]
              and p.char["gold"] == gold0
              and 7 in [r[1] for r in p.char["items"]], seen)
        seen, out = converse(p, cassius, 115, [None])
        check("...Esc (a non-menu ack) is the cancel branch: nothing either",
              p.char["gold"] == gold0
              and 7 in [r[1] for r in p.char["items"]], seen)
        seen, out = converse(p, cassius, 115, [0])
        check("Yes: the cheese is gone and 200 G paid AFTER the close",
              p.char["gold"] == gold0 + 200
              and 7 not in [r[1] for r in p.char["items"]], (p.char["gold"],
                                                              p.char["items"]))
        p.char["items"] = p.char["items"] + [[8103, 7, 0, 1]]
        seen, out = converse(p, cassius, 115)
        check("a second cheese at once: 'come back another day', no menu",
              [o for o, _x in seen] == [3, 8]
              and "another" in seen[0][1], seen)
        st = p.char["quests"]
        st["cheese"]["last"] = int(time.time()) - 2401
        p.char["quests"] = st
        seen, out = converse(p, cassius, 115, [0])
        check("one in-game day later: the hand-in menu again, paid",
              0x107 in [o for o, _x in seen]
              and p.char["gold"] == gold0 + 400, (seen, p.char["gold"]))

        # ---- the luck test
        gold0 = p.char["gold"]
        n0 = len(p.char["items"])
        seen, out = converse(p, ficky, 535, [0])
        check("Ficky: offer, 'Try your luck for 30 gold?', the prize line, END",
              [o for o, _x in seen] == [3, 0x107, 3, 8]
              and "30 gold" in seen[1][1], seen)
        new = [r for r in p.char["items"][n0:]]
        check("...30 G taken and ONE of Bread/Power Pot/Steak in the bag",
              p.char["gold"] == gold0 - 30 and len(new) == 1
              and new[0][1] in (4, 20, 35), (p.char["gold"], new))
        check("...the line names what was drawn",
              any(name in seen[2][1] for _i, _p, name in events.LUCK_PRIZES),
              seen[2])
        p.char["gold"] = 10
        n0 = len(p.char["items"])
        seen, out = converse(p, ficky, 535, [0])
        check("10 G on hand: 'haven't got the gold', nothing taken or given",
              p.char["gold"] == 10 and len(p.char["items"]) == n0
              and "gold" in seen[-2][1], seen)
        p.char["gold"] = 500
        p.char["items"] = [[9000 + i, 4, 0, 1] for i in range(30)]
        seen, out = converse(p, ficky, 535, [0])
        check("a full bag: 'pack is full', no fee", p.char["gold"] == 500
              and "full" in seen[-2][1], seen)
        counts = {}
        rng = random.Random(1)
        for _ in range(10000):
            item, _n = events.luck_roll(rng)
            counts[item] = counts.get(item, 0) + 1
        check("the odds are July 2006's: ~53.8 / 42 / 4.2 %",
              abs(counts[4] / 100.0 - 53.8) < 2 and abs(counts[20] / 100.0 - 42)
              < 2 and abs(counts[35] / 100.0 - 4.2) < 1, counts)
    finally:
        p.leave()
    return H.finish("fe_questloop_test")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("FAIL: %s" % e)
        sys.exit(1)
