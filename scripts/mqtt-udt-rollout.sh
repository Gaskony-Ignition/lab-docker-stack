#!/usr/bin/env bash
#
# Fix 2 of the UDT question: the vendor's rollout procedure, scripted.
#
#   scripts/mqtt-udt-rollout.sh                   the add_member variant
#   scripts/mqtt-udt-rollout.sh VARIANT=rename    a different divergence
#   scripts/mqtt-udt-rollout.sh SETTLE=2000       pause after the Engine delete
#
# WHAT IT DOES, AND WHY IT IS A COMMAND AND NOT A BUTTON
#
# The customer's question is "two edges have a UDT with the same name and
# slightly different members -- what does the cloud do?". Fix 1 is the answer a
# customer watches on the page: give the changed layout its own version, and
# both edges keep working. Fix 2 is the answer an ENGINEER applies: push the one
# changed definition to EVERY edge, make Engine forget its learnt copy, and
# re-birth the lot so it learns the new one.
#
# IT REFUSES WHEN AN EDGE IS NOT PUBLISHING. Fix 2 keeps ONE type name, so it
# is only correct for a fleet reachable all at once; an edge that misses the
# push births its old layout on return, collides, and loses the change in the
# cloud while still publishing it. And the push reaches a CUT edge anyway --
# the definition travels to its observer over the container network, not the
# broker -- so this used to report "ok rolled out" and "matches the edge;
# data paused 0.0 s" for a node that had sent nothing at all (0.0 s because it
# measures the longest gap BETWEEN messages, and there were none). The
# pre-check is sparkplug_demo._rollout_all's, from Engine's Node Info/Online
# and the wire witness; `make mqtt-udt-check` asks the same question without
# changing anything.
#
# Fix 2 blanks both edges' cloud tags for the length of the re-birth, so it is
# not something to press in front of a customer -- which is why it lives here
# and not on the demo page. Its own measured pause is printed below, from the
# wire ring: `maxGapMs` per edge is the longest interval with no message from
# that node while the procedure ran.
#
# It calls sparkplug_demo.udt_rollout_all through the hub's `sparkplug/udt`
# WebDev route, so the procedure has exactly ONE implementation -- the same one
# the page's Reset and `make mqtt-reset` use -- and no second copy of it in
# bash to drift. The route's POST needs X-WD-Token: the hub's own
# `sparkplug-token` secret, read out of the container and handed to curl in a
# config on stdin, never in argv and never printed.
#
# Put it back afterwards with:  make mqtt-reset

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

VARIANT=""
SETTLE=0
for a in "$@"; do
  case "$a" in
    VARIANT=*) VARIANT="${a#VARIANT=}" ;;
    SETTLE=*)  SETTLE="${a#SETTLE=}" ;;
    *)         die "unknown argument: $a (try: VARIANT=<name> SETTLE=<ms>)" ;;
  esac
done
case "$SETTLE" in ''|*[!0-9]*) die "SETTLE must be whole milliseconds (got '$SETTLE')" ;; esac

UDT_ROUTE="/system/webdev/GatewayAdmin/sparkplug/udt"
SECRET_FILE=/usr/local/bin/ignition/data/wd-secrets/sparkplug-token

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

need_docker

# The ACTIVE half -- a standby's Engine consumes nothing, so its view of the
# rollout would be a stale one. Same lookup verify-demos.sh makes (:864).
HUB="$(gateways_with_role hub | head -1)"
for g in "$HUB" $(gateways_with_role backup); do
  gateway_running "$g" || continue
  if curl -s --max-time 5 "$(gateway_url "$g")/system/gwinfo" 2>/dev/null \
       | grep -q 'RedundantNodeActiveStatus=Active'; then
    HUB="$g"; break
  fi
done
require_gateway "$HUB"

TOKEN="$(docker exec "$HUB" cat "$SECRET_FILE" 2>/dev/null || true)"
[ -n "$TOKEN" ] || die "no action token on $HUB ($SECRET_FILE) -- the demo's own
     setup generates it:  make sparkplug-setup"

ARGS="{\"settle_ms\": $SETTLE"
[ -n "$VARIANT" ] && ARGS="$ARGS, \"variant\": \"$VARIANT\""
ARGS="$ARGS}"
printf '{"fn": "rollout_all", "args": %s}' "$ARGS" > "$WORK/body.json"

