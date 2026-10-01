#!/usr/bin/env python3
"""fe_presence_test.py -- Fantasy Earth PLAYERS SEE EACH OTHER (services/
fepresence.py), end to end, no client. Run from the repo root:
    python tools/fe_presence_test.py [-v]

Two sessions on two threads (Lex charid 7 here, Bob charid 8 on a worker),
each fed through feworld's REAL extension seam; every record fepresence sends
is decoded with the client's own reader order and asserted fully consumed:

    0x1006 MSG_ADD   [u16 1][u32 obj][u8 0][u32 mask1][u32 sub]{identity,
                     ascending _AVATAR_SUB bits}[u32 mask2][f32 x3 pos]
                     [f32 x3 facing][u32 mask3]{[u32 uid][u32 2][u16 no]}
    0x2023 action 0  [u32 0][u32 a][u32 b][u16 state][i16 x2 spd][i16 x3 pos]
                     [u32 tail]  (28 B, feworld._MV_*)
    0x1004 MSG_DEL   header-only, the envelope id is the object

and each check asserts the envelope (header) id is the PEER's charid.
"""
import os
import queue
import struct
import sys
import threading
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import fenet       # noqa: E402
import feworld     # noqa: E402
import fepresence  # noqa: E402

# EVERY module, as prod loads them: a second claim on 0x2023/0x2000/0x2017
# would raise here rather than crash-loop prod.
feworld.load_extensions()

REPORT = sys.stdout
FAILS = []


def check(what, cond, detail=""):
    if cond:
        print("  %-66s PASS" % what, file=REPORT)
    else:
        print("  %-66s FAIL %s" % (what, detail), file=REPORT)
        FAILS.append(what)


# ---------------------------------------------------------------------------
# stubs: the character store and the wire
# ---------------------------------------------------------------------------
CHARS = {
    7: {"name": "Lex", "sex": 0, "look1": 3, "f24": 0, "f28": 0, "look2": 4,
        "look3": 1, "look4": 0, "force": 1, "charid": 7},
    8: {"name": "Bob", "sex": 1, "look1": 2, "f24": 5, "f28": 0, "look2": 1,
        "look3": 2, "look4": 3, "force": 2, "charid": 8},
    400: {"name": "Edge", "sex": 0, "force": 1, "charid": 400},
}
STORE = {}


def _stub_store():
    def _cid():
        return feworld._SESSION.get("charid")
    feworld._self_char = lambda a: CHARS.get(_cid())
    # `force` is the NATION and fw.nation_of reads it through here, so it has
    # to come from the character, not from the (item/equip) store rows
    feworld._load_char_field = lambda a, k, d=None: (
        CHARS.get(_cid(), {}).get(k, d) if k == "force"
        else STORE.get(_cid(), {}).get(k, d))


OUT = {}            # thread ident -> [(mid, unit, body)]


def _capture(conn, mid, body, prefix):
    unit, msg = struct.unpack_from(">IH", body, 0)
    OUT.setdefault(threading.get_ident(), []).append((msg, unit, body[6:]))


def _out():
    return OUT.setdefault(threading.get_ident(), [])


def _args(**kw):
    a = dict(unit_id="auto", seq_mode="count", world_prefix=4, monster_base=400,
             presence="on", presence_stale=10.0, presence_submask=0x2FF,
             presence_gear="off", presence_hp="off",
             presence_map="on", presence_map_self="off",
             presence_move_state="client", presence_move_tail="client",
             # the blocks below exercise the older PACED path on purpose;
             # raw_relay() tests the shipped default (raw) and asserts it
             presence_relay="paced", presence_relay_actions="all",
             presence_clock="map",
             # the two_players block tests the RAW relay; paced mode (the
             # shipped default) has its own block below
             presence_interp="off", presence_step_min=1.05,
             presence_move_min=1.5, presence_settle_ms=700.0)
    a.update(kw)
    return types.SimpleNamespace(**a)


def ctx_of(a):
    return feworld.Ctx(None, None, "ecb", False, a)


def pump(a, now=None):
    fepresence.pump(ctx_of(a), now=now)


def hb(tick_a, tick_b, spd, pos, state=0, tail=0x00080000):
    """A client's own 28-byte 0x2023 action-0 heartbeat.

    The tail default is what MEASURED records carry (prod, 2026-09-11): every
    real 28-byte 0x2023 from both clients ends 00 08 00 00."""
    return (struct.pack(">IIIH", 0, tick_a, tick_b, state)
            + struct.pack(">hh", *spd)
            + struct.pack(">hhh", *(int(round(v * 10)) for v in pos))
            + struct.pack(">I", tail))


def send_hb(a, body):
    """Through the SEAM exactly as _serve_loop calls it; must DECLINE."""
    return feworld.ext_dispatch(ctx_of(a), 0x2023, struct.pack(">H", 0x2023) + body)


def enter(charid, field, room=-1, account=None):
    s = feworld._SESSION
    s["account"] = account or ("member:%d" % charid)
    s["charid"] = charid
    s["in_field"] = True
    s["field"] = field
    s["room"] = room
    s["field_ready"] = True
    feworld._chat_room_join()
    feworld._chat_room_name(CHARS.get(charid, {}).get("name", "?"))


# ---------------------------------------------------------------------------
# decoders -- the client's reader order
# ---------------------------------------------------------------------------
class Reader:
    def __init__(self, b):
        self.b, self.i = b, 0

    def take(self, fmt):
        v = struct.unpack_from(">" + fmt, self.b, self.i)
        self.i += struct.calcsize(">" + fmt)
        return v if len(v) > 1 else v[0]

    def cstr(self):
        e = self.b.index(b"\0", self.i)
        s = self.b[self.i:e].decode("cp932")
        self.i = e + 1
        return s

    def done(self):
        return self.i == len(self.b)


def decode_add(body):
    r = Reader(body)
    out = {"count": r.take("H"), "obj": r.take("I"), "type": r.take("B")}
    m1 = r.take("I")
    out["mask1"] = m1
    ident = {}
    if m1 & 1:
        sub = r.take("I")
        out["sub"] = sub
        for bit, key, code, _o in feworld._AVATAR_SUB:
            if sub & bit:
                ident[key] = r.cstr() if code == "cstr" else r.take(code)
    out["ident"] = ident
    m2 = r.take("I")
    out["mask2"] = m2
    if m2 & 1:
        out["pos"] = r.take("fff")
    if m2 & 4:
        out["facing"] = r.take("fff")
    m3 = r.take("I")
    out["mask3"] = m3
    out["worn"] = []
    for i in range(13):
        if m3 & (1 << i):
            out["worn"].append((i, r.take("I"), r.take("I"), r.take("H")))
    out["done"] = r.done()
    return out


