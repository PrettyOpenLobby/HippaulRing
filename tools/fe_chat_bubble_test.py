#!/usr/bin/env python3
"""fe_chat_bubble_test.py -- a speech balloon over the player who spoke.

Run from services/:  python ../tools/fe_chat_bubble_test.py

WHY. The chat arm (0x05053cbe) SKIPS the balloon only when the speaker id
names a kind-1 unit -- the local player, whose own /say already drew one --
and otherwise hands the id to the balloon (0x5129890), which accepts a kind-2
avatar: exactly what fepresence draws a peer as, under its charid. We always
relayed speaker 0, so no balloon ever appeared over another player.

Drives the real functions: chat_relay (queues the sender's charid),
chat_pump (drains on the receiver's thread) and bubble_speaker.
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
    say("  %-66s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label + (": " + detail if detail else ""))


def _args(**kw):
    a = dict(chat_relay="on", chat_bubbles="all", seq_mode="count",
             world_prefix=4, unit_id="auto")
    a.update(kw)
    return types.SimpleNamespace(**a)


SAY, ALL, TELL = 0x201A, 0x201B, 0x2067


def main():
    speakers = []
    saved = (feworld.chat_send, feworld.blacklist_rows, feworld.nation_of)
    feworld.chat_send = (lambda conn, outbound, mode, be, args, mid, parts,
                         speaker=None, **k: speakers.append((mid, speaker)))
    feworld.blacklist_rows = lambda args: []
    feworld.nation_of = lambda args: 1
    try:
        say("bubble_speaker")
        feworld._TLS.session = {"pres_seen": {7: {}}}
        a = _args()
        check("a peer this client draws -> its charid",
              feworld.bubble_speaker(a, SAY, 7) == 7)
        check("/all bubbles too under 'all' (the arm treats them alike)",
              feworld.bubble_speaker(a, ALL, 7) == 7)
        check("'say' keeps /all at 0",
              feworld.bubble_speaker(_args(chat_bubbles="say"), ALL, 7) == 0
              and feworld.bubble_speaker(_args(chat_bubbles="say"), SAY, 7) == 7)
        check("'off' is the old behaviour: 0",
              feworld.bubble_speaker(_args(chat_bubbles="off"), SAY, 7) == 0)
        check("/tell keeps 0 (a different arm, unread)",
              feworld.bubble_speaker(a, TELL, 7) == 0)
        check("a charid this client does NOT draw -> 0 (never a wrong unit)",
              feworld.bubble_speaker(a, SAY, 9) == 0)
        check("no charid -> 0", feworld.bubble_speaker(a, SAY, 0) == 0)

        say("chat_relay -> chat_pump, end to end")
        feworld._TLS.session = {"in_field": True, "field": 5, "pres_seen": {7: {}}}
        feworld._chat_room_join()
        me = feworld._chat_room_self() if hasattr(feworld, "_chat_room_self") else None
        # a SENDER on another 'thread': queue as chat_relay would from there
        import threading
        got = {}

        def sender():
            feworld._TLS.session = {"in_field": True, "field": 5, "charid": 7}
            got["n"] = feworld.chat_relay(SAY, ["Quinn", "hello"], field=5)
        t = threading.Thread(target=sender)
        t.start()
        t.join()
        check("the line reached this session's queue", got.get("n") == 1,
              repr(got))
        feworld.chat_pump(None, None, "ecb", False, _args())
        check("...and went out with the SENDER's charid as the speaker",
              speakers == [(SAY, 7)], repr(speakers))
        del speakers[:]
        feworld._TLS.session["pres_seen"] = {}
        t = threading.Thread(target=sender)
        t.start()
        t.join()
        feworld.chat_pump(None, None, "ecb", False, _args())
        check("a sender this client is not drawing: speaker 0, log line only",
              speakers == [(SAY, 0)], repr(speakers))
        del speakers[:]
        # an old-shape 2-tuple (a queue filled before the upgrade) still drains
        with feworld._CHAT_ROOM_LOCK:
            ent = feworld._CHAT_ROOM.get(threading.get_ident())
        ent["q"].put((SAY, ["Quinn", "old"]))
        feworld.chat_pump(None, None, "ecb", False, _args())
        check("a 2-item queue entry still drains (speaker 0)",
              speakers == [(SAY, 0)], repr(speakers))
        feworld._chat_room_leave()
    finally:
        feworld.chat_send, feworld.blacklist_rows, feworld.nation_of = saved

    say("\n%d/%d checks passed" % (sum(ok for _, ok in CHECKS), len(CHECKS)))


if __name__ == "__main__":
    main()
