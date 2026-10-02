#!/usr/bin/env bash
#
# Show or reset a gateway's Perspective trial.
#
#   scripts/ign-trial.sh              status of all three gateways
#   scripts/ign-trial.sh reset [gw]   reset one (default: ignition)
#   scripts/ign-trial.sh reset --all  reset every gateway
#
# These gateways are unlicensed, so Perspective runs on a rolling 2-hour trial.
# When it lapses every session is replaced by a "Trial Expired" splash --
# including the headless screenshots used to verify a view, which then look
# exactly like a broken deploy. Check here before believing a render.
#
# Reset needs a browser: 8.3 authenticates its API with a session cookie, and
# HTTP Basic gets 401. scripts/ign-trial-reset.js does the login and the POST.
# Credentials go via a 0600 temp file, never argv.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# One map, in lib.sh, reading .gateways.env -- which already knows both the
# container address and this machine's host port for every gateway, and which
# knows about the redundant backup. The three-case ladder that used to live here
# did not, so the backup came out as "unreachable at http://localhost:" with no
# port: a lookup that missed, presented as a gateway that was down.
url_of() { gateway_url "$1"; }

# THREE outcomes, not two, and the third is why this used to die mid-cold-start.
# A gateway that is still starting answers **503 with an HTML page**, which curl
# fetches SUCCESSFULLY -- so the old empty-string test passed it straight to
# json.loads, which raised, and `set -euo pipefail` took the whole run with it.
# `make trial` is a cold-start command; a starting gateway is the normal case at
# the moment it is asked, not an error. Ask for the status code alongside the
# body so one request settles which of the three this is.
status_one() {
  local gw="$1" url; url="$(url_of "$gw")"
  local resp code json
  resp="$(curl -s --max-time 5 -w '\n%{http_code}' "$url/data/api/v1/trial" 2>/dev/null || true)"
  if [ -z "$resp" ]; then
    printf '  %-16s %s\n' "$gw" "unreachable at $url"
    return
  fi
  code="${resp##*$'\n'}"
  json="${resp%$'\n'*}"
  if [ "$code" != "200" ]; then
    printf '  %-16s %s\n' "$gw" "not ready yet (HTTP $code) -- starting, or still commissioning"
    return
  fi
  python3 -c "
import json,sys
raw = sys.stdin.read()
try:
    d = json.loads(raw)
except ValueError:
    # 200 with a body that is not JSON. Report the gateway, never raise: one
    # odd answer must not cost the other three their line of output.
    print(f'  {\"$gw\":<16} unreadable answer from the trial route')
    sys.exit(0)
mins = int(d.get('trialSecondsLeft', 0)) // 60
if d.get('licenseMode') != 'Trial':
    print(f'  {\"$gw\":<16} licensed ({d.get(\"licenseMode\")})')
elif d.get('expired'):
    print(f'  {\"$gw\":<16} EXPIRED -- sessions show the Trial Expired splash')
else:
    print(f'  {\"$gw\":<16} {mins} min left')
" <<< "$json"
}

# Container name -> credentials stanza is `stanza_for` in lib.sh. It lived in
# four scripts as four identical `case` blocks, and adding the redundant backup
# meant finding all four -- the third one was missed on the first pass and its
# gateway simply never appeared in the output.
cred_name() { stanza_for "$1"; }

# Whether this gateway's trial has actually lapsed. GET /data/api/v1/trial is an
# OPEN_ROUTE, so this needs no credentials on any of the three.
trial_expired() {
  local json; json="$(curl -s --max-time 5 "$(url_of "$1")/data/api/v1/trial" 2>/dev/null || true)"
  [ -n "$json" ] || return 1
  # grep -c, not grep -q: an early-exiting grep -q SIGPIPEs its upstream, so the
  # pipeline returns 141 under pipefail and a real expiry reads as "not expired"
  # (see CLAUDE.md). A starting gateway's HTML body simply counts zero here.
  local n; n="$(printf '%s' "$json" | grep -c '"expired"[[:space:]]*:[[:space:]]*true' || true)"
  [ "${n:-0}" -gt 0 ]
}

reset_one() {
  local gw="$1"
  require_gateway "$gw"

  # A healthy gateway has nothing to reset and no way to do it: the licensing
  # page only grows a Reset control once the trial has lapsed, so driving the
  # UI here fails, dumps a page of buttons and links, and reads like a broken
  # script. Nothing resets a trial automatically: a person runs this, presses
  # Reset on the Trials page (docs/TRIALS.md), or uses the console's EAM tab.
  if ! trial_expired "$gw"; then
    dim "  $gw -- trial still has time; nothing to reset"
    # 8.3.9 refuses a reset that early on any gateway, the standby included;
    # reset each one once it lapses (docs/TRIALS.md).
    return 0
  fi

  node "$REPO_ROOT/scripts/ign-gw.js" trial-reset --gateway "$(cred_name "$gw")" \
    && ok "$gw" || warn "$gw -- reset failed"
}

case "${1:-status}" in
  status)
    say "Perspective trial status"
    for gw in "${GATEWAYS[@]}"; do status_one "$gw"; done
    echo
    dim "Unlicensed gateways get a rolling 2-hour Perspective trial."
    dim "Reset with: scripts/ign-trial.sh reset --all"
    dim "The redundant BACKUP's trial is its own: reset it once it lapses (https://console.test/_wd/trials)"
    ;;
  reset)
    shift
    if [ "${1:-}" = "--all" ]; then
      for gw in "${GATEWAYS[@]}"; do gateway_running "$gw" && reset_one "$gw"; done
    else
      reset_one "${1:-ignition}"
    fi
    echo
    "$0" status
    ;;
  *)
    die "usage: ign-trial.sh [status | reset [gateway|--all]]"
    ;;
esac
