#!/usr/bin/env python3
"""fe_ui_test.py -- Fantasy Earth's IN-GAME UI protocol, end to end, no client.

Run from services/:  python ../tools/fe_ui_test.py

WHAT THIS PINS, AND WHY IT EXISTS
---------------------------------
Every FE UI action is a request that registers exactly two acceptable replies,
and an unanswered one HANGS THAT DIALOG forever -- the same mechanism that
blocked the entire game for three sessions with the war-prep wait. As of
2026-08-25 feworld answers the blacklist (0x20A0/0x20A1/0x20A2), the character
distribution poll (0x2080) and the GM command channel's `comment` (0xF900).

The failure mode these replies have is silent and asymmetric: a body with one
field too many or too few does not error, it DESYNCHRONISES the client's stream
and everything after it decodes as garbage. So the checks below are not "did we
send something" -- each one decodes our reply with the exact primitive sequence
the client's own arm uses, and asserts the stream is fully consumed:

    0x5045dc0 -> 0x522f940   u8     +1
    0x5045e30 -> 0x522f970   u16    +2
    0x5045e60 -> 0x522f9b0   u32    +4
    0x5045f50 -> 0x522f5e0   cstr   NUL-terminated

    0x115F MSG_SET_BLACKLIST_OK        arm 0x050241e2  u32, cstr
    0x1161 MSG_REMOVE_BLACKLIST_OK     arm 0x05024413  u32
    0x1163 MSG_GET_BLACKLIST_OK        arm 0x05024490  u32 count, then
                                                       count x { u32, cstr }
    0x1123 MSG_GET_CHARACTER_          arm 0x05055b74 -> 0x05010ff0
           DISTRIBUTION_INFO_OK                        u16 count, then
                                                       count x { u8,u8,u8,u32 }
    0x111E MSG_SET_COMMENT_OK          arm 0x05055b24  NOTHING -- header-only
    0xF900 GM command                  0x0517e510      u16 len, then len bytes
    0x2095 MSG_ORGANIZE_ITEM_OK        arm 0x05058747  NOTHING -- header-only
    0x1179 MSG_ITEM_SORT_OK            arm 0x0505846d  NOTHING -- header-only
    ... and every other row of feworld.UI_HEADER_ONLY_OK

0x2095 and 0x1179 are the inventory STACK and SORT buttons, which hung live an
hour apart on 2026-08-25 -- the same failure twice, from two different buttons,
which is why the table exists instead of another pair of hand-written arms.

THE ONE INVARIANT THAT IS NOT ABOUT BYTES: 0x115F's u32 and the id in the 0x1163
list must be the SAME NUMBER for the same name. The client appends {id, name} to
its own vector (0x05170be0, stride 0x14) and MSG_REMOVE erases BY THAT ID
(0x05170cc0). Echoing the requester's charid -- which is what the request
carries -- would give every row the same id, and removing one would remove the
wrong one. That is the trap this file exists to keep shut.

Part 2 drives feworld.serve() itself with the crypto stubbed out, so the real
dispatch chain and the real arms run, not just the encoders.
"""
import io
import os
import re
import shutil
import struct
import tempfile
import threading
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import fenet     # noqa: E402
import feworld   # noqa: E402


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
    """Swap felobby's character store for a dict. The store itself has its own
    test (fe_store_test.py); mixing the two would make a failure here ambiguous.
    """
    STORE.clear()
    feworld._store_char_field = lambda a, k, v: (STORE.__setitem__(k, v), True)[1]
    feworld._load_char_field = lambda a, k, d=None: STORE.get(k, d)
    feworld._SESSION["account"] = "tester"
    feworld._SESSION["charid"] = 7


def _args(**kw):
    a = dict(unit_id="0", seq_mode="count", world_prefix=4, read_window=1.0,
             blacklist="on", distribution="on", distribution_rows=[],
             set_comment="ok", chat_echo="off", chat_line=None,
             chat_relay="off",
             gmcmd=None, gmcmd_file=None,
             ui_auto="ok", ui_auto_err=0, move_request="off",
             move_authority="off", unit_speed=None, unit_speed_repeat="off",
             npc=None, npc_base=1000, npc_walk=None, stat_probe="off",
             buildings=[], building_base=3000, building_hp=(100, 100),
             # `doors` is the MODE, `door_rows` the parsed --door rows. They
             # were one name until 2026-09-09, which silenced fet_area_portal.
             doors="off", door_rows=[], door_height="client",
             door_radius=15.0, door_default=10,
             spawn_xyz=(0.0, 0.0, 0.0),
             town_file="", spawns="off", spawns_max=12, monster_base=400,
             monster_level=1, events="on", shop_types="weapon=1,armor=2,item=3,ring=4",
             event_greeting=None, shop_price=100, shop_page=30,
             field_items="bag", field_equip="off", field_item_kind="0", bag_size=30,
             skill_grant="stored", equip_reflect="on", sell_price=50,
             bag_organize="on", skill_palette="request", room_exit="conversation",
             use_item="on",
             islands=5, groups=1, field_nations="", field_defenders="",
             gold=None, ring=None, total_score=None,
             validate_finish=10.0,
             war="offensive", war_fields="0,0,0,0", war_start="off",
             war_start_fields="0,0", war_deadline_ms=0, war_clock="telemetry",
             war_cycle="off", war_length_ms=600000, war_truce_ms=120000,
             war_result="desert", war_result_values="zero",
             war_result_rows=0,
             skill_list="on", skill_list_value=1, class_levels="self:30",
             acquire_skill="ok", status="off", status_resync="off",
             # the LEGACY flat SP budget (`--add-stats 0:N` minus one per
             # stored skill) is what part 3 pins; the level-earned ledger
             # (--sp-per-level, the 2026-09-11 default) is fe_prog_test's
             sp_per_level="off",
             status_nums=list(range(101, 110)), status_skills=[],
             status_id=1, status_name="Lex", status_sex=0, status_class=1,
             status_nation=1, status_str2="",
             probe_on_auth=False, capture_only=False)
    a.update(kw)
    return types.SimpleNamespace(**a)


# ---------------------------------------------------------------------------
# PART 1 -- the encoders, read back the way the client reads them
# ---------------------------------------------------------------------------
def part1():
    _stub_store()
    sent = []
    real_send = feworld.send
    def _send(conn, out, body, mode, be, echo=True, prefix=4):
        """(msgId, unitId, payload) out of [u32 unit][u16 id][payload]."""
        unit, mid = struct.unpack_from(">IH", body, 0)
        sent.append((mid, unit, body[6:]))

    feworld.send = _send
    try:
        args = _args()

        nid, rows, fresh = feworld.blacklist_add(args, "JIMMY")
        assert fresh and nid >= feworld.BLACKLIST_ID_BASE, (nid, fresh)
        body = struct.pack(">I", nid) + "JIMMY".encode("cp932") + bytes(1)
        r = Reader(body)
        assert r.u32() == nid
        assert r.cstr() == "JIMMY"
        r.done()

        # A repeat add must NOT allocate a second row: the client appends
        # whatever we answer, so two OKs for one name is two rows in the window.
        nid2, rows2, fresh2 = feworld.blacklist_add(args, "JIMMY")
        assert (nid2, fresh2, len(rows2)) == (nid, False, 1), (nid2, fresh2, rows2)

        nid3, _, fresh3 = feworld.blacklist_add(args, "BOB")
        assert fresh3 and nid3 == nid + 1, (nid3, nid)

        sent[:] = []
        feworld.blacklist_send(None, None, 0, False, args, who=7)
        mid, unit, body = sent[-1]
        assert mid == 0x1163, hex(mid)
        r = Reader(body)
        got = [(r.u32(), r.cstr()) for _ in range(r.u32())]
        r.done()
        assert got == [(nid, "JIMMY"), (nid3, "BOB")], got

        assert feworld.blacklist_remove(args, nid) is True
        assert [x["name"] for x in feworld.blacklist_rows(args)] == ["BOB"]
        assert feworld.blacklist_remove(args, 0xDEADBEEF) is False

        # 0x1123, empty and populated. The client clamps the count to 0xFF
        # (0x05010d70) and then reads exactly that many rows, so a longer list
        # would not truncate -- it would desynchronise. main() refuses one.
        for rows_in in ([], [(1, 2, 0x84, 7), (0, 0, 0x05, 9)]):
            payload = struct.pack(">H", len(rows_in))
            for a, b, cls, cid in rows_in:
                payload += struct.pack(">BBBI", a, b, cls, cid)
            r = Reader(payload)
            out = [(r.u8(), r.u8(), r.u8(), r.u32()) for _ in range(r.u16())]
            r.done()
            assert out == rows_in, out

        # 0xF900 matches the captured wire byte for byte:
        #     f9 00 | 00 15 | "comment Hello comment"
        sent[:] = []
        feworld.gm_command(None, None, 0, False, args, "comment Hello comment")
        mid, unit, body = sent[-1]
        assert mid == 0xF900, hex(mid)
        assert body == struct.pack(">H", 0x15) + b"comment Hello comment", \
            body.hex()

        # and the inner frame is the 6-byte world header that 0x0517e510's
        # `length - 6` assumes
        assert len(feworld.inner_msg(0xF900, body, 0)) - 6 == len(body)

        # 0x111E is header-only: its arm makes zero reader calls
        assert len(feworld.inner_msg(0x111E, b"", 0)) == 6

        # long text is clipped to the client's own 0x200 field buffer
        sent[:] = []
        feworld.gm_command(None, None, 0, False, args, "/" + "x" * 4000)
        body = sent[-1][2]
        assert struct.unpack_from(">H", body, 0)[0] == 0x1FF
        assert len(body) == 2 + 0x1FF
    finally:
        feworld.send = real_send
    print("[fe_ui_test] PART 1 ok -- every body round-trips through the "
          "client's own reader sequence")


# ---------------------------------------------------------------------------
# PART 2 -- feworld.serve() itself, crypto stubbed
# ---------------------------------------------------------------------------
class _Conn:
    def settimeout(self, t):
        pass

    def getpeername(self):
        return ("127.0.0.1", 1)


def part2():
    _stub_store()
    inbox, outbox = [], []
    saved = (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap,
             fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame)
    fenet.recv_frame = lambda conn: inbox.pop(0) if inbox else None
    fenet.bf_decrypt = lambda st, body, mode, be: body
    fenet.traffic_unwrap = lambda plain: (1, plain)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data
    def _capture(conn, mid, body, prefix):
        unit, msg = struct.unpack_from(">IH", body, 0)
        outbox.append((msg, unit, body[6:]))

    feworld.send_world_frame = _capture

    def q(mid, payload=b""):
        inbox.append((0x30, struct.pack(">H", mid) + payload))

    try:
        args = _args(distribution_rows=[(1, 2, 0x84, 7)], chat_echo="gm")
        q(0x20A0, struct.pack(">I", 7) + b"JIMMY\0")
        q(0x20A2, struct.pack(">I", 7))
        q(0x2080)
        q(0xF900, struct.pack(">H", 21) + b"comment Hello comment")
        q(0x201A, b"Lex\0Hi this is a chat message\0")
        q(0x209F, struct.pack(">I", 1))     # answers nothing, by design
        q(0x2002, struct.pack(">HH", 0, 0))  # ditto
        feworld.serve(_Conn(), args, None, None, 0, False)

        ids = [m for m, _, _ in outbox]
        assert ids == [0x115F, 0x1163, 0x1123, 0x111E, 0xF900], \
            [hex(i) for i in ids]

        r = Reader(outbox[1][2])
        assert r.u32() == 1
        nid, name = r.u32(), r.cstr()
        r.done()
        assert name == "JIMMY"
        assert struct.unpack_from(">I", outbox[0][2], 0)[0] == nid, (
            "0x115F and 0x1163 disagree about JIMMY's id -- REMOVE erases by "
            "that id, so this would erase the wrong row")

        assert outbox[2][2] == (struct.pack(">H", 1)
                                + struct.pack(">BBBI", 1, 2, 0x84, 7))
        assert outbox[3][2] == b"", outbox[3][2]
        # WARNING: THE PROFILE COMMENT MUST NOT LAND UNDER "comment".
        # felobby maps that key to record offset 0xE4, which is the
        # USE-PROHIBITED (suspension) comment -- so storing it there served
        # the player's own profile text back as a BAN NOTICE, and the live
        # client logged `banned comment JOJ`. This assertion used to pin
        # the buggy key, which is how it survived; it now pins BOTH halves.
        assert STORE["profile_comment"] == "Hello comment", STORE
        assert "comment" not in STORE, (
            "the profile comment must not reach felobby's 0xE4 suspension "
            "slot: %r" % STORE)

        echo = b"@Lex: Hi this is a chat message"
        assert outbox[4][2] == struct.pack(">H", len(echo)) + echo

        outbox[:] = []
        q(0x20A1, struct.pack(">II", 7, nid))
        feworld.serve(_Conn(), args, None, None, 0, False)
        assert outbox[0][0] == 0x1161
        assert outbox[0][2] == struct.pack(">I", nid)
        assert STORE["blacklist"] == [], STORE

        # --blacklist off is the A/B, and it must answer NOTHING
        outbox[:] = []
        q(0x20A0, struct.pack(">I", 7) + b"JIMMY\0")
        feworld.serve(_Conn(), _args(blacklist="off"), None, None, 0, False)
        assert outbox == [], outbox

        # ------------------------------------------------------------------
        # THE TWO LIVE HANGS OF 2026-08-25, byte for byte off the prod log.
        # Inventory STACK sent `11 4d 00 00 00 01`; inventory SORT sent `20 aa`
        # with no body at all. Both registered an OK the server never sent, and
        # both OK arms make zero stream reads -- so the reply is the 6-byte
        # header and NOTHING else. A body here would desynchronise the stream.
        # ------------------------------------------------------------------
        outbox[:] = []
        q(0x114D, struct.pack(">I", 1))      # STACK
        q(0x20AA)                            # SORT
        q(0x20AB)                            # manual sort, same family
        feworld.serve(_Conn(), _args(), None, None, 0, False)
        assert [m for m, _, _ in outbox] == [0x2095, 0x1179, 0x117B], \
            [hex(m) for m, _, _ in outbox]
        for _mid, _unit, _body in outbox:
            assert _body == b"", (hex(_mid), _body)

        # every row of the table answers, and answers header-only.
        # WARNING: 0x2033 is the ONE row that emits a second message -- the war-result
        # push that un-parks the dummy area -- so the sweep is run with
        # --war-result off. The follow-up is asserted on its own below; folding
        # it into "one reply per row" would make the row count stop meaning
        # "every row answers".
        # 0x2021/0x2022/0x2098 are NO LONGER generic: they read [u32 uid][u8]
        # and answer OK only for a uid this character actually owns, so a
        # bodyless one is an NG by design. They have their own test (part14).
        # 0x204C SELL joined them 2026-09-06 for the same reason -- it reads
        # [u32 shop][u32 uid][u16 count] and refuses a uid the bag does not
        # hold, so a bodyless one is an NG too. Its test is part16.
        # The four BANK rows (0x1149..0x114C) left the sweep 2026-09-11: they
        # are simulated now (fe_economy_test), and with no bank window open
        # they are deliberately NOT answered -- their reply arms deref the
        # window unchecked. `--bank off` still takes the generic arm.
        _equip_reqs = (0x2021, 0x2022, 0x2098, 0x204C,
                       0x1149, 0x114A, 0x114B, 0x114C)
        _sweep = [r for r in feworld.UI_HEADER_ONLY_OK if r not in _equip_reqs]
        outbox[:] = []
        for _req in _sweep:
            q(_req)
        feworld.serve(_Conn(), _args(war_result="off"), None, None, 0, False)
        assert len(outbox) == len(_sweep), len(outbox)
        for (_mid, _unit, _body), _req in zip(outbox, _sweep):
            assert _mid == feworld.UI_HEADER_ONLY_OK[_req][0], hex(_mid)
            assert _body == b"", (hex(_mid), _body)
        # and every equip row still HAS its table entry -- the branch reads the
        # OK/NG pair out of it rather than hardcoding them
        for _req in _equip_reqs:
            assert _req in feworld.UI_HEADER_ONLY_OK, hex(_req)

        # ---- THE SETTLEMENT DRAGS THE WAR RESULT BEHIND IT.
        # 0x1045 alone is what shipped before, and it left the player standing
        # in the dummy area forever: mode 0x0B state 8 spins on [scene+0x59]
        # and only the war-result window's close button ever sets that byte.
        # So the settlement must produce TWO messages, in this order.
        outbox[:] = []
        q(0x2033)
        feworld.serve(_Conn(), _args(), None, None, 0, False)
        assert [m for m, _, _ in outbox] == [0x1045, 0x1100], \
            [hex(m) for m, _, _ in outbox]
        assert outbox[0][2] == b"", "0x1045 is header-only"
        assert len(outbox[1][2]) == 39, (
            "0x1100 with 0 rows is 39 bytes: 1 + 4+4+2+4+4+4+4+2+2+2 + 4+2")

        # and --war-result off puts the hang back, deliberately and only here
        outbox[:] = []
        q(0x2033)
        feworld.serve(_Conn(), _args(war_result="off"), None, None, 0, False)
        assert [m for m, _, _ in outbox] == [0x1045], \
            [hex(m) for m, _, _ in outbox]

        # ---- FIELD OUT: 0x209A -> 0x1154, then 0x1157 when the timer ends.
        # 0x1154 only STARTS a countdown ([unit+0x138c] = 0x2710), so
        # answering OK alone moves the hang ten seconds later, not fixes it.
        outbox[:] = []
        feworld._SESSION.pop("validate_due", None)
        q(0x209A)
        feworld.serve(_Conn(), _args(validate_finish=0.01), None, None, 0, False)
        assert outbox[0][0] == 0x1154 and outbox[0][2] == b"", outbox
        assert "validate_due" in feworld._SESSION, "the FINISH must be armed"
        import time as _t
        _t.sleep(0.05)
        outbox[:] = []
        q(0x2023, bytes(13))          # any frame wakes the pump
        feworld.serve(_Conn(), _args(validate_finish=0.01), None, None, 0, False)
        assert outbox and outbox[0][0] == 0x1157, [hex(m) for m, _, _ in outbox]
        assert outbox[0][2] == b"", "0x1157 is header-only"
        assert "validate_due" not in feworld._SESSION, "must fire exactly once"

        # Cancel (0x209C) must disarm the pending FINISH
        outbox[:] = []
        q(0x209A)
        feworld.serve(_Conn(), _args(validate_finish=99), None, None, 0, False)
        assert "validate_due" in feworld._SESSION
        q(0x209C)
        feworld.serve(_Conn(), _args(validate_finish=99), None, None, 0, False)
        assert "validate_due" not in feworld._SESSION, "Cancel must disarm"

        # ---- equipment. These three USED to be plain header-only rows, and
        # that is exactly why an equip was accepted and nothing happened: the
        # OK arm 0x0503c390 clears one flag and reads no body. They now read
        # [u32 uid][u8 worn slot], persist the change and push the 0x107A that
        # actually moves the item -- so a uid this character does not own is an
        # NG, not an OK. The full round trip is part14.
        outbox[:] = []
        q(0x2021, bytes(5))
        q(0x2022, bytes(5))
        q(0x2098, bytes(5))
        feworld.serve(_Conn(), _args(), None, None, 0, False)
        got = [m for m, _, _ in outbox]
        assert got == [0x102B, 0x102D, 0x114F], [hex(m) for m in got]
        for _m, _u, _b in outbox:
            assert len(_b) == 4, "an NG carries its error code"

        # ---- UI_REPLY_BODY: the OK carries one u32 and its VALUE matters.
        # 0x201E throw-away names an object; 0x1025 feeds that u32 to an
        # object lookup, so the reply must ECHO the request, not invent an id.
        outbox[:] = []
        q(0x201E, struct.pack(">I", 0x1234) + bytes(2))
        feworld.serve(_Conn(), _args(), None, None, 0, False)
        assert outbox[0][0] == 0x1025, hex(outbox[0][0])
        assert outbox[0][2] == struct.pack(">I", 0x1234), outbox[0][2]

        # 0x2073 -> a page COUNT, not an echo
        outbox[:] = []
        q(0x2073, struct.pack(">I", 99))
        feworld.serve(_Conn(), _args(), None, None, 0, False)
        assert outbox[0][0] == 0x1102
        assert outbox[0][2] == struct.pack(">I", 1), outbox[0][2]

        # --ui-auto ng refuses through the same path
        outbox[:] = []
        q(0x201E, struct.pack(">I", 7) + bytes(2))
        feworld.serve(_Conn(), _args(ui_auto="ng", ui_auto_err=4),
                      None, None, 0, False)
        assert outbox[0][0] == 0x1026
        assert outbox[0][2] == struct.pack(">I", 4)
        # --ui-auto off restores the hang, --ui-auto ng carries the u32 the NG
        # arms actually read
        outbox[:] = []
        q(0x20AA)
        feworld.serve(_Conn(), _args(ui_auto="off"), None, None, 0, False)
        assert outbox == [], outbox
        outbox[:] = []
        q(0x20AA)
        feworld.serve(_Conn(), _args(ui_auto="ng", ui_auto_err=5),
                      None, None, 0, False)
        assert outbox[0][0] == 0x117A, hex(outbox[0][0])
        assert outbox[0][2] == struct.pack(">I", 5), outbox[0][2]

        # 0x20A6 MSG_MOVE_REQUEST is measured header-only but must stay SILENT
        # by default -- it is in the movement path, a separate investigation
        outbox[:] = []
        q(0x20A6, struct.pack(">I", 0))
        feworld.serve(_Conn(), _args(), None, None, 0, False)
        assert outbox == [], outbox
        outbox[:] = []
        q(0x20A6, struct.pack(">I", 0))
        feworld.serve(_Conn(), _args(move_request="ok"), None, None, 0, False)
        assert outbox[0][0] == 0x1168 and outbox[0][2] == b""
    finally:
        (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap,
         fenet.bf_encrypt, fenet.traffic_wrap,
         feworld.send_world_frame) = saved
    print("[fe_ui_test] PART 2 ok -- serve() dispatches every new id and the "
          "replies agree with each other")


