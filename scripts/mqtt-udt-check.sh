#!/usr/bin/env bash
#
# Does what each edge PUBLISHES still match what the cloud holds?
#
#   scripts/mqtt-udt-check.sh          one line per edge; exit 1 if any drifts
#
# WHY THIS EXISTS
#
# Engine learns a UDT from the FIRST birth that carries it and keeps that one.
# Change the type on one edge of a fleet and the cloud says nothing: the log
# writes "UDT definition collision detected" once, the edge goes on publishing
# its own members, and Engine's copy of that edge's station quietly lacks them
# (docs/SPARKPLUG.md, T8 and *Three field questions*, B -- where a datatype
# change left Engine holding Float8 while the instance took Int4). Nothing in
# any status, tag quality or alarm marks it. This is the check that does.
#
# It calls sparkplug_demo.udt_drift() through the hub's `sparkplug/udt` WebDev
# route, whose GET is open and read-only -- the SAME comparison scenario 5 puts
# on the page, so the command and the page cannot disagree and there is no
# second copy of the comparison in bash to drift. No token is needed, because
# nothing here writes.
#
# What it compares, per edge: the definition the edge publishes (its own
# observer), the type Engine learnt, and that edge's Engine instance. What it
# CANNOT compare: an alarm limit. A Sparkplug Template carries no alarm
# configuration, so the cloud never receives one to disagree with -- for that,
# compare the edges with each other (scenario 5 shows both layouts side by
# side).
#
# Exit status: 0 when every edge matches, 1 when any does not, so a rollout or
# a release step can gate on it.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ $# -eq 0 ] || die "mqtt-udt-check takes no arguments (got: $*)"

UDT_ROUTE="/system/webdev/GatewayAdmin/sparkplug/udt"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

need_docker

# The ACTIVE half -- a standby's Engine consumes nothing, so its view of the
# cloud's types is whatever it last held. Same lookup mqtt-udt-rollout.sh makes.
HUB="$(gateways_with_role hub | head -1)"
for g in "$HUB" $(gateways_with_role backup); do
  gateway_running "$g" || continue
  if curl -s --max-time 5 "$(gateway_url "$g")/system/gwinfo" 2>/dev/null \
       | grep -q 'RedundantNodeActiveStatus=Active'; then
    HUB="$g"; break
  fi
done
require_gateway "$HUB"

say "UDT drift check: each edge against the cloud (hub: $HUB)"

curl -s --max-time 30 "$(gateway_url "$HUB")$UDT_ROUTE" > "$WORK/udt.json" 2>/dev/null || true
[ -s "$WORK/udt.json" ] || die "the hub's UDT route did not answer at $UDT_ROUTE
     it is open and read-only, so this GET is the whole test:
       curl $(gateway_url "$HUB")$UDT_ROUTE"

RC=0
python3 - "$WORK/udt.json" <<'PY' || RC=$?
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("  the route answered something that was not JSON:")
    print("  " + open(sys.argv[1]).read()[:400])
    raise SystemExit(1)
rows = d.get("drift") or []
if not rows:
    print("  the route answered no drift rows -- is the GatewayAdmin project scanned in?")
    raise SystemExit(1)
bad = 0
for r in rows:
    held = "%s, %s tags" % (r.get("type") or "?", r.get("tags") or 0)
    print("  %-8s %-22s %s" % (r.get("edge") or "?", held, r.get("drift") or "?"))
    if r.get("matches") is not True:
        bad += 1
for node, v in sorted((d.get("retiredNodes") or {}).items()):
    print("  node %s is not one of this demo's edges and still holds %d alarm row(s) in the cloud (%d active)"
          % (node, v.get("rows") or 0, v.get("active") or 0))
    print("     it never births again, so nothing clears them -- decommission it:")
    print("     docs/SPARKPLUG.md, 'Decommissioning an edge node'")
raise SystemExit(1 if bad else 0)
PY

echo
if [ "$RC" -eq 0 ]; then
  ok "every edge's layout matches what the cloud holds"
else
  # Not `die`: the drift IS the answer, and it is printed above.
  warn "at least one edge does not match the cloud -- the lines above say how"
  dim  "  a new name is the safe fix (docs/SPARKPLUG.md, Recommendation for UDT changes, A);"
  dim  "  one name for every edge at once is  make mqtt-udt-rollout  (Fix 2), and  make mqtt-reset  puts the rig back"
fi
exit "$RC"
