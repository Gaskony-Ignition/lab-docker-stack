#!/usr/bin/env bash
#
# v1.1.0 -- https://ignition.test serves the hub while the pair's proxy is down.
#
# WHAT CHANGED. ignition.test goes npm -> ignition-ha (HAProxy) -> the active
# half, and ignition-ha is part of the Redundancy demo, so on a machine with
# that demo stopped -- most of the time -- the main front door answered 502.
# stacks/ignition-ha/stack.meta now carries TEST_FALLBACK=ignition, and
# stacks/npm/create-proxy-hosts.sh writes a named location into that proxy
# host's advanced config: a 502/504 nginx makes itself (the proxy's name does
# not resolve or does not answer) is served by the hub instead.
#
# WHY A PULL IS NOT ENOUGH. The proxy host's config lives in npm's own database
# and data volume, not in a file this repo owns. The script that writes it is
# idempotent and only touches what differs.
#
# NO RESTART. NPM reloads nginx itself when a proxy host is saved, and rejects
# a snippet that does not parse, leaving the previous config serving.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"

if ! gateway_running npm; then
  mig_defer "the npm stack is not running, so ignition.test's fallback was not written" \
            "./wd core   then   ./wd proxy-hosts"
  mig_finish
fi

# The config NPM generated, read where nginx reads it. `|| true` on the whole
# pipeline: no match is a legitimate answer.
n="$(docker exec npm sh -c 'grep -l "server_name ignition.test;" /data/nginx/proxy_host/*.conf \
      | xargs -r grep -c "@wd_fallback"' 2>/dev/null | head -1 || true)"

if [ "${n:-0}" -gt 0 ]; then
  mig_ok "ignition.test falls back to the hub when the pair's proxy is not running"
else
  mig_do "giving ignition.test a fallback to the hub (NPM reloads itself; nothing restarts)" \
    "$WD" proxy-hosts
fi

mig_finish