def decode_move(body):
    if len(body) != 28:
        return None
    act, a, b, st, s1, s2, x, y, z, tail = struct.unpack(">IIIHhhhhhI", body)
    return {"act": act, "ticks": (a, b), "state": st, "spd": (s1, s2),
            "pos": (x * 0.1, y * 0.1, z * 0.1), "tail": tail}


def near(p, q, eps=0.051):
    return all(abs(x - y) <= eps for x, y in zip(p, q))


# ---------------------------------------------------------------------------
# a second session on its own thread
# ---------------------------------------------------------------------------
class _Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.tasks, self.results = queue.Queue(), queue.Queue()

    def run(self):
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


# ---------------------------------------------------------------------------
def seam():
    print("\nthe seam: three shadows, all DECLINE", file=REPORT)
    for mid in (0x2023, 0x2000, 0x2017):
        check("0x%04X is claimed by fepresence" % mid,
              getattr(feworld.EXT_HANDLERS.get(mid), "__module__", "")
              == "fepresence")
    a = _args()
    ctx = ctx_of(a)
    check("0x2023 shadow returns False (feworld's arm still runs)",
          send_hb(a, hb(1, 2, (1000, 0), (1, 2, 3))) is False)
    check("0x2000 shadow returns False",
          feworld.ext_dispatch(ctx, 0x2000, struct.pack(">HI", 0x2000, 5)) is False)
    check("0x2017 shadow returns False",
          feworld.ext_dispatch(ctx, 0x2017, struct.pack(">H", 0x2017)) is False)
    # a malformed inner must still decline -- ext_dispatch counts a RAISE as
    # handled, which would starve feworld's own 0x2023 arm
    check("0x2023 shadow on garbage still returns False (never raises)",
          fepresence.on_telemetry(ctx, None) is False)
    check("a 14-byte 0x2023 (another action) is not recorded",
          send_hb(a, struct.pack(">IIIH", 0xF, 1, 2, 1)) is False)
    # PROD TODAY: --unit-id 1. The whole module must be inert, shadows and all,
    # so landing it changes nothing until that knob moves.
    for k in ("pres_mv", "pres_mv_n", "pres_gone"):
        feworld._SESSION.pop(k, None)
    fixed = _args(unit_id="1")
    send_hb(fixed, hb(1, 2, (1000, 0), (1, 2, 3)))
    feworld.ext_dispatch(ctx_of(fixed), 0x2017, struct.pack(">H", 0x2017))
    check("under a fixed --unit-id the shadows record NOTHING (inert on prod)",
          feworld._SESSION.get("pres_mv") is None
          and feworld._SESSION.get("pres_gone") is None,
          dict(feworld._TLS.session))
    for k in ("pres_mv", "pres_mv_n", "pres_mv_t", "pres_gone"):
        feworld._SESSION.pop(k, None)


