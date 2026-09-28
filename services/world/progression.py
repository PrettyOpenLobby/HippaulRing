"""EXP, class level, skill points and Pw, and the pushes that show them."""
import struct
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import character, combat, itemrecords, sess, skilllist, wallet, wire

# ---------------------------------------------------------------------------
# KEY: PROGRESSION -- EXP -> CLASS LEVEL -> SKILL POINTS, and Pw (2026-09-11)
#
# What 2006 retail did (per the 2026-09-11 audit of period sources):
# new characters start at Lv1, the cap was 40 at launch, levels come from EXP,
# skill points come from levels and buy skills (GET!/LVUP), every skill costs
# Pw and Pw regenerates by itself, faster while crouching. Before this block
# every character was seeded Lv30 in all seven class slots, nothing ever
# levelled, SP was a flat budget and no skill cost anything.
#
# THE CLIENT'S OWN LEVEL-UP PROTOCOL, read off FE_Client.dll (dump base
# 0x04F90000) -- static only, nothing here has been seen on a screen:
#
#   0x1075 is a 4-way mask record (0x05051E20), applied IN BIT ORDER:
#     0x1 -> 0x05051E80  [u8 n] x {u8 idx, u8 v} -> [unit+0x9F0+idx], and
#                        [unit+0x3AC] = the LAST v written
#     0x2 -> 0x05052200  [u32]  -> [unit+0x135C]  EXP needed (the "next")
#     0x4 -> 0x05051F10  [u8 n] x {u8 class, u32 exp} -> [unit+0x9F8+class*4]
#                        (and the select struct's +0x8F0). If the new value is
#                        above the old one it floats "GetExp %d" (0x05262008)
#                        and prints "Gained %d exp" (0x052D56E0) -- and then,
#                        unless the unit is transformed ([unit+0x3B4]&0x3F),
#                        `if [unit+0x135C] > 0 && exp >= [unit+0x135C]` it
#                        SENDS 0x2059 (0x05052127) -- the level-up REQUEST.
#     0x8 -> 0x05052180  the acquired-skill words
#   0x2059 is answered 0x1091 (header-only; its arm 0x0503d800 is a bare
#   `ret`) or 0x1092 [u32 code] (1 = max level); the level the player SEES is
#   the byte bit 0x1 writes. So SE's flow is: server pushes EXP (0x4), the
#   CLIENT notices it crossed [unit+0x135C] and asks, the server grants and
#   pushes the new level/next/remainder. The per-class dword is PROGRESS
#   WITHIN THE LEVEL: the HUD bar widget (0x050CE2F4..0x050CE352) animates to
#   full and RESETS its accumulator when the level byte goes up, then loads
#   the new [unit+0x135C] -- a bar built for a numerator that starts over.
#
# WARNING: NO EXP CURVE SHIPS (dat.pak's tag census has no level/EXP table; the fet_*
# list has none either). exp_need() serves the RoD table the 2006 fan wiki
# kept (ROD_EXP_NEXT, `--exp-curve rod`, the default since 2026-09-11's
# second pass); `auto` is the invented curve it replaced.
#
# THE LEVEL GATES, all of them (every reader of [unit+0x9F0+class]):
#   0x05076194  the item EQUIP validator 0x05076090, code 4 = "too low":
#               [unit+0x9F0+[unit+0x3AB]] < [itemrec+0x86]. THE ONLY GATE.
#   0x0507DB93  its non-refusing twin 0x0507DB30 (flags: 1 prereq, 2 level,
#               4 sex asset) -- colours the item, refuses nothing
#   0x050E5294  the item tooltip's "Eq. Level" (red when short) -- display
#   0x050CD28B  "Lv.%d"; 0x050E129B the Status screen; 0x050CCA93 /
#   0x050CE2F4 / 0x050DEA4B the HUD EXP bar; 0x050CF15F a HUD widget;
#   0x050E439B a unit-list row; 0x050DFF02 level DIFFERENCE vs a target --
#               all display.
# The cast ladder (0x0507F510) and the skill window (0x050D3A00..0x050D5120)
# never read it: skills have NO level gate. So Lv30 was working around ONE
# thing -- the equip validator at level 0 (the byte was never sent) -- and
# every starting item (fet_initialize_equip: 301/483/531 + the casual set)
# ships req_level 1, so Lv1 in the character's OWN class passes it.
#
# SKILL POINTS: the skill window's GET!/LVUP state (0x050D3A64..0x050D3B8E)
# compares [skill+0xF0] of the NEXT rank against [unit+0x498]: 3 = GET greyed
# (not enough SP), 4 = LVUP greyed. +0xF0 is read as a u16 by the SKILL_DATA
# parser (0x051B131D) and is 2 for every learnable rank 1..5 in the shipped
# table (fe-fet-skill.tsv `sp_cost`), so the SP COST IS SHIPPED DATA. What a
# level GRANTS is not: 2026-09-11 (second pass) the RoD number turned up --
# 1 per level, 40 at Lv40 (fewiki Guide/メモ 2006-05) -- and it is the
# default; the FEZ-era 2/1/0 schedule is FEZ_SP_PER_LEVEL.
# WARNING: THE RoD wiki's PER-RANK costs do NOT match this client, checked
# 2026-09-11: fewiki's warrior page (2006-06-14) gives Sonic Boom 2/1/1,
# Behemoth Tail 3/1/1/2/2, Dragon Tail 4/2 -- first rank 2-4, later 1-2, and
# 2-5 ranks per skill. This build's SKILL_DATA has FIVE ranks for every skill
# (Sonic Boom L1..L5 = ids 40..44) and every integer field of the record was
# dumped against the wiki pattern (fefet.skill_record): +0xF0 is 2 on every
# learnable rank and no other field carries 2-4 / 1-2. SKILL_LEARN_DATA is a
# prerequisite table (c/d are 1/1, 2/1, 2/2 steps, not costs). So the client
# shipped a different (flatter) skill tree than the June 2006 one, and the
# server keeps charging +0xF0: it is the number the client's OWN skill window
# greys GET!/LVUP against, and charging a wiki cost the window does not know
# would refuse clicks the window offered.
#: the SEED for a character with no stored table: Lv1 in its own class (2006:
#: every character started at 1). Was `all:30`, the equip-gate workaround.
DEFAULT_CLASS_LEVELS = "self:1"
DEFAULT_EXP_MODEL = "level"          # `flat` = the 09-09 display-only bar
#: 2026-09-11 (second pass): the RoD table the 2006 fan wikis kept -- see
#: ROD_EXP_NEXT. `auto` (16*L*(L+5)) was OURS and stays selectable.
DEFAULT_EXP_CURVE = "rod"
#: RoD: 1 SP per level, 40 at Lv40 -- fewiki Guide/メモ 2006-05-26
#: 「Lvが1上がるごとに1スキルポイントを獲得」 and the 2006-06-14 warrior page
#: (22 common + 18 one-hand SP = exactly 40 at the Lv40 cap). The FEZ-era
#: 2/1/0 schedule (fewiki 2007-02, FFSKY 日服) is FEZ_SP_PER_LEVEL.
DEFAULT_SP_PER_LEVEL = "1:1-40"
FEZ_SP_PER_LEVEL = "2:1-5,1:6-35,0:36-40"

