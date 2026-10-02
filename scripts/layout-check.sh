#!/usr/bin/env bash
#
# Headless layout gate for the demo console: does a screen fit, without a
# human looking at a screenshot to find out.
#
#   scripts/layout-check.sh [PROJECT] [PAGE]
#   make layout-check TAB=MQTT STEPS="1 Live data|2 Alarms|3 Cloud to edge"
#
# TAB and PAGE follow scripts/ign-shot.sh exactly: TAB is the console tab
# button's accessible LABEL, clicked once via getByRole -- Perspective's
# data-component-path is a positional index and matches nothing reliable --
# and PAGE is explicitly-empty-means-root, not unset-means-some-default, for
# the same reason ign-shot.sh distinguishes the two (GatewayAdmin has no
# `demo` route; its only route is `/`).
#
# STEPS is new here: a `|`-separated list of further button labels, each
# clicked ONE AT A TIME within whatever TAB opened -- the demo console's
# sub-tabs -- with the layout probe (and a screenshot) run after every click.
# Labels can carry spaces ("1 Live data"), which is why `|` and not a comma or
# space separates them.
#
# The shell half exists for one reason, same as ign-shot.sh: gateway_url.
# There are two addresses for every gateway and picking the wrong one is
# invisible, so nothing here builds a URL by hand -- lib.sh already knows
# which.
#
# See scripts/layout-check.js for what "fits" means, and why the thresholds
# are what they are.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PROJECT="${PROJECT:-${1:-GatewayAdmin}}"
# `${PAGE+set}`, not `${PAGE:-}`: an explicitly empty PAGE is still "empty",
# so this is only here for symmetry with ign-shot.sh -- PAGE's default really
# is empty (root), so both spellings land the same place. Kept explicit so a
# future default does not silently change what PAGE= means.
if [ -n "${PAGE+set}" ]; then PAGE="$PAGE"; else PAGE="${2:-}"; fi
GATEWAY="${GATEWAY:-ignition}"
TAB="${TAB:-}"
STEPS="${STEPS:-}"
SIZES="${SIZES:-1366x640,1920x1080}"
WAIT="${WAIT:-8000}"
OUT_DIR="${OUT_DIR:-.shots/layout}"

need_docker
cd "$REPO_ROOT"

require_gateway "$GATEWAY"
URL="$(gateway_url "$GATEWAY")"

mkdir -p "$OUT_DIR"

say "$GATEWAY -> $PROJECT/$PAGE${TAB:+ (tab: $TAB)}${STEPS:+ (steps: $STEPS)} @ $SIZES"
node "$REPO_ROOT/scripts/layout-check.js" \
  --url "$URL" --project "$PROJECT" --page "$PAGE" \
  --wait "$WAIT" --sizes "$SIZES" --out-dir "$OUT_DIR" \
  ${TAB:+--tab "$TAB"} ${STEPS:+--steps "$STEPS"}
