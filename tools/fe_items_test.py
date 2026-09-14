#!/usr/bin/env python3
"""fe_items_test.py -- services/feitems.py end to end, no client.

Run from the repo root:  python tools/fe_items_test.py

Every reply feitems builds is decoded here with the EXACT primitive sequence
febody.py / the screen arms reported for it (see feitems.py's docstring for
the VA of each), then the Reader asserts the stream is fully consumed -- a
body one field long or short does not error on the client, it desynchronises
everything after it.

    0x1122 MSG_ENCHANT_REPLY            arm 0x050f53f2   u32, u32
    0x1120 MSG_ENCHANT_OK               arm 0x050f5524   NOTHING
    0x1121 MSG_ENCHANT_NG               arm 0x050f54b9   u32
    0x112C MSG_REPAIR_ITEM_OK           arm 0x050f116b   NOTHING
    0x112D MSG_REPAIR_ITEM_NG           arm 0x050f109e   u32 (struct+8)
    0x115B MSG_GET_REPAIR_ITEM_COST_OK  CHOSEN           u32 uid, u32 cost
    0x1023 MSG_ITEM_GATHER_OK           arm 0x0503c2d0   u32
    0x1024 MSG_ITEM_GATHER_NG           arm 0x0503c320   u32
    0x1086 MSG_GET_SELL_ITEM_VALUE_OK   reader 0x050f6470  u32, u32
    0x1171 PLAYER ITEM SLOTS            ctor 0x051263e0  u32 n, n x { u32 uid,
                                                          if uid: u16, u8, u32 }
    0x107A MSG_ITEM_ADD                 worker 0x0503d540  u8 u8 u32 [u32 uid +
                                                          item_fields]  (feworld's)
"""
import os
import struct
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import fenet      # noqa: E402
import feworld    # noqa: E402
import feitems    # noqa: E402
from fe_ui_test import Reader, STORE, _args, _stub_store   # noqa: E402

feworld.load_extensions(["feitems"])   # registers ours only

OUT = []


def _capture(conn, mid, body, prefix):
    unit, msg = struct.unpack_from(">IH", body, 0)
    OUT.append((msg, unit, body[6:]))


def _dispatch(a, inner):
    """ONE inner message through the real _serve_loop, crypto stubbed."""
    class _Conn:
        def recv(self, n):
            raise OSError("done")

        def settimeout(self, t):
            pass

        def getpeername(self):
            return ("127.0.0.1", 1)
    saved = (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap,
             fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame)
    frames = [(0x30, inner)]
    fenet.recv_frame = lambda c: frames.pop(0) if frames else None
    fenet.bf_decrypt = lambda s, b, m, be: b
    fenet.traffic_unwrap = lambda p: (1, p)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data
    feworld.send_world_frame = _capture
    try:
        feworld._serve_loop(_Conn(), a, None, None, "ecb", False)
    finally:
        (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap,
         fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame) = saved


