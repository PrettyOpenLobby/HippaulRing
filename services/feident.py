#!/usr/bin/env python3
"""feident.py -- WHICH POL MEMBER is on the other end of a Fantasy Earth socket.

WHY THIS FILE EXISTS
--------------------
Until 2026-08-24 `felobby.py` was launched with no `--account` at all, so every
session fell to the argparse default `"TestPlayer"` -- and
`data/fe_characters.json` is keyed by exactly that string:

    {"accounts": {"TestPlayer": [{"name": "Lex", ...}]}}

One roster for the whole server. Two people playing FE would have seen, edited
and DELETED each other's characters. That is not a profile gap to be papered
over per Content ID later; it is the absence of any per-player identity at all,
and it has to be fixed before "which profile does this character fill" is even a
well-posed question.

THE MECHANISM, AND ITS LIMIT
----------------------------
FE never tells us who it is in a form we can trust:

  * `0xC007 MSG_CERTIFICATION` carries a 52-byte credential blob lifted from
    polcore ([0x52fe920]->vtable[0xea0]).
    WARNING: THIS FILE ORIGINALLY SAID THAT BLOB IS A CONSTANT AND MUST NEVER BE
    KEYED ON. THAT WAS WRONG -- see the blob section further down for the
    correction and the evidence. The short version: fmo.py's "identical across
    two machines" measurement is about a 16-byte field at `+0x38` that FE does
    not send, while the 52-byte blob FE *does* send is the one fmo.py records
    as VARYING PER SESSION. It may well be the per-account identity this whole
    file works around, and `tools/fe_blob_verdict.py` is the experiment that
    settles it. Until it does, nothing here keys on it -- but the reason is now
    "unmeasured", not "known useless".
  * The account name in `0xC010 MSG_CERTIFICATION_OK` is whatever WE send. It is
    an output, not an input.

So identity comes from where FMO gets it: `responders.py` writes a `session` row
(member_id, nick, peer_ip, created_at) to accounts.db on every POL sign-in, an
FE launch can only follow a POL login from the same box, and both FE containers
mount the same `/data`. The freshest session row for the connecting address
names the member.

WARNING -- TWO REAL LIMITS, STATED PLAINLY RATHER THAN DISCOVERED LATER.

 1. TWO POL ACCOUNTS BEHIND ONE ADDRESS COLLIDE. That is the lobby's own
    documented per-IP session binding, not a
    new defect -- but it does mean two players in one house still share a
    roster. The freshest row wins and the collision is LOGGED.
 2. THIS NEEDS HOST NETWORKING TO RESOLVE ANYTHING. accounts.py says it
    outright: on the dev bridge network every Viewer reaches the stack as the
    Docker gateway 172.18.0.1, so `session.peer_ip` is one constant there too.
    A production deployment runs `network_mode: host`, which is where real
    client addresses arrive; in dev this degrades to `addr:172.18.0.1`.
    Degrading to an address key is still strictly better than what it replaces
    -- it is per-machine rather than per-server -- and it NEVER falls back to a
    single shared bucket. A roster that silently merges two players is worse
    than one that is merely inconvenient to find.

THE FELOBBY -> FEWORLD HANDOFF
------------------------------
felobby (54849) and feworld (54850) are SEPARATE PROCESSES in separate
containers, so FMO's trick of remembering the identity in a module-level dict
across two connections is not available here. Two carries, tried in this order:

  1. THE ACCOUNT ECHO. `0x400F MSG_SERVER_UNIT_LOGIN_REQUEST` opens with
     `[cstr account]`, and that string is the one the client stored from our own
     `0xC010`. Put the store key on the wire and the client hands it straight
     back to the world door with no heuristic at all.
  2. THE HANDOFF FILE, `data/fe_sessions.json`, written by felobby at
     certification and read by feworld by peer address. This is the belt to the
     echo's braces: the echo is the client re-sending a string we chose, which
     is very likely verbatim but has NOT been proven byte-for-byte on a live
     0x400F, so feworld does not depend on it alone.

Both are checked, and feworld LOGS WHICH ONE FIRED -- because a carry that
quietly stopped working and fell through to an address lookup is
indistinguishable from one that worked, which is how this class of bug hides.
"""
import datetime
import io
import json
import os
import sqlite3
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

#: Fantasy Earth's POL content code (contentlist.py: 11 = FantasyEarth). Used
#: only to decorate the resolved identity with the member's FE Content ID -- the
#: roster is keyed by MEMBER, because `content` is UNIQUE (member_id,
#: content_code) so the two are one-to-one for a single title anyway, and the
#: member id survives the content being re-registered.
FE_CONTENT_CODE = 11

#: The account name felobby used to send for everybody. Recognised so that a
#: roster still sitting under it can be REPORTED (and rekeyed with
#: tools/fe_roster_rekey.py) instead of silently vanishing from the select
#: screen. It is never adopted automatically: handing the first player to log in
#: after this change somebody else's characters is the same cross-account bleed
#: in a nicer costume.
LEGACY_ACCOUNT = "TestPlayer"


