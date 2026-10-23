#!/usr/bin/env python3
"""fedevtool.py -- the world-building panel (phase 1 of the dev-overlay plan).

Placing a spawn point, an NPC or a door means standing somewhere in the game
and then typing a line into a file on the server. That works, and it means
alt-tabbing out of a fullscreen client for every single placement -- twenty
times per capital, and FE's alt-tab is its own recorded problem.

This is the same commands, served as a page. It shows where you are standing,
WHAT THE AREA IS STILL MISSING (dat.pak ships a twenty-role roster per capital
and the monster/door positions per field -- see fegamedata.area_expected), and
turns each missing thing into a button.

WHY IT IS A SEPARATE MODULE. feworld.py is already 13k lines, and none of this
belongs in its read loop. The split is: feworld builds a state dict and hands
over a way to queue a command; this module owns the socket, the page, and
nothing else. It never imports feworld, so there is no cycle and it can be
exercised on its own.

WARNING: THIS PORT EXECUTES COMMANDS AGAINST A LIVE SESSION -- goto, npc, land. It
binds LOOPBACK by default and is off unless --devtool-port is given. Binding it
anywhere else requires a token, and it refuses to start otherwise rather than
listening wide open because somebody was in a hurry.

HOW A COMMAND ACTUALLY RUNS. It does not run here. It goes on a queue that
feworld's existing gm_pump drains on the next inbound frame -- the same path,
the same dispatcher, the same parsing as a line typed into --gmcmd-file. So
there is exactly one implementation of every command, the file console keeps
working, and anything this page can do can also be typed.
"""
import collections
import hmac
import inspect
import json
import os
import threading
import urllib.parse

_MAPDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fedata", "minimaps")

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except ImportError:                                   # pragma: no cover
    ThreadingHTTPServer = None

#: Commands waiting for gm_pump. A deque, not a list, because the reader
#: truncates it under a lock and a bounded one cannot grow without limit if
#: nobody is playing and the pump never runs.
_Q = collections.deque(maxlen=200)
#: The tail of feworld's own stdout, so the page can show what a command said.
#: This is the whole reason the panel is usable: the answer to "did that work?"
#: is a log line, and it used to be on a terminal somewhere else.
_LOG = collections.deque(maxlen=300)
_LOCK = threading.Lock()


def enqueue(line):
    """Queue one command line. Returns False if it is empty or a comment."""
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return False
    with _LOCK:
        _Q.append(line)
    return True


def drain():
    """Every queued command, oldest first, clearing the queue."""
    with _LOCK:
        out = list(_Q)
        _Q.clear()
    return out


def pending():
    """How many commands are waiting for the next inbound frame."""
    with _LOCK:
        return len(_Q)


def note(text):
    """Record one line of server output for the page's log tail."""
    for ln in str(text).rstrip("\n").split("\n"):
        if ln.strip():
            _LOG.append(ln)


def log_tail(n=60):
    return list(_LOG)[-int(n):]


class _Handler(BaseHTTPRequestHandler):
    server_version = "feworld-devtool/1"
    # Silence the default per-request stderr line: feworld's log is a working
    # surface and a state poll every second would bury it.
    def log_message(self, fmt, *a):
        pass

    def _authed(self):
        token = self.server.devtool_token
        if not token:
            return True
        q = urllib.parse.urlparse(self.path).query
        got = urllib.parse.parse_qs(q).get("t", [""])[0]
        hdr = self.headers.get("X-Devtool-Token") or ""
        want = token.encode("utf-8")
        return (hmac.compare_digest(got.encode("utf-8"), want)
                or hmac.compare_digest(hdr.encode("utf-8"), want))

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        # Everything on this port is gated, the page shell included. It
        # carries no token and no data, but it IS the authoring UI, and on
        # a LAN bind a scanner should meet a 403 rather than a tour of the
        # commands. The browser always arrives as /?t=<token> -- the page
        # reads it back out of location.search -- so this costs the real
        # user nothing, and a tokenless loopback run is unaffected because
        # _authed() returns True when no token is configured.
        if not self._authed():
            return self._send(403, {"error": "bad or missing token"})
        if path in ("/", "/index.html"):
            # another title's panel (fmodevtool) rides this same server and
            # gate with its own page; the FE page is the default
            return self._send(200, self.server.devtool_page or PAGE, "text/html")
        if path.startswith("/map/"):
            # the minimap art for one capital half. Named by AREA so the page
            # never has to know the map00_01-style stems.
            name = os.path.basename(path[5:])
            if not name.endswith(".png") or "/" in name or ".." in name:
                return self._send(404, {"error": "no such map"})
            f = os.path.join(_MAPDIR, name)
            if not os.path.isfile(f):
                return self._send(404, {"error": "no such map"})
            try:
                with open(f, "rb") as fh:
                    blob = fh.read()
            except OSError:
                return self._send(404, {"error": "unreadable"})
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(blob)))
            # the art never changes; let the browser keep it
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(blob)
            return
        if path == "/floor":
            area = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query
                                         ).get("area", [None])[0]
            if self.server.devtool_floor is None or area is None:
                return self._send(404, {"error": "no floor map"})
            try:
                res = self.server.devtool_floor(int(area))
            except (TypeError, ValueError) as e:
                return self._send(400, {"error": "bad area: %s" % e})
            if res is None:
                return self._send(404, {"error": "area %s has no floor map" % area})
            return self._send(200, res)
        if path == "/state":
            # ?area=N asks for a particular capital -- the editor can open any
            # of them with nobody logged in
            _qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            # FE asks for a capital (?area=N); FMO for a band (?band=hq). One
            # positional argument either way -- the state_fn knows its own kind.
            area = (_qs.get("area") or _qs.get("band") or [None])[0]
            try:
                # how many positionals the state_fn takes decides what it gets:
                # () the old snapshot, (area) FE's, (area, query) FMO's -- read
                # off the signature rather than retried on TypeError, so an
                # error INSIDE the function is reported as itself
                fn = self.server.devtool_state
                try:
                    _n = len([p for p in inspect.signature(fn).parameters.values()
                              if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)])
                except (TypeError, ValueError):
                    _n = 1
                st = fn(*([area, _qs][:min(_n, 2)]))
            except Exception as e:                     # never take the server down
                st = {"error": "%s: %s" % (type(e).__name__, e)}
            st["log"] = log_tail()
            return self._send(200, st)
        route = (self.server.devtool_routes or {}).get(("GET", path))
        if route is not None:
            return self._route(route, urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query))
        return self._send(404, {"error": "no such thing"})

    def _actor(self):
        """Who is at the keyboard, as the admin panel's proxy names them. Only
        for the log; the token is what authorises."""
        return (self.headers.get("X-Devtool-Actor") or "").strip()[:64]

    def _route(self, fn, arg):
        """One of a title's extra routes (start(routes=...)): fn(arg, actor)
        -> (status, body[, content type]). Never takes the server down."""
        try:
            res = fn(arg, self._actor())
        except Exception as e:
            res = (500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)})
        return self._send(res[0], res[1], *(res[2:3] or ("application/json",)))

    def do_POST(self):
        if not self._authed():
            return self._send(403, {"error": "bad or missing token"})
        path = urllib.parse.urlparse(self.path).path
        if path == "/edit":
            # KEY: AN EDIT IS APPLIED NOW, not queued. The /cmd queue drains on
            # the player's connection thread, which only wakes on client
            # traffic -- live 2026-09-11 a queued `!goto` sat for minutes behind
            # a frozen client, and with nobody logged in it would never run.
            # Data edits need no client at all, so they run here and the answer
            # is the result, not "queued".
            if self.server.devtool_edit is None:
                return self._send(404, {"error": "no editor on this server"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                op = json.loads(self.rfile.read(min(n, 8192)).decode("utf-8"))
                if not isinstance(op, dict):
                    raise ValueError("not an object")
            except (ValueError, OSError) as e:
                return self._send(400, {"ok": False, "msg": "bad edit: %s" % e})
            try:
                res = self.server.devtool_edit(op)
            except Exception as e:                     # never take the server down
                res = {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
            # PRINTED, not only noted: refusals used to reach the panel's
            # message line and nowhere else, so a "random" failed placement
            # left nothing in the server log to explain it.
            print("[devtool] edit%s %s -> %s%s" % (
                " by " + self._actor() if self._actor() else "",
                json.dumps(op, sort_keys=True), "" if res.get("ok") else
                "REFUSED: ", res.get("msg")), flush=True)
            return self._send(200, res)
        route = (self.server.devtool_routes or {}).get(("POST", path))
        if route is not None:
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 65536)).decode("utf-8") or "{}")
                if not isinstance(body, dict):
                    raise ValueError("not an object")
            except (ValueError, OSError) as e:
                return self._send(400, {"ok": False, "msg": "bad request: %s" % e})
            return self._route(route, body)
        if path != "/cmd":
            return self._send(404, {"error": "no such thing"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            line = self.rfile.read(min(n, 4096)).decode("utf-8", "replace")
        except (ValueError, OSError):
            return self._send(400, {"error": "unreadable body"})
        ok = enqueue(line)
        if ok:
            note("[devtool] queued%s: %s" % (
                " by " + self._actor() if self._actor() else "", line.strip()))
        return self._send(200, {"queued": ok, "line": line.strip(),
                                "pending": len(_Q)})


def start(port, bind, token, state_fn, edit_fn=None, page=None, name="fe-devtool",
          floor_fn=None, routes=None):
    """Start the panel on its own daemon thread. Returns the server, or None.

    `page` swaps the HTML for another title's panel (fmodevtool.py uses this
    server, its gate and its /state + /edit plumbing unchanged); `name` is
    the thread's, so a stack dump says which panel it is. `routes` adds pages
    and endpoints of the title's own: {("GET"|"POST", "/path"): fn}, where
    fn(query dict or JSON body, actor) returns (status, body[, content type]).
    They sit behind the same token gate as everything else here.

    Raises SystemExit for the one configuration that is genuinely dangerous:
    a non-loopback bind with no token.
    """
    if not port:
        return None
    if ThreadingHTTPServer is None:                    # pragma: no cover
        raise SystemExit("--devtool-port needs Python 3.7+")
    bind = bind or "127.0.0.1"
    if bind not in ("127.0.0.1", "localhost", "::1") and not token:
        raise SystemExit(
            "--devtool-bind %s with no --devtool-token: this port runs !goto, "
            "!npc and !land against a live session. Give it a token, or leave "
            "it on loopback and reach it through an ssh tunnel." % bind)
    srv = ThreadingHTTPServer((bind, int(port)), _Handler)
    srv.devtool_state = state_fn
    srv.devtool_edit = edit_fn
    srv.devtool_floor = floor_fn
    srv.devtool_token = token or ""
    srv.devtool_page = page
    srv.devtool_routes = dict(routes or {})
    t = threading.Thread(target=srv.serve_forever, name=name, daemon=True)
    t.start()
    return srv


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>World builder</title>
<style>
/* The admin panel's colours (openlobby admin_web): dark unless the system or
   the panel's own theme switch asks for light. The page sits in the panel's
   frame and copies that switch onto its own root (syncTheme below). */
:root{
  --bg:#0f1216;--panel:#161a20;--sunk:#1d222a;--ink:#e6e9ef;--ink2:#9aa3b2;
  --ink3:#7d8696;--rule:#2a303b;--accent:#6ea6ff;--on-accent:#04121f;
  --warn:#e5a63e;--good:#43c08f;--bad:#f2736b;--scheme:dark;
  --m:ui-monospace,Consolas,monospace;
}
@media (prefers-color-scheme:light){
  :root:not([data-theme="dark"]){--bg:#f4f5f8;--panel:#fff;--sunk:#eef0f4;--ink:#161a21;
    --ink2:#566070;--ink3:#6b7483;--rule:#dde1e8;--accent:#1f5fd6;--on-accent:#fff;
    --warn:#935800;--good:#0f7a54;--bad:#c13a32;--scheme:light}
}
:root[data-theme="light"]{--bg:#f4f5f8;--panel:#fff;--sunk:#eef0f4;--ink:#161a21;
  --ink2:#566070;--ink3:#6b7483;--rule:#dde1e8;--accent:#1f5fd6;--on-accent:#fff;
  --warn:#935800;--good:#0f7a54;--bad:#c13a32;--scheme:light}
:root{color-scheme:var(--scheme)}
*{box-sizing:border-box}
/* a nation badge: the nation's own colour (the client's palette) on a shield */
.nat{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:500;
  color:var(--ink);border:1px solid var(--rule);border-radius:999px;padding:1px 9px 1px 6px;
  background:var(--sunk);white-space:nowrap;vertical-align:middle;line-height:1.5}
.nat svg{width:11px;height:13px;flex:none}
h1 .nat{font-size:12.5px;margin-left:8px}
.capbtns{display:flex;flex-wrap:wrap;gap:6px}
.capbtns button{display:inline-flex;align-items:center;gap:7px;padding:5px 11px 5px 9px;
  border-radius:8px;font-size:13px;text-align:left}
.capbtns button svg{width:13px;height:15px;flex:none}
.capbtns button small{display:block;font-size:11px;color:var(--ink2);line-height:1.2}
.capbtns button span{line-height:1.2}
.capbtns button.on{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent);
  background:color-mix(in srgb,var(--accent) 12%,var(--panel))}
.capbtns .you{font-size:10.5px;color:var(--good);font-weight:600;margin-left:2px}
/* function colours: one swatch per kind of NPC, the same as on the map */
.fdot{display:inline-block;width:10px;height:10px;flex:none;border:1.5px solid #000a;
  transform:rotate(45deg);border-radius:2px;background:var(--c)}
.fdot.ring{transform:none;border-radius:50%;background:none;border:2px solid var(--c);box-shadow:0 0 0 1px #0007,inset 0 0 0 1px #0007}
.fnkey{display:flex;flex-wrap:wrap;gap:5px}
.fnkey button{display:inline-flex;align-items:center;gap:6px;padding:2px 9px;font-size:12px;
  border-radius:999px}
.fnkey button b{font-weight:600;color:var(--ink2);font-variant-numeric:tabular-nums}
.fnkey button.off{opacity:.45;text-decoration:line-through}
.people{display:grid;gap:6px}
.people input{min-width:0;width:100%;font:inherit;font-size:13px;background:var(--sunk);
  color:var(--ink);border:1px solid var(--rule);border-radius:6px;padding:5px 9px}
.plist{display:grid;gap:2px}
.pscroll{display:grid;gap:2px;max-height:470px;overflow:auto;padding-right:2px;align-content:start}
.pscroll .grp{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink3);
  margin:6px 0 1px;display:flex;justify-content:space-between}
.keys{margin:0;font-size:12px;color:var(--ink3);line-height:1.9}
.key i.ghostkey{border-radius:2px;transform:rotate(45deg);background:none;
  border:1.5px dashed var(--ink2);width:9px;height:9px}
.blk{border-top:1px solid var(--rule);padding-top:12px;margin-top:12px}
.blk .hd h3{font-size:.92rem;margin:0;font-weight:600}
.plist .nl{color:var(--ink3);font-style:italic}
.plist button{display:grid;grid-template-columns:12px minmax(0,1fr) auto;gap:8px;
  align-items:center;text-align:left;padding:4px 8px;font-size:12.5px;border-radius:6px}
.plist button .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.plist button .fl{font-size:11.5px;color:var(--ink2);white-space:nowrap}
.plist button.on{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 14%,var(--sunk))}
.plist button.miss{border-style:dashed;background:none}
.plist button.miss .fl{color:var(--warn)}
.plist button.armed{border-color:var(--good);border-style:solid;
  background:color-mix(in srgb,var(--good) 14%,var(--sunk))}
.plist .grp{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink3);
  margin:6px 0 1px;display:flex;justify-content:space-between}
.armbar{font-size:12.5px;border:1px solid var(--good);border-radius:6px;padding:6px 9px;
  display:flex;gap:8px;align-items:center;justify-content:space-between}
.armbar button{padding:2px 9px;font-size:12px}
kbd{font-family:var(--m);font-size:11px;border:1px solid var(--rule);border-bottom-width:2px;
  border-radius:4px;padding:0 4px;background:var(--sunk)}
/* A class that sets display beats the hidden ATTRIBUTE in a plain page -- the
   placer sat on screen permanently because .placer{display:grid} won. */
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 'Segoe UI',system-ui,sans-serif}
.wrap{max-width:1320px;margin:0 auto;padding:18px 14px 60px;display:grid;grid-template-columns:minmax(0,1fr);gap:14px}
/* text cards stay at a readable width; the map card is allowed to be wide */
.wrap>*{width:100%;max-width:900px;justify-self:center}
.wrap>#mapcard{max-width:none}
h1{font-size:1.15rem;margin:0;font-weight:600;letter-spacing:-.01em}
h1 small{color:var(--ink3);font-weight:400;font-family:var(--m);font-size:11px;
         letter-spacing:.12em;text-transform:uppercase;display:block;margin-bottom:4px}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:12px;padding:14px 16px}
