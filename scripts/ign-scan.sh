#!/usr/bin/env bash
#
# Make deployed files live on a gateway.
#
#   scripts/ign-scan.sh [gateway] [--wait SECONDS]
#
# External edits to data/projects do NOT auto-apply. This script was written on
# the belief that 8.3 offers only the Config UI's "Scan File System" button and a
# restart, neither scriptable. That was wrong: 8.3.8 has POST
# /data/api/v1/scan/projects (see CLAUDE.md, "How the scan works here", and
# scripts/sparkplug-setup.sh, which uses it). The trigger-file mechanism below
# still works and is kept until this is moved over. A restart is the wrong tool
# either way: it drops every live Perspective session, which is exactly what
# you do not want mid-demonstration.
#
# So the repo ships its own trigger. The `Ops` project carries a gateway timer
# (ignition/timer/AutoScan) that polls for a trigger file every 5 seconds and
# calls system.project.requestScan() when it appears. This script drops that
# file and waits for the timer to acknowledge it in the gateway log.
#
# Chicken-and-egg: `Ops` is itself a project, so it cannot apply itself. It is
# installed once by bootstrap.sh, which restarts the gateway a single time on a
# freshly commissioned volume. After that, nothing here ever restarts anything.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TRIGGER=/usr/local/bin/ignition/data/.scan-trigger
# Whichever project carries the timer: `Ops` on the hub, and on an isolated
# Sparkplug edge its one project, `Edge`. An Edge gateway runs exactly one
# project, so it cannot also run Ops; ign-deploy.sh --as Edge ships the same
# AutoScan inside that one. The trigger file is the same path on both.
AUTOSCAN_GLOB='/usr/local/bin/ignition/data/projects/*/ignition/timer/AutoScan'

GATEWAY="ignition"
WAIT=45
while [ $# -gt 0 ]; do
  case "$1" in
    --wait) WAIT="$2"; shift 2 ;;
    -*)     die "unknown option: $1" ;;
    *)      GATEWAY="$1"; shift ;;
  esac
done

need_docker
require_gateway "$GATEWAY"

# --- is the AutoScan timer actually installed? --------------------------------
if ! docker exec "$GATEWAY" sh -c "ls -d $AUTOSCAN_GLOB >/dev/null 2>&1"; then
  warn "no AutoScan timer (Ops on the hub, Edge on an isolated edge) is present on '$GATEWAY'."
  echo
  echo "This script's trigger-file route needs it. Either:"
  echo "  make bootstrap                     installs Ops and restarts once, or"
  echo "  make sparkplug-deploy              the same for an isolated edge's Edge, or"
  echo "  POST /data/api/v1/scan/projects    the gateway's own REST scan (8.3.8):"
  echo "      ./wd -- node scripts/ign-gw.js api --gateway <stanza> --method POST \\"
  echo "           --path /data/api/v1/scan/projects"
  exit 1
fi

since=$(date -u +%Y-%m-%dT%H:%M:%S)

# THE CHOWN IS ALLOWED TO FAIL, AND ITS FAILURE MEANS SUCCESS.
#
# Two steps, and the timer this is signalling can consume the file between
# them: `touch` creates it, AutoScan's next tick removes it, and `chown` then
# says `cannot access '.scan-trigger': No such file or directory`. Under
# `set -euo pipefail` that killed ign-scan.sh -- and therefore every `make
# deploy` -- at the exact moment the scan had WORKED. Seen on 22/08/2026: the
# deploy reported 1 while the gateway log carried
# `Ops.AutoScan: project scan requested by ign-scan.sh` from the same second.
#
# So don't judge it here. The trigger being gone is precisely what the wait
# loop below reads as success, and it is the honest test either way. The chown
# is defensive anyway: the timer only needs to os.remove() the file, which
# takes write permission on the DIRECTORY, not on the file.
say "requesting a project scan on $GATEWAY"
docker exec -u 0 "$GATEWAY" sh -c \
  "touch '$TRIGGER'; chown \$(stat -c '%u:%g' /usr/local/bin/ignition/data) '$TRIGGER' 2>/dev/null || true"

waited=0
while [ "$waited" -lt "$WAIT" ]; do
  # The timer removes the trigger as soon as it picks it up.
  if ! docker exec "$GATEWAY" test -e "$TRIGGER" 2>/dev/null; then
    # grep -c, not grep -q: under `set -o pipefail` an early-exiting grep -q
    # SIGPIPEs docker logs and the pipeline returns 141, so a real failure would
    # be read as "no failure". See the same note in bootstrap.sh.
    failures="$(docker logs --since "$since" "$GATEWAY" 2>&1 | grep -c 'project scan failed' || true)"
    if [ "${failures:-0}" -gt 0 ]; then
      docker logs --since "$since" "$GATEWAY" 2>&1 | grep 'project scan failed' | tail -1
      die "the gateway timer ran but the scan itself failed"
    fi
    ok "scan applied (${waited}s)"
    exit 0
  fi
  sleep 3; waited=$((waited + 3))
done

warn "the trigger file is still there after ${WAIT}s -- the AutoScan timer is not running."
echo
echo "Most likely the project carrying AutoScan (Ops, or an edge's Edge) is deployed"
echo "but was never loaded by the gateway."
echo "Check:  docker logs $GATEWAY 2>&1 | grep -i 'AutoScan\|Starting project'"
echo "Fallback: gateway Config UI -> Projects -> Scan File System"
exit 1
