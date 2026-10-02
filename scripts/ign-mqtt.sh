#!/usr/bin/env bash
#
# Point the Cirrus Link MQTT modules at the broker: MQTT Distributor on the hub's
# redundant pair, TLS 8883 (docs/MQTT-DISTRIBUTOR.md, topology T1).
#
#   scripts/ign-mqtt.sh setup            hub Engine + every MQTT-speaking edge
#   scripts/ign-mqtt.sh setup ignition   just one gateway
#   scripts/ign-mqtt.sh status           what each module is connected to
#   scripts/ign-mqtt.sh check [gateway]  READ ONLY: what setup would change, as
#                                        `drift:` lines (scripts/drift.sh); exit 1 if any
#
# THE SHAPE, AND WHY EACH PIECE (measured in MQTT-DISTRIBUTOR.md round 2)
#
#   edge   server set `Default` with primaryHostId IamHost, and two servers in
#          list order: `Hub A Master` ssl://mqtt-master:8883, `Hub B Backup`
#          ssl://mqtt-backup:8883, keepalive 10. A Warm standby runs no broker
#          (T-D1), so the list is how an edge follows a failover; the primary
#          host ID is how it knows to move (Engine's STATE offline) and to
#          buffer until an Engine is there. Keepalive 10 is how soon a cut edge
#          and the broker notice (T-D12). The Rolling History Buffer that makes
#          a handover lossless is the history store's, and sf-arm.sh sets it
#          (T-D11). Both names exist only on wd-mqtt, so taking an edge off
#          that network is the cut.
#   hub    Engine server `Distributor` ssl://localhost:8883 in its OWN set
#          `Distributor`: each half's Engine talks to its own broker, the only
#          one listening while that half is active (T-D6). Sparkplug B is bound
#          to the set BEFORE the server exists -- a set without it subscribes
#          to nothing while every status reads Good (T-D10).
#   both   every other server is DELETED, so nothing is left dialling an old broker.
#
# The backup has no MQTT_MODULE: redundancy syncs Engine's config from the
# master, and configuring it directly would fight the sync.
#
# Why this is a script and not a click-path: the broker password is generated
# (MQTT_PASSWORD in the gitignored .secrets.env) and must never reach a
# terminal, a transcript or argv. It goes to ign-gw.js in a 0600 file that is
# deleted on exit. Nothing here prints it -- CLAUDE.md rule 2.
#
# Idempotent: ign-gw.js mqtt-server reads each resource and writes only what
# differs, because every Transmission write restarts every transmitter on the
# edge and every Engine write re-births every edge (T-D17). MQTT_FORCE=1
# re-sends the CA and the passwords, which cannot be compared (they are
# encrypted) -- use it after rotating MQTT_PASSWORD.
#
# Certificates are NOT uploaded as files: `cert-file` is an ordinary config
# resource whose config.fileContents is an ENCRYPTED value (see ign-gw.js).

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CA_PEM="$STACKS_DIR/npm/certs/rootCA.pem"
HOST_ID=IamHost          # Engine's primary host ID; the edges wait for its STATE
ENGINE_SET=Distributor
EDGE_SET=Default
# A knob, not a constant: this is one of the handful of values a machine is
# allowed to keep across an update (demo-settings.env.example, and the table in
# docs/RELEASING.md). sf-arm.sh's ROLLING_MAX_AGE has to stay at or above 1.5x
# this, which is why they are documented together.
KEEPALIVE="$(setting KEEPALIVE 10)"

module_for() {
  case "$(meta_get "$1" MQTT_MODULE)" in
    engine)       echo com.cirruslink.mqtt.engine.gateway ;;
    transmission) echo com.cirruslink.mqtt.transmission.gateway ;;
    *)            echo '' ;;
  esac
}
cred_name() { stanza_for "$1"; }   # lib.sh -- one map, not four copies

cmd="${1:-status}"; shift || true
targets=("$@")
[ "${#targets[@]}" -eq 0 ] && targets=( $(gateways_where MQTT_MODULE engine)
                                        $(gateways_where MQTT_MODULE transmission) )

need_docker

case "$cmd" in
  status)
    printf '%-16s %-24s %-28s %s\n' GATEWAY SERVER URL SET
    printf '%-16s %-24s %-28s %s\n' ------- ------ --- ---
    for g in "${targets[@]}"; do
      gateway_running "$g" || { printf '%-16s %s\n' "$g" '(not running)'; continue; }
      mod="$(module_for "$g")"
      node "$REPO_ROOT/scripts/ign-gw.js" api --gateway "$(cred_name "$g")" \
        --path "/data/api/v1/resources/list/$mod/server?limit=10&offset=0" 2>/dev/null \
        | tail -n +2 \
        | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    print('  (unreadable)'); raise SystemExit
for i in d.get('items', []):
    c = i['config']
    print('%-16s %-24s %-28s %s' % ('$g', i['name'], c.get('url', '?'), c.get('serverSet', '?')))
