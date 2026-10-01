"""feparty.py -- Fantasy Earth PARTY: invite, judge, add/del notifies, leave,
kick, breakup, member-scoped /party chat. PARTIAL: BUILT 2026-09-10, NOT LIVE-TESTED.

Extension module for feworld.py (see THE EXTENSION SEAM there). Registers
through `register(fw)`; never imports feworld itself.

WHAT THE CLIENT ACTUALLY DOES (all measured off the FE_Client.dll runtime dump,
base 0x04F90000, by static disassembly, 2026-09-10)
------------------------------------------------------------------------------
KEY: THE GATE IS NOT THE WINDOW POSTER. The 08-27 sweep filed this group under
"replies go through the window poster 0x0509fc50, a party window must be
open". Every party arm in the main dispatcher (0x05055bdc..0x05055da8) is
instead:

    mov ecx,[0x5339d14]      ; THE PARTY MANAGER singleton
    test ecx,ecx / je 0x5057423   ; no manager -> the message is DROPPED
    push 0x5338350 ; call <helper>   ; the helper acts on the manager

The manager is constructed by 0x05050f90 (called from 0x04ffb0bd, a scene
object factory) and stored at 0x05050fe3; it exists for the life of the
scene, no window needs to be open. The one party id that DOES ride the poster
is 0x113D MSG_PARTY_CHAT_NG (c1 arm 0x05053e02 -> 0x0509fc50): it is discarded
unless a listening window is open, and that is logged on every send.

The manager object (`P` below): +0x04 member list head (0x58-byte records:
+0x18 ChrID, +0x1c name, +0x3e Class, +0x3f/+0x40/+0x42/+0x44/+0x4c the 0x1139
status fields), +0x0c "have party" pointer, +0x4c the pending kick target's
NAME (copied by the 0x2088 builder for the OK message), +0x70 PENDING BITS:
bit0 canvass sent (0x050511b2), bit1 judge YES sent (0x05051369), bit2 leave
sent (0x05051413), bit3 kick sent (0x050514ea). Each OK/NG arm clears its bit,
and 0x050511a0 REFUSES a new invite while +0x70 != 0 -- so an unanswered
0x112E/0x2087/0x2089/0x2088 wedges the party UI for the session. Answering is
the fix; that is why every request here is answered even when refused.

INBOUND (client -> server), read off each BUILDER:
  0x112E MSG_CANVASS_PARTY_MEMBER_REQUEST   [u32 TargetObjID]
         builder 0x050511a0: begin 0x050511c0, write_u32 0x050511cc, flush,
         log 0x52d548c "< MSG_CANVASS_PARTY_MEMBER_REQUEST : TargetObjID=%d".
         TargetObjID is the UNIT id of the selected object ON THE INVITER'S
         SCREEN. Answers: 0x112F OK / 0x1130 NG (main-dispatcher arms).
  0x2087 MSG_JUDGE_JOIN_PARTY_REQUEST       [u8 YesNo][u32 TargetObjID][u32 reason]
         builder 0x05051340: registers 0x1131/0x1132/0x1133/0x1088
         (0x05051362..0x0505137a), begin 0x050513a3, write_u8 0x050513ab,
         write_u32 0x050513b7, write_u32 0x050513c3. Callers: the judge
         window's YES button 0x050ca64c passes (1, [win+0xc0], 0); its NO
         button 0x050ca6b1 passes (0, [win+0xc0], <arg>); the AUTO-DECLINE
         at 0x0505129f (blacklisted inviter, or option byte [0x5166490()+0x4d]
         == 0) passes (0, B, 7). [win+0xc0] is the SECOND u32 of the inbound
         0x112E (see below), so "TargetObjID" here is OUR token echoed back.
  0x113E MSG_CANVASS_PARTY_MEMBER_CANCEL_REQUEST  [u32 TargetObjID]
         builder 0x050ca9f0 (the canvass-wait window's cancel): begin
         0x050caa33, write_u32 0x050caa42 (= the window's own arg), flush,
         then CLOSES ITSELF. Registers nothing -- but bit0 stays set until a
         0x112F/0x1130 arrives, so this module answers it with 0x1130.
  0x2089 MSG_DISBAND_PARTY_REQUEST          (no body) = LEAVE the party
         builder 0x05051410: sets +0x70=4, registers 0x1137/0x1138/0x1136,
         begin 0x05051451, flush. The OK arm prints "Left the party." and
         clears the local member list.
  0x2088 MSG_PARTY_MEMBER_DISBAND_REQUEST   [u32 TargetChrID] = KICK
         builder 0x050514d5: copies the target's name into P+0x4c, registers
         0x113A/0x113B, begin 0x05051505, write_u32 0x0505150d (ebp = the
         member's ChrID), log 0x52d5520 "TargetChrID=%d".
  0x208A /party chat                        [cstr speaker][cstr text]
         same arm as the other chat ids (0x05053cbe); feworld's _CHAT_IDS row.
         Shadowed here with override=True so the fan-out can be scoped.

OUTBOUND (server -> client), read off each ARM (its typed-read sequence + the helper):
  0x112E -> TARGET  [u32 A][u32 B]   helper 0x05051200: read_u32 x2
         (0x0505120d, 0x0505121c). A is looked up as an object of KIND 3
         (0x0504cb30(A,3) -> 0x0504d3a0) for the blacklist test on its
         [+0x3c0] ChrID, and again as KIND 2 (0x0504d410(A,2)) whose
         [+0x389] NAME is what the judge window shows. WARNING: IF NO KIND-2 UNIT
         WITH ID A EXISTS ON THE TARGET'S CLIENT, 0x050512e4 TAKES THE je AND
         NO PROMPT OPENS (nothing is logged). B is stored in [win+0xc0] and
         echoed in 0x2087. The window auto-closes after 0x7530 = 30 s
         (0x050ca731). The prompt plays sound 0x69.
  0x112F MSG_CANVASS_PARTY_MEMBER_OK        header-only  (0x050518a0: clears
         bit0, closes the wait window [0x5343c84], logs)
  0x1130 MSG_CANVASS_PARTY_MEMBER_NG        [u32 err]    (0x050518e0: same,
         then 0x050613e0(0x1130, err) -> the NG table text, codes 0..25)
  0x1131 MSG_JUDGE_JOIN_PARTY_OK            header-only  (0x05051940: clears bit1)
  0x1132 (JUDGE NG, no NG-table row)        [u32 err]    (0x05051960: closes
         the judge window [0x5343c80], 0x050613e0(0x1132, err))
  0x1133 MSG_JUDGE_JOIN_PARTY_CANCEL        header-only  (0x050519b0: closes
         the judge window, msgbox "Inviter cancelled the invite.")
  0x1134 MSG_PARTY_MEMBER_ADD_NOTIFY   [u8 N] then N x {[u32 ChrID][cstr name][u8 Class]}
         KEY: A COUNTED LOOP (flattened in the notes here): helper 0x050515c0 reads u8
         at 0x050515ee, loop 0x05051645..0x050517a7 reads u32 0x0505164c,
         cstr 0x05051658 (strcpy into a 0x200 stack slot -- clamp), u8
         0x05051664. Before the loop: if P+0x0c == 0, msgbox "Party formed."
         when N == 1 else "Joined the party." (0x050515ff). Per record: sets
         [unit+0x9f8]=1 on the unit whose ChrID matches (0x0504d310, not a
         gate), allocates the 0x58 record unless 0x05051160 finds the ChrID
         already, updates the party window [0x5343c7c] (0x050cb420) and the
         map [0x5343c90] (0x050cc4e0), msgbox "%s joined the party." unless
         the record was a duplicate, logs "ChrID=%d [%s] Class=%d".
  0x1135 MSG_PARTY_MEMBER_DEL_NOTIFY        [u32 ChrID]  (0x050517d0: msgbox
         "%sさんがパーティから外れました。" if found, window/map rows removed,
         [unit+0x9f8]=0, record freed 0x05051140)
  0x1136 MSG_PARTY_BREAKUP_NOTIFY           header-only  (0x05051a00: msgbox
         "Party disbanded.", +0x70=0, list freed)
  0x1137 MSG_DISBAND_PARTY_OK               header-only  (0x05051a80: clears
         bit2, msgbox "Left the party.", list freed)
  0x1138 MSG_DISBAND_PARTY_NG               [u32 err]    (0x05051b00)
  0x1139 (member STATUS push, unnamed) [u8 N] then N x {[u32 ChrID][u8 b][u16 w1][u16 w2][u32 d1][u32 d2]}
         KEY: A COUNTED LOOP: helper 0x05051c60 reads u8 0x05051c71, loop
         0x05051c84..0x05051d44 reads u32/u8/u16/u16/u32/u32 into the member
         record at +0x18/+0x3f/+0x40/+0x42/+0x44/+0x4c (0x05051cf2..0x05051d19)
         and writes w2 (sign-extended) to the window row's +0x6c
         (0x05051d34). WARNING: WHAT b/w1/w2/d1/d2 MEAN IS NOT MEASURED -- served
         only by the `!party status` probe with operator-chosen values.
  0x113A MSG_PARTY_MEMBER_DISBAND_OK        header-only  (0x05051b50: clears
         bit3, msgbox "<P+0x4c name> removed from party."; the ROW IS NOT
         REMOVED here -- a 0x1135 must follow)
  0x113B MSG_PARTY_MEMBER_DISBAND_NG        [u32 err]    (0x05051bb0)
  0x113C MSG_PARTY_MEMBER_DISBAND_NOTIFY    header-only  (0x05051be0: msgbox
         "Removed from party.", list freed). WARNING: THE TSV's "PARTY_CHAT_OK" NAME
         WAS DERIVED (NG-1 of 0x113D) AND IS WRONG -- the arm's own log
         string 0x52d56bc names it. This is the "you were kicked" notify.
  0x113D MSG_PARTY_CHAT_NG                  header-only, WINDOW-GATED
         (c1 arm 0x05053e02 -> poster 0x0509fc50)

WHAT IS CHOSEN (not measured) -- every one is a knob or documented:
  * party size 5 (`--party-max`) -- SE's own 2006 guide (flow11): max 5.
  * any member may invite; only the LEADER may kick (0x113B code 2 exists
    for exactly that refusal); the leader leaving promotes the oldest member;
    a party that drops below 2 members is broken up (0x1136).
  * the joiner receives the FULL member list in one 0x1134 (N = party size,
    so the arm says "Joined the party."); existing members receive N=1 with
    the joiner (the leader's first one says "Party formed."). Whether retail
    included the joiner's own row in the joiner's list is unknown -- it is
    included here, so the window shows every member.
  * a NO from the target maps to 0x1130 code 8 ("target refused"); the
    client's auto-decline reason 7 (blacklisted inviter, or invites turned
    off) maps to code 11 "Partner is busy; can't apply." -- it used to be
    passed through as code 7, which the NG table reads as "They are already
    in another party" (fixed 2026-10-01).
  * TEARDOWN (2026-10-01, manual p.43): a member leaves the party on logout
    (readloop.serve's finally -> session_end), on Field Out (0x2033 ->
    field_out), and when their outdoor field/area changes, which covers the
    capital's halves (tick, also nudged from fepresence's 0x2017 shadow).
    The rest get 0x1135 (or 0x1136 when one is left); the leaver, while their
    client is still in the scene, gets 0x1137. Pending invites to or from the
    leaver are closed (inviter 0x1130, target 0x1133). A leader who leaves
    hands over to the oldest member: the client has no "new leader" message
    we know of, and the oldest member is the first row of every client's
    list, so the server's view and the list agree.
  * WAR (manual p.43): during PREP/WAR an invite across hostile sides is
    refused with 0x1130 code 4 ("You serve different nations"), and a member
    whose side becomes hostile to the leader's is removed (0x113C to them) --
    checked on the member's own thread every --party-war-check seconds, and
    by war_sweep() for a caller that wants it at once.
  * BLACKLIST (manual p.48, both directions): an invite between two players
    where either has the other on their blacklist is refused with code 11,
    the same answer the client's own auto-decline gets.
  * invites only within the same field (manual p.43): code 1.
  * an invite nobody answers within `--party-invite-timeout` (35 s, just past
    the prompt's own 30 s) is closed with 0x1130 code 8.
  * a cancel (0x113E) is answered with 0x1130 code 13 ("Cancelled.").

WARNING: THE TWO-CLIENT WALL. feworld never pushes one player's avatar into another
player's client (population is only our 0x1006), and prod runs --unit-id 1
for every session, so (a) the inviter's client cannot SELECT a real peer --
TargetObjID can only name a served avatar/NPC -- and (b) the target's client
has no kind-2 unit for the inviter, so the judge prompt cannot open
(0x050512e4). What ships around it:
  * `--party-phantom on` (OFF by default since 2026-09-11 -- a one-client
    TEST aid, not a game rule: it let an operator party with an NPC or a
    monster): an invite whose TargetObjID is not a
    live session is accepted by a PHANTOM member named after the unit, so one
    client sees the whole invite -> OK -> ADD -> window flow.
  * `--party-proxy-unit N`: the inbound 0x112E's A field, i.e. the unit the
    TARGET'S client must already hold (an `--npc`/`!npc` avatar) for the
    prompt to open, named as that avatar. 0 = use the inviter's charid,
    which only resolves under --unit-id auto AND a peer avatar push that
    does not exist yet.
  * `!party invite NAME` / `!party accept` / `!party decline` drive the
    cross-session relay from --gmcmd-file without the client UI.

KNOBS (read with getattr; the test harness builds args by hand):
  --party-max 5   --party-phantom off|on   --party-proxy-unit 0
  --party-war-check 2 (CHOSEN; 0 = off)
  --party-invite-timeout 35   --party-chat field|party   (field = what prod
  does today: every in-field session gets /party lines; party = members
  only, and a speaker with no party gets 0x113D)   --party-status b,w1,w2,d1,d2
  --party-phantom-base 0x7F000000

!VERBS (--gmcmd-file): !party list | fake NAME[:CLASS] | add N | del NAME |
  invite NAME | cancel | accept | decline [REASON] | leave | kick NAME |
  breakup | status NAME [b w1 w2 d1 d2]

State is a module-level dict under a lock, keyed by party id, membership by
ChrID (charid). It does not persist across restarts -- a party is a session
thing.
"""
import itertools
import struct
import threading
import time

