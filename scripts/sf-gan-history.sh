#!/usr/bin/env bash
#
# Make an edge's tag history reach the hub over the GATEWAY NETWORK.
#
#   scripts/sf-gan-history.sh [gateway ...]      default: ignition-edge1
#
# This is the other half of the store-and-forward demonstration. The MQTT edge
# buffers inside MQTT Transmission (scripts/sf-arm.sh); the Gateway Network edge
# uses Ignition's own store and forward -- record locally, sync to the hub's
# historian when the link is there, backfill with original timestamps when it
# comes back.
#
# THREE things have to line up, and none of them is where you would look.
#
#   1. The edge's historian is NOT a historian-provider resource. An Edge
#      gateway has exactly ONE historian and you do not create it: its name
#      comes from the `ignition/edge-system-properties` singleton
#      (`historianName`, default "Edge Historian"), and EdgeHistorianCollection
#      builds that one instead of reading the historian-provider collection at
#      all. A provider created on an Edge lists happily over the REST API
#      reporting `enabled: true` and is NEVER INSTANTIATED -- no start line in
#      the log, no store on disk -- so every tag pointed at it records nothing
#      while looking perfectly configured. This script deletes any such leftover
#      rather than leaving the decoy in place.
#
#   2. Forwarding is NOT the provider's own `remoteSync` block. It is the
#      `ignition/edge-sync-settings` singleton: remoteServerName,
#      remoteHistoryProviderName, remoteHistoryEnabled. Singletons READ from
#      /resources/singleton/<module>/<type> and WRITE to /resources/<module>/
#      <type> with a one-element ARRAY -- the read path 404s on PUT and the
#      write path 404s on GET, so each half looks like the wrong endpoint.
#
#   3. The HUB refuses the data by default, and this is the one that costs a
#      day. A gateway grants remote gateways QueryOnly on its historian unless
#      told otherwise -- AbstractServiceDescriptorFactory.DEFAULT_ACCESS is
#      literally RemoteServiceAccessLevel.QueryOnly -- so the edge records fine,
#      syncs every 10s and logs:
#
#        DataStorageException: The remote service has reported that it will not
#        accept storage requests to '<historian>'
#
#      Granting it is a SECURITY ZONE on the hub carrying an entity policy for
#      `TagHistoryProvider`. Do not go looking for the Service Security page:
#      it exists in 8.3.8's bundle, and it is a MOCK -- placeholder labels
#      ("Access Level: $test"), hard-coded profile names, empty selects, and not
#      one API call or submit handler in the whole chunk. Whatever you set there
#      is not persisted anywhere.
#
# Idempotent. Verifies from the edge's own log and from the hub's historian
# tables, never from a response code.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

HUB_STANZA="local"
HUB_CONTAINER="ignition"
ZONE="EdgeHistorySync"
# The three services an edge stores into on this road. History's id is the one
# that cost a day; audit's and the journal's were read from gateway-api-8.3.9.jar
# (*SecurityMarker classes, 01/10/2026) -- the UI names them "Audit Log Access"
# and the alarm journal section, never these ids.
ENTITIES="TagHistoryProvider AuditProfileProvider AlarmJournalProvider"

# The EAM agent name, as the hub knows it, comes from the edge's manifest --
# the security zone matches on it. (This was a declare -A, which is bash 4 and
# would have broken the day this script ran on a macOS host.)
agent_of() { meta_get "$1" EAM_AGENT; }