def _default_accounts_db():
    """`<repo>/data/accounts.db` -- the same file on host and in the container,
    because services/ is bind-mounted at /app so `_HERE/../data` resolves to
    pol-server/data either way. (Probing an absolute `/data` first is wrong on
    Windows, where it means `<current drive>/data`: felobby.py learned that the
    hard way, wrote to E:/data, and looked like it had worked.)"""
    d = os.path.normpath(os.path.join(_HERE, os.pardir, "data"))
    return os.path.join(d if os.path.isdir(d) else _HERE, "accounts.db")


def _default_session_store():
    """`<repo>/data/fe_sessions.json` -- the felobby -> feworld handoff."""
    d = os.path.normpath(os.path.join(_HERE, os.pardir, "data"))
    return os.path.join(d if os.path.isdir(d) else _HERE, "fe_sessions.json")


def _default_char_store():
    """`<repo>/data/fe_characters.json` -- felobby's roster, READ ONLY here.

    feident needs it for one question: which member keys actually own Fantasy
    Earth characters. That is the tie-break when two POL accounts share an
    address, and it is the difference between handing a player their roster and
    handing them somebody else's empty one. felobby owns this file; this module
    never writes it. (It cannot import felobby to reuse `store_accounts()` --
    felobby imports THIS module.)"""
    d = os.path.normpath(os.path.join(_HERE, os.pardir, "data"))
    return os.path.join(d if os.path.isdir(d) else _HERE, "fe_characters.json")


def _default_ip_memory():
    """`<repo>/data/fe_ip_members.json` -- which FE player last used an address."""
    d = os.path.normpath(os.path.join(_HERE, os.pardir, "data"))
    return os.path.join(d if os.path.isdir(d) else _HERE, "fe_ip_members.json")


#: The resolutions confident enough to REMEMBER for an address.
#:
#: `sole` is one member, no guessing. `roster` is several members at the address
#: and exactly one of them owns FE characters -- if anything the FIRMER of the
#: two, because it was disambiguated by evidence rather than by there being no
#: alternative.
#:
#: WARNING: `roster` was missing here until 2026-09-04 and it broke the whole point of
#: the memory. On a box where PCSX2 signs a second POL account in constantly,
#: EVERY good login is `roster` (both accounts have live rows) -- so nothing was
#: ever written, and an hour later when the FE player's own row had been purged
#: there was no memory to detect the collision against. It resolved `sole` to
#: the wrong member and showed an empty character list, twice in one evening.
#: `ambiguous` and `contested` stay out: those are guesses, and a remembered
#: guess is a guess that outlives its evidence.
REMEMBERABLE = ("sole", "roster")

#: Empty disables POL-member keying and leaves every connection on an address
#: key. Provided so a capture run can be made hermetic, NOT as a fallback --
#: there is no configuration in which sharing one roster is the right answer.
ACCOUNTS_DB = os.environ.get("FE_ACCOUNTS_DB",
                             os.environ.get("POL_ACCOUNTS_DB",
                                            _default_accounts_db()))

SESSION_STORE = os.environ.get("FE_SESSION_STORE", _default_session_store())

#: felobby's character store, read (never written) for the tie-break above.
CHAR_STORE = os.environ.get("FE_CHAR_STORE", _default_char_store())

#: The FE-player-per-address memory. Empty disables it entirely.
IP_MEMORY = os.environ.get("FE_IP_MEMORY", _default_ip_memory())

#: What to do when a LIVE POL session row names a member who owns no Fantasy
#: Earth characters, at an address remembered for an FE player who does. That
#: is the 2026-09-04 shape exactly: PCSX2 signing a PS2 test account
#: (member:15, no FE characters) in from the FE player's own machine, minutes
#: after that player's own POL row had been purged, so member:15 was the ONLY
#: candidate and no tie-break could see the collision.
#:
#: OFF by default, and the default is a judgement call, not an oversight:
#:
#:   off  the live login wins. A BRAND-NEW FE player on a box somebody else has
#:        played on gets their own empty roster and creates a character, which
#:        is correct. The cost is the 2026-09-04 evening: the returning player
#:        is told they have no character. Inconvenient, and recoverable.
#:   on   the remembered FE player wins. The returning player gets their roster
#:        back. The cost is that a brand-new player on that box would be shown
#:        SOMEBODY ELSE'S characters -- the cross-account bleed this whole
#:        module exists to prevent, and the module's own rule is that a roster
#:        which silently merges two players is worse than one that is merely
#:        inconvenient to find.
#:
#: So it stays off unless an operator turns it on for a server where they know
#: the population -- which, on a private revival where one person owns every
#: account and PCSX2 shares a box with the Viewer, is the sane setting. Either
#: way the collision is LOGGED and the identity is marked `contested`, so a
#: character is never created under a guess without a line saying so.
PREFER_REMEMBERED = os.environ.get("FE_PREFER_REMEMBERED_PLAYER",
                                   "0").strip().lower() in ("1", "yes", "true",
                                                            "on")

