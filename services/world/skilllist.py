"""The acquired skill list (0x1075), class level rows and the skill palette."""
import struct
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import character, entities, itemrecords, progression, sess, wire

def acquired_skills(args):
    """The session character's acquired skill ids, as a list of ints."""
    out = []
    for k in character._load_char_field(args, "skills", None) or []:
        try:
            out.append(int(k) & 0xFFFFFFFF)
        except (TypeError, ValueError):
            continue
    return out


def acquire_skill(args, skill_id):
    """Add one skill id. Returns (was_new, the full list).

    WARNING: DEDUPED. 0x303E's list is read into a fresh std::list every time
    (0x05024be5 allocates a node per id with no membership check), so serving
    the same id twice puts the same skill in the window twice.
    """
    rows = acquired_skills(args)
    if skill_id in rows:
        return None, rows          # None = already known, nothing written
    rows.append(skill_id)
    # WARNING: REPORT WHAT THE STORE SAID, not what we intended. The first version
    # returned True here and the caller printed "PERSISTED" off it -- so a
    # failed write (no matching stored character) would have logged success and
    # the player would have lost the points silently. `check-our-own-logs` is
    # worth nothing if the log is generous with itself.
    return character._store_char_field(args, "skills", rows), rows


# ---------------------------------------------------------------------------
# 0x1075 -- THE ACQUIRED SKILL LIST. Skills persisted server-side but never came
# back, so they vanished on every relaunch. This is the message that returns
# them, and it was found by walking the DRAW site backwards, not by name.
#
# THE CHAIN, bottom up, every link measured 2026-08-25:
#
#   the skill row's "acquired" flag is
#       0x050D3A06  cmp word [unit + skillId*2 + 0xA14], 0
#                   seta al ; mov [widget+0x85], al
#   and the same array gates whether a row is offered at all
#       0x050D50C1  cmp word [unit + prereq*2 + 0xA14], 0
#   the array is built by the CONSTRUCTOR at
#       0x0507D5B6  lea edi,[esi+0xA14] ; mov ecx,0x252 ; rep stosd
#                   0x252 dwords = 0x948 bytes = **0x4A4 u16 entries**, ending
#                   at 0x135C -- and 0x4A4 is exactly the bound the client's own
#                   accessors check (0x0507D608 add, 0x0507D62A get) and exactly
#                   the bound the learn path checks on the SKILL ID
#                   (0x050D4D23 `cmp ax,0x4A4; jae`). So the index IS the id.
#   the only network writer of that array is
#       0x05052180  0x0504CB30(objId,1) -> 0x0504D410 -> unit
#                   [u16 count] then count x { [u16 idx][u8 value] }
#                   [unit + idx*2 + 0xA14] = value
#   which is bit 3 of a four-way mask decoder
#       0x05051E20  [u32 mask]
#                     &1 -> 0x05051E80  [u8 n] x {u8 idx, u8 v} -> [unit+0x9F0+idx]
#                                       (and [unit+0x3AC]) -- a different array
#                     &2 -> 0x05052200      &4 -> 0x05051F10   -- not needed here
#                     &8 -> 0x05052180  THE SKILLS
#   whose only caller is the arm at 0x0503A423, which fedisp resolves to
#       0x1075, dispatcher c0 (0x05039FA0 -- `cmp eax,0x1075 / je` is literally
#       the first comparison in that dispatcher, before its own jump table)
#
# 🚫 THIS IS WHY THE NAME SEARCHES FAILED. 0x1075 has NO entry in the client's
# NG table and no registered request, so no name sweep and no reply-pair sweep
# could reach it -- the same blind spot that hid 0x2033 and 0x1100. The two ids
# whose NAMES said "acquired skill list" (0x1073/0x1074) only log, and 0x303E,
# picked the same way, was a debug echo. Three for three: walk the arm, not the
# name.
#
# WARNING: THE INDEX IS UNBOUNDED ON THE WIRE. 0x05052180 writes
# `[unit + idx*2 + 0xA14]` with **no range check** -- unlike every one of the
# client's own accessors. An idx of 0xFFFF writes ~0x1FFFE bytes past the array,
# straight through [unit+0x1360] (the dress-applier witness) and [unit+0x138C]
# (the Field Out countdown). skill_list_body() drops anything >= 0x4A4 rather
# than clamping it, because clamping would silently mark the WRONG skill.
#
# WARNING: NOT LIVE-TESTED. What the u8 VALUE means is also not measured: the predicate
# is `!= 0`, but 0x0507D600 ADDs into the same slot, which reads like a level or
# a count rather than a flag. 1 is the smallest thing that satisfies every
# reader found; --skill-list-value exists to try others without a redeploy.
# ---------------------------------------------------------------------------
# 0x1075 MASK BIT 0 -- THE CLASS LEVEL ARRAY, and it is why a skill you own
# still cannot be equipped.
#
# Reported live 2026-08-25: bit 3 landed, the skills came back and the window
# shows them as owned -- and double-clicking one to equip does nothing. The
# skill window sends NOTHING when you do that (its only outbound builder in the
# whole class is the 0x2049 GET! at 0x050D4AA8/0x050D4AB4), so equip is a purely
# LOCAL decision and something local is refusing it.
#
# The refusal is a validator that returns small reason codes, and one of its
# arms is:
#
#     0x05076195  movzx eax, byte [edx + ebx + 0x9F0]   ; [unit + class + 0x9F0]
#     0x0507619D  cmp   ax, word [ebp + 0x86]           ; the skill's REQUIRED LEVEL
#     0x050761A6  sbb   eax, eax
#     0x050761A8  and   eax, 4                          ; 0 = allowed, 4 = too low
#
# `[unit + 0x9F0 + classId]` is the player's LEVEL IN THAT CLASS (the class id is
# `[unit+0x3AB]`, the same byte 0x050D4E24 uses to pick the skill table). It is
# read as a byte in a dozen places -- 0x050CCA96, 0x050DEA4E/0x050DEA69,
# 0x050DFF01, 0x050E129E, 0x050E439F, 0x050E5298 -- and its constructor clears
# it at 0x0507D596. **We have never sent it, so every class sits at level 0 and
# every skill's level requirement fails.**
#
# It is bit 0 of the SAME 0x1075 record the acquired list rides on
# (0x05051E80): [u8 count] then count x { [u8 idx][u8 value] }, writing
# [unit + 0x9F0 + idx] and ALSO [unit+0x3AC] with the last value written.
#
# WARNING: A LEVEL IS A CLAIM, and we do not track progression -- so this is a knob
# with a chosen default, said out loud, rather than a number that arrives by
# accident. The accident is the status quo: nobody chose 0 either, and 0 is what
# has been blocking every equip. That is [[tm-start-money-is-zero]] read the
# other way round -- the unchosen zero is the bug, not the safe option.
# WARNING: Bit 0 also writes [unit+0x3AC], which the Status screen draws, so whatever
# is set here is VISIBLE to the player as their level. Do not raise it casually.
SKILL_LIST = (0x1075, "acquired skill list (unnamed -- no NG entry, no request)",
              0x0503A423)