def two_players():
    print("\ntwo players, one field", file=REPORT)
    _stub_store()
    a = _args()
    b = _args()
    out = _out()
    out[:] = []
    enter(7, 11)
    w = _Worker()
    w.start()
    w.do(_stub_store)
    w.do(lambda: enter(8, 11))
    bob_out = w.do(_out)

    # Bob reports, builds his card on his own thread
    bob_pos = (10.0, 20.5, -30.0)
    w.do(lambda: send_hb(b, hb(900, 901, (1000, 0), bob_pos)))
    w.do(lambda: pump(b))
    check("Bob alone sees nobody (Lex has no heartbeat yet)", bob_out == [], bob_out)

    # --unit-id 1: nothing, however present the peer is
    send_hb(a, hb(100, 101, (1000, 0), (0, 0, 0)))
    pump(_args(unit_id="1"))
    check("--unit-id 1 -> presence sends NOTHING (refuses a fixed id)", out == [], out)

    pump(a)
    ids = [(m, u) for m, u, _ in out]
    check("Lex pump -> ONE 0x1006 addressed to Bob's charid 8",
          ids == [(0x1006, 8)], ids)
    d = decode_add(out[0][2]) if out else {}
    check("...record: count 1, obj 8, type 0 (Avatar), mask1 1, sub 0x2FF",
          (d.get("count"), d.get("obj"), d.get("type"), d.get("mask1"),
           d.get("sub")) == (1, 8, 0, 1, 0x2FF), d)
    idt = d.get("ident", {})
    check("...identity is Bob's stored character (name, sex, looks, nation 2)",
          (idt.get("name"), idt.get("sex"), idt.get("look1"), idt.get("f24"),
           idt.get("look2"), idt.get("look3"), idt.get("look4"), idt.get("force"))
          == ("Bob", 1, 2, 5, 1, 2, 3, 2), idt)
    check("...stored form 0 is served as HUM 0x200 (like the self record)",
          idt.get("f28") == feworld.SELF_FORM_HUM, idt.get("f28"))
    check("...position = Bob's last heartbeat, facing (0,0,1) never zero",
          near(d.get("pos", ()), bob_pos) and d.get("facing") == (0.0, 0.0, 1.0),
          (d.get("pos"), d.get("facing")))
    check("...mask2 5, mask3 0 (gear off), record fully consumed",
          (d.get("mask2"), d.get("mask3"), d.get("done")) == (5, 0, True), d)
    out[:] = []

    # Bob's pump now sees Lex
    w.do(lambda: pump(b))
    bd = decode_add(bob_out[0][2]) if bob_out else {}
    check("Bob pump -> 0x1006 for Lex (obj 7, name Lex, nation 1)",
          [(m, u) for m, u, _ in bob_out] == [(0x1006, 7)]
          and bd.get("ident", {}).get("name") == "Lex"
          and bd.get("ident", {}).get("force") == 1, bob_out)
    bob_out[:] = []

    pump(a)
    check("no new heartbeat -> nothing", out == [], out)

    # Bob walks
    bob_pos2 = (12.0, 20.5, -28.0)
    w.do(lambda: send_hb(b, hb(950, 951, (1000, 250), bob_pos2, state=0, tail=77)))
    send_hb(a, hb(130, 131, (1000, 0), (0, 0, 0)))
    pump(a)
    mv = decode_move(out[0][2]) if out and out[0][0] == 0x2023 else None
    check("Bob moved -> ONE 0x2023 addressed to 8",
          [(m, u) for m, u, _ in out] == [(0x2023, 8)], out)
    # KEY: the sender's own state and tail ride VERBATIM (measured on prod: every
    # real record carries tail 0x00080000, and forcing 0 made peers SNAP)
    # that heartbeat was sent with tail=77 on purpose: an arbitrary value
    # arriving unchanged is what proves the relay does not force its own
    check("...action 0, and the sender's own state and tail ride VERBATIM",
          mv is not None and (mv["act"], mv["state"], mv["tail"]) == (0, 0, 77),
          mv)
    forced = fepresence.relay_body(
        hb(1, 2, (1000, 0), (1.0, 2.0, 3.0), state=0, tail=0x00080000),
        hb(9, 9, (1000, 0), (0.0, 0.0, 0.0)),
        _args(presence_move_tail="0", presence_move_state="1"))
    mvf = decode_move(forced)
    check("--presence-move-tail/-state force the old zeros back for an A/B",
          mvf["tail"] == 0 and mvf["state"] == 1, mvf)
    # --presence-clock map: the sender's timestamp is moved into the
    # receiver's clock by ONE offset, so the first sample lands exactly on the
    # receiver's own reading and later ones keep the spacing they were sent
    # with -- which is what the client's own interpolation reads.
    check("...the timestamp is mapped into the receiver's clock (A 0, B 131)",
          mv is not None and mv["ticks"] == (0, 131), mv)
    check("...Bob's speed pair and position, verbatim",
          mv is not None and mv["spd"] == (1000, 250) and near(mv["pos"], bob_pos2),
          mv)
    out[:] = []

    w.do(lambda: send_hb(b, hb(990, 991, (1000, 250), bob_pos2)))
    pump(a)
    check("same speed + position again -> nothing (relay on CHANGE only)", out == [], out)

    bob_pos3 = (15.0, 20.5, -25.0)
    w.do(lambda: send_hb(b, hb(1000, 1001, (0, 0), bob_pos3)))
    pump(a)
    mv = decode_move(out[0][2]) if out else None
    # WARNING: NO FLOOR any more: the speed pair drives the RUN ANIMATION, so
    # forcing 1.0 on a stopped peer is what made them run on the spot
    check("a stopped peer's zero speed pair rides verbatim (no run animation)",
          mv is not None and mv["spd"] == (0, 0), mv)
    out[:] = []

    # WARNING: THE SUB-UNIT HOLD (live 2026-09-11, "mostly running in place"): the
    # mover discards a target under 1.0u from where the unit stands, but the
    # speed pair is written first -- so a relayed dribble animates and travels
    # nowhere. Hold until --presence-move-min, then send one target.
    near_pos = (bob_pos3[0] + 0.4, bob_pos3[1], bob_pos3[2])
    w.do(lambda: send_hb(b, hb(1002, 1003, (1000, 0), near_pos)))
    pump(a)
    check("a 0.4u step is HELD, not relayed (it would be discarded anyway)",
          out == [], out)
    near2 = (bob_pos3[0] + 0.8, bob_pos3[1], bob_pos3[2])
    w.do(lambda: send_hb(b, hb(1004, 1005, (1000, 0), near2)))
    pump(a)
    check("...and so is the next one, 0.8u from the last TARGET", out == [], out)
    far_pos = (bob_pos3[0] + 1.8, bob_pos3[1], bob_pos3[2])
    w.do(lambda: send_hb(b, hb(1006, 1007, (1000, 0), far_pos)))
    pump(a)
    mv = decode_move(out[0][2]) if out else None
    check("...then 1.8u crosses --presence-move-min and ONE target goes out",
          [(m, u) for m, u, _ in out] == [(0x2023, 8)]
          and mv is not None and near(mv["pos"], far_pos), out)
    out[:] = []

    # and a peer who STOPS inside the threshold still ends up in the right spot
    rest = (far_pos[0] + 0.5, far_pos[1], far_pos[2])
    w.do(lambda: send_hb(b, hb(1008, 1009, (0, 0), rest)))
    pump(a)
    check("a 0.5u drift is held while it is fresh", out == [], out)
    w.do(lambda: send_hb(b, hb(1010, 1011, (0, 0), rest)))
    t_now = time.monotonic() + 1.0          # past --presence-settle-ms 700
    pump(a, now=t_now)
    mv = decode_move(out[0][2]) if out else None
    check("...and sent once they have stopped there (the settle)",
          mv is not None and near(mv["pos"], rest), out)
    out[:] = []
    bob_pos3 = rest

    # a door / return to base
    far = (bob_pos3[0] + 100.0, 20.5, bob_pos3[2])
    w.do(lambda: send_hb(b, hb(1010, 1011, (1000, 0), far)))
    pump(a)
    ids = [(m, u) for m, u, _ in out]
    d = decode_add(out[1][2]) if len(out) > 1 else {}
    check("a 100-unit jump -> 0x1004 then 0x1006 at the new spot (both to 8)",
          ids == [(0x1004, 8), (0x1006, 8)] and near(d.get("pos", ()), far), ids)
    check("...0x1004 is header-only", out and out[0][2] == b"", out[:1])
    out[:] = []

    # a different ROOM of the same field
    w.do(lambda: feworld._SESSION.__setitem__("room", 15))
    w.do(lambda: (send_hb(b, hb(1020, 1021, (1000, 0), far)), pump(b)))
    pump(a)
    check("Bob steps into room 15 -> 0x1004 8 (field AND room must match)",
          [(m, u) for m, u, _ in out] == [(0x1004, 8)], out)
    out[:] = []
    w.do(lambda: feworld._SESSION.__setitem__("room", -1))
    w.do(lambda: (send_hb(b, hb(1030, 1031, (1000, 0), far)), pump(b)))
    pump(a)
    check("...and back outdoors -> 0x1006 8 again",
          [(m, u) for m, u, _ in out] == [(0x1006, 8)], out)
    out[:] = []
    bob_out[:] = []

    # Field Out: 0x2017 -> gone at once, whatever the heartbeat does
    w.do(lambda: feworld.ext_dispatch(ctx_of(b), 0x2017, struct.pack(">H", 0x2017)))
    w.do(lambda: send_hb(b, hb(1040, 1041, (1000, 0), far)))
    pump(a)
    check("Bob sends 0x2017 (Field Out) -> 0x1004 8 even with a fresh heartbeat",
          [(m, u) for m, u, _ in out] == [(0x1004, 8)], out)
    out[:] = []
    w.do(lambda: pump(b))
    check("...and Bob's own pump sends nothing while gone", bob_out == [], bob_out)

    # re-entry: 0x2000 clears gone; field_ready drops, then the first heartbeat
    def _reenter():
        feworld.ext_dispatch(ctx_of(b), 0x2000, struct.pack(">HI", 0x2000, 11))
        feworld._SESSION["field_ready"] = False
        pump(b)
        feworld._SESSION["field_ready"] = True
        send_hb(b, hb(1100, 1101, (1000, 0), bob_pos))
        pump(b)
    w.do(_reenter)
    pump(a)
    check("Bob re-enters (0x2000 -> ready) -> 0x1006 8",
          [(m, u) for m, u, _ in out] == [(0x1006, 8)], out)
    check("...and Bob's client gets Lex again (his scene was rebuilt)",
          [(m, u) for m, u, _ in bob_out] == [(0x1006, 7)], bob_out)
    out[:] = []
    bob_out[:] = []

    # another field
    w.do(lambda: feworld._SESSION.__setitem__("field", 12))
    w.do(lambda: (send_hb(b, hb(1110, 1111, (1000, 0), bob_pos)), pump(b)))
    pump(a)
    check("Bob in field 12 -> 0x1004 8 in field 11",
          [(m, u) for m, u, _ in out] == [(0x1004, 8)], out)
    out[:] = []
    w.do(lambda: feworld._SESSION.__setitem__("field", 11))
    w.do(lambda: (send_hb(b, hb(1120, 1121, (1000, 0), bob_pos)), pump(b)))
    pump(a)
    out[:] = []
    bob_out[:] = []

    # staleness: a socket that died without a FIN
    t = w.do(lambda: feworld._SESSION.get("pres_mv_t"))
    pump(a, now=t + 9.0)
    check("Bob silent 9 s (< --presence-stale 10) -> still shown", out == [], out)
    pump(a, now=t + 11.0)
    check("Bob silent 11 s -> 0x1004 8", [(m, u) for m, u, _ in out] == [(0x1004, 8)], out)
    out[:] = []
    w.do(lambda: (send_hb(b, hb(1130, 1131, (1000, 0), bob_pos)), pump(b)))
    pump(a)
    check("...a heartbeat again -> 0x1006 8",
          [(m, u) for m, u, _ in out] == [(0x1006, 8)], out)
    out[:] = []
    bob_out[:] = []

    # Lex leaves and re-enters: the client dropped Bob with the scene, so NO
    # 0x1004 goes out -- and Bob is re-added once Lex is loaded again
    feworld._SESSION["field_ready"] = False
    pump(a)
    check("Lex's own field_ready drops -> nothing sent, the set is forgotten",
          out == [] and feworld._SESSION.get("pres_seen") == {}, out)
    feworld._SESSION["field_ready"] = True
    send_hb(a, hb(200, 201, (1000, 0), (0, 0, 0)))
    pump(a)
    check("...ready again -> 0x1006 8 (no 0x1004 first)",
          [(m, u) for m, u, _ in out] == [(0x1006, 8)], out)
    out[:] = []

    # --presence off
    w.do(lambda: send_hb(b, hb(1140, 1141, (1000, 0), (15.0, 20.5, -25.0))))
    pump(_args(presence="off"))
    check("--presence off -> nothing", out == [], out)

    # Bob hangs up: his thread leaves the room
    w.do(feworld._chat_room_leave)
    pump(a)
    check("Bob's session ends -> 0x1004 8",
          [(m, u) for m, u, _ in out] == [(0x1004, 8)], out)
    out[:] = []
    w.tasks.put(None)
    return a


