"""Drive the FE world-building panel in a REAL browser and check what it does.

The panel's JavaScript had never been run by anything but a human in a real
browser, and two releases in a row shipped broken because of it: static
checks said the script was balanced and declared nothing twice, and the page
still could not be used. So this starts fedevtool on loopback with a
realistic world state,
opens the page in headless Chrome over the DevTools protocol, and:

  * fails on ANY JavaScript exception or console error
  * screenshots the page, so a human (or a model) can look at it
  * dispatches real mouse events -- press, move, release -- and then reads the
    command the page QUEUED, which is the effect-level assertion. "The page
    rendered" is not a test; "a drag on door 12's arrival queued
    `!arrive 12 X:Z`" is.

Needs Chrome and `websocket-client`. Nothing here touches prod.

    python tools/fe_panel_browser.py            # checks + screenshots
    python tools/fe_panel_browser.py --shots D  # screenshots into D
"""
import base64
import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "services"))

import websocket  # noqa: E402  (websocket-client)

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ---------------------------------------------------------------------------
# the world the page is shown
# ---------------------------------------------------------------------------
def build_world(tmp, area=91, pos=(-5.0, 20.0, -75.0)):
    """A realistic state: standing in a capital half with a spawn row, so
    placing and landing are enabled exactly as they would be live."""
    import feworld
    args = _Args(town_file=os.path.join(tmp, "town.json"), door_radius=15.0,
                 gmcmd_file=None, spawn_file=os.path.join(tmp, "spawn.json"),
                 territory_file=os.path.join(tmp, "terr.json"), spawn_area="",
                 spawn_height="dat", mapcal_file=os.path.join(tmp, "cal.json"),
                 door_arrive_file=os.path.join(tmp, "arr.json"),
                 doors="dat", door_height="dest", force_table={})
    args.spawn_xyz = (0.0, 0.0, 0.0)
    args.self_xyz = (0.0, 0.0, 0.0)
    feworld.territory_load(args)
    feworld.spawn_load(args)
    feworld.mapcal_load(args)
    feworld.doorarr_load(args)
    feworld.spawn_add(args, area, (pos[0], pos[1], pos[2]), "")
    set_session(area, pos)
    return args


def set_session(area, pos, **extra):
    import feworld
    s = {"in_field": True, "field": area, "cpos": pos}
    s.update(extra)
    feworld._TLS.session = s
    feworld.devtool_snapshot()


def run_queue(args):
    """Apply whatever the page queued, the way the player's thread would."""
    import feworld
    import fedevtool
    lines = list(fedevtool._Q)
    buf = io.StringIO()
    real = feworld.send
    feworld.send = lambda *a, **k: None
    try:
        with contextlib.redirect_stdout(buf):
            feworld.gm_pump(None, None, 0, False, args)
    finally:
        feworld.send = real
    feworld.devtool_snapshot()
    return lines, buf.getvalue()