.hd{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:4px 10px;margin-bottom:9px}
.hd h2{font-size:.95rem;margin:0;font-weight:600}
.hd .sub{font-family:var(--m);font-size:11.5px;color:var(--ink3)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:1px;
      background:var(--rule);border:1px solid var(--rule);border-radius:4px;overflow:hidden}
.grid div{background:var(--panel);padding:7px 10px}
.grid dt{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink3)}
.grid dd{margin:1px 0 0;font-family:var(--m);font-size:13px}
.row{display:flex;flex-wrap:wrap;gap:7px;margin-top:9px}
button{font:inherit;font-size:13.5px;cursor:pointer;color:var(--ink);
       background:var(--sunk);border:1px solid var(--rule);border-radius:6px;
       padding:6px 12px}
button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
button:disabled{opacity:.4;cursor:not-allowed}
button.go{border-color:var(--good);color:var(--good)}
button.warn{border-color:var(--warn);color:var(--warn)}
ul.miss{list-style:none;margin:9px 0 0;padding:0;display:grid;gap:5px;
        max-height:290px;overflow:auto}
ul.miss li{display:flex;flex-wrap:wrap;align-items:center;gap:4px 9px;font-family:var(--m);font-size:12.5px}
ul.miss li span{color:var(--ink2)}
ul.miss button{padding:3px 10px;font-size:12px}
.bar{height:5px;background:var(--sunk);border-radius:3px;overflow:hidden;margin-top:7px}
.bar i{display:block;height:100%;background:var(--good)}
.maprow{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}
/* the map column is only as wide as the (height-capped) map, so the door
   list sits against it instead of across a dead gap */
@media(min-width:1000px){.maprow{grid-template-columns:minmax(0,max(320px,calc(100vh - 150px))) 360px;justify-content:start}}
.mapcol{display:grid;gap:8px;align-content:start;min-width:0}
.toolbar{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center}
.halftabs{display:flex;flex-wrap:wrap;gap:6px}
.capsel{display:inline-flex;gap:6px;align-items:center;font-size:12.5px;color:var(--ink2)}
.capsel select{font:inherit;font-size:13px;background:var(--sunk);color:var(--ink);
  border:1px solid var(--rule);border-radius:4px;padding:4px 6px}
.editmsg{font-size:12.5px;border-radius:4px;padding:6px 9px;border:1px solid var(--rule)}
.editmsg.ok{border-color:var(--good);color:var(--good)}
.editmsg.bad{border-color:var(--warn);color:var(--warn)}
.livebar{font-size:12px;color:var(--ink3);border-left:3px solid var(--rule);padding:2px 0 2px 9px}
.tfbar{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px;font-size:12px;color:var(--ink3)}
.tfbar:empty{display:none}
.tfbar button{padding:3px 9px;font-size:12px}
.npcdetail .says{font-size:12.5px;color:var(--ink2);border-left:3px solid var(--rule);
  padding:2px 0 2px 9px;font-style:italic}
.livebar.live{border-left-color:var(--good);color:var(--ink2)}
.npcdetail{border-color:var(--accent)}
.npcdetail .t{display:flex;align-items:center;gap:7px}
.exitwrap{position:relative;border:1px solid var(--rule);border-radius:4px;overflow:hidden;
  background:var(--sunk);aspect-ratio:1/1;width:100%}
.exitwrap canvas{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair;
  image-rendering:pixelated}
.npcdetail .acts button{padding:4px 9px;font-size:12.5px}
.npcdetail select{flex:1;min-width:0;font:inherit;font-size:12.5px;background:var(--sunk);
  color:var(--ink);border:1px solid var(--rule);border-radius:4px;padding:4px 6px}