SKILL_ARRAY_MAX = 0x4A4      # 0x0507D5B6's own `mov ecx,0x252` (= 0x4A4 u16)
SKILL_MASK_BIT = 0x8         # 0x05051E20 bit 3 -> 0x05052180
CLASS_MASK_BIT = 0x1         # 0x05051E20 bit 0 -> 0x05051E80
CLASS_ARRAY_MAX = 0x100      # [u8 idx] -- the index is a BYTE, so 0..255
# WARNING: RETRACTED 2026-09-09. This said: "it starts at [unit+0x9F0] and the SKILL
# array starts at [unit+0xA14], so there are 0x24 = 36 bytes of it, and `all:N`
# fills exactly that -- not one byte into the skills." The gap is real and the
# skills are where it said, but the 36 bytes are NOT all levels: the Status
# screen draws `Exp %d/%d` from `[unit + class*4 + 0x9F8]` and `[unit+0x135c]`
# (0x050e1388), so a per-class EXP DWORD array sits at +0x9F8, immediately
# behind 8 bytes of levels. `all:30` therefore wrote level 30 over the EXP of
# every class, and the live Status screen read **Exp 505290270/1679**:
# 505290270 is 0x1E1E1E1E, four bytes of 30. Seven levels + seven dwords +
# the skill array fit the gap exactly, and FE_CLASS_BASIC_PARAM_DATA has
# exactly 7 rows.
CLASS_LEVEL_SLOTS = 7        # bytes at +0x9F0 (+0x9F7 is padding)
CLASS_EXP_BASE = 0x8         # +0x9F8, as an index into the same byte array


