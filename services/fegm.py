"""fegm.py -- Fantasy Earth's GM / debug / popup / system-message channel.

PARTIAL: EVERYTHING HERE IS BUILT AND UNTESTED ON A SCREEN (2026-09-10). Every arm
was opened in the runtime dump of FE_Client.dll (base 0x04F90000) by
static disassembly; the read sequences below are what was MEASURED, and
each one is decoded back in tools/fe_gm_test.py with the same primitives.

WHAT THE 2026-08-27 SWEEP CALLED "GROUP 8", RE-READ
---------------------------------------------------
Three of the sweep's labels were wrong and are corrected here, because a
server that answers the wrong message hangs nothing and shows nothing:

 * 0xE009 is NOT the `/chr` family. Its four builders (0x05137010 sub 0,
   0x051370d0 sub 1, 0x05137070 sub 2, 0x05137130 sub 3) all write
   [u8 sub][cstr my_name (self+0x389)][cstr text, or "@clear@" when empty]
   -- a GM SYSTEM MESSAGE request, whose notify is 0xE00B (below) and whose
   NG is 0xE00C MSG_R_GM_SYSTEM_MESSAGE_NG (NG table: 0 undefined, 1 too few
   params, 2 no character). The four subs are the four console verbs in
   feworld.GM_COMMANDS["debug"] -- sysmes / fieldmes / hostmes / areames --
   and WHICH sub is which verb is not measured (no xref reaches the four
   builders; they sit in a table the dump does not resolve). Sub 0 is
   relayed to everyone, 1..3 to sessions in the same field. THAT MAPPING IS
   OURS.
 * The `/chr` verbs ARE 0x208C MSG_DEBUG_COMMAND, one sub-byte each, from the
   dispatcher at 0x051365ad (strcmp against the verb, then begin(0x208C),
   write_u8(sub), fields, flush). Measured per block:

       sub 0x07  break     [u32 objId]                      (0x051356f7)
       sub 0x0f  item      [u32 objId][u16 n] n x [u16 item] (0x051366ac)
       sub 0x10  endurance [u32 objId][u32 a][u32 b]        (0x05136a6d)
       sub 0x11  pos       [u32 objId][f32 x][f32 y][f32 z] (0x05136b38)
       sub 0x12  hp        [u32 objId][u16 v]  (writer 0x5045c40 = +2)
       sub 0x13  power     [u32 objId][u16 v]
       sub 0x14  stamina   [u32 objId][u16 v]
       sub 0x15  gold      [u32 objId][u32 v]
       sub 0x16  score     [u32 objId][u32 v]
       sub 0x17  crystal   [u32 objId][u32 v]
       sub 0x18  exp       [u32 objId][u32 v]
       sub 0x1c  token     [u32 objId][u32 v]
       sub 0x1e  fame      [u32 objId][u32 v]

   objId is the object the client resolved from the NAME the GM typed
   (`chr fame %s[ID:%d] %d`, unknown name -> `unknown ObjectName`). The
   replies are 0x1140 MSG_DEBUG_COMMAND_OK (arm 0x05055df0, HEADER-ONLY) and
   0x1141 MSG_DEBUG_COMMAND_NG [u32 code] (arm 0x05055e0b -> reader
   0x5045ef0, logs "%d"; NG table 0 undefined / 2,3 bad parameter / 20 cannot
   create item). No block registers a reply, so nothing hangs either way.
 * 0x1129 is not a generic popup. Its arm (0x05058371) reads
   [cstr a][u8 s][cstr b][u8 k] -- the u8 reader is 0x5045e90 (`inc edx`,
   one byte; the automated body-reader did not know it and printed
   `cstr, cstr`; the sweep guessed u16) -- and hands them to
   0x0511eeb0 -> 0x0511efc0, which is a
   sprintf of "%s defeated %s" / "%s destroyed %s" (0x52ea1a4/1bc/1d4) into
   the message window with a colour: it is the KILL TICKER. `s & 0xf` is
   compared against the viewer's own side byte (0x504a480) to pick the
   colour; `k >> 4` selects the verb (1 = defeated, 4 = destroyed); 0xff in
   either u8 takes the plain "%s defeated %s" path. It is gated on the
   message-window object [0x53451fc] != NULL.

THE REPLIES THIS MODULE SENDS (arm, measured read sequence)
------------------------------------------------------------
    0x1143  COUNTDOWN popup     0x05055e3c  u32 secs, cstr text
            -> window factory 0x0509c1f0 returns [0x53432bc]; NULL, or
               0x0509d480(it) == 0, jumps to 0x5057423 = SILENT DROP. Logged
               on every send.
    0x1129  KILL TICKER         0x05058371  cstr, u8, cstr, u8   (see above)
    0xE00B  MSG_R_GM_SYSTEM_MESSAGE_NOTIFY  0x05057121  cstr title, cstr text
            -> text == "@clear@" (0x52d5928) closes the banner window
               ([0x5345460] -> 0x508d9f0); otherwise creates it through the
               same factory 0x0509c1f0 (NULL = dropped) and sets the text
               with 0x5123240. Logged as "> MSG_R_GM_SYSTEM_MESSAGE_NOTIFY".
    0xD016  (unnamed dialog)    0x050588c3  u8 flag, cstr a, cstr b
            -> flag 0: allocates a 0x4c0 window (0x5123370) via 0x0509c1f0
               and shows both strings; flag 1: nothing; other: logs
               "ignore display type."; then ALWAYS prints b as a system line
               (0x5121840(0x2710, b)). c3 dispatcher arm.
    0x20B1  MSG_SOS_OK          0x05056936  HEADER-ONLY; prints
            "Reinforcements called." (0x52d5ba8)
    0x20B2  MSG_SOS_NG          0x0505690e  u32 code (0 "Only warring
            nations can call aid", 8 "Conditions unmet")
    0x20AF  SOS broadcast       0x050564c4  u32 force, u32 area, u32 unit,
            u32 ally, u32 foe, cstr text
            -> sprintf("Reinforce! ->%s:%s (ally %d : foe %d)",
               areaName(area-1) via 0x50081e0, unitName(unit)+0x2e via
               0x5009820, ally, foe). The unit MUST exist on the receiving
               client (lookup miss -> 0x5057423 = dropped); in a capital
               with scene state 0xb it is re-posted as a 0x201B chat line.
    0x1140  MSG_DEBUG_COMMAND_OK   0x05055df0  HEADER-ONLY
    0x1141  MSG_DEBUG_COMMAND_NG   0x05055e0b  u32 code
    0xB00C  (unnamed)           0x05056f88 -> 0x05069b30
            [u32 unitId][u8 kind][u8 flag]; kind must be 1; the unit is
            looked up (0x504d350) and [unit+0x9f8] = (flag != 0). No reader
            of +0x9f8 was found by a byte-pattern scan (mov/cmp/movzx over
            eax/esi/edi/ecx bases); the field's meaning is UNOPENED. Served
            as a probe only (`!npcflag`).

DECODED BUT NOT SERVED (why)
----------------------------
    0x1142  arm 0x05055bab -> 0x04ff5df0(obj = 0x50014d0()): [u32 mask]
            bit0 u32, bit1 u16 -> [obj+0x5a], bit2 u16 (reader 0x5045ec0)
            -> [obj+0x58], bit3 u32, bit4 u16. Unnamed, unknown object
            fields. Registered in KNOWN; nothing sends it.
    0x303F  arm 0x05024a56: [u32 v] -> 0x50613e0(0x1162, v) posts an
            internal event. Unnamed; nothing sends it.
    0xE00E  MSG_R_GM_FORCE_MOVE_OK arm 0x050573e0: [u32], read and ignored
            (al = 1 straight after). The OK for the client's 0xE00D request
            [cstr name][u32 area <= 0x5f][f32 x][f32 y][f32 z] (builder
            0x05137190); feworld already owns 0xE00D/0xE00F.
    0xE001  doc-only in feworld (GET_CHARACTER_INFO_FROM_NAME_OK).
    0xF100  popitem: schema-serialised through 0x0517e050 from a struct
            {u16 item; u32 num; ...} (0x05137734.., "too many items" > 0x63).
            The schema registry [0x5339998] is unopened; logged as hex with
            a best-effort [u16][u32] read. Items are feitems' lane.
    0xF300  RequestDamageMap {u32 object_id} through 0x05047800 (the same
            serialiser); the F301/F302 replies are unopened. Logged.
    0xF303  THE UNKNOWN-SLASH FORWARD: the slash dispatcher (0x05133e00
            family, 0x05133ecb..) walks the command table [0x528d96c]
            (stride 0x10) and on a MISS builds {char[0x200]; u16 len} from
            the text after '/' and sends it as 0xF303 -- the same shape as
            0xF900. Wire: [u16 len][bytes]. Routed to the `!` verb table when
            the sender is a GM account (--gm-slash verbs).
    0xF500  ReplyToBuildKeep {u8 reply 1|2} and 0xF501 CommandBuildKeepForWar
            {u16 type, gx, gy, gz, dir=1} -- WARNING: NOT a GM window: the `cmp
            esi,5` is the keep-vote dialog's loop over its FIVE members
            (2026-09-11). fecampaign owns both and shadows these log-only
            placeholders when it loads (see its docstring).
    0x208C sub 0x07 break building: DECODED here, APPLIED by the fewar
            worker. fewar loads BEFORE fegm and claims 0x208C itself (it
            answers sub 7 and only logs the rest), so register() shadows
            its handler on purpose and passes sub 7 back to it untouched
            (_PREV_208C / _PREV_SUBS). `register_debug_sub(0x07, fn)` is
            the alternative seam; with neither, sub 7 is logged and
            answered NG 2.

THE GM GATE
-----------
Nothing on this channel mutates state unless the session's ACCOUNT is in
--gm-accounts (comma list, `*` = everyone). Default EMPTY: every request is
decoded and logged, the client is told NG, and nothing changes. That is the
"defaults off" rule -- the client's own gate is a GM level nobody here
issues, so any client can send these.

WHAT IS OK: APPLIED (through feworld's own pushes, store first)
    /chr hp     -> ctx.session["player_hp"], player_hp_push (0x2024 bit 4)
    /chr gold   -> store "gold",  0x2024 maskA bit 0x00200000 (+0x4ec)
    /chr score  -> store "total_score", 0x2024 maskB bit 1 (+0x500)
    /chr crystal-> store "crystal", crystal_push(value) (0x2035 bit 1)
    /chr exp    -> store "exp" via combat_exp_push(delta) (0x1075 bit 2)
    /chr pos    -> goto_push(current field, pos) = 0x1166 same-field warp
    /monster    -> 0xA00D [u8 num][cstr name][f32x3 pos][f32x3 dir]
                   (builder 0x05138350, caller 0x051375e1 = the console's
                   monster picker) -> npc_send() type-8 monsters by
                   fet_npc_type NAME, level from the table.
    sysmes etc. -> 0xE009 -> 0xE00B relayed (include_me) to the chosen scope.
    SOS         -> 0x20AF [u32 field] (sender 0x05138290, 60 s cooldown
                   client-side) -> 0x20B1 OK + 0x20AF broadcast to the
                   caller's side. --sos on|ok|off, default on: the OK is
                   harmless and the broadcast is the point of the message.

`!verbs` (--gmcmd-file / the devtool queue):
    !popup SECS|TEXT              0x1143
    !notice KILLER|VICTIM[|k[|s]] 0x1129  k: 1 defeated (default) 4
                                  destroyed 255 plain; s: side byte
                                  (default 255 = plain colour)
    !sysmsg TITLE|TEXT            0xE00B   (`!sysmsg clear` closes it)
    !dialog FLAG|A|B              0xD016   (flag 0 window, 1 line only)
    !npcflag ID|0|1               0xB00C probe
    !gm SUB|ARGS                  re-inject a 0x208C locally (test lever)
"""
import struct