# ---------------------------------------------------------------------------
# PART 3 -- 0x303E, the character status record that carries the skill list
# ---------------------------------------------------------------------------
def part3():
    """Decode 0x303E exactly as arm 0x05024a80 does, reader for reader.

    This is the message whose absence is why SP reads 0, Skill Points reads 0
    and every GET! is greyed: feworld had never sent it. The body is long and
    ends in a counted list, so an off-by-one field here would not show up as an
    error -- it would show up as a status screen full of plausible wrong numbers.
    """
    _stub_store()
    sent = []

    def _send(conn, out, body, mode, be, echo=True, prefix=4):
        unit, mid = struct.unpack_from(">IH", body, 0)
        sent.append((mid, unit, body[6:]))

    real_send = feworld.send
    feworld.send = _send
    try:
        args = _args(status="on", status_id=1, status_name="Lex",
                     status_sex=0, status_class=1, status_nation=1,
                     status_str2="",
                     status_nums=list(range(101, 110)),
                     status_skills=[3, 9, 27])
        feworld.status_push(None, None, 0, False, args)
        mid, unit, body = sent[-1]
        assert mid == 0x303E, hex(mid)

        r = Reader(body)
        assert r.u32() == 1                      # 0x05024b08  id
        assert r.cstr() == "Lex"                 # 0x05024b17  name
        assert r.u8() == 0                       # 0x05024b23  sex
        assert r.u16() == 1                      # 0x05024b32  class_type
        nine = [r.u32() for _ in range(9)]       # 0x05024b41..0x05024bad
        assert nine == list(range(101, 110)), nine
        assert r.u16() == 1                      # 0x05024bb9  nation
        assert r.cstr() == ""                    # 0x05024bc8  second string
        n = r.u32()                              # 0x05024bd4  COUNT
        assert n == 3, n
        assert [r.u32() for _ in range(n)] == [3, 9, 27]   # 0x05024bf0 loop
        r.done()

        # the empty list still has to carry its count, or the loop reads garbage
        args.status_skills = []
        sent[:] = []
        feworld.status_push(None, None, 0, False, args)
        r = Reader(sent[-1][2])
        r.u32(); r.cstr(); r.u8(); r.u16()
        [r.u32() for _ in range(9)]
        r.u16(); r.cstr()
        assert r.u32() == 0
        r.done()
    finally:
        feworld.send = real_send
    # ---- 0x2049 GET!: persist, then answer header-only, then serve it back
    outbox_local = []

    def _cap(conn, out, body, mode, be, echo=True, prefix=4):
        unit, mid = struct.unpack_from(">IH", body, 0)
        outbox_local.append((mid, unit, body[6:]))

    feworld.send = _cap
    a2 = _args(status="on", status_skills=[], acquire_skill="ok",
               status_resync="off")
    fresh, rows = feworld.acquire_skill(a2, 42)
    assert fresh and rows == [42], rows
    fresh2, rows2 = feworld.acquire_skill(a2, 42)
    assert not fresh2 and rows2 == [42], rows2       # deduped: no double row
    feworld.acquire_skill(a2, 7)
    outbox_local[:] = []
    feworld.status_push(None, None, 0, False, a2)
    r = Reader(outbox_local[-1][2])
    r.u32(); r.cstr(); r.u8(); r.u16()
    [r.u32() for _ in range(9)]
    r.u16(); r.cstr()
    n = r.u32()
    assert [r.u32() for _ in range(n)] == [42, 7], "0x303E must serve the "         "STORED skills, or the SP the player spent bought nothing"
    r.done()

    # ---- 0x1003 kind 0: the record that ACTUALLY writes Skill Points.
    # Decoded exactly as 0x0503aae0 does: u8 kind, u32 flags, and -- only when
    # flags bit 1 is set -- u32 mask, u32 mask2, then ONE i16 per set bit.
    # The applier's 17 field reads are all mask-gated, so the count of set bits
    # IS the count of i16 that follow. A bit set with no value behind it does
    # not draw a wrong number, it desynchronises the record mid-decode.
    a3 = _args(char_mask=0x5, char_fields={0: 5, 2: 77}, unit_id="1")
    outbox_local[:] = []
    feworld.send = _cap
    feworld.char_record_push(None, None, 0, False, a3)
    mid, unit, body = outbox_local[-1]
    assert mid == 0x1003, hex(mid)
    assert unit == 1, "the frame's leading u32 selects the target unit"
    r = Reader(body)
    assert r.u8() == 0                       # 0x0503ab2d  kind
    assert r.u32() == 2                      # 0x0503ac62  flags (bit1 = stats)
    mask, mask2 = r.u32(), r.u32()           # 0x0503ac95 / 0x0503aca1
    assert (mask, mask2) == (0x5, 0)
    vals = []
    for b in range(32):
        if mask & (1 << b):
            v = r.u16()
            vals.append(v - 0x10000 if v >= 0x8000 else v)
    r.done()
    assert vals == [5, 77], vals

    # a bit set with no value must still emit a slot, never a short record
    a3.char_fields = {}
    outbox_local[:] = []
    feworld.char_record_push(None, None, 0, False, a3)
    r = Reader(outbox_local[-1][2])
    r.u8(); r.u32(); m = r.u32(); r.u32()
    assert [r.u16() for _ in range(bin(m).count("1"))] == [0, 0]
    r.done()

    # ---- 0x2023 action 0, the move echo.
    # The client's own 28-byte 0x2023 IS a valid inbound action-0 record:
    # same layout, same units (outbound x1000 / x10, inbound x0.001 / x0.1
    # -- exact inverses), and the leading u32 it sends as 0 is the action
    # code for the move arm 0x04fea890. So the echo must be byte-identical,
    # or we are inventing numbers we have no measurement for.
    outbox_local[:] = []
    feworld.send = _cap
    telem = bytes(range(28))
    a6 = _args(move_authority='echo', unit_id='1')
    assert feworld.move_authority(None, None, 0, False, a6, telem) is True
    mid, unit, body = outbox_local[-1]
    assert mid == 0x2023, hex(mid)
    assert body == telem, 'the echo must not alter a single byte'

    # 'state1' forces ONLY the u16 STATE, which lives at offset 12 -- the
    # flag whose zero makes 0x04feadd8 skip the movement controller.
    # This assertion used to say offset 8, matching a bug in the code it was
    # checking: 8 is the tick's second u32, so the write corrupted the ticks
    # AND left the state at zero. A test that mirrors the implementation
    # instead of the client's read order pins the bug, not the contract --
    # which is why feworld now names every offset.
    outbox_local[:] = []
    a7 = _args(move_authority='state1', unit_id='1')
    assert feworld.move_authority(None, None, 0, False, a7, telem) is True
    body = outbox_local[-1][2]
    assert body[12:14] == bytes([0, 1]), body[12:14]
    assert body[:12] == telem[:12] and body[14:] == telem[14:]

    # 'walk' INVENTS a speed and a target ahead of the player. It must touch
    # exactly three fields -- state, the FIRST speed component, and Z -- and
    # leave the ticks, the second speed component, X, Y and the tail alone.
    outbox_local[:] = []
    a8 = _args(move_authority='walk', unit_id='1',
               move_speed=1.5, move_dist=5.0)
    assert feworld.move_authority(None, None, 0, False, a8, telem) is True
    body = outbox_local[-1][2]
    assert body[12:14] == bytes([0, 1]), 'state must be forced to 1'
    spd = struct.unpack_from('>h', body, 14)[0]
    assert spd == 1500, spd                      # 1.5 / 0.001
    z_in = struct.unpack_from('>h', telem, 22)[0]
    z_out = struct.unpack_from('>h', body, 22)[0]
    assert z_out == z_in + 50, (z_in, z_out)     # 5.0 / 0.1
    assert body[0:12] == telem[0:12], 'action and both ticks are the client s'
    assert body[16:18] == telem[16:18], 'the 2nd speed component is untouched'
    assert body[18:22] == telem[18:22], 'X and Y are untouched'
    assert body[24:28] == telem[24:28], 'the tail is untouched'

    # a body whose layout we have NOT decoded must never be echoed: its
    # leading u32 would be read as some other action by the 18-arm
    # sub-dispatcher at 0x04fea720.
    outbox_local[:] = []
    assert feworld.move_authority(None, None, 0, False, a6,
                                  bytes(14)) is False
    assert not outbox_local, 'a 14-byte 0x2023 must not be echoed'
    assert feworld.move_authority(None, None, 0, False,
                                  _args(move_authority='off'),
                                  telem) is False
    assert not outbox_local, 'off must send nothing'

    # ---- 0x1006 identity sub-record, THE FULL DEFAULT SUBMASK (0x2FF).
    # 0x05079d60 tests bit 0, then 1, then 2 ... and READS AS IT GOES, so the
    # fields must be laid down in ascending bit order and each at exactly the
    # width that reader advances [0x5338360] by. Widths, all pinned to the
    # reader itself rather than to the field they land in:
    #   bit 0x001 -> +0x389  0x5045f90  cstr    (0x522f5e0, to the NUL)
    #   bit 0x002 -> +0x3aa  0x5045dc0  u8
    #   bit 0x004 -> +0x3ab  0x5045dc0  u8
    #   bit 0x008 -> +0x3b0  0x5045e60  u32
    #   bit 0x010 -> +0x3b4  0x5045e30  u16
    #   bit 0x020 -> +0x3b6  0x5045dc0  u8
    #   bit 0x040 -> +0x3b7  0x5045dc0  u8
    #   bit 0x080 -> +0x3b8  0x5045dc0  u8
    #   bit 0x100 -> +0x94   0x5045e90  ONE BYTE  (`inc edx`, 0x05045ea2)
    #   bit 0x200 -> +0x3bc  0x5045e60  u32
    # The last two were left out of the record for weeks because their widths
    # were unknown. A wrong width here does not render a wrong number -- it
    # desynchronises the rest of the record, so this asserts full consumption.
    import feworld as fw5
    outbox_local[:] = []
    feworld.send = _cap
    a5 = _args(add_self="on", add_type=0, add_mask1=0x1, add_submask=0x2FF,
               add_mask2=0x1, add_parts="", add_stats={},
               spawn_xyz=(0.0, 0.0, 0.0), spawn_dir_xyz=(0.0, 0.0, 1.0),
               unit_id="1")
    fw5._SESSION["charid"] = 1
    saved5 = fw5._self_char
    fw5._self_char = lambda a: {"name": "Lex", "sex": 0, "look1": 2, "f24": 0,
                                "f28": 0, "look2": 4, "look3": 5, "look4": 1,
                                "army": 0, "force": 1}
    try:
        feworld.add_entities(None, None, 0, False, a5)
        mid, unit, body = outbox_local[-1]
        assert mid == 0x1006, hex(mid)
        r = Reader(body)
        assert r.u16() == 1                      # entity count
        assert r.u32() == 1                      # objectId
        assert r.u8() == 0                       # type 0 = Avatar
        assert r.u32() == 0x1                    # mask1: identity only
        assert r.u32() == 0x2FF                  # the submask itself
        assert r.cstr() == "Lex"                 # 0x001 -> +0x389
        assert r.u8() == 0                       # 0x002 -> +0x3aa sex
        assert r.u8() == 2                       # 0x004 -> +0x3ab
        assert r.u32() == 0                      # 0x008 -> +0x3b0
        # 0x010 -> +0x3b4 the RACE word: a stored 0 goes out as HUM 0x200
        # (2026-09-11) -- 0 cleared the bit the self ctor stamps, and the
        # target menu then offered 'Unsummon' instead of 'Summ' forever
        assert r.u16() == 0x200                  # 0x010 -> +0x3b4
        assert r.u8() == 4                       # 0x020 -> +0x3b6
        assert r.u8() == 5                       # 0x040 -> +0x3b7
        assert r.u8() == 1                       # 0x080 -> +0x3b8
        # WARNING: 0x2FF IS bits 0..7 PLUS BIT 9. Bit 8 (0x100, +0x94 ARMY) is
        # deliberately CLEAR -- 0x2FF = 0b10_1111_1111 -- because the client
        # picks its own army and overriding it is unmeasured. The army row
        # exists in _AVATAR_SUB with its width pinned, ready for the day
        # that gets measured; it must NOT appear on the wire today.
        assert 0x2FF & 0x100 == 0, "0x100 must stay clear in the default"
        assert r.u32() == 1                      # 0x200 -> +0x3bc NATION
        assert r.u32() == 0x1                    # mask2: position only
        # WARNING: and the facing must never be all zero when mask2 bit 2 is set --
        # that is the 2026-08-25 invisible-character bug, and main() refuses it.
        assert r.u32() == 0 and r.u32() == 0 and r.u32() == 0   # spawn pos
        assert r.u32() == 0                      # mask3: no parts
        r.done()
    finally:
        fw5._self_char = saved5

    # ---- 0x2035 CRYSTAL. Mask-driven like every other record here, and the
    # bit ORDER is the wire order: 0x04fe4dfa tests bit 0 (-> [obj+0x8a8])
    # before 0x04fe4e08 tests bit 1 (-> [obj+0x8ac], the CRYSTAL the Status
    # screen draws at 0x050e142e). We set only bit 1, so exactly one u32
    # follows the mask -- a second value here would be read as the NEXT
    # message.
    outbox_local[:] = []
    feworld.send = _cap
    ac = _args(crystal=1234, unit_id="1")
    feworld.crystal_push(None, None, 0, False, ac)
    mid, unit, body = outbox_local[-1]
    assert mid == 0x2035, hex(mid)
    assert unit == 1, unit          # the header id is what 0x2035 resolves
    r = Reader(body)
    assert r.u32() == 0x2           # mask: bit 1 only
    assert r.u32() == 1234          # -> [unit+0x8ac]
    r.done()

    # and unset must send NOTHING -- zero is a real crystal count, so "we never
    # served it" must not look like "you have none".
    outbox_local[:] = []
    feworld.crystal_push(None, None, 0, False, _args(crystal=None, unit_id="1"))
    assert not outbox_local, "unset --crystal must not put a record on the wire"

    # ---- 0x1006 entity type 8: a MONSTER (CFeClientNPCObject).
    # The type byte indexes a NINE-entry jump table at 0x0503bb0c and type 8's
    # arm (0x0503af8d) reads three fields before constructing, each width
    # pinned to the reader that advances the stream:
    #   0x5045e30  u16  -> ctor arg2 -> [unit+0x9fa]  the MODELTYPE, looked up
    #                      in dat\data_NPC_ModelType.dat by 0x51ab3f0
    #   0x5045e30  u16  -> ctor arg3 -> 0 gives [unit+0x24]=0xBBC, else 0xBBB
    #   0x5045dc0  u8   -> [unit+0x3ac]  (the LEVEL: ' Lv=%3d' at 0x050e466c)
    # and then -- 2026-08-27 correction -- falls into the SAME mask tail as
    # type 0 (every construction path jumps to 0x0503b111): [u32 mask1]
    # [u32 mask2] (bit0 = f32 x3 position, bit2 = f32 x3 facing) [u32 mask3].
    # The earlier version of this test asserted "no mask word" and the
    # reader would have taken mask1 from stale stack. Full consumption is
    # still the point; now it is full consumption of the RIGHT record.
    outbox_local[:] = []
    feworld.send = _cap
    a8 = _args(monster=["3:10:0:20"], monster_base=400, monster_kind=0,
               monster_level=1, npc=None, npc_base=1000)
    feworld.monster_push(None, None, 0, False, a8)
    mid, unit, body = outbox_local[-1]
    assert mid == 0x1006, hex(mid)
    assert unit == 400, unit            # the header u32 targets the object
    r = Reader(body)
    assert r.u16() == 1                 # entity count
    assert r.u32() == 400               # objectId == --monster-base
    assert r.u8() == 8                  # type 8 = CFeClientNPCObject
    assert r.u16() == 3                 # modeltype (gob_05 in the shipped table)
    assert r.u16() == 0                 # -> kind 0xBBC
    assert r.u8() == 1                  # -> [unit+0x3ac]
    assert r.u32() == 0                 # mask1: no sub-record, no stat block
    assert r.u32() == 0x5               # mask2: position (bit0) + facing (bit2)
    assert struct.unpack_from(">fff", body, r.i) == (10.0, 0.0, 20.0)
    r.i += 12                           # -> [unit+0x1c4]
    assert struct.unpack_from(">fff", body, r.i) == (0.0, 0.0, 1.0)
    r.i += 12                           # -> [unit+0x1e4], never all-zero
    assert r.u32() == 0                 # mask3: no parts
    r.done()

    # ---- and the guards. An unknown modeltype is NOT a clean refusal in the
    # client (0x050695eb reads the null row), and a colliding id has the
    # record applied to whatever object already owns it (0x0503b0f7, 'Npc %d
    # already exists'). Both must die at startup, not on screen.
    for bad in (dict(monster=["50:0:0:0"]),                       # id gap 48..51
                dict(monster=["300:0:0:0"]),                      # past the table
                dict(monster=["3:0:0:0"], monster_base=1000,
                     npc=["Bob:0:0:0"], npc_base=1000),           # == an --npc
                dict(monster=["3:0:0:0"], monster_base=7, unit_id="7"),  # == player
                dict(monster=["3:0:0:0", "4:0:0:0"], monster_base=400,
                     npc=["Bob"], npc_base=401)):                 # 2nd == npc
        kw = dict(monster_base=400, monster_kind=0, monster_level=1,
                  npc=None, npc_base=1000)
        kw.update(bad)
        try:
            feworld.monster_push(None, None, 0, False, _args(**kw))
        except SystemExit:
            pass
        else:
            raise AssertionError("monster_push accepted %r" % (bad,))

    # ---- placement: 0x2023 action 0 is now OPT-IN (--monster-walk on). The
    # ADD carries the position, so the default sends nothing per tick...
    outbox_local[:] = []
    feworld.send = _cap
    feworld.monster_place_push(None, None, 0, False, a8, bytes(28))
    assert not outbox_local, "monster_place_push must be silent unless --monster-walk on"
    # ...and with it on, the same 28-byte record as --npc-walk, speed zero.
    a8w = _args(monster=["3:10:0:20"], monster_base=400, monster_kind=0,
                monster_level=1, monster_walk="on", npc=None, npc_base=1000)
    feworld.monster_place_push(None, None, 0, False, a8w, bytes(28))
    mid, unit, body = outbox_local[-1]
    assert mid == 0x2023, hex(mid)
    assert unit == 400, unit
    assert len(body) == 28, len(body)
    import struct as _st
    assert _st.unpack_from(">I", body, 0)[0] == 0        # action 0 = move
    assert _st.unpack_from(">H", body, 12)[0] == 0       # state 0
    assert _st.unpack_from(">hh", body, 14) == (0, 0)    # speed: standing
    assert _st.unpack_from(">hhh", body, 18) == (100, 0, 200)   # x10 fixed point

    # ---- 0x1006 MSG_ADD carrying the STAT BLOCK (mask1 bit 1).
    # This is the SAFE route to [unit+0x498]: the same per-entity decoder
    # 0x0503aae0 that 0x1003 uses, but the identity sub-record travels in the
    # same record, so the model rebuild has what it needs. 0x1003 sent alone
    # released the models and crashed the client on field load.
    import feworld as fw
    outbox_local[:] = []
    feworld.send = _cap
    STORE["skills"] = []      # the earlier acquire test left two in the ledger
    a4 = _args(add_self="on", add_type=0, add_mask1=0x1, add_submask=0x7,
               add_mask2=0x1, add_parts="", add_stats={0: 5},
               spawn_xyz=(0.0, 0.0, 0.0), spawn_dir_xyz=(0.0, 0.0, 0.0),
               unit_id="1")
    fw._SESSION["charid"] = 1
    saved_self = fw._self_char
    fw._self_char = lambda a: {"name": "Lex", "sex": 0, "look1": 2}
    try:
        feworld.add_entities(None, None, 0, False, a4)
        assert outbox_local, "MSG_ADD sent nothing"
        mid, unit, body = outbox_local[-1]
        assert mid == 0x1006, hex(mid)
        r = Reader(body)
        assert r.u16() == 1                    # entity count
        r.u32()                                # objectId
        assert r.u8() == 0                     # type 0 = Avatar
        mask1 = r.u32()
        assert mask1 & 0x2, "the code must SET bit 1 when stats are supplied"
        if mask1 & 0x1:                        # identity sub-record
            sub = r.u32()
            for bit, code in ((0x1, "cstr"), (0x2, "u8"), (0x4, "u8")):
                if sub & bit:
                    if code == "cstr":
                        r.cstr()
                    else:
                        r.u8()
        if mask1 & 0x2:                        # THE STAT BLOCK
            sm, sm2 = r.u32(), r.u32()
            assert (sm, sm2) == (0x1, 0), (sm, sm2)
            v = r.u16()
            assert (v - 0x10000 if v >= 0x8000 else v) == 5
        m2 = r.u32()
        if m2 & 0x1:
            for _ in range(3):
                r.u32()                        # position, 3 f32
        if m2 & 0x4:
            for _ in range(3):
                r.u32()
        assert r.u32() == 0                    # mask3, no parts
        r.done()

        # ---- BIT 0 IS A BUDGET, NOT A BALANCE.
        # Serving the configured value flat would hand the player a fresh five
        # skill points at EVERY field entry -- learn five, relaunch, learn five
        # more, forever. That is the TM 10000-gold minting bug's exact shape.
        # What goes on the wire must be budget MINUS what the store already
        # holds, because the store is the ledger.
        STORE["skills"] = [270, 275]          # two already learned
        a4.add_stats = {0: 5}
        outbox_local[:] = []
        feworld.add_entities(None, None, 0, False, a4)
        r = Reader(outbox_local[-1][2])
        r.u16(); r.u32(); r.u8()
        m1 = r.u32()
        if m1 & 0x1:
            sub = r.u32()
            for bit, code in ((0x1, "cstr"), (0x2, "u8"), (0x4, "u8")):
                if sub & bit:
                    if code == "cstr":
                        r.cstr()
                    else:
                        r.u8()
        assert m1 & 0x2
        r.u32(); r.u32()
        got = r.u16()
        assert got == 3, "budget 5 - 2 learned must serve 3, got %d" % got

        # and it must never go negative
        STORE["skills"] = list(range(100, 109))    # nine learned, budget five
        outbox_local[:] = []
        feworld.add_entities(None, None, 0, False, a4)
        r = Reader(outbox_local[-1][2])
        r.u16(); r.u32(); r.u8()
        m1 = r.u32()
        if m1 & 0x1:
            sub = r.u32()
            for bit, code in ((0x1, "cstr"), (0x2, "u8"), (0x4, "u8")):
                if sub & bit:
                    if code == "cstr":
                        r.cstr()
                    else:
                        r.u8()
        r.u32(); r.u32()
        assert r.u16() == 0, "overspend must clamp to 0, never wrap negative"
        STORE["skills"] = []

        # acquire_skill must report the STORE's answer, not its intent
        saved_store = feworld._store_char_field
        feworld._store_char_field = lambda a, k, v: False   # write fails
        ok, _rows = feworld.acquire_skill(a4, 999)
        assert ok is False, "a failed store write must NOT report success"
        feworld._store_char_field = saved_store
        STORE["skills"] = []

        # no stats -> bit 1 CLEAR and no block emitted, or the record desyncs
        a4.add_stats = {}
        outbox_local[:] = []
        feworld.add_entities(None, None, 0, False, a4)
        r = Reader(outbox_local[-1][2])
        r.u16()
        r.u32()
        r.u8()
        assert not (r.u32() & 0x2), "bit 1 must not be set with no values"
    finally:
        fw._self_char = saved_self

    print("[fe_ui_test] PART 3 ok -- 0x303E round-trips through arm "
          "0x05024a80's reader sequence, and 0x2049 persists into it")


