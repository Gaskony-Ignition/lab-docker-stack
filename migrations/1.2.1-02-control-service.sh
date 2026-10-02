#!/usr/bin/env bash
#
# v1.2.1 -- starting the Redundancy demo clears the changeover record, and
# trials are reset by a person on the Trials page (/trials), not automatically.
#
# wd-control runs that code from the checkout, so it needs one restart to load
# it. Restarts no gateway.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"

if container_exists wd-control; then
  started="$(date -d "$(docker inspect -f '{{.State.StartedAt}}' wd-control)" +%s)"
  newest="$(stat -c %Y "$REPO_ROOT"/control/*.py | sort -n | tail -1)"
  if [ "$started" -lt "$newest" ]; then
    restart_control() { docker restart wd-control >/dev/null; }
    mig_do "restarting wd-control to load this release's control service" restart_control
  else
    mig_ok "wd-control is running this release's control service"
  fi
fi
mig_finish
