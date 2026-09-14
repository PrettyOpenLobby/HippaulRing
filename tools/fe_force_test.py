#!/usr/bin/env python3
"""fe_force_test.py -- services/feforce.py: force / army administration and
the CHAT NG family, decoded the way the client's arms read them.

Run from the repo root:  python tools/fe_force_test.py

Every reply feforce builds is decoded here with the EXACT primitive sequence
measured off the arm (see feforce.py's docstring for the VAs), then
`r.done()` asserts the stream is fully consumed -- a body one field long or
short does not error on the client, it desynchronises the stream silently.

  0x1177 / 0x1178   registry-erased, no arm            NOTHING
  0x3029            0x05056ad7 -> 0x5050d20             u32 key, u32 mask,
                                                       bits 0..17 gated
  0x302A            0x05056af6 -> 0x5050f30             u32, u32, u32
  0x3033            0x050057ec                          NOTHING
  0x3034            0x0500580e                          u32
  0x3028            0x050058d7                          u32, u32, u32
  0x3017 0x3019 0x301F 0x3020 0x3035                    NOTHING
  0x1180            0x05053ad3                          u32
  0x206C..0x206F    0x05053e02 (posts; reads nothing)   NOTHING by default

PARTIAL: Green here means the bytes match the arms; it says nothing about what
the client DRAWS. Only a screenshot from a live client settles that.
"""
import json
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
import feforce   # noqa: E402

CHECKS = []
OUT = sys.__stdout__


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-62s %s%s" % (label, "PASS" if cond else "FAIL",
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
    feworld._SESSION["account"] = "tester"
    feworld._SESSION["charid"] = 7


def _args(**kw):
    a = dict(unit_id="0", seq_mode="count", world_prefix=4, read_window=1.0,
             gmcmd=None, gmcmd_file=None, probe_on_auth=False,
             ui_auto="ok", ui_auto_err=0, move_request="off",
             chat_relay="off", chat_echo="off", chat_line=None,
             validate_finish=0, war_start="off", war_clock="off",
             war_deadline_ms=0,
             force_table={1: {"name": "Netzawar", "TargetFieldID": 3},
                          2: {"name": "Gebrand"}},
             force_shop="ok", force_node="ok", force_node_mask=0,
             force_node_field="", force_join="auto", force_file=None,
             chat_ng="off", chat_ng_body="none")
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


def _gm(a, lines, outbox):
    """Feed `lines` through gm_pump (the --gmcmd-file path) with the wire
    captured, the way a live !verb arrives."""
    saved = (feworld.send_world_frame, fenet.bf_encrypt, fenet.traffic_wrap)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data

    def _capture(conn, mid, body, prefix):
        unit, msg = struct.unpack_from(">IH", body, 0)
        outbox.append((msg, unit, body[6:]))

    feworld.send_world_frame = _capture
    fd, path = tempfile.mkstemp(suffix=".gm")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        a.gmcmd_file = path
        feworld.gm_pump(_Conn(), None, "ecb", False, a)
    finally:
        (feworld.send_world_frame, fenet.bf_encrypt, fenet.traffic_wrap) = saved
        a.gmcmd_file = None
        try:
            os.unlink(path)
        except OSError:
            pass


def _tmp_force_file():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)          # feforce must cope with "not there yet"
    return path


def _fresh_model(path):
    feforce._STATE.update({"loaded": False, "forces": {}, "judge": None,
                           "last_node_key": None})
    return _args(force_file=path)


# ---------------------------------------------------------------------------
def part_registry():
    say("registration")
    feworld.load_extensions(["feforce"])
    for mid in (0x4013, 0x206B, 0x20A9, 0x4012, 0x2067):
        check("0x%04X is claimed by feforce" % mid,
              feworld.EXT_HANDLERS.get(mid) is not None
              and feworld.EXT_HANDLERS[mid].__module__ == "feforce")
    check("0x4012 / 0x2067 are deliberate overrides of builtin arms",
          0x4012 in feworld._BUILTIN_IDS and 0x2067 in feworld._CHAT_IDS)
    check("KNOWN names registered (0x3029, 0x1180, 0x20A9)",
          all(m in feworld.KNOWN for m in (0x3029, 0x1180, 0x20A9)))
    check("!force / !chatng verb registered",
          any(getattr(f, "__module__", "") == "feforce" for f in feworld.EXT_GM))


