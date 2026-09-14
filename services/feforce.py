"""feforce.py -- Fantasy Earth FORCE / army administration + the CHAT NG family.

PARTIAL: EVERYTHING IN THIS MODULE IS BUILT AND UNTESTED LIVE (2026-09-10). Nothing
here is "fixed" or "working" until it has been seen on a live client screen; the
docstrings say what was MEASURED off the client dump (FE_Client.dll runtime
dump, base 0x04F90000, static disassembly) and what was CHOSEN.

WHAT A "FORCE" IS HERE
----------------------
The served path already in feworld.py is unambiguous: a Force is a NATION.
`0x206A MSG_GET_FORCE_INFO_REQUEST ForceID=%d` is asked for 1..5, its
`0x3027` record carries `m_KingMessage`, `m_NumTerritory`, `m_CultureLevel`,
and `0x4012 MSG_JOIN_FORCE_REQUEST ForceID=%d` is the nation choice the
character store keeps under the key `force` (felobby CHAR_FIELDS -> 0xD002).
The guild-shaped names in the client's NG table -- MSG_CREATE_FORCE_NG,
MSG_CANVASS_FORCE_MEMBER_NG, MSG_FORCE_BREAKUP_NG, MSG_FORCE_EXPLUSION_NG,
MSG_FORCE_WITHDRAWAL_NG, MSG_JUDGE_JOIN_FORCE_NG -- are VESTIGIAL IN THIS
BUILD, measured three ways (the client's NG table regenerated without its
msgid filter):

  * every one of their 0x70-byte NG records has msgid 0 at +0x50 and, in the
    TEXT slot at +0x0c, a `MSG_CA_*` / `MSG_AC_*` placeholder name instead of
    a message -- the Common<->Area server-internal names, never shown;
  * no `< MSG_*_REQUEST` builder log string exists for any of them (the only
    force builders in the image are 0x206A, 0x4012, 0x4013, 0x206B, 0x20A9);
  * no immediate compare against a msgid for them exists anywhere in code.

So there is no client verb for "create a force" or "expel a member": the
model below is SERVER-SIDE ONLY, driven by `!force` lines, and the client
sees its consequences through the two channels it does have -- the nation
record (0x3027, re-asked on every world entry) and the join answer
(0x3033/0x3034) plus the stored `force` field that decides whether the
nation prompt shows on the next login (scene mode 6, 0x04ffc7d0: force 0 or
0x7FFFFFFF -> NationSelect).

MEASURED ARMS (every reply this module sends, in the order the client reads)
--------------------------------------------------------------------------
Stream primitives (fe_ui_test.py): 0x5045dc0 u8, 0x5045e30 u16, 0x5045e60
u32, 0x5045f50 cstr, 0x5045e90 1 byte, 0x5045ec0 u16 (advances [0x5338360]
by 2, reader 0x522f880 -- re-measured here).

  0x3017  main arm 0x050569da: gate [0x53371cc] != 0, then 0x5009550 ->
          [obj+0x14] = 1. HEADER-ONLY. It is the NG of 0x400A (0x3016's arm
          0x050569f4 -> 0x5009540 sets +0x14 = 0). Unnamed in the image;
          the label below is INVENTED from the OK's own log string.
  0x3019  main arm 0x05056a22: gate [0x53371cc], then 0x50095a0 ->
          [obj+0x18] = 1. HEADER-ONLY. The NG of 0x400B (0x3018 -> 0x5009590
          sets +0x18 = 0). WARNING: The 08-27 sweep called this
          DISTRIBUTE_GROUPINFO_LIST_NOTIFY; that string (0x52d5b54) is logged
          by 0x3030's arm at 0x05056a59, not by this one.
  0x301F  main arm 0x05056ab0: [player(0x5336d1c)+0x299] = 1. HEADER-ONLY.
  0x3020  main arm 0x05056ac4: [player+0x299] = 0. HEADER-ONLY. No reader of
          +0x299 was found (a `99020000` scan of the image hits only these
          two setters; every other hit is a jump displacement), so what
          polls the byte is UNMEASURED. Unnamed; served as a probe only.
  0x3029  main arm 0x05056ad7 -> 0x5051540: u32 KEY, then 0x5051160 walks the
          list at [[0x5339d14]+4] for a node with [node+0x18] == KEY. MISS ->
          returns WITHOUT reading further (so a probe must echo a key the
          client asked about). HIT -> 0x5050d20(node, stream): u32 MASK then
          bit-gated fields, bits 0..17 (0x3FFFF is exactly what the 0x206B
          builder asks for):
              bit0  cstr  (local, discarded)     bit9   u8(0x5045e90) local
              bit1  u8    (local)                bit10  u16 local
              bit2  cstr  -> node+0x1c (0x100)   bit11  u16 -> node+0x42
              bit3  u8    -> node+0x3d           bit12  u16 local
              bit4  u8    -> node+0x3e           bit13  u16 local
              bit5  u8    -> node+0x3f           bit14  u16 local
              bit6  u8    local                  bit15  u32 -> node+0x44
              bit7  u8    local                  bit16  u32 local
              bit8  u8    local                  bit17  u32 local
          then [node+0x54] = 0 (the builder set it to 2 = pending).
  0x302A  main arm 0x05056af6 -> 0x5051580: u32 KEY, same lookup, HIT ->
          0x5050f30: u32 (local), u32 CODE -> 0x50613e0(0x302A, CODE),
          [node+0x54] = 1. The NG table has NO 0x302A rows, so the text
          lookup (0x5061240) misses and nothing is drawn.
  0x3028  screen arm 0x050058d7 (via 0x05056b55 -> dispatcher 0x050057c0,
          id-0x3027 byte table 0x5005958 / jump table 0x5005944):
          u32 ForceID, u32 Bit, u32 ErrorID -> 0x50613e0(0x3028, ErrorID),
          [scr+0x50] = 3 (the same "answered" state the OK sets). No 0x3028
          rows in the NG table -> nothing drawn.
  0x3034  screen arm 0x0500580e: u32 CODE -> [scr+0x4c], [scr+0x54] = 1,
          0x5061290(0x3034, CODE). WARNING: the inventory's "reads a cstr" is wrong:
          the call at 0x05005816 is 0x5045e60 = u32. No 0x3034 NG rows.
  0x3035  main arm 0x05056b55 (jump table 0x50576e0, index 2) -> screen
          dispatcher 0x050057c0, whose window is 0x3027..0x3034: 0x3035 - 0x3027
          = 0xE > 0xD -> `ja 0x500593a` = pop/ret. NO CONSUMER. Probe only.
  0x1177 / 0x1178  registered by the 0x20A9 builder (0x050c7560, `push 2`,
          words 0x1177/0x1178 at [esp+0x10]/[esp+0x12]) with the reply
          registry [0x535cec8] (vtable 0x5298608). NOT in any c-dispatcher.
          The registry's match path 0x51b5cc0 -> 0x51b5d15 logs
          `erase >>> 0x%04x` (0x52f25d0, the REQUEST id) and erases the entry
          (0x51b6250); it never reads the stream and never calls back into
          the screen -- the screen polls "busy?" (0x51b5d50: [this+0x10]).
          So both are HEADER-ONLY. shop_page_body does NOT match (nothing
          reads a page), so no page is sent.

  CHAT NG family -- one shared table, ten 0x70-byte records at 0x05266100
  (the NG lookup 0x5061240 scans 0x52620b0..0x52836e0 in 0x70 steps: up to
  16 name pointers at +0x00, up to 16 u16 ids at +0x40, CODE at +0x60,
  display KIND at +0x68, TEXT at +0x6c). Ids per record (+0x40):
      0x206D MSG_ALL_CHAT_NG   0x206E MSG_ARMY_CHAT_NG   0x206C MSG_CHAT_NG
      0x206F MSG_FORCE_CHAT_NG 0x1180 MSG_WHISPER_NG
      (0x603A/0x603E/0x6038/0x603C/0x6040 = the MSG_AC_* server-internal ids)
  Codes 0..9; kind 2 = "system error" for 0..7, kind 4 = a plain notice for
  8 and 9 (CHAT_NG_TEXT below, the client's own cp932 strings).
  0x1180  main arm 0x05053ad3: u32 CODE -> 0x50613e0(0x1180, CODE) ->
          0x5061240 lookup -> 0x50612b0 render: kind 4 -> 0x5121840(0xbb8,
          text), a chat-log line. NOT poster-gated; drawn straight away.
  0x206C..0x206F  main arm 0x05053e02: builds {stream, id} (0x509fca0) and
          POSTS it (0x509fc50) to every listener in [0x53432d8] via
          vtable+0x54. The arm itself reads NOTHING. No code in the image
          compares against 0x206C..0x206F, so no listener that reads a code
          off the event was found -- whether anything is drawn is
          UNMEASURED. Default body: none (what the arm reads); --chat-ng-body
          u32 is the probe.
  0x113D  MSG_PARTY_CHAT_NG: in the NG table with codes 0/1/2/8 (kind 2).
          Its arm is not opened here (party is another module's feature);
          it is in the request->NG map so chat_refuse() can name it, and
          `!chatng` will send it with the same body rule as 0x206C.

THE WINDOW-POSTER GATE. The brief asks for it on every force reply: NONE of
the force replies above go through the poster 0x0509fc50. Their gates are
object-presence tests (GATES below) and are logged on every send so a silent
no-op on screen can be read off the server log. The only poster users in
this module are 0x206C..0x206F.

REQUESTS (opened from their builders)
  0x4013  MSG_INVENTORY_STATUS_INFO_REQUEST (0x52d2fa4; builder 0x050056e0:
          beginMessage, flush, NO body, registers NO reply) -- and
          an xref sweep of 050056e0 is EMPTY: the builder has no caller in
          this build. Dead. Logged if it ever arrives; nothing is owed.
  0x206B  UNNAMED. Builder 0x05050ca0 (class vtable 0x5261fe8, the same class
          whose next method 0x050511a0 builds 0x112E CANVASS_PARTY_MEMBER):
          `[u32 node+0x18][u32 0x3FFFF]`, sent by the node tick 0x5050c80
          when [node+0x52] is set, then [node+0x54] = 2 = pending. Answered
          by 0x3029 / 0x302A (above). Label INVENTED: "node info request".
          WARNING: what the node KEY is (a force id or a character id) is not
          settled: 0x0503af44 in the MSG_ADD unit path looks up
          [unit+0x3c0] in the same list and sets [unit+0x9f8] = 1 on a hit.
  0x20A9  MSG_GET_FORCE_SHOP_ITEM_REQUEST ForceID=%d (0x52e20a8; builder
          0x050c7560: `[u32 [scr+0x70]]`, registers 0x1177/0x1178).
  0x4012  MSG_JOIN_FORCE_REQUEST ForceID = %d -- OVERRIDDEN ON PURPOSE (the
          seam's override=True) so a join can be JUDGED: --force-join auto
          reproduces feworld's builtin byte for byte (0x3033 header-only,
          unit id 0, _store_char); ng:CODE answers 0x3034 [u32 CODE] and
          stores nothing.
  0x2067  /tell -- OVERRIDDEN ON PURPOSE so a whisper to nobody can be
          REFUSED with 0x1180 code 8 ("that character does not exist or is
          not logged in") instead of the silent drop chat_relay does today.
          --chat-ng off (default) reproduces the builtin exactly.

NOT DONE, AND WHY: no client verb exists for create/canvass/breakup/expel/
withdraw/boss-word/judge (see above), so those are `!force` lines that move
the server model; the client learns of them on its next 0x206A / next login.
Which listener (if any) draws 0x206C..0x206F is unmeasured. What polls
[player+0x299] is unmeasured.
"""
import json
import os
import struct
import threading

