"""Groups, capitals and rooms: the 0x3031 group record and which area id means what."""
import struct
from . import territory

#: Areas the client is told a door leads to. 0x1166's arm (0x05056089) reads
#: [u32 A][u32 B][f32 x][f32 y][f32 z]; A == the current field (with B == -1)
#: or B == the current field is a SAME-FIELD WARP to (x,y,z) via 0x4ff8020;
#: anything else stores A/B/xyz at 0x5345e88..0x5345e9c, sets the byte
#: 0x5345ea0 = 1 that makes the transition classifier (0x04ffbb1a) pick kind
#: 3 = 0x20A6 MSG_MOVE_REQUEST, and the client then asks 0x2000 for area A.
#: A room has no group id of its own, so A is encoded here as
#: ROOM_AREA_BASE + room index and decoded back in the 0x2000 handler, which
#: enters the island's WAR-FIELD gid with that room (a capital gid would take
#: the capital loader instead -- the id wins over the room index).
ROOM_AREA_BASE = 0x1000


# ---------------------------------------------------------------------------
# The 0x3031 GROUP record's optional fields, in parse order. Bit N of the mask
# gates entry N: 0x5008850 reads the id and the mask unconditionally, then does
# `test byte [mask], <bit> / je` before every one of these.
#
# The names come from the client's own FIELD log line (0x52d32e4), whose
# arguments are read straight off these offsets at 0x050093bb:
#
#   FIELD[%02d:ID%02d [%03d-%03d] "%16s"] Status[%d]
#        Char[D%d/%dvsA%d/%d(F%d)(ALL%d)]
#        [(ID:%02d)[%16s]vs[%16s](ID:%02d)] COLOR[%d]
#
# A group here is a FIELD -- a contested territory -- so "status", the two
# nation ids and the character counts are what the war map is drawn from. Fields
# with no log-line role are left positional rather than named.
GROUP_FIELDS = [
    ("u8",   "status",   0x04, "Status[%d]"),
    # KEY: 2026-09-13 (static RE of the group-fields readers):
    # g08, g10, g1a, g24 and g50 have NO READER in the client. The parser
    # (0x05008850) stores them into the 0xCC group object (+0x08, three 5 x u16
    # arrays at +0x10/+0x1a/+0x24, +0x50) and the map screen's snapshot copy
    # (0x05103a00) copies them; nothing else touches those offsets (a taint
    # scan from every group/island getter found every KNOWN reader and none
    # of these). Leftover server bookkeeping or a cut feature: send nothing,
    # prod's mask leaves all five clear. If ever set, each 5 x u16 bit MUST
    # carry exactly 10 bytes or every later record in the 0x3031 is garbled.
    ("u32",  "g08",      0x08, ""),
    ("u16",  "chars_all", 0x0c, "(ALL%d)"),
    ("u16",  "chars_f",  0x0e, "(F%d)"),
    ("u16",  "g10",      0x10, ""),
    ("u16",  "g1a",      0x1a, ""),
    ("u16",  "g24",      0x24, ""),
    ("cstr", "name",     0x2e, "the FIELD NAME"),
    ("u32",  "g50",      0x50, ""),
    ("cstr", "def_name", 0x54, "defending nation name"),
    ("u32",  "def_id",   0x78, "defending nation id"),
    ("u32",  "def_chars", 0x7c, "D%d"),
    # KEY: BIT 12 IS THE FIELD'S NEIGHBOUR LIST, and it is the whole of FE's
    # geography. Decoded 2026-09-09 at 0x0500899c:
    #
    #     test ah,0x10                       -- mask bit 12
    #     read u32           -> [grp+0x80]   -- COUNT
    #     free [grp+0x84]; new(count*4)      -- the array
    #     count x { read u32 -> [grp+0x84][i] }
    #
    # so the wire form is exactly [u32 count][u32 area id] * count.
    #
    # What reads it back is the only nation-based rule in the client. The
    # frontier test 0x05009870 answers "is this field mine, or NEXT TO
    # something of mine": [grp+0x78] == my nation, else the same test on every
    # id in this array. 0x050098f0 then permits a move only when the
    # destination is IN THE SOURCE'S array and both ends pass that test, and
    # the map paints a route line per neighbour -- the destination's colour
    # when permitted, opaque black when not (0x0510b183). The refusal the
    # player sees is 0x052e4cd0, "このフィールドは所属国と隣接していないので
    # 入ることができません" -- "this field is not adjacent to your country".
    #
    # WARNING: SO WHILE THIS WAS UNSERVED, NOBODY COULD EVER ENTER AN ENEMY FIELD.
    # An empty array collapses the frontier test to "my nation already holds
    # it", which is the one case that needs no adjacency at all. It read as a
    # design ("you cannot leave your country") and it was our omission.
    ("u32arr", "neighbours", 0x80, "the adjacency list -- [u32 n][u32 id]*n"),
    # WARNING: +0x88 IS THE ATTACKERS' CEILING, NOT THEIR COUNT (2026-09-12, read off
    # the FIELD log call at 0x050093bb..0x0500942b, args in push order): the
    # client prints `Char[D%d/%dvsA%d/%d(F%d)(ALL%d)]` as
    #     D <+0x7c> / <+0x8a>   vs   A <ALL - D, COMPUTED> / <+0x88>
    #     (F <+0x0e>) (ALL <+0x0c>)
    # so the A count is never on the wire -- it is chars_all minus def_chars --
    # and +0x88 is the "/%d" after it. The map's war panel agrees: it draws
    # "<+0xbc>/<+0x8a>" on the defender's line and "<+0xbe>/<+0x88>" on the
    # attacker's (0x0510b6cb / 0x0510b7be). This key was `atk_chars` and carried
    # the attackers' head-count into the attackers' MAXIMUM.
    ("u16",  "atk_max",   0x88, "the /%d after A -- the attackers' ceiling"),
    ("u16",  "def_max",   0x8a, "the /%d after D -- the defenders' ceiling"),
    ("u16",  "g8c",       0x8c, ""),
    # KEY: THE SIXTH NUMBER, named 2026-09-12 off the client's OWN log format
    # (0x052D32E4): `Char[D%d/%dvsA%d/%d(F%d)(ALL%d)]`. That is
    #     D <def_chars +0x7c> / <def_sub +0x8a>
    #  vs A <atk_chars +0x88> / <THIS FIELD>
    #     (F <chars_f +0x0e>) (ALL <chars_all +0x0c>)
    # so the `/%d` after A is the one slot in that line our table left unnamed.
    # WARNING: It is NOT +0x8c: that is the FLAGS word (--field-flags, whose bit 3
    # gates the home-field resolve -- the load-bearing field behind the
    # 2026-08-27 black screen). +0x8e is the free u16 next to atk_chars/def_sub,
    # and its position in the struct is the argument.
    # WARNING: RETRACTED the same day: the paragraph above reasoned from struct
    # position, and the log call (see atk_max) never reads +0x8e at all. Its
    # meaning is unknown; nothing sends it.
    ("u16",  "g8e",       0x8e, "UNKNOWN -- not in the FIELD log line"),
    ("2i16", "coords",    0x90, "[%03d-%03d] -- ONE bit gates BOTH"),
    # KEY: +0x94 IS THE Hmap MAP NUMBER -- the terrain a field is fought on, and
    # THE reason an outdoor field entry has no ground. On the outdoor arm of the
    # field-entry step (0x04ff9df6) the client does
    #     rec  = fieldinfo.find([scene+0x4ec]); edi = u16 [rec+0x94]
    #     rec2 = 0x5007e30(edi)          -- static table 0x52d30e0, stride 6
    #     0x4fd6f20(rec2, ...)           -- loads Data\Hmap\map%02d.pak
    #                                       + Data\Hmap\MapSpot%02d.cfg
    # Table 0x52d30e0's keys are 1..14 -- exactly the mapNN.pak shipped in
    # Data\Hmap -- plus dev maps 100..105 that are NOT in the install. Serving 0
    # (what "never set" gives) misses the table, 0x5007e30 returns NULL, and the
    # very next instruction reads [NULL+2] -> access violation. On the outdoor
    # path this field is MANDATORY, not optional. The terrain FLAVOUR (plain /
    # canyon / highland, table 0x52d3160 via 0x5007e80) is the client's own
    # per-map constant -- we do not choose it.
    ("u16",  "hmap",      0x94, r"the Data\Hmap\map%02d.pak number, 1..14"),
    ("i8",   "color",     0x96, "COLOR[%d]"),
    ("u32",  "atk_id",    0xb8, "ATTACKING nation id -- the (ID:-1) when unset"),
    ("cstr", "atk_name",  0x97, "attacking nation name"),
    # KEY: BITS 22 AND 23 -- the two sides' PLAYERS NOW, read off the parser
    # (0x05008b03 / 0x05008b1e: u16 -> +0xbc, u16 -> +0xbe). Their only
    # consumer is the map's WAR panel (field status 1/2, 0x0510b59e): a
    # two-segment gauge set from both (0x508a140 idx 0/1) and the lines
    # "%2d/%d" = <+0xbc>/<def_max> and <+0xbe>/<atk_max>. Never sent before
    # 2026-09-12, so a field at war showed an empty gauge and " 0/50".
    ("u16",  "def_now",   0xbc, "defenders present -- war panel, over def_max"),
    ("u16",  "atk_now",   0xbe, "attackers present -- war panel, over atk_max"),
]

