# CrystalRing

A server reimplementation for Square Enix's Fantasy Earth (2006, the original
PlayOnline-era service). Together with the core lobby stack it lets an
unmodified client log in, walk the capital and fields, fight, level, trade,
party, and see other players, with no connection to Square Enix.

This project is a clean-room reimplementation based on protocol observation.
It contains no Square Enix code, art, or data: the game data the server needs
is extracted from YOUR OWN client install by the tools in this repository.

## Prerequisites

- The core lobby stack (openlobby) checked out beside this repository
  (`../openlobby`) and running on the same Docker host. The Fantasy Earth
  services join its compose project and keep their characters and mail in
  its PostgreSQL database.
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

# 3. the services, added to the core's compose project:
#    set FE_ADVERTISE to your server's LAN/VPN IP in ../openlobby/.env
docker compose --project-directory ../openlobby     -f ../openlobby/docker-compose.yml -f docker-compose.yml up -d --build
```

The image is built on the core image (`openlobby:latest`, or the image named
by `OPENLOBBY_IMAGE`), so build the core first.

Without building: the image is published to
`ghcr.io/prettyopenlobby/crystalring` on every push (it carries the cipher
tables, so step 1 is not needed); step 2 still runs on the host, and the
override mounts your `services/fedata/` into the containers:

```
docker compose --project-directory ../openlobby -f ../openlobby/docker-compose.yml     -f docker-compose.yml -f docker-compose.ghcr.yml up -d
```

Step 2 writes `services/fedata/` (spawn tables, item and skill parameters,
map geometry, minimap art). Only two files in that directory ship with the
repository, because they are original work: `fe-drops.tsv` (a drop table
reconstructed from 2006 community records; SE's server-side original was
never public) and `fe-area-names-en.tsv` (English renderings of the area
names).

## Where the data lives

Characters (`services/festore.py`) and in-game mail (`services/femail.py`)
are tables in the core's PostgreSQL database, `fe_character` and `fe_mail`,
reached through the core's `polcore` package with `POL_DATABASE_URL`. The
compose file sets it for every Fantasy Earth service. Their schema is
CrystalRing's own, in `services/fe_migrations/`, and is applied when a service
first touches the database, or by hand:

```
docker compose --project-directory ../openlobby -f ../openlobby/docker-compose.yml     -f docker-compose.yml exec feworld python fedb.py migrate
```

These files share the core's `schema_migrations` table, which records a
migration by its number alone, so CrystalRing numbers its files from 1001 and
the core keeps 0001 to 0999. Every CrystalRing table starts with `fe_`.

A server set up before PostgreSQL has its characters in `/data/fe.db` and its
mail in `/data/fe_mail.db`. They are not read any more and have to be imported
into the database once. The old `fe_characters.json` is not imported
automatically while an `fe.db` sits beside it, because that JSON file stopped
being written when `fe.db` took over and would bring back characters as they
were then. Setting `FE_DB=` (empty) keeps the characters in
`fe_characters.json` instead of the database.

The world, campaign, spawn and town files in `/data` (`fe_territory.json`,
`fe_campaign.json`, `fe_spawn.json`, `fe_town.json`) are unchanged.

## The title plugin (the Viewer's profile)

The core builds the profile the Viewer shows for a Fantasy Earth Content ID from
data only this title holds, so a small plugin runs inside the core's `login`
and `authsess` processes (OpenLobby's `services/titles.py`, `POL_TITLES`).
`Dockerfile.title` layers it on the core image and `docker-compose.title.yml`
swaps that image into those two services. From this directory, with the core
checked out beside it:

```
docker compose --project-directory ../openlobby     -f ../openlobby/docker-compose.yml -f docker-compose.title.yml     up -d --build login authsess
```

Without it the game plays the same; only the Viewer's profile screen for a Fantasy Earth Content ID stays empty. The plugin reads the player database, the core's own PostgreSQL. To run several titles, build each title image on the previous
one (`OPENLOBBY_IMAGE`) and list them all in `POL_TITLES` in OpenLobby's
`.env`, for example `POL_TITLES=tmtitle,fetitle`.

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
step 2 has been run. The suites that touch the database need the core checked
out beside this repository (or `OPENLOBBY_DIR` pointing at it), the Python
drivers (`pip install "psycopg[binary]" psycopg-pool valkey`) and Docker: each
suite gets its own empty database on a throwaway PostgreSQL container, which is
removed at the end. Without Docker, `POL_TEST_DATABASE_URL` names a server the
suites may create databases on; with neither, those suites report SKIP. CONTRIBUTING.md lists where each part of the server
lives and how to run the checks.

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
