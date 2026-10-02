#!/bin/bash
# Creates the .test proxy hosts in Nginx Proxy Manager via its REST API -- one
# per TEST_HOST in stacks/*/stack.meta, with the *.test leaf certificate
# attached and HTTPS forced. Adding a stack adds its name here for free.
#
# Idempotent: re-uploads nothing that exists, skips any domain that exists.
#
# ADDING AND REPOINTING ARE AUTOMATIC; REMOVING IS OPT-IN (--prune). Retiring a
# stack leaves its name behind, the proxy still answers on it, and the down-page
# offers to start a demo that no longer exists -- so every run REPORTS a host no
# manifest declares. It does not delete one unless asked, because this runs from
# bootstrap and from `make signin`, and a proxy table that silently loses entries
# on an unrelated command is worse than a stale row. scripts/hosts-setup.sh has
# the same split for the same reason, and the two must agree.
#
#   bash create-proxy-hosts.sh --prune
# Run after `make env` (for .env) and `make certs` (for the leaf certificate).
#
# The admin credentials never touch argv: every process on the machine can read
# argv, so the login body goes to curl via a 0600 temp file instead.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# Parsed once, into a name. NOT read as $1 where it is used: the repoint loop
# below does `set -- $row`, so positional parameters do not survive this script
# and a check written as "$1" would silently start reading a domain name.
PRUNE=0
for a in "$@"; do
  case "$a" in
    --prune) PRUNE=1 ;;
    *) echo "create-proxy-hosts: unknown option: $a" >&2; exit 2 ;;
  esac
done

[ -f .env ] || { echo "no .env here -- run: make env" >&2; exit 1; }
[ -f certs/leaf.pem ] && [ -f certs/leaf.key ] \
  || { echo "no certs/leaf.pem -- run: make certs" >&2; exit 1; }

