"""Status effects: timed good and bad statuses on players and monsters."""
import struct
import time
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import sess, wire

# ---------------------------------------------------------------------------
# STATUS EFFECTS (2026-10-01, audit B8 / manual pp.38-39).
#
# WHAT THE CLIENT'S DATA SAYS, read statically (fegamedata.EFFECT_COND_RANGES
# has the addresses):
#
#   * a skill lists EFFECT_DATA ids (SKILL_DATA +0x108, and its child's);
#   * an effect row carries the CONDITION bits it puts on its target
#     (EFFECT_DATA +0x54, "bitsE"), a duration (+0x24, or +0x2C when +0x24 is
#     0: Hide, Mysticdrug), a tick (+0x30), the stat it touches (bitsA 0x4 =
#     HP), a speed modifier (bitsB 0x6000, vals[0] = -0.10 .. -0.45) and an
#     amount (vals[0]: poison -15 .. -43 HP per tick);
#   * the CONDITION bit names are the client's own (debug dump 0x050CD340,
#     tooltip labels 0x052E1D30, indexed by bit number).
#
# Status        bit         where it comes from                      confidence
# poison/burn   0x1         D81_PoisonStatus / E5_Fire rows           PROVEN (data)
# stun          0x10        D77_Stun rows, "Stunned!!"                PROVEN (data)
# root          0x20        C33_SpiderNet x28 14, "Rooted!!"          PROVEN (data)
# disarm        0x40        C40_WeaponBreak, "Disarmed!!", ATTACK_OFF PROVEN (data)
# invincible    0x1000      already feunit.COND_INVINCIBLE            PROVEN (data)
# hide          0x2000      Hide L1-5 (123-127), INVISI; the hide-only
#                           skills (Punishing Strike, Void Darkness)
#                           require 0x2000 in SKILL_DATA +0xd0         PROVEN (data)
# no flinch     0x4000      Ender Pain (1-5), UI "No flinch"          PROVEN (data)
# blind         0x8000      E7_Steam (Void Darkness), "Blind!!"        PROVEN (data)
# chant         0x2000000   詠唱 (275-279); chant-only spells require
#                           0x02000000 in SKILL_DATA +0xd0             PROVEN (data)
# slow          (none)      bitsB 0x6000, vals[0] = speed fraction    INFERRED (shape)
# resist up/dn  (none)      bitsC 0x800, vals[0] +5..+17 / -10..-25   INFERRED (shape)
#
# Which skills carry them, from the same tables: Shield Bash 35-39 stun;
# Force Impact 45-49 and Arm Break 195-199 disarm; Spider Web 160-164 slow;
# Freezing Wave 335-339 and Blizzard Caress 345-349 root; Ice Javelin 330-334
# (the manual's "Ice Bolt"), Behemoth Tail 15-19 and Poison Blow L4/L5 slow;
# Viper Bite 190-194 and Poison Blow 150-154 poison; Blaze Shot 165-169, Fire
# Lance 305-309, Spark Flare and Hellfire burn; Void Darkness 205-209 blind +
# resist down; Ender Pain 5-9 no flinch + resist up; Hide 140-144 hide (and
# slow); 詠唱 275-279 chant. Monsters: Venomous 602 poison, Voltex poison,
# Fire Lizard burn, Ice Lizard root.
#
# WHAT THE SERVER ENFORCES (--status-effects on, default): poison/burn HP
# over time on players and monsters; slow, root and stun in the monster AI (chase,
# stroll, swing); a stunned or disarmed attacker does no damage; resistance
# up/down in the player's damage taken; root (manual) and hide (CHOSEN) end
# when the holder is hit; a hidden player can't be target-locked in PvP.
#
# WHAT IT DOES NOT: the player's OWN movement and skill bar are the client's.
# --status-effects-wire (default ON since 2026-10-08) tells the client, through
# the CONDITION word (0x2024 maskA 0x100000, the setter unitstate already uses).
#
# THE STATIC CHECK BEHIND THE DEFAULT (2026-10-08, read off the dump):
#   * the HUD's status-icon loop at 0x050cd369..0x050cde46 tests
#     [unit+0x2b4] one bit at a time (bytes 0x1,0x2,..0x80, then 0x10000..
#     0x80000000) and labels each from the table at 0x052E1D30, indexed by
#     bit number: [0] 毒効果, [4]/[5] Stunned, [6] No atk, [12] 無敵,
#     [13] Invisible, [14] No flinch, [15] Blinded, [23] 死亡, [24] 幽霊状態.
#     So the icons ARE the CONDITION word, bit for bit as KIND_BITS names it.
#   * the client owns three bits of that word itself: it sets 0x4000 (No
#     flinch) while a skill motion plays (0x05069405 `or ch,0x40`, cleared at
#     0x05042770 / 0x0506ceec) and clears 0x2000 Invisible | 0x20000 Ambush
#     when the player acts (0x0506ceec `and eax,0xfffddfff`). A full-word push
#     must therefore never re-assert HIDE after the client dropped it: a cast
#     ends the server's hide too (player_cast), and the transient 0x4000 is
#     left alone (we only push on a status CHANGE).
#   The screen proof is still owed: cond_sync's docstring says what to watch.
# ---------------------------------------------------------------------------