def part_force_shop():
    say("0x20A9 MSG_GET_FORCE_SHOP_ITEM_REQUEST -> 0x1177 / 0x1178")
    _stub_store()
    out = []
    _drive(_args(), [struct.pack(">HI", 0x20A9, 1)], out)
    check("ok: exactly one reply", len(out) == 1, repr(out))
    mid, unit, body = out[0]
    check("ok: 0x1177, header-only (registry-erased; no arm reads it)",
          mid == 0x1177 and body == b"", "%04x %r" % (mid, body))
    check("ok: header unit id 0 (the builtin force-family shape)", unit == 0)
    out = []
    _drive(_args(force_shop="ng"), [struct.pack(">HI", 0x20A9, 2)], out)
    check("ng: 0x1178 header-only",
          [(m, b) for m, _, b in out] == [(0x1178, b"")], repr(out))
    out = []
    _drive(_args(force_shop="off"), [struct.pack(">HI", 0x20A9, 2)], out)
    check("off: nothing sent (prod's silence, kept as a knob)", out == [],
          repr(out))


def part_node_info():
    say("0x206B node info -> 0x3029 / 0x302A")
    _stub_store()
    out = []
    a = _args()
    _drive(a, [struct.pack(">HII", 0x206B, 0x2A, 0x3FFFF)], out)
    check("one 0x3029 reply", len(out) == 1 and out[0][0] == 0x3029, repr(out))
    r = Reader(out[0][2])
    check("0x3029: u32 key echoes the request (the lookup must HIT)",
          r.u32() == 0x2A)
    check("0x3029: u32 mask 0 by default (parser reads nothing after it)",
          r.u32() == 0)
    r.done()
    check("0x3029 mask 0 stream fully consumed", True)
    check("last node key remembered for !force node",
          feforce._STATE["last_node_key"] == 0x2A)

    # Every measured bit, decoded with the width 0x5050d20 reads it with.
    out = []
    a = _args(force_node_mask=0x3FFFF,
              force_node_field="0=alpha,1=7,2=Lex,3=1,4=2,5=3,6=4,7=5,8=6,"
                               "9=9,10=1000,11=1001,12=1002,13=1003,14=1004,"
                               "15=100000,16=100001,17=100002")
    _drive(a, [struct.pack(">HII", 0x206B, 0x2A, 0x3FFFF)], out)
    r = Reader(out[0][2])
    check("0x3029 full mask: key", r.u32() == 0x2A)
    check("0x3029 full mask: mask 0x3FFFF", r.u32() == 0x3FFFF)
    check("bit0 cstr (local)", r.cstr() == "alpha")
    check("bit1 u8 (local)", r.u8() == 7)
    check("bit2 cstr -> node+0x1c", r.cstr() == "Lex")
    check("bit3..5 u8 -> node+0x3d/3e/3f", [r.u8() for _ in range(3)] == [1, 2, 3])
    check("bit6..8 u8 (local)", [r.u8() for _ in range(3)] == [4, 5, 6])
    check("bit9 1 byte (0x5045e90)", r.u8() == 9)
    check("bit10 u16 (0x5045ec0)", r.u16() == 1000)
    check("bit11 u16 -> node+0x42", r.u16() == 1001)
    check("bit12..14 u16", [r.u16() for _ in range(3)] == [1002, 1003, 1004])
    check("bit15 u32 -> node+0x44", r.u32() == 100000)
    check("bit16..17 u32", [r.u32() for _ in range(2)] == [100001, 100002])
    r.done()
    check("0x3029 full-mask stream fully consumed", True)

    out = []
    _drive(_args(force_node="ng:6"), [struct.pack(">HII", 0x206B, 9, 0x3FFFF)], out)
    check("ng: one 0x302A", len(out) == 1 and out[0][0] == 0x302A, repr(out))
    r = Reader(out[0][2])
    check("0x302A: u32 key, u32 x, u32 code (0x5050f30)",
          (r.u32(), r.u32(), r.u32()) == (9, 0, 6))
    r.done()
    check("0x302A stream fully consumed", True)


