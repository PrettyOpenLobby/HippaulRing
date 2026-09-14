"""Decrypt Fantasy Earth "FE00EN00" container files.

The FE client stores its data files as an 8-byte magic "FE00" "EN00" followed by
a size-preserving ciphertext of filesize-8 bytes. Files without the magic are
plaintext and read as-is.

Cipher (verified byte-for-byte against known plaintext/ciphertext pairs):

  * Rijndael with block Nb=8 words (256-bit block), key Nk=4 words (128-bit),
    Nr = 14 rounds. Standard FIPS-197 byte order (state byte i sits at row i%4,
    column i//4; round-key words are big-endian). ShiftRows offsets for Nb=8
    are (0, 1, 3, 4) per the Rijndael spec.
  * Full 32-byte blocks are chained CBC-style with a zero IV:
    P[i] = D(C[i]) xor C[i-1], with C[-1] = 0.
  * A trailing remainder r = size mod 32 (r > 0) is covered by one extra block
    in ECB over the FINAL 32 bytes of the payload, overlapping the last full
    block: V = D(ct[n-32:n]) gives the last r plaintext bytes in V[32-r:], and
    V[:32-r] restores the ciphertext bytes of the last full block that the
    encryptor overwrote. The last full block is then decrypted normally.

There is no single key. The client loader is called with a per-file-type
16-byte key (one for .pak archives, another for .bmp images, others for
.cfg/UI layout and the rest). No key ships with this repository: the key
lives as plain data in your own FE_Client.dll and fekeys.py recovers it from
there by trial decryption of the input file's first block.

Usage:

    python fedecrypt.py <infile> <outfile> --key <hex32>
    python fedecrypt.py <infile> <outfile> --dll <FE_Client.dll>

--dll recovers the key against the input file itself; that works for a dat
archive (path table beginning "dat\\") and for .bmp images (plaintext
"BM"). Other file types need --key.

Importable API: decrypt_bytes(data, key) and decrypt_file(path, key), where
key is 16 raw bytes or 32 hex chars. Pure standard library.
"""
import argparse
import os
import struct
import sys
import time

MAGIC = b"FE00EN00"
NB, NK, NR = 8, 4, 14      # words per block, words per key, rounds
BS = NB * 4                # 32-byte block

# ---------------------------------------------------------------------------
# Rijndael tables (computed at import; a few ms)
# ---------------------------------------------------------------------------

def _gmul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1
        b >>= 1
    return r


def _build_tables():
    # S-box: multiplicative inverse in GF(2^8) followed by the affine transform.
    sbox = [0] * 256
    inv_sbox = [0] * 256
    for i in range(256):
        b = 0
        if i:
            b = 1
            for _ in range(254):
                b = _gmul(b, i)
        s = 0x63
        for k in range(8):
            bit = ((b >> k) ^ (b >> ((k + 4) % 8)) ^ (b >> ((k + 5) % 8)) ^
                   (b >> ((k + 6) % 8)) ^ (b >> ((k + 7) % 8))) & 1
            s ^= bit << k
        sbox[i] = s
    for i, v in enumerate(sbox):
        inv_sbox[v] = i

    g9 = [_gmul(x, 9) for x in range(256)]
    g11 = [_gmul(x, 11) for x in range(256)]
    g13 = [_gmul(x, 13) for x in range(256)]
    g14 = [_gmul(x, 14) for x in range(256)]

    # Decryption T-tables: InvSubBytes then InvMixColumns, per input row.
    td0 = [0] * 256
    td1 = [0] * 256
    td2 = [0] * 256
    td3 = [0] * 256
    for x in range(256):
        y = inv_sbox[x]
        td0[x] = (g14[y] << 24) | (g9[y] << 16) | (g13[y] << 8) | g11[y]
        td1[x] = (g11[y] << 24) | (g14[y] << 16) | (g9[y] << 8) | g13[y]
        td2[x] = (g13[y] << 24) | (g11[y] << 16) | (g14[y] << 8) | g9[y]
        td3[x] = (g9[y] << 24) | (g13[y] << 16) | (g11[y] << 8) | g14[y]
    return sbox, inv_sbox, (g9, g11, g13, g14), (td0, td1, td2, td3)


