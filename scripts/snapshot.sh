#!/usr/bin/env bash
#
# Save what this machine's gateways HAVE, right now, before an update touches
# them -- so an engineer who changed a gateway setting by hand can inspect
# what it was, or put it back themselves, afterwards.
#
#   scripts/snapshot.sh                  every gateway
#   scripts/snapshot.sh ignition         just one (also: make snapshot GATEWAY=ignition)
#   scripts/snapshot.sh --list           what snapshots exist, size + date
#
# THIS SCRIPT NEVER RESTORES ANYTHING. It only writes. Nothing else in this
# repo reads a snapshot back into a gateway either -- there is no replay path,
# by design. `make update` / `self-update.sh` always win: an update is not
# allowed to stall waiting for a decision about someone's hand-made change.
# What this buys instead is the other half of that trade: the change is not
# allowed to simply vanish unrecorded. A snapshot is the machine's own memory
# of what it had, for a human to diff or copy back by hand if that turns out
# to be what they wanted. Restoring is always a choice made afterwards, at the
# gateway UI (Config > System > Backup/Restore for a .gwbk; the Config UI
# itself for a resource in config/) -- never a side effect of taking one.
#
# WHY .snapshots/ IS GITIGNORED
#
# Every gateway's .gwbk is a full backup, and a full backup carries that
# gateway's own admin credentials -- exactly why .gwbk/ itself is already
# gitignored (scripts/gwbk.sh). A snapshot folder holds a copy of those same
# files, so it inherits the same rule: `.snapshots/` must never be committed.
#
# WHAT IT WRITES, into .snapshots/<version>-<YYYYmmdd-HHMMSS>/
#
#   gwbk/     a .gwbk from every targeted gateway (scripts/gwbk.sh, reused --
#             see the comment at the copy step for why COPIED, not moved)
#   config/<gateway>/<module>.<type>.json
#             a read-only export of the gateway config resources the setup
#             scripts in this repo manage -- the table below says exactly
#             which, and why each one is there
#   MANIFEST.txt
#             stack version, timestamp, git commit, which gateways were up,
#             and which captures succeeded, were skipped, or failed
#
# --- the config resources every setup script manages, and why -------------
#
# `node scripts/ign-gw.js api --gateway <stanza> --path <path>` is the same
# read-only route those scripts already use to read a resource before writing
# it (ign-gw.js's own `api` command, see its header) -- this script only ever
# calls it with GET, never PUT/POST/DELETE. Two shapes of path:
#
#   list       /data/api/v1/resources/list/<module>/<type>?limit=500&offset=0
#   singleton  /data/api/v1/resources/singleton/<module>/<type>
#
# Two settings are NOT resources at all -- their owning script talks to a
# dedicated REST route instead (ign-redundancy.sh's own header explains why
# for the first) -- and are captured the same way, by the exact path in place
# of a module/type pair:
#
#   redundancy-config   GET /data/api/v1/redundancy/config     (ign-redundancy.sh)
#   web-server          GET /data/config/web-server             (ign-public-address.sh)
#
# Which gateway each row applies to is NOT "every gateway that is up": Engine
# lives on the hub, Transmission on an edge, and so on, exactly as the owning
# script itself decides it (ign-mqtt.sh's module_for(), for instance). Re-
# deriving each script's exact targeting here would be a second copy of logic
# that already lives in nine places and already drifts between them -- so
# instead each row below carries a GATE, a short name resolved against the
# same stack.meta fields (ROLE, MQTT_MODULE, SF_TRANSPORT) those scripts
# already read via lib.sh's meta_get. A row whose gate does not match a given
# gateway is simply never attempted there; one whose gate matches but whose
# module is not actually installed 404s, which is recorded as "skip", not a
# failure -- read-only GETs are cheap enough that attempting is simpler and
# safer than trying to be exhaustively precise about installed modules too.
#
#   gate                    matches a gateway when
#   ----                    ----------------------
#   all                     always
#   hub                     ROLE=hub
#   not_backup              ROLE is not "backup"
#   hub_or_backup           ROLE=hub or ROLE=backup           (the pair)
#   mqtt_engine             MQTT_MODULE=engine                (the hub)
#   mqtt_transmission       MQTT_MODULE=transmission          (every edge)
#   edge_any                ROLE=edge or ROLE=edge-isolated
#   gan_edge                SF_TRANSPORT=gan
#   hub_or_gan_edge         ROLE=hub, or SF_TRANSPORT=gan
#   hub_or_sparkplug_edge   ROLE=hub, or (ROLE=edge-isolated and MQTT_MODULE=transmission)
#
#   module                                     | type                  | shape     | gate                  | owning script
#   --------------------------------------------|-----------------------|-----------|-----------------------|---------------------------
#   com.cirruslink.mqtt.distributor.gateway     | general               | singleton | hub_or_backup         | distributor-setup.sh
#   com.cirruslink.mqtt.distributor.gateway     | user                  | list      | hub_or_backup         | distributor-setup.sh
#   com.cirruslink.mqtt.engine.gateway          | general               | singleton | mqtt_engine           | sparkplug-setup.sh
#   com.cirruslink.mqtt.engine.gateway          | cert-file             | list      | mqtt_engine           | ign-mqtt.sh
#   com.cirruslink.mqtt.engine.gateway          | server-set            | list      | mqtt_engine           | ign-mqtt.sh
#   com.cirruslink.mqtt.engine.gateway          | server                | list      | mqtt_engine           | ign-mqtt.sh
#   com.cirruslink.mqtt.engine.gateway          | namespace-server-set  | list      | mqtt_engine           | ign-mqtt.sh
#   com.cirruslink.mqtt.engine.gateway          | custom-namespace      | list      | mqtt_engine           | sparkplug-setup.sh
#   com.cirruslink.mqtt.transmission.gateway    | cert-file             | list      | mqtt_transmission     | ign-mqtt.sh
#   com.cirruslink.mqtt.transmission.gateway    | server-set            | list      | mqtt_transmission     | ign-mqtt.sh
#   com.cirruslink.mqtt.transmission.gateway    | server                | list      | mqtt_transmission     | ign-mqtt.sh
#   com.cirruslink.mqtt.transmission.gateway    | transmitter           | list      | mqtt_transmission     | sf-arm.sh, sparkplug-setup.sh
#   com.cirruslink.mqtt.transmission.gateway    | history-store         | list      | mqtt_transmission     | sf-arm.sh
#   ignition                                    | secret-provider       | list      | hub_or_sparkplug_edge | sparkplug-setup.sh
#   ignition                                    | alarm-journal         | list      | hub                   | sparkplug-setup.sh
#   ignition                                    | security-zone         | list      | hub                   | sf-gan-history.sh
#   ignition                                    | tag-provider          | list      | hub                   | sf-gan-history.sh
#   com.inductiveautomation.historian           | historian-provider    | list      | hub_or_gan_edge       | sf-gan-history.sh
#   ignition                                    | edge-sync-settings    | singleton | gan_edge              | sf-gan-history.sh
#   ignition                                    | edge-system-properties| singleton | edge_any              | ign-edge-visual.sh
#   ignition                                    | security-properties   | singleton | not_backup            | ign-designer-idp.sh
#   -  (raw path, see above)                    | redundancy-config     | raw       | hub_or_backup         | ign-redundancy.sh
#   -  (raw path, see above)                    | web-server            | raw       | all                   | ign-public-address.sh
#
# A resource GET returns any secret-valued field already encrypted (a
# gateway-side JWE, `{"type":"Embedded","data":{...}}`) -- the same shape
# every setup script above already reads and writes without treating it as
# exposure. Nothing here prints or logs one; ign-gw.js's own scrub() covers
# its stderr regardless.
#
# Each capture is its own `node scripts/ign-gw.js api` process: a fresh
# headless login per call, exactly as every script in the table above already
# pays for one read at a time. That makes a full sweep of every gateway slow
# (minutes, not seconds) -- pass a gateway name to snapshot just the one you
# are about to touch when that matters more than completeness.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SNAP_ROOT="$REPO_ROOT/.snapshots"

