"""Running extensions: the Ctx handed to a handler, dispatch, relay posts between sessions, pumps."""
import queue
import threading
from . import chat, ext, sess, wire

class Ctx(object):
    """One session's send-side, bundled. See THE EXTENSION SEAM."""
    __slots__ = ("conn", "outbound", "mode", "be", "args")

    def __init__(self, conn, outbound, mode, be, args):
        self.conn, self.outbound, self.mode, self.be, self.args = (
            conn, outbound, mode, be, args)

    @property
    def session(self):
        return sess._SESSION

    def reply(self, mid, body=b"", unit_id=None, why=""):
        """Send one inner message on this session, header id resolved the
        way every arm here resolves it (unit_id_of), sequence per --seq-mode."""
        uid = wire.unit_id_of(self.args) if unit_id is None else unit_id
        wire.send(self.conn, self.outbound, wire.inner_msg(mid, body, uid),
             self.mode, self.be, self.args.seq_mode == "echo",
             getattr(self.args, "world_prefix", 4))
        print("[feworld] -> 0x30 inner 0x%04X%s (%d body bytes)"
              % (mid, " " + why if why else "", len(body)), flush=True)
        return mid


def ext_dispatch(ctx, real_id, inner):
    """Run the registered handler. A failing extension logs its traceback and
    the session CONTINUES -- a thread dying on one message looks exactly like
    the client hanging up, which is the most misleading failure this file has.

    Returns False ONLY when the handler itself returned False -- "not mine,
    let the builtin arm answer". WARNING: 2026-09-10: this return was IGNORED and
    the message was dropped on the floor. fecampaign shadows 0x2084 and
    0x2018 with override=True and returns False while --campaign is off (the
    prod default), so from the moment it loaded the war heartbeat got no
    0x1127 and the army choice got no 0x1020 -- the client sat on
    'waiting...' after PLAY_START, the exact hang 0x1020 was added to cure
    on 08-24. A raising handler counts as HANDLED (None), as before."""
    fn = ext.EXT_HANDLERS[real_id]
    try:
        return fn(ctx, inner)
    except Exception:                                  # noqa: BLE001
        import traceback
        print("[feworld] EXTENSION %s failed on 0x%04X -- session kept:"
              % (getattr(fn, "__module__", "?"), real_id), flush=True)
        traceback.print_exc()
        return None


def ext_sessions():
    """Every live world session: [{key, session, name}] (the session dicts are
    live thread-locals -- read them, never write them from another thread)."""
    with chat._CHAT_ROOM_LOCK:
        return [dict(key=k, session=e["session"], name=e["name"])
                for k, e in chat._CHAT_ROOM.items()]


def ext_post(kind, payload, to=None, include_me=False):
    """Queue `payload` for EXT_RELAY[kind] on OTHER sessions' threads.

    `to` is None (everyone) or fn(session_dict, name) -> bool. The receiving
    session's own read loop drains it in ext_pump() -- the same design as
    chat_relay: the socket, the cipher and _OUT_SEQ are only ever touched by
    their owning thread. Returns how many queues were written."""
    me = threading.get_ident()
    n = 0
    with chat._CHAT_ROOM_LOCK:
        for key, ent in chat._CHAT_ROOM.items():
            if key == me and not include_me:
                continue
            if to is not None and not to(ent["session"], ent["name"]):
                continue
            ent["ext"].put((kind, payload))
            chat._wake(ent)
            n += 1
    return n


def ext_pump(ctx):
    """Drain this session's relay queue, then run every registered pump."""
    with chat._CHAT_ROOM_LOCK:
        me = chat._CHAT_ROOM.get(threading.get_ident())
    if me is not None:
        while True:
            try:
                kind, payload = me["ext"].get_nowait()
            except queue.Empty:
                break
            fn = ext.EXT_RELAY.get(kind)
            if fn is None:
                print("[feworld]    relay kind %r has no receiver -- dropped"
                      % (kind,), flush=True)
                continue
            try:
                fn(ctx, payload)
            except Exception:                          # noqa: BLE001
                import traceback
                print("[feworld] EXTENSION relay %r failed -- session kept:"
                      % (kind,), flush=True)
                traceback.print_exc()
    for fn in ext.EXT_PUMPS:
        try:
            fn(ctx)
        except Exception:                              # noqa: BLE001
            import traceback
            print("[feworld] EXTENSION pump %r failed -- session kept:"
                  % (getattr(fn, "__module__", "?"),), flush=True)
            traceback.print_exc()