SBOX, INV_SBOX, _GTABS, TD = _build_tables()


def _expand_key(key):
    """Expand a 16-byte key to Nb*(Nr+1) = 120 round-key words.

    Returns (rk_first, rk_mid, rk_last):
      rk_first = round 14 words (initial AddRoundKey of the inverse cipher),
      rk_mid   = rounds 13..1 with InvMixColumns applied (T-table form),
      rk_last  = round 0 words (final AddRoundKey).
    """
    if len(key) != 16:
        raise ValueError("key must be 16 bytes")
    w = list(struct.unpack(">4I", key))
    rcon = 1
    i = NK
    while len(w) < NB * (NR + 1):
        t = w[-1]
        if i % NK == 0:
            t = ((t << 8) | (t >> 24)) & 0xFFFFFFFF
            t = ((SBOX[(t >> 24) & 0xFF] << 24) | (SBOX[(t >> 16) & 0xFF] << 16) |
                 (SBOX[(t >> 8) & 0xFF] << 8) | SBOX[t & 0xFF])
            t ^= rcon << 24
            rcon = ((rcon << 1) ^ 0x1B) & 0xFF if rcon & 0x80 else rcon << 1
        w.append(w[-NK] ^ t)
        i += 1

    g9, g11, g13, g14 = _GTABS

    def imc(word):
        b0 = (word >> 24) & 0xFF
        b1 = (word >> 16) & 0xFF
        b2 = (word >> 8) & 0xFF
        b3 = word & 0xFF
        return ((g14[b0] ^ g11[b1] ^ g13[b2] ^ g9[b3]) << 24 |
                (g9[b0] ^ g14[b1] ^ g11[b2] ^ g13[b3]) << 16 |
                (g13[b0] ^ g9[b1] ^ g14[b2] ^ g11[b3]) << 8 |
                (g11[b0] ^ g13[b1] ^ g9[b2] ^ g14[b3]))

    rk_first = tuple(w[NR * NB:(NR + 1) * NB])
    rk_mid = tuple(tuple(imc(w[r * NB + c]) for c in range(NB))
                   for r in range(NR - 1, 0, -1))
    rk_last = tuple(w[0:NB])
    return rk_first, rk_mid, rk_last