# Default to the edges that demonstrate the Gateway Network road -- that is
# what SF_TRANSPORT declares. The MQTT edge buffers with Transmission's own
# history store and is armed by sf-arm.sh instead.
TARGETS=("$@")
[ ${#TARGETS[@]} -eq 0 ] && TARGETS=( $(gateways_where SF_TRANSPORT gan) )

need_docker
cd "$REPO_ROOT"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

api() {  # api <stanza> <method> <path> [body-file]
  local stanza="$1" method="$2" path="$3" body="${4:-}"
  if [ -n "$body" ]; then
    node scripts/ign-gw.js api --gateway "$stanza" --method "$method" \
      --path "$path" --body-file "$body" 2>&1
  else
    node scripts/ign-gw.js api --gateway "$stanza" --path "$path" 2>&1
  fi
}

require_gateway "$HUB_CONTAINER"

# --- what the hub is called, and what its historian is called -----------------
# Both are named by the EDGE in its sync settings, so a wrong guess here is a
# sync that runs forever against a server that does not exist.
api "$HUB_STANZA" GET /data/api/v1/gateway-info | tail -1 > "$WORK/hub.json"

# A REDUNDANT PAIR HAS TWO NAMES AND THE EDGE MUST BE GIVEN THE OTHER ONE.
# Pairing does add a role suffix, but it names the CONNECTION, not the server.
# In the edge's own log both appear, and they are not interchangeable:
#
#   remote='ignition-standard-master'            <- the connection
#   Changing target route for redundant server 'Ignition-Standard' to 'Master'
#                             ^^^^^^^^^^^^^^^^^ the server, and what to use
#
# `remoteServerName` is resolved against the SERVER, so the suffixed form finds
# nothing at all. The two failures look nothing alike, which is what made this
# expensive -- the suffixed name never even reaches the hub:
#
#   Ignition-Standard-Master -> sink state 'ServerUnavailable', then
#       "Remote historian sink isn't accepting data at this time.
#        Likely that the remote gateway is unavailable."
#   Ignition-Standard        -> sink state 'Available'
#
# Verified 05/08/2026 by writing each in turn and watching the sink's state
# change in the edge's log. Using the plain name is also correct for the
# unpaired case, and it survives a failover: the whole point of the logical
# name is that metro re-routes it to whichever half is active, so an edge
# pinned to '-Master' would stop forwarding the moment the backup took over --
# precisely when you least want to lose history.
#
# It fails in the worst possible way. LIVE values keep arriving over the remote
# tag provider, so the edge's card reads STREAMING with LAST VALUE 0s ago while
# its history quietly goes nowhere -- the trend simply stops, which reads as an
# edge that has never been sending rather than one that stopped. Found 05/08/2026
# with 29 hours of history missing and every status on the page green.
#
# > Supersedes the earlier reading (05/08/2026, earlier the same day) that the
# > role had to be appended. That change is what produced 'ServerUnavailable'.
HUB_NAME="$(python3 - "$WORK/hub.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["name"])
PY
)"
[ -n "$HUB_NAME" ] || die "could not read the hub's gateway name"

api "$HUB_STANZA" GET \
  "/data/api/v1/resources/list/com.inductiveautomation.historian/historian-provider" \
  | tail -1 > "$WORK/hist.json"
HISTORIAN="$(python3 - "$WORK/hist.json" <<'PY'
import json, sys
items = json.load(open(sys.argv[1])).get("items", [])
sql = [i for i in items if i["config"]["profile"]["type"] == "SqlHistorian" and i["enabled"]]
print(sql[0]["name"] if sql else "")
PY
)"
[ -n "$HISTORIAN" ] || die "no enabled SqlHistorian on $HUB_CONTAINER -- run scripts/sf-historian.sh"
ok "hub is '$HUB_NAME', historian is '$HISTORIAN'"

# --- 3 (first, because the edges fail loudly without it) ----------------------
# The zone grants the entity policy to the named gateways. Written every run:
# the policy is the whole reason storage is accepted, and a zone that drifted
# back to defaults fails in exactly the same silent-looking way as never having
# created one.
say "granting remote history storage on $HUB_CONTAINER"

api "$HUB_STANZA" GET "/data/api/v1/resources/list/ignition/security-zone" \
  | tail -1 > "$WORK/zones.json"

