#!/usr/bin/env python3
"""fe_harness.py -- several FE world sessions through feworld's own objects.

Not a suite (fe_run_all runs fe_*_test.py only); the round-2 system suites
import it. What it gives them, and why each piece is the real one:

  * real_args()   the args feworld.main() itself finishes with: the parser,
                  every extension's knobs, RELEASE_DEFAULTS and main's own
                  post-processing (socket() is the first thing main builds
                  after that, so a shim there hands the args back). A suite
                  that hand-builds args tests a default nobody runs.
  * Peer          one world session: its own session dict, listed in the chat
                  room the way serve() lists a live one, so ext_post reaches
                  it. `send()` runs an inbound frame through the REAL
                  readloop._serve_loop (fenet's cipher stubbed, one frame,
                  every pump and the whole dispatch chain); `pump()` drains
                  its relays the way ext_pump does on its own thread; `run()`
                  calls any function with this session current.
  * the store     felobby's roster API (load_roster / update_roster /
                  store_accounts / save_roster) over a dict, so every reader
                  -- _self_char, _load_char_field, the wallet, the bank,
                  fecampaign's _update_char -- sees one consistent character.

While a peer acts, the chat-room entry it owns is re-keyed to the current
thread id (and the thread's own entry parked), so ext_post's "not me" and
_my_wake_socket see exactly what a real thread would.
"""
import contextlib
import copy
import io
import os
import queue
import socket as _real_socket
import struct
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)
# a standalone run must not fall back to /data (the drive root on Windows)
if not os.environ.get("POL_DATA_DIR", "").strip():
    _TMP = tempfile.mkdtemp(prefix="fe-harness-")
    for _k in ("POL_DATA_DIR", "POL_RESOURCE_DIR", "POL_LOG_DIR"):
        os.environ.setdefault(_k, os.path.join(_TMP, _k.lower()))
        os.makedirs(os.environ[_k], exist_ok=True)

import fenet      # noqa: E402
import felobby    # noqa: E402
import feworld    # noqa: E402

OUT = sys.__stdout__
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

CHECKS = []
QUIET = [True]


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-70s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + str(detail)) if (detail != "" and not cond)
                          else ""))
    if not cond:
        raise AssertionError(label + (": " + str(detail) if detail != "" else ""))


@contextlib.contextmanager
def quiet():
    """Swallow the server's own prints (they go to stdout) unless -v."""
    if not QUIET[0]:
        yield
        return
    with contextlib.redirect_stdout(io.StringIO()):
        yield


# ---------------------------------------------------------------------------
# the character store
# ---------------------------------------------------------------------------
STORE = {}          # account -> [character dict]
_STORE_LOCK = threading.Lock()


def _load_roster(path, account):
    with _STORE_LOCK:
        r = STORE.get(account)
        return None if r is None else [c for c in r]


def _update_roster(path, account, mutate):
    with _STORE_LOCK:
        r = STORE.setdefault(account, [])
        return mutate(r)


def _save_roster(path, account, roster):
    with _STORE_LOCK:
        STORE[account] = list(roster)
    return True


def _store_accounts(path):
    with _STORE_LOCK:
        return {a: len(r) for a, r in STORE.items()}


def install_store():
    felobby.load_roster = _load_roster
    felobby.update_roster = _update_roster
    felobby.save_roster = _save_roster
    felobby.store_accounts = _store_accounts
    felobby._default_store = lambda: "<harness>"


# ---------------------------------------------------------------------------
# the wire: capture every outbound inner message for the CURRENT peer
# ---------------------------------------------------------------------------
_CURRENT = [None]       # the outbox list of the peer acting right now


def _capture(conn, mid, body, prefix):
    unit, msg = struct.unpack_from(">IH", body, 0)
    box = _CURRENT[0]
    if box is not None:
        box.append((msg, unit, body[6:]))


def install_wire():
    fenet.bf_decrypt = lambda st, body, mode, be: body
    fenet.traffic_unwrap = lambda plain: (1, plain)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data
    feworld.send_world_frame = _capture