COND_POISON = 0x1
COND_STUN = 0x10
COND_ROOT = 0x20
COND_DISARM = 0x40
COND_INVINCIBLE = 0x1000
COND_HIDE = 0x2000
COND_NOFLINCH = 0x4000
COND_BLIND = 0x8000
COND_CHANT = 0x2000000
COND_DEAD = 0x800000

#: status kind -> the CONDITION bit it shows as (slow and resist have none)
KIND_BITS = {"poison": COND_POISON, "burn": COND_POISON, "stun": COND_STUN,
             "root": COND_ROOT, "disarm": COND_DISARM,
             "invincible": COND_INVINCIBLE, "hide": COND_HIDE,
             "noflinch": COND_NOFLINCH, "blind": COND_BLIND,
             "chant": COND_CHANT}
#: the GOOD statuses (manual p.38) land on the CASTER; the rest on the target
GOOD = frozenset(("noflinch", "hide", "chant", "invincible", "resist_up"))
DOT = ("poison", "burn")
#: CHOSEN: an effect shorter than this is an instant item's flag, not a
#: status (the Antidote's 600 ms INVISI bit next to its cure)
MIN_STATUS_MS = 1000


def on(args):
    return str(getattr(args, "status_effects", "on") or "on") == "on"


def wire_on(args):
    return str(getattr(args, "status_effects_wire", "on") or "on") == "on"


def _log(msg):
    print("[feworld]    status: " + msg, flush=True)


# ---------------------------------------------------------------------------
# what a skill does
# ---------------------------------------------------------------------------
_SPEC_CACHE = {}


def effect_specs(effect_id):
    """[{"kind", "ms", "effect", ...}] for one EFFECT_DATA row."""
    x = fegamedata.effects().get(int(effect_id))
    if not x:
        return []
    dur = x["total_ms"] or (x.get("x2c", 0) if x.get("x2c", 0) > 1 else 0)
    if dur < MIN_STATUS_MS:
        return []
    cond, v0 = x.get("cond", 0), (x["vals"][0] if x["vals"] else 0.0)
    out = []

    def add(kind, **kw):
        out.append(dict(kind=kind, ms=int(dur), effect=int(effect_id), **kw))

    if (cond & COND_POISON and x["a"] & fegamedata.EFFECT_HP and v0 < 0
            and x["tick_ms"] > 0):
        burn = x["name"].startswith("E5_Fire") or x.get("x28") == 25
        add("burn" if burn else "poison", amount=int(round(-v0)),
            tick_ms=int(x["tick_ms"]))
    for bit, kind in ((COND_STUN, "stun"), (COND_ROOT, "root"),
                      (COND_DISARM, "disarm"), (COND_INVINCIBLE, "invincible"),
                      (COND_HIDE, "hide"), (COND_NOFLINCH, "noflinch"),
                      (COND_BLIND, "blind"), (COND_CHANT, "chant")):
        if cond & bit:
            add(kind)
    if x["b"] & 0x6000 and v0 < 0:
        add("slow", factor=max(0.0, 1.0 + v0))
    # INFERRED: bitsC 0x800 = resistance (耐性). Ender Pain +5..+17 (the manual
    # pairs it with nothing else; FEZ's Ender Pain raised defence) and Void
    # Darkness's blind -10..-25 (the manual's 耐性ダウン sits beside 暗闇).
    if x["c"] & 0x800 and not x["c"] & 0x20 and v0:
        add("resist_up" if v0 > 0 else "resist_down", amount=abs(float(v0)))
    return out


