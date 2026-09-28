# One image for the Fantasy Earth services; the entrypoint is chosen per
# service in docker-compose.yml.
#
# It is built on the OpenLobby core image. The characters and the in-game mail
# live in the stack's PostgreSQL database through the core's polcore package
# (see fedb.py), and the core image carries that package and its database
# drivers. Build the core first, or point OPENLOBBY_IMAGE at the image you use.
# The Python base and its digest pin are the core's.
ARG OPENLOBBY_IMAGE=openlobby:latest
FROM ${OPENLOBBY_IMAGE}

WORKDIR /app
COPY . /app/

# Refuse to build an image without the generated cipher tables and game data
# (see README: run tools/gen_blowfish_tables.py and tools/fedata_build.py
# before building), or on a core image that has no polcore.
RUN python - <<'EOF'
import os, sys
missing = [p for p in ("fe_blowfish_P.bin", "fe_blowfish_S.bin") if not os.path.exists(p)]
if missing:
    sys.exit("missing generated tables: %s - run tools/gen_blowfish_tables.py first" % ", ".join(missing))
try:
    import fedb
except ImportError as exc:
    sys.exit("the base image has no polcore (%s) - build it from the OpenLobby core image" % exc)
if not os.path.exists("fedata/fe-fet-area.tsv"):
    print("NOTE: fedata/ is not populated; run tools/fedata_build.py against "
          "your client install for a playable world", file=sys.stderr)
EOF

ENTRYPOINT ["python", "feworld.py"]
