"""femail.py -- Fantasy Earth IN-GAME MAIL (the 0xA05x..0xA08x family + 0x2074).

PARTIAL: BUILT 2026-09-10, NOT LIVE-TESTED. Every reply here is decoded from the
client's own reader sequence (FE_Client.dll runtime dump, base 0x04F90000) and
round-tripped in tools/fe_mail_test.py; none of it has been seen on a screen.

WHAT THE CLIENT DOES (all measured, VAs below)
----------------------------------------------
Every mail REQUEST goes through ONE sender helper, 0x050c3f50(id, obj):

    beginMessage(id)                        0x050459c0
    obj->vt[1](stream)                      pre-name writes (only 0xA050 has one)
    write 0x20 bytes  [player+0x389]        0x05045ad0 -- the CHARACTER NAME,
                                            NUL-padded, from the unit 0x0507d570()
    obj->vt[2](stream)                      the request's own fields
    flush                                   0x05045a60

so every request body starts with `name[32]`. The per-request objects:

    0xA050 MSG_MAIL_SEND_REQ       vt 0x52871dc  vt[1]=0x050c3ef0 u32 [obj+0x14]
                                                 vt[2]=0x050c3f00 to[32] (obj+0x42)
                                                 block64 (obj+0x63) text[512]
                                                 (obj+0xa8) attach[128] (obj+0x2a9)
    0xA060 MSG_MAIL_NUM_ALL_QUERY  vt 0x52871f4  vt[2]=0x050c4110 u8 folder
    0xA063 MSG_MAIL_NUM_NEW_QUERY  vt 0x52871f4  same: u8 folder
    0xA066 MSG_MAIL_HEADER_QUERY   vt 0x5287200  vt[2]=0x050c43d0 u8 folder,
                                                 u32 index, u32 count
    0xA069 MSG_MAIL_BODY_QUERY     vt 0x528720c  vt[2]=0x050c4960 u8 folder, u32 id
    0xA080 MSG_MAIL_DEL_REQ        vt 0x528720c  same: u8 folder, u32 id
    0x2074 MSG_ATTACH_ITEM_GET_REQUEST (0x050c5140, NOT via the helper):
                                   u32 [player+0x3c0], u32 mailId, u16 slot
                                   -- and it REGISTERS 0x111C/0x111D with the
                                   task manager [0x535cec8] (mov word [esp+0x18])

Write helpers: 0x05045c10 = +1, 0x05045c70 = +4, 0x05045ad0(ptr, n) = n raw
bytes, all big-endian like the rest of the door.

THE MAIL RECORD (the client's struct, 0x50c0130 init / 0x50c0080 copy ctor):

    +0x08 u32   mail id          (BODY_QUERY compares the reply's id to it)
    +0x0c u32   ref              init 0xFFFFFFFF; the u32 0xA050 sends FIRST.
                                 Meaning NOT pinned (reply-to id? attachment
                                 ref?) -- stored and echoed verbatim.
    +0x10 u32 / +0x14 u32        two more u32 (0x05045e60 reads, +0x14 FIRST
                                 then +0x10); meaning not pinned. Served as the
                                 send time (unix seconds) in both -- A GUESS.
    +0x18 u8    read flag        0 = unread: 0x050c1b97 counts rows with
                                 [rec+0x18]==0; BODY_RESULT sets it to 1
                                 (0x050c4b62). "Unread mail %d/%d" (0x52e1534).
    +0x19 [33]  FROM name        0x0505886b... the row shows +0x19 unless
    +0x3a [33]  TO name          flag bit 2 (folder 0) -> shows +0x3a (0x050c3506)
    +0x5b [64]  ONE 64-byte block on the wire that the client treats as TWO
                strings: +0x5b date ("00/00/00 00:00" template 0x52e150c; the
                HEADER arm forces a NUL at +0x79 = 30 chars max) and +0x7b
                SUBJECT[32] (the reply builder 0x050c03c0 writes "Re:" +
                old+0x7b into new+0x7b; the row's second column draws +0x7b).
                The SEND log's "Subject:%s" prints +0x5b, so on the WAY IN the
                compose window may put the typed subject in either half --
                on_send keeps both and takes the non-empty one.
    +0x9c u8    flags: bit0 set when the body was fetched, bit2 set BY THE
                CLIENT on every folder-0 header (0x050c4575)
    +0xa0 [513] body text
    +0x2a1[129] ATTACHMENT TEXT (see below)
    +0x324..    3 attachment slots parsed OUT OF THAT TEXT by 0x050c0530

FOLDERS. 0x050c32f0 accepts folder 0 or 1 only (0xff = none). The row renderer
shows the TO name for folder 0 and the FROM name otherwise, so folder 0 is the
box where you are the sender: 0 = SENT ("Sent" 0x52e14d8), 1 = INBOX ("受信"
0x52e14d0). --mail-folders flips that without a code change if the screen
disagrees (PARTIAL: not yet verified live).

ATTACHMENTS ride in the 128-byte text field, parsed by 0x050c0530 with sscanf:

    "Attach:N," then N x "%d,%d,%d,%d,%d,"  =  (0, taken, type, id, num)

    type 1 = GOLD (goes to slot 0, "%dGold" 0x52e1588 prints num); any other
    type = an item (slots 1..2, "MAIL ATTACH ITEM :: type = %d id = %d num = %d"
    0x52e16cc); `taken` != 0 hides the take button (0x050c3a32). The client
    writes the same text when it sends (0x050c0480: "%d,%d,%d,%d,%d," with
    0, 0, type, id, num). Whether `id` is the item NUMBER or the object uid is
    NOT pinned; --mail-attach take treats it as the item number.

THE REPLIES, arm by arm (read off the mail window's own handler 0x050c5280,
whose switch is: 0xA051->0x050c4010, 0xA052->0x050c4040, 0xA061->0x050c4150,
0xA062->0x050c41a0, 0xA064->0x050c4280, 0xA065->0x050c42d0, and the jump table
at 0x050c5404/0x050c5420 for 0xA067->0x050c4430, 0xA068->0x050c4620,
0xA06A->0x050c49c0, 0xA06B->0x050c4c50, 0xA081->0x050c50a0, 0xA082->0x050c50e0,
0x111C->0x050c51f0, 0x111D->0x050c5210). Readers: 0x05045e90 u8, 0x05045ef0 and
0x05045e60 u32 (both +4 big-endian), 0x05045b10(ptr, n) raw n bytes:

    0xA051 MAIL_SEND_OK         nothing
    0xA052 MAIL_SEND_NG         u8 err       -> the NG table's 0xA052 text
    0xA061 MAIL_NUM_ALL_RESULT  u32 count    "AllMailNums"
    0xA062 MAIL_NUM_ALL_ERROR   u8 err
    0xA064 MAIL_NUM_NEW_RESULT  u32 count    "NewMailNums"
    0xA065 MAIL_NUM_NEW_ERROR   u8 err
    0xA067 MAIL_HEADER_RESULT   u32 n, then n x { u32 id, u32 ref, u32 [+0x14],
                                u32 [+0x10], u8 read, from[32], to[32],
                                block64 = date[32] + subject[32] }
    0xA068 MAIL_HEADER_ERROR    u8 err
    0xA06A MAIL_BODY_RESULT     u32 id, text[512], attach[128]
                                (the id must equal the one asked for, else the
                                arm keeps nothing: 0x050c4b53)
    0xA06B MAIL_BODY_ERROR      u8 err
    0xA081 MAIL_DEL_OK          nothing
    0xA082 MAIL_DEL_NG          u8 err
    0x111C ATTACH_ITEM_GET_OK   nothing
    0x111D ATTACH_ITEM_GET_NG   u8 err

Names are the client's own log strings (0x52e1748..0x52e1cf8); "ref", "date",
"note" and the folder names are OURS.

WARNING: THE GATE, and it is worse than "the window must be open". The c3 dispatcher
(0x05057d50) arms are:

    0xA051..52, 0xA061..62, 0xA064..65 -> 0x05058847: copy (0x0509fca0) then
                                          POST to every open window (0x0509fc50)
    0xA067..68, 0xA06A..6B             -> 0x05058887: copy, jmp epilogue. NO POST.
    0xA081..82, 0x111C..1D             -> 0x0505889e: copy, jmp epilogue. NO POST.

The epilogue 0x05058b17 is `mov al,1; ret` -- the message is consumed. So the
header list, the body, the delete ack and the attachment ack reach the Mail
window ONLY if the pre-dispatch notifier at 0x050475d3 (`[0x535cec8]->vt[1](id)`,
the task manager 0x2074 registers with) delivers them; that object is NULL in
the dump and could not be read. A live client decides this; the server side
is complete either way. Every send below logs the gate.

PERSISTENCE: its own sqlite file, `data/fe_mail.db` (resolved the way
festore.default_db does: pol-server/data on the host = /data in the
container), env FE_MAIL_DB or --mail-db to move it. NEVER data/fe.db. One
table, `mail`, keyed by owner = "<account>/<charid>" so two characters on one
account have two boxes and a renamed character keeps hers.

NOT DONE / NOT POSSIBLE:
  * MSG_MAIL_HEADER_NEW_QUERY / MAIL_DATA_UPDATE_REQ / MAIL_CHECKMARK_REQ exist
    as log strings (0x52e1974, 0x52e1ab8, 0x52e1b44) and builders (0x050c46xx,
    0x050c4cb0 vt 0x5287218, 0x050c4f30) but their ids never appear in the
    window's switch and the CHECKMARK builder has no caller (xref empty) --
    dead code in this build; nothing to serve.
  * A "you have mail" push: no inbound id carries one (0x303D is the only
    other id 0x05058815 posts and it is not a mail id). The recipient's live
    session gets a server LOG line via the relay, nothing on screen.
"""
import os
import sqlite3
import struct
import threading
import time

