"""fepresence.py -- Fantasy Earth PLAYERS SEE EACH OTHER (2026-09-11).

PARTIAL: BUILT 2026-09-11, NOT LIVE-TESTED. Offline harness: tools/fe_presence_test.py.

THE GAP. Until this module, two players in the same field were each alone in
it: no code put one player's character into the other's client, and nothing
passed anyone's movement on. Chat reached across (feworld's chat room), party/
trade/mail relayed messages, but the world itself was single-player.

NOTHING HERE IS NEW PROTOCOL. Three messages, each already proved live on a
non-player object, pointed at a different id:

  0x1006 MSG_ADD type 0   the avatar record. `--npc Bob` rendered with exactly
                          this shape on 2026-08-25 (feworld.npc_push): the ctor
                          0x050679c0 stamps KIND 2 at 0x05067a32, the kind the
                          0x2023 action-0 mover accepts (0x04fea923).
  0x2023 action 0         the mover. `--npc-walk` walked Bob with it the same
                          day: state 0 (the path 0x04feae15 that skips the
                          controller-mode check), the RECEIVING client's own
                          tick pair, tail 0 (feworld.npc_walk_push).
  0x1004 MSG_DEL          header-only; the envelope id is the object. Every
                          monster death since the 09-09 kill (mob_kill_push).

KEY: AND THE MOVEMENT RECORD IS THE CLIENT'S OWN. A client reports its player's
movement as a 28-byte 0x2023 every ~0.4 s (builder 0x0515dd85) -- and that is,
field for field and scale for scale, the action-0 record the receive side
moves OTHER units with (feworld.move_authority's docstring; the whole inbound
0x2023 family is for other units). So one player's heartbeat, re-addressed to
that player's object id, IS the message that walks their avatar in another
client. The only fields rewritten are the ones npc_walk_push proved must be
the receiver's: the tick pair (the receiving client's own, lifted off its
latest heartbeat), state 0 and tail 0. A zero speed pair is floored to 1.0 --
a speed-0 avatar is a statue.

KEY: HOW THE CLIENT DRAWS A REMOTE PLAYER -- STATIC 2026-09-12, THE SPEC
(FE_Client.dll runtime dump, every address below is in it):

  THE RECEIVER (0x2023 action 0, avatar branch 0x04FEACDC -> 0x0515B870)
  keeps a FIVE-slot waypoint queue per unit ([ctrl+0x4c], ctor 0x0515BA40)
  and walks it every frame in 0x0515BCE0: head target at speed
  [ctrl+0x10] = (spd1+spd2) x row[0] of the 7-row table 0x5158E40 (row 0 =
  4.0 u/s in a war field, row 6 = 6.0 in a capital; there is NO row of 12),
  pops a waypoint when the frame's step reaches it, drops a head older than
  400 ms when a newer one is queued, and TELEPORTS to a lone target older
  than 3 s. For a non-local unit the speed is not fixed: 0x0515B77F adds a
  catch-up term each frame ([ctrl+0xa8] = 4D/T, [ctrl+0xac] = -6D/T^2 with
  T = 0.48 s and D = dist/T - speed, computed at push time) and caps it at
  3.0 x nominal (0x5261940). KEY: The "k = 12" measured live on 2026-09-12 was
  that cap: 3 x 4.0, an avatar running flat out because the server fed it
  late, sparse targets. The record's A:B is ONE 64-bit millisecond stamp
  (A high, B low), compared with the receiver's own clock 0x04FFEF10 =
  [0x5336ec0:c4] base + ms since connect; the base is zeroed at connect
  (0x04FFC362) and only 0x1148 changes it (feworld.clock_sync_push).

  THE SENDER (0x0515DAA0, rate-limited to one per 400 ms at 0x0515DD64)
  reports action 0 | clock | state | its SPEED STAT pair (x1000) | its
  position PLUS 0.4 s of its own velocity (0x0515DBD1: a lookahead, so the
  avatar arrives when the next report lands) | unit flags [+0x48] as the
  tail. `state` is the controller MODE that just ended: leaving mode 0
  (walk) or 1 (flying-form air) and entering mode 4 (idle) send a state-1
  record (0x0515C2DA/0x0515C5D2) -- THE STOP. On the receiver a state != 0
  record goes through 0x0515BA10, which needs the avatar in mode 0/1, sets
  [ctrl+0x95] and pushes the final spot; once the queue drains the avatar
  goes mode 3 (300 ms settle) -> 4 (idle animation, 0x5038ED0). The next
  state-0 record moves it and mode 4 -> 0 restarts the walk animation
  (0x0515D430). Forcing state 1 on EVERY record parks a peer for good --
  0x0515BA10 refuses mode 4 -- which is what `--presence-move-state 1` did.

  JUMPS ARE NOT IN THE WALK RECORD. A normal (non-flying) jump is a timed
  ARC: the jump key (0x05152670) simulates the trajectory (0x5065240) and
  starts a pathed-move object (0x5049730, class vtable 0x5261DB4), which
  SENDS ITSELF as 0x2023 ACTION 5 -- 28 bytes: [u32 5][u32 start ms][u32
  end ms][i16 x3 from][i16 x3 to][u32 flags] (builder 0x0503EEF0). The
  inbound arm 0x04FEB210 accepts it on KIND-2 units only, re-bases the two
  times onto its own clock (duration is all that matters) and plays the
  same arc. 0x2023 action 0x11 [u8 value] (0x04FEC6F0) sets the airborne
  flag 0x400 on the header's unit -- sent only by the flying-form mode 1.
  The other actions (1,3,4,6,7,0xa-0xf) are the same family of timed
  action objects (casts, attacks) addressed by the header id.

  SO THE SERVER'S WHOLE JOB IS A VERBATIM RELAY: every 0x2023 a client
  sends, re-addressed to that client's charid, with ONLY the 64-bit clock
  of the 28-byte action-0 record moved into the receiver's clock (or
  nothing at all once --war-clock sync has put every client on the server
  epoch). Substituting speed, pacing, coalescing or holding records fights
  the client's own dead reckoning -- every one of those was tried here
  between 2026-09-11 and 09-12 and every one read as a snap.
  --presence-relay raw (default since 2026-09-12) is that relay;
  `paced` keeps the older synthesized path for an A/B.

OBJECT IDS. A player's object id is their CHARID -- the id feworld spawns the
player's own unit under in 0x1000 and in the self 0x1006, and the one felobby
allocates unique across the whole store (festore.next_charid). That only
works if every client's OWN unit carries its charid too, and the client takes
that id from 0x302B's HEADER (0x050476d3 -> [server_unit+0x9e0] -> the player
unit ctor's arg, [player_unit+0x3c], 0x04ffadce; see feworld's --unit-id
help) -- i.e. `--unit-id auto`. Under a fixed `--unit-id N`
every client's own unit is object N, so a peer whose charid is N would hit the
type-0 arm's "Chara %d already exists" branch, which is an in-place UPDATE
(0x0503ac46): the peer's name and looks would be written onto the VIEWER's own
character. So this module REFUSES to run under a fixed --unit-id, and says so
once. (By the same reading, the self 0x1006 of a character whose charid is not
N misses and builds a second copy of the player -- unverified live; `auto`
retires it either way.) Charids at or above --monster-base (400) would collide
with the server's own monster/NPC/building ids and are refused per id.

WHO SEES WHOM -- a pull on each session's own thread, every inbound message
(the field heartbeat wakes it every ~0.4 s). A peer is VISIBLE when it is:
  * in the same field AND room -- `(field, room)`, both set on every 0x2000;
  * loaded -- `field_ready` (the first 28-byte heartbeat after entry);
  * not gone -- it has not sent 0x2017 (Field Out's last hop) since its last
    0x2000, and its last heartbeat is younger than --presence-stale seconds.
    A socket that died without a FIN keeps its thread for --read-window; the
    staleness is what hides it before then;
  * a different charid, below --monster-base.
Visible and not yet spawned -> 0x1006 at the peer's last reported position.
Spawned and no longer visible -> 0x1004. Spawned and moved -> the relayed
0x2023, only when speed or position actually changed. A jump of more than
TELEPORT_UNITS (a door, a return to base) is a delete + re-add rather than a
walk across the map.

When THIS client leaves a field (field_ready drops on 0x2000) the spawned set
is simply forgotten: the client tore the scene down with everything in it,
exactly as monsters are re-served per field (feworld sets mobs = {}).

WHAT A PEER LOOKS LIKE. The identity sub-record (mask1 bit 0) from the peer's
STORED character, packed by the peer's OWN thread into its session as a card
(name, sex, looks, the form word -- HUM 0x200 for a stored 0, as the self
record serves it -- and the nation). Default submask 0x2FF: Bob's proven 0xFF
plus bit 0x200, the u32 nation at +0x3BC (a plain store, 0x5045e60). NOT bit
0x8000: the profile comment is an unbounded cstr into +0x3D8, measured as a
129-byte buffer on the PLAYER unit's ctor (0x05079c2a); nobody has read the
avatar class's layout there. Worn gear (mask3) is OFF by default: on the self
record a weapon in mask3 set STANCE with nothing to end it and froze runs 2/3
of 2026-09-08 -- --presence-gear on serves it for a peer when someone is ready
to look.

WARNING: INERT UNDER A FIXED --unit-id, AND THAT IS NOT AN OVERSIGHT. When
feworld's argv says `--unit-id 1` (or any fixed N), this module is INERT (see
_active): the three shadows return False having read nothing and the pump
returns before it touches a session. To switch it on, set `--unit-id auto` on
feworld (the docker-compose.yml entrypoint) and recreate the container --
`docker restart` keeps the container's creation-time argv.

NOT DONE (and why): PvP -- the client only reports hits where the LOCAL
player is the attacker (feworld ~15030), so a player hit needs the server to
route 0xA011 at a peer id to that peer's session; next. (Monsters were one
copy PER SESSION until 2026-09-13; feworld's SHARED MONSTERS block now keeps
one set per field, --monster-share.) Chat still speaks as unit 0 (feworld.chat_pump), so no balloon over a
peer's head yet -- the peer object exists now, so that is a one-line follow-up
once presence is seen working.
"""
import math
import struct
import threading
import time

