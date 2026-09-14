"""feunit.py -- UNIT STATE: stat setters, profile/metamorphosis, ghost, invincible,
batch delete, item durability, building HP, crystal heal.  PARTIAL: BUILT, NOT LIVE.

Fantasy Earth extension module (see THE EXTENSION SEAM in feworld.py). Every
wire shape below was read off the client dump (FE_Client.dll @ 0x04F90000,
static disassembly, 2026-09-10); every SCREEN claim names the draw site
it came from; every label that is not the client's own says "invented".
Nothing here has been seen on a screen: PARTIAL: on the whole file.

====================================================================
1. THE 0x2026 SETTER FAMILY -- 19 fields, not 12
====================================================================
The 08-27 sweep counted "12 setters 0x4ff5810..0x4ff5b30". Opening the applier
0x04ff5030 end to end: it reads ONE u32 mask (0x5045e60 at 04ff503d) and tests
bits 0x1..0x40000 in order -- NINETEEN setters, 0x4ff5810 .. 0x4ff5da0, plus a
twentieth call that is not a setter:

    04ff51c7  test dword [esp+0x10], 0x7e0    ; any of bits 5..10 set?
    04ff51d1  call 0x4ffeae0(unit)            ; re-evaluates worn slots 0 and 1
                                              ; (0x507bad0 -> 0x504cb40 kind 0xbb8,
                                              ; category [item+0x1c8] <= 10)

Every setter was opened (dis-setters.txt in the session scratch): all nineteen
have the SAME body -- reader 0x5045ec0 (i16; `add edx,2` on [0x5338360]),
object by id 0x504cb30 -> 0x504d350, gate 0x4fcf8d0 == 3 (KIND-B 3 = the
PLAYER; a monster or building silently ignores the whole message), then

    mov word ptr [esi + edx*2 + BASE], ax     ; edx = the idx argument (0/1)

so each field is a PAIR of i16 columns 4 bytes apart, column 0 at BASE and
column 1 at BASE+2 -- except bit 0x20000 (0x4ff5d60), which takes no idx and
writes the lone word +0x558. The four entry points differ only in the column
and in where the object id comes from:

    0x2026  arm 0503a6eb -> 0x4ff5030(HEADER unit id, stream)   column 0
    0x2027  arm 0503a705 -> 0x4ff51e0(HEADER unit id, stream)   column 1
    0x1183  arm 0503a5e8 -> 0x4ff5390(stream)   [u16 N] x {[u32 obj][u32 mask]..}  col 0
    0x1184  arm 0503a5d3 -> 0x4ff55d0(stream)   same shape                          col 1

WARNING: The sweep's "0x1183/0x1184 are gated on a u16 that must match a unit field"
is WRONG: `xor edi,edi / cmp word [esp+0xc], di / jbe end` at 04ff53a7 is
"count > 0", and the loop tail (04ff55ad: inc edi; cmp edi, count; jl) walks N
records, each carrying its own object id (the setters' first argument is that
u32, not the header). So 0x1183/0x1184 are the BATCH form for many players.

WHICH FIELD IS WHICH ON SCREEN (the Status window, draw block 0x050e1380..):
  * row 攻撃力 (label 0x052e2a18 via the layout row at 0x052e29f8):
        class [+0x3ab] in {0,1,3,6}:  +0x4ae + +0x4ac + +0x514 + +0x516
        any other class:              +0x4b2 + +0x4b0 + +0x518 + +0x51a
    (0x050e14c3..0x050e1519). So +0x514/+0x516 and +0x518/+0x51a are the two
    ATTACK addends, one per class group; +0x4ac/+0x4b0 are the 0x2024 bits
    0x80/0x100 feworld already serves.
  * row 耐性 (label 0x052dfb40 via 0x052e2a08): (+0x540 + +0x542 + +0x544 +
    ... + +0x556, all twelve words) * 6 -- `lea eax,[eax+eax*2]; shl eax,1`
    at 0x050e15ba. Six pairs, +0x540..+0x554: the RESISTANCE group.
  * a second draw block, 0x050e7473..0x050e7608, draws +0x528..+0x53e (six
    pairs, col0+col1 each, then col0 alone) and +0x540..+0x556 the same way.
    Which window that is has not been opened; the six-pair shape and the
    worn-weapon re-evaluation on bits 5..10 are why the label says ATTACK
    ELEMENT. That label is INVENTED.
  * +0x51c/+0x520/+0x524: written by the class-table copy at 0x0506a6f0..
    and by nothing else that draws. UNNAMED. `!statprobe` exists to name them
    from a screenshot.
  * +0x558: the ctor stores dword 0x64e80064 there (0x0507a40f), i.e. +0x558 =
    100 and +0x55a = 0x64e8; no reader outside the setters. UNNAMED.

====================================================================
2. 0x203D -- the UNIT PROFILE record (and the METAMORPHOSIS lever)
====================================================================
arm 0503a683 -> 0x4feb980(HEADER unit id, stream). Kind-3 lookup by the header
id (04feb9a9), then [u32 mask] and per bit, in test order:
    0x1     cstr (0x5045f90) -> unit+0x389 name       0x2     u8  -> +0x3aa sex
    0x4     u8  -> +0x3ab look1/class                0x8     u32 -> +0x3b0 f24
    0x10    u16 (0x5045e30) -> +0x3b4 RACE/FORM, then 0x506c7d0(form) rebuilds
            the model and 0x506c9f0(form) -- THIS IS THE TRANSFORMATION PUSH
    0x20/0x40/0x80  u8 -> +0x3b6/+0x3b7/+0x3b8 look2/3/4
    0x100   u8 (0x5045e90) -> +0x94 army           0x200   u32 -> +0x3bc force
    0x400   u32 -> +0x3c0                          0x800   32 raw bytes (0x5045b10)
    0x4000  u32 -> +0x3d4 (bit 0 = the GM invincible flag, see 4.)
    0x8000  cstr -> +0x3d8 profile comment         0x10000 u8 -> +0x3c4
(bits 0x1000/0x2000 are not tested.) The same offsets feworld's _AVATAR_SUB
carries for the self record -- two decodes agree. The form word is ONE-HOT in
its low 6 bits and the bit index keys METAMORPHOSIS_DATA (fedata
fe-fet-metamorphosis.tsv, ids 0..5 = GIANT DRAGON CHIMAIRA KNIGHT WRAITH
FLYNGSHIP); the client's own RACE bit names are GIA DRA CHIM KNIT WRTH FSHIP
at 0x001..0x020, and 0x200 HUM is the untransformed human.

====================================================================
3. METAMORPHOSIS = THE SUMMON: 0x2044 -> 0x106A/0x106B, 0x2045 -> 0x106C, 0x1070
====================================================================
(Re-read end to end 2026-09-11 off the dump; PARTIAL: NOTHING of it has been
on a screen. The 2006 rules that ride on it are in section 7.)

THE CLIENT'S HALF, in the order a player meets it:
  * TARGET MENU (0x050e2b9f): target a BUILDING ([menu+0xa4] == 2) and press
    the 'Summ' button. Gates, all client-side: the player's RACE word
    [+0x3b4] has HUM 0x200 (the button reads 'Unsummon' instead when it does
    not, label pick at 0x050e3247); the building's state byte [+0x38] == 1
    (0x5071e20) and != 2 (0x5071e30); distance player->building <= 35.0
    (float 0x5261cbc) else the client prints "Too far to summon." itself
    (0x52e2bec) and sends NOTHING. Then it opens the 'Summon Window'.
    ("Target is too close!" 0x052dee2c is a skill-target line, not a summon
    gate.)
  * SUMMON WINDOW (ctor 0x050e1a90, arg = the building's TYPE and object):
    row = FE_BUILDING_DATA[type]; name from METAMORPHOSIS_DATA[row+0xe2];
    cost = the 0x1070 map (0x53458b8+0, operator[] 0x5126ed0) KEYED BY
    row+0xe2: [0] GOLD -> [win+0x70], [4] CRYSTAL -> [win+0x74], drawn as
    'Crystal use %4d'. OK is enabled only while CRYSTAL [+0x8ac] >= [win+0x74]
    and GOLD [+0x4ec] >= [win+0x70] (0x050e2280) -- no 0x1070 = cost 0/0.
  * OK -> 0x2044, builder 0x05071230 (`this` = the building):
        gate: [this+0x780] & 1 == 0 (no request pending)
        registers replies (0x106a, 0x106b), count 2
        body: [u16 row+0xde][u8 row+0xe0][u32 building uid = [this+0x3c]]
        then [this+0x780] |= 1
    KEY: row+0xde IS THE SUMMON SKILL ID, NOT A FORM. dat.pak's FE_BUILDING_DATA
    (parser 0x051906f8, layout read off it with feparse): WarCraft 951,
    DragonAltar 952 ("SummonDragon" in fet_skill), AlchemyLabo 953, Castle /
    Keep 954, GateOfHades 955, every other type 0xFFFF; +0xe0 = 1 (level);
    +0xe2 = the METAMORPHOSIS id: WarCraft 0 GIANT, DragonAltar 1 DRAGON,
    AlchemyLabo 2 CHIMAIRA, Castle 15/20 + Keep 16/17/18 3 KNIGHT,
    GateOfHades 4 WRAITH. So the 2026-09-10 assumption "u16 0..5 = form id"
    was WRONG: every real request carries 951..955 and `ok` answered NG 7.
    (The same rows ship +0xa4 = the BUILD crystal cost -- ArrowTower 18,
    Obelisk 15, WarCraft/Gate/Altar/Labo 20, the 2006 numbers -- +0xe1 = a
    per-type cap -- ArrowTower 10, Obelisk 25, the rest 1, FFSKY's caps --
    and +0xb0 = 12,000,000 on the castles, FFSKY's base HP.)
  * 0x106A arm 0503a3a1 -> 0x503d180: [u32 X]; [player+0x136c] = X (the
    building this summon is BOUND to); building X's +0x780 &= ~1. It does NOT
    transform anyone. 0x106B arm 0503a3bb -> 0x503d1d0: [u32 X][u32 code];
    same clear, then the NG table line (codes below).
  * THE TRANSFORM is 0x203D bit 0x10 (section 2): the arm stores the u16 at
    +0x3b4, then 0x506c7d0(word): if (word & 0x3f) == 0 it RETURNS -- no model
    change -- else clears HUM (and word 0xfdff), bit index -> METAMORPHOSIS
    row, builds '<model><side suffix>' from row+0x94 and the unit's army byte,
    loads it (0x5077d80), 0x5078a20(0, row+0x114), 0x5078cd0(row+0x228), HUD
    refresh for the self unit; 0x506c9f0 plays the change effect. Speed and
    jump follow on their own: the movement row is picked from [+0x3b4] & 0x3f
    (feworld _MOVE_ROWS). HP is NOT touched by any of it -- the server pushes
    the summon's HP (0x2024 maskA 0x2 max +0x49a, 0x4 current +0x49e).
  * RELEASE. The client sends header-only 0x2045 (no reply registered) from:
    the target menu's 'Unsummon' (0x050e2cc9, any time the unit is not HUM);
    the building DESTRUCTOR 0x05070384 when the torn-down building is the one
    in [player+0x136c] (the building fell, or the field was left); and the
    player vtable slot 0x60 0x0507fc70, reached from slot 0x54 0x0506b360,
    which CONDITION's setter 0x0506caa0 calls whenever the new word has GHOST
    (0x0506ccec). DEAD alone (slot 0x50 0x0506b0b0) sends nothing.
    KEY: THE ANSWER IS 0x106C, NOT FORM 0: c0 table (0x05039fd2 byte map,
    0x503a7ec) sends 0x106C -> 0x503d230 and 0x106E -> 0x503d260, both
    header-only, both a kind-3 lookup of the HEADER unit and a jump to
    0x0506c930 = REVERT TO HUMAN: HUM set (`or [+0x3b5],2`), human model
    rebuilt (slot 0x4c -> 0x507dc00), weapon motion (0x506aa30), and for the
    self unit [player+0x136c] = 0. (0x106D -> 0x503d250 is a bare `ret`.)
    It does NOT clear the form bits, so a 0x203D form 0x200 follows it: that
    leaves [+0x3b4] = 0x200 exactly as the ctor made it (0x506c7d0 returns
    early on it). WARNING: The old answer, 0x203D form 0, did NOTHING to the model
    (the early return) and CLEARED HUM -- the player stayed a Knight and could
    never summon again.
  * WARNING: HUM AND THE SELF RECORD: the self unit's ctor stamps HUM (0x0507D6E0),
    and feworld's self record (0x1006 type 0, sub bit 0x10 -> 0x5079dd6)
    overwrote [+0x3b4] with the stored f28 = 0 -- clearing HUM, so the menu
    said 'Unsummon' and every press sent 0x2045 instead of opening the window.
    feworld now serves FORM_HUM there.
NG codes (fe-ng-table.tsv / fe-ui-xlate): 1 クリスタルのHPが0 'Crystal HP is 0',
2 'Summon building destroyed', 3/4 system errors, 5 'Already summoning',
6 'Conditions unmet', 7 system error 'building offers a different monster'.
0x1070 arm 0x05055802 logs "METAMORPHOSIS COST :: Gold = %d / Crystal = %d":
[u16 N] x {[u16 METAMORPHOSIS id][u32 GOLD][u32 CRYSTAL]}.
LIVE-ONLY: what the self unit's army suffix does to the model name (red/blue
per side, SE: "赤=攻撃 青=防衛"); whether other players see the form (we serve
0x203D on the own unit only); whether 0x106C's human-model rebuild keeps the
worn gear drawn; the summon HP gauge.

====================================================================
4. GHOST (0x1030) and INVINCIBLE (0x1165)
====================================================================
0x1030 arm 0x05055b59: logs ">MSG_CHANGE_GHOST_OK" and returns 1 -- header-only,
no state. The state is CONDITION [unit+0x2b4] bit 0x1000000 (the client's own
name), pushed through 0x2024 maskA 0x100000 exactly as player_dead_push does
for DEAD. WARNING: The REQUEST id is not found: the packed reply pair 30 10 31 10 has
no hit in the image, so 0x1030 is served as a push behind `!ghost`.
0x1165 arm 0x05056315: NO body. Each receipt TOGGLES [0x52ffad0]+0x1e, prints
"無敵モード : %s" (ON 0x52d5c18 / OFF 0x52d5c14) as a system line
(0x5121fd0(3, ..)), and sets/clears bit 0 of [player+0x3d4]. Because it is a
toggle, `!invincible on|off` sends it only when the tracked state differs.

====================================================================
5. 0x1005 MSG_DEL batch, 0x2032 durability, 0x209D building HP, 0x20B0
====================================================================
0x1005 arm 0503a093: [u16 N] x [u32 id] -> 0x503bb30(id) each (log 0x52d4788
"MSG_DEL > unique id = %d", vtbl+0x18 destroy). N == 0 falls into the shared
no-op arm 0503a7cd. 0x1004 is the header-id form feworld's mob_kill_push sends.
0x2032 arm 0503a7a1 -> 0x500a280: [u32 item uid][u8 announce][u32 mask4] then
group-4 fields (0x5075b80): 0x1 u8, 0x2 i16 -> +0x4a8 count (flags a decrease),
0x4 i16 -> +0x4aa durability max, 0x8 i16 -> new +0x4ac current: with max !=
-1, old > 0 and new <= 0 prints 0x52dea50 "%s broke and can't be used."; with
max >= 100 and max/5 in (new, old] prints 0x52de9ec "%s is below 20%%
durability"; 0x10 u16, 0x20 ... Then 0x507b330(owner, uid) and, when announce
and the count fell, 0x511f500(item, old-new). 0x2030/0x2031 are the same
envelope for groups 1/2 ([u32 uid][u32 mask] -> 0x5075980 / 0x5075ad0);
decoded to reader widths only, see GROUP1_READERS.
0x209D arm 0503a6b7 -> 0x4ff4d30: [u32 maskA][u32 maskB]; A bit 0x2 u32
(0x5045ef0) -> building +0x774 via 0x5071220 (max), A bit 0x4 u32 -> +0x778
via 0x5071210 (current; 0x4ff4dd0 also plays the hit effect by distance);
both gated KIND-B == 5 (building). maskB is read and unused.
0x20B0 arm 0503a7bb -> 0x503d820(HEADER id): header-only; if the id is the
player, effect 0x6e at the player's position (0x516cc00/0x516d060), else a
kind-2 lookup; then 0x51b4ee0(uid). Label invented: UNIT EFFECT 0x6E.
0x1185 arm 0503a5be -> 0x503ce90: [u16 N] x {[u32 unit][u8 count] x {u16 u8
u32 ..}} into 0x503c880 (0x590 bytes, NOT read). Logged as a shape only.
0x115D / 0x115E / 0x2005 share arm 0503a7cd = `mov al,1; ret`: header-only
NO-OPS on this build. Nothing to serve.

====================================================================
6. 0x20AD MSG_RECOVER_PARAMETER_FROM_CRYSTAL_REQUEST, 0x20AE
====================================================================
Sender 0x05152dc2: window byte [0x5336d1c]+0x5a == 2, key 0x5077cd0 & 0x400,
nearest kind-7 object to the player (0x5066440(pos, -1, 7)) with [obj+0x778]
> 0 and within [0x5261960], player CONDITION & 0x1800000 == 0 (not DEAD/GHOST),
5 s cooldown ([this+0x88] += 0x1388). Body: [u32 obj uid]. No reply
registered. 0x20AE is header-only, sent right after when CONDITION bit
0x80000000 is set -- unread; logged.
Served (assumption, --crystal-heal N > 0): HP += N through feworld's own
bookkeeping (_SESSION["player_hp"], player_hp_push), the per-character
crystal store debited by --crystal-heal-cost and re-pushed (crystal_push).
The building's +0x778 is left alone unless --crystal-heal-drain > 0 (0x209D).

====================================================================
7. THE 2006 SUMMON RULES (--metamorphosis rules, the default)
====================================================================
Sources: SE's own site (warsystem02, polnews, dev/news12); the 2006 fan wikis
in the Wayback Machine -- fewiki WAR/召喚獣 (2006-05-26), Netzawar 召喚講座 and
Dragon (2006-06); FFSKY summon.htm (early FEZ, 2007) where RoD is silent
(web research, 2026-09-11). Every refusal
answers the client's own NG code; nothing is invented on the wire.
  * WHICH BUILDING: the one the request names, from THIS session's registry
    -- fecampaign's keeps (each side's base) and fewar's built war buildings
    -- served as the TYPE whose +0xde the request carries (else NG 7);
    unknown/fallen = NG 2. The form is that type's +0xe2 (the client's own
    table): Castle/Keep KNIGHT, WarCraft GIANT, GateOfHades WRAITH [4/14].
  * NOT the Dragon Altar / Alchemy Labo: fewiki 2006 lists both 未実装; when
    the Dragon (6/8) and the Chimera (8/24) shipped, the Chimera was called
    at the Castle/Keep and the Dragon needed no building. So those two rows
    summon nothing (NG 6) unless --summon-altars on.
      - CHIMERA: at your Castle/Keep, holding Chimera Blood (--summon-items
        CHIMERA=; SE 9/8 キマイラブラッドを所持した状態で、キャッスル/キープ
        にてクリスタルを40個消費). The keep's window shows the Knight at 40 --
        the Chimera's price too -- and the server serves the Chimera.
      - DRAGON: a holder of the Dragon Soul (--summon-items DRAGON=) on the
        losing side rises as one ON DEATH, at --summon-dragon-death odds, full
        HP (FFSKY 2007, the only mechanism recorded; the odds are CHOSEN).
      NEITHER ITEM SHIPS in this client's FE_ITEM_DATA: with --summon-items
      empty (default) there is no Chimera and no Dragon.
  * WHOSE: the building's side must be the player's (NG 6).
  * WHEN: --summon-phase war (default): only while the field is AT WAR.
  * WHERE: within --summon-range of the building (the client: 35.0).
  * COST: --summon-cost, crystal debited from the same store feworld's 0x2035
    serves, and served as 0x1070 so the Summon Window shows the same number:
    Knight 40, Giant 30, Wraith 50 (40 before 4/25) [Netz 2006: ナイト40個、
    ジャイアント30個、レイス50個 -- Famitsu's "30以上" was the minimum, the
    Giant's]; Chimera 40 [SE 9/8]; Dragon 0 (its price is the item).
  * HOW MANY: --summon-alive-cap per side at a time (Wraith 1 [SE 4/25],
    Dragon 3 [SE 6/8], Chimera 1 [SE 9/8]); --summon-war-cap per side per war
    (Chimera 2). The Chimera also unlocks one per bar of YOUR base's HP gauge
    lost; the Dragon needs your base >= --summon-dragon-gap bars behind the
    enemy's and > --summon-dragon-floor bars left (FFSKY 0.8 / 0.5). The
    gauge's bar count is --summon-bars (CHOSEN 3). Knight/Giant: no cap.
  * WHO: any class, any level (fewiki: 「Lv1からでも」).
  * HP: fewiki 2006 -- Knight 1800 below Lv10, 2900 from Lv10; Giant 5400;
    Wraith 2300; Dragon 8000; Chimera 4300 (FFSKY). The player's HP
    PERCENTAGE carries over, both ways (a Knight at 50% becomes an infantryman
    at 50%). A summon can't regain HP (the crystal heal refuses). The Chimera
    melts (--summon-chimera-drain HP/s, rate CHOSEN).
  * THE END: 'Unsummon' (0x2045) -> 0x106C + form 0x200 + the human HP. Its
    building destroyed ("建物が破壊されると...魂はクリスタルの源へ", the
    client's own blurbs): the client sends 0x2045 itself, and the pump also
    ends a summon whose building is gone. The field left: the same. DEATH: a
    summon at 0 HP is a death (fewiki/Netz: base damage as for an infantry
    death) -- feworld's DEAD and return-to-base run, and the form ends.
"""
import random
import struct
import sys
import time