fw = None   # the feworld module, handed in by register()

#: sub-byte -> (verb, struct fields after [u32 objId]) for 0x208C
DEBUG_SUBS = {
    0x07: ("break", ""),
    0x0f: ("item", "items"),
    0x10: ("endurance", "II"),
    0x11: ("pos", "fff"),
    0x12: ("hp", "H"),
    0x13: ("power", "H"),
    0x14: ("stamina", "H"),
    0x15: ("gold", "I"),
    0x16: ("score", "I"),
    0x17: ("crystal", "I"),
    0x18: ("exp", "I"),
    0x1c: ("token", "I"),
    0x1e: ("fame", "I"),
}
#: sub-byte -> fn(ctx, obj_id, fields) -> True if it answered. Another
#: module (fewar for 0x07) claims a sub here; the decode stays in one place.
DEBUG_SUB_HOOKS = {}

#: The four 0xE009 subs, named after GM_COMMANDS["debug"]'s four message
#: verbs. WARNING: WHICH SUB IS WHICH VERB IS NOT MEASURED -- see the docstring.
SYSMSG_SUBS = {0: "sysmes", 1: "fieldmes", 2: "hostmes", 3: "areames"}

#: 0x2024 field bits, copied from feworld so a rename there fails loudly in
#: the test rather than silently here.
_GOLD_BIT = 0x00200000
_SCORE_BIT_B = 0x00000001

