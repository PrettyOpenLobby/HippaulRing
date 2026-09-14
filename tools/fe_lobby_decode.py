#!/usr/bin/env python3
"""fe_lobby_decode.py -- crack the mode of Fantasy Earth's lobby channel (54849).

The algorithm and key are settled (see fe-lobby-blowfish in memory): Blowfish,
key `fantasyearth`, with NON-STANDARD tables -- the stock pi-derived constants
with 1 added to every byte, stored XOR 0x91 in FE_Client. What is NOT settled is
the chaining mode and IV, and that needs ciphertext.

Feed it a capture from services/felobby.py:

    python tools/fe_lobby_decode.py logs/felobby-capture.bin

It sweeps ECB / CBC / CFB / OFB / CTR, both table sets (FE's and stock, so a
wrong assumption about the +1 shows up rather than hiding), both block byte
orders, and a few IV guesses, then SCORES each candidate against the frame shape
we already know from the 54848 dispatch:

    [u16 len][u16 id][u16 f2][u16 f3][body]     big-endian, len counts after itself

A hit is a plausible `len` that matches the buffer, and an `id` in the family the
client's own switch compares (0x7001/0x7002 LOBBY_LOGIN, 0x7821/0x7822
JOIN_GAME, 0x7831/0x7832 GET_GAME_INFO, 0x9000-0x9002 LLB). Anything scoring
above zero is printed with a hexdump so it can be judged by eye -- the score is a
filter, not a verdict.

Self-tests the Blowfish core against the standard all-zero test vector first, so
a failure here is never silently blamed on FE.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(HERE, "..", "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

# ---------------------------------------------------------------- Blowfish ---
# The cipher and FE's tables MOVED to services/feblowfish.py 2026-08-17 and are
# imported rather than duplicated. They used to live here, which put a service
# (felobby.py) in the position of exec'ing a tool to get them -- and pointed both
# at a gitignored build dir in another repo. One implementation, one copy of the
# tables, and both now ship inside this repo.
#
# `--p`/`--s` still override, so a freshly re-extracted pair can be tested
# without replacing the shipped ones.
from feblowfish import (Blowfish, load_tables,  # noqa: E402
                        minus_one_per_byte, selftest)

TBL_DIR = os.path.join(HERE, "..", "services")


# ------------------------------------------------------------------- modes ---
def xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def dec_ecb(bf, ct, iv, be):
    return b"".join(bf.decrypt_block(ct[i:i + 8], be) for i in range(0, len(ct) - 7, 8))


def dec_cbc(bf, ct, iv, be):
    out, prev = b"", iv
    for i in range(0, len(ct) - 7, 8):
        blk = ct[i:i + 8]
        out += xor(bf.decrypt_block(blk, be), prev)
        prev = blk
    return out


def dec_cfb(bf, ct, iv, be):
    out, prev = b"", iv
    for i in range(0, len(ct) - 7, 8):
        blk = ct[i:i + 8]
        out += xor(blk, bf.encrypt_block(prev, be))
        prev = blk
    return out


def dec_ofb(bf, ct, iv, be):
    out, fb = b"", iv
    for i in range(0, len(ct), 8):
        fb = bf.encrypt_block(fb, be)
        out += xor(ct[i:i + 8], fb[:len(ct[i:i + 8])])
    return out


def dec_ctr(bf, ct, iv, be):
    out = b""
    ctr = int.from_bytes(iv, "big")
    for i in range(0, len(ct), 8):
        ks = bf.encrypt_block(ctr.to_bytes(8, "big"), be)
        out += xor(ct[i:i + 8], ks[:len(ct[i:i + 8])])
        ctr = (ctr + 1) & ((1 << 64) - 1)
    return out


MODES = [("ECB", dec_ecb), ("CBC", dec_cbc), ("CFB", dec_cfb),
         ("OFB", dec_ofb), ("CTR", dec_ctr)]

KNOWN_IDS = {0x7001, 0x7002, 0x7821, 0x7822, 0x7831, 0x7832,
             0x9000, 0x9001, 0x9002, 0x0002}


def score(pt, ctlen):
    """How much does this look like [u16 len][u16 id][u16 f2][u16 f3]?"""
    if len(pt) < 4:
        return 0, ""
    ln, mid = struct.unpack(">HH", pt[:4])
    s, why = 0, []
    if ln + 2 == ctlen:
        s += 5; why.append("len==buffer")
    elif 0 < ln <= ctlen:
        s += 2; why.append("len plausible")
    if mid in KNOWN_IDS:
        s += 5; why.append("id=0x%04X KNOWN" % mid)
    elif 0x7000 <= mid <= 0x7900 or 0x9000 <= mid <= 0x9100:
        s += 2; why.append("id=0x%04X in range" % mid)
    if len(pt) >= 8:
        f2, f3 = struct.unpack(">HH", pt[4:8])
        if f2 == 0 and f3 == 0:
            s += 1; why.append("f2=f3=0")
    return s, ", ".join(why)


def hexdump(b, indent="      "):
    out = []
    for off in range(0, min(len(b), 64), 16):
        chunk = b[off:off + 16]
        hx = " ".join("%02x" % c for c in chunk)
        asc = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        out.append("%s%04x  %-47s  %s" % (indent, off, hx, asc))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", help="raw bytes from services/felobby.py")
    ap.add_argument("--key", default="fantasyearth")
    ap.add_argument("--p", default=os.path.join(TBL_DIR, "fe_blowfish_P.bin"))
    ap.add_argument("--s", default=os.path.join(TBL_DIR, "fe_blowfish_S.bin"))
    ap.add_argument("--min-score", type=int, default=5)
    args = ap.parse_args()

    fe_P, fe_S = load_tables(args.p, args.s)
    st_P, st_S = minus_one_per_byte(fe_P, fe_S)

    ok, got, want = selftest(st_P, st_S)
    print("Blowfish self-test (stock tables, derived as FE-1): %s" % ("PASS" if ok else "FAIL"))
    if not ok:
        print("  got %s want %s -- core is wrong, fix before trusting anything below" % (got, want))
        return 1
    print("  -> confirms both the core AND that FE's tables really are stock+1\n")

    ct = open(args.capture, "rb").read()
    print("capture: %d bytes" % len(ct))
    print(hexdump(ct, "  "))
    print()

    key = args.key.encode()
    ivs = [("zero", b"\0" * 8), ("key8", (key + b"\0" * 8)[:8]),
           ("ff", b"\xff" * 8)]
    hits = []
    for tname, (P, S) in (("FE(+1)", (fe_P, fe_S)), ("stock", (st_P, st_S))):
        bf = Blowfish(key, P, S)
        for be in (True, False):
            for mname, fn in MODES:
                for ivname, iv in ivs:
                    if mname == "ECB" and ivname != "zero":
                        continue
                    try:
                        pt = fn(bf, ct, iv, be)
                    except Exception:
                        continue
                    sc, why = score(pt, len(ct))
                    if sc >= args.min_score:
                        hits.append((sc, tname, mname, "BE" if be else "LE", ivname, why, pt))

    hits.sort(key=lambda h: -h[0])
    if not hits:
        print("NO candidate scored >= %d." % args.min_score)
        print("Read that as information, not defeat: it rules out this whole grid")
        print("(both table sets x 5 modes x 2 block orders x 3 IVs), which points at")
        print("a session key or a handshake preamble rather than a static-key stream.")
        return 0
    print("%d candidate(s):\n" % len(hits))
    for sc, tname, mname, order, ivname, why, pt in hits[:8]:
        print("  score %d  tables=%s mode=%s order=%s iv=%s   [%s]"
              % (sc, tname, mname, order, ivname, why))
        print(hexdump(pt))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