fw = None   # the feworld module, handed in by register()

# ---------------------------------------------------------------------------
# Names. The client's own where a log string / NG-table name exists; INVENTED
# labels say so.
# ---------------------------------------------------------------------------
NAMES = {
    0x4013: "MSG_INVENTORY_STATUS_INFO_REQUEST (no body, no reply, NO CALLER)",
    0x206B: "node info request [u32 key][u32 mask 0x3FFFF] -> 0x3029/0x302A "
            "(label INVENTED; builder 0x05050ca0)",
    0x20A9: "MSG_GET_FORCE_SHOP_ITEM_REQUEST [u32 ForceID] -> 0x1177 OK / "
            "0x1178 NG, both header-only (registry-erased, no arm)",
    0x3017: "NG of 0x400A REGISTER_DISTRIBUTER_OF_FIELDINFO (label INVENTED; "
            "[obj+0x14]=1) header-only",
    0x3019: "NG of 0x400B (label INVENTED; [obj+0x18]=1) header-only",
    0x301F: "player flag [player+0x299]=1 (unnamed) header-only",
    0x3020: "player flag [player+0x299]=0 (unnamed) header-only",
    0x3028: "MSG_GET_FORCE_INFO_NG [u32 ForceID][u32 Bit][u32 ErrorID]",
    0x3029: "node info OK [u32 key][u32 mask]+gated (label INVENTED)",
    0x302A: "node info NG [u32 key][u32 x][u32 code] (label INVENTED)",
    0x3034: "MSG_JOIN_FORCE_NG [u32 code]",
    0x3035: "(no consumer: screen dispatcher 0x050057c0 bails) header-only",
    0x1177: "MSG_GET_FORCE_SHOP_ITEM OK (label from the request) header-only",
    0x1178: "MSG_GET_FORCE_SHOP_ITEM NG (label from the request) header-only",
    0x206C: "MSG_CHAT_NG", 0x206D: "MSG_ALL_CHAT_NG",
    0x206E: "MSG_ARMY_CHAT_NG", 0x206F: "MSG_FORCE_CHAT_NG",
    0x1180: "MSG_WHISPER_NG [u32 code]", 0x113D: "MSG_PARTY_CHAT_NG",
}