def edges(a):
    print("\nedges: ids, doubles, gear", file=REPORT)
    out = _out()
    out[:] = []

    # a charid on the server's own object range is never drawn
    w = _Worker()
    w.start()
    w.do(_stub_store)
    w.do(lambda: enter(400, 11))
    w.do(lambda: (send_hb(a, hb(1, 2, (1000, 0), (5, 5, 5))), pump(a)))
    send_hb(a, hb(300, 301, (1000, 0), (0, 0, 0)))
    pump(a)
    check("charid 400 (= --monster-base) -> never drawn", out == [], out)
    w.do(feworld._chat_room_leave)
    w.tasks.put(None)

    # the SAME character on a second session (a reconnect) is not drawn to
    # itself; two sessions of one OTHER charid draw it once, the fresher
    w1, w2 = _Worker(), _Worker()
    w1.start()
    w2.start()
    for w_ in (w1, w2):
        w_.do(_stub_store)
    w1.do(lambda: enter(7, 11))
    w1.do(lambda: (send_hb(a, hb(1, 2, (1000, 0), (1, 1, 1))), pump(a)))
    pump(a)
    check("a second session of MY OWN charid -> not drawn", out == [], out)
    w1.do(feworld._chat_room_leave)
    w2.do(lambda: enter(8, 11))
    w2.do(lambda: (send_hb(a, hb(1, 2, (1000, 0), (2, 2, 2))), pump(a)))
    w3 = _Worker()
    w3.start()
    w3.do(_stub_store)
    w3.do(lambda: enter(8, 11))
    w3.do(lambda: (send_hb(a, hb(1, 2, (1000, 0), (3, 3, 3))), pump(a)))
    # monotonic() ticks at ~15 ms on Windows: make w3 unambiguously fresher
    w3.do(lambda: feworld._SESSION.__setitem__(
        "pres_mv_t", feworld._SESSION["pres_mv_t"] + 1.0))
    send_hb(a, hb(310, 311, (1000, 0), (0, 0, 0)))
    pump(a)
    d = decode_add(out[0][2]) if out else {}
    check("two sessions of charid 8 -> ONE 0x1006, the fresher one's spot",
          [(m, u) for m, u, _ in out] == [(0x1006, 8)] and near(d.get("pos", ()), (3, 3, 3)),
          out)
    out[:] = []
    for w_ in (w2, w3):
        w_.do(feworld._chat_room_leave)
        w_.tasks.put(None)
    pump(a)
    out[:] = []

    # worn gear, opt-in
    g = _Worker()
    g.start()
    g.do(_stub_store)
    STORE[8] = {"items": [[8001, 0x300, 0, 1], [8002, 0x214, 0, 1]],
                "equip": [[1, 8001], [3, 8002]]}   # (slot TYPE, uid)
    ga = _args(presence_gear="on")
    g.do(lambda: enter(8, 11))
    g.do(lambda: (send_hb(ga, hb(1, 2, (1000, 0), (4, 4, 4))), pump(ga)))
    send_hb(a, hb(320, 321, (1000, 0), (0, 0, 0)))
    pump(a)
    d = decode_add(out[0][2]) if out else {}
    want_m3, _body, _desc = g.do(lambda: feworld.worn_gear_block(
        feworld.stored_equip_rows(ga)))
    check("--presence-gear on -> mask3 carries the stored worn rows",
          d.get("mask3") == want_m3 and want_m3 != 0 and d.get("done"),
          (d.get("mask3"), want_m3, d.get("worn")))
    check("...each worn row is [u32 uid][u32 2][u16 item]",
          all(m == 2 for _i, _u, m, _n in d.get("worn", [])) and
          any(u == 8001 and n == 0x300 for _i, u, _m, n in d.get("worn", [])),
          d.get("worn"))
    out[:] = []
    g.do(feworld._chat_room_leave)
    g.tasks.put(None)

    # a submask bit with no packer is masked off rather than desynchronising
    c = fepresence.build_card(_args(presence_submask=0x2FF | 0x400), (11, -1))
    check("submask bit 0x400 (no packer) is dropped from the card",
          c is not None and c["sub"] == 0x2FF, c and hex(c["sub"]))


