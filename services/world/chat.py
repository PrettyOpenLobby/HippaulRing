"""Chat: the shared chat room, relaying a line to other sessions, speech bubbles."""
import queue
import socket
import threading
from . import character, messages, sess, territory, wire

# ---- THE CHAT ROOM: how one session's line reaches the OTHER sessions. -----
#
# Every per-connection thing (socket, Blowfish state, _OUT_SEQ) is owned by
# the thread that serves that connection, so a line is never SENT from another
# thread. It is dropped into the receiver's queue.Queue (thread-safe by
# itself) and the receiver's own read loop drains it in chat_pump(), next to
# gm_pump -- the loop is woken every ~400ms in the field by the client's own
# 0x2023 telemetry, so the latency is that and no thread is added. The only
# shared structure is this dict, held under _CHAT_ROOM_LOCK for the few
# microseconds a lookup or a fan-out takes.
#
# Membership is "in the field": a session is listed once serve() starts and
# the fan-out skips entries whose in_field flag is not set, so a player still
# on the map does not get area chat. ONE ROOM FOR THE WHOLE DOOR is CHOSEN,
# not measured -- prod serves one field, and per-field rooms need a field key
# in _SESSION that does not exist yet.
_CHAT_ROOM_LOCK = threading.Lock()
_CHAT_ROOM = {}     # thread ident -> {"q": Queue, "session": dict, "name": str}


def _chat_room_join():
    key = threading.get_ident()
    # KEY: THE WAKE PAIR (2026-09-12). ext_post used to queue a relay and the
    # receiving thread found it on its next inbound frame or --idle-tick-ms
    # (250 ms) -- up to a quarter second of jitter on every relayed 0x2023,
    # against a client whose mover drops a head older than 400 ms. Writing
    # one byte to the receiver's pair makes its select() return at once.
    try:
        wake = socket.socketpair()
        for w in wake:
            w.setblocking(False)
    except (OSError, AttributeError):
        wake = None
    with _CHAT_ROOM_LOCK:
        _CHAT_ROOM[key] = {"q": queue.Queue(), "session": sess._TLS.session,
                           "name": "", "ext": queue.Queue(), "wake": wake}
    return key


def _chat_room_leave():
    with _CHAT_ROOM_LOCK:
        ent = _CHAT_ROOM.pop(threading.get_ident(), None)
    if ent and ent.get("wake"):
        for w in ent["wake"]:
            try:
                w.close()
            except OSError:
                pass


def _wake(ent):
    """Poke a session's wake pair; never blocks, never raises."""
    wake = ent.get("wake") if ent else None
    if not wake:
        return
    try:
        wake[1].send(b"w")
    except (OSError, BlockingIOError):
        pass


def _my_wake_socket():
    with _CHAT_ROOM_LOCK:
        ent = _CHAT_ROOM.get(threading.get_ident())
    wake = ent.get("wake") if ent else None
    return wake[0] if wake else None


def _chat_room_name(name):
    """Remember the name this session's client signs its chat with.

    The client writes its own [unit+0x389] name into every line it sends
    (0x0513354b lea esi,[ebx+0x389]); that is the only place feworld sees the
    name the OTHER clients will test against their blacklists, so it is what
    /tell routing matches on.
    """
    with _CHAT_ROOM_LOCK:
        me = _CHAT_ROOM.get(threading.get_ident())
        if me is not None:
            me["name"] = name


#: 2006 channel scope (SE's guide, flow09): 範囲 /say = those around you,
#: 全体 /all = the whole FIELD, 軍団 /army = your own nation in that field,
#: 個人 /tell = one person anywhere, パーティ /party = your party (feparty).
#: "Around you" has no measured range, so /say is the field too.
CHAT_SAME_FIELD = {0x201A, 0x201B, 0x2065, 0x2066, 0x208A}
CHAT_SAME_NATION = {0x2065, 0x2066}


