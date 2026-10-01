#!/usr/bin/env python3
"""fe_unit_test.py -- feunit.py (unit state / stat setters / metamorphosis /
ghost / invincible / durability / building hp / crystal heal), no client.

Run from the repo root:  python tools/fe_unit_test.py

Every body feunit builds is decoded here with the EXACT primitive sequence the
client's arm reads (febody.py + the opened setters, 2026-09-10), then the
Reader asserts the stream is fully consumed -- a body one field long or short
does not error on the client, it desynchronises the stream.

    0x2026/0x2027   u32 mask, then i16 per set bit (0x5045ec0 in each setter)
    0x1183/0x1184   u16 N, N x { u32 obj, u32 mask, i16.. }
    0x1005          u16 N, N x u32
    0x203D          u32 mask, bit 0x10 -> u16
    0x106A          u32           0x106B  u32, u32
    0x1070          u16 N, N x { u16, u32, u32 }
    0x2024          u32, u32, u32 (maskA 0x100000 -> CONDITION)
    0x1030 / 0x1165 / 0x20B0 / 0x2045   header-only
    0x2032          u32 uid, u8, u32 mask, i16 per bit
    0x209D          u32 maskA, u32 maskB, u32 per A bit
    0x2035          u32 mask, u32 (crystal, feworld's own)
"""
import os
import struct
import sys
import tempfile
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import fenet     # noqa: E402
import feworld   # noqa: E402
import feunit    # noqa: E402

feworld.load_extensions(["feunit"])

