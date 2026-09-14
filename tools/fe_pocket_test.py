#!/usr/bin/env python3
"""fe_pocket_test.py -- a POCKET item can be used (0x2051), live bug 2026-09-12.

Observed in live testing: the player used both pocket items, the client
emptied the pockets, and a relog brought them back: the server log had six
`0x108A MSG_USE_ITEM_NG uid=1011
REFUSED: that item is EQUIPPED`. A pocket IS a worn slot (equip slot type 11
-> worn index 11/12, feworld.WORN_SCAN), and the 0x2051 branch refused every
worn uid. Run from the repo root:  python tools/fe_pocket_test.py
"""
import os
import struct
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import fenet     # noqa: E402
import feworld   # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
feworld.load_extensions()
FAILS = []


def check(what, cond, detail=""):
    print("  %-66s %s" % (what, "PASS" if cond else "FAIL %s" % (detail,)))
    if not cond:
        FAILS.append(what)


STORE = {}
SENT = []


def _reset():
    STORE.clear()
    STORE["items"] = [[1011, 7, 1, 1], [1009, 19, 1, 2], [1002, 1666, 1, 1],
                      [1001, 531, 1, 1]]
    STORE["equip"] = [[11, 1011], [11, 1009], [4, 1002], [10, 1001]]
    del SENT[:]


feworld._load_char_field = lambda a, k, d=None: STORE.get(k, d)


def _store(a, k, v):
    STORE[k] = v
    return True


feworld._store_char_field = _store
feworld._self_char = lambda a: {"name": "Lex", "charid": 1, "force": 1}
feworld.send_world_frame = lambda conn, mid, body, prefix: SENT.append(
    (struct.unpack_from(">IH", body, 0)[1], body[6:]))
fenet.bf_encrypt = lambda st, body, mode, be: body
fenet.traffic_wrap = lambda data, seq=1: data


class _Conn:
    def settimeout(self, t):
        pass

    def getpeername(self):
        return ("127.0.0.1", 1)


def _args():
    a = dict(unit_id="auto", seq_mode="count", world_prefix=4, monster_base=400,
             presence="off", ui_auto="ok", item_heal=0, monster_attack="off",
             bag_organize="on", gmcmd=None, gmcmd_file=None, chat_relay="off",
             move_authority="off", unit_speed_repeat="off", npc=[], npc_walk=None,
             npc_base=1000, read_window=1.0, probe_on_auth=False,
             capture_only=False, idle_tick_ms=0)
    return types.SimpleNamespace(**a)


def use(uid, count=1):
    frames = [(0x30, struct.pack(">H", 0x2051) + struct.pack(">II", uid, count))]
    saved = (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap)
    fenet.recv_frame = lambda c: frames.pop(0) if frames else None
    fenet.bf_decrypt = lambda s, b, m, be: b
    fenet.traffic_unwrap = lambda p: (1, p)
    s = feworld._SESSION
    s["account"] = "member:1"
    s["charid"] = 1
    s["in_field"] = True
    s["field_ready"] = True
    err = None
    try:
        feworld._serve_loop(_Conn(), _args(), None, None, "ecb", False)
    except Exception as e:                              # noqa: BLE001
        import traceback
        traceback.print_exc()
        err = e
    finally:
        fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap = saved
    return err


def ids():
    return [m for m, _ in SENT]


print("pocket items: 0x2051 on a worn-slot-11 uid is a USE, not a refusal")
_reset()
err = use(1011)
check("a pocket item (slot type 11) is consumed: 0x1089 OK, no 0x108A NG",
      err is None and 0x1089 in ids() and 0x108A not in ids(), (err, ids()))
check("...it left the stored bag", 1011 not in [r[0] for r in STORE["items"]],
      STORE["items"])
check("...AND the stored worn map -- the relog would refill the pocket otherwise",
      [11, 1011] not in STORE["equip"] and [11, 1009] in STORE["equip"],
      STORE["equip"])
check("...the bag layout was pushed (0x107A)", 0x107A in ids(), ids())

_reset()
err = use(1009)
check("a STACK of two in a pocket: one consumed, worn entry kept",
      err is None and [r for r in STORE["items"] if r[0] == 1009] == [[1009, 19, 1, 1]]
      and [11, 1009] in STORE["equip"], (STORE["items"], STORE["equip"]))

_reset()
err = use(1002)
check("a worn non-pocket item (slot type 4, armour) is still REFUSED with 0x108A",
      err is None and 0x108A in ids() and 0x1089 not in ids()
      and [4, 1002] in STORE["equip"] and 1002 in [r[0] for r in STORE["items"]],
      (err, ids()))

_reset()
err = use(9999)
check("an unknown uid is refused", err is None and 0x108A in ids(), ids())

if FAILS:
    print("[fe_pocket_test] %d FAILED: %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("[fe_pocket_test] OK -- %d checks" % 7)