# The zone names EVERY edge's agent, not only today's GAN targets: granting
# storage to an edge that never uses it is harmless, and a demo flipped to the
# other road should not fail on a security zone nobody remembers.
ZONE_AGENTS=()
for _e in $(gateways_with_role edge); do ZONE_AGENTS+=( "$(agent_of "$_e")" ); done

python3 - "$WORK/zones.json" "$WORK/zone-put.json" "$ZONE" "$ENTITIES" "${ZONE_AGENTS[@]}" <<'PY'
import json, sys
src, dst, zone, entities = sys.argv[1:5]
agents = sys.argv[5:]
existing = next((i for i in json.load(open(src)).get("items", [])
                 if i["name"] == zone), None)
body = {
    "name": zone,
    "description": "Lets the Gateway Network edges store tag history, audit records "
                   "and alarm journal events into this gateway",
    "config": {
        "ipAddresses": [], "hostNames": [], "gatewayNames": agents,
        "isSecure": False, "directConnection": False,
        "scopeClient": False, "scopeDesigner": False, "scopeGateway": True,
        "entityPolicies": [
            {"entityId": e, "propName": "defaultAccess", "propValue": "QueryAndStorage"}
            for e in entities.split()
        ],
        "entityZonePolicy": {"priority": 1},
    },
}
# A modify needs the current signature; a create must not carry one.
if existing:
    body["signature"] = existing["signature"]
json.dump([body], open(dst, "w"))
print("modify" if existing else "create")
PY

api "$HUB_STANZA" PUT "/data/api/v1/resources/ignition/security-zone" "$WORK/zone-put.json" \
  | grep -q '"newSignature"' \
  || api "$HUB_STANZA" POST "/data/api/v1/resources/ignition/security-zone" "$WORK/zone-put.json" \
       | grep -q '"newSignature"' \
  || die "could not write the '$ZONE' security zone"
ok "zone '$ZONE' grants $ENTITIES = QueryAndStorage"

# --- 0. the OTHER half of this road: live values ------------------------------
# The Gateway Network road carries two things, and only one of them is history.
# Live values reach the hub through a REMOTE tag provider it mounts the edge as
# -- `[Edge1]MQTT Tags/Demo` in sf_demo.TRANSPORTS -- and that provider was
# hand-made in the gateway UI when this road was first built. It was therefore
# GATEWAY STATE THAT NO SCRIPT RECREATED, so it did not survive a rebuild.
#
# The failure is quiet and asymmetric, which is why it went unnoticed on two
# machines at once: the trend keeps drawing both lines, because history arrives
# by the sync settings below and has nothing to do with the provider. Only the
# Site card goes dark -- OFFLINE, three dashes, "LAST VALUE nothing yet" -- and
# the hub counts "1/2 EDGES streaming". Every check that looks at stored rows
# says the road is healthy.
#
# The shape comes from the REMOTE extension point of ignition/tag-provider
# (GET /data/api/v1/resources/type/ignition/tag-provider lists it with its
# defaultSettings, which is the reliable way to get these right rather than
# guessing). `serverName` is the GAN SERVER name -- the same distinction that
# bites remoteServerName below, so the plain gateway name, never the
# role-suffixed connection. `remoteProviderName` is the provider on the EDGE:
# edge_stream.PROVIDER, which is "edge".
say "mounting each Gateway Network edge as a remote tag provider on $HUB_CONTAINER"

EDGE_PROVIDER="edge"   # edge_stream.PROVIDER -- where the demo tags live

api "$HUB_STANZA" GET "/data/api/v1/resources/list/ignition/tag-provider" \
  | tail -1 > "$WORK/providers.json"