def _gm(a, line):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "gm.txt")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(line + "\n")
    a.gmcmd_file = p
    saved = (fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data
    feworld.send_world_frame = _capture
    try:
        feworld.gm_pump(None, None, "ecb", False, a)
    finally:
        fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame = saved


def _fresh(**kw):
    """A stubbed store with a 3-item bag (uid 1001 x1, 1002 x3, 1003 worn)."""
    _stub_store()
    OUT[:] = []
    STORE["items"] = [[1001, 531, 0, 1], [1002, 532, 0, 3], [1003, 1666, 0, 1]]
    STORE["equip"] = [[4, 1003]]
    STORE["class_levels"] = {"2": 5}
    STORE["skills"] = [285]
    feworld._SESSION["in_field"] = True
    feworld._SESSION.pop("enchant", None)
    feworld._SESSION.pop("exp", None)
    feworld.self_class_id = lambda args: 2
    d = dict(equip_reflect="on", sell_price=50, shop_price=100)
    d.update(kw)
    return _args(**d)


def q(mid, payload=b""):
    return struct.pack(">H", mid) + payload


def ids():
    return [m for m, _, _ in OUT]


def _read_107a_add(body):
    """0x107A add form, exactly as 0x0503d540 + 0x0503d280 + the four masked
    groups (feworld.item_fields) read it."""
    r = Reader(body)
    add, ann, slot, uid = r.u8(), r.u8(), r.u32(), r.u32()
    assert add == 1, add
    m1 = r.u32()
    assert m1 == 2, hex(m1)
    no = r.u16()
    assert r.u32() == 0 and r.u32() == 0
    m4 = r.u32()
    assert m4 == (feworld.IT_COUNT | feworld.IT_DURMAX | feworld.IT_DURCUR
                  | feworld.IT_KIND | feworld.IT_EQUIP | feworld.IT_SLOT), hex(m4)
    count = r.u16()
    dmax = struct.unpack(">h", struct.pack(">H", r.u16()))[0]
    dcur = struct.unpack(">h", struct.pack(">H", r.u16()))[0]
    kind = r.u16()
    marker = r.u8()
    slot2 = r.u32()
    r.done()
    assert slot == slot2, (slot, slot2)
    return dict(slot=slot, uid=uid, no=no, count=count, dmax=dmax, dcur=dcur,
                kind=kind, marker=marker)


def check(label, cond):
    print("  %-58s %s" % (label, "PASS" if cond else "FAIL"))
    if not cond:
        raise AssertionError(label)


def main():
    # ---- ENCHANT ------------------------------------------------------------
    a = _fresh(enchant_cost=0)
    _dispatch(a, q(0x2077, struct.pack(">II", 1001, 1002)))
    check("0x2077 -> exactly one 0x1122", ids() == [0x1122])
    r = Reader(OUT[0][2])
    rid, cost = r.u32(), r.u32()
    r.done()
    check("0x1122 [u32 RID][u32 COST] with COST=0", cost == 0 and rid >= 1)
    check("pending enchant recorded in the session",
          feworld._SESSION.get("enchant", {}).get("rid") == rid)

    OUT[:] = []
    _dispatch(a, q(0x2078, struct.pack(">I", rid)))
    # material 1002 x3 -> x2 (a count change is one 0x107A re-add at its slot),
    # then the OK, then the target re-push
    check("0x2078 -> 0x107A (material count), 0x1120, 0x107A (target)",
          ids() == [0x107A, 0x1120, 0x107A])
    mat = _read_107a_add(OUT[0][2])
    check("material 1002 re-added at slot 1 with count 2",
          mat["uid"] == 1002 and mat["slot"] == 1 and mat["count"] == 2)
    check("0x1120 is header-only", OUT[1][2] == b"")
    tgt = _read_107a_add(OUT[2][2])
    check("target 1001 re-pushed at slot 0, not worn",
          tgt["uid"] == 1001 and tgt["slot"] == 0 and tgt["marker"] == 0xFF)
    check("store: material decremented, enchant recorded",
          STORE["items"][1][3] == 2 and STORE["enchants"] == {"1001": [532]})
    check("pending cleared after the OK", "enchant" not in feworld._SESSION)

    # a second OK for the same RID is refused
    OUT[:] = []
    _dispatch(a, q(0x2078, struct.pack(">I", rid)))
    r = Reader(OUT[0][2])
    check("stale RID -> 0x1121 code 0", ids() == [0x1121] and r.u32() == 0)
    r.done()

    # cancel
    OUT[:] = []
    _dispatch(a, q(0x2077, struct.pack(">II", 1001, 1002)))
    rid2 = Reader(OUT[0][2]).u32()
    OUT[:] = []
    _dispatch(a, q(0x2079, struct.pack(">I", rid2)))
    r = Reader(OUT[0][2])
    check("0x2079 -> 0x1121 code 7 (Enchant cancelled)",
          ids() == [0x1121] and r.u32() == 7)
    r.done()
    check("bag untouched by the cancel", STORE["items"][1][3] == 2)

    # worn target
    OUT[:] = []
    _dispatch(a, q(0x2077, struct.pack(">II", 1003, 1002)))
    r = Reader(OUT[0][2])
    check("worn target -> 0x1121 code 13", ids() == [0x1121] and r.u32() == 13)
    r.done()

    # material consumed to zero: uid 1001 x1 as the material -> row removed
    OUT[:] = []
    _dispatch(a, q(0x2077, struct.pack(">II", 1002, 1001)))
    rid3 = Reader(OUT[0][2]).u32()
    OUT[:] = []
    _dispatch(a, q(0x2078, struct.pack(">I", rid3)))
    check("single material -> remove + shifts, then OK, then target",
          ids()[0] == 0x107A and 0x1120 in ids() and ids()[-1] == 0x107A)
    r = Reader(OUT[0][2])
    check("first 0x107A is the REMOVE at slot 0 (add=0, [u8][u8][u32])",
          r.u8() == 0 and r.u8() == 0 and r.u32() == 0)
    r.done()
    check("store: uid 1001 gone", [x[0] for x in STORE["items"]] == [1002, 1003])

    # gold gate
    a = _fresh(enchant_cost=50, gold=30)
    _dispatch(a, q(0x2077, struct.pack(">II", 1001, 1002)))
    r = Reader(OUT[0][2])
    check("cost 50 > gold 30 -> 0x1121 code 8", ids() == [0x1121] and r.u32() == 8)
    r.done()
    a = _fresh(enchant_cost=50, gold=80)
    _dispatch(a, q(0x2077, struct.pack(">II", 1001, 1002)))
    rid4 = Reader(OUT[0][2]).u32()
    OUT[:] = []
    _dispatch(a, q(0x2078, struct.pack(">I", rid4)))
    check("paid enchant: wallet 0x2024 pushed and gold 80 -> 30",
          0x2024 in ids() and STORE["gold"] == 30)

    # --enchant off answers nothing
    a = _fresh(enchant="off")
    _dispatch(a, q(0x2077, struct.pack(">II", 1001, 1002)))
    check("--enchant off: nothing sent", OUT == [])

    # ---- REPAIR -------------------------------------------------------------
    a = _fresh()
    _dispatch(a, q(0x209E, struct.pack(">I", 1001)))
    r = Reader(OUT[0][2])
    check("0x209E default -> 0x112D code 6 (durability full)",
          ids() == [0x112D] and r.u32() == 6)
    r.done()
    a = _fresh(repair_cost="ok:25")
    _dispatch(a, q(0x209E, struct.pack(">I", 1001)))
    r = Reader(OUT[0][2])
    check("0x209E ok:25 -> 0x115B [uid][25] (chosen shape)",
          ids() == [0x115B] and r.u32() == 1001 and r.u32() == 25)
    r.done()

    a = _fresh(item_durability="-1:100")
    _dispatch(a, q(0x2086, struct.pack(">Ii", 1002, -1)))
    check("0x2086 -> 0x112C then one 0x107A", ids() == [0x112C, 0x107A])
    check("0x112C is header-only", OUT[0][2] == b"")
    rep = _read_107a_add(OUT[1][2])
    check("re-push carries +0x4aa/+0x4ac = -1/100 at the item's slot",
          rep["uid"] == 1002 and rep["slot"] == 1 and rep["count"] == 3
          and (rep["dmax"], rep["dcur"]) == (-1, 100))
    OUT[:] = []
    _dispatch(a, q(0x2086, struct.pack(">Ii", 1003, -1)))
    rep = _read_107a_add(OUT[1][2])
    check("repairing the WORN item keeps its worn marker (3 for slot type 4)",
          rep["uid"] == 1003 and rep["marker"] == 3)
    OUT[:] = []
    _dispatch(a, q(0x2086, struct.pack(">Ii", 4242, -1)))
    r = Reader(OUT[0][2])
    check("unknown uid -> 0x112D code 2", ids() == [0x112D] and r.u32() == 2)
    r.done()
    a = _fresh(repair="ng")
    _dispatch(a, q(0x2086, struct.pack(">Ii", 1001, -1)))
    r = Reader(OUT[0][2])
    check("--repair ng -> 0x112D code 5", ids() == [0x112D] and r.u32() == 5)
    r.done()

    # ---- GATHER -------------------------------------------------------------
    a = _fresh()
    _dispatch(a, q(0x201F, struct.pack(">I", 3005)))
    r = Reader(OUT[0][2])
    # 2026-09-12: 0x1024's code is a u16 (the arm's reader 0x5045ec0 advances
    # 2) -- this check used to read a u32 and so pinned the width bug that
    # made every gather NG arrive as code 0 with no dialog
    check("0x201F default -> 0x1024 [u16 code 2]",
          ids() == [0x1024] and r.u16() == 2)
    r.done()
    a = _fresh(gather="ok:540")
    _dispatch(a, q(0x201F, struct.pack(">I", 3005)))
    check("gather ok:540 -> 0x107A add, 0x1023, 0x1004",
          ids() == [0x107A, 0x1023, 0x1004])
    got = _read_107a_add(OUT[0][2])
    check("new item 540 lands in slot 3 with a fresh uid",
          got["no"] == 540 and got["slot"] == 3 and got["uid"] == 7001)
    r = Reader(OUT[1][2])
    check("0x1023 [u32 objectId] echoes the object", r.u32() == 3005)
    r.done()
    check("0x1004 MSG_DEL header names the object, no body",
          OUT[2][1] == 3005 and OUT[2][2] == b"")
    check("store grew to 4 rows", len(STORE["items"]) == 4)
    a = _fresh(gather="off")
    _dispatch(a, q(0x201F, struct.pack(">I", 3005)))
    check("--gather off: nothing sent", OUT == [])

    # ---- SELL VALUE ---------------------------------------------------------
    a = _fresh()
    _dispatch(a, q(0x2050, struct.pack(">II", 9, 1002)))
    r = Reader(OUT[0][2])
    # 09-11: 50% of the item table's OWN price (FE_ITEM_DATA +0x90), not of a
    # flat --shop-price -- uid 1002 is item 532
    want = feworld.fegamedata.items()[532]["price"] * 50 // 100
    check("0x2050 -> one 0x1086 [uid][value] at --sell-price 50%% of %d"
          % feworld.fegamedata.items()[532]["price"],
          ids() == [0x1086] and r.u32() == 1002 and r.u32() == want and want > 0)
    r.done()
    a = _fresh(sell_price=0)
    _dispatch(a, q(0x2050, struct.pack(">II", 9, 1002)))
    check("--sell-price 0: 0x2050 not answered", OUT == [])

    # ---- CLASS LEVEL-UP: moved to feprog.py / tools/fe_prog_test.py (2026-09-11)

    # ---- !verbs -------------------------------------------------------------
    a = _fresh()
    _gm(a, "!slots")
    check("!slots -> one 0x1171", ids() == [0x1171])
    r = Reader(OUT[0][2])
    n = r.u32()
    rows = []
    for i in range(n):
        uid = r.u32()
        if uid:
            rows.append((uid, r.u16(), r.u8(), r.u32()))
    r.done()
    check("0x1171 lists the 3 rows with the worn marker on uid 1003",
          rows == [(1001, 531, 0xFF, 0), (1002, 532, 0xFF, 1), (1003, 1666, 3, 2)])

    a = _fresh()
    _gm(a, "!exchange")
    check("!exchange -> header-only 0x1150", ids() == [0x1150] and OUT[0][2] == b"")
    OUT[:] = []
    _gm(a, "!exchange ng 11")
    r = Reader(OUT[0][2])
    check("!exchange ng 11 -> 0x1151 [u32 11]", ids() == [0x1151] and r.u32() == 11)
    r.done()

    # ---- decode-and-log ids answer NOTHING -----------------------------------
    a = _fresh()
    # 0x2028 (Pw regen) and 0x1031/0x1032 (skill use) moved to feprog.py
    for mid, body in ((0x2081, struct.pack(">I", 77)),
                      (0x2097, b""), (0x2014, b""),
                      (0x2076, b"Lex".ljust(32, b"\0")),
                      (0xA00E, struct.pack(">BIH", 1, 7, 3))):
        _dispatch(a, q(mid, body))
    check("10 log-only ids: nothing sent back", OUT == [])
    check("feitems no longer claims the ids feprog answers",
          not any(getattr(feworld.EXT_HANDLERS.get(m), "__module__", "")
                  == "feitems" for m in (0x2059, 0x2028, 0x1031, 0x1032)))

    # ---- registration discipline ------------------------------------------------
    check("no feitems id shadows a feworld table row",
          not any(m in feworld.UI_HEADER_ONLY_OK or m in feworld.UI_REPLY_BODY
                  or m in feworld._BUILTIN_IDS for m in feworld.EXT_HANDLERS
                  if feworld.EXT_HANDLERS[m].__module__ == "feitems"))
    check("KNOWN names registered for the client-sent ids",
          all(m in feworld.KNOWN for m in feitems.NAMES))
    print("[fe_items_test] OK")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    quiet = "-v" not in sys.argv
    if quiet:
        _real = print

        class _Null:
            def write(self, *a):
                pass

            def flush(self):
                pass
        _saved = sys.stdout
        sys.stdout = _Null()
        # the check lines go to the real stdout
        def check_print(*a, **k):        # noqa: E306
            _real(*a, file=_saved, **k)
        globals()["print"] = check_print
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
