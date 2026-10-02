#!/usr/bin/env bash
#
# Make sure the Ignition database and its role exist, whatever state Postgres
# is in.
#
#   scripts/pg-ensure.sh
#
# WHY THIS EXISTS ALONGSIDE initdb/01-ignition.sh
#
# Scripts in /docker-entrypoint-initdb.d run ONLY on a first start against an
# empty data directory. That is a one-shot window, and anything that makes
# Postgres miss it leaves a container that starts, reports healthy, and has none
# of the databases -- with no error anywhere. It is only noticed later, when the
# gateway's database connection is Faulted and you go looking at the gateway.
#
# That window was missed for real: the init directory used to arrive as a
# relative bind mount (`./initdb`), which Compose resolves against the client's
# filesystem. Run from the toolbox that path does not exist on the host, and
# Docker silently substitutes an empty directory rather than failing. Postgres
# came up with no init scripts at all. The mount is a seeded named volume now
# (see scripts/lib.sh:seed_volume), but a volume that has already been through
# that will never re-run initdb -- so the databases have to be creatable after
# the fact as well.
#
# Idempotent, and deliberately so: this runs on every bootstrap, and bootstrap
# is meant to be able to REPAIR a half-built stack rather than only build a
# clean one.
#
# The password comes from stacks/postgres/.env, is passed to psql through the
# environment, and is never echoed or placed in argv (CLAUDE.md rule 2).
# `\gexec` is what makes CREATE DATABASE conditional: it cannot run inside a
# DO block or a transaction, so the usual IF NOT EXISTS pattern is unavailable
# and the query has to generate the statement and then execute it.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PG_ENV="$STACKS_DIR/postgres/.env"

need_docker
gateway_running postgres || die "the postgres stack is not running"
[ -f "$PG_ENV" ] || die "no stacks/postgres/.env -- run: make env"

read_env() { grep -E "^$1=" "$PG_ENV" | head -1 | cut -d= -f2-; }

SUPER_USER="$(read_env POSTGRES_USER)";       SUPER_USER="${SUPER_USER:-postgres}"
SUPER_DB="$(read_env POSTGRES_DB)";           SUPER_DB="${SUPER_DB:-postgres}"
DB_NAME="$(read_env IGNITION_DB_NAME)";       DB_NAME="${DB_NAME:-ignition}"
DB_USER="$(read_env IGNITION_DB_USER)";       DB_USER="${DB_USER:-ignition}"

if docker exec postgres psql -U "$SUPER_USER" -d "$SUPER_DB" -tAc \
     "select 1 from pg_database where datname='$DB_NAME'" 2>/dev/null | grep -q 1; then
  ok "database '$DB_NAME' already exists"
  exit 0
fi

say "creating database '$DB_NAME' and role '$DB_USER'"

# The password reaches psql on STDIN and nowhere else -- not in argv, which
# every process on the box can read, and not in an environment variable, which
# `docker inspect` prints. make-env.sh generates alphanumeric passwords only, so
# embedding one in a SQL literal here cannot break the quoting; if that ever
# changes, this is the line that has to change with it.
#
# \gexec rather than IF NOT EXISTS: CREATE DATABASE cannot run inside a DO block
# or a transaction, so the conditional has to be a query that GENERATES the
# statement, which \gexec then executes.
{
  printf "SELECT format('CREATE ROLE %%I WITH LOGIN PASSWORD %%L', '%s', '%s')\n" \
         "$DB_USER" "$(read_env IGNITION_DB_PASSWORD)"
  printf " WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '%s')\n" "$DB_USER"
  printf "\\\\gexec\n"
  printf "SELECT format('CREATE DATABASE %%I OWNER %%I', '%s', '%s')\n" "$DB_NAME" "$DB_USER"
  printf " WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '%s')\n" "$DB_NAME"
  printf "\\\\gexec\n"
} | docker exec -i postgres \
      psql -v ON_ERROR_STOP=1 -U "$SUPER_USER" -d "$SUPER_DB" >/dev/null

# Postgres 15+ revoked the implicit CREATE grant on the public schema, so the
# owner must be granted it explicitly or Ignition cannot create its tables --
# and the failure surfaces as a historian that connects and then cannot write.
docker exec -i postgres psql -v ON_ERROR_STOP=1 -U "$SUPER_USER" -d "$DB_NAME" <<SQL
GRANT ALL ON SCHEMA public TO "$DB_USER";
ALTER SCHEMA public OWNER TO "$DB_USER";
SQL

ok "database '$DB_NAME' is ready, owned by '$DB_USER'"
