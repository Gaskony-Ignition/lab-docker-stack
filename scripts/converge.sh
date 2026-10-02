#!/usr/bin/env bash
#
# Re-apply the gateway settings bootstrap creates, to gateways that are up.
#
#   scripts/converge.sh                    every running stack
#   scripts/converge.sh ignition-edge1 ... just these (a demo that has just started)
#   scripts/converge.sh --starting ...     also the steps that restart what they
#                                          touch on every run (store-and-forward
#                                          arming): only when those gateways have
#                                          just started, so nothing is interrupted
#   --if-needed                            skip gateways already converged on this
#                                          release (a demo start: see below)
#
# ONCE PER GATEWAY PER RELEASE ON A DEMO START. A cold run is ~4 minutes, almost
# all of it one sign-in per step, which is too long to pay on every start. So a
# clean run records the release in .wd-local/converged/<gateway>, and a demo
# start (--if-needed) skips the gateways already on it. `make update` runs
# without it, so a new release re-applies everything once.
#
# WHY. Bootstrap applies every setting once. A machine built before a setting
# existed never got it -- no update and no demo start ran it -- and the fix was
# a command somebody had to type (29/09/2026: two edges still giving their Panel
# session to Vision, because ign-edge-visual.sh arrived after that machine was
# built). `make update` and every demo start now run this, so what bootstrap
# sets, every machine gets.
#
# SAFE TO RUN ANY TIME, BY CONSTRUCTION: each step is one bootstrap already
# runs, reads first and writes only what differs, and restarts no gateway. The
# steps that do restart something are left out: modules-trim and a theme
# install (a restart to load), and sf-arm / sf-gan-history, which rewrite on
# every run and bounce a transmitter or the edge historian -- those run only
# with --starting.
#
# BOOTSTRAP IS BUILT ON IT: it starts one demo's gateways at a time, runs this
# on them with --starting, and stops them again.
#
# A HAND CHANGE ON A DEMO GATEWAY IS PUT BACK. That is the point of it; `make
# drift` reports such changes before an update overwrites them.
#
# Fail soft, like bootstrap: one step failing is reported and the rest still run.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# After lib.sh, which sets -e: a failed step is reported by try(), not fatal.
set +e -uo pipefail

STARTING=0; IFNEEDED=0
while :; do
  case "${1:-}" in
    --starting)  STARTING=1; shift ;;
    --if-needed) IFNEEDED=1; shift ;;
    *) break ;;
  esac
done

MARKS="$REPO_ROOT/.wd-local/converged"
RELEASE="$(cat "$REPO_ROOT/VERSION" 2>/dev/null || echo unreleased)"
if [ "$IFNEEDED" = 1 ]; then
  keep=""
  for g in "$@"; do
    [ "$(cat "$MARKS/$g" 2>/dev/null)" = "$RELEASE" ] || keep="$keep $g"
  done
  if [ -z "$keep" ]; then
    ok "converge: already done on $RELEASE for $*"
    exit 0
  fi
  # shellcheck disable=SC2086
  set -- $keep
fi

WANT=" $* "
FAILED=""
in_scope() { [ "$WANT" = "  " ] || case "$WANT" in *" $1 "*) return 0 ;; *) return 1 ;; esac; }
up() { in_scope "$1" && gateway_running "$1"; }
try() {  # try <description> <command...>
  local what="$1" t0=$SECONDS; shift
  say "$what"
  if "$@"; then dim "  took $((SECONDS - t0))s: $what"; return 0; fi
  # One retry: a gateway sign-in occasionally does not take (measured
  # 29/09/2026, "still anonymous after submitting credentials"), and the
  # same step passes seconds later. Not after a long failure: that was a step
  # waiting out its own timeout (the pair's is 10 minutes), and a second wait
  # only pushed the demo start past its 20-minute limit (30/09/2026).
  if [ $((SECONDS - t0)) -lt 120 ]; then
    sleep 5
    if "$@"; then dim "  took $((SECONDS - t0))s (second try): $what"; return 0; fi
  fi
  warn "$what did not complete ($((SECONDS - t0))s)"
  FAILED="$FAILED
  - $what"
  return 0
}

HUB="$(gateways_with_role hub | head -1)"
if ! gateway_running "$HUB"; then
  warn "the hub is not running -- nothing to converge"
  exit 0
fi