# ---------------------------------------------------------------------------
# KEY: THE 2006 NUMBERS (2026-09-11, second pass). Three 2006 fan wikis turned
# up in the Wayback Machine -- fewiki.com (the main RoD wiki), the Netzawar
# wiki, the Hordaine war wiki -- and a 2007-02 revision of fewiki's
# Guide/メモ labels its level table 旧仕様 (old spec = RoD) against cβ/oβ
# (FEZ). These replace numbers the team had INVENTED. Research note:
# scratchpad research-numbers.md, 2026-09-11. Every table says its source.
# ---------------------------------------------------------------------------
#: EXP from Lv L to L+1 ("NEXT"), L = 1..40. fewiki Guide/メモ 2006-05-26
#: (labelled 旧仕様 in the 2007-02-07 revision); crowd-filled from memory but
#: backed by dated edits (Lv19 5,200 on 2006-03-05; Lv34 500k / Lv35 700k on
#: 2006-03-04), and China's 2007 closed test ran almost the same curve.
#: Lv40 shows 3,200,000 but "経験値は入りません" -- EXP stops at the cap.
ROD_EXP_NEXT = (
    30, 45, 60, 100, 120, 150, 200, 250, 300, 350,                    # 1-10
    450, 600, 850, 1100, 1500, 2000, 2800, 3800, 5200, 7000,          # 11-20
    9500, 13000, 18000, 24000, 32000, 44000, 60000, 82000, 110000,    # 21-29
    150000,                                                           # 30
    200000, 280000, 380000, 500000, 700000, 950000, 1300000,          # 31-37
    1750000, 2400000, 3200000)                                        # 38-40
#: A monster's EXP and gold are set by its LEVEL alone ("Mobの種類によらず
#: レベルだけによって決まります"). fewiki `Monster` 2006-05-26, confirmed by
#: FFSKY's 2007 China-test table. EXP = the level up to Lv20.
ROD_MOB_EXP = dict(
    [(L, L) for L in range(1, 21)]
    + list(zip(range(21, 41), (30, 37, 47, 58, 72, 86, 104, 128, 154, 195,
                               240, 312, 395, 481, 641, 821, 1064, 1360, 1775,
                               2258)))
    + [(42, 3880), (43, 5090), (44, 6567)])
ROD_MOB_GOLD = dict(zip(range(1, 49), (
    5, 9, 13, 17, 21, 26, 30, 35, 39, 44,
    49, 55, 61, 67, 75, 82, 91, 101, 113, 126,
    141, 160, 184, 210, 239, 264, 326, 385, 449, 536,
    630, 779, 840, 1106, 1378, 1676, 2059, 2500, 3100, 3735,
    4946, 6550, 8173, 11400, 15180, 19414, 26113, 34426)))


def rod_mob_exp(level):
    """EXP for a monster of `level` (ROD_MOB_EXP). WARNING: Lv41 is blank on the
    page -- the geometric middle of Lv40 and Lv42 (2960) is ASSUMED; above
    Lv44 no number survives, so Lv44's 6567 is held (ASSUMED)."""
    L = max(1, int(level or 1))
    if L in ROD_MOB_EXP:
        return ROD_MOB_EXP[L]
    if L == 41:
        return int(round((ROD_MOB_EXP[40] * ROD_MOB_EXP[42]) ** 0.5))
    return ROD_MOB_EXP[44]


def rod_mob_gold(level):
    """Gold for a monster of `level` (ROD_MOB_GOLD); above Lv48 no number
    survives, so Lv48's 34,426 is held (ASSUMED)."""
    L = max(1, int(level or 1))
    return ROD_MOB_GOLD.get(L, ROD_MOB_GOLD[48])
DEFAULT_CLASS_LEVEL_MAX = 40         # 4Gamer: 「正式サービスでは，40まで上がります」
DEFAULT_CLASS_LEVELUP = "ok"
DEFAULT_PW_MAX = 100                 # SE 3rd beta: Lv25 Sorcerer Pw 100
#: Pw per 0x2028 tick. KEY: 2026-09-12: 16, SOURCED -- fewiki メモ rev
#: 2006-04-12 (RoD) 「16Pow/3〜4秒、座ると16Pow/1〜2秒」, fewiki 2007-02 and
#: ffsky (num/ffsky_information.txt:23-24) "16 per 3 s, 16 per 1.5 s seated".
#: The client holds no amount (its sender 0x0507D810 sends only the tick
#: count; 3000 ms, 1349 ms crouched), so this is the server's number. Was 3,
#: chosen.
DEFAULT_PW_REGEN = 16
DEFAULT_PW_COST = "cast"
EXP_BIT_LEVEL, EXP_BIT_NEXT, EXP_BIT_EXP, EXP_BIT_SKILL = 0x1, 0x2, 0x4, 0x8
#: 0x2024 maskA bits on the PLAYER's own unit (setters gated [+0x20]==3):
#: 0x1 -> +0x498 skill points (0x04FF3B57), 0x10 -> +0x4A4 Pw current
#: (0x04FF3FD7). Both are plain i16 STORES.
U2024_SP, U2024_PW = 0x1, 0x10