fw = None                       # the feworld module, handed in by register()

#: a reported position further than this from the last one relayed is a
#: TELEPORT (door, return to base, GM goto), not a walk. CHOSEN: the fastest
#: served move is ~speed 1.0 x the engine's run, a few units per 0.4 s sample.
TELEPORT_UNITS = 30.0
#: bits of the identity sub-record this module knows how to pack (feworld's
#: _AVATAR_SUB). A submask bit outside it would promise a field that is never
#: written and desynchronise the record, so it is masked off.
_DEFAULT_SUBMASK = 0x2FF

_ONCE = set()
_ONCE_LOCK = threading.Lock()
#: bumped by `!presence hp` so every session rebuilds its card and re-states
#: the peers it is showing. A re-sent 0x1006 for an id the client already has
#: is the type-0 arm's "Chara %d already exists" IN-PLACE UPDATE (0x0503ac46),
#: so this refreshes an avatar rather than duplicating or deleting it.
_EPOCH = [0]


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------
def register(feworld):
    global fw
    fw = feworld
    # All three SHADOW a builtin arm and ALWAYS return False, so feworld's own
    # arm still answers them (ext_dispatch honours a False return).
    fw.register_handler(0x2023, on_telemetry, override=True)
    fw.register_handler(0x2000, on_enter_area, override=True)
    fw.register_handler(0x2017, on_move_field, override=True)
    fw.register_handler(0x2080, on_distribution, override=True)
    fw.register_pump(pump)
    fw.register_relay("presence.mv", on_move_relay)
    fw.register_gm(gm)
    fw.register_args(add_args)


def add_args(ap):
    ap.add_argument("--presence", default="on", choices=("on", "off"),
                    help="players in the same field see each other: each "
                         "peer is a 0x1006 avatar under its charid, walked by "
                         "its own relayed 0x2023 heartbeat, removed with "
                         "0x1004. Needs --unit-id auto (refuses a fixed id). "
                         "PARTIAL: built 2026-09-11, not live-tested. See "
                         "fepresence.py.")
    ap.add_argument("--presence-dead", default="drop",
                    choices=("drop", "condition", "off"),
                    help="what a peer's DEATH does to the copy other players "
                         "see. 'drop' (default) stops drawing them until they "
                         "revive -- the proven add/remove path, so it is "
                         "certain to look right, at the cost of popping out "
                         "instead of falling over; 'condition' keeps them and "
                         "pushes CONDITION DEAD at their avatar (retail-"
                         "looking, UNPROVEN on a kind-2 served avatar); 'off' "
                         "is the pre-2026-09-12 bug -- Quinn killed Lex and "
                         "Lex stayed standing on Quinn's screen")
    ap.add_argument("--presence-stale", type=float, default=45.0,
                    metavar="SECONDS",
                    help="a peer silent this long is gone -- silent meaning NO "
                         "INBOUND MESSAGE AT ALL, not just no movement "
                         "heartbeat. A dead socket keeps its thread until "
                         "--read-window (120 s), which is what this is for. "
                         "WARNING: It was 10 s against the 0x2023 heartbeat alone "
                         "until 2026-09-11, when the first two-player session "
                         "showed peers FLICKERING: a client sitting in a "
                         "window sends telemetry every ~20 s, so each peer "
                         "went stale, was deleted, and was re-added seconds "
                         "later, over and over.")
    ap.add_argument("--presence-submask", type=lambda v: int(v, 0),
                    default=_DEFAULT_SUBMASK,
                    help="identity sub-record mask for a peer's 0x1006. "
                         "Default 0x2FF = name, sex, looks, form, nation "
                         "(Bob's live 0xFF + 0x200). 0x8000 (profile comment "
                         "into +0x3D8) is left out on purpose -- unread on the "
                         "avatar class.")
    ap.add_argument("--presence-speed", default="auto", choices=("auto", "client"),
                    help="'auto' (default) replaces a relayed sample's speed "
                         "pair with one timed to the REPORT INTERVAL, so the "
                         "avatar is still walking when the next waypoint "
                         "lands. 'client' relays the sender's own pair, which "
                         "is a multiplier on the engine's run speed and makes "
                         "the avatar sprint to the waypoint and park.")
    ap.add_argument("--presence-speed-scale", type=float, default=12.0,
                    metavar="K",
                    help="units per second that ONE unit of the speed pair "
                         "buys. VERIFIED: MEASURED 12.0 on a live client 2026-09-12 "
                         "(live memory read of [ctrl+0x10] against the pair: "
                         "1.300->15.600, 0.165->1.980, 0.525->6.300, exactly "
                         "x12 every time). Guesses of 3 and 6.5 preceded it "
                         "and both animated peers at the wrong rate.")
    ap.add_argument("--presence-relay", default="raw", choices=("raw", "paced"),
                    help="'raw' (default since 2026-09-12): every 0x2023 a "
                         "peer sends is forwarded VERBATIM to the others in "
                         "its field, re-addressed to its charid, the moment "
                         "it arrives (their thread, via ext_post) -- speed "
                         "pair, state, tail, jump arcs (action 5) and all. "
                         "The client's own dead reckoning (a 5-slot waypoint "
                         "queue with a x3 catch-up law, read 2026-09-12) does "
                         "the smoothing, exactly as it did against SE's "
                         "server. 'paced' is the older path: the pump "
                         "synthesizes records from the latest heartbeat with "
                         "--presence-speed/--presence-interp/--presence-move-*.")
    ap.add_argument("--presence-relay-casts", default="on", choices=("on", "off"),
                    help="forward a peer's 0x1031/0x1032 SKILL USE record "
                         "verbatim under their charid, so the others see the "
                         "cast (feprog.on_cast -> fepresence.relay_inner). The "
                         "client's inbound arm for those ids (0x0503A0FD -> "
                         "0x503BB70) reads the layout the client writes. PARTIAL: "
                         "built 2026-09-12, not on a screen.")
    ap.add_argument("--presence-relay-actions", default="all",
                    choices=("all", "move"),
                    help="which 0x2023 records the raw relay forwards: 'all' "
                         "(default) every sub-action -- the 28-byte action 0 "
                         "walk, action 5 jump/dash arcs, the 0x11 airborne "
                         "byte, cast/attack motions -- or 'move', the 28-byte "
                         "action 0 alone.")
    ap.add_argument("--presence-clock", default="arrival",
                    choices=("arrival", "map", "now"),
                    help="how a peer's sample timestamp (the record's 64-bit "
                         "A:B, their own clock in ms) is expressed in the "
                         "receiver's clock. 'arrival' (default since "
                         "2026-09-12): the receiver's clock at the moment the "
                         "sample reached the server -- its own last report's "
                         "B plus the server time since -- which needs no "
                         "offset bookkeeping and cannot drift. 'map' adds ONE "
                         "fixed offset measured off two samples that may be "
                         "seconds apart. 'now' stamps the forwarding moment. "
                         "A record whose A is non-zero (clocks synced by "
                         "0x1148, --war-clock sync) is never rewritten.")
    ap.add_argument("--presence-interp", default="off", choices=("on", "off"),
                    help="PACE a peer's movement server-side instead of "
                         "relaying their raw reports. A client reports its "
                         "position only every ~2.5 s and the mover applies a "
                         "target as a SNAP, so raw relaying is one 3-6 unit "
                         "jump every few seconds. On, the server walks its own "
                         "idea of the peer toward their last report at the "
                         "speed their own reports imply, in steps of "
                         "--presence-step-min. Off = relay reports verbatim "
                         "(with --presence-move-min / --presence-settle-ms).")
    ap.add_argument("--presence-face", default="on", choices=("on", "off"),
                    help="turn a peer to face the way they are walking, with "
                         "a facing-only 0x1006 (mask2 bit 2) before each step. "
                         "Their facing is otherwise written once at spawn and "
                         "never again -- the movement record carries no facing "
                         "field -- which is why peers were seen 'often facing "
                         "the wrong direction' on 2026-09-11.")
    ap.add_argument("--presence-lag-ms", type=float, default=0, metavar="MS",
                    help="how far BEHIND real time a peer is drawn, which is "
                         "what buys smooth motion from reports ~2.5 s apart. "
                         "0 (default) = auto: one report interval, clamped to "
                         "0.3-3 s. Raise it if peers stutter, lower it if "
                         "they feel too far behind where they really are.")
    ap.add_argument("--presence-step-min", type=float, default=1.05,
                    metavar="UNITS",
                    help="smallest paced step. VERIFIED: MEASURED LIVE 2026-09-11: it "
                         "must stay above 1.0. The mover discards a target "
                         "closer than 1.0 units to where the unit stands "
                         "(0x04FEAAD4 vs 0x525f5bc = 1.0f) -- that threshold "
                         "had only ever been read on the MONSTER branch, and "
                         "dropping this to 0.4 live made peer movement "
                         "visibly WORSE, which is the avatar branch answering "
                         "the same question. Smaller steps are thrown away; "
                         "the motion they would have drawn is lost.")
    ap.add_argument("--presence-move-state", default="client",
                    help="what to put in the record's state u16: 'client' "
                         "(default) relays what the sender's own client sent, "
                         "a number forces it. state 0 takes the mover "
                         "0x515b870 directly; non-zero goes through "
                         "0x515ba10, which returns unless the unit's "
                         "controller mode is 0 or 1 (a fresh avatar measured "
                         "4). Forced to 0 until 2026-09-11.")
    ap.add_argument("--presence-move-tail", default="client",
                    help="the record's trailing u32. 'client' (default) "
                         "relays the sender's own -- MEASURED as 0x00080000 in "
                         "every real record -- and at state 0 that value is "
                         "passed to the mover as an argument. Forcing it to 0 "
                         "(the pre-2026-09-11 behaviour, copied from "
                         "npc_walk_push) is the prime suspect for peers that "
                         "SNAP between positions instead of walking.")
    ap.add_argument("--presence-move-min", type=float, default=0.0,
                    metavar="UNITS",
                    help="hold a peer's move until they are this far from the "
                         "last target we relayed. DEFAULT 0 = relay every "
                         "sample, which is what the sender's own client does "
                         "(~0.4 s). Raise it if sub-unit targets are being "
                         "discarded: the mover drops a target under 1.0 units "
                         "from where the unit stands (0x04FEAAD4 vs "
                         "0x525f5bc = 1.0f) while the speed pair is written "
                         "BEFORE that branch, so a discarded move still plays "
                         "the run animation. WARNING: Coalescing to 1.5 was tried "
                         "first for 'running in place' and live testing then "
                         "showed the same run with occasional BLIPS -- which is "
                         "a snap, not a discard, so the tail (see "
                         "--presence-move-tail) was the better suspect.")
    ap.add_argument("--presence-settle-ms", type=float, default=700.0,
                    metavar="MS",
                    help="once a peer stops moving, send their exact position "
                         "this long after the last relay even if it is under "
                         "--presence-move-min -- otherwise they would stand "
                         "up to that far from where they really are.")
    ap.add_argument("--presence-map", default="on", choices=("on", "off"),
                    help="answer the client's ~10 s 0x2080 poll with ONE ROW "
                         "PER PLAYER standing in this field (0x1123) instead "
                         "of feworld's count 0 -- the minimap distribution "
                         "dots. `off` hands the poll back to feworld. "
                         "--distribution-rows (the hand-probe knob) and "
                         "--distribution off both win over this.")
    ap.add_argument("--presence-map-self", default="on", choices=("on", "off"),
                    help="include the player's OWN row in that answer. Off by "
                         "default (the client should already know where it "
                         "is); on is the cheap oracle for whether the two "
                         "position bytes are the grid this thinks they are -- "
                         "a dot on top of you means yes.")
    ap.add_argument("--presence-hp", default="on", choices=("on", "off"),
                    help="carry the peer's HP pair in the 0x1006 stat block "
                         "(mask1 bit 1, bits 1/2 = +0x49A max / +0x49E "
                         "current -- the same applier the self record's "
                         "--add-stats uses). What a PvP damage number "
                         "subtracts from. OFF until presence itself is "
                         "proved on a screen; `!presence hp on` flips it live "
                         "and re-states every peer.")
    ap.add_argument("--presence-gear", default="on", choices=("on", "off"),
                    help="serve a peer's stored WORN gear in the 0x1006 mask3 "
                         "-- otherwise every player is drawn in the default "
                         "outfit, observed live on 2026-09-11 ('the "
                         "other player has nothing equipped'). WARNING: ON since "
                         "then by explicit decision, and the risk is "
                         "named: on the SELF record a weapon in mask3 set "
                         "STANCE with nothing to end it and froze runs 2/3 of "
                         "2026-09-08. Unmeasured on an avatar. If a peer "
                         "stops animating, this is the first knob to put back "
                         "to off (`!presence gear off`, live).")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _on(args):
    return getattr(args, "presence", "on") == "on"


