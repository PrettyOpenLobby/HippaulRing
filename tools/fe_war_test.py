#!/usr/bin/env python3
"""fe_war_test.py -- fewar.py (Fantasy Earth war entities) end to end, no client.

Run from the repo root:  python tools/fe_war_test.py

Every reply fewar builds is decoded here with the EXACT primitive sequence
febody.py / fedis.py reported for the client's arm, then `r.done()` asserts
the stream is fully consumed -- a body one field long or short does not error
on the client, it desynchronises the stream silently.

    0x1009  MSG_BUILD_BUILDING_OK     arm 0x05054bda  HEADER-ONLY
    0x100A  MSG_BUILD_BUILDING_NG     arm 0x05054c06  u32
    0x1006  type 1 (building)         arm 0x0503b583  u16 u16 u32 u8 i32 i32 u16 u16 u16 u8 u32
                                                       (feworld.building_record, live 09-05)
    0x1006  type 2 (item)             arm 0x0503b7ff  cstr u16 u8 f32 f32 f32
    0x1006  type 7 (skill effect)     arm 0x0503b9fe  u32 u32 u32 u16 u8 f32 f32 f32 u32
    0x2035  wallet mask               arm 0x04fe4de0  u32 mask, u32 (bit 1)
    0x1004  MSG_DEL                   arm 0x0503a079  HEADER-ONLY, envelope = object
    0x100C  MSG_DESTROY_BUILDING_OK   arm 0x05054c6c  HEADER-ONLY
    0x100D  MSG_DESTROY_BUILDING_NG   arm 0x05054c94  HEADER-ONLY
    0x1043  entrance add              arm 0x05055212  u16 n x {u8 u32 f32 f32 f32 f32}
    0x1044  entrance remove-by-type   arm 0x05055350  u16 n x {u8}
    0x1049  build cost table          arm 0x050553c5  u16 n x {u16 u8 u16 u16}
    0x104A  clock offset byte         arm 0x05055aef  u8
    0x101A  war-map arm               0x05118367      u32 u32 u32 u32
    0x1156  VALIDATE_INTERVAL         0x050AD16F      u32 u32
    0x1158  VALIDATE_SEQUENCE_CANCEL  0x050AD1EC      u32
    0x112A  popup                     0x5112e50       u16 n x {cstr u16 u16 u16 u16 u16 u32}
    0x2010  attack notify             0x503be90       u32 u32 u16 u8 u32 f32 f32 f32 u32
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
import fewar     # noqa: E402

feworld.load_extensions(["fewar"])   # registers ours only

CHECKS = []
OUT = sys.__stdout__


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-64s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label + (": " + detail if detail else ""))


class Reader:
    """FE's stream primitives, by the byte count each one advances."""

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
            "stream, and everything after this message decodes as garbage"
            % (self.i, len(self.b), self.b[self.i:]))


STORE = {}


def _stub_store():
    STORE.clear()
    feworld._store_char_field = lambda a, k, v: (STORE.__setitem__(k, v), True)[1]
    feworld._load_char_field = lambda a, k, d=None: STORE.get(k, d)
    feworld._SESSION.clear()
    feworld._SESSION["account"] = "tester"
    feworld._SESSION["charid"] = 7


def _args(**kw):
    """The namespace feworld's chain reads before an extension gets the
    message (copied from fe_ui_test._args, plus fewar's own knobs)."""
    a = dict(unit_id="0", seq_mode="count", world_prefix=4, read_window=1.0,
             blacklist="on", distribution="on", distribution_rows=[],
             set_comment="ok", chat_echo="off", chat_line=None,
             chat_relay="off", gmcmd=None, gmcmd_file=None,
             ui_auto="ok", ui_auto_err=0, move_request="off",
             move_authority="off", unit_speed=None, unit_speed_repeat="off",
             npc=None, npc_base=1000, npc_walk=None, stat_probe="off",
             buildings=[], building_base=3000, building_hp=(100, 100),
             doors="off", door_rows=[], door_height="client",
             door_radius=15.0, door_default=10, spawn_xyz=(0.0, 0.0, 0.0),
             town_file="", spawns="off", spawns_max=12, monster_base=400,
             monster_level=1, events="on",
             shop_types="weapon=1,armor=2,item=3,ring=4",
             event_greeting=None, shop_price=100, shop_page=30,
             field_items="bag", field_equip="off", field_item_kind="0",
             bag_size=30, skill_grant="stored", equip_reflect="on",
             sell_price=50, bag_organize="on", skill_palette="request",
             room_exit="conversation", use_item="on",
             islands=5, groups=1, field_nations="", field_defenders="",
             gold=None, ring=None, total_score=None, crystal=None,
             validate_finish=10.0,
             war="offensive", war_fields="0,0,0,0", war_start="off",
             war_start_fields="0,0", war_deadline_ms=0, war_clock="telemetry",
             war_cycle="off", war_length_ms=600000, war_truce_ms=120000,
             war_result="desert", war_result_values="zero", war_result_rows=0,
             skill_list="on", skill_list_value=1, class_levels="self:30",
             acquire_skill="ok", status="off", status_resync="off",
             status_nums=list(range(101, 110)), status_skills=[],
             status_id=1, status_name="Lex", status_sex=0, status_class=1,
             status_nation=1, status_str2="",
             probe_on_auth=False, capture_only=False,
             # fewar
             build="real", build_costs="off", destroy="on", war_obj_base=5000)
    a.update(kw)
    return types.SimpleNamespace(**a)


