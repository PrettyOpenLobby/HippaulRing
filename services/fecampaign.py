#!/usr/bin/env python3
"""fecampaign.py -- the WAR CYCLE: sign-ups, phases, an outcome, and land that
changes hands.

PARTIAL: BUILT 2026-09-10, NOT LIVE-TESTED. Nothing here has been on a screen.

## What was already there, and what was not

The client's half of a war has been answered for weeks. It polls
`0x2084 MSG_GET_INFO_OF_PROCLAMATION_OF_WAR_REQUEST` once a second while the
war-prep window is up and takes `0x1127` back as four u16 --
`[attack COUNT][defense COUNT][attack MAX][defense MAX]`, pinned off the
client's own printf at 0x050581df. It asks `0x2018 MSG_DECIDE_COUNTRY_REQUEST`
the moment the battle starts and waits for `0x1020`. It has a name for every
outcome: `0x3036` DEPRIVE (you took it), `0x3037` DEPRIVED (you lost it),
`0x303A/B` KEEP (held / your attack failed), and `0x101D`/`0x101E`
START_WAR_AS_OFFENSIVE / DIFFENSIVE. It draws a field's `status` byte through
its own five-name table: 0 Peace, 1 War Prep, 2 At war, 3 Truce, 4 ServerDown.

Every one of those was served with a CONSTANT. The counts never moved, the
status never changed, and no message ever caused a field to change hands. This
module is the part that was missing: something that decides.

## The state, per area

    peace  --(declare)-->  prep  --(prep_ms)-->  war  --(war_ms)-->  truce
      ^                                                                |
      +------------------------ truce_ms ------------------------------+

`status` on the 0x3031 group record is served from here, so the war map shows
a field's real phase instead of a constant, and the client's own label table
names it.

## Who wins

`--war-decide keeps` (prod) is SE's rule [warsystem03/04]: a keep or castle at
0 hp loses the war; if neither falls before the clock runs out, THE DEFENDER
WINS ("どちらかの城が落とされず、Endに到達すれば防御側の勝利"). Until
2026-09-11 a dented castle lost to a healthier keep at time-up -- that was
ours and it is gone. `signups` (the default) is still OURS: whichever side
put more players in wins, attacker losing ties -- a rule that closes the loop
when no keeps are served. Do not let it calcify into "how FE worked".

## How a war STARTS (2026-09-11) -- the Keep declaration

SE [warsystem01]: a war is declared by BUILDING A KEEP in a peaceful ENEMY
field adjacent to your own land; every member of your nation there is asked;
4 or more accepting starts it, 3 rejections or 60 s cancel it. This client
build carries that flow as its own party protocol, read statically off the
FE_Client.dll dump (base 0x04F90000) -- the 0xF5xx family, dispatched by high
byte at 0x0517e5a0 (called from the world pump at 0x050476a6 after the c0..c3
ladders), bodies schema-serialised BIG-endian by the stream at vtable
0x5261f4c (u32 = 0x5236030, u16 = 0x5235fd0, s16 = 0x5236130):

  out 0xF501 CommandBuildKeepForWar  [u16 type][u16 gx][u16 gy][u16 gz][u16 dir=1]
      sender 0x05072bd0, reached from the build commit 0x05030d40 INSTEAD of
      0x2007 when the building-type row's flags byte (+0xec) has bit 3 -- the
      keep types. Descriptor 0x52983f0 (size 0xa, writer 0x0517b6c0).
  in  0xF502 NotifyBuildToReadyState [u32 party_id][u16 last_time][u16 type]
      [u16 gx][u16 gy][u16 gz][u16 dir][u32 builder][u32 member x5][u8 status x5]
      = 45 bytes (reader 0x0517c6c0, writer 0x0517b830). Arm 0x0517e9c8:
      creates the keep PREVIEW (0x5033720, a 0xb10 building object at the
      grid) and the "Build a keep?" dialog (ctor 0x05033240, 'Keep request'
      title, OK/CANCEL buttons at +0x78/+0x7c); a repeat updates both
      (0x050336c0 copies the 0x30 struct to dlg+0x84). Per frame 0x050334a0
      finds the player's OWN id ([player+0x3c], the self entity id = charid)
      among the five members and switches on its status byte: 0 = buttons
      live, 1/3 = buttons hidden, 2 = hidden + 'Waiting for party reply'.
  out 0xF500 ReplyToBuildKeep [u8 reply] -- button handler 0x050335d0:
      CANCEL -> 1 (dialog closes), OK -> 2 (buttons disabled, dialog stays).
      Only sent when the player's id is one of the five members.
  in  0xF503 NotifyBuildToReadyKeepOK [u32 party_id] -- closes the dialog and
      the preview (0x0517e961).
  in  0xF504 NotifyBuildToReadyKeepError [s16 code][u32 party_id][u32 member
      x5][s16 gold_short][u32 crystal_short] = 32 bytes (reader 0x0517cc00);
      closes both and prints the code through the NG table (0xF504 rows at
      0x05281290..): 1 truce, 2 not adjacent to your nation, 3 not enough
      party members, 4 cancelled, 5 not approved in time, 6 too close to the
      enemy castle, 7 your own land, 8 too close to an enemy base, 9 short of
      gold/crystal (the two shorts are drawn), 10 over the keep limit,
      11 undefined.

The client's words are "party" (this build predates retail's nation-wide
vote -- its in-game book says a 5-person party declares), and the dialog has
FIVE member slots. We follow SE's retail rule inside that shape: the voters
are the builder plus up to four other sessions OF THE BUILDER'S NATION IN THAT
FIELD. KEY: THE BUILDER COUNTS AS ONE ACCEPT -- CHOSEN, per SE's own wording
("4人以上が「承諾」を選択する", 4 or more select Accept, among the nation's
members, of which the builder is one) and per the client, which shows the
builder the status-2 'Waiting for party reply' line, not the buttons. FFSKY
(early FEZ) reads it as four BESIDES the builder; SE outranks it.
`--declare-accepts N` (default 4) counts the builder; 0 or 1 = the builder
alone declares (solo testing). `--declare-rejects` (3) and
`--declare-timeout-ms` (60000) are SE's.
Refusals (can_declare): a capital or an unknown area (11), your own land (7),
a truce (1), a field already preparing/at war or with a vote open (10), a
BEGINNER field -- one touching a capital, never conquerable from SE's 2nd
beta -- (8, CHOSEN code: the nearest text the table has), a field not
adjacent to your nation's land (2), a holder nation already at war (11, SE:
"隣接している国が戦争中の場合"), a keep closer than --keep-min-cells to the
castle (6; the book's own rule "砦から一定の距離をとり", distance CHOSEN), too
few voters present (3), a builder already in another war (11).

PARTIAL: BUILT 2026-09-11 off the static dump only. NOTHING of the 0xF5xx family has
been on a screen; the first live keep build is the proof.
"""
import json
import os
import math
import struct
import threading
import time

fw = None       # the feworld module, handed in by register()

PROCLAMATION_REQ = 0x2084
DECIDE_COUNTRY_REQ = 0x2018

#: the client's own five, off the string table it indexes with the byte
PEACE, PREP, WAR, TRUCE, SERVER_DOWN = 0, 1, 2, 3, 4
PHASE_NAME = {PEACE: "Peace", PREP: "War Prep", WAR: "At war",
              TRUCE: "Truce", SERVER_DOWN: "ServerDown"}

#: the 0xF5xx keep-declaration protocol (see the module docstring)
F_REPLY_KEEP, F_BUILD_KEEP = 0xF500, 0xF501
F_READY_STATE, F_READY_OK, F_READY_ERR = 0xF502, 0xF503, 0xF504
#: 0xF504 codes, the client's own table rows 0x05281290..
KEEP_ERR = {0: "no error", 1: "can't build a keep during a truce",
            2: "not adjacent to your nation's field", 3: "not enough party members",
            4: "keep build cancelled", 5: "keep build not approved in time",
            6: "too close to the enemy castle", 7: "can't start a war on your own land",
            8: "too close to an enemy base", 9: "short of gold/crystal",
            10: "over the keep limit", 11: "undefined error"}
#: the per-member status byte the dialog switches on (0x050334a0), and the
#: reply byte the buttons send (0x050335d0) -- the same numbers on purpose
VOTE_PENDING, VOTE_REJECT, VOTE_ACCEPT = 0, 1, 2
#: 0x1021 MSG_DECIDE_COUNTRY_NG codes (fe-ng-table.tsv); the war screen's arm
#: 0x05117f7c clears the in-flight byte [screen+0x70] and draws the text
DC_NG_ALREADY, DC_NG_NOT_AT_WAR = 5, 7
DC_NG_DEF_FULL, DC_NG_ATK_FULL = 11, 14
DC_NG_NATION_ATTACKS = 17               # "Your nation attacks; can't defend."
DC_NG_NATION_DEFENDS = 16               # "Your nation defends; can't attack."
DC_NG_NO_DEF, DC_NG_NO_ATK = 20, 21     # "Can't join defenders/attackers."

#: RANK ★1..5 [SE 3rd beta, update/index@20060131]: up on CONSECUTIVE wins,
#: down on consecutive losses, everyone starts at 1. 1->2 = 2 wins and 3->4 =
#: 5 wins are SE's; 2->3 = 2, 4->5 = 10 and every step DOWN are atwiki 249
#: (FEZ wiki, RoD-era data) -- the only source that gives them.
RANK_UP = {1: 2, 2: 2, 3: 5, 4: 10}
RANK_DOWN = {5: 2, 4: 2, 3: 3, 2: 3}
#: `--war-rewards`: rod = the 2006 formula (war_reward), ours = the first
#: pass's invented winner bonus (rings_for), no war EXP
DEFAULT_WAR_REWARDS = "rod"
#: the 2006 building caps per side per war -- SHIPPED: FE_BUILDING_DATA
#: +0xe1 (dat.pak, parser 0x051906f8, decoded 2026-09-11), and the 2006 wikis
#: agree (Hordaine 建築物 2006-05 / fewiki: Obelisk 25, Arrow Tower 10, War
#: Craft 1, Gate of Hades 1 -- once per war, no rebuild, SE 4/25). Every
#: other war type ships 1. Keeps are 0xF501's, never 0x2007's.
BUILD_CAPS_2006 = "0=10,19=10,6=25,29=25,5=1,4=1,1=1,2=1,3=1,8=1"
#: how many of the base gauge's 480 dots a thing costs its side (fewiki +
#: Hordaine + FFSKY, RoD): an obelisk destroyed 4, an arrow tower 2, a war
#: craft 4 (fewiki; FFSKY's FEZ-early says 3), a death 1 (FFSKY 1/480,
#: FEZ-early; RoD scaled it by level -- 「Lvが高いほど…ダメージが大きく」 --
#: by an amount nobody wrote down). Gate of Hades: not found -> 0.
BASE_DOTS = 480
BASE_DOTS_2006 = {0: 2, 19: 2, 6: 4, 29: 4, 5: 4, "death": 1}

_LOCK = threading.Lock()
_STATE = {}         # area -> {phase, until, atk, signups, keeps, members, grid, ...}
_NATIONS = {}       # nation -> {"wars": n, "wins": n}  (the Manager's 0x3027)
_VOTES = {}         # area -> the open keep vote (in memory: 60 s never outlives a restart)
_VOTE_SEQ = [0]


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------
def _path(args):
    p = getattr(args, "campaign_file", None)
    if p is None:
        d = os.path.normpath(os.path.join(fw._HERE, os.pardir, "data"))
        p = os.path.join(d if os.path.isdir(d) else fw._HERE, "fe_campaign.json")
    return p


def _row_from(v):
    """One stored row, every field defaulted -- a store written before a
    field existed still loads."""
    return {"phase": int(v.get("phase", PEACE)),
            "until": float(v.get("until", 0) or 0),
            "atk": int(v.get("atk", 0) or 0),
            "signups": {str(a): int(b) for a, b
                        in (v.get("signups") or {}).items()},
            "keeps": {str(a): [int(b[0]), int(b[1])] for a, b
                      in (v.get("keeps") or {}).items()},
            "members": {str(k): dict(m) for k, m
                        in (v.get("members") or {}).items()},
            "grid": ([int(v["grid"][0]), int(v["grid"][1])]
                     if v.get("grid") else None),
            "drawn": {str(k): int(n) for k, n in (v.get("drawn") or {}).items()},
            "built": {str(s): {str(t): int(n) for t, n in (b or {}).items()}
                      for s, b in (v.get("built") or {}).items()},
            # the giant crystals of THIS war: [[amount left, amount], ...] by
            # deposit index (object id = --keep-base + 10 + index)
            "crystals": [[int(c[0]), int(c[1])] for c in (v.get("crystals") or [])],
            # obelisks each side has put up this war: {side: [[gx, gz], ...]}
            "obelisks": {str(s): [[int(p[0]), int(p[1])] for p in (ps or [])]
                         for s, ps in (v.get("obelisks") or {}).items()},
            # per side, per METAMORPHOSIS id: summons this war (feunit's caps)
            "summoned": {str(s): {str(t): int(n) for t, n in (b or {}).items()}
                         for s, b in (v.get("summoned") or {}).items()},
            # KEY: EVERY PLAYER-BUILT BUILDING OF THIS WAR, by object id
            # (2026-09-12). It used to live in the BUILDER's session alone
            # (fewar `war_buildings`), which meant nobody else could see it,
            # nothing could damage it, and two builders allocated the same
            # object ids from 3000 up. The war owns them now.
            "buildings": {str(o): {"t": int(b["t"]), "m": int(b["m"]),
                                   "gx": int(b["gx"]), "gz": int(b["gz"]),
                                   "side": b.get("side"),
                                   "hp": [int(b["hp"][0]), int(b["hp"][1])],
                                   "by": b.get("by")}
                          for o, b in (v.get("buildings") or {}).items()},
            "build_seq": int(v.get("build_seq", 0) or 0),
            # SE's rank entry cost committed per side (--rank-cost)
            "spent": {str(a): int(b) for a, b in (v.get("spent") or {}).items()},
            # when PREP turned into WAR (time.time()): the start of the
            # war-time every member's participation is measured against
            "war_at": float(v.get("war_at", 0) or 0),
            "settled": bool(v.get("settled", False))}


def load(args):
    global _STATE, _NATIONS
    out, nations = {}, {}
    try:
        with open(_path(args), encoding="utf-8") as fh:
            doc = json.load(fh) or {}
        for k, v in doc.items():
            if k == "nations":
                for n, st in (v or {}).items():
                    try:
                        nations[int(n)] = {"wars": int(st.get("wars", 0)),
                                           "wins": int(st.get("wins", 0))}
                    except (TypeError, ValueError, AttributeError):
                        continue
                continue
            try:
                out[int(k)] = _row_from(v)
            except (TypeError, ValueError, AttributeError, KeyError, IndexError):
                continue
    except (OSError, ValueError):
        pass
    with _LOCK:
        _STATE = out
        _NATIONS = nations
    return out


def save(args):
    p = _path(args)
    if not p or p == os.devnull:
        return
    with _LOCK:
        snap = {str(k): v for k, v in _STATE.items()}
        snap["nations"] = {str(k): dict(v) for k, v in _NATIONS.items()}
    tmp = "%s.tmp.%d" % (p, os.getpid())
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(snap, indent=1, sort_keys=True))
        os.replace(tmp, p)
    except OSError as e:
        print("[fecampaign] save failed: %s" % e, flush=True)


def state_of(area):
    """The live row for `area`, or a peace row. Never None. (A shallow copy:
    read it, mutate _STATE under _LOCK.)"""
    with _LOCK:
        r = _STATE.get(int(area))
        return dict(r) if r else {"phase": PEACE, "until": 0.0, "atk": 0,
                                  "signups": {}, "members": {}}


def declared_grid(area):
    """(gx, gz) of the keep a player BUILT to declare the war in `area`, or
    None. feworld.keep_grids puts the attacker's keep there (and so the
    attackers' arrival point) instead of the reflected-castle default."""
    r = state_of(area)
    if r["phase"] == PEACE or not r.get("grid"):
        return None
    return int(r["grid"][0]), int(r["grid"][1])


def phase_of(area):
    return state_of(area)["phase"]


def deadline_ms_of(area):
    """Milliseconds until this area's current phase ends, or None when
    nothing is under way (peace, or no row). feworld's war notify reads it, so
    the countdown the client draws IS the campaign's clock -- not
    --war-deadline-ms, which is what it showed before 2026-09-10."""
    r = state_of(area)
    if r["phase"] == PEACE or not r["until"]:
        return None
    return max(0, int((r["until"] - time.time()) * 1000))


def report(args):
    """One paragraph on the state of the campaign, for an NPC to say."""
    if getattr(args, "campaign", "off") != "on":
        return "The war office is closed. No campaign is under way."
    names = {}
    try:
        names = fw.fegamedata.area_names_en()
    except Exception:                                  # noqa: BLE001
        pass
    with _LOCK:
        rows = sorted((a, dict(r)) for a, r in _STATE.items() if r["phase"] != PEACE)
    if not rows:
        # WARNING: 2026-09-11: this said "walk out to a field on the border and a
        # war will be declared there" -- true of --campaign-auto, never of
        # FE. A war starts when a keep goes up and countrymen agree.
        return ("All quiet on every front. Raise a keep in an enemy field "
                "next to our land, and when four of our nation there agree, "
                "the war begins.")
    parts = []
    for a, r in rows[:4]:
        left = max(0, r["until"] - time.time()) if r["until"] else 0
        k = r.get("keeps") or {}
        kt = ""
        if k.get("def") and k.get("atk"):
            kt = ", castle %d/%d, keep %d/%d" % (k["def"][0], k["def"][1],
                                                 k["atk"][0], k["atk"][1])
        parts.append("%s is in %s, nation %d attacking, %d attackers to %d "
                     "defenders, %d s left%s"
                     % (names.get(a) or ("field %d" % a),
                        PHASE_NAME.get(r["phase"], "?").lower(), r["atk"],
                        r["signups"].get("atk", 0), r["signups"].get("def", 0),
                        left, kt))
    return "; ".join(parts) + "."





def war_cap(args):
    """Per-side cap: 50 v 50 [SE warsystem: 最大50人対50人]."""
    try:
        return max(1, int(getattr(args, "war_cap", 50) or 50))
    except (TypeError, ValueError):
        return 50


# ---------------------------------------------------------------------------
# KEY: THE RANK ENTRY COST -- SE's own balancer, 2026-09-12
#
# SE warsystem01, 「ランクによる戦争参加制限」:
#   「キャラクターのランクは戦争での連勝数・連敗数によって変化します。ランクごとに
#     参戦コストが発生し、ランクが高いキャラクターほど参戦コストが大きくなります。
#     低ランクキャラクターが多い軍は、高ランクキャラクターが多い軍に比べて、より
#     多人数で戦うことができます。」
# = rank moves on win/loss STREAKS (rank_step already does that); each rank
# carries an ENTRY COST that grows with the rank; and an army of low-rank
# characters can field MORE PEOPLE than one of high-rank characters. So the
# real RoD limiter is a ★ COST (see 2026-09-13 below: a GAP between the
# two sides' ★ totals, not the per-side budget first read here), not the flat headcount everyone
# assumes -- and it is what the 2010 Zero-era "overflow queue with preference"
# description is a retelling of. The 50-per-side cap is real too (SE
# warsystem01: 最大50人対50人) and stays as the ceiling.
#
# VERIFIED: 2026-09-13: THE NUMBERS ARE FOUND, AND THEY ARE A DIFFERENCE, NOT A
# BUDGET. All RoD-era, 2006:
#   * fewiki WAR/戦争システム (num/fv_warsys.txt:92-94, captured 2006-06-14):
#     「両陣営のランク合計に大きな差が出ると、ランク合計の高い方に参加制限が
#     かかります」 -- the side whose RANK TOTAL is higher is the one limited;
#     the 50 seats are a separate cap, first come first served.
#   * SE's 2006-05-09 patch table as copied by the Holdein wiki
#     (num/hw_17.txt:298-300): 「ランク / 参戦コスト / ★14差までを、★18差まで
#     可能にしました」 -- a gap of ★14 (beta 3 / launch) widened to ★18.
#   * fewiki's glossary: 「ランク制が導入され防衛 50 vs 攻撃 0 は殆ど見かけなく
#     なった」 -- which a fixed budget could not have done (50 ★1 fit any
#     budget of 50) and a gap rule does.
# So `diff` (the default): a character costs its ★, and a join is refused
# when it would put its side more than --rank-gap (18) ★ ahead of the other.
# The client has no rank message; a refusal reads as that side being FULL,
# as retail's did. fewiki's WAR/ランク page (on disk, num/fv_rank.txt) has NO
# cost table -- nobody ever wrote one -- so cost = ★ is an INFERENCE from
# 「ランク合計」 (medium-high confidence). Unknown: ">18" vs ">=18" (we refuse
# only past 18), and whether a member leaving gives their ★ back (members
# stay counted for the war here, as they always have).
# `rank` and a RANK=N table keep the 09-12 fixed-budget model, now legacy.
# ---------------------------------------------------------------------------
#: what a ★N character costs: its ★ (diff) or its share of a budget (rank)
RANK_COST_RANK = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}


def rank_cost(args, rank):
    """What a ★`rank` character costs to enter a war, or 0 when --rank-cost
    is off (then nothing below does anything)."""
    spec = _rank_mode(args)
    if spec == "off":
        return 0
    r = max(1, min(5, int(rank or 1)))
    if spec in ("rank", "diff"):
        return RANK_COST_RANK.get(r, r)
    table = {}
    for part in spec.split(","):
        k, _, v = part.strip().partition("=")
        try:
            table[int(k, 0)] = int(v, 0)
        except ValueError:
            continue
    return max(0, int(table.get(r, r)))


