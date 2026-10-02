#!/usr/bin/env bash
#
# Build the Sparkplug alarms demo: every isolated edge, and the hub's side of it.
#
#   scripts/sparkplug-setup.sh                    every running ROLE=edge-isolated gateway
#   scripts/sparkplug-setup.sh ignition-edge3     just this edge (the hub side still runs)
#   scripts/sparkplug-setup.sh --deploy-only      push the Edge project and apply it, nothing else
#   scripts/sparkplug-setup.sh --check [gateway]  READ ONLY, for scripts/drift.sh: the hub's
#                                                 Engine settings and custom namespace, or
#                                                 one edge's transmitter and server-set RPC
#                                                 client, as `drift:` lines; exit 1 if any
#
#   SPARKPLUG_CONVERT_UDTS=true|false             how Transmission publishes UDTs
#                                                 (unset: leave the transmitter as it is)
#   SPARKPLUG_PUBLISH_UDT_DEFS=true|false         whether the NBIRTH carries the UDT
#                                                 DEFINITIONS (unset: leave it as it is)
#   SPARKPLUG_DISPLAY_PATH_TYPE=EDGE|ENGINE|IDS_AND_EDGE
#                                                 the hub's propagated-alarm display
#                                                 paths (unset: leave Engine as it is)
#
# Idempotent: every step reads first and writes only what differs, and each is
# judged by the end state it leaves, never by an exit code.
#
# WHAT THE DEMO CLAIMS, AND WHY ALL OF THIS IS A SCRIPT
#
# An edge with NO Gateway Network and no EAM sends its tags, alarms and
# acknowledgements to the cloud, and takes cloud writes and cloud acks back --
# and nothing crosses except the MQTT broker. Every piece of that is gateway
# state (CLAUDE.md rule 4), and gateway state that no script creates is one
# rebuild away from gone. So, in the order that works:
#
#   1. THE ACTION TOKEN. The console stands in for an operator at the edge
#      (trip the pump, acknowledge here) through POST .../sparkplug/action,
#      which checks X-WD-Token against the gateway's own `wd` secret store. One
#      random value, generated ONCE -- the hub's file is the source of truth --
#      and copied to every isolated edge. Never printed, never an argument
#      (argv is world-readable), never in git or a view. `file` provider, for
#      the reasons ign-secrets.sh gives.
#
#   2. THE EDGE PROJECT. An Edge gateway runs exactly ONE project and it must be
#      called `Edge`; these edges are never EAM agents, so it is deployed
#      directly: ignition/edge-projects/SparkplugEdge, landed as `Edge`. The
#      FIRST deploy onto a stub `Edge` has a chicken-and-egg: the AutoScan timer
#      that applies deploys arrives inside the very project being applied, so
#      nothing on the gateway can scan it in. That one time it is applied with
#      POST /data/api/v1/scan/projects -- the REST route behind the Config UI's
#      Scan File System button, listed in 8.3.8's /openapi.json. A restart is
#      the fallback, and is still what a modules.json change needs. After that,
#      deploys apply through AutoScan and nothing restarts.
#      The observer is a WebDev route, so the WebDev module must be ENABLED:
#      ign-modules-trim.sh used to disable it everywhere, and a disabled module
#      answers every route with a 404 indistinguishable from a failed deploy.
#
#   3. THE TAGS. Config, not project files, so a deploy cannot carry them. The
#      project provisions its own (sp_udt.provision, via the `provision`
#      action) and its Simulate timer re-provisions anything missing -- so a
#      rebuilt edge heals itself even if this script never runs again.
#
#   4. THE TRANSMITTER. group AlarmDemo; edge node derived from the gateway's
#      system name (read back from the edge's own observer, so bash does not
#      repeat the rule); tag provider `edge` and the station's folder; and
#      alarmEventEnable, which ships FALSE -- without it the tags reach the cloud
#      and the alarms on them never do, with nothing in any log to say so.
#      server-set rpcClientEnabled too, which system.cirruslink.transmission
#      .publish needs.
#
#   5. STORE AND FORWARD. The three-setting trap belongs to sf-arm.sh, so it is
#      called rather than copied: it enables the history store, re-saves the
#      transmitter, and refuses to report success until the log says
#      historyFlushType=ASYNC. NONE means DROP, silently.
#
#   6. THE HUB'S MQTT ENGINE. enableAlarmEventPublishing, and blockNodeCommands
#      / blockDeviceCommands OFF. Both block flags ship TRUE, and then a cloud
#      write produces no Sparkplug traffic at all -- no error, no dialog, the
#      value just snaps back (sparkplug-lab/docs/RESULTS.md). The display-path
#      type is left alone unless SPARKPLUG_DISPLAY_PATH_TYPE says otherwise.
#
# Config writes are PUT /data/api/v1/resources/<module>/<type> with an ARRAY
# body carrying the signature just read; a singleton reads from
# .../resources/singleton/... (CLAUDE.md, Store & Forward). ign-gw.js adds the
# X-CSRF-Token every write needs.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TX=com.cirruslink.mqtt.transmission.gateway
ENGINE=com.cirruslink.mqtt.engine.gateway
PROJECT_SRC=SparkplugEdge          # ignition/edge-projects/SparkplugEdge
PROJECT_AS=Edge                    # the only project name an Edge gateway runs
PROJ_DIR=/usr/local/bin/ignition/data/projects
MODULES_JSON=/usr/local/bin/ignition/data/modules.json
SECRET_DIR=/usr/local/bin/ignition/data/wd-secrets
PROVIDER=wd
TOKEN_NAME=sparkplug-token
WEBDEV="/system/webdev/$PROJECT_AS/sparkplug"
STORE="Default In-Memory Store"    # the store sf-arm.sh arms
# The demo runs UDTs as Sparkplug TEMPLATES, and both settings are ENFORCED so a
# rebuilt stack comes back the same (docs/SPARKPLUG.md, T8, measured 11/09/2026):
#
#   convertUdts=true (the module's default) flattens every instance into plain
#   metrics: Engine holds folders and an empty _types_, so the demo's UDT panel
#   would have nothing on the cloud side and the customer's question -- what
#   does Engine do when two edges' "same" UDT diverges? -- would be answered
#   by an absence.
#
#   convertUdts=false ALONE sends each instance as a Template but no
#   definition, because publishUdtDefinitions ships false: still no UDT.
#
#   BOTH puts one [MQTT Engine]_types_/PumpStation in the cloud, both edges'
#   instances bound to it, and a divergence shows as Engine's own
#   "UDT definition collision detected" -- the answer, live.
#
# Override either for an experiment; an empty value leaves the transmitter as is.
# THROUGH `setting`, so a machine's choice is not retyped on every invocation.
# These three have been readable from the environment for a while and had no
# committed home at all, which meant they did not survive anything -- not an
# update, not a new terminal. demo-settings.env.example is now that home, and
# the table in docs/RELEASING.md says so.
#
# `setting NAME` WITH NO DEFAULT, not `setting NAME false`. The old spelling
# was `${VAR-false}` -- a single dash -- so an explicitly EMPTY value meant
# "leave the transmitter's setting alone" rather than "write false". `setting`
# keeps that: it returns the empty string when the name is set-but-empty, and
# the `if env.get(...)` guards below already treat that as "do not write".
CONVERT_UDTS="$(setting SPARKPLUG_CONVERT_UDTS false)"
PUBLISH_UDT_DEFS="$(setting SPARKPLUG_PUBLISH_UDT_DEFS true)"
# ENGINE, measured against the other two on the same alarm (11/09/2026):
#   EDGE          edge/AlarmDemo/Pumps/South/PumpFault/Pump Fault
#                 -- no edge node in it: two edges built alike collide
#   IDS_AND_EDGE  edge/AlarmDemo/Edge4/Pumps/AlarmDemo/Pumps/South/PumpFault/Pump Fault
#                 -- the shipped default, and it repeats itself
#   ENGINE        Edge Nodes/AlarmDemo/Edge4/Pumps/South/PumpFault/Pump Fault
#                 -- names the node, and IS the Engine tag's own path
# Each Engine save re-births every edge, so it is written only when it differs.
# `setting`, with the same empty-means-leave-alone behaviour as the two above --
# which is what the header has always claimed and the old `:-ENGINE` spelling
# did not do: a colon-dash treats an empty value as unset, so ENGINE was
# asserted on every run and there was no way to say "leave it". Verified
# 21/09/2026; the comment and the code disagreed.
DISPLAY_PATH_TYPE="$(setting SPARKPLUG_DISPLAY_PATH_TYPE ENGINE)"