# The client's own log names (NG table / arm strings) for the ids we answer.
NAMES = {
    0x1129: "KILL TICKER '%s defeated %s' [cstr][u8 side][cstr][u8 kind<<4] (arm 05058371)",
    0x1140: "MSG_DEBUG_COMMAND_OK (header-only, arm 05055df0)",
    0x1141: "MSG_DEBUG_COMMAND_NG [u32 code] (arm 05055e0b)",
    0x1142: "(unnamed) mask-driven setter [u32 mask]{u32,u16,u16,u32,u16} -> 04ff5df0",
    0x1143: "COUNTDOWN popup [u32 secs][cstr] (arm 05055e3c, factory 0509c1f0)",
    0x208C: "MSG_DEBUG_COMMAND [u8 sub][u32 objId]... (the /chr verbs + break)",
    0x20AF: "SOS: request [u32 field] / broadcast [u32 force][u32 area][u32 unit][u32 ally][u32 foe][cstr]",
    0x20B1: "MSG_SOS_OK (header-only, arm 05056936)",
    0x20B2: "MSG_SOS_NG [u32 code] (arm 0505690e)",
    0x303F: "(unnamed) [u32] -> internal event 0x1162 (arm 05024a56)",
    0xA00D: "MSG_NPC_GENERATE_REQUEST [u8 num][cstr name][f32x3 pos][f32x3 dir] (builder 05138350)",
    0xB00C: "(unnamed) [u32 unitId][u8 kind==1][u8 flag] -> [unit+0x9f8] (05069b30)",
    0xD016: "(unnamed dialog) [u8 flag][cstr a][cstr b] (c3 arm 050588c3)",
    0xE009: "GM SYSTEM MESSAGE request [u8 sub][cstr myName][cstr text|@clear@]",
    0xE00B: "MSG_R_GM_SYSTEM_MESSAGE_NOTIFY [cstr title][cstr text] (arm 05057121)",
    0xE00C: "MSG_R_GM_SYSTEM_MESSAGE_NG (registered reply, no dispatcher arm)",
    0xE00E: "MSG_R_GM_FORCE_MOVE_OK [u32] read and ignored (arm 050573e0)",
    0xF100: "popitem {u16 item; u32 num} via schema 0517e050 (unopened)",
    0xF300: "RequestDamageMap {u32 object_id} via schema 05047800",
    0xF303: "unknown-slash forward [u16 len][text] (0x05133ecb, table miss)",
    0xF500: "ReplyToBuildKeep {u8 reply} (keep-vote dialog 050335d0; fecampaign's)",
    0xF501: "CommandBuildKeepForWar {u16 type,gx,gy,gz,dir} (05072bd0; fecampaign's)",
}