def _active(args):
    """ON, and addressing units the only way that can work. Everything this
    module does -- the three shadows included -- hangs off this, so under
    prod's current `--unit-id 1` the module is INERT: each shadow is a bare
    `return False` and the pump returns before it reads anything. That is
    deliberate, so landing this code changes nothing until the knob moves."""
    if not _on(args):
        return False
    if str(getattr(args, "unit_id", "0")) != "auto":
        _once("unit-id", "OFF: --unit-id is %r, not auto -- every client "
              "addresses its own unit by that one number, so a peer drawn "
              "under its charid could BE that number (the 0x1006 type-0 arm "
              "would apply that peer onto the viewer's own character). "
              "Presence needs --unit-id auto in feworld's argv."
              % (getattr(args, "unit_id", None),))
        return False
    return True


def _log(msg):
    print("[fepresence] " + msg, flush=True)


def _once(key, msg):
    with _ONCE_LOCK:
        if key in _ONCE:
            return
        _ONCE.add(key)
    _log(msg)


def _key(s):
    """(field, room) when session `s` stands in a LOADED field, else None.
    `s` is this thread's _SESSION proxy or another session's plain dict --
    both answer .get()."""
    if not (s.get("in_field") and s.get("field_ready") and s.get("charid")):
        return None
    if s.get("pres_gone"):
        return None
    return (s.get("field"), s.get("room"))


def _clock(mv):
    """The sender's own timestamp for this sample: B, in milliseconds."""
    return struct.unpack_from(">I", mv, fw._MV_A + 4)[0]


def clock_offset(st, peer_mv, my_mv):
    """What to add to the peer's clock to express it in the receiver's.

    The MINIMUM offset ever observed is kept, the way an NTP client keeps its
    minimum round trip: every sample is delayed by some unknown amount before
    we see it, and the smallest delay seen is the closest to the truth. A
    per-sample offset would re-introduce exactly the jitter this exists to
    remove.
    """
    off = _clock(my_mv) - _clock(peer_mv)
    best = st.get("clock_off")
    if best is None or off < best:
        st["clock_off"] = off
        return off
    return best


def _pos(mv):
    """The (x, y, z) a 28-byte heartbeat reports, i16 x0.1 (feworld._MV_POS)."""
    x, y, z = struct.unpack_from(">hhh", mv, fw._MV_POS)
    return (x * 0.1, y * 0.1, z * 0.1)


def _dist(a, b):
    return math.sqrt(sum((p - q) * (p - q) for p, q in zip(a, b)))


def _send(ctx, mid, body, obj):
    fw.send(ctx.conn, ctx.outbound, fw.inner_msg(mid, body, obj), ctx.mode,
            ctx.be, ctx.args.seq_mode == "echo",
            getattr(ctx.args, "world_prefix", 4))


# ---------------------------------------------------------------------------
# the shadows -- record what the builtin arm is about to consume, then DECLINE
# ---------------------------------------------------------------------------
def _relay_mode(args):
    return getattr(args, "presence_relay", "raw") or "raw"