def chat_relay(mid, parts, field=None, nation=None):
    """Fan one inbound line out to the OTHER in-field sessions it reaches.

    `parts` is what the sender's client put on the wire, already decoded.
    0x2067 (/tell) is [TARGET][SENDER][TEXT] on the wire (0x05133b36 writes
    the argv[0] target first, then [unit+0x389], then the text) and goes to
    the ONE session whose client signs as TARGET, wherever it is. The rest
    reach the sender's FIELD only, and /army only the sender's nation there
    (CHAT_SAME_FIELD / CHAT_SAME_NATION). WARNING: Until 09-11 every line reached
    every session in every field. Returns the number of queues written to.
    """
    me = threading.get_ident()
    want = messages._CHAT_IDS.get(mid, (None, 2))[1] or 2
    if len(parts) < want:
        return 0
    target = parts[0] if mid == 0x2067 else None
    # the SENDER's charid rides along: every caller runs on the sender's own
    # thread, and under --unit-id auto a player's unit id IS its charid --
    # which is the id fepresence draws that player under on everyone else's
    # screen, so it is what puts a speech bubble over them (bubble_speaker)
    cid = int(sess._SESSION.get("charid") or 0)
    n = 0
    with _CHAT_ROOM_LOCK:
        for key, ent in _CHAT_ROOM.items():
            if key == me or not ent["session"].get("in_field"):
                continue
            if mid in CHAT_SAME_FIELD and field is not None                     and ent["session"].get("field") != field:
                continue
            if mid in CHAT_SAME_NATION and (nation is None
                                            or ent.get("nation") != nation):
                continue
            # /tell's target is whatever the sender TYPED; the far session's
            # name is what its client signs with. The client upper-cases a
            # blacklist name on the wire (measured 08-25), so an exact compare
            # here is a coin toss on case -- fold it. Exact still wins.
            if (target is not None and ent["name"] != target
                    and ent["name"].casefold() != target.casefold()):
                continue
            ent["q"].put((mid, list(parts[:want]), cid))
            n += 1
    return n


def bubble_speaker(args, mid, cid):
    """The speaker unit id a relayed chat line goes out with -- the charid of
    the player who said it when this client DRAWS that player, else 0.

    WARNING:KEY: CORRECTS the 09-11 note "a speech balloon over a peer is not possible
    -- the bubble lookup demands kind 1". It is the other way round, read off
    the arm 2026-09-12 (0x05053cbe): at 0x05053db5 it looks for a unit with
    [+0x3c] == speaker AND kind [+0x24] == 1 (0x504d410: sete on the id, sete
    on the kind, test), and on a HIT `jne 0x5053dd7` SKIPS the balloon; on a
    miss it calls 0x5129890(speaker, name, text, 0, -1) -- the balloon, whose
    own lookup takes kinds {1, 2, 0xBBB, 0xBBC} (table 0x528d158). Kind 1 is
    only ever the LOCAL player (ctor 0x0507d650, its sole kind-1 stamp at
    0x0507d6d1), whose /say draws its own balloon when it sends -- so the kind
    test exists to stop a double balloon over yourself. A peer is a kind-2
    avatar (0x1006 type 0 -> ctor 0x050679c0), which the balloon accepts.
    We simply always sent speaker 0, which no unit carries.

    Only a charid in this session's `pres_seen` (the peers this client is
    drawing) is used, so an id can never land on a monster or NPC that
    happens to share it. /tell (0x2067, another arm, unread) keeps 0.
    --chat-bubbles: 'all' (the client treats /say /all /army /party alike),
    'say' (0x201A only), 'off' (speaker 0 always, the old behaviour).
    """
    want = str(getattr(args, "chat_bubbles", "all") or "all")
    if want == "off" or mid == 0x2067 or not cid:
        return 0
    if want == "say" and mid != 0x201A:
        return 0
    if int(cid) not in (sess._SESSION.get("pres_seen") or {}):
        return 0
    return int(cid)