# What must be non-NULL for the arm to do anything (see the docstring). Logged
# on every send: "gate" is the client object, "arm" the VA that tests it.
GATES = {
    0x3017: ("[0x53371cc] distributer object", "0x050569da"),
    0x3019: ("[0x53371cc] distributer object", "0x05056a22"),
    0x301F: ("[0x5336d1c] player (unconditional write)", "0x05056ab0"),
    0x3020: ("[0x5336d1c] player (unconditional write)", "0x05056ac4"),
    0x3028: ("[0x5336fa8] force screen; ForceID must resolve", "0x050058d7"),
    0x3029: ("[0x5339d14] node list; KEY must be a node", "0x05056ad7"),
    0x302A: ("[0x5339d14] node list; KEY must be a node", "0x05056af6"),
    0x3033: ("[0x5336fa8] force screen", "0x050057ec"),
    0x3034: ("[0x5336fa8] force screen", "0x0500580e"),
    0x3035: ("NONE -- no consumer (0x050057c0 bails)", "0x05056b55"),
    0x1177: ("registry entry for 0x20A9 (erase >>> 0x20a9)", "0x051b5d15"),
    0x1178: ("registry entry for 0x20A9 (erase >>> 0x20a9)", "0x051b5d15"),
    0x1180: ("none: drawn by 0x50612b0 directly", "0x05053ad3"),
    0x206C: ("POSTER 0x0509fc50 -> listeners [0x53432d8]", "0x05053e02"),
    0x206D: ("POSTER 0x0509fc50 -> listeners [0x53432d8]", "0x05053e02"),
    0x206E: ("POSTER 0x0509fc50 -> listeners [0x53432d8]", "0x05053e02"),
    0x206F: ("POSTER 0x0509fc50 -> listeners [0x53432d8]", "0x05053e02"),
    0x113D: ("arm not opened here (party module)", "?"),
}

HEADER_ONLY_PUSHES = (0x3017, 0x3019, 0x301F, 0x3020, 0x3035)

# ---------------------------------------------------------------------------
# The CHAT NG family.
# ---------------------------------------------------------------------------
#: chat REQUEST id -> the NG id the client's table pairs with that channel.
#: 0x2065/0x2066 are "/army" and "/army, second form" in feworld._CHAT_IDS;
#: which of ARMY_CHAT_NG / FORCE_CHAT_NG belongs to which is INFERRED from
#: the id order, not measured.
CHAT_NG_FOR = {
    0x201A: 0x206C,   # /say  -> MSG_CHAT_NG
    0x201B: 0x206D,   # /all  -> MSG_ALL_CHAT_NG
    0x2065: 0x206E,   # /army -> MSG_ARMY_CHAT_NG      (INFERRED pairing)
    0x2066: 0x206F,   # /army 2nd form -> MSG_FORCE_CHAT_NG (INFERRED)
    0x208A: 0x113D,   # /party -> MSG_PARTY_CHAT_NG
    0x2067: 0x1180,   # /tell -> MSG_WHISPER_NG
}
CHAT_NG_IDS = frozenset((0x206C, 0x206D, 0x206E, 0x206F, 0x1180, 0x113D))