_GW = {"u8": ">B", "i8": ">B", "u16": ">H", "u32": ">I"}


#: Bits whose CLIENT-SIDE width is not the single value GROUP_FIELDS implies.
#: Read off the parser at 0x05008850, 2026-09-04, while chasing a desync:
#:
#:   bit 4  (0x05008 8c7) lea edi,[esi+0x10]; mov ebx,5; {u16; edi+=2} x5
#:   bit 5  (0x05008 8e7) same shape at +0x1a
#:   bit 6  (0x05008 907) same shape at +0x24
#:   bit 12 (0x05008 99c) u32 at +0x80, then MALLOCs [+0x84] = count*4 and
#:                        reads that many u32s
#:
#: GROUP_FIELDS calls each of these ONE value. Emitting one where the client
#: reads five does not error -- it DESYNCHRONISES every record after it in the
#: same 0x3031, and a desync in the LAST record of a payload is INVISIBLE. That
#: is why this went unnoticed: every island has always carried exactly one
#: group. Refused rather than half-supported, because a guess here corrupts the
#: whole message.
#: VERIFIED: BIT 12 LEFT THIS SET 2026-09-09. It is a counted u32 array and the shape
#: is now read straight off the parser (see GROUP_FIELDS), so it is emitted
#: rather than refused. Bits 4/5/6 stay refused: each reads FIVE u16s and we
#: have never established what the five are.
GROUP_BITS_UNSUPPORTED = {4: "five u16 at +0x10", 5: "five u16 at +0x1a",
                          6: "five u16 at +0x24"}