def on_telemetry(ctx, inner):
    """0x2023: keep this session's latest action-0 heartbeat for the others,
    and (--presence-relay raw) hand EVERY 0x2023 to the peers in this field
    the moment it lands -- on their threads, through ext_post.

    WARNING: MUST NOT RAISE: ext_dispatch counts a raising handler as HANDLED, and
    then feworld's own 0x2023 arm -- the clock, field_ready, the equip replay
    -- would never run. Everything is inside the try."""
    try:
        if not _active(ctx.args):
            return False
        f = inner[2:]
        if len(f) < 4:
            return False
        act = struct.unpack_from(">I", f, 0)[0]
        s = fw._SESSION
        now = time.monotonic()
        is_move = len(f) == fw._MV_LEN and act == 0
        if is_move:
            s["pres_mv"] = bytes(f)
            s["pres_mv_n"] = int(s.get("pres_mv_n", 0) or 0) + 1
            s["pres_mv_t"] = now
            # the receiver-side clock map (on_move_relay) is anchored on this
            # session's OWN latest sample: its A:B and when it reached us.
            # A (the HIGH dword) is 0 until a 0x1148 base lands; after one it
            # is not, and a map that kept only B would put every peer record
            # ~56 years in this client's past (see rebase_clock).
            s["pres_mv_a"] = struct.unpack_from(">I", f, fw._MV_A)[0]
            s["pres_mv_b"] = _clock(f)
            s["pres_mv_arr"] = now
        if _relay_mode(ctx.args) != "raw":
            return False
        key = _key(s)
        cid = int(s.get("charid") or 0)
        if key is None or not cid:
            return False
        if not is_move and getattr(ctx.args, "presence_relay_actions",
                                   "all") != "all":
            return False
        n = fw.ext_post("presence.mv",
                        {"cid": cid, "key": key, "body": bytes(f), "arr": now,
                         "act": act},
                        to=lambda ps, name: _key(ps) == key)
        k = int(s.get("pres_post_n", 0) or 0) + 1
        s["pres_post_n"] = k
        if k <= 3 or k % 500 == 0:
            _log("0x2023 action %d (%d B) from charid %d posted raw to %d "
                 "session(s) in %s" % (act, len(f), cid, n, key))
    except Exception as e:                              # noqa: BLE001
        _once("telemetry-fail", "0x2023 shadow failed (%r) -- declined, "
              "feworld's arm still answers" % (e,))
    return False


def relay_inner(ctx, inner):
    """Any inner message from THIS session, forwarded verbatim to the peers in
    its field under its charid (--presence-relay raw). Used for the cast
    record (0x1031/0x1032, via feprog.on_cast); 0x2023 has its own shadow."""
    if not _active(ctx.args) or _relay_mode(ctx.args) != "raw":
        return 0
    if getattr(ctx.args, "presence_relay_casts", "on") != "on":
        return 0
    s = fw._SESSION
    key = _key(s)
    cid = int(s.get("charid") or 0)
    if key is None or not cid or not inner or len(inner) < 2:
        return 0
    mid = struct.unpack_from(">H", inner, 0)[0]
    n = fw.ext_post("presence.mv",
                    {"cid": cid, "key": key, "mid": mid, "body": bytes(inner[2:]),
                     "arr": time.monotonic(), "act": -1},
                    to=lambda ps, name: _key(ps) == key)
    k = int(s.get("pres_cast_n", 0) or 0) + 1
    s["pres_cast_n"] = k
    if k <= 3 or k % 200 == 0:
        _log("0x%04X (%d B) from charid %d posted raw to %d session(s) in %s"
             % (mid, len(inner) - 2, cid, n, key))
    return n


def rebase_clock(body, arr, s, mode="arrival", now=None):
    """The 28-byte action-0 record with its 64-bit A:B moved into THIS
    receiver's clock. None when it cannot be mapped yet (no own sample).

    The client compares A:B against 0x04FFEF10 = base + ms since connect
    (0x0515BDBC..); a record stamped in another client's clock is either
    'due long ago' (dropped, 0x0515BE06, when another is queued) or 'due in
    the far future'. The receiver's clock at the instant the sample reached
    the server is: its own last report's B + the server time since that
    report arrived. Both arrivals are server monotonic, both clocks tick in
    ms, so this needs no offset and cannot drift.

    WARNING: THE WHOLE 64 BITS, BOTH SIDES (2026-09-12). This used to map B alone,
    write A = 0, and pass any record whose A was non-zero through untouched
    on the theory that "A != 0 means the clocks were synced by 0x1148". That
    holds only if BOTH clients took the same base at the same moment. A
    0x1148 base (--world-clock session) is epoch ms, so a synced client's A
    is ~0x1A0, and one player synced ahead of the other broke remote walking
    both ways: a synced sender's record reached an unsynced receiver ~56
    years in its future, and an unsynced sender's record, stamped A = 0,
    reached a synced receiver ~56 years in its past (dropped). The sender's
    stamp is not needed at all -- the target is the RECEIVER's clock -- so
    the result is the receiver's own last A:B plus the elapsed ms. With the
    receiver unsynced (A = 0) this is byte-identical to the old formula."""
    my_b, my_arr = s.get("pres_mv_b"), s.get("pres_mv_arr")
    if my_b is None or my_arr is None:
        return None
    mine = (int(s.get("pres_mv_a", 0) or 0) << 32) | int(my_b)
    if mode == "map":
        # one raw offset, the old way: peer + (mine - peer) is simply mine
        n = mine
    else:
        at = (time.monotonic() if now is None else now) if mode == "now" else arr
        n = mine + int(round((float(at) - float(my_arr)) * 1000.0))
    n &= 0xFFFFFFFFFFFFFFFF
    out = bytearray(body)
    struct.pack_into(">II", out, fw._MV_A, n >> 32, n & 0xFFFFFFFF)
    return bytes(out)


def on_move_relay(ctx, payload):
    """On the RECEIVING session's thread (ext_pump): one peer's 0x2023,
    verbatim, re-addressed to that peer's charid -- if this client already
    draws the peer (the pump's 0x1006 came first) and they share a field."""
    args = ctx.args
    if not _active(args) or _relay_mode(args) != "raw":
        return
    s = fw._SESSION
    if payload.get("key") != _key(s):
        return
    cid = int(payload.get("cid") or 0)
    st = (s.get("pres_seen") or {}).get(cid)
    if st is None:
        # the avatar is not on this screen yet; the pump ADDs it at the
        # peer's latest heartbeat on its next run and the records after
        # that walk it -- nothing lost but this one
        s["pres_raw_early"] = int(s.get("pres_raw_early", 0) or 0) + 1
        return
    body = payload.get("body") or b""
    act = int(payload.get("act", 0))
    mid = int(payload.get("mid", 0x2023))
    if mid != 0x2023:
        # a non-movement record (the cast): verbatim, no clock to map
        _send(ctx, mid, body, cid)
        k = int(s.get("pres_raw_n", 0) or 0) + 1
        s["pres_raw_n"] = k
        if k <= 5 or k % 500 == 0:
            _log("-> 0x%04X (%d B) peer %d #%d VERBATIM" % (mid, len(body), cid, k))
        return
    if len(body) == fw._MV_LEN and act == 0:
        body = rebase_clock(body, float(payload.get("arr", 0.0)), s,
                            getattr(args, "presence_clock", "arrival"))
        if body is None:
            return
        pos = _pos(body)
        st["pos"], st["sent_pos"], st["sent_t"] = pos, pos, time.monotonic()
        st["sent"] = body[fw._MV_SPD:fw._MV_TAIL]
    _send(ctx, 0x2023, body, cid)
    k = int(s.get("pres_raw_n", 0) or 0) + 1
    s["pres_raw_n"] = k
    if k <= 5 or k % 500 == 0:
        if len(body) == fw._MV_LEN and act == 0:
            st_, spd = struct.unpack_from(">Hh", body, fw._MV_STATE)
            _log("-> 0x2023 action 0 peer %d #%d VERBATIM: state %d spd %.3f "
                 "to (%.1f, %.1f, %.1f) tail 0x%08X, B mapped %s"
                 % (cid, k, st_, spd / 1000.0, st["pos"][0], st["pos"][1],
                    st["pos"][2], struct.unpack_from(">I", body, fw._MV_TAIL)[0],
                    getattr(args, "presence_clock", "arrival")))
        else:
            _log("-> 0x2023 action %d (%d B) peer %d #%d VERBATIM"
                 % (act, len(body), cid, k))


def on_enter_area(ctx, inner):
    """0x2000: a field ENTRY clears 'gone'. MUST NOT RAISE (see above)."""
    try:
        if _active(ctx.args):
            fw._SESSION.pop("pres_gone", None)
    except Exception:                                   # noqa: BLE001
        pass
    return False


def on_move_field(ctx, inner):
    """0x2017, Field Out's last hop (0x1045 -> 0x2017 -> 0x1012, feworld's
    VALIDATE notes): the player is leaving for the map. Nothing else clears
    in_field/field_ready on the way out, so without this a peer would keep
    seeing them standing where they left until --presence-stale ran out --
    or forever, if the map screen keeps the heartbeat going."""
    try:
        if _active(ctx.args):
            fw._SESSION["pres_gone"] = True
    except Exception:                                   # noqa: BLE001
        pass
    return False


# ---------------------------------------------------------------------------
# the card: what OTHER clients draw this player as. Built on the owner's thread.
# ---------------------------------------------------------------------------
def _known_bits():
    m = 0
    for bit, _k, _c, _o in fw._AVATAR_SUB:
        m |= bit
    return m