#: The handler that owned 0x208C before fegm loaded (fewar's: it answers sub
#: 0x07 break-building and only LOGS every other sub). fegm loads LAST in
#: EXT_MODULES, so the id is already claimed; we shadow it on purpose and
#: hand the subs in _PREV_SUBS back to it, everything else is ours.
_PREV_208C = None
_PREV_SUBS = frozenset((0x07,))


def register(feworld):
    global fw, _PREV_208C
    fw = feworld
    fw.register_handler(0x20AF, on_sos)
    prev = fw.EXT_HANDLERS.get(0x208C)
    if prev is None or prev is on_debug_command:
        fw.register_handler(0x208C, on_debug_command)
    else:
        # A deliberate shadow: register_handler would raise 'claimed twice'.
        _PREV_208C = prev
        fw.EXT_HANDLERS[0x208C] = on_debug_command
        print("[fegm] 0x208C was claimed by %s -- fegm now decodes it and "
              "hands sub %s back to that handler unchanged"
              % (getattr(prev, "__module__", "?"),
                 ",".join("0x%02x" % s for s in sorted(_PREV_SUBS))),
              flush=True)
    fw.register_handler(0xE009, on_sysmsg_request)
    fw.register_handler(0xA00D, on_npc_generate)
    fw.register_handler(0xF100, on_f100)
    fw.register_handler(0xF300, on_f300)
    fw.register_handler(0xF303, on_f303)
    fw.register_handler(0xF500, on_f500)
    fw.register_handler(0xF501, on_f501)
    fw.register_gm(gm)
    fw.register_args(add_args)
    fw.register_relay("gm.sos", on_relay_sos)
    fw.register_relay("gm.sysmsg", on_relay_sysmsg)
    for mid, name in NAMES.items():
        fw.KNOWN.setdefault(mid, name)


def register_debug_sub(sub, fn):
    """Let another module own one 0x208C sub-byte (fewar: 0x07 break)."""
    DEBUG_SUB_HOOKS[int(sub)] = fn


def add_args(p):
    p.add_argument("--gm-accounts", default="",
                   help="comma list of ACCOUNT names whose GM/debug requests "
                        "are APPLIED (`*` = all). Default: none -- every "
                        "request is decoded, logged and answered NG.")
    p.add_argument("--sos", default="on", choices=("on", "ok", "off"),
                   help="0x20AF SOS: on = OK + broadcast to the caller's side, "
                        "ok = OK only, off = log only")
    p.add_argument("--gm-slash", default="verbs", choices=("verbs", "log"),
                   help="0xF303 (a /command the client does not know): route "
                        "to the server's `!` verb table for GM accounts, or log")


# ---------------------------------------------------------------------------
# wire helpers
def _cstr(f, i):
    """NUL-terminated cp932 at f[i:] -> (text, next index). A body with no NUL
    (the raw writer 0x5045d20 counts strlen; whether 0x522f580 emits the NUL
    is not proved) takes the rest of the buffer."""
    e = f.find(b"\0", i)
    if e < 0:
        return f[i:].decode("cp932", "replace"), len(f)
    return f[i:e].decode("cp932", "replace"), e + 1


def _pc(s):
    return str(s).encode("cp932", "replace") + b"\0"


def _account(ctx):
    return ctx.session.get("account") or ""


def is_gm(ctx):
    allow = str(getattr(ctx.args, "gm_accounts", "") or "").strip()
    if not allow:
        return False
    if allow == "*":
        return True
    acct = _account(ctx).lower()
    return acct in [a.strip().lower() for a in allow.split(",") if a.strip()]


def _self_unit(ctx):
    return fw.unit_id_of(ctx.args)


def _my_name(ctx):
    c = fw._self_char(ctx.args) or {}
    return str(c.get("name") or ctx.session.get("name") or "")


def _my_force(ctx):
    c = fw._self_char(ctx.args) or {}
    try:
        return int(c.get("force") or 0)
    except (TypeError, ValueError):
        return 0


def _log(msg):
    print("[fegm] " + msg, flush=True)