def build_group_record(gid, vals):
    """[u32 id][u32 mask] + only the fields present in `vals`.

    The mask is BUILT from what is supplied, so an unset field is simply not on
    the wire -- which is what mask 0 was doing for every field at once.
    """
    for i, (_k, key, _o, _n) in enumerate(GROUP_FIELDS):
        if i in GROUP_BITS_UNSUPPORTED and key in vals:
            raise SystemExit(
                "group field %r (mask bit %d) cannot be emitted: the client "
                "reads %s there, GROUP_FIELDS models it as one value, and a "
                "short field desynchronises every record after it in the same "
                "0x3031. See GROUP_BITS_UNSUPPORTED."
                % (key, i, GROUP_BITS_UNSUPPORTED[i]))
    mask = 0
    body = b""
    for i, (kind, key, _off, _note) in enumerate(GROUP_FIELDS):
        if key not in vals:
            continue
        mask |= 1 << i
        v = vals[key]
        if kind == "cstr":
            body += v.encode("cp932", "replace") + bytes(1)
        elif kind == "2i16":
            # WARNING: ONE bit, TWO values: 0x05008a6b's test gates the reads at
            # 0x05008a81 AND 0x05008a92. Emitting a single i16 here would leave
            # the record two bytes short and desync everything after it.
            body += struct.pack(">hh", int(v[0]), int(v[1]))
        elif kind == "u32arr":
            # WARNING: ONE bit, 1 + n VALUES. The client reads the count, ALLOCATES
            # count * 4 and then reads exactly that many u32s (0x0500899c), so
            # the count on the wire and the number of ids after it must agree
            # or every record behind this one in the same 0x3031 is garbage.
            # Building both from the same list is the only reason they do.
            ids = [int(x) for x in v]
            body += struct.pack(">I", len(ids))
            body += b"".join(struct.pack(">I", x) for x in ids)
        else:
            body += struct.pack(_GW[kind], int(v))
    # KEY: THE MANDATORY 5-BYTE TAIL. After the LAST mask-gated field the parser
    # at 0x05008850 does two reads that NO BIT GATES:
    #
    #     05008b39  lea ecx,[esi+0xc0] ; call 0x5045e90   -- 1 byte (inc edx)
    #     05008b4a  lea edx,[esi+0xc4] ; call 0x5045e60   -- u32
    #
    # Five bytes, on EVERY record, whatever the mask. We never emitted them, so
    # the client has always read 5 bytes past the end of every group record.
    #
    # Why this hid for the life of the project: with ONE group per island the
    # over-read runs off the end of the last record in the message and nothing
    # downstream notices. With TWO it eats the next record's first 5 bytes, and
    # that record decodes as garbage -- measured 2026-09-04, the client's own
    # FIELD log reporting id 0x36868101, which is the tail of that record's own
    # mask plus its status byte. Every island, every time.
    #
    # WARNING: This is why --groups has effectively never worked, and it is what the
    # capital was blamed for through two crashes and a hang: the capital was
    # simply the first SECOND record this server ever sent.
    #
    # KEY: AND BOTH ARE DRAWN (2026-09-12). +0xC0 is the OTHER side's palette
    # index: the war panel (0x0510b65a) tints the attacker's circles with
    # `movsx byte [group+0xc0]` beside +0x97, exactly as it tints the holder's
    # with +0x96 beside +0x54. +0xC4 is the "Fields Held" row of the field
    # panel (0x0510b4d8), and the player's own panel copies it from the first
    # field its nation holds (0x0510533e) -- so it is the HOLDER'S territory
    # count, and our constant 0 is the "Fields Held: 0" on every screenshot.
    # The caller supplies them as `tail_color` / `tail_held` (not mask fields).
    body += struct.pack(">b", max(-128, min(127, int(vals.get("tail_color", 0)))))
    body += struct.pack(">I", int(vals.get("tail_held", 0)) & 0xFFFFFFFF)
    return struct.pack(">II", gid, mask) + body


