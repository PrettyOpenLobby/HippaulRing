#!/usr/bin/env python3
"""felobby.py -- Fantasy Earth LOBBY probe, f-earth00.pol.com:54849.

The second hop of FE's login. fellb.py gets FE here: the balancer on
54848 hands back this address and FE dials it immediately, announcing
`Cipher Master Key ...fantasyearth` on the way in. The channel is Blowfish with
NON-STANDARD tables -- see feblowfish.py for the tables and their derivation.

TWO MODES. `--capture` is the original probe: accept, read, close. The default is
`--handshake`, which plays the server side of FE's 3-phase key exchange far
enough to PROVE whether we can read its session key.

THE EXCHANGE (mapped from the client image)
-------------------------------------------
Framing is `[u16 len][u16 id][body]`, big-endian, len counting after itself.

  phase 1  C->S  id 0x34   the client generates a RANDOM SESSION KEY (rand() at
                           0x51b8714, [obj+0x10] bytes), enciphers it under the
                           MASTER key `fantasyearth`, and sends it. Written with
                           the transport cipher OFF (0x522f4b0 flag 0), so the
                           24 bytes on the wire are master-key ciphertext.
  phase 2  S->C  id 0x35   our reply. The client deciphers the body into
                           [cipherobj+0x18] via vtable slot +0xc. ANY OTHER ID
                           ABORTS (cmp word,0x35 / je at 0x05232ae0).
  phase 3  C->S  id 0x36   the client ECHOES the deciphered body back, now with
                           the transport cipher ON (0x522f4b0 flag 1), i.e.
                           enciphered under the session key.

WHY PHASE 3 IS THE ORACLE. Because the client echoes what we sent, we do not need
it to "accept" anything to know we are right: send a known challenge in 0x35, and
if our derived session key is correct the 0x36 body decrypts back to exactly that
challenge. Wrong key -> garbage. That is a clean pass/fail per attempt, decided
here rather than by squinting at FE's log -- which matters because the phase-1
plaintext is a RANDOM KEY and so is unrecognisable on its own (the mistake that
made an earlier mode sweep look like a negative result when it proved nothing).

`--master-mode` selects how the phase-1 blob is deciphered (ecb/cbc, be/le). Each
FE launch tests one; the tool says plainly whether it matched.

WARNING: WHY IT CLOSES, AND WHY THAT IS NOT OPTIONAL. A logger-only bind that holds the
connection open is worse than leaving the port shut: the connect succeeds, FE
waits for a reply that never comes, and there is no way back to the Viewer but
killing it (the same warning fellb.py's own header carries).
Closing right after the read gives FE a clean disconnect, so it fails fast and
still shows its dialog. Do not "improve" this by keeping the socket open.

It also EXITS on its own -- after `captures` connections or `life` seconds --
because a stray listener on this host is a real hazard (lingering python
processes lock polinject.dll and break the next injected run).

Usage (on the host, outside docker -- deliberately, so a capture run leaves
the running docker stack untouched):

    python services/felobby.py --port 54849 --captures 1 --life 300 \
        --out logs/felobby-capture.bin

Then launch FE. The capture lands as raw bytes plus a hexdump in the log.

WHAT TO DO WITH THE BYTES
-------------------------
Plaintext shape is known from the same dispatch FE uses on 54848:
`[u16 len][u16 id][u16 f2][u16 f3][body]`, big-endian, len counting after itself.
The first message should be MSG_LOBBY_LOGIN_REQ, and the sibling ids in that
switch are 0x7001/0x7002 (LOBBY_LOGIN), 0x7821/0x7822 (JOIN_GAME) and
0x7831/0x7832 (GET_GAME_INFO). That is a strong crib: try ECB first (each 8-byte
block independent), then CBC/OFB with a zero IV, using the +1 tables. A plausible
`len` in the first two plaintext bytes is the tell.

THE CHARACTER SCREEN (added 2026-08-17)
---------------------------------------
The lobby chain is closed; the screen behind it is now served too. Its four
requests and their only acceptable answers, each pair taken from the builder's
own registration call rather than guessed:

    0xC001 GET_CHARACTER_LIST  -> 0xD002 OK / 0xD003 NG
    0xC002 ADD_CHARACTER       -> 0xD004 OK [u32 CharaID] / 0xD005 NG [u32 err]
    0xC003 DELETE_CHARACTER    -> 0xD006 OK (no fields)   / 0xD007 NG [u32 err]
    0xC004 DECIDE_CHARACTER    -> 0xD008 OK [u16][u32][u8]/ 0xD009 NG [u32 err]

0xD002's record layout is in CHAR_FIELDS below. 0xC004 is the one that matters:
it is the last thing the screen does, and 0xD008 releases it.

WARNING: These handlers all live in the CHARACTER sub-dispatcher 0x5023ad0, not the
lobby switch 0x05046e09. An id missing from the lobby switch is not unhandled --
its default arm (0x050472f1) chains five more dispatchers.

WHOSE CHARACTERS THEY ARE (fixed 2026-08-24)
--------------------------------------------
Those four handlers read and write `data/fe_characters.json`, and until today
they keyed it by `--account` -- which nothing ever passed, so every session took
the argparse default "TestPlayer" and the whole server shared ONE roster. Two FE
players would have seen, edited and deleted each other's characters.

The key is now the POL member the connecting address resolves to; `feident.py`
holds that resolution, the felobby -> feworld handoff, and an honest account of
both limits (two POL accounts behind one address still collide, and the lookup
needs host networking to see real client addresses at all). `--account` survives
only under `--account-mode fixed`, for reproducing old captures.

The listener is THREADED for the same reason: it used to serve one connection to
completion -- up to --read-window 300s -- before accepting the next, so a second
player's FE waited out the first player's entire session. Per-player rosters are
meaningless while only one player fits in the lobby.
"""
import argparse
import contextlib
import io
import json
import os
import socket
import struct
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


import feident  # noqa: E402  -- WHO is on the socket; see its module docstring
import fegamedata  # noqa: E402  -- dat.pak's starting-gear and item tables
from fenet import (bf_decrypt, bf_encrypt, fold_checksum,  # noqa: E402
                   hexdump, inner_msg, recv_frame, send_frame, traffic_unwrap,
                   traffic_wrap, unwrap, wrap, wrap2)

try:
    # KEY: THE PLAYER DATABASE -- see festore.py for what moved into it and why
    # it is its own file rather than a table in accounts.db. Guarded because a
    # store that will not import must degrade to the JSON file it replaces,
    # loudly, rather than take the lobby down: a player who cannot reach the
    # character screen has no way back to the Viewer.
    import festore  # noqa: E402
except ImportError as _e:                                    # pragma: no cover
    print("[felobby] WARNING: festore.py did not import (%r) -- staying on the JSON "
          "character store" % (_e,), flush=True)
    festore = None

#: The character store is read-modify-written whole, and the listener is now
#: THREADED (one player must not have to wait out another's --read-window), so
#: two select screens creating characters at the same moment would otherwise
#: race and one roster would win the file. Held only across the load/mutate/save
#: of a single message.
_store_lock = threading.Lock()


def _flock(fh):
    """Advisory whole-file lock, blocking. fcntl in the containers (Linux),
    msvcrt on the Windows host; a platform with neither gets the thread lock
    alone, which is what this file had until 2026-09-04."""
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        return True
    except ImportError:
        pass
    try:
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        return True
    except ImportError:
        return False


def _funlock(fh):
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return
    except ImportError:
        pass
    try:
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except ImportError:
        pass


@contextlib.contextmanager
def roster_lock(path):
    """Hold the character store against OTHER THREADS *and* THE OTHER
    CONTAINER.

    WARNING: TWO PROCESSES WRITE THIS FILE. felobby (create/delete, here) and feworld
    (nation, skills, blacklist, comment, tutorial flag -- through
    update_roster below) run in separate containers over the same /data, and
    every write is a whole-file read-modify-write. A thread lock covers one
    process; the two used to share nothing at all, and also shared ONE scratch
    name (`<store>.tmp`), so one container could os.replace() the other's
    half-written temp file into place. feident fixed the same shape for its
    own store with a per-pid temp name; this does that AND takes an advisory
    lock on `<store>.lock` (a sidecar, so the lock never touches the file that
    os.replace() swaps out from under it).
    """
    with _store_lock:
        fh = None
        if path:
            try:
                d = os.path.dirname(os.path.abspath(path))
                os.makedirs(d, exist_ok=True)
                fh = open(path + ".lock", "a+b")
                _flock(fh)
            except OSError as e:
                print("[felobby] WARNING: store lock %s.lock not taken (%s) -- this "
                      "write is protected against other threads only, not "
                      "against the other FE container" % (path, e), flush=True)
                if fh is not None:
                    fh.close()
                fh = None
        try:
            yield
        finally:
            if fh is not None:
                try:
                    _funlock(fh)
                except OSError:
                    pass
                fh.close()


def update_roster(path, account, mutate):
    """Atomically read `account`'s roster, apply `mutate(roster)`, save.

    THE ONLY WRITE PATH OTHER SERVICES SHOULD USE. feworld used to call
    load_roster/save_roster back to back with nothing held, so two world
    sessions on different accounts could interleave -- A loads the file, B
    loads the file, A saves, B saves, and A's skill purchase is gone. Returns
    what `mutate` returned, or None when the account has no roster (nothing is
    written then; a missing roster is not a place to create one).

    WARNING: ON THE DATABASE THE SIDECAR LOCK FILE IS GONE, and that is the point.
    `roster_lock` degrades to a thread-only lock whenever `<store>.lock` cannot
    be opened -- and says so, but only in a log line nobody is reading during a
    live session. festore.update_roster runs the read and the write inside one
    BEGIN IMMEDIATE, so the cross-container exclusion is sqlite's writer lock
    and has no degraded mode to miss.
    """
    db = use_db(path)
    if db:
        return festore.update_roster(
            account, lambda roster: mutate(_normalise(roster)), db)
    with roster_lock(path):
        roster = load_roster(path, account)
        if roster is None:
            return None
        result = mutate(roster)
        save_roster(path, account, roster)
        return result


def _load_crypto():
    """The Blowfish core + FE's tables, from `services/feblowfish.py`.

    Everything the cipher needs lives under `services/`, which compose both
    builds and bind-mounts, so the container has it too. The table .bin files
    are generated by `tools/gen_blowfish_tables.py` from public Blowfish
    constants -- see feblowfish.py's header for the derivation.
    """
    import feblowfish as m
    return m, *m.load_tables()


def default_world_ip():
    """The `--world-ip` default: the address this host is reachable at.

    The same ladder fellb.py walks for POL_FELLB_IP, because the two hops are
    the same host and a lobby that hands out a different address than the
    balancer did is a bug nobody can see from either log alone. Both compose
    files pass --world-ip explicitly; this is what a bare host run gets.
    """
    return (os.environ.get("POL_ADVERTISE") or os.environ.get("POL_STUB_IP")
            or "127.0.0.1")


def cp932(s, limit=None):
    """`s` as cp932 bytes, never raising, never ending in a split DBCS pair.

    Two of this file's encodes used to be bare `.encode("cp932")`, which raises
    on any character outside the codepage and takes the whole session thread
    with it -- an emoji in POL_FE_WORLD would have closed the lobby socket on
    every player. With `limit`, the cut is re-decoded and re-encoded so a lead
    byte left at the end of the window is dropped rather than sent.
    """
    raw = (s or "").encode("cp932", "replace")
    if limit is not None and len(raw) > limit:
        raw = raw[:limit].decode("cp932", "ignore").encode("cp932", "replace")
    return raw


def default_worlds():
    """The `--worlds` default, with the NAME taken from `POL_FE_WORLD`.

    WARNING: THIS IS THE HALF OF A SHARED VALUE, NOT A PRIVATE DEFAULT. The FE world
    name reaches a player through two unrelated services: this one serves it in
    0xD00C to FE's own world picker, and responders.py puts it in the POL
    content profile. Those were two separate string literals, and they agreed
    only because they were written an hour apart from the same value -- agreement
    by luck. `POL_FE_WORLD` (one env var, shared by both services' compose
    environment) is the
    source of truth; both sides read it, so a change lands on both or neither.

    Same shape as FFXI's name by design -- `LSB_SERVER_NAME` -> XI_MAIN_SERVER_
    NAME -- and the same VALUE, because the two titles must not disagree about
    what this server is called. Making responders import this module instead was
    considered and rejected: it is a 1,500-line capture probe that pulls in
    fenet, feident and the Blowfish tables, which is a lot of machinery to read
    ten characters.

    WARNING: COLONS ARE REPLACED, not passed through: parse_worlds splits the spec on
    ':' and requires exactly four parts, so a name containing one would arrive
    as a malformed record -- and `--worlds` overriding this whole string is
    still the escape hatch for anything more exotic.
    """
    name = (os.environ.get("POL_FE_WORLD") or "PlayOnline").strip() or "PlayOnline"
    return "0:1:0:" + name.replace(":", " ")