fw = None            # the feworld module, handed in by register()

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOCK = threading.Lock()

# ---- the client's own names (log strings at 0x52e1748..) ------------------
NAMES = {
    0xA050: "MSG_MAIL_SEND_REQ [u32 ref][name32][to32][date32+subject32]"
            "[text512][attach128] (0x050c3ce6 -> 0x050c3f50)",
    0xA051: "MSG_MAIL_SEND_OK (0x050c4010: header-only)",
    0xA052: "MSG_MAIL_SEND_NG [u8 err] (0x050c4040)",
    0xA060: "MSG_MAIL_NUM_ALL_QUERY [name32][u8 folder] (0x050c40a0)",
    0xA061: "MSG_MAIL_NUM_ALL_RESULT [u32 AllMailNums] (0x050c4150)",
    0xA062: "MSG_MAIL_NUM_ALL_ERROR [u8 err] (0x050c41a0)",
    0xA063: "MSG_MAIL_NUM_NEW_QUERY [name32][u8 folder] (0x050c4200)",
    0xA064: "MSG_MAIL_NUM_NEW_RESULT [u32 NewMailNums] (0x050c4280)",
    0xA065: "MSG_MAIL_NUM_NEW_ERROR [u8 err] (0x050c42d0)",
    0xA066: "MSG_MAIL_HEADER_QUERY [name32][u8 folder][u32 index][u32 count]"
            " (0x050c4350)",
    0xA067: "MSG_MAIL_HEADER_RESULT [u32 n] n x {u32 id,u32 ref,u32,u32,u8 read,"
            "from32,to32,date32,subject32} (0x050c4430)",
    0xA068: "MSG_MAIL_HEADER_ERROR [u8 err] (0x050c4620)",
    0xA069: "MSG_MAIL_BODY_QUERY [name32][u8 folder][u32 id] (0x050c48e0)",
    0xA06A: "MSG_MAIL_BODY_RESULT [u32 id][text512][attach128] (0x050c49c0)",
    0xA06B: "MSG_MAIL_BODY_ERROR [u8 err] (0x050c4c50)",
    0xA080: "MSG_MAIL_DEL_REQ [name32][u8 folder][u32 id] (0x050c5010)",
    0xA081: "MSG_MAIL_DEL_OK (0x050c50a0: header-only)",
    0xA082: "MSG_MAIL_DEL_NG [u8 err] (0x050c50e0)",
    0x2074: "MSG_ATTACH_ITEM_GET_REQUEST [u32 unit+0x3c0][u32 mailId][u16 slot]"
            " (0x050c5140)",
}

# Folder indices as the CLIENT numbers them (0x050c32f0 accepts 0..1). Which
# one is which is read off the row renderer (docstring); --mail-folders can
# swap the labels. These two module globals are what the knob rewrites.
FOLDER_SENT = 0
FOLDER_INBOX = 1
FOLDER_LABEL = {0: "sent", 1: "inbox"}