DEPLOY_ONLY=0
HUB_ONLY=0
CHECK=0
DRIFT=0
TARGETS=()
for a in "$@"; do
  case "$a" in
    --deploy-only) DEPLOY_ONLY=1 ;;
    --check)       CHECK=1 ;;
    # The hub's side alone (token, Engine, journal): no transmitter is re-saved,
    # so no edge's Sparkplug session is bounced -- safe in the middle of a demo.
    --hub-only)    HUB_ONLY=1 ;;
    -*)            die "unknown option: $a" ;;
    *)             TARGETS+=( "$a" ) ;;
  esac
done
[ ${#TARGETS[@]} -gt 0 ] || TARGETS=( $(sparkplug_edges) )
[ ${#TARGETS[@]} -gt 0 ] || die "no ROLE=edge-isolated, MQTT_MODULE=transmission stack in stacks/*/stack.meta"

case "$CONVERT_UDTS" in ""|true|false) ;;
  *) die "SPARKPLUG_CONVERT_UDTS must be true or false (got '$CONVERT_UDTS')" ;; esac
case "$PUBLISH_UDT_DEFS" in ""|true|false) ;;
  *) die "SPARKPLUG_PUBLISH_UDT_DEFS must be true or false (got '$PUBLISH_UDT_DEFS')" ;; esac
case "$DISPLAY_PATH_TYPE" in ""|EDGE|ENGINE|IDS_AND_EDGE) ;;
  *) die "SPARKPLUG_DISPLAY_PATH_TYPE must be EDGE, ENGINE or IDS_AND_EDGE" ;; esac

HUB=""
for g in $(gateways_with_role hub); do HUB="$g"; break; done
[ -n "$HUB" ] || die "no ROLE=hub stack"

need_docker
cd "$REPO_ROOT"
WORK="$(mktemp -d)"; chmod 700 "$WORK"; trap 'rm -rf "$WORK"' EXIT

FAILED=""
RESTARTED=""
failed() { FAILED="$FAILED
  - $1"; warn "$1"; }

# --- helpers -------------------------------------------------------------------

api() {  # api <stanza> <method> <path> [body-file] -- status line, then the body
  # --method ALWAYS, body or not: a POST without a body would otherwise go out
  # as a GET and answer 200 with the resource it was meant to act on.
  local stanza="$1" method="$2" path="$3" body="${4:-}"
  if [ -n "$body" ]; then
    node scripts/ign-gw.js api --gateway "$stanza" --method "$method" \
      --path "$path" --body-file "$body" 2>&1
  else
    node scripts/ign-gw.js api --gateway "$stanza" --method "$method" --path "$path" 2>&1
  fi
}

# The Config UI's Projects -> Scan File System, as the REST call behind it.
# 8.3.8's /openapi.json lists POST /data/api/v1/scan/projects ("Request Project
# Scan"); CLAUDE.md's "a script can drive neither trigger" predates finding it.
rest_scan() {  # rest_scan <container> -- 0 when the gateway accepted the request
  local out
  out="$(api "$(stanza_for "$1")" POST /data/api/v1/scan/projects || true)"
  case "$out" in 2[0-9][0-9]\ *) return 0 ;; esac
  warn "$1: POST /data/api/v1/scan/projects answered: $(printf '%s' "$out" | head -c 200)"
  return 1
}

# A write is judged by its body, not by grep -q in a pipe: under pipefail an
# early-exiting grep can SIGPIPE its producer and read a success as a failure.
put_ok() {  # put_ok <stanza> <method> <path> <body-file>
  local out
  out="$(api "$1" "$2" "$3" "$4" || true)"
  case "$out" in *'"success": true'*|*'"success":true'*) return 0 ;; esac
  warn "$2 $3 was refused:"
  printf '%s\n' "$out" | head -c 600 >&2; echo >&2
  return 1
}

jget() {  # jget <file> <a.b.c> -- one value out of a JSON file, "" when absent
  python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import json, sys
try:
    v = json.load(open(sys.argv[1]))
    for k in sys.argv[2].split("."):
        v = v[int(k)] if isinstance(v, list) else v.get(k)
except Exception:
    v = None
print("" if v is None else (json.dumps(v) if isinstance(v, (dict, list, bool)) else v))
PY
}

observer() {  # observer <container> -- 0 when GET .../state answers with the document
  curl -s --max-time 10 "$(gateway_url "$1")$WEBDEV/state" > "$WORK/$1.state.json" 2>/dev/null \
    || return 1
  [ -n "$(jget "$WORK/$1.state.json" gateway)" ]
}

wait_for_observer() {  # wait_for_observer <container> [seconds]
  local waited=0 limit="${2:-60}"
  while [ "$waited" -lt "$limit" ]; do
    if observer "$1"; then return 0; fi
    sleep 3; waited=$((waited + 3))
  done
  return 1
}

# POST an action with the token. The token travels in curl's CONFIG on stdin --
# never argv, never echoed -- and the body in a 0600 file.
action() {  # action <container> <json> -- prints the response body
  local body="$WORK/action.json"
  printf '%s' "$2" > "$body"
  printf 'header = "X-WD-Token: %s"\n' "$TOKEN" \
    | curl -s --max-time 30 -K - -X POST -H 'Content-Type: application/json' \
        --data-binary @"$body" "$(gateway_url "$1")$WEBDEV/action" 2>/dev/null || true
}

reply() {  # reply <json-text> <field> -- one field of an action's answer
  printf '%s' "$1" | python3 -c '
import json, sys
try:
    v = json.load(sys.stdin).get(sys.argv[1])
except Exception:
    v = None
print("" if v is None else v)' "$2" 2>/dev/null || true
}

webdev_state() {  # webdev_state <container> -> enabled | disabled | absent | unreadable
  local raw
  raw="$(docker exec "$1" cat "$MODULES_JSON" 2>/dev/null || true)"
  printf '%s' "$raw" | python3 -c '
import json, sys
try:
    mods = json.load(sys.stdin)
except Exception:
    print("unreadable"); raise SystemExit
m = mods.get("com.inductiveautomation.webdev")
print("absent" if m is None else m.get("onStartup", "enabled"))' 2>/dev/null || true
}

# --- 1. the token ------------------------------------------------------------------