# ---------------------------------------------------------------------------
# PUSHES (server -> client), one per reply id, each decoded in the test
def popup_push(ctx, secs, text):
    """0x1143: [u32 secs][cstr text]. Arm 0x05055e3c reads u32 (0x5045e60)
    then cstr (0x5045f50) and calls the window factory 0x0509c1f0; a NULL
    there (`je 0x5057423`) drops the popup with no log line on the client."""
    body = struct.pack(">I", int(secs) & 0xFFFFFFFF) + _pc(text)
    ctx.reply(0x1143, body, why="COUNTDOWN popup %ds %r" % (int(secs), text))
    _log("0x1143 is WINDOW-GATED: factory 0x0509c1f0 returns [0x53432bc]; "
         "NULL (or 0x0509d480() == 0) -> je 0x5057423, dropped silently. "
         "'あと %d 秒' is what a rendered one says.")


def notice_push(ctx, killer, victim, kind=1, side=0xFF):
    """0x1129: [cstr killer][u8 side][cstr victim][u8 kind << 4].
    kind 1 -> '%s defeated %s', 4 -> '%s destroyed %s', 0xff -> plain."""
    k = 0xFF if int(kind) == 0xFF else ((int(kind) & 0xF) << 4)
    body = _pc(killer) + bytes([int(side) & 0xFF]) + _pc(victim) + bytes([k])
    ctx.reply(0x1129, body, why="KILL TICKER %r %s %r" % (
        killer, {1: "defeated", 4: "destroyed"}.get(int(kind), "?"), victim))
    _log("0x1129 is gated on the message window [0x53451fc] != NULL "
         "(0x0511eeb0); side byte compared to the viewer's own via 0x504a480 "
         "picks the colour only.")


def sysmsg_push(ctx, title, text):
    """0xE00B: [cstr title][cstr text]. text '@clear@' closes the banner."""
    ctx.reply(0xE00B, _pc(title) + _pc(text),
              why="MSG_R_GM_SYSTEM_MESSAGE_NOTIFY %r/%r" % (title, text))
    if text == "@clear@":
        _log("0xE00B '@clear@': closes [0x5345460] if open, else dropped.")
    else:
        _log("0xE00B is WINDOW-GATED: factory 0x0509c1f0 NULL -> je "
             "0x5057423, dropped silently; otherwise a 0x64-byte banner "
             "(0x5122ae0) is created/updated with the text.")


def dialog_push(ctx, flag, a, b):
    """0xD016: [u8 flag][cstr a][cstr b] (c3 arm 0x050588c3)."""
    ctx.reply(0xD016, bytes([int(flag) & 0xFF]) + _pc(a) + _pc(b),
              why="dialog flag=%d" % int(flag))
    _log("0xD016 flag %d: %s; b is ALWAYS printed as a system line "
         "(0x5121840(0x2710, b))." % (int(flag), {
             0: "0x4c0 window via factory 0x0509c1f0 (NULL = dropped)",
             1: "no window"}.get(int(flag), "'ignore display type.' logged")))


def npcflag_push(ctx, unit_id, flag):
    """0xB00C: [u32 unitId][u8 1][u8 flag] -> [unit+0x9f8] = flag != 0."""
    ctx.reply(0xB00C, struct.pack(">IBB", int(unit_id) & 0xFFFFFFFF, 1,
                                  1 if flag else 0),
              why="[unit %d +0x9f8] = %d (meaning UNOPENED)" % (
                  int(unit_id), 1 if flag else 0))


def debug_ok(ctx):
    ctx.reply(0x1140, b"", why="MSG_DEBUG_COMMAND_OK")


def debug_ng(ctx, code):
    ctx.reply(0x1141, struct.pack(">I", int(code) & 0xFFFFFFFF),
              why="MSG_DEBUG_COMMAND_NG code=%d" % int(code))


def sos_ok(ctx):
    ctx.reply(0x20B1, b"", why="MSG_SOS_OK ('Reinforcements called.')")


def sos_ng(ctx, code):
    ctx.reply(0x20B2, struct.pack(">I", int(code) & 0xFFFFFFFF),
              why="MSG_SOS_NG code=%d" % int(code))


def sos_broadcast_body(force, area, unit, ally, foe, text):
    """0x20AF inbound to the client, arm 0x050564c4: five u32 then cstr."""
    return struct.pack(">IIIII", force & 0xFFFFFFFF, area & 0xFFFFFFFF,
                       unit & 0xFFFFFFFF, ally & 0xFFFFFFFF,
                       foe & 0xFFFFFFFF) + _pc(text)