#: THE GROUP IDS THE CLIENT TREATS AS A CAPITAL, read off its own jump table
#: rather than a doc: 0x04ff7ef0 does `id - 0x15`, bails if the result is above
#: 0x4a (so the window is 21..95), then indexes a 75-byte table at 0x04ff7f20
#: whose entry selects 0x04ff7f12 (`mov al,1`) or 0x04ff7f15 (`xor al,al`).
#: Decoded 2026-09-04; exactly ten ids come back true.
#:
#: Each maps 1:1 to a shipped map through the 36-byte records at 0x052d1858
#: (first u16 = the group id, NUL-terminated, linear search at 0x04fe5900 that
#: returns NULL on a miss -- all ten have rows, so no id here can null-deref):
#:      21 -> map00_00   91 -> map00_01     39 -> map01_00   92 -> map01_01
#:      57 -> map02_01   93 -> map02_00     62 -> map03_00   94 -> map03_01
#:      78 -> map04_00   95 -> map04_01
CAPITAL_GROUP_IDS = (21, 39, 57, 62, 78, 91, 92, 93, 94, 95)

#: The INNER half of each capital -- the town proper, reached only through a
#: door in the outer half. fet_area puts these on their nation's island but
#: NOTHING in the 95-area graph points at them: all 154 edges are reciprocal
#: except these five, which list neighbours and are listed by none. That is the
#: table agreeing with the 2026-09-06 live proof that a capital door, not the
#: world map, is how you get into one.
INNER_CAPITAL_GROUP_IDS = (91, 92, 93, 94, 95)

#: outer capital -> its inner half, from the client's table at 0x052D1858.
#: WARNING: THE ID IS THE FIRST DWORD OF AN ENTRY, NOT THE LAST. Reading it as the
#: last shifted every pairing by one row, shipped, and put a player in a
#: DIFFERENT city where their coordinates meant nothing -- they fell to
#: y=21128 and saw only skybox. A wrong id here is not an error message.
INNER_CAPITAL_PAIRS = {21: 91, 39: 92, 57: 93, 62: 94, 78: 95}

