#!/usr/bin/env bash
#
# v1.0.0 -- MQTT Distributor as the broker, with TLS, on the hub pair.
#
# WHAT CHANGED. The broker moved off a standalone EMQX container and into the
# hub pair's own MQTT Distributor module: each half is a TLS broker on 8883 with
# its OWN certificate, answering as mqtt-master / mqtt-backup on wd-mqtt, and
# every MQTT-speaking edge plus the hub's Engine was pointed at it
# (docs/MQTT-DISTRIBUTOR.md).
#
# WHY A PULL IS NOT ENOUGH -- five separate pieces of machine state, none of
# which is a file this repo owns:
#
#   the .modl           modules/ is gitignored; scripts/get-modules.sh fetches
#                       it by hash from the vendor
#   the module INSTALLED  it lives in the gateway's Docker volume
#   each half's cert    minted from this machine's CA, which is machine-local
#                       by design, and stored under data/config/local -- which
#                       redundancy does NOT sync, so each half needs its own
#   the TLS-only shape  enableTls, securePort 8883, TCP/WS/anonymous off
#   the broker user     encrypted on the gateway; the shipped `admin` deleted
#
# INSTALLING THE MODULE IS THE ONE STEP THIS WILL NOT DO. scripts/ign-modules.sh
# stops the gateway, writes into its volume, restarts it -- and the gateway then
# sits behind a third-party certificate gate that 302-redirects every request to
# /welcome until a human clicks through the wizard. `--yes` skips the prompt, not
# the gate. Running that from a nightly timer would take the rig down and leave
# it down, looking for all the world like a broken gateway. So it DEFERS: it
# says the command and stops, and stays pending until it is done.
#
# Everything after the install is idempotent by construction, because every
# Distributor write restarts the broker -- so each of those scripts reads,
# compares and writes only differences. Re-running them is cheap and safe.
#
# ONE-WAY: the Distributor module stays installed, the hub pair stays a TLS-only broker on 8883, and Engine and every MQTT edge stay pointed at it. Going back to a release that expected a separate broker will not undo any of that -- re-point them with 'make mqtt-setup' against whatever that release wants.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
. "$(dirname "${BASH_SOURCE[0]}")/../scripts/migrate-lib.sh"

# --- 1. the .modl is on this machine ------------------------------------------
# get-modules.sh is hash-checked and idempotent, and exits 0 when a file simply
# cannot be fetched -- which is why the presence test is separate from the run.
modl_present() {
  [ -n "$(find "$REPO_ROOT/modules" -name 'MQTT-Distributor*.modl' 2>/dev/null | head -1)" ]
}

if modl_present; then
  mig_ok "the MQTT Distributor .modl is in modules/"
else
  mig_do "fetching the third-party modules (hash-checked)" \
    "$WD" -- scripts/get-modules.sh
fi

# --- 2. the hub has to be up for anything below -------------------------------
if ! gateway_running ignition; then
  mig_defer "the hub is not running, so the broker was not configured" \
            "./wd core   then   scripts/migrate.sh"
  mig_finish
fi

# --- 3. is the module installed? ----------------------------------------------
installed="$("$WD" -- scripts/ign-modules.sh --list ignition 2>/dev/null \
             | grep -ci 'distributor' || true)"
if [ "${installed:-0}" -eq 0 ]; then
  mig_defer "MQTT Distributor is NOT installed on the hub. Installing it stops the
       gateway and raises a certificate gate somebody has to clear at /welcome,
       so it is not done unattended -- pick a moment when nobody is watching" \
            "./wd modules GATEWAY=ignition   (then finish the wizard at /welcome)"
  mig_finish
fi
mig_ok "MQTT Distributor is installed on the hub"

# --- 4. the TLS broker shape, per half ----------------------------------------
# --status changes nothing and is the check. Its own report names the
# certificate state, the keys of `general` that differ, and the users.
if [ "$MIG_CHECK" = 1 ]; then
  say "distributor-setup --status ignition -- what is there now (reads only)"
  "$WD" -- scripts/distributor-setup.sh --status ignition 2>&1 | sed 's/^/     /' || true
  # NOT a claim that anything differs. This migration has simply never been
  # RECORDED here, and the scripts it runs read-compare-write, so applying it on
  # an already-correct machine writes nothing at all. `make drift` is the command
  # that answers "does anything differ".
  mig_do "record this migration by running it (idempotent: writes only what differs)" true
else
  mig_do "making the hub a TLS broker on 8883 with its own certificate" \
    "$WD" -- scripts/distributor-setup.sh ignition
  # The backup only when it is up: it has its OWN certificate, because
  # data/config/local does not sync between the halves.
  if gateway_running ignition-backup; then
    mig_do "the same for the redundant backup (its own certificate)" \
      "$WD" -- scripts/distributor-setup.sh ignition-backup
  else
    dim "     the backup is not running -- run 'make distributor-setup' when it is"
  fi

  # --- 5. point Engine and the edges at it ------------------------------------
  # Idempotent: ign-gw.js mqtt-server reads, compares and writes only the keys
  # that differ, because every write restarts a transmitter or re-births an edge.
  mig_do "pointing the hub's Engine and every MQTT edge at the hub pair's broker" \
    "$WD" -- scripts/ign-mqtt.sh setup
fi

mig_finish