def paced(a):
    """PACED MODE (the shipped default): a client reports every ~2.5 s and an
    accepted target SNAPS, so the server walks its own idea of the peer toward
    the report in steps that clear the mover's 1.0u discard."""
    print("\npaced movement", file=REPORT)
    out = _out()
    out[:] = []
    p = _args(presence_interp="on", presence_step_min=1.05)
    # earlier blocks left peers spawned on THIS session; start clean so the
    # first paced step is measured from Bob's spawn, not from an old position
    feworld._SESSION["pres_seen"] = {}
    feworld._SESSION["pres_at"] = None
    w = _Worker()
    w.start()
    w.do(_stub_store)
    w.do(lambda: enter(8, 11))
    start = (0.0, 20.0, 0.0)
    w.do(lambda: (send_hb(p, hb(1, 2, (1300, 0), start)), pump(p)))
    send_hb(p, hb(500, 501, (0, 0), (0.0, 20.0, 0.0)))
    pump(p)                                   # spawns Bob at `start`
    out[:] = []

    # two reports 2.5 s apart, 6 units apart -- what a walking client sends
    t0 = time.monotonic()
    w.do(lambda: send_hb(p, hb(3, 4, (1300, 0), (0.0, 20.0, 0.0))))
    pump(p, now=t0)
    out[:] = []
    w.do(lambda: send_hb(p, hb(5, 6, (1300, 0), (6.0, 20.0, 0.0))))
    pump(p, now=t0 + 2.5)
    out[:] = []

    steps, _faces = [], []
    for i in range(1, 15):                    # idle ticks, 250 ms apart
        pump(p, now=t0 + 2.5 + i * 0.25)
        for m, u, body in out:
            if m == 0x2023 and u == 8:
                steps.append(decode_move(body)["pos"])
            if m == 0x1006 and u == 8:
                _faces.append((m, u, body))
        out[:] = []
    check("paced mode emits SEVERAL targets between two reports",
          len(steps) >= 2, steps)
    faces = [b for m, u, b in _faces if m == 0x1006 and u == 8]
    if faces:
        r = Reader(faces[-1])
        r.take("H"), r.take("I"), r.take("B")
        m1, m2 = r.take("I"), r.take("I")
        fx, fy, fz = r.take("fff")
        check("...each turn is a facing-only 0x1006 (mask1 0, mask2 4)",
              (m1, m2) == (0, 4) and r.take("I") == 0 and r.done(), (m1, m2))
        check("...pointing the way they walk, flat and never all zero",
              abs(fx - 1.0) < 0.01 and fy == 0.0 and abs(fz) < 0.01,
              (fx, fy, fz))
    else:
        check("a facing update rides before the step", False, "none sent")
    gaps = [_d(steps[i - 1], steps[i]) if i else _d((0.0, 20.0, 0.0), steps[0])
            for i in range(len(steps))]
    check("...each step clears the mover's 1.0u discard",
          all(g >= 1.0 - 1e-6 for g in gaps), gaps)
    check("...and none overshoots the peer's reported position",
          all(s[0] <= 6.0 + 1e-6 for s in steps), steps)
    check("...arriving there and then stopping",
          near(steps[-1], (6.0, 20.0, 0.0), 0.1) , steps[-1])
    out[:] = []
    w.do(feworld._chat_room_leave)
    w.tasks.put(None)
    pump(a)
    out[:] = []


def speeds():
    """The relayed speed pair is timed to the REPORT INTERVAL: the sender's
    own value is a multiplier on the engine's run speed, so relaying it makes
    the avatar sprint to the waypoint and park (static, 2026-09-12)."""
    print("\nthe timed speed", file=REPORT)
    a = _args(presence_speed_scale=3.0)
    check("6 units over 10 s at k=3 -> 0.20 (a walk, not a sprint)",
          abs(fepresence.timed_speed(6.0, 10.0, a) - 0.2) < 1e-6,
          fepresence.timed_speed(6.0, 10.0, a))
    check("the same distance in 2 s is five times the speed",
          abs(fepresence.timed_speed(6.0, 2.0, a) - 1.0) < 1e-6,
          fepresence.timed_speed(6.0, 2.0, a))
    check("a bigger scale means a smaller multiplier",
          fepresence.timed_speed(6.0, 10.0, _args(presence_speed_scale=6.0))
          < fepresence.timed_speed(6.0, 10.0, a))
    # WARNING: standing still must SAY zero: "no opinion" left the last walking
    # speed on the unit and the peer ran on the spot forever
    check("standing still is speed ZERO, not silence",
          fepresence.timed_speed(0.0, 10.0, a) == 0.0
          and fepresence.timed_speed(0.02, 0.5, a) == 0.0)
    check("...but a bad interval still asks for nothing",
          fepresence.timed_speed(6.0, 0.0, a) is None)
    check("it is clamped to something the wire can carry",
          fepresence.timed_speed(9999.0, 0.1, a) <= 3.0)


