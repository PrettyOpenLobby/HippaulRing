"""fewarbots.py -- WAR BOTS: a test mode that fills a war with soldiers, so one
or two real players can stand in a war the size RoD was built for
(2026-10-23, phase 1).

PARTIAL: BUILT 2026-10-23. First live run 2026-10-23 12:45Z (--war-bots 99,
45 a side) FAILED: bots raced and bunched on the player and the client hung
and crashed under ~125 messages a second. The view radius, budget, walk
record and spawn below are the fix, NOT yet live-tested.
Harness: tools/fe_warbots_test.py.

OFF BY DEFAULT. `--war-bots N` (0 = off), `!warbots N` through --gmcmd-file,
or the admin panel's "War bots" action (POST /admin/warbots {count}, world/
devtool.py). A change applies to every war under way at once (bots are added
or taken away to reach N a side) and to every war that starts after it.

WHAT A BOT IS. A soldier that exists only in this process: an id, a side, a
position, HP. Nothing of it is ever stored with a character.
  * AT WAR START (fecampaign._advance PREP -> WAR) N bots a side stand
    BEHIND their side's keep (feworld.keep_grids, the same grids the keeps
    and the arrival points use): 15-30 u back from it on the side away from
    the enemy keep, so a keep built on top of the declaring player does not
    put forty soldiers on top of them. They set off one by one,
    --war-bot-stagger-ms apart, so a column does not arrive all at once.
    Each is SIGNED UP through fecampaign.sign_up with a member row keyed
    "bot|<id>" and flagged `bot`, so the war's Attack/Defense [Count/Max]
    (0x1127) counts them. At most --war-cap - 5 a side (45 at the retail
    cap of 50), so real players always have room to join. Until 2026-10-23
    a temporary ceiling held it at 20 a side; 20 a side ran live with no
    crash and the operator raised it to the war cap.
  * Member rows flagged `bot` are skipped by everything that reaches a stored
    character or a live session: settle() (rank, Rings, war EXP, the
    ranking), side_players() (--pop-scale counts real players online only;
    --pop-scale-extra is its own knob) and the presence tick (keyed by the
    session's own member key, which no bot has).
  * THEY ARE DRAWN AS PLAYERS: the 0x1006 type-0 avatar record fepresence
    builds for a peer (avatar_body / identity_bytes), the side byte (+0x94,
    sub-record bit 0x100) and nation (bit 0x200) set so the client's
    friend/foe check reads them like any peer, moved with 0x2023 action-0
    records stamped in the RECEIVING client's clock (fepresence.rebase_clock),
    and removed with 0x1004. Each session draws them on its OWN thread from
    its own pump, so there is no thread per bot and no socket is touched
    from another session's thread.
  * A SCREEN SEES ONLY WHAT IS NEAR IT, AT A BOUNDED RATE (2026-10-23, after
    the first live run: 90 bots on one screen sent ~125 messages a second
    and the client hung, then crashed). Each session draws only the bots
    within --war-bot-view of its player (0x1004 once one is more than
    VIEW_HYST further), adds at most ADDS_PER_PUMP new ones per pump, and
    sends at most --war-bot-msg-rate bot messages in any one second, in
    this order: removals and falls, adds (nearest first), moves (nearest
    first), then HP changes and swings. What does not fit waits for the
    next pump and is rebuilt from the bot's state then, so a later move
    supersedes a skipped one and only a bot's latest swing is ever drawn.
  * ONE DRIVER PER FIELD (the _tower_driver pattern): the session that claims
    the field ticks every bot there -- walk to the nearest enemy (bot or real
    player) within --war-bot-aggro, else toward the enemy keep; swing every
    --war-bot-swing-ms in reach. A real player is hit through "pvp.hit" on
    their own thread, exactly as tower_fire does, so death, the penalty and
    the revive are fepvp's. A bot loses HP here, dies (0x1004 everywhere) and
    stands up again at its keep after --war-bot-respawn-s. A keep takes
    --war-bot-keep-damage through fecampaign.keep_damage(credit=False).
  * A REAL PLAYER HITS A BOT through fepvp (0xA011 and the cast path): the
    same damage they would deal a peer, applied to the bot's HP here.
  * AT WAR END (WAR -> TRUCE, before the decision, so the sign-up count of
    --war-decide signups is the real players') every bot of that field is
    removed with its sign-up, and the same when the count goes to 0.

BOT IDS (CHOSEN, 2026-10-23). A drawn unit's object id is the client's key
for it, and a real player's object id is their CHARID (fepresence: a peer is
drawn under its charid, which must be below --monster-base, 400). Charids
come from festore.next_charid: MAX(charid) + 1 over the whole store, so they
grow up from 1 and never reuse. Bots take the TOP of that space,
--monster-base - 100 .. --monster-base - 1 (300..399 by default; --war-bot-ids
LO-HI moves it), allocated from the top down. An id is skipped when a live
session plays it or when the store's highest charid has reached it, and the
first such skip is logged: once real charids reach the block the block has
to move (or --monster-base has to). Nothing else in the world uses ids below
400: NPCs are 1000+, monsters 400+, keeps 2900+, buildings 3000+.

SWINGS AND FALLS (2026-10-23, phase 2). A bot is drawn the way a real
player's cast reaches a peer (feprog.on_cast -> fepresence.relay_inner):
  * EVERY SWING (on a player, a bot or a keep) is a 0x1032 SKILL USE under
    the bot's id, in the layout a real client sends for a basic attack
    (prod, 2026-09-29, charid 12, skill 0):
        10 32 | 00 07 61 0c  00 07 65 ee  01  00 00 00 0c  00 00 00 01
              | 00 00  01  00 00 00 00  00 00 2e e1
              | 42 a4 a8 03  42 64 43 cd  43 3e c6 cf
    = [u32 start][u32 end = start + windup + active + recovery of the skill
    (skill_combat; checked on 0/130/135/140/155/270)][u8 1 = a position
    follows][u32 the caster's own id][u32 its cast count][u16 skill][u8 1]
    [u32 0 = no target unit][u32 the weapon uid][f32 x y z, the aim point].
    The skill is the class's basic attack from fe-fet-skill.tsv: Footman 0
    基本攻撃 (axe), Ranger 130 基本弓攻撃 (bow), Adept 270 基本魔法攻撃
    (wand), and the bot WEARS its class's starting gear from
    fe-fet-initialize_equip.tsv (--war-bot-gear) so the weapon motion set and
    the weapon uid exist on the receiver. Start and end are in the RECEIVING
    client's cast clock (fepresence.cast_clock), turned to face the target
    first (--war-bot-swing-anim).
  * A DEATH keeps the body drawn: HP 0 and 0x2024 CONDITION 0x800000 (the
    peer death push, fepresence.condition_body) under the bot's id, and the
    0x1004 only --war-bot-corpse-s later (0 = the old instant 0x1004).

WARNING: UNPROVEN, AND WHAT TO LOOK FOR FIRST ON A SCREEN:
  * the SWING: whether the receiver plays a served 0x1032 under a bot id
    (its arm 0x0503BB70 builds the same action object a local cast does,
    0x0504A8F0, for a unit of class 3; whether the start/end are compared
    against its clock is unread). A relayed real cast has never been
    confirmed on a screen either. If a bot stands still while HP drops,
    compare the log's "cast clock" source: 'connect' is ~30 s off;
  * the FALL: CONDITION 0x800000 on a served avatar is the same open
    question as --presence-dead condition. If the bot stays standing for
    --war-bot-corpse-s and then vanishes, the condition is not read there;
  * whether a kind-2 avatar shows the 0x2024 HP change as a damage number
    (the same open question as a peer's, fepvp --pvp-show-damage);
  * the GROUND: a bot's Y is area_ground_height (the map's MEDIAN emitter
    height), so on a hilly field a bot may float or sink;
  * the client with many extra avatars: the first live run (2026-10-23,
    --war-bots 99 = 90 bots, every one drawn, ~125 messages a second) hung
    the client within 20 s and crashed it. The view radius, the add stagger
    and the per-second budget are the answer to that; 20 a side then ran
    live with no crash. 45 a side (the war cap less 5) is UNPROVEN live:
    the budget still holds one screen to --war-bot-msg-rate a second.
Drawing needs fepresence active (--unit-id auto), for the reason it does.

SOLDIERS, NOT A HIVEMIND (2026-10-23, phase 1; operator: "they all take the
same path, do the same things, same stats"). OURS, every number CHOSEN:
  * SQUADS of 3-5 (SQUAD_SIZES, by the bot's number on its side). A squad
    has a ROUTE: front (straight at the enemy keep through a jittered
    midpoint), flank (out 25-40 u to one side through two waypoints and in
    on the keep from that side) or defend (no march: guard their own keep).
    The first live member that is not falling back leads; the rest walk to
    loose formation slots behind it, and the leader walks at the slowest
    member's speed. The whole squad sets off together, the squad's first
    bot number x --war-bot-stagger-ms after the spawn.
  * ROLES: the leader's place is the route's (front / flank); every second
    member is RANGED (a Ranger or an Adept); a defend squad is all
    defenders. Ranged bots shoot from their class's range
    (--war-bot-range, each bot 0.95-1.15 of it), and step back from an
    enemy inside --war-bot-kite (for at most KITE_SECS, then they stand
    and shoot). Defenders turn on enemies within --war-bot-guard of their
    own keep, else hold a post in front of it. Melee reach is
    --war-bot-reach. A bot's economy role (phase 3, "miner" / "builder",
    its `eco`) comes before its fighting role:
    ROLE_STEP[eco or role] = fn(args, area, bot, bots, players, now, dt)
    -> True takes over a bot's step.
  * STATS: each bot has a level factor (1 +- --war-bot-stat-spread) on
    --war-bot-hp and --war-bot-damage, times its class's (CLASS_HP,
    CLASS_DMG), its own swing time, aggro and walking speed
    (--war-bot-speed-min .. --war-bot-speed, never faster than the real
    5.2 u/s), and a way of picking targets (nearest, weakest, a random one
    in range held a few seconds).
  * BEHAVIOUR: short random pauses (--war-bot-pause a second, 0.6-2 s),
    and under --war-bot-retreat of its HP a bot walks home, gets
    --war-bot-regen of its max HP a second back there, and rejoins at 90%.
PLAYERS DECIDE THE WAR (2026-10-23, phase 2, operator chose a 50% floor):
  * a bot's swing at a keep never takes it below --war-bot-keep-floor of
    its max (fecampaign.keep_damage floor=), and neither does a player's
    death dealt by a bot (fepvp marks it; fecampaign.base_hit). So no
    bot-only war ends in a fallen keep: it runs to time-up, and the
    --war-decide rule decides it with the bots already gone (keeps: the
    DEFENDER holds; signups: the real players' count).
  * bot kills and bot damage are credited to nobody (no member row, no
    pc_damage, no kill crystals, no ticker).
  * soft balance: a side with fewer bots standing respawns faster, by
    (its standing + 1) / (theirs + 1), never under --war-bot-respawn-min of
    --war-bot-respawn-s. Both sides draw from the same squads and stats.
CRYSTALS, BUILDINGS AND SUMMONS (2026-10-23, phase 3, operator-approved;
--war-bot-economy, default on). OURS, every number CHOSEN unless named:
  * MINERS (--war-bot-miners a side, place 2 of the first marching squads)
    walk to the nearest giant crystal on their own half of the field, stand
    beside it and draw at the real client's pace: the first crystal 5 s after
    arriving, then one every 5 s, every 10 s once carrying more than 10
    (on_draw_crystal's KEY), --crystal-draw each, at most --crystal-war-max
    (20) a war and --crystal-carry-max (50) carried. A draw depletes the
    deposit like a player's (fecampaign.bot_draw) but never below
    --war-bot-deposit-reserve of its start, so the players keep the rest.
    The deposit's level reaches the screens through the players' own
    channel (campaign.crystal), at most once per DEP_RELAY_SECS.
  * KNEEL: NOT DRAWN. No record a peer's crouch reaches another screen by
    was found that is not a 0x1006: the client never reports its crouch
    (the server reads it off cadences, world/status.py), the walk record's
    state is the controller mode, and 0x2023 action 0x11 sets the AIRBORNE
    flag. A drawing miner just stands (the stop record), faced at the
    crystal.
  * HAND-OFF: a miner carrying HANDOFF_AT or more (or all it has once it
    can draw no more) walks to its side's builder and the crystals move in
    memory, the carry cap (50) holding as for a player trade. No record.
  * THE BUILDER (bot number 1 of a side) holds its spot behind the keep for
    the miners and spends: alternately a building (BOT_BUILD_ORDER: Arrow
    Tower, Obelisk; the --build-costs prices, 2006 ones when that is off)
    and a summon. A building goes through fecampaign.bot_build: the same
    cap and territory rules a player's 0x2007 meets, BOT_BUILD_GAP cells
    clear of everything, side-owned, by=None, credited to nobody, at most
    --war-bot-buildings a side a war, and served to every session by the
    ordinary building path (fecampaign._buildings_present: ONE type-1 0x1006
    per building per screen, a new object, never a bot's avatar).
  * THE SUMMON: at its own keep (within --summon-range) the builder becomes
    a KNIGHT, or a GIANT while the enemy has buildings standing, at the
    --summon-cost price (--pop-scale applied), within --summon-alive-cap and
    --summon-war-cap (shared with the players: feunit.alive_count counts
    bot summons) and --war-bot-summons a side a war. Server side it takes
    the form's HP (--summon-hp / the Knight's high row), BOT_FORMS' damage,
    reach and pace, and a Giant goes for the enemy buildings
    (fecampaign.bot_building_hit). It reverts on death: the corpse falls as
    the form and the bot stands up again a soldier (a new spawn, so a fresh
    0x1006 add, never an update).
  * DRAWING THE FORM (--war-bot-summon-anim, default on): the record a real
    summoner's peers get (feunit.peer_relay, --summon-seen): 0x203D UNIT
    PROFILE bit 0x10 [u16 form word] under the bot's id, then 0x2024 maskA
    0x6 (the form's max and current HP), through the budget like any bot
    record. NOT a 0x1006. WARNING: 0x203D's one-hot form word makes the
    client rebuild the unit's model (0x506c7d0) ONCE per summon, as it does
    for a real player's summon on a peer screen; a peer's summon form has
    never been watched on a screen (the summoner's own has: Dragon and
    Knight, 10-23). 'off' = the summon is server-only: only its HP max and
    current are sent, the soldier model stays.
  * A summoned bot's swing is drawn with its form's first skill (feunit
    FORM_SKILLS: Knight 973, Giant 975), weapon uid 0. UNPROVEN.
  * NOBODY GAINS: no member credit (build_count/summon_add credit=False), a
    bot tower's hit is from 0 and marked bot, so the death it deals drains
    the base only to --war-bot-keep-floor; the part of the obelisk drain
    only bot obelisks add stops there too, and so do a summoned bot's keep
    swings and a building a bot Giant fells. Bots never trade with players.
"""
import collections
import itertools
import math
import random
import struct
import sys
import threading
import time

fw = None                       # the feworld module, handed in by register()

_LOCK = threading.Lock()        # guards _BOTS and every bot dict in it
#: area -> {bot id: bot}. A bot: id, area, side, nation, name, cls, sex,
#: looks, home, lane, pos, hp, hpmax, dead_until, swing_at, goal, moving,
#: face_to, card
_BOTS = {}
#: serialises _fill/_despawn: two pumps reconciling at once must not both add
#: the same deficit. Order: _FILL_LOCK -> fecampaign._LOCK, _FILL_LOCK ->
#: _LOCK; nothing in here takes _FILL_LOCK while holding either.
_FILL_LOCK = threading.Lock()
_DRIVER = {}                    # area -> (charid, monotonic) -- who ticks it
_TICK = {}                      # area -> monotonic of the last tick
_RECONCILE = [0.0, False]       # [last reconcile, the boot purge has run]
_ONCE = set()
#: every spawn and respawn takes a new number: a viewer whose drawn copy has
#: an older one (an id reused by `!warbots 0` then N, or a respawn it never
#: saw the death of) deletes it and draws the new one
_GEN = itertools.count(1)

