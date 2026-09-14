"""fetrade.py -- Fantasy Earth PLAYER-TO-PLAYER TRADE (the "Group 4" message ids).

PARTIAL: BUILT 2026-09-10, NOT LIVE-TESTED. Nothing here has been seen on a screen;
every claim below is a static reading of the runtime dump of FE_Client.dll
(base 0x04F90000) plus the fe_trade_test.py harness.

WHAT THE CLIENT DOES (measured, VA per step)
--------------------------------------------
The trade UI is three windows, and TWO of them are popups that exist only
because a request was made. That shapes everything the server can do:

  requester popup   0x050b40d0..0x050b4440   +0x7c = target unit id, +0x78 = tid,
                                             +0x84 = 2 sent / 0 OK / 1 NG
  permission popup  0x050b45b0 / 0x050b4a40  +0xbc = tid; created by the
                                             inbound 0x2052 ARM (0x50b3d30)
  trade window      0x050b1c40 ctor, 0x5b6c bytes, handler 0x050b3910
                    +0x70 state, +0x5b68 tid, +0x5b60 SlotCnt (written, NEVER
                    read), +0x5b6a "partner offer shown" flag, +0x11c the
                    9-row entry list (0x50b3d90 loops `cmp esi, 9`), +0xe0/+0xe4
                    the two "Input" numeric boxes (x=0x100 / x=0x120),
                    +0xec/+0xf0 the partner's two numbers (same x)

Every reply is posted through 0x509fc50 ("MsgType->0x%X"), which walks the
listener list at [0x53432d8] -- a window that is not open is simply not in
the list, so the message is DROPPED SILENTLY. Three of them go further and
read the STREAM inside the window (0x1090, 0x2061 and 0x1176 via a global):
with the window closed those bytes stay on the stream and everything after
them decodes as garbage. Those sends are logged as such here.

OUTBOUND (client -> server), read off `push id; call 0x50459c0` builders:
  0x2052 MSG_TRADE_REQUEST            [u32 target unit id]           0x050b4090
  0x2053 MSG_TRADE_REQUEST_PERMISSION [u16 tid]                      0x050b47f0
         (logs "< MSG_TRADE_REQUEST_PERMISSION" 0x52e0fd8; the ACCEPT button)
  0x2057 MSG_TRADE_REQUEST_CANCEL     [u16 tid][u32 reason]  0x050b40fa (requester
         popup, +0x78) and 0x050b49da (permission popup, +0xbc = DECLINE)
  0x2054 TRADE ENTRY (name invented: no MSG_ string; its NGs are
         MSG_TRADE_ENTRY_NG)          [u16 tid][u32 A][u32 B][u16 n]
                                      n x {[u32 uid][u16 count]}     0x050b374c
         sent when +0x70 == 0xc (the ENTRY button, 0x050b2d93), then 0x70 = 0xd.
         A = atoi(box +0xe0), B = atoi(box +0xe4) (0x5082960 -> 0x51b777a).
         uid/count per row from 0x50b96f0 / 0x50b9730 over the +0x11c list.
  0x2055 TRADE COMPLETE request       [u16 tid]                      0x050b3809
         sent when +0x70 == 0xf (the CONFIRM button, 0x050b2de6), then 0x10.
  0x2056 MSG_TRADE_CANCEL             [u16 tid][u32 reason]          0x050b2eda
         (trade window CANCEL button). ALSO sent by the inbound-0x2052 arm
         itself at 0x05053b5d with reason 7 when the requester's id is on the
         blacklist (0x5170e90 on [unit+0x3c0]) OR the option byte
         [0x5166490()+0x4e] is ZERO -- an auto-decline the server must treat
         like a decline.
  0x2059 -- NOT TRADE. Empty body, sent from the stat arm 0x05051f1d (called
         from 0x05051e62) at 0x05052128 when a stat written to
         [unit + idx*4 + 0x9f8] reaches [unit+0x135c] and (unit+0x3b4 & 0x3f)
         == 0 -- reads like the class LEVEL-UP request (0x1091 is
         MSG_CLASS_LEVEL_UP_OK). Logged only here; not answered.

INBOUND (server -> client), the typed-read sequence per arm, then the window:
  0x108B MSG_TRADE_REQUEST_OK   arm 0x050535ca  u32, u16
         -> event ctor 0x50b19e0: [ev+8]=u32, [ev+0xc]=u16. Requester popup
         0x050b4346: u32 MUST equal +0x7c (the target it sent) or the event
         is ignored; u16 -> +0x78 (tid); +0x84 = 0.  WINDOW-GATED (popup).
  0x108C MSG_TRADE_REQUEST_NG   arm 0x050536ff  u32 code -> NG table, +0x84=1
  0x108D "> MSG_TRADE_START TradeID=%d SlotCnt" (0x52d578c)
                                arm 0x05053748  u16 TradeID, u16 SlotCnt
         -> ctor 0x50b1a10: [ev+8]=TradeID, [ev+0xa]=SlotCnt. EITHER popup
         (0x050b4378 / 0x050b4a9d) allocates the 0x5b6c trade window
         (0x50b1c40) and closes itself; the new window then takes the same
         event at 0x050b3a93 (+0x5b68=tid, +0x5b60=SlotCnt, show, state 1).
         WINDOW-GATED: no popup, no trade window, ever.
  0x101F -- NOT TRADE_START (the 08-27 sweep's label was wrong): arm
         0x0505353e is header-only and POSTS; xref finds NO window switching
         on 0x101F. A dead-letter post. Not sent.
  0x108E MSG_TRADE_ENTRY_OK     arm 0x050537c0  header-only, post
         -> trade window 0x050b3a16: button +0xd0 off, +0xd4 (CONFIRM) on,
         both lists locked, state 0xd -> 0xe.
  0x108F MSG_TRADE_ENTRY_NG     arm 0x050537e6  u16, u32
         the u16 is read and DROPPED by the arm; u32 -> [ev+8] -> NG table
         (0x50613e0) in the window 0x050b394e; lists unlocked, state -> 1.
  0x1090 THE PARTNER'S OFFER (name invented; the sweep called it "trade
         complete", it is not). arm 0x05053830 is header-only + post, but the
         trade window 0x050b3ac2 then READS THE STREAM ITSELF:
             u16 tid   -- must equal +0x5b68 or the rest is NOT read (desync)
             u32 A, u32 B, u16 n, then n x item_fields (0x5075980 / 0x5075a80
             / 0x5075ad0 / 0x5075b80 = the SAME four group readers 0x0503d280
             uses for 0x107A, no uid) into +0x2e40..; count from [item+0x4a8];
             rows built by 0x50b68a0(kind 0x23); A -> widget +0xec, B ->
             +0xf0 ("%d"); +0x5b6a = 1.
         WARNING: WINDOW-GATED STREAM READ: sent with the window closed, the whole
         body stays on the stream.
  0x2055 TRADE COMPLETE notice  arm 0x05053baa  header-only: prints
         'Trade complete.' (0x52d572c, kind 3 chat log) and posts; trade
         window 0x050b3ceb: close (0x508d9f0), state 0x11. THE ONLY ONE OF
         THE THREE THAT PRINTS THE STRING.
  0x2056 / 0x2057 (inbound)     arms 0x05053c79 / 0x05053bfc  u16, u32
         0x2057's arm logs the NG-table row for its u32 (MSG_TRADE_REQUEST_
         CANCEL); both post. Trade window 0x050b3cdc: 'Partner cancelled the
         trade' (0x52e0f14), close, state 0x11. Both popups: log only.
  0x2058                        arm 0x05053c53  header-only, post
         -> trade window 0x050b3d03: +0x5b6a = 0 (partner's offer withdrawn).
         No PC-client builder pushes 0x2058; nothing maps to it. Probe only.
  0x1145 MSG_TRADE_ENTRY_CLEAR_NG / 0x1147 MSG_TRADE_COMPLETE_NG
         arms 0x05053856 / 0x0505388d  u16, u32 -> NG table, NO post.
         0x1147 answers 0x2055; 0x1145 answers a request the PC client never
         builds (probe only).
  0x1176 -- NOT TRADE. arm 0x05053a64: IF [0x5344f14] (the Progress.tex bar
         built at 0x0510dc80) exists, f32 -> [bar+0x40], f32 -> [bar+0x44];
         then post. With no bar it reads NOTHING (a body would desync). Not
         sent.
  0x2061 -- NOT TRADE. arm 0x05053fd4 header-only + post; the field HUD
         0x05117e00 (0x0511860f) then reads [u16 n]{u32}*n off the stream IF
         a player unit exists and prints 'Army info...' (0x52e6274). Not sent.

WHAT THIS MODULE DOES
---------------------
A trade record per tid (u16, from 1) with two parties. A party is a live
SESSION (messages cross threads through fw.ext_post -> relay "trade", store
writes happen on the OWNING thread) or a PHANTOM driven by `!trade` verbs so
one client can walk the whole machine.

  0x2052 -> 0x108B/0x108C; the target's thread sends its client 0x2052
            [u32 face][u16 tid] (face = the unit in THAT client that fronts
            the requester: --trade-face, default --npc-base; feworld spawns
            no unit for other players, so a real face has to be an NPC)
  0x2053 -> 0x108D to both (SlotCnt = --trade-slots)
  0x2057 -> drop; 0x2057 [tid][reason] to the other side
  0x2054 -> validate -> 0x108E or 0x108F [tid][code]; 0x1090 to the partner
  0x2055 -> when both sides confirmed: validate, APPLY on each side's own
            thread (rows out of one store, into the other, A=gold B=crystal),
            then 0x2055 + the 0x107A burst (bag_layout_push) + wallet_push
  0x2056 -> drop; 0x2056 [tid][reason] to the other side

Chosen, not measured: A = gold, B = crystal (the NG texts name both
currencies, the box ORDER is not labelled anywhere in the ctor);
--trade-slots 9 (the entry list's own bound); SlotCnt is never read by the
PC client anyway. The two-sided apply is NOT atomic: the partner applies
first and reports back; if the local re-check then fails, the partner keeps
its half and the log says so in red.

VERIFIED: 2026-09-11: CLICK-TO-TRADE NEEDS NO BIND ANY MORE (under --unit-id auto).
fepresence draws every peer under its CHARID, so the clicked unit id resolves
straight to that player's session (_session_by_charid), and the face on the
partner's inbound 0x2052 is the requester's own avatar rather than a stand-in
NPC (_peer_face). `!trade bind` stays for the one-client probe and for a
fixed --unit-id, where every session's own unit is the same number and an id
cannot name a player. The item table's "can't be traded" flag (0x108F code 9)
is still not read (column unidentified).
"""
import struct
import threading