#: How far back a POL session row may still name the member at an address.
#:
#: WARNING: THE OLD COMMENT HERE SAID "the freshest row is right by construction".
#: THAT IS FALSE, and it cost a live session on 2026-09-04. Two things break it:
#:
#:  1. `accounts.open_session()` gives every row `ttl_seconds=3600` and
#:     `purge_sessions()` deletes expired rows ON EVERY LOGIN. So this 24-hour
#:     window asks a question the data cannot answer: after an hour YOUR row is
#:     gone, and "the freshest row at this address" becomes whoever signed in
#:     after you.
#:  2. When it is somebody else, we used to take them SILENTLY. On 2026-09-04
#:     the FE player's POL login (member:3, 23:25Z) had been purged, PCSX2
#:     signed the PS2 test account (member:15) in seven times between 00:49 and
#:     01:31, and that player's Fantasy Earth launch at 01:31:51 was handed
#:     member:15's empty roster. The character select read "you have no
#:     character".
#:
#: The window is kept generous ON PURPOSE -- shortening it does not help, since
#: the rows are DELETED, not merely aged out -- but it is no longer trusted on
#: its own. `member_for_ip()` now gathers every candidate at the address and
#: refuses to guess between them; see the confidence levels there.
MEMBER_WINDOW = float(os.environ.get("FE_MEMBER_WINDOW", "86400"))

#: What `accounts.open_session()` actually grants a row. Not enforced here --
#: recorded so the mismatch above stays visible to the next reader.
SESSION_ROW_TTL = 3600.0

#: How long the FE-player memory below may name an address after its POL
#: session row has been purged. Only ever written for a player who OWNS FE
#: characters, and only ever read when accounts.db has nothing at all for the
#: address -- see `_ip_memory_get`.
IP_MEMORY_TTL = float(os.environ.get("FE_IP_MEMORY_TTL", str(30 * 86400)))

#: How long felobby's handoff record stays good for feworld. The real gap is the
#: seconds between MSG_LOBBY_JOIN_GAME_OK and the world dial; this is generous
#: only so a slow client cannot silently drop to an address key.
HANDOFF_TTL = float(os.environ.get("FE_HANDOFF_TTL", "900"))

_lock = threading.Lock()


def _log(tag, msg):
    print("[%s] %s" % (tag, msg), flush=True)


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


# ---------------------------------------------------------------------------
# POL member resolution
# ---------------------------------------------------------------------------

def roster_member_keys(path=None):
    """The store keys that own at least one Fantasy Earth character.

    Read-only, failure-tolerant: an unreadable or absent store yields an empty
    set, which simply disables the tie-break rather than breaking a login.
    """
    p = CHAR_STORE if path is None else path
    if not p:
        return set()
    try:
        with io.open(p, encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, ValueError):
        return set()
    if not isinstance(blob, dict):
        return set()
    return {k for k, v in (blob.get("accounts") or {}).items() if v}


