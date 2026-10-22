#!/usr/bin/env python3
"""fe_admin_test.py -- the OpenLobby admin panel's two Fantasy Earth actions,
System message and Start a war, from the panel's own action code through its
proxy (admingames.call) and the world server's tool port (fedevtool) to the
GM paths they end in:

    System message  -> devtool.admin_sysmsg -> fegm.announce -> ext_post
                       "gm.sysmsg" -> on_relay_sysmsg -> 0xE00B on a session
    Start a war     -> devtool.admin_war -> fecampaign.admin_declare ->
                       can_declare + declare (`!campaign declare AREA NATION`)

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
             shop_types="weapon=1,armor=2,item=3,ring=4")
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
    finally:
        admingames.forward = real


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