#: code -> (kind, text) off the ten records at 0x05266100 (+0x60, +0x68,
#: +0x6c). The texts are the client's; the English gloss is ours.
CHAT_NG_TEXT = {
    0: (2, "システムエラー / タイムアウトしました。 (timed out)"),
    1: (2, "システムエラー / タスクを生成できません。 (cannot create task)"),
    2: (2, "システムエラー / タスクが登録できません。 (cannot register task)"),
    3: (2, "システムエラー / メッセージパラメータが不足しています。 (short params)"),
    4: (2, "システムエラー / キャラクタオブジェクトが存在しません。 (no char object)"),
    5: (2, "システムエラー / キャラクタが部隊に所属していません。 (not in a force)"),
    6: (2, "システムエラー / 指定の部隊が存在しません。 (no such force)"),
    7: (2, "システムエラー / タスクの状態が不正です。 (bad task state)"),
    8: (4, "指定したキャラクタは現在存在していないか、ログインしていないため届きません "
           "(target does not exist / is not logged in)"),
    9: (4, "指定したキャラクタは現在チャットが届かないところにいるか、移動中のため"
           "届きません (target is out of reach / moving)"),
}
CHAT_NG_NOT_LOGGED_IN = 8
CHAT_NG_OUT_OF_REACH = 9

# ---------------------------------------------------------------------------
# The force model: nation rows overlaid on args.force_table, persisted.
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()
_STATE = {"loaded": False, "forces": {}, "judge": None, "last_node_key": None}
_ALL_CHARS = None      # test hook: () -> [(account, char dict)]


def force_path(args):
    """data/fe_force.json beside fe_territory.json (same resolution rule as
    feworld.territory_path); --force-file overrides."""
    p = getattr(args, "force_file", None)
    if p:
        return p
    here = getattr(fw, "_HERE", os.path.dirname(os.path.abspath(__file__)))
    d = os.path.normpath(os.path.join(here, os.pardir, "data"))
    return os.path.join(d if os.path.isdir(d) else here, "fe_force.json")


def _load(args):
    """Overlay the persisted rows onto args.force_table ONCE per process.

    feworld's 0x206A arm (builtin, not overridden) reads args.force_table,
    which apply_force_targets built from --forces at start-up. A `!force`
    edit lands in that same dict, so the next GET_FORCE_INFO serves it; the
    JSON file is what survives a restart.
    """
    with _LOCK:
        if _STATE["loaded"]:
            return
        _STATE["loaded"] = True
        path = force_path(args)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return
        _STATE["judge"] = doc.get("judge")
        table = getattr(args, "force_table", None)
        for k, vals in (doc.get("forces") or {}).items():
            try:
                fid = int(k)
            except ValueError:
                continue
            _STATE["forces"][fid] = dict(vals)
            if isinstance(table, dict):
                table.setdefault(fid, {}).update(vals)
        print("[feforce] loaded %d force row(s) from %s"
              % (len(_STATE["forces"]), path), flush=True)


def _save(args):
    path = force_path(args)
    doc = {"forces": {str(k): v for k, v in _STATE["forces"].items()},
           "judge": _STATE["judge"]}
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print("[feforce] WARNING: could not write %s: %s" % (path, e), flush=True)
        return False


def force_rows(args):
    """{ForceID: vals} -- args.force_table (the --forces seed) with the
    persisted overlay applied."""
    _load(args)
    table = getattr(args, "force_table", None) or {}
    out = {k: dict(v) for k, v in table.items()}
    for k, v in _STATE["forces"].items():
        out.setdefault(k, {}).update(v)
    return out


def force_set(args, fid, key, value):
    """Mutate one field of one force, in the live table AND the file."""
    _load(args)
    with _LOCK:
        _STATE["forces"].setdefault(fid, {})[key] = value
        table = getattr(args, "force_table", None)
        if isinstance(table, dict):
            table.setdefault(fid, {})[key] = value
    return _save(args)


def force_remove(args, fid):
    _load(args)
    with _LOCK:
        _STATE["forces"].pop(fid, None)
        table = getattr(args, "force_table", None)
        if isinstance(table, dict):
            table.pop(fid, None)
    return _save(args)


def judge_policy(args):
    """('ok', 0) or ('ng', code): what the next 0x4012 gets.

    The `!force judge` line wins over --force-join; both default to ok, which
    is the builtin behaviour."""
    _load(args)
    spec = _STATE["judge"] or getattr(args, "force_join", "auto") or "auto"
    spec = str(spec).strip().lower()
    if spec.startswith("ng"):
        _, _, code = spec.partition(":")
        try:
            return ("ng", int(code or "0", 0))
        except ValueError:
            return ("ng", 0)
    return ("ok", 0)


# --- membership --------------------------------------------------------------
def _all_chars():
    """[(account, char)] over EVERY stored account. The test swaps this."""
    if _ALL_CHARS is not None:
        return _ALL_CHARS()
    try:
        import felobby
    except ImportError:
        return []
    store = felobby._default_store()
    out = []
    for acct in felobby.store_accounts(store) or []:
        for c in felobby.load_roster(store, acct) or []:
            out.append((acct, c))
    return out