def member_for_ip(ip, tag="feident", accounts_db=None, window=None,
                  roster=None):
    """The POL member behind `ip`, or None.

    Returns {key, member_id, nick, content_id, confidence, detail}; `key` is the
    store key, `"member:<id>"`.

    CONFIDENCE, because "we could not tell" and "it is definitely them" must not
    look alike to the caller or in the log:

      ``sole``      exactly one POL member has a recent session row here. The
                    normal case, and the only one trusted enough to be
                    remembered for later (see `_ip_memory_put`).
      ``roster``    several members are candidates, and exactly one of them owns
                    FE characters. We take that one -- an empty roster handed to
                    the player who owns one is the failure being fixed.
      ``ambiguous`` several candidates and the roster cannot separate them. We
                    still answer (the protocol needs a name) but say so loudly,
                    because a character CREATED under a guess lands in somebody
                    else's roster and cannot be told apart afterwards.

    NEVER RAISES -- a database fault must not break an FE login -- but it is
    always LOGGED, because a lookup that quietly did not run looks exactly like
    one that ran and found nobody, and those need different fixes.
    """
    db_path = ACCOUNTS_DB if accounts_db is None else accounts_db
    if not db_path:
        return None
    win = MEMBER_WINDOW if window is None else window
    cutoff = (_utcnow() - datetime.timedelta(seconds=win)
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        db = sqlite3.connect(db_path, timeout=2)
        try:
            rows = db.execute(
                "SELECT member_id, nick, created_at FROM session"
                " WHERE peer_ip = ? AND created_at >= ?"
                " ORDER BY created_at DESC LIMIT 32",
                (ip, cutoff)).fetchall()
            if not rows:
                return None
            # One entry per member, keeping that member's freshest row. Ordered
            # freshest-first, so [0] is the old "freshest row wins" answer and
            # every later element is a candidate that answer was silently
            # discarding.
            seen = {}
            for mid, nick, at in rows:
                if mid not in seen:
                    seen[mid] = (mid, nick, at)
            cands = list(seen.values())
            pick, confidence = cands[0], "sole"
            if len(cands) > 1:
                keys = roster_member_keys() if roster is None else roster
                owners = [c for c in cands
                          if ("member:%d" % c[0]) in keys]
                if len(owners) == 1:
                    pick, confidence = owners[0], "roster"
                else:
                    pick, confidence = cands[0], "ambiguous"
            mid, nick, at = pick
            cid = _content_id(db, mid)
        finally:
            db.close()
    except Exception as e:                     # noqa: BLE001 -- see docstring
        _log(tag, "WARN POL member lookup for %s failed (%r; accounts.db at %s) "
                  "-- this connection falls back to an ADDRESS key, so its "
                  "characters land somewhere other than its last session's"
                  % (ip, e, db_path))
        return None

    if confidence == "roster":
        losers = sorted(c[0] for c in cands if c[0] != mid)
        _log(tag, "%s has recent POL sessions for members %s -- taking member "
                  "%s (%r), the only one of them that owns Fantasy Earth "
                  "characters. The freshest row alone would have said member "
                  "%s and shown an EMPTY character list."
                  % (ip, sorted(c[0] for c in cands), mid, nick, cands[0][0]))
        del losers
    elif confidence == "ambiguous":
        _log(tag, "WARN %s has recent POL sessions for members %s and the "
                  "roster cannot separate them (none or several own FE "
                  "characters). Falling back to the freshest: member %s (%r, "
                  "%s). !! IF A CHARACTER IS CREATED NOW IT LANDS IN THAT "
                  "member's roster. Sign the other account(s) out of POL and "
                  "reconnect to make this unambiguous."
                  % (ip, sorted(c[0] for c in cands), mid, nick, at))

    return {"key": "member:%d" % mid, "member_id": mid, "nick": nick,
            "content_id": cid, "confidence": confidence,
            "detail": "POL login %r (member %s%s) at %s [%s]"
                      % (nick, mid,
                         ", FE Content ID %s" % cid if cid else "", at,
                         confidence)}


def _content_id(db, member_id):
    """This member's Fantasy Earth Content ID, or None.

    The same query as accounts.member_content_id, inlined rather than imported:
    accounts.py is a 4,500-line module that opens its own connections, and this
    is one read on a connection already in hand. If a third caller needs it,
    import accounts rather than growing a second copy.
    """
    try:
        row = db.execute(
            "SELECT hc.content_id FROM handle_content hc"
            " JOIN handle h ON h.id = hc.handle_id"
            " WHERE h.member_id = ? AND hc.content_code = ?"
            "   AND hc.content_id IS NOT NULL AND hc.status = 'active'"
            " ORDER BY h.is_primary DESC, h.id ASC LIMIT 1",
            (member_id, FE_CONTENT_CODE)).fetchone()
    except Exception:                          # noqa: BLE001
        return None
    return row[0] if row else None


def _ip_memory_get(ip, path=None):
    """The FE player last seen unambiguously at `ip`, or None."""
    p = IP_MEMORY if path is None else path
    if not p:
        return None
    blob = _read(p)
    rec = (blob.get("by_ip") or {}).get(ip)
    if not rec:
        return None
    if time.time() - rec.get("at", 0) > IP_MEMORY_TTL:
        return None
    return rec


def _ip_memory_put(ip, ident, tag="feident", path=None):
    """Remember an UNAMBIGUOUS FE player at `ip`.

    Deliberately narrow, because a sticky wrong answer would be worse than the
    bug this helps with. Two conditions, both required:

      * the resolution was ``sole`` or ``roster`` (see REMEMBERABLE) -- one
        member, or several with exactly one owning FE characters;
      * that member OWNS Fantasy Earth characters.

    The second is what keeps a non-FE account from claiming a box. The
    2026-09-04 PS2 test account (member:15, no FE characters, signing in from
    PCSX2 on the FE player's own machine) is exactly the account that must
    never be written here, or it would have inherited the address for a month.
    """
    p = IP_MEMORY if path is None else path
    if (not p or ident.get("confidence") not in REMEMBERABLE
            or not ident.get("member_id")):
        return False
    rec = {"key": ident["key"], "member_id": ident.get("member_id"),
           "nick": ident.get("nick"), "at": time.time()}
    with _lock:
        blob = _read(p)
        by_ip = blob.setdefault("by_ip", {})
        prev = by_ip.get(ip) or {}
        by_ip[ip] = rec
        for k in [k for k, v in by_ip.items()
                  if time.time() - v.get("at", 0) > IP_MEMORY_TTL]:
            del by_ip[k]
        ok = _write(p, blob)
    if ok and prev.get("key") != rec["key"]:
        _log(tag, "%s is now remembered as %s (was %s) -- used only if "
                  "accounts.db has NO session row for this address"
                  % (ip, rec["key"], prev.get("key") or "nobody"))
    return ok


def identify(ip, tag="feident", accounts_db=None, window=None,
             char_store=None, ip_memory=None, prefer_remembered=None):
    """Resolve `ip` to a store identity, always returning a usable dict.

    Order, and why:

      1. A POL session row at this address (`member_for_ip`), which carries its
         own confidence and refuses to guess quietly.
      2. Failing that, the FE player last seen UNAMBIGUOUSLY here. This exists
         because `accounts.open_session()` keeps a row for one hour while a
         player stays logged into POL for an evening: after the hour their row
         is purged and step 1 finds nothing, which used to drop a returning
         player onto an address key and an EMPTY character list. Only consulted
         when step 1 found NOTHING AT ALL -- never to override a live row.
      3. The address. Deliberate, and NOT a shared bucket: `addr:<ip>` is
         per-machine, which is the worst this may degrade to.

    A live row that names a member with NO FE characters, at an address
    remembered for one who has them, is `contested` -- see PREFER_REMEMBERED.
    """
    if prefer_remembered is None:
        prefer_remembered = PREFER_REMEMBERED
    roster = roster_member_keys(char_store)
    got = member_for_ip(ip, tag=tag, accounts_db=accounts_db, window=window,
                        roster=roster)
    if got:
        if got.get("confidence") in REMEMBERABLE and got["key"] in roster:
            _ip_memory_put(ip, got, tag=tag, path=ip_memory)
            return got
        if got["key"] not in roster:
            # The live login owns no FE characters. If this address is
            # remembered for somebody who does, the two claims disagree and
            # neither the address nor anything else on the wire can settle it.
            rec = _ip_memory_get(ip, path=ip_memory)
            other = (rec or {}).get("key")
            if other and other in roster and other != got["key"]:
                if prefer_remembered:
                    _log(tag, "%s: the live POL login is %s, which owns no "
                              "Fantasy Earth characters, and this address is "
                              "remembered for %s, which does. Taking %s "
                              "(FE_PREFER_REMEMBERED_PLAYER is on). If a NEW "
                              "player is meant to be starting here, turn it off "
                              "or clear this address from %s."
                              % (ip, got["key"], other, other,
                                 IP_MEMORY if ip_memory is None else ip_memory))
                    return {"key": other, "member_id": (rec or {}).get("member_id"),
                            "nick": (rec or {}).get("nick"), "content_id": None,
                            "confidence": "remembered-preferred",
                            "detail": "live POL login %s owns no FE characters; "
                                      "took remembered FE player %s "
                                      "[remembered-preferred]"
                                      % (got["key"], other)}
                _log(tag, "WARN %s: the live POL login is %s, which owns NO "
                          "Fantasy Earth characters, while this address is "
                          "remembered for %s, which does. Serving %s, so the "
                          "character list will be EMPTY. If that is the wrong "
                          "player, sign the other POL account out and sign the "
                          "FE player back in -- a session row lasts one hour. "
                          "Set FE_PREFER_REMEMBERED_PLAYER=1 to prefer %s here."
                          % (ip, got["key"], other, got["key"], other))
                got = dict(got, confidence="contested",
                           detail=got["detail"] +
                           "  !! CONTESTED: %s owns FE characters at this "
                           "address and this login does not" % other)
        return got

    rec = _ip_memory_get(ip, path=ip_memory)
    if rec and rec.get("key") in roster:
        _log(tag, "no POL session row for %s -- but %s owns Fantasy Earth "
                  "characters and was the last player seen here (%s). Using "
                  "it. This is the one-hour session-row expiry, not a new "
                  "login; see MEMBER_WINDOW."
                  % (ip, rec["key"],
                     datetime.datetime.utcfromtimestamp(rec["at"]).strftime(
                         "%Y-%m-%dT%H:%M:%SZ")))
        return {"key": rec["key"], "member_id": rec.get("member_id"),
                "nick": rec.get("nick"), "content_id": None,
                "confidence": "remembered",
                "detail": "no live POL session row for %s; remembered FE player "
                          "%s [remembered]" % (ip, rec["key"])}

    owners = sorted(roster)
    return {"key": "addr:" + ip, "member_id": None, "nick": None,
            "content_id": None, "confidence": "address",
            "detail": "no POL session row for %s -- keyed by ADDRESS. Two POL "
                      "accounts on this machine share this roster, and the same "
                      "player from another machine gets a different one.%s" %
                      (ip,
                       ("  !! THE STORE HOLDS CHARACTERS FOR %s -- if one of "
                        "those is you, your POL session row has expired (they "
                        "last one hour); sign out and back into POL, then "
                        "relaunch." % ", ".join(owners)) if owners else "")}


def wire_account(ident, mode="member", fixed=None):
    """The account string to put in 0xC010 MSG_CERTIFICATION_OK.

    Kept separate from the STORE KEY on purpose: the key must be stable forever,
    while the wire name is a string the client keeps, hands to the world door,
    and may display. `member` sends the key itself, which is what makes the
    feworld carry free; `nick` sends the POL nick; `fixed` restores the old
    constant.

    BOUNDED AT 31 BYTES. The client's reader (0x5045f50 -> 0x522f5e0) copies
    until NUL into a fixed buffer, so length is our responsibility, not its.
    """
    if mode == "fixed":
        name = fixed or LEGACY_ACCOUNT
    elif mode == "nick" and ident.get("nick"):
        name = ident["nick"]
    else:
        name = ident["key"]
    raw = name.encode("cp932", "replace")[:31]
    return raw.decode("cp932", "replace")


# ---------------------------------------------------------------------------
# The 0xC007 credential blob -- EVIDENCE GATHERING, not yet a key
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS, AND WHAT IT IS NOT. The module docstring above says the blob
# is a constant against our K=0 login and must never be keyed on. WARNING: THAT WAS
# WRONG, and the correction is the reason this section exists.
#
# It came from applying an FMO measurement to the wrong field. fmo.py's 0x0321
# credentials have TWO identity-shaped things in them:
#
#     +0x00  52B  polcore-minted auth blob -- "VARIES PER SESSION"
#     +0x38  16B  polcore identity        -- stable per install, and MEASURED
#                                            identical across two machines
#
# The "identical across two machines" finding is about **+0x38**, which FE's
# 0xC007 does not carry at all. What it does carry is 52 bytes from the same
# `vtable[0xEA0]` call that fills FMO's +0x00 -- the half fmo.py calls "a
# session token plus a fixed machine/account identity", and whose interesting
# half "belongs to polcore, whose auth our own server already mints elsewhere".
#
# Four captured FE blobs (logs/felobby-live*.log, one machine, one account)
# agree with that shape: byte 2 and bytes 4..7 are identical in all four, the
# leading u32 climbs, and the remaining 44 bytes vary completely.
#
# WARNING: AND THAT IS EXACTLY AS FAR AS THE EVIDENCE GOES. One machine and one
# account cannot distinguish "these bytes identify the ACCOUNT" from "these
# bytes identify the INSTALL", and those want opposite fixes:
#
#   per-ACCOUNT  -> key on them directly; no address, no handoff file, and two
#                   POL accounts on one machine finally get two rosters.
#   per-INSTALL  -> useless for that case; the answer is instead correlating
#                   the varying remainder against the session token WE mint.
#
# So this records observations and DECIDES NOTHING. Nothing below is consulted
# when resolving an identity. `tools/fe_blob_verdict.py` reads the file back and
# says which of the two it is -- once, and only once, two different POL members
# have each presented a blob.
#
# WARNING: DO NOT "SHORTCUT" THIS BY KEYING ON THE FIXED FIELD BEFORE THE VERDICT. If
# it turns out to be the install, that is one roster per MACHINE -- a smaller
# version of the bug this whole module exists to fix, and one that would look
# correct on every single-account test.

BLOB_LOG = os.environ.get("FE_BLOB_LOG") or os.path.join(
    os.path.normpath(os.path.join(_HERE, os.pardir, "data")), "fe_blobid.jsonl")

#: Kept small on purpose -- this is a decisive experiment, not telemetry. Once
#: the verdict is in, the recording should come out again.
BLOB_LOG_MAX = int(os.environ.get("FE_BLOB_LOG_MAX", "500"))


def blob_fields(blob):
    """The candidate identity windows in a 0xC007 credential blob.

    Deliberately returns SEVERAL slices rather than the one I guessed. The
    fixed field looked like bytes 4..7 in four captures from a single machine,
    but picking that window here would bake my guess into the evidence --
    fe_blob_verdict.py recomputes which offsets are actually invariant from the
    full blob, so a wrong guess costs nothing.
    """
    hexed = blob.hex() if isinstance(blob, (bytes, bytearray)) else str(blob)
    return {"full": hexed,
            "lead": hexed[0:8],      # climbs per session
            "fixed": hexed[8:16],    # constant in all four captures so far
            "tail": hexed[16:]}      # the 44 bytes that vary completely


def record_blob(ident, blob, tag="felobby", path=None):
    """Append one observation and return a one-line summary for the log.

    Never raises and never blocks a login: this is a side-experiment riding on
    a live authentication path, and it must not be able to break one.
    """
    f = blob_fields(blob)
    line = ("BLOBID fixed=%s lead=%s member=%s nick=%s ip=%s"
            % (f["fixed"], f["lead"], ident.get("key"),
               ident.get("nick"), ident.get("ip")))
    p = BLOB_LOG if path is None else path
    if not p:
        return line
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "key": ident.get("key"), "member_id": ident.get("member_id"),
           "nick": ident.get("nick"), "ip": ident.get("ip"),
           "content_id": ident.get("content_id"), **f}
    try:
        with _lock:
            existing = []
            if os.path.exists(p):
                with io.open(p, encoding="utf-8") as fh:
                    existing = fh.read().splitlines()
            existing.append(json.dumps(rec, sort_keys=True))
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
            tmp = p + ".tmp"
            with io.open(tmp, "w", encoding="utf-8") as fh:
                fh.write("\n".join(existing[-BLOB_LOG_MAX:]) + "\n")
            os.replace(tmp, p)
    except (OSError, ValueError) as e:
        _log(tag, "WARN blob observation not recorded (%r) -- the log line "
                  "above still has it" % (e,))
    return line