def skill_specs(skill_id):
    """Every status skill `skill_id` applies, from its effects and its child's."""
    if skill_id is None:
        return []
    k = int(skill_id)
    if k not in _SPEC_CACHE:
        out = []
        for e in fegamedata.skill_effect_ids(k):
            out += effect_specs(e)
        _SPEC_CACHE[k] = out
    return _SPEC_CACHE[k]


def skill_cures(skill_id):
    """The CONDITION bits a skill clears (bitsF: the Antidote's 0x1)."""
    m = 0
    for e in fegamedata.skill_effect_ids(int(skill_id)) if skill_id is not None else []:
        m |= int(fegamedata.effects().get(e, {}).get("cure", 0) or 0)
    return m


def bad_specs(skill_id):
    return [s for s in skill_specs(skill_id) if s["kind"] not in GOOD]


def good_specs(skill_id):
    return [s for s in skill_specs(skill_id) if s["kind"] in GOOD]


def skill_required_bits(skill_id):
    """SKILL_DATA +0xd0: the CONDITION bits a skill needs (0x2000000 chant,
    0x2000 hide)."""
    return int(fegamedata.skills().get(int(skill_id), {}).get("state", 0) or 0)


# ---------------------------------------------------------------------------
# the store: holder["status"] = {kind: entry}. A holder is a session dict
# (touched only on its own thread) or a monster record (touched under
# monsters._MOB_LOCK).
# ---------------------------------------------------------------------------
def table(holder):
    return holder.setdefault("status", {})


def prune(holder, now=None):
    """Drop what has run out; the kinds that ended."""
    st = holder.get("status")
    if not st:
        return []
    now = time.monotonic() if now is None else now
    gone = [k for k, e in st.items() if e["until"] <= now]
    for k in gone:
        del st[k]
    return gone


def active(holder, now=None):
    prune(holder, now)
    return dict(holder.get("status") or {})


def has(holder, kind, now=None):
    e = (holder.get("status") or {}).get(kind)
    now = time.monotonic() if now is None else now
    return bool(e and e["until"] > now)


def apply(holder, specs, src=None, skill=None, now=None):
    """Put `specs` on `holder`. A status already running is refreshed to the
    later end, and a DoT keeps the stronger amount. Returns the kinds set."""
    if not specs:
        return []
    now = time.monotonic() if now is None else now
    st = table(holder)
    done = []
    for s in specs:
        until = now + s["ms"] / 1000.0
        e = st.get(s["kind"])
        if e is None or e["until"] <= now:
            e = st[s["kind"]] = {"until": until, "src": src, "skill": skill,
                                 "effect": s["effect"]}
            if s["kind"] in DOT:
                e["next_at"] = now + s["tick_ms"] / 1000.0
        else:
            e["until"] = max(e["until"], until)
        for k in ("amount", "tick_ms", "factor"):
            if k not in s:
                continue
            if k == "amount" and s["kind"] in DOT and e.get("amount", 0) > s[k]:
                continue
            if k == "factor" and e.get("factor", 1.0) < s[k]:
                continue                    # the slower slow wins
            e[k] = s[k]
            if k == "amount":
                e["src"] = src
        done.append(s["kind"])
    return done


def cure(holder, mask):
    """Clear every status whose CONDITION bit is in `mask`."""
    st = holder.get("status") or {}
    gone = [k for k in st if KIND_BITS.get(k, 0) & mask]
    for k in gone:
        del st[k]
    return gone


def clear(holder):
    st = holder.get("status")
    n = len(st or {})
    if st:
        st.clear()
    return n


def break_on_hit(holder, now=None):
    """Taking a hit ends ROOT (manual p.39: "Broken by taking a hit") and HIDE
    (CHOSEN: FEZ's rule; the manual only says hidden players can't be
    target-locked). Stun is NOT broken (manual)."""
    st = holder.get("status") or {}
    gone = [k for k in ("root", "hide") if k in st]
    for k in gone:
        del st[k]
    return gone


