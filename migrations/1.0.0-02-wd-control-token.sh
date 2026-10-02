#!/usr/bin/env bash
#
# v1.0.0 -- WD_CONTROL_TOKEN, and the retired secrets it replaced.
#
# WHAT CHANGED. The console's MQTT cut is a POST to wd-control, and wd-control
# accepts it only with a shared token in `X-WD-Token` (control/mqttcut.py). The
# token is generated into the gitignored root `.secrets.env` by
# scripts/make-env.sh and installed into each gateway half's own secret store by
# scripts/ign-secrets.sh, where the Perspective script reads it with
# system.secrets.readSecretValue.
#
# WHY A PULL IS NOT ENOUGH. `.secrets.env` is gitignored -- deliberately, it is
# the one file that must never reach GitHub -- so the key is simply absent on a
# machine that has not run `make env` since. And the per-half copy lives in a
# Docker volume, which is not a file this repo owns either. Symptoms, both of
# which read as "the button is broken":
#
#   token missing from .secrets.env   wd-control answers 503 with
#                                     "wd-control has no WD_CONTROL_TOKEN"
#   present but not installed         the page never calls out at all:
#                                     "this gateway has no wd-control token"
#
# IT ALSO PRUNES THE RETIRED ONES. ign-secrets.sh carries a RETIRED list and
# deletes those names from every half -- the two EMQX dashboard secrets, left
# behind when EMQX was retired. That is the same job as migration 06 and it
# belongs here, because it is the same script run.
#
# NO VALUE IS EVER PRINTED, and none is read here. This checks for the PRESENCE
# of a key with `grep -c`, which prints a count, and leaves generating and
# installing to the two scripts that already do it.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"

# --- 1. the token exists locally ---------------------------------------------
have=0
if [ -f "$SECRETS_FILE" ]; then
  have="$(grep -cE '^WD_CONTROL_TOKEN=' "$SECRETS_FILE" || true)"
fi

if [ "${have:-0}" -gt 0 ]; then
  mig_ok "WD_CONTROL_TOKEN is in .secrets.env"
else
  # make-env.sh generates only what is absent and never rotates an existing
  # value, so this is safe on a machine that already has every other secret.
  mig_do "generating WD_CONTROL_TOKEN (make-env.sh -- the value is never printed)" \
    "$WD" -- scripts/make-env.sh
fi

# --- 2. the gateway halves have their copy ------------------------------------
# ign-secrets.sh needs the hub running: it writes into the gateway's own volume
# through the container. It does the backup too when the backup is up, and the
# files do NOT sync by redundancy -- only the provider resource does -- so each
# half needs its own run.
if ! gateway_running ignition; then
  mig_defer "the hub is not running, so the gateway's copy of the token was not installed" \
            "./wd core   then   ./wd -- scripts/ign-secrets.sh"
else
  # Names only, never values -- that is what --list prints.
  n="$("$WD" -- scripts/ign-secrets.sh --list 2>/dev/null \
       | grep -c 'wd-control-token' || true)"
  if [ "${n:-0}" -gt 0 ]; then
    mig_ok "the hub has its wd-control-token secret"
  else
    mig_do "installing the token into each half's secret store, and deleting the retired EMQX ones" \
      "$WD" -- scripts/ign-secrets.sh
  fi
fi

mig_finish
