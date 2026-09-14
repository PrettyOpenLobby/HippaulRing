#!/usr/bin/env python3
"""fe_declare_test.py -- how a war STARTS, who may join it, what it pays.

Run from services/:  python ../tools/fe_declare_test.py

2026-09-11. Until today a war was declared by a player merely STANDING in a
peaceful field (--campaign-auto), even their own nation's, and nobody was ever
rewarded for fighting one. This drives the 2006 rules through the objects prod
uses (feworld.Ctx, the thread-local session, the ext_post relays, the pump):

  * 0xF501 CommandBuildKeepForWar -> the refusals (0xF504 codes), the vote
    (0xF502 to every voter of the builder's nation IN THE FIELD, byte-exact
    against the client's reader 0x0517c6c0), 0xF500 replies, 0xF503 on the
    fourth accept (the builder counts), 0xF504 code 4 on rejects / code 5 on
    the 60 s timeout, and `--declare-accepts 0` = the builder alone
  * the declared keep stands where the player built it (keep_grids)
  * 0x2018 join rules: your nation's side; a FOREIGN volunteer only on the
    smaller side; one war at a time (0x1021 code 5); a re-sent 0x2018 is not
    a second sign-up
  * after the war: rank steps by streak, Rings = rank (+ winners' bonus),
    stored on the character and re-served (0x2024) through the relay; the
    nations' war/win tallies; the 0x3027 nation record carries them
  * crystals: 0x2081 draws, 20 per war, 50 carried; the giant crystal is a
    type-7 building
  * fewar's 0x2007: only in war prep/war, by a side, Gate of Hades once
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
    a = dict(campaign="on", campaign_auto="off", war_cap=50, war_prep_ms=60000,
             war_length_ms=60000, war_truce_ms=60000, war_decide="keeps",
             campaign_file=None, territory_file=None, territory="",
             war="offensive", war_fields="0,0,0,0", war_deadline_ms=60000,
             war_clock="off", war_trace="off", war_start_fields="0,0",
             war_cycle="on", seq_mode="count", world_prefix=4, unit_id="0",
             campaign_notify_all=False, keeps="on", keep_hp=3000,
             declare_accepts=4, declare_rejects=3, declare_timeout_ms=60000,
             beginner_fields="on", keep_min_cells=30, foreign_join="smaller",
             # the first pass's Ring bonus PINNED (its checks below); the
             # 2006 reward (--war-rewards rod) is fe_rod_numbers_test's
             war_rewards="ours",
             war_ring_win=2, war_ring_dmg=1000, king_message=["3:Hold the line."],
             force_population="on", war_crystals=0, crystal=5, ring=1, gold=None,
             total_score=None, crystal_draw=1, crystal_war_max=20,
             crystal_carry_max=50, crystal_reset="off", build_territory="off",
             build="real", build_costs="off",
             build_phase="war", build_caps="4=1", building_base=3000,
             combat="on", hit_damage=400, world="dat", islands=6, groups=1,
             field_nations=None, capital="1:39")
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

    def __init__(self, name, charid, nation, area, args):
        self.name, self.charid, self.args = name, charid, args
        self.key = ("fake", charid)
        self.session = {"account": "acct-" + name, "charid": charid,
                        "in_field": True, "field": area, "room": -1,
                        "campaign_nation": nation, "field_ready": True}
        CHARS[("acct-" + name, charid)] = {"charid": charid, "force": nation}
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

    def goto(self, area):
        self.session["field"] = area

    def leave(self):
        with feworld._CHAT_ROOM_LOCK:
            feworld._CHAT_ROOM.pop(self.key, None)


def deliver(players):
    """Drain every relay queue on its own session (== ext_pump)."""
    n = 0
    for p in players:
        q = feworld._CHAT_ROOM.get(p.key, {}).get("ext")
        while q is not None:
            try:
                kind, payload = q.get_nowait()
            except queue.Empty:
                break
            p.act(feworld.EXT_RELAY[kind], payload)
            n += 1
    return n


def got(cid, mid):
    return [b for c, m, b in SENT if c == cid and m == mid]


def parse_state(b):
    """0xF502 exactly as the client's reader 0x0517c6c0 consumes it."""
    assert len(b) == 45, len(b)
    party, left, btype, x, y, z, bdir, builder = struct.unpack_from(">IHHHHHHI", b, 0)
    members = list(struct.unpack_from(">5I", b, 20))
    status = list(b[40:45])
    return dict(party=party, left=left, type=btype, pos=(x, y, z), dir=bdir,
                builder=builder, members=members, status=status)