def _set_char_force(account, charid, fid):
    """Write `force` on one stored character of any account (the same key
    0x4012 writes for the session's own character)."""
    try:
        import felobby
    except ImportError:
        return False

    def mutate(roster):
        hit = False
        for c in roster:
            if c.get("charid") == charid:
                c["force"] = fid
                hit = True
        return hit
    return bool(felobby.update_roster(felobby._default_store(), account, mutate))


def members(args, fid=None):
    """[(account, name, charid, force)] -- every stored character, or only
    those in force `fid`."""
    rows = []
    for acct, c in _all_chars():
        f = c.get("force")
        if fid is None or f == fid:
            rows.append((acct, c.get("name"), c.get("charid"), f))
    return rows


def _find_char(args, who):
    """A character by name (case-folded) or by charid."""
    for acct, c in _all_chars():
        if str(c.get("charid")) == str(who) or \
                str(c.get("name", "")).casefold() == str(who).casefold():
            return acct, c
    return None


# ---------------------------------------------------------------------------
# Senders. Every force reply is sent with unit id 0 in the header, matching
# feworld's builtin 0x3027/0x3033/0x3016/0x3018 sends (inner_msg default) --
# the shape verified live; the screen arms never read the header id.
# ---------------------------------------------------------------------------
def _gate_line(mid):
    gate, arm = GATES.get(mid, ("?", "?"))
    return "gate: %s (arm %s)" % (gate, arm)


def push(ctx, mid, body=b"", why=None):
    name = why or NAMES.get(mid, "")
    ctx.reply(mid, body, unit_id=0, why=name)
    print("[feforce]    0x%04X %s" % (mid, _gate_line(mid)), flush=True)
    return mid


def node_ok_body(key, mask=0, fields=None):
    """0x3029 body for the 0x5050d20 parser (see the module docstring).

    `fields` maps bit -> value; bits absent from `fields` but set in `mask`
    are sent as zero / empty of their measured width. Widths per bit:
    0 cstr, 1 u8, 2 cstr, 3..9 u8, 10..14 u16, 15..17 u32."""
    fields = fields or {}
    out = struct.pack(">II", key & 0xFFFFFFFF, mask & 0x3FFFF)
    for bit in range(18):
        if not mask & (1 << bit):
            continue
        v = fields.get(bit, 0)
        if bit in (0, 2):
            out += (v or "").encode("cp932", "replace")[:0xFF] + bytes(1)
        elif bit <= 9:
            out += struct.pack(">B", int(v) & 0xFF)
        elif bit <= 14:
            out += struct.pack(">H", int(v) & 0xFFFF)
        else:
            out += struct.pack(">I", int(v) & 0xFFFFFFFF)
    return out


def node_ng_body(key, code, x=0):
    """0x302A: [u32 key][u32 x (read, unused)][u32 code]."""
    return struct.pack(">III", key & 0xFFFFFFFF, x & 0xFFFFFFFF,
                       code & 0xFFFFFFFF)


def force_info_ng_body(force_id, bits, err):
    """0x3028: [u32 ForceID][u32 Bit][u32 ErrorID] (arm 0x050058d7)."""
    return struct.pack(">III", force_id & 0xFFFFFFFF, bits & 0xFFFFFFFF,
                       err & 0xFFFFFFFF)


def join_ng_body(code):
    """0x3034: [u32 code] (arm 0x0500580e)."""
    return struct.pack(">I", code & 0xFFFFFFFF)


def chat_ng_body(ng_id, code, args=None):
    """0x1180 reads [u32 code] (0x05053ad3). 0x206C..0x206F read NOTHING in
    the arm (0x05053e02 posts the stream to listeners); --chat-ng-body u32
    appends the code anyway as the probe for whether a listener reads it."""
    if ng_id == 0x1180:
        return struct.pack(">I", code & 0xFFFFFFFF)
    if getattr(args, "chat_ng_body", "none") == "u32":
        return struct.pack(">I", code & 0xFFFFFFFF)
    return b""


def chat_refuse(ctx, req_id, code, ng_id=None):
    """REFUSE one chat line with the NG the client's table pairs with its
    channel. `req_id` is the request the client sent (0x201A /say, 0x2067
    /tell, ...) or an NG id directly. Returns the NG id sent, or None.

    This is the function feworld's chat code can call instead of dropping a
    line silently. Note what it can and cannot show: 0x1180 code 8/9 draws a
    chat-log notice (measured render path); every other id/code is either a
    "system error" kind or posted to listeners nobody has been seen to read.
    """
    if ng_id is None:
        ng_id = req_id if req_id in CHAT_NG_IDS else CHAT_NG_FOR.get(req_id)
    if ng_id is None:
        print("[feforce]    chat_refuse: 0x%04X is not a chat request and "
              "not an NG id -- nothing sent" % req_id, flush=True)
        return None
    kind, text = CHAT_NG_TEXT.get(code, (None, "(code not in the table)"))
    body = chat_ng_body(ng_id, code, ctx.args)
    ctx.reply(ng_id, body, unit_id=0,
              why="%s code=%d" % (NAMES.get(ng_id, "CHAT NG"), code))
    print("[feforce]    0x%04X %s; kind %s: %s%s"
          % (ng_id, _gate_line(ng_id), kind, text,
             "" if body else " -- HEADER-ONLY (the arm reads nothing; "
             "--chat-ng-body u32 to probe)"), flush=True)
    return ng_id