class _Conn:
    def settimeout(self, t):
        pass

    def getpeername(self):
        return ("127.0.0.1", 1)


class _Capture:
    """Stub fenet's crypto and catch every world frame as (mid, unit, body)."""

    def __init__(self, frames=()):
        self.out = []
        self.inbox = [(0x30, f) for f in frames]

    def __enter__(self):
        self.saved = (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap,
                      fenet.bf_encrypt, fenet.traffic_wrap,
                      feworld.send_world_frame)
        fenet.recv_frame = lambda conn: self.inbox.pop(0) if self.inbox else None
        fenet.bf_decrypt = lambda st, body, mode, be: body
        fenet.traffic_unwrap = lambda plain: (1, plain)
        fenet.bf_encrypt = lambda st, body, mode, be: body
        fenet.traffic_wrap = lambda data, seq=1: data

        def _cap(conn, mid, body, prefix):
            unit, msg = struct.unpack_from(">IH", body, 0)
            self.out.append((msg, unit, body[6:]))
        feworld.send_world_frame = _cap
        return self

    def __exit__(self, *e):
        (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap,
         fenet.bf_encrypt, fenet.traffic_wrap,
         feworld.send_world_frame) = self.saved


def _dispatch(a, *inners):
    """Run inner messages through the REAL feworld._serve_loop."""
    with _Capture(inners) as cap:
        feworld._serve_loop(_Conn(), a, None, None, "ecb", False)
    return cap.out


def _gm(a, line):
    """Feed one `!verb` line to fewar.gm with a real Ctx."""
    with _Capture() as cap:
        ctx = feworld.Ctx(_Conn(), None, "ecb", False, a)
        took = fewar.gm(ctx, line)
    return took, cap.out


def _in_field(field=39, pos=(10.0, 2.0, -20.0)):
    s = feworld._SESSION
    s["in_field"] = True
    s["field"] = field
    s["cpos"] = pos
    s.pop("war_costs_field", None)
    s.pop("build_n", None)


def build_request(btype, model, gx, gy, gz, f38e, x, y, z):
    """The 23-byte body of 0x2007 as the three builders write it."""
    return (struct.pack(">H", 0x2007)
            + struct.pack(">HHHHHB", btype, model, gx, gy, gz, f38e)
            + struct.pack(">fff", x, y, z))


def decode_building(body):
    """0x1006 type 1 in the arm's read order (static-2026-09-04 README)."""
    r = Reader(body)
    n, obj, t = r.u16(), r.u32(), r.u8()
    # 0x0503b583..0x0503b616: u16 u16 u32 u8(side, +0x94 -- the static README
    # table MISSED this read) i32 i32 u16 u16 u16 u8 u32
    rec = dict(n=n, obj=obj, type=t, btype=r.u16(), model=r.u16(),
               f384=r.u32(), side=r.u8(), hp0=r.u32(), hp1=r.u32(),
               gx=r.u16(), gy=r.u16(), gz=r.u16(), f38e=r.u8(), stamp=r.u32())
    r.done()
    return rec


