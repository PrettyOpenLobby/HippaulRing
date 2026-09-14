#!/usr/bin/env python3
"""fe_party_test.py -- feparty.py (Fantasy Earth PARTY) end to end, no client.

Run from the repo root:  python tools/fe_party_test.py

Every reply feparty builds is decoded here with the EXACT primitive sequence
the client's arm makes (febody.py + the helper disassembly, VAs in
feparty.py's docstring), then Reader.done() asserts nothing is left on the
stream -- a body one field long or short does not error on the client, it
desynchronises everything after it.

    0x112F 0x1131 0x1133 0x1136 0x1137 0x113A 0x113C 0x113D   header-only
    0x1130 0x1132 0x1138 0x113B                                u32
    0x1135                                                     u32
    0x112E (to the target)                                     u32 u32
    0x1134   u8 N, then N x { u32 ChrID, cstr name, u8 Class }   (LOOP, 0x050515c0)
    0x1139   u8 N, then N x { u32, u8, u16, u16, u32, u32 }      (LOOP, 0x05051c60)
    0x208A   cstr speaker, cstr text                             (chat arm 0x05053cbe)

Part 1 drives ONE session through the real feworld._serve_loop (crypto
stubbed, send_world_frame captured): the phantom flow, the !party verbs, the
refusals. Part 2 fakes a second and third session in feworld's chat room
(the same structure ext_post fans out over) and walks the cross-session
invite -> judge -> add -> chat -> leave/decline/timeout/cancel paths, running
each relay on the receiver's ctx the way ext_pump does.
"""
import os
import struct
import sys
import threading
import time
import types
import queue

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import fenet     # noqa: E402
import feworld   # noqa: E402
import feparty   # noqa: E402

feworld.load_extensions(["feparty"])   # registers feparty only

OUT = sys.__stdout__
CHECKS = []


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-62s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label + (": " + detail if detail else ""))


class Reader:
    """FE's stream primitives, by the byte count each one advances
    (0x5045dc0 u8 +1, 0x5045ec0 u16 +2, 0x5045e60 u32 +4, 0x5045f50 cstr)."""

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

    def cstr(self):
        e = self.b.index(b"\0", self.i)
        v = self.b[self.i:e]
        self.i = e + 1
        return v.decode("cp932")

    def done(self):
        assert self.i == len(self.b), (
            "%d of %d bytes consumed -- the client would leave %r on the "
            "stream" % (self.i, len(self.b), self.b[self.i:]))


def read_add(body):
    """0x1134 the way 0x050515c0 reads it."""
    r = Reader(body)
    n = r.u8()
    rows = [(r.u32(), r.cstr(), r.u8()) for _ in range(n)]
    r.done()
    return rows


def read_status(body):
    """0x1139 the way 0x05051c60 reads it."""
    r = Reader(body)
    n = r.u8()
    rows = [(r.u32(), r.u8(), r.u16(), r.u16(), r.u32(), r.u32())
            for _ in range(n)]
    r.done()
    return rows


def read_u32(body):
    r = Reader(body)
    v = r.u32()
    r.done()
    return v


def read_none(body):
    Reader(body).done()
    return True


CHARS = {7: {"charid": 7, "name": "Alice"},
         8: {"charid": 8, "name": "Bob"},
         9: {"charid": 9, "name": "Carol"}}


def _stub_store():
    feworld._store_char_field = lambda a, k, v: True
    feworld._load_char_field = lambda a, k, d=None: d
    feworld._self_char = lambda a: CHARS.get(feworld._SESSION.get("charid"))
    feworld.self_class_id = lambda a: 3
    feworld.blacklist_rows = lambda a: []


def _args(**kw):
    a = dict(unit_id="0", seq_mode="count", world_prefix=4, read_window=1.0,
             gmcmd=None, gmcmd_file=None, probe_on_auth=False,
             ui_auto="ok", ui_auto_err=0, move_request="off",
             chat_relay="off", chat_echo="off", chat_line=None,
             validate_finish=0, war_start="off", war_clock="off",
             war_deadline_ms=0, blacklist="on",
             npc=["Bob:1:1"], npc_base=1000,
             # the one-client TEST shapes this suite drives; the SHIPPED
             # defaults (phantom off, party-only chat) are checked on their own
             party_phantom="on", party_chat="field")
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


