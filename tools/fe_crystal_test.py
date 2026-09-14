#!/usr/bin/env python3
"""fe_crystal_test.py -- the giant crystals, 2006-style, end to end.

Run from services/:  python ../tools/fe_crystal_test.py

2026-09-11. Until today every character was handed `--crystal 4321` and no
war ever served a giant crystal (`--war-crystals 0`, "the crystal model
lacks _stand"). This drives the loop through the objects prod uses
(feworld.Ctx, the thread-local session, the ext_post relays, the pump):

  * deposits: served by DEFAULT in war prep/war as type-7 buildings, model 6
    `crystal`, holding 700 (+0x778/+0x774), withheld until field_ready,
    0x1004'd after; one BESIDE EACH BASE and one in the middle on a
    shipped monster spawn point (2026-09-11, the 2006 wikis' 城横クリ);
    --crystal-pos replaces them
  * the draw: 0x2081 on a deposit credits 1, drains the deposit, re-serves
    what is left on the building channel (0x2024 bit 0x4) to the drawer AND
    to others in the field; an unknown object or a spent deposit gives
    nothing; a re-entry shows the drained amount
  * the reset: crystals count only during war -- the fighters and drawers go
    to 0 when it ends, a character in no war carries 0, the old 4321 seed is
    zeroed once and a new character starts at 0; --crystal-reset off keeps
    the old behaviour
  * territory: 0x2007 only inside your base's/obelisks' circle, never in the
    enemy's; an obelisk extends it, a destroyed one takes it back
"""
import os
import queue
import struct
import sys
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import fegamedata   # noqa: E402
import feworld      # noqa: E402

CHECKS = []
OUT = sys.__stdout__
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-66s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label + (": " + detail if detail else ""))


def _args(**kw):
    """Prod's campaign knobs; the crystal ones are left to their DEFAULTS
    (read with getattr) so the test sees what a fresh deploy does."""
    a = dict(campaign="on", campaign_auto="off", war_cap=50, war_prep_ms=60000,
             war_length_ms=60000, war_truce_ms=60000, war_decide="keeps",
             campaign_file=None, territory_file=None, territory="",
             war="offensive", war_fields="0,0,0,0", war_deadline_ms=60000,
             war_clock="off", war_trace="off", war_start_fields="0,0",
             war_cycle="on", seq_mode="count", world_prefix=4, unit_id="0",
             campaign_notify_all=False, keeps="on", keep_hp=3000,
             declare_accepts=1, declare_rejects=3, declare_timeout_ms=60000,
             beginner_fields="on", keep_min_cells=30, foreign_join="smaller",
             war_ring_win=2, war_ring_dmg=1000, king_message=[],
             force_population="off", crystal=4321, ring=1, gold=None,
             total_score=None, build="real", build_costs="off",
             build_phase="war", build_caps="4=1", building_base=3000,
             combat="on", hit_damage=400, world="dat", islands=6, groups=1,
             field_nations=None, capital="1:39")
    a.update(kw)
    return types.SimpleNamespace(**a)


SENT = []            # (charid of the sending session, unit, mid, body)
CHARS = {}           # (account, charid) -> stored character


def _send(conn, outbound, body, mode, be, echo=True, prefix=4):
    unit, mid = struct.unpack_from(">IH", body, 0)
    SENT.append((feworld._SESSION.get("charid"), unit, mid, body[6:]))


def _char():
    s = feworld._SESSION
    return CHARS.setdefault((s.get("account"), s.get("charid")), {})


class Player:
    def __init__(self, name, charid, nation, area, args):
        self.name, self.charid, self.args = name, charid, args
        self.key = ("fake-crys", charid)
        self.session = {"account": "acct-" + name, "charid": charid,
                        "in_field": True, "field": area, "room": -1,
                        "campaign_nation": nation, "field_ready": True}
        CHARS[("acct-" + name, charid)] = {"charid": charid, "force": nation}
        with feworld._CHAT_ROOM_LOCK:
            feworld._CHAT_ROOM[self.key] = {"q": queue.Queue(),
                                            "session": self.session,
                                            "name": name, "ext": queue.Queue()}

    @property
    def char(self):
        return CHARS[("acct-" + self.name, self.charid)]

    def act(self, fn, *a):
        saved = getattr(feworld._TLS, "session", None)
        feworld._TLS.session = self.session
        try:
            return fn(feworld.Ctx(None, None, "ecb", False, self.args), *a)
        finally:
            feworld._TLS.session = saved

    def send(self, mid, body=b""):
        return self.act(lambda ctx: feworld.EXT_HANDLERS[mid](
            ctx, struct.pack(">H", mid) + body))

    def pump(self):
        import fecampaign
        return self.act(fecampaign.pump)

    def leave(self):
        with feworld._CHAT_ROOM_LOCK:
            feworld._CHAT_ROOM.pop(self.key, None)