# ---------------------------------------------------------------------------
# PART 4 -- 0x1100 WAR RESULT, the message that un-parks the DUMMY AREA
# ---------------------------------------------------------------------------
def part4():
    """Decode 0x1100 with the arm's OWN reader order, not the builder's.

    The point of writing the expected sequence out longhand here, instead of
    calling war_result_body and comparing, is that a test which mirrors the
    implementation pins the bug rather than the contract -- which is exactly how
    the 0x2023 state-at-offset-8 bug survived its own test earlier today. What
    follows is transcribed from 0x05114870, call by call.
    """
    _stub_store()
    sent = []
    saved = feworld.send

    def _send(conn, out, body, mode, be, echo=True, prefix=4):
        unit, mid = struct.unpack_from(">IH", body, 0)
        sent.append((mid, unit, body[6:]))

    feworld.send = _send
    try:
        a = _args()
        feworld.war_result_push(None, None, 0, False, a)
        assert len(sent) == 1, "one 0x1100 per settlement, got %d" % len(sent)
        mid, unit, body = sent[-1]
        assert mid == 0x1100, "expected 0x1100, got 0x%04X" % mid

        r = Reader(body)
        kind = r.u8()                     # 0x0511489f  -> [win+0xAB4]
        assert kind == 0, "desert is kind 0 (WarResultDesert)"
        # WARNING: the DEFAULT is zeros now that 0x1100 is live and mapped. Sentinels
        # answered "which field is which"; leaving them on serves a real player
        # an invented Score they cannot tell from a real one.
        for _ in range(2):
            assert r.u32() == 0
        r.u16(); r.u32(); r.u32(); r.u32(); r.u32(); r.u16(); r.u16()
        n = r.u16()
        assert n == 0
        r.u32(); r.u16()
        r.done()

        # and with the probe explicitly on, the live-measured map holds
        sent[:] = []
        feworld.war_result_push(None, None, 0, False,
                                _args(war_result_values="sentinel"))
        r = Reader(sent[-1][2])
        assert r.u8() == 0
        assert r.u32() == 101             # 0x051148ab  -> [win+0xABC]  Attack
        assert r.u32() == 102             # 0x051148b7  -> [win+0xAB8]  Defend
        assert r.u16() == 103             # 0x051148c3  0x05045EC0 -- UNDRAWN
        assert r.u32() == 104             # 0x051148cf  (slot 0x48) -- UNDRAWN
        assert r.u32() == 105             # 0x051148db  -> [win+0xAC0]  Score
        assert r.u32() == 106             # 0x051148e7  -> [win+0xAD4]  Exp
        assert r.u32() == 107             # 0x051148f3  -> [win+0xAC4]  Crystal
        assert r.u16() == 108             # 0x051148ff  -> [win+0xACC]  Destroyed
        assert r.u16() == 109             # 0x0511490b  -> [win+0xAC8]  Built
        n = r.u16()                       # 0x05114917  -> [win+0xAD8] COUNT
        assert n == 0, "default rows is 0"
        for _ in range(n):
            r.u32(); r.u32(); r.u32(); r.u32()
        assert r.u32() == 110             # 0x051149bc  -> [win+0xAD0]  Rewards
        assert r.u16() == 111             # 0x051149c8  (slot 0x42) -- UNDRAWN
        r.done()
        # the undrawn three must still be SENT: they are parsed, and dropping
        # one shifts every field after it by its own width
        assert feworld.WAR_RESULT_FIELDS.count("(undrawn u16)") == 2
        assert feworld.WAR_RESULT_FIELDS.count("(undrawn u32)") == 1
        assert len(feworld.WAR_RESULT_FIELDS) == 11

        # THE OVERRUN GUARD, and it is the only way this message can corrupt
        # memory. The window's ctor runs the eh vector constructor iterator with
        # count = 2 (0x05113D07) over 0x4FC-byte elements at [win+0x80], inside
        # a 0xADC allocation: 0x80 + 3*0x4FC = 0xF74. The decode loop has no
        # bounds check of its own, so the clamp here IS the bound.
        for asked, want in ((0, 0), (1, 1), (2, 2), (3, 2), (99, 2), (-1, 0)):
            b = feworld.war_result_body(0, feworld.WAR_RESULT_SENTINELS, asked)
            rr = Reader(b)
            rr.u8(); rr.u32(); rr.u32(); rr.u16(); rr.u32(); rr.u32()
            rr.u32(); rr.u32(); rr.u16(); rr.u16()
            got = rr.u16()
            assert got == want, ("rows=%r must clamp to %d, got %d -- 3 rows "
                                 "writes 0x4FC bytes past a 0xADC object"
                                 % (asked, want, got))
            for _ in range(got):
                rr.u32(); rr.u32(); rr.u32(); rr.u32()
            rr.u32(); rr.u16()
            rr.done()

        # rows carry four all-zero masks each, which is what makes a row read
        # NOTHING beyond them (0x05075980/A80/AD0/B80 are all mask-gated).
        b = feworld.war_result_body(2, feworld.WAR_RESULT_SENTINELS, 2)
        rr = Reader(b)
        rr.u8(); rr.u32(); rr.u32(); rr.u16(); rr.u32(); rr.u32()
        rr.u32(); rr.u32(); rr.u16(); rr.u16()
        assert rr.u16() == 2
        for _ in range(2):
            assert (rr.u32(), rr.u32(), rr.u32(), rr.u32()) == (0, 0, 0, 0), (
                "a non-zero mask promises fields we do not send, and the "
                "record desynchronises mid-decode")
        rr.u32(); rr.u16()
        rr.done()

        # the kind is what selects WarResultDesert/Win/Lose, range-checked at
        # 0x05114CF7 -- 0..2 only, so the choices must not grow silently
        assert set(feworld.WAR_RESULT_KINDS.values()) == {0, 1, 2}
        for name, want in feworld.WAR_RESULT_KINDS.items():
            sent[:] = []
            feworld.war_result_push(None, None, 0, False, _args(war_result=name))
            assert Reader(sent[-1][2]).u8() == want

        # 'off' must send NOTHING -- the knob that restores the known hang has
        # to actually be a knob
        sent[:] = []
        a_off = _args(war_result="off")
        assert a_off.war_result == "off"

        # sentinels are available but are NOT the default any more
        sent[:] = []
        feworld.war_result_push(None, None, 0, False, _args())
        r = Reader(sent[-1][2])
        r.u8()
        assert r.u32() == 0 and r.u32() == 0
        assert len(set(feworld.WAR_RESULT_SENTINELS)) == 11, (
            "the sentinels must be DISTINCT or one screenshot names nothing")
    finally:
        feworld.send = saved

    print("[fe_ui_test] PART 4 ok -- 0x1100 decodes through arm 0x05114870's "
          "reader order and the row count cannot overrun the window")


# ---------------------------------------------------------------------------
# PART 5 -- 0x1075, the ACQUIRED SKILL LIST (the relaunch amnesia)
# ---------------------------------------------------------------------------
def part5():
    """Decode 0x1075 the way 0x05051E20 -> 0x05052180 read it.

    Transcribed from the arms, not from the builder:

        [u32 mask]            0x05051E2D
          mask & 8 ->         0x05052180
            [u16 count]       0x050521A6
            count x           0x050521B4 .. 0x050521F4
              [u16 idx]       0x050521BB
              [u8  value]     0x050521C7
              -> [unit + idx*2 + 0xA14] = value   (0x050521E0, NO bounds check)
    """
    _stub_store()
    sent = []
    saved = feworld.send

    def _send(conn, out, body, mode, be, echo=True, prefix=4):
        unit, mid = struct.unpack_from(">IH", body, 0)
        sent.append((mid, unit, body[6:]))

    feworld.send = _send
    # `self:` in --class-levels resolves through the stored character, so the
    # roster has to be stubbed here too -- look1 = 2 is the class the live
    # character actually has, which is the whole point of this part.
    saved_self = feworld._self_char
    feworld._self_char = lambda a: {"name": "Lex", "sex": 0, "look1": 2}
    try:
        a = _args()
        assert feworld.self_class_id(a) == 2

        # the five that were learned live on 2026-08-25 and did not come back
        STORE["skills"] = [270, 275, 285, 305, 330]
        sent[:] = []
        feworld.skill_list_push(None, None, 0, False, a)
        mid, unit, body = sent[-1]
        assert mid == 0x1075, "expected 0x1075, got 0x%04X" % mid
        r = Reader(body)
        # WARNING: BIT ORDER IS WIRE ORDER: 0x05051E20 tests bit 0 (the class levels)
        # before bit 3 (the skills), so the class block comes FIRST. Emitting
        # them the other way round decodes both as garbage with no error.
        assert r.u32() == feworld.CLASS_MASK_BIT | feworld.SKILL_MASK_BIT
        n_cls = r.u8()
        assert n_cls == 1
        # WARNING: the index is the STORED character's class, not a constant. The
        # first version hardcoded 1 while the character's look1 is 2, so it
        # wrote a level nothing read and the theory went untested.
        assert (r.u8(), r.u8()) == (2, 30), (
            "`self` must resolve to the stub character's look1 (2)")
        n = r.u16()
        assert n == 5, n
        got = [(r.u16(), r.u8()) for _ in range(n)]
        r.done()
        assert got == [(270, 1), (275, 1), (285, 1), (305, 1), (330, 1)], got

        # with no class block, bit 0 must be CLEAR -- a bit set with nothing
        # behind it desynchronises the record
        sent[:] = []
        feworld.skill_list_push(None, None, 0, False, _args(class_levels=""))
        r = Reader(sent[-1][2])
        assert r.u32() == feworld.SKILL_MASK_BIT, (
            "only bit 3 may be set -- bits 0/1/2 promise three other arrays "
            "(0x05051E80 / 0x05052200 / 0x05051F10) and a bit set with no "
            "block behind it desynchronises the record")
        assert r.u16() == 5
        for _ in range(5):
            r.u16(); r.u8()
        r.done()

        # class rows are dropped, never clamped, for the same reason skill ids
        # are -- a level on a class that does not exist is a silent lie
        rows, bad = feworld.class_level_rows("1:30, 255:1, 256:1, 1:256, junk")
        assert rows == [(1, 30), (255, 1)], rows
        assert bad == ["256:1", "1:256", "junk"], bad

        # `self` resolves, and is DROPPED rather than guessed when there is no
        # stored character -- a level on the wrong class is silent, not an error
        assert feworld.class_level_rows("self:7", 2)[0] == [(2, 7)]
        rows, bad = feworld.class_level_rows("self:7", None)
        assert rows == [] and len(bad) == 1 and bad[0].startswith("self:7"), (rows, bad)

        # empty is a valid list and must still be a well-formed record
        STORE["skills"] = []
        sent[:] = []
        feworld.skill_list_push(None, None, 0, False, _args(class_levels=""))
        r = Reader(sent[-1][2])
        assert r.u32() == feworld.SKILL_MASK_BIT
        assert r.u16() == 0
        r.done()

        # ---- THE UNBOUNDED INDEX, which is the only way this corrupts memory.
        # 0x05052180 writes [unit + idx*2 + 0xA14] with no range check, while
        # every one of the client's OWN accessors checks `< 0x4A4` (0x0507D608,
        # 0x0507D62A, 0x050D4D23). The array is 0x252 dwords -- 0x4A4 u16 --
        # cleared at 0x0507D5B6, ending at 0x135C, immediately before
        # [unit+0x1360] and [unit+0x138C].
        assert feworld.SKILL_ARRAY_MAX == 0x4A4
        body, rows, dropped = feworld.skill_list_body(
            [0, 1, 0x4A3, 0x4A4, 0x4A5, 0xFFFF, -1, 270])
        assert rows == [0, 1, 0x4A3, 270], rows
        assert dropped == [0x4A4, 0x4A5, 0xFFFF, -1], dropped
        r = Reader(body)
        assert r.u32() == feworld.SKILL_MASK_BIT   # no class rows passed
        assert r.u16() == len(rows)
        for want in rows:
            assert r.u16() == want
            r.u8()
        r.done()

        # DROPPED, not clamped: folding 0xFFFF onto 0x4A3 would mark a skill the
        # player does not have, which renders as data instead of as a hole.
        assert 0x4A3 not in feworld.skill_list_body([0xFFFF])[1]

        # the value is a knob because what it MEANS is unmeasured
        STORE["skills"] = [7]
        sent[:] = []
        feworld.skill_list_push(None, None, 0, False,
                                _args(skill_list_value=3, class_levels=""))
        r = Reader(sent[-1][2])
        r.u32(); r.u16(); assert r.u16() == 7
        assert r.u8() == 3
        r.done()
        STORE["skills"] = []
    finally:
        feworld.send = saved
        feworld._self_char = saved_self

    print("[fe_ui_test] PART 5 ok -- 0x1075 decodes through 0x05051E20's mask "
          "and 0x05052180's loop, and an out-of-range id cannot reach the wire")


