#!/usr/bin/env python3
"""fe_ext_test.py -- the EXTENSION SEAM in feworld.py, on its own.

Run from services/:  python ../tools/fe_ext_test.py

WHY. On 2026-09-10 the eight unserved Fantasy Earth feature groups (party,
mail, trade, unit state, war entities, items, force admin, GM) were built as
eight modules in parallel, each registering into feworld through five small
tables (EXT_HANDLERS / EXT_GM / EXT_ARGS / EXT_PUMPS / EXT_RELAY). Every one
of those modules has its own test, and every one of those tests assumes the
seam itself behaves. This file pins the seam so a failure in it shows up here,
named, rather than as eight unrelated red suites:

  * register_handler REFUSES an id feworld.py already answers (a table row or
    a hard-coded arm) unless override=True -- two owners of one id is the
    silent kind of bug, where the second handler never runs.
  * the ext dispatch runs FIRST in _serve_loop's chain and sees the WHOLE
    inner (id included), and ctx.reply() lands on the wire with the resolved
    unit id and the registered --seq-mode.
  * a handler that RAISES logs and the session continues -- a thread dying
    on one message looks exactly like the client hanging up.
  * ext_post / ext_pump carry a payload from one session's thread to another
    session's queue and run the relay on the RECEIVER's ctx -- with `to`
    filtering by session dict / name, and `include_me` off by default.
  * a `!verb` line in --gmcmd-file reaches EXT_GM before gm_command, and a
    verb that returns False falls through to the client verbatim.
  * load_extensions() skips a module that is NOT PRESENT and raises on one
    that is present but broken -- "feature silently missing" is the failure
    this distinction exists to prevent.
"""
import os
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

CHECKS = []
OUT = sys.__stdout__


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-58s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label + (": " + detail if detail else ""))


def _args(**kw):
    a = dict(unit_id="0", seq_mode="count", world_prefix=4, read_window=1.0,
             gmcmd=None, gmcmd_file=None, probe_on_auth=False,
             ui_auto="ok", ui_auto_err=0, move_request="off",
             chat_relay="off", chat_echo="off", chat_line=None,
             validate_finish=0, war_start="off", war_clock="off",
             war_deadline_ms=0)
    a.update(kw)
    return types.SimpleNamespace(**a)


class _Conn:
    def settimeout(self, t):
        pass

    def getpeername(self):
        return ("127.0.0.1", 1)


def _drive(a, frames, outbox):
    """Run the given inner frames through the REAL _serve_loop with fenet's
    crypto stubbed and send_world_frame captured as (mid, unit, payload)."""
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


def _reset_seam():
    feworld.EXT_HANDLERS.clear()
    del feworld.EXT_GM[:]
    del feworld.EXT_ARGS[:]
    del feworld.EXT_PUMPS[:]
    feworld.EXT_RELAY.clear()


def part_registry():
    _reset_seam()
    say("registry")
    ok = False
    try:
        feworld.register_handler(0x114D, lambda c, i: None)
    except ValueError:
        ok = True
    check("a UI_HEADER_ONLY_OK id (0x114D STACK) is refused", ok)
    ok = False
    try:
        feworld.register_handler(0xA011, lambda c, i: None)
    except ValueError:
        ok = True
    check("a hard-coded arm id (0xA011 HIT) is refused", ok)
    feworld.register_handler(0x114D, lambda c, i: None, override=True)
    check("override=True takes the id on purpose",
          0x114D in feworld.EXT_HANDLERS)
    ok = False
    try:
        feworld.register_handler(0x114D, lambda c, i: 1, override=True)
    except ValueError:
        ok = True
    check("a second DIFFERENT owner of the same id is refused", ok)
    f = feworld.EXT_HANDLERS[0x114D]
    feworld.register_handler(0x114D, f, override=True)
    check("re-registering the SAME function is idempotent", True)
    feworld.register_relay("t.kind", lambda c, p: None)
    ok = False
    try:
        feworld.register_relay("t.kind", lambda c, p: 1)
    except ValueError:
        ok = True
    check("a relay kind claimed twice is refused", ok)
    _reset_seam()