def bits(holder, now=None):
    m = 0
    for k in active(holder, now):
        m |= KIND_BITS.get(k, 0)
    return m


def refusal(holder, now=None):
    """Why this holder can't attack right now, or None. Manual p.39: stun =
    no actions at all; disarm = no weapon attacks and no skills."""
    if has(holder, "stun", now):
        return "stunned"
    if has(holder, "disarm", now):
        return "disarmed"
    return None


def resist_delta(holder, now=None):
    a = active(holder, now)
    return (float(a.get("resist_up", {}).get("amount", 0) or 0)
            - float(a.get("resist_down", {}).get("amount", 0) or 0))


def speed_factor(holder, now=None):
    """1.0 normally; the slow's fraction; 0 while rooted or stunned."""
    a = active(holder, now)
    if "root" in a or "stun" in a:
        return 0.0
    return float(a.get("slow", {}).get("factor", 1.0) or 0.0)


def dot_ticks(holder, now=None):
    """[(kind, amount, src)] for every poison/burn tick now due."""
    now = time.monotonic() if now is None else now
    out = []
    for k in DOT:
        e = (holder.get("status") or {}).get(k)
        if not e:
            continue
        tick = max(0.1, float(e.get("tick_ms", 1000)) / 1000.0)
        while e["next_at"] <= min(now, e["until"] + 1e-6):
            out.append((k, int(e.get("amount", 0)), e.get("src")))
            e["next_at"] += tick
    prune(holder, now)
    return out


# ---------------------------------------------------------------------------
# hide: who may see / lock whom
# ---------------------------------------------------------------------------
def hidden_from(viewer, peer, now=None):
    """True when `peer` (a session dict) is hidden from `viewer`: the peer
    has HIDE and the two are of different nations. Allies and the hider
    still see them (manual p.38: semi-transparent to allies). Read-only on
    the peer's dict."""
    st = peer.get("status") or {}
    e = st.get("hide")
    now = time.monotonic() if now is None else now
    if not e or e.get("until", 0) <= now:
        return False

    def force(s):
        try:
            return int((s.get("pres_card") or {}).get("force") or 0)
        except (TypeError, ValueError):
            return 0
    mine, theirs = force(viewer), force(peer)
    return not (mine and theirs and mine == theirs)


# ---------------------------------------------------------------------------
# crouch (manual p.38: crouching heals, "but you take extra damage")
# ---------------------------------------------------------------------------
#: CHOSEN: how long one piece of crouch evidence counts. The crystal heal
#: (0x20AD) comes every 5 s while crouched; the regen ask every 1.349 s.
CROUCH_WINDOW = 6.0
#: SOURCED: the client's regen ask comes every 3000 ms standing and 1349 ms
#: crouched (feprog, 0x0507D84B). A gap under this is a crouch.
CROUCH_REGEN_GAP = 2.2
#: CHOSEN: moving further than this from where the crouch was seen stands up
CROUCH_MOVE = 0.75


def crouch_note(s, why, now=None):
    """Evidence that this session's player is crouched right now."""
    now = time.monotonic() if now is None else now
    first = not crouched(s, now)
    s["crouch_at"] = now
    s["crouch_pos"] = s.get("cpos")
    if first:
        _log("charid %s is CROUCHED (%s)" % (s.get("charid"), why))


def crouch_watch(s, now=None):
    """Read the regen-ask clock feprog keeps (pw_tick_at): two asks closer
    than CROUCH_REGEN_GAP are the client's own crouch cadence."""
    t = s.get("pw_tick_at")
    last = s.get("crouch_tick_seen")
    if t is None or t == last:
        return False
    s["crouch_tick_seen"] = t
    if last is not None and 0.2 < t - last < CROUCH_REGEN_GAP:
        crouch_note(s, "0x2028 regen ask after %.2fs, the 1349 ms crouch "
                    "cadence" % (t - last), now)
        return True
    return False