fw = None   # the feworld module, handed in by register()

# ---------------------------------------------------------------------------
# names: the client's own (NG table / arm log strings)
# ---------------------------------------------------------------------------
NAMES = {
    0x112E: "MSG_CANVASS_PARTY_MEMBER_REQUEST [u32 TargetObjID] -> 0x112F / "
            "0x1130 (builder 0x050511a0)",
    0x112F: "MSG_CANVASS_PARTY_MEMBER_OK",
    0x1130: "MSG_CANVASS_PARTY_MEMBER_NG",
    0x1131: "MSG_JUDGE_JOIN_PARTY_OK",
    0x1132: "JUDGE_JOIN_PARTY NG [u32 err] (no NG-table row; arm 0x05051960)",
    0x1133: "MSG_JUDGE_JOIN_PARTY_CANCEL",
    0x1134: "MSG_PARTY_MEMBER_ADD_NOTIFY [u8 N]{u32 ChrID, cstr name, u8 Class}",
    0x1135: "MSG_PARTY_MEMBER_DEL_NOTIFY [u32 ChrID]",
    0x1136: "MSG_PARTY_BREAKUP_NOTIFY",
    0x1137: "MSG_DISBAND_PARTY_OK (= LEFT the party)",
    0x1138: "MSG_DISBAND_PARTY_NG [u32 err]",
    0x1139: "party member STATUS push [u8 N]{u32 ChrID,u8,u16,u16,u32,u32} "
            "(unnamed; fields unmeasured)",
    0x113A: "MSG_PARTY_MEMBER_DISBAND_OK (kick acknowledged)",
    0x113B: "MSG_PARTY_MEMBER_DISBAND_NG [u32 err]",
    0x113C: "MSG_PARTY_MEMBER_DISBAND_NOTIFY (you were kicked) -- NOT "
            "'PARTY_CHAT_OK', see feparty.py",
    0x113D: "MSG_PARTY_CHAT_NG (header-only, window-gated via 0x0509fc50)",
    0x113E: "MSG_CANVASS_PARTY_MEMBER_CANCEL_REQUEST [u32 TargetObjID] "
            "(builder 0x050ca9f0)",
    0x2087: "MSG_JUDGE_JOIN_PARTY_REQUEST [u8 YesNo][u32 TargetObjID][u32 "
            "reason] -> 0x1131 / 0x1132 / 0x1133 (builder 0x05051340)",
    0x2088: "MSG_PARTY_MEMBER_DISBAND_REQUEST [u32 TargetChrID] (= KICK) -> "
            "0x113A / 0x113B (builder 0x050514d5)",
    0x2089: "MSG_DISBAND_PARTY_REQUEST (no body; = LEAVE) -> 0x1137 / 0x1138 "
            "(builder 0x05051410)",
}

