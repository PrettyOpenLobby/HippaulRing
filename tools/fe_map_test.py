#!/usr/bin/env python3
"""fe_map_test.py -- the LIVE DOMINION MAP (services/femap.py).

Run from anywhere:  python tools/fe_map_test.py

Pins, in order:
  * the snapshot: 90 markers (95 areas minus the five capital second halves),
    owners off the territory board, players counted per field AND per nation,
    a capital's inner half counted on its outer marker, the Fields column
    matching the nation record's own count
  * NAMES are absent unless --map-names on (a public page is the default)
  * events: the first look only seeds; a capture and a war start each produce
    exactly one event
  * Discord: first tick POSTs (?wait=true) and keeps the message id; a change
    EDITS that message; a deleted message is re-posted; 429 holds; nothing is
    sent inside the interval
  * the PNG (when Pillow is present) and the page server's routes -- including
    that /art/ serves only the whitelist
  * the feworld wiring: femap is in EXT_MODULES and its start() is a
    register_start hook
"""
import json
import os
import socket
import struct
import sys
import tempfile
import time
import types
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "services"))

import feworld      # noqa: E402
import femap        # noqa: E402

OUT = sys.__stdout__
CHECKS = []


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-70s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label + (": %s" % detail if detail else ""))


FORCES = "1:Netzawar,2:Cathedira,3:Elsord,4:Holdein,5:Gebrand"


def _args(tmp, **kw):
    a = dict(force_table=feworld.parse_forces(FORCES), campaign="off",
             map_names="off", map_poll=3.0, map_title="FE Dominion", map_url="",
             territory="", territory_file=os.path.join(tmp, "terr.json"),
             map_discord_webhook="", map_discord_every=60.0,
             map_discord_view="world", map_discord_events="on",
             map_discord_state=os.path.join(tmp, "discord.json"),
             map_port=0, map_bind="127.0.0.1")
    a.update(kw)
    return types.SimpleNamespace(**a)


def _sess(key, name, area, force, in_field=True):
    return {"key": key, "name": name,
            "session": {"in_field": in_field, "field": area,
                        "pres_card": {"force": force}}}


def _reset():
    femap._SEEN.update(owners=None, phases=None)
    femap._EVENTS.clear()
    femap._SNAP.update(t=0.0, snap=None)
    femap._RENDER.clear()


def snapshot_checks(tmp):
    say("the snapshot")
    a = _args(tmp)
    feworld.territory_load(a)
    feworld.ext_sessions = lambda: [
        _sess(1, "Lex", 5, 1), _sess(2, "Quinn", 5, 3),
        _sess(3, "Elena", 91, 5), _sess(4, "Idle", None, 2, in_field=False)]
    s = femap.snapshot(a)
    ids = {f["id"] for f in s["fields"]}
    check("90 markers: 95 areas minus the five capital second halves",
          len(s["fields"]) == 90 and not ids & set(femap.CAPITAL_TWINS),
          len(s["fields"]))
    check("every marker is on a continent image (0..512) and island 1..6",
          all(0 <= f["x"] <= 512 and 0 <= f["y"] <= 512 and 1 <= f["island"] <= 6
              for f in s["fields"]))
    f5 = next(f for f in s["fields"] if f["id"] == 5)
    check("two players in area 5, one per nation",
          f5["players"] == 2 and f5["by_nation"] == {"1": 1, "3": 1},
          (f5["players"], f5["by_nation"]))
    f21 = next(f for f in s["fields"] if f["id"] == 21)
    check("a player in capital half 91 is counted on its outer marker 21",
          f21["players"] == 1 and f21["capital"], f21)
    check("the owner is the territory board's",
          all(f["owner"] == feworld.territory_owner(f["id"], a) for f in s["fields"]))
    held = {n: sum(1 for x in feworld.fegamedata.areas()
                   if feworld.territory_owner(x, a) == n) for n in range(1, 6)}
    check("each nation's Fields is the nation record's count (all 95 areas)",
          {n["id"]: n["fields"] for n in s["nations"]} == held
          and sum(held.values()) == 95, held)
    check("online counts every session in the world, by its presence force",
          {n["id"]: n["online"] for n in s["nations"]} == {1: 1, 2: 1, 3: 1, 4: 0, 5: 1}
          and s["in_world"] == 4)
    check("names are ABSENT by default", all("names" not in f for f in s["fields"])
          and s["names"] is False)
    a.map_names = "on"
    s2 = femap.snapshot(a)
    f5 = next(f for f in s2["fields"] if f["id"] == 5)
    check("...and listed, sorted, with --map-names on",
          [p["name"] for p in f5["names"]] == ["Lex", "Quinn"]
          and f5["names"][0]["nation"] == 1, f5.get("names"))
    check("the whole thing is JSON", json.loads(json.dumps(s2))["fields"])
    check("a neighbour list never names a twin or itself",
          all(n not in femap.CAPITAL_TWINS and n != f["id"]
              for f in s["fields"] for n in f["neighbours"]))
    return a


