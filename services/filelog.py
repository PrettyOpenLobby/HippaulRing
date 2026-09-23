"""Keep a service's console output in LOG_DIR/<name>.log as well.

WHY. A service that prints to stdout and nothing else keeps its history only
in the container's docker log, and recreating the container (an image update,
a compose change) takes that history with it -- usually right when a player's
problem report needs it.

A file under LOG_DIR survives the recreate, and a server-side report tool that
cuts every *.log in LOG_DIR by time can pick it up.

Every line is STAMPED as it starts ("2026-09-23T21:04:05Z "), because a
time-window cut needs a stamp to bisect on, and a file of unstamped lines can
only be taken whole. Lines already stamped by their writer are left alone.

Size-capped: at POL_FILELOG_MAX bytes (default 64 MB) the file rotates once to
<name>.1.log, so two files at most per service.

Usage, first thing in main():  filelog.tee("feworld")
"""
import os
import re
import sys
import threading
import time

LOG_DIR = os.environ.get("POL_LOG_DIR", "/logs")
MAX_BYTES = int(os.environ.get("POL_FILELOG_MAX", str(64 * 1024 * 1024)))

_STAMPED = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


class _Sink(object):
    """One appending file shared by stdout and stderr, stamping line starts."""

    def __init__(self, path, max_bytes):
        self.path = path
        self.max_bytes = max_bytes
        self.lock = threading.Lock()
        self.at_line_start = True
        self.fh = open(path, "a", encoding="utf-8", errors="replace")

    def _rotate(self):
        try:
            self.fh.close()
            base, ext = os.path.splitext(self.path)
            os.replace(self.path, base + ".1" + ext)
        except OSError:
            pass
        self.fh = open(self.path, "a", encoding="utf-8", errors="replace")

    def write(self, text):
        if not text:
            return
        with self.lock:
            try:
                out = []
                for piece in text.splitlines(True):
                    if self.at_line_start and not _STAMPED.match(piece):
                        out.append(time.strftime("%Y-%m-%dT%H:%M:%SZ ", time.gmtime()))
                    out.append(piece)
                    self.at_line_start = piece.endswith("\n")
                self.fh.write("".join(out))
                self.fh.flush()
                if self.fh.tell() > self.max_bytes:
                    self._rotate()
            except (OSError, ValueError):
                # The console copy is the one that must never fail; a full
                # disk or a closed file only costs the file copy.
                pass


class _Tee(object):
    def __init__(self, inner, sink):
        self._inner = inner
        self._sink = sink

    def write(self, text):
        r = self._inner.write(text)
        self._sink.write(text)
        return r

    def flush(self):
        return self._inner.flush()

    def __getattr__(self, k):
        return getattr(self._inner, k)


def tee(name, log_dir=None, max_bytes=None):
    """Copy sys.stdout and sys.stderr into <log_dir>/<name>.log from now on.

    Returns the path, or None when the directory is absent or not writable
    (a developer running the service by hand without /logs), in which case
    nothing changes and the service logs to the console exactly as before.
    """
    d = log_dir or LOG_DIR
    if not os.path.isdir(d):
        return None
    path = os.path.join(d, "%s.log" % name)
    try:
        sink = _Sink(path, max_bytes or MAX_BYTES)
    except OSError:
        return None
    sys.stdout = _Tee(sys.stdout, sink)
    sys.stderr = _Tee(sys.stderr, sink)
    return path


def _selftest():
    import tempfile
    d = tempfile.mkdtemp()
    real_out, real_err = sys.stdout, sys.stderr
    try:
        p = tee("t", log_dir=d, max_bytes=400)
        print("hello", flush=True)
        sys.stdout.write("part one ")
        sys.stdout.write("and two\n")
        print("2026-01-02T03:04:05Z already stamped", flush=True)
        sys.stderr.write("an error\n")
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    lines = open(p, encoding="utf-8").read().splitlines()
    ok = True
    ok &= bool(re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z hello$", lines[0]))
    ok &= bool(re.match(r"^\S+Z part one and two$", lines[1]))      # one stamp per line
    ok &= lines[2] == "2026-01-02T03:04:05Z already stamped"         # not double-stamped
    ok &= lines[3].endswith("Z an error")                            # stderr too
    # rotation: push past 400 bytes and the old content moves to t.1.log
    sink = sys.stdout
    try:
        tee("r", log_dir=d, max_bytes=100)
        for i in range(10):
            print("line %d padding padding padding" % i, flush=True)
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    ok &= os.path.exists(os.path.join(d, "r.1.log"))
    ok &= os.path.getsize(os.path.join(d, "r.log")) <= 200
    # no directory -> no tee, streams untouched
    ok &= tee("x", log_dir=os.path.join(d, "absent")) is None and sys.stdout is real_out
    del sink
    print("filelog selftest: %s" % ("ok" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
