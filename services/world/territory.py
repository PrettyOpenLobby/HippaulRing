"""Nations and the territory map: the 0x3027 force record, who holds each area."""
import json
import os
import struct
import threading
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import character, deps, wire, zones

# ---------------------------------------------------------------------------
# 0x3027 MSG_GET_FORCE_INFO_OK -- the NATION record.
#
# The client asks for ForceID 1..5 in a loop the moment it is in the world, and
# an empty answer leaves the nation-select list blank (reported live). The
# handler at 0x05005868 reads [u32 ForceID][u32 Bit] and then, IF its own force
# lookup (0x5005560) resolves, hands the message to 0x5005090 -- which reads the
# body STRAIGHT THROUGH. Despite the `Bit[%x]` in the log line, that parser never
# touches the mask: it takes arg2 (the message) and ignores arg1 (the bits). So
# this is a fixed record, not a bitmask-driven one.
#
# Every name below is the client's own, taken from the log format string emitted
# beside each read (0x52d2cdc..0x52d2e6c). The reads with no log line are
# unnamed and left at zero.
FORCE_FIELDS = [
    ("u32",  "f0c",        "-> force+0x0c"),
    ("cstr", "name",       "-> force+0x10, and copied to +0x459: the nation NAME"),
    # KEY: +0x34 AND THE PAIR COUNT AT +0x38 ARE THE NATION LEGEND, and both were
    # sent as zero until 2026-09-12; live testing reported "fields held always
    # seems to say 0". They are read in exactly ONE place in the image -- 0x050FFDBE,
    # which resolves the force (0x5005560, the same lookup the 0x3027 handler
    # uses) and formats
    #     "●%s　国民数：%4d人　制圧フィールド数：%3d"   (0x052E4890)
    #        name         +0x34            +0x38
    # i.e. the ●-prefixed per-nation line: POPULATION from +0x34 and FIELDS
    # HELD from +0x38. SE's own world-map page says each continent lists
    # 「その大陸に領土を持っている国の名前」, which is this line.
    #
    # +0x38 is not a field of its own on the wire: it is the COUNT of the
    # (u32,u32) pair list, so the number of fields a nation holds IS the number
    # of pairs sent. We sent none. The pairs' contents are never read -- of the
    # 22 callers of the force lookup, only this one touches +0x34/+0x38, the
    # array of first elements at +0x3c has no reader at all, and the sum of
    # second elements at +0x1b8 has exactly one instruction touching it in the
    # whole image, the accumulator that builds it (0x05005177). So we send the
    # AREA IDS as the first element and 0 as the second: honest, self-
    # documenting, and nothing can read it wrong.
    ("u32",  "f34",        "POPULATION -> force+0x34, the 国民数 of the ●line"),
    # count + that many (u32, u32) pairs. The COUNT lands at force+0x38 and is
    # drawn as 制圧フィールド数; the first elements go to an array at +0x3c (no
    # reader) and the seconds are summed into +0x1b8 (no reader). Pass the
    # pairs as vals["pairs"]; vals["_pairs"] still forces a bare count.
    ("u32",  "_pairs",     "count of (u32,u32) pairs = FIELDS HELD (+0x38)"),
    ("i8",   "f1bc",       "-> force+0x1bc"),
    ("i16",  "w_local",    "read into a local, not stored on the force"),
    ("cstr", "s1c8",       "-> force+0x1c8"),
    ("cstr", "s1e9",       "-> force+0x1e9"),
    ("u32",  "FormerNumMember",          "m_FormerNumMember"),
    ("u32",  "FormerNumTerritory",       "m_FormerNumTerritory"),
    ("u16",  "FormerCultureLevel",       "m_FormerCultureLevel"),
    ("u32",  "WarTotalNum",              "m_WarTotalNum"),
    ("u32",  "FormerWarTotalNum",        "m_FormerWarTotalNum"),
    ("u32",  "WinTotalNum",              "m_WinTotalNum"),
    ("u32",  "FormerWinTotalNum",        "m_FormerWinTotalNum"),
    ("u32",  "TargetFieldID",            "m_TargetFieldID"),
    ("u32",  "ContinualClearNum",        "m_ContinualClearNum"),
    ("i16",  "HoursUntilNextInventory",  "m_HoursUntilNextInventory"),
    ("cstr", "KingMessage",              "m_KingMessage"),
]

