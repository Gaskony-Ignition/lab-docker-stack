#!/usr/bin/env bash
#
# Put the MQTT demo back to default.
#
#   scripts/mqtt-reset.sh            the cut, the fault, the UDTs, the settings
#   scripts/mqtt-reset.sh --quick    skip the sparkplug-setup.sh re-run
#
# The demo has two purposes (docs/SPARKPLUG.md). The second is that an engineer
# opens the gateways THEMSELVES and changes configuration -- so there has to be
# a way back that does not mean rebuilding the stack. This is it.
#
# Every step reads the live state first and writes only what differs, so a run
# on an untouched rig prints "nothing to do" line by line and changes nothing.
# The summary at the end lists exactly what it did touch.
#
# THE ORDER IS NOT ARBITRARY:
#
#   1. THE CUT first. An edge off `wd-mqtt` receives no rebirth, so a UDT reset
#      run over a cut edge reports a divergence that is really an outage --
#      and then "reset" has lied about the one thing it exists to fix.
#   2. THE FAULT next, with the link proven, so the clear and the local
#      acknowledgement ride the same Sparkplug session.
#   3. THE UDTs, which re-birth both edges: the vendor procedure in reverse
#      (bind v1, retire v2, make Engine forget its copy, re-birth).
#   4. sparkplug-setup.sh, which re-asserts every gateway SETTING in the
#      reference table -- the part a person edits in the web UI. Idempotent,
#      and it re-saves a transmitter only when a value differs, so an
#      untouched rig loses no session to this.
#   5. verify-demos, because a reset nobody checked is a claim, not a reset.
#
# The wire rings are NOT cleared here, and cannot be: see the note where the
# step would have gone.
#
# Two secrets are involved and neither is ever printed, echoed or passed in
# argv (which is world-readable):
#
#   * the hub's `sparkplug-token` (X-WD-Token) for the UDT route and the edge
#     observers, read out of the hub container and handed to curl in a config
#     on stdin -- the pattern scripts/sparkplug-setup.sh already uses;
#   * WD_CONTROL_TOKEN from .secrets.env for wd-control's /mqtt/restore.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

QUICK=0
for a in "$@"; do
  case "$a" in
    --quick) QUICK=1 ;;
    *)       die "unknown option: $a (try: --quick)" ;;
  esac
done

UDT_ROUTE="/system/webdev/GatewayAdmin/sparkplug/udt"
EDGE_ROUTE="/system/webdev/Edge/sparkplug/action"
EDGE_STATE="/system/webdev/Edge/sparkplug/state"
SECRET_FILE=/usr/local/bin/ignition/data/wd-secrets/sparkplug-token
FAULT_ALARM="Pump Fault"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
CHANGED=()
changed() { CHANGED+=( "$1" ); ok "$1"; }

need_docker