def part_dispatch():
    _reset_seam()
    say("dispatch")
    seen = []

    def on_1143(ctx, inner):
        seen.append(inner)
        ctx.reply(0x1129, b"hello\0\0", why="probe")

    def boom(ctx, inner):
        raise RuntimeError("deliberate")

    feworld.register_handler(0x1143, on_1143)
    feworld.register_handler(0x1144, boom)
    outbox = []
    a = _args()
    frames = [struct.pack(">HI", 0x1143, 7),
              struct.pack(">H", 0x1144),
              struct.pack(">HI", 0x1143, 8)]
    _drive(a, frames, outbox)
    check("the handler sees the WHOLE inner, id included",
          seen and seen[0] == struct.pack(">HI", 0x1143, 7), repr(seen))
    check("ctx.reply lands on the wire as [u32 unit][u16 id][body]",
          outbox and outbox[0][0] == 0x1129 and outbox[0][2] == b"hello\0\0",
          repr(outbox))
    check("the header unit id is unit_id_of(args) (0 here)",
          outbox and outbox[0][1] == 0)
    check("a RAISING handler keeps the session: the next message still ran",
          len(seen) == 2 and len(outbox) == 2, "seen=%d out=%d"
          % (len(seen), len(outbox)))
    _reset_seam()

    # WARNING: 2026-09-10: a handler that returns False has DECLINED the message and
    # the builtin arm must answer it. fecampaign shadows 0x2018 and returns
    # False with --campaign off; the return was ignored and the army choice
    # after PLAY_START got no 0x1020 -- 'waiting...' on screen.
    declined = []

    def decline(ctx, inner):
        declined.append(inner)
        return False

    feworld.register_handler(0x2018, decline, override=True)
    outbox = []
    _drive(_args(decide_country="ok"),
           [struct.pack(">HI", 0x2018, 0)], outbox)
    check("a handler returning False falls through to the builtin arm "
          "(0x2018 -> 0x1020)",
          len(declined) == 1 and [m for m, _, _ in outbox] == [0x1020],
          "declined=%d out=%r" % (len(declined), outbox))
    _reset_seam()


def part_relay():
    _reset_seam()
    say("relay")
    got = {}

    def on_relay(ctx, payload):
        got.setdefault(threading.get_ident(), []).append(payload)
        ctx.reply(0x1129, payload.encode("cp932") + b"\0", why="relayed")

    feworld.register_relay("t.ping", on_relay)
    # Two "sessions": this thread and one helper thread, both joined to the
    # room the way serve() joins it. The helper posts; we pump.
    me = feworld._chat_room_join()
    feworld._SESSION.clear()
    feworld._SESSION["charid"] = 1
    feworld._chat_room_name("ALICE")
    counts = {}
    ready = threading.Event()
    go = threading.Event()

    def other():
        feworld._chat_room_join()
        feworld._SESSION["charid"] = 2
        feworld._chat_room_name("BOB")
        ready.set()
        go.wait(5)
        counts["all"] = feworld.ext_post("t.ping", "to-everyone")
        counts["alice"] = feworld.ext_post(
            "t.ping", "to-alice", to=lambda s, n: n == "ALICE")
        counts["carol"] = feworld.ext_post(
            "t.ping", "to-carol", to=lambda s, n: n == "CAROL")
        counts["self"] = feworld.ext_post(
            "t.ping", "to-self", to=lambda s, n: s.get("charid") == 2)
        counts["self+me"] = feworld.ext_post(
            "t.ping", "to-self", to=lambda s, n: s.get("charid") == 2,
            include_me=True)
        feworld._chat_room_leave()

    t = threading.Thread(target=other, daemon=True)
    t.start()
    ready.wait(5)
    go.set()
    t.join(5)
    check("post to everyone reached exactly one OTHER session",
          counts.get("all") == 1, repr(counts))
    check("`to` by name filters", counts.get("alice") == 1
          and counts.get("carol") == 0, repr(counts))
    check("the poster is excluded unless include_me", counts.get("self") == 0
          and counts.get("self+me") == 1, repr(counts))
    outbox = []
    a = _args()
    ctx = feworld.Ctx(_Conn(), None, "ecb", False, a)
    saved = (feworld.send_world_frame, fenet.bf_encrypt, fenet.traffic_wrap)
    feworld.send_world_frame = lambda c, mid, body, p: outbox.append(
        (struct.unpack_from(">H", body, 4)[0], body[6:]))
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data
    try:
        feworld.ext_pump(ctx)
    finally:
        feworld.send_world_frame, fenet.bf_encrypt, fenet.traffic_wrap = saved
    mine = got.get(threading.get_ident(), [])
    check("ext_pump ran the relay on the RECEIVER's thread, in order",
          mine == ["to-everyone", "to-alice"], repr(mine))
    check("...and its ctx.reply went out on the receiver's connection",
          [m for m, _ in outbox] == [0x1129, 0x1129], repr(outbox))
    # an unknown kind is dropped with a log line, not an exception
    with feworld._CHAT_ROOM_LOCK:
        feworld._CHAT_ROOM[me]["ext"].put(("no.such", 1))
    feworld.ext_pump(ctx)
    check("a relay kind with no receiver is dropped, session kept", True)
    feworld._chat_room_leave()
    _reset_seam()


