#!/usr/bin/env bash
#
# v1.0.0 -- the wd-mqtt network, and the containers that must be on it.
#
# WHAT CHANGED. `wd-mqtt` carries MQTT and nothing else: the hub pair's MQTT
# Distributor answers on it as `mqtt-master` / `mqtt-backup`, names that exist
# on no other network, and every MQTT-speaking edge dials only those. That is
# what makes the console's cut work -- `docker network disconnect wd-mqtt <edge>`
# takes an edge off the broker while backbone still reaches it, so the observer
# can watch a gateway it has just isolated (docs/MQTT-DISTRIBUTOR.md).
#
# WHY A PULL IS NOT ENOUGH, TWICE OVER:
#
#   * The network does not exist. Five stacks declare it `external: true`, so
#     the next `docker compose up -d` on any of them fails outright with
#     "network wd-mqtt declared as external, but could not be found".
#   * A container created before the change is still on backbone alone. It keeps
#     running and reports healthy, and no MQTT client on it can resolve
#     mqtt-master. This is the silent half.
#
# AND WHY self-update WILL NOT FIX IT: a changed compose file is reported, never
# applied, because recreating a stack stops containers. Correct, and it leaves
# this undone -- which is the gap this whole migrations/ directory exists for.
#
# NO RESTART, DELIBERATELY. `docker network connect` attaches a RUNNING
# container, with its alias, and takes effect immediately -- scripts/verify-
# demos.sh already names exactly that as the repair. Recreating from compose
# would also work and would stop a gateway somebody may be presenting from, for
# no gain: the next ordinary `./wd up` recreates it from the compose file and
# lands in the same place.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"

# The stacks that declare it, read from the compose files rather than listed
# here -- a second copy of that list is a second thing to be wrong the day an
# edge is added.
declaring_stacks() {
  local f
  for f in "$STACKS_DIR"/*/compose.yaml; do
    [ -f "$f" ] || continue
    if [ "$(grep -c "name: $MQTT_NET" "$f" || true)" -gt 0 ]; then
      printf '%s\n' "$(basename "$(dirname "$f")")"
    fi
  done
  return 0
}

# The alias that stack claims on wd-mqtt, or empty. The block is
#
#     mqtt:
#       aliases:
#         - mqtt-master
#
# so it is the first `- ` item within three lines of the `mqtt:` key. A stack
# that writes `mqtt: {}` (every edge) has no item there and gets no alias, which
# is right: only the broker halves answer to a name.
alias_on_mqtt() {  # alias_on_mqtt <stack>
  local a
  a="$(sed -n '/^ *mqtt:/,+3p' "$STACKS_DIR/$1/compose.yaml" \
       | sed -n 's/^ *- *//p' | head -1 || true)"
  case "$a" in mqtt-*) printf '%s' "$a" ;; *) printf '' ;; esac
  return 0
}

# --- 1. the network -----------------------------------------------------------
if docker network inspect "$MQTT_NET" >/dev/null 2>&1; then
  mig_ok "the '$MQTT_NET' network exists"
else
  mig_do "creating the MQTT-only '$MQTT_NET' network" \
    docker network create "$MQTT_NET"
fi

# --- 2. the containers on it --------------------------------------------------
#
# A CUT IS NOT DRIFT, and reconnecting one would end a demonstration
# mid-sentence. The demo console can take an edge off wd-mqtt on purpose, on a
# deadline that survives a restart (control/mqttcut.py), and `docker inspect`
# cannot tell that apart from a container created before the network existed --
# both are simply "not on wd-mqtt". So ask the console: /state's `mqtt.cuts`
# carries the container and the seconds left for each live cut.
#
# A console that cannot be reached leaves this empty, which is the safe
# direction: there is then nothing to protect and the ordinary repair runs.
cut_containers() {
  local out
  out="$(curl -fsS --max-time 5 "$(control_base)/state" 2>/dev/null)" || return 0
  printf '%s' "$out" | python3 -c '
import json, sys
try:
    s = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
for c in (s.get("mqtt") or {}).get("cuts", {}).values():
    if c.get("secondsLeft", 0) > 0 and c.get("container"):
        print(c["container"])
' 2>/dev/null || true
  return 0
}
CUT=" $(cut_containers | tr '\n' ' ') "

# Only ones that are RUNNING. A stack that is down gets its membership from the
# compose file the next time it starts, which is the ordinary path and needs
# nothing from here.
attached=0
for s in $(declaring_stacks); do
  if ! gateway_running "$s"; then
    dim "     $s is not running -- compose will put it on $MQTT_NET when it starts"
    continue
  fi
  if network_has "$MQTT_NET" "$s"; then
    mig_ok "$s is on $MQTT_NET"
    continue
  fi
  case "$CUT" in
    *" $s "*)
      mig_ok "$s is off $MQTT_NET because the console CUT it -- left alone"
      continue
      ;;
  esac
  a="$(alias_on_mqtt "$s")"
  if [ -n "$a" ]; then
    mig_do "connecting $s to $MQTT_NET as '$a' (no restart)" \
      docker network connect --alias "$a" "$MQTT_NET" "$s"
  else
    mig_do "connecting $s to $MQTT_NET (no restart)" \
      docker network connect "$MQTT_NET" "$s"
  fi
  attached=$((attached + 1))
done

if [ "$attached" -gt 0 ] && [ "$MIG_CHECK" != 1 ]; then
  ok "$attached container(s) joined $MQTT_NET without a restart"
fi

mig_finish