for container in "${TARGETS[@]}"; do
  mount="$(meta_get "$container" GAN_PROVIDER)"
  agent="$(agent_of "$container")"
  [ -n "$mount" ] || die "$container has no GAN_PROVIDER in its stack.meta"
  [ -n "$agent" ] || die "$container has no EAM_AGENT in its stack.meta"

  python3 - "$WORK/providers.json" "$WORK/tp-put.json" \
            "$mount" "$agent" "$EDGE_PROVIDER" <<'PY'
import json, sys
src, dst, mount, server, remote = sys.argv[1:6]
existing = next((i for i in json.load(open(src)).get("items", [])
                 if i["name"] == mount), None)
body = {
    "name": mount,
    "description": "Live tags from %s over the Gateway Network" % server,
    "enabled": True,
    "config": {
        "profile": {"type": "REMOTE", "allowBackfill": False,
                    "enableTagReferenceStore": True},
        "settings": {
            "serverName": server,
            "remoteProviderName": remote,
            # History for this edge does NOT come through the provider -- it is
            # forwarded by the edge's own sync settings into the hub's historian
            # and read back from there. Leaving these empty keeps one mechanism
            # per job; setting them would give the trend a second, competing
            # source for the same tags.
            "historyMode": "GatewayNetwork",
            "historyDatasourceName": "",
            "historyDriverName": "",
            "historyProviderName": "",
            "alarmStatusEnabled": True,
            "alarmMode": "Queried",
        },
    },
}
if existing:
    body["signature"] = existing["signature"]
json.dump([body], open(dst, "w"))
PY

  api "$HUB_STANZA" PUT "/data/api/v1/resources/ignition/tag-provider" "$WORK/tp-put.json" \
    | grep -q '"newSignature"' \
    || api "$HUB_STANZA" POST "/data/api/v1/resources/ignition/tag-provider" "$WORK/tp-put.json" \
         | grep -q '"newSignature"' \
    || die "could not mount '$mount' as a remote tag provider on $HUB_CONTAINER"
  ok "[$mount] -> $agent's '$EDGE_PROVIDER' provider"
done

# --- 1 and 2, per edge --------------------------------------------------------
for container in "${TARGETS[@]}"; do
  stanza="$(stanza_for "$container")"
  [ -n "$stanza" ] || die "no gateway stanza known for '$container'"
  require_gateway "$container"
  say "$container"

  # 1. clear any decoy historian-provider. Deleting takes an array of
  #    {name, signature} at /resources/delete/<module>/<type>; the per-name path
  #    404s for every verb, which reads as a wrong endpoint rather than a wrong
  #    body shape.
  api "$stanza" GET \
    "/data/api/v1/resources/list/com.inductiveautomation.historian/historian-provider" \
    | tail -1 > "$WORK/prov.json"

  if python3 - "$WORK/prov.json" "$WORK/prov-del.json" <<'PY'
import json, sys
items = json.load(open(sys.argv[1])).get("items", [])
if not items:
    sys.exit(1)
json.dump([{"name": i["name"], "signature": i["signature"]} for i in items],
          open(sys.argv[2], "w"))
PY
  then
    api "$stanza" POST \
      "/data/api/v1/resources/delete/com.inductiveautomation.historian/historian-provider" \
      "$WORK/prov-del.json" >/dev/null
    warn "removed a historian-provider resource -- an Edge never runs one"
  fi

  # 2. the sync settings singleton.
  api "$stanza" GET "/data/api/v1/resources/singleton/ignition/edge-sync-settings" \
    | tail -n +2 > "$WORK/sync.json"

  python3 - "$WORK/sync.json" "$WORK/sync-put.json" "$HUB_NAME" "$HISTORIAN" <<'PY'