OUT = sys.__stdout__
FAILS = []


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    say("  %-62s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(label)


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

    def i16(self):
        v = struct.unpack_from(">h", self.b, self.i)[0]
        self.i += 2
        return v

    def u32(self):
        v = struct.unpack_from(">I", self.b, self.i)[0]
        self.i += 4
        return v

    def cstr(self):
        e = self.b.index(b"\0", self.i)
        v = self.b[self.i:e]
        self.i = e + 1
        return v.decode("cp932")

    def done(self):
        assert self.i == len(self.b), (
            "%d of %d bytes consumed -- %r would be left on the stream"
            % (self.i, len(self.b), self.b[self.i:]))


STORE = {}


def _stub_store():
    STORE.clear()
    feworld._store_char_field = lambda a, k, v: (STORE.__setitem__(k, v), True)[1]
    feworld._load_char_field = lambda a, k, d=None: STORE.get(k, d)
    feworld._SESSION["account"] = "tester"
    feworld._SESSION["charid"] = 7


def _args(**kw):
    a = dict(unit_id="7", seq_mode="count", world_prefix=4, read_window=1.0,
             gmcmd=None, gmcmd_file=None, probe_on_auth=False,
             ui_auto="ok", ui_auto_err=0, move_request="off",
             chat_relay="off", chat_echo="off", chat_line=None,
             validate_finish=0, war_start="off", war_clock="off",
             war_deadline_ms=0, player_hp=200, crystal=None,
             building_hp=(100, 100))
    a.update(kw)
    return types.SimpleNamespace(**a)


class _Conn:
    def settimeout(self, t):
        pass

    def getpeername(self):
        return ("127.0.0.1", 1)


def _drive(a, frames, outbox):
    """Run inner frames through the REAL _serve_loop with fenet's crypto
    stubbed and send_world_frame captured as (mid, unit, payload)."""
    saved = (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap,
             fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame)
    inbox = [(0x30, f) for f in frames]
    fenet.recv_frame = lambda conn: inbox.pop(0) if inbox else None
    fenet.bf_decrypt = lambda st, body, mode, be: body
    fenet.traffic_unwrap = lambda plain: (1, plain)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data

    def _capture(conn, mid, body, prefix):
        unit, msg = struct.unpack_from(">IH", body, 0)
        outbox.append((msg, unit, body[6:]))

    feworld.send_world_frame = _capture
    try:
        feworld._serve_loop(_Conn(), a, None, None, "ecb", False)
    finally:
        (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap,
         fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame) = saved


def _dispatch(a, inner):
    out = []
    _drive(a, [inner], out)
    return out


def _gm(a, line):
    """One `!verb` line through gm_pump, captured the same way."""
    out = []
    saved = (fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data

    def _capture(conn, mid, body, prefix):
        unit, msg = struct.unpack_from(">IH", body, 0)
        out.append((msg, unit, body[6:]))

    feworld.send_world_frame = _capture
    fd, path = tempfile.mkstemp(suffix=".gm")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(line + "\n")
        a.gmcmd_file = path
        feworld.gm_pump(_Conn(), None, "ecb", False, a)
    finally:
        fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame = saved
        os.unlink(path)
        a.gmcmd_file = None
    return out


def _read_stat(payload):
    r = Reader(payload)
    mask = r.u32()
    got = {}
    for bit in sorted(feunit.UNIT_STAT):
        if mask & bit:
            got[bit] = r.i16()
    r.done()
    return mask, got


# ---------------------------------------------------------------------------
def part_table():
    say("setter table")
    check("nineteen setters, bits 0x1..0x40000, each opened",
          len(feunit.UNIT_STAT) == 19 and sorted(feunit.UNIT_STAT)[-1] == 0x40000)
    offs = [feunit.UNIT_STAT[b][0] for b in sorted(feunit.UNIT_STAT)]
    check("column-0 offsets are +0x514..+0x554 stride 4, then +0x558, +0x55a",
          offs == list(range(0x514, 0x558, 4)) + [0x558, 0x55A], repr(offs))
    vas = [feunit.UNIT_STAT[b][3] for b in sorted(feunit.UNIT_STAT)]
    # 0x50 apart up to the lone-word setter 0x4ff5d60, which is shorter (no
    # column index), so the last one sits at 0x4ff5da0, not 0x4ff5db0.
    check("setter VAs are the applier's call targets, in bit order",
          vas == [0x4FF5810 + 0x50 * i for i in range(18)] + [0x4FF5DA0], repr(vas))
    check("the refresh mask is bits 5..10 (0x7e0, applier 0x04ff51c7)",
          feunit.UNIT_STAT_REFRESH_MASK == 0x7E0)
    check("+0x514/+0x518 are the two 攻撃力 addends, +0x540.. the 耐性 six",
          feunit.UNIT_STAT[0x1][1] == "ATK_ADD_A"
          and feunit.UNIT_STAT[0x2][1] == "ATK_ADD_B"
          and all(feunit.UNIT_STAT[1 << i][1].startswith("RES_ELEM")
                  for i in range(11, 17)))


def part_stat():
    _stub_store()
    say("stat setters")
    a = _args()
    out = _gm(a, "!stat ATK_ADD_A 123")
    check("!stat sends ONE 0x2026 on the header unit", len(out) == 1
          and out[0][0] == 0x2026 and out[0][1] == 7, repr(out))
    mask, got = _read_stat(out[0][2])
    check("body = [u32 mask 0x1][i16 123], fully consumed",
          mask == 0x1 and got == {0x1: 123})
    out = _gm(a, "!stat +0x540 -5 0 1")
    check("column 1 goes out as 0x2027",
          out and out[0][0] == 0x2027 and _read_stat(out[0][2]) == (0x800, {0x800: -5}))
    out = _gm(a, "!stat 0x542 9")
    check("a column-1 offset resolves to the same bit",
          out and _read_stat(out[0][2])[0] == 0x800)
    out = _gm(a, "!stat RES_ELEM5 40 3001")
    check("with a UNIT the batch form 0x1183 goes out, header = that unit",
          out and out[0][0] == 0x1183 and out[0][1] == 3001, repr(out))
    r = Reader(out[0][2])
    n = r.u16()
    obj, mask = r.u32(), r.u32()
    v = r.i16()
    r.done()
    check("batch body = [u16 1][u32 3001][u32 0x10000][i16 40]",
          (n, obj, mask, v) == (1, 3001, 0x10000, 40))
    out = _gm(a, "!stat U558 77 0 1")
    check("bit 0x20000 (+0x558) has no column 1: sent on 0x2026",
          out and out[0][0] == 0x2026 and _read_stat(out[0][2]) == (0x20000, {0x20000: 77}))
    out = _gm(a, "!statprobe 1001")
    mask, got = _read_stat(out[0][2])
    check("!statprobe carries all nineteen bits, ascending sentinels",
          mask == 0x7FFFF and [got[b] for b in sorted(got)] == list(range(1001, 1020)))
    body = feunit.stat_body({0x4: 1, 0x1: 2, 0x800: 3})
    check("stat_body orders fields by bit regardless of dict order",
          body == struct.pack(">Ihhh", 0x805, 2, 1, 3))
    out = _gm(a, "!stat NOSUCH 1")
    check("an unknown field is refused, nothing sent", out == [])
    out = _gm(a, "!stat ATK_ADD_A 99999")
    check("an out-of-range i16 is refused, nothing sent", out == [])


def part_profile():
    _stub_store()
    say("profile / metamorphosis")
    a = _args()
    out = _gm(a, "!morph dragon")
    check("!morph sends 0x203D on the header unit", out and out[0][0] == 0x203D
          and out[0][1] == 7, repr(out))
    r = Reader(out[0][2])
    mask, form = r.u32(), r.u16()
    r.done()
    check("body = [u32 0x10][u16 0x2] (DRA = bit 1 = fe-fet id 1)",
          (mask, form) == (0x10, 0x2))
    # WARNING: 2026-09-11: 'off' was 0, which 0x506c7d0 ignores (early return on
    # (word & 0x3f) == 0) while the arm still stores it -- clearing HUM
    check("form words: GIANT 0x1 .. FLYNGSHIP 0x20, off = HUM 0x200, ids 0..5",
          [feunit.form_word(s) for s in ("GIANT", "1", "chim", "KNIGHT", "4", "ship", "off")]
          == [0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x200])
    body = feunit.profile_body({0x1: "Lex", 0x4: 2, 0x8000: "hi"})
    r = Reader(body)
    m = r.u32()
    nm, look1, cm = r.cstr(), r.u8(), r.cstr()
    r.done()
    check("profile_body packs name(cstr) look1(u8) comment(cstr) in bit order",
          (m, nm, look1, cm) == (0x8005, "Lex", 2, "hi"))
    # 0x2044 under the default (rules) outside a field: NG 6, the client waits
    feworld._SESSION.pop("morph", None)
    req = struct.pack(">HHBI", 0x2044, 954, 1, 3005)
    out = _dispatch(_args(), req)
    check("0x2044 with the default knob (rules), no field: 0x106B",
          out and out[0][0] == 0x106B, repr(out))
    r = Reader(out[0][2])
    bld, code = r.u32(), r.u32()
    r.done()
    check("0x106B body = [u32 building 3005][u32 code 6]", (bld, code) == (3005, 6))
    out = _dispatch(_args(metamorphosis="ng"), req)
    check("--metamorphosis ng (the escape hatch) still refuses with 6",
          out and out[0][2] == struct.pack(">II", 3005, 6))
    out = _dispatch(_args(metamorphosis="off"), req)
    check("--metamorphosis off sends nothing", out == [])
    # --metamorphosis ok (testing: no rules) -- in a field, or the pump ends it;
    # the cost table (0x1070) goes first, once per field entry
    feworld._SESSION["in_field"] = True
    feworld._SESSION["field"] = 17
    feworld._SESSION["room"] = -1
    feworld._SESSION["morphcost_sent"] = True
    feworld._SESSION.pop("player_hp", None)
    out = _dispatch(_args(metamorphosis="ok"), req)
    check("--metamorphosis ok: 0x106A, the 0x203D form, the summon's HP",
          [o[0] for o in out] == [0x106A, 0x203D, 0x2024],
          repr([hex(o[0]) for o in out]))
    r = Reader(out[0][2])
    check("0x106A body = [u32 building]", r.u32() == 3005)
    r.done()
    r = Reader(out[1][2])
    # WARNING: the u16 is the SUMMON SKILL (FE_BUILDING_DATA +0xde): 954 = Castle/
    # Keep = KNIGHT. The old reading ("0..5 = the form") answered NG 7 to it.
    check("skill 954 (the keep's +0xde) = KNIGHT 0x8", (r.u32(), r.u16()) == (0x10, 0x8))
    r.done()
    r = Reader(out[2][2])
    check("0x2024 = [maskA 0x6][0][i16 max 1800][i16 cur 1800] (a Lv1 KNIGHT, "
          "full HP carried over)",
          (r.u32(), r.u32(), r.i16(), r.i16()) == (0x6, 0, 1800, 1800))
    r.done()
    out = _dispatch(_args(metamorphosis="ok"), req)
    check("already summoned -> NG 5", out and out[0][2] == struct.pack(">II", 3005, 5))
    feworld._SESSION["player_hp"] = 900                  # the Knight at 50%
    out = _dispatch(_args(), struct.pack(">H", 0x2045))
    check("0x2045 -> 0x106C (REVERT TO HUMAN), form HUM 0x200, human HP",
          [o[0] for o in out] == [0x106C, 0x203D, 0x2024], repr(out))
    check("...0x106C is header-only on the self unit",
          out[0][2] == b"" and out[0][1] == 7)
    check("...0x203D form = 0x200 (NOT 0: 0 changed no model and cleared HUM)",
          out[1][2] == struct.pack(">IH", 0x10, 0x200))
    check("...0x2024 = human max 200, and the Knight's 50% carried back: 100",
          out[2][2] == struct.pack(">IIhh", 0x6, 0, 200, 100), repr(out[2][2]))
    out = _dispatch(_args(), struct.pack(">H", 0x2045))
    check("0x2045 with no summon on record sends nothing", out == [])
    out = _dispatch(_args(metamorphosis="ok", metamorphosis_form="giant"), req)
    r = Reader(out[1][2])
    check("--metamorphosis-form overrides (GIANT 0x1)", (r.u32(), r.u16()) == (0x10, 0x1))
    feworld._SESSION.pop("morph", None)
    out = _dispatch(_args(metamorphosis="ok"), struct.pack(">HHBI", 0x2044, 77, 0, 3005))
    check("a u16 that is no summon skill -> NG 7", out and out[0][0] == 0x106B
          and Reader(out[0][2]).u32() == 3005 and out[0][2][4:] == struct.pack(">I", 7))
    feworld._SESSION.pop("morph", None)
    feworld._SESSION["in_field"] = False
    feworld._SESSION.pop("player_hp", None)
    # cost notify
    a = _args(metamorphosis_cost="1:100:0,3:250:1")
    out = _gm(a, "!morphcost")
    check("!morphcost sends 0x1070", out and out[0][0] == 0x1070)
    r = Reader(out[0][2])
    n = r.u16()
    rows = [(r.u16(), r.u32(), r.u32()) for _ in range(n)]
    r.done()
    check("0x1070 body = [u16 2] x {u16 form, u32 gold, u32 crystal}",
          rows == [(1, 100, 0), (3, 250, 1)])
    out = _gm(_args(), "!morphcost")
    r = Reader(out[0][2])
    rows = [(r.u16(), r.u32(), r.u32()) for _ in range(r.u16())]
    r.done()
    check("no --metamorphosis-cost: the 2006 --summon-cost rows, crystal in "
          "the 2nd u32", rows == [(0, 0, 30), (1, 0, 0), (2, 0, 40), (3, 0, 40),
                                  (4, 0, 50)], repr(rows))
    out = _gm(_args(summon_cost="off"), "!morphcost")
    check("--summon-cost off and no --metamorphosis-cost: nothing", out == [])
    # the pump: once per field entry
    feworld._SESSION["in_field"] = True
    feworld._SESSION.pop("morphcost_sent", None)
    out = _dispatch(a, struct.pack(">H", 0x20AE))
    check("entering a field pumps the cost table once", [o[0] for o in out] == [0x1070])
    out = _dispatch(a, struct.pack(">H", 0x20AE))
    check("...and not again", out == [])
    feworld._SESSION["in_field"] = False


def part_condition():
    _stub_store()
    say("ghost / invincible")
    a = _args(unit_state=0x1E000000)
    feworld._SESSION.pop("player_dead", None)
    out = _gm(a, "!ghost on")
    check("!ghost on sends 0x2024 CONDITION then header-only 0x1030",
          [o[0] for o in out] == [0x2024, 0x1030] and out[1][2] == b"", repr(out))
    r = Reader(out[0][2])
    ma, mb, v = r.u32(), r.u32(), r.u32()
    r.done()
    check("CONDITION = base | GHOST, maskA 0x100000, maskB 0",
          (ma, mb, v) == (0x100000, 0, 0x1E000000 | feunit.COND_GHOST))
    feworld._SESSION["player_dead"] = True
    out = _gm(a, "!ghost on")
    check("DEAD (feworld's flag) is kept in the word",
          Reader(out[0][2]).b[8:] == struct.pack(">I", 0x1E000000 | 0x800000 | 0x1000000))
    feworld._SESSION["player_dead"] = False
    out = _gm(a, "!ghost off")
    check("!ghost off clears only the GHOST bit",
          out[0][2] == struct.pack(">III", 0x100000, 0, 0x1E000000))
    feworld._SESSION.pop("invincible", None)
    out = _gm(a, "!invincible on")
    check("!invincible on sends header-only 0x1165 once", [o[0] for o in out] == [0x1165]
          and out[0][2] == b"")
    out = _gm(a, "!invincible on")
    check("!invincible on again sends NOTHING (it is a toggle)", out == [])
    out = _gm(a, "!invincible off")
    check("!invincible off toggles it back", [o[0] for o in out] == [0x1165])


def part_misc():
    _stub_store()
    say("delete / durability / building hp / effect")
    a = _args()
    feworld._SESSION["mobs"] = {401: {"dead": False}, 402: {"dead": False}}
    out = _gm(a, "!del 401 402")
    check("!del sends 0x1005", out and out[0][0] == 0x1005)
    r = Reader(out[0][2])
    n = r.u16()
    ids = [r.u32() for _ in range(n)]
    r.done()
    check("0x1005 body = [u16 2][u32 401][u32 402]", ids == [401, 402])
    check("...and the session's mob table forgets them", feworld._SESSION["mobs"] == {})
    out = _gm(a, "!durability 7001 0 100")
    check("!durability sends 0x2032", out and out[0][0] == 0x2032)
    r = Reader(out[0][2])
    uid, ann, mask = r.u32(), r.u8(), r.u32()
    fields = {}
    for bit in (0x2, 0x4, 0x8):
        if mask & bit:
            fields[bit] = r.i16()
    r.done()
    check("0x2032 body = [u32 7001][u8 1][u32 0xC][i16 max 100][i16 cur 0]",
          (uid, ann, mask, fields) == (7001, 1, 0xC, {0x4: 100, 0x8: 0}))
    out = _gm(a, "!durability 7001 -1")
    check("cur only -> mask 0x8", out[0][2] == struct.pack(">IBIh", 7001, 1, 0x8, -1))
    out = _gm(a, "!bhp 3001 40 100")
    check("!bhp sends 0x209D with the BUILDING as header unit",
          out and out[0][0] == 0x209D and out[0][1] == 3001)
    r = Reader(out[0][2])
    ma, mb = r.u32(), r.u32()
    vals = [r.u32() for _ in range(bin(ma).count("1"))]
    r.done()
    check("0x209D body = [u32 0x6][u32 0][u32 max 100][u32 cur 40]",
          (ma, mb, vals) == (0x6, 0, [100, 40]))
    out = _gm(a, "!effect6e")
    check("!effect6e is header-only on the self unit",
          out and out[0][0] == 0x20B0 and out[0][1] == 7 and out[0][2] == b"")
    out = _gm(a, "!effect6e 3001")
    check("!effect6e UNIT puts that unit in the header", out[0][1] == 3001)
    out = _gm(a, "!unitstat")
    check("!unitstat is taken (help), nothing sent", out == [])


def part_crystal():
    _stub_store()
    say("recover from crystal")
    feworld._SESSION.pop("player_hp", None)
    req = struct.pack(">HI", 0x20AD, 3007)
    out = _dispatch(_args(crystal_heal=0), req)
    check("0x20AD with --crystal-heal 0 sends nothing", out == [])
    feworld._SESSION["player_hp"] = 50
    out = _dispatch(_args(crystal_heal=60, crystal=500, crystal_heal_cost=20), req)
    check("heal: crystal push then HP push", [o[0] for o in out] == [0x2035, 0x2024],
          repr([hex(o[0]) for o in out]))
    r = Reader(out[0][2])
    check("0x2035 = [u32 0x2][u32 480] (500 - 20)", (r.u32(), r.u32()) == (0x2, 480))
    r.done()
    check("the store now holds 480", STORE.get("crystal") == 480)
    r = Reader(out[1][2])
    check("0x2024 maskA 0x4 carries the NEW hp 110 (50 + 60)",
          (r.u32(), r.u32(), r.i16()) == (0x4, 0, 110))
    r.done()
    check("session hp bookkeeping updated", feworld._SESSION["player_hp"] == 110)
    feworld._SESSION["player_hp"] = 190
    out = _dispatch(_args(crystal_heal=60), req)
    check("heal is capped at player_hp_max (200), no cost = no crystal push",
          [o[0] for o in out] == [0x2024] and out[0][2][8:] == struct.pack(">h", 200))
    STORE["crystal"] = 5
    out = _dispatch(_args(crystal_heal=60, crystal=500, crystal_heal_cost=20), req)
    check("not enough crystal: refused, nothing sent", out == [])
    feworld._SESSION.pop("crystal_left", None)
    out = _dispatch(_args(crystal_heal=10, crystal_heal_drain=30), req)
    check("--crystal-heal-drain adds a 0x209D on the building",
          [o[0] for o in out] == [0x2024, 0x209D] and out[1][1] == 3007
          and out[1][2] == struct.pack(">III", 0x4, 0, 70))
    out = _dispatch(_args(), struct.pack(">H", 0x20AE))
    check("0x20AE is logged, nothing sent", out == [])


def _capture_out():
    """send_world_frame captured as (mid, unit, payload); returns (out,
    restore)."""
    out = []
    saved = (fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data

    def _capture(conn, mid, body, prefix):
        unit, msg = struct.unpack_from(">IH", body, 0)
        out.append((msg, unit, body[6:]))
    feworld.send_world_frame = _capture

    def restore():
        fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame = saved
    return out, restore


def part_status():
    """world/status.py (2026-10-01): statuses from the skills' own
    EFFECT_DATA rows, what the server enforces with them, and the wire bits
    behind --status-effects-wire."""
    def check(label, cond, detail=""):
        globals()["check"](label, cond, repr(detail) if detail != "" else "")

    import time
    from world import combat, damage, monsters, status
    say("status effects")
    # --- the data: which skill carries what
    kinds = lambda sk: sorted(x["kind"] for x in status.skill_specs(sk))
    check("Shield Bash L1 (35) STUNS (EFFECT_DATA 529, bitsE 0x10)",
          kinds(35) == ["stun"], kinds(35))
    check("Spider Web L1 (160) SLOWS by 10% (173, bitsB 0x6000, -0.1)",
          kinds(160) == ["slow"]
          and abs(status.skill_specs(160)[0]["factor"] - 0.9) < 1e-6,
          status.skill_specs(160))
    check("Ice Javelin L1 (330, the manual's Ice Bolt) SLOWS",
          "slow" in kinds(330), kinds(330))
    check("Freezing Wave L1 (335) ROOTS (bitsE 0x20, 'Rooted!!')",
          "root" in kinds(335), kinds(335))
    check("Arm Break L1 (195) DISARMS (bitsE 0x40)", "disarm" in kinds(195),
          kinds(195))
    vb = [x for x in status.skill_specs(190) if x["kind"] == "poison"]
    check("Viper Bite L1 (190) POISONS: 15 HP every 2 s for 10 s",
          vb and vb[0]["amount"] == 15 and vb[0]["tick_ms"] == 2000
          and vb[0]["ms"] == 10000, vb)
    check("Blaze Shot L1 (165) BURNS", "burn" in kinds(165), kinds(165))
    check("Void Darkness L1 (205) BLINDS and lowers resistance",
          kinds(205) == ["blind", "resist_down"], kinds(205))
    check("Ender Pain L1 (5): no flinch + resistance up (GOOD, on the caster)",
          [x["kind"] for x in status.good_specs(5)] == ["noflinch", "resist_up"],
          status.skill_specs(5))
    check("Hide L1 (140): hide (0x2000) and a 40% slow, from +0x2C's 120 s",
          kinds(140) == ["hide", "slow"]
          and status.skill_specs(140)[0]["ms"] == 120000, status.skill_specs(140))
    check("詠唱 L1 (275): chant 0x2000000 = the bit Fire Lance (305) requires",
          kinds(275) == ["chant"]
          and status.skill_required_bits(305) == status.COND_CHANT)
    check("Venomous's bite (monster skill 602) poisons",
          "poison" in kinds(602), kinds(602))
    check("the Antidote's 600 ms flag is not a status; it CURES poison (0x1)",
          kinds(1052) == [] and status.skill_cures(1052) == 0x1)
    check("the basic attack carries nothing", kinds(0) == [])

    # --- the store
    h = {}
    status.apply(h, status.bad_specs(160), now=100.0)
    check("a slow: speed factor 0.9", abs(status.speed_factor(h, 100.5) - 0.9) < 1e-6)
    status.apply(h, status.bad_specs(335), now=100.0)
    check("...rooted: 0", status.speed_factor(h, 100.5) == 0.0)
    check("a hit breaks ROOT (manual) but not the slow",
          status.break_on_hit(h) == ["root"] and status.has(h, "slow", 100.5))
    status.apply(h, status.bad_specs(35), now=100.0)
    status.break_on_hit(h)
    check("a hit does NOT break STUN (manual)", status.has(h, "stun", 100.5))
    check("...a stunned holder may not attack",
          status.refusal(h, 100.5) == "stunned")
    check("...and it runs out (3 s)", not status.has(h, "stun", 103.5)
          and status.refusal(h, 103.5) is None)
    h = {}
    status.apply(h, status.bad_specs(190), src=7, now=0.0)
    t = status.dot_ticks(h, 4.0)
    check("poison ticks: two by t=4 s, 15 each, credited to the applier",
          t == [("poison", 15, 7), ("poison", 15, 7)], t)
    t = status.dot_ticks(h, 30.0)
    check("...three more until it ends at 10 s, then nothing",
          len(t) == 3 and not status.has(h, "poison", 30.0), t)

    # --- a monster: a skill's statuses land, DoT kills pay the applier
    a = _args(combat="on", monster_attack="on", kill_reward="off", drops="off",
              kill_gold="off", status_effects="on")
    _KEYS = ("in_field", "charid", "field", "room", "cpos", "mobs", "status",
             "player_hp", "crouch_at", "crouch_pos", "pw_tick_at",
             "crouch_tick_seen", "status_bits_sent", "player_dead")
    for k in _KEYS:
        feworld._SESSION.pop(k, None)
    for k, v in dict(in_field=True, charid=7, field=12, room=-1,
                     cpos=(0.0, 0.0, 0.0)).items():
        feworld._SESSION[k] = v
    m = {"type": 1, "level": 1, "name": "Dummy", "type_id": None, "hp": 1000,
         "hpmax": 1000, "attack": 92, "defence": 0, "pos": (2.0, 0.0, 0.0),
         "home": (2.0, 0.0, 0.0), "dead": False, "hits": 0}
    feworld._SESSION["mobs"] = {4401: m}
    out, restore = _capture_out()
    try:
        combat.mob_damage(None, None, "ecb", False, a, 4401, m, 10, 7, "t",
                          skill=330)
        check("Ice Javelin's hit SLOWS the monster", status.has(m, "slow"),
              m.get("status"))
        check("...and the chase reads it", monsters.status_speed(a, m) < 1.0)
        combat.mob_damage(None, None, "ecb", False, a, 4401, m, 10, 7, "t",
                          skill=335)
        check("Freezing Wave ROOTS it: chase speed 0",
              monsters.status_speed(a, m) == 0.0)
        combat.mob_damage(None, None, "ecb", False, a, 4401, m, 10, 7, "t")
        check("...the next hit breaks the root", not status.has(m, "root"))
        combat.mob_damage(None, None, "ecb", False, a, 4401, m, 10, 7, "t",
                          skill=35)
        check("Shield Bash STUNS it: it refuses to swing",
              status.mob_refusal(m) == "stunned")
        status.clear(m)
        m["hp"] = 20
        combat.mob_damage(None, None, "ecb", False, a, 4401, m, 1, 7, "t",
                          skill=190)
        m["status"]["poison"]["next_at"] = time.monotonic() - 0.01   # due now
        del out[:]
        n = status.mob_pump(None, None, "ecb", False, a)
        check("the monster's poison ticks through mob_damage (credited to 7)",
              n >= 1 and m["ledger"].get(7, 0) >= 15, (n, m.get("ledger")))
        m["hp"], m["dead"] = 5, False
        m["status"]["poison"].update(next_at=time.monotonic() - 0.01)
        status.mob_pump(None, None, "ecb", False, a)
        check("...and a poison tick can KILL it (0x1004 sent)",
              m["dead"] and any(o[0] == 0x1004 for o in out), out)

        # --- the player: a crouch costs extra damage
        s = feworld._SESSION
        s.pop("crouch_at", None)
        base, _w = damage.monster_hit_damage(a, {"attack": 92})
        status.crouch_note(s, "test")
        crouch, why = damage.monster_hit_damage(a, {"attack": 92})
        check("a CROUCHED player takes --crouch-damage 1.3x (%d -> %d)"
              % (base, crouch), crouch == int(round(base * 1.3)), why)
        s["cpos"] = (5.0, 0.0, 0.0)
        check("...standing up (moving off the spot) ends it",
              damage.monster_hit_damage(a, {"attack": 92})[0] == base)
        s["cpos"] = (0.0, 0.0, 0.0)
        a1 = _args(crouch_damage=1.0)
        status.crouch_note(s, "test")
        check("--crouch-damage 1.0 turns it off",
              damage.monster_hit_damage(a1, {"attack": 92})[0] == base)
        s.pop("crouch_at", None)
        s["pw_tick_at"] = 10.0
        status.crouch_watch(s, 10.0)
        s["pw_tick_at"] = 11.35
        status.crouch_watch(s, 11.35)
        check("two 0x2028 regen asks 1.35 s apart = the client's crouch cadence",
              s.get("crouch_at") is not None)
        s.pop("crouch_at", None)
        s["pw_tick_at"] = 14.35
        status.crouch_watch(s, 14.35)
        check("...3 s apart = standing", s.get("crouch_at") is None)

        # --- the player: poison ticks the HP down; wire off sends no CONDITION
        s["player_hp"] = 100
        status.player_apply(None, None, "ecb", False, a, status.bad_specs(190),
                            src=4401)
        s["status"]["poison"]["next_at"] = time.monotonic() - 0.01
        del out[:]
        status.player_pump(None, None, "ecb", False, a)
        hp_push = [o for o in out if o[0] == 0x2024
                   and struct.unpack_from(">I", o[2], 0)[0] == 0x4]
        check("player poison: -15 HP, pushed as 0x2024 maskA 0x4",
              s["player_hp"] == 85 and len(hp_push) == 1, (s["player_hp"], out))
        check("...--status-effects-wire off: no CONDITION word goes out",
              not [o for o in out if o[0] == 0x2024
                   and struct.unpack_from(">I", o[2], 0)[0] == 0x100000])
        aw = _args(combat="on", status_effects="on",
                   status_effects_wire="on", unit_state=0x1E000000)
        del out[:]
        status.cond_sync(None, None, "ecb", False, aw)
        cw = [o for o in out if o[0] == 0x2024]
        check("--status-effects-wire on: 0x2024 maskA 0x100000 = base | POISON 0x1",
              len(cw) == 1 and cw[0][2] == struct.pack(">III", 0x100000, 0,
                                                       0x1E000001), cw)
        del out[:]
        status.cond_sync(None, None, "ecb", False, aw)
        check("...sent once per change, not every pump", out == [])
        status.clear(s)
        status.cond_sync(None, None, "ecb", False, aw)
        check("...and cleared when it ends",
              out and out[-1][2] == struct.pack(">III", 0x100000, 0, 0x1E000000),
              out)
        # stunned/disarmed attacker: no damage on 0xA011
        status.apply(s, status.bad_specs(195))          # Arm Break: disarm
        m2 = dict(m, hp=500, dead=False, ledger={}, first={}, status={})
        s["mobs"] = {4402: m2}
        body = struct.pack(">IIII", 4402, 7, 0, 0) + struct.pack(">HB", 0, 1)
        combat.combat_hit(None, None, "ecb", False, a, body)
        check("a DISARMED player's 0xA011 does no damage (manual p.39)",
              m2["hp"] == 500, m2["hp"])
        status.clear(s)
        combat.combat_hit(None, None, "ecb", False, a, body)
        check("...and once it ends, it does", m2["hp"] < 500, m2["hp"])
    finally:
        restore()
        for k in _KEYS:
            feworld._SESSION.pop(k, None)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    quiet = "-v" not in sys.argv
    if quiet:
        class _Null:
            def write(self, *a):
                pass

            def flush(self):
                pass
        _saved_out = sys.stdout
        sys.stdout = _Null()
    try:
        part_table()
        part_stat()
        part_profile()
        part_condition()
        part_misc()
        part_crystal()
        part_status()
    finally:
        if quiet:
            sys.stdout = _saved_out
    if FAILS:
        say("[fe_unit_test] FAILED: %s" % FAILS)
        sys.exit(1)
    say("[fe_unit_test] OK")