def identity_bytes(ch, sub):
    """The mask1-bit0 sub-record fields for `sub`, ASCENDING bit order (the
    reader 0x5079d60 tests bit 0, 1, 2 ... and reads as it goes) -- the same
    packing feworld.add_entities gives the player's own record."""
    out = b""
    for bit, key, code, _off in fw._AVATAR_SUB:
        if not (sub & bit):
            continue
        if code == "cstr":
            out += str(ch.get(key, "") or "").encode("cp932", "replace")[:32] + b"\x00"
        else:
            v = int(ch.get(key, 0) or 0)
            if key == "f28" and not v:
                v = fw.SELF_FORM_HUM
            out += struct.pack(">" + code, v & {"B": 0xFF, "H": 0xFFFF,
                                                "I": 0xFFFFFFFF}[code])
    return out


def build_card(args, key):
    ch = fw._self_char(args)
    if not ch:
        return None
    want = int(getattr(args, "presence_submask", _DEFAULT_SUBMASK)) & 0xFFFFFFFF
    sub = want & _known_bits()
    if sub != want:
        _once(("submask", want), "--presence-submask 0x%X has bits with no "
              "packer (0x%X) -- serving 0x%X" % (want, want & ~sub, sub))
    m3, worn = 0, b""
    if getattr(args, "presence_gear", "off") == "on":
        m3, worn, _desc = fw.worn_gear_block(fw.stored_equip_rows(args))
    hp = None
    if getattr(args, "presence_hp", "off") == "on":
        hpmax = int(fw.player_hp_max(args))
        hp = (max(0, int(fw._SESSION.get("player_hp", hpmax))), hpmax)
    try:
        cls = int(fw.self_class_id(args) or 0)
    except (TypeError, ValueError):
        cls = 0
    return {"key": key, "sub": sub, "ident": identity_bytes(ch, sub),
            "m3": m3, "worn": worn, "hp": hp, "cls": cls,
            "name": str(ch.get("name", "") or ""),
            # the NATION, published for whoever needs to tell friend from
            # enemy without reaching into another thread's store (fepvp)
            "force": fw.nation_of(args)}


def peer_session(cid):
    """The live session dict of charid `cid` standing in MY (field, room), or
    None -- what fepvp asks before letting a hit land. Read only: the dict
    belongs to that player's thread."""
    me = threading.get_ident()
    key = _key(fw._SESSION)
    if key is None:
        return None
    for ent in fw.ext_sessions():
        if ent["key"] == me:
            continue
        ps = ent["session"]
        if _key(ps) != key:
            continue
        try:
            if int(ps.get("charid") or 0) == int(cid):
                return ps
        except (TypeError, ValueError):
            continue
    return None


#: the two _AVATAR_STATS rows a peer's HP pair rides: bit 1 -> +0x49A (max),
#: bit 2 -> +0x49E (current). ASCENDING bit order, one i16 each, exactly as
#: feworld.add_entities lays the self record's stat block down.
_HP_BITS = (1, 2)


def avatar_body(cid, card, pos):
    """0x1006 [u16 1][u32 obj][u8 type 0] + the type-0 record: mask1 = 1
    (identity) | 2 (the stat block, only when the peer published an HP pair),
    mask2 = 5 (position + facing), mask3 = worn gear or 0.

    WARNING: mask1 bit 1 is derived from the VALUES, never set by hand -- the mask
    promises how many i16 follow, so a bit with nothing behind it does not
    draw a wrong number, it desynchronises the whole record."""
    hp = card.get("hp")
    mask1 = 0x1 | (0x2 if hp else 0)
    rec = struct.pack(">II", mask1, card["sub"]) + card["ident"]
    if hp:
        cur, hpmax = hp
        statmask = 0
        for b in _HP_BITS:
            statmask |= 1 << b
        rec += struct.pack(">II", statmask, 0)
        # ascending: bit 1 is the MAX (+0x49A), bit 2 the CURRENT (+0x49E)
        rec += struct.pack(">hh", hpmax & 0x7FFF, cur & 0x7FFF)
    rec += (struct.pack(">I", 0x5) + struct.pack(">fff", *pos)
           # the facing is NEVER all zero: the look-at basis collapses and the
           # avatar renders as its shadow (feworld.npc_push, 2026-08-25)
           + struct.pack(">fff", 0.0, 0.0, 1.0)
           + struct.pack(">I", card["m3"]) + card["worn"])
    return struct.pack(">HIB", 1, cid & 0xFFFFFFFF, 0) + rec


def relay_body(peer_mv, my_mv, args=None, offset=None):
    """The peer's heartbeat as an action-0 record for THIS client.

    KEY: MEASURED OFF PROD 2026-09-11, from the clients' own 28-byte records:

        action 0 | A 0 | B = the sender's CLOCK IN MS | state 0
        spd 1.300 | pos | tail 0x00080000

    B's deltas are ~400 between consecutive samples and the heartbeat is
    ~400 ms, so B is milliseconds in that client's own clock -- which is why
    it is the ONE field that must be rewritten: the receiver judges it against
    ITS clock, whose base is its own connect. A is always 0.

    WARNING: AND THE TAIL IS NOT ZERO. Every real record carries 0x00080000, and at
    state 0 the tail is handed to the mover as an argument (0x04feae15 ->
    0x515b870, where the state!=0 path 0x515ba10 passes 0 instead). We forced
    it to 0 because npc_walk_push did -- and live testing showed peers snap
    between positions while the run animation played: "running in place and
    then sometimes kinda blips to a new location". A move that arrives
    instantly is exactly what a zeroed argument to the mover would look like.

    So: relay the sender's own record VERBATIM except B. The client already
    knows how to describe its own movement; every value we substitute is a
    guess competing with a measurement. --presence-move-state and
    --presence-move-tail put the old forced zeros back for an A/B run.
    """
    out = bytearray(peer_mv)
    struct.pack_into(">I", out, fw._MV_ACTION, 0)
    # ---- THE CLOCK. B is the sender's timestamp for this sample, and the two
    # clients' clocks are based on their own connects, so it has to be moved
    # into the receiver's domain. HOW matters:
    #
    #   "now"  stamps the receiver's current clock on every relay. Samples
    #          that were 2.5 s apart then arrive stamped at whatever instant
    #          we forwarded them -- which tells the client each new position
    #          is due RIGHT NOW, i.e. teleport. This was the only mode until
    #          2026-09-11 and it is why smoothing had to be faked server-side.
    #   "map"  adds one fixed offset, so the samples keep their original
    #          spacing in the receiver's clock. That is what every other
    #          networked game hands its client: timestamped snapshots, spaced
    #          as they were taken, for the client's own interpolation to walk
    #          between (entity interpolation -- the receiver renders remote
    #          players slightly in the past and fills in the gaps).
    if offset is None:
        out[fw._MV_A:fw._MV_A + 8] = my_mv[fw._MV_A:fw._MV_A + 8]
    else:
        their_b = struct.unpack_from(">I", peer_mv, fw._MV_A + 4)[0]
        struct.pack_into(">I", out, fw._MV_A, 0)
        struct.pack_into(">I", out, fw._MV_A + 4,
                         (their_b + int(offset)) & 0xFFFFFFFF)
    st = getattr(args, "presence_move_state", "client") if args else "client"
    if st != "client":
        struct.pack_into(">H", out, fw._MV_STATE, int(st) & 0xFFFF)
    tail = getattr(args, "presence_move_tail", "client") if args else "client"
    if tail != "client":
        struct.pack_into(">I", out, fw._MV_TAIL, int(tail, 0) & 0xFFFFFFFF)
    # WARNING: NO SPEED FLOOR. Until 2026-09-11 a zero speed pair was forced to
    # 1.000 "so the avatar is not a statue" -- and the speed pair is exactly
    # what drives the RUN ANIMATION (0x04feadcc writes it before any movement
    # decision). So a peer standing still was served "I am running", forever:
    # the live report "he's just running in place" is partly this. The sender's
    # own record says 0 when stopped and 1.300 when walking; relay it.
    return bytes(out)


def step_toward(cur, target, step):
    """`cur` moved at most `step` units toward `target` (and exactly onto it
    when that is closer)."""
    d = _dist(cur, target)
    if d <= step or d <= 1e-6:
        return tuple(target), d
    f = step / d
    return (cur[0] + (target[0] - cur[0]) * f,
            cur[1] + (target[1] - cur[1]) * f,
            cur[2] + (target[2] - cur[2]) * f), d


def facing_body(cid, direction):
    """A 0x1006 type-0 record carrying NOTHING BUT THE FACING.

    WARNING: 2026-09-11, live: "the character is often facing the wrong direction".
    A peer's facing is written once, at spawn, as (0,0,1) -- and nothing has
    ever updated it since, because the movement record (0x2023) has no facing
    field at all. Whatever the mover does to a unit's heading on a target it
    accepts, it plainly does not leave it pointing where the player is going.

    The type-0 arm applies masks onto an EXISTING unit in place ("Chara %d
    already exists", 0x0503ac46), so mask1 = 0 with only mask2 bit 2 set is a
    facing-only update: [u32 mask1 0][u32 mask2 4][f32 x3][u32 mask3 0].

    WARNING: NEVER all zero -- the client builds its look-at as position + facing
    (0x05078e40), so a zero vector collapses the basis and the avatar renders
    as its own shadow (measured 2026-08-25).
    """
    rec = (struct.pack(">II", 0x0, 0x4) + struct.pack(">fff", *direction)
           + struct.pack(">I", 0))
    return struct.pack(">HIB", 1, cid & 0xFFFFFFFF, 0) + rec