def part_join():
    say("0x4012 MSG_JOIN_FORCE_REQUEST (overridden) -> 0x3033 / 0x3034")
    path = _tmp_force_file()
    try:
        _stub_store()
        out = []
        a = _fresh_model(path)
        _drive(a, [struct.pack(">HI", 0x4012, 2)], out)
        check("auto: one 0x3033 header-only, unit 0 (the builtin shape)",
              [(m, u, b) for m, u, b in out] == [(0x3033, 0, b"")], repr(out))
        check("auto: nation persisted as `force` (what 0xD002 serves back)",
              STORE.get("force") == 2, repr(STORE))

        STORE.clear()
        out = []
        a = _fresh_model(path)
        a.force_join = "ng:3"
        _drive(a, [struct.pack(">HI", 0x4012, 2)], out)
        check("--force-join ng:3: one 0x3034", len(out) == 1 and out[0][0] == 0x3034,
              repr(out))
        r = Reader(out[0][2])
        check("0x3034: u32 code (0x0500580e, NOT a cstr)", r.u32() == 3)
        r.done()
        check("0x3034 stream fully consumed", True)
        check("refused join stores nothing", "force" not in STORE, repr(STORE))

        # !force judge wins over the flag and persists.
        out = []
        a = _fresh_model(path)
        a.force_join = "auto"
        _gm(a, ["!force judge ng:5"], out)
        check("!force judge persisted",
              json.load(open(path, encoding="utf-8")).get("judge") == "ng:5")
        out = []
        a = _fresh_model(path)          # reload from the file
        _drive(a, [struct.pack(">HI", 0x4012, 1)], out)
        check("persisted judge policy refuses the next join with code 5",
              len(out) == 1 and out[0][0] == 0x3034
              and struct.unpack(">I", out[0][2])[0] == 5, repr(out))
        out = []
        _gm(a, ["!force judge ok"], out)
        out = []
        _drive(a, [struct.pack(">HI", 0x4012, 1)], out)
        check("!force judge ok restores the OK", out and out[0][0] == 0x3033)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def part_inventory_status():
    say("0x4013 MSG_INVENTORY_STATUS_INFO_REQUEST (dead builder)")
    _stub_store()
    out = []
    _drive(_args(), [struct.pack(">H", 0x4013)], out)
    check("nothing sent: registers no reply, has no caller", out == [], repr(out))


def part_model():
    say("the force model: rows overlay args.force_table and persist")
    path = _tmp_force_file()
    try:
        _stub_store()
        a = _fresh_model(path)
        out = []
        _gm(a, ["!force kingmsg 1 Hold the line",
                "!force set 1 TargetFieldID 17",
                "!force create 3 Hordaine",
                "!force set 9 nosuchkey 1"], out)
        check("kingmsg lands in the LIVE table feworld's 0x206A reads",
              a.force_table[1].get("KingMessage") == "Hold the line",
              repr(a.force_table))
        check("set TargetFieldID as an int", a.force_table[1]["TargetFieldID"] == 17)
        check("create adds a row", a.force_table.get(3, {}).get("name") == "Hordaine")
        check("an unknown FORCE_FIELDS key is refused", 9 not in a.force_table)
        doc = json.load(open(path, encoding="utf-8"))
        check("persisted to the force file",
              doc["forces"]["1"]["KingMessage"] == "Hold the line"
              and doc["forces"]["3"]["name"] == "Hordaine", repr(doc))
        # the record still builds the way 0x3027 wants it
        rec = feworld.build_force_record(a.force_table[1])
        check("0x3027 record still builds with the edited row",
              b"Hold the line\0" in rec and b"Netzawar\0" in rec)

        b = _fresh_model(path)          # a new process: overlay from the file
        rows = feforce.force_rows(b)
        check("a restart re-applies the overlay onto args.force_table",
              b.force_table[1].get("KingMessage") == "Hold the line"
              and rows[3]["name"] == "Hordaine", repr(rows))
        _gm(b, ["!force breakup 3"], out)
        check("breakup drops the row (0x206A then answers an empty record)",
              3 not in b.force_table and "3" not in
              json.load(open(path, encoding="utf-8"))["forces"])

        # membership across accounts, through the store hooks
        chars = [("acctA", {"charid": 7, "name": "Lex", "force": 1}),
                 ("acctB", {"charid": 9, "name": "Bob", "force": 1}),
                 ("acctB", {"charid": 10, "name": "Eve", "force": 2})]
        writes = []
        feforce._ALL_CHARS = lambda: chars
        feforce._set_char_force = lambda acct, cid, fid: (
            writes.append((acct, cid, fid)) or True)
        check("members(force 1)", sorted(n for _, n, _, _ in feforce.members(b, 1))
              == ["Bob", "Lex"])
        _gm(b, ["!force expel bob", "!force canvass 10 1", "!force withdraw"], out)
        check("expel by name (case-folded) writes force=0 for THAT account/char",
              ("acctB", 9, 0) in writes, repr(writes))
        check("canvass by charid writes the force id", ("acctB", 10, 1) in writes,
              repr(writes))
        check("withdraw writes this session's force=0", STORE.get("force") == 0)
    finally:
        feforce._ALL_CHARS = None
        if os.path.exists(path):
            os.unlink(path)