def part_gm():
    _reset_seam()
    say("gm verbs")
    import tempfile
    taken, fell = [], []

    def verb(ctx, line):
        if line.startswith("!ping"):
            taken.append(line)
            return True
        return False

    feworld.register_gm(verb)
    saved = feworld.gm_command
    feworld.gm_command = lambda c, o, m, b, a, line: fell.append(line)
    fd, path = tempfile.mkstemp(suffix=".gm")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("!ping 1\n!crystalx 5\n/say hi\n")
        a = _args(gmcmd_file=path)
        feworld.gm_pump(_Conn(), None, "ecb", False, a)
    finally:
        feworld.gm_command = saved
        os.unlink(path)
    check("a registered !verb takes its line before gm_command",
          taken == ["!ping 1"], repr(taken))
    check("an unclaimed !line and a /line still fall through verbatim",
          fell == ["!crystalx 5", "/say hi"], repr(fell))
    _reset_seam()


def part_load():
    _reset_seam()
    say("load_extensions")
    import tempfile
    d = tempfile.mkdtemp()
    sys.path.insert(0, d)
    try:
        with open(os.path.join(d, "fe_zz_ok.py"), "w") as fh:
            fh.write("def register(fw):\n    fw.register_gm(lambda c, l: False)\n")
        with open(os.path.join(d, "fe_zz_bad.py"), "w") as fh:
            fh.write("import no_such_module_anywhere\n")
        done = feworld.load_extensions(["fe_zz_absent", "fe_zz_ok"])
        check("an ABSENT module is skipped, a present one registers",
              done == ["fe_zz_ok"] and len(feworld.EXT_GM) == 1, repr(done))
        again = feworld.load_extensions(["fe_zz_ok"])
        check("loading it twice registers nothing twice",
              again == [] and len(feworld.EXT_GM) == 1)
        raised = False
        try:
            feworld.load_extensions(["fe_zz_bad"])
        except ImportError:
            raised = True
        check("a PRESENT module whose own import fails RAISES", raised)
    finally:
        sys.path.remove(d)
        for n in ("fe_zz_ok", "fe_zz_bad"):
            sys.modules.pop(n, None)
            feworld._EXT_LOADED.discard(n)
    _reset_seam()


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    quiet = "-v" not in sys.argv

    class _Null:
        def write(self, *a):
            pass

        def flush(self):
            pass
    saved = (sys.stdout, sys.stderr)
    if quiet:
        sys.stdout = sys.stderr = _Null()
    try:
        part_registry()
        part_dispatch()
        part_relay()
        part_gm()
        part_load()
    except AssertionError as e:
        sys.stdout, sys.stderr = saved
        say("[fe_ext_test] FAIL: %s" % e)
        sys.exit(1)
    finally:
        sys.stdout, sys.stderr = saved
    say("[fe_ext_test] OK -- %d checks" % len(CHECKS))


if __name__ == "__main__":
    main()