# ---------------------------------------------------------------------------
# 0x20AF  SOS
def on_sos(ctx, inner):
    f = inner[2:]
    field = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
    mode = str(getattr(ctx.args, "sos", "on"))
    in_field = bool(ctx.session.get("in_field"))
    _log("0x20AF SOS request field=%d from %r (in_field=%s, --sos %s)"
         % (field, _account(ctx), in_field, mode))
    if mode == "off":
        return
    if not in_field:
        sos_ng(ctx, 0)      # 'Only warring nations can call aid.'
        return
    sos_ok(ctx)
    if mode != "on":
        return
    force = _my_force(ctx)
    unit = _self_unit(ctx)
    myfield = ctx.session.get("field")
    ally = foe = 0
    for s in fw.ext_sessions():
        sd = s["session"]
        if sd.get("field") != myfield:
            continue
        # a side is what the OTHER session's stored nation says; unknown
        # counts as foe so the numbers never flatter the caller
        if sd.get("force", None) == force:
            ally += 1
        else:
            foe += 1
    payload = {"force": force, "area": field or (myfield or 0), "unit": unit,
               "ally": ally, "foe": foe, "text": _my_name(ctx),
               "field": myfield}
    n = fw.ext_post("gm.sos", payload, include_me=True,
                    to=lambda s, name: s.get("field") == myfield)
    _log("   -> 0x20AF broadcast queued for %d session(s) in field %s: "
         "force=%d area=%d unit=%d ally=%d foe=%d. WARNING: The receiving arm "
         "resolves unit %d with 0x5009820 and DROPS the line if that "
         "object is unknown there." % (n, myfield, force, payload["area"],
                                        unit, ally, foe, unit))


def on_relay_sos(ctx, p):
    body = sos_broadcast_body(p["force"], p["area"], p["unit"], p["ally"],
                              p["foe"], p["text"])
    ctx.reply(0x20AF, body, why="SOS broadcast 'Reinforce! ->%s:%s (ally %d : "
              "foe %d)'" % (p["area"], p["text"], p["ally"], p["foe"]))


# ---------------------------------------------------------------------------
# 0x208C  MSG_DEBUG_COMMAND -- the /chr verbs and break
def decode_debug(f):
    """-> (sub, verb, obj_id, fields) or None. `fields` is a tuple; for
    `item` it is (n, [item ids])."""
    if len(f) < 5:
        return None
    sub = f[0]
    obj = struct.unpack_from(">I", f, 1)[0]
    verb, fmt = DEBUG_SUBS.get(sub, ("?", None))
    rest = f[5:]
    if fmt is None:
        return sub, verb, obj, (rest,)
    if fmt == "items":
        n = struct.unpack_from(">H", rest, 0)[0] if len(rest) >= 2 else 0
        ids = [struct.unpack_from(">H", rest, 2 + 2 * i)[0]
               for i in range(n) if len(rest) >= 4 + 2 * i]
        return sub, verb, obj, (n, ids)
    if fmt == "":
        return sub, verb, obj, ()
    size = struct.calcsize(">" + fmt)
    if len(rest) < size:
        return sub, verb, obj, (rest,)
    return sub, verb, obj, struct.unpack_from(">" + fmt, rest, 0)


def on_debug_command(ctx, inner):
    d = decode_debug(inner[2:])
    if d is None:
        _log("0x208C: %d-byte body, not decoded: %s" % (len(inner) - 2,
                                                          inner[2:].hex()))
        return
    sub, verb, obj, fields = d
    me = _self_unit(ctx)
    _log("0x208C MSG_DEBUG_COMMAND sub=0x%02x %s objId=%d%s fields=%r from %r"
         % (sub, verb, obj, " (self)" if obj == me else "", fields,
            _account(ctx)))
    hook = DEBUG_SUB_HOOKS.get(sub)
    if hook is not None:
        if hook(ctx, obj, fields):
            return
    elif sub in _PREV_SUBS and _PREV_208C is not None:
        # fewar's lane (break building): the whole inner, untouched
        _PREV_208C(ctx, inner)
        return
    if not is_gm(ctx):
        _log("   %r is not in --gm-accounts: NOT applied, NG 0" % _account(ctx))
        debug_ng(ctx, 0)
        return
    if verb in ("hp", "gold", "score", "crystal", "exp", "pos") and obj not in (me, 0):
        # the object is another unit; the store and the pushes here are the
        # SELF session's. Cross-session application would need a relay
        # keyed by unit id; not built.
        _log("   objId %d is not this session's unit %d -- not applied, NG 2"
             % (obj, me))
        debug_ng(ctx, 2)
        return
    a = ctx.args
    c, o, m, b = ctx.conn, ctx.outbound, ctx.mode, ctx.be
    if verb == "hp":
        hp = int(fields[0])
        ctx.session["player_hp"] = hp
        fw.player_hp_push(c, o, m, b, a, hp)
        _log("   applied: HP -> %d (0x2024 maskA bit 4 STORES the new hp)" % hp)
    elif verb == "gold":
        v = int(fields[0])
        fw._store_char_field(a, "gold", v)
        ctx.reply(0x2024, struct.pack(">III", _GOLD_BIT, 0, v & 0xFFFFFFFF),
                  why="GOLD=%d (+0x4ec)" % v)
        _log("   applied: gold stored and served (0x2024 maskA 0x%08X)" % _GOLD_BIT)
    elif verb == "score":
        v = int(fields[0])
        fw._store_char_field(a, "total_score", v)
        ctx.reply(0x2024, struct.pack(">III", 0, _SCORE_BIT_B, v & 0xFFFFFFFF),
                  why="TOTAL SCORE=%d (+0x500)" % v)
        _log("   applied: total_score stored and served (0x2024 maskB bit 1)")
    elif verb == "crystal":
        v = int(fields[0])
        fw._store_char_field(a, "crystal", v)
        fw.crystal_push(c, o, m, b, a, value=v)
        _log("   applied: crystal stored and served (0x2035 bit 1)")
    elif verb == "exp":
        v = int(fields[0])
        cur = fw.exp_seed(a)
        fw.combat_exp_push(c, o, m, b, a, v - cur)
        _log("   applied: exp %d -> %d through combat_exp_push (stored)" % (cur, v))
    elif verb == "pos":
        x, y, z = fields
        fld = ctx.session.get("field")
        if fld is None:
            _log("   no current field in the session -- NG 2")
            debug_ng(ctx, 2)
            return
        fw.goto_push(c, o, m, b, a, int(fld), pos=(float(x), float(y), float(z)))
        _log("   applied: same-field warp via 0x1166 to (%g, %g, %g)" % (x, y, z))
    else:
        _log("   %s is decoded but has NO server-side setter here "
             "(power/stamina: 0x2024 bit not pinned; token/fame/item/"
             "endurance/break: another worker's lane) -- NG 2" % verb)
        debug_ng(ctx, 2)
        return
    debug_ok(ctx)