case "${1:-}" in
  --list)
    [ -d "$SNAP_ROOT" ] || { dim "no snapshots yet -- run scripts/snapshot.sh"; exit 0; }
    say "snapshots in .snapshots (gitignored -- see the header for why)"
    found=0
    for d in "$SNAP_ROOT"/*/; do
      [ -d "$d" ] || continue   # an unmatched glob stays literal on an empty dir
      found=1
      name="$(basename "$d")"
      # `du -sh`, not `ls -lh`: each snapshot is a directory, and `ls` would
      # report the directory inode's own size rather than what is inside it.
      size="$(du -sh "$d" 2>/dev/null | cut -f1)"
      when="$(date -r "$d" '+%Y-%m-%d %H:%M' 2>/dev/null)"
      printf '  %-42s %6s  %s\n' "$name" "${size:-?}" "${when:-?}"
    done
    [ "$found" -eq 1 ] || dim "  (none yet)"
    exit 0
    ;;
esac

need_docker
cd "$REPO_ROOT"
[ -f "$REPO_ROOT/.gateways.env" ] \
  || die "no .gateways.env -- it is generated: run 'make env'"

TARGETS=("$@")
[ ${#TARGETS[@]} -eq 0 ] && [ -n "${GATEWAY:-}" ] && TARGETS=("$GATEWAY")
[ ${#TARGETS[@]} -eq 0 ] && TARGETS=( "${GATEWAYS[@]}" )

VERSION="$(stack_version)"
STAMP="$(date +%Y%m%d-%H%M%S)"
SNAP_DIR="$SNAP_ROOT/${VERSION}-${STAMP}"
mkdir -p "$SNAP_DIR/gwbk" "$SNAP_DIR/config"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

MANIFEST="$SNAP_DIR/MANIFEST.txt"
# have_git guarantees the `||` side always wins on a checkout with no .git at
# all (a from-scratch tarball, say) rather than tripping over _git_ro's own
# stderr redirection; _git_ro itself is what makes `git` answer honestly from
# INSIDE the toolbox at all (safe.directory + --no-optional-locks -- see its
# comment in lib.sh) rather than failing with "detected dubious ownership",
# which reads exactly like "this is not a git repository".
COMMIT="$(have_git && _git_ro rev-parse HEAD || echo unknown)"

{
  echo "Ignition-Demos-Stack snapshot"
  echo "=============================="
  echo "captured : $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "stack    : $VERSION"
  echo "commit   : $COMMIT"
  echo
  echo "FOR INSPECTION AND MANUAL RESTORE ONLY. Nothing in this repo replays"
  echo "this folder automatically -- no make target and no script ever reads"
  echo "it back into a gateway. It exists so that after an update, an"
  echo "engineer who changed a gateway setting by hand can see what the"
  echo "machine had before, and put it back themselves if that is what they"
  echo "decide to do -- a .gwbk restores from the gateway's own"
  echo "Config > System > Backup/Restore page; a config/ export is something"
  echo "to diff against what the gateway reports now, by hand, resource by"
  echo "resource."
  echo
  echo "gateways"
  echo "--------"
} > "$MANIFEST"

for gw in "${TARGETS[@]}"; do
  if gateway_running "$gw"; then
    printf '  %-18s up\n' "$gw" >> "$MANIFEST"
  else
    printf '  %-18s down -- skipped\n' "$gw" >> "$MANIFEST"
  fi
done

# --- 1. a .gwbk from every targeted gateway ----------------------------------
#
# Reuses scripts/gwbk.sh rather than re-issuing the backup download itself:
# gwbk.sh already knows the long-timeout route through ign-gw.js a big backup
# needs, already treats a down gateway as a normal skip, and already reports
# size -- duplicating that here would be a second copy of the exact download
# this script has no reason to get wrong differently.
#
# gwbk.sh writes into .gwbk/, which is ALSO the general-purpose backup store
# `make gwbk` uses on its own, with its own --prune retention (newest 5 per
# gateway). So the files are COPIED into this snapshot, never moved: moving
# them would empty .gwbk/ on every snapshot, which would quietly break
# `make gwbk --list` / `--prune` for a store this script does not own and has
# no business emptying as a side effect.
#
# A marker file's mtime -- not gwbk.sh's own STAMP, which is minute-resolution
# and shared by every gateway in ITS run, and not parsing its coloured
# stdout, which is free to change -- is what tells this script exactly which
# files THIS invocation produced.
if [ ${#TARGETS[@]} -eq "${#GATEWAYS[@]}" ]; then say "gwbk: every gateway"
else say "gwbk: ${TARGETS[*]}"; fi

marker="$WORK/gwbk-marker"
mkdir -p "$REPO_ROOT/.gwbk"
touch "$marker"
gwbk_rc=0
bash "$REPO_ROOT/scripts/gwbk.sh" "${TARGETS[@]}" || gwbk_rc=$?

{
  echo
  echo "gwbk backups"
  echo "------------"
} >> "$MANIFEST"

# Gateway names carry no whitespace by the same grammar stacks_in_order()
# relies on (lib.sh), so the unquoted expansion below only ever splits on the
# filenames find actually printed, one per line.
gwbk_count=0
for f in $(find "$REPO_ROOT/.gwbk" -maxdepth 1 -name '*.gwbk' -newer "$marker" 2>/dev/null); do
  cp "$f" "$SNAP_DIR/gwbk/"
  sz="$(du -h "$f" 2>/dev/null | cut -f1)"
  printf '  ok    %s  (%s)\n' "$(basename "$f")" "${sz:-?}" >> "$MANIFEST"
  gwbk_count=$((gwbk_count + 1))
done
if [ "$gwbk_count" -eq 0 ]; then
  echo "  none -- every targeted gateway was down, or the backup failed above" >> "$MANIFEST"
fi

# --- 2. the gateway config resources the setup scripts manage ---------------
#
# See the table in the header. Every row is a read-only GET through the same
# ign-gw.js route those scripts already use; a row whose gate does not match
# this gateway is never attempted, and one that 404s (the module simply is
# not installed here) is recorded as skipped rather than failing the run.
ROWS=(
  "com.cirruslink.mqtt.distributor.gateway|general|singleton|hub_or_backup"
  "com.cirruslink.mqtt.distributor.gateway|user|list|hub_or_backup"
  "com.cirruslink.mqtt.engine.gateway|general|singleton|mqtt_engine"
  "com.cirruslink.mqtt.engine.gateway|cert-file|list|mqtt_engine"
  "com.cirruslink.mqtt.engine.gateway|server-set|list|mqtt_engine"
  "com.cirruslink.mqtt.engine.gateway|server|list|mqtt_engine"
  "com.cirruslink.mqtt.engine.gateway|namespace-server-set|list|mqtt_engine"
  "com.cirruslink.mqtt.engine.gateway|custom-namespace|list|mqtt_engine"
  "com.cirruslink.mqtt.transmission.gateway|cert-file|list|mqtt_transmission"
  "com.cirruslink.mqtt.transmission.gateway|server-set|list|mqtt_transmission"
  "com.cirruslink.mqtt.transmission.gateway|server|list|mqtt_transmission"
  "com.cirruslink.mqtt.transmission.gateway|transmitter|list|mqtt_transmission"
  "com.cirruslink.mqtt.transmission.gateway|history-store|list|mqtt_transmission"
  "ignition|secret-provider|list|hub_or_sparkplug_edge"
  "ignition|alarm-journal|list|hub"
  "ignition|security-zone|list|hub"
  "ignition|tag-provider|list|hub"
  "com.inductiveautomation.historian|historian-provider|list|hub_or_gan_edge"
  "ignition|edge-sync-settings|singleton|gan_edge"
  "ignition|edge-system-properties|singleton|edge_any"
  "ignition|security-properties|singleton|not_backup"
  "-|redundancy-config|raw:/data/api/v1/redundancy/config|hub_or_backup"
  "-|web-server|raw:/data/config/web-server|all"
)

# gate_ok <gate> <gateway> -- always called from an `if`, never bare: the
# `case` below is the function's last command, and under `set -e` a bare call
# whose match is false would end the whole script right here (the exact trap
# CLAUDE.md names, and the one ign-redundancy.sh's own wait_for_active hits).
gate_ok() {
  local gate="$1" gw="$2" role mod sf
  role="$(meta_get "$gw" ROLE)"
  mod="$(meta_get "$gw" MQTT_MODULE)"
  sf="$(meta_get "$gw" SF_TRANSPORT)"
  case "$gate" in
    all)                    return 0 ;;
    hub)                    [ "$role" = hub ] ;;
    not_backup)             [ "$role" != backup ] ;;
    hub_or_backup)          [ "$role" = hub ] || [ "$role" = backup ] ;;
    mqtt_engine)            [ "$mod" = engine ] ;;
    mqtt_transmission)      [ "$mod" = transmission ] ;;
    edge_any)               [ "$role" = edge ] || [ "$role" = edge-isolated ] ;;
    gan_edge)                [ "$sf" = gan ] ;;
    hub_or_gan_edge)        [ "$role" = hub ] || [ "$sf" = gan ] ;;
    hub_or_sparkplug_edge)  [ "$role" = hub ] \
                              || { [ "$role" = edge-isolated ] && [ "$mod" = transmission ]; } ;;
    *) return 1 ;;
  esac
}

ok_count=0; warn_count=0; skip_count=0

# capture_resource <gateway> <stanza> <dir> <module> <type> <shape> -- GET
# only; writes <dir>/<module>.<type>.json on success, updates the counters
# above (plain globals, deliberately: this always runs in the main shell, one
# call at a time, never in a pipeline or a subshell that would lose them).
capture_resource() {
  local gw="$1" stanza="$2" dir="$3" module="$4" type="$5" shape="$6"
  local path fname out errfile
  case "$shape" in
    list)      path="/data/api/v1/resources/list/$module/$type?limit=500&offset=0" ;;
    singleton) path="/data/api/v1/resources/singleton/$module/$type" ;;
    raw:*)     path="${shape#raw:}" ;;
  esac
  if [ "$module" = - ]; then fname="$type"; else fname="$module.$type"; fi
  errfile="$WORK/$gw.$fname.err"

  if out="$(node scripts/ign-gw.js api --gateway "$stanza" --path "$path" 2>"$errfile")"; then
    # tail -n +2: ign-gw.js's `api` command prints a "<status> <method> <path>"
    # line before the body (see its own comment) -- drop it, same as every
    # setup script's own `body()` helper does.
    printf '%s\n' "$out" | tail -n +2 > "$dir/$fname.json"
    if python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$dir/$fname.json" 2>/dev/null; then
      printf '  ok    %-18s %s\n' "$gw" "$fname" >> "$MANIFEST"
      ok_count=$((ok_count + 1))
    else
      printf '  warn  %-18s %s -- 200 but not parseable JSON; kept anyway\n' "$gw" "$fname" >> "$MANIFEST"
      warn_count=$((warn_count + 1))
    fi
  else
    printf '  skip  %-18s %s -- %s\n' "$gw" "$fname" \
      "$(tr '\n' ' ' < "$errfile" | head -c 200)" >> "$MANIFEST"
    skip_count=$((skip_count + 1))
  fi
}

say "config resources"
{
  echo
  echo "config resources  (config/<gateway>/<module>.<type>.json)"
  echo "-----------------------------------------------------------"
} >> "$MANIFEST"

for gw in "${TARGETS[@]}"; do
  if ! gateway_running "$gw"; then
    continue   # already noted as down above; never worth a login attempt
  fi
  stanza="$(stanza_for "$gw")"
  mkdir -p "$SNAP_DIR/config/$gw"
  for row in "${ROWS[@]}"; do
    IFS='|' read -r module type shape gate <<<"$row"
    if gate_ok "$gate" "$gw"; then
      capture_resource "$gw" "$stanza" "$SNAP_DIR/config/$gw" "$module" "$type" "$shape"
    fi
  done
done

{
  echo
  echo "summary: gwbk $gwbk_count; resources $ok_count ok, $warn_count kept-but-unparsed, $skip_count not applicable/failed"
} >> "$MANIFEST"

echo
ok "snapshot written: $SNAP_DIR"
dim "  gwbk:      $gwbk_count file(s)"
dim "  resources: $ok_count ok, $warn_count warn, $skip_count skipped -- see MANIFEST.txt"
dim "  nothing replays this automatically -- inspect it, or restore by hand"

if [ "$gwbk_rc" -ne 0 ]; then
  warn "gwbk.sh reported a failure for a gateway that was running (see above)"
  exit 1
fi
exit 0
