#!/usr/bin/env bash
#
# Audit records and alarm events from the Store & Forward edges to the hub.
#
#   scripts/sf-audit-alarms.sh        reads first, writes only what differs
#
# Two roads, kept distinct on purpose (docs/STORE-FORWARD.md, "Audit and alarms"):
#
#   Edge 1, Gateway Network   Edge sync sends both, built in: remoteAuditEnabled +
#                             remoteAuditProfileName, remoteJournalEnabled +
#                             remoteJournalName. The hub refuses storage until
#                             EdgeHistorySync grants AuditProfileProvider and
#                             AlarmJournalProvider (sf-gan-history.sh writes that zone).
#   Edge 2, MQTT only         Alarms ride Sparkplug (sf-arm.sh sets alarmEventEnable).
#                             Sparkplug has no audit records, so the hub PULLS Edge 2's
#                             audit log over Ignition's REST API with a read-only API
#                             token (sf_audit.pull on the hub). No Gateway Network.
#
# Restarts nothing. Secrets are written straight into the gateways' wd-secrets
# folders and never printed.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

HUB="$(gateways_with_role hub | head -1)"
AUDIT=EdgeAudit
JOURNAL=EdgeAlarms
SECRET_DIR=/usr/local/bin/ignition/data/wd-secrets
TOKEN_NAME=hub-audit-reader      # the API token's name on the MQTT edge
KEY_FILE=edge2-audit-token       # its key, on the hub
WD_TOKEN_NAME=sf-token                # X-WD-Token for the edges' sf/* WebDev routes

