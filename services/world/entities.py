"""Drawing units: the player's own avatar, other players, NPCs and monsters (0x1006)."""
import struct
from . import arrival, equipment, movement, progression, sess, skilllist, spawns, wire

def _self_char(args):
    """The session's stored character record, or None -- the SAME dict felobby
    served at the select screen, read back through felobby's own roster API so
    the two cannot drift."""
    acct, charid = sess._SESSION.get("account"), sess._SESSION.get("charid")
    if not acct or charid is None:
        return None
    try:
        import felobby
    except ImportError:
        return None
    for c in (felobby.load_roster(felobby._default_store(), acct) or []):
        if c.get("charid") == charid:
            return c
    return None


# The avatar sub-record behind mask1 bit0. Bit -> (key in felobby's stored
# character, struct code, the client offset it lands on).
#
# THE OFFSETS ARE A CROSS-CHECK, NOT A GUESS. They were derived here by walking
# 0x5079d60 (`test bl,<bit>` then a reader into `[esi+<off>]`), and they match
# felobby's CHAR_FIELDS -- decoded in a different session, from a different
# message, against the record->appearance copy at 0x04fe7519 -- offset for
# offset: +0x3aa +0x3ab +0x3b0 +0x3b4 +0x3b6 +0x3b7 +0x3b8. Two independent
# decodes agreeing on seven offsets is the strongest confirmation this codebase
# gets, and it is why the values below are READ FROM THE STORED CHARACTER
# rather than invented: the client already built m_face03_1.mdl / m_hair03_04_00
# / m_body.mdl from exactly these numbers at the select screen.
_AVATAR_SUB = [
    (0x01, "name",  "cstr", 0x389),
    (0x02, "sex",   "B",    0x3AA),   # -> the model-path selector, see below
    (0x04, "look1", "B",    0x3AB),
    (0x08, "f24",   "I",    0x3B0),
    # WARNING: f28 IS THE METAMORPHOSIS FORM, NOT A CLASS -- STATIC 2026-09-06 (2nd
    # session). Its low 6 bits are a ONE-HOT selector: 0x01/0x02/0x04/0x08/0x10
    # and nothing else. The client proves it with a hand-written jump table
    # (0x05158f93: `eax = ([+0x3b4]&0x3f) - 1`, byte map at 0x05159060 =
    # [0,1,5,2,5,5,5,3,5,5,5,5,5,5,5,4], so only indices 0/1/3/7/15 -- the
    # one-hot values -- reach a distinct arm), and 0x0506c7d0 / 0x05039090 both
    # convert it with a shift-until-zero loop, i.e. to a BIT INDEX.
    #
    # That bit index keys METAMORPHOSIS_DATA (container 0x535ccc0, tag xref
    # 0x051a9d29), NOT FE_CLASS_BASIC_PARAM_DATA (container 0x535caa0, tag xref
    # 0x05195199, which is keyed by [unit+0x3AB] -- our `look1`, see
    # self_class_id()). 0x0506c7d0 rebuilds `<name>.mdl` / `<name>b.tex` from
    # [rec+0x94], so a non-zero f28 SWAPS THE PLAYER'S MODEL for a transformed
    # form and switches its jump row.
    #
    # WARNING: So serving f28 to satisfy the cast gate's classless branch is the wrong
    # lever -- it transforms the character. The other operand of that branch is
    # [unit+0x48] bit 0x80000 = "/USESKILLRDY" (the client's own name for it,
    # 0x0507e4b5 -> 0x052dec60), and its ONLY writer in the whole image is the
    # entity record's own tail (0x0503aed7), from the worn array as it stands
    # when the record is applied. See static-2026-09-06/README.md addendum 2.
    #
    # WARNING: AND THE HUMAN VALUE IS 0x200, NOT 0 (2026-09-11): bit 0x200 is HUM in
    # the client's own RACE names, the self ctor stamps it, and the summon
    # menu reads it. A stored 0 is served as SELF_FORM_HUM -- see the packer.
    (0x10, "f28",   "H",    0x3B4),
    (0x20, "look2", "B",    0x3B6),
    (0x40, "look3", "B",    0x3B7),
    (0x80, "look4", "B",    0x3B8),
    # VERIFIED: THE HIGH BITS, PINNED 2026-08-25 -- they were left out because two
    # reader widths were unknown, and a wrong width does not draw a wrong
    # number, it desynchronises the whole record mid-decode. Both are
    # measured now, off each reader's own advance of [0x5338360]:
    #   0x5045e90 does `inc edx`  -> ONE byte   (bit 0x100 -> +0x94)
    #   0x5045e60 does `add edx,4` -> u32       (bit 0x200 -> +0x3bc)
    (0x100, "army",  "B",    0x94),
    (0x200, "force", "I",    0x3BC),
    # VERIFIED: THE PROFILE COMMENT, 2026-08-25. Bit 0x8000 reads a cstr into
    # [unit+0x3d8] (0x05079f1d -> 0x5045f50). Two things had to be checked
    # before serving a string into it, and both came back fine:
    #   SIZE  -- the unit ctor zeroes 0x20 dwords + 1 byte there
    #            (0x05079c2a `mov ecx,0x20` / 0x05079c2f / rep stosd), so
    #            the buffer is 129 bytes. The reader 0x5045f50 is UNBOUNDED
    #            (it copies to the NUL), so the length is ours to control;
    #            the client truncates its own comments to 23.
    #   USED  -- the UI copies it out at 0x050e444f, from [unit+0x3d8] into
    #            a display struct at [ebx+0x96], right after a name at
    #            [ebx+0x64]. It is a string the client DISPLAYS.
    # WARNING: The store key is profile_comment, NOT comment: "comment" is
    # felobby record offset 0xE4, the SUSPENSION notice.
    (0x8000, "profile_comment", "cstr", 0x3D8),
]
#: [unit+0x3b4] of an untransformed player: RACE bit 0x200 'HUM' (0x0507A500's
#: names), what the self ctor stamps at 0x0507D6E0 (`or byte [+0x3b5], 2`)
SELF_FORM_HUM = 0x200


