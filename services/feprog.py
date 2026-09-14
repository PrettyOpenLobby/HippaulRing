"""feprog.py -- FE PROGRESSION: level-ups by EXP, skill points, learning,
and the Pw that every skill costs.  PARTIAL: BUILT 2026-09-11 FROM STATIC RE ONLY --
no FE client was run; every claim below is an address, not a screenshot.

The 2006 rules this serves (from 2006-era press, SE's own guides, and JP wiki
captures, researched 2026-09-11):
start at Lv1, cap 40 at launch [4Gamer], levels from EXP, SP from levels,
skills GET!/LVUP for SP [SE guide skill01], no respec, every skill costs Pw
including basic attacks [4Gamer], Pw regenerates by itself and faster while
crouching [RoD-press].  The data half (curve, SP ledger, the 0x1075/0x2024
pushes) lives in feworld's PROGRESSION block so the kill path and the entry
burst can use it without this module; this module owns the MESSAGES.

== 0x2059 -- the LEVEL-UP REQUEST (client -> server, no body) ==============
  sent by the 0x1075 bit-0x4 EXP applier 0x05051F10 at 0x05052127, right
  after it writes [unit+0x9F8+class*4] and floats "GetExp %d", when
  [unit+0x3B4]&0x3F == 0 (not transformed), [unit+0x135C] > 0 and the new
  EXP >= [unit+0x135C].  So the CLIENT decides when to ask; we decide whether
  to grant.  Registers no reply.
  in 0x1091 MSG_CLASS_LEVEL_UP_OK  header-only; arm 0x0503A55F -> 0x0503D800
                                   = `ret`: it does NOTHING on the client
  in 0x1092 MSG_CLASS_LEVEL_UP_NG  [u32 code]; NG table: 1 = max level
  MODEL: grant only what the STORED progress pays for (feworld.
  level_up_apply), answer 0x1091, then push level/next/remainder in ONE 0x1075
  (bits 0x1|0x2|0x4) and the new SP (0x2024 maskA 0x1).  A request the store
  cannot pay for (client and server disagree) is NOT refused -- no NG code
  says "not enough EXP" -- it is answered with a resync push of the true
  next/progress.  At the cap: 0x1092 code 1 (the client's own text).

== 0x2049 -- GET! / LVUP (MSG_ACQUIRE_SKILL_REQUEST) [u16 skillId][u8 1] ====
  builder 0x050D4A8C; registers 0x1078 OK / 0x1079 NG.  LVUP is the SAME
  message: every rank is its own skill id (5,6,7,8,9 = L1..L5), and the
  window sends the NEXT rank's id.
  The window's own SP test (0x050D3A64..0x050D3B8E): cost = [next rank's
  skill+0xF0] (u16, parser 0x051B131D; 2 for every rank 1..5 -- shipped,
  fe-fet-skill.tsv `sp_cost`) vs [unit+0x498]; greyed when cost > SP.
  in 0x1078 OK   header-only; arm 0x050534F7 -> 0x050D4CF0: prints the
                 "learned" line, REBUILDS the rows from [unit+0xA14] and runs
                 the palette re-scan.  KEY: NOTHING in the client marks the
                 skill owned: the only writer of [unit+0xA14+id*2] reachable
                 at runtime is 0x1075 bit 0x8 (0x0507D600, the local adder,
                 has no caller).  So the 0x1075 goes out BEFORE the 0x1078.
  in 0x1079 NG   [u32 code]: 1 already learned, 2 conditions not met (used
                 for not enough SP), 4 not a registered learnable skill.
  NOT validated server-side (logged only): prerequisites and the class tree.
  SKILL_LEARN_DATA is a partial table (fefet.py: skills 0..38 of 734) and the
  window already hides rows whose prerequisite is not owned (0x050D50C1).

== 0x2028 -- the Pw REGEN ASK  [u32 mask] + per set bit [u8 ticks] ==========
  builder 0x0507D810 (a unit method): skipped when CONDITION has DEAD|GHOST
  (0x1800000); the interval is 3000 ms, or 1349 ms when OBJFLAG ([unit+0x40])
  has 0x400 = CROUCH (0x0507D84B) -- THE CLIENT'S OWN "faster while
  crouching"; bit 0x10 when [unit+0x4A4] < [unit+0x4A0]+[unit+0x4A2] and the
  unit is not attacking (OBJFLAG 0x10); bit 0x800 when [unit+0x4BC] > 0;
  ticks = elapsed / interval, one byte per set bit.  Registers no reply.
  MODEL: bit 0x10 -> Pw += ticks * --pw-regen (16, SOURCED 2026-09-12:
  fewiki 2006 「16Pow/3〜4秒」; the client holds no amount), capped at
  --pw-max, pushed as 0x2024 maskA 0x10.
  With a flat per-tick amount the crouch rate is 3000/1349 = 2.2x -- the
  CLIENT sets the ratio.  Bit 0x800 (the knockback gauge) is logged only.

== 0x1031 / 0x1032 -- a SKILL USE, announced once per cast ==================
  the cast commit (0x0507FC40 -> 0x0504B580 -> 0x0504A8F0) builds a skill
  ACTION object; action types 0/1 (jump table 0x0504B564) are the ctor
  0x05041900 class, whose vtable+8 (0x050423F0, and the type-0 subclass's
  0x05042DA0) sends 0x1031 -- 0x1032 when [skill+0x100] != 0 -- and then sets
  its own "sent" bit ([action+0x48] |= 4).  Items take the type-0 branch with
  flag 0x2000 and send 0x2051 MSG_USE_ITEM instead.  Body (0x050423F0):
      [u32 +0x40][u32 +0x44][u8][u32 +0x7C][u32 +0x78][u16 SKILL][u8 1]
      [u32 +0x80][u32 +0x84] then, when +0x80 == 0, [f32 x][f32 y][f32 z]
  so the skill id is the u16 at body offset 17.
  KEY: THE CLIENT NEVER DEBITS Pw.  Gate 5 is a READ (0x05065DC0:
  [unit+0x4A4] >= [skill+0xF4], "Not enough Pow to use skill." from
  0x0507F767); the only writers of +0x4A4 are the entry copies, the class
  table init, the 0x1006 stat block and the 0x2024 setter -- no subtract
  anywhere in the image.  So a server debit cannot double-count.
  MODEL (--pw-cost cast, default): debit [skill+0xF4] (fe-fet-skill.tsv
  `pow_cost`) on each 0x1031/0x1032 and push the new Pw.  A second message
  for the same skill inside --pw-dedupe-ms is not charged again (types 2/3
  build other action classes; whether any of them re-announces is unread).

== what a LIVE probe must answer (none of this has been on a screen) ======
  1. kill a monster: the log's "0x1075 mask=0x6" line, the client's floating
     "GetExp N" and "Gained N exp" -- then, once the bar passes `next`, does
     feworld log "<- 0x2059"?  If the popup shows but 0x2059 never comes, the
     test at 0x05052117 is refusing ([unit+0x135C] or the transform byte).
  2. after 0x2059: does "Lv." change on the HUD/Status (bit 0x1 row) and the
     bar empty (the widget's own reset at 0x050CE33A)?
  3. cast any skill: does "<- 0x1031" (or 0x1032) arrive once per cast?  If
     it never arrives, Pw is never charged -- set --pw-cost off and report.
  4. stand still below max Pw, then crouch: does 0x2028 arrive every ~3 s and
     every ~1.3 s crouched, and does the gauge climb?
  5. GET! a skill: SP drops by 2 in the window, the row shows as learned.

Knobs (read with getattr -- the harness builds args by hand; defaults are
feworld's DEFAULT_* constants so there is one default per setting):
  --exp-model level|flat   --exp-curve rod|auto|flat:N|N1,N2,... (rod)
  --class-levelup ok|ng|off (ok)   --class-level-max N (40)
  --sp-per-level SCHEDULE|off (1:1-40, RoD; FEZ-era 2:1-5,1:6-35,0:36-40)
  --skill-learn sp|legacy (sp)
  --pw-cost cast|off (cast)   --pw-regen N (16, fewiki 2006)   --pw-max N (100)
  --pw-dedupe-ms MS (250)   --pw-regen-fallback-ms MS (6000, a safety net)
!verbs: !levelup [N]  !setlevel N  !sp  !pw [N]
"""
import struct
import sys
import time

