# One image for the three Fantasy Earth services; the entrypoint is chosen
# per service in docker-compose.yml.
# Digest pinned 2026-09-13; bump deliberately, not by surprise.
FROM python:3.12-slim@sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79

RUN pip install --no-cache-dir pyyaml pycryptodome pillow

WORKDIR /app
COPY . /app/

# Refuse to build an image without the generated cipher tables and game data
# (see README: run tools/gen_blowfish_tables.py and tools/fedata_build.py
# before building).
RUN python - <<'EOF'
import os, sys
missing = [p for p in ("fe_blowfish_P.bin", "fe_blowfish_S.bin") if not os.path.exists(p)]
if missing:
    sys.exit("missing generated tables: %s - run tools/gen_blowfish_tables.py first" % ", ".join(missing))
if not os.path.exists("fedata/fe-fet-area.tsv"):
    print("NOTE: fedata/ is not populated; run tools/fedata_build.py against "
          "your client install for a playable world", file=sys.stderr)
EOF

ENTRYPOINT ["python", "feworld.py"]