# ---------------------------------------------------------------------------
# felobby -> feworld handoff
# ---------------------------------------------------------------------------

def remember(ip, ident, wire, tag="felobby", path=None):
    """Record who is at `ip` so feworld can key the same roster.

    Written atomically (temp file + os.replace) for the same reason the
    character store is: a container restart landing mid-write must not be able
    to leave a file that parses as "nobody is here".
    """
    p = SESSION_STORE if path is None else path
    if not p:
        return
    rec = {"key": ident["key"], "member_id": ident.get("member_id"),
           "nick": ident.get("nick"), "content_id": ident.get("content_id"),
           "wire": wire, "at": time.time()}
    with _lock:
        blob = _read(p)
        by_ip = blob.setdefault("by_ip", {})
        by_wire = blob.setdefault("by_wire", {})
        by_ip[ip] = rec
        by_wire[wire] = rec
        _expire(by_ip)
        _expire(by_wire)
        if not _write(p, blob):
            _log(tag, "WARN handoff store %s not written -- feworld falls back "
                      "to the account echo, then to an address lookup" % p)
            return
    _log(tag, "handoff: %s -> %s (wire account %r) in %s"
              % (ip, ident["key"], wire, p))


def recall(ip=None, wire=None, path=None):
    """The identity felobby recorded, by account string first and address
    second, or None. Expired records are ignored rather than deleted: a reader
    must not need write access to the handoff store."""
    p = SESSION_STORE if path is None else path
    if not p:
        return None
    with _lock:
        blob = _read(p)
    for table, k in (("by_wire", wire), ("by_ip", ip)):
        if not k:
            continue
        rec = blob.get(table, {}).get(k)
        if rec and time.time() - rec.get("at", 0) <= HANDOFF_TTL:
            return dict(rec, via=table)
    return None