need_docker
cd "$REPO_ROOT"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
FAILED=""
failed() { FAILED="$FAILED
  - $1"; warn "$1"; }

api() {  # api <stanza> <method> <path> [body-file]  -- JSON body only on stdout
  local args=(--gateway "$1" --method "$2" --path "$3")
  [ -n "${4:-}" ] && args+=(--body-file "$4")
  node scripts/ign-gw.js api "${args[@]}" 2>/dev/null | sed -n '/^[[{]/,$p'
}
wrote() { grep -qE '"newSignature"|"success": ?true'; }

require_gateway "$HUB"
HS="$(stanza_for "$HUB")"

# --- the hub: where both roads land -------------------------------------------
say "$HUB: audit profile '$AUDIT' and alarm journal '$JOURNAL'"
api "$HS" GET /data/api/v1/resources/list/ignition/audit-profile > "$WORK/ap.json"
api "$HS" GET /data/api/v1/resources/list/ignition/alarm-journal > "$WORK/aj.json"
api "$HS" GET /data/api/v1/resources/type/ignition/alarm-journal > "$WORK/ajt.json"
python3 - "$WORK" "$AUDIT" "$JOURNAL" <<'PY'
import json, os, sys
w, audit, journal = sys.argv[1:4]
def items(f):
    try:
        return json.load(open(os.path.join(w, f))).get("items", [])
    except Exception:
        return []
want_a = {"profile": {"type": "database", "retentionDays": 90},
          "settings": {"databaseName": "Postgres", "pruneEnabled": True,
                       "autoCreate": True, "tableName": "edge_audit_events"}}
row = next((i for i in items("ap.json") if i["name"] == audit), None)
have = (row or {}).get("config") or {}
if row is None or any(have.get("settings", {}).get(k) != v for k, v in want_a["settings"].items()):
    body = {"name": audit, "enabled": True, "config": want_a,
            "description": "Audit records from the edges: Edge 1 by Edge sync over the "
                           "Gateway Network, Edge 2 pulled by the hub over the REST API."}
    if row:
        body["signature"] = row["signature"]
    json.dump([body], open(os.path.join(w, "ap-put.json"), "w"))
point = next((p for p in json.load(open(os.path.join(w, "ajt.json"))).get("extensionPoints", [])
              if p.get("typeId") == "DATASOURCE"), {})
row = next((i for i in items("aj.json") if i["name"] == journal), None)
if row is None:
    s = json.loads(json.dumps(point.get("defaultSettings") or {}))
    s["datasource"] = "Postgres"
    s.setdefault("pruning", {}).update({"enabled": True, "age": 14, "ageUnits": "DAY"})
    s.setdefault("advanced", {}).update({"tableName": "edge_alarm_events",
                                         "dataTableName": "edge_alarm_event_data"})
    json.dump([{"name": journal, "collection": "core", "enabled": True,
                "description": "Alarm events from the Store & Forward edges: Edge 1 by Edge "
                               "sync over the Gateway Network, Edge 2 over MQTT (Sparkplug).",
                "config": {"profile": {"type": "DATASOURCE", "queryOnly": False},
                           "settings": s}}], open(os.path.join(w, "aj-put.json"), "w"))
PY
for k in ap aj; do
  [ -f "$WORK/$k-put.json" ] || continue
  path=/data/api/v1/resources/ignition/audit-profile; [ "$k" = aj ] && path=/data/api/v1/resources/ignition/alarm-journal
  method=POST; grep -q '"signature"' "$WORK/$k-put.json" && method=PUT
  api "$HS" "$method" "$path" "$WORK/$k-put.json" | wrote && ok "$HUB: $path written" \
    || failed "$HUB: could not write $path"
done
[ -f "$WORK/ap-put.json" ] || [ -f "$WORK/aj-put.json" ] || ok "$HUB: both present"

# The zone that lets the Gateway Network edge store into this gateway. Owned by
# sf-gan-history.sh, which writes all three grants on a demo start; added here
# too (never removed) so `make update` alone is enough.
api "$HS" GET /data/api/v1/resources/list/ignition/security-zone > "$WORK/z.json"
if python3 - "$WORK/z.json" <<'PY2'
import json, sys
z = next((i for i in json.load(open(sys.argv[1])).get("items", []) if i["name"] == "EdgeHistorySync"), None)
if z is None:
    sys.exit(1)
c = z["config"]; have = {p["entityId"] for p in c.get("entityPolicies", [])}
add = [e for e in ("AuditProfileProvider", "AlarmJournalProvider") if e not in have]
if not add:
    sys.exit(1)
for e in add:
    c.setdefault("entityPolicies", []).append({"entityId": e, "propName": "defaultAccess",
                                               "propValue": "QueryAndStorage"})
json.dump([{"name": z["name"], "description": z.get("description", ""),
            "signature": z["signature"], "config": c}], open(sys.argv[1], "w"))
PY2
then
  api "$HS" PUT /data/api/v1/resources/ignition/security-zone "$WORK/z.json" | wrote \
    && ok "$HUB: EdgeHistorySync grants audit and journal storage" \
    || failed "$HUB: could not add audit and journal storage to EdgeHistorySync"
fi

# --- the X-WD-Token the console uses for the edges' sf/* routes ---------------
# One value, generated on the hub (its copy is the source of truth), installed in
# the wd file provider of the hub, its backup and the Store & Forward edges.
if ! docker exec "$HUB" test -s "$SECRET_DIR/$WD_TOKEN_NAME" 2>/dev/null; then
  python3 -c 'import secrets, sys; sys.stdout.write(secrets.token_urlsafe(32))' \
    | docker exec -i "$HUB" sh -c "mkdir -p $SECRET_DIR && chmod 700 $SECRET_DIR && umask 077 && cat > $SECRET_DIR/$WD_TOKEN_NAME"
  ok "$HUB: generated $WD_TOKEN_NAME (never printed)"
fi
register() {  # register <container> <secret-name> <description>
  local gw="$1" name="$2" st; st="$(stanza_for "$gw")"
  api "$st" GET /data/api/v1/resources/list/ignition/secret-provider > "$WORK/sp.json"
  NAME="$name" DIR="$SECRET_DIR" DESC="$3" python3 - "$WORK/sp.json" "$WORK/sp-put.json" <<'PY' || return 0
import json, os, sys
try:
    doc = json.load(open(sys.argv[1]))
except Exception:
    doc = {}
item = next((i for i in doc.get("items", []) if i.get("name") == "wd"), None)
entry = {"description": os.environ["DESC"], "filePath": os.environ["DIR"] + "/" + os.environ["NAME"],
         "fileType": "CLEARTEXT"}
if item is None:
    body = {"name": "wd", "collection": "core", "enabled": True,
            "description": "Demo-estate secrets: files in the gateway's data volume, "
                           "installed by the Ignition-Demos-Stack scripts.",
            "config": {"profile": {"type": "file"},
                       "settings": {"files": {os.environ["NAME"]: entry}}}}
else:
    files = item.setdefault("config", {}).setdefault("settings", {}).setdefault("files", {})
    if files.get(os.environ["NAME"]) == entry:
        sys.exit(1)
    files[os.environ["NAME"]] = entry
    body = {"name": "wd", "collection": "core", "enabled": True,
            "description": item.get("description", ""), "config": item["config"],
            "signature": item["signature"]}
json.dump([body], open(sys.argv[2], "w"))
PY
  local method=POST; grep -q '"signature"' "$WORK/sp-put.json" && method=PUT
  api "$st" "$method" /data/api/v1/resources/ignition/secret-provider "$WORK/sp-put.json" | wrote \
    && ok "$gw: $name registered in 'wd'" || failed "$gw: could not register $name in 'wd'"
}
SF_EDGES="$(gateways_where SF_TRANSPORT gan) $(gateways_where SF_TRANSPORT mqtt)"
for gw in $HUB $(gateways_with_role backup) $SF_EDGES; do
  gateway_running "$gw" || continue
  if [ "$gw" != "$HUB" ]; then
    docker exec "$HUB" cat "$SECRET_DIR/$WD_TOKEN_NAME" | docker exec -i "$gw" sh -c \
      "mkdir -p $SECRET_DIR && chmod 700 $SECRET_DIR && umask 077 && cat > $SECRET_DIR/$WD_TOKEN_NAME && chmod 600 $SECRET_DIR/$WD_TOKEN_NAME"
  fi
  register "$gw" "$WD_TOKEN_NAME" "Store & Forward demo action token (X-WD-Token) -- scripts/sf-audit-alarms.sh"
done

# --- Edge 1: Edge sync, built in ----------------------------------------------
for gw in $(gateways_where SF_TRANSPORT gan); do
  gateway_running "$gw" || { dim "  $gw not running -- skipped"; continue; }
  st="$(stanza_for "$gw")"
  api "$st" GET /data/api/v1/resources/singleton/ignition/edge-sync-settings > "$WORK/sync.json"
  if AUDIT="$AUDIT" JOURNAL="$JOURNAL" python3 - "$WORK/sync.json" "$WORK/sync-put.json" <<'PY'
import json, os, sys
r = json.load(open(sys.argv[1])); c = dict(r["config"])
want = {"remoteAuditEnabled": True, "remoteAuditProfileName": os.environ["AUDIT"],
        "remoteJournalEnabled": True, "remoteJournalName": os.environ["JOURNAL"]}
if all(c.get(k) == v for k, v in want.items()):
    sys.exit(1)
c.update(want)
json.dump([{"name": r.get("name") or "edge-sync-settings", "signature": r["signature"],
            "config": c}], open(sys.argv[2], "w"))
PY
  then
    api "$st" PUT /data/api/v1/resources/ignition/edge-sync-settings "$WORK/sync-put.json" | wrote \
      && ok "$gw: Edge sync sends audit to '$AUDIT' and alarms to '$JOURNAL'" \
      || failed "$gw: could not write its Edge sync settings"
  else
    ok "$gw: Edge sync already sends audit and alarms"
  fi
done

# --- Edge 2: a read-only API token for the hub's pull -------------------------
# ApiReader is a level of our own, added to the READ permissions only: the token
# reads the audit log (and any config) and every write answers 403.
for gw in $(gateways_where SF_TRANSPORT mqtt); do
  gateway_running "$gw" || { dim "  $gw not running -- skipped"; continue; }
  st="$(stanza_for "$gw")"
  api "$st" GET /data/api/v1/resources/singleton/ignition/security-levels > "$WORK/sl.json"
  if python3 - "$WORK/sl.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); c = d["config"]
if any(l["name"] == "ApiReader" for l in c["securityLevels"]):
    sys.exit(1)
c["securityLevels"].append({"name": "ApiReader", "children": [],
    "description": "Read-only REST API access: the hub reading this edge's audit log "
                   "without the Gateway Network."})
json.dump([{"name": d.get("name") or "security-levels", "signature": d["signature"],
            "config": c}], open(sys.argv[1], "w"))
PY
  then
    api "$st" PUT /data/api/v1/resources/ignition/security-levels "$WORK/sl.json" | wrote \
      && ok "$gw: security level ApiReader added" || failed "$gw: could not add ApiReader"
  fi
  api "$st" GET /data/api/v1/resources/singleton/ignition/security-properties > "$WORK/sp.json"
  if python3 - "$WORK/sp.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); rp = d["config"]["readPermissions"]
if any(l["name"] == "ApiReader" for l in rp["securityLevels"]):
    sys.exit(1)
rp["securityLevels"].append({"name": "ApiReader", "children": []})
json.dump([{"name": d.get("name") or "security-properties", "signature": d["signature"],
            "config": d["config"]}], open(sys.argv[1], "w"))
PY
  then
    api "$st" PUT /data/api/v1/resources/ignition/security-properties "$WORK/sp.json" | wrote \
      && ok "$gw: ApiReader may read the API" || failed "$gw: could not grant ApiReader read"
  fi

  # Transmission audits what arrives over MQTT (a hub write, sent as a Sparkplug
  # command) into the edge's own profile; blank, such writes leave no record.
  # The record then reaches the hub by the same REST pull as the rest.
  TX=com.cirruslink.mqtt.transmission.gateway
  api "$st" GET "/data/api/v1/resources/singleton/$TX/general" > "$WORK/txg.json"
  if python3 - "$WORK/txg.json" <<'PY'
import json, sys
t = open(sys.argv[1]).read(); d = json.loads(t[t.index("{"):]); c = d["config"]
if c.get("auditProfile") == "EdgeAuditProfile":
    sys.exit(1)
c["auditProfile"] = "EdgeAuditProfile"
json.dump([{"name": d.get("name") or "general", "signature": d["signature"],
            "config": c}], open(sys.argv[1], "w"))
PY
  then
    api "$st" PUT "/data/api/v1/resources/$TX/general" "$WORK/txg.json" | wrote \
      && ok "$gw: MQTT Transmission audits incoming writes to EdgeAuditProfile" \
      || failed "$gw: could not set MQTT Transmission's audit profile"
  else
    ok "$gw: MQTT Transmission already audits incoming writes"
  fi

  # A token whose key the hub no longer holds is useless, and the edge keeps only
  # the hash -- so a missing key on either side means a new pair.
  api "$st" GET /data/api/v1/resources/list/ignition/api-token > "$WORK/at.json"
  sig="$(TN="$TOKEN_NAME" python3 -c 'import json,os,sys
try: print(next(i["signature"] for i in json.load(open(sys.argv[1]))["items"] if i["name"]==os.environ["TN"]))
except Exception: print("")' "$WORK/at.json")"
  if [ -n "$sig" ] && docker exec "$HUB" test -s "$SECRET_DIR/$KEY_FILE" 2>/dev/null; then
    ok "$gw: API token '$TOKEN_NAME' present, key on $HUB"
  else
    ( umask 077
      api "$st" POST /data/api/v1/api-token/generate > "$WORK/gen.json"
      TN="$TOKEN_NAME" SIG="$sig" python3 - "$WORK/gen.json" "$WORK/key" "$WORK/tok.json" <<'PY'
import json, os, sys, time
g = json.load(open(sys.argv[1]))
open(sys.argv[2], "w").write(g["key"])
body = {"name": os.environ["TN"], "collection": "core", "enabled": True,
        "description": "Lets the hub read this edge's audit log over the REST API (no "
                       "Gateway Network). The key is held only in the hub's wd secret provider.",
        "config": {"profile": {"type": "basic-token", "secureChannelRequired": False,
                               "securityLevels": [{"name": "ApiReader", "children": []}],
                               "timestamp": int(time.time() * 1000)},
                   "settings": {"tokenHash": g["hash"]}}}
if os.environ["SIG"]:
    body["signature"] = os.environ["SIG"]
json.dump([body], open(sys.argv[3], "w"))
PY
    )
    method=POST; [ -n "$sig" ] && method=PUT
    if api "$st" "$method" /data/api/v1/resources/ignition/api-token "$WORK/tok.json" | wrote; then
      docker exec -i "$HUB" sh -c "mkdir -p $SECRET_DIR && umask 077 && cat > $SECRET_DIR/$KEY_FILE" < "$WORK/key"
      ok "$gw: API token '$TOKEN_NAME' created; its key is on $HUB (never printed)"
    else
      failed "$gw: could not create the API token"
    fi
    rm -f "$WORK/key" "$WORK/gen.json" "$WORK/tok.json"
  fi
done
# The backup runs the pull when it is the active half, so it holds the key too.
for gw in $HUB $(gateways_with_role backup); do
  gateway_running "$gw" || continue
  if [ "$gw" != "$HUB" ]; then
    docker exec "$HUB" cat "$SECRET_DIR/$KEY_FILE" 2>/dev/null | docker exec -i "$gw" sh -c \
      "mkdir -p $SECRET_DIR && chmod 700 $SECRET_DIR && umask 077 && cat > $SECRET_DIR/$KEY_FILE"
  fi
  register "$gw" "$KEY_FILE" "Key for Edge 2's read-only API token (header value $TOKEN_NAME:<key>) -- scripts/sf-audit-alarms.sh"
done

if [ -n "$FAILED" ]; then
  warn "sf-audit-alarms: these did not complete:$FAILED"
  exit 1
fi
ok "audit and alarms: both roads in place"
