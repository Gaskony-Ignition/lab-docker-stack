#!/usr/bin/env bash
# Install the vendored Gaskony themes onto a gateway's CONFIG resources.
#
#   scripts/ign-themes.sh              every gateway in the manifests
#   scripts/ign-themes.sh ignition     just this one
#
# WHY THIS IS SEPARATE FROM ign-deploy.sh, WHICH IS THE WHOLE POINT OF THEMES.
#
# A theme is a gateway CONFIG resource, not a project resource. It lives under
# data/config/resources/core/, it is visible to every project on the gateway,
# and -- this is the part that shapes everything -- it CANNOT travel in a
# project export or an inheritance chain. EAM's Send Project carries project
# resources and nothing else, so a theme installed on the hub never reaches an
# edge by pushing a project.
#
# That is not a problem to work around, it is the seam:
#
#   the PAINT   is platform config    -> installed on every gateway, by this
#   the CHOICE  is a project resource -> demo_styles.CHOSEN_PACK, carried by EAM
#
# which is how a real site would run it, and it means picking a theme and
# pushing to the edges works exactly as it did before.
#
# THE SCAN, AND WHY BOOTSTRAP ORDER MATTERS
#
# A theme added to a RUNNING gateway needs the Platform -> Overview "Scan File
# System" -- the CONFIG scan, a different button from the project scan, and one
# with no REST route and no in-process API (both jars checked). There is no
# `system.*` call to wrap in a timer the way Ops/AutoScan wraps
# requestScan(), so the trick this repo uses for projects is not available.
#
# But a theme present when the gateway STARTS registers with no scan at all --
# measured 25/08/2026: installed, 404; restarted; 200, serving 90,149 bytes.
# So bootstrap installs themes BEFORE the one restart it already performs for
# Ops, and the scan step simply does not exist. On a gateway that is already up,
# this script says so and names the restart.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SRC="$REPO_ROOT/ignition/themes"
DEST=/usr/local/bin/ignition/data/config/resources/core/com.inductiveautomation.perspective/themes

[ -d "$SRC" ] || die "no $SRC -- run: python3 scripts/gen-themes.py --themes <ignition-themes> --classes <styles-template-v2>"

# The gateway's own shipped themes. NEVER touched: they are gateway-owned, they
# are what our themes @import as a base, and a custom name is safe across an
# upgrade only because it cannot collide with one of these.
RESERVED="light dark light-cool light-warm dark-cool dark-warm"