def parse_worlds(spec):
    """`UnitID:GameID:UserNum:WorldName` records for 0xD00C, comma-separated.

    One tuple per game unit. WorldName is truncated to 35 cp932 bytes because the
    client's record is a fixed 44 bytes with the string at +8 and read by a
    copy-until-NUL (0x522f5e0) that does not bound itself.
    """
    out = []
    for rec in spec.split(","):
        rec = rec.strip()
        if not rec:
            continue
        parts = rec.split(":")
        if len(parts) != 4:
            raise SystemExit("--worlds record %r is not "
                             "UnitID:GameID:UserNum:WorldName" % rec)
        out.append((int(parts[0], 0), int(parts[1], 0), int(parts[2], 0),
                    parts[3]))
    if not out:
        raise SystemExit("--worlds must offer at least one game unit")
    return out


# ---------------------------------------------------------------------------
# 0xD002 MSG_LC_GET_CHARACTER_LIST_OK -- the character record, mapped 2026-08-17
#
# Walked read by read over 0x05025018..0x050256e9 in the runtime dump of
# FE_Client.dll (base 0x04F90000). The earlier
# note called this "~30 reads mixing u8/u16/u32/cstr under conditionals" and
# warned it off as unmappable; that was pessimism. The parse is STRAIGHT-LINE --
# one unconditional chain of typed reads per record, then three counted
# sub-lists. The only branch in it is the record loop's own.
#
# The reader wrappers give their widths away by the byte counter they bump at
# [0x5338360], and their endianness by their tails: 0x522f880/0x522f970 call
# ntohs, 0x522f8c0/0x522f9b0/0x522fa30 call ntohl (IAT thunks 0x524d3b0 and
# 0x524d3b6 -> 0x525f404 / 0x525f408). So EVERYTHING HERE IS BIG-ENDIAN.
#
#     0x5045dc0 -> u8    0x5045e30 -> u16    0x5045e60 -> u32 (unsigned)
#     0x5045e90 -> i8    0x5045ec0 -> i16    0x5045ef0 -> i32 (signed)
#     0x5045f20 -> 4 bytes into a FLOAT member
#     0x5045f50 -> NUL-terminated string (0x522f5e0 consumes the NUL too)
#
# THE HEADER IS TWO COUNTS, NOT A COUNT AND A FORMAT: `[u32 total][u16 filled]`.
# 0x4fe7150(total) resizes the slot vector, then for i in 0..total-1 the compare
# at 0x0502505f takes one of two arms --
#
#     i <  filled   parse a full record; slot = 0x4fe70b0(i, charid, 1)
#     i >= filled   0x4fe70b0(i, -1, 0), and NOTHING IS READ FROM THE WIRE
#
# -- so the trailing slots are the select screen's EMPTY slots and they cost
# zero bytes. (0x4fe70b0 mallocs 0x1260, stores arg2 at +0x34 and arg3 -- the
# "occupied" byte -- at +0. The old note here guessed the u16 was a per-record
# format selector "re-read at the top of every iteration"; it is read ONCE into
# a local before the loop, and 0x05025056 re-reads that local, not the wire.)
#
# total == 0 still short-circuits (0x05025050) straight to the screen's accept
# store, which is why the empty list was already servable.
CHAR_FIELDS = [
    # (width, key, dest, note) -- dest is the offset in the client's 0x1260-byte
    # character struct, kept only as a cross-reference back to the disassembly.
    # NAMED, not guessed: the client sends BOTH of these straight back. The
    # delete builder (0x050f86b6) writes [esi+0x1258] then [esi+0x34] and logs
    # them as `MSG_CL_DELETE_CHARACTER_REQUEST unitID=%d`, and the decide builder
    # (0x050f88a6) does the same under `... unitID=%d chr=%d`. So the first byte
    # of the record is the GAME UNIT the character lives on -- the same UnitID
    # served in 0xD00C -- and it defaults to 0xFF ("none") in the ctor.
    ("u8",   "unit",  0x1258, "game unit id; echoed back in 0xC003/0xC004"),
    ("u32",  "charid", 0x34,  "the slot key: 0x4fe70b0(i, THIS, 1); echoed back"),
    ("u8",   "u8_b",  None,   "READ AND THROWN AWAY by this parser"),
    ("i32",  "f60",   0x60,   ""),
    ("u32",  "f6c",   0x6c,   "sent BEFORE f68 -- the pair is swapped on the wire"),
    ("u32",  "f68",   0x68,   ""),
    ("cstr", "name",  0x01,   "buffer +0x01..+0x21, 33 bytes including the NUL"),
    # THE APPEARANCE, and this is measured, not guessed. 0x04fe7519 copies the
    # record straight into the character-edit/appearance struct, field by field:
    #
    #   record +0x22 -> +0x3aa    +0x23 -> +0x3ab    +0x24 -> +0x3b0
    #   record +0x28 -> +0x3b4    +0x2a -> +0x3b6    +0x2b -> +0x3b7
    #   record +0x2c -> +0x3b8    +0x2d -> +0x94     +0x30 -> +0x3bc
    #
    # and the ADD_CHARACTER request (0xC002) sends exactly those appearance
    # bytes back out -- `[u8 +0x3aa][u8 +0x3ab][u32 0][u8 +0x3b6][u8 +0x3b8]
    # [u8 +0x3b7]`. So the five bytes a player picks at creation belong in
    # record fields 0x22, 0x23, 0x2a, 0x2c and 0x2b, and putting them there is
    # what makes a created character keep its own face.
    #
    # `sex` is named outright: the model-path builder at 0x0507a04f reads
    # +0x3aa, shifts it by 2 and indexes a table of (directory, prefix) pairs
    # for `Model\%s\%sface%02d_%d.mdl` -- which is why an all-zero record
    # always produced Model\Male\m_*. The other four are the remaining
    # creation choices; which is face, hair, colour is NOT established, so they
    # keep positional names.
    ("u8",   "sex",   0x22,   "-> +0x3aa; picks Male/m_ vs the other table row"),
    ("u8",   "look1", 0x23,   "-> +0x3ab"),
    ("u32",  "f24",   0x24,   "-> +0x3b0"),
    ("u16",  "f28",   0x28,   "-> +0x3b4"),
    ("u8",   "look2", 0x2a,   "-> +0x3b6"),
    ("u8",   "look3", 0x2b,   "-> +0x3b7"),
    ("u8",   "look4", 0x2c,   "-> +0x3b8"),
    ("i8",   "f2d",   0x2d,   ""),
    # THE NATION, and this closes the loop the ctor default hinted at. The
    # record->character copy at 0x04fe7566 puts this field into [char+0x3bc], and
    # 0x04ff25dd compares THAT against 0x7FFFFFFF to decide whether the character
    # has a nation at all -- the very value 0x4fe70b0 writes here as its ctor
    # default. The force module reads the same field off "my character"
    # (0x507d570 -> [+0x3bc]) at 0x050054b1, and the force-info parser compares it
    # against a force record's own id at 0x050050d3.
    #
    # WARNING: SO ZERO WAS NEVER "UNSET". Nations are 1..5 (see feworld --forces), and
    # 0x7FFFFFFF is the sentinel for none. Serving 0 told the client the character
    # belonged to nation ZERO, which is not a nation.
    #
    # feworld writes the chosen nation onto the stored character under this same
    # key when 0x4012 MSG_JOIN_FORCE_REQUEST arrives, so a saved choice now
    # reaches the client on the next login instead of only sitting on disk.
    ("u32",  "force", 0x30,   "the NATION; 0x7FFFFFFF = none"),
    ("cstr", "s38",   0x38,   "buffer +0x38..+0x5F, 40 bytes including the NUL"),
]
CHAR_FIELDS += [("i16", "w%02x" % o, o, "") for o in range(0x70, 0xAA, 2)]
CHAR_FIELDS += [("f32", "f%02x" % o, o, "") for o in range(0xAC, 0xC4, 4)]
CHAR_FIELDS += [("u32", "d%02x" % o, o, "") for o in (0xC4, 0xC8)]
CHAR_FIELDS += [("f32", "f%02x" % o, o, "") for o in range(0xCC, 0xDC, 4)]
CHAR_FIELDS += [
    # The client LOGS these three, which is what named them and what makes them
    # the alignment oracle for everything above:
    #   0x05025479  "<date> %d/%d/%d %dh - %d/%d/%d %dh"
    #   0x05025493  "use-prohibited comment %s"
    # Both dwords go through the divide-by-100 ladder at 0x050253ae, which reads
    # them as YY/MM/DD/HH -- i.e. PACKED DECIMAL, YY*1000000 + MM*10000 +
    # DD*100 + HH. Left 0 by default: a populated range is a SUSPENSION, and
    # there is no reason to hand the client one while probing.
    ("u32",  "period_from", 0xDC, "packed decimal YYMMDDHH"),
    ("u32",  "period_to",   0xE0, "packed decimal YYMMDDHH"),
    ("cstr", "comment",     0xE4, "the suspension comment the client prints"),
]

CHAR_WIDTH = {"u8": 1, "i8": 1, "u16": 2, "i16": 2, "u32": 4, "i32": 4, "f32": 4}


def _char_pack(kind, value):
    if kind in ("u8", "i8"):
        return struct.pack(">B", int(value) & 0xFF)
    if kind in ("u16", "i16"):
        return struct.pack(">H", int(value) & 0xFFFF)
    if kind == "f32":
        return struct.pack(">f", float(value))
    if kind == "cstr":
        return value.encode("cp932", "replace") + b"\0"
    return struct.pack(">I", int(value) & 0xFFFFFFFF)


def char_defaults(probe=False):
    """Every field zero -- or, with `probe`, each field carrying its own WIRE
    OFFSET as its value.

    Probe mode exists because 55 of these 61 fields are still unnamed. The client
    renders and logs some of them, and a value that IS its own offset can be
    traced straight back to the bytes that produced it, turning one live run into
    a naming pass. It is not the default because an arbitrary id in a field the
    client uses as a table index is a plausible way to crash it, and zero is the
    value least likely to do that.
    """
    # `name` is seeded before the walk, not after it: in probe mode every value
    # IS its own offset, so a string whose length changes afterwards would slide
    # every offset below it and quietly make the probe lie.
    # `force` is seeded like `name`: a character with no nation must carry the
    # 0x7FFFFFFF sentinel, and neither 0 nor a probe offset is a valid nation.
    vals, off = {"name": "Tester", "force": 0x7FFFFFFF}, 0
    for kind, key, _dest, _note in CHAR_FIELDS:
        if kind == "cstr":
            if key not in vals:
                vals[key] = ("+%02x" % off) if probe else ""
            off += len(vals[key].encode("cp932", "replace")) + 1
        else:
            if key not in vals:
                vals[key] = off if probe else 0
            off += CHAR_WIDTH[kind]
    # WARNING: ALL THREE SUB-LISTS DEFAULT TO EMPTY, AND THAT IS A CORRECTNESS FIX, NOT
    # TIDYING. They used to carry one sentinel entry each -- item UID 0x12345678
    # / NO 0x1234, equip 0x11111111, skill 0x2222 -- chosen to be unmistakable in
    # the client's `Item UID %8d,NO %4d` line, which is how the record's
    # alignment was proven (twice, 2026-08-17).
    #
    # Those values are also DELIBERATELY ABSURD IDs, and the client resolves them
    # against its own tables. Serving a character carrying them made FE parse the
    # record, print the item line, and then RETURN TO THE VIEWER -- no crash
    # marker, no error string, just the title exiting and pol.exe bringing the
    # shell back up, which is the "second PlayOnline window". An EMPTY list
    # rendered the character screen fine and let a character be created, so the
    # difference was the record we served, not the renderer.
    #
    # The oracle did its job and the alignment is settled, so the default is now
    # the realistic one: a brand-new character owns nothing. Ask for the
    # sentinels explicitly (`items=1`) when the alignment check is wanted again,
    # and expect the bail while the ids are still fictional.
    vals["equip"] = []
    vals["skills"] = []
    vals["items"] = []
    return vals


