#!/usr/bin/env python3
"""fe_mail_test.py -- services/femail.py (Fantasy Earth in-game mail), no client.

Run from the repo root:  python tools/fe_mail_test.py

Every request body below is built the way the client's own serializer writes
it (0x050c3f50: name[32] first, then the per-request object's vt[2]) and every
reply is decoded with the EXACT primitive sequence the mail window's handler
uses (see femail.py's docstring for the VAs), then `r.done()` asserts nothing
is left on the stream -- a body one field long or short does not error on the
client, it desynchronises everything after it.

    0xA051/0xA081/0x111C  nothing
    0xA052/0xA062/0xA065/0xA068/0xA06B/0xA082/0x111D   u8
    0xA061/0xA064         u32
    0xA067                u32 n, n x {u32,u32,u32,u32,u8, 32,32,32,32 raw}
    0xA06A                u32, 512 raw, 128 raw

The store is a temp sqlite file (--mail-db); the roster lookups are stubbed
(femail._all_chars / feworld._self_char) so no data/ file is touched.
"""
import io
import os
import struct
import sys
import tempfile
import threading
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import fenet     # noqa: E402
import feworld   # noqa: E402
import femail    # noqa: E402

feworld.load_extensions(["femail"])   # registers femail only

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

    def raw(self, n):
        """0x05045b10(ptr, n): n bytes verbatim, decoded to the first NUL."""
        v = self.b[self.i:self.i + n]
        assert len(v) == n, "raw(%d) ran off the stream" % n
        self.i += n
        return femail.dec_fixed(v)

    def cstr(self):
        e = self.b.index(b"\0", self.i)
        v = self.b[self.i:e]
        self.i = e + 1
        return v.decode("cp932")

    def done(self):
        assert self.i == len(self.b), (
            "%d of %d bytes consumed -- the client would leave %r on the "
            "stream" % (self.i, len(self.b), self.b[self.i:]))


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
ROSTER = [("acct:A", 7, "ALICE"), ("acct:B", 9, "Bob")]
STORE = {}          # per-character fields for THIS session (take mode)
PUSHES = []         # (kind, detail) from the stubbed wallet/bag pushes


def _stub_store():
    STORE.clear()
    PUSHES[:] = []
    feworld._store_char_field = lambda a, k, v: (STORE.__setitem__(k, v), True)[1]
    feworld._load_char_field = lambda a, k, d=None: STORE.get(k, d)
    feworld._seeded_value = lambda a, k, seed: STORE.get(k, seed)
    feworld.wallet_push = lambda c, o, m, b, a: PUSHES.append(("wallet", STORE.get("gold")))
    feworld.bag_layout_push = (lambda c, o, m, b, a, old, new, gone, why:
                               PUSHES.append(("bag", old, new, gone, why)))
    femail._all_chars = lambda: list(ROSTER)
    feworld._self_char = lambda a: {"name": "ALICE", "charid": 7}
    feworld._SESSION["account"] = "acct:A"
    feworld._SESSION["charid"] = 7
    feworld._SESSION["in_field"] = True


def _args(**kw):
    a = dict(unit_id="0", seq_mode="count", world_prefix=4, read_window=1.0,
             gmcmd=None, gmcmd_file=None, probe_on_auth=False,
             ui_auto="ok", ui_auto_err=0, move_request="off",
             chat_relay="off", chat_echo="off", chat_line=None,
             validate_finish=0, war_start="off", war_clock="off",
             war_deadline_ms=0, gold=None,
             mail="on", mail_db=None, mail_folders="sent,inbox",
             mail_box_size=30, mail_attach="display", mail_from="GM",
             mail_order="new")
    a.update(kw)
    return types.SimpleNamespace(**a)


class _Conn:
    def settimeout(self, t):
        pass

    def getpeername(self):
        return ("127.0.0.1", 1)


def _drive(a, frames):
    """Run inner frames through the REAL _serve_loop, crypto stubbed; returns
    [(mid, unit, payload)] and the captured log."""
    outbox = []
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
    log = io.StringIO()
    real = sys.stdout
    sys.stdout = log
    try:
        feworld._serve_loop(_Conn(), a, None, None, "ecb", False)
    finally:
        sys.stdout = real
        (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap,
         fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame) = saved
    return outbox, log.getvalue()


