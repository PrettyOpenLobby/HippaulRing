#!/usr/bin/env python3
"""Build services/fedata/ from your own Fantasy Earth client install.

One command runs the whole extraction pipeline:

  python tools/fedata_build.py --client "C:/path/to/FantasyEarth"

Steps (each continues on failure; the summary says what was produced):
  1. decrypt   DATA/dat.pak -> a plaintext archive (skipped if already plain).
               The file key is recovered from your FE_Client.dll (or
               FE_Client.en.dll) by tools/fedatagen/fekeys.py and cached in
               <out>/.cache/dat.key; no key ships with the repository.
  2. fefet     the fet_* gameplay tables -> fe-fet-*.tsv (+ map heights)
  3. fenpc     NPC model movement speeds -> fe-npc-model-speeds.tsv
  4. feoct     capital collision .oct -> fe-capital-ground.json
  5. fetex     minimap textures -> services/fedata/minimaps/*.png
  6. feicons   shop icon positions off the minimaps -> fe-shop-icons.tsv
  7. femapfit  minimap pixel<->world calibration -> fe-minimap-cal.tsv
  8. mapart    world/continent select art -> services/fedata/mapart/

The armour-defence table (fe-armour-defence.tsv) is built from archived 2006
community records rather than the client; see tools/fe_armour_defence_build.py.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DEFAULT = os.path.normpath(os.path.join(HERE, "..", "services", "fedata"))
PY = sys.executable
CLIENT_DLLS = ("FE_Client.dll", "FE_Client.en.dll")
results = []


def find_client_dll(client):
    """The client DLL: at the top level of the install first, then anywhere
    below it. None when there is no such file."""
    for name in CLIENT_DLLS:
        p = os.path.join(client, name)
        if os.path.isfile(p):
            return p
    lower = {n.lower() for n in CLIENT_DLLS}
    for dirpath, _dirs, files in os.walk(client):
        for fn in files:
            if fn.lower() in lower:
                return os.path.join(dirpath, fn)
    return None


def recover_dat_key(client, pak, cache, dll=None):
    """The 16-byte key for dat.pak as hex, from <cache>/dat.key when a
    previous run recovered it, else by scanning the client DLL with fekeys.
    Returns None (after printing why) when no key can be had."""
    keyfile = os.path.join(cache, "dat.key")
    if os.path.isfile(keyfile):
        with open(keyfile, encoding="ascii") as fh:
            key = fh.read().strip()
        if len(key) == 32:
            print("    key: cached in", keyfile)
            return key
    dll = dll or find_client_dll(client)
    if not dll:
        print("    decrypt: no %s or %s under %s - the file key is recovered "
              "from your client's DLL; pass --dll if yours is installed "
              "elsewhere" % (CLIENT_DLLS[0], CLIENT_DLLS[1], client))
        return None
    sys.path.insert(0, os.path.join(HERE, "fedatagen"))
    import fekeys
    with open(dll, "rb") as fh:
        dll_bytes = fh.read()
    with open(pak, "rb") as fh:
        sample = fh.read()
    print("    key: scanning", dll)
    key, off, st = fekeys.scan(dll_bytes, sample)
    if key is None:
        print("    decrypt: no 16-byte window of %s decrypts %s (%d candidates, "
              "%.0fs)" % (dll, pak, st["tried"], st["seconds"]))
        return None
    print("    key: found at %s+0x%x (%d candidates, %.1fs)"
          % (os.path.basename(dll), off, st["tried"], st["seconds"]))
    with open(keyfile, "w", encoding="ascii") as fh:
        fh.write(key.hex() + "\n")
    return key.hex()


def step(name, argv, **kw):
    print(f"--- {name}: {' '.join(str(a) for a in argv[1:])}")
    try:
        r = subprocess.run(argv, timeout=3600, **kw)
        ok = r.returncode == 0
    except Exception as e:                       # noqa: BLE001
        print(f"    {name}: {e}")
        ok = False
    results.append((name, ok))
    print(f"    {name}: {'ok' if ok else 'FAILED (continuing)'}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True,
                    help="your Fantasy Earth install directory (contains DATA/)")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--dat", help="use an already-decrypted dat archive")
    ap.add_argument("--dll", help="your FE_Client.dll, when it is not under "
                                  "--client (the dat.pak key is recovered from it)")
    args = ap.parse_args()

    # Installed clients keep game data under DATA/; disc images and some
    # dumps are flat, with dat.pak and the .tex/.oct files at the top level.
    def locate(*rel):
        for base in (os.path.join(args.client, "DATA"), args.client):
            p = os.path.join(base, *rel)
            if os.path.exists(p):
                return p
        return None

    if not (locate("dat.pak") or args.dat):
        sys.exit(f"no dat.pak under {args.client} (or {args.client}/DATA) - "
                 "point --client at the install directory")
    os.makedirs(args.out, exist_ok=True)
    cache = os.path.join(args.out, ".cache")
    os.makedirs(cache, exist_ok=True)

    # 1. the dat archive, decrypted
    dat = args.dat
    if not dat:
        pak = locate("dat.pak")
        dat = os.path.join(cache, "dat.dec")
        with open(pak, "rb") as fh:
            magic = fh.read(8)
        if magic == b"FE00EN00":
            if not os.path.exists(dat) or os.path.getmtime(dat) < os.path.getmtime(pak):
                print("--- decrypt:", pak)
                key = recover_dat_key(args.client, pak, cache, args.dll)
                if key is None:
                    results.append(("decrypt", False))
                    print("    decrypt: FAILED (continuing)")
                elif not step("decrypt", [PY, os.path.join(HERE, "fedatagen",
                              "fedecrypt.py"), pak, dat, "--key", key]):
                    key = None
                if key is None:
                    sys.exit("cannot continue without a decrypted dat archive")
            else:
                print("--- decrypt: cached", dat)
        else:
            dat = pak
            print("--- decrypt: dat.pak is already plaintext")

    gen = os.path.join(HERE, "fedatagen")
    step("fefet", [PY, os.path.join(gen, "fefet.py"),
                   "--dat", dat, "--out", args.out])
    rich = os.path.join(cache, "npc-modeltypes-full.tsv")
    if step("fenpc", [PY, os.path.join(gen, "fenpc.py"), "--pak", dat,
                      "--tsv", rich]):
        # the server reads the 4-column subset, model path as the client
        # spells it
        with open(rich, encoding="utf-8") as fh:
            rows = [l.rstrip("\n").split("\t") for l in fh]
        head = rows[0]
        gi = {c: head.index(c) for c in ("id", "model", "walk_ups", "run_ups")}
        with open(os.path.join(args.out, "fe-npc-model-speeds.tsv"),
                  "w", encoding="utf-8", newline="") as fh:
            fh.write("modeltype\tmodel\twalk_ups\trun_ups\n")
            for r in rows[1:]:
                fh.write("%s\tModel\\%s\t%s\t%s\n" %
                         (r[gi["id"]], r[gi["model"]],
                          r[gi["walk_ups"]], r[gi["run_ups"]]))
    oct_dir = locate("capital") or (
        args.client if glob.glob(os.path.join(args.client, "*_hit.oct"))
        else os.path.join(args.client, "DATA"))
    step("feoct", [PY, os.path.join(gen, "feoct.py"),
                   "--data", oct_dir,
                   "--grid", os.path.join(args.out, "fe-capital-ground.json")])

    tex = sorted(glob.glob(os.path.join(args.client, "DATA", "Window", "MAP",
                                        "map0*_mini.tex"))
                 or glob.glob(os.path.join(args.client, "map0*_mini.tex")))
    if tex and step("fetex", [PY, os.path.join(gen, "fetex.py"), "extract",
                              *tex, "--out", cache, "--png"]):
        mm = os.path.join(args.out, "minimaps")
        os.makedirs(mm, exist_ok=True)
        for p in glob.glob(os.path.join(cache, "map0*_mini*.png")):
            base = os.path.basename(p).replace("_mini", "")
            shutil.copyfile(p, os.path.join(mm, base))
    step("feicons", [PY, os.path.join(gen, "feicons.py"), "--maps", cache,
                     "--out", os.path.join(args.out, "fe-shop-icons.tsv")])
    step("femapfit", [PY, os.path.join(gen, "femapfit.py"),
                      "--icons", os.path.join(args.out, "fe-shop-icons.tsv"),
                      "--out", os.path.join(args.out, "fe-minimap-cal.tsv")])
    step("mapart", [PY, os.path.join(HERE, "fe_mapart_build.py"),
                    "--install", args.client])

    print()
    for name, ok in results:
        print(f"  {name:10} {'ok' if ok else 'FAILED'}")
    expected = ["fe-fet-area.tsv", "fe-fet-items.tsv", "fe-fet-skill.tsv",
                "fe-capital-ground.json", "fe-npc-model-speeds.tsv",
                "fe-shop-icons.tsv", "fe-minimap-cal.tsv"]
    missing = [f for f in expected if not os.path.exists(os.path.join(args.out, f))]
    if missing:
        print("still missing:", ", ".join(missing))
        sys.exit(1)
    print("fedata complete")


if __name__ == "__main__":
    main()