# ---------------------------------------------------------------------------
# a tiny DevTools-protocol client
# ---------------------------------------------------------------------------
class Browser:
    def __init__(self, width=1500, height=1500, webgl=False):
        exe = next((c for c in CHROME_CANDIDATES if os.path.exists(c)), None)
        if exe is None:
            raise SystemExit("no Chrome/Edge found -- this harness needs one")
        self.port = free_port()
        self.prof = tempfile.mkdtemp(prefix="fe-panel-chrome-")
        # webgl=True: a page that draws in WebGL (the Jan /watch table) gets
        # SwiftShader, Chrome's software GPU; everything else keeps no GPU
        gpu = (["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if webgl
               else ["--disable-gpu"])
        self.proc = subprocess.Popen(
            [exe, "--headless=new"] + gpu + ["--no-first-run",
             "--no-default-browser-check", "--remote-allow-origins=*",
             "--remote-debugging-port=%d" % self.port,
             "--user-data-dir=%s" % self.prof,
             "--window-size=%d,%d" % (width, height), "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = None
        for _ in range(100):
            try:
                tabs = json.loads(urllib.request.urlopen(
                    "http://127.0.0.1:%d/json" % self.port, timeout=1).read())
                url = next(t["webSocketDebuggerUrl"] for t in tabs
                           if t.get("type") == "page")
                break
            except Exception:
                time.sleep(0.1)
        if url is None:
            self.close()
            raise SystemExit("Chrome never offered a debugging target")
        self.ws = websocket.create_connection(url, timeout=10)
        self.n = 0
        self.errors = []
        self.dialogs = []
        for m in ("Runtime.enable", "Page.enable", "Log.enable"):
            self.call(m)
        self.call("Emulation.setDeviceMetricsOverride",
                  width=width, height=height, deviceScaleFactor=1,
                  mobile=False)

    def _event(self, msg):
        m = msg.get("method")
        p = msg.get("params", {})
        if m == "Page.javascriptDialogOpening":
            # confirm()/alert() block the page until answered; say yes, and
            # remember that one was shown -- a confirm is part of the contract
            self.dialogs.append(p.get("message", ""))
            self.n += 1
            self.ws.send(json.dumps({"id": self.n,
                                     "method": "Page.handleJavaScriptDialog",
                                     "params": {"accept": True}}))
            return
        if m == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            ex = d.get("exception", {}).get("description") or d.get("text")
            self.errors.append("EXCEPTION %s (line %s)" % (ex, d.get("lineNumber")))
        elif m == "Runtime.consoleAPICalled" and p.get("type") in ("error", "assert"):
            self.errors.append("console.%s %s" % (
                p["type"], " ".join(str(a.get("value", a.get("description")))
                                   for a in p.get("args", []))))
        elif m == "Log.entryAdded" and p.get("entry", {}).get("level") == "error":
            e = p["entry"]
            # a favicon 404 is the browser's own business, not the page's
            if "favicon" not in (e.get("url") or ""):
                self.errors.append("log %s %s" % (e.get("text"), e.get("url", "")))

    def call(self, method, **params):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s: %s" % (method, msg["error"]))
                return msg.get("result", {})
            self._event(msg)

    def pump(self, secs):
        """Let the page run (its 1 Hz poll, timers) while collecting events."""
        end = time.time() + secs
        self.ws.settimeout(0.1)
        try:
            while time.time() < end:
                try:
                    self._event(json.loads(self.ws.recv()))
                except websocket.WebSocketTimeoutException:
                    pass
        finally:
            self.ws.settimeout(10)

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=True)
        if r.get("exceptionDetails"):
            raise RuntimeError("js failed: %s -> %s" % (
                expr[:80], r["exceptionDetails"].get("exception", {})
                .get("description")))
        return r.get("result", {}).get("value")

    def goto(self, url, settle=2.5):
        self.call("Page.navigate", url=url)
        self.pump(settle)

    def mouse(self, kind, x, y, buttons=1):
        self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y,
                  button="left", buttons=buttons if kind != "mouseReleased" else 0,
                  clickCount=1)

    def wheel(self, x, y, dy):
        self.call("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y,
                  deltaX=0, deltaY=dy)
        self.pump(0.2)

    def key(self, key, code, vk, shift=False):
        mods = 8 if shift else 0
        for kind in ("rawKeyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=kind, key=key, code=code,
                      windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk,
                      modifiers=mods)
        self.pump(0.2)

    def click(self, x, y):
        self.mouse("mouseMoved", x, y, buttons=0)
        self.mouse("mousePressed", x, y)
        self.mouse("mouseReleased", x, y)
        self.pump(0.2)

    def drag(self, x0, y0, x1, y1, steps=8):
        self.mouse("mouseMoved", x0, y0, buttons=0)
        self.mouse("mousePressed", x0, y0)
        for i in range(1, steps + 1):
            self.mouse("mouseMoved", x0 + (x1 - x0) * i / steps,
                       y0 + (y1 - y0) * i / steps)
        self.mouse("mouseReleased", x1, y1)
        self.pump(0.3)

    def screenshot(self, path, selector=None):
        kw = {"format": "png"}
        if selector:
            r = self.js("(()=>{const e=document.querySelector(%s);"
                        "if(!e)return null;e.scrollIntoView();"
                        "const b=e.getBoundingClientRect();"
                        "return [b.left+scrollX,b.top+scrollY,b.width,b.height]})()"
                        % json.dumps(selector))
            if r:
                kw["clip"] = {"x": r[0], "y": r[1], "width": r[2],
                              "height": r[3], "scale": 1}
                kw["captureBeyondViewport"] = True
        data = self.call("Page.captureScreenshot", **kw)["data"]
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(data))
        return path

    def center(self, selector):
        """Viewport centre of the first element matching `selector`."""
        r = self.js("(()=>{const e=document.querySelector(%s);if(!e)return null;"
                    "e.scrollIntoView({block:'center'});const b=e.getBoundingClientRect();"
                    "return [b.left+b.width/2,b.top+b.height/2]})()" % json.dumps(selector))
        if r is None:
            raise RuntimeError("no element %s" % selector)
        return r

    def click_el(self, selector):
        x, y = self.center(selector)
        self.click(x, y)

    def map_point(self, px, py):
        """A pixel on the 256 px ART -> viewport coordinates of the canvas."""
        r = self.js("(()=>{const c=document.querySelector('#mapcv');"
                    "c.scrollIntoView({block:'center'});"
                    "const b=c.getBoundingClientRect();"
                    "return [b.left,b.top,b.width,b.height]})()")
        return (r[0] + px / 256.0 * r[2], r[1] + py / 256.0 * r[3])

    def close(self):
        with contextlib.suppress(Exception):
            self.ws.close()
        with contextlib.suppress(Exception):
            self.proc.terminate()
            self.proc.wait(5)
        shutil.rmtree(self.prof, ignore_errors=True)


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------
def main(argv):
    import feworld
    import fedevtool
    import fegamedata
    shots = None
    if "--shots" in argv:
        shots = argv[argv.index("--shots") + 1]
        os.makedirs(shots, exist_ok=True)
    fails = []

    def check(name, ok, detail=""):
        print("  %-70s %s" % (name, "PASS" if ok else "FAIL"))
        if not ok:
            fails.append("%s  %s" % (name, detail))

    def shot(b, name, sel=None):
        if shots:
            b.screenshot(os.path.join(shots, name + ".png"), sel)

    def row(p):
        return '#doorlist button[data-portal="%d"]' % p

    def exit_point(b, mx, my):
        """A pixel on the DESTINATION half's art -> viewport coords of #exitcv."""
        r = b.js("(()=>{const c=document.querySelector('#exitcv');"
                 "c.scrollIntoView({block:'center'});const b=c.getBoundingClientRect();"
                 "const g=selGate(),h2=halves(MAPST).find(o=>o.area===g.dest_area);"
                 "const w=exitWindow(h2,g);"
                 "return [b.left,b.top,b.width,b.height,w.x0,w.y0]})()")
        return (r[0] + (mx - r[4]) / 40.0 * r[2], r[1] + (my - r[5]) / 40.0 * r[3])

    tmp = tempfile.mkdtemp(prefix="fe-panel-")
    args = build_world(tmp, area=91, pos=(-5.0, 20.0, -75.0))
    port = free_port()
    srv = fedevtool.start(port, "127.0.0.1", "tok",
                          lambda area=None: feworld.devtool_state(args, area),
                          lambda op: feworld.devtool_edit(args, op),
                          floor_fn=lambda area: feworld.devtool_floor(args, area))
    b = Browser()
    try:
        print("\nthe panel, in a real browser (area 91)")
        b.goto("http://127.0.0.1:%d/?t=tok" % port)
        shot(b, "01-loaded", "#mapcard")
        check("the page loads with NO JavaScript errors", not b.errors,
              "; ".join(b.errors))

        # ---- the page-wide bugs the first look found ----------------------
        check("the placer is HIDDEN until a map click (hidden beat display)",
              b.js("getComputedStyle(document.querySelector('#placer')).display")
              == "none")
        widths = b.js("[...document.querySelectorAll('input[type=checkbox]')]"
                      ".map(e=>e.getBoundingClientRect().width)")
        check("no checkbox is stretched (the 180 px input rule)",
              widths and max(widths) < 30, repr(widths))
        spill = b.js(
            "(()=>{const c=document.querySelector('#mapcard')"
            ".getBoundingClientRect();"
            "return [...document.querySelectorAll('#mapcard *')]"
            ".filter(e=>e.getBoundingClientRect().right>c.right+1)"
            ".map(e=>e.tagName+'#'+e.id).slice(0,5)})()")
        check("nothing in the map card spills past its right edge", not spill,
              repr(spill))

        # ---- choosing a door is a LIST, not a pixel hunt -------------------
        rows = b.js("[...document.querySelectorAll('#doorlist button')]"
                    ".map(e=>+e.dataset.portal)")
        want = sorted(g["portal"] for g in fegamedata.area_portals(91))
        check("the door list has one row per shipped door in this half",
              sorted(rows or []) == want, "%r vs %r" % (rows, want))
        # (not vacuous: .every() of NO rows is true, so demand rows exist)
        check("...each row carries a grid reference",
              b.js("(()=>{const g=[...document.querySelectorAll('#doorlist .g')];"
                   "return g.length>0&&g.every(e=>/^[A-H][1-8]$/.test(e.textContent))})()"))
        b.click_el(row(12))
        b.pump(0.3)
        check("clicking a row SELECTS that door",
              b.js("SELP") == 12 and b.js(
                  "document.querySelector('#doorlist button.on').dataset.portal")
              == "12")
        g12 = fegamedata.portal(12)
        check("...and its panel shows where it puts you ON THE OTHER HALF'S MAP",
              b.js("!document.querySelector('#doordetail').hidden")
              and b.js("!!document.querySelector('#exitcv')")
              and b.js("selGate().dest_area") == 21)
        check("...where Land here is off: you are not standing in 21, and it says so",
              b.js("document.querySelector('#doordetail button.go').disabled")
              and "walk through this door" in (b.js(
                  "document.querySelector('#doordetail').textContent") or ""))
        check("the main map draws ONLY the doorway for a selected door (no "
              "landing drawn against this half's art)",
              "arr" not in (b.js("(()=>{const h=cur(),g=selGate(),[x,y]=w2p(h,g.x,g.z);"
                                 "const r=hit(x,y);return r?r.kind:''})()") or ""))
        shot(b, "02-selected-12", "#mapcard")

        # ---- the click that used to be swallowed by the 1 Hz re-render -----
        x, y = b.center(row(13))
        b.mouse("mouseMoved", x, y, buttons=0)
        b.mouse("mousePressed", x, y)
        b.pump(1.6)                              # at least one re-render
        b.mouse("mouseReleased", x, y)
        b.pump(0.3)
        check("a press held across a re-render still lands as a click",
              b.js("SELP") == 13, "SELP=%r" % b.js("SELP"))

        # ---- Land here, from the door you walk THROUGH ---------------------
        # You stand in 91. The door that puts you in 91 is door #12's partner
        # #2, in area 21 -- selected on 21's tab, its Land here is live.
        key = g12["dest"]
        b.click_el('#halftabs button[data-half="1"]')
        b.pump(0.3)
        check("the second tab shows the OTHER half", b.js("cur().area") == 21,
              repr(b.js("cur().area")))
        b.click_el(row(key))
        b.pump(0.3)
        check("door #%d (21 -> 91) has Land here ENABLED while you stand in 91" % key,
              b.js("!document.querySelector('#doordetail button.go').disabled"))
        fedevtool.drain()
        b.click_el("#doordetail button.go")
        lines, out = run_queue(args)
        check("Land here queues `!arrive <this door> here`",
              lines == ["!arrive %d here" % key], repr(lines))
        got = feworld.doorarr_get(key)
        check("...and walking through #%d now puts you down WHERE YOU STOOD" % key,
              got and abs(got[0] + 5.0) < 0.05 and abs(got[1] + 75.0) < 0.05,
              "%r / %s" % (got, out.strip()[-240:]))
        b.pump(1.4)
        check("...and door #%d's row says its landing was moved" % key,
              b.js("!!document.querySelector('%s .pill.moved')" % row(key)))

        # ---- the floor overlay ----------------------------------------------
        fl = b.js("(()=>{const f=FLOOR[91];if(!f||!f.bits)return null;"
                  "let n=0;for(const v of f.bits){let x=v;while(x){n+=x&1;x>>=1}}return n})()")
        want_fl = feworld.devtool_floor(args, 91)["floor_px"]
        check("the page has 91's floor map, pixel for pixel what the server counts",
              fl == want_fl and want_fl > 100, "%r vs %r" % (fl, want_fl))
        spot = b.js("(()=>{const h=halves(MAPST).find(o=>o.area===91);"
                    "for(let y=10;y<250;y+=3)for(let x=10;x<250;x+=3)"
                    "if(floorAt(h,x+.5,y+.5)){const [wx,wz]=p2w(h,x+.5,y+.5);return [wx,wz]}})()")
        check("...and a pixel it calls floor has ground in the collision",
              spot and fegamedata.capital_ground(91, spot[0], spot[1]) is not None,
              repr(spot))

        # ---- the exit, dragged on the DESTINATION half's zoomed map ---------
        ep = b.js("(()=>{const g=selGate(),h2=halves(MAPST).find(o=>o.area===g.dest_area);"
                  "return w2p(h2,g.out_ax,g.out_az)})()")
        tgt = None
        for dpx, dpy in ((3, 4), (-3, 4), (0, 5), (3, -4), (-3, -4), (5, 0), (-5, 0)):
            wx, wz = b.js("p2w(halves(MAPST).find(o=>o.area===91), %f, %f)"
                          % (ep[0] + dpx, ep[1] + dpy))
            if (fegamedata.capital_ground(91, wx, wz) is not None and
                    ((wx - g12["gate"][0]) ** 2
                     + (wz - g12["gate"][1]) ** 2) ** 0.5 > g12["radius"] + 1):
                tgt = (ep[0] + dpx, ep[1] + dpy); break
        check("the fixture found a floor pixel near the exit", tgt)
        b.drag(*exit_point(b, *ep), *exit_point(b, *tgt))
        b.pump(0.6)
        want_w = b.js("p2w(halves(MAPST).find(o=>o.area===91), %f, %f)" % tgt)
        got = feworld.doorarr_get(key)
        check("dragging the exit on 91's OWN map stores it -- in 91's coordinates",
              got and abs(got[0] - want_w[0]) < 0.6
              and abs(got[1] - want_w[1]) < 0.6, "%r vs %r / %s"
              % (got, want_w, b.js("document.querySelector('#editmsg').textContent")))
        b.pump(1.3)
        shot(b, "03-exit-on-the-other-map", "#mapcard")
        # a drop with no floor under it is caught BEFORE it is sent
        nofl = b.js("(()=>{const g=selGate(),h2=halves(MAPST).find(o=>o.area===g.dest_area);"
                    "const w=exitWindow(h2,g);for(let y=0;y<40;y++)for(let x=0;x<40;x++)"
                    "if(floorAt(h2,w.x0+x+.5,w.y0+y+.5)===false)return [w.x0+x+.5,w.y0+y+.5]})()")
        check("the exit's window has a no-floor pixel to test with", nofl)
        before = feworld.doorarr_get(key)
        ep = b.js("(()=>{const g=selGate(),h2=halves(MAPST).find(o=>o.area===g.dest_area);"
                  "return w2p(h2,g.out_ax,g.out_az)})()")
        b.drag(*exit_point(b, *ep), *exit_point(b, *nofl))
        b.pump(0.6)
        msg = b.js("document.querySelector('#editmsg').textContent") or ""
        check("...a drop onto no floor says so and changes nothing",
              "No floor" in msg and feworld.doorarr_get(key) == before, msg)

        # ---- the door you walked THROUGH is picked for you -----------------
        b.click_el('#halftabs button[data-half="0"]')
        b.pump(0.3)
        set_session(91, (-5.0, 20.0, -75.0), arrived_via=15)
        b.pump(1.6)
        through = fegamedata.portal(15)["dest"]
        check("coming out of #15 SELECTS the door you walked through, #%d, on its tab"
              % through,
              b.js("cur().area") == 21 and b.js("SELP") == through,
              "half=%r SELP=%r" % (b.js("cur().area"), b.js("SELP")))
        check("...with Land here ready (you are standing where it put you)",
              b.js("!document.querySelector('#doordetail button.go').disabled"))
        check("...its row says you came through it",
              b.js("!!document.querySelector('%s .pill.here')" % row(through)))
        shot(b, "04-came-through", "#mapcard")
        b.click_el('#halftabs button[data-half="0"]')
        b.pump(0.3)
        check("...and #15's row, on this half, says you came out there",
              b.js("!!document.querySelector('%s .pill.here')" % row(15)))
        b.click_el(row(11))
        b.pump(1.5)
        check("...but only ONCE: choosing another door is not overridden",
              b.js("SELP") == 11, "SELP=%r" % b.js("SELP"))

        # ---- the partner door, one click away ------------------------------
        b.click_el("#doordetail .linkish")
        b.pump(0.3)
        g11 = [g for g in fegamedata.area_portals(91) if g["portal"] == 11][0]
        check("'Open <other half>' jumps to the partner door on the other tab",
              b.js("cur().area") == 21 and b.js("SELP") == g11["dest"],
              "half=%r SELP=%r want %r" % (b.js("cur().area"), b.js("SELP"),
                                           g11["dest"]))

        # ---- clicking a door ON THE MAP selects it --------------------------
        b.click_el('#halftabs button[data-half="0"]')
        b.pump(0.3)
        lone = b.js(
            "(()=>{const h=cur();for(const d of h.gates){"
            "const [a,c]=w2p(h,d.x,d.z);"
            "if(h.gates.filter(o=>{const [e,f]=w2p(h,o.x,o.z);"
            "return Math.hypot(e-a,f-c)<8}).length===1)return [d.portal,a,c]}})()")
        x, y = b.map_point(lone[1], lone[2])
        b.click(x, y)
        check("clicking a door dot on the map selects that door",
              b.js("SELP") == lone[0], "want #%s got %r" % (lone[0], b.js("SELP")))

        # ---- empty map: place on floor, a message off it -------------------
        # (the spot must be clear of every door and NPC, or the click picks it)
        clear = ("(want)=>{const h=cur();for(let y=12;y<244;y+=2)for(let x=12;x<244;x+=2){"
                 "if(floorAt(h,x,y)!==want)continue;"
                 "if([...h.gates,...h.npcs].some(o=>{const [a,c]=w2p(h,o.x,o.z);"
                 "return Math.hypot(a-x,c-y)<12}))continue;return [x,y]}}")
        onf = b.js("(%s)(true)" % clear)
        b.click(*b.map_point(*onf))
        check("clicking empty FLOOR opens the placer",
              b.js("getComputedStyle(document.querySelector('#placer')).display")
              != "none")
        b.click_el("#placecancel")
        check("...and cancel hides it again",
              b.js("getComputedStyle(document.querySelector('#placer')).display")
              == "none")
        off = b.js("(%s)(false)" % clear)
        b.click(*b.map_point(*off))
        b.pump(0.2)
        check("clicking where there is NO floor says so instead of silently failing",
              b.js("getComputedStyle(document.querySelector('#placer')).display")
              == "none" and "No floor" in (b.js(
                  "document.querySelector('#editmsg').textContent") or ""),
              b.js("document.querySelector('#editmsg').textContent"))

        # ---- align mode: I am standing here ---------------------------------
        b.click_el("#calMode")
        fedevtool.drain()
        x, y = b.map_point(40, 60)
        b.click(x, y)
        lines = fedevtool.drain()
        check("align mode: a click asks first, then anchors where you stand",
              b.dialogs and lines and lines[0].startswith("!mapcal here "),
              "dialogs=%r lines=%r" % (b.dialogs[-1:], lines))
        b.click_el("#calMode")

        # ================================================================
        # THE OFFLINE EDITOR -- nobody logged in
        # ================================================================
        print("\nthe editor with NOBODY in game")
        feworld._TLS.session = {}
        feworld.devtool_snapshot()
        b.goto("http://127.0.0.1:%d/?t=tok" % port)
        check("with nobody in game the editor still opens a capital map",
              b.js("!!(MAPST && halves(MAPST).length)")
              and b.js("!document.querySelector('#maprow').hidden"))
        check("...at full strength, while the session-only cards are faded",
              b.js("getComputedStyle(document.querySelector('#mapcard')).opacity") == "1"
              and b.js("getComputedStyle(document.querySelector('#checklist')).opacity") != "1")
        b.js("document.querySelector('#capsel').value='62';"
             "document.querySelector('#capsel').dispatchEvent(new Event('change'))")
        b.pump(1.5)
        check("the capital picker switches the map to the chosen capital",
              b.js("cur().area") == 62, repr(b.js("cur() && cur().area")))
        check("...and says nobody is standing there",
              "Nobody is in this half" in (b.js(
                  "document.querySelector('#livebar').textContent") or ""))
        # click a floor pixel -> placer -> place
        floor = None
        for py_ in range(60, 200, 6):
            for px_ in range(60, 200, 6):
                wx, wz = b.js("p2w(cur(), %d, %d)" % (px_, py_))
                if fegamedata.capital_ground(62, wx, wz) is not None:
                    floor = (px_, py_, wx, wz); break
            if floor: break
        check("the fixture found floor on 62's map", floor)
        b.click(*b.map_point(floor[0], floor[1]))
        b.pump(0.3)
        check("clicking empty floor opens the placer with this half's roles",
              b.js("getComputedStyle(document.querySelector('#placer')).display")
              != "none" and b.js("document.querySelector('#placerole').options.length") > 0)
        role = b.js("document.querySelector('#placerole').value")
        b.click_el("#placego")
        b.pump(1.2)
        rows = feworld.town_get(args, 62, "npcs")
        check("placing stores the NPC -- no session, height from the floor",
              len(rows) == 1 and rows[0]["name"] == role
              and abs(float(rows[0]["y"]) - fegamedata.capital_ground(
                  62, rows[0]["x"], rows[0]["z"])) < 0.01,
              "%r / %s" % (rows, b.js("document.querySelector('#editmsg').textContent")))
        # click the NPC -> selected, named
        npx = b.js("(()=>{const n=cur().npcs[0];return w2p(cur(),n.x,n.z)})()")
        b.click(*b.map_point(*npx))
        b.pump(0.3)
        check("clicking an NPC selects it",
              b.js("SELN") == 0 and b.js("!document.querySelector('#npcdetail').hidden"))
        check("...and names it", role in (b.js(
            "document.querySelector('#npcdetail .t').textContent") or ""))
        shot(b, "05-editor-npc", "#mapcard")
        # rotate by button: the effect is the stored yaw
        y0_ = float(feworld.town_get(args, 62, "npcs")[0]["yaw"])
        # the first row of buttons is -45, -15, +15, +45: the 4th turns +45
        b.click_el('#npcdetail .acts button:nth-child(4)')
        b.pump(1.0)
        y1_ = float(feworld.town_get(args, 62, "npcs")[0]["yaw"])
        check("the rotate button turns the NPC 45° -- stored",
              abs(((y1_ - y0_) % 360) - 45) < 0.01, "%r -> %r" % (y0_, y1_))
        # rotate by dragging the handle to due east of the NPC
        hx, hy = b.js("(()=>{const n=selNpc();return rotHandle(cur(),n,n.yaw)})()")
        nx_, ny_ = b.js("(()=>{const n=selNpc();return w2p(cur(),n.x,n.z)})()")
        b.drag(*b.map_point(hx, hy), *b.map_point(nx_ + 9, ny_))
        b.pump(1.0)
        y2_ = float(feworld.town_get(args, 62, "npcs")[0]["yaw"])
        check("dragging the handle due EAST of the NPC turns it to 90°",
              abs(y2_ - 90) < 0.01, repr(y2_))
        # move by dragging the NPC itself onto another floor pixel
        dest = None
        for dpx, dpy in ((6, 0), (-6, 0), (0, 6), (0, -6), (8, 8), (-8, -8)):
            wx, wz = b.js("p2w(cur(), %f, %f)" % (npx[0] + dpx, npx[1] + dpy))
            if fegamedata.capital_ground(62, wx, wz) is not None:
                dest = (npx[0] + dpx, npx[1] + dpy, wx, wz); break
        check("the fixture found floor to drag the NPC onto", dest)
        b.drag(*b.map_point(*npx), *b.map_point(dest[0], dest[1]))
        b.pump(1.0)
        r_ = feworld.town_get(args, 62, "npcs")[0]
        check("dragging the NPC moves it -- stored, on the new spot's floor",
              abs(r_["x"] - dest[2]) < 0.6 and abs(r_["z"] - dest[3]) < 0.6
              and abs(float(r_["y"]) - fegamedata.capital_ground(62, r_["x"], r_["z"])) < 0.01,
              repr(r_))
        # change its type from the dropdown
        b.js("document.querySelector('#npcrole').value='Bank_Keeper'")
        b.click_el("#npcretype")
        b.pump(1.0)
        r_ = feworld.town_get(args, 62, "npcs")[0]
        check("changing the type in the NPC panel swaps who it is -- stored",
              r_["name"] == "Bank_Keeper", repr(r_))
        # remove, with the confirm answered
        nd0 = len(b.dialogs)
        b.click_el('#npcdetail button.warn')
        b.pump(1.0)
        check("Remove asks once, then deletes the NPC",
              len(b.dialogs) == nd0 + 1 and not feworld.town_get(args, 62, "npcs"),
              "dialogs %d -> %d" % (nd0, len(b.dialogs)))
        # facing lines point the right way: a north-facing NPC's line goes UP
        feworld.devtool_edit(args, {"op": "place", "area": 62, "x": floor[2],
                                    "z": floor[3], "role": role, "yaw": 0})
        b.pump(1.3)
        up = b.js("(()=>{const h=cur(),n=h.npcs[0];SELN=n.idx;drawMap(MAPST);"
                  "const [hx,hy]=rotHandle(h,n,n.yaw),[px,py]=w2p(h,n.x,n.z);"
                  "return hy<py})()")
        check("a NORTH-facing NPC's handle is drawn ABOVE it (was mirrored)", up)
        feworld.devtool_edit(args, {"op": "remove", "area": 62, "idx": 0})

        # ---- the townsfolk: named districts, one button, their own words ----
        b.pump(1.3)
        check("the half tabs name the 2006 districts",
              "West" in (b.js("document.querySelector('#halftabs').textContent") or "")
              and "East" in (b.js("document.querySelector('#halftabs').textContent") or ""))
        n62 = len(feworld.town_get(args, 62, "npcs"))
        left = b.js("cur().townsfolk.filter(t=>!t.placed).length")
        nd1 = len(b.dialogs)
        b.click_el("#tfbar button")
        b.pump(1.6)
        rows62 = feworld.town_get(args, 62, "npcs")
        check("'Place the other N' asks once, then places the half's townsfolk -- stored",
              len(b.dialogs) == nd1 + 1 and left and left > 10
              and len(rows62) == n62 + left, "%d -> %d, left %r" % (n62, len(rows62), left))
        check("...and the bar then counts them all here, with no button",
              ("%d of %d" % (left, left)) in (b.js("document.querySelector('#tfbar').textContent") or "")
              and not b.js("!!document.querySelector('#tfbar button')"))
        for i_ in range(len(rows62) - 1, -1, -1):
            feworld.town_del(args, 62, "npcs", i_)
        # Nutsberry's people have no recorded words; the Forest Ward's all do
        feworld.devtool_edit(args, {"op": "townsfolk", "area": 39})
        b.js("document.querySelector('#capsel').value='39';"
             "document.querySelector('#capsel').dispatchEvent(new Event('change'))")
        b.pump(1.6)
        b.js("HALF=halves(MAPST).findIndex(h=>h.area===39);drawMap(MAPST)")
        lone = b.js("(()=>{const h=cur();for(const n of h.npcs){if(!n.says)continue;"
                    "const [a,c]=w2p(h,n.x,n.z);if(h.npcs.filter(o=>{const [e,f]=w2p(h,o.x,o.z);"
                    "return Math.hypot(e-a,f-c)<2.5}).length===1&&!h.gates.some(g=>{const [e,f]=w2p(h,g.x,g.z);"
                    "return Math.hypot(e-a,f-c)<5}))return [n.idx,a,c,n.says]}})()")
        check("the fixture found a townsperson standing apart", lone)
        b.click(*b.map_point(lone[1], lone[2]))
        b.pump(0.3)
        check("clicking a townsperson shows what they say",
              b.js("SELN") == lone[0] and lone[3][:30] in (b.js(
                  "(document.querySelector('#npcdetail .says')||{}).textContent") or ""),
              repr(b.js("SELN")))
        # the NEAREST NPC wins a click: put Jade 5 units from a townsperson
        # and click 30% and then 70% of the way from one to the other
        n0 = feworld.town_get(args, 39, "npcs")[lone[0]]
        off = next((dx, dz) for dx, dz in ((5, 0), (0, 5), (-5, 0), (0, -5), (4, 4), (-4, -4))
                   if fegamedata.capital_ground(39, n0["x"] + dx, n0["z"] + dz) is not None)
        feworld.devtool_edit(args, {"op": "place", "area": 39, "role": "Jade",
                                    "x": n0["x"] + off[0], "z": n0["z"] + off[1]})
        b.pump(1.4)
        jade = len(feworld.town_get(args, 39, "npcs")) - 1
        ends = b.js("(()=>{const h=cur(),a=h.npcs.find(n=>n.idx===%d),c=h.npcs.find(n=>n.idx===%d);"
                    "return [w2p(h,a.x,a.z),w2p(h,c.x,c.z)]})()" % (lone[0], jade))
        (ax_, ay_), (cx_, cy_) = ends
        b.click(*b.map_point(ax_ + (cx_ - ax_) * 0.3, ay_ + (cy_ - ay_) * 0.3))
        b.pump(0.3)
        s1 = b.js("SELN")
        b.click(*b.map_point(ax_ + (cx_ - ax_) * 0.7, ay_ + (cy_ - ay_) * 0.7))
        b.pump(0.3)
        s2 = b.js("SELN")
        check("a click between two close NPCs picks the NEARER one, both ways",
              s1 == lone[0] and s2 == jade and 1.0 < ((cx_ - ax_) ** 2 + (cy_ - ay_) ** 2) ** 0.5 < 6,
              "want %r then %r, got %r then %r" % (lone[0], jade, s1, s2))
        shot(b, "06-townsfolk", "#mapcard")

        # ---- ZOOM: wheel at a point, pan by dragging empty map, fine drags --
        # art px -> viewport, THROUGH the view (b.map_point assumes 1x)
        def view_point(px, py):
            r = b.js("(()=>{const c=document.querySelector('#mapcv');"
                     "const b=c.getBoundingClientRect();return [b.left,b.top,b.width,VX0,VY0,VZ]})()")
            return (r[0] + (px - r[3]) * r[5] / 256.0 * r[2],
                    r[1] + (py - r[4]) * r[5] / 256.0 * r[2])
        b.js("SELN=null;SELP=null;drawMap(MAPST)")
        tx, ty = b.js("(()=>{const h=cur(),n=h.npcs.find(n=>n.idx===%d);return w2p(h,n.x,n.z)})()"
                      % lone[0])
        wx_, wy_ = b.map_point(tx, ty)
        for _ in range(9):
            b.wheel(wx_, wy_, -120)
        z_ = b.js("VZ")
        back = b.js("(()=>{const c=document.querySelector('#mapcv').getBoundingClientRect();"
                    "return evPix({clientX:%f,clientY:%f})})()" % (wx_, wy_))
        check("the wheel zooms in at the pointer, keeping that spot under it",
              z_ >= 7 and abs(back[0] - tx) < 0.3 and abs(back[1] - ty) < 0.3,
              "VZ %r, %r vs %r" % (z_, back, (tx, ty)))
        m_ = b.js("getComputedStyle(document.querySelector('#mapimg')).transform")
        want_ = b.js("(()=>{const w=document.querySelector('#mapcv').getBoundingClientRect().width;"
                     "return [VZ,-VX0/256*w*VZ,-VY0/256*w*VZ]})()")
        mm_ = [float(v) for v in m_[m_.index("(") + 1:-1].split(",")] if "(" in m_ else []
        check("...and the ART moves with the markers (same scale and offset)",
              mm_ and abs(mm_[0] - want_[0]) < 1e-3 and abs(mm_[4] - want_[1]) < 0.6
              and abs(mm_[5] - want_[2]) < 0.6, "%r vs %r" % (m_, want_))
        # a drag at 8x+ moves the NPC in FINE steps: 16 screen px is < 1 art px
        n_before = feworld.town_get(args, 39, "npcs")[lone[0]]
        sx_, sy_ = view_point(tx, ty)
        dest_ = None
        for ddx, ddy in ((16, 0), (-16, 0), (0, 16), (0, -16)):
            ax_, ay_ = b.js("evPix({clientX:%f,clientY:%f})" % (sx_ + ddx, sy_ + ddy))
            wx2, wz2 = b.js("p2w(cur(), %f, %f)" % (ax_, ay_))
            if fegamedata.capital_ground(39, wx2, wz2) is not None:
                dest_ = (ddx, ddy, wx2, wz2)
                break
        check("the fixture found floor 16 screen px from the NPC", dest_)
        b.drag(sx_, sy_, sx_ + dest_[0], sy_ + dest_[1])
        b.pump(1.0)
        n_after = feworld.town_get(args, 39, "npcs")[lone[0]]
        moved_ = ((n_after["x"] - n_before["x"]) ** 2 + (n_after["z"] - n_before["z"]) ** 2) ** 0.5
        ax1 = abs(b.js("cur().proj.ax"))
        check("...a 16-pixel drag zoomed in moves it a FINE step (< 1 art px, "
              "%.2f world units per art px)" % ax1,
              0 < moved_ < ax1 * 1.01 and abs(n_after["x"] - dest_[2]) < 0.2,
              "moved %.2f u: %r -> %r" % (moved_, (n_before["x"], n_before["z"]),
                                         (n_after["x"], n_after["z"])))
        # pan: drag EMPTY map -- the view moves, nothing is selected or placed
        empty_ = b.js("(()=>{const h=cur();const r=document.querySelector('#mapcv').getBoundingClientRect();"
                      "for(let fy=.1;fy<.9;fy+=.05)for(let fx=.1;fx<.9;fx+=.05){"
                      "const x=r.left+fx*r.width,y=r.top+fy*r.height,p=evPix({clientX:x,clientY:y});"
                      "if(!hit(p[0],p[1]))return [x,y]}})()")
        vx_before = b.js("[VX0,VY0]")
        sel_before = b.js("SELN")
        b.drag(empty_[0], empty_[1], empty_[0] - 60, empty_[1] - 40)
        b.pump(0.4)
        vx_after = b.js("[VX0,VY0]")
        check("dragging empty map PANS the zoomed view, and selects/places nothing",
              (vx_after[0] > vx_before[0] or vx_after[1] > vx_before[1])
              and b.js("SELN") == sel_before
              and b.js("getComputedStyle(document.querySelector('#placer')).display") == "none",
              "%r -> %r" % (vx_before, vx_after))
        # arrow keys and Q/E on the selected NPC
        b.js("SELN=%d;drawMap(MAPST)" % lone[0])
        r0 = feworld.town_get(args, 39, "npcs")[lone[0]]
        sgn = 1 if b.js("cur().proj.ax") > 0 else -1
        want_x = r0["x"] + 0.5 * sgn
        if fegamedata.capital_ground(39, want_x, r0["z"]) is None:
            sgn, want_x = -sgn, r0["x"] - 0.5 * sgn
        if sgn == (1 if b.js("cur().proj.ax") > 0 else -1):
            b.key("ArrowRight", "ArrowRight", 39)
        else:
            b.key("ArrowLeft", "ArrowLeft", 37)
        b.pump(0.8)
        r1 = feworld.town_get(args, 39, "npcs")[lone[0]]
        check("an arrow key nudges the selected NPC half a world unit -- stored",
              abs(r1["x"] - want_x) < 0.06 and abs(r1["z"] - r0["z"]) < 0.06,
              "%r -> %r" % ((r0["x"], r0["z"]), (r1["x"], r1["z"])))
        b.key("e", "KeyE", 69)
        b.pump(0.8)
        r2 = feworld.town_get(args, 39, "npcs")[lone[0]]
        check("...and E turns it 15 degrees -- stored",
              abs(((float(r2["yaw"]) - float(r1["yaw"])) % 360) - 15) < 0.01,
              "%r -> %r" % (r1["yaw"], r2["yaw"]))
        shot(b, "07-zoomed", "#mapcard")
        b.click_el("#zfit")
        check("Fit shows the whole map again", b.js("VZ") == 1 and b.js("VX0") == 0)
        rows39 = feworld.town_get(args, 39, "npcs")
        for i_ in range(len(rows39) - 1, -1, -1):
            feworld.town_del(args, 39, "npcs", i_)

        check("no JavaScript error at any point", not b.errors,
              "; ".join(b.errors))
    finally:
        b.close()
        srv.shutdown()
    print()
    if fails:
        print("FAILURES:")
        for f in fails:
            print("  " + f)
        return 1
    print("[fe_panel_browser] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