DEFAULT_SKILL_GRANT = "class"


def granted_class_skills(args, cls=None):
    """The class's own starting set (FE_CLASS_BASIC_PARAM_DATA +0x70) -- see
    skill_list_push's `class` grant. [] with no class."""
    if cls is None:
        cls = skilllist.self_class_id(args)
    return [] if cls is None else list(fegamedata.class_start_skills(cls))


def served_skills(args):
    """Every id skill_list_push marks owned, without its log lines -- what the
    client's skill window believes the character has."""
    ids = set(skilllist.acquired_skills(args))
    grant = getattr(args, "skill_grant", DEFAULT_SKILL_GRANT)
    if grant == "class":
        ids |= set(granted_class_skills(args))
    elif grant == "items":
        ids |= set(fegamedata.item_skills(
            [int(no) for _u, no, _f, _c in itemrecords.item_rows(args)]))
    elif grant == "all":
        ids = set(range(skilllist.SKILL_ARRAY_MAX))
    return ids


def level_model_on(args):
    """True when EXP drives the class level (the 2006 rule). `flat` keeps the
    2026-09-09 behaviour: a display-only bar of --exp-per-level, no level-ups."""
    return str(getattr(args, "exp_model", DEFAULT_EXP_MODEL)) == "level"


def exp_need(args, level):
    """EXP needed to go from `level` to `level + 1` -- the value served in
    [unit+0x135C]. No curve SHIPS in the client.

    `rod` (default, 2026-09-11) = ROD_EXP_NEXT, the RoD table of the 2006
    fan wiki: 30 at Lv1, 350 at Lv10, 7,000 at Lv20, 150,000 at Lv30,
    2,400,000 at Lv39, 3,200,000 at Lv40 (~9.03M to reach Lv40). A level
    past the table repeats Lv40's. Against the RoD monster table (EXP = the
    level to L20) Lv1 takes 30 kills of Lv1 monsters -- in 2006 war was the
    main EXP source past the early levels (fecampaign's war_reward).

    `auto` (OURS, the 2026-09-11 first pass) = 16 * L * (L + 5): 96 at Lv1,
    2,400 at Lv10, 16,800 at Lv30, 27,456 at Lv39 -- about 391k to reach 40,
    scaled to the SHIPPED npc_type EXP column (~15.4 x the monster's level).
    `flat:N` = N every level; `N1,N2,...` = an explicit table (the last entry
    repeats)."""
    L = max(1, int(level or 1))
    spec = str(getattr(args, "exp_curve", DEFAULT_EXP_CURVE)
               or DEFAULT_EXP_CURVE).strip()
    if spec == "rod":
        return ROD_EXP_NEXT[min(L, len(ROD_EXP_NEXT)) - 1]
    try:
        if spec.startswith("flat:"):
            return max(1, int(spec[5:], 0))
        if spec != "auto":
            vals = [int(x, 0) for x in spec.split(",") if x.strip()]
            if vals:
                return max(1, vals[min(L, len(vals)) - 1])
    except ValueError:
        pass
    return 16 * L * (L + 5)


def class_level_cap(args):
    return max(1, int(getattr(args, "class_level_max", DEFAULT_CLASS_LEVEL_MAX)
                      or DEFAULT_CLASS_LEVEL_MAX))


def self_level(args, cls=None):
    """The stored level of the character's OWN class (0 when none)."""
    if cls is None:
        cls = skilllist.self_class_id(args)
    if cls is None:
        return 0
    rows, _bad = skilllist.stored_class_levels(args, cls)
    return dict((int(i), int(v)) for i, v in rows).get(int(cls), 0)


def class_exp_seed(args):
    """_SESSION["class_exp"] = {class: EXP into the CURRENT level}, from the
    store's `class_exp` (it rides festore's `extra` column -- no migration).
    A character that has none starts every class at 0 -- which is also the
    migration: a stored Lv30 keeps its 30 and starts 0 into it."""
    if "class_exp" not in sess._SESSION:
        stored = character._load_char_field(args, "class_exp", None) or {}
        out = {}
        if isinstance(stored, dict):
            for k, v in stored.items():
                try:
                    out[int(k)] = max(0, int(v))
                except (TypeError, ValueError):
                    continue
        sess._SESSION["class_exp"] = out
    return sess._SESSION["class_exp"]


def class_exp_store(args, cls, value):
    ce = class_exp_seed(args)
    ce[int(cls)] = max(0, int(value))
    return character._store_char_field(args, "class_exp",
                             {str(k): v for k, v in sorted(ce.items())})


def exp_view(args):
    """(cls, level, into, need, served_into) for the character's own class.

    At the cap the served progress is clamped to need-1: the client asks for a
    level-up whenever exp >= [unit+0x135C], and answering every kill with
    0x1092 "max level" would pop the NG dialog on each one."""
    cls = skilllist.self_class_id(args)
    if cls is None:
        return None, 0, 0, exp_need(args, 1), 0
    level = self_level(args, cls)
    into = class_exp_seed(args).get(int(cls), 0)
    need = exp_need(args, level)
    served = into
    if level >= class_level_cap(args):
        served = min(into, need - 1)
    return cls, level, into, need, served