def self_class_id(args):
    """The class index the CLIENT will look this player up under.

    KEY: It is `[unit+0x3AB]`, and we already serve that byte -- as `look1` out of
    the stored character, bit 0x04 of the avatar sub-record. It is not
    --status-class and it is not a constant.

    THIS IS WHY THE FIRST ATTEMPT DID NOTHING. `--class-levels 1:30` wrote level
    30 into class index 1 while the stored character's look1 is 2, so
    [unit+0x9F0+2] stayed at 0 and the gate it was meant to open never saw a
    different number. The level theory was not falsified by that attempt -- the
    attempt never tested it. WARNING: Deriving the index from the same place the byte itself
    comes from is the only way these two cannot drift apart again.

    WARNING: `look1` is feworld's own label for +0x3AB and it is an APPEARANCE guess
    inherited from felobby's CHAR_FIELDS. The client uses that byte as a CLASS:
    0x050D4E24 feeds it to 0x05006F40 to pick the skill table, and 0x05076152
    feeds it to the level lookup. Two independent class uses, no appearance use
    found. The name is kept for store compatibility; the meaning is class.
    """
    ch = entities._self_char(args)
    if not ch:
        return None
    try:
        return int(ch.get("look1", 0)) & 0xFF
    except (TypeError, ValueError):
        return None


# WARNING: THE CLASS-LEVEL ARRAY IS 8 BYTES LONG, AND WHAT FOLLOWS IT IS THE EXP.
# Read 2026-09-09 off the Status screen's own draw (0x050e1388):
#
#     mov ecx, [esi+0x135c]                 ; the SECOND %d
#     mov dl, [esi+0x3ab]                   ; the class
#     mov eax, [esi + edx*4 + 0x9f8]        ; the FIRST %d  <-- per-class EXP
#     sprintf(buf, "%d/%d", eax, ecx)
#
# so the unit lays out: class LEVELS as bytes at +0x9F0, then a per-class EXP
# DWORD array at +0x9F8, then the skill array at +0xA14 -- and 7 classes fit
# each of them exactly (FE_CLASS_BASIC_PARAM_DATA has 7 rows).
#
# The bit-0 setter (0x05051edf `mov [ecx+esi+0x9f0], dl`) takes the index as a
# u8 and is UNBOUNDED, so `--class-levels all:30` wrote 36 levels straight
# through the EXP array: the live Status screen read
# **Exp 505290270/1679**, and 505290270 is 0x1E1E1E1E -- four bytes of level 30.
# Levels are clamped to the array here, on the way OUT, because the store was
# seeded with 36 of them before this was known and a stored table cannot be
# fixed by changing a flag.
def class_exp_rows(self_class, exp):
    """The four (index, byte) pairs that put `exp` in the current class's EXP
    dword, addressed through the SAME byte array the levels use.

    That is not a trick, it is the only wire path: 0x1075's four bits are the
    level bytes (0x1), +0x135c (0x2), an effect record (0x4) and the skill
    words (0x8) -- nothing carries +0x9F8, and the level setter's index reaches
    it because it is unbounded. Little-endian, because the client reads the
    result as a dword."""
    if self_class is None:
        return []
    base = CLASS_EXP_BASE + int(self_class) * 4
    v = max(0, int(exp)) & 0xFFFFFFFF
    return [(base + i, (v >> (8 * i)) & 0xFF) for i in range(4)]