TOKEN=""
hub_token() {
  local f="$SECRET_DIR/$TOKEN_NAME"
  if ! docker exec "$HUB" test -s "$f" 2>/dev/null; then
    say "generating the action token (once -- the hub's copy is the source of truth; never printed)"
    python3 -c 'import secrets, sys; sys.stdout.write(secrets.token_urlsafe(32))' \
      | docker exec -i "$HUB" sh -c \
          "mkdir -p $SECRET_DIR && chmod 700 $SECRET_DIR && umask 077 && cat > $f"
  fi
  TOKEN="$(docker exec "$HUB" cat "$f")"
  [ -n "$TOKEN" ] || die "the hub's $f is empty"
  ok "action token present on $HUB"
}

install_token() {  # install_token <container>
  local f="$SECRET_DIR/$TOKEN_NAME"
  # printf, not echo, and no trailing newline: the provider reads the whole file
  # as the secret, so a newline would become part of the token.
  printf '%s' "$TOKEN" | docker exec -i "$1" sh -c \
    "mkdir -p $SECRET_DIR && chmod 700 $SECRET_DIR && umask 077 && cat > $f && chmod 600 $f"
}

# Register the token in the gateway's `wd` file provider, MERGED into whatever
# the provider already lists. On the hub it shares the provider with
# ign-secrets.sh's UI logins, and both scripts merge, so neither drops the
# other's names.
register_secret() {  # register_secret <container>
  local gw="$1" stanza verb
  stanza="$(stanza_for "$gw")"
  api "$stanza" GET "/data/api/v1/resources/list/ignition/secret-provider" \
    > "$WORK/sp-$gw.json" || true
  verb="$(PROVIDER="$PROVIDER" NAME="$TOKEN_NAME" DIR="$SECRET_DIR" \
          python3 - "$WORK/sp-$gw.json" "$WORK/sp-$gw.put.json" <<'PY'
import json, os, sys
src, dst = sys.argv[1], sys.argv[2]
provider, name, directory = os.environ["PROVIDER"], os.environ["NAME"], os.environ["DIR"]
try:
    raw = open(src).read()
    doc = json.loads(raw[raw.index("{"):])
except Exception:
    doc = {}
item = next((i for i in doc.get("items", []) if i.get("name") == provider), None)
entry = {"description": "Sparkplug demo action token (X-WD-Token) -- "
                        "installed by scripts/sparkplug-setup.sh",
         "filePath": directory + "/" + name, "fileType": "CLEARTEXT"}
if item is None:
    body = {"name": provider, "collection": "core", "enabled": True,
            "description": "Demo-estate secrets: files in the gateway's data volume, "
                           "installed by the Ignition-Demos-Stack scripts.",
            "config": {"profile": {"type": "file"}, "settings": {"files": {name: entry}}}}
    verb = "POST"
else:
    config = item.get("config") or {}
    files = config.setdefault("settings", {}).setdefault("files", {})
    if files.get(name) == entry and item.get("enabled", True):
        print("same")
        raise SystemExit
    files[name] = entry
    body = {"name": provider, "collection": "core", "enabled": True,
            "description": item.get("description", ""), "config": config,
            "signature": item.get("signature", "")}
    verb = "PUT"
json.dump([body], open(dst, "w"))
print(verb)
PY
)"
  case "$verb" in
    same) ;;
    POST|PUT)
      put_ok "$stanza" "$verb" /data/api/v1/resources/ignition/secret-provider \
        "$WORK/sp-$gw.put.json" || { failed "$gw: could not register $TOKEN_NAME in '$PROVIDER'"; return 1; } ;;
    *) failed "$gw: could not read its secret providers"; return 1 ;;
  esac
  # Verified by a re-read of the NAMES, never a value.
  api "$stanza" GET "/data/api/v1/resources/list/ignition/secret-provider" \
    > "$WORK/sp-$gw.after.json" || true
  if PROVIDER="$PROVIDER" NAME="$TOKEN_NAME" python3 - "$WORK/sp-$gw.after.json" <<'PY'
import json, os, sys
raw = open(sys.argv[1]).read()
doc = json.loads(raw[raw.index("{"):])
for item in doc.get("items", []):
    if item.get("name") == os.environ["PROVIDER"]:
        files = ((item.get("config") or {}).get("settings") or {}).get("files") or {}
        sys.exit(0 if os.environ["NAME"] in files else 1)
sys.exit(1)
PY
  then
    ok "$gw: '$PROVIDER' provider lists $TOKEN_NAME"
  else
    failed "$gw: '$PROVIDER' provider does not list $TOKEN_NAME after the write"
    return 1
  fi
}

# --- 0. a trial that has already lapsed ------------------------------------------
# Nothing resets a trial automatically, so an edge commissioned more than two
# hours before its first setup is already expired -- and an expired Edge is dead in every way this demo needs: WebDev
# answers every route `402 Trial Expired`, MQTT Transmission logs "Trial license
# is expired" and stops, and no gateway timer runs, so the project deploys and
# scans perfectly and nothing happens. Measured 11/09/2026 on both new edges.
# POST /data/api/v1/trial recovers a lapsed trial, and
# ONLY then -- LicensingRoutes answers 403 while any time remains (CLAUDE.md).
ensure_trial() {  # ensure_trial <container>
  local left
  left="$(trial_seconds "$1")"
  if [ "$left" = 0 ]; then
    say "$1: its trial has EXPIRED -- resetting it"
    api "$(stanza_for "$1")" POST /data/api/v1/trial >/dev/null || true
    sleep 3
    left="$(trial_seconds "$1")"
  fi
  if [ "$left" -gt 0 ] 2>/dev/null; then
    ok "$1: trial has $((left / 60)) min left"
  else
    failed "$1: trial is expired or unreadable ($left) -- make trial-reset GATEWAY=$1"
    return 1
  fi
}

# --- 2. the Edge project ---------------------------------------------------------

deploy_edge() {  # deploy_edge <container> -- 0 once the observer answers
  local edge="$1" restart_for=""

  case "$(webdev_state "$edge")" in
    enabled|absent) ;;
    disabled)
      say "$edge: WebDev is disabled in its modules.json -- the observer is a WebDev route"
      "$REPO_ROOT/scripts/ign-modules-trim.sh" "$edge" | sed 's/^/  /' || true
      restart_for="modules.json is read at startup, and WebDev was just re-enabled" ;;
    *) warn "$edge: could not read modules.json to check WebDev" ;;
  esac

  # A stub `Edge` has no AutoScan, so a trigger-file scan would sit unanswered
  # for its whole timeout. Copy without scanning and ask over REST instead --
  # twice, because a project whose resource set changed can need a second scan
  # before WebDev registers its routes. A restart is the fallback, not the plan.
  local first=0
  if ! docker exec "$edge" test -d "$PROJ_DIR/$PROJECT_AS/ignition/timer/AutoScan" 2>/dev/null; then
    first=1
  fi

  if [ -z "$restart_for" ] && [ "$first" -eq 1 ]; then
    "$REPO_ROOT/scripts/ign-deploy.sh" "$PROJECT_SRC" "$edge" --as "$PROJECT_AS" --no-scan \
      || { failed "$edge: deploying $PROJECT_SRC as $PROJECT_AS failed"; return 1; }
    say "$edge: first deploy -- applying it with the REST project scan"
    if rest_scan "$edge"; then
      sleep 8
      rest_scan "$edge" || true
      if wait_for_observer "$edge" 60; then
        ok "$edge: observer answers at $WEBDEV/state (applied by REST scan, no restart)"
        return 0
      fi
    fi
    restart_for="the REST project scan did not bring the observer up"
  fi

  if [ -n "$restart_for" ]; then
    "$REPO_ROOT/scripts/ign-deploy.sh" "$PROJECT_SRC" "$edge" --as "$PROJECT_AS" --no-scan \
      || { failed "$edge: deploying $PROJECT_SRC as $PROJECT_AS failed"; return 1; }
    say "restarting $edge once: $restart_for"
    docker restart "$edge" >/dev/null
    RESTARTED="$RESTARTED $edge"
    # In a subshell: wait_for_gateway dies on a timeout, and one slow edge must
    # not abandon the others.
    if ! ( wait_for_gateway "$edge" 420 ); then
      failed "$edge did not come back after its restart"; return 1
    fi
  else
    "$REPO_ROOT/scripts/ign-deploy.sh" "$PROJECT_SRC" "$edge" --as "$PROJECT_AS" \
      || warn "$edge: the deploy reported failure -- judging by the observer instead"
  fi

  if wait_for_observer "$edge" 90; then
    ok "$edge: observer answers at $WEBDEV/state"
    return 0
  fi
  # A project whose resource set changed can need a SECOND scan before WebDev
  # registers its routes -- between the two, the route 404s (toolkit).
  "$REPO_ROOT/scripts/ign-scan.sh" "$edge" || true
  if wait_for_observer "$edge" 45; then
    ok "$edge: observer answers at $WEBDEV/state (after a second scan)"
    return 0
  fi
  failed "$edge: the Edge project is on disk but $WEBDEV/state never answered"
  return 1
}