import fegamedata

fw = None   # the feworld module, handed in by register()

NAMES = {
    0x2059: "MSG_CLASS_LEVEL_UP_REQUEST (chosen) no body; sent by the 0x1075 "
            "EXP applier 0x05052127 when exp >= [unit+0x135c]",
    0x2028: "Pw/knockback REGEN ASK (unnamed) [u32 mask] + [u8 ticks] per set "
            "bit (0x0507d810; 3000 ms, 1349 ms crouched)",
    0x1031: "SKILL USE, announced once per cast [u32][u32][u8][u32][u32]"
            "[u16 skill][u8 1][u32][u32](+xyz) (0x050423f0)",
    0x1032: "SKILL USE sibling of 0x1031 ([skill+0x100] != 0)",
}
PUSH_NAMES = {
    0x1091: "MSG_CLASS_LEVEL_UP_OK (header-only; arm 0x0503d800 is `ret`)",
    0x1092: "MSG_CLASS_LEVEL_UP_NG [u32 code] (1 = max level)",
    0x1078: "MSG_ACQUIRE_SKILL_OK (header-only; arm 0x050534f7 rebuilds the window)",
    0x1079: "MSG_ACQUIRE_SKILL_NG [u32 code]",
}

LEVELUP_NG_UNDEFINED, LEVELUP_NG_MAX = 0, 1
ACQ_NG_UNDEFINED, ACQ_NG_KNOWN, ACQ_NG_CONDITIONS, ACQ_NG_NOT_LEARNABLE = 0, 1, 2, 4
REGEN_PW_BIT, REGEN_KNOCK_BIT = 0x10, 0x800