def part_pushes():
    say("!force pushes -- the NG / flag / probe family")
    _stub_store()
    a = _args()
    out = []
    _gm(a, ["!force ng 3017", "!force ng 3019", "!force flag299 1",
            "!force flag299 0", "!force probe 3035",
            "!force ng 3028 2 0xFFFFFFFF 4", "!force ng 3034 7"], out)
    mids = [m for m, _, _ in out]
    check("order and ids", mids == [0x3017, 0x3019, 0x301F, 0x3020, 0x3035,
                                    0x3028, 0x3034], repr(mids))
    for m, u, b in out[:5]:
        check("0x%04X header-only, unit 0" % m, b == b"" and u == 0, repr((m, u, b)))
    r = Reader(out[5][2])
    check("0x3028: u32 ForceID, u32 Bit, u32 ErrorID (0x050058d7)",
          (r.u32(), r.u32(), r.u32()) == (2, 0xFFFFFFFF, 4))
    r.done()
    r = Reader(out[6][2])
    check("0x3034 via !force: u32 code", r.u32() == 7)
    r.done()

    # node probes need a key the client asked about
    feforce._STATE["last_node_key"] = None
    out = []
    _gm(a, ["!force node 0x4 2=Lex"], out)
    check("!force node refuses to invent a key (lookup MISS = desync)", out == [])
    feforce._STATE["last_node_key"] = 0x2A
    out = []
    _gm(a, ["!force node 0x4 2=Lex", "!force nodeng 1"], out)
    r = Reader(out[0][2])
    check("!force node: key, mask 4, bit2 cstr",
          (r.u32(), r.u32(), r.cstr()) == (0x2A, 4, "Lex"))
    r.done()
    r = Reader(out[1][2])
    check("!force nodeng: key, x, code", (r.u32(), r.u32(), r.u32()) == (0x2A, 0, 1))
    r.done()


