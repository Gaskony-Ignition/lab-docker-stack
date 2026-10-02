#!/usr/bin/env bash
#
# Make a gateway's Cirrus Link MQTT Distributor a TLS broker the rest of the
# stack can use -- docs/MQTT-DISTRIBUTOR.md, round 2.
#
#   scripts/distributor-setup.sh ignition          the hub -- the demo's broker (T1)
#   scripts/distributor-setup.sh ignition-backup   the backup, which needs its OWN
#                                                  certificate (below); the rest
#                                                  syncs from the master
#   scripts/distributor-setup.sh ignition-broker   the parked standalone broker (T2)
#   scripts/distributor-setup.sh --status <gw>     what it has now, changes nothing
#
# --status COVERS ALL THREE WRITE STEPS, and used to cover one. It printed the
# certificate's state and SANs without comparing them to want_sans(), and listed
# the users without saying whether the two things the apply path acts on -- is
# MQTT_USER there, is the shipped `admin` still there -- were right. So it read
# as a clean report on a gateway that the next apply would change in three
# places. It now exits non-zero when anything differs and 0 when nothing does,
# which is what scripts/drift.sh reads. The one existing caller,
# migrations/1.0.0-04-distributor-tls.sh, already ends the call in `|| true`.
#
# Idempotent: each step reads first and writes only what differs. Every write to
# a Distributor user or to its general settings RESTARTS THE WHOLE BROKER (every
# client dropped, T-D5), so nothing is written that is already right.
#
# WHAT IT SETS, AND WHY EACH ONE
#
#   1. THE GATEWAY'S OWN WEB CERTIFICATE. Distributor has no keystore setting of
#      its own: with TLS on it reads data/config/local/ignition/webserver/
#      keystore/ssl.pfx -- the gateway's HTTPS certificate -- with the password
#      "ignition" (both constants in DistributorGwHook, 5.0.4). So the broker's
#      certificate IS the gateway's web certificate, installed the way the
#      Config UI's Web Server > SSL/TLS page does it:
#      POST /data/config/ssl/transition/ca-signed-certificate. A leaf from the
#      repo CA (stacks/npm/certs), SANs = every name a client dials. That file
#      is under data/config/LOCAL, which redundancy does not synchronise: each
#      half of a pair needs its own.
#   2. TLS ON, PLAIN TCP AND WEBSOCKETS OFF. The rest of the stack is all-TLS
#      (docs/MQTTS.md), and a broker that also listens in clear is one typo
#      from a client that sends the password in clear.
#   3. ONE USER, the stack's broker login (MQTT_PASSWORD in .secrets.env),
#      and the shipped admin/changeme DELETED.
#
# The network is not this script's: wd-mqtt is joined in each stack's compose
# file (scripts/lib.sh:ensure_mqtt_net), and on it the halves answer as
# mqtt-master / mqtt-backup -- the names in each half's certificate.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DIST=com.cirruslink.mqtt.distributor.gateway
CA_PEM="$STACKS_DIR/npm/certs/rootCA.pem"
CA_KEY="$STACKS_DIR/npm/certs/rootCA.key"
STATUS=0; GW=""
for a in "$@"; do
  case "$a" in
    --status)  STATUS=1 ;;
    -*)        die "unknown option: $a" ;;
    *)         GW="$a" ;;
  esac
done
[ -n "$GW" ] || die "usage: scripts/distributor-setup.sh [--status] <gateway>"
need_docker
require_gateway "$GW"
cd "$REPO_ROOT"
STANZA="$(stanza_for "$GW")"
WORK="$(mktemp -d)"; chmod 700 "$WORK"; trap 'rm -rf "$WORK"' EXIT

# The names a client dials this broker by, and only this half's: the container
# name (backbone -- the console's listener), `localhost` (the hub's own Engine,
# so each half talks to its own broker), and its alias on wd-mqtt (the edges).
case "$GW" in
  ignition)                 SANS="ignition localhost mqtt-master" ;;
  ignition-backup)          SANS="ignition-backup localhost mqtt-backup" ;;
  ignition-broker)          SANS="ignition-broker localhost mqtt-broker" ;;
  *)                        SANS="$GW localhost" ;;
esac

api() {  # api <method> <path> [body-file] [raw] -- status line, then the body
  local extra=()
  [ -n "${3:-}" ] && { if [ "${4:-}" = raw ]; then extra=(--body-file-raw "$3"); else extra=(--body-file "$3"); fi; }
  node scripts/ign-gw.js api --gateway "$STANZA" --method "$1" --path "$2" "${extra[@]}" 2>&1
}
body() { sed 1d; }   # drop ign-gw.js's status line

# --- 1. the certificate --------------------------------------------------------
cert_state() { api GET /data/config/ssl/state | body | tr -d '"'; }
cert_sans() {  # the SANs of the certificate the gateway serves now, sorted
  api GET /data/config/ssl/certificate.crt | body > "$WORK/current.crt" || true
  openssl x509 -in "$WORK/current.crt" -noout -ext subjectAltName 2>/dev/null \
    | tr ',' '\n' | sed -n 's/^ *DNS://p' | sort | tr '\n' ' '
}
want_sans() { printf '%s\n' $SANS | sort | tr '\n' ' '; }