def event_checks(tmp):
    say("events -- captures and war starts")
    import fecampaign
    a = _args(tmp)
    feworld.territory_load(a)
    feworld.ext_sessions = lambda: []
    _reset()
    check("the first look only seeds", femap.observe(femap.snapshot(a)) == [])
    check("...and an unchanged board says nothing",
          femap.observe(femap.snapshot(a)) == [])
    was = feworld.territory_owner(7, a)
    new = 1 if was != 1 else 2
    feworld.territory_set(7, new, a)
    ev = femap.observe(femap.snapshot(a))
    check("a field changing hands is ONE capture event",
          len(ev) == 1 and ev[0]["kind"] == "capture" and ev[0]["area"] == 7
          and ev[0]["from"] == was and ev[0]["to"] == new, ev)
    txt = femap.event_text(a, ev[0])
    check("...worded with both nations' names",
          femap._nation_name(a, new) in txt and femap._nation_name(a, was) in txt, txt)
    a.campaign = "on"
    with fecampaign._LOCK:
        fecampaign._STATE[12] = {"phase": fecampaign.WAR, "until": time.time() + 125,
                                 "atk": 3, "signups": {"def": 7, "atk": 4},
                                 "members": {}, "keeps": {"def": [2500, 3000],
                                                          "atk": [3000, 3000]}}
    try:
        s = femap.snapshot(a)
        w = next(f for f in s["fields"] if f["id"] == 12)["war"]
        check("a war row carries phase, attacker, sides, time left, keeps",
              w["phase"] == 2 and w["phase_name"] == "At war" and w["attacker"] == 3
              and (w["def"], w["atk"]) == (7, 4) and 120 <= w["left_s"] <= 125
              and w["keeps"]["def"] == [2500, 3000], w)
        ev = femap.observe(s)
        check("...and its start is ONE war event",
              [e["kind"] for e in ev] == ["war"] and ev[0]["attacker"] == 3, ev)
        check("...listed on the snapshot, newest first",
              femap.snapshot(a)["events"][0]["kind"] == "war")
    finally:
        with fecampaign._LOCK:
            fecampaign._STATE.pop(12, None)
        feworld.territory_set(7, was, a)
    return a


class FakeNet:
    """Stands in for urllib.request.urlopen: records every request, answers
    from a script of (status, json)."""

    def __init__(self):
        self.calls = []
        self.script = []

    def __call__(self, req, timeout=None):
        body = req.data or b""
        self.calls.append((req.get_method(), req.full_url, body,
                           req.get_header("Content-type"), req.get_header("User-agent")))
        st, data = self.script.pop(0) if self.script else (200, {})
        raw = json.dumps(data).encode()
        if st >= 400:
            raise urllib.error.HTTPError(req.full_url, st, "x", {}, _Body(raw))
        return _Resp(st, raw)


class _Body:
    def __init__(self, raw):
        self.raw = raw

    def read(self, *a):
        return self.raw

    def close(self):
        pass