def _rank_mode(args):
    """--rank-cost as given: 'off', 'diff' (SE's rule), 'rank' or a table."""
    return str(getattr(args, "rank_cost", "diff") or "off").strip().lower()


def rank_gap(args):
    """How many ★ one side may stand AHEAD of the other (--rank-gap; 18 =
    SE's 2006-05-09 patch, 14 before it)."""
    return max(0, int(getattr(args, "rank_gap", 18) or 0))


def rank_over(args, mine, theirs, cost):
    """True when a character costing `cost` would put its side (`mine` ★
    already in) more than the allowed margin past the other (`theirs`) --
    the gap in 'diff' mode, the fixed budget otherwise."""
    if _rank_mode(args) == "diff":
        return mine + cost - theirs > rank_gap(args)
    return mine + cost > rank_budget(args)


def rank_budget(args):
    """A side's total entry cost. 0 = --rank-budget unset = 2 x --war-cap."""
    n = int(getattr(args, "rank_budget", 0) or 0)
    return n if n > 0 else 2 * war_cap(args)


def spent_of(area, side):
    """The entry cost `side` has already committed in `area`'s war."""
    r = state_of(area)
    return int((r.get("spent") or {}).get(side, 0) or 0)


def _my_rank(args):
    """This session's character's ★ rank, 1 when nothing is stored."""
    try:
        v = fw._load_char_field(args, "war_rank", None)
    except Exception:                                  # noqa: BLE001
        v = None
    try:
        return max(1, min(5, int(v or 1)))
    except (TypeError, ValueError):
        return 1


def rank_room(args, area, side, rank=None):
    """(True, cost) when a ★`rank` character still fits `side`'s budget, else
    (False, cost). With --rank-cost off this is always (True, 0)."""
    cost = rank_cost(args, rank if rank is not None else _my_rank(args))
    if cost <= 0:
        return True, 0
    other = "def" if side == "atk" else "atk"
    return (not rank_over(args, spent_of(area, side), spent_of(area, other),
                          cost)), cost


def counts_of(area, args):
    """(attack count, defense count, attack max, defense max) for `area`.

    The caps are one number for the whole world (`--war-cap`) because nothing
    ships a per-field one; the counts are real sign-ups.
    """
    r = state_of(area)
    cap = war_cap(args)
    return (int(r["signups"].get("atk", 0)), int(r["signups"].get("def", 0)),
            cap, cap)


# ---------------------------------------------------------------------------
# who is this session
# ---------------------------------------------------------------------------
def _me():
    """(member key, account, charid) of the session's character."""
    s = fw._SESSION
    acct, cid = s.get("account"), s.get("charid")
    if cid is None:
        return None, acct, None
    return "%s|%d" % (acct, int(cid)), acct, int(cid)


def _nation(args):
    """The session's nation, cached on the session so OTHER sessions can read
    it (voters are found by scanning ext_sessions()); dropped on field exit
    and re-read at the next entry, so a stale nation cannot outlive a field."""
    s = fw._SESSION
    if "campaign_nation" not in s:
        try:
            s["campaign_nation"] = int(fw.nation_of(args) or 0)
        except Exception:                              # noqa: BLE001
            s["campaign_nation"] = 0
    return s["campaign_nation"]


def _war_of(key, exclude=None):
    """The area of the PREP/WAR row `key` is a member of, else None -- one
    war at a time until it ends [SE warsystem01]."""
    if key is None:
        return None
    with _LOCK:
        for a, r in _STATE.items():
            if a != exclude and r["phase"] in (PREP, WAR) \
                    and key in (r.get("members") or {}):
                return a
    return None


def _at_war(nation):
    """Is `nation` a side of any war in PREP/WAR (as attacker or holder)?"""
    with _LOCK:
        rows = [(a, r["atk"]) for a, r in _STATE.items()
                if r["phase"] in (PREP, WAR)]
    return any(int(atk) == int(nation) or fw.territory_owner(a) == int(nation)
               for a, atk in rows)


def is_beginner_field(area):
    """A field directly connected to a capital: never conquerable, never a
    front, from SE's 2nd beta [update/index]."""
    row = fw.fegamedata.areas().get(int(area)) or {}
    return any(int(n) in fw.CAPITAL_GROUP_IDS for n in row.get("neighbours") or ())


def can_declare(args, area, attacker, grid=None):
    """(None, "") when `attacker` may declare on `area`, else (0xF504 code,
    reason). Every player-facing declaration goes through this: the keep vote
    and --campaign-auto. `!campaign declare ... force` skips it."""
    areas = fw.fegamedata.areas()
    try:
        area = int(area)
    except (TypeError, ValueError):
        return 11, "no area"
    if area not in areas or area in fw.CAPITAL_GROUP_IDS:
        return 11, "area %s is a capital or not one of the 95" % area
    if not attacker:
        return 11, "the builder belongs to no nation"
    attacker = int(attacker)
    holder = fw.territory_owner(area, args)
    if attacker == holder:
        return 7, "nation %d already holds area %d" % (attacker, area)
    phase = phase_of(area)
    if phase == TRUCE:
        return 1, "area %d is in truce" % area
    if phase != PEACE:
        return 10, "area %d is already %s" % (area, PHASE_NAME.get(phase))
    with _LOCK:
        if area in _VOTES:
            return 10, "a keep vote is already open in area %d" % area
    if getattr(args, "beginner_fields", "on") == "on" and is_beginner_field(area):
        return 8, ("area %d touches a capital: a beginner field, never "
                   "conquerable" % area)
    if not any(fw.territory_owner(n, args) == attacker
               for n in areas[area]["neighbours"]):
        return 2, ("area %d does not touch any field nation %d holds"
                   % (area, attacker))
    if _at_war(holder):
        return 11, "the holder, nation %d, is already at war" % holder
    if grid is not None:
        try:
            cg = (fw.keep_grids(area) or {}).get("def")
        except Exception:                              # noqa: BLE001
            cg = None
        need = int(getattr(args, "keep_min_cells", 30) or 0)
        if cg and need > 0:
            d = ((int(grid[0]) - cg[0]) ** 2 + (int(grid[1]) - cg[1]) ** 2) ** 0.5
            if d < need:
                return 6, ("keep at grid (%d,%d) is %.0f cells from the castle "
                           "(%d,%d); --keep-min-cells %d"
                           % (grid[0], grid[1], d, cg[0], cg[1], need))
    return None, ""


# ---------------------------------------------------------------------------
# transitions
# ---------------------------------------------------------------------------
def declare(args, area, attacker=None, now=None, grid=None, members=None):
    """Move `area` from peace to PREP. Returns the row, or None if it refuses.

    KEY: The attacker is a NATION, and it must not be the holder -- a field
    cannot attack itself, and serving def_id == atk_id gives the client a war
    with one side. This is the MECHANISM (GM verbs, tests); the RULES a
    player's declaration must pass are can_declare's.
    `grid` = the keep the declarer built (the attacker's keep stands there);
    `members` = {key: member} already enlisted (the builder and those who
    accepted -- they are the attacking army from the start).
    """
    area = int(area)
    if area not in fw.fegamedata.areas():
        return None
    holder = fw.territory_owner(area, args)
    if attacker is None:
        # whoever holds something next door and is not the holder
        for n in fw.fegamedata.areas()[area]["neighbours"]:
            o = fw.territory_owner(n, args)
            if o and o != holder:
                attacker = o
                break
    if not attacker or int(attacker) == int(holder):
        return None
    now = now or time.time()
    row = _row_from({"phase": PREP, "until": now + _ms(args, "war_prep_ms") / 1000.0,
                     "atk": int(attacker)})
    if grid is not None:
        row["grid"] = [int(grid[0]), int(grid[1])]
    for k, m in (members or {}).items():
        row["members"][k] = dict(m)
        row["signups"][m["side"]] = row["signups"].get(m["side"], 0) + 1
    if getattr(args, "keeps", "off") == "on":
        hp = max(1, min(32767, int(getattr(args, "keep_hp", 3000) or 3000)))
        row["keeps"] = {"def": [hp, hp], "atk": [hp, hp]}
    with _LOCK:
        _STATE[area] = row
    save(args)
    return row


def _ms(args, name, default=60000):
    try:
        return float(getattr(args, name, default) or default)
    except (TypeError, ValueError):
        return default


def sign_up(args, area, side, who=None):
    """Record one player choosing a side. `side` is "atk" or "def".

    `who` = {"key", "a", "c", "n"} makes the sign-up a MEMBERSHIP: counted
    once however many times the client re-sends 0x2018 (it sends one per
    battle start, and every re-entry used to count again), and remembered
    for the reward and the one-war rule. Without it (`!campaign join`) it is
    a bare count, as before."""
    area = int(area)
    if side not in ("atk", "def"):
        return None
    with _LOCK:
        r = _STATE.get(area)
        if not r or r["phase"] not in (PREP, WAR):
            return None
        members = r.setdefault("members", {})
        if who and who.get("key") in members:
            return r["signups"].get(members[who["key"]]["side"], 0)
        if r["signups"].get(side, 0) >= war_cap(args):
            return "full"
        # SE's rank ENTRY COST (--rank-cost diff): a side may not stand more
        # than --rank-gap ★ ahead of the other, so a low-rank army is bigger
        # (checked again here, under the lock, against a racing join)
        cost = int(who.get("cost") or 0) if who else 0
        if cost > 0:
            sp = r.setdefault("spent", {})
            spent = int(sp.get(side, 0) or 0)
            other = int(sp.get("def" if side == "atk" else "atk", 0) or 0)
            if rank_over(args, spent, other, cost):
                return "full"
            sp[side] = spent + cost
        r["signups"][side] = r["signups"].get(side, 0) + 1
        n = r["signups"][side]
        if who and who.get("key"):
            members[who["key"]] = {"a": who.get("a"), "c": who.get("c"),
                                   "n": who.get("n"), "side": side, "dmg": 0,
                                   "pc_dmg": 0, "kills": 0, "cost": cost,
                                   "t": time.time()}
    save(args)
    return n


def join_side(args, area, army=None):
    """(side, None) for this session's character in `area`'s war, or
    (None, 0x1021 code). The 2006 join rules:
      * your nation's war: attacker nation -> "atk", holder -> "def";
      * someone ELSE's war: only on the side with FEWER players [SE
        polnews 8521, the 9/8 rule; a tie takes either];
      * one war at a time until it ends [SE warsystem01] -- checked by the
        caller against the member key.
    `army` is the 0x2018 u32; it is honoured only when it equals one of the
    two nation ids (its encoding is otherwise unread)."""
    r = state_of(area)
    if r["phase"] not in (PREP, WAR):
        return None, DC_NG_NOT_AT_WAR
    mine = _nation(args)
    atk, holder = int(r["atk"]), fw.territory_owner(area, args)
    wanted = ("atk" if army == atk else "def" if army == holder else None) \
        if army not in (None, 0) else None
    a, d, _am, _dm = counts_of(area, args)
    if mine and mine == atk:
        if wanted == "def":
            return None, DC_NG_NATION_ATTACKS
        side = "atk"
    elif mine and mine == holder:
        if wanted == "atk":
            return None, DC_NG_NATION_DEFENDS
        side = "def"
    else:
        if getattr(args, "foreign_join", "smaller") == "off":
            return None, DC_NG_NO_DEF if wanted == "def" else DC_NG_NO_ATK
        smaller = "atk" if a < d else "def" if d < a else None
        if wanted and smaller and wanted != smaller:
            return None, DC_NG_NO_ATK if wanted == "atk" else DC_NG_NO_DEF
        side = wanted or smaller or "def"
    if (a if side == "atk" else d) >= war_cap(args):
        return None, DC_NG_ATK_FULL if side == "atk" else DC_NG_DEF_FULL
    # ...and the rank budget, SE's own limiter. The client has no "your rank
    # is too expensive" code, so a spent budget reports as that side being
    # FULL -- which is what it is [SE warsystem01: 「キャラクターのランクによる
    # 戦争参加制限が設けられているため、参戦できない場合もあります」].
    room, cost = rank_room(args, area, side)
    if not room:
        other = "def" if side == "atk" else "atk"
        print("[fecampaign] area %s: a ★%d joining %s (%d★ in vs %d★ on %s) "
              "would pass the %s -- reported as that side being full"
              % (area, cost, side, spent_of(area, side), spent_of(area, other),
                 other, "★%d gap (--rank-gap)" % rank_gap(args)
                 if _rank_mode(args) == "diff"
                 else "budget of %d" % rank_budget(args)), flush=True)
        return None, DC_NG_ATK_FULL if side == "atk" else DC_NG_DEF_FULL
    return side, None


def keep_damage(args, area, side, dmg):
    """Take `dmg` off `side`'s keep in `area`. Returns the new hp, or None
    when there is no war on there (peace/prep/truce) or no such keep. The
    damage is credited to the hitting session's member row -- the one score
    this server can measure (the result screen's 建築物与ダメージ, SE
    warsystem04), used for the winners' Ring bonus."""
    key = _me()[0]
    with _LOCK:
        r = _STATE.get(int(area))
        if not r or r["phase"] != WAR:
            return None
        k = (r.get("keeps") or {}).get(side)
        if not k:
            return None
        before = int(k[0])
        k[0] = max(0, before - int(dmg))
        new = k[0]
        m = (r.get("members") or {}).get(key)
        if m is not None:
            m["dmg"] = int(m.get("dmg", 0)) + (before - new)
    save(args)
    return new


def pc_damage(args, area, dmg, kill=False):
    """Credit THIS session's member row with damage dealt to an enemy PLAYER,
    and optionally the kill that ended them.

    SE warsystem04 scores a war in four categories -- PC 与ダメージ,
    キル数, 建築物与ダメージ, クリスタル獲得数 -- and settle() had the
    first two hardcoded to 0 with the comment "no PvP on this server". PvP
    landed 2026-09-12 (fepvp settles a cast against every enemy peer in
    range), so the two grades that decide a third of the Ring bonus are
    measurable now. Returns (pc_dmg, kills) after the credit, or None."""
    key = _me()[0]
    if key is None or area is None:
        return None
    with _LOCK:
        r = _STATE.get(int(area))
        if not r or r["phase"] != WAR:
            return None
        m = (r.get("members") or {}).get(key)
        if m is None:
            return None
        m["pc_dmg"] = int(m.get("pc_dmg", 0) or 0) + max(0, int(dmg))
        if kill:
            m["kills"] = int(m.get("kills", 0) or 0) + 1
        out = (m["pc_dmg"], int(m.get("kills", 0) or 0))
    save(args)
    return out


def base_dots(args, cause):
    """Dots of the 480-dot base gauge `cause` costs (a building TYPE id, or
    "death"): --base-dots SPEC ('TYPE=N,...,death=N'; '2006' =
    BASE_DOTS_2006; 'off' = none)."""
    spec = str(getattr(args, "base_dots", "2006") or "off").strip().lower()
    if spec == "off":
        return 0
    if spec == "2006":
        table = BASE_DOTS_2006
    else:
        table = {}
        for part in spec.split(","):
            k, _, v = part.strip().partition("=")
            try:
                table[k if k == "death" else int(k, 0)] = int(v, 0)
            except ValueError:
                continue
    return max(0, int(table.get(cause, 0)))


def base_hit(args, area, side, cause, conn=None, outbound=None, mode=None,
             be=None, dots=None):
    """`side`'s BASE (its keep/castle) loses what `cause` costs -- the keep's
    max HP (--keep-hp) x dots/480, at least 1 when the cause costs anything.
    No member is credited (nobody swung). Shown on this session's client
    when it was served that keep, and relayed to the field (campaign.keep).
    Returns the new hp, or None (no war, no keeps, a free cause).

    `dots` overrides the --base-dots table for a cause that is not a fixed
    price -- the obelisk drain, whose size is the territory share."""
    dots = base_dots(args, cause) if dots is None else max(0, int(dots))
    if dots <= 0 or area is None:
        return None
    with _LOCK:
        r = _STATE.get(int(area))
        if not r or r["phase"] != WAR:
            return None
        k = (r.get("keeps") or {}).get(side)
        if not k:
            return None
        dmg = max(1, int(round(int(k[1]) * dots / float(BASE_DOTS))))
        before = int(k[0])
        k[0] = max(0, before - dmg)
        new = k[0]
    save(args)
    obj = int(getattr(args, "keep_base", 2900) or 2900) + (0 if side == "def" else 1)
    print("[fecampaign] area %s: the %s base loses %d (%d/%d dots of %d: %s) "
          "-> %d" % (area, side, before - new, dots, BASE_DOTS, k[1],
                     cause if isinstance(cause, str)
                     else "%s destroyed" % fw.BUILDING_TYPES.get(cause, cause),
                     new), flush=True)
    s = fw._SESSION
    if obj in (s.get("keeps") or {}):
        fw.building_hp_push(conn, outbound, mode, be, args, obj, new)
        if new <= 0:
            fw.mob_kill_push(conn, outbound, mode, be, args, obj)
            s["keeps"].pop(obj, None)
    fw.ext_post("campaign.keep", {"area": int(area), "obj": obj, "side": side,
                                  "hp": int(new)})
    return new


def keep_fallen(area):
    """'atk'/'def' whose keep is at 0 in a war, else None."""
    r = state_of(area)
    if r["phase"] != WAR:
        return None
    for side in ("def", "atk"):
        k = (r.get("keeps") or {}).get(side)
        if k and int(k[0]) <= 0:
            return side
    return None


def decide(args, area, forced=None):
    """Resolve a war. Returns ("atk"|"def", old holder, new holder).

    `keeps` is SE's rule; `signups` is ours -- see the module docstring.
    """
    r = state_of(area)
    rule = getattr(args, "war_decide", "signups")
    a, d = r["signups"].get("atk", 0), r["signups"].get("def", 0)
    if forced in ("atk", "def"):
        winner = forced
    elif rule == "attacker":
        winner = "atk"
    elif rule == "defender":
        winner = "def"
    elif rule == "keeps" and r.get("keeps"):
        # THE KEEPS DECIDE: a keep or castle at 0 loses outright. WARNING: At
        # time-up the DEFENDER WINS, full stop [SE warsystem03: "どちらかの城
        # が落とされず、Endに到達すれば防御側の勝利"]. Until 2026-09-11 the side
        # whose keep kept more of its hp won, so an attacker who dented the
        # castle more than the defenders dented the keep TOOK THE FIELD at
        # time-up -- the audit's item 5. Obelisk territory and player kills
        # also drained a base in 2006, but only ever mattered at 0.
        kd, ka = r["keeps"].get("def") or [1, 1], r["keeps"].get("atk") or [1, 1]
        if int(kd[0]) <= 0:
            winner = "atk"
        else:
            winner = "def"
    else:                                   # "signups", the default
        winner = "atk" if a > d else "def"
    old = fw.territory_owner(area, args)
    new = int(r["atk"]) if winner == "atk" else old
    if winner == "atk" and new and new != old:
        fw.territory_set(area, new, args)
    return winner, old, new


# ---------------------------------------------------------------------------
# after a war: rank, Rings, the nation's tally
# ---------------------------------------------------------------------------
def rank_step(rank, streak, won):
    """One war's effect on (rank, streak). streak > 0 = consecutive wins,
    < 0 = consecutive losses; it restarts at 0 whenever the rank moves."""
    rank = max(1, min(5, int(rank or 1)))
    streak = int(streak or 0)
    if won:
        streak = streak + 1 if streak > 0 else 1
        if rank < 5 and streak >= RANK_UP[rank]:
            rank, streak = rank + 1, 0
    else:
        streak = streak - 1 if streak < 0 else -1
        if rank > 1 and -streak >= RANK_DOWN[rank]:
            rank, streak = rank - 1, 0
    return rank, streak


def rings_for(args, rank, won, dmg):
    """`--war-rewards ours` ONLY (the 2026-09-11 first pass): Rings = rank
    win or lose (SE 5/9), and for winners an INVENTED bonus -- --war-ring-win
    flat plus one Ring per --war-ring-dmg keep damage. The default is
    war_reward() below, the RoD formula the 2006 wiki published."""
    n = max(1, int(rank or 1))
    if won:
        n += max(0, int(getattr(args, "war_ring_win", 2) or 0))
        per = int(getattr(args, "war_ring_dmg", 1000) or 0)
        if per > 0:
            n += max(0, int(dmg or 0)) // per
    return n


# ---------------------------------------------------------------------------
# KEY: THE RoD WAR REWARD (2026-09-11, second pass) -- fewiki `WAR/報酬`, the
# 2006-05-22 revision (captured 2006-05-26), FFSKY bonus.htm (2007) agreeing:
#
#   EXP   common = base(Lv) x participation factor x enemy-base-destroyed %
#         bonus  = base(Lv) x (sum of the four grades' bonus %)
#         (worked example on the page: 178770x60%x100% + 178770x(15+10+25+5)%
#          = 205,585; fractions truncated -- 「小数点の扱いに関しては基本切捨て」)
#         a war raises you AT MOST ONE LEVEL; at the Lv40 cap EXP stops.
#   Rings everyone: your rank (★); winners also 0-3 for time present and
#         S+3 / A+2 / B+1 per category; losers get none of the extras
#         (「戦争に敗北すると追加取得の条件を満たしていても0個」).
#
# The four categories are the result screen's: PC damage, kills, building
# damage, crystals. All four are measured since 2026-09-12: building damage in
# keep_damage, crystals in the row's `drawn`, and PC damage + kills in
# pc_damage (credited from fepvp's own report, on the attacker's thread).
# PARTIAL: Only the building and crystal halves have ever run in a real war.
# ---------------------------------------------------------------------------
#: base EXP by level, Lv1..40. The column is FFSKY information.htm / fewiki
#: 2007; its Lv40 (178,700) matches RoD's dated 178,770 (fewiki 2006-05-22),
#: so the column is taken as unchanged from RoD -- ASSUMED for Lv1..39.
#: Lv40 uses RoD's own 178,770; Lv15 uses fewiki's 750 (FFSKY prints 780;
#: 750 is the one its own NEXT/base ratio column agrees with).
WAR_BASE_EXP = (50, 64, 75, 83, 90, 109, 125, 153, 178, 233,
                281, 352, 472, 578, 750, 909, 1166, 1461, 1857, 2333,
                2878, 3611, 4615, 5714, 6956, 8000, 10909, 13666, 16666, 20833,
                25316, 32558, 40425, 48543, 61946, 76612, 95588, 117449, 147239,
                178770)