# ---------------------------------------------------------------------------
# Inbound handlers
# ---------------------------------------------------------------------------
def on_inventory_status(ctx, inner):
    """0x4013 -- header-only, registers nothing, and its builder 0x050056e0
    has NO caller in this build. Nothing to answer; log the sighting, because
    seeing it at all would mean the caller exists somewhere we did not look."""
    print("[feforce]    0x4013 MSG_INVENTORY_STATUS_INFO_REQUEST arrived "
          "(%d body bytes) -- builder 0x050056e0 registers no reply and has "
          "no caller in the dump; nothing owed" % (len(inner) - 2), flush=True)


def on_node_info(ctx, inner):
    """0x206B [u32 key][u32 mask] -> 0x3029 (or 0x302A per --force-node)."""
    args = ctx.args
    f = inner[2:]
    key = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
    mask = struct.unpack_from(">I", f, 4)[0] if len(f) >= 8 else 0
    _STATE["last_node_key"] = key
    print("[feforce]    0x206B node info request key=%d mask=0x%05X "
          "(builder 0x05050ca0 asks 0x3FFFF)" % (key, mask), flush=True)
    spec = str(getattr(args, "force_node", "ok") or "ok").lower()
    if spec.startswith("ng"):
        _, _, code = spec.partition(":")
        try:
            code = int(code or "0", 0)
        except ValueError:
            code = 0
        push(ctx, 0x302A, node_ng_body(key, code),
             why="node info NG key=%d code=%d" % (key, code))
        return
    reply_mask = int(getattr(args, "force_node_mask", 0) or 0) & 0x3FFFF
    fields = _node_fields(args)
    push(ctx, 0x3029, node_ok_body(key, reply_mask, fields),
         why="node info OK key=%d mask=0x%05X%s"
         % (key, reply_mask, "" if reply_mask else
            " (mask 0: the parser reads nothing after it, [node+0x54]=0)"))


def _node_fields(args):
    """--force-node-field BIT=VALUE,... -> {bit: value}; strings for 0/2."""
    out = {}
    for part in (getattr(args, "force_node_field", None) or "").split(","):
        if "=" not in part:
            continue
        b, _, v = part.partition("=")
        try:
            b = int(b, 0)
        except ValueError:
            continue
        out[b] = v if b in (0, 2) else int(v, 0)
    return out


def on_force_shop(ctx, inner):
    """0x20A9 [u32 ForceID] -> 0x1177 OK / 0x1178 NG, both header-only.

    Neither id has an arm: the reply registry erases the pending 0x20A9
    (`erase >>> 0x20a9` in the client log) and the shop screen polls busy.
    What the screen does NEXT is the live question -- a stock-list request
    (0x2073/0x204A, already served by feworld) is the expectation, a blank
    screen the failure. --force-shop off keeps prod's silence."""
    args = ctx.args
    f = inner[2:]
    force_id = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
    mode = str(getattr(args, "force_shop", "ok") or "ok").lower()
    name = force_rows(args).get(force_id, {}).get("name", "?")
    print("[feforce]    0x20A9 MSG_GET_FORCE_SHOP_ITEM_REQUEST ForceID=%d (%s) "
          "-- registered 0x1177/0x1178; --force-shop %s"
          % (force_id, name, mode), flush=True)
    if mode == "off":
        print("[feforce]    0x20A9 left UNANSWERED (--force-shop off): the "
              "registry entry stays and the shop screen stays busy", flush=True)
        return
    push(ctx, 0x1178 if mode == "ng" else 0x1177)


def on_join_force(ctx, inner):
    """0x4012 MSG_JOIN_FORCE_REQUEST ForceID = %d -- the builtin, plus judging.

    The `ok` path is feworld's elif branch line for line (0x3033 header-only
    with unit id 0, then _store_char). Overriding a live-verified arm is the
    price of a judged join; fe_force_test pins the `ok` bytes against that
    shape so a drift here fails a test rather than a login."""
    args = ctx.args
    f = inner[2:]
    force_id = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
    name = force_rows(args).get(force_id, {}).get("name", "?")
    print("[feworld]    JOIN FORCE ForceID=%d (%s)" % (force_id, name),
          flush=True)
    verdict, code = judge_policy(args)
    if verdict == "ng":
        push(ctx, 0x3034, join_ng_body(code),
             why="MSG_JOIN_FORCE_NG code=%d (judged: refused; nothing stored, "
                 "the nation prompt returns next login)" % code)
        return
    push(ctx, 0x3033, why="MSG_JOIN_FORCE_OK (no fields)")
    if fw._store_char(args, force_id):
        print("[feworld]    saved nation ForceID=%d for %r/charid=%s -- served "
              "back as 0xD002 `force`, so the nation prompt will be SKIPPED on "
              "the next login"
              % (force_id, ctx.session.get("account"),
                 ctx.session.get("charid")), flush=True)