def register(feworld):
    global fw
    fw = feworld
    fw.register_handler(0x2059, on_level_up)
    # 0x2049 has a hard-coded arm in feworld (persist + 0x1078). This one
    # shadows it ON PURPOSE to charge SP and push the owned flag; it returns
    # False (decline -> the builtin answers) under --skill-learn legacy or an
    # --acquire-skill other than ok.
    fw.register_handler(0x2049, on_acquire, override=True)
    fw.register_handler(0x2028, on_regen)
    fw.register_handler(0x1031, on_cast)
    fw.register_handler(0x1032, on_cast)
    fw.register_pump(pump)
    fw.register_gm(gm)
    fw.register_args(add_args)
    for mid, name in NAMES.items():
        fw.KNOWN.setdefault(mid, name)


def add_args(ap):
    ap.add_argument("--level-fallback-ms", type=int, default=5000, metavar="MS",
                    help="how long an unclaimed level-up waits for the client "
                         "to ask (0x2059) before --level-fallback takes it. "
                         "WARNING: not 0: the pump runs on every inbound message, so "
                         "no delay steals the level-up off the client and its "
                         "0x1091 grant stops happening (fe_prog_test caught "
                         "that). 0 = take it immediately")
    ap.add_argument("--level-fallback", default="on", choices=("on", "off"),
                    help="take a level SERVER-SIDE when the stored EXP covers "
                         "it and the client has not asked (0x2059). KEY: this is "
                         "why the HUD EXP bar reads 100%%: the bar is "
                         "progress/need clamped (0x050CE2FB reads the "
                         "per-class EXP, 0x050CE346 the threshold, the gauge "
                         "clamps to its max), --exp-curve rod makes Lv1 need "
                         "30 and one kill pays 100+, and nothing reset the "
                         "overflow unless the client asked. off = the "
                         "pre-2026-09-12 behaviour")
    ap.add_argument("--exp-model", default=fw.DEFAULT_EXP_MODEL,
                    choices=["level", "flat"],
                    help="level (default) = EXP fills the own class's level "
                         "and the client ASKS for the level-up (0x2059) when "
                         "it passes [unit+0x135c]; flat = the 2026-09-09 "
                         "display-only bar of --exp-per-level, no levels")
    ap.add_argument("--exp-curve", default=fw.DEFAULT_EXP_CURVE,
                    help="EXP from level L to L+1. rod (default) = the RoD "
                         "table of the 2006 fan wiki (fewiki Guide/メモ, "
                         "labelled 旧仕様): 30 at Lv1, 350 at Lv10, 7,000 at "
                         "Lv20, 150,000 at Lv30, 3,200,000 at Lv40; auto = "
                         "16*L*(L+5) (OURS, the curve it replaced); flat:N; "
                         "or N1,N2,... (last repeats)")
    ap.add_argument("--class-levelup", default=fw.DEFAULT_CLASS_LEVELUP,
                    choices=["ok", "ng", "off"],
                    help="0x2059: ok = grant what the stored EXP pays for, "
                         "0x1091 + the new level/next/SP; ng = 0x1092 code 1; "
                         "off = log only")
    ap.add_argument("--class-level-max", type=int,
                    default=fw.DEFAULT_CLASS_LEVEL_MAX,
                    help="the level cap (2006 launch: 40)")
    ap.add_argument("--sp-per-level", default=fw.DEFAULT_SP_PER_LEVEL,
                    metavar="PER:LO-HI,...|off",
                    help="skill points granted per level. Default 1:1-40 = "
                         "RoD's 1 per level, 40 at Lv40 (fewiki Guide/メモ "
                         "2006-05-26; the 2006-06 warrior page's 22+18 SP = "
                         "40). The FEZ-era schedule is 2:1-5,1:6-35,0:36-40 "
                         "(fewiki 2007-02 / FFSKY). The COST of a rank is "
                         "shipped ([skill+0xF0], 2 every rank -- the RoD "
                         "wiki's 2-4/1-2 per rank belongs to a different "
                         "skill tree than this client ships). `off` = the "
                         "old flat --add-stats 0:N budget")
    ap.add_argument("--skill-learn", default="sp", choices=["sp", "legacy"],
                    help="0x2049 GET!/LVUP: sp = charge the rank's shipped SP "
                         "cost, refuse when short (0x1079 code 2), push the "
                         "owned flag and the new SP before 0x1078; legacy = "
                         "feworld's builtin arm (persist + OK, no charge)")
    ap.add_argument("--pw-cost", default=fw.DEFAULT_PW_COST,
                    choices=["cast", "off"],
                    help="cast = debit [skill+0xF4] (pow_cost) on each "
                         "0x1031/0x1032 and push [unit+0x4A4] (the client "
                         "never debits it itself -- proven statically); off = "
                         "skills are free (the old behaviour)")
    ap.add_argument("--pw-regen", type=int, default=fw.DEFAULT_PW_REGEN,
                    help="Pw per 0x2028 tick (16 = fewiki 2006 / ffsky "
                         "'16 Pw per 3 s'; the client holds no amount). The "
                         "client asks every "
                         "3000 ms, every 1349 ms crouched, so crouching is "
                         "2.2x faster by the client's own timing. 0 = no regen")
    ap.add_argument("--pw-max", type=int, default=fw.DEFAULT_PW_MAX,
                    help="the server's Pw ceiling -- the class table's own "
                         "100 (2006: Pw 100 at every level)")
    ap.add_argument("--pw-dedupe-ms", type=int, default=250,
                    help="a second 0x1031/0x1032 for the SAME skill inside "
                         "this window is not charged again (OURS)")
    ap.add_argument("--pw-regen-fallback-ms", type=int, default=6000,
                    help="SAFETY NET (ours): if Pw is below max and no 0x2028 "
                         "has arrived for this long, regenerate server-side "
                         "at the standing rate (one tick per 3000 ms) -- so a "
                         "wrong reading of the regen ask cannot strand a "
                         "player at 0 Pw. Idle whenever 0x2028 flows. 0 = off")


