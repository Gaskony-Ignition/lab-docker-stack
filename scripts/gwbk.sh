#!/usr/bin/env bash
#
# Take a .gwbk from every gateway, into a gitignored folder.
#
#   scripts/gwbk.sh                  every gateway
#   scripts/gwbk.sh ignition         just one
#   scripts/gwbk.sh --list           what is already there
#   scripts/gwbk.sh --prune          delete all but the newest 5 per gateway
#
# WHY
#
# This repo owns project resources and nothing else. Everything a gateway knows
# that is NOT file-based -- commissioning, admin credentials, Gateway Network
# pairing, EAM registration, database connections, MQTT module config, the
# redundancy pair, the security zone, the remote tag provider -- lives only in
# its Docker volume. `bootstrap` can rebuild all of it from scratch, but that
# takes 15-20 minutes and re-issues the CA, which is not what you want an hour
# before a demonstration. A .gwbk restores in about one.
#
# So: take one when the stack is known good, and again before anything
# destructive. `make verify-demos` tells you whether "known good" is true.
#
# THESE FILES CONTAIN THE GATEWAY'S OWN CREDENTIALS. They are written under
# .gwbk/, which .gitignore already covers twice (`*.gwbk` and the directory),
# and `make check-secrets` refuses a commit carrying one. Do not move them into
# the repo proper, do not attach one to an issue, and do not copy one between
# machines -- each machine's gateways have their own passwords and their own CA,
# and a restore would quietly graft one machine's secrets onto another.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

OUT_DIR="$REPO_ROOT/.gwbk"
KEEP=5

case "${1:-}" in
  --list)
    [ -d "$OUT_DIR" ] || { dim "no backups yet -- run scripts/gwbk.sh"; exit 0; }
    say "backups in .gwbk"
    ls -lh "$OUT_DIR" 2>/dev/null | tail -n +2 | awk '{printf "  %-42s %6s  %s %s %s\n", $9, $5, $6, $7, $8}'
    exit 0
    ;;
  --prune)
    [ -d "$OUT_DIR" ] || exit 0
    say "keeping the newest $KEEP per gateway"
    for gw in "${GATEWAYS[@]}"; do
      # Newest first, drop the first $KEEP, delete the rest. `ls -t` rather than
      # sorting the timestamp out of the name: the name's timestamp is the local
      # clock at the time and the file's mtime is authoritative.
      n=0
      for f in $(ls -t "$OUT_DIR/$gw"-*.gwbk 2>/dev/null || true); do
        n=$((n + 1))
        [ "$n" -le "$KEEP" ] && continue
        rm -f "$f"; dim "  removed $(basename "$f")"
      done
    done
    ok "pruned"
    exit 0
    ;;
esac

need_docker
cd "$REPO_ROOT"
mkdir -p "$OUT_DIR"

TARGETS=("$@")
[ ${#TARGETS[@]} -eq 0 ] && TARGETS=( "${GATEWAYS[@]}" )

# One stamp for the whole run, so a set of backups taken together is obviously
# a set. Per-file stamps drift by the minute it takes to pull four gateways and
# then nothing tells you which four belong to the same moment.
STAMP="$(date +%Y%m%d-%H%M)"

failed=0
for gw in "${TARGETS[@]}"; do
  if ! gateway_running "$gw"; then
    warn "$gw is not running -- skipped"
    continue
  fi
  out="$OUT_DIR/$gw-$STAMP.gwbk"
  say "$gw"
  # A backup of a big gateway takes a while to assemble before a byte is sent,
  # which is why ign-gw.js gives this route its own long timeout.
  if bytes="$(node scripts/ign-gw.js backup --gateway "$(stanza_for "$gw")" --out "$out" 2>&1 | tail -1)"; then
    ok "$(basename "$out")  $(printf '%s' "$bytes" | awk '{printf "%.1f MB", $1/1048576}')"
  else
    warn "$gw: $bytes"
    # A redundant master refuses a backup while its backup half is down.
    case "$bytes" in *"Redundant peer is not available"*)
      dim "  $gw is half of a redundant pair and will not back up alone:"
      dim "  ./wd demo-start DEMO=redundancy, then run this again" ;;
    esac
    rm -f "$out"
    failed=$((failed + 1))
  fi
done

echo
if [ "$failed" -gt 0 ]; then
  warn "$failed gateway(s) did not back up"
  exit 1
fi
ok "backups in .gwbk (gitignored -- they hold gateway credentials)"
dim "  restore: gateway UI -> Config -> System -> Backup/Restore"
dim "  older ones: scripts/gwbk.sh --list, scripts/gwbk.sh --prune"