# 0x1130 codes, from the client's own NG table (dumped 2026-09-04)
NG_UNDEFINED, NG_MOVED_FIELD, NG_NO_PARAM, NG_SELF, NG_OTHER_NATION = 0, 1, 2, 3, 4
NG_NO_INVITER, NG_NO_TARGET, NG_ALREADY_IN_PARTY, NG_REFUSED = 5, 6, 7, 8
NG_INVITER_CANCELLED, NG_BUSY, NG_PARTNER_BUSY, NG_FULL, NG_CANCELLED = 9, 10, 11, 12, 13
NG_TARGET_BEING_INVITED = 25
# the 0x2087 reason the client's AUTO-DECLINE sends (0x0505129f)
AUTO_DECLINE = 7
# 0x1138 codes
LEAVE_NG_NO_OBJECT, LEAVE_NG_NOT_IN_PARTY = 1, 2
# 0x113B codes
KICK_NG_NO_REQUESTER, KICK_NG_NOT_LEADER, KICK_NG_NOT_IN_PARTY = 1, 2, 3
KICK_NG_NO_PARTY, KICK_NG_NOT_A_MEMBER, KICK_NG_SHORT = 4, 5, 6

GATE = ("gate: party manager [0x5339d14] != 0 (else the arm takes je "
        "0x5057423 and drops it silently; created by 0x05050f90 at scene init)")

_LOCK = threading.Lock()
_PARTIES = {}      # party id -> {"id", "leader", "members": {charid: rec}, "order": [charid]}
_MEMBER_OF = {}    # charid -> party id
_INVITES = {}      # (target charid, inviter charid) -> invite dict
#: charid -> the session dict (fw._TLS.session) that holds that membership.
#: A relog whose old socket has not timed out yet is two sessions on one
#: charid; this says which one the party belongs to, so the old one's
#: teardown cannot remove the new one and the new one can shed a ghost.
_OWNER = {}
_NEXT_PARTY = itertools.count(1)
_NEXT_PHANTOM = itertools.count(0)
_NAME_MAX = 0x20   # the 0x1134 name lands next to [unit+0x389..0x3ac]-sized
                   # slots and in a 0x200 stack buffer; keep the chat clamp


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------
def register(feworld):
    global fw
    fw = feworld
    fw.register_handler(0x112E, on_canvass)
    fw.register_handler(0x2087, on_judge)
    fw.register_handler(0x113E, on_canvass_cancel)
    fw.register_handler(0x2089, on_leave)
    # 0x2088 sits in feworld.UI_HEADER_ONLY_OK (a blind header-only OK); the
    # model here needs it -- the OK must be followed by 0x1135/0x113C pushes.
    fw.register_handler(0x2088, on_kick, override=True)
    # 0x208A is a feworld _CHAT_IDS row; shadowed so the fan-out can be scoped.
    fw.register_handler(0x208A, on_party_chat, override=True)
    fw.register_gm(gm)
    fw.register_args(add_args)
    fw.register_pump(tick)
    for kind, fn in (("party.invite", relay_invite),
                     ("party.canvass_ok", relay_canvass_ok),
                     ("party.canvass_ng", relay_canvass_ng),
                     ("party.judge_cancel", relay_judge_cancel),
                     ("party.add", relay_add),
                     ("party.del", relay_del),
                     ("party.breakup", relay_breakup),
                     ("party.kicked", relay_kicked),
                     ("party.chat", relay_chat)):
        fw.register_relay(kind, fn)
    for mid, name in NAMES.items():
        fw.KNOWN.setdefault(mid, name)


def add_args(ap):
    g = ap.add_argument_group("party (feparty.py)")
    g.add_argument("--party-max", type=int, default=5,
                   help="members per party. Default 5 = SE's 2006 guide (flow11)")
    g.add_argument("--party-phantom", choices=("on", "off"), default="off",
                   help="an invite whose TargetObjID is not a live session "
                        "(an --npc avatar) is accepted by a PHANTOM member so "
                        "one client can see the whole flow -- a TEST aid (it lets "
                        "you party with an NPC or a monster). Default off")
    g.add_argument("--party-proxy-unit", type=lambda s: int(s, 0), default=0,
                   help="the A field of the inbound 0x112E: the KIND-2 unit "
                        "on the TARGET's client that names the inviter "
                        "(0x0504d410(A,2) -> [+0x389]); no such unit = no "
                        "prompt (0x050512e4). 0 = the inviter's charid")
    g.add_argument("--party-invite-timeout", type=float, default=35.0,
                   help="seconds before an unanswered invite is closed with "
                        "0x1130 code 8 (the prompt's own timer is 30 s)")
    g.add_argument("--party-chat", choices=("field", "party"), default="party",
                   help="/party (0x208A) fan-out: party = members only and "
                        "0x113D for a speaker with no party (default; SE's "
                        "flow09 channel list), field = every session in the "
                        "field (the pre-09-11 behaviour)")
    g.add_argument("--party-status", default="0,100,100,0,0",
                   help="b,w1,w2,d1,d2 defaults for the 0x1139 status probe "
                        "(`!party status NAME`); meanings unmeasured")
    g.add_argument("--party-phantom-base", type=lambda s: int(s, 0),
                   default=0x7F000000,
                   help="first ChrID handed to phantom members")
    g.add_argument("--party-war-check", type=float, default=2.0,
                   help="seconds between a member's own check that their war "
                        "side is not hostile to the leader's (manual p.43). "
                        "CHOSEN. 0 = never")
    # Knobs owned by world/chat.py and world/death.py. They are registered
    # HERE because launch.py's parser is a shared file; move them there when
    # it is convenient (both readers use getattr with these defaults).
    w = ap.add_argument_group("chat range and logout (world/chat.py, "
                              "world/death.py)")
    w.add_argument("--say-range", type=float, default=60.0,
                   help="/say (0x201A) reaches players within this many world "
                        "units of the speaker (both positions known, same "
                        "room). CHOSEN: no retail number is known. 0 = the "
                        "whole field (the pre-2026-10-01 behaviour)")
    w.add_argument("--validate-cancel-on-hit", choices=("on", "off"),
                   default="off",
                   help="taking damage while a Field Out countdown is pending "
                        "sends 0x1158 MSG_START_VALIDATE_SEQUENCE_CANCEL_NOTIFY "
                        "and drops the 0x1157 FINISH (manual p.22/23: being "
                        "attacked cancels). The arm 0x050AD1EC was read "
                        "statically; not yet seen live")
    w.add_argument("--validate-capital", choices=("instant", "wait"),
                   default="instant",
                   help="Field Out from a capital finishes at once (0x1157 "
                        "right after the 0x1154 OK) instead of after "
                        "--validate-finish seconds (manual p.22/23: logout in "
                        "a capital is immediate)")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _k(args, name, default):
    return getattr(args, name, default)


def _log(msg):
    print("[feparty] " + msg, flush=True)


def _my_charid(ctx):
    cid = ctx.session.get("charid")
    return None if cid is None else int(cid) & 0xFFFFFFFF