fw = None                       # the feworld module, handed in by register()

_LOCK = threading.RLock()
TRADES = {}                     # tid -> Trade
_NEXT = [1]                     # tid 0 would match a fresh window's +0x5b68

# The client's own names (NG table, dumped 2026-09-04) for the
# codes this module sends. Anything else is a bare number.
NG_REQUEST = {1: "cannot trade with yourself", 5: "character object does not exist",
              7: "Busy now; can't apply", 8: "Partner is busy; can't apply",
              10: "in an item trade", 11: "already being asked by another player",
              12: "trading with another player"}
NG_ENTRY = {4: "partner's item slots are full", 5: "invalid item id",
            7: "you do not hold that item", 8: "This item is equipped",
            10: "not that many held", 11: "already registered",
            12: "the partner is not registered", 14: "partner's crystal over limit"}
NG_COMPLETE = {0: "undefined error", 1: "missing parameter", 2: "invalid trade id"}
NG_CANCEL = {1: "trade object does not exist", 5: "invalid trade id",
             7: "Busy now; can't apply (the client's own auto-decline)",
             8: "offer object does not exist"}
NG_REQCANCEL = {1: "trade object does not exist",
                2: "partner cancelled the request"}


class Party(object):
    __slots__ = ("kind", "name", "session", "face", "offer", "entered",
                 "confirmed", "charid")

    def __init__(self, kind, name, session=None, face=0, charid=None):
        self.kind, self.name, self.session = kind, name, session
        self.face = face                  # unit id THIS party is seen as
        self.offer = {"items": [], "a": 0, "b": 0}
        self.entered = False
        self.confirmed = False
        self.charid = charid

    def is_me(self):
        return self.kind == "session" and self.session is fw._TLS.session