def part6():
    """Decode chat the way arm 0x05053cbe reads it: [cstr name][cstr text].

    Transcribed from the arm, not from the builder:

        0x05053cd7  read cstr -> [esp+0xd5]     the SPEAKER NAME
        0x05053ce6  read cstr -> [esp+0x54]     the TEXT
        0x05053d5f  0x05170e10(name)            blacklist; a HIT DROPS the line
        0x05053dcf  0x05129890(id, name, text)  the chat log window

    WARNING: The reader is 0x05045f50 -> 0x0522f5e0, an UNBOUNDED strcpy into a
    stack frame whose text slot is 0x81 bytes ([esp+0x54] up to the name buffer
    at [esp+0xd5]). The clamp is not cosmetic and it is asserted here: a relay
    is a remote write into every client that receives it.
    """
    _stub_store()
    sent = []
    saved = feworld.send

    def _send(conn, out, body, mode, be, echo=True, prefix=4):
        unit, mid = struct.unpack_from(">IH", body, 0)
        sent.append((mid, unit, body[6:]))

    feworld.send = _send
    try:
        a = _args(chat_line=["0x201a|Bob|hello there"])
        feworld.chat_push(None, None, 0, False, a)
        mid, _unit, body = sent[-1]
        assert mid == 0x201A, "expected 0x201A, got 0x%04X" % mid
        r = Reader(body)
        assert r.cstr() == "Bob"
        assert r.cstr() == "hello there"
        r.done()

        # every id the dispatcher routes to a chat arm, and the string count
        # each arm actually reads -- 0x2067 reads THREE (0x05053e41/0x05053e50/
        # 0x05053e5f), the other five read two.
        for mid_, (_name, want) in sorted(feworld._CHAT_IDS.items()):
            sent[:] = []
            parts = ["N%d" % i for i in range(want - 1)] + ["body"]
            feworld.chat_send(None, None, 0, False, _args(), mid_, parts)
            got_id, _u, b = sent[-1]
            assert got_id == mid_, (got_id, mid_)
            r = Reader(b)
            assert [r.cstr() for _ in range(want)] == parts
            r.done()

        # THE CLAMP. Both slots are fixed on the client's stack; a long line
        # must be cut HERE, not by the strcpy on the far side.
        sent[:] = []
        feworld.chat_send(None, None, 0, False, _args(), 0x201A,
                          ["N" * 200, "T" * 500])
        r = Reader(sent[-1][2])
        assert len(r.cstr()) == feworld._CHAT_NAME_MAX
        assert len(r.cstr()) == feworld._CHAT_TEXT_MAX
        r.done()
        assert feworld._CHAT_TEXT_MAX < 0x81, (
            "the client's text slot is 0x81 bytes INCLUDING the terminator")

        # --chat-echo say relays on the id it arrived on. The doubling on
        # screen is expected: the client echoed its own line locally first.
        sent[:] = []
        feworld.chat_send(None, None, 0, False, _args(chat_echo="say"),
                          0x2065, ["Lex", "army test"])
        assert sent[-1][0] == 0x2065

        # a malformed --chat-line is refused, not half-sent
        assert feworld.parse_chat_line("nope") is None
        assert feworld.parse_chat_line("zz|a|b") is None
        assert feworld.parse_chat_line("0x201a|a|b") == (0x201A, ["a", "b"])

        # --chat-echo self: the retail shape, speaker == the player's own unit
        # id (0x051334f0 never logs its own /say; the log line is the echo).
        sent[:] = []
        feworld.chat_send(None, None, 0, False, _args(unit_id="7"), 0x201A,
                          ["Lex", "hi"], speaker=7)
        assert sent[-1][:2] == (0x201A, 7), sent[-1]

        # THE RELAY. Two fake peers in the room next to this thread: one in
        # the field, one still on the map. A /say from here reaches only the
        # in-field one; a /tell reaches only the peer that signs as TARGET.
        import queue as _queue
        feworld._chat_room_join()
        try:
            feworld._CHAT_ROOM["peer-field"] = {
                "q": _queue.Queue(), "session": {"in_field": True},
                "name": "Bob"}
            feworld._CHAT_ROOM["peer-map"] = {
                "q": _queue.Queue(), "session": {}, "name": "Eve"}
            assert feworld.chat_relay(0x201A, ["Lex", "hello"]) == 1
            assert feworld._CHAT_ROOM["peer-map"]["q"].empty()
            assert feworld._CHAT_ROOM["peer-field"]["q"].get_nowait()[:2] == \
                (0x201A, ["Lex", "hello"])
            # /tell = [TARGET][SENDER][TEXT]: only Bob's session gets it
            assert feworld.chat_relay(0x2067, ["Bob", "Lex", "psst"]) == 1
            assert feworld.chat_relay(0x2067, ["Zed", "Lex", "psst"]) == 0
            assert feworld._CHAT_ROOM["peer-field"]["q"].get_nowait()[:2] == \
                (0x2067, ["Bob", "Lex", "psst"])
            # the sender never puts a line in its OWN queue
            me = feworld._CHAT_ROOM[threading.get_ident()]
            assert me["q"].empty()
            # WARNING: SCOPE (SE flow09): /say and /all reach the sender's FIELD,
            # /army the sender's nation there, /tell anyone. Until 09-11 every
            # line reached every session in every field.
            feworld._CHAT_ROOM["peer-field"]["session"]["field"] = 39
            feworld._CHAT_ROOM["peer-field"]["nation"] = 2
            feworld._CHAT_ROOM["peer-away"] = {
                "q": _queue.Queue(), "session": {"in_field": True, "field": 5},
                "name": "Ann", "nation": 2}
            assert feworld.chat_relay(0x201B, ["Lex", "all"], field=39, nation=2) == 1
            assert feworld._CHAT_ROOM["peer-away"]["q"].empty(), \
                "/all reached a player in another field"
            feworld._CHAT_ROOM["peer-field"]["q"].get_nowait()
            assert feworld.chat_relay(0x2065, ["Lex", "army"], field=39, nation=1) == 0, \
                "/army reached another nation"
            assert feworld.chat_relay(0x2065, ["Lex", "army"], field=39, nation=2) == 1
            feworld._CHAT_ROOM["peer-field"]["q"].get_nowait()
            assert feworld.chat_relay(0x2067, ["Ann", "Lex", "psst"], field=39, nation=2) == 1, \
                "/tell must reach another field"
            feworld._CHAT_ROOM["peer-away"]["q"].get_nowait()
            del feworld._CHAT_ROOM["peer-away"]
            # DRAIN on the receiving thread: speaker unit id 0, both strings
            # clamped, and a blacklisted speaker never reaches the wire.
            STORE["blacklist"] = [{"id": 5, "name": "Mallory"}]
            me["q"].put((0x201B, ["Mallory", "spam"]))
            me["q"].put((0x201B, ["N" * 200, "T" * 500]))
            sent[:] = []
            a = _args(chat_relay="on")
            feworld.chat_pump(None, None, 0, False, a)
            assert len(sent) == 1, [x[:2] for x in sent]
            mid, unit, body = sent[0]
            assert (mid, unit) == (0x201B, 0)
            r = Reader(body)
            assert len(r.cstr()) == feworld._CHAT_NAME_MAX
            assert len(r.cstr()) == feworld._CHAT_TEXT_MAX
            r.done()
            # --chat-relay off leaves the queue alone
            me["q"].put((0x201A, ["Bob", "x"]))
            sent[:] = []
            feworld.chat_pump(None, None, 0, False, _args())
            assert sent == [] and not me["q"].empty()
        finally:
            feworld._CHAT_ROOM.pop("peer-field", None)
            feworld._CHAT_ROOM.pop("peer-map", None)
            feworld._chat_room_leave()
            STORE.pop("blacklist", None)
    finally:
        feworld.send = saved

    print("[fe_ui_test] PART 6 ok -- chat decodes through 0x05053cbe's two "
          "cstr reads, 0x2067's three, and the strcpy clamp holds")


# ---------------------------------------------------------------------------
# PART 7 -- THE WAR CLOCK AND THE WAR CYCLE (2026-08-27, war-clock).
#
# Everything here is about ORDER and UNITS on the wire, read off the arms:
#   0x2023 out  0x0515dd85: [u32 0][u32 HI][u32 LO] -- the client's ms clock
#   0x1148 in   0x05055edb: [u32 HI][u32 LO] -> [0x5336ec4]:[0x5336ec0]
#   0x1015/0x1016/0x1018 deadlines: HIGH first, then LOW (0x5045e60's
#   destination is arg1 after its own push esi, so the FIRST read fills the
#   slot pushed LAST; the 0x1018 arm at 0x05118263-0x0511829b is the proof)
#   0x1016 body: [u32 key][u32 HI][u32 LO], key 0x7FFFFFFF = "no record"
#   (0x05118125 pre-load, 0x051181e4 compare) -- anything else is a lookup
#   whose NULL result 0x5118880 dereferences.

def part7():
    S = feworld._SESSION
    SENT = []
    real_send = feworld.send
    feworld.send = lambda conn, outbound, msg, *a, **k: SENT.append(msg)
    try:
        S.clear()
        S["in_field"] = True

        # ---- the client clock, off a 28-byte telemetry body --------------
        clk = (1 << 32) | 0x12345
        body = struct.pack(">III", 0, clk >> 32, clk & 0xFFFFFFFF) + bytes(16)
        assert len(body) == 28
        assert feworld.note_client_clock(body) == clk
        now = feworld.client_now_ms()
        assert clk <= now < clk + 1000, now
        # -1:-1 is the invalid clock, not a time
        assert feworld.note_client_clock(
            struct.pack(">III", 0, 0xFFFFFFFF, 0xFFFFFFFF) + bytes(16)) is None
        assert S["cclock"][0] == clk

        # ---- the deadline pair: HIGH dword first --------------------------
        assert feworld.pack_deadline((1 << 32) | 5) == b"\0\0\0\1\0\0\0\5"
        dl, kind = feworld.war_abs_deadline(_args(), 60000)
        assert kind == "client" and clk + 60000 <= dl < clk + 61000
        dl2, kind2 = feworld.war_abs_deadline(_args(war_clock="off"), 60000)
        assert (dl2, kind2) == (60000, "raw")

        # ---- 0x1148: the base that makes the client clock = epoch ms ------
        # (2026-09-12: once per connection, well after the field is ready --
        # fe_clock_test has the gate; here, a field ready a minute ago)
        import time as _t
        S["field_ready"] = True
        S["field_ready_at"] = _t.monotonic() - 60
        a = _args(war_clock="sync")
        feworld.clock_sync_push(None, None, 0, False, a)
        assert len(SENT) == 1
        mid = struct.unpack(">H", SENT[0][4:6])[0]
        assert mid == 0x1148, hex(mid)
        r = Reader(SENT[0][6:])
        hi, lo = r.u32(), r.u32()          # 0x05055edb: first read -> HIGH
        r.done()
        base = (hi << 32) | lo
        import time as _t
        epoch = int(_t.time() * 1000)
        # the estimate moved with the base: the client now reads epoch ms
        assert abs(feworld.client_now_ms() - epoch) < 5000
        assert base > 1_000_000_000_000
        assert S["clock_synced"] is True
        feworld.clock_sync_push(None, None, 0, False, a)   # once only
        assert len(SENT) == 1
        del SENT[:]

        # ---- the cycle: prewar -> war -> truce -> peace -> prewar ---------
        a = _args(war_cycle="on", war_deadline_ms=60000, war_length_ms=600000,
                  war_truce_ms=120000)
        feworld.war_notify(None, None, 0, False, a)
        assert S["war_phase"] == "prewar"
        n18 = SENT[-1]
        assert struct.unpack(">H", n18[4:6])[0] == 0x1018
        r = Reader(n18[6:])
        r.u32(); r.u32()
        pre_hi, pre_lo = r.u32(), r.u32()
        r.done()
        assert ((pre_hi << 32) | pre_lo) == S["war_deadline"]
        assert S["war_deadline"] >= feworld.client_now_ms() + 59000

        # not due yet: the pump sends nothing
        feworld.war_deadline_pump(None, None, 0, False, a)
        assert len(SENT) == 1
        # due (fake the client clock past it): 0x1015 with the war length
        S["cclock"] = (S["war_deadline"], S["cclock"][1])
        feworld.war_deadline_pump(None, None, 0, False, a)
        assert S["war_phase"] == "war" and S["war_started"] is True
        m15 = SENT[-1]
        assert struct.unpack(">H", m15[4:6])[0] == 0x1015
        r = Reader(m15[6:])
        w_hi, w_lo = r.u32(), r.u32()
        r.done()
        war_dl = (w_hi << 32) | w_lo
        assert war_dl == S["war_deadline"]
        assert 600000 <= war_dl - feworld.client_now_ms() + 1000
        assert war_dl - feworld.client_now_ms() <= 600000

        # !war forces the next step: truce
        S["war_force"] = True
        feworld.war_deadline_pump(None, None, 0, False, a)
        assert S["war_phase"] == "truce"
        m16 = SENT[-1]
        assert struct.unpack(">H", m16[4:6])[0] == 0x1016
        r = Reader(m16[6:])
        assert r.u32() == 0x7FFFFFFF        # 0x051181e4: skip the lookup
        t_hi, t_lo = r.u32(), r.u32()
        r.done()
        assert ((t_hi << 32) | t_lo) == S["war_deadline"]
        assert S["war_deadline"] - feworld.client_now_ms() <= 120000

        # and again: peace (0x1017, no body) then a fresh 0x1018
        S["war_force"] = True
        feworld.war_deadline_pump(None, None, 0, False, a)
        ids = [struct.unpack(">H", m[4:6])[0] for m in SENT[-2:]]
        assert ids == [0x1017, 0x1018], [hex(i) for i in ids]
        assert SENT[-2][6:] == b""
        assert S["war_phase"] == "prewar" and S["war_started"] is False

        # --war-start deadline without the cycle: prewar -> war and STOP
        S.clear()
        S["in_field"] = True
        del SENT[:]
        a = _args(war_start="deadline", war_deadline_ms=60000)
        # 2026-09-08: in the FIELD with no clock sample yet the notify is
        # HELD, not sent raw -- the raw shape is what made every 0x1015 land
        # ~8 s after the client's own label (measured 08-28, three runs, and
        # the telemetry mode had never once engaged for this arm).
        feworld.war_notify(None, None, 0, False, a)
        assert SENT == [], [hex(struct.unpack(">H", m[4:6])[0]) for m in SENT]
        assert S["war_notify_deferred"] is True
        assert "war_deadline" not in S
        # the first 28-byte 0x2023 lands; the handler pops the flag and calls
        # war_notify again, which now has a clock to be absolute against
        clk = 47157
        feworld.note_client_clock(
            struct.pack(">III", 0, 0, clk) + bytes(16))
        assert S.pop("war_notify_deferred") is True
        feworld.war_notify(None, None, 0, False, a)
        assert len(SENT) == 1
        assert struct.unpack(">H", SENT[-1][4:6])[0] == 0x1018
        assert S["war_deadline_kind"] == "client", S["war_deadline_kind"]
        assert clk + 60000 <= S["war_deadline"] < clk + 61000
        r = Reader(SENT[-1][6:])
        r.u32(); r.u32()
        assert ((r.u32() << 32) | r.u32()) == S["war_deadline"]
        r.done()
        # on the WAR MAP (not in the field) there is no telemetry to wait
        # for, so the raw shape survives there
        S.clear()
        del SENT[:]
        feworld.war_notify(None, None, 0, False, a)
        assert len(SENT) == 1 and "war_notify_deferred" not in S
        r = Reader(SENT[-1][6:])
        assert [r.u32() for _ in range(4)] == [0, 0, 0, 60000]
        # and --war-clock off keeps raw in the field too
        S.clear()
        S["in_field"] = True
        del SENT[:]
        feworld.war_notify(None, None, 0, False,
                           _args(war_start="deadline", war_deadline_ms=60000,
                                 war_clock="off"))
        assert len(SENT) == 1 and S["war_deadline_kind"] == "raw"
        S.clear()
        S["in_field"] = True
        del SENT[:]
        feworld.note_client_clock(struct.pack(">III", 0, 0, clk) + bytes(16))
        feworld.war_notify(None, None, 0, False, a)
        S["war_force"] = True
        feworld.war_deadline_pump(None, None, 0, False, a)
        assert struct.unpack(">H", SENT[-1][4:6])[0] == 0x1015
        assert S["war_phase"] == "war"
        S["war_force"] = True
        feworld.war_deadline_pump(None, None, 0, False, a)
        assert struct.unpack(">H", SENT[-1][4:6])[0] == 0x1015   # nothing new
    finally:
        feworld.send = real_send
        S.clear()


# ---------------------------------------------------------------------------
# PART 8 -- the CAPITAL announcement (2026-09-04)
# ---------------------------------------------------------------------------
def part8():
    """A capital is served by ANNOUNCING A GROUP ID, so the two things that can
    go wrong are arithmetic, not protocol:

      * 0x3031 is [u16 island][u16 count] then exactly `count` records. If the
        count and the record list disagree the client's parser DESYNCHRONISES
        and every field after it is garbage -- the silent asymmetric failure
        this whole file exists for.
      * an id the client's predicate 0x04ff7ef0 does not accept takes the Hmap
        battlefield branch instead, giving a war field wearing a town's name.
    """
    caps = feworld.parse_capitals("1:39")
    a = _args(groups=1)

    gids = feworld.group_ids_for(1, a, caps)
    assert gids == [1, 39], gids
    assert feworld.group_ids_for(2, a, caps) == [2], "capital leaked islands"
    assert feworld.group_ids_for(1, a, {}) == [1], "capital appeared unasked"

    # The header count must be taken from the SAME list the records are built
    # from. Build it exactly as the 0x3031 handler does and read it back.
    payload = struct.pack(">HH", 1, len(gids))
    for g in gids:
        payload += feworld.build_group_record(g, {"name": "X", "def_id": 1,
                                                  "g8c": 8})
    island, count = struct.unpack_from(">HH", payload, 0)
    assert island == 1 and count == len(gids) == 2, (island, count)

    # Walk the records the way the client's outer parser does -- [u32 gid]
    # [u32 mask] then the optional fields -- and assert the stream is fully
    # consumed with the capital's id present and last.
    seen, off = [], 4
    for _ in range(count):
        gid = struct.unpack_from(">I", payload, off)[0]
        seen.append(gid)
        off += len(feworld.build_group_record(gid, {"name": "X", "def_id": 1,
                                                    "g8c": 8}))
    assert off == len(payload), "0x3031 desync: %d of %d consumed" % (
        off, len(payload))
    assert seen == [1, 39], seen

    # Every id the flag accepts must be one the client's own jump table calls a
    # capital -- that table is the authority, not this list.
    for g in feworld.CAPITAL_GROUP_IDS:
        assert feworld.parse_capitals("1:%d" % g) == {1: [g]}
    for bad in (0, 1, 20, 22, 96, 200):
        try:
            feworld.parse_capitals("1:%d" % bad)
        except SystemExit:
            pass
        else:
            raise AssertionError("--capital accepted non-capital id %d" % bad)

    # ---- 2026-09-04: the 5-byte tail, and MULTI-RECORD 0x3031 ------------
    # This file's whole premise is that a short body does not error, it
    # DESYNCHRONISES the client. That happened here for the life of the project
    # and no check caught it, because every check decoded ONE record: the parser
    # at 0x05008850 ends with two reads NO MASK BIT GATES -- 1 byte at +0xC0
    # (0x5045e90, `inc edx`) and a u32 at +0xC4 (0x5045e60) -- and an over-read
    # off the end of the LAST record in a message is invisible. With two records
    # it eats the next one's first 5 bytes.
    #
    # So decode a TWO-record payload exactly as the client does, tail included,
    # and assert the second record's id survives and the stream ends clean.
    KL = {"u8": 1, "u32": 4, "u16": 2, "i8": 1, "2i16": 4}

    def client_read_record(buf, off, want=None):
        """Consume one 0x3031 record the way 0x05008850 does, and hand back
        the neighbour array when one is present -- the decoder has to grow the
        same shapes the parser has, or it stops being a check."""
        gid, mask = struct.unpack_from(">II", buf, off)
        p = off + 8
        nbrs = None
        for bit, (kind, _key, _o, _n) in enumerate(feworld.GROUP_FIELDS):
            if not (mask >> bit) & 1:
                continue
            if kind == "cstr":
                p = buf.index(b"\x00", p) + 1
            elif kind == "u32arr":
                # 0x0500899c: one u32 count, then EXACTLY that many u32s. The
                # client allocates count*4 off the wire, so a count that does
                # not match what follows is not a short field, it is a stream
                # that never realigns.
                n = struct.unpack_from(">I", buf, p)[0]
                p += 4
                nbrs = list(struct.unpack_from(">%dI" % n, buf, p)) if n else []
                p += 4 * n
            else:
                p += KL[kind]
        return (gid, p + 1 + 4, nbrs) if want else (gid, p + 1 + 4)

    vals_a = {"status": 1, "g8c": 8, "name": "Ruined Plain",
              "coords": (300, 200), "hmap": 1,
              "def_name": "Netzawar", "def_id": 1,
              "atk_name": "Gebrand", "atk_id": 5}
    vals_b = dict(vals_a, name="Old Bridge", coords=(500, 250), hmap=2)
    two = (struct.pack(">HH", 1, 2)
           + feworld.build_group_record(1, vals_a)
           + feworld.build_group_record(2, vals_b))

    g0, nxt = client_read_record(two, 4)
    g1, end = client_read_record(two, nxt)
    assert g0 == 1, 'REGRESSION 0x3031 record 0 decodes as its own id'
    assert g1 == 2, 'REGRESSION the SECOND record is not eaten by the first'
    assert end == len(two), 'REGRESSION a two-record 0x3031 is consumed exactly'

    # The tail is unconditional: it is present even on a bare record.
    bare = feworld.build_group_record(7, {})
    assert len(bare) == 8 + 5, 'every record carries the 5-byte tail'

    # ---- 2026-09-09: BIT 12, THE NEIGHBOUR ARRAY ------------------------
    # The one field that decides whether a player can walk into enemy land.
    # It is a COUNTED array, so the failure it can cause is not "a wrong
    # neighbour" -- it is a stream that never realigns again. Decode a payload
    # whose records carry DIFFERENT-LENGTH arrays, because a length bug that
    # happens to be constant hides behind records of equal size.
    va = dict(vals_a, neighbours=[5, 8, 15, 13, 10, 6, 14, 4, 12])   # area 5
    vb = dict(vals_b, neighbours=[21])                               # a leaf
    vc = dict(vals_a, name="No neighbours", neighbours=[])           # count 0
    three = (struct.pack(">HH", 1, 3)
             + feworld.build_group_record(5, va)
             + feworld.build_group_record(20, vb)
             + feworld.build_group_record(43, vc))
    g0, n1, a0 = client_read_record(three, 4, want=True)
    g1, n2, a1 = client_read_record(three, n1, want=True)
    g2, end, a2 = client_read_record(three, n2, want=True)
    assert (g0, g1, g2) == (5, 20, 43), \
        'REGRESSION bit 12 desynchronised a multi-record 0x3031: %r' % ((g0, g1, g2),)
    assert a0 == va["neighbours"], 'the adjacency list round-trips in order'
    assert a1 == [21] and a2 == [], 'a one-entry and an EMPTY array both parse'
    assert end == len(three), 'a three-record 0x3031 with arrays is consumed exactly'

    # An empty list is not the same as an absent field: count 0 still costs the
    # bit and four bytes, and that is what makes it safe to serve a leaf field.
    assert (struct.unpack_from(">I", feworld.build_group_record(1, {"neighbours": []}), 4)[0]
            >> 12) & 1, 'an empty neighbour list still sets mask bit 12'
    assert len(feworld.build_group_record(1, {"neighbours": []})) == 8 + 4 + 5

    # Bit 12 must no longer be refused, and 4/5/6 must still be.
    assert 12 not in feworld.GROUP_BITS_UNSUPPORTED, 'bit 12 is decoded now'
    for _b in (4, 5, 6):
        assert _b in feworld.GROUP_BITS_UNSUPPORTED, \
            'bit %d reads five u16 and is still not modelled' % _b

    # A capital is a PEACE field: war_notify must refuse to arm in one.
    S = feworld._SESSION
    real_send, sent = feworld.send, []
    feworld.send = lambda *args, **kw: sent.append(args)
    try:
        # `field` is the group the 0x2000 handler ENTERED. `group` is the
        # 0x4011 leftover -- the island's first war field -- and is rewritten
        # every lap, which is why the guard read it as "not a capital" while
        # the player stood in one (2026-09-04).
        S.clear()
        S["field"] = 39
        S["group"] = 1              # the 0x4011 leftover, as prod has it
        S["in_field"] = True
        feworld.war_notify(None, None, 0, False, _args(war="offensive"))
        assert not sent, "war_notify armed a war inside a capital"
        S.clear()
        S["field"] = 1
        S["in_field"] = True
        feworld.war_notify(None, None, 0, False, _args(war="offensive"))
        assert sent, "war_notify stopped arming on a normal field"
        # Out of field there is no entered group, so a stale `field` from a
        # previous entry must not hush the map screen's notify.
        del sent[:]
        S.clear()
        S["field"] = 39
        feworld.war_notify(None, None, 0, False, _args(war="offensive"))
        assert sent, "war_notify hushed the map screen off a stale field"
    finally:
        feworld.send = real_send
        S.clear()