def _unit_vector(frm, to):
    dx, dy, dz = to[0] - frm[0], to[1] - frm[1], to[2] - frm[2]
    n = math.sqrt(dx * dx + dy * dy + dz * dz)
    if n < 1e-6:
        return None
    # the heading is horizontal: a step up a slope should not tip the model
    h = math.sqrt(dx * dx + dz * dz)
    if h < 1e-6:
        return None
    return (dx / h, 0.0, dz / h)


def timed_speed(distance, interval, args):
    """The speed pair to relay so the avatar's walk LASTS `interval`.

    KEY: STATIC, 2026-09-12 (the avatar branch, read at last): the mover stores
    `[ctrl+0xa0] = distance` and `[ctrl+0x10] = ([unit+0x4d8] + [unit+0x4d4])
    * table(0x5158e40)` and then walks the unit through its waypoint queue at
    that speed. The pair is a MULTIPLIER on the engine's own run speed, not
    units per second -- so relaying the sender's own 1.300 makes the avatar
    RUN to a waypoint that took the real player ten seconds to reach, arrive
    in about a second, and stand there. "Moves, then stands, then blips" is
    that, exactly.

    A client reports its position only ~0.1 times a second (measured on prod:
    74 samples in 6 minutes across two players), so the honest fix is not more
    waypoints -- there are none to be had -- but a walk slow enough to still
    be going when the next one lands.

    WARNING: THE SCALE IS A GUESS AND IT IS THE ONE NUMBER TO TUNE. `k` is how many
    units per second one unit of the speed pair buys, and nothing measures it
    from outside the client. --presence-speed-scale, live via `!presence
    speed N`.
    """
    k = float(getattr(args, "presence_speed_scale", 12.0) or 12.0)
    if interval <= 0.05 or k <= 0:
        return None
    # WARNING: STANDING STILL IS A SPEED, AND IT IS ZERO. Returning None here (no
    # opinion) left the PREVIOUS speed on the unit, so the moment a player
    # stopped their avatar inherited whatever it was last walking at and ran
    # on the spot forever -- live report, 2026-09-12: "I stop moving the
    # character and then they begin running in place". The sender's own pair
    # cannot say this either: it is their speed STAT (a constant 1.300),
    # not their velocity.
    if distance <= 0.05:
        return 0.0
    want = (distance / interval) / k          # multiplier, not u/s
    return max(0.05, min(3.0, want))


def paced_body(peer_mv, my_mv, args, pos, offset=None):
    """A relay record with `pos` substituted -- the server's own intermediate
    step toward where the peer actually is.

    WARNING: Paced steps are the server pretending to be the clock, so they keep the
    RECEIVER's own timestamp (offset None by default): a paced step is "be
    here now", which is what it means."""
    out = bytearray(relay_body(peer_mv, my_mv, args, offset))
    struct.pack_into(">hhh", out, fw._MV_POS,
                     fw._i16(pos[0] * 10.0), fw._i16(pos[1] * 10.0),
                     fw._i16(pos[2] * 10.0))
    return bytes(out)


# ---------------------------------------------------------------------------
# THE MINIMAP DOTS -- 0x2080 -> 0x1123, one row per player standing here
#
# The avatar records put players in the WORLD; the minimap is a different
# channel and feworld answers it with COUNT 0 ("the honest answer", because
# what the rows draw was never measured). So two players could stand next to
# each other and the map stayed empty -- reported live 2026-09-11.
#
# The row is measured, read call by call in 0x05010ff0 (feworld's 0x2080 arm
# quotes the addresses): [u8][u8][u8 class|side][u32 charid]. What is CHOSEN
# here is what the first two bytes MEAN: the same 0..255 building grid the
# placer uses, fw.world_to_grid -- which is live-measured (2026-09-05, a
# `!build` landed where the player stood). u8 range fits it exactly.
#
# The third byte is measured as "low 5 bits a class code, top bit the side,
# compared against the player's own nation"; serving the peer's real class and
# setting the top bit for a DIFFERENT nation is the reading, not a measurement.
# WARNING: Only class codes 1,4,5,6,7,8,9 reach an arm at 0x050110cc -- a character
# whose class is outside that set may simply not draw, which is logged rather
# than papered over.
# ---------------------------------------------------------------------------
def _dot(cid, pos, cls, enemy):
    gx, gz = fw.world_to_grid(pos[0]), fw.world_to_grid(pos[2])
    b = (int(cls) & 0x1F) | (0x80 if enemy else 0)
    return struct.pack(">BBBI", gx & 0xFF, gz & 0xFF, b, int(cid) & 0xFFFFFFFF)


def on_distribution(ctx, inner):
    """0x2080 -> 0x1123. Declined (so feworld answers, as before) whenever
    presence is not running, --presence-map is off, the operator switched
    --distribution off, or they are driving --distribution-rows by hand."""
    try:
        args = ctx.args
        if not _active(args) or getattr(args, "presence_map", "on") != "on":
            return False
        if getattr(args, "distribution", "on") == "off":
            return False
        if getattr(args, "distribution_rows", None):
            _once("map-rows", "--distribution-rows is set, so the operator's "
                  "own probe answers 0x2080, not the live player list")
            return False
        s = fw._SESSION
        key = _key(s)
        if key is None:
            return False
        now = time.monotonic()
        mine = int((s.get("pres_card") or {}).get("force") or 0)
        rows = []
        for cid, (card, mv, _n, _t) in sorted(visible_peers(s, key, now, args).items()):
            theirs = int(card.get("force") or 0)
            rows.append(_dot(cid, _pos(mv), card.get("cls", 0),
                             bool(mine and theirs and mine != theirs)))
            _once(("map-class", card.get("cls", 0)),
                  "minimap: serving class code %s; only 1,4,5,6,7,8,9 reach an "
                  "arm at 0x050110cc, so anything else may not draw"
                  % card.get("cls", 0))
        if getattr(args, "presence_map_self", "off") == "on" and s.get("pres_mv"):
            rows.append(_dot(s.get("charid") or 0, _pos(s["pres_mv"]),
                             (s.get("pres_card") or {}).get("cls", 0), False))
        body = struct.pack(">H", len(rows) & 0xFF) + b"".join(rows)
        _send(ctx, 0x1123, body, fw.unit_id_of(args))
        k = int(s.get("pres_map_n", 0) or 0) + 1
        s["pres_map_n"] = k
        if k <= 3 or k % 30 == 0:
            _log("-> 0x1123 %d dot(s) for charid %s in %s (poll #%d)"
                 % (len(rows), s.get("charid"), key, k))
        return True
    except Exception as e:                              # noqa: BLE001
        _once("map-fail", "0x2080 answer failed (%r) -- declined, feworld's "
              "own count-0 answer stands" % (e,))
        return False


# ---------------------------------------------------------------------------
# the pump -- reconcile what this client shows with who is actually here
# ---------------------------------------------------------------------------
def _dead_mode(args):
    """--presence-dead: what a peer's death does to the copy others see.

    'drop' (default) takes the certain route -- the dead peer stops being a
    visible peer, so the reconciler 0x1004s them and re-adds them when they
    stand up. Add/remove is the proven path, so this is guaranteed to LOOK
    right; the cost is that they pop out instead of falling over.

    'condition' keeps them drawn and pushes CONDITION DEAD at their avatar
    (0x2024 maskA 0x100000, the same message family whose HP field already
    draws a damage number over a peer -- fepvp). Retail-looking IF the client
    honours the condition on a kind-2 served avatar, which is UNPROVEN: if it
    does not, the corpse stands up again and you are back to the live bug.

    'off' is the behaviour before 2026-09-12: nobody is told anything."""
    return str(getattr(args, "presence_dead", "drop") or "drop")


def push_peer_dead(ctx, args, seen):
    """--presence-dead condition: tell this client which of its peers are
    down, once per change. `seen` is the per-session peer registry."""
    if _dead_mode(args) != "condition":
        return
    state = fw._SESSION.setdefault("pres_dead", {})
    live = {}
    for ent in fw.ext_sessions():
        ps = ent["session"]
        cid = int(ps.get("charid") or 0)
        if cid and cid in seen:
            live[cid] = bool(ps.get("player_dead"))
    for cid, dead in live.items():
        if state.get(cid) == dead:
            continue
        state[cid] = dead
        base = getattr(args, "unit_state", None)
        v = (base if base is not None else 0) & 0xFFFFFFFF
        v = (v | 0x800000) if dead else (v & ~0x800000)
        fw.send(ctx.conn, ctx.outbound,
                fw.inner_msg(0x2024, struct.pack(">III", 0x100000, 0, v), cid),
                ctx.mode, ctx.be, args.seq_mode == "echo",
                getattr(args, "world_prefix", 4))
        _log("-> 0x2024 CONDITION 0x%08X on peer %d (%s) -- UNPROVEN on a "
             "served kind-2 avatar" % (v, cid, "DEAD" if dead else "alive"))
    for cid in [c for c in state if c not in live]:
        state.pop(cid, None)


