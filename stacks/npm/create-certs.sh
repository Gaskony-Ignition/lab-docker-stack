#!/bin/bash
# Generates a local certificate authority and one SAN certificate covering the
# *.test names, then uploads it to Nginx Proxy Manager and forces HTTPS on
# every proxy host.
#
# Let's Encrypt cannot be used here: .test is reserved by RFC 6761 and will
# never resolve publicly, so no public CA will ever issue for it. A local CA is
# the only way to get real TLS on these names.
#
# Idempotent: re-running reuses the existing CA (so you do not have to re-trust
# it) and replaces the leaf certificate in NPM.
set -euo pipefail
cd "$(dirname "$0")"

CERTDIR=certs
DAYS_CA=3650
DAYS_LEAF=825          # macOS rejects leaf certs valid for more than 825 days
# Inside the toolbox `localhost` is the TOOLBOX, so a host-port URL reaches
# nothing and the upload is silently skipped -- leaving a freshly issued
# certificate on disk that NPM is not serving, which looks like the cert is
# wrong rather than absent. Same rule as create-proxy-hosts.sh: container
# name in there, host port out here.
if [ "${WD_TOOLBOX:-}" = "1" ]; then API=http://npm:81/api
else                                 API=http://localhost:81/api
fi

# The SAN list follows the manifests, so a stack added with a TEST_HOST is
# covered by the next `make certs` with no edit here. The wildcard below covers
# it even before that.
DOMAINS=( $(sed -n 's/^TEST_HOST=//p' ../../stacks/*/stack.meta | tr -d '\r') )

mkdir -p "$CERTDIR"
chmod 700 "$CERTDIR"

# ---------------------------------------------------------------- local CA --
if [ ! -f "$CERTDIR/rootCA.pem" ]; then
  echo "==> creating local CA (trust this once, see README)"
  openssl genrsa -out "$CERTDIR/rootCA.key" 4096 2>/dev/null
  openssl req -x509 -new -nodes -key "$CERTDIR/rootCA.key" \
    -sha256 -days "$DAYS_CA" -out "$CERTDIR/rootCA.pem" \
    -subj "/C=AU/ST=South Australia/L=Adelaide/O=Home Lab/CN=Home Lab Local CA"
else
  echo "==> reusing existing CA at $CERTDIR/rootCA.pem"
fi

# ------------------------------------------------------------ leaf cert -----
# Wildcard *.test plus every explicit name. The wildcard means adding a seventh
# proxy host later needs no new certificate; the explicit entries keep older
# clients that ignore wildcards happy.
{
  echo "[req]"
  echo "distinguished_name = dn"
  echo "req_extensions = v3"
  echo "prompt = no"
  echo "[dn]"
  echo "CN = *.test"
  echo "[v3]"
  echo "basicConstraints = CA:FALSE"
  echo "keyUsage = digitalSignature, keyEncipherment"
  echo "extendedKeyUsage = serverAuth"
  echo "subjectAltName = @alt"
  echo "[alt]"
  echo "DNS.1 = *.test"
  i=2
  for d in "${DOMAINS[@]}"; do
    echo "DNS.$i = $d"
    i=$((i + 1))
  done
} > "$CERTDIR/leaf.cnf"

echo "==> issuing leaf certificate for *.test + ${#DOMAINS[@]} explicit names"
openssl genrsa -out "$CERTDIR/leaf.key" 2048 2>/dev/null
openssl req -new -key "$CERTDIR/leaf.key" -out "$CERTDIR/leaf.csr" \
  -config "$CERTDIR/leaf.cnf"
openssl x509 -req -in "$CERTDIR/leaf.csr" \
  -CA "$CERTDIR/rootCA.pem" -CAkey "$CERTDIR/rootCA.key" -CAcreateserial \
  -out "$CERTDIR/leaf.pem" -days "$DAYS_LEAF" -sha256 \
  -extfile "$CERTDIR/leaf.cnf" -extensions v3 2>/dev/null

chmod 600 "$CERTDIR"/*.key

# ------------------------------------------------- the site proxies' copy ----
# A SITE proxy needs the leaf and its key, and must never see the CA's private
# key. Seeding is directory-at-a-time, so `certs/` cannot be handed to a site
# container without handing over rootCA.key with it -- which would put the
# authority for every name in this stack on a box sitting in a plant.
#
# So the two files a front door actually needs are written out separately, and
# that is what gets seeded into wd-npm-site-certs.
SITE=site-certs
mkdir -p "$SITE"; chmod 700 "$SITE"
cp "$CERTDIR/leaf.pem" "$CERTDIR/leaf.key" "$SITE/"
chmod 600 "$SITE/leaf.key"
echo "==> site proxy copy written to $SITE/ (leaf only, no CA key)"

# THIS BLOCK MUST STAY ABOVE THE UPLOAD GUARD BELOW. It is local file work --
# copying two files that already exist -- and has nothing to do with NPM. It
# used to sit after the `exit 0` that fires when the admin API is not
# answering, and on a FRESH machine that guard always fires: bootstrap runs
# this at step 2 and does not start a container until step 5. So site-certs/
# was never created on exactly the machines that had never had it, and
# bootstrap then died at step 4:
#
#     fail cannot seed volume 'wd-npm-site-certs':
#          /work/stacks/npm/site-certs does not exist.
#
# which is fatal, so a clean clone could not build the stack at all. It
# survived because every machine that already had a site-certs/ from an
# earlier run kept sailing past it.

# ------------------------------------------------------------- upload -------
# The CA above is also what distributor-setup.sh signs each hub half's broker
# (web) certificate with, so this script has to be runnable before NPM exists. If the admin API
# is not answering, stop here with the certificates on disk rather than failing
# -- re-run once NPM is up to do the upload half.
if ! curl -fsS --max-time 5 "$API/" >/dev/null 2>&1; then
  echo
  echo "==> NPM is not answering on $API -- certificates written, upload skipped."
  echo "    Re-run this script once NPM is up to upload the leaf and force HTTPS."
  exit 0
fi

EMAIL=$(grep '^INITIAL_ADMIN_EMAIL=' .env | cut -d= -f2-)
PASS=$(grep '^INITIAL_ADMIN_PASSWORD=' .env | cut -d= -f2-)
TOKEN=$(curl -s -X POST "$API/tokens" -H 'Content-Type: application/json' \
  -d "{\"identity\":\"$EMAIL\",\"secret\":\"$PASS\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

echo "==> uploading certificate to NPM"
CERT_ID=$(curl -s -X POST "$API/nginx/certificates" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"nice_name":"local-test-wildcard","provider":"other"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

curl -s -X POST "$API/nginx/certificates/$CERT_ID/upload" \
  -H "Authorization: Bearer $TOKEN" \
  -F "certificate=@$CERTDIR/leaf.pem" \
  -F "certificate_key=@$CERTDIR/leaf.key" > /dev/null

echo "    certificate id = $CERT_ID"

# ------------------------------------------------- attach to proxy hosts ----
# ssl_forced redirects http -> https.
# hsts_enabled is deliberately left OFF: HSTS is cached hard by browsers and
# would make it painful to go back to plain http on these names later.
echo "==> forcing HTTPS on every proxy host"
curl -s "$API/nginx/proxy-hosts" -H "Authorization: Bearer $TOKEN" \
  | python3 -c 'import sys,json;[print(h["id"], h["domain_names"][0]) for h in json.load(sys.stdin)]' \
  | while read -r id domain; do
      code=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "$API/nginx/proxy-hosts/$id" \
        -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
        -d "{\"certificate_id\":$CERT_ID,\"ssl_forced\":true,\"http2_support\":true,\"hsts_enabled\":false}")
      printf '    %-16s HTTP %s\n' "$domain" "$code"
    done

echo
echo "Done. Trust the CA so browsers stop warning:  make trust-ca"
echo "(host-only and idempotent -- it handles macOS, the Linux system store,"
echo " and Chrome/Firefox NSS profiles, replacing a rotated CA if one is there)"
