"""Gold, rings and crystal: the wallet pushes and the ledger that spends it."""
import struct
from . import character, wire

# 0x2024 maskA bits for the two stat-block fields the Status screen actually
# DRAWS, named live 2026-08-25 by distinct sentinels and confirmed against the
# client's own label table at 0x052e29d8 / 0x052e29e8:
#     bit 0x00200000 -> +0x4ec  GOLD  (u32; the label's format is "%lu")
#     bit 0x04000000 -> +0x4f0  RING  (u32)
_U2024_GOLD = (0x00200000, 0x4EC)
_U2024_RING = (0x04000000, 0x4F0)
# maskB bit 0x1 -> +0x500 TOTAL SCORE (u32). Found late, because
# fe2024.py's destination filter only matched 0x3xx/0x4xx/0x9xx and
# reported this row as writing nothing -- a gap that read as a finding.
# The Status window draws it at 0x050e13ca against the label at
# 0x052e29a8, one row above Skill Points.
_U2024_SCORE = (0x00000001, 0x500)      # <- maskB, not maskA


def crystal_push(conn, outbound, mode, be, args, value=None):
    """CRYSTAL -- `0x2035`, mask bit 1, one u32 into [unit+0x8ac].

    2026-08-26, and it closes the row the 08-25 sentinel probe could not:
    CRYSTAL sits between Skill Points and GOLD on the Status screen, took no
    sentinel, and was written down as "probably one of the six ladder bits
    whose destination did not fall out of the scan". It is not a 0x2024 bit at
    all -- **it is not even in the +0x4xx block the probe covered.**

    FOUND DRAW-SITE-BACKWARDS, the move that found 0x1075 and the chat ids.
    The Status screen draws its fields from a table of {label, x, y, fmt} rows
    at 0x052e28e8 -- 'Lv', 'HP', 'Pw', 'Exp', 'Total Score', 'Skill Points',
    'CRYSTAL', 'GOLD', 'RING' -- one straight-line block per row, and CRYSTAL's
    pushes its fmt from 0x052e29d4:

        050e142e  mov eax, [esi + 0x8ac]     <- the value
        050e1434  mov ecx, [0x52e29d4]       <- 'CRYSTAL' fmt '%d'

    so CRYSTAL is `[unit+0x8ac]`, a u32. Its only writer outside the ctor
    (0x0507abb6 zero-inits it beside +0x8a8) is reached from message **0x2035**
    (c0 arm 0x0503a71f -> 0x04fe4de0), and that handler is mask-driven like
    everything else here:

        04fe4ded  0x5045e60(&mask)          u32
        04fe4dfa  test al, 1     -> 0x4fe4e20: u32 -> [obj+0x8a8]
        04fe4e08  test al, 2     -> 0x4fe4e50: u32 -> [obj+0x8ac]  CRYSTAL

    KEY: THE TARGET IS THE HEADER'S UNIT ID, and that took reading the c0
    dispatcher's own frame rather than trusting a slot number. 0x05039fa0 is
    called with (unit_id, msg_id, stream): before its `push edi` it reads the
    msg id at [esp+0x1c], and after it the arms push [esp+0x24] (stream) then
    [esp+0x1c] (unit id). Both sub-setters then resolve the object by that id
    (0x504cb30 / 0x504d3a0, kind 3 -- the same lookup the avatar path uses), so
    this must carry the same unit id as everything else we send.

    WARNING: IT IS NOT A BALANCE. 0x04fe4e9a compares the incoming value against the
    stored one and plays the E25_GetCrystal pickup effect when it INCREASED, so
    the client treats this as an authoritative total, not a delta -- and
    nothing here debits it. Do not build the summon economy on it: a
    price that depends on which door the player came in by is what that costs.

    WARNING: BIT 0 (+0x8a8) IS LEFT ALONE. It is unnamed, zeroed beside CRYSTAL by the
    same ctor, and drawn by no screen we have opened. Serving an invented value
    into an unnamed field launders a guess into a choice.
    """
    # `value` is an explicit one-off (the `!crystal N` probe) and is NOT
    # persisted; otherwise the per-character stored value, seeded from the
    # flag the first time -- see _seeded_value.
    if value is None:
        value = character._seeded_value(args, "crystal", getattr(args, "crystal", None))
    if value is None:
        return
    body = struct.pack(">II", 0x2, value & 0xFFFFFFFF)
    wire.send(conn, outbound, wire.inner_msg(0x2035, body, wire.unit_id_of(args)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld]    -> 0x2035 CRYSTAL %d -> [unit+0x8ac] (mask bit 1). The "
          "Status screen draws it at 0x050e142e with '%%d'. WARNING: per-character "
          "and stored, but nothing debits it yet." % value, flush=True)