_FW = {"u8": ">B", "i8": ">B", "u16": ">H", "i16": ">H", "u32": ">I"}


def build_force_record(vals):
    out = b""
    for kind, key, _note in FORCE_FIELDS:
        v = vals.get(key, 0)
        if key == "_pairs":
            # the count, then the pairs themselves -- see FORCE_FIELDS. The
            # count is what the client draws as 制圧フィールド数, so a nation
            # holding twenty fields must send twenty pairs.
            pairs = vals.get("pairs")
            if pairs is None:
                out += struct.pack(">I", int(v) & 0xFFFFFFFF)
            else:
                pairs = list(pairs)
                out += struct.pack(">I", len(pairs) & 0xFFFFFFFF)
                for a, b in pairs:
                    out += struct.pack(">II", int(a) & 0xFFFFFFFF,
                                       int(b) & 0xFFFFFFFF)
            continue
        if kind == "cstr":
            out += (v or "").encode("cp932", "replace") + bytes(1)
        else:
            out += struct.pack(_FW[kind], int(v) & (0xFF if "8" in kind else
                                                    0xFFFF if "16" in kind
                                                    else 0xFFFFFFFF))
    return out


def field_contest(island, args):
    """(defender, attacker) nation ids for a war field on `island`.

    None when --field-nations is unset. ONE place for the rule, because the
    0x3031 group record (def_id/atk_id) and the 0x3027 force record
    (m_TargetFieldID) both derive from it and used to compute it separately --
    the group side honoured --field-defenders, the force side did not, so the
    scoreboard said nation 5 attacked field 1 while field 5's record said
    nation 1 did.

    --field-defenders: island i is defended by entry i-1 (cycling); the
    attacker stays the ATK half of --field-nations unless that collides with
    the defender, in which case the next defender in the list attacks it.
    """
    if not getattr(args, "field_nations", None):
        return None
    a, _, b = args.field_nations.partition(":")
    dfn, atk = int(a, 0), int(b or a, 0)
    spec = getattr(args, "field_defenders", None)
    if spec:
        defs = [int(x, 0) for x in spec.split(",") if x != ""]
        if defs:
            dfn = defs[(island - 1) % len(defs)]
            if atk == dfn:
                atk = defs[island % len(defs)] if len(defs) > 1 else int(a, 0)
    return dfn, atk


# ---------------------------------------------------------------------------
# THE TERRITORY MAP -- which nation currently holds each of the 95 areas.
#
# KEY: WHY THIS HAS TO EXIST BEFORE ANY OF THE WAR DOES. The client's only
# nation-based rule (0x05009870) asks "is this field defended by my nation, or
# is anything NEXT to it": it reads `def_id` off the group record and walks the
# neighbour array. So `def_id` is not decoration on a war map, it IS the map --
# it decides which fields a player can walk into, what colour the route lines
# are drawn in, and which side of a war they are on. --field-nations/-defenders
# were assigning it by a formula over the island number, which cannot express
# "nation 2 took area 18 last night".
#
# Seeded from fet_area's own `nation` column -- SE's starting board -- then
# overlaid by the store file, then by --territory. Winning a war moves one
# entry; that is the whole of conquest.
# ---------------------------------------------------------------------------
_TERRITORY = {}
_TERRITORY_LOCK = threading.Lock()


def territory_path(args):
    p = getattr(args, "territory_file", None)
    if p is None:
        d = os.path.normpath(os.path.join(deps._HERE, os.pardir, "data"))
        p = os.path.join(d if os.path.isdir(d) else deps._HERE, "fe_territory.json")
    return p