def _my_name(ctx):
    ch = fw._self_char(ctx.args) or {}
    name = ch.get("name")
    if not name:
        # the name the client signs its chat with, if it has chatted
        me = threading.get_ident()
        for s in fw.ext_sessions():
            if s["key"] == me and s["name"]:
                name = s["name"]
    return name or "Player%d" % (_my_charid(ctx) or 0)


def _my_class(ctx):
    try:
        return int(fw.self_class_id(ctx.args)) & 0xFF
    except Exception:                                  # noqa: BLE001
        return int(_k(ctx.args, "status_class", 1) or 1) & 0xFF


def _session_by_charid(charid):
    """The live session dict whose character is `charid`, or None."""
    for s in fw.ext_sessions():
        if s["session"].get("charid") is not None and \
                (int(s["session"]["charid"]) & 0xFFFFFFFF) == charid:
            return s
    return None


def _session_by_name(name):
    want = name.casefold()
    for s in fw.ext_sessions():
        if (s["name"] or "").casefold() == want:
            return s
        # the stored character's name, for a session that has not chatted yet
        cid = s["session"].get("charid")
        acct = s["session"].get("account")
        if cid is None or not acct:
            continue
        try:
            import felobby
            for c in felobby.load_roster(felobby._default_store(), acct) or []:
                if c.get("charid") == cid and \
                        (c.get("name") or "").casefold() == want:
                    return s
        except Exception:                              # noqa: BLE001
            pass
    return None


def _to_charid(cid):
    return lambda s, name: (s.get("charid") is not None
                            and (int(s["charid"]) & 0xFFFFFFFF) == cid)


def _post(kind, payload, cid):
    """Queue for the ONE session serving charid `cid` (phantoms have none).

    include_me=True on purpose: the `to` filter is by charid and no caller
    here ever posts to its own charid (a self-invite is refused up front, and
    the member loops send to `me` with ctx.reply directly), so the thread-key
    exclusion adds nothing -- and the test drives three sessions on ONE
    thread, which that exclusion would silently break."""
    n = fw.ext_post(kind, payload, to=_to_charid(cid), include_me=True)
    if not n:
        _log("   relay %s -> charid %d: no live session (phantom or gone)"
             % (kind, cid))
    return n


# ---------------------------------------------------------------------------
# bodies -- each read sequence is the arm's, VA in the module docstring
# ---------------------------------------------------------------------------
def add_body(records):
    """0x1134: [u8 N] then N x {[u32 ChrID][cstr name][u8 Class]}
    (helper 0x050515c0: u8 0x050515ee; loop u32 0x0505164c, cstr 0x05051658,
    u8 0x05051664)."""
    out = struct.pack(">B", len(records) & 0xFF)
    for r in records[:255]:
        raw = (r["name"] or "").encode("cp932", "replace")[:_NAME_MAX]
        out += struct.pack(">I", r["charid"] & 0xFFFFFFFF) + raw + b"\0"
        out += struct.pack(">B", int(r.get("class", 0)) & 0xFF)
    return out


def del_body(charid):
    """0x1135: [u32 ChrID] (helper 0x050517d0: read_u32 0x050517e5)."""
    return struct.pack(">I", charid & 0xFFFFFFFF)


def err_body(code):
    """0x1130 / 0x1132 / 0x1138 / 0x113B: [u32 err] (each arm: one read_u32
    then 0x050613e0(id, err))."""
    return struct.pack(">I", code & 0xFFFFFFFF)


def invite_body(a, b):
    """Inbound-to-target 0x112E: [u32 A][u32 B] (helper 0x05051200: read_u32
    0x0505120d, read_u32 0x0505121c). A = the kind-2 unit naming the inviter
    on the target's client; B = our token, echoed in 0x2087."""
    return struct.pack(">II", a & 0xFFFFFFFF, b & 0xFFFFFFFF)


def status_body(rows):
    """0x1139: [u8 N] then N x {[u32 ChrID][u8 b][u16 w1][u16 w2][u32 d1][u32 d2]}
    (helper 0x05051c60: u8 0x05051c71; loop u32 0x05051c8e, u8 0x05051c9d,
    u16 0x05051cac, u16 0x05051cbb, u32 0x05051cca, u32 0x05051cd9)."""
    out = struct.pack(">B", len(rows) & 0xFF)
    for cid, b, w1, w2, d1, d2 in rows[:255]:
        out += struct.pack(">IBHHII", cid & 0xFFFFFFFF, b & 0xFF, w1 & 0xFFFF,
                           w2 & 0xFFFF, d1 & 0xFFFFFFFF, d2 & 0xFFFFFFFF)
    return out


def _reply(ctx, mid, body=b"", why=""):
    ctx.reply(mid, body, why="%s -- %s" % (why or NAMES.get(mid, ""), GATE))


# ---------------------------------------------------------------------------
# the model (call with _LOCK held)
# ---------------------------------------------------------------------------
def _party_of(cid):
    pid = _MEMBER_OF.get(cid)
    return _PARTIES.get(pid) if pid is not None else None


def _new_party(leader_rec):
    pid = next(_NEXT_PARTY)
    p = {"id": pid, "leader": leader_rec["charid"], "members": {}, "order": []}
    _PARTIES[pid] = p
    _add_member(p, leader_rec)
    return p


def _add_member(p, rec):
    cid = rec["charid"]
    if cid not in p["members"]:
        p["order"].append(cid)
    p["members"][cid] = rec
    _MEMBER_OF[cid] = p["id"]


def _remove_member(p, cid):
    p["members"].pop(cid, None)
    if cid in p["order"]:
        p["order"].remove(cid)
    _MEMBER_OF.pop(cid, None)
    _OWNER.pop(cid, None)
    if p["leader"] == cid and p["order"]:
        p["leader"] = p["order"][0]


def _drop_party(p):
    for cid in list(p["order"]):
        _MEMBER_OF.pop(cid, None)
        _OWNER.pop(cid, None)
    _PARTIES.pop(p["id"], None)


def _records(p):
    return [dict(p["members"][c]) for c in p["order"]]


def _rec(charid, name, cls, phantom=False):
    return {"charid": charid & 0xFFFFFFFF, "name": name or "",
            "class": int(cls or 0) & 0xFF, "phantom": bool(phantom)}


def _phantom_rec(args, name, cls=0):
    cid = (_k(args, "party_phantom_base", 0x7F000000)
           + next(_NEXT_PHANTOM)) & 0xFFFFFFFF
    return _rec(cid, name, cls, phantom=True)


# ---------------------------------------------------------------------------
# the join: shared by the target's YES, the phantom accept and `!party accept`
# ---------------------------------------------------------------------------
def _join(ctx, inviter_cid, inviter_name, inviter_cls, joiner_rec,
          joiner_is_me, inviter_session=None):
    """Put `joiner_rec` into the inviter's party (creating it with the inviter
    as leader), then PUSH the resulting state (rule 2): every existing member
    gets 0x1134 N=1 {joiner}; the joiner gets 0x1134 N=all. Returns (party,
    existing member charids) or (None, code) on refusal."""
    mine = fw._TLS.session
    with _LOCK:
        if _party_of(joiner_rec["charid"]) is not None:
            return None, NG_ALREADY_IN_PARTY
        p = _party_of(inviter_cid)
        if p is None:
            p = _new_party(_rec(inviter_cid, inviter_name, inviter_cls))
            owner = mine if inviter_cid == _my_charid(ctx) else inviter_session
            if owner is not None:
                _OWNER[inviter_cid] = owner
        if len(p["order"]) >= _k(ctx.args, "party_max", 5):
            return None, NG_FULL
        existing = list(p["order"])
        _add_member(p, joiner_rec)
        if joiner_is_me:
            _OWNER[joiner_rec["charid"]] = mine
        everyone = _records(p)
    for cid in existing:
        if cid == _my_charid(ctx):
            _reply(ctx, 0x1134, add_body([joiner_rec]),
                   why="ADD_NOTIFY N=1 {%s} (a member's view)"
                       % joiner_rec["name"])
        else:
            _post("party.add", {"records": [joiner_rec]}, cid)
    if joiner_is_me:
        _reply(ctx, 0x1134, add_body(everyone),
               why="ADD_NOTIFY N=%d (the joiner's full list; the arm says "
                   "'Joined the party.' for N>1)" % len(everyone))
    elif not joiner_rec.get("phantom"):
        _post("party.add", {"records": everyone}, joiner_rec["charid"])
    _log("party %d: %s joined; members now %s (leader %d)"
         % (p["id"], joiner_rec["name"],
            ", ".join("%s#%d" % (r["name"], r["charid"]) for r in everyone),
            p["leader"]))
    return p, existing