fw = None   # the feworld module, handed in by register()

# ---------------------------------------------------------------------------
# 1. the 0x2026 family, bit -> (column-0 offset, label, where it draws, setter)
# Labels marked (inv) are invented here; the rest are the client's own strings.
# ---------------------------------------------------------------------------
UNIT_STAT = {
    0x00001: (0x514, "ATK_ADD_A",  "攻撃力 row, classes 0/1/3/6 (0x050e1507)", 0x4FF5810),
    0x00002: (0x518, "ATK_ADD_B",  "攻撃力 row, other classes (0x050e14e7)",   0x4FF5860),
    0x00004: (0x51C, "U51C",       "no draw site opened",                     0x4FF58B0),
    0x00008: (0x520, "U520",       "no draw site opened",                     0x4FF5900),
    0x00010: (0x524, "U524",       "no draw site opened",                     0x4FF5950),
    0x00020: (0x528, "ATK_ELEM0",  "(inv) 2nd block 0x050e747a; refresh 0x4ffeae0", 0x4FF59A0),
    0x00040: (0x52C, "ATK_ELEM1",  "(inv) 2nd block 0x050e7490; refresh 0x4ffeae0", 0x4FF59F0),
    0x00080: (0x530, "ATK_ELEM2",  "(inv) 2nd block 0x050e74a4; refresh 0x4ffeae0", 0x4FF5A40),
    0x00100: (0x534, "ATK_ELEM3",  "(inv) 2nd block 0x050e74b8; refresh 0x4ffeae0", 0x4FF5A90),
    0x00200: (0x538, "ATK_ELEM4",  "(inv) 2nd block 0x050e74cc; refresh 0x4ffeae0", 0x4FF5AE0),
    0x00400: (0x53C, "ATK_ELEM5",  "(inv) 2nd block 0x050e74e4; refresh 0x4ffeae0", 0x4FF5B30),
    0x00800: (0x540, "RES_ELEM0",  "耐性 row (sum x6, 0x050e156f)",            0x4FF5B80),
    0x01000: (0x544, "RES_ELEM1",  "耐性 row (sum x6, 0x050e1566)",            0x4FF5BD0),
    0x02000: (0x548, "RES_ELEM2",  "耐性 row (sum x6, 0x050e155d)",            0x4FF5C20),
    0x04000: (0x54C, "RES_ELEM3",  "耐性 row (sum x6, 0x050e1554)",            0x4FF5C70),
    0x08000: (0x550, "RES_ELEM4",  "耐性 row (sum x6, 0x050e154d)",            0x4FF5CC0),
    0x10000: (0x554, "RES_ELEM5",  "耐性 row (sum x6, 0x050e1546)",            0x4FF5D10),
    0x20000: (0x558, "U558",       "lone word, no column; ctor 100; no reader", 0x4FF5D60),
    0x40000: (0x55A, "U55A",       "pair; no reader found",                   0x4FF5DA0),
}
#: bits whose change makes the applier re-evaluate the worn weapon (0x4ffeae0)
UNIT_STAT_REFRESH_MASK = 0x7E0
#: the lone-word bit: the setter takes no column argument
UNIT_STAT_NO_COLUMN = 0x20000