def _p(msg):
    print("[feprog] " + msg, flush=True)


def _send_1075(ctx, body):
    fw.send(ctx.conn, ctx.outbound, fw.inner_msg(0x1075, body, fw.unit_id_of(ctx.args)),
            ctx.mode, ctx.be, ctx.args.seq_mode == "echo",
            getattr(ctx.args, "world_prefix", 4))


# --- LEVEL-UP -----------------------------------------------------------------
def on_level_up(ctx, inner):
    a = ctx.args
    mode = getattr(a, "class_levelup", fw.DEFAULT_CLASS_LEVELUP)
    cls, level, into, need, served = fw.exp_view(a)
    _p("<- 0x2059 LEVEL-UP REQUEST (no body) -- class %s Lv%d, stored %d/%d"
       % (cls, level, into, need))
    if mode == "off":
        _p("    --class-levelup off: logged only")
        return
    if mode == "ng":
        ctx.reply(0x1092, struct.pack(">I", LEVELUP_NG_MAX),
                  why="MSG_CLASS_LEVEL_UP_NG code=1 (--class-levelup ng)")
        return
    code, old, new = fw.level_up_apply(a, 1, need_exp=True)
    if code == 1:
        ctx.reply(0x1092, struct.pack(">I", LEVELUP_NG_MAX),
                  why="MSG_CLASS_LEVEL_UP_NG code=1 max level (%d)"
                      % fw.class_level_cap(a))
        fw.level_state_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a,
                            "resync at the cap")
        return
    if code == 2:
        # the client thinks it has passed `next` and the store says no --
        # there is no "not enough EXP" code, so put the true pair back
        _send_1075(ctx, fw.exp_1075_body(nxt=need, exp_rows=[(cls, served)]))
        _p("    NOT granted: stored progress %d < next %d -- resynced the "
           "client (0x1075 mask=0x6), no reply" % (into, need))
        return
    if code is not None:
        ctx.reply(0x1092, struct.pack(">I", LEVELUP_NG_UNDEFINED),
                  why="MSG_CLASS_LEVEL_UP_NG code=0 (%s)"
                      % ("no class / no stored table" if code == 0
                         else "the store refused"))
        return
    ctx.reply(0x1091, b"", why="MSG_CLASS_LEVEL_UP_OK (header-only; 0x0503d800 "
                               "is `ret` -- the 0x1075 below is what shows)")
    fw.level_state_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a,
                        "0x2059 granted Lv%d -> Lv%d" % (old, new))


