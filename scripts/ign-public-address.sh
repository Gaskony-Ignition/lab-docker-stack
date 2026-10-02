#!/usr/bin/env bash
#
# Tell every gateway the address a BROWSER should use, and stop it guessing.
#
#   scripts/ign-public-address.sh                 every running gateway
#   scripts/ign-public-address.sh ignition-edge1  just this one
#   scripts/ign-public-address.sh --check         REPORT only -- writes nothing
#
# --check runs the same `matches()` predicate the apply path runs and stops
# there: one `drift:` line per gateway whose public address is not what this
# script would write, exit 1 if any. It is what scripts/drift.sh calls; nothing
# else about the script changes.
#
# WHY AUTO-DETECT IS WRONG HERE, ALWAYS
#
# `autoDetectPublicAddress` makes a gateway advertise the address it observes
# itself at. In a container that is a docker-internal address -- `ignition:8088`,
# or a 172.x one -- which is correct from inside the network and unreachable
# from the machine the browser is on. Two things are then built from it and both
# break in ways that do not name the cause:
#
#   * the gateway home page's **Launch Perspective** button, which lands on an
#     error instead of a session; and
#   * a redundant standby's REDIRECT, which sends a client to the active half's
#     public address -- a different origin the browser then blocks, leaving the
#     session at "Connecting" forever (verified 05/08/2026, and it reads exactly
#     like a hung gateway rather than a CORS refusal).
#
# So every gateway gets an explicit address, from PUBLIC_HOST in its stack.meta.
# The hub and the backup deliberately share ONE name -- the pair's front door --
# because that is the only way a session survives a failover; the edges carry
# their own proxy names, being nothing to do with the pair.
#
# The write is POST /data/config/web-server. Changing it restarts the web server,
# which drops the connection mid-response, so the POST regularly reports a
# failure for a change that worked. NEVER read the result from the POST -- the
# re-read below is the only honest check.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PUBLIC_HTTP_PORT="${PUBLIC_HTTP_PORT:-80}"
PUBLIC_HTTPS_PORT="${PUBLIC_HTTPS_PORT:-443}"

# Only `--check` is intercepted; everything else still becomes a target, as it
# always did.
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
body() { tail -1; }

matches() {  # matches <json-on-stdin> <host>
  python3 -c '
import json, sys
host = sys.argv[1]
try:
    d = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if (d.get("publicAddress") == host
                       and not d.get("autoDetectPublicAddress", True)) else 1)' "$1" 2>/dev/null
}

# What the gateway says NOW, in the words the Config UI uses. Pure -- it reads
# the same body `matches` just judged, so --check costs no extra gateway call.
describe() {  # describe <json-on-stdin>
  python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("its web-server config was unreadable"); raise SystemExit
addr = d.get("publicAddress") or "(empty)"
auto = "on" if d.get("autoDetectPublicAddress", True) else "off"
print("public address is %s, auto-detect is %s" % (addr, auto))' 2>/dev/null \
    || printf 'its web-server config was unreadable\n'
}

apply_one() {  # apply_one <container>
  local gw="$1" stanza host current
  host="$(meta_get "$gw" PUBLIC_HOST "")"
  if [ -z "$host" ]; then
    warn "$gw has no PUBLIC_HOST in its stack.meta -- skipped"
    return 0
  fi
  stanza="$(stanza_for "$gw")"

  current="$(api "$stanza" GET /data/config/web-server | body)"
  if printf '%s' "$current" | matches "$host"; then
    ok "$gw already advertises $host (auto-detect off)"
    return 0
  fi

  # THE GATE. `matches` has already given the whole answer; --check stops here
  # rather than building a body and POSTing it.
  if [ "$CHECK" -eq 1 ]; then
    printf '  drift: %s %s -- should be %s with auto-detect off (Config > Networking > Web Server)\n' \
      "$gw" "$(printf '%s' "$current" | describe)" "$host"
    DRIFT=1
    return 0
  fi

  printf '%s' "$current" | python3 -c '
import json, sys
host, http, https = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
d = json.load(sys.stdin)
d["publicAddress"] = host
d["publicHttpPort"] = http
d["publicHttpsPort"] = https
d["autoDetectPublicAddress"] = False
json.dump(d, sys.stdout)' "$host" "$PUBLIC_HTTP_PORT" "$PUBLIC_HTTPS_PORT" \
    > "$WORK/$gw.json" || { warn "$gw -- could not read its web-server config"; return 0; }

  api "$stanza" POST /data/config/web-server "$WORK/$gw.json" >/dev/null 2>&1 || true
  sleep 3

  if api "$stanza" GET /data/config/web-server | body | matches "$host"; then
    ok "$gw now advertises $host:$PUBLIC_HTTPS_PORT (auto-detect off)"
  else
    warn "$gw did NOT take the public address"
    dim "  Launch Perspective on its home page will lead to an unreachable"
    dim "  address. Set it by hand: Config > Networking > Web Server."
  fi
}

TARGETS="${*:-}"
if [ -z "$TARGETS" ]; then
  TARGETS="$(gateways)"
fi

if [ "$CHECK" -eq 1 ]; then
  say "public address per gateway -- checking only (auto-detect OFF everywhere)"
else
  say "public address per gateway (auto-detect OFF everywhere)"
fi
for gw in $TARGETS; do
  if gateway_running "$gw"; then
    apply_one "$gw"
  else
    dim "  $gw is not running -- skipped"
  fi
done

# `if`, not `&&` -- see the note in ign-edge-visual.sh and CLAUDE.md.
if [ "$CHECK" -eq 1 ] && [ "$DRIFT" -ne 0 ]; then exit 1; fi
exit 0