# ---------------------------------------------------------------------------
# inbound handlers
# ---------------------------------------------------------------------------
def on_canvass(ctx, inner):
    """0x112E [u32 TargetObjID] -> 0x112F now (phantom) / later (a session
    answered) / 0x1130 [u32 code]."""
    f = inner[2:]
    if len(f) < 4:
        _reply(ctx, 0x1130, err_body(NG_NO_PARAM), why="NG 2: short body")
        return
    target_obj = struct.unpack_from(">I", f, 0)[0]
    me = _my_charid(ctx)
    _log("0x112E MSG_CANVASS_PARTY_MEMBER_REQUEST TargetObjID=%d from charid=%s"
         % (target_obj, me))
    if me is None:
        _reply(ctx, 0x1130, err_body(NG_NO_INVITER),
               why="NG 5: this session has no charid")
        return
    if target_obj == fw.unit_id_of(ctx.args):
        _reply(ctx, 0x1130, err_body(NG_SELF), why="NG 3: cannot invite yourself")
        return
    with _LOCK:
        p = _party_of(me)
        if p is not None and len(p["order"]) >= _k(ctx.args, "party_max", 5):
            code = NG_FULL
        else:
            code = None
    if code is not None:
        _reply(ctx, 0x1130, err_body(code), why="NG 12: party full")
        return
    # A live session whose unit id is TargetObjID? Only --unit-id auto makes
    # unit ids distinct per session (unit id == charid); under prod's
    # --unit-id 1 every player is unit 1 and this cannot match a peer.
    target = None
    if _k(ctx.args, "unit_id", "0") == "auto":
        target = _session_by_charid(target_obj)
        if target is not None and target["session"].get("charid") == me:
            target = None
    if target is not None:
        _invite_session(ctx, me, target, target_obj)
        return
    if _k(ctx.args, "party_phantom", "off") != "on":
        _reply(ctx, 0x1130, err_body(NG_NO_TARGET),
               why="NG 6: unit %d is no live session and --party-phantom off"
                   % target_obj)
        return
    # THE ONE-CLIENT PROBE: the selected unit is one of our served avatars /
    # NPCs -- accept on its behalf so the window fills.
    name = _unit_name(ctx, target_obj)
    rec = _phantom_rec(ctx.args, name)
    _log("   unit %d is not a session -> PHANTOM member %r ChrID=%d "
         "(--party-phantom on)" % (target_obj, name, rec["charid"]))
    _reply(ctx, 0x112F, why="OK (phantom accepted at once)")
    p, code = _join(ctx, me, _my_name(ctx), _my_class(ctx), rec, False)
    if p is None:
        _reply(ctx, 0x1130, err_body(code), why="NG %d after phantom OK" % code)


def _unit_name(ctx, obj):
    """A name for a served unit id: the --npc/!npc avatar or monster if the
    session knows it, else 'Unit<id>'."""
    mobs = ctx.session.get("mobs") or {}
    m = mobs.get(obj)
    if isinstance(m, dict) and m.get("name"):
        return str(m["name"])
    # feworld's --npc rows are "NAME:..." strings numbered from --npc-base
    npcs = _k(ctx.args, "npc", None) or []
    base = _k(ctx.args, "npc_base", 1000)
    try:
        i = obj - int(base)
        if 0 <= i < len(npcs):
            return str(npcs[i]).split(":")[0][:_NAME_MAX] or "Unit%d" % obj
    except (TypeError, ValueError):
        pass
    return "Unit%d" % obj


def _invite_session(ctx, me, target, target_obj):
    """A real cross-session invite: record it, relay the prompt."""
    tcid = int(target["session"]["charid"]) & 0xFFFFFFFF
    ts = target["session"]
    _reap_ghost(tcid)
    # the manual's rules (p.43, p.48), before the model is touched
    rule = None
    if not _same_field(fw._TLS.session, ts):
        rule = NG_MOVED_FIELD, "not in the same field (manual p.43)"
    elif _hostile(fw._TLS.session, ts, ctx.args):
        rule = NG_OTHER_NATION, "hostile war sides (manual p.43)"
    elif _blocked(ctx, ts, tcid, target.get("name")):
        rule = NG_PARTNER_BUSY, "blacklist, one way or the other (manual p.48)"
    if rule is not None:
        _reply(ctx, 0x1130, err_body(rule[0]),
               why="NG %d: %s" % (rule[0], rule[1]))
        return
    with _LOCK:
        if _party_of(tcid) is not None:
            code = NG_ALREADY_IN_PARTY
        elif any(k[0] == tcid for k in _INVITES):
            code = NG_TARGET_BEING_INVITED
        else:
            code = None
            _INVITES[(tcid, me)] = {
                "inviter": me, "inviter_name": _my_name(ctx),
                "inviter_class": _my_class(ctx), "target": tcid,
                "target_obj": target_obj, "t0": time.time(),
                "inviter_session": fw._TLS.session}
    if code is not None:
        _reply(ctx, 0x1130, err_body(code), why="NG %d" % code)
        return
    a = _k(ctx.args, "party_proxy_unit", 0) or me
    payload = {"a": a, "b": me, "inviter": me, "inviter_name": _my_name(ctx)}
    n = _post("party.invite", payload, tcid)
    _log("   invite charid %d -> charid %d queued (%d); the inviter's client "
         "now waits on 0x112F/0x1130 (bit0 of [P+0x70])" % (me, tcid, n))
    if not n:
        with _LOCK:
            _INVITES.pop((tcid, me), None)
        _reply(ctx, 0x1130, err_body(NG_NO_TARGET), why="NG 6: session gone")


def on_judge(ctx, inner):
    """0x2087 [u8 YesNo][u32 TargetObjID][u32 reason] from the TARGET."""
    f = inner[2:]
    if len(f) < 9:
        _reply(ctx, 0x1132, err_body(NG_NO_PARAM), why="short body")
        return
    yes, token, reason = struct.unpack_from(">BII", f, 0)
    me = _my_charid(ctx)
    _log("0x2087 MSG_JUDGE_JOIN_PARTY_REQUEST YesNo=%d TargetObjID=%d reason=%d "
         "from charid=%s" % (yes, token, reason, me))
    with _LOCK:
        key = (me, token) if (me, token) in _INVITES else next(
            (k for k in _INVITES if k[0] == me), None)
        inv = _INVITES.pop(key, None) if key else None
    if inv is None:
        _reply(ctx, 0x1132, err_body(NG_NO_INVITER),
               why="no pending invite for charid %s (token %d)" % (me, token))
        return
    _judge(ctx, inv, bool(yes), reason)


def _judge(ctx, inv, yes, reason):
    """Apply the target's answer (this runs on the TARGET's thread)."""
    me = _my_charid(ctx)
    if not yes:
        _reply(ctx, 0x1131, why="OK: the NO was taken (clears bit1 only)")
        if reason == AUTO_DECLINE:
            # 0x0505129f's auto-decline: blacklisted inviter or invites
            # turned off. Code 7 would read "already in another party".
            code = NG_PARTNER_BUSY
        else:
            code = reason if 0 < reason <= NG_TARGET_BEING_INVITED else NG_REFUSED
        _post("party.canvass_ng", {"code": code, "target": me}, inv["inviter"])
        _log("   declined (reason %d) -> inviter gets 0x1130 code %d"
             % (reason, code))
        return
    rec = _rec(me, _my_name(ctx), _my_class(ctx))
    p, code = _join(ctx, inv["inviter"], inv["inviter_name"],
                    inv["inviter_class"], rec, True,
                    inviter_session=inv.get("inviter_session"))
    if p is None:
        _reply(ctx, 0x1132, err_body(code), why="join refused, code %d" % code)
        _post("party.canvass_ng", {"code": code, "target": me}, inv["inviter"])
        return
    _reply(ctx, 0x1131, why="OK: joined party %d" % p["id"])
    _post("party.canvass_ok", {"target": me}, inv["inviter"])