# ---------------------------------------------------------------------------
# 2. 0x203D UNIT PROFILE: bit -> (struct code or "cstr"/"raw32", offset, name)
# ---------------------------------------------------------------------------
UNIT_PROFILE = {
    0x00001: ("cstr", 0x389, "name"),
    0x00002: ("B",    0x3AA, "sex"),
    0x00004: ("B",    0x3AB, "look1"),
    0x00008: ("I",    0x3B0, "f24"),
    0x00010: ("H",    0x3B4, "form"),      # RACE/metamorphosis; model rebuild
    0x00020: ("B",    0x3B6, "look2"),
    0x00040: ("B",    0x3B7, "look3"),
    0x00080: ("B",    0x3B8, "look4"),
    0x00100: ("B",    0x094, "army"),
    0x00200: ("I",    0x3BC, "force"),
    0x00400: ("I",    0x3C0, "f3c0"),
    0x00800: ("raw32", None, "text32"),    # 0x5045b10 len 0x20, a display string
    0x04000: ("I",    0x3D4, "flags3d4"),  # bit 0 = GM invincible
    0x08000: ("cstr", 0x3D8, "profile_comment"),
    0x10000: ("B",    0x3C4, "f3c4"),
}
PROFILE_FORM_BIT = 0x10

#: fe-fet-metamorphosis id -> the client's own RACE bit name; form word = 1<<id
METAMORPH_FORMS = {0: "GIA", 1: "DRA", 2: "CHIM", 3: "KNIT", 4: "WRTH", 5: "FSHIP"}
METAMORPH_NAMES = {"GIANT": 0, "DRAGON": 1, "CHIMAIRA": 2, "CHIMERA": 2,
                   "KNIGHT": 3, "WRAITH": 4, "FLYNGSHIP": 5, "FLYINGSHIP": 5,
                   "SHIP": 5}
#: 0x106B codes, from the client's NG table (fe-ng-table.tsv)
METAMORPH_NG = {1: "Crystal HP is 0", 2: "Summon building destroyed",
                3: "summoner id is not a PC (system error)",
                4: "no character object (system error)",
                5: "Already summoning", 6: "Conditions unmet",
                7: "building offers a different monster (system error)"}
NG_BUILDING_GONE, NG_ALREADY, NG_CONDITIONS, NG_WRONG_MONSTER = 2, 5, 6, 7

#: the RACE word's human bit (the self ctor 0x0507D6E0 stamps it; the target
#: menu reads it to choose 'Summ' over 'Unsummon'); a human's +0x3b4 is this
FORM_HUM = 0x200
#: the releases the client's own dispatcher answers with REVERT TO HUMAN
#: (0x0506c930): 0x106C (arm 0x503d230) and 0x106E (0x503d260), header-only
MSG_RELEASE_OK, MSG_RELEASE_NOTIFY = 0x106C, 0x106E

#: FE_BUILDING_DATA (dat.pak, parser 0x051906f8, read 2026-09-11):
#: building TYPE -> (+0xde summon SKILL, +0xe0 level, +0xe2 METAMORPHOSIS id).
#: Every other type ships +0xde = +0xe2 = 0xFFFF: no summon.
SUMMON_BUILDINGS = {
    1: (952, 1, 1),                    # DragonAltar -> DRAGON
    3: (953, 1, 2),                    # AlchemyLabo -> CHIMAIRA
    4: (955, 1, 4),                    # GateOfHades -> WRAITH
    5: (951, 1, 0),                    # WarCraft    -> GIANT
    15: (954, 1, 3), 20: (954, 1, 3),  # Castle      -> KNIGHT
    16: (954, 1, 3), 17: (954, 1, 3), 18: (954, 1, 3),   # Keep -> KNIGHT
}
#: the summon SKILL a 0x2044 carries -> METAMORPHOSIS id (fet_skill 951..955)
SUMMON_SKILLS = {951: 0, 952: 1, 953: 2, 954: 3, 955: 4}
#: summon rows the building table can offer (0..4; 5 FLYNGSHIP has none)
SUMMONABLE = (0, 1, 2, 3, 4)
#: the two building types 2006 NEVER SHIPPED as summon buildings: fewiki
#: (2006-05-26) lists アルターオブドラゴン and アルケミーラボ as 未実装; when
#: the Dragon (6/8) and the Chimera (8/24) came, the Chimera was called at the
#: Castle/Keep and the Dragon needed no building. The client carries the rows.
ALTAR_TYPES = (1, 3)
#: the Castle/Keep types (their +0xde is the Knight's 954)
BASE_TYPES = (15, 16, 17, 18, 20)

#: section 7's defaults, by METAMORPHOSIS id (see the docstring for sources)
SUMMON_COST_2006 = "KNIGHT=40,GIANT=30,WRAITH=50,CHIMERA=40,DRAGON=0"
SUMMON_HP_DEFAULT = "GIANT=5400,WRAITH=2300,CHIMERA=4300,DRAGON=8000"
SUMMON_KNIGHT_HP = "1800:2900:10"

#: CONDITION [unit+0x2b4] bits, the client's own names (0x0507A500)
COND_DEAD = 0x00800000
COND_GHOST = 0x01000000
COND_INVINCIBLE = 0x00001000

# ---------------------------------------------------------------------------
# 5. item group 4 (0x2032) and the decode-only groups 1/2
# ---------------------------------------------------------------------------
IT4_COUNT = 0x2      # i16 -> +0x4a8
IT4_DURMAX = 0x4     # i16 -> +0x4aa  (-1 = infinite)
IT4_DURCUR = 0x8     # i16 -> +0x4ac
#: group 1 (0x2030 -> 0x5075980) reader per bit; destinations read for three
GROUP1_READERS = {0x1: "cstr->+0x380", 0x2: "u16->+0x3a2 item no (+0x4e8 row)",
                  0x4: "u8", 0x8: "u8", 0x10: "i16", 0x20: "i16", 0x40: "i16",
                  0x80: "i16", 0x100: "u8->+0x3ae", 0x200: "u8"}
#: group 2 (0x2031 -> 0x5075ad0): bit 0x1 -> u8, u8 count, count x 0x18-byte
#: records {u16 f32 u16 f32 u16 u16 u16}; nothing else tested
GROUP2_SHAPE = "bit1: u8, u8 n, n x {u16 f32 u16 f32 u16 u16 u16}"

BHP_MAX = 0x2        # 0x209D maskA -> +0x774 via 0x5071220
BHP_CUR = 0x4        # 0x209D maskA -> +0x778 via 0x5071210