def force_level(ctx, levels=None, to=None, why="!levelup"):
    """GM: +N levels (progress kept) or exactly `to` (progress reset)."""
    a = ctx.args
    cls = fw.self_class_id(a)
    if cls is None:
        _p("    %s: no stored character/class" % why)
        return None
    if to is not None:
        cap = fw.class_level_cap(a)
        to = max(1, min(int(to), cap))
        rows, _bad = fw.stored_class_levels(a, cls)
        table = dict((int(i), int(v)) for i, v in rows)
        table[int(cls)] = to
        fw._store_char_field(a, "class_levels",
                             {str(i): v for i, v in sorted(table.items())})
        fw.class_exp_store(a, cls, 0)
        new = to
    else:
        code, _old, new = fw.level_up_apply(a, levels or 1, need_exp=False)
        if code is not None:
            _p("    %s: not applied (code %s)" % (why, code))
            fw.level_state_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a, why)
            return None
    fw.level_state_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a, why)
    return new


# --- GET! / LVUP -------------------------------------------------------------
def on_acquire(ctx, inner):
    a = ctx.args
    if (getattr(a, "skill_learn", "sp") != "sp"
            or getattr(a, "acquire_skill", "ok") != "ok"):
        return False                      # feworld's builtin arm answers
    f = inner[2:]
    if len(f) < 2:
        return False
    sid = struct.unpack_from(">H", f, 0)[0]
    s = fegamedata.skills().get(sid)
    cost = fw.skill_sp_cost(sid)
    have = fw.skill_points(a, (getattr(a, "add_stats", None) or {}).get(0))
    _p("<- 0x2049 GET!/LVUP skill %d %s -- costs %d SP ([skill+0xF0]), "
       "character has %d" % (sid, (s or {}).get("name", "?"), cost, have))

    def ng(code, why):
        ctx.reply(0x1079, struct.pack(">I", code),
                  why="MSG_ACQUIRE_SKILL_NG code=%d (%s) for skill %d"
                      % (code, why, sid))

    if s is None or not cost:
        return ng(ACQ_NG_NOT_LEARNABLE, "not a learnable rank")
    served = fw.served_skills(a)
    if sid in served:
        return ng(ACQ_NG_KNOWN, "already owned")
    if have < cost:
        return ng(ACQ_NG_CONDITIONS, "not enough SP: %d < %d" % (have, cost))
    if s.get("rank", 1) > 1 and not any(
            o.get("family") == s.get("family") and o.get("rank") == s["rank"] - 1
            and oid in served for oid, o in fegamedata.skills().items()):
        _p("    WARNING: LVUP to rank %d without rank %d owned -- allowed (the "
           "window decides what it offers), logged" % (s["rank"], s["rank"] - 1))
    stored, rows = fw.acquire_skill(a, sid)
    if stored is not True:
        return ng(ACQ_NG_UNDEFINED, "the store did not take it")
    # the owned flag FIRST: 0x1078's arm rebuilds the rows from [unit+0xA14]
    _send_1075(ctx, fw.exp_1075_body(skills=[sid],
                                     value=getattr(a, "skill_list_value", 1)))
    sp = fw.sp_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a)
    ctx.reply(0x1078, b"", why="MSG_ACQUIRE_SKILL_OK for skill %d -- stored "
                               "(%d now), SP %s" % (sid, len(rows), sp))
    if getattr(a, "status_resync", "on") == "on":
        if getattr(a, "char_record", "off") != "off":
            fw.char_record_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a)
        if getattr(a, "status", "off") != "off":
            fw.status_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a)
    return None