def territory_load(args):
    """Seed _TERRITORY: shipped owners, then the store, then --territory.

    WARNING: The seed is the FULL 95 areas every time, so an area missing from the
    store falls back to who SE gave it to rather than to nobody. A field with
    def_id 0 is defended by nation 0, which is not a nation, and the frontier
    test would then never match it for anyone.
    """
    global _TERRITORY
    board = {a: r["nation"] for a, r in fegamedata.areas().items() if r["nation"]}
    p = territory_path(args)
    try:
        with open(p, encoding="utf-8") as fh:
            for k, v in (json.load(fh) or {}).items():
                try:
                    board[int(k)] = int(v)
                except (TypeError, ValueError):
                    continue
    except (OSError, ValueError):
        pass
    for part in [x for x in (getattr(args, "territory", "") or "").split(",") if x.strip()]:
        a, _, n = part.partition(":")
        try:
            board[int(a, 0)] = int(n, 0)
        except ValueError:
            raise SystemExit("--territory wants AREA:NATION, got %r" % part)
    with _TERRITORY_LOCK:
        _TERRITORY = board
    return board


def territory_save(args):
    p = territory_path(args)
    # os.devnull means "do not persist". Writing a temp file beside it and
    # renaming onto `nul` fails on Windows and strands nul.tmp.<pid> in the
    # working directory -- 33 of them had piled up from test runs.
    if not p or p == os.devnull:
        return
    with _TERRITORY_LOCK:
        snap = dict(_TERRITORY)
    tmp = "%s.tmp.%d" % (p, os.getpid())
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({str(k): v for k, v in sorted(snap.items())},
                                indent=1, sort_keys=True))
        os.replace(tmp, p)
    except OSError as e:
        print("[feworld] territory save failed: %s" % e, flush=True)


def territory_owner(area, args=None):
    """Who holds `area` right now. Falls back to the shipped owner."""
    with _TERRITORY_LOCK:
        if _TERRITORY:
            v = _TERRITORY.get(int(area))
            if v:
                return v
    return fegamedata.areas().get(int(area), {}).get("nation", 0)


def territory_set(area, nation, args=None):
    """Hand `area` to `nation`. Returns the previous holder, or None if the
    area is not one of the 95 -- a capture of an id the client cannot resolve
    is a no-op, not a new entry."""
    area, nation = int(area), int(nation)
    if area not in fegamedata.areas():
        return None
    with _TERRITORY_LOCK:
        was = _TERRITORY.get(area)
        _TERRITORY[area] = nation
    if args is not None:
        territory_save(args)
    return was


#: THE WAR-OUTCOME NOTIFIES -- what the world hears when a field changes hands.
#: All six were unserved until 2026-09-09; the client has handled them since
#: 2006 and each one has its own sentence ready to print.
#:
#: KEY: ALL SIX ARMS ARE THE SAME RECORD, decoded off 0x05056b80 / 0x05056c2c /
#: 0x05056cd8 and their siblings, which are byte-for-byte the same shape:
#:
#:     [u32 a][u32 OTHER NATION][u32 AREA]
#:
#: read in that order, then `group_by_id(#3)` (0x5009820) and
#: `force_by_id(#2)` (0x5005560), and the two names are pulled off
#: `group+0x2e` (the FIELD NAME) and `force+0x10` (the NATION NAME).
#:
#: #2 is always THE OTHER SIDE from the recipient's point of view -- the loser
#: in "Captured %s from %s", the winner in "%s was captured by %s", the beaten
#: attacker in "Defended %s from %s". #1 is READ AND PRINTED BUT NEVER USED by
#: any of the six arms, so what goes in it costs nothing; we send the
#: recipient's own side, which is the only other number in the pair.
LAND_NOTIFY_IDS = {
    "deprive":        0x3036,   # you TOOK it        -- Captured "%s" from %s.
    "deprived":       0x3037,   # you LOST it        -- "%s" was captured by %s.
    "attacking":      0x3038,   # your attack begins
    "being_attacked": 0x3039,   # your land is under attack
    "keep":           0x303A,   # you HELD it        -- Defended "%s" from %s.
    "keep_failed":    0x303B,   # your attack failed -- %s failed to capture "%s".
}


def land_notify_body(mine, other, area):
    """[u32 mine][u32 other nation][u32 area] -- see LAND_NOTIFY_IDS."""
    return struct.pack(">III", int(mine) & 0xFFFFFFFF,
                       int(other) & 0xFFFFFFFF, int(area) & 0xFFFFFFFF)


