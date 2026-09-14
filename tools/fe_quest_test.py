#!/usr/bin/env python3
"""fe_quest_test.py -- quests on the event VM, driven through the real engine.

Run from services/:  python ../tools/fe_quest_test.py

WHY. The client has no quest system (built 2005-12-19, before SE's 2006-08-03
quest log); a 2006 quest was a server-driven conversation. This drives
quest_script_for -> event_step -> the 0x20A8 pick (event_menu_pick) ->
conversation_close -> event_after with the store, bag push and wallet
stubbed, and checks the rules that matter: only a clear Yes pays, the item
must still be there at the close, the cooldown holds, and nothing hangs.
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
    a = dict(quests="on", quest_cooldown_secs=-1, seq_mode="count",
             world_prefix=4, unit_id="1", talk_wrap=48, talk_lines=4)
    a.update(kw)
    return types.SimpleNamespace(**a)


CHEESE, GIVER, NPC = 7, {"name": "Cassius", "script": 115}, 1519


def main():
    store = {"items": [], "quests": {}}
    sent, credits, pushes = [], [], []
    real_inner = feworld.inner_msg

    def inner(mid, payload=b"", *a, **k):
        sent.append((mid, bytes(payload)))
        return real_inner(mid, payload, *a, **k)

    saved = {n: getattr(feworld, n) for n in (
        "send", "inner_msg", "_load_char_field", "_store_char_field",
        "item_rows", "bag_layout_push", "wallet_credit", "room_exit_push")}
    feworld.send = lambda *a, **k: None
    feworld.inner_msg = inner
    feworld._load_char_field = lambda args, key, default=None: store.get(key, default)

    def _store(args, key, value):
        store[key] = value
        return True
    feworld._store_char_field = _store
    feworld.item_rows = lambda args: [tuple(r) for r in store["items"]]
    feworld.bag_layout_push = lambda *a, **k: pushes.append(a[-1])
    feworld.wallet_credit = (lambda conn, out, mode, be, args, gold=0, rings=0,
                             why="": credits.append((gold, why)) or gold)
    feworld.room_exit_push = lambda *a, **k: None

    def ops():
        """the 0x1174 ops sent since the last clear: (op, text-or-None)"""
        out = []
        for mid, p in sent:
            if mid == 0x1174:
                op = struct.unpack_from(">H", p, 4)[0]
                out.append(op)
            elif mid in (0x1175, 0x1152):
                out.append(hex(mid))
        return out

    def talk(args, row=GIVER):
        """0x2046 -> script -> 0x20A7: the event starts, first command out"""
        steps = feworld.event_script_for(args, row)
        feworld._SESSION["event"] = {"npc": NPC, "script": row["script"],
                                     "steps": steps, "pc": 0,
                                     "name": row["name"], "started": True}
        del sent[:]
        feworld.event_step(None, None, "ecb", False, args)
        return steps

    def ack(a, b, c, args):
        """the 0x20A8 arm's own order: log the ack, resolve a menu, step"""
        ev = feworld._SESSION.get("event")
        ev.setdefault("acks", []).append((a, b, c))
        if ev.get("await_menu") is not None:
            feworld.event_menu_pick(ev, a, b, c)
        feworld.event_step(None, None, "ecb", False, args)

    def close(args):
        feworld.conversation_close(None, None, "ecb", False, args, b"", "outer")

    try:
        args = _args()
        feworld._TLS.session = {"in_field": True}

        say("who gives a quest")
        check("a non-giver has no quest", feworld.quest_script_for(
            args, {"name": "Someone", "script": 999}) is None)
        check("--quests off: a giver plays its plain line",
              feworld.quest_script_for(_args(quests="off"), GIVER) is None)
        check("all ten 2006 givers are mapped", len(feworld.QUEST_BY_GIVER) == 10)

        say("first talk: offer -> menu -> Yes -> accepted at the close")
        steps = talk(args)
        check("the offer line goes first", ops() == [feworld.EV_TEXT], repr(ops()))
        del sent[:]
        ack(NPC, 6, 0, args)                     # the text box clicked through
        check("...then the Yes/No menu (op 0x107)",
              ops() == [feworld.EV_MENU], repr(ops()))
        check("...and the engine waits for its pick",
              feworld._SESSION["event"].get("await_menu") is not None)
        del sent[:]
        ack(NPC, 0x107, 0, args)                 # Yes
        check("Yes -> the accept line", ops() == [feworld.EV_TEXT], repr(ops()))
        check("...nothing stored yet (deferred to the close)",
              store["quests"] == {})
        del sent[:]
        ack(NPC, 6, 0, args)
        check("...then the END window", ops() == [feworld.EV_WINDOW], repr(ops()))
        close(args)
        check("closed: the quest is ACCEPTED on the character",
              store["quests"].get("cheese", {}).get("on") is True, repr(store))

        say("accepted, no cheese: a reminder, nothing else")
        steps = talk(args)
        check("reminder + END, no menu",
              [s[0] for s in steps] == ["text", "window"], repr(steps))
        close(args)
        check("...no reward", credits == [] and pushes == [])

        say("holding cheese: ask -> menu -> Yes -> taken + 200 G at the close")
        store["items"] = [[1007, CHEESE, 0, 1], [1002, 1666, 0, 1]]
        talk(args)
        del sent[:]
        ack(NPC, 6, 0, args)
        check("the hand-in menu", ops() == [feworld.EV_MENU], repr(ops()))
        ack(NPC, 0x107, 0, args)                 # Yes
        check("nothing taken before the close", len(store["items"]) == 2
              and credits == [])
        ack(NPC, 6, 0, args)
        close(args)
        check("the cheese is GONE from the bag (the armour stays)",
              [r[1] for r in store["items"]] == [1666], repr(store["items"]))
        check("...a bag push went out", pushes == ["quest cheese hand-in"],
              repr(pushes))
        check("...and 200 gold credited", credits == [(200, "quest cheese reward")],
              repr(credits))
        st = store["quests"]["cheese"]
        check("...one hand-in counted, stamped now",
              st.get("n") == 1 and abs(st.get("last", 0) - time.time()) < 5)

        say("the cooldown")
        store["items"].append([1008, CHEESE, 0, 1])
        del credits[:], pushes[:]
        steps = talk(args)
        check("inside the cooldown: 'come back another day', no menu",
              [s[0] for s in steps] == ["text", "window"]
              and "another day" in steps[0][1], repr(steps))
        st["last"] = time.time() - feworld.QUEST_DAY_S - 1
        steps = talk(args)
        check("after one in-game day: the hand-in menu again",
              any(s[0] == "menu" for s in steps))
        check("--quest-cooldown-secs overrides it",
              feworld.quest_cooldown(_args(quest_cooldown_secs=60), "cheese") == 60)

        say("No, Esc, and a cheese that vanished")
        feworld._SESSION.pop("event", None)
        talk(args)
        ack(NPC, 6, 0, args)
        ack(NPC, 0x107, 1, args)                 # No
        ack(NPC, 6, 0, args)
        close(args)
        check("No: nothing taken, nothing paid",
              credits == [] and any(r[1] == CHEESE for r in store["items"]))
        talk(args)
        ack(NPC, 6, 0, args)
        del sent[:]
        ack(NPC, 6, 0, args)                     # NOT a menu reply (Esc?)
        check("a non-menu ack while the menu is up -> the cancel branch, "
              "no hang", ops() == [feworld.EV_TEXT], repr(ops()))
        ack(NPC, 6, 0, args)
        close(args)
        check("...and no reward", credits == [])
        talk(args)
        ack(NPC, 6, 0, args)
        ack(NPC, 0x107, 7, args)                 # an option we never offered
        ack(NPC, 6, 0, args)
        close(args)
        check("an unknown option value -> cancel, no reward", credits == [])
        talk(args)
        ack(NPC, 6, 0, args)
        ack(NPC, 0x107, 0, args)                 # Yes...
        store["items"] = [r for r in store["items"] if r[1] != CHEESE]
        ack(NPC, 6, 0, args)
        close(args)
        check("Yes, but the cheese left the bag before the close: NO reward",
              credits == [], repr(credits))

        say("Goblin Book (the same shape; unobtainable until drops)")
        store["quests"] = {}
        steps = feworld.quest_script_for(args, {"name": "Bakin", "script": 340})
        check("Bakin offers the Goblin Book quest",
              steps and "Goblin Book" in steps[0][1] and steps[1][0] == "menu")
    finally:
        for n, f in saved.items():
            setattr(feworld, n, f)

    say("\n%d/%d checks passed" % (sum(ok for _, ok in CHECKS), len(CHECKS)))


if __name__ == "__main__":
    main()