# --- Pw ------------------------------------------------------------------------
def decode_cast(f):
    """(skill, text) for a client 0x1031/0x1032 body (layout: docstring)."""
    if len(f) < 28:
        return None, "short body %d: %s" % (len(f), f.hex())
    a, b, flag, t1, t2, skill, one, c, d = struct.unpack_from(">IIBIIHBII", f, 0)
    txt = ("a=%d b=%d u8=%d %d/%d skill=%d u8=%d c=%d d=%d"
           % (a, b, flag, t1, t2, skill, one, c, d))
    if c == 0 and len(f) >= 40:
        txt += " pos=(%.2f,%.2f,%.2f)" % struct.unpack_from(">fff", f, 28)
    return skill, txt


def on_cast(ctx, inner):
    a = ctx.args
    mid = struct.unpack_from(">H", inner, 0)[0]
    skill, txt = decode_cast(inner[2:])
    _p("<- 0x%04X SKILL USE :: %s" % (mid, txt))
    # the peers see the cast: fepresence forwards the record verbatim under
    # this player's charid (the client has an inbound arm for 0x1031/0x1032,
    # 0x0503A0E1/0x0503A0FD -> 0x503BB70, reading the same layout)
    pres = sys.modules.get("fepresence")
    if pres is not None and hasattr(pres, "relay_inner"):
        try:
            pres.relay_inner(ctx, inner)
        except Exception as e:                          # noqa: BLE001
            _p("    presence cast relay failed (%r) -- the cast still counts" % (e,))
    # ...and player damage is settled from it (the victim's client never
    # reports a hit on an avatar -- fepvp.on_cast)
    pvp = sys.modules.get("fepvp")
    if pvp is not None and hasattr(pvp, "on_cast"):
        try:
            pvp.on_cast(ctx, inner)
        except Exception as e:                          # noqa: BLE001
            _p("    pvp cast path failed (%r) -- the cast still counts" % (e,))
    if skill is None or getattr(a, "pw_cost", fw.DEFAULT_PW_COST) != "cast":
        return
    cost = fegamedata.skills().get(skill, {}).get("pow", 0)
    if cost <= 0:
        return
    now = time.monotonic()
    last = ctx.session.setdefault("pw_cast_seen", {})
    win = max(0, int(getattr(a, "pw_dedupe_ms", 250) or 0)) / 1000.0
    if now - last.get(skill, -1e9) < win:
        _p("    same skill %d again inside %d ms -- not charged twice"
           % (skill, win * 1000))
        return
    last[skill] = now
    before = fw.pw_now(a)
    if before >= fw.pw_max(a):
        ctx.session["pw_tick_at"] = now   # the regen clock starts with the spend
    fw.pw_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a, before - cost,
               "skill %d costs %d ([skill+0xF4]): %d -> %d"
               % (skill, cost, before, max(0, before - cost)))


