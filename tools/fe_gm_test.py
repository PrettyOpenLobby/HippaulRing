#!/usr/bin/env python3
"""fe_gm_test.py -- services/fegm.py, every reply decoded with the client's
own primitive sequence, every request built the way its builder writes it.

Run from the repo root:  python tools/fe_gm_test.py

The harness pieces (`_args`, `_stub_store`, `_dispatch`) are IMPORTED from
fe_ui_test.py rather than copied: `_serve_loop` reads ~120 args attributes and
a copied dict drifts the day someone adds one. The Reader is local because it
needs f32 and s16 as well.

Arms and the sequences pinned here (all measured in the dump, see fegm.py):
    0x1143  u32, cstr                       0x05055e3c
    0x1129  cstr, u8, cstr, u8              0x05058371 (u8 = 0x5045e90)
    0xE00B  cstr, cstr                      0x05057121
    0xD016  u8, cstr, cstr                  0x050588c3
    0xB00C  u32, u8, u8                     0x05069b30
    0x20B1  header-only / 0x20B2 u32        0x05056936 / 0x0505690e
    0x20AF  u32 x5, cstr                    0x050564c4
    0x1140  header-only / 0x1141 u32        0x05055df0 / 0x05055e0b
    0x2024  u32 maskA, u32 maskB, fields    (feworld's own encoders)
    0x1166  u32, u32, f32 x3                goto_push
    0x1006  type-8 record                   npc_send
"""
import os
import struct
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
for p in (_SERVICES, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fenet      # noqa: E402
import feworld    # noqa: E402
import fegm       # noqa: E402
import fe_ui_test as H   # noqa: E402  -- _args, _stub_store, _dispatch, STORE

feworld.load_extensions(["fegm"])


class Reader:
    def __init__(self, b):
        self.b, self.i = b, 0

    def u8(self):
        v = self.b[self.i]
        self.i += 1
        return v

    def u16(self):
        v = struct.unpack_from(">H", self.b, self.i)[0]
        self.i += 2
        return v

    def s16(self):
        v = struct.unpack_from(">h", self.b, self.i)[0]
        self.i += 2
        return v

    def u32(self):
        v = struct.unpack_from(">I", self.b, self.i)[0]
        self.i += 4
        return v

    def f32(self):
        v = struct.unpack_from(">f", self.b, self.i)[0]
        self.i += 4
        return v

    def cstr(self):
        e = self.b.index(b"\0", self.i)
        v = self.b[self.i:e]
        self.i = e + 1
        return v.decode("cp932")

    def done(self):
        assert self.i == len(self.b), (
            "%d of %d bytes consumed -- the client would leave %r on the "
            "stream" % (self.i, len(self.b), self.b[self.i:]))


OUT = []
_FAILS = []


def _capture(conn, mid, body, prefix):
    unit, msg = struct.unpack_from(">IH", body, 0)
    OUT.append((msg, unit, body[6:]))


def _ok(what):
    print("  %-58s PASS" % what)


def _check(cond, what, detail=""):
    if cond:
        _ok(what)
    else:
        print("  %-58s FAIL %s" % (what, detail))
        _FAILS.append(what)


def _setup():
    H._stub_store()
    feworld._SESSION.pop("exp", None)
    feworld._SESSION.pop("player_hp", None)
    feworld._SESSION.pop("mobs", None)
    feworld._SESSION.pop("gm_spawn_n", None)
    feworld._SESSION["in_field"] = True
    feworld._SESSION["field"] = 39
    feworld._SESSION["cpos"] = (1.0, 2.0, 3.0)
    OUT[:] = []


def _ctx(a):
    return feworld.Ctx(None, None, "ecb", False, a)


def _ids():
    return [m for m, _, _ in OUT]


def _body(mid, n=0):
    hits = [b for m, _, b in OUT if m == mid]
    assert hits, "no 0x%04X sent; got %s" % (mid, [hex(i) for i in _ids()])
    return hits[n]


def main():
    saved = (fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame,
             feworld._self_char)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data
    feworld.send_world_frame = _capture
    feworld._self_char = lambda a: {"name": "Lex", "force": 1, "charid": 7}
    try:
        run()
    finally:
        (fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame,
         feworld._self_char) = saved


def run():
    a = H._args(unit_id="auto", gm_accounts="", sos="on", gm_slash="verbs",
                monster_base=400)
    me = feworld.unit_id_of(a)

    # --- registration -----------------------------------------------------
    _check(all(m in feworld.EXT_HANDLERS for m in
               (0x20AF, 0x208C, 0xE009, 0xA00D, 0xF100, 0xF300, 0xF303,
                0xF500, 0xF501)), "fegm claims its nine inbound ids")
    _check(0x1143 in feworld.KNOWN and 0x1129 in feworld.KNOWN,
           "KNOWN names registered")

    # --- !popup -> 0x1143 [u32][cstr] --------------------------------------
    _setup()
    _check(fegm.gm(_ctx(a), "!popup 10|Hello there"), "!popup taken")
    r = Reader(_body(0x1143))
    _check(r.u32() == 10 and r.cstr() == "Hello there", "0x1143 u32, cstr")
    r.done()
    _ok("0x1143 stream fully consumed")

    # --- !notice -> 0x1129 [cstr][u8][cstr][u8] ------------------------------
    _setup()
    fegm.gm(_ctx(a), "!notice Lex|Duke_Orc|4|2")
    r = Reader(_body(0x1129))
    got = (r.cstr(), r.u8(), r.cstr(), r.u8())
    r.done()
    _check(got == ("Lex", 2, "Duke_Orc", 0x40),
           "0x1129 cstr, u8 side, cstr, u8 kind<<4", repr(got))
    _setup()
    fegm.gm(_ctx(a), "!notice A|B")
    r = Reader(_body(0x1129))
    got = (r.cstr(), r.u8(), r.cstr(), r.u8())
    r.done()
    _check(got == ("A", 0xFF, "B", 0x10), "0x1129 defaults: side 0xff, kind 1")

    # --- !sysmsg -> 0xE00B [cstr][cstr] ----------------------------------
    _setup()
    fegm.gm(_ctx(a), "!sysmsg Server|Restart in 5 min")
    r = Reader(_body(0xE00B))
    got = (r.cstr(), r.cstr())
    r.done()
    _check(got == ("Server", "Restart in 5 min"), "0xE00B cstr title, cstr text")
    _setup()
    fegm.gm(_ctx(a), "!sysmsg clear")
    r = Reader(_body(0xE00B))
    got = (r.cstr(), r.cstr())
    r.done()
    _check(got[1] == "@clear@", "0xE00B '@clear@' closes the banner")

    # --- !dialog -> 0xD016 [u8][cstr][cstr] ------------------------------
    _setup()
    fegm.gm(_ctx(a), "!dialog 0|Title|Body text")
    r = Reader(_body(0xD016))
    got = (r.u8(), r.cstr(), r.cstr())
    r.done()
    _check(got == (0, "Title", "Body text"), "0xD016 u8 flag, cstr, cstr")

    # --- !npcflag -> 0xB00C [u32][u8 1][u8 flag] ---------------------------
    _setup()
    fegm.gm(_ctx(a), "!npcflag 500|1")
    r = Reader(_body(0xB00C))
    got = (r.u32(), r.u8(), r.u8())
    r.done()
    _check(got == (500, 1, 1), "0xB00C u32 unit, u8 kind==1, u8 flag")

    # --- 0x20AF SOS through _serve_loop ----------------------------------
    _setup()
    H._dispatch(a, struct.pack(">HI", 0x20AF, 39))
    _check(_ids() == [0x20B1] and _body(0x20B1) == b"",
           "0x20AF in field -> 0x20B1 header-only", [hex(i) for i in _ids()])
    _setup()
    feworld._SESSION["in_field"] = False
    H._dispatch(a, struct.pack(">HI", 0x20AF, 39))
    r = Reader(_body(0x20B2))
    _check(r.u32() == 0, "0x20AF outside -> 0x20B2 [u32 0]")
    r.done()
    _setup()
    H._dispatch(H._args(unit_id="auto", sos="off"), struct.pack(">HI", 0x20AF, 39))
    _check(_ids() == [], "--sos off answers nothing")
    # the broadcast body, as the receiving session's relay builds it
    _setup()
    fegm.on_relay_sos(_ctx(a), {"force": 1, "area": 39, "unit": 7,
                                "ally": 2, "foe": 3, "text": "Lex"})
    r = Reader(_body(0x20AF))
    got = (r.u32(), r.u32(), r.u32(), r.u32(), r.u32(), r.cstr())
    r.done()
    _check(got == (1, 39, 7, 2, 3, "Lex"), "0x20AF broadcast u32 x5, cstr")

    # --- 0x208C MSG_DEBUG_COMMAND ------------------------------------------
    def dbg(sub, *fields, fmt="", args=None, obj=None):
        body = struct.pack(">HB I", 0x208C, sub, me if obj is None else obj)
        if fmt:
            body += struct.pack(">" + fmt, *fields)
        H._dispatch(args or a, body)

    # decode alone, every sub
    d = fegm.decode_debug(bytes([0x0f]) + struct.pack(">IH", 5, 2)
                          + struct.pack(">HH", 100, 200))
    _check(d == (0x0f, "item", 5, (2, [100, 200])), "decode item [u32][u16 n] n x u16")
    d = fegm.decode_debug(bytes([0x11]) + struct.pack(">Ifff", 5, 1.0, 2.0, 3.0))
    _check(d[:3] == (0x11, "pos", 5) and tuple(d[3]) == (1.0, 2.0, 3.0),
           "decode pos [u32][f32 x3]")
    d = fegm.decode_debug(bytes([0x12]) + struct.pack(">IH", 5, 150))
    _check(d == (0x12, "hp", 5, (150,)), "decode hp [u32][u16]")
    d = fegm.decode_debug(bytes([0x07]) + struct.pack(">I", 3001))
    _check(d == (0x07, "break", 3001, ()), "decode break [u32]")

    # non-GM: NG 0, nothing stored
    _setup()
    dbg(0x15, 500, fmt="I")
    r = Reader(_body(0x1141))
    _check(_ids() == [0x1141] and r.u32() == 0 and "gold" not in H.STORE,
           "non-GM /chr gold -> 0x1141 [u32 0], NOT stored")
    r.done()

    g = H._args(unit_id="auto", gm_accounts="tester", sos="on",
                gm_slash="verbs", monster_base=400)
    # hp
    _setup()
    dbg(0x12, 150, fmt="H", args=g)
    r = Reader(_body(0x2024))
    got = (r.u32(), r.u32(), r.s16())
    r.done()
    _check(got == (0x4, 0, 150) and feworld._SESSION["player_hp"] == 150
           and _ids()[-1] == 0x1140,
           "GM /chr hp 150 -> 0x2024 [4][0][s16 150] then 0x1140", repr(got))
    # gold
    _setup()
    dbg(0x15, 4321, fmt="I", args=g)
    r = Reader(_body(0x2024))
    got = (r.u32(), r.u32(), r.u32())
    r.done()
    _check(got == (0x00200000, 0, 4321) and H.STORE.get("gold") == 4321
           and _ids()[-1] == 0x1140,
           "GM /chr gold 4321 -> stored + 0x2024 maskA GOLD, then 0x1140")
    # score
    _setup()
    dbg(0x16, 12000, fmt="I", args=g)
    r = Reader(_body(0x2024))
    got = (r.u32(), r.u32(), r.u32())
    r.done()
    _check(got == (0, 1, 12000) and H.STORE.get("total_score") == 12000,
           "GM /chr score -> stored + 0x2024 maskB bit 1")
    # crystal
    _setup()
    dbg(0x17, 77, fmt="I", args=g)
    r = Reader(_body(0x2035))
    got = (r.u32(), r.u32())
    r.done()
    _check(got == (2, 77) and H.STORE.get("crystal") == 77,
           "GM /chr crystal -> stored + 0x2035 [2][77]")
    # exp
    _setup()
    dbg(0x18, 823, fmt="I", args=g)
    _check(0x1075 in _ids() and H.STORE.get("exp") == 823
           and feworld._SESSION.get("exp") == 823 and _ids()[-1] == 0x1140,
           "GM /chr exp 823 -> combat_exp_push, stored total 823")
    # pos
    _setup()
    dbg(0x11, 10.0, 20.0, 30.0, fmt="fff", args=g)
    r = Reader(_body(0x1166))
    got = (r.u32(), r.u32(), r.f32(), r.f32(), r.f32())
    r.done()
    _check(got == (39, 0xFFFFFFFF, 10.0, 20.0, 30.0),
           "GM /chr pos -> 0x1166 [field][-1][f32 x3]")
    # power: decoded, no setter -> NG 2
    _setup()
    dbg(0x13, 999, fmt="H", args=g)
    r = Reader(_body(0x1141))
    _check(r.u32() == 2 and _ids() == [0x1141], "GM /chr power -> NG 2 (no setter)")
    r.done()
    # another unit's id -> NG 2
    _setup()
    dbg(0x15, 1, fmt="I", args=g, obj=99999)
    r = Reader(_body(0x1141))
    _check(r.u32() == 2 and "gold" not in H.STORE,
           "GM /chr gold on ANOTHER unit -> NG 2, not stored")
    r.done()
    # break: hand-off hook
    seen = []
    fegm.register_debug_sub(0x07, lambda ctx, obj, fields: (seen.append(obj), True)[1])
    _setup()
    dbg(0x07, args=g, obj=3001)
    _check(seen == [3001] and _ids() == [], "break building -> hook owns it")
    fegm.DEBUG_SUB_HOOKS.pop(0x07)
    _setup()
    dbg(0x07, args=g, obj=3001)
    r = Reader(_body(0x1141))
    _check(r.u32() == 2, "break building without a hook -> NG 2")
    r.done()
    # fewar's lane: when 0x208C was already claimed, sub 7 goes back to that
    # handler with the WHOLE inner, every other sub stays here
    prev_seen = []
    fegm._PREV_208C = lambda ctx, inner: prev_seen.append(inner)
    _setup()
    dbg(0x07, args=g, obj=3001)
    _check(prev_seen == [struct.pack(">HBI", 0x208C, 7, 3001)] and _ids() == [],
           "sub 7 handed to the previous 0x208C owner untouched")
    _setup()
    dbg(0x12, 99, fmt="H", args=g)
    _check(not prev_seen[1:] and feworld._SESSION["player_hp"] == 99,
           "other subs stay with fegm when chained")
    fegm._PREV_208C = None
    # the !gm re-injection lever
    _setup()
    _check(fegm.gm(_ctx(a), "!gm hp 42") and feworld._SESSION["player_hp"] == 42
           and _ids()[-1] == 0x1140, "!gm hp 42 applies without the account gate")
    _check(a.gm_accounts == "", "!gm restores --gm-accounts")

    # --- 0xE009 -> 0xE00B --------------------------------------------------
    _setup()
    H._dispatch(a, struct.pack(">HB", 0xE009, 0) + b"Lex\0Hello all\0")
    _check(_ids() == [], "non-GM 0xE009 -> nothing sent")
    _setup()
    H._dispatch(g, struct.pack(">HB", 0xE009, 0) + b"Lex\0Hello all\0")
    _check(_ids() == [], "GM 0xE009 queues a relay (no live sessions here)")
    _setup()
    fegm.on_relay_sysmsg(_ctx(a), {"title": "Lex", "text": "Hello all"})
    r = Reader(_body(0xE00B))
    got = (r.cstr(), r.cstr())
    r.done()
    _check(got == ("Lex", "Hello all"), "relayed 0xE00B cstr, cstr")

    # --- 0xA00D -> 0x1006 type 8 -------------------------------------------
    types = feworld.fegamedata.npc_types()
    pick = next((v for t, v in sorted(types.items())
                 if v["modeltype"] in feworld.MONSTER_MODELTYPE_IDS
                 and v["modeltype"] < 228), None)
    assert pick, "no monster type in fet_npc_type"
    body = (struct.pack(">HB", 0xA00D, 2) + pick["name"].encode("cp932") + b"\0"
            + struct.pack(">fff", 5.0, 6.0, 7.0) + struct.pack(">fff", 0, 0, 1.0))
    d = fegm.decode_npc_generate(body[2:])
    _check(d[0] == 2 and d[1] == pick["name"] and d[2] == (5.0, 6.0, 7.0),
           "decode 0xA00D [u8 num][cstr][f32 x3][f32 x3]")
    _setup()
    H._dispatch(a, body)
    _check(_ids() == [], "non-GM 0xA00D spawns nothing")
    _setup()
    H._dispatch(g, body)
    _check(_ids() == [0x1006, 0x1006], "GM 0xA00D num=2 -> two 0x1006 records",
           [hex(i) for i in _ids()])
    r = Reader(_body(0x1006, 0))
    cnt, obj, typ = r.u16(), r.u32(), r.u8()
    mt, kind, lvl = r.u16(), r.u16(), r.u8()
    m1 = r.u32()
    # 16c1633c: monsters carry a NAME -- mask1 bit 0 + the name-only identity
    # sub-record ([u32 submask 0x1][cstr name]) before mask2
    shown = None
    if m1 & 0x1:
        sub = r.u32()
        shown = r.cstr() if sub & 0x1 else None
    m2 = r.u32()
    pos = (r.f32(), r.f32(), r.f32())
    fac = (r.f32(), r.f32(), r.f32())
    m3 = r.u32()
    r.done()
    _check((cnt, typ, mt, kind, m1, m2, m3) == (1, 8, pick["modeltype"], 0, 1, 5, 0)
           and pos == (5.0, 6.0, 7.0) and obj == 600
           and shown == feworld.npc_display_name(pick["name"]),
           "type-8 record: model, kind 0, masks 1/5/0 with its NAME, position, "
           "id base+200", repr(shown))
    _check(600 in feworld._SESSION.get("mobs", {}) and
           601 in feworld._SESSION.get("mobs", {}),
           "both monsters registered for 0xA011")
    _setup()
    H._dispatch(g, struct.pack(">HB", 0xA00D, 1) + b"NoSuchMonster\0"
                + struct.pack(">ffffff", 0, 0, 0, 0, 0, 1))
    _check(_ids() == [], "unknown name spawns nothing")

    # --- 0xF303 unknown slash -> ! verbs ----------------------------------
    _setup()
    text = b"popup 3|Hi"
    H._dispatch(g, struct.pack(">HH", 0xF303, len(text)) + text)
    r = Reader(_body(0x1143))
    _check(r.u32() == 3 and r.cstr() == "Hi", "GM /popup via 0xF303 -> 0x1143")
    r.done()
    _setup()
    H._dispatch(a, struct.pack(">HH", 0xF303, len(text)) + text)
    _check(_ids() == [], "non-GM 0xF303 -> logged only")

    # --- F100/F300/F500/F501: decoded, nothing sent, no exception ----------
    _setup()
    H._dispatch(g, struct.pack(">HHI", 0xF100, 613, 1))
    H._dispatch(g, struct.pack(">HI", 0xF300, 7))
    H._dispatch(g, struct.pack(">HB", 0xF500, 2))
    H._dispatch(g, struct.pack(">HHHH", 0xF501, 1, 2, 1))
    _check(_ids() == [], "F100/F300/F500/F501 log only")

    if _FAILS:
        print("[fe_gm_test] FAILED: %s" % _FAILS)
        sys.exit(1)
    print("[fe_gm_test] OK")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