# Field widths, from the write helper calls (0x050c3f00) and the readers
# (0x050c4430 / 0x050c49c0). NUL-padded, cp932.
W_NAME, W_HALF, W_TEXT, W_ATTACH = 32, 32, 512, 128
HEADER_ROW = 4 + 4 + 4 + 4 + 1 + W_NAME + W_NAME + 2 * W_HALF     # 145

# NG codes from the client's own NG table (dumped 2026-09-04).
NG_SEND_PARAM = 1          # 不正なパラメータです
NG_SEND_NO_RECIPIENT = 33  # "Recipient not found; not sent."
NG_SEND_INBOX_FULL = 35    # 送信先の受信ボックスが一杯です
NG_SEND_SENTBOX_FULL = 36  # 送信ボックスが一杯です
NG_NUM_NO_ACCOUNT = 16     # "That account does not exist." (0xA062/0xA065)
NG_GENERIC = 0             # 未定義のエラーです
NG_ATTACH_NOT_GOT = 2      # 添付アイテムを取得できませんでした (0x111D)
NG_ATTACH_BAG_FULL = 6     # 所持アイテムが一杯なので... (0x111D)
NG_ATTACH_ALREADY = 7      # 既に取得済みのアイテムです (0x111D)


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #
def register(feworld):
    global fw
    fw = feworld
    for mid in (0xA050, 0xA060, 0xA063, 0xA066, 0xA069, 0xA080):
        fw.register_handler(mid, _HANDLERS[mid])
    # feworld answers 0x2074 header-only (UI_HEADER_ONLY_OK). Shadowed ON
    # PURPOSE: the attachment slot lives in this module's store, and a bare OK
    # would tell the player the item was taken while nothing moved.
    fw.register_handler(0x2074, on_attach_get, override=True)
    fw.register_gm(gm)
    fw.register_args(add_args)
    fw.register_relay("mail.new", on_relay_new)
    for mid, name in NAMES.items():
        fw.KNOWN.setdefault(mid, name)


def add_args(ap):
    ap.add_argument("--mail", default="on", choices=("on", "off"),
                    help="answer the Mail window at all (default on: the "
                         "window is waiting for these replies, so answering "
                         "is the fix; off = log the request and hang the "
                         "window as before)")
    ap.add_argument("--mail-db", default=None,
                    help="sqlite file for the mailboxes (default data/fe_mail.db"
                         " next to fe.db; env FE_MAIL_DB). Never fe.db itself.")
    ap.add_argument("--mail-folders", default="sent,inbox",
                    help="label of client folder 0, then folder 1. The row "
                         "renderer 0x050c3506 says folder 0 shows the TO name, "
                         "hence sent,inbox; flip it if the screen disagrees.")
    ap.add_argument("--mail-box-size", type=int, default=30,
                    help="mails per folder before SEND is refused with the "
                         "client's own 'inbox full' / 'sendbox full' NG (35/36)")
    ap.add_argument("--mail-attach", default="display",
                    choices=("display", "take"),
                    help="display: attachments are stored and SHOWN in the "
                         "mail, the take button answers NG 2 and nothing "
                         "leaves or enters a bag (no duplication, no loss). "
                         "take: PARTIAL: gold and items really move -- debited from "
                         "the sender on SEND, added to the taker's bag/wallet "
                         "on 0x2074 with the 0x107A / 0x2024 pushes.")
    ap.add_argument("--mail-from", default="GM",
                    help="the FROM name on mail sent with `!mail send`")
    ap.add_argument("--mail-order", default="new", choices=("new", "old"),
                    help="header paging order: newest first (default) or oldest")


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #
def default_db():
    d = os.path.normpath(os.path.join(_HERE, os.pardir, "data"))
    return os.path.join(d if os.path.isdir(d) else _HERE, "fe_mail.db")


def db_path(args):
    return (getattr(args, "mail_db", None) or os.environ.get("FE_MAIL_DB")
            or default_db())


_SCHEMA = """
CREATE TABLE IF NOT EXISTS mail (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,   -- the wire mail id
    owner     TEXT    NOT NULL,                    -- "<account>/<charid>"
    folder    INTEGER NOT NULL,                    -- the CLIENT's folder index
    ref       INTEGER NOT NULL DEFAULT 4294967295, -- rec+0x0c, echoed
    sent_at   INTEGER NOT NULL,                    -- unix seconds
    read      INTEGER NOT NULL DEFAULT 0,          -- rec+0x18
    from_name TEXT    NOT NULL,
    to_name   TEXT    NOT NULL,
    date      TEXT    NOT NULL,                    -- rec+0x5b, "YY/MM/DD hh:mm"
    subject   TEXT    NOT NULL,                    -- rec+0x7b
    text      TEXT    NOT NULL,                    -- rec+0xa0
    attach    TEXT    NOT NULL DEFAULT ''          -- rec+0x2a1, "Attach:N,..."
);
CREATE INDEX IF NOT EXISTS mail_box ON mail(owner, folder, id);
"""


def connect(args):
    path = db_path(args)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=%s"
                     % os.environ.get("POL_SQLITE_JOURNAL", "TRUNCATE"))
    except sqlite3.Error:
        pass
    conn.executescript(_SCHEMA)
    return conn


def _owner(account, charid):
    return "%s/%d" % (account, int(charid))


def box_count(args, owner, folder, unread_only=False):
    with _LOCK, connect(args) as c:
        q = "SELECT COUNT(*) FROM mail WHERE owner=? AND folder=?"
        if unread_only:
            q += " AND read=0"
        return c.execute(q, (owner, folder)).fetchone()[0]


def box_page(args, owner, folder, index, count):
    order = "DESC" if getattr(args, "mail_order", "new") == "new" else "ASC"
    with _LOCK, connect(args) as c:
        rows = c.execute("SELECT * FROM mail WHERE owner=? AND folder=? "
                         "ORDER BY id %s LIMIT ? OFFSET ?" % order,
                         (owner, folder, max(0, count), max(0, index))).fetchall()
    return [dict(r) for r in rows]


def box_get(args, owner, folder, mid):
    with _LOCK, connect(args) as c:
        r = c.execute("SELECT * FROM mail WHERE owner=? AND folder=? AND id=?",
                      (owner, folder, mid)).fetchone()
    return dict(r) if r else None