def crouched(s, now=None):
    at = s.get("crouch_at")
    if at is None:
        return False
    now = time.monotonic() if now is None else now
    if now - at > CROUCH_WINDOW:
        return False
    p, q = s.get("crouch_pos"), s.get("cpos")
    if p and q and ((p[0] - q[0]) ** 2 + (p[2] - q[2]) ** 2) ** 0.5 > CROUCH_MOVE:
        s.pop("crouch_at", None)
        return False
    return True


def crouch_mult(args, s, now=None):
    """--crouch-damage while crouched (CHOSEN 1.3: RoD players felt 1.2-1.5x),
    else 1.0."""
    f = float(getattr(args, "crouch_damage", 1.3) or 1.0)
    if f == 1.0 or not crouched(s, now):
        return 1.0
    return f


# ---------------------------------------------------------------------------
# the player
# ---------------------------------------------------------------------------
def cond_word(args, s):
    """The CONDITION word this session's client should hold: --unit-state's
    base, DEAD, the protection flag feworld itself sent, and the statuses."""
    base = getattr(args, "unit_state", None)
    v = (base if base is not None else 0) & 0xFFFFFFFF
    if s.get("player_dead"):
        v |= COND_DEAD
    if s.get("protect_flag_sent"):
        v |= COND_INVINCIBLE
    return v | bits(s)


def cond_sync(conn, outbound, mode, be, args):
    """--status-effects-wire on (default): push the CONDITION word when the
    status bits change.

    NOT LIVE-TESTED past the DEAD bit. The setter is the one unitstate and
    death already use (0x2024 maskA 0x100000 -> 0x0506caa0), the bit names
    are the client's own and the HUD icon loop reads exactly this word (see
    the header: 0x050cd369, label table 0x052E1D30). THE LIVE CHECK: `!status
    poison 10` (or a Venomous bite) -- the 毒効果 icon should appear on the
    HUD and clear when the poison ends; `!status root 10` should stop the
    player walking (STUN1); `!status hide 30` should draw the player semi-
    transparent. If the icon does not show, --status-effects-wire off is the
    rollback. The client's own writers of the word (the 0x4000 no-flinch
    toggle around a skill motion and the 0x2000|0x20000 clear on an act) are
    accounted for in the header; player_cast keeps the server's hide in step
    with the client's clear."""
    if not wire_on(args):
        return False
    s = sess._SESSION
    b = bits(s)
    if b == s.get("status_bits_sent", 0):
        return False
    v = cond_word(args, s)
    wire.send(conn, outbound,
              wire.inner_msg(0x2024, struct.pack(">III", 0x100000, 0, v),
                             wire.unit_id_of(args)),
              mode, be, args.seq_mode == "echo", args.world_prefix)
    _log("-> 0x2024 CONDITION 0x%08X (status bits 0x%X -> 0x%X, "
         "--status-effects-wire on, UNPROVEN)" % (v, s.get("status_bits_sent", 0), b))
    s["status_bits_sent"] = b
    return True


def player_apply(conn, outbound, mode, be, args, specs, src=None, skill=None,
                 why=""):
    """Put `specs` on THIS session's player (its own thread)."""
    if not on(args) or not specs:
        return []
    s = sess._SESSION
    if s.get("player_dead") or not s.get("in_field"):
        return []
    from . import death
    if death.spawn_protected(args):
        return []
    done = apply(s, specs, src=src, skill=skill)
    if done:
        _log("charid %s now %s (%s)" % (s.get("charid"), ", ".join(done), why))
        cond_sync(conn, outbound, mode, be, args)
    return done


def player_hit_taken(conn, outbound, mode, be, args, why=""):
    """The player was hit: end root and hide."""
    if not on(args):
        return []
    gone = break_on_hit(sess._SESSION)
    if gone:
        _log("charid %s: %s broken by a hit (%s)"
             % (sess._SESSION.get("charid"), ", ".join(gone), why))
        cond_sync(conn, outbound, mode, be, args)
    return gone