# --- 3. the tags -------------------------------------------------------------------

provision() {  # provision <container>
  local edge="$1" resp code
  # The guard first: a POST WITHOUT the token must be refused, or the token is
  # decoration. The observer's GET stays open by design.
  # A no-op action, so a broken guard shows up as a 400 rather than doing something.
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X POST \
            -H 'Content-Type: application/json' --data-binary '{"action":"none"}' \
            "$(gateway_url "$edge")$WEBDEV/action" 2>/dev/null || true)"
  if [ "$code" = 403 ]; then
    ok "$edge: an action without X-WD-Token is refused (403)"
  else
    failed "$edge: an action WITHOUT the token answered $code, not 403"
  fi

  resp="$(action "$edge" '{"action": "provision", "user": "sparkplug-setup"}')"
  if [ "$(reply "$resp" ok)" = True ]; then
    ok "$edge: $(reply "$resp" message)"
  else
    failed "$edge: provisioning refused -- $(reply "$resp" message)"
    return 1
  fi
}

# --- 4. the transmitter ------------------------------------------------------------

configure_transmitter() {  # configure_transmitter <container>
  local edge="$1" stanza f="$WORK/$1.state.json" report name changed unknown
  stanza="$(stanza_for "$edge")"
  api "$stanza" GET "/data/api/v1/resources/list/$TX/transmitter" | tail -n +2 \
    > "$WORK/$edge.tx.json" || true

  report="$(GROUP="$(jget "$f" group)" NODE="$(jget "$f" node)" \
            FOLDER="$(jget "$f" folder)" TAGPROVIDER="$(jget "$f" provider)" \
            JOURNAL="$(jget "$f" journal)" STORE="$STORE" CONVERT="$CONVERT_UDTS" \
            PUBDEFS="$PUBLISH_UDT_DEFS" \
            python3 - "$WORK/$edge.tx.json" "$WORK/$edge.tx-put.json" <<'PY'
import json, os, sys
src, dst = sys.argv[1], sys.argv[2]
env = os.environ
for k in ("GROUP", "NODE", "FOLDER", "TAGPROVIDER", "JOURNAL"):
    if not env.get(k):
        print("ERROR the observer did not report %s" % k.lower()); raise SystemExit
try:
    items = json.load(open(src)).get("items", [])
except Exception:
    print("ERROR the transmitter list was unreadable"); raise SystemExit
if not items:
    print("ERROR there is no transmitter resource"); raise SystemExit
row = next((i for i in items if (i.get("config") or {}).get("groupId") == env["GROUP"]), items[0])
cfg = dict(row.get("config") or {})
cfg_have = dict(cfg)
want = {"groupId": env["GROUP"], "edgeNodeId": env["NODE"],
        "tagProvider": env["TAGPROVIDER"], "tagPath": env["FOLDER"],
        "alarmEventEnable": True, "alarmJournalName": env["JOURNAL"],
        "historyStore": env["STORE"]}
if env.get("CONVERT"):
    want["convertUdts"] = env["CONVERT"] == "true"
if env.get("PUBDEFS"):
    want["publishUdtDefinitions"] = env["PUBDEFS"] == "true"
if cfg.get("deviceId"):
    want["deviceId"] = ""        # first-level folders ARE the devices
unknown = sorted(k for k in want if k not in cfg)
changed = sorted(k for k, v in want.items() if cfg.get(k) != v)
if not row.get("enabled", True):
    changed.append("enabled")
cfg.update(want)
json.dump([{"name": row["name"], "description": row.get("description", ""),
            "enabled": True, "config": cfg, "signature": row["signature"]}], open(dst, "w"))
print("NAME %s" % row["name"])
print("CHANGED %s" % (",".join(changed) or "-"))
print("UNKNOWN %s" % (",".join(unknown) or "-"))
for k in changed:
    print("DIFF %s is %s, want %s" % (k, json.dumps(row.get("enabled", True) if k == "enabled" else cfg_have.get(k)),
                                      json.dumps(True if k == "enabled" else want[k])))
PY
)"
  case "$report" in ERROR*|"") failed "$edge: transmitter -- ${report:-no answer}"; return 1 ;; esac
  if [ "$CHECK" -eq 1 ]; then
    name="$(printf '%s\n' "$report" | sed -n 's/^NAME //p')"
    while IFS= read -r line; do
      printf '  drift: %s transmitter %s %s\n' "$edge" "'$name'" "$line"; DRIFT=1
    done < <(printf '%s\n' "$report" | sed -n 's/^DIFF //p')
    [ "$DRIFT" -ne 0 ] || ok "$edge: transmitter '$name' as setup would write it"
  fi
  name="$(printf '%s\n' "$report" | sed -n 's/^NAME //p')"
  changed="$(printf '%s\n' "$report" | sed -n 's/^CHANGED //p')"
  unknown="$(printf '%s\n' "$report" | sed -n 's/^UNKNOWN //p')"
  if [ "$unknown" != "-" ]; then
    warn "$edge: transmitter '$name' has no field(s) $unknown -- sent anyway; the PUT decides"
  fi
  if [ "$CHECK" -eq 1 ]; then
    :
  elif [ "$changed" = "-" ]; then
    ok "$edge: transmitter '$name' already publishes the station"
  elif put_ok "$stanza" PUT "/data/api/v1/resources/$TX/transmitter" "$WORK/$edge.tx-put.json"; then
    ok "$edge: transmitter '$name' set ($changed)"
  else
    failed "$edge: transmitter '$name' refused the change ($changed)"; return 1
  fi

  # rpcClientEnabled on every server set. Not needed for tags or alarms; needed
  # by system.cirruslink.transmission.publish, which the notification half of
  # the demo investigates. Its absence is a warning, not a failure.
  api "$stanza" GET "/data/api/v1/resources/list/$TX/server-set" | tail -n +2 \
    > "$WORK/$edge.ss.json" || true
  local rpc
  rpc="$(python3 - "$WORK/$edge.ss.json" "$WORK/$edge.ss-put.json" <<'PY'
import json, sys
try:
    items = json.load(open(sys.argv[1])).get("items", [])
except Exception:
    print("unreadable"); raise SystemExit