def box_mark_read(args, mid):
    with _LOCK, connect(args) as c:
        c.execute("UPDATE mail SET read=1 WHERE id=?", (mid,))


def box_set_attach(args, mid, attach):
    with _LOCK, connect(args) as c:
        c.execute("UPDATE mail SET attach=? WHERE id=?", (attach, mid))


def box_delete(args, owner, folder, mid):
    with _LOCK, connect(args) as c:
        n = c.execute("DELETE FROM mail WHERE owner=? AND folder=? AND id=?",
                      (owner, folder, mid)).rowcount
    return n > 0


def box_put(args, owner, folder, from_name, to_name, subject, text,
            attach="", ref=0xFFFFFFFF, read=0, when=None):
    when = int(when if when is not None else time.time())
    date = time.strftime("%y/%m/%d %H:%M", time.localtime(when))
    with _LOCK, connect(args) as c:
        cur = c.execute(
            "INSERT INTO mail(owner,folder,ref,sent_at,read,from_name,to_name,"
            "date,subject,text,attach) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (owner, folder, int(ref) & 0xFFFFFFFF, when, int(read), from_name,
             to_name, date, subject, text, attach or ""))
        return cur.lastrowid


# --------------------------------------------------------------------------- #
# identity: who is this session, who is NAME
# --------------------------------------------------------------------------- #
def _all_chars():
    """[(account, charid, name)] across the WHOLE store -- the roster API is
    per account, so this walks store_accounts() then load_roster() each.
    Split out so the test can stub it without a store."""
    try:
        import felobby
    except ImportError:
        return []
    path = felobby._default_store()
    out = []
    for acct in (felobby.store_accounts(path) or {}):
        for c in felobby.load_roster(path, acct) or []:
            if c.get("charid") is None:
                continue
            out.append((acct, int(c["charid"]), str(c.get("name", ""))))
    return out


def resolve_name(name):
    """(account, charid, name) for a character NAME, exact first, then
    case-folded (the client upper-cases some names on the wire -- see
    chat_relay), else None."""
    want = name.strip()
    if not want:
        return None
    rows = _all_chars()
    for r in rows:
        if r[2] == want:
            return r
    for r in rows:
        if r[2].casefold() == want.casefold():
            return r
    return None


def my_identity(ctx, wire_name=""):
    """(owner, name) of THIS session, or (None, name) with a log line when the
    session has no stored character (then every reply is the NG the client
    names 'That account does not exist')."""
    s = ctx.session
    acct, charid = s.get("account"), s.get("charid")
    if not acct or charid is None:
        print("[femail]    session has no account/charid -- no mailbox",
              flush=True)
        return None, wire_name
    rec = None
    try:
        rec = fw._self_char(ctx.args)
    except Exception:                                  # noqa: BLE001
        rec = None
    name = str((rec or {}).get("name") or wire_name or "")
    if wire_name and rec and name != wire_name:
        print("[femail]    the client signed the request %r but the roster "
              "says %r -- using the roster" % (wire_name, name), flush=True)
    return _owner(acct, charid), name


# --------------------------------------------------------------------------- #
# field codecs
# --------------------------------------------------------------------------- #
def enc_fixed(s, width):
    """cp932, clipped to width-1 bytes on a character boundary, NUL padded."""
    b = (s or "").encode("cp932", "replace")
    if len(b) > width - 1:
        cut = b[:width - 1]
        # never leave half a double-byte character at the end
        while True:
            try:
                cut.decode("cp932")
                break
            except UnicodeDecodeError:
                cut = cut[:-1]
        b = cut
    return b + bytes(width - len(b))


def dec_fixed(b):
    b = bytes(b)
    i = b.find(b"\0")
    if i >= 0:
        b = b[:i]
    return b.decode("cp932", "replace")


def folder_ok(f):
    return f in (0, 1)


def folder_name(f):
    return FOLDER_LABEL.get(f, "folder %d" % f)


def _apply_folder_labels(args):
    global FOLDER_SENT, FOLDER_INBOX, FOLDER_LABEL
    spec = getattr(args, "mail_folders", None) or "sent,inbox"
    parts = [p.strip().lower() for p in spec.split(",")]
    if len(parts) == 2 and sorted(parts) == ["inbox", "sent"]:
        FOLDER_SENT = parts.index("sent")
        FOLDER_INBOX = parts.index("inbox")
        FOLDER_LABEL = {0: parts[0], 1: parts[1]}


def _gate(mid, why):
    """Rule 3: every reply is discarded by the client unless the Mail window
    is open (poster 0x0509fc50 walks [0x53432d8]); and for the ids the c3 arm
    never posts at all, say so, so a blank screen can be read off the log."""
    posted = mid in (0xA051, 0xA052, 0xA061, 0xA062, 0xA064, 0xA065)
    print("[femail]    0x%04X %s -- window-gated: %s" % (
        mid, why,
        "posted to open windows by 0x05058847; the Mail window must be open"
        if posted else
        "c3 arm 0x05058887/0x0505889e COPIES AND DOES NOT POST; reaches the "
        "Mail window only via the task notifier [0x535cec8] (unresolved)"),
        flush=True)


def _off(ctx, mid, name):
    if getattr(ctx.args, "mail", "on") == "off":
        print("[femail]    --mail off: 0x%04X %s NOT answered (the window "
              "hangs, as before this module)" % (mid, name), flush=True)
        return True
    _apply_folder_labels(ctx.args)
    return False


# --------------------------------------------------------------------------- #
# attachments: "Attach:N," + N x "0,taken,type,id,num,"   (0x050c0530)
# --------------------------------------------------------------------------- #
def parse_attach(text):
    """-> [(taken, type, id, num)], the way 0x050c0530 reads it: strstr
    'Attach:', sscanf 'Attach:%d,', then N groups of five ints separated by
    commas. Malformed input yields what the client would keep: nothing."""
    i = (text or "").find("Attach:")
    if i < 0:
        return []
    rest = text[i + len("Attach:"):]
    parts = rest.split(",")
    try:
        n = int(parts[0])
    except (ValueError, IndexError):
        return []
    out = []
    p = 1
    for _ in range(max(0, min(n, 3))):
        try:
            grp = [int(x) for x in parts[p:p + 5]]
        except ValueError:
            break
        if len(grp) < 5:
            break
        out.append((grp[1], grp[2], grp[3], grp[4]))
        p += 5
    return out


