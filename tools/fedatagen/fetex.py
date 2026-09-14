"""Fantasy Earth `.tex` (IMG0) container: list / extract / replace.

Format (proved 2026-09-09 against DATA/Window/*.tex):
    0x00  "IMG0"
    0x04  u32 total          = filesize - 16 (a few files pad to 16 B alignment)
    0x08  u32 count
    0x0C  u32 0
  then `count` entries, each:
    +0x00 u32 size           = length of the payload
    +0x04 char[12] type      = "tex"
    +0x10 char[32] name      = texture name (cp932)
    +0x30 payload            = a headerless-ID TGA, image type 1
                               (uncompressed colour-mapped), 256-entry
                               palette at 24 or 32 bpp, 8 bpp pixels,
                               bottom-up (descriptor 0x00).

The payload is a *complete* TGA file, so it round-trips through any image
editor.  `replace` keeps every other entry byte-identical and rewrites the
container's size fields, so a swapped image may change size.  Some files pad
to a 16-byte boundary after the last entry; `parse` hands that trailer back
and `build` writes it again, so an untouched file rebuilds byte-for-byte.
"""
import argparse, io, os, struct, sys

MAGIC = b"IMG0"
EHDR = 0x30


def parse(data):
    """-> (entries, trailer). See `build` for the round trip."""
    if data[:4] != MAGIC:
        raise ValueError("not an IMG0 container")
    total, count, zero = struct.unpack_from("<III", data, 4)
    out, off = [], 16
    for i in range(count):
        (size,) = struct.unpack_from("<I", data, off)
        typ = data[off + 4:off + 16].split(b"\0")[0].decode("cp932", "replace")
        name = data[off + 16:off + 48].split(b"\0")[0].decode("cp932", "replace")
        body = data[off + EHDR:off + EHDR + size]
        if len(body) != size:
            raise ValueError(f"entry {i} truncated at {off:#x}")
        out.append({"i": i, "off": off, "size": size, "type": typ,
                    "name": name, "raw": data[off + 16:off + 48], "body": body})
        off += EHDR + size
    return out, data[off:]


def build(entries, tail=b""):
    buf = io.BytesIO()
    buf.write(MAGIC + b"\0" * 12)
    for e in entries:
        buf.write(struct.pack("<I", len(e["body"])))
        buf.write(e["type"].encode("cp932").ljust(12, b"\0"))
        buf.write(e["raw"].ljust(32, b"\0")[:32])
        buf.write(e["body"])
    out = bytearray(buf.getvalue())
    struct.pack_into("<III", out, 4, len(out) - 16, len(entries), 0)
    assert out[:4] == MAGIC
    return bytes(out) + tail


def tga_info(body):
    idl, cmt, it = body[0], body[1], body[2]
    cmo, cml = struct.unpack_from("<HH", body, 3)
    cme = body[7]
    xo, yo, w, h = struct.unpack_from("<HHHH", body, 8)
    return {"type": it, "cmap": cml, "cmap_bits": cme,
            "w": w, "h": h, "bpp": body[16], "desc": body[17]}


def to_png(body):
    """Decode the 8 bpp colour-mapped TGA to an RGBA Pillow image.

    Pillow refuses a 32-bit colour map, so unpack it by hand: the map is
    B,G,R[,A] per entry and the pixel rows are stored bottom-up.
    """
    from PIL import Image
    t = tga_info(body)
    if t["type"] == 2 and t["bpp"] in (24, 32):
        n = t["bpp"] // 8
        px = body[18:18 + t["w"] * t["h"] * n]
        img = Image.frombytes("RGBA" if n == 4 else "RGB", (t["w"], t["h"]), px,
                              "raw", "BGRA" if n == 4 else "BGR").convert("RGBA")
        if not t["desc"] & 0x20:
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        return img
    if t["type"] != 1 or t["bpp"] != 8:
        raise ValueError(f"unsupported TGA type={t['type']} bpp={t['bpp']}")
    ent = t["cmap_bits"] // 8
    pal = body[18:18 + t["cmap"] * ent]
    px = body[18 + len(pal):18 + len(pal) + t["w"] * t["h"]]
    lut = []
    for i in range(t["cmap"]):
        c = pal[i * ent:(i + 1) * ent]
        lut.append((c[2], c[1], c[0], c[3] if ent == 4 else 255))
    img = Image.new("RGBA", (t["w"], t["h"]))
    img.putdata([lut[b] for b in px])
    if not t["desc"] & 0x20:
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
    return img


def safe(name):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name) or "unnamed"


def cmd_list(a):
    for path in a.files:
        data = open(path, "rb").read()
        ents, _ = parse(data)
        print(f"{path}  ({len(ents)} entries, {len(data)} B)")
        for e in ents:
            t = tga_info(e["body"])
            print(f"  [{e['i']:2d}] {e['name']:<24} {t['w']}x{t['h']} "
                  f"{t['bpp']}bpp pal={t['cmap']}x{t['cmap_bits']} size={e['size']}")


def cmd_extract(a):
    os.makedirs(a.out, exist_ok=True)
    for path in a.files:
        ents, _ = parse(open(path, "rb").read())
        stem = safe(os.path.splitext(os.path.basename(path))[0])
        for e in ents:
            base = f"{stem}__{e['i']:02d}__{safe(e['name'])}"
            tga = os.path.join(a.out, base + ".tga")
            open(tga, "wb").write(e["body"])
            if a.png:
                to_png(e["body"]).save(os.path.join(a.out, base + ".png"))
        print(f"{path}: {len(ents)} extracted")


def cmd_replace(a):
    data = open(a.file, "rb").read()
    ents, tail = parse(data)
    hit = [e for e in ents if e["name"] == a.name or str(e["i"]) == a.name]
    if len(hit) != 1:
        sys.exit(f"{a.name!r} matched {len(hit)} entries")
    body = open(a.tga, "rb").read()
    if body[:2] != b"\0\1" or body[2] != 1:
        sys.exit("replacement must be an uncompressed colour-mapped TGA "
                 "(id length 0, colour-map type 1, image type 1)")
    old, new = tga_info(hit[0]["body"]), tga_info(body)
    if (old["w"], old["h"]) != (new["w"], new["h"]):
        sys.exit(f"size mismatch: entry is {old['w']}x{old['h']}, "
                 f"replacement is {new['w']}x{new['h']}")
    if old["cmap_bits"] != new["cmap_bits"]:
        sys.exit(f"palette depth mismatch: {old['cmap_bits']} vs {new['cmap_bits']}")
    if old["desc"] != new["desc"]:
        sys.exit(f"image descriptor mismatch: {old['desc']:#x} vs {new['desc']:#x}")
    hit[0]["body"] = body
    out = a.out or a.file
    open(out, "wb").write(build(ents, tail))
    print(f"{out}: replaced {hit[0]['name']!r} ({old['size']} -> {len(body)} B)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("files", nargs="+"); p.set_defaults(f=cmd_list)
    p = sub.add_parser("extract"); p.add_argument("files", nargs="+")
    p.add_argument("--out", required=True); p.add_argument("--png", action="store_true")
    p.set_defaults(f=cmd_extract)
    p = sub.add_parser("replace"); p.add_argument("file"); p.add_argument("name")
    p.add_argument("tga"); p.add_argument("--out"); p.set_defaults(f=cmd_replace)
    a = ap.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