EMAIL=$(grep '^INITIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
PASS=$(grep '^INITIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)
# Inside the toolbox `localhost` is the TOOLBOX, so a host-port URL reaches
# nothing -- and curl's empty reply then surfaces as a JSONDecodeError on the
# login response rather than as a wrong address. Same rule as gateway_url in
# lib.sh: container name in there, host port out here.
if [ "${WD_TOOLBOX:-}" = "1" ]; then
  API=http://npm:81/api
else
  # The HOST port, which is not 81. Every published port moved into the 29xxx
  # block, and this line kept the container's -- so running this natively died
  # at the login with a JSONDecodeError on an empty reply, which reads as a
  # broken NPM rather than a wrong address. Invisible because `wd` and
  # bootstrap always run it INSIDE the toolbox, where npm:81 is right.
  API=http://localhost:$(sed -n 's/^NPM_ADMIN_HOST_PORT=//p' .env | tr -d '\r' | tail -1)/api
  case "$API" in *localhost:/api) API=http://localhost:29081/api ;; esac
fi

login=$(mktemp); chmod 600 "$login"
trap 'rm -f "$login" /tmp/npm-resp.json' EXIT
printf '{"identity":"%s","secret":"%s"}' "$EMAIL" "$PASS" > "$login"

TOKEN=$(curl -s -X POST "$API/tokens" -H 'Content-Type: application/json' \
  -d @"$login" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# --- the *.test certificate ---------------------------------------------------
# NPM stores uploaded certificates under provider "other". Find ours by name,
# upload it once if absent. Every proxy host below points at this one entry.
CERT_NAME="wildcard-dot-test"
CERT_ID=$(curl -s "$API/nginx/certificates" -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import sys, json
for c in json.load(sys.stdin):
    if c.get('nice_name') == '$CERT_NAME':
        print(c['id']); break")

if [ -z "$CERT_ID" ]; then
  CERT_ID=$(curl -s -X POST "$API/nginx/certificates" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"provider\":\"other\",\"nice_name\":\"$CERT_NAME\"}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
  curl -s -o /tmp/npm-resp.json -X POST "$API/nginx/certificates/$CERT_ID/upload" \
    -H "Authorization: Bearer $TOKEN" \
    -F certificate=@certs/leaf.pem -F certificate_key=@certs/leaf.key
  printf 'certificate      UPLOADED (id %s)\n' "$CERT_ID"
else
  printf 'certificate      SKIP (exists, id %s)\n' "$CERT_ID"
fi

# domain -> id:host:port for every host that already exists, so a host whose
# target has CHANGED can be corrected rather than skipped. It only ever skipped
# before, which is fine while the table only grows -- and silently wrong the
# first time an entry is repointed, because the run reports SKIP (exists) and
# leaves the old target in place. That is how ignition.test would have gone on
# reaching the master directly after being pointed at the failover proxy.
existing=$(curl -s "$API/nginx/proxy-hosts" -H "Authorization: Bearer $TOKEN" \
  | python3 -c '
import sys, json
for h in json.load(sys.stdin):
    for n in h["domain_names"]:
        print("%s %s %s %s" % (n, h["id"], h["forward_host"], h["forward_port"]))')

# --- the page a stopped .test name shows -------------------------------------
#
# Every .test name belongs to an optional demo, so on a core-only machine they
# are all dead -- correctly, and the browser reports that as "502 Bad Gateway",
# which names no cause and suggests no action. The first person it happened to
# went looking for a broken proxy.
#
# One page serves all of them: the lookup runs in the browser off
# location.hostname, so nginx needs nothing per host beyond the error_page
# below, and the mapping is generated from demos.json rather than written out
# by hand. `error_page` without `=` keeps the original status, so a machine
# still sees a 502 while a human sees which demo to start.
#
# It lives in npm's data volume rather than a bind mount, for the reason the
# volumes in compose.yaml already give.
DOWN_CONF='error_page 502 503 504 /__wd-down.html;
location = /__wd-down.html {
  root /data/wd;
  internal;
}'

# --- the sign-in door, on the GATEWAY hosts only ------------------------------
#
# The console's "Open" buttons point at https://<gateway>.test/_wd/login, which
# lands here and is proxied to the control plane. It has to be served from the
# GATEWAY'S OWN NAME and nowhere else: the response's Set-Cookie is only usable
# by the browser if it arrives on the origin the session belongs to. A single
# shared login host cannot work -- console.test and edge1.test share no parent
# domain a cookie could be scoped to.
#
# ONLY /_wd/login IS EXPOSED, never /_wd/. The control plane can start and stop
# every stack on the machine and is deliberately unauthenticated on `backbone`
# (see control/service.py); publishing all of it on a gateway's public name
# would hand that to anything that can reach the proxy. Routes by name: login,
# and the Trials page with its reset (control/trialkeeper.py) -- an expired
# gateway's own pages are behind the trial screen, so that button lives here.
#
# proxy_pass goes through a VARIABLE on purpose. With a literal upstream nginx
# resolves the name when the config loads, so a reload while wd-control happens
# to be down takes the WHOLE proxy with it -- every .test host, including the
# gateways this is meant to make easier to reach. With a variable plus a
# resolver the name is looked up per request, and a stopped control plane costs
# one 502 on one route instead.
AUTH_CONF='location /_wd/login {
  resolver 127.0.0.11 valid=10s ipv6=off;
  set $wd_control wd-control:8080;
  proxy_pass http://$wd_control/login$is_args$args;
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-Proto $scheme;
}
location ~ ^/_wd/(trials|trials/reset)$ {
  resolver 127.0.0.11 valid=10s ipv6=off;
  set $wd_control wd-control:8080;
  proxy_pass http://$wd_control/$1;
}'

# Which hosts get it: the same rule control/autologin.py uses -- a gateway, or
# a host whose manifest names the credentials to sign in with. The second is
# the redundant pair's front door, which is served by the proxy stack and is
# still an Ignition config UI on the other side.
GATEWAY_HOSTS=$(for f in ../../stacks/*/stack.meta; do
  m=$(tr -d '\r' < "$f")
  case "$m" in *KIND=gateway*|*SIGNIN_STANZA=*) ;; *) continue ;; esac
  printf '%s\n' "$m" | sed -n 's/^TEST_HOST=//p'
done | paste -sd, -)

# --- a host that falls back to another stack ---------------------------------
#
# ignition.test forwards to the pair's proxy, which is part of the Redundancy
# demo and so is down most of the time. TEST_FALLBACK=<stack> sends the
# requests nginx cannot deliver (502/504: the proxy's name does not resolve or
# does not answer) to that stack instead of the down page. Only nginx's OWN
# errors are caught -- no proxy_intercept_errors -- so a 503 from HAProxy while
# it is up still reaches the browser rather than quietly landing on the hub.
FALLBACKS=$(for f in ../../stacks/*/stack.meta; do
  m=$(tr -d '\r' < "$f")
  d=$(printf '%s\n' "$m" | sed -n 's/^TEST_HOST=//p')
  fb=$(printf '%s\n' "$m" | sed -n 's/^TEST_FALLBACK=//p')
  [ -n "$d" ] && [ -n "$fb" ] || continue
  printf '%s=%s:%s\n' "$d" "$fb" \
    "$(sed -n 's/^TEST_FORWARD_PORT=//p' "../../stacks/$fb/stack.meta" | tr -d '\r')"
done | paste -sd, -)

if python3 gen-down-page.py > /tmp/wd-down.html 2>/tmp/wd-down.err; then
  docker exec npm mkdir -p /data/wd
  docker cp /tmp/wd-down.html npm:/data/wd/__wd-down.html >/dev/null
  printf 'down page        INSTALLED (%s bytes)\n' "$(wc -c < /tmp/wd-down.html)"
else
  # Not fatal: a proxy that returns a bare 502 is what it did before this
  # existed. Failing the whole run over the error page would be worse.
  printf 'down page        FAILED -- proxy hosts will return a bare 502\n  %s\n' \
    "$(head -c 200 /tmp/wd-down.err)"
  DOWN_CONF=""
fi

# domain -> forward host -> port, derived from the stack manifests: every
# stack.meta declaring a TEST_HOST gets a proxy host, and the forward host is
# ALWAYS the stack/container name -- which is what routes ignition.test to the
# ignition-ha stack (the active-passive proxy for the redundant PAIR) rather
# than to either half; see stacks/ignition-ha/stack.meta for why the name
# belongs there.
hosts="$(for f in ../../stacks/*/stack.meta; do
  d=$(sed -n 's/^TEST_HOST=//p' "$f" | tr -d '\r')
  [ -n "$d" ] || continue
  printf '%s %s %s\n' "$d" "$(basename "$(dirname "$f")")" \
    "$(sed -n 's/^TEST_FORWARD_PORT=//p' "$f" | tr -d '\r')"
done)"

# --- hosts no manifest declares any more --------------------------------------
# Only .test names are ever considered: NPM is the machine's proxy and may well
# carry a host this repo knows nothing about, which is not ours to remove.
WANT_DOMAINS=" $(printf '%s\n' "$hosts" | awk 'NF{print $1}' | tr '\n' ' ')"
stale_rows="$(printf '%s\n' "$existing" | awk -v want="$WANT_DOMAINS" '
  NF >= 2 && $1 ~ /\.test$/ && index(want, " " $1 " ") == 0 { print $1, $2 }')"
if [ -n "$stale_rows" ]; then
  printf '%s\n' "$stale_rows" | while read -r sname sid; do
    [ -n "${sname:-}" ] || continue
    if [ "$PRUNE" = 1 ]; then
      code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE \
        "$API/nginx/proxy-hosts/$sid" -H "Authorization: Bearer $TOKEN")
      case "$code" in
        200|201|204) printf '%-16s REMOVED (no stack declares it)\n' "$sname" ;;
        *)           printf '%-16s REMOVE FAILED (HTTP %s)\n' "$sname" "$code" ;;
      esac
    else
      printf '%-16s STALE -- no stack declares it (--prune removes it)\n' "$sname"
    fi
  done