def format_attach(entries):
    if not entries:
        return ""
    s = "Attach:%d," % len(entries)
    for taken, typ, iid, num in entries:
        s += "%d,%d,%d,%d,%d," % (0, taken, typ, iid, num)
    return s


def attach_slots(entries):
    """Which client slot each entry lands in (0x050c0637): type 1 -> slot 0
    (gold); others -> slot 1, then slot 2 once slot 1's type is non-zero."""
    slots, used1 = {}, False
    for k, e in enumerate(entries):
        if e[1] == 1:
            slots[0] = k
        elif not used1:
            slots[1] = k
            used1 = True
        else:
            slots[2] = k
    return slots


# --------------------------------------------------------------------------- #
# body encoders (what the test decodes with the arm's reader sequence)
# --------------------------------------------------------------------------- #
def header_row(m):
    """One 0xA067 row: u32 id, u32 ref, u32 [+0x14], u32 [+0x10], u8 read,
    from[32], to[32], date[32] + subject[32]  (0x050c44d0..0x050c4542)."""
    t = int(m.get("sent_at") or 0) & 0xFFFFFFFF
    return (struct.pack(">IIIIB", int(m["id"]) & 0xFFFFFFFF,
                        int(m.get("ref", 0xFFFFFFFF)) & 0xFFFFFFFF, t, t,
                        1 if m.get("read") else 0)
            + enc_fixed(m["from_name"], W_NAME) + enc_fixed(m["to_name"], W_NAME)
            + enc_fixed(m["date"], W_HALF) + enc_fixed(m["subject"], W_HALF))


def header_body(rows):
    return struct.pack(">I", len(rows)) + b"".join(header_row(m) for m in rows)


def body_body(m):
    """0xA06A: u32 id, text[512], attach[128] (0x050c4b0e..0x050c4b44)."""
    return (struct.pack(">I", int(m["id"]) & 0xFFFFFFFF)
            + enc_fixed(m["text"], W_TEXT) + enc_fixed(m.get("attach", ""), W_ATTACH))


# --------------------------------------------------------------------------- #
# inbound handlers
# --------------------------------------------------------------------------- #
def _parse_folder_req(inner, extra_fmt=""):
    """[u16 id][name32][u8 folder][extra...] -> (name, folder, extras)."""
    f = inner[2:]
    need = W_NAME + 1 + struct.calcsize(">" + extra_fmt)
    if len(f) < need:
        return None
    name = dec_fixed(f[:W_NAME])
    folder = f[W_NAME]
    extras = struct.unpack_from(">" + extra_fmt, f, W_NAME + 1) if extra_fmt else ()
    return name, folder, extras


def on_send(ctx, inner):
    """0xA050 MSG_MAIL_SEND_REQ -> 0xA051 / 0xA052 [u8 err]."""
    if _off(ctx, 0xA050, "MSG_MAIL_SEND_REQ"):
        return
    f = inner[2:]
    want = 4 + W_NAME + W_NAME + 2 * W_HALF + W_TEXT + W_ATTACH      # 772
    if len(f) < want:
        print("[femail]    0xA050 body is %d bytes, the builder writes %d -- "
              "refused" % (len(f), want), flush=True)
        _gate(0xA052, "MSG_MAIL_SEND_NG err=%d (short body)" % NG_SEND_PARAM)
        ctx.reply(0xA052, bytes([NG_SEND_PARAM]), why="MSG_MAIL_SEND_NG")
        return
    ref = struct.unpack_from(">I", f, 0)[0]
    p = 4
    wire_name = dec_fixed(f[p:p + W_NAME]); p += W_NAME
    to_name = dec_fixed(f[p:p + W_NAME]); p += W_NAME
    half_a = dec_fixed(f[p:p + W_HALF]); p += W_HALF
    half_b = dec_fixed(f[p:p + W_HALF]); p += W_HALF
    text = dec_fixed(f[p:p + W_TEXT]); p += W_TEXT
    attach = dec_fixed(f[p:p + W_ATTACH]); p += W_ATTACH
    # the subject is +0x7b (second half) by the reply builder's evidence; the
    # SEND log prints +0x5b. Keep whichever the compose window filled.
    subject = half_b or half_a
    print("[femail] <- 0xA050 MSG_MAIL_SEND_REQ ref=0x%08X from=%r SendTo=%r "
          "Subject=%r (halves %r | %r) text=%d chars attach=%r%s"
          % (ref, wire_name, to_name, subject, half_a, half_b, len(text), attach,
             " (+%d trailing bytes)" % (len(f) - p) if len(f) > p else ""),
          flush=True)
    owner, me = my_identity(ctx, wire_name)
    if owner is None:
        _gate(0xA052, "MSG_MAIL_SEND_NG err=34 (no sender character)")
        ctx.reply(0xA052, bytes([34]), why="MSG_MAIL_SEND_NG")   # 送信者がいません
        return
    hit = resolve_name(to_name)
    if hit is None:
        print("[femail]    no character named %r in the store" % to_name,
              flush=True)
        _gate(0xA052, "MSG_MAIL_SEND_NG err=%d 'Recipient not found; not sent.'"
              % NG_SEND_NO_RECIPIENT)
        ctx.reply(0xA052, bytes([NG_SEND_NO_RECIPIENT]), why="MSG_MAIL_SEND_NG")
        return
    to_owner = _owner(hit[0], hit[1])
    cap = int(getattr(ctx.args, "mail_box_size", 30) or 30)
    if box_count(ctx.args, to_owner, FOLDER_INBOX) >= cap:
        _gate(0xA052, "MSG_MAIL_SEND_NG err=%d (recipient inbox full)"
              % NG_SEND_INBOX_FULL)
        ctx.reply(0xA052, bytes([NG_SEND_INBOX_FULL]), why="MSG_MAIL_SEND_NG")
        return
    if box_count(ctx.args, owner, FOLDER_SENT) >= cap:
        _gate(0xA052, "MSG_MAIL_SEND_NG err=%d (sent box full)"
              % NG_SEND_SENTBOX_FULL)
        ctx.reply(0xA052, bytes([NG_SEND_SENTBOX_FULL]), why="MSG_MAIL_SEND_NG")
        return
    entries = parse_attach(attach)
    if entries and getattr(ctx.args, "mail_attach", "display") == "take":
        err = _debit_sender(ctx, entries)
        if err is not None:
            _gate(0xA052, "MSG_MAIL_SEND_NG err=%d (attachment debit)" % err)
            ctx.reply(0xA052, bytes([err]), why="MSG_MAIL_SEND_NG")
            return
    attach_txt = format_attach(entries)
    when = int(time.time())
    mid_in = box_put(ctx.args, to_owner, FOLDER_INBOX, me, hit[2], subject, text,
                     attach_txt, ref=ref, read=0, when=when)
    # the sender's copy: same text, attachments marked taken (the client marks
    # all three slots taken on folder-0 bodies itself, 0x050c4b84)
    mid_out = box_put(ctx.args, owner, FOLDER_SENT, me, hit[2], subject, text,
                      format_attach([(1, t, i, n) for _, t, i, n in entries]),
                      ref=ref, read=1, when=when)
    print("[femail]    stored: inbox of %s as mail %d, sent box of %s as mail %d"
          " (attach %r, mode %s)" % (to_owner, mid_in, owner, mid_out,
                                      attach_txt,
                                      getattr(ctx.args, "mail_attach", "display")),
          flush=True)
    _gate(0xA051, "MSG_MAIL_SEND_OK")
    ctx.reply(0xA051, b"", why="MSG_MAIL_SEND_OK")
    n = fw.ext_post("mail.new", {"to_owner": to_owner, "from": me,
                                 "subject": subject, "id": mid_in},
                    to=lambda s, nm: _owner(s.get("account", ""),
                                            s.get("charid") or -1) == to_owner)
    print("[femail]    'you have mail' relayed to %d live session(s) -- a LOG "
          "line on their thread; no inbound id carries a mail notice" % n,
          flush=True)


