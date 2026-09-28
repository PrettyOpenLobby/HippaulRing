"""0x1100, the war result window."""
import struct
from . import character, sess, wire

# ---------------------------------------------------------------------------
# 0x1100 -- THE WAR RESULT WINDOW, and the thing that un-parks the DUMMY AREA.
#
# KEY 2026-08-25. Field Out completes (0x209A -> 0x1154 -> 0x1157 -> 0x2033 ->
# 0x1045 -> 0x2017 -> 0x1012) and the client then sits in the dummy area
# forever, polling 0x2080 and asking for nothing. It is not waiting on a reply.
# It is waiting on a BYTE, and that byte has exactly three writers.
#
# The scene runs mode 0x0B (thunk 0x04FF7B69 -> 0x04FFA030) as a switch on
# [scene+0x39], jump table 0x04FFAAA8, eleven arms. The tail of it is:
#
#   state 6 sub 1  0x04FFA85F  cmp eax, 2 on 0x050ACED0 (= [0x052E032C]).
#                  == 2 -> set_b59(0), send the settlement (0x04FFB430 -> 0x2033)
#                  != 2 -> set_b59(1)
#   state 7        0x04FFA97E  the dummy-area move lands, logs the client's own
#                              "dummy area move ok", and then FORKS ON b59:
#                                 b59 != 0 -> straight to mode 0x0D
#                                 b59 == 0 -> state 8
#   state 8        0x04FFA9EA  every frame: call 0x05113C70, then
#                              `cmp byte [[0x05336D1C]+0x59], 0; je done`.
#                              Non-zero -> log "settlement complete", mode 0x0D.
#                              That is the whole arm. THIS IS WHERE THE PLAYER
#                              IS STANDING.
#
# We take the == 2 leg (our own log has "settle req sent" then "settling"), so
# b59 is 0 and state 8 is the park. And 0x05113C70 -- whose ONLY two callers
# are inside mode 0x0B -- is this:
#
#     [0x053432BC] -> +0x1A8 -> +0xEC -> +0xAB0   ; four derefs, any NULL = 0
#     0x0508DA60(widget, 1)  0x051155B0(widget, 1) ; show it, start its timer
#
# +0xEC is the WAR RESULT WINDOW. It is built by ONE message, id 0x1100, arm
# 0x05053638 (dispatcher c1): it closes whatever is at [mgr+0xEC], mallocs
# 0xADC, constructs (0x05113CB0), decodes the body (0x05114870) and stores it.
# So state 8 is not a wait for data at all -- it is the war-result screen's own
# frame, and with no window there is nothing to show and nothing to dismiss.
#
# THE REST OF THE CHAIN, so the round trip is written down before it is sent:
# the widget at [win+0xAB0] is a four-state timer (0x051154F0 on [w+0x74]):
# 1 = fade in (1s) -> 2 = hold. The window's own notify handler 0x05114FA0
# (its two buttons live at [win+0x70] and [win+0x74], and code 0x16 hits the
# same path) calls 0x05115000, which puts the widget in state 3 -- and state 3,
# one second later, is the ONLY code in the client that does set_b59(1)
# (0x05115565 -> 0x04FF8010). So: we send 0x1100, the player closes the window,
# and one second after that mode 0x0B leaves for mode 0x0D.
#
# WARNING -- THE ONE WAY THIS CORRUPTS MEMORY, and it is bounded: the body
# carries a u16 COUNT and the loop writes 0x4FC-byte records at [win+0x80] with
# no bounds check. The window ctor builds EXACTLY TWO (0x05113D07, the eh vector
# constructor iterator with count = 2), and 0x80 + 3*0x4FC = 0xF74 overruns a
# 0xADC object. war_result_body() clamps to 2. Do not raise it.
#
# WARNING -- THE BODY IS NOT WHAT febody PRINTS UNAIDED. Two independent holes
# met here: 0x05045EC0 was missing from its READERS (a second u16 primitive,
# fixed 2026-08-25) and _scan walks LINEARLY, so it renders a counted loop body
# once with no marker. The layout below was hand-walked off 0x05114870.
#
#   [u8  kind]     0 = Desert (WarResultDesert, "withdrew from the front line"
#                      -- which is literally what Field Out is), 1 = Win,
#                      2 = Lose. > 2 is range-checked at 0x05114CF7.
#   [u32] -> [win+0xABC]      [u32] -> [win+0xAB8]
#   [u16]  <- 0x05045EC0, the reader febody used to drop
#   [u32]  (stack slot 0x48, not stored in the block that was mapped)
#   [u32] -> [win+0xAC0]      [u32] -> [win+0xAD4]     [u32] -> [win+0xAC4]
#   [u16] -> [win+0xACC]      [u16] -> [win+0xAC8]
#   [u16 count] -> [win+0xAD8]   <= 2
#      count x [u32 m1][u32 m2][u32 m3][u32 m4]   four MASK-gated sub-decoders
#      (0x05075980 / 0x05075A80 / 0x05075AD0 / 0x05075B80). All-zero masks read
#      NOTHING further, which is why count 0 and count 2 are both safe.
#   [u32] -> [win+0xAD0]      [u16]  (stack slot 0x42)
#
# CONFIRMED LIVE 2026-08-25, first try: the window rendered, Close returned the
# player to the map, and the sentinels named EVERY field in ONE screenshot --
# which is the entire argument for shipping 101..111 instead of plausible
# numbers ([[served-zeros-launder-into-choices]]). Eight of the eleven draw:
#
#   wire #2  -> [win+0xABC]  Players / ATTACK      #3  -> [win+0xAB8]  Players / DEFEND
#   wire #6  -> [win+0xAC0]  Score                 #7  -> [win+0xAD4]  Exp
#   wire #8  -> [win+0xAC4]  Crystal               #9  -> [win+0xACC]  Destroyed
#   wire #10 -> [win+0xAC8]  Built                 #12 -> [win+0xAD0]  Rewards
#   wire #4, #5, #13         PARSED BUT NOT DRAWN -- they still have to be on the
#                            wire or every field after them shifts.
#
# "Winner: Unknown (left)" is not a failed lookup: kind 0 skips the army
# resolution at 0x05114CE3 and formats [0x052E6004][0] by design.
#
# THE DEFAULT IS NOW ZEROS, and the flip is the point. Sentinels were right
# while the question was "which field is which" and are wrong the moment a real
# player fields out, because 105 under "Score" is our invented number rendered as
# though it were theirs. We do not simulate a battle, so zero is the honest
# answer; `--war-result-values sentinel` puts the probe back for one run.
WAR_RESULT = (0x1100, "WAR RESULT (unnamed in the NG table -- a NOTIFY)",
              0x05053638)