def on_regen(ctx, inner):
    a = ctx.args
    f = inner[2:]
    if len(f) < 4:
        return
    mask = struct.unpack_from(">I", f, 0)[0]
    bits = [i for i in range(32) if mask >> i & 1]
    ticks = {1 << b: (f[4 + n] if 4 + n < len(f) else 0) for n, b in enumerate(bits)}
    regen = int(getattr(a, "pw_regen", fw.DEFAULT_PW_REGEN) or 0)
    if mask & REGEN_PW_BIT:
        ctx.session["pw_tick_at"] = time.monotonic()
        ctx.session["pw_ask_seen"] = True
    if mask & REGEN_PW_BIT and regen > 0:
        before = fw.pw_now(a)
        after = min(fw.pw_max(a), before + ticks[REGEN_PW_BIT] * regen)
        if after != before:
            fw.pw_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a, after)
        n = ctx.session["pw_regen_n"] = ctx.session.get("pw_regen_n", 0) + 1
        if n <= 3 or n % 20 == 0:
            _p("<- 0x2028 regen ask #%d mask=0x%X ticks=%s -> Pw %d -> %d "
               "(+%d/tick; the client asks every 3000 ms, 1349 ms crouched)"
               % (n, mask, ticks[REGEN_PW_BIT], before, after, regen))
    elif mask & ~REGEN_PW_BIT:
        _p("<- 0x2028 mask=0x%X ticks=%s -- bit 0x800 (knockback gauge "
           "[unit+0x4BC]) is not modelled" % (mask, sorted(ticks.values())))


def level_fallback(ctx, now=None):
    """THE LEVEL-UP SAFETY NET -- and the reason the EXP BAR IS ALWAYS FULL.

    WARNING: 2026-09-12, the fourth report of "the exp bar in the UI still always
    shows 100%". The three previous fixes (split the two halves 09-09,
    progress-within-level the same day, push the numerator per kill)
    all targeted the right fields -- confirmed by RE this time rather than
    assumed: ONE client function reads both, `mov ebx,[edi+eax*4+0x9f8]`
    (the per-class EXP) at 0x050CE2FB and `mov edx,[edi+0x135c]` (the
    threshold) at 0x050CE346, and it clamps the gauge's current
    (`[gauge+0x7c]`) to its max (`[gauge+0x80]`, copied from +0x135c at
    0x050CCA9D). So the bar is `progress / need`, clamped.

    KEY: WHICH MEANS A FULL BAR IS `progress >= need`, AND THAT IS EXACTLY WHAT
    WE SERVE. Under `--exp-curve rod` Lv1 needs **30** EXP and one kill pays
    100+. `exp_view` clamps the served progress to need-1 only AT THE CAP, so
    below the cap the overflow goes out as-is and the client pins the gauge.
    The only thing that ever resets it is a LEVEL-UP -- and nothing here
    levelled anybody unless the client sent 0x2059. feprog's own module
    docstring names that risk ("if the popup shows but 0x2059 never comes")
    and left it as a question for a live run; the live run has now said it
    four times.

    So: when the stored progress covers the next level and the level is below
    the cap, take the level SERVER-SIDE, exactly as the 0x2059 handler would
    (level_up_apply pays for it out of progress). One level per pump, which is
    self-limiting and matches what the client does when it asks -- and if
    0x2059 does arrive, it gets there first and this finds nothing owed.

    The same shape as the Pw regen net below it: a safety net for a client
    message that may never come. --level-fallback off restores the old
    behaviour."""
    a = ctx.args
    if str(getattr(a, "level_fallback", "on") or "off") != "on":
        return
    if not fw.level_model_on(a) or not ctx.session.get("in_field"):
        return
    try:
        cls, level, into, need, _served = fw.exp_view(a)
    except Exception:                                  # noqa: BLE001
        return
    if cls is None or need <= 0 or into < need:
        ctx.session.pop("level_owed_at", None)
        return
    if level >= fw.class_level_cap(a):
        ctx.session.pop("level_owed_at", None)
        return
    # WARNING: GIVE THE CLIENT ITS TURN FIRST. The pump runs on EVERY inbound
    # message, so a fallback with no delay fires before the client's next
    # 0x2059 and TAKES THE LEVEL-UP OFF IT -- the 0x1091 grant, and whatever
    # the client does with it, stop happening. fe_prog_test caught exactly
    # that. So the overflow has to sit unclaimed for --level-fallback-ms
    # before we step in, the same way the Pw net below waits out 0x2028.
    ms = int(getattr(a, "level_fallback_ms", 5000) or 0)
    now = time.monotonic() if now is None else now
    owed = ctx.session.setdefault("level_owed_at", now)
    if ms > 0 and now - owed < ms / 1000.0:
        return
    ctx.session.pop("level_owed_at", None)
    code, old, new = fw.level_up_apply(a, 1, need_exp=True)
    if code is not None or new <= old:
        return
    _p("LEVEL-UP SAFETY NET: class %d had %d/%d -- the client never asked "
       "(0x2059), so the level was taken here: Lv%d -> Lv%d. An un-levelled "
       "overflow is what pins the HUD bar at 100%%." % (cls, into, need, old, new))
    try:
        fw.level_state_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a,
                            "level fallback Lv%d -> Lv%d" % (old, new))
    except Exception as e:                             # noqa: BLE001
        _p("   level re-serve failed: %s" % e)