def _capturing(outbox):
    """Context: capture ctx.reply() output as (mid, unit, payload)."""
    class _C:
        def __enter__(self):
            self.saved = (feworld.send_world_frame, fenet.bf_encrypt,
                          fenet.traffic_wrap)
            feworld.send_world_frame = lambda c, mid, body, p: outbox.append(
                (struct.unpack_from(">H", body, 4)[0],
                 struct.unpack_from(">I", body, 0)[0], body[6:]))
            fenet.bf_encrypt = lambda st, body, mode, be: body
            fenet.traffic_wrap = lambda data, seq=1: data
            return self

        def __exit__(self, *e):
            (feworld.send_world_frame, fenet.bf_encrypt,
             fenet.traffic_wrap) = self.saved
    return _C()


def _reset_model():
    with feparty._LOCK:
        feparty._PARTIES.clear()
        feparty._MEMBER_OF.clear()
        feparty._INVITES.clear()


def ids(outbox):
    return [m for m, _, _ in outbox]


# ---------------------------------------------------------------------------
# PART 1 -- one session, the phantom flow and the refusals
# ---------------------------------------------------------------------------
def part1():
    say("part 1: one session (phantom flow, !party verbs, refusals)")
    _stub_store()
    _reset_model()
    feworld._SESSION.clear()
    feworld._SESSION["account"] = "tester"
    feworld._SESSION["charid"] = 7
    feworld._SESSION["in_field"] = True
    feworld._chat_room_join()
    try:
        a = _args()
        out = []
        # invite unit 1000 (our --npc "Bob", no session) -> phantom accept
        _drive(a, [struct.pack(">HI", 0x112E, 1000)], out)
        check("0x112E to a served unit -> 0x112F then 0x1134",
              ids(out) == [0x112F, 0x1134], repr(ids(out)))
        check("0x112F is header-only (arm 0x050518a0 reads nothing)",
              read_none(out[0][2]))
        rows = read_add(out[1][2])
        check("0x1134 N=1 {ChrID, 'Bob' from --npc, Class} decodes as the loop",
              len(rows) == 1 and rows[0][1] == "Bob"
              and rows[0][0] >= 0x7F000000, repr(rows))
        phantom_bob = rows[0][0]

        # !party fake NAME:CLASS
        out[:] = []
        with _capturing(out):
            ctx = feworld.Ctx(_Conn(), None, "ecb", False, a)
            check("!party is taken by feparty's gm hook",
                  feparty.gm(ctx, "!party fake Dave:4"))
        rows = read_add(out[0][2])
        check("!party fake -> 0x1134 N=1 {*, 'Dave', 4}",
              ids(out) == [0x1134] and rows[0][1:] == ("Dave", 4), repr(rows))
        dave = rows[0][0]

        # !party status NAME b w1 w2 d1 d2 -> 0x1139
        out[:] = []
        with _capturing(out):
            feparty.gm(ctx, "!party status Dave 1 2 3 4 5")
        srows = read_status(out[0][2])
        check("!party status -> 0x1139 N=1 {ChrID,u8,u16,u16,u32,u32}",
              ids(out) == [0x1139] and srows == [(dave, 1, 2, 3, 4, 5)],
              repr(srows))

        # !party list logs only
        out[:] = []
        with _capturing(out):
            feparty.gm(ctx, "!party list")
        check("!party list sends nothing", out == [])

        # kick Dave (0x2088) as leader -> 0x113A header-only, then 0x1135
        out[:] = []
        _drive(a, [struct.pack(">HI", 0x2088, dave)], out)
        check("0x2088 kick -> 0x113A then 0x1135 (the OK arm keeps the row)",
              ids(out) == [0x113A, 0x1135], repr(ids(out)))
        check("0x113A header-only", read_none(out[0][2]))
        check("0x1135 [u32 ChrID] names the kicked member",
              read_u32(out[1][2]) == dave)

        # kick a non-member -> 0x113B [5]
        out[:] = []
        _drive(a, [struct.pack(">HI", 0x2088, 12345)], out)
        check("0x2088 non-member -> 0x113B [u32 5]",
              ids(out) == [0x113B] and read_u32(out[0][2]) == 5, repr(out))

        # party full: --party-max 2 with Alice+Bob -> 0x1130 [12]
        out[:] = []
        _drive(_args(party_max=2), [struct.pack(">HI", 0x112E, 1001)], out)
        check("0x112E with the party full -> 0x1130 [u32 12]",
              ids(out) == [0x1130] and read_u32(out[0][2]) == 12, repr(out))

        # leave (0x2089): Alice + phantom Bob -> Alice 0x1137, party broken up
        out[:] = []
        _drive(a, [struct.pack(">H", 0x2089)], out)
        check("0x2089 leave -> 0x1137 header-only",
              ids(out) == [0x1137] and read_none(out[0][2]), repr(out))
        check("the party is gone (below 2 members)",
              feparty._PARTIES == {} and feparty._MEMBER_OF == {})

        # leave again -> 0x1138 [2]
        out[:] = []
        _drive(a, [struct.pack(">H", 0x2089)], out)
        check("0x2089 with no party -> 0x1138 [u32 2]",
              ids(out) == [0x1138] and read_u32(out[0][2]) == 2, repr(out))

        # kick with no party -> 0x113B [3]
        out[:] = []
        _drive(a, [struct.pack(">HI", 0x2088, phantom_bob)], out)
        check("0x2088 with no party -> 0x113B [u32 3]",
              ids(out) == [0x113B] and read_u32(out[0][2]) == 3, repr(out))

        # self-invite (TargetObjID == own unit id 0) -> 0x1130 [3]
        out[:] = []
        _drive(a, [struct.pack(">HI", 0x112E, 0)], out)
        check("0x112E on yourself -> 0x1130 [u32 3]",
              ids(out) == [0x1130] and read_u32(out[0][2]) == 3, repr(out))

        # the SHIPPED default: no phantom -- an invite to a monster is refused
        out[:] = []
        a_def = _args()
        del a_def.party_phantom
        _drive(a_def, [struct.pack(">HI", 0x112E, 1000)], out)
        check("by DEFAULT an invite to an NPC/monster is refused (no phantom) -> [u32 6]",
              ids(out) == [0x1130] and read_u32(out[0][2]) == 6, repr(out))
        # phantom off -> 0x1130 [6]
        out[:] = []
        _drive(_args(party_phantom="off"),
               [struct.pack(">HI", 0x112E, 1000)], out)
        check("--party-phantom off -> 0x1130 [u32 6]",
              ids(out) == [0x1130] and read_u32(out[0][2]) == 6, repr(out))

        # 0x113E cancel -> 0x1130 [13] (bit0 must be cleared)
        out[:] = []
        _drive(a, [struct.pack(">HI", 0x113E, 1000)], out)
        check("0x113E cancel -> 0x1130 [u32 13]",
              ids(out) == [0x1130] and read_u32(out[0][2]) == 13, repr(out))

        # 0x2087 with nothing pending -> 0x1132 [5]
        out[:] = []
        _drive(a, [struct.pack(">HBII", 0x2087, 1, 7, 0)], out)
        check("0x2087 with no invite -> 0x1132 [u32 5]",
              ids(out) == [0x1132] and read_u32(out[0][2]) == 5, repr(out))

        # short 0x2087 -> 0x1132 [2]
        out[:] = []
        _drive(a, [struct.pack(">HB", 0x2087, 1)], out)
        check("short 0x2087 -> 0x1132 [u32 2]",
              ids(out) == [0x1132] and read_u32(out[0][2]) == 2, repr(out))

        # !party add 3 -> three 0x1134, names Fake0..2; then !party breakup
        out[:] = []
        with _capturing(out):
            feparty.gm(ctx, "!party add 3")
        names = [read_add(b)[0][1] for _, _, b in out]
        check("!party add 3 -> three 0x1134 N=1",
              ids(out) == [0x1134] * 3 and names == ["Fake0", "Fake1", "Fake2"],
              repr(names))
        out[:] = []
        with _capturing(out):
            feparty.gm(ctx, "!party del Fake1")
        check("!party del -> 0x1135 [u32]", ids(out) == [0x1135]
              and read_u32(out[0][2]) >= 0x7F000000, repr(out))
        out[:] = []
        with _capturing(out):
            feparty.gm(ctx, "!party breakup")
        check("!party breakup -> 0x1136 header-only, model cleared",
              ids(out) == [0x1136] and read_none(out[0][2])
              and feparty._PARTIES == {}, repr(out))

        # the name clamp: a 100-byte phantom name is cut to 0x20
        out[:] = []
        with _capturing(out):
            feparty.gm(ctx, "!party fake " + "N" * 100)
        rows = read_add(out[0][2])
        check("a long name is clamped to 0x20 bytes (strcpy on the far side)",
              len(rows[0][1]) == 0x20, repr(rows))
        with _capturing(out):
            feparty.gm(ctx, "!party breakup")

        # /party chat in field scope with no party: relayed like feworld does
        # (chat_relay off here -> nothing), NO 0x113D
        out[:] = []
        _drive(a, [struct.pack(">H", 0x208A) + b"Alice\0hello\0"], out)
        check("0x208A field scope (default) answers nothing", out == [],
              repr(out))
        # the SHIPPED default is party scope: no party -> 0x113D
        out[:] = []
        a_def = _args()
        del a_def.party_chat
        _drive(a_def, [struct.pack(">H", 0x208A) + b"Alice\0hello\0"], out)
        check("by DEFAULT /party reaches the party only (none -> 0x113D)",
              ids(out) == [0x113D] and read_none(out[0][2]), repr(out))
        # party scope with no party -> 0x113D header-only
        out[:] = []
        _drive(_args(party_chat="party"),
               [struct.pack(">H", 0x208A) + b"Alice\0hello\0"], out)
        check("0x208A party scope, no party -> 0x113D header-only",
              ids(out) == [0x113D] and read_none(out[0][2]), repr(out))
        # chat echo 'self' still works through the override
        out[:] = []
        _drive(_args(chat_echo="self"),
               [struct.pack(">H", 0x208A) + b"Alice\0hello\0"], out)
        r = Reader(out[0][2])
        check("0x208A --chat-echo self echoes [speaker][text] on 0x208A",
              ids(out) == [0x208A] and (r.cstr(), r.cstr()) == ("Alice", "hello")
              and r.done() is None, repr(out))
    finally:
        feworld._chat_room_leave()
        _reset_model()