# ---------------------------------------------------------------------------
# 0xE009  GM system message request -> 0xE00B notify
def on_sysmsg_request(ctx, inner):
    f = inner[2:]
    if not f:
        return
    sub = f[0]
    name, i = _cstr(f, 1)
    text, i = _cstr(f, i)
    verb = SYSMSG_SUBS.get(sub, "sub%d" % sub)
    _log("0xE009 %s (sub %d) name=%r text=%r from %r" % (verb, sub, name,
                                                         text, _account(ctx)))
    if not is_gm(ctx):
        _log("   not a GM account: logged only (no 0xE00C sent -- it is a "
             "registered reply with no dispatcher arm; nothing waits)")
        return
    myfield = ctx.session.get("field")
    payload = {"title": name, "text": text, "sub": sub, "field": myfield}
    if sub == 0:
        n = fw.ext_post("gm.sysmsg", payload, include_me=True)
        scope = "everyone"
    else:
        n = fw.ext_post("gm.sysmsg", payload, include_me=True,
                        to=lambda s, nm: s.get("field") == myfield)
        scope = "field %s" % myfield
    _log("   -> 0xE00B queued for %d session(s) (%s). WARNING: sub->scope mapping "
         "is OURS, not measured." % (n, scope))


def on_relay_sysmsg(ctx, p):
    sysmsg_push(ctx, p["title"], p["text"])


# ---------------------------------------------------------------------------
# 0xA00D  MSG_NPC_GENERATE_REQUEST -> spawn through feworld.npc_send
def decode_npc_generate(f):
    if len(f) < 2:
        return None
    num = f[0]
    name, i = _cstr(f, 1)
    if len(f) < i + 24:
        return num, name, None, None
    pos = struct.unpack_from(">fff", f, i)
    d = struct.unpack_from(">fff", f, i + 12)
    return num, name, pos, d


def on_npc_generate(ctx, inner):
    d = decode_npc_generate(inner[2:])
    if d is None:
        _log("0xA00D: short body %s" % inner[2:].hex())
        return
    num, name, pos, dirn = d
    _log("0xA00D MSG_NPC_GENERATE_REQUEST name=%r num=%d pos=%s dir=%s from %r"
         % (name, num, pos, dirn, _account(ctx)))
    if not is_gm(ctx):
        _log("   not a GM account: logged only")
        return
    if pos is None:
        _log("   no position in the body -- nothing spawned")
        return
    types = fw.fegamedata.npc_types()
    hit = [(t, v) for t, v in types.items()
           if str(v["name"]).lower() == name.lower()]
    if not hit:
        _log("   no fet_npc_type named %r -- nothing spawned" % name)
        return
    tid, row = sorted(hit)[0]
    mtype = int(row["modeltype"])
    if mtype not in fw.MONSTER_MODELTYPE_IDS:
        _log("   modeltype %d is not in data_NPC_ModelType.dat -- refused "
             "(an unknown modeltype is a null deref a few frames later)" % mtype)
        return
    level = int(fw.fegamedata.npc_level(mtype, tid) or 1)
    base = int(getattr(ctx.args, "monster_base", 400)) + 200
    n0 = ctx.session.get("gm_spawn_n", 0)
    a = ctx.args
    for k in range(max(1, min(int(num), 16))):
        obj = base + n0 + k
        x, y, z = pos
        # spread a group by 1.5 units on x so they do not stack
        fw.npc_send(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a, obj, mtype,
                    0, level, x + 1.5 * k, y, z, why="(0xA00D %s x%d)" % (name, num),
                    name=str(row["name"]), type_id=tid)
    ctx.session["gm_spawn_n"] = n0 + max(1, min(int(num), 16))
    _log("   spawned %d x %s (type %d, model %d, level %d) as kind 0 "
         "MONSTERS -- registered for 0xA011 hits" % (
             max(1, min(int(num), 16)), row["name"], tid, mtype, level))