def on_tell(ctx, inner):
    """0x2067 /tell [cstr TARGET][cstr SENDER][cstr TEXT] -- feworld's chat
    branch reproduced (decode, room name, echo modes, relay), then the one
    addition: with --chat-ng on, a relay that reached NO session answers
    0x1180 MSG_WHISPER_NG code 8 -- the client's own "that character does
    not exist or is not logged in" line -- instead of silence."""
    args = ctx.args
    real_id = 0x2067
    name, want = fw._CHAT_IDS[real_id]
    want = want or 3
    f = inner[2:]
    parts = [p.decode("cp932", "replace") for p in f.split(bytes(1))[:want]]
    while len(parts) < want:
        parts.append("")
    speaker = parts[1]
    text = parts[-1]
    fw._chat_room_name(speaker)
    print("[feworld]    0x%04X CHAT %s %r: %r (to %r)"
          % (real_id, name, speaker, text, parts[0]), flush=True)
    echo = getattr(args, "chat_echo", "off")
    if echo == "gm":
        fw.gm_command(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                      "@%s: %s" % (speaker, text))
    elif echo == "say":
        fw.chat_send(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                     real_id, parts)
    # "self" is skipped for /tell in the builtin: the client logs its own
    # ">> target : text" (0x512c8b0 at 0x05133b22), so an echo would double it.
    if getattr(args, "chat_relay", "on") == "on":
        n = fw.chat_relay(real_id, parts)
        print("[feworld]    chat relay: queued for %d other session(s)%s"
              % (n, "" if n else " -- no in-field session signs as %r"
                 % parts[0]), flush=True)
        if n == 0 and getattr(args, "chat_ng", "off") == "on":
            chat_refuse(ctx, real_id, CHAT_NG_NOT_LOGGED_IN)


# ---------------------------------------------------------------------------
# !verbs
# ---------------------------------------------------------------------------
_HELP = """!force verbs (server-side model; the client has no verb for any of these):
  !force info                    force rows + members
  !force members [ID]            stored characters (by force)
  !force create ID NAME          add / rename a force row (persisted)
  !force breakup ID              drop a force row
  !force set ID KEY VALUE        any FORCE_FIELDS key (KingMessage, TargetFieldID, ...)
  !force kingmsg ID TEXT         = set ID KingMessage TEXT (the 'boss word', INFERRED)
  !force canvass WHO ID          put character WHO (name|charid) in force ID
  !force expel WHO               WHO's force -> 0 (nation prompt on next login)
  !force withdraw                this character's force -> 0
  !force judge ok|ng:CODE        policy for the next 0x4012 (persisted)
  !force ng 3017|3019|3028 [ForceID Bit Err]|3034 CODE   send an NG
  !force flag299 1|0             0x301F / 0x3020
  !force probe 3035              header-only 0x3035 (no consumer)
  !force node MASK [BIT=VAL,..]  0x3029 for the LAST 0x206B key
  !force nodeng CODE             0x302A for the LAST 0x206B key
!chatng ID [CODE]                send a chat NG (ID = NG id or the request id)"""


def gm(ctx, line):
    line = line.strip()
    if line.startswith("!chatng"):
        return _gm_chatng(ctx, line.split()[1:])
    if not line.startswith("!force"):
        return False
    argv = line.split()[1:]
    args = ctx.args
    verb = argv[0].lower() if argv else "help"
    try:
        if verb in ("help", "?"):
            print("[feforce] " + _HELP, flush=True)
        elif verb == "info":
            for fid, vals in sorted(force_rows(args).items()):
                print("[feforce]    force %d: %s" % (fid, json.dumps(
                    vals, ensure_ascii=False)), flush=True)
            for row in members(args):
                print("[feforce]    member %s/%s charid=%s force=%s" % row,
                      flush=True)
            print("[feforce]    judge policy: %s" % (judge_policy(args),),
                  flush=True)
        elif verb == "members":
            fid = int(argv[1], 0) if len(argv) > 1 else None
            for row in members(args, fid):
                print("[feforce]    member %s/%s charid=%s force=%s" % row,
                      flush=True)
        elif verb == "create":
            fid = int(argv[1], 0)
            force_set(args, fid, "name", " ".join(argv[2:]))
            print("[feforce]    force %d created/renamed -> served on the "
                  "next 0x206A" % fid, flush=True)
        elif verb == "breakup":
            fid = int(argv[1], 0)
            force_remove(args, fid)
            print("[feforce]    force %d removed -> 0x206A answers an EMPTY "
                  "record for it" % fid, flush=True)
        elif verb == "set":
            fid, key = int(argv[1], 0), argv[2]
            val = " ".join(argv[3:])
            keys = dict((k, kind) for kind, k, _ in fw.FORCE_FIELDS)
            if key not in keys:
                print("[feforce]    WARNING: %r is not a FORCE_FIELDS key" % key,
                      flush=True)
                return True
            if keys[key] != "cstr":
                val = int(val, 0)
            force_set(args, fid, key, val)
            print("[feforce]    force %d %s=%r -> served on the next 0x206A"
                  % (fid, key, val), flush=True)
        elif verb == "kingmsg":
            fid = int(argv[1], 0)
            force_set(args, fid, "KingMessage", " ".join(argv[2:]))
            print("[feforce]    force %d KingMessage set -> next 0x206A (the "
                  "'boss word' reading is INFERRED)" % fid, flush=True)
        elif verb == "canvass":
            hit = _find_char(args, argv[1])
            fid = int(argv[2], 0)
            if hit and _set_char_force(hit[0], hit[1].get("charid"), fid):
                print("[feforce]    %r -> force %d (stored; their next "
                      "login skips the nation prompt)" % (argv[1], fid),
                      flush=True)
            else:
                print("[feforce]    WARNING: no stored character %r" % argv[1],
                      flush=True)
        elif verb == "expel":
            hit = _find_char(args, argv[1])
            if hit and _set_char_force(hit[0], hit[1].get("charid"), 0):
                print("[feforce]    %r expelled: force=0 -> the nation prompt "
                      "returns on their next login" % argv[1], flush=True)
            else:
                print("[feforce]    WARNING: no stored character %r" % argv[1],
                      flush=True)
        elif verb == "withdraw":
            if fw._store_char(args, 0):
                print("[feforce]    withdrawn: this character's force=0",
                      flush=True)
            else:
                print("[feforce]    WARNING: no stored character for this session",
                      flush=True)
        elif verb == "judge":
            _load(args)
            _STATE["judge"] = argv[1] if len(argv) > 1 else None
            _save(args)
            print("[feforce]    judge policy now %s" % (judge_policy(args),),
                  flush=True)
        elif verb == "ng":
            mid = int(argv[1], 16)
            if mid in (0x3017, 0x3019):
                push(ctx, mid)
            elif mid == 0x3028:
                fid = int(argv[2], 0) if len(argv) > 2 else 1
                bits = int(argv[3], 0) if len(argv) > 3 else 0
                err = int(argv[4], 0) if len(argv) > 4 else 0
                push(ctx, 0x3028, force_info_ng_body(fid, bits, err))
            elif mid == 0x3034:
                push(ctx, 0x3034, join_ng_body(int(argv[2], 0)
                                               if len(argv) > 2 else 0))
            else:
                print("[feforce]    WARNING: !force ng takes 3017|3019|3028|3034",
                      flush=True)
        elif verb == "flag299":
            push(ctx, 0x301F if argv[1] == "1" else 0x3020)
        elif verb == "probe":
            mid = int(argv[1], 16)
            if mid in HEADER_ONLY_PUSHES:
                push(ctx, mid)
            else:
                print("[feforce]    WARNING: probe takes one of %s" % ", ".join(
                    "%04X" % m for m in HEADER_ONLY_PUSHES), flush=True)
        elif verb in ("node", "nodeng"):
            key = _STATE["last_node_key"]
            if key is None:
                print("[feforce]    WARNING: no 0x206B seen yet: the arm's lookup "
                      "MISSES an unknown key and then reads nothing, so an "
                      "invented key would desynchronise the stream",
                      flush=True)
                return True
            if verb == "node":
                mask = int(argv[1], 0) if len(argv) > 1 else 0
                fields = {}
                for part in (argv[2] if len(argv) > 2 else "").split(","):
                    if "=" in part:
                        b, _, v = part.partition("=")
                        b = int(b, 0)
                        fields[b] = v if b in (0, 2) else int(v, 0)
                push(ctx, 0x3029, node_ok_body(key, mask, fields))
            else:
                push(ctx, 0x302A, node_ng_body(key, int(argv[1], 0)
                                               if len(argv) > 1 else 0))
        else:
            print("[feforce]    WARNING: unknown !force verb %r\n%s" % (verb, _HELP),
                  flush=True)
    except (IndexError, ValueError) as e:
        print("[feforce]    WARNING: !force %s: %s\n%s" % (verb, e, _HELP), flush=True)
    return True