def part_chat_ng():
    say("the CHAT NG family")
    _stub_store()
    a = _args()
    ctx = feworld.Ctx(_Conn(), None, "ecb", False, a)
    out = []
    saved = (feworld.send_world_frame, fenet.bf_encrypt, fenet.traffic_wrap)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data
    feworld.send_world_frame = lambda c, mid, body, p: out.append(
        (struct.unpack_from(">H", body, 4)[0], struct.unpack_from(">I", body)[0],
         body[6:]))
    try:
        check("request -> NG map covers all six chat ids",
              set(feforce.CHAT_NG_FOR) == set(feworld._CHAT_IDS),
              repr(sorted(feforce.CHAT_NG_FOR)))
        got = feforce.chat_refuse(ctx, 0x2067, feforce.CHAT_NG_NOT_LOGGED_IN)
        check("/tell -> 0x1180 MSG_WHISPER_NG", got == 0x1180 and out[-1][0] == 0x1180)
        r = Reader(out[-1][2])
        check("0x1180: u32 code (0x05053ad3), unit 0", r.u32() == 8 and out[-1][1] == 0)
        r.done()
        for req, ng in ((0x201A, 0x206C), (0x201B, 0x206D), (0x2065, 0x206E),
                        (0x2066, 0x206F), (0x208A, 0x113D)):
            got = feforce.chat_refuse(ctx, req, 3)
            check("0x%04X -> 0x%04X, HEADER-ONLY by default (the arm reads nothing)"
                  % (req, ng), got == ng and out[-1] == (ng, 0, b""), repr(out[-1]))
        a.chat_ng_body = "u32"
        feforce.chat_refuse(ctx, 0x201A, 3)
        r = Reader(out[-1][2])
        check("--chat-ng-body u32 appends the code as the probe", r.u32() == 3)
        r.done()
        a.chat_ng_body = "none"
        n = len(out)
        check("an NG id given directly is sent as itself",
              feforce.chat_refuse(ctx, 0x206D, 0) == 0x206D and out[-1][0] == 0x206D)
        check("a non-chat id sends nothing",
              feforce.chat_refuse(ctx, 0x2000, 1) is None and len(out) == n + 1)
        check("code table: 8 and 9 are the notice kind (drawn as a chat line)",
              feforce.CHAT_NG_TEXT[8][0] == 4 and feforce.CHAT_NG_TEXT[9][0] == 4
              and all(feforce.CHAT_NG_TEXT[c][0] == 2 for c in range(8)))
    finally:
        (feworld.send_world_frame, fenet.bf_encrypt, fenet.traffic_wrap) = saved

    # !chatng through the gm path
    out = []
    _gm(a, ["!chatng 1180 9", "!chatng 201B 3", "!chatng zz"], out)
    check("!chatng 1180 9 -> 0x1180 [u32 9]",
          out[0][0] == 0x1180 and struct.unpack(">I", out[0][2])[0] == 9)
    check("!chatng 201B 3 -> 0x206D header-only (request id mapped)",
          out[1] == (0x206D, 0, b""))
    check("!chatng with garbage sends nothing", len(out) == 2)


def part_tell_override():
    say("0x2067 /tell (overridden): refuse a whisper to nobody")
    _stub_store()
    line = struct.pack(">H", 0x2067) + b"Nobody\0Lex\0hello\0"
    out = []
    _drive(_args(chat_relay="on", chat_ng="off"), [line], out)
    check("--chat-ng off: the builtin silence (nothing sent)", out == [], repr(out))
    out = []
    _drive(_args(chat_relay="on", chat_ng="on"), [line], out)
    check("--chat-ng on: one 0x1180", [m for m, _, _ in out] == [0x1180], repr(out))
    r = Reader(out[0][2])
    check("0x1180 code 8: 'that character does not exist / is not logged in'",
          r.u32() == feforce.CHAT_NG_NOT_LOGGED_IN)
    r.done()
    out = []
    _drive(_args(chat_relay="off", chat_ng="on"), [line], out)
    check("relay off: no relay attempt, so no NG either (builtin order kept)",
          out == [], repr(out))
    out = []
    _drive(_args(chat_relay="on", chat_ng="on", chat_echo="say"), [line], out)
    check("chat_echo say still echoes on 0x2067 before the NG",
          [m for m, _, _ in out] == [0x2067, 0x1180], repr([m for m, _, _ in out]))


def main():
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
        part_registry()
        part_force_shop()
        part_node_info()
        part_join()
        part_inventory_status()
        part_model()
        part_pushes()
        part_chat_ng()
        part_tell_override()
    except AssertionError as e:
        say("[fe_force_test] FAIL: %s" % e)
        return 1
    finally:
        if quiet:
            sys.stdout = sys.__stdout__
    bad = [l for l, ok in CHECKS if not ok]
    say("[fe_force_test] %s (%d checks)" % ("OK" if not bad else "FAIL", len(CHECKS)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