_KNOWN = {
    0x1005: "MSG_DEL batch [u16 N] x [u32 id] (0x503bb30 each)",
    0x2026: "UNIT STAT SETTERS col 0 [u32 mask] + i16 per bit -> +0x514..+0x55a (feunit.UNIT_STAT)",
    0x2027: "UNIT STAT SETTERS col 1 (same bits, +2)",
    0x1183: "UNIT STAT SETTERS batch col 0 [u16 N] x {[u32 obj][u32 mask] i16..}",
    0x1184: "UNIT STAT SETTERS batch col 1",
    0x203D: "UNIT PROFILE [u32 mask] (bit 0x10 u16 FORM = metamorphosis, model rebuild)",
    0x2030: "ITEM group-1 fields [u32 uid][u32 mask] -> 0x5075980",
    0x2031: "ITEM group-2 fields [u32 uid][u32 mask] -> 0x5075ad0",
    0x2032: "ITEM group-4 fields [u32 uid][u8 announce][u32 mask]: count/durability, 'broke' notice",
    0x209D: "BUILDING HP [u32 maskA][u32 maskB]: A 0x2 u32 max +0x774, A 0x4 u32 cur +0x778",
    0x20B0: "UNIT EFFECT 0x6E on the header unit (invented label; header-only)",
    0x1185: "UNIT LIST record [u16 N] x {[u32 unit][u8 n] x {..}} (0x503c880, not decoded)",
    0x115D: "header-only NO-OP on this build (arm 0503a7cd = mov al,1)",
    0x115E: "header-only NO-OP on this build (arm 0503a7cd = mov al,1)",
    0x2005: "header-only NO-OP on this build (arm 0503a7cd = mov al,1)",
    0x1030: ">MSG_CHANGE_GHOST_OK (header-only; the state is CONDITION 0x1000000 via 0x2024)",
    0x2044: "MSG_METAMORPHOSIS request [u16 summon skill = row+0xde][u8 row+0xe0][u32 building uid]",
    0x2045: "MSG_METAMORPHOSIS_RELEASE (invented label; header-only; 3 emitters)",
    0x106A: "MSG_METAMORPHOSIS_OK [u32 building uid] -> [player+0x136c]",
    0x106B: "MSG_METAMORPHOSIS_NG [u32 building uid][u32 code]",
    0x106C: "METAMORPHOSIS RELEASE -> REVERT TO HUMAN 0x0506c930 (header-only, header unit)",
    0x106D: "header-only no-op (arm 0x503d250 = ret)",
    0x106E: "METAMORPHOSIS RELEASE (2nd arm, same revert 0x0506c930; header-only)",
    0x1070: "MSG_METAMORPHOSIS_COST_NOTIFY [u16 N] x {[u16 form id][u32 gold][u32 crystal]}",
    0x1165: "GM INVINCIBLE toggle (no body; flips [dbg+0x1e], [player+0x3d4] bit 0)",
    0x20AD: "MSG_RECOVER_PARAMETER_FROM_CRYSTAL_REQUEST [u32 crystal building uid]",
    0x20AE: "companion of 0x20AD when CONDITION bit 0x80000000 (header-only, unread)",
}


def register(feworld):
    global fw
    fw = feworld
    fw.register_handler(0x2044, on_metamorphosis)
    fw.register_handler(0x2045, on_metamorphosis_release)
    fw.register_handler(0x20AD, on_recover_from_crystal)
    fw.register_handler(0x20AE, on_20ae)
    fw.register_gm(gm)
    fw.register_args(add_args)
    fw.register_pump(pump)
    for k, v in _KNOWN.items():
        fw.KNOWN.setdefault(k, v)


def add_args(ap):
    ap.add_argument("--metamorphosis", default="rules",
                    choices=("off", "ng", "ok", "rules"),
                    help="what a 0x2044 SUMMON request gets: rules (default) = "
                         "the 2006 summon rules (feunit section 7: your side's "
                         "building, at war, in range, crystals debited, per-side "
                         "caps) -> 0x106A + the form + the summon's HP, or 0x106B "
                         "with the client's own code; ok = always OK, no rules, "
                         "no debit (testing); ng = 0x106B --metamorphosis-ng "
                         "every time (the pre-09-11 escape hatch); off = log "
                         "only (the building's pending bit stays set)")
    ap.add_argument("--metamorphosis-ng", type=int, default=6,
                    help="0x106B code when --metamorphosis ng (6 = Conditions unmet)")
    ap.add_argument("--metamorphosis-form", default=None,
                    help="force the form for every OK (id 0..5 or a name) "
                         "instead of the one the building's summon skill names")
    ap.add_argument("--metamorphosis-cost", default="",
                    help="0x1070 rows FORM:GOLD:CRYSTAL,... served VERBATIM once "
                         "per field entry and on !morphcost, overriding the rows "
                         "built from --summon-cost; empty = use --summon-cost")
    ap.add_argument("--summon-cost", default="2006", metavar="SPEC",
                    help="per-summon price, FORM=CRYSTAL[:GOLD],... (FORM = "
                         "KNIGHT/GIANT/WRAITH/CHIMERA/DRAGON or 0..5); '2006' = "
                         + SUMMON_COST_2006 + " (Netz wiki 召喚講座 2006: ナイト40 "
                         "ジャイアント30 レイス50; Chimera SE 9/8; the Dragon is "
                         "paid in an item). Debited from the crystal store and "
                         "served as 0x1070 so the Summon Window shows the same")
    ap.add_argument("--summon-hp", default=SUMMON_HP_DEFAULT, metavar="SPEC",
                    help="the summon's full HP, FORM=HP,... (i16 on the wire): "
                         "fewiki WAR/召喚獣 2006 -- Giant 5400, Wraith 2300, "
                         "Dragon 8000; Chimera 4300 is FFSKY's (early FEZ). "
                         "The Knight has its own knob. The player's HP "
                         "PERCENTAGE carries over, both ways")
    ap.add_argument("--summon-knight-hp", default=SUMMON_KNIGHT_HP,
                    metavar="LOW:HIGH:LEVEL",
                    help="the Knight's HP by level (fewiki 2006: 1800 below "
                         "Lv10, 2900 from Lv10) -- the only summon whose HP "
                         "depends on level")
    ap.add_argument("--summon-altars", choices=("off", "on"), default="off",
                    help="off (2006): the Dragon Altar and the Alchemy Labo "
                         "summon nothing -- fewiki 2006 lists both 未実装; the "
                         "Chimera is called at the Castle/Keep and the Dragon "
                         "comes on death. on = serve the client's own rows "
                         "(skill 952/953) under the same rules")
    ap.add_argument("--summon-bars", type=int, default=3, metavar="N",
                    help="how many bars a base's HP gauge draws, for the "
                         "Chimera's one-per-bar-lost unlock (SE 9/8) and the "
                         "Dragon's 'bars behind' (FFSKY). CHOSEN. 0 = neither "
                         "rule")
    ap.add_argument("--summon-dragon-death", type=float, default=0.25,
                    metavar="ODDS",
                    help="the Dragon needs no building: a holder of the Dragon "
                         "Soul (--summon-items DRAGON=) on the losing side "
                         "turns into one ON DEATH at low odds, full HP (FFSKY "
                         "2007). The odds are CHOSEN. 0 = never")
    ap.add_argument("--summon-dragon-floor", type=float, default=0.5,
                    metavar="BARS",
                    help="...and only while your base still has more than "
                         "this many bars left (FFSKY: 0.5)")
    ap.add_argument("--summon-phase", choices=("war", "prep", "any"),
                    default="war",
                    help="when a summon is allowed: war = the field is AT WAR "
                         "(default); prep = war prep or war; any = no war "
                         "needed (and no side check without a campaign)")
    ap.add_argument("--summon-range", type=float, default=40.0, metavar="U",
                    help="max distance from the building, world units (the "
                         "client refuses past 35.0 itself, 0x5261cbc; this "
                         "allows for telemetry lag). 0 = not checked")
    ap.add_argument("--summon-alive-cap", default="WRAITH=1,DRAGON=3,CHIMERA=1",
                    metavar="SPEC",
                    help="per side, alive at a time, FORM=N,... (SE: Wraith "
                         "1 from 4/25, Dragon 3 from 6/8, Chimera 1 from 9/8)")
    ap.add_argument("--summon-war-cap", default="CHIMERA=2", metavar="SPEC",
                    help="per side, per war, FORM=N,... (SE 9/8: Chimera 2)")
    ap.add_argument("--summon-dragon-gap", type=float, default=0.8,
                    metavar="BARS",
                    help="the Dragon is for the LOSING side only (SE 6/8: "
                         "戦況に大きな差...負けている側): your base must trail "
                         "the enemy's by at least this many bars (FFSKY: 0.8). "
                         "Negative = no rule")
    ap.add_argument("--summon-chimera-drain", type=float, default=10.0,
                    metavar="HP_PER_S",
                    help="the Chimera melts (SE: HPが時間で減少; fewiki: its "
                         "self-dissolving DoT kills it); the rate is CHOSEN. "
                         "0 = no drain")
    ap.add_argument("--summon-items", default="", metavar="SPEC",
                    help="FORM=ITEM,... -- the item a summon needs and uses up: "
                         "DRAGON = 竜の魂 Dragon Soul, CHIMERA = キマイラブラッド "
                         "Chimera Blood (SE). NEITHER ships in this client's "
                         "FE_ITEM_DATA, so empty (default): no Chimera at the "
                         "keep and no Dragon on death until an item is named")
    ap.add_argument("--crystal-heal", type=int, default=50,
                    help="HP restored per 0x20AD -- RoD: +50 every 5 s crouched "
                         "at a giant crystal (the client's own 5 s cooldown); "
                         "0 = log only")
    ap.add_argument("--crystal-heal-cost", type=int, default=0,
                    help="crystals debited from the per-character store per 0x20AD")
    ap.add_argument("--crystal-heal-drain", type=int, default=0,
                    help="subtract this from the crystal building's +0x778 via 0x209D")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _log(msg):
    print("[feunit]    " + msg, flush=True)


def _uid(ctx):
    return fw.unit_id_of(ctx.args)


def _i16(v):
    v = int(v)
    if not -0x8000 <= v <= 0x7FFF:
        raise ValueError("i16 out of range: %d" % v)
    return struct.pack(">h", v)


def _stat_field(name):
    """A field spec -> the 0x2026 bit. Accepts a label, +0xHEX / 0xHEX column-0
    offset (either column), bitN, or a raw mask bit."""
    s = name.strip().upper()
    for bit, (off, label, _w, _va) in UNIT_STAT.items():
        if s == label:
            return bit
    try:
        v = int(s.lstrip("+"), 0)
    except ValueError:
        if s.startswith("BIT"):
            return 1 << int(s[3:])
        raise ValueError("unknown stat field %r" % name)
    for bit, (off, _l, _w, _va) in UNIT_STAT.items():
        if v in (off, off + 2):
            return bit
    if v in UNIT_STAT:
        return v
    raise ValueError("unknown stat field %r" % name)


# ---------------------------------------------------------------------------
# 1. stat setters
# ---------------------------------------------------------------------------
def stat_body(fields):
    """[u32 mask] then one i16 per set bit, ascending -- the applier's own
    test order (0x04ff504a .. 0x04ff51b1). `fields` = {bit: value}."""
    mask, body = 0, b""
    for bit in sorted(fields):
        if bit not in UNIT_STAT:
            raise ValueError("0x%X is not a 0x2026 bit" % bit)
        mask |= bit
        body += _i16(fields[bit])
    return struct.pack(">I", mask) + body


def stat_batch_body(rows):
    """0x1183/0x1184: [u16 N] x {[u32 obj][u32 mask] i16..}. rows = [(obj, fields)]."""
    out = struct.pack(">H", len(rows))
    for obj, fields in rows:
        out += struct.pack(">I", obj & 0xFFFFFFFF) + stat_body(fields)
    return out