# A module a release switches back on (the OPC UA server, v1.2.1) is in a
# gateway's volume, read at startup: a just-started gateway whose module list
# changes takes one restart now, before anything is configured on it.
if [ "$STARTING" = 1 ]; then
  for g in $(gateways); do
    up "$g" || continue
    trimmed="$("$REPO_ROOT/scripts/ign-modules-trim.sh" "$g" 2>/dev/null || true)"
    case "$trimmed" in
      *CHANGED*)
        say "$g: module list changed -- one restart to load it"
        docker restart "$g" >/dev/null && ( wait_for_gateway "$g" 420 ) \
          || FAILED="$FAILED
  - $g restart for its module list" ;;
    esac
  done
fi

if up postgres; then
  try "the Ignition database and role" "$REPO_ROOT/scripts/pg-ensure.sh"
  try "duplicate-tolerant history tables" "$REPO_ROOT/scripts/pg-history-guard.sh"
fi
try "the .test proxy hosts" bash "$REPO_ROOT/stacks/npm/create-proxy-hosts.sh"
# The connection and the historian both need Postgres answering: without it the
# connection waits minutes for a Valid it cannot reach.
PG=0; gateway_running postgres && PG=1
# The hub's own settings run whatever the scope: every demo leans on them,
# and a demo start that only looked at its own gateways never repaired a hub
# missing its historian (30/09/2026, store and forward could not arm).
[ "$PG" = 1 ] && try "the Postgres database connection" "$REPO_ROOT/scripts/ign-db.sh"

# Each EAM edge also dials the backup. With only the master dialled, the pair's
# route goes to None when the backup takes over: Edge sync stops forwarding and
# EAM finds no controller (02/10/2026), so this runs before EAM below. It runs
# when either end has just come up: the Redundancy demo can start after Store &
# Forward, or the other way round.
for b in $(gateways_with_role backup); do
  gateway_running "$b" || continue
  pair=""
  for e in $(gateways_with_role edge); do
    gateway_running "$e" && { in_scope "$e" || in_scope "$b"; } && pair="$pair $e"
  done
  [ -n "$pair" ] || continue
  for e in $pair; do
    try "$e dials the backup" node "$REPO_ROOT/scripts/ign-gw.js" gan-connect \
      --gateway "$(stanza_for "$e")" --host "$b" --port 8060
  done
  # shellcheck disable=SC2086
  try "Gateway Network certificates with $b" "$REPO_ROOT/scripts/ign-gan.sh" certs $b $pair
  sleep 20
  # shellcheck disable=SC2086
  try "Gateway Network certificates with $b, second pass" "$REPO_ROOT/scripts/ign-gan.sh" certs $b $pair
  try "incoming Gateway Network connections on $b" \
    node "$REPO_ROOT/scripts/ign-gw.js" gan-approve --gateway "$(stanza_for "$b")"
done

# Gateway Network + EAM, for the EAM edges that are in scope.
edges=""
for e in $(gateways_with_role edge); do up "$e" && edges="$edges $e"; done
if [ -n "$edges" ]; then
  for e in $edges; do
    try "$e dials the hub" node "$REPO_ROOT/scripts/ign-gw.js" gan-connect \
      --gateway "$(stanza_for "$e")" --host ignition --port 8060
  done
  # Twice: a peer's certificate only exists on the other side once it has tried
  # to connect, and it cannot try until it trusts the other side. One pass
  # leaves a first pairing configured and permanently disconnected.
  try "Gateway Network certificates" "$REPO_ROOT/scripts/ign-gan.sh" certs
  sleep 20
  try "Gateway Network certificates, second pass" "$REPO_ROOT/scripts/ign-gan.sh" certs
  try "incoming Gateway Network connections" \
    node "$REPO_ROOT/scripts/ign-gw.js" gan-approve --gateway local
  try "the hub as EAM controller" \
    node "$REPO_ROOT/scripts/ign-gw.js" eam-setup --gateway local --role controller
  for e in $edges; do
    try "$e as an EAM agent" node "$REPO_ROOT/scripts/ign-gw.js" eam-setup \
      --gateway "$(stanza_for "$e")" --role agent
  done
  try "EAM agents approved" node "$REPO_ROOT/scripts/ign-gw.js" eam-agents --gateway local --approve
  # An approved agent dials back a few seconds later; a push in that gap sends
  # nothing (the first from-scratch build did exactly that).
  waited=0
  while [ "$waited" -lt 90 ]; do
    listing="$(node "$REPO_ROOT/scripts/ign-gw.js" eam-agents --gateway local 2>/dev/null || true)"
    printf '%s' "$listing" | grep -q Connected \
      && ! printf '%s' "$listing" | grep -qE 'Disconnected|Pending' && break
    sleep 5; waited=$((waited + 5))
  done
  [ "$waited" -lt 90 ] || warn "EAM agents still not all connected after 90s -- pushing anyway"