def part9():
    """0x1006 type 1 -- the BUILDING record, decoded with the type-1 arm's own
    read sequence (0x0503b583..0x0503b616, 2026-09-04) and fully consumed.

    The eleven reads, by primitive: u16 u16 u32 u8 i32 i32 u16 u16 u16 u8 u32.
    The u8 at +0x94 is the one the static README's table missed; a record
    built from that table would have been one byte short and desynchronised
    everything after it in the same 0x1006.
    """
    body = feworld.building_record(3000, 10, 10, 130, 150, side=1,
                                   hp=(100, 100))
    r = Reader(body)
    assert r.u16() == 1                       # entity count
    assert r.u32() == 3000                    # object id
    assert r.u8() == 1                        # type 1 = building
    assert r.u16() == 10                      # +0x380 type    (0x5045e30)
    assert r.u16() == 10                      # +0x382 model   (0x5045ec0)
    assert r.u32() == 0                       # +0x384         (0x5045e60)
    assert r.u8() == 1                        # +0x94 side     (0x5045e90)
    assert r.u32() == 100                     # +0x778         (0x5045ef0)
    assert r.u32() == 100                     # +0x774         (0x5045ef0)
    assert r.u16() == 130                     # +0x388 grid X  (0x5045e30)
    assert r.u16() == 0                       # +0x38a         (0x5045e30)
    assert r.u16() == 150                     # +0x38c grid Z  (0x5045e30)
    assert r.u8() == 0                        # +0x38e         (0x5045dc0)
    assert r.u32() == 0                       # timestamp      (0x5045e60)
    assert r.i == len(body), "type-1 record not fully consumed"

    # the placer's formula and its inverse. SIGN MEASURED LIVE 2026-09-05:
    # grid 55 rendered at world -182.5, so world = (g-128)*2.5, not its
    # negation as the static note had it.
    assert feworld.grid_to_world(128) == 0.0
    assert feworld.grid_to_world(55) == -182.5
    assert feworld.grid_to_world(130) == 5.0
    assert feworld.world_to_grid(5.0) == 130
    assert feworld.world_to_grid(183.7) == 201
    assert feworld.world_to_grid(0.0) == 128
    assert feworld.world_to_grid(9999) == 255 and feworld.world_to_grid(-9999) == 0

    # --building parsing refuses rows the client's tables do not have
    assert feworld.parse_buildings(["39:10:10:130:150"]) == [(39, 10, 10, 130, 150)]
    for bad in ("39:30:10:1:1", "39:10:32:1:1", "39:10:10:256:1", "x"):
        try:
            feworld.parse_buildings([bad])
        except SystemExit:
            pass
        else:
            raise AssertionError("parse_buildings accepted %r" % bad)

    # building_push serves only the entered group, and the room exit is the
    # same group forced outdoors.
    S = feworld._SESSION
    real_send, sent = feworld.send, []
    feworld.send = lambda *a, **kw: sent.append(a[2])
    try:
        S.clear()
        S["field"] = 39
        S["in_field"] = True
        a = _args(buildings=[(39, 10, 10, 130, 150), (1, 9, 9, 10, 10)],
                  unit_id="1")
        feworld.building_push(None, None, 0, False, a)
        assert len(sent) == 1, "building_push must serve the entered group only"
        assert sent[0][4:6] == b"\x10\x06" and sent[0][:4] == (3000).to_bytes(4, "big")
        # !build with no telemetry position sends nothing
        del sent[:]
        feworld.build_here(None, None, 0, False, a, "10")
        assert not sent
        S["cpos"] = (5.0, 24.0, 55.0)
        feworld.build_here(None, None, 0, False, a, "10")
        assert len(sent) == 1
        r = Reader(sent[0][6:])
        r.u16(); r.u32(); r.u8(); r.u16(); r.u16(); r.u32(); r.u8(); r.u32(); r.u32()
        assert r.u16() == 130 and r.u16() == 0 and r.u16() == 150, \
            "!build must place at the client's reported position"
    finally:
        feworld.send = real_send
        S.clear()


def part10():
    """The DOOR round trip: 0x20AC -> 0x1166, decoded with the arm's reads.

    0x05056089 reads u32 A, u32 B, f32 x, f32 y, f32 z and nothing else; a
    body that is not exactly 20 bytes desynchronises the next message. A must
    NOT equal the current field (that branch is a same-field warp), and the
    room comes back out of A in the 0x2000 handler.
    """
    S = feworld._SESSION
    real_send, sent = feworld.send, []
    feworld.send = lambda *a, **kw: sent.append(a[2])
    try:
        S.clear()
        S["in_field"] = True
        S["field"] = 39
        # doors="off" keeps the SHIPPED table out of this case on purpose:
        # what is under test is a hand-mapped --door row, and area 39 is a
        # capital that has shipped portals of its own.
        a = _args(door_rows=[(39, 186.5, -132.5, 11)], doors="off",
                  door_default=10, unit_id="1")
        frame = struct.pack(">H", 0x20AC) + struct.pack(">fff", 186.5, 0.003, -132.5)
        # drive the real dispatch through serve() the way part2 does: here
        # the handler is exercised directly via the inner-message router.
        _dispatch(a, frame)
        assert len(sent) == 1, "one reply to a door"
        body = sent[0]
        assert body[4:6] == b"\x11\x66"
        r = Reader(body[6:])
        A, B = r.u32(), r.u32()
        assert A == feworld.ROOM_AREA_BASE + 11, "mapped door -> its room"
        assert B == 0xFFFFFFFF
        assert struct.unpack_from(">fff", body[6:], 8) == (0.0, 0.0, 0.0)
        assert 6 + 20 == len(body), "0x1166 is exactly u32 u32 f32 f32 f32"
        # an unmapped door far from the entry takes --door-default
        del sent[:]
        _dispatch(a, struct.pack(">H", 0x20AC) + struct.pack(">fff", -50.0, 0, 20.0))
        assert Reader(sent[0][6:]).u32() == feworld.ROOM_AREA_BASE + 10
        # ...and NG when there is no default at all
        del sent[:]
        _dispatch(_args(doors=[], door_default=None, unit_id="1"),
                  struct.pack(">H", 0x20AC) + struct.pack(">fff", 1, 2, 3))
        assert sent[0][4:6] == b"\x11\x67" and len(sent[0]) == 6 + 4
        # the war notify stays quiet indoors
        del sent[:]
        S["room"] = 10
        feworld.war_notify(None, None, 0, False, _args(war="offensive"))
        assert not sent, "war_notify armed a countdown inside a room"
    finally:
        feworld.send = real_send
        S.clear()


def part11():
    """The TOWN FILE and dat.pak's spawn tables.

    `!npc` / `!build` / `!door` persist to the town file and the next entry of
    that group serves them; spawn_push serves a war field's shipped points and
    nothing for a capital; the gear seed produces well-formed item and equip
    lists from the real tables.
    """
    import tempfile
    import fegamedata
    import felobby
    S = feworld._SESSION
    real_send, sent = feworld.send, []
    feworld.send = lambda *a, **kw: sent.append(a[2])
    d = tempfile.mkdtemp(prefix="fetown-")
    tf = os.path.join(d, "fe_town.json")
    try:
        S.clear()
        S["in_field"], S["field"], S["cpos"] = True, 39, (10.0, 2.0, -20.0)
        a = _args(town_file=tf, unit_id="1")
        # !npc persists with the dat.pak name/script of that modeltype
        feworld.town_add(a, 39, "npcs", {"model": 233, "kind": 1, "level": 1,
                                         "x": 10.0, "y": 2.0, "z": -20.0,
                                         "name": "Warrior_Weapon_Shop", "script": 2102})
        feworld.town_add(a, 39, "doors", {"x": 186.5, "z": -132.5, "room": 11})
        feworld.town_add(a, 1, "npcs", {"model": 54, "kind": 1, "level": 1,
                                        "x": 0, "y": 0, "z": 0})
        assert feworld.town_door_for(a, 39, 190.0, -130.0) == 11, "town-file door"
        assert feworld.town_door_for(a, 39, 0.0, 0.0) is None
        assert feworld.town_door_for(a, 1, 186.5, -132.5) is None, "doors are per group"
        del sent[:]
        feworld.town_push(None, None, 0, False, a)
        assert len(sent) == 1, "one NPC for group 39, none of group 1's"
        r = Reader(sent[0][6:])
        assert r.u16() == 1 and r.u32() == 500 and r.u8() == 8 and r.u16() == 233
        # the talk request resolves that unit id back to its script id
        del sent[:]
        _dispatch(a, struct.pack(">H", 0x2046) + struct.pack(">II", 500, 0))
        assert sent and sent[0][4:6] == b"\x10\x71"
        r = Reader(sent[0][6:])
        assert (r.u32(), r.u16(), r.u16(), r.u32()) == (500, 0, 0, 2102), \
            "D = the keeper's dat.pak script -- the event VM arms a script"
        assert r.i == 12
        assert len(sent) == 1, "with a script, no premature 0x1172"
        assert S.get("event") and len(S["event"]["steps"]) == 3, "text, shop, END window"
        S.pop("event", None)
        # spawn tables: a war field gets its shipped points, a capital none
        del sent[:]
        S["field"] = 1
        # 2026-09-12: this USED TO assert exactly 3 monsters -- the old ONE
        # per point. spawns_max still caps POINTS; each point now serves its
        # generator's population (fegamedata.spawn_populations), so 3 points
        # are those three groups. `one` keeps the original meaning below.
        feworld.spawn_push(None, None, 0, False, _args(spawns="dat", spawns_max=3))
        want = min(200, sum(n for g in fegamedata.spawn_groups(1)[:3]
                            for (_t, _m, _nm, n) in g[5]))
        assert len(sent) == want and want > 3,             "spawns_max caps the POINTS, each serving its generator's population"
        r = Reader(sent[0][6:]); r.u16(); r.u32(); r.u8()
        assert r.u16() in fegamedata.SHIPPED_MODELTYPES
        del sent[:]
        feworld.spawn_push(None, None, 0, False,
                           _args(spawns="dat", spawns_max=3, spawn_population="one"))
        assert len(sent) == 3, "--spawn-population one: spawns_max caps the shipped points"
        del sent[:]
        S["field"] = 39
        feworld.spawn_push(None, None, 0, False, _args(spawns="dat"))
        assert not sent, "a capital has no shipped spawns"
        # the gear tables are consistent with the item table for the classes
        # this build ships (0 warrior, 1 scout, 2 sorcerer, 6); classes 3..5
        # name items 1698..1715 that FE_ITEM_DATA does not have here.
        for cls in (0, 1, 2):
            for sex in (0, 1):
                gear = fegamedata.starting_gear(cls, sex)
                assert gear, "class %d sex %d has starting gear" % (cls, sex)
                for no in gear:
                    assert fegamedata.items().get(no, {}).get("slot", -1) >= 0, \
                        "item %d has a slot type" % no
        assert all(no not in fegamedata.items() for no in (1705, 1712)), \
            "if these appear, classes 3..5 shipped and the seed's skip is stale"
        # and a seeded record still encodes with felobby's own builder
        vals = felobby.char_defaults()
        vals["charid"], vals["look1"], vals["sex"] = 7, 2, 1
        gear = fegamedata.starting_gear(2, 1)
        vals["items"] = [(7000 + i + 1, no, 1) for i, no in enumerate(gear)]
        vals["equip"] = [(fegamedata.items()[no]["slot"], 7000 + i + 1)
                         for i, no in enumerate(gear)]
        rec = felobby.build_char_record(vals)
        assert rec.endswith(struct.pack(">IHB", 7004, gear[-1], 1))
        # 2026-09-08: the record's item array is the WORN set -- the client's
        # builder (0x04fe77e3) Equips every entry at 0x04fe7880 with no flag
        # test, so a bag item here is worn at the select screen and in the
        # unit the client builds. Two wands in the bag = the second one wins.
        vals["items"] = [(7001, 531, 1), (7002, 1666, 1), (7003, 531, 1),
                         (7004, 7, 1, 3), (7005, 19, 1)]
        vals["equip"] = [(10, 7001), (4, 7002), (11, 7005), (11, 7004)]
        worn = felobby.worn_items(vals)
        assert [r[0] for r in worn] == [7001, 7002, 7005, 7004], worn
        rec = felobby.build_char_record(vals)
        assert struct.pack(">IHB", 7003, 531, 1) not in rec, "the spare wand rode along"
        assert rec.endswith(struct.pack(">H", 4) + struct.pack(">IHB", 7001, 531, 1)
                            + struct.pack(">IHB", 7002, 1666, 1)
                            + struct.pack(">IHB", 7005, 19, 1)
                            + struct.pack(">IHB", 7004, 7, 1)), rec[-40:].hex()
        # an equip pair naming a uid the bag does not hold is ignored, and a
        # character with nothing worn sends an empty array
        vals["equip"] = [(10, 7777)]
        assert felobby.worn_items(vals) == []
        assert felobby.build_char_record(vals).endswith(struct.pack(">H", 0))
    finally:
        feworld.send = real_send
        S.clear()


def _class_levels_pinned():
    """`all:N` fills the whole class-level array and stops at the SKILL array.

    Live 2026-09-05: every item refused with "Wrong class; can't equip", which
    is reason 4 of the validator at 0x05076090 -- the level in the class the
    CLIENT thinks the unit is ([unit+0x9F0 + [unit+0x3AB]] < [tbl+0x86]).
    Filling one slot bets on that byte; filling all of them cannot miss.
    """
    # WARNING: 2026-09-09: `all` was 0x24 = every byte between the level array and
    # the skills -- but the per-class EXP dwords live at +0x9F8, inside that
    # span, and 36 levels of 30 wrote 0x1E1E1E1E over the EXP the Status
    # screen draws. Seven classes, seven levels.
    rows, dropped = feworld.class_level_rows("all:30", 2)
    assert not dropped and len(rows) == 7, len(rows)
    assert rows[0] == (0, 30) and rows[-1] == (6, 30)
    assert len(rows) <= 0xFF, "the count is a u8"
    # and the block still decodes: [u8 n] then n x (u8 class, u8 level)
    body, _r, _d = feworld.skill_list_body([], 1, rows)
    r = Reader(body)
    assert r.u32() == (feworld.CLASS_MASK_BIT | feworld.SKILL_MASK_BIT)
    assert r.u8() == 7
    for i in range(7):
        assert (r.u8(), r.u8()) == (i, 30)
    assert r.u16() == 0 and r.i == len(r.b)


def _bagsize_pinned():
    """The bag size rides 0x2024 maskA bit 0x20000000 as ONE u16, and the item
    window sizes its grid from it (ctor 0x050b884c reads word [player+0x4fc]),
    so without it the window draws 0/0 however full the bag arrays are."""
    real_send, sent = feworld.send, []
    feworld.send = lambda *a, **kw: sent.append(a[2])
    try:
        feworld.bag_size_push(None, None, 0, False, _args(bag_size=30))
        assert len(sent) == 1 and sent[0][4:6] == b"\x20\x24"
        r = Reader(sent[0][6:])
        assert r.u32() == 0x20000000 and r.u32() == 0
        assert r.u16() == 30 and r.i == len(r.b), "one bit, exactly its u16"
        del sent[:]
        feworld.bag_size_push(None, None, 0, False, _args(bag_size=0))
        assert not sent, "0 disables the send"
    finally:
        feworld.send = real_send
    # and NOT in the 0x1006 stat block, whose applier stops at bit 23: a row
    # past 23 there is bytes nothing reads (live 2026-09-05, reverted)
    assert max(b for b, _o, _n, _c in feworld._AVATAR_STATS) <= 23


