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

The world state the game rewrites while it runs is in the same database
(`services/festate.py`). Each store is one row of `fe_world_state`, holding
the document its file used to hold:

| Row | Was | What it holds |
| --- | --- | --- |
| `campaign` | `fe_campaign.json` | the war cycle per area and each nation's record |
| `territory` | `fe_territory.json` | which nation holds each area |
| `spawn` | `fe_spawn.json` | arrival points per area (`!spawn`), seeded from `fedata/fe_spawn.json` on the first start |
| `town` | `fe_town.json` | placed NPCs, buildings and doors (`!npc`, `!build`, `!door`) |
| `door_arrive` | `fe_door_arrive.json` | where a door puts a player down |
| `minimap_cal` | `fe_minimap_cal.json` | the minimap calibration anchors |
| `force` | `fe_force.json` | force rows and the judge policy (`!force`) |
| `map_discord` | `fe_map_discord.json` | the Discord map message being edited |

`python festate.py list` and `python festate.py show NAME` print them. The
record of the 0xC007 credential blobs that `tools/fe_blob_verdict.py` reads
is the `fe_blob_observation` table (it was `fe_blobid.jsonl`).

Each of `--campaign-file`, `--territory-file`, `--spawn-file`, `--town-file`,
`--force-file` and `--map-discord-state` still takes a path, and then that
store is kept in the file instead, which is meant for tests and one-off runs.
An empty value (or `os.devnull`) keeps the store in memory only, and
`--town-file ""` still turns the town off.

Two things stay outside the database. `--gmcmd-file` (`/data/fe_gmcmd.txt`)
is the operator's command inbox and stays a file. The small map snapshot for
the board bot (polboards `boardfe`) is live state: with `--map-board on`
(env `FE_MAP_BOARD=on`) the world publishes it in Valkey under the key
`fe:map:board`, behind `POL_KV_PREFIX` (so `pol:fe:map:board` by default),
every few seconds, and the key expires `FE_MAP_BOARD_TTL` seconds (default 60)
after the last write. `FE_MAP_BOARD_FILE` still writes the old file as well,
for a bot that has not moved to the key yet.

## Moving an existing /data

A server set up before PostgreSQL has its characters in `/data/fe.db`, its
mail in `/data/fe_mail.db` and its world state in JSON files beside them.
None of them is read any more. Import them once, after the core's own import
(`tools/db_import.py` in OpenLobby) and before the Fantasy Earth services
start for the first time. Run them in a container that sees the old volume,
for example:

```
docker compose --project-directory ../openlobby -f ../openlobby/docker-compose.yml     -f docker-compose.yml run --rm --no-deps --entrypoint python feworld fedb.py import fe_db /data/fe.db --dry-run
```

Try each one with `--dry-run` first, which reads everything, writes nothing
and prints what the real run would do. The order:

1. `python fedb.py import fe_db /data/fe.db` for the characters. A server
   that never had an `fe.db` imports `fe_characters.json` instead, with
   `python festore.py --import /data/fe_characters.json`. Do not import that
   JSON file when an `fe.db` exists: it stopped being written when `fe.db`
   took over and would bring back characters as they were then, which is
   why felobby skips its automatic import of it while an `fe.db` sits
   beside it.
2. `python fedb.py import fe_mail_db /data/fe_mail.db` for the mail.
3. `python fedb.py import world /data` for the world state files listed
   above and `fe_blobid.jsonl`. Run it before feworld first starts, or the
   spawn row is already seeded from the shipped file and the import needs
   `--merge`.

Every import opens its source read-only and never writes to it. It runs in
one transaction, prints what it counted, and lists each row it could not
carry over and why. It refuses (exit 2) to write into a table that already
holds rows unless `--merge` is given, and `--merge` adds only the keys that
are not there yet, keeping the database's version of the rest and listing
them. A second run finds nothing missing and changes nothing. Mail keeps its
ids, and new mail is numbered above every id the old server handed out.

Setting `FE_DB=` (empty) keeps the characters in `fe_characters.json` instead
of the database.

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