def stat_push(ctx, fields, col=0, obj=None):
    """Serve unit stat fields. Header route (0x2026/0x2027, the header unit)
    unless `obj` is given (0x1183/0x1184 batch of one). Returns the id sent."""
    what = ", ".join("%s(+0x%03X)=%d" % (UNIT_STAT[b][1], UNIT_STAT[b][0] + 2 * col,
                                          fields[b]) for b in sorted(fields))
    refresh = " [re-evaluates the worn weapon: 0x4ffeae0]" \
        if any(b & UNIT_STAT_REFRESH_MASK for b in fields) else ""
    if obj is None:
        mid = 0x2027 if col else 0x2026
        ctx.reply(mid, stat_body(fields), why="UNIT STAT col %d: %s%s" % (col, what, refresh))
    else:
        mid = 0x1184 if col else 0x1183
        ctx.reply(mid, stat_batch_body([(obj, fields)]), unit_id=obj,
                  why="UNIT STAT batch obj=%d col %d: %s%s" % (obj, col, what, refresh))
    _log("gate: every setter is [obj+0x20]==3 (a PLAYER); a monster or "
         "building ignores it silently. PARTIAL: not screen-proved")
    return mid


# ---------------------------------------------------------------------------
# 2. profile / metamorphosis
# ---------------------------------------------------------------------------
def profile_body(fields):
    """0x203D: [u32 mask] then fields in bit order per UNIT_PROFILE."""
    mask, body = 0, b""
    for bit in sorted(fields):
        code, _off, _name = UNIT_PROFILE[bit]
        v = fields[bit]
        mask |= bit
        if code == "cstr":
            body += str(v).encode("cp932", "replace") + b"\0"
        elif code == "raw32":
            body += (str(v).encode("cp932", "replace") + b"\0" * 32)[:32]
        else:
            body += struct.pack(">" + code, int(v))
    return struct.pack(">I", mask) + body


def form_word(spec):
    """'GIANT' / 'dra' / 0..5 / 'off' / 0xMASK -> the +0x3b4 word.

    WARNING: A HUMAN IS FORM_HUM (0x200), NOT 0 (2026-09-11). 0x506c7d0 returns
    before touching the model when (word & 0x3f) == 0, so a 0 changed no model
    and only CLEARED HUM -- which turns the target menu's 'Summ' into
    'Unsummon' for good. The way back to a human is release_push (0x106C)."""
    s = str(spec).strip().upper()
    if s in ("OFF", "HUMAN", "HUM", "NONE"):
        return FORM_HUM
    try:
        return 1 << form_id(s)
    except ValueError:
        return int(s, 0) & 0xFFFF       # a raw word, e.g. 0x200


def form_id(spec):
    """A form spec -> METAMORPHOSIS id 0..5: KNIGHT / 'kni' (the RACE name) /
    3. ValueError for anything else."""
    s = str(spec).strip().upper()
    if s in METAMORPH_NAMES:
        return METAMORPH_NAMES[s]
    for i, n in METAMORPH_FORMS.items():
        if s == n:
            return i
    try:
        v = int(s, 0)
    except ValueError:
        raise ValueError("no such form %r" % spec)
    if 0 <= v <= 5:
        return v
    raise ValueError("no such form %r (ids are 0..5)" % spec)


#: METAMORPHOSIS_DATA's own names, for the log
FORM_NAME = {0: "GIANT", 1: "DRAGON", 2: "CHIMERA", 3: "KNIGHT", 4: "WRAITH",
             5: "FLYNGSHIP"}


def form_label(word):
    for i, n in METAMORPH_FORMS.items():
        if word == (1 << i):
            return n
    return "HUM" if word in (0, FORM_HUM) else "0x%X" % word


def metamorph_push(ctx, word, unit_id=None):
    """0x203D bit 0x10: the u16 form -> [unit+0x3b4]; a one-hot form bit
    rebuilds the model (0x506c7d0), FORM_HUM only rewrites the word. PARTIAL: NEVER
    SEEN ON A SCREEN."""
    ctx.reply(0x203D, profile_body({PROFILE_FORM_BIT: word}), unit_id=unit_id,
              why="UNIT PROFILE form 0x%X (%s) -> [unit+0x3b4]%s"
                  % (word, form_label(word),
                     ", model rebuild 0x506c7d0" if word & 0x3F else
                     " (no form bit: 0x506c7d0 returns early, no model change)"))
    ctx.session["morph_form"] = word
    return 0x203D


def form_spec(spec):
    """'KNIGHT=40:0,GIANT=30' -> {3: [40, 0], 0: [30]}. Keys are form specs
    (names or ids); values are ':'-separated ints."""
    out = {}
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part or part.lower() == "off":
            continue
        k, _, v = part.partition("=")
        out[form_id(k)] = [int(x, 0) if x.strip() else 0 for x in v.split(":")]
    return out


def summon_cost(args, fid):
    """(crystal, gold) one summon of form `fid` costs under --summon-cost."""
    spec = getattr(args, "summon_cost", "2006")
    if str(spec).strip().lower() == "2006":
        spec = SUMMON_COST_2006
    row = form_spec(spec).get(int(fid)) or [0]
    return max(0, row[0]), max(0, row[1] if len(row) > 1 else 0)


def summon_hp(args, fid):
    """The summon's FULL HP (i16): --summon-knight-hp by level for the Knight,
    --summon-hp for the rest, the human max when unset."""
    if int(fid) == 3:
        try:
            lo, hi, lv = (int(x, 0) for x in
                          str(getattr(args, "summon_knight_hp", SUMMON_KNIGHT_HP)
                              or SUMMON_KNIGHT_HP).split(":"))
        except (TypeError, ValueError):
            lo, hi, lv = 1800, 2900, 10
        try:
            level = int(fw.self_level(args) or 1)
        except Exception:                              # noqa: BLE001
            level = 1                                  # no class/level store
        return max(1, min(0x7FFF, hi if level >= lv else lo))
    row = form_spec(getattr(args, "summon_hp", SUMMON_HP_DEFAULT)).get(int(fid))
    if not row or row[0] <= 0:
        return fw.player_hp_max(args)
    return max(1, min(0x7FFF, row[0]))


def carry_hp(cur, old_max, new_max):
    """HP PERCENTAGE CARRIES OVER, both ways (fewiki 2006: a Knight at 50%
    becomes an infantryman at 50%). Never 0 for a living unit."""
    old_max = max(1, int(old_max))
    frac = max(0.0, min(1.0, float(cur) / old_max))
    return max(1, min(int(new_max), int(round(int(new_max) * frac))))


def _cost_rows(args):
    """0x1070 rows (form, gold, crystal): --metamorphosis-cost verbatim when
    given, else one per summonable form from --summon-cost."""
    out = []
    for spec in (getattr(args, "metamorphosis_cost", "") or "").split(","):
        spec = spec.strip()
        if not spec:
            continue
        k, a, b = (int(x, 0) for x in spec.split(":"))
        out.append((k, a, b))
    if out:
        return out
    if str(getattr(args, "summon_cost", "2006")).strip().lower() == "off":
        return []
    for fid in SUMMONABLE:
        crystal, gold = summon_cost(args, fid)
        out.append((fid, gold, crystal))
    return out


def cost_notify_body(rows):
    """0x1070: [u16 N] x {[u16 form][u32 gold][u32 crystal]} (arm 0x05055802
    reads u16 / u16 / u32 / u32 per row via 0x5045e30 / 0x5045e60 and logs
    'METAMORPHOSIS COST :: Gold = %d' then 'Crystal = %d')."""
    body = struct.pack(">H", len(rows))
    for k, a, b in rows:
        body += struct.pack(">HII", k & 0xFFFF, a & 0xFFFFFFFF, b & 0xFFFFFFFF)
    return body


def cost_notify_push(ctx):
    rows = _cost_rows(ctx.args)
    if not rows:
        return None
    ctx.reply(0x1070, cost_notify_body(rows),
              why="MSG_METAMORPHOSIS_COST_NOTIFY %d row(s) into the map at "
                  "0x53458b8 keyed by the METAMORPHOSIS id -- the Summon "
                  "Window's 'Crystal use' and its OK gate: %s"
                  % (len(rows), ", ".join("%s %dc/%dg" % (FORM_NAME.get(k, k), b, a)
                                          for k, a, b in rows)))
    return 0x1070


# ---------------------------------------------------------------------------
# 3b. THE SUMMON -- which building, whose, the rules, the change and the end
# ---------------------------------------------------------------------------
def hp_push(ctx, cur=None, mx=None, why=""):
    """0x2024 maskA 0x2 (i16 max -> +0x49a) and/or 0x4 (i16 current ->
    +0x49e, the STORE that draws the difference as a number), in bit order."""
    mask, body = 0, b""
    if mx is not None:
        mask |= 0x2
        body += struct.pack(">h", max(1, min(0x7FFF, int(mx))))
    if cur is not None:
        mask |= 0x4
        body += struct.pack(">h", max(0, min(0x7FFF, int(cur))))
    ctx.reply(0x2024, struct.pack(">II", mask, 0) + body,
              why="HP %s/%s%s" % ("-" if cur is None else cur,
                                  "-" if mx is None else mx,
                                  (" " + why) if why else ""))


def _camp():
    return sys.modules.get("fecampaign")


def _campaign_on(args):
    return _camp() is not None and getattr(args, "campaign", "off") == "on"


def summon_building(ctx, bld):
    """What THIS session knows of building `bld`: {type, side, area, grid,
    what}, or None. The registries are the ones that put the building on this
    client: fecampaign's keeps (feworld.keep_push) and fewar's real builds."""
    s, args = ctx.session, ctx.args
    try:
        bld = int(bld)
    except (TypeError, ValueError):
        return None
    k = (s.get("keeps") or {}).get(bld)
    if k is not None:
        types = fw._pair(getattr(args, "keep_types", None), (4, 16))
        btype = types[0] if k["side"] == "def" else types[1]
        try:
            grid = (fw.keep_grids(k["area"]) or {}).get(k["side"])
        except Exception:                              # noqa: BLE001
            grid = None
        return dict(type=int(btype), side=k["side"], area=int(k["area"]),
                    grid=grid, what="the %s base %d (type %d %s)"
                    % ("defenders'" if k["side"] == "def" else "attackers'",
                       bld, btype, fw.BUILDING_TYPES.get(btype, "?")))
    w = (s.get("war_buildings") or {}).get(bld)
    if w is not None:
        btype = int(w["type"])
        return dict(type=btype, side=w.get("side"),
                    area=w.get("area", s.get("field")),
                    grid=(int(w["gx"]), int(w["gz"])),
                    what="%s %d (built by %s)"
                    % (fw.BUILDING_TYPES.get(btype, "?"), bld,
                       w.get("side") or "this session"))
    return None


def alive_count(area, side, fid):
    """Summons of form `fid` standing for `side` in `area` right now, across
    every live session (a session's `morph` is its summon)."""
    n = 0
    for e in fw.ext_sessions():
        m = (e.get("session") or {}).get("morph")
        if m and m.get("area") == area and m.get("side") == side \
                and m.get("form") == fid:
            n += 1
    return n


def _base_frac(camp, area, side):
    """`side`'s base HP as a fraction of its max, or None without keeps."""
    k = (camp.state_of(area).get("keeps") or {}).get(side)
    if not k or int(k[1]) <= 0:
        return None
    return max(0.0, int(k[0]) / float(k[1]))


def _has_item(args, item_no):
    return any(int(r[1]) == int(item_no) for r in fw.item_rows(args))