class Trade(object):
    __slots__ = ("tid", "requester", "target", "state")

    def __init__(self, tid, requester, target):
        self.tid, self.requester, self.target = tid, requester, target
        self.state = "requested"         # -> open -> done | cancelled

    def parties(self):
        return (self.requester, self.target)

    def mine(self):
        for p in self.parties():
            if p.is_me():
                return p
        return None

    def other(self, p):
        return self.target if p is self.requester else self.requester


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------
def register(feworld):
    global fw
    fw = feworld
    fw.register_handler(0x2052, on_request)
    fw.register_handler(0x2053, on_permission)
    fw.register_handler(0x2054, on_entry)
    fw.register_handler(0x2055, on_complete)
    fw.register_handler(0x2056, on_cancel)
    fw.register_handler(0x2057, on_request_cancel)
    # 0x2059 is NOT registered here (2026-09-10 integration): feprog.py owns
    # it (since 2026-09-11) as MSG_CLASS_LEVEL_UP_REQUEST (--class-levelup);
    # on_2059 below is kept as the record of what this module measured.
    fw.register_gm(gm)
    fw.register_args(add_args)
    fw.register_relay("trade", on_relay)
    for mid, name in (
            (0x2052, "MSG_TRADE_REQUEST [u32 target unit] (0x050b4090) -> 0x108B/0x108C"),
            (0x2053, "MSG_TRADE_REQUEST_PERMISSION [u16 tid] (0x050b47f0) -> 0x108D"),
            (0x2054, "TRADE ENTRY [u16 tid][u32 A][u32 B][u16 n]{u32 uid,u16 count} (0x050b374c) -> 0x108E/0x108F"),
            (0x2055, "TRADE COMPLETE request [u16 tid] (0x050b3809) -> 0x2055/0x1147"),
            (0x2056, "MSG_TRADE_CANCEL [u16 tid][u32 reason] (0x050b2eda / auto 0x05053b5d)"),
            (0x2057, "MSG_TRADE_REQUEST_CANCEL [u16 tid][u32 reason] (0x050b40fa / 0x050b49da)"),
            (0x2059, "NOT trade: empty request from the stat arm 0x05051f1d when a stat reaches [unit+0x135c]"),
            (0x108B, "MSG_TRADE_REQUEST_OK [u32 target][u16 tid] (popup-gated)"),
            (0x108D, "> MSG_TRADE_START [u16 tid][u16 SlotCnt] (popup-gated; opens the trade window)"),
            (0x1090, "PARTNER'S OFFER [u16 tid][u32 A][u32 B][u16 n]{item_fields} -- WINDOW-GATED STREAM READ"),
            (0x2058, "partner offer withdrawn (window +0x5b6a=0); no client builder")):
        fw.KNOWN.setdefault(mid, name)


def add_args(ap):
    ap.add_argument("--trade", default="on", choices=("on", "off"),
                    help="answer the trade messages (0x2052..0x2057). PARTIAL: "
                         "BUILT 2026-09-10, not live-tested. `off` answers "
                         "nothing, which is what prod did before this module "
                         "(the requester popup then sits at state 2 forever).")
    ap.add_argument("--trade-phantom", default="off", choices=("on", "off"),
                    help="a 0x2052 aimed at a unit that is not bound to a "
                         "live session is answered by a PHANTOM counterparty "
                         "driven by `!trade accept/put/confirm/cancel`. "
                         "Default off: such a request gets 0x108C code 5.")
    ap.add_argument("--trade-phantom-name", default="Phantom",
                    help="the phantom's server-side name (the client shows "
                         "the FACE unit's own [unit+0x389] name, not this)")
    ap.add_argument("--trade-face", type=lambda s: int(s, 0), default=None,
                    help="unit id in THIS client that fronts an incoming "
                         "request (the inbound 0x2052's u32; the arm looks it "
                         "up as class 3 and reads its name). Default: "
                         "--npc-base, i.e. the first --npc. With no unit "
                         "there is nothing to front the popup and the "
                         "request is refused with 0x108C code 8.")
    ap.add_argument("--trade-slots", type=int, default=9,
                    help="SlotCnt in 0x108D. The PC client stores it at "
                         "+0x5b60 and never reads it; its entry list holds 9.")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _on(args):
    return getattr(args, "trade", "on") != "off"


def _log(msg):
    print("[fetrade] " + msg, flush=True)


def _face_for(args):
    face = getattr(args, "trade_face", None)
    if face is None and getattr(args, "npc", None):
        face = int(getattr(args, "npc_base", 0) or 0)
    return int(face or 0)


def _binds():
    return fw._SESSION.setdefault("trade_bind", {})


def _my_trade():
    tid = fw._SESSION.get("trade")
    return TRADES.get(tid) if tid else None


def _session_by_charid(cid):
    """The live OTHER session playing charid `cid`, or None.

    2026-09-11: with fepresence on, the unit a player CLICKS to trade is that
    player's own avatar, whose object id IS their charid -- so a request needs
    no `!trade bind` any more. Only meaningful under --unit-id auto: with a
    fixed id every session's own unit is the same number and this would match
    whoever happens to hold it (feparty's 0x112E does the same check).
    """
    me = fw._TLS.session
    for ent in fw.ext_sessions():
        if ent["session"] is me:
            continue
        c = ent["session"].get("charid")
        if c is not None and (int(c) & 0xFFFFFFFF) == (int(cid) & 0xFFFFFFFF):
            return ent
    return None