todo = [i for i in items if (i.get("config") or {}).get("rpcClientEnabled") is not True]
if not items:
    print("none"); raise SystemExit
if not todo:
    print("same"); raise SystemExit
out = []
for i in todo:
    cfg = dict(i.get("config") or {}); cfg["rpcClientEnabled"] = True
    out.append({"name": i["name"], "description": i.get("description", ""),
                "enabled": i.get("enabled", True), "config": cfg, "signature": i["signature"]})
json.dump(out, open(sys.argv[2], "w"))
print("put")
PY
)"
  if [ "$CHECK" -eq 1 ]; then
    case "$rpc" in
      same) ok "$edge: server set RPC client enabled" ;;
      put)  printf '  drift: %s server set rpcClientEnabled is not true on every set\n' "$edge"; DRIFT=1 ;;
      *)    printf '  drift: %s has no readable MQTT Transmission server set\n' "$edge"; DRIFT=1 ;;
    esac
    return 0
  fi
  case "$rpc" in
    same) ok "$edge: server set already has the RPC client enabled" ;;
    put)  if put_ok "$stanza" PUT "/data/api/v1/resources/$TX/server-set" "$WORK/$edge.ss-put.json"; then
            ok "$edge: server set RPC client enabled"
          else
            warn "$edge: could not enable the server set's RPC client"
          fi ;;
    *)    warn "$edge: no readable server-set resource -- rpcClientEnabled not set" ;;
  esac
}

# THE BROKER. Every edge dials the hub pair's MQTT Distributor: both halves in
# list order, a primary host ID, keepalive 10 (ign-mqtt.sh says why each). And
# the RPC client -- a SECOND MQTT connection with its own CA and credentials
# (rpcCaCertFile, rpcUsername, rpcPassword on each server) that
# system.cirruslink.transmission.publish uses. Left at the defaults -- no CA,
# rpcUsername "admin" -- it failed TLS every 2 s ("PKIX path building failed")
# and every publish() returned normally with nothing sent (measured
# 11/09/2026). ign-mqtt.sh owns the credentials and writes only what differs,
# so this is a no-op on an edge that is already right.
rpc_state() {  # rpc_state <container> -- same | wrong | none | unreadable
  api "$(stanza_for "$1")" GET "/data/api/v1/resources/list/$TX/server" | tail -n +2 \
    > "$WORK/$1.srv.json" || true
  python3 - "$WORK/$1.srv.json" <<'PY'
import json, sys
try:
    items = json.load(open(sys.argv[1])).get("items", [])
except Exception:
    print("unreadable"); raise SystemExit
if not items:
    print("none"); raise SystemExit
# The password is encrypted and cannot be compared; CA and user are written in
# the same PUT, so they stand for it.
bad = [i for i in items
       if (i.get("config") or {}).get("rpcCaCertFile") != (i.get("config") or {}).get("caCertFile")
       or (i.get("config") or {}).get("rpcUsername") != (i.get("config") or {}).get("username")
       or "mqtt-" not in str((i.get("config") or {}).get("url"))]
print("wrong" if bad else "same")
PY
}

configure_broker() {  # configure_broker <container>
  local edge="$1"
  "$REPO_ROOT/scripts/ign-mqtt.sh" setup "$edge" 2>&1 | sed 's/^/  /' || true
  if [ "$(rpc_state "$edge")" = same ]; then
    ok "$edge: dials the hub pair's Distributor; RPC client uses the broker CA and credentials"
  else
    failed "$edge: its Transmission servers are not the hub pair's Distributor with the RPC client set"; return 1
  fi
}

# --- 6. the hub's MQTT Engine --------------------------------------------------------

configure_engine() {
  local stanza shape report changed current
  stanza="$(stanza_for "$HUB")"
  shape=singleton
  api "$stanza" GET "/data/api/v1/resources/singleton/$ENGINE/general" | tail -n +2 \
    > "$WORK/engine.json" || true
  if [ -z "$(jget "$WORK/engine.json" signature)" ]; then
    shape=list
    api "$stanza" GET "/data/api/v1/resources/list/$ENGINE/general" | tail -n +2 \
      > "$WORK/engine.json" || true
  fi

  report="$(SHAPE="$shape" DISPLAY="$DISPLAY_PATH_TYPE" \
            python3 - "$WORK/engine.json" "$WORK/engine-put.json" <<'PY'
import json, os, sys
try:
    doc = json.load(open(sys.argv[1]))
except Exception:
    print("ERROR unreadable"); raise SystemExit
row = doc if os.environ["SHAPE"] == "singleton" else (doc.get("items") or [None])[0]
if not row or "config" not in row:
    print("ERROR no Engine general settings"); raise SystemExit
cfg = dict(row["config"])
want = {"enableAlarmEventPublishing": True, "blockNodeCommands": False,
        "blockDeviceCommands": False}
if os.environ.get("DISPLAY"):
    want["alarmDisplayPathType"] = os.environ["DISPLAY"]
unknown = sorted(k for k in want if k not in cfg)
changed = sorted(k for k, v in want.items() if cfg.get(k) != v)
cfg.update(want)
if os.environ["SHAPE"] == "singleton":
    body = [{"signature": row["signature"], "config": cfg}]
else:
    body = [{"name": row["name"], "description": row.get("description", ""),
             "enabled": row.get("enabled", True), "config": cfg, "signature": row["signature"]}]
json.dump(body, open(sys.argv[2], "w"))
print("CHANGED %s" % (",".join(changed) or "-"))
print("UNKNOWN %s" % (",".join(unknown) or "-"))
print("DISPLAY %s" % cfg.get("alarmDisplayPathType"))
for k in changed:
    print("DIFF %s is %s, want %s" % (k, json.dumps(row["config"].get(k)), json.dumps(want[k])))
PY
)"
  case "$report" in ERROR*|"") failed "$HUB: MQTT Engine general -- ${report:-no answer}"; return 1 ;; esac
  if [ "$CHECK" -eq 1 ]; then
    local before="$DRIFT" line
    while IFS= read -r line; do
      printf '  drift: %s MQTT Engine general %s\n' "$HUB" "$line"; DRIFT=1
    done < <(printf '%s\n' "$report" | sed -n 's/^DIFF //p')
    [ "$DRIFT" -ne "$before" ] || ok "$HUB: MQTT Engine general as setup would write it"
    return 0
  fi
  changed="$(printf '%s\n' "$report" | sed -n 's/^CHANGED //p')"
  current="$(printf '%s\n' "$report" | sed -n 's/^DISPLAY //p')"
  [ "$(printf '%s\n' "$report" | sed -n 's/^UNKNOWN //p')" = "-" ] \
    || warn "$HUB: MQTT Engine general lacks $(printf '%s\n' "$report" | sed -n 's/^UNKNOWN //p')"
  if [ "$changed" = "-" ]; then
    ok "$HUB: MQTT Engine already publishes alarm events and passes commands"
  elif put_ok "$stanza" PUT "/data/api/v1/resources/$ENGINE/general" "$WORK/engine-put.json"; then
    ok "$HUB: MQTT Engine set ($changed)"
  else
    failed "$HUB: MQTT Engine refused the change ($changed)"; return 1
  fi
  dim "  alarm display paths: $current${DISPLAY_PATH_TYPE:+ (asked for $DISPLAY_PATH_TYPE)}"

  # Re-read: the flags that decide whether a cloud write or ack goes anywhere.
  if [ "$shape" = singleton ]; then
    api "$stanza" GET "/data/api/v1/resources/singleton/$ENGINE/general" | tail -n +2 \
      > "$WORK/engine-after.json" || true
    if [ "$(jget "$WORK/engine-after.json" config.blockNodeCommands)" = false ] \
       && [ "$(jget "$WORK/engine-after.json" config.blockDeviceCommands)" = false ] \
       && [ "$(jget "$WORK/engine-after.json" config.enableAlarmEventPublishing)" = true ]; then
      ok "$HUB: verified -- commands pass, alarm events publish"
    else
      failed "$HUB: MQTT Engine does not read back as set"
    fi
  fi
}

