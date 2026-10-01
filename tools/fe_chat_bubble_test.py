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


def redirect_stdout_quiet():
    import contextlib
    import io
    return contextlib.redirect_stdout(io.StringIO())


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
        del speakers[:]

        say("2026-10-01: /say range, /tell before speaking, blacklist case")
        rx = feworld._TLS.session
        rx.update({"cpos": (0.0, 0.0, 0.0), "room": -1, "charid": 9})

        def send_from(mid, parts, pos, room=-1):
            def run():
                feworld._TLS.session = {"in_field": True, "field": 5,
                                        "charid": 7, "cpos": pos, "room": room}
                got["n"] = feworld.chat_relay(mid, parts, field=5)
            t = threading.Thread(target=run)
            t.start()
            t.join()
            return got["n"]

        feworld.chat._SAY_RANGE[0] = feworld.chat.SAY_RANGE_DEFAULT
        check("/say from 30 u away arrives (default --say-range 60, CHOSEN)",
              send_from(SAY, ["Quinn", "near"], (30.0, 5.0, 0.0)) == 1)
        check("/say from 100 u away does not",
              send_from(SAY, ["Quinn", "far"], (100.0, 0.0, 0.0)) == 0)
        check("/all from 100 u away still does (the whole field)",
              send_from(ALL, ["Quinn", "far"], (100.0, 0.0, 0.0)) == 1)
        check("/say from another room (a shop) does not",
              send_from(SAY, ["Quinn", "x"], (1.0, 0.0, 0.0), room=10) == 0)
        check("/say from a sender with no position yet arrives",
              send_from(SAY, ["Quinn", "x"], None) == 1)
        feworld.chat._SAY_RANGE[0] = 0.0
        check("--say-range 0 is the old behaviour: the whole field",
              send_from(SAY, ["Quinn", "far"], (100.0, 0.0, 0.0)) == 1)
        feworld.chat._SAY_RANGE[0] = feworld.chat.SAY_RANGE_DEFAULT
        feworld.chat_pump(None, None, "ecb", False, _args())
        del speakers[:]

        # /tell to a player who has never chatted: the room entry is named
        # from the STORE by chat_pump (it used to be empty until the first line)
        saved_load = feworld._load_char_field
        feworld._load_char_field = lambda a, k, d=None: "Alice" if k == "name" else d
        try:
            with feworld._CHAT_ROOM_LOCK:
                ent = feworld._CHAT_ROOM.get(threading.get_ident())
                ent["name"] = ""
                ent.pop("name_retry", None)      # as at the first field entry
            check("before: a /tell to Alice reaches nobody",
                  send_from(TELL, ["alice", "Quinn", "psst"], None) == 0)
            feworld.chat_pump(None, None, "ecb", False, _args())
            check("chat_pump names the entry from the stored character",
                  ent["name"] == "Alice", repr(ent["name"]))
            check("after: the /tell (typed lower-case) reaches her",
                  send_from(TELL, ["alice", "Quinn", "psst"], None) == 1)
            feworld.chat_pump(None, None, "ecb", False, _args())
            check("...and is delivered", speakers == [(TELL, 0)], repr(speakers))
        finally:
            feworld._load_char_field = saved_load
        del speakers[:]

        # the blacklist holds the name UPPER-CASED (the client's 0x20A0)
        feworld.blacklist_rows = lambda args: [{"id": 0x40000001, "name": "QUINN"}]
        send_from(SAY, ["Quinn", "hi"], (1.0, 0.0, 0.0))
        feworld.chat_pump(None, None, "ecb", False, _args())
        check("a line from 'Quinn' is dropped by a blacklist row 'QUINN'",
              speakers == [], repr(speakers))
        feworld.blacklist_rows = lambda args: [{"id": 7, "name": "SOMEONE"}]
        send_from(SAY, ["Renamed", "hi"], (1.0, 0.0, 0.0))
        feworld.chat_pump(None, None, "ecb", False, _args())
        check("...and by the speaker's real charid whatever it signs as",
              speakers == [], repr(speakers))
        feworld._chat_room_leave()

        say("blacklist ids: a name resolves to the REAL charid, any account")
        store = {}
        saved_bl = (feworld._load_char_field, feworld._store_char_field,
                    feworld.send, feworld.character._ALL_CHARS_FN)
        feworld.blacklist_rows = saved[1]
        feworld._load_char_field = lambda a, k, d=None: store.get(k, d)
        feworld._store_char_field = lambda a, k, v: (store.__setitem__(k, v), True)[1]
        feworld.send = lambda *a, **k: None
        feworld.character._ALL_CHARS_FN = lambda: [("acct-b", 8, "Jimmy"),
                                                   ("acct-c", 9, "Carol")]
        try:
            a = _args()
            nid, rows, fresh = feworld.blacklist_add(a, "JIMMY")
            check("'JIMMY' (as the client sends it) -> charid 8 of another "
                  "account, not a synthetic id", (nid, fresh) == (8, True),
                  repr((nid, fresh)))
            nid2, rows2, fresh2 = feworld.blacklist_add(a, "jimmy")
            check("...a second add in another case is the same row",
                  (nid2, fresh2, len(rows2)) == (8, False, 1), repr(rows2))
            nid3, _r, _f = feworld.blacklist_add(a, "NOBODY")
            check("an unknown name still gets a synthetic id",
                  nid3 >= feworld.BLACKLIST_ID_BASE, hex(nid3))
            store["blacklist"].append({"id": nid3 + 1, "name": "CAROL"})
            with redirect_stdout_quiet():
                feworld.blacklist_send(None, None, 0, False, a)
            check("0x1163 upgrades an old synthetic row to the real charid",
                  [r["id"] for r in store["blacklist"]] == [8, nid3, 9],
                  repr(store["blacklist"]))
            check("blacklist_hit: by real id, by folded name, never by a "
                  "synthetic id", feworld.character.blacklist_hit(
                      store["blacklist"], 9, "x")
                  and feworld.character.blacklist_hit(store["blacklist"],
                                                      None, "nobody")
                  and not feworld.character.blacklist_hit(store["blacklist"],
                                                          nid3, "x"))
        finally:
            (feworld._load_char_field, feworld._store_char_field,
             feworld.send, feworld.character._ALL_CHARS_FN) = saved_bl
    finally:
        feworld.chat_send, feworld.blacklist_rows, feworld.nation_of = saved

    say("\n%d/%d checks passed" % (sum(ok for _, ok in CHECKS), len(CHECKS)))


if __name__ == "__main__":
    main()
