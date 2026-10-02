#!/usr/bin/env bash
#
# v1.1.6 -- every gateway setting bootstrap creates is re-applied, on update
# and on the first demo start after one (scripts/converge.sh).
#
# WHAT CHANGED. A machine built before a setting existed never got it: two
# edges on the work machine still gave their Panel session to Vision
# (29/09/2026). `make update` now runs converge -- but the update that brings
# this release is still running the previous release's update script, which
# does not. So this runs it once, and restarts wd-control so a demo start
# converges the gateways it brings up. Neither restarts a gateway.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"

if container_exists wd-control; then
  if ! docker logs --since "$(docker inspect -f '{{.State.StartedAt}}' wd-control)" wd-control 2>&1 \
       | grep -q '^converge:'; then
    restart_control() { docker restart wd-control >/dev/null; }
    mig_do "restarting wd-control so demo starts apply gateway settings" restart_control
  else
    mig_ok "wd-control already applies gateway settings on a demo start"
  fi
fi

if ! gateway_running "$(gateways_with_role hub | head -1)"; then
  mig_ok "the hub is not running -- the next update or demo start applies the settings"
  mig_finish
fi
if [ "$(cat "$REPO_ROOT/.wd-local/converged/ignition" 2>/dev/null)" = "$(cat "$REPO_ROOT/VERSION")" ]; then
  mig_ok "gateway settings already applied on this release"
  mig_finish
fi
converge() { "$WD" converge || mig_defer "some gateway settings did not apply" "./wd converge"; }
mig_do "re-applying every gateway setting to the running gateways (restarts none)" converge
mig_finish