# ---------------------------------------------------------------------------
# 0xF1xx / 0xF3xx / 0xF5xx -- the 0xF900 siblings
def on_f100(ctx, inner):
    f = inner[2:]
    guess = ""
    if len(f) >= 6:
        item, num = struct.unpack_from(">HI", f, 0)
        guess = " best-effort {u16 item=%d; u32 num=%d}" % (item, num)
    _log("0xF100 popitem %d B: %s%s -- schema [0x5339998] unopened; items "
         "are feitems' lane; NOT applied" % (len(f), f.hex(), guess))


def on_f300(ctx, inner):
    f = inner[2:]
    obj = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else None
    _log("0xF300 RequestDamageMap object_id=%s (%s) -- F301/F302 replies "
         "unopened; logged only" % (obj, f.hex()))


def on_f303(ctx, inner):
    f = inner[2:]
    ln = struct.unpack_from(">H", f, 0)[0] if len(f) >= 2 else 0
    text = f[2:2 + ln].decode("cp932", "replace")
    _log("0xF303 unknown /command forwarded by the client: %r (len %d) from %r"
         % (text, ln, _account(ctx)))
    if str(getattr(ctx.args, "gm_slash", "verbs")) != "verbs":
        return
    if not is_gm(ctx):
        _log("   not a GM account: logged only")
        return
    line = "!" + text.lstrip("/")
    if any(fn(ctx, line) for fn in fw.EXT_GM):
        _log("   -> handled as %r" % line)
    else:
        _log("   -> no `!` verb took %r" % line)


def on_f500(ctx, inner):
    f = inner[2:]
    _log("0xF500 sub=%s (%s) -- GM-gated debug window button; logged only"
         % (f[0] if f else None, f.hex()))


def on_f501(ctx, inner):
    f = inner[2:]
    vals = struct.unpack_from(">HHH", f, 0) if len(f) >= 6 else None
    _log("0xF501 %s (%s) -- logged only" % (vals, f.hex()))


# ---------------------------------------------------------------------------
# `!verbs`
def _split(rest, n):
    parts = [p.strip() for p in rest.split("|")]
    return parts + [""] * (n - len(parts))


def gm(ctx, line):
    if line.startswith("!popup "):
        secs, text = _split(line[7:], 2)
        try:
            secs = int(secs, 0)
        except ValueError:
            _log("!popup wants SECS|TEXT")
            return True
        popup_push(ctx, secs, text)
        return True
    if line.startswith("!notice "):
        killer, victim, kind, side = _split(line[8:], 4)
        try:
            kind = int(kind, 0) if kind else 1
            side = int(side, 0) if side else 0xFF
        except ValueError:
            _log("!notice wants KILLER|VICTIM[|KIND[|SIDE]]")
            return True
        notice_push(ctx, killer, victim, kind, side)
        return True
    if line.startswith("!sysmsg"):
        rest = line[7:].strip()
        if rest == "clear" or not rest:
            sysmsg_push(ctx, "", "@clear@")
            return True
        title, text = _split(rest, 2)
        if not text:
            title, text = "GM", title
        sysmsg_push(ctx, title, text)
        return True
    if line.startswith("!dialog "):
        flag, a, b = _split(line[8:], 3)
        try:
            flag = int(flag, 0)
        except ValueError:
            _log("!dialog wants FLAG|A|B")
            return True
        dialog_push(ctx, flag, a, b)
        return True
    if line.startswith("!npcflag "):
        uid, flag = _split(line[9:], 2)
        try:
            uid, flag = int(uid, 0), int(flag or "1", 0)
        except ValueError:
            _log("!npcflag wants ID|0|1")
            return True
        npcflag_push(ctx, uid, flag)
        return True
    if line.startswith("!gm "):
        # re-inject a 0x208C locally: `!gm hp 150`, `!gm gold 500`,
        # `!gm pos 1.0|2.0|3.0`. Same code path as the client's /chr, minus
        # the account gate -- the operator's console is trusted.
        verb, rest = (line[4:].strip().split(" ", 1) + [""])[:2]
        sub = next((s for s, (v, _) in DEBUG_SUBS.items() if v == verb), None)
        if sub is None:
            _log("!gm verbs: " + " ".join(v for v, _ in DEBUG_SUBS.values()))
            return True
        fmt = DEBUG_SUBS[sub][1]
        try:
            if fmt == "fff":
                vals = [float(v) for v in _split(rest, 3)]
            elif fmt in ("H", "I", "II"):
                vals = [int(v, 0) for v in _split(rest, len(fmt))]
            else:
                vals = []
        except ValueError:
            _log("!gm %s wants %d value(s)" % (verb, len(fmt)))
            return True
        body = bytes([sub]) + struct.pack(">I", _self_unit(ctx))
        if fmt in ("fff", "H", "I", "II"):
            body += struct.pack(">" + fmt, *vals)
        saved = getattr(ctx.args, "gm_accounts", "")
        ctx.args.gm_accounts = "*"
        try:
            on_debug_command(ctx, struct.pack(">H", 0x208C) + body)
        finally:
            ctx.args.gm_accounts = saved
        return True
    return False
