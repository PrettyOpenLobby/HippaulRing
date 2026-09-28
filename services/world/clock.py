"""The client's clock read off its telemetry, and the clock pushes a war timer needs."""
import struct
import time
from . import sess, wire

# ---------------------------------------------------------------------------
# THE CLIENT'S CLOCK, READ OFF ITS OWN TELEMETRY -- and the war cycle on top.
#
# 2026-08-27 (war-clock). Every 28-byte 0x2023 the client sends begins
#
#     [u32 0][u32 HI][u32 LO]
#
# and that pair is the client's 64-bit MILLISECOND clock: the builder at
# 0x0515dd85 writes the leading 0, calls 0x4ffef10 (edx:eax = now) and pushes
# eax then edx, so the u32 written FIRST is edx = HIGH (0x0515dda5..0x0515ddb3;
# the second builder 0x0503ea40 does the same). It is the same clock 0x5111010
# compares the war deadline against, so the server reads `now` in the
# client's own units every ~400 ms and no longer has to ASSUME when the
# field-load timer was reset. An all-ones pair is the -1:-1 that 0x4ffef10
# returns while the valid byte [0x5336ec8] is 0 -- not a time.
#
# 0x1148 is the message that sets the clock's BASE. Its arm (0x05055edb) reads
# two u32s -- HIGH first (the read that runs first fills the slot pushed last,
# [esp+0x3c], which goes out in edx = arg2 = [0x5336ec4]) -- and hands them to
# 0x4ffef70, which stores [0x5336ec0]=LOW, [0x5336ec4]=HIGH and nothing else:
# it does not touch the valid byte and does not reset the timer. The only
# other writers of that base are two steps of the RESUME (reconnect) sequence
# at 0x04ffc362/0x04ffc501, which zero it -- so a base set after 0x1000 holds
# for the whole field session. --war-clock sync sends it once with
# base = server epoch ms - client elapsed, after which the client's clock IS
# server wall time and the NEXT telemetry sample shows the jump. That jump is
# the acceptance proof; nothing on screen is.
#
# NO GATE in the 0x1148 arm beyond the main dispatcher's own (the same ladder
# 0x1000 and 0x1012 live in). "The bytes decode" is measured; "the base is
# harmless to every other consumer" is NOT -- 0x0509d306 (a /1000 display),
# 0x04fec860 (clock + n*0x24c3 stamps), 0x0515bce9 and the two telemetry
# builders also read it. That is why 'sync' is a knob and 'telemetry' is the
# default: the latter sends nothing new and only computes deadlines off a
# value the client itself reported.
# ---------------------------------------------------------------------------

def note_client_clock(f):
    """Record the client's clock from a 28-byte 0x2023 body. Returns it or None."""
    if len(f) < 12:
        return None
    hi, lo = struct.unpack_from(">II", f, 4)
    if hi == 0xFFFFFFFF and lo == 0xFFFFFFFF:
        return None
    now = (hi << 32) | lo
    prev = sess._SESSION.get("cclock")
    t = time.monotonic()
    sess._SESSION["cclock"] = (now, t)
    sess._SESSION["cclock_n"] = sess._SESSION.get("cclock_n", 0) + 1
    drift = None
    if prev:
        drift = (now - prev[0]) - (t - prev[1]) * 1000.0
    if sess._SESSION["cclock_n"] == 1 or (drift is not None and abs(drift) > 2000):
        print("[feworld]    client clock (0x2023 bytes 4..12, HIGH first) = "
              "%d ms  hi=0x%08X lo=0x%08X%s"
              % (now, hi, lo,
                 "" if drift is None else
                 "  -- JUMPED %+.0f ms against our own elapsed (a 0x1148 base "
                 "landing looks exactly like this)" % drift), flush=True)
    if sess._SESSION.get("clock_connect_sent") and not sess._SESSION.get("clock_verified"):
        # the first sample after a LOGIN-time 0x1148 is its only receipt
        sess._SESSION["clock_verified"] = True
        if now < _CLOCK_ALREADY_BASED:
            sess._SESSION["clock_synced"] = False
            print("[feworld]    WARNING: the login-time 0x1148 did NOT hold: the first "
                  "sample reads a raw %d ms (dropped, or a RESUME step zeroed "
                  "the base). The 'session' path will send it once the field is "
                  "safe (--world-clock-after-ms after field ready)." % now,
                  flush=True)
        else:
            print("[feworld]    login-time 0x1148 held: the client's clock reads "
                  "%d ms, %+d ms from server epoch (plus any --clock-epoch-ms)"
                  % (now, now - int(time.time() * 1000)), flush=True)
    return now