WAR_RESULT_KINDS = {"desert": 0, "win": 1, "lose": 2}
# The eleven scalars in WIRE order, named off the live screenshot. The three
# marked (undrawn) are parsed and discarded by the window -- they still have to
# be sent or everything after them shifts by their width.
WAR_RESULT_FIELDS = ("players_attack", "players_defend", "(undrawn u16)",
                     "(undrawn u32)", "score", "exp", "crystal", "destroyed",
                     "built", "rewards", "(undrawn u16)")
WAR_RESULT_SENTINELS = (101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111)


def war_result_body(kind, vals, count=0):
    """Build 0x1100 exactly the way 0x05114870 reads it.

    `vals` is the eleven scalars in WIRE order (the order the arm reads them),
    which is deliberately NOT the order they are stored in -- the window writes
    them out of sequence at 0x051149CD..0x05114A51, and mirroring the struct
    here instead of the read order is how a body goes one field out of step.
    """
    a, b, c, d, e, f, g, h, i, j, k = vals
    count = max(0, min(2, int(count)))
    body = struct.pack(">BIIHIIIIHHH",
                       kind & 0xFF, a & 0xFFFFFFFF, b & 0xFFFFFFFF, c & 0xFFFF,
                       d & 0xFFFFFFFF, e & 0xFFFFFFFF, f & 0xFFFFFFFF,
                       g & 0xFFFFFFFF, h & 0xFFFF, i & 0xFFFF, count)
    body += struct.pack(">IIII", 0, 0, 0, 0) * count
    body += struct.pack(">IH", j & 0xFFFFFFFF, k & 0xFFFF)
    return body