def _one(a, frame):
    out, log = _drive(a, [frame])
    assert len(out) == 1, "expected ONE reply, got %r" % (out,)
    return out[0][0], out[0][2], log


# ---- request builders, in the client's write order ------------------------ #
def _name(s):
    return femail.enc_fixed(s, 32)


def req_send(to, subject, text, attach="", ref=0xFFFFFFFF, me="ALICE",
             date=""):
    """0xA050: vt[1] u32 ref; name[32]; vt[2] to[32] block64 text[512] attach[128].
    The subject goes in the SECOND half of the 64-byte block (rec+0x7b)."""
    return (struct.pack(">HI", 0xA050, ref) + _name(me) + _name(to)
            + femail.enc_fixed(date, 32) + femail.enc_fixed(subject, 32)
            + femail.enc_fixed(text, 512) + femail.enc_fixed(attach, 128))


def req_folder(mid, folder, me="ALICE"):
    return struct.pack(">H", mid) + _name(me) + bytes([folder])


def req_header(folder, index, count, me="ALICE"):
    return req_folder(0xA066, folder, me) + struct.pack(">II", index, count)


def req_id(mid, folder, mail_id, me="ALICE"):
    return req_folder(mid, folder, me) + struct.pack(">I", mail_id)


def req_attach(mail_id, slot, unit=7):
    return struct.pack(">HIIH", 0x2074, unit, mail_id, slot)


def dec_headers(body):
    r = Reader(body)
    rows = []
    for _ in range(r.u32()):
        rows.append(dict(id=r.u32(), ref=r.u32(), t14=r.u32(), t10=r.u32(),
                         read=r.u8(), frm=r.raw(32), to=r.raw(32),
                         date=r.raw(32), subject=r.raw(32)))
    r.done()
    return rows


SENT, INBOX = 0, 1