def worn_items(vals):
    """The item rows this record carries: the WORN ones, in equip-list order.

    WARNING: LIVE 2026-09-08, read off the client's own builder. The record->unit
    builder (0x04fe77e3) walks this array and, for every present entry,
    constructs the item, registers it and calls unit->Equip(uid, -1, 1) at
    0x04fe7880 UNCONDITIONALLY -- there is no worn flag; being in the list IS
    being worn. The shim's Equip hook showed all TEN bag items equipped at
    build, last-per-slot winning: the second Beginner Wand (uid 1005) replaced
    the first in worn 0, a second body piece replaced the first in worn 2.
    That is why the character-select screen and the field disagreed and why
    players had to re-equip by hand every session.

    So the bag does NOT belong here -- the field's 0x1000 record carries it,
    and constructs any uid the client has not met (item_fields carries the
    item number). The `equip` pairs decide what rides along; order follows
    the equip list so two pocket items (slot type 11) land in worn 11 then 12
    the way they were assigned."""
    rows = [tuple(x) for x in vals.get("items", [])]
    by_uid = {int(r[0]): r for r in rows if len(r) >= 3}
    out, seen = [], set()
    for _slot, uid in vals.get("equip", []):
        uid = int(uid)
        if uid in by_uid and uid not in seen:
            out.append(by_uid[uid])
            seen.add(uid)
    return out


def class_level_record(vals):
    """The POSITIONAL (level, exp) array for the 0xD002 record's first counted
    sublist -- entry i is class i, NOT a (class, level) pair.

    WARNING: 2026-09-11, STATIC: this sublist is the class LEVEL + per-class EXP array,
    NOT equipment. The client parser (0x05025554) reads `[u8 count]` then per
    entry `{u8 -> struct+0x8e6+i, u32 -> struct+0x8f0+4i}`, and the character->
    unit copy (0x04fe78e0 / 0x04fe7560) moves struct+0x8e6 -> unit+0x9f0 (the
    class-level byte array the equip validator 0x05076194 reads and the "Lv.%d"
    draw at 0x050cd299 shows) and struct+0x8f0 -> unit+0x9f8 (the per-class EXP
    dword, the first half of "Exp %d/%d" at 0x050e1388). The two arrays fit 7
    classes each (FE_CLASS_BASIC_PARAM_DATA has 7 rows).

    felobby USED to pack the equip (slot, uid) pairs here, on the belief that it
    was "equipment". It is not: equip slot numbers landed in the class-level
    array and uids in the per-class EXP, so a seeded character drew a garbage
    "Lv." at the select screen, and the field only looked right because
    feworld's 0x1075 (--class-levels) overwrites unit+0x9f0 AFTER field entry.
    The worn set is carried by the item list (worn markers) and dressed in the
    field by the 0x1000 bag markers / 0x107A -- not by this sublist. So char-
    select now reads the SAME stored class_levels the field serves, off the one
    festore row, instead of two independent values that disagreed.

    Empty when the character has no stored class_levels -- a fresh character's
    "Lv." then falls to the client's own default exactly as before (the old
    equip list was almost always empty too), so this changes nothing for a brand
    new character and everything for one that has progressed.

    Per-class EXP is left 0 here on purpose: only a single total is stored (not a
    per-class split) and the select screen's EXP readout is not confirmed to draw
    from this array -- the field pushes the real EXP via 0x1075 after entry
    (feworld.exp_numerator_push). Serving a number we cannot place is how the
    "Exp 505290270/1679" garbage happened; 0 says "nothing to claim"."""
    cl = vals.get("class_levels")
    if not cl:
        return []
    table = {}
    for k, v in (cl.items() if isinstance(cl, dict) else cl):
        try:
            table[int(k)] = int(v) & 0xFF
        except (TypeError, ValueError):
            continue
    if not table:
        return []
    # Always the full 7-class array: the unit copy reads exactly 7 bytes from
    # +0x8e6 regardless of count, and a dense array cannot misalign a class.
    return [(table.get(i, 0), 0) for i in range(7)]


def build_char_record(vals):
    """One character, in WIRE order -- which is the parser's call order, not the
    order of the struct offsets. f6c precedes f68, and `unit` (+0x1258, the last
    member of the struct) is the very FIRST byte on the wire."""
    out = b""
    for kind, key, _dest, _note in CHAR_FIELDS:
        out += _char_pack(kind, vals[key])
    # class levels + per-class EXP: u8 count, then count x (u8 level -> +0x8e6+n,
    # u32 exp -> +0x8f0+4n). See class_level_record -- this is NOT equipment.
    levels = class_level_record(vals)
    out += struct.pack(">B", len(levels))
    for lvl, exp in levels:
        out += struct.pack(">BI", lvl & 0xFF, exp & 0xFFFFFFFF)
    # skills: u16 count, then count x u16 -> +0x90c+2n
    skills = vals["skills"]
    out += struct.pack(">H", len(skills))
    for s in skills:
        out += struct.pack(">H", s & 0xFFFF)
    # items: u16 count, then count x (u32 uid, u16 no, u8) into the 96-entry
    # array of 0x30-byte records malloc'd at 0x050255d1 (0x1204 = 4 + 0x60*0x30).
    # WARNING: the count is NOT bounded -- 0x0502564c only tests it against zero
    # -- so more than 96 walks off the end of the client's own buffer.
    items = worn_items(vals)
    out += struct.pack(">H", len(items))
    # WARNING: A ROW MAY BE FOUR LONG. feworld's Stack button merges duplicate rows
    # and writes a COUNT as an optional fourth element (see item_rows there);
    # this record has no count field measured, so the extra number is dropped
    # here rather than guessed into the unmeasured u8.
    for uid, no, flag, *_rest in items:
        out += struct.pack(">IHB", uid & 0xFFFFFFFF, no & 0xFFFF, flag & 0xFF)
    return out


def _default_store():
    """`<repo>/data/fe_characters.json`, which is the same file in both places.

    ONE relative path covers host and container: services/ is bind-mounted at
    /app, so `_HERE/../data` is `pol-server/data` on the host and `/data` in the
    container -- the same directory either way. (Probing for an absolute `/data`
    first is wrong on Windows, where it resolves to `<current drive>/data`: the
    host wrote to `E:/data` and looked like it had worked.)
    """
    d = os.path.normpath(os.path.join(_HERE, "..", "data"))
    return os.path.join(d if os.path.isdir(d) else _HERE, "fe_characters.json")


# --------------------------------------------------------------------------- #
# KEY: THE PLAYER DATABASE (2026-09-08). The five functions below are the store's
# whole public API -- felobby creates and deletes, feworld writes nation,
# skills, items, equip, palette, blacklist, comment and the wallet through
# them. Each one now DISPATCHES to festore when a database is in use and falls
# through to the original JSON body when it is not, so the switch is one
# environment variable and NOT ONE CALL SITE MOVED.
# --------------------------------------------------------------------------- #
#: WARNING: DERIVED FROM THE STORE PATH, not a path of its own. prod points the JSON
#: store at /data, the dev stack at pol-server/data and every test at a temp
#: directory; a database that did not follow would have quietly made a test
#: write to the real one. `FE_DB=` (empty) keeps the JSON store -- the state
#: every measurement before today ran against, and a real rollback rather than
#: a hope, because the JSON file is never touched again once the database is in
#: use.
_db_state = {}
_db_lock = threading.Lock()


def _say(msg):
    """print() that cannot take a login down.

    WARNING: THE CONTAINERS ARE UTF-8 AND THE WINDOWS HOST IS NOT. `print("KEY: ...")`
    raises UnicodeEncodeError under the host console's cp1252 codepage, and
    this file's house style puts WARNING: and KEY: in operator-facing lines. That is
    survivable in a log line nobody depends on; it is NOT survivable here,
    because use_db() sits on the character-screen path and an exception escaping
    it strands the player with no way back to the Viewer. Caught it live: the
    success print raised, the `except Exception` handler's own print raised
    inside the handler, and the whole thing came out of load_roster().
    """
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(msg.encode(enc, "replace").decode(enc, "replace"), flush=True)
    except Exception:                                        # pragma: no cover
        pass                            # a log line is never worth a login


def use_db(path):
    """The database path to use for JSON store `path`, or None to stay on JSON.

    Does the one-shot JSON import on the first call that finds an empty
    database. Never raises: a database fault must degrade to the JSON store,
    not break a login -- but it is LOGGED, because a store that quietly does
    not run is indistinguishable from one that ran and found nothing.
    """
    if not festore or not path:
        return None
    env = os.environ.get("FE_DB")
    if env is not None and not env.strip():
        return None                     # FE_DB= explicitly disables it
    db = env.strip() if env else os.path.join(
        os.path.dirname(os.path.abspath(path)), "fe.db")
    with _db_lock:
        if db in _db_state:
            return _db_state[db]
        _db_state[db] = None            # once, whatever happens below
        try:
            n_a, n_c = festore.import_json(path, db)
            _db_state[db] = db          # BEFORE the log line, not after it
            if n_c:
                _say("[felobby] KEY: PLAYER DATABASE: imported %d "
                     "character(s) for %d account(s) from %s into %s. The JSON "
                     "file is UNTOUCHED and is now the backup -- set FE_DB= "
                     "(empty) to fall back to it." % (n_c, n_a, path, db))
            else:
                _say("[felobby] player database %s: %d character(s) on file"
                     % (db, festore.count(db)))
        except Exception as e:                           # pragma: no cover
            _db_state[db] = None
            _say("[felobby] WARNING: player database %s unusable (%r) -- falling "
                 "back to the JSON store %s" % (db, e, path))
    return _db_state[db]


def _normalise(rows):
    """Tuples do not survive JSON, and they do not survive sqlite either.

    WARNING: ONE COPY, ON THE WAY OUT OF THE STORE, whichever store it was. feworld
    does `[tuple(x) for x in _load_char_field(...)]` at a dozen call sites
    precisely because this normalisation used to be JSON-specific; keeping it
    here means the database hands back records that are `==` to the JSON ones.
    """
    for vals in rows or []:
        vals["equip"] = [tuple(x) for x in vals.get("equip", [])]
        vals["items"] = [tuple(x) for x in vals.get("items", [])]
        vals["skills"] = list(vals.get("skills", []))
    return rows


def load_roster(path, account):
    """Characters saved for `account`, or None if there is no store yet.

    SQLITE SINCE 2026-09-08 (festore.py), JSON before it and still as the
    fallback. The old note here read "JSON, not SQLite, DELIBERATELY", on the
    grounds that `data/accounts.db` was truncated once by a container restart
    landing mid-commit. That hazard is real and it is why festore opens its own
    file in TRUNCATE journal mode rather than joining accounts.db -- but it was
    never an argument for hand-building atomicity in a whole-file rewrite that
    two containers share. See festore.update_roster.

    WARNING: ON THE DATABASE A FAULT RAISES festore.StoreUnavailable AND IS MEANT TO.
    Look at serve_lobby's caller: `if roster is None:` seeds a NEW player from
    --characters, so returning None for "the read failed" shows the player an
    empty character list and the create that follows REPLACES their real
    roster. `_serve_one` catches per connection and closes the socket, so the
    exception costs one failed login -- FE shows its own dialog and the player
    gets back to the Viewer -- and writes nothing. Do not "fix" this by
    catching it here.

    WARNING: Nor does a database fault fall back to the JSON file. Once the database
    is in use the JSON copy is a frozen backup, and serving a stale roster that
    the next save would then write back over is the same data loss taking
    longer. `FE_DB=` is the deliberate rollback; there is no automatic one.
    """
    db = use_db(path)
    if db:
        return _normalise(festore.load_roster(account, db))
    if not path or not os.path.exists(path):
        return None
    try:
        with io.open(path, encoding="utf-8") as f:
            blob = json.load(f)
    except (ValueError, OSError) as e:
        print("[felobby] WARNING: character store %s is unreadable (%s) -- ignoring it "
              "rather than overwriting; move it aside to start fresh"
              % (path, e), flush=True)
        return None
    rows = blob.get("accounts", {}).get(account)
    if rows is None:
        return None
    # Tuples do not survive JSON; the sub-lists come back as lists of lists.
    for vals in rows:
        vals["equip"] = [tuple(x) for x in vals.get("equip", [])]
        vals["items"] = [tuple(x) for x in vals.get("items", [])]
        vals["skills"] = list(vals.get("skills", []))
    return rows


def store_accounts(path):
    """Every account key in the store, with its character count. Used to report
    a roster still sitting under the pre-2026-08-24 shared account name."""
    db = use_db(path)
    if db:
        return festore.store_accounts(db)
    if not path or not os.path.exists(path):
        return {}
    try:
        with io.open(path, encoding="utf-8") as f:
            blob = json.load(f)
    except (ValueError, OSError):
        return {}
    return {k: len(v or []) for k, v in blob.get("accounts", {}).items()}


def next_charid(path, roster):
    """A character id free across the WHOLE store, not just this roster.

    `max(this roster) + 1` gave every account a charid 1, and charid is not a
    private number: feworld sends it as the unit login value (`--unit-login-value
    auto`) and spawns the player entity under it, so two players in one world
    would be two entities claiming the same id. Nothing shares a world yet --
    this is a footgun disarmed while the store is being rekeyed anyway, not a
    fix for an observed collision.
    """
    db = use_db(path)
    if db:
        return festore.next_charid(roster, db)
    top = max([c.get("charid", 0) for c in (roster or [])] + [0])
    if path and os.path.exists(path):
        try:
            with io.open(path, encoding="utf-8") as f:
                blob = json.load(f)
            for rows in blob.get("accounts", {}).values():
                for c in rows or []:
                    top = max(top, int(c.get("charid", 0) or 0))
        except (ValueError, OSError, TypeError):
            pass                # a bad store is already reported by load_roster
    return top + 1


