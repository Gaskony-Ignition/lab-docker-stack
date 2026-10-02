#!/usr/bin/env bash
#
# Point each Edge gateway's single Panel session at PERSPECTIVE.
#
#   scripts/ign-edge-visual.sh                 every running edge
#   scripts/ign-edge-visual.sh ignition-edge1  just this one
#   scripts/ign-edge-visual.sh --check         REPORT only -- writes nothing
#
# --check exists because an engineer changing this by hand is purpose 2 of this
# rig, and a re-run of the setup would overwrite it with no notice. It reads the
# same singleton the apply path reads, compares it the same way, prints one
# `drift:` line per gateway that differs, and exits 1 if any did. Nothing else
# about the script changes: scripts/drift.sh is the only caller that passes it.
#
# WHY THIS EXISTS, AND WHAT IT COST TO FIND
#
# An Ignition Edge gateway carries ONE Panel session, and `visualizationName` in
# the `ignition/edge-system-properties` singleton decides which visualization
# module gets it. It defaults to **VISION**. Perspective then has no session to
# give, and every browser that asks for one is answered with Edge's
#
#     Sessions Exceeded -- the number of running client sessions has exceeded
#     the permitted number
#
# splash, on a gateway whose trial is healthy, whose Perspective module started
# cleanly, and which is in every other respect working. It is the perfect
# false negative: it names a limit that has not been reached, and reads as a
# licensing wall rather than a one-word setting.
#
# It cost a full wrong conclusion here. Both edges were written up as "an
# unlicensed Edge grants zero Perspective sessions", supported by a controlled
# experiment against two throwaway gateways -- which proved nothing, because
# both inherited the same VISION default, so the thing being varied (the
# EDITION) was never the thing that mattered. Nigel said flatly that Edge in
# trial serves Perspective and that he had done it many times; he was right.
# Measured 31/08/2026: one PUT, no restart, and the edge rendered the full
# dashboard, 122 components, zero errors.
#
# THE GENERAL RULE THIS EARNS: when an experiment "isolates" a variable, check
# that the thing you did not vary is not the cause. Two gateways sharing a
# default are not a control.
#
# And it is here in a script, not in a note, because of the rule this repo
# already carries: gateway state that no bootstrap script creates does not
# survive a rebuild. This setting is invisible until someone opens an edge in
# front of a customer.
#
# Singletons READ from /resources/singleton/<module>/<type> and are modified with
# **PUT** to /resources/<module>/<type> carrying a ONE-ELEMENT ARRAY. POST is
# CREATE and answers `CREATE conflict: ... already exists`, which reads like the
# wrong endpoint and is the wrong verb.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

WANT="${EDGE_VISUALIZATION:-PERSPECTIVE}"
TYPE=ignition/edge-system-properties

# Only `--check` is intercepted. Anything else keeps travelling through to
# TARGETS exactly as before, including a stray `-x`, which used to become a
# gateway name and be reported as not running -- turning that into a `die` now
# would be a behaviour change for no gain.
CHECK=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --check) CHECK=1 ;;
    *)       ARGS+=( "$a" ) ;;
  esac
done
set -- ${ARGS[@]+"${ARGS[@]}"}
DRIFT=0

need_docker
cd "$REPO_ROOT"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

api() {  # api <stanza> <method> <path> [body-file]
  local stanza="$1" method="$2" path="$3" body="${4:-}"
  if [ -n "$body" ]; then
    node scripts/ign-gw.js api --gateway "$stanza" --method "$method" \
      --path "$path" --body-file "$body" 2>&1
  else
    node scripts/ign-gw.js api --gateway "$stanza" --method "$method" \
      --path "$path" 2>&1
  fi
}

apply_one() {  # apply_one <container>
  local edge="$1" stanza; stanza="$(stanza_for "$edge")"

  # `tail -n +2` drops the api helper's status line; the rest is the body.
  api "$stanza" GET "/data/api/v1/resources/singleton/$TYPE" | tail -n +2 \
    > "$WORK/$edge.json"

  local current
  current="$(python3 -c '
import json, sys
try:
    print(json.load(open(sys.argv[1]))["config"].get("visualizationName", "?"))
except Exception:
    print("unreadable")' "$WORK/$edge.json")"

  if [ "$current" = "unreadable" ]; then
    warn "$edge -- could not read $TYPE"
    return 0
  fi
  if [ "$current" = "$WANT" ]; then
    ok "$edge already serves $WANT"
    return 0
  fi

  # THE GATE. Everything above is a read; everything below writes. Under --check
  # we stop here and say what differs, in the words the Config UI uses.
  if [ "$CHECK" -eq 1 ]; then
    printf '  drift: %s Edge visualization is %s, not %s (Config > Edge > Visualization)\n' \
      "$edge" "$current" "$WANT"
    DRIFT=1
    return 0
  fi

  python3 -c '
import json, sys
row = json.load(open(sys.argv[1]))
cfg = dict(row["config"])
cfg["visualizationName"] = sys.argv[3]
# The signature is an optimistic lock: send back the one just read, or the
# write is refused as a mismatch.
json.dump([{"signature": row["signature"], "config": cfg}], open(sys.argv[2], "w"))
' "$WORK/$edge.json" "$WORK/$edge.put.json" "$WANT"

  api "$stanza" PUT "/data/api/v1/resources/$TYPE" "$WORK/$edge.put.json" >/dev/null 2>&1 || true
  sleep 2

  # Judge by a re-read, never by the write's return: this is a config push and
  # a rejected one still answers 200 at the HTTP layer.
  local now
  now="$(api "$stanza" GET "/data/api/v1/resources/singleton/$TYPE" | tail -n +2 | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin)["config"].get("visualizationName", "?"))
except Exception:
    print("unreadable")')"
  if [ "$now" = "$WANT" ]; then
    ok "$edge $current -> $WANT"
  else
    warn "$edge did NOT take the change (still $now)"
    dim "  its Perspective sessions will answer 'Sessions Exceeded' -- which is"
    dim "  this setting, not a licence limit. Config > Edge > Visualization."
  fi
}

TARGETS="${*:-}"
# Both roles: an edge-isolated gateway (the Sparkplug demo's edges -- no
# Gateway Network, no EAM) still carries an Edge gateway's single Panel
# session and needs this exactly as much as an ordinary ROLE=edge spoke does.
# It is the one place that has to name both explicitly, because every other
# GAN/EAM-only script already keys on ROLE=edge alone and must NOT change.
[ -n "$TARGETS" ] || TARGETS="$(gateways_with_role edge) $(gateways_with_role edge-isolated)"

if [ "$CHECK" -eq 1 ]; then
  say "Edge Panel visualization -- checking only ($WANT)"
else
  say "Edge Panel visualization ($WANT)"
fi
for e in $TARGETS; do
  if gateway_running "$e"; then
    apply_one "$e"
  else
    dim "  $e is not running -- skipped"
  fi
done

# `if`, not `[ ... ] && exit 1`: a && list whose test fails IS the whole
# statement, so under `set -e` the no-drift case would end the script here with
# the status of a failed test. Same trap CLAUDE.md names.
if [ "$CHECK" -eq 1 ] && [ "$DRIFT" -ne 0 ]; then exit 1; fi
exit 0