def _take_item(ctx, item_no):
    """Use up ONE of `item_no` from the bag: stored, then pushed (0x107A)."""
    args = ctx.args
    rows = fw.item_rows(args)
    for i, (uid, no, flag, count) in enumerate(rows):
        if int(no) != int(item_no):
            continue
        new = list(rows)
        gone = []
        if count > 1:
            new[i] = (uid, no, flag, count - 1)
        else:
            del new[i]
            gone = [(uid, i)]
        fw._store_char_field(args, "items", [list(x) for x in new])
        fw.bag_layout_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                           rows, new, gone, "used up by a summon")
        return True
    return False


def summon_check(ctx, skill, bld):
    """(None, why, plan) when this session may summon from building `bld`
    with summon skill `skill`, else (0x106B code, why, None). Section 7."""
    args, s = ctx.args, ctx.session
    if not s.get("in_field") or s.get("room", -1) != -1:
        return NG_CONDITIONS, "not standing in a field", None
    m = s.get("morph")
    if m:
        return NG_ALREADY, "already a %s" % FORM_NAME.get(m["form"]), None
    b = summon_building(ctx, bld)
    if b is None:
        return NG_BUILDING_GONE, ("building %s is none this session was shown "
                                  "(fallen, or not a war building)" % bld), None
    row = SUMMON_BUILDINGS.get(b["type"])
    if row is None or row[0] != skill:
        return NG_WRONG_MONSTER, ("%s offers summon skill %s, the request "
                                  "carries %d" % (b["what"], row and row[0],
                                                  skill)), None
    fid = row[2]
    if b["type"] in ALTAR_TYPES and getattr(args, "summon_altars", "off") != "on":
        return NG_CONDITIONS, ("%s summoned nothing in 2006 (fewiki: 未実装); "
                               "--summon-altars on serves the client's row"
                               % b["what"]), None
    if s.get("player_dead") or s.get("ghost"):
        return NG_CONDITIONS, "the player is dead", None
    area = int(s.get("field"))
    rule = getattr(args, "summon_phase", "war")
    camp, campaign = _camp(), _campaign_on(args)
    side = b.get("side")
    if campaign:
        ph = camp.phase_of(area)
        allowed = {"war": (camp.WAR,), "prep": (camp.PREP, camp.WAR)}.get(rule)
        if allowed is not None and ph not in allowed:
            return NG_CONDITIONS, ("area %d is at %s (--summon-phase %s)"
                                   % (area, camp.PHASE_NAME.get(ph, ph), rule)), None
        mine = camp._my_side(args, area)
        if side in ("atk", "def") and mine != side:
            return NG_CONDITIONS, ("%s belongs to the %s side; this character "
                                   "is %s" % (b["what"], side,
                                              mine or "no side of this war")), None
        if mine is None and rule != "any":
            return NG_CONDITIONS, "this character is no side of the war", None
        side = mine or side
    elif rule != "any":
        return NG_CONDITIONS, ("no war here (--campaign off) and --summon-phase "
                               "%s" % rule), None
    rng = float(getattr(args, "summon_range", 40.0) or 0)
    pos = s.get("cpos")
    if rng > 0 and b.get("grid") and pos:
        bx, bz = fw.grid_to_world(b["grid"][0]), fw.grid_to_world(b["grid"][1])
        d = ((float(pos[0]) - bx) ** 2 + (float(pos[2]) - bz) ** 2) ** 0.5
        if d > rng:
            return NG_CONDITIONS, ("%.1fu from %s at (%.1f, %.1f) "
                                   "(--summon-range %g)" % (d, b["what"], bx, bz,
                                                            rng)), None
    items = form_spec(getattr(args, "summon_items", ""))
    if fid == 3 and b["type"] in BASE_TYPES and items.get(2) \
            and _has_item(args, items[2][0]):
        # THE CHIMERA IS CALLED AT THE CASTLE/KEEP (SE 9/8: キマイラブラッドを
        # 所持した状態で、キャッスル/キープにて). This client's keep rows offer
        # only the Knight (954), so the keep's Summon Window is the way in:
        # holding the Blood makes it a Chimera -- same 40 crystals it shows.
        code, why = (_war_rules(ctx, camp, area, side, 2)
                     if campaign and side in ("atk", "def") else (None, ""))
        if code is None:
            _log("   holds Chimera Blood (item %d) at %s: the CHIMERA, not the "
                 "Knight" % (items[2][0], b["what"]))
            fid = 2
        else:
            _log("   holds Chimera Blood, but %s -- the Knight instead" % why)
    if campaign and side in ("atk", "def"):
        code, why = _war_rules(ctx, camp, area, side, fid)
        if code is not None:
            return code, why, None
    item = (items.get(fid) or [None])[0]
    if item:
        if not _has_item(args, item):
            return NG_CONDITIONS, "the %s needs item %d" % (FORM_NAME[fid], item), None
    elif fid in (1, 2):
        _log("   (the %s needed an item in 2006; none ships in FE_ITEM_DATA and "
             "--summon-items names none -- not required)" % FORM_NAME[fid])
    crystal, gold = summon_cost(args, fid)
    have = fw._seeded_value(args, "crystal", getattr(args, "crystal", None))
    if crystal > 0 and have is not None and int(have) < crystal:
        return NG_CONDITIONS, ("costs %d crystal, carries %d" % (crystal, have)), None
    if gold > 0:
        g = fw._seeded_value(args, "gold", getattr(args, "gold", None))
        if g is not None and int(g) < gold:
            return NG_CONDITIONS, "costs %d gold, carries %d" % (gold, g), None
    return None, "ok", dict(b=b, fid=fid, side=side, area=area, crystal=crystal,
                            gold=gold, have=have, item=item, campaign=campaign)


def _bars(args):
    return int(getattr(args, "summon_bars", 3) or 0)


def dragon_losing(ctx, camp, area, side):
    """(True, why) when `side` is losing badly enough for a Dragon (SE 6/8:
    the side far behind; FFSKY: >= --summon-dragon-gap bars behind the enemy
    base and > --summon-dragon-floor bars of its own left)."""
    args = ctx.args
    gap = float(getattr(args, "summon_dragon_gap", 0.8))
    if gap < 0:
        return True, "no losing-side rule"
    foe = "def" if side == "atk" else "atk"
    me, them = _base_frac(camp, area, side), _base_frac(camp, area, foe)
    if me is None or them is None:
        return False, "the Dragon needs a war situation to read and no bases are served"
    bars = _bars(args) or 1
    behind, left = (them - me) * bars, me * bars
    floor = float(getattr(args, "summon_dragon_floor", 0.5))
    if behind < gap:
        return False, ("the Dragon is for the LOSING side: the %s base is %.2f "
                       "bar(s) behind, needs %g" % (side, behind, gap))
    if left <= floor:
        return False, ("the %s base has %.2f bar(s) left, needs more than %g"
                       % (side, left, floor))
    return True, "%.2f bar(s) behind, %.2f left" % (behind, left)


def _war_rules(ctx, camp, area, side, fid):
    """(None, "") or (0x106B code, why): the per-side caps and the Dragon's
    and the Chimera's situational rules for form `fid` in `area`'s war."""
    args = ctx.args
    done = camp.summon_count(area, side, fid)
    cap = form_spec(getattr(args, "summon_alive_cap", "")).get(fid)
    if cap and cap[0] > 0:
        n = alive_count(area, side, fid)
        if n >= cap[0]:
            return NG_CONDITIONS, ("the %s side already has %d %s standing "
                                   "(--summon-alive-cap %d)"
                                   % (side, n, FORM_NAME[fid], cap[0]))
    wcap = form_spec(getattr(args, "summon_war_cap", "")).get(fid)
    if wcap and wcap[0] > 0 and done >= wcap[0]:
        return NG_CONDITIONS, ("the %s side has summoned %d %s this war "
                               "(--summon-war-cap %d)"
                               % (side, done, FORM_NAME[fid], wcap[0]))
    if fid == 1:
        ok, why = dragon_losing(ctx, camp, area, side)
        if not ok:
            return NG_CONDITIONS, why
    if fid == 2 and _bars(args) > 0:
        me = _base_frac(camp, area, side)
        if me is None:
            return NG_CONDITIONS, ("the Chimera unlocks per bar of the base's HP "
                                   "and no base is served")
        lost = int((1.0 - me) * _bars(args) + 1e-9)
        if done >= lost:
            return NG_CONDITIONS, ("the Chimera: %d of %d bar(s) of the %s base "
                                   "lost, %d already summoned"
                                   % (lost, _bars(args), side, done))
    return None, ""


def morph_begin(ctx, fid, bld=None, side=None, area=None, bound=False, why="",
                full=False):
    """THE CHANGE: 0x106A (when a building asked), the 0x203D form, the
    summon's HP -- the player's HP PERCENTAGE of the summon's max (fewiki
    2006), or all of it (`full`: the Dragon that rises on death, FFSKY)."""
    args, s = ctx.args, ctx.session
    human_max = fw.player_hp_max(args)
    human_hp = int(s.get("player_hp", human_max))
    mx = summon_hp(args, fid)
    hp = mx if full else carry_hp(human_hp, human_max, mx)
    if bld is not None:
        ctx.reply(0x106A, struct.pack(">I", int(bld) & 0xFFFFFFFF),
                  why="MSG_METAMORPHOSIS_OK -> [player+0x136c]=%d (the summon is "
                      "BOUND to it), [bld+0x780]&=~1" % int(bld))
    now = time.monotonic()
    s["morph"] = dict(form=int(fid), word=1 << int(fid), bld=bld, bound=bound,
                      side=side, area=area if area is not None else s.get("field"),
                      hp_max=mx, human_hp=human_hp, t=now, drain_t=now,
                      drain_pushed=now)
    metamorph_push(ctx, 1 << int(fid))
    s["player_hp"] = hp
    hp_push(ctx, hp, mx, why="(the %s's; %s)" % (
        FORM_NAME.get(fid), "full" if full else
        "the player's %d/%d carried over as a percentage" % (human_hp, human_max)))
    _log("SUMMONED: %s (form 0x%X, HP %d/%d, side %s)%s -- the client swaps "
         "the model and the movement row from [+0x3b4] itself"
         % (FORM_NAME.get(fid), 1 << int(fid), hp, mx, side,
            (" " + why) if why else ""))


def release_push(ctx, why=""):
    """The two messages that make the unit a human again, with no bookkeeping:
    0x106C (-> 0x0506c930: HUM set, human model, [player+0x136c]=0) and the
    0x203D form FORM_HUM (0x0506c930 leaves the old form bits in +0x3b4, and
    the cast gate, the palette and the movement row all read them). Both arms
    null-check their unit, so this is safe mid field change."""
    ctx.reply(MSG_RELEASE_OK, b"", why="METAMORPHOSIS RELEASE -> 0x0506c930: HUM "
              "set, human model rebuilt, [player+0x136c]=0%s"
              % ((" " + why) if why else ""))
    metamorph_push(ctx, FORM_HUM)


