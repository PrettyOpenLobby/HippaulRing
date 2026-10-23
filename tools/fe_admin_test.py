#!/usr/bin/env python3
"""fe_admin_test.py -- the OpenLobby admin panel's two Fantasy Earth actions,
System message and Start a war, from the panel's own action code through its
proxy (admingames.call) and the world server's tool port (fedevtool) to the
GM paths they end in:

    System message  -> devtool.admin_sysmsg -> fegm.announce -> ext_post
                       "gm.sysmsg" -> on_relay_sysmsg -> 0xE00B on a session
    Start a war     -> devtool.admin_war -> fecampaign.admin_declare ->
                       can_declare + declare (`!campaign declare AREA NATION`)

and the panel's Calendar: the module's tool_calendar reads calendar.json
(devtool.admin_calendar -> fecampaign.calendar), the war's current phase and
the ones still ahead of it,

and that bad war input is refused by the panel before any request is made,
and by the tool port before fecampaign is asked, when it is sent directly.

Run from the repo root:  python tools/fe_admin_test.py

The panel half needs the OpenLobby core checked out beside this repository
(or OPENLOBBY_DIR); without it that half reports SKIP and the tool port half
still runs.
"""
import json
import os
import socket
import struct
import sys
import tempfile
import types
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
_SERVICES = os.path.join(ROOT, "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)
OPENLOBBY = os.environ.get("OPENLOBBY_DIR", os.path.join(ROOT, os.pardir, "openlobby"))

import fedevtool    # noqa: E402
import feworld      # noqa: E402
from world import chat, devtool, extrun, territory   # noqa: E402

TOKEN = "test-token-1234"
NUL = bytes(1)
FAILS = []
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def check(label, cond, detail=""):
    print("  %-64s %s%s" % (label, "PASS" if cond else "FAIL",
                            ("  " + detail) if (detail and not cond) else ""), flush=True)
    if not cond:
        FAILS.append(label)


def _args(tmp, **kw):
    a = dict(campaign="on", campaign_auto="off", war_cap=60, war_prep_ms=65000,
             war_length_ms=250, war_truce_ms=250, war_decide="signups",
             campaign_file=os.path.join(tmp, "camp.json"),
             territory_file=os.path.join(tmp, "terr.json"), territory="",
             war="offensive", war_fields="0,0,0,0", war_deadline_ms=60000,
             war_clock="off", war_trace="off", war_start_fields="0,0",
             war_cycle="on", seq_mode="count", world_prefix=4, unit_id="0",
             campaign_notify_all=False, capital_staff="off", town_file="",
             events="on", event_greeting=None, capital="1:39",
             gm_accounts="", sos="on", gm_slash="verbs",
             force_table=territory.parse_forces("1:Netzawar,2:Cathedira,3:Elsord,"
                                                "4:Holdein,5:Gebrand"),
             shop_types="weapon=1,armor=2,item=3,ring=4", timed_events="off")
    a.update(kw)
    return types.SimpleNamespace(**a)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _post(port, path, body, token=TOKEN, actor="Wren"):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data,
                                 headers={"X-Devtool-Token": token,
                                          "X-Devtool-Actor": actor,
                                          "Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _get(port, path, token=TOKEN):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 headers={"X-Devtool-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def tool_port(args, port, ctx, sent, asked):
    import fecampaign
    print("tool port: System message")
    st, res = _post(port, "/admin/sysmsg", {"text": "Restart in 5 min"}, token="wrong")
    check("a request without the port's token is refused (403)", st == 403, repr(st))
    sent[:] = []
    st, res = _post(port, "/admin/sysmsg", {"text": "  Restart   in 5 min\x00 "})
    check("the message is accepted for the one session in the world",
          st == 200 and res.get("ok") and "1 player" in res.get("msg", ""), repr(res))
    extrun.ext_pump(ctx)
    e00b = [b for m, b in sent if m == 0xE00B]
    check("the session's own pump pushes it as 0xE00B, once",
          len(e00b) == 1, repr([hex(m) for m, _ in sent]))
    if e00b:
        title, _, rest = e00b[0].partition(b"\0")
        text, _, tail = rest.partition(b"\0")
        check("0xE00B [cstr title][cstr text], NUL and spaces cleaned",
              (title, text, tail) == (b"Server", b"Restart in 5 min", b""),
              repr(e00b[0]))
    for bad, why in (({"text": "   "}, "blank message"), ({"text": 7}, "number for a message"),
                     ({"text": "x" * 201}, "message over 200 characters"),
                     ({"text": "@clear@"}, "message of the banner's close word")):
        sent[:] = []
        st, res = _post(port, "/admin/sysmsg", bad)
        extrun.ext_pump(ctx)
        check("a %s is refused and nothing is sent" % why,
              st == 400 and not res.get("ok") and not sent, repr((st, res, sent)))

    print("tool port: Start a war")
    for bad, why in (({"area": "17; !campaign end", "nation": "3"}, "a command in the field"),
                     ({"area": "-17", "nation": "3"}, "a negative field"),
                     ({"area": "0x11", "nation": "3"}, "a hex field"),
                     ({"area": " 17", "nation": "3"}, "a padded field"),
                     ({"area": "1700", "nation": "3"}, "a four-digit field"),
                     ({"area": True, "nation": "3"}, "a true/false field"),
                     ({"area": "17", "nation": "3.0"}, "a decimal nation"),
                     ({"area": "17"}, "a missing nation"),
                     ({"area": "96", "nation": "3"}, "a field that does not exist"),
                     ({"area": "39", "nation": "3"}, "a capital")):
        asked[:] = []
        st, res = _post(port, "/admin/war", bad)
        check("%s is refused before fecampaign is asked" % why,
              st == 400 and not res.get("ok") and not asked
              and fecampaign.phase_of(17) == fecampaign.PEACE, repr((st, res, asked)))
    st, res = _post(port, "/admin/war", b"[17, 3]")
    check("a body that is not an object is refused", st == 400, repr((st, res)))
    asked[:] = []
    st, res = _post(port, "/admin/war", {"area": "17", "nation": "1"})
    check("a nation attacking its own field is refused by the keep-vote rules",
          st == 200 and not res.get("ok") and asked == [(17, 1)]
          and "already holds" in res.get("msg", "")
          and fecampaign.phase_of(17) == fecampaign.PEACE, repr((res, asked)))
    st, res = _post(port, "/admin/war", {"area": "17", "nation": "9"})
    check("a nation the server does not have is refused",
          not res.get("ok") and fecampaign.phase_of(17) == fecampaign.PEACE, repr(res))

    print("tool port: the player picker and Give Rings")
    import felobby
    from world import character as _ch
    store = {"member:7": [{"charid": 0x101, "name": "Wren", "ring": 3},
                          {"charid": 0x102, "name": "Arden"}],
             "member:8": [{"charid": 0x201, "name": "Lexa"}]}
    saved = (_ch._ALL_CHARS_FN, felobby.update_roster, felobby._default_store)
    _ch._ALL_CHARS_FN = lambda: [(a, c["charid"], c["name"]) for a, r in store.items() for c in r]
    felobby._default_store = lambda: "test-store"

    def _update(path, acct, mutate):
        roster = store.get(acct)
        return None if roster is None else mutate(roster)
    felobby.update_roster = _update
    try:
        st, res = _get(port, "/admin/players.json?q=AR&limit=5")
        check("players.json finds Arden by part of the name, any case",
              st == 200 and [p["id"] for p in res.get("players", [])] == ["Arden"], repr(res))
        st, res = _get(port, "/admin/players.json?q=&limit=2")
        check("...an empty search lists characters, up to the limit",
              st == 200 and len(res.get("players", [])) == 2, repr(res))
        st, res = _post(port, "/admin/rings", {"name": "wren", "amount": "40"})
        check("Give Rings adds to the stored balance (3 -> 43)",
              st == 200 and res.get("ok") and store["member:7"][0]["ring"] == 43
              and "43 Rings" in res.get("msg", ""), repr(res))
        args.ring = 1
        st, res = _post(port, "/admin/rings", {"name": "Lexa", "amount": "5"})
        check("...a character with no stored Rings starts from --ring (1 + 5)",
              st == 200 and store["member:8"][0]["ring"] == 6, repr((res, store["member:8"])))
        for bad, why in (({"name": "Wren", "amount": "0"}, "zero Rings"),
                         ({"name": "Wren", "amount": "1000"}, "four digits of Rings"),
                         ({"name": "Wren", "amount": "-5"}, "a negative amount"),
                         ({"name": "Nobody", "amount": "5"}, "a name nobody has"),
                         ({"amount": "5"}, "no name")):
            st, res = _post(port, "/admin/rings", bad)
            check("%s is refused and nothing is written" % why,
                  st == 400 and not res.get("ok") and store["member:7"][0]["ring"] == 43,
                  repr((st, res)))
    finally:
        _ch._ALL_CHARS_FN, felobby.update_roster, felobby._default_store = saved
        args.ring = None

    print("tool port: summon test mode")
    import feunit
    args.declare_accepts, args.summon_cost, args.summon_dragon_death = 4, "2006", 0.25
    st, res = _post(port, "/admin/testmode", {"on": "maybe"})
    check("test mode refuses anything but on / off", st == 400 and not res.get("ok"), repr(res))
    st, res = _post(port, "/admin/testmode", {"on": "on"})
    check("test mode on: one vote, free summons, a Dragon on every death",
          st == 200 and res.get("ok") and args.declare_accepts == 1
          and feunit.summon_cost(args, 3) == (0, 0) and args.summon_dragon_death == 1.0,
          repr((res, args.declare_accepts, feunit.summon_cost(args, 3))))
    _post(port, "/admin/testmode", {"on": "on"})
    st, res = _post(port, "/admin/testmode", {"on": "off"})
    check("...and off puts the command line's values back, even after a second on",
          res.get("ok") and (args.declare_accepts, args.summon_cost, args.summon_dragon_death)
          == (4, "2006", 0.25) and feunit.summon_cost(args, 3)[0] == 40, repr(res))
    st, res = _post(port, "/admin/testmode", {"on": "off"})
    check("...off again says it was not on", "not on" in res.get("msg", ""), repr(res))

    print("tool port: calendar.json")
    st, res = _get(port, "/calendar.json?from=0&to=86400", token="wrong")
    check("calendar.json without the port's token is refused (403)", st == 403, repr(st))
    for q in ("", "?from=5", "?from=a&to=9", "?from=9&to=5", "?from=0&to=%d" % (401 * 86400)):
        st, res = _get(port, "/calendar.json" + q)
        check("calendar.json%s is refused (400)" % q, st == 400 and not res.get("ok"), repr((st, res)))
    st, res = _get(port, "/calendar.json?from=0&to=%d" % (300 * 86400))
    check("all quiet: 200 and no events", st == 200 and res.get("events") == [], repr(res))
    # SE's timed events (world/timedevents.py, fedata/fe-timed-events.tsv):
    # October 2006 holds the shop sale's end, the Double Up and the Dragon
    # feast, at SE's own JST times; the quests are not served, so not shown
    import calendar as _cal
    jst = lambda *t: _cal.timegm(t + (0,) * (6 - len(t))) - 9 * 3600   # noqa: E731
    args.timed_events = "table"
    try:
        st, res = _get(port, "/calendar.json?from=%d&to=%d" % (jst(2006, 10, 1), jst(2006, 11, 1)))
    finally:
        args.timed_events = "off"
    got = [(e["id"], e["start"], e["end"]) for e in res.get("events") or []]
    check("SE's timed events: sale, Double Up, Dragon feast (October 2006)",
          st == 200 and got == [
              ("fe-shop-sale-2006", jst(2006, 9, 21, 15, 15), jst(2006, 10, 5, 16)),
              ("fe-double-up-2006", jst(2006, 10, 5, 16), jst(2006, 10, 19, 14)),
              ("fe-dragon-feast-2006", jst(2006, 10, 19, 15, 30), jst(2006, 10, 26, 14))],
          repr(got))


def panel(args, port, ctx, sent, asked):
    """The admin panel's own action code, through its real proxy."""
    import fecampaign
    if not os.path.isfile(os.path.join(OPENLOBBY, "services", "adminmodules", "fe.py")):
        print("panel: SKIP (no OpenLobby checkout at %s)" % OPENLOBBY)
        return
    sys.path.insert(0, os.path.join(OPENLOBBY, "services"))
    os.environ["POL_ADMIN_GAME_FE_URL"] = "http://127.0.0.1:%d" % port
    os.environ["POL_ADMIN_GAME_FE_TOKEN"] = TOKEN
    import adminmodules
    import admingames
    m = adminmodules.load(reload=True).get("fe")
    check("the panel loads the fe module", m is not None)
    if m is None:
        return
    calls = []
    real = admingames.forward

    def counted(*a, **kw):
        calls.append(a[1])
        return real(*a, **kw)
    admingames.forward = counted
    try:
        print("panel: System message")
        ann = m.action("announce")
        check("the maintenance warning finds System message as FE's announce",
              ann is not None and ann.id == "sysmsg")
        sent[:] = []
        ok, msg = ann.run(m, {"text": "Maintenance in 10 minutes"}, "Wren")
        check("System message reaches the world through the proxy",
              ok and calls == ["admin/sysmsg"], repr((ok, msg, calls)))
        extrun.ext_pump(ctx)
        check("...and the session pushes it as 0xE00B",
              [b for mid, b in sent if mid == 0xE00B]
              == [b"Server%sMaintenance in 10 minutes%s" % (NUL, NUL)], repr(sent))
        calls[:] = []
        ok, msg = ann.run(m, {"text": ""}, "Wren")
        check("an empty message is refused on the panel, no request made",
              not ok and not calls and msg, repr((ok, msg, calls)))

        print("panel: Start a war")
        war = m.action("war")
        check("Start a war is marked dangerous, with two text fields",
              war is not None and war.danger
              and [f["id"] for f in war.fields] == ["area", "nation"])
        for bad in ({"area": "17;x", "nation": "3"}, {"area": "0", "nation": "3"},
                    {"area": "96", "nation": "3"}, {"area": "17", "nation": "6"},
                    {"area": "17", "nation": "-1"}, {"area": "", "nation": "3"},
                    {"area": "17"}, {"area": "١٧", "nation": "3"}):
            calls[:] = []
            asked[:] = []
            ok, msg = war.run(m, bad, "Wren")
            check("panel refuses %r with no request made" % (bad,),
                  not ok and msg and not calls and not asked, repr((ok, msg, calls)))
        calls[:] = []
        asked[:] = []
        ok, msg = war.run(m, {"area": " 17 ", "nation": "3"}, "Wren")
        row = fecampaign.state_of(17)
        check("Start a war 17 / 3 puts field 17 into War Prep, nation 3 attacking",
              ok and calls == ["admin/war"] and asked == [(17, 3)]
              and row["phase"] == fecampaign.PREP and row["atk"] == 3, repr((ok, msg, row)))
        check("...and says so in a sentence naming the nation",
              "Elsord" in msg and "War Prep" in msg, msg)
        ok, msg = war.run(m, {"area": "17", "nation": "3"}, "Wren")
        check("a second declaration on the same field is refused with the reason",
              not ok and "already" in msg, msg)

        print("panel: Give Rings and the player picker")
        import felobby
        from world import character as _ch
        store = {"member:7": [{"charid": 0x101, "name": "Wren", "ring": 2}]}
        saved = (_ch._ALL_CHARS_FN, felobby.update_roster, felobby._default_store)
        _ch._ALL_CHARS_FN = lambda: [(a, c["charid"], c["name"]) for a, r in store.items() for c in r]
        felobby._default_store = lambda: "test-store"
        felobby.update_roster = lambda path, acct, mutate: (
            None if acct not in store else mutate(store[acct]))
        try:
            rings = m.action("rings")
            check("Give Rings asks for a character with the player picker, and an amount",
                  rings is not None and [(f["id"], f.get("type")) for f in rings.fields]
                  == [("name", "player"), ("amount", "text")], rings and rings.fields)
            calls[:] = []
            found = adminmodules.find_players("fe", "wr", 5)
            check("the picker finds Wren through the world's admin/players.json",
                  [p["id"] for p in found] == ["Wren"] and calls == ["admin/players.json"],
                  repr((found, calls)))
            calls[:] = []
            ok, msg = rings.run(m, {"name": "Wren", "amount": "10"}, "Arden")
            check("Give Rings reaches the world through the proxy: 2 -> 12",
                  ok and calls == ["admin/rings"] and store["member:7"][0]["ring"] == 12, msg)
            for bad in ({"name": "", "amount": "5"}, {"name": "Wren", "amount": "1000"},
                        {"name": "Wren", "amount": "5;x"}):
                calls[:] = []
                ok, msg = rings.run(m, bad, "Arden")
                check("panel refuses %r with no request made" % (bad,),
                      not ok and msg and not calls and store["member:7"][0]["ring"] == 12,
                      repr((ok, msg, calls)))
        finally:
            _ch._ALL_CHARS_FN, felobby.update_roster, felobby._default_store = saved

        print("panel: Calendar")
        calendar(args, port, m, calls)
    finally:
        admingames.forward = real


def calendar(args, port, m, calls):
    """Field 17 is in War Prep (Start a war above): the panel's Calendar shows
    that phase to its end, then the war and the truce at their lengths."""
    import time
    import fecampaign
    saved = (args.war_length_ms, args.war_truce_ms)
    args.war_length_ms, args.war_truce_ms = 3600000, 600000
    try:
        row = fecampaign.state_of(17)
        until = int(row["until"])
        lo, hi = int(time.time()) - 86400, int(time.time()) + 86400
        calls[:] = []
        ents = m.calendar(m, lo, hi)
        got = [(e["kind"], e["start"], e["end"]) for e in ents]
        check("the module asks the tool port for calendar.json, once",
              calls == ["calendar.json"], repr(calls))
        check("War Prep to its end, then the war and the truce at their lengths",
              got == [("war prep", until - 65, until), ("war", until, until + 3600),
                      ("truce", until + 3600, until + 4200)], repr(got))
        check("...titled by the field, naming the attacking nation",
              ents and ents[0]["title"].startswith("War Prep: ") and "Elsord attacks" in ents[0]["about"]
              and "Expected" in ents[1]["about"] and "Expected" not in ents[0]["about"],
              repr(ents[:1]))
        ents = m.calendar(m, until + 3600, until + 3700)
        check("only what touches the range: the truce alone",
              [e["kind"] for e in ents] == ["truce"], repr(ents))
        # the same war once it is under way, and then in its truce
        now = time.time()
        with fecampaign._LOCK:
            fecampaign._STATE[17].update(phase=fecampaign.WAR, war_at=now - 600, until=now + 3000)
        got = [(e["kind"], e["start"], e["end"]) for e in m.calendar(m, lo, hi)]
        check("at war: from war_at to its end at the latest, then the truce",
              got == [("war", int(now - 600), int(now + 3000)),
                      ("truce", int(now + 3000), int(now + 3600))], repr(got))
        with fecampaign._LOCK:
            fecampaign._STATE[17].update(phase=fecampaign.TRUCE, until=now + 300)
        got = [(e["kind"], e["start"], e["end"]) for e in m.calendar(m, lo, hi)]
        check("in its truce: the truce alone, its length back from its end",
              got == [("truce", int(now + 300 - 600), int(now + 300))], repr(got))
        with fecampaign._LOCK:
            fecampaign._STATE[17].update(phase=fecampaign.PREP, until=row["until"], war_at=0.0)
        args.campaign = "off"
        check("with the campaign off there is nothing on the calendar",
              m.calendar(m, lo, hi) == [])
        args.campaign = "on"
        real = os.environ.pop("POL_ADMIN_GAME_FE_URL")
        try:
            check("a panel with no FE tools set up has no FE calendar, and asks nothing",
                  m.calendar(m, lo, hi) == [])
        finally:
            os.environ["POL_ADMIN_GAME_FE_URL"] = real
    finally:
        args.war_length_ms, args.war_truce_ms = saved
        args.campaign = "on"


def main():
    feworld.load_extensions(["fegm", "fecampaign"])
    import fecampaign
    tmp = tempfile.mkdtemp(prefix="fe-admin-")
    args = _args(tmp)
    sent, asked = [], []
    saved = (feworld.send, fecampaign.admin_declare)
    feworld.send = (lambda conn, outbound, body, mode, be, echo=True, prefix=4:
                    sent.append((struct.unpack_from(">IH", body, 0)[1], body[6:])))
    real_declare = fecampaign.admin_declare

    def counted(a, area, nation, actor=""):
        asked.append((area, nation))
        return real_declare(a, area, nation, actor=actor)
    fecampaign.admin_declare = counted
    port = _free_port()
    srv = None
    try:
        feworld.territory_load(args)
        fecampaign.load(args)
        feworld._TLS.session = {"in_field": True, "field": 39, "room": -1}
        chat._chat_room_join()
        ctx = feworld.Ctx(None, None, "ecb", False, args)
        srv = fedevtool.start(port, "127.0.0.1", TOKEN, lambda area=None: {},
                              routes=devtool.admin_routes(args))
        tool_port(args, port, ctx, sent, asked)
        panel(args, port, ctx, sent, asked)
    finally:
        feworld.send, fecampaign.admin_declare = saved
        chat._chat_room_leave()
        if srv is not None:
            srv.shutdown()
    if FAILS:
        print("[fe_admin_test] FAILED: %s" % FAILS)
        sys.exit(1)
    print("[fe_admin_test] OK")


if __name__ == "__main__":
    main()