#: EXP bonus per grade, in percent of base (fewiki WAR/報酬)
GRADE_EXP_PCT = {"S": 25, "A": 20, "B": 15, "C": 10, "D": 5, "E": 0}
#: winners' extra Rings per grade
GRADE_RINGS = {"S": 3, "A": 2, "B": 1}
WAR_CATEGORIES = ("pc_dmg", "kills", "bld_dmg", "crystals")
#: grade FLOORS (S, A, B, C, D) per category; below D is E. fewiki WAR/報酬
#: (RoD) with FFSKY's PC-damage C/D. WARNING: Building damage: S 29,000 and A
#: 20,000 are the wiki's (players saw A at 26,631 and 28,900); its B "9,500"
#: contradicts a player's 9,553 = C, so B is the lowest B anyone reported,
#: 9,977 (ASSUMED); C and D are unknown and take the PC-damage ladder's
#: halving (B/2, B/4 -- ASSUMED). Crystals C/D carry the wiki's own "?".
GRADE_FLOORS = {
    "kills": (20, 15, 10, 5, 3),
    "pc_dmg": (20000, 13000, 6500, 3250, 1625),
    "bld_dmg": (29000, 20000, 9977, 4989, 2494),
    "crystals": (200, 150, 100, 75, 50),
}


def grade(category, value):
    """'S'..'E' for one result-screen category."""
    v = int(value or 0)
    for g, floor in zip("SABCD", GRADE_FLOORS[category]):
        if v >= floor:
            return g
    return "E"


def war_base_exp(level):
    """WAR_BASE_EXP for `level` (1..40; above 40 holds Lv40's)."""
    L = max(1, int(level or 1))
    return WAR_BASE_EXP[min(L, len(WAR_BASE_EXP)) - 1]


def participation_step(frac):
    """The participation factor in fifths: 0-20% present -> 1 (20%),
    20-40% -> 2 (40%), ... 80-100% -> 5 (100%) (fewiki WAR/報酬)."""
    f = max(0.0, min(1.0, float(frac)))
    step = 1
    while step < 5 and f > step * 0.2 + 1e-9:
        step += 1
    return step


def participation_factor(frac):
    return participation_step(frac) / 5.0


def time_rings(frac):
    """Winners' 0-3 Rings for the share of the war they were present:
    81-100% -> 3, 50-80% -> 2, under 50% -> 1, nothing present -> 0.
    fewiki's table puts the bands at 81 / 51 / 0-50 WITH ITS OWN "?"; its
    worked example gives 2 Rings for "about half the war" (2+0+1+3+0+3 = 9),
    so the 2-Ring band starts at 50% here (ASSUMED between the two)."""
    f = float(frac)
    if f <= 0:
        return 0
    return 3 if f >= 0.81 else 2 if f >= 0.5 - 1e-9 else 1


def war_reward(level, won, frac, destroyed, scores, rank=1):
    """The RoD reward for one participant: {"exp", "rings", "grades",
    "common", "bonus"}. `frac` = share of the war present (0..1),
    `destroyed` = the ENEMY base's damage share (0..1), `scores` = the four
    categories' raw values, `rank` = ★ before this war."""
    grades = {c: grade(c, (scores or {}).get(c, 0)) for c in WAR_CATEGORIES}
    base = war_base_exp(level)
    # integer-first so 178770 x 60% x 100% is 107262, not 107261.999...
    common = int(base * participation_step(frac)
                 * max(0.0, min(1.0, float(destroyed))) / 5.0 + 1e-9)
    bonus = base * sum(GRADE_EXP_PCT[g] for g in grades.values()) // 100
    rings = max(1, int(rank or 1))
    if won:
        rings += time_rings(frac) + sum(GRADE_RINGS.get(g, 0)
                                        for g in grades.values())
    return {"exp": common + bonus, "common": common, "bonus": bonus,
            "rings": rings, "grades": grades}


def apply_war_exp(args, c, gained):
    """Credit `gained` war EXP to one STORED character dict `c` (its own
    class, look1): at most ONE level per war (fewiki: 「戦争で上がるレベルは
    最大1」), nothing at the cap (Lv40: 「経験値は入りません」). The level-up
    is applied here, not left to the client's 0x2059: the reward reaches
    players who have logged off. Returns (credited, old level, new level)."""
    try:
        cls = int(c.get("look1", 0) or 0) & 0xFF
    except (TypeError, ValueError):
        cls = 0
    levels = c.get("class_levels") if isinstance(c.get("class_levels"), dict) else {}
    exps = c.get("class_exp") if isinstance(c.get("class_exp"), dict) else {}
    try:
        lv = int(levels.get(str(cls), levels.get(cls, 1)) or 1)
        into = int(exps.get(str(cls), exps.get(cls, 0)) or 0)
    except (TypeError, ValueError):
        lv, into = 1, 0
    cap = fw.class_level_cap(args)
    gained = max(0, int(gained or 0))
    if lv >= cap or gained <= 0:
        return 0, lv, lv
    new_lv, new_into = lv, into + gained
    need = fw.exp_need(args, lv)
    if new_into >= need:
        new_lv, new_into = lv + 1, new_into - need
    # one level at most: stop one short of the NEXT level's threshold
    new_into = min(new_into, fw.exp_need(args, new_lv) - 1)
    credited = max(0, (new_into - into) if new_lv == lv
                   else (need - into) + new_into)
    levels = dict(levels)
    levels[str(cls)] = new_lv
    levels.pop(cls, None)
    exps = dict(exps)
    exps[str(cls)] = new_into
    exps.pop(cls, None)
    c["class_levels"], c["class_exp"] = levels, exps
    try:
        c["exp"] = int(c.get("exp") or 0) + credited
    except (TypeError, ValueError):
        c["exp"] = credited
    return credited, lv, new_lv


def _member_frac(r, m, end):
    """Share of the war this member was present: from sign-up (member "t")
    or the war's start, whichever is later, to its end. WARNING: ASSUMED: leaving
    the field is not tracked, so a member counts as present to the end."""
    start = float(r.get("war_at") or 0)
    if not start or end <= start:
        return 1.0
    joined = max(start, float(m.get("t") or start))
    return max(0.0, min(1.0, (end - joined) / (end - start)))


def _enemy_destroyed(r, side, won):
    """The ENEMY base's damage share (0..1). With no keeps served there is
    no base to measure: the winner counts 100%, the loser 0% (ASSUMED)."""
    enemy = "def" if side == "atk" else "atk"
    k = (r.get("keeps") or {}).get(enemy)
    if not k or int(k[1]) <= 0:
        return 1.0 if won else 0.0
    return max(0.0, min(1.0, (int(k[1]) - int(k[0])) / float(k[1])))


def _update_char(account, charid, mutate):
    """Run `mutate(char_dict)` on one STORED character of any account (the
    rewards reach players who have logged off). Tests replace this."""
    try:
        import felobby
    except ImportError:
        return False

    def m(roster):
        hit = False
        for c in roster:
            if c.get("charid") == charid:
                mutate(c)
                hit = True
        return hit
    return bool(felobby.update_roster(felobby._default_store(), account, m))


def settle(args, area, winner, defender):
    """Pay out a finished war ONCE: every member's rank steps, Rings are
    credited to the stored `ring` (the column --ring seeds and 0x2024's RING
    field serves), war EXP goes into the stored level/progress (--war-rewards
    rod: war_reward + apply_war_exp -- win or lose, Netz 2006 「勝っても負け
    ても経験値が貰えます」), the two nations' war/win tallies move, and every
    member still online is told through the campaign.reward relay so their
    RING and level are re-served on their own thread. `defender` = the
    holder BEFORE decide() moved the field. Returns [(key, won, rank, rings)]."""
    end = time.time()
    with _LOCK:
        r = _STATE.get(int(area))
        if not r or r.get("settled"):
            return []
        r["settled"] = True
        members = {k: dict(m) for k, m in (r.get("members") or {}).items()}
        drawers = [k for k in (r.get("drawn") or {}) if k not in members]
        drawn = {k: int(n) for k, n in (r.get("drawn") or {}).items()}
        war = {"war_at": r.get("war_at"),
               "keeps": {s: list(k) for s, k in (r.get("keeps") or {}).items()}}
        atk = int(r["atk"])
        signups = dict(r.get("signups") or {})
    holder = int(defender or 0)
    mode = str(getattr(args, "war_rewards", DEFAULT_WAR_REWARDS) or "rod")
    reset = getattr(args, "crystal_reset", "war") == "war"
    # CRYSTAL COUNTS ONLY DURING WAR: everyone who fought or drew in this one
    # carries 0 once it is over -- written here so a player who has logged
    # off is reset too; an online one is re-served by _crystal_expire when
    # their session sees the war end.
    for key in drawers if reset else ():
        acct, _, cid = key.rpartition("|")
        try:
            _update_char(acct, int(cid), lambda c: c.__setitem__("crystal", 0))
        except (TypeError, ValueError):
            continue
        except Exception as e:                         # noqa: BLE001
            print("[fecampaign] crystal reset for %s failed: %s" % (key, e),
                  flush=True)
    out = []
    for key, m in sorted(members.items()):
        won = m.get("side") == winner
        got = {}
        frac = _member_frac(war, m, end)
        scores = {"pc_dmg": int(m.get("pc_dmg") or 0),
                  "kills": int(m.get("kills") or 0),
                  "bld_dmg": int(m.get("dmg") or 0),
                  "crystals": drawn.get(key, 0)}
        destroyed = _enemy_destroyed(war, m.get("side"), won)

        def mutate(c, won=won, m=m, got=got, frac=frac, scores=scores,
                   destroyed=destroyed):
            before = int(c.get("war_rank") or 1)
            rank, streak = rank_step(before, c.get("war_streak"), won)
            if mode == "ours":
                rings = rings_for(args, before, won, m.get("dmg"))
            else:
                cls = int(c.get("look1", 0) or 0) & 0xFF
                lv_tab = c.get("class_levels") if isinstance(
                    c.get("class_levels"), dict) else {}
                try:
                    lv = int(lv_tab.get(str(cls), lv_tab.get(cls, 1)) or 1)
                except (TypeError, ValueError):
                    lv = 1
                rw = war_reward(lv, won, frac, destroyed, scores, before)
                rings = rw["rings"]
                credited, lv0, lv1 = apply_war_exp(args, c, rw["exp"])
                got.update(exp=credited, earned=rw["exp"], level=(lv0, lv1),
                           grades=rw["grades"], frac=frac, destroyed=destroyed)
            c["war_rank"], c["war_streak"] = rank, streak
            c["war_wins" if won else "war_losses"] = \
                int(c.get("war_wins" if won else "war_losses") or 0) + 1
            # a stored 0 is a real 0; only a MISSING ring falls back to the
            # --ring seed (the _seeded_value rule)
            have = c.get("ring")
            if have is None:
                have = getattr(args, "ring", 0) or 0
            c["ring"] = int(have) + rings
            if reset:
                c["crystal"] = 0
            got.update(rank=rank, rings=rings, before=before, ring=c["ring"])
        ok = False
        try:
            if m.get("a") and m.get("c") is not None:
                ok = _update_char(m.get("a"), int(m["c"]), mutate)
        except Exception as e:                         # noqa: BLE001
            print("[fecampaign] reward for %s failed: %s" % (key, e), flush=True)
        if not ok:
            print("[fecampaign] area %s: member %s has no stored character -- "
                  "no reward" % (area, key), flush=True)
            continue
        out.append((key, won, got["rank"], got["rings"]))
        print("[fecampaign] area %s: %s %s -- rank %d -> %d, +%d Rings (now %d)%s"
              % (area, key, "WON" if won else "lost", got["before"], got["rank"],
                 got["rings"], got["ring"],
                 "" if "exp" not in got else
                 "; war EXP +%d of %d (present %.0f%%, enemy base %.0f%% down, "
                 "grades %s; Lv%d -> Lv%d, one level at most)"
                 % (got["exp"], got["earned"], got["frac"] * 100,
                    got["destroyed"] * 100,
                    "/".join(got["grades"][c] for c in WAR_CATEGORIES),
                    got["level"][0], got["level"][1])), flush=True)
        if m.get("c") is not None:
            cid = int(m["c"])
            # everything the WAR RESULT window draws goes with it: the window
            # is pushed on Field Out, on the player's own thread, long after
            # this settlement ran on somebody else's (feworld.war_result_*)
            fw.ext_post("campaign.reward",
                        {"charid": cid, "won": won, "area": int(area),
                         "rank": got["rank"], "rings": got["rings"],
                         "exp": got.get("exp", 0),
                         "side": m.get("side"),
                         "players": {"atk": int(signups.get("atk", 0)),
                                     "def": int(signups.get("def", 0))},
                         "scores": dict(scores),
                         "destroyed": destroyed},
                        to=lambda s, _n, cid=cid: s.get("charid") is not None
                        and int(s["charid"]) == cid, include_me=True)
    win_nation = atk if winner == "atk" else holder
    with _LOCK:
        for n in {atk, holder}:
            if n:
                st = _NATIONS.setdefault(int(n), {"wars": 0, "wins": 0})
                st["wars"] += 1
        if win_nation:
            _NATIONS.setdefault(int(win_nation), {"wars": 0, "wins": 0})["wins"] += 1
    save(args)
    return out


def on_reward_relay(ctx, payload):
    """A war this character fought just paid out: re-serve RING (0x2024,
    maskA +0x4f0 -- the field the Status screen draws) from the store, and
    keep the settlement for the WAR RESULT window (SE warsystem04).

    The window is built by 0x1100 when the player LEAVES the field, which can
    be minutes after this relay lands and is the only moment the client will
    render a result at all -- so the numbers wait in the session until then
    (feworld.war_result_values, --war-result-values session)."""
    try:
        fw._SESSION["war_result"] = {
            "won": bool(payload.get("won")),
            "area": payload.get("area"),
            "rings": int(payload.get("rings") or 0),
            "exp": int(payload.get("exp") or 0),
            "players": dict(payload.get("players") or {}),
            "scores": dict(payload.get("scores") or {}),
            "destroyed": float(payload.get("destroyed") or 0.0),
            "t": time.time(),
        }
    except Exception as e:                             # noqa: BLE001
        print("[fecampaign]    war result stash failed: %s" % e, flush=True)
    try:
        fw.wallet_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args)
    except Exception as e:                             # noqa: BLE001
        print("[fecampaign]    RING re-serve failed: %s" % e, flush=True)
    if int(payload.get("exp") or 0) > 0:
        # settle() wrote the level/progress to the STORE from another thread;
        # this session's cached copies are stale -- drop them and re-serve
        # the level, next and progress (0x1075 bits 0x1|0x2|0x4) + SP
        s = fw._SESSION
        s.pop("class_exp", None)
        s.pop("exp", None)
        try:
            fw.level_state_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be,
                                ctx.args, "war EXP +%s" % payload.get("exp"))
        except Exception as e:                         # noqa: BLE001
            print("[fecampaign]    level re-serve failed: %s" % e, flush=True)
    print("[fecampaign]    war over: %s -- rank %s, +%s Rings, +%s war EXP"
          % ("won" if payload.get("won") else "lost", payload.get("rank"),
             payload.get("rings"), payload.get("exp", 0)), flush=True)


def nation_stats(nation):
    with _LOCK:
        return dict(_NATIONS.get(int(nation)) or {"wars": 0, "wins": 0})


_POP_CACHE = {"t": 0.0, "rows": {}}


def _population(args):
    """{nation: stored characters} -- feforce.members over the whole store,
    cached for a minute (0x206A is asked five times per world entry)."""
    now = time.time()
    if now - _POP_CACHE["t"] < 60 and _POP_CACHE["rows"]:
        return _POP_CACHE["rows"]
    rows = {}
    try:
        import feforce
        for _acct, _name, _cid, f in feforce.members(args):
            if f:
                rows[int(f)] = rows.get(int(f), 0) + 1
    except Exception:                                  # noqa: BLE001
        rows = {}
    _POP_CACHE.update(t=now, rows=rows)
    return rows


def king_messages(args):
    """--king-message N:TEXT (repeatable) -> {nation: text}."""
    out = {}
    for spec in getattr(args, "king_message", None) or []:
        n, _, text = str(spec).partition(":")
        try:
            out[int(n, 0)] = text
        except ValueError:
            continue
    return out


def force_vals(args, force_id, vals):
    """The Manager's nation record (0x3027 FORCE_FIELDS) with the LIVE
    numbers in it instead of zeros: territory held (the 95-area board, as the
    PAIR LIST whose count the client draws), wars fought and won (this
    module's tally), population (stored characters of that nation, into
    force+0x34) and the King's message (--king-message, unless `!force
    kingmsg` already set one). Returns a NEW dict."""
    out = dict(vals or {})
    fid = int(force_id)
    held = sorted(a for a in fw.fegamedata.areas()
                  if fw.territory_owner(a, args) == fid)
    st = nation_stats(fid)
    # KEY: THE ●LINE'S "FIELDS" IS THE PAIR COUNT (2026-09-12, live: it always
    # read 0). force+0x38 is drawn as 制圧フィールド数 and is filled by the
    # COUNT of the record's (u32,u32) pair list -- so one pair per field this
    # nation holds, the area id as the first element and 0 as the second
    # (neither is ever read: see FORCE_FIELDS). FormerNumTerritory stays the
    # same number, which is what makes the screen's delta read "(+0)" until
    # something moves; a real previous-count would need a per-war snapshot.
    out["pairs"] = [(a, 0) for a in held]
    out["FormerNumTerritory"] = len(held)
    out["WarTotalNum"] = out["FormerWarTotalNum"] = st["wars"]
    out["WinTotalNum"] = out["FormerWinTotalNum"] = st["wins"]
    if getattr(args, "force_population", "on") == "on":
        pop = _population(args).get(fid, 0)
        # +0x34 is the ●line's 国民数 and was an unnamed zero until 2026-09-12;
        # FormerNumMember is the same number for the delta.
        out["f34"] = pop
        out["FormerNumMember"] = pop
    km = king_messages(args).get(fid)
    if km and not out.get("KingMessage"):
        out["KingMessage"] = km
    return out


# ---------------------------------------------------------------------------
# the tick
# ---------------------------------------------------------------------------
def pump(ctx):
    """Advance any field whose clock has run out, then show this session the
    field it stands in. Runs on an inbound frame, so it only moves while
    somebody is connected -- which is the only time a phase change can be
    seen anyway."""
    args = ctx.args
    if getattr(args, "campaign", "off") != "on":
        return
    now = time.time()
    due = []
    with _LOCK:
        for area, r in _STATE.items():
            if r["phase"] in (PEACE,) or not r["until"] or now < r["until"]:
                continue
            due.append(area)
    for area in due:
        _advance(ctx, args, area, now)
    # the obelisk drain runs BEFORE the keep check so a base emptied by
    # territory ends its war on the same pump a swing would have
    obelisk_drain(ctx, args, now)
    tower_fire(ctx, args, now)
    with _LOCK:
        warring = [a for a, r in _STATE.items() if r["phase"] == WAR]
    for area in warring:
        if keep_fallen(area):
            print("[fecampaign] area %d: the %s keep has fallen -- the war "
                  "ends now" % (area, keep_fallen(area)), flush=True)
            _advance(ctx, args, area, now)
    _votes_expire(ctx, args, now)
    _present_here(ctx, args, now)
    _trace(ctx, args, now)


def _here(args):
    """The WAR FIELD this session stands in, or None: not in a field, indoors,
    or a capital (a capital is a peace field and never a front)."""
    s = fw._SESSION
    if not s.get("in_field") or s.get("room", -1) != -1:
        return None
    area = s.get("field")
    if area is None or int(area) in fw.CAPITAL_GROUP_IDS:
        return None
    return int(area)