# ---------------------------------------------------------------------------
def part_build():
    say("0x2007 BUILD_BUILDING (real)")
    _stub_store()
    _in_field()
    STORE["crystal"] = 50
    a = _args(crystal=50, build_costs="all=20:100:3")
    out = _dispatch(a, build_request(6, 8, 130, 0, 120, 0, 5.0, 0.0, -20.0))
    ids = [m for m, _, _ in out]
    check("field entry pushed the cost table, then OK, building, wallet",
          ids == [0x1049, 0x1009, 0x1006, 0x2035], [hex(i) for i in ids])
    check("0x1009 is HEADER-ONLY (arm 0x05054bda reads nothing)",
          out[1][2] == b"")
    rec = decode_building(out[2][2])
    check("0x1006 type 1 decodes with the arm's sequence and is consumed",
          rec["n"] == 1 and rec["type"] == 1, repr(rec))
    check("the building carries the requested type/model/grid",
          (rec["btype"], rec["model"], rec["gx"], rec["gz"]) == (6, 8, 130, 120),
          repr(rec))
    check("the building's object id is --building-base + build_n",
          rec["obj"] == 3000 and out[2][1] == 3000, repr((rec["obj"], out[2][1])))
    # THE CONSTRUCTION TIMER, 2026-09-12. The record's last u32 is this
    # building's AGE IN MS at the add: the arm stores now-minus-it and
    # 0x05072770 compares now-minus-that against FE_BUILDING_DATA +0xac,
    # drawing a construction site while it is short. We sent 0 for EVERY
    # building, so every one of them raised itself again at every player it
    # was sent to. A build the player has just paid for is the one place 0
    # is right.
    check("a freshly paid-for build carries age 0 -- the client raises it",
          rec["stamp"] == 0, repr(rec))
    check("+0xac: Obelisk 30 s, Arrow Tower 40 s, War Craft 60 s, Castle none",
          [feworld.build_time_ms(t) for t in (6, 0, 5, 15)]
          == [30000, 40000, 60000, 0],
          [feworld.build_time_ms(t) for t in (6, 0, 5, 15)])
    check("anything ALREADY standing is finished instead (age None)",
          feworld.building_age(a, 6) > feworld.build_time_ms(6)
          and feworld.building_age(a, 16) > feworld.build_time_ms(16),
          (feworld.building_age(a, 6), feworld.building_age(a, 16)))
    check("a real age passes through, and is clamped at finished",
          (feworld.building_age(a, 6, 12000),
           feworld.building_age(a, 6, 10 ** 9))
          == (12000, feworld.build_time_ms(6) + 1000),
          (feworld.building_age(a, 6, 12000),
           feworld.building_age(a, 6, 10 ** 9)))
    check("--build-age zero restores the pre-09-12 wire value",
          feworld.building_age(_args(build_age="zero"), 6) == 0)
    check("--build-age done never shows a site, even for a fresh build",
          feworld.building_age(_args(build_age="done"), 6, 0)
          > feworld.build_time_ms(6))
    r = Reader(out[3][2])
    mask, val = r.u32(), r.u32()
    r.done()
    check("0x2035 bit 1 carries the DEBITED total (50 - 20)",
          mask == 2 and val == 30, repr((mask, val)))
    check("the store holds the new balance", STORE["crystal"] == 30, repr(STORE))
    check("the session remembers the placed building",
          feworld._SESSION["war_buildings"].get(3000, {}).get("cost") == 20)

    say("0x2007 refusals")
    STORE["crystal"] = 5
    out = _dispatch(a, build_request(6, 8, 130, 0, 120, 0, 0, 0, 0))
    check("too few crystals -> 0x100A code 9 and nothing placed",
          [m for m, _, _ in out] == [0x100A]
          and struct.unpack(">I", out[0][2])[0] == 9, repr(out))
    r = Reader(out[0][2])
    r.u32()
    r.done()
    feworld._SESSION["in_field"] = False
    out = _dispatch(a, build_request(6, 8, 130, 0, 120, 0, 0, 0, 0))
    check("not in a field -> 0x100A code 8",
          [m for m, _, _ in out] == [0x100A]
          and struct.unpack(">I", out[0][2])[0] == 8, repr(out))
    _in_field()
    out = _dispatch(_args(build_costs="all=20"),
                    build_request(6, 8, 130, 0, 120, 0, 0, 0, 0))
    check("cost set but the CRYSTAL channel off -> built, no debit push",
          [m for m, _, _ in out] == [0x1049, 0x1009, 0x1006], repr(out))
    out = _dispatch(_args(), build_request(6, 8, 130, 0, 120, 0, 0, 0, 0))
    check("--build-costs off -> no table, no wallet, building still placed",
          [m for m, _, _ in out] == [0x1009, 0x1006], repr(out))
    out = _dispatch(_args(), build_request(99, 8, 1, 0, 1, 0, 0, 0, 0))
    check("a type outside the shipped table -> 0x100A code 10",
          [m for m, _, _ in out] == [0x100A]
          and struct.unpack(">I", out[0][2])[0] == 10, repr(out))
    out = _dispatch(_args(), build_request(9, 9, 0, 0, 0, 0, 12.5, 0, -30.0))
    rec = decode_building(out[1][2])
    check("grid (0,0) with a world position falls back to world_to_grid",
          (rec["gx"], rec["gz"]) == (feworld.world_to_grid(12.5),
                                     feworld.world_to_grid(-30.0)), repr(rec))

    say("0x2007 modes")
    out = _dispatch(_args(build="ack"), build_request(6, 8, 130, 0, 120, 0, 0, 0, 0))
    check("--build ack -> the header-only OK alone (pre-09-10 behaviour)",
          [m for m, _, _ in out] == [0x1009] and out[0][2] == b"", repr(out))
    out = _dispatch(_args(build="off"), build_request(6, 8, 130, 0, 120, 0, 0, 0, 0))
    check("--build off -> nothing (the dialog hangs, A/B only)", out == [], repr(out))
    out = _dispatch(_args(), struct.pack(">H", 0x2007) + b"\x00\x01")
    check("a short body is acknowledged, not decoded",
          [m for m, _, _ in out] == [0x1009], repr(out))