DRIVER_SECS = 5.0               # a silent driver hands the field over
TICK_SECS = 0.25
RECONCILE_SECS = 1.0
#: the 7-row speed table's war-field row (fepresence: row 0 = 4.0 u/s): the
#: move record's speed pair is a multiplier on it
WAR_FIELD_SPEED = 4.0
#: KEY: CHOSEN made-up soldier names, by class (look1 0 Warrior, 1 Scout,
#: 2 Sorcerer) -- a number follows, so no two bots share a name
CLASS_NAMES = ("Footman", "Ranger", "Adept")
#: KEY: SOURCED 2026-10-23 (fe-fet-skill.tsv): each class's basic attack, by
#: class byte -- 0 基本攻撃 (weapon mask 0x0067, the axe's 0x0004), 130
#: 基本弓攻撃 (0x0100, the bow), 270 基本魔法攻撃 (0x0202, the wand). The same
#: skill ids real players' basic attacks send (prod log: 0, 130, 270)
BASIC_ATTACK = (0, 130, 270)
#: the SKILL USE id a basic attack goes out under: 0x1032 ([skill+0x100] !=
#: 0), every basic attack on prod (0, 130, 135, 270) was 0x1032
SWING_MID = 0x1032
#: a swing older than this when a viewer's pump draws it is not drawn
SWING_FRESH_SECS = 1.5
#: SQUADS AND ROLES (module docstring, SOLDIERS). Bot number i of a side
#: falls in squad k by these sizes, cycled; squad k's route is
#: SQUAD_ROUTES[k], cycled -- the same for both sides
SQUAD_SIZES = (4, 3, 5, 4, 4, 3, 5)
SQUAD_ROUTES = ("front", "flank", "front", "defend", "flank", "front", "defend")
ROLES = ("front", "ranged", "flank", "defend")
#: phase 3: the economy roles (a bot's "eco", over its fighting "role")
ROLES_LATER = ("miner", "builder")
#: role (or eco role) -> fn(args, area, bot, bots, players, now, dt) -> True
#: when it took the bot's step (under _LOCK; no side effect outside the bot
#: dicts -- anything else is queued on _ECO_CTX[area]["ev"])
ROLE_STEP = {}
# ---- PHASE 3 (module docstring, CRYSTALS). CHOSEN unless named.
#: the builder is bot number BUILDER_I of its side
BUILDER_I = 1
#: a miner hands its crystals over once it carries this many
HANDOFF_AT = 10
#: ...standing this close to the builder
HANDOFF_REACH = 2.5
#: a miner draws standing this far from the crystal's centre, toward its own
#: keep (the client draws within 10.0 u, on_draw_crystal)
DRAW_SPOT = 4.0
#: the client's first draw comes 5 s after the crouch, then every 5 s, 10 s
#: once carrying more than 10 (fecampaign.on_draw_crystal)
CROUCH_S, DRAW_S, DRAW_SLOW_S, DRAW_SLOW_OVER = 5.0, 5.0, 10.0, 10
#: a deposit's level is relayed to the screens at most this often per deposit
DEP_RELAY_SECS = 5.0
#: what the builder puts up, in turn, and the model each is served with
#: (WARNING: the Arrow Tower's model is a reading -- b_build05_a ships a
#: complete set -- not seen from a real client's 0x2007; the Obelisk's 8
#: obelisk_a is what the suites build)
BOT_BUILD_ORDER = (0, 6)
BOT_BUILD_MODEL = {0: 0, 6: 8}
#: grid cells (2.5 u) out from the own keep toward the enemy a building goes
BOT_BUILD_DIST = {0: (12, 16, 20, 24), 6: (22, 26, 30, 34)}
#: a failed build or summon waits this long before the builder tries again
ECO_RETRY_S = 10.0
#: a summoned bot, by METAMORPHOSIS id: damage x, keep swing x, reach u,
#: walking pace x, building swing (the Giant's, on enemy buildings)
BOT_FORMS = {3: {"name": "Knight", "dmg": 2.5, "keep": 3.0, "reach": 4.5,
                 "speed": 1.0, "bld": 0},
             0: {"name": "Giant", "dmg": 1.5, "keep": 5.0, "reach": 6.0,
                 "speed": 0.8, "bld": 400}}
#: a summoned bot's swing skill (feunit.FORM_SKILLS' first)
FORM_SWING = {3: 973, 0: 975}
#: the Giant swings at a building from this far from its grid centre
GIANT_BLD_REACH = 8.0
#: the client's own human form word (feunit.FORM_HUM)
FORM_HUM = 0x200
#: area -> the tick's economy context (read before _LOCK; "ev" collects the
#: side effects the steps queue under it)
_ECO_CTX = {}
#: area -> {"built": {side: n}, "summons": {side: n}, "relay": {idx: t}}:
#: this war's bot buildings and summons (a war's bots leave with it)
_ECO_WAR = {}
#: per class (0 Footman, 1 Ranger, 2 Adept): HP and damage on the level factor
CLASS_HP = (1.0, 0.8, 0.75)
CLASS_DMG = (1.0, 0.75, 0.9)
#: how a bot picks among the enemies in its aggro, and how often
PICKS = ("near", "near", "weak", "random")
#: a "random" bot keeps its pick this long while it stays in reach of it
PICK_HOLD_SECS = 3.0
#: a ranged bot backs off for at most this long before it stands and shoots
KITE_SECS = 2.5
#: formation slots (across, back) behind a squad leader, by place in squad
FORM = ((0.0, 0.0), (-3.5, -3.0), (3.5, -3.0), (-2.0, -6.5), (2.0, -6.5))
#: seeds every bot's own random stream; a test sets it for repeatable runs
SEED = [time.time_ns()]
#: (area, side) -> the keep HP last relayed for a bot swing
_KEEP_LAST = {}
#: a drawn bot is removed once it is this much further than --war-bot-view,
#: so one walking along the edge is not added and removed every pump
VIEW_HYST = 10.0
#: the most new avatars one pump adds to one screen
ADDS_PER_PUMP = 8
#: a bot hit by a monster turns on it for this long (while it stays within
#: 1.5 x --war-bot-monster-radius), even from outside the radius
MOB_FOE_SECS = 6.0

# KEY: WHAT A REAL WALK RECORD CARRIES (prod log 2026-10-23 12:43:41-12:45:15,
# charid 1 walking in area 53; the 28-byte 0x2023 action-0 records it sent):
#
#   B (ms)      state  speed pair  position (x, y, z)    step from the last
#   1758059965  0      1300 0      138.8 65.5 148.4
#   1758060372  0      1300 0      137.2 65.6 149.6      407 ms  2.00 u
#   1758060762  0      1300 0      135.6 65.7 150.9      390 ms  2.06 u
#   1758061168  0      1300 0      134.4 65.7 152.5      406 ms  2.00 u
#   ... 70-odd more, every one 390-407 ms and 1.9-2.1 u apart
#   1758152965  1      1300 0      102.3 55.6 161.8      (standing)
#   1758153012  0      1300 0      100.7 57.0 161.1       47 ms  1.75 u
#   1758153450  1      1300 0      101.9 55.3 161.6      (the stop)
#
#   * CADENCE: one record per ~400 ms (the sender's 400 ms limiter).
#   * SPEED PAIR: the speed stat x1000, constant whether it walks or stops
#     (1300 + 0 = 1.3 x the war-field row's 4.0 = 5.2 u/s), and the steps
#     agree: 2.0 u per 0.4 s = 5.0-5.2 u/s.
#   * POSITION: NOT where the walker is at the stamp but where it will be one
#     record later -- its position plus 0.4 s of its velocity (the lookahead
#     at 0x0515DBD1). The log proves it twice: the first walk record after
#     standing is 1.5-1.75 u from the standing spot only 3-47 ms later (no
#     walk covers 1.75 u in 47 ms), and the STOP record that follows is
#     BEHIND that walk record (101.9 after 100.7, walking -x): the stop
#     carries the real spot, the walk record the spot 0.4 s on.
#   * STAMP: the sender's own clock at the moment it sends.
# So a bot's walk record is the same: stamped "now" in the receiver's clock,
# at the bot's position plus --war-bot-move-ms of its walk toward where it is
# heading (never past where it will halt), a speed pair of the speed it
# really walks, one per --war-bot-move-ms (400). Before 2026-10-23 it carried
# a point 0.8 s ahead (4 u, twice a real one's lookahead) sent once per
# 0.8 s, which the client's catch-up law (fepresence: D = dist/0.48 s -
# speed) turned into a sprint and a wait every record.


def register(feworld):
    global fw
    fw = feworld
    fw.register_pump(pump)
    fw.register_gm(gm)
    fw.register_args(add_args)


def add_args(ap):
    ap.add_argument("--war-bots", type=int, default=0, metavar="N",
                    help="TEST MODE (2026-10-23): N soldier bots a side join "
                         "every war at its start, behind their keep, signed up "
                         "so the war's counts include them (at most "
                         "--war-cap - 5 a side, 45 at the cap of 50). "
                         "They march in squads of 3-5 (front, flank, ranged, "
                         "defenders), each with its own stats, path and "
                         "target choice, and fight: players through "
                         "pvp.hit, bots in memory, keeps through keep_damage "
                         "(never below --war-bot-keep-floor). Never stored, "
                         "never rewarded, "
                         "not counted by --pop-scale. 0 (default) = off. "
                         "`!warbots N` and the panel's War bots change it live, "
                         "wars under way included. Drawing needs --unit-id auto.")
    ap.add_argument("--war-bot-ids", default="", metavar="LO-HI",
                    help="the object ids bots are drawn under. Default: the "
                         "top 100 below --monster-base (300-399): real charids "
                         "grow up from 1 and a peer must be under "
                         "--monster-base, so the block sits as far from them "
                         "as it can. An id a live player or the store's "
                         "highest charid has reached is skipped and logged.")
    ap.add_argument("--war-bot-hp", type=int, default=1000, metavar="N",
                    help="a war bot's HP at level factor 1 (each bot's is "
                         "this x its factor x its class's; u16 on the wire, "
                         "<= 32767)")
    ap.add_argument("--war-bot-damage", type=int, default=40, metavar="N",
                    help="one bot swing on a player or a bot at level factor "
                         "1 (x its factor x its class's; flat, the victim's "
                         "armour is not applied)")
    ap.add_argument("--war-bot-stat-spread", type=float, default=0.25,
                    metavar="F",
                    help="each bot's level factor is 1 +- F (uniform): its "
                         "HP and damage vary by that much. 0 = every bot "
                         "of a class the same")
    ap.add_argument("--war-bot-keep-damage", type=int, default=1, metavar="N",
                    help="one bot swing on the enemy keep, in --keep-hp points "
                         "(no member is credited). 0 = bots never hit a keep")
    ap.add_argument("--war-bot-keep-floor", type=float, default=0.5,
                    metavar="F",
                    help="PLAYERS DECIDE THE WAR (operator 2026-10-23): bot "
                         "swings, and player deaths dealt by bots, never "
                         "take a keep or castle below F of its max HP. "
                         "Players, summons and buildings are not held by "
                         "it. 0 = no floor")
    ap.add_argument("--war-bot-speed", type=float, default=5.2, metavar="U",
                    help="the FASTEST a bot walks, world units a second. "
                         "5.2 = the speed pair 1300 a real player's walk "
                         "records carry (1.3 x the war-field row's 4.0, prod "
                         "2026-10-23)")
    ap.add_argument("--war-bot-speed-min", type=float, default=4.6,
                    metavar="U",
                    help="the slowest: each bot walks at its own speed "
                         "between this and --war-bot-speed")
    ap.add_argument("--war-bot-aggro", type=float, default=15.0, metavar="U",
                    help="a bot turns on an enemy about this close (XZ; "
                         "each bot 0.9-1.15 of it, a ranged one at least "
                         "its range + 3), else it marches on")
    ap.add_argument("--war-bot-reach", type=float, default=4.0, metavar="U",
                    help="how close a melee bot (Footman) stands to swing")
    ap.add_argument("--war-bot-range", type=float, default=16.0, metavar="U",
                    help="how far a ranged bot (Ranger bow, Adept wand) "
                         "shoots from; each bot 0.95-1.15 of it")
    ap.add_argument("--war-bot-kite", type=float, default=5.0, metavar="U",
                    help="a ranged bot steps back from an enemy this close "
                         "(for at most 2.5 s, then it stands and shoots)")
    ap.add_argument("--war-bot-guard", type=float, default=25.0, metavar="U",
                    help="a defender turns on enemies this close to its own "
                         "keep, else holds its post in front of it")
    ap.add_argument("--war-bot-retreat", type=float, default=0.25,
                    metavar="F",
                    help="under F of its max HP a bot walks back behind its "
                         "keep, heals there and rejoins at 90%%. 0 = never")
    ap.add_argument("--war-bot-regen", type=float, default=0.02, metavar="F",
                    help="while back behind its keep a retreating bot gets F "
                         "of its max HP a second")
    ap.add_argument("--war-bot-pause", type=float, default=0.1, metavar="P",
                    help="the chance a second that a marching bot stops for "
                         "0.6-2 s. 0 = never")
    ap.add_argument("--war-bot-respawn-min", type=float, default=0.4,
                    metavar="F",
                    help="SOFT BALANCE: a side with fewer bots standing "
                         "respawns faster, by (its standing + 1) / (the "
                         "other's + 1), never faster than F x "
                         "--war-bot-respawn-s. 1 = off")
    ap.add_argument("--war-bot-keep-reach", type=float, default=12.0, metavar="U",
                    help="how close to the keep's centre a bot swings at it")
    ap.add_argument("--war-bot-swing-ms", type=int, default=2000, metavar="MS",
                    help="one swing per this many ms")
    ap.add_argument("--war-bot-respawn-s", type=float, default=30.0, metavar="S",
                    help="a killed bot stands up again at its keep after this")
    ap.add_argument("--war-bot-move-ms", type=int, default=400, metavar="MS",
                    help="a moving bot's walk record goes to a screen once "
                         "per this many ms, carrying its position this many "
                         "ms on -- what a real client sends (one per ~400 ms, "
                         "its position plus 0.4 s of its walk; prod "
                         "2026-10-23). The per-second budget "
                         "(--war-bot-msg-rate) can only send fewer")
    ap.add_argument("--war-bot-view", type=float, default=50.0, metavar="U",
                    help="a screen draws only the bots within this many "
                         "world units (XZ) of its player, and removes one "
                         "(0x1004) once it is 10 u further than this")
    ap.add_argument("--war-bot-msg-rate", type=int, default=20, metavar="N",
                    help="the most war bot messages one screen is sent in "
                         "any one second. Removals and falls go first, then "
                         "adds and moves of the nearest bots, then HP changes "
                         "and swings; the rest waits (a later move replaces "
                         "a skipped one). The first live run sent one client "
                         "~125 a second and it hung")
    ap.add_argument("--war-bot-stagger-ms", type=int, default=500, metavar="MS",
                    help="at war start a squad whose first bot is number i "
                         "of its side waits i x this many ms behind its keep "
                         "before it marches, the whole squad together (an "
                         "enemy in aggro starts a bot at once), so they do "
                         "not all arrive together. 0 = all at once")
    ap.add_argument("--war-bot-swing-anim", default="on", choices=("on", "off"),
                    help="draw each bot swing (2026-10-23): turn the bot to "
                         "its target and send the screens in its field a "
                         "0x1032 SKILL USE under its id, the record a real "
                         "player's basic attack sends (Footman skill 0, "
                         "Ranger 130, Adept 270). PARTIAL: not on a screen. "
                         "'off' = the swing lands with nothing drawn")
    ap.add_argument("--war-bot-gear", default="on", choices=("on", "off"),
                    help="bots wear their class's starting gear "
                         "(fe-fet-initialize_equip.tsv: axe, bow or wand and "
                         "the casual clothes), drawn like a peer's worn gear; "
                         "the swing names the weapon's uid. 'off' = drawn "
                         "empty-handed")
    ap.add_argument("--war-bot-corpse-s", type=float, default=5.0, metavar="S",
                    help="a felled bot stays drawn this long, with HP 0 and "
                         "the CONDITION DEAD push (0x2024 0x800000) so it can "
                         "fall, before it is removed (0x1004). 0 = removed "
                         "at once, the behaviour before 2026-10-23")
    ap.add_argument("--war-bot-face", choices=("turn", "off", "rebuild"),
                    default="turn",
                    help="how a STANDING bot is turned to what it faces (a "
                         "walking one turns by itself): turn (default) = "
                         "0x2023 action 1, place + face, no model work; "
                         "rebuild = the facing-only 0x1006 on every step and "
                         "swing, which rebuilds the avatar's models and "
                         "crashed a client 2026-10-23; off = never turned")
    ap.add_argument("--war-bot-monsters", default="on", choices=("on", "off"),
                    help="MONSTERS AND BOTS FIGHT (operator 2026-10-23): "
                         "the field's monsters chase and hit bots as they do "
                         "players (--monster-attack on for the hits), and a "
                         "bot hit by a monster, or with a living one within "
                         "--war-bot-monster-radius, fights it with its normal "
                         "swing. A bot's hit on a monster credits nobody: no "
                         "EXP, no chest, no gold, no ledger. Bots never hunt "
                         "monsters, and monsters never count for the war. "
                         "'off' = they ignore each other")
    ap.add_argument("--war-bot-monster-radius", type=float, default=9.0,
                    metavar="U",
                    help="a bot turns on a living monster this close to it "
                         "(XZ), or on the one that hit it in the last 6 s "
                         "while it stays within 1.5 x this; otherwise it "
                         "carries on with its role")
    # ---- phase 3 (2026-10-23): crystals, buildings, summons
    ap.add_argument("--war-bot-economy", default="on", choices=("on", "off"),
                    help="BOTS DRAW CRYSTALS, BUILD AND SUMMON (phase 3): "
                         "--war-bot-miners bots a side draw from the giant "
                         "crystals on their half at the real client's pace "
                         "and carry them to their side's builder, which puts "
                         "up Arrow Towers and Obelisks and summons Knights "
                         "(Giants against buildings) under the players' "
                         "rules and caps. Nobody is credited, and nothing "
                         "of theirs takes a keep below --war-bot-keep-floor. "
                         "'off' = every bot is a soldier (phase 1-2)")
    ap.add_argument("--war-bot-miners", type=int, default=3, metavar="N",
                    help="miners a side (the third place of the first N "
                         "marching squads). 0 = no crystals are drawn")
    ap.add_argument("--war-bot-deposit-reserve", type=float, default=0.5,
                    metavar="F",
                    help="bots never draw a giant crystal below F of what "
                         "it started with: the rest is the players'. 1 = "
                         "bots never draw")
    ap.add_argument("--war-bot-buildings", type=int, default=3, metavar="N",
                    help="buildings bots put up a side a war (Arrow Tower "
                         "and Obelisk in turn, inside the side's own "
                         "territory, the players' caps counting them too). "
                         "0 = bots never build")
    ap.add_argument("--war-bot-summons", type=int, default=2, metavar="N",
                    help="summons bots make a side a war (a Knight, or a "
                         "Giant while the enemy has buildings), each within "
                         "--summon-alive-cap/--summon-war-cap like a "
                         "player's. 0 = bots never summon")
    ap.add_argument("--war-bot-summon-anim", default="on", choices=("on", "off"),
                    help="draw a bot's summon: 0x203D UNIT PROFILE form "
                         "under its id (the record a real summoner's peers "
                         "get, --summon-seen; NOT a 0x1006, but the client "
                         "rebuilds the model once for it) and its form's HP. "
                         "PARTIAL: a peer's summon form has never been "
                         "watched on a screen. 'off' = the summon is "
                         "server-only (its HP is sent, the model stays)")


