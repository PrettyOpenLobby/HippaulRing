#!/usr/bin/env python3
"""fe_clock_connect_test.py -- --world-clock connect: the base at LOGIN.

Run from services/:  python ../tools/fe_clock_connect_test.py

WHY. Observed in live testing: under 'session' the player arrived in a field
at NIGHT (the unset clock) and the sky jumped to day ~12 s later, when the
0x1148 landed. The
client's clock counts from its world CONNECT (measured: 20890 ms at
21:35:30.369 for a CONNECT at 21:35:09.477), so the base can go out with the
0x302B login OK -- before any field loads -- as plain server epoch ms.

This drives the real functions: clock_connect_push, war_abs_deadline,
note_client_clock's verification and clock_sync_push as the fallback.
"""
import os
import struct
import sys
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import feworld      # noqa: E402

CHECKS = []
OUT = sys.__stdout__
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-66s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label + (": " + detail if detail else ""))


def _args(**kw):
    a = dict(world_clock="connect", world_clock_after_ms=10000,
             war_clock="telemetry", clock_epoch_ms=0, seq_mode="count",
             world_prefix=4)
    a.update(kw)
    return types.SimpleNamespace(**a)


def sample(ms):
    f = bytearray(28)
    struct.pack_into(">II", f, 4, (ms >> 32) & 0xFFFFFFFF, ms & 0xFFFFFFFF)
    return bytes(f)


def main():
    feworld.load_extensions()
    sent = []
    real_inner = feworld.inner_msg

    def inner(mid, payload=b"", *a, **k):
        sent.append((mid, bytes(payload)))
        return real_inner(mid, payload, *a, **k)

    saved = (feworld.send, feworld.inner_msg)
    feworld.send = lambda *a, **k: None
    feworld.inner_msg = inner

    def fresh(**kw):
        feworld._TLS.session = dict(kw)
        del sent[:]
        return feworld._TLS.session

    def clocks():
        return [struct.unpack(">II", p) for m, p in sent if m == 0x1148]

    def login(args):
        return feworld.clock_connect_push(None, None, "ecb", False, args)

    try:
        say("at login")
        S = fresh()
        check("--world-clock session: nothing at login",
              login(_args(world_clock="session")) is False and not clocks())
        check("--world-clock connect: ONE 0x1148 at login",
              login(_args()) is True and len(clocks()) == 1)
        hi, lo = clocks()[0]
        epoch = int(time.time() * 1000)
        check("base = server epoch ms (the client counts from its connect)",
              abs(((hi << 32) | lo) - epoch) < 2000, "%d" % ((hi << 32) | lo))
        check("our estimate is seeded: client_now_ms ~ epoch before any sample",
              abs(feworld.client_now_ms() - epoch) < 2000)
        check("not twice on one connection",
              login(_args()) is False and len(clocks()) == 1)
        dl, kind = feworld.war_abs_deadline(_args(), 60000)
        check("a map-screen war deadline is ABSOLUTE in the new units",
              kind == "client" and abs(dl - (epoch + 60000)) < 2000,
              repr((dl, kind)))
        check("the entry reset keeps the login flags",
              "clock_connect_sent" not in feworld.FIELD_ENTRY_CLOCK_CLEAR
              and "clock_verified" not in feworld.FIELD_ENTRY_CLOCK_CLEAR)

        say("the first sample in the field verifies it")
        for k in feworld.FIELD_ENTRY_CLOCK_CLEAR:
            S.pop(k, None)
        S.update(in_field=True, field_ready=True,
                 field_ready_at=time.monotonic() - 60)
        feworld.note_client_clock(sample(epoch + 25000))
        check("held: verified, still synced",
              S.get("clock_verified") is True and S.get("clock_synced") is True)
        check("...and the session path sends nothing more",
              feworld.clock_sync_push(None, None, "ecb", False, _args()) is False
              and len(clocks()) == 1)

        say("a base that did NOT hold (dropped / a RESUME zeroed it)")
        S = fresh()
        login(_args())
        for k in feworld.FIELD_ENTRY_CLOCK_CLEAR:
            S.pop(k, None)
        S.update(in_field=True, field_ready=True,
                 field_ready_at=time.monotonic() - 60)
        feworld.note_client_clock(sample(20890))
        check("a raw first sample clears clock_synced",
              S.get("clock_synced") is False and S.get("clock_verified") is True)
        check("the session path then sends it, from the raw clock",
              feworld.clock_sync_push(None, None, "ecb", False, _args()) is True
              and len(clocks()) == 2)
        hi, lo = clocks()[1]
        check("...with base = epoch - the raw clock",
              abs(((hi << 32) | lo) + 20890 - int(time.time() * 1000)) < 2000)
        check("and only once", feworld.clock_sync_push(
            None, None, "ecb", False, _args()) is False and len(clocks()) == 2)
    finally:
        feworld.send, feworld.inner_msg = saved

    say("\n%d/%d checks passed" % (sum(ok for _, ok in CHECKS), len(CHECKS)))


if __name__ == "__main__":
    main()