def on_relay_new(ctx, payload):
    print("[femail]    NEW MAIL for this session: %r from %r (mail %s). The "
          "client learns of it when it next asks 0xA063 with the Mail window "
          "open." % (payload.get("subject"), payload.get("from"),
                     payload.get("id")), flush=True)


def on_num_all(ctx, inner):
    """0xA060 -> 0xA061 [u32 AllMailNums] / 0xA062 [u8 err]."""
    if _off(ctx, 0xA060, "MSG_MAIL_NUM_ALL_QUERY"):
        return
    _num(ctx, inner, 0xA060, 0xA061, 0xA062, False)


def on_num_new(ctx, inner):
    """0xA063 -> 0xA064 [u32 NewMailNums] / 0xA065 [u8 err]."""
    if _off(ctx, 0xA063, "MSG_MAIL_NUM_NEW_QUERY"):
        return
    _num(ctx, inner, 0xA063, 0xA064, 0xA065, True)


def _num(ctx, inner, req, ok, ng, unread):
    label = "NUM_NEW" if unread else "NUM_ALL"
    r = _parse_folder_req(inner)
    if r is None:
        print("[femail] <- 0x%04X short body (%d bytes)" % (req, len(inner) - 2),
              flush=True)
        _gate(ng, "MSG_MAIL_%s_ERROR err=%d" % (label, NG_GENERIC))
        ctx.reply(ng, bytes([NG_GENERIC]), why="MSG_MAIL_%s_ERROR" % label)
        return
    name, folder, _ = r
    print("[femail] <- 0x%04X MSG_MAIL_%s_QUERY Folder:%d (%s) from %r"
          % (req, label, folder, folder_name(folder), name), flush=True)
    owner, _me = my_identity(ctx, name)
    if owner is None or not folder_ok(folder):
        err = NG_NUM_NO_ACCOUNT if owner is None else NG_GENERIC
        _gate(ng, "MSG_MAIL_%s_ERROR err=%d" % (label, err))
        ctx.reply(ng, bytes([err]), why="MSG_MAIL_%s_ERROR" % label)
        return
    n = box_count(ctx.args, owner, folder, unread_only=unread)
    _gate(ok, "MSG_MAIL_%s_RESULT Folder:%d %sMailNums:%d"
          % (label, folder, "New" if unread else "All", n))
    ctx.reply(ok, struct.pack(">I", n), why="MSG_MAIL_%s_RESULT" % label)


def on_header(ctx, inner):
    """0xA066 [name32][u8 folder][u32 index][u32 count] -> 0xA067 / 0xA068."""
    if _off(ctx, 0xA066, "MSG_MAIL_HEADER_QUERY"):
        return
    r = _parse_folder_req(inner, "II")
    if r is None:
        _gate(0xA068, "MSG_MAIL_HEADER_ERROR err=%d (short body)" % NG_GENERIC)
        ctx.reply(0xA068, bytes([NG_GENERIC]), why="MSG_MAIL_HEADER_ERROR")
        return
    name, folder, (index, count) = r
    print("[femail] <- 0xA066 MSG_MAIL_HEADER_QUERY Folder:%d (%s) Index:[%d+%d]"
          " from %r" % (folder, folder_name(folder), index, count, name),
          flush=True)
    owner, _me = my_identity(ctx, name)
    if owner is None or not folder_ok(folder):
        err = NG_NUM_NO_ACCOUNT if owner is None else NG_GENERIC
        _gate(0xA068, "MSG_MAIL_HEADER_ERROR err=%d" % err)
        ctx.reply(0xA068, bytes([err]), why="MSG_MAIL_HEADER_ERROR")
        return
    # the client allocates one 0x428 object per row and its list is bounded
    # by the box size; never hand it more than it asked for
    rows = box_page(ctx.args, owner, folder, index, min(count, 255))
    for m in rows:
        print("[femail]      Mail:[%s][%s] id=%d read=%d" % (
            m["to_name"] if folder == FOLDER_SENT else m["from_name"],
            m["subject"], m["id"], m["read"]), flush=True)
    _gate(0xA067, "MSG_MAIL_HEADER_RESULT Folder:%d GetHeaderNum:%d"
          % (folder, len(rows)))
    ctx.reply(0xA067, header_body(rows), why="MSG_MAIL_HEADER_RESULT")