class _Resp(_Body):
    def __init__(self, st, raw):
        super().__init__(raw)
        self.status = st

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def discord_checks(tmp):
    say("Discord -- one webhook message, edited in place")
    hook = "https://discord.com/api/webhooks/123/tok"
    a = _args(tmp, map_discord_webhook=hook, map_discord_every=60.0)
    feworld.territory_load(a)
    feworld.ext_sessions = lambda: [_sess(1, "Lex", 5, 1)]
    net = FakeNet()
    d = femap.Discord(a, opener=net)
    s = femap.snapshot(a)
    net.script = [(200, {"id": "999"})]
    check("the first tick POSTs with ?wait=true and keeps the message id",
          d.tick(s, now=1000.0) == "posted" and d.msg_id == "999"
          and net.calls[-1][0] == "POST" and net.calls[-1][1] == hook + "?wait=true",
          net.calls[-1][:2])
    body, ctype = net.calls[-1][2], net.calls[-1][3]
    check("...multipart, with payload_json%s" % (" and the PNG" if femap._pil() else ""),
          ctype.startswith("multipart/form-data") and b'name="payload_json"' in body
          and ((b'name="files[0]"' in body and b"\x89PNG" in body) if femap._pil() else True))
    pj = json.loads(body.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
    check("...an embed listing every nation, no pings",
          len(pj["embeds"]) == 1 and all(n in pj["embeds"][0]["description"]
                                         for n in ("Netzawar", "Gebrand"))
          and pj["allowed_mentions"] == {"parse": []}, pj["embeds"][0]["description"])
    check("...a Discord-style User-Agent (a bare urllib one is refused)",
          (net.calls[-1][4] or "").startswith("DiscordBot"))
    check("the id is remembered for THIS webhook across a restart",
          femap.Discord(a, opener=net).msg_id == "999")
    check("...but not for a different webhook",
          femap.Discord(_args(tmp, map_discord_webhook=hook + "x"), opener=net).msg_id is None)
    n0 = len(net.calls)
    check("nothing is sent inside the interval", d.tick(s, now=1030.0) is None
          and len(net.calls) == n0)
    check("nothing is sent when nothing changed (until the 10-minute refresh)",
          d.tick(s, now=1100.0) is None and len(net.calls) == n0)
    feworld.ext_sessions = lambda: [_sess(1, "Lex", 5, 1), _sess(2, "Quinn", 6, 4)]
    s2 = femap.snapshot(a)
    net.script = [(200, {"id": "999"})]
    check("a change EDITS the same message (PATCH .../messages/999)",
          d.tick(s2, now=1100.0) == "edited" and net.calls[-1][0] == "PATCH"
          and net.calls[-1][1] == hook + "/messages/999", net.calls[-1][:2])
    check("...and the 10-minute refresh edits even with no change",
          (net.script.append((200, {})) or True) and d.tick(s2, now=1800.0) == "edited")
    net.script = [(404, {"message": "Unknown Message"}), (200, {"id": "1000"})]
    check("a deleted map message is re-posted", d.tick(s, now=2500.0, force=True) == "posted"
          and d.msg_id == "1000" and [c[0] for c in net.calls[-2:]] == ["PATCH", "POST"])
    net.script = [(429, {"retry_after": 30})]
    check("429 holds the webhook for retry_after",
          d.tick(s2, now=time.time(), force=True) == "limited" and d.hold_until > time.time() + 20)
    n0 = len(net.calls)
    check("...and nothing is sent while held", d.tick(s, force=True) is None
          and d.say("x") is False and len(net.calls) == n0)
    d.hold_until = 0
    net.script = [(200, {"id": "e1"})]
    check("an event is one short JSON message with no pings",
          d.say("⚔ war") is True and net.calls[-1][3] == "application/json"
          and json.loads(net.calls[-1][2])["allowed_mentions"] == {"parse": []})
    check("...posted with ?wait=true, and its id kept for the sweep",
          net.calls[-1][1] == hook + "?wait=true"
          and [e["id"] for e in d.events] == ["e1"], d.events)

    say("Discord -- status posts clean themselves up")
    d.swept = True                      # past the start-up sweep
    d.ttl = 600
    d.events[-1]["t"] = time.time() - 700
    net.script = [(200, {"id": "e2"})]
    d.say("🏳 capture")
    n0 = len(net.calls)
    net.script = [(204, {})]
    check("a post older than the TTL is DELETEd (and only it)",
          d.sweep() == 1 and len(net.calls) == n0 + 1
          and net.calls[-1][0] == "DELETE" and net.calls[-1][1] == hook + "/messages/e1"
          and [e["id"] for e in d.events] == ["e2"], (net.calls[-1][:2], d.events))
    n0 = len(net.calls)
    check("...a young one stays, and nothing is sent for it",
          d.sweep() == 0 and len(net.calls) == n0)
    check("the ids still up survive a restart (kept in the state file)",
          [e["id"] for e in femap.Discord(a, opener=net).events] == ["e2"])
    d2 = femap.Discord(a, opener=net)
    net.script = [(404, {"message": "Unknown Message"})]
    check("a restart's FIRST sweep deletes the last run's posts, young or not "
          "(404 = already gone, counts as done)",
          d2.sweep() == 1 and net.calls[-1][0] == "DELETE" and d2.events == [])
    check("...and that is saved, so the next restart does not retry it",
          femap.Discord(a, opener=net).events == [])
    d3 = femap.Discord(_args(tmp, map_discord_webhook=hook,
                             map_discord_event_ttl=0), opener=net)
    d3.swept = True
    d3.events = [{"id": "old", "t": time.time() - 99999}]
    n0 = len(net.calls)
    check("--map-discord-event-ttl 0 keeps every post",
          d3.sweep() == 0 and len(net.calls) == n0)
    d.events = [{"id": "e9", "t": 0.0}]
    net.script = [(429, {"retry_after": 12})]
    check("a 429 on a delete holds the webhook and keeps the id for later",
          d.sweep() == 0 and d.hold_until > time.time() + 5
          and [e["id"] for e in d.events] == ["e9"])
    d.hold_until = 0


def render_checks(tmp):
    say("the PNG")
    if not femap._pil():
        say("  (Pillow not installed here -- render checks skipped; the "
            "Discord post falls back to text)")
        return
    a = _args(tmp)
    feworld.ext_sessions = lambda: [_sess(1, "Lex", 5, 1)]
    s = femap.snapshot(a)
    for view in ("world", "1", "6"):
        png = femap.render_png(s, view)
        w, h = struct.unpack(">II", png[16:24])
        check("view %s renders a %dx%d PNG" % (view, w, h),
              png[:8] == b"\x89PNG\r\n\x1a\n" and (w, h) == (1090, 600))


def _free_port():
    x = socket.socket()
    x.bind(("127.0.0.1", 0))
    p = x.getsockname()[1]
    x.close()
    return p


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.read()


def server_checks(tmp):
    say("the page server")
    a = _args(tmp)
    feworld.ext_sessions = lambda: [_sess(1, "Lex", 5, 1)]
    _reset()
    port = _free_port()
    srv = femap.serve(a, port)
    base = "http://127.0.0.1:%d" % port
    try:
        st, ct, body = _get(base + "/")
        check("/ is the page", st == 200 and ct.startswith("text/html")
              and b"<canvas id=\"map\"" in body)
        st, ct, body = _get(base + "/state.json")
        j = json.loads(body)
        check("/state.json is the snapshot", st == 200 and len(j["fields"]) == 90
              and "names" in j and j["names"] is False)
        st, ct, body = _get(base + "/art/world.png")
        check("/art/world.png is SE's world map", st == 200 and ct == "image/png"
              and body[:4] == b"\x89PNG")
        for bad in ("/art/../femap.py", "/art/%2e%2e/femap.py", "/art/feworld.py",
                    "/art/"):
            st, _ct, _b = _get(base + bad)
            check("%s is refused" % bad, st == 404, st)
        st, _ct, _b = _get(base + "/map.png?view=9")
        check("/map.png refuses an unknown view", st == 400)
        st, ct, body = _get(base + "/map.png?view=world")
        check("/map.png draws (or says there is no Pillow)",
              (st == 200 and body[:4] == b"\x89PNG") if femap._pil() else st == 503)
        st, _ct, body = _get(base + "/healthz")
        check("/healthz", st == 200 and body == b"ok")
    finally:
        srv.shutdown()


def wiring_checks(tmp):
    say("the feworld wiring")
    check("femap is one of feworld's extension modules", "femap" in feworld.EXT_MODULES)
    import ast
    with open(feworld.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    dead = set()
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Constant) \
                and node.test.value is False:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    dead.update(a.name for a in sub.names)
    check("the dead `if False:` imports == EXT_MODULES, so pol-stale-check's "
          "ast closure restarts feworld for ANY extension (femap.py alone "
          "restarted nothing)", dead == set(feworld.EXT_MODULES),
          sorted(dead ^ set(feworld.EXT_MODULES)))
    check("feworld has a register_start hook, and femap.start is on it",
          hasattr(feworld, "register_start") and femap.start in feworld.EXT_START)
    check("with no port and no webhook, start() starts nothing",
          femap.start(_args(tmp)) is None)
    check("the art the page asks for is all on disk",
          all(os.path.isfile(os.path.join(femap.ART_DIR, n)) for n in femap.ART_FILES))


def main():
    tmp = tempfile.mkdtemp(prefix="fe-map-test-")
    saved = feworld.ext_sessions
    femap.register(feworld)
    try:
        snapshot_checks(tmp)
        event_checks(tmp)
        discord_checks(tmp)
        render_checks(tmp)
        server_checks(tmp)
        wiring_checks(tmp)
    finally:
        feworld.ext_sessions = saved
    say("[fe_map_test] OK -- %d checks" % len(CHECKS))


if __name__ == "__main__":
    main()