# ---------------------------------------------------------------------------
# PART 2 -- three sessions in one thread: the cross-session relay
# ---------------------------------------------------------------------------
class Fake:
    """A second session, entered into feworld's chat room under a fake thread
    key so ext_post() fans out to it. `run(fn, payload)` executes a relay
    receiver with THIS session current, the way ext_pump does on its thread."""

    def __init__(self, key, charid, name, args):
        self.key, self.charid, self.name, self.args = key, charid, name, args
        self.session = {"account": "t%d" % charid, "charid": charid,
                        "in_field": True}
        with feworld._CHAT_ROOM_LOCK:
            feworld._CHAT_ROOM[key] = {"q": queue.Queue(), "session": self.session,
                                       "name": name, "ext": queue.Queue()}

    def drain(self):
        got = []
        q = feworld._CHAT_ROOM[self.key]["ext"]
        while True:
            try:
                got.append(q.get_nowait())
            except queue.Empty:
                return got

    def run(self, fn, *fnargs):
        """Run fn(ctx, *fnargs) with this session current; returns outbox.

        Faithful to a real thread: the session dict is swapped in AND the
        chat-room entries are re-keyed so that, for the duration, THIS fake
        is what threading.get_ident() resolves to (ext_post's me-exclusion,
        _chat_room_name's "my entry") and the real thread's entry is parked."""
        out = []
        me = threading.get_ident()
        saved = feworld._TLS.session
        feworld._TLS.session = self.session
        park = ("parked", me)
        with feworld._CHAT_ROOM_LOCK:
            # the real thread's entry stays LISTED (a real server lists every
            # session), just under a parking key for the duration
            if me in feworld._CHAT_ROOM:
                feworld._CHAT_ROOM[park] = feworld._CHAT_ROOM.pop(me)
            feworld._CHAT_ROOM[me] = feworld._CHAT_ROOM.pop(self.key)
        try:
            with _capturing(out):
                fn(feworld.Ctx(_Conn(), None, "ecb", False, self.args), *fnargs)
        finally:
            feworld._TLS.session = saved
            with feworld._CHAT_ROOM_LOCK:
                feworld._CHAT_ROOM[self.key] = feworld._CHAT_ROOM.pop(me)
                if park in feworld._CHAT_ROOM:
                    feworld._CHAT_ROOM[me] = feworld._CHAT_ROOM.pop(park)
        return out

    def pump(self):
        """Drain my queue and run each relay on my ctx (== ext_pump)."""
        out = []
        for kind, payload in self.drain():
            out += self.run(feworld.EXT_RELAY[kind], payload)
        return out

    def inbound(self, inner):
        """One inbound inner message handled as me (== ext_dispatch)."""
        return self.run(feworld.EXT_HANDLERS[struct.unpack(">H", inner[:2])[0]],
                        inner)

    def leave(self):
        with feworld._CHAT_ROOM_LOCK:
            feworld._CHAT_ROOM.pop(self.key, None)