def exp_1075_body(level_rows=(), nxt=None, exp_rows=(), skills=None, value=1):
    """One 0x1075 with any of its four blocks, IN BIT ORDER (0x05051E20 tests
    0x1, 0x2, 0x4, 0x8 and reads as it goes). A bit is set only when its
    block follows -- a bit with no block desynchronises the record."""
    level_rows, exp_rows = list(level_rows), list(exp_rows)
    mask = ((EXP_BIT_LEVEL if level_rows else 0)
            | (EXP_BIT_NEXT if nxt is not None else 0)
            | (EXP_BIT_EXP if exp_rows else 0)
            | (EXP_BIT_SKILL if skills is not None else 0))
    body = struct.pack(">I", mask)
    if level_rows:
        body += struct.pack(">B", len(level_rows))
        for idx, v in level_rows:
            body += struct.pack(">BB", int(idx) & 0xFF, int(v) & 0xFF)
    if nxt is not None:
        body += struct.pack(">I", int(nxt) & 0xFFFFFFFF)
    if exp_rows:
        body += struct.pack(">B", len(exp_rows))
        for cls, v in exp_rows:
            body += struct.pack(">BI", int(cls) & 0xFF, int(v) & 0xFFFFFFFF)
    if skills is not None:
        ids = [int(s) for s in skills if 0 <= int(s) < skilllist.SKILL_ARRAY_MAX]
        body += struct.pack(">H", len(ids))
        for sid in ids:
            body += struct.pack(">HB", sid, value & 0xFF)
    return body