# --------------------------------------------------------------------------- #
def main():
    tmp = tempfile.mkdtemp(prefix="femail-")
    db = os.path.join(tmp, "fe_mail.db")
    _stub_store()
    a = _args(mail_db=db)
    say("[fe_mail_test] store %s" % db)

    say("registration")
    check("the six mail requests + 0x2074 are claimed by femail",
          all(feworld.EXT_HANDLERS.get(m) for m in
              (0xA050, 0xA060, 0xA063, 0xA066, 0xA069, 0xA080, 0x2074)))
    check("KNOWN names the whole family", 0xA067 in feworld.KNOWN
          and "MSG_MAIL_HEADER_RESULT" in feworld.KNOWN[0xA067])
    check("the store path is never data/fe.db",
          not femail.default_db().endswith("fe.db")
          and femail.default_db().endswith("fe_mail.db"))

    say("encoders")
    check("enc_fixed clips on a cp932 character boundary and NUL-pads",
          femail.enc_fixed("あ" * 20, 32)[-1] == 0
          and len(femail.enc_fixed("あ" * 20, 32)) == 32
          and femail.dec_fixed(femail.enc_fixed("あ" * 20, 32)) == "あ" * 15)
    row = femail.header_row(dict(id=5, ref=0xFFFFFFFF, sent_at=1234, read=1,
                                 from_name="A", to_name="B", date="26/09/10 01:02",
                                 subject="s"))
    check("a header row is the 145 bytes 0x050c44d0..0x050c4542 reads",
          len(row) == femail.HEADER_ROW == 145)
    check("attach text round-trips through the 0x050c0530 grammar",
          femail.parse_attach(femail.format_attach([(0, 1, 0, 500), (0, 2, 77, 3)]))
          == [(0, 1, 0, 500), (0, 2, 77, 3)]
          and femail.parse_attach("") == [] and femail.parse_attach("Attach:x") == [])
    check("attach slots: gold -> 0, items -> 1 then 2 (0x050c0637)",
          femail.attach_slots([(0, 2, 10, 1), (0, 1, 0, 9), (0, 3, 11, 1)])
          == {1: 0, 0: 1, 2: 2})

    say("empty box")
    mid, body, log = _one(a, req_folder(0xA060, INBOX))
    r = Reader(body)
    check("0xA060 -> 0xA061 [u32 AllMailNums] = 0", mid == 0xA061 and r.u32() == 0)
    r.done()
    check("the gate is logged on every send", "window-gated" in log)
    mid, body, _ = _one(a, req_folder(0xA063, INBOX))
    r = Reader(body)
    check("0xA063 -> 0xA064 [u32 NewMailNums] = 0", mid == 0xA064 and r.u32() == 0)
    r.done()
    mid, body, _ = _one(a, req_folder(0xA060, 5))
    r = Reader(body)
    check("folder 5 (0x050c32f0 accepts 0..1) -> 0xA062 [u8 err]",
          mid == 0xA062 and r.u8() == femail.NG_GENERIC)
    r.done()
    mid, body, _ = _one(a, req_header(INBOX, 0, 10))
    check("0xA066 on an empty box -> 0xA067 with n=0", mid == 0xA067
          and dec_headers(body) == [])

    say("send")
    mid, body, _ = _one(a, req_send("Nobody", "hi", "text"))
    r = Reader(body)
    check("unknown recipient -> 0xA052 err=33 'Recipient not found; not sent.'",
          mid == 0xA052 and r.u8() == 33)
    r.done()
    mid, body, log = _one(a, req_send("bob", "Hello Bob", "line one\nline two",
                                      ref=0x11223344))
    check("known recipient (case-folded 'bob' -> Bob) -> 0xA051 header-only",
          mid == 0xA051 and body == b"")
    check("the sender's own copy landed in folder 0 (sent)",
          femail.box_count(a, "acct:A/7", SENT) == 1)
    check("the recipient's copy landed in folder 1 (inbox), unread",
          femail.box_count(a, "acct:B/9", INBOX) == 1
          and femail.box_count(a, "acct:B/9", INBOX, True) == 1)
    check("SEND_REQ's first u32 (rec+0x0c) is kept on both copies",
          femail.box_page(a, "acct:B/9", INBOX, 0, 1)[0]["ref"] == 0x11223344)
    mid, body, _ = _one(a, req_send("Bob", "", "", me="ALICE",
                                    date="subject in the first half"))
    check("a subject typed into the FIRST half of the 64-byte block survives",
          mid == 0xA051 and femail.box_page(a, "acct:B/9", INBOX, 0, 1)[0]["subject"]
          == "subject in the first half")
    short = struct.pack(">HI", 0xA050, 0) + _name("ALICE")
    mid, body, _ = _one(a, short)
    r = Reader(body)
    check("a short 0xA050 body -> 0xA052 err=1 (parameter)", mid == 0xA052
          and r.u8() == 1)
    r.done()

    say("headers")
    mid, body, _ = _one(a, req_folder(0xA060, SENT))
    r = Reader(body)
    check("sent box now counts 2", mid == 0xA061 and r.u32() == 2)
    r.done()
    mid, body, log = _one(a, req_header(SENT, 0, 10))
    rows = dec_headers(body)
    check("0xA067 decodes as u32 n + n x 145-byte rows, stream consumed",
          mid == 0xA067 and len(rows) == 2)
    check("newest first (--mail-order new); from/to/subject/date filled",
          rows[0]["id"] > rows[1]["id"] and rows[1]["frm"] == "ALICE"
          and rows[1]["to"] == "Bob" and rows[1]["subject"] == "Hello Bob"
          and len(rows[1]["date"]) == 14 and rows[1]["read"] == 1)
    check("the two u32s after ref carry the send time (a guess, documented)",
          rows[1]["t14"] == rows[1]["t10"] == femail.box_page(
              a, "acct:A/7", SENT, 1, 1)[0]["sent_at"])
    mid, body, _ = _one(a, req_header(SENT, 1, 10))
    check("Index:[1+10] pages past the first row",
          [x["id"] for x in dec_headers(body)] == [rows[1]["id"]])
    mid, body, _ = _one(a, req_header(SENT, 0, 1))
    check("Index:[0+1] returns exactly one", len(dec_headers(body)) == 1)
    check("the HEADER log names each row the client's way",
          "Mail:[Bob][Hello Bob]" in log)

    # now be Bob
    feworld._SESSION["account"], feworld._SESSION["charid"] = "acct:B", 9
    feworld._self_char = lambda a_: {"name": "Bob", "charid": 9}
    say("body (as Bob)")
    mid, body, _ = _one(a, req_folder(0xA063, INBOX, me="Bob"))
    r = Reader(body)
    check("Bob's NewMailNums is 2", mid == 0xA064 and r.u32() == 2)
    r.done()
    mid, body, _ = _one(a, req_header(INBOX, 0, 10, me="Bob"))
    brows = dec_headers(body)
    hello = [x for x in brows if x["subject"] == "Hello Bob"][0]
    check("Bob's inbox rows show read=0 and from=ALICE",
          hello["read"] == 0 and hello["frm"] == "ALICE" and hello["to"] == "Bob")
    mid, body, _ = _one(a, req_id(0xA069, INBOX, hello["id"], me="Bob"))
    r = Reader(body)
    got_id = r.u32()
    text = r.raw(512)
    attach = r.raw(128)
    r.done()
    check("0xA06A = u32 id (== GetMailID) + 512 text + 128 attach, consumed",
          mid == 0xA06A and got_id == hello["id"] and text == "line one\nline two"
          and attach == "")
    mid, body, _ = _one(a, req_folder(0xA063, INBOX, me="Bob"))
    r = Reader(body)
    check("reading the body marks it read: NewMailNums drops to 1",
          mid == 0xA064 and r.u32() == 1)
    r.done()
    mid, body, _ = _one(a, req_id(0xA069, INBOX, 424242, me="Bob"))
    r = Reader(body)
    check("an unknown GetMailID -> 0xA06B [u8 err=20]", mid == 0xA06B
          and r.u8() == 20)
    r.done()
    mid, body, _ = _one(a, req_id(0xA069, SENT, hello["id"], me="Bob"))
    r = Reader(body)
    check("asking in the WRONG folder is not found either", mid == 0xA06B
          and r.u8() == 20)
    r.done()

    say("delete")
    mid, body, _ = _one(a, req_id(0xA080, INBOX, hello["id"], me="Bob"))
    check("0xA080 -> 0xA081 header-only", mid == 0xA081 and body == b"")
    mid, body, _ = _one(a, req_id(0xA080, INBOX, hello["id"], me="Bob"))
    r = Reader(body)
    check("deleting it again -> 0xA082 [u8 err=20]", mid == 0xA082 and r.u8() == 20)
    r.done()
    mid, body, _ = _one(a, req_folder(0xA060, INBOX, me="Bob"))
    r = Reader(body)
    check("AllMailNums is 1 after the delete", r.u32() == 1)
    r.done()
    check("ALICE's sent copy is untouched by Bob's delete",
          femail.box_count(a, "acct:A/7", SENT) == 2)

    say("box limits")
    small = _args(mail_db=db, mail_box_size=1)
    feworld._SESSION["account"], feworld._SESSION["charid"] = "acct:A", 7
    feworld._self_char = lambda a_: {"name": "ALICE", "charid": 7}
    mid, body, _ = _one(small, req_send("Bob", "x", "y"))
    r = Reader(body)
    check("recipient inbox at --mail-box-size -> 0xA052 err=35",
          mid == 0xA052 and r.u8() == 35)
    r.done()
    femail.box_delete(a, "acct:B/9", INBOX,
                      femail.box_page(a, "acct:B/9", INBOX, 0, 1)[0]["id"])
    mid, body, _ = _one(small, req_send("Bob", "x", "y"))
    r = Reader(body)
    check("sender's sent box full -> 0xA052 err=36", mid == 0xA052 and r.u8() == 36)
    r.done()

    say("attachments, --mail-attach display (default)")
    att = femail.format_attach([(0, 1, 0, 500), (0, 2, 77, 3)])
    mid, body, _ = _one(a, req_send("Bob", "gift", "take it", attach=att))
    check("a SEND with 'Attach:2,...' is stored verbatim for the recipient",
          mid == 0xA051 and femail.box_page(a, "acct:B/9", INBOX, 0, 1)[0]["attach"]
          == att)
    check("the sender's copy has both slots marked taken",
          femail.parse_attach(femail.box_page(a, "acct:A/7", SENT, 0, 1)[0]["attach"])
          == [(1, 1, 0, 500), (1, 2, 77, 3)])
    check("nothing moved in display mode", PUSHES == [] and STORE == {})
    feworld._SESSION["account"], feworld._SESSION["charid"] = "acct:B", 9
    feworld._self_char = lambda a_: {"name": "Bob", "charid": 9}
    gift = femail.box_page(a, "acct:B/9", INBOX, 0, 1)[0]
    mid, body, _ = _one(a, req_id(0xA069, INBOX, gift["id"], me="Bob"))
    r = Reader(body)
    r.u32()
    r.raw(512)
    check("0xA06A carries the attach text in its 128-byte field",
          r.raw(128) == att)
    r.done()
    mid, body, _ = _one(a, req_attach(gift["id"], 0))
    r = Reader(body)
    check("0x2074 slot 0 (gold) in display mode -> 0x111D err=2, not a fake OK",
          mid == 0x111D and r.u8() == 2)
    r.done()

    say("attachments, --mail-attach take (UNTESTED LIVE; the pushes are stubbed here)")
    take = _args(mail_db=db, mail_attach="take")
    STORE.clear()
    STORE["gold"] = 100
    STORE["items"] = [[9001, 55, 1, 1]]
    PUSHES[:] = []
    mid, body, _ = _one(take, req_attach(gift["id"], 0))
    check("slot 0 -> 0x111C, gold 100 -> 600 and wallet_push (0x2024) fired",
          mid == 0x111C and body == b"" and STORE["gold"] == 600
          and ("wallet", 600) in PUSHES)
    mid, body, _ = _one(take, req_attach(gift["id"], 0))
    r = Reader(body)
    check("taking slot 0 twice -> 0x111D err=7 (already taken)",
          mid == 0x111D and r.u8() == 7)
    r.done()
    mid, body, _ = _one(take, req_attach(gift["id"], 1))
    bag = [p for p in PUSHES if p[0] == "bag"]
    check("slot 1 (item 77 x3) -> 0x111C, bag row appended, bag_layout_push (0x107A)",
          mid == 0x111C and STORE["items"][-1][1:] == [77, 1, 3]
          and STORE["items"][-1][0] == 9002 and bag and bag[-1][2][-1] == (9002, 77, 1, 3))
    mid, body, _ = _one(take, req_attach(gift["id"], 2))
    r = Reader(body)
    check("an empty slot -> 0x111D err=2", mid == 0x111D and r.u8() == 2)
    r.done()
    # the sender side of take mode: debit before the mail exists
    feworld._SESSION["account"], feworld._SESSION["charid"] = "acct:A", 7
    feworld._self_char = lambda a_: {"name": "ALICE", "charid": 7}
    STORE.clear()
    STORE["gold"] = 10
    PUSHES[:] = []
    mid, body, _ = _one(take, req_send("Bob", "poor", "x",
                                       attach=femail.format_attach([(0, 1, 0, 50)])))
    r = Reader(body)
    check("SEND attaching more gold than held -> 0xA052 err=7, nothing stored",
          mid == 0xA052 and r.u8() == 7 and STORE["gold"] == 10)
    r.done()
    STORE["items"] = [[7001, 77, 1, 5]]
    mid, body, _ = _one(take, req_send("Bob", "rich", "x",
                                       attach=femail.format_attach([(0, 1, 0, 4), (0, 2, 77, 2)])))
    check("SEND with 4 gold + item 77 x2 debits both and pushes",
          mid == 0xA051 and STORE["gold"] == 6 and STORE["items"] == [[7001, 77, 1, 3]]
          and ("wallet", 6) in PUSHES and any(p[0] == "bag" for p in PUSHES))

    say("--mail off")
    off = _args(mail_db=db, mail="off")
    out, log = _drive(off, [req_folder(0xA060, INBOX)])
    check("--mail off answers nothing and says so", out == [] and "NOT answered" in log)

    say("!mail verbs")
    ctx = feworld.Ctx(_Conn(), None, "ecb", False, a)
    sent = []
    real_send = feworld.send
    feworld.send = lambda *x, **k: sent.append(x)
    try:
        check("a non-mail line is not taken", femail.gm(ctx, "!party x") is False)
        check("!mail send needs NAME|SUBJECT|TEXT", femail.gm(ctx, "!mail send x") is True)
        before = femail.box_count(a, "acct:B/9", INBOX)
        check("!mail send NAME|SUBJECT|TEXT delivers from --mail-from",
              femail.gm(ctx, "!mail send Bob|Welcome|Enjoy the field.") is True
              and femail.box_count(a, "acct:B/9", INBOX) == before + 1
              and femail.box_page(a, "acct:B/9", INBOX, 0, 1)[0]["from_name"] == "GM")
        femail.gm(ctx, "!mail send Bob|Prize|here|gold=25|item=88x2")
        check("!mail send ...|gold=25|item=88x2 attaches both",
              femail.parse_attach(femail.box_page(a, "acct:B/9", INBOX, 0, 1)[0]["attach"])
              == [(0, 1, 0, 25), (0, 2, 88, 2)])
        check("!mail send to an unknown name is refused",
              femail.gm(ctx, "!mail send Zed|a|b") is True
              and femail.box_count(a, "acct:B/9", INBOX) == before + 2)
        check("!mail count / list / clear take the line",
              femail.gm(ctx, "!mail count") and femail.gm(ctx, "!mail list inbox")
              and femail.gm(ctx, "!mail clear sent")
              and femail.box_count(a, "acct:A/7", SENT) == 0)
        check("no client-bound frame was sent by any verb", sent == [])
    finally:
        feworld.send = real_send
    # through the seam: a `!mail` line in --gmcmd-file reaches EXT_GM
    gmf = os.path.join(tmp, "gm.txt")
    with open(gmf, "w", encoding="utf-8") as fh:
        fh.write("!mail send Bob|From the file|via gm_pump\n")
    seam = _args(mail_db=db, gmcmd_file=gmf)
    before = femail.box_count(a, "acct:B/9", INBOX)
    out, log = _drive(seam, [req_folder(0xA060, SENT)])
    check("a `!mail send` line in --gmcmd-file is delivered by gm_pump -> EXT_GM",
          femail.box_count(a, "acct:B/9", INBOX) == before + 1
          and "!mail send -> mail" in log)

    say("relay")
    # Bob's live session gets a log line on ITS thread: this thread plays Bob,
    # a helper thread plays ALICE sending.
    feworld._SESSION.clear()
    feworld._SESSION["account"], feworld._SESSION["charid"] = "acct:B", 9
    feworld._SESSION["in_field"] = True
    me = feworld._chat_room_join()
    feworld._chat_room_name("Bob")
    ready, go = threading.Event(), threading.Event()
    posted = []

    def alice():
        feworld._SESSION.clear()
        feworld._SESSION["account"], feworld._SESSION["charid"] = "acct:A", 7
        feworld._SESSION["in_field"] = True
        feworld._chat_room_join()
        feworld._chat_room_name("ALICE")
        ready.set()
        go.wait(5)
        n = feworld.ext_post("mail.new", {"to_owner": "acct:B/9", "from": "ALICE",
                                          "subject": "ping", "id": 1},
                             to=lambda s, nm: femail._owner(s.get("account", ""),
                                                            s.get("charid") or -1)
                             == "acct:B/9")
        posted.append(n)
        feworld._chat_room_leave()

    t = threading.Thread(target=alice)
    t.start()
    ready.wait(5)
    go.set()
    t.join(5)
    log = io.StringIO()
    real = sys.stdout
    sys.stdout = log
    try:
        feworld.ext_pump(ctx)
    finally:
        sys.stdout = real
    feworld._chat_room_leave()
    check("ext_post('mail.new') reaches exactly the recipient's session",
          posted == [1] and "NEW MAIL for this session: 'ping' from 'ALICE'" in log.getvalue(),
          repr((posted, log.getvalue()[-200:])))

    say("[fe_mail_test] %d checks" % len(CHECKS))
    say("[fe_mail_test] OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        say("[fe_mail_test] FAIL: %s" % e)
        sys.exit(1)