say "rolling the changed UDT definition out to every edge (hub: $HUB)"
dim "  both edges' cloud tags go blank while Engine re-learns the type -- this is the vendor procedure, not a customer visual"

# --max-time covers the route's own wait: _rollout_all waits up to
# ROLLOUT_WAIT_MS (25 s, sparkplug_demo.code:3271) for both births, on top of
# the pushes and the settle.
printf 'header = "X-WD-Token: %s"\n' "$TOKEN" \
  | curl -s --max-time 180 -K - -X POST -H 'Content-Type: application/json' \
      --data-binary @"$WORK/body.json" "$(gateway_url "$HUB")$UDT_ROUTE" \
      > "$WORK/reply.json" 2>/dev/null || true
[ -s "$WORK/reply.json" ] || die "the hub's UDT route did not answer at $UDT_ROUTE
     GET it first -- it is open and read-only:  curl $(gateway_url "$HUB")$UDT_ROUTE"

# Everything printed here comes out of the route's own reply. Nothing is
# re-measured in bash: one measurement, one number, no second opinion to
# disagree with the page.
RC=0
python3 - "$WORK/reply.json" <<'PY' || RC=$?
import json, sys
try:
    r = json.load(open(sys.argv[1]))
except Exception:
    print("  the route answered something that was not JSON:")
    print("  " + open(sys.argv[1]).read()[:400])
    raise SystemExit(1)
if not r.get("ok"):
    print("  refused: %s" % (r.get("message") or "?"))
    raise SystemExit(1)
d = r.get("detail") or {}
# The PRE-CHECK's answer. An edge off the broker still TAKES the push -- the
# definition travels to its observer over the container network -- so without
# this the run reported "ok rolled out" and "matches the edge; paused 0.0 s"
# for a node that had sent nothing (docs/SPARKPLUG.md, Three field questions, B).
if d.get("refused"):
    print("  %s" % (r.get("message") or "refused"))
    raise SystemExit(2)
print("  %s" % (r.get("message") or ""))
print()
print("  took                 %.1f s  (the whole route call)" % ((r.get("tookMs") or 0) / 1000.0))
print("  push to re-birth     %.1f s  (both edges took the new definition, Engine's copy deleted)"
      % ((d.get("pushToRebirthMs") or 0) / 1000.0))
if d.get("settleMs"):
    print("  settle               %.1f s  (asked for, after the Engine delete)" % (d["settleMs"] / 1000.0))
for name, v in sorted((d.get("edges") or {}).items()):
    wire, edge, engine = v.get("wire") or {}, v.get("edge") or {}, v.get("engine") or {}
    # A node that sent nothing has no gap to report: 0.0 s read as "no
    # interruption" for an edge that was not there at all.
    paused = ("SENT NOTHING" if not wire.get("dataMessages")
              else "%.1f s" % ((wire.get("maxGapMs") or 0) / 1000.0))
    print("  %-20s %-12s  %s; edge %s(%d) -> cloud %s %s(%d), %s"
          % (name, paused,
             "sent no message the whole time" if not wire.get("dataMessages")
             else "longest gap with no message from this node",
             edge.get("typeId") or "?", len(edge.get("members") or []),
             engine.get("tagType") or "?", engine.get("typeId") or "-",
             len(engine.get("members") or []),
             "matches" if v.get("engineTypeMatchesEdge") else "DOES NOT MATCH"))
held = d.get("engineTypes") or {}
print("  Engine _types_       %s" % (", ".join("%s (%d tags)" % (k, len(x)) for k, x in sorted(held.items()))
                                     or "no template"))
if d.get("deleted"):
    print("  deleted from Engine  %s" % ", ".join(d["deleted"]))
if d.get("repairedFolders"):
    print("  repaired             %s  (came back as plain Folders and were re-bound)" % ", ".join(d["repairedFolders"]))
PY
case "$RC" in
  0) ;;
  2) die "refused -- nothing was changed. make mqtt-udt-check asks the same question read-only" ;;
  *) die "the rollout did not report success -- its answer is above" ;;
esac

echo
ok "rolled out -- put the rig back with:  make mqtt-reset"
