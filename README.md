# CrystalRing

A server reimplementation for Square Enix's Fantasy Earth (2006, the original
PlayOnline-era service). Together with the core lobby stack it lets an
unmodified client log in, walk the capital and fields, fight, level, trade,
party, and see other players, with no connection to Square Enix.

This project is a clean-room reimplementation based on protocol observation.
It contains no Square Enix code, art, or data: the game data the server needs
is extracted from YOUR OWN client install by the tools in this repository.

## Prerequisites

- The core lobby stack (openlobby) running on the same Docker host
- A Fantasy Earth client install of your own
- Docker with Compose v2, and Python 3.10+ with Pillow on the host for the
  two generation steps (`apt install python3-pil` / `pip install pillow`;
  the art extraction steps need it)

## Bring-up

```
# 1. cipher tables, computed from public constants (no client needed):
python tools/gen_blowfish_tables.py

# 2. game data, extracted from YOUR client install:
python tools/fedata_build.py --client "C:\path\to\FantasyEarth"

# 3. the services:
cp .env.example .env      # set FE_ADVERTISE to your server's LAN/VPN IP
docker compose up -d --build
```

Step 2 writes `services/fedata/` (spawn tables, item and skill parameters,
map geometry, minimap art). Only two files in that directory ship with the
repository, because they are original work: `fe-drops.tsv` (a drop table
reconstructed from 2006 community records; SE's server-side original was
never public) and `fe-area-names-en.tsv` (English renderings of the area
names).

## Pointing a client at it

The client discovers this stack through the core lobby: content id 11 in the
games menu hands the client to `fellb` (port 54848), which balances onto
`felobby`/`feworld`. Nothing needs to be configured client-side beyond the
DNS/hosts redirection already done for the core stack.

## Selftests

```
python tools/fe_run_all.py
```

runs the offline suite. Suites that need generated fedata are skipped until
step 2 has been run.

## What is not included, and why

- No Square Enix game data: `services/fedata/` is generated from your own
  client, not shipped.
- The cipher tables are generated from pi (the client's tables are the
  public Blowfish constants under a trivial byte transform; see
  `tools/gen_blowfish_tables.py`).

## License

AGPL-3.0 (see LICENSE).

## Credits

- The 2006 Fantasy Earth community wikis, whose records made the drop and
  equipment reconstructions possible.
- The PlayOnline preservation community.
