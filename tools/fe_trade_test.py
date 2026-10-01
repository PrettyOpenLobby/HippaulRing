#!/usr/bin/env python3
"""fe_trade_test.py -- Fantasy Earth player-to-player TRADE (services/fetrade.py),
end to end, no client. Run from the repo root:  python tools/fe_trade_test.py [-v]

Every reply fetrade builds is decoded here with the EXACT primitive sequence
febody.py reported for that arm (see the module docstring for the VAs), then
Reader.done() asserts the stream is fully consumed:

    0x108B MSG_TRADE_REQUEST_OK   u32, u16
    0x108C MSG_TRADE_REQUEST_NG   u32
    0x108D MSG_TRADE_START        u16, u16
    0x108E MSG_TRADE_ENTRY_OK     header-only
    0x108F MSG_TRADE_ENTRY_NG     u16, u32
    0x1090 partner's offer        window read 0x050b3ac2: u16, u32, u32, u16,
                                  then n x item_fields (u32 mask1, u16 no, u32,
                                  u32, u32 mask4, u16, u16, u16, u16, u8, u32)
    0x1147 MSG_TRADE_COMPLETE_NG  u16, u32
    0x2052 inbound request        u32, u16
    0x2055 complete notice        header-only
    0x2056 / 0x2057 inbound       u16, u32
    0x2058                        header-only

Part 1 drives the whole machine against the PHANTOM counterparty on one
session; part 2 runs two sessions on two threads and moves rows between two
stores through feworld's relay queue.
"""
import os
import queue
import struct
import sys
import threading
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import fenet     # noqa: E402
import feworld   # noqa: E402
import fetrade   # noqa: E402

feworld.load_extensions(["fetrade"])


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

    def u32(self):
        v = struct.unpack_from(">I", self.b, self.i)[0]
        self.i += 4
        return v

    def item_fields(self):
        """The four masked groups exactly as feworld.item_fields lays them out
        and 0x5075980/0x5075a80/0x5075ad0/0x5075b80 read them."""
        assert self.u32() == 2
        no = self.u16()
        assert self.u32() == 0
        assert self.u32() == 0
        self.u32()                      # mask4
        count = self.u16()
        self.u16(); self.u16()          # durability max / cur
        self.u16()                      # kind
        self.u8()                       # equip marker
        slot = self.u32()
        return no, count, slot

    def done(self):
        assert self.i == len(self.b), (
            "%d of %d bytes consumed -- the client would leave %r on the stream"
            % (self.i, len(self.b), self.b[self.i:]))


STORE = {}          # charid -> {key: value}
NAMES = {7: "Lex", 8: "Bob"}


def _stub_store():
    def _cid():
        return feworld._SESSION.get("charid")
    feworld._store_char_field = lambda a, k, v: (STORE.setdefault(_cid(), {}).__setitem__(k, v), True)[1]
    feworld._load_char_field = lambda a, k, d=None: STORE.get(_cid(), {}).get(k, d)
    feworld._self_char = lambda a: {"name": NAMES.get(_cid(), "?"), "charid": _cid()}


def _args(**kw):
    a = dict(unit_id="0", seq_mode="count", world_prefix=4, read_window=1.0,
             blacklist="on", distribution="on", distribution_rows=[],
             set_comment="ok", chat_echo="off", chat_line=None, chat_relay="off",
             gmcmd=None, gmcmd_file=None,
             ui_auto="ok", ui_auto_err=0, move_request="off",
             move_authority="off", unit_speed=None, unit_speed_repeat="off",
             npc=["x"], npc_base=1000, npc_walk=None, stat_probe="off",
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
             gold=500, ring=None, total_score=None, crystal=None,
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
             # fetrade's knobs
             trade="on", trade_phantom="on", trade_phantom_name="Phantom",
             trade_face=None, trade_slots=9)
    a.update(kw)
    return types.SimpleNamespace(**a)