def _expire(table):
    now = time.time()
    for k in [k for k, v in table.items() if now - v.get("at", 0) > HANDOFF_TTL]:
        del table[k]


def _read(path):
    try:
        with io.open(path, encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, ValueError):
        return {}
    return blob if isinstance(blob, dict) else {}


def _write(path, blob):
    # PER-PROCESS temp name. felobby and feworld are separate containers over
    # the same /data, and both now resolve identities, so a shared "<path>.tmp"
    # lets one container's half-written file be os.replace()d into place by the
    # other. The replace stays atomic either way; this stops the two from
    # writing the same scratch file at all.
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(blob, f, indent=1, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
def _selftest():
    """Run: python services/feident.py

    Proves the things whose quiet failure would put the shared roster back: that
    two POL members resolve to two different keys, that an unresolved connection
    lands on its own address key rather than a shared one, and that the handoff
    round-trips by both address and account string.
    """
    import tempfile
    state = {"ok": True}

    def check(label, cond):
        print("%-58s %s" % (label, "ok" if cond else "FAIL"))
        state["ok"] = state["ok"] and bool(cond)

    tmpdir = tempfile.mkdtemp(prefix="feident-")
    db_path = os.path.join(tmpdir, "accounts.db")
    db = sqlite3.connect(db_path)
    db.executescript(
        "CREATE TABLE session (token TEXT PRIMARY KEY, member_id INTEGER,"
        " nick TEXT, peer_ip TEXT, iv TEXT, lobby_port INTEGER,"
        " created_at TEXT, expires_at TEXT);"
        "CREATE TABLE handle (id INTEGER PRIMARY KEY, member_id INTEGER,"
        " is_primary INTEGER);"
        "CREATE TABLE handle_content (handle_id INTEGER, content_code INTEGER,"
        " content_id TEXT, status TEXT, linked_at TEXT);")
    now = _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    older = (_utcnow() - datetime.timedelta(minutes=5)
             ).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.executemany("INSERT INTO session (token, member_id, nick, peer_ip,"
                   " created_at, expires_at) VALUES (?,?,?,?,?,?)", [
                       ("t1", 16, "UA4XX8PKP", "192.0.2.5", now, now),
                       ("t2", 21, "OTHERNICK", "192.0.2.6", now, now),
                       ("t3", 99, "GHOSTNICK", "192.0.2.5", older, now),
                       # 2026-09-04 REGRESSION FIXTURE. The shape that handed
                       # a returning player somebody else's empty roster: the player
                       # who OWNS the characters signed in FIRST, a second
                       # account on the same box signed in AFTER, and "freshest
                       # row wins" therefore named the wrong one.
                       ("t4", 43, "INTRUDER", "192.0.2.8", now, now),
                       ("t5", 42, "REALOWNER", "192.0.2.8", older, now),
                       # Two candidates, NEITHER owning characters -- nothing
                       # can separate them and the answer must say so.
                       ("t6", 50, "TWINA", "192.0.2.9", now, now),
                       ("t7", 51, "TWINB", "192.0.2.9", older, now),
                       # The 2026-09-04 shape at its worst: the OTHER account is
                       # the only one with a live row, because the FE player's
                       # was purged an hour ago. Nothing to tie-break against.
                       ("t8", 88, "PS2TESTER", "192.0.2.10", now, now),
                       # Same login, at an address nobody is remembered for --
                       # a genuinely new player, who must NOT be disturbed.
                       ("t9", 88, "PS2TESTER", "192.0.2.11", now, now),
                   ])
    db.execute("INSERT INTO handle VALUES (1, 16, 1)")
    db.execute("INSERT INTO handle_content VALUES (1, ?, '1000000123',"
               " 'active', ?)", (FE_CONTENT_CODE, now))
    db.commit()
    db.close()

    # Who owns Fantasy Earth characters. The tie-break when an address has more
    # than one candidate, and the gate on being remembered at all.
    roster_path = os.path.join(tmpdir, "fe_characters.json")
    with io.open(roster_path, "w", encoding="utf-8") as f:
        json.dump({"accounts": {"member:16": [{"name": "Lex"}],
                                "member:21": [{"name": "Solo"}],
                                "member:42": [{"name": "RealOwner"}]}}, f)
    mem_path = os.path.join(tmpdir, "fe_ip_members.json")

    def ident(ip, **kw):
        kw.setdefault("accounts_db", db_path)
        kw.setdefault("char_store", roster_path)
        kw.setdefault("ip_memory", mem_path)
        return identify(ip, **kw)

    a = ident("192.0.2.5")
    b = ident("192.0.2.6")
    check("two members resolve to two different keys", a["key"] != b["key"])
    check("member 16 keyed as member:16", a["key"] == "member:16")
    check("FE Content ID resolved", a["content_id"] == "1000000123")
    check("the character owner wins over a ghost at the same address",
          a["member_id"] == 16)

    c = ident("192.0.2.7")
    check("unknown address gets an ADDRESS key, not a shared one",
          c["key"] == "addr:192.0.2.7")
    check("...and it is distinct from every member key",
          c["key"] not in (a["key"], b["key"]))

    narrow = ident("192.0.2.5", window=60)
    check("a narrow window still names the fresh member, not the ghost",
          narrow["key"] == "member:16")

    # ---- 2026-09-04: the failure that started this ------------------------
    d = ident("192.0.2.8")
    check("REGRESSION a FRESHER non-owner does NOT steal the owner's roster",
          d["key"] == "member:42")
    check("...and the pick is reported as the roster tie-break",
          d["confidence"] == "roster")

    e = ident("192.0.2.9")
    check("two candidates and no owner -> still answers", bool(e["member_id"]))
    check("...but says AMBIGUOUS rather than pretending to know",
          e["confidence"] == "ambiguous")
    check("a lone candidate is reported as sole", b["confidence"] == "sole")

    # ---- the one-hour session-row expiry ----------------------------------
    check("an unambiguous FE player is remembered for their address",
          (_ip_memory_get("192.0.2.6", path=mem_path) or {}).get("key")
          == "member:21")
    check("an AMBIGUOUS resolution is never remembered",
          _ip_memory_get("192.0.2.9", path=mem_path) is None)
    check("a non-owner is never remembered in a roster tie-break",
          (_ip_memory_get("192.0.2.8", path=mem_path) or {}).get("key")
          != "member:43")

    db2 = sqlite3.connect(db_path)
    db2.execute("DELETE FROM session WHERE peer_ip = '192.0.2.6'")
    db2.commit()
    db2.close()
    gone = ident("192.0.2.6")
    check("REGRESSION a purged session row no longer loses the roster",
          gone["key"] == "member:21")
    check("...and it is reported as remembered, not as a live login",
          gone["confidence"] == "remembered")

    unknown = ident("192.0.2.77")
    check("memory never invents an identity for an address it never saw",
          unknown["key"] == "addr:192.0.2.77")
    check("...and the address answer names the rosters that DO exist",
          "member:16" in unknown["detail"])

    # ---- CONTESTED: a live non-FE login where an FE player is remembered ---
    # This is 2026-09-04 exactly, and it is the case the roster tie-break
    # CANNOT reach: member 88 is the only candidate with a live row.
    with io.open(mem_path, encoding="utf-8") as f:
        mem = json.load(f)
    mem.setdefault("by_ip", {})["192.0.2.10"] = {
        "key": "member:16", "member_id": 16, "nick": "UA4XX8PKP",
        "at": time.time() - 7200}
    with io.open(mem_path, "w", encoding="utf-8") as f:
        json.dump(mem, f)

    off = ident("192.0.2.10", prefer_remembered=False)
    check("contested is DETECTED, not served silently",
          off["confidence"] == "contested")
    check("...and the default still serves the live login (new players safe)",
          off["key"] == "member:88")
    check("...and the detail names who it is contested with",
          "member:16" in off["detail"])

    on = ident("192.0.2.10", prefer_remembered=True)
    check("REGRESSION 2026-09-04 resolves to the FE player when preferred",
          on["key"] == "member:16")
    check("...and says it took the remembered player",
          on["confidence"] == "remembered-preferred")

    fresh = ident("192.0.2.11", prefer_remembered=True)
    check("a NEW player at an unremembered address is untouched",
          fresh["key"] == "member:88" and fresh["confidence"] == "sole")

    # ---- 2026-09-04 #2: the memory was never written on the path that matters
    # On a box running PCSX2 beside the Viewer BOTH accounts have live rows, so
    # every good login resolves `roster`, never `sole` -- and `roster` was
    # excluded from REMEMBERABLE. Nothing was ever recorded for exactly the
    # machine that needs it; an hour later, with the owner's row purged, there
    # was no memory to detect the collision against and the wrong member was
    # served. Twice in one evening.
    #
    # WARNING: 192.0.2.5 CANNOT test this: the narrow-window call above resolves it
    # `sole` (the ghost falls outside a 60s window) and remembers it either way.
    # The first version of this check used it and passed with the fix REVERTED.
    # 192.0.2.8 is only ever reached as `roster`.
    check("the 192.0.2.8 resolution really is `roster`, not `sole`",
          d["confidence"] == "roster")
    check("REGRESSION a `roster` login IS remembered (not only `sole`)",
          (_ip_memory_get("192.0.2.8", path=mem_path) or {}).get("key")
          == "member:42")

    # Replay the evening on that address: owner's row purged, the other account
    # still signing in. The collision must be SEEN, not served.
    db3 = sqlite3.connect(db_path)
    db3.execute("DELETE FROM session WHERE peer_ip = '192.0.2.8'")
    db3.execute("INSERT INTO session (token, member_id, nick, peer_ip,"
                " created_at, expires_at) VALUES ('tA', 43, 'INTRUDER',"
                " '192.0.2.8', ?, ?)", (now, now))
    db3.commit()
    db3.close()
    check("REGRESSION the purged-owner collision is CONTESTED, not `sole`",
          ident("192.0.2.8", prefer_remembered=False)["confidence"]
          == "contested")
    check("...and preferring the remembered player resolves it to the owner",
          ident("192.0.2.8", prefer_remembered=True)["key"] == "member:42")

    print("\n%s" % ("all checks passed" if state["ok"] else "SOMETHING FAILED"))
    return 0 if state["ok"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