def parse_err(b):
    """0xF504 as the client's reader 0x0517cc00 consumes it (32 bytes)."""
    assert len(b) == 32, len(b)
    code, party = struct.unpack_from(">hI", b, 0)
    return code, party


def keep_body(btype, gx, gz, gy=0, bdir=1):
    return struct.pack(">5H", btype, gx, gy, gz, bdir)


def main():
    feworld.load_extensions()
    import fecampaign
    import fewar
    tmp = os.environ.get("TEMP", ".")
    cf = os.path.join(tmp, "fe_decl_c_%d.json" % os.getpid())
    tf = os.path.join(tmp, "fe_decl_t_%d.json" % os.getpid())
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
        # nation 3 (Cesedria-side) against 17, nation 1's snowfield: it
        # borders 53 (nation 3's), touches no capital
        alice = Player("Alice", 11, 3, 17, args)
        bob = Player("Bob", 12, 3, 17, args)
        carol = Player("Carol", 13, 3, 17, args)
        dave = Player("Dave", 14, 3, 17, args)
        eve = Player("Eve", 15, 1, 17, args)          # the holder's nation
        frank = Player("Frank", 16, 3, 18, args)      # nation 3, ANOTHER field
        players = [alice, bob, carol, dave, eve, frank]
        far = feworld.keep_grids(17)["atk"]
        castle = feworld.keep_grids(17)["def"]

        say("the refusals (0xF504, the client's own codes)")
        for who, area, grid, code, label in (
                (alice, 53, far, 7, "your own land"),
                (alice, 25, far, 8, "a beginner field (touches capital 21)"),
                (alice, 28, far, 2, "a field not touching your nation's land"),
                (alice, 21, far, 11, "a capital"),
                (alice, 17, (castle[0] + 3, castle[1]), 6, "a keep beside the castle")):
            who.goto(area)
            del SENT[:]
            who.send(fecampaign.F_BUILD_KEEP, keep_body(16, grid[0], grid[1]))
            errs = got(who.charid, fecampaign.F_READY_ERR)
            check("%s -> 0xF504 code %d" % (label, code),
                  len(errs) == 1 and parse_err(errs[0])[0] == code
                  and fecampaign.phase_of(area) == fecampaign.PEACE,
                  "%r" % [(m, parse_err(b)) for c, m, b in SENT if m == 0xF504])
        alice.goto(17)
        fecampaign.declare(args, 29, 3)                # nation 1 is now at war
        del SENT[:]
        alice.send(fecampaign.F_BUILD_KEEP, keep_body(16, far[0], far[1]))
        check("a holder nation already at war -> code 11 (SE: 隣接している国が戦争中)",
              [parse_err(b)[0] for b in got(11, 0xF504)] == [11])
        with fecampaign._LOCK:
            fecampaign._STATE.pop(29, None)
        for p in (bob, carol, dave):
            p.goto(18)
        del SENT[:]
        alice.send(fecampaign.F_BUILD_KEEP, keep_body(16, far[0], far[1]))
        check("alone with --declare-accepts 4 -> code 3 (not enough members)",
              [parse_err(b)[0] for b in got(11, 0xF504)] == [3])
        args.campaign = "off"
        del SENT[:]
        alice.send(fecampaign.F_BUILD_KEEP, keep_body(16, far[0], far[1]))
        check("--campaign off: logged only, nothing sent (the old behaviour)",
              SENT == [])
        args.campaign = "on"

        say("solo: --declare-accepts 0 -- the builder alone declares")
        args.declare_accepts = 0
        del SENT[:]
        alice.send(fecampaign.F_BUILD_KEEP, keep_body(16, far[0], far[1]))
        row = fecampaign.state_of(17)
        check("0xF503 KeepOK straight away, no dialog",
              len(got(11, 0xF503)) == 1 and not got(11, 0xF502), repr(SENT))
        check("...the war is declared: PREP, attacker nation 3, the keep at "
              "the builder's grid", row["phase"] == fecampaign.PREP
              and row["atk"] == 3 and row["grid"] == list(far), repr(row))
        check("...and the builder is enlisted as an attacker",
              list(row["members"]) == ["acct-Alice|11"]
              and row["members"]["acct-Alice|11"]["side"] == "atk"
              and fecampaign.counts_of(17, args)[0] == 1)
        with fecampaign._LOCK:
            fecampaign._STATE.pop(17, None)
        args.declare_accepts = 4

        say("the vote: four of nation 3 in the field, one holder, one elsewhere")
        for p in (bob, carol, dave):
            p.goto(17)
        grid = (far[0] - 8, far[1])
        del SENT[:]
        alice.send(fecampaign.F_BUILD_KEEP, keep_body(16, grid[0], grid[1], gy=3))
        mine = got(11, 0xF502)
        check("the builder gets 0xF502 NotifyBuildToReadyState directly",
              len(mine) == 1, repr(SENT))
        st = parse_state(mine[0])
        check("...45 bytes the client's reader consumes: builder, five slots, "
              "the placed keep", st["builder"] == 11 and st["type"] == 16
              and st["pos"] == (grid[0], 3, grid[1]) and st["dir"] == 1
              and st["members"] == [11, 12, 13, 14, 0], repr(st))
        check("...the builder shows status 2 (accepted -- 'Waiting for party "
              "reply'), the others 0 (buttons)", st["status"] == [2, 0, 0, 0, 0])
        check("...last_time counts the 60 s", 58 <= st["left"] <= 60, repr(st))
        check("no war yet", fecampaign.phase_of(17) == fecampaign.PEACE)
        deliver(players)
        check("every voter of the nation in the field gets it on their own "
              "thread; the holder's Eve and Frank (another field) do not",
              all(len(got(c, 0xF502)) == 1 for c in (12, 13, 14))
              and not got(15, 0xF502) and not got(16, 0xF502), repr(SENT))
        del SENT[:]
        bob.send(fecampaign.F_REPLY_KEEP, b"\x02")
        deliver(players)
        st = parse_state(got(11, 0xF502)[-1])
        check("Bob's OK (reply 2) is shown to everyone: status [2,2,0,0,0]",
              st["status"] == [2, 2, 0, 0, 0] and len(got(13, 0xF502)) == 1)
        carol.send(fecampaign.F_REPLY_KEEP, b"\x02")
        deliver(players)
        check("three accepts of four: still open", fecampaign.phase_of(17) == fecampaign.PEACE
              and 17 in fecampaign._VOTES)
        del SENT[:]
        dave.send(fecampaign.F_REPLY_KEEP, b"\x02")
        deliver(players)
        row = fecampaign.state_of(17)
        check("the FOURTH accept (builder counted) declares: 0xF503 to all four",
              all(len(got(c, 0xF503)) == 1 for c in (11, 12, 13, 14))
              and struct.unpack(">I", got(11, 0xF503)[0])[0] == st["party"])
        check("...War Prep, nation 3 attacking, the four enlisted as attackers",
              row["phase"] == fecampaign.PREP and row["atk"] == 3
              and len(row["members"]) == 4
              and fecampaign.counts_of(17, args)[:2] == (4, 0), repr(row))
        check("...and the attacker's keep stands where Alice built it",
              feworld.keep_grids(17)["atk"] == grid
              and feworld.keep_grids(17)["def"] == castle)
        del SENT[:]
        alice.act(fecampaign.pump)
        check("the builder's pump presents War Prep (0x1018) and the keeps",
              0x1018 in [m for c, m, b in SENT if c == 11]
              and 0x1006 in [m for c, m, b in SENT if c == 11], repr(SENT))
        del SENT[:]
        alice.send(fecampaign.F_BUILD_KEEP, keep_body(16, grid[0], grid[1]))
        check("a second keep in a field already at war -> code 10",
              [parse_err(b)[0] for b in got(11, 0xF504)] == [10])

        say("cancelled votes")
        grace = Player("Grace", 17, 4, 2, args)       # nation 4 holds area 2
        players.append(grace)
        # nation 3 players in area 2 (nation 4's, next to 3's area 10)
        vs = [Player("V%d" % i, 30 + i, 3, 2, args) for i in range(5)]
        players += vs
        g2 = feworld.keep_grids(2)["atk"]
        args.declare_accepts = 2          # so rejects, not arithmetic, decide
        del SENT[:]
        vs[0].send(fecampaign.F_BUILD_KEEP, keep_body(16, g2[0], g2[1]))
        deliver(players)
        check("five voters fill the dialog's five slots",
              parse_state(got(30, 0xF502)[0])["members"] == [30, 31, 32, 33, 34])
        vs[1].send(fecampaign.F_REPLY_KEEP, b"\x01")
        vs[2].send(fecampaign.F_REPLY_KEEP, b"\x01")
        deliver(players)
        check("two CANCELs (reply 1) of five: still open -- 2 can still accept",
              2 in fecampaign._VOTES)
        del SENT[:]
        vs[3].send(fecampaign.F_REPLY_KEEP, b"\x01")
        deliver(players)
        check("the THIRD reject cancels: 0xF504 code 4 to all five",
              all([parse_err(b)[0] for b in got(c, 0xF504)] == [4]
                  for c in range(30, 35)) and 2 not in fecampaign._VOTES
              and fecampaign.phase_of(2) == fecampaign.PEACE, repr(SENT))
        args.declare_accepts = 5
        del SENT[:]
        vs[0].send(fecampaign.F_BUILD_KEEP, keep_body(16, g2[0], g2[1]))
        vs[4].send(fecampaign.F_REPLY_KEEP, b"\x01")
        deliver(players)
        check("with 5 needed, ONE reject makes it unreachable: code 4 at once",
              [parse_err(b)[0] for b in got(32, 0xF504)] == [4])
        args.declare_accepts = 4
        del SENT[:]
        vs[0].send(fecampaign.F_BUILD_KEEP, keep_body(16, g2[0], g2[1]))
        with fecampaign._LOCK:
            fecampaign._VOTES[2]["until"] = time.time() - 1
        vs[0].act(fecampaign.pump)
        deliver(players)
        check("60 s without enough accepts: code 5 (not approved in time)",
              [parse_err(b)[0] for b in got(33, 0xF504)] == [5]
              and 2 not in fecampaign._VOTES)

        say("joining: your side, the smaller side, one war at a time")
        with fecampaign._LOCK:
            fecampaign._STATE[17]["phase"] = fecampaign.WAR
            fecampaign._STATE[17]["until"] = time.time() + 60
        del SENT[:]
        eve.send(0x2018, struct.pack(">I", 0))
        check("Eve (nation 1, the holder) joins the DEFENCE: 0x1020",
              [m for c, m, b in SENT if c == 15] == [0x1020]
              and fecampaign.state_of(17)["members"]["acct-Eve|15"]["side"] == "def")
        eve.send(0x2018, struct.pack(">I", 0))
        check("...a re-sent 0x2018 is NOT a second sign-up",
              fecampaign.counts_of(17, args)[:2] == (4, 1))
        hugo = Player("Hugo", 18, 2, 17, args)        # nation 2: a foreigner
        players.append(hugo)
        del SENT[:]
        hugo.send(0x2018, struct.pack(">I", 3))       # asks for the attackers
        check("a foreigner asking for the BIGGER side (4 v 1) -> 0x1021 code 21",
              [(m, b) for c, m, b in SENT if c == 18]
              == [(0x1021, struct.pack(">I", 21))], repr(SENT))
        hugo.send(0x2018, struct.pack(">I", 0))
        check("...unspecified: placed on the SMALLER side (defence)",
              fecampaign.state_of(17)["members"]["acct-Hugo|18"]["side"] == "def"
              and fecampaign.counts_of(17, args)[:2] == (4, 2))
        fecampaign.declare(args, 2, 3)
        with fecampaign._LOCK:
            fecampaign._STATE[2]["phase"] = fecampaign.WAR
        hugo.goto(2)
        del SENT[:]
        hugo.send(0x2018, struct.pack(">I", 0))
        check("...and cannot join a SECOND war while that one runs: code 5",
              [(m, b) for c, m, b in SENT if c == 18]
              == [(0x1021, struct.pack(">I", 5))], repr(SENT))
        with fecampaign._LOCK:
            fecampaign._STATE.pop(2, None)
        hugo.goto(17)

        say("after the war: rank, Rings, the nations' tally")
        alice.act(lambda ctx: fecampaign.keep_damage(args, 17, "def", 2500))
        check("keep damage is credited to the hitter's member row",
              fecampaign.state_of(17)["members"]["acct-Alice|11"]["dmg"] == 2500)
        alice.act(lambda ctx: fecampaign.keep_damage(args, 17, "def", 600))
        del SENT[:]
        alice.act(fecampaign.pump)                     # the castle fell: war ends
        row = fecampaign.state_of(17)
        check("the castle at 0 ends the war; the attacker takes 17",
              row["phase"] == fecampaign.TRUCE and feworld.territory_owner(17, args) == 3)
        a = CHARS[("acct-Alice", 11)]
        check("Alice (won, first war): rank stays 1 -- 1->2 takes TWO straight wins",
              a["war_rank"] == 1 and a["war_streak"] == 1 and a["war_wins"] == 1)
        check("...Rings: 1 (= rank) + 2 (winners' flat) + 3 (3000 damage // 1000), "
              "on top of the --ring 1 seed", a["ring"] == 1 + 1 + 2 + 3, repr(a))
        e = CHARS[("acct-Eve", 15)]
        check("Eve (lost): rank 1, streak -1, Rings = rank only",
              e["war_rank"] == 1 and e["war_streak"] == -1 and e["ring"] == 1 + 1
              and e["war_losses"] == 1, repr(e))
        check("the nation tallies: nation 3 fought 1 won 1, nation 1 fought 1 won 0",
              fecampaign.nation_stats(3) == {"wars": 1, "wins": 1}
              and fecampaign.nation_stats(1) == {"wars": 1, "wins": 0})
        deliver(players)
        check("every enlisted player online is re-served RING (0x2024) on "
              "their own thread", all(got(c, 0x2024) for c in (11, 12, 15, 18)),
              repr([(c, m) for c, m, b in SENT]))
        ring_off = struct.unpack(">I", got(11, 0x2024)[-1][8:12])[0]
        check("...carrying the stored total", ring_off == a["ring"], "%d" % ring_off)
        fecampaign.settle(args, 17, "atk", 1)
        check("a war pays out ONCE", CHARS[("acct-Alice", 11)]["ring"] == a["ring"])
        # the second straight win
        with fecampaign._LOCK:
            fecampaign._STATE[5] = fecampaign._row_from(
                {"phase": fecampaign.WAR, "atk": 3,
                 "members": {"acct-Alice|11": {"a": "acct-Alice", "c": 11,
                                               "side": "atk", "dmg": 0}}})
        fecampaign.settle(args, 5, "atk", 5)
        check("a SECOND straight win: rank 1 -> 2, streak restarts",
              a["war_rank"] == 2 and a["war_streak"] == 0 and a["ring"] == 7 + 1 + 2)
        rs = [(1, 0)]
        for won in (True,) * 20:
            rs.append(fecampaign.rank_step(rs[-1][0], rs[-1][1], won))
        check("the ladder up: 2 / 2 / 5 / 10 straight wins for 1->2->3->4->5",
              [r for r, _s in rs].index(2) == 2 and [r for r, _s in rs].index(3) == 4
              and [r for r, _s in rs].index(4) == 9 and [r for r, _s in rs].index(5) == 19,
              repr(rs))
        down = [(5, 0)]
        for won in (False,) * 10:
            down.append(fecampaign.rank_step(down[-1][0], down[-1][1], won))
        check("...and down: 2 / 2 / 3 / 3 straight losses for 5->4->3->2->1",
              [r for r, _s in down] == [5, 5, 4, 4, 3, 3, 3, 2, 2, 2, 1], repr(down))
        check("a win breaks a losing streak", fecampaign.rank_step(3, -2, True) == (3, 1))

        say("the Manager's nation record (0x3027)")
        import feforce
        feforce._ALL_CHARS = lambda: [("x", {"charid": 1, "force": 3}),
                                      ("y", {"charid": 2, "force": 3}),
                                      ("z", {"charid": 3, "force": 1})]
        fecampaign._POP_CACHE.update(t=0.0, rows={})
        v = feworld.campaign_force_vals(args, 3, {"name": "Netz"})
        held = sum(1 for x in fegamedata.areas() if feworld.territory_owner(x, args) == 3)
        check("territory, wars, wins, population and the King's message are "
              "live, not zeros", v["FormerNumTerritory"] == held and held >= 20
              and v["WarTotalNum"] == 2 and v["WinTotalNum"] == 2
              and v["FormerNumMember"] == 2 and v["KingMessage"] == "Hold the line."
              and v["name"] == "Netz", repr(v))
        rec = feworld.build_force_record(v)
        check("...and the record still builds (FORCE_FIELDS order)",
              b"Hold the line.\x00" in rec and rec.startswith(b"\0\0\0\0Netz\0"))
        check("`!force kingmsg` (already in the row) wins over --king-message",
              feworld.campaign_force_vals(args, 3, {"KingMessage": "Mine"})
              ["KingMessage"] == "Mine")
        feforce._ALL_CHARS = None

        say("crystals: 0x2081 draws, 20 a war, 50 carried")
        with fecampaign._LOCK:
            fecampaign._STATE[17] = fecampaign._row_from(
                {"phase": fecampaign.WAR, "until": time.time() + 60, "atk": 1})
        feworld.territory_set(17, 3, args)
        CHARS[("acct-Bob", 12)]["crystal"] = 5
        # 2026-09-11: a draw must name one of this war's giant crystals
        # (object --keep-base + 10 + i); fe_crystal_test drives the rest
        args.war_crystals = 1
        del SENT[:]
        bob.send(0x2081, struct.pack(">I", 2910))
        check("a draw at war: crystal 5 -> 6, served on 0x2035",
              CHARS[("acct-Bob", 12)]["crystal"] == 6
              and struct.unpack(">II", got(12, 0x2035)[0]) == (2, 6))
        args.crystal_draw = 7
        for _ in range(4):
            bob.send(0x2081, struct.pack(">I", 2910))
        check("...at most 20 per character per war (1 + 7 + 7 + 5, then none)",
              CHARS[("acct-Bob", 12)]["crystal"] == 25
              and fecampaign.state_of(17)["drawn"]["acct-Bob|12"] == 20)
        CHARS[("acct-Carol", 13)]["crystal"] = 46
        carol.send(0x2081, struct.pack(">I", 2910))
        check("...and never past 50 carried", CHARS[("acct-Carol", 13)]["crystal"] == 50)
        with fecampaign._LOCK:
            fecampaign._STATE[17]["phase"] = fecampaign.PREP
        del SENT[:]
        dave.send(0x2081, struct.pack(">I", 2910))
        check("not at war (prep): nothing drawn", not got(14, 0x2035))
        args.keeps = "off"
        dave.session.pop("crystals_area", None)
        del SENT[:]
        dave.act(fecampaign.pump)                      # the prod path
        adds = got(14, 0x1006)
        check("--war-crystals 1 (even with --keeps off): the pump serves a giant "
              "crystal as a TYPE-7 building (what the draw builder searches for)",
              len(adds) == 1 and struct.unpack_from(">H", adds[0], 7)[0] == 7,
              repr(adds))
        del SENT[:]
        dave.act(fecampaign.pump)
        check("...once", not got(14, 0x1006))
        with fecampaign._LOCK:
            fecampaign._STATE[17]["phase"] = fecampaign.TRUCE
        dave.act(fecampaign.pump)
        check("...and it is removed (0x1004) when the war is over",
              got(14, 0x1004) and not dave.session.get("war_crystals"))
        with fecampaign._LOCK:
            fecampaign._STATE[17]["phase"] = fecampaign.PREP
        args.war_crystals, args.keeps = 0, "on"

        say("fewar 0x2007: build only in a war, by a side, Gate of Hades once")

        def build(p, btype, model):
            body = struct.pack(">HHHHHB", btype, model, 100, 0, 100, 0) + \
                struct.pack(">fff", 0.0, 0.0, 0.0)
            del SENT[:]
            p.send(0x2007, body)
            return [(m, b) for c, m, b in SENT if c == p.charid]
        with fecampaign._LOCK:
            fecampaign._STATE.pop(28, None)
        alice.goto(28)
        r = build(alice, 6, 8)
        check("an obelisk in a field at PEACE -> NG 15 'Can't build here.'",
              r == [(0x100A, struct.pack(">I", 15))], repr(r))
        alice.goto(17)
        with fecampaign._LOCK:
            fecampaign._STATE[17]["members"]["acct-Alice|11"] = {
                "a": "acct-Alice", "c": 11, "side": "atk", "dmg": 0}
        r = build(alice, 4, 4)
        check("a Gate of Hades in war prep, by the attackers -> OK + the building",
              [m for m, b in r][:2] == [0x1009, 0x1006], repr(r))
        r = build(alice, 4, 4)
        check("...a SECOND one on the same side -> NG 40 (one per side per battle)",
              r == [(0x100A, struct.pack(">I", 40))], repr(r))
        r = build(alice, 16, 16)
        check("a Keep on 0x2007 -> NG 7 (keeps are declared with 0xF501)",
              r == [(0x100A, struct.pack(">I", 7))], repr(r))
        r = build(hugo, 6, 8)
        check("a player who is no side of the war -> NG 15",
              r == [(0x100A, struct.pack(">I", 15))], repr(r))
        check("--build-costs 2006: Obelisk 15, Arrow Tower 18, War Craft 20, "
              "Gate of Hades 20", {t: c[0] for t, c in fewar.parse_costs("2006").items()}
              == {0: 18, 19: 18, 5: 20, 6: 15, 29: 15, 4: 20})

        say("--campaign-auto (a test knob) obeys the same rules")
        args.campaign_auto = "on"
        with fecampaign._LOCK:
            for k in (53, 25):
                fecampaign._STATE.pop(k, None)
        for area in (53, 25):
            alice.goto(area)
            alice.act(fecampaign.pump)
            check("auto never declares in %s (%s)" % (area, "own land" if area == 53
                                                     else "a beginner field"),
                  fecampaign.phase_of(area) == fecampaign.PEACE)
        args.campaign_auto = "off"
        with fecampaign._LOCK:
            fecampaign._STATE.clear()
        rep = fecampaign.report(_args(campaign="on"))
        check("the war office no longer says walking out declares a war",
              "walk out" not in rep.lower() and "keep" in rep.lower(), rep)
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
    say("[fe_declare_test] %s -- %d checks" % ("FAIL" if bad else "OK", len(CHECKS)))
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("[fe_declare_test] FAIL: %s" % e)
        sys.exit(1)