OUT = {}            # thread ident -> [(mid, unit, body)]


def _capture(conn, mid, body, prefix):
    unit, msg = struct.unpack_from(">IH", body, 0)
    OUT.setdefault(threading.get_ident(), []).append((msg, unit, body[6:]))


def _out():
    return OUT.setdefault(threading.get_ident(), [])


def _dispatch(a, inner):
    """ONE inner message through the real _serve_loop, crypto stubbed."""
    class _Conn:
        def recv(self, n):
            raise OSError("done")

        def settimeout(self, t):
            pass

        def getpeername(self):
            return ("127.0.0.1", 1)
    saved = (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap)
    frames = [(0x30, inner)]
    fenet.recv_frame = lambda c: frames.pop(0) if frames else None
    fenet.bf_decrypt = lambda s, b, m, be: b
    fenet.traffic_unwrap = lambda p: (1, p)
    try:
        feworld._serve_loop(_Conn(), a, None, None, "ecb", False)
    finally:
        fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap = saved


def q(a, mid, payload=b""):
    _dispatch(a, struct.pack(">H", mid) + payload)


def gm(a, line):
    assert fetrade.gm(feworld.Ctx(None, None, "ecb", False, a), line) is True


def pump(a):
    feworld.ext_pump(feworld.Ctx(None, None, "ecb", False, a))


def _reset():
    fetrade.TRADES.clear()
    fetrade._NEXT[0] = 1
    feworld._SESSION["trade"] = None
    feworld._SESSION.pop("trade_bind", None)
    _out()[:] = []


def ok(what):
    print("  %-58s PASS" % what, file=REPORT)


def _session(charid, account, name):
    feworld._SESSION["account"] = account
    feworld._SESSION["charid"] = charid
    feworld._SESSION["in_field"] = True
    feworld._chat_room_join()
    feworld._chat_room_name(name)