def client_now_ms():
    """Best estimate of the client's clock right now, or None before the
    first 28-byte 0x2023 has been seen."""
    s = sess._SESSION.get("cclock")
    if not s:
        return None
    return s[0] + int((time.monotonic() - s[1]) * 1000)


def war_abs_deadline(args, ms_ahead):
    """An absolute deadline `ms_ahead` from now, in the client's clock.

    Returns (deadline, kind): kind 'client' when it was computed from a
    telemetry sample, 'raw' when no sample exists yet (or --war-clock off), in
    which case the number is the old meaning -- N ms after the client's
    field-load timer reset, i.e. base 0 -- and expiry is judged by our own
    elapsed time from the moment it was sent."""
    if getattr(args, "war_clock", "telemetry") != "off":
        now = client_now_ms()
        if now is not None:
            return now + ms_ahead, "client"
    return ms_ahead, "raw"


def pack_deadline(dl):
    """The wire form of a 64-bit deadline: HIGH dword first, then LOW -- the
    shape all four installing arms share (0x1015 0x0511850f, 0x1016
    0x051180fb, 0x1018 0x0511823d, 0x1019 0x0511843b)."""
    return struct.pack(">II", (dl >> 32) & 0xFFFFFFFF, dl & 0xFFFFFFFF)


def war_arm(phase, dl, kind):
    sess._SESSION["war_phase"] = phase
    sess._SESSION["war_deadline"] = dl
    sess._SESSION["war_deadline_kind"] = kind
    sess._SESSION["war_deadline_t"] = time.monotonic()


def war_deadline_due():
    """Has the armed deadline passed? `!war` forces it."""
    dl = sess._SESSION.get("war_deadline")
    if dl is None:
        return False
    if sess._SESSION.get("war_force"):
        return True
    if sess._SESSION.get("war_deadline_kind") == "client":
        now = client_now_ms()
        return now is not None and now >= dl
    t0 = sess._SESSION.get("war_deadline_t")
    return bool(t0) and (time.monotonic() - t0) * 1000.0 >= dl