def visible_peers(s, key, now, args):
    """charid -> (card, heartbeat, heartbeat count, heartbeat time) for every
    peer this client should be drawing right now, SNAPSHOT once: the dicts
    belong to other threads and change under us, so each value is read one
    time and used from here. The freshest session wins a doubled charid (a
    reconnect whose old socket has not timed out yet)."""
    me = threading.get_ident()
    my_cid = int(s.get("charid") or 0)
    limit = int(getattr(args, "monster_base", 400) or 400)
    stale = float(getattr(args, "presence_stale", 10.0))
    best = {}
    for ent in fw.ext_sessions():
        if ent["key"] == me:
            continue
        ps = ent["session"]
        if _key(ps) != key:
            continue
        card, mv = ps.get("pres_card"), ps.get("pres_mv")
        n, t = ps.get("pres_mv_n", 0), float(ps.get("pres_mv_t", -1e18))
        if not card or card.get("key") != key or mv is None:
            continue
        # LIVENESS IS ANY INBOUND MESSAGE, not the movement heartbeat: a
        # client standing still in a window still polls (0x2084 every second,
        # 0x2080 every ten) but may not report movement for ~20 s. Keyed on
        # the heartbeat alone, every peer flickered -- live 2026-09-11.
        alive = float(ps.get("pres_alive_t", t))
        if now - max(alive, t) > stale:
            continue
        cid = int(ps.get("charid") or 0)
        if cid == my_cid:
            continue
        # WARNING: LIVE 2026-09-12: Quinn killed Lex. Lex's own client showed them
        # dead; Quinn's client kept drawing Lex standing, walking on the last
        # heartbeat they ever sent. Nothing here had ever looked at
        # `player_dead` -- the victim's death was applied on the VICTIM's
        # thread (fepvp.on_hit_relay -> player_dead_push, addressed to their
        # own unit) and no peer was told anything at all. So the one thing a
        # PvP kill has to produce -- the other player falling over -- was the
        # one thing it did not.
        if ps.get("player_dead") and _dead_mode(args) == "drop":
            continue
        if not 0 < cid < limit:
            _once(("range", cid), "charid %d is outside 1..%d (--monster-base "
                  "starts the server's own object ids) -- that player is NOT "
                  "shown to anyone" % (cid, limit - 1))
            continue
        if cid in best and best[cid][3] >= t:
            continue
        best[cid] = (card, mv, n, t)
    return best


def pump(ctx, now=None):
    args = ctx.args
    if not _active(args):
        return
    now = time.monotonic() if now is None else now
    s = fw._SESSION
    # this pump runs once per inbound message on this session's own thread, so
    # it IS the "this player's client is still talking to us" clock
    s["pres_alive_t"] = now
    key = _key(s)
    if key is None:
        if s.get("pres_seen"):
            _log("charid %s left the field -- forgetting %d peer(s); the "
                 "client dropped them with the scene"
                 % (s.get("charid"), len(s.get("pres_seen"))))
        s["pres_seen"] = {}
        s["pres_at"] = None
        return
    if s.get("pres_at") != key or s.get("pres_epoch") != _EPOCH[0]:
        # a new field (or room): the scene is new, nothing we spawned survived.
        # An epoch bump (`!presence hp`) takes the same path -- the records go
        # out again as in-place updates.
        s["pres_seen"] = {}
        s["pres_at"] = key
        s["pres_epoch"] = _EPOCH[0]
        s["pres_card"] = build_card(args, key)
        if s.get("pres_card") is None:
            _log("charid %s: no stored character -- the others will not see "
                 "this player" % (s.get("charid"),))
    mine = s.get("pres_mv")
    if mine is None:
        return
    seen = s["pres_seen"]
    want = visible_peers(s, key, now, args)

    for cid in [c for c in seen if c not in want]:
        fw.mob_kill_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, cid)
        del seen[cid]
        _log("-> 0x1004 MSG_DEL peer %d (gone from %s) to charid %s"
             % (cid, key, s.get("charid")))
    push_peer_dead(ctx, args, seen)

    for cid in sorted(want):
        card, mv, n, _t = want[cid]
        pos = _pos(mv)
        st = seen.get(cid)
        paced = getattr(args, "presence_interp", "on") == "on"
        off = (clock_offset(st, mv, mine)
               if st is not None
               and getattr(args, "presence_clock", "map") in ("map", "arrival")
               else None)
        # WARNING: A PACED TICK MUST RUN WITH NO NEW REPORT -- that is the whole
        # point of it: the peer's last two reports are played out over the
        # ticks BETWEEN them. This early-out (right for the raw relay, which
        # has nothing to say until a report lands) silently disabled pacing
        # altogether the first time it shipped.
        if st is not None and n == st["n"] and not paced:
            continue
        if st is not None and _dist(pos, st["pos"]) > TELEPORT_UNITS:
            fw.mob_kill_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, cid)
            _log("peer %d jumped %.0f units -- re-added, not walked"
                 % (cid, _dist(pos, st["pos"])))
            st = None
        if st is None:
            _send(ctx, 0x1006, avatar_body(cid, card, pos), cid)
            # p0/p1 seed the interpolation segment HERE: without them the
            # first real report becomes both ends of the segment, which
            # collapses it and jumps straight to the new position.
            seen[cid] = {"n": n, "pos": pos, "sent": None, "sent_pos": pos,
                         "sent_t": now, "held": 0,
                         "p0": pos, "p1": pos, "t0": now, "t1": now}
            _log("-> 0x1006 MSG_ADD peer %d %r at (%.1f, %.1f, %.1f) to charid "
                 "%s in %s" % (cid, card["name"], pos[0], pos[1], pos[2],
                               s.get("charid"), key))
            continue
        if _relay_mode(args) == "raw":
            # the peer's own records walk the avatar (on_move_relay, this
            # same thread); the pump's job is who is on the screen at all
            st["n"] = n
            continue
        # ---- PACED MODE: walk our own idea of where the peer is toward where
        # they say they are, in steps that clear the mover's 1.0u discard.
        #
        # KEY: THE TWO MEASUREMENTS THIS IS BUILT ON (prod, 2026-09-11):
        # a client reports its position only every ~2.5 s (150 records in 8
        # minutes across two sessions), and an accepted target is applied as a
        # SNAP. Relaying raw therefore gives one 3-6 unit jump every few
        # seconds -- "he doesn't move for a long time, then blips over".
        # Stepping at the peer's own measured speed turns that into a series
        # of small hops at the right average pace, which is the best a mover
        # that does not interpolate can be driven to do.
        if paced:
            # ENTITY INTERPOLATION: draw the peer at (now - lag), walking the
            # segment between their last TWO reports. Deliberately behind real
            # time, because that is what buys continuous motion out of reports
            # that arrive ~2.5 s apart -- extrapolating ahead would overshoot
            # every time they stopped, and jumping straight to the newest
            # report is the 3-6 unit blip observed in live testing.
            if n != st["n"]:
                # WARNING: THE SEGMENT STARTS WHERE WE ACTUALLY DREW THEM, not at
                # their previous report. Starting from the report meant that
                # every time one arrived the avatar was yanked forward to
                # wherever the last segment had begun -- a visible correction
                # per report, which live testing showed as "constantly
                # trying to correct itself". From the drawn position the path
                # is continuous by construction; the cost is being permanently
                # about one report behind, which is the price of smoothness.
                gap_t = now - float(st.get("rep_t", now))
                if 0.2 <= gap_t <= 6.0:       # a plausible report interval
                    old = float(st.get("span", gap_t))
                    st["span"] = old * 0.5 + gap_t * 0.5      # smoothed
                st["rep_t"], st["n"] = now, n
                st["p0"] = st.get("sent_pos") or pos
                st["p1"], st["t0"] = pos, now
                lag_ms = float(getattr(args, "presence_lag_ms", 0) or 0)
                st["t1"] = now + ((lag_ms / 1000.0) if lag_ms > 0
                                  else min(4.0, max(0.5, float(st.get("span", 2.0)))))
            p0, p1 = st.get("p0"), st.get("p1")
            if p1 is None:
                st["p0"], st["p1"], st["t0"], st["t1"] = pos, pos, now, now
                continue
            span = max(1e-3, float(st["t1"]) - float(st["t0"]))
            f = min(1.0, max(0.0, (now - float(st["t0"])) / span))
            want = tuple(p0[i] + (p1[i] - p0[i]) * f for i in range(3))
            body = relay_body(mv, mine, args, off)
            sig = body[fw._MV_SPD:fw._MV_TAIL]
            step_min = float(getattr(args, "presence_step_min", 1.05) or 0)
            gap = _dist(want, st.get("sent_pos", want))
            # a step only counts if it clears the mover's discard; a changed
            # speed pair (they started or stopped) always goes, so the run
            # animation starts and stops on time
            if gap < step_min and sig == st.get("sent"):
                continue
            if getattr(args, "presence_face", "on") == "on":
                face = _unit_vector(st.get("sent_pos", want), want)
                if face and face != st.get("face"):
                    _send(ctx, 0x1006, facing_body(cid, face), cid)
                    st["face"] = face
            _send(ctx, 0x2023, paced_body(mv, mine, args, want), cid)
            st["sent"], st["sent_pos"], st["sent_t"] = sig, want, now
            k = int(s.get("pres_relay_n", 0) or 0) + 1
            s["pres_relay_n"] = k
            if k <= 5 or k % 200 == 0:
                _log("-> 0x2023 peer %d #%d to (%.1f, %.1f, %.1f) -- %.0f%% "
                     "along their last %.1fu over %.1fs, %.1fu step"
                     % (cid, k, want[0], want[1], want[2], f * 100,
                        _dist(p0, p1), span, gap))
            continue

        st["n"] = n
        body = relay_body(mv, mine, args, off)
        # TIME THE WALK TO THE REPORT INTERVAL (see timed_speed): the avatar
        # should still be walking when the next waypoint lands, not parked.
        if getattr(args, "presence_speed", "auto") == "auto":
            gap_t = now - float(st.get("rep_t", now))
            spd = timed_speed(_dist(pos, st.get("pos", pos)), gap_t, args)
            if spd is not None:
                body = bytearray(body)
                struct.pack_into(">hh", body, fw._MV_SPD,
                                 fw._i16(spd * 1000.0), 0)
                body = bytes(body)
                k = int(s.get("pres_speed_n", 0) or 0) + 1
                s["pres_speed_n"] = k
                if k <= 5 or k % 100 == 0:
                    _log("peer %d: %.1fu in %.1fs -> speed %.2f (they reported "
                         "%.2f; the pair is a RUN MULTIPLIER, so theirs would "
                         "sprint and park)"
                         % (cid, _dist(pos, st.get("pos", pos)), gap_t, spd,
                            struct.unpack_from(">h", mv, fw._MV_SPD)[0] / 1000.0))
        st["rep_t"] = now
        sig = body[fw._MV_SPD:fw._MV_TAIL]
        if sig == st["sent"]:
            st["pos"] = pos
            continue
        # WARNING: HOLD A SUB-UNIT STEP. The mover discards a target under 1.0 units
        # from where the unit already stands, but writes the speed pair first
        # -- so relaying every 0.4 s sample animates a run and travels nowhere
        # ("mostly running in place", live 2026-09-11). Coalesce until the peer
        # is --presence-move-min away, then send ONE target that clears it.
        moved = _dist(pos, st.get("sent_pos", pos))
        far = moved >= float(getattr(args, "presence_move_min", 1.5) or 0)
        settled = (st.get("held") and pos == st.get("pos")
                   and (now - st.get("sent_t", 0)) * 1000.0
                   >= float(getattr(args, "presence_settle_ms", 700.0) or 0))
        st["pos"] = pos
        if not far and not settled:
            st["held"] = int(st.get("held", 0)) + 1
            continue
        _send(ctx, 0x2023, body, cid)
        st["sent"], st["sent_pos"], st["sent_t"] = sig, pos, now
        held, st["held"] = int(st.get("held", 0)), 0
        k = int(s.get("pres_relay_n", 0) or 0) + 1
        s["pres_relay_n"] = k
        if k <= 5 or k % 100 == 0:
            _log("-> 0x2023 action 0 peer %d #%d to (%.1f, %.1f, %.1f) -- "
                 "%.1fu since the last target%s (the mover discards anything "
                 "under 1.0u)"
                 % (cid, k, pos[0], pos[1], pos[2], moved,
                    ", %d sample(s) held" % held if held else ""))


