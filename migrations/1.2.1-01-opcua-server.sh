#!/usr/bin/env bash
#
# v1.2.1 -- every gateway runs the OPC UA server (its drivers stay trimmed), and
# publishes it on a 29x41 host port, so the demo can show a customer how to
# expose tags.
#
# A stopped gateway needs nothing: a demo start creates it with the port, and
# converge --starting re-trims and restarts it once. A RUNNING one has the module
# switched on in its modules.json here, which needs a restart, and the port needs
# a recreate -- `./wd restart` does both, so it DEFERS (docs/RELEASING.md, rule 3).
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"

stale=""
for s in $(gateways); do
  gateway_running "$s" || continue
  state="$(docker exec "$s" cat /usr/local/bin/ignition/data/modules.json 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("com.inductiveautomation.opcua",{}).get("onStartup",""))' 2>/dev/null || true)"
  [ "$state" = disabled ] && mig_do "$s: OPC UA server enabled for its next start" \
    "$REPO_ROOT/scripts/ign-modules-trim.sh" "$s"
  if [ "$state" = disabled ] || ! docker port "$s" 62541 >/dev/null 2>&1; then
    stale="$stale $s"
  fi
done

if [ -z "$stale" ]; then
  mig_ok "every running gateway serves OPC UA on its published port"
  mig_finish
fi

cmd=""
for s in $stale; do cmd="${cmd:+$cmd  &&  }./wd restart STACK=$s"; done
mig_defer "OPC UA server waits on a restart:$stale" "$cmd"
mig_finish