def _peer_face(ctx, charid):
    """The unit id that fronts `charid` in THIS client: their own avatar when
    fepresence has drawn it (it owns the session's `pres_seen`, keyed by
    charid), else --trade-face/--npc-base -- an NPC standing in for them."""
    try:
        drawn = charid and int(charid) in (ctx.session.get("pres_seen") or {})
    except (TypeError, ValueError):
        drawn = False
    return int(charid) if drawn else _face_for(ctx.args)


def _session_named(name):
    """A live OTHER session whose chat name matches (case folded, the way
    chat_relay matches /tell targets)."""
    me = fw._TLS.session
    for ent in fw.ext_sessions():
        if ent["session"] is me:
            continue
        if ent["name"] == name or ent["name"].casefold() == name.casefold():
            return ent
    return None


def _u16(f, o):
    return struct.unpack_from(">H", f, o)[0] if len(f) >= o + 2 else None


def _u32(f, o):
    return struct.unpack_from(">I", f, o)[0] if len(f) >= o + 4 else None


def _ng(ctx, mid, tid, code, table, name):
    """[u16 tid][u32 code] -- the shape of 0x108F/0x1145/0x1147 and of the
    inbound 0x2056/0x2057 (all `u16, u32` per the read sequence)."""
    ctx.reply(mid, struct.pack(">HI", tid & 0xFFFF, code & 0xFFFFFFFF),
              why="%s tid=%d code=%d (%s)" % (name, tid, code,
                                              table.get(code, "?")))


def _offer_body(tid, offer):
    """0x1090 as the trade window reads it at 0x050b3ac2 (see the docstring):
    [u16 tid][u32 A][u32 B][u16 n] then n x item_fields(no, count, slot=i)."""
    items = offer["items"]
    body = struct.pack(">HIIH", tid & 0xFFFF, offer["a"] & 0xFFFFFFFF,
                       offer["b"] & 0xFFFFFFFF, len(items))
    for i, (_uid, no, count) in enumerate(items):
        body += fw.item_fields(no, count, i, None, 0)
    return body


def _send_offer(ctx, tid, offer, whose):
    body = _offer_body(tid, offer)
    ctx.reply(0x1090, body, why="PARTNER'S OFFER from %s: %d item row(s) A=%d "
              "B=%d" % (whose, len(offer["items"]), offer["a"], offer["b"]))
    _log("   0x1090 is a WINDOW-GATED STREAM READ (0x050b3ac2): if this "
         "client's trade window is not open on tid %d the %d body bytes stay "
         "on the stream and everything after them desynchronises" % (tid, len(body)))


def _send_start(ctx, tid):
    slots = int(getattr(ctx.args, "trade_slots", 9) or 9)
    ctx.reply(0x108D, struct.pack(">HH", tid & 0xFFFF, slots & 0xFFFF),
              why="MSG_TRADE_START tid=%d SlotCnt=%d" % (tid, slots))
    _log("   0x108D is POPUP-GATED: only the requester popup (0x050b4378) or "
         "the permission popup (0x050b4a9d) builds the trade window; with "
         "neither open this is dropped and no window appears. Look for the "
         "client's own '> MSG_TRADE_START TradeID=%d SlotCnt' line." % tid)


def _send_inbound_request(ctx, tid, requester_name, face):
    ctx.reply(0x2052, struct.pack(">IH", face & 0xFFFFFFFF, tid & 0xFFFF),
              why="inbound MSG_TRADE_REQUEST from %s, fronted by unit %d, tid=%d"
              % (requester_name, face, tid))
    _log("   the arm (0x05053b08) looks unit %d up as class 3 and 0x50b3d30 "
         "builds the permission popup from its name; no such unit = no "
         "popup, silently. The client may auto-answer 0x2056 [tid][7] if the "
         "requester is blacklisted or option byte [0x5166490()+0x4e] is 0." % face)


# ---------------------------------------------------------------------------
# delivery: run an op on the party's OWN thread (or nowhere for a phantom)
# ---------------------------------------------------------------------------
def _deliver(ctx, trade, party, op, **kw):
    """Returns True when the op reached a thread (or the party is a phantom)."""
    payload = dict(kw, op=op, tid=trade.tid)
    if party.kind != "session":
        return True
    if party.is_me():
        on_relay(ctx, payload)
        return True
    n = fw.ext_post("trade", payload, to=lambda s, _n: s is party.session)
    if n == 0:
        _log("   relay %r to %s reached NO session -- the partner is gone"
             % (op, party.name))
    return n > 0