def class_level_rows(spec, self_class=None):
    """Parse `--class-levels` (`CLASS:LEVEL,...`) into (idx, level) pairs.

    CLASS may be the literal `self`, which resolves to the stored character's
    own class -- see self_class_id(). That is the default, and a hardcoded index
    is the thing it exists to stop anyone writing again.

    Out-of-range indices are DROPPED for the same reason skill ids are: the
    decoder at 0x05051E80 reads the index as a u8, so it cannot overrun on its
    own -- but a level for a class that does not exist is a silent lie in the
    array rather than an error, and dropping it says so in the log.
    """
    rows, dropped = [], []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            idx_s, lvl_s = part.split(":")
            lvl = int(lvl_s, 0)
            if idx_s.strip().lower() == "all":
                # WARNING: LIVE 2026-09-05: EVERY item refused with "Wrong class;
                # can't equip", which is reason code 4 of the validator at
                # 0x05076090 -- `[unit+0x9F0 + [unit+0x3AB]] < [tbl+0x86]`,
                # i.e. the level in the class the CLIENT thinks the unit is.
                # We set only the class our stored `look1` names, so if that
                # byte lands differently the level sits in the wrong slot and
                # every item is "too low". `all:N` fills the whole array,
                # which cannot miss.
                rows.extend((i, lvl) for i in range(CLASS_LEVEL_SLOTS))
                continue
            if idx_s.strip().lower() == "self":
                if self_class is None:
                    dropped.append(part + "  (no stored character yet)")
                    continue
                idx = self_class
            else:
                idx = int(idx_s, 0)
        except ValueError:
            dropped.append(part)
            continue
        if 0 <= idx < CLASS_ARRAY_MAX and 0 <= lvl <= 0xFF:
            rows.append((idx, lvl))
        else:
            dropped.append(part)
    return rows, dropped


def _clamp_class_rows(rows, why=""):
    """Drop level rows outside the 7-slot array. See CLASS_LEVEL_SLOTS."""
    keep = [(i, v) for i, v in rows if 0 <= int(i) < CLASS_LEVEL_SLOTS]
    if len(keep) != len(rows):
        print("[feworld]    class levels: %d of %d rows are outside the "
              "%d-slot array and were DROPPED%s -- index %d onward is the "
              "per-class EXP dwords at +0x9F8, and writing levels over them is "
              "what made the Status screen read Exp 505290270 (0x1E1E1E1E)"
              % (len(rows) - len(keep), len(rows), CLASS_LEVEL_SLOTS,
                 (" (%s)" % why) if why else "", CLASS_LEVEL_SLOTS), flush=True)
    return keep


def stored_class_levels(args, self_class):
    """This CHARACTER's class levels, seeded from `--class-levels` once.

    KEY: THE LEVEL TABLE IS PROGRESSION, so it cannot be a flag. `--class-levels
    all:30` gave every class of every player on the server level 30; a player
    who levelled could not be told from one who had not, and nothing a player
    did could change the number. Same contract as _seeded_value one type up: the
    flag is the STARTING table for a character with none stored, and after that
    the store is served.

    Stored as {class index: level} with STRING keys, because that is what JSON
    does to an int key and pretending otherwise is how a dict read back from a
    store stops matching the one written to it. Converted here, once.

    Returns (rows, dropped) exactly as class_level_rows does, so the caller and
    its whole log block are unchanged.

    WARNING: AN EMPTY `--class-levels` STILL SENDS NOTHING, whatever the store holds,
    and that is not a corner case -- it is the same rule _seeded_value states
    for an unset flag ("unset must not look like broke"). Here it is also
    PROTOCOL-CRITICAL: an empty flag clears CLASS_MASK_BIT, and a mask bit set
    with no block behind it desynchronises the whole 0x1075 record (bits 0/1/2
    each promise an array to 0x05051E80 / 0x05052200 / 0x05051F10). So the
    admin's off switch has to win over the store, or turning the class block
    off would stop working the moment a table had been seeded -- which is
    exactly what fe_ui_test caught when this was written the other way round.

    2026-09-11: the default seed is `self:1` (DEFAULT_CLASS_LEVELS) -- a NEW
    character starts at Lv1 in its own class and climbs by EXP (feprog's
    0x2059). A character that already has a table keeps it: prod's stored
    Lv30s stay 30 (nobody is demoted by a deploy); `!setlevel N` resets one.
    """
    spec = getattr(args, "class_levels", progression.DEFAULT_CLASS_LEVELS)
    if not (spec or "").strip():
        return [], []
    stored = character._load_char_field(args, "class_levels", None)
    if stored:
        rows, bad = [], []
        for k, v in sorted(stored.items() if isinstance(stored, dict)
                           else stored, key=lambda kv: int(kv[0])):
            try:
                idx, lvl = int(k), int(v)
            except (TypeError, ValueError):
                bad.append("%r:%r  (unreadable stored row)" % (k, v))
                continue
            if 0 <= idx < CLASS_ARRAY_MAX and 0 <= lvl <= 0xFF:
                rows.append((idx, lvl))
            else:
                bad.append("%d:%d  (stored, out of range)" % (idx, lvl))
        if rows:
            return rows, bad

    rows, bad = class_level_rows(spec, self_class)
    if rows and character._store_char_field(
            args, "class_levels", {str(i): l for i, l in rows}):
        print("[feworld]    class_levels: no stored table for account=%r "
              "charid=%s -- seeded %d class(es) from --class-levels; from now "
              "on the STORE is served, not the flag"
              % (sess._SESSION.get("account"), sess._SESSION.get("charid"), len(rows)),
              flush=True)
    return rows, bad