#: KEY: WHICH MAP EACH CAPITAL ID LOADS, read off the client's table at
#: 0x052D1858 -- ten entries, stride 0x24, each {GROUP ID, oct, _BG, _hit,
#: _mini, colorset, short name, f32, f32}.
#:
#: WARNING: THE ID IS THE FIRST DWORD OF AN ENTRY, NOT THE LAST, and reading it as
#: the last shifted every pairing by one row. That wrong table went live on
#: 2026-09-06 and sent the test player into group 91 expecting the other half of
#: their own capital; they got map00_01 -- the right file for 91, but 91 is
#: capital 21's partner, a different city -- and fell through empty space at
#: y=21128 because their position meant nothing there.
#:
#: KEY: THE CLIENT'S OWN LOG IS WHAT CAUGHT IT, and it had been sitting in
#: polshim.<pid>.log the whole time: `[DATA\capital\map01_00.oct] Stand-By ok.`
#: on entering 39, and `map00_01.oct` on entering 91. Two lines, no ambiguity.
#: The disassembly was read three times and never disagreed with itself; only
#: the running client did.
#:
#: The pairing is regular once aligned: each of the five capitals the map
#: screen offers (21, 39, 57, 62, 78) has a 9x partner holding the OTHER half
#: of the same town.
CAPITAL_MAP = {
    21: "DATA/capital/map00_00.oct", 91: "DATA/capital/map00_01.oct",
    39: "DATA/capital/map01_00.oct", 92: "DATA/capital/map01_01.oct",
    57: "DATA/capital/map02_01.oct", 93: "DATA/capital/map02_00.oct",
    62: "DATA/capital/map03_00.oct", 94: "DATA/capital/map03_01.oct",
    78: "DATA/capital/map04_00.oct", 95: "DATA/capital/map04_01.oct",
}
#: The other half of each capital, for a door that crosses between them.
#: WARNING: 39's partner is 92, NOT 91. Getting this wrong is a fall into the void:
#: the client loads the partner's map happily and the player's coordinates
#: belong to a different town.
CAPITAL_OTHER_HALF = {21: 91, 91: 21, 39: 92, 92: 39, 57: 93, 93: 57,
                      62: 94, 94: 62, 78: 95, 95: 78}

#: VERIFIED: THESE WORK. Announcing one on the map screen, and ENTERING it,
#: are both verified live (2026-09-04, island 1 / gid 39): the client logs
#: `FIELD[01:ID39 "Capital"]`, the town loads, and it was walked end to end --
#: a long path with a castle at one end and vendor stalls at the other.
#: WARNING: That last clause used to say "i.e. DATA/capital/map01_00.oct" and it was
#: WRONG -- see CAPITAL_MAP above, read off the client's table: gid 39 is
#: map00_01. map01_* belongs to gids 92 and 57.
#:
#: WARNING: IT TOOK TWO CRASHES AND A HANG TO GET THERE AND THE CAPITAL WAS
#: INNOCENT OF ALL OF IT. Two root causes were announced for those failures and
#: BOTH were falsified ("dropping atk_id/atk_name corrupted the parse" -- the
#: record is a clean, well-formed subset; "the client indexes group storage by
#: id, so 39 overruns" -- 0x05009820 is a BOUNDED linear search). The real bug
#: was in build_group_record and had nothing to do with capitals: every 0x3031
#: group record was FIVE BYTES SHORT of the parser's ungated tail, so a second
#: record in the same message ate the first five bytes of its own body. The
#: capital was simply the first SECOND RECORD this server ever sent. See the
#: tail of build_group_record for the measurement.
#:
#: KEY: The lesson, because it cost two evenings: picking the part of a
#: change you feel least sure about is a good instinct for where to LOOK and is
#: not evidence. What finally discriminated was an A/B with no capital in it at
#: all -- two plain war fields per island -- whose garbage was self-labelling.