.halftabs button{padding:4px 11px;font-size:13px}
.halftabs button.on{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.chips{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12.5px;color:var(--ink2)}
.chips label{display:inline-flex;gap:5px;align-items:center;cursor:pointer;white-space:nowrap}
/* as big as the column allows, but never taller than the screen: on a
   1366x768 laptop the map was 884 px tall and its bottom rows needed a scroll */
.mapwrap{position:relative;width:100%;max-width:max(320px,calc(100vh - 150px));
         aspect-ratio:1/1;background:var(--sunk);
         border:1px solid var(--rule);border-radius:4px;overflow:hidden}
.mapwrap img,.mapwrap canvas{position:absolute;inset:0;width:100%;height:100%;
  image-rendering:pixelated}
.mapwrap img{transform-origin:0 0}
.mapwrap.zoomed canvas{cursor:grab}
.mapwrap.panning canvas{cursor:grabbing}
.zoombar{display:inline-flex;gap:4px;align-items:center}
.zoombar button{padding:2px 9px;font-size:13px;min-width:30px}
.zoombar .lvl{font-size:12px;color:var(--ink3);min-width:3.2em;
  font-variant-numeric:tabular-nums}
.mapfoot{display:flex;flex-wrap:wrap;gap:4px 14px;align-items:center}
.key{display:inline-flex;gap:6px;align-items:center;color:var(--ink2);font-size:12px}
.key i{width:10px;height:10px;border-radius:50%;border:1px solid #0006;flex:0 0 auto}
.side{display:grid;gap:10px;align-content:start;min-width:0}
.side h3{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink3);
         margin:0;font-weight:600;display:flex;justify-content:space-between}
.doorlist{display:grid;gap:3px}
.doorlist button{display:grid;grid-template-columns:38px 44px minmax(0,1fr) auto;
  gap:8px;align-items:center;text-align:left;padding:5px 8px;font-size:12.5px}
.doorlist button.on{border-color:var(--warn);background:color-mix(in srgb,var(--warn) 14%,var(--sunk))}
.doorlist .g{font-family:var(--m);color:var(--ink2)}
.doorlist .n{font-family:var(--m);font-weight:600}
.doorlist .to{color:var(--ink2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pill{font-size:10.5px;letter-spacing:.04em;border:1px solid var(--rule);border-radius:9px;
      padding:0 7px;color:var(--ink2);white-space:nowrap}
.pill.moved{border-color:var(--warn);color:var(--warn)}
.pill.here{border-color:var(--good);color:var(--good)}
.doordetail{border:1px solid var(--warn);border-radius:5px;padding:10px 11px;
            display:grid;gap:7px;font-size:13px}
.doordetail .t{font-weight:600}
.doordetail dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:3px 10px;margin:0}
.doordetail dt{color:var(--ink3);font-size:12px}
.doordetail dd{margin:0;font-family:var(--m);font-size:12.5px}
.doordetail .acts{display:flex;flex-wrap:wrap;gap:6px}
.doordetail .why{font-size:12px;color:var(--ink3)}
.linkish{background:none;border:0;padding:0;color:var(--accent);font-size:12.5px;
         cursor:pointer;text-decoration:underline}
.linkish:hover:not(:disabled){color:var(--ink)}
.calbox{font-size:12px;line-height:1.5;color:var(--ink2);border:1px solid var(--rule);
        border-radius:5px;padding:8px 9px}
.calbox b{color:var(--ink)}
.placer{display:grid;gap:6px;border:1px solid var(--rule);border-radius:5px;padding:9px}
.placer .at{font-family:var(--m);font-size:12px;color:var(--accent)}
.placer select{width:100%;font:inherit;font-size:13px;background:var(--sunk);
  color:var(--ink);border:1px solid var(--rule);border-radius:4px;padding:5px 7px}
.placer .row2{display:grid;grid-template-columns:1fr auto;gap:6px}
.readout{font-family:var(--m);font-size:12px;color:var(--ink2);
         background:var(--sunk);border-radius:3px;padding:5px 8px;min-height:24px}
pre{font-family:var(--m);font-size:11.5px;line-height:1.55;background:var(--sunk);
    border:1px solid var(--rule);border-radius:4px;padding:10px;margin:0;
    max-height:230px;overflow:auto;white-space:pre-wrap;word-break:break-word}
.warnbox{border-left:3px solid var(--warn);padding-left:11px;color:var(--ink2);font-size:13.5px}
/* TEXT inputs only. This rule used to hit every checkbox too, forcing each to
   180 px wide inside a 210 px column and shoving its label off the card. */
input:not([type]),input[type=text]{font:inherit;font-family:var(--m);font-size:13px;
      background:var(--sunk);color:var(--ink);border:1px solid var(--rule);
      border-radius:4px;padding:6px 9px;flex:1;min-width:180px}
input[type=checkbox]{margin:0;accent-color:var(--accent)}
select{font:inherit;font-size:13px;background:var(--sunk);color:var(--ink);
  border:1px solid var(--rule);border-radius:6px;padding:5px 7px;max-width:100%}
/* not in world: fade the cards that need a live session -- NOT the map
   editor, which works with nobody logged in */
.dead #ingame .blk{opacity:.45}
</style></head><body><div class="wrap">
<h1><small>Fantasy Earth · world builder</small><span id="maptitle">connecting…</span><span id="mapnat"></span></h1>

<div class="card" id="mapcard" hidden>
  <div class="hd"><h2>Capital map</h2><span class="sub" id="mapsub"> - </span></div>
  <div class="warnbox" id="mapnone" hidden>The map is drawn for the area a
  client is standing in, and nothing is connected. Log in and walk into a
  capital and it appears here. The same goes for every button on this page:
  commands are run by the player's own connection, so they queue until there
  is one.</div>
  <div class="maprow" id="maprow">
    <div class="mapcol">
      <div class="toolbar">
        <div class="capbtns" id="capbtns" role="group" aria-label="Capital"></div>
        <select id="capsel" aria-label="Capital" hidden></select>
        <div class="halftabs" id="halftabs"></div>
        <div class="chips">
          <label><input type="checkbox" id="shIcons" checked> Shop icons</label>
          <label><input type="checkbox" id="shNpcs" checked> NPCs</label>
          <label><input type="checkbox" id="shGhost" checked> Missing shop spots</label>
          <label><input type="checkbox" id="shGates" checked> Doors</label>
          <label><input type="checkbox" id="shArr"> Landings into this half</label>
          <label><input type="checkbox" id="shFloor" checked> Floor</label>
          <label><input type="checkbox" id="shYou" checked> You</label>
          <label><input type="checkbox" id="shBoth"> Other half's shop icons</label>
          <label><input type="checkbox" id="calMode"> Align this map</label>
        </div>
      </div>
      <div class="fnkey" id="fnkey" aria-label="NPC kinds: click one to hide or show it"></div>
      <div class="mapwrap">
        <img id="mapimg" alt="capital minimap">
        <canvas id="mapcv" width="1024" height="1024"></canvas>
      </div>
      <div class="editmsg" id="editmsg" hidden></div>
      <div class="mapfoot">
        <span class="zoombar" title="wheel = zoom at the pointer; drag empty map to pan">
          <button id="zin" aria-label="zoom in">+</button>
          <button id="zout" aria-label="zoom out">&minus;</button>
          <button id="zfit">Fit</button><span class="lvl" id="zlvl">1&times;</span>
        </span>
        <button id="undobtn" disabled title="Nothing to undo">Undo</button>
        <div class="readout" id="mapread" style="flex:1;min-width:220px"> - </div>
        <span class="key">Shop icons by class:</span>
        <span class="key"><i style="background:#e8563a"></i>Warrior</span>
        <span class="key"><i style="background:#4ed446"></i>Scout</span>
        <span class="key"><i style="background:#76ccfc"></i>Sorcerer</span>
        <span class="key"><i style="background:#fde604"></i>Door</span>
        <span class="key"><i style="background:none;border:2px solid #ff3b30"></i>You</span>
        <span class="key"><i class="ghostkey"></i>Missing shop spot</span>
      </div>
      <p class="keys" id="keys">Keys: <kbd>1</kbd>-<kbd>5</kbd> capital ·
      <kbd>H</kbd> other half · <kbd>/</kbd> find · <kbd>Esc</kbd> clear ·
      <kbd>Ctrl</kbd>+<kbd>Z</kbd> undo · arrows move the selected NPC
      (<kbd>Shift</kbd> further) · <kbd>Q</kbd> / <kbd>E</kbd> turn it ·
      wheel zooms</p>
    </div>
    <div class="side">
      <div class="armbar" id="armbar" hidden></div>
      <div class="calbox" id="calbox" hidden></div>
      <div class="livebar" id="livebar"></div>
      <div class="tfbar" id="tfbar"></div>
      <div class="tfbar" id="shopbar"></div>
      <div class="doordetail" id="doordetail" hidden></div>
      <div class="doordetail npcdetail" id="npcdetail" hidden></div>
      <div class="placer" id="placer" hidden>
        <div class="at" id="placeat"> - </div>
        <select id="placerole"></select>
        <div class="row2">
          <select id="placeyaw">
            <option value="0">faces N</option><option value="90">faces E</option>
            <option value="180">faces S</option><option value="270">faces W</option>
          </select>
          <button id="placego" class="go">place</button>
        </div>
        <button id="spawnhere">set spawn here</button>
        <button id="placecancel" class="warn">cancel</button>
      </div>
      <div class="people" id="peoplebox">
        <h3><span>In this half</span><span id="pcount"></span></h3>
        <input type="search" id="psearch" placeholder="Find a name, kind or door ( / )"
               aria-label="Find an NPC by name or kind, or a door" autocomplete="off">
        <div class="pscroll" id="pscroll">
          <div class="plist" id="plist"></div>
          <div class="grp" id="doorsbox"><span>Doors</span><span id="doorcount"></span></div>
          <div class="doorlist" id="doorlist"></div>
        </div>
      </div>
      <p class="why" style="margin:0;font-size:12px;color:var(--ink3)">You do not
      need to be in game. Click an NPC or a door to select it, or empty floor to
      place someone there. A faint diamond is a missing shopkeeper's painted
      spot: click it to place them there.</p>
    </div>
  </div>
</div>

<section class="card" id="ingame">
  <div class="hd"><h2>In game</h2><span class="sub">these act where your character stands</span></div>
  <p class="why" style="margin:0 0 4px;font-size:12.5px;color:var(--ink3)">Everything
  here runs through your own connection: log in and walk into the area first.
  Edits show after you re-enter the area; the client reads them on entry only.</p>

<div class="blk" id="where">
  <div class="hd"><h3>Where you are: <span id="title">connecting…</span><span id="wherenat"></span></h3><span class="sub" id="conn"> - </span></div>
  <div class="grid" id="pos"></div>
  <div class="warnbox" id="nopos" hidden>No position since you changed area - take one step in game and this fills in. Placing at a stale position puts
  things where you used to be.</div>
</div>

<div class="blk" id="checklist">
  <div class="hd"><h3>What this area is missing</h3><span class="sub" id="progress"> - </span></div>
  <div class="bar"><i id="barfill" style="width:0%"></i></div>
  <ul class="miss" id="miss"></ul>
</div>

<div class="blk">
  <div class="hd"><h3>Spawn points</h3><span class="sub" id="spawnsub"> - </span></div>
  <div class="row">
    <button data-cmd="!spawn" class="go">Set plain spawn here</button>
    <button data-cmd="!spawn atk" class="go">Set attacker spawn</button>
    <button data-cmd="!spawn def" class="go">Set defender spawn</button>
    <button data-cmd="!spawn clear" class="warn">Clear this area</button>
  </div>
</div>

<div class="blk">
  <div class="hd"><h3>Link a door</h3><span class="sub" id="doorsub"> - </span></div>
  <div class="row">
    <select id="linkdest"><option value="">where should it go?</option></select>
    <button id="linkgo">Link this door → there</button>
    <button data-cmd="!link">Link (second side)</button>
    <button data-cmd="!link cancel" class="warn">Cancel pending</button>
  </div>
</div>

<div class="blk">
  <div class="hd"><h3>The nearest NPC</h3><span class="sub" id="npcsub">walk up to one</span></div>
  <div class="row">
    <button data-cmd="!face 0">face N</button>
    <button data-cmd="!face 90">face E</button>
    <button data-cmd="!face 180">face S</button>
    <button data-cmd="!face 270">face W</button>
    <input id="facedeg" placeholder="or degrees" inputmode="numeric" style="max-width:130px">
    <button id="facego">turn</button>
  </div>
  <div class="row">
    <button data-cmd="!move" class="go">move it here</button>
    <button data-cmd="!remove" class="warn" data-confirm="Delete the nearest NPC? It goes back on the missing list.">remove it</button>
    <button data-cmd="!nudge 0:1">nudge +Z</button>
    <button data-cmd="!nudge 0:-1">nudge −Z</button>
    <button data-cmd="!nudge 1:0">nudge +X</button>
    <button data-cmd="!nudge -1:0">nudge −X</button>
  </div>
</div>

<div class="blk">
  <div class="hd"><h3>Any command</h3><span class="sub">runs exactly as if typed</span></div>
  <div class="row">
    <input id="raw" placeholder="!expect  ·  !town list  ·  !goto 92  ·  @hello">
    <button id="rawgo">Run</button>
  </div>
</div>

<div class="blk">
  <div class="hd"><h3>Server log</h3><span class="sub" id="pending"></span></div>
  <pre id="log">…</pre>
</div>
</section>
</div>
<script>
const T = new URLSearchParams(location.search).get('t') || '';
const q = s => document.querySelector(s);
// THE PANEL'S THEME SWITCH. Inside the admin panel this page is a same-origin
// frame, and the panel's light / dark / system choice is a data-theme on ITS
// root, which a media query here cannot see. Copy it, and follow changes.
// ?theme=light|dark does the same for a page opened on its own.
function syncTheme(){
  let t = new URLSearchParams(location.search).get('theme') || '';
  try{ if(window.parent !== window) t = parent.document.documentElement.dataset.theme || ''; }catch(e){}
  if(t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
}
syncTheme();
try{ if(window.parent !== window) new MutationObserver(syncTheme).observe(
  parent.document.documentElement, {attributes: true, attributeFilter: ['data-theme']}); }catch(e){}
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// A nation, as the game colours it: the server sends the client's own
// palette colour and the name it serves the client (world/devtool.py
// capital_nation). Drawn as a shield, like a capital on the war map.
function shield(col){
  return `<svg viewBox="0 0 12 14" aria-hidden="true"><path d="M1 1h10v7.2L6 13 1 8.2z" fill="${esc(col || '#888')}" stroke="rgba(0,0,0,.55)" stroke-width="1.2"/></svg>`;
}
function natBadge(n){
  return n ? `<span class="nat" title="Capital of ${esc(n.name)}">${shield(n.color)}${esc(n.name)}</span>` : '';
}
// What an NPC is FOR (world/devtool.py npc_function), as a colour. Function
// NPCs are drawn as filled diamonds; townsfolk and unknown rows as rings, so
// the people who DO something stand out from the crowd at a glance.
const FNCOL = {shop:'#ff5fa2', bank:'#b388ff', inn:'#ff9f43', warp:'#00e5c0',
               manager:'#4d8dff', trainer:'#2fd47a', prize:'#ffd84d', quest:'#ff4d4d',
               weapon:'#d2a86e', guide:'#f2f2f2', guard:'#8fa0b8', townsfolk:'#ffffff',
               unknown:'#ff3df2'};
const RINGFN = new Set(['townsfolk', 'unknown']);
let FNS = [];          // [{key, label}] from the server, in legend order
function fnLabel(k){ const f = FNS.find(o => o.key === k); return f ? f.label : (k || 'Unknown'); }
function fnDot(k){
  return `<i class="fdot${RINGFN.has(k) ? ' ring' : ''}" style="--c:${FNCOL[k] || '#999'}" aria-hidden="true"></i>`;
}
const pretty = n => String(n || '').replace(/_/g, ' ');
// Remembered per browser: which layers are on, which NPC kinds are hidden,
// the half last shown per capital. Storage can be unavailable; the page
// works without it.
let PREFS = {};
try{ PREFS = JSON.parse(localStorage.getItem('fe-wb') || '{}') || {}; }catch(e){ PREFS = {}; }
PREFS.layers = PREFS.layers || {}; PREFS.halfOf = PREFS.halfOf || {};
let HIDEFN = new Set(PREFS.hide || []);
function savePrefs(){
  PREFS.hide = [...HIDEFN];
  try{ localStorage.setItem('fe-wb', JSON.stringify(PREFS)); }catch(e){}
}
let ARMED = null;      // a missing role waiting for a click on the map
let PICKNAME = null;   // the role a ghost click opened the placer for
let PENDSEL = null;    // a name just placed: select it when it appears
let PFILTER = '';      // the people search
let UNDO = [];         // the server's undo list, newest first
function undo(){
  if(!UNDO.length) return;
  ARMED = null; PICK = null; PICKNAME = null; q('#placer').hidden = true;
  edit({op: 'undo'});
}
// Relative, so the page works both at / and behind the admin panel's
// /games/<title>/ proxy (which adds the token itself).
const qs = p => { p = p.replace(/^\//, ''); return T ? p + (p.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(T) : p; };
let live = false, area = null;

async function send(line){
  try{
    const r = await fetch(qs('/cmd'), {method:'POST', body:line});
    const j = await r.json();
    if(!j.queued) note('refused: ' + line);
    setTimeout(tick, 250);
  }catch(e){ note('send failed: ' + e); }
}
function note(s){ const l = q('#log'); l.textContent += '\n' + s; l.scrollTop = l.scrollHeight; }

// WARNING: NEVER REBUILD WHAT HAS NOT CHANGED. The page re-renders every second; a
// list rebuilt with innerHTML REPLACES the button you are pressing, and a
// press whose element is gone by the release never becomes a click. That is
// how clicks on "place" were being silently swallowed.
function setHTML(el, html){ if(el.dataset.h !== html){ el.dataset.h = html; el.innerHTML = html; } }

function facts(o){
  return Object.entries(o).map(([k,v]) =>
    `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('');
}

function render(s){
  q('#title').textContent = s.area ? `${s.area_name} · area ${s.area}` : 'not in a field';
  setHTML(q('#wherenat'), s.nation ? natBadge(s.nation) : '');
  q('#conn').textContent = s.in_field ? 'in world' : 'not in world';
  document.body.classList.toggle('dead', !s.in_field);
  area = s.area;
  const p = s.pos;
  q('#nopos').hidden = !!p;
  setHTML(q('#pos'), facts({
    x: p ? p[0].toFixed(1) : ' - ', y: p ? p[1].toFixed(2) : ' - ',
    z: p ? p[2].toFixed(1) : ' - ',
    'battle map': s.hmap ? 'map' + String(s.hmap).padStart(2,'0') : ' - ',
    'map ground': s.ground ?? ' - ', 'holder': s.holder ?? ' - '
  }));

  const tot = s.roster_total, have = s.roster_placed;
  q('#progress').textContent = tot ? `${have} of ${tot} roles placed`
    : (s.monsters ? `${s.monsters} monster points ship here` : 'nothing shipped for this area');
  q('#barfill').style.width = tot ? (100*have/tot).toFixed(0)+'%' : '0%';
  setHTML(q('#miss'), (s.missing||[]).map(m =>
    `<li><button data-npc="${esc(m.name)}" title="Places it where your character is standing">Place where I stand</button>
       ${fnDot(m.fn)}<span>${esc(m.name)}</span>
       <span style="color:var(--ink3)">${esc(fnLabel(m.fn))} · model ${m.model} · script ${m.script}</span></li>`).join('')
    || (tot ? '<li><span style="color:var(--good)">every role is placed</span></li>' : ''));

  q('#spawnsub').textContent = (s.spawns && s.spawns.length)
    ? s.spawns.map(r => `${r.tag||'plain'} (${r.x.toFixed(0)}, ${r.y.toFixed(1)}, ${r.z.toFixed(0)})`).join('   ')
    : 'none - falls back to the map height or --spawn-pos';
  const sel = q('#linkdest'); const keep = sel.value;
  setHTML(sel, '<option value="">where should it go?</option>' +
    (s.destinations||[]).map(d =>
      `<option value="${d.area}">${esc(d.name)} · ${d.area}${d.capital ? ' (capital' + (d.nation ? ' of ' + esc(d.nation.name) : '') + ')' : ''} - ${esc(d.why)}</option>`).join(''));
  if(keep && [...sel.options].some(o=>o.value===keep)) sel.value = keep;
  q('#npcsub').textContent = s.roster_placed
    ? s.roster_placed + ' placed in this area' : 'none placed here yet';
  q('#doorsub').textContent = s.doors_total
    ? `${s.doors_mapped} of ${s.doors_total} shipped doors mapped`
    + (s.nearest_door != null ? ` · nearest is ${s.nearest_door.toFixed(0)}u away` : '')
    : 'no shipped doors in this area';
  q('#pending').textContent = s.pending ? s.pending + ' queued' : '';
  UNDO = s.undo || [];
  const ub = q('#undobtn');
  ub.disabled = !UNDO.length;
  ub.textContent = UNDO.length ? `Undo (${UNDO.length})` : 'Undo';
  ub.title = UNDO.length ? `Undo: ${UNDO[0].label} (Ctrl+Z)` : 'Nothing to undo';
  const l = q('#log');
  const stuck = l.scrollTop + l.clientHeight >= l.scrollHeight - 20;
  l.textContent = (s.log||[]).join('\n');
  if(stuck) l.scrollTop = l.scrollHeight;
  // The map card manages its own buttons (a door on the far half cannot be
  // landed on, whatever the connection state), so the blanket switch skips
  // it -- and runs BEFORE the map draws, not after, so nothing it sets is
  // silently undone a line later.
  for(const b of document.querySelectorAll('button'))
    if(!b.closest('#mapcard')) b.disabled = !s.in_field;
  q('#linkdest').disabled = !s.in_field;
  drawMap(s);
}


// ---- the minimap ---------------------------------------------------------
// The art is 256 px and the canvas is 1024, so everything is drawn at 4x and
// the pixel-art stays crisp (image-rendering: pixelated on both layers).
const CLASSCOL = {red:'#e8563a', green:'#4ed446', blue:'#76ccfc', tan:'#c8b488',
                  yellow:'#fde604'};
const SELCOL = '#ff9500';
let MAPST = null;      // the last full state
let HALF = 0;          // which half of the capital is on screen
let CAL = false;       // "align this map"
let SELP = null;       // the selected door, by portal id, on the shown half
let SEENVIA = null;    // last arrived_via seen, so auto-select fires once
let PRESS = null;      // a mouse press in progress (maybe a drag); blocks redraw
let PICK = null;       // an empty-map click waiting for "place"
let SELN = null;       // the selected NPC, by its town ROW index, on the shown half
let EXITPRESS = null;  // a press on the exit mini-map; blocks redraw like PRESS
// Where each half's collision has FLOOR, per minimap pixel, as a tinted
// canvas -- fetched once per half and per alignment (it moves with the fit).
const FLOOR = {};
const ARTIMG = {};
function projKey(h){ const p = h.proj; return [p.ax, p.cx, p.az, p.cz].join(','); }
function loadFloor(h){
  const f = FLOOR[h.area];
  if(f && (f.key === projKey(h) || f.loading)) return f;
  FLOOR[h.area] = {key: projKey(h), loading: true};
  fetch(qs('/floor?area=' + h.area)).then(r => r.json()).then(res => {
    const raw = atob(res.bits), n = res.size;
    const bits = new Uint8Array(raw.length);
    for(let i = 0; i < raw.length; i++) bits[i] = raw.charCodeAt(i);
    const cv = document.createElement('canvas'); cv.width = cv.height = n;
    const g = cv.getContext('2d'), im = g.createImageData(n, n);
    for(let i = 0; i < n * n; i++){
      if(bits[i >> 3] & (1 << (i & 7))){
        im.data[i*4] = 60; im.data[i*4+1] = 230; im.data[i*4+2] = 255; im.data[i*4+3] = 90;
      }
    }
    g.putImageData(im, 0, 0);
    FLOOR[h.area] = {key: projKey(h), bits, size: n, cv};
    if(MAPST && !PRESS && !EXITPRESS) drawMap(MAPST);
  }).catch(() => { FLOOR[h.area] = null; });
  return FLOOR[h.area];
}
// true / false when the floor map is loaded, null when it is not yet
function floorAt(h, px, py){
  const f = FLOOR[h.area];
  if(!f || !f.bits || f.key !== projKey(h)) return null;
  const x = Math.floor(px), y = Math.floor(py);
  if(x < 0 || y < 0 || x >= f.size || y >= f.size) return false;
  const i = y * f.size + x;
  return !!(f.bits[i >> 3] & (1 << (i & 7)));
}
function art(stem){
  if(!ARTIMG[stem]){ const im = new Image(); im.onload = () => MAPST && drawMap(MAPST);
                     im.src = qs('/map/' + stem + '.png'); ARTIMG[stem] = im; }
  return ARTIMG[stem];
}
// the capital the editor shows; remembered between visits. Storage can be
// unavailable (private windows, blocked site data) -- the page works without.
let FOCUS = null;
try{ FOCUS = localStorage.getItem('fe-focus') || null; }catch(e){ FOCUS = null; }
let WANTAREA = FOCUS && PREFS.halfOf[FOCUS] != null ? +PREFS.halfOf[FOCUS] : null;

async function edit(op){
  // Applied on the server at once -- no game session, no queue. The reply is
  // the result, so say it where the user is looking.
  let res;
  try{
    const r = await fetch(qs('/edit'), {method:'POST', body: JSON.stringify(op)});
    res = await r.json();
  }catch(e){ res = {ok:false, msg:'could not reach the server: ' + e}; }
  const m = q('#editmsg');
  m.hidden = false; m.className = 'editmsg ' + (res.ok ? 'ok' : 'bad');
  m.textContent = res.msg || (res.ok ? 'done' : 'refused');
  setTimeout(tick, 150);
  return res;
}
const COMPASS = ['north','north-east','east','south-east','south','south-west','west','north-west'];
function compass(yaw){ return COMPASS[Math.round(((yaw % 360) + 360) % 360 / 45) % 8]; }
function selNpc(){
  const h = cur(); if(!h || SELN === null) return null;
  return (h.npcs || []).find(n => n.idx === SELN) || null;
}
// 🔍 THE VIEW: a window onto the 256 px art -- zoom VZ (1..16) with its top
// left at (VX0, VY0) in art pixels. Everything the page REASONS in stays in
// art pixels; only drawing (T in drawMap) and the pointer (evPix) go through
// the view. Markers, labels and click radii keep their SCREEN size, so at
// 8x two townsfolk 3 art px apart are 24 px apart and easy to tell apart.
let VZ = 1, VX0 = 0, VY0 = 0, VIEWAREA = null;
const VZ_MAX = 16, ART = 256;
function clampView(){
  VZ = Math.min(VZ_MAX, Math.max(1, VZ));
  const span = ART / VZ;
  VX0 = Math.min(ART - span, Math.max(0, VX0));
  VY0 = Math.min(ART - span, Math.max(0, VY0));
  q('#zlvl').textContent = (VZ < 10 ? VZ.toFixed(1).replace(/\.0$/, '') : VZ.toFixed(0)) + '\u00d7';
  q('.mapwrap').classList.toggle('zoomed', VZ > 1);
}
// zoom to `nz`, keeping the art point under screen fraction (fx, fy) still
function zoomAt(nz, fx, fy){
  const ax = VX0 + fx * ART / VZ, ay = VY0 + fy * ART / VZ;
  VZ = Math.min(VZ_MAX, Math.max(1, nz));
  VX0 = ax - fx * ART / VZ; VY0 = ay - fy * ART / VZ;
  clampView();
  if(MAPST) drawMap(MAPST);
}
// a radius in SCREEN terms (canvas px at 1x = art px), as art pixels
const ZR = r => r / VZ;
// the end of a selected NPC's facing line, in MAP pixels -- the rotation handle
// (a constant distance on SCREEN: ROT_R art px at 1x, ROT_R / VZ zoomed)
const ROT_R = 7;
function rotHandle(h, n, yaw){
  const [px, py] = w2p(h, n.x, n.z), a = yaw * Math.PI / 180;
  return [px + Math.sin(a) * ZR(ROT_R), py - Math.cos(a) * ZR(ROT_R)];
}

function halves(s){
  if(s.halves && s.halves.length) return s.halves;
  if(!s.minimap) return [];
  return [{area: s.area, live: true, minimap: s.minimap, name: s.area_name,
           proj: s.proj, icons: s.icons || [], npcs: s.npcs || [],
           gates: s.gates || [], cal: [], missing: s.missing || []}];
}
function cur(){
  const hs = MAPST ? halves(MAPST) : [];
  return hs[Math.min(HALF, hs.length - 1)] || null;
}
function halfName(h){
  // both halves of a capital carry the SAME English name; the district
  // (from the old-capital guides) and the area id tell them apart
  return `${h.name}${h.district ? ' ' + h.district : (h.inner ? ' inner' : '')} · ${h.area}`;
}
function drawTownsfolk(h){
  const tf = h.townsfolk || [], left = tf.filter(t => !t.placed).length;
  const silent = tf.filter(t => t.silent).length;
  setHTML(q('#tfbar'), !tf.length ? '' :
    `<span>Townsfolk: ${tf.length - left} of ${tf.length} here`
    + (silent ? `, ${silent} with no recorded lines (they only say "Good day.")` : '') + `</span>`
    + (left ? `<button data-edit='${JSON.stringify({op:"townsfolk", area: h.area})}'`
             + ` data-confirm="Place ${left} townsfolk on their suggested spots in ${esc(h.district || h.area)}?">`
             + `Place the other ${left}</button>` : ''));
  // the missing shopkeepers whose painted icon is in this half: one button
  // puts them all on their icons (the same spots the faint diamonds show)
  const gh = h.ghosts || [], can = gh.filter(g => g.y != null), no = gh.filter(g => g.y == null);
  const where = esc(h.district || h.area);
  setHTML(q('#shopbar'), !gh.length ? '' :
    `<span>Shop icons: ${gh.length} shopkeeper${gh.length > 1 ? 's' : ''} not placed</span>`
    + (can.length ? `<button id="shopsgo" data-edit='${JSON.stringify({op:"shops", area: h.area})}'`
       + ` data-confirm="Place ${can.length} on their painted icons in ${where}?\n\n`
       + can.map(g => esc(pretty(g.name))).join('\n')
       + (no.length ? `\n\nSkipped, no floor near the icon: ${no.map(g => esc(pretty(g.name))).join(', ')}` : '')
       + `\n\nRoles already placed are left alone. Undo takes them all back.">`
       + `Place ${can.length} on their icons</button>` : ''));
}
function w2p(h, x, z){ const p = h.proj; return [x / p.ax + p.cx, z / p.az + p.cz]; }
// a ghost's place on the art: its floor spot, or the icon itself when the
// icon has no floor near it
function ghostPix(h, gh){ return gh.y != null ? w2p(h, gh.x, gh.z) : [gh.px, gh.py]; }
function p2w(h, px, py){ const p = h.proj; return [(px - p.cx) * p.ax, (py - p.cz) * p.az]; }
function evPix(e){
  const r = q('#mapcv').getBoundingClientRect();
  const sz = (cur() || {proj:{size:256}}).proj.size;
  return [VX0 + (e.clientX - r.left) / r.width * sz / VZ,
          VY0 + (e.clientY - r.top) / r.height * sz / VZ];
}
function selGate(){
  const h = cur(); if(!h || SELP === null) return null;
  return (h.gates || []).find(g => g.portal === SELP) || null;
}
function drawShape(g, kind, X, Y, col){
  g.lineWidth = 3; g.strokeStyle = 'rgba(0,0,0,.75)'; g.fillStyle = col;
  g.beginPath();
  if(kind === 'sword'){
    g.moveTo(X-9,Y+9); g.lineTo(X+9,Y-9); g.lineWidth = 7; g.stroke();
    g.beginPath(); g.moveTo(X-9,Y+9); g.lineTo(X+9,Y-9);
    g.lineWidth = 4; g.strokeStyle = col; g.stroke(); return;
  }
  if(kind === 'shield'){
    g.moveTo(X,Y-10); g.lineTo(X+8,Y-5); g.lineTo(X+8,Y+3);
    g.lineTo(X,Y+11); g.lineTo(X-8,Y+3); g.lineTo(X-8,Y-5); g.closePath();
  } else if(kind === 'ring'){
    g.arc(X,Y,8,0,6.2832); g.stroke(); g.beginPath(); g.arc(X,Y,4.5,0,6.2832);
  } else {
    g.arc(X,Y,6,0,6.2832);
  }
  g.fill(); g.stroke();
}
function tag(g, text, X, Y, col){
  g.font = '600 20px ui-monospace,Menlo,Consolas,monospace';
  g.textAlign = 'left'; g.textBaseline = 'middle';
  const w = g.measureText(text).width;
  g.fillStyle = 'rgba(8,11,18,.85)'; g.fillRect(X, Y - 13, w + 12, 26);
  g.fillStyle = col; g.fillText(text, X + 6, Y + 1);
}

// ---- the side column -----------------------------------------------------
function drawPicker(s){
  const caps = s.capitals || [];
  setHTML(q('#capsel'), caps.map(c =>
    `<option value="${c.area}">${esc(c.name)}${c.nation ? ' (' + esc(c.nation.name) + ')' : ''}</option>`).join(''));
  let on = null;
  if(s.focus != null){
    const hs = halves(s), want = String(hs.length ? hs[0].area : s.focus);
    // show the OUTER half's option for either half of a capital
    on = caps.find(c => c.area === +want || c.inner === +want) || null;
    if(on) q('#capsel').value = String(on.area);
  }
  // one button per capital, each wearing its nation: nobody has to remember
  // which capital is whose
  setHTML(q('#capbtns'), caps.map(c =>
    `<button data-cap="${c.area}" class="${on && on.area === c.area ? 'on' : ''}"`
    + ` aria-pressed="${on && on.area === c.area}" title="${esc(c.name)}, capital of ${esc(c.nation ? c.nation.name : '?')}">`
    + `${shield(c.nation && c.nation.color)}<span>${esc(c.name)}${c.here ? '<span class="you"> · you</span>' : ''}`
    + `<small>${esc(c.nation ? c.nation.name : '')}</small></span></button>`).join(''));
  const h = cur();
  q('#maptitle').textContent = h ? h.name : 'World builder';
  setHTML(q('#mapnat'), h && h.nation ? natBadge(h.nation) : '');
}
function drawFnKey(h){
  // the legend IS the filter: one chip per kind present in this half, with
  // how many are placed; a click hides or shows that kind on the map
  const n = {}, m = {};
  for(const p of (h.npcs || [])) n[p.fn] = (n[p.fn] || 0) + 1;
  for(const r of (h.missing || [])) m[r.fn] = (m[r.fn] || 0) + 1;
  setHTML(q('#fnkey'), FNS.filter(f => n[f.key] || m[f.key]).map(f =>
    `<button data-fn="${f.key}" class="${HIDEFN.has(f.key) ? 'off' : ''}" aria-pressed="${!HIDEFN.has(f.key)}"`
    + ` title="${HIDEFN.has(f.key) ? 'Show' : 'Hide'} ${esc(f.label)} on the map${m[f.key] ? ' (' + m[f.key] + ' missing)' : ''}">`
    + `${fnDot(f.key)}${esc(f.label)} <b>${n[f.key] || 0}${m[f.key] ? '/' + ((n[f.key] || 0) + m[f.key]) : ''}</b></button>`).join(''));
}
function drawPeople(h){
  const f = PFILTER.trim().toLowerCase();
  const match = (name, fn) => !f || pretty(name).toLowerCase().includes(f)
    || String(name).toLowerCase().includes(f) || fnLabel(fn).toLowerCase().includes(f);
  const miss = (h.missing || []).filter(r => match(r.name, r.fn));
  const order = k => { const i = FNS.findIndex(o => o.key === k); return i < 0 ? 99 : i; };
  const placed = (h.npcs || []).filter(p => match(p.name, p.fn)).slice()
    .sort((a, b) => order(a.fn) - order(b.fn) || pretty(a.name).localeCompare(pretty(b.name)));
  const all = h.npcs || [];
  q('#pcount').textContent = `${all.length} placed` + ((h.missing || []).length ? ` · ${h.missing.length} roles missing` : '');
  let html = '';
  if(miss.length){
    html += `<div class="grp"><span>Missing roles</span><span>pick one, then the map</span></div>`;
    html += miss.map(r => `<button class="miss${ARMED === r.name ? ' armed' : ''}" data-arm="${esc(r.name)}"`
      + ` title="Place ${esc(pretty(r.name))}: click here, then click the floor on the map">`
      + `${fnDot(r.fn)}<span class="nm">${esc(pretty(r.name))}</span><span class="fl">${esc(fnLabel(r.fn))} · missing</span></button>`).join('');
  }
  if(placed.length){
    html += `<div class="grp"><span>Placed</span><span></span></div>`;
    html += placed.map(p => {
      const [px, py] = w2p(h, p.x, p.z);
      return `<button data-sel="${p.idx}" class="${p.idx === SELN ? 'on' : ''}">`
        + `${fnDot(p.fn)}<span class="nm">${esc(pretty(p.name))}</span>`
        + `<span class="fl">${p.home && !p.says ? '<span class="nl">no line</span> · ' : ''}`
        + `${esc(fnLabel(p.fn))} · ${gridOf(px, py)}</span></button>`;
    }).join('');
  }
  if(!html) html = `<p class="why" style="margin:0">${f ? 'Nobody here matches that.' : 'Nobody is placed in this half yet. Click empty floor on the map to place someone.'}</p>`;
  setHTML(q('#plist'), html);
  const ab = q('#armbar');
  ab.hidden = !ARMED;
  setHTML(ab, ARMED ? `<span>Click the floor on the map to place <b>${esc(pretty(ARMED))}</b>.</span><button data-disarm="1">Cancel</button>` : '');
}
function drawLive(h){
  const b = q('#livebar');
  b.className = 'livebar' + (h.live ? ' live' : '');
  b.textContent = h.live
    ? 'You are standing in this half - NPC changes show in game straight away.'
    : 'Nobody is in this half - changes are saved and show the next time someone enters.';
}
function drawTabs(s){
  const hs = halves(s);
  setHTML(q('#halftabs'), hs.length < 2 ? '' : hs.map((h, i) =>
    `<button data-half="${i}" class="${i === HALF ? 'on' : ''}">${halfName(h)}`
    + `${h.live ? ' · you' : ''}</button>`).join(''));
}
function sortedGates(h){
  return (h.gates || []).slice().sort((a, b) =>
    (a.grid || '').localeCompare(b.grid || '') || a.portal - b.portal);
}
function drawDoorList(s, h){
  const f = PFILTER.trim().toLowerCase().replace(/^#/, '');
  const gs = sortedGates(h).filter(g => !f || 'door'.includes(f) || String(g.portal).includes(f)
    || (g.dest_name || '').toLowerCase().includes(f) || (g.grid || '').toLowerCase() === f);
  q('#doorcount').textContent = gs.length ? gs.length : '';
  q('#doorsbox').hidden = f && !gs.length;
  setHTML(q('#doorlist'), gs.length ? gs.map(g => {
    const came = h.live && s.arrived_via === g.portal;
    const went = !h.live && s.arrived_via != null && g.dest === s.arrived_via;
    return `<button data-portal="${g.portal}" class="${g.portal === SELP ? 'on' : ''}">`
      + `<span class="g">${g.grid || ' - '}</span><span class="n">#${g.portal}</span>`
      + `<span class="to">to ${g.dest_name || '?'} #${g.dest}</span>`
      + (came ? '<span class="pill here">came out here</span>'
         : went ? '<span class="pill here">you came through</span>'
              : g.out_moved ? '<span class="pill moved">exit moved</span>' : '<span></span>')
      + `</button>`;
  }).join('') : (f ? '' : '<p class="why" style="margin:0">No shipped doors in this half.</p>'));
}
function drawDetail(s, h){
  // A door is an ENTRANCE here and an EXIT on the other half. The exit is
  // shown and edited on the other half's own map, zoomed, because the two
  // halves are different pictures -- on most capitals they do not line up,
  // so a landing placed against this half's art is placed wrong.
  const g = selGate(), box = q('#doordetail');
  if(!g){ box.hidden = true; setHTML(box, ''); return; }
  box.hidden = false;
  const hs = halves(s);
  const h2 = hs.find(o => o.area === g.dest_area);
  const there = `${g.dest_name || '?'}${g.dest_area != null ? ' · ' + g.dest_area : ''}${h2 && h2.inner ? ' inner' : ''}`;
  const canLand = !!(h2 && h2.live && s.in_field && s.pos);
  const why = !h2 ? '' : !h2.live ? `Land here needs you standing in ${there}: walk through this door, then stand where it should put you.`
            : !s.pos ? 'Take one step in game first.' : '';
  setHTML(box,
    `<div class="t">Door #${g.portal} · ${g.grid || ''} · ${halfName(h)}</div>`
    + `<dl><dt>goes to</dt><dd>${there}, beside door #${g.dest}</dd>`
    + `<dt>puts you</dt><dd>${g.out_ax.toFixed(1)}, ${g.out_az.toFixed(1)} `
    + `<span class="pill ${g.out_moved ? 'moved' : ''}">${g.out_moved ? 'moved' : "SE's"}</span></dd></dl>`
    + (h2 ? `<div class="exitwrap"><canvas id="exitcv" width="320" height="320"></canvas></div>`
          + `<div class="why">That is <b>${there}</b>'s own map, zoomed in on where this door puts you. `
          + `Click or drag to move it; the tint is floor.</div>` : '')
    + `<div class="acts">`
    + `<button class="go" data-cmd="!arrive ${g.portal} here" ${canLand ? '' : 'disabled'}>Land here - where I'm standing</button>`
    + (g.out_moved ? `<button class="warn" data-edit='${JSON.stringify({op:"arrive_drop", portal: g.portal})}'>Reset to SE's</button>` : '')
    + `</div>`
    + (why ? `<div class="why">${why}</div>` : '')
    + (h2 ? `<div class="why"><button class="linkish" data-goto-half="${hs.indexOf(h2)}" data-goto-portal="${g.dest}">Open ${there} →</button></div>` : ''));
  drawExit(s, h2, g);
}
const EXIT_W = 40;          // map pixels across the mini-map: 8x the main map's 256
function exitWindow(h2, g){
  const e = (EXITPRESS && EXITPRESS.moved) ? [EXITPRESS.mx, EXITPRESS.my]
                                          : w2p(h2, g.out_ax, g.out_az);
  const c = w2p(h2, g.out_ax, g.out_az);   // the window follows the STORED spot
  const x0 = Math.max(0, Math.min(256 - EXIT_W, c[0] - EXIT_W / 2));
  const y0 = Math.max(0, Math.min(256 - EXIT_W, c[1] - EXIT_W / 2));
  return {x0, y0, e};
}
function drawExit(s, h2, g){
  const cv = document.getElementById('exitcv');
  if(!cv || !h2) return;
  const x = cv.getContext('2d'), K = cv.width / EXIT_W;
  const {x0, y0, e} = exitWindow(h2, g);
  x.clearRect(0, 0, cv.width, cv.height);
  x.imageSmoothingEnabled = false;
  const im = art(h2.minimap);
  if(im.complete && im.naturalWidth) x.drawImage(im, x0, y0, EXIT_W, EXIT_W, 0, 0, cv.width, cv.height);
  loadFloor(h2);
  const fl = FLOOR[h2.area];
  if(q('#shFloor').checked && fl && fl.cv && fl.key === projKey(h2))
    x.drawImage(fl.cv, x0, y0, EXIT_W, EXIT_W, 0, 0, cv.width, cv.height);
  const P = (mx, my) => [(mx - x0) * K, (my - y0) * K];
  // the door you come out beside, on that map
  const beside = (h2.gates || []).find(d => d.portal === g.dest);
  if(beside){
    const [bx, by] = P(...w2p(h2, beside.x, beside.z));
    x.beginPath(); x.arc(bx, by, 10, 0, 6.2832); x.fillStyle = '#fde604'; x.fill();
    x.lineWidth = 3; x.strokeStyle = '#000'; x.stroke();
    x.font = '600 13px ui-monospace,Menlo,Consolas,monospace'; x.fillStyle = '#fde604';
    x.fillText('#' + beside.portal, bx + 13, by - 9);
  }
  if(s.pos && h2.live){
    const [ux, uy] = P(...w2p(h2, s.pos[0], s.pos[2]));
    x.beginPath(); x.arc(ux, uy, 9, 0, 6.2832); x.strokeStyle = '#ff3b30'; x.lineWidth = 3; x.stroke();
  }
  const [ex, ey] = P(e[0], e[1]);
  x.beginPath(); x.moveTo(ex-14, ey); x.lineTo(ex+14, ey); x.moveTo(ex, ey-14); x.lineTo(ex, ey+14);
  x.strokeStyle = 'rgba(0,0,0,.85)'; x.lineWidth = 8; x.stroke();
  x.strokeStyle = SELCOL; x.lineWidth = 4; x.stroke();
}
function drawNpcDetail(s, h){
  const n = selNpc(), box = q('#npcdetail');
  if(!n){ box.hidden = true; setHTML(box, ''); return; }
  box.hidden = false;
  const [px, py] = w2p(h, n.x, n.z);
  const yaw = PRESS && PRESS.kind === 'rot' ? PRESS.yaw : n.yaw;
  const ed = (o) => `data-edit='${JSON.stringify(Object.assign({area: h.area, idx: n.idx}, o))}'`;
  const turn = (d) => `<button ${ed({op:'turn', yaw: ((n.yaw + d) % 360 + 360) % 360})}>${d > 0 ? '⟳' : '⟲'} ${Math.abs(d)}°</button>`;
  const face = (y, l) => `<button ${ed({op:'turn', yaw: y})}>${l}</button>`;
  setHTML(box,
    `<div class="t">${fnDot(n.fn)}<span>${esc(pretty(n.name))} · ${gridOf(px, py)}</span></div>`
    + (n.says ? `<div class="says">“${esc(n.says)}”</div>` : '')
    + `<dl><dt>kind</dt><dd>${esc(fnLabel(n.fn))}${n.fn_detail ? ' · ' + esc(n.fn_detail) : ''}</dd>`
    + `${n.home && n.home !== h.district ? `<dt>lives in</dt><dd>${esc(n.home)}</dd>` : ''}<dt>model</dt><dd>${n.model ?? '?'}${n.script ? ' · script ' + n.script : ''} · row ${n.idx}</dd>`
    + `<dt>at</dt><dd>${n.x.toFixed(1)}, ${n.z.toFixed(1)} · floor ${n.y.toFixed(1)}</dd>`
    + `<dt>facing</dt><dd>${yaw.toFixed(0)}° · ${compass(yaw)}</dd></dl>`
    + `<div class="acts"><select id="npcrole" aria-label="change this NPC's type">`
    + (h.roles || []).map(r => `<option value="${esc(r)}"${r === n.name ? ' selected' : ''}>${esc(pretty(r))}</option>`).join('')
    + `</select><button id="npcretype">Change type</button></div>`
    + `<div class="acts">${turn(-45)}${turn(-15)}${turn(15)}${turn(45)}</div>`
    + `<div class="acts">${face(0,'N')}${face(90,'E')}${face(180,'S')}${face(270,'W')}`
    + `<button class="warn" ${ed({op:'remove'})} data-confirm="Remove ${esc(pretty(n.name))}? It goes back on the missing list.">Remove</button></div>`
    + `<div class="why">Drag the NPC to move it; drag the orange handle to turn it. `
    + `Arrow keys nudge it (Shift = further), Q / E turn it. The mouse wheel zooms the map.</div>`);
}
function drawCalBox(h){
  const box = q('#calbox');
  if(!CAL){ box.hidden = true; return; }
  box.hidden = false;
  const p = h.proj, n = p.anchors || 0;
  const worst = (h.cal && h.cal.length) ? h.cal[0].d : null;
  setHTML(box, `<b>Aligning ${p.stem || h.minimap}</b><br>`
    + (n ? `${n} anchor${n > 1 ? 's' : ''} · x: ${p.how_x}<br>z: ${p.how_z}`
         + (worst !== null ? `<br>worst anchor off by ${worst.toFixed(1)} px` : '')
       : 'no anchors - using the shipped fit')
    + `<br><br><b>Easiest:</b> stand somewhere you recognise in game, then click `
    + `the spot on this map where you actually are.<br>Or drag a yellow door onto `
    + `the door as it is painted.`);
}

// ---- the canvas ------------------------------------------------------------
function drawMap(s){
  const card = q('#mapcard');
  const hs = halves(s);
  if(!hs.length){
    // Say WHY there is no map instead of showing an empty card.
    const none = !s.in_field || !s.area;
    card.hidden = !none;
    q('#mapnone').hidden = !none;
    q('#maprow').hidden = true;
    setHTML(q('#halftabs'), '');
    q('#mapsub').textContent = ' - ';
    MAPST = null;
    return;
  }
  q('#mapnone').hidden = true; q('#maprow').hidden = false; card.hidden = false;
  MAPST = s;
  if(WANTAREA != null){
    const wi = hs.findIndex(o => o.area === WANTAREA);
    if(wi >= 0) HALF = wi;
    WANTAREA = null;
  }
  if(HALF >= hs.length) HALF = 0;
  // KEY: THE DOOR YOU JUST CAME OUT OF, selected for you -- once per arrival, so
  // it never fights a choice you made since.
  // Selected is the door you walked THROUGH (the partner of the one you came
  // out beside): its panel shows where it put you, on the map you are in,
  // with Land here enabled -- which is the whole fix-a-landing workflow.
  if(s.arrived_via != null && s.arrived_via !== SEENVIA){
    SEENVIA = s.arrived_via;
    const beside = hs.flatMap(o => o.gates || []).find(g => g.portal === s.arrived_via);
    const through = beside && beside.dest;
    const ti = hs.findIndex(o => (o.gates || []).some(g => g.portal === through));
    if(ti >= 0){ HALF = ti; SELP = through; SELN = null; }
  }
  const h = hs[HALF];
  if(s.functions) FNS = s.functions;
  if(PENDSEL){
    // the role just placed from the list: select the newest row with its name
    const mine = (h.npcs || []).filter(n => n.name === PENDSEL);
    if(mine.length){ SELN = mine[mine.length - 1].idx; SELP = null; PENDSEL = null; }
  }
  if(ARMED && !(h.missing || []).some(r => r.name === ARMED)
     && !(h.roles || []).includes(ARMED)) ARMED = null;
  if(SELP !== null && !(h.gates || []).some(g => g.portal === SELP)) SELP = null;
  if(SELN !== null && !(h.npcs || []).some(n => n.idx === SELN)) SELN = null;
  drawPicker(s); drawTabs(s); drawLive(h); drawTownsfolk(h); drawDoorList(s, h); drawDetail(s, h);
  drawNpcDetail(s, h); drawCalBox(h); drawFnKey(h); drawPeople(h);

  const img = q('#mapimg');
  if(img.dataset.name !== h.minimap){ img.dataset.name = h.minimap; img.src = qs('/map/' + h.minimap + '.png'); }
  // a new half or capital opens at the whole map
  if(VIEWAREA !== h.area){ VIEWAREA = h.area; VZ = 1; VX0 = 0; VY0 = 0; }
  clampView();
  img.style.transform = VZ === 1 ? '' :
    `scale(${VZ}) translate(${-VX0 / ART * 100}%, ${-VY0 / ART * 100}%)`;
  const cv = q('#mapcv'), g = cv.getContext('2d');
  const S = cv.width / h.proj.size;               // 1024 / 256 = 4
  // art pixel -> canvas pixel, through the view
  const T = (px, py) => [(px - VX0) * S * VZ, (py - VY0) * S * VZ];
  g.clearRect(0, 0, cv.width, cv.height);
  const anc = h.proj.anchors || 0;
  q('#mapsub').textContent = `${h.minimap} · ${(h.gates||[]).length} doors · `
    + `${(h.npcs||[]).length} placed · ` + (anc ? `aligned from ${anc} anchors` : 'shipped alignment');

  // the floor: where the collision has something to stand on
  for(const o of hs) loadFloor(o);
  const fl = FLOOR[h.area];
  if(q('#shFloor').checked && fl && fl.cv && fl.key === projKey(h)){
    g.imageSmoothingEnabled = false;
    const k = fl.size / ART;
    g.drawImage(fl.cv, VX0 * k, VY0 * k, ART / VZ * k, ART / VZ * k, 0, 0, cv.width, cv.height);
  }
  // shops: at the pixel they were MEASURED at, never re-projected
  const shopLayers = [h].concat(q('#shBoth').checked ? hs.filter(o => o !== h) : []);
  if(q('#shIcons').checked) for(const lay of shopLayers) for(const i of (lay.icons || [])){
    if(i.kind === 'door') continue;
    g.globalAlpha = (i.trust === 'noisy' ? 0.35 : 1) * (lay === h ? 1 : 0.4);
    drawShape(g, i.kind, ...T(i.px, i.py), CLASSCOL[i.colour] || '#bbb');
  }
  g.globalAlpha = 1;
  // MISSING shopkeepers, faint, at their painted spot: a click places one
  if(q('#shGhost').checked && !CAL) for(const gh of (h.ghosts || [])){
    if(HIDEFN.has(gh.fn) || gh.name === PICKNAME) continue;
    const [X, Y] = T(...ghostPix(h, gh)), r = 12;
    g.beginPath(); g.moveTo(X, Y - r); g.lineTo(X + r, Y); g.lineTo(X, Y + r); g.lineTo(X - r, Y); g.closePath();
    g.globalAlpha = 0.4; g.fillStyle = FNCOL[gh.fn] || '#bbb'; g.fill();
    g.globalAlpha = 0.8; g.lineWidth = 5; g.strokeStyle = 'rgba(0,0,0,.8)'; g.stroke();
    g.globalAlpha = 1; g.setLineDash([4, 3]); g.lineWidth = 2.5;
    g.strokeStyle = gh.y == null ? '#ff3b30' : (FNCOL[gh.fn] || '#fff'); g.stroke(); g.setLineDash([]);
    // a "+" in the middle: this one is not there yet
    g.beginPath(); g.moveTo(X - 4, Y); g.lineTo(X + 4, Y); g.moveTo(X, Y - 4); g.lineTo(X, Y + 4);
    g.lineWidth = 2; g.strokeStyle = '#fff'; g.stroke();
  }
  if(PICK && PICKNAME){
    // the spot a ghost click chose, waiting for "place"
    const [X, Y] = T(...w2p(h, PICK[0], PICK[1]));
    g.beginPath(); g.arc(X, Y, 13, 0, 6.2832); g.lineWidth = 3; g.strokeStyle = SELCOL; g.stroke();
  }
  // placed NPCs, thin, under the doors
  if(q('#shNpcs').checked) (h.npcs || []).forEach((n, k) => {
    const dragging = PRESS && PRESS.moved && PRESS.kind === 'npc' && PRESS.i === k;
    const [px, py] = dragging ? [PRESS.px, PRESS.py] : w2p(h, n.x, n.z);
    const isSel = n.idx === SELN;
    const yaw = isSel && PRESS && PRESS.kind === 'rot' ? PRESS.yaw : n.yaw;
    // WARNING: 0 = +Z = NORTH = UP on this map. The line used to go DOWN for north
    // (Y + cos), so every NPC's facing was drawn mirrored north-to-south.
    if(HIDEFN.has(n.fn) && !isSel) return;
    const [X, Y] = T(px, py), a = yaw * Math.PI / 180;
    const fc = FNCOL[n.fn] || '#bbb', ring = RINGFN.has(n.fn);
    // facing line first, so the marker sits on top of it
    const L = isSel ? ROT_R * S : (ring ? 13 : 16);
    g.beginPath(); g.moveTo(X, Y); g.lineTo(X + Math.sin(a) * L, Y - Math.cos(a) * L);
    g.strokeStyle = 'rgba(0,0,0,.7)'; g.lineWidth = isSel ? 6 : 4; g.stroke();
    g.strokeStyle = isSel ? SELCOL : fc; g.lineWidth = isSel ? 3 : 2; g.stroke();
    if(ring){
      // townsfolk (ambient) and unknown rows: a ring, as they always were
      g.beginPath(); g.arc(X, Y, 7, 0, 6.2832);
      g.lineWidth = 4; g.strokeStyle = 'rgba(0,0,0,.6)'; g.stroke();
      g.setLineDash(n.fn === 'unknown' ? [4, 3] : []);
      g.lineWidth = 2; g.strokeStyle = fc; g.stroke(); g.setLineDash([]);
    } else {
      // a function NPC: a filled diamond in its function's colour
      const r = 10;
      g.beginPath(); g.moveTo(X, Y - r); g.lineTo(X + r, Y); g.lineTo(X, Y + r); g.lineTo(X - r, Y); g.closePath();
      g.fillStyle = fc; g.fill(); g.lineWidth = 2.5; g.strokeStyle = 'rgba(0,0,0,.85)'; g.stroke();
    }
    if(isSel){
      g.beginPath(); g.arc(X, Y, 15, 0, 6.2832);
      g.lineWidth = 6; g.strokeStyle = 'rgba(0,0,0,.75)'; g.stroke();
      g.lineWidth = 3; g.strokeStyle = SELCOL; g.stroke();
      g.beginPath(); g.arc(X + Math.sin(a) * L, Y - Math.cos(a) * L, 7, 0, 6.2832);
      g.fillStyle = SELCOL; g.fill(); g.lineWidth = 2; g.strokeStyle = '#000'; g.stroke();
      tag(g, pretty(n.name), X + 20, Y + 20, SELCOL);
    }
  });
  // every arrival, only if asked for -- normally just the selected door's
  const sel = selGate();
  if(q('#shArr').checked && !CAL) for(const d of (h.gates || [])){
    if(d === sel) continue;
    const [AX, AY] = T(...w2p(h, d.ax, d.az));
    g.beginPath(); g.moveTo(AX-5, AY); g.lineTo(AX+5, AY);
    g.moveTo(AX, AY-5); g.lineTo(AX, AY+5);
    g.strokeStyle = 'rgba(253,230,4,.6)'; g.lineWidth = 2; g.stroke();
  }
  // doors: plain dots; only the SELECTED one gets a ring, a label and its
  // arrival. Labelling all of them is what made the E5 gate unreadable.
  if(q('#shGates').checked) (h.gates || []).forEach((d, k) => {
    const dragging = CAL && PRESS && PRESS.moved && PRESS.kind === 'gate' && PRESS.i === k;
    const [px, py] = dragging ? [PRESS.px, PRESS.py] : w2p(h, d.x, d.z);
    const [DX, DY] = T(px, py);
    drawShape(g, 'door', DX, DY, d === sel ? SELCOL : CLASSCOL.yellow);
    if(CAL){ g.beginPath(); g.arc(DX, DY, 14, 0, 6.2832);
             g.strokeStyle = 'rgba(253,230,4,.6)'; g.lineWidth = 2; g.stroke(); }
  });
  if(sel && !CAL){
    // Only the doorway is on THIS map. Where it puts you is on the OTHER
    // half's map, and is drawn there (the panel's mini-map): the two halves
    // do not share one picture, so a landing drawn here was being placed
    // against the wrong art.
    const [GX, GY] = T(...w2p(h, sel.x, sel.z));
    g.beginPath(); g.arc(GX, GY, 16, 0, 6.2832); g.strokeStyle = SELCOL; g.lineWidth = 3; g.stroke();
    tag(g, '#' + sel.portal + ' \u2192 ' + (sel.dest_area ?? '?'), GX + 20, GY - 18, SELCOL);
  }
  if(q('#shYou').checked && s.pos && h.live){
    const [YX, YY] = T(...w2p(h, s.pos[0], s.pos[2]));
    g.beginPath(); g.arc(YX, YY, 12, 0, 6.2832); g.strokeStyle = '#ff3b30'; g.lineWidth = 4; g.stroke();
    g.beginPath(); g.arc(YX, YY, 4, 0, 6.2832); g.fillStyle = '#ff3b30'; g.fill();
  }
}

// ---- what is under the pointer --------------------------------------------
// In MAP pixels (256 px art). Order matters: the selected door's arrival
// cross wins over everything, then NPCs, then doors.
function hit(px, py){
  const h = cur(); if(!h) return null;
  const near = (x, z, r) => { const [a, b] = w2p(h, x, z); return Math.hypot(a - px, b - py) <= r; };
  if(CAL){
    let best = null, bd = ZR(4.5);
    (h.gates || []).forEach((d, i) => {
      const [a, b] = w2p(h, d.x, d.z); const dd = Math.hypot(a - px, b - py);
      if(dd < bd){ bd = dd; best = {kind: 'gate', i}; }
    });
    return best;
  }
  const sn = selNpc();
  if(sn){
    const [hx, hy] = rotHandle(h, sn, sn.yaw);
    if(Math.hypot(hx - px, hy - py) <= ZR(3)) return {kind: 'rot', idx: sn.idx, yaw: sn.yaw};
  }
  const sel = selGate();
  if(q('#shNpcs').checked){
    // the NEAREST within 3 px, not the first in row order: with the
    // townsfolk out, neighbours stand ~3 px apart and the first match was
    // often the wrong person
    let i = -1, bd = ZR(3);
    (h.npcs || []).forEach((n, k) => {
      if(HIDEFN.has(n.fn) && n.idx !== SELN) return;
      const [a, b] = w2p(h, n.x, n.z), d = Math.hypot(a - px, b - py);
      if(d <= bd){ bd = d; i = k; }
    });
    if(i >= 0) return {kind: 'npc', i};
  }
  if(q('#shGates').checked){
    let best = null, bd = ZR(3.5);
    for(const d of (h.gates || [])){
      const [a, b] = w2p(h, d.x, d.z); const dd = Math.hypot(a - px, b - py);
      if(dd < bd){ bd = dd; best = {kind: 'door', portal: d.portal}; }
    }
    if(best) return best;
  }
  if(q('#shGhost').checked){
    let best = null, bd = ZR(3);
    for(const gh of (h.ghosts || [])){
      if(HIDEFN.has(gh.fn)) continue;
      const [a, b] = ghostPix(h, gh), dd = Math.hypot(a - px, b - py);
      if(dd <= bd){ bd = dd; best = {kind: 'ghost', name: gh.name}; }
    }
    if(best) return best;
  }
  return null;
}
function doorsNear(px, py){
  const h = cur(); if(!h) return [];
  return (h.gates || []).filter(d => { const [a, b] = w2p(h, d.x, d.z);
    return Math.hypot(a - px, b - py) <= ZR(3.5); });
}

q('#mapcv').addEventListener('mousedown', e => {
  if(!MAPST || e.button !== 0) return;
  const [px, py] = evPix(e);
  PRESS = Object.assign({px, py, px0: px, py0: py, moved: false,
                         cx0: e.clientX, cy0: e.clientY, vx0: VX0, vy0: VY0},
                        hit(px, py) || {kind: 'none'});
  e.preventDefault();
});
window.addEventListener('mousemove', e => {
  if(!PRESS) return;
  // drag on EMPTY map, zoomed in: pan the view (a still click stays a click)
  if(PRESS.kind === 'none' && VZ > 1){
    const r = q('#mapcv').getBoundingClientRect();
    const dx = e.clientX - PRESS.cx0, dy = e.clientY - PRESS.cy0;
    if(PRESS.panned || Math.hypot(dx, dy) > 4){
      PRESS.panned = true;
      q('.mapwrap').classList.add('panning');
      VX0 = PRESS.vx0 - dx / r.width * ART / VZ;
      VY0 = PRESS.vy0 - dy / r.height * ART / VZ;
      clampView(); drawMap(MAPST);
      return;
    }
  }
  const [px, py] = evPix(e);
  PRESS.px = px; PRESS.py = py;
  const draggable = PRESS.kind === 'arr' || PRESS.kind === 'gate'
                    || PRESS.kind === 'npc' || PRESS.kind === 'rot';
  if(draggable && Math.hypot(px - PRESS.px0, py - PRESS.py0) > ZR(1.2)) PRESS.moved = true;
  if(PRESS.moved && PRESS.kind === 'rot'){
    // yaw from the NPC to the pointer; 0 = north (up), 90 = east (right)
    const n = selNpc(), [nx, ny] = w2p(cur(), n.x, n.z);
    const deg = Math.atan2(px - nx, -(py - ny)) * 180 / Math.PI;
    PRESS.yaw = Math.round(((deg % 360) + 360) % 360 / 5) * 5 % 360;
    q('#mapread').textContent = `turning ${n.name} to ${PRESS.yaw}° · ${compass(PRESS.yaw)}`;
    drawMap(MAPST);
    return;
  }
  if(PRESS.moved){
    const [x, z] = p2w(cur(), px, py);
    q('#mapread').textContent = (PRESS.kind === 'gate' ? 'aligning to ' : 'moving to ')
      + `${x.toFixed(0)}, ${z.toFixed(0)}`;
    drawMap(MAPST);
  }
});
window.addEventListener('mouseup', e => {
  if(!PRESS) return;
  const p = PRESS; PRESS = null;
  q('.mapwrap').classList.remove('panning');
  const h = cur(); if(!h || !MAPST) return;
  if(p.panned) return;          // that was a pan, not a click
  if(p.moved){
    q('#mapread').textContent = 'sent - the map updates when the server confirms';
    const [x, z] = p2w(h, p.px, p.py);
    // keyed by the door you come THROUGH to land here -- this door's partner
    if(p.kind === 'arr') edit({op:'arrive', portal: p.key, x: +x.toFixed(1), z: +z.toFixed(1)});
    else if(p.kind === 'npc') edit({op:'move', area: h.area, idx: h.npcs[p.i].idx,
                                    x: +x.toFixed(1), z: +z.toFixed(1)});
    else if(p.kind === 'rot') edit({op:'turn', area: h.area, idx: p.idx, yaw: p.yaw});
    else if(p.kind === 'gate'){
      const g0 = h.gates[p.i];
      send(`!mapcal ${h.minimap} ${g0.x.toFixed(1)}:${g0.z.toFixed(1)} `
           + `${p.px.toFixed(1)}:${p.py.toFixed(1)} door ${g0.portal}`);
    }
    drawMap(MAPST);
    return;
  }
  // a CLICK
  if(e.target !== q('#mapcv')) return;
  if(CAL){
    if(p.kind === 'gate') return;
    if(!MAPST.pos || !MAPST.in_field || !h.live){
      q('#mapread').textContent = 'stand in this half and take one step first'; return;
    }
    const drawn = w2p(h, MAPST.pos[0], MAPST.pos[2]);
    if(!confirm(`Anchor this map using where you are standing?\n\n`
        + `You are at world ${MAPST.pos[0].toFixed(1)}, ${MAPST.pos[2].toFixed(1)}.\n`
        + `The map draws that at ${drawn[0].toFixed(1)}, ${drawn[1].toFixed(1)}; you clicked `
        + `${p.px.toFixed(1)}, ${p.py.toFixed(1)}.`)) return;
    send(`!mapcal here ${p.px.toFixed(1)}:${p.py.toFixed(1)}`);
    return;
  }
  if(p.kind === 'ghost'){
    // a missing shopkeeper's painted spot: the placer opens there, with
    // that role chosen -- or, with no floor near the icon, the role is armed
    // for a click on the floor
    const gh = (h.ghosts || []).find(o => o.name === p.name);
    SELN = null; SELP = null;
    if(!gh) return;
    if(gh.y == null){
      ARMED = gh.name; PICK = null; PICKNAME = null; q('#placer').hidden = true;
      const m = q('#editmsg'); m.hidden = false; m.className = 'editmsg bad';
      m.textContent = `${pretty(gh.name)}'s icon has no floor near it. Click the floor where they should stand.`;
      drawMap(MAPST); return;
    }
    openPlacer(h, gh.x, gh.z, ...w2p(h, gh.x, gh.z), gh.name);
    return;
  }
  if(p.kind === 'door' || p.kind === 'arr'){
    SELP = p.portal; SELN = null; q('#placer').hidden = true; PICK = null; PICKNAME = null;
    drawMap(MAPST); return;
  }
  if(p.kind === 'npc' || p.kind === 'rot'){
    SELN = h.npcs[p.i] ? h.npcs[p.i].idx : p.idx; SELP = null;
    q('#placer').hidden = true; PICK = null; PICKNAME = null; drawMap(MAPST); return;
  }
  // empty map: offer to place something here -- any half, no session needed;
  // the server takes the height from the floor and refuses a spot with none
  SELN = null; SELP = null;
  if(floorAt(h, p.px, p.py) === false){
    // KEY: SAY IT HERE. This used to open the placer, send the edit, and get a
    // refusal back in a message line under the map -- which read as "it just
    // didn't place, kind of random".
    const [wx0, wz0] = p2w(h, p.px, p.py);
    const m = q('#editmsg'); m.hidden = false; m.className = 'editmsg bad';
    m.textContent = `No floor at ${wx0.toFixed(1)}, ${wz0.toFixed(1)} - nothing to stand on `
      + 'there in the game. The blue tint shows where there is.';
    q('#placer').hidden = true; PICK = null; PICKNAME = null; drawMap(MAPST); return;
  }
  const [x, z] = p2w(h, p.px, p.py);
  if(ARMED){
    // a missing role picked from the list: one click puts it here
    const role = ARMED; ARMED = null; PENDSEL = role;
    edit({op:'place', area: h.area, x: +x.toFixed(1), z: +z.toFixed(1), role,
          yaw: +q('#placeyaw').value});
    q('#placer').hidden = true; PICK = null; PICKNAME = null; drawMap(MAPST); return;
  }
  openPlacer(h, x, z, p.px, p.py, null);
});
function openPlacer(h, x, z, ppx, ppy, want){
  const p = {px: ppx, py: ppy};
  PICK = [x, z]; PICKNAME = want; SELP = null; SELN = null;
  q('#placeat').textContent = (want ? `place ${pretty(want)} on its icon at ` : 'place at ')
    + `${x.toFixed(0)}, ${z.toFixed(0)} (${gridOf(p.px, p.py)})`;
  const missr = h.missing || MAPST.missing || [];
  const miss = missr.map(m => m.name);
  const fnOf = n => { const r = missr.find(m => m.name === n) || (h.npcs || []).find(m => m.name === n)
                      || (h.townsfolk || []).find(m => m.name === n);
                      return r && r.fn ? ' · ' + fnLabel(r.fn) : ''; };
  const done = (h.npcs || []).map(n => n.name).filter(n => !miss.includes(n));
  let best = '', bd = 7;
  for(const i of (h.icons || [])) if(i.role){
    const d = Math.hypot(i.px - p.px, i.py - p.py);
    if(d < bd){ bd = d; best = i.role; }
  }
  const tfl = (h.townsfolk || []).filter(t => !t.placed).map(t => t.name);
  q('#placerole').innerHTML =
    `<optgroup label="Shops and staff">`
    + miss.map(n => `<option value="${esc(n)}">${esc(pretty(n))}${fnOf(n)}</option>`).join('')
    + done.map(n => `<option value="${esc(n)}">${esc(pretty(n))}${fnOf(n)} (already placed)</option>`).join('')
    + `</optgroup>`
    + (tfl.length ? `<optgroup label="Townsfolk of ${h.district || 'this half'}">`
       + tfl.map(n => `<option value="${esc(n)}">${esc(pretty(n))}${fnOf(n)}</option>`).join('') + `</optgroup>` : '');
  if(best) q('#placerole').value = best;
  if(want) q('#placerole').value = want;
  q('#placer').hidden = false;
  drawMap(MAPST);
}
q('#mapcv').addEventListener('wheel', e => {
  if(!MAPST) return;
  e.preventDefault();
  const r = q('#mapcv').getBoundingClientRect();
  zoomAt(VZ * (e.deltaY < 0 ? 1.25 : 0.8),
         (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
}, {passive: false});
// the buttons zoom about the SELECTED NPC or door if there is one, else the middle
function zoomFocus(){
  const h = cur(), n = selNpc(), d = selGate();
  const o = n || d;
  if(!h || !o) return [0.5, 0.5];
  const [px, py] = w2p(h, o.x, o.z);
  return [Math.min(1, Math.max(0, (px - VX0) * VZ / ART)),
          Math.min(1, Math.max(0, (py - VY0) * VZ / ART))];
}
q('#zin').addEventListener('click', () => zoomAt(VZ * 2, ...zoomFocus()));
q('#zout').addEventListener('click', () => zoomAt(VZ / 2, ...zoomFocus()));
q('#zfit').addEventListener('click', () => { VZ = 1; VX0 = VY0 = 0; clampView(); if(MAPST) drawMap(MAPST); });
// ARROWS nudge the selected NPC half a world unit (Shift: 2), Q / E turn it 15
// degrees -- for the last bit of precision a mouse cannot give
window.addEventListener('keydown', e => {
  if(e.key === 'Escape'){
    // Esc: leave the search box, then drop arming, the placer and the selection
    if(e.target.closest('input,select,textarea')){ e.target.blur(); return; }
    if(ARMED || PICK || SELN !== null || SELP !== null){
      ARMED = null; PICK = null; SELN = null; SELP = null; q('#placer').hidden = true;
      if(MAPST) drawMap(MAPST);
    }
    return;
  }
  if(!MAPST || e.target.closest('input,select,textarea')) return;
  if(e.key === '/'){ e.preventDefault(); q('#psearch').focus(); return; }
  if((e.ctrlKey || e.metaKey) && !e.shiftKey && (e.key === 'z' || e.key === 'Z')){
    e.preventDefault(); undo(); return;
  }
  if(e.ctrlKey || e.metaKey || e.altKey) return;
  // 1-5: the capitals, in the picker's order; H: the other half
  if(/^[1-5]$/.test(e.key)){
    const b = document.querySelectorAll('#capbtns button[data-cap]')[+e.key - 1];
    if(b){ e.preventDefault(); b.click(); }
    return;
  }
  if(e.key === 'h' || e.key === 'H'){
    const hs = halves(MAPST);
    if(hs.length > 1){
      e.preventDefault();
      const b = q(`#halftabs button[data-half="${(HALF + 1) % hs.length}"]`);
      if(b) b.click();
    }
    return;
  }
  const n = selNpc(), h = cur();
  if(!n || !h) return;
  const step = e.shiftKey ? 2 : 0.5;
  const mv = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1]}[e.key];
  if(mv){
    // up on the map is +Z when the map's z axis runs upward (az < 0)
    const sx = Math.sign(h.proj.ax) || 1, sz = -(Math.sign(h.proj.az) || 1);
    const x = n.x + mv[0] * step * sx, z = n.z + mv[1] * step * sz;
    e.preventDefault();
    edit({op: 'move', area: h.area, idx: n.idx, x: +x.toFixed(1), z: +z.toFixed(1)});
    return;
  }
  const turn = {q: -15, Q: -15, e: 15, E: 15}[e.key];
  if(turn){
    e.preventDefault();
    edit({op: 'turn', area: h.area, idx: n.idx, yaw: ((n.yaw + turn) % 360 + 360) % 360});
  }
});
function gridOf(px, py){
  const c = Math.min(8, Math.max(1, Math.round((px - 6.5) / 32) + 1));
  const r = Math.min(7, Math.max(0, Math.round((py - 25) / 32)));
  return 'ABCDEFGH'[r] + c;
}
q('#mapcv').addEventListener('mousemove', e => {
  if(!MAPST || PRESS) return;
  const h = cur(); if(!h) return;
  const [px, py] = evPix(e);
  const [x, z] = p2w(h, px, py);
  const here = doorsNear(px, py);
  let what = '';
  if(here.length === 1) what = `  door #${here[0].portal} → ${here[0].dest_name} #${here[0].dest}`;
  else if(here.length > 1) what = `  doors ${here.map(d => '#' + d.portal).join(' ')} - pick one from the list`;
  else {
    const nn = (h.npcs || []).find(n => { if(HIDEFN.has(n.fn)) return false;
                                          const [a, b] = w2p(h, n.x, n.z);
                                          return Math.hypot(a - px, b - py) <= ZR(3); });
    if(nn) what = `  ${pretty(nn.name)} (${fnLabel(nn.fn)}) · facing ${compass(nn.yaw)}`;
    else {
      const t0 = hit(px, py);
      if(t0 && t0.kind === 'ghost') what = `  ${pretty(t0.name)} is missing: click to place on its icon`;
      else for(const i of (h.icons || [])) if(i.role && Math.hypot(i.px - px, i.py - py) < 6){ what = '  ' + i.role; break; }
    }
  }
  const t = hit(px, py);
  q('#mapcv').style.cursor = t ? (t.kind === 'door' ? 'pointer' : 'grab') : (CAL ? 'crosshair' : 'cell');
  q('#mapread').textContent = `${gridOf(px, py)} · ${x.toFixed(0)}, ${z.toFixed(0)}${what}`;
});
q('#mapcv').addEventListener('mouseleave', () => { if(!PRESS) q('#mapread').textContent = ' - '; });

for(const id of ['shIcons','shNpcs','shGhost','shGates','shArr','shYou','shBoth','shFloor']){
  if(typeof PREFS.layers[id] === 'boolean') q('#' + id).checked = PREFS.layers[id];
  q('#' + id).addEventListener('change', e => {
    PREFS.layers[id] = e.target.checked; savePrefs();
    if(MAPST) drawMap(MAPST);
  });
}
// the capital buttons drive the (hidden) select, so there is one code path
q('#undobtn').addEventListener('click', undo);
q('#capbtns').addEventListener('click', e => {
  const b = e.target.closest('button[data-cap]'); if(!b) return;
  const sel = q('#capsel');
  if(sel.value === b.dataset.cap && FOCUS === b.dataset.cap) return;
  sel.value = b.dataset.cap; sel.dispatchEvent(new Event('change'));
});
q('#fnkey').addEventListener('click', e => {
  const b = e.target.closest('button[data-fn]'); if(!b) return;
  const k = b.dataset.fn;
  if(HIDEFN.has(k)) HIDEFN.delete(k); else HIDEFN.add(k);
  savePrefs();
  if(MAPST) drawMap(MAPST);
});
q('#psearch').addEventListener('input', e => { PFILTER = e.target.value; if(MAPST) drawMap(MAPST); });
q('#psearch').addEventListener('keydown', e => {
  // Enter picks the first match: a placed NPC is selected, a missing role armed
  if(e.key !== 'Enter') return;
  const b = q('#pscroll button'); if(b){ e.preventDefault(); b.click(); }
});
function centreOn(h, o){
  // zoomed in: bring the chosen NPC into view; at 1x everything already is
  if(VZ <= 1 || !h || !o) return;
  const [px, py] = w2p(h, o.x, o.z), span = ART / VZ;
  if(px < VX0 || px > VX0 + span || py < VY0 || py > VY0 + span){
    VX0 = px - span / 2; VY0 = py - span / 2; clampView();
  }
}
q('#mapcard').addEventListener('click', e => {
  const t = e.target.closest('[data-sel],[data-arm],[data-disarm]');
  if(!t) return;
  const h = cur();
  if(t.dataset.sel !== undefined){
    SELN = +t.dataset.sel; SELP = null; ARMED = null;
    centreOn(h, selNpc());
  } else if(t.dataset.arm !== undefined){
    ARMED = (ARMED === t.dataset.arm) ? null : t.dataset.arm;
    SELN = null; SELP = null;
  } else {
    ARMED = null;
  }
  q('#placer').hidden = true; PICK = null; PICKNAME = null;
  if(MAPST) drawMap(MAPST);
});
q('#capsel').addEventListener('change', e => {
  FOCUS = e.target.value; HALF = 0; SELP = null; SELN = null; ARMED = null;
  WANTAREA = PREFS.halfOf[FOCUS] != null ? +PREFS.halfOf[FOCUS] : null;
  try{ localStorage.setItem('fe-focus', FOCUS); }catch(err){}
  q('#mapimg').dataset.name = '';
  tick();
});
function exitPix(e){
  const cv = document.getElementById('exitcv'), r = cv.getBoundingClientRect();
  const g = selGate(), h2 = halves(MAPST).find(o => o.area === g.dest_area);
  const {x0, y0} = exitWindow(h2, g);
  return [x0 + (e.clientX - r.left) / r.width * EXIT_W, y0 + (e.clientY - r.top) / r.height * EXIT_W, h2, g];
}
q('#doordetail').addEventListener('mousedown', e => {
  if(e.target.id !== 'exitcv' || e.button !== 0 || !MAPST) return;
  const [mx, my] = exitPix(e);
  EXITPRESS = {mx, my, moved: true};
  e.preventDefault();
  drawExit(MAPST, halves(MAPST).find(o => o.area === selGate().dest_area), selGate());
});
window.addEventListener('mousemove', e => {
  if(!EXITPRESS) return;
  const [mx, my, h2, g] = exitPix(e);
  EXITPRESS.mx = mx; EXITPRESS.my = my;
  const [wx, wz] = p2w(h2, mx, my);
  const fl = floorAt(h2, mx, my);
  q('#mapread').textContent = `door #${g.portal} will put you at ${wx.toFixed(0)}, ${wz.toFixed(0)}`
    + (fl === false ? ' - NO FLOOR there' : '');
  drawExit(MAPST, h2, g);
});
window.addEventListener('mouseup', e => {
  if(!EXITPRESS) return;
  const p = EXITPRESS; EXITPRESS = null;
  const g = selGate(), h2 = halves(MAPST).find(o => o.area === g.dest_area);
  const [wx, wz] = p2w(h2, p.mx, p.my);
  if(floorAt(h2, p.mx, p.my) === false){
    const m = q('#editmsg'); m.hidden = false; m.className = 'editmsg bad';
    m.textContent = `No floor at ${wx.toFixed(1)}, ${wz.toFixed(1)} on ${halfName(h2)} - `
      + 'the tint shows where the game has ground. Nothing was changed.';
    drawMap(MAPST); return;
  }
  edit({op:'arrive', portal: g.portal, x: +wx.toFixed(1), z: +wz.toFixed(1)});
});
q('#npcdetail').addEventListener('click', e => {
  if(!e.target.closest('#npcretype')) return;
  const n = selNpc(), h = cur(), role = q('#npcrole').value;
  if(!n || !h || role === n.name) return;
  edit({op:'retype', area: h.area, idx: n.idx, role});
});
// every data-edit button on the page posts its JSON to /edit
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-edit]'); if(!b || b.disabled) return;
  if(b.dataset.confirm && !confirm(b.dataset.confirm)) return;
  edit(JSON.parse(b.dataset.edit));
});
q('#calMode').addEventListener('change', e => {
  CAL = e.target.checked; q('#placer').hidden = true; PICK = null; PICKNAME = null;
  if(MAPST) drawMap(MAPST);
});
q('#mapcard').addEventListener('click', e => {
  const t = e.target.closest('[data-half],[data-portal],[data-goto-half]');
  if(!t) return;
  if(t.dataset.gotoHalf !== undefined){
    HALF = +t.dataset.gotoHalf; SELP = +t.dataset.gotoPortal;
  } else if(t.dataset.half !== undefined){
    HALF = +t.dataset.half; SELP = null; SELN = null; ARMED = null;
    const hh = cur();
    if(hh){ PREFS.halfOf[q('#capsel').value] = hh.area; savePrefs(); }
  } else {
    const p = +t.dataset.portal; SELP = (SELP === p ? null : p); SELN = null;
    if(SELP !== null) centreOn(cur(), selGate());
  }
  q('#placer').hidden = true; PICK = null; PICKNAME = null;
  if(MAPST) drawMap(MAPST);
});
q('#placego').addEventListener('click', () => {
  if(!PICK) return;
  const role = q('#placerole').value, yaw = +q('#placeyaw').value;
  edit({op:'place', area: cur().area, x: +PICK[0].toFixed(1), z: +PICK[1].toFixed(1),
        role, yaw});
  PENDSEL = role;        // select it once the server has it
  q('#placer').hidden = true; PICK = null; PICKNAME = null;
});
q('#spawnhere').addEventListener('click', () => {
  if(!PICK) return;
  edit({op:'spawn', area: cur().area, x: +PICK[0].toFixed(1), z: +PICK[1].toFixed(1)});
  q('#placer').hidden = true; PICK = null; PICKNAME = null;
});
q('#placecancel').addEventListener('click', () => {
  q('#placer').hidden = true; PICK = null; PICKNAME = null;
});

async function tick(){
  try{
    const r = await fetch(qs('/state' + (FOCUS ? '?area=' + encodeURIComponent(FOCUS) : '')));
    if(r.status === 403){ q('#title').textContent = 'bad or missing token'; return; }
    if(PRESS || EXITPRESS){ live = true; return; }  // never redraw under a press
    const st = await r.json();
    if(st.focus == null && !FOCUS && st.capitals && st.capitals.length){
      FOCUS = String(st.capitals[0].area); return tick();
    }
    render(st);
    live = true;
  }catch(e){ q('#conn').textContent = 'server unreachable'; live = false; }
}

document.addEventListener('click', e => {
  const b = e.target.closest('button'); if(!b || b.disabled) return;
  if(b.dataset.edit !== undefined) return;       // the editor's own handler
  if(b.dataset.confirm && !confirm(b.dataset.confirm)) return;
  if(b.dataset.cmd) send(b.dataset.cmd);
  else if(b.dataset.npc) send('!npc ' + b.dataset.npc + ':1:1:0');
  else if(b.id === 'linkgo'){
    const d = q('#linkdest').value.trim();
    send(d ? '!link ' + d : '!link');
  }
  else if(b.id === 'facego'){ const v = q('#facedeg').value.trim(); if(v) send('!face ' + v); }
  else if(b.id === 'rawgo'){ const v = q('#raw').value.trim(); if(v){ send(v); q('#raw').value=''; } }
});
q('#raw').addEventListener('keydown', e => { if(e.key==='Enter') q('#rawgo').click(); });
q('#linkdest').addEventListener('keydown', e => { if(e.key==='Enter') q('#linkgo').click(); });
tick(); setInterval(tick, 1000);
</script></body></html>
"""