def on_relay(ctx, payload):
    """Runs on the receiving session's thread with ITS ctx/session/store."""
    op, tid = payload.get("op"), payload.get("tid")
    with _LOCK:
        trade = TRADES.get(tid)
        me = trade.mine() if trade else None
    if trade is None or me is None:
        _log("relay %r for tid %s: no such trade on this session -- dropped"
             % (op, tid))
        return
    if op == "request":
        fw._SESSION["trade"] = tid
        face = _peer_face(ctx, trade.requester.charid)
        if not face:
            _log("no --trade-face / --npc-base unit to front %s's request: "
                 "refusing" % trade.requester.name)
            _finish(ctx, trade, me, "reqcancel", 1)
            return
        me.face = face
        _send_inbound_request(ctx, tid, trade.requester.name, face)
    elif op == "start":
        _send_start(ctx, tid)
    elif op == "offer":
        _send_offer(ctx, tid, payload["offer"], payload.get("whose", "partner"))
    elif op == "reqcancel":
        _ng(ctx, 0x2057, tid, payload.get("reason", 2), NG_REQCANCEL,
            "MSG_TRADE_REQUEST_CANCEL (partner)")
        fw._SESSION["trade"] = None
    elif op == "cancel":
        _ng(ctx, 0x2056, tid, payload.get("reason", 1), NG_CANCEL,
            "MSG_TRADE_CANCEL (partner) -- window prints 'Partner cancelled the trade'")
        fw._SESSION["trade"] = None
    elif op == "apply":
        # phase 1 on the PARTNER's thread: validate + apply my half, report back
        ok, why = _apply_side(ctx, trade, me)
        if ok:
            fw._SESSION["trade"] = None       # the record is dropped by 'applied'
            _deliver(ctx, trade, trade.other(me), "applied")
        else:
            _log("   apply on %s FAILED: %s" % (me.name, why))
            _deliver(ctx, trade, trade.other(me), "cancel", reason=8)
            fw._SESSION["trade"] = None
    elif op == "applied":
        ok, why = _apply_side(ctx, trade, me)
        if not ok:
            _log("WARNING: the partner already applied its half and THIS side now "
                 "fails (%s): the trade is half done. Not atomic -- see the "
                 "module docstring." % why)
            _ng(ctx, 0x2056, tid, 0, NG_CANCEL, "MSG_TRADE_CANCEL (apply failed)")
        with _LOCK:
            trade.state = "done"
            TRADES.pop(tid, None)
        fw._SESSION["trade"] = None
    else:
        _log("relay op %r unknown -- dropped" % (op,))


def _finish(ctx, trade, me, op, reason):
    """Tear a trade down from `me`'s side: tell the other party, forget it."""
    with _LOCK:
        trade.state = "cancelled"
        TRADES.pop(trade.tid, None)
    fw._SESSION["trade"] = None
    _deliver(ctx, trade, trade.other(me), op, reason=reason)


# ---------------------------------------------------------------------------
# the store side: what each party gives / takes, applied on its own thread
# ---------------------------------------------------------------------------
def _money(args, key, seed_attr):
    return fw._seeded_value(args, key, getattr(args, seed_attr, None))


def _bag_cap(args):
    cap = getattr(args, "bag_size", None)
    try:
        cap = int(fw._seeded_value(args, "bag_size", int(cap)) or 0) if cap else 0
    except (TypeError, ValueError):
        cap = 0
    return min(96, cap) if cap > 0 else 96


def _validate_give(args, offer):
    """The ENTRY checks, in the client's NG vocabulary. Returns (code, why)
    or (0, '') when the offer is good."""
    rows = fw.item_rows(args)
    by_uid = {r[0]: r for r in rows}
    equip = [tuple(x) for x in (fw._load_char_field(args, "equip", []) or [])]
    worn = {int(u) for _sl, u in equip}
    seen = set()
    for uid, count in offer["raw"]:
        if uid in seen:
            return 11, "uid %d listed twice" % uid
        seen.add(uid)
        row = by_uid.get(uid)
        if row is None:
            return 7, "uid %d is not in this character's bag" % uid
        if uid in worn:
            return 8, "uid %d is equipped" % uid
        if count < 1 or count > row[3]:
            return 10, "uid %d: asked %d, holds %d" % (uid, count, row[3])
    gold = _money(args, "gold", "gold")
    if offer["a"] and (gold is None or offer["a"] > gold):
        return 10, "gold %d offered, %s held" % (offer["a"], gold)
    crystal = _money(args, "crystal", "crystal")
    if offer["b"] and (crystal is None or offer["b"] > crystal):
        return 10, "crystal %d offered, %s held" % (offer["b"], crystal)
    return 0, ""


def _apply_side(ctx, trade, me):
    """Move rows OUT of my store (my offer) and INTO it (the partner's), then
    push: 0x2055 first (closes the window, prints 'Trade complete.'), the
    0x107A burst, the wallet. Everything below runs on MY thread."""
    args = ctx.args
    other = trade.other(me)
    give, take = me.offer, other.offer
    code, why = _validate_give(args, dict(give, raw=[(u, c) for u, _n, c in give["items"]]))
    if code:
        return False, why
    old = fw.item_rows(args)
    charid = int(fw._SESSION.get("charid") or 0)
    new_rows, gone = list(old), []
    for uid, _no, count in give["items"]:
        idx = next(i for i, r in enumerate(new_rows) if r[0] == uid)
        row = new_rows[idx]
        if count >= row[3]:
            gone.append((uid, old.index(row)))
            new_rows.pop(idx)
        else:
            new_rows[idx] = (row[0], row[1], row[2], row[3] - count)
    if len(new_rows) + len(take["items"]) > _bag_cap(args):
        return False, "bag would hold %d rows, capacity %d" % (
            len(new_rows) + len(take["items"]), _bag_cap(args))
    # never reuse a uid the remove burst just destroyed on the client
    # ...nor one an item in the BANK still carries (feworld.new_item_uid)
    nxt = fw.new_item_uid(args, list(old) + list(new_rows))
    for _uid, no, count in take["items"]:
        new_rows.append((nxt, no, 0, count))
        nxt += 1
    if not fw._store_char_field(args, "items", [list(r) for r in new_rows]):
        return False, "no stored character for account=%r charid=%s" % (
            fw._SESSION.get("account"), charid)
    for key, seed_attr, out, inc in (("gold", "gold", give["a"], take["a"]),
                                     ("crystal", "crystal", give["b"], take["b"])):
        if not (out or inc):
            continue
        have = _money(args, key, seed_attr)
        if have is None:
            _log("   %s: channel unset (no seed) -- %d in / %d out NOT applied"
                 % (key, inc, out))
            continue
        fw._store_char_field(args, key, int(have) - out + inc)
        _log("   %s %d -> %d" % (key, have, int(have) - out + inc))
    ctx.reply(0x2055, b"", why="TRADE COMPLETE tid=%d -- 'Trade complete.' + "
              "window close (0x050b3ceb)" % trade.tid)
    fw.bag_layout_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                       old, new_rows, gone,
                       "trade %d with %s" % (trade.tid, other.name))
    fw.wallet_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args)
    _log("   applied on %s: gave %d row(s) A=%d B=%d, took %d row(s) A=%d "
         "B=%d; bag %d -> %d" % (me.name, len(give["items"]), give["a"],
                                 give["b"], len(take["items"]), take["a"],
                                 take["b"], len(old), len(new_rows)))
    return True, ""