def _d(x, y):
    return sum((p - q) ** 2 for p, q in zip(x, y)) ** 0.5


def minimap(a):
    """0x2080 -> 0x1123: one dot per player standing here (2026-09-11, from
    "neither sees the other on the minimap")."""
    print("\nthe minimap dots", file=REPORT)
    out = _out()
    out[:] = []
    w = _Worker()
    w.start()
    w.do(_stub_store)
    w.do(lambda: enter(8, 11))
    bob_pos = (25.0, 20.0, -40.0)
    w.do(lambda: (send_hb(a, hb(1, 2, (1000, 0), bob_pos)), pump(a)))
    send_hb(a, hb(400, 401, (1000, 0), (0.0, 20.0, 0.0)))
    pump(a)
    out[:] = []

    handled = feworld.ext_dispatch(ctx_of(a), 0x2080, struct.pack(">H", 0x2080))
    check("0x2080 is answered by fepresence", handled is True)
    ids = [(m, u) for m, u, _ in out]
    check("...with 0x1123", [m for m, _u in ids] == [0x1123], ids)
    body = out[0][2] if out else b""
    count = struct.unpack_from(">H", body, 0)[0] if len(body) >= 2 else -1
    check("...one row for Bob, and not for me", count == 1, (count, body.hex()))
    gx, gz, cls, cid = struct.unpack_from(">BBBI", body, 2)
    check("...the row is [u8 gx][u8 gz][u8 class|side][u32 charid]",
          (gx, gz, cid) == (feworld.world_to_grid(bob_pos[0]),
                            feworld.world_to_grid(bob_pos[2]), 8),
          (gx, gz, hex(cls), cid))
    check("...the top bit is set: Bob's nation (2) is not mine (1)",
          cls & 0x80 and (cls & 0x1F) == (CHARS[8].get("look1") or 0),
          hex(cls))
    out[:] = []

    # my own dot, the cheap oracle for the grid guess
    ma = _args(presence_map_self="on")
    send_hb(ma, hb(410, 411, (1000, 0), (0.0, 20.0, 0.0)))
    feworld.ext_dispatch(ctx_of(ma), 0x2080, struct.pack(">H", 0x2080))
    body = out[0][2] if out else b""
    check("--presence-map-self on -> my own row rides too",
          struct.unpack_from(">H", body, 0)[0] == 2
          and struct.unpack_from(">BBBI", body, 9)[3] == 7,
          body.hex())
    out[:] = []

    # the operator probe and the off switches win
    check("--presence-map off -> declined (feworld's count 0 answers)",
          feworld.ext_dispatch(ctx_of(_args(presence_map="off")), 0x2080,
                               struct.pack(">H", 0x2080)) is False)
    check("--distribution-rows set -> declined (the probe wins)",
          feworld.ext_dispatch(ctx_of(_args(distribution_rows=[(1, 2, 3, 4)])),
                               0x2080, struct.pack(">H", 0x2080)) is False)
    check("--distribution off -> declined",
          feworld.ext_dispatch(ctx_of(_args(distribution="off")), 0x2080,
                               struct.pack(">H", 0x2080)) is False)
    check("--unit-id 1 -> declined",
          feworld.ext_dispatch(ctx_of(_args(unit_id="1")), 0x2080,
                               struct.pack(">H", 0x2080)) is False)
    check("!presence map off flips it live",
          fepresence.gm(ctx_of(a), "!presence map off") is True
          and a.presence_map == "off")
    a.presence_map = "on"
    out[:] = []
    w.do(feworld._chat_room_leave)
    w.tasks.put(None)
    pump(a)
    out[:] = []