class _Conn(object):
    def settimeout(self, t):
        pass

    def getpeername(self):
        return ("127.0.0.1", 1)


# ---------------------------------------------------------------------------
# the real args
# ---------------------------------------------------------------------------
class _Got(Exception):
    pass


class _SockShim(object):
    def __getattr__(self, k):
        return getattr(_real_socket, k)

    def socket(self, *a, **k):
        raise _Got(sys._getframe(1).f_locals["args"])


_BASE_ARGS = []


def real_args(**over):
    """A COPY of the args feworld.main() runs with (no argv), with `over`
    applied on top (underscored names, as argparse stores them)."""
    if not _BASE_ARGS:
        saved = (feworld.socket, sys.argv)
        feworld.socket, sys.argv = _SockShim(), ["feworld.py"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                feworld.main()
        except _Got as g:
            _BASE_ARGS.append(g.args[0])
        finally:
            feworld.socket, sys.argv = saved
        if not _BASE_ARGS:
            raise AssertionError("feworld.main() never reached its listener")
    a = copy.copy(_BASE_ARGS[0])
    # the world-state stores default to the database (festate.InDatabase);
    # a suite gets files of its own instead, fresh per process, so it never
    # reads or writes a real stack's town, territory or campaign
    for k in STATE_FILES:
        if getattr(a, k, None) is None:
            setattr(a, k, os.path.join(_state_dir(), k[:-5] + ".json"))
    for k, v in over.items():
        setattr(a, k, v)
    return a


STATE_FILES = ("campaign_file", "force_file", "door_arrive_file",
               "mapcal_file", "spawn_file", "territory_file", "town_file")
_STATE = []


def _state_dir():
    if not _STATE:
        d = tempfile.mkdtemp(prefix="fe-harness-state-")
        import atexit
        import shutil
        atexit.register(shutil.rmtree, d, True)
        _STATE.append(d)
    return _STATE[0]


# ---------------------------------------------------------------------------
# a session
# ---------------------------------------------------------------------------
class Peer(object):
    def __init__(self, name, charid, args, nation=1, field=17, account=None,
                 char=None):
        self.name, self.charid, self.args = name, int(charid), args
        self.account = account or "acct-%s" % name.lower()
        self.key = ("peer", self.charid)
        self.out = []
        self.session = {"account": self.account, "charid": self.charid,
                        "in_field": True, "field": field, "room": -1,
                        "campaign_nation": nation, "field_ready": True}
        rec = {"charid": self.charid, "name": name, "force": nation,
               "class": 1, "gold": 1000}
        rec.update(char or {})
        with _STORE_LOCK:
            r = STORE.setdefault(self.account, [])
            r[:] = [c for c in r if c.get("charid") != self.charid] + [rec]
        try:
            wake = _real_socket.socketpair()
            for w in wake:
                w.setblocking(False)
        except (OSError, AttributeError):
            wake = None
        self.stand(0.0, 0.0, 0.0)
        with feworld._CHAT_ROOM_LOCK:
            feworld._CHAT_ROOM[self.key] = {
                "q": queue.Queue(), "session": self.session, "name": name,
                "ext": queue.Queue(), "wake": wake}

    # -- the store row ----------------------------------------------------
    @property
    def char(self):
        with _STORE_LOCK:
            for c in STORE.get(self.account, []):
                if c.get("charid") == self.charid:
                    return c
        return {}

    # -- acting as this session -------------------------------------------
    @contextlib.contextmanager
    def current(self):
        me = threading.get_ident()
        saved = getattr(feworld._TLS, "session", None)
        feworld._TLS.session = self.session
        park = ("parked", me)
        with feworld._CHAT_ROOM_LOCK:
            if me in feworld._CHAT_ROOM:
                feworld._CHAT_ROOM[park] = feworld._CHAT_ROOM.pop(me)
            feworld._CHAT_ROOM[me] = feworld._CHAT_ROOM.pop(self.key)
        prev = _CURRENT[0]
        _CURRENT[0] = self.out
        try:
            yield
        finally:
            _CURRENT[0] = prev
            feworld._TLS.session = saved
            with feworld._CHAT_ROOM_LOCK:
                feworld._CHAT_ROOM[self.key] = feworld._CHAT_ROOM.pop(me)
                if park in feworld._CHAT_ROOM:
                    feworld._CHAT_ROOM[me] = feworld._CHAT_ROOM.pop(park)

    def ctx(self):
        return feworld.Ctx(_Conn(), None, "ecb", False, self.args)

    def run(self, fn, *a):
        """fn(ctx, *a) as this session; returns what it sent."""
        n = len(self.out)
        with self.current(), quiet():
            fn(self.ctx(), *a)
        return self.out[n:]

    def call(self, fn, *a):
        """fn(*a) as this session (no ctx); returns (result, sent)."""
        n = len(self.out)
        with self.current(), quiet():
            r = fn(*a)
        return r, self.out[n:]

    def send(self, mid, body=b""):
        """One inbound inner message through the REAL _serve_loop."""
        inner = struct.pack(">H", mid) + bytes(body)
        inbox = [(0x30, inner)]
        saved = fenet.recv_frame
        fenet.recv_frame = lambda conn: inbox.pop(0) if inbox else None
        n = len(self.out)
        try:
            with self.current(), quiet():
                feworld._serve_loop(_Conn(), self.args, None, None, "ecb",
                                    False)
        finally:
            fenet.recv_frame = saved
        return self.out[n:]

    def pump(self):
        """Drain this session's relay queue (== ext_pump on its thread)."""
        n = len(self.out)
        with self.current(), quiet():
            feworld.extrun.ext_pump(self.ctx())
        return self.out[n:]

    def queued(self):
        return feworld._CHAT_ROOM[self.key]["ext"].qsize()

    def stand(self, x, y, z):
        """Where the server's range checks read this player: cpos and the
        latest 28-byte action-0 heartbeat (three i16 tenths at +18), stamped
        fresh the way fepresence's 0x2023 shadow stamps it."""
        import time
        self.session["cpos"] = (float(x), float(y), float(z))
        self.session["pres_mv"] = bytes(18) + struct.pack(
            ">hhh", int(x * 10), int(y * 10), int(z * 10)) + bytes(4)
        self.session["pres_mv_n"] = int(self.session.get("pres_mv_n", 0)) + 1
        self.session["pres_mv_t"] = time.monotonic()

    def tick(self):
        """The pumps with no inbound message (the read loop's idle tick)."""
        return self.pump()

    def leave(self):
        with feworld._CHAT_ROOM_LOCK:
            ent = feworld._CHAT_ROOM.pop(self.key, None)
        if ent and ent.get("wake"):
            for w in ent["wake"]:
                try:
                    w.close()
                except OSError:
                    pass


def deliver(*peers):
    """Pump every peer until no relay is left (a relay may post another)."""
    got = {p.charid: [] for p in peers}
    more = True
    while more:
        more = False
        for p in peers:
            if p.queued():
                got[p.charid] += p.pump()
                more = True
    return got


def see_each_other(*peers):
    """Run every peer's pump until each draws the others (fepresence's
    0x1006 under the peer's charid): what two clients in one field do."""
    for _ in range(3):
        for p in peers:
            p.stand(*p.session.get("cpos", (0.0, 0.0, 0.0)))
            p.tick()
    return {p.charid: sorted((p.session.get("pres_seen") or {}).keys())
            for p in peers}


def ids(box):
    return [m for m, _u, _b in box]


def bodies(box, mid):
    return [b for m, _u, b in box if m == mid]


def setup():
    install_store()
    install_wire()
    with quiet():
        feworld.load_extensions()


def finish(name):
    n = len(CHECKS)
    say("%s: %d/%d checks passed" % (name, sum(1 for _l, ok in CHECKS if ok), n))
    return 0 if all(ok for _l, ok in CHECKS) else 1


if "-v" in sys.argv:
    QUIET[0] = False