def _present_here(ctx, args, now):
    """KEY: THE PART THAT WAS MISSING (2026-09-10). Nothing ever DECLARED a war:
    `!campaign declare` was the only path, so with --campaign on and no
    operator typing, every field sat at peace, feworld's own pump stood down
    ("a campaign owns this field's phase") and the client counted its 0x1018
    down to 00:00:00 with nothing behind it.

    --campaign-auto (a TEST knob, default off; 2026-09-11 wars start with a
    Keep vote -- on_build_keep) declares for a player standing in a war field
    at peace, as the ATTACKER, and only where that player could have built a
    keep (can_declare): never in their own nation's land (it used to pick a
    hostile neighbour and start a war AGAINST the player's own nation), never
    in a beginner field, never against a nation already at war.
    And whenever the field or its phase differs from what THIS session last
    saw -- another session's transition, a persisted war entered mid-way --
    the session is shown the current phase."""
    area = _here(args)
    s = fw._SESSION
    if area is None:
        s.pop("campaign_seen", None)
        s.pop("campaign_start_due", None)
        s.pop("campaign_nation", None)    # re-read at the next field entry
        s.pop("keeps_none", None)
        if s.get("keeps_area") is not None:
            s.pop("keeps_area", None)
            s.pop("keeps", None)          # the client tore them down on exit
        s.pop("crystals_area", None)      # ...and the giant crystals with them
        s.pop("war_crystals", None)
        s.pop("built_seen", None)         # ...and the players' buildings
        _crystal_expire(ctx, args, None)  # crystals outlive no war
        return
    _nation(args)                         # cached for the voters' scan
    r = state_of(area)
    if r["phase"] == PEACE and getattr(args, "campaign_auto", None) == "on":
        mine = _nation(args)
        holder = fw.territory_owner(area, args)
        code, why = can_declare(args, area, mine)
        row = None if code else declare(args, area, mine, now)
        if row is not None:
            print("[fecampaign] area %d: at peace with a player in it -- war "
                  "DECLARED (--campaign-auto): attacker nation %d vs holder %d, "
                  "prep %.0f s" % (area, row["atk"], holder,
                                   _ms(args, "war_prep_ms") / 1000.0), flush=True)
            r = row
        elif s.get("campaign_auto_refused") != (area, code):
            s["campaign_auto_refused"] = (area, code)
            print("[fecampaign] area %d: --campaign-auto declares nothing: %s"
                  % (area, why or "no attacker"), flush=True)
    if s.get("campaign_peace_due") and _client_armed():
        s.pop("campaign_peace_due", None)
        fw.war_peace(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args)
        print("[fecampaign] area %d: PEACE, said now that the client has a "
              "war window to close" % area, flush=True)
    if (s.get("campaign_start_due") and _client_armed()):
        # the 0x1018 this session was waiting on has landed: now the advance
        s.pop("campaign_start_due", None)
        _start(ctx, args, area, r["until"])
    if s.get("campaign_seen") != (area, r["phase"]):
        drive_client(ctx, args, area, r["phase"], r["until"])
    # Keeps retry EVERY pump (not only on a phase change): the placement waits
    # for field_ready (building.pak mounted), which lands after the phase was
    # first presented. _keeps_present is idempotent -- keeps_area gates it once
    # the buildings are out.
    _keeps_present(ctx, args, area, r["phase"])
    _crystal_expire(ctx, args, area)


def _keeps_present(ctx, args, area, phase):
    """Show (PREP/WAR) or remove (TRUCE/PEACE) the war buildings of `area` on
    this session: the keeps, then the giant crystals (independent knobs)."""
    _keeps_only(ctx, args, area, phase)
    _crystals_present(ctx, args, area, phase)
    _buildings_present(ctx, args, area, phase)


def _keeps_only(ctx, args, area, phase):
    """Reconcile this session's BASES in `area` with the war, side by side:
    PREP/WAR -> the defender's castle and the attacker's keep; any other
    phase -> the castle ALONE, served for whoever holds the field.

    KEY: THE CASTLE IS PERMANENT (live report, 2026-09-12: "when you spawn in
    by default there's no castle"). The client's own book puts it on the
    PEACETIME radar -- fet_bookset_info 52/53 「●平和時のレーダーマップ …
    ◆青い建築物マーク　これは城じゃ」 -- skills are learned from masters in
    "the capital's castle or the FIELD's castle" (book 14), a keep is built
    「城から十分離れたところ」 (book 56: the castle is already there), and
    FEWiki 2006 calls it the field's entry point outside a war. Only the KEEP
    comes and goes with a war. Until today both were 0x1004'd at TRUCE/PEACE.
    `--castle-always off` restores that.

    A base that FELL this war stays down until the war is over (its row hp
    is 0), and the castle is re-served when the field changes hands (its
    nation byte is the holder's). Adds wait for field_ready; deletes do not."""
    if getattr(args, "keeps", "off") != "on" or not hasattr(fw, "keep_push"):
        return
    s = fw._SESSION
    area = int(area)
    r = state_of(area)
    holder = int(fw.territory_owner(area, args) or 0)
    want = {}                                   # side -> (nation, [hp, max])
    if phase in (PREP, WAR) and r.get("keeps"):
        want["def"] = (holder, list(r["keeps"].get("def") or [0, 0]))
        want["atk"] = (int(r["atk"]), list(r["keeps"].get("atk") or [0, 0]))
    elif str(getattr(args, "castle_always", "on") or "on") == "on" and holder:
        hp = max(1, min(32767, int(getattr(args, "keep_hp", 3000) or 3000)))
        want["def"] = (holder, [hp, hp])
    want = {k: v for k, v in want.items() if int(v[1][0]) > 0}
    reg = s.get("keeps") or {}
    for obj in [o for o, k in reg.items() if k.get("area") != area]:
        reg.pop(obj)                # another field's: the client dropped it
    shown = {k["side"]: int(k.get("nation") or 0) for k in reg.values()}
    gone = [side for side, nat in shown.items()
            if side not in want or nat != want[side][0]]
    if gone:
        fw.keep_del_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                         why="(area %d is %s%s)"
                         % (area, PHASE_NAME.get(phase, "?"),
                            ", new holder" if any(s_ in want for s_ in gone)
                            else ""), sides=gone)
    add = [side for side in want if side not in shown or side in gone]
    # KEY: WAIT FOR THE FIELD (2026-09-11). A type-1 building placed before
    # the client finishes mounting building.pak loads its models from an
    # unmounted pak -- NULL model -> crash at field exit (0x05071531).
    # feworld sets field_ready at the first clock sample, the same gate worn
    # items wait for; the pump runs every frame, so this simply retries until
    # the field is up. An area with no castle row is said ONCE (keeps_none).
    if add and s.get("field_ready") and s.get("keeps_none") != area:
        got = fw.keep_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                           area, {k: v[1] for k, v in want.items()},
                           {k: v[0] for k, v in want.items()}, sides=add)
        if not got:
            s["keeps_none"] = area
    if s.get("keeps"):
        s["keeps_area"] = area
    else:
        s.pop("keeps_area", None)


# ---------------------------------------------------------------------------
# the giant crystals -- where crystals come from
#
# KEY: CRASH-SAFE, STATICALLY (2026-09-11, disassembly of the dump at 0x04F90000).
# The building class sets up EVERY type through one function, 0x05070530
# (the type-1 arm 0x0503b583 calls it for any [+0x380]), so a type-7 Crystal
# takes the same path as a keep. It loads four model SLOTS through 0x05077d80
# -- slot 0 `Build\<model>.mdl`+`.tex` (+`_hit.mdl`), 1 `_m`, 2 `_s`, 3
# `_light` -- into [obj+0x84+slot*4], each only if the resource lookup
# 0x04fceb40 finds the file (a miss leaves the slot NULL and logs nothing).
# `_stand.mdl` is NOT a slot: it goes into an EMBEDDED KcMODEL at +0x3f0
# (constructed in the ctor at 0x050701a6, never a pointer) through SetupMdl
# 0x04fbc420, which logs "KcMODEL::SetupMdl : %s was not found" and returns
# 1; 0x0507087e then skips the stand texture. The ONLY unchecked model
# pointer in the class is slot 0 -- `mov eax,[esi+0x84]; mov cl,[eax+0x2bd]`
# at 0x05071531, the +0xE1531 crash -- every [+0x88..+0x90] read goes
# through the guarded getter 0x05077ec0 (0x05071f8c falls back to slot 0,
# 0x05071fcb tests slot 3). So the `_stand ... not found` lines in the
# 09-11 crash log were a SYMPTOM (building.pak not mounted yet -> slot 0 NULL
# too, silently), never the cause -- which is why castle00_a (no _stand)
# survived its first field live. building.pak ships `Build\crystal.mdl`
# (38544 B), `crystal.tex`, `crystal.anm`, `crystal_hit.mdl`: slot 0 loads,
# slots 1-3 stay NULL (guarded), the stand stays empty (embedded). With the
# field_ready gate (the race fix) model 6 is as safe as the keeps.
#
# KEY: THE CLIENT SHOWS DEPLETION ITSELF. The draw search (0x05066440, kind 7
# = [row+0] of the FE_BUILDING_DATA row) and the builder 0x05152ca0 both
# skip a crystal whose [+0x778] <= 0, and the update 0x05071fb3 has a branch
# for [row+0xf2] == 7 that greys the mesh (rgb 0x64) and stops its effects
# when [+0x778] <= 0. So +0x778 IS what is left in the deposit: we serve
# 700 (the Crystal row's shipped +0xb0 -- DEFAULT_WAR_CRYSTAL_HP; FFSKY's
# 2007 page said 532), take one per draw (and one per crystal heal),
# and push the new amount on the building channel (0x2024 bit 0x4, the same
# u16 NEW value the keeps use). Reach is 10.0 world units ([0x5261960]).
# ---------------------------------------------------------------------------
_SPAWN_POOL = {}


def _crystal_base(args):
    return int(getattr(args, "keep_base", 2900) or 2900) + 10


def _spawn_pool(hmap):
    """Grid cells of every SHIPPED monster spawn point on Hmap `hmap`
    (fet_npc_generator_pos, pooled over every area that fights on that
    terrain -- area 5 ships none but shares map10 with area 6). SE put a
    monster there, so it is ground a unit can stand on."""
    if hmap in _SPAWN_POOL:
        return _SPAWN_POOL[hmap]
    cells = set()
    try:
        for a, row in fw.fegamedata.areas().items():
            if int(row.get("hmap") or 0) != int(hmap) or row.get("capital"):
                continue
            for p in fw.fegamedata.spawn_points(a):
                cells.add((fw.world_to_grid(p[0]), fw.world_to_grid(p[2])))
    except Exception:                                  # noqa: BLE001
        cells = set()
    _SPAWN_POOL[hmap] = sorted(cells)
    return _SPAWN_POOL[hmap]


def parse_crystal_pos(specs):
    """--crystal-pos AREA:GX:GZ (repeatable) -> {area: [(gx, gz), ...]}."""
    out = {}
    for spec in specs or []:
        try:
            a, gx, gz = (int(v, 0) for v in str(spec).split(":")[:3])
        except ValueError:
            continue
        out.setdefault(a, []).append((max(0, min(255, gx)), max(0, min(255, gz))))
    return out


def crystal_grids(area, n, args=None):
    """`n` grid cells for the field's giant crystals.

    WARNING: NO SHIPPED TABLE PLACES THEM -- dat.pak has no crystal table (every
    FE_/fet_ class tag enumerated), the Hmap paks carry only terrain (.hmp),
    MapSpot*.cfg only ambient sounds, field_*.pak only scenery meshes; SE
    moved crystals on 4/14 by a server update, so they were server data.

    KEY: 2026-09-11 (second pass): the 2006 wikis say there is ALWAYS one
    BESIDE EACH BASE -- 「城横クリ」 (the crystal beside the castle), 「城また
    はキープ傍のクリスタル」 (Netz / fewiki map pages) -- and name others by
    grid square. So the three defaults are: the MIDDLE of the castle->keep
    line (the contested one; SE's own field page shows a central crystal on
    ブローデン古戦場跡), then one --crystal-base-gap cells (16 = 40 world
    units, half a minimap square: CHOSEN) out from the castle toward the
    keep, and the same from the keep. More than three add the quarter
    points; two are the two bases' only. The middle is SNAPPED to the
    unused shipped monster spawn point nearest equidistant from both bases
    (reachable ground); a base's deposit snaps only to a spawn point within
    --crystal-base-gap of its target (else it stays on the line -- the
    ground beside a base is ground the base stands on). None within 12
    cells of a base. --crystal-place line skips the snap; --crystal-pos
    AREA:GX:GZ replaces the lot for one area."""
    n = max(0, int(n))
    if args is not None:
        fixed = parse_crystal_pos(getattr(args, "crystal_pos", None)).get(int(area))
        if fixed:
            return list(fixed)
    g = fw.keep_grids(area) or {}
    if not g or not n:
        return []
    (dx, dz), (ax, az) = g["def"], g["atk"]
    span = max(1.0, ((ax - dx) ** 2 + (az - dz) ** 2) ** 0.5)
    gap = int(getattr(args, "crystal_base_gap", 16) if args is not None else 16)
    gap = max(12, gap)                       # never on top of a base
    near = min(0.45, gap / span)             # the base deposits' fraction
    # fr: fraction along castle->keep. `near` / `1 - near` = beside a base
    fr = [near, 1.0 - near] if n == 2 else [0.5, near, 1.0 - near]
    k = 2
    while len(fr) < n:
        for i in range(1, k, 2):
            f = i / (2.0 * k)
            fr += [f, 1.0 - f]
        k *= 2
    fr = fr[:n]
    beside = {i for i, f in enumerate(fr) if f in (near, 1.0 - near)}
    targets = [(dx + (ax - dx) * f, dz + (az - dz) * f) for f in fr]
    place = getattr(args, "crystal_place", "spawns") if args is not None else "spawns"
    pool = []
    if place == "spawns":
        row = fw.fegamedata.areas().get(int(area)) or {}
        pool = [c for c in _spawn_pool(row.get("hmap") or 0)
                if min((c[0] - bx) ** 2 + (c[1] - bz) ** 2
                       for bx, bz in ((dx, dz), (ax, az))) >= 12 ** 2]
    def d2(c, x, z):
        return (c[0] - x) ** 2 + (c[1] - z) ** 2
    out = [None] * n
    # the sides' deposits pick first (outermost fractions), the middle last:
    # the pool is ~10 points a map, and the middle taking the one point near
    # a base would leave that side's deposit across the field
    for i in sorted(range(n), key=lambda i: -abs(fr[i] - 0.5)):
        f, (tx, tz) = fr[i], targets[i]
        free = [c for c in pool if c not in out]
        # a deposit meant for one side's half stays on that half, so each side
        # has one nearer itself than the enemy
        half = [c for c in free
                if f == 0.5 or (d2(c, dx, dz) < d2(c, ax, az)) == (f < 0.5)]
        free = half or free
        if i in beside:
            # beside its base: a spawn point only if one is that close, else
            # the point on the line itself
            free = [c for c in free if d2(c, tx, tz) <= gap * gap]
        if free and f == 0.5:
            # the contested one: as near EQUIDISTANT from both bases as the
            # pool allows, then near the middle
            out[i] = min(free, key=lambda c: abs(d2(c, dx, dz) ** 0.5
                                                 - d2(c, ax, az) ** 0.5)
                         + d2(c, tx, tz) ** 0.5)
        elif free:
            out[i] = min(free, key=lambda c: d2(c, tx, tz))
        else:
            out[i] = (max(20, min(235, int(round(tx)))),
                      max(20, min(235, int(round(tz)))))
    return out


#: what one giant crystal holds. 2026-09-11 (second pass): SHIPPED --
#: FE_BUILDING_DATA row 7 'Crystal' +0xb0 = 700, the max-HP field (the same
#: field reads Arrow Tower 8,000 and War Craft 18,000, the wikis' HP), and a
#: crystal's HP IS its deposit (the client greys it at +0x778 <= 0). FFSKY's
#: 532 (2007) was the value before; the later FEZ wiki's 700 agrees with the
#: client.
DEFAULT_WAR_CRYSTAL_HP = 700


def _crystal_amount(args):
    return max(1, min(32767, int(getattr(args, "war_crystal_hp",
                                         DEFAULT_WAR_CRYSTAL_HP)
                                 or DEFAULT_WAR_CRYSTAL_HP)))


def deposits(args, area):
    """The giant crystals of `area`'s current war: [[left, amount], ...],
    created full the first time a war field is presented with --war-crystals
    > 0, persisted in the campaign row (so a re-entering player sees the
    depleted ones), emptied at Peace. [] when there is no war there."""
    n = int(getattr(args, "war_crystals", 0) or 0)
    with _LOCK:
        r = _STATE.get(int(area)) if area is not None else None
        if r is None or r["phase"] not in (PREP, WAR) or n <= 0:
            return []
        cur = r.setdefault("crystals", [])
        fresh = len(cur) < n
        while len(cur) < n:
            amt = _crystal_amount(args)
            cur.append([amt, amt])
        out = [list(c) for c in cur[:n]]
    if fresh:
        save(args)
    return out


def _crystals_present(ctx, args, area, phase):
    """Serve --war-crystals giant crystals (building TYPE 7 'Crystal', model
    --war-crystal-model 6 `crystal`: what the draw search 0x05066440 finds)
    while a war is prepared or fought; 0x1004 them after. Waits for
    field_ready like the keeps (the building.pak mount race). The record
    carries what is LEFT in each deposit, so a depleted one comes back grey."""
    s = fw._SESSION
    n = int(getattr(args, "war_crystals", 0) or 0)
    if n > 0 and phase in (PREP, WAR):
        if s.get("crystals_area") == int(area) or not s.get("field_ready"):
            return
        base = _crystal_base(args)
        model = int(getattr(args, "war_crystal_model", 6))
        left = deposits(args, area)
        reg = s.setdefault("war_crystals", {})
        for i, (gx, gz) in enumerate(crystal_grids(area, n, args)):
            obj = base + i
            cur, amt = left[i] if i < len(left) else (_crystal_amount(args),) * 2
            # age = finished; a Crystal is canonical group 7, which
            # 0x050727a2 exempts from the construction effect regardless
            body = fw.building_record(obj, 7, model, gx, gz, side=0,
                                      hp=(int(cur), int(amt)),
                                      stamp=fw.building_age(args, 7))
            fw.send(ctx.conn, ctx.outbound, fw.inner_msg(0x1006, body, obj),
                    ctx.mode, ctx.be, args.seq_mode == "echo",
                    getattr(args, "world_prefix", 4))
            reg[obj] = int(area)
            print("[fecampaign] -> 0x1006 type 1 GIANT CRYSTAL id=%d type 7 "
                  "model %d (%s) grid=(%d,%d) -> world (%g, ?, %g) holds %d/%d "
                  "(area %d)" % (obj, model, fw.BUILDING_MODELS.get(model, "?"),
                                 gx, gz, fw.grid_to_world(gx), fw.grid_to_world(gz),
                                 cur, amt, area), flush=True)
        s["crystals_area"] = int(area)
    elif s.get("crystals_area") is not None and phase not in (PREP, WAR):
        for obj in sorted(s.pop("war_crystals", None) or {}):
            fw.mob_kill_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, obj)
        s.pop("crystals_area", None)


def _deplete(args, area, idx, n):
    """Take `n` out of deposit `idx` of `area`'s war. Returns the amount left,
    or None when the deposit is not there (no war, no such index)."""
    with _LOCK:
        r = _STATE.get(int(area))
        if not r or r["phase"] not in (PREP, WAR):
            return None
        cur = r.get("crystals") or []
        if not 0 <= idx < len(cur):
            return None
        cur[idx][0] = max(0, int(cur[idx][0]) - int(n))
        left = cur[idx][0]
    save(args)
    return left


def on_crystal_relay(ctx, payload):
    """Another player drew from a giant crystal in this field: show what is
    left in it here too (0x2024 bit 0x4 on the building)."""
    s = fw._SESSION
    try:
        area, obj, left = int(payload["area"]), int(payload["obj"]), int(payload["left"])
    except (KeyError, TypeError, ValueError):
        return
    if s.get("field") != area or obj not in (s.get("war_crystals") or {}):
        return
    fw.building_hp_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args, obj, left)


def _crystal_war_of(key):
    """The area of the PREP/WAR row `key` fights in or has drawn from."""
    if key is None:
        return None
    with _LOCK:
        for a, r in _STATE.items():
            if r["phase"] in (PREP, WAR) and (key in (r.get("members") or {})
                                             or key in (r.get("drawn") or {})):
                return a
    return None


def _crystal_legacy(args):
    """ONE-TIME, per character: a character stored before crystals were
    earned carries the old `--crystal` seed (prod's 4321), which nobody drew.
    The first time a session with --crystal-reset war sees the character,
    its stored total is written 0 -- a real stored 0, so the --crystal seed
    can never apply to it afterwards (_seeded_value seeds only a MISSING
    value): a new character starts with 0 whatever the seed says -- and
    `crystal_epoch` 1 marks it done. Returns True when it took some away."""
    if getattr(args, "crystal_reset", "war") != "war":
        return False
    if fw._load_char_field(args, "crystal_epoch", None) is not None:
        return False
    have = fw._load_char_field(args, "crystal", None)
    try:
        had = int(have) if have is not None else 0
    except (TypeError, ValueError):
        had = 0
    if have is None or had != 0:
        fw._store_char_field(args, "crystal", 0)
    fw._store_char_field(args, "crystal_epoch", 1)
    if had > 0:
        print("[fecampaign]    crystals: %d carried from before crystals were "
              "earned (the old --crystal seed) -- reset to 0, once" % had,
              flush=True)
        return True
    return False


def _crystal_expire(ctx, args, area):
    """CRYSTAL COUNTS ONLY DURING WAR [SE economy page: CRYSTAL counts only
    during war]. A character who is no side of a live war -- not enlisted,
    never drew in one, not standing in one's field on a side -- carries 0.
    Checked when the session's (field, phase, war) changes, not every frame
    (the store read is a roster load)."""
    if getattr(args, "crystal_reset", "war") != "war" \
            or getattr(args, "crystal", None) is None:
        return
    s = fw._SESSION
    key = _me()[0]
    if key is None:
        return
    phase = state_of(area)["phase"] if area is not None else None
    war = _crystal_war_of(key)
    sig = (area, phase, war)
    prev = s.get("crystal_sig")
    if prev == sig:
        return
    s["crystal_sig"] = sig
    # a war this character was in just ended: settle() may already have
    # zeroed the STORE from another session's thread, but this client still
    # shows the old total -- re-serve it
    zeroed = _crystal_legacy(args) or bool(prev and prev[2] is not None
                                           and war is None)
    keep = war is not None or (area is not None and phase in (PREP, WAR)
                               and _my_side(args, area) is not None)
    have = fw._load_char_field(args, "crystal", None)
    try:
        have = int(have) if have is not None else 0
    except (TypeError, ValueError):
        have = 0
    if have > 0 and not keep:
        fw._store_char_field(args, "crystal", 0)
        zeroed = True
        print("[fecampaign]    crystals: %s is in no war now -- %d carried "
              "crystals go to 0 (they count only during war)" % (key, have),
              flush=True)
    if zeroed:
        fw.crystal_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args)