def part13():
    """THE FIELD INVENTORY in 0x1000: the bag sub-record (0x0503d330) and the
    worn-gear sub-record (0x0503c470), read exactly as the client reads them."""
    items = [(7001, 301, 1), (7002, 1165, 1), (7003, 4, 1)]
    equip = [(2, 7001), (4, 7002)]
    b = feworld.bag_record(items, {7001: 2}, "0")
    r = Reader(b)
    assert (r.u8(), r.u8(), r.u8()) == (1, 1, 1), "[pages][ADD=1][announce=1 probe] (live #12: swapped flags = the 0xAC647 crash)"
    assert r.u32() == 0 and r.u32() == 0b111, "page 0, slots 0..2"
    for n, (uid, no, _) in enumerate(items):
        assert r.u32() == uid
        assert r.u32() == 2 and r.u16() == no, "group 1 bit1 = the u16 table key"
        assert r.u32() == 0 and r.u32() == 0, "groups 2 and 3 empty"
        # group 4: count, kind, equip marker, SLOT -- ascending bit order
        assert r.u32() == (feworld.IT_COUNT | feworld.IT_DURMAX | feworld.IT_DURCUR
                           | feworld.IT_KIND | feworld.IT_EQUIP | feworld.IT_SLOT)
        assert r.u16() == 1, "stack count"
        assert r.u16() == 0xFFFF and r.u16() == 100, "durability max -1, current 100"
        assert r.u16() == 0, "kind 0 = the bag array (the insert refuses anything but 0/1)"
        assert r.u8() == (2 if uid == 7001 else 0xFF), "equip marker: 0xff = not worn"
        assert r.u32() == n, "THE BAG SLOT (live #13: all-zero slots drew nothing)"
    assert r.i == len(r.b)
    # 33 items spill onto page 1 and keep counting slots
    big = [(9000 + i, 4, 1) for i in range(33)]
    r = Reader(feworld.bag_record(big, None, "0")); r.u8(); r.u8(); r.u8()
    assert r.u32() == 0 and r.u32() == 0xFFFFFFFF
    for i in range(32):
        r.u32(); r.u32(); r.u16(); r.u32(); r.u32(); r.u32(); r.u16(); r.u16(); r.u16(); r.u16(); r.u8()
        assert r.u32() == i
    assert r.u32() == 1 and r.u32() == 1, "page 1, one slot"
    # the probe form sends every item into BOTH arrays under separate uids
    both = feworld.bag_record(items, None, "both")
    r = Reader(both); assert r.u8() == 1 and r.u8() == 1 and r.u8() == 1
    assert r.u32() == 0 and r.u32() == 0b111111, "3 items x 2 arrays"
    kinds = []
    for _ in range(6):
        r.u32(); r.u32(); r.u16(); r.u32(); r.u32(); r.u32(); r.u16()
        r.u16(); r.u16()                     # durability max, current
        kinds.append(r.u16()); r.u8(); r.u32()
    assert kinds == [0, 0, 0, 1, 1, 1], kinds
    e = feworld.equip_record(equip, items)
    r = Reader(e)
    assert r.u32() == (1 << 2) | (1 << 4)
    assert (r.u32(), r.u32(), r.u16()) == (7001, 2, 301)
    assert (r.u32(), r.u32(), r.u16()) == (7002, 2, 1165)
    assert r.i == len(r.b)
    # an equip pair whose uid is not in the bag is dropped, not sent
    assert feworld.equip_record([(3, 9999)], items) == struct.pack(">I", 0)
    # 97 items are capped at the client's 96-deep array: 3 pages
    big = [(8000 + i, 4, 1) for i in range(97)]
    r = Reader(feworld.bag_record(big, None, "0")); assert r.u8() == 3
    # the empty case is the header alone + the empty worn mask
    assert feworld.bag_record([], None, "0") == struct.pack(">BBB", 0, 1, 1)
    assert feworld.equip_record([], []) == struct.pack(">I", 0)
    # the probe form doubles the row count
    assert Reader(feworld.bag_record(items, None, "both")).u8() == 1


def _skill_ids(body):
    """The skill ids out of a 0x1075 body: [u32 mask][class block][u16 n]
    then n x [u16 id][u8 value] -- 0x05051E20 tests bit 0 first, bit 3 last."""
    r = Reader(body)
    mask = r.u32()
    if mask & feworld.CLASS_MASK_BIT:
        for _ in range(r.u8()):
            r.u8()
            r.u8()
    out = []
    for _ in range(r.u16()):
        out.append(r.u16())
        r.u8()
    assert r.i == len(r.b), "the block must consume exactly"
    return out


def part14():
    """THE EQUIP REFLECTION: 0x2021 -> 0x102A -> 0x107A, and the sell price.

    The bug this pins: the client validates locally, sends 0x2021, gets 0x102A,
    and nothing happens -- because 0x102A's arm (0x0503c390) clears one flag and
    reads no body. The reflection is 0x107A, whose per-item reader 0x0503d280
    ends in `cmp byte [item+0x4c8], 0xff / jne -> Equip(uid, -1, 1)`.
    """
    _stub_store()
    STORE["items"] = [[7001, 301, 1], [7002, 1165, 1], [7003, 531, 1]]
    STORE["equip"] = []
    # The client resolves 0x107A's unit id with NO null check, so feworld
    # refuses to send one before the field exists. Live 2026-09-06T00:34:22Z a
    # queued probe fired 20 ms after CONNECT and killed the client at
    # 0x05077D14. Pin BOTH sides: refused out of field, sent in it.
    feworld._SESSION.pop("in_field", None)
    assert feworld.equip_reflect_push(None, None, 0, False, _args(),
                                      [(7001, 301, 0, 1)], "t", "request") == 0,         "0x107A must NOT be sent before the field exists"
    feworld._SESSION["in_field"] = True
    outbox = []
    real = feworld.send

    def _send(conn, out, body, mode, be, echo=True, prefix=4):
        unit, mid = struct.unpack_from(">IH", body, 0)
        outbox.append((mid, unit, body[6:]))
    feworld.send = _send
    try:
        a = _args(field_items="bag")

        # ---- the worn-slot map, read off Equip's own jump table 0x0507b920
        assert feworld.worn_index(1) == 0 and feworld.worn_index(9) == 0
        assert [feworld.worn_index(t) for t in range(2, 8)] == [1, 2, 3, 4, 5, 6]
        assert feworld.worn_index(8) == 7, "type 8 scans 7..10"
        assert feworld.worn_index(8, {7, 8}) == 9, "and takes the first free"
        assert feworld.worn_index(11) == 11, "type 11 scans 11..12"
        assert feworld.worn_index(11, {11, 12}) == 11, "full -> the first"
        assert feworld.worn_index(0) is None and feworld.worn_index(12) is None
        assert max(feworld.WORN_FIXED.values()) < feworld.WORN_SLOTS

        # ---- THE ONE MEASURED REQUEST. Captured off a live feworld
        # 2026-09-05T20:10:16Z, a real player's equip:
        #     20 21 | 00 00 03 ea | 03
        # uid 1002 (charid*1000 + i, felobby's seeding scheme) and worn slot 3.
        # That u8 is the CLIENT's own choice of worn index, and 3 is exactly
        # what WORN_FIXED gives an item of slot type 4 (body armour) -- so the
        # type -> index map read off Equip's jump table 0x0507b920 is confirmed
        # by the client, not just by the disassembly.
        assert feworld.worn_index(4) == 3, "slot type 4 -> worn 3, measured"
        live = bytes.fromhex("000003ea03")
        assert struct.unpack_from(">I", live, 0)[0] == 1002
        assert live[4] == 3 and len(live) == 5, "[u32 uid][u8 worn], 5 bytes"

        # ---- EQUIP the axe (item 301, slot type 2 -> worn 1)
        del outbox[:]
        inner = struct.pack(">HIB", 0x2021, 7001, 1)
        _dispatch(a, inner)
        # + 0x2004 since 2026-09-12: the worn gear is RESTATED after a weapon
        # goes on in the field (only the entry replay did it; a weapon equipped
        # mid-session had no motion set -- the scout's bow T-posed, live)
        assert [m for m, _, _ in outbox] == [0x102A, 0x107A, 0x2004], \
            [hex(m) for m, _, _ in outbox]
        assert outbox[0][2] == b"", "0x102A is header-only, it reads no body"
        assert STORE["equip"] == [[2, 7001]], STORE["equip"]

        # the 0x107A body, read exactly as 0x0503d540 reads it
        r = Reader(outbox[1][2])
        assert r.u8() == 1, "add"
        assert r.u8() == 0, "announce off -- this is a re-send, not a pickup"
        # WARNING: A u32, NOT a u16. The reader at 0x0503d598 is 0x5045ef0 (`add
        # edx,4`). Shipping it as a u16 put the uid 2 bytes out and the client
        # read 1001 as 0x03E90000 -- measured by the feitem hook 2026-09-05,
        # and the sole cause of six crashed probes.
        assert r.u32() == 0, "bag slot 0: the axe's index in the stored list"
        assert r.u32() == 7001
        assert r.u32() == 2 and r.u16() == 301, "group 1 bit1 = the table key"
        assert r.u32() == 0 and r.u32() == 0, "groups 2 and 3 empty"
        assert r.u32() == (feworld.IT_COUNT | feworld.IT_DURMAX | feworld.IT_DURCUR
                           | feworld.IT_KIND | feworld.IT_EQUIP | feworld.IT_SLOT)
        assert r.u16() == 1, "count"
        assert r.u16() == 0xFFFF and r.u16() == 100, "durability max -1, current 100"
        assert r.u16() == 0, "bag array"
        assert r.u8() == 1, "THE MARKER = the worn index, not 0xff"
        assert r.u32() == 0 and r.i == len(r.b), "bag slot again, then done"
        # addressed with the SAME envelope id every other unit-scoped push
        # uses: 0x0503d540 resolves it through 0x504d410(mgr, id, class 1),
        # the identical lookup 0x1075's skill applier (0x05051e80) does --
        # and `--skill-grant all` landing on [unit+0xA14] live with
        # --unit-id 0 is the proof that this id reaches the player. That
        # lookup has NO null check (0x0503d570 dereferences the result at
        # once), so an id the client does not know is an AV, not a drop.
        assert outbox[1][1] == feworld.unit_id_of(a), outbox[1][1]
        assert outbox[1][2] == feworld.item_add_body(7001, 301, 0, 1, 1, 0, 0), \
            'the builder and the wire agree'

        # ---- UNEQUIP it: 0x2004 clears the worn slot, THEN 0x107A puts
        # the item back in the bag with the 0xff marker.
        #
        # THE ORDER IS THE POINT (2026-09-06, reported live as "the game
        # removes the item but doesn't restore the default mesh
        # underneath and the item still shows as equipped"). 0x2004's
        # uid==0 branch (0x0503c50f) reads the item back out of
        # [unit+0x870+slot] to find what to unequip, so it has to run
        # while that array still names it. Sending only the 0x107A sets
        # [item+0x4c8] = 0xff and touches the UNIT not at all -- which is
        # why the inventory kept drawing it worn and the body part the
        # equip swapped out never came back.
        #
        # 0x2022 IS THE ONLY UNEQUIP, and it carries NO SLOT. Measured off
        # the live wire 2026-09-06 -- the whole request is six bytes:
        #
        #     0x2021   20 21 | 00 00 03 ea | 03    [u32 uid][u8 worn]
        #     0x2022   20 22 | 00 00 03 ef        [u32 uid]
        #
        # so the worn index has to be derived from the item table the way
        # the client's own Equip derives it. The first cut trusted a u8
        # that is not there, fell back to 0xFF, and refused every unequip
        # as "outside the 13-slot array" -- seen live as a hat that would
        # not come off.
        del outbox[:]
        _dispatch(a, struct.pack(">HI", 0x2022, 7001))
        assert [m for m, _, _ in outbox] == [0x102C, 0x2004, 0x107A], \
            [hex(m) for m, _, _ in outbox]
        assert STORE["equip"] == [], STORE["equip"]
        # 0x2004 = [u32 applier mask][u32 13-slot mask][u32 uid]; uid 0
        # means "this slot is now empty". Item 301 is slot type 2 -> worn 1.
        assert outbox[1][2] == struct.pack(">III", 1, 1 << 1, 0), \
            outbox[1][2].hex()
        assert outbox[1][1] == feworld.unit_id_of(a), \
            "addressed to the LOCAL PLAYER: for any other unit the same " \
            "branch DESTROYS the item object (0x0503c578)"
        r = Reader(outbox[2][2])
        r.u8(); r.u8(); r.u32(); r.u32(); r.u32(); r.u16(); r.u32(); r.u32()
        r.u32(); r.u16(); r.u16(); r.u16(); r.u16()   # count, dur max, dur cur, kind
        assert r.u8() == 0xFF, "0xff = not worn, so the reader's tail skips Equip"

        # ---- 0x2098 IS NOT AN UNEQUIP, whatever MSG_DISRAM_EQUIP_ITEM
        # sounds like. Live 2026-09-06: the player put cheese, a potion
        # and a feather "into slot 1 and 2 for quick use" and the client
        # sent 0x2098 carrying worn slots 11 and 12 -- exactly where item
        # slot type 11 lands. It ASSIGNS to an occupied slot. Sending the
        # 0x2004 clear for it would empty the quick slot just filled.
        STORE["items"] = [[7001, 301, 1]]
        STORE["equip"] = [[2, 7001]]
        del outbox[:]
        _dispatch(a, struct.pack(">HIB", 0x2098, 7001, 11))
        assert 0x2004 not in [m for m, _, _ in outbox], \
            [hex(m) for m, _, _ in outbox]

        # ---- one item per worn slot: a second slot-2 weapon evicts the first
        STORE["items"] = [[7001, 301, 1], [7004, 302, 1]]
        STORE["equip"] = []
        del outbox[:]
        _dispatch(a, struct.pack(">HIB", 0x2021, 7001, 1))
        _dispatch(a, struct.pack(">HIB", 0x2021, 7004, 1))
        assert STORE["equip"] == [[2, 7004]], STORE["equip"]

        # ---- 0xF102 DISCARD, from the bytes the client actually sent
        # 2026-09-06 when a live player dropped a spare wand. The uid is
        # the field to act on; the item number is a cross-check, because
        # that bag held TWO Beginner Wands and acting on the number would
        # have dropped the wrong one.
        STORE["items"] = [[7001, 301, 1], [7005, 531, 1], [7006, 531, 1]]
        STORE["equip"] = []
        del outbox[:]
        _dispatch(a, struct.pack(">HHIIHHH",
                                 0xF102, 0, 1, 7006, 531, 1, 0))
        assert [r[0] for r in STORE["items"]] == [7001, 7005], STORE["items"]
        assert 0x2004 not in [m for m, _, _ in outbox], \
            "nothing was worn, so no unequip"

        # dropping something you are WEARING takes it off first, or the
        # stored equipment names a uid the bag no longer has
        STORE["items"] = [[7001, 301, 1]]
        STORE["equip"] = [[2, 7001]]
        del outbox[:]
        _dispatch(a, struct.pack(">HHIIHHH",
                                 0xF102, 0, 1, 7001, 301, 1, 0))
        assert STORE["items"] == [], STORE["items"]
        assert STORE["equip"] == [], STORE["equip"]
        assert 0x2004 in [m for m, _, _ in outbox], \
            [hex(m) for m, _, _ in outbox]

        # a uid this bag does not have changes nothing and sends nothing
        STORE["items"] = [[7001, 301, 1]]
        STORE["equip"] = []
        del outbox[:]
        _dispatch(a, struct.pack(">HHIIHHH",
                                 0xF102, 0, 1, 999999, 301, 1, 0))
        assert STORE["items"] == [[7001, 301, 1]], STORE["items"]
        assert not outbox, [hex(m) for m, _, _ in outbox]

        STORE["items"] = [[7001, 301, 1], [7004, 302, 1]]
        STORE["equip"] = [[2, 7004]]

        # ---- a uid this character does not own is an NG, and pushes nothing
        del outbox[:]
        _dispatch(a, struct.pack(">HIB", 0x2021, 999999, 1))
        assert [m for m, _, _ in outbox] == [0x102B], \
            [hex(m) for m, _, _ in outbox]
        assert STORE["equip"] == [[2, 7004]], "an NG changes nothing"

        # ---- THE DEFAULT IS OFF, and it is off because it CRASHED THE
        # CLIENT on field load the first time it shipped (live 2026-09-05).
        # Pinned so a later edit cannot quietly re-arm it: the faulting RVA
        # has not been read yet, so nothing has earned the default back.
        # the switch's DEFAULT, read out of the source rather than the parser
        # (main() builds it inline), so the pin cannot pass by accident
        _src = io.open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "services", "feworld.py"),
            encoding="utf-8").read()
        # VERIFIED: The crash this used to guard is SOLVED and the fix is
        # verified live: 0x107A's header slot is a u32, and shipping it as a u16
        # put every byte after it TWO OUT, so the client read a garbage uid and
        # unit. The default is now "request" -- reflect a click the player made.
        # What is still NOT proved is "on", which adds the entry replay, so the
        # pin moved to that boundary rather than being deleted.
        assert '"--equip-reflect", default="request"' in _src, \
            "--equip-reflect should default to request (verified live 2026-09-06)"
        _tail = _src.split("--equip-reflect")[1][:200]
        assert 'default="on"' not in _tail, \
            "on adds the entry replay, which has never been verified live"
        # and which caller each value permits
        del outbox[:]
        _off = _args(equip_reflect="off")
        assert feworld.equip_reflect_push(None, None, 0, False, _off,
                                          [(1, 301, 0, 1)], "t", "entry") == 0
        assert feworld.equip_reflect_push(None, None, 0, False, _off,
                                          [(1, 301, 0, 1)], "t", "request") == 0
        _req = _args(equip_reflect="request")
        assert feworld.equip_reflect_push(None, None, 0, False, _req,
                                          [(1, 301, 0, 1)], "t", "entry") == 0,             "'request' must NOT replay on entry -- that is the half that crashed"
        assert feworld.equip_reflect_push(None, None, 0, False, _req,
                                          [(1, 301, 0, 1)], "t", "request") == 1
        assert not outbox or len(outbox) == 1
        del outbox[:]

        # ---- --equip-reflect off answers and stops, which is the old bug
        del outbox[:]
        _dispatch(_args(equip_reflect="off"), struct.pack(">HIB", 0x2021, 7001, 1))
        assert [m for m, _, _ in outbox] == [0x102A], \
            [hex(m) for m, _, _ in outbox]

        # ---- the stored equipment maps to worn indices, once each
        items = [(1, 301, 1), (2, 1165, 1), (3, 531, 1)]
        assert feworld.worn_layout([(2, 1), (4, 2)], items) == {1: 1, 2: 3}
        assert feworld.worn_layout([(2, 99)], items) == {}, "unknown uid dropped"
        # two type-8 items land in different scan slots
        assert feworld.worn_layout([(8, 1), (8, 2)], items) == {1: 7, 2: 8}

        # ---- THE SELL PRICE: 0x1086, the only writer of [item+0x4f0]
        STORE["items"] = [[7001, 301, 1], [7002, 1165, 1]]
        del outbox[:]
        # 09-11: half the item table's OWN price (+0x90: the beginner axe 30,
        # the casual shirt 20), not half a flat --shop-price
        rows = feworld.sell_value_rows(_args(sell_price=50, shop_price=100))
        assert rows == [(7001, 15), (7002, 10)], rows
        assert feworld.sell_value_rows(_args(sell_price=50, shop_price=100,
                                             shop_prices="flat")) == \
            [(7001, 50), (7002, 50)], "flat keeps the old --shop-price basis"
        feworld.sell_value_push(None, None, 0, False, _args(), rows)
        assert [m for m, _, _ in outbox] == [0x1086, 0x1086]
        assert outbox[0][2] == struct.pack(">II", 7001, 15)
        assert feworld.sell_value_rows(_args(sell_price=0)) == [], "0 disables"

        # ---- and it is NOT reachable through item_fields: no masked group
        # touches +0x4f0, which is why the bag record could never carry it
        assert len(feworld.item_fields(301)) == 4 + 2 + 4 + 4 + 4 + 2 + 2 + 2 + 2 + 1 + 4
    finally:
        feworld.send = real


def part15():
    """--skill-grant items: the prerequisite skills the BAG names, and no more.

    Validator 0x05076090 code 5 (arm 0x050761AC) passes when one of the item's
    up-to-two ids at [tbl+0x88] is owned in [unit+0xA14 + id*2]. `all` proved
    that live by granting the whole tree; this serves only what is needed.
    """
    import fegamedata
    _stub_store()
    STORE["skills"] = [270, 275]
    STORE["items"] = [[7001, 301, 1], [7002, 1165, 1], [7003, 531, 1]]
    outbox = []
    real = feworld.send

    def _send(conn, out, body, mode, be, echo=True, prefix=4):
        unit, mid = struct.unpack_from(">IH", body, 0)
        outbox.append((mid, unit, body[6:]))
    feworld.send = _send
    try:
        # the table itself: an axe wants 439, a wand 443, a shirt nothing
        tbl = fegamedata.items()
        assert tbl[301]["skills"] == [439], tbl[301]
        assert tbl[531]["skills"] == [443], tbl[531]
        assert tbl[1165]["skills"] == [], tbl[1165]
        assert fegamedata.item_skills([301, 531, 1165]) == [439, 443]
        # every row is either "none" or a plausible id -- an id the client's
        # own loop would skip (0, or >= 0x4a4) is a silent never-equip
        for no, row in tbl.items():
            for sid in row["skills"]:
                assert 0 < sid < 0x4A4, (no, sid)
        # and no row lists more than the two the record has room for
        assert max(len(r["skills"]) for r in tbl.values()) <= 2

        del outbox[:]
        feworld.skill_list_push(None, None, 0, False, _args(skill_grant="items"))
        ids = _skill_ids(outbox[0][2])
        assert set(ids) == {270, 275, 439, 443}, ids

        del outbox[:]
        feworld.skill_list_push(None, None, 0, False, _args(skill_grant="stored"))
        assert set(_skill_ids(outbox[0][2])) == {270, 275}

        del outbox[:]
        feworld.skill_list_push(None, None, 0, False, _args(skill_grant="all"))
        assert len(_skill_ids(outbox[0][2])) == feworld.SKILL_ARRAY_MAX, \
            "the probe grants the whole tree -- that is why it is not default"

        # an empty bag asks for nothing beyond what the character owns
        STORE["items"] = []
        del outbox[:]
        feworld.skill_list_push(None, None, 0, False, _args(skill_grant="items"))
        assert set(_skill_ids(outbox[0][2])) == {270, 275}
    finally:
        feworld.send = real