def _try_complete(ctx, trade, me):
    """Both confirmed: the phantom needs no thread; a session partner applies
    first and reports back ('applied'), then this side applies."""
    other = trade.other(me)
    code, why = _validate_give(ctx.args, dict(me.offer, raw=[(u, c) for u, _n, c in me.offer["items"]]))
    if code:
        _log("   my half no longer validates (%s)" % why)
        _ng(ctx, 0x1147, trade.tid, 0, NG_COMPLETE, "MSG_TRADE_COMPLETE_NG")
        _finish(ctx, trade, me, "cancel", 8)
        return
    if other.kind == "phantom":
        ok, why = _apply_side(ctx, trade, me)
        if not ok:
            _log("   apply FAILED: %s" % why)
            _ng(ctx, 0x1147, trade.tid, 0, NG_COMPLETE, "MSG_TRADE_COMPLETE_NG")
            _ng(ctx, 0x2056, trade.tid, 8, NG_CANCEL, "MSG_TRADE_CANCEL")
        with _LOCK:
            trade.state = "done"
            TRADES.pop(trade.tid, None)
        fw._SESSION["trade"] = None
        return
    if not _deliver(ctx, trade, other, "apply"):
        _ng(ctx, 0x2056, trade.tid, 1, NG_CANCEL, "MSG_TRADE_CANCEL (partner gone)")
        with _LOCK:
            TRADES.pop(trade.tid, None)
        fw._SESSION["trade"] = None


# ---------------------------------------------------------------------------
# inbound handlers (this session's client)
# ---------------------------------------------------------------------------
def on_request(ctx, inner):
    """0x2052 [u32 target unit id] (builder 0x050b4090, from the requester
    popup's +0x7c). Answered by 0x108B [u32 target][u16 tid] -- the popup
    ignores a 0x108B whose u32 is not the id it sent (0x050b434c)."""
    f = inner[2:]
    target = _u32(f, 0)
    args = ctx.args
    _log("<- 0x2052 MSG_TRADE_REQUEST target unit=%s" % target)
    if target is None or not _on(args):
        _log("   not answered (%s)" % ("short body" if target is None else "--trade off"))
        return
    if target == fw.unit_id_of(args) or target == int(fw._SESSION.get("charid") or -1):
        ctx.reply(0x108C, struct.pack(">I", 1), why="MSG_TRADE_REQUEST_NG 1 (yourself)")
        return
    if _my_trade() is not None:
        ctx.reply(0x108C, struct.pack(">I", 12), why="MSG_TRADE_REQUEST_NG 12 (already trading)")
        return
    me_name = fw._SESSION.get("chat_name") or _my_name()
    bound = _binds().get(target)
    ent = _session_named(bound) if bound else None
    if ent is None and str(getattr(args, "unit_id", "0")) == "auto":
        # the clicked unit IS a player: fepresence draws each peer under its
        # charid, so no `!trade bind` is needed between two live clients
        ent = _session_by_charid(target)
    with _LOCK:
        tid = _NEXT[0]
        _NEXT[0] = (_NEXT[0] % 0xFFFF) + 1
        if ent is not None:
            if ent["session"].get("trade"):
                ctx.reply(0x108C, struct.pack(">I", 8), why="MSG_TRADE_REQUEST_NG 8 (partner busy)")
                return
            if not ent["session"].get("in_field"):
                ctx.reply(0x108C, struct.pack(">I", 5), why="MSG_TRADE_REQUEST_NG 5 (partner not in a field)")
                return
            other = Party("session", ent["name"], ent["session"], face=target,
                          charid=ent["session"].get("charid"))
        elif getattr(args, "trade_phantom", "off") == "on":
            other = Party("phantom", getattr(args, "trade_phantom_name", "Phantom"),
                          face=target)
        else:
            ctx.reply(0x108C, struct.pack(">I", 5),
                      why="MSG_TRADE_REQUEST_NG 5 -- unit %d is bound to no session "
                      "(`!trade bind %d NAME`) and --trade-phantom is off" % (target, target))
            return
        me = Party("session", me_name, fw._TLS.session, charid=fw._SESSION.get("charid"))
        trade = Trade(tid, me, other)
        TRADES[tid] = trade
    fw._SESSION["trade"] = tid
    ctx.reply(0x108B, struct.pack(">IH", target, tid),
              why="MSG_TRADE_REQUEST_OK target=%d tid=%d -> %s %s"
              % (target, tid, other.kind, other.name))
    if other.kind == "phantom":
        _log("   phantom %s holds the request: `!trade accept` / `!trade decline`"
             % other.name)
    else:
        _deliver(ctx, trade, other, "request")


def on_permission(ctx, inner):
    """0x2053 [u16 tid] (0x050b47f0) -- the requested side's ACCEPT."""
    tid = _u16(inner[2:], 0)
    _log("<- 0x2053 MSG_TRADE_REQUEST_PERMISSION tid=%s" % tid)
    if not _on(ctx.args):
        return
    with _LOCK:
        trade = TRADES.get(tid)
        me = trade.mine() if trade else None
    if trade is None or me is None or me is not trade.target or trade.state != "requested":
        _ng(ctx, 0x2057, tid or 0, 1, NG_REQCANCEL, "MSG_TRADE_REQUEST_CANCEL")
        return
    with _LOCK:
        trade.state = "open"
    _send_start(ctx, tid)
    _deliver(ctx, trade, trade.requester, "start")