def on_canvass_cancel(ctx, inner):
    """0x113E [u32 TargetObjID]: the inviter withdrew. Answer 0x1130 code 13
    (bit0 stays set until 0x112F/0x1130) and close the target's prompt."""
    f = inner[2:]
    target_obj = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
    me = _my_charid(ctx)
    _log("0x113E MSG_CANVASS_PARTY_MEMBER_CANCEL_REQUEST TargetObjID=%d from "
         "charid=%s" % (target_obj, me))
    with _LOCK:
        mine = [k for k, v in _INVITES.items() if v["inviter"] == me]
        for k in mine:
            _INVITES.pop(k, None)
    for tcid, _ in mine:
        _post("party.judge_cancel", {"inviter": me}, tcid)
    _reply(ctx, 0x1130, err_body(NG_CANCELLED),
           why="NG 13 'Cancelled.' -- clears bit0 so the next invite is allowed")


def on_leave(ctx, inner):
    """0x2089 (no body): LEAVE. 0x1137 to me, 0x1135 to the rest, 0x1136 to a
    last member left alone."""
    me = _my_charid(ctx)
    _log("0x2089 MSG_DISBAND_PARTY_REQUEST (leave) from charid=%s" % me)
    with _LOCK:
        p = _party_of(me) if me is not None else None
        if p is None:
            code = LEAVE_NG_NOT_IN_PARTY
            rest = []
        else:
            code = None
            _remove_member(p, me)
            rest = list(p["order"])
            breakup = len(rest) < 2
            if breakup:
                _drop_party(p)
    if code is not None:
        _reply(ctx, 0x1138, err_body(code), why="NG 2: not in a party")
        return
    _reply(ctx, 0x1137, why="OK 'Left the party.' (the arm frees the list)")
    for cid in rest:
        if breakup:
            _post("party.breakup", {}, cid)
        else:
            _post("party.del", {"charid": me}, cid)
    _log("   left party %d; %s" % (p["id"], "BROKEN UP (%d left)" % len(rest)
                                    if breakup else "leader now %d" % p["leader"]))


def on_kick(ctx, inner):
    """0x2088 [u32 TargetChrID]: leader kicks. 0x113A then 0x1135 to the leader
    (the OK arm prints but does not remove the row), 0x113C to the kicked,
    0x1135 to everyone else."""
    f = inner[2:]
    if len(f) < 4:
        _reply(ctx, 0x113B, err_body(KICK_NG_SHORT), why="NG 6: short body")
        return
    target = struct.unpack_from(">I", f, 0)[0]
    me = _my_charid(ctx)
    _log("0x2088 MSG_PARTY_MEMBER_DISBAND_REQUEST TargetChrID=%d from charid=%s"
         % (target, me))
    with _LOCK:
        p = _party_of(me) if me is not None else None
        if p is None:
            code = KICK_NG_NOT_IN_PARTY
        elif p["leader"] != me:
            code = KICK_NG_NOT_LEADER
        elif target not in p["members"] or target == me:
            code = KICK_NG_NOT_A_MEMBER
        else:
            code = None
            kicked = p["members"][target]
            _remove_member(p, target)
            rest = list(p["order"])
            breakup = len(rest) < 2
            if breakup:
                _drop_party(p)
    if code is not None:
        _reply(ctx, 0x113B, err_body(code), why="NG %d" % code)
        return
    _reply(ctx, 0x113A, why="OK '<name> removed from party.'")
    _reply(ctx, 0x1135, del_body(target),
           why="DEL_NOTIFY %s -- the OK arm does not remove the row" % kicked["name"])
    if not kicked.get("phantom"):
        _post("party.kicked", {}, target)
    for cid in rest:
        if cid == me:
            if breakup:
                _reply(ctx, 0x1136, why="BREAKUP: alone after the kick")
            continue
        if breakup:
            _post("party.breakup", {}, cid)
        else:
            _post("party.del", {"charid": target}, cid)


def on_party_chat(ctx, inner):
    """0x208A [cstr speaker][cstr text]. --party-chat field = feworld's own
    behaviour reproduced (echo + everyone in field); party = members only."""
    f = inner[2:]
    parts = [p.decode("cp932", "replace") for p in f.split(b"\0")[:2]]
    while len(parts) < 2:
        parts.append("")
    speaker, text = parts
    fw._chat_room_name(speaker)
    args = ctx.args
    _log("0x208A /party %r: %r (scope %s)"
         % (speaker, text, _k(args, "party_chat", "party")))
    echo = _k(args, "chat_echo", "off")
    if echo == "gm":
        fw.gm_command(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                      "@%s: %s" % (speaker, text))
    elif echo == "say":
        fw.chat_send(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, 0x208A, parts)
    elif echo == "self":
        fw.chat_send(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, 0x208A, parts,
                     speaker=fw.unit_id_of(args))
    if _k(args, "party_chat", "party") != "party":
        if _k(args, "chat_relay", "on") == "on":
            n = fw.chat_relay(0x208A, parts)
            _log("   field scope: queued for %d other session(s)" % n)
        return
    me = _my_charid(ctx)
    with _LOCK:
        p = _party_of(me) if me is not None else None
        members = [c for c in p["order"] if c != me] if p else None
    if members is None:
        _reply(ctx, 0x113D,
               why="MSG_PARTY_CHAT_NG header-only -- WARNING: WINDOW-GATED: c1 arm "
                   "0x05053e02 hands it to the poster 0x0509fc50, discarded "
                   "unless a listening window is open")
        return
    n = 0
    for cid in members:
        n += _post("party.chat", {"parts": parts, "cid": me}, cid)
    _log("   party scope: queued for %d of %d member session(s)"
         % (n, len(members)))


# ---------------------------------------------------------------------------
# relay receivers -- these run on the RECEIVING session's thread
# ---------------------------------------------------------------------------
def relay_invite(ctx, pl):
    a, b = pl["a"], pl["b"]
    _reply(ctx, 0x112E, invite_body(a, b),
           why="INVITE from %s: A=%d (must be a KIND-2 unit on THIS client or "
               "0x050512e4 opens no prompt -- --party-proxy-unit), B=%d token"
               % (pl["inviter_name"], a, b))


def relay_canvass_ok(ctx, pl):
    _reply(ctx, 0x112F, why="OK: charid %d accepted" % pl["target"])


def relay_canvass_ng(ctx, pl):
    _reply(ctx, 0x1130, err_body(pl["code"]),
           why="NG %d from charid %d" % (pl["code"], pl["target"]))


def relay_judge_cancel(ctx, pl):
    _reply(ctx, 0x1133, why="CANCEL: inviter %d withdrew ('Inviter cancelled "
                            "the invite.')" % pl["inviter"])


def relay_add(ctx, pl):
    recs = pl["records"]
    _reply(ctx, 0x1134, add_body(recs),
           why="ADD_NOTIFY N=%d {%s}" % (len(recs),
                                         ", ".join(r["name"] for r in recs)))


def relay_del(ctx, pl):
    _reply(ctx, 0x1135, del_body(pl["charid"]),
           why="DEL_NOTIFY ChrID=%d" % pl["charid"])


def relay_breakup(ctx, pl):
    _reply(ctx, 0x1136, why="BREAKUP_NOTIFY 'Party disbanded.'")


def relay_kicked(ctx, pl):
    _reply(ctx, 0x113C, why="MSG_PARTY_MEMBER_DISBAND_NOTIFY 'Removed from party.'")


def relay_chat(ctx, pl):
    parts = pl["parts"]
    try:
        # by the speaker's charid or the name case-folded (the client stores
        # blacklist names upper-cased; an exact compare missed until 10-01)
        blocked = fw.character.blacklist_hit(fw.blacklist_rows(ctx.args),
                                             pl.get("cid"), parts[0])
    except Exception:                                  # noqa: BLE001
        blocked = False
    if blocked:
        _log("   /party relay: %r is on this player's blacklist -- 0x05170e10 "
             "would drop it silently, dropped here instead" % parts[0])
        return
    fw.chat_send(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args, 0x208A,
                 parts, speaker=0)