themes_to_install() {
  local d
  for d in "$SRC"/*/; do
    [ -d "$d" ] || continue
    printf '%s\n' "$(basename "$d")"
  done
}

install_into() {  # install_into <container>
  local gw="$1" t n=0 restarted_needed=0
  # `docker cp` writes as root and the gateway runs as a non-root user, which
  # then cannot rewrite them -- the same trap ign-deploy.sh chowns around.
  for t in $(themes_to_install); do
    case " $RESERVED " in
      *" $t "*) die "refusing to install '$t': that is a gateway-owned theme name" ;;
    esac
    docker exec "$gw" sh -c "rm -rf '$DEST/$t'" 2>/dev/null || true
    docker cp "$SRC/$t" "$gw:$DEST/$t"
    docker exec -u root "$gw" chown -R ignition:ignition "$DEST/$t"
    n=$((n + 1))
  done

  # Judge by the END STATE, not by the copy's exit code -- and probe EVERY
  # theme, not one of them.
  #
  # This used to check only the first theme alphabetically, which made it
  # useless for the case it matters most: adding a NEW theme to a gateway that
  # already has the others. The old one answers 200, the script reports
  # "installed and serving", and the new one is a 404 nobody looked for. Found
  # exactly that way, adding newsprint-dark to a running hub.
  #
  # AND 200 IS NOT ENOUGH EITHER, which is the same bug one level in. Updating
  # a theme that ALREADY EXISTS leaves the gateway serving what it compiled at
  # registration: the new bytes are on disk, the URL answers 200, and the CSS
  # that reaches the browser is the old one. Measured 25/08/2026 taking
  # themes-1.1.0 -- newsprint-dark's accent was #b1554a on disk and #e8e2d6 in
  # every session, with this script reporting "all 10 serving".
  #
  # So compare CONTENT -- and SAMPLE the file rather than betting on one
  # variable. The first version of this check fingerprinted --callToAction
  # alone, which caught newsprint-dark's new accent and would have said
  # "current" for leather-dark and leather-light in the very same release:
  # their change was confined to --div-9..16, a diverging ramp whose far pole
  # moved from red to blue, with every other declaration identical. A check
  # that only looks where the last bug was is not a check.
  #
  # Every 9th declaration gives ~12 probes spread through the file for one
  # fetch, which is enough to catch a change anywhere in it. They are compared
  # as `--name: value` pairs, so a value that merely occurs elsewhere in the
  # sheet cannot mask a stale one.
  local url t served=0 missing="" stale="" tmp code miss
  url="$(gateway_url "$gw")"
  tmp="$(mktemp)"
  for t in $(themes_to_install); do
    # --max-time: this runs once per theme in the loop below, unbounded, so a
    # gateway that stops answering mid-check used to hang here once per theme
    # left to probe rather than failing the one check and moving on.
    code="$(curl -s --max-time 10 -o "$tmp.raw" -w '%{http_code}' \
            "$url/data/perspective/themes/$t.css" || true)"
    if [ "$code" != "200" ]; then
      missing="$missing $t"
      continue
    fi
    # NORMALISE BOTH SIDES, not one. The source pads every declaration into a
    # column (`--callToAction:` then 23 spaces) and the served sheet keeps that
    # padding, so squeezing the whitespace on the local side alone made all ten
    # themes -- including seven that had not changed and were serving perfectly
    # -- report stale. A comparison is only as good as its least normalised end.
    sed 's/:[[:space:]]*/: /' "$tmp.raw" > "$tmp"
    # Both greps are ALLOWED to find nothing, so each pipeline ends `|| true`
    # and the count is what decides -- see CLAUDE.md on the bug this repo
    # keeps writing. `grep -c`, never `grep -q`: an early-exiting grep -q
    # SIGPIPEs its upstream and the pipeline returns 141, so a real mismatch
    # would read as a match.
    miss=0
    while IFS= read -r decl; do
      [ -n "$decl" ] || continue
      if [ "$(grep -c -F -- "$decl" "$tmp" || true)" = "0" ]; then
        miss=$((miss + 1))
      fi
    done <<EOF
$(grep -oE -- '--[a-zA-Z0-9-]+:[[:space:]]*[^;]+' "$SRC/$t/variables.css" 2>/dev/null \
  | sed 's/:[[:space:]]*/: /' | awk 'NR % 9 == 1' || true)
EOF
    if [ "$miss" -gt 0 ]; then
      stale="$stale $t"
    else
      served=$((served + 1))
    fi
  done
  rm -f "$tmp" "$tmp.raw"

  if [ -z "$missing" ] && [ -z "$stale" ]; then
    ok "$gw -- $n themes installed, all $served serving the installed bytes"
  else
    warn "$gw -- $n themes installed, $served current;${missing:+ NOT REGISTERED:$missing}${stale:+ STALE (serving older CSS):$stale}
     A theme registers -- and a CHANGED theme recompiles -- when the gateway
     STARTS, or on a Platform -> Overview 'Scan File System' (UI only: no REST
     route, no in-process API, so no timer can drive it the way Ops/AutoScan
     drives a PROJECT scan). During bootstrap this is expected: the restart
     that follows picks them up.
     On a running gateway: docker restart $gw"
  fi
}

main() {
  need_docker
  local targets
  if [ $# -gt 0 ]; then
    targets="$*"
  else
    # Derived from the manifests, like every other list here -- a gateway added
    # later gets its themes with no edit to this script.
    targets="$(gateways)"
  fi
  say "installing $(themes_to_install | wc -l | tr -d ' ') themes"
  local gw
  for gw in $targets; do
    if ! docker ps --format '{{.Names}}' | grep -qx "$gw"; then
      warn "$gw is not running -- skipped"
      continue
    fi
    install_into "$gw"
  done
}

main "$@"