def part2():
    say("part 2: three sessions (invite -> judge -> add -> chat -> leave)")
    _stub_store()
    _reset_model()
    a = _args(unit_id="auto", party_chat="party", party_proxy_unit=1000)
    # Alice is THIS thread
    feworld._SESSION.clear()
    feworld._SESSION["account"] = "t7"
    feworld._SESSION["charid"] = 7
    feworld._SESSION["in_field"] = True
    me = feworld._chat_room_join()
    feworld._chat_room_name("Alice")
    alice_room = feworld._CHAT_ROOM[me]
    bob = Fake(900001, 8, "Bob", a)
    carol = Fake(900002, 9, "Carol", a)

    def alice_pump():
        out = []
        with _capturing(out):
            feworld.ext_pump(feworld.Ctx(_Conn(), None, "ecb", False, a))
        return out

    try:
        # ---- invite: Alice selects unit 8 (== Bob's charid under --unit-id auto)
        out = []
        _drive(a, [struct.pack(">HI", 0x112E, 8)], out)
        check("0x112E naming a live session answers the inviter NOTHING yet",
              out == [], repr(out))
        check("carol's queue is untouched", carol.drain() == [])
        bout = bob.pump()
        check("bob's session got 0x112E [u32 A][u32 B]",
              ids(bout) == [0x112E], repr(bout))
        r = Reader(bout[0][2])
        A, B = r.u32(), r.u32()
        r.done()
        check("A = --party-proxy-unit (the kind-2 unit Bob's client must hold)",
              A == 1000, repr((A, B)))
        check("B = the inviter's charid (echoed back in 0x2087)", B == 7)
        check("the invite is pending", (8, 7) in feparty._INVITES)

        # a second invite to Bob while one is pending -> 0x1130 [25]
        out[:] = []
        carol_out = carol.inbound(struct.pack(">HI", 0x112E, 8))
        check("a second inviter is refused with 0x1130 [u32 25]",
              ids(carol_out) == [0x1130] and read_u32(carol_out[0][2]) == 25,
              repr(carol_out))

        # ---- Bob says YES: 0x2087 [1][B][0]
        bout = bob.inbound(struct.pack(">HBII", 0x2087, 1, B, 0))
        check("bob gets 0x1134 (his full list) then 0x1131 header-only",
              ids(bout) == [0x1134, 0x1131] and read_none(bout[1][2]),
              repr(ids(bout)))
        rows = read_add(bout[0][2])
        check("bob's 0x1134 lists Alice then Bob (N=2, class 3)",
              rows == [(7, "Alice", 3), (8, "Bob", 3)], repr(rows))
        aout = alice_pump()
        check("alice gets 0x1134 N=1 {Bob} then 0x112F header-only",
              ids(aout) == [0x1134, 0x112F] and read_none(aout[1][2]),
              repr(ids(aout)))
        check("...the N=1 record is Bob",
              read_add(aout[0][2]) == [(8, "Bob", 3)])
        check("model: one party, leader 7, members [7, 8]",
              [p["order"] for p in feparty._PARTIES.values()] == [[7, 8]]
              and list(feparty._PARTIES.values())[0]["leader"] == 7)
        check("carol saw none of it", carol.drain() == [])

        # ---- /party chat is member-scoped
        out[:] = []
        _drive(a, [struct.pack(">H", 0x208A) + b"Alice\0party hi\0"], out)
        check("speaker gets nothing back (no echo, no NG)", out == [])
        bout = bob.pump()
        r = Reader(bout[0][2]) if bout else None
        check("bob receives 0x208A [Alice][party hi] with speaker unit 0",
              ids(bout) == [0x208A] and bout[0][1] == 0
              and (r.cstr(), r.cstr()) == ("Alice", "party hi")
              and r.done() is None, repr(bout))
        check("carol (not a member) receives no /party line",
              carol.drain() == [])
        # Carol has no party -> 0x113D
        cout = carol.inbound(struct.pack(">H", 0x208A) + b"Carol\0lonely\0")
        check("a speaker with no party gets 0x113D header-only",
              ids(cout) == [0x113D] and read_none(cout[0][2]), repr(cout))

        # ---- Carol joins via !party invite NAME + !party accept
        out[:] = []
        with _capturing(out):
            feparty.gm(feworld.Ctx(_Conn(), None, "ecb", False, a),
                       "!party invite carol")
        check("!party invite by name relays the prompt to Carol",
              out == [] and ids(carol.pump()) == [0x112E])
        cout = carol.run(feparty.gm, "!party accept")
        check("!party accept on Carol -> her 0x1134 N=3 then 0x1131",
              ids(cout) == [0x1134, 0x1131]
              and [x[1] for x in read_add(cout[0][2])] == ["Alice", "Bob", "Carol"],
              repr(cout))
        aout, bout = alice_pump(), bob.pump()
        check("alice: 0x1134 {Carol} + 0x112F; bob: 0x1134 {Carol}",
              ids(aout) == [0x1134, 0x112F] and ids(bout) == [0x1134]
              and read_add(bout[0][2]) == [(9, "Carol", 3)],
              repr((ids(aout), ids(bout))))

        # ---- kick Carol as leader: leader 0x113A+0x1135, Carol 0x113C, Bob 0x1135
        out[:] = []
        _drive(a, [struct.pack(">HI", 0x2088, 9)], out)
        check("leader: 0x113A then 0x1135 [9]",
              ids(out) == [0x113A, 0x1135] and read_u32(out[1][2]) == 9,
              repr(out))
        cout, bout = carol.pump(), bob.pump()
        check("carol: 0x113C header-only ('Removed from party.')",
              ids(cout) == [0x113C] and read_none(cout[0][2]), repr(cout))
        check("bob: 0x1135 [9]", ids(bout) == [0x1135]
              and read_u32(bout[0][2]) == 9, repr(bout))
        # a non-leader kick -> 0x113B [2]
        bout = bob.inbound(struct.pack(">HI", 0x2088, 7))
        check("a non-leader kicking -> 0x113B [u32 2]",
              ids(bout) == [0x113B] and read_u32(bout[0][2]) == 2, repr(bout))

        # ---- Bob leaves: Bob 0x1137, Alice alone -> 0x1136
        bout = bob.inbound(struct.pack(">H", 0x2089))
        check("bob leaving -> 0x1137 header-only",
              ids(bout) == [0x1137] and read_none(bout[0][2]), repr(bout))
        aout = alice_pump()
        check("alice, left alone -> 0x1136 header-only; party dropped",
              ids(aout) == [0x1136] and read_none(aout[0][2])
              and feparty._PARTIES == {}, repr(aout))

        # ---- decline: Alice invites Bob, Bob's auto-decline (0, B, 7)
        out[:] = []
        _drive(a, [struct.pack(">HI", 0x112E, 8)], out)
        bob.pump()
        bout = bob.inbound(struct.pack(">HBII", 0x2087, 0, 7, 7))
        check("a NO still gets 0x1131 (bit1 is not set on NO; harmless)",
              ids(bout) == [0x1131], repr(bout))
        aout = alice_pump()
        check("the inviter gets 0x1130 [u32 7] (reason passed through)",
              ids(aout) == [0x1130] and read_u32(aout[0][2]) == 7, repr(aout))
        check("no invite left pending", feparty._INVITES == {})

        # plain NO with reason 0 -> code 8
        _drive(a, [struct.pack(">HI", 0x112E, 8)], out)
        bob.pump()
        bob.inbound(struct.pack(">HBII", 0x2087, 0, 7, 0))
        aout = alice_pump()
        check("a NO with reason 0 -> 0x1130 [u32 8] 'target refused'",
              ids(aout) == [0x1130] and read_u32(aout[0][2]) == 8, repr(aout))

        # ---- timeout: invite, age it, tick on Alice's thread
        out[:] = []
        _drive(a, [struct.pack(">HI", 0x112E, 8)], out)
        bob.pump()
        with feparty._LOCK:
            feparty._INVITES[(8, 7)]["t0"] -= 1000
        out[:] = []
        with _capturing(out):
            feparty.tick(feworld.Ctx(_Conn(), None, "ecb", False, a))
        check("timeout -> inviter 0x1130 [u32 8]",
              ids(out) == [0x1130] and read_u32(out[0][2]) == 8, repr(out))
        bout = bob.pump()
        check("...and the target's prompt is closed with 0x1133 header-only",
              ids(bout) == [0x1133] and read_none(bout[0][2]), repr(bout))

        # ---- cancel (0x113E): Alice withdraws
        out[:] = []
        _drive(a, [struct.pack(">HI", 0x112E, 8)], out)
        bob.pump()
        out[:] = []
        _drive(a, [struct.pack(">HI", 0x113E, 8)], out)
        check("0x113E -> inviter 0x1130 [u32 13]",
              ids(out) == [0x1130] and read_u32(out[0][2]) == 13, repr(out))
        bout = bob.pump()
        check("...target 0x1133 header-only", ids(bout) == [0x1133]
              and read_none(bout[0][2]), repr(bout))
        check("no invite left", feparty._INVITES == {})

        # ---- already in a party: Bob+Carol party, Alice invites Bob -> [7]
        cout = carol.run(feparty.gm, "!party fake Zed")
        check("carol forms a party with a phantom", ids(cout) == [0x1134])
        bout = bob.run(feparty.gm, "!party fake Yan")
        out[:] = []
        _drive(a, [struct.pack(">HI", 0x112E, 8)], out)
        check("inviting someone already in a party -> 0x1130 [u32 7]",
              ids(out) == [0x1130] and read_u32(out[0][2]) == 7, repr(out))
        check("bob's queue got no prompt", bob.drain() == [])
    finally:
        bob.leave()
        carol.leave()
        feworld._chat_room_leave()
        _reset_model()
        del alice_room


def main():
    # run_all pipes stdout, which on Windows is cp1252 -- feparty logs a
    # U+26A0 in its poster-gate line and the print would raise otherwise.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        part1()
        part2()
    except AssertionError as e:
        say("[fe_party_test] FAIL: %s" % e)
        sys.exit(1)
    n = len(CHECKS)
    say("[fe_party_test] OK -- %d checks" % n)


if __name__ == "__main__":
    main()