# ---------------------------------------------------------------------------
# PART 1 -- one session, the phantom on the other side
# ---------------------------------------------------------------------------
def part1():
    _stub_store()
    _reset()
    _session(7, "a", "Lex")
    STORE[7] = {"items": [[1001, 0x213, 0, 1], [1002, 0x214, 0, 3]],
                "equip": [], "gold": 500}
    a = _args()
    out = _out()

    # 0x2052 at yourself -> 0x108C code 1
    q(a, 0x2052, struct.pack(">I", 7))
    assert [m for m, _, _ in out] == [0x108C], out
    r = Reader(out[0][2]); assert r.u32() == 1; r.done()
    ok("0x2052 at yourself -> 0x108C [u32 1]")
    out[:] = []

    # 0x2052 at unit 1000 -> phantom -> 0x108B [u32 1000][u16 1]
    q(a, 0x2052, struct.pack(">I", 1000))
    assert [m for m, _, _ in out] == [0x108B], out
    r = Reader(out[0][2]); assert r.u32() == 1000; tid = r.u16(); r.done()
    assert tid == 1 and feworld._SESSION.get("trade") == 1
    ok("0x2052 at unit 1000 -> 0x108B [u32 1000][u16 tid=1] (phantom)")
    out[:] = []

    # !trade accept -> 0x108D [u16 tid][u16 SlotCnt]
    gm(a, "!trade accept")
    assert [m for m, _, _ in out] == [0x108D], out
    r = Reader(out[0][2]); assert r.u16() == 1; assert r.u16() == 9; r.done()
    ok("!trade accept -> 0x108D [u16 1][u16 9]")
    out[:] = []

    # !trade put -> 0x1090 with the phantom's full offer each time
    gm(a, "!trade put 0x300 2")
    gm(a, "!trade put gold 25")
    assert [m for m, _, _ in out] == [0x1090, 0x1090], out
    r = Reader(out[1][2])
    assert r.u16() == 1
    assert r.u32() == 25 and r.u32() == 0
    assert r.u16() == 1
    assert r.item_fields() == (0x300, 2, 0)
    r.done()
    ok("!trade put -> 0x1090 [u16 tid][u32 25][u32 0][u16 1] + item_fields")
    out[:] = []

    # the client's ENTRY: 100 gold + 2 of uid 1002 -> 0x108E header-only
    q(a, 0x2054, struct.pack(">HIIH", 1, 100, 0, 1) + struct.pack(">IH", 1002, 2))
    assert [m for m, _, _ in out] == [0x108E] and out[0][2] == b"", out
    ok("0x2054 [tid][100][0][1]{1002 x2} -> 0x108E (header-only)")
    out[:] = []

    # a second ENTRY -> 0x108F code 11 (already registered)
    q(a, 0x2054, struct.pack(">HIIH", 1, 0, 0, 0))
    r = Reader(out[0][2]); assert out[0][0] == 0x108F
    assert r.u16() == 1 and r.u32() == 11; r.done()
    ok("second 0x2054 -> 0x108F [u16 tid][u32 11]")
    out[:] = []

    # the client confirms first: nothing goes out until the phantom does
    q(a, 0x2055, struct.pack(">H", 1))
    assert out == [], out
    ok("0x2055 before the phantom confirms -> nothing sent")

    gm(a, "!trade confirm")
    ids = [m for m, _, _ in out]
    assert ids[0] == 0x2055 and out[0][2] == b"", ids
    assert 0x107A in ids and ids[-1] == 0x2024, ids
    assert ids.index(0x2055) < ids.index(0x107A) < ids.index(0x2024)
    # the store moved: uid 1002 3 -> 1, item 0x300 x2 landed as a new row
    assert STORE[7]["items"] == [[1001, 0x213, 0, 1], [1002, 0x214, 0, 1],
                                 [7001, 0x300, 0, 2]], STORE[7]["items"]
    assert STORE[7]["gold"] == 500 - 100 + 25, STORE[7]["gold"]
    # every 0x107A is the ADD form at the slot the store says
    adds = [(b) for m, _, b in out if m == 0x107A]
    got = []
    for b in adds:
        r = Reader(b)
        assert r.u8() == 1
        r.u8()
        slot = r.u32(); uid = r.u32()
        no, count, fslot = r.item_fields()
        r.done()
        assert slot == fslot
        got.append((slot, uid, no, count))
    assert got == [(1, 1002, 0x214, 1), (2, 7001, 0x300, 2)], got
    r = Reader(out[-1][2])
    maskA = r.u32(); r.u32()
    assert maskA == feworld._U2024_GOLD[0] and r.u32() == 425; r.done()
    assert feworld._SESSION.get("trade") is None and not fetrade.TRADES
    ok("!trade confirm -> 0x2055, 0x107A x2 (slots 1,2), 0x2024 gold=425; stores moved")
    out[:] = []

    # ENTRY refusals: equipped (8) and more gold than held (10)
    STORE[7]["equip"] = [[0, 1001]]
    q(a, 0x2052, struct.pack(">I", 1000)); gm(a, "!trade accept"); out[:] = []
    q(a, 0x2054, struct.pack(">HIIH", 2, 0, 0, 1) + struct.pack(">IH", 1001, 1))
    r = Reader(out[0][2]); assert out[0][0] == 0x108F
    assert r.u16() == 2 and r.u32() == 8; r.done()
    out[:] = []
    q(a, 0x2054, struct.pack(">HIIH", 2, 9999, 0, 0))
    r = Reader(out[0][2]); assert out[0][0] == 0x108F
    assert r.u16() == 2 and r.u32() == 10; r.done()
    out[:] = []
    ok("0x2054 equipped uid -> 0x108F code 8; gold > held -> code 10")

    # the client's CANCEL drops the trade; nothing goes back to the client
    q(a, 0x2056, struct.pack(">HI", 2, 3))
    assert out == [] and feworld._SESSION.get("trade") is None and not fetrade.TRADES
    ok("0x2056 from the client -> trade dropped, nothing sent")
    STORE[7]["equip"] = []

    # phantom-initiated: !trade offer -> inbound 0x2052 [u32 face][u16 tid]
    gm(a, "!trade offer Bob")
    assert [m for m, _, _ in out] == [0x2052], out
    r = Reader(out[0][2]); assert r.u32() == 1000; tid = r.u16(); r.done()
    assert tid == 3
    out[:] = []
    q(a, 0x2053, struct.pack(">H", 3))
    r = Reader(out[0][2]); assert out[0][0] == 0x108D
    assert r.u16() == 3 and r.u16() == 9; r.done()
    out[:] = []
    q(a, 0x2057, struct.pack(">HI", 3, 2))
    assert out == [] and not fetrade.TRADES
    ok("!trade offer -> 0x2052 [u32 1000][u16 3]; 0x2053 -> 0x108D; 0x2057 drops it")

    # phantom declines the client's request -> 0x2057 [u16 tid][u32 2]
    q(a, 0x2052, struct.pack(">I", 1000)); out[:] = []
    gm(a, "!trade decline")
    r = Reader(out[0][2]); assert out[0][0] == 0x2057
    assert r.u16() == 4 and r.u32() == 2; r.done()
    assert not fetrade.TRADES
    ok("!trade decline -> 0x2057 [u16 tid][u32 2]")
    out[:] = []

    # confirm with no entry -> 0x1147 [u16 tid][u32 0]; the phantom cancel
    q(a, 0x2052, struct.pack(">I", 1000)); gm(a, "!trade accept"); out[:] = []
    q(a, 0x2055, struct.pack(">H", 5))
    r = Reader(out[0][2]); assert out[0][0] == 0x1147
    assert r.u16() == 5 and r.u32() == 0; r.done()
    out[:] = []
    gm(a, "!trade cancel 8")
    r = Reader(out[0][2]); assert out[0][0] == 0x2056
    assert r.u16() == 5 and r.u32() == 8; r.done()
    assert not fetrade.TRADES and feworld._SESSION.get("trade") is None
    ok("0x2055 with no entry -> 0x1147 [tid][0]; !trade cancel -> 0x2056 [tid][8]")
    out[:] = []

    # probes
    gm(a, "!trade offer"); gm(a, "!trade clear"); gm(a, "!trade ng 1145 15"); out_ids = [m for m, _, _ in out]
    assert out_ids == [0x2052, 0x2058, 0x1145], [hex(i) for i in out_ids]
    assert out[1][2] == b""
    r = Reader(out[2][2]); assert r.u16() == 6 and r.u32() == 15; r.done()
    q(a, 0x2057, struct.pack(">HI", 6, 2))
    ok("!trade clear -> 0x2058 header-only; !trade ng 1145 15 -> [u16 tid][u32 15]")
    out[:] = []

    # --trade off answers nothing; --trade-phantom off refuses with code 5
    q(_args(trade="off"), 0x2052, struct.pack(">I", 1000))
    assert out == [], out
    q(_args(trade_phantom="off"), 0x2052, struct.pack(">I", 1000))
    r = Reader(out[0][2]); assert out[0][0] == 0x108C and r.u32() == 5; r.done()
    ok("--trade off -> silence; --trade-phantom off -> 0x108C [u32 5]")
    out[:] = []

    # 0x2059 is logged and NOT answered
    q(a, 0x2059)
    assert out == []
    ok("0x2059 (not trade) -> logged, nothing sent")
    feworld._chat_room_leave()


