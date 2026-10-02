#!/usr/bin/env bash
#
# Disable the Ignition modules this stack never uses.
#
#   scripts/ign-modules-trim.sh                 every running gateway
#   scripts/ign-modules-trim.sh ignition        just one
#   scripts/ign-modules-trim.sh --restore ...   put them all back
#   scripts/ign-modules-trim.sh --list ...      show what is enabled, change nothing
#
# WHY THIS EXISTS
#
# A stock 8.3 gateway registers ~32 modules and STARTS THEM ALL, whether or not
# anything uses them. Measured on the hub, idle, running one Perspective project:
# 343 threads and 16.6% of one core with nobody connected. OPC-UA alone -- with
# zero devices configured -- accounted for 29 of those threads (16
# opc-ua-scheduler, 8 milo-netty-event, 3 milo-shared-sched, 2 milo-trust-list),
# each waking on a timer forever.
#
# On a 16-core workstation that is invisible. On the 8 GB / 2-core machines this
# stack is meant to travel to it is ~6.5% of the WHOLE MACHINE per gateway, spent
# on drivers for hardware that does not exist. That is the load this removes; the
# RAM it returns (metaspace, code cache, thread stacks) is a bonus and is smaller
# than the heap change that landed beside it.
#
# WHY onStartup, NOT DELETING THE .modl
#
# Every entry in data/modules.json carries "onStartup": "enabled". Flipping that
# to "disabled" is the gateway's OWN switch: reversible, no re-download, and it
# leaves the file exactly where get-modules.sh and ign-modules.sh put it.
# Deleting .modl files would fight both and could not be undone without network.
#
# THE TRAP THIS FILE EXISTS TO AVOID -- see CLAUDE.md, it cost a full day once:
# modules.json written from the HOST ends up root-owned, and a root-owned
# registry makes every FUTURE module install fail SILENTLY while the gateway UI
# still reports success. The Ignition image has no python3, so the transform runs
# on the host, but the BYTES are written by a shell running as the ignition user.
#
# NOT REPO STATE (rule 4). This is gateway state in a Docker volume, so a rebuild
# restores all 32 -- exactly how the Gateway Network remote tag provider went
# missing on two machines for days. bootstrap.sh calling this is the only thing
# that makes it survive.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# What the three demonstrations actually need. Everything else is disabled.
#
# Kept deliberately though nothing references it today:
#   embr.charts -- the dashboard moved to stock ia.chart.* (0 refs under
#                  ignition/projects), but the repo pins and fetches it on
#                  purpose, so retiring it is a modules.manifest decision rather
#                  than a startup flag. Noted so the next reader need not re-derive it.
KEEP=(
  com.inductiveautomation.perspective          # every screen in the demo
  com.inductiveautomation.eam                  # the EAM push demonstration
  com.inductiveautomation.historian            # store & forward
  com.inductiveautomation.historian.sql        # store & forward
  com.inductiveautomation.jdbc.postgresql      # the only database this stack has
  com.cirruslink.mqtt.engine.gateway           # Site 2's Sparkplug road, hub end
  com.cirruslink.mqtt.transmission.gateway     # ... and the edge end
  com.cirruslink.mqtt.distributor.gateway      # SPIKE: broker in the hub (docs/MQTT-DISTRIBUTOR.md)
  com.mussonindustrial.embr.charts             # see note above
  com.wargoetz.archbuilder                     # the console's Architecture tab
  com.inductiveautomation.webdev               # the Sparkplug tab's broker witness, and
                                               # the isolated edges' observer endpoint
  com.inductiveautomation.alarm-notification   # the Sparkplug demo's notification
                                               # pipelines -- trimmed, the hub silently
                                               # has no pipeline to run at all
  com.inductiveautomation.opcua                # the OPC UA server, on every gateway: the
                                               # demo shows a customer how to expose tags.
                                               # Its drivers stay trimmed.
)

MODULES_JSON=/usr/local/bin/ignition/data/modules.json

MODE=trim
case "${1:-}" in
  --restore) MODE=restore; shift ;;
  --list)    MODE=list;    shift ;;
esac

# No gateway named -> every one that is up. A stopped gateway is not a failure
# here, it simply has nothing to rewrite.
if [ "$#" -gt 0 ]; then
  TARGETS=( "$@" )
else
  TARGETS=()
  # Every gateway the manifests declare, not a typed list: the isolated
  # Sparkplug edges were added after this was written and were never trimmed.
  for gw in $(gateways); do
    if [ "$(docker inspect -f '{{.State.Running}}' "$gw" 2>/dev/null || echo false)" = "true" ]; then
      TARGETS+=( "$gw" )
    fi
  done
fi
[ "${#TARGETS[@]}" -gt 0 ] || die "no gateway is running (name one explicitly to override)"

TMP_JSON="$(mktemp)"
trap 'rm -f "$TMP_JSON"' EXIT
changed_any=0
keep_csv="$(IFS=,; echo "${KEEP[*]}")"

for gw in "${TARGETS[@]}"; do
  say "$gw"

  current="$(docker exec "$gw" cat "$MODULES_JSON")" || die "$gw: cannot read modules.json"

  # The new registry goes to stderr (captured to a file), the human report to
  # stdout -- so one pass produces both without parsing its own output.
  out="$(printf '%s' "$current" \
         | python3 "$REPO_ROOT/scripts/lib/modules-trim.py" "$MODE" "$keep_csv" 2>"$TMP_JSON")" \
    || die "$gw: transform failed -- $out"

  printf '%s\n' "$out" | sed 's/^/  /'

  case "$out" in
    CHANGED*)
      [ -s "$TMP_JSON" ] || die "$gw: refusing to write an empty modules.json"
      # Write beside and rename: a half-written registry is a gateway that loses
      # every module on its next start, and this is the file that must never tear.
      docker exec -i -u ignition "$gw" sh -c \
        "cat > '$MODULES_JSON.trim-tmp' && mv '$MODULES_JSON.trim-tmp' '$MODULES_JSON'" \
        < "$TMP_JSON" || die "$gw: write-back failed"
      changed_any=1
      ;;
  esac

  # Belt and braces on the ownership trap, even though the write ran as ignition.
  docker exec -u root "$gw" chown ignition:ignition "$MODULES_JSON" 2>/dev/null || true
done

if [ "$MODE" != "list" ] && [ "$changed_any" -eq 1 ]; then
  echo
  warn "modules.json is read at STARTUP -- these gateways must be restarted:"
  for gw in "${TARGETS[@]}"; do echo "    docker restart $gw"; done
  echo "  A restart is enough (unlike a JVM-arg change, which needs a recreate)."
fi
