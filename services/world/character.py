"""Whose character this is: account resolution, stored character fields, the blacklist."""
import struct
import feident  # noqa: E402  -- WHOSE character this is; see its docstring
from . import sess, wire

def resolve_account(echoed, ip, args):
    """The character-store key for this world session.

    Three carries, best first, and the one that fires is LOGGED:

      1. THE ECHO. `echoed` is the account name felobby put in 0xC010 and the
         client handed back in 0x400F. With `--account-mode member` (felobby's
         default) that string IS the store key, so this needs no heuristic at
         all -- it is confirmed against felobby's handoff record rather than
         trusted blind, so a client that mangles or truncates the string falls
         through instead of inventing an account.
      2. THE HANDOFF RECORD, by address: felobby wrote the kv key
         fe:handoff:<ip> at certification (feident.remember).
      3. A DIRECT POL MEMBER LOOKUP for this address, for the case where
         felobby could not write the handoff (Valkey unreachable, say) but
         the account database is readable.

    Falls back to the echoed string itself only when all three miss, and says
    so loudly: that is the old shared-roster behaviour and it should never be
    the quiet default.
    """
    if args.account_mode == "echo":
        print("[feworld]    --account-mode echo: keying the store by the raw "
              "echoed name %r, which is the pre-2026-08-24 behaviour when "
              "felobby sends a constant -- one roster for every player"
              % echoed, flush=True)
        return echoed
    got = feident.recall(ip=ip, wire=echoed)
    if got and got.get("via") == "by_wire":
        print("[feworld]    identity: %s via the 0x400F ACCOUNT ECHO (%r), "
              "confirmed against felobby's handoff%s"
              % (got["key"], echoed,
                 " -- FE Content ID %s" % got["content_id"]
                 if got.get("content_id") else ""), flush=True)
        return got["key"]
    if got:
        print("[feworld]    identity: %s via felobby's HANDOFF RECORD for %s. The "
              "0x400F echo was %r, which did not match a recorded wire name -- "
              "worth a look if it repeats, because the echo is the carry that "
              "needs no address at all." % (got["key"], ip, echoed), flush=True)
        return got["key"]
    ident = feident.identify(ip, tag="feworld", lookup=feident.lookup_arg(args),
                             window=args.member_window)
    if ident.get("member_id") is not None:
        print("[feworld]    identity: %s via a DIRECT POL lookup -- %s. Neither "
              "the echo (%r) nor a handoff record carried it, so felobby may "
              "not be writing the kv handoff (fe:handoff:<ip>)."
              % (ident["key"], ident["detail"], echoed), flush=True)
        return ident["key"]
    print("[feworld]    WARNING: identity NOT resolved for %s: no handoff record, no "
          "POL session row. Keying by %r -- if felobby is sending a constant "
          "account name this is ONE ROSTER FOR EVERY PLAYER, which is the bug "
          "feident.py exists to fix. Check felobby's own `identity:` line."
          % (ip, echoed), flush=True)
    return echoed


def _store_char(args, force_id):
    """Write the chosen nation onto the stored character.

    WARNING: This reaches into felobby's store, which is a service importing a service.
    It is deliberate and small: the store's shape and its atomic-write and .bak
    handling live there, and duplicating them here is how two copies drift apart.
    If this grows past the nation field, split the store into its own module
    rather than copying more of it.
    """
    return _store_char_field(args, "force", force_id)


def _load_char_field(args, key, default=None):
    """Read one key back off the session's stored character.

    The write side (_store_char_field) has existed since the nation choice; the
    UI protocol needs the read side too, because a blacklist and a profile
    comment are things the client SETS and then expects to be told about again
    on the next login.
    """
    acct, charid = sess._SESSION.get("account"), sess._SESSION.get("charid")
    if not acct or charid is None:
        return default
    try:
        import felobby
    except ImportError:
        return default
    for c in felobby.load_roster(felobby._default_store(), acct) or []:
        if c.get("charid") == charid:
            return c.get(key, default)
    return default


# The synthetic character-id space the blacklist allocates from. A blacklist
# entry is {u32 id, std::string name} on the CLIENT side (the vector at
# 0x535c298, stride 0x14, appended by 0x05170be0 from the 0x115F arm) and
# MSG_REMOVE_BLACKLIST erases BY THAT ID -- while the only thing the client
# tells us when adding is a NAME. So every name we accept needs an id that is
# stable across sessions. Real charids are small and allocated by felobby;
# starting well above them keeps a synthetic id from colliding with a real one.
BLACKLIST_ID_BASE = 0x40000000


def blacklist_rows(args):
    """The session character's blacklist, as [{"id": int, "name": str}, ...]."""
    out = []
    for r in _load_char_field(args, "blacklist", None) or []:
        try:
            out.append({"id": int(r["id"]) & 0xFFFFFFFF, "name": str(r["name"])})
        except (KeyError, TypeError, ValueError):
            continue          # a hand-edited store row is not worth dying over
    return out


def blacklist_save(args, rows):
    return _store_char_field(args, "blacklist", rows)


def blacklist_add(args, name):
    """Add `name`, returning (id, rows, was_new).

    DEDUPED BY NAME, deliberately. The client APPENDS the {id, name} pair we
    answer with straight into its own vector -- it does not check for a row it
    already has. Answering the same add twice is how one name ends up in the
    window twice, which is the exact shape of the group-chat double-row bug.
    """
    rows = blacklist_rows(args)
    for r in rows:
        if r["name"] == name:
            return r["id"], rows, False
    # A name we already know as a character keeps that character's real id, so
    # the blacklist and the rest of the world agree about who this is.
    nid = _charid_of_name(args, name)
    if nid is None:
        nid = max([BLACKLIST_ID_BASE - 1]
                  + [r["id"] for r in rows if r["id"] >= BLACKLIST_ID_BASE]) + 1
    rows.append({"id": nid & 0xFFFFFFFF, "name": name})
    blacklist_save(args, rows)
    return nid & 0xFFFFFFFF, rows, True