def part_destroy():
    say("destroy: 0x208C sub 7 / !destroy")
    _stub_store()
    _in_field()
    a = _args()
    out = _dispatch(a, struct.pack(">HBI", 0x208C, 7, 3000))
    ids = [(m, u, b) for m, u, b in out]
    check("GM 'break building 3000' -> 0x1004 MSG_DEL on the object + 0x100C",
          ids == [(0x1004, 3000, b""), (0x100C, 0, b"")], repr(ids))
    out = _dispatch(a, struct.pack(">HBI", 0x208C, 0x0f, 1))
    check("an unnamed sub-command is logged only", out == [], repr(out))
    out = _dispatch(a, struct.pack(">H", 0x208C))
    check("an empty 0x208C body does not raise or send", out == [])
    out = _dispatch(_args(destroy="off"), struct.pack(">HBI", 0x208C, 7, 3000))
    check("--destroy off -> logged only", out == [], repr(out))
    took, out = _gm(a, "!destroy 3001")
    check("!destroy OBJ is taken and sends the same pair",
          took and [(m, u, b) for m, u, b in out]
          == [(0x1004, 3001, b""), (0x100C, 0, b"")], repr(out))
    took, out = _gm(a, "!destroy ng 3")
    check("!destroy ng -> 0x100D header-only (arm 0x05054c94 reads nothing)",
          took and out == [(0x100D, 0, b"")], repr(out))
    took, out = _gm(a, "!destroy")
    check("!destroy with no argument is taken and sends nothing",
          took and out == [])


