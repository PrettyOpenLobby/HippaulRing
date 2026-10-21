"""A per-address share of the handler threads, for the three FE listeners.

fellb, felobby and feworld each accept on a plain socket and start one thread
per connection with no ceiling at all, so a single peer could hold every
thread and every descriptor the process has. This caps what one address may
hold at once; POL_MAX_CONNS_PER_IP sets the share (default 32, the same knob
the OpenLobby core's serving.py reads) and 0 disables it.

    if not peercap.acquire(addr[0]):
        conn.close()
    threading.Thread(target=peercap.run, args=(handler, conn, addr, ...))

`run` calls the handler and gives the slot back when it returns, however it
returns.
"""
import os
import threading

MAX_PER_IP = int(os.environ.get("POL_MAX_CONNS_PER_IP", "32"))
_HELD = {}
_LOCK = threading.Lock()


def acquire(ip):
    """Take one of `ip`'s slots; False when it already holds them all."""
    if MAX_PER_IP <= 0:
        return True
    with _LOCK:
        n = _HELD.get(ip, 0)
        if n >= MAX_PER_IP:
            return False
        _HELD[ip] = n + 1
    return True


def release(ip):
    if MAX_PER_IP <= 0:
        return
    with _LOCK:
        n = _HELD.get(ip, 0) - 1
        if n > 0:
            _HELD[ip] = n
        else:
            _HELD.pop(ip, None)


def held(ip):
    with _LOCK:
        return _HELD.get(ip, 0)


def run(handler, conn, addr, *rest):
    """Thread target: the handler, then the slot back."""
    try:
        handler(conn, addr, *rest)
    finally:
        release(addr[0])


def selftest():
    saved = MAX_PER_IP
    try:
        globals()["MAX_PER_IP"] = 2
        _HELD.clear()
        assert acquire("192.0.2.10") and acquire("192.0.2.10")
        assert not acquire("192.0.2.10"), "third slot must be refused"
        assert acquire("192.0.2.11"), "another address is unaffected"
        seen = []
        run(lambda c, a: seen.append(a), None, ("192.0.2.10", 1))
        assert seen == [("192.0.2.10", 1)] and held("192.0.2.10") == 1
        assert acquire("192.0.2.10"), "the slot came back"
        try:
            run(lambda c, a: 1 / 0, None, ("192.0.2.10", 2))
        except ZeroDivisionError:
            pass
        assert held("192.0.2.10") == 1, "a raising handler still releases"
        release("192.0.2.10")
        release("192.0.2.11")
        assert not _HELD, _HELD
        globals()["MAX_PER_IP"] = 0
        assert all(acquire("192.0.2.12") for _ in range(100)), "0 = no cap"
        assert not _HELD
    finally:
        globals()["MAX_PER_IP"] = saved
        _HELD.clear()
    print("peercap selftest ok")


if __name__ == "__main__":
    selftest()
