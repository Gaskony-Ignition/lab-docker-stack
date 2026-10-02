#!/usr/bin/env bash
#
# v1.0.0 -- the Postgres history guard.
#
# WHAT CHANGED. The hub historian's tables have to tolerate two things the MQTT
# demo does on purpose: DUPLICATE ROWS, because an edge's Rolling History Buffer
# replays a window it has already sent, and LONG STRINGS, because EdgeNotice
# carries JSON that overran `varchar(255)`. Either one aborts a whole history
# batch while every status in the gateway still reads Good -- which is the worst
# shape a failure can have.
#
# scripts/pg-history-guard.sh installs three things in Postgres: a BEFORE INSERT
# trigger that returns NULL on a duplicate (tagid, t_stamp) so one bad row does
# not take the batch with it, a widening of `stringvalue` to `text`, and -- the
# part that matters over time -- an EVENT TRIGGER on CREATE TABLE that guards
# every partition Ignition creates in FUTURE, next month's included.
#
# WHY A PULL IS NOT ENOUGH. These are objects inside the Postgres data volume,
# not files. A machine that pulled the commit has the script and none of its
# effects. The symptom is `duplicate key value violates unique constraint
# sqlt_data_1_YYYY_MM_pkey` followed by `Error forwarding data`, and lost
# history.
#
# NO RESTART OF ANYTHING. It is DDL through `docker exec postgres psql`; the
# gateway is not involved and does not need to be running.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"

if ! gateway_running postgres; then
  mig_defer "the postgres stack is not running, so the history guard was not installed" \
            "./wd up STACK=postgres   then   scripts/migrate.sh"
  mig_finish
fi

# --status changes nothing. Its report marks an unguarded partition MISSING,
# NO-DEDUP or `character varying` -- exactly the three words the script's own
# self-check counts, so counting them here uses the same judgement rather than
# a second, drifting one.
bad="$("$REPO_ROOT/scripts/pg-history-guard.sh" --status 2>/dev/null \
       | grep -c 'MISSING\|NO-DEDUP\|character varying' || true)"

if [ "${bad:-1}" -eq 0 ]; then
  mig_ok "every history partition skips duplicates and takes long strings; new ones will too"
else
  mig_do "installing the history guard ($bad partition(s) or trigger(s) not yet guarded)" \
    "$REPO_ROOT/scripts/pg-history-guard.sh"
fi

mig_finish