def _decrypt_block(cols, rk_first, rk_mid, rk_last):
    """Decrypt one 32-byte block given as 8 big-endian u32 columns.

    Equivalent inverse cipher with T-tables. Column mapping follows the Nb=8
    InvShiftRows offsets (0, 1, 3, 4): output column c takes its row-0 byte
    from column c, row 1 from c-1, row 2 from c-3, row 3 from c-4 (mod 8).
    """
    td0, td1, td2, td3 = TD
    c0, c1, c2, c3, c4, c5, c6, c7 = cols
    k = rk_first
    c0 ^= k[0]; c1 ^= k[1]; c2 ^= k[2]; c3 ^= k[3]
    c4 ^= k[4]; c5 ^= k[5]; c6 ^= k[6]; c7 ^= k[7]
    for k in rk_mid:
        n0 = td0[(c0 >> 24) & 255] ^ td1[(c7 >> 16) & 255] ^ td2[(c5 >> 8) & 255] ^ td3[c4 & 255] ^ k[0]
        n1 = td0[(c1 >> 24) & 255] ^ td1[(c0 >> 16) & 255] ^ td2[(c6 >> 8) & 255] ^ td3[c5 & 255] ^ k[1]
        n2 = td0[(c2 >> 24) & 255] ^ td1[(c1 >> 16) & 255] ^ td2[(c7 >> 8) & 255] ^ td3[c6 & 255] ^ k[2]
        n3 = td0[(c3 >> 24) & 255] ^ td1[(c2 >> 16) & 255] ^ td2[(c0 >> 8) & 255] ^ td3[c7 & 255] ^ k[3]
        n4 = td0[(c4 >> 24) & 255] ^ td1[(c3 >> 16) & 255] ^ td2[(c1 >> 8) & 255] ^ td3[c0 & 255] ^ k[4]
        n5 = td0[(c5 >> 24) & 255] ^ td1[(c4 >> 16) & 255] ^ td2[(c2 >> 8) & 255] ^ td3[c1 & 255] ^ k[5]
        n6 = td0[(c6 >> 24) & 255] ^ td1[(c5 >> 16) & 255] ^ td2[(c3 >> 8) & 255] ^ td3[c2 & 255] ^ k[6]
        n7 = td0[(c7 >> 24) & 255] ^ td1[(c6 >> 16) & 255] ^ td2[(c4 >> 8) & 255] ^ td3[c3 & 255] ^ k[7]
        c0, c1, c2, c3, c4, c5, c6, c7 = n0, n1, n2, n3, n4, n5, n6, n7
    s = INV_SBOX
    k = rk_last
    return (
        (s[(c0 >> 24) & 255] << 24 | s[(c7 >> 16) & 255] << 16 | s[(c5 >> 8) & 255] << 8 | s[c4 & 255]) ^ k[0],
        (s[(c1 >> 24) & 255] << 24 | s[(c0 >> 16) & 255] << 16 | s[(c6 >> 8) & 255] << 8 | s[c5 & 255]) ^ k[1],
        (s[(c2 >> 24) & 255] << 24 | s[(c1 >> 16) & 255] << 16 | s[(c7 >> 8) & 255] << 8 | s[c6 & 255]) ^ k[2],
        (s[(c3 >> 24) & 255] << 24 | s[(c2 >> 16) & 255] << 16 | s[(c0 >> 8) & 255] << 8 | s[c7 & 255]) ^ k[3],
        (s[(c4 >> 24) & 255] << 24 | s[(c3 >> 16) & 255] << 16 | s[(c1 >> 8) & 255] << 8 | s[c0 & 255]) ^ k[4],
        (s[(c5 >> 24) & 255] << 24 | s[(c4 >> 16) & 255] << 16 | s[(c2 >> 8) & 255] << 8 | s[c1 & 255]) ^ k[5],
        (s[(c6 >> 24) & 255] << 24 | s[(c5 >> 16) & 255] << 16 | s[(c3 >> 8) & 255] << 8 | s[c2 & 255]) ^ k[6],
        (s[(c7 >> 24) & 255] << 24 | s[(c6 >> 16) & 255] << 16 | s[(c4 >> 8) & 255] << 8 | s[c3 & 255]) ^ k[7],
    )


def _resolve_key(key):
    if key is None:
        raise ValueError("a key is required: 16 bytes or 32 hex chars; "
                         "recover yours from FE_Client.dll with fekeys.py")
    if isinstance(key, (bytes, bytearray)):
        kb = bytes(key)
    else:
        try:
            kb = bytes.fromhex(key)
        except ValueError:
            raise ValueError("key must be 32 hex chars (no key table ships; "
                             "recover yours with fekeys.py or --dll)") from None
    if len(kb) != 16:
        raise ValueError("key must be 16 bytes / 32 hex chars")
    return kb