#: THE SHIPPED INDOOR ROOMS, and the --enter-room / --field-rooms index that
#: selects each. Decoded 2026-09-04 from the switch at 0x050191b0: `index - 9`,
#: bail above 0x15, then a byte table at 0x5019228 into a jump table at
#: 0x5019210. So the switch's WINDOW is 9..30, and only these five are known to
#: land on an arm that names a model. Everything else -- in the window or out of
#: it -- falls to the default arm, which is NOT nothing; see the last note below.
#:
#: KEY: The shops are ROOMS, not NPCs. The client ships no NPC placement
#: data at all -- DATA/Hmap/MapSpot*.cfg decrypts (FE00EN00 Rijndael, .cfg key)
#: to ambient SOUND (@SOUND WaterFall3 { @POS 142 49 134; @DIS 0 85; }), there
#: are no spawn/shop/town files anywhere in the install, and entity population
#: arrives only via 0x1006, which is ours to author. But the rooms themselves
#: ship: room_armsshop.pak, room_magicshop.pak, room_bar.pak, room_castle.pak,
#: room_house00.pak, room_bath.pak and room_test.pak are all present in a
#: retail client's DATA directory (checked on disk 2026-09-04), and the room
#: branch of field entry picks between them on this index through the name map
#: at 0x5192030 -> Rooms\<name>.mdl.
#:
#: WARNING: THE INDEX->ROOM PAIRING IS DECODED, NOT LIVE-VERIFIED. All five names
#: are in the client's .data as one contiguous block (file offsets
#: 0x74d70..0x74db4 of FE_Client.dll, in the order bar, house, castle, magic,
#: arms -- NOT index order), which corroborates the SET but not the mapping of
#: index to name: the on-disk DLL is ASProtect-packed, so 0x5192030 cannot be
#: resolved from it and the byte table was read in a dumped image. If a live
#: probe lands in a room other than the one named here, THE TABLE IS WRONG AND
#: THE PROBE STILL SUCCEEDED -- fix the names, not the mechanism.
#:
#: The names are room ids, not file names: each resolves to a pak whose single
#: model is named differently (checked 2026-09-04 by decrypting all seven with
#: fedecrypt key C -- room_armsshop.pak holds arms_storeroom_a.mdl +
#: arms_storeroom_a_hit.mdl, room_bar.pak holds bar_room_a.mdl, and so on).
#:
#: KEY: TWO ROOMS SHIP THAT THIS TABLE DOES NOT NAME: room_bath.pak
#: (bathroom_Inside_a.mdl) and room_test.pak. The window has 22 slots and we
#: have decoded 5, so there are almost certainly more arms to find.
#:
#: WARNING: AND THE DEFAULT ARM IS NOT "LOADS NOTHING" -- IT LOADS room_test.
#: Our own live history says so: feworld sent index **0** on every field entry
#: until 2026-08-23, 0 is BELOW the window (`index - 9` underflows and bails),
#: and what those runs loaded was room_test.pak. So a wrong index is a cheap
#: mistake -- the test room, not a black screen -- which is what makes sweeping
#: the unnamed slots a reasonable thing to do rather than a dangerous one.
SHIPPED_ROOMS = {
    9: "R5_bar_3D",              # the bar / inn
    10: "R2_arms_storeroom_3D",  # THE WEAPON SHOP
    11: "R3_magic_storeroom_3D", # THE MAGIC SHOP
    14: "R4_house_3D",           # a house
    15: "R1_castle00_room_3D",   # the castle (30 maps here too)
}

#: The switch's whole accepted window, named or not (`index - 9`, bail above
#: 0x15). -1 is not in it: -1 is the OUTDOORS sentinel, tested earlier by
#: 0x4ff7ed0, and never reaches this switch.
ROOM_INDEX_WINDOW = range(9, 31)


#: WARNING: RETRACTED 2026-09-06, and it was wrong TWICE over. It used to read
#: `{21: "DATA/capital/map00_00_BG.oct"}` -- "capital 21's assets are not
#: completely served by our mirror".
#:
#:   1. `map00_00` is not capital 21's map. The client's own table (CAPITAL_MAP)
#:      says map00_00 belongs to group 91 and map00_01 to group 39; 21's entry
#:      is not in that table at all. The pairing was guessed from the id order.
#:   2. The file is not missing from our mirror, it does not EXIST. The 2005
#:      disc has map00_00.oct and map00_00_hit.oct and no _BG at all, while
#:      every other capital map ships all three. So SE never shipped it and the
#:      client does not require it for that map.
#:
#: Both halves of the old note were inference presented as a finding, and the
#: honest check -- look at the disc -- took one command. Nothing is incomplete;
#: the dict stays so the warning code has something to read, and stays EMPTY.
CAPITAL_INCOMPLETE = {}

#: island -> [capital gid]; filled by main() from --capital.
_CAPITALS = {}


def parse_capitals(spec):
    """`ISLAND:GID[,ISLAND:GID...]` -> {island: [gid, ...]}."""
    out = {}
    for part in [p for p in (spec or "").split(",") if p.strip()]:
        a, _, b = part.partition(":")
        try:
            island, gid = int(a, 0), int(b, 0)
        except ValueError:
            raise SystemExit("--capital wants ISLAND:GID, got %r" % part)
        if gid not in CAPITAL_GROUP_IDS:
            raise SystemExit(
                "--capital %r: group id %d is not one the client treats as a "
                "capital. Its predicate 0x04ff7ef0 accepts only %s -- any other "
                "id takes the Hmap/room branch instead and you get a battlefield "
                "with a town's name." % (part, gid,
                                         ", ".join(str(g) for g in CAPITAL_GROUP_IDS)))
        out.setdefault(island, []).append(gid)
    return out


