#!/usr/bin/env bash
#
# v1.1.12 -- a demo start keeps its settings run's output in
# .wd-local/converge-last.log, and a run past its 20 minutes is a warning that
# names that file rather than a failed start that lost the output.
#
# wd-control runs that code from the checkout, so it needs one restart to load
# it. Restarts no gateway.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"

if container_exists wd-control; then
  if ! docker logs --since "$(docker inspect -f '{{.State.StartedAt}}' wd-control)" wd-control 2>&1 \
       | grep -q 'converge-last.log'; then
    restart_control() { docker restart wd-control >/dev/null; }
    mig_do "restarting wd-control so demo starts keep their settings log" restart_control
  else
    mig_ok "wd-control already keeps the settings log"
  fi
fi
mig_finish