# ---------------------------------------------------------------------------
# PART 2 -- two sessions on two threads, rows cross between two stores
# ---------------------------------------------------------------------------
class _Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.tasks, self.results = queue.Queue(), queue.Queue()

    def run(self):
        # OUT is keyed by thread ident, and a finished thread's ident is
        # handed to the next thread started (Linux does this at once). Without
        # this, part 3's worker inherits part 2's leftover sends as its own.
        OUT.pop(threading.get_ident(), None)
        while True:
            fn = self.tasks.get()
            if fn is None:
                return
            try:
                self.results.put((True, fn()))
            except BaseException as e:            # noqa: BLE001
                import traceback
                traceback.print_exc()
                self.results.put((False, e))

    def do(self, fn):
        self.tasks.put(fn)
        good, val = self.results.get(timeout=10)
        if not good:
            raise val
        return val


def part2():
    _stub_store()
    _reset()
    STORE[7] = {"items": [[1001, 0x213, 0, 1]], "equip": [], "gold": 500}
    STORE[8] = {"items": [[8001, 0x400, 0, 2]], "equip": [], "gold": 100}
    _session(7, "a", "Lex")
    a = _args()
    b = _args(gold=100)
    w = _Worker(); w.start()
    w.do(lambda: (_session(8, "b", "Bob"), feworld._SESSION.__setitem__("trade", None)))
    bob_out = w.do(_out)
    out = _out()

    gm(a, "!trade bind 1000 Bob")
    q(a, 0x2052, struct.pack(">I", 1000))
    r = Reader(out[0][2]); assert out[0][0] == 0x108B
    assert r.u32() == 1000; tid = r.u16(); r.done()
    out[:] = []
    w.do(lambda: pump(b))
    assert [m for m, _, _ in bob_out] == [0x2052], bob_out
    r = Reader(bob_out[0][2]); assert r.u32() == 1000 and r.u16() == tid; r.done()
    bob_out[:] = []
    ok("bound 0x2052 -> Lex 0x108B, Bob gets inbound 0x2052 [u32 face][u16 tid]")

    w.do(lambda: q(b, 0x2053, struct.pack(">H", tid)))
    r = Reader(bob_out[0][2]); assert bob_out[0][0] == 0x108D
    assert r.u16() == tid and r.u16() == 9; r.done()
    bob_out[:] = []
    pump(a)
    r = Reader(out[0][2]); assert out[0][0] == 0x108D
    assert r.u16() == tid and r.u16() == 9; r.done()
    out[:] = []
    ok("Bob 0x2053 -> 0x108D to Bob and (relayed) to Lex")

    q(a, 0x2054, struct.pack(">HIIH", tid, 50, 0, 1) + struct.pack(">IH", 1001, 1))
    assert [m for m, _, _ in out] == [0x108E], out
    out[:] = []
    w.do(lambda: pump(b))
    r = Reader(bob_out[0][2]); assert bob_out[0][0] == 0x1090
    assert r.u16() == tid and r.u32() == 50 and r.u32() == 0 and r.u16() == 1
    assert r.item_fields() == (0x213, 1, 0); r.done()
    bob_out[:] = []
    w.do(lambda: q(b, 0x2054, struct.pack(">HIIH", tid, 0, 0, 1) + struct.pack(">IH", 8001, 2)))
    assert [m for m, _, _ in bob_out] == [0x108E]
    bob_out[:] = []
    pump(a)
    r = Reader(out[0][2]); assert out[0][0] == 0x1090
    assert r.u16() == tid and r.u32() == 0 and r.u32() == 0 and r.u16() == 1
    assert r.item_fields() == (0x400, 2, 0); r.done()
    out[:] = []
    ok("each 0x2054 -> 0x108E to the sender and 0x1090 to the partner")

    q(a, 0x2055, struct.pack(">H", tid))
    assert out == [], out
    w.do(lambda: q(b, 0x2055, struct.pack(">H", tid)))
    assert bob_out == [], bob_out           # Bob waits for Lex to apply first
    pump(a)                                  # Lex applies, reports back
    ids = [m for m, _, _ in out]
    assert ids[0] == 0x2055 and 0x107A in ids and ids[-1] == 0x2024, [hex(i) for i in ids]
    w.do(lambda: pump(b))                    # Bob applies
    bids = [m for m, _, _ in bob_out]
    assert bids[0] == 0x2055 and 0x107A in bids and bids[-1] == 0x2024, [hex(i) for i in bids]
    assert STORE[7]["items"] == [[7001, 0x400, 0, 2]], STORE[7]
    assert STORE[7]["gold"] == 450, STORE[7]
    assert STORE[8]["items"] == [[8002, 0x213, 0, 1]], STORE[8]
    assert STORE[8]["gold"] == 150, STORE[8]
    assert not fetrade.TRADES
    assert feworld._SESSION.get("trade") is None
    assert w.do(lambda: feworld._SESSION.get("trade")) is None
    ok("both 0x2055 -> apply on BOTH threads: rows and gold crossed, 0x2055/0x107A/0x2024 each")

    # a partner that hangs up mid-trade: the relay reaches nobody -> 0x2056
    out[:] = []; bob_out[:] = []
    q(a, 0x2052, struct.pack(">I", 1000)); out[:] = []
    w.do(lambda: (pump(b), q(b, 0x2053, struct.pack(">H", 2))))
    pump(a); out[:] = []
    q(a, 0x2054, struct.pack(">HIIH", 2, 0, 0, 0))
    assert [m for m, _, _ in out] == [0x108E]; out[:] = []
    # Bob enters an empty offer, confirms, and hangs up
    w.do(lambda: (pump(b), q(b, 0x2054, struct.pack(">HIIH", 2, 0, 0, 0)),
                  q(b, 0x2055, struct.pack(">H", 2))))
    w.do(feworld._chat_room_leave)
    q(a, 0x2055, struct.pack(">H", 2))      # pumps Bob's 0x1090 first, then completes
    ids = [m for m, _, _ in out]
    assert ids == [0x1090, 0x2056], [hex(i) for i in ids]
    r = Reader(out[1][2]); assert r.u16() == 2 and r.u32() == 1; r.done()
    assert not fetrade.TRADES and feworld._SESSION.get("trade") is None
    assert STORE[7]["items"] == [[7001, 0x400, 0, 2]], "nothing may move"
    ok("partner gone at completion -> 0x2056 [tid][1] to the survivor, nothing moved")
    w.tasks.put(None)
    feworld._chat_room_leave()