def part12():
    """THE EVENT VM: click -> 0x1071 D=script -> 0x20A7 -> 0x1174 op 3 text
    -> 0x20A8 ack -> 0x1174 op 8 (shop window) -> ack -> 0x1172 END; and the
    close 0x2099 -> 0x1152. Every body decoded with 0x5173a70's own reads."""
    import tempfile
    S = feworld._SESSION
    real_send, sent = feworld.send, []
    feworld.send = lambda *a, **kw: sent.append(a[2])
    d = tempfile.mkdtemp(prefix="feev-")
    tf = os.path.join(d, "fe_town.json")
    try:
        S.clear()
        S["in_field"], S["field"] = True, 39
        a = _args(town_file=tf, unit_id="1")
        feworld.town_add(a, 39, "npcs", {"model": 233, "kind": 1, "level": 1, "x": 0, "y": 0, "z": 0,
                                         "name": "Warrior_Weapon_Shop", "script": 2102})
        # the keeper was there when the player walked in: entry served it, so
        # there is no LIVE edit to push (town_live_refresh) ahead of the click
        feworld._TOWN_DIRTY.clear()
        # the click
        _dispatch(a, struct.pack(">H", 0x2046) + struct.pack(">I", 500))
        assert len(sent) == 1 and sent[0][4:6] == b"\x10q"
        assert Reader(sent[0][6:]).u32() == 500
        assert S["event"]["pc"] == 0 and len(S["event"]["steps"]) == 3, "text, shop, END window"
        # the starter's own ack, then its 0x20A7 -> first command
        del sent[:]
        _dispatch(a, struct.pack(">H", 0x20A8) + struct.pack(">IHI", 500, 0, 0))
        assert not sent, "the starter's ack must not consume a step"
        _dispatch(a, struct.pack(">H", 0x20A7) + struct.pack(">II", 500, 2102))
        assert len(sent) == 2 and sent[0][4:6] == b"\x11\x72" and len(sent[0]) == 6, \
            "0x20A7's registered reply 0x1172 first, header-only (live #6: without it " \
            "the pending table drew 'waiting...' over the open shop)"
        assert sent[1][4:6] == b"\x11t"
        assert sent[1][:4] == (500).to_bytes(4, "big"), "0x1174 is addressed to the NPC unit"
        r = Reader(sent[1][6:])
        assert r.u32() == 500 and r.u16() == 3, "the head u32 is the NPC UNIT (live #4: the script id there closed the shop)"
        n = r.u16(); txt = r.b[r.i:r.i + n]; r.i += n
        assert txt.startswith(b"Welcome") and r.i == len(r.b), "op 3 = [u16 len][bytes], fully consumed"
        # the dialogue window's click-through ack -> NOTHING yet (live #2:
        # the shop opened under the still-open dialogue box and its page
        # count was swallowed); the box's close notify 0x2085 -> the shop
        del sent[:]
        _dispatch(a, struct.pack(">H", 0x20A8) + struct.pack(">IHI", 2102, 6, 0))
        assert len(sent) == 1, "the click-through ack cues the window step"
        r = Reader(sent[0][6:])
        assert (r.u32(), r.u16(), r.u16(), r.u32()) == (500, 8, 1, 500) and r.i == len(r.b), \
            "op 8 = [u16 type][u32]; weapon shop = type 1"
        assert S["event"].get("in_window")
        assert S["shop"]["kind"] == "weapon" and S["shop"]["stock"], "a weapon shop has stock"
        # the shop's first request goes out from its constructor and is
        # answered AT ONCE (nothing is held: the box is not at [mgr+0xe8])
        del sent[:]
        _dispatch(a, struct.pack(">H", 0x2073) + struct.pack(">I", 1))
        assert len(sent) == 1 and sent[0][4:6] == b"\x11\x02"
        pages = Reader(sent[0][6:]).u32()
        assert pages == feworld.shop_pages(a, S["shop"]["stock"]) >= 1
        # the item list, page by page: [u32 page][u16 n]{7 x u32}, read the
        # way the page reader 0x50f5df0 reads it -- item, A, B, four masks
        import fegamedata
        weapon_slots = set(feworld.SHOP_SLOTS["weapon"])
        total = 0
        for pg in range(pages):
            del sent[:]
            _dispatch(a, struct.pack(">H", 0x204A) + struct.pack(">II", 7, pg))
            assert len(sent) == 1 and sent[0][4:6] == b"\x10\x7b"
            r = Reader(sent[0][6:])
            assert r.u32() == pg
            n = r.u16(); assert 1 <= n <= a.shop_page
            for i in range(n):
                no, gold, rings = r.u32(), r.u32(), r.u32()
                assert fegamedata.items()[no]["slot"] in weapon_slots
                # 09-11: the item table's own price, and the Warrior's stall
                # sells only what a Warrior's proficiencies can hold
                assert gold == fegamedata.items()[no]["price"] > 0 and rings == 0, \
                    "A = gold, B = RINGS (live #7: the stock index there read as 'rings required')"
                assert 0 in fegamedata.item_classes(no), (no, "not a Warrior's weapon")
                assert r.u32() == 2 and r.u16() == no, \
                    "mask1 bit1 = the u16 table key at obj+0x3a2 (live #5: 0xffff there CRASHED the row setup)"
                assert (r.u32(), r.u32(), r.u32()) == (0, 0, 0), "masks 2..4 empty"
            assert r.i == len(r.b), "the page reader consumes exactly this"
            total += n
        assert total == len(S["shop"]["stock"])
        # a page past the end is empty, not an error
        del sent[:]
        _dispatch(a, struct.pack(">H", 0x204A) + struct.pack(">II", 7, pages + 3))
        r = Reader(sent[0][6:]); r.u32(); assert r.u16() == 0 and r.i == len(r.b)
        # THE PURCHASE: 0x204B [shop][item][u16 count] -> stored on the
        # character (the suite's STORE stub), 0x107D header-only; a foreign
        # item -> NG
        STORE["items"] = [[7001, 4, 1]]
        S["account"], S["charid"] = "acct", 7
        try:
            del sent[:]
            _dispatch(a, struct.pack(">H", 0x204B) + struct.pack(">IIH", 500, 301, 2))
            # 0x107D, then the DELIVERY: one 0x107A per unit bought, appended
            # to the end of the bag so nothing else shifts. Before
            # 2026-09-06 this was the OK alone and the purchase showed up
            # only after a re-entry -- reported live as "I bought and sold
            # an item but my inventory didn't change".
            assert sent[0][4:6] == b"}" and len(sent[0]) == 6
            assert [m[4:6] for m in sent[1:]] == [b"z", b"z"]
            got = [tuple(x) for x in STORE["items"]]
            # four long since the Stack button: the fourth element is the
            # stack count, and a row that has never been stacked is 1
            assert got == [(7001, 4, 1, 1), (7002, 301, 1, 1),
                           (7003, 301, 1, 1)], got
            del sent[:]
            _dispatch(a, struct.pack(">H", 0x204B) + struct.pack(">IIH", 500, 4, 1))
            assert sent[0][4:6] == b"~", "bread is not sold at the weapon stall"
            # and the next 0x1000 would carry them: 3 items, page 0, mask 0b111
            r = Reader(feworld.bag_record(got, None, "0")); r.u8(); r.u8(); r.u8()
            assert r.u32() == 0 and r.u32() == 0b111
            r.u32(); r.u32(); r.u16(); r.u32(); r.u32(); r.u32(); r.u16()
            r.u16(); r.u16()                     # durability max, current
            r.u16(); r.u8()
            assert r.u32() == 0, "the bought axe lands in a numbered slot"
        finally:
            STORE.pop("items", None)
            S.pop("account", None); S.pop("charid", None)
        # the item shop sells food, the armour shop wears
        assert all(fegamedata.items()[no]["slot"] == 11 for no, _g, _r in feworld.shop_stock(a, "item"))
        assert feworld.shop_kind_for(a, "Heavy_Armor_Shop") == "armor"
        assert feworld.shop_kind_for(a, "", 3) == "item" and feworld.shop_kind_for(a, "", 4) == "ring"
        # the shop's CLOSE: 0x2085 [mode] (nothing sent) then the (npc, 8, 0)
        # ack, which cues the next command -- END, and the event is over
        del sent[:]
        _dispatch(a, struct.pack(">H", 0x2085) + struct.pack(">I", 0))
        assert not sent and "event" in S
        _dispatch(a, struct.pack(">H", 0x20A8) + struct.pack(">IHI", 500, 8, 0))
        assert len(sent) == 1 and sent[0][4:6] == b"\x11t", \
            "the closed window's ack -> the END WINDOW, never 0x1172 (live #1/#4: stuck)"
        r = Reader(sent[0][6:])
        assert (r.u32(), r.u16(), r.u16(), r.u32()) == (500, 8, 0x13, 500) and r.i == len(r.b)
        assert "event" in S and "shop" not in S
        # with no shop open, 0x204A falls back to the header-only table
        del sent[:]
        _dispatch(a, struct.pack(">H", 0x204A) + struct.pack(">II", 7, 0))
        assert len(sent) == 1 and sent[0][4:6] == b"\x10\x7b" and len(sent[0]) == 6
        # the END window's 0x2099 arrives as a BARE OUTER frame (live #7: the
        # op writes through a stack-local stream) -> 0x1152 [npc][""], and
        # the event is over
        del sent[:]
        _dispatch(a, struct.pack(">I", 500), outer=0x2099)
        assert len(sent) == 2 and sent[0][4:6] == b"\x11\x52"
        r = Reader(sent[0][6:]); assert r.u32() == 500 and r.cstr() == "" and r.i == len(r.b)
        assert sent[1][4:6] == b"\x11\x75" and len(sent[1]) == 6, \
            "the EVENT END is 0x1175 (arm 0x5056039); 0x1172's arm LOCKS (live #8/#9)"
        assert "event" not in S
        # a keeper with no window step: its END window step, then the close
        del sent[:]
        feworld.town_add(a, 39, "npcs", {"model": 230, "kind": 1, "level": 1, "x": 1, "y": 0, "z": 1,
                                         "name": "Inn_Master", "script": 3105})
        feworld._TOWN_DIRTY.clear()      # served on entry, as above
        _dispatch(a, struct.pack(">H", 0x2046) + struct.pack(">I", 501))
        _dispatch(a, struct.pack(">H", 0x20A8) + struct.pack(">IHI", 501, 0, 0))
        _dispatch(a, struct.pack(">H", 0x20A7) + struct.pack(">II", 501, 3105))
        del sent[:]
        _dispatch(a, struct.pack(">H", 0x20A8) + struct.pack(">IHI", 3105, 6, 0))
        assert len(sent) == 1
        r = Reader(sent[0][6:]); assert (r.u32(), r.u16(), r.u16(), r.u32()) == (501, 8, 0x13, 501)
        del sent[:]
        _dispatch(a, struct.pack(">I", 501), outer=0x2099)
        assert sent[0][4:6] == b"\x11\x52" and sent[1][4:6] == b"\x11\x75" and "event" not in S
        # and the enveloped form, should a build ever send it, answers too
        _dispatch(a, struct.pack(">H", 0x2099) + struct.pack(">I", 501))
        assert len(sent) == 4 and sent[2][4:6] == b"\x11\x52"
        # the console's raw-send probe
        del sent[:]
        import tempfile as _tf
        gm = os.path.join(d, "gm.txt")
        with open(gm, "w") as fh:
            fh.write("!send 1172\n!send 0x1174:000001f4000800130000000000\n")
        a2 = _args(town_file=tf, unit_id="1", gmcmd_file=gm)
        feworld.gm_pump(None, None, 0, False, a2)
        assert len(sent) == 2 and sent[0][4:6] == b"\x11\x72" and len(sent[0]) == 6
        assert sent[1][4:6] == b"\x11t" and len(sent[1]) == 6 + 13
        # menu encoding round-trips the interpreter's reads
        body = feworld.ev_body(7, feworld.EV_MENU, "Pick", ["Buy", "Sell"])
        r = Reader(body); assert r.u32() == 7 and r.u16() == 0x107
        n = r.u32(); r.i += n; assert r.u32() == 2
        for i in range(2):
            assert r.u32() == i; n = r.u32(); r.i += n
        assert r.i == len(body)
    finally:
        feworld.send = real_send
        S.clear()


def part16():
    """SORT (0x20AA), STACK (0x114D) and THE SKILL PALETTE (0x2029/0x202A).

    All three failed the way the equip failed: the acknowledgement was sent and
    nothing moved, because none of the OK arms reads the stream. Sort and Stack
    are answered with a 0x107A burst; the palette is answered with ITS OWN ID
    coming back, which is why it registers no reply pair.
    """
    _stub_store()
    feworld._SESSION["in_field"] = True
    outbox = []
    real = feworld.send

    def _send(conn, out, body, mode, be, echo=True, prefix=4):
        unit, mid = struct.unpack_from(">IH", body, 0)
        outbox.append((mid, unit, body[6:]))
    feworld.send = _send
    try:
        a = _args(field_items="bag")

        # ---- the stored row grew an OPTIONAL fourth element, so an old
        # roster still reads as a bag of singles
        assert feworld.item_rows([[1, 301, 1], [2, 43, 1, 5]]) == [
            (1, 301, 1, 1), (2, 43, 1, 5)]

        # ---- SORT. The order is CHOSEN (equipment by slot type, then
        # everything the table gives no slot to, then item number, then uid),
        # so what is pinned is the decision, not a measurement.
        #   301 = slot 2, 1165 = slot 4, 531 = slot 10, 43/44 = slot 0
        STORE["items"] = [[7001, 44, 1], [7002, 531, 1], [7003, 301, 1],
                          [7004, 1165, 1], [7005, 43, 1]]
        STORE["equip"] = []
        assert [r[1] for r in feworld.bag_sorted(STORE["items"])] == \
            [301, 1165, 531, 43, 44]

        del outbox[:]
        _dispatch(a, struct.pack(">H", 0x20AA))
        got = [m for m, _, _ in outbox]
        # the OK first -- it is what clears the window's in-flight state --
        # then one 0x107A per row that actually moved, which here is all five
        assert got[0] == 0x1179 and outbox[0][2] == b"", got
        assert set(got[1:]) == {0x107A} and len(got) == 6, got
        assert [r[0] for r in STORE["items"]] == [7003, 7004, 7002, 7005, 7001], \
            STORE["items"]
        # ...and it is PERSISTED, or the next field entry undoes it
        assert feworld.bag_record(STORE["items"], {}, "0")[3:11] == \
            struct.pack(">II", 0, 0x1F), "one page, a mask of five"
        r = Reader(outbox[1][2])
        assert r.u8() == 1 and r.u8() == 0, "add, no pickup notice"
        assert r.u32() == 0, "the axe goes to bag slot 0 -- a u32, never a u16"
        assert r.u32() == 7003

        # a second Sort of an already-sorted bag pushes NOTHING but still
        # answers, because the window is waiting on the OK either way
        del outbox[:]
        _dispatch(a, struct.pack(">H", 0x20AA))
        assert [m for m, _, _ in outbox] == [0x1179], \
            [hex(m) for m, _, _ in outbox]

        # ---- STACK. Only what the item table gives NO slot type to merges:
        # two axes are two axes. 43 and 44 are slot 0, 301 is slot 2.
        STORE["items"] = [[7001, 43, 1], [7002, 301, 1], [7003, 43, 1],
                          [7004, 301, 1], [7005, 44, 1], [7006, 43, 1]]
        STORE["equip"] = []
        new, gone = feworld.bag_stacked(STORE["items"])
        assert [(r[0], r[1], r[3]) for r in new] == [
            (7001, 43, 3), (7002, 301, 1), (7004, 301, 1), (7005, 44, 1)], new
        assert gone == [(7003, 2), (7006, 5)], gone
        # a worn item never merges, whatever its type
        n2, g2 = feworld.bag_stacked([[1, 43, 1], [2, 43, 1]], equip_uids=[2])
        assert len(n2) == 2 and g2 == [], (n2, g2)

        del outbox[:]
        _dispatch(a, struct.pack(">HI", 0x114D, 1))
        got = [m for m, _, _ in outbox]
        assert got[0] == 0x2095 and outbox[0][2] == b"", got
        # WARNING: THE REMOVES COME FIRST, at the slots those uids are in NOW. The
        # remove branch 0x0503d693 reads the uid out of the CLIENT's array by
        # slot and destroys the object; doing the moves first would orphan the
        # merged-away items and aim a later remove at a survivor.
        assert outbox[1][2] == feworld.item_remove_body(2), outbox[1][2].hex()
        assert outbox[2][2] == feworld.item_remove_body(5), outbox[2][2].hex()
        assert [r[0] for r in STORE["items"]] == [7001, 7002, 7004, 7005]
        assert STORE["items"][0][3] == 3, "the survivor carries the count"
        # the survivor is re-sent with its new count even though slot 0 did
        # not change -- a stack of three that still says one is the bug
        body = [b for m, _, b in outbox[3:]
                if struct.unpack_from(">I", b, 6)[0] == 7001]
        assert body and body[0] == feworld.item_add_body(7001, 43, 0, 3, None,
                                                         0, 0)
        # and the count reaches [item+0x4a8] through group 4, where the bag
        # record has always carried it
        assert feworld.item_fields(43, 3)[18:20] == struct.pack(">H", 3)
        # 2026-09-08: durability rides group 4 bits 0x4/0x8, right after the
        # count, as two i16 -- max -1 (infinite) and current 100 by default
        assert feworld.item_fields(43, 3)[20:24] == struct.pack(">hh", -1, 100)

        # ---- --bag-organize off is the old acknowledge-and-do-nothing
        del outbox[:]
        _dispatch(_args(bag_organize="off"), struct.pack(">H", 0x20AA))
        assert [m for m, _, _ in outbox] == [0x1179], \
            [hex(m) for m, _, _ in outbox]

        # ---- THE SKILL PALETTE. Captured live: 20 29 | 00 01 | 01 1D | 01 00
        # = mask bit 0 (palette slot 0), skill 285, then the two bytes the
        # client's builder writes and its own reader consumes.
        live = bytes.fromhex("2029000101 1d0100".replace(" ", ""))
        assert struct.unpack_from(">H", live, 2)[0] == 1
        assert struct.unpack_from(">H", live, 4)[0] == 285
        STORE.pop("palette", None)
        del outbox[:]
        _dispatch(a, live)
        assert [m for m, _, _ in outbox] == [0x2029], \
            [hex(m) for m, _, _ in outbox]
        # the ANSWER IS THE SAME ID AND THE SAME BYTES -- that is the whole
        # mechanism: the double-click sender (vtable 0x05297688 +0x24) and the
        # local setter (+0x28) are different methods and only the inbound
        # worker 0x0503CF60 calls the setter.
        assert outbox[0][2] == live[2:], (outbox[0][2].hex(), live[2:].hex())
        assert STORE["palette"] == [[0, 285]], STORE["palette"]

        # a second skill into slot 3, then a clear of slot 0
        del outbox[:]
        _dispatch(a, struct.pack(">HHHBB", 0x2029, 1 << 3, 305, 1, 0))
        assert STORE["palette"] == [[0, 285], [3, 305]], STORE["palette"]
        del outbox[:]
        _dispatch(a, struct.pack(">HH", 0x202A, 1 << 0))
        assert [m for m, _, _ in outbox] == [0x202A]
        assert outbox[0][2] == struct.pack(">H", 1), "the clear is mask-only"
        assert STORE["palette"] == [[3, 305]], STORE["palette"]

        # slots 8..15 are inside the 16-bit mask the worker loops over
        # (0x0503CFCC) but the local setter bails above 7 (0x0516236F), so
        # they are never stored
        del outbox[:]
        _dispatch(a, struct.pack(">HHHBB", 0x2029, 1 << 9, 285, 1, 0))
        assert STORE["palette"] == [[3, 305]], STORE["palette"]

        # the builders, read the way the workers read them
        assert feworld.palette_set_body([(0, 285), (3, 305)]) == \
            struct.pack(">HHBBHBB", 0b1001, 285, 1, 0, 305, 1, 0)
        assert feworld.palette_clear_body([0, 3]) == struct.pack(">H", 0b1001)

        # ---- the entry replay is the part that has never had a live run, and
        # both workers deref the HUD singletons with no null check. Pinned at
        # the boundary: 'request' echoes a click, only 'on' replays.
        STORE["palette"] = [[0, 285]]
        del outbox[:]
        assert feworld.palette_push(None, None, 0, False, _args(),
                                    feworld.palette_rows(_args()), "t") == 1
        feworld._SESSION.pop("in_field", None)
        assert feworld.palette_push(None, None, 0, False, _args(),
                                    feworld.palette_rows(_args()), "t") == 0, \
            "never before the HUD exists"
        feworld._SESSION["in_field"] = True
        _src = io.open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "services", "feworld.py"),
            encoding="utf-8").read()
        assert '"--skill-palette", default="request"' in _src, \
            "the entry replay has never been verified live"
        # ---- and off restores the silence
        del outbox[:]
        _dispatch(_args(skill_palette="off"), live)
        assert outbox == [], outbox
    finally:
        feworld.send = real
    print("[fe_ui_test] PART 16 ok -- Sort and Stack push the bag layout, the "
          "palette echoes its own id, and both are persisted")