def decrypt_bytes(data, key, progress=False):
    """Decrypt one FE00EN00 container held in memory.

    data: full file content, magic included. Returned unchanged if the magic
    is absent. key: 32 hex chars or 16 raw bytes.
    """
    if data[:8] != MAGIC:
        return bytes(data)
    ct = bytes(data[8:])
    n = len(ct)
    if n == 0:
        return b""
    if n < BS:
        # Never observed in shipped data; the tail scheme needs one full block.
        sys.stderr.write("fedecrypt: payload shorter than one 32-byte block, "
                         "returning it unchanged\n")
        return ct

    rk_first, rk_mid, rk_last = _expand_key(_resolve_key(key))
    full = n - (n % BS)
    r = n - full

    work = bytearray(ct[:full])
    out = bytearray(n)
    if r:
        # Extra ECB block over the final 32 bytes of the payload. Its decrypt
        # holds the last r plaintext bytes and restores the 32-r ciphertext
        # bytes of the last full block that the encryptor overwrote.
        v = struct.pack(">8I", *_decrypt_block(
            struct.unpack(">8I", ct[n - BS:]), rk_first, rk_mid, rk_last))
        out[full:] = v[BS - r:]
        work[n - BS:full] = v[:BS - r]

    unpack = struct.Struct(">%dI" % (full // 4)).unpack
    cols = unpack(bytes(work))
    pack_into = struct.Struct(">8I").pack_into
    dec = _decrypt_block
    prev = (0, 0, 0, 0, 0, 0, 0, 0)
    t0 = time.time()
    step = (1 << 22) // BS  # progress every 4 MB
    nblocks = full // BS
    for b in range(nblocks):
        i = b * 8
        c = cols[i:i + 8]
        p = dec(c, rk_first, rk_mid, rk_last)
        pack_into(out, i * 4,
                  p[0] ^ prev[0], p[1] ^ prev[1], p[2] ^ prev[2], p[3] ^ prev[3],
                  p[4] ^ prev[4], p[5] ^ prev[5], p[6] ^ prev[6], p[7] ^ prev[7])
        prev = c
        if progress and b and b % step == 0:
            done = b * BS
            rate = done / max(time.time() - t0, 1e-9) / 1048576
            sys.stderr.write("fedecrypt: %d/%d MB (%.1f MB/s)\n"
                             % (done >> 20, n >> 20, rate))
    return bytes(out)


def decrypt_file(path, key, progress=True):
    """Decrypt a file with the given key (16 bytes or 32 hex chars). A file
    without the magic is returned as-is, key unused."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != MAGIC:
        return data
    return decrypt_bytes(data, key, progress=progress)


def recover_key(dll_path, path):
    """Recover the key for `path` from the client DLL (see fekeys.py).
    Returns (key_bytes, dll_offset); raises SystemExit when none is found."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fekeys
    with open(dll_path, "rb") as f:
        dll = f.read()
    with open(path, "rb") as f:
        sample = f.read()
    magic = b"BM" if os.path.splitext(path)[1].lower() == ".bmp" else None
    key, off, st = fekeys.scan(dll, sample, magic)
    if key is None:
        sys.exit("fedecrypt: no key in %s opens %s (%d candidates tried, %.0fs)"
                 % (dll_path, path, st["tried"], st["seconds"]))
    sys.stderr.write("fedecrypt: key recovered at %s+0x%x (%d candidates, %.1fs)\n"
                     % (os.path.basename(dll_path), off, st["tried"], st["seconds"]))
    return key, off


def main():
    ap = argparse.ArgumentParser(description="Decrypt FE00EN00 container files")
    ap.add_argument("infile")
    ap.add_argument("outfile")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--key", default=None, help="the 16-byte key as 32 hex chars")
    g.add_argument("--dll", default=None,
                   help="your FE_Client.dll: recover the key from it against "
                        "the input file (dat archives and .bmp)")
    a = ap.parse_args()
    t0 = time.time()
    key = a.key
    with open(a.infile, "rb") as f:
        encrypted = f.read(8) == MAGIC
    if encrypted and key is None:
        if a.dll is None:
            sys.exit("fedecrypt: %s is encrypted; pass --key <hex32> or "
                     "--dll <FE_Client.dll>" % a.infile)
        key = recover_key(a.dll, a.infile)[0]
    try:
        out = decrypt_file(a.infile, key)
    except ValueError as e:
        sys.exit("fedecrypt: %s" % e)
    with open(a.outfile, "wb") as f:
        f.write(out)
    print("%s -> %s (%d bytes, %s, %.1fs)"
          % (a.infile, a.outfile, len(out),
             "decrypted" if encrypted else "already plaintext", time.time() - t0))


if __name__ == "__main__":
    main()