def on_draw_crystal(ctx, inner):
    """0x2081 [u32 crystal object] -- "draw crystal" (builder 0x05152ca0,
    registers no reply). The CLIENT drives the cadence: it sends one while
    crouched (flag 0x400) within 10.0 u of a type-7 building with [+0x778]
    > 0, carrying < 20 ([unit+0x8ac] < 0x14 at 0x05152cab), field status 2
    (at war), not a ghost, 5 s after the crouch (0x05152cf6), then every
    5 s -- 10 s once it carries MORE THAN 10 (`cmp [ecx+0x8ac],0xa; jbe` at
    0x05152d66 adds a second 5000 ms). Re-read 2026-09-11 against the RoD
    wiki (fewiki WAR/クリスタル: 5 s each up to 12 held, then 10 s, stop at
    20): the stop at 20 agrees; the slow-down threshold is 10 in this build
    against the wiki's 12 -- the client's timer is what runs, and THIS
    SERVER DOES NOT PACE DRAWS AT ALL (it credits each 0x2081), so nothing
    here contradicts either. Each draw credits --crystal-draw 1. Capped at
    --crystal-war-max per character PER WAR (20, SE
    warsystem01: 巨大クリスタルから取得できるクリスタルは1キャラクターあたり
    最大20) and --crystal-carry-max carried (50, same sentence: トレードなどで
    最大50まで), and by what the deposit has left (--crystal-deplete on: the
    deposit gives what it holds and no more -- 採取で巨大クリスタルのHPが減る,
    SE warsystem01). Stored and re-served as the 0x2035 total."""
    args = ctx.args
    f = inner[2:]
    obj = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else None
    if getattr(args, "campaign", "off") != "on":
        print("[fecampaign]    0x2081 draw crystal obj=%s -- --campaign off, "
              "logged only" % obj, flush=True)
        return
    area = _here(args)
    r = state_of(area) if area is not None else None
    if r is None or r["phase"] != WAR:
        print("[fecampaign]    0x2081 draw crystal obj=%s: not at war here -- "
              "nothing drawn" % obj, flush=True)
        return
    left = deposits(args, area)
    idx = (int(obj) - _crystal_base(args)) if obj is not None else -1
    if not 0 <= idx < len(left):
        print("[fecampaign]    0x2081 draw crystal obj=%s: not one of this "
              "war's %d giant crystal(s) -- nothing drawn" % (obj, len(left)),
              flush=True)
        return
    deplete = getattr(args, "crystal_deplete", "on") == "on"
    if deplete and left[idx][0] <= 0:
        print("[fecampaign]    0x2081: giant crystal %d is spent (0/%d) -- "
              "nothing drawn" % (obj, left[idx][1]), flush=True)
        return
    _crystal_legacy(args)
    have = fw._seeded_value(args, "crystal", getattr(args, "crystal", None))
    if have is None:
        print("[fecampaign]    0x2081: the CRYSTAL channel is off (--crystal "
              "unset) -- nothing to credit", flush=True)
        return
    key = _me()[0] or "?"
    step = max(1, int(getattr(args, "crystal_draw", 1) or 1))
    per_war = int(getattr(args, "crystal_war_max", 20) or 20)
    carry = int(getattr(args, "crystal_carry_max", 50) or 50)
    with _LOCK:
        row = _STATE.get(area)
        drawn = int((row.get("drawn") or {}).get(key, 0)) if row else 0
        gain = max(0, min(step, per_war - drawn, carry - int(have)))
        if deplete:
            gain = min(gain, max(0, int(left[idx][0])))
        if gain and row is not None:
            row.setdefault("drawn", {})[key] = drawn + gain
    if gain <= 0:
        print("[fecampaign]    0x2081: %s has drawn %d this war (max %d) and "
              "carries %d (max %d) -- nothing more" % (key, drawn, per_war,
                                                       have, carry), flush=True)
        return
    new = int(have) + gain
    fw._store_char_field(args, "crystal", new)
    fw.crystal_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, value=new)
    rest = left[idx][0]
    if deplete:
        rest = _deplete(args, area, idx, gain)
        if rest is not None:
            fw.building_hp_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                                int(obj), rest)
            fw.ext_post("campaign.crystal", {"area": int(area), "obj": int(obj),
                                             "left": int(rest)})
    save(args)
    print("[fecampaign]    0x2081 drew %d crystal from giant crystal %s (%s/%d "
          "left): %d -> %d (%d/%d this war)"
          % (gain, obj, rest, left[idx][1], have, new, drawn + gain, per_war),
          flush=True)


def heal_drain(ctx, args, obj, n=1):
    """The crystal HEAL's cost to the deposit (RoD and FEZ-early: each heal
    tick drains one crystal from the giant crystal -- changed only on
    2009-08-24). Takes `n` from THIS war's deposit `obj`, re-serves what is
    left to this client and the field -- the draw's own channel, so the two
    share ONE counter and cannot double-drain. Returns (handled, left):
    handled False = not a war deposit (the caller may use its own legacy
    path); left None = spent / no war (refuse the heal)."""
    s = fw._SESSION
    area = (s.get("war_crystals") or {}).get(int(obj))
    if area is None:
        return False, None
    idx = int(obj) - _crystal_base(args)
    cur = deposits(args, area)
    if not 0 <= idx < len(cur) or cur[idx][0] <= 0:
        return True, None
    if int(n) <= 0:
        return True, int(cur[idx][0])
    left = _deplete(args, area, idx, int(n))
    if left is None:
        return True, None
    fw.building_hp_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                        int(obj), left)
    fw.ext_post("campaign.crystal", {"area": int(area), "obj": int(obj),
                                     "left": int(left)})
    return True, left


# ---------------------------------------------------------------------------
# building rules for fewar's 0x2007 (fewar loads first, so it asks us)
# ---------------------------------------------------------------------------
def parse_caps(spec):
    """'TYPE=N,...' -> {type: N}; '2006' = BUILD_CAPS_2006 (shipped +0xe1)."""
    out = {}
    if str(spec or "").strip().lower() == "2006":
        spec = BUILD_CAPS_2006
    for part in str(spec or "").split(","):
        k, _, v = part.strip().partition("=")
        try:
            out[int(k, 0)] = int(v, 0)
        except ValueError:
            continue
    return out


#: FE_BUILDING_DATA rows named Obelisk -- the buildings that claim territory
OBELISK_TYPES = (6, 29)


#: an obelisk's territory radius in GRID CELLS. THE UNITS (feworld.
#: world_to_grid): a cell is 2.5 world units, the field 256 cells = 640 u,
#: its minimap 8x8 squares (A-H / 1-8) of 32 cells = 80 u. RoD's radius is
#: "about 1.2 squares" (fewiki / Hordaine 2006; FEZ later 1.25) = 96 u =
#: 38.4 cells -> 38 (95 u, 1.19 squares). Was 40 (1.25, the FEZ figure).
OBELISK_RADIUS_CELLS = 38


#: the obelisk drain, the THIRD way a base loses HP in 2006 and the one this
#: server never served [SE warsystem02, the Obelisk: 「支配領域の広さに
#: 比例して一定時間ごとに敵拠点へダメージを与える」 = "deals damage to the
#: enemy base every so often, in proportion to the breadth of the controlled
#: area"]. SE's page gives the RULE and no numbers, and no 2006 wiki wrote the
#: period or the coefficient down, so BOTH OF THESE ARE CHOSEN:
#:   * the tick is 30 s;
#:   * a side that held the WHOLE map would take 16 of the 480 dots per tick,
#:     i.e. it would end a war in 480/16 x 30 s = 15 minutes of total control.
#: Scale for an hour-long war (--war-length-ms 3600000): the 25-obelisk cap at
#: radius 38 covers a little over half a 256-cell field once overlaps are
#: counted, so a side that wins the obelisk war outright drains a full base in
#: roughly half an hour -- decisive, not instant, and it cannot by itself beat
#: a defender who only has to survive.
OBELISK_DRAIN_MS = 30000
OBELISK_DRAIN_DOTS = 16
#: grid stride for the coverage sample. 256/4 = 64x64 = 4,096 points per side
#: per tick, which is exact enough for a percentage and cheap enough to run on
#: an inbound frame.
_SHARE_STRIDE = 4


def territory_share(args, area, side):
    """0.0..1.0 -- the fraction of the field `side`'s OBELISKS cover.

    Obelisks only: the base's own radius is ours (territory_of marks it
    CHOSEN) and SE attributes 支配領域 to the obelisk alone. Overlapping
    circles count once, which is what "the breadth of the controlled area"
    means and is also why this samples a grid instead of summing πr²."""
    r = state_of(area)
    pts = (r.get("obelisks") or {}).get(side) or []
    if not pts:
        return 0.0
    rad = int(getattr(args, "obelisk_radius", OBELISK_RADIUS_CELLS) or 0)
    if rad <= 0:
        return 0.0
    rr = rad * rad
    step, hit, total = _SHARE_STRIDE, 0, 0
    for gx in range(0, 256, step):
        for gz in range(0, 256, step):
            total += 1
            for p in pts:
                dx, dz = gx - int(p[0]), gz - int(p[1])
                if dx * dx + dz * dz <= rr:
                    hit += 1
                    break
    return (hit / float(total)) if total else 0.0


# ---------------------------------------------------------------------------
# KEY: THE ARROW TOWER SHOOTS -- 2026-09-12
#
# SE warsystem02: 「アロータワー 監視塔。自国領土に設置することで、近づいた敵に
# 自動的に弓での攻撃を行います」 -- "a WATCH TOWER; placed in your own territory it
# automatically attacks approaching enemies with its bow." We placed the
# building and it did nothing at all.
#
# KEY: THE CLIENT SHIPS THE TOWER'S ATTACK. FE_BUILDING_DATA (parser 0x051906f8,
# the record re-read with feparse 2026-09-12) carries per building:
#     +0xa4  crystal cost      ArrowTower 18, Obelisk 15, WarCraft 20  <- the
#            2006 wikis' own numbers, so the table and the wikis agree
#     +0xac  build time in ms  ArrowTower 40000, Obelisk 30000, WarCraft 60000
#            <- Hordaine's 40 s / 30 s / ~60 s, agreeing again
#     +0xb0  max HP            ArrowTower 8000  <- fewiki's 8,000
#     +0xe1  cap per side      ArrowTower 10, Obelisk 25  <- fewiki's
#     +0xda  ATTACK SKILL      ArrowTower 958, DefenseTower/Castle/Keep 959
#     +0xd8  100 on exactly those four rows, 0 on every building with no skill
# and fet_skill NAMES both ids: 958 = 「監視塔の攻撃」 (the watch tower's attack --
# SE's page calls the Arrow Tower 監視塔), 959 = 「キャノン塔の攻撃」, with 960
# 「キャノン塔の弾爆発」 its shell burst. So the fire is a shipped mechanism and
# the skill id is not a guess.
#
# WARNING: WHAT IS A READING, NOT A MEASUREMENT: that +0xb4 is the tower's POWER (35
# on row 0, 100 on row 19 and the DefenseTower, 80 and 45 on the castles and
# keeps, 0 on every building with no attack skill -- but 1000 on the town
# shops, which have no attack skill either, so the field is not exclusively an
# attack), that +0xd0 (40.0f on the tower) is its RANGE in world units, and
# that +0xd4 (5.0f) is its interval in seconds. The interval has one
# independent check: the Crystal row's +0xd4 is 5.0 and the crystal DRAW timer
# is 5 s (measured 2026-09-11). All three are knobs.
#
# WARNING: AND THE CASTLE/KEEP CANNON IS OFF BY DEFAULT. Rows 15/16/17/18/20 carry
# skill 959, so retail's bases shot back -- but SE's page only ever claims the
# tower, a base that shoots changes every siege, and nothing here has been seen
# on a screen. --tower-fire covers types 0/19; --base-cannon is separate.
# ---------------------------------------------------------------------------
#: FE_BUILDING_DATA +0xda -- the building's own attack skill -- and +0xb4, read
#: as its power. Only the rows that ship a skill are here.
TOWER_ATTACK = {0: (958, 35), 19: (958, 100), 8: (959, 100),
                15: (959, 80), 16: (959, 80), 17: (959, 45), 18: (959, 45),
                20: (959, 45)}
#: the tower types --tower-fire covers (SE's アロータワー rows)
TOWER_TYPES = (0, 19)
#: the base rows whose 959 cannon --base-cannon covers
CANNON_TYPES = (8, 15, 16, 17, 18, 20)
#: +0xd0 read as range in world units, +0xd4 as the interval in seconds
TOWER_RANGE = 40.0
TOWER_INTERVAL = 5.0

_TOWER_CLOCK = {}
_TOWER_DRIVER = {}


def _under_construction(row):
    """A building still inside its shipped +0xac build time (feworld's
    BUILD_TIME_MS). The CLIENT already refuses to finish drawing one -- it
    stands as a construction site until the timer expires (0x05072770) -- so
    a tower that shoots out of a scaffold is the server disagreeing with the
    screen. Rows with no `t0` (written before 2026-09-12) count as finished.
    """
    age = building_age_ms(row)
    if age is None:
        return False
    return age < fw.build_time_ms(int(row.get("t", -1)))


def _tower_rows(args, area):
    """[(obj, row)] -- the buildings in `area` that shoot, per the knobs."""
    types = ()
    if str(getattr(args, "tower_fire", "on") or "off") == "on":
        types += TOWER_TYPES
    cannon = str(getattr(args, "base_cannon", "off") or "off") == "on"
    if cannon:
        types += CANNON_TYPES
    if not types:
        return []
    out = [(o, b) for o, b in buildings_of(area).items()
           if int(b.get("t", -1)) in types and b.get("side") in ("atk", "def")
           and not _under_construction(b)]
    if cannon:
        # the two keeps are not in `buildings` -- they are the war's own, and
        # keep_grids says where they stand
        for obj, k in (fw._SESSION.get("keeps") or {}).items():
            out.append((int(obj), {"t": 20 if k.get("side") == "def" else 16,
                                   "side": k.get("side"), "keep": True,
                                   "gx": None, "gz": None}))
    return out


def _tower_driver(area, now):
    """True on the ONE session that fires this area's towers. Whoever claims
    it keeps it while they keep pumping; a 5 s silence hands it over.

    Without this every player in the field would fire every tower on their own
    pump, and a tower would hit N times per interval for N players -- the same
    class of bug as a roster pushed once per session."""
    me = fw._SESSION.get("charid")
    if me is None:
        return False
    with _LOCK:
        d = _TOWER_DRIVER.get(int(area))
        if d and d[0] != int(me) and now - d[1] < 5.0:
            return False
        _TOWER_DRIVER[int(area)] = (int(me), now)
    return True


def _tower_pos(args, area, obj, b):
    """A tower's world position: its own grid, or the keep grids for a base."""
    if b.get("keep"):
        g = (fw.keep_grids(area) or {}).get(b.get("side"))
        if not g:
            return None
        gx, gz = g
    else:
        gx, gz = b.get("gx"), b.get("gz")
        if gx is None:
            return None
    y = 0.0
    try:                        # the map's median emitter height, the same
        y = float(fw.fegamedata.area_ground_height(area) or 0.0)
    except Exception:           # estimate derived_spawn already uses
        pass                                           # noqa: BLE001
    return (fw.grid_to_world(gx), y, fw.grid_to_world(gz))


def _peer_xyz(ps):
    """A session's last reported position, off its own 0x2023 heartbeat (the
    shape fepvp._peer_pos reads: three i16 tenths at +18)."""
    mv = ps.get("pres_mv")
    if not mv or len(mv) < 24:
        return None
    x, y, z = struct.unpack_from(">hhh", mv, 18)
    return (x * 0.1, y * 0.1, z * 0.1)


def _peer_nation(ps):
    """A session's nation. WARNING: NOT `ps["nation"]` -- that key does not exist
    (feworld puts the nation on the CHAT-ROOM entry, not on the session), and
    the first draft of _tower_target read it, so every peer came back nation 0
    and the same-side guard below never fired: a tower would have shot its own
    army. The session's nation is its presence card's `force`, which is what
    fepvp._nation_of_session has always read."""
    card = ps.get("pres_card") or {}
    try:
        return int(card.get("force") or 0)
    except (TypeError, ValueError):
        return 0


def _tower_target(args, area, side, pos, rng):
    """The nearest enemy player in range of `pos`, or (None, 0.0). An enemy is
    a session in this field whose nation is not the tower's side's.

    WARNING: RANGE IS MEASURED IN THE HORIZONTAL PLANE, and that is a fix, not a
    shortcut. The first draft compared in 3D -- but a tower's y is
    `area_ground_height`, the map's MEDIAN emitter height (57 in area 17),
    while the player's y is their real one. On any field whose ground differs
    from its median by more than the range, the y term alone exceeded 40 u and
    no tower could ever find a target. Measuring in XZ is also what "an enemy
    approaches the tower" means: nothing in FE fires at a different altitude.
    """
    mine = _side_byte(args, area, side)
    best, bestd = None, rng + 1.0
    for ent in fw.ext_sessions():
        ps = ent["session"]
        cid = int(ps.get("charid") or 0)
        if not cid or not ps.get("in_field") or ps.get("player_dead"):
            continue
        if int(ps.get("field") or -1) != int(area) or ps.get("room", -1) != -1:
            continue
        theirs = _peer_nation(ps)
        if mine and theirs and mine == theirs:
            continue                       # a tower never shoots its own side
        p = _peer_xyz(ps)
        if p is None:
            continue
        d = math.hypot(pos[0] - p[0], pos[2] - p[2])
        if d < bestd:
            best, bestd = {"cid": cid, "session": ps}, d
    return (best, bestd) if best else (None, 0.0)


def tower_fire(ctx, args, now):
    """Every tower standing in a war field shoots the nearest enemy player in
    range, on its own clock. Runs off the campaign pump.

    The damage is applied the way a player's hit is -- posted to the VICTIM's
    own thread (`pvp.hit`, fepvp.on_hit_relay owns their HP) -- so a tower
    kill takes the same death, penalty and base-dot path a PvP kill does and
    needs no new protocol. The hit is credited to the tower's BUILDER, which
    is the client's own rule: fet_bookset_info 61, on the Watch Tower --
    「倒した敵からのスコア（経験値）は建設者に入る」."""
    if str(getattr(args, "tower_fire", "on") or "off") != "on" \
            and str(getattr(args, "base_cannon", "off") or "off") != "on":
        return
    area = _here(args)
    if area is None or state_of(area).get("phase") != WAR:
        return
    rows = _tower_rows(args, area)
    if not rows:
        return
    rng = float(getattr(args, "tower_range", TOWER_RANGE) or 0.0)
    per = float(getattr(args, "tower_interval", TOWER_INTERVAL) or 0.0)
    if rng <= 0 or per <= 0 or not _tower_driver(area, now):
        return
    clocks = _TOWER_CLOCK.setdefault(int(area), {})
    for obj, b in rows:
        if now - float(clocks.get(obj, 0) or 0) < per:
            continue
        pos = _tower_pos(args, area, obj, b)
        if pos is None:
            continue
        victim, d = _tower_target(args, area, b.get("side"), pos, rng)
        if victim is None:
            continue
        clocks[obj] = now
        skill, power = TOWER_ATTACK.get(int(b.get("t", -1)), (958, 35))
        dmg = int(getattr(args, "tower_damage", 0) or 0) or power
        # KEY: book 61 on the Watch Tower: 「倒した敵からのスコア（経験値）は建設者
        # に入る」 -- the score and EXP from an enemy the tower kills go to its
        # BUILDER. So the hit carries the builder's charid, not 0, and the
        # victim's thread reports back to them exactly as for a hand-thrown
        # swing (fepvp.on_report_relay -> pc_damage).
        fw.ext_post("pvp.hit", {"from": int(b.get("by") or 0),
                                "to": int(victim["cid"]),
                                "dmg": dmg, "skill": int(skill),
                                "tower": int(obj)},
                    to=lambda s_, _n, ps=victim["session"]: s_ is ps)
        print("[fecampaign] area %d: the %s tower %d (type %d) shoots skill %d "
              "at charid %d, %.1f u away (range %.0f) for %d"
              % (area, b.get("side"), obj, b.get("t", -1), skill,
                 victim["cid"], d, rng, dmg), flush=True)


def obelisk_drain(ctx, args, now):
    """Every --obelisk-drain-ms, each side's obelisk territory takes dots off
    the ENEMY base. SE warsystem04 lists this as one of the three ways to
    reduce a base's HP, beside swinging at it and killing its soldiers; until
    2026-09-12 the obelisk claimed ground and did nothing with it.

    Runs off the campaign pump, so it only moves while somebody is connected
    -- the same rule the phase clock already runs under, and a war with
    nobody in it is not being fought."""
    if str(getattr(args, "obelisk_drain", "on") or "off") != "on":
        return
    per = _ms(args, "obelisk_drain_ms") / 1000.0
    if per <= 0:
        return
    full = int(getattr(args, "obelisk_drain_dots", OBELISK_DRAIN_DOTS) or 0)
    if full <= 0:
        return
    with _LOCK:
        warring = [a for a, r in _STATE.items() if r["phase"] == WAR]
    for area in warring:
        with _LOCK:
            r = _STATE.get(area)
            if not r or r["phase"] != WAR or not r.get("keeps"):
                continue
            last = float(r.get("ob_tick") or 0.0)
            if last and now - last < per:
                continue
            r["ob_tick"] = now
            first = not last
        if first:
            continue                    # the first pump only starts the clock
        for side in ("atk", "def"):
            share = territory_share(args, area, side)
            dots = int(share * full)
            if dots <= 0:
                continue
            foe = "def" if side == "atk" else "atk"
            print("[fecampaign] area %d: %s holds %.0f%% of the field by "
                  "obelisk -- %d/%d dots off the %s base (SE warsystem02)"
                  % (area, side, share * 100, dots, BASE_DOTS, foe), flush=True)
            base_hit(args, area, foe, "obelisk territory", conn=ctx.conn,
                     outbound=ctx.outbound, mode=ctx.mode, be=ctx.be, dots=dots)


