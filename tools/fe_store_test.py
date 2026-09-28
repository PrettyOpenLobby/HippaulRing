#!/usr/bin/env python3
"""fe_store_test.py -- the Fantasy Earth character store, end to end, no client.

Run from services/:  python ../tools/fe_store_test.py

WHAT THIS PINS, AND WHY IT EXISTS
---------------------------------
Until 2026-08-24 `felobby.py` was launched with no `--account`, so every session
took the argparse default and `data/fe_characters.json` held ONE roster under
"TestPlayer" for the whole server: two FE players would have seen, edited and
deleted each other's characters. Nothing caught it, because with one player the
shared key and a correct key are indistinguishable.

So this test never has fewer than two players in it. It covers:

  * two POL members on two addresses resolve to two DIFFERENT store keys, and
    creating a character under one leaves the other's roster alone;
  * an address with no POL session row gets its OWN key, not a shared bucket;
  * the felobby -> feworld carry agrees, by all three routes (the 0x400F account
    echo, felobby's kv handoff, and a direct member lookup) -- because the
    world door writing the chosen nation onto the wrong player's character is
    the same bug one hop later;
  * charids stay unique ACROSS the store, since feworld sends charid as the
    unit login value and spawns the player entity under it;
  * `--account-mode fixed` / `--account-mode echo` still reproduce the old
    shared behaviour, so the pre-fix captures remain replayable on purpose
    rather than by accident.

It drives felobby's and feworld's real functions -- resolve_identity,
load_roster/save_roster/next_charid, resolve_account -- not copies of them.
The POL accounts are made in a throwaway PostgreSQL database with OpenLobby's
own accounts functions (tools/fepg.py), whichever store the characters use.
"""
import argparse
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.normpath(os.path.join(_HERE, os.pardir, "services"))
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)
sys.path.insert(0, _HERE)
import fepg         # noqa: E402

import feident      # noqa: E402
import felobby      # noqa: E402
import festore      # noqa: E402
import feworld      # noqa: E402

STATE = {"ok": True}


def check(label, cond):
    print("  %-64s %s" % (label, "ok" if cond else "FAIL"))
    STATE["ok"] = STATE["ok"] and bool(cond)


def make_members():
    """Two POL members signed in from two addresses, each holding a Fantasy
    Earth Content ID. Returns their member ids."""
    conn = feident._accounts().connect()
    try:
        ids = []
        for name, ip, cid in (("JDPL7746", "192.0.2.5", "30000074"),
                              ("SWQR3596", "192.0.2.6", "30000075")):
            mid = fepg.pol_member(conn, name, fe_content_id=cid)[0]
            fepg.pol_session(conn, mid, name, ip)
            ids.append(mid)
        return ids
    finally:
        conn.close()


def lobby_args(store, mode="member"):
    """The subset of felobby's argparse namespace these functions read."""
    return argparse.Namespace(char_store=store, member_lookup=None,
                              member_window=None,
                              account_mode=mode, account="TestPlayer",
                              char_probe=False, char_reset=False)


def world_args(mode="resolve"):
    return argparse.Namespace(member_lookup=None, member_window=None,
                              account_mode=mode)


def add_character(store, key, name):
    """What felobby's 0xC002 arm does: allocate a store-wide charid, append,
    save. Uses felobby's own next_charid/save_roster."""
    roster = felobby.load_roster(store, key) or []
    charid = felobby.next_charid(store, roster)
    vals = felobby.char_defaults(False)
    vals["name"] = name
    vals["charid"] = charid
    roster.append(vals)
    felobby.save_roster(store, key, roster)
    return charid