install_cert() {
  [ -f "$CA_KEY" ] || die "no $CA_KEY -- run: make certs"
  {
    printf '[req]\ndistinguished_name = dn\nreq_extensions = v3\nprompt = no\n'
    printf '[dn]\nCN = %s\n' "$GW"
    printf '[v3]\nbasicConstraints = CA:FALSE\nkeyUsage = digitalSignature, keyEncipherment\n'
    printf 'extendedKeyUsage = serverAuth, clientAuth\nsubjectAltName = @alt\n[alt]\n'
    local i=1 d; for d in $SANS; do printf 'DNS.%d = %s\n' "$i" "$d"; i=$((i + 1)); done
    printf 'IP.1 = 127.0.0.1\n'
  } > "$WORK/leaf.cnf"
  openssl genrsa -out "$WORK/leaf.key" 2048 2>/dev/null
  openssl req -new -key "$WORK/leaf.key" -out "$WORK/leaf.csr" -config "$WORK/leaf.cnf"
  # -CAserial into WORK: never touch the CA directory's own serial file.
  openssl x509 -req -in "$WORK/leaf.csr" -CA "$CA_PEM" -CAkey "$CA_KEY" \
    -CAserial "$WORK/ca.srl" -CAcreateserial -out "$WORK/leaf.pem" -days 825 -sha256 \
    -extfile "$WORK/leaf.cnf" -extensions v3 2>/dev/null
  # The UI's own body: the chain as PEM strings ending in the self-signed root
  # ("Chain of trust is incomplete" otherwise), the key base64-encoded.
  python3 - "$WORK/leaf.pem" "$CA_PEM" "$WORK/leaf.key" "$WORK/ssl.json" <<'PY'
import base64, json, os, sys
leaf, ca, key, out = sys.argv[1:5]
body = {"certificateChain": [open(leaf).read(), open(ca).read()],
        "privateKey": {"privateKey": base64.b64encode(open(key, "rb").read()).decode()}}
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, json.dumps(body).encode()); os.close(fd)
PY
  local out
  out="$(api POST /data/config/ssl/transition/ca-signed-certificate "$WORK/ssl.json")"
  rm -f "$WORK/leaf.key" "$WORK/ssl.json"
  case "$out" in 2[0-9][0-9]\ *) ;; *) die "$GW refused the certificate: $(printf '%s' "$out" | head -c 300)" ;; esac
}

# --- 2. TLS on, clear off ----------------------------------------------------------
general() {  # general <check|apply> -- prints what differs
  api GET "/data/api/v1/resources/singleton/$DIST/general" | body > "$WORK/gen.json"
  python3 - "$WORK/gen.json" "$WORK/gen-put.json" "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
cfg = dict(d["config"])
want = {"enableDistributor": True, "enableTls": True, "securePort": 8883,
        "enableTcp": False, "enableWebsocket": False, "enableSecureWebsocket": False,
        "allowAnonymousConnections": False}
diff = sorted(k for k, v in want.items() if cfg.get(k) != v)
cfg.update(want)
json.dump([{"signature": d["signature"], "config": cfg}], open(sys.argv[2], "w"))
print(",".join(diff) or "-")
PY
}

# --- 3. the user ---------------------------------------------------------------------
users() { api GET "/data/api/v1/resources/list/$DIST/user" | body > "$WORK/users.json"
          python3 -c 'import json,sys; print(" ".join(sorted(i["name"] for i in json.load(open(sys.argv[1])).get("items", []))))' "$WORK/users.json"; }