# ---------------------------------------------------------------------------
# the manual's rules: same field, war sides, blacklist (manual p.43, p.48)
# ---------------------------------------------------------------------------
def _area(s):
    """The outdoor field a session stands in, else None (on the map, not
    loaded, or indoors: a room's `field` is the island's war field, not the
    capital the door is in, so indoors says nothing about a move)."""
    if not s or not s.get("in_field") or s.get("room", -1) != -1:
        return None
    return s.get("field")


def _same_field(a, b):
    """Both in the same field. Unknown on either side passes: a refusal on a
    guess is worse than the old behaviour."""
    fa, fb = (a or {}).get("field"), (b or {}).get("field")
    return fa is None or fb is None or fa == fb


def _war_side(s, args):
    """"atk"/"def" for a session standing in a field at PREP/WAR, else None.
    The member row fecampaign keeps for a sign-up wins; else the nation (the
    attacker's nation is "atk", the holder's "def"; anyone else has no side
    until they sign up). Read only, from any thread."""
    import sys
    fc = sys.modules.get("fecampaign")
    area = _area(s)
    if fc is None or area is None:
        return None
    try:
        if int(area) in fw.CAPITAL_GROUP_IDS:
            return None
        r = fc.state_of(area)
        if r["phase"] not in (fc.PREP, fc.WAR):
            return None
        cid, acct = s.get("charid"), s.get("account")
        if cid is not None:
            m = (r.get("members") or {}).get("%s|%d" % (acct, int(cid)))
            if m is not None and m.get("side") in ("atk", "def"):
                return m["side"]
        nation = s.get("campaign_nation")
        if not nation:
            nation = (s.get("pres_card") or {}).get("force")
        nation = int(nation or 0)
        if not nation:
            return None
        if nation == int(r.get("atk") or 0):
            return "atk"
        if nation == fw.territory_owner(area, args):
            return "def"
    except Exception:                                  # noqa: BLE001
        return None
    return None


def _hostile(a, b, args):
    sa, sb = _war_side(a, args), _war_side(b, args)
    return sa is not None and sb is not None and sa != sb


def _blocked(ctx, ts, tcid, tname):
    """Either side has the other blacklisted (character.blacklist_blocked)."""
    try:
        return bool(fw.character.blacklist_blocked(
            ctx.args, ts.get("account"), tcid, tname or ""))
    except Exception:                                  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# teardown: logout, Field Out, an area change, a hostile side (manual p.43)
# ---------------------------------------------------------------------------
def _live(s):
    """Is session dict `s` still a listed world session?"""
    return any(e["session"] is s for e in fw.ext_sessions())


def _reap_ghost(cid):
    """`cid`'s membership belongs to a session that is gone (it died without
    its teardown running, which only an exception in serve() could do): drop
    it so the player is not "already in a party" with nobody behind it."""
    with _LOCK:
        owner = _OWNER.get(cid)
        ghost = owner is not None and _party_of(cid) is not None \
            and not _live(owner)
    if ghost:
        _log("charid %d's membership has no live session -- removed" % cid)
        _drop_member(cid, "ghost")


def _drop_member(cid, why):
    """Take `cid` out of its party and tell the rest: 0x1135 each, or 0x1136
    when one member would be left. Returns the party dict, or None."""
    with _LOCK:
        p = _party_of(cid)
        if p is None:
            return None
        was_leader = p["leader"] == cid
        _remove_member(p, cid)
        rest = list(p["order"])
        breakup = len(rest) < 2
        if breakup:
            _drop_party(p)
    for other in rest:
        if breakup:
            _post("party.breakup", {}, other)
        else:
            _post("party.del", {"charid": cid}, other)
    _log("charid %d left party %d (%s); %s" % (
        cid, p["id"], why, "BROKEN UP (%d left)" % len(rest) if breakup
        else "leader now %d%s" % (p["leader"], " (handed over)" if was_leader
                                  else "")))
    return p


def _drop_invites(cid, code):
    """Close every pending invite to or from `cid`: the inviter's wait
    window gets 0x1130 `code`, the target's prompt 0x1133."""
    with _LOCK:
        hit = [(k, v) for k, v in _INVITES.items()
               if v["inviter"] == cid or v["target"] == cid]
        for k, _v in hit:
            _INVITES.pop(k, None)
    for _k_, v in hit:
        if v["inviter"] == cid:
            _post("party.judge_cancel", {"inviter": cid}, v["target"])
        else:
            _post("party.canvass_ng", {"code": code, "target": cid},
                  v["inviter"])
    return len(hit)


def leave_all(why, code=NG_NO_TARGET, ctx=None, self_mid=None):
    """THIS session's character leaves its party and drops its invites.
    `self_mid` (0x1137 / 0x113C) is sent to its own client when `ctx` is
    given -- only while that client is still in the scene. Never raises."""
    if fw is None:
        return False
    try:
        s = fw._TLS.session
        cid = s.get("charid") if s is not None else None
        if cid is None:
            return False
        cid = int(cid) & 0xFFFFFFFF
        with _LOCK:
            owner = _OWNER.get(cid)
        if owner is not None and owner is not s:
            return False          # a newer session of this charid holds it
        _drop_invites(cid, code)
        p = _drop_member(cid, why)
        if p is not None and ctx is not None and self_mid:
            _reply(ctx, self_mid, why="%s (%s)" % (NAMES.get(self_mid, ""), why))
        return p is not None
    except Exception:                                  # noqa: BLE001
        import traceback
        _log("leave_all(%s) failed:" % why)
        traceback.print_exc()
        return False


def session_end():
    """Logout / disconnect: called from readloop.serve()'s finally, on the
    dying session's own thread. Manual p.43: a leader who logs out hands the
    party over and it carries on without them."""
    return leave_all("logout", NG_NO_TARGET)


def field_out(ctx=None):
    """Field Out (0x2033 answered): leaving the field screen leaves the party
    (manual p.43). Nothing to the leaver: its scene, and the party manager
    with it (0x05050f90 is a scene object), is going away."""
    return leave_all("field out", NG_MOVED_FIELD)


def area_check(ctx):
    """An area change leaves the party (manual p.43: another field, or
    another area inside the capital). Called from the tick and nudged from
    fepresence's 0x2017 shadow; compares the outdoor field this member had
    when last seen with the one they stand in now."""
    me = _my_charid(ctx)
    if me is None:
        return False
    now = _area(fw._TLS.session)
    with _LOCK:
        p = _party_of(me)
        rec = p["members"].get(me) if p else None
        if rec is None or now is None:
            return False
        was = rec.get("area")
        if was is None:
            rec["area"] = now
            return False
        moved = was != now
    if moved:
        return leave_all("moved from area %s to %s" % (was, now),
                         NG_MOVED_FIELD, ctx, 0x1137)
    return False


def _membership_tick(ctx, me):
    s = fw._TLS.session
    with _LOCK:
        p = _party_of(me)
        owner = _OWNER.get(me)
        if p is not None and owner is None:
            _OWNER[me] = owner = s
        leader = p["leader"] if p else None
    if p is None:
        return
    if owner is not s:
        # this charid's party was joined by an OLDER session (a relog whose
        # old socket has not timed out): this client has no party list, so
        # the membership is a ghost -- shed it the way a logout would
        _log("charid %d: party %d belongs to an older session -- removed"
             % (me, p["id"]))
        with _LOCK:
            _OWNER[me] = s
        leave_all("relog", NG_NO_TARGET)
        return
    if area_check(ctx):
        return
    every = float(_k(ctx.args, "party_war_check", 2.0) or 0)
    if not every or leader == me:
        return
    now = time.time()
    if now - float(s.get("party_war_t", 0) or 0) < every:
        return
    s["party_war_t"] = now
    with _LOCK:
        lead_s = _OWNER.get(leader)
    if lead_s is None:
        ent = _session_by_charid(leader)
        lead_s = ent["session"] if ent else None
    if lead_s is not None and _hostile(s, lead_s, ctx.args):
        leave_all("on a side hostile to the leader", NG_OTHER_NATION, ctx,
                  0x113C)