def on_body(ctx, inner):
    """0xA069 [name32][u8 folder][u32 id] -> 0xA06A / 0xA06B."""
    if _off(ctx, 0xA069, "MSG_MAIL_BODY_QUERY"):
        return
    r = _parse_folder_req(inner, "I")
    if r is None:
        _gate(0xA06B, "MSG_MAIL_BODY_ERROR err=%d (short body)" % NG_GENERIC)
        ctx.reply(0xA06B, bytes([NG_GENERIC]), why="MSG_MAIL_BODY_ERROR")
        return
    name, folder, (mid,) = r
    print("[femail] <- 0xA069 MSG_MAIL_BODY_QUERY Folder:%d (%s) GetMailID:%d "
          "from %r" % (folder, folder_name(folder), mid, name), flush=True)
    owner, _me = my_identity(ctx, name)
    m = box_get(ctx.args, owner, folder, mid) if owner and folder_ok(folder) else None
    if m is None:
        err = NG_NUM_NO_ACCOUNT if owner is None else 20   # メールが取得できません
        _gate(0xA06B, "MSG_MAIL_BODY_ERROR err=%d" % err)
        ctx.reply(0xA06B, bytes([err]), why="MSG_MAIL_BODY_ERROR")
        return
    if not m["read"]:
        box_mark_read(ctx.args, mid)
    _gate(0xA06A, "MSG_MAIL_BODY_RESULT Folder:%d GetMailID:%d" % (folder, mid))
    ctx.reply(0xA06A, body_body(m), why="MSG_MAIL_BODY_RESULT")


def on_delete(ctx, inner):
    """0xA080 [name32][u8 folder][u32 id] -> 0xA081 / 0xA082 [u8 err]."""
    if _off(ctx, 0xA080, "MSG_MAIL_DEL_REQ"):
        return
    r = _parse_folder_req(inner, "I")
    if r is None:
        _gate(0xA082, "MSG_MAIL_DEL_NG err=%d (short body)" % NG_GENERIC)
        ctx.reply(0xA082, bytes([NG_GENERIC]), why="MSG_MAIL_DEL_NG")
        return
    name, folder, (mid,) = r
    print("[femail] <- 0xA080 MSG_MAIL_DEL_REQ Folder:%d (%s) DeleteMailID:%d "
          "from %r" % (folder, folder_name(folder), mid, name), flush=True)
    owner, _me = my_identity(ctx, name)
    ok = bool(owner) and folder_ok(folder) and box_delete(ctx.args, owner,
                                                          folder, mid)
    if not ok:
        err = NG_NUM_NO_ACCOUNT if owner is None else 20
        _gate(0xA082, "MSG_MAIL_DEL_NG err=%d" % err)
        ctx.reply(0xA082, bytes([err]), why="MSG_MAIL_DEL_NG")
        return
    _gate(0xA081, "MSG_MAIL_DEL_OK Folder:%d DeleteMailID:%d" % (folder, mid))
    ctx.reply(0xA081, b"", why="MSG_MAIL_DEL_OK")


def on_attach_get(ctx, inner):
    """0x2074 [u32 unit+0x3c0][u32 mailId][u16 slot] -> 0x111C / 0x111D [u8].

    Shadows feworld's header-only row on purpose. `display` mode answers NG 2
    (the client's 'could not take the attached item') because nothing here
    moves an item; `take` mode PARTIAL: moves it with the 0x107A / 0x2024 pushes.
    """
    f = inner[2:]
    if len(f) >= 10:
        unit, mid, slot = struct.unpack_from(">IIH", f, 0)
    elif len(f) >= 6:
        unit, (mid, slot) = None, struct.unpack_from(">IH", f, 0)
    else:
        print("[femail] <- 0x2074 short body (%d bytes)" % len(f), flush=True)
        ctx.reply(0x111D, bytes([1]), why="MSG_ATTACH_ITEM_GET_NG")
        return
    print("[femail] <- 0x2074 MSG_ATTACH_ITEM_GET_REQUEST TargetMailID:%d "
          "ItemSlot:%d (unit field %s)" % (mid, slot, unit), flush=True)
    owner, _me = my_identity(ctx)
    m = box_get(ctx.args, owner, FOLDER_INBOX, mid) if owner else None
    entries = parse_attach(m["attach"]) if m else []
    slots = attach_slots(entries)
    if m is None or slot not in slots:
        _gate(0x111D, "MSG_ATTACH_ITEM_GET_NG err=%d (no such mail/slot)"
              % NG_ATTACH_NOT_GOT)
        ctx.reply(0x111D, bytes([NG_ATTACH_NOT_GOT]), why="MSG_ATTACH_ITEM_GET_NG")
        return
    k = slots[slot]
    taken, typ, iid, num = entries[k]
    if taken:
        _gate(0x111D, "MSG_ATTACH_ITEM_GET_NG err=%d (already taken)"
              % NG_ATTACH_ALREADY)
        ctx.reply(0x111D, bytes([NG_ATTACH_ALREADY]), why="MSG_ATTACH_ITEM_GET_NG")
        return
    if getattr(ctx.args, "mail_attach", "display") != "take":
        print("[femail]    --mail-attach display: slot %d (type %d id %d num %d)"
              " is shown but not movable -- answering the client's own "
              "'could not take' NG rather than an OK that moves nothing"
              % (slot, typ, iid, num), flush=True)
        _gate(0x111D, "MSG_ATTACH_ITEM_GET_NG err=%d" % NG_ATTACH_NOT_GOT)
        ctx.reply(0x111D, bytes([NG_ATTACH_NOT_GOT]), why="MSG_ATTACH_ITEM_GET_NG")
        return
    err = _credit_taker(ctx, typ, iid, num)
    if err is not None:
        _gate(0x111D, "MSG_ATTACH_ITEM_GET_NG err=%d" % err)
        ctx.reply(0x111D, bytes([err]), why="MSG_ATTACH_ITEM_GET_NG")
        return
    entries[k] = (1, typ, iid, num)
    box_set_attach(ctx.args, mid, format_attach(entries))
    _gate(0x111C, "MSG_ATTACH_ITEM_GET_OK TargetMailID:%d" % mid)
    ctx.reply(0x111C, b"", why="MSG_ATTACH_ITEM_GET_OK")


# --------------------------------------------------------------------------- #
# --mail-attach take: the bag / wallet side (PARTIAL: the pushes are feworld's)
# --------------------------------------------------------------------------- #
def _gold_get(args):
    v = fw._seeded_value(args, "gold", getattr(args, "gold", None))
    return int(v) if v is not None else None