EDGES=( $(sparkplug_edges) )
[ ${#EDGES[@]} -gt 0 ] || die "no MQTT edge in stacks/*/stack.meta (ROLE=edge-isolated, MQTT_MODULE=transmission)"

# The ACTIVE half. A standby's Engine consumes nothing by design, so its copy
# of the UDT snapshot is stale for as long as it stands by -- ask the half that
# is actually running the demo. Same lookup verify-demos.sh makes (:864).
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

# --- talking to the gateways ---------------------------------------------------
#
# The token travels in curl's CONFIG on stdin, never argv, and the body in a
# file under the 0700 work directory.

post() {  # post <url> <json> -- prints the reply body, or nothing
  local body="$WORK/post.json"
  printf '%s' "$2" > "$body"
  printf 'header = "X-WD-Token: %s"\n' "$TOKEN" \
    | curl -s --max-time 120 -K - -X POST -H 'Content-Type: application/json' \
        --data-binary @"$body" "$1" 2>/dev/null || true
}

udt_post()  { post "$(gateway_url "$HUB")$UDT_ROUTE" "$1"; }
edge_post() { post "$(gateway_url "$1")$EDGE_ROUTE" "$2"; }

field() {  # field <json-text> <key> -- one top-level field, "" when absent
  printf '%s' "$1" | python3 -c '
import json, sys
try:
    v = json.load(sys.stdin).get(sys.argv[1])
except Exception:
    v = None
print("" if v is None else (json.dumps(v) if isinstance(v, (dict, list)) else v))' "$2" 2>/dev/null || true
}

udt_snapshot() {  # -> $WORK/udt.json
  curl -s --max-time 30 "$(gateway_url "$HUB")$UDT_ROUTE" > "$WORK/udt.json" 2>/dev/null \
    || die "the hub's UDT route did not answer at $UDT_ROUTE -- is the GatewayAdmin project scanned in?"
  [ -s "$WORK/udt.json" ] || die "the hub's UDT route answered with nothing"
}

# One line per edge: "<node> <variant> <typeId> <engineTypeId> <matches>".
udt_lines() {
  python3 - "$WORK/udt.json" <<'PY' 2>/dev/null || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit
for e in d.get("edges") or []:
    edge, engine = e.get("edge") or {}, e.get("engine") or {}
    print("%s %s %s %s %s" % (
        e.get("node") or "?", edge.get("variant") or "?", edge.get("typeId") or "?",
        engine.get("typeId") or "-",
        "same" if set(edge.get("members") or []) == set(engine.get("members") or []) and edge.get("members")
        else "DIFFERENT"))
PY
}

# Baseline is: every edge on the `base` variant of PumpStation, the cloud's copy
# bound to the same type with the same members, and no PumpStation_v2 left in
# Engine's _types_ (Fix 1 puts one there; the reset takes it away again).
udt_at_baseline() {
  python3 - "$WORK/udt.json" <<'PY' >/dev/null 2>&1
import json, sys
d = json.load(open(sys.argv[1]))
edges = d.get("edges") or []
if not edges:
    raise SystemExit(1)
for e in edges:
    edge, engine = e.get("edge") or {}, e.get("engine") or {}
    if edge.get("variant") != "base" or edge.get("typeId") != "PumpStation":
        raise SystemExit(1)
    if engine.get("typeId") != "PumpStation" or engine.get("tagType") != "UdtInstance":
        raise SystemExit(1)
    if set(engine.get("members") or []) != set(edge.get("members") or []):
        raise SystemExit(1)
for name in (d.get("engineTypes") or {}):
    if name != "PumpStation":
        raise SystemExit(1)
PY
}

# --- 1. the broker link ---------------------------------------------------------
#
# docker's own membership of `wd-mqtt` is the ground truth, not wd-control's
# record of what it cut: a cut left behind by an older wd-control, or made by
# hand, has no record and still has to be undone (control/mqttcut.py says the
# same about its own restore path).

say "1/5  the broker link"
BASE="$(control_base)"
if ! curl -fsS --max-time 5 "$BASE/state" > "$WORK/state.json" 2>/dev/null; then
  warn "wd-control is not answering on $BASE, so a cut cannot be undone from here"
  warn "  it is core, so it should be up:  make up STACK=wd-control"
else
  for e in "${EDGES[@]}"; do
    gateway_running "$e" || { dim "  $e is not running -- nothing to restore"; continue; }
    if python3 - "$WORK/state.json" "$e" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
m = s.get("mqtt") or {}
stack = sys.argv[2]
cut = stack in (m.get("cuts") or {})
off = stack not in (m.get("onNetwork") or [])
raise SystemExit(0 if (cut or off) else 1)
PY
    then
      reply="$(printf 'header = "X-WD-Token: %s"\n' "$(secret WD_CONTROL_TOKEN)" \
        | curl -s --max-time 60 -K - -X POST -H 'Content-Type: application/json' \
            --data-binary "{\"stack\": \"$e\"}" "$BASE/mqtt/restore" 2>/dev/null || true)"
      if [ "$(field "$reply" ok)" = True ]; then
        changed "$e: broker link restored (back on $MQTT_NET)"
      else
        warn "$e is off $MQTT_NET and wd-control would not put it back: $(field "$reply" why)"
      fi
    else
      dim "  $e: on $MQTT_NET -- nothing to do"
    fi
  done
fi

# --- 2. the pump fault ----------------------------------------------------------
#
# Only the demo's OWN alarm. Level High, Level Low and Pressure High come and go
# on their own from the Simulate timer, so acknowledging those would be a line
# of output on every run that says nothing about state anyone changed.
#
# Two separate things, and an edge can need one without the other: the FAULT
# TAG being true is what keeps the alarm active, and the alarm stays in the
# status table needing an acknowledgement after it clears.

say "2/5  the pump fault at each edge"
for e in "${EDGES[@]}"; do
  gateway_running "$e" || { dim "  $e is not running -- skipped"; continue; }
  if ! curl -s --max-time 15 "$(gateway_url "$e")$EDGE_STATE" > "$WORK/$e.state.json" 2>/dev/null \
     || [ ! -s "$WORK/$e.state.json" ]; then
    warn "$e: its observer did not answer at $EDGE_STATE -- fault state unknown"
    continue
  fi
  # "<active> <unacked-id> <unacked-id> ..." -- active is the FAULT TAG, which is
  # what has to go false before a reset means anything.
  read -r fault_active fault_ids <<EOF
$(FAULT="$FAULT_ALARM" python3 - "$WORK/$e.state.json" <<'PY' 2>/dev/null || echo "?"
import json, os, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("?"); raise SystemExit
name = os.environ["FAULT"]
tag = next((t for t in (d.get("tags") or []) if t.get("name") == "PumpFault"), None)
rows = [a for a in (d.get("alarms") or []) if a.get("name") == name]
active = bool(tag and tag.get("value")) or any(a.get("active") for a in rows)
print(" ".join(["yes" if active else "no"]
               + [a["id"] for a in rows if a.get("id") and not a.get("acked")]))
PY
)
EOF
  if [ "$fault_active" = "?" ]; then
    warn "$e: could not read its fault state"
    continue
  fi
  if [ "$fault_active" = yes ]; then
    reply="$(edge_post "$e" '{"action": "reset_fault", "user": "mqtt-reset"}')"
    if [ "$(field "$reply" ok)" = True ]; then
      changed "$e: $FAULT_ALARM reset ($(field "$reply" message))"
    else
      warn "$e: $FAULT_ALARM would not reset -- $(field "$reply" message)"
    fi
  else
    dim "  $e: $FAULT_ALARM is not active -- nothing to do"
  fi
  if [ -n "${fault_ids:-}" ]; then
    n=0
    for id in $fault_ids; do
      reply="$(edge_post "$e" "{\"action\": \"ack_local\", \"id\": \"$id\", \"user\": \"mqtt-reset\"}")"
      [ "$(field "$reply" ok)" = True ] && n=$((n + 1))
    done
    if [ "$n" -gt 0 ]; then
      changed "$e: $n $FAULT_ALARM event(s) acknowledged at the edge"
    else
      warn "$e: $FAULT_ALARM had unacknowledged events and none would acknowledge"
    fi
  else
    dim "  $e: no unacknowledged $FAULT_ALARM event -- nothing to do"
  fi
done

# --- 3. the UDTs ---------------------------------------------------------------

say "3/5  the UDT on both edges and in the cloud"
udt_snapshot
BEFORE="$(udt_lines)"
printf '%s\n' "$BEFORE" | while read -r line; do [ -n "$line" ] && dim "  before  $line"; done
if udt_at_baseline; then
  dim "  both edges on the base PumpStation, Engine agrees -- nothing to do"
else
  reply="$(udt_post '{"fn": "reset_baseline"}')"
  if [ "$(field "$reply" ok)" = True ]; then
    changed "UDTs back to baseline: $(field "$reply" message)"
  else
    warn "the UDT reset did not finish: $(field "$reply" message)"
  fi
  udt_snapshot
  printf '%s\n' "$(udt_lines)" | while read -r line; do [ -n "$line" ] && dim "  after   $line"; done
  udt_at_baseline || warn "the UDTs are still not at baseline -- the lines above say how they differ"
fi

# THE WIRE RINGS ARE NOT CLEARED, and this is not an omission.
#
# They live in system.util.getGlobals() under `sparkplug_wire`
# (sparkplug_demo.code:_wire_store), and nothing exposes a clear: the wire
# route's GET takes `?debug=` only and is read-only by contract, and the UDT
# route's POST map has no such fn. The one thing that does empty them is a
# script-library restart, which is a side effect of a project save, not a
# mechanism -- and they are ring buffers on live traffic, so they refill on
# their own within a couple of minutes either way. Inventing a clear route
# here would be adding gateway surface to tidy a display.
dim "  the wire rings are left alone -- nothing clears them, and they turn over on their own"

# --- 4. the gateway settings ---------------------------------------------------

say "4/5  the gateway settings"
# The phrases below are the ones sparkplug-setup.sh prints when it actually
# WRITES a resource, taken from the script itself (:529 transmitter, :561 server
# set, :660 Engine general, :751 alarm journal, :798 custom namespace). Every
# other `ok` line of its is a read that already matched -- which is why a plain
# count of its `ok` lines would report a change on every run.
SETUP_WROTE='transmitter .* set \(|server set RPC client enabled|MQTT Engine set \(|alarm journal .* (POST|PUT) on |custom namespace .* (POST|PUT) '
if [ "$QUICK" = 1 ]; then
  dim "  --quick: sparkplug-setup.sh not re-run, so nothing re-asserted the settings"
else
  if "$REPO_ROOT/scripts/sparkplug-setup.sh" > "$WORK/setup.log" 2>&1; then
    grep -vE '^.{0,12}==>' "$WORK/setup.log" | sed 's/^/    /' || true
    if grep -qE "$SETUP_WROTE" "$WORK/setup.log"; then
      changed "sparkplug-setup.sh rewrote a gateway setting -- the lines above say which"
    else
      dim "  every setting already matched -- nothing to do"
    fi
  else
    sed 's/^/    /' "$WORK/setup.log" | tail -40
    die "sparkplug-setup.sh failed -- its last lines are above"
  fi
fi

# --- 5. the check --------------------------------------------------------------

say "5/5  is the demo ready?"
if DEMO=sparkplug "$REPO_ROOT/scripts/verify-demos.sh"; then
  VERDICT=ready
else
  VERDICT=NOT-ready
fi

say "what this reset changed"
if [ ${#CHANGED[@]} -eq 0 ]; then
  ok "nothing to do -- the demo was already at default"
else
  for c in "${CHANGED[@]}"; do printf '  - %s\n' "$c"; done
fi
[ "$VERDICT" = ready ] || die "verify-demos says the sparkplug demo is not ready -- its own fixes are above"
ok "the MQTT demo is at default, and verify-demos agrees"