def player_cast(conn, outbound, mode, be, args, skill=None):
    """The player used a skill: the CLIENT clears Invisible (0x2000) and
    Ambush (0x20000) on its own unit when the player acts (0x0506ceec `and
    eax,0xfffddfff`), so the server's hide ends here too -- otherwise the next
    cond_sync would put the bit back and the hider would turn invisible again
    mid-attack. The hide-only skills (SKILL_DATA +0xd0 wants 0x2000) are cast
    FROM hide, so the clear lands after the gate, the same order the client
    uses. Returns the kinds ended."""
    if not on(args):
        return []
    s = sess._SESSION
    st = s.get("status") or {}
    gone = [k for k in ("hide",) if k in st]
    for k in gone:
        del st[k]
    if gone:
        _log("charid %s: %s ends on the cast%s (the client clears 0x2000 itself)"
             % (s.get("charid"), ", ".join(gone),
                "" if skill is None else " of skill %s" % skill))
        cond_sync(conn, outbound, mode, be, args)
    return gone


def player_dies(conn, outbound, mode, be, args, why):
    """The same death a monster's swing causes (monsters.monster_attack_tick)."""
    from . import death
    s = sess._SESSION
    now = time.monotonic()
    s["player_dead"] = True
    s["revive_at"] = now + float(getattr(args, "revive_secs", 60) or 60)
    death.player_dead_push(conn, outbound, mode, be, args, True)
    death.respawn_wait_arm(conn, outbound, mode, be, args)
    _log("THE PLAYER IS DEAD (%s)" % why)
    death.death_penalty(conn, outbound, mode, be, args)


def player_pump(conn, outbound, mode, be, args, now=None):
    """Once per inbound message on this session's thread: crouch evidence,
    run-outs, and poison/burn ticks on the player's HP."""
    s = sess._SESSION
    now = time.monotonic() if now is None else now
    crouch_watch(s, now)
    if not on(args):
        return 0
    if s.get("player_dead") or not s.get("in_field"):
        if clear(s):
            _log("charid %s: statuses end (dead or out of the field)"
                 % s.get("charid"))
        cond_sync(conn, outbound, mode, be, args)
        return 0
    ticks = dot_ticks(s, now)
    hurt = 0
    if ticks:
        from . import death
        hpmax = death.player_hp_max(args)
        hp = int(s.get("player_hp", hpmax))
        # RoD: poison and burn never take a player below 1 HP (fewiki memo,
        # 2006-05-26: 毒・火傷ではHP1以下にならない). --dot-floor 0 lets
        # them kill (the 2026-10-01 behaviour).
        floor = max(0, int(getattr(args, "dot_floor", 1) or 0))
        for kind, amt, _src in ticks:
            if hp <= floor:
                break
            hp = max(floor, hp - amt)
            hurt += amt
        s["player_hp"] = hp
        death.player_hp_push(conn, outbound, mode, be, args, hp)
        _log("charid %s: %s -%d HP -> %d/%d"
             % (s.get("charid"), "+".join(sorted(set(k for k, _a, _s in ticks))),
                hurt, hp, hpmax))
        if hp <= 0:
            player_dies(conn, outbound, mode, be, args, "poison/burn")
            clear(s)
    cond_sync(conn, outbound, mode, be, args)
    return hurt


# ---------------------------------------------------------------------------
# monsters
# ---------------------------------------------------------------------------
def mob_refusal(m, now=None):
    return refusal(m, now)


def mob_pump(conn, outbound, mode, be, args, now=None):
    """Poison/burn ticks on monsters, run by ONE session per field (the
    monster driver) or by every solo session for its own copies. The damage
    goes through combat.mob_damage, credited to whoever applied it, so a DoT
    kill pays the damage ledger's winner like any other kill."""
    if not on(args) or getattr(args, "combat", "off") != "on":
        return 0
    mobs = sess._SESSION.get("mobs") or {}
    if not mobs:
        return 0
    from . import combat, monsters
    mm = monsters._mob_mode(args)
    if mm == "wait" or (mm == "shared" and not monsters.mob_driver(args)):
        return 0
    now = time.monotonic() if now is None else now
    due = []
    with monsters._MOB_LOCK:
        for obj, m in list(mobs.items()):
            if m.get("dead") or not m.get("status"):
                continue
            for kind, amt, src in dot_ticks(m, now):
                due.append((obj, m, kind, amt, src))
    for obj, m, kind, amt, src in due:
        combat.mob_damage(conn, outbound, mode, be, args, obj, m, amt, src,
                          "%s tick" % kind)
    return len(due)
