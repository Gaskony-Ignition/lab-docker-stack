#!/usr/bin/env bash
#
# Give the hub a tag historian backed by the Postgres connection.
#
#   scripts/sf-historian.sh
#
# The Store & Forward page's trend is the demonstration: a gap while an edge is
# cut off, then the SAME gap filled in once it reconnects. That only reads as
# "the data was buffered and arrived late" if the values land at their ORIGINAL
# timestamps, which needs a real historian -- a table written by a script would
# stamp them "now" and show a flat line instead.
#
# Two Ignition behaviours make the backfill land correctly:
#
#   * MQTT Engine's `storeHistoricalEvents` (on by default) routes a Sparkplug
#     metric flagged historical straight to the historian carrying its own
#     timestamp, rather than updating the live tag value.
#   * The SQL historian writes the value's timestamp, not the collection time.
#
# Idempotent: creating a historian that already exists is left alone.
#
# The resource API here is the same one the gateway's own config UI calls --
# PUT/POST /data/api/v1/resources/<module>/<type> with the body as an ARRAY.
# The generic per-name path (.../<type>/<name>) does NOT exist and 404s, which
# is easy to mistake for "the endpoint is wrong".

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

GATEWAY_STANZA="local"
DATABASE="Postgres"
NAME="Postgres"
TYPE="com.inductiveautomation.historian/historian-provider"

need_docker
require_gateway ignition

cd "$REPO_ROOT"

say "checking for an existing '$NAME' historian"
if node scripts/ign-gw.js api --gateway "$GATEWAY_STANZA" \
     --path "/data/api/v1/resources/list/$TYPE" 2>/dev/null \
   | grep -q "\"name\":\"$NAME\""; then
  ok "historian '$NAME' already exists -- nothing to do"
  exit 0
fi

# Monthly partitions AND monthly pruning. This is a demonstration writing three
# signals a second from each edge, indefinitely, and none of it is worth keeping
# once the day is over. Pruning drops whole partitions rather than deleting rows,
# so it costs nothing at run time and the table count stays bounded instead of
# growing a new one every month forever.
#
# No comments inside the body below -- it is JSON, and a `#` line there is sent
# verbatim and rejected as a parse error.
BODY="$(mktemp)"; trap 'rm -f "$BODY"' EXIT
cat > "$BODY" <<EOF
[{"name":"$NAME",
  "description":"Tag history for the store-and-forward demonstration",
  "enabled":true,
  "config":{
    "profile":{"type":"SqlHistorian"},
    "settings":{
      "database":"$DATABASE",
      "partition":{"enabled":true,"size":1,"sizeUnits":"MONTH",
                   "partitionSeedQueryLimit":2,"optimized":false,
                   "optimizedWindowSeconds":60},
      "pruning":{"enabled":true,"age":1,"ageUnits":"MONTH"},
      "trackSce":true,
      "staleMultiplier":2}}}]
EOF

say "creating the '$NAME' historian against database '$DATABASE'"
node scripts/ign-gw.js api --gateway "$GATEWAY_STANZA" --method POST \
  --path "/data/api/v1/resources/$TYPE" --body-file "$BODY" >/dev/null \
  || die "the gateway refused the historian"

# Verify from a second source rather than the status code: the historian only
# really exists once it has built its schema in the database.
say "waiting for the historian to build its schema"
for _ in $(seq 1 20); do
  if docker exec postgres psql -U ignition -d ignition -tAc \
       "select 1 from information_schema.tables where table_name='sqlth_te'" \
       2>/dev/null | grep -q 1; then
    ok "historian '$NAME' is live -- sqlth_* tables present in Postgres"
    exit 0
  fi
  sleep 2
done

die "historian created but no sqlth_* tables appeared -- check the gateway log"
