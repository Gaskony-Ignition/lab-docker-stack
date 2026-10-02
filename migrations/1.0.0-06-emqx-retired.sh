#!/usr/bin/env bash
#
# v1.0.0 -- clear up after EMQX.
#
# WHAT CHANGED. EMQX was retired: the broker is now MQTT Distributor inside the
# hub pair. The commit deleted the stack folder, its certificate script, the
# API-key script and the wire witness, and took `emqx` out of demos.json's core.
#
# WHY A PULL IS NOT ENOUGH, AND WHY THIS ONE MATTERS MORE THAN IT LOOKS. Deleting
# `stacks/emqx/` from git does not stop a container. Worse, it makes it INVISIBLE:
# wd-control decides what to stop from `stacks/*/stack.meta`, so with the folder
# gone `emqx` is in neither "wanted" nor "ours" and the console will never stop
# it -- the safety net that used to catch it was removed by the same commit. On
# this hardware the retired broker and its witness were measured at 262% and 196%
# CPU with a load average over 25. So a machine that ran the old version keeps
# paying for a broker nothing uses and nothing can see.
#
# WHAT IT LEAVES BEHIND, all verified against the deleted files at 70eb9ae^:
#
#   container `emqx`                   compose project `emqx`
#   volumes emqx_emqx_data, emqx_emqx_log, and external wd-emqx-certs
#   stacks/emqx/.env and stacks/emqx/certs/   -- gitignored, so the pull left them
#   an orphaned EMQX_DASHBOARD_* line in .secrets.env (make-env.sh only appends)
#   the `emqx.test` proxy host in NPM, and its line in this machine's hosts file
#
# THE VOLUMES ARE REMOVED, and that is a decision worth stating. TRANSFER.md is
# emphatic that a GATEWAY's Docker volume is unrecoverable local state -- module
# installs, commissioning, Gateway Network pairing. These are not that: they are
# a retired broker's message store and log, for a broker no compose file, script
# or document in this repo references any more, and which cannot be started
# again from this repo at all because its stack folder is gone. Nothing can ever
# read them.
#
# THE NAMES ARE ONLY REPORTED. Removing the `emqx.test` proxy host and its hosts
# entry is `make prune-names`, which touches /etc/hosts and may ask for sudo. A
# migration that runs from a timer does not get to hold a sudo prompt, and a
# stale name is cosmetic -- it resolves to 127.0.0.1 and the front door answers
# 404. So it is named, not done.
#
# NO SECRET IS PRINTED. The orphaned line is deleted by pattern with sed; its
# value is never read, echoed or passed to anything.
#
# ONE-WAY: the emqx container and its three volumes are gone, and this filesystem TRIMs deleted blocks. A release old enough to want EMQX back gets its stacks/emqx folder from the checkout, but the broker is then empty -- 'make certs' and 'make up STACK=emqx' build it again from nothing.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"

EMQX_VOLUMES="emqx_emqx_data emqx_emqx_log wd-emqx-certs"

# --- 1. the container ---------------------------------------------------------
if container_exists emqx; then
  mig_do "removing the retired 'emqx' container (nothing in this repo can start it again)" \
    docker rm -f emqx
else
  mig_ok "no 'emqx' container here"
fi

# --- 2. its volumes -----------------------------------------------------------
left=""
for v in $EMQX_VOLUMES; do
  if volume_exists "$v"; then left="$left $v"; fi
done
if [ -z "$left" ]; then
  mig_ok "none of EMQX's volumes are left"
else
  for v in $left; do
    mig_do "removing volume $v" docker volume rm "$v"
  done
fi

# --- 3. the stack folder's gitignored leftovers -------------------------------
# GUARDED ON GIT KNOWING NOTHING ABOUT IT. If `git ls-files` returns anything
# under stacks/emqx then this checkout still tracks the stack -- the machine is
# on a commit BEFORE the retirement -- and deleting it would be destroying
# tracked files. Refuse rather than guess.
if [ -d "$STACKS_DIR/emqx" ]; then
  tracked="$(cd "$REPO_ROOT" && git ls-files stacks/emqx | head -1 || true)"
  if [ -n "$tracked" ]; then
    die "stacks/emqx is still TRACKED in this checkout ($tracked).
     That means this tree predates the retirement, so this migration is running
     too early. Take the release first: git checkout the tag, then re-run."
  fi
  mig_do "removing stacks/emqx (untracked leftovers: .env, certs)" \
    rm -rf "$STACKS_DIR/emqx"
else
  mig_ok "stacks/emqx is gone"
fi

# --- 4. the orphaned secret line ----------------------------------------------
if [ -f "$SECRETS_FILE" ]; then
  n="$(grep -cE '^EMQX_DASHBOARD_' "$SECRETS_FILE" || true)"
  if [ "${n:-0}" -gt 0 ]; then
    # By pattern, in place, value never read. make-env.sh only ever appends, so
    # nothing else would ever remove this.
    mig_do "removing $n orphaned EMQX_DASHBOARD_* line(s) from .secrets.env (value never read)" \
      sed -i '/^EMQX_DASHBOARD_/d' "$SECRETS_FILE"
  else
    mig_ok ".secrets.env carries no EMQX keys"
  fi
fi

# --- 5. the names, reported ---------------------------------------------------
# /etc/hosts is the half that can be checked without credentials or a network.
# NPM's proxy host is the other half and create-proxy-hosts.sh reports it on
# every run; `make prune-names` does both.
hosts_file=/etc/hosts
if [ -r "$hosts_file" ] && [ "$(grep -c 'emqx\.test' "$hosts_file" || true)" -gt 0 ]; then
  warn "'emqx.test' is still in $hosts_file, and probably still a proxy host in NPM."
  dim  "       Cosmetic -- it resolves to 127.0.0.1 and the front door answers 404."
  dim  "       Clear both (host, may ask for sudo):  make prune-names"
else
  mig_ok "no 'emqx.test' in this machine's hosts file"
fi

# The gateway-side EMQX dashboard secrets are deleted by scripts/ign-secrets.sh,
# which carries them in its RETIRED list -- migration 02 is the one that runs it.
mig_finish