# ---------------------------------------------------------------------------
# PART 3 -- CLICK-TO-TRADE with no bind (2026-09-11, --unit-id auto): the
# clicked unit id IS the peer's charid, because fepresence draws it that way
# ---------------------------------------------------------------------------
def part3():
    _stub_store()
    _reset()
    STORE[7] = {"items": [], "equip": [], "gold": 0}
    STORE[8] = {"items": [], "equip": [], "gold": 0}
    _session(7, "a", "Lex")
    a = _args(unit_id="auto")
    b = _args(unit_id="auto")
    w = _Worker(); w.start()
    w.do(lambda: (_session(8, "b", "Bob"), feworld._SESSION.__setitem__("trade", None)))
    bob_out = w.do(_out)
    out = _out()

    # no !trade bind anywhere: Lex clicks Bob's avatar, object id 8
    q(a, 0x2052, struct.pack(">I", 8))
    assert [m for m, _, _ in out] == [0x108B], out
    r = Reader(out[0][2]); assert r.u32() == 8; tid = r.u16(); r.done()
    out[:] = []
    ok("0x2052 at unit 8 with NO bind -> 0x108B (resolved by charid)")

    # Bob has Lex drawn (fepresence's pres_seen) -> the face is Lex's own
    # avatar, charid 7, not the stand-in NPC
    w.do(lambda: feworld._SESSION.__setitem__("pres_seen", {7: {}}))
    w.do(lambda: pump(b))
    assert [m for m, _, _ in bob_out] == [0x2052], bob_out
    r = Reader(bob_out[0][2]); face = r.u32(); assert r.u16() == tid; r.done()
    assert face == 7, face
    bob_out[:] = []
    ok("...and Bob's inbound 0x2052 fronts it with Lex's OWN avatar (face 7)")

    # with the peer NOT drawn, it falls back to the stand-in NPC.
    # (since 2026-10-01 the requester's cancel reaches the partner and clears
    # its session key itself -- part 4; the explicit clear here is harmless)
    q(a, 0x2056, struct.pack(">HI", tid, 3))
    w.do(lambda: (feworld._SESSION.__setitem__("pres_seen", {}),
                  feworld._SESSION.__setitem__("trade", None), pump(b)))
    bob_out[:] = []
    out[:] = []
    q(a, 0x2052, struct.pack(">I", 8))
    assert out and out[0][0] == 0x108B, [(hex(m), b) for m, _, b in out]
    r = Reader(out[0][2]); r.u32(); tid2 = r.u16(); r.done()
    out[:] = []
    w.do(lambda: pump(b))
    r = Reader(bob_out[0][2]); face = r.u32(); assert r.u16() == tid2; r.done()
    assert face == 1000, face
    ok("...peer not drawn -> face falls back to --npc-base 1000")
    bob_out[:] = []

    # under a FIXED unit id the charid shortcut must not fire: every session's
    # own unit is the same number, so an id cannot name a player
    q(a, 0x2056, struct.pack(">HI", tid2, 3))
    w.do(lambda: (feworld._SESSION.__setitem__("trade", None), pump(b)))
    out[:] = []; bob_out[:] = []
    q(_args(unit_id="1", trade_phantom="off"), 0x2052, struct.pack(">I", 8))
    r = Reader(out[0][2]); assert out[0][0] == 0x108C and r.u32() == 5; r.done()
    ok("--unit-id 1 -> no charid shortcut, 0x108C code 5 (unbound unit)")
    out[:] = []
    w.do(feworld._chat_room_leave)
    w.tasks.put(None)
    feworld._chat_room_leave()


