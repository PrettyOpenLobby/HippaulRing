"""Recover the FE00EN00 file key from your own Fantasy Earth client binary.

The client keeps the 16-byte keys for its container files (dat.pak and the
rest) as plain data inside FE_Client.dll (FE_Client.en.dll on the English
client). This repository ships no key material; instead the key is found at
build time by scanning your DLL: every aligned 16-byte window of the DLL's
data sections is tried as the key against the first 32-byte block of a
sample container, and the one that decrypts it to the known plaintext wins.

Why one block is enough: the container is CBC with a zero IV, so block 0
decrypts as plain ECB (P0 = D(C0)). A decrypted dat.pak begins with a path
table: u32, u32, u16 path length, then the path text "dat\\...", so bytes
10..13 of block 0 must read "dat\\" once the key is right.

Usage:

    python fekeys.py --dll <FE_Client.dll> --sample <dat.pak>

prints the key as hex and the file offset it was found at. Importable API:
find_key(dll_bytes, sample_file_bytes) -> bytes | None, and scan() for the
offset and timing. Pure standard library.
"""
import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fedecrypt  # noqa: E402

BS = fedecrypt.BS
MAGIC = fedecrypt.MAGIC
PREFERRED_SECTIONS = (b".rdata", b".data")

# bytes.translate(None, table) deletes the table's bytes: a window that
# becomes empty was all printable ASCII (text, never a key).
_PRINTABLE = bytes(range(0x20, 0x7F))
_ZERO16 = bytes(16)
_FF16 = b"\xff" * 16


def archive_plaintext(pt):
    """True when block 0 of a decrypted dat archive looks right."""
    n = pt[8] | (pt[9] << 8)
    return pt[10:14] == b"dat\\" and 4 <= n < 256


def pe_ranges(dll):
    """Split the file into (preferred, rest) lists of (start, end) raw ranges.

    preferred = the raw data of the .rdata and .data sections, in that order;
    rest = everything else in the file. A file that is not a PE, or has no
    such sections, comes back as ([], [(0, len)]).
    """
    n = len(dll)
    preferred = []
    try:
        if dll[:2] == b"MZ":
            pe = struct.unpack_from("<I", dll, 0x3C)[0]
            if dll[pe:pe + 4] == b"PE\0\0":
                nsec = struct.unpack_from("<H", dll, pe + 6)[0]
                opt = struct.unpack_from("<H", dll, pe + 20)[0]
                base = pe + 24 + opt
                secs = {}
                for i in range(nsec):
                    name, _vs, _va, raw_size, raw_off = struct.unpack_from(
                        "<8sIIII", dll, base + i * 40)
                    name = name.rstrip(b"\0")
                    if raw_size and raw_off < n:
                        secs[name] = (raw_off, min(raw_off + raw_size, n))
                preferred = [secs[s] for s in PREFERRED_SECTIONS if s in secs]
    except struct.error:
        preferred = []
    rest = []
    pos = 0
    for s, e in sorted(preferred):
        if s > pos:
            rest.append((pos, s))
        pos = max(pos, e)
    if pos < n:
        rest.append((pos, n))
    return preferred, rest


def candidate_offsets(ranges):
    """Yield window offsets over the ranges: 16-byte aligned first, then the
    8-byte ones not yet tried, then the remaining 4-byte ones."""
    for align in (16, 8, 4):
        coarser = align * 2
        for s, e in ranges:
            start = (s + align - 1) // align * align
            for off in range(start, e - 15, align):
                if align != 16 and off % coarser == 0:
                    continue
                yield off


def scan(dll, sample, magic=None, progress=None):
    """Find the key that decrypts the sample's first block.

    dll: the client DLL bytes. sample: an FE00EN00 container (dat.pak by
    default; pass magic=b"..." to instead require the plaintext block to
    start with those bytes, e.g. b"BM" for a .bmp). progress: optional
    callable(tried, elapsed_seconds), called every 20000 candidates.

    Returns (key, offset, stats) with stats = {"tried", "skipped",
    "seconds"}; key and offset are None when nothing matched.
    """
    if sample[:8] != MAGIC:
        raise ValueError("sample is not an FE00EN00 container")
    if len(sample) < 8 + BS:
        raise ValueError("sample is shorter than one 32-byte block")
    c0 = struct.unpack(">8I", sample[8:8 + BS])
    if magic is None:
        accept = archive_plaintext
    else:
        accept = lambda pt: pt.startswith(magic)  # noqa: E731

    expand = fedecrypt._expand_key
    dec = fedecrypt._decrypt_block
    pack = struct.Struct(">8I").pack
    preferred, rest = pe_ranges(dll)
    tried = skipped = 0
    t0 = time.time()
    seen = set()
    for group in (preferred, rest):
        for off in candidate_offsets(group):
            w = dll[off:off + 16]
            if (w == _ZERO16 or w == _FF16 or w in seen
                    or not w.translate(None, _PRINTABLE)):
                skipped += 1
                continue
            seen.add(w)
            tried += 1
            rk_first, rk_mid, rk_last = expand(w)
            if accept(pack(*dec(c0, rk_first, rk_mid, rk_last))):
                return w, off, {"tried": tried, "skipped": skipped,
                                "seconds": time.time() - t0}
            if progress and tried % 20000 == 0:
                progress(tried, time.time() - t0)
    return None, None, {"tried": tried, "skipped": skipped,
                        "seconds": time.time() - t0}


def find_key(dll_bytes, sample_file_bytes, magic=None):
    """The 16-byte key inside dll_bytes that decrypts sample_file_bytes, or
    None if no aligned window of the DLL does."""
    return scan(dll_bytes, sample_file_bytes, magic)[0]


def main():
    ap = argparse.ArgumentParser(
        description="Recover an FE00EN00 file key from your FE_Client.dll")
    ap.add_argument("--dll", required=True, help="FE_Client.dll or FE_Client.en.dll")
    ap.add_argument("--sample", required=True,
                    help="an encrypted container the key must open (dat.pak)")
    ap.add_argument("--magic", default=None,
                    help="expected plaintext prefix (text) for samples other "
                         "than a dat archive, e.g. BM for a .bmp")
    ap.add_argument("--quiet", action="store_true", help="no progress lines")
    a = ap.parse_args()
    with open(a.dll, "rb") as f:
        dll = f.read()
    with open(a.sample, "rb") as f:
        sample = f.read()
    magic = a.magic.encode("latin-1") if a.magic else None

    def progress(tried, secs):
        sys.stderr.write("fekeys: %d candidates, %.0f/s\n"
                         % (tried, tried / max(secs, 1e-9)))

    key, off, st = scan(dll, sample, magic, None if a.quiet else progress)
    rate = st["tried"] / max(st["seconds"], 1e-9)
    if key is None:
        print("no key found in %s (%d candidates tried, %d skipped, %.1fs, %.0f/s)"
              % (a.dll, st["tried"], st["skipped"], st["seconds"], rate))
        sys.exit(1)
    print("key=%s offset=0x%x (%d candidates tried, %d skipped, %.1fs, %.0f/s)"
          % (key.hex(), off, st["tried"], st["skipped"], st["seconds"], rate))


if __name__ == "__main__":
    main()