def part17():
    """SELLING (0x204C), the purchase DELIVERY, and the ROOM EXIT.

    All three were reported live on 2026-09-06 within minutes of each other:
    "I bought and sold an item but my inventory didn't change", and "can't
    leave the tavern by any means".
    """
    _stub_store()
    feworld._SESSION["in_field"] = True
    feworld._SESSION["room"] = -1
    feworld._SESSION.pop("outside", None)
    outbox = []
    real = feworld.send

    def _send(conn, out, body, mode, be, echo=True, prefix=4):
        unit, mid = struct.unpack_from(">IH", body, 0)
        outbox.append((mid, unit, body[6:]))
    feworld.send = _send
    try:
        a = _args(field_items="bag")

        # ---- THE MEASURED SELL REQUEST, captured off prod 2026-09-06:
        #     20 4c | 00 00 01 f6 | 00 00 03 ee | 00 01
        # shop 502, uid 1006, count 1. WARNING: THE MIDDLE u32 IS A uid: the builder
        # fills it from 0x0507BA90(unit, [row+0x88], 0), the same "uid at bag
        # slot N" call the 0x107A remove uses. Reading it as an item number
        # would sell the wrong row whenever a bag held two of anything -- and
        # this bag does.
        live = bytes.fromhex("204c000001f6000003ee0001")
        assert struct.unpack_from(">I", live, 2)[0] == 502
        assert struct.unpack_from(">I", live, 6)[0] == 1006
        assert struct.unpack_from(">H", live, 10)[0] == 1 and len(live) == 12

        STORE["items"] = [[1001, 531, 1], [1002, 1666, 1], [1003, 531, 1]]
        STORE["equip"] = []
        del outbox[:]
        _dispatch(a, struct.pack(">HIIH", 0x204C, 502, 1002, 1))
        got = [m for m, _, _ in outbox]
        assert got[0] == 0x1080 and outbox[0][2] == b"", got
        # the remove goes at the slot the uid is in NOW (1), then 1003 moves
        # up into it -- the OK moves nothing at all
        assert outbox[1][2] == feworld.item_remove_body(1), outbox[1][2].hex()
        assert [tuple(r) for r in STORE["items"]] == [
            (1001, 531, 1, 1), (1003, 531, 1, 1)], STORE["items"]

        # ---- a uid this bag does not hold is an NG, and changes nothing
        del outbox[:]
        _dispatch(a, struct.pack(">HIIH", 0x204C, 502, 999999, 1))
        assert [m for m, _, _ in outbox] == [0x1081],             [hex(m) for m, _, _ in outbox]
        assert len(STORE["items"]) == 2, "an NG sells nothing"

        # ---- and so is selling what you are WEARING. The client builds its
        # sell list off the bag array, which still holds worn items, so this
        # is reachable; refusing keeps the equip pair from dangling.
        STORE["equip"] = [[2, 1001]]
        del outbox[:]
        _dispatch(a, struct.pack(">HIIH", 0x204C, 502, 1001, 1))
        assert [m for m, _, _ in outbox] == [0x1081],             [hex(m) for m, _, _ in outbox]
        assert len(STORE["items"]) == 2

        # ---- a PARTIAL stack keeps its slot: nothing is removed, the row is
        # re-sent with fewer in it
        STORE["items"] = [[1001, 43, 1, 5]]
        STORE["equip"] = []
        del outbox[:]
        _dispatch(a, struct.pack(">HIIH", 0x204C, 502, 1001, 2))
        assert [m for m, _, _ in outbox] == [0x1080, 0x107A],             [hex(m) for m, _, _ in outbox]
        assert outbox[1][2] == feworld.item_add_body(1001, 43, 0, 3, None, 0, 0)
        assert STORE["items"] == [[1001, 43, 1, 3]], STORE["items"]

        # ---- EATING THE CHEESE. The measured request, captured live
        # 2026-09-06: uid 1011 was item 7, bought minutes earlier.
        #     20 51 | 00 00 03 f3 | 00 00 00 01 | 00 01 0f 35 00 01 14 e0
        #           | 00 | 00 00 00 01 | 00 00 00 01                (25 body)
        live_use = bytes.fromhex(
            "2051000003f30000000100010f35000114e00000000001" "00000001")
        assert struct.unpack_from(">I", live_use, 2)[0] == 1011
        assert len(live_use) - 2 == 25, len(live_use) - 2

        STORE["items"] = [[1011, 7, 1, 3], [1012, 19, 1]]
        STORE["equip"] = []
        del outbox[:]
        _dispatch(a, live_use)
        assert [m for m, _, _ in outbox] == [0x1089, 0x107A],             [hex(m) for m, _, _ in outbox]
        # THE OK CARRIES THE uid AND GOES FIRST: its arm (0x050580EC) looks
        # that object up and clears [obj+0x4ec] bit 0, and the 0x107A below
        # would have destroyed it.
        assert outbox[0][2] == struct.pack(">II", 0, 1011), outbox[0][2].hex()
        # a stack of three loses ONE and keeps its slot -- `arg1` is not
        # confirmed to be a count, so it is logged, not obeyed
        assert STORE["items"] == [[1011, 7, 1, 2], [1012, 19, 1, 1]],             STORE["items"]
        assert outbox[1][2] == feworld.item_add_body(1011, 7, 0, 2, None, 0, 0)

        # the LAST one empties the slot instead
        STORE["items"] = [[1011, 7, 1, 1]]
        del outbox[:]
        _dispatch(a, live_use)
        assert [m for m, _, _ in outbox] == [0x1089, 0x107A]
        assert outbox[1][2] == feworld.item_remove_body(0)
        assert STORE["items"] == [], STORE["items"]

        # a uid the bag does not hold is an NG and eats nothing
        del outbox[:]
        _dispatch(a, live_use)
        assert [m for m, _, _ in outbox] == [0x108A],             [hex(m) for m, _, _ in outbox]

        # ---- THE ROOM EXIT. Outdoors it must do NOTHING: 0x1166 with A equal
        # to the current field and B = -1 is a same-field warp, so an unguarded
        # call teleports the player to the spawn point every time a
        # conversation ends.
        feworld._SESSION["room"] = -1
        feworld._SESSION["field"] = 39
        del outbox[:]
        assert feworld.room_exit_push(None, None, 0, False, a, "t") == 0
        assert outbox == [], outbox

        # ...and in a room it pushes 0x1166 back to the field the door came
        # from. A room has no door of its own, so this is the only way out
        # that is not a hand-typed !goto.
        feworld._SESSION["room"] = 10
        feworld._SESSION["outside"] = 5
        del outbox[:]
        assert feworld.room_exit_push(None, None, 0, False, a, "t") == 1
        assert [m for m, _, _ in outbox] == [0x1166]
        assert struct.unpack_from(">II", outbox[0][2], 0) == (5, 0xFFFFFFFF)

        # ---- ONCE PER ROOM. A conversation can end down both paths in
        # quick succession, and [room] does not change until the client answers
        # our 0x1166 with its own 0x2000 -- so a second push would transition
        # the player twice.
        del outbox[:]
        assert feworld.room_exit_push(None, None, 0, False, a, "t") == 0
        assert outbox == [], "the exit for this room already went out"

        # ---- the conversation close carries it: 0x1152, 0x1175, then the exit
        feworld._SESSION.pop("room_exit_sent", None)
        del outbox[:]
        feworld.conversation_close(None, None, 0, False, a,
                                   struct.pack(">I", 77), "test")
        assert [m for m, _, _ in outbox] == [0x1152, 0x1175, 0x1166],             [hex(m) for m, _, _ in outbox]

        # ---- and so does the OTHER ending, the one where the script simply
        # runs out. A `say`-only NPC inside a room is the only way out of it,
        # so this path has to offer the exit too -- it did not until
        # 2026-09-06, which made a plain talking NPC a dead end.
        feworld._SESSION.pop("room_exit_sent", None)
        feworld._SESSION["event"] = {"npc": 501, "script": 0, "steps": [],
                                     "pc": 0, "name": "t"}
        del outbox[:]
        feworld.event_step(None, None, 0, False, a)
        assert [m for m, _, _ in outbox] == [0x1175, 0x1166],             [hex(m) for m, _, _ in outbox]
        assert feworld._SESSION.get("event") is None

        # ---- --room-exit off restores the trap, deliberately
        feworld._SESSION.pop("room_exit_sent", None)
        feworld._SESSION["room"] = 10
        del outbox[:]
        assert feworld.room_exit_push(None, None, 0, False,
                                      _args(room_exit="off"), "t") == 0
        assert outbox == [], outbox
    finally:
        feworld.send = real
        feworld._SESSION["room"] = -1
        feworld._SESSION.pop("outside", None)
        feworld._SESSION.pop("room_exit_sent", None)
        feworld._SESSION.pop("event", None)
    print("[fe_ui_test] PART 17 ok -- selling empties the slot, buying fills "
          "one, and a conversation walks the player out of a room")


def part18():
    """THE CASTLE GATE GUARD: a town-file row whose conversation is the door.

    The castle gate is NOT a door volume (measured), so the way in is an NPC
    whose script ends in the same 0x1166 transition the street doors use.
    """
    a = _args()

    # ---- the OVERRIDE beats the name, and used to be thrown away without it.
    # `if not name: return []` ran BEFORE the event check, so a hand-placed
    # guard on a model the dat.pak type table does not name said nothing at
    # all, silently.
    row = {"model": 239, "name": "", "script": 0,
           "event": "say:Halt! State your business.;goto:15"}
    steps = feworld.event_script_for(a, row)
    assert steps == [("text", "Halt! State your business."),
                     ("goto", feworld.ROOM_AREA_BASE + 15)], steps
    # room 15 is the castle interior, and it is inside the client's own switch
    assert feworld.SHIPPED_ROOMS[15] == "R1_castle00_room_3D"
    assert 15 in feworld.ROOM_INDEX_WINDOW
    # a bare goto still gets a greeting, because the client needs a step to ack
    assert feworld.event_script_for(a, dict(row, event="goto:15"))[0][0] == "text"
    # and a row with neither a name nor an event still plays nothing
    assert feworld.event_script_for(a, {"model": 239, "name": ""}) == []

    # ---- a target OUTSIDE the room window is a field id, not a room
    assert feworld.event_script_for(a, dict(row, event="goto:39"))[-1] ==         ("goto", 39)

    # ---- `!town set` VALUES MAY CONTAIN SPACES. The parser used to be
    # line.split(), so `event=say:Halt there;goto:15` kept only "say:Halt"
    # and the guard greeted nobody.
    def _changes(text):
        rest = ("set npcs 0 " + text).split()
        tail = " ".join(rest[3:])
        keys = list(re.finditer(r"(?:^|\s)([A-Za-z_][A-Za-z_0-9]*)=", tail))
        out = {}
        for n, m in enumerate(keys):
            end = keys[n + 1].start() if n + 1 < len(keys) else len(tail)
            out[m.group(1)] = tail[m.end():end].strip()
        return out
    assert _changes("event=say:Halt there;goto:15") == {
        "event": "say:Halt there;goto:15"}
    assert _changes("name=Item_Shop yaw=180") == {"name": "Item_Shop",
                                                  "yaw": "180"}
    # a value may even contain '=' -- a new key needs whitespace before it
    assert _changes("event=say:a=b c;goto:15 yaw=90") == {
        "event": "say:a=b c;goto:15", "yaw": "90"}
    # ---- WARNING: A DOOR MUST NOT OPEN INTO A ROOM WITH NO WAY OUT. --door-default
    # is 10 in prod and room 10 has no keeper, so touching any unmapped door
    # would have dropped the player into an empty room and held them there
    # until the read window closed the session -- the tavern trap of
    # 2026-09-06 with a stranger's door instead of a stranger's dialogue.
    S = feworld._SESSION
    real_send, sent = feworld.send, []
    feworld.send = lambda *a, **kw: sent.append(a[2])
    d = tempfile.mkdtemp(prefix="fedoor-")
    tf = os.path.join(d, "fe_town.json")
    try:
        S.clear()
        S["in_field"], S["field"] = True, 39
        ad = _args(town_file=tf, door_default=10, unit_id="1")
        door = struct.pack(">H", 0x20AC) + struct.pack(">fff", -50.0, 0.0, 20.0)

        # room 10 is empty -> the door's own NG, which clears the in-flight
        # flag so the NEXT door still works
        _dispatch(ad, door)
        assert len(sent) == 1 and sent[0][4:6] == b"g", sent
        assert struct.unpack_from(">I", sent[0][6:], 0)[0] == 4

        # put somebody in it and the same door opens
        feworld.town_add(ad, "room:10", "npcs",
                         {"model": 233, "kind": 1, "level": 1,
                          "x": 0.0, "y": 0.0, "z": 0.0,
                          "name": "Warrior_Weapon_Shop", "script": 2102})
        del sent[:]
        S["in_field"], S["field"] = True, 39
        _dispatch(ad, door)
        assert len(sent) == 1 and sent[0][4:6] == b"f", sent
        assert struct.unpack_from(">I", sent[0][6:], 0)[0] ==             feworld.ROOM_AREA_BASE + 10

        # ...and with NO town file at all the guard stays out of the way: a
        # server that does not author rooms is not authoring traps either
        del sent[:]
        S["in_field"], S["field"] = True, 39
        _dispatch(_args(door_default=10, unit_id="1"), door)
        assert len(sent) == 1 and sent[0][4:6] == b"f", sent
    finally:
        feworld.send = real_send
        S.clear()
        shutil.rmtree(d, ignore_errors=True)

    # ---- THE CAPITAL TABLE, read off the client at 0x052D1880. Every capital
    # ships as TWO maps under TWO group ids, and the pairing is NOT in id order
    # -- which retracts the old guess that gid 39 loads map01_00.
    # WARNING: The id is the FIRST dword of a table entry, not the last. Reading it
    # as the last shifted every pairing by one row, went live, and dropped the
    # player into the void: 39's partner is 92, and 91 is capital 21's.
    # The CLIENT'S OWN LOG is what settled it -- polshim.<pid>.log carries
    # `[DATA\capital\map01_00.oct] Stand-By ok.` on entering 39.
    assert feworld.CAPITAL_MAP[39] == "DATA/capital/map01_00.oct"
    assert feworld.CAPITAL_MAP[91] == "DATA/capital/map00_01.oct"
    assert feworld.CAPITAL_MAP[21] == "DATA/capital/map00_00.oct"
    assert feworld.CAPITAL_OTHER_HALF[39] == 92, "39 pairs with 92, not 91"
    assert set(feworld.CAPITAL_MAP) <= set(feworld.CAPITAL_GROUP_IDS)
    # the halves pair up both ways, and nothing pairs with itself
    for a_, b_ in feworld.CAPITAL_OTHER_HALF.items():
        assert feworld.CAPITAL_OTHER_HALF[b_] == a_ and a_ != b_, (a_, b_)

    # ---- a door may target a GROUP, not just a room: same convention the
    # event `goto:N` parser uses, and the keeper check does not apply because a
    # group is a whole field rather than a room with one way out
    S = feworld._SESSION
    real_send2, sent2 = feworld.send, []
    feworld.send = lambda *a, **kw: sent2.append(a[2])
    d2 = tempfile.mkdtemp(prefix="fegrp-")
    tf2 = os.path.join(d2, "fe_town.json")
    try:
        S.clear()
        S["in_field"], S["field"] = True, 39
        ag = _args(town_file=tf2, door_default=91, unit_id="1")
        _dispatch(ag, struct.pack(">H", 0x20AC) + struct.pack(">fff", 1.0, 0, 2.0))
        assert len(sent2) == 1 and sent2[0][4:6] == b"f", sent2
        assert struct.unpack_from(">I", sent2[0][6:], 0)[0] == 91,             "a group id goes out AS IS, not ROOM_AREA_BASE + it"
    finally:
        feworld.send = real_send2
        S.clear()
        shutil.rmtree(d2, ignore_errors=True)

    # ---- WARNING: A POSITION DOES NOT SURVIVE AN AREA CHANGE. `!npc` places at the
    # reported position, and after a transition that is where the player USED
    # to be. Live 2026-09-06: a !goto into a room followed straight away by
    # !npc put the keeper at the player's OUTDOOR coordinates, unreachable, in
    # a room whose only way out is talking to somebody. Pinned from the source
    # because enter_area wants the whole argument surface; what matters is that
    # the drop sits WITH the room assignment and cannot drift away from it.
    _src = io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "services", "feworld.py"),
        encoding="utf-8").read()
    _blk = _src[_src.index('    _SESSION["room"] = room'):]
    _blk = _blk[:_blk.index(chr(10) + "def ")]
    assert '_SESSION.pop("cpos", None)' in _blk, \
        "entering an area must drop the stale reported position"

    print("[fe_ui_test] PART 18 ok -- an event= override plays without a name, "
          "and its value may contain spaces")


def _dispatch(a, inner, outer=0x30):
    """Run ONE inner message through _serve_loop's dispatch with a stubbed
    transport: the crypto layer is replaced so `inner` arrives as-is.
    `outer` != 0x30 delivers a BARE outer frame with `inner` as its body."""
    class _Conn:
        def __init__(self, frames):
            self.frames = list(frames)

        def recv(self, n):
            raise OSError("done")

        def settimeout(self, t):
            pass

        def getpeername(self):
            return ("127.0.0.1", 1)
    saved = (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap)
    frames = [(outer, inner)]
    fenet.recv_frame = lambda c: frames.pop(0) if frames else None
    fenet.bf_decrypt = lambda s, b, m, be: b
    fenet.traffic_unwrap = lambda p: (1, p)
    try:
        feworld._serve_loop(_Conn([]), a, None, None, "ecb", False)
    finally:
        fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap = saved


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
        _saved_out = sys.stdout
        sys.stdout = _Null()
    try:
        part1()
        part2()
        part3()
        part4()
        part5()
        part6()
        part7()
        part8()
        part9()
        part10()
        part11()
        part12()
        part13()
        part14()
        part15()
        part16()
        part17()
        part18()
        _bagsize_pinned()
        _class_levels_pinned()
    finally:
        if quiet:
            sys.stdout = _saved_out
    print("[fe_ui_test] OK")