def deliver(players):
    for p in players:
        q = feworld._CHAT_ROOM.get(p.key, {}).get("ext")
        while q is not None:
            try:
                kind, payload = q.get_nowait()
            except queue.Empty:
                break
            p.act(feworld.EXT_RELAY[kind], payload)


def got(cid, mid, unit=None):
    return [b for c, u, m, b in SENT if c == cid and m == mid
            and (unit is None or u == unit)]


def crystal_adds(cid):
    """(obj, type, model, cur, max, gx, gz) of every type-1 0x1006 of type 7
    this session was sent, decoded in the type-1 arm's read order."""
    out = []
    for c, u, m, b in SENT:
        if c != cid or m != 0x1006:
            continue
        n, obj, kind = struct.unpack_from(">HIB", b, 0)
        if kind != 1:
            continue
        btype, model, _f384, _side = struct.unpack_from(">HHIB", b, 7)
        cur, mx = struct.unpack_from(">ii", b, 16)
        gx, _gy, gz = struct.unpack_from(">HHH", b, 24)
        if btype == 7:
            out.append((obj, btype, model, cur, mx, gx, gz))
    return out


def main():
    feworld.load_extensions()
    import fecampaign
    import fewar
    tmp = os.environ.get("TEMP", ".")
    cf = os.path.join(tmp, "fe_crys_c_%d.json" % os.getpid())
    tf = os.path.join(tmp, "fe_crys_t_%d.json" % os.getpid())
    for p in (cf, tf):
        if os.path.exists(p):
            os.remove(p)
    args = _args(campaign_file=cf, territory_file=tf)
    saved = (feworld.send, feworld._load_char_field, feworld._store_char_field,
             fecampaign._update_char)
    feworld.send = _send
    feworld._load_char_field = lambda a, k, d=None: _char().get(k, d)
    feworld._store_char_field = lambda a, k, v: (_char().__setitem__(k, v), True)[1]

    def upd(account, charid, mutate):
        c = CHARS.get((account, charid))
        if c is None:
            return False
        mutate(c)
        return True
    fecampaign._update_char = upd
    players = []
    try:
        feworld.territory_load(args)
        fecampaign.load(args)
        # nation 3 attacks 17 (nation 1's), as in fe_declare_test
        alice = Player("Alice", 41, 3, 17, args)
        bob = Player("Bob", 42, 3, 17, args)
        eve = Player("Eve", 45, 1, 17, args)          # the holder's nation
        ian = Player("Ian", 46, 3, 39, args)          # in a capital, no war
        players = [alice, bob, eve, ian]
        base = int(args.__dict__.get("keep_base", 2900)) + 10

        say("the defaults a fresh deploy runs with")
        ap = __import__("argparse").ArgumentParser()
        fecampaign.add_args(ap)
        d = ap.parse_args([])
        check("--war-crystals defaults to 3 deposits (was 0: none ever served)",
              d.war_crystals == 3, repr(d.war_crystals))
        check("...each holding 700 (the Crystal row's SHIPPED +0xb0; FFSKY "
              "said 532), model 6 `crystal`, draw 1 per 0x2081",
              d.war_crystal_hp == 700 and d.war_crystal_model == 6
              and d.crystal_draw == 1)
        check("...crystals reset with the war, deposits drain, territory on "
              "(obelisk radius 38 cells = 95 u = ~1.2 minimap squares, RoD)",
              d.crystal_reset == "war" and d.crystal_deplete == "on"
              and d.build_territory == "on" and d.obelisk_radius == 38)
        for k in ("war_crystals", "war_crystal_hp", "war_crystal_model",
                  "crystal_draw", "crystal_war_max", "crystal_carry_max",
                  "crystal_reset", "crystal_deplete", "crystal_place",
                  "crystal_pos", "build_territory", "obelisk_radius",
                  "base_radius", "crystal_base_gap", "crystal_heal_deplete"):
            setattr(args, k, getattr(d, k))

        say("legacy: the old --crystal 4321 seed does not survive")
        ian.char["crystal"] = 4321                      # stored before today
        del SENT[:]
        ian.pump()
        check("a stored 4321 (no crystal_epoch) goes to 0 at the first pump, "
              "re-served on 0x2035", ian.char.get("crystal") == 0
              and ian.char.get("crystal_epoch") == 1
              and struct.unpack(">II", got(46, 0x2035)[-1]) == (2, 0),
              repr((ian.char, got(46, 0x2035))))
        del SENT[:]
        ian.pump()
        check("...once: the next pump reads nothing and sends nothing",
              not got(46, 0x2035))
        # a brand-new character with nothing stored, prod still seeding 4321
        bob.pump()
        check("a NEW character (nothing stored) starts with 0 whatever --crystal "
              "seeds: a real stored 0", bob.char.get("crystal") == 0
              and bob.act(lambda ctx: feworld._seeded_value(
                  args, "crystal", args.crystal)) == 0, repr(bob.char))

        say("deposits: served in war prep, by default, after field_ready")
        row = fecampaign.declare(args, 17, 3)
        check("war declared on 17 (prep)", row and row["phase"] == fecampaign.PREP)
        alice.session["field_ready"] = False
        del SENT[:]
        alice.pump()
        check("withheld while building.pak is still mounting (field_ready "
              "unset -- the +0xE1531 race)", not crystal_adds(41))
        alice.session["field_ready"] = True
        del SENT[:]
        alice.pump()
        adds = crystal_adds(41)
        check("three giant crystals: TYPE 7 (the draw search's kind), model 6, "
              "ids --keep-base+10..", [a[0] for a in adds] == [base, base + 1, base + 2]
              and all(a[1] == 7 and a[2] == 6 for a in adds), repr(adds))
        check("...each holding 700/700 (+0x778 current, +0x774 max)",
              all(a[3] == 700 and a[4] == 700 for a in adds), repr(adds))
        g = feworld.keep_grids(17)
        hmap = fegamedata.areas()[17]["hmap"]
        pool = {(feworld.world_to_grid(p[0]), feworld.world_to_grid(p[2]))
                for a2, r2 in fegamedata.areas().items() if r2["hmap"] == hmap
                for p in fegamedata.spawn_points(a2)}
        cells = [(a[5], a[6]) for a in adds]
        check("...the MIDDLE one on a SHIPPED monster spawn point of that "
              "terrain (reachable ground), three different cells",
              cells[0] in pool and len(set(cells)) == 3,
              repr((cells, sorted(pool))))

        def near(c, side):
            o = g["atk" if side == "def" else "def"]
            s = g[side]
            return (c[0] - s[0]) ** 2 + (c[1] - s[1]) ** 2 < \
                (c[0] - o[0]) ** 2 + (c[1] - o[1]) ** 2

        def dist(c, side):
            return ((c[0] - g[side][0]) ** 2 + (c[1] - g[side][1]) ** 2) ** 0.5
        check("...one BESIDE the castle and one beside the keep (the 2006 "
              "wikis' 城横クリ: within 2 x --crystal-base-gap 16 cells), none "
              "on a base (>= 12 cells)",
              near(cells[1], "def") and dist(cells[1], "def") <= 32
              and near(cells[2], "atk") and dist(cells[2], "atk") <= 32
              and all(min(dist(c, "def"), dist(c, "atk")) >= 12 for c in cells),
              repr((cells, g)))
        check("...and the middle one far from both (the contested deposit)",
              min(dist(cells[0], "def"), dist(cells[0], "atk")) > 32,
              repr((cells, g)))
        check("the deposits are the WAR's, persisted in the campaign row",
              fecampaign.state_of(17)["crystals"] == [[700, 700]] * 3)
        del SENT[:]
        alice.pump()
        check("...served once per field visit", not crystal_adds(41))
        args.crystal_pos = ["17:100:120", "17:140:90"]
        check("--crystal-pos AREA:GX:GZ replaces the derived places",
              fecampaign.crystal_grids(17, 3, args) == [(100, 120), (140, 90)])
        args.crystal_pos = []

        say("the draw: 1 per 0x2081, out of the deposit, shown to the field")
        with fecampaign._LOCK:
            fecampaign._STATE[17]["phase"] = fecampaign.WAR
            fecampaign._STATE[17]["until"] = time.time() + 60
        bob.pump()
        alice.pump()
        bob.session.pop("crystal_sig", None)
        del SENT[:]
        alice.send(0x2081, struct.pack(">I", base + 1))
        check("a draw at war credits ONE (the client's own 5 s cadence is the "
              "rate): 0 -> 1 on 0x2035", alice.char["crystal"] == 1
              and struct.unpack(">II", got(41, 0x2035)[-1]) == (2, 1),
              repr(alice.char))
        hp = got(41, 0x2024, unit=base + 1)
        check("...the deposit drains 700 -> 699, re-served to the drawer on the "
              "building channel (0x2024 bit 0x4, u16 NEW value, the obj in the "
              "header)", fecampaign.state_of(17)["crystals"][1] == [699, 700]
              and hp and struct.unpack(">IIh", hp[-1]) == (4, 0, 699), repr(hp))
        deliver(players)
        check("...and to Bob in the same field on his own thread -- not to Ian "
              "(a capital)", struct.unpack(">IIh", got(42, 0x2024, unit=base + 1)[-1])
              == (4, 0, 699) and not got(46, 0x2024))
        del SENT[:]
        alice.send(0x2081, struct.pack(">I", 2999))
        check("an object that is none of this war's giant crystals gives nothing",
              alice.char["crystal"] == 1 and not got(41, 0x2035))
        with fecampaign._LOCK:
            fecampaign._STATE[17]["crystals"][0] = [1, 700]
        bob.send(0x2081, struct.pack(">I", base))
        check("the last crystal in a deposit: Bob 0 -> 1, the deposit 1 -> 0",
              bob.char["crystal"] == 1
              and fecampaign.state_of(17)["crystals"][0] == [0, 700])
        del SENT[:]
        bob.send(0x2081, struct.pack(">I", base))
        check("...a SPENT deposit gives nothing (the client greys it at 0 and "
              "stops asking; a late ask is refused)", bob.char["crystal"] == 1
              and not got(42, 0x2035))
        bob.session.pop("crystals_area", None)
        del SENT[:]
        bob.pump()
        again = {a[0]: a[3] for a in crystal_adds(42)}
        check("a re-entry serves what is LEFT: 0, 699, 700",
              again == {base: 0, base + 1: 699, base + 2: 700}, repr(again))
        args.crystal_draw = 30
        with fecampaign._LOCK:
            fecampaign._STATE[17]["crystals"][2] = [40, 700]
        alice.send(0x2081, struct.pack(">I", base + 2))
        check("the caps still hold: 20 per war (alice 1 + 19), the deposit "
              "gives only that", alice.char["crystal"] == 20
              and fecampaign.state_of(17)["crystals"][2] == [21, 700],
              repr((alice.char, fecampaign.state_of(17)["crystals"])))
        args.crystal_draw = 1

        say("crystals count only during war")
        del SENT[:]
        alice.session["field"] = 39                   # to the capital, mid-war
        alice.pump()
        check("a drawer who walks to the capital mid-war KEEPS them (still in "
              "that war)", alice.char["crystal"] == 20 and not got(41, 0x2035))
        ian.char["crystal"] = 7                       # e.g. a trade, no war
        ian.session.pop("crystal_sig", None)
        ian.pump()
        check("a character in NO war carrying 7 goes to 0",
              ian.char["crystal"] == 0)
        alice.session["field"] = 17
        alice.pump()                                  # back: shown again
        eve.pump()
        eve.send(0x2018, struct.pack(">I", 0))       # the holder enlists
        eve.char["crystal"] = 5                       # got some by trade
        eve.session.pop("crystal_sig", None)
        eve.pump()
        check("an enlisted defender carrying 5 keeps them while the war runs",
              eve.char["crystal"] == 5)
        with fecampaign._LOCK:
            fecampaign._STATE[17]["until"] = time.time() - 1
            fecampaign._STATE[17]["keeps"]["def"][0] = 0     # the castle falls
        del SENT[:]
        alice.pump()                                  # WAR -> TRUCE, settle
        check("the war ends (castle fallen) -> Truce",
              fecampaign.phase_of(17) == fecampaign.TRUCE)
        check("...every fighter AND drawer is reset in the store -- Eve "
              "(member), Bob (drew, never enlisted), Alice",
              eve.char["crystal"] == 0 and bob.char["crystal"] == 0
              and alice.char["crystal"] == 0,
              repr((eve.char, bob.char, alice.char)))
        check("...Alice's own client is told 0 (0x2035) and the deposits are "
              "taken down (0x1004)", struct.unpack(">II", got(41, 0x2035)[-1]) == (2, 0)
              and got(41, 0x1004, unit=base), repr(SENT[-8:]))
        del SENT[:]
        bob.pump()
        check("...Bob's session sees the war end and re-serves his stored 0",
              struct.unpack(">II", got(42, 0x2035)[-1]) == (2, 0))
        with fecampaign._LOCK:
            fecampaign._STATE[17]["until"] = time.time() - 1
        alice.pump()                                  # TRUCE -> PEACE
        check("Peace empties the deposits; the next war's are full again",
              fecampaign.state_of(17)["crystals"] == [])

        say("--crystal-reset off keeps the old behaviour")
        args.crystal_reset = "off"
        ian.char["crystal"] = 9
        ian.session.pop("crystal_sig", None)
        ian.pump()
        check("a character in no war keeps 9", ian.char["crystal"] == 9)
        args.crystal_reset = "war"

        say("territory: build only inside your base's and obelisks' circles")
        feworld.territory_set(17, 1, args)
        fecampaign.declare(args, 17, 3)
        with fecampaign._LOCK:
            fecampaign._STATE[17]["phase"] = fecampaign.WAR
            fecampaign._STATE[17]["members"]["acct-Alice|41"] = {
                "a": "acct-Alice", "c": 41, "side": "atk", "dmg": 0}
        g = feworld.keep_grids(17)
        k = g["atk"]
        far = (k[0] + 60 if k[0] < 128 else k[0] - 60, k[1])

        def build(p, btype, model, gx, gz):
            body = struct.pack(">HHHHHB", btype, model, gx, 0, gz, 0) + \
                struct.pack(">fff", 0.0, 0.0, 0.0)
            del SENT[:]
            p.send(0x2007, body)
            return [(m, b) for c, u, m, b in SENT if c == p.charid]
        alice.char["crystal"] = 50
        r = build(alice, 6, 8, far[0], far[1])
        check("an obelisk 60 cells from the attacker's keep (radius 38) -> NG "
              "16 'Can't build here.'", r == [(0x100A, struct.pack(">I", 16))], repr(r))
        step = (k[0] + 30 if k[0] < 128 else k[0] - 30, k[1])
        r = build(alice, 6, 8, step[0], step[1])
        check("...30 cells out, inside the keep's circle -> OK + the building",
              [m for m, b in r][:2] == [0x1009, 0x1006], repr(r))
        r = build(alice, 6, 8, far[0], far[1])
        check("...and now the 60-cell spot is inside THAT obelisk's circle -> OK",
              [m for m, b in r][:2] == [0x1009, 0x1006], repr(r))
        check("the side's territory is its keep and both obelisks",
              len(fecampaign.territory_of(args, 17, "atk")) == 3)
        c = g["def"]
        spot = (c[0], c[1] + 5)
        with fecampaign._LOCK:          # say the attackers had an obelisk there
            fecampaign._STATE[17]["obelisks"]["atk"].append([spot[0], spot[1] + 20])
        code, why = alice.act(lambda ctx: fecampaign.build_check(
            args, 17, 0, grid=spot))
        r = build(alice, 0, 1, spot[0], spot[1])
        check("a tower inside your own circle but beside the ENEMY castle -> NG "
              "16 (enemy-held land: destroy theirs first)",
              code == 16 and "enemy" in why
              and r == [(0x100A, struct.pack(">I", 16))], repr((code, why, r)))
        with fecampaign._LOCK:
            fecampaign._STATE[17]["obelisks"]["atk"].pop()
        obs = [o for o, b in alice.session.get("war_buildings", {}).items()
               if (b["gx"], b["gz"]) == step]
        alice.act(lambda ctx: fewar.destroy_building(ctx, obs[0]))
        check("a destroyed obelisk takes its circle with it",
              len(fecampaign.territory_of(args, 17, "atk")) == 2)
        args.build_territory = "off"
        r = build(alice, 0, 1, far[0] + 5, far[1] + 30)
        check("--build-territory off: anywhere (the old rule)",
              [m for m, b in r][:2] == [0x1009, 0x1006], repr(r))
    finally:
        (feworld.send, feworld._load_char_field, feworld._store_char_field,
         fecampaign._update_char) = saved
        for p in players:
            p.leave()
        for p in (cf, tf):
            try:
                os.remove(p)
            except OSError:
                pass

    bad = [l for l, ok in CHECKS if not ok]
    say("[fe_crystal_test] %s -- %d checks" % ("FAIL" if bad else "OK", len(CHECKS)))
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("[fe_crystal_test] FAIL: %s" % e)
        sys.exit(1)