# ---------------------------------------------------------------------------
def _log(msg):
    print("[fewarbots] " + msg, flush=True)


def _once(key, msg):
    if key in _ONCE:
        return
    _ONCE.add(key)
    _log(msg)


def _camp():
    return sys.modules.get("fecampaign")


def _pres():
    return sys.modules.get("fepresence")


def _int(args, name, default):
    try:
        return int(getattr(args, name, default))
    except (TypeError, ValueError):
        return default


def _float(args, name, default):
    try:
        return float(getattr(args, name, default))
    except (TypeError, ValueError):
        return default


def count(args):
    return max(0, _int(args, "war_bots", 0))


def per_side(args):
    """Bots a side: --war-bots, never more than --war-cap - 5 (45 at the
    retail cap of 50), so real players always have room to join."""
    n = count(args)
    camp = _camp()
    if not n or camp is None:
        return 0
    cap = max(0, camp.war_cap(args) - 5)
    if n > cap:
        _once(("max", n, cap), "--war-bots %d: %d a side (the war cap less 5)"
              % (n, cap))
    return max(0, min(n, cap))


def id_range(args):
    """(lo, hi) inclusive: --war-bot-ids, else the top 100 below
    --monster-base. Always inside 1 .. --monster-base - 1."""
    limit = max(2, _int(args, "monster_base", 400) or 400)
    lo, hi = max(1, limit - 100), limit - 1
    spec = str(getattr(args, "war_bot_ids", "") or "").strip()
    if spec:
        a, _, b = spec.replace(":", "-").partition("-")
        try:
            lo, hi = int(a), int(b)
        except ValueError:
            _once(("ids", spec), "--war-bot-ids %r is not LO-HI -- using %d-%d"
                  % (spec, lo, hi))
    lo, hi = max(1, lo), min(limit - 1, hi)
    return lo, max(lo - 1, hi)


def _store_top():
    """The highest charid on file (festore.next_charid - 1), 0 when the store
    cannot be read. Tests replace this."""
    try:
        import festore
        return int(festore.next_charid([])) - 1
    except Exception:                                  # noqa: BLE001
        return 0


def _live_charids():
    out = set()
    for ent in fw.ext_sessions():
        try:
            cid = int(ent["session"].get("charid") or 0)
        except (TypeError, ValueError):
            continue
        if cid:
            out.add(cid)
    return out


def is_bot(bid):
    try:
        bid = int(bid)
    except (TypeError, ValueError):
        return False
    with _LOCK:
        return any(bid in bots for bots in _BOTS.values())


def bots_of(area):
    """{id: copy of the bot} in `area` (dead ones included)."""
    with _LOCK:
        return {b: dict(v) for b, v in (_BOTS.get(int(area)) or {}).items()}


# ---------------------------------------------------------------------------
# where things stand
# ---------------------------------------------------------------------------
def _ground(area):
    """A bot's Y: the map's median emitter height (the estimate tower fire
    and derived_spawn use), else 0."""
    try:
        y = fw.fegamedata.area_ground_height(area)
        return float(y) if y is not None else 0.0
    except Exception:                                  # noqa: BLE001
        return 0.0


def keep_pos(area, side):
    """(x, y, z) of `side`'s keep in `area`, or None (no castle row)."""
    g = (fw.keep_grids(area) or {}).get(side)
    if not g:
        return None
    return (fw.grid_to_world(g[0]), _ground(area), fw.grid_to_world(g[1]))


def _away(area, side):
    """(own keep, unit XZ vector pointing AWAY from the enemy keep). With no
    enemy keep, away from the map centre; with no keep at all, either side
    of the centre."""
    c = fw.grid_to_world(128)
    k = keep_pos(area, side)
    if k is None:
        k = (c + (-40.0 if side == "atk" else 40.0), _ground(area), c)
    e = keep_pos(area, "def" if side == "atk" else "atk") or (c, k[1], c)
    dx, dz = k[0] - e[0], k[2] - e[2]
    h = math.hypot(dx, dz)
    if h < 1e-6:
        return k, ((1.0, 0.0) if side == "atk" else (-1.0, 0.0))
    return k, (dx / h, dz / h)


def _home(area, side, i):
    """Where bot number `i` of a side stands: BEHIND its own keep, 15-30 u
    back on the side away from the enemy keep, its squad in formation and
    squads side by side across that line (2026-10-23: they stood round the
    keep, and the attackers' keep is
    built where the declaring player stands, so the first live run put
    them all on top of the player). Never off the 256-cell grid."""
    k, (ux, uz) = _away(area, side)
    # SQUADS (2026-10-23): a squad stands in its formation, its leader
    # 15-20 u back and squads side by side 9 u apart, each member at its
    # FORM slot behind the leader (at most ~30 u back in all)
    sq, place = squad_of(i)
    a, f = FORM[place % len(FORM)]
    if role_of(i)[0] == "ranged":
        f -= 2.0
    back = 15.0 + (sq % 3) * 2.5 - f
    # (this across axis is the mirror of _frame's, hence the sign)
    across = -(((sq % 5) - 2) * 9.0 + a)
    lim = abs(fw.grid_to_world(2))
    x = k[0] + ux * back - uz * across
    z = k[2] + uz * back + ux * across
    return (max(-lim, min(lim, x)), k[1], max(-lim, min(lim, z)))


def squad_of(i):
    """(squad number, place in the squad) of bot number `i` of a side."""
    k, start = 0, 0
    while True:
        n = SQUAD_SIZES[k % len(SQUAD_SIZES)]
        if i < start + n:
            return k, i - start
        start += n
        k += 1


def squad_start(k):
    """The bot number of squad `k`'s first place."""
    return sum(SQUAD_SIZES[j % len(SQUAD_SIZES)] for j in range(k))