# ---------------------------------------------------------------------------
# PART 4 -- the manual's rules (2026-10-01): a partner going away cancels the
# trade on the survivor's screen, same field only, blacklist both ways, and
# the 50-crystal carry cap (NG 14)
# ---------------------------------------------------------------------------
def part4():
    _stub_store()
    _reset()
    STORE[7] = {"items": [], "equip": [], "gold": 0, "crystal": 20}
    STORE[8] = {"items": [], "equip": [], "gold": 0, "crystal": 45}
    _session(7, "a", "Lex")
    a = _args(unit_id="auto", crystal=0)
    b = _args(unit_id="auto", crystal=0)
    w = _Worker(); w.start()
    w.do(lambda: (_session(8, "b", "Bob"), feworld._SESSION.__setitem__("trade", None)))
    bob_out = w.do(_out)
    out = _out()

    def bob_trade():
        return w.do(lambda: feworld._SESSION.get("trade"))

    def request():
        out[:] = []
        q(a, 0x2052, struct.pack(">I", 8))
        assert out and out[0][0] == 0x108B, [(hex(m), x) for m, _, x in out]
        r = Reader(out[0][2]); r.u32(); tid = r.u16(); r.done()
        out[:] = []
        w.do(lambda: pump(b))
        bob_out[:] = []
        return tid

    # the requester cancels: the relay now REACHES the partner
    tid = request()
    q(a, 0x2056, struct.pack(">HI", tid, 3))
    w.do(lambda: pump(b))
    assert [m for m, _, _ in bob_out] == [0x2057], bob_out
    r = Reader(bob_out[0][2]); assert r.u16() == tid and r.u32() == 2; r.done()
    assert bob_trade() is None
    bob_out[:] = []
    ok("a cancel reaches the partner: 0x2057 [tid][2], its trade key cleared")

    # the requester LOGS OUT while the request is pending
    tid = request()
    fetrade.session_end()
    w.do(lambda: pump(b))
    assert [m for m, _, _ in bob_out] == [0x2057], bob_out
    assert bob_trade() is None and not fetrade.TRADES
    assert feworld._SESSION.get("trade") is None
    bob_out[:] = []
    ok("requester logout -> partner 0x2057, both trade keys clear")

    # the partner logs out with the window OPEN
    tid = request()
    w.do(lambda: q(b, 0x2053, struct.pack(">H", tid)))
    pump(a); out[:] = []; bob_out[:] = []
    w.do(fetrade.session_end)
    pump(a)
    assert [m for m, _, _ in out] == [0x2056], out
    r = Reader(out[0][2]); assert r.u16() == tid and r.u32() == 1; r.done()
    assert feworld._SESSION.get("trade") is None and not fetrade.TRADES
    out[:] = []
    ok("partner logout mid-trade -> 0x2056 [tid][1] ('Partner cancelled')")

    # same field only
    feworld._TLS.session["field"] = 5
    w.do(lambda: feworld._TLS.session.__setitem__("field", 6))
    q(a, 0x2052, struct.pack(">I", 8))
    r = Reader(out[0][2]); assert out[0][0] == 0x108C and r.u32() == 5; r.done()
    out[:] = []
    w.do(lambda: feworld._TLS.session.__setitem__("field", 5))
    ok("partner in another field -> 0x108C code 5")

    # blacklist: Lex has BOB (upper-cased, as the client stores it)
    saved = feworld.blacklist_rows
    feworld.blacklist_rows = lambda args: [{"id": 0x40000001, "name": "BOB"}]
    try:
        q(a, 0x2052, struct.pack(">I", 8))
    finally:
        feworld.blacklist_rows = saved
    r = Reader(out[0][2]); assert out[0][0] == 0x108C and r.u32() == 8; r.done()
    assert not fetrade.TRADES
    out[:] = []
    ok("blacklisted partner -> 0x108C code 8, no trade opened")

    # the carry cap: Bob holds 45, Lex offers 10 crystal -> 55 > 50 -> NG 14
    tid = request()
    w.do(lambda: q(b, 0x2053, struct.pack(">H", tid)))
    pump(a); out[:] = []; bob_out[:] = []
    saved_of = fetrade._crystal_of
    fetrade._crystal_of = (lambda args, party: 45 if not party.is_me()
                           else saved_of(args, party))
    try:
        q(a, 0x2054, struct.pack(">HIIH", tid, 0, 10, 0))
        r = Reader(out[0][2]); assert out[0][0] == 0x108F
        assert r.u16() == tid and r.u32() == 14; r.done()
        out[:] = []
        ok("an offer that puts the partner over 50 crystal -> 0x108F code 14")
        q(a, 0x2054, struct.pack(">HIIH", tid, 0, 5, 0))
        assert [m for m, _, _ in out] == [0x108E], out
        ok("...5 (to exactly 50) is accepted")
    finally:
        fetrade._crystal_of = saved_of
    out[:] = []
    q(a, 0x2056, struct.pack(">HI", tid, 3))
    w.do(lambda: pump(b))
    w.do(feworld._chat_room_leave)
    w.tasks.put(None)
    feworld._chat_room_leave()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    REPORT = sys.stdout
    quiet = "-v" not in sys.argv
    saved_send = (feworld.send_world_frame, fenet.bf_encrypt, fenet.traffic_wrap)
    feworld.send_world_frame = _capture
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data
    if quiet:
        class _Null:
            def write(self, *a):
                pass

            def flush(self):
                pass
        _saved_out = sys.stdout
        sys.stdout = _Null()
    try:
        part1()
        part2()
        part3()
        part4()
    finally:
        if quiet:
            sys.stdout = _saved_out
        feworld.send_world_frame, fenet.bf_encrypt, fenet.traffic_wrap = saved_send
    print("[fe_trade_test] OK")
