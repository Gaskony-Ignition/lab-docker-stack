#!/usr/bin/env bash
#
# v1.1.0 -- release the hub's quarantined store-and-forward backlog.
#
# WHAT CHANGED. Before the Postgres duplicate-skip guard (1.0.0-05), a history
# batch holding a row Postgres already had was rejected whole, and after its
# retries the hub QUARANTINED it in its store-and-forward buffer:
#   data/var/ignition/store-forward/postgres/tag-history/postgres/tag_history_data.idb
# The guard stops new ones, but it does nothing for batches already there, and
# the engine rescans them every 100 ms for ever. Measured 22/09/2026 on the
# machine that found it: 22,517 quarantined batches, ~70% of a core, gone once
# they were released.
#
# WHAT THIS DOES, ONLY IF THAT FILE HOLDS QUARANTINED ROWS. Stops the hub,
# keeps a copy of the file beside it (`.pre-release-<date>`), clears
# `quarantine_id` and `attempt_count` on every quarantined batch, deletes the
# `quarantine_info` rows, and starts the hub. With the guard in place the
# released batches then forward, their duplicates skipped by the trigger.
# A machine with no quarantined rows -- every fresh build -- is left alone.
#
# IT NEEDS A HUB RESTART, so it DEFERS like any other (docs/RELEASING.md, rule
# 3): `make update` can land in the middle of a demonstration. It prints the
# command and stays pending until someone runs it with SF_RELEASE=1.
#
# The SQLite work runs in the toolbox image with the hub's own volumes
# (--volumes-from), as the gateway's user, so the file keeps its owner and the
# host needs neither python nor to know the volume's name.
#
# ONE-WAY: the released batches are forwarded or dropped by the gateway; the copy beside the file is the only way back
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"

HUB=ignition
DIR=/usr/local/bin/ignition/data/var/ignition/store-forward/postgres/tag-history/postgres
DB="$DIR/tag_history_data.idb"
TOOLBOX="wd-toolbox:$(sed -n 's/^TAG=\([0-9][0-9]*\)$/\1/p' "$REPO_ROOT/wd" | head -1)"

container_exists "$HUB" || { mig_ok "no hub container here -- nothing to release"; mig_finish; }
docker image inspect "$TOOLBOX" >/dev/null 2>&1 \
  || { mig_defer "the toolbox image $TOOLBOX is not built yet" "./wd help   then   scripts/migrate.sh"; mig_finish; }

sql() {  # sql <mode ro|rw> <python> -- run against the hub's buffer file
  docker run --rm -i --volumes-from "$HUB" --user "$(docker exec "$HUB" sh -c 'echo $(id -u):$(id -g)' 2>/dev/null || echo 2003:2003)" \
    --entrypoint python3 -e DB="$DB" -e MODE="$1" "$TOOLBOX" - <<<"$2"
}

COUNT='
import os, sqlite3, sys
db = os.environ["DB"]
if not os.path.exists(db):
    print("0 0"); sys.exit()
c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
q = c.execute("select count(*) from persistent_data where quarantine_id is not null").fetchone()[0]
i = c.execute("select count(*) from quarantine_info").fetchone()[0]
print(q, i)'

# Readable while the gateway runs: SQLite allows a reader beside the writer.
# The toolbox runs as the gateway's user, which needs the gateway running for
# `docker exec id`; a stopped hub falls back to 2003, the stock image's user.
read -r quarantined infos <<<"$(sql ro "$COUNT" 2>/dev/null || echo "? ?")"
case "$quarantined" in
  ''|*[!0-9]*) mig_defer "could not read $DB" "docker logs $HUB   then   scripts/migrate.sh"; mig_finish ;;
esac

if [ "$quarantined" -eq 0 ] && [ "${infos:-0}" = 0 ]; then
  mig_ok "the hub's store-and-forward buffer holds no quarantined batches"
  mig_finish
fi

RELEASE='
import os, shutil, sqlite3, time
db = os.environ["DB"]
bak = db + ".pre-release-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(db, bak)
for ext in ("-wal", "-shm"):
    if os.path.exists(db + ext):
        shutil.copy2(db + ext, bak + ext)
c = sqlite3.connect(db)
n = c.execute("update persistent_data set quarantine_id = null, attempt_count = 0 "
              "where quarantine_id is not null").rowcount
m = c.execute("delete from quarantine_info").rowcount
c.commit()
left = c.execute("select count(*) from persistent_data where quarantine_id is not null").fetchone()[0]
c.close()
print("released %d batch(es), deleted %d quarantine record(s), %d left; copy kept at %s" % (n, m, left, bak))'

release() {
  local uid
  uid="$(docker exec "$HUB" sh -c 'echo $(id -u):$(id -g)')"
  warn "RESTARTING THE HUB ($HUB): $quarantined quarantined batch(es) in its store-and-forward buffer.
       The file cannot be edited under a running gateway. Back in about a minute."
  docker stop "$HUB" >/dev/null
  local rc=0
  docker run --rm -i --volumes-from "$HUB" --user "$uid" --entrypoint python3 \
    -e DB="$DB" "$TOOLBOX" - <<<"$RELEASE" || rc=$?
  # Started whatever happened above: a failed edit must not leave the hub down.
  docker start "$HUB" >/dev/null
  wait_for_gateway "$HUB" 300
  [ "$rc" -eq 0 ] || die "the release failed (exit $rc); the hub is running on the unchanged file"
}

if [ "${SF_RELEASE:-}" != 1 ]; then
  mig_defer "$quarantined quarantined store-and-forward batch(es) cost the hub CPU; releasing them restarts it (~1 min)" \
    "SF_RELEASE=1 scripts/migrate.sh"
  mig_finish
fi
mig_do "releasing $quarantined quarantined batch(es) ($infos quarantine record(s)) -- STOPS AND STARTS THE HUB" release

mig_finish