# ---------------------------------------------------------------------------
# !presence -- what this session shows, and who the server thinks is where
# ---------------------------------------------------------------------------
def gm(ctx, line):
    if not line.startswith("!presence"):
        return False
    words = line.split()
    # `!presence move state 1|client`, `!presence move tail 0|client`,
    # `!presence move min 1.5`, `!presence move step 1.2`, `!presence move
    # interp on|off` -- the movement A/B, live, because every one of these is
    # a guess about a mover nobody has disassembled end to end.
    if len(words) > 2 and words[1].lower() == "speed":
        # `!presence speed 2.5` -- THE tuning number for peer movement
        try:
            ctx.args.presence_speed_scale = float(words[2])
        except ValueError:
            ctx.args.presence_speed = ("client" if words[2].lower() == "client"
                                       else "auto")
            _log("!presence speed = %s" % ctx.args.presence_speed)
            return True
        _log("!presence speed scale = %.2f units/sec per unit of the speed "
             "pair (lower = peers walk faster)"
             % ctx.args.presence_speed_scale)
        return True
    if len(words) > 2 and words[1].lower() == "relay":
        # `!presence relay raw|paced` -- the whole movement path, live
        val = words[2].lower()
        ctx.args.presence_relay = "paced" if val == "paced" else "raw"
        _EPOCH[0] += 1
        _log("!presence relay = %s (server-wide; peers re-stated)"
             % ctx.args.presence_relay)
        return True
    if len(words) > 2 and words[1].lower() == "casts":
        ctx.args.presence_relay_casts = "off" if words[2].lower() == "off" else "on"
        _log("!presence casts = %s" % ctx.args.presence_relay_casts)
        return True
    if len(words) > 2 and words[1].lower() == "actions":
        val = words[2].lower()
        ctx.args.presence_relay_actions = "move" if val == "move" else "all"
        _log("!presence actions = %s" % ctx.args.presence_relay_actions)
        return True
    if len(words) > 3 and words[1].lower() == "move":
        what, val = words[2].lower(), words[3]
        knob = {"state": "presence_move_state", "tail": "presence_move_tail",
                "min": "presence_move_min", "step": "presence_step_min",
                "interp": "presence_interp"}.get(what)
        if knob is None:
            _log("!presence move state|tail|min|step|interp VALUE")
            return True
        try:
            setattr(ctx.args, knob,
                    float(val) if what in ("min", "step") else val)
        except ValueError:
            _log("!presence move %s: %r is not a number" % (what, val))
            return True
        _EPOCH[0] += 1
        _log("!presence move %s = %s (server-wide; peers re-stated)"
             % (what, getattr(ctx.args, knob)))
        return True
    if len(words) > 2 and words[1].lower() in ("hp", "gear", "map", "mapself",
                                               "face", "clock"):
        knob = "presence_" + {"mapself": "map_self"}.get(words[1].lower(),
                                                         words[1].lower())
        val = words[2].lower()
        if words[1].lower() == "clock":          # map | now, not on | off
            setattr(ctx.args, knob,
                    val if val in ("now", "map", "arrival") else "arrival")
        else:
            setattr(ctx.args, knob, "on" if val == "on" else "off")
        _EPOCH[0] += 1
        _log("!presence %s %s -- every session rebuilds its card and re-states "
             "the peers it shows (in-place 0x1006 updates, no delete)"
             % (words[1].lower(), getattr(ctx.args, knob)))
        return True
    s = fw._SESSION
    now = time.monotonic()
    _log("!presence: me charid=%s at %s, unit-id=%s, showing %s "
         "(map %s, self %s, hp %s, gear %s)"
         % (s.get("charid"), _key(s), getattr(ctx.args, "unit_id", None),
            sorted(s.get("pres_seen") or {}),
            getattr(ctx.args, "presence_map", "on"),
            getattr(ctx.args, "presence_map_self", "off"),
            getattr(ctx.args, "presence_hp", "off"),
            getattr(ctx.args, "presence_gear", "off")))
    _log("   verbs: !presence, !presence relay raw|paced, !presence actions "
         "all|move, !presence clock arrival|map|now, !presence "
         "map|mapself|hp|gear on|off  (relay %s, actions %s, clock %s, raw "
         "sent %s early %s)"
         % (_relay_mode(ctx.args),
            getattr(ctx.args, "presence_relay_actions", "all"),
            getattr(ctx.args, "presence_clock", "arrival"),
            s.get("pres_raw_n", 0), s.get("pres_raw_early", 0)))
    for ent in fw.ext_sessions():
        ps = ent["session"]
        age = now - float(ps.get("pres_mv_t", -1e18))
        _log("  session %s charid=%s at %s card=%s heartbeat %s"
             % (ent["name"] or "?", ps.get("charid"), _key(ps),
                bool(ps.get("pres_card")),
                ("%.1fs ago" % age) if age < 1e17 else "never"))
    return True