def skill_list_body(skill_ids, value=1, class_rows=()):
    """Build 0x1075 the way 0x05051E20 + 0x05052180 read it.

    Ids at or past SKILL_ARRAY_MAX are DROPPED, not clamped: the decoder has no
    bounds check of its own, and folding an out-of-range id onto the last slot
    would light up a skill the player does not have instead of writing past the
    object. Both are wrong; only one is silent.
    """
    rows, dropped = [], []
    for sid in skill_ids:
        sid = int(sid)
        if 0 <= sid < SKILL_ARRAY_MAX:
            rows.append(sid)
        else:
            dropped.append(sid)
    class_rows = list(class_rows)
    mask = (CLASS_MASK_BIT if class_rows else 0) | SKILL_MASK_BIT
    body = struct.pack(">I", mask)
    # WARNING: BIT ORDER IS WIRE ORDER. 0x05051E20 tests bit 0 first and bit 3 last,
    # so the class block must precede the skill block or both decode as garbage.
    if class_rows:
        body += struct.pack(">B", len(class_rows))
        for idx, lvl in class_rows:
            body += struct.pack(">BB", idx, lvl)
    body += struct.pack(">H", len(rows))
    for sid in rows:
        body += struct.pack(">HB", sid, value & 0xFF)
    return body, rows, dropped