def territory_of(args, area, side):
    """[(gx, gz, radius)] -- 勢力範囲, the circles `side` holds in `area`'s war.

    KEY: THE CLIENT'S OWN TUTORIAL BOOK DEFINES THIS (fet_bookset_info 58,
    read 2026-09-12): 「勢力範囲とはオベリスクや他の建物の周辺の領域のことじゃ。
    この範囲内でのみ自軍の建物が建設可能じゃ」 -- the sphere of influence is the
    area around obelisks AND OTHER BUILDINGS, and only inside it can your side
    build. So every building the side owns extends it, not just its obelisks:
    the arrow tower you put up is itself ground you can then build on. Until
    2026-09-12 this returned the base and the obelisks only.

    WARNING: 勢力範囲 is NOT 支配領域. The book keeps them apart and so do we: this is
    the BUILDABLE (and, per book 59, the attackable) range, while territory_share
    -- what drains the enemy base -- counts obelisks alone, which is what book
    290 ties the castle's HP loss to.

    Radii in GRID CELLS (2.5 world units): --obelisk-radius 38 = 95 u = 1.19
    minimap squares of 32 cells [RoD ~1.2, FEZ-early 1.25; the map is 8x8
    squares A:1-H:8 over 256 cells]; --base-radius 38 is CHOSEN (= an
    obelisk's; no source gives the base's or a tower's)."""
    ob = int(getattr(args, "obelisk_radius", OBELISK_RADIUS_CELLS) or 0)
    br = int(getattr(args, "base_radius", OBELISK_RADIUS_CELLS) or 0)
    g = fw.keep_grids(area) or {}
    out = [(g[side][0], g[side][1], br)] if g.get(side) else []
    if str(getattr(args, "influence", "buildings") or "") == "buildings":
        # every OTHER building of this side, at the base radius (no source
        # gives a per-building one)
        for o, b in buildings_of(area).items():
            if b.get("side") == side and int(b.get("t", -1)) not in OBELISK_TYPES:
                out.append((int(b["gx"]), int(b["gz"]), br))
    out += [(p[0], p[1], ob) for p in (state_of(area).get("obelisks") or {}).get(side, [])]
    return out


def _inside(circles, grid):
    return any((grid[0] - x) ** 2 + (grid[1] - z) ** 2 <= r * r
               for x, z, r in circles)


def build_check(args, area, btype, grid=None):
    """(None, side) when this session may build `btype` in `area` now, else
    (0x100A code, reason). Buildings go up only in a field preparing for or
    at war, only by a side of that war, and within --build-caps per side per
    war (2006, --build-caps 2006: Obelisk 25, Arrow Tower 10, War Craft 1,
    Gate of Hades 1 -- FE_BUILDING_DATA +0xe1 and the wikis agree; 'can't be
    rebuilt once destroyed' falls out of counting builds, not survivors).
    With `grid` and --build-territory on, only inside the side's own
    territory and outside the enemy's [SE warsystem02: オベリスクで獲得した
    自国領土にしか建てることができません; an obelisk can't go up in enemy-held
    land -- destroy theirs first] -> code 16 "Can't build here."."""
    if getattr(args, "campaign", "off") != "on" \
            or getattr(args, "build_phase", "war") == "any":
        return None, None
    if area is None:
        return 8, "not in a field"
    r = state_of(area)
    if r["phase"] not in (PREP, WAR):
        return 15, "area %s is at %s -- build only in war prep or war" % (
            area, PHASE_NAME.get(r["phase"], "?"))
    side = _my_side(args, area)
    if side is None:
        return 15, "this character is not a side of the war in area %s" % area
    cap = parse_caps(getattr(args, "build_caps", "2006")).get(int(btype))
    if cap is not None:
        n = int(((r.get("built") or {}).get(side) or {}).get(str(int(btype)), 0))
        if n >= cap:
            return 40, "the %s side has built %d of type %d (cap %d)" % (
                side, n, btype, cap)
    if grid is not None and getattr(args, "build_territory", "on") == "on":
        grid = (int(grid[0]), int(grid[1]))
        mine = territory_of(args, area, side)
        foe = territory_of(args, area, "def" if side == "atk" else "atk")
        if mine and not _inside(mine, grid):
            return 16, ("grid (%d,%d) is outside the %s side's territory (its "
                        "base and obelisks)" % (grid[0], grid[1], side))
        if _inside(foe, grid):
            return 16, ("grid (%d,%d) is inside the enemy's territory -- "
                        "destroy their obelisk first" % grid)
    return None, side


def build_count(args, area, side, btype, grid=None):
    """Record one building of `btype` put up by `side` in `area`'s war (an
    obelisk's `grid` extends that side's territory)."""
    with _LOCK:
        r = _STATE.get(int(area))
        if not r:
            return 0
        b = r.setdefault("built", {}).setdefault(side, {})
        b[str(int(btype))] = int(b.get(str(int(btype)), 0)) + 1
        n = b[str(int(btype))]
        if grid is not None and int(btype) in OBELISK_TYPES:
            r.setdefault("obelisks", {}).setdefault(side, []).append(
                [int(grid[0]), int(grid[1])])
    save(args)
    return n


# ---------------------------------------------------------------------------
# KEY: THE WAR'S BUILDINGS -- shared, 2026-09-12
#
# Until today a building a player put up existed in THAT PLAYER'S SESSION and
# nowhere else (`fewar`: `ctx.session["war_buildings"][obj]`, object ids from a
# per-session counter at --building-base). Three consequences, all real:
#   * nobody else in the field was ever sent the 0x1006, so an arrow tower was
#     drawn for its builder alone -- the same single-player world fepresence
#     fixed for avatars, still in force for buildings;
#   * two builders both allocated 3000, 3001, ... so their buildings collided
#     on object id and a hit on one addressed the other's;
#   * nothing could damage one: feworld.keep_hit only knows the two keeps, so
#     0x2019 on a tower fell through and the tower was indestructible.
#   ...while the obelisk TERRITORY those buildings claim was already shared,
#   so one player's invisible obelisk gated another player's placement and (as
#   of this morning) drained the enemy base.
#
# So the WAR owns them: the row carries every building with its own hp pair,
# ids come from the row's sequence, and each session reconciles what it has
# been served against what the row holds on every pump -- the shape
# _crystals_present already uses, but as a diff so a building somebody ELSE
# puts up arrives, and one somebody else fells goes away.
# ---------------------------------------------------------------------------
def building_add(args, area, side, btype, model, grid, hp, by=None):
    """Register one player-built building in `area`'s war. Returns its object
    id, or None when there is no war on. `hp` is (current, max)."""
    if area is None:
        return None
    base = int(getattr(args, "building_base", 3000) or 3000)
    with _LOCK:
        r = _STATE.get(int(area))
        if not r or r["phase"] not in (PREP, WAR):
            return None
        seq = int(r.get("build_seq", 0) or 0)
        r["build_seq"] = seq + 1
        obj = base + seq
        r.setdefault("buildings", {})[str(obj)] = {
            "t": int(btype), "m": int(model),
            "gx": int(grid[0]), "gz": int(grid[1]),
            "side": side if side in ("atk", "def") else None,
            # WHEN it was raised (epoch seconds). The type-1 record's last
            # u32 is the building's AGE in ms (feworld's CONSTRUCTION TIMER
            # block), so a player who arrives later must be told how old this
            # building is or the client raises it in front of them again.
            # Rows written before 2026-09-12 have no t0 and read as finished.
            "t0": time.time(),
            "hp": [int(hp[0]), int(hp[1])], "by": by}
    save(args)
    return obj


def building_age_ms(row):
    """How long a war building has stood, in ms, or None when the row predates
    `t0` (2026-09-12) -- None means FINISHED, which is the safe reading for a
    building that was already on the board when this field was added."""
    t0 = row.get("t0")
    if not t0:
        return None
    return max(0, int((time.time() - float(t0)) * 1000))


def buildings_of(area):
    """{obj: row} -- every player-built building standing in `area`'s war."""
    r = state_of(area)
    return {int(o): dict(b) for o, b in (r.get("buildings") or {}).items()}


def building_of(area, obj):
    return (buildings_of(area) or {}).get(int(obj))


def building_del(args, area, obj):
    """Take a building off the war's board. Returns its row, or None."""
    with _LOCK:
        r = _STATE.get(int(area)) if area is not None else None
        if not r:
            return None
        b = (r.get("buildings") or {}).pop(str(int(obj)), None)
    if b is not None:
        save(args)
    return dict(b) if b else None


def building_fell(args, area, obj, conn=None, outbound=None, mode=None,
                  be=None):
    """Everything that follows a building coming down, in one place: it leaves
    the war's board, an obelisk gives its territory back, and its OWN side's
    base pays the dots (SE's 戦況ゲージ -- an obelisk 4 of 480, an arrow tower
    2, a war craft 4). Returns the row that fell, or None.

    Both callers route here so a building felled by a swing
    (feworld.building_hit) and one the operator `!destroy`s
    (fewar.destroy_building) end identically -- they used to differ, because
    only the second one existed."""
    b = building_del(args, area, obj)
    if not b:
        return None
    btype = int(b.get("t", -1))
    if btype in OBELISK_TYPES:
        obelisk_gone(args, area, (b["gx"], b["gz"]))
    if b.get("side") in ("atk", "def"):
        base_hit(args, area, b["side"], btype, conn=conn, outbound=outbound,
                 mode=mode, be=be)
    return b


def in_influence(args, area, side, pos):
    """Is grid `pos` inside `side`'s 勢力範囲? (True when the gate is off.)"""
    if str(getattr(args, "influence", "buildings") or "off") == "off":
        return True
    for gx, gz, rad in territory_of(args, area, side):
        dx, dz = int(pos[0]) - int(gx), int(pos[1]) - int(gz)
        if dx * dx + dz * dz <= rad * rad:
            return True
    return False


def building_damage(args, area, obj, dmg, from_grid=None, by_side=None):
    """Take `dmg` off a player-built building, credited to this session's
    member row the way keep_damage credits a keep hit (the result screen's
    建築物与ダメージ counts both). Returns (new hp, row) or None.

    KEY: book 59: 「建設のみならず敵の建物への攻撃も勢力範囲内のみとなっておる」 --
    attacking an enemy building, like building one, only works inside YOUR OWN
    sphere of influence. Pass `from_grid` (the attacker's cell) and `by_side`
    to enforce it; without them the swing lands as it did before."""
    if from_grid is not None and by_side in ("atk", "def") \
            and not in_influence(args, area, by_side, from_grid):
        return "out of influence"
    key = _me()[0]
    with _LOCK:
        r = _STATE.get(int(area)) if area is not None else None
        if not r or r["phase"] != WAR:
            return None
        b = (r.get("buildings") or {}).get(str(int(obj)))
        if not b or int(b["hp"][1]) <= 0:
            return None            # no hp channel on it (--build-hp off)
        before = int(b["hp"][0])
        b["hp"][0] = max(0, before - max(0, int(dmg)))
        new = b["hp"][0]
        m = (r.get("members") or {}).get(key)
        if m is not None:
            m["dmg"] = int(m.get("dmg", 0) or 0) + (before - new)
        out = (new, dict(b))
    save(args)
    return out


def _buildings_present(ctx, args, area, phase):
    """Reconcile the buildings THIS session has been served against the ones
    the war holds: send a 0x1006 for anything new, 0x1004 anything gone.

    Runs on every pump, so a building another player puts up shows up within a
    frame or two, and one that falls disappears for everyone. Waits for
    field_ready like the keeps (the building.pak mount race)."""
    if getattr(args, "build_share", "on") != "on":
        return
    s = fw._SESSION
    seen = s.setdefault("built_seen", {})
    want = buildings_of(area) if phase in (PREP, WAR) else {}
    if want and not s.get("field_ready"):
        return
    for obj in sorted(set(seen) - set(want)):
        fw.mob_kill_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, obj)
        seen.pop(obj, None)
        print("[fecampaign] -> 0x1004 building %d is gone from area %s"
              % (obj, area), flush=True)
    for obj in sorted(set(want) - set(seen)):
        b = want[obj]
        # ITS REAL AGE, not 0 -- see building_add's t0. A tower that has
        # stood for ten minutes must not replay its 40-second construction
        # site at every player who walks in.
        body = fw.building_record(obj, b["t"], b["m"], b["gx"], b["gz"],
                                  side=_side_byte(args, area, b.get("side")),
                                  hp=(int(b["hp"][0]), int(b["hp"][1])),
                                  stamp=fw.building_age(args, b["t"],
                                                        building_age_ms(b)))
        fw.send(ctx.conn, ctx.outbound, fw.inner_msg(0x1006, body, obj),
                ctx.mode, ctx.be, args.seq_mode == "echo",
                getattr(args, "world_prefix", 4))
        seen[obj] = int(area)
        print("[fecampaign] -> 0x1006 type 1 BUILDING id=%d type %d (%s) model "
              "%d grid=(%d,%d) hp %d/%d side %s (area %s, built by %s)"
              % (obj, b["t"], fw.BUILDING_TYPES.get(b["t"], "?"), b["m"],
                 b["gx"], b["gz"], b["hp"][0], b["hp"][1], b.get("side"),
                 area, b.get("by")), flush=True)


def _side_byte(args, area, side):
    """The NATION a building record carries: SE warsystem02 draws the
    attackers' buildings red and the defenders' blue, and the record's side
    field is a nation id (fw.building_record)."""
    if side == "atk":
        return int(state_of(area).get("atk") or 0)
    if side == "def":
        return int(fw.territory_owner(area, args) or 0)
    return 0


def obelisk_gone(args, area, grid):
    """An obelisk at `grid` was destroyed: its territory goes with it."""
    with _LOCK:
        r = _STATE.get(int(area)) if area is not None else None
        if not r:
            return False
        hit = False
        for side, pts in (r.get("obelisks") or {}).items():
            for p in list(pts):
                if (p[0], p[1]) == (int(grid[0]), int(grid[1])):
                    pts.remove(p)
                    hit = True
                    break
            if hit:
                break
    if hit:
        save(args)
    return hit


def summon_count(area, side, form):
    """Summons of METAMORPHOSIS id `form` `side` has made in `area`'s war."""
    r = state_of(area)
    return int(((r.get("summoned") or {}).get(side) or {}).get(str(int(form)), 0))


def summon_add(args, area, side, form):
    """Record one summon (feunit's --summon-war-cap and the Chimera's
    one-per-bar unlock count these; a new war's row starts at none)."""
    with _LOCK:
        r = _STATE.get(int(area))
        if not r:
            return 0
        b = r.setdefault("summoned", {}).setdefault(side, {})
        b[str(int(form))] = int(b.get(str(int(form)), 0)) + 1
        n = b[str(int(form))]
    save(args)
    return n


# ---------------------------------------------------------------------------
# THE KEEP DECLARATION -- 0xF501 in, the vote, 0xF503/0xF504 out
# ---------------------------------------------------------------------------
def ready_state_body(party, last_time, btype, pos, bdir, builder, members, status):
    """0xF502 NotifyBuildToReadyState, 45 bytes, reader 0x0517c6c0."""
    m = (list(members) + [0] * 5)[:5]
    st = (list(status) + [0] * 5)[:5]
    return (struct.pack(">IHHHHHHI", party & 0xFFFFFFFF, last_time & 0xFFFF,
                        btype & 0xFFFF, pos[0] & 0xFFFF, pos[1] & 0xFFFF,
                        pos[2] & 0xFFFF, bdir & 0xFFFF, builder & 0xFFFFFFFF)
            + struct.pack(">5I", *[v & 0xFFFFFFFF for v in m])
            + bytes(v & 0xFF for v in st))


def ready_ok_body(party):
    """0xF503 NotifyBuildToReadyKeepOK [u32 party_id]."""
    return struct.pack(">I", party & 0xFFFFFFFF)


def ready_err_body(code, party=0, members=(), gold_short=0, crystal_short=0):
    """0xF504 NotifyBuildToReadyKeepError, 32 bytes, reader 0x0517cc00."""
    m = (list(members) + [0] * 5)[:5]
    return (struct.pack(">hI", int(code), party & 0xFFFFFFFF)
            + struct.pack(">5I", *[v & 0xFFFFFFFF for v in m])
            + struct.pack(">hI", int(gold_short), crystal_short & 0xFFFFFFFF))


def _accepts_needed(args):
    """--declare-accepts, the BUILDER INCLUDED; 0 and 1 both mean the builder
    alone (solo testing)."""
    try:
        return max(0, int(getattr(args, "declare_accepts", 4)))
    except (TypeError, ValueError):
        return 4


def _voters(args, area, nation, me_cid):
    """The builder, then every other live session of the builder's nation
    standing in `area` (not in a room), up to the dialog's five slots."""
    out = [me_cid]
    for ent in fw.ext_sessions():
        s = ent["session"]
        cid = s.get("charid")
        if cid is None or int(cid) in out:
            continue
        if not s.get("in_field") or s.get("field") != area \
                or s.get("room", -1) != -1:
            continue
        if int(s.get("campaign_nation") or 0) != int(nation):
            continue
        out.append(int(cid))
        if len(out) >= 5:
            break
    return out


def _vote_send(ctx, members, mid, body, why, area):
    """One F-message to every member: this session directly, the others on
    their own threads through the campaign.vote relay."""
    me = _me()[2]
    others = set(int(c) for c in members if c != me)
    if me in members:
        ctx.reply(mid, body, why=why)
    if others:
        fw.ext_post("campaign.vote", {"mid": mid, "body": body, "why": why,
                                      "area": area},
                    to=lambda s, _n: s.get("charid") is not None
                    and int(s["charid"]) in others, include_me=True)


def on_vote_relay(ctx, payload):
    ctx.reply(payload["mid"], payload["body"], why=payload.get("why", ""))


def _keep_err(ctx, code, why, party=0, crystal_short=0):
    ctx.reply(F_READY_ERR, ready_err_body(code, party, crystal_short=crystal_short),
              why="NotifyBuildToReadyKeepError code %d (%s) -- %s"
              % (code, KEEP_ERR.get(code, "?"), why))


def _vote_state(vote):
    left = max(0, int(vote["until"] - time.time()))
    return ready_state_body(vote["party"], left, vote["type"], vote["pos"],
                            vote["dir"], vote["builder"], vote["members"],
                            [vote["status"].get(c, VOTE_PENDING)
                             for c in vote["members"]])


def on_build_keep(ctx, inner):
    """0xF501 CommandBuildKeepForWar -- a player placed a KEEP: the war
    declaration (see the module docstring). Refuse with 0xF504, else open the
    vote, or declare at once when the builder alone is enough."""
    args = ctx.args
    f = inner[2:]
    if len(f) < 10:
        print("[fecampaign]    0xF501 CommandBuildKeepForWar: %d-byte body, "
              "expected 10: %s" % (len(f), f.hex()), flush=True)
        return True
    btype, gx, gy, gz, bdir = struct.unpack_from(">5H", f, 0)
    area = _here(args)
    print("[fecampaign] <- 0xF501 CommandBuildKeepForWar type=%d (%s) grid=(%d,"
          "%d,%d) dir=%d in area %s" % (btype, fw.BUILDING_TYPES.get(btype, "?"),
                                        gx, gy, gz, bdir, area), flush=True)
    if getattr(args, "campaign", "off") != "on":
        print("[fecampaign]    --campaign off: logged only", flush=True)
        return True
    key, acct, cid = _me()
    if area is None or cid is None:
        _keep_err(ctx, 11, "not standing in a war field")
        return True
    nation = _nation(args)
    code, why = can_declare(args, area, nation, grid=(gx, gz))
    if code is None and _war_of(key) is not None:
        code, why = 11, "already fighting in area %s" % _war_of(key)
    if code is not None:
        print("[fecampaign]    keep REFUSED: %s" % why, flush=True)
        _keep_err(ctx, code, why)
        return True
    need = _accepts_needed(args)
    voters = _voters(args, area, nation, cid)
    if len(voters) < need:
        _keep_err(ctx, 3, "%d of nation %d here, --declare-accepts %d"
                  % (len(voters), nation, need))
        return True
    with _LOCK:
        _VOTE_SEQ[0] += 1
        vote = {"area": area, "party": _VOTE_SEQ[0], "builder": cid,
                "atk": nation, "type": btype, "pos": (gx, gy, gz), "dir": bdir,
                "grid": (gx, gz), "members": voters, "need": need,
                "keys": {cid: (key, acct)}, "status": {cid: VOTE_ACCEPT},
                "until": time.time() + _ms(args, "declare_timeout_ms") / 1000.0}
        for ent in fw.ext_sessions():
            s = ent["session"]
            if s.get("charid") is not None and int(s["charid"]) in voters:
                c = int(s["charid"])
                vote["keys"].setdefault(c, ("%s|%d" % (s.get("account"), c),
                                            s.get("account")))
        _VOTES[area] = vote
    print("[fecampaign]    keep vote OPEN in area %d: party %d, nation %d, "
          "voters %s, %d accept(s) needed (builder counts), %d reject(s) "
          "cancel, %.0f s" % (area, vote["party"], nation, voters, need,
                              int(getattr(args, "declare_rejects", 3) or 3),
                              _ms(args, "declare_timeout_ms") / 1000.0),
          flush=True)
    _vote_tally(ctx, args, area)
    return True