def clock_sync_push(conn, outbound, mode, be, args):
    """0x1148 once: set the client's clock base.

    KEY: AND THAT CLOCK IS THE IN-GAME WORLD TIME (2026-09-12, live: "the
    ingame world time isn't synced at all"). Read off the getter 0x04FFEF10:

        cl = [0x5336ec8]            ; is the base SET?
        test cl,cl ; jne ...
        return -1 : -1              ; NOT SET -> "no time"
        call 0x522f470              ; the client's own elapsed ms
        eax = [0x5336ec0]           ; the 0x1148 base, low
        edx = [0x5336ec4]           ;                high
        eax += elapsed ; adc edx    ; base + elapsed

    and the world-time object at [0x5336378] divides that by 1000 for seconds
    (0x0509D321 pushes 0x3E8) and by 0x3436B300 for the in-game YEAR, with
    day/hour/minute accessors beside it -- the client's own log line is
    `fetime=%11lld(sec) %d(y)/%3d(d) : %2d:%2d` (0x052DF664). Every accessor
    treats -1 as "no time" (`(eax & edx) == -1`).

    So the world clock was never WRONG, it was UNSET: the flag at 0x5336ec8 is
    written only by the 0x1148 arm, and prod pinned --war-clock telemetry,
    under which this function returned immediately. --world-clock drives it
    now, independent of the war-deadline telemetry knob that used to own it.

    WARNING: THE BASE IS EPOCH MS, WHICH MAKES THE IN-GAME YEAR ABSURD -- about
    2043, since epoch_ms / 0x3436B300 is roughly that. What it DOES give is the
    one property that matters here: every client shares the same base, so every
    client agrees on the world time. FE's own calendar epoch is not written
    down anywhere we have, so a nicer absolute date has nothing to come from;
    --clock-epoch-ms offsets the base if a source ever turns up.

    WARNING:KEY: ONCE PER CONNECTION, NOT PER ENTRY (2026-09-12, evening). The first
    cut sent this at every field entry, and every entry parked the client in
    mode 0xb substate 3 (the fade) until an input -- weather, empty bars and
    unposed armour, fixed by switching it off (3d943ded). Three things read
    since, off the dump:

      * THE FADE IS NOT A CONSUMER. Its elapsed (+0x4c) grows by
        min([[0x52ffad0]+0x11c], 100) * K per frame (0x0503949b), and +0x11c
        is written in the main loop (0x04fb0788) as timeGetTime() - last
        frame -- WINMM!timeGetTime, IAT 0x525f3dc -- with anything <= 0
        stored as 0. Its start/duration are written only from arguments
        (0x050395c3/cd). None of this getter's nine callers is fade code. So
        whatever parked substate 3 came through ANOTHER gate (one of the nine
        callers of the fade flag, 0x04ff9cf4..0x04ffc2b5), and it is NOT
        traced. That is why the default stays off and `!clock` exists.
      * THE PER-ENTRY RESEND FLIPPED THE CLOCK. The base lasts the connection
        (only the RESUME steps zero it), and each entry cleared `cclock` and
        `clock_synced`, so the second entry computed base = epoch - now from a
        clock that ALREADY read epoch: ~0, putting the world clock back ~56
        years. Entries alternated forward and back. _CLOCK_ALREADY_BASED
        refuses that arithmetic outright, even across a reconnect that kept
        the client's old base.
      * A DEADLINE UNDER IT GOES STALE. Every war countdown we arm is in the
        client's units; moving the base under an armed one puts it ~56 years
        in the past. So 'session' waits for a field with nothing armed.
        Monster moves are safe (only END - START is read, _chase_clock_ms);
        fepresence's map follows the receiver's full 64 bits (rebase_clock),
        and its anchor is moved here with ours.

    Returns True when it sent.
    """
    force = bool(sess._SESSION.pop("clock_force", False))
    wc = str(getattr(args, "world_clock", "off") or "off")
    # 'connect' is here as the FALLBACK: clock_connect_push sends at login,
    # and only if the first sample shows that base did not hold does
    # note_client_clock clear clock_synced and let this path resend it safely
    enabled = (wc in ("session", "on", "connect")
               or getattr(args, "war_clock", "telemetry") == "sync")
    if not (enabled or force):
        return False

    def refused(why):
        if force:
            print("[feworld]    !clock -- NOT sent: %s" % why, flush=True)
        return False

    if sess._SESSION.get("clock_synced"):
        return refused("already sent on this connection; the base lasts until "
                       "the client reconnects")
    if not sess._SESSION.get("in_field"):
        return refused("not in a field")
    now = client_now_ms()
    if now is None:
        return refused("no 28-byte 0x2023 clock sample yet -- take one step")
    if now >= _CLOCK_ALREADY_BASED:
        sess._SESSION["clock_synced"] = True
        print("[feworld]    0x1148 NOT sent: the client's clock already reads "
              "%d ms, which only a base can do -- a 0x1148 from an earlier "
              "connection survived (only the RESUME zeroes it). Re-sending "
              "would compute base ~0 and put the world clock back ~56 years."
              % now, flush=True)
        return False
    if not force:
        ready_at = sess._SESSION.get("field_ready_at")
        if not sess._SESSION.get("field_ready") or ready_at is None:
            return False
        after = float(getattr(args, "world_clock_after_ms", 10000) or 0)
        if (time.monotonic() - ready_at) * 1000.0 < after:
            return False
        if (sess._SESSION.get("war_deadline") is not None
                or sess._SESSION.get("war_notify_deferred")):
            if not sess._SESSION.get("clock_defer_said"):
                sess._SESSION["clock_defer_said"] = True
                print("[feworld]    0x1148 deferred: a %s war countdown is "
                      "armed in the client's units, and a base landing under "
                      "it would put it ~56 years in the past. Waiting for a "
                      "field with none (a capital, a peace field)."
                      % (sess._SESSION.get("war_phase") or "held"), flush=True)
            return False
    base = int(time.time() * 1000) - now + int(getattr(args, "clock_epoch_ms", 0) or 0)
    if base < 0:
        base = 0
    sess._SESSION["clock_synced"] = True
    wire.send(conn, outbound, wire.inner_msg(0x1148, pack_deadline(base)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    # Move our own estimates with it; the next 0x2023 either confirms the jump
    # (accepted) or does not (dropped), and note_client_clock prints which.
    s = sess._SESSION["cclock"]
    sess._SESSION["cclock"] = (s[0] + base, s[1])
    if sess._SESSION.get("pres_mv_b") is not None:
        # fepresence's anchor for mapping peers into THIS client's clock --
        # a standing player sends no new one until they move
        mine = ((int(sess._SESSION.get("pres_mv_a", 0) or 0) << 32)
                | int(sess._SESSION["pres_mv_b"])) + base
        sess._SESSION["pres_mv_a"] = (mine >> 32) & 0xFFFFFFFF
        sess._SESSION["pres_mv_b"] = mine & 0xFFFFFFFF
    print("[feworld] -> 0x30 inner 0x1148 clock base = %d ms (hi=0x%08X "
          "lo=0x%08X; arm 0x05055edb -> 0x4ffef70 -> [0x5336ec4]:[0x5336ec0])%s. "
          "The client's clock should now read ~%d = server epoch ms; the next "
          "0x2023 sample must show a jump of about +%d ms or the message was "
          "not accepted." % (base, (base >> 32) & 0xFFFFFFFF, base & 0xFFFFFFFF,
                             " -- FORCED by !clock" if force else "",
                             now + base, base), flush=True)
    return True


def clock_connect_push(conn, outbound, mode, be, args):
    """0x1148 AT LOGIN -- --world-clock connect, the default since 2026-09-12.

    Why at login: the unset clock (-1) draws the sky as NIGHT and the HUD
    clock as `--時--分`, and 'session' waits until 10 s after the first field
    is ready -- live, the player arrived at night and the sky jumped to day
    ~12 s later. The map screen shows the clock too. The one thing a base
    must never do is move DURING a field entry (the substate-3 park, measured
    2026-09-12), and at login no field is loading yet.

    No sample is needed for the base: the client's clock counts from ITS
    world CONNECT (measured: 20890 ms at 21:35:30.369 for a CONNECT at
    21:35:09.477), and this goes out with 0x302B ~30 ms after it, so
    base = server epoch ms is right to ~0.02 of an in-game minute.

    Our own estimate is seeded with it, so a map-screen war deadline armed
    before any 0x2023 is computed in the new units -- the old raw shape (N ms
    from zero) would sit ~56 years in the past under this base. The first
    28-byte sample then VERIFIES the base (note_client_clock); if it did not
    hold, the 'session' path sends it once the field is safe.
    """
    if str(getattr(args, "world_clock", "off") or "off") != "connect":
        return False
    if sess._SESSION.get("clock_synced"):
        return False
    base = int(time.time() * 1000) + int(getattr(args, "clock_epoch_ms", 0) or 0)
    sess._SESSION["clock_synced"] = True
    sess._SESSION["clock_connect_sent"] = True
    wire.send(conn, outbound, wire.inner_msg(0x1148, pack_deadline(base)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    sess._SESSION["cclock"] = (base, time.monotonic())
    print("[feworld] -> 0x30 inner 0x1148 clock base = %d ms (hi=0x%08X "
          "lo=0x%08X) AT LOGIN (--world-clock connect): the client's clock "
          "counts from its world connect, so it now reads ~server epoch ms. "
          "The first 28-byte 0x2023 in the field verifies it."
          % (base, (base >> 32) & 0xFFFFFFFF, base & 0xFFFFFFFF), flush=True)
    return True


# What a field ENTRY forgets about the clock (the 0x2000 handler). The war's
# deadlines are per field; `cclock` is re-learned from the next sample, which
# is in the same units if a base has landed. WARNING: `clock_synced` must never be in
# here: the base outlives the field, and re-arming the send is how it flipped.
FIELD_ENTRY_CLOCK_CLEAR = ("war_phase", "war_deadline", "war_deadline_kind",
                           "war_deadline_t", "war_force", "cclock", "cclock_n",
                           "field_ready_at", "clock_defer_said")

# A raw client clock is ms since CONNECT; 2**40 ms is ~34.8 years. A sample at
# or past it can only be carrying a 0x1148 base already.
_CLOCK_ALREADY_BASED = 1 << 40