def on_request_cancel(ctx, inner):
    """0x2057 [u16 tid][u32 reason] -- either popup's cancel/decline."""
    f = inner[2:]
    tid, reason = _u16(f, 0), _u32(f, 2)
    _log("<- 0x2057 MSG_TRADE_REQUEST_CANCEL tid=%s reason=%s" % (tid, reason))
    if not _on(ctx.args):
        return
    with _LOCK:
        trade = TRADES.get(tid)
        me = trade.mine() if trade else None
    if trade is None or me is None:
        fw._SESSION["trade"] = None
        return
    _finish(ctx, trade, me, "reqcancel", 2)


def on_cancel(ctx, inner):
    """0x2056 [u16 tid][u32 reason] -- the trade window's CANCEL, or the
    inbound-0x2052 arm's auto-decline (reason 7)."""
    f = inner[2:]
    tid, reason = _u16(f, 0), _u32(f, 2)
    _log("<- 0x2056 MSG_TRADE_CANCEL tid=%s reason=%s%s"
         % (tid, reason, " (the client's own auto-decline: blacklist or "
            "option byte [0x5166490()+0x4e]==0)" if reason == 7 else ""))
    if not _on(ctx.args):
        return
    with _LOCK:
        trade = TRADES.get(tid)
        me = trade.mine() if trade else None
    if trade is None or me is None:
        fw._SESSION["trade"] = None
        return
    _finish(ctx, trade, me, "reqcancel" if trade.state == "requested" else "cancel",
            2 if trade.state == "requested" else 1)


def on_entry(ctx, inner):
    """0x2054 [u16 tid][u32 A][u32 B][u16 n] n x {[u32 uid][u16 count]}
    (0x050b374c). OK is header-only 0x108E; NG is 0x108F [u16 tid][u32 code]."""
    f = inner[2:]
    tid = _u16(f, 0)
    a, b, n = _u32(f, 2), _u32(f, 6), _u16(f, 10)
    raw = []
    if n is not None:
        for i in range(n):
            uid, count = _u32(f, 12 + i * 6), _u16(f, 16 + i * 6)
            if uid is None or count is None:
                break
            raw.append((uid, count))
    _log("<- 0x2054 TRADE ENTRY tid=%s A=%s B=%s n=%s %s" % (tid, a, b, n, raw))
    if not _on(ctx.args):
        return
    with _LOCK:
        trade = TRADES.get(tid)
        me = trade.mine() if trade else None
    if trade is None or me is None or trade.state != "open":
        _ng(ctx, 0x108F, tid or 0, 12, NG_ENTRY, "MSG_TRADE_ENTRY_NG")
        return
    if a is None or b is None or n is None or len(raw) != n:
        _ng(ctx, 0x108F, tid, 2, NG_ENTRY, "MSG_TRADE_ENTRY_NG (short body)")
        return
    if me.entered:
        _ng(ctx, 0x108F, tid, 11, NG_ENTRY, "MSG_TRADE_ENTRY_NG")
        return
    offer = {"a": a, "b": b, "raw": raw}
    code, why = _validate_give(ctx.args, offer)
    if code:
        _log("   refused: %s" % why)
        _ng(ctx, 0x108F, tid, code, NG_ENTRY, "MSG_TRADE_ENTRY_NG")
        return
    by_uid = {r[0]: r for r in fw.item_rows(ctx.args)}
    other = trade.other(me)
    slots = int(getattr(ctx.args, "trade_slots", 9) or 9)
    if len(raw) > slots:
        _ng(ctx, 0x108F, tid, 4, NG_ENTRY, "MSG_TRADE_ENTRY_NG")
        return
    with _LOCK:
        me.offer = {"items": [(u, by_uid[u][1], c) for u, c in raw], "a": a, "b": b}
        me.entered = True
    ctx.reply(0x108E, b"", why="MSG_TRADE_ENTRY_OK tid=%d -- window state 0xd->0xe, "
              "CONFIRM button enabled" % tid)
    _deliver(ctx, trade, other, "offer", offer=me.offer, whose=me.name)


def on_complete(ctx, inner):
    """0x2055 [u16 tid] (0x050b3809) -- the CONFIRM button; the window sits
    at state 0x10 until 0x2055 (done) or 0x2056/0x2057 (cancelled) arrives."""
    tid = _u16(inner[2:], 0)
    _log("<- 0x2055 TRADE COMPLETE request tid=%s" % tid)
    if not _on(ctx.args):
        return
    with _LOCK:
        trade = TRADES.get(tid)
        me = trade.mine() if trade else None
    if trade is None or me is None or trade.state != "open":
        _ng(ctx, 0x1147, tid or 0, 2, NG_COMPLETE, "MSG_TRADE_COMPLETE_NG")
        return
    if not me.entered:
        _ng(ctx, 0x1147, tid, 0, NG_COMPLETE, "MSG_TRADE_COMPLETE_NG (no entry)")
        return
    with _LOCK:
        me.confirmed = True
        both = all(p.confirmed for p in trade.parties())
    if not both:
        _log("   waiting for %s to confirm" % trade.other(me).name)
        return
    _try_complete(ctx, trade, me)


def on_2059(ctx, inner):
    _log("<- 0x2059 (empty) -- NOT a trade message: the stat arm 0x05051f1d "
         "sends it when a stat at [unit+idx*4+0x9f8] reaches [unit+0x135c]; "
         "reads like the class level-up request (0x1091 CLASS_LEVEL_UP_OK). "
         "Not answered; %d body bytes" % (len(inner) - 2))


# ---------------------------------------------------------------------------
# !trade verbs -- the phantom counterparty and the probes
# ---------------------------------------------------------------------------
def _my_name():
    """The name this session's client signs its chat with (what chat_relay
    and `!trade bind` match on), else the stored character's name."""
    me = fw._TLS.session
    for ent in fw.ext_sessions():
        if ent["session"] is me and ent["name"]:
            return ent["name"]
    try:
        c = fw._self_char(None)
    except Exception:                                  # noqa: BLE001
        c = None
    return str((c or {}).get("name") or "char%s" % fw._SESSION.get("charid"))