def blacklist_remove(args, target_id):
    """Drop the row with this id. Returns True if a row went away."""
    rows = blacklist_rows(args)
    keep = [r for r in rows if r["id"] != (target_id & 0xFFFFFFFF)]
    if len(keep) == len(rows):
        return False
    blacklist_save(args, keep)
    return True


def _charid_of_name(args, name):
    """The charid of a character with this name, in the session's own roster.

    Only the session's roster: felobby's store is keyed by account and there is
    no whole-store reader, so a cross-account lookup would mean reaching past
    its API into the file layout. Resolving "I blacklisted my own alt" is worth
    having; everything else falls through to a synthetic id, which works just as
    well because the id only has to be stable, not meaningful.
    """
    acct = sess._SESSION.get("account")
    if not acct:
        return None
    try:
        import felobby
    except ImportError:
        return None
    for c in felobby.load_roster(felobby._default_store(), acct) or []:
        if str(c.get("name", "")) == name and c.get("charid") is not None:
            try:
                return int(c["charid"]) & 0xFFFFFFFF
            except (TypeError, ValueError):
                return None
    return None


def _store_char_field(args, key, value):
    """Write one key onto the session's stored character (see _store_char).

    Goes through felobby.update_roster, which holds the store against other
    world sessions AND the lobby container for the whole read-modify-write.
    This used to be a bare load_roster/save_roster pair, so two sessions on
    two accounts could interleave and the first one's write would vanish.
    """
    acct, charid = sess._SESSION.get("account"), sess._SESSION.get("charid")
    if not acct or charid is None:
        return False
    try:
        import felobby
    except ImportError:
        return False

    def mutate(roster):
        hit = False
        for c in roster:
            if c.get("charid") == charid:
                c[key] = value
                hit = True
        return hit

    return bool(felobby.update_roster(felobby._default_store(), acct, mutate))


def _seeded_value(args, key, seed):
    """A per-character NUMBER whose command-line flag is only its STARTING value.

    KEY: THIS IS THE PATTERN EVERY GLOBAL KNOB MOVES ONTO, and it was called
    `_wallet_value` while it had four callers (GOLD, RING, CRYSTAL, TOTAL
    SCORE). It is not about wallets: `--bag-size` joined it 2026-09-08 and
    `--class-levels` uses the same shape one type up. Renamed rather than
    copied, because two of these drifting apart is the whole failure mode.

    The flag is the STARTING value for a character that has none stored; the
    wire carries whatever the store holds. Until 2026-09-04 these were served
    flat from the flag on every field entry, with the docstrings saying so
    ("NOT PERSISTED ... nothing debits it") -- honest, but it meant the value
    was one global constant for every player and nothing could ever change it
    for one of them. Seeding once and serving the store is the same shape the
    skill-point budget took: the store is the ledger, the wire is a view.

    `seed` None still sends NOTHING, whatever the store holds -- that flag
    being unset means the channel is off, and "unset" must not look like
    "broke" (the [[tm-start-money-is-zero]] shape).

    WARNING: Since 2026-09-11 GOLD and RING ARE SPENT (the shops, the inn, the bank)
    -- always through gold_get / rings_get / wallet_charge / wallet_credit,
    which write the store first and push the view after. Crystal, score and
    bag size are still only per-character values.
    """
    if seed is None:
        return None
    stored = _load_char_field(args, key, None)
    if stored is not None:
        try:
            return int(stored)
        except (TypeError, ValueError):
            pass
    if _store_char_field(args, key, int(seed)):
        print("[feworld]    %s: no stored value for account=%r charid=%s -- "
              "seeded %d from the flag; from now on the STORE is served, not "
              "the flag" % (key, sess._SESSION.get("account"),
                            sess._SESSION.get("charid"), int(seed)), flush=True)
    return int(seed)


def blacklist_send(conn, outbound, mode, be, args, who=0):
    """Push 0x1163 MSG_GET_BLACKLIST_OK -- the WHOLE list, every time.

        [u32 count] then count x { [u32 id][cstr name] }

    read at 0x05024522 (count) and 0x0502454f/0x0502455e (the pair) in arm
    0x05024490. The arm CLEARS the client's vector before it fills it
    (0x050244a2..0x05024510 walks [0x535c29c]..[0x535c2a0] destroying every
    row), so this is a whole-list PUT and re-sending it is idempotent -- unlike
    0x115F, which appends.

    0x20A2 registers no reply of its own (0x051333d0 has none of the
    `mov word ptr [esp+N], imm16` pairs), so 0x1163 is an unsolicited push and
    may be sent whenever the list changes.
    """
    rows = blacklist_rows(args)
    payload = struct.pack(">I", len(rows))
    for r in rows:
        payload += (struct.pack(">I", r["id"])
                    + r["name"].encode("cp932", "replace") + bytes(1))
    wire.send(conn, outbound, wire.inner_msg(0x1163, payload, wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0x1163 MSG_GET_BLACKLIST_OK, %d row(s)%s"
          % (len(rows), " for charid=%d" % who if who else ""), flush=True)
    for r in rows:
        print("[feworld]        id=%d %r" % (r["id"], r["name"]), flush=True)