def wallet_push(conn, outbound, mode, be, args):
    """GOLD and RING, via `0x2024` -- the two stat-block fields that DRAW.

    2026-08-25. The sentinel probe put impossible values in the ten unnamed
    stat-block fields; exactly two reached the screen. The live screen read 4007
    against GOLD and 4008 against RING, and the client's own layout table
    confirms it independently:

        0x052e29d8  'GOLD'  fmt '%lu'      <- sentinel 4007 = +0x4ec
        0x052e29e8  'RING'  fmt '%d'       <- sentinel 4008 = +0x4f0

    Two sources agreeing -- what rendered, and what the table says renders
    there -- is why these are named rather than guessed. The other EIGHT
    sentinels appeared nowhere, which is its own result: those fields are not
    drawn, and nobody should spend a session serving them.

    \u26a0 UNSET SENDS NOTHING. A zero here is a real value -- it would show the
    player as broke -- so "no flag" and "zero gold" have to be different
    things. This is Tetra Master's start-money bug: a wallet that reads
    zero because nobody served it looks exactly like a wallet that is empty.

    \u26a0 NOT PERSISTED. This serves whatever the flag says on every field
    entry; it is not a balance, and nothing debits it. Do not build buying or
    selling on it until there is a store behind it.

    \u26a0 CRYSTAL sits between Skill Points and GOLD in the same table
    (0x052e29c8) and NONE of the sentinels landed on it, so its offset is not
    in the ten probed. It is probably one of the six ladder bits whose
    destination did not fall out of the static scan. Unfinished, not absent.
    """
    # Each row is the CHARACTER's stored value, seeded from the flag the first
    # time it is served -- see _seeded_value. An unset flag still sends
    # nothing for that row.
    fields = []
    gold = character._seeded_value(args, "gold", args.gold)
    if gold is not None:
        fields.append((_U2024_GOLD[0], _U2024_GOLD[1], "GOLD", gold))
    ring = character._seeded_value(args, "ring", args.ring)
    if ring is not None:
        fields.append((_U2024_RING[0], _U2024_RING[1], "RING", ring))
    scoreb = []
    score = character._seeded_value(args, "total_score", args.total_score)
    if score is not None:
        scoreb.append((_U2024_SCORE[0], _U2024_SCORE[1], "SCORE", score))
    if not fields and not scoreb:
        return
    # WARNING: ASCENDING BIT ORDER IS THE WIRE ORDER, and maskA's fields come
    # BEFORE maskB's -- 0x04ff36c0 walks maskA's ladder to the end and
    # only then tests maskB. Sorting the two lists together would put a
    # maskB bit 0x1 field first and desynchronise everything after it.
    fields.sort()
    maskA = 0
    maskB = 0
    body = b""
    for bit, _off, _name, val in fields:
        maskA |= bit
        body += struct.pack(">I", val & 0xFFFFFFFF)
    for bit, _off, _name, val in scoreb:
        maskB |= bit
        body += struct.pack(">I", val & 0xFFFFFFFF)
    fields = fields + scoreb
    wire.send(conn, outbound, wire.inner_msg(0x2024,
                                   struct.pack(">II", maskA, maskB) + body,
                                   wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld]    -> 0x2024 wallet maskA=0x%08X maskB=0x%08X  %s  -- "
          "Status screen fields, each named from the window's own draw "
          "code at 0x050e13ca.."
          % (maskA, maskB, ", ".join("%s=%d (+0x%x)" % (n, v, o)
                                     for _b, o, n, v in fields)), flush=True)


# ---------------------------------------------------------------------------
# THE WALLET AS A LEDGER -- 2026-09-11, the day something first SPENDS it.
#
# GOLD and RING were per-character stored numbers (festore columns `gold` and
# `ring`, seeded once from --gold / --ring by _seeded_value) that nothing but
# the enchant cost and mail attachments ever moved. The shops, the inn and the
# bank now charge and credit them, and every one of those goes through the
# four helpers below so the rule is written once:
#
#   * the STORE is the balance and the 0x2024 push is only a view of it --
#     write first, then push;
#   * an UNSET flag means that currency's channel is OFF (not "broke"): a cost
#     in it is waived and logged, a credit in it is dropped and logged. Same
#     contract as feitems._debit, which predates these.
#
# KEY: RINGS: the war worker AWARDS them and this file SPENDS them (the Ring
# Shop). Both sides use rings_get / rings_add; the balance is festore's `ring`
# column, the same one wallet_push has served since 2026-09-04.
def gold_get(args):
    """The character's GOLD, or None when no gold wallet is served."""
    return character._seeded_value(args, "gold", getattr(args, "gold", None))