def gm(ctx, line):
    if not line.startswith("!trade"):
        return False
    words = line.split()
    verb = words[1].lower() if len(words) > 1 else "status"
    args = ctx.args
    trade = _my_trade()
    me = trade.mine() if trade else None
    other = trade.other(me) if trade and me else None
    if verb == "status":
        with _LOCK:
            _log("!trade: %s" % ("no trade on this session" if trade is None else
                 "tid %d state %s; me=%s entered=%s confirmed=%s offer=%s | %s %s "
                 "entered=%s confirmed=%s offer=%s"
                 % (trade.tid, trade.state, me.name, me.entered, me.confirmed,
                    me.offer, other.kind, other.name, other.entered,
                    other.confirmed, other.offer)))
            _log("!trade: binds %s; %d trade(s) server-wide" % (_binds(), len(TRADES)))
        return True
    if verb == "bind" and len(words) >= 4:
        uid = int(words[2], 0)
        _binds()[uid] = words[3]
        _log("!trade bind: a 0x2052 aimed at unit %d now reaches session %r"
             % (uid, words[3]))
        return True
    if verb == "offer":
        if trade is not None:
            _log("!trade offer: already in tid %d -- `!trade cancel` first" % trade.tid)
            return True
        name = words[2] if len(words) > 2 else getattr(args, "trade_phantom_name", "Phantom")
        face = int(words[3], 0) if len(words) > 3 else _face_for(args)
        if _session_named(name) is not None:
            _log("!trade offer: %r is a LIVE session -- its client has no "
                 "requester popup, so 0x108D could never open its window. "
                 "Have that player click a unit you bound with "
                 "`!trade bind UNITID %s` instead." % (name, _my_name()))
            return True
        if not face:
            _log("!trade offer: no face unit (--trade-face / --npc-base) -- the "
                 "inbound 0x2052 needs an existing class-3 unit to name")
            return True
        with _LOCK:
            tid = _NEXT[0]
            _NEXT[0] = (_NEXT[0] % 0xFFFF) + 1
            req = Party("phantom", name, face=face)
            mine = Party("session", _my_name(), fw._TLS.session,
                         charid=fw._SESSION.get("charid"))
            TRADES[tid] = Trade(tid, req, mine)
        fw._SESSION["trade"] = tid
        _send_inbound_request(ctx, tid, name, face)
        return True
    if trade is None:
        _log("!trade %s: no trade on this session" % verb)
        return True
    if other.kind != "phantom" and verb in ("accept", "decline", "put", "confirm"):
        _log("!trade %s: the partner is a live session, not a phantom" % verb)
        return True
    if verb == "accept":
        if trade.state != "requested" or other is not trade.target:
            _log("!trade accept: nothing pending for the phantom to accept")
            return True
        with _LOCK:
            trade.state = "open"
        _send_start(ctx, trade.tid)
        return True
    if verb == "decline":
        code = int(words[2], 0) if len(words) > 2 else 2
        with _LOCK:
            trade.state = "cancelled"
            TRADES.pop(trade.tid, None)
        fw._SESSION["trade"] = None
        _ng(ctx, 0x2057, trade.tid, code, NG_REQCANCEL, "MSG_TRADE_REQUEST_CANCEL (phantom)")
        return True
    if verb == "cancel":
        # the OTHER side cancels on me: my client gets 0x2056 (window closes
        # with 'Partner cancelled the trade'); a live partner is told too
        code = int(words[2], 0) if len(words) > 2 else 1
        _finish(ctx, trade, me, "cancel", code)
        _ng(ctx, 0x2056, trade.tid, code, NG_CANCEL, "MSG_TRADE_CANCEL (!trade cancel)")
        return True
    if verb == "put":
        if trade.state != "open":
            _log("!trade put: the trade window is not open yet (state %s)" % trade.state)
            return True
        what = words[2].lower() if len(words) > 2 else "none"
        with _LOCK:
            if what == "none":
                other.offer = {"items": [], "a": 0, "b": 0}
            elif what == "gold":
                other.offer["a"] = int(words[3], 0)
            elif what == "crystal":
                other.offer["b"] = int(words[3], 0)
            else:
                no = int(what, 0)
                count = int(words[3], 0) if len(words) > 3 else 1
                other.offer["items"].append((0, no, count))
            other.entered = True
        _send_offer(ctx, trade.tid, other.offer, other.name)
        return True
    if verb == "confirm":
        if trade.state != "open":
            _log("!trade confirm: state %s" % trade.state)
            return True
        with _LOCK:
            other.entered = True
            other.confirmed = True
            both = me.confirmed
        if both:
            _try_complete(ctx, trade, me)
        else:
            _log("!trade confirm: phantom confirmed; waiting for the client's 0x2055")
        return True
    if verb == "clear":
        ctx.reply(0x2058, b"", why="probe: partner offer withdrawn (window +0x5b6a=0)")
        return True
    if verb == "ng" and len(words) >= 4:
        mid, code = int(words[2], 16), int(words[3], 0)
        if mid == 0x108C:
            ctx.reply(0x108C, struct.pack(">I", code), why="probe MSG_TRADE_REQUEST_NG")
        elif mid in (0x108F, 0x1145, 0x1147, 0x2056, 0x2057):
            _ng(ctx, mid, trade.tid, code,
                {0x108F: NG_ENTRY, 0x1145: {}, 0x1147: NG_COMPLETE,
                 0x2056: NG_CANCEL, 0x2057: NG_REQCANCEL}[mid], "probe")
        else:
            _log("!trade ng: id %04X is not one of 108C/108F/1145/1147/2056/2057" % mid)
        return True
    _log("!trade: verbs are status | bind UNITID NAME | offer [NAME [UNITID]] | "
         "accept | decline [CODE] | put ITEMNO [COUNT] | put gold N | "
         "put crystal N | put none | confirm | cancel [CODE] | clear | ng ID CODE")
    return True