def part_tables():
    say("0x1043 / 0x1044 entrance, 0x1049 cost table")
    _stub_store()
    _in_field()
    a = _args()
    took, out = _gm(a, "!entrance add 3 77 1.5 2.5 3.5 0.25")
    check("!entrance add is taken", took and [m for m, _, _ in out] == [0x1043])
    r = Reader(out[0][2])
    n = r.u16()
    row = (r.u8(), r.u32(), r.f32(), r.f32(), r.f32(), r.f32())
    r.done()
    check("0x1043 = [u16 1]{u8 type,u32 id,f32 x4} fully consumed",
          n == 1 and row == (3, 77, 1.5, 2.5, 3.5, 0.25), repr(row))
    took, out = _gm(a, "!entrance add 3 78")
    r = Reader(out[0][2])
    r.u16()
    r.u8()
    r.u32()
    pos = (r.f32(), r.f32(), r.f32())
    r.f32()
    r.done()
    check("without coordinates the reported position is used",
          pos == (10.0, 2.0, -20.0), repr(pos))
    took, out = _gm(a, "!entrance del 3 4")
    r = Reader(out[0][2])
    n = r.u16()
    types_ = [r.u8() for _ in range(n)]
    r.done()
    check("0x1044 = [u16 n]{u8 type}", out[0][0] == 0x1044 and types_ == [3, 4])

    took, out = _gm(a, "!warcost 0=20:100:3,6=15")
    check("!warcost SPEC is taken and pushes 0x1049",
          took and [m for m, _, _ in out] == [0x1049], repr(out))
    r = Reader(out[0][2])
    n = r.u16()
    rows = [(r.u16(), r.u8(), r.u16(), r.u16()) for _ in range(n)]
    r.done()
    check("0x1049 rows = {u16 type, u8 crystal, u16 c, u16 gold}, consumed",
          rows == [(0, 20, 3, 100), (6, 15, 0, 0)], repr(rows))
    took, out = _gm(a, "!warcost push")
    check("!warcost push re-sends the session table",
          took and [m for m, _, _ in out] == [0x1049])
    took, out = _gm(_args(), "!warcost")
    check("!warcost with nothing sends nothing", took and out == [])
    t = fewar.parse_costs("all=10,7=0:5")
    check("parse_costs: all= fills every shipped type, TYPE= overrides",
          t is not None and len(t) == len(feworld.BUILDING_TYPES)
          and t[7] == (0, 5, 0) and t[0] == (10, 0, 0), repr(t))
    check("parse_costs('off') is None", fewar.parse_costs("off") is None)

    say("the pump")
    feworld._SESSION.pop("war_costs_spec", None)
    _in_field(39)
    a = _args(build_costs="all=10")
    out = _dispatch(a, struct.pack(">HBI", 0x208C, 0x0f, 0),
                    struct.pack(">HBI", 0x208C, 0x0f, 0))
    check("the table is pushed ONCE per field entry",
          [m for m, _, _ in out] == [0x1049], repr(out))
    feworld._SESSION["field"] = 40
    out = _dispatch(a, struct.pack(">HBI", 0x208C, 0x0f, 0))
    check("a new field pushes it again", [m for m, _, _ in out] == [0x1049])
    feworld._SESSION["in_field"] = False
    feworld._SESSION.pop("war_costs_field", None)
    out = _dispatch(a, struct.pack(">HBI", 0x208C, 0x0f, 0))
    check("not in a field -> nothing", out == [])


def part_misc():
    say("0x104A, 0x101A, 0x1156, 0x1158, 0x112A")
    _stub_store()
    _in_field()
    a = _args()
    took, out = _gm(a, "!clockbyte 5")
    r = Reader(out[0][2])
    v = r.u8()
    r.done()
    check("0x104A = [u8]", took and out[0][0] == 0x104A and v == 5)

    took, out = _gm(a, "!warclock 1 2 60000")
    r = Reader(out[0][2])
    vals = (r.u32(), r.u32(), r.u32(), r.u32())
    r.done()
    check("0x101A = [u32 A][u32 B][u32 HI][u32 LO], header unit 0",
          out[0][0] == 0x101A and out[0][1] == 0 and vals == (1, 2, 0, 60000),
          repr(vals))

    took, out = _gm(a, "!fieldout interval 12345")
    r = Reader(out[0][2])
    hi, lo = r.u32(), r.u32()
    r.done()
    check("0x1156 = [u32 HI][u32 LO] (%lld)",
          out[0][0] == 0x1156 and (hi, lo) == (0, 12345))
    feworld._SESSION["validate_due"] = 1.0
    took, out = _gm(a, "!fieldout cancel 4")
    r = Reader(out[0][2])
    code = r.u32()
    r.done()
    check("0x1158 = [u32] and feworld's pending FINISH is dropped",
          out[0][0] == 0x1158 and code == 4
          and "validate_due" not in feworld._SESSION)

    took, out = _gm(a, "!warpopup Take crystals|Cancel")
    r = Reader(out[0][2])
    n = r.u16()
    rows = []
    for _ in range(n):
        rows.append((r.cstr(), r.u16(), r.u16(), r.u16(), r.u16(), r.u16(), r.u32()))
    r.done()
    check("0x112A = [u16 n]{cstr, u16 x5, u32} (reader 0x5112e50)",
          out[0][0] == 0x112A and [x[0] for x in rows]
          == ["Take crystals", "Cancel"], repr(rows))