def war_result_values(args):
    """The eleven 0x1100 scalars, in WIRE order (see WAR_RESULT_FIELDS).

    'session' (the default since 2026-09-08): every field the window DRAWS
    that this server can measure. Until 2026-09-12 that was Exp alone; the
    war settlement now hands the rest over on the campaign.reward relay
    (fecampaign.on_reward_relay stashes _SESSION["war_result"]), so a player
    who fought a war to its end and then walks out sees SE warsystem04's own
    columns:

        Players/ATTACK, Players/DEFEND  the sign-ups the war settled with
        Score                           this visit's rank-ladder score
        Exp                             the war EXP credited, else the visit's
        Crystal                         crystals drawn in that war
        Destroyed / Built               buildings this session felled / put up
                                        -- and those two are COUNTS, not the
                                        敵城破壊率 percentage SE's page also
                                        lists: the client's own strings are
                                        建物破壊数 / 建物建筑数 (fe-ui-xlate.tsv
                                        .data 553816/553828)
        Rewards                         the Rings the settlement paid

    A player who leaves a war still running has no settlement, and then only
    the four things the visit itself produced are non-zero -- which is the
    honest answer for a desertion. 'zero' is the pre-combat behaviour,
    'sentinel' the probe."""
    mode = getattr(args, "war_result_values", "session")
    if mode == "sentinel":
        return WAR_RESULT_SENTINELS
    vals = [0] * 11
    if mode != "session":
        return tuple(vals)
    at = lambda name, v: vals.__setitem__(WAR_RESULT_FIELDS.index(name),
                                          max(0, int(v or 0)))
    b = sess._SESSION.get("battle", {})
    at("exp", b.get("exp", 0))
    at("score", b.get("score", 0))
    at("destroyed", b.get("destroyed", 0))
    at("built", b.get("built", 0))
    try:                                  # crystals CARRIED (--crystal-reset
        at("crystal",                     # war zeroes them when a war ends,
           character._seeded_value(args, "crystal", # so mid-war this is what was drawn)
                         getattr(args, "crystal", None)))
    except Exception:                                  # noqa: BLE001
        pass
    w = sess._SESSION.get("war_result") or {}
    if w:
        players = w.get("players") or {}
        at("players_attack", players.get("atk", 0))
        at("players_defend", players.get("def", 0))
        at("rewards", w.get("rings", 0))
        if int(w.get("exp") or 0) > 0:
            at("exp", w["exp"])             # the war's EXP, not the visit's
        scores = w.get("scores") or {}
        if scores.get("crystals"):
            at("crystal", scores["crystals"])
    return tuple(vals)


def war_result_kind(args):
    """0x1100's kind byte: 1 Win / 2 Lose when this session's war actually
    settled, 0 Desert otherwise ("You left the front. More is expected." --
    which is what Field Out mid-war IS). --war-result pins one instead."""
    want = getattr(args, "war_result", "auto")
    if want != "auto":
        return WAR_RESULT_KINDS[want], want
    w = sess._SESSION.get("war_result") or {}
    if not w:
        return WAR_RESULT_KINDS["desert"], "desert (no settled war this session)"
    kind = "win" if w.get("won") else "lose"
    return WAR_RESULT_KINDS[kind], "%s (area %s settled)" % (kind, w.get("area"))


def war_result_push(conn, outbound, mode, be, args):
    """Push 0x1100 so mode 0x0B state 8 has a window to run. See WAR_RESULT."""
    kind, why = war_result_kind(args)
    vals = war_result_values(args)
    body = war_result_body(kind, vals, args.war_result_rows)
    mid, name, arm = WAR_RESULT
    wire.send(conn, outbound, wire.inner_msg(mid, body, wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0x%04X %s  kind=%d (%s) rows=%d values=%s "
          "(%d body bytes, arm %08x) -- mode 0x0B state 8 polls 0x05113C70 for "
          "this window every frame; with it absent the dummy area never exits"
          % (mid, name, kind, why, args.war_result_rows,
             args.war_result_values, len(body), arm), flush=True)
    print("[feworld]    %s" % ", ".join(
        "%s=%d" % (n, v) for n, v in zip(WAR_RESULT_FIELDS, vals)
        if not n.startswith("(")), flush=True)
    sess._SESSION.pop("war_result", None)      # drawn once; the next exit is a desert
    print("[feworld]    now CLOSE the window in-game. Its handler 0x05114FA0 "
          "-> 0x05115000 puts [win+0xAB0] in state 3, and one second later "
          "0x05115565 does the only set_b59(1) in the client.", flush=True)
