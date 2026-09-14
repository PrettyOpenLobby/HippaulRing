#!/usr/bin/env python3
"""Generate services/fe_blowfish_P.bin and fe_blowfish_S.bin from first
principles.

Fantasy Earth's client uses Blowfish whose tables are the STANDARD pi-derived
constants with every byte incremented by one (see services/feblowfish.py for
the full derivation, including the XOR 0x91 storage layer inside the client
image). Because the standard tables are simply the fractional hexadecimal
digits of pi, the shipped .bin files contain no proprietary data at all and
can be regenerated from mathematics alone. This script does exactly that:

  1. compute pi to enough places (Chudnovsky series, integer arithmetic),
  2. take 8336 fractional hex digits -> the standard P (18 words) and
     S (4 x 256 words) tables,
  3. serialize little-endian and add 1 to every byte (mod 256),
  4. verify the STANDARD tables against the published Blowfish known-answer
     test (all-zero key, all-zero block -> 4EF997456198DD78) before writing.

Run once before building the Docker image:

  python tools/gen_blowfish_tables.py
"""
import os
import struct
import sys
from math import isqrt

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, "services")
N_WORDS = 18 + 4 * 256          # P then S0..S3
KAT_CIPHERTEXT = 0x4EF997456198DD78


def pi_hex_words(n_words):
    """Fractional hex digits of pi as n_words 32-bit words, via Chudnovsky."""
    hex_digits = n_words * 8
    dec_digits = int(hex_digits * 1.20412) + 60   # log10(16) margin
    one = 10 ** dec_digits
    # Chudnovsky: 1/pi = 12 * sum_k (-1)^k (6k)! (13591409+545140134k)
    #                    / ((3k)! (k!)^3 640320^(3k+3/2))
    k, a_k, a_sum, b_sum = 0, one, one, 0
    C = 640320
    C3_OVER_24 = C ** 3 // 24
    while a_k != 0:
        k += 1
        a_k *= -(6 * k - 5) * (2 * k - 1) * (6 * k - 1)
        a_k //= k * k * k * C3_OVER_24
        a_sum += a_k
        b_sum += k * a_k
    total = 13591409 * a_sum + 545140134 * b_sum
    sqrt_c = isqrt(10005 * one * one)
    pi_scaled = 426880 * sqrt_c * one // total
    frac = pi_scaled - 3 * one
    words = []
    for _ in range(n_words):
        frac *= 16 ** 8
        w, frac = divmod(frac, one)
        words.append(w & 0xFFFFFFFF)
    return words


def blowfish_encrypt_zero(P, S):
    """Encrypt the all-zero block with the all-zero key, big-endian halves
    (the standard algorithm, used only for the known-answer test)."""
    P = list(P)  # zero key: subkeys unchanged by the XOR step
    M = 0xFFFFFFFF

    def F(x):
        h = (S[(x >> 24) & 0xFF] + S[256 + ((x >> 16) & 0xFF)]) & M
        return ((h ^ S[512 + ((x >> 8) & 0xFF)]) + S[768 + (x & 0xFF)]) & M

    def enc(l, r):
        for i in range(16):
            l = (l ^ P[i]) & M
            r = (r ^ F(l)) & M
            l, r = r, l
        l, r = r, l
        return (l ^ P[17]) & M, (r ^ P[16]) & M

    # key schedule with zero key, then the standard P/S replacement rounds
    l = r = 0
    for i in range(0, 18, 2):
        l, r = enc(l, r)
        P[i], P[i + 1] = l, r
    for i in range(0, 1024, 2):
        l, r = enc(l, r)
        S[i], S[i + 1] = l, r
    l, r = enc(0, 0)
    return (l << 32) | r


def main():
    print("computing %d words of pi..." % N_WORDS)
    words = pi_hex_words(N_WORDS)
    P, S = words[:18], words[18:]
    assert P[0] == 0x243F6A88 and P[1] == 0x85A308D3, "pi digits wrong"
    assert S[0] == 0xD1310BA6, "S-box digits wrong"
    kat = blowfish_encrypt_zero(P, list(S))
    if kat != KAT_CIPHERTEXT:
        sys.exit("known-answer test FAILED (%016X); refusing to write" % kat)
    print("known-answer test passed (%016X)" % kat)
    p_raw = struct.pack("<18I", *P)
    s_raw = struct.pack("<1024I", *S)
    plus1 = bytes((b + 1) & 0xFF for b in p_raw + s_raw)
    with open(os.path.join(OUT_DIR, "fe_blowfish_P.bin"), "wb") as fh:
        fh.write(plus1[:72])
    with open(os.path.join(OUT_DIR, "fe_blowfish_S.bin"), "wb") as fh:
        fh.write(plus1[72:])
    print("wrote services/fe_blowfish_P.bin (72 B) and fe_blowfish_S.bin (4096 B)")


if __name__ == "__main__":
    main()