fi

# Every Edge gateway, EAM or isolated: its one Panel session goes to Perspective.
vis=""
for e in $(gateways_with_role edge) $(sparkplug_edges); do up "$e" && vis="$vis $e"; done
# shellcheck disable=SC2086
[ -n "$vis" ] && try "each edge's Panel session on Perspective" "$REPO_ROOT/scripts/ign-edge-visual.sh" $vis
for e in $edges; do
  try "the site project on $e" "$REPO_ROOT/scripts/eam-push.sh" "$e"
done

try "the hub's MQTT Distributor as a TLS broker" "$REPO_ROOT/scripts/distributor-setup.sh" "$HUB"
if [ "$WANT" = "  " ]; then
  try "the MQTT modules pointed at the broker" "$REPO_ROOT/scripts/ign-mqtt.sh" setup
else
  # Only the gateways just started: the rest were converged before.
  for g in $(gateways); do
    [ -n "$(meta_get "$g" MQTT_MODULE "")" ] || continue
    up "$g" && try "$g's MQTT module pointed at the broker" "$REPO_ROOT/scripts/ign-mqtt.sh" setup "$g"
  done
fi
[ "$PG" = 1 ] && try "the hub historian" "$REPO_ROOT/scripts/sf-historian.sh"
[ "$PG" = 1 ] && try "audit and alarms from the Store & Forward edges" "$REPO_ROOT/scripts/sf-audit-alarms.sh"
# Store-and-forward arming only when its demo is starting: it needs Postgres,
# which the EAM demo does not start, and it costs two minutes.
if [ "$STARTING" = 1 ] && [ -n "$edges" ] && [ "$PG" = 1 ]; then
  try "the edge transmitters armed" "$REPO_ROOT/scripts/sf-arm.sh"
  try "Gateway Network history sync armed" "$REPO_ROOT/scripts/sf-gan-history.sh"
fi

sp=""
for e in $(sparkplug_edges); do up "$e" && sp="$sp $e"; done
# shellcheck disable=SC2086
[ -n "$sp" ] && try "the Sparkplug demo on$sp" "$REPO_ROOT/scripts/sparkplug-setup.sh" $sp

for b in $(gateways_with_role backup); do
  up "$b" || continue
  try "the redundant pair" "$REPO_ROOT/scripts/ign-redundancy.sh" setup
  try "the backup's MQTT Distributor certificate" "$REPO_ROOT/scripts/distributor-setup.sh" "$b"
done

gws=""
for g in $(gateways); do up "$g" && [ -n "$(meta_get "$g" STANZA "")" ] && gws="$gws $g"; done
# shellcheck disable=SC2086
[ -n "$gws" ] && try "the address each gateway advertises" "$REPO_ROOT/scripts/ign-public-address.sh" $gws
try "UI logins in the gateway secret store" bash "$REPO_ROOT/scripts/ign-secrets.sh"
stanzas=""
for g in $gws; do stanzas="$stanzas $(stanza_for "$g")"; done
if [ "$WANT" = "  " ]; then
  try "Designer login through the identity provider" bash "$REPO_ROOT/scripts/ign-designer-idp.sh"
elif [ -n "$stanzas" ]; then
  # shellcheck disable=SC2086
  try "Designer login through the identity provider" bash "$REPO_ROOT/scripts/ign-designer-idp.sh" $stanzas
fi

if [ -n "$FAILED" ]; then
  warn "converge: these did not complete:$FAILED"
  exit 1
fi
# Only a clean --starting run is recorded, so a failed step is tried again next
# time, and a run without --starting (make update) leaves the store-and-forward
# arming to the next demo start rather than marking it done.
# Written by wd-control (root) and by the toolbox (the host user) alike, so
# both must be able to replace each other's files. A marker that cannot be
# written only means the next demo start converges again.
[ "$STARTING" = 1 ] || { ok "converge: every setting in place"; exit 0; }
mkdir -p "$MARKS" 2>/dev/null || true
chmod a+rwx "$MARKS" 2>/dev/null || true
for g in $(gateways) postgres; do
  if up "$g"; then
    rm -f "$MARKS/$g" 2>/dev/null || true
    { printf '%s\n' "$RELEASE" > "$MARKS/$g" && chmod a+rw "$MARKS/$g"; } 2>/dev/null || true
  fi
done
ok "converge: every setting in place"