#: Identity confidences that are NOT safe to WRITE a roster under. Reading a
#: wrong roster shows an empty character list and is recoverable; writing one
#: puts a character in another member's store where nothing afterwards can tell
#: it from theirs. feident.identify() classifies every resolution -- see its
#: PREFER_REMEMBERED block for the 2026-09-04 session this came from.
UNSAFE_TO_WRITE = ("contested", "ambiguous", "address")


def warn_if_unsafe_write(ident, what):
    """Say so, loudly, before a roster write under an identity we guessed."""
    conf = (ident or {}).get("confidence")
    if conf not in UNSAFE_TO_WRITE:
        return False
    print("[felobby] !! %s UNDER A %s IDENTITY (%s). %s"
          % (what, conf.upper(), ident.get("key"), ident.get("detail")),
          flush=True)
    print("[felobby]    If this is the wrong player the character lands in "
          "that member's roster and cannot be told apart from theirs "
          "afterwards. Sign the other POL account(s) out and reconnect.",
          flush=True)
    return True


def save_roster(path, account, roster):
    """Write the store back ATOMICALLY -- temp file in the same directory, then
    os.replace. A half-written roster read as a corrupt store on the next launch
    would lose every character, and a container restart can land anywhere.

    On the database that atomicity is sqlite's: one DELETE + INSERT per account
    in one transaction. The temp-file dance below is what it replaces."""
    db = use_db(path)
    if db:
        if festore.save_roster(account, roster, db):
            print("[felobby]    saved %d character(s) for %r -> %s"
                  % (len(roster or []), account, db), flush=True)
        return
    if not path:
        return
    blob = {"accounts": {}}
    if os.path.exists(path):
        try:
            with io.open(path, encoding="utf-8") as f:
                blob = json.load(f)
        except (ValueError, OSError):
            blob = {"accounts": {}}
    blob.setdefault("accounts", {})[account] = roster
    # PER-PROCESS temp name -- felobby and feworld both write this file from
    # separate containers, and a shared `<path>.tmp` let one os.replace() the
    # other's half-written scratch file into place. See roster_lock().
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        # Keep the previous generation. These are PLAYER-CREATED characters, and
        # the store has already been lost once -- not to a crash, but to a
        # maintainer deleting it deliberately during unrelated work. A .bak costs
        # nothing and makes that recoverable without going back through logs.
        if os.path.exists(path):
            try:
                with io.open(path, encoding="utf-8") as f_in, \
                        io.open(path + ".bak", "w", encoding="utf-8") as f_out:
                    f_out.write(f_in.read())
            except OSError:
                pass
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(blob, f, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as e:
        print("[felobby] WARNING: could not save the character store: %s" % e, flush=True)
        return
    print("[felobby]    saved %d character(s) for %r -> %s"
          % (len(roster), account, path), flush=True)


def parse_characters(tokens, probe=False):
    """`Name[:charid[:key=value ...]]`, one token per character.

    Any key in CHAR_FIELDS is settable, plus `equip=`, `skills=` and `items=`
    as counts. Everything unset takes char_defaults().
    """
    keys = {f[1] for f in CHAR_FIELDS}
    kinds = {f[1]: f[0] for f in CHAR_FIELDS}
    out = []
    for tok in tokens:
        parts = tok.split(":")
        vals = char_defaults(probe)
        if parts and parts[0]:
            vals["name"] = parts[0]
        if len(parts) > 1 and parts[1]:
            vals["charid"] = int(parts[1], 0)
        for kv in parts[2:]:
            if "=" not in kv:
                raise SystemExit("--characters field %r is not key=value" % kv)
            k, v = kv.split("=", 1)
            if k in ("equip", "skills", "items"):
                n = int(v, 0)
                if k == "equip":
                    vals[k] = [(i + 1, 0x11110000 + i) for i in range(n)]
                elif k == "skills":
                    vals[k] = [0x2200 + i for i in range(n)]
                else:
                    vals[k] = [(0x12345678 + i, 0x1234 + i, 1) for i in range(n)]
            elif k in keys:
                kind = kinds[k]
                vals[k] = v if kind == "cstr" else (
                    float(v) if kind == "f32" else int(v, 0))
            else:
                raise SystemExit("--characters: unknown field %r (known: %s)"
                                 % (k, ", ".join(sorted(keys))))
        if len(vals["name"].encode("cp932", "replace")) > 32:
            raise SystemExit("character name %r exceeds the client's 33-byte "
                             "buffer at +0x01" % vals["name"])
        out.append(vals)
    return out


def do_handshake(conn, args, ident):
    """Play the server side of phases 1-3 and report whether our key was right.

    `ident` is who feident.py says is on the far end -- carried through rather
    than looked up in serve_lobby so the resolution happens ONCE, at accept, and
    is logged next to the CONNECT line whether or not the handshake gets that
    far."""
    m, P, S = _load_crypto()
    mode, order = args.master_mode.split("/")
    be = order == "be"
    master = m.Blowfish(args.master_key.encode(), P, S)

    fr = recv_frame(conn)
    if not fr:
        print("[felobby] no frame -- client closed", flush=True)
        return False
    mid, body = fr
    print("[felobby] <- id=0x%04X  %d bytes" % (mid, len(body)), flush=True)
    print(hexdump(body), flush=True)
    if mid != 0x34:
        print("[felobby] expected phase-1 id 0x34, got 0x%04X -- not the handshake"
              % mid, flush=True)
        return False

    envelope = bf_decrypt(master, body, mode, be)
    print("\n[felobby] phase 1 deciphered under master key %r (%s):"
          % (args.master_key, args.master_mode), flush=True)
    print(hexdump(envelope), flush=True)

    # ENVELOPE, settled by diffing two live captures: bytes 2-3 and 20-23 are
    # identical across sessions while everything else is random.
    #   [u16 nonce][u16 keylen=0x0010][keylen bytes of KEY][u32 0x00000001]
    # keylen 16 is the same 0x10 handed to the cipher ctor at 0x52324d0.
    if len(envelope) < 6:
        print("[felobby] envelope too short", flush=True)
        return False
    nonce, keylen = struct.unpack(">HH", envelope[:4])
    tail = envelope[4 + keylen:4 + keylen + 4]
    ok_shape = keylen == 0x10 and tail == b"\0\0\0\1"
    print("[felobby]   nonce=0x%04X keylen=%d tail=%s  -> envelope %s"
          % (nonce, keylen, tail.hex(), "VALID" if ok_shape else "UNEXPECTED"),
          flush=True)
    if not ok_shape:
        print("[felobby]   keylen should be 16 and tail 00000001. A wrong "
              "--master-mode looks exactly like this; the shape IS the check, so "
              "there is no need to guess from FE's log.", flush=True)
        return False
    session_key = envelope[4:4 + keylen]
    print("[felobby]   SESSION KEY = %s" % session_key.hex(), flush=True)

    session = m.Blowfish(session_key, P, S)

    # WHICH KEY DOES PHASE 2 USE? The MASTER one. The pipeline object keeps two
    # ciphers and they are NOT interchangeable:
    #   objA = [pipeline+4]  keyed with the MASTER key at construction (0x05232559)
    #   objB = [pipeline+8]  keyed with the SESSION key by phase 1 (0x05232a21)
    # decipher (pipeline vtable+0xc, 0x052355c0) delegates to [this+4] = objA, so
    # our 0x35 body must be enciphered under `fantasyearth`. Enciphering it under
    # the session key produces exactly the silent livelock described above -- the
    # frame arrives, the id matches, decipher fails, the handler rewinds and
    # retries for ever.
    # PHASE 2 MUST ECHO THE CLIENT'S OWN KEY BACK.
    # exchange_key_phase3 (0x052355c0) does not merely parse our body: after
    # checking the length agrees with its own key length ("disagree with encipher
    # key length") it runs a BYTE-BY-BYTE COMPARE at 0x0523573a against the key it
    # generated. So the payload is not a free challenge -- returning the session
    # key is exactly how we prove we decrypted phase 1.
    phase2 = master if args.phase2_key == "master" else session
    server_key = bytes.fromhex(args.server_key)
    body = wrap2(session_key, server_key, args.seq)
    enc = bf_encrypt(phase2, body, mode, be)
    send_frame(conn, 0x35, enc)
    print("\n[felobby] -> id=0x35: client's key echoed + OUR %d-byte server key, "
          "enciphered under the %s key" % (len(server_key), args.phase2_key),
          flush=True)
    print("[felobby]    structure: %s" % body.hex(), flush=True)
    print("[felobby]    [cks=0x%04X][keylen=%d][client key][len2=%d][server key]"
          "[seq=%d]" % (struct.unpack(">H", body[:2])[0], len(session_key),
                        len(server_key), args.seq), flush=True)
    print("[felobby]    server key = %s" % server_key.hex(), flush=True)
    if not server_key:
        print("[felobby]    !! empty server key WILL crash the client (idiv by "
              "zero at 0x05233c8d)", flush=True)
    # Outbound traffic is enciphered under OUR key (objC); the client's own
    # messages keep using theirs (objB).
    outbound = m.Blowfish(server_key, P, S) if server_key else session
    # PHASE 3 ECHOES *OUR* SERVER KEY BACK, not the client's own key:
    # the client installs what we sent as its outbound cipher and returns
    # it, wrapped in the PHASE-1 structure and enciphered under the MASTER
    # key. Comparing against session_key here reported a false failure on a
    # run that had actually completed the whole exchange.
    challenge = server_key

    conn.settimeout(args.read_window)
    try:
        fr = recv_frame(conn)
    except (OSError, socket.timeout):
        fr = None
    if not fr:
        # Do NOT read this as "wrong session key" -- the envelope already proved
        # the key. Silence here means our 0x35 BODY failed decipher validation:
        # the client rewinds (0x522f180) and retries the same message for ever,
        # so it spams `Exchanging Cipher key phase03...` with no failure line.
        # Suspect, in order: the wrong one of master/session (--phase2-key), the
        # envelope shape, or a length the decipher rejects.
        print("\n[felobby] NO phase-3 reply -- our 0x35 body failed the client's "
              "decipher validation (0x052355c0). The session key is NOT in doubt; "
              "the envelope already validated it. Check --phase2-key and the "
              "envelope shape. FE will be spinning on 'phase03...' with no error "
              "line -- that silent poll IS the signature.", flush=True)
        return False
    mid3, body3 = fr
    print("\n[felobby] <- id=0x%04X  %d bytes" % (mid3, len(body3)), flush=True)
    print(hexdump(body3), flush=True)
    if mid3 != 0x36:
        print("[felobby] expected 0x36; got 0x%04X" % mid3, flush=True)
        return False

    # Phase 3 writes [container+0x18] -- the DECIPHERED buffer -- through the raw
    # path, so it is unclear without measuring whether it goes out in the clear or
    # re-enciphered, and under which key. Try all three rather than assume; the one
    # that reproduces our challenge is the answer, and it is worth knowing.
    # The echo PROVES the handshake: the client installed our server key and
    # returned it. Match on the key appearing in the plaintext rather than on a
    # full structural validate -- phase 3's checksum region is not phase 1's, and
    # gating on that detail reported a false failure on a run that had in fact
    # completed the entire exchange. We never have to BUILD a phase-3 frame, so
    # the exact convention there is not worth blocking on.
    echo, how = None, None
    for label, cand in (("raw (not enciphered)", body3),
                        ("client session key", bf_decrypt(session, body3, mode, be)),
                        ("master key", bf_decrypt(master, body3, mode, be)),
                        ("our server key", bf_decrypt(outbound, body3, mode, be))):
        print("\n[felobby] phase 3 as %s:" % label, flush=True)
        print(hexdump(cand), flush=True)
        if challenge and challenge in cand:
            echo, how = cand, label
            break
    if echo is None:
        print("\n[felobby] the client ANSWERED phase 3 (so our 0x35 was accepted -- "
              "real progress) but none of raw/session/master reproduces the "
              "challenge. The echo is framed some other way; the bytes above are "
              "the evidence for working out how.", flush=True)
        return False

    print("\n[felobby] *** MATCH via %s -- the echo equals our challenge. The "
          "handshake is COMPLETE and the lobby cipher is BROKEN end to end. ***"
          % how, flush=True)

    serve_lobby(conn, args, session, outbound, mode, be, ident)
    return True


def serve_lobby(conn, args, session, outbound, mode, be, ident):
    """Speak the post-handshake protocol.

    Every message now rides an OUTER envelope (see 0x5232cd2):
        [u16 len][u16 0x30][ encipher( [u16 real_id][fields...] ) ]
    so the real id is INSIDE the ciphertext -- 0x7000 never appears on the wire.
    """
    print("\n[felobby] --- entering post-handshake protocol (outer id 0x30) ---",
          flush=True)
    # The character list is STATE, not a constant. Within a session the select
    # screen creates (0xC002) and deletes (0xC003) and then re-requests the list,
    # so a fresh parse per 0xC001 would undo whatever the player just did; and
    # ACROSS sessions it is loaded from and saved to --char-store, because a
    # character that evaporates when the socket closes is not a character.
    # --characters only SEEDS an account that has no store yet.
    # WARNING: OUR OUTBOUND SEQUENCE IS OUR OWN COUNTER, NOT AN ECHO OF THE CLIENT'S.
    # This is what broke the world door on its first live reply, and the failure
    # is silent from here: the client validates the sequence in the enveloped
    # body (exchange_key_phase3's error strings include `illegal(sequence)`, and
    # it keeps a decremented copy at [obj+0x20]). Our 0x302B answer carried
    # seq=2, because 0x20 MSG_AUTH_CODE_NOTIFY arrived first and we did not
    # answer it -- so our first outbound frame claimed to be our second. The
    # client received the frame, never dispatched it (no `> MSG_SERVER_UNIT_
    # LOGIN_OK` and no NG either), and dropped the connection with
    # `>Disconnected. err=0`.
    #
    # The lobby door had been echoing too and got away with it ONLY because it
    # answers essentially every enveloped message, so the echoed number happened
    # to equal its own count. That is a coincidence, not a design, and it would
    # have broken the first time an unanswered message arrived mid-session.
    out_seq = [0]
    last_in = [0]

    def next_seq():
        """The sequence to stamp on the NEXT outbound message.

        WARNING: WHICH RULE THE CLIENT WANTS IS NOT ESTABLISHED, so this is a knob
        and the default is the one that has actually carried a player into
        the world. `echo` answers with the sequence of the message being
        answered; `count` numbers our own frames from 1.

        They are identical whenever we answer every enveloped message in
        order, which the lobby normally does -- which is exactly why the
        switch to `count` was argued to be behaviour-neutral here. The very
        next live run stopped dialling the world. That may or may not be
        this change, but nothing else on the server side moved, so blaming
        the shim build before re-testing my own edit was the wrong order.
        """
        out_seq[0] += 1
        return last_in[0] if args.seq_mode == "echo" else out_seq[0]

    # WARNING: THE STORE KEY IS THE RESOLVED POL MEMBER, NOT --account. Before
    # 2026-08-24 this was args.account, felobby was launched without it, and so
    # every session on the server read and wrote the ONE roster under
    # "TestPlayer": every FE player saw, edited and deleted everybody else's
    # characters. feident.identify() answers "member:<id>" from the POL session
    # row for this address, or "addr:<ip>" when it cannot -- never a shared
    # bucket. See feident.py for what that costs (two accounts behind one
    # address still collide) and what it needs (host networking).
    acct = ident["key"]
    roster = None if args.char_reset else load_roster(args.char_store, acct)
    if roster is None:
        roster = parse_characters(args.characters, args.char_probe)
        print("[felobby] no stored characters for %r -- seeding %d from "
              "--characters" % (acct, len(roster)), flush=True)
        legacy = store_accounts(args.char_store).get(feident.LEGACY_ACCOUNT)
        if legacy:
            print("[felobby] WARNING: the store still holds %d character(s) under the "
                  "old shared account %r. They are NOT adopted here -- giving "
                  "them to whoever logs in first is the same cross-account "
                  "bleed in a nicer costume. Assign them deliberately:\n"
                  "[felobby]      python tools/fe_roster_rekey.py --to %s"
                  % (legacy, feident.LEGACY_ACCOUNT, acct), flush=True)
    else:
        print("[felobby] loaded %d character(s) for %r from %s"
              % (len(roster), acct, args.char_store), flush=True)
    conn.settimeout(args.read_window)
    while True:
        try:
            fr = recv_frame(conn)
        except (OSError, socket.timeout):
            print("[felobby] no further traffic", flush=True)
            return
        if not fr:
            print("[felobby] client closed", flush=True)
            return
        outer, body = fr
        if outer != 0x30:
            print("[felobby] <- OUTER id=0x%04X (expected 0x30) %d bytes"
                  % (outer, len(body)), flush=True)
            print(hexdump(body), flush=True)
            continue
        plain = bf_decrypt(session, body, mode, be)
        env = traffic_unwrap(plain)
        if not env:
            print("[felobby] <- 0x30 whose envelope did not validate:", flush=True)
            print(hexdump(plain), flush=True)
            continue
        seq, inner = env
        last_in[0] = seq
        if len(inner) < 2:
            print("[felobby] <- 0x30 with a short inner message", flush=True)
            continue
        (real_id,) = struct.unpack(">H", inner[:2])
        print("\n[felobby] <- 0x30 wrapping inner id=0x%04X, %d bytes of fields "
              "[client seq=%d -> our reply carries %d, --seq-mode %s]"
              % (real_id, len(inner) - 2, seq,
                 seq if args.seq_mode == "echo" else out_seq[0] + 1,
                 args.seq_mode), flush=True)
        print(hexdump(inner), flush=True)

        if real_id == 0x7000:
            # MSG_LOBBY_LOGIN_REQ = beginMessage(0x7000) + writeDword(0x4B).
            # Its OK reply (0x7001) carries NO fields at all: the handler at
            # 0x5046f2e only logs and clears state.
            ver = struct.unpack(">I", inner[2:6])[0] if len(inner) >= 6 else None
            print("[felobby]    MSG_LOBBY_LOGIN_REQ, version dword = %s"
                  % (("0x%X (%d)" % (ver, ver)) if ver is not None else "?"),
                  flush=True)
            reply = bf_encrypt(outbound,
                               traffic_wrap(inner_msg(0x7001), next_seq()),
                               mode, be)
            send_frame(conn, 0x30, reply)
            print("[felobby] -> 0x30 wrapping inner 0x7001 MSG_LOBBY_LOGIN_OK "
                  "(no fields)", flush=True)
            print("[felobby]    watch FE's log for '>MSG_LOBBY_LOGIN_OK'", flush=True)
        elif real_id == 0xC007:
            # AUTHENTICATION. Built at 0x05047dd0: beginMessage(0xC007) then
            # write_bytes(blob, 0x34) -- a 52-byte credential blob fetched from
            # [0x52fe920]->vtable[0xea0], i.e. the PlayOnline session's own ticket.
            # The client pre-registers exactly two acceptable replies at 0x05047db4:
            #   0xC010 MSG_CERTIFICATION_OK, carrying a NUL-terminated account name
            #   0xC011 the rejection (reads an error dword -> code 0xC011xxxx)
            # The string reader (0x5045f50 -> 0x522f5e0) copies bytes until NUL, so
            # the account goes on the wire as plain NUL-terminated text.
            #
            # WARNING: THE BLOB MAY BE THE IDENTITY WE KEEP WORKING AROUND. This
            # comment used to assert it was a constant and therefore useless.
            # That was wrong: it applied fmo.py's "identical across two
            # machines" measurement, which is about a 16-byte field at +0x38,
            # to the 52-byte blob at +0x00 -- which the same file records as
            # VARYING PER SESSION. FE sends the second one.
            #
            # So it is now RECORDED next to the resolved identity, and still
            # not keyed on: four captures from one machine cannot tell
            # "identifies the account" from "identifies the install", and those
            # want opposite fixes. `tools/fe_blob_verdict.py` decides it the
            # moment two different POL members have each presented a blob.
            print("[felobby]    MSG_CERTIFICATION request, %d-byte credential blob"
                  % (len(inner) - 2), flush=True)
            print("[felobby]    blob: %s" % inner[2:].hex(), flush=True)
            print("[felobby]    identity: %s -- %s"
                  % (ident["key"], ident["detail"]), flush=True)
            print("[felobby]    %s" % feident.record_blob(ident, inner[2:]),
                  flush=True)
            # The name we send is the name the client keeps and hands to the
            # world door in 0x400F, so in the default `member` mode it carries
            # the store key across to feworld by itself. The handoff file below
            # is the belt to that echo's braces -- see feident.py.
            wire = ident.get("wire") or feident.wire_account(ident)
            body = inner_msg(0xC010, wire.encode("cp932", "replace") + b"\0")
            send_frame(conn, 0x30,
                       bf_encrypt(outbound, traffic_wrap(body, next_seq()), mode, be))
            print("[felobby] -> 0x30 inner 0xC010 MSG_CERTIFICATION_OK account=%r"
                  % wire, flush=True)
            print("[felobby]    watch FE's log for '>MSG_CERTIFICATION_OK account='",
                  flush=True)
            if args.session_store != "":
                feident.remember(ident["ip"], ident, wire,
                                 path=args.session_store or None)
        elif real_id == 0xC006:
            # Built at 0x05047e73 with NO fields, registering 0xC00D / 0xC00E as
            # its two acceptable replies (0x05047e5e). 0xC00D is
            # MSG_LOGIN_PROCEDURE_OK and carries nothing; 0xC00E reads a dword and
            # branches on 0x19 / 0x1b before reading a string, so it is the
            # error/redirect arm.
            print("[felobby]    login-procedure request (no fields)", flush=True)
            reply = bf_encrypt(outbound,
                               traffic_wrap(inner_msg(0xC00D), next_seq()),
                               mode, be)
            send_frame(conn, 0x30, reply)
            print("[felobby] -> 0x30 inner 0xC00D MSG_LOGIN_PROCEDURE_OK (no fields)",
                  flush=True)
        elif real_id == 0xC005:
            # MSG_CL_REGISTER_BROADCAST_GAME_UNIT_INFO_REQ (its own log name,
            # 0x52d5128). "Getting game info......." (0x52d5158) is pushed to the
            # screen immediately before it, at 0x05047f79 = lobby state 9, which
            # also sets [ebp+0x938] = 2 and advances to state 0xa.
            #
            # WARNING: THE REPLY IS 0xD00C, **NOT** 0x7831. State 0xa spins until
            # [ebp+0x938] goes to 0, and the ONLY handler that clears it is
            # 0xD00C MSG_LC_GAME_UNIT_INFO_NOTIFY (0x05047404 -> 0x0504741b).
            # 0x7831 MSG_LOBBY_GET_GAME_INFO_OK is a real case in the same switch
            # and logs "Num of Games", so it LOOKS accepted -- but it leaves
            # [0x938] at 2 and instead calls 0x5046d10, which fires a bare
            # MSG_LOBBY_JOIN_GAME_REQ (0x7820) from OUTSIDE the state machine.
            # That is the trap that stranded us: the JOIN exchange completes and
            # FE prints the right world address, while the lobby routine is still
            # parked in state 0xa and so never returns 0 to its caller -- and only
            # a 0 there advances the LOGIN sequence (0x5047960) to its state 4,
            # the relay connect at 0x50483b0 that actually dials the world.
            #
            # 0xD00C body, from the reader at 0x05047417-0x05047555:
            #     [u16 count]  then count fixed 44-byte records, each read as
            #     [u8 UnitID][u16 GameID][u32 UserNum][cstr WorldName]
            # (read_u8 0x5045dc0, read_u16 0x5045e30, read_u32 0x5045e60,
            # read_str 0x5045f50; the record is malloc'd count*0x2c and the string
            # lands at +8, so WorldName has 36 bytes including its NUL).
            # It is logged as
            #   ">            NEW! %d(%d) UnitID=%d GameID=%d UserNum=%d
            #    WorldName=%s"
            # WARNING: UnitID is the key the rest of the client indexes by: the world
            # picker resolves a name through 0x50fbbd0 and lobby state 0x10 looks
            # the chosen unit up with 0x5048880, which returns NULL on a miss and
            # is then dereferenced unchecked. Offer every UnitID the client may
            # have stored, or it crashes at the JOIN step.
            units = parse_worlds(args.worlds)
            print("[felobby]    game-unit info request; offering %d unit(s):"
                  % len(units), flush=True)
            for unit_id, game_id, users, name in units:
                print("[felobby]      UnitID=%d GameID=%d UserNum=%d WorldName=%r"
                      % (unit_id, game_id, users, name), flush=True)
            payload = struct.pack(">H", len(units))
            for unit_id, game_id, users, name in units:
                payload += (struct.pack(">BHI", unit_id, game_id, users)
                            + cp932(name, 35) + b"\0")
            send_frame(conn, 0x30,
                       bf_encrypt(outbound,
                                  traffic_wrap(inner_msg(0xD00C, payload), next_seq()),
                                  mode, be))
            print("[felobby] -> 0x30 inner 0xD00C MSG_LC_GAME_UNIT_INFO_NOTIFY",
                  flush=True)
            print("[felobby]    expect FE to log '>MSG_LC_GAME_UNIT_INFO_NOTIFY' "
                  "then 'NumGame = %d' and one NEW! line per unit, then send "
                  "0xC009" % len(units), flush=True)
        elif real_id == 0xC009:
            # Lobby state 0xd (0x05047fdf): sent with NO fields, [ebp+0x968] = 2,
            # then state 0xe (0x0504800c) spins on that word. Two replies clear it:
            #
            #   0xD015 (0x050473a1)  [0x968] = 1 -- nothing to show. State 0xe
            #                        jumps straight to state 0xb.
            #   0xD014 (0x050473b0)  [dword noticeId][cstr title][cstr body].
            #                        The id is compared against the global at
            #                        0x52d49ac (initialised 0xFFFFFFFF): EQUAL
            #                        means "already seen" and falls into the same
            #                        [0x968] = 1 path; different stores it and sets
            #                        [0x968] = 0, which makes state 0xe build a
            #                        0x4c0-byte scrolling window from the two
            #                        strings ([ebp+0x30] and [ebp+0x130]) and wait
            #                        in state 0xf for the reader to close it.
            #
            # So this is the login NOTICE / message-of-the-day, and 0xD015 is the
            # "no notice" answer. Either way the machine lands in state 0xb, which
            # sets [0x95c] = 1 / [0x95d] = 0 and hands over to the WORLD PICKER;
            # state 0xc then waits for the UI to set [0x95d] (0x050f8c1a) -- i.e.
            # for the player to choose a world -- before state 0x10 sends 0x7820.
            if args.notice:
                title, _, text = args.notice.partition("|")
                payload = (struct.pack(">I", args.notice_id)
                           + cp932(title) + b"\0"
                           + cp932(text) + b"\0")
                send_frame(conn, 0x30,
                           bf_encrypt(outbound,
                                      traffic_wrap(inner_msg(0xD014, payload), next_seq()),
                                      mode, be))
                print("[felobby] -> 0x30 inner 0xD014 notice id=0x%08X title=%r"
                      % (args.notice_id, title), flush=True)
                print("[felobby]    FE shows a window; it advances only when the "
                      "player closes it", flush=True)
            else:
                send_frame(conn, 0x30,
                           bf_encrypt(outbound,
                                      traffic_wrap(inner_msg(0xD015), next_seq()),
                                      mode, be))
                print("[felobby] -> 0x30 inner 0xD015 (no notice)", flush=True)
            print("[felobby]    next is the WORLD PICKER -- FE waits for the "
                  "player to choose before it sends MSG_LOBBY_JOIN_GAME_REQ "
                  "(0x7820)", flush=True)
        elif real_id == 0x7820:
            # MSG_LOBBY_JOIN_GAME_REQ -- the LAST lobby step. Its OK reply hands
            # the client off to the WORLD server, exactly as the balancer handed
            # it to us. Handler 0x05046e48 reads, in order:
            #     read_u32 -> [ebp+0x9cc]   IP
            #     read_u16 -> [ebp+0x9d0]   PORT
            #     read_u32 -> [ebp+0x9d4]   CODE
            # and prints the address LSB-FIRST, so the octets go on the wire
            # REVERSED -- the same wart as the LLB reply (see fellb.py).
            # 0x7822 is the NG arm (">MSG_LOBBY_JOIN_GAME_NG (errID=%d)").
            #
            # Its real job is the two stores at the end of that handler:
            # [ebp+0x9bc] = 1 and **[ebp+0x93c] = 0** (0x05046eb6/0x05046ebd).
            # The zero is what releases lobby state 0x11 (0x0504831e), the only
            # path that returns 0 from the lobby routine -- and only that 0 lets
            # the LOGIN sequence at 0x5047960 step to its state 4, the relay
            # connect at 0x50483b0, which is what finally dials the stored
            # IP/port/code. Reaching this message by the 0x7831 shortcut instead
            # gets the reply parsed and logged but leaves the machine parked, so
            # FE sits on the lobby socket polling 0x0002 for ever.
            print("[felobby]    JOIN_GAME request, selection=%s"
                  % inner[2:].hex(), flush=True)
            octets = socket.inet_aton(args.world_ip)
            body = inner_msg(0x7821, octets[::-1]
                             + struct.pack(">H", args.world_port)
                             + struct.pack(">I", args.world_code))
            send_frame(conn, 0x30,
                       bf_encrypt(outbound, traffic_wrap(body, next_seq()), mode, be))
            print("[felobby] -> 0x30 inner 0x7821 MSG_LOBBY_JOIN_GAME_OK "
                  "world=%s:%d code=%d" % (args.world_ip, args.world_port,
                                           args.world_code), flush=True)
            print("[felobby]    expect FE to log 'IP(%s):Port(%d)' -- any other "
                  "address means the octet order regressed"
                  % (args.world_ip, args.world_port), flush=True)
        elif real_id == 0xC001:
            # THE CHARACTER LIST REQUEST, and the gate the world picker actually
            # waits behind. Sent from the character-select screen at 0x050f8214
            # with no fields; the registration right before it (0x050f81f5) puts
            # 0xD002 in BOTH acceptable-reply slots, so 0xD002 is the only answer
            # the client will take.
            #
            # 0xD002's handler is 0x0502500d, in the character sub-dispatcher
            # 0x5023ad0 rather than the lobby switch -- which is why it never
            # showed up while we were reading 0x05046e09. It reads
            #     [u32 count][u16 fmt]   then `count` character records
            # and finishes by setting the screen's result byte [0x53378a9]: 1 =
            # accepted (UI state 3 -> 4), 0 = the failure arm, which pops a window
            # and parks the screen in state 0x1e.
            #
            # WARNING: count == 0 SHORT-CIRCUITS THE WHOLE PARSE (`test eax,eax / jbe` at
            # 0x05025050 jumps straight to the [0x53378a9] = 1 store at
            # 0x050256e9), so an empty list is a complete, valid answer. That is
            # what we send: the per-record layout is long and branchy -- ~30
            # reads mixing u8/u16/u32/cstr under conditionals -- and is NOT mapped
            # yet. `fmt` is re-read at the top of every record iteration, so it
            # very likely selects which fields a record carries; with count 0 it
            # is unused.
            print("[felobby]    character-list request (no fields)", flush=True)
            chars = roster
            # WARNING: THE STAT TRIPLE w72/w74/w76 MUST NOT BE ALL ZERO. Record
            # +0x72/+0x74/+0x76 are copied onto the field unit (0x4fe75a0 /
            # 0x4fe79a0 -> unit +0x49a/+0x49c/+0x49e, overwriting the ctor's
            # own default of 10 at 0x507a36a), and the in-field HP gauge
            # (0x50ccf70) computes 123*cur/([+0x49a]+[+0x49c]) -- an idiv at
            # 0x50cd049 whose divisor is w72+w74. Stored characters created
            # before 2026-08-23 persist zeros there, which crashed FE with
            # c0000094 at RVA 0x13D049 the instant the first-ever field load
            # finished (right after 0x100E released the loading screen).
            # Backfill at SERVE time so old stores heal without data surgery.
            # The PW gauge (0x50ce000 family) is the HP gauge's twin: max =
            # [unit+0x4a0]+[unit+0x4a2] = record w78+w7a, cur = +0x4a4 = w7c,
            # idiv at 0x50ce184. Confirmed live 2026-08-23: with only HP
            # backfilled, the SAME pick crashed c0000094 at RVA 0x13E184 -- the
            # PW divisor. A whole-image idiv sweep (0x50c0000..0x51d0000) says
            # these two widgets are the ONLY unguarded stat divides (0x50ced59
            # and 0x50cc175 test their divisor first; the rest divide by
            # constants or loop counts), so this closes the family.
            # KEY: 2026-09-11: HP IS FLAT (2006: 1000 at every level), so the
            # backfill is now an ENFORCE, not a fill-if-zero. Characters that
            # were listed under the old --char-hp 200 had 200 PERSISTED into
            # w72/w76 (the gear-seeding save below writes the backfilled
            # record), and "fill only when zero" would have served them 200
            # for ever -- present-but-wrong. Any max that is not --char-hp is
            # rewritten here and the roster is saved, so the store heals too.
            healed = False
            for vals in chars:
                w72, w74, w76 = (int(vals.get(k) or 0)
                                 for k in ("w72", "w74", "w76"))
                if args.char_hp and w72 + w74 != args.char_hp:
                    vals["w72"], vals["w74"] = args.char_hp, 0
                    vals["w76"] = args.char_hp
                    healed = True
                    print("[felobby]    charid=%s: HP triple %d/%d/%d -> "
                          "w72=w76=%d (HP max/cur, flat; gauge divisor "
                          "0x50cd049)%s"
                          % (vals.get("charid"), w72, w74, w76, args.char_hp,
                             " -- MIGRATED from a stored max" if w72 or w74
                             else ""), flush=True)
                elif args.char_hp and not w76:
                    vals["w76"] = args.char_hp
                    healed = True
                if args.char_pw and not (vals.get("w78") or vals.get("w7a")):
                    vals["w78"] = args.char_pw
                    if not vals.get("w7c"):
                        vals["w7c"] = args.char_pw
                    print("[felobby]    charid=%s: zero PW triple -- "
                          "backfilled w78=w7c=%d (PW max/cur; gauge divisor "
                          "0x50ce184)" % (vals.get("charid"), args.char_pw),
                          flush=True)
            # STARTING GEAR, from dat.pak's own fet_initialize_equip_item_data
            # (2026-09-05). A character with no items gets its class's list
            # -- class = the `look1` byte the client uses as [unit+0x3AB],
            # sex picks the male|female list -- as item INSTANCES in the
            # 0xD002 record ([u32 uid][u16 item][u8]) and, with `on`, an
            # equip pair ([u8 slot][u32 uid]) per item, the slot being the
            # item table's slot type (the [item+0x358] the validator tests).
            # KEY: This is the item channel the combat gate was waiting for
            # ("No weapon equipped; can't use skill" is enforced client-side
            # off exactly these records). Persisted, so it happens once.
            # WARNING: NOT LIVE-TESTED: the last time this record carried items they
            # were ABSURD ids and the client bailed to the Viewer; these are
            # the ids SE hands a new character. `--char-gear off` for an A/B.
            seeded = False
            for vals in chars:
                if args.char_gear == "off" or vals.get("items") or vals.get("gear_seeded"):
                    continue
                gear = fegamedata.starting_gear(vals.get("look1", 0) or 0,
                                                vals.get("sex", 0) or 0)
                if not gear:
                    print("[felobby]    charid=%s: no starting gear for class "
                          "%s -- fet_initialize_equip has no row" %
                          (vals.get("charid"), vals.get("look1")), flush=True)
                    continue
                items, equip = [], []
                for i, no in enumerate(gear):
                    # WARNING: Classes 3..5's lists name items 1698..1715 that this
                    # build's FE_ITEM_DATA does not have. An id the client
                    # cannot resolve is the absurd-id bail of 08-17; skip it.
                    if no not in fegamedata.items():
                        print("[felobby]    charid=%s: starting item %d is not "
                              "in FE_ITEM_DATA -- skipped" % (vals.get("charid"), no),
                              flush=True)
                        continue
                    uid = int(vals.get("charid", 0) or 0) * 1000 + i + 1
                    items.append((uid, no, 1))
                    slot = fegamedata.items()[no].get("slot", -1)
                    if args.char_gear == "on" and slot >= 0:
                        equip.append((slot, uid))
                if not items:
                    continue
                vals["items"], vals["equip"], vals["gear_seeded"] = items, equip, True
                seeded = True
                print("[felobby]    charid=%s class=%s sex=%s: seeded starting "
                      "gear %s (%d equipped) from fet_initialize_equip"
                      % (vals.get("charid"), vals.get("look1"), vals.get("sex"),
                         [(no, fegamedata.items().get(no, {}).get("slot"))
                          for _u, no, _f in items], len(equip)), flush=True)
            if seeded or healed:
                with roster_lock(args.char_store):
                    save_roster(args.char_store, acct, roster)
            slots = args.char_slots if args.char_slots is not None else len(chars)
            if slots < len(chars):
                slots = len(chars)
            payload = struct.pack(">IH", slots, len(chars))
            for vals in chars:
                payload += build_char_record(vals)
            send_frame(conn, 0x30,
                       bf_encrypt(outbound,
                                  traffic_wrap(inner_msg(0xD002, payload), next_seq()),
                                  mode, be))
            print("[felobby] -> 0x30 inner 0xD002 character list, %d slot(s), "
                  "%d filled, %d payload bytes"
                  % (slots, len(chars), len(payload)), flush=True)
            for vals in chars:
                print("[felobby]      charid=%d name=%r equip=%d skills=%d "
                      "items=%d" % (vals["charid"], vals["name"],
                                    len(vals["equip"]), len(vals["skills"]),
                                    len(vals["items"])), flush=True)
            if chars:
                # The client narrates its own parse. These two lines are the
                # alignment check -- and the ONLY one, because a misaligned
                # record is still accepted on our side.
                print("[felobby]    WARNING: NOW READ THE CLIENT'S LOG, not this one. It "
                      "prints `EquipItem %03d` with the item count and then "
                      "`Item UID %8d,NO %4d` per item. Values that come back "
                      "shifted or wrong mean a width is wrong ABOVE them; the "
                      "server side looks like a clean success either way.",
                      flush=True)
            else:
                print("[felobby]    empty means 'no characters yet'; FE should "
                      "leave the wait state and move to character CREATION",
                      flush=True)
        elif real_id == 0xC002:
            # MSG_CL_ADD_CHARACTER_REQUEST -- character CREATION, built at
            # 0x050f8c8e. Its registration (0x050f8c77) names the only two
            # answers it will take: 0xD004 OK / 0xD005 NG.
            #
            #     [u8 unit][u8][cstr name][u8][u8][u32 0][u8][u8][u8]
            #
            # The u32 is a literal zero in the builder (`push 0` at 0x050f8ce9),
            # not a field the screen fills. The five loose bytes are the creation
            # choices -- sex/class/face/hair and so on -- read out of the edit
            # screen's object at +0x3aa, +0x3ab, +0x3b6, +0x3b8, +0x3b7; they are
            # NOT named yet, and the order on the wire is 3b6, 3b8, 3b7, which is
            # not their order in memory.
            f = inner[2:]
            look = {}
            try:
                unit, b1 = f[0], f[1]
                e = f.index(b"\0", 2)
                cname = f[2:e].decode("cp932", "replace")
                rest = f[e + 1:]
                # [u8 +0x3aa][u8 +0x3ab][u32 0][u8 +0x3b6][u8 +0x3b8][u8 +0x3b7]
                # -- note the last three are NOT in memory order on the wire, and
                # the u32 is a literal `push 0` in the builder, not a field.
                if len(rest) >= 9:
                    look = {"sex": rest[0], "look1": rest[1],
                            "look2": rest[6], "look4": rest[7],
                            "look3": rest[8]}
            except (IndexError, ValueError):
                unit, b1, cname, rest = 0, 0, "?", b""
            print("[felobby]    ADD CHARACTER unit=%d b1=%d name=%r rest=%s"
                  % (unit, b1, cname, rest.hex()), flush=True)
            # ONE lock from the id allocation to the save. The id used to be
            # picked under the lock, the lock dropped, and the save taken
            # under a second one -- so two select screens creating at the
            # same moment could both read `top + 1` before either wrote it,
            # and two accounts would own one charid (which feworld then uses
            # as the player's entity id).
            with roster_lock(args.char_store):
                charid = next_charid(args.char_store, roster)
                vals = char_defaults(args.char_probe)
                vals["name"] = cname
                vals["charid"] = charid
                vals["unit"] = unit
                # KEEP THE PLAYER'S FACE. Without this the record goes back
                # all zeros, the select screen rebuilds the default Male model
                # from it, and the character you just designed comes back a
                # stranger -- reported live 2026-08-17. The record->appearance
                # copy at 0x04fe7519 is what makes these five the right five.
                vals.update(look)
                if look:
                    print("[felobby]    keeping the creation choices: %s"
                          % " ".join("%s=%d" % kv
                                     for kv in sorted(look.items())),
                          flush=True)
                roster.append(vals)
                warn_if_unsafe_write(ident, 'CREATING A CHARACTER')
                save_roster(args.char_store, acct, roster)
            # WARNING: next_seq(), NOT the inbound `seq`. This reply and 0xD008 used
            # to stamp the client's own number straight back, which is
            # indistinguishable from next_seq() in `echo` mode -- and silently
            # wrong in `count` mode, where it also skipped our counter, so
            # every reply after it was one behind. Both fixed 2026-09-04.
            send_frame(conn, 0x30,
                       bf_encrypt(outbound,
                                  traffic_wrap(inner_msg(0xD004,
                                                         struct.pack(">I", charid)),
                                               next_seq()), mode, be))
            print("[felobby] -> 0x30 inner 0xD004 MSG_LC_ADD_CHARACTER_OK "
                  "CharaID=%d (roster now %d)" % (charid, len(roster)), flush=True)
        elif real_id == 0xC003:
            # MSG_CL_DELETE_CHARACTER_REQUEST (0x050f86b6), `[u8 unit][u32 charid]`
            # -- the client logs it as `unitID=%d`. Answers: 0xD006 OK (NO FIELDS)
            # / 0xD007 NG [u32 err].
            f = inner[2:]
            unit = f[0] if f else 0
            charid = struct.unpack_from(">I", f, 1)[0] if len(f) >= 5 else 0
            print("[felobby]    DELETE CHARACTER unit=%d charid=%d"
                  % (unit, charid), flush=True)
            roster[:] = [c for c in roster if c["charid"] != charid]
            warn_if_unsafe_write(ident, 'DELETING A CHARACTER')
            with roster_lock(args.char_store):
                save_roster(args.char_store, acct, roster)
            send_frame(conn, 0x30,
                       bf_encrypt(outbound,
                                  traffic_wrap(inner_msg(0xD006), next_seq()), mode, be))
            print("[felobby] -> 0x30 inner 0xD006 MSG_LC_DELETE_CHARACTER_OK "
                  "(roster now %d)" % len(roster), flush=True)
        elif real_id == 0xC004:
            # MSG_CL_DECIDE_CHARACTER_REQUEST (0x050f88a6), `[u8 unit][u32 charid]`
            # -- the client logs `unitID=%d chr=%d`. THIS IS THE ONE THAT MATTERS:
            # it is the last thing the character screen does, and 0xD008 is what
            # releases it.
            #
            #     0xD008 MSG_LC_DECIDE_CHARACTER_OK = [u16][u32][u8]
            #
            # read at 0x050257ea. The u16 and u32 are read and DROPPED; only the
            # trailing byte survives, into the global 0x52d3a88. We echo unit and
            # charid into the two dead fields because a wrong value there cannot
            # matter and an echo makes the client's log self-describing.
            #
            # KEY: THE TRAILING BYTE IS THE TUTORIAL-DONE FLAG (named 2026-08-22).
            # 0x04ffc8a0 (scene mode 8) reads 0x52d3a88 to pick the map screen
            # variant: 0 -> arg 4 = the FIRST-LOGIN TUTORIAL map, 1 -> arg 0 =
            # the normal map. The tutorial map ends with the client sending
            # 0x4014 [u32 charid] ("チュートリアル終了" at 0x05105a9a), setting
            # 0x52d3a88=1 itself, and -- after its field pick and enter-area
            # complete -- CLEANLY EXITING BACK TO THE VIEWER (GameStart returns
            # 0). Serving 0 here for a veteran character replays the tutorial
            # every login and re-triggers that designed exit, which reads as
            # "FE dies at 'please wait' and spawns another Viewer". feworld
            # persists `tutorial: 1` on the stored character when 0x4014
            # arrives; serve it back here.
            #
            # 0xD009 is the NG arm: [u32 err] indexing a 12-entry English string
            # table at 0x52d0784 ("Lobby server busy", "Common server: char data
            # missing", ...) -- worth serving deliberately if a failure path ever
            # needs testing.
            f = inner[2:]
            unit = f[0] if f else 0
            charid = struct.unpack_from(">I", f, 1)[0] if len(f) >= 5 else 0
            tutorial = 0
            known = False
            for c in (roster or []):
                if c.get("charid") == charid:
                    known = True
                    if c.get("tutorial"):
                        tutorial = 1
            print("[felobby]    DECIDE CHARACTER unit=%d charid=%d tutorial=%d"
                  % (unit, charid, tutorial), flush=True)
            if not known:
                # The client can only pick from the list we served, so this
                # means the store changed underneath the screen (a rekey, a
                # hand edit, a restore from .bak). It is answered OK anyway --
                # the NG string table's index for "char data missing" is not
                # pinned -- but said out loud, because feworld will then find
                # no stored character for this charid and serve NO avatar,
                # which reads on screen as a shadow with nobody in it.
                print("[felobby]    WARNING: charid=%d is NOT in %r's stored roster "
                      "(%s). The world door will have no character record to "
                      "dress -- expect 'NO stored character matched' there."
                      % (charid, acct,
                         [c.get("charid") for c in (roster or [])]),
                      flush=True)
            send_frame(conn, 0x30,
                       bf_encrypt(outbound,
                                  traffic_wrap(
                                      inner_msg(0xD008,
                                                struct.pack(">HIB", unit,
                                                            charid, tutorial)),
                                      next_seq()), mode, be))
            print("[felobby] -> 0x30 inner 0xD008 MSG_LC_DECIDE_CHARACTER_OK "
                  "(tutorial-done=%d -> %s map)"
                  % (tutorial, "normal" if tutorial else "TUTORIAL"), flush=True)
            print("[felobby]    the character screen is released here. Watch for "
                  "the WORLD PICKER and then 0x7820 MSG_LOBBY_JOIN_GAME_REQ -- "
                  "the lobby state machine only RETURNS at state 0x11, after "
                  "0x7821 sets [obj+0x93c]", flush=True)
        else:
            print("[felobby]    (no handler for inner 0x%04X -- logging only; its "
                  "arrival is the finding)" % real_id, flush=True)


def resolve_identity(ip, args):
    """WHO is at `ip`, and what name we will hand them in 0xC010.

    Resolved ONCE per connection, at accept, so the answer is logged next to the
    CONNECT line whether or not the handshake gets far enough to use it -- an
    identity that is only reported when it works cannot be debugged when it
    does not.
    """
    if args.account_mode == "fixed":
        # The pre-2026-08-24 behaviour, deliberately reachable for A/B against
        # old captures: one name, and therefore ONE ROSTER for every player.
        ident = {"key": args.account, "member_id": None, "nick": None,
                 "content_id": None,
                 "detail": "--account-mode fixed: every connection keys the "
                           "SAME roster (%r). This is the pre-2026-08-24 bug "
                           "on purpose; do not run a server on it."
                           % args.account}
    else:
        # None -> feident's own default path; "" -> skip the lookup entirely.
        ident = feident.identify(ip, tag="felobby",
                                 accounts_db=args.accounts_db,
                                 window=args.member_window)
    ident["ip"] = ip
    ident["wire"] = feident.wire_account(ident, args.account_mode, args.account)
    print("[felobby] identity: store key %s, wire account %r -- %s"
          % (ident["key"], ident["wire"], ident["detail"]), flush=True)
    return ident


def _serve_one(conn, addr, args):
    """One lobby session, on its own thread."""
    try:
        ident = resolve_identity(addr[0], args)
        conn.settimeout(args.read_window)
        do_handshake(conn, args, ident)
    except Exception as e:                      # noqa: BLE001
        print("[felobby] handshake error for %s:%d: %r" % (addr[0], addr[1], e),
              flush=True)
    finally:
        try:
            conn.close()
        except OSError:
            pass
        print("[felobby] CLOSED (%s:%d)" % addr, flush=True)


def main():
    # WARNING: REDIRECTED STDOUT IS cp1252 ON THIS HOST, AND THIS FILE PRINTS NON-ASCII.
    # Without this, `> felobby.log` turns the first WARNING: in a print into a
    # UnicodeEncodeError -- which is exactly how a live run died at the moment it
    # succeeded on 2026-08-17: the 0xD002 frame was already on the wire and the
    # client had parsed it, but the crash closed the socket and dropped FE back
    # to the Viewer, so it read as a protocol failure and was not one. The frame
    # goes out BEFORE the print, so the wire is never what breaks -- the session
    # is.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=54849)
    ap.add_argument("--capture", action="store_true",
                    help="original probe: accept, read, close (no handshake)")
    ap.add_argument("--master-key", default="fantasyearth")
    ap.add_argument("--master-mode", default="ecb/le",
                    choices=["ecb/be", "ecb/le", "cbc/be", "cbc/le"],
                    help="how to decipher the phase-1 blob; ecb/le is MEASURED "
                         "(it is the only one whose envelope is self-consistent "
                         "across two live captures)")
    ap.add_argument("--phase2-key", default="master", choices=["master", "session"],
                    help="which cipher the 0x35 body is enciphered under. MASTER "
                         "is what the code says: decipher (vtable+0xc) delegates "
                         "to objA, keyed with the master key at construction, "
                         "while phase 1 keys objB with the session key.")
    ap.add_argument("--world-ip", default=default_world_ip(),
                    help="world-server address in MSG_LOBBY_JOIN_GAME_OK; goes on "
                         "the wire byte-REVERSED, like the LLB reply. Defaults "
                         "to $POL_ADVERTISE, then $POL_STUB_IP, then 127.0.0.1 "
                         "-- the same ladder fellb uses for the lobby address, "
                         "so the two hops cannot name different hosts by "
                         "accident. (It used to default to one dev machine's "
                         "LAN address.)")
    ap.add_argument("--world-port", type=int, default=54850)
    ap.add_argument("--world-code", type=int, default=1)
    ap.add_argument("--worlds", default=default_worlds(),
                    help="game units offered in MSG_LC_GAME_UNIT_INFO_NOTIFY "
                         "(0xD00C), comma-separated as "
                         "UnitID:GameID:UserNum:WorldName. UnitID is the key the "
                         "world picker and the JOIN step index by, and a miss is "
                         "dereferenced unchecked at 0x050482e1 -- offer every id "
                         "the client might have stored. "
                         "The NAME is what the player reads on the world picker; "
                         "it comes from POL_FE_WORLD (see default_worlds), "
                         "which the POL content profile reads too so the two "
                         "cannot drift. It is `PlayOnline` to match FFXI: "
                         "prod's .env sets "
                         "LSB_SERVER_NAME=PlayOnline, so LSB shows that on FFXI's "
                         "character-select and world-list screens and the two "
                         "titles should not disagree about what this server is "
                         "called. It used to be the placeholder `Test World` -- "
                         "which was still SERVED, so `no world name is configured "
                         "anywhere` was never true; it was configured badly.")
    ap.add_argument("--characters", default=[], nargs="*",
                    metavar="NAME[:CHARID[:KEY=VALUE...]]",
                    help="characters served in 0xD002. The record layout is "
                         "mapped (see CHAR_FIELDS); any of its keys is settable "
                         "per character, plus equip=/skills=/items= as counts. "
                         "Default is the empty list, which is still a complete "
                         "valid answer.")
    ap.add_argument("--char-slots", type=int, default=None,
                    help="total character SLOTS in the 0xD002 header. Slots past "
                         "the ones filled cost no wire bytes -- the client makes "
                         "them empty slots with charid -1 -- so this is how the "
                         "select screen is given room to create a character. "
                         "Defaults to exactly the number of characters served.")
    ap.add_argument("--seq-mode", default="echo", choices=["echo", "count"],
                    help="how the outbound traffic envelope is numbered. "
                         "`echo` mirrors the sequence of the message being "
                         "answered and is what has actually carried a player "
                         "into the world; `count` numbers our own frames from "
                         "1. The client validates this field -- its cipher "
                         "reports `illegal(sequence)` -- and which rule it "
                         "wants is NOT established.")
    ap.add_argument("--char-store", default=_default_store(),
                    help="JSON file created characters are saved to and loaded "
                         "from, keyed by the RESOLVED POL MEMBER (feident.py) "
                         "-- not by --account, which is what made it one "
                         "roster for the whole server. Set empty to disable. JSON "
                         "and not SQLite on purpose: a container restart once "
                         "truncated data/accounts.db, because WAL-mode SQLite on "
                         "a Windows bind mount is not crash-safe "
                         "(accounts-db-wal-hazard).")
    ap.add_argument("--char-reset", action="store_true",
                    help="ignore this member's stored characters and re-seed "
                         "from --characters, overwriting them on the next save")
    ap.add_argument("--char-hp", type=lambda v: int(v, 0), default=1000,
                    help="the FLAT max HP every served character carries "
                         "(2006: 1000 at every level; was 200 until "
                         "2026-09-11). A character whose w72+w74 is anything "
                         "else -- zero, or an old stored 200 -- is rewritten "
                         "to w72=w76=N and the roster saved. Keep it equal to "
                         "feworld's --player-hp. The 0xD002 record's "
                         "+0x72/+0x74/+0x76 are copied onto the field unit "
                         "(0x4fe75a0: -> +0x49a/+0x49c/+0x49e) and the in-field "
                         "HP gauge divides by (+0x49a + +0x49c) at 0x50cd049 -- "
                         "all-zero stats are a guaranteed c0000094 divide-by-"
                         "zero the moment the field finishes loading (live "
                         "2026-08-23). 0 disables the backfill for A/B.")
    ap.add_argument("--char-pw", type=lambda v: int(v, 0), default=100,
                    help="same backfill for the PW triple w78/w7a/w7c "
                         "(-> unit +0x4a0/+0x4a2/+0x4a4; the PW gauge's idiv "
                         "at 0x50ce184 crashed the pick AFTER the HP fix, live "
                         "2026-08-23). 100 matches the widget's own no-player "
                         "fallback (0x50ce10f). 0 disables.")
    ap.add_argument("--char-gear", default="on", choices=["on", "items", "off"],
                    help="seed a character that owns nothing with its class's "
                         "STARTING GEAR from dat.pak's fet_initialize_equip "
                         "(services/fedata via fegamedata.py) the next time "
                         "its list is served, and persist it. 'on' = item "
                         "instances AND equip pairs (slot from the item "
                         "table); 'items' = instances only, nothing worn; "
                         "'off' = the pre-2026-09-05 empty bag. Class is the "
                         "stored `look1`, sex picks the male|female list.")
    ap.add_argument("--char-probe", action="store_true",
                    help="fill every unnamed character field with its own WIRE "
                         "OFFSET instead of 0, so anything the client renders or "
                         "logs can be traced back to the bytes that produced it. "
                         "Off by default: an arbitrary value in a field used as a "
                         "table index is a plausible way to crash the client.")
    ap.add_argument("--notice", default=None, metavar="TITLE|TEXT",
                    help="answer 0xC009 with a 0xD014 notice window instead of "
                         "0xD015 'nothing to show'. FE then blocks in lobby state "
                         "0xf until the player closes the window")
    ap.add_argument("--notice-id", type=lambda s: int(s, 0), default=1,
                    help="the 0xD014 id dword. The client caches it at 0x52d49ac "
                         "(initially 0xFFFFFFFF) and SKIPS the window when it "
                         "matches, so 0xFFFFFFFF is a no-op on a fresh client")
    ap.add_argument("--account-mode", default="member",
                    choices=["member", "nick", "fixed"],
                    help="what goes on the wire as the account name in 0xC010, "
                         "and NOT what the roster is keyed by (that is always "
                         "the resolved POL member -- see feident.py). `member` "
                         "sends the store key itself, which is what lets "
                         "feworld pick up the same roster from the client's own "
                         "0x400F echo with no address heuristic; `nick` sends "
                         "the POL nick; `fixed` restores the pre-2026-08-24 "
                         "constant --account for A/B against old captures. "
                         "WARNING: `fixed` also forces the STORE key back to that "
                         "constant, i.e. one roster for every player -- it is "
                         "for reproducing the old behaviour, not for running.")
    ap.add_argument("--account", default="TestPlayer",
                    help="the account name for --account-mode fixed ONLY. It "
                         "used to be the name for everybody, which is exactly "
                         "the bug feident.py exists to fix: felobby was launched "
                         "without it, every session took this default, and "
                         "fe_characters.json is keyed by it -- so every FE "
                         "player shared one character roster. Goes on the wire "
                         "NUL-terminated, cp932.")
    ap.add_argument("--accounts-db", default=None,
                    help="POL accounts.db consulted to turn the connecting "
                         "address into a member (default: data/accounts.db, or "
                         "$FE_ACCOUNTS_DB / $POL_ACCOUNTS_DB). Set empty to skip "
                         "the lookup -- every connection then keys by ADDRESS, "
                         "which is per-machine and still never shared.")
    ap.add_argument("--member-window", type=float, default=None,
                    help="seconds a POL session row may still name the member at "
                         "an address (default 86400 / $FE_MEMBER_WINDOW)")
    ap.add_argument("--session-store", default=None,
                    help="the felobby -> feworld handoff file (default "
                         "data/fe_sessions.json). Set empty to disable it and "
                         "leave feworld relying on the 0x400F account echo.")
    ap.add_argument("--server-key", default="0123456789abcdef0123456789abcdef",
                    help="hex of OUR half of the key exchange (objC). MUST be "
                         "non-empty: the client runs key[i %% keylen] with an idiv "
                         "at 0x05233c8d, so a zero length crashes it outright.")
    ap.add_argument("--seq", type=int, default=1,
                    help="phase-2 sequence dword; the client stores it decremented "
                         "at [obj+0x20] (0x052358fc)")
    ap.add_argument("--challenge-len", type=int, default=16,
                    help="16 makes the envelope exactly 24 bytes, mirroring the "
                         "client's own phase-1 frame")
    ap.add_argument("--captures", type=int, default=1,
                    help="exit after this many connections; 0 = never, which is "
                         "what the docker service uses")
    ap.add_argument("--life", type=int, default=300,
                    help="exit after this many seconds no matter what; 0 = never. "
                         "Both limits exist because a stray listener on THIS host "
                         "is a hazard (host-process-hygiene: lingering python "
                         "locks polinject.dll and breaks the next injected run). "
                         "In a container neither applies, so both are 0 there.")
    ap.add_argument("--read-window", type=float, default=5.0,
                    help="seconds to keep reading before closing the connection")
    ap.add_argument("--out", default=None, help="write raw captured bytes here")
    args = ap.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(4)
    srv.settimeout(1.0)
    print("[felobby] capture probe on 0.0.0.0:%d -- accepts, reads <=%.1fs, then "
          "CLOSES so FE fails fast" % (args.port, args.read_window), flush=True)
    limits = " and ".join(
        [t for t in (("%d capture(s)" % args.captures) if args.captures > 0 else "",
                     ("%ds" % args.life) if args.life > 0 else "") if t])
    print("[felobby] %s" % ("exits after " + limits if limits else
                            "RUNS UNTIL STOPPED (no capture or life limit) -- "
                            "each idle connection is still hung up after "
                            "%gs" % args.read_window),
          flush=True)

    # Report live FE lobby sessions to the deploy gate (one named thread each).
    try:
        import live_sessions
        live_sessions.start_heartbeat(
            "felobby", lambda: live_sessions.thread_count("felobby-"))
    except Exception as _e:
        print("[felobby] live-session heartbeat not started: %r" % _e, flush=True)

    deadline = time.time() + args.life
    got = 0
    # 0 means "no limit" for either bound -- see the --life help. The listener
    # still hangs up on each idle connection after --read-window, so an unlimited
    # service never becomes the thing the whole file warns about: a bind that
    # accepts and then leaves FE waiting with no way back to the Viewer.
    while ((args.captures <= 0 or got < args.captures)
           and (args.life <= 0 or time.time() < deadline)):
        try:
            conn, addr = srv.accept()
        except socket.timeout:
            continue
        print("\n[felobby] CONNECT from %s:%d" % addr, flush=True)
        if not args.capture:
            got += 1
            # WARNING: THREADED, AND THAT IS PART OF THE FIX, NOT A FLOURISH. The
            # accept loop used to serve one connection to completion -- up to
            # --read-window 300s in prod -- before accepting the next, so a
            # second player's FE sat unanswered on a connected socket until the
            # first one's session ended. Per-player rosters mean nothing while
            # only one player can be in the lobby. Every per-session value now
            # lives in serve_lobby's frame or `ident`; the only shared mutable
            # thing left is the character store, held under _store_lock.
            threading.Thread(target=_serve_one, args=(conn, addr, args),
                             name="felobby-%s-%d" % addr, daemon=True).start()
            continue
        buf = b""
        conn.settimeout(args.read_window)
        stop = time.time() + args.read_window
        try:
            while time.time() < stop:
                c = conn.recv(4096)
                if not c:
                    break
                buf += c
                print("[felobby]   +%d bytes (total %d)" % (len(c), len(buf)), flush=True)
        except (OSError, socket.timeout):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
        print("[felobby] CLOSED (FE should now report a connection failure)", flush=True)

        if buf:
            got += 1
            print("[felobby] captured %d bytes:" % len(buf), flush=True)
            print(hexdump(buf), flush=True)
            if args.out:
                os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
                with open(args.out, "wb") as f:
                    f.write(buf)
                print("[felobby] raw bytes -> %s" % args.out, flush=True)
        else:
            print("[felobby] connection carried NO data -- FE may be waiting for a "
                  "server-first greeting; if this repeats, that itself is the finding",
                  flush=True)

    srv.close()
    # WARNING: WAIT FOR SESSIONS IN FLIGHT before returning. The session threads are
    # daemons, so process exit would kill them where they stand -- and this file
    # already carries the scar from a session dying at the moment it succeeded:
    # the 0xD002 frame was on the wire and the client had parsed it, but the
    # dropped socket read as a protocol failure it was not. `--captures 1` on
    # the host is exactly the case that hits this, because the limit is reached
    # at ACCEPT, when the session has barely started.
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.name.startswith("felobby-"):
            t.join(timeout=max(args.read_window, 5.0) + 5.0)
    print("\n[felobby] done (%d capture(s)); listener closed" % got, flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
