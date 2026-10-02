#!/usr/bin/env bash
#
# v1.1.1 -- the gateways move from Ignition 8.3.8 to 8.3.9.
#
# WHAT CHANGED. The compose defaults now declare 8.3.9. A stopped or removed
# gateway needs nothing: `up` (or a demo start) creates it from the declared
# image. A RUNNING one keeps the image it was created from until it is
# recreated, and self-update.sh names changed gateway stacks only once, in the
# run that pulled them. This keeps it pending, and offered again, until every
# running gateway is on the version this tree declares (or the local override
# in .wd-local/ignition-version).
#
# IT NEEDS A RESTART, so it DEFERS (docs/RELEASING.md, rule 3). Recreate the
# redundant pair together: a backup on another build than its master reports
# Incompatible and restarts itself trying.
#
# ONE-WAY: a gateway volume that has started on 8.3.9 cannot be read by 8.3.8 -- going back to v1.1.0 needs the override set to 8.3.9 (scripts/ign-version.sh 8.3.9), or a rebuild
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"

stale=""
for s in $(gateways); do
  gateway_running "$s" || continue
  want="$(ign_version_override)"
  [ -n "$want" ] || want="$(sed -n 's|.*image: *inductiveautomation/ignition:\${IGNITION_VERSION:-\([0-9.]*\)}.*|\1|p' \
                             "$STACKS_DIR/$s/compose.yaml" | head -1)"
  have="$(docker inspect -f '{{.Config.Image}}' "$s" 2>/dev/null | sed 's|.*ignition:||')"
  [ -n "$want" ] && [ "$have" != "$want" ] && stale="$stale $s:$have"
done

if [ -z "$stale" ]; then
  mig_ok "every running gateway is on the Ignition build this release declares"
  mig_finish
fi

cmd=""
for x in $stale; do cmd="${cmd:+$cmd  &&  }./wd restart STACK=${x%%:*}"; done
mig_defer "running on an older Ignition image than this release declares:$stale" "$cmd"
mig_finish
