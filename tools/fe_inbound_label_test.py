#!/usr/bin/env python3
"""fe_inbound_label_test.py -- the read loop names every id the client can
send, answers 0x4010, and logs a client id no arm answers by name with a
decoded body. No client; frames go through the REAL _serve_loop with the
crypto stubbed (the fe_mail_test harness).

    python tools/fe_inbound_label_test.py

WHY. The per-message log line used to print "NOT SEEN BEFORE -- this is the
finding" for any id missing from messages.KNOWN. It fired for 18 HANDLED ids
(0x400A on every war heartbeat), so a genuinely new id could not be told from
a war heartbeat.

FE_MSG_INVENTORY=PATH (optional) names a send-site inventory TSV (id, then
in/out/both); when set, CLIENT_SENT is compared against its out/both ids.
"""
import io
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
from world import messages, readloop  # noqa: E402

OUT = sys.__stdout__
CHECKS = []

#: The 18 handled ids the old log labelled NOT SEEN BEFORE.
PROD_MISLABELLED = (0x400A, 0x4011, 0x206A, 0xA011, 0x400B, 0x4015, 0x2051,
                    0x209A, 0x4012, 0x4014, 0x2029, 0x2098, 0x204D, 0x114D,
                    0x20AB, 0x209C, 0x204C, 0x202A)
INVENTORY = os.environ.get("FE_MSG_INVENTORY") or ""


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-66s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label)


def _args(**kw):
    a = dict(unit_id="0", seq_mode="count", world_prefix=4, read_window=1.0,
             gmcmd=None, gmcmd_file=None, probe_on_auth=False,
             ui_auto="ok", ui_auto_err=0, move_request="off",
             chat_relay="off", chat_echo="off", chat_line=None,
             validate_finish=0, war_start="off", war_clock="off",
             war_deadline_ms=0, gold=None, system_log=None)
    a.update(kw)
    return types.SimpleNamespace(**a)


class _Conn:
    def settimeout(self, t):
        pass

    def getpeername(self):
        return ("127.0.0.1", 1)


def _drive(a, frames):
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


def main():
    with io.StringIO() as quiet:
        real = sys.stdout
        sys.stdout = quiet
        try:
            feworld.load_extensions(list(feworld.EXT_MODULES))
        finally:
            sys.stdout = real

    say("the client-sent table")
    check("CLIENT_SENT names 135 ids", len(messages.CLIENT_SENT) == 135,
          len(messages.CLIENT_SENT))
    if INVENTORY and os.path.exists(INVENTORY):
        inv = set()
        with io.open(INVENTORY, encoding="utf-8") as fh:
            for ln in fh.read().splitlines()[1:]:
                f = ln.split("\t")
                if len(f) > 1 and f[1] in ("out", "both"):
                    inv.add(int(f[0], 16))
        check("...exactly the send-site inventory's out/both ids",
              inv == set(messages.CLIENT_SENT),
              sorted(hex(x) for x in inv ^ set(messages.CLIENT_SENT)))
    else:
        say("  (FE_MSG_INVENTORY not set or not found; set comparison "
            "skipped)")
    bad = [hex(i) for i in PROD_MISLABELLED
           if "NOT SEEN BEFORE" in readloop.inbound_label(i)]
    check("none of the 18 ids once mislabelled reads NOT SEEN BEFORE", not bad,
          bad)
    no_arm = [hex(i) for i in PROD_MISLABELLED if readloop.handler_of(i) is None]
    check("...and each of them names an arm", not no_arm, no_arm)
    check("an id outside the inventory still reads as the finding",
          "NOT SEEN BEFORE" in readloop.inbound_label(0x7777))
    check("0x1036 is named with its arm (feunit, since 2026-10-08)",
          "remove my effects" in readloop.inbound_label(0x1036)
          and "[ext:feunit]" in readloop.inbound_label(0x1036),
          readloop.inbound_label(0x1036))
    check("0xE00D (a debug verb) is named and flagged as having no arm",
          "NO ARM" in readloop.inbound_label(0xE00D),
          readloop.inbound_label(0xE00D))
    check("0x400A is named with its readloop arm",
          readloop.inbound_label(0x400A).endswith("[readloop]"),
          readloop.inbound_label(0x400A))
    check("a module-owned id names the module (0xA050 -> femail)",
          "[ext:femail]" in readloop.inbound_label(0xA050),
          readloop.inbound_label(0xA050))

    say("0x4010 MSG_GET_SYSTEM_LOG_REQUEST")
    out, log = _drive(_args(), [struct.pack(">H", 0x4010)])
    check("no body -> 0x302D [u8 0] (an empty, valid log)",
          [(m, b) for m, _u, b in out] == [(0x302D, b"\x00")], out)
    out, _ = _drive(_args(system_log=["Server restarts at 04:00.", "x" * 600]),
                    [struct.pack(">H", 0x4010)])
    body = out[0][2] if out else b""
    rows = body[1:].split(b"\x00")
    check("--system-log lines: [u8 n] n x cstr, each under 0x200",
          body[:1] == b"\x02" and rows[0] == b"Server restarts at 04:00."
          and len(rows[1]) == 0x1FF and rows[2:] == [b""], body[:40])
    out, _ = _drive(_args(system_log=["l%d" % i for i in range(30)]),
                    [struct.pack(">H", 0x4010)])
    check("at most 20 lines (the reader's 0x2800 block of 0x200 rows)",
          out and out[0][2][0] == 20, out[0][2][:1] if out else out)

    say("unanswered client ids")
    out, log = _drive(_args(), [struct.pack(">HBHH", 0xE00D, 2, 0x0101, 0x0203)])
    check("0xE00D -> no reply, logged UNHANDLED with its body",
          out == [] and "UNHANDLED 0xE00D" in log, log[-300:])
    out, log = _drive(_args(), [struct.pack(">HI", 0x7777, 1)])
    check("an unknown id -> the finding label and the UNHANDLED line",
          out == [] and "NOT SEEN BEFORE" in log and "UNHANDLED 0x7777" in log)
    out, log = _drive(_args(), [struct.pack(">HI", 0x2016, 1)])
    check("a handled id prints no UNHANDLED line",
          [m for m, _u, _b in out] == [0x1010] and "UNHANDLED" not in log
          and "NOT SEEN BEFORE" not in log, log[-300:])

    say("[fe_inbound_label_test] %d checks" % len(CHECKS))
    say("[fe_inbound_label_test] OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        say("[fe_inbound_label_test] FAIL: %s" % e)
        sys.exit(1)