if [ "$STATUS" -eq 1 ]; then
  # Each of the four reads happens ONCE and is held in a variable. Every one of
  # them is an ign-gw.js call costing ~17s, and the verdicts below need the same
  # answers the lines above print -- asking twice would double the cost of this
  # command to say the same thing.
  say "$GW -- MQTT Distributor"
  s_state="$(cert_state)"
  s_sans="$(cert_sans)"
  s_want="$(want_sans)"
  s_gen="$(general check)"
  s_users="$(users)"
  s_user="$(mqtt_user)"
  drift=0

  dim "  web certificate: $s_state   SANs: $s_sans"
  # The SAME comparison the apply path makes at the certificate step below.
  if [ "$s_state" = CA_SIGNED_CERTIFICATE ] && [ "$s_sans" = "$s_want" ]; then
    ok "web certificate is signed by the repo CA for: $s_want"
  else
    printf '  drift: %s web certificate is %s for [%s] -- should be CA_SIGNED_CERTIFICATE for [%s] (Network > Web Server > SSL/TLS)\n' \
      "$GW" "$s_state" "${s_sans% }" "${s_want% }"
    drift=1
  fi

  dim "  general differs from the wanted TLS-only shape in: $s_gen"
  if [ "$s_gen" = "-" ]; then
    ok "TLS 8883 on; TCP, WebSocket and anonymous off"
  else
    printf '  drift: %s MQTT Distributor general settings differ in %s (Connections > MQTT Distributor > General)\n' \
      "$GW" "$s_gen"
    drift=1
  fi

  # The two user verdicts the apply path acts on, and nothing more: the
  # password is encrypted on the gateway and cannot be compared, which is why
  # the apply path does not compare it either.
  dim "  users: $s_users"
  case " $s_users " in
    *" $s_user "*) ok "broker user '$s_user' present" ;;
    *) printf '  drift: %s has no MQTT Distributor user %s -- every client in the stack logs in as it (Connections > MQTT Distributor > Users)\n' \
         "$GW" "$s_user"; drift=1 ;;
  esac
  case " $s_users " in
    *" admin "*) printf '  drift: %s still has the shipped MQTT Distributor user admin (password changeme) -- it should be deleted\n' \
                   "$GW"; drift=1 ;;
    *) ok "no shipped 'admin' user" ;;
  esac

  # Non-zero ONLY for real drift. Everything matching has to exit 0: this is
  # read from `|| true` in one migration today and from scripts/drift.sh, and a
  # non-zero exit for the ordinary answer is the bug this repo keeps writing.
  if [ "$drift" -ne 0 ]; then exit 1; fi
  exit 0
fi

say "$GW -- MQTT Distributor as a TLS broker"
if [ "$(cert_state)" = CA_SIGNED_CERTIFICATE ] && [ "$(cert_sans)" = "$(want_sans)" ]; then
  ok "web certificate already signed by the repo CA for: $(want_sans)"
else
  install_cert
  [ "$(cert_state)" = CA_SIGNED_CERTIFICATE ] || die "$GW: certificate state is $(cert_state) after the install"
  ok "web certificate installed (the broker's TLS certificate): $(want_sans)"
fi

diff="$(general check)"
if [ "$diff" = "-" ]; then
  ok "TLS 8883 on; TCP, WebSocket and anonymous off"
else
  out="$(api PUT "/data/api/v1/resources/$DIST/general" "$WORK/gen-put.json")"
  case "$out" in *'"success": true'*|*'"success":true'*) ok "general set ($diff) -- the broker restarted" ;;
    *) die "general refused: $(printf '%s' "$out" | head -c 300)" ;; esac
fi

MQTT_USER="$(mqtt_user)"
have="$(users)"
case " $have " in
  *" $MQTT_USER "*) ok "user '$MQTT_USER' present (its password is not compared: it is encrypted)" ;;
  *)
    # The password reaches the gateway in a 0600 file, is encrypted THERE, and
    # only the ciphertext goes into the resource. Never argv, never printed.
    ( umask 077; secret MQTT_PASSWORD > "$WORK/pw" )
    [ -s "$WORK/pw" ] || die "MQTT_PASSWORD missing from .secrets.env"
    api POST /data/api/v1/encryption/encrypt "$WORK/pw" raw | body > "$WORK/jwe.json"; rm -f "$WORK/pw"
    python3 - "$WORK/jwe.json" "$MQTT_USER" "$WORK/user.json" <<'PY'
import json, sys
jwe = json.load(open(sys.argv[1]))
json.dump([{"name": sys.argv[2], "collection": "core", "enabled": True,
            "description": "Broker login for every MQTT client in the stack -- scripts/distributor-setup.sh",
            "config": {"password": {"type": "Embedded", "data": jwe}, "ACLs": "RW #"}}],
          open(sys.argv[3], "w"))
PY
    out="$(api POST "/data/api/v1/resources/$DIST/user" "$WORK/user.json")"; rm -f "$WORK/user.json" "$WORK/jwe.json"
    case "$out" in *'"success": true'*|*'"success":true'*) ok "user '$MQTT_USER' created -- the broker restarted" ;;
      *) die "user refused: $(printf '%s' "$out" | head -c 300)" ;; esac ;;
esac
case " $(users) " in *" admin "*) has_admin=1 ;; *) has_admin=0 ;; esac
if [ "$has_admin" -eq 1 ]; then
  sig="$(python3 -c 'import json,sys; print(next(i["signature"] for i in json.load(open(sys.argv[1]))["items"] if i["name"]=="admin"))' "$WORK/users.json")"
  out="$(api DELETE "/data/api/v1/resources/$DIST/user/admin/$sig")"
  case "$out" in 2[0-9][0-9]\ *) ok "shipped user 'admin' deleted -- the broker restarted" ;;
    *) warn "could not delete 'admin': $(printf '%s' "$out" | head -c 200)" ;; esac
else
  ok "no shipped 'admin' user"
fi

say "done: ssl://$(printf '%s' "$SANS" | awk '{print $NF}'):8883 on wd-mqtt, ssl://$GW:8883 on backbone"
