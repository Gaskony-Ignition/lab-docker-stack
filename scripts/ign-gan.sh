#!/usr/bin/env bash
#
# Approve Gateway Network peer certificates, on disk.
#
#   scripts/ign-gan.sh certs    [gateway...]  approve pending certs (default: all three)
#   scripts/ign-gan.sh status   [gateway...]  what is pending / trusted
#   scripts/ign-gan.sh incoming [gateway...]  securityStatus of each incoming connection
#
# Certificates are step one of two. Approving the CONNECTION is a separate act
# and lives in ign-gw.js:
#
#   node scripts/ign-gw.js gan-approve --gateway local
#
# That one goes through the gateway's REST API rather than touching disk. The
# securityStatus of an incoming connection is a config resource on disk too, but
# writing it there skips whatever the gateway does on approval; `incoming` above
# is for READING it back, as an independent check on what the API reported.
#
# A Gateway Network connection with SSL on does not come up until each side has
# APPROVED the other's certificate. Until then the connection sits at FAULTED
# and the only clue is buried in the gateway log:
#
#   java.security.cert.CertificateException: No approved certificate could be
#   found in the certificates folder! To allow connection, the server
#   certificate 'CN=<host>:8060, OU=GatewayNetwork, ...' must be approved
#
# "The certificates folder" is literal, and that is what makes this scriptable.
# Each gateway keeps a PKI tree per direction:
#
#   data/config/local/ignition/gateway-network/<client|server>/security/pki/
#       rejected/        a peer cert that has been seen but not accepted
#       trusted/certs/   approved -- the connection comes up
#       issuers/certs/   CA certs
#
# Approving is moving the file from rejected/ to trusted/certs/. The UI's
# per-row kebab does the same thing, but driving it headlessly proved
# unreliable: the grids are divs rather than tables, the menu renders in a
# portal, and a click that appears to succeed can leave the cert rejected --
# which looks exactly like the approval having worked.
#
#   client/  certs of gateways THIS one dials out to   (a spoke trusting the hub)
#   server/  certs of gateways that dial IN to this one (the hub trusting a spoke)
#
# Both directions are handled: a spoke needs the hub in client/, and the hub
# needs each spoke in server/.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PKI=/usr/local/bin/ignition/data/config/local/ignition/gateway-network

# 8.3 keeps incoming Gateway Network connections as file-based config resources,
# one folder per peer, exactly like a project resource:
#
#   config/resources/core/ignition/gateway-network-incoming/<name>_<uuid>/
#       config.json     {"connectionId": "...", "securityStatus": "..."}
#       resource.json   manifest, with the same lastModificationSignature trap
#
# securityStatus is IncomingConnection$SecurityStatus in gateway-api-8.3.8.jar:
# PendingApproval | Approved | NotOnWhiteList.
#
# Read-only here on purpose. Approving through the API (ign-gw.js gan-approve)
# is what actually brings the connection up; this is the independent check that
# it did, because the DOM route it replaced reported success and changed nothing.
GNI=/usr/local/bin/ignition/data/config/resources/core/ignition/gateway-network-incoming

cmd="${1:-status}"; shift || true
targets=("$@")
[ "${#targets[@]}" -eq 0 ] && targets=("${GATEWAYS[@]}")

need_docker

pending_count() {
  docker exec "$1" sh -c \
    "ls -1 '$PKI/$2/security/pki/rejected' 2>/dev/null | wc -l" 2>/dev/null || echo 0
}
trusted_count() {
  docker exec "$1" sh -c \
    "ls -1 '$PKI/$2/security/pki/trusted/certs' 2>/dev/null | wc -l" 2>/dev/null || echo 0
}

case "$cmd" in
  status)
    printf '%-16s %-22s %-22s\n' GATEWAY 'client (dials out)' 'server (accepts in)'
    printf '%-16s %-22s %-22s\n' ------- ------------------ -------------------
    for g in "${targets[@]}"; do
      gateway_running "$g" || { printf '%-16s %s\n' "$g" '(not running)'; continue; }
      printf '%-16s %-22s %-22s\n' "$g" \
        "$(pending_count "$g" client) pending, $(trusted_count "$g" client) trusted" \
        "$(pending_count "$g" server) pending, $(trusted_count "$g" server) trusted"
    done
    ;;

  certs)
    for g in "${targets[@]}"; do
      gateway_running "$g" || { warn "$g is not running"; continue; }
      moved=0
      for dir in client server; do
        # One shell per direction: list the rejected files, move each into
        # trusted/certs, and hand ownership back to the gateway user. Files
        # written as root are unreadable to the gateway process, which fails the
        # same way as not approving them at all.
        n=$(docker exec -u 0 "$g" sh -c "
          src='$PKI/$dir/security/pki/rejected'
          dst='$PKI/$dir/security/pki/trusted/certs'
          [ -d \"\$src\" ] || exit 0
          count=0
          for f in \"\$src\"/*; do
            [ -e \"\$f\" ] || continue
            mkdir -p \"\$dst\"
            mv \"\$f\" \"\$dst\"/ && count=\$((count+1))
          done
          owner=\$(stat -c '%u:%g' '$PKI')
          chown -R \"\$owner\" \"\$dst\" 2>/dev/null
          echo \$count" 2>/dev/null || echo 0)
        n=$(printf '%s' "$n" | tr -dc '0-9'); n=${n:-0}
        [ "$n" -gt 0 ] && { ok "$g: approved $n $dir certificate(s)"; moved=$((moved+n)); }
      done
      [ "$moved" -eq 0 ] && dim "  $g: nothing pending"
    done
    echo
    dim "The gateway retries the connection on its own; give it ~30s, then:"
    dim "    scripts/ign-gan.sh status"
    ;;

  incoming)
    for g in "${targets[@]}"; do
      gateway_running "$g" || { warn "$g is not running"; continue; }
      say "$g incoming connections"
      docker exec "$g" sh -c "
        for f in '$GNI'/*/config.json; do
          [ -e \"\$f\" ] || continue
          d=\$(dirname \"\$f\"); printf '  %-46s ' \"\$(basename \"\$d\")\"
          grep -o '\"securityStatus\": *\"[A-Za-z]*\"' \"\$f\" | cut -d'\"' -f4
        done" 2>/dev/null || true
    done
    ;;

  *)
    die "usage: ign-gan.sh [status|certs|incoming] [gateway...]"
    ;;
esac