def _gm_chatng(ctx, argv):
    try:
        mid = int(argv[0], 16)
        code = int(argv[1], 0) if len(argv) > 1 else CHAT_NG_NOT_LOGGED_IN
    except (IndexError, ValueError):
        print("[feforce]    !chatng ID [CODE] -- ID is an NG id (%s) or a chat "
              "request id (%s); codes 0..9" % (
                  " ".join("%04X" % m for m in sorted(CHAT_NG_IDS)),
                  " ".join("%04X" % m for m in sorted(CHAT_NG_FOR))),
              flush=True)
        return True
    chat_refuse(ctx, mid, code)
    return True


# ---------------------------------------------------------------------------
# knobs / registration
# ---------------------------------------------------------------------------
def add_args(p):
    p.add_argument("--force-shop", default="ok", choices=("ok", "ng", "off"),
                   help="0x20A9 -> 0x1177 header-only OK (default), 0x1178 "
                        "NG, or leave it unanswered as prod did (off)")
    p.add_argument("--force-node", default="ok",
                   help="0x206B -> 0x3029 (ok) or 0x302A (ng:CODE)")
    p.add_argument("--force-node-mask", type=lambda s: int(s, 0), default=0,
                   help="0x3029 mask bits 0..17 (default 0 = nothing after "
                        "the mask; see feforce.node_ok_body)")
    p.add_argument("--force-node-field", default="",
                   help="BIT=VALUE,... values for the mask bits sent")
    p.add_argument("--force-join", default="auto",
                   help="0x4012 policy: auto (= the builtin OK) or ng:CODE")
    p.add_argument("--force-file", default=None,
                   help="force rows + judge policy JSON (default "
                        "data/fe_force.json)")
    p.add_argument("--chat-ng", default="off", choices=("off", "on"),
                   help="on: a /tell that reaches no session gets 0x1180 "
                        "MSG_WHISPER_NG code 8 instead of silence")
    p.add_argument("--chat-ng-body", default="none", choices=("none", "u32"),
                   help="body for 0x206C..0x206F/0x113D: none = what the arm "
                        "reads (default); u32 = append the code (probe)")


def tick(ctx):
    """Once per inbound message: make sure the persisted overlay is on
    args.force_table before the client's first 0x206A."""
    _load(ctx.args)


def register(feworld):
    global fw
    fw = feworld
    fw.register_handler(0x4013, on_inventory_status)
    fw.register_handler(0x206B, on_node_info)
    fw.register_handler(0x20A9, on_force_shop)
    # Shadowed ON PURPOSE (see the docstring): judged joins, refused tells.
    fw.register_handler(0x4012, on_join_force, override=True)
    fw.register_handler(0x2067, on_tell, override=True)
    fw.register_gm(gm)
    fw.register_args(add_args)
    fw.register_pump(tick)
    for mid, name in NAMES.items():
        fw.KNOWN.setdefault(mid, name)
