#!/usr/bin/env python3
"""fe_map_browser.py -- render the LIVE DOMINION MAP page in a real headless
Chrome and check what a viewer actually gets (render-web-ui-before-shipping).

Starts femap's page server on loopback over a realistic board -- players in
three fields, a war on the Central Continent -- then:

  * fails on ANY JavaScript exception or console error
  * screenshots the world view, clicks a continent, screenshots that
  * hovers a field and checks the card names it; clicks a war in the side
    panel and checks the view moved there

    python tools/fe_map_browser.py [--shots DIR] [--names]

Needs Chrome/Edge and `websocket-client` (see fe_panel_browser.py).
"""
import argparse
import os
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, os.pardir, "services"))

from fe_panel_browser import Browser, free_port   # noqa: E402
import feworld      # noqa: E402
import femap        # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=tempfile.mkdtemp(prefix="fe-map-shots-"))
    ap.add_argument("--names", action="store_true")
    o = ap.parse_args(argv)
    os.makedirs(o.shots, exist_ok=True)
    import fecampaign
    tmp = tempfile.mkdtemp(prefix="fe-map-browser-")
    args = types.SimpleNamespace(
        force_table=feworld.parse_forces("1:Netzawar,2:Cathedira,3:Elsord,4:Holdein,5:Gebrand"),
        campaign="on", map_names="on" if o.names else "off", map_poll=2.0,
        map_title="Fantasy Earth - Dominion", map_url="", territory="",
        territory_file=os.path.join(tmp, "terr.json"))
    feworld.territory_load(args)
    femap.register(feworld)
    people = [("Lex", 5, 1), ("Quinn", 5, 3), ("Elena", 12, 5), ("Rin", 12, 4),
              ("Elm", 12, 4), ("Ned", 62, 4)]
    feworld.ext_sessions = lambda: [
        {"key": i, "name": n, "session": {"in_field": True, "field": a,
                                          "pres_card": {"force": f}}}
        for i, (n, a, f) in enumerate(people)]
    with fecampaign._LOCK:
        fecampaign._STATE[12] = {"phase": fecampaign.WAR, "until": time.time() + 240,
                                 "atk": 5, "signups": {"def": 2, "atk": 1},
                                 "members": {}, "keeps": {"def": [2600, 3000],
                                                          "atk": [3000, 3000]}}
    femap.observe(femap.snapshot(args))
    # one capture for the Recent list -- area 2 is Holdein's on the shipped
    # board (area 7 is ALREADY Gebrand's, so "taking" it changed nothing)
    feworld.territory_set(2, 5, args)
    femap.observe(femap.snapshot(args))
    port = free_port()
    srv = femap.serve(args, port)
    fails = []

    def check(name, ok, detail=""):
        print("  %-60s %s%s" % (name, "PASS" if ok else "FAIL",
                                "  " + str(detail) if detail and not ok else ""))
        if not ok:
            fails.append(name)

    b = Browser(width=1400, height=1000)
    try:
        b.goto("http://127.0.0.1:%d/" % port, settle=3.0)
        check("the page loads the board", b.js("S && S.fields.length") == 90)
        check("the nations table has five rows",
              b.js("document.querySelectorAll('#nations tr').length") == 5)
        check("the war is listed", "Gebrand" in (b.js("document.querySelector('#wars').innerText") or ""))
        check("the capture is listed", "took" in (b.js("document.querySelector('#events').innerText") or ""))
        b.screenshot(os.path.join(o.shots, "1-world.png"))
        # click the Central Continent's centre on the world map
        s, ox, oy = femap.WORLD_PLACE[1]
        r = b.js("(()=>{const b=document.querySelector('#map').getBoundingClientRect();"
                 "return [b.left,b.top,b.width,b.height]})()")
        lx, ly = ox + 256 * s, oy + 300 * s
        b.click(r[0] + lx * r[2] / 800.0, r[1] + ly * r[3] / 600.0)
        b.pump(0.6)
        check("clicking the Central Continent opens it", b.js("VIEW") == 1, b.js("VIEW"))
        b.screenshot(os.path.join(o.shots, "2-central.png"))
        # hover Land of Beginnings (area 5) -- continent coords + origin
        f5 = next(f for f in femap.snapshot(args)["fields"] if f["id"] == 5)
        cx, cy = femap.CONTINENT_ORIGIN[0] + f5["x"], femap.CONTINENT_ORIGIN[1] + f5["y"]
        px, py = r[0] + cx * r[2] / 800.0, r[1] + cy * r[3] / 600.0
        b.mouse("mouseMoved", px, py, buttons=0)
        b.pump(0.4)
        card = b.js("(()=>{const c=document.querySelector('#card');return c.hidden?null:c.innerText})()")
        check("hovering Land of Beginnings shows its card",
              bool(card) and f5["name"] in card and "Total Players" in card, card)
        b.screenshot(os.path.join(o.shots, "3-hover.png"))
        b.click_el("#wars li.war")
        b.pump(0.5)
        check("clicking the war pins its field", b.js("PIN && PIN.id") == 12)
        b.screenshot(os.path.join(o.shots, "4-war.png"))
        b.click_el("#tabs button:nth-child(6)")
        b.pump(0.5)
        check("a continent tab switches the view", b.js("VIEW") == 5, b.js("VIEW"))
        b.js("location.hash = '#c3'")
        b.pump(0.4)
        check("a hash change (a shared link, the back button) follows",
              b.js("VIEW") == 3, b.js("VIEW"))
        b.screenshot(os.path.join(o.shots, "5-holdein.png"))
        b.call("Emulation.setDeviceMetricsOverride", width=420, height=900,
               deviceScaleFactor=2, mobile=True)
        # a fresh load (the query forces it -- a hash-only change is not one)
        b.goto("http://127.0.0.1:%d/?phone=1#c1" % port, settle=2.5)
        check("a phone-width page opens on the #c1 continent", b.js("VIEW") == 1)
        check("...without sideways scrolling",
              b.js("document.documentElement.scrollWidth <= window.innerWidth + 1"))
        b.screenshot(os.path.join(o.shots, "6-phone.png"))
        check("no JavaScript errors", not b.errors, b.errors)
    finally:
        b.close()
        srv.shutdown()
        with fecampaign._LOCK:
            fecampaign._STATE.pop(12, None)
    print("screenshots in %s" % o.shots)
    if fails:
        raise SystemExit("[fe_map_browser] %d FAILED: %s" % (len(fails), ", ".join(fails)))
    print("[fe_map_browser] OK")


if __name__ == "__main__":
    main()