def chat_pump(conn, outbound, mode, be, args):
    """Drain this session's relay queue -- on THIS thread, so the socket, the
    cipher state and _OUT_SEQ are only ever touched by their owner.

    Blacklist: 0x05170e10 in the receiving arm tests the SPEAKER NAME against
    the client's list and drops a hit with no log at all. feworld serves that
    list (blacklist_rows), so a hit is dropped HERE instead, with a log line,
    rather than shipped to be silently thrown away.
    """
    if getattr(args, "chat_relay", "on") != "on":
        return
    with _CHAT_ROOM_LOCK:
        me = _CHAT_ROOM.get(threading.get_ident())
    if me is None:
        return
    if "nation" not in me:
        # what /army filters on; a character's nation never changes mid-session
        me["nation"] = territory.nation_of(args)
    blocked = None
    while True:
        try:
            item = me["q"].get_nowait()
        except queue.Empty:
            return
        mid, parts = item[0], item[1]
        cid = item[2] if len(item) > 2 else 0
        speaker_name = parts[1] if mid == 0x2067 else parts[0]
        if blocked is None:
            blocked = set(r["name"] for r in character.blacklist_rows(args))
        if speaker_name in blocked:
            print("[feworld]    chat relay: %r is on this player's blacklist "
                  "-- 0x05170e10 would drop it silently, so it is dropped "
                  "here instead" % speaker_name, flush=True)
            continue
        # The speaker unit id picks the BALLOON, never the log line: the log
        # (0x0512bf60) takes every line whatever the id. 0 -> no unit carries
        # it -> no balloon; the sender's charid -> a balloon over the avatar
        # this client draws for them (bubble_speaker has the disassembly).
        # The old reason for always 0 -- "prod runs --unit-id 1 for EVERY
        # player, so it would read as 'me'" -- is gone: prod runs --unit-id
        # auto (unit id = charid) since fepresence.
        chat_send(conn, outbound, mode, be, args, mid, parts,
                  speaker=bubble_speaker(args, mid, cid))
_CHAT_NAME_MAX = 0x20       # the unit's own name field is [unit+0x389..0x3ac]
_CHAT_TEXT_MAX = 0x80       # the client's stack slot is 0x81 including the NUL


def chat_body(parts):
    """`[cstr][cstr]...` -- NUL-terminated, no length prefix, no terminator run.

    Matched to the client's own outbound capture byte for byte:

        20 1a | "Lex\\0" | 82 b1 82 f1 82 c9 82 bf 82 cd 00
          id     speaker   cp932  こ ん に ち は

    The first string is the one the blacklist is tested against, so it is the
    SPEAKER; the second is the text.
    """
    out = b""
    for i, p in enumerate(parts):
        raw = p.encode("cp932", "replace")
        cap = _CHAT_NAME_MAX if i == 0 else _CHAT_TEXT_MAX
        if len(raw) > cap:
            print("[feworld]    WARNING: chat field %d clamped %d -> %d bytes: the "
                  "client copies it with strcpy into a fixed stack slot"
                  % (i, len(raw), cap), flush=True)
            raw = raw[:cap]
        out += raw + b"\x00"
    return out