def raw_relay(a):
    """--presence-relay raw (THE DEFAULT since 2026-09-12): every 0x2023 a
    peer sends reaches the others VERBATIM, on their own thread, re-addressed
    to the peer's charid, with only the 28-byte action-0 record's 64-bit
    clock moved into the receiver's clock. Static basis: the client's own
    waypoint queue (0x0515B870/0x0515BCE0) and its action-5 jump arc
    (0x0503EEF0 -> 0x04FEB210), read 2026-09-12 -- see fepresence.py."""
    print("\nraw relay (the default): verbatim, on the receiver's thread",
          file=REPORT)
    import argparse
    ap = argparse.ArgumentParser()
    fepresence.add_args(ap)
    d = ap.parse_args([])
    check("argparse defaults: relay raw, actions all, clock arrival",
          (d.presence_relay, d.presence_relay_actions, d.presence_clock)
          == ("raw", "all", "arrival"),
          (d.presence_relay, d.presence_relay_actions, d.presence_clock))
    _stub_store()
    ra = _args(presence_relay="raw")
    rb = _args(presence_relay="raw")
    out = _out()
    out[:] = []
    for k in ("pres_seen", "pres_at", "pres_mv", "pres_mv_n", "pres_mv_t",
              "pres_mv_b", "pres_mv_arr", "pres_raw_n", "pres_raw_early"):
        feworld._SESSION.pop(k, None)
    enter(7, 11)
    w = _Worker()
    w.start()
    w.do(_stub_store)
    w.do(lambda: enter(8, 11))
    bob_out = w.do(_out)
    w.do(lambda: [feworld._SESSION.pop(k, None) for k in
                  ("pres_seen", "pres_at", "pres_mv", "pres_mv_n", "pres_mv_t",
                   "pres_mv_b", "pres_mv_arr", "pres_raw_n", "pres_raw_early")])
    ctx = ctx_of(ra)
    # ext_pump runs EVERY extension's pump, so other modules' pushes (EXP,
    # campaign...) land in `out` too; only presence's three ids are judged
    po = lambda: [(m, u, b) for m, u, b in out if m in (0x1006, 0x2023, 0x1004)]

    # Lex's own clock sample: B = 100000 at "now"; Bob's first heartbeat
    send_hb(ra, hb(0, 100000, (1300, 0), (0, 0, 0)))
    w.do(lambda: send_hb(rb, hb(0, 555000, (1300, 0), (10.0, 20.5, -30.0))))
    w.do(lambda: pump(rb))                  # Bob builds his card, his thread
    # a heartbeat that arrives BEFORE Lex draws Bob is dropped, not queued
    feworld.ext_pump(ctx)
    early = feworld._SESSION.get("pres_raw_early", 0)
    adds = [(m, u, b) for m, u, b in po() if m == 0x1006]
    check("Lex: the pump ADDs Bob (0x1006 to 8) at his latest heartbeat",
          [(m, u) for m, u, _ in adds] == [(0x1006, 8)]
          and near(decode_add(adds[0][2]).get("pos", ()) if adds else (),
                   (10.0, 20.5, -30.0)), po())
    check("...a record posted before the ADD is counted early, not sent",
          early == 1 and not [m for m, _, _ in po() if m == 0x2023],
          (early, po()))
    out[:] = []

    # Bob walks: state 0, spd (1300, 250), tail 0x00080000 -> VERBATIM
    t0 = time.monotonic()
    w.do(lambda: send_hb(rb, hb(0, 555400, (1300, 250), (12.0, 20.5, -28.0),
                                state=0, tail=0x00080000)))
    feworld.ext_pump(ctx)
    mv = decode_move(po()[0][2]) if po() and po()[0][0] == 0x2023 else None
    check("Bob's heartbeat -> ONE 0x2023 to obj 8 on Lex's thread",
          [(m, u) for m, u, _ in po()] == [(0x2023, 8)], po())
    check("...speed pair, position, state and tail ride VERBATIM",
          mv is not None and mv["act"] == 0 and mv["state"] == 0
          and mv["spd"] == (1300, 250) and near(mv["pos"], (12.0, 20.5, -28.0))
          and mv["tail"] == 0x00080000, mv)
    el = (time.monotonic() - t0) * 1000.0
    check("...A:B = LEX's clock at arrival (100000 + ms since his sample), A 0",
          mv is not None and mv["ticks"][0] == 0
          and 100000 <= mv["ticks"][1] <= 100000 + el + 1500, (mv, el))
    check("...the pump's own state follows (pos = the relayed target)",
          near(feworld._SESSION["pres_seen"][8]["pos"], (12.0, 20.5, -28.0)))
    out[:] = []

    # the same position again with state 1 = the STOP: never coalesced
    w.do(lambda: send_hb(rb, hb(0, 555800, (1300, 250), (12.0, 20.5, -28.0),
                                state=1, tail=0x00080000)))
    feworld.ext_pump(ctx)
    mv = decode_move(po()[0][2]) if po() and po()[0][0] == 0x2023 else None
    check("a state-1 record at the SAME spot still goes (the stop marker)",
          mv is not None and mv["state"] == 1 and near(mv["pos"], (12.0, 20.5, -28.0)),
          (mv, po()))
    out[:] = []

    # a jump: 28-byte ACTION 5 [5][start][end][from i16x3][to i16x3][flags]
    arc = (struct.pack(">III", 5, 700000, 701350)
           + struct.pack(">hhh", 120, 205, -280)
           + struct.pack(">hhh", 150, 205, -250)
           + struct.pack(">I", 0x00080400))
    w.do(lambda: send_hb(rb, arc))
    feworld.ext_pump(ctx)
    check("an action-5 jump arc is relayed BYTE-FOR-BYTE to obj 8 (no clock "
          "rewrite: the client re-bases the pair itself, 0x04FEB3F0)",
          po() == [(0x2023, 8, arc)], po())
    out[:] = []

    # the short form: action 0x11 + one byte (airborne flag, flying forms)
    short = struct.pack(">IB", 0x11, 2)
    w.do(lambda: send_hb(rb, short))
    feworld.ext_pump(ctx)
    check("a 5-byte action-0x11 record is relayed verbatim to obj 8",
          po() == [(0x2023, 8, short)], po())
    out[:] = []

    # a CAST (0x1032 SKILL USE, 40 B) handed over by feprog.on_cast ->
    # relay_inner: verbatim, its own message id, under the caster's charid
    cast = (struct.pack(">IIBIIHBII", 520936, 522536, 1, 1, 40, 270, 1, 0, 3001)
            + struct.pack(">fff", -146.75, 36.79, -0.02))
    n = w.do(lambda: fepresence.relay_inner(ctx_of(rb), struct.pack(">H", 0x1032) + cast))
    feworld.ext_pump(ctx)
    casts = [(m, u, b) for m, u, b in out if m == 0x1032]
    check("a peer's 0x1032 cast record is relayed BYTE-FOR-BYTE as 0x1032 to obj 8",
          n == 1 and casts == [(0x1032, 8, cast)], (n, casts))
    out[:] = []
    n = w.do(lambda: fepresence.relay_inner(
        ctx_of(_args(presence_relay="raw", presence_relay_casts="off")),
        struct.pack(">H", 0x1032) + cast))
    feworld.ext_pump(ctx)
    check("--presence-relay-casts off: the cast is not posted",
          n == 0 and not [m for m, _, _ in out if m == 0x1032], out)
    out[:] = []

    # --presence-relay-actions move: only the 28-byte action 0 travels
    w.do(lambda: send_hb(_args(presence_relay="raw",
                               presence_relay_actions="move"), arc))
    feworld.ext_pump(ctx)
    check("--presence-relay-actions move: the arc is NOT posted", po() == [], po())
    out[:] = []

    # a peer in ANOTHER field is not a recipient (the ext_post predicate)
    w.do(lambda: enter(8, 12))
    n = w.do(lambda: send_hb(rb, hb(0, 556000, (1300, 0), (1, 1, 1))))
    feworld.ext_pump(ctx)
    check("a heartbeat from a peer in field 12 reaches nobody in field 11",
          not [m for m, _, _ in po() if m == 0x2023], po())
    out[:] = []
    w.do(lambda: enter(8, 11))

    # paced mode ignores the posts even if one is queued
    w.do(lambda: send_hb(rb, hb(0, 556400, (1300, 0), (13.0, 20.5, -27.0))))
    saved_mode = ra.presence_relay
    ra.presence_relay = "paced"
    feworld.ext_pump(ctx)
    relayed = [m for m, _, _ in po() if m == 0x2023]
    ra.presence_relay = saved_mode
    check("!presence relay paced: a queued raw record is dropped, not sent "
          "twice", relayed == [], po())
    out[:] = []

    # no own sample yet -> the clock cannot be mapped -> nothing is sent
    saved = (feworld._SESSION.pop("pres_mv_b", None),
             feworld._SESSION.pop("pres_mv_arr", None))
    w.do(lambda: send_hb(rb, hb(0, 556800, (1300, 0), (14.0, 20.5, -26.0))))
    feworld.ext_pump(ctx)
    check("no own clock sample yet -> the action-0 record waits (not sent "
          "with a foreign clock)", not [m for m, _, _ in po() if m == 0x2023], po())
    feworld._SESSION["pres_mv_b"], feworld._SESSION["pres_mv_arr"] = saved
    out[:] = []

    # rebase_clock itself: arrival, map, now, and a synced (A != 0) record
    s = {"pres_mv_b": 5000, "pres_mv_arr": 100.0}
    body = hb(0, 999, (1000, 0), (1, 2, 3))
    r = decode_move(fepresence.rebase_clock(body, 101.25, s, "arrival"))
    check("rebase_clock arrival: B = own B + (arrival - own arrival) ms",
          r["ticks"] == (0, 6250), r)
    r = decode_move(fepresence.rebase_clock(body, 101.25, s, "map"))
    check("rebase_clock map: B = own B (one raw offset)", r["ticks"] == (0, 5000), r)
    r = decode_move(fepresence.rebase_clock(body, 101.25, s, "now", now=102.0))
    check("rebase_clock now: B = own B + (now - own arrival) ms",
          r["ticks"] == (0, 7000), r)
    # WARNING: this used to assert that a synced record (A != 0) passes UNTOUCHED --
    # which is the bug: into an unsynced receiver that stamp is ~56 years in
    # its future. The target is the receiver's clock (fe_clock_test has the
    # synced-receiver half).
    synced = hb(1, 999, (1000, 0), (1, 2, 3))
    r = decode_move(fepresence.rebase_clock(synced, 101.25, s, "arrival"))
    check("rebase_clock maps a synced SENDER into this receiver's clock",
          r["ticks"] == (0, 6250), r)

    w.do(feworld._chat_room_leave)
    w.tasks.put(None)
    out[:] = []
    for k in ("pres_seen", "pres_at"):
        feworld._SESSION.pop(k, None)


