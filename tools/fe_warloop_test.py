#!/usr/bin/env python3
"""fe_warloop_test.py -- the WAR LOOP end to end, through prod's own objects.

Run from services/:  python ../tools/fe_warloop_test.py

A small world rarely has four of one nation online together, and the pieces
of the loop are otherwise only tested one at a time. This drives all of it with several sessions listed in the
chat room the way live ones are (feworld.Ctx, the thread-local session, the
ext_post relays, the pump), in the order a war happens:

  * the keep vote's quorum: SE's four, and the OURS exception --declare-quorum
    online (the nation's players online when fewer than four)
  * declare -> War Prep: 0x1018 with both nations' keys and 0x101F army ids
    (on by default now), the castle (type 15, the gauge's row) and keep
  * the gauge's two 支配領域 bars (0x1176): zero at the start, the attackers'
    share once their obelisk stands, re-said to everyone in the field once
  * War Prep -> At war: 0x1015 + 0x101D / 0x101E by side
  * the arrow tower fires at an enemy in range (pvp.hit on the victim's
    thread, the hit reported back and credited to the builder), never at its
    own side, on its interval
  * the rank gap (--rank-cost diff): declarers commit their ★, a join that
    opens the gap wider than --rank-gap is refused as that side being full
  * a kill: the ticker 0x1129 to the field, kills/deaths on the rows
  * the castle falls: truce, land change, settlement, Rings, the ranking
  * Field Out: 0x1100 from the settlement, then 0x112A -- the ranking window
    in score order, byte-exact against the client's reader 0x05112e50
  * truce -> peace: the war forgotten, the castle re-served to its new holder
  * a field CHANGE forgets what was served in the old field
"""
import os
import queue
import struct
import sys
import threading
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
    say("  %-68s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label + (": " + detail if detail else ""))


def _args(**kw):
    a = dict(campaign="on", campaign_auto="off", war_cap=50, war_prep_ms=60000,
             war_length_ms=60000, war_truce_ms=60000, war_decide="keeps",
             campaign_file=None, territory_file=None, territory="",
             war="offensive", war_fields="0,0,0,0", war_deadline_ms=60000,
             war_clock="off", war_trace="off", war_start_fields="0,0",
             war_cycle="on", seq_mode="count", world_prefix=4, unit_id="0",
             campaign_notify_all=False, keeps="on", keep_hp=3000,
             declare_accepts=4, declare_rejects=3, declare_timeout_ms=60000,
             beginner_fields="on", keep_min_cells=30, foreign_join="smaller",
             war_rewards="rod", king_message=[], force_population="off",
             war_crystals=0, crystal=5, ring=1, gold=None, total_score=None,
             crystal_reset="off", build_territory="off", build="real",
             build_costs="off", build_phase="war", build_caps="2006",
             building_base=3000, combat="on", hit_damage=400, world="dat",
             islands=6, groups=1, field_nations=None, capital="1:39",
             war_result="auto", war_result_rows=0, war_result_values="session",
             war_result_destroyed="count", war_ranking="on", war_gauge="on",
             rank_cost="diff", rank_gap=18, tower_fire="on", base_cannon="off",
             tower_range=40.0, tower_interval=5.0, tower_damage=0,
             obelisk_drain="on", build_share="on", influence="buildings",
             pvp="on", pvp_damage="table", revive_secs=8, status_effects="off",
             status_effects_wire="off", war_presence="on", war_score="on",
             # the parser's defaults since 2026-10-08 (hand-built args carry
             # no defaults of their own; fe_parser_test pins the real parser)
             war_notify_keys="on", war_army_keys="on", war_ticker="on",
             declare_quorum="accepts")
    a.update(kw)
    return types.SimpleNamespace(**a)


SENT = []            # (charid of the sending session, mid, body)
CHARS = {}           # (account, charid) -> stored character


def _send(conn, outbound, body, mode, be, echo=True, prefix=4):
    _unit, mid = struct.unpack_from(">IH", body, 0)
    SENT.append((feworld._SESSION.get("charid"), mid, body[6:]))


def _char():
    s = feworld._SESSION
    return CHARS.setdefault((s.get("account"), s.get("charid")), {})


class Player:
    """One session, listed in feworld's chat room under a fake key so
    ext_post fans out to it; `act` runs code with THIS session current."""

    def __init__(self, name, charid, nation, area, args, rank=1):
        self.name, self.charid, self.args = name, charid, args
        self.key = ("fake", charid)
        self.session = {"account": "acct-" + name, "charid": charid,
                        "in_field": True, "field": area, "room": -1,
                        "campaign_nation": nation, "field_ready": True,
                        "pres_card": {"name": name, "force": nation}}
        CHARS[("acct-" + name, charid)] = {"charid": charid, "force": nation,
                                           "name": name, "war_rank": rank}
        with feworld._CHAT_ROOM_LOCK:
            feworld._CHAT_ROOM[self.key] = {"q": queue.Queue(),
                                            "session": self.session,
                                            "name": name, "ext": queue.Queue()}

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

    def goto(self, area):
        self.session["field"] = area

    def stand(self, x, y, z):
        """What the tower's target scan reads: the 0x2023 heartbeat's three
        i16 tenths at +18 (fepvp._peer_pos / fecampaign._peer_xyz)."""
        self.session["pres_mv"] = bytes(18) + struct.pack(
            ">hhh", int(x * 10), int(y * 10), int(z * 10)) + bytes(4)

    def leave(self):
        with feworld._CHAT_ROOM_LOCK:
            feworld._CHAT_ROOM.pop(self.key, None)


def deliver(players):
    """Drain every relay queue on its own session (== ext_pump), again while
    a relay posts another (a tower hit on the victim's thread reports back
    to the builder's)."""
    n, more = 0, True
    while more:
        more = False
        for p in players:
            q = feworld._CHAT_ROOM.get(p.key, {}).get("ext")
            while q is not None:
                try:
                    kind, payload = q.get_nowait()
                except queue.Empty:
                    break
                p.act(feworld.EXT_RELAY[kind], payload)
                n += 1
                more = True
    return n


def got(cid, mid):
    return [b for c, m, b in SENT if c == cid and m == mid]


def mids(cid):
    return [m for c, m, b in SENT if c == cid]


def parse_state(b):
    assert len(b) == 45, len(b)
    party, left, btype, x, y, z, bdir, builder = struct.unpack_from(">IHHHHHHI", b, 0)
    return dict(party=party, left=left, type=btype, pos=(x, y, z), dir=bdir,
                builder=builder, members=list(struct.unpack_from(">5I", b, 20)),
                status=list(b[40:45]))


def parse_err(b):
    assert len(b) == 32, len(b)
    return struct.unpack_from(">hI", b, 0)


def keep_body(btype, gx, gz, gy=0, bdir=1):
    return struct.pack(">5H", btype, gx, gy, gz, bdir)


def build_body(btype, model, gx, gz):
    """0x2007's 23-byte body (fewar.decode_build_request)."""
    return struct.pack(">HHHHHB", btype, model, gx, 0, gz, 0) + struct.pack(">fff", 0, 0, 0)


def parse_ranking(b):
    """0x112A as the client's reader 0x05112e50 consumes it."""
    n = struct.unpack_from(">H", b, 0)[0]
    o, rows = 2, []
    for _ in range(n):
        e = b.index(b"\0", o)
        name = b[o:e].decode("cp932")
        assert e - o <= 0x23, "name %r longer than the row's slot" % name
        o = e + 1
        k, d, s, ds, bu, sc = struct.unpack_from(">HHHHHI", b, o)
        o += 14
        rows.append(dict(name=name, kills=k, deaths=d, summons=s,
                         destroyed=ds, built=bu, score=sc))
    assert o == len(b), "%d bytes left over" % (len(b) - o)
    return rows


def main():
    feworld.load_extensions()
    import fecampaign
    import fewar
    from world import warresult
    tmp = os.environ.get("TEMP", ".")
    cf = os.path.join(tmp, "fe_loop_c_%d.json" % os.getpid())
    tf = os.path.join(tmp, "fe_loop_t_%d.json" % os.getpid())
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
        # nation 3 against 17, nation 1's snowfield (borders 53, nation 3's)
        alice = Player("Alice", 11, 3, 17, args)
        bob = Player("Bob", 12, 3, 17, args)
        carol = Player("Carol", 13, 3, 17, args)
        eve = Player("Eve", 15, 1, 17, args)           # the holder's nation
        frank = Player("Frank", 16, 3, 18, args)       # nation 3, elsewhere
        players = [alice, bob, carol, eve, frank]
        castle = feworld.keep_grids(17)["def"]
        far = feworld.keep_grids(17)["atk"]
        keep = (far[0] - 8, far[1])

        say("the quorum: SE's four, and the OURS exception")
        check("nation 3 has four online (three here, Frank elsewhere)",
              fecampaign._nation_online(3) == 4 and fecampaign._nation_online(1) == 1)
        del SENT[:]
        alice.send(fecampaign.F_BUILD_KEEP, keep_body(16, keep[0], keep[1]))
        check("--declare-quorum accepts (default): three in the field -> code 3",
              [parse_err(b)[0] for b in got(11, 0xF504)] == [3]
              and fecampaign.phase_of(17) == fecampaign.PEACE)
        check("...and the quorum is the four SE asks for",
              fecampaign._accepts_needed(args, 3) == 4)
        check("!campaign quorum online flips it live",
              alice.act(fecampaign.gm, "!campaign quorum online") is True
              and args.declare_quorum == "online")
        check("...with four online the quorum is STILL four (never raised, "
              "only lowered)", fecampaign._accepts_needed(args, 3) == 4)
        del SENT[:]
        alice.send(fecampaign.F_BUILD_KEEP, keep_body(16, keep[0], keep[1]))
        check("...so three in the field is still code 3",
              [parse_err(b)[0] for b in got(11, 0xF504)] == [3])
        frank.leave()
        players.remove(frank)
        check("Frank logs off: nation 3 has three online -> the quorum is three",
              fecampaign._nation_online(3) == 3
              and fecampaign._accepts_needed(args, 3) == 3)
        del SENT[:]
        alice.send(fecampaign.F_BUILD_KEEP, keep_body(16, keep[0], keep[1]))
        deliver(players)
        st = parse_state(got(11, 0xF502)[0])
        check("the vote opens: three slots, the builder accepted",
              st["members"] == [11, 12, 13, 0, 0] and st["status"] == [2, 0, 0, 0, 0]
              and all(len(got(c, 0xF502)) == 1 for c in (12, 13))
              and not got(15, 0xF502), repr(st))
        bob.send(fecampaign.F_REPLY_KEEP, b"\x02")
        deliver(players)
        check("two of three: still open", 17 in fecampaign._VOTES)
        del SENT[:]
        carol.send(fecampaign.F_REPLY_KEEP, b"\x02")
        deliver(players)
        row = fecampaign.state_of(17)
        check("the THIRD accept declares: 0xF503 to all three, War Prep",
              all(len(got(c, 0xF503)) == 1 for c in (11, 12, 13))
              and row["phase"] == fecampaign.PREP and row["atk"] == 3
              and len(row["members"]) == 3, repr(row))
        check("...the declarers carry their names and zeroed ranking columns",
              all(m.get("name") and m.get("kills") == 0 and m.get("built") == 0
                  for m in row["members"].values())
              and row["members"]["acct-Alice|11"]["name"] == "Alice", repr(row["members"]))
        check("...and committed their ★ (three ★1 attackers = 3 spent)",
              fecampaign.spent_of(17, "atk") == 3, repr(row.get("spent")))
        check("!campaign quorum accepts puts SE's rule back",
              alice.act(fecampaign.gm, "!campaign quorum accepts") is True
              and fecampaign._accepts_needed(args, 3) == 4)

        say("War Prep presented: keys, army ids, the castle row, the gauge")
        del SENT[:]
        alice.pump()
        n18 = got(11, 0x1018)
        check("0x1018 carries the defender's and attacker's keys (1, 3) by default",
              n18 and struct.unpack_from(">II", n18[0], 0) == (1, 3), repr(mids(11)))
        check("...followed by 0x101F [0][0][1][3], the Join buttons' armies",
              got(11, 0x101F) == [struct.pack(">IIII", 0, 0, 1, 3)], repr(mids(11)))
        adds = got(11, 0x1006)
        # the 0x1006 body is [u16 1][u32 obj][u8 1] then the record, whose
        # first u16 is the FE_BUILDING_DATA row (building_record)
        types_ = sorted(struct.unpack_from(">H", b, 7)[0] for b in adds)
        check("the castle goes out as TYPE 15 (the gauge's 0x0f row) and the "
              "keep as 16", types_ == [15, 16] and set(alice.session["keeps"]) == {2900, 2901},
              repr(types_))
        # 2026-10-23: +0x94 (body[15]) is the WAR SIDE, not the nation
        sides_ = sorted((struct.unpack_from(">H", b, 7)[0], b[15]) for b in adds)
        check("...the castle as side 1 (defence), the keep as side 0 (attack)",
              sides_ == [(15, 1), (16, 0)], repr(sides_))
        g = got(11, 0x1176)
        check("the gauge's 支配領域 pair is said at the presentation: (0, 0)",
              g == [struct.pack(">II", 0, 0)], repr(g))
        del SENT[:]
        alice.pump()
        check("...once: a second pump with nothing changed re-says nothing",
              not got(11, 0x1176), repr(mids(11)))

        say("joining: Eve defends with the army id the buttons now carry")
        del SENT[:]
        eve.send(0x2018, struct.pack(">I", 1))
        check("0x2018 army=1 (her nation, from 0x101F) -> 0x1020, defence",
              mids(15) == [0x1020]
              and fecampaign.state_of(17)["members"]["acct-Eve|15"]["side"] == "def"
              and fecampaign.spent_of(17, "def") == 1)

        say("buildings: an obelisk moves the gauge, a tower stands")
        del SENT[:]
        alice.send(fewar.BUILD_REQUEST, build_body(6, 8, far[0] + 4, far[1]))
        check("0x2007 obelisk -> OK and the 0x1006, beside the keep",
              0x1009 in mids(11) and 0x1006 in mids(11), repr(mids(11)))
        ob = [b for b in got(11, 0x1006) if len(b) > 15 and b[6] == 1]
        check("...built by an attacker: its +0x94 is side 0, Alice's own, so "
              "her Summ filter matches it",
              len(ob) == 1 and ob[0][15] == 0, repr([b[15] for b in ob]))
        share = fecampaign.state_of(17).get("share") or {}
        check("the attackers' 支配領域 is computed on the build, the defenders' 0",
              share.get("atk", 0) > 0 and share.get("def", 0) == 0, repr(share))
        del SENT[:]
        alice.pump()
        bob.pump()
        ga, gb = got(11, 0x1176), got(12, 0x1176)
        check("0x1176 to everyone in the field: attacker > 0 (first u32), "
              "defender 0, the same pair",
              len(ga) == 1 and ga == gb
              and 0 < struct.unpack(">II", ga[0])[0] <= fecampaign.GAUGE_FULL
              and struct.unpack(">II", ga[0])[1] == 0, repr((ga, gb)))
        check("...worth %.0f%% of the field in the client's 0x10000 scale"
              % (share["atk"] * 100),
              struct.unpack(">II", ga[0])[0] == int(round(share["atk"] * 0x10000)))
        del SENT[:]
        alice.send(fewar.BUILD_REQUEST, build_body(0, 0, far[0] + 6, far[1] + 2))
        check("0x2007 arrow tower -> OK", 0x1009 in mids(11), repr(mids(11)))
        m = fecampaign.state_of(17)["members"]["acct-Alice|11"]
        check("Alice's Built column counts both", m.get("built") == 2, repr(m))
        with fecampaign._LOCK:
            for b in fecampaign._STATE[17]["buildings"].values():
                b["t0"] -= 120.0            # finished building a while ago
        tower = next(o for o, b in fecampaign.buildings_of(17).items() if b["t"] == 0)
        del SENT[:]
        bob.pump()
        check("Bob is served the tower (0x1006) on his next pump",
              any(struct.unpack_from(">H", b, 7)[0] == 0 for b in got(12, 0x1006)),
              repr(mids(12)))

        say("War Prep -> At war")
        with fecampaign._LOCK:
            fecampaign._STATE[17]["until"] = time.time() - 1
        del SENT[:]
        alice.pump()
        check("0x1015 and 0x101D (offensive) to the attacker",
              0x1015 in mids(11) and 0x101D in mids(11)
              and fecampaign.phase_of(17) == fecampaign.WAR, repr(mids(11)))
        del SENT[:]
        eve.pump()
        eve.pump()
        check("Eve, unarmed, gets 0x1018 (after her keeps) before 0x1015, then "
              "0x101E (defensive)",
              0x1018 in mids(15) and 0x1015 in mids(15) and 0x101E in mids(15)
              and mids(15).index(0x1018) < mids(15).index(0x1015)
              < mids(15).index(0x101E), repr(mids(15)))

        say("the arrow tower fires")
        tpos = fecampaign._tower_pos(args, 17, tower, fecampaign.building_of(17, tower))
        eve.stand(tpos[0] + 10.0, tpos[1], tpos[2])
        bob.stand(tpos[0] + 200.0, tpos[1], tpos[2])
        del SENT[:]
        alice.pump()
        deliver(players)
        check("Alice's pump (the field's driver) shoots Eve, 10 u from the tower, "
              "for the row's power 35: her HP 1000 -> 965",
              eve.session.get("player_hp") == 965 and got(15, 0x2024), repr(mids(15)))
        check("...and the hit is reported back and credited to the BUILDER's "
              "PC damage", fecampaign.state_of(17)["members"]["acct-Alice|11"]["pc_dmg"] == 35)
        eve.session["player_hp"] = 1000
        del SENT[:]
        alice.pump()
        deliver(players)
        check("the next pump is inside the 5 s interval: no second shot",
              eve.session["player_hp"] == 1000, repr(eve.session["player_hp"]))
        fecampaign._TOWER_CLOCK.get(17, {}).clear()
        eve.stand(tpos[0] + 300.0, tpos[1], tpos[2])
        bob.stand(tpos[0] + 5.0, tpos[1], tpos[2])
        bob.session["player_hp"] = 1000
        alice.pump()
        deliver(players)
        check("with Eve out of range and Bob (its own side) beside it: nobody is shot",
              bob.session["player_hp"] == 1000 and eve.session["player_hp"] == 1000)
        fecampaign._TOWER_CLOCK.get(17, {}).clear()
        eve.stand(tpos[0] + 39.0, tpos[1], tpos[2])
        alice.pump()
        deliver(players)
        check("...Eve at 39 u (range 40) is shot again",
              eve.session["player_hp"] == 965)
        check("...and a dead player is never a target",
              (eve.session.__setitem__("player_dead", True),
               fecampaign._tower_target(args, 17, "atk", tpos, 40.0)[0] is None)[1])
        eve.session.pop("player_dead", None)
        # 2026-10-23: ext_post skips the poster unless include_me, so the
        # player whose pump DRIVES the towers was never shot
        fecampaign._TOWER_CLOCK.get(17, {}).clear()
        fecampaign._TOWER_DRIVER.pop(17, None)
        eve.session["player_hp"] = 1000
        eve.stand(tpos[0] + 10.0, tpos[1], tpos[2])
        # a live session is keyed by its thread's ident, which is what
        # ext_post compares; the fake key would never be "me"
        real = threading.get_ident()
        with feworld._CHAT_ROOM_LOCK:
            feworld._CHAT_ROOM[real] = feworld._CHAT_ROOM.pop(eve.key)
        eve.key, fake = real, eve.key
        try:
            eve.pump()
            deliver(players)
        finally:
            with feworld._CHAT_ROOM_LOCK:
                feworld._CHAT_ROOM[fake] = feworld._CHAT_ROOM.pop(real)
            eve.key = fake
        check("the tower shoots Eve when EVE's own pump drives the towers",
              fecampaign._TOWER_DRIVER.get(17, (None,))[0] == 15
              and eve.session["player_hp"] == 965, repr(eve.session["player_hp"]))
        fecampaign._TOWER_DRIVER.pop(17, None)

        say("the rank gap (--rank-cost diff, --rank-gap)")
        args.rank_gap = 2
        ivan = Player("Ivan", 19, 1, 17, args, rank=5)
        dave = Player("Dave", 20, 3, 17, args, rank=3)
        judy = Player("Judy", 21, 1, 17, args, rank=2)
        players += [ivan, dave, judy]
        del SENT[:]
        ivan.send(0x2018, struct.pack(">I", 1))
        check("a ★5 defender with 1★ in against 3★: 1+5-3 = 3 > gap 2 -> "
              "0x1021 code 11 (defence 'full')",
              [(m, b) for c, m, b in SENT if c == 19] == [(0x1021, struct.pack(">I", 11))]
              and "acct-Ivan|19" not in fecampaign.state_of(17)["members"], repr(SENT))
        del SENT[:]
        judy.send(0x2018, struct.pack(">I", 1))
        check("a ★2 defender: 1+2-3 = 0 -> joins (0x1020), 3★ on each side",
              mids(21) == [0x1020] and fecampaign.spent_of(17, "def") == 3)
        del SENT[:]
        dave.send(0x2018, struct.pack(">I", 3))
        check("a ★3 attacker: 3+3-3 = 3 > 2 -> 0x1021 code 14 (attack 'full')",
              [(m, b) for c, m, b in SENT if c == 20] == [(0x1021, struct.pack(">I", 14))])
        CHARS[("acct-Dave", 20)]["war_rank"] = 1
        del SENT[:]
        dave.send(0x2018, struct.pack(">I", 3))
        check("...the same player at ★1: 3+1-3 = 1 -> joins",
              mids(20) == [0x1020] and fecampaign.spent_of(17, "atk") == 4)
        args.rank_gap = 18

        say("a kill: the ticker, the rows")
        del SENT[:]
        alice.act(lambda ctx: fecampaign.pc_damage(args, 17, 500, kill=True, victim=15))
        deliver(players)
        tick = got(15, 0x1129)
        check("0x1129 'Alice defeated Eve' reaches everyone in the field (on by default)",
              tick == [b"Alice\x00\x03Eve\x00\x10"] and got(12, 0x1129) and got(11, 0x1129),
              repr(tick))
        mem = fecampaign.state_of(17)["members"]
        check("Alice +1 kill, Eve +1 death on the rows",
              mem["acct-Alice|11"]["kills"] == 1 and mem["acct-Eve|15"]["deaths"] == 1)

        say("the castle falls: truce, the land, the settlement")
        alice.act(lambda ctx: fecampaign.keep_damage(args, 17, "def", 3000))
        del SENT[:]
        alice.pump()
        deliver(players)
        row = fecampaign.state_of(17)
        check("truce; nation 3 holds 17; 0x1016 and the DEPRIVE notify to Alice",
              row["phase"] == fecampaign.TRUCE and feworld.territory_owner(17, args) == 3
              and 0x1016 in mids(11) and 0x3036 in mids(11), repr(mids(11)))
        del SENT[:]
        eve.pump()
        check("...Eve's pump: 0x1016 and the DEPRIVED notify",
              0x1016 in mids(15) and 0x3037 in mids(15), repr(mids(15)))
        a = CHARS[("acct-Alice", 11)]
        check("Alice won: rank 1 (two straight wins to ★2), Rings 1 + 1 + 3 "
              "(time) + 0 grades, war score 52 into Total Score",
              a["war_rank"] == 1 and a["ring"] == 1 + 1 + 3 and a["total_score"] == 52,
              repr(a))
        wr = alice.session.get("war_result") or {}
        rk = wr.get("ranking") or []
        # Bob and Carol barely stood in the field during the war (--war-presence:
        # pumped before it started, not during), so they earn the 20% floor
        check("the settlement reached Alice's session with the whole war RANKED "
              "by score: Alice 52 first, the barely-present attackers 10, the "
              "losers at 0 last",
              [r["name"] for r in rk[:3]] == ["Alice", "Bob", "Carol"]
              and [r["score"] for r in rk[:3]] == [52, 10, 10]
              and rk[-1]["score"] == 0 and len(rk) == 6, repr(rk))
        check("...the same list on Eve's session",
              (eve.session.get("war_result") or {}).get("ranking") == rk)
        check("...with the columns: Alice kills 1 built 2, Eve deaths 1",
              next(r for r in rk if r["name"] == "Alice")["kills"] == 1
              and next(r for r in rk if r["name"] == "Alice")["built"] == 2
              and next(r for r in rk if r["name"] == "Eve")["deaths"] == 1, repr(rk))

        say("Field Out: the result window and the ranking window")
        del SENT[:]
        alice.act(lambda ctx: warresult.war_result_push(None, None, "ecb", False, args))
        res = got(11, 0x1100)
        check("0x1100 kind 1 (Win), players 4 attack / 2 defend (Ivan was "
              "refused), score 52, "
              "destroyed 0 (a COUNT), rewards 5 Rings",
              len(res) == 1 and res[0][0] == 1
              and struct.unpack_from(">II", res[0], 1) == (4, 2)
              and dict(zip(warresult.WAR_RESULT_FIELDS,
                           struct.unpack_from(">BIIHIIIIHHH", res[0], 0)[1:]))["score"] == 52,
              repr(res))
        rank = got(11, 0x112A)
        check("0x112A follows it", len(rank) == 1 and mids(11).index(0x112A) > mids(11).index(0x1100))
        rows = parse_ranking(rank[0])
        check("...and parses as the client's reader does: 6 rows, Alice first "
              "with kills 1 / deaths 0 / summons 0 / destroyed 0 / built 2 / score 52",
              len(rows) == 6 and rows[0] == dict(name="Alice", kills=1, deaths=0,
                                                 summons=0, destroyed=0, built=2,
                                                 score=52), repr(rows))
        check("...Eve's row: deaths 1, score 0",
              next(r for r in rows if r["name"] == "Eve") == dict(
                  name="Eve", kills=0, deaths=1, summons=0, destroyed=0, built=0, score=0))
        check("the stash is used once: a second exit is a desert with no ranking",
              not alice.session.get("war_result"))
        long_name = "x" * 60
        body = warresult.war_ranking_body([{"name": long_name, "score": 1}] * 3)
        rows = parse_ranking(body)
        check("a name longer than the row's 0x24-byte slot is clamped to fit",
              len(rows) == 3 and rows[0]["name"] == "x" * 0x23)
        check("--war-ranking off sends nothing",
              (args.__setattr__("war_ranking", "off"),
               not warresult.war_ranking_push(None, None, "ecb", False, args, [{}]))[1])
        args.war_ranking = "on"

        say("truce -> peace, and the next field")
        with fecampaign._LOCK:
            fecampaign._STATE[17]["until"] = time.time() - 1
        del SENT[:]
        alice.pump()
        row = fecampaign.state_of(17)
        check("peace: 0x1017, the war forgotten, the 支配領域 with it",
              0x1017 in mids(11) and row["phase"] == fecampaign.PEACE
              and row["members"] == {} and row.get("share") == {}, repr(row))
        check("the castle stands alone, re-served under nation 3; the keep is gone",
              set(alice.session.get("keeps") or {}) == {2900}
              and alice.session["keeps"][2900]["nation"] == 3
              and 2901 not in (alice.session.get("keeps") or {}), repr(alice.session.get("keeps")))
        check("...and the gauge is not fed at peace",
              not got(11, 0x1176) and "gauge_sent" not in alice.session)
        # (peace already emptied the real one: stand in for a mid-war walk)
        alice.session["built_seen"] = {3000: 17}
        seen_before = dict(alice.session["built_seen"])
        alice.goto(18)
        del SENT[:]
        alice.pump()
        check("walking into field 18: the old field's served buildings are forgotten "
              "(a 3000 there is not 'already served' here)",
              seen_before and not alice.session.get("built_seen")
              and alice.session.get("campaign_field") == 18, repr(seen_before))
        check("...the keeps registry keeps nothing of area 17",
              all(k["area"] == 18 for k in (alice.session.get("keeps") or {}).values()),
              repr(alice.session.get("keeps")))
    finally:
        feworld.send, feworld._load_char_field, feworld._store_char_field, \
            fecampaign._update_char = saved
        for p in players:
            p.leave()
        for p in (cf, tf):
            try:
                os.remove(p)
            except OSError:
                pass

    bad = [l for l, ok in CHECKS if not ok]
    say("[fe_warloop_test] %s -- %d checks" % ("FAIL" if bad else "OK", len(CHECKS)))
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("[fe_warloop_test] FAIL: %s" % e)
        sys.exit(1)