# --- 7. the hub's alarm journal ------------------------------------------------------
# Propagated alarms land in the hub's alarm STATUS on their own. They are
# JOURNALED only if the hub has a journal -- and this hub had none (measured
# 11/09/2026: zero alarm-journal profiles), so "real alarms in the hub's status
# and journal" was half true. Its own profile and its OWN TABLES on the existing
# connection: a demo owns its resources, and sharing Ignition's default
# alarm_events table means anybody's `DELETE FROM alarm_events` takes this
# demo's history with it. Settings start from the type's own defaultSettings,
# so nothing here guesses a field; pruning is on because two edges alarm every
# few minutes for as long as the stack runs.
HUB_JOURNAL=AlarmDemoJournal
# `queryOnly` is NOT optional, whatever the form suggests. The profile schema
# lists it beside `type`, and a profile without it is ACCEPTED -- 200, listed,
# enabled -- and then fails to start with a NullPointerException on
# AlarmJournalConfig.queryOnly() (measured 11/09/2026), after which every
# queryJournal answers "profile does not exist". So an existing journal whose
# profile lacks it is REPAIRED here, not reported present.
configure_hub_journal() {
  local stanza ds verb
  stanza="$(stanza_for "$HUB")"
  api "$stanza" GET /data/api/v1/resources/list/ignition/alarm-journal | tail -n +2 \
    > "$WORK/journals.json" || true
  verb="$(python3 - "$WORK/journals.json" "$HUB_JOURNAL" <<'PY'
import json, sys
try:
    items = json.load(open(sys.argv[1])).get("items", [])
except Exception:
    items = []
row = next((i for i in items if i.get("name") == sys.argv[2]), None)
if row is None:
    print("POST")
elif ((row.get("config") or {}).get("profile") or {}).get("queryOnly") is None:
    print("PUT " + row.get("signature", ""))
else:
    print("same")
PY
)"
  if [ "$verb" = same ]; then
    ok "$HUB: alarm journal '$HUB_JOURNAL' present"
    return 0
  fi
  api "$stanza" GET /data/api/v1/resources/names/ignition/database-connection | tail -n +2 \
    > "$WORK/dbs.json" || true
  ds="$(jget "$WORK/dbs.json" items.0.name)"
  [ -n "$ds" ] || { failed "$HUB: no database connection for an alarm journal (make sf-setup creates one)"; return 1; }
  api "$stanza" GET /data/api/v1/resources/type/ignition/alarm-journal | tail -n +2 \
    > "$WORK/journal-type.json" || true
  if ! NAME="$HUB_JOURNAL" DS="$ds" SIG="${verb#PUT }" VERB="${verb%% *}" \
       python3 - "$WORK/journal-type.json" "$WORK/journal-post.json" <<'PY'
import json, os, sys
doc = json.load(open(sys.argv[1]))
point = next((p for p in doc.get("extensionPoints", []) if p.get("typeId") == "DATASOURCE"), None)
if point is None:
    sys.exit(1)
settings = json.loads(json.dumps(point.get("defaultSettings") or {}))
settings["datasource"] = os.environ["DS"]
settings.setdefault("pruning", {}).update({"enabled": True, "age": 14, "ageUnits": "DAY"})
settings.setdefault("advanced", {}).update({"tableName": "sp_alarm_events",
                                            "dataTableName": "sp_alarm_event_data"})
body = {"name": os.environ["NAME"], "collection": "core", "enabled": True,
        "description": "The Sparkplug alarms demo's journal: what the hub records of "
                       "alarms propagated from the isolated edges. scripts/sparkplug-setup.sh.",
        "config": {"profile": {"type": "DATASOURCE", "queryOnly": False}, "settings": settings}}
if os.environ["VERB"] == "PUT":
    body["signature"] = os.environ["SIG"]
json.dump([body], open(sys.argv[2], "w"))
PY
  then
    failed "$HUB: the alarm-journal type offers no DATASOURCE extension point"; return 1
  fi
  if put_ok "$stanza" "${verb%% *}" /data/api/v1/resources/ignition/alarm-journal "$WORK/journal-post.json"; then
    ok "$HUB: alarm journal '$HUB_JOURNAL' ${verb%% *} on '$ds' (tables sp_alarm_events / sp_alarm_event_data)"
  else
    failed "$HUB: could not write alarm journal '$HUB_JOURNAL'"; return 1
  fi
}

# --- 8. notifications as plain MQTT: the hub's custom namespace ---------------------
# The edges' notification pipelines publish plain JSON (not Sparkplug) to
# notify/AlarmDemo/<node>. MQTT Engine only turns non-Sparkplug topics into tags
# through a custom namespace, and this is it. rootFolder PREFIXES the whole
# topic, so the tags land at [MQTT Engine]Notify/notify/AlarmDemo/<node>/<key>
# (measured 11/09/2026). qos1 is false on purpose: with it on, Engine warns that
# the server has no client id and subscribes at QoS0 anyway -- say what happens.
HUB_NAMESPACE=AlarmDemoNotify
configure_hub_notify() {
  local stanza verb
  stanza="$(stanza_for "$HUB")"
  api "$stanza" GET "/data/api/v1/resources/list/$ENGINE/custom-namespace" | tail -n +2 \
    > "$WORK/cns.json" || true
  verb="$(NAME="$HUB_NAMESPACE" python3 - "$WORK/cns.json" "$WORK/cns-put.json" <<'PY'
import json, os, sys
want = {"subscription": "notify/AlarmDemo/#", "qos1": False, "rootFolder": "Notify",
        "tagName": "", "jsonPayload": True, "charset": "UTF_8", "writableTags": False,
        "numbersAsFloats": False}
try:
    items = json.load(open(sys.argv[1])).get("items", [])
except Exception:
    items = []
row = next((i for i in items if i.get("name") == os.environ["NAME"]), None)
body = {"name": os.environ["NAME"], "collection": "core", "enabled": True,
        "description": "Sparkplug demo: alarm notifications an edge publishes as plain JSON "
                       "to notify/AlarmDemo/<node>. scripts/sparkplug-setup.sh.",
        "config": want}
if row is None:
    verb = "POST"
elif all((row.get("config") or {}).get(k) == v for k, v in want.items()) and row.get("enabled", True):
    print("same"); raise SystemExit
else:
    body["signature"] = row["signature"]; verb = "PUT"
json.dump([body], open(sys.argv[2], "w"))
print(verb)
PY
)"
  if [ "$CHECK" -eq 1 ]; then
    case "$verb" in
      same) ok "$HUB: custom namespace '$HUB_NAMESPACE' as setup would write it" ;;
      POST) printf '  drift: %s custom namespace %s is missing\n' "$HUB" "$HUB_NAMESPACE"; DRIFT=1 ;;
      PUT)  printf '  drift: %s custom namespace %s differs from notify/AlarmDemo/# -> Notify, JSON, UTF_8\n' "$HUB" "$HUB_NAMESPACE"; DRIFT=1 ;;
      *)    failed "$HUB: could not read Engine's custom namespaces"; return 1 ;;
    esac
    return 0
  fi
  case "$verb" in
    same) ok "$HUB: custom namespace '$HUB_NAMESPACE' present (notify/AlarmDemo/# -> [MQTT Engine]Notify)" ;;
    POST|PUT)
      if put_ok "$stanza" "$verb" "/data/api/v1/resources/$ENGINE/custom-namespace" "$WORK/cns-put.json"; then
        ok "$HUB: custom namespace '$HUB_NAMESPACE' $verb (notify/AlarmDemo/# -> [MQTT Engine]Notify)"
      else
        failed "$HUB: could not write custom namespace '$HUB_NAMESPACE'"; return 1
      fi ;;
    *) failed "$HUB: could not read Engine's custom namespaces"; return 1 ;;
  esac
}