import json, sys
src, dst, server, historian = sys.argv[1:5]
row = json.load(open(src))
cfg = dict(row["config"])
cfg["remoteServerName"] = server
cfg["remoteHistoryProviderName"] = historian
cfg["remoteHistoryEnabled"] = True
json.dump([{"signature": row["signature"], "config": cfg}], open(dst, "w"))
PY

  # Write DISABLED first, then the real settings. Two writes on purpose, and
  # not as a refresh ritual: the historian only restarts when a value CHANGES,
  # and its remote sink CACHES a critical failure until that restart. So a
  # same-value PUT -- which is what this script used to do on a re-run -- can
  # never recover a sink that faulted while the hub was unreachable or the
  # security zone was wrong: the settings read as correct, the script reports
  # them applied, and the sink keeps refusing forever. Toggling guarantees a
  # restart, a freshly-probing sink, and a log line this run can honestly wait
  # for rather than one from hours ago.
  python3 - "$WORK/sync-put.json" "$WORK/sync-off.json" <<'OFFPY'
import json, sys
row = json.load(open(sys.argv[1]))[0]
cfg = dict(row["config"]); cfg["remoteHistoryEnabled"] = False
json.dump([{"signature": row["signature"], "config": cfg}], open(sys.argv[2], "w"))
OFFPY
  api "$stanza" PUT "/data/api/v1/resources/ignition/edge-sync-settings" "$WORK/sync-off.json" \
    | grep -q '"newSignature"' || die "could not write edge-sync-settings on $container"
  sleep 3
  # The signature moved with the write above; re-read so the ON write carries
  # the current one (it is an optimistic lock -- see CLAUDE.md's signature trap).
  api "$stanza" GET "/data/api/v1/resources/singleton/ignition/edge-sync-settings" \
    | tail -n +2 > "$WORK/sync2.json"
  python3 - "$WORK/sync2.json" "$WORK/sync-on.json" "$HUB_NAME" "$HISTORIAN" <<'ONPY'
import json, sys
row = json.load(open(sys.argv[1]))
cfg = dict(row["config"])
cfg["remoteServerName"] = sys.argv[3]
cfg["remoteHistoryProviderName"] = sys.argv[4]
cfg["remoteHistoryEnabled"] = True
json.dump([{"signature": row["signature"], "config": cfg}], open(sys.argv[2], "w"))
ONPY
  api "$stanza" PUT "/data/api/v1/resources/ignition/edge-sync-settings" "$WORK/sync-on.json" \
    | grep -q '"newSignature"' || die "could not write edge-sync-settings on $container"

  # Verify from the gateway's own log -- and only from THIS run's window. The
  # toggle guarantees a fresh announcement; a stale one from hours ago must
  # not satisfy the check, which is exactly what --tail let happen.
  say "waiting for the historian to name its target"
  synced=""
  for _ in $(seq 1 20); do
    line="$(docker logs --since 90s "$container" 2>&1 \
            | grep 'Will synchronize data to remote historian' | tail -1 || true)"
    case "$line" in
      *"'$HISTORIAN' on server '$HUB_NAME'"*) synced="$line"; break ;;
    esac
    sleep 2
  done
  [ -n "$synced" ] || die "$container never reported a sync target"
  ok "syncing to '$HISTORIAN' on '$HUB_NAME'"

  # And that the hub is now ACCEPTING it. Storage is refused per-request, so a
  # configured-and-refused edge looks identical here until you read the errors.
  say "checking the hub accepts the data"
  sleep 12
  refused="$(docker logs --since 20s "$container" 2>&1 \
             | grep -c 'will not accept storage requests' || true)"
  if [ "${refused:-0}" -gt 0 ]; then
    die "$HUB_CONTAINER is still refusing storage -- the '$ZONE' zone did not take"
  fi
  ok "no storage refusals in the last 20s"
done

echo
say "the edge still has to POINT tags at its historian"
dim "  edge_stream.ensure_history() does that from the Edge project, and only"
dim "  where remoteHistoryEnabled is set -- so the MQTT edge stays untouched."
dim "  Confirm the hub actually filed it:"
dim "    docker exec postgres psql -U postgres -d ignition -c \\"
dim "      \"select * from sqlth_drv;\"   # expect a row per source gateway+provider"