def on_reply_keep(ctx, inner):
    """0xF500 ReplyToBuildKeep [u8 reply]: 1 = CANCEL (reject), 2 = OK."""
    args = ctx.args
    f = inner[2:]
    reply = f[0] if f else None
    cid = _me()[2]
    with _LOCK:
        area = next((a for a, v in _VOTES.items() if cid in v["members"]), None)
        if area is not None and reply in (VOTE_REJECT, VOTE_ACCEPT):
            _VOTES[area]["status"][cid] = reply
    print("[fecampaign] <- 0xF500 ReplyToBuildKeep reply=%s (%s) from charid %s "
          "-- %s" % (reply, {1: "CANCEL/reject", 2: "OK/accept"}.get(reply, "?"),
                     cid, "vote in area %s" % area if area is not None
                     else "no open vote names this character"), flush=True)
    if area is not None:
        _vote_tally(ctx, args, area)
    return True


def _vote_tally(ctx, args, area):
    """Pass, cancel, or re-show the vote in `area`."""
    with _LOCK:
        vote = _VOTES.get(area)
        if vote is None:
            return None
        st = [vote["status"].get(c, VOTE_PENDING) for c in vote["members"]]
    acc, rej = st.count(VOTE_ACCEPT), st.count(VOTE_REJECT)
    pend = st.count(VOTE_PENDING)
    rejects = max(1, int(getattr(args, "declare_rejects", 3) or 3))
    if acc >= vote["need"]:
        return _vote_pass(ctx, args, vote)
    if rej >= rejects or acc + pend < vote["need"]:
        return _vote_fail(ctx, args, vote, 4, "%d rejected (%d accepted, %d "
                          "undecided, %d needed)" % (rej, acc, pend, vote["need"]))
    _vote_send(ctx, vote["members"], F_READY_STATE, _vote_state(vote),
               "NotifyBuildToReadyState party %d: %d/%d accepted, %d rejected"
               % (vote["party"], acc, vote["need"], rej), area)
    return "open"


def _vote_fail(ctx, args, vote, code, why):
    with _LOCK:
        if _VOTES.get(vote["area"]) is not vote:
            return None
        _VOTES.pop(vote["area"], None)
    print("[fecampaign]    keep vote in area %d CANCELLED (code %d, %s): %s"
          % (vote["area"], code, KEEP_ERR.get(code), why), flush=True)
    _vote_send(ctx, vote["members"], F_READY_ERR,
               ready_err_body(code, vote["party"], vote["members"]),
               "NotifyBuildToReadyKeepError code %d (%s)" % (code, KEEP_ERR.get(code)),
               vote["area"])
    return "cancelled"


def _vote_pass(ctx, args, vote):
    with _LOCK:
        if _VOTES.get(vote["area"]) is not vote:
            return None
        _VOTES.pop(vote["area"], None)
    area = vote["area"]
    # the builder and everyone who accepted are the attacking army already
    members = {}
    for c in vote["members"]:
        if vote["status"].get(c) != VOTE_ACCEPT:
            continue
        key, acct = vote["keys"].get(c, ("?|%d" % c, None))
        members[key] = {"a": acct, "c": c, "n": vote["atk"], "side": "atk",
                        "dmg": 0, "t": time.time()}
    code, why = can_declare(args, area, vote["atk"])     # the world may have moved
    row = None if code is not None else declare(args, area, vote["atk"],
                                                grid=vote["grid"], members=members)
    if row is None:
        return _vote_fail(ctx, args, vote, code or 11, why or "declare refused")
    _vote_send(ctx, vote["members"], F_READY_OK, ready_ok_body(vote["party"]),
               "NotifyBuildToReadyKeepOK party %d -- the keep goes up" % vote["party"],
               area)
    print("[fecampaign] area %d: KEEP BUILT at grid (%d,%d) -- nation %d DECLARES "
          "WAR on nation %d; %d enlisted; War Prep %.0f s"
          % (area, vote["grid"][0], vote["grid"][1], vote["atk"],
             fw.territory_owner(area, args), len(members),
             _ms(args, "war_prep_ms") / 1000.0), flush=True)
    return "declared"


def _votes_expire(ctx, args, now):
    with _LOCK:
        due = [v for v in _VOTES.values() if now >= v["until"]]
    for v in due:
        _vote_fail(ctx, args, v, 5, "60 s passed without enough accepts")


def on_keep_relay(ctx, payload):
    """Another session hit a keep: show its new hp here too."""
    s = fw._SESSION
    try:
        area, obj, hp = int(payload["area"]), int(payload["obj"]), int(payload["hp"])
    except (KeyError, TypeError, ValueError):
        return
    if s.get("field") != area or obj not in (s.get("keeps") or {}):
        return
    fw.building_hp_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args, obj, hp)
    if hp <= 0:
        fw.mob_kill_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args, obj)
        s["keeps"].pop(obj, None)


def _client_armed():
    """Has this session's client received a 0x1018 (not one still held for
    the first clock sample)? 0x1015/0x1016 before it would talk to a window
    nothing armed."""
    s = fw._SESSION
    return bool(s.get("war_notify_t")) and not s.get("war_notify_deferred")


def _notify(ctx, args):
    """Send (or leave held) the 0x1018 that arms the client's war window. The
    deadline it carries is read from THIS module by feworld.war_notify
    (campaign_deadline_ms), so a held one picks the campaign's clock up when
    the first 0x2023 sample lands."""
    s = fw._SESSION
    if s.get("war_notify_deferred"):
        return
    if getattr(args, "war", None) not in ("offensive", "defensive"):
        return
    fw.war_notify(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args)


def _start(ctx, args, area, until):
    ms = max(0, int((until - time.time()) * 1000)) if until else 0
    dl, kind = fw.war_abs_deadline(args, ms)
    fw.war_start(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                 deadline=(dl, kind))
    _side_notify(ctx, args, area)


def _advance(ctx, args, area, now):
    r = state_of(area)
    if r["phase"] == PREP:
        with _LOCK:
            _STATE[area]["phase"] = WAR
            _STATE[area]["until"] = now + _ms(args, "war_length_ms", 600000) / 1000.0
            _STATE[area]["war_at"] = now
        save(args)
        print("[fecampaign] area %d: War Prep -> At war (attacker nation %d, "
              "signups atk %d / def %d)"
              % (area, r["atk"], r["signups"].get("atk", 0),
                 r["signups"].get("def", 0)), flush=True)
        drive_client(ctx, args, area, WAR, state_of(area)["until"])
    elif r["phase"] == WAR:
        winner, old, new = decide(args, area)
        settle(args, area, winner, old)
        with _LOCK:
            _STATE[area]["phase"] = TRUCE
            _STATE[area]["until"] = now + _ms(args, "war_truce_ms", 120000) / 1000.0
            _STATE[area]["signups"] = {}
        save(args)
        print("[fecampaign] area %d: At war -> Truce. %s won (rule %r); "
              "nation %s holds it%s"
              % (area, "ATTACKER" if winner == "atk" else "DEFENDER",
                 getattr(args, "war_decide", "signups"), new,
                 "" if new == old else " (was %s)" % old), flush=True)
        drive_client(ctx, args, area, TRUCE, state_of(area)["until"])
        _outcome_notify(ctx, args, area, winner, old, new)
    elif r["phase"] == TRUCE:
        with _LOCK:
            _STATE[area]["phase"] = PEACE
            _STATE[area]["until"] = 0.0
            _STATE[area]["atk"] = 0
            # the war is over: nobody is 'in' it any more (one war at a time
            # ends HERE), and the next one starts from a fresh keep
            for k in ("members", "drawn", "built", "obelisks", "summoned"):
                _STATE[area][k] = {}
            _STATE[area]["crystals"] = []   # the next war's deposits are full
            _STATE[area]["grid"] = None
            _STATE[area]["war_at"] = 0.0
            _STATE[area]["settled"] = False
        save(args)
        print("[fecampaign] area %d: Truce -> Peace" % area, flush=True)
        drive_client(ctx, args, area, PEACE, 0)


def _my_side(args, area):
    """"atk"/"def"/None for the player on this session: the side they
    ENLISTED on (a foreign volunteer's is on their member row), else their
    nation's."""
    r = state_of(area)
    m = (r.get("members") or {}).get(_me()[0])
    if m is not None:
        return m.get("side")
    mine = _nation(args)
    if not mine:
        return None
    if int(r["atk"]) == mine:
        return "atk"
    if fw.territory_owner(area, args) == mine:
        return "def"
    return None


#: `!campaign trace on` -- say, once per pump, what this session's field is
#: doing and which gate is holding the advance. Built 2026-09-11 after "the
#: declaration of war never actually completed": a phase that does not move
#: and a phase that moved but was never SENT look identical from a chair.
_TRACE = [False]


def _trace(ctx, args, now):
    if not _TRACE[0]:
        return
    s = fw._SESSION
    area = _here(args)
    if area is None:
        print("[fecampaign] TRACE: not in a war field (in_field=%s field=%s "
              "room=%s) -- no phase can advance for this session"
              % (s.get("in_field"), s.get("field"), s.get("room")), flush=True)
        return
    r = state_of(area)
    left = (r["until"] - now) if r["until"] else 0
    print("[fecampaign] TRACE area %d: phase %s, %.1fs left, seen %s, "
          "client armed %s (0x1018 at %s, deferred %s), start due %s, "
          "signups atk %d/def %d"
          % (area, PHASE_NAME.get(r["phase"], "?"), left,
             s.get("campaign_seen"), _client_armed(),
             s.get("war_notify_t"), s.get("war_notify_deferred"),
             s.get("campaign_start_due"),
             r["signups"].get("atk", 0), r["signups"].get("def", 0)))
    if r["phase"] != PEACE and r["until"] and left <= 0:
        print("[fecampaign] TRACE area %d: the clock is OUT and the phase has "
              "not moved -- the next pump should advance it; if this repeats, "
              "the advance itself is the fault, not the clock" % area,
              flush=True)


def drive_client(ctx, args, area, phase, until):
    """Show this field's phase to the player standing in it.

    KEY: ONE CLOCK, TWO HALVES. feworld already had a war pump that armed the
    client's countdown (0x1018), pushed the phase advance (0x1015) and re-armed
    itself -- a per-SESSION machine. This module is a per-AREA one. Run side by
    side they were two clocks with no relationship, and the war map could say
    "At war" while the client's own countdown still showed prep.

    So feworld's pump now stands down for any field a campaign owns
    (feworld.war_deadline_pump -> campaign_phase), and this is what drives it
    instead: the campaign's own `until` becomes the client's deadline, and the
    campaign's transition is what sends the advance. The client's clock and the
    war map cannot disagree because there is only one number.
    """
    s = fw._SESSION
    if s.get("field") != area:
        # KEY: THE ADVANCE HAPPENED, THE CLIENT WAS NOT TOLD. Whoever's pump
        # noticed the clock run out drives it, and they may be standing
        # somewhere else entirely -- the players IN the field then only catch
        # up on their own next pump (campaign_seen). Said out loud because
        # "the war never started" and "the war started and nobody was told"
        # are the same picture from a chair.
        print("[fecampaign] area %d -> %s, but the session that advanced it "
              "stands in %s -- the players in %d are told on their own next "
              "pump" % (area, PHASE_NAME.get(phase, "?"), s.get("field"), area),
              flush=True)
        return                                  # not the field they are in
    s["campaign_seen"] = (int(area), phase)
    ms = max(0, int((until - time.time()) * 1000)) if until else 0
    _keeps_present(ctx, args, area, phase)
    if phase == PREP:
        # the pre-war countdown: arm it and send feworld's own 0x1018 (or
        # leave the held one to carry our deadline when the clock lands)
        dl, kind = fw.war_abs_deadline(args, ms)
        fw.war_arm("prewar", dl, kind)
        print("[fecampaign] area %d: PREP -- the client's countdown is %d ms"
              % (area, ms), flush=True)
        _notify(ctx, args)
    elif phase == WAR:
        dl, kind = fw.war_abs_deadline(args, ms)
        fw.war_arm("war", dl, kind)
        if _client_armed():
            _start(ctx, args, area, until)
        else:
            # entered mid-war, or the 0x1018 is still held: arm the window
            # first, advance on the next pump once it has landed
            s["campaign_start_due"] = True
            print("[fecampaign] area %d: AT WAR, %d ms left -- arming the "
                  "window first, the advance follows its 0x1018" % (area, ms),
                  flush=True)
            _notify(ctx, args)
    elif phase == TRUCE:
        dl, kind = fw.war_abs_deadline(args, ms)
        if _client_armed():
            fw.war_truce(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, dl, kind)
        else:
            fw.war_arm("truce", dl, kind)
    else:
        if _client_armed():
            fw.war_peace(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args)
        else:
            # the same shape as campaign_start_due: the 0x1017 that closes the
            # war window is worth nothing before the client has a window to
            # close, and this branch runs ONCE per (area, phase) -- so without
            # the retry a peace that arrived early was never spoken again.
            s["campaign_peace_due"] = True
        s["war_phase"] = "peace"
        s.pop("war_deadline", None)


def _side_notify(ctx, args, area):
    if fw._SESSION.get("field") != area:
        return
    side = _my_side(args, area)
    if side is None:
        return
    fw.war_start_notify(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                        "offensive" if side == "atk" else "defensive")


def _outcome_notify(ctx, args, area, winner, old, new):
    """Push the land notify that matches THIS player's side."""
    if fw._SESSION.get("field") != area and not getattr(args, "campaign_notify_all", False):
        return
    mine = fw.nation_of(args)
    if not mine:
        return
    r = state_of(area)
    atk = int(r["atk"])
    if winner == "atk":
        kind, other = ("deprive", old) if mine == atk else \
                      ("deprived", atk) if mine == old else (None, 0)
    else:
        kind, other = ("keep", atk) if mine == old else \
                      ("keep_failed", old) if mine == atk else (None, 0)
    if kind is None:
        return
    fw.land_notify(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                   kind, mine, other, area)


# ---------------------------------------------------------------------------
# the two requests this module answers
# ---------------------------------------------------------------------------
def on_proclamation(ctx, inner):
    """0x2084 -> 0x1127 with the REAL counts for the polled area.

    `inner` is the WHOLE inner, id included (the seam's contract); the body
    starts at +2. WARNING: 2026-09-10: read from +0, which is the id bytes
    `20 84` -- area 0x20840000, never the polled one."""
    import struct
    args = ctx.args
    body = inner[2:]
    area = struct.unpack_from(">I", body, 0)[0] if len(body) >= 4 else 0
    if getattr(args, "campaign", "off") != "on":
        return False                        # let feworld's constant answer it
    a, d, am, dm = counts_of(area, args)
    # WARNING: 2026-09-10: this was ctx.send(), which Ctx does not have -- with
    # --campaign on both handlers raised AttributeError into the seam's
    # catch, and the heartbeat/army choice got NOTHING (fe_world_test's fake
    # ctx defines send(), so it never noticed). The seam's Ctx has reply().
    ctx.reply(0x1127, struct.pack(">HHHH", a & 0xFFFF, d & 0xFFFF,
                                 am & 0xFFFF, dm & 0xFFFF),
              why="MSG_GET_INFO_OF_PROCLAMATION_OF_WAR_OK area=%d %s "
                 "Attack[%d/%d] Defense[%d/%d]"
                 % (area, PHASE_NAME.get(phase_of(area), "?"), a, am, d, dm))
    return True


def on_decide_country(ctx, inner):
    """0x2018 -> 0x1020, and COUNT IT. The army choice is the sign-up: the
    client sends it when the battle starts, so a player who is in the field
    when a war begins is a player who joined it.

    Returns False while --campaign is off: the seam then falls through to
    feworld's own constant 0x1020 (a False return that was DROPPED until
    2026-09-10 -- see ext_dispatch). `inner` includes the 2-byte id."""
    import struct
    args = ctx.args
    if getattr(args, "campaign", "off") != "on":
        return False
    body = inner[2:]
    army = struct.unpack_from(">I", body, 0)[0] if len(body) >= 4 else 0
    area = _here(args)
    got, side = None, None
    if area is not None and phase_of(area) in (PREP, WAR):
        key, acct, cid = _me()
        other = _war_of(key, exclude=area)
        if other is not None:
            # one war at a time until it ends [SE warsystem01] -- 0x1021's
            # arm clears the in-flight byte, so the client is not left hanging
            ctx.reply(0x1021, struct.pack(">I", DC_NG_ALREADY),
                      why="MSG_DECIDE_COUNTRY_NG code %d (already in the war "
                          "in area %d)" % (DC_NG_ALREADY, other))
            return True
        side, code = join_side(args, area, army)
        if side is None:
            ctx.reply(0x1021, struct.pack(">I", code),
                      why="MSG_DECIDE_COUNTRY_NG code %d (army=%d)" % (code, army))
            return True
        got = sign_up(args, area, side, who={"key": key, "a": acct, "c": cid,
                                             "n": _nation(args),
                                             "cost": rank_cost(args,
                                                               _my_rank(args))})
        if got == "full":
            code = DC_NG_ATK_FULL if side == "atk" else DC_NG_DEF_FULL
            ctx.reply(0x1021, struct.pack(">I", code),
                      why="MSG_DECIDE_COUNTRY_NG code %d (%s side full)"
                          % (code, side))
            return True
    ctx.reply(0x1020, b"", why="MSG_DECIDE_COUNTRY_OK (army=%d)%s"
              % (army, "" if got is None else
                 " -- signed up %s, now %s" % (side, got)))
    return True


# ---------------------------------------------------------------------------
# operator verbs
# ---------------------------------------------------------------------------
def gm(ctx, line):
    # KEY: `!census N` -- the map's field-marker probe, live (2026-09-12).
    # The marker a field draws is a POPULATION TIER (mapselect.tex `Mark2`:
    # four silhouettes labelled 100~/50~/10~/~9 plus a crown), and a tester
    # looking at the continent map has field-ed OUT, so the honest count is 0
    # everywhere and "the client ignores chars_all" looks exactly like "the
    # client draws nothing for an empty field". This sets feworld's probe
    # WITHOUT a deploy: `!census 120` then reopen the map, `!census 0` after.
    # It lives here because fecampaign's gm() is already on the seam and
    # feworld has no live knob setter -- the shape `!pvp damage N` uses.
    if line.startswith("!census"):
        w = line.split()
        if len(w) < 2:
            print("[fecampaign] !census N -- report N players in EVERY field "
                  "(the map's marker is a population tier). Now: %s. "
                  "`!census 0` restores the real count."
                  % (getattr(ctx.args, "field_census_probe", 0) or "real"),
                  flush=True)
            return True
        try:
            n = max(0, int(w[1], 0))
        except ValueError:
            print("[fecampaign] !census N -- N must be a number", flush=True)
            return True
        ctx.args.field_census_probe = n
        print("[fecampaign] !census %d: every field now reports %s. REOPEN THE "
              "CONTINENT MAP. %s"
              % (n, "%d players" % n if n else "its real count",
                 "If a silhouette appears, chars_all IS the marker source."
                 if n else "Probe off."), flush=True)
        return True
    if not line.startswith("!campaign"):
        return False
    args = ctx.args
    rest = line[9:].strip().split()
    here = fw._SESSION.get("field")
    if not rest or rest[0] in ("show", "status"):
        with _LOCK:
            rows = sorted(_STATE.items())
        if not rows:
            print("[fecampaign] nothing under way. `!campaign declare [AREA "
                  "[NATION]]` starts one.", flush=True)
        for a, r in rows:
            left = max(0, r["until"] - time.time()) if r["until"] else 0
            print("[fecampaign] area %-3d %-10s atk nation %-2d  signups "
                  "atk %-3d def %-3d  %d enlisted  keep grid %s  %s"
                  % (a, PHASE_NAME.get(r["phase"], "?"), r["atk"],
                     r["signups"].get("atk", 0), r["signups"].get("def", 0),
                     len(r.get("members") or {}), r.get("grid"),
                     "%.0fs left" % left if left else "-"), flush=True)
        with _LOCK:
            votes = list(_VOTES.values())
            nats = sorted(_NATIONS.items())
        for v in votes:
            print("[fecampaign] keep vote area %d party %d nation %d: %s, %.0fs "
                  "left" % (v["area"], v["party"], v["atk"],
                            {c: v["status"].get(c, 0) for c in v["members"]},
                            max(0, v["until"] - time.time())), flush=True)
        for n, st in nats:
            print("[fecampaign] nation %d: %d wars, %d won" % (n, st["wars"], st["wins"]),
                  flush=True)
        return True
    verb = rest[0]
    try:
        area = int(rest[1], 0) if len(rest) > 1 and rest[1].lstrip("-").isdigit() else here
    except (ValueError, IndexError):
        area = here
    if verb == "declare":
        nat = None
        if len(rest) > 2 and rest[2] != "force":
            try:
                nat = int(rest[2], 0)
            except ValueError:
                nat = None
        if "force" not in rest[1:]:
            code, why = can_declare(args, area, nat or _nation(args))
            if code is not None:
                print("[fecampaign] !campaign declare: REFUSED (0xF504 code %d, "
                      "%s): %s -- add `force` to override" % (
                          code, KEEP_ERR.get(code), why), flush=True)
                return True
            nat = nat or _nation(args)
        row = declare(args, area, nat)
        if row is None:
            print("[fecampaign] !campaign declare: area %s cannot be attacked "
                  "-- not one of the 95, or no neighbouring nation to attack "
                  "it (a field cannot attack itself)." % area, flush=True)
        else:
            print("[fecampaign] area %s: War Prep for %.0fs, attacker nation %d"
                  % (area, _ms(args, "war_prep_ms") / 1000.0, row["atk"]),
                  flush=True)
            drive_client(ctx, args, int(area), PREP, row["until"])
        return True
    if verb in ("win", "resolve"):
        forced = rest[2] if len(rest) > 2 else (rest[1] if len(rest) > 1
                                               and rest[1] in ("atk", "def") else None)
        if area is None:
            print("[fecampaign] !campaign win: not in a field", flush=True)
            return True
        winner, old, new = decide(args, area, forced)
        settle(args, area, winner, old)
        with _LOCK:
            if area in _STATE:
                _STATE[area]["phase"] = TRUCE
                _STATE[area]["until"] = time.time() + _ms(args, "war_truce_ms", 120000) / 1000.0
                _STATE[area]["signups"] = {}
        save(args)
        print("[fecampaign] area %s resolved: %s won, nation %s holds it%s"
              % (area, winner, new, "" if new == old else " (was %s)" % old),
              flush=True)
        _outcome_notify(ctx, args, area, winner, old, new)
        return True
    if verb == "end":
        with _LOCK:
            _STATE.pop(int(area), None)
        save(args)
        print("[fecampaign] area %s: back to peace" % area, flush=True)
        return True
    if verb == "trace":
        want = rest[1].lower() if len(rest) > 1 else "on"
        _TRACE[0] = (want == "on")
        print("[fecampaign] !campaign trace %s -- every pump says this "
              "session's phase, its clock, whether the client's 0x1018 landed "
              "and whether an advance is waiting on it"
              % ("on" if _TRACE[0] else "off"), flush=True)
        return True
    if verb == "auto":
        # Live report, 2026-09-11: "the second I spawn into the world, I
        # get the declaration of war screen". That is this knob doing exactly
        # what it says -- declaring for a player standing in a peaceful field
        # -- and until now only a compose edit and a container recreate could
        # stop it. A war a player did not start is the most confusing thing
        # this module can do, so it has to be switchable from the gmcmd file.
        want = rest[1].lower() if len(rest) > 1 else ""
        if want not in ("on", "off"):
            print("[fecampaign] !campaign auto on|off (currently %s)"
                  % getattr(args, "campaign_auto", "off"), flush=True)
            return True
        prev = getattr(args, "campaign_auto", "off")
        args.campaign_auto = want
        print("[fecampaign] !campaign auto %s (was %s) -- server-wide, every "
              "session, until the next restart, which takes whatever the "
              "compose says. %s" % (want, prev,
                           "Standing in a peaceful field no longer declares "
                           "anything; a war now needs a Keep vote or "
                           "`!campaign declare`." if want == "off" else
                           "A player in an eligible peaceful field declares a "
                           "war again."), flush=True)
        if want == "off":
            fw._SESSION.pop("campaign_auto_refused", None)
        return True
    if verb == "join":
        side = rest[2] if len(rest) > 2 else (rest[1] if len(rest) > 1 else "")
        got = sign_up(args, area, side)
        print("[fecampaign] !campaign join %s: %s" % (side, got or "refused "
              "(no war in that area, or a bad side -- atk|def)"), flush=True)
        return True
    print("[fecampaign] !campaign show | declare [AREA [NATION]] [force] | join "
          "atk|def | win [atk|def] | end | auto on|off | trace on|off",
          flush=True)
    return True


