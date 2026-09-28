"""Per-connection state: the thread-local session dict (_SESSION) and the sequence slots."""
import threading

class _PerConnection(threading.local):
    """Per-connection state that used to be module globals.

    WARNING: THESE WERE PLAIN GLOBALS AND THE LISTENER WAS SERIAL, so "one connection
    at a time" made them accidentally correct. Making the listener threaded --
    so a second FE player is not left waiting out the first player's whole
    --read-window on a connected socket -- makes them a data race: two sessions
    would share one sequence counter, one in_field flag, and one `account`, and
    the last one to arrive would write its charid over the other's before
    _store_char reached the store.

    threading.local, rather than passing state down ~30 call sites, is chosen
    deliberately: one thread per connection means each session gets its own
    dict with no call-site churn, and it also retires the "stale _SESSION
    leftover answered group=5" hazard noted at the 0x2016 handler -- a fresh
    thread starts with a fresh dict, so nothing survives from a previous
    session at all.
    """

    def __init__(self):
        self.session = {}
        self.out_seq = 0
        self.last_in = 0


_TLS = _PerConnection()


class _SessionProxy(object):
    """`_SESSION` kept as a name, backed by the current thread's dict.

    Only the four operations the file actually uses are forwarded; anything
    else should be added here rather than reaching into `_TLS` at a call site,
    so the per-connection boundary stays in one place.
    """

    def _d(self):
        return _TLS.session

    def get(self, k, default=None):
        return self._d().get(k, default)

    def pop(self, k, default=None):
        return self._d().pop(k, default)

    def clear(self):
        self._d().clear()

    def setdefault(self, k, default=None):
        return self._d().setdefault(k, default)

    def __getitem__(self, k):
        return self._d()[k]

    def __setitem__(self, k, v):
        self._d()[k] = v

    def __contains__(self, k):
        return k in self._d()

    def __repr__(self):
        return repr(self._d())


_SESSION = _SessionProxy()


class _SeqSlot(object):
    """`_OUT_SEQ[0]` / `_LAST_IN[0]` kept as names, per connection."""

    def __init__(self, attr):
        self._attr = attr

    def __getitem__(self, i):
        return getattr(_TLS, self._attr)

    def __setitem__(self, i, v):
        setattr(_TLS, self._attr, v)


_OUT_SEQ = _SeqSlot("out_seq")
_LAST_IN = _SeqSlot("last_in")