fi

echo "$hosts" | while read -r domain fhost fport; do
  [ -z "${domain:-}" ] && continue
  row=$(grep -E "^$domain " <<<"$existing" || true)
  if [ -n "$row" ]; then
    set -- $row
    have_id=$2 have_host=$3 have_port=$4
    if [ "$have_host" = "$fhost" ] && [ "$have_port" = "$fport" ]; then
      printf '%-16s SKIP (exists -> %s:%s)\n' "$domain" "$have_host" "$have_port"
      continue
    fi
    # PUT carries only the fields being changed; NPM merges the rest.
    code=$(curl -s -o /tmp/npm-resp.json -w '%{http_code}' -X PUT \
      "$API/nginx/proxy-hosts/$have_id" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      -d "{\"forward_host\": \"$fhost\", \"forward_port\": $fport}")
    if [ "$code" = "200" ]; then
      printf '%-16s REPOINTED %s:%s -> %s:%s\n' \
        "$domain" "$have_host" "$have_port" "$fhost" "$fport"
    else
      printf '%-16s UPDATE FAILED (HTTP %s) %s\n' \
        "$domain" "$code" "$(head -c 200 /tmp/npm-resp.json)"
    fi
    continue
  fi
  # allow_websocket_upgrade is on for every host: Perspective runs its whole
  # session over a websocket, and it is a no-op where it is not used.
  code=$(curl -s -o /tmp/npm-resp.json -w '%{http_code}' -X POST "$API/nginx/proxy-hosts" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{
      \"domain_names\": [\"$domain\"],
      \"forward_scheme\": \"http\",
      \"forward_host\": \"$fhost\",
      \"forward_port\": $fport,
      \"allow_websocket_upgrade\": true,
      \"block_exploits\": true,
      \"caching_enabled\": false,
      \"access_list_id\": 0,
      \"certificate_id\": $CERT_ID,
      \"ssl_forced\": true,
      \"http2_support\": true,
      \"hsts_enabled\": false,
      \"hsts_subdomains\": false,
      \"advanced_config\": $(case ",$GATEWAY_HOSTS," in *",$domain,"*) printf '%s\n\n%s' "$DOWN_CONF" "$AUTH_CONF";; *) printf '%s' "$DOWN_CONF";; esac | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
      \"meta\": {},
      \"locations\": []
    }")
  if [ "$code" = "200" ] || [ "$code" = "201" ]; then
    printf '%-16s CREATED -> %s:%s (https forced)\n' "$domain" "$fhost" "$fport"
  else
    printf '%-16s FAILED (HTTP %s) %s\n' "$domain" "$code" "$(head -c 200 /tmp/npm-resp.json)"
  fi
done

# --- error page on the hosts that already existed ----------------------------
#
# Separate from the loop above, because advanced_config is a multi-line string
# and `existing` is a line-per-host table -- reading it there would need the
# table to grow a field it cannot hold. This asks NPM directly instead.
#
# It matters that this runs for OLD hosts: every proxy host on a machine built
# before this existed carries advanced_config "", so without a reconcile the
# error page would only ever appear on hosts created afterwards -- which is the
# subset least likely to be the one you hit.
[ -z "$DOWN_CONF" ] || API="$API" TOKEN="$TOKEN" DOWN_CONF="$DOWN_CONF" \
  AUTH_CONF="$AUTH_CONF" GATEWAY_HOSTS="$GATEWAY_HOSTS" FALLBACKS="$FALLBACKS" \
  python3 - <<'PY'
import json, os, urllib.request

API, TOKEN = os.environ["API"], os.environ["TOKEN"]
DOWN = os.environ["DOWN_CONF"]
AUTH = os.environ.get("AUTH_CONF", "")
GATEWAYS = {h for h in os.environ.get("GATEWAY_HOSTS", "").split(",") if h}
FALLBACKS = dict(x.split("=", 1) for x in os.environ.get("FALLBACKS", "").split(",") if x)

# The named location repeats what NPM's own `location /` does (the websocket
# headers and conf.d/include/proxy.conf, which proxies to $server:$port) with
# the fallback's address, so the hub is served exactly as its own host serves it.
FALLBACK = """error_page 502 504 = @wd_fallback;
location @wd_fallback {
  set $server %s;
  set $port %s;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection $http_connection;
  proxy_http_version 1.1;
  include conf.d/include/proxy.conf;
}"""


def wanted(name: str) -> str:
    """What this host's advanced_config should say.

    Gateways get the sign-in door as well as the error page; everything else
    gets the error page alone. Computed per host rather than applied to all,
    because /_wd/login on a host that is not an Ignition gateway would answer
    a confident 404 from the control plane instead of not existing.
    """
    base = DOWN
    if name in FALLBACKS:
        # The down page stays for 503 (nothing nginx makes itself); 502 and
        # 504 are redirected, and error_page's last match wins per code.
        base = DOWN.replace("error_page 502 503 504", "error_page 503") + "\n\n" \
            + FALLBACK % tuple(FALLBACKS[name].split(":", 1))
    return base + "\n\n" + AUTH if (AUTH and name in GATEWAYS) else base


def call(method, path, body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + TOKEN,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


for host in call("GET", "/nginx/proxy-hosts"):
    name = host["domain_names"][0]
    want = wanted(name)
    what = "error page + sign-in" if name in GATEWAYS and AUTH else "error page"
    if name in FALLBACKS:
        what += " + fallback to " + FALLBACKS[name]
    if (host.get("advanced_config") or "").strip() == want.strip():
        print("%-16s SKIP (%s set)" % (name, what))
        continue
    try:
        # NPM validates the snippet by reloading nginx and rejects the save if
        # the config does not parse, so a bad edit here cannot take the proxy
        # down -- it fails loudly and leaves the previous config serving.
        call("PUT", "/nginx/proxy-hosts/%d" % host["id"],
             {"advanced_config": want})
        print("%-16s %s SET" % (name, what.upper()))
    except Exception as e:  # noqa: BLE001
        print("%-16s CONFIG FAILED: %s" % (name, e))
PY