def add_args(ap):
    ap.add_argument("--campaign", choices=("on", "off"), default="off",
                    help="run the WAR CYCLE: real sign-up counts in 0x1127, a "
                         "live status byte on the war map, phases on a clock, "
                         "and an outcome that moves territory and pushes the "
                         "land notifies. Off leaves the constants feworld "
                         "served before.")
    ap.add_argument("--campaign-auto", choices=("on", "off"), default="off",
                    help="TEST KNOB. Declare a war for a player standing in a "
                         "peaceful ENEMY war field they could have built a "
                         "keep in (the same refusals as the keep vote). Off "
                         "(default, the 2006 rule): a war starts only when a "
                         "player builds a Keep and --declare-accepts of their "
                         "nation there agree, or `!campaign declare`.")
    ap.add_argument("--war-cap", type=int, default=50, metavar="N",
                    help="the per-side sign-up cap the client draws as "
                         "Attack [Count/Max]: 50 v 50 [SE warsystem]. Nothing "
                         "ships a per-field one.")
    ap.add_argument("--declare-accepts", type=int, default=4, metavar="N",
                    help="accepts that pass a keep vote, THE BUILDER COUNTED "
                         "(SE: 4人以上が承諾; FFSKY reads 4 besides the builder). "
                         "0 or 1 = the builder alone declares (solo testing).")
    ap.add_argument("--declare-rejects", type=int, default=3, metavar="N",
                    help="rejections that cancel a keep vote (SE: 3人が却下)")
    ap.add_argument("--declare-timeout-ms", type=int, default=60000, metavar="MS",
                    help="a keep vote with too few accepts by then is "
                         "cancelled, 0xF504 code 5 (SE: 60秒)")
    ap.add_argument("--beginner-fields", choices=("on", "off"), default="on",
                    help="refuse a declaration on a field touching a capital "
                         "(SE 2nd beta: never conquerable)")
    ap.add_argument("--keep-min-cells", type=int, default=30, metavar="N",
                    help="a keep closer than this many grid cells to the "
                         "field's castle is refused (0xF504 code 6). The rule "
                         "is the book's (砦から一定の距離をとり); the number is "
                         "CHOSEN. 0 = no check.")
    ap.add_argument("--foreign-join", choices=("smaller", "off"),
                    default="smaller",
                    help="a player whose nation is neither side joins only the "
                         "side with fewer players (SE 9/8 rule); off = refused")
    ap.add_argument("--war-rewards", choices=("rod", "ours"),
                    default=DEFAULT_WAR_REWARDS,
                    help="how a finished war pays. rod (default) = the 2006 "
                         "formula (fewiki WAR/報酬 2006-05-22): Rings = rank "
                         "for everyone, winners +0-3 for time present and "
                         "S+3/A+2/B+1 per category; war EXP = base(Lv) x "
                         "participation x enemy-base-destroyed%% + base x "
                         "the four grades' bonus (S25/A20/B15/C10/D5/E0%%), at "
                         "most +1 level, win or lose. PC damage and kills "
                         "grade E (no PvP here). ours = the first pass's "
                         "invented winner bonus (--war-ring-win/-dmg), no EXP")
    ap.add_argument("--war-ring-win", type=int, default=2, metavar="N",
                    help="--war-rewards ours only: winners' flat Ring bonus "
                         "(CHOSEN, superseded by the 2006 formula)")
    ap.add_argument("--war-ring-dmg", type=int, default=1000, metavar="N",
                    help="--war-rewards ours only: one more Ring per N keep "
                         "damage (CHOSEN, superseded)")
    ap.add_argument("--base-dots", default="2006", metavar="2006|off|SPEC",
                    help="what drains a side's BASE besides swings, in dots "
                         "of the 480-dot gauge x --keep-hp: 2006 = an obelisk "
                         "destroyed 4, an arrow tower 2, a war craft 4 "
                         "(fewiki/Hordaine/FFSKY), a war death 1 (FFSKY; RoD "
                         "scaled it by level, amount unknown); Gate of Hades "
                         "unknown -> 0. SPEC = 'TYPE=N,...,death=N'; off")
    ap.add_argument("--king-message", action="append", default=[],
                    metavar="N:TEXT",
                    help="the King's message in nation N's 0x3027 record "
                         "(repeatable). `!force kingmsg` wins over it.")
    ap.add_argument("--force-population", choices=("on", "off"), default="on",
                    help="fill the nation record's member count from the "
                         "character store (cached 60 s)")
    ap.add_argument("--war-crystals", type=int, default=3, metavar="N",
                    help="giant crystals (building type 7, model "
                         "--war-crystal-model) served in a war field during "
                         "prep/war: one BESIDE EACH BASE (the 2006 wikis: "
                         "there is always one, 「城横クリ」) and one in the "
                         "middle of the castle-keep line (snapped to a "
                         "shipped monster spawn point). No table ships the "
                         "count or places. 0 = none. Crash-safety: model 6's "
                         "base mesh ships, and a missing _stand is not "
                         "fatal (static, 0x05070530)")
    ap.add_argument("--crystal-base-gap", type=int, default=16, metavar="CELLS",
                    help="how far from its base the base-side giant crystal "
                         "stands, in grid cells of 2.5 world units (16 = 40 "
                         "u, half a minimap square: CHOSEN; min 12)")
    ap.add_argument("--war-crystal-model", type=int, default=6, metavar="M",
                    help="building model for the giant crystals (6 = crystal)")
    ap.add_argument("--war-crystal-hp", type=int,
                    default=DEFAULT_WAR_CRYSTAL_HP, metavar="N",
                    help="crystals one giant crystal holds -- its +0x778. "
                         "700 = SHIPPED (FE_BUILDING_DATA 'Crystal' +0xb0, the "
                         "max-HP field; the later FEZ wiki agrees); FFSKY's "
                         "2007 page said 532. Drawing and healing drain it "
                         "and the client greys a spent one")
    ap.add_argument("--crystal-pos", action="append", default=[],
                    metavar="AREA:GX:GZ",
                    help="a giant crystal at grid (GX,GZ) in AREA "
                         "(repeatable); any for an area REPLACE the derived "
                         "places there")
    ap.add_argument("--crystal-place", choices=("spawns", "line"),
                    default="spawns",
                    help="derived places: snapped to the nearest shipped "
                         "monster spawn point (reachable ground) or left on "
                         "the castle-keep line")
    ap.add_argument("--crystal-deplete", choices=("on", "off"), default="on",
                    help="a draw takes what it credits out of the deposit "
                         "and re-serves what is left (SE: taking crystals "
                         "drains the giant crystal)")
    ap.add_argument("--crystal-reset", choices=("war", "off"), default="war",
                    help="war = crystals count only during a war (SE): a "
                         "character in no live war carries 0, the war's "
                         "fighters and drawers go to 0 when it ends, and a "
                         "character stored before this (the old --crystal "
                         "seed) is zeroed once; off = the stored total "
                         "persists and --crystal seeds new characters")
    ap.add_argument("--build-territory", choices=("on", "off"), default="on",
                    help="0x2007 builds only inside your side's territory "
                         "(its base + its obelisks) and outside the enemy's "
                         "(SE warsystem02) -> NG 16 'Can't build here.'")
    ap.add_argument("--obelisk-radius", type=int, default=OBELISK_RADIUS_CELLS,
                    metavar="CELLS",
                    help="an obelisk's territory radius in GRID CELLS (2.5 "
                         "world units each; a minimap square is 32 cells = "
                         "80 u, the 640-u field is 8x8 squares). 38 = 95 u = "
                         "1.19 squares: RoD's ~1.2 (FEZ later 1.25 = 40)")
    ap.add_argument("--influence", default="buildings",
                    choices=("buildings", "obelisks", "off"),
                    help="勢力範囲, the side's sphere of influence: what "
                         "counts toward it and whether it gates a swing at an "
                         "enemy building. The client's own tutorial book "
                         "(fet_bookset_info 58/59) says it is the area around "
                         "「オベリスクや他の建物」 -- obelisks AND OTHER BUILDINGS "
                         "-- and that attacking an enemy building, not only "
                         "building one, works only inside it. 'buildings' = "
                         "the book's rule; 'obelisks' = the base and obelisks "
                         "only (the pre-2026-09-12 reading), still gating; "
                         "'off' = no gate on attacking at all")
    ap.add_argument("--rank-cost", default="diff",
                    metavar="off|diff|rank|RANK=N,...",
                    help="SE's own war limiter, 参戦コスト [warsystem01]. "
                         "'diff' (default, SOURCED 2026-09-13): a character "
                         "costs its ★ and a join is refused (as 'side full') "
                         "when it would put its side more than --rank-gap ★ "
                         "ahead of the other -- fewiki WAR/戦争システム "
                         "(2006-06-14) and SE's 2006-05-09 patch table as "
                         "copied by the Holdein wiki (★14 -> ★18). 'rank' / "
                         "a table = the older CHOSEN fixed per-side budget "
                         "(--rank-budget); 'off' = no limit")
    ap.add_argument("--rank-gap", type=int, default=18, metavar="STARS",
                    help="how many ★ one side may stand AHEAD of the other "
                         "under --rank-cost diff: 18 after SE's 2006-05-09 "
                         "patch, 14 at beta 3 / launch")
    ap.add_argument("--rank-budget", type=int, default=0, metavar="N",
                    help="a side's total entry cost under --rank-cost rank or "
                         "a table (not diff). 0 = 2 x --war-cap (CHOSEN)")
    ap.add_argument("--tower-fire", default="on", choices=("on", "off"),
                    help="an ARROW TOWER shoots the nearest enemy player in "
                         "range on its own clock [SE warsystem02: the tower is "
                         "a watch tower that attacks approaching enemies with "
                         "its bow]. The attack SKILL is shipped -- "
                         "FE_BUILDING_DATA +0xda is 958 and fet_skill 958 is "
                         "the watch tower's attack -- the power, range and "
                         "interval are READINGS of +0xb4/+0xd0/+0xd4")
    ap.add_argument("--base-cannon", default="off", choices=("on", "off"),
                    help="castles, keeps and defence towers fire their own "
                         "shipped skill 959 too (the cannon tower's attack). "
                         "OFF: retail's rows carry it, but SE's page only "
                         "claims the arrow tower, and a base that shoots back "
                         "changes every siege")
    ap.add_argument("--tower-damage", type=int, default=0, metavar="N",
                    help="damage one tower shot does. 0 (default) = the "
                         "building row's own +0xb4 (arrow tower 35, the later "
                         "row 100, a castle 80/45) -- READ as the power")
    ap.add_argument("--tower-range", type=float, default=TOWER_RANGE,
                    metavar="U",
                    help="how far a tower shoots, world units (+0xd0 = 40.0 "
                         "on the arrow tower, READ as range)")
    ap.add_argument("--tower-interval", type=float, default=TOWER_INTERVAL,
                    metavar="S",
                    help="seconds between a tower's shots (+0xd4 = 5.0, READ "
                         "as the interval -- the Crystal row's 5.0 is its "
                         "known 5 s draw timer, which is the one check)")
    ap.add_argument("--build-share", default="on", choices=("on", "off"),
                    help="a player-built building is served to EVERY session "
                         "in the field, not just its builder's, and anybody's "
                         "swing can fell it. Before 2026-09-12 a building "
                         "lived in the builder's session alone: nobody else "
                         "saw it, nothing could damage it, and two builders "
                         "allocated the same object ids. off = that behaviour")
    ap.add_argument("--obelisk-drain", default="on", choices=("on", "off"),
                    help="an obelisk's territory DAMAGES the enemy base every "
                         "--obelisk-drain-ms [SE warsystem02: 支配領域の広さ"
                         "に比例して一定時間ごとに敵拠点へダメージ]. This is the "
                         "third of warsystem04's three ways to empty a base, "
                         "beside hitting it and killing its soldiers; before "
                         "2026-09-12 an obelisk claimed ground and nothing "
                         "came of it. off = the old behaviour")
    ap.add_argument("--obelisk-drain-ms", type=int, default=OBELISK_DRAIN_MS,
                    metavar="MS",
                    help="how often the drain ticks. CHOSEN -- SE gives the "
                         "rule and no period, and no 2006 wiki wrote one down")
    ap.add_argument("--obelisk-drain-dots", type=int,
                    default=OBELISK_DRAIN_DOTS, metavar="N",
                    help="dots of the 480-dot base gauge a side takes per tick "
                         "when it holds the WHOLE field; the real charge is "
                         "that x its coverage (territory_share, obelisk "
                         "circles only, overlaps counted once). CHOSEN: 16 = "
                         "total control kills a base in 15 min, so the "
                         "25-obelisk cap decides an hour-long war in about "
                         "half of it. 0 = off")
    ap.add_argument("--base-radius", type=int, default=OBELISK_RADIUS_CELLS,
                    metavar="CELLS",
                    help="the castle's / keep's own build radius (CHOSEN "
                         "= an obelisk's; no source gives it)")
    ap.add_argument("--crystal-draw", type=int, default=1, metavar="N",
                    help="crystals credited per 0x2081 draw. The CLIENT times "
                         "the draws (0x05152ca0: 5 s, then 10 s above 10 "
                         "carried, none at 20); the RoD wiki says 1 per 5 s, "
                         "10 s above 12. The server does not pace them")
    ap.add_argument("--crystal-heal-deplete", type=int, default=1, metavar="N",
                    help="crystals each crystal HEAL (0x20AD, feunit "
                         "--crystal-heal 50 HP per 5 s) takes out of a war's "
                         "giant crystal -- RoD/FEZ-early: 1 per heal tick "
                         "(changed 2009-08-24). Same counter the draws use; "
                         "a spent deposit heals nobody. 0 = heals are free")
    ap.add_argument("--crystal-war-max", type=int, default=20, metavar="N",
                    help="crystals one character may draw per war (SE: 20)")
    ap.add_argument("--crystal-carry-max", type=int, default=50, metavar="N",
                    help="crystals one character may carry (SE: 50)")
    # KEY: THE WAR AND TRUCE LENGTHS ARE FEWORLD'S OWN KNOBS, reused rather
    # than duplicated. --war-cycle already had --war-length-ms and
    # --war-truce-ms with exactly these meanings, prod already sets them, and
    # re-declaring one is an argparse conflict that kills the process at
    # startup -- which is how this was found. Only the PREP window is new.
    ap.add_argument("--war-prep-ms", type=int, default=65000, metavar="MS",
                    help="how long a declared field sits in War Prep before "
                         "the war starts. 65000 = RoD's preparation, 1 min "
                         "5 s (fewiki WAR/戦争システム 2006-06: a 1-min "
                         "declaration phase -- joining by the sword icon -- "
                         "then 1:05 of preparation, non-joiners expelled; "
                         "this server has one PREP phase). The war and truce "
                         "lengths come from --war-length-ms and "
                         "--war-truce-ms.")
    ap.add_argument("--war-decide", default="signups",
                    choices=("signups", "attacker", "defender", "manual", "keeps"),
                    help="WARNING: WHO WINS -- and this is OURS, not FE's. Retail "
                         "decided a war inside the battle (keeps, the crystal) "
                         "and we serve none of that. 'signups' gives it to "
                         "whichever side put more players in, attacker losing "
                         "ties; 'manual' resolves nothing until `!campaign "
                         "win`; 'keeps' = a fallen keep loses, else the side "
                         "whose keep kept more hp, defender holding ties "
                         "(--keeps on).")
    ap.add_argument("--campaign-file", default=None,
                    help="the campaign store (default data/fe_campaign.json)")


def register(feworld):
    global fw
    fw = feworld
    fw.register_handler(PROCLAMATION_REQ, on_proclamation, override=True)
    fw.register_handler(DECIDE_COUNTRY_REQ, on_decide_country, override=True)
    # 0xF500/0xF501 were fegm's and 0x2081 feitems' LOG-ONLY placeholders
    # (fegm read the keep dialog's five-member loop `cmp esi,5` as a GM gate).
    # Both load earlier; this shadows them on purpose, the 0x208C pattern.
    for mid, fn in ((F_BUILD_KEEP, on_build_keep), (F_REPLY_KEEP, on_reply_keep),
                    (0x2081, on_draw_crystal)):
        prev = fw.EXT_HANDLERS.get(mid)
        if prev is not None and prev is not fn and \
                getattr(prev, "__module__", "") in ("fegm", "feitems"):
            fw.EXT_HANDLERS[mid] = fn
        else:
            fw.register_handler(mid, fn)
    fw.register_gm(gm)
    fw.register_args(add_args)
    fw.register_pump(pump)
    fw.register_relay("campaign.keep", on_keep_relay)
    fw.register_relay("campaign.vote", on_vote_relay)
    fw.register_relay("campaign.reward", on_reward_relay)
    fw.register_relay("campaign.crystal", on_crystal_relay)
    for mid, name in (
            (0x1127, "MSG_GET_INFO_OF_PROCLAMATION_OF_WAR_OK [u16 atkCount]"
                     "[u16 defCount][u16 atkMax][u16 defMax] (fecampaign)"),
            (F_REPLY_KEEP, "ReplyToBuildKeep [u8 reply 1=CANCEL 2=OK] (fecampaign)"),
            (F_BUILD_KEEP, "CommandBuildKeepForWar [u16 type][u16 gx][u16 gy]"
                           "[u16 gz][u16 dir] (fecampaign)"),
            (F_READY_STATE, "NotifyBuildToReadyState [u32 party][u16 last_time]"
                            "[u16 type][u16 pos x3][u16 dir][u32 builder]"
                            "[u32 member x5][u8 status x5] (fecampaign)"),
            (F_READY_OK, "NotifyBuildToReadyKeepOK [u32 party] (fecampaign)"),
            (F_READY_ERR, "NotifyBuildToReadyKeepError [s16 code][u32 party]"
                          "[u32 member x5][s16 gold_short][u32 crystal_short]"),
            (0x1021, "MSG_DECIDE_COUNTRY_NG [u32 code]"),
            (0x2081, "draw crystal [u32 crystal building] (fecampaign)")):
        if mid in (0x1127, 0x1021):
            fw.KNOWN.setdefault(mid, name)
        else:
            fw.KNOWN[mid] = name     # fegm/feitems had these as guesses