# Every enabled Engine server must sit in a server set that has a Sparkplug
# namespace bound. One that does not connects, publishes STATE online and
# subscribes to STATE alone: every edge trusts it, publishes live, buffers
# nothing, and the broker drops it all while every status reads Good
# (docs/MQTT-DISTRIBUTOR.md T-D5, T-D10). Checked, never repaired here: which
# set a server belongs in is a decision, and binding a namespace re-births
# every edge on that set.
check_engine_bindings() {
  local stanza out
  stanza="$(stanza_for "$HUB")"
  for t in server namespace-server-set; do
    api "$stanza" GET "/data/api/v1/resources/list/$ENGINE/$t" | tail -n +2 > "$WORK/eng-$t.json" || true
  done
  out="$(python3 - "$WORK/eng-server.json" "$WORK/eng-namespace-server-set.json" <<'PY'
import json, sys
try:
    servers = json.load(open(sys.argv[1])).get("items", [])
    binds = json.load(open(sys.argv[2])).get("items", [])
except Exception:
    print("unreadable"); raise SystemExit
bound = set((b.get("config") or {}).get("serverSet") for b in binds
            if b.get("enabled", True) and str((b.get("config") or {}).get("namespace", "")).startswith("Sparkplug"))
for s in servers:
    c = s.get("config") or {}
    if s.get("enabled", True) and c.get("serverSet") not in bound:
        print("%s (set '%s', %s)" % (s["name"], c.get("serverSet"), c.get("url")))
PY
)"
  case "$out" in
    unreadable) warn "$HUB: could not read Engine's servers to check namespace bindings" ;;
    "") ok "$HUB: every Engine server's set has the Sparkplug B namespace bound" ;;
    *) failed "$HUB: Engine server(s) whose set has NO Sparkplug namespace -- they publish STATE online and consume nothing: $(printf '%s' "$out" | tr '\n' ';')" ;;
  esac
}

# The cloud's half of the notification demo. An alarm that arrives over Sparkplug
# never runs a hub pipeline -- Engine's propagated alarm events carry the edge's
# pipeline name, and a hub pipeline of exactly that qualified name is never
# evaluated (measured 11/09/2026). A HUB alarm does: a reference tag on each
# edge's Engine PumpFault, alarmed and bound to the CloudNotify pipeline in the
# SparkplugCloud project, which answers the edge by writing Engine's CloudNotice
# (a DCMD). The project lives in ignition/edge-projects/ so that it is deployed
# BY NAME, here, and ign-deploy.sh --all never sweeps it onto another gateway.
HUB_CLOUD_PROJECT=SparkplugCloud
HUB_CLOUD_FOLDER=SparkplugDemo      # [default]SparkplugDemo on the hub

configure_hub_cloud() {
  local stanza e nodes="" out
  stanza="$(stanza_for "$HUB")"
  if "$REPO_ROOT/scripts/ign-deploy.sh" "$HUB_CLOUD_PROJECT" "$HUB" 2>&1 | sed 's/^/  /'; then
    ok "$HUB: project $HUB_CLOUD_PROJECT deployed (alarm pipeline CloudNotify)"
  else
    failed "$HUB: deploying $HUB_CLOUD_PROJECT failed"; return 1
  fi
  for e in "${EDGES[@]}"; do
    if observer "$e"; then
      nodes="$nodes $(jget "$WORK/$e.state.json" group)/$(jget "$WORK/$e.state.json" node)/$(jget "$WORK/$e.state.json" device)/$(jget "$WORK/$e.state.json" site)"
    else
      warn "$e: its observer did not answer -- no hub alarm for it this run"
    fi
  done
  [ -n "$nodes" ] || { failed "$HUB: no edge observer answered, so there is nothing to alarm on"; return 1; }
  NODES="$nodes" FOLDER="$HUB_CLOUD_FOLDER" PROJECT="$HUB_CLOUD_PROJECT" \
    python3 - "$WORK/hub-cloud-tags.json" <<'PY'
import json, os, sys
ref = "project:%s:/pipeline:CloudNotify" % os.environ["PROJECT"]
folders = []
for spec in os.environ["NODES"].split():
    group, node, device, site = spec.split("/")
    # "Edge3" -> "Edge 3 offline", the same spelling sparkplug_demo._short_name
    # gives every edge on the page, so the row reads plainly in the alarm table.
    offline = "Edge %s offline" % node[-1]
    folders.append({"name": node, "tagType": "Folder", "tags": [{
        "name": "PumpFault", "tagType": "AtomicTag", "valueSource": "reference", "dataType": "Boolean",
        "sourceTagPath": "[MQTT Engine]Edge Nodes/%s/%s/%s/%s/PumpFault" % (group, node, device, site),
        "alarms": [{"name": "Pump Fault (cloud)", "mode": "Equal", "setpointA": 1, "priority": "Low",
                    "ackMode": "Auto", "activePipeline": ref, "clearPipeline": ref,
                    "notes": "A HUB alarm on the Engine tag, so that a hub pipeline runs: an alarm "
                             "that arrives over Sparkplug never enters one. Auto-acknowledged -- the "
                             "operator acknowledges the edge's own Pump Fault."}]}, {
        # THE LINK ALARM. During an outage every propagated alarm row in the
        # cloud keeps its pre-cut state and nothing marks it (SPARKPLUG.md,
        # Three field questions, A). The cloud has to raise its OWN alarm to
        # say so, and Engine already knows: Node Info/Online goes false when
        # the broker publishes the edge's Last Will, ~1.5 x keepalive after
        # the link drops. Critical, because an edge that has gone quiet makes
        # every other row for it a guess. Auto-acknowledged, so a restored
        # link leaves nothing behind for `make mqtt-reset` to tidy.
        "name": "Online", "tagType": "AtomicTag", "valueSource": "reference", "dataType": "Boolean",
        "sourceTagPath": "[MQTT Engine]Edge Nodes/%s/%s/Node Info/Online" % (group, node),
        "alarms": [{"name": offline, "mode": "Equal", "setpointA": 0, "priority": "Critical",
                    "ackMode": "Auto",
                    "notes": "The CLOUD's own alarm that this edge has gone quiet, on Engine's "
                             "Node Info/Online. It is the gateway half of marking an outage; the "
                             "console's Alarms scenario is the screen half. No pipeline: the "
                             "notification demo is the pump fault, not the link."}]}]})
json.dump({"name": os.environ["FOLDER"], "tagType": "Folder", "tags": [
    {"name": "CloudNotifyLog", "tagType": "AtomicTag", "valueSource": "memory",
     "dataType": "String", "value": "[]"}] + folders}, open(sys.argv[1], "w"))
PY
  # tags/import takes the export file's BYTES (application/octet-stream), hence
  # --body-file-raw; the api() helper sends JSON.
  out="$(node scripts/ign-gw.js api --gateway "$stanza" --method POST \
          --path "/data/api/v1/tags/import?provider=default&type=json&collisionPolicy=Overwrite" \
          --body-file-raw "$WORK/hub-cloud-tags.json" 2>&1 || true)"
  case "$out" in
    *'"failureCount":0'*)
      ok "$HUB: [default]$HUB_CLOUD_FOLDER/<node>/ PumpFault -> $HUB_CLOUD_PROJECT/CloudNotify, Online -> 'Edge N offline' for$(printf ' %s' $nodes | sed 's#[^ ]*/\([^/ ]*\)/[^/ ]*/[^/ ]*#\1#g')" ;;
    *) failed "$HUB: the hub alarm tags were not imported: $(printf '%s' "$out" | head -c 300)"; return 1 ;;
  esac
}