def pump(ctx, now=None):
    """The regen SAFETY NET (--pw-regen-fallback-ms), and the LEVEL-UP one.
    Runs once per inbound message; does nothing while 0x2028 keeps arriving
    (each one restamps pw_tick_at), and nothing at full Pw."""
    level_fallback(ctx, now)
    a = ctx.args
    s = ctx.session
    ms = int(getattr(a, "pw_regen_fallback_ms", 6000) or 0)
    regen = int(getattr(a, "pw_regen", fw.DEFAULT_PW_REGEN) or 0)
    now = time.monotonic() if now is None else now
    if (ms <= 0 or regen <= 0 or not s.get("in_field")
            or getattr(a, "pw_cost", fw.DEFAULT_PW_COST) == "off"
            or "pw" not in s):
        return
    if fw.pw_now(a) >= fw.pw_max(a):
        s["pw_tick_at"] = now     # full: the regen clock is not running
        return
    since = s.setdefault("pw_tick_at", now)
    if now - since < ms / 1000.0:
        return
    ticks = int((now - since) / 3.0)
    s["pw_tick_at"] = since + ticks * 3.0
    before = fw.pw_now(a)
    after = fw.pw_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a,
                       before + ticks * regen)
    n = s["pw_fallback_n"] = s.get("pw_fallback_n", 0) + 1
    if n <= 3 or n % 20 == 0:
        _p("    Pw FALLBACK regen #%d: %s for %.1f s -> %d tick(s), Pw %d -> %d"
           " (the client's own ask never came -- report this; see probe 4)"
           % (n, "no 0x2028 at all" if not s.get("pw_ask_seen")
              else "no 0x2028", now - since, ticks, before, after))


# --- !verbs ---------------------------------------------------------------------
def gm(ctx, line):
    words = line.strip().split()
    if not words:
        return False
    verb = words[0].lower()
    try:
        n = int(words[1], 0) if len(words) > 1 else None
    except ValueError:
        n = None
    if verb == "!levelup":
        force_level(ctx, levels=n or 1, why="!levelup %d" % (n or 1))
        return True
    if verb == "!setlevel":
        if n is None:
            _p("    !setlevel N -- N required")
            return True
        force_level(ctx, to=n, why="!setlevel %d" % n)
        return True
    if verb == "!sp":
        a = ctx.args
        sched = fw.sp_schedule(a)
        lv = fw.self_level(a)
        _p("    SP: Lv%d earned %s, stored skills cost %d, left %d; pushed %s"
           % (lv, fw.sp_earned(sched, lv) if sched is not None else "(off)",
              fw.sp_spent(a), fw.skill_points(a, (getattr(a, "add_stats", None)
                                                  or {}).get(0)),
              fw.sp_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a)))
        return True
    if verb == "!pw":
        a = ctx.args
        fw.pw_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, a,
                   fw.pw_max(a) if n is None else n, "!pw")
        return True
    return False