" || true
    done
    ;;

  setup|check)
    [ -f "$CA_PEM" ] || die "no $CA_PEM -- run: make certs"
    MQTT_USER="$(mqtt_user)"
    # check compares and writes nothing, so it never needs the password: the
    # password is encrypted on the gateway and could not be compared anyway.
    MQTT_PASSWORD=""
    [ "$cmd" = check ] || MQTT_PASSWORD="$(secret MQTT_PASSWORD)"
    DRIFT=0

    # Edges FIRST, then the hub. An edge moved to a broker Engine has not
    # reached yet waits for IamHost's STATE and buffers; Engine moved first
    # would leave the edges publishing live into the old broker to nobody.
    ordered=()
    for g in "${targets[@]}"; do [ "$(meta_get "$g" MQTT_MODULE)" = transmission ] && ordered+=("$g"); done
    for g in "${targets[@]}"; do [ "$(meta_get "$g" MQTT_MODULE)" = engine ] && ordered+=("$g"); done

    for g in "${ordered[@]}"; do
      # The Gateway Network store-and-forward edge has exactly one transport,
      # and it is not MQTT: sf-arm.sh keeps its transmitter disabled and it is
      # not on wd-mqtt, so pointing it at the broker would only make it retry.
      if [ "$(meta_get "$g" SF_TRANSPORT)" = gan ]; then
        dim "  $g: skipped -- its road is the Gateway Network; its transmitter stays disabled"
        continue
      fi
      gateway_running "$g" || { warn "$g is not running"; continue; }
      mod="$(module_for "$g")"
      [ "$cmd" = check ] || case "$mod" in
        *transmission*) say "$g -> ssl://mqtt-master:8883, then ssl://mqtt-backup:8883  (Transmission)" ;;
        *)              say "$g -> ssl://localhost:8883 in set '$ENGINE_SET'  (Engine)" ;;
      esac

      # 0600 and removed on exit: the payload carries the broker password, and
      # the password reaches python through the environment, never argv.
      payload="$(mktemp)"; chmod 600 "$payload"
      trap 'rm -f "$payload"' EXIT
      MQTT_USER="$MQTT_USER" MQTT_PASSWORD="$MQTT_PASSWORD" FORCE="${MQTT_FORCE:-0}" \
      MODULE="$mod" CA="$CA_PEM" HOST_ID="$HOST_ID" ENGINE_SET="$ENGINE_SET" CHECK="$cmd" \
      EDGE_SET="$EDGE_SET" KEEPALIVE="$KEEPALIVE" python3 -c '
import json, os, sys
e = os.environ
p = {"module": e["MODULE"], "caName": "rootCA", "caPem": open(e["CA"]).read(),
     "username": e["MQTT_USER"], "password": e["MQTT_PASSWORD"],
     "prune": True, "force": e.get("FORCE") == "1", "check": e["CHECK"] == "check",
     "description": "The hub pair MQTT Distributor -- scripts/ign-mqtt.sh"}
if "transmission" in e["MODULE"]:
    p.update(serverSet=e["EDGE_SET"], serverSetConfig={"primaryHostId": e["HOST_ID"]},
             servers=[{"name": "Hub A Master", "url": "ssl://mqtt-master:8883"},
                      {"name": "Hub B Backup", "url": "ssl://mqtt-backup:8883"}],
             serverConfig={"keepAlive": int(e["KEEPALIVE"])})
else:
    p.update(serverSet=e["ENGINE_SET"],
             serverSetConfig={"primaryHostEnabled": True, "primaryHostId": e["HOST_ID"]},
             servers=[{"name": "Distributor", "url": "ssl://localhost:8883"}],
             bindNamespaces=["Sparkplug B"], bindIfPresent=["AlarmDemoNotify"])
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_TRUNC)
os.write(fd, json.dumps(p).encode()); os.close(fd)
' "$payload"
      if [ "$cmd" = check ]; then
        say "$g -- MQTT servers, server set, namespaces"
        node "$REPO_ROOT/scripts/ign-gw.js" mqtt-server \
            --gateway "$(cred_name "$g")" --payload "$payload" || DRIFT=1
      else
        node "$REPO_ROOT/scripts/ign-gw.js" mqtt-server \
            --gateway "$(cred_name "$g")" --payload "$payload" \
          || warn "$g -- mqtt-server failed"
      fi
      rm -f "$payload"; trap - EXIT
    done

    if [ "$cmd" = check ]; then
      if [ "$DRIFT" -ne 0 ]; then exit 1; fi
      exit 0
    fi

    echo
    dim "The modules reconnect on their own; give them ~20s, then:"
    dim "    scripts/ign-mqtt.sh status"
    dim "    scripts/verify-demos.sh DEMO=sparkplug"
    ;;

  *)
    die "usage: ign-mqtt.sh [status|setup|check] [gateway...]"
    ;;
esac