# --- the end state, per edge -------------------------------------------------------

verify_edge() {  # verify_edge <container>
  local edge="$1"
  sleep 4     # a tick or three of the Simulate timer, and sp_tx's 5 s cache
  wait_for_observer "$edge" 30 || { failed "$edge: the observer stopped answering"; return 1; }
  python3 - "$WORK/$edge.state.json" <<'PY' || { failed "$edge: not in its intended state (above)"; return 1; }
import json, sys
d = json.load(open(sys.argv[1]))
tx, udt, tags = d.get("transmission") or {}, d.get("udt") or {}, d.get("tags") or []
good = [t for t in tags if str(t.get("quality", "")).startswith("Good")]
problems = []
if udt.get("instanceType") != "UdtInstance":
    problems.append("station %s is %s, not a UdtInstance" % (d.get("instancePath"), udt.get("instanceType")))
if not tags or len(good) != len(tags):
    problems.append("%d of %d station tags Good" % (len(good), len(tags)))
for key, want in (("groupId", d.get("group")), ("edgeNodeId", d.get("node")),
                  ("tagPath", d.get("folder")), ("alarmEventEnable", True)):
    if tx.get(key) != want:
        problems.append("transmitter %s is %r, not %r" % (key, tx.get(key), want))
print("  node %s/%s  station %s  UDT %s (%s)  convertUdts=%s  store-and-forward=%s  connected=%s"
      % (d.get("group"), d.get("node"), d.get("instancePath"), udt.get("name"),
         udt.get("variant"), tx.get("convertUdts"), tx.get("storeForward"), tx.get("connected")))
for p in problems:
    print("  PROBLEM " + p)
for e in d.get("errors") or []:
    print("  observer could not read: " + e)
sys.exit(1 if problems else 0)
PY
  ok "$edge: in its intended state"
}

# --- run -----------------------------------------------------------------------------

# --check: the same reads and comparisons as below, no write. One gateway per
# call (drift.sh plans one check per gateway): the hub, or one edge. The edge's
# group and node come from its open observer, exactly as the write path gets them.
if [ "$CHECK" -eq 1 ]; then
  for e in "${TARGETS[@]}"; do
    if [ "$e" = "$HUB" ]; then
      require_gateway "$HUB"
      configure_engine || true
      configure_hub_notify || true
    else
      require_gateway "$e"
      if observer "$e"; then
        configure_transmitter "$e" || true
      else
        failed "$e: its observer did not answer at $WEBDEV/state, so the transmitter was not compared"
      fi
    fi
  done
  if [ -n "$FAILED" ]; then die "could not compare:$FAILED"; fi
  if [ "$DRIFT" -ne 0 ]; then exit 1; fi
  exit 0
fi

say "Sparkplug alarms demo -> ${TARGETS[*]}  (hub: $HUB)"
EDGES=()
for e in "${TARGETS[@]}"; do
  if [ "$(meta_get "$e" ROLE)" != edge-isolated ]; then
    failed "$e is not ROLE=edge-isolated -- this demo's edge project must never land on a GAN/EAM gateway"
  elif [ "$(meta_get "$e" MQTT_MODULE)" != transmission ]; then
    failed "$e runs no MQTT Transmission (MQTT_MODULE) -- it is not one of this demo's edges"
  elif gateway_running "$e"; then
    EDGES+=( "$e" )
  else
    failed "$e is not running -- start it: wd demo-start DEMO=sparkplug"
  fi
done
[ ${#EDGES[@]} -gt 0 ] || die "no isolated edge to set up${FAILED}"

if [ "$DEPLOY_ONLY" -eq 0 ]; then
  require_gateway "$HUB"
  echo; say "1. the action token"
  hub_token
  register_secret "$HUB" || true
  # The provider resource reaches the backup by sync; the FILE does not (it is
  # in each half's own volume), so without this every guarded MQTT action 403s
  # while the backup is active. Found 23/09/2026. Same rule as ign-secrets.sh.
  for b in $(gateways_with_role backup); do
    if gateway_running "$b"; then install_token "$b"; ok "action token copied to $b"; fi
  done
fi

for e in "${EDGES[@]}"; do
  if [ "$HUB_ONLY" -eq 1 ]; then break; fi
  echo; say "$e"
  ensure_trial "$e" || continue
  deploy_edge "$e" || continue
  if [ "$DEPLOY_ONLY" -eq 1 ]; then continue; fi
  install_token "$e"
  register_secret "$e" || continue
  provision "$e" || continue
  configure_transmitter "$e" || continue
  configure_broker "$e" || true
  if ! "$REPO_ROOT/scripts/sf-arm.sh" "$e"; then
    failed "$e: store and forward is not armed (sf-arm.sh, above)"
  fi
  verify_edge "$e" || true
done

if [ "$DEPLOY_ONLY" -eq 0 ]; then
  echo; say "6. $HUB's MQTT Engine"
  configure_engine || true
  configure_hub_journal || true
  # The history the edges' rolling buffer replays after every hub handover must
  # not abort a batch (duplicates, EdgeNotice's long JSON).
  if gateway_running postgres; then
    "$REPO_ROOT/scripts/pg-history-guard.sh" 2>&1 | grep -E 'ok|fail|warn' | sed 's/^/  /' || true
  fi
  configure_hub_notify || true
  # The broker, after the notify namespace exists so it is bound to the
  # Distributor's set too; after the edges, so they are waiting when it moves.
  "$REPO_ROOT/scripts/ign-mqtt.sh" setup "$HUB" 2>&1 | sed 's/^/  /' || true
  check_engine_bindings || true
  configure_hub_cloud || true
  # The console's side of the demo is WebDev on the hub too. A trim made before
  # this demo existed disabled it there; that is a restart this script will
  # not do to the hub on its own, so it says exactly what is needed.
  case "$(webdev_state "$HUB")" in
    disabled) warn "$HUB has WebDev disabled -- the console's Sparkplug endpoints need it:"
              dim  "    scripts/ign-modules-trim.sh $HUB && docker restart $HUB   (say so first: someone may be mid-demo)" ;;
  esac
fi

echo
[ -z "$RESTARTED" ] || dim "restarted:$RESTARTED (each once, for the reason given above)"
if [ -n "$FAILED" ]; then
  warn "the Sparkplug demo is NOT fully set up:"
  printf '%s\n' "$FAILED"
  exit 1
fi
say "the Sparkplug demo is set up on ${EDGES[*]}"
dim "  edge page:      $(gateway_url "${EDGES[0]}")/data/perspective/client/$PROJECT_AS"
dim "  observer:       $(gateway_url "${EDGES[0]}")$WEBDEV/state"