def chat_send(conn, outbound, mode, be, args, mid, parts, speaker=None):
    """Push one chat line as message `mid`.

    WARNING: 2026-08-26 -- **THE HEADER u32 IS THE SPEAKER'S UNIT ID, AND SENDING OUR
    OWN THREW EVERY LINE AWAY.** Chat was reported dead on its first
    live run; the arm says why, and the record decoded perfectly the whole time.
    Both display paths in 0x05053cbe are gated on that u32 (`ebx`, read at
    0x05053cbe from the dispatcher's arg1):

        05053d7e  call 0x507d570        ; the LOCAL player unit
        05053d85  call 0x5077cc0        ; -> [unit+0x3c], its unit id
        05053d8a  cmp eax, ebx
        05053d8c  je  0x5053d9e         ; speaker == me -> NO bubble
        05053d8e  [0x534ae88]->vt[0x2c](text)

        05053dab  push 1 / push ebx
        05053dae  call 0x504cb30        ; the object list [0x5339cc4]
        05053db5  call 0x504d410        ; find [obj+0x3c]==ebx AND [obj+0x24]==1
        05053dba  test eax, eax
        05053dbc  jne 0x5053dd7         ; FOUND -> NO chat-log line either
        05053dcf  call 0x5129890        ; the chat log sink

    We sent `unit_id_of(args)`, and prod runs `--unit-id 1`, so the u32 was the
    player's own id: the first test took the `je` (no bubble) and the second
    FOUND the player -- id 1, kind 1 -- and took the `jne` (no log line).
    **Every sentinel was parsed and discarded.** Nothing about the six ids,
    the two arms or the string layout is retracted by this.

    KEY: `0x504d410` matches on BOTH `[obj+0x3c]` (id) and `[obj+0x24]` (kind,
    and it passes **1**, which is the kind the player reads) -- the same pair of
    fields the 0x2023 movement family gates on. an earlier note: CHECK THE
    GATE BEFORE THE PAYLOAD.

    So the speaker id must NOT resolve to a live kind-1 unit if the line is to
    reach the chat log, which is what a served/relayed line wants. 0 is the
    client's own "no unit" sentinel and no object carries it -- hence the
    default. `--chat-unit-id` exists for the deliberate opposite: give a real
    unit's id and the text should appear over THAT unit instead, which is the
    experiment that proves the reading.
    """
    name, want = messages._CHAT_IDS.get(mid, ("NOT A KNOWN CHAT ID", None))
    if want is not None and len(parts) != want:
        print("[feworld]    WARNING: 0x%04X (%s) reads %d strings, %d given -- a "
              "short record leaves the reader on the next message's bytes"
              % (mid, name, want, len(parts)), flush=True)
    # getattr, not args.chat_unit_id: fe_ui_test builds a SimpleNamespace and
    # the suite is meant to run without knowing every flag.
    # An EXPLICIT speaker is the caller meaning it -- --chat-echo self passes
    # the player's own id on purpose ("the speaker IS this player"), so the
    # warning below must not fire on it. It used to, and it claimed a
    # working line would be discarded while the line visibly appeared.
    speaker_from_flag = speaker is None
    if speaker is None:
        speaker = getattr(args, "chat_unit_id", 0)
    if speaker_from_flag and speaker and speaker == wire.unit_id_of(args):
        print("[feworld]    WARNING: --chat-unit-id %d is the player's own unit id. "
              "0x05053cbe suppresses the speech BALLOON when the speaker is you "
              "(0x05053d8a) -- the chat LOG still shows the line, because it is "
              "an unconditional broadcast at 0x05053da3 gated only by the "
              "blacklist. Use 0 if you want the bubble too." % speaker,
              flush=True)
    wire.send(conn, outbound, wire.inner_msg(mid, chat_body(parts), speaker),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld]    -> 0x%04X CHAT %s %s -- arm %s, speaker unit id %d "
          "(%s)"
          % (mid, name, " | ".join(repr(p) for p in parts),
             "0x05053cbe" if mid != 0x2067 else "0x05053e28", speaker,
             "no such unit -> the CHAT LOG path" if not speaker
             else "resolves? -> bubble if it is a live kind-1 unit"), flush=True)


def parse_chat_line(spec):
    """`ID|NAME|TEXT` (or `ID|A|B|TEXT` for 0x2067) -> (id, [parts]) or None."""
    fields = spec.split("|")
    if len(fields) < 3:
        print("[feworld]    WARNING: --chat-line %r needs ID|NAME|TEXT" % spec,
              flush=True)
        return None
    try:
        mid = int(fields[0], 0) & 0xFFFF
    except ValueError:
        print("[feworld]    WARNING: --chat-line %r: %r is not an id"
              % (spec, fields[0]), flush=True)
        return None
    if mid not in messages._CHAT_IDS:
        print("[feworld]    WARNING: --chat-line id 0x%04X is not one of the six the "
              "client's dispatcher routes to a chat arm (%s) -- sending it "
              "anyway, but the arm that receives it is unknown"
              % (mid, ", ".join("0x%04X" % k for k in sorted(messages._CHAT_IDS))),
              flush=True)
    return mid, fields[1:]


def chat_push(conn, outbound, mode, be, args):
    """Serve every --chat-line once, at field entry.

    WARNING: ONE-SHOT, like npc_push: this fires inside the add-complete block, so a
    line served here lands as the field comes up. Use `!chat ID|NAME|TEXT` in
    --gmcmd-file to fire one at a moment of your choosing instead -- that is
    the lever that lets all six ids be compared on ONE screen in ONE session
    rather than one redeploy per id.
    """
    for spec in getattr(args, "chat_line", None) or []:
        got = parse_chat_line(spec)
        if got:
            chat_send(conn, outbound, mode, be, args, got[0], got[1])