def morph_end(ctx, why, restore_hp=True):
    """THE END: release_push, then the human HP. Returns the ended summon, or
    None when this session had none (nothing is sent then)."""
    args, s = ctx.args, ctx.session
    m = s.pop("morph", None)
    if m is None:
        return None
    release_push(ctx)
    mx = fw.player_hp_max(args)                         # human again
    in_field = s.get("in_field") and s.get("room", -1) == -1
    if not in_field:
        s["morph_hpfix"] = True        # the HP max waits for the next field
        s["player_hp"] = mx
    elif s.get("player_dead") or not restore_hp:
        hp_push(ctx, None, mx, why="(max back to the human's; the revive "
                                   "restores the rest)")
    else:
        cur = int(s.get("player_hp", m["hp_max"]))
        hp = carry_hp(cur, m["hp_max"], mx)
        s["player_hp"] = hp
        hp_push(ctx, hp, mx, why="(the %s's %d/%d carried back as a percentage)"
                % (FORM_NAME.get(m["form"]), cur, m["hp_max"]))
    _log("UNSUMMONED: the %s ends -- %s" % (FORM_NAME.get(m["form"]), why))
    return m


def on_metamorphosis(ctx, inner):
    f = inner[2:]
    if len(f) < 7:
        _log("0x2044 short body %r -- expected [u16][u8][u32]" % (f,))
        return
    skill, level, bld = struct.unpack_from(">HBI", f, 0)
    args = ctx.args
    mode = getattr(args, "metamorphosis", "rules")
    _log("MSG_METAMORPHOSIS request: summon skill %d (%s) lv %d from building %d "
         "(the client set [bld+0x780] bit 0 until 0x106A/0x106B clears it)"
         % (skill, FORM_NAME.get(SUMMON_SKILLS.get(skill), "not a summon skill"),
            level, bld))
    if mode == "off":
        _log("--metamorphosis off: NOT answered; the building's pending bit "
             "stays set and the client will not ask again from it")
        return

    def ng(code, why):
        ctx.reply(0x106B, struct.pack(">II", bld, code),
                  why="MSG_METAMORPHOSIS_NG code %d (%s): %s"
                      % (code, METAMORPH_NG.get(code, "?"), why))

    if mode == "ng":
        code = int(getattr(args, "metamorphosis_ng", 6) or 6)
        return ng(code, "--metamorphosis ng")
    forced = getattr(args, "metamorphosis_form", None)
    if mode == "ok":
        if ctx.session.get("morph"):
            return ng(NG_ALREADY, "already summoned (--metamorphosis ok)")
        if forced is not None:
            fid = form_id(forced)
        elif skill in SUMMON_SKILLS:
            fid = SUMMON_SKILLS[skill]
        elif 0 <= skill <= 5:
            fid = skill
        else:
            return ng(NG_WRONG_MONSTER, "u16 %d is no summon skill and "
                      "--metamorphosis-form is unset" % skill)
        b = summon_building(ctx, bld)
        morph_begin(ctx, fid, bld, side=b and b.get("side"), bound=b is not None,
                    why="(--metamorphosis ok: no rules, no debit)")
        return
    code, why, plan = summon_check(ctx, skill, bld)
    if code is not None:
        _log("   SUMMON REFUSED: %s" % why)
        return ng(code, why)
    fid = form_id(forced) if forced is not None else plan["fid"]
    # THE PRICE, same store and push as fewar's builds and feworld's 0x2035
    if plan["crystal"] > 0:
        if plan["have"] is None:
            _log("   costs %d crystal but the CRYSTAL channel is off (--crystal "
                 "unset): nothing to debit" % plan["crystal"])
        else:
            new = max(0, int(plan["have"]) - plan["crystal"])
            fw._store_char_field(args, "crystal", new)
            fw.crystal_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                            value=new)
            _log("   crystal %d -> %d (a %s costs %d)"
                 % (plan["have"], new, FORM_NAME[fid], plan["crystal"]))
    if plan["gold"] > 0:
        fw.wallet_charge(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                         plan["gold"], 0, "a %s summon" % FORM_NAME[fid])
    if plan["item"]:
        _take_item(ctx, plan["item"])
    if plan["campaign"] and plan["side"] in ("atk", "def"):
        n = _camp().summon_add(args, plan["area"], plan["side"], fid)
        _log("   the %s side has summoned %d %s this war"
             % (plan["side"], n, FORM_NAME[fid]))
    morph_begin(ctx, fid, bld, side=plan["side"], area=plan["area"], bound=True,
                why="from %s" % plan["b"]["what"])


def on_metamorphosis_release(ctx, inner):
    _log("MSG_METAMORPHOSIS_RELEASE (0x2045, header-only, no reply registered): "
         "'Unsummon', the bound building torn down, or GHOST set")
    if getattr(ctx.args, "metamorphosis", "rules") == "off":
        return
    if not ctx.session.get("morph"):
        _log("   no summon on this session -- not answered (a 0x106C would "
             "rebuild a human's model for nothing)")
        return
    morph_end(ctx, "0x2045 from the client")


def dragon_on_death(ctx):
    """THE DRAGON NEEDS NO BUILDING (fewiki 2006: the Altar never shipped).
    A holder of the Dragon Soul (--summon-items DRAGON=) on the LOSING side of
    a war rises as a Dragon when he dies, at low odds, with full HP (FFSKY
    2007 -- the only mechanism recorded). One roll per death. Returns True
    when it happened."""
    s, args = ctx.session, ctx.args
    death = s.get("revive_at") or "dead"
    if s.get("dragon_rolled") == death:
        return False
    s["dragon_rolled"] = death
    odds = float(getattr(args, "summon_dragon_death", 0.25) or 0)
    item = (form_spec(getattr(args, "summon_items", "")).get(1) or [None])[0]
    if odds <= 0 or not item or not _campaign_on(args) \
            or getattr(args, "metamorphosis", "rules") != "rules":
        return False
    if not _has_item(args, item):
        return False
    camp = _camp()
    area = s.get("field")
    if area is None or camp.phase_of(area) != camp.WAR:
        return False
    side = camp._my_side(args, area)
    if side not in ("atk", "def"):
        return False
    code, why = _war_rules(ctx, camp, area, side, 1)
    if code is not None:
        _log("   holds the Dragon Soul and died, but %s" % why)
        return False
    if random.random() >= odds:
        _log("   holds the Dragon Soul and died on the losing side -- the odds "
             "(%g) said no this time" % odds)
        return False
    s["player_dead"] = False
    s.pop("revive_at", None)
    fw.player_dead_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, False)
    _take_item(ctx, item)
    n = camp.summon_add(args, area, side, 1)
    morph_begin(ctx, 1, None, side=side, area=int(area), bound=False, full=True,
                why="(rose from death with the Dragon Soul; the %s side's #%d)"
                    % (side, n))
    return True


def morph_tick(ctx):
    """The pump's half: end a summon whose player died, whose field was left,
    or whose bound building is gone; drain the Chimera; re-send the human HP
    max after a summon that ended outside a field."""
    s, args = ctx.session, ctx.args
    m = s.get("morph")
    if m is None:
        if s.get("morph_hpfix") and s.get("in_field"):
            s.pop("morph_hpfix", None)
            hp_push(ctx, None, fw.player_hp_max(args),
                    why="(the human max, after a summon ended off-field)")
        if s.get("player_dead"):
            dragon_on_death(ctx)
        return
    if s.get("player_dead"):
        morph_end(ctx, "the summon was KILLED (a death, like any other)",
                  restore_hp=False)
        return
    if not s.get("in_field") or s.get("room", -1) != -1 \
            or s.get("field") != m.get("area"):
        morph_end(ctx, "the player left the field")
        return
    if m.get("bound") and summon_building(ctx, m.get("bld")) is None:
        morph_end(ctx, "its building %s is gone (the soul returns to the "
                       "Crystal)" % m.get("bld"))
        return
    rate = float(getattr(args, "summon_chimera_drain", 0) or 0)
    if m["form"] != 2 or rate <= 0:
        return
    now = time.monotonic()
    lost = int((now - m["drain_t"]) * rate)
    if lost <= 0:
        return
    m["drain_t"] += lost / rate
    hp = max(0, int(s.get("player_hp", m["hp_max"])) - lost)
    s["player_hp"] = hp
    if hp > 0 and now - m.get("drain_pushed", 0) < 5.0:
        return                          # one visible number per 5 s, not per tick
    m["drain_pushed"] = now
    hp_push(ctx, hp, why="(the Chimera drains, --summon-chimera-drain %g/s)" % rate)
    if hp <= 0:
        if getattr(args, "monster_attack", "off") == "on":
            s["player_dead"] = True
            s["revive_at"] = now + float(getattr(args, "revive_secs", 8) or 8)
            fw.player_dead_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, True)
            fw.respawn_wait_arm(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args)
            morph_end(ctx, "the Chimera's HP ran out -- a death", restore_hp=False)
        else:
            morph_end(ctx, "the Chimera's HP ran out (no revive clock: "
                           "--monster-attack off, so released, not killed)")


# ---------------------------------------------------------------------------
# 4. ghost / invincible
# ---------------------------------------------------------------------------
def condition_word(ctx, ghost=None):
    """The CONDITION word this server believes: --unit-state base, DEAD from
    feworld's own player_dead flag, GHOST from ours."""
    base = getattr(ctx.args, "unit_state", None)
    v = (base if base is not None else 0) & 0xFFFFFFFF
    if ctx.session.get("player_dead"):
        v |= COND_DEAD
    if ghost is None:
        ghost = bool(ctx.session.get("ghost"))
    v = (v | COND_GHOST) if ghost else (v & ~COND_GHOST)
    return v & 0xFFFFFFFF


def ghost_push(ctx, on):
    """CONDITION GHOST via 0x2024 maskA 0x100000 (setter 0x04ff4760 ->
    0x0506caa0), then the header-only 0x1030 the client only logs."""
    ctx.session["ghost"] = bool(on)
    v = condition_word(ctx, on)
    ctx.reply(0x2024, struct.pack(">III", 0x100000, 0, v),
              why="CONDITION 0x%08X (%s)" % (v, "GHOST set" if on else "GHOST cleared"))
    ctx.reply(0x1030, b"", why=">MSG_CHANGE_GHOST_OK (client logs it, nothing else)")
    return v


def invincible_push(ctx, on):
    """0x1165 is a TOGGLE with no body; send it only when the tracked state
    differs, so `on` twice does not switch it back off."""
    cur = bool(ctx.session.get("invincible"))
    if cur == bool(on):
        _log("invincible already %s -- 0x1165 is a toggle, not sent"
             % ("ON" if cur else "OFF"))
        return None
    ctx.session["invincible"] = bool(on)
    ctx.reply(0x1165, b"", why="GM INVINCIBLE toggle -> the client prints "
                               "'Invincible: %s' itself" % ("ON" if on else "OFF"))
    return 0x1165


# ---------------------------------------------------------------------------
# 5. delete batch, item durability, building hp, effect
# ---------------------------------------------------------------------------
def del_batch_body(ids):
    return struct.pack(">H", len(ids)) + b"".join(struct.pack(">I", i & 0xFFFFFFFF)
                                                  for i in ids)