def _debit_sender(ctx, entries):
    """Take the attachments OUT of the sender before the mail exists. Returns
    an 0xA052 NG code or None."""
    args = ctx.args
    for taken, typ, iid, num in entries:
        if typ == 1:
            have = _gold_get(args)
            if have is None or have < num or num < 0:
                return 7          # 添付アイテムの金が持ち金オーバーフロー
            fw._store_char_field(args, "gold", have - num)
            fw.wallet_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args)
            print("[femail]    debited %d gold from the sender (%d left)"
                  % (num, have - num), flush=True)
            continue
        old = fw.item_rows(args)
        hit = None
        for slot, (uid, no, flag, count) in enumerate(old):
            if no == iid and count >= max(1, num):
                hit = (slot, uid, no, flag, count)
                break
        if hit is None:
            return 9              # 添付しようとしたアイテムが存在しません
        slot, uid, no, flag, count = hit
        new = list(old)
        gone = []
        if count - max(1, num) > 0:
            new[slot] = (uid, no, flag, count - max(1, num))
        else:
            del new[slot]
            gone.append((uid, slot))
        fw._store_char_field(args, "items", [list(x) for x in new])
        fw.bag_layout_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                           old, new, gone, "mail attachment item %d x%d" % (no, num))
    return None


def _credit_taker(ctx, typ, iid, num):
    """Put one attachment INTO this session's character. Returns an 0x111D NG
    code or None."""
    args = ctx.args
    if typ == 1:
        have = _gold_get(args) or 0
        fw._store_char_field(args, "gold", have + num)
        fw.wallet_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args)
        print("[femail]    credited %d gold (now %d) -- 0x2024 pushed"
              % (num, have + num), flush=True)
        return None
    old = fw.item_rows(args)
    if len(old) + 1 > 96:
        return NG_ATTACH_BAG_FULL
    nxt = fw.new_item_uid(args, old)        # bag AND bank uids
    new = old + [(nxt, iid, 1, max(1, num))]
    fw._store_char_field(args, "items", [list(x) for x in new])
    fw.bag_layout_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                       old, new, [], "mail attachment item %d x%d" % (iid, num))
    return None


# --------------------------------------------------------------------------- #
# the operator's verbs
# --------------------------------------------------------------------------- #
def gm(ctx, line):
    """!mail send NAME|SUBJECT|TEXT[|gold=N][|item=NO[xNUM]]   (from --mail-from)
       !mail count [folder]        counts for THIS session's character
       !mail list [folder]         the headers, on the server log
       !mail clear [folder]        empty a folder of THIS session's character
    """
    if not line.startswith("!mail"):
        return False
    _apply_folder_labels(ctx.args)
    parts = line.split(None, 2)
    verb = parts[1].lower() if len(parts) > 1 else "help"
    rest = parts[2] if len(parts) > 2 else ""
    if verb == "send":
        seg = [s.strip() for s in rest.split("|")]
        if len(seg) < 3:
            print("[femail]    !mail send NAME|SUBJECT|TEXT[|gold=N][|item=NO[xNUM]]",
                  flush=True)
            return True
        name, subject, text = seg[0], seg[1], seg[2]
        entries = []
        for extra in seg[3:]:
            k, _, v = extra.partition("=")
            try:
                if k.strip().lower() == "gold":
                    entries.append((0, 1, 0, int(v)))
                elif k.strip().lower() == "item":
                    no, _, n = v.partition("x")
                    entries.append((0, 2, int(no), int(n or 1)))
            except ValueError:
                print("[femail]    !mail send: bad attachment %r" % extra,
                      flush=True)
                return True
        hit = resolve_name(name)
        if hit is None:
            print("[femail]    !mail send: no character named %r" % name,
                  flush=True)
            return True
        to_owner = _owner(hit[0], hit[1])
        sender = getattr(ctx.args, "mail_from", "GM") or "GM"
        mid = box_put(ctx.args, to_owner, FOLDER_INBOX, sender, hit[2], subject,
                      text, format_attach(entries), ref=0xFFFFFFFF, read=0)
        print("[femail]    !mail send -> mail %d in the inbox of %s (%s) from %r"
              " subject %r%s" % (mid, hit[2], to_owner, sender, subject,
                                 " attach %r" % format_attach(entries)
                                 if entries else ""), flush=True)
        fw.ext_post("mail.new", {"to_owner": to_owner, "from": sender,
                                 "subject": subject, "id": mid},
                    to=lambda s, nm: _owner(s.get("account", ""),
                                            s.get("charid") or -1) == to_owner,
                    include_me=True)
        return True
    owner, me = my_identity(ctx)
    if owner is None:
        print("[femail]    !mail %s: this session has no character" % verb,
              flush=True)
        return True
    folders = [0, 1]
    if rest.strip():
        want = rest.strip().lower()
        folders = [f for f in (0, 1) if want in (str(f), FOLDER_LABEL.get(f))]
    if verb == "count":
        for f in folders:
            print("[femail]    %s (folder %d): %d mail, %d unread"
                  % (folder_name(f), f, box_count(ctx.args, owner, f),
                     box_count(ctx.args, owner, f, True)), flush=True)
        return True
    if verb == "list":
        for f in folders:
            rows = box_page(ctx.args, owner, f, 0, 255)
            print("[femail]    %s (folder %d): %d mail" % (folder_name(f), f,
                                                          len(rows)), flush=True)
            for m in rows:
                print("[femail]      #%d %s %s -> %s  %r%s%s"
                      % (m["id"], m["date"], m["from_name"], m["to_name"],
                         m["subject"], "" if m["read"] else "  [unread]",
                         "  " + m["attach"] if m["attach"] else ""), flush=True)
        return True
    if verb == "clear":
        for f in folders:
            n = 0
            for m in box_page(ctx.args, owner, f, 0, 100000):
                n += box_delete(ctx.args, owner, f, m["id"])
            print("[femail]    cleared %d mail from %s" % (n, folder_name(f)),
                  flush=True)
        return True
    print("[femail]    !mail send NAME|SUBJECT|TEXT[|gold=N][|item=NO[xNUM]] | "
          "count [folder] | list [folder] | clear [folder]", flush=True)
    return True


_HANDLERS = {
    0xA050: on_send,
    0xA060: on_num_all,
    0xA063: on_num_new,
    0xA066: on_header,
    0xA069: on_body,
    0xA080: on_delete,
}