def rings_get(args):
    """The character's RINGS, or None when no ring wallet is served."""
    return character._seeded_value(args, "ring", getattr(args, "ring", None))


def rings_set(args, n):
    """Store the character's RINGS (clamped at 0). No push -- see wallet_push."""
    return character._store_char_field(args, "ring", max(0, int(n)))


def rings_add(args, n):
    """Add `n` RINGS (negative spends) to the stored balance; returns the new
    balance, or None when nothing was stored. With --ring unset the stored
    column is still written (a war award must not vanish because the display
    channel is off) -- it is served the moment the flag is set."""
    have = rings_get(args)
    if have is None:
        try:
            have = int(character._load_char_field(args, "ring", 0) or 0)
        except (TypeError, ValueError):
            have = 0
    new = max(0, int(have) + int(n))
    return new if rings_set(args, new) else None


def wallet_short(args, gold=0, rings=0):
    """None when the character can pay `gold` and `rings`, else the currency
    that is short ("gold" / "rings"). A currency whose channel is off (flag
    unset) never runs short -- see the block comment. Writes nothing."""
    gold, rings = max(0, int(gold or 0)), max(0, int(rings or 0))
    g = gold_get(args) if gold else None
    if gold and g is not None and int(g) < gold:
        return "gold"
    r = rings_get(args) if rings else None
    if rings and r is not None and int(r) < rings:
        return "rings"
    return None


def wallet_charge(conn, outbound, mode, be, args, gold=0, rings=0, why=""):
    """Spend `gold` and `rings` together, or neither. Returns (ok, short) where
    `short` names the currency that ran out ("gold" / "rings") on a refusal.

    Checks BOTH balances before writing EITHER, so a purchase priced in both
    can never take the gold and then fail on the rings."""
    gold, rings = max(0, int(gold or 0)), max(0, int(rings or 0))
    short = wallet_short(args, gold, rings)
    if short:
        return False, short
    g = gold_get(args) if gold else None
    r = rings_get(args) if rings else None
    waived = []
    if gold and g is None:
        waived.append("%d gold (--gold unset: no gold wallet)" % gold)
    if rings and r is None:
        waived.append("%d rings (--ring unset: no ring wallet)" % rings)
    wrote = []
    if gold and g is not None and character._store_char_field(args, "gold", int(g) - gold):
        wrote.append("gold %d -> %d" % (int(g), int(g) - gold))
    if rings and r is not None and rings_set(args, int(r) - rings):
        wrote.append("rings %d -> %d" % (int(r), int(r) - rings))
    if wrote:
        wallet_push(conn, outbound, mode, be, args)
    if wrote or waived:
        print("[feworld]    wallet %s: %s%s" % (why, "; ".join(wrote) or "nothing",
                                               ("  WAIVED " + "; ".join(waived))
                                               if waived else ""), flush=True)
    return True, None


def wallet_credit(conn, outbound, mode, be, args, gold=0, rings=0, why=""):
    """Add `gold` / `rings` to the stored balances and push the wallet. The
    client's own GOLD setter (0x04ff47a0) prints "%dGold入手しました。" when
    the value it is handed GREW, so a credit shows the player what they got
    without us sending a word. Returns the gold actually credited."""
    gold, rings = max(0, int(gold or 0)), max(0, int(rings or 0))
    done, g = [], None
    if gold:
        g = gold_get(args)
        if g is None:
            print("[feworld]    wallet %s: %d gold NOT credited -- --gold is "
                  "unset, so no gold wallet is served" % (why, gold), flush=True)
            gold = 0
        elif character._store_char_field(args, "gold", int(g) + gold):
            done.append("gold %d -> %d" % (int(g), int(g) + gold))
        else:
            gold = 0
    if rings:
        new = rings_add(args, rings)
        if new is not None:
            done.append("rings -> %d" % new)
    if done:
        wallet_push(conn, outbound, mode, be, args)
        print("[feworld]    wallet %s: %s" % (why, "; ".join(done)), flush=True)
    return gold