def draw_self_push(conn, outbound, mode, be, args):
    """`0x1170` -- THE CAMERA / DRAW-SELF SWITCH, a server PUSH we have never sent.

    2026-08-25, found from the symptom "I see the shadow where the character
    should be but no character model" -- which is a RENDER fact, not a dress
    fact, and the dress side is already settled: the client's own log says
    `MSG_ADD> Avatar( Lex ) uniqueID=1`, so the record parses, the models load
    (`m_face03_1` / `m_hair03_04_00` / `m_body`) and unit+0x84 slot 0 is set.

    THE FLAG. `[0x52ffad0] + 0x1d` is a global the client calls **Draw self**
    outright: `/stealth` (`0x05135400`) toggles that one byte and prints
    `Draw self: %s` with "OFF" when it is non-zero (`0x0513541f..0x0513542d`).
    Its only non-command reader is `0x0507e7f0`, inside the player-unit class's
    vtable slot +0x14 (`0x0507e7d0`), which `je`s straight past the whole block
    -- animation advance (`0x506af90` on unit+0x238/+0x80), `0x506a5d0`,
    `0x506c090`, `0x506de20` -- when the byte reads zero.

    THREE SERVER MESSAGES WRITE IT, and feworld sends none of them:

        0x1170  [u16]  arm 0x0505639e -- the pair switch:
                   != 0 -> +0x1d = 0, +0x18 = 2, +0x1c = 0, +0x1e = 1,
                           camera mode 3, [player+0x3d4] |= 1
                   == 0 -> +0x1d = 1, +0x18 = 1, +0x1c = 1, +0x1e = 0,
                           camera mode 1, [player+0x3d4] &= ~1
        0x1181  (no body) arm 0x050564a3 -- +0x1d = 0
        0x1182  (no body) arm 0x050564b3 -- +0x1d = 1

    The non-zero branch of 0x1170 is byte-for-byte the same state `/freecam`'s
    "on" arm installs (`0x0513559f`), which is what names camera mode 3.

    WARNING: THE POLARITY IS NOT ESTABLISHED, AND THAT IS WHY THIS DEFAULTS TO "off".
    The `/stealth` label says non-zero = "Draw self: OFF"; the only reader says
    non-zero = "run the self-animation block". Those two readings disagree, and
    guessing which is right is exactly the an earlier note
    failure -- either value renders *a* screen. `--draw-self` therefore takes
    the literal wire value and sends nothing at all unless asked:

        --draw-self off   send nothing (default -- today's behaviour)
        --draw-self 1     0x1170 with u16 = 1   (+0x1d = 0, camera mode 3)
        --draw-self 0     0x1170 with u16 = 0   (+0x1d = 1, camera mode 1)
        --draw-self on    0x1181, the header-only "+0x1d = 0"
        --draw-self hide  0x1182, the header-only "+0x1d = 1"

    THE CHEAPER TEST FIRST: `/stealth` typed into FE's own chat toggles the same
    byte with no deploy at all. If the character appears, this knob is the fix
    and the test also names the polarity. Do that before shipping anything here.
    """
    v = args.draw_self
    if v == "off":
        return
    if v in ("on", "hide"):
        mid = 0x1181 if v == "on" else 0x1182
        body = b""
    else:
        mid, body = 0x1170, struct.pack(">H", int(v, 0) & 0xFFFF)
    wire.send(conn, outbound, wire.inner_msg(mid, body, wire.unit_id_of(args)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0x%04X draw-self/camera switch (--draw-self "
          "%s). CHECK THE SCREEN, not the log: this message registers no reply "
          "and its arm logs nothing, so silence proves nothing either way."
          % (mid, v), flush=True)


# KEY: THE STAT BLOCK -- mask1 BIT 1 of the same 0x1006 record, applied by
# 0x0507a990. Ascending bit order, one i16 each, exactly like _AVATAR_SUB.
#
# CONFIRMED ON THE LIVE CLIENT 2026-08-25 (`felive --stats`, elevated, in a
# field): the player unit [0x533aab4] read name 'Lex' at +0x389 -- so the object
# and base are right -- and the four slots next to skill points held
#
#     +0x49A = 200   +0x49E = 200   +0x4A0 = 100   +0x4A4 = 100
#
# against a Status screen reading HP 200/200, Pw 100/100. The offsets are not
# inferred from a name; the neighbours match the screen and +0x498 is the exact
# i16 the window draws (0x050e13fc `movsx edx, word ptr [esi+0x498]`).
#
# WARNING: Only bit 0 is tied to a LABEL. The HP/Pw reading above makes bits 1-4 a
# strong inference, not a measurement -- nothing has served them yet. Which is
# why they are not in the default: a mask bit is a PROMISE about how many i16
# follow, so a bit set without a value does not draw a wrong number, it
# desynchronises the record.
#
# KEY: 2026-08-26 -- THE LADDER WAS READ TO ITS END, and two of its bits are the
# reason DOUBLE-CLICKING A SKILL DOES NOTHING. The equip validator
# (0x05076090, the single caller is the skill row's own handler at 0x050b6638,
# which refuses on any non-zero return) has SEVEN outcomes, and two stat gates
# sit AHEAD of the class-level one everybody was looking at:
#
#     if [rec+0x82] > [unit+0x4ac] + [unit+0x4ae]   -> 1
#     if [rec+0x84] > [unit+0x4b0] + [unit+0x4b2]   -> 2
#     if !class_table([unit+0x3ab])                 -> 7
#     if a prereq slot is set and neither is owned  -> 5
#     if [unit+0x9f0+class] < [rec+0x86]            -> 4
#     (and 6 earlier, if the skill has no asset string for your SEX:
#      [rec+0xc8] for sex 0, [rec+0x148] for sex 1)
#
# +0x4ac is the client's own 攻撃力 -- the Status screen sums it with +0x514
# and +0x516 at 0x050e1500 -- so gate 1 is an ATTACK requirement and gate 2 its
# sibling. Both read ZERO on our served character (measured 08-25), so every
# skill whose requirement is above zero was refused before the class level was
# ever consulted, and --class-levels could not have fixed it alone.
#
# The gates are SUMS and this applier writes the first term of each (+0x4ac,
# +0x4b0); +0x4ae and +0x4b2 are not in the ladder at all, which does not
# matter -- one term is enough to clear a sum.
#
# WARNING: THE TABLE STOPS AT BIT 16 ON PURPOSE. Bit 0x20000 and up read through
# 0x5045f20, which advances FOUR bytes (f32), not two. Listing one of those
# here would put an i16 on the wire where the client reads a float and
# desynchronise everything after it.
# KEY: 2026-08-27 (unit-attrs ladder sweep): the ladder reads
# with THREE different primitives, so every row now carries its struct code --
# bits 0..16 are i16 (0x5045ec0, `add edx,2`), 17/18/19/22/23 are f32
# (0x5045f20, `add edx,4`), 20/21 are u32 (0x5045e60, `add edx,4`). And the
# HP/Pw NAMES below were swapped against the Status window's own printf: at
# 0x050e1303 it pushes [+0x49e] FIRST and ([+0x49a]+[+0x49c]) SECOND into
# '%d/%d', so +0x49e is CURRENT and +0x49a (+ bonus +0x49c) is MAX; 0x2024
# maskA bit 0x4 (0x04ff3be0) is the damage event and it SUBTRACTS from +0x49e,
# which is only consistent with +0x49e being the current value. Same shape
# for Pw at 0x050e1344: +0x4a4 current, +0x4a0 + +0x4a2 max. (felive --avatar
# prints them with the old, swapped labels -- read it knowing that.)
_AVATAR_STATS = [
    (0, 0x498, "skill_points", "h"),   # THE one pinned to a label
    (1, 0x49A, "hp_max_base", "h"),    # drawn as the SECOND %d (max) + [+0x49c]
    (2, 0x49E, "hp_cur", "h"),         # drawn FIRST; 0x2024 bit 0x4 subtracts from it
    (3, 0x4A0, "pw_max_base", "h"),
    (4, 0x4A4, "pw_cur", "h"),
    (5, 0x4A6, "?", "h"),
    (6, 0x4AA, "?", "h"),
    (7, 0x4AC, "attack (equip gate 1)", "h"),   # 攻撃力, and [rec+0x82] is tested
    (8, 0x4B0, "equip gate 2", "h"),            #          against [rec+0x84]
    (9, 0x4B4, "?", "h"),
    (10, 0x4B8, "knockback threshold (+0x4ba pair)", "h"),
    (11, 0x4BC, "knockback value (0x2024 bit 0x800 is the EVENT form)", "h"),
    (12, 0x4BE, "?", "h"),
    (13, 0x4C2, "?", "h"),
    (14, 0x4C6, "?", "h"),
    (15, 0x4CA, "?", "h"),
    (16, 0x4CE, "?", "h"),
    # ---- the wide tail, pinned 2026-08-27 off each reader's own advance.
    # WARNING: bit 17 is the WALK SPEED that --unit-speed already serves through
    # 0x2024; listing it here is for completeness of the map, not for use --
    # two writers of one field drift.
    (17, 0x4D4, "walk speed (--unit-speed owns it via 0x2024)", "f"),
    # SmartJump physics, from the local jump handler 0x05152670:
    #   vertical launch = ([+0x4e0] + [+0x4dc]) * row[0xc] (4.7)   0x0504b640
    #   gravity         = ([+0x4e8] + [+0x4e4]) * 24.5            0x0504b680
    # both read ZERO on our character (felive 08-25), which is exactly a flat,
    # gravity-free dash with no arc. See --jump-phys.
    (18, 0x4DC, "jump vertical launch (x4.7)", "f"),
    (19, 0x4E4, "jump gravity (x24.5)", "f"),
    # +0x2b4 is the unit FLAG WORD (stealth 0x2000, 0x800000/0x1000000 tested
    # by the SmartJump and the move gates). The 0x1006 applier stores it RAW;
    # 0x2024 bit 0x100000 routes the same value through 0x0506caa0, which
    # applies the side effects (stealth log, model tint). NEVER probe with a
    # sentinel: every bit means something.
    (20, 0x2B4, "unit flags (raw)", "I"),
    (21, 0x4EC, "GOLD (also 0x2024 bit 0x200000, --gold)", "I"),
    (22, 0x504, "f32 ? (a divisor at 0x04fd7bcd; other classes ctor it to 999/100)", "f"),
    (23, 0x50C, "f32 ?", "f"),
    # WARNING: THE LIST ENDS AT BIT 23 AND SO DOES THE READER. 0x0507a990 tests
    # bit 0, 1, ... 0x800000 (bit 23 -> +0x50C), then reads ONE trailing byte
    # and returns: a row past 23 puts bytes into the record that nothing
    # consumes, which desynchronises everything after the stat block. Bit 29
    # (the BAG SIZE) was added here on 2026-09-05 and had to come straight
    # back out; it belongs to 0x2024's mask, which has the wider applier --
    # see bag_size_push.
]


def add_entities(conn, outbound, mode, be, args):
    """0x1006 MSG_ADD -- the client's OWN path for building the player's model.

    WHY. The 2026-08-24 crash was a null deref of slot 0 of the player unit's
    4-slot array at unit+0x84 (0x0515BD45, `cmp word [edi+0x2a4],8`, no null
    check), and it fires ON MOVEMENT -- pressing W in live testing made the client
    die at the same image offset +0x1CBD45 twice, with EDI=0 both times. The
    same sessions' client logs contain ZERO `MSG_ADD> Avatar(` lines: no avatar
    was ever added, so the unit has no model, and the first thing that needs one
    kills it. Same shape as FMO's undressed wanzer -- and the same rule: make
    the client's OWN flow dress it (match-the-original-not-a-stopgap).

    THE RECORD (0x1006 arm 0x05039ff9 -> per-entity 0x0503aae0 -> type-0 arm
    0x0503ab4b). Reader widths are pinned by each reader's own advance of the
    stream position counter [0x5338360]: 0x5045dc0 +1, 0x5045e30/0x5045ec0 +2,
    0x5045e60 +4, 0x5045f20 +4, 0x5045f90 = a NUL-TERMINATED string (0x522f5e0
    copies bytes until the NUL and consumes it -- no length prefix).

        [u16 count]
        count x {
          [u32 objectId]
          [u8  type]                          0 = Avatar
          [u32 mask1]
             bit0 -> [u32 submask] + the sub-record below   (0x5079d60)
             bit1 -> [u32][u32]                             (0x507a990)
          [u32 mask2]
             bit0 -> [f32 x3] -> unit+0x1c4  POSITION
             bit2 -> [f32 x3] -> unit+0x1e4  FACING
          [u32 mask3]  THE WORN-GEAR ARRAY, 13 slots (`cmp ebx,0xd` at
                       0x0503ae9c -- [unit+0x870])
             per set bit i: [u32 uid][u32 mask1][group-1 fields]
        }

    RETRACTED 2026-09-05: mask3 was documented here as "parts, per set bit
    [u32 partId][u32 param]", read off the pair of u32 reads at 0x0503adde /
    0x0503adea without following either. They are a uid and a FIELD MASK: the
    arm looks the uid up in category 0xbb8 (0x0503adf6), MALLOCS 0x4fc and
    constructs an item on a miss (0x0503ae12), feeds the mask to the item's
    group-1 reader 0x5075980 (0x0503ae7a), inserts it into the unit
    (0x0507b150) and then calls unit->Equip(uid, -1, 1) at 0x0503ae96. So this
    is the bulk form of the same equip 0x107A does one item at a time, and
    "serving parts with no sub-record would have dressed nothing" was reasoning
    about a mechanism that does not exist. --add-parts still packs the old
    shape, so it is a knob that writes a WRONG record; leave it empty.

    WARNING: A CORRECTION TO THIS FILE'S OWN EARLIER COMMENT. The first version of
    this decode called the record "closed" because every reader call site in the
    type-0 arm was accounted for. That was wrong: 0x5079d60 and 0x507a990 are
    handed the STREAM and read further fields from it, so counting only the
    arm's direct calls proved less than it appeared to. The sub-record above is
    the part that was missing -- and it is the part that matters, because it
    carries the name and the appearance.

    WHY THE SUB-RECORD IS THE FIX. The model path is built at 0x0507a04f from
    `Model\\%s\\%sface%02d_%d.mdl` (0x052deb44), and the directory/prefix pair is
    indexed by `mov al, byte ptr [esi + 0x3aa]` -- the SEX byte, which arrives
    through mask1 bit0's sub-record. mask3 is the worn gear (see above), which
    is layered on top of that model, not a substitute for it.

    WARNING: STILL UNMEASURED: that this fills unit+0x84 slot 0, and therefore that it
    stops the crash. The chain -- appearance -> +0x3aa -> model path -> a built
    model -> a non-null slot -- is read off the disassembly and is NOT yet
    confirmed live. OFF by default. The self-check that comes BEFORE any claim:
    the client logs `MSG_ADD> Avatar( <name> ) uniqueID=%d` (0x052d4744) at the
    END of the type-0 arm, so that line in the LOCAL per-pid shim log
    (polshim.<pid>.log next to the shim DLL -- NOT the shipped shim-<host>.log)
    means the record parsed all the way through. No line = it was rejected, and
    nothing about the crash or the screen means anything yet.
    """
    ch = _self_char(args)
    if ch is None:
        print("[feworld]    --add-self on, but NO stored character matched this "
              "session (account=%r charid=%r) -- sending nothing. Serving an "
              "avatar with invented appearance would only prove that invented "
              "appearance does not build a model."
              % (sess._SESSION.get("account"), sess._SESSION.get("charid")), flush=True)
        return

    sub = args.add_submask & 0xFFFFFFFF
    # WARNING: mask1 BIT 1 IS DERIVED FROM THE VALUES, NEVER SET BY HAND. The mask
    # promises how many i16 follow it, so a bit with no value behind it is not a
    # wrong number on screen -- it is a desynchronised record and every field
    # after it decodes as garbage. Making the code the only writer of this bit
    # means the two cannot disagree.
    # Values are kept as parsed: the f32 rows (bits 17+) take a float, and an
    # int() here used to truncate `18:1.5` to 1 before the pack below turned it
    # back into 1.0. The width is applied at pack time, per row.
    stats = {b: v for b, v in (args.add_stats or {}).items()
             if any(b == bit for bit, _o, _n, _c in _AVATAR_STATS)}
    # WARNING: BIT 0 IS A BUDGET, NOT A BALANCE -- and getting that wrong MINTS.
    #
    # Found 2026-08-25, minutes after skill points first worked: the test player
    # learned five skills with the five points we served, and the very next
    # field entry would have served a flat 5 again. Relaunch, spend five more,
    # forever. That is an earlier note's shape exactly -- a constant
    # served as a balance, materialised at every touch, and the TM version of
    # it minted 10000 gold per member before anyone noticed.
    #
    # So the configured value is what the character STARTED with, and what goes
    # on the wire is what is LEFT: budget minus the skills actually stored. The
    # store is the ledger; the wire is a view of it.
    #
    # WARNING: This is a stand-in, not FE's economy. Real FE grants skill points by
    # levelling and we have no level system, so "a fixed budget" is the honest
    # placeholder -- but an honest placeholder still must not print money.
    #
    # 2026-09-11: and now the budget is EARNED. With --sp-per-level (default
    # on) the value is what the character's level granted minus what its
    # stored skills cost at their own [skill+0xF0] -- skill_points(); the N in
    # `--add-stats 0:N` only matters with --sp-per-level off.
    if 0 in stats:
        spent = len(skilllist.acquired_skills(args))
        budget = int(stats[0])
        stats[0] = progression.skill_points(args, budget)
        if progression.sp_schedule(args) is not None:
            print("[feworld]    skill points: Lv%d earned %d, stored skills "
                  "cost %d -> %d served (--sp-per-level; --add-stats 0:%d is "
                  "only the bit)"
                  % (progression.self_level(args), progression.sp_earned(progression.sp_schedule(args),
                                                 progression.self_level(args)),
                     progression.sp_spent(args), stats[0], budget), flush=True)
        elif spent:
            print("[feworld]    skill points: budget %d - %d already learned = "
                  "%d served (the STORE is the ledger; serving the budget flat "
                  "would mint %d more every field entry)"
                  % (budget, spent, stats[0], budget), flush=True)
    statmask = 0
    for bit in stats:
        statmask |= 1 << bit
    mask1 = (args.add_mask1 & 0xFFFFFFFD) | (0x2 if stats else 0)
    rec = struct.pack(">I", mask1)
    if mask1 & 0x1:
        rec += struct.pack(">I", sub)
        # ASCENDING BIT ORDER -- 0x5079d60 tests bit 0, then 1, then 2 ... and
        # reads as it goes, so the fields must be laid down in that order.
        for bit, key, code, _off in _AVATAR_SUB:
            if not (sub & bit):
                continue
            if code == "cstr":
                nm = str(ch.get(key, "") or "").encode("cp932", "replace")
                rec += nm[:32] + b"\x00"
            else:
                v = int(ch.get(key, 0) or 0)
                if key == "f28" and not v:
                    # WARNING: 2026-09-11: THE RACE WORD OF A HUMAN IS HUM 0x200, NOT
                    # 0. The self unit's ctor stamps it (0x0507D6E0) and this
                    # record lands IN PLACE on that unit (0x5079dd6 stores the
                    # u16 straight into +0x3b4), so the stored 0 CLEARED it --
                    # and the target menu reads HUM to offer 'Summ' (0x050e3247)
                    # or 'Unsummon'. Without it no summon window ever opened;
                    # every press sent 0x2045 instead. feunit section 3.
                    v = SELF_FORM_HUM
                rec += struct.pack(">" + code, v
                                   & {"B": 0xFF, "H": 0xFFFF,
                                      "I": 0xFFFFFFFF}[code])

    # mask1 BIT 1 -- the stat block, and THE REASON SKILL POINTS READS 0.
    #
    # KEY: THIS RIDES THE MESSAGE THAT ALREADY WORKS. 0x1006 and 0x1003 share the
    # same per-entity decoder (0x0503aae0) and the same type-0 arm, so both can
    # carry stats -- but 0x1003 sent on its own is a character CREATE/REPLACE
    # that releases the models first, and serving it with stats and no identity
    # CRASHED THE CLIENT on field load (2026-08-25: `Chara 1 already exists` ->
    # `m_body.mdl::Release` -> AV at FE_Client.dll+0xEB70E). Here the identity
    # sub-record is already in the same record, so the rebuild has what it
    # needs -- which is exactly the missing half that crashed it.
    if stats:
        rec += struct.pack(">II", statmask, 0)
        # ASCENDING BIT ORDER, same rule as the sub-record: 0x0507a990 tests
        # bit 0, then 1, then 2 ... and reads as it goes.
        for bit, _off, _name, code in _AVATAR_STATS:
            if statmask & (1 << bit):
                # the WIDTH is the reader's, not the value's: an i16 where the
                # client reads a f32 desynchronises everything after it.
                v = stats[bit]
                rec += struct.pack(">" + code,
                                   float(v) if code == "f" else int(v))

    m2 = args.add_mask2 & 0xFFFFFFFF
    # WARNING: mask2 bit 2 is the FACING at unit+0x1e4 and it must not be all
    # zero -- see the --spawn-dir help and the startup check in main().
    rec += struct.pack(">I", m2)
    if m2 & 0x1:
        rec += struct.pack(">fff", *spawns.spawn_for(args))
    if m2 & 0x4:
        rec += struct.pack(">fff", *arrival.arrival_dir(args))

    # mask3 -- THE WORN-GEAR ARRAY, from the store. WARNING: LIVE 2026-09-08: this
    # record REPLACES the unit the client built from the lobby record ("Chara 1
    # already exists" in the client's own log, and the shim's Equip hook saw
    # a new unit address afterwards), and it was served with mask3 = 0 -- so
    # the field unit was undressed however well the select screen dressed it,
    # the quick-use pockets (worn 11/12) were empty on every login, and the
    # decoder tail that lights USESKILLRDY found no weapon. The worn array is
    # the ONLY dressing the rebuilt unit gets, so it rides here, per worn index
    # bit: [u32 uid][u32 mask1=2][u16 item number] -- the same group-1 shape
    # the shop page and the 0x1000 worn-gear record already use (reader
    # 0x5075980, then unit->Equip(uid,-1,1) at 0x0503ae96).
    if getattr(args, "add_parts", ""):
        print("[feworld]    --add-parts is IGNORED: mask3 is the worn-gear array "
              "(uid + item number per worn index), not parts, and it is served "
              "from the stored equipment", flush=True)
    # WARNING: RETRACTION 2026-09-08 (run 6): the type-0 arm's lookup HIT at
    # 0x0503ab68 -> 0x0503ac46 logs "Chara %d already exists" and then applies
    # mask1 (identity 0x5079d60, stats 0x507a990), mask2 and mask3 onto the
    # EXISTING unit -- it is an in-place UPDATE, not a rebuild. The "new unit
    # address" that sold the replace story was the character-select unit
    # (built by 0x04fe77e3 at the select screen) versus the field's own bare
    # 'Chara 1' (built at "char info move ok") -- two objects all along. What
    # WAS real: the worn-gear tail (0x0503aecb) sets STANCE with no action to
    # end it when this record carries a weapon, and that froze runs 2 and 3.
    # So mask3 stays EMPTY by default; the field's dressing is the 0x1000 bag
    # markers plus the 0x107A replay after the first clock sample, which is
    # also what registers the worn items in the live item manager.
    if getattr(args, "worn_gear_record", "off") == "on":
        worn_rows = equipment.stored_equip_rows(args)
        m3, worn_body, worn_desc = equipment.worn_gear_block(worn_rows)
    else:
        m3, worn_body, worn_desc = 0, b"", ""
    rec += struct.pack(">I", m3) + worn_body

    obj = sess._SESSION.get("charid", 0) or 0
    body = struct.pack(">H", 1) + struct.pack(">I", obj) + \
        struct.pack(">B", args.add_type & 0xFF) + rec
    wire.send(conn, outbound, wire.inner_msg(0x1006, body, wire.unit_id_of(args)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    if stats:
        print("[feworld]    0x1006 carries the STAT BLOCK: mask=0x%X %s"
              % (statmask, ", ".join(
                  "bit%d(+0x%03X %s)=%s" % (b, o, n, stats[b])
                  for b, o, n, _c in _AVATAR_STATS if b in stats)), flush=True)
    print("[feworld] -> 0x30 inner 0x1006 MSG_ADD obj=%d type=%d mask1=0x%X "
          "submask=0x%X mask2=0x%X worn(mask3=0x%04X)=%s name=%r sex=%s look=%s -- the "
          "client's own DRESS path. CHECK THE CLIENT LOG for 'MSG_ADD> Avatar( "
          "%s )' before reading anything into the screen."
          % (obj, args.add_type, mask1, sub, m2, m3, worn_desc or "none",
             ch.get("name"), ch.get("sex"),
             [ch.get(k) for k in ("look1", "look2", "look3", "look4")],
             ch.get("name")), flush=True)


def npc_push(conn, outbound, mode, be, args):
    """A SECOND AVATAR in the field -- and the client makes it kind 2 for us.

    2026-08-25, straight off the day's findings, with nothing new reversed:

      * `0x1006` MSG_ADD's type-0 arm (0x0503ab4b) allocates 0xa08 and runs the
        avatar ctor 0x050679c0, and that ctor does
        **`0x05067a32  mov dword [ebx+0x24], 2`** -- it stamps KIND 2.
      * kind 2 is exactly what `0x2023` action 0 demands (0x04fea923
        `cmp eax,2 / je`), the arm that writes the speed pair and drives the
        movement controller.

    So a served avatar is, by construction, the one thing the movement message
    WILL act on -- the mirror image of the player, who is kind 1 and whom that
    message ignores. Everything needed to spawn and move another character was
    already on the wire; it just had to be pointed at a different object.

    THE RECORD is the same one --add-self sends, with three differences:
      * a DIFFERENT object id (--npc-base upward), because the id is what the
        0x1006 arm looks up and what 0x2023 targets;
      * a name of our choosing;
      * the appearance copied from the stored character, so the model is one
        we have already watched the client build (m_face03_1 etc.) rather than
        an invented look that might not resolve to a file.

    \u26a0 The FACING is 0,0,1 for the same reason --spawn-dir is: an all-zero
    facing collapses the look-at basis and the avatar renders as nothing but
    its shadow. That cost most of a session; do not pass zeros here.

    \u26a0 NOT MOVED YET. This spawns and dresses. Driving it needs a 0x2023
    action-0 record aimed at the SAME id -- the inner header's leading u32 is
    the target, which is why unit_id is passed explicitly here rather than
    taken from --unit-id.
    """
    if not args.npc:
        return
    ch = _self_char(args) or {}
    base = args.npc_base
    for k, spec in enumerate(args.npc):
        f = spec.split(":")
        name = f[0] or ("NPC%d" % (base + k))
        try:
            x, y, z = (float(v) for v in f[1:4]) if len(f) >= 4 else (0.0, 0.0, 0.0)
        except ValueError:
            raise SystemExit("--npc wants NAME[:X:Y:Z], got %r" % spec)
        obj = base + k
        sub = 0xFF
        rec = struct.pack(">I", 0x1) + struct.pack(">I", sub)
        for bit, key, code, _off in _AVATAR_SUB:
            if not (sub & bit):
                continue
            if code == "cstr":
                rec += name.encode("cp932", "replace")[:32] + b"\x00"
            else:
                rec += struct.pack(">" + code, int(ch.get(key, 0) or 0)
                                   & {"B": 0xFF, "H": 0xFFFF,
                                      "I": 0xFFFFFFFF}[code])
        rec += struct.pack(">I", 0x5)                    # mask2: position+facing
        rec += struct.pack(">fff", x, y, z)
        rec += struct.pack(">fff", 0.0, 0.0, 1.0)        # NEVER all zero
        rec += struct.pack(">I", 0)                      # mask3: no parts
        body = struct.pack(">H", 1) + struct.pack(">I", obj) + \
            struct.pack(">B", 0) + rec
        wire.send(conn, outbound, wire.inner_msg(0x1006, body, obj), mode, be,
             args.seq_mode == "echo", args.world_prefix)
        print("[feworld]    -> 0x1006 NPC id=%d %r at (%g, %g, %g) -- the ctor "
              "0x050679c0 stamps [obj+0x24]=2, which is the kind 0x2023 action "
              "0 moves. CHECK THE CLIENT LOG for 'MSG_ADD> Avatar( %s )'."
              % (obj, name, x, y, z, name), flush=True)


# MEASURED 2026-08-27: `python tools/fedatagen/fenpc.py` over dat.pak -- 271
# NPC_ModelType rows, ids 0..47 and 52..274 (48..51 absent). 0x051ab3f0 misses
# on anything else and the miss is not a clean refusal (see monster_push).
MONSTER_MODELTYPE_IDS = frozenset(range(0, 48)) | frozenset(range(52, 275))


def monster_push(conn, outbound, mode, be, args):
    """0x1006 entity type **8** -- `CFeClientNPCObject`. A MONSTER.

    2026-08-26. `0x1006`'s per-entity dispatch (0x0503aae0) reads a type byte
    and jumps a NINE-entry table at 0x0503bb0c; we had only ever sent type 0,
    the avatar. Type 8 builds a different class entirely:

        0503af93  0x5045e30(&A)      u16   <- the MODELTYPE
        0503afad  0x5045e30(&B)      u16   <- selects the object KIND
        0503afb7  0x5045dc0(&C)      u8    -> [unit+0x3ac]
        0503afdf  malloc 0xa08
        0503b00c  ctor 0x5068f80(objid, A, B)
        0503b02e  [unit+0x3ac] = C

    and the ctor is where it stops being a guess:

        05068fa3  [esp+0x1c] -> 0x5077cb0   ; [unit+0x3c] = the object id
        05068fbb  cmp word [esp+0x24], 0    ; B
        05068fd6  == 0 -> [unit+0x24] = 0xBBC
        05068fdf  != 0 -> [unit+0x24] = 0xBBB
        05068ff2  [unit+0x9fa] = A          ; the MODELTYPE, u16
        0506920c  0x51ab3f0(A) -> [unit+0xa00]   ; the table row

    2026-08-27 (sweep, worker `monsters`) -- THE RECORD DOES NOT END AT C.
    Every path out of the type-8 construction block (`0x0503b0d5`,
    `0x0503b0e7`, `0x0503b0f5`) lands on `0x0503b111`, which is the SAME
    mask tail the avatar arm has:

        0503b118  0x5045e60 -> [esp+0x10]   u32 mask1
                    bit0 -> 0x5079d60(stream)  identity/appearance sub-record
                    bit1 -> 0x507a990(stream)  the stat block
        0503b175  0x5045e60 -> [esp+0x10]   u32 mask2
                    bit0 -> f32 x3 -> [unit+0x1c4] POSITION, then 0x5000450
                            ground snap -> [unit+0x1c8], 0x5078f00(8)
                    bit2 -> f32 x3 -> [unit+0x1e4] FACING
        0503b269  vtable+0x4c (NPC: 0x0506ab70, an empty ret); [unit+0x94]=2
        0503b27a  0x5045e60 -> [esp+0x10]   u32 mask3, 13 slots (cmp ebx,0xd
                            at 0x0503b346): per set bit [u32 partId][u32 param]
        0503b353  the 'already existed' byte skips; `cmp word [esp+0x48], 4`
                  -- B == 4 also allocates a 0x418 helper via 0x04fe0650

    The first version of this builder stopped after C. That was not "no
    mask": the reader 0x0522f9b0 returns 0 and LEAVES THE DESTINATION
    UNTOUCHED when the stream is exhausted (`jle` at 0x0522f9c9), and no
    instruction between the arm's entry and 0x0503b118 writes [esp+0x10] --
    so mask1 would have been whatever the previous 0x1006 call left in that
    stack slot (the player's own record leaves 0x3 there), and bit 0 would
    have run the CHARACTER appearance applier on a monster with a sub-record
    made of stale stack. That is the 0x1003 crash shape again. The record now
    carries all three masks explicitly: mask1 = 0, mask2 = 0x5 with the
    position and facing, mask3 = 0. The position therefore arrives IN the
    ADD -- no 0x2023 needed to make it stand somewhere.

    LOOKUP HIT (0x0503afd9 -> 0x0503b0f7): the client logs
    `Npc %d は既に存在しています` (0x052d4724), sets the 'existed' byte and
    APPLIES THE MASKS TO THE EXISTING OBJECT. It does not release or rebuild
    (unlike 0x1003's kind 0). The lookup is 0x0504d3a0(id, category 3) --
    category 3 is every unit, player included -- so an id that collides with
    the player or an --npc would have our masks applied to THAT object.
    Hence the id guard below.

    MODELTYPE MISS: ctor -> 0x05069200 -> 0x051ab3f0 miss -> logs `ERROR :
    CFeClientNPCObject::Initialize() get Modeltype type = %d` and returns
    with [unit+0xa00] = 0 and state [unit+0x38] = 1. Every later reader of
    [+0xa00] in the class is behind a state/null test EXCEPT the 0xBBC idle
    branch of state 1 (0x050695eb reads [esi+0xa00] under `cmp edx,0x46`
    with no null test) -- so an unknown modeltype is a probable null deref a
    few frames later, not a clean refusal. The id set above is MEASURED
    (fenpc.py over dat.pak: ids 0..47 and 52..274, 271 rows).

    MODEL FILE MISSING: 0x05069200 checks the .mdl and .tex with 0x04fceb40
    and on failure zeroes [+0x38..0x3b] and returns -- silent, state 0,
    retried every frame, no crash.

    WHAT IS STILL CHOSEN: C ("level"). On kind 0xBBC the client reads
    [unit+0x3ac] as the LEVEL for real -- the target window prints it with
    ` Lv=%3d` (0x052e2c18, at 0x050e466c) and 0x050dfee4 subtracts it from
    the player's level byte to pick a row -- so the NAME is now measured,
    the VALUE is still ours.
    """
    mobs = getattr(args, "monster", None)
    if not mobs:
        return
    player = wire.unit_id_of(args)
    npc_ids = set()
    if getattr(args, "npc", None):
        npc_ids = set(args.npc_base + k for k in range(len(args.npc)))
    seen = set()
    for k, spec in enumerate(mobs):
        f = spec.split(":")
        try:
            mtype = int(f[0], 0)
            x, y, z = (float(v) for v in f[1:4]) if len(f) >= 4 else (0.0, 0.0, 0.0)
            # OPTIONAL 5th field: this entity's KIND, overriding --monster-kind.
            # The type-8 ctor (0x05068fd4) stamps [unit+0x24] from it: 0 makes a
            # MONSTER (0xBBC -- level, hit detection), non-zero a TOWN NPC
            # (0xBBB -- talk range). It was a single global knob, so a run could
            # have monsters OR townsfolk, never both; a capital wants NPCs while
            # the war field wants monsters, and both are served on every field
            # entry from the same list.
            kind = int(f[4], 0) if len(f) >= 5 else args.monster_kind
        except (ValueError, IndexError):
            raise SystemExit("--monster wants TYPE[:X:Y:Z[:KIND]], got %r" % spec)
        if mtype not in MONSTER_MODELTYPE_IDS:
            raise SystemExit("--monster %r: modeltype %d is not in "
                             "dat\\data_NPC_ModelType.dat (ids 0..47, "
                             "52..274). The client would log 'get Modeltype "
                             "type = %d' and then read a null row at "
                             "0x050695eb." % (spec, mtype, mtype))
        obj = args.monster_base + k
        if obj == player or obj in npc_ids or obj in seen:
            raise SystemExit("--monster %r: object id %d collides with the "
                             "player (%d), an --npc (%s) or another monster. "
                             "0x1006 looks the id up in category 3 (every "
                             "unit) and on a HIT applies the record to THAT "
                             "object -- 'Npc %%d は既に存在しています'."
                             % (spec, obj, player, sorted(npc_ids)))
        seen.add(obj)
        rec = struct.pack(">H", mtype & 0xFFFF)
        rec += struct.pack(">H", kind & 0xFFFF)
        rec += struct.pack(">B", args.monster_level & 0xFF)
        rec += struct.pack(">I", 0)                      # mask1: no sub-record, no stats
        rec += struct.pack(">I", 0x5)                    # mask2: position + facing
        rec += struct.pack(">fff", x, y, z)              # -> [unit+0x1c4], ground-snapped
        rec += struct.pack(">fff", 0.0, 0.0, 1.0)        # -> [unit+0x1e4], NEVER all zero
        rec += struct.pack(">I", 0)                      # mask3: no parts
        body = (struct.pack(">H", 1) + struct.pack(">I", obj) +
                struct.pack(">B", 8) + rec)
        wire.send(conn, outbound, wire.inner_msg(0x1006, body, obj), mode, be,
             args.seq_mode == "echo", args.world_prefix)
        print("[feworld]    -> 0x1006 type 8 MONSTER id=%d modeltype=%d "
              "kind=0x%X level=%d at (%g, %g, %g) -- ctor 0x5068f80, masks "
              "0/0x5/0. CHECK THE CLIENT LOG: 'get Modeltype type = %d' means "
              "the table missed; 'Npc %d は既に存在しています' means the id "
              "was already an object."
              % (obj, mtype, 0xBBC if not kind else 0xBBB,
                 args.monster_level, x, y, z, mtype, obj), flush=True)


def monster_place_push(conn, outbound, mode, be, args, client_body):
    """Put each `--monster` at its coordinates with `0x2023` action 0.

    Same record and the same borrowed tick pair as `npc_walk_push` -- see its
    docstring for why the pair is lifted off the client's own heartbeat rather
    than invented, and why state is 0. The only difference is that this holds
    a position instead of advancing one: the question here is "does a monster
    render where it was put", not "does it walk".

    WARNING: This is the FIRST TIME that arm has been aimed at a 0xBBB/0xBBC object.
    Bob (kind 2) took the `je` at 0x04fea923 into the speed-writing block;
    these fall past both bails into the other path, which is UNREAD. If the
    monster spawns but never places, that path is where to look -- not here.
    """
    mobs = getattr(args, "monster", None)
    if not mobs or len(client_body) != movement._MV_LEN:
        return
    # 2026-08-27: OFF unless --monster-walk on. The ADD record now carries the
    # position itself (mask2 bit 0), so the first live test has ONE new
    # message on the wire, not two. Action 0 on a 0xBBB/0xBBC unit requires the
    # model to be loaded ([model+0x27c] != 0, 0x04fea956) -- that is the gate to
    # read off the ADD. WARNING: NOT the 0x800000 flag: 0x0504A4A0 hands a non-player
    # object a hard 0 (see monster_chase_tick), so that test never fires for a
    # monster; the "no ground under the unit" reading here was wrong.
    if getattr(args, "monster_walk", "off") != "on":
        return
    for k, spec in enumerate(mobs):
        f = spec.split(":")
        try:
            x, y, z = (float(v) for v in f[1:4]) if len(f) >= 4 else (0.0, 0.0, 0.0)
        except ValueError:
            return
        obj = args.monster_base + k
        out = bytearray(movement._MV_LEN)
        struct.pack_into(">I", out, movement._MV_ACTION, 0)
        out[movement._MV_A:movement._MV_A + 8] = client_body[movement._MV_A:movement._MV_A + 8]
        struct.pack_into(">H", out, movement._MV_STATE, 0)
        struct.pack_into(">hh", out, movement._MV_SPD, 0, 0)
        struct.pack_into(">hhh", out, movement._MV_POS,
                         movement._i16(x * 10.0), movement._i16(y * 10.0), movement._i16(z * 10.0))
        wire.send(conn, outbound, wire.inner_msg(0x2023, bytes(out), obj), mode, be,
             args.seq_mode == "echo", args.world_prefix)
    n = sess._SESSION.get("monster_place_n", 0) + 1
    sess._SESSION["monster_place_n"] = n
    if n <= 2 or n % 60 == 0:
        print("[feworld]    -> 0x2023 action 0 x%d MONSTER placement #%d -- "
              "kinds 0xBBB/0xBBC fall past both of that arm's bails."
              % (len(mobs), n), flush=True)
