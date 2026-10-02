#!/usr/bin/env bash
#
# Make the hub historian's tables survive what the MQTT road sends them -- on
# every partition that exists now AND every one Ignition creates later.
#
#   scripts/pg-history-guard.sh            install / repair (idempotent)
#   scripts/pg-history-guard.sh --status   what is in place, changes nothing
#   scripts/pg-history-guard.sh --prove    also create a throwaway partition the
#                                          way Ignition does, as Ignition's role,
#                                          check it came out guarded, drop it
#
# TWO THINGS ABORT WHOLE HISTORY BATCHES, and both were measured
# (docs/MQTT-DISTRIBUTOR.md T-D11):
#
#   1. DUPLICATE ROWS. The edges' Rolling History Buffer replays the last 60 s
#      on every reconnect -- that is what makes a hub handover lossless -- and
#      the replay re-sends values the hub already stored. The SQL historian
#      inserts in batches, Postgres rejects the duplicate key, and the batch
#      goes with it, NEW rows included ("duplicate key value violates unique
#      constraint sqlt_data_1_2026_09_pkey" -> Error forwarding data). Cirrus
#      Link calls these errors benign; for a batching historian they are not.
#      Fix: a BEFORE INSERT row trigger that drops a row whose (tagid, t_stamp)
#      is already there. A trigger, not round 2's rule: a row trigger also
#      sees rows inserted earlier in the SAME statement, so a duplicate inside
#      one batch is dropped too.
#   2. LONG STRINGS. stringvalue is varchar(255), and the Sparkplug demo's
#      EdgeNotice JSON is longer: each one aborted its batch, other tags' rows
#      included (18 batches in 40 minutes). Fix: stringvalue is text.
#
# Ignition creates a new partition every month (sqlt_data_<driver>_<yyyy>_<mm>),
# and it creates it the way it always has: varchar(255), no trigger. So fixing
# today's table is a fix for three weeks. The permanent part is a Postgres
# EVENT TRIGGER on CREATE TABLE that applies both fixes to any new sqlt_data_*
# table in the same transaction Ignition created it in -- no cron, no gateway
# hook, nothing to run on the first of the month. It can never block Ignition:
# any error inside it becomes a WARNING and the CREATE TABLE goes ahead.
#
# Event triggers need a superuser, so this runs as `postgres` through docker
# exec. The functions it installs run as whoever issues the DDL -- Ignition's
# own role, which owns the new table and so may alter it.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PG_ENV="$STACKS_DIR/postgres/.env"
MODE=install
case "${1:-}" in
  --status) MODE=status ;;
  --prove)  MODE=prove ;;
  "") ;;
  *) die "usage: scripts/pg-history-guard.sh [--status|--prove]" ;;
esac

need_docker
gateway_running postgres || die "the postgres stack is not running"
read_env() { grep -E "^$1=" "$PG_ENV" 2>/dev/null | head -1 | cut -d= -f2- || true; }
SUPER_USER="$(read_env POSTGRES_USER)"; SUPER_USER="${SUPER_USER:-postgres}"
DB_NAME="$(read_env IGNITION_DB_NAME)"; DB_NAME="${DB_NAME:-ignition}"
DB_USER="$(read_env IGNITION_DB_USER)"; DB_USER="${DB_USER:-ignition}"

psql_su() { docker exec -i postgres psql -v ON_ERROR_STOP=1 -X -q -U "$SUPER_USER" -d "$DB_NAME" "$@"; }

status() {
  psql_su -tA -F'|' <<'SQL'
SELECT 'event-trigger', coalesce((SELECT evtenabled::text FROM pg_event_trigger
                                  WHERE evtname = 'wd_sqlt_partition_guard'), 'MISSING');
SELECT c.relname,
       (SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
         WHERE a.attrelid = c.oid AND a.attname = 'stringvalue'),
       CASE WHEN EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = c.oid
                           AND t.tgname = 'wd_skip_duplicate') THEN 'dedup' ELSE 'NO-DEDUP' END
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'r' AND n.nspname = 'public' AND c.relname ~ '^sqlt_data_[0-9]+_'
 ORDER BY c.relname;
SQL
}

if [ "$MODE" = status ]; then
  say "history guard on '$DB_NAME'"
  status | sed 's/^/  /'
  exit 0
fi

say "history guard on '$DB_NAME': duplicate-tolerant, text stringvalue, now and every new partition"
psql_su <<'SQL'
-- 1. drop a row whose (tagid, t_stamp) is already stored
CREATE OR REPLACE FUNCTION public.wd_sqlt_skip_duplicate() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE dup boolean;
BEGIN
  EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I.%I WHERE tagid = $1 AND t_stamp = $2)',
                 TG_TABLE_SCHEMA, TG_TABLE_NAME)
     INTO dup USING NEW.tagid, NEW.t_stamp;
  IF dup THEN
    RETURN NULL;          -- skip this row, keep the rest of the batch
  END IF;
  RETURN NEW;
END $fn$;

-- 2. apply both fixes to one table (idempotent)
CREATE OR REPLACE FUNCTION public.wd_sqlt_guard_table(t regclass) RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = t AND attname = 'stringvalue'
               AND atttypid <> 'text'::regtype) THEN
    -- varchar -> text is binary-compatible: no table rewrite
    EXECUTE format('ALTER TABLE %s ALTER COLUMN stringvalue TYPE text', t);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = t AND tgname = 'wd_skip_duplicate') THEN
    EXECUTE format('CREATE TRIGGER wd_skip_duplicate BEFORE INSERT ON %s '
                   'FOR EACH ROW EXECUTE FUNCTION public.wd_sqlt_skip_duplicate()', t);
  END IF;