def skill_list_push(conn, outbound, mode, be, args):
    """Push 0x1075 so the skills the store already holds come back."""
    ids = acquired_skills(args)
    grant = getattr(args, "skill_grant", progression.DEFAULT_SKILL_GRANT)
    if grant == "class":
        # KEY: 2026-09-11, THE DEFAULT: the stored skills PLUS what the class
        # starts knowing -- FE_CLASS_BASIC_PARAM_DATA's +0x70 array, served as
        # owned and FREE (sp_spent skips them). It carries BOTH halves a Lv1
        # character needs: the weapon/armour PROFICIENCIES the equip
        # validator's code 5 tests on the starting gear (Warrior 437..441/444/
        # 447, Scout 442/436/446, Sorcerer 443/445) and the starting SKILLS
        # (Warrior 0 basic attack + 5; Scout 130/135 basic bow/dagger + 140;
        # Sorcerer 270 basic magic + 275). Nothing in the client grants them
        # itself: the class record's only readers are the validator's class
        # check (0x0507615B) and UI. 'items' served proficiencies off the BAG
        # instead, which let any class wear any family it happened to carry.
        start = progression.granted_class_skills(args)
        ids = sorted(set(ids) | set(start))
        print("[feworld]    --skill-grant class: %d stored + the class's own "
              "starting set %s (FE_CLASS_BASIC_PARAM_DATA +0x70, free)"
              % (len(acquired_skills(args)), start or "EMPTY -- no class "
                 "row; nothing granted"), flush=True)
    elif grant == "all":
        # THE PROBE THAT ANSWERED IT, kept only as an A/B. Every item refused
        # with "Wrong class; can't equip." -- and that TEXT is sub-code 3 of
        # message 0xF0FF (the catalogue row at 0x5281d80), which the caller
        # emits for validator code 5 (0x050b66b9), not the class code. Code 5
        # is the PREREQUISITE SKILL arm (0x050761AC). Granting all 1188 ids
        # proved that; it also hands the character every skill in the game,
        # which is why it is no longer the default.
        ids = list(range(SKILL_ARRAY_MAX))
        print("[feworld]    --skill-grant all: serving every skill id as owned "
              "-- A PROBE, not a setting. It grants the whole skill tree; use "
              "`items` unless you are re-running the 2026-09-05 A/B.",
              flush=True)
    elif grant == "items":
        # THE NARROW FORM. An item names up to two prerequisite skills at its
        # table record's +0x88 and the validator (0x050761AC) passes when ONE
        # of them is owned in [unit+0xA14 + id*2]; an item that names none
        # (both 0xffff) skips the check. They are weapon/armour PROFICIENCIES,
        # one per family -- 436 dagger, 437..439 axe/sword, 440 great axe,
        # 441 hammer, 442 bow, 443 wand, 444 shield, 445..447 armour weights --
        # so what a character needs is exactly the set its own bag names.
        # Anything it does not carry stays unlearned.
        bag = [int(no) for _u, no, _f, _c in itemrecords.item_rows(args)]
        need = fegamedata.item_skills(bag)
        ids = sorted(set(ids) | set(need))
        print("[feworld]    --skill-grant items: %d stored skill(s) + the %d "
              "PREREQUISITE skill(s) the %d item(s) in this bag name (%s) -- "
              "the ids at each item table record's +0x88, which is what "
              "validator code 5 tests. Nothing else is granted."
              % (len(ids) - len(need) if not set(need) & set(ids) else
                 len(acquired_skills(args)), len(need), len(bag),
                 ", ".join(str(x) for x in need) or "none"), flush=True)
    self_class = self_class_id(args)
    class_rows, class_bad = stored_class_levels(args, self_class)
    # WARNING: the levels are 7 bytes; index 8 onward is the per-class EXP dwords.
    class_rows = _clamp_class_rows(class_rows, "stored table")
    # ...and the EXP goes in on the SAME bit, as the dword the Status screen
    # draws as the first half of "Exp %d/%d". 2026-09-11: EXP bytes FIRST and
    # the own class's level LAST, because the bit-0x1 setter also stores the
    # last value it writes into [unit+0x3AC] (0x05051EEA) -- a unit-list row
    # draws that as the level (0x050E43B7), and it used to get an EXP byte.
    own = [r for r in class_rows if self_class is not None and r[0] == self_class]
    class_rows = (progression.exp_class_rows(args)
                  + [r for r in class_rows if r not in own] + own)
    body, rows, dropped = skill_list_body(ids, args.skill_list_value, class_rows)
    mid, name, arm = SKILL_LIST
    wire.send(conn, outbound, wire.inner_msg(mid, body, wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    mask = (CLASS_MASK_BIT if class_rows else 0) | SKILL_MASK_BIT
    print("[feworld] -> 0x30 inner 0x%04X %s  mask=0x%X %d skill(s) value=%d "
          "(%d body bytes, arm %08x) -- writes [unit+0x%X + id*2] on unit %d; "
          "the Status/skill window reads that array directly (0x050D3A06)"
          % (mid, name, mask, len(rows), args.skill_list_value,
             len(body), arm, 0xA14, wire.unit_id_of(args)), flush=True)
    if rows:
        print("[feworld]    ids: %s" % ", ".join(str(x) for x in rows),
              flush=True)
    print("[feworld]    this character's class ([unit+0x3AB], served as the "
          "stored `look1`) = %s"
          % ("UNKNOWN -- no stored character, so `self:` rows are dropped"
             if self_class is None else self_class), flush=True)
    if class_rows:
        print("[feworld]    class levels: %s -- [unit+0x9F0+class] is what the "
              "equip validator compares against a skill's required level "
              "(0x05076195); at 0 every skill is refused and double-click does "
              "nothing"
              % ", ".join("class %d = %d" % r for r in class_rows), flush=True)
    if class_bad:
        print("[feworld]    DROPPED unparseable/out-of-range --class-levels "
              "entries: %s" % ", ".join(class_bad), flush=True)
    if dropped:
        print("[feworld]    DROPPED %d id(s) outside 0..%d: %s -- the client's "
              "decoder has NO bounds check, so these would have written past "
              "the array and through [unit+0x1360]/[unit+0x138C]"
              % (len(dropped), SKILL_ARRAY_MAX - 1,
                 ", ".join(str(x) for x in dropped)), flush=True)


# ---------------------------------------------------------------------------
# THE SKILL PALETTE: 0x2029 AND 0x202A ARE THE SAME MESSAGE IN BOTH DIRECTIONS
#
# 2026-09-06. Double-clicking a skill in the skill window ALREADY sent a
# request and feworld had no handler for it, so nothing reached the skill bar.
# The reason the request looked like a dead end is that it registers no reply
# pair -- and it registers none because the answer is the SAME ID COMING BACK.
#
#     out  0x051622F0   [u16 1 << slot][u16 skillId][u8 1][u8 0]
#     out  0x051623B0   [u16 1 << slot]                        (clear a slot)
#     in   c0 arm 0x0503A739 -> 0x0503CF60                     (set)
#     in   c0 arm 0x0503A753 -> 0x0503D0F0                     (clear)
#
# and the inbound workers read a MASK plus one record per set bit, so one
# message can carry the whole palette:
#
#     [u16 mask]                                        0x0503CF70
#     per set bit, ascending:  [u16 skillId][u8][u8]     0x0503CF90/9C/A8
#
# KEY: THE CLIENT DOES NOT APPLY ITS OWN CLICK. The sender (vtable 0x05297688
# +0x24) and the local setter (+0x28, 0x05162360) are two different methods and
# the double-click only calls the first; the inbound worker calls the second
# (0x0503CFC8, `call [vtbl+0x28]` on the singleton at 0x053491E8). That is the
# equip pattern exactly -- 0x102A all over again -- and it is why pressing a
# skill did nothing at all rather than doing something and losing it.
#
# The local setter takes four arguments (`ret 0x10`) and uses two: the slot and
# the skill id. It bails unless 0 <= slot < 8 (0x0516236A/0x0516236F), so the
# palette is EIGHT slots even though the mask is sixteen bits wide, and it
# resolves the id through 0x0505F4B0 into `[palette + slot*12 + 0x54]`.
#
# WARNING: BOTH WORKERS DEREFERENCE TWO SINGLETONS WITH NO NULL CHECK -- the palette
# at 0x053491E8 (per set bit) and 0x053491EC (unconditionally, at 0x0503CFE9,
# even for an empty mask). Both are zeroed at startup (0x05155FA0) and built
# with the in-game HUD, so this must never be sent to a client that has no HUD.
# Answering a request the client itself just sent is safe by construction -- it
# sent it FROM that object. The entry replay is not, which is why it is a knob.
PALETTE_SLOTS = 8               # 0x0516236F `cmp edi, 8 / jge` -- the bail
PALETTE_MASK_BITS = 16          # 0x0503CFCC `cmp esi, 0x10` -- the loop bound


def palette_rows(args):
    """The stored palette as a sorted [(slot, skill id)], slots 0..7."""
    out = {}
    for row in (character._load_char_field(args, "palette", []) or []):
        try:
            slot, skill = int(row[0]), int(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= slot < PALETTE_SLOTS and 0 <= skill <= 0xFFFF:
            out[slot] = skill
    return sorted(out.items())


def palette_set_body(rows):
    """0x2029 outbound: [u16 mask] + [u16 skill][u8 1][u8 0] per set bit.

    The two trailing bytes are what the client's own builder sends (1 then 0)
    and what its reader consumes; the local setter ignores both. Copying the
    client is the cheap way to be right about a field nothing reads yet.
    """
    rows = [(s, k) for s, k in rows if 0 <= s < PALETTE_MASK_BITS]
    mask = 0
    for slot, _skill in rows:
        mask |= 1 << slot
    body = struct.pack(">H", mask)
    for _slot, skill in sorted(rows):
        body += struct.pack(">HBB", skill & 0xFFFF, 1, 0)
    return body


def palette_clear_body(slots):
    """0x202A outbound: [u16 mask] and nothing else (worker 0x0503D0F0)."""
    mask = 0
    for slot in slots:
        if 0 <= int(slot) < PALETTE_MASK_BITS:
            mask |= 1 << int(slot)
    return struct.pack(">H", mask)


def palette_push(conn, outbound, mode, be, args, rows, why):
    """Send the palette the client should be showing."""
    if not sess._SESSION.get("in_field"):
        print("[feworld]    0x2029 NOT sent (%s): no field yet. Both workers "
              "deref the HUD singletons 0x053491E8/0x053491EC with no null "
              "check, and they are null until the in-game HUD is built."
              % why, flush=True)
        return 0
    if not rows:
        print("[feworld]    palette (%s): nothing stored, nothing to push"
              % why, flush=True)
        return 0
    wire.send(conn, outbound,
         wire.inner_msg(0x2029, palette_set_body(rows), wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0x2029 SKILL PALETTE %s (%s) -- one record "
          "per set bit, applied by 0x05162360 into [palette + slot*12 + 0x54]"
          % (", ".join("slot %d = skill %d" % r for r in rows), why), flush=True)
    return len(rows)
