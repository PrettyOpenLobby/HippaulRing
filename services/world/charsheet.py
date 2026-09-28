"""The character record (0x1003) and the status record (0x303E)."""
import struct
from . import entities, sess, skilllist, wire

def char_record_push(conn, outbound, mode, be, args):
    """0x1003 -- THE CHARACTER RECORD. This is what actually writes Skill Points.

    WARNING: 0x303E DOES NOT. Retracted 2026-08-25 after serving it live with nine
    sentinels and watching the Status screen come back all zeros: its arm decodes
    the record, prints it to the in-game message window and FREES it, storing
    nothing. See status_push() -- kept as a decode oracle, not a setter.

    THE CHAIN, every link measured, bottom up:

        the Status window draws Skill Points from
            0x050e13fc  movsx edx, word ptr [esi + 0x498]      i16
        the ONLY writer of +0x498 is the mask-gated applier
            0x0507a990  test bl,1 -> lea eax,[esi+0x498]       BIT 0
                        (17 field reads, ALL of them mask-gated -- verified, so
                         a mask with one bit set puts exactly one i16 on the
                         wire and nothing else)
        which is called by the character-record decoder
            0x0503acb3  call 0x0507a990(stream, mask, mask2)
        which is 0x1003 kind 0
            0x0503ab2d  u8 kind -> jump table 0x0503bb0c, kind 0 = 0x0503ab4b
        dispatched by 0x05039fa0 -- the FIFTH handler on the world pump, which
        was missing from our map until 2026-08-25. 0x100E MSG_ADD_COMPLETE lives
        in it too, and we have been serving that successfully for weeks, which
        is the proof the handler list was short and not the client.

    THE WIRE:

        [u8  kind]        0 = the full record (8 = an update form, unmapped)
        [u32 flags]       read at 0x0503ac62, then:
          flags & 1  ->   [u32 x]           0x0503ac75, fed to 0x05079d60
          flags & 2  ->   [u32 mask]        0x0503ac95
                          [u32 mask2]       0x0503aca1
                          then ONE i16 per set bit, in bit order:
                              bit 0 -> +0x498  SKILL POINTS
                              bit 1 -> +0x49a    bit 2 -> +0x49e
                              bit 3 -> +0x4a0    bit 4 -> +0x4a4
                              bit 5 -> +0x4a6    bit 6 -> +0x4aa
                              bit 7 -> +0x4ac    ...

    WARNING: THE TARGET IS THE FRAME'S LEADING u32. Kind 0 looks the object up with
    0x0504cb30(<that u32>, 3) and allocates a fresh 0xa08-byte one if it misses
    (0x0503ab4b..0x0503ab73). So --unit-id has to name the player's unit or this
    writes Skill Points onto a phantom nobody is looking at. It currently sends
    1, and the charid is 1.

    WARNING: AND ONLY BIT 0 IS MEASURED. The other sixteen offsets are known but which
    STAT each one is is not -- +0x498 is pinned only because the Status window
    reads that exact offset for that exact label. --char-mask is deliberately
    1, not 0xFFFF: a mask is a promise about how many i16 follow, so a bit set
    without a value behind it does not draw a wrong number, it DESYNCHRONISES
    the record mid-decode.
    """
    fields = args.char_fields
    mask = args.char_mask & 0xFFFFFFFF
    body = struct.pack(">BII", 0, 2, mask) + struct.pack(">I", 0)
    for i in range(32):
        if mask & (1 << i):
            # The same two bytes for -1 and 0xFFFF; the old `>h` of a masked
            # value raised struct.error on anything above 32767.
            body += struct.pack(">H", int(fields.get(i, 0)) & 0xFFFF)
    wire.send(conn, outbound, wire.inner_msg(0x1003, body, wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    named = {0: "SKILL POINTS (+0x498)"}
    print("[feworld] -> 0x30 inner 0x1003 CHARACTER RECORD kind=0 flags=2 "
          "mask=0x%X -> %d i16: %s  [target unit = the frame's leading u32 = %d]"
          % (mask, bin(mask).count("1"),
             ", ".join("bit%d=%d%s" % (i, fields.get(i, 0),
                                       " " + named[i] if i in named else "")
                       for i in range(32) if mask & (1 << i)),
             wire.unit_id_of(args)), flush=True)


def status_push(conn, outbound, mode, be, args):
    """0x303E -- THE CHARACTER STATUS RECORD, and it carries the SKILL LIST.

    WARNING: THE REASON SP IS 0, SKILL POINTS IS 0 AND EVERY `GET!` IS GREYED OUT:
    feworld has never sent this message. Not once -- 0x303E appears nowhere in
    this file before 2026-08-25.

    THE WIRE, read call by call off arm 0x05024a80 (c2), reader by reader:

        0x05024b08  u32     id
        0x05024b17  cstr    name
        0x05024b23  u8      sex
        0x05024b32  u16     class_type
        0x05024b41  u32  \
        0x05024b50  u32   |
        0x05024b5f  u32   |
        0x05024b6e  u32   |
        0x05024b7d  u32   >  NINE u32 -- see the warning below
        0x05024b89  u32   |
        0x05024b95  u32   |
        0x05024ba1  u32   |
        0x05024bad  u32  /
        0x05024bb9  u16     nation
        0x05024bc8  cstr    (a second string)
        0x05024bd4  u32     COUNT
        0x05024bf0  u32     skill id      -- x COUNT, loop 0x05024be5..0x05024c44,
                                             each id linked into a 0xc-byte list
                                             node at [node+8]

    The client's own debug dump at the end of that arm prints the decoded record
    field by field (format strings 0x052d3c64..0x052d3d0c) and then the list
    under `--------skilllist--------` (0x052d3c48), resolving each id to a skill
    NAME through 0x0505f4b0. The dumped scalars are exactly nine numbers --
    skill_point, life, power, stamina, down, gold, ring, fame, total_score --
    which is why the nine u32 above is a clean fit and not a coincidence.

    WARNING: WHICH OF THE NINE IS `skill_point` IS **NOT ESTABLISHED**. The dump's
    PRINT order is known; the WIRE order is not, and the two need not agree.
    Trying to pin it by matching the readers' `lea ecx,[esp+N]` destinations
    against the dump's loads did not converge -- the stack delta drifts through
    the sprintf sequence -- so it is left UNKNOWN rather than guessed.

    Guessing here is the [[served-zeros-launder-into-choices]] failure exactly:
    a wrong slot would put the player's skill points into `gold` or `fame`,
    every screen would still render, and it would read as "served, works".

    🔬 SO THE DEFAULT IS A PROBE, NOT A GUESS. --status-nums defaults to
    101..109, nine DISTINCT sentinels in wire order. Send it once and read the
    status screen: whichever field shows 10N names slot N, and one run labels
    all nine at once. Then set the real values and this becomes a server record.

    WARNING: AND THE TRIGGER IS UNMEASURED. 0x303E registers no reply anywhere in the
    image, so it is a server PUSH with no request behind it -- but WHEN the
    client expects it is not known. This fires it after field entry, next to the
    other post-entry pushes, because that is where the client is when the status
    screen can be opened. If the screen shows nothing, the message may simply
    have arrived with no screen to receive it, which is the same failure mode
    0x1018 had (measured 2026-08-19: one sent, zero received) -- resend rather
    than concluding the body is wrong.
    """
    nums = args.status_nums
    # The stored list is the real one; --status-skills overrides it for a probe.
    # Without this, a skill the player just learned through 0x2049 would come
    # back missing on the next login and the SP would have bought nothing.
    skills = args.status_skills or skilllist.acquired_skills(args)
    # THE IDENTITY FIELDS COME FROM THE STORED CHARACTER unless a flag pins
    # them. Until 2026-09-04 the defaults were id=1, name "Fox", class 1,
    # nation 1 -- one specific test character, hard-coded -- so any other
    # player who ever had --status on would have been told they were Fox.
    ch = entities._self_char(args) or {}
    sid = args.status_id
    if sid is None:
        sid = sess._SESSION.get("charid") or 0
    name = args.status_name
    if name is None:
        name = str(ch.get("name") or "")
    sex = args.status_sex
    if sex is None:
        sex = int(ch.get("sex") or 0)
    cls = args.status_class
    if cls is None:
        cls = int(ch.get("look1") or 0)      # [unit+0x3AB], the CLASS byte
    nation = args.status_nation
    if nation is None:
        force = ch.get("force")
        nation = int(force) if force not in (None, 0, 0x7FFFFFFF) else 0
    payload = (struct.pack(">I", sid & 0xFFFFFFFF)
               + name.encode("cp932", "replace") + bytes(1)
               + struct.pack(">BH", sex & 0xFF, cls & 0xFFFF)
               + b"".join(struct.pack(">I", n & 0xFFFFFFFF) for n in nums)
               + struct.pack(">H", nation & 0xFFFF)
               + args.status_str2.encode("cp932", "replace") + bytes(1)
               + struct.pack(">I", len(skills))
               + b"".join(struct.pack(">I", k & 0xFFFFFFFF) for k in skills))
    wire.send(conn, outbound, wire.inner_msg(0x303E, payload, wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0x303E CHARACTER STATUS: id=%d name=%r "
          "sex=%d class=%d nation=%d, nine u32 %s, %d skill(s) %s"
          % (sid, name, sex, cls, nation, list(nums), len(skills),
             list(skills)), flush=True)
    if list(nums) == list(range(101, 110)):
        print("[feworld]    🔬 PROBE VALUES (101..109 in WIRE order). Open the "
              "status screen and read which field shows which number -- that "
              "labels all nine slots in one run, including skill_point, which "
              "is the whole point. Then set --status-nums for real.",
              flush=True)
