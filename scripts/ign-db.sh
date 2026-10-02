#!/usr/bin/env bash
#
# Give the hub its Postgres database connection.
#
#   scripts/ign-db.sh [gateway]
#
# Idempotent: ign-gw.js db-create checks the connection list first and returns
# without touching anything if the name is already there.
#
# WHY THIS IS A SCRIPT AND NOT TWO LINES IN THE DOCS
#
# A database connection holds a PASSWORD, and there is no file-based way to
# write one: the gateway's SecretConfig accepts `Embedded` (a JWE encrypted with
# the gateway's own key) or `Referenced`, so a hand-written config.json can
# never carry a usable credential. The connection has to be created by the
# gateway itself, which is what ign-gw.js does through the UI.
#
# That leaves the password to get from stacks/postgres/.env to the gateway
# without going through a terminal, a chat transcript or argv -- argv being
# world-readable on the box (CLAUDE.md rule 2). It travels in the environment of
# exactly one process and is never echoed.
#
# The JDBC URL uses the CONTAINER NAME. `postgres:5432` resolves on the backbone
# network from every gateway; the host mapping is bound to 127.0.0.1 and is for
# a desktop client on this machine only. A connection pointed at localhost would
# be a gateway trying to reach a database inside its own container.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

GATEWAY="${1:-ignition}"
NAME="Postgres"
PG_ENV="$STACKS_DIR/postgres/.env"

need_docker
require_gateway "$GATEWAY"
[ -f "$PG_ENV" ] || die "no stacks/postgres/.env -- run: make env"

DB_NAME="$(grep -E '^IGNITION_DB_NAME=' "$PG_ENV" | head -1 | cut -d= -f2-)"
DB_USER="$(grep -E '^IGNITION_DB_USER=' "$PG_ENV" | head -1 | cut -d= -f2-)"
DB_NAME="${DB_NAME:-ignition}"
DB_USER="${DB_USER:-ignition}"

say "creating the '$NAME' database connection on $GATEWAY"

# The password never lands in a variable this script echoes, and never in argv.
DB_PASSWORD="$(grep -E '^IGNITION_DB_PASSWORD=' "$PG_ENV" | head -1 | cut -d= -f2-)" \
  node "$REPO_ROOT/scripts/ign-gw.js" db-create \
    --gateway "$(stanza_for "$GATEWAY")" \
    --name "$NAME" \
    --driver PostgreSQL \
    --username "$DB_USER" \
    --url "jdbc:postgresql://postgres:5432/$DB_NAME" \
  || die "the gateway refused the database connection"

# Verify that the connection WORKS, not merely that it exists.
#
# The resource list is the wrong thing to check and this script used to check
# it: a connection whose database does not exist, or whose password is wrong,
# appears there looking exactly like a healthy one. On the first from-scratch
# run on another machine that is precisely what happened -- the database had
# never been created, this step reported success, and the failure only surfaced
# three steps later when the historian could not write.
#
# `/data/status/databases` is the gateway's own Databases status page data, and
# it carries the runtime state: {"name":"Postgres","status":"Valid",...}. Note
# the mount -- it is NOT under /data/api/v1/, which 404s and reads like a wrong
# guess at the route.
say "waiting for the connection to report Valid"
for _ in $(seq 1 20); do
  status="$(node "$REPO_ROOT/scripts/ign-gw.js" api --gateway "$(stanza_for "$GATEWAY")" \
    --path "/data/status/databases" 2>/dev/null | tail -1)"

  state="$(printf '%s' "$status" | python3 -c '
import json, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    raise SystemExit
name = sys.argv[1]
for row in rows:
    if row.get("name") == name:
        print(row.get("status", "?"))
        break
' "$NAME" 2>/dev/null)"

  case "$state" in
    Valid) ok "'$NAME' is connected on $GATEWAY"; exit 0 ;;
    "")    : ;;   # not registered yet
    *)     dim "  status: $state" ;;
  esac
  sleep 3
done

die "'$NAME' never reached Valid on $GATEWAY (last status: ${state:-not registered}).
     A connection that exists but is Faulted usually means the database or the
     role is missing -- run: scripts/pg-ensure.sh"
