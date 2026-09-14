#!/usr/bin/env python3
"""fe_clock_test.py -- the 0x1148 world clock, once per CONNECTION.

Run from services/:  python ../tools/fe_clock_test.py

WHY. --world-clock first shipped as a send per FIELD ENTRY and parked every
entry in mode 0xb substate 3 (3d943ded turned it off). Reading it again:

  * the base lasts the connection, and each entry re-armed the send, so the
    second entry computed base = epoch - now from a clock already reading
    epoch -- ~0 -- and put the world clock back ~56 years (entries alternated)
  * a base landing under an armed war countdown (client units) makes it stale
  * fepresence mapped peers into the receiver's clock by B alone with A = 0,
    so one synced player and one unsynced one broke remote walking both ways

This drives the real functions through the thread-local session, the way
fe_campaign_test does: clock_sync_push, note_client_clock, gm_pump's `!clock`,
the entry-reset list the 0x2000 handler uses, fepresence.rebase_clock.
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
    a = dict(world_clock="session", world_clock_after_ms=10000,
             war_clock="telemetry", clock_epoch_ms=0, seq_mode="count",
             world_prefix=4, gmcmd_file=None, devtool_port=0)
    a.update(kw)
    return types.SimpleNamespace(**a)


def sample(ms):
    """A 28-byte 0x2023 action-0 body carrying the client clock `ms`."""
    f = bytearray(28)
    struct.pack_into(">II", f, 4, (ms >> 32) & 0xFFFFFFFF, ms & 0xFFFFFFFF)
    return bytes(f)


def main():
    feworld.load_extensions()          # binds fepresence.fw, as prod does
    import fepresence
    sent = []
    real_inner = feworld.inner_msg

    def inner(mid, payload=b""):
        sent.append((mid, bytes(payload)))
        return real_inner(mid, payload)

    saved = (feworld.send, feworld.inner_msg)
    feworld.send = lambda *a, **k: None
    feworld.inner_msg = inner

    def fresh(**kw):
        feworld._TLS.session = dict(kw)
        del sent[:]
        return feworld._TLS.session

    def clocks():
        return [p for m, p in sent if m == 0x1148]

    def push(args):
        return feworld.clock_sync_push(None, None, "ecb", False, args)

    try:
        ready = dict(in_field=True, field_ready=True)
        raw = 3 * 60 * 1000             # 3 minutes since connect: unsynced

        say("off")
        S = fresh(field_ready_at=time.monotonic() - 60, **ready)
        feworld.note_client_clock(sample(raw))
        check("--world-clock off: nothing, however ready the field is",
              push(_args(world_clock="off")) is False and not clocks())

        say("session: waits past the field-ready batch, then ONE send")
        S = fresh(field_ready_at=time.monotonic(), **ready)
        feworld.note_client_clock(sample(raw))
        check("inside --world-clock-after-ms of field ready: not yet",
              push(_args()) is False and not clocks())
        S["field_ready_at"] = time.monotonic() - 11
        check("past it: sent", push(_args()) is True and len(clocks()) == 1)
        hi, lo = struct.unpack(">II", clocks()[0])
        base = (hi << 32) | lo
        epoch = int(time.time() * 1000)
        check("base = server epoch ms - the client's clock",
              abs(base + raw - epoch) < 2000, "base %d" % base)
        check("...and our estimate of the client's clock moved with it",
              abs(feworld.client_now_ms() - epoch) < 2000)
        check("a second call on the same connection sends nothing",
              push(_args()) is False and len(clocks()) == 1)

        say("a second FIELD ENTRY on the same connection")
        check("the entry reset does not clear clock_synced",
              "clock_synced" not in feworld.FIELD_ENTRY_CLOCK_CLEAR)
        for k in feworld.FIELD_ENTRY_CLOCK_CLEAR:
            S.pop(k, None)
        S["field_ready_at"] = time.monotonic() - 60
        feworld.note_client_clock(sample(epoch + 5000))   # already based
        check("no resend after re-entry", push(_args()) is False
              and len(clocks()) == 1)
        # the old per-entry arithmetic, for the record: ~0, i.e. 56 years back
        old_base = int(time.time() * 1000) - feworld.client_now_ms()
        check("(what the per-entry resend computed: base ~0 -- the flip)",
              abs(old_base) < 10000, "old base %d" % old_base)

        say("a NEW connection to a client whose base survived")
        S = fresh(field_ready_at=time.monotonic() - 60, **ready)
        feworld.note_client_clock(sample(epoch))
        check("clock already past 2**40: refuses, marks synced",
              push(_args()) is False and not clocks()
              and S.get("clock_synced") is True)

        say("a war countdown armed in the client's units")
        S = fresh(field_ready_at=time.monotonic() - 60, war_phase="prewar",
                  war_deadline=raw + 60000, war_deadline_kind="client", **ready)
        feworld.note_client_clock(sample(raw))
        check("deferred while armed", push(_args()) is False and not clocks())
        check("...and says so once", S.get("clock_defer_said") is True)
        S.pop("war_deadline")
        check("sent once nothing is armed", push(_args()) is True
              and len(clocks()) == 1)

        say("!clock in the gmcmd file")
        tmp = os.path.join(os.environ.get("TEMP", "."),
                           "fe_clock_t_%d.txt" % os.getpid())
        S = fresh(field_ready_at=time.monotonic(), war_deadline=1,
                  pres_mv_b=raw - 400, pres_mv_arr=time.monotonic(), **ready)
        feworld.note_client_clock(sample(raw))
        with open(tmp, "w") as fh:
            fh.write("!clock\n")
        try:
            feworld.gm_pump(None, None, "ecb", False,
                            _args(world_clock="off", gmcmd_file=tmp))
        finally:
            os.remove(tmp)
        check("!clock sends under --world-clock off, before the delay, "
              "under an armed deadline", len(clocks()) == 1)
        hi, lo = struct.unpack(">II", clocks()[0])
        base = (hi << 32) | lo
        mine = (S["pres_mv_a"] << 32) | S["pres_mv_b"]
        check("fepresence's anchor (pres_mv_a:b) moved by the same base",
              mine == raw - 400 + base, "%d vs %d" % (mine, raw - 400 + base))
        S["clock_force"] = True
        check("a second !clock refuses (once per connection)",
              push(_args(world_clock="off")) is False and len(clocks()) == 1)

        say("--war-clock sync alone still sends (the older spelling)")
        S = fresh(field_ready_at=time.monotonic() - 60, **ready)
        feworld.note_client_clock(sample(raw))
        check("war_clock=sync, world_clock=off: sent",
              push(_args(world_clock="off", war_clock="sync")) is True)
        check("--world-clock on is a spelling of session",
              push(_args(world_clock="on")) is False    # already synced
              and S.get("clock_synced") is True)

        say("fepresence.rebase_clock follows the RECEIVER's 64 bits")
        body = sample(0)
        body = body[:4] + struct.pack(">II", 0, 999) + body[12:]
        s = {"pres_mv_b": 5000, "pres_mv_arr": 100.0}
        a, b = struct.unpack_from(">II",
                                  fepresence.rebase_clock(body, 101.25, s), 4)
        check("unsynced receiver: A = 0, B = own B + 1250 (old formula)",
              (a, b) == (0, 6250), repr((a, b)))
        syn_sender = body[:4] + struct.pack(">II", 0x1A0, 999) + body[12:]
        a, b = struct.unpack_from(
            ">II", fepresence.rebase_clock(syn_sender, 101.25, s), 4)
        check("a SYNCED sender into an unsynced receiver: mapped, A = 0",
              (a, b) == (0, 6250), repr((a, b)))
        s2 = {"pres_mv_a": 0x1A0, "pres_mv_b": 0xFFFFFF00, "pres_mv_arr": 100.0}
        a, b = struct.unpack_from(">II",
                                  fepresence.rebase_clock(body, 101.25, s2), 4)
        check("a synced receiver: its own A, and B carries into it",
              (a << 32 | b) == ((0x1A0 << 32) | 0xFFFFFF00) + 1250,
              repr((hex(a), hex(b))))
    finally:
        feworld.send, feworld.inner_msg = saved

    say("\n%d/%d checks passed" % (sum(ok for _, ok in CHECKS), len(CHECKS)))


if __name__ == "__main__":
    main()