def war_sweep(args):
    """Remove every member whose war side is hostile to their leader's, at
    once, from any thread (manual p.43). Each member's own tick does the same
    within --party-war-check seconds; this is for a caller that has just
    started a war or taken a sign-up and wants it now. Returns the charids
    removed."""
    if fw is None:
        return []
    with _LOCK:
        plan = [(p["leader"], list(p["order"])) for p in _PARTIES.values()]
        owners = dict(_OWNER)
    out = []
    for leader, order in plan:
        lead_s = owners.get(leader)
        if lead_s is None:
            continue
        for cid in order:
            s = owners.get(cid)
            if cid == leader or s is None or not _hostile(s, lead_s, args):
                continue
            _drop_invites(cid, NG_OTHER_NATION)
            if _drop_member(cid, "war: hostile to the leader") is not None:
                _post("party.kicked", {}, cid)
                out.append(cid)
    return out


# ---------------------------------------------------------------------------
# the pump: invite timeouts (runs on every session's thread; only the
# inviter's own thread sends its 0x1130)
# ---------------------------------------------------------------------------
def tick(ctx):
    me = _my_charid(ctx)
    if me is None:
        return
    _membership_tick(ctx, me)
    limit = _k(ctx.args, "party_invite_timeout", 35.0)
    now = time.time()
    with _LOCK:
        stale = [k for k, v in _INVITES.items()
                 if v["inviter"] == me and now - v["t0"] > limit]
        for k in stale:
            _INVITES.pop(k, None)
    for tcid, _ in stale:
        _log("invite to charid %d unanswered for %.0fs -> 0x1130 code 8"
             % (tcid, limit))
        _post("party.judge_cancel", {"inviter": me}, tcid)
        _reply(ctx, 0x1130, err_body(NG_REFUSED), why="NG 8: invite timed out")


# ---------------------------------------------------------------------------
# !party verbs
# ---------------------------------------------------------------------------
def gm(ctx, line):
    if not (line == "!party" or line.startswith("!party ")):
        return False
    words = line.split()
    verb = words[1] if len(words) > 1 else "list"
    rest = words[2:]
    me = _my_charid(ctx)
    fn = {"list": _gm_list, "fake": _gm_fake, "add": _gm_add, "del": _gm_del,
          "invite": _gm_invite, "cancel": _gm_cancel, "accept": _gm_accept,
          "decline": _gm_decline, "leave": _gm_leave, "kick": _gm_kick,
          "breakup": _gm_breakup, "status": _gm_status}.get(verb)
    if fn is None:
        _log("!party: unknown verb %r (list|fake|add|del|invite|cancel|accept|"
             "decline|leave|kick|breakup|status)" % verb)
        return True
    if me is None and verb != "list":
        _log("!party %s: this session has no charid yet" % verb)
        return True
    fn(ctx, me, rest)
    return True


def _gm_list(ctx, me, rest):
    with _LOCK:
        ps = [dict(p, members=_records(p)) for p in _PARTIES.values()]
        inv = dict(_INVITES)
    if not ps and not inv:
        _log("!party list: no parties, no pending invites")
    for p in ps:
        _log("party %d leader %d: %s" % (p["id"], p["leader"], ", ".join(
            "%s#%d%s" % (r["name"], r["charid"], " (phantom)" if r["phantom"]
                         else "") for r in p["members"])))
    for (t, i), v in inv.items():
        _log("invite %d -> %d, %.0fs old" % (i, t, time.time() - v["t0"]))


def _find_member(p, word):
    """A member by name (casefold) or numeric ChrID."""
    for cid in p["order"]:
        r = p["members"][cid]
        if r["name"].casefold() == word.casefold():
            return r
    try:
        cid = int(word, 0) & 0xFFFFFFFF
        return p["members"].get(cid)
    except ValueError:
        return None


def _gm_fake(ctx, me, rest):
    if not rest:
        _log("!party fake NAME[:CLASS]")
        return
    name, _, cls = rest[0].partition(":")
    rec = _phantom_rec(ctx.args, name[:_NAME_MAX], int(cls or 0))
    p, code = _join(ctx, me, _my_name(ctx), _my_class(ctx), rec, False)
    if p is None:
        _log("!party fake: refused, 0x1130 code %d" % code)


def _gm_add(ctx, me, rest):
    n = int(rest[0]) if rest else 1
    for i in range(max(1, min(n, 254))):
        _gm_fake(ctx, me, ["Fake%d" % i])


def _gm_del(ctx, me, rest):
    with _LOCK:
        p = _party_of(me)
        r = _find_member(p, rest[0]) if (p and rest) else None
        if r:
            _remove_member(p, r["charid"])
            others = [c for c in p["order"] if c != me]
    if not r:
        _log("!party del: no such member in my party")
        return
    _reply(ctx, 0x1135, del_body(r["charid"]), why="DEL_NOTIFY %s" % r["name"])
    for cid in others:
        _post("party.del", {"charid": r["charid"]}, cid)


def _gm_invite(ctx, me, rest):
    if not rest:
        _log("!party invite NAME  (a live session's character name)")
        return
    s = _session_by_name(rest[0])
    # ctx.session is feworld's thread-local PROXY, never the dict itself, so
    # "is it me" must compare charids
    if s is None or s["session"].get("charid") == me:
        _log("!party invite: no OTHER live session is named %r" % rest[0])
        return
    _invite_session(ctx, me, s, 0)


def _gm_cancel(ctx, me, rest):
    on_canvass_cancel(ctx, struct.pack(">HI", 0x113E, 0))


def _gm_accept(ctx, me, rest):
    with _LOCK:
        key = next((k for k in _INVITES if k[0] == me), None)
        inv = _INVITES.pop(key, None) if key else None
    if inv is None:
        _log("!party accept: no invite pending for charid %d" % me)
        return
    _judge(ctx, inv, True, 0)


def _gm_decline(ctx, me, rest):
    with _LOCK:
        key = next((k for k in _INVITES if k[0] == me), None)
        inv = _INVITES.pop(key, None) if key else None
    if inv is None:
        _log("!party decline: no invite pending for charid %d" % me)
        return
    _judge(ctx, inv, False, int(rest[0]) if rest else 0)


def _gm_leave(ctx, me, rest):
    on_leave(ctx, struct.pack(">H", 0x2089))


def _gm_kick(ctx, me, rest):
    with _LOCK:
        p = _party_of(me)
        r = _find_member(p, rest[0]) if (p and rest) else None
    if not r:
        _log("!party kick NAME: no such member in my party")
        return
    on_kick(ctx, struct.pack(">HI", 0x2088, r["charid"]))


def _gm_breakup(ctx, me, rest):
    with _LOCK:
        p = _party_of(me)
        if p is None:
            others = None
        else:
            others = [c for c in p["order"] if c != me]
            _drop_party(p)
    if others is None:
        _log("!party breakup: not in a party")
        return
    _reply(ctx, 0x1136, why="BREAKUP_NOTIFY (operator)")
    for cid in others:
        _post("party.breakup", {}, cid)


def _gm_status(ctx, me, rest):
    """!party status NAME [b w1 w2 d1 d2] -> 0x1139 N=1 for that member."""
    with _LOCK:
        p = _party_of(me)
        r = _find_member(p, rest[0]) if (p and rest) else None
    if not r:
        _log("!party status NAME [b w1 w2 d1 d2]: no such member in my party")
        return
    vals = [int(x, 0) for x in _k(ctx.args, "party_status", "0,100,100,0,0").split(",")]
    vals = (vals + [0] * 5)[:5]
    for i, w in enumerate(rest[1:6]):
        vals[i] = int(w, 0)
    row = (r["charid"],) + tuple(vals)
    _reply(ctx, 0x1139, status_body([row]),
           why="STATUS probe ChrID=%d b=%d w1=%d w2=%d d1=%d d2=%d -- w2 lands "
               "on the window row +0x6c; meanings UNMEASURED" % row)