def group_ids_for(island, args, capitals=None):
    """Every group id announced for `island`: the war fields, then its capitals.

    Pulled out of the 0x3031 handler so the one thing that can desynchronise the
    client's parser is testable: 0x3031's header is [u16 island][u16 count] and
    then exactly `count` records, so the list the count is taken from and the
    list the records are built from must be the SAME list. They are, because
    there is only one.
    """
    if territory.world_from_dat(args):
        # KEY: In dat mode the island's membership is fet_area's, not a formula,
        # and the capital is already IN it (21 is on island 2 because the
        # table says so) -- so --capital adds nothing here and must not append
        # a duplicate id. A repeated id in one 0x3031 is two records claiming
        # the same group, and the client keeps whichever it parsed last.
        return territory.world_area_ids(island, args)
    caps = (_CAPITALS if capitals is None else capitals).get(island, [])
    n = max(0, int(getattr(args, "groups", 1)))
    return [_group_id(island, i, n) for i in range(n)] + list(caps)


def _group_id(island, i, per_island):
    """A group id that is unique across islands and inside 1..0x5F.

    The bound is the client's: 0x04ff8419 rejects anything above 0x5F before it
    will even look a group up.
    """
    return min(0x5F, (island - 1) * per_island + i + 1)


#: gid -> room index, filled by main() from --field-rooms.
_FIELD_ROOMS = {}


def parse_field_rooms(spec):
    """`GID:INDEX[,GID:INDEX...]` -> {gid: index}, validated against the switch.

    KEY: WHY THIS IS PER-GROUP AND --enter-room IS NOT ENOUGH. The room
    index is one i16 in 0x1000, but field entry branches on it THIRD, and the
    branch order is what makes a global value dangerous (0x04ff9d5e onward):

        1. [scene+0x4ec] in CAPITAL_GROUP_IDS -> the capital loader
        2. else index == -1                   -> the Hmap BATTLEFIELD
        3. else                               -> the indoor room

    So a global `--enter-room 10` does NOT put you in the arms shop from the
    capital -- the capital test wins and the index is never consulted -- while
    it DOES send every war-field entry indoors, replacing the battlefield with
    a shop. Exactly backwards from what anyone arming it wants. Keyed by group,
    one field can be a door into a room while the rest stay outdoors.
    """
    out = {}
    for part in [p for p in (spec or "").split(",") if p.strip()]:
        a, _, b = part.partition(":")
        try:
            gid, idx = int(a, 0), int(b, 0)
        except ValueError:
            raise SystemExit("--field-rooms wants GID:INDEX, got %r" % part)
        if idx != -1 and idx not in ROOM_INDEX_WINDOW:
            # Warned, not refused: index 0 is outside the window and is what
            # this server sent on every field entry for weeks, landing in
            # room_test. The default arm is a real room, so an out-of-window
            # index is a wasted run and not a wedge -- and refusing something
            # we have already watched work is its own kind of wrong.
            print("[feworld] WARNING: --field-rooms %s: %d is outside the switch's "
                  "window (0x050191b0 does `index - 9` and bails above 0x15, so "
                  "only %d..%d reach a decoded arm). It will fall to the DEFAULT "
                  "arm, which our own 2026-08 runs show is room_test."
                  % (part, idx, ROOM_INDEX_WINDOW[0], ROOM_INDEX_WINDOW[-1]),
                  flush=True)
        out[gid] = idx
    return out


def room_label(idx):
    """How to describe a room index in a log line, honestly."""
    if idx in SHIPPED_ROOMS:
        return SHIPPED_ROOMS[idx]
    if idx in ROOM_INDEX_WINDOW:
        return "unnamed -- inside the switch window but on no arm we have decoded"
    return "outside the switch window -- the default arm, i.e. room_test"


def room_for(group_id, args):
    """The i16 that goes to [scene+0x4e6] for THIS group -- see parse_field_rooms."""
    return _FIELD_ROOMS.get(group_id, args.enter_room)