def role_of(i):
    """(role, class byte) of bot number `i` of a side: the squad's route for
    the leader's place and every second place after it, RANGED (Ranger or
    Adept, alternating) between them; a defend squad all defenders, a
    Footman or two with a Ranger and an Adept."""
    k, j = squad_of(i)
    route = SQUAD_ROUTES[k % len(SQUAD_ROUTES)]
    if route == "defend":
        return "defend", (0, 1, 0, 2, 0)[j % 5]
    if j % 2 == 1:
        return "ranged", 1 if (k + j // 2) % 2 == 0 else 2
    return route, 0


def miner_slots(n):
    """The bot numbers of a side's `n` miners: the third place of each of
    the first `n` marching (non-defend) squads with three or more."""
    out, k = [], 0
    while len(out) < max(0, int(n)) and k < 64:
        if SQUAD_ROUTES[k % len(SQUAD_ROUTES)] != "defend" \
                and SQUAD_SIZES[k % len(SQUAD_SIZES)] >= 3:
            out.append(squad_start(k) + 2)
        k += 1
    return out


def eco_role(args, i):
    """"builder", "miner" or None for bot number `i` of a side. Set whatever
    --war-bot-economy says (the tick reads the flag), so turning it on
    mid-war gives the bots already standing their jobs."""
    if i == BUILDER_I:
        return "builder"
    if i in miner_slots(_int(args, "war_bot_miners", 3)):
        return "miner"
    return None


def economy_on(args):
    return str(getattr(args, "war_bot_economy", "on") or "on") == "on"


def summons_alive(area, side, fid):
    """War bots of `side` standing in `area` as summon form `fid` (feunit's
    alive caps count these with the players' own)."""
    try:
        area, fid = int(area), int(fid)
    except (TypeError, ValueError):
        return 0
    with _LOCK:
        return sum(1 for v in (_BOTS.get(area) or {}).values()
                   if v.get("morph") == fid and v["side"] == side
                   and not v["dead_until"])


def _clamp(p):
    lim = abs(fw.grid_to_world(2))
    return (max(-lim, min(lim, p[0])), p[1], max(-lim, min(lim, p[2])))


def _frame(area, side):
    """(own keep, enemy keep, forward unit (toward the enemy), across unit)
    in XZ. With no keeps, the fallbacks _away uses."""
    own, (ux, uz) = _away(area, side)
    fx, fz = -ux, -uz
    foe = keep_pos(area, "def" if side == "atk" else "atk") \
        or _clamp((own[0] + fx * 80.0, own[1], own[2] + fz * 80.0))
    return own, foe, (fx, fz), (-fz, fx)


def _at(base, fwd, acr, f, a):
    """`base` moved `f` along forward and `a` across, kept on the grid."""
    return _clamp((base[0] + fwd[0] * f + acr[0] * a, base[1],
                   base[2] + fwd[1] * f + acr[1] * a))


def _route(area, side, rng, k):
    """The waypoints a bot of squad `k` walks when it leads, the last one
    beside the enemy keep. front: one midpoint jittered 10 u across; flank:
    out 25-40 u to one side (squads alternate sides) at 30% and 70% of the
    way, in on the keep from that side. Every bot gets its own jitter."""
    own, foe, fwd, acr = _frame(area, side)
    dist = _xz(own, foe)
    kind = SQUAD_ROUTES[k % len(SQUAD_ROUTES)]
    sgn = 1.0 if (k // 2) % 2 == 0 else -1.0
    pts = []
    if kind == "flank":
        wide = sgn * rng.uniform(25.0, 40.0)
        for t in (0.3, 0.7):
            pts.append(_at(own, fwd, acr, dist * t + rng.uniform(-4.0, 4.0),
                           wide + rng.uniform(-3.0, 3.0)))
        pts.append(_at(foe, fwd, acr, -rng.uniform(0.0, 4.0),
                       sgn * rng.uniform(5.0, 9.0)))
    else:
        pts.append(_at(own, fwd, acr, dist * rng.uniform(0.4, 0.6),
                       rng.uniform(-10.0, 10.0)))
        pts.append(_at(foe, fwd, acr, -rng.uniform(0.0, 5.0),
                       rng.uniform(-7.0, 7.0)))
    return pts


def _post(area, side, rng):
    """A defender's post: 6-14 u in front of its own keep, up to 12 across."""
    own, _foe, fwd, acr = _frame(area, side)
    return _at(own, fwd, acr, rng.uniform(6.0, 14.0), rng.uniform(-12.0, 12.0))


def _slot(lead, b, foe):
    """Where squad member `b` walks: its FORM slot behind leader `lead`, in
    the frame of the leader's heading (toward where it walks, else the
    enemy keep), plus the member's own jitter."""
    to = lead.get("goal") if lead.get("moving") and lead.get("goal") else foe
    dx, dz = to[0] - lead["pos"][0], to[2] - lead["pos"][2]
    h = math.hypot(dx, dz)
    fwd = (dx / h, dz / h) if h > 1e-6 else (1.0, 0.0)
    acr = (-fwd[1], fwd[0])
    a, f = b["form"]
    return _at(lead["pos"], fwd, acr, f, a)


def _xz(a, b):
    return math.hypot(a[0] - b[0], a[2] - b[2])


def _step(cur, target, step):
    d = _xz(cur, target)
    if d <= step or d < 1e-6:
        return (target[0], cur[1], target[2])
    f = step / d
    return (cur[0] + (target[0] - cur[0]) * f, cur[1],
            cur[2] + (target[2] - cur[2]) * f)


# ---------------------------------------------------------------------------
# spawning and removal
# ---------------------------------------------------------------------------
def _card(args, bot):
    """What fepresence.avatar_body draws: the peer card, built for a bot."""
    pres = _pres()
    sides = {"atk": 0, "def": 1}             # buildings.WAR_SIDE
    ch = {"name": bot["name"], "sex": bot["sex"], "look1": bot["cls"],
          "f24": 0, "f28": 0, "look2": bot["looks"][0],
          "look3": bot["looks"][1], "look4": bot["looks"][2],
          "army": sides.get(bot["side"], 0), "force": bot["nation"]}
    want = _int(args, "presence_submask", 0x3FF) & 0xFFFFFFFF
    sub = want & pres._known_bits()
    m3, worn = 0, b""
    if str(getattr(args, "war_bot_gear", "on")) == "on":
        m3, worn = bot.get("gear") or (0, b"")
    return {"key": None, "sub": sub, "ident": pres.identity_bytes(ch, sub),
            "m3": m3, "worn": worn, "hp": None, "cls": bot["cls"],
            "name": bot["name"], "army": ch["army"], "force": bot["nation"]}


def _gear(bid, cls, sex):
    """((mask3, worn block), weapon uid) for a bot: its class's starting gear
    (fegamedata.starting_gear), worn where Equip would put it, under uids
    <bot id> * 1000 + n -- the shape real uids have (charid * 1000 + n), in
    the bot block's own range, so none can be a real player's item."""
    try:
        items = fw.fegamedata.starting_gear(cls, sex)
        tbl = fw.fegamedata.items()
    except Exception:                                  # noqa: BLE001
        return (0, b""), 0
    rows, taken, weapon = [], set(), 0
    for k, no in enumerate(items):
        slot = (tbl.get(int(no)) or {}).get("slot")
        if slot is None:
            continue
        w = fw.worn_index(slot, taken)
        if w is None or w in taken:
            continue
        taken.add(w)
        uid = int(bid) * 1000 + k + 1
        rows.append((uid, int(no), k, w, 1))
        if w in (0, 1) and not weapon:
            weapon = uid
    m3, body, _desc = fw.worn_gear_block(rows)
    return (m3, body), weapon


def _new_bot(args, area, side, nation, bid, i):
    role, cls = role_of(i)
    squad, place = squad_of(i)
    gen = next(_GEN)
    rng = random.Random("%s|%d|%s|%d|%d" % (SEED[0], int(area), side, bid, gen))
    home = _home(area, side, i)
    spread = max(0.0, min(0.9, _float(args, "war_bot_stat_spread", 0.25)))
    lvl = 1.0 + rng.uniform(-spread, spread)
    hpmax = max(1, min(0x7FFF, int(round(_int(args, "war_bot_hp", 1000)
                                         * lvl * CLASS_HP[cls]))))
    dmg = max(1, int(round(max(0, _int(args, "war_bot_damage", 40))
                           * lvl * CLASS_DMG[cls])))
    reach = max(0.5, _float(args, "war_bot_reach", 4.0))
    rng_ = reach if cls == 0 else max(
        reach, _float(args, "war_bot_range", 16.0) * rng.uniform(0.95, 1.15))
    vmax = max(0.0, _float(args, "war_bot_speed", 5.2))
    vmin = max(0.0, min(vmax, _float(args, "war_bot_speed_min", 4.6)))
    swing = max(0.1, _int(args, "war_bot_swing_ms", 2000) / 1000.0)
    a, f = FORM[place % len(FORM)]
    if role == "ranged":
        f -= 2.0                        # the bows and wands walk at the back
    bot = {"id": bid, "area": int(area), "side": side, "nation": int(nation),
           "i": i, "name": "%s %d" % (CLASS_NAMES[cls], bid),
           "cls": cls, "sex": (i // 3) % 2, "looks": (i % 4, (i // 2) % 4, i % 5),
           "home": home, "pos": home, "hp": hpmax,
           "hpmax": hpmax, "dead_until": None, "swing_at": 0.0, "goal": None,
           "moving": False, "face_to": None, "gen": gen,
           # SOLDIERS (module docstring): role, squad, own stats and path
           "role": role, "squad": squad, "place": place, "lvl": lvl,
           "dmg": dmg, "range": rng_,
           "aggro": max(_float(args, "war_bot_aggro", 15.0) * rng.uniform(0.9, 1.15),
                        rng_ + 3.0 if cls else 0.0),
           "speed": rng.uniform(vmin, vmax), "walk": 0.0,
           "swing_s": swing * rng.uniform(0.9, 1.15),
           "pick": PICKS[rng.randrange(len(PICKS))], "target": None,
           "target_until": 0.0, "route": _route(area, side, rng, squad),
           "wp": 0, "post": _post(area, side, rng) if role == "defend" else None,
           "form": (a + rng.uniform(-1.0, 1.0), f + rng.uniform(-1.0, 1.0)),
           "retreat": False, "pause_until": 0.0, "kite_left": KITE_SECS,
           "rng": rng,
           # the stagger: squad k marches (its first bot number) x
           # --war-bot-stagger-ms after it spawned, the whole squad at once
           # (an enemy in aggro starts a bot sooner)
           "wait_until": time.monotonic() + squad_start(squad) * max(
               0, _int(args, "war_bot_stagger_ms", 500)) / 1000.0,
           # swing = (count, aim point, when), the last swing, for the
           # screens to draw; died_at = when it fell, for the corpse
           "swing": None, "swing_n": 0, "died_at": None,
           # PHASE 3: its economy role (None, "miner", "builder"), what it
           # carries and has drawn this war, its summon form (None = a
           # soldier) and the soldier's stats while it is one
           "eco": eco_role(args, i), "crystals": 0, "drawn": 0,
           "draw_at": None, "draw_wait": False, "eco_busy": False,
           "eco_wait": 0.0, "morph": None, "kmult": 1.0}
    bot["gear"], bot["weapon"] = _gear(bid, cls, bot["sex"])
    if _pres() is not None:
        bot["card"] = _card(args, bot)
    return bot


def _free_ids(args, area, n, taken):
    """Up to `n` free bot ids, from the top of the block down."""
    lo, hi = id_range(args)
    live = _live_charids()
    top = _store_top()
    out = []
    for bid in range(hi, lo - 1, -1):
        if len(out) >= n:
            break
        if bid in taken:
            continue
        if bid in live or bid <= top:
            _once(("clash", lo), "bot id %d is a real charid (a live player, or "
                  "the store's highest charid is %d) -- skipped. The block "
                  "%d-%d is being reached by real charids: move --war-bot-ids "
                  "or --monster-base" % (bid, top, lo, hi))
            continue
        out.append(bid)
    if len(out) < n:
        _once(("short", lo, hi), "only %d bot id(s) free in %d-%d for %d wanted "
              "-- fewer bots" % (len(out), lo, hi, n))
    return out


def _fill(args, area, want):
    """Bring `area`'s bots to `want` a side: add (signed up) or remove (and
    their sign-ups). Returns (added, removed)."""
    camp = _camp()
    if camp is None:
        return 0, 0
    area = int(area)
    with _FILL_LOCK:
        r = camp.state_of(area)
        if r.get("phase") != camp.WAR:
            return 0, 0
        nations = {"atk": int(r.get("atk") or 0),
                   "def": int(fw.territory_owner(area, args) or 0)}
        with _LOCK:
            mine = _BOTS.setdefault(area, {})
            taken = {b for bots in _BOTS.values() for b in bots}
            have = {s: sorted((b for b, v in mine.items() if v["side"] == s),
                              key=lambda b: mine[b]["i"]) for s in ("atk", "def")}
            drop = []
            for s in ("atk", "def"):
                extra = len(have[s]) - want
                if extra > 0:
                    drop += have[s][-extra:]
            for b in drop:
                mine.pop(b, None)
        removed = camp.drop_bot_members(args, area, only=set(drop)) if drop else 0
        added = []
        for s in ("atk", "def"):
            need = want - len(have[s])
            if need <= 0:
                continue
            ids = _free_ids(args, area, need, taken)
            taken |= set(ids)
            used = {v["i"] for v in mine.values() if v["side"] == s}
            i = 0
            for bid in ids:
                while i in used:
                    i += 1
                used.add(i)
                bot = _new_bot(args, area, s, nations[s], bid, i)
                got = camp.sign_up(args, area, s,
                                   {"key": "bot|%d" % bid, "a": None, "c": bid,
                                    "n": bot["name"], "name": bot["name"],
                                    "bot": True}, persist=False)
                if got is None or got == "full":
                    _log("area %d: the %s side is full (or the war is over) -- "
                         "no more bots there" % (area, s))
                    break
                added.append(bot)
        if added:
            camp.save(args)
            with _LOCK:
                mine = _BOTS.setdefault(area, {})
                for bot in added:
                    mine[bot["id"]] = bot
        with _LOCK:
            if not _BOTS.get(area):
                _BOTS.pop(area, None)
    if added or drop:
        _log("area %d: %d bot(s) a side wanted -- %d added (%s), %d removed; "
             "ids %s" % (area, want, len(added),
                         ", ".join("%d %s" % (sum(1 for b in added
                                                  if b["side"] == s), s)
                                   for s in ("atk", "def")),
                         len(drop), "%d-%d" % id_range(args)))
    return len(added), removed


def despawn(args, area):
    """Every bot of `area` gone, with its sign-up. The sessions drawing them
    send 0x1004 on their own next pump."""
    camp = _camp()
    with _FILL_LOCK:
        with _LOCK:
            gone = _BOTS.pop(int(area), None) or {}
            _DRIVER.pop(int(area), None)
            _TICK.pop(int(area), None)
            _ECO_CTX.pop(int(area), None)
            _ECO_WAR.pop(int(area), None)
            for k in [k for k in _KEEP_LAST if k[0] == int(area)]:
                _KEEP_LAST.pop(k, None)
        n = camp.drop_bot_members(args, area) if camp is not None else 0
    if gone or n:
        _log("area %s: %d bot(s) removed, %d sign-up(s) withdrawn"
             % (area, len(gone), n))
    return len(gone)


def on_war_start(args, area):
    """fecampaign._advance, PREP -> WAR (never under fecampaign._LOCK)."""
    want = per_side(args)
    if want and getattr(args, "campaign", "off") == "on":
        _fill(args, area, want)


def on_war_end(args, area):
    """fecampaign._advance, WAR -> TRUCE before the decision, and TRUCE ->
    PEACE (never under fecampaign._LOCK)."""
    with _LOCK:
        mine = int(area) in _BOTS
    if mine:
        despawn(args, area)
    else:
        camp = _camp()
        if camp is not None:
            camp.drop_bot_members(args, area)


def reconcile(args, force=False, now=None):
    """Every war under way to --war-bots a side, every other field to none,
    and any bot sign-up this process has no bot for (a restart mid-war)
    withdrawn. At most once a second unless `force`."""
    now = time.monotonic() if now is None else now
    camp = _camp()
    with _LOCK:
        if not force and now - _RECONCILE[0] < RECONCILE_SECS:
            return
        _RECONCILE[0] = now
        first = not _RECONCILE[1]
        _RECONCILE[1] = True
        have = sorted(_BOTS)
    if camp is None or getattr(args, "campaign", "off") != "on":
        for area in have:
            despawn(args, area)
        return
    with camp._LOCK:
        wars = sorted(a for a, r in camp._STATE.items() if r["phase"] == camp.WAR)
        ghosts = sorted(a for a, r in camp._STATE.items()
                        if any(m.get("bot") for m in (r.get("members") or {}).values()))
    want = per_side(args)
    for area in have:
        if area not in wars or not want:
            despawn(args, area)
    for area in ghosts if (first or force or want) else ():
        # under _FILL_LOCK: a _fill signs its bots up BEFORE it lists them in
        # _BOTS, and must not have them withdrawn as ghosts in between
        with _FILL_LOCK:
            with _LOCK:
                keep = set((_BOTS.get(area) or {}).keys())
            n = camp.drop_bot_members(args, area, keep=keep)
        if n:
            _log("area %d: %d bot sign-up(s) with no bot behind them (a "
                 "restart?) withdrawn" % (area, n))
    if want:
        for area in wars:
            _fill(args, area, want)


def set_count(args, n, actor=""):
    """`!warbots N` / POST /admin/warbots: --war-bots on the running world.
    Returns the sentence the panel shows. More than the war cap less 5 is
    taken as that, and the sentence says so."""
    asked = max(0, int(n))
    camp = _camp()
    top = max(0, camp.war_cap(args) - 5) if camp is not None else asked
    n = min(asked, top)
    args.war_bots = n
    reconcile(args, force=True)
    _log("--war-bots %d by %s" % (n, actor or "?"))
    wars = []
    with _LOCK:
        rows = {a: sum(1 for _ in bots.values()) for a, bots in _BOTS.items()}
    try:
        names = fw.fegamedata.area_names_en()
    except Exception:                                  # noqa: BLE001
        names = {}
    for a, k in sorted(rows.items()):
        wars.append("%s: %d" % (names.get(a) or "Field %d" % a, k))
    if not n:
        return "War bots are off; every bot has left its war."
    cap = per_side(args)
    note = ""
    if asked > cap:
        note = (" (%d asked for; at most the war cap less 5, %d a side)"
                % (asked, cap))
    msg = "War bots: %d soldiers a side join every war%s" % (cap, note)
    if camp is None or getattr(args, "campaign", "off") != "on":
        return msg + ". The campaign is off, so no war is running."
    return msg + ". " + ("Bots in the field now - " + "; ".join(wars) + "."
                         if wars else "No war is under way; they join the next one.")


# ---------------------------------------------------------------------------
# hits
# ---------------------------------------------------------------------------
def hit_bot(args, bid, dmg, area=None, by=None, mob=None, now=None):
    """Take `dmg` off bot `bid`. {"hp", "hpmax", "dead", "side", "nation",
    "name"} after it, or None (no such live bot, or not in `area`). `mob` =
    the monster object id when a monster swung (the bot turns on it)."""
    now = time.monotonic() if now is None else now
    with _LOCK:
        for a, bots in _BOTS.items():
            b = bots.get(int(bid))
            if b is None:
                continue
            if (area is not None and int(area) != a) or b["dead_until"]:
                return None
            b["hp"] = max(0, int(b["hp"]) - max(0, int(dmg)))
            if mob is not None:
                b["mob_foe"] = (int(mob), now)
            dead = b["hp"] <= 0
            back = 0.0
            if dead:
                _fell(args, bots, b, now)
                back = b["dead_until"] - now
            out = {"hp": b["hp"], "hpmax": b["hpmax"], "dead": dead,
                   "side": b["side"], "nation": b["nation"], "name": b["name"]}
            break
        else:
            return None
    _log("bot %d (%s) took %d from %s -- HP %d/%d%s"
         % (int(bid), out["name"], int(dmg), by if by is not None else "?",
            out["hp"], out["hpmax"], " -- DOWN, back in %.0fs" % back
            if out["dead"] else ""))
    return out


def respawn_s(args, bots, side):
    """SOFT BALANCE (phase 2): how long a bot of `side` that falls now stays
    down. --war-bot-respawn-s, shortened for the side with fewer bots
    standing by (its standing + 1) / (the other's + 1), never under
    --war-bot-respawn-min of it. `bots` = the field's (under _LOCK)."""
    base = _float(args, "war_bot_respawn_s", 30.0)
    lo = max(0.05, min(1.0, _float(args, "war_bot_respawn_min", 0.4)))
    up = {"atk": 0, "def": 0}
    for v in bots.values():
        if not v["dead_until"]:
            up[v["side"]] = up.get(v["side"], 0) + 1
    mine = up.get(side, 0)
    theirs = up.get("def" if side == "atk" else "atk", 0)
    if theirs <= mine:
        return base
    return base * max(lo, (mine + 1.0) / (theirs + 1.0))


def _fell(args, bots, b, now):
    """Bot `b` is down (under _LOCK): when it stands up again."""
    b["dead_until"] = now               # down now, so it is not counted
    b["dead_until"] = now + respawn_s(args, bots, b["side"])
    b.update(moving=False, died_at=now, retreat=False, target=None,
             draw_at=None, draw_wait=False)
    if b.get("morph") is not None:
        # A SUMMON REVERTS ON DEATH (phase 3): the soldier's stats back now;
        # the corpse falls as the form and the respawn is a fresh spawn (a
        # new gen: 0x1004 + a soldier 0x1006 add, never an update)
        _log("bot %d's %s falls -- it stands up again a soldier"
             % (b["id"], BOT_FORMS.get(b["morph"], {}).get("name", b["morph"])))
        b.update(b.pop("human", None) or {})
        b.update(morph=None, kmult=1.0)


# ---------------------------------------------------------------------------
# MONSTERS AND BOTS (operator 2026-10-23). OURS, every number CHOSEN.
#   * the field's shared monsters (world/monsters.py) count a live bot as a
#     target alongside the real players: the monster driver chases the
#     nearest of either (monster_targets) and swings at a bot in reach when
#     no real player is in reach (monsters.monster_attack_bots), through
#     hit_bot here with the monster as the source. The bot's HP change and
#     its fall are drawn by _draw like any other, inside the budget.
#   * KEY: NOTHING DRAWS A MONSTER'S SWING. A monster's hit on a real player
#     is drawn ONLY as that player's own 0x2024 maskA 0x4 HP (player_hp_push;
#     the client draws old - new as the number). The server sends no attack
#     record under the monster's id and none naming a target, so a hit on a
#     bot sends nothing new either: the bot's 0x2024 HP is the equivalent.
#   * a bot hit by a monster, or with a living one within
#     --war-bot-monster-radius, fights it with its own swing (the 0x1032
#     through the budget, the standing turn by action 1); the damage goes
#     through combat.mob_damage_by_bot: the same HP and death as a player's
#     hit, but no ledger, no EXP, no gold, no chest, no kill tally, and NO
#     0x1006 HP push (the type-8 HP record is a 0x1006 on a unit already on
#     the screen, and it is not under this budget). A death is the normal
#     0x1004 relay to every screen showing the field; the driver respawns it.
#   * bots never hunt: once the monster is dead or out of the radius (and
#     the one that hit it is past MOB_FOE_SECS or 1.5 x the radius) the bot
#     goes back to its role. Monsters never touch keeps, sign-ups or results.
# ---------------------------------------------------------------------------
def monsters_on(args):
    return str(getattr(args, "war_bot_monsters", "on") or "on") == "on"


def monster_targets(args, area):
    """[(bot id, (x, y, z))] of the living bots in `area` a monster may chase
    and hit; [] under --war-bot-monsters off."""
    if not monsters_on(args) or area is None:
        return []
    with _LOCK:
        return [(b, tuple(v["pos"])) for b, v in
                sorted((_BOTS.get(int(area)) or {}).items())
                if not v["dead_until"]]


def _mobs_for(args, area, now):
    """[(obj, (x, y, z))] of the living monsters of `area` where the screens
    have them, read before _LOCK is taken (never nested with _MOB_LOCK)."""
    if not monsters_on(args) or getattr(args, "combat", "off") != "on":
        return []
    try:
        from world import monsters as _wm
    except Exception:                                  # noqa: BLE001
        return []
    return _wm.living_mobs(area, now)


def _mob_foe(b, mobs, rad, now):
    """(obj, pos, distance) of the monster bot `b` fights, or None: the one
    that hit it in the last MOB_FOE_SECS while within 1.5 x `rad`, else the
    nearest living one within `rad`. Never one further off (no hunting)."""
    if not mobs or rad <= 0:
        return None
    hit = b.get("mob_foe")
    if hit and now - hit[1] <= MOB_FOE_SECS:
        for obj, pos in mobs:
            if obj == hit[0]:
                d = _xz(b["pos"], pos)
                if d <= rad * 1.5:
                    return obj, pos, d
                break
    best = None
    for obj, pos in mobs:
        d = _xz(b["pos"], pos)
        if d <= rad and (best is None or d < best[2]):
            best = (obj, pos, d)
    return best


def near(area, pos, rng, only=None):
    """[(id, nation, side, xz distance)] of the live bots in `area` within
    `rng` of `pos` (fepvp's cast path). `only` = a targeted skill's target."""
    out = []
    with _LOCK:
        for bid, b in (_BOTS.get(int(area)) or {}).items():
            if b["dead_until"] or (only and int(only) != bid):
                continue
            d = _xz(pos, b["pos"])
            if d <= rng:
                out.append((bid, b["nation"], b["side"], d))
    return out


# ---------------------------------------------------------------------------
# PHASE 3: crystals, buildings, summons (module docstring, CRYSTALS)
# ---------------------------------------------------------------------------
def _other(side):
    return "def" if side == "atk" else "atk"


def _build_costs(args):
    """{building type: crystal price} a bot pays: --build-costs, or the 2006
    table when that is off (what a player would pay with it on)."""
    fwar = sys.modules.get("fewar")
    if fwar is not None:
        try:
            tbl = fwar.parse_costs(getattr(args, "build_costs", "off")) \
                or fwar.parse_costs("2006")
            return {int(t): int(v[0]) for t, v in (tbl or {}).items()}
        except Exception:                              # noqa: BLE001
            pass
    return {0: 18, 19: 18, 6: 15, 29: 15}


def _summon_price(args, area, side, fid, camp, unit):
    """The crystal price of summon `fid` for `side`: --summon-cost with the
    side's --pop-scale factor, as a player of that side pays it."""
    if unit is None:
        return {3: 40, 0: 30}.get(int(fid), 40)
    factor = 1.0
    try:
        if camp.pop_scales(args, "summon"):
            factor = camp.pop_factor(args, area, side)
    except Exception:                                  # noqa: BLE001
        factor = 1.0
    return int(unit.summon_cost(args, fid, factor)[0])


def _summon_room(args, area, side, fid, camp, unit, n_war):
    """(True, "") when `side`'s bots may summon `fid` now: under
    --war-bot-summons this war and under the players' own --summon-alive-cap
    (bots and players counted together) and --summon-war-cap."""
    most = max(0, _int(args, "war_bot_summons", 2))
    if n_war >= most:
        return False, "the %s side's bots have summoned %d (--war-bot-summons %d)" \
            % (side, n_war, most)
    if unit is None:
        return True, ""
    cap = unit.form_spec(getattr(args, "summon_alive_cap", "")).get(int(fid))
    if cap and cap[0] > 0 and unit.alive_count(area, side, int(fid)) >= cap[0]:
        return False, "--summon-alive-cap %d reached" % cap[0]
    wcap = unit.form_spec(getattr(args, "summon_war_cap", "")).get(int(fid))
    if wcap and wcap[0] > 0 and camp.summon_count(area, side, fid) >= wcap[0]:
        return False, "--summon-war-cap %d reached" % wcap[0]
    return True, ""


def _bot_summon_hp(args, fid, unit):
    """The full HP of a bot's summon: the Knight's HIGH row of
    --summon-knight-hp (a bot has no level; it stands for a soldier past
    Lv10), --summon-hp for the rest."""
    if int(fid) == 3:
        try:
            hi = int(str(getattr(args, "summon_knight_hp", "1800:2900:10")
                         or "1800:2900:10").split(":")[1], 0)
        except (IndexError, ValueError):
            hi = 2900
        return max(1, min(0x7FFF, hi))
    if unit is not None:
        try:
            return max(1, min(0x7FFF, int(unit.summon_hp(args, fid))))
        except Exception:                              # noqa: BLE001
            pass
    return 5400


def _eco_context(args, area, camp, keeps):
    """What the economy steps of `area` act on this tick, or None (economy
    off, no giant crystals in this war). Read before _LOCK: it takes
    fecampaign's lock and walks the sessions."""
    if not economy_on(args):
        return None
    try:
        left = camp.deposits(args, area)
        grids = camp.crystal_grids(area, len(left), args) if left else []
    except Exception:                                  # noqa: BLE001
        left, grids = [], []
    if not left:
        return None
    y = _ground(area)
    reserve = max(0.0, min(1.0, _float(args, "war_bot_deposit_reserve", 0.5)))
    deps = []
    for i, (row, g) in enumerate(zip(left, grids)):
        pos = (fw.grid_to_world(g[0]), y, fw.grid_to_world(g[1]))
        spots, dist = {}, {}
        for s in ("atk", "def"):
            k = keeps.get(s)
            if k is None:
                continue
            dist[s] = _xz(pos, k)
            # beside it on the side toward the own keep, inside the client's
            # 10 u draw radius
            spots[s] = _step(pos, k, DRAW_SPOT)
        deps.append({"idx": i, "pos": pos, "left": int(row[0]),
                     "amt": int(row[1]),
                     "keep": int(math.ceil(reserve * int(row[1]))),
                     "spot": spots, "dist": dist})
    with _LOCK:
        war = _ECO_WAR.setdefault(area, {"built": {}, "summons": {},
                                         "relay": {}})
        built, summ = dict(war["built"]), dict(war["summons"])
    foe_bld = {"atk": [], "def": []}
    for obj, row in sorted(camp.buildings_of(area).items()):
        if row.get("side") in ("atk", "def"):
            foe_bld[_other(row["side"])].append(
                (int(obj), (fw.grid_to_world(row["gx"]), y,
                            fw.grid_to_world(row["gz"]))))
    unit = sys.modules.get("feunit")
    costs = _build_costs(args)
    nb = max(0, _int(args, "war_bot_buildings", 3))
    plan = {}
    for s in ("atk", "def"):
        b_n, s_n = built.get(s, 0), summ.get(s, 0)
        build = None
        if b_n < nb:
            bt = BOT_BUILD_ORDER[b_n % len(BOT_BUILD_ORDER)]
            build = ("build", bt, costs.get(bt, 0))
        summon = None
        for fid in ((0, 3) if foe_bld[s] else (3,)):
            ok, _why = _summon_room(args, area, s, fid, camp, unit, s_n)
            if ok:
                summon = ("summon", fid,
                          _summon_price(args, area, s, fid, camp, unit))
                break
        if build and (summon is None or b_n <= s_n):
            plan[s] = build
        else:
            plan[s] = summon or build
    return {"deps": deps, "keeps": dict(keeps), "foe_bld": foe_bld,
            "plan": plan, "reserve": reserve,
            "step": max(1, _int(args, "crystal_draw", 1)),
            "per_war": max(0, _int(args, "crystal_war_max", 20)),
            "carry": max(0, _int(args, "crystal_carry_max", 50)),
            "summon_range": max(0.0, _float(args, "summon_range", 40.0)),
            "ev": []}


def _foe_near(b, bots, players, rad):
    """An enemy bot or player within `rad` of bot `b` (under _LOCK)."""
    for o in bots.values():
        if o["side"] != b["side"] and not o["dead_until"] \
                and _xz(b["pos"], o["pos"]) <= rad:
            return True
    return any(side != b["side"] and _xz(b["pos"], pos) <= rad
               for _cid, _ps, side, pos in players)


def _pick_deposit(eco, b):
    """The giant crystal miner `b` draws from: the nearest to its own keep
    among those on its own half (nearer its keep than the enemy's) that
    hold more than the players' reserve. None when there is none."""
    side, foe = b["side"], _other(b["side"])
    best = None
    for d in eco["deps"]:
        if d["left"] - d["keep"] < 1 or side not in d["spot"]:
            continue
        if foe in d["dist"] and d["dist"][foe] < d["dist"][side]:
            continue
        if best is None or d["dist"][side] < best["dist"][side]:
            best = d
    return best


def _builder_of(bots, side):
    """Its side's builder standing as a soldier, or None (under _LOCK)."""
    for v in bots.values():
        if v.get("eco") == "builder" and v["side"] == side \
                and not v["dead_until"] and v.get("morph") is None:
            return v
    return None


def _miner_step(args, area, b, bots, players, now, dt):
    """MINER (phase 3): draw at a giant crystal of its own half at the real
    pace, hand the crystals to the builder. False (a soldier for this step)
    when there is nothing to do, it is falling back, or an enemy is near."""
    eco = _ECO_CTX.get(area)
    if eco is None or b.get("morph") is not None or b["retreat"] \
            or _foe_near(b, bots, players, b["aggro"] * 0.6):
        b["draw_at"] = None
        return False
    room = eco["carry"] - b["crystals"]
    dep = None
    if b["drawn"] < eco["per_war"] and room > 0:
        dep = _pick_deposit(eco, b)
    if b["crystals"] > 0 and (dep is None or b["crystals"] >= HANDOFF_AT):
        bld = _builder_of(bots, b["side"])
        if bld is not None:
            b["draw_at"] = None
            if _xz(b["pos"], bld["pos"]) <= HANDOFF_REACH:
                b["moving"], b["goal"] = False, None
                # the carry cap holds as for a player trade (fetrade)
                n = max(0, min(b["crystals"], eco["carry"] - bld["crystals"]))
                if n:
                    b["crystals"] -= n
                    bld["crystals"] += n
                    eco["ev"].append(("handoff", b["id"], bld["id"], n))
                return True
            _walk(b, bld["pos"], HANDOFF_REACH * 0.8, b["speed"], dt)
            return True
    if dep is None:
        b["draw_at"] = None
        return False
    spot = dep["spot"][b["side"]]
    if _xz(b["pos"], spot) > 1.0:
        b["draw_at"] = None
        _walk(b, spot, 0.3, b["speed"], dt)
        return True
    # AT THE CRYSTAL: standing, faced at it (no kneel is drawn: the module
    # docstring's KNEEL), drawing on the client's own clock
    b["moving"], b["goal"], b["face_to"] = False, None, dep["pos"]
    if b.get("draw_at") is None:
        b["draw_at"] = now + CROUCH_S
        b["mine"] = dep["idx"]
    elif now >= b["draw_at"] and not b.get("draw_wait"):
        n = min(eco["step"], eco["per_war"] - b["drawn"], room)
        if n > 0:
            b["draw_wait"] = True
            eco["ev"].append(("draw", b["id"], dep["idx"], n))
    return True


def _giant_step(args, area, b, eco, now, dt):
    """A bot GIANT goes for the nearest enemy building (BOT_FORMS' building
    swing). False when the enemy has none: then it fights as a soldier."""
    blds = eco["foe_bld"].get(b["side"]) or []
    if not blds:
        return False
    obj, pos = min(blds, key=lambda t: _xz(b["pos"], t[1]))
    if _xz(b["pos"], pos) > GIANT_BLD_REACH:
        _walk(b, pos, GIANT_BLD_REACH * 0.8, b["speed"], dt)
        return True
    b["moving"], b["goal"], b["face_to"] = False, None, pos
    if now >= b["swing_at"]:
        b["swing_at"] = now + b["swing_s"]
        _swung(b, pos, now)
        eco["ev"].append(("bhit", b["id"], obj,
                          int(BOT_FORMS[0]["bld"])))
    return True


def _builder_step(args, area, b, bots, players, now, dt):
    """BUILDER (phase 3): hold its spot behind the keep for the miners and
    spend what they bring, a building and a summon in turn (the tick's
    plan). Summoned, it fights (a Giant goes for buildings)."""
    eco = _ECO_CTX.get(area)
    if eco is None:
        return False
    if b.get("morph") is not None:
        return b["morph"] == 0 and not b["retreat"] \
            and _giant_step(args, area, b, eco, now, dt)
    if b["retreat"]:
        return False
    if b.get("eco_busy"):
        b["moving"], b["goal"] = False, None
        return True
    if _foe_near(b, bots, players, b["aggro"] * 0.6):
        return False
    plan = eco["plan"].get(b["side"])
    if plan and b["crystals"] >= plan[2] and now >= b.get("eco_wait", 0.0):
        own = eco["keeps"].get(b["side"])
        reach = eco["summon_range"] * 0.8
        if plan[0] == "summon" and own is not None and reach > 0 \
                and _xz(b["pos"], own) > reach:
            _walk(b, own, reach * 0.9, b["speed"], dt)
            return True
        b["crystals"] -= plan[2]            # paid now, refunded on a refusal
        b["eco_busy"] = True
        b["moving"], b["goal"] = False, None
        eco["ev"].append((plan[0], b["id"], b["side"], plan[1], plan[2]))
        return True
    if _xz(b["pos"], b["home"]) > 1.0:
        _walk(b, b["home"], 0.5, b["speed"], dt)
    else:
        b["moving"], b["goal"] = False, None
    return True


ROLE_STEP["miner"] = _miner_step
ROLE_STEP["builder"] = _builder_step


def _build_cands(area, side, btype, seed):
    """Grid cells for a bot building of `btype`, nearest first: out from the
    own keep toward the enemy one (BOT_BUILD_DIST cells), up to 18 across,
    the across order shuffled by `seed`."""
    g = (fw.keep_grids(area) or {}).get(side)
    if not g:
        return []
    e = (fw.keep_grids(area) or {}).get(_other(side)) or (128, 128)
    dx, dz = float(e[0] - g[0]), float(e[1] - g[1])
    h = math.hypot(dx, dz)
    fx, fz = (dx / h, dz / h) if h > 1e-6 else (1.0, 0.0)
    ax, az = -fz, fx
    rng = random.Random(seed)
    out = []
    for d in BOT_BUILD_DIST.get(int(btype), (16, 20, 24)):
        across = [0, 6, -6, 12, -12, 18, -18]
        rng.shuffle(across)
        for a in across:
            out.append((int(round(g[0] + fx * d + ax * a)),
                        int(round(g[1] + fz * d + az * a))))
    return out


def _apply_form(b, fid, mx):
    """Bot `b` becomes summon `fid` (under _LOCK): the form's HP (its share
    of HP carried over, as a player's), damage, reach and pace; the
    soldier's kept for the revert (_fell)."""
    F = BOT_FORMS[int(fid)]
    b["human"] = {k: b[k] for k in ("hpmax", "dmg", "range", "speed", "aggro")}
    frac = max(0.0, min(1.0, b["hp"] / float(b["hpmax"] or 1)))
    b["hpmax"] = int(mx)
    b["hp"] = max(1, min(int(mx), int(round(mx * frac))))
    b["dmg"] = max(1, int(round(b["dmg"] * F["dmg"])))
    b["range"] = F["reach"]
    b["aggro"] = max(b["aggro"], F["reach"] + 8.0)
    b["speed"] = b["speed"] * F["speed"]
    b.update(kmult=F["keep"], morph=int(fid), target=None, eco_busy=False)


def _eco_events(args, area, camp, eco, now, floor):
    """The economy steps' side effects, outside _LOCK: draws (fecampaign.
    bot_draw), buildings (bot_build), summons, a Giant's hits on buildings.
    Returns what happened, for a test."""
    out = []
    unit = sys.modules.get("feunit")
    for ev in eco["ev"]:
        kind = ev[0]
        if kind == "handoff":
            _, frm, to, n = ev
            _log("area %d: miner %d hands %d crystal(s) to builder %d"
                 % (area, frm, n, to))
            out.append(ev)
        elif kind == "draw":
            _, bid, idx, n = ev
            got = camp.bot_draw(args, area, idx, n, eco["reserve"])
            take, left, amt = got if got else (0, None, None)
            with _LOCK:
                b = (_BOTS.get(area) or {}).get(bid)
                if b is not None:
                    b["draw_wait"] = False
                    if take:
                        b["crystals"] += take
                        b["drawn"] += take
                        b["draw_at"] = now + (DRAW_SLOW_S if b["crystals"]
                                              > DRAW_SLOW_OVER else DRAW_S)
                    else:
                        b["draw_at"] = None     # spent to the reserve: re-pick
                    carried = b["crystals"]
                war = _ECO_WAR.setdefault(area, {"built": {}, "summons": {},
                                                 "relay": {}})
                due = take and left is not None and (
                    now - war["relay"].get(idx, -1e9) >= DEP_RELAY_SECS)
                if due:
                    war["relay"][idx] = now
            if take:
                _log("area %d: miner %d drew %d from giant crystal %d (%d/%d "
                     "left, reserve %.0f%%) -- carries %d"
                     % (area, bid, take, idx, left, amt, eco["reserve"] * 100,
                        carried if b is not None else -1))
            if due:
                # the level the players' own draws relay (on_crystal_relay)
                fw.ext_post("campaign.crystal",
                            {"area": int(area),
                             "obj": camp._crystal_base(args) + int(idx),
                             "left": int(left)}, include_me=True)
            out.append(("drew", bid, idx, take, left))
        elif kind == "build":
            _, bid, side, btype, cost = ev
            fwar = sys.modules.get("fewar")
            hp = (fwar.build_hp(args, btype) if fwar is not None else None) \
                or (0, 0)
            try:
                if hp[1] and camp.pop_scales(args, "hp"):
                    f = camp.pop_factor(args, area, side)
                    if f < 1.0:
                        hp = tuple(max(1, int(round(h * f))) for h in hp)
            except Exception:                          # noqa: BLE001
                pass
            with _LOCK:
                n_b = (_ECO_WAR.get(area) or {}).get("built", {}).get(side, 0)
            obj, g = camp.bot_build(
                args, area, side, btype, BOT_BUILD_MODEL.get(int(btype), 0),
                _build_cands(area, side, btype, "%s|%d|%s|%d" % (
                    SEED[0], area, side, n_b)), hp)
            with _LOCK:
                b = (_BOTS.get(area) or {}).get(bid)
                war = _ECO_WAR.setdefault(area, {"built": {}, "summons": {},
                                                 "relay": {}})
                if obj is not None:
                    war["built"][side] = war["built"].get(side, 0) + 1
                if b is not None:
                    b["eco_busy"] = False
                    if obj is None:
                        b["crystals"] += cost
                        b["eco_wait"] = now + ECO_RETRY_S
            if obj is None:
                _log("area %d: builder %d could not put up type %d: %s -- "
                     "crystals back, again in %.0fs"
                     % (area, bid, btype, g, ECO_RETRY_S))
                out.append(("build_refused", bid, btype, g))
            else:
                _log("area %d: builder %d put up %s %d at grid %s for %d "
                     "crystal(s)" % (area, bid, fw.BUILDING_TYPES.get(
                         int(btype), btype), obj, g, cost))
                out.append(("built", bid, obj, btype, g))
        elif kind == "summon":
            _, bid, side, fid, cost = ev
            with _LOCK:
                n_s = (_ECO_WAR.get(area) or {}).get("summons", {}).get(side, 0)
            ok, why = _summon_room(args, area, side, fid, camp, unit, n_s)
            mx = _bot_summon_hp(args, fid, unit)
            with _LOCK:
                b = (_BOTS.get(area) or {}).get(bid)
                if b is None or b["dead_until"] or b.get("morph") is not None:
                    ok, why = False, why or "the builder is down"
                if b is not None:
                    b["eco_busy"] = False
                    if ok:
                        _apply_form(b, fid, mx)
                        war = _ECO_WAR.setdefault(
                            area, {"built": {}, "summons": {}, "relay": {}})
                        war["summons"][side] = war["summons"].get(side, 0) + 1
                    else:
                        b["crystals"] += cost
                        b["eco_wait"] = now + ECO_RETRY_S
            if ok:
                camp.summon_add(args, area, side, fid, credit=False)
                _log("area %d: builder %d SUMMONS a %s for the %s side (%d "
                     "crystal(s), HP %d) -- nobody credited"
                     % (area, bid, BOT_FORMS[fid]["name"], side, cost, mx))
                out.append(("summoned", bid, fid))
            else:
                _log("area %d: builder %d may not summon a %s: %s"
                     % (area, bid, BOT_FORMS.get(fid, {}).get("name", fid), why))
                out.append(("summon_refused", bid, fid, why))
        elif kind == "bhit":
            _, bid, obj, dmg = ev
            res = camp.bot_building_hit(args, area, obj, dmg, floor)
            out.append(("bhit", bid, obj, dmg, res[0] if res else None))
    eco["ev"] = []
    return out


# ---------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------
def _claim(area, now):
    me = fw._SESSION.get("charid")
    if me is None:
        return False
    with _LOCK:
        d = _DRIVER.get(int(area))
        if d and d[0] != int(me) and now - d[1] < DRIVER_SECS:
            return False
        _DRIVER[int(area)] = (int(me), now)
    return True


def _players(args, area, camp):
    """[(charid, session, side, pos)] of the living real players outdoors in
    `area` who are on a side of its war (their member row, else their
    nation's)."""
    sides = camp.member_sides(area)
    r = camp.state_of(area)
    atk = int(r.get("atk") or 0)
    holder = int(fw.territory_owner(area, args) or 0)
    out = []
    for ent in fw.ext_sessions():
        ps = ent["session"]
        try:
            cid = int(ps.get("charid") or 0)
        except (TypeError, ValueError):
            continue
        if not cid or not ps.get("in_field") or ps.get("player_dead"):
            continue
        if ps.get("field") != area or ps.get("room", -1) != -1:
            continue
        pos = ps.get("cpos") or camp._peer_xyz(ps)
        if not pos:
            continue
        side = sides.get(cid)
        if side is None:
            n = camp._peer_nation(ps)
            side = "atk" if n and n == atk else "def" if n and n == holder else None
        if side is None:
            continue
        out.append((cid, ps, side, tuple(pos)))
    return out


def _swung(b, aim, now):
    """Note a swing on bot `b` (under _LOCK): each screen's _draw sends it
    once, as its own 0x1032."""
    b["swing_n"] = int(b.get("swing_n", 0) or 0) + 1
    b["swing"] = (b["swing_n"], tuple(aim), now)


def _leaders(bots):
    """{(side, squad): (leader id, the squad's walking speed)}: the first
    live member by place that is not falling back leads; it walks at the
    slowest such member's speed so the squad keeps together. Defend squads
    have none (each defender holds its own post)."""
    out = {}
    for bid in sorted(bots, key=lambda x: (bots[x]["place"], x)):
        b = bots[bid]
        if b["dead_until"] or b["retreat"] or b["role"] == "defend":
            continue
        if b.get("eco") and b.get("morph") is None \
                and _ECO_CTX.get(b["area"]) is not None:
            continue                    # at its job, not leading a squad
        key = (b["side"], b["squad"])
        lead, spd = out.get(key, (bid, b["speed"]))
        out[key] = (lead, min(spd, b["speed"]))
    return out


def _walk(b, goal, stop, spd, dt):
    """Step bot `b` toward `goal`, halting `stop` short of it. False when it
    stands (already there, or no speed)."""
    d = _xz(b["pos"], goal)
    if d <= stop or spd <= 0:
        b["moving"] = False
        return False
    # `goal` from here on is where it will HALT (stop short of the target),
    # so a screen's lookahead never runs past it
    dest = _step(b["pos"], goal, d - stop)
    b["pos"] = _step(b["pos"], dest, spd * dt)
    b["goal"], b["moving"], b["face_to"], b["walk"] = dest, True, None, spd
    return True


def _pick(b, cands, now):
    """The enemy bot `b` turns on, by its own way of picking: the nearest,
    the weakest (lowest HP share), or a random one held PICK_HOLD_SECS while
    it stays in reach. `cands` = [(kind, key, pos, hp share, distance)]."""
    if not cands:
        b["target"] = None
        return None
    how = b.get("pick", "near")
    if how == "random":
        held = b.get("target")
        if held is not None and now < b.get("target_until", 0.0):
            for c in cands:
                if (c[0], c[1] if c[0] == "bot" else c[1][0]) == held:
                    return c
        c = cands[b["rng"].randrange(len(cands))]
        b["target"] = (c[0], c[1] if c[0] == "bot" else c[1][0])
        b["target_until"] = now + PICK_HOLD_SECS
        return c
    if how == "weak":
        return min(cands, key=lambda c: (c[3], c[4]))
    return min(cands, key=lambda c: c[4])


def tick(args, area, now=None):
    """One step of every bot in `area`. Runs on the driver's thread; also
    called by the harness directly. Returns what it did, for a test."""
    camp = _camp()
    now = time.monotonic() if now is None else now
    area = int(area)
    if camp is None or camp.phase_of(area) != camp.WAR:
        return []
    with _LOCK:
        last = _TICK.get(area)
        _TICK[area] = now
    dt = 0.0 if last is None else max(0.0, min(1.0, now - last))
    players = _players(args, area, camp)
    kreach = _float(args, "war_bot_keep_reach", 12.0)
    kdmg = max(0, _int(args, "war_bot_keep_damage", 1))
    kite = max(0.0, _float(args, "war_bot_kite", 5.0))
    guard = _float(args, "war_bot_guard", 25.0)
    low = max(0.0, min(0.95, _float(args, "war_bot_retreat", 0.25)))
    regen = max(0.0, _float(args, "war_bot_regen", 0.02))
    pause_p = max(0.0, _float(args, "war_bot_pause", 0.1))
    keeps = {s: keep_pos(area, s) for s in ("atk", "def")}
    # MONSTERS AND BOTS: the field's living monsters, read under the monster
    # lock BEFORE _LOCK (the two are never held together)
    mobs = _mobs_for(args, area, now)
    mrad = max(0.0, _float(args, "war_bot_monster_radius", 9.0))
    # PHASE 3: the deposits, buildings, prices and caps the miners and the
    # builder act on, read before _LOCK (they take fecampaign's own lock)
    eco = _eco_context(args, area, camp, keeps)
    if eco is None:
        _ECO_CTX.pop(area, None)
    else:
        _ECO_CTX[area] = eco
    did = []
    with _LOCK:
        bots = _BOTS.get(area) or {}
        leaders = _leaders(bots)
        # leaders step first, so their squads follow where they are now
        for bid in sorted(bots, key=lambda x: (bots[x]["place"], x)):
            b = bots[bid]
            if b["dead_until"]:
                if now >= b["dead_until"]:
                    b.update(dead_until=None, hp=b["hpmax"], pos=b["home"],
                             goal=None, moving=False, swing_at=now,
                             gen=next(_GEN), died_at=None, swing=None,
                             retreat=False, wp=0, target=None,
                             pause_until=0.0, kite_left=KITE_SECS,
                             mob_foe=None)
                    did.append(("respawn", bid))
                continue
            step = ROLE_STEP.get(b.get("eco") or b["role"])
            if step is not None and step(args, area, b, bots, players, now, dt):
                continue
            enemy = "def" if b["side"] == "atk" else "atk"
            own = keeps.get(b["side"])
            # ---- FALLING BACK: under --war-bot-retreat of its HP it walks
            # home, heals there, and rejoins at 90%
            if not b["retreat"] and low and b["hp"] < low * b["hpmax"]:
                b.update(retreat=True, target=None, pause_until=0.0)
                did.append(("retreat", bid))
            if b["retreat"]:
                if _xz(b["pos"], b["home"]) <= 3.0:
                    b["moving"] = False
                    if dt > 0 and regen > 0:
                        b["hp"] = min(b["hpmax"], b["hp"] + max(
                            1, int(round(regen * b["hpmax"] * dt))))
                    if b["hp"] >= 0.9 * b["hpmax"]:
                        b["retreat"] = False
                        did.append(("rally", bid))
                else:
                    _walk(b, b["home"], 0.5, b["speed"], dt)
                continue
            # ---- MONSTERS: one that hit it, or one in its way, is fought
            # with its normal swing; none further off (bots do not hunt)
            foe = _mob_foe(b, mobs, mrad, now)
            if foe is not None:
                obj, mpos, d = foe
                if d <= b["range"]:
                    b["moving"], b["goal"], b["face_to"] = False, None, mpos
                    if now >= b["swing_at"]:
                        b["swing_at"] = now + b["swing_s"]
                        _swung(b, mpos, now)
                        did.append(("mob", bid, obj, b["dmg"]))
                else:
                    _walk(b, mpos, b["range"] * 0.9, b["speed"], dt)
                continue
            # ---- the enemies it may turn on
            cands = []
            for oid in sorted(bots):
                o = bots[oid]
                if o["side"] == b["side"] or o["dead_until"]:
                    continue
                d = _xz(b["pos"], o["pos"])
                if b["role"] == "defend":
                    ok = d <= b["range"] or (own is not None
                                             and _xz(o["pos"], own) <= guard)
                else:
                    ok = d <= b["aggro"]
                if ok:
                    cands.append(("bot", oid, o["pos"],
                                  o["hp"] / float(o["hpmax"] or 1), d))
            for cid, ps, side, pos in players:
                if side != enemy:
                    continue
                d = _xz(b["pos"], pos)
                if b["role"] == "defend":
                    ok = d <= b["range"] or (own is not None
                                             and _xz(pos, own) <= guard)
                else:
                    ok = d <= b["aggro"]
                if ok:
                    try:
                        share = float(ps.get("player_hp", 1000)) / 1000.0
                    except (TypeError, ValueError):
                        share = 1.0
                    cands.append(("pc", (cid, ps), pos, share, d))
            if cands and b["cls"] and kite and b.get("morph") is None:
                # ---- a RANGED bot steps back from an enemy too close, for
                # at most KITE_SECS, then stands and shoots
                near_ = min(cands, key=lambda c: c[4])
                if near_[4] < kite and b["kite_left"] > 0:
                    b["kite_left"] -= dt
                    dx = b["pos"][0] - near_[2][0]
                    dz = b["pos"][2] - near_[2][2]
                    h = math.hypot(dx, dz)
                    if h < 1e-6:
                        dx, dz, h = 1.0, 0.0, 1.0
                    back = kite + 3.0 - near_[4]
                    away = _clamp((b["pos"][0] + dx / h * back, b["pos"][1],
                                   b["pos"][2] + dz / h * back))
                    _walk(b, away, 0.1, b["speed"], dt)
                    did.append(("kite", bid))
                    continue
            tgt = _pick(b, cands, now)
            if tgt is not None:
                kind, key, tpos, _share, d = tgt
                if d <= b["range"]:
                    b["moving"], b["goal"], b["face_to"] = False, None, tpos
                    if now < b["swing_at"]:
                        continue
                    b["swing_at"] = now + b["swing_s"]
                    b["kite_left"] = KITE_SECS
                    _swung(b, tpos, now)
                    if kind == "bot":
                        o = bots[key]
                        o["hp"] = max(0, int(o["hp"]) - b["dmg"])
                        if o["hp"] <= 0:
                            _fell(args, bots, o, now)
                            did.append(("kill", bid, key))
                        else:
                            did.append(("hit", bid, key, b["dmg"]))
                    else:
                        did.append(("pc", bid, key[0], key[1], b["dmg"],
                                    b["name"]))
                    continue
                _walk(b, tpos, b["range"] * 0.9, b["speed"], dt)
                continue
            # ---- nothing to fight: march (or hold)
            if now < (b.get("wait_until") or 0.0) or now < b["pause_until"]:
                # the stagger, or a short pause
                b["moving"], b["goal"] = False, None
                continue
            if b["moving"] and pause_p and b["rng"].random() < pause_p * dt:
                b["pause_until"] = now + b["rng"].uniform(0.6, 2.0)
                b["moving"], b["goal"] = False, None
                did.append(("pause", bid))
                continue
            if b["role"] == "defend":
                _walk(b, b["post"], 0.5, b["speed"], dt)
                continue
            k = keeps.get(enemy)
            if k is None:
                b["moving"] = False
                continue
            kr = max(kreach, b["range"]) if b["cls"] else kreach
            if _xz(b["pos"], k) <= kr:
                b["moving"], b["goal"], b["face_to"] = False, None, k
                if kdmg and now >= b["swing_at"]:
                    b["swing_at"] = now + b["swing_s"]
                    _swung(b, k, now)
                    # a summoned bot hits harder (BOT_FORMS), floored alike
                    did.append(("keep", bid, enemy, max(1, int(round(
                        kdmg * float(b.get("kmult") or 1.0))))))
                continue
            lead, spd = leaders.get((b["side"], b["squad"]), (bid, b["speed"]))
            if lead != bid:
                _walk(b, _slot(bots[lead], b, k), 0.6, b["speed"], dt)
                continue
            route = b["route"]
            while b["wp"] < len(route) - 1 and _xz(b["pos"], route[b["wp"]]) <= 2.0:
                b["wp"] += 1
            _walk(b, route[b["wp"]], 0.5, spd, dt)
    # the side effects run outside _LOCK: other sessions' queues, and the
    # campaign's own lock
    floor = max(0.0, min(1.0, _float(args, "war_bot_keep_floor", 0.5)))
    kills = []
    for ev in did:
        if ev[0] == "pc":
            _, bid, cid, ps, n, name = ev
            # include_me: the driving session may be the victim itself
            fw.ext_post("pvp.hit", {"from": bid, "to": cid, "dmg": n, "skill": 0,
                                    "model": "flat", "bot": bid, "name": name},
                        to=lambda s_, _n, ps=ps: s_ is ps, include_me=True)
        elif ev[0] == "keep":
            _, bid, side, n = ev
            # PLAYERS DECIDE THE WAR: a bot never takes a keep below the floor
            new = camp.keep_damage(args, area, side, n, credit=False,
                                   floor=floor)
            if new is not None and _KEEP_LAST.get((area, side)) != int(new):
                _KEEP_LAST[(area, side)] = int(new)
                obj = _int(args, "keep_base", 2900) + (0 if side == "def" else 1)
                fw.ext_post("campaign.keep", {"area": area, "obj": obj,
                                              "side": side, "hp": int(new)},
                            include_me=True)
        elif ev[0] == "mob":
            # the monster's HP and death through combat's own path, credited
            # to nobody (combat.mob_damage_by_bot)
            _, bid, obj, n = ev
            try:
                from world import combat as _wc
                res = _wc.mob_damage_by_bot(args, area, obj, n, bid)
            except Exception as e:                     # noqa: BLE001
                res = None
                _once(("mobhit", type(e).__name__),
                      "a bot's hit on a monster failed: %r" % (e,))
            if res and res.get("dead"):
                kills.append(("mob_kill", bid, obj))
                _log("area %d: bot %d felled monster %d (%s) -- no EXP, no "
                     "chest, nobody credited" % (area, bid, obj, res["name"]))
        elif ev[0] == "kill":
            _log("area %d: bot %d felled bot %d" % (area, ev[1], ev[2]))
        elif ev[0] in ("retreat", "rally"):
            _log("area %d: bot %d %s" % (area, ev[1], "falls back to heal"
                                         if ev[0] == "retreat" else
                                         "is healed and rejoins"))
    if eco is not None:
        kills += _eco_events(args, area, camp, eco, now, floor)
    return did + kills


# ---------------------------------------------------------------------------
# drawing, on each session's own thread
# ---------------------------------------------------------------------------
def _send(ctx, mid, body, obj):
    fw.send(ctx.conn, ctx.outbound, fw.inner_msg(mid, body, obj), ctx.mode,
            ctx.be, ctx.args.seq_mode == "echo",
            getattr(ctx.args, "world_prefix", 4))


def move_body(s, pos, speed, state=0, now=None):
    """A 28-byte 0x2023 action-0 record for a bot, stamped NOW in THIS
    client's clock: state 0 = walk, 1 = the stop, the speed pair a
    multiplier on the war-field row (kept on a stop, as a real record keeps
    it), the real records' tail 0x00080000. `pos` is what the caller wants
    drawn: for a walk, where the bot will be one cadence on (KEY above)."""
    out = bytearray(fw._MV_LEN)
    struct.pack_into(">I", out, fw._MV_ACTION, 0)
    struct.pack_into(">H", out, fw._MV_STATE, int(state) & 0xFFFF)
    struct.pack_into(">hh", out, fw._MV_SPD,
                     fw._i16(speed / WAR_FIELD_SPEED * 1000.0), 0)
    struct.pack_into(">hhh", out, fw._MV_POS, fw._i16(pos[0] * 10.0),
                     fw._i16(pos[1] * 10.0), fw._i16(pos[2] * 10.0))
    struct.pack_into(">I", out, fw._MV_TAIL, 0x00080000)
    body = None
    pres = _pres()
    if pres is not None:
        body = pres.rebase_clock(bytes(out), 0.0, s, mode="now", now=now)
    if body is None:
        mine = s.get("pres_mv") or b""
        if len(mine) >= fw._MV_A + 8:
            out[fw._MV_A:fw._MV_A + 8] = mine[fw._MV_A:fw._MV_A + 8]
        body = bytes(out)
    return body


def swing_body(s, bid, n, cls, aim, weapon, now=None, skill=None):
    """(body, clock source) of the 0x1032 SKILL USE drawing swing number `n`
    of bot `bid` on THIS client: a real client's basic-attack record field
    for field (module docstring), its start in the receiver's cast clock and
    its end the skill's windup + active + recovery later. `skill` = a
    summoned bot's form skill (FORM_SWING) instead of the class's."""
    if skill is None:
        skill = BASIC_ATTACK[int(cls) % len(BASIC_ATTACK)]
    pres = _pres()
    a, src = pres.cast_clock(s, now) if pres is not None else (0, "none")
    try:
        sc = fw.fegamedata.skill_combat(skill) or {}
    except Exception:                                  # noqa: BLE001
        sc = {}
    dur = sum(int(sc.get(k, 0) or 0) for k in ("windup", "active", "recovery"))
    body = struct.pack(">IIBIIHBII", a & 0xFFFFFFFF, (a + dur) & 0xFFFFFFFF, 1,
                       int(bid) & 0xFFFFFFFF, int(n) & 0xFFFFFFFF, skill, 1, 0,
                       int(weapon or 0) & 0xFFFFFFFF)
    return body + struct.pack(">fff", *(float(v) for v in aim)), src


def _hp_body(hp):
    """0x2024 maskA 0x4, the NEW hp as a u16: byte for byte what
    building_hp_push sends, and what fepvp already sends a peer's avatar
    (the setter stores it and draws old - new as the number). A bot's HP is
    1..32767 (--war-bot-hp is clamped there), so the field never wraps."""
    return struct.pack(">IIh", 0x4, 0, max(0, int(hp)) & 0x7FFF)


def form_body(word):
    """0x203D UNIT PROFILE bit 0x10, the u16 form word: what feunit.
    peer_relay sends a real summoner's peers (and fepresence a peer drawn
    while summoned). Not a 0x1006: no "already exists" rebuild of the
    avatar, but a one-hot word does rebuild the model (0x506c7d0) once."""
    return struct.pack(">IH", 0x10, int(word) & 0xFFFF)


def _hpmax_body(hp, hpmax):
    """0x2024 maskA 0x2 | 0x4: the max (i16) then the current (i16), the
    order feunit.hp_push sends a summoner's own."""
    return struct.pack(">IIhh", 0x6, 0, max(1, min(0x7FFF, int(hpmax))),
                       max(0, min(0x7FFF, int(hp))))


def _face(frm, to):
    dx, dz = to[0] - frm[0], to[2] - frm[2]
    h = math.hypot(dx, dz)
    return None if h < 1e-6 else (dx / h, 0.0, dz / h)


#: 0x2023 action 1's own duration, B - A in ms (anything > 0 is queued)
TURN_MS = 100


def turn_body(pos, face):
    """0x2023 ACTION 1, place + face (see the KEY in _draw): the unit at
    `pos`, facing `face` (x, 0, z) -- no model work, unlike any 0x1006."""
    return struct.pack(">III3f3f", 1, 0, TURN_MS, float(pos[0]), float(pos[1]),
                       float(pos[2]), float(face[0]), 0.0, float(face[2]))


def _face_rec(mode, bid, pos, face):
    """(mid, body) of one turn: action 1, or the old rebuilding 0x1006."""
    if mode == "rebuild":
        return 0x1006, _pres().facing_body(bid, face)
    return 0x2023, turn_body(pos, face)


def _turn_to(st, pos, to):
    """The facing a turn toward `to` needs, or None when the drawn facing is
    within ~20 degrees of it already (or there is nothing to face)."""
    face = _face(pos, to) if to else None
    if face and (st["face"] is None
                 or face[0] * st["face"][0] + face[2] * st["face"][2] < 0.94):
        return face
    return None


def _viewer_pos(s):
    """Where this screen's own player stands: its cpos, else its last walk
    record. None when neither is known."""
    pos = s.get("cpos")
    if pos:
        return tuple(pos)
    camp = _camp()
    return camp._peer_xyz(s) if camp is not None else None


def _room(s, args, now):
    """How many bot messages this screen may still be sent on this pump: the
    per-second budget (--war-bot-msg-rate) less what went out in the last
    second, and never more than a quarter of it on one pump so a second's
    worth is spread over the ~4 pumps of that second."""
    rate = max(1, _int(args, "war_bot_msg_rate", 20))
    log = s.get("wb_sent")
    if log is None:
        log = s["wb_sent"] = collections.deque()
    while log and now - log[0] >= 1.0:
        log.popleft()
    return max(0, min(rate - len(log), -(-rate // 4)))


def _draw(ctx, args, area, now):
    """Reconcile what this client shows with the bots of the field it stands
    in, within --war-bot-view of its player and --war-bot-msg-rate a second:
    0x1004 for one that died, left or went out of view, the fall for one
    that was felled, 0x1006 for a new one in view (nearest first, at most
    ADDS_PER_PUMP a pump), 0x2023 for one that walks (nearest first), the HP
    change and the swing last. What the budget leaves out is rebuilt from
    the bot's state on the next pump."""
    s = fw._SESSION
    pres = _pres()
    key = pres._key(s) if pres is not None else None
    here = key if (key is not None and area is not None and key[0] == area
                   and key[1] == -1) else None
    if s.get("wb_at") != here or s.get("wb_epoch") != pres._EPOCH[0]:
        # a new field (or none): the client dropped the scene, ours with it
        s["wb_at"], s["wb_epoch"], s["wb_seen"] = here, pres._EPOCH[0], {}
    seen = s.setdefault("wb_seen", {})
    if here is None or not s.get("pres_mv"):
        return
    # KEY: ONLY A PLAYER ENLISTED IN THIS WAR SEES ITS BOTS (operator,
    # 2026-10-23). LIVE that day a player refused at the briefing (the King's-
    # message gate) sat on the War Prepare window -- 0x2084 a second, sent
    # out of the field by the client's own 0x1021 handling -- while every
    # pump kept drawing bots, moves and falls at them, and their client hung
    # (14:37:46Z, cause unread). Nothing is sent to a session that has not
    # joined; it is drawn the pump after it does.
    camp = _camp()
    cid = s.get("charid")
    try:
        enlisted = camp is not None and cid is not None \
            and int(cid) in camp.member_sides(area)
    except Exception:                                  # noqa: BLE001
        enlisted = False
    if not enlisted:
        if not s.get("wb_unjoined"):
            s["wb_unjoined"] = True
            _log("charid %s is in area %s but not enlisted in its war -- no war "
                 "bots drawn for them" % (cid, area))
        return
    s.pop("wb_unjoined", None)
    me = _viewer_pos(s)
    view = max(1.0, _float(args, "war_bot_view", 50.0))
    # a felled bot stays drawn (as a body) for --war-bot-corpse-s
    corpse = max(0.0, _float(args, "war_bot_corpse_s", 5.0))
    with _LOCK:
        live = {b: {"pos": tuple(v["pos"]), "moving": v["moving"],
                    "goal": v["goal"], "hp": v["hp"], "hpmax": v["hpmax"],
                    "face_to": v["face_to"], "card": v.get("card"),
                    "gen": v["gen"], "dead": bool(v["dead_until"]),
                    "swing": v.get("swing"), "cls": v["cls"],
                    "weapon": v.get("weapon", 0), "morph": v.get("morph"),
                    # the speed it really walks (its own, or its squad's)
                    "speed": v.get("walk") or v.get("speed") or 0.0}
                for b, v in (_BOTS.get(area) or {}).items()
                if not v["dead_until"]
                or (corpse and v.get("died_at") is not None
                    and now - v["died_at"] < corpse)}

    def dist(pos):
        return _xz(me, pos) if me is not None else float("inf")
    vmax = max(0.0, _float(args, "war_bot_speed", 5.2))

    def speed_of(v):
        """A bot's walk, never faster than --war-bot-speed (5.2 u/s)."""
        return min(vmax, float(v["speed"] or vmax))
    cadence = max(0.1, _int(args, "war_bot_move_ms", 400) / 1000.0)
    anim = str(getattr(args, "war_bot_swing_anim", "on")) == "on"
    sanim = str(getattr(args, "war_bot_summon_anim", "on")) == "on"
    # ---- what this screen is owed, as (tier, distance, id, kind)
    items = []
    for bid, st in seen.items():
        v = live.get(bid)
        if v is None or st["gen"] != v["gen"]:
            items.append((0, 0.0, bid, "del"))
            continue
        d = dist(v["pos"])
        if me is not None and d > view + VIEW_HYST:
            items.append((0, d, bid, "del"))
        elif v["dead"]:
            if not st.get("dead"):
                items.append((0, d, bid, "fall"))
        else:
            if v["moving"] and v["goal"] is not None:
                if not st["moving"] or now - st["t"] >= cadence * 0.95:
                    items.append((2, d, bid, "move"))
            elif st["moving"]:
                items.append((2, d, bid, "stop"))
            if v["morph"] != st.get("morph") or v["hpmax"] != st.get(
                    "hpmax", v["hpmax"]):
                # PHASE 3: a summon (or its revert) -- 0x203D + 0x2024 max
                items.append((2, d, bid, "morph"))
            if v["hp"] != st["hp"]:
                items.append((3, d, bid, "hp"))
            sw = v["swing"]
            if sw and sw[0] != st.get("swing_n"):
                if anim and now - sw[2] <= SWING_FRESH_SECS:
                    items.append((3, d, bid, "swing"))
                else:
                    st["swing_n"] = sw[0]       # stale or not drawn: dropped
    # an id drawn under an older spawn is deleted above (tier 0) and drawn
    # again here in the same pump: the strict order below never sends the
    # add without the delete before it
    new = sorted((dist(v["pos"]), bid) for bid, v in live.items()
                 if (bid not in seen or seen[bid]["gen"] != v["gen"])
                 and not v["dead"] and v["card"] is not None
                 and dist(v["pos"]) <= view)
    items += [(1, d, bid, "add") for d, bid in new[:ADDS_PER_PUMP]]
    items.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    # KEY: NO FACING RECORDS (2026-10-23, live crash). A facing-only 0x1006
    # is not light on this client: it logs "Chara N already exists", RELEASES
    # the avatar's face/hair/body/lod models and rebuilds them. Bots turned on
    # every step and swing, so one session rebuilt avatars 83 times in a few
    # minutes, and the 83rd (Footman 386, right after the operator's own
    # death) faulted at FE_Client.dll+0x2A1D9 reading freed memory (polshim
    # log 49016). EVERY 0x1006 on an existing unit rebuilds (static: the hit
    # path 0x0503ac46 ends in an unconditional [vtable+0x4c] at 0x0503adb6 ->
    # 0x0507dbe0 -> the model builder 0x5079f90, whatever the masks).
    # KEY: THE TURN IS 0x2023 ACTION 1 (static, 2026-10-23): a "place + face"
    # record, [u32 1][u32 A][u32 B][f32 x,y,z][f32 fx,fy,fz], read by
    # 0x04feae50 (unit by id, no kind gate) into class 0x5040d10 whose update
    # 0x05040dc0 copies the position to +0x1c4 and sets the facing (fx,0,fz)
    # -- no model work. The client builds it itself (a warp 0x0507f420, a
    # monster facing the player 0x0506b4eb). Only B - A matters (the queue
    # refuses B <= A). A WALKING unit needs none: the avatar tick turns +0x1e4
    # toward its motion (0x0515CF00 -> 0x0515DED0), so only a standing bot is
    # turned -- at its stop and before a swing. UNPROVEN on a screen.
    face_mode = str(getattr(args, "war_bot_face", "turn"))
    face_on = face_mode in ("turn", "rebuild")
    # ---- send them, first things first, inside the budget
    room = _room(s, args, now)
    log = s["wb_sent"]
    sent = {"add": 0, "move": 0, "swing": 0}

    def out(mid, body, bid):
        _send(ctx, mid, body, bid)
        log.append(now)

    left = 0
    for n_, (_tier, _d, bid, kind) in enumerate(items):
        st, v = seen.get(bid), live.get(bid)
        if kind == "del":
            cost = 1
        elif kind == "add":
            cost = 1 + (sanim and v["morph"] is not None)
        elif kind == "morph":
            # the form word (on, or back to HUM: the release 0x106C first)
            # when drawn, and always the HP max + current
            if not sanim or v["morph"] == st.get("morph"):
                cost = 1
            else:
                cost = 2 + (v["morph"] is None)
        elif kind == "hp" and v["hp"] == st["hp"]:
            continue                            # the form record carried it
        elif kind == "fall":
            cost = 1 + (v["hp"] != st["hp"]) + bool(st["moving"])
        elif kind == "move":
            tgt = _step(v["pos"], v["goal"], speed_of(v) * cadence)
            face = _turn_to(st, v["pos"], tgt) if face_mode == "rebuild" \
                else None
            cost = 1 + (face is not None)
        elif kind == "stop":
            face = _face(v["pos"], v["face_to"]) \
                if (face_on and v["face_to"]) else None
            cost = 1 + (face is not None)
        elif kind == "hp":
            cost = 1
        else:                                               # swing
            # action 1 also PLACES the unit: never mid-walk, it would snap back
            face = _turn_to(st, v["pos"], v["swing"][1]) \
                if face_on and (face_mode == "rebuild" or not st["moving"]) \
                else None
            cost = 1 + (face is not None)
        if cost > room:
            left = len(items) - n_
            break
        room -= cost
        if kind == "del":
            fw.mob_kill_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, bid)
            log.append(now)
            del seen[bid]
        elif kind == "add":
            out(0x1006, pres.avatar_body(bid, dict(v["card"],
                                                   hp=(v["hp"], v["hpmax"])),
                                         v["pos"]), bid)
            sw = v["swing"]
            seen[bid] = {"pos": v["pos"], "t": now, "moving": False,
                         "hp": v["hp"], "face": None, "gen": v["gen"],
                         "dead": False, "swing_n": sw[0] if sw else 0,
                         "morph": v["morph"], "hpmax": v["hpmax"]}
            if sanim and v["morph"] is not None:
                # drawn while summoned: the form right after the avatar, as
                # fepresence does for a summoned peer drawn late
                out(0x203D, form_body(1 << int(v["morph"])), bid)
            sent["add"] += 1
        elif kind == "morph":
            if sanim and v["morph"] != st.get("morph"):
                if v["morph"] is None:
                    # the release a real summoner's peers get (feunit.
                    # release_push): 0x106C, then the HUM word
                    out(0x106C, b"", bid)
                    out(0x203D, form_body(FORM_HUM), bid)
                else:
                    out(0x203D, form_body(1 << int(v["morph"])), bid)
                _log("-> 0x203D form %s on war bot %d to charid %s (summon "
                     "drawn, --war-bot-summon-anim on)"
                     % (BOT_FORMS.get(v["morph"], {}).get("name", "HUM")
                        if v["morph"] is not None else "HUM", bid,
                        s.get("charid")))
            out(0x2024, _hpmax_body(v["hp"], v["hpmax"]), bid)
            st.update(morph=v["morph"], hpmax=v["hpmax"], hp=v["hp"])
            sent["morph"] = sent.get("morph", 0) + 1
        elif kind == "fall":
            # THE FALL: its last HP, stopped where it stands, then CONDITION
            # DEAD -- the 0x1004 comes once the corpse time is up
            if v["hp"] != st["hp"]:
                out(0x2024, _hp_body(v["hp"]), bid)
                st["hp"] = v["hp"]
            if st["moving"]:
                out(0x2023, move_body(s, v["pos"], speed_of(v), 1, now), bid)
                st.update(pos=v["pos"], t=now, moving=False)
            body, cv = pres.condition_body(args, True)
            out(0x2024, body, bid)
            st["dead"] = True
            _log("-> 0x2024 CONDITION 0x%08X on war bot %d to charid %s "
                 "(down; removed in %.0fs) -- the fall plays (operator, "
                 "live 2026-10-23)"
                 % (cv, bid, s.get("charid"), corpse))
        elif kind == "move":
            # what a real walk record carries (KEY above): stamped now, at
            # the spot it will reach one cadence on, its real speed
            if face is not None:
                out(0x1006, pres.facing_body(bid, face), bid)   # rebuild only
                st["face"] = face
            out(0x2023, move_body(s, tgt, speed_of(v), 0, now), bid)
            st.update(pos=v["pos"], t=now, moving=True)
            sent["move"] += 1
        elif kind == "stop":
            # THE STOP: state 1 where it stands, the speed pair unchanged
            # (a real stop record keeps it), then turned to what it faces
            out(0x2023, move_body(s, v["pos"], speed_of(v), 1, now), bid)
            if face is not None:
                out(*_face_rec(face_mode, bid, v["pos"], face), bid)
                st["face"] = face
            st.update(pos=v["pos"], t=now, moving=False)
            sent["move"] += 1
        elif kind == "hp":
            out(0x2024, _hp_body(v["hp"]), bid)
            st["hp"] = v["hp"]
        else:
            # THE SWING, once per swing per screen: faced at the aim point,
            # then the cast record a real player's basic attack sends
            sw = v["swing"]
            st["swing_n"] = sw[0]
            if face is not None:
                out(*_face_rec(face_mode, bid, v["pos"], face), bid)
                st["face"] = face
            summoned = v["morph"] is not None and sanim
            body, src = swing_body(s, bid, sw[0], v["cls"], sw[1],
                                   0 if summoned else v["weapon"], now,
                                   skill=FORM_SWING.get(v["morph"])
                                   if summoned else None)
            out(SWING_MID, body, bid)
            sent["swing"] += 1
            if src != "cast":
                _once(("cc", s.get("charid"), src),
                      "charid %s: no cast clock sample yet -- war bot "
                      "swings stamped from the '%s' clock (UNPROVEN, "
                      "may not play)" % (s.get("charid"), src))
    if sent["add"]:
        _log("-> 0x1006 %d war bot(s) to charid %s in area %s (%d drawn, "
             "view %.0f u)" % (sent["add"], s.get("charid"), area, len(seen),
                               view))
    k = int(s.get("wb_left", 0) or 0)
    s["wb_left"] = k + left
    if left and (k == 0 or (k + left) // 500 != k // 500):
        _log("charid %s: %d war bot record(s) held back by --war-bot-msg-rate "
             "%d so far (sent again from the bot's state on a later pump)"
             % (s.get("charid"), k + left, _int(args, "war_bot_msg_rate", 20)))
    k = int(s.get("wb_swings", 0) or 0)
    s["wb_swings"] = k + sent["swing"]
    if sent["swing"] and (k < 3 or (k + sent["swing"]) // 200 != k // 200):
        _log("-> 0x%04X war bot swings to charid %s: %d so far"
             % (SWING_MID, s.get("charid"), k + sent["swing"]))
    k = int(s.get("wb_moves", 0) or 0)
    s["wb_moves"] = k + sent["move"]
    if sent["move"] and (k < 5 or (k + sent["move"]) // 500 != k // 500):
        _log("-> 0x2023 war bot moves to charid %s: %d so far"
             % (s.get("charid"), k + sent["move"]))


def map_dots(s, mine):
    """0x1123 rows (fepresence._dot) for the live bots of the field `s`
    stands in: fepresence.on_distribution appends them."""
    pres = _pres()
    area = s.get("field")
    if pres is None or area is None:
        return []
    with _LOCK:
        rows = [(b, tuple(v["pos"]), v["cls"], v["nation"])
                for b, v in (_BOTS.get(int(area)) or {}).items()
                if not v["dead_until"]]
    return [pres._dot(b, pos, cls, bool(mine and nation and mine != nation))
            for b, pos, cls, nation in sorted(rows)]


def pump(ctx):
    """Reconcile (once a second), drive this field's bots if this session
    holds it, draw them. A no-op with --war-bots 0 and no bot left."""
    args = ctx.args
    s = fw._SESSION
    with _LOCK:
        any_bots = bool(_BOTS)
        booted = _RECONCILE[1]
    if not count(args) and not any_bots and not s.get("wb_seen") and booted:
        return
    now = time.monotonic()
    reconcile(args, now=now)
    camp = _camp()
    pres = _pres()
    if camp is None or pres is None:
        return
    area = camp._here(args)
    with _LOCK:
        mine = area is not None and area in _BOTS
        last = _TICK.get(area)
    if mine and (last is None or now - last >= TICK_SECS) and _claim(area, now):
        tick(args, area, now)
    if pres._active(args):
        _draw(ctx, args, area, now)


# ---------------------------------------------------------------------------
def gm(ctx, line):
    if not line.startswith("!warbots"):
        return False
    w = line.split()
    if len(w) < 2:
        with _LOCK:
            rows = {a: (sum(1 for v in b.values() if not v["dead_until"]), len(b))
                    for a, b in _BOTS.items()}
        _log("!warbots N -- N bots a side in every war (0 = off). Now %d; %s"
             % (count(ctx.args), ", ".join("area %d: %d/%d standing" % (a, u, n)
                                           for a, (u, n) in sorted(rows.items()))
                or "no bots in any field"))
        return True
    try:
        n = int(w[1])
    except ValueError:
        _log("!warbots wants a number, got %r" % w[1])
        return True
    if not 0 <= n <= 200:
        _log("!warbots: 0 to 200")
        return True
    _log(set_count(ctx.args, n, actor="!warbots"))
    return True