def land_notify(conn, outbound, mode, be, args, kind, mine, other, area):
    """Push one war-outcome notify. Returns the id sent, or None if refused.

    WARNING: Refuses an area the client cannot resolve. Every arm does
    `group_by_id(area)` and bails to a common exit on NULL, so an id outside
    the 95 is a silent no-op on the client -- it would look like the message
    never arrived, and we would go looking in the wrong place.
    """
    mid = LAND_NOTIFY_IDS.get(kind)
    if mid is None:
        return None
    if int(area) not in fegamedata.areas():
        print("[feworld]    land notify %s: area %s is not one of the 95, the "
              "client would resolve it to NULL and drop the message"
              % (kind, area), flush=True)
        return None
    wire.send(conn, outbound, wire.inner_msg(mid, land_notify_body(mine, other, area)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x%04X %s land notify: nation %s vs %s over area %s (%s)"
          % (mid, kind, mine, other, area,
             fegamedata.areas().get(int(area), {}).get("name", "?")), flush=True)
    return mid


def nation_of(args):
    """The playing character's nation -- the 0xD002 `force` field.

    This is the value the client's frontier test compares every field's def_id
    against ([char+0x30], read at 0x0502513d), so anything that reasons about
    where the player may go has to agree with it. 0x7FFFFFFF is the allocator's
    "never joined", not a nation.
    """
    v = character._load_char_field(args, "force")
    try:
        v = int(v)
    except (TypeError, ValueError):
        return 0
    return 0 if v in (0, 0x7FFFFFFF) else v


def world_from_dat(args):
    """True when the world is the shipped 95-area one rather than the
    synthetic islands-of-N that --islands/--groups build."""
    return getattr(args, "world", "synthetic") == "dat"


def world_area_ids(island, args):
    """Every area announced for `island` in dat mode, in id order.

    Includes the capital's INNER half (91..95): the table puts it on the
    island, and a group the client has never been told about cannot be
    resolved by 0x5009820 when a door lands the player in it -- the current
    island would stay -1. It draws at the same map pixel as its outer half, so
    PARTIAL: expect two markers on top of each other until someone looks.
    """
    ids = fegamedata.areas_on_island(island)
    if getattr(args, "world_inner_capitals", "on") != "on":
        ids = [a for a in ids if a not in zones.INNER_CAPITAL_GROUP_IDS]
    return ids


def apply_force_targets(force_table, args):
    """Fill m_TargetFieldID -- which field a nation is currently attacking.

    🔵 WHY THIS EXISTS. The client's own log, read off prod 2026-08-19, showed
    `m_TargetFieldID = 0` on EVERY nation, because feworld had never set it. A
    nation whose target field is 0 is attacking nothing, so across the whole
    world there is no war in preparation -- which is exactly the state a "War
    Prepare Window" would have nothing to show for.

    Default: each island's ATTACKER (field_contest, i.e. --field-nations with
    --field-defenders applied) targets that island's first group id; a nation
    attacking several islands targets the lowest. --force-targets overrides
    per nation as `forceid:fieldid` pairs.

    WARNING: UNVERIFIED WHICH ID SPACE THIS IS. The client's FIELD line prints the group
    id (`FIELD[00:ID01 ...]`), so the group id is the reasonable first guess, and
    it is what the default uses. If the window still does not open, an island id
    is the other candidate -- try `--force-targets` before assuming the field is
    not the blocker.
    """
    spec = getattr(args, "force_targets", None)
    if spec:
        for rec in spec.split(","):
            rec = rec.strip()
            if not rec:
                continue
            fid, _, target = rec.partition(":")
            if int(fid, 0) in force_table:
                force_table[int(fid, 0)]["TargetFieldID"] = int(target or 0, 0)
        return
    n = max(0, int(getattr(args, "groups", 1)))
    for island in range(1, int(getattr(args, "islands", 5)) + 1):
        contest = field_contest(island, args)
        if not contest or not n:
            continue
        _dfn, atk = contest
        rec = force_table.get(atk)
        if rec is not None and not rec.get("TargetFieldID"):
            rec["TargetFieldID"] = zones._group_id(island, 0, n)


def parse_forces(spec):
    """`id:Name` records, comma-separated -- the nations offered to the client."""
    out = {}
    for rec in spec.split(","):
        rec = rec.strip()
        if not rec:
            continue
        fid, _, name = rec.partition(":")
        out[int(fid, 0)] = {"name": name or ("Force %s" % fid)}
    return out