def serve_loop(a):
    """One real 0x2023 through _serve_loop: the shadow ran AND feworld's own
    arm still ran (its telemetry counter and cpos moved)."""
    print("\nthe real _serve_loop", file=REPORT)

    class _Conn:
        def settimeout(self, t):
            pass

        def getpeername(self):
            return ("127.0.0.1", 1)
    full = types.SimpleNamespace(**vars(_args()))
    for k, v in dict(gmcmd=None, gmcmd_file=None, chat_relay="off",
                     move_authority="off", unit_speed_repeat="off", npc=[],
                     npc_walk=None, npc_base=1000, read_window=1.0,
                     probe_on_auth=False, capture_only=False).items():
        setattr(full, k, v)
    saved = (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap)
    frames = [(0x30, struct.pack(">H", 0x2023) + hb(5, 6, (1000, 0), (7.0, 8.0, 9.0)))]
    fenet.recv_frame = lambda c: frames.pop(0) if frames else None
    fenet.bf_decrypt = lambda s, b, m, be: b
    fenet.traffic_unwrap = lambda p: (1, p)
    before = feworld._SESSION.get("telemetry", 0)
    n_before = feworld._SESSION.get("pres_mv_n", 0)
    err = None
    try:
        feworld._serve_loop(_Conn(), full, None, None, "ecb", False)
    except Exception as e:                              # noqa: BLE001
        err = e
    finally:
        fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap = saved
    check("_serve_loop ran the 0x2023 without raising", err is None, repr(err))
    check("...fepresence recorded it (pres_mv_n +1)",
          feworld._SESSION.get("pres_mv_n", 0) == n_before + 1)
    check("...AND feworld's own arm ran (telemetry +1, cpos = 7,8,9)",
          feworld._SESSION.get("telemetry", 0) == before + 1
          and near(feworld._SESSION.get("cpos", ()), (7.0, 8.0, 9.0)),
          (feworld._SESSION.get("telemetry"), feworld._SESSION.get("cpos")))


def hide():
    """--hide-presence (2026-10-01): a HIDDEN peer leaves enemy screens only,
    and only with the flag on (world/status.py hidden_from)."""
    from world import status
    print("\nHide: a hidden peer is dropped from ENEMY screens (--hide-presence)",
          file=REPORT)
    now = time.monotonic()
    mv = hb(1, 2, (1000, 0), (1.0, 2.0, 3.0))
    me = {"in_field": True, "field_ready": True, "charid": 7, "field": 11,
          "room": -1, "pres_card": {"force": 1}}
    peer = {"in_field": True, "field_ready": True, "charid": 8, "field": 11,
            "room": -1, "pres_card": {"force": 2, "key": (11, -1)},
            "pres_mv": mv, "pres_mv_t": now, "pres_alive_t": now}
    ally = dict(peer, charid=9, pres_card={"force": 1, "key": (11, -1)})
    real = feworld.ext_sessions
    feworld.ext_sessions = lambda: [{"key": -1, "session": peer, "name": "p"},
                                    {"key": -2, "session": ally, "name": "a"}]
    try:
        on = _args(hide_presence="on")
        status.apply(peer, status.good_specs(140))
        status.apply(ally, status.good_specs(140))
        got = fepresence.visible_peers(me, (11, -1), now, on)
        check("--hide-presence on: the hidden ENEMY (8) is not drawn, the "
              "hidden ALLY (9) still is", sorted(got) == [9], sorted(got))
        got = fepresence.visible_peers(me, (11, -1), now, _args())
        check("--hide-presence off (the default): both drawn, as before",
              sorted(got) == [8, 9], sorted(got))
        status.clear(peer)
        got = fepresence.visible_peers(me, (11, -1), now, on)
        check("Hide over: the enemy is back", sorted(got) == [8, 9], sorted(got))
    finally:
        feworld.ext_sessions = real


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
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
        seam()
        a = two_players()
        edges(a)
        speeds()
        paced(a)
        minimap(a)
        raw_relay(a)
        serve_loop(a)
        hide()
    finally:
        if quiet:
            sys.stdout = _saved_out
        feworld.send_world_frame, fenet.bf_encrypt, fenet.traffic_wrap = saved_send
    if FAILS:
        print("[fe_presence_test] %d FAILED: %s" % (len(FAILS), FAILS))
        sys.exit(1)
    print("[fe_presence_test] OK")