END $fn$;

-- 3. the permanent part: every sqlt_data_* table created from now on
CREATE OR REPLACE FUNCTION public.wd_sqlt_partition_guard() RETURNS event_trigger
LANGUAGE plpgsql AS $fn$
DECLARE r record;
BEGIN
  FOR r IN SELECT objid FROM pg_event_trigger_ddl_commands()
            WHERE command_tag = 'CREATE TABLE' AND object_type = 'table' LOOP
    IF (SELECT relname FROM pg_class WHERE oid = r.objid) ~ '^sqlt_data_[0-9]+_' THEN
      BEGIN
        PERFORM public.wd_sqlt_guard_table(r.objid::regclass);
      EXCEPTION WHEN OTHERS THEN
        -- Never block Ignition's partition: an unguarded table is a degraded
        -- historian, a failed CREATE TABLE is no historian at all.
        RAISE WARNING 'wd_sqlt_partition_guard: % not guarded: %', r.objid::regclass, SQLERRM;
      END;
    END IF;
  END LOOP;
END $fn$;

DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_event_trigger WHERE evtname = 'wd_sqlt_partition_guard') THEN
    CREATE EVENT TRIGGER wd_sqlt_partition_guard ON ddl_command_end
      WHEN TAG IN ('CREATE TABLE')
      EXECUTE FUNCTION public.wd_sqlt_partition_guard();
  END IF;
END $do$;
ALTER EVENT TRIGGER wd_sqlt_partition_guard ENABLE;

-- 4. every partition that already exists
DO $do$
DECLARE t oid;
BEGIN
  FOR t IN SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r' AND n.nspname = 'public' AND c.relname ~ '^sqlt_data_[0-9]+_' LOOP
    PERFORM public.wd_sqlt_guard_table(t::regclass);
  END LOOP;
END $do$;
SQL

bad="$(status | grep -c 'MISSING\|NO-DEDUP\|character varying' || true)"
status | sed 's/^/  /'
[ "${bad:-0}" -eq 0 ] || die "the guard is not complete (above)"
ok "every sqlt_data partition is duplicate-tolerant with a text stringvalue, and new ones will be"

if [ "$MODE" = prove ]; then
  # NEXT MONTH's partition for the hub's own driver, created exactly as
  # Ignition creates one and AS Ignition's role -- the event trigger has to fire
  # for that role, not only for the superuser. Dropped at the end whatever
  # happens (a leftover would be the real partition's name on the 1st), and
  # never attempted if Ignition has already made it.
  T="sqlt_data_1_$(date -d "$(date +%Y-%m-01) +1 month" +%Y_%m)"
  if [ -n "$(docker exec postgres psql -X -tA -U "$SUPER_USER" -d "$DB_NAME" \
               -c "select to_regclass('public.$T')" 2>/dev/null | tr -d '[:space:]')" ]; then
    T=sqlt_data_999_2099_01
  fi
  say "proving it on a throwaway partition ($T) created as '$DB_USER'"
  out="$(docker exec -i postgres psql -X -q -tA -v ON_ERROR_STOP=1 -U "$SUPER_USER" -d "$DB_NAME" 2>&1 <<SQL || true
SET ROLE "$DB_USER";
CREATE TABLE $T (tagid integer NOT NULL, intvalue bigint, floatvalue double precision,
  stringvalue character varying(255), datevalue timestamp without time zone,
  dataintegrity integer, t_stamp bigint NOT NULL, PRIMARY KEY (tagid, t_stamp));
CREATE INDEX ${T}t_stampndx ON $T (t_stamp);
SELECT 'type', format_type(atttypid, atttypmod) FROM pg_attribute
 WHERE attrelid = '$T'::regclass AND attname = 'stringvalue';
-- one batch: a new row, a replayed duplicate of it, and a 600-character string
INSERT INTO $T (tagid, intvalue, stringvalue, dataintegrity, t_stamp) VALUES
  (1, 1, NULL, 192, 1000), (1, 1, NULL, 192, 1000), (2, NULL, repeat('x', 600), 192, 1000);
-- a later batch replaying the same row beside a new one
INSERT INTO $T (tagid, intvalue, dataintegrity, t_stamp) VALUES (1, 1, 192, 1000), (1, 2, 192, 2000);
SELECT 'rows', count(*) FROM $T;
SELECT 'longest', max(length(stringvalue)) FROM $T;
RESET ROLE;
DROP TABLE $T;
SQL
)"
  docker exec postgres psql -X -q -U "$SUPER_USER" -d "$DB_NAME" -c "DROP TABLE IF EXISTS $T" >/dev/null 2>&1 || true
  printf '%s\n' "$out" | sed 's/^/  /'
  if printf '%s' "$out" | grep -q '^type|text$' && printf '%s' "$out" | grep -q '^rows|3$' \
     && printf '%s' "$out" | grep -q '^longest|600$'; then
    ok "a new partition came out text + duplicate-tolerant: 5 rows offered in 2 batches, 3 stored, no error"
  else
    die "the throwaway partition was NOT guarded (above)"
  fi
fi