def main():
    tmp = tempfile.mkdtemp(prefix="fe-store-test-")
    store = os.path.join(tmp, "fe_characters.json")
    kv = feident._kv()
    kv.reset(kv.MemoryKV())
    m16, m21 = make_members()
    args = lobby_args(store)

    print("\n-- two players, two rosters " + "-" * 40)
    a = felobby.resolve_identity("192.0.2.5", args)
    b = felobby.resolve_identity("192.0.2.6", args)
    check("two POL members get two different store keys", a["key"] != b["key"])
    check("a member is keyed as member:<id>", a["key"] == "member:%d" % m16)
    check("the FE Content ID is resolved alongside",
          (a["content_id"], b["content_id"]) == ("30000074", "30000075"))

    ca = add_character(store, a["key"], "Fox")
    cb = add_character(store, b["key"], "Other")
    check("player A sees only their own character",
          [c["name"] for c in felobby.load_roster(store, a["key"])] == ["Fox"])
    check("player B sees only their own character",
          [c["name"] for c in felobby.load_roster(store, b["key"])] == ["Other"])
    check("charids are unique ACROSS the store, not per roster", ca != cb)

    print("\n-- a player with no POL session row " + "-" * 32)
    c = felobby.resolve_identity("192.0.2.9", args)
    check("gets an ADDRESS key", c["key"] == "addr:192.0.2.9")
    check("...that is nobody else's", c["key"] not in (a["key"], b["key"]))
    check("...and an empty roster, not somebody else's",
          felobby.load_roster(store, c["key"]) is None)

    print("\n-- felobby -> feworld carry " + "-" * 40)
    feident.remember("192.0.2.5", a, a["wire"])
    feident.remember("192.0.2.6", b, b["wire"])
    w = world_args()
    check("the 0x400F account ECHO resolves to the same key",
          feworld.resolve_account(a["wire"], "192.0.2.5", w) == a["key"])
    check("...for the second player too",
          feworld.resolve_account(b["wire"], "192.0.2.6", w) == b["key"])
    check("a MANGLED echo falls through to the handoff record, not to a guess",
          feworld.resolve_account("garbled", "192.0.2.6", w) == b["key"])
    # no handoff at all: an empty kv, as after a Valkey restart
    handoffs = kv.default()
    kv.reset(kv.MemoryKV())
    try:
        check("with no handoff at all, a direct POL lookup still keys it right",
              feworld.resolve_account("garbled", "192.0.2.5", w) == a["key"])
    finally:
        kv.reset(handoffs)
    check("two world sessions never collapse to one key",
          feworld.resolve_account(a["wire"], "192.0.2.5", w)
          != feworld.resolve_account(b["wire"], "192.0.2.6", w))

    print("\n-- feworld writes onto the RIGHT player's character " + "-" * 16)
    before_b = json.dumps(felobby.load_roster(store, b["key"]), sort_keys=True,
                          default=list)
    feworld._SESSION["account"] = feworld.resolve_account(a["wire"],
                                                          "192.0.2.5", w)
    feworld._SESSION["charid"] = ca
    saved = felobby._default_store
    felobby._default_store = lambda: store
    try:
        hit = feworld._store_char_field(None, "force", 3)
    finally:
        felobby._default_store = saved
    check("the nation lands on player A's character", hit)
    # NOT "force is absent" -- char_defaults() already carries a force field,
    # so the only honest test is that B's roster is byte-for-byte what it was.
    check("...and player B's roster is unchanged, field for field",
          json.dumps(felobby.load_roster(store, b["key"]), sort_keys=True,
                     default=list) == before_b)
    check("player A's really has it",
          felobby.load_roster(store, a["key"])[0].get("force") == 3)
    check("...and B's is still its own value",
          felobby.load_roster(store, b["key"])[0].get("force")
          != felobby.load_roster(store, a["key"])[0].get("force"))

    print("\n-- PROGRESSION survives a relog (2026-09-08) " + "-" * 22)
    # KEY: WHAT THIS IS FOR. EXP, the class-level table and the bag size were
    # GLOBAL KNOBS: --gold/--class-levels/--bag-size served one number to every
    # player, and combat_exp_push said outright that its running total "is not
    # persisted to the character store yet" -- so an evening of kills was gone
    # at the socket close. These pin the contract the knobs moved onto: the flag
    # SEEDS a character that has none, the store is served afterwards, and the
    # operator off switch still wins.
    feworld._SESSION["account"] = feworld.resolve_account(a["wire"],
                                                          "192.0.2.5", w)
    feworld._SESSION["charid"] = ca
    saved = felobby._default_store
    felobby._default_store = lambda: store
    try:
        # EXP accumulates and is written through, not kept in the session only
        feworld._SESSION.pop("exp", None)
        feworld._store_char_field(None, "exp", 1500)
        feworld._SESSION.pop("exp", None)
        sent = []
        real_send = feworld.send
        feworld.send = lambda *args_, **kw: sent.append(args_[2])
        try:
            # exp_model flat: this pins the lifetime TOTAL's persistence. Under
            # the level model (the 2026-09-11 default) the same kill would also
            # SEED the class table at self:1 before the seed checks below run
            # -- that path is tools/fe_prog_test.py's.
            wargs = argparse.Namespace(seq_mode="count", world_prefix=4,
                                       unit_id="0", char_record="off",
                                       exp_model="flat")
            feworld.combat_exp_push(None, None, 0, False, wargs, 25)
        finally:
            feworld.send = real_send
        check("EXP resumes from the STORED total, not from zero",
              feworld._SESSION["exp"] == 1525)
        check("...and the new total is written back for the next login",
              felobby.load_roster(store, a["key"])[0].get("exp") == 1525)

        # the class-level table seeds once, then the store wins
        cargs = argparse.Namespace(class_levels="self:30")
        rows, _bad = feworld.stored_class_levels(cargs, 2)
        check("--class-levels seeds the character's table", rows == [(2, 30)])
        stored = felobby.load_roster(store, a["key"])[0].get("class_levels")
        check("...and it is on the character, keyed by class",
              stored == {"2": 30})
        # a LEVEL UP is now possible: change the store, not the flag
        feworld._store_char_field(None, "class_levels", {"2": 31})
        rows, _bad = feworld.stored_class_levels(cargs, 2)
        check("a stored table BEATS the flag (so levelling is possible)",
              rows == [(2, 31)])
        check("an empty --class-levels is still OFF, whatever is stored",
              feworld.stored_class_levels(
                  argparse.Namespace(class_levels=""), 2) == ([], []))

        # WARNING: 2026-09-11: the 0xD002 select record's first counted sublist is the
        # class-level + per-class-EXP array (struct+0x8e6 -> unit+0x9f0 "Lv.%d"),
        # NOT equipment -- so char-select now reads the SAME stored class_levels
        # the field serves, off one row. Player A's store holds {"2": 31} here.
        def _sublist1(rec):
            off = 0
            for kind, key, _d, _n in felobby.CHAR_FIELDS:
                if kind == "cstr":
                    off = rec.index(b"\0", off) + 1
                else:
                    off += felobby.CHAR_WIDTH[kind]
            n = rec[off]
            off += 1
            return n, [(rec[off + 5 * i],
                        int.from_bytes(rec[off + 5 * i + 1:off + 5 * i + 5],
                                       "big"))
                       for i in range(n)]
        recA = felobby.build_char_record(
            dict(felobby.load_roster(store, a["key"])[0]))
        nA, entA = _sublist1(recA)
        check("D002 sublist-1 is the 7-class LEVEL array, not equip",
              nA == 7 and entA[2] == (31, 0))
        recB = felobby.build_char_record(
            dict(felobby.load_roster(store, b["key"])[0]))
        nB, _ = _sublist1(recB)
        check("a character with no stored levels sends count 0 (client default)",
              nB == 0)

        # the bag size, same contract
        check("--bag-size seeds the character",
              feworld._seeded_value(argparse.Namespace(), "bag_size", 30) == 30)
        feworld._store_char_field(None, "bag_size", 48)
        check("a stored bag size BEATS the flag",
              feworld._seeded_value(argparse.Namespace(), "bag_size", 30) == 48)
        sent = []
        real_send = feworld.send
        feworld.send = lambda *args_, **kw: sent.append(args_[2])
        try:
            feworld.bag_size_push(
                None, None, 0, False,
                argparse.Namespace(bag_size=0, seq_mode="count",
                                   world_prefix=4, unit_id="0",
                                   char_record="off"))
        finally:
            feworld.send = real_send
        check("--bag-size 0 is still OFF even with 48 stored", not sent)

        check("and none of it touched player B",
              felobby.load_roster(store, b["key"])[0].get("exp") is None)
    finally:
        felobby._default_store = saved
        feworld._SESSION.pop("exp", None)

    print("\n-- the old behaviour stays reproducible, and only on request " + "-" * 6)
    fixed = lobby_args(store, mode="fixed")
    fa = felobby.resolve_identity("192.0.2.5", fixed)
    fb = felobby.resolve_identity("192.0.2.6", fixed)
    check("--account-mode fixed puts both players back on ONE key",
          fa["key"] == fb["key"] == "TestPlayer")
    echo = world_args(mode="echo")
    check("feworld --account-mode echo keys by the raw echoed name",
          feworld.resolve_account("TestPlayer", "192.0.2.5", echo)
          == "TestPlayer")

    print("\n-- the store on disk " + "-" * 47)
    # THROUGH THE API, NOT THE FILE. Until 2026-09-08 this opened
    # fe_characters.json and read `["accounts"]` straight out of it; since
    # festore.py the store is a database and the JSON file may not exist at
    # all, so a direct read fails with FileNotFoundError -- a test that broke
    # on a backend change, not a store that lost anything.
    keys = sorted(felobby.store_accounts(store))
    on_db = bool(felobby.use_db(store))
    print("   backend: %s"
          % (festore.where() + " (festore)" if on_db else "fe_characters.json"))
    print("   keys: %s" % ", ".join(keys))
    check("no roster is filed under the old shared default",
          feident.LEGACY_ACCOUNT not in keys)
    if on_db:
        check("the store this run configured really holds the characters",
              festore.count() == sum(felobby.store_accounts(store).values())
              >= 2 and not os.path.exists(store))
    else:
        check("the store this run configured is really on disk",
              os.path.exists(store))

    print("\n%s\n" % ("all checks passed" if STATE["ok"] else "SOMETHING FAILED"))
    return 0 if STATE["ok"] else 1


if __name__ == "__main__":
    # The POL accounts always live in a throwaway database. FE_DB= (empty) is
    # fe_run_all.py's store_json run: the characters stay on the JSON store.
    if fepg.fresh_database() is None:
        sys.exit(fepg.skip_or_fail("fe_store_test"))
    sys.exit(main())