def level_state_push(conn, outbound, mode, be, args, why=""):
    """Push the own class's level, next and progress in ONE 0x1075 (bits
    0x1|0x2|0x4) and the skill points. The level row is the ONLY bit-0x1 row
    so [unit+0x3AC] (the last value written) is the level too. If the
    remainder still covers the new `next` the client asks again (0x2059), so
    one big kill walks up several levels one request at a time."""
    cls, level, into, need, served = exp_view(args)
    if cls is None:
        return False
    body = exp_1075_body([(cls, level)], need, [(cls, served)])
    wire.send(conn, outbound, wire.inner_msg(0x1075, body, wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", getattr(args, "world_prefix", 4))
    sp = sp_push(conn, outbound, mode, be, args)
    print("[feworld]    -> 0x1075 mask=0x7 class %d LEVEL %d, next %d, "
          "progress %d/%d%s; skill points %s%s"
          % (cls, level, need, served, need,
             " (AT THE CAP %d -- clamped below next so the client never asks)"
             % class_level_cap(args) if level >= class_level_cap(args) else "",
             sp if sp is not None else "not served",
             (" -- " + why) if why else ""), flush=True)
    return True


def level_up_apply(args, levels=1, need_exp=True):
    """Advance the own class `levels` times. With need_exp each step must be
    PAID from the stored progress (the 2006 rule, and what 0x2059 asks for);
    without it (the GM verb) progress is kept. Returns (code, old, new):
    code None = done, 1 = at the cap, 0 = no class/table, 2 = not enough EXP,
    3 = the store refused."""
    cls = skilllist.self_class_id(args)
    if cls is None:
        return 0, 0, 0
    rows, _bad = skilllist.stored_class_levels(args, cls)
    if not rows:
        return 0, 0, 0
    table = dict((int(i), int(v)) for i, v in rows)
    old = cur = table.get(int(cls), 0)
    cap = class_level_cap(args)
    if cur >= cap:
        return 1, old, cur
    into = class_exp_seed(args).get(int(cls), 0)
    for _ in range(max(1, int(levels))):
        if cur >= cap:
            break
        need = exp_need(args, cur)
        if need_exp:
            if into < need:
                break
            into -= need
        cur += 1
    if cur == old:
        return 2, old, cur
    table[int(cls)] = cur
    if not character._store_char_field(args, "class_levels",
                             {str(i): v for i, v in sorted(table.items())}):
        return 3, old, old
    class_exp_store(args, cls, into)
    return None, old, cur


def sp_schedule(args):
    """--sp-per-level as [(per, lo, hi)], or None for `off` (the legacy flat
    --add-stats 0:N budget)."""
    spec = str(getattr(args, "sp_per_level", DEFAULT_SP_PER_LEVEL)
               or "").strip()
    if not spec or spec == "off":
        return None
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            per, rng = part.split(":")
            lo, _, hi = rng.partition("-")
            out.append((int(per), int(lo), int(hi or lo)))
        except ValueError:
            print("[feworld]    --sp-per-level: unreadable %r ignored" % part,
                  flush=True)
    return out


def sp_earned(sched, level):
    """Skill points a character of `level` has been granted in total."""
    return sum(per for per, lo, hi in sched
               for lv in range(1, int(level) + 1) if lo <= lv <= hi)


def sp_spent(args, cls=None):
    """SP the stored skills cost: each learned rank's own [skill+0xF0]
    (shipped). The class's starting set (FE_CLASS_BASIC_PARAM_DATA +0x70:
    basic attack, first skills, proficiencies) is FREE -- it is what the class
    starts knowing -- so a stored copy of one costs nothing."""
    if cls is None:
        cls = skilllist.self_class_id(args)
    free = set(fegamedata.class_start_skills(cls)) if cls is not None else set()
    return sum(skill_sp_cost(s) for s in set(skilllist.acquired_skills(args))
               if int(s) not in free)


def skill_sp_cost(sid):
    """[skill+0xF0] for a LEARNABLE rank, else 0. The 436..447 proficiencies
    carry 100 and monster skills 0 -- neither is bought through GET!, so a
    stored copy of one (an old store, a probe) must not eat the budget."""
    c = fegamedata.skills().get(int(sid), {}).get("sp", 0)
    return c if 0 < c < 100 else 0


def skill_points(args, flag_budget=None):
    """The +0x498 value: what is LEFT. With --sp-per-level off this is the old
    flat budget (--add-stats 0:N) minus one per stored skill; otherwise it is
    what the character's level earned minus what its skills cost. Never
    negative -- a stored table seeded before this existed can overspend."""
    sched = sp_schedule(args)
    if sched is None:
        return max(0, int(flag_budget or 0) - len(skilllist.acquired_skills(args)))
    cls = skilllist.self_class_id(args)
    return max(0, sp_earned(sched, self_level(args, cls)) - sp_spent(args, cls))


def sp_push(conn, outbound, mode, be, args):
    """0x2024 maskA 0x1 -> [unit+0x498]. Returns the value, or None when SP
    is not served (--sp-per-level off and no --add-stats bit 0)."""
    stats = getattr(args, "add_stats", None) or {}
    if sp_schedule(args) is None and 0 not in stats:
        return None
    sp = skill_points(args, stats.get(0))
    wire.send(conn, outbound,
         wire.inner_msg(0x2024, struct.pack(">IIh", U2024_SP, 0, min(sp, 0x7FFF)),
                   wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", getattr(args, "world_prefix", 4))
    return sp


def pw_max(args):
    return max(1, int(getattr(args, "pw_max", DEFAULT_PW_MAX) or DEFAULT_PW_MAX))


def pw_now(args):
    return int(sess._SESSION.get("pw", pw_max(args)))


def pw_push(conn, outbound, mode, be, args, value, why=""):
    """0x2024 maskA 0x10 -> [unit+0x4A4], the Pw the gauge draws and cast
    gate 5 (0x05065DC0) tests. KEY: THE CLIENT NEVER LOWERS IT ITSELF: the only
    writers of +0x4A4 in the image are the char->unit copies (0x04FE75FD /
    0x04FE79FE), the class-table init (0x0506A62D), the 0x1006 stat block
    (0x0507A9EE) and this setter (0x04FF3FD7) -- no subtract anywhere. So a
    server debit cannot double-count, and without one every skill is free."""
    v = max(0, min(int(value), pw_max(args)))
    sess._SESSION["pw"] = v
    wire.send(conn, outbound,
         wire.inner_msg(0x2024, struct.pack(">IIh", U2024_PW, 0, v),
                   wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", getattr(args, "world_prefix", 4))
    if why:
        print("[feworld]    -> 0x2024 maskA 0x10 Pw %d/%d -- %s"
              % (v, pw_max(args), why), flush=True)
    return v


def progress_entry_push(conn, outbound, mode, be, args):
    """Field entry: skill points (level model) and a full Pw gauge, after the
    unit exists (both setters resolve it by id and skip a miss). Pw starts
    full because the client's own unit does: the class table (0x0506A5E0)
    writes max into +0x4A4 when it builds the unit."""
    if level_model_on(args):
        sp = sp_push(conn, outbound, mode, be, args)
        if sp is not None:
            print("[feworld]    -> 0x2024 maskA 0x1 SKILL POINTS %d (level %d "
                  "earned %s, skills cost %d)"
                  % (sp, self_level(args),
                     sp_earned(sp_schedule(args) or [], self_level(args)),
                     sp_spent(args)), flush=True)
    if str(getattr(args, "pw_cost", DEFAULT_PW_COST)) != "off":
        pw_push(conn, outbound, mode, be, args, pw_max(args),
                "field entry: the gauge starts full")


def exp_seed(args):
    """_SESSION["exp"] = the character's stored EXP total (0 when none).
    Idempotent; the first caller pays the store read."""
    if "exp" not in sess._SESSION:
        stored = character._load_char_field(args, "exp", None)
        try:
            sess._SESSION["exp"] = int(stored) if stored is not None else 0
        except (TypeError, ValueError):
            sess._SESSION["exp"] = 0
        if stored is not None:
            print("[feworld]    EXP: resuming from the stored total %d"
                  % sess._SESSION["exp"], flush=True)
    return sess._SESSION["exp"]


def exp_numerator(args, total):
    """What goes in the per-class dword -- the FIRST number of "Exp %d/%d".

    WARNING: 2026-09-09, SECOND PASS. Splitting the two halves was not enough: with
    `auto` the numerator was still the RUNNING TOTAL against "the next multiple
    of 1000", so a total of 823 drew 823/1000 -- 82%, which reads as full at a
    glance, and it never resets. A level bar has to EMPTY.

    So with `auto` the pair is progress WITHIN the level:

        numerator   = total mod --exp-per-level
        denominator = --exp-per-level

    823 -> 823/1000, and the next kill (+184) -> 7/1000. The bar fills and
    starts again, which is what a level bar does and what makes it obvious on
    screen that it moved at all.

    Any other --exp-next (a pinned number, or `off`) leaves the numerator as
    the running total, which is the old meaning.

    2026-09-11: under --exp-model level (the default) the numerator is the
    STORED progress into the current level of the own class (exp_view) and
    `total` is not consulted -- it is only the lifetime ledger now.
    """
    if str(getattr(args, "exp_next", "auto")) != "auto":
        return int(total)
    if level_model_on(args):
        return exp_view(args)[4]
    per = max(1, int(getattr(args, "exp_per_level", 1000) or 1000))
    return int(total) % per


def exp_denominator(args, total):
    """What goes in [unit+0x135c] -- the SECOND number of the Status screen's
    "Exp %d/%d", and the one that makes the HUD bar full or not.

    WARNING: THE BUG SEEN LIVE, 2026-09-09: "exp is being gained after
    successful combat, but the bar on the main battle HUD doesn't update -- it
    always appears full." It always was full. The draw is

        0x050E1388  mov ecx, [esi+0x135c]            ; the SECOND %d
                    mov dl,  [esi+0x3ab]             ; the class
                    mov eax, [esi + edx*4 + 0x9f8]   ; the FIRST %d
                    sprintf(buf, "%d/%d", eax, ecx)

    -- two DIFFERENT fields, and we were writing the same running total into
    both (the per-class dword via --exp-display, +0x135c via 0x1075 bit 2), so
    it read "823/823" and the bar pinned at 100%.

    WARNING: THE CURVE IS OURS. No EXP-per-level table ships in dat.pak -- the
    tag census has no such member, and FE_CLASS_BASIC_PARAM_DATA's twelve u16
    are flat base stats, not a curve. `auto` is deliberately the most obviously
    placeholder rule that makes the bar move: the next multiple of
    --exp-per-level above the total. It is a PROGRESS BAR that fills and
    resets, not a claim about FE's progression. Give --exp-next a number to
    pin it, or `off` to restore the full bar.
    """
    mode = str(getattr(args, "exp_next", "auto"))
    if mode == "off":
        return total
    if mode != "auto":
        try:
            return max(1, int(mode, 0))
        except (TypeError, ValueError):
            return total
    if level_model_on(args):
        # the curve's step for the level the character IS at (exp_need)
        return exp_view(args)[3]
    per = max(1, int(getattr(args, "exp_per_level", 1000) or 1000))
    return per


def exp_class_rows(args):
    """The EXP dword rows for THIS character, or []. Rides bit 0 with the
    levels, which is why it is built here and not in the resume push."""
    if getattr(args, "exp_display", "on") != "on":
        return []
    if level_model_on(args):
        # progress into the level, written SILENTLY through the level bytes:
        # the native bit 0x4 would float "GetExp N" at every field entry,
        # because the unit arrives with the select struct's 0 in that dword.
        cls, _lv, _into, _need, served = exp_view(args)
        return skilllist.class_exp_rows(cls, served) if cls is not None and served > 0 else []
    exp = exp_seed(args)
    if exp <= 0:
        return []          # nothing to say, and four zero bytes say it louder
    return skilllist.class_exp_rows(skilllist.self_class_id(args), exp_numerator(args, exp))


def exp_max_value(args):
    """What [unit+0x135c] should hold for this character, or None when there
    is nothing to say (no level model, no stored total)."""
    if level_model_on(args) and skilllist.self_class_id(args) is not None:
        return exp_view(args)[3]
    total = exp_seed(args)
    if total <= 0:
        return None
    return exp_denominator(args, total)


def exp_max_push(conn, outbound, mode, be, args, why=""):
    """0x1075 mask bit 2 ALONE -- [unit+0x135c], which is BOTH the HUD EXP
    gauge's max and the threshold the client tests before asking 0x2059.

    WARNING: IT HAS TO GO OUT BEFORE THE CLIENT BUILDS THE GAUGE, and that is the
    only reason this exists apart from exp_resume_push. Measured 2026-09-12 on
    the fifth report of "the exp bar shows 100%":

        gauge ctor  0x050CCA9D   [gauge+0x80] = [unit+0x135c]   ; the max, CACHED
        update      0x050CE2C0   ebp = [gauge+0x80]
                                 cmp ebx, [gauge+0x84] ; je settled
                                 cmp ebx, ebp          ; ja RETURN   <- BAILS
                                 [gauge+0x84] = ebx
                    settled:     the fill creeps toward the exp; on a level
                                 rise it rolls over and THEN refreshes
                                 [gauge+0x80] from +0x135c (0x050CE34C)

    +0x80 is written in exactly two places -- construction and post-rollover --
    so a stale max DEADLOCKS: an exp above it returns before storing +0x84, the
    settled branch never runs, the rollover never happens, the max is never
    refreshed, and [gauge+0x7c] keeps whatever fill it had. Nothing on the wire
    can force a refresh afterwards.

    It used to ride exp_resume_push, ~90 lines further down the field-entry
    burst: after skill_list_push had already put the numerator in +0x9F8, and
    after 0x100E released the loading screen. So the gauge could be built on a
    zero max with a non-zero exp -- the deadlock exactly. That is why 09-09's
    two value fixes and 1acc94d1 all failed: they corrected the numbers, and
    the widget stops reading before it reaches them.

    exp_resume_push still calls this, so --add-self off (which has no ADD to
    hang the early push off) keeps a denominator. The repeat is an idempotent
    write of the same value onto the same field.
    """
    nxt = exp_max_value(args)
    if nxt is None:
        return None
    wire.send(conn, outbound,
         wire.inner_msg(0x1075, exp_1075_body(nxt=nxt), wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld]    -> 0x1075 mask=0x2 [unit+0x135c] = %d%s -- the HUD "
          "gauge CACHES this at construction and can never refresh it while "
          "exp > it, so it has to be on the wire before the gauge is built"
          % (nxt, (" (" + why + ")") if why else ""), flush=True)
    return nxt


def exp_max_seed_pump(conn, outbound, mode, be, args):
    """Repeat the bit-2 denominator DURING FIELD LOAD, until it lands.

    WARNING: WHY THIS EXISTS, and it is the sixth pass at the HUD EXP bar. The
    gauge is fully read now (2026-09-12):

        ctor    0x050CCA7D  [gauge+0x84] = [gauge+0x7c] = [unit+0x9F8+cls*4]
                0x050CCA93  [gauge+0x7a] = [unit+0x9F0+cls]   the LEVEL
                0x050CCA9D  [gauge+0x80] = [unit+0x135c]      the MAX
        update  0x050CE306  eax = (long)([gauge+0x80] * K)    the step
                0x050CE315  cmp exp, [gauge+0x80] ; ja RETURN
                0x050CE331  eax = [gauge+0x7c] + step
                0x050CE336  cmp eax, [gauge+0x80] ; jbe RETURN
                0x050CE346  [gauge+0x80] = [unit+0x135c]      the ONLY refresh

    Everything is cached at CONSTRUCTION. If the gauge is built while
    [unit+0x135c] is still 0 then max = 0, the step is (long)(0 * K) = 0, the
    fill can never exceed the max, the rollover that would refresh the max is
    unreachable, and the widget is left drawing 0/0 -- which is the "always
    full" bar, on its fifth report. Any exp above the cached max bails at
    0x050CE315 before even storing +0x84.

    KEY: And [unit+0x135c] has EXACTLY ONE writer in the whole client:
    0x0505222C, the 0x1075 mask bit-2 arm. Nothing in the lobby record, the
    select struct or the class table touches it. So the ONLY wire-side lever
    is to make that write land before the gauge is constructed, and pushing
    it first in the post-load 0x4011 burst (a1900b9a) was still too late:
    the client builds its own "Chara 1" and its HUD DURING field load, which
    is before that burst exists.

    0x1075 resolves the unit by id (0x0504CB30) and a miss writes nothing --
    silently, no error -- so an early push simply costs 8 bytes when it is
    too early. This therefore repeats it on every inbound message from the
    0x2000 MSG_ENTER_AREA answer onward, up to --exp-max-seed times, so that
    whichever message first finds the unit alive lands the threshold. It
    stops as soon as the field-entry burst has run.

    WARNING: IT IS A PROBE. If the bar is still full with this on, then the gauge
    is constructed before the unit is reachable at all and there is no
    wire-side lever left -- the next move is the Status window's own
    "Exp %d/%d" text (0x050E1388), which draws +0x9F8 and +0x135c directly.
    """
    left = int(sess._SESSION.get("exp_max_seed_left", 0) or 0)
    if left <= 0 or sess._SESSION.get("add_complete_sent"):
        return False
    sess._SESSION["exp_max_seed_left"] = left - 1
    nxt = exp_max_value(args)
    if nxt is None:
        sess._SESSION["exp_max_seed_left"] = 0
        return False
    wire.send(conn, outbound,
         wire.inner_msg(0x1075, exp_1075_body(nxt=nxt), wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    if left == int(getattr(args, "exp_max_seed", 8) or 8):
        print("[feworld]    -> 0x1075 mask=0x2 [unit+0x135c] = %d DURING FIELD "
              "LOAD (--exp-max-seed %d): the HUD gauge caches this at "
              "construction and the client builds its HUD while the field "
              "loads, so the post-load burst is too late. A push before the "
              "unit exists is a silent no-op (0x0504CB30 miss), which is why "
              "it repeats." % (nxt, left), flush=True)
    return True


def exp_resume_push(conn, outbound, mode, be, args):
    """Serve the STORED EXP on field entry -- the numerator into the per-class
    dword, with exp_max_push's denominator behind it.

    Without this the store held the number and the client never saw it: the
    only 0x1075 bit-2 push was combat_exp_push, i.e. AFTER the next kill, so a
    relog showed 0 EXP until something died. The live bar for the store
    ("relog, EXP still there") could not have passed. Sent only when the
    stored total is non-zero: a fresh character has nothing to resume and the
    field's 0x1075 skill list already went out in this burst.

    2026-09-11, --exp-model level: sent for EVERY character, fresh ones too.
    [unit+0x135C] is the level-up threshold as well as the denominator, and
    the client only asks for a level (0x2059) when it is non-zero -- a fresh
    Lv1 whose "next" was never served could never level.

    WARNING: 2026-09-12: THE DENOMINATOR NO LONGER WAITS FOR THIS CALL. It goes out
    at the top of the burst, before skill_list_push writes the numerator and
    before 0x100E releases the field, because the HUD gauge caches it at
    construction -- see exp_max_push. The call kept here is the backstop for
    --add-self off, and writes the same value onto the same field.
    """
    total = exp_seed(args)
    level_model = level_model_on(args) and skilllist.self_class_id(args) is not None
    if not level_model and total <= 0:
        return False
    nxt = exp_max_push(conn, outbound, mode, be, args, "stored EXP, on entry")
    exp_numerator_push(conn, outbound, mode, be, args, total)
    if level_model:
        cls, level, _into, need, served = exp_view(args)
        print("[feworld]    EXP resumed: class %d Lv%d, %d/%d into the level "
              "(lifetime total %d). The Status line should read 'Exp %d/%d'."
              % (cls, level, served, need, total, served, need), flush=True)
        return True
    num = exp_numerator(args, total)
    print("[feworld]    EXP resumed: stored total %d, so the Status line "
          "should read 'Exp %d/%d' and THE BAR SHOULD BE %.0f%% FULL. If the "
          "screen disagrees with this line, the numerator (the per-class "
          "dword, --exp-display) is what did not land."
          % (total, num, nxt, 100.0 * num / max(1, nxt)), flush=True)
    return True


def exp_numerator_push(conn, outbound, mode, be, args, total):
    """Push the per-class EXP dword (+0x9F8+class*4) -- the FIRST half of the
    Status line's "Exp %d/%d" and what the HUD bar's fill actually tracks.

    It rides 0x1075 bit 1 (the level byte-array), the only wire path to +0x9F8
    (see class_exp_rows). skill_list_push sends it at field entry alongside the
    levels and skills; this is the light per-kill form -- just the four exp
    bytes, no skills, no level rewrite -- so the bar moves on every kill, not
    only on re-entry. Silent when --exp-display is off or there is no class.
    """
    if getattr(args, "exp_display", "on") != "on":
        return False
    rows = skilllist.class_exp_rows(skilllist.self_class_id(args), exp_numerator(args, total))
    if not rows:
        return False
    body, _r, _d = skilllist.skill_list_body([], getattr(args, "skill_list_value", 1), rows)
    wire.send(conn, outbound, wire.inner_msg(0x1075, body, wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    return True


def combat_exp_push(conn, outbound, mode, be, args, gained):
    """EXP -- `0x1075` mask bit 2, one u32 into [unit+0x135c].

    Not a 0x2024 bit. The acquired-skill message 0x1075 (c0 arm 0x0503a423,
    applier 0x05051e20) is mask driven and its bit-2 setter (0x05052200) is
    the only writer of +0x135c reachable from the wire; the applier resolves
    the object with 0x0504d410 and has no kind gate.

    The value is a TOTAL, not a delta -- nothing in the setter adds, so the
    running total has to be kept on our side.

    KEY: AND IT IS NOW KEPT ON THE CHARACTER, not just in the session. Until
    2026-09-08 this docstring ended "it is not persisted to the character store
    yet", which meant every kill was forgotten at the socket close: a player
    could grind all evening and log back in at zero. The session copy stays as
    the working value (this is called per kill, and a store read per kill is
    pointless), but it is SEEDED from the store on the first call and written
    back on every one -- so the wire, the session and the store cannot drift.
    """
    exp_seed(args)
    total = sess._SESSION.get("exp", 0) + int(gained)
    sess._SESSION["exp"] = total
    combat.battle_tally("exp", gained)
    character._store_char_field(args, "exp", total)
    if level_model_on(args) and skilllist.self_class_id(args) is not None:
        return exp_gain_push(conn, outbound, mode, be, args, gained, total)
    nxt = exp_denominator(args, total)
    wire.send(conn, outbound,
         wire.inner_msg(0x1075, struct.pack(">II", 0x2, nxt & 0xFFFFFFFF),
                   wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    # WARNING: 2026-09-11: the BAR NEVER MOVED because this pushed only the
    # DENOMINATOR (+0x135c, bit 2). The NUMERATOR -- the per-class dword at
    # +0x9F8+class*4, the first half of "Exp %d/%d" -- rides bit 1 and was
    # sent ONCE at field entry (skill_list_push). So a kill changed nothing
    # the bar draws (in `auto` the denominator is a constant), and it sat
    # frozen, reading full. Push the numerator here too, every kill.
    exp_numerator_push(conn, outbound, mode, be, args, total)
    print("[feworld]       -> EXP %d (+%d); [unit+0x135c] = %d, so the Status "
          "line should read 'Exp %d/%d' and THE BAR SHOULD BE %.0f%% FULL"
          % (total, gained, nxt, exp_numerator(args, total), nxt,
             100.0 * exp_numerator(args, total) / max(1, nxt)), flush=True)


def exp_gain_push(conn, outbound, mode, be, args, gained, total=None):
    """--exp-model level: add `gained` to the own class's progress and push it
    the way SE's client expects EXP -- ONE 0x1075 with bit 0x2 (the level's
    `next`) and bit 0x4 ([u8 1][u8 class][u32 progress]). In bit order the
    threshold lands first, so the 0x2059 test right after the EXP write
    (0x05052117..0x05052131) compares against the right number; the client
    then floats "GetExp N", prints "Gained N exp" and, if progress >= next,
    ASKS for the level (0x2059 -> feprog). Nothing is levelled here: the
    level moves only on that request, so a lost request costs a level-up
    until the next kill re-asks, never a double one."""
    cls, level, into, need, _served = exp_view(args)
    into += max(0, int(gained))
    class_exp_store(args, cls, into)
    cls, level, into, need, served = exp_view(args)
    body = exp_1075_body(nxt=need, exp_rows=[(cls, served)])
    wire.send(conn, outbound, wire.inner_msg(0x1075, body, wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld]       -> EXP +%d: class %d Lv%d now %d/%d into the level "
          "(lifetime %s) -- 0x1075 mask=0x6. The client should float "
          "'GetExp %d'%s"
          % (gained, cls, level, served, need,
             total if total is not None else "?", gained,
             ", and because %d >= %d it should SEND 0x2059 (level-up request)"
             % (served, need) if served >= need else "."), flush=True)
    return served


def score_seed(args):
    """_SESSION["score"] = the character's stored TOTAL SCORE. Idempotent."""
    if "score" not in sess._SESSION:
        stored = character._load_char_field(args, "total_score", None)
        try:
            sess._SESSION["score"] = int(stored) if stored is not None else 0
        except (TypeError, ValueError):
            sess._SESSION["score"] = 0
        if stored is not None:
            rank, name, _nxt = fegamedata.fame_rank(sess._SESSION["score"])
            print("[feworld]    SCORE: resuming from the stored total %d "
                  "(rank %d, %s)" % (sess._SESSION["score"], rank, name), flush=True)
    return sess._SESSION["score"]


def combat_score_push(conn, outbound, mode, be, args, gained):
    """Add to the character's TOTAL SCORE and re-serve it -- FE's RANK ladder.

    KEY: dat.pak ships fet_fame_rank: eleven ranks keyed by total score, and its
    names are ENGLISH even in the Japanese build (Beginner at 0, Apprentice at
    10k, ... Commander at 12M). +0x500 is the total score the Status window
    draws, and it has been served as a fixed constant, so no player has ever
    ranked up. Now a kill adds to it and it persists on the character, the same
    way EXP does.

    WARNING: THAT THE CLIENT DRAWS ITS RANK TITLE FROM +0x500 VIA THIS LADDER IS NOT
    PROVED -- the ladder is keyed by "a score" and +0x500 is "the total score",
    and joining them is a reading. What IS certain is that a score which never
    moves can never show a rank change; this makes the number move and prints
    the rank it should read, so one look at the Status screen settles it.

    WARNING: It is also NOT proved that FE's score is earned per kill. Retail score
    came out of the war (the war-result window draws Exp and Score separately),
    so --kill-score is OUR rule for making the ladder reachable outside a war,
    and it defaults to the monster's own EXP reward rather than to an invented
    number.
    """
    if int(gained) <= 0:
        return
    score_seed(args)
    total = sess._SESSION.get("score", 0) + int(gained)
    sess._SESSION["score"] = total
    combat.battle_tally("score", gained)
    character._store_char_field(args, "total_score", total)
    rank, name, nxt = fegamedata.fame_rank(total)
    body = struct.pack(">II", 0, wallet._U2024_SCORE[0]) + struct.pack(">I", total & 0xFFFFFFFF)
    wire.send(conn, outbound, wire.inner_msg(0x2024, body, wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld]       -> 0x2024 maskB 0x1 TOTAL SCORE %d (+%d) -> "
          "[unit+0x500]; fet_fame_rank says rank %d %s%s"
          % (total, gained, rank, name,
             ", next at %d" % nxt if nxt else " (top rank)"), flush=True)