def del_push(ctx, ids):
    ids = [int(i) for i in ids]
    ctx.reply(0x1005, del_batch_body(ids),
              why="MSG_DEL batch %s (each -> 0x503bb30 -> vtbl+0x18)" % ids)
    mobs = ctx.session.get("mobs") or {}
    for i in ids:
        mobs.pop(i, None)
    return 0x1005


def item_group4_body(uid, fields, announce=1):
    """0x2032: [u32 uid][u8 announce][u32 mask] then i16 per bit (0x5075b80
    reads 0x1 as u8, 0x2/0x4/0x8 as i16 via 0x5045ec0)."""
    mask, body = 0, b""
    for bit in sorted(fields):
        if bit not in (IT4_COUNT, IT4_DURMAX, IT4_DURCUR):
            raise ValueError("group-4 bit 0x%X not built" % bit)
        mask |= bit
        body += _i16(fields[bit])
    return struct.pack(">IBI", uid & 0xFFFFFFFF, announce & 0xFF, mask) + body


def durability_push(ctx, uid, cur, mx=None, announce=1):
    fields = {IT4_DURCUR: int(cur)}
    if mx is not None:
        fields[IT4_DURMAX] = int(mx)
    ctx.reply(0x2032, item_group4_body(uid, fields, announce),
              why="ITEM %d durability cur=%s%s -- the client prints 'broke' "
                  "itself when it crosses 0 (0x52dea50), '20%%' at max/5 (0x52de9ec)"
                  % (uid, cur, "" if mx is None else " max=%s" % mx))
    return 0x2032


def building_hp_body(cur=None, mx=None):
    mask, body = 0, b""
    if mx is not None:
        mask |= BHP_MAX
        body += struct.pack(">I", int(mx) & 0xFFFFFFFF)
    if cur is not None:
        mask |= BHP_CUR
        body += struct.pack(">I", int(cur) & 0xFFFFFFFF)
    return struct.pack(">II", mask, 0) + body


def building_hp_push(ctx, uid, cur=None, mx=None):
    ctx.reply(0x209D, building_hp_body(cur, mx), unit_id=uid,
              why="BUILDING %d hp cur=%s max=%s (+0x778/+0x774; KIND-B==5 only)"
                  % (uid, cur, mx))
    return 0x209D


def effect_push(ctx, unit_id=None):
    ctx.reply(0x20B0, b"", unit_id=unit_id,
              why="UNIT EFFECT 0x6E (header-only; self = effect at own position)")
    return 0x20B0


# ---------------------------------------------------------------------------
# 6. recover from crystal
# ---------------------------------------------------------------------------
def on_recover_from_crystal(ctx, inner):
    f = inner[2:]
    if len(f) < 4:
        _log("0x20AD short body %r" % (f,))
        return
    bld = struct.unpack_from(">I", f, 0)[0]
    args = ctx.args
    heal = int(getattr(args, "crystal_heal", 50) or 0)
    # 2026-09-11: a giant crystal of a live war (fecampaign's deposits) pays
    # the heal out of ITS deposit -- RoD and FEZ-early drained one crystal per
    # heal tick (changed only 2009-08-24). Checked here, before anything is
    # charged: a spent deposit (or a war that just ended) heals nobody.
    import sys
    camp = sys.modules.get("fecampaign")
    on_deposit, left = (camp.heal_drain(ctx, args, bld, 0)
                        if heal > 0 and camp is not None
                        and hasattr(camp, "heal_drain") else (False, None))
    if on_deposit and left is None:
        _log("0x20AD at giant crystal %d: spent, or its war is over -- not "
             "healed, nothing charged" % bld)
        return
    _log("MSG_RECOVER_PARAMETER_FROM_CRYSTAL_REQUEST at building %d (nearest "
         "kind-7 object with +0x778 > 0; client cooldown 5 s)" % bld)
    if heal <= 0:
        _log("--crystal-heal 0: logged only (no reply is registered, nothing hangs)")
        return
    if ctx.session.get("morph"):
        _log("a SUMMON can't regain HP (fewiki 2006: 回復不可) -- not healed, "
             "nothing charged")
        return
    cost = int(getattr(args, "crystal_heal_cost", 0) or 0)
    if cost > 0:
        have = fw._seeded_value(args, "crystal", getattr(args, "crystal", None))
        if have is None:
            _log("no crystal store (--crystal unset): heal refused")
            return
        if have < cost:
            _log("crystal %d < cost %d: heal refused" % (have, cost))
            return
        fw._store_char_field(args, "crystal", int(have - cost))
        fw.crystal_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                        value=int(have - cost))
    if on_deposit:
        n = int(getattr(args, "crystal_heal_deplete", 1) or 0)
        _ok, left = camp.heal_drain(ctx, args, bld, n)
        _log("the heal drew %d from giant crystal %d's deposit (%s left) -- the "
             "draw's own counter, so nothing is taken twice" % (n, bld, left))
    mx = fw.player_hp_max(args)
    hp = min(mx, int(ctx.session.get("player_hp", mx)) + heal)
    ctx.session["player_hp"] = hp
    fw.player_hp_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, hp)
    _log("ASSUMPTION: healed to %d/%d via the 0x2024 HP STORE (cost %d crystal)"
         % (hp, mx, cost))
    drain = int(getattr(args, "crystal_heal_drain", 0) or 0)
    if drain > 0 and not on_deposit:
        left = ctx.session.setdefault("crystal_left", {})
        cur = left.get(bld, int(getattr(args, "building_hp", (100, 100))[0]))
        cur = max(0, cur - drain)
        left[bld] = cur
        building_hp_push(ctx, bld, cur=cur)


def on_20ae(ctx, inner):
    _log("0x20AE (header-only; sent when CONDITION bit 0x80000000 is set) -- "
         "unread, logged only")


# ---------------------------------------------------------------------------
# pump: the summon's life (morph_tick), the cost table once per field entry
# ---------------------------------------------------------------------------
def pump(ctx):
    s = ctx.session
    if s.get("morph") is not None or s.get("morph_hpfix") or s.get("player_dead"):
        morph_tick(ctx)
    if not s.get("player_dead"):
        s.pop("dragon_rolled", None)          # the next death rolls again
    if not s.get("in_field"):
        s.pop("morphcost_sent", None)
        return
    if s.get("morphcost_sent"):
        return
    s["morphcost_sent"] = True
    if getattr(ctx.args, "metamorphosis", "rules") != "off":
        cost_notify_push(ctx)


# ---------------------------------------------------------------------------
# !verbs
# ---------------------------------------------------------------------------
HELP = ("!stat FIELD VALUE [UNIT] [col]  -- 0x2026/0x2027 (UNIT: 0x1183/0x1184)\n"
        "!statprobe [BASE]              -- every bit with sentinel BASE+i (col 0)\n"
        "!morph FORM|off                -- raw 0x203D bit 0x10 (GIANT..FLYNGSHIP / 0..5; off = 0x200)\n"
        "!summon FORM                    -- the whole change, no rules, no debit\n"
        "!unsummon                       -- 0x106C + form 0x200 + the human HP\n"
        "!morphcost                      -- 0x1070 (--metamorphosis-cost or --summon-cost)\n"
        "!ghost on|off                   -- CONDITION 0x1000000 + 0x1030\n"
        "!invincible on|off              -- 0x1165 toggle (tracked)\n"
        "!del ID [ID..]                  -- 0x1005 batch MSG_DEL\n"
        "!durability UID CUR [MAX]       -- 0x2032 group-4\n"
        "!bhp UID CUR [MAX]              -- 0x209D building hp\n"
        "!effect6e [UNIT]                -- 0x20B0 header-only")


def gm(ctx, line):
    parts = line.split()
    if not parts:
        return False
    verb, rest = parts[0].lower(), parts[1:]
    try:
        if verb == "!unitstat":
            _log("\n" + HELP)
            return True
        if verb == "!stat":
            if len(rest) < 2:
                _log("!stat FIELD VALUE [UNIT] [col]")
                return True
            bit = _stat_field(rest[0])
            val = int(rest[1], 0)
            # UNIT `0`, `-` or `self` = the header route (0x2026/0x2027)
            obj = None
            if len(rest) > 2 and rest[2].lower() not in ("0", "-", "self"):
                obj = int(rest[2], 0)
            col = int(rest[3], 0) if len(rest) > 3 else 0
            if col and bit == UNIT_STAT_NO_COLUMN:
                _log("bit 0x20000 (+0x558) has no column 1; sending col 0")
                col = 0
            stat_push(ctx, {bit: val}, col=col, obj=obj)
            return True
        if verb == "!statprobe":
            base = int(rest[0], 0) if rest else 1001
            fields = {b: base + i for i, b in enumerate(sorted(UNIT_STAT))}
            stat_push(ctx, fields, col=0)
            _log("read the Status window: 攻撃力 = class base + %d/%d (or %d/%d), "
                 "耐性 = 6 x (sum of %d..%d); anything else drawn names an UNNAMED field"
                 % (base, base + 1, base + 1, base, base + 11, base + 16))
            return True
        if verb == "!morph":
            if not rest:
                _log("!morph FORM|off  (%s)" % ", ".join(
                    "%d=%s" % (i, n) for i, n in METAMORPH_FORMS.items()))
                return True
            metamorph_push(ctx, form_word(rest[0]))
            return True
        if verb == "!summon":
            if not rest:
                _log("!summon FORM  (%s)" % ", ".join(
                    "%d=%s" % (i, n) for i, n in FORM_NAME.items()))
                return True
            if ctx.session.get("morph"):
                morph_end(ctx, "!summon replaces it")
            morph_begin(ctx, form_id(rest[0]), side=None,
                        why="(!summon: no building, no rules, no debit)")
            return True
        if verb == "!unsummon":
            if morph_end(ctx, "!unsummon") is None:
                release_push(ctx, "!unsummon with no summon on record")
            return True
        if verb == "!morphcost":
            if cost_notify_push(ctx) is None:
                _log("--metamorphosis-cost is empty and --summon-cost off; "
                     "nothing to send")
            return True
        if verb == "!ghost":
            ghost_push(ctx, rest[:1] == ["on"])
            return True
        if verb == "!invincible":
            invincible_push(ctx, rest[:1] == ["on"])
            return True
        if verb == "!del":
            if rest:
                del_push(ctx, [int(x, 0) for x in rest])
            return True
        if verb == "!durability":
            if len(rest) < 2:
                _log("!durability UID CUR [MAX]")
                return True
            durability_push(ctx, int(rest[0], 0), int(rest[1], 0),
                            int(rest[2], 0) if len(rest) > 2 else None)
            return True
        if verb == "!bhp":
            if len(rest) < 2:
                _log("!bhp UID CUR [MAX]")
                return True
            building_hp_push(ctx, int(rest[0], 0), cur=int(rest[1], 0),
                             mx=int(rest[2], 0) if len(rest) > 2 else None)
            return True
        if verb == "!effect6e":
            effect_push(ctx, int(rest[0], 0) if rest else None)
            return True
    except ValueError as e:
        _log("%s: %s" % (verb, e))
        return True
    return False