def part_entities():
    say("0x2010 and 0x1006 types 2 / 7")
    _stub_store()
    _in_field()
    a = _args()
    body = fewar.hit_body(400, 7, 12, 2, 0, (1.0, 2.0, 3.0), 0)
    out = _dispatch(a, struct.pack(">H", 0x2010) + body)
    check("an inbound 0x2010 is decoded and answered with nothing", out == [])
    out = _dispatch(a, struct.pack(">H", 0x2010) + b"\x00\x01")
    check("a short 0x2010 body does not raise", out == [])
    # 0x2019 -- the BUILDING HIT (the only thing a swing on a keep sends,
    # live 2026-09-12: 17 of them at keep 2901, 0x2010/0xA011 at zero)
    hits = []
    saved_kh = getattr(feworld, "keep_hit", None)
    feworld.keep_hit = lambda conn, ob, mode, be, args, target, why="", skill=None: (
        hits.append((int(target), why, skill)) or True)
    body = (struct.pack(">IIHBII", 1, 29, 270, 1, 2901, 1001)
            + struct.pack(">fff", -175.3, 41.2, -10.7))
    ca = with_(a, combat="on") if "with_" in globals() else a
    try:
        setattr(ca, "combat", "on")
        out = _dispatch(ca, struct.pack(">H", 0x2019) + body)
        check("an inbound 0x2019 [attacker][seq][skill][hits][building][weapon]"
              "[pos] routes the building id to keep_hit, with the hit's skill "
              "(the swing's damage is the skill's power, audit A2)",
              hits == [(2901, "(0x2019)", 270)], hits)
        del hits[:]
        setattr(ca, "combat", "off")
        _dispatch(ca, struct.pack(">H", 0x2019) + body)
        check("...--combat off: logged only, keep_hit not called", hits == [])
        out = _dispatch(ca, struct.pack(">H", 0x2019) + b"\x00\x01")
        check("a short 0x2019 body does not raise", out == [])
    finally:
        if saved_kh is not None:
            feworld.keep_hit = saved_kh
    took, out = _gm(a, "!swing 400 7 12 2")
    r = Reader(out[0][2])
    vals = (r.u32(), r.u32(), r.u16(), r.u8(), r.u32(), r.f32(), r.f32(),
            r.f32(), r.u32())
    r.done()
    check("!swing -> 0x2010 [u32 u32 u16 u8 u32 f32x3 u32], header = attacker",
          out[0][0] == 0x2010 and out[0][1] == 400
          and vals[:4] == (400, 7, 12, 2) and vals[5:8] == (10.0, 2.0, -20.0),
          repr(vals))

    took, out = _gm(a, "!spawn2 5 Potion of X")
    r = Reader(out[0][2])
    n, obj, t = r.u16(), r.u32(), r.u8()
    name, item, f3ae = r.cstr(), r.u16(), r.u8()
    pos = (r.f32(), r.f32(), r.f32())
    r.done()
    check("type 2 = [cstr name][u16 item][u8][f32 x3], envelope = object",
          (n, t, name, item, f3ae) == (1, 2, "Potion of X", 5, 0)
          and obj == 5000 and out[0][1] == 5000 and pos == (10.0, 2.0, -20.0),
          repr((n, obj, t, name, item, f3ae, pos)))
    took, out = _gm(a, "!spawn7 12 3")
    r = Reader(out[0][2])
    n, obj, t = r.u16(), r.u32(), r.u8()
    caster, b, c = r.u32(), r.u32(), r.u32()
    skill, level = r.u16(), r.u8()
    pos = (r.f32(), r.f32(), r.f32())
    d = r.u32()
    r.done()
    check("type 7 = [u32 caster][u32][u32][u16 skill][u8 lv][f32 x3][u32]",
          (n, t, caster, skill, level) == (1, 7, 7, 12, 3) and obj == 5001,
          repr((n, obj, t, caster, b, c, skill, level, pos, d)))
    feworld._SESSION["in_field"] = False
    took, out = _gm(a, "!spawn2 5")
    check("!spawn2 outside a field sends nothing", took and out == [])
    took, out = _gm(a, "!spawn7 12")
    check("!spawn7 outside a field sends nothing", took and out == [])
    took, out = _gm(a, "!swing x y z")
    check("a bad argument is reported, not raised", took and out == [])
    took, out = _gm(a, "!somebodyelse 1")
    check("a foreign verb is NOT taken", not took)


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
        sys.stdout = _Null()
    try:
        part_build()
        part_destroy()
        part_tables()
        part_misc()
        part_entities()
    except Exception:
        import traceback
        traceback.print_exc(file=OUT)
        say("[fe_war_test] FAILED after %d checks" % len(CHECKS))
        sys.exit(1)
    say("[fe_war_test] OK -- %d checks" % len(CHECKS))
